"""RED-first tests: a pull request opened outside the dispatch writes the lane (DRE-4179).

THE BUG (counted 2026-09-17). A card whose pull request was opened OUTSIDE the
relay's `agent-execute` dispatch never leaves the lane it was filed in:

  * `agent-task.yml` is the ONLY writer of `In Progress`, and it runs only on
    that dispatch;
  * the relay refuses to dispatch anything that is not in Todo
    (`lambda_function.py` — `state=<lane>, ignored`), so no dispatch means no
    `agent-task` run and no lane write, ever;
  * every later move is the guarded `cmd_advance`, and `Intake` is not an
    allowed from-lane anywhere in the pipeline;
  * `linear-sync` closes the card on the merge event off the head ref alone.

So the board read "Intake" while an agent was mid-build, and on merge the card
jumped **Intake → Done** having shown neither In Progress nor In Review.
DRE-4132 (bureau-pipeline#433, critic APPROVE, still Intake), DRE-4161
(portico#587) and DRE-4164 (agent-bureau#2595) were the observed three; 289
cards sat in Intake that day, 25 of them carrying build evidence.

FIX UNDER TEST. The lane is written from the side the evidence is on — the
pull request. `qa-review.yml` grows one step (id `lane`), ordered AFTER the
verdict is posted, that on PR `opened`/`reopened` advances the card into the
review lane from any pre-review lane. Two properties it must have and that a
grep of the YAML cannot prove:

  * the branch match is ANCHORED (`^agent/DRE-<n>-`). qa-review's existing
    card grep is unanchored — right for LOGGING a card, wrong for moving one —
    so a branch that merely mentions `agent/DRE-1234-` later in its name must
    move nothing;
  * a Linear outage must not turn a completed review red: the verdict is
    already posted and the lane is the least load-bearing thing the job writes.

And two `cmd_advance` properties, asserted against the real function rather
than against the csv the workflow hands it: a card at or past the review lane
is never moved, and `--not-epic` refuses an epic outright.

This is the complement of `reconcile.move_hand_built_to_review()` (DRE-4356),
which makes the same move for `hand-built` cards only and leaves a card without
that label to "the run that opened its pull request". This is that run.

Run: cd bureau-pipeline && python3 -m pytest tests/test_pr_side_in_review.py -v
"""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parent.parent
QA_REVIEW = ROOT / ".github" / "workflows" / "qa-review.yml"

sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")

import lane_contract  # noqa: E402
import linear_ops  # noqa: E402

LANE_STEP_ID = "lane"
REVIEW_LANE = lane_contract.lane("In Review")["name"]


# --------------------------------------------------------------------------
# the workflow step, executed
# --------------------------------------------------------------------------
def _steps() -> list:
    doc = yaml.safe_load(QA_REVIEW.read_text())
    return doc["jobs"]["review"]["steps"]


def _lane_step() -> dict:
    for step in _steps():
        if step.get("id") == LANE_STEP_ID:
            return step
    raise AssertionError(
        f"qa-review.yml has no step with id {LANE_STEP_ID!r} — the PR-side "
        "lane write (DRE-4179) is not there"
    )


def _from_lanes() -> list[str]:
    """The pre-review lanes the step hands `advance`, read off the step itself.

    Read rather than restated: a test carrying its own copy of the csv would
    pass while the workflow wrote lanes the board does not have.
    """
    run = _lane_step()["run"]
    for token in re.findall(r'"([^"\n]*)"', run):
        parts = [part.strip() for part in token.split(",")]
        if len(parts) > 1 and all(p in lane_contract.lane_names() for p in parts):
            return parts
    raise AssertionError("the lane step names no from-lane csv")


