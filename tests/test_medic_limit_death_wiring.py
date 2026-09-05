"""RED-first: the medic hands the failed log to the limit classifier and never
reruns a limit death (DRE-3171, the wiring half).

`dead_run.py decide --limit-log …` classifies a limit death and prints the
marker; until the medic calls it, no marker is written and the reconcile sweep
(`limit_recovery.py`) has nothing to bring back. This file pins the call and
the gate, by RUNNING the classify job's new step under a shim rather than
reading its text: the step's `run:` block is rendered from a fake
`workflow_run` payload exactly the way GitHub renders it, executed under the
workflow's own shell flags (`bash -e -o pipefail`), and every `python3` it
makes is recorded.

What must hold:

  1. The decide invocation carries the four facts off the RUN — the failed
     log's path, the workflow name, the run id, and the run's own completion
     time as `--now` (a medic that wakes late must not push a Claude reset a
     day out) — and `--account` only when `CLAUDE_ACCOUNT` is non-empty.
  2. A limit death publishes `limit=true` and posts the marker ONCE on the
     card named by the head branch; a second pass over the same run posts
     nothing more. A run with no card still publishes `limit=true` (no
     rerun) and posts nothing.
  3. Anything else publishes `limit=false` and posts nothing — including a
     classifier that could not run at all, because this step must never take
     the whole medic down (every job `needs: classify`).
  4. The retry job and the diagnosis job both read the output: a limit death
     gets exactly one marker and no rerun.

Run: cd bureau-pipeline && GITHUB_REPOSITORY=dreadnought-foundry/bureau-pipeline \\
     python3 -m pytest tests/test_medic_limit_death_wiring.py -v
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
WORKFLOW = ROOT / ".github" / "workflows" / "medic.yml"

import medic_retry  # noqa: E402 — the one card-from-branch rule

RUN_ID = "33912345678"
UPDATED_AT = "2026-09-05T20:31:00Z"
PAYLOAD = {
    "github.event.workflow_run.id": RUN_ID,
    "github.event.workflow_run.name": "Agent Task (reusable)",
    "github.event.workflow_run.head_branch": "agent/DRE-3062-limit-death",
    "github.event.workflow_run.html_url": f"https://github.com/o/r/actions/runs/{RUN_ID}",
    "github.event.workflow_run.updated_at": UPDATED_AT,
    "github.event.workflow_run.run_attempt": "1",
    "github.event.workflow_run.conclusion": "failure",
    "github.repository": "dreadnought-foundry/bureau-pipeline",
    "secrets.LINEAR_API_KEY": "test-key",
}
MARKER = (
    f"🪦 limit-death: kind=claude stage=build reset=2026-09-06T20:30:00Z run={RUN_ID}\n\n"
    "This run hit the Claude account's usage limit during the build stage."
)

SHIM = r"""#!/bin/bash
# Records every python3 call the step makes, one line per call, and answers
# the three the step needs: the classifier, the card's thread, the comment.
printf '%s\n' "$*" >> "$SHIM_LOG"
case "$*" in
  *dead_run.py\ decide*)
    if [ "${FAKE_DECIDE_RC:-0}" != "0" ]; then exit "$FAKE_DECIDE_RC"; fi
    printf '%s\n' "$FAKE_DECISION"; exit 0 ;;
  *linear_ops.py\ dump-comments*)
    printf '%s\n' "${FAKE_COMMENTS:-[]}"; exit 0 ;;
  *linear_ops.py\ comment*)
    exit "${FAKE_COMMENT_RC:-0}" ;;
esac
exit 0
"""


def _medic() -> dict:
    with open(WORKFLOW, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _limit_step() -> dict:
    steps = _medic()["jobs"]["classify"]["steps"]
    return next(s for s in steps if s.get("id") == "limit")


def _render(text: str, extra: dict) -> str:
    """`${{ … }}` → the fake payload's value, as GitHub would substitute it."""
    values = {**PAYLOAD, **extra}

    def sub(match):
        key = match.group(1).strip()
        if key not in values:
            raise AssertionError(f"the step reads {key!r}, which this payload does not fake")
        return values[key]

    return re.sub(r"\$\{\{\s*([^}]+?)\s*\}\}", sub, text)


