"""The medic reads a card from Linear once per run (Stage 2 fix #23, BP-6).

Before this change a medic run for the DRE-5620 shape (a Linear Sync run that
died on the fleet's exhausted Linear quota, on a card carrying `needs-human`)
sent six Linear requests, against the quota that had just run out:

  classify / retry gate    `card_facts`                               1 read
  classify / limit step    `dump-comments` (the marker de-duplication) 1 read
  classify / limit step    `comment` (the 🪦 marker): get_issue + write 1 + 1
  retry_declined           `post` (the 🩺 refusal): get_issue + write   1 + 1

Two of those go with fix #23 itself: the gate no longer refuses a Linear Sync
run, so `retry_declined` does not run. One goes here: the gate already holds
the card's newest fifty comments, so it leaves them in a snapshot file and the
limit step's de-duplication reads that file instead of asking Linear again. It
falls back to `dump-comments` only when the file is missing, which is when the
gate's own read failed open.

After: 1 read (the gate) + 1 read and 1 write (the marker) = three requests.
Every other card-bearing run with a limit death saves the one read too.

Measured here with `linear_ops.requests_made()`, the counter `gql` keeps for the
`linear-budget:` line, behind a fake transport. The live budget lines from the
two incident runs (37051307731, 37057166857) show the gate's read and both
writes; the `dump-comments` read never printed one because its stderr goes to
/dev/null, so that one is counted from the code, here.
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
os.environ.setdefault("REPO", "dreadnought-foundry/test")
os.environ.setdefault("GH_TOKEN", "test")

import dead_run  # noqa: E402
import linear_ops  # noqa: E402
import medic_retry  # noqa: E402

FIXTURES = os.path.join(ROOT, "tests", "fixtures")
WORKFLOW = os.path.join(ROOT, ".github", "workflows", "medic.yml")
DRE5620_LOG = os.path.join(
    FIXTURES, "linear-sync-card-done-ratelimited-dre5620-2026-10-02.log"
)
RUN_ID = "37051027773"
CARD = "DRE-5620"

# The marker the limit step greps for, exactly as medic.yml writes it.
_DEDUPE = r"limit-death:.* run={run}([^0-9]|$)"


class _Resp:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode()
        self.headers = {}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class _Linear:
    """A Linear stand-in that answers each query shape the medic sends and
    counts nothing itself: the count is `linear_ops.requests_made()`, the
    number the run's own budget line is computed from."""

    def __init__(self, comments=()):
        self.comments = list(comments)  # newest first, as Linear orders them

    def __call__(self, request, *_a, **_k):
        query = json.loads(request.data)["query"]
        window = {
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "nodes": [
                {"body": body, "createdAt": "2026-10-02T19:01:10Z",
                 "user": {"id": "fleet"}}
                for body in self.comments
            ],
        }
        if "commentCreate" in query:
            return _Resp({"data": {"commentCreate": {"success": True}}})
        if "viewer" in query:
            return _Resp({"data": {"viewer": {"id": "fleet"},
                                   "issue": {"comments": window}}})
        if "children(first: 1)" in query:
            return _Resp({"data": {"issue": {
                "id": "uuid-5620", "identifier": CARD, "title": "t",
                "team": {"id": "team"},
                "state": {"id": "s", "name": "In Review", "type": "started"},
                "labels": {"nodes": [{"name": dead_run.HOLD_LABEL}]},
                "children": {"nodes": []},
            }}})
        return _Resp({"data": {"issue": {
            "state": {"name": "In Review"},
            "labels": {"nodes": [{"name": "repo:agent-bureau"},
                                 {"name": dead_run.HOLD_LABEL}]},
            "comments": window,
        }}})


@contextlib.contextmanager
def _linear(comments=()):
    linear_ops._reset_budget_state()
    with mock.patch.object(linear_ops.urllib.request, "urlopen", _Linear(comments)):
        with contextlib.redirect_stderr(io.StringIO()):
            yield
    linear_ops._reset_budget_state()


def _gate(snapshot: str, *, branch="agent/DRE-5620-chain-risk-reason") -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        medic_retry.main([
            "decide", "--branch", branch, "--log", DRE5620_LOG,
            "--run-started-at", "2026-10-02T18:58:28Z",
            "--run-id", RUN_ID, "--run-attempt", "1",
            "--workflow", "Linear Sync", "--snapshot", snapshot,
        ])
    return buf.getvalue()


class TheGateLeavesItsReadBehindTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.snapshot = os.path.join(self.dir.name, "medic-card.json")

    def tearDown(self):
        self.dir.cleanup()

    def test_the_gate_reads_the_card_once(self):
        with _linear():
            _gate(self.snapshot)
            self.assertEqual(1, linear_ops.requests_made())

    def test_the_snapshot_is_the_facts_the_gate_decided_on(self):
        marker = dead_run.limit_marker("linear", "sync", None, "999")
        with _linear([marker]):
            _gate(self.snapshot)
        with open(self.snapshot, encoding="utf-8") as f:
            snap = json.load(f)
        self.assertEqual(CARD, snap["card"])
        self.assertEqual("In Review", snap["state"])
        self.assertIn(dead_run.HOLD_LABEL, snap["labels"])
        self.assertEqual([marker], [c["body"] for c in snap["comments"]])
        self.assertTrue(all(c["created_at"] for c in snap["comments"]))

    def test_the_limit_steps_grep_finds_this_runs_marker_in_the_snapshot(self):
        """The de-duplication reads the snapshot with the same pattern it used
        on `dump-comments`. Both are `json.dumps` of the bodies, so a marker
        found in one is found in the other."""
        mine = dead_run.limit_marker("linear", "sync", None, RUN_ID)
        with _linear([mine]):
            _gate(self.snapshot)
        with open(self.snapshot, encoding="utf-8") as f:
            text = f.read()
        self.assertRegex(text, _DEDUPE.format(run=re.escape(RUN_ID)))
        self.assertNotRegex(text, _DEDUPE.format(run="3705102777"))

    def test_a_failed_read_leaves_no_snapshot_so_the_step_asks_linear(self):
        """Fail-open stays fail-open: with no snapshot the limit step falls
        back to `dump-comments`. A stale file from an earlier job on the same
        machine must not stand in for this card's read."""
        with open(self.snapshot, "w", encoding="utf-8") as f:
            f.write('{"card": "DRE-1", "comments": []}')
        with mock.patch.object(
            medic_retry, "card_facts", side_effect=RuntimeError("linear down")
        ):
            with contextlib.redirect_stderr(io.StringIO()):
                _gate(self.snapshot)
        self.assertFalse(os.path.exists(self.snapshot))

    def test_a_run_with_no_card_sends_nothing_and_writes_nothing(self):
        with _linear():
            _gate(self.snapshot, branch="main")
            self.assertEqual(0, linear_ops.requests_made())
        self.assertFalse(os.path.exists(self.snapshot))

    def test_the_decision_lines_are_unchanged_by_the_snapshot(self):
        """Four `key=value` lines and nothing else on stdout: they go straight
        into $GITHUB_OUTPUT."""
        with _linear():
            out = _gate(self.snapshot)
        self.assertEqual(
            ["retry", "rule", "card", "detail"],
            [ln.split("=", 1)[0] for ln in out.splitlines() if ln.strip()],
        )


class TheIncidentShapeSpendsHalfTest(unittest.TestCase):
    """The six requests before and the three after, measured through the same
    seams medic.yml drives."""

    def test_before_six_after_three(self):
        mine = dead_run.limit_marker("linear", "sync", None, RUN_ID)
        with tempfile.TemporaryDirectory() as d:
            snapshot = os.path.join(d, "medic-card.json")
            with _linear():
                # BEFORE: the gate, the de-duplication's own thread read, the
                # marker, and the refusal the old gate answered with.
                medic_retry.card_facts(CARD)
                linear_ops.comment_bodies(CARD)
                with contextlib.redirect_stdout(io.StringIO()):
                    linear_ops.cmd_comment(CARD, mine)
                    medic_retry.post_declined(
                        CARD,
                        medic_retry.decide(parked_because="the label is on it"),
                    )
                before = linear_ops.requests_made()
            with _linear():
                # AFTER: the gate (which now leaves its read behind and no
                # longer refuses a Linear Sync run), then the marker.
                _gate(snapshot)
                with contextlib.redirect_stdout(io.StringIO()):
                    linear_ops.cmd_comment(CARD, mine)
                after = linear_ops.requests_made()
        self.assertEqual(6, before)
        self.assertEqual(3, after)


def _limit_step() -> dict:
    with open(WORKFLOW, encoding="utf-8") as f:
        steps = yaml.safe_load(f)["jobs"]["classify"]["steps"]
    return {s.get("id"): s for s in steps}


class MedicWiringTest(unittest.TestCase):
    def setUp(self):
        self.steps = _limit_step()

    def test_the_gate_writes_the_snapshot_in_the_jobs_own_temp(self):
        """$RUNNER_TEMP, never /tmp: a self-hosted machine runs several jobs,
        and /tmp would hand one medic run another card's comments."""
        run = self.steps["r"]["run"]
        self.assertIn('--snapshot "$RUNNER_TEMP/medic-card.json"', run)

    def test_the_limit_step_reads_the_snapshot_before_asking_linear(self):
        run = self.steps["limit"]["run"]
        self.assertIn('"$RUNNER_TEMP/medic-card.json"', run)
        self.assertIn("-s ", run)
        # The fallback stays, for a gate that failed open.
        self.assertIn("dump-comments", run)
        self.assertLess(
            run.index("$RUNNER_TEMP/medic-card.json"), run.index("dump-comments")
        )


if __name__ == "__main__":
    unittest.main()
