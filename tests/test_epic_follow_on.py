"""RED-first: an epic's follow-on cards keep moving after it closes (DRE-7948).

An epic now closes on the sweep pass after its last build card is Done, while
its PROOF card and an after-merge OPERATOR card are still open
(`tests/test_close_epics.py`, the DRE-7912 replay). Two readers refused every
child of an epic that was not In Progress, and either would strand those cards
under the closed epic:

  * the promotion gate (`promote_ready`) — at the parent check, in the live
    re-check, and in the pass's gated-epics read the green light comes off;
  * the proof dispatch (`proof_dispatch`, condition 2).

Both now take a Done epic's FOLLOW-ON child — `epic_cap.follow_on`: open, not
an epic, and not `buildable()`, so a PROOF card or a card carrying a person's
mark. A buildable child of a Done epic is still refused, naming the epic's
state, and every other gate reads the card exactly as before.

Run: cd bureau-pipeline && python3 -m pytest tests/test_epic_follow_on.py -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")

import epic_cap  # noqa: E402
import linear_ops  # noqa: E402
import reconcile  # noqa: E402
import routing_verdict  # noqa: E402
import test_proof_dispatch as pd  # noqa: E402

AT = "2026-10-24T08:00:00.000Z"
EPIC = "DRE-7912"
PROOF_CARD, OPERATOR_CARD, BUILD_CARD = "DRE-7919", "DRE-7917", "DRE-7920"
OPERATOR = routing_verdict.verdict_comment("OPERATOR", "the proof is observed live")
FLEET = routing_verdict.verdict_comment("FLEET", "the acceptance criteria are unit-testable")


@pytest.fixture(autouse=True)
def _fresh_pass(monkeypatch):
    monkeypatch.setattr(reconcile, "REPO_SLUG", "bureau-pipeline")
    reconcile._write_failures.clear()
    reconcile.reset_sweep_cards()
    yield
    reconcile.reset_sweep_cards()
    reconcile._write_failures.clear()


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------
def _card(identifier, *, title, comments, labels=(), epic_state="Done", blocked_by=()):
    """A Queued child as `backlog_children` hands it to the gate."""
    return {
        "id": f"uuid-{identifier}",
        "identifier": identifier,
        "title": title,
        "description": "**Repo:** bureau-pipeline\nwork",
        "createdAt": AT,
        "priority": 3,
        "parent": {"identifier": EPIC, "state": {"name": epic_state}},
        "children": {"nodes": []},
        "labels": {"nodes": [{"name": n} for n in ("repo:bureau-pipeline", *labels)]},
        "comments": {"nodes": [{"body": b} for b in comments]},
        "inverseRelations": {"nodes": [
            {"type": "blocks", "issue": {"identifier": i, "state": {"name": s}}}
            for i, s in blocked_by
        ]},
    }


def _proof(**kw):
    return _card(PROOF_CARD, title=f"PROOF: {EPIC} observed live",
                 comments=[OPERATOR], labels=("no-code",), **kw)


def _build(**kw):
    return _card(BUILD_CARD, title="bureau-pipeline: one more build",
                 comments=[FLEET], **kw)


def _epic_record(state="Done"):
    """The Done epic as the pass's record carries it (`EPIC_RECORD_GQL`)."""
    return {
        "identifier": EPIC,
        "description": "**Repo:** bureau-pipeline\nan epic",
        "state": {"name": state},
        "children": {"nodes": [
            {"identifier": i, "createdAt": AT, "state": {"name": s}}
            for i, s in (("DRE-7913", "Done"), (OPERATOR_CARD, "Hand-work"),
                         (PROOF_CARD, "Queued"))
        ]},
        "history": {"nodes": []},
        "inverseRelations": {"nodes": []},
    }


class FakeLinear:
    def __init__(self, state="Done"):
        self.state = state
        self.record_reads: list = []

    def gql(self, query, variables=None):
        if "$numbers" in query:
            self.record_reads.append(sorted((variables or {}).get("numbers") or ()))
            return {"issues": {"nodes": [_epic_record(self.state)],
                               "pageInfo": {"hasNextPage": False, "endCursor": None}}}
        raise linear_ops.LinearError(f"FakeLinear: unexpected {' '.join(query.split())[:80]}")


def _sweep(*cards, state="Done"):
    """One promotion pass; (moves, labels added, the gated-epics set)."""
    fake = FakeLinear(state)
    activated = [{"body": f"{epic_cap.ACTIVATED_NOTE} (3 children)",
                  "authored_by_pipeline": True, "created_at": None}]
    gated: list = []
    real_threads = reconcile._door_epic_threads
    with patch.object(linear_ops, "gql", side_effect=fake.gql), \
            patch.object(reconcile, "backlog_children", return_value=list(cards)), \
            patch.object(reconcile, "epic_thread", return_value=activated), \
            patch.object(reconcile, "_door_epic_threads",
                         side_effect=lambda e: (gated.append(set(e)), real_threads(e))[1]), \
            patch.object(linear_ops, "cmd_advance") as advance, \
            patch.object(linear_ops, "add_label") as add_label, \
            patch.object(linear_ops, "cmd_comment"), \
            patch.object(routing_verdict, "lane_moves", return_value=[]):
        reconcile.promote_ready(active_count=0)
    moves = [(c.args[0], c.args[1]) for c in advance.call_args_list]
    labels = [(c.args[0], c.args[1]) for c in add_label.call_args_list]
    return moves, labels, gated


