"""Regression pin (DRE-6851): a change set the Settings page refused BY DESIGN
is an answer to the person who asked, never a pipeline failure — the medic
neither retries it nor files a failure card for it.

On 2026-10-10 at 22:25 PT the CEO dragged Designer into Builders on Settings ›
Agents & models. `Model config change` run 38114763170 refused the set at its
"Validate the change set" step — exactly what that step is for — and the page
told him "Not applied". The medic read the red run as a crash: it retried the
run (refused again, writing nothing), then spent a diagnosis agent on attempt
2, which filed DRE-6830 "Pipeline failure: Model config change" at 22:28 PT.
Planning read that notice as a business decision and parked it in Green Light
with a question for him, for 51 minutes, while the real fix was being built.

The refusal is read off the failed run's own log, and only in its own place:

  * a `Model config change` run whose failed step is "Validate the change set"
    and whose log carries the validator's refusal line is `refused-by-design`
    — no retry, no diagnosis, no card, and one sentence in the medic's own
    run summary;
  * the same workflow failing at a step AFTER validation (a crash after the
    push, a failed pull request open) is a real failure, retried and carded
    exactly as before;
  * the validation step failing WITHOUT the refusal line (the validator
    itself threw) is a real failure too.
"""

import contextlib
import io
import json
import os
import re
import sys
import tempfile
import unittest
from unittest import mock

import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")

import medic_retry  # noqa: E402
import model_config_change  # noqa: E402

MEDIC = os.path.join(ROOT, ".github", "workflows", "medic.yml")
SELF_MEDIC = os.path.join(ROOT, ".github", "workflows", "self-medic.yml")
CHANGE_WORKFLOW = os.path.join(ROOT, ".github", "workflows", "model-config-change.yml")

WORKFLOW_NAME = "Model config change"
VALIDATE = "Validate the change set"
RUN_ID = "38114763170"

# Run 38114763170's shape, as `gh run view --log-failed` prints it: job
# `change`, failed at "Validate the change set", the refusal line the card
# quotes, then the step's exit.
REFUSED_LOG = (
    "change\tValidate the change set\t2026-10-11T05:25:41.1000000Z "
    "##[group]Run cp config/models.yaml \"$RUNNER_TEMP/models-before.yaml\"\n"
    "change\tValidate the change set\t2026-10-11T05:25:41.2000000Z "
    "python3 scripts/model_config_change.py apply --changes "
    "\"$RUNNER_TEMP/change-set.json\" --check\n"
    "change\tValidate the change set\t2026-10-11T05:25:42.3000000Z "
    "::error::change set refused: change 1 of 1 (move_agent designer "
    "workhorse): designer has no entry in the agent registry\n"
    "change\tValidate the change set\t2026-10-11T05:25:42.4000000Z "
    "##[error]Process completed with exit code 2.\n"
)

# The same workflow, past validation: the pull request would not open.
AFTER_VALIDATION_LOG = (
    "change\tOpen the pull request\t2026-10-11T05:31:02.1000000Z "
    "pull request create failed: GraphQL: Resource not accessible by "
    "integration (createPullRequest)\n"
    "change\tOpen the pull request\t2026-10-11T05:31:02.2000000Z "
    "##[error]Process completed with exit code 1.\n"
)

# The validation step, failed by the validator ITSELF: a traceback, and no
# refusal line anywhere.
VALIDATOR_CRASH_LOG = (
    "change\tValidate the change set\t2026-10-11T05:40:11.1000000Z "
    "Traceback (most recent call last):\n"
    "change\tValidate the change set\t2026-10-11T05:40:11.2000000Z "
    "  File \"scripts/model_config_change.py\", line 840, in _cmd_apply\n"
    "change\tValidate the change set\t2026-10-11T05:40:11.3000000Z "
    "KeyError: 'ladders'\n"
    "change\tValidate the change set\t2026-10-11T05:40:11.4000000Z "
    "##[error]Process completed with exit code 1.\n"
)