def run_step(*, decision: str = "limit\n\n" + MARKER, comments: str = "[]",
             account: str = "", head_branch: str | None = None,
             decide_rc: int = 0, comment_rc: int = 0) -> dict:
    """Execute the classify job's limit step as the workflow would.

    Returns the recorded python3 calls, the GITHUB_OUTPUT lines, the exit
    status and the combined output."""
    step = _limit_step()
    extra = {"vars.CLAUDE_ACCOUNT": account}
    if head_branch is not None:
        extra["github.event.workflow_run.head_branch"] = head_branch
    # The card is the retry gate's own output (steps.r), read off the head
    # branch by the ONE extraction medic.yml carries — so the fake publishes
    # it the way that step does, through the same function.
    extra["steps.r.outputs.card"] = medic_retry.card_from_branch(
        extra.get("github.event.workflow_run.head_branch", PAYLOAD["github.event.workflow_run.head_branch"])
    ) or ""
    env = {k: _render(str(v), extra) for k, v in (step.get("env") or {}).items()}
    script = _render(step["run"], extra)
    with tempfile.TemporaryDirectory() as raw:
        td = Path(raw)
        (td / "python3").write_text(SHIM)
        os.chmod(td / "python3", 0o755)
        log = td / "calls.log"
        log.touch()
        out = td / "github_output"
        out.touch()
        proc = subprocess.run(
            ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", script],
            cwd=td, capture_output=True, text=True, check=False,
            env={
                **os.environ, **env,
                "PATH": f"{td}:{os.environ['PATH']}",
                "GITHUB_OUTPUT": str(out),
                "SHIM_LOG": str(log),
                "FAKE_DECISION": decision,
                "FAKE_COMMENTS": comments,
                "FAKE_DECIDE_RC": str(decide_rc),
                "FAKE_COMMENT_RC": str(comment_rc),
            },
        )
        return {
            "calls": [line for line in log.read_text().splitlines() if line],
            "outputs": out.read_text().splitlines(),
            "rc": proc.returncode,
            "text": proc.stdout + proc.stderr,
        }


def _decide_call(result: dict) -> str:
    calls = [c for c in result["calls"] if "dead_run.py decide" in c]
    assert len(calls) == 1, f"expected exactly one decide call, saw {result['calls']}"
    return calls[0]


def _comment_calls(result: dict) -> list:
    return [c for c in result["calls"] if "linear_ops.py comment" in c]


# ── 1. the invocation ────────────────────────────────────────────────────────
class DecideInvocationTest(unittest.TestCase):
    def test_the_four_facts_come_off_the_run(self):
        call = _decide_call(run_step())
        self.assertIn("--limit-log /tmp/medic-log.txt", call)
        self.assertIn("--workflow Agent Task (reusable)", call)
        self.assertIn(f"--run-id {RUN_ID}", call)
        self.assertIn(f"--now {UPDATED_AT}", call, "the run's own completion time, never the medic's clock")

    def test_no_account_flag_when_the_account_is_unknown(self):
        self.assertNotIn("--account", _decide_call(run_step(account="")))

    def test_the_account_flag_rides_along_when_known(self):
        self.assertIn("--account main", _decide_call(run_step(account="main")))

    def test_the_log_is_the_same_file_the_classifier_read(self):
        """One fetch of the failed log, shared: the DRE-2488 lesson was that a
        second fetch during an outage classifies an empty log."""
        body = yaml.safe_dump(_medic()["jobs"]["classify"])
        self.assertEqual(1, body.count("--log-failed"), "the failed log is fetched once")
        self.assertIn("--limit-log /tmp/medic-log.txt", body)


