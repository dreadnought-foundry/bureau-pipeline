"""The sweep carries a card held as an operator step to Hand-work (DRE-6427).

THE HOLE. `promote_ready` skipped a Backlog card wearing `needs-human` on the
bare label, before it read the card's verdict or its blockers. So an operator
card filed `needs-human` + `no-code` never reached the OPERATOR path that
already carries a card to Hand-work (DRE-5322). Read live on 2026-10-09, five
cards — DRE-5999, DRE-6161, DRE-5662, DRE-5109 and DRE-4672 — carried an
OPERATOR verdict and one Done blocker apiece, and sat in Backlog.

WHAT IS UNDER TEST, and every one of these fails before the fix:

  * the promotion gate asks the registry why the card is held
    (`hold.respects(..., "sweep")`, DRE-6182), so a card held `operator-step`
    (DRE-6426) goes on through the gates, and once its blockers are terminal
    it is lifted (`needs-human` off, the lift line) and carried to Hand-work,
    with nothing dispatched and no WIP slot spent;
  * a card held under any reason the sweep stands down for is skipped as
    before, with no write, and the skip line names the reason;
  * one pass over a `manual` card and an `operator-step` card with an open
    blocker completes — the skip site and the blockers message's local `held`
    in one call, which raises `UnboundLocalError` if the skip ever calls the
    module-level `held`;
  * an `operator-step` card routed anywhere but Hand-work (a FLEET verdict)
    is refused, and one with no verdict is refused as today;
  * the live re-check passes the held card the same way.

The registry is a fixture: the real one, plus one `operator-step` row naming
the readers `fix-dispatch`, `medic` and `limit-recovery` — as the real rows
will, since the sweep is this reason's lifter and never its reader.

Run: python3 -m pytest tests/test_operator_hold_promotion.py -v
"""
from __future__ import annotations

import copy
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")

import hold  # noqa: E402
import reconcile  # noqa: E402
import routing_verdict  # noqa: E402

LABEL = hold.HOLD_LABEL
CARD = "DRE-5999"
BLOCKER = "DRE-6426"
SHA = "d" * 40

OPERATOR = routing_verdict.verdict_comment("OPERATOR", "it is a deploy, not a diff")
FLEET = routing_verdict.verdict_comment("FLEET", "the acceptance criteria are unit-testable")
OPERATOR_STAMP = hold.stamp_line(hold.OPERATOR_STEP_REASON, "none", "scripts/planner.py")
LIFT_LINE = (f"{hold.LIFT_PREFIX} reason={hold.OPERATOR_STEP_REASON} "
             f"because={hold.BLOCKERS_TERMINAL} by=reconcile.py")
HELD_OPENER = f"promotion: {CARD} is held for a human ("
ROUTED_OPENER = f"promotion: {CARD} is held as an operator step but routed"

# The stamps of the reasons the sweep stands down for. `manual` is the label
# with no stamp at all.
STAND_DOWN = {
    "review-cap-spent": [hold.stamp_line("review-cap-spent", SHA, "scripts/merge_gate.py")],
    "dead-run-cap": [hold.stamp_line("dead-run-cap", "none", "scripts/reconcile.py")],
    "manual": [],
}


def _registry():
    """The real registry, plus the one `operator-step` row the real rows
    will carry: every reader but the sweep, which is its lifter."""
    doc = copy.deepcopy(hold.load())
    doc["sites"].append({
        "file": "scripts/planner.py",
        "scope": "<fixture>",
        "anchor": "operator step",
        "reasons": [{"reason": hold.OPERATOR_STEP_REASON}],
        "readers": ["fix-dispatch", "medic", "limit-recovery"],
    })
    return doc


@pytest.fixture(autouse=True)
def _fixture_registry():
    doc = _registry()
    with patch.object(hold, "load", lambda path=None: doc):
        yield


@pytest.fixture(autouse=True)
def _clear_write_failures():
    reconcile._write_failures.clear()
    yield
    reconcile._write_failures.clear()


def _card(
    identifier: str = CARD,
    *,
    verdict: str | None = OPERATOR,
    stamp: list[str] | None = None,
    blockers: dict[str, str] | None = None,
    labels=("repo:bureau-pipeline", LABEL, "no-code", "operator-step"),
):
    """An operator card in Backlog as `backlog_children` hands it to the
    gate: parentless, its verdict and its stamp in its own comment window
    (Linear's order, newest first), and its blockers as relations."""
    bodies = ([verdict] if verdict else []) + (
        [OPERATOR_STAMP] if stamp is None else list(stamp))
    blockers = {BLOCKER: "Done"} if blockers is None else blockers
    return {
        "id": f"uuid-{identifier}",
        "identifier": identifier,
        "title": "Rotate the relay's signing secret",
        "description": "Rotate the relay's signing secret and confirm a dispatch.",
        "createdAt": "2026-10-01T00:00:00.000Z",
        "parent": None,
        "labels": {"nodes": [{"name": name} for name in labels]},
        "comments": {"nodes": [{"body": b} for b in reversed(bodies)]},
        "inverseRelations": {"nodes": [
            {"type": "blocks", "issue": {"identifier": ident, "state": {"name": state}}}
            for ident, state in blockers.items()
        ]},
    }