def _decide(log_text, *, workflow=WORKFLOW_NAME, failed_step="", branch="main"):
    """Run the medic's `decide` exactly as medic.yml's gate step does, and
    return its `key=value` outputs. A card read is a test failure: these runs
    are dispatched on the default branch and name no card."""
    with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as f:
        f.write(log_text)
        path = f.name
    argv = ["decide", "--branch", branch, "--log", path,
            "--run-started-at", "2026-10-11T05:25:20Z",
            "--run-id", RUN_ID, "--run-attempt", "1",
            "--workflow", workflow]
    if failed_step:
        argv += ["--failed-step", failed_step]
    out = io.StringIO()
    try:
        with mock.patch.object(medic_retry, "card_facts",
                               side_effect=AssertionError("no card to read")), \
                contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(io.StringIO()):
            rc = medic_retry.main(argv)
    finally:
        os.unlink(path)
    assert rc == 0
    return dict(line.split("=", 1) for line in out.getvalue().splitlines() if "=" in line)


# ── an Actions `if:` evaluated the way Actions evaluates it ─────────────────
# Copied from tests/test_should_review_pr.py's `_render`: `||`/`&&` return an
# operand, `==`/`!=` compare, a missing property is null.
_IDENT = re.compile(r"'[^']*'|\b[a-z_]+(?:\.[a-z_]+)+\b")


def _runs(condition: str, ctx: dict) -> bool:
    py = _IDENT.sub(
        lambda m: m.group(0) if m.group(0).startswith("'")
        else f"ctx.get({m.group(0)!r})", condition)
    py = py.replace("||", " or ").replace("&&", " and ")
    py = " ".join(py.split())  # a folded `if:` keeps its indented newlines
    return bool(eval(py, {"ctx": ctx}))  # noqa: S307 — the repo's own workflow


def _context(outputs: dict, *, attempt: int) -> dict:
    ctx = {
        "github.event.workflow_run.conclusion": "failure",
        "github.event.workflow_run.run_attempt": attempt,
        "needs.classify.outputs.infra_crash": "false",
        "needs.classify.outputs.class": "normal",
        "needs.classify.outputs.limit": "false",
    }
    for key, value in outputs.items():
        ctx[f"needs.classify.outputs.{key}"] = value
    return ctx


def _medic_jobs() -> dict:
    with open(MEDIC, encoding="utf-8") as f:
        return yaml.safe_load(f)["jobs"]


def _jobs_that_run(outputs: dict, *, attempt: int) -> set:
    ctx = _context(outputs, attempt=attempt)
    return {name for name, job in _medic_jobs().items()
            if name != "classify" and _runs(job.get("if", "true"), ctx)}


def _files_a_card(job: dict) -> bool:
    return "linear_ops.py create" in yaml.safe_dump(job, width=10_000)