class _Harness:
    """A temp tree with a stub `linear_ops.py` that records its argv."""

    def __init__(self, td: Path, *, exit_code: int = 0):
        self.td = td
        self.calls_dir = td / "calls"
        self.calls_dir.mkdir()
        scripts = td / "pipeline" / "scripts"
        scripts.mkdir(parents=True)
        (scripts / "linear_ops.py").write_text(
            "import sys, json, pathlib\n"
            f"d = pathlib.Path({str(self.calls_dir)!r})\n"
            "n = len(list(d.iterdir()))\n"
            "(d / f'{n:03d}.json').write_text(json.dumps(sys.argv[1:]))\n"
            f"sys.exit({exit_code})\n"
        )

    def calls(self) -> list[list[str]]:
        return [
            json.loads(f.read_text()) for f in sorted(self.calls_dir.iterdir())
        ]


def _run_lane(head_ref: str, *, exit_code: int = 0):
    """Execute the step's run block the way Actions would."""
    td = Path(tempfile.mkdtemp())
    harness = _Harness(td, exit_code=exit_code)
    run = _lane_step()["run"]
    assert "${{" not in run, (
        "the lane step interpolates a GitHub expression into its shell — "
        "env, never `${{ }}` (DRE-3484)"
    )
    script = td / "lane.sh"
    script.write_text("set -eo pipefail\n" + run)
    env = dict(os.environ)
    env.update({
        "PIPELINE_DIR": str(td / "pipeline"),
        "HEAD_REF": head_ref,
        "LINEAR_API_KEY": "test-key",
    })
    proc = subprocess.run(
        ["bash", str(script)], cwd=td, env=env, capture_output=True, text=True
    )
    return proc, harness


CARD = "DRE-1234"
AGENT_BRANCH = f"agent/{CARD}-x"


class TheHeadlineTest(unittest.TestCase):
    """A PR on `agent/DRE-1234-x` whose card is in Intake ends at In Review."""

    def test_an_agent_branch_advances_its_card_into_the_review_lane(self):
        proc, harness = _run_lane(AGENT_BRANCH)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        calls = harness.calls()
        self.assertEqual(len(calls), 1, f"expected one Linear call: {calls}")
        argv = calls[0]
        self.assertEqual(argv[0], "advance")
        self.assertEqual(argv[1], CARD)
        self.assertEqual(argv[2], REVIEW_LANE)
        self.assertIn("Intake", [lane.strip() for lane in argv[3].split(",")])

    def test_the_card_id_is_normalised_to_upper_case(self):
        proc, harness = _run_lane("agent/dre-1234-lower")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(harness.calls()[0][1], CARD)


class TheBranchMatchIsAnchoredTest(unittest.TestCase):
    """`^agent/DRE-<n>-`, and nothing else, moves a card."""

    def test_a_branch_that_mentions_an_agent_ref_later_moves_nothing(self):
        # The exact shape the card names: qa-review's own card grep is
        # unanchored, so an unanchored reuse would move DRE-1234 here.
        proc, harness = _run_lane("bot/rename-agent/DRE-1234-x")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(harness.calls(), [], "an unanchored match moved a card")

    def test_branches_that_name_no_card_move_nothing(self):
        for branch in (
            "dependabot/pip/urllib3-2.5.0",
            "bot/standards-sync",
            "main",
            "ops/rotate-the-key",
            "agent/DRE-1234",          # no delimiter after the number
            "agents/DRE-1234-x",       # not the `agent/` prefix
            " agent/DRE-1234-x",       # not at the start of the ref
        ):
            with self.subTest(branch=branch):
                proc, harness = _run_lane(branch)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(harness.calls(), [], f"{branch!r} moved a card")

    def test_a_repair_branch_is_left_alone(self):
        """A Red-Main Repair files its own card and is not this defect."""
        proc, harness = _run_lane("repair/DRE-1234-a1b2c3d4e5f6")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(harness.calls(), [])


