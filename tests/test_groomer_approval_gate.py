"""Nothing leaves Intake without the CEO's approval of the batch (DRE-2683).

The cutover moves the whole Backlog into Intake, and nothing may leave it
automatically. The groomer proposes; the CEO approves; only then is anything
classified. That is D5 ("on demand, until the groomer's judgement has been
audited") made load-bearing rather than cautious — it is the only arrangement
under which the first batch cannot surprise anyone.

The stated cost, in the open: on demand means it runs when someone remembers,
and this programme's whole thesis is that anything relying on remembering
eventually does not happen. Revisit the cadence once the calls have been
checked against a real batch.

The gate is worth nothing if the proposer can approve its own proposal, so the
approval must come from someone who is not the pipeline: every pipeline write
goes through one `LINEAR_API_KEY` that resolves to one Linear user, so "the
pipeline wrote this" is exactly "the key's own viewer wrote this"
(`linear_ops.comment_records`, DRE-2721 — two stray comments carrying a marker
line once overrode a real critic rejection).

Run: cd bureau-pipeline && python3 -m pytest tests/test_groomer_approval_gate.py -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import groomer  # noqa: E402

from test_groomer_population import CYCLES, card  # noqa: E402

PROPOSAL_CARD = "DRE-2683"


class FakeOps:
    """Records every write the drain attempts. A refused drain must leave this
    empty — a gate that refuses AFTER moving three cards is not a gate."""

    def __init__(self, comments=None):
        self.comments = comments or []
        self.state_writes: list[tuple[str, str]] = []
        self.mutations: list[tuple[str, dict]] = []

    def comment_records(self, identifier):
        return list(self.comments)

    def get_issue(self, identifier):
        return {"id": f"uuid-{identifier}", "identifier": identifier,
                "team": {"id": "team-1"}, "state": {"name": "Intake"}}

    def gql(self, query, variables=None):
        self.mutations.append((query, variables or {}))
        return {"issueUpdate": {"success": True}}

    def cmd_state(self, identifier, state_name, *flags):
        self.state_writes.append((identifier, state_name))


def _proposal():
    cards = [card(f"DRE-{n:03d}") for n in range(6)]
    return groomer.propose(cards, cycles=CYCLES, capacity=3, batch_cycles=1)


def _approval(proposal, *, by_pipeline=False):
    return {"body": groomer.approval_comment(groomer.proposal_id(proposal)),
            "authored_by_pipeline": by_pipeline}


# --------------------------------------------------------------------------
# the gate
# --------------------------------------------------------------------------
def test_a_drain_with_no_approval_writes_nothing():
    proposal = _proposal()
    ops = FakeOps(comments=[{"body": "looks good to me", "authored_by_pipeline": False}])
    with pytest.raises(groomer.NotApproved) as exc:
        groomer.drain(ops, proposal, card=PROPOSAL_CARD)
    assert ops.state_writes == [], "cards left Intake without an approval"
    assert ops.mutations == [], "cycles were assigned without an approval"
    assert groomer.APPROVAL_TAG in str(exc.value), (
        "the refusal must say what would unblock it"
    )


def test_the_pipeline_cannot_approve_its_own_proposal():
    proposal = _proposal()
    ops = FakeOps(comments=[_approval(proposal, by_pipeline=True)])
    with pytest.raises(groomer.NotApproved) as exc:
        groomer.drain(ops, proposal, card=PROPOSAL_CARD)
    assert ops.state_writes == []
    assert "pipeline" in str(exc.value).lower()


def test_an_approval_of_a_different_batch_does_not_approve_this_one():
    """The proposal id is derived from the batch itself, so re-running the
    groomer after the population moved produces a different id and the old
    approval stops applying. An approval is for a batch, not for the groomer."""
    proposal = _proposal()
    stale = {"body": groomer.approval_comment("0" * 12), "authored_by_pipeline": False}
    ops = FakeOps(comments=[stale])
    with pytest.raises(groomer.NotApproved):
        groomer.drain(ops, proposal, card=PROPOSAL_CARD)
    assert ops.state_writes == []


def test_a_mention_of_approval_in_prose_is_not_an_approval():
    proposal = _proposal()
    pid = groomer.proposal_id(proposal)
    ops = FakeOps(comments=[
        {"body": f"I think we should send groom-approved: {pid} once Ana has read it",
         "authored_by_pipeline": False},
    ])
    with pytest.raises(groomer.NotApproved):
        groomer.drain(ops, proposal, card=PROPOSAL_CARD)
    assert ops.state_writes == []


# --------------------------------------------------------------------------
# the approved path
# --------------------------------------------------------------------------
def test_an_approved_batch_drains_in_the_proposed_order():
    proposal = _proposal()
    ops = FakeOps(comments=[_approval(proposal)])
    result = groomer.drain(ops, proposal, card=PROPOSAL_CARD)
    batch = [r["identifier"] for r in sorted(proposal["outcomes"]["now"],
                                             key=lambda r: r["position"])]
    assert [i for i, _ in ops.state_writes] == batch
    assert result["moved"] == batch


def test_only_the_batch_moves():
    proposal = _proposal()
    ops = FakeOps(comments=[_approval(proposal)])
    groomer.drain(ops, proposal, card=PROPOSAL_CARD)
    moved = {i for i, _ in ops.state_writes}
    for row in proposal["outcomes"]["not-now"]:
        assert row["identifier"] not in moved, "a deferred card left Intake"


def test_the_batch_lands_in_the_lane_that_classifies_it():
    proposal = _proposal()
    ops = FakeOps(comments=[_approval(proposal)])
    groomer.drain(ops, proposal, card=PROPOSAL_CARD)
    assert {s for _, s in ops.state_writes} == {"Planning"}


def test_the_cycle_is_written_with_linears_own_primitive():
    proposal = _proposal()
    ops = FakeOps(comments=[_approval(proposal)])
    groomer.drain(ops, proposal, card=PROPOSAL_CARD)
    assert ops.mutations, "no cycle was assigned"
    for query, variables in ops.mutations:
        assert "issueUpdate" in query
        assert variables["input"]["cycleId"] == "cyc-12"


# --------------------------------------------------------------------------
# the groomer recommends and never cancels
# --------------------------------------------------------------------------
def test_the_drain_refuses_a_terminal_destination():
    """Cancelling is destructive and belongs to the operator. The drain has one
    destination and refuses any terminal one — asserted by pointing it at
    Canceled, which is the mistake this rule exists to make impossible."""
    proposal = _proposal()
    ops = FakeOps(comments=[_approval(proposal)])
    with pytest.raises(groomer.WillNotCancel):
        groomer.drain(ops, proposal, card=PROPOSAL_CARD, to="Canceled")
    assert ops.state_writes == []


def test_a_dead_recommendation_is_never_executed_by_the_drain():
    cards = [card("DRE-1", description="**Superseded by:** DRE-2719")]
    cards += [card(f"DRE-{n}") for n in range(2, 5)]
    proposal = groomer.propose(cards, cycles=CYCLES)
    ops = FakeOps(comments=[_approval(proposal)])
    groomer.drain(ops, proposal, card=PROPOSAL_CARD)
    assert "DRE-1" not in {i for i, _ in ops.state_writes}
    assert {s for _, s in ops.state_writes} <= {"Planning"}


def test_a_projected_cycle_is_never_written():
    """A cycle beyond the ones Linear carries has no id. The drain refuses
    rather than inventing one."""
    proposal = _proposal()
    for row in proposal["outcomes"]["now"]:
        row["cycle_id"] = None
    ops = FakeOps(comments=[_approval(proposal)])
    with pytest.raises(ValueError):
        groomer.drain(ops, proposal, card=PROPOSAL_CARD)
    assert ops.state_writes == []
