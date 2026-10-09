"""Both restarts an Operator decision can start leave a live review-cap hold
alone (DRE-6247).

When the review lane's re-trigger budget is spent the sweep parks the card in
Green Light under a `review-cap-spent` stamp (DRE-6181), and the way back is a
new head on the pull request. An **Operator decision** comment starts the fix
agent once on the person's words, and the fix it pushes is that head. Two paths
act on the decision, and before this card both would have broken the hold:

  * the sweep's `reconcile._release_card` (DRE-2409) took the label off, moved
    Triage → In Review (a no-op for a card in Green Light) and posted its note;
  * the comment-started run's `Announce fix attempt` step (DRE-2601) took the
    label off with no lift line.

Either way the card was left in Green Light with the label gone and the stamp
still live, so the holds lane — which pages the cards carrying the label —
never met it again on the new head. Both paths now read the hold's reason
first, through `hold.reason_of`, and stand down on `review-cap-spent` alone.

Every other reason — no label, a bare label (`manual`), `fix-dispute`, a spent
`review-cap-spent` stamp — takes exactly the path it took on `main`.

Run: python3 -m pytest tests/test_review_cap_release.py -v
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/test")
os.environ.setdefault("GH_TOKEN", "test")

import hold  # noqa: E402
import reconcile  # noqa: E402
import step_shell  # noqa: E402

WORKFLOW = os.path.join(ROOT, ".github", "workflows", "agent-fix.yml")

CARD = "DRE-6247"
PR_NUMBER = 41
HEAD = "a1b2c3d4" * 5
CALLER_NOTE = (
    f"🔓 Your answer on PR #{PR_NUMBER} was picked up — the fix agent is "
    "running again and this card is out of your queue. Nothing more needed "
    "from you."
)


def _pr() -> dict:
    return {"number": PR_NUMBER, "headRefName": f"agent/{CARD}-x"}


def _stamp(reason: str, at: str) -> str:
    return hold.stamp_line(reason, at, "reconcile.py")


def _issue(labels, bodies) -> dict:
    """A card as the Linear API answers it — the comment window newest-first,
    the order `linear_ops.window_nodes` reverses."""
    return {
        "issue": {
            "state": {"name": "Green Light"},
            "labels": {"nodes": [{"name": n} for n in labels]},
            "comments": {
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "nodes": [{"body": b} for b in reversed(bodies)],
            },
        }
    }


def release(labels, bodies):
    """Run `_release_card` with every Linear call patched. Returns the patched
    `gql` (the reads) and the three writes."""
    payload = _issue(labels, bodies)
    ops = reconcile.linear_ops
    with mock.patch.object(ops, "gql", return_value=payload) as gql, \
         mock.patch.object(ops, "remove_label") as remove_label, \
         mock.patch.object(ops, "cmd_advance") as cmd_advance, \
         mock.patch.object(ops, "cmd_comment") as cmd_comment, \
         mock.patch.object(reconcile, "_write_failures", []) as failures:
        reconcile._release_card(_pr(), CALLER_NOTE)
    return gql, remove_label, cmd_advance, cmd_comment, failures


LIVE_REVIEW_CAP = [
    "⏳ 3/5 green",
    _stamp("review-cap-spent", HEAD),
    "Three review re-triggers spent on this head — which way should it go?",
]


class LiveReviewCapIsLeftAloneTest(unittest.TestCase):
    """The sweep's release stands down on the park its own sweep wrote."""

    def test_neither_label_nor_lane_is_written(self):
        _, remove_label, cmd_advance, _, failures = release(
            [hold.HOLD_LABEL, "repo:bureau-pipeline"], LIVE_REVIEW_CAP
        )
        remove_label.assert_not_called()
        cmd_advance.assert_not_called()
        self.assertEqual(failures, [])

    def test_one_note_worded_for_the_park(self):
        _, _, _, cmd_comment, _ = release([hold.HOLD_LABEL], LIVE_REVIEW_CAP)
        cmd_comment.assert_called_once()
        card, note = cmd_comment.call_args[0][:2]
        self.assertEqual(card, CARD)
        self.assertTrue(note.startswith("🔓"), note)
        self.assertIn(f"PR #{PR_NUMBER}", note)
        self.assertIn("fix agent is running on your decision", note)
        self.assertIn("Green Light", note)
        self.assertIn("back to In Review on its own", note)
        self.assertIn("new head", note)
        self.assertIn("Nothing more needed from you", note)
        # Not the caller's note — that one says the card is out of the queue,
        # which is exactly what is no longer true here.
        self.assertNotEqual(note, CALLER_NOTE)
        self.assertNotIn("out of your queue", note)

    def test_the_note_writes_no_hold_vocabulary(self):
        # The note is a person's answer, never a stamp or a lift line — a
        # 🔓 hold lifted: first line would retire the stamp it leaves standing.
        _, _, _, cmd_comment, _ = release([hold.HOLD_LABEL], LIVE_REVIEW_CAP)
        note = cmd_comment.call_args[0][1]
        self.assertFalse(note.startswith(hold.LIFT_PREFIX))
        self.assertFalse(note.startswith(hold.STAMP_PREFIX))
        self.assertNotIn(hold.HYG_CLEARED_TAG, note)
        for word in hold.FORBIDDEN:
            self.assertNotIn(word, note)

    def test_the_card_is_read_once(self):
        gql, _, _, _, _ = release([hold.HOLD_LABEL], LIVE_REVIEW_CAP)
        self.assertEqual(gql.call_count, 1)
        query = gql.call_args[0][0]
        self.assertIn("labels", query)
        self.assertIn("comments", query)


