"""RED-first (DRE-3430): the medic acts on the environment class — it names the
cause on the card, spends no diagnosis agent on it, and holds after the one
retry.

DRE-3428 gave the pipeline the vocabulary (`scripts/reviewer_environment.py`:
four signatures, the evidence note, the hold receipt) and taught the classifier
a fifth class, `environment_crash`. Nothing read it yet. Today the medic still:

  * posts a note on the card blaming "an infrastructure rate-limit" — which on
    2026-09-08 went on three held cards while the real cause was a vendor
    action that left no Claude launcher on the runner (DRE-3416); and
  * on attempt two of any other workflow that died the same way, dispatches
    the DIAGNOSIS AGENT, which is itself a Claude run and dies on the same
    wall: a runner-minute spent proving nothing.

What this file pins, in the `MedicWiringTest` shape the rest of this file's
gates are pinned in (regex + YAML over the workflow source, because that is
how medic.yml is held today):

  1. `classify` exposes `signature`, `check` and `meaning`; `diagnose` is
     gated off the class; `backoff` branches; `environment_hold` exists for
     attempt >= 2 and reruns nothing.
  2. `retry`'s gate is UNTOUCHED for this class on attempt 1 — a transient
     blip deserves its one retry, and the second identical death is the proof
     that the environment is the cause.
  3. THE REPLAY: the DRE-3416 log, through the real classifier and then
     through the real `if:` expressions, evaluated. A QA-review crash yields
     no rerun, no diagnosis and exactly one evidence note naming
     `native-binary-missing` and its check; a second failed run of a non-review
     workflow yields one hold receipt and no diagnosis.
  4. THE FLEET WITNESS (epic DRE-3420): both branches leave exactly one card
     comment per failed critic run STARTING with
     `reviewer_environment.CRITIC_UNAVAILABLE_MARKER` — the literal on one
     branch, the evidence note on the other — because
     `reviewer_down.witness_from_comments` (DRE-3433) counts those comments as
     could-not-run outcomes and that is how a second repository's crash is seen
     at all.

Run: cd bureau-pipeline && python3 -m pytest tests/test_medic_environment_hold.py -v
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")

import medic_classify  # noqa: E402
import reviewer_environment as renv  # noqa: E402

WORKFLOW = ROOT / ".github" / "workflows" / "medic.yml"

SHA = "0ff1ce5b1a1e0ff1ce5b1a1e0ff1ce5b1a1e0ff1"
RUN_ID = "34201234567"
RUN_URL = f"https://github.com/dreadnought-foundry/agent-bureau/actions/runs/{RUN_ID}"

# The DRE-3416 log, as all six kinds of Claude-running job actually died:
# `job<TAB>step<TAB><ISO timestamp> <content>`, which is what
# `gh run view --log-failed` writes into /tmp/medic-log.txt.
PREFIX = "qa-review\tReview the pull request\t2026-09-08T18:03:12.1234567Z "
NATIVE_BINARY_LOG = "".join(
    f"{PREFIX}{line}\n" for line in (
        "Run anthropics/claude-code-action@v1",
        "Installing Claude Code...",
        "Error: Claude Code native binary not found",
        "##[error]Process completed with exit code 1.",
    )
)


def _src() -> str:
    return WORKFLOW.read_text("utf-8")


def _medic() -> dict:
    return yaml.safe_load(_src())


def _job(name: str) -> dict:
    jobs = _medic()["jobs"]
    assert name in jobs, f"medic.yml declares no {name!r} job"
    return jobs[name]


def _job_source(name: str) -> str:
    """The job's own lines out of the YAML source — the shape the rest of
    medic.yml's gates are pinned in (`MedicWiringTest`)."""
    src = _src()
    match = re.search(rf"\n  {name}:\n(.*?)(?=\n  \w+:\n|\Z)", src, re.S)
    assert match, f"{name} job not found in medic.yml"
    return match.group(1)