# ── 2. the marker, once ──────────────────────────────────────────────────────
class MarkerTest(unittest.TestCase):
    def test_a_limit_death_publishes_true_and_posts_the_marker_on_the_card(self):
        result = run_step()
        self.assertEqual(0, result["rc"], result["text"])
        self.assertIn("limit=true", result["outputs"])
        posts = _comment_calls(result)
        self.assertEqual(1, len(posts), result["calls"])
        self.assertIn("DRE-3062", posts[0])
        self.assertIn(f"🪦 limit-death: kind=claude stage=build reset=2026-09-06T20:30:00Z run={RUN_ID}", posts[0])

    def test_the_card_is_the_retry_gates_one_extraction(self):
        """medic.yml parses the head branch ONCE (tests/test_card_ref_case_
        insensitive.py pins that); this step reads the gate's output rather
        than extracting a second time."""
        step = _limit_step()
        self.assertEqual("${{ steps.r.outputs.card }}", (step.get("env") or {}).get("CARD"))
        self.assertNotIn("grep -oiE 'DRE-", step["run"])
        posts = _comment_calls(run_step(head_branch="agent/dre-3062-limit-death"))
        self.assertEqual(1, len(posts))
        self.assertIn(" DRE-3062 ", posts[0])

    def test_the_marker_is_written_once_per_run(self):
        already = f'["🧠 model-attempt: claude-opus-5", "{MARKER.splitlines()[0]}"]'
        result = run_step(comments=already)
        self.assertIn("limit=true", result["outputs"])
        self.assertEqual([], _comment_calls(result), "a second pass over the same run posts nothing")

    def test_an_older_run_s_marker_does_not_suppress_this_one(self):
        older = f'["🪦 limit-death: kind=claude stage=build reset=unknown run={int(RUN_ID) - 1}"]'
        self.assertEqual(1, len(_comment_calls(run_step(comments=older))))

    def test_a_run_with_no_card_still_blocks_the_retry_and_posts_nothing(self):
        result = run_step(head_branch="main")
        self.assertEqual(0, result["rc"], result["text"])
        self.assertIn("limit=true", result["outputs"])
        self.assertEqual([], _comment_calls(result))

    def test_a_refused_marker_write_does_not_fail_the_step(self):
        """Under a Linear limit the marker write itself can be refused. The
        step says so and stays green; the retry is still blocked."""
        result = run_step(comment_rc=1)
        self.assertEqual(0, result["rc"], result["text"])
        self.assertIn("limit=true", result["outputs"])
        self.assertIn("::warning", result["text"])


# ── 3. anything else ─────────────────────────────────────────────────────────
class NotALimitTest(unittest.TestCase):
    def test_an_ordinary_death_publishes_false_and_posts_nothing(self):
        result = run_step(decision="requeue\n\n🪦 dead-run-requeue: agent died")
        self.assertEqual(0, result["rc"], result["text"])
        self.assertIn("limit=false", result["outputs"])
        self.assertEqual([], _comment_calls(result))
        self.assertEqual([], [c for c in result["calls"] if "dump-comments" in c])

    def test_a_classifier_that_cannot_run_publishes_false_and_stays_green(self):
        result = run_step(decide_rc=2)
        self.assertEqual(0, result["rc"], result["text"])
        self.assertIn("limit=false", result["outputs"])
        self.assertEqual([], _comment_calls(result))


# ── 4. the gates ─────────────────────────────────────────────────────────────
class GateTest(unittest.TestCase):
    def setUp(self):
        self.jobs = _medic()["jobs"]

    def test_classify_publishes_limit(self):
        self.assertEqual("${{ steps.limit.outputs.limit }}", self.jobs["classify"]["outputs"]["limit"])

    def test_the_retry_job_reads_it(self):
        self.assertIn("needs.classify.outputs.limit != 'true'", self.jobs["retry"]["if"])

    def test_the_diagnosis_job_reads_it_too(self):
        """A rerun is not the only thing that must not fire into the wall: the
        diagnosis agent would spend inference to report that the account was
        limited, which the marker already says."""
        self.assertIn("needs.classify.outputs.limit != 'true'", self.jobs["diagnose"]["if"])

    def test_the_step_never_fails_the_job(self):
        """Every medic job `needs: classify`; a step that can exit non-zero is
        a single point of failure in front of every back-off."""
        script = _limit_step()["run"]
        self.assertRegex(script, r"dead_run\.py decide[\s\S]*\|\| *DECISION=\"\"")

    def test_the_marker_site_is_declared_in_the_registry(self):
        import check_act_receipts  # noqa: PLC0415 — the guard is the assertion

        problems = [p for p in check_act_receipts.problems() if "medic.yml" in p]
        self.assertEqual([], problems)


if __name__ == "__main__":
    unittest.main()