class EveryOtherReasonReleasesAsOnMainTest(unittest.TestCase):
    """The existing path, unchanged, for every answer but a live review cap."""

    def assert_released(self, labels, bodies):
        gql, remove_label, cmd_advance, cmd_comment, failures = release(labels, bodies)
        remove_label.assert_called_once_with(CARD, reconcile.HOLD_LABEL)
        cmd_advance.assert_called_once_with(
            CARD, reconcile.REVIEW_LANE, reconcile.PARKED_STATE
        )
        self.assertEqual(reconcile.REVIEW_LANE, "In Review")
        self.assertEqual(reconcile.PARKED_STATE, "Triage")
        cmd_comment.assert_called_once_with(CARD, CALLER_NOTE)
        self.assertEqual(gql.call_count, 1)
        self.assertEqual(failures, [])

    def test_bare_label_reads_manual_and_releases(self):
        self.assertEqual(hold.reason_of([hold.HOLD_LABEL], ["hello"]), "manual")
        self.assert_released([hold.HOLD_LABEL], ["hello"])

    def test_no_label_releases(self):
        self.assert_released(["repo:bureau-pipeline"], LIVE_REVIEW_CAP)

    def test_fix_dispute_stamp_releases(self):
        self.assert_released(
            [hold.HOLD_LABEL], ["⏳ 3/5 green", _stamp("fix-dispute", HEAD)]
        )

    def test_review_cap_spent_by_a_lift_line_releases(self):
        lifted = hold.lift_line("review-cap-spent", "new-head", "hygiene.py")
        self.assert_released([hold.HOLD_LABEL], LIVE_REVIEW_CAP + [lifted])

    def test_review_cap_spent_by_a_hygiene_receipt_releases(self):
        receipt = f"✅ cleared the hold `{hold.HYG_CLEARED_TAG}` reason=review-cap-spent because=new-head"
        self.assert_released([hold.HOLD_LABEL], LIVE_REVIEW_CAP + [receipt])

    def test_a_stamp_newer_than_its_lift_is_live_again(self):
        # Non-vacuous twin of the two above: the retiring line only spends
        # stamps OLDER than it.
        lifted = hold.lift_line("review-cap-spent", "new-head", "hygiene.py")
        bodies = LIVE_REVIEW_CAP + [lifted, _stamp("review-cap-spent", "f" * 40)]
        _, remove_label, cmd_advance, _, _ = release([hold.HOLD_LABEL], bodies)
        remove_label.assert_not_called()
        cmd_advance.assert_not_called()


class UnreadableCardTest(unittest.TestCase):
    def test_a_linear_outage_is_recorded_not_raised(self):
        ops = reconcile.linear_ops
        with mock.patch.object(ops, "gql", side_effect=RuntimeError("503")), \
             mock.patch.object(ops, "remove_label") as remove_label, \
             mock.patch.object(ops, "cmd_advance") as cmd_advance, \
             mock.patch.object(ops, "cmd_comment") as cmd_comment, \
             mock.patch.object(reconcile, "_write_failures", []) as failures:
            reconcile._release_card(_pr(), CALLER_NOTE)
        remove_label.assert_not_called()
        cmd_advance.assert_not_called()
        cmd_comment.assert_not_called()
        self.assertEqual(len(failures), 1)
        self.assertIn(CARD, failures[0])


# ── the fix run's Announce step, executed for real ─────────────────────────


def steps() -> list:
    return yaml.safe_load(step_shell.workflow_source(WORKFLOW))["jobs"]["fix"]["steps"]


def step_named(name: str) -> dict:
    for step in steps():
        if step.get("name") == name:
            return step
    raise AssertionError(f"step {name!r} not found in agent-fix.yml")


def substitute(run: str, values: dict) -> str:
    def repl(m):
        key = m.group(1).strip()
        if key not in values:
            raise AssertionError(f"harness has no value for ${{{{ {key} }}}}")
        return values[key]

    out = re.sub(r"\$\{\{([^}]*)\}\}", repl, run)
    assert "${{" not in out
    return out


def write_exec(path: str, body: str) -> None:
    with open(path, "w") as f:
        f.write(body)
    os.chmod(path, 0o755)