# ── 1. the wiring ────────────────────────────────────────────────────────────
class MedicWiringTest(unittest.TestCase):
    def test_classify_exposes_the_three_new_outputs(self):
        outputs = _job("classify")["outputs"]
        for key in ("signature", "check", "meaning"):
            self.assertIn(key, outputs, f"classify does not expose {key}")
            self.assertIn(
                f"steps.c.outputs.{key}", outputs[key],
                f"{key} must come off the classifier step, not be invented here",
            )

    def test_the_three_outputs_are_the_classifier_s_own_lines(self):
        """One vocabulary, not two: the keys the classifier prints are the keys
        the job publishes (`reviewer_environment.report_lines`)."""
        printed = [
            line.split("=", 1)[0]
            for line in renv.report_lines(renv.by_slug("native-binary-missing"))
        ]
        self.assertEqual(printed, ["signature", "check", "meaning"])
        for key in printed:
            self.assertIn(key, _job("classify")["outputs"])

    def test_diagnose_is_gated_off_the_environment_class(self):
        # The diagnosis agent is itself a Claude run: on this class it dies on
        # the same wall, a runner-minute spent proving nothing.
        self.assertIn(
            "needs.classify.outputs.class != 'environment_crash'",
            _job("diagnose")["if"],
        )

    def test_backoff_calls_the_evidence_note(self):
        body = _job_source("backoff")
        self.assertIn("reviewer_environment.py note", body)
        self.assertIn("--signature", body)
        self.assertIn("github.event.workflow_run.head_sha", body)

    def test_backoff_keeps_the_unconverted_literal_exactly_once(self):
        # `config/pipeline-acts.json`'s `unconverted` row for the medic note
        # must keep matching EXACTLY ONE site (check_act_receipts.py), and the
        # rate-limit wording is still right for every other infra crash.
        self.assertEqual(_src().count(renv.CRITIC_UNAVAILABLE_MARKER), 1)
        self.assertIn(renv.CRITIC_UNAVAILABLE_MARKER, _job_source("backoff"))

    def test_backoff_branches_on_the_class(self):
        body = _job_source("backoff")
        self.assertIn("environment_crash", body)
        self.assertIn("infra_crash == 'true'", _job("backoff")["if"])

    def test_environment_hold_exists_for_the_second_death(self):
        gate = _job("environment_hold")["if"]
        self.assertIn("github.event.workflow_run.run_attempt >= 2", gate)
        self.assertIn("needs.classify.outputs.class == 'environment_crash'", gate)

    def test_environment_hold_needs_classify_and_posts_the_hold(self):
        body = _job_source("environment_hold")
        self.assertIn("needs: classify", body)
        self.assertIn("reviewer_environment.py hold", body)
        self.assertIn("--count twice", body)
        self.assertIn("--subject", body)

    def test_environment_hold_reruns_nothing(self):
        # A decision, not a failure, and above all not a third attempt at the
        # same wall.
        self.assertNotIn("gh run rerun", _job_source("environment_hold"))

    def test_the_hold_reads_the_card_the_retry_gate_already_derived(self):
        self.assertIn(
            "needs.classify.outputs.card", _job_source("environment_hold")
        )

    def test_the_one_automatic_retry_is_untouched_by_this_class(self):
        """A transient blip deserves its one retry, and the second identical
        death is the proof the environment is the cause — so `retry`'s gate
        must not name this class at all."""
        self.assertNotIn("environment_crash", _job("retry")["if"])


# ── 2. the fleet witness (epic DRE-3420) ─────────────────────────────────────
class FleetWitnessTest(unittest.TestCase):
    """`reviewer_down.witness_from_comments` (DRE-3433, wired into the sweep by
    DRE-3435) counts every card comment STARTING with the crash phrase as one
    could-not-run outcome from that repository — it is how a second
    repository's crash is seen at all. Both of the medic's branches must keep
    feeding it, and neither may post twice."""

    def test_the_yaml_literal_opens_with_the_shared_phrase(self):
        body = _job_source("backoff")
        posted = re.search(r'"(\U0001f50c[^"]*)', body)
        self.assertIsNotNone(posted, "the literal note is not posted in backoff")
        self.assertTrue(
            posted.group(1).startswith(renv.CRITIC_UNAVAILABLE_MARKER),
            "the witness counts comments STARTING with the phrase",
        )

    def test_the_evidence_note_opens_with_the_same_phrase(self):
        note = renv.evidence_note(
            renv.by_slug("native-binary-missing"), SHA, RUN_URL
        )
        self.assertTrue(note.startswith(renv.CRITIC_UNAVAILABLE_MARKER))

    def test_the_note_is_posted_once_per_failed_run_from_backoff_only(self):
        # One call site in the whole file, inside `backoff`, which is gated to
        # attempt 1 — so one note per failed critic run, never one per rerun.
        self.assertEqual(_src().count("reviewer_environment.py note"), 1)
        self.assertIn("reviewer_environment.py note", _job_source("backoff"))
        self.assertIn(
            "github.event.workflow_run.run_attempt == 1", _job("backoff")["if"]
        )

    def test_the_hold_is_not_counted_as_a_crash(self):
        # The hold is mirrored to the same card; the detector must count
        # crashes, never holds.
        hold = renv.hold_receipt(renv.by_slug("native-binary-missing"), SHA, 2)
        self.assertNotIn(renv.CRITIC_UNAVAILABLE_MARKER, hold)