# ── the decision ────────────────────────────────────────────────────────────
class RefusedByDesignDecisionTest(unittest.TestCase):
    def test_run_38114763170_is_refused_by_design_and_not_retried(self):
        out = _decide(REFUSED_LOG)
        self.assertEqual("false", out["retry"])
        self.assertEqual(medic_retry.RULE_REFUSED_BY_DESIGN, out["rule"])
        self.assertEqual("", out["card"])

    def test_the_summary_names_the_refusal_and_where_the_person_was_told(self):
        detail = _decide(REFUSED_LOG)["detail"]
        self.assertIn("refused by design", detail)
        self.assertIn(VALIDATE, detail)
        self.assertIn("designer has no entry in the agent registry", detail)
        self.assertIn("Not applied", detail)
        self.assertIn("Settings", detail)
        # One sentence: it crosses a $GITHUB_OUTPUT boundary and is said once.
        self.assertNotIn("\n", detail)
        self.assertEqual(1, len(re.findall(r"\.(?:\s|$)", detail)), detail)

    def test_the_jobs_api_failed_step_is_enough_when_the_log_names_none(self):
        """`gh run view --log-failed` has printed `UNKNOWN STEP` for a step's
        name; the failed step off the Jobs API answers then."""
        log = REFUSED_LOG.replace(VALIDATE, "UNKNOWN STEP")
        self.assertEqual(medic_retry.RULE_REFUSED_BY_DESIGN,
                         _decide(log, failed_step=VALIDATE)["rule"])
        self.assertEqual(medic_retry.RULE_NONE, _decide(log)["rule"])

    def test_the_real_validators_refusal_lines_are_read(self):
        """Every refusal `model_config_change.py apply --check` really prints,
        on both of its refusal paths, as the step's log would carry it."""
        with tempfile.TemporaryDirectory() as tmp:
            # A move the gate still refuses: a session agent such as
            # product-lead may join any group since DRE-6833, a pipeline
            # role may not leave its kind's rule.
            refused = os.path.join(tmp, "refused.json")
            with open(refused, "w") as f:
                json.dump({"requested_by": "the CEO", "changes": [
                    {"op": "move_agent", "agent": "engineer",
                     "group": "judgement"}]}, f)
            malformed = os.path.join(tmp, "malformed.json")
            with open(malformed, "w") as f:
                f.write("{not json")
            for changes in (refused, malformed):
                with self.subTest(changes=os.path.basename(changes)):
                    printed = io.StringIO()
                    with contextlib.redirect_stdout(printed), \
                            contextlib.redirect_stderr(io.StringIO()), \
                            mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": ""}):
                        rc = model_config_change.main(
                            ["apply", "--changes", changes, "--check"])
                    self.assertEqual(2, rc)
                    log = "".join(
                        f"change\t{VALIDATE}\t2026-10-11T05:25:42.3000000Z {line}\n"
                        for line in printed.getvalue().splitlines())
                    self.assertEqual(medic_retry.RULE_REFUSED_BY_DESIGN,
                                     _decide(log)["rule"], log)

    def test_the_annotation_form_of_the_line_is_read_too(self):
        """A downloaded log can carry the command as the annotation it became."""
        log = REFUSED_LOG.replace("::error::change set refused:",
                                  "##[error]change set refused:")
        self.assertEqual(medic_retry.RULE_REFUSED_BY_DESIGN, _decide(log)["rule"])


class RealFailuresKeepTheirRetryTest(unittest.TestCase):
    def test_a_failure_after_validation_is_retried(self):
        out = _decide(AFTER_VALIDATION_LOG, failed_step="Open the pull request")
        self.assertEqual("true", out["retry"])
        self.assertEqual(medic_retry.RULE_NONE, out["rule"])

    def test_a_crashed_validator_is_retried(self):
        out = _decide(VALIDATOR_CRASH_LOG, failed_step=VALIDATE)
        self.assertEqual("true", out["retry"])
        self.assertEqual(medic_retry.RULE_NONE, out["rule"])

    def test_the_refusal_line_at_a_later_step_is_not_the_rule(self):
        """The apply step runs the same script; a refusal there, after the set
        was validated, is not the validator answering the person."""
        log = REFUSED_LOG.replace(VALIDATE, "Apply the change set to config/models.yaml")
        out = _decide(log, failed_step="Apply the change set to config/models.yaml")
        self.assertEqual("true", out["retry"])

    def test_the_jobs_api_naming_a_later_step_overrules_the_log(self):
        out = _decide(REFUSED_LOG, failed_step="Commit and push the branch")
        self.assertEqual("true", out["retry"])

    def test_another_workflow_quoting_the_line_is_not_the_rule(self):
        """An agent's log can quote this card's text verbatim."""
        for name in ("Agent Task", "Pipeline Tests", ""):
            with self.subTest(workflow=name):
                out = _decide(REFUSED_LOG, workflow=name)
                self.assertEqual(medic_retry.RULE_NONE, out["rule"])
                self.assertEqual("true", out["retry"])

    def test_the_rule_is_a_pure_decision_input(self):
        decision = medic_retry.decide(refused_by_design="x was refused by design")
        self.assertFalse(decision.retry)
        self.assertEqual(medic_retry.RULE_REFUSED_BY_DESIGN, decision.rule)
        self.assertTrue(medic_retry.decide().retry)


