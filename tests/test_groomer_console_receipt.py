"""RED-first: the drain honours a console-signed decision, and still refuses the
fleet's own (DRE-3754, the pipeline half of DRE-3735).

The CEO, 2026-09-12 21:28 PT: *"If I sign in, I am who I am by definition. Why
would I need to give you any more proof than that?"* — after Green Light's
Decline refused him for the third or fourth time with "connect your own Linear
key". The console holds Linear keys only for the fleet and for operator-tools,
so a decision the console writes without a pasted personal key is authored by
`Agent-Bureau`: the drain's own identity, whose markers it refuses (DRE-2721 —
the proposer cannot approve its own proposal).

The rule these tests hold, and the whole of it:

  * A marker the fleet wrote **with a valid console receipt** on its last line
    decides — approval, decline, exclusion, addition, and the repo switch.
  * A marker the fleet wrote **without** one is refused exactly as today, and
    one whose receipt fails is refused with the reason named where the CEO
    reads the drain's record.
  * A marker authored by somebody other than the pipeline — the CEO's own
    Linear user — decides as it always has, and never makes the drain fetch a
    key: an unreachable console refuses only receipted markers.

What the receipt binds: the marker line itself, the card the comment sits on,
the batch it names, the console user, and the time — held against the
comment's OWN Linear creation time rather than the drain's clock, because a
week-old exclusion or repo hold is still the CEO's answer when the drain reads
it, and a copy posted later is not.

Run: cd bureau-pipeline && python3 -m pytest tests/test_groomer_console_receipt.py -v
"""
from __future__ import annotations

import os
import sys
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import console_receipt  # noqa: E402
import console_receipt_vectors as V  # noqa: E402
import groomer  # noqa: E402
import linear_ops  # noqa: E402

from test_groomer_decisions import (  # noqa: E402
    PROPOSAL_CARD, SPARE, FakeOps, _batch_ids, _decision, _lanes, _outcome,
    _proposal, _record, _row, _sole_record)