# ── 3. the gates, evaluated ──────────────────────────────────────────────────
_TERM = re.compile(
    r"^(?P<left>[\w.\-]+)\s*(?P<op>==|!=|>=|<=|>|<)\s*(?P<right>'[^']*'|\d+)$"
)


def fires(gate: str, context: dict) -> bool:
    """Evaluate one `if:` expression against a context.

    Deliberately strict: an operator or an unmodelled term raises rather than
    reading false, because a gate this evaluator cannot read is a gate this
    test is not checking.
    """
    assert "||" not in gate, f"this evaluator cannot read {gate!r}"
    for raw in gate.split("&&"):
        term = raw.strip()
        match = _TERM.match(term)
        assert match, f"this evaluator cannot read the term {term!r}"
        left, operator, right = (
            match.group("left"), match.group("op"), match.group("right")
        )
        assert left in context, f"the gate reads {left!r}, which this replay does not model"
        actual = context[left]
        if right.startswith("'"):
            expected = right[1:-1]
        else:
            actual, expected = int(actual), int(right)
        if operator == "==" and not actual == expected:
            return False
        if operator == "!=" and not actual != expected:
            return False
        if operator == ">=" and not actual >= expected:
            return False
        if operator == "<=" and not actual <= expected:
            return False
        if operator == ">" and not actual > expected:
            return False
        if operator == "<" and not actual < expected:
            return False
    return True


def classify(workflow_name: str, log_text: str) -> dict:
    """The classifier's own stdout, as `$GITHUB_OUTPUT` would carry it."""
    with tempfile.TemporaryDirectory() as raw:
        log = Path(raw) / "medic-log.txt"
        log.write_text(log_text, encoding="utf-8")
        done = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "medic_classify.py"),
             workflow_name, str(log)],
            capture_output=True, text=True, check=True,
        )
    return dict(
        line.split("=", 1) for line in done.stdout.strip().splitlines()
    )


def fired_jobs(*, workflow_name: str, log_text: str, attempt: int,
               card: str = "DRE-3416", retry: str = "true") -> tuple:
    """Every medic job that fires for this failed run, and the classification.

    `retry='true'` is the adversarial choice: the DRE-2954 gate said a retry
    would be fine, so anything that does not rerun here is stopped by THIS
    card's wiring and not by that one.
    """
    outputs = classify(workflow_name, log_text)
    context = {
        "github.event.workflow_run.conclusion": "failure",
        "github.event.workflow_run.run_attempt": attempt,
        "needs.classify.outputs.infra_crash": outputs["infra_crash"],
        "needs.classify.outputs.class": outputs["class"],
        "needs.classify.outputs.limit": "false",
        "needs.classify.outputs.retry": retry,
        "needs.classify.outputs.card": card,
    }
    jobs = _medic()["jobs"]
    fired = {
        name for name, job in jobs.items()
        if "if" not in job or fires(job["if"], context)
    }
    return fired, outputs