# ── the medic's jobs, evaluated against the decision ────────────────────────
class MedicWiringTest(unittest.TestCase):
    def test_the_refusal_runs_no_retry_no_diagnosis_and_files_no_card(self):
        outputs = _decide(REFUSED_LOG)
        for attempt in (1, 2):  # 2: a person re-ran the refused run by hand
            with self.subTest(attempt=attempt):
                running = _jobs_that_run(outputs, attempt=attempt)
                self.assertNotIn("retry", running)
                self.assertNotIn("diagnose", running)
                self.assertNotIn("retry_declined", running)
                for name in running:
                    self.assertFalse(_files_a_card(_medic_jobs()[name]), name)
                self.assertIn("refused_by_design", running)

    def test_a_failure_after_validation_is_retried_then_carded(self):
        outputs = _decide(AFTER_VALIDATION_LOG, failed_step="Open the pull request")
        self.assertIn("retry", _jobs_that_run(outputs, attempt=1))
        second = _jobs_that_run(outputs, attempt=2)
        self.assertIn("diagnose", second)
        self.assertTrue(_files_a_card(_medic_jobs()["diagnose"]))
        self.assertNotIn("refused_by_design", second)

    def test_a_crashed_validator_is_retried_then_carded(self):
        outputs = _decide(VALIDATOR_CRASH_LOG, failed_step=VALIDATE)
        self.assertIn("retry", _jobs_that_run(outputs, attempt=1))
        self.assertIn("diagnose", _jobs_that_run(outputs, attempt=2))
        self.assertNotIn("refused_by_design", _jobs_that_run(outputs, attempt=1))

    def test_the_summary_job_writes_the_one_sentence_and_ends_green(self):
        job = _medic_jobs()["refused_by_design"]
        self.assertEqual("classify", job.get("needs"))
        steps = job["steps"]
        self.assertEqual(1, len(steps))
        run = steps[0]["run"]
        self.assertIn("GITHUB_STEP_SUMMARY", run)
        self.assertIn("$DETAIL", run)
        self.assertEqual("${{ needs.classify.outputs.detail }}",
                         steps[0]["env"]["DETAIL"])
        body = yaml.safe_dump(job)
        for never in ("gh run rerun", "claude-code-action", "linear_ops.py", "exit 1"):
            self.assertNotIn(never, body)

    def test_the_gate_step_hands_over_the_failed_step(self):
        step = next(s for s in _medic_jobs()["classify"]["steps"] if s.get("id") == "r")
        self.assertIn('--failed-step "$FAILED_STEP"', step["run"])


class TheRunItReadsTest(unittest.TestCase):
    """The names the rule matches are the change workflow's own."""

    def test_the_validation_step_is_the_change_jobs(self):
        with open(CHANGE_WORKFLOW, encoding="utf-8") as f:
            doc = yaml.safe_load(f)
        self.assertEqual(WORKFLOW_NAME, doc["name"])
        names = [s.get("name") for s in doc["jobs"]["change"]["steps"]]
        self.assertIn(medic_retry.REFUSED_STEP, names)
        self.assertEqual(VALIDATE, medic_retry.REFUSED_STEP)
        self.assertTrue(medic_retry.is_change_set_workflow(doc["name"]))


class SelfMedicHeaderTest(unittest.TestCase):
    def test_the_header_says_a_refusal_is_not_retried_and_not_carded(self):
        with open(SELF_MEDIC, encoding="utf-8") as f:
            header = f.read().split("\nname:", 1)[0]
        block = header.split("# Model config change (DRE-6624)", 1)[1]
        text = " ".join(line.lstrip("#").strip() for line in block.splitlines())
        self.assertIn("DRE-6851", text)
        self.assertIn("not retried", text)
        self.assertIn("no failure card", text)
        self.assertNotIn("its retry is refused the same way", text)


if __name__ == "__main__":
    unittest.main()
