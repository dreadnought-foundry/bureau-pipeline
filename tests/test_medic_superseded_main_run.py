"""Regression pin (DRE-6882): the medic does not retry a failed `push` run on
the default branch once that branch has moved past the run's commit.

On 2026-10-10 the automatic retry of a failed `main` run on agent-bureau
finished red at 23:47 PT, ten minutes after the fix (#3618) had merged at
23:37 PT. `main` showed a fresh red run for a commit that was no longer its
tip, and the repair had already landed. A rerun of a commit that is no longer
on the branch says nothing about the branch, red or green.

The rule, read in the retry gate (`medic_retry.py decide`) off facts the
workflow hands it:

  * a `push` run whose head branch is the default branch, and whose head
    commit is no longer that branch's tip, is `superseded-on-default` — no
    retry, no refusal receipt, no diagnosis, and one sentence in the medic's
    own run summary and as a `::notice::`;
  * a commit still at the tip keeps its retry exactly as before;
  * a tip that could not be read keeps its retry, out loud (a `::warning::`);
  * a `pull_request`, `schedule` or `workflow_dispatch` run is never read by
    the rule, and the gate step makes no tip read for it.
"""

import contextlib
import io
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")

import medic_retry  # noqa: E402
import medic_wake  # noqa: E402

MEDIC = os.path.join(ROOT, ".github", "workflows", "medic.yml")

RUN_ID = "38131570214"
DEFAULT = "main"
# The failed run's commit, and the tip the fix moved `main` to.
HEAD_SHA = "4b1e0c9d2f3a5e6b7c8d9e0f1a2b3c4d5e6f7a8b"
TIP_SHA = "9c8d7e6f5a4b3c2d1e0f9a8b7c6d5e4f3a2b1c0d"

# A failed Pipeline Tests run on `main`: a test failed, nothing else.
FAILED_LOG = (
    "test\tRun the suite\t2026-10-11T06:37:12.1000000Z "
    "FAILED tests/test_release_train.py::test_plan - AssertionError\n"
    "test\tRun the suite\t2026-10-11T06:37:12.2000000Z "
    "##[error]Process completed with exit code 1.\n"
)


def _decide(*, event="push", branch=DEFAULT, default_branch=DEFAULT,
            head_sha=HEAD_SHA, tip_sha=TIP_SHA, attempt="1", log=FAILED_LOG,
            err=None):
    """Run the medic's `decide` exactly as medic.yml's gate step does, and
    return its `key=value` outputs. A card read is a test failure: a run on
    the default branch names no card."""
    with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as f:
        f.write(log)
        path = f.name
    argv = ["decide", "--branch", branch, "--log", path,
            "--run-started-at", "2026-10-11T06:30:02Z",
            "--run-id", RUN_ID, "--run-attempt", attempt,
            "--workflow", "Pipeline Tests",
            "--event", event, "--head-sha", head_sha,
            "--default-branch", default_branch, "--tip-sha", tip_sha]
    out = io.StringIO()
    try:
        with mock.patch.object(medic_retry, "card_facts",
                               side_effect=AssertionError("no card to read")), \
                contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err if err is not None else io.StringIO()):
            rc = medic_retry.main(argv)
    finally:
        os.unlink(path)
    assert rc == 0
    return dict(line.split("=", 1) for line in out.getvalue().splitlines() if "=" in line)


# ── an Actions `if:` evaluated the way Actions evaluates it ─────────────────
# Copied from tests/test_medic_refused_change_set.py: `||`/`&&` return an
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


def _gate_step() -> dict:
    return next(s for s in _medic_jobs()["classify"]["steps"] if s.get("id") == "r")


