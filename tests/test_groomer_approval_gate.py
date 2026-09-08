"""Nothing leaves Intake without the CEO's approval of the batch (DRE-2683),
and what leaves is the batch that was APPROVED, read from the record (DRE-3338).

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
approval must come from someone who is not the pipeline — and that is still the
property this gate leans on. Every unattended write goes through the FLEET key,
which resolves to the one fleet user `Agent-Bureau` (declared in
`config/linear-identities.json`, held to by
`scripts/check_linear_identities.py check`, DRE-3172), so "the pipeline wrote
this" is exactly "the fleet user wrote this" and `authored_by_pipeline` is true
only for that user. The operator's own tools write as `bureau-tools` and so read
as somebody else's — the safe direction (`linear_ops.comment_records`, DRE-2721
— two stray comments carrying a marker line once overrode a real critic
rejection).

WHAT DRE-3338 CHANGED. The drain used to rebuild the proposal from live state
and refuse when the id it re-derived differed from the approved one. That is a
real safety property said the wrong way round: it made the drain fail on two
ordinary events — a card entering Intake between propose and drain, and the
model answering the same census slightly differently — and each failure cost
another ~$6 model call, another eight minutes, and another CEO approval, for a
reason that had nothing to do with the batch. The drain now READS the approved
batch off the proposal comment on the card and moves exactly those cards, in
that order. The "different read → different id → refuse" test below is
therefore replaced by "the approval names an id with no record → refuse".

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
    """Records every write the drain attempts. A refused drain must leave the
    state writes empty — a gate that refuses AFTER moving three cards is not a
    gate.

    `lanes` is where each card is RIGHT NOW, which is the fact the drain reads
    per card before it moves anything: a card someone moved by hand between the
    approval and the drain is not the card the CEO approved a move for.
    """

    def __init__(self, comments=None, *, lanes=None, cycles=CYCLES):
        self.comments = list(comments or [])
        self.lanes = dict(lanes or {})
        self.cycles = list(cycles)
        self.state_writes: list[tuple[str, str]] = []
        self.mutations: list[tuple[str, dict]] = []
        self.written: list[tuple[str, str]] = []

    def comment_records(self, identifier):
        return list(self.comments)

    def get_issue(self, identifier):
        return {"id": f"uuid-{identifier}", "identifier": identifier,
                "team": {"id": "team-1"},
                "state": {"name": self.lanes.get(identifier, "Intake")}}

    def gql(self, query, variables=None):
        if "cycles(" in query:
            return {"cycles": {"nodes": [dict(c, completedAt=None)
                                         for c in self.cycles]}}
        self.mutations.append((query, variables or {}))
        return {"issueUpdate": {"success": True}}

    def cmd_state(self, identifier, state_name, *flags):
        self.state_writes.append((identifier, state_name))

    def cmd_comment(self, identifier, body, *flags):
        self.written.append((identifier, body))


def _proposal():
    cards = [card(f"DRE-{n:03d}") for n in range(6)]
    return groomer.propose(cards, cycles=CYCLES, capacity=3, batch_cycles=1)


def _record(proposal):
    """The proposal AS THE CARD CARRIES IT.

    Not a fixture of what a proposal comment might look like — the very string
    `propose --post` writes, so the render and the read that follows it are
    tested as one contract. The pipeline wrote it, which is exactly why it
    cannot also approve it.
    """
    return {"body": groomer.proposal_comment(proposal),
            "authored_by_pipeline": True}


def _approval(proposal, *, by_pipeline=False):
    return {"body": groomer.approval_comment(groomer.proposal_id(proposal)),
            "authored_by_pipeline": by_pipeline}


def _thread(proposal, **kwargs):
    """The ordinary thread on a proposal card: the proposal, then its approval."""
    return [_record(proposal), _approval(proposal, **kwargs)]


def _batch_ids(proposal):
    return [r["identifier"] for r in sorted(proposal["outcomes"]["now"],
                                            key=lambda r: r["position"])]


# --------------------------------------------------------------------------
# the record — the drain reads the batch it moves, it does not re-derive it
# --------------------------------------------------------------------------
def test_the_proposal_comment_carries_the_batch_the_drain_reads():
    """The record round-trips: what `render_proposal` writes is what the drain
    reads back. Two halves of one contract, so a render change that drops the
    batch table fails here rather than in a live drain."""
    proposal = _proposal()
    got = groomer.parse_proposal_comment(groomer.proposal_comment(proposal))
    assert got is not None, "the proposal comment did not parse as a record"
    assert got["id"] == proposal["id"]
    assert got["lane"] == "Intake"
    assert got["cycles"] == proposal["batch"]["cycles"]
    assert [r["identifier"] for r in got["batch"]] == _batch_ids(proposal)
    assert [r["position"] for r in got["batch"]] == \
        list(range(1, len(got["batch"]) + 1))


def test_a_comment_that_is_not_a_proposal_is_not_a_record():
    assert groomer.parse_proposal_comment("looks good to me") is None
    assert groomer.parse_proposal_comment(
        f"I will post the {groomer.PROPOSAL_TAG}: abcdef123456 shortly") is None


def test_a_card_joining_intake_after_the_proposal_changes_nothing(monkeypatch):
    """The event this card exists for. DRE-3337 was filed five minutes after
    proposal `f673bfefa340` was read, and the drain refused a batch the CEO had
    approved because a re-derivation saw one more card. The drain moves the
    record; a card that joined afterwards is not in it and is not mentioned."""
    proposal = _proposal()
    monkeypatch.setattr(groomer, "read_population", _never("read the population"))
    ops = FakeOps(comments=_thread(proposal), lanes={"DRE-999": "Intake"})
    result = groomer.drain(ops, card=PROPOSAL_CARD)
    assert result["moved"] == _batch_ids(proposal)
    assert "DRE-999" not in {i for i, _ in ops.state_writes}
    assert "DRE-999" not in ops.written[0][1], (
        "the drain's record mentions a card that was never in the batch"
    )


def _never(what):
    def refuse(*args, **kwargs):                # pragma: no cover - the point
        raise AssertionError(f"the drain {what}")
    return refuse


def test_a_drain_makes_no_model_call(monkeypatch):
    """A drain is a move, not a judgement. Both seams are booby-trapped: the
    ranked read itself, and the population read that would feed it."""
    proposal = _proposal()
    ops = FakeOps(comments=_thread(proposal))
    monkeypatch.setattr(groomer.groom_judgement, "run", _never("called a model"))
    monkeypatch.setattr(groomer, "read_population", _never("read the population"))
    monkeypatch.setattr(groomer, "read_cycles", _never("re-read the cycles"))
    monkeypatch.setattr(groomer, "linear_ops", ops)
    assert groomer.main(["drain", "--card", PROPOSAL_CARD]) == 0
    assert [i for i, _ in ops.state_writes] == _batch_ids(proposal)


def test_the_drain_refuses_an_approval_whose_batch_has_no_record():
    """Replaces "a different read produced a different id, so refuse". An
    approval naming a batch this card carries no proposal for is an approval
    the drain cannot read a list out of, and it refuses rather than guessing."""
    proposal = _proposal()
    stale = {"body": groomer.approval_comment("0" * 12),
             "authored_by_pipeline": False}
    ops = FakeOps(comments=[_record(proposal), stale])
    with pytest.raises(groomer.NoProposalRecord) as exc:
        groomer.drain(ops, card=PROPOSAL_CARD)
    assert ops.state_writes == []
    assert ops.mutations == []
    assert "0" * 12 in str(exc.value), "the refusal must name the id it looked for"


def test_the_drain_refuses_when_the_card_carries_no_proposal_at_all():
    proposal = _proposal()
    ops = FakeOps(comments=[_approval(proposal)])
    with pytest.raises(groomer.NoProposalRecord):
        groomer.drain(ops, card=PROPOSAL_CARD)
    assert ops.state_writes == []


# --------------------------------------------------------------------------
# the gate
# --------------------------------------------------------------------------
def test_a_drain_with_no_approval_writes_nothing():
    proposal = _proposal()
    ops = FakeOps(comments=[_record(proposal),
                            {"body": "looks good to me",
                             "authored_by_pipeline": False}])
    with pytest.raises(groomer.NotApproved) as exc:
        groomer.drain(ops, card=PROPOSAL_CARD)
    assert ops.state_writes == [], "cards left Intake without an approval"
    assert ops.mutations == [], "cycles were assigned without an approval"
    assert groomer.APPROVAL_TAG in str(exc.value), (
        "the refusal must say what would unblock it"
    )


def test_the_pipeline_cannot_approve_its_own_proposal():
    proposal = _proposal()
    ops = FakeOps(comments=[_record(proposal),
                            _approval(proposal, by_pipeline=True)])
    with pytest.raises(groomer.NotApproved) as exc:
        groomer.drain(ops, card=PROPOSAL_CARD)
    assert ops.state_writes == []
    assert "pipeline" in str(exc.value).lower()


def test_a_mention_of_approval_in_prose_is_not_an_approval():
    proposal = _proposal()
    pid = groomer.proposal_id(proposal)
    ops = FakeOps(comments=[
        _record(proposal),
        {"body": f"I think we should send groom-approved: {pid} once Ana has read it",
         "authored_by_pipeline": False},
    ])
    with pytest.raises(groomer.NotApproved):
        groomer.drain(ops, card=PROPOSAL_CARD)
    assert ops.state_writes == []


# --------------------------------------------------------------------------
# the approved path
# --------------------------------------------------------------------------
def test_an_approved_batch_drains_in_the_proposed_order():
    proposal = _proposal()
    ops = FakeOps(comments=_thread(proposal))
    result = groomer.drain(ops, card=PROPOSAL_CARD)
    batch = _batch_ids(proposal)
    assert [i for i, _ in ops.state_writes] == batch
    assert result["moved"] == batch


def test_only_the_batch_moves():
    proposal = _proposal()
    ops = FakeOps(comments=_thread(proposal))
    groomer.drain(ops, card=PROPOSAL_CARD)
    moved = {i for i, _ in ops.state_writes}
    for row in proposal["outcomes"]["not-now"]:
        assert row["identifier"] not in moved, "a deferred card left Intake"


def test_the_batch_lands_in_the_lane_that_classifies_it():
    proposal = _proposal()
    ops = FakeOps(comments=_thread(proposal))
    groomer.drain(ops, card=PROPOSAL_CARD)
    assert {s for _, s in ops.state_writes} == {"Planning"}


def test_the_cycle_is_written_with_linears_own_primitive():
    proposal = _proposal()
    ops = FakeOps(comments=_thread(proposal))
    groomer.drain(ops, card=PROPOSAL_CARD)
    assert ops.mutations, "no cycle was assigned"
    for query, variables in ops.mutations:
        assert "issueUpdate" in query
        assert variables["input"]["cycleId"] == "cyc-12"


def test_the_cycle_is_resolved_even_after_it_has_started():
    """`read_cycles` excludes the running cycle on purpose — a PROPOSAL must
    not schedule work into a period half over. A drain is not scheduling: it
    executes a number the CEO already approved, and days pass between the
    proposal and the approval. Reading the proposal's own filter here would
    refuse every batch whose cycle opened in the meantime."""
    proposal = _proposal()
    started = [dict(c, startsAt="2020-01-01T00:00:00.000Z") for c in CYCLES]
    ops = FakeOps(comments=_thread(proposal), cycles=started)
    result = groomer.drain(ops, card=PROPOSAL_CARD)
    assert result["moved"] == _batch_ids(proposal)


# --------------------------------------------------------------------------
# a card that left the lane by hand
# --------------------------------------------------------------------------
def test_the_drain_refuses_a_card_that_is_no_longer_in_intake():
    """Somebody moved a card by hand between the approval and the drain. The
    approved list is no longer the list on the board, and the drain refuses the
    whole batch before any write rather than moving the part that still fits."""
    proposal = _proposal()
    gone = _batch_ids(proposal)[1]
    ops = FakeOps(comments=_thread(proposal), lanes={gone: "In Progress"})
    with pytest.raises(groomer.NotInLane) as exc:
        groomer.drain(ops, card=PROPOSAL_CARD)
    assert ops.state_writes == [], "the batch moved around a card that had left"
    assert ops.mutations == []
    assert gone in str(exc.value), "the refusal must name the card"
    assert "In Progress" in str(exc.value), "the refusal must name its lane now"


# --------------------------------------------------------------------------
# the record of what the drain did (DRE-3326, landed here)
# --------------------------------------------------------------------------
def test_the_drain_records_every_card_it_moved_with_its_position():
    proposal = _proposal()
    ops = FakeOps(comments=_thread(proposal))
    groomer.drain(ops, card=PROPOSAL_CARD)
    assert len(ops.written) == 1, "the drain wrote no record of what it moved"
    target, body = ops.written[0]
    assert target == PROPOSAL_CARD
    assert body.startswith(f"{groomer.MARK} {groomer.DRAIN_TAG}: {proposal['id']}")
    for position, identifier in enumerate(_batch_ids(proposal), start=1):
        line = next((ln for ln in body.splitlines()
                     if f"| {identifier} |" in ln), None)
        assert line, f"{identifier} is not in the drain's record"
        assert line.strip().startswith(f"| {position} |"), (
            f"{identifier} is recorded without its position"
        )
        assert proposal["id"] in line, (
            f"{identifier} is recorded without the proposal it came from"
        )


def test_the_drain_records_every_card_it_refused_and_why():
    proposal = _proposal()
    gone = _batch_ids(proposal)[0]
    ops = FakeOps(comments=_thread(proposal), lanes={gone: "Done"})
    with pytest.raises(groomer.NotInLane):
        groomer.drain(ops, card=PROPOSAL_CARD)
    assert len(ops.written) == 1, "a refused drain left no record"
    target, body = ops.written[0]
    assert target == PROPOSAL_CARD
    assert gone in body and "Done" in body, (
        "the record must name the refused card and the lane it is in now"
    )
    assert "| Planning |" not in body, "a refused drain recorded a move"


# --------------------------------------------------------------------------
# the groomer recommends and never cancels
# --------------------------------------------------------------------------
def test_the_drain_refuses_a_terminal_destination():
    """Cancelling is destructive and belongs to the operator. The drain has one
    destination and refuses any terminal one — asserted by pointing it at
    Canceled, which is the mistake this rule exists to make impossible."""
    proposal = _proposal()
    ops = FakeOps(comments=_thread(proposal))
    with pytest.raises(groomer.WillNotCancel):
        groomer.drain(ops, card=PROPOSAL_CARD, to="Canceled")
    assert ops.state_writes == []
    assert ops.written == []


def test_a_dead_recommendation_is_never_executed_by_the_drain():
    cards = [card("DRE-1", description="**Superseded by:** DRE-2719")]
    cards += [card(f"DRE-{n}") for n in range(2, 5)]
    proposal = groomer.propose(cards, cycles=CYCLES)
    ops = FakeOps(comments=_thread(proposal))
    groomer.drain(ops, card=PROPOSAL_CARD)
    assert "DRE-1" not in {i for i, _ in ops.state_writes}
    assert {s for _, s in ops.state_writes} <= {"Planning"}


def test_the_doc_describes_the_drain_as_moving_the_approved_record():
    """A change that contradicts a document updates that document. `drain`
    re-deriving the proposal from live state is what `docs/groomer.md` said,
    and it is exactly what stopped being true."""
    doc = (ROOT / "docs" / "groomer.md").read_text(encoding="utf-8")
    assert "re-derives the proposal from live state" not in doc, (
        "the doc still describes the drain the way it worked before DRE-3338"
    )
    assert "reads the approved batch off the proposal comment" in doc
    assert groomer.DRAIN_TAG in doc, (
        "the doc does not name the record the drain writes"
    )


def test_a_cycle_linear_does_not_carry_is_never_written():
    """A cycle beyond the ones Linear carries has no id. The drain refuses
    rather than inventing one."""
    proposal = _proposal()
    ops = FakeOps(comments=_thread(proposal), cycles=[])
    with pytest.raises(ValueError) as exc:
        groomer.drain(ops, card=PROPOSAL_CARD)
    assert ops.state_writes == []
    assert ops.mutations == []
    assert "12" in str(exc.value), "the refusal must name the cycle it wanted"