class GateReplayTest(unittest.TestCase):
    def test_a_review_crash_notes_and_neither_reruns_nor_diagnoses(self):
        fired, outputs = fired_jobs(
            workflow_name="QA Review (reusable)",
            log_text=NATIVE_BINARY_LOG, attempt=1,
        )
        self.assertEqual(outputs["class"], "environment_crash")
        self.assertEqual(outputs["signature"], "native-binary-missing")
        self.assertEqual(fired, {"classify", "backoff"})

    def test_a_second_death_of_another_workflow_holds_and_never_diagnoses(self):
        fired, outputs = fired_jobs(
            workflow_name="Agent Task (reusable)",
            log_text=NATIVE_BINARY_LOG, attempt=2,
        )
        self.assertEqual(outputs["class"], "environment_crash")
        self.assertEqual(fired, {"classify", "environment_hold"})

    def test_the_first_death_of_another_workflow_still_gets_its_one_retry(self):
        fired, _ = fired_jobs(
            workflow_name="Agent Task (reusable)",
            log_text=NATIVE_BINARY_LOG, attempt=1,
        )
        self.assertIn("retry", fired)
        self.assertNotIn("environment_hold", fired)
        self.assertNotIn("diagnose", fired)

    def test_a_rate_limit_crash_is_handled_exactly_as_it_was(self):
        fired, outputs = fired_jobs(
            workflow_name="QA Review (reusable)",
            log_text=f"{PREFIX}API rate limit exceeded for installation ID 1\n",
            attempt=1,
        )
        self.assertEqual(outputs["class"], "critic_infra_crash")
        self.assertEqual(fired, {"classify", "backoff"})

    def test_a_normal_failure_still_diagnoses_on_attempt_two(self):
        fired, outputs = fired_jobs(
            workflow_name="Agent Task (reusable)",
            log_text=f"{PREFIX}FAILED tests/test_x.py::test_y - AssertionError\n",
            attempt=2,
        )
        self.assertEqual(outputs["class"], "normal")
        self.assertEqual(fired, {"classify", "diagnose"})


# ── 4. the steps, executed ───────────────────────────────────────────────────
SHIM = r"""#!/bin/bash
# Records every python3 call the step makes, one line per call, and answers
# the two writers it can reach.
printf '%s\n' "$*" >> "$SHIM_LOG"
exit 0
"""


FAILING_SHIM = r"""#!/bin/bash
# Every write refused, the way a dead Linear refuses one.
printf '%s\n' "$*" >> "$SHIM_LOG"
echo "linear_ops: POST https://api.linear.app/graphql failed" >&2
exit 1
"""


def run_step(job: str, payload: dict, shim: str = SHIM) -> dict:
    """Execute a job's single `run:` step the way GitHub would.

    The step's `${{ … }}` references are substituted from `payload` (an
    unmodelled one raises — a value this replay does not fake is a value the
    test is not checking) and the body runs under the workflow's own shell.
    """
    steps = [s for s in _job(job)["steps"] if s.get("run")]
    assert len(steps) == 1, f"{job} has {len(steps)} run steps, expected one"
    step = steps[0]

    def render(text: str) -> str:
        def sub(match):
            key = match.group(1).strip()
            assert key in payload, f"the step reads {key!r}, which this payload does not fake"
            return payload[key]
        return re.sub(r"\$\{\{\s*([^}]+?)\s*\}\}", sub, text)

    env = {k: render(str(v)) for k, v in (step.get("env") or {}).items()}
    script = render(step["run"])
    with tempfile.TemporaryDirectory() as raw:
        td = Path(raw)
        (td / "python3").write_text(shim)
        os.chmod(td / "python3", 0o755)
        log = td / "calls.log"
        log.touch()
        done = subprocess.run(
            ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", script],
            cwd=td, capture_output=True, text=True, check=False,
            env={**os.environ, **env,
                 "PATH": f"{td}:{os.environ['PATH']}",
                 "SHIM_LOG": str(log)},
        )
        return {
            "calls": [line for line in log.read_text().splitlines() if line],
            "rc": done.returncode,
            "text": done.stdout + done.stderr,
        }


def _payload(**overrides) -> dict:
    base = {
        "secrets.LINEAR_API_KEY": "test-key",
        "github.event.workflow_run.head_branch": "agent/DRE-3416-native-binary",
        "github.event.workflow_run.head_sha": SHA,
        "github.event.workflow_run.html_url": RUN_URL,
        "github.event.workflow_run.name": "Agent Task (reusable)",
        "github.event.workflow_run.id": RUN_ID,
        "needs.classify.outputs.class": "environment_crash",
        "needs.classify.outputs.card": "DRE-3416",
        "needs.classify.outputs.signature": "native-binary-missing",
        "needs.classify.outputs.check": renv.by_slug("native-binary-missing").check,
        "needs.classify.outputs.meaning": renv.by_slug("native-binary-missing").meaning,
    }
    base.update(overrides)
    return base