# ── the decision ────────────────────────────────────────────────────────────
class SupersededDecisionTest(unittest.TestCase):
    def test_a_main_run_behind_the_tip_is_not_retried(self):
        out = _decide()
        self.assertEqual("false", out["retry"])
        self.assertEqual(medic_retry.RULE_SUPERSEDED, out["rule"])
        self.assertEqual("superseded-on-default", out["rule"])
        self.assertEqual("", out["card"])

    def test_the_sentence_names_the_run_its_commit_and_the_tip(self):
        detail = _decide()["detail"]
        self.assertIn(RUN_ID, detail)
        self.assertIn(HEAD_SHA[:12], detail)
        self.assertIn(TIP_SHA[:12], detail)
        self.assertIn(DEFAULT, detail)
        # One line: it crosses a $GITHUB_OUTPUT boundary.
        self.assertNotIn("\n", detail)

    def test_a_commit_still_at_the_tip_keeps_its_retry(self):
        err = io.StringIO()
        out = _decide(tip_sha=HEAD_SHA, err=err)
        self.assertEqual("true", out["retry"])
        self.assertEqual(medic_retry.RULE_NONE, out["rule"])
        self.assertNotIn("::warning", err.getvalue())

    def test_an_unreadable_tip_keeps_the_retry_and_says_so(self):
        err = io.StringIO()
        out = _decide(tip_sha="", err=err)
        self.assertEqual("true", out["retry"])
        self.assertEqual(medic_retry.RULE_NONE, out["rule"])
        said = err.getvalue()
        self.assertIn("::warning::", said)
        self.assertIn(RUN_ID, said)
        self.assertIn(HEAD_SHA[:12], said)
        self.assertIn("tip could not be read", said)

    def test_other_events_on_the_default_branch_are_never_read(self):
        for event in ("schedule", "workflow_dispatch", "pull_request", ""):
            for tip in (TIP_SHA, ""):
                with self.subTest(event=event, tip=tip):
                    err = io.StringIO()
                    out = _decide(event=event, tip_sha=tip, err=err)
                    self.assertEqual("true", out["retry"])
                    self.assertEqual(medic_retry.RULE_NONE, out["rule"])
                    self.assertNotIn("::warning", err.getvalue())

    def test_a_pull_request_run_is_never_read(self):
        err = io.StringIO()
        out = _decide(event="pull_request", branch="agent/DRE-6882-x",
                      tip_sha=TIP_SHA, err=err)
        self.assertEqual("true", out["retry"])
        self.assertEqual(medic_retry.RULE_NONE, out["rule"])

    def test_a_push_to_another_branch_is_never_read(self):
        out = _decide(branch="release/v9", tip_sha=TIP_SHA)
        self.assertEqual("true", out["retry"])
        self.assertEqual(medic_retry.RULE_NONE, out["rule"])

    def test_an_unknown_default_branch_or_commit_keeps_the_retry(self):
        for kwargs in ({"default_branch": ""}, {"head_sha": ""}):
            with self.subTest(**kwargs):
                self.assertEqual("true", _decide(**kwargs)["retry"])


class TheRuleIsAPureFunctionTest(unittest.TestCase):
    def test_superseded_on_default(self):
        said = medic_retry.superseded_on_default(
            "push", DEFAULT, DEFAULT, HEAD_SHA, TIP_SHA, run_id=RUN_ID)
        self.assertIn(RUN_ID, said)
        self.assertNotIn("\n", said)
        for args in (
            ("push", DEFAULT, DEFAULT, HEAD_SHA, HEAD_SHA),
            ("push", DEFAULT, DEFAULT, HEAD_SHA, ""),
            ("push", DEFAULT, DEFAULT, "", TIP_SHA),
            ("push", "feature", DEFAULT, HEAD_SHA, TIP_SHA),
            ("push", DEFAULT, "", HEAD_SHA, TIP_SHA),
            ("pull_request", DEFAULT, DEFAULT, HEAD_SHA, TIP_SHA),
            ("schedule", DEFAULT, DEFAULT, HEAD_SHA, TIP_SHA),
            ("workflow_dispatch", DEFAULT, DEFAULT, HEAD_SHA, TIP_SHA),
        ):
            with self.subTest(args=args):
                self.assertEqual("", medic_retry.superseded_on_default(*args))

    def test_decide_takes_it_after_the_park_and_the_refusal(self):
        decision = medic_retry.decide(superseded="run 1 is superseded")
        self.assertFalse(decision.retry)
        self.assertEqual(medic_retry.RULE_SUPERSEDED, decision.rule)
        self.assertEqual(
            medic_retry.RULE_PARKED,
            medic_retry.decide(parked_because="x", superseded="y").rule)
        self.assertEqual(
            medic_retry.RULE_REFUSED_BY_DESIGN,
            medic_retry.decide(refused_by_design="x", superseded="y").rule)

    def test_decide_takes_it_before_the_stall_memory_and_turn_rules(self):
        turned = {"is_error": True, "subtype": "error_max_turns", "num_turns": "60"}
        for kwargs in ({"repeat_stall": "x"}, {"out_of_memory": "x"},
                       {"turn_receipt": "x"}, {"execution": turned}):
            with self.subTest(rule=sorted(kwargs)):
                self.assertEqual(
                    medic_retry.RULE_SUPERSEDED,
                    medic_retry.decide(superseded="y", **kwargs).rule)