def run_announce(td: str, reason: str = "", hold_exit: int = 0, card: str = CARD):
    """Execute the real 'Announce fix attempt' run block with `hold.py` and
    `linear_ops.py` stubbed. `reason` is what the stubbed `hold.py reason`
    prints. Returns (proc, linear_calls, hold_calls)."""
    scripts = os.path.join(td, ".bureau-pipeline", "scripts")
    os.makedirs(scripts, exist_ok=True)
    linear_log = os.path.join(td, "linear-calls.jsonl")
    hold_log = os.path.join(td, "hold-calls.jsonl")
    write_exec(
        os.path.join(scripts, "linear_ops.py"),
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        f"open({linear_log!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n",
    )
    write_exec(
        os.path.join(scripts, "hold.py"),
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        f"open({hold_log!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n"
        f"if {hold_exit}:\n"
        "    sys.stderr.write('Traceback: boom\\n')\n"
        f"    sys.exit({hold_exit})\n"
        f"if {reason!r}:\n"
        f"    print({reason!r})\n",
    )
    run = substitute(
        step_shell.step_shell(step_named("Announce fix attempt")),
        {
            "steps.pr.outputs.mode": "fix",
            "steps.pr.outputs.attempt": "2",
            "steps.pr.outputs.number": str(PR_NUMBER),
            "steps.pr.outputs.card": card,
            "steps.pr.outputs.rearmed": "true",
            "steps.pr.outputs.fresh_eyes": "false",
            "steps.pr.outputs.nonconverging": "0",
            "steps.pr.outputs.stop": "2",
        },
    )
    script = os.path.join(td, "announce.sh")
    with open(script, "w") as f:
        f.write("set -eo pipefail\n" + run)
    proc = subprocess.run(
        ["bash", script], cwd=td,
        env=dict(os.environ, LINEAR_API_KEY="test-key", GH_TOKEN="test"),
        capture_output=True, text=True,
    )

    def read(path):
        if not os.path.exists(path):
            return []
        return [json.loads(line) for line in open(path).read().splitlines()]

    return proc, read(linear_log), read(hold_log)


def clears(calls: list) -> list:
    return [c for c in calls if c[:1] == ["remove-label"]]


class AnnounceReadsTheReasonFirstTest(unittest.TestCase):

    def test_review_cap_spent_keeps_the_label(self):
        with tempfile.TemporaryDirectory() as td:
            proc, calls, hold_calls = run_announce(td, "review-cap-spent")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(hold_calls, [["reason", CARD]])
        self.assertEqual(clears(calls), [])
        # The announcement itself still posts.
        self.assertEqual(len([c for c in calls if c[:1] == ["comment"]]), 1)

    def test_every_other_reason_clears_as_on_main(self):
        for reason in ("fix-dispute", "manual", "unfixable-check", ""):
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as td:
                proc, calls, hold_calls = run_announce(td, reason)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(hold_calls, [["reason", CARD]])
                self.assertEqual(clears(calls), [["remove-label", CARD, "needs-human"]])

    def test_a_failing_hold_read_does_not_fail_the_run(self):
        with tempfile.TemporaryDirectory() as td:
            proc, calls, _ = run_announce(td, hold_exit=1)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(clears(calls), [["remove-label", CARD, "needs-human"]])
        self.assertEqual(len([c for c in calls if c[:1] == ["comment"]]), 1)

    def test_a_cardless_branch_reads_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            proc, calls, hold_calls = run_announce(td, "review-cap-spent", card="")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(hold_calls, [])
        self.assertEqual(calls, [])


# The step as it stands on `main` at the branch point. `advance … "In Review"
# "Triage"` moves nothing for a card in Green Light, which is now the intended
# answer for a review-cap park — so this step does not change.
RELEASE_STEP = {
    "name": "Release the card the operator answered",
    "if": (
        "steps.decision.outputs.start == 'decision' && "
        "steps.pr.outputs.go == 'true' && "
        "steps.unfixable.outputs.escalate != 'true' && "
        "steps.pr.outputs.card != ''"
    ),
    "env": {
        "LINEAR_API_KEY": (
            "${{ vars.LINEAR_AGENT_BUCKET == 'planner' && "
            "secrets.LINEAR_PLANNER_KEY || secrets.LINEAR_API_KEY }}"
        ),
        "LINEAR_API_KEY_FALLBACK": "${{ secrets.LINEAR_API_KEY }}",
    },
    "run": (
        "# The sweep's own move, argument for argument\n"
        "# (reconcile._release_card: REVIEW_LANE out of PARKED_STATE) — a\n"
        "# card in any other lane is left exactly where it is.\n"
        "python3 .bureau-pipeline/scripts/linear_ops.py advance \\\n"
        "  \"${{ steps.pr.outputs.card }}\" \"In Review\" \"Triage\" || true\n"
    ),
}


class ReleaseStepUnchangedTest(unittest.TestCase):
    def test_release_step_is_byte_identical_to_main(self):
        self.assertEqual(step_named("Release the card the operator answered"), RELEASE_STEP)


if __name__ == "__main__":
    unittest.main()