class TheVerdictIsNeverAtRiskTest(unittest.TestCase):
    def test_a_failed_linear_write_does_not_fail_the_review_job(self):
        proc, harness = _run_lane(AGENT_BRANCH, exit_code=1)
        self.assertEqual(
            proc.returncode, 0,
            f"a Linear outage turned a completed review red:\n{proc.stderr}",
        )
        self.assertEqual(len(harness.calls()), 1)
        self.assertIn("warning", (proc.stdout + proc.stderr).lower())

    def test_the_lane_step_runs_after_the_verdict_is_posted(self):
        ids = [step.get("id") for step in _steps()]
        self.assertIn("post", ids)
        self.assertIn(LANE_STEP_ID, ids)
        self.assertGreater(
            ids.index(LANE_STEP_ID), ids.index("post"),
            "the lane write must not be able to precede the verdict",
        )

    def test_the_step_fires_only_when_a_pull_request_opens_or_reopens(self):
        """The critic fires on every push; a per-push Linear write would add
        hundreds of writes per repo for a move that happens once."""
        condition = _lane_step()["if"]
        self.assertIn("github.event.action", condition)
        self.assertIn("'opened'", condition)
        self.assertIn("'reopened'", condition)


class TheFromLanesAreThePreReviewLanesTest(unittest.TestCase):
    def test_every_from_lane_is_a_lane_the_contract_carries(self):
        for lane in _from_lanes():
            with self.subTest(lane=lane):
                self.assertIn(lane, lane_contract.lane_names())

    def test_intake_is_a_from_lane(self):
        self.assertIn("Intake", _from_lanes())

    def test_the_review_lane_and_every_terminal_lane_are_absent(self):
        lanes = _from_lanes()
        for lane in (REVIEW_LANE, "Done", "Canceled", "Duplicate"):
            with self.subTest(lane=lane):
                self.assertNotIn(lane, lanes)

    def test_the_ceos_own_queue_is_absent(self):
        """`Green Light` holds a question waiting on the CEO. Taking a card out
        of his queue because a branch exists is not this step's business — the
        same line `reconcile.HAND_BUILT_REVIEW_LANES` draws (DRE-4356)."""
        self.assertNotIn("Green Light", _from_lanes())

    def test_every_pre_review_flow_lane_but_that_one_is_present(self):
        flow = lane_contract.flow_lanes()
        upstream = [
            name for name in flow[: flow.index(REVIEW_LANE)]
            if name != "Green Light"
        ]
        self.assertEqual(
            [lane for lane in _from_lanes() if lane in flow], upstream,
            "the from-lanes drifted from the contract's own pre-review flow",
        )


# --------------------------------------------------------------------------
# cmd_advance: the guard the step leans on
# --------------------------------------------------------------------------
class _Linear:
    """A Linear double for one card. Records every `issueUpdate`."""

    STATES = {
        "Intake": ("st-intake", "backlog"),
        "Triage": ("st-triage", "backlog"),
        "Planning": ("st-planning", "backlog"),
        "Green Light": ("st-greenlight", "backlog"),
        "Backlog": ("st-backlog", "backlog"),
        "Todo": ("st-todo", "unstarted"),
        "In Progress": ("st-inprogress", "started"),
        "In Review": ("st-inreview", "started"),
        "Done": ("st-done", "completed"),
        "Canceled": ("st-canceled", "canceled"),
        "Duplicate": ("st-duplicate", "canceled"),
    }

    def __init__(self, state, *, title="a card", children=(), labels=()):
        self.current = state
        self.title = title
        self.children = list(children)
        self.labels = list(labels)
        self.updates = []

    def _node(self, name):
        sid, stype = self.STATES[name]
        return {"id": sid, "name": name, "type": stype}

    def _name_for(self, state_id):
        for name, (sid, _t) in self.STATES.items():
            if sid == state_id:
                return name
        raise AssertionError(f"unknown stateId {state_id!r}")

    def gql(self, query, variables=None):
        v = variables or {}
        q = " ".join(query.split())
        if "issue(id: $id) { id identifier title team" in q:
            return {"issue": {
                "id": "card-uuid",
                "identifier": CARD,
                "title": self.title,
                "team": {"id": "team-1"},
                "state": {
                    "name": self.current,
                    "type": self.STATES[self.current][1],
                },
                "labels": {"nodes": [{"name": n} for n in self.labels]},
                "children": {"nodes": [{"id": c} for c in self.children]},
            }}
        if "history" in q:
            return {"issue": {"history": {"nodes": []}}}
        if "workflowStates" in q:
            return {"workflowStates": {"nodes": [
                {"id": sid, "name": name, "type": stype}
                for name, (sid, stype) in self.STATES.items()
            ]}}
        if "issueUpdate" in q:
            sid = v["input"].get("stateId")
            self.updates.append(self._name_for(sid))
            self.current = self._name_for(sid)
            return {"issueUpdate": {"success": True}}
        raise AssertionError(f"unexpected gql query: {q}")


