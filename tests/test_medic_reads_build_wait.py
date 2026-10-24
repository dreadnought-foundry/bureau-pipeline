"""The medic reads a build death the build already marked as a wait as a wait
(DRE-7805).

After DRE-7802 a build killed by our own token renewal (`kind=rotation`) or a
brief 529/5xx (`kind=vendor-transient`) posts one `🪦 limit-death:` marker
(DRE-7800) and still ends red, so the medic wakes on it. The failed LOG shows
neither a usage limit nor Linear's quota, so `dead_run.py decide --limit-log`
reads an ordinary death: the medic would re-run the build at once, and on
attempt 2 diagnose it and post `run-failure-diagnosed` — whose act trailer
`limit_recovery.is_receipt` reads as the pipeline superseding the marker, so
the card would never be brought back.

What must hold:

  1. The classify job's `limit` step asks the card first, through DRE-7792's
     `medic_next_step.py waiting --run-id`, on the retry gate's snapshot (as
     the REAL `medic_retry.write_snapshot` writes it) or on a `dump-comments`
     array. `waiting=true` publishes `limit=true`, calls no `dead_run.py` and
     posts nothing.
  2. A marker naming another run, or one a `🔁` recovery receipt closed, and
     every failure to read or ask, leave the step on today's path.
  3. No job that posts on the card runs while `limit=true` if what it posts
     would close a standing marker. The walk below composes each writer's
     comment through its own module and asks `limit_recovery.waiting`.

The step is EXECUTED the way tests/test_medic_limit_death_wiring.py drives it
— rendered from a fake `workflow_run` payload and run under `bash -e -o
pipefail` — with a python3 shim that runs the REAL `medic_next_step.py`.

Run: cd bureau-pipeline && python3 -m pytest tests/test_medic_reads_build_wait.py -v
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
WORKFLOW = ROOT / ".github" / "workflows" / "medic.yml"

import dead_run
import limit_recovery
import linear_ops
import medic_retry
import reviewer_environment
import stream_watchdog
import test_medic_limit_death_wiring as wiring  # the one renderer

RUN_ID = wiring.RUN_ID
CARD = "DRE-3062"  # the card the wiring payload's head branch names
ENDED = datetime(2026, 10, 24, 17, 5, tzinfo=UTC)
ORDINARY = "requeue\n\n🪦 dead-run-requeue: the agent step failed"
GATE = "needs.classify.outputs.limit != 'true'"
# A comment on the thread that is no marker; it names no model, so no
# console save reaches it (tests/test_no_test_pins_a_console_choice.py).
CHATTER = "🔨 build picked up the card"


def wait_marker(kind: str, run_id: str = RUN_ID) -> str:
    """The marker the build writes (DRE-7800), through its one writer."""
    return dead_run.wait_death(kind, run_id, ENDED).marker()


def recovery_receipt(marker: str) -> str:
    """Limit recovery's own `🔁` receipt for that marker."""
    return limit_recovery.recovery_receipt(
        dead_run.parse_limit_marker(marker), "window reset at 10:15am PT",
        "Moved In Progress → In Progress, which dispatches a fresh run")


def facts(bodies) -> dict:
    """`medic_retry.card_facts`'s shape: comments as `{body, created_at}`."""
    return {
        "state": "In Progress",
        "labels": ["repo:bureau-pipeline", "agent:devops"],
        "comments": [
            {"body": body, "created_at": f"2026-10-24T17:0{i}:00.000Z"}
            for i, body in enumerate(bodies)
        ],
    }


SHIM = r"""#!/bin/bash
# Records every python3 call. `waiting` is the REAL medic_next_step.py on the
# real interpreter; the classifier, the thread read and the marker write are
# answered from the FAKE_* variables.
printf '%s\n' "$*" >> "$SHIM_LOG"
case "$*" in
  *medic_next_step.py\ waiting*)
    if [ "${FAKE_WAITING_RC:-0}" != "0" ]; then echo "waiting: boom" >&2; exit "$FAKE_WAITING_RC"; fi
    exec "$REAL_PYTHON" "$REAL_SCRIPTS/medic_next_step.py" "${@:2}" ;;
  *dead_run.py\ decide*)
    printf '%s\n' "$FAKE_DECISION"; exit 0 ;;
  *linear_ops.py\ dump-comments*)
    if [ "${FAKE_DUMP_RC:-0}" != "0" ]; then echo "linear: down" >&2; exit "$FAKE_DUMP_RC"; fi
    printf '%s\n' "${FAKE_COMMENTS:-[]}"; exit 0 ;;
  *limit_death_record.py\ post*)
    echo "marker=written"; exit 0 ;;
esac
exit 0
"""