class _Board:
    """`reconcile.promote_ready` over a Backlog roster, every write recorded
    in one ordered list: ("label"|"unlabel"|"advance"|"comment", card, what)."""

    def __init__(self, *cards):
        self.cards = list(cards)
        self.writes: list[tuple[str, str, str]] = []

    def promote(self, active_count: int = 0, *, door: set[str] | None = None) -> int:
        def record(kind):
            return lambda ident, what, *rest: self.writes.append((kind, ident, what))

        live = {c["identifier"]: copy.deepcopy(c) for c in self.cards}
        with patch.object(reconcile, "REPO_SLUG", "bureau-pipeline"), patch.object(
            reconcile, "backlog_children", return_value=self.cards
        ), patch.object(
            reconcile, "epic_blockers_unmet", return_value=False
        ), patch.object(
            reconcile.routing_verdict, "lane_moves", return_value=[]
        ), patch.object(
            reconcile, "_door_sourced", set(door or ())
        ), patch.object(
            reconcile, "_fetch_backlog_linear",
            side_effect=lambda ids: [live[i] for i in ids if i in live],
        ), patch.object(
            reconcile.linear_ops, "cmd_advance",
            side_effect=lambda ident, to, frm: self.writes.append(("advance", ident, to)),
        ), patch.object(
            reconcile.linear_ops, "add_label", side_effect=record("label")
        ), patch.object(
            reconcile.linear_ops, "remove_label", side_effect=record("unlabel")
        ), patch.object(
            reconcile.linear_ops, "cmd_comment", side_effect=record("comment")
        ), patch.object(
            reconcile.linear_ops, "count_comments",
            side_effect=lambda i, needle, **kw: sum(
                1 for k, ci, body in self.writes
                if k == "comment" and ci == i and needle in body),
        ):
            return reconcile.promote_ready(active_count=active_count)

    def on(self, identifier: str = CARD) -> list[tuple[str, str]]:
        return [(k, what) for k, ci, what in self.writes if ci == identifier]

    def comments_on(self, identifier: str = CARD) -> list[str]:
        return [what for k, what in self.on(identifier) if k == "comment"]


def _lines(out: str, opener: str) -> list[str]:
    return [line for line in out.splitlines() if line.startswith(opener)]


# --------------------------------------------------------------------------- #
# 1. the held operator card, its blockers Done, goes to Hand-work             #
# --------------------------------------------------------------------------- #


class TestTheHeldOperatorCardIsCarried:
    def test_one_pass_lifts_the_hold_and_moves_it_to_hand_work(self, capsys):
        board = _Board(_card())
        assert board.promote() == 1
        writes = board.on()
        # The lift, then the move, then the receipt — in that order.
        unlabel = writes.index(("unlabel", LABEL))
        lifted = writes.index(("comment", LIFT_LINE))
        advance = writes.index(("advance", "Hand-work"))
        receipts = [i for i, (k, w) in enumerate(writes)
                    if k == "comment" and w.startswith("🧹 Auto-promoted Backlog → Hand-work")]
        assert len(receipts) == 1
        assert unlabel < lifted < advance < receipts[0]
        assert routing_verdict.destination("OPERATOR") == "Hand-work"
        out = capsys.readouterr().out
        assert not _lines(out, HELD_OPENER)

    def test_the_lift_line_is_the_registry_s_own(self):
        assert LIFT_LINE == hold.lift_line(
            hold.OPERATOR_STEP_REASON, hold.BLOCKERS_TERMINAL, "reconcile.py")

    def test_the_receipt_says_the_hold_was_lifted(self):
        board = _Board(_card())
        board.promote()
        receipt = next(c for c in board.comments_on() if c.startswith("🧹 Auto-promoted"))
        assert LABEL in receipt and "lifted" in receipt
        assert "nothing was dispatched" in receipt

    def test_nothing_is_dispatched_and_no_slot_is_spent(self, capsys):
        """At the cap the card still moves: it takes no slot, it is counted
        as hand-built, and nothing is written to Todo."""
        board = _Board(_card())
        assert board.promote(active_count=reconcile.MAX_WIP) == 1
        assert ("advance", "Todo") not in board.on()
        out = capsys.readouterr().out
        assert ("promotion: 1 card(s) promoted, 1 parentless one-off(s), "
                f"1 hand-built (nothing dispatched) "
                f"(WIP {reconcile.MAX_WIP}+0/{reconcile.MAX_WIP})") in out

    def test_the_marks_go_on_before_the_lift(self):
        """A card arriving without its marks gets them first, as today."""
        board = _Board(_card(labels=("repo:bureau-pipeline", LABEL)))
        board.promote()
        writes = board.on()
        marks = [writes.index(("label", m))
                 for m in routing_verdict.card_marks("OPERATOR", "Rotate the relay's signing secret")]
        assert marks, "OPERATOR declares no marks — the order pins nothing"
        assert max(marks) < writes.index(("unlabel", LABEL))

    def test_the_live_re_check_passes_the_held_card(self, capsys):
        """A door-sourced candidate is read again live, and the live read's
        hold question is the registry's too, so it passes the same card."""
        board = _Board(_card())
        assert board.promote(door={CARD}) == 1
        assert ("advance", "Hand-work") in board.on()
        assert "the live re-check refused it" not in capsys.readouterr().out

    def test_the_sweep_never_asks_lift_due(self):
        with patch.object(hold, "lift_due", side_effect=AssertionError("lift_due")):
            assert _Board(_card()).promote() == 1