# ── the medic's jobs, evaluated against the decision ────────────────────────
class MedicWiringTest(unittest.TestCase):
    def test_a_superseded_run_gets_no_retry_receipt_or_diagnosis(self):
        outputs = _decide()
        for card in ("", "DRE-6882"):  # the rare log that does name a card
            for attempt in (1, 2):  # 2: a person re-ran it by hand
                with self.subTest(card=card, attempt=attempt):
                    running = _jobs_that_run(dict(outputs, card=card), attempt=attempt)
                    self.assertNotIn("retry", running)
                    self.assertNotIn("retry_declined", running)
                    self.assertNotIn("diagnose", running)
                    self.assertIn("superseded", running)

    def test_a_commit_at_the_tip_is_retried_then_diagnosed(self):
        outputs = _decide(tip_sha=HEAD_SHA)
        first = _jobs_that_run(outputs, attempt=1)
        self.assertIn("retry", first)
        self.assertNotIn("superseded", first)
        second = _jobs_that_run(outputs, attempt=2)
        self.assertIn("diagnose", second)
        self.assertNotIn("superseded", second)

    def test_an_unreadable_tip_is_retried(self):
        self.assertIn("retry", _jobs_that_run(_decide(tip_sha=""), attempt=1))

    def test_the_summary_job_says_the_sentence_and_ends_green(self):
        job = _medic_jobs()["superseded"]
        self.assertEqual("classify", job.get("needs"))
        gate = " ".join(str(job["if"]).split())
        self.assertTrue(gate.startswith(medic_wake.expression()), gate)
        self.assertTrue(
            gate.endswith("needs.classify.outputs.rule == 'superseded-on-default'"), gate)
        steps = job["steps"]
        self.assertEqual(1, len(steps))
        run = steps[0]["run"]
        self.assertIn("GITHUB_STEP_SUMMARY", run)
        self.assertIn("::notice::", run)
        self.assertIn("$DETAIL", run)
        self.assertIn("$RUN_URL", run)
        self.assertEqual("${{ needs.classify.outputs.detail }}", steps[0]["env"]["DETAIL"])
        self.assertEqual("${{ github.event.workflow_run.html_url }}",
                         steps[0]["env"]["RUN_URL"])
        self.assertNotIn("${{", run)
        body = yaml.safe_dump(job)
        for never in ("gh run rerun", "claude-code-action", "linear_ops.py", "exit 1"):
            self.assertNotIn(never, body)

    def test_the_summary_job_writes_the_sentence_and_the_run_url(self):
        step = _medic_jobs()["superseded"]["steps"][0]
        detail = _decide()["detail"]
        url = f"https://github.com/o/r/actions/runs/{RUN_ID}"
        with tempfile.TemporaryDirectory() as raw:
            summary = Path(raw) / "summary.md"
            proc = subprocess.run(
                ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", step["run"]],
                capture_output=True, text=True, check=False,
                env={**os.environ, "DETAIL": detail, "RUN_URL": url,
                     "GITHUB_STEP_SUMMARY": str(summary)},
            )
            self.assertEqual(0, proc.returncode, proc.stderr)
            written = summary.read_text()
        self.assertIn(detail, written)
        self.assertIn(url, written)
        self.assertEqual(1, len(written.strip().splitlines()), written)
        self.assertIn(f"::notice::{detail}", proc.stdout)

    def test_the_declined_receipt_and_the_diagnosis_name_the_rule(self):
        jobs = _medic_jobs()
        for name in ("retry_declined", "diagnose"):
            with self.subTest(job=name):
                self.assertIn("needs.classify.outputs.rule != 'superseded-on-default'",
                              str(jobs[name]["if"]))