class BackoffStepTest(unittest.TestCase):
    def test_the_environment_branch_posts_the_evidence_note_and_nothing_else(self):
        result = run_step("backoff", _payload(
            **{"github.event.workflow_run.name": "QA Review (reusable)"}
        ))
        self.assertEqual(result["rc"], 0, result["text"])
        notes = [c for c in result["calls"] if "reviewer_environment.py note" in c]
        self.assertEqual(len(notes), 1, result["calls"])
        self.assertIn("--card DRE-3416", notes[0])
        self.assertIn(f"--sha {SHA}", notes[0])
        self.assertIn("--signature native-binary-missing", notes[0])
        self.assertIn(f"--run-url {RUN_URL}", notes[0])
        self.assertEqual(
            [c for c in result["calls"] if "linear_ops.py comment" in c], [],
            "one note per failed run — the rate-limit literal must not also post",
        )

    def test_the_note_it_posts_names_the_cause_and_the_check(self):
        signature = renv.by_slug("native-binary-missing")
        note = renv.evidence_note(signature, SHA, RUN_URL)
        self.assertIn("native-binary-missing", note)
        self.assertIn(signature.check, note)
        self.assertIn(RUN_URL, note)

    def test_any_other_infra_crash_still_posts_the_literal(self):
        result = run_step("backoff", _payload(
            **{"needs.classify.outputs.class": "critic_infra_crash",
               "needs.classify.outputs.signature": "",
               "needs.classify.outputs.check": "",
               "needs.classify.outputs.meaning": ""}
        ))
        self.assertEqual(result["rc"], 0, result["text"])
        literals = [c for c in result["calls"] if "linear_ops.py comment" in c]
        self.assertEqual(len(literals), 1, result["calls"])
        self.assertIn(renv.CRITIC_UNAVAILABLE_MARKER, literals[0])
        self.assertEqual(
            [c for c in result["calls"] if "reviewer_environment.py" in c], []
        )

    def test_no_card_posts_nothing_on_either_branch(self):
        for klass in ("environment_crash", "critic_infra_crash"):
            result = run_step("backoff", _payload(
                **{"needs.classify.outputs.class": klass,
                   "needs.classify.outputs.card": "",
                   "github.event.workflow_run.head_branch": "main"}
            ))
            self.assertEqual(result["rc"], 0, result["text"])
            self.assertEqual(result["calls"], [], klass)


class EnvironmentHoldStepTest(unittest.TestCase):
    def test_it_posts_exactly_one_hold_receipt(self):
        result = run_step("environment_hold", _payload())
        self.assertEqual(result["rc"], 0, result["text"])
        holds = [c for c in result["calls"] if "reviewer_environment.py hold" in c]
        self.assertEqual(len(holds), 1, result["calls"])
        self.assertIn("--card DRE-3416", holds[0])
        self.assertIn(f"--sha {SHA}", holds[0])
        self.assertIn("--signature native-binary-missing", holds[0])
        self.assertIn("--count twice", holds[0])
        self.assertIn("Agent Task (reusable)", holds[0])

    def test_the_receipt_it_posts_is_the_hold_act(self):
        signature = renv.by_slug("native-binary-missing")
        body = renv.hold_receipt(signature, SHA, "twice", "Agent Task (reusable)")
        self.assertIn(renv.HOLD_TAG, body)
        self.assertIn("twice", body)
        self.assertIn("Agent Task (reusable)", body)
        self.assertIn(signature.check, body)

    def test_no_card_logs_the_decision_and_posts_nothing(self):
        result = run_step(
            "environment_hold", _payload(**{"needs.classify.outputs.card": ""})
        )
        self.assertEqual(result["rc"], 0, result["text"])
        self.assertEqual(result["calls"], [])
        self.assertIn("native-binary-missing", result["text"])

    def test_it_ends_green_and_says_so_when_the_write_fails(self):
        """A hold is a decision the pipeline made on purpose; a red medic run
        is an operator email about it (the `retry_declined` shape). A refused
        write is said out loud, never swallowed."""
        result = run_step("environment_hold", _payload(), shim=FAILING_SHIM)
        self.assertEqual(result["rc"], 0, result["text"])
        self.assertIn("::warning", result["text"])


if __name__ == "__main__":
    unittest.main()
