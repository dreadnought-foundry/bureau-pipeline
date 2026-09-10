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
  5. A TURN-CAP result is none of the above (DRE-3499): the step publishes
     `limit=false`, posts no marker and says so, even though the failed log
     carries `rate_limit_error` in prose the agent read.

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

import medic_retry  # noqa: E402 — the one card-resolution rule

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
    "github.token": "gh-test-token",
}
MARKER = (
    f"🪦 limit-death: kind=claude stage=build reset=2026-09-06T20:30:00Z run={RUN_ID}\n\n"
    "This run hit the Claude account's usage limit during the build stage."
)

SHIM = r"""#!/bin/bash
# Records every python3 call the step makes, one line per call, and answers
# the three the step needs: the classifier, the card's thread, the comment.
# With FAKE_DECISION empty the classifier is the REAL dead_run.py, run by the
# real interpreter on the fake failed log.
printf '%s\n' "$*" >> "$SHIM_LOG"
case "$*" in
  *dead_run.py\ decide*)
    if [ "${FAKE_DECIDE_RC:-0}" != "0" ]; then exit "$FAKE_DECIDE_RC"; fi
    if [ -n "${FAKE_DECISION:-}" ]; then printf '%s\n' "$FAKE_DECISION"; exit 0; fi
    ARGS=(); for a in "${@:2}"; do [ "$a" = "/tmp/medic-log.txt" ] && a="$FAKE_LOG"; ARGS+=("$a"); done
    exec "$REAL_PYTHON" "$REAL_DEAD_RUN" "${ARGS[@]}" ;;
  *linear_ops.py\ dump-comments*)
    printf '%s\n' "${FAKE_COMMENTS:-[]}"; exit 0 ;;
  *linear_ops.py\ comment*)
    exit "${FAKE_COMMENT_RC:-0}" ;;
esac
exit 0
"""