# --------------------------------------------------------------------------
# the promotion gate
# --------------------------------------------------------------------------
def test_a_queued_proof_under_a_done_epic_is_promoted_once_its_blockers_are_terminal(capsys):
    moves, labels, _ = _sweep(_proof(blocked_by=(("DRE-7913", "Done"),)))
    out = capsys.readouterr().out
    assert moves == [(PROOF_CARD, routing_verdict.destination("OPERATOR"))], out
    assert "not active" not in out


def test_a_queued_proof_under_a_done_epic_still_waits_on_an_open_blocker(capsys):
    """Its `blockedBy` relations are read exactly as before."""
    moves, _, _ = _sweep(_proof(blocked_by=(("DRE-7916", "In Review"),)))
    out = capsys.readouterr().out
    assert moves == [], out
    assert f"promotion: {PROOF_CARD} is held by DRE-7916 (In Review)" in out, out


def test_an_operator_step_card_under_a_done_epic_is_follow_on_work(capsys):
    card = _card(OPERATOR_CARD, title="bureau-pipeline: rotate the signing secret",
                 comments=[OPERATOR], labels=("operator-step", "no-code"))
    moves, _, _ = _sweep(card)
    assert moves == [(OPERATOR_CARD, routing_verdict.destination("OPERATOR"))], (
        capsys.readouterr().out)


def test_a_buildable_queued_card_under_a_done_epic_is_refused_naming_its_state(capsys):
    moves, _, gated = _sweep(_build())
    out = capsys.readouterr().out
    assert moves == []
    assert f"promotion: {BUILD_CARD}'s epic {EPIC} is not active (Done) — skipping" in out
    assert all(EPIC not in g for g in gated), "a refused child buys no epic read"


@pytest.mark.parametrize("lane", ["Planning", "Green Light", "Canceled"])
def test_a_follow_on_child_of_an_epic_neither_active_nor_done_is_refused(capsys, lane):
    moves, _, _ = _sweep(_proof(epic_state=lane), state=lane)
    out = capsys.readouterr().out
    assert moves == []
    assert f"is not active ({lane})" in out


def test_the_gated_epics_read_takes_the_done_parent_of_a_follow_on_child():
    """The green light the gate reads comes off the pass's epic record, read
    once before the loop for every parent the gate will ask about."""
    _, _, gated = _sweep(_proof())
    assert gated and EPIC in gated[0], gated


def test_the_live_recheck_lets_a_follow_on_child_of_a_done_epic_through():
    card = _proof()
    with patch.object(reconcile, "_fetch_backlog_linear", return_value=[_proof()]):
        live, why = reconcile.live_promotion_refusal(card, reconcile.card_comment_bodies(card))
    assert why is None, why
    assert live is not None


def test_the_live_recheck_refuses_a_buildable_child_of_a_done_epic():
    card = _build()
    with patch.object(reconcile, "_fetch_backlog_linear", return_value=[_build()]):
        _, why = reconcile.live_promotion_refusal(card, reconcile.card_comment_bodies(card))
    assert why is not None and f"its epic {EPIC} is Done now, not active" in why


def test_the_live_recheck_still_refuses_an_epic_sent_back():
    card = _proof()
    with patch.object(reconcile, "_fetch_backlog_linear",
                      return_value=[_proof(epic_state="Green Light")]):
        _, why = reconcile.live_promotion_refusal(card, reconcile.card_comment_bodies(card))
    assert why is not None and "Green Light" in why


# --------------------------------------------------------------------------
# the proof dispatch, condition 2
# --------------------------------------------------------------------------
def test_condition_2_passes_for_a_proof_whose_epic_is_done(monkeypatch, capsys):
    board = pd.Board(hand=[pd.lane_card("DRE-5930")],
                     details={"DRE-5930": pd.detail(epic_state="Done")})
    h = pd.Harness(monkeypatch, board)
    h.sweep()
    lines = pd._about(pd._lines(capsys), "DRE-5930")
    assert not any("condition 2" in line for line in lines), lines
    assert h.fired == [("DRE-5930", pd.REPO, "first proof run", "proof-execute")]


@pytest.mark.parametrize("lane", ["Planning", "Green Light"])
def test_condition_2_still_refuses_a_proof_whose_epic_is_unapproved(monkeypatch, capsys, lane):
    board = pd.Board(hand=[pd.lane_card("DRE-5930")],
                     details={"DRE-5930": pd.detail(epic_state=lane)})
    h = pd.Harness(monkeypatch, board)
    h.sweep()
    lines = pd._about(pd._lines(capsys), "DRE-5930")
    assert h.fired == []
    assert any("condition 2" in line and lane in line for line in lines), lines


def test_condition_3_still_holds_a_done_epics_proof_on_an_open_blocker(monkeypatch, capsys):
    board = pd.Board(hand=[pd.lane_card("DRE-5930")],
                     details={"DRE-5930": pd.detail(epic_state="Done",
                                                    blockers={"DRE-5921": "In Review"})})
    h = pd.Harness(monkeypatch, board)
    h.sweep()
    lines = pd._about(pd._lines(capsys), "DRE-5930")
    assert h.fired == []
    assert any("condition 3" in line for line in lines), lines