# ── the gate step, executed ──────────────────────────────────────────────────
GH_SHIM = r"""#!/bin/bash
printf '%s\n' "$*" >> "$SHIM_LOG"
case "$1" in
  api) [ "${FAKE_TIP_RC:-0}" = 0 ] || exit "$FAKE_TIP_RC"; printf '%s\n' "$FAKE_TIP" ;;
  run) printf '%s\n' "Run the suite" ;;
esac
exit 0
"""


def _render(text: str, values: dict) -> str:
    def sub(match):
        key = match.group(1).strip()
        if key not in values:
            raise AssertionError(f"the step reads {key!r}, which this test does not fake")
        return values[key]

    return re.sub(r"\$\{\{\s*([^}]+?)\s*\}\}", sub, text)


def _gate(*, event="push", branch=DEFAULT, tip=TIP_SHA, tip_rc=0):
    """Execute the classify job's gate step under the workflow's shell flags,
    with a fake `gh` and the real `medic_retry.py`. Returns the process, the
    `gh` calls it made and the `$GITHUB_OUTPUT` it wrote."""
    step = _gate_step()
    values = {
        "secrets.LINEAR_API_KEY": "test-key",
        "github.event.workflow_run.head_branch": branch,
        "github.event.workflow_run.run_started_at": "2026-10-11T06:30:02Z",
        "github.event.workflow_run.id": RUN_ID,
        "github.event.workflow_run.run_attempt": "1",
        "github.event.workflow_run.name": "Pipeline Tests",
        "github.event.workflow_run.event": event,
        "github.event.workflow_run.head_sha": HEAD_SHA,
        "github.event.repository.default_branch": DEFAULT,
        "github.token": "test-token",
    }
    env = {k: _render(str(v), values) for k, v in (step.get("env") or {}).items()}
    with tempfile.TemporaryDirectory() as raw:
        td = Path(raw)
        (td / "bin").mkdir()
        (td / "bin" / "gh").write_text(GH_SHIM)
        os.chmod(td / "bin" / "gh", 0o755)
        (td / ".bureau-pipeline").symlink_to(ROOT)
        log = td / "medic-log.txt"
        log.write_text(FAILED_LOG)
        calls = td / "calls.log"
        calls.touch()
        output = td / "output"
        output.touch()
        script = step["run"].replace("/tmp/medic-log.txt", str(log))
        proc = subprocess.run(
            ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", script],
            cwd=td, capture_output=True, text=True, check=False,
            env={**os.environ, **env, "PATH": f"{td / 'bin'}:{os.environ['PATH']}",
                 "SHIM_LOG": str(calls), "FAKE_TIP": tip, "FAKE_TIP_RC": str(tip_rc),
                 "GITHUB_REPOSITORY": "o/r", "GITHUB_OUTPUT": str(output),
                 "RUNNER_TEMP": str(td)},
        )
        written = dict(line.split("=", 1) for line in output.read_text().splitlines()
                       if "=" in line)
        return proc, [c for c in calls.read_text().splitlines() if c], written


def _tip_reads(calls):
    return [c for c in calls if c.startswith("api ")]