OPENSSL = V.capable_openssl()
USER = "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"
SIGNED_AT = datetime(2026, 9, 13, 15, 15, 2, tzinfo=timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _linear_stamp(moment: datetime) -> str:
    """Linear's own createdAt shape — milliseconds, Z."""
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


@pytest.fixture(autouse=True)
def _console_key(monkeypatch):
    """The console's published key is the TEST key; the openssl the module
    probes is the capable one this machine has. Fetch counts are recorded so a
    test can prove the drain never asked."""
    if OPENSSL is None:
        if os.environ.get("CI"):
            pytest.fail("no Ed25519-capable openssl on a CI runner")
        pytest.skip("no Ed25519-capable openssl on this machine")
    monkeypatch.setenv("OPENSSL_BIN", OPENSSL)
    monkeypatch.setattr(console_receipt, "_OPENSSL", None, raising=False)
    fetched = []

    def loader():
        fetched.append(1)
        return console_receipt.PublicKey.from_b64(V.PUBLIC_KEY_B64)
    monkeypatch.setattr(groomer, "_VERIFIER",
                        console_receipt.Verifier(key_loader=loader))
    return fetched


def receipted(marker: str, *, card: str = PROPOSAL_CARD, proposal: str,
              at: datetime = SIGNED_AT, posted: datetime | None = None,
              user: str = USER, sign_card: str | None = None,
              sign_proposal: str | None = None, sign_marker: str | None = None,
              by_pipeline: bool = True) -> dict:
    """A decision exactly as the console writes it on the fleet's key: the
    marker, a line the CEO reads, and the receipt on the last line. `sign_*`
    lets a test sign something other than what the comment carries — the
    shape of a receipt copied from somewhere else."""
    signed = console_receipt.signed_bytes(
        sign_marker or marker, sign_card or card,
        sign_proposal or proposal, user, _iso(at))
    sig = V.b64url(V.sign(signed, openssl=OPENSSL))
    trailer = console_receipt.trailer(
        card=sign_card or card, proposal=sign_proposal or proposal, user=user,
        at=_iso(at), kid=V.KID, sig=sig)
    body = (f"{marker}\n\nDecided in the console by Test Owner (tenant owner) "
            f"at 2026-09-13 08:15 PT.\n{trailer}")
    posted = posted or at + timedelta(seconds=1)
    return {"body": body, "authored_by_pipeline": by_pipeline,
            "created_at": _linear_stamp(posted)}


def _marker(tag, proposal, *, card=None, reason=None):
    return groomer.decision_comment(tag, groomer.proposal_id(proposal),
                                    card=card, reason=reason)


def _approval(proposal, **kw):
    return receipted(_marker(groomer.APPROVAL_TAG, proposal),
                     proposal=groomer.proposal_id(proposal), **kw)


# --------------------------------------------------------------------------
# accepted: a console-signed decision on the fleet identity
# --------------------------------------------------------------------------
def test_a_receipted_approval_on_the_fleet_identity_drains_the_batch():
    proposal = _proposal()
    ops = FakeOps(comments=[_record(proposal), _approval(proposal)],
                  lanes=_lanes(proposal))
    result = groomer.drain(ops, card=PROPOSAL_CARD)
    assert result["moved"] == _batch_ids(proposal)


def test_a_receipted_decline_on_the_fleet_identity_refuses_with_the_ceos_reason():
    proposal = _proposal()
    pid = groomer.proposal_id(proposal)
    decline = receipted(_marker(groomer.DECLINE_TAG, proposal,
                                reason="not this batch"), proposal=pid,
                        at=SIGNED_AT + timedelta(minutes=5))
    ops = FakeOps(comments=[_record(proposal), _approval(proposal), decline],
                  lanes=_lanes(proposal))
    with pytest.raises(groomer.NotApproved) as exc:
        groomer.drain(ops, card=PROPOSAL_CARD)
    assert "not this batch" in str(exc.value)
    assert ops.state_writes == []


def test_a_receipted_not_this_one_holds_the_card_back():
    """The CEO, 2026-09-13 07:55 PT: *"I can say, 'I don't want to do this card
    in the Groomer.'"* — the per-card exclusion, from the console, no key."""
    proposal = _proposal()
    batch = _batch_ids(proposal)
    pid = groomer.proposal_id(proposal)
    exclusion = receipted(_marker(groomer.EXCLUDE_TAG, proposal, card=batch[0]),
                          proposal=pid, at=SIGNED_AT - timedelta(days=2))
    ops = FakeOps(comments=[_record(proposal), exclusion, _approval(proposal)],
                  lanes=_lanes(proposal))
    result = groomer.drain(ops, card=PROPOSAL_CARD)
    assert result["held_back"] == [batch[0]], (
        "a two-day-old console exclusion was not honoured — staleness is "
        "measured against the comment's own time, never the drain's clock")
    assert batch[0] not in result["moved"]


def test_a_receipted_repo_hold_switches_the_repo_off_for_propose():
    hold = receipted(f"{groomer.MARK} {groomer.REPO_HOLD_TAG}: atlas",
                     proposal=console_receipt.NO_PROPOSAL,
                     at=SIGNED_AT - timedelta(days=20))
    records = groomer.vouch([hold], card=PROPOSAL_CARD)
    assert groomer.held_repos(records) == ["atlas"]


def test_propose_reads_the_repo_hold_through_the_receipt():
    """`read_holds` is the propose side's read. It must vouch the thread it
    reads, or a console hold switches nothing."""
    hold = receipted(f"{groomer.MARK} {groomer.REPO_HOLD_TAG}: atlas",
                     proposal=console_receipt.NO_PROPOSAL)

    class Ops:
        def comment_records(self, identifier, *, whole_thread=False):
            return [hold]

    class Args:
        hold_repo = None
        post = PROPOSAL_CARD

    assert groomer.read_holds(Ops(), Args()) == ["atlas"]


def test_the_next_proposal_answers_a_receipted_decline():
    proposal = _proposal()
    pid = groomer.proposal_id(proposal)
    decline = receipted(_marker(groomer.DECLINE_TAG, proposal,
                                reason="two of these edit one file"),
                        proposal=pid)
    records = groomer.vouch([_record(proposal), decline], card=PROPOSAL_CARD)
    assert groomer.decline_to_answer(records) == {
        "id": pid, "reason": "two of these edit one file"}


# --------------------------------------------------------------------------
# refused: everything else the fleet could write
# --------------------------------------------------------------------------
def test_a_fleet_approval_with_no_receipt_is_refused_exactly_as_today(_console_key):
    proposal = _proposal()
    bare = _decision(groomer.APPROVAL_TAG, proposal, by_pipeline=True)
    ops = FakeOps(comments=[_record(proposal), bare], lanes=_lanes(proposal))
    with pytest.raises(groomer.NotApproved) as exc:
        groomer.drain(ops, card=PROPOSAL_CARD)
    assert ops.state_writes == []
    assert "the proposer cannot approve its own proposal" in str(exc.value)
    assert _console_key == [], "a marker with no receipt made the drain fetch a key"


def test_a_bad_signature_is_refused_and_the_record_names_it():
    proposal = _proposal()
    pid = groomer.proposal_id(proposal)
    forged = _approval(proposal)
    # One character of the signature changed — still well-formed base64url.
    sig_at = forged["body"].rindex("sig=") + len("sig=")
    flipped = "A" if forged["body"][sig_at] != "A" else "B"
    forged["body"] = forged["body"][:sig_at] + flipped + forged["body"][sig_at + 1:]
    ops = FakeOps(comments=[_record(proposal), forged], lanes=_lanes(proposal))
    with pytest.raises(groomer.NotApproved) as exc:
        groomer.drain(ops, card=PROPOSAL_CARD)
    assert ops.state_writes == []
    assert "signature" in str(exc.value)
    body = _sole_record(ops)
    assert body.startswith(f"{groomer.MARK} {groomer.DRAIN_REFUSED_TAG}: {pid}")
    assert "signature" in body


def test_a_receipt_copied_onto_another_card_is_refused():
    proposal = _proposal()
    copied = _approval(proposal, sign_card="DRE-4242")
    ops = FakeOps(comments=[_record(proposal), copied], lanes=_lanes(proposal))
    with pytest.raises(groomer.NotApproved) as exc:
        groomer.drain(ops, card=PROPOSAL_CARD)
    assert "DRE-4242" in str(exc.value)
    assert ops.state_writes == []


def test_a_receipt_copied_onto_another_proposal_is_refused():
    """An approval the CEO gave batch A, re-used under batch B's marker."""
    proposal = _proposal()
    copied = _approval(proposal, sign_proposal="0123456789ab",
                       sign_marker=groomer.decision_comment(
                           groomer.APPROVAL_TAG, "0123456789ab"))
    ops = FakeOps(comments=[_record(proposal), copied], lanes=_lanes(proposal))
    with pytest.raises(groomer.NotApproved) as exc:
        groomer.drain(ops, card=PROPOSAL_CARD)
    assert "0123456789ab" in str(exc.value)
    assert ops.state_writes == []


def test_a_stale_receipt_is_refused():
    """A receipt posted an hour after it was signed is a copy of one, not a
    decision somebody just made."""
    proposal = _proposal()
    late = _approval(proposal, posted=SIGNED_AT + timedelta(hours=1))
    ops = FakeOps(comments=[_record(proposal), late], lanes=_lanes(proposal))
    with pytest.raises(groomer.NotApproved) as exc:
        groomer.drain(ops, card=PROPOSAL_CARD)
    assert "stale" in str(exc.value)
    assert ops.state_writes == []


def test_a_replayed_approval_cannot_outrank_a_later_decline():
    """The CEO approved, then declined. The fleet re-posts the approval comment
    verbatim, inside the time window. A receipt is honoured once — at the first
    comment that carries it — so the copy decides nothing and the decline
    stands."""
    proposal = _proposal()
    pid = groomer.proposal_id(proposal)
    approval = _approval(proposal)
    decline = receipted(_marker(groomer.DECLINE_TAG, proposal,
                                reason="changed my mind"), proposal=pid,
                        at=SIGNED_AT + timedelta(minutes=2))
    replay = dict(approval, created_at=_linear_stamp(
        SIGNED_AT + timedelta(minutes=3)))
    ops = FakeOps(comments=[_record(proposal), approval, decline, replay],
                  lanes=_lanes(proposal))
    with pytest.raises(groomer.NotApproved) as exc:
        groomer.drain(ops, card=PROPOSAL_CARD)
    assert "changed my mind" in str(exc.value)
    assert ops.state_writes == []


def test_an_unreachable_key_endpoint_is_refused_not_accepted(monkeypatch):
    """The real fetch path, with the network down."""
    def down(request, timeout=None):
        raise urllib.error.URLError("connection refused")
    monkeypatch.setattr(console_receipt, "urlopen", down)
    monkeypatch.setattr(groomer, "_VERIFIER", None)
    proposal = _proposal()
    ops = FakeOps(comments=[_record(proposal), _approval(proposal)],
                  lanes=_lanes(proposal))
    with pytest.raises(groomer.NotApproved) as exc:
        groomer.drain(ops, card=PROPOSAL_CARD)
    assert ops.state_writes == []
    assert console_receipt.KEY_URL in str(exc.value)
    assert "connection refused" in _sole_record(ops)


def test_a_refused_receipt_on_a_per_card_decision_is_named_on_that_cards_row():
    proposal = _proposal()
    batch = _batch_ids(proposal)
    pid = groomer.proposal_id(proposal)
    stale = receipted(_marker(groomer.EXCLUDE_TAG, proposal, card=batch[0]),
                      proposal=pid, posted=SIGNED_AT + timedelta(hours=3))
    ops = FakeOps(comments=[_record(proposal), _approval(proposal), stale],
                  lanes=_lanes(proposal))
    result = groomer.drain(ops, card=PROPOSAL_CARD)
    assert batch[0] in result["moved"], "a stale exclusion held a card back"
    row = _row(_sole_record(ops), batch[0])
    assert "stale" in row


def test_a_receipted_repo_hold_signed_under_a_proposal_is_refused():
    """A repo switch names no batch; its receipt names `-`. One signed under a
    proposal id is a receipt made for something else."""
    hold = receipted(f"{groomer.MARK} {groomer.REPO_HOLD_TAG}: atlas",
                     proposal=console_receipt.NO_PROPOSAL,
                     sign_proposal="2b10ecfb36f6")
    assert groomer.held_repos(groomer.vouch([hold], card=PROPOSAL_CARD)) == []


def test_unvouched_records_refuse_every_receipted_marker():
    """Fail closed at the seam: a reader handed the raw thread — nobody checked
    the receipts — reads a fleet marker as the fleet's, whatever it carries."""
    proposal = _proposal()
    assert groomer.read_decisions([_record(proposal), _approval(proposal)])[
        "approved"] is None


# --------------------------------------------------------------------------
# unchanged: the CEO's own Linear user
# --------------------------------------------------------------------------
def test_the_ceos_own_marker_is_still_accepted_and_no_key_is_fetched(_console_key):
    proposal = _proposal()
    own = _decision(groomer.APPROVAL_TAG, proposal)
    ops = FakeOps(comments=[_record(proposal), own], lanes=_lanes(proposal))
    assert groomer.drain(ops, card=PROPOSAL_CARD)["moved"] == _batch_ids(proposal)
    assert _console_key == []


def test_the_ceos_own_marker_is_accepted_while_the_console_is_down(monkeypatch):
    def down(request, timeout=None):
        raise AssertionError("the drain fetched a key for a marker with no receipt")
    monkeypatch.setattr(console_receipt, "urlopen", down)
    monkeypatch.setattr(groomer, "_VERIFIER", None)
    proposal = _proposal()
    ops = FakeOps(comments=[_record(proposal),
                            _decision(groomer.APPROVAL_TAG, proposal)],
                  lanes=_lanes(proposal))
    assert groomer.drain(ops, card=PROPOSAL_CARD)["moved"] == _batch_ids(proposal)


# --------------------------------------------------------------------------
# the thread read carries the time the receipt is held against
# --------------------------------------------------------------------------
def test_comment_records_carries_each_comments_own_linear_time():
    thread = {"viewer": {"id": "me"}, "issue": {"comments": {"nodes": [
        {"body": "newer", "createdAt": "2026-09-13T15:16:00.000Z",
         "user": {"id": "me"}},
        {"body": "older", "createdAt": "2026-09-13T15:15:03.412Z",
         "user": {"id": "ceo"}},
    ]}}}
    with patch.object(linear_ops, "gql", return_value=thread):
        rows = linear_ops.comment_records("DRE-1")
    assert [r["created_at"] for r in rows] == [
        "2026-09-13T15:15:03.412Z", "2026-09-13T15:16:00.000Z"]
    assert [r["authored_by_pipeline"] for r in rows] == [False, True]


def test_a_receipt_shaped_line_inside_a_reason_is_defanged():
    """A decline reason is re-rendered into the next proposal (DRE-3373). A
    receipt-shaped line inside it is defanged like every other marker."""
    reason = f"fine\n{V.TRAILER}"
    rendered, count = groomer.defang_reason(reason)
    assert count == 1
    assert "[defanged]" in rendered