def _advance(fake, *args):
    buf = io.StringIO()
    linear_ops._card_memo.clear()
    with patch.object(linear_ops, "gql", side_effect=fake.gql):
        with redirect_stdout(buf):
            linear_ops.cmd_advance(CARD, REVIEW_LANE, _from_lanes_csv(), *args)
    return buf.getvalue()


def _from_lanes_csv() -> str:
    return ",".join(_from_lanes())


class AdvanceFromAPreReviewLaneTest(unittest.TestCase):
    def test_an_intake_card_is_advanced(self):
        fake = _Linear("Intake")
        _advance(fake, "--not-epic")
        self.assertEqual(fake.updates, [REVIEW_LANE])

    def test_every_pre_review_lane_advances(self):
        for lane in _from_lanes():
            with self.subTest(lane=lane):
                fake = _Linear(lane)
                _advance(fake, "--not-epic")
                self.assertEqual(fake.updates, [REVIEW_LANE])

    def test_a_card_at_or_past_the_review_lane_is_not_moved(self):
        for lane in (REVIEW_LANE, "Done", "Canceled", "Duplicate"):
            with self.subTest(lane=lane):
                fake = _Linear(lane)
                out = _advance(fake, "--not-epic")
                self.assertEqual(fake.updates, [], f"{lane} was moved")
                self.assertIn("not advancing", out.lower())

    def test_a_card_in_the_ceos_queue_is_not_moved(self):
        fake = _Linear("Green Light")
        _advance(fake, "--not-epic")
        self.assertEqual(fake.updates, [])


class AnEpicIsNeverMovedTest(unittest.TestCase):
    def test_an_epic_with_children_is_refused(self):
        fake = _Linear("Intake", children=("child-1",))
        out = _advance(fake, "--not-epic")
        self.assertEqual(fake.updates, [], "an epic was dragged into review")
        self.assertIn("epic", out.lower())

    def test_an_epic_by_title_is_refused(self):
        fake = _Linear("Intake", title="[EPIC] the agent working logs")
        out = _advance(fake, "--not-epic")
        self.assertEqual(fake.updates, [])
        self.assertIn("epic", out.lower())

    def test_the_flag_is_what_refuses_it(self):
        """Mutation check: without `--not-epic` the same card advances, so the
        guard is the flag and not some other property of the fixture."""
        fake = _Linear("Intake", children=("child-1",))
        _advance(fake)
        self.assertEqual(fake.updates, [REVIEW_LANE])

    def test_the_step_passes_the_flag(self):
        _, harness = _run_lane(AGENT_BRANCH)
        self.assertIn("--not-epic", harness.calls()[0])

    def test_an_unknown_flag_is_refused_rather_than_ignored(self):
        fake = _Linear("Intake")
        with self.assertRaises(linear_ops.LinearError):
            _advance(fake, "--not-an-option")
        self.assertEqual(fake.updates, [])


# --------------------------------------------------------------------------
# the lane contract documents the route
# --------------------------------------------------------------------------
class TheContractDocumentsTheRouteTest(unittest.TestCase):
    def test_the_review_lanes_entrance_names_the_pre_review_entry(self):
        entrance = lane_contract.lane(REVIEW_LANE)["clauses"]["entrance"]["text"]
        self.assertIn("pre-review", entrance.lower())

    def test_qa_review_is_a_declared_writer_of_the_review_lane(self):
        self.assertIn(
            "qa-review.yml", lane_contract.lane_writers(REVIEW_LANE)
        )

    def test_the_contract_check_passes(self):
        report = lane_contract.check()
        self.assertEqual(
            report.failures(), [],
            f"the lane contract does not check: {report.text()}",
        )


if __name__ == "__main__":
    unittest.main()