class TheGateStepTest(unittest.TestCase):
    def test_the_new_facts_come_from_env_never_inline(self):
        step = _gate_step()
        env = step["env"]
        self.assertEqual("${{ github.event.workflow_run.event }}", env["EVENT"])
        self.assertEqual("${{ github.event.workflow_run.head_sha }}", env["HEAD_SHA"])
        self.assertEqual("${{ github.event.repository.default_branch }}",
                         env["DEFAULT_BRANCH"])
        run = step["run"]
        self.assertNotIn("${{", run)
        for flag in ('--event "$EVENT"', '--head-sha "$HEAD_SHA"',
                     '--default-branch "$DEFAULT_BRANCH"', '--tip-sha "$TIP_SHA"'):
            self.assertIn(flag, run)

    def test_a_superseded_push_on_main_is_declined_after_one_tip_read(self):
        proc, calls, out = _gate()
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual([f"api repos/o/r/git/ref/heads/{DEFAULT} --jq .object.sha"],
                         _tip_reads(calls))
        self.assertEqual("false", out["retry"])
        self.assertEqual("superseded-on-default", out["rule"])

    def test_a_commit_still_at_the_tip_is_retried(self):
        proc, calls, out = _gate(tip=HEAD_SHA)
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual(1, len(_tip_reads(calls)))
        self.assertEqual(("true", "none"), (out["retry"], out["rule"]))

    def test_a_failed_tip_read_is_a_retry_and_a_warning_never_a_failed_step(self):
        proc, calls, out = _gate(tip="", tip_rc=1)
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual(1, len(_tip_reads(calls)))
        self.assertEqual(("true", "none"), (out["retry"], out["rule"]))
        self.assertIn("::warning::", proc.stderr)
        self.assertIn("tip could not be read", proc.stderr)

    def test_no_other_run_makes_a_tip_read(self):
        for event, branch in (("pull_request", "agent/DRE-6882-x"),
                              ("pull_request", DEFAULT),
                              ("schedule", DEFAULT),
                              ("workflow_dispatch", DEFAULT),
                              ("push", "release/v9")):
            with self.subTest(event=event, branch=branch):
                proc, calls, out = _gate(event=event, branch=branch)
                self.assertEqual(0, proc.returncode, proc.stderr)
                self.assertEqual([], _tip_reads(calls))
                self.assertEqual(("true", "none"), (out["retry"], out["rule"]))


# ── the comments that say the rule ──────────────────────────────────────────
def _flat(text: str) -> str:
    return " ".join(line.lstrip("#").strip() for line in text.splitlines())


class TheRuleIsWrittenDownTest(unittest.TestCase):
    def test_the_module_docstring_lists_it_as_rule_5(self):
        doc = medic_retry.__doc__
        self.assertIn("5. **", doc)
        rule = doc.split("5. **", 1)[1].split("\n\n", 1)[0]
        flat = " ".join(rule.split())
        self.assertIn("DRE-6882", flat)
        self.assertIn("not retried", flat)
        self.assertIn("no longer", flat)

    def test_the_header_carries_an_eighth_exception(self):
        with open(MEDIC, encoding="utf-8") as f:
            header = f.read().split("\nname:", 1)[0]
        self.assertIn("# EIGHTH EXCEPTION (DRE-6882)", header)
        block = _flat(header.split("# EIGHTH EXCEPTION (DRE-6882)", 1)[1]
                      .split("\n#\n", 1)[0])
        self.assertIn("superseded-on-default", block)
        self.assertIn("not retried", block)
        self.assertIn("no longer", block)

    def test_the_retry_job_comment_names_the_rule(self):
        with open(MEDIC, encoding="utf-8") as f:
            text = f.read()
        comment = text.split("\n  retry:\n", 1)[0].rsplit("\n\n", 1)[1]
        flat = _flat(comment)
        self.assertIn("DRE-6882", flat)
        self.assertIn("superseded-on-default", flat)


if __name__ == "__main__":
    unittest.main()