GH_SHIM = r"""#!/bin/bash
# The Jobs API read the step makes for the failed step's name (DRE-3171,
# second review): answers FAKE_FAILED_STEP, or fails with FAKE_GH_RC.
printf 'gh %s\n' "$*" >> "$SHIM_LOG"
if [ "${FAKE_GH_RC:-0}" != "0" ]; then echo "gh: boom" >&2; exit "$FAKE_GH_RC"; fi
printf '%s\n' "${FAKE_FAILED_STEP:-}"
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


def run_step(*, decision: str | None = "limit\n\n" + MARKER, comments: str = "[]",
             account: str = "", head_branch: str | None = None, log_text: str = "",
             payload: dict | None = None, failed_step: str = "", gh_rc: int = 0,
             decide_rc: int = 0, comment_rc: int = 0) -> dict:
    """Execute the classify job's limit step as the workflow would.

    Returns the recorded python3 calls, the GITHUB_OUTPUT lines, the exit
    status and the combined output."""
    step = _limit_step()
    extra = {"vars.CLAUDE_ACCOUNT": account, **(payload or {})}
    if head_branch is not None:
        extra["github.event.workflow_run.head_branch"] = head_branch
    # The card is the retry gate's own output (steps.r), resolved by the ONE
    # function — the head branch first, then the failed log a planner run
    # names itself in (DRE-3223) — so the fake publishes it the way that step
    # does, through that same function, never by hand.
    extra["steps.r.outputs.card"] = medic_retry.card_for_run(
        extra.get("github.event.workflow_run.head_branch", PAYLOAD["github.event.workflow_run.head_branch"]),
        log_text,
    ) or ""
    env = {k: _render(str(v), extra) for k, v in (step.get("env") or {}).items()}
    script = _render(step["run"], extra)
    with tempfile.TemporaryDirectory() as raw:
        td = Path(raw)
        (td / "python3").write_text(SHIM)
        os.chmod(td / "python3", 0o755)
        (td / "gh").write_text(GH_SHIM)
        os.chmod(td / "gh", 0o755)
        fake_log = td / "medic-log.txt"
        fake_log.write_text(log_text)
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
                "GITHUB_REPOSITORY": "dreadnought-foundry/bureau-pipeline",
                "SHIM_LOG": str(log),
                "FAKE_DECISION": decision or "",
                "FAKE_LOG": str(fake_log),
                "REAL_PYTHON": sys.executable,
                "REAL_DEAD_RUN": str(ROOT / "scripts" / "dead_run.py"),
                "FAKE_FAILED_STEP": failed_step,
                "FAKE_GH_RC": str(gh_rc),
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


# ── 1b. the failed step, off the Jobs API (second review of #279) ───────────
CLASSIFY_STEP = "Classify the card — one-off, epic or wave"
PLAN_RUN = {"github.event.workflow_run.name": "Agent Plan (reusable)"}
PLAN_LOG_CLAUDE = (
    "call / plan\tUNKNOWN STEP\t2026-09-05T20:40:01.0000000Z "
    '{"type":"result","is_error":true,"result":"You\'ve hit your limit · resets 8:30pm (UTC)","num_turns":12}\n'
)


class FailedStepTest(unittest.TestCase):
    """`stage=classify` was documented and unreachable: the workflow name is
    'Agent Plan (reusable)' whether the classifier or the planner died, and
    the step never passed `--failed-step`. The Jobs API names the failed
    step; one read, and the record says which of the two it was."""

    def test_the_failed_step_is_read_off_the_jobs_api_and_passed_through(self):
        result = run_step(failed_step=CLASSIFY_STEP)
        gh_calls = [c for c in result["calls"] if c.startswith("gh ")]
        self.assertEqual(1, len(gh_calls), result["calls"])
        self.assertIn(f"run view {RUN_ID}", gh_calls[0])
        self.assertIn("--json jobs", gh_calls[0])
        self.assertIn(f"--failed-step {CLASSIFY_STEP}", _decide_call(result))

    def test_a_classifier_death_on_a_planner_run_is_stage_classify(self):
        result = run_step(decision=None, log_text=PLAN_LOG_CLAUDE, payload=PLAN_RUN,
                          failed_step=CLASSIFY_STEP)
        self.assertEqual(0, result["rc"], result["text"])
        self.assertIn("limit=true", result["outputs"])
        posts = _comment_calls(result)
        self.assertEqual(1, len(posts), result["calls"])
        self.assertIn("🪦 limit-death: kind=claude stage=classify", posts[0])

    def test_a_planner_death_on_a_planner_run_is_stage_plan(self):
        result = run_step(decision=None, log_text=PLAN_LOG_CLAUDE, payload=PLAN_RUN,
                          failed_step="Plan epic")
        self.assertIn("🪦 limit-death: kind=claude stage=plan", _comment_calls(result)[0])

    def test_an_unreadable_jobs_api_still_marks_the_death_and_says_plan(self):
        """The read is a refinement of the record, never a condition of it."""
        result = run_step(decision=None, log_text=PLAN_LOG_CLAUDE, payload=PLAN_RUN, gh_rc=1)
        self.assertEqual(0, result["rc"], result["text"])
        self.assertIn("limit=true", result["outputs"])
        self.assertIn("🪦 limit-death: kind=claude stage=plan", _comment_calls(result)[0])


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


# ── 5. a planner run's card comes from its own log (DRE-3223) ────────────────
PLANNER = {"github.event.workflow_run.name": "Agent Plan (reusable)",
           "github.event.workflow_run.head_branch": "main"}
PLANNER_LOG_CLAUDE = (
    "call / bureau-card: DRE-3162\tUNKNOWN STEP\t2026-09-05T20:36:52.3466088Z bureau-card: DRE-3162\n"
    "call / bureau-card: DRE-3162\tUNKNOWN STEP\t2026-09-05T20:40:01.0000000Z "
    '{"type":"result","is_error":true,"result":"You\'ve hit your limit · resets 8:30pm (UTC)","num_turns":12}\n'
)
PLANNER_LOG_LINEAR = (
    "call / bureau-card: DRE-3162\tUNKNOWN STEP\t2026-09-05T20:36:52.3466088Z bureau-card: DRE-3162\n"
    "call / bureau-card: DRE-3162\tUNKNOWN STEP\t2026-09-05T20:40:01.0000000Z "
    "linear_ops.LinearRateLimited: Linear API returned 400 from https://api.linear.app/graphql: "
    "rate limited: 2500 requests/hour exhausted\n"
)


class PlannerCardTest(unittest.TestCase):
    """DRE-3223's acceptance cases, driven through the REAL classifier on a
    fake failed log, with the card coming from the retry gate's one
    resolution function. On 2026-09-05 every planner limit death (DRE-3162,
    3130, 3072, 3169, 3168 ×2) had the gate and no card to mark."""

    def test_a_claude_limit_on_a_planner_run_marks_the_card_the_log_names(self):
        result = run_step(decision=None, log_text=PLANNER_LOG_CLAUDE, payload=PLANNER,
                          failed_step="Plan epic")
        self.assertEqual(0, result["rc"], result["text"])
        self.assertIn("limit=true", result["outputs"], "no rerun, no diagnosis")
        posts = _comment_calls(result)
        self.assertEqual(1, len(posts), result["calls"])
        self.assertIn(" DRE-3162 ", posts[0])
        self.assertIn(f"🪦 limit-death: kind=claude stage=plan reset=2026-09-06T20:30:00Z run={RUN_ID}", posts[0])

    def test_a_linear_limit_on_a_planner_run_marks_kind_linear(self):
        result = run_step(decision=None, log_text=PLANNER_LOG_LINEAR, payload=PLANNER,
                          failed_step="Plan epic")
        self.assertIn("limit=true", result["outputs"])
        posts = _comment_calls(result)
        self.assertEqual(1, len(posts), result["calls"])
        self.assertIn(" DRE-3162 ", posts[0])
        self.assertIn(f"🪦 limit-death: kind=linear stage=plan reset=unknown run={RUN_ID}", posts[0])

    def test_a_build_run_still_resolves_its_card_from_the_branch(self):
        """The branch wins when both speak: a build log can quote any card."""
        log = PLANNER_LOG_CLAUDE.replace("bureau-card: DRE-3162", "bureau-card: DRE-1")
        result = run_step(decision=None, log_text=log)
        posts = _comment_calls(result)
        self.assertEqual(1, len(posts))
        self.assertIn(" DRE-3062 ", posts[0])
        self.assertIn("stage=build", posts[0])

    def test_a_planner_log_naming_no_card_still_blocks_the_rerun_and_posts_nothing(self):
        log = PLANNER_LOG_CLAUDE.replace("bureau-card: DRE-3162", "")
        result = run_step(decision=None, log_text=log, payload=PLANNER)
        self.assertIn("limit=true", result["outputs"])
        self.assertEqual([], _comment_calls(result))


TURN_CAP_LOG = (ROOT / "tests" / "fixtures"
                / "medic-turn-cap-over-ceiling-log.txt").read_text(encoding="utf-8")


class TurnCapIsNotALimitDeathTest(unittest.TestCase):
    """DRE-3499, through the step's own shell and the REAL classifier.

    On 2026-09-07 at 16:50Z this step posted `🪦 limit-death: kind=claude
    stage=plan reset=unknown` on epic DRE-3257 for agent-bureau run
    34144302622. Nothing in that run hit an account limit: the post-approval
    review ran 51 turns against a 48-turn ceiling and ended `"subtype":
    "success"`. The log carried `rate_limit_error` because the reviewer had
    READ the standard that quotes it — and `limit_recovery.py` reads the
    marker and re-enters the plan stage when the window it never hit resets.

    The fixture is that run's log, synthesised from the fields the epic
    records (the run is not readable from this repo's runner).
    """

    def test_the_card_is_resolvable_so_the_absence_of_a_marker_is_the_verdict(self):
        """Not "no card to mark" — the log names the epic the medic marked."""
        self.assertEqual("DRE-3257", medic_retry.card_for_run("main", TURN_CAP_LOG))

    def test_the_step_publishes_false_and_posts_no_marker(self):
        result = run_step(decision=None, log_text=TURN_CAP_LOG, payload=PLANNER,
                          failed_step="Plan epic")
        self.assertEqual(0, result["rc"], result["text"])
        self.assertIn("limit=false", result["outputs"])
        self.assertNotIn("limit=true", result["outputs"])
        self.assertEqual([], _comment_calls(result), result["calls"])
        self.assertNotIn("🪦 limit-death:", result["text"])

    def test_the_step_says_the_death_is_not_a_limit_death(self):
        result = run_step(decision=None, log_text=TURN_CAP_LOG, payload=PLANNER,
                          failed_step="Plan epic")
        self.assertIn("not a limit death", result["text"])
        self.assertIn("requeue", result["text"], "the ordinary medic handling")


class CardResolutionTest(unittest.TestCase):
    """One function, two sources — and the log source is structural."""

    def test_branch_first(self):
        self.assertEqual("DRE-3062", medic_retry.card_for_run("agent/dre-3062-x", PLANNER_LOG_CLAUDE))

    def test_log_when_the_branch_names_nothing(self):
        self.assertEqual("DRE-3162", medic_retry.card_for_run("main", PLANNER_LOG_CLAUDE))

    def test_the_job_name_field_alone_is_enough(self):
        only_prefix = "call / bureau-card: DRE-3162\tPlanner agent\t2026-09-05T20:40:01Z is_error\n"
        self.assertEqual("DRE-3162", medic_retry.card_for_run("main", only_prefix))

    def test_the_echoed_line_alone_is_enough(self):
        only_echo = "call / plan\tSay the card\t2026-09-05T20:36:52.3466088Z bureau-card: DRE-3162\n"
        self.assertEqual("DRE-3162", medic_retry.card_for_run("main", only_echo))

    def test_a_bare_line_is_enough(self):
        self.assertEqual("DRE-3162", medic_retry.card_for_run("main", "bureau-card: dre-3162\n"))

    def test_quoted_prose_does_not_name_a_card(self):
        prose = "call / plan\tPlanner agent\t2026-09-05T20:40:01Z the card says \"bureau-card: DRE-9\" here\n"
        self.assertIsNone(medic_retry.card_for_run("main", prose))
        echoed_script = "call / plan\tUNKNOWN STEP\t2026-09-05T20:40:01Z \x1b[36;1mecho \"bureau-card: DRE-9\"\x1b[0m\n"
        self.assertIsNone(medic_retry.card_for_run("main", echoed_script))

    def test_nothing_names_nothing(self):
        self.assertIsNone(medic_retry.card_for_run("main", ""))
        self.assertIsNone(medic_retry.card_for_run("chore/deps", "no card here"))

    def test_the_gate_uses_the_one_function(self):
        import inspect
        self.assertIn("card_for_run(", inspect.getsource(medic_retry._decide_cli))


class PlannerSaysItsCardTest(unittest.TestCase):
    """plan.yml carries the card in the two places the failed log keeps: its
    job name (the first field of every log line — survives a gh that prints
    only failed steps) and one echoed line at the top of the job."""

    def setUp(self):
        with open(ROOT / ".github" / "workflows" / "plan.yml", encoding="utf-8") as f:
            self.plan = yaml.safe_load(f)

    def test_the_job_is_named_after_the_card(self):
        self.assertEqual(
            "bureau-card: ${{ github.event.client_payload.identifier }}",
            self.plan["jobs"]["plan"].get("name"),
        )

    def test_the_first_step_says_the_card(self):
        first = self.plan["jobs"]["plan"]["steps"][0]
        self.assertIn('echo "bureau-card: $CARD"', first.get("run", ""))
        self.assertEqual("${{ github.event.client_payload.identifier }}", (first.get("env") or {}).get("CARD"))