def run_step(*, snapshot: list | None = None, comments: list | None = None,
             decision: str = ORDINARY, dump_rc: int = 0, waiting_rc: int = 0,
             head_branch: str | None = None) -> dict:
    """Execute the classify job's `limit` step.

    `snapshot` is the bodies the retry gate read, written to
    `$RUNNER_TEMP/medic-card.json` by the REAL `medic_retry.write_snapshot`;
    None leaves no file, the shape a gate that failed open leaves.
    `comments` is what `dump-comments` prints, as the bodies array it is."""
    step = wiring._limit_step()
    extra = {"vars.CLAUDE_ACCOUNT": ""}
    branch = head_branch or wiring.PAYLOAD["github.event.workflow_run.head_branch"]
    extra["github.event.workflow_run.head_branch"] = branch
    extra["steps.r.outputs.card"] = medic_retry.card_for_run(branch, "") or ""
    env = {k: wiring._render(str(v), extra) for k, v in (step.get("env") or {}).items()}
    script = wiring._render(step["run"], extra)
    with tempfile.TemporaryDirectory() as raw:
        td = Path(raw)
        temp = td / "runner-temp"
        temp.mkdir()
        snapshot_path = temp / "medic-card.json"
        if snapshot is not None:
            medic_retry.write_snapshot(str(snapshot_path), CARD, facts(snapshot))
            assert snapshot_path.exists(), "write_snapshot wrote no file"
        (td / "python3").write_text(SHIM)
        os.chmod(td / "python3", 0o755)
        (td / "gh").write_text(wiring.GH_SHIM)
        os.chmod(td / "gh", 0o755)
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
                "RUNNER_TEMP": str(temp),
                "GITHUB_OUTPUT": str(out),
                "GITHUB_REPOSITORY": "dreadnought-foundry/bureau-pipeline",
                "SHIM_LOG": str(log),
                "REAL_PYTHON": sys.executable,
                "REAL_SCRIPTS": str(ROOT / "scripts"),
                "FAKE_DECISION": decision,
                "FAKE_COMMENTS": json.dumps(comments if comments is not None else []),
                "FAKE_DUMP_RC": str(dump_rc),
                "FAKE_WAITING_RC": str(waiting_rc),
            },
        )
        return {
            "calls": [line for line in log.read_text().splitlines() if line],
            "outputs": out.read_text().splitlines(),
            "rc": proc.returncode,
            "text": proc.stdout + proc.stderr,
            "snapshot": str(snapshot_path),
        }


def _calls(result: dict, needle: str) -> list:
    return [c for c in result["calls"] if needle in c]


def _posts(result: dict) -> list:
    return (_calls(result, "limit_death_record.py post")
            + _calls(result, "linear_ops.py comment"))


# ── 1. the build's own wait marker is a limit death ─────────────────────────
class TheBuildsWaitIsAWaitTest(unittest.TestCase):
    def assert_waits(self, result: dict) -> None:
        self.assertEqual(0, result["rc"], result["text"])
        self.assertIn("limit=true", result["outputs"])
        self.assertNotIn("limit=false", result["outputs"])
        self.assertEqual([], _calls(result, "dead_run.py"), result["calls"])
        self.assertEqual([], _posts(result), "the build's marker is the only thing written")
        self.assertIn("limit recovery brings the card back", result["text"].lower())

    def test_a_vendor_transient_marker_in_the_snapshot(self):
        result = run_step(snapshot=[CHATTER,
                                    wait_marker("vendor-transient")])
        self.assert_waits(result)
        asked = _calls(result, "medic_next_step.py waiting")
        self.assertEqual(1, len(asked), result["calls"])
        self.assertIn(f"--comments-file {result['snapshot']}", asked[0],
                      "the snapshot's own path, never a reshaped copy")
        self.assertIn(f"--run-id {RUN_ID}", asked[0])
        self.assertEqual([], _calls(result, "dump-comments"), "the gate's read is reused")

    def test_a_rotation_marker_in_the_snapshot(self):
        self.assert_waits(run_step(snapshot=[wait_marker("rotation")]))

    def test_the_marker_in_a_dump_comments_array_when_no_snapshot(self):
        result = run_step(comments=[CHATTER,
                                    wait_marker("vendor-transient")])
        self.assert_waits(result)
        self.assertEqual(1, len(_calls(result, f"dump-comments {CARD}")), result["calls"])

    def test_nothing_else_runs_not_even_the_failed_step_read(self):
        result = run_step(snapshot=[wait_marker("rotation")])
        self.assertEqual([], [c for c in result["calls"] if c.startswith("gh ")])


