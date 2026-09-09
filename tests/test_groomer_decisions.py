"""RED-first: the groomer's decision vocabulary, read by the drain (DRE-3370).

`🧺 groom-approved:` is one marker, and a batch is not a yes/no question. The
CEO who reads a twenty-card proposal wants to say *yes, except these two* and
*yes, and this one as well* — and until now the only way to say either was to
re-run the groomer, spend another ranked read, and ask for a second approval of
a batch that was already right apart from two rows.

So the vocabulary grows to the set the console needs, and every one of them is
read exactly the way the approval is read: anchored at the START of the comment,
the emoji optional, the id `[0-9a-f]{6,}`, and honoured only when its author is
NOT the pipeline's own Linear identity. That last clause is the whole gate
(DRE-2721 — two stray comments carrying a marker line once overrode a real
critic rejection), and it is why the self-approval refusal is untouched by this
card: the proposer cannot approve, decline, exclude or add anything.

WHAT THE DRAIN DOES WITH THEM. It moves the approved batch MINUS every excluded
card PLUS every added card, and it writes down what it did in one record:
`🧺 groom-drained: <id>`, a summary line in a fixed grammar, and a table with
one row per card. Every refusal — before any write, always — posts
`🧺 groom-drain-refused: <id> — <reason>` and exits 2, so a drain that refuses
and says so only in a workflow log is no longer a stall with an alibi.

AND IT READS THE WHOLE THREAD. One comment per per-card decision means a
proposal card outgrows the fifty-comment window the moment a CEO excludes a
handful of cards, and the approval — posted first — is the one that falls out.
A drain that read the newest fifty would refuse a batch it was looking at.

Run: cd bureau-pipeline && python3 -m pytest tests/test_groomer_decisions.py -v
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import groomer  # noqa: E402
import linear_ops  # noqa: E402

from test_groomer_population import CYCLES, card  # noqa: E402

PROPOSAL_CARD = "DRE-2683"

#: Cards that exist in the lane but were never in any batch — what an addition
#: names, and what an exclusion must not be allowed to invent.
SPARE = ("DRE-900", "DRE-901")


class FakeOps:
    """Records every write the drain attempts, and serves the thread the way
    Linear does: `comment_records` gives the NEWEST fifty unless the caller
    asks for the whole thread.

    That default is the point of the pagination test below — a drain that does
    not ask for the whole thread gets a truncated one here, exactly as it would
    against the live API, rather than a fixture that hides the difference.
    """

    def __init__(self, comments=None, *, lanes=None, cycles=CYCLES):
        self.comments = list(comments or [])
        self.lanes = dict(lanes or {})
        self.cycles = list(cycles)
        self.state_writes: list[tuple[str, str]] = []
        self.mutations: list[tuple[str, dict]] = []
        self.written: list[tuple[str, str]] = []
        self.whole_thread_asked: list[bool] = []

    def comment_records(self, identifier, *, whole_thread=False):
        self.whole_thread_asked.append(bool(whole_thread))
        if whole_thread:
            return list(self.comments)
        return list(self.comments)[-linear_ops.COMMENT_WINDOW:]

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
    """The proposal AS THE CARD CARRIES IT — the very string `propose --post`
    writes, so the render and every read of it are one contract."""
    return {"body": groomer.proposal_comment(proposal),
            "authored_by_pipeline": True}


def _decision(tag, proposal, *, card=None, reason=None, by_pipeline=False):
    """One decision comment, written through the groomer's own writer.

    Never a hand-typed marker string: the writer and the reader are two halves
    of one contract, and a test that types its own fixture proves the reader
    against a shape nothing writes.
    """
    return {"body": groomer.decision_comment(
                tag, groomer.proposal_id(proposal), card=card, reason=reason),
            "authored_by_pipeline": by_pipeline}


def _thread(proposal, *decisions):
    """The ordinary thread: the proposal, its approval, then the per-card
    decisions in the order the CEO wrote them."""
    return [_record(proposal),
            _decision(groomer.APPROVAL_TAG, proposal), *decisions]


def _batch_ids(proposal):
    return [r["identifier"] for r in sorted(proposal["outcomes"]["now"],
                                            key=lambda r: r["position"])]


def _lanes(proposal, **overrides):
    """Every card the drain will look up, in Intake unless said otherwise."""
    lanes = {i: "Intake" for i in _batch_ids(proposal)}
    lanes.update({i: "Intake" for i in SPARE})
    lanes.update(overrides)
    return lanes


def _row(body, identifier):
    """The card's row in the drain's table, as one string."""
    return next((ln for ln in body.splitlines()
                 if re.match(rf"^\|[^|]*\|\s*{identifier}\s*\|", ln.strip())), None)


def _outcome(body, identifier):
    row = _row(body, identifier)
    assert row, f"{identifier} has no row in the drain's record:\n{body}"
    return [c.strip() for c in row.strip().strip("|").split("|")][2]


def _sole_record(ops):
    assert len(ops.written) == 1, (
        f"the drain wrote {len(ops.written)} record(s), not one")
    target, body = ops.written[0]
    assert target == PROPOSAL_CARD
    return body


# --------------------------------------------------------------------------
# the vocabulary itself — one shape, read the way the approval is read
# --------------------------------------------------------------------------
def test_the_vocabulary_carries_every_marker_the_console_mirrors():
    assert groomer.DECLINE_TAG == "groom-declined"
    assert groomer.EXCLUDE_TAG == "groom-excluded"
    assert groomer.ADD_TAG == "groom-added"
    assert groomer.DRAINED_TAG == "groom-drained"
    assert groomer.DRAIN_REFUSED_TAG == "groom-drain-refused"
    assert groomer.DECISION_TAGS == (
        groomer.APPROVAL_TAG, groomer.DECLINE_TAG,
        groomer.EXCLUDE_TAG, groomer.ADD_TAG)


@pytest.mark.parametrize("tag", ["groom-approved", "groom-declined",
                                 "groom-excluded", "groom-added"])
def test_every_decision_marker_is_anchored_and_the_emoji_is_optional(tag):
    """The same anchoring the approval has always had, for the same reason: a
    reader that matched the marker anywhere would read a sentence ABOUT
    excluding a card as an exclusion."""
    pid = "abc123def456"
    tail = " DRE-77 — because" if tag in (groomer.EXCLUDE_TAG,
                                          groomer.ADD_TAG) else " — because"
    assert groomer.decision_match(tag, f"{groomer.MARK} {tag}: {pid}{tail}")
    assert groomer.decision_match(tag, f"{tag}: {pid}{tail}"), (
        "the emoji must be optional, exactly as it is on the approval"
    )
    assert groomer.decision_match(
        tag, f"I will send {tag}: {pid}{tail} once Ana has read it") is None
    assert groomer.decision_match(tag, f"{groomer.MARK} {tag}: nope{tail}") is None


def test_a_decision_marker_reads_the_rest_of_a_multi_line_comment():
    """A CEO writes prose under the marker. The marker still opens the comment
    and the card it names is still on the first line."""
    pid = "abc123def456"
    body = (f"{groomer.MARK} {groomer.EXCLUDE_TAG}: {pid} DRE-77 — design first"
            f"\n\nAna and I talked this through on Tuesday.")
    got = groomer.decision_match(groomer.EXCLUDE_TAG, body)
    assert got and got.group(1) == pid
    assert "DRE-77" in got.group(2)


# --------------------------------------------------------------------------
# exclusions — the batch minus these, and both are recorded as held back
# --------------------------------------------------------------------------
def test_two_exclusions_hold_their_cards_back_and_the_rest_moves_in_order():
    proposal = _proposal()
    batch = _batch_ids(proposal)
    out = [batch[0], batch[2]]
    ops = FakeOps(comments=_thread(
        proposal,
        _decision(groomer.EXCLUDE_TAG, proposal, card=out[0],
                  reason="design is not settled"),
        _decision(groomer.EXCLUDE_TAG, proposal, card=out[1])),
        lanes=_lanes(proposal))
    result = groomer.drain(ops, card=PROPOSAL_CARD)
    assert [i for i, _ in ops.state_writes] == [batch[1]], (
        "the drain moved a card the CEO excluded"
    )
    assert result["moved"] == [batch[1]]
    assert sorted(result["held_back"]) == sorted(out)


def test_an_excluded_card_is_recorded_as_held_back_with_the_marker_as_the_why():
    proposal = _proposal()
    batch = _batch_ids(proposal)
    ops = FakeOps(comments=_thread(
        proposal,
        _decision(groomer.EXCLUDE_TAG, proposal, card=batch[0],
                  reason="design is not settled")),
        lanes=_lanes(proposal))
    groomer.drain(ops, card=PROPOSAL_CARD)
    body = _sole_record(ops)
    assert _outcome(body, batch[0]) == "held back"
    row = _row(body, batch[0])
    assert groomer.EXCLUDE_TAG in row, "the Why does not name the marker"
    assert "design is not settled" in row


def test_an_exclusion_naming_a_card_outside_the_batch_changes_nothing():
    """An exclusion cannot invent a card into the batch, and it cannot hold
    back a card that was never going to move. It is reported and ignored."""
    proposal = _proposal()
    ops = FakeOps(comments=_thread(
        proposal, _decision(groomer.EXCLUDE_TAG, proposal, card=SPARE[0])),
        lanes=_lanes(proposal))
    result = groomer.drain(ops, card=PROPOSAL_CARD)
    assert result["moved"] == _batch_ids(proposal)
    assert SPARE[0] not in {i for i, _ in ops.state_writes}
    assert _outcome(_sole_record(ops), SPARE[0]) == "refused"


# --------------------------------------------------------------------------
# additions — after the batch, on the batch's own cycle
# --------------------------------------------------------------------------
def test_an_addition_moves_last_and_takes_the_batchs_first_cycle():
    proposal = _proposal()
    batch = _batch_ids(proposal)
    ops = FakeOps(comments=_thread(
        proposal, _decision(groomer.ADD_TAG, proposal, card=SPARE[0],
                            reason="Ana needs it this week")),
        lanes=_lanes(proposal))
    result = groomer.drain(ops, card=PROPOSAL_CARD)
    assert [i for i, _ in ops.state_writes] == batch + [SPARE[0]], (
        "the added card did not move after the batch, in order"
    )
    assert result["added"] == [SPARE[0]]
    for _, variables in ops.mutations:
        assert variables["input"]["cycleId"] == "cyc-12", (
            "the added card was filed into a cycle the batch was not approved for"
        )
    body = _sole_record(ops)
    assert _outcome(body, SPARE[0]) == "added"
    assert groomer.ADD_TAG in _row(body, SPARE[0])


def test_an_addition_naming_a_card_that_is_not_in_the_lane_refuses():
    """Before any write, and the refusal names the lane the card is in now —
    an addition is the CEO reaching into the lane, so a card that has already
    left it is not the card they meant."""
    proposal = _proposal()
    ops = FakeOps(comments=_thread(
        proposal, _decision(groomer.ADD_TAG, proposal, card=SPARE[0])),
        lanes=_lanes(proposal, **{SPARE[0]: "In Progress"}))
    with pytest.raises(groomer.NotInLane) as exc:
        groomer.drain(ops, card=PROPOSAL_CARD)
    assert ops.state_writes == [], "the batch moved around a bad addition"
    assert ops.mutations == []
    assert SPARE[0] in str(exc.value) and "In Progress" in str(exc.value)
    body = _sole_record(ops)
    assert body.startswith(f"{groomer.MARK} {groomer.DRAIN_REFUSED_TAG}: ")
    assert "In Progress" in body, "the refusal record does not name the lane"


def test_an_addition_of_a_card_already_in_the_batch_moves_it_once():
    proposal = _proposal()
    batch = _batch_ids(proposal)
    ops = FakeOps(comments=_thread(
        proposal, _decision(groomer.ADD_TAG, proposal, card=batch[1])),
        lanes=_lanes(proposal))
    result = groomer.drain(ops, card=PROPOSAL_CARD)
    assert result["moved"] == batch, "a card moved twice"
    assert [i for i, _ in ops.state_writes] == batch


def test_a_card_named_by_both_markers_follows_the_newest_comment():
    """The CEO excluded it, then changed their mind. The newest comment is the
    decision that is current — the same rule the approval has always used."""
    proposal = _proposal()
    batch = _batch_ids(proposal)
    excluded_then_added = _thread(
        proposal,
        _decision(groomer.EXCLUDE_TAG, proposal, card=SPARE[0]),
        _decision(groomer.ADD_TAG, proposal, card=SPARE[0]))
    ops = FakeOps(comments=excluded_then_added, lanes=_lanes(proposal))
    assert groomer.drain(ops, card=PROPOSAL_CARD)["added"] == [SPARE[0]]

    added_then_excluded = _thread(
        proposal,
        _decision(groomer.ADD_TAG, proposal, card=batch[0]),
        _decision(groomer.EXCLUDE_TAG, proposal, card=batch[0]))
    ops = FakeOps(comments=added_then_excluded, lanes=_lanes(proposal))
    result = groomer.drain(ops, card=PROPOSAL_CARD)
    assert result["held_back"] == [batch[0]]
    assert batch[0] not in result["moved"]


def test_a_decision_naming_another_batch_is_never_honoured():
    """Two proposals on one card. A decision names the id it belongs to, and a
    marker naming the older batch says nothing about the approved one."""
    proposal = _proposal()
    other = groomer.decision_comment(
        groomer.EXCLUDE_TAG, "0" * 12, card=_batch_ids(proposal)[0])
    ops = FakeOps(comments=_thread(proposal) +
                  [{"body": other, "authored_by_pipeline": False}],
                  lanes=_lanes(proposal))
    result = groomer.drain(ops, card=PROPOSAL_CARD)
    assert result["moved"] == _batch_ids(proposal)
    assert result["held_back"] == []


# --------------------------------------------------------------------------
# declines
# --------------------------------------------------------------------------
def test_a_decline_refuses_the_drain_and_names_the_reason():
    proposal = _proposal()
    ops = FakeOps(comments=_thread(
        proposal, _decision(groomer.DECLINE_TAG, proposal,
                            reason="the Forms epic lands first")),
        lanes=_lanes(proposal))
    with pytest.raises(groomer.NotApproved) as exc:
        groomer.drain(ops, card=PROPOSAL_CARD)
    assert ops.state_writes == [] and ops.mutations == []
    assert "the Forms epic lands first" in str(exc.value)
    body = _sole_record(ops)
    assert body.startswith(f"{groomer.MARK} {groomer.DRAIN_REFUSED_TAG}: ")
    assert "the Forms epic lands first" in body


def test_a_decline_with_no_reason_is_ignored_and_reported():
    """The reason is what makes a decline actionable — without it the CEO has
    said no to a batch and left nobody able to fix it. So it is not read as a
    decline at all, the approval still stands, and the record says so."""
    proposal = _proposal()
    bare = {"body": f"{groomer.MARK} {groomer.DECLINE_TAG}: "
                    f"{groomer.proposal_id(proposal)}",
            "authored_by_pipeline": False}
    ops = FakeOps(comments=_thread(proposal) + [bare], lanes=_lanes(proposal))
    result = groomer.drain(ops, card=PROPOSAL_CARD)
    assert result["moved"] == _batch_ids(proposal)
    body = _sole_record(ops)
    assert groomer.DECLINE_TAG in body, "the ignored decline is not reported"
    assert "reason" in body.lower()


def test_a_decline_older_than_the_approval_does_not_refuse():
    proposal = _proposal()
    ops = FakeOps(comments=[
        _record(proposal),
        _decision(groomer.DECLINE_TAG, proposal, reason="not this week"),
        _decision(groomer.APPROVAL_TAG, proposal),
    ], lanes=_lanes(proposal))
    assert groomer.drain(ops, card=PROPOSAL_CARD)["moved"] == _batch_ids(proposal)


# --------------------------------------------------------------------------
# the authorship rule — the proposer decides nothing
# --------------------------------------------------------------------------
@pytest.mark.parametrize("tag", ["groom-declined", "groom-excluded",
                                 "groom-added"])
def test_a_decision_authored_by_the_pipeline_is_ignored_and_the_record_says_so(tag):
    """The self-approval refusal, applied to the whole vocabulary. A marker the
    pipeline wrote is a marker the pipeline could mint for itself, so none of
    them is honoured — and the record names each one it dropped."""
    proposal = _proposal()
    named = SPARE[0] if tag != groomer.DECLINE_TAG else None
    ops = FakeOps(comments=_thread(
        proposal, _decision(tag, proposal, card=named, reason="because",
                            by_pipeline=True)),
        lanes=_lanes(proposal))
    result = groomer.drain(ops, card=PROPOSAL_CARD)
    assert result["moved"] == _batch_ids(proposal), (
        f"a {tag} written by the pipeline changed the batch"
    )
    body = _sole_record(ops)
    assert tag in body, "the record does not name the decision it ignored"
    assert "pipeline" in body.lower()
    assert len(result["refused"]) == 1


def test_a_pipeline_written_exclusion_does_not_hold_its_card_back():
    proposal = _proposal()
    batch = _batch_ids(proposal)
    ops = FakeOps(comments=_thread(
        proposal, _decision(groomer.EXCLUDE_TAG, proposal, card=batch[0],
                            by_pipeline=True)),
        lanes=_lanes(proposal))
    result = groomer.drain(ops, card=PROPOSAL_CARD)
    assert result["moved"] == batch
    assert result["held_back"] == []


# --------------------------------------------------------------------------
# the record the drain writes
# --------------------------------------------------------------------------
_SUMMARY = re.compile(
    r"^moved: (\d+) · held back: (\d+) · added: (\d+) · refused: (\d+) → "
    r"(\w+) at \d{4}-\d{2}-\d{2} \d{2}:\d{2} PT$", re.M)


def test_the_drained_record_opens_with_the_marker_and_the_fixed_grammar():
    proposal = _proposal()
    batch = _batch_ids(proposal)
    ops = FakeOps(comments=_thread(
        proposal,
        _decision(groomer.EXCLUDE_TAG, proposal, card=batch[0]),
        _decision(groomer.ADD_TAG, proposal, card=SPARE[0]),
        _decision(groomer.ADD_TAG, proposal, card=SPARE[1], by_pipeline=True)),
        lanes=_lanes(proposal))
    groomer.drain(ops, card=PROPOSAL_CARD)
    body = _sole_record(ops)
    assert body.splitlines()[0] == \
        f"{groomer.MARK} {groomer.DRAINED_TAG}: {proposal['id']}"
    summary = _SUMMARY.search(body)
    assert summary, f"the summary line is not in the fixed grammar:\n{body}"
    assert summary.groups() == ("2", "1", "1", "1", "Planning")
    assert "| # | Card | Outcome | Why |" in body


def test_the_record_carries_one_row_per_card_and_the_move_order():
    proposal = _proposal()
    batch = _batch_ids(proposal)
    ops = FakeOps(comments=_thread(
        proposal,
        _decision(groomer.EXCLUDE_TAG, proposal, card=batch[1]),
        _decision(groomer.ADD_TAG, proposal, card=SPARE[0])),
        lanes=_lanes(proposal))
    groomer.drain(ops, card=PROPOSAL_CARD)
    body = _sole_record(ops)
    assert [_outcome(body, i) for i in batch] == ["moved", "held back", "moved"]
    assert _outcome(body, SPARE[0]) == "added"
    moved_in_order = [ln.split("|")[2].strip() for ln in body.splitlines()
                      if re.match(r"^\|\s*\d+\s*\|", ln.strip())]
    assert moved_in_order == [batch[0], batch[2], SPARE[0]], (
        "the numbered rows do not carry the order the cards moved in"
    )


def test_every_row_carries_one_of_the_four_outcomes():
    proposal = _proposal()
    ops = FakeOps(comments=_thread(proposal), lanes=_lanes(proposal))
    groomer.drain(ops, card=PROPOSAL_CARD)
    body = _sole_record(ops)
    for identifier in _batch_ids(proposal):
        assert _outcome(body, identifier) in groomer.DRAIN_OUTCOMES


# --------------------------------------------------------------------------
# a second dispatch moves nothing
# --------------------------------------------------------------------------
def test_a_drain_against_an_already_drained_batch_refuses_and_writes_nothing():
    proposal = _proposal()
    ops = FakeOps(comments=_thread(proposal), lanes=_lanes(proposal))
    groomer.drain(ops, card=PROPOSAL_CARD)
    first = _sole_record(ops)

    again = FakeOps(comments=_thread(proposal) +
                    [{"body": first, "authored_by_pipeline": True}],
                    lanes=_lanes(proposal))
    with pytest.raises(groomer.AlreadyDrained) as exc:
        groomer.drain(again, card=PROPOSAL_CARD)
    assert again.state_writes == [], "a second dispatch moved cards"
    assert again.mutations == []
    assert proposal["id"] in str(exc.value)
    assert _sole_record(again).startswith(
        f"{groomer.MARK} {groomer.DRAIN_REFUSED_TAG}: {proposal['id']} — ")


def test_a_drained_record_for_another_batch_does_not_block_this_one():
    proposal = _proposal()
    stale = groomer.drained_record({
        "proposal": "0" * 12, "rows": [], "moved": [], "held_back": [],
        "added": [], "refused": [], "to": "Planning", "from": "Intake",
        "cycle": 12})
    ops = FakeOps(comments=[{"body": stale, "authored_by_pipeline": True}]
                  + _thread(proposal), lanes=_lanes(proposal))
    assert groomer.drain(ops, card=PROPOSAL_CARD)["moved"] == _batch_ids(proposal)


# --------------------------------------------------------------------------
# every refusal is written down, and every refusal exits 2
# --------------------------------------------------------------------------
#: Every refusal the drain can reach AFTER it has read the card — the set that
#: owes a written record. The terminal destination is deliberately not here:
#: it is caught before the read, and there is no batch for it to name.
REFUSALS = ("no approval", "a foreign approval", "no record",
            "a card that left the lane", "a cycle Linear does not carry")


def _refusals(proposal):
    """One FakeOps per refusal in REFUSALS."""
    batch = _batch_ids(proposal)
    return {
        "no approval": FakeOps(comments=[_record(proposal)],
                               lanes=_lanes(proposal)),
        "a foreign approval": FakeOps(
            comments=[_record(proposal),
                      _decision(groomer.APPROVAL_TAG, proposal,
                                by_pipeline=True)],
            lanes=_lanes(proposal)),
        "no record": FakeOps(
            comments=[{"body": groomer.decision_comment(
                           groomer.APPROVAL_TAG, "0" * 12),
                       "authored_by_pipeline": False}],
            lanes=_lanes(proposal)),
        "a card that left the lane": FakeOps(
            comments=_thread(proposal),
            lanes=_lanes(proposal, **{batch[0]: "In Progress"})),
        "a cycle Linear does not carry": FakeOps(
            comments=_thread(proposal), lanes=_lanes(proposal), cycles=[]),
    }


@pytest.mark.parametrize("why", REFUSALS)
def test_every_refusal_posts_a_drain_refused_record(why):
    proposal = _proposal()
    ops = _refusals(proposal)[why]
    with pytest.raises(groomer.DrainRefused):
        groomer.drain(ops, card=PROPOSAL_CARD)
    assert ops.state_writes == [], f"{why}: a refused drain moved a card"
    assert ops.mutations == [], f"{why}: a refused drain assigned a cycle"
    body = _sole_record(ops)
    marker, _, reason = body.splitlines()[0].partition(" — ")
    assert marker.startswith(f"{groomer.MARK} {groomer.DRAIN_REFUSED_TAG}: ")
    assert reason.strip(), f"{why}: the refusal record carries no reason"


@pytest.mark.parametrize("why", REFUSALS)
def test_every_refusal_exits_2(why, monkeypatch):
    ops = _refusals(_proposal())[why]
    monkeypatch.setattr(groomer, "linear_ops", ops)
    assert groomer.main(["drain", "--card", PROPOSAL_CARD]) == 2


def test_the_pen_being_held_is_refused_in_writing(monkeypatch):
    proposal = _proposal()
    monkeypatch.setattr(groomer, "INTAKE_HOLD", "2026-09-08")
    ops = FakeOps(comments=_thread(proposal), lanes=_lanes(proposal))
    with pytest.raises(groomer.IntakeHeld):
        groomer.drain(ops, card=PROPOSAL_CARD)
    assert ops.state_writes == []
    body = _sole_record(ops)
    assert body.startswith(f"{groomer.MARK} {groomer.DRAIN_REFUSED_TAG}: ")
    assert len(body.strip().splitlines()) == 1, (
        "the refusal marker must be one line — the console reads its grammar"
    )


def test_a_terminal_destination_is_refused_before_the_card_is_even_read():
    """The one refusal that writes nothing: it is a bad invocation, caught
    before the drain has read the card or knows which batch to name."""
    proposal = _proposal()
    ops = FakeOps(comments=_thread(proposal), lanes=_lanes(proposal))
    with pytest.raises(groomer.WillNotCancel):
        groomer.drain(ops, card=PROPOSAL_CARD, to="Canceled")
    assert ops.written == [] and ops.state_writes == []


# --------------------------------------------------------------------------
# the whole thread, paginated — one comment per per-card decision
# --------------------------------------------------------------------------
def test_the_drain_reads_the_whole_thread_not_the_newest_fifty():
    """120 comments, the approval the oldest decision on the card and well
    outside the fifty-comment window. A drain reading the window would find no
    approval and refuse a batch it was looking at."""
    proposal = _proposal()
    chatter = [{"body": f"a note ({n})", "authored_by_pipeline": False}
               for n in range(118)]
    ops = FakeOps(comments=_thread(proposal) + chatter, lanes=_lanes(proposal))
    assert len(ops.comments) == 120
    window = ops.comment_records(PROPOSAL_CARD)
    assert not any(groomer.decision_match(groomer.APPROVAL_TAG, c["body"])
                   for c in window), (
        "the fixture must put the approval OUTSIDE the window for this to prove "
        "anything"
    )
    assert groomer.drain(ops, card=PROPOSAL_CARD)["moved"] == _batch_ids(proposal)
    assert all(ops.whole_thread_asked[1:]), (
        "the drain read the thread without asking for the whole of it"
    )


def test_comment_records_walks_past_the_window_when_asked_for_the_whole_thread():
    """`comment_records` outside a sweep reads the newest fifty and stops
    (DRE-3250). The drain needs the whole thread, so it asks — and the ask has
    to actually page, or the fixture below hands it fifty of 120."""
    thread = [{"body": f"comment {n}", "createdAt": f"2026-09-0{n % 9 + 1}",
               "user": {"id": "someone"}} for n in range(120)]
    newest_first = list(reversed(thread))
    pages: list[str | None] = []

    def fake_gql(query, variables=None):
        v = variables or {}
        if "comments(" not in " ".join(query.split()):
            return {"viewer": {"id": "fleet"}}
        after = v.get("after")
        pages.append(after)
        start = int(after) if after else 0
        size = linear_ops.COMMENT_WINDOW if after is None else 100
        nodes = newest_first[start:start + size]
        conn = {"pageInfo": {"hasNextPage": start + len(nodes) < len(thread),
                             "endCursor": str(start + len(nodes))},
                "nodes": nodes}
        return {"viewer": {"id": "fleet"}, "issue": {"comments": conn}}

    with mock.patch.object(linear_ops, "gql", side_effect=fake_gql):
        windowed = linear_ops.comment_records("DRE-1")
        whole = linear_ops.comment_records("DRE-1", whole_thread=True)
    assert len(windowed) == linear_ops.COMMENT_WINDOW
    assert len(whole) == 120, "the whole-thread read stopped at the window"
    assert whole[0]["body"] == "comment 0", "the oldest comment was never read"
    assert len(pages) > 2, "no page beyond the window was requested"


# --------------------------------------------------------------------------
# a drain is a move, not a judgement
# --------------------------------------------------------------------------
def _never(what):
    def refuse(*args, **kwargs):                # pragma: no cover - the point
        raise AssertionError(f"the drain {what}")
    return refuse


def test_a_drain_with_decisions_still_makes_no_model_call(monkeypatch):
    proposal = _proposal()
    ops = FakeOps(comments=_thread(
        proposal,
        _decision(groomer.EXCLUDE_TAG, proposal, card=_batch_ids(proposal)[0]),
        _decision(groomer.ADD_TAG, proposal, card=SPARE[0])),
        lanes=_lanes(proposal))
    monkeypatch.setattr(groomer.groom_judgement, "run", _never("called a model"))
    monkeypatch.setattr(groomer, "read_population", _never("read the population"))
    monkeypatch.setattr(groomer, "linear_ops", ops)
    assert groomer.main(["drain", "--card", PROPOSAL_CARD]) == 0


# --------------------------------------------------------------------------
# the document the console mirrors
# --------------------------------------------------------------------------
def test_the_doc_carries_the_vocabulary_table():
    doc = (ROOT / "docs" / "groomer.md").read_text(encoding="utf-8")
    for tag in (groomer.PROPOSAL_TAG, groomer.APPROVAL_TAG, groomer.DECLINE_TAG,
                groomer.EXCLUDE_TAG, groomer.ADD_TAG, groomer.DRAINED_TAG,
                groomer.DRAIN_REFUSED_TAG):
        assert f"`{groomer.MARK} {tag}:" in doc, (
            f"docs/groomer.md does not carry {tag} in the vocabulary table"
        )
    assert "| Marker | Written by | Shape |" in doc
    assert "moved: n · held back: n · added: n · refused: n" in doc