# --------------------------------------------------------------------------- #
# 2. a blocker still open holds it, and says which                            #
# --------------------------------------------------------------------------- #


class TestAnOpenBlockerHoldsIt:
    def test_it_stays_put_and_the_log_names_the_blocker(self, capsys):
        board = _Board(_card(blockers={BLOCKER: "Done", "DRE-6500": "Backlog"}))
        assert board.promote() == 0
        assert board.on() == []
        out = capsys.readouterr().out
        assert f"promotion: {CARD} is held by DRE-6500 (Backlog)" in out
        assert not _lines(out, HELD_OPENER)

    def test_a_manual_and_an_operator_step_card_in_one_pass(self, capsys):
        """The skip site and the blockers message's local `held` in the same
        call: a skip that called the module-level `held` raises
        UnboundLocalError here."""
        manual = _card("DRE-6001", stamp=[])
        waiting = _card("DRE-6002", blockers={"DRE-6500": "Backlog"})
        board = _Board(manual, waiting)
        assert board.promote() == 0
        assert board.writes == []
        out = capsys.readouterr().out
        assert (f"promotion: DRE-6001 is held for a human ('{LABEL}' label, "
                "reason=manual)") in out
        assert "promotion: DRE-6002 is held by DRE-6500 (Backlog)" in out


# --------------------------------------------------------------------------- #
# 3. a reason the sweep stands down for is skipped as today                   #
# --------------------------------------------------------------------------- #


class TestTheSweepStandsDownForEveryOtherReason:
    @pytest.mark.parametrize("reason", sorted(STAND_DOWN))
    def test_skipped_with_no_write_and_the_reason_named(self, capsys, reason):
        board = _Board(_card(stamp=STAND_DOWN[reason]))
        assert board.promote() == 0
        assert board.writes == []
        out = capsys.readouterr().out
        assert _lines(out, HELD_OPENER) == [
            f"promotion: {CARD} is held for a human ('{LABEL}' label, "
            f"reason={reason}) — never auto-promoted; skipping"
        ]


# --------------------------------------------------------------------------- #
# 4. held as an operator step but routed elsewhere, or not routed at all      #
# --------------------------------------------------------------------------- #


class TestAHeldCardRoutedElsewhereIsRefused:
    def test_a_fleet_verdict_is_neither_promoted_nor_dispatched(self, capsys):
        board = _Board(_card(verdict=FLEET))
        assert board.promote() == 0
        assert board.writes == []
        out = capsys.readouterr().out
        assert _lines(out, ROUTED_OPENER) == [
            f"{ROUTED_OPENER} FLEET → {routing_verdict.destination('FLEET')}; "
            "a person reads it — skipping"
        ]

    def test_no_verdict_is_refused_as_today(self, capsys):
        board = _Board(_card(verdict=None))
        assert board.promote() == 0
        assert not [w for w in board.on() if w[0] in ("advance", "unlabel", "label")]
        assert LIFT_LINE not in board.comments_on()
        out = capsys.readouterr().out
        assert f"promotion: {CARD} is not being promoted — " in out
        assert not _lines(out, ROUTED_OPENER)