# ── 2. today's path, for everything that is not this run's standing wait ────
class EverythingElseRunsAsTodayTest(unittest.TestCase):
    def assert_today(self, result: dict) -> None:
        self.assertEqual(0, result["rc"], result["text"])
        decide = _calls(result, "dead_run.py decide")
        self.assertEqual(1, len(decide), result["calls"])
        self.assertIn("--limit-log /tmp/medic-log.txt", decide[0])
        self.assertIn(f"--run-id {RUN_ID}", decide[0])
        self.assertIn("limit=false", result["outputs"])
        self.assertIn("not a limit death", result["text"])

    def test_a_marker_naming_another_run(self):
        other = str(int(RUN_ID) - 7)
        self.assert_today(run_step(snapshot=[wait_marker("vendor-transient", other)]))

    def test_a_marker_a_recovery_receipt_closed(self):
        marker = wait_marker("vendor-transient")
        self.assert_today(run_step(snapshot=[marker, recovery_receipt(marker)]))

    def test_a_thread_with_no_marker(self):
        self.assert_today(run_step(snapshot=[CHATTER]))

    def test_no_snapshot_and_a_failing_dump_comments(self):
        result = run_step(dump_rc=1)
        self.assert_today(result)

    def test_a_failing_waiting_call_says_so_and_carries_on(self):
        result = run_step(snapshot=[wait_marker("vendor-transient")], waiting_rc=2)
        self.assert_today(result)
        self.assertIn("::warning", result["text"])

    def test_no_card_asks_nothing(self):
        result = run_step(head_branch="main")
        self.assert_today(result)
        self.assertEqual([], _calls(result, "medic_next_step.py waiting"))
        self.assertEqual([], _calls(result, "dump-comments"))

    def test_a_real_limit_death_still_writes_its_marker_once(self):
        """The de-duplication after `decide` is unchanged: this run's limit
        marker is posted when the thread lacks it, and one read serves both
        questions."""
        decision = "limit\n\n" + wiring.MARKER
        result = run_step(comments=[CHATTER], decision=decision)
        self.assertIn("limit=true", result["outputs"])
        self.assertEqual(1, len(_calls(result, "limit_death_record.py post")), result["calls"])
        self.assertEqual(1, len(_calls(result, "dump-comments")), result["calls"])
        again = run_step(snapshot=[wiring.MARKER], decision=decision)
        self.assertEqual([], _calls(again, "limit_death_record.py post"), again["calls"])


# ── 3. limit=true stops every writer that would close the marker ───────────
WRITERS = (
    "linear_ops.py comment",
    "medic_retry.py post",
    "stream_watchdog.py record",
    "reviewer_environment.py note",
    "reviewer_environment.py hold",
)
# A stopped run's log as `gh run view --log-failed` prints it: the one line
# the watchdog writes, through its own writer.
STALL_LOG = Path(tempfile.mkdtemp()) / "stall-log.txt"
STALL_LOG.write_text(
    "build\tRun claude\t2026-10-24T17:04:12.0000000Z "
    + stream_watchdog.stall_line(stream_watchdog.Stall(
        step="Run claude", last_event="2026-10-24T16:59:00Z",
        silence_seconds=312, streamed_seconds=0)) + "\n",
    encoding="utf-8")
# One fixture per flag a writer is called with; a `$VAR` this table does not
# cover fails the walk rather than being guessed at.
FIXTURE = {
    "--card": CARD,
    "--rule": "parked",
    "--detail": "the card carries the needs-human label",
    "--run-url": f"https://github.com/o/r/actions/runs/{RUN_ID}",
    "--sha": "4f2a" * 10,
    "--signature": reviewer_environment.SIGNATURES[0].slug,
    "--log": str(STALL_LOG),
    "--run-id": RUN_ID,
    "--run-attempt": "1",
    "--retrying": "false",
    "--limit": "true",
    "--count": "twice",
    "--subject": "Agent Task (reusable)",
}
# A path the step itself fills before the writer runs (`gh run view
# --log-failed > /tmp/stall-log.txt`) takes its fixture too.
FILLED_BY_THE_STEP = {"--log"}
FIXTURE_BODY = "The build failed at the agent step; the report says why."


def _medic() -> dict:
    with open(WORKFLOW, encoding="utf-8") as f:
        return yaml.safe_load(f)


def writer_calls(run: str) -> list:
    """Every card-writer call in a step's shell: `(writer, argv)`, argv as the
    shell would split it, up to the end of that command."""
    text = run.replace("\\\n", " ")
    found = []
    for m in re.finditer(r"python3 \S*/scripts/(\w+\.py) ([\w-]+)", text):
        writer = f"{m.group(1)} {m.group(2)}"
        if writer not in WRITERS:
            continue
        rest = text[m.end():].split("\n", 1)[0]
        lexer = shlex.shlex(rest, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        argv = []
        for token in lexer:
            if token and set(token) <= set("|&;()<>"):
                break
            argv.append(token)
        found.append((writer, argv))
    return found


def _fill(argv: list, positional: list) -> list:
    """Each `$`-bearing value replaced by its fixture."""
    filled, flag, at = [], "", 0
    for token in argv:
        if token.startswith("--"):
            flag = "" if "=" in token else token
        elif flag:
            if "$" in token or flag in FILLED_BY_THE_STEP:
                if flag not in FIXTURE:
                    raise AssertionError(f"no fixture for {flag} {token!r}")
                token = FIXTURE[flag]
            flag = ""
        else:
            if "$" in token:
                token = positional[at]
            at += 1
        filled.append(token)
    return filled


def compose(writer: str, argv: list) -> list:
    """The bodies the writer posts on the card, through its own module, with
    only Linear's transport replaced."""
    posted = []

    def gql(query, variables=None, **_kw):
        if "commentCreate" in query:
            posted.append(variables["input"]["body"])
        return {"commentCreate": {"success": True}}

    script, command = writer.split()
    with mock.patch.object(linear_ops, "get_issue", return_value={"id": "uuid"}), \
            mock.patch.object(linear_ops, "gql", side_effect=gql), \
            contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(io.StringIO()):
        if script == "linear_ops.py":
            card, body, *flags = _fill(argv, [CARD, FIXTURE_BODY])
            linear_ops.cmd_comment(card, body, *flags)
        else:
            module = {"medic_retry.py": medic_retry,
                      "stream_watchdog.py": stream_watchdog,
                      "reviewer_environment.py": reviewer_environment}[script]
            rc = module.main([command, *_fill(argv, [])])
            assert not rc, f"{writer} exited {rc}"
    assert posted, f"{writer} {argv} posted nothing on the card"
    return posted


def closes_a_marker(body: str) -> bool:
    marker = wait_marker("vendor-transient")
    assert limit_recovery.waiting([marker]) is not None
    return limit_recovery.waiting([marker, body]) is None


def _top_level_and(expr: str) -> list:
    terms, depth, start = [], 0, 0
    text = " ".join(str(expr).split())
    for i, ch in enumerate(text):
        depth += ch == "("
        depth -= ch == ")"
        if depth == 0 and text.startswith("&&", i):
            terms.append(text[start:i].strip())
            start = i + 2
    terms.append(text[start:].strip())
    return terms


def card_writing_jobs() -> dict:
    """job name → [(writer, argv, bodies)] for every job that needs classify."""
    jobs = {}
    for name, job in _medic()["jobs"].items():
        needs = job.get("needs")
        if "classify" not in ([needs] if isinstance(needs, str) else (needs or [])):
            continue
        for step in job.get("steps") or []:
            for writer, argv in writer_calls(step.get("run") or ""):
                jobs.setdefault(name, []).append((writer, argv, compose(writer, argv)))
    return jobs


class EveryWriterStandsDownOnAWaitTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.jobs = _medic()["jobs"]
        cls.writing = card_writing_jobs()

    def test_the_walk_finds_the_card_writers(self):
        for name in ("retry_declined", "stall_record", "backoff",
                     "environment_hold", "diagnose"):
            self.assertIn(name, self.writing)

    def test_a_job_whose_comment_closes_a_marker_carries_the_gate(self):
        ungated = []
        for name, calls in self.writing.items():
            closing = [w for w, _argv, bodies in calls
                       if any(closes_a_marker(b) for b in bodies)]
            if closing and GATE not in _top_level_and(self.jobs[name]["if"]):
                ungated.append(f"{name} ({', '.join(closing)})")
        self.assertEqual([], ungated, "these jobs would close the build's wait marker")

    def test_the_three_new_gates(self):
        for name in ("retry_declined", "backoff", "environment_hold"):
            self.assertIn(GATE, _top_level_and(self.jobs[name]["if"]), name)
            self.assertTrue(any(closes_a_marker(b) for _w, _a, bodies in self.writing[name]
                                for b in bodies), f"{name}'s comment closes a marker")

    def test_the_stall_record_does_not_close_a_marker_and_stays_ungated(self):
        bodies = [b for _w, _a, bs in self.writing["stall_record"] for b in bs]
        self.assertFalse(any(closes_a_marker(b) for b in bodies), bodies)
        self.assertNotIn(GATE, _top_level_and(self.jobs["stall_record"]["if"]))

    def test_the_retry_and_the_diagnosis_still_read_limit(self):
        for name in ("retry", "diagnose"):
            self.assertIn(GATE, _top_level_and(self.jobs[name]["if"]), name)


if __name__ == "__main__":
    unittest.main()
