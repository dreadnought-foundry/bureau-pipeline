"""RED-first tests: the sweep promotes an OPERATOR/WORKBENCH card (DRE-3385).

THE HOLE. `config/routing-verdicts.json` says an OPERATOR or WORKBENCH card's
destination is `Todo` and that the operator handles it there. Nothing performed
that move: `promote_ready` refused every non-FLEET verdict, and `Backlog` admits
no human writer — so the cards sat in the one lane that leaves them alone, with
the destination written and no turn ever coming. Read live on 2026-09-08, 33 of
40 Backlog cards were proof/operator cards, most of them under an epic that was
already In Progress.

Moving them to `In Progress` by hand was not the answer either: every Todo /
In Progress / In Review card counted against `MAX_WIP`, so a person's queue
starves the fleet's — 19 such cards against agent-bureau's cap of 12 prints
"WIP at cap — none promoted" and no engineer card promotes again.

WHAT IS UNDER TEST, and every one of these fails before the fix:

  * a Backlog child whose epic is active and whose blockers are Done promotes
    to `Todo` when its verdict is WORKBENCH or OPERATOR, not only when it is
    FLEET;
  * the marks the verdict declares (`hand-built`, plus `no-code` for OPERATOR)
    are applied BEFORE the state move, so the card never sits in Todo unmarked
    — unmarked is exactly the state in which the nudge loop dispatches an agent
    at it;
  * the receipt names the person whose turn it is and says nothing was
    dispatched;
  * PARKED and NEEDS WORK stay refused, and a card carrying NO verdict at all
    is refused now too — for a child as well as a one-off;
  * the WIP count skips a card marked `hand-built` or `no-code`, in every lane
    it spans, and a hand-built promotion does not spend the promotion budget.

Run: cd bureau-pipeline && python3 -m pytest tests/test_operator_card_promotion.py -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")

import lane_contract  # noqa: E402
import linear_ops  # noqa: E402
import reconcile  # noqa: E402
import routing_verdict  # noqa: E402

GREEN_LIGHT = "2026-08-01T00:00:00.000Z"
CREATED = "2026-07-01T00:00:00.000Z"  # before the green light: not a mid-epic addition
EPIC = "DRE-3300"

# The verdict comments, built by the one writer that writes them on a real
# card — never hand-typed here, or this suite would pass against a vocabulary
# that had stopped saying what it says.
FLEET = routing_verdict.verdict_comment("FLEET", "the acceptance criteria are unit-testable")
WORKBENCH = routing_verdict.verdict_comment("WORKBENCH", "it drives a live auth flow")
OPERATOR = routing_verdict.verdict_comment("OPERATOR", "it is a deploy, not a diff")
PARKED = routing_verdict.verdict_comment("PARKED", "we decided not to build this")
NEEDS_WORK = routing_verdict.verdict_comment("NEEDS WORK", "it states no exit condition")


def _card(
    *,
    identifier: str = "DRE-3385",
    parent_state: str | None = "In Progress",
    comments=(),
    labels=("repo:bureau-pipeline", "agent:ops"),
):
    """A Backlog card as `backlog_children` hands it to the gate: under an
    active epic by default, no blocking relation, its verdict in its own
    comment window."""
    return {
        "id": f"uuid-{identifier}",
        "identifier": identifier,
        "title": "Rotate the relay's signing secret",
        "description": "Rotate the relay's signing secret and confirm a dispatch.",
        "createdAt": CREATED,
        "parent": (
            {"identifier": EPIC, "state": {"name": parent_state}}
            if parent_state
            else None
        ),
        "labels": {"nodes": [{"name": name} for name in labels]},
        "comments": {"nodes": [{"body": b} for b in comments]},
        "inverseRelations": {"nodes": []},
    }


class _Board:
    """`reconcile.promote_ready` over a Backlog roster with every gate that is
    not under test held open: WIP has room, the epic is green-lit, active and
    unblocked, and the second critic has passed.

    Copied in shape from tests/test_parentless_promotion.py — same sweep, same
    seams, so the three promotion suites read the same way. What it adds is the
    LABEL WRITES, recorded in the same ordered list as the state move: the
    thing this card has to get right is not only that the marks are applied but
    that they are applied FIRST.
    """

    def __init__(self, *cards, green_light=GREEN_LIGHT):
        self.cards = list(cards)
        self.green_light = green_light
        self.posted: list[tuple[str, str]] = []
        self.labelled: list[tuple[str, str]] = []
        #: every write, in the order it happened: ("label"|"advance", card, what)
        self.writes: list[tuple[str, str, str]] = []
        self.lanes = {c["identifier"]: ["Backlog"] for c in self.cards}

    def promote(self, active_count: int = 0) -> int:
        def advance(ident, to_state, from_states):
            self.writes.append(("advance", ident, to_state))
            self.lanes.setdefault(ident, []).append(to_state)

        def add_label(ident, label):
            self.labelled.append((ident, label))
            self.writes.append(("label", ident, label))

        with patch.object(reconcile, "REPO_SLUG", "bureau-pipeline"), patch.object(
            reconcile, "backlog_children", return_value=self.cards
        ), patch.object(
            reconcile, "epic_blockers_unmet", return_value=False
        ), patch.object(
            reconcile.mid_epic, "last_green_light", return_value=self.green_light
        ), patch.object(
            reconcile, "epic_thread", return_value=[]
        ), patch.object(
            reconcile.plan_critic, "promotion_refusal", return_value=None
        ), patch.object(
            reconcile, "card_state", return_value="Done"
        ), patch.object(
            reconcile.linear_ops, "cmd_advance", side_effect=advance
        ), patch.object(
            reconcile.linear_ops, "add_label", side_effect=add_label
        ), patch.object(
            reconcile.linear_ops, "cmd_comment",
            side_effect=lambda i, b: self.posted.append((i, b)),
        ), patch.object(
            reconcile.linear_ops, "count_comments",
            side_effect=lambda i, needle, **kw: sum(
                1 for pi, pb in self.posted if pi == i and needle in pb
            ),
        ):
            return reconcile.promote_ready(active_count=active_count)

    def lane_of(self, identifier: str) -> str:
        return self.lanes[identifier][-1]

    def comments_on(self, identifier: str) -> list[str]:
        return [b for i, b in self.posted if i == identifier]

    def labels_on(self, identifier: str) -> list[str]:
        return [l for i, l in self.labelled if i == identifier]

    def receipt_for(self, identifier: str) -> str:
        notes = [b for b in self.comments_on(identifier) if "Auto-promoted" in b]
        assert notes, f"{identifier} was never promoted: {self.posted}"
        return notes[0]


@pytest.fixture(autouse=True)
def _clear_write_failures():
    reconcile._write_failures.clear()
    yield
    reconcile._write_failures.clear()


# --------------------------------------------------------------------------
# 1: the move itself
# --------------------------------------------------------------------------
class TestTheSweepPerformsTheMove:
    def test_a_workbench_child_is_promoted_to_todo(self):
        """The headline. Active epic, blockers Done, WORKBENCH verdict — the
        destination the vocabulary already declared is finally reached."""
        board = _Board(_card(comments=[WORKBENCH]))
        assert board.promote() == 1
        assert board.lane_of("DRE-3385") == "Todo"

    def test_an_operator_child_is_promoted_to_todo(self):
        board = _Board(_card(comments=[OPERATOR]))
        assert board.promote() == 1
        assert board.lane_of("DRE-3385") == "Todo"

    def test_a_workbench_card_arrives_carrying_hand_built(self):
        board = _Board(_card(comments=[WORKBENCH]))
        board.promote()
        assert board.labels_on("DRE-3385") == list(routing_verdict.marks("WORKBENCH"))
        assert reconcile.HAND_BUILT_LABEL in board.labels_on("DRE-3385")

    def test_an_operator_card_arrives_carrying_no_code_as_well(self):
        board = _Board(_card(comments=[OPERATOR]))
        board.promote()
        assert board.labels_on("DRE-3385") == list(routing_verdict.marks("OPERATOR"))
        assert linear_ops.NO_CODE_LABEL in board.labels_on("DRE-3385")

    def test_the_marks_are_applied_before_the_state_move(self):
        """Order is the whole protection. A `hand-built` card is left alone by
        the nudge loop BECAUSE of the label; land it in Todo unmarked and the
        next sweep — fifteen minutes later — dispatches an agent at work a
        person is meant to do."""
        board = _Board(_card(comments=[OPERATOR]))
        board.promote()
        kinds = [kind for kind, ident, _ in board.writes if ident == "DRE-3385"]
        assert kinds == ["label", "label", "advance"], board.writes

    def test_a_card_that_already_carries_the_mark_is_not_relabelled(self):
        """`add_label` is idempotent and costs a Linear request to find out.
        The card's own labels came free with the candidates query."""
        board = _Board(_card(
            comments=[WORKBENCH],
            labels=("repo:bureau-pipeline", "agent:ops", reconcile.HAND_BUILT_LABEL),
        ))
        assert board.promote() == 1
        assert board.labels_on("DRE-3385") == []
        assert board.lane_of("DRE-3385") == "Todo"

    def test_nothing_is_dispatched_for_it(self):
        """The promoter never dispatches — the relay does, off the Todo
        transition — and the marks are what stop it there (DRE-3341). Asserted
        rather than assumed, because "promoted" and "dispatched" became one word
        in this gate once before."""
        with patch.object(reconcile, "redispatch") as dispatch:
            board = _Board(_card(comments=[OPERATOR]))
            assert board.promote() == 1
        dispatch.assert_not_called()

    def test_a_fleet_card_is_still_promoted_and_still_unmarked(self):
        """Control: the path this card extends must keep working, and FLEET
        declares no marks — stamping one would stop the fleet dispatching."""
        board = _Board(_card(comments=[FLEET]))
        assert board.promote() == 1
        assert board.lane_of("DRE-3385") == "Todo"
        assert board.labels_on("DRE-3385") == []


# --------------------------------------------------------------------------
# 2: the receipt says a person's turn has come, and that nothing ran
# --------------------------------------------------------------------------
class TestTheReceipt:
    def test_it_names_the_person_whose_turn_it_is(self):
        board = _Board(_card(comments=[WORKBENCH]))
        board.promote()
        assert routing_verdict.actor("WORKBENCH") in board.receipt_for("DRE-3385")

    def test_it_says_nothing_was_dispatched(self):
        board = _Board(_card(comments=[OPERATOR]))
        board.promote()
        receipt = board.receipt_for("DRE-3385")
        assert "your turn" in receipt
        assert "nothing was dispatched" in receipt

    def test_it_names_the_verdict_and_the_marks(self):
        board = _Board(_card(comments=[OPERATOR]))
        board.promote()
        receipt = board.receipt_for("DRE-3385")
        assert "OPERATOR" in receipt
        for mark in routing_verdict.marks("OPERATOR"):
            assert mark in receipt

    def test_a_fleet_receipt_does_not_claim_a_person_is_coming(self):
        board = _Board(_card(comments=[FLEET]))
        board.promote()
        receipt = board.receipt_for("DRE-3385")
        assert "your turn" not in receipt
        assert "nothing was dispatched" not in receipt


# --------------------------------------------------------------------------
# 3: what is still refused
# --------------------------------------------------------------------------
class TestWhatStaysRefused:
    def test_a_parked_child_is_not_promoted(self):
        board = _Board(_card(comments=[PARKED]))
        assert board.promote() == 0
        assert board.lane_of("DRE-3385") == "Backlog"
        assert board.labelled == []
        assert any(
            routing_verdict.NOT_FLEET_TAG in b for b in board.comments_on("DRE-3385")
        )

    def test_the_parked_refusal_names_the_reason(self):
        board = _Board(_card(comments=[PARKED]))
        board.promote()
        notice = board.comments_on("DRE-3385")[0]
        assert "PARKED" in notice
        assert routing_verdict.revival("PARKED").split(".")[0] in notice

    def test_a_needs_work_child_is_not_promoted(self):
        """NEEDS WORK routes back to Planning, which is the planner's move —
        the sweep promotes to Todo and nowhere else."""
        board = _Board(_card(comments=[NEEDS_WORK]))
        assert board.promote() == 0
        assert board.lane_of("DRE-3385") == "Backlog"

    def test_a_child_with_no_verdict_at_all_is_not_promoted(self):
        """The `:564-575` gap. A child used to promote on its epic's approval
        alone — so a card nobody had routed was handed to the fleet, which is
        how eight unmarked operator cards came to be one cleared blocker from
        an engineer dispatch."""
        board = _Board(_card(comments=[]))
        assert board.promote() == 0
        assert board.lane_of("DRE-3385") == "Backlog"
        assert any(
            routing_verdict.NO_VERDICT_TAG in b for b in board.comments_on("DRE-3385")
        )

    def test_that_refusal_says_the_epic_is_not_evidence_on_its_own(self):
        board = _Board(_card(comments=[]))
        board.promote()
        notice = board.comments_on("DRE-3385")[0]
        assert "epic" in notice.lower()
        assert "routing_verdict.py" in notice  # it names the way out

    def test_the_two_refusals_carry_different_tags(self):
        """The sweep posts each refusal at most once, keyed on its tag. Pair a
        notice with the wrong tag and the two silence each other."""
        no_verdict = routing_verdict.promotion_refusal("DRE-3385", [])
        parked = routing_verdict.promotion_refusal("DRE-3385", [PARKED])
        assert routing_verdict.refusal_tag(no_verdict) == routing_verdict.NO_VERDICT_TAG
        assert routing_verdict.refusal_tag(parked) == routing_verdict.NOT_FLEET_TAG

    def test_an_inactive_epic_still_holds_a_workbench_child(self):
        """Nothing about a person doing the work exempts the card from the epic
        gate: DRE-1893's reasoning is unchanged."""
        board = _Board(_card(parent_state="Backlog", comments=[WORKBENCH]))
        assert board.promote() == 0
        assert board.lane_of("DRE-3385") == "Backlog"

    def test_a_held_workbench_child_is_never_auto_promoted(self):
        board = _Board(_card(
            comments=[WORKBENCH],
            labels=("repo:bureau-pipeline", "agent:ops", reconcile.HOLD_LABEL),
        ))
        assert board.promote() == 0
        assert board.lane_of("DRE-3385") == "Backlog"


# --------------------------------------------------------------------------
# 4: the WIP cap stops counting work no run is coming for
# --------------------------------------------------------------------------
class TestTheWipCount:
    def _in_lane(self, lane: str, *labels: str, identifier: str = "DRE-1"):
        return {
            "identifier": identifier,
            "title": "work",
            "description": "**Repo:** bureau-pipeline\nwork",
            "state": {"name": lane},
            "children": {"nodes": []},
            "labels": {"nodes": [{"name": name} for name in labels]},
            "updatedAt": "2026-09-08T00:00:00Z",
        }

    @pytest.mark.parametrize("lane", reconcile.SWEEP_STATES)
    def test_a_hand_built_card_does_not_count_in_any_lane_it_spans(self, lane):
        assert not reconcile.counts_against_wip(
            self._in_lane(lane, reconcile.HAND_BUILT_LABEL)
        )

    @pytest.mark.parametrize("lane", reconcile.SWEEP_STATES)
    def test_a_no_code_card_does_not_count_in_any_lane_it_spans(self, lane):
        assert not reconcile.counts_against_wip(
            self._in_lane(lane, linear_ops.NO_CODE_LABEL)
        )

    def test_an_ordinary_card_still_counts(self):
        """Control: the cap still caps. Removing it is how the pipeline floods."""
        assert reconcile.counts_against_wip(self._in_lane("In Progress"))
        assert reconcile.counts_against_wip(
            self._in_lane("In Progress", "agent:engineer")
        )

    def test_the_label_match_folds_case(self):
        """Linear labels are typed by humans; `held()` folds case and so does
        every other reader of a label in this module."""
        assert not reconcile.counts_against_wip(self._in_lane("Todo", "Hand-Built"))
        assert not reconcile.counts_against_wip(self._in_lane("Todo", "No-Code"))

    def test_a_near_miss_label_still_counts(self):
        """`no-codegen` is not `no-code` — the same exact-match rule the routing
        label map keeps, one field over."""
        assert reconcile.counts_against_wip(self._in_lane("Todo", "no-codegen"))
        assert reconcile.counts_against_wip(self._in_lane("Todo", "hand-built-ish"))

    def test_twelve_hand_built_todo_cards_do_not_starve_the_fleet(self):
        """The acceptance criterion, as a board and through the real read.
        Twelve hand-built cards in Todo at a cap of twelve used to print "WIP at
        cap — none promoted" for ever; the FLEET card behind them promotes."""
        occupied = [
            self._in_lane("Todo", reconcile.HAND_BUILT_LABEL, identifier=f"DRE-{n}")
            for n in range(100, 112)
        ]
        assert len(occupied) == 12
        with patch.object(reconcile, "active_cards", return_value=occupied):
            counted = reconcile.wip_count(reconcile.active_cards())
        assert counted == 0
        with patch.object(reconcile, "MAX_WIP", 12):
            promoted = _Board(_card(comments=[FLEET])).promote(active_count=counted)
        assert promoted == 1

    def test_the_same_twelve_without_the_mark_do_hold_the_fleet_at_the_cap(self):
        """Guard the guard: the exemption is the label, not the harness."""
        occupied = [
            self._in_lane("Todo", identifier=f"DRE-{n}") for n in range(100, 112)
        ]
        with patch.object(reconcile, "active_cards", return_value=occupied):
            counted = reconcile.wip_count(reconcile.active_cards())
        assert counted == 12
        with patch.object(reconcile, "MAX_WIP", 12):
            promoted = _Board(_card(comments=[FLEET])).promote(active_count=counted)
        assert promoted == 0

    def test_the_sweep_budgets_promotion_off_the_filtered_count(self):
        """The count is not merely available — `main()` has to be the caller
        that uses it, or the fix is a function nobody reads."""
        cards = [
            self._in_lane("In Progress", identifier="DRE-1"),
            self._in_lane("Todo", reconcile.HAND_BUILT_LABEL, identifier="DRE-2"),
            self._in_lane("Todo", linear_ops.NO_CODE_LABEL, identifier="DRE-3"),
        ]
        mocks = {
            "active_cards": MagicMock(return_value=cards),
            "close_finished_epics": MagicMock(),
            "promote_ready": MagicMock(return_value=0),
        }
        with patch.object(reconcile, "REPO_SLUG", "bureau-pipeline"), \
                patch.multiple(reconcile, **mocks):
            reconcile.main(promote_only=True)
        mocks["promote_ready"].assert_called_once_with(active_count=1)

    def test_a_hand_built_promotion_does_not_spend_the_budget(self):
        """A Backlog full of operator cards must not eat the fleet's promotion
        budget on its way out — nothing is dispatched for any of them, so no
        slot under the cap is taken."""
        cards = [
            _card(identifier=f"DRE-{n}", comments=[OPERATOR])
            for n in range(3390, 3395)
        ]
        cards.append(_card(identifier="DRE-3399", comments=[FLEET]))
        with patch.object(reconcile, "MAX_WIP", 1):
            board = _Board(*cards)
            assert board.promote(active_count=0) == 6
        assert board.lane_of("DRE-3399") == "Todo"

    def test_the_fleet_budget_is_still_a_budget(self):
        """Control for the line above: FLEET cards still stop at the cap."""
        cards = [
            _card(identifier=f"DRE-{n}", comments=[FLEET])
            for n in range(3390, 3395)
        ]
        with patch.object(reconcile, "MAX_WIP", 2):
            board = _Board(*cards)
            assert board.promote(active_count=0) == 2


# --------------------------------------------------------------------------
# 5: the vocabulary and the contract say the same thing the code does
# --------------------------------------------------------------------------
class TestTheContractSaysSo:
    def test_the_sweep_promotes_exactly_the_verdicts_bound_for_todo(self):
        promoted = [
            name for name in routing_verdict.verdicts()
            if routing_verdict.sweep_promotes(name)
        ]
        assert promoted == ["FLEET", "WORKBENCH", "OPERATOR"]

    def test_every_dispatched_verdict_is_one_the_sweep_promotes(self):
        """`is_promotable` is the narrower question — may a RUN be dispatched —
        and a verdict the relay dispatches at a lane the sweep never moves a
        card into is a dispatch that never happens."""
        for name in routing_verdict.verdicts():
            if routing_verdict.is_promotable(name):
                assert routing_verdict.sweep_promotes(name)

    def test_the_promotion_lane_is_a_live_lane_the_promoter_may_write(self):
        assert routing_verdict.PROMOTION_LANE in lane_contract.lane_names(status="live")
        assert routing_verdict.PROMOTER in lane_contract.lane_writers(
            routing_verdict.PROMOTION_LANE
        )

    def test_the_vocabulary_still_checks_out(self):
        assert routing_verdict.config_problems() == []

    def test_the_backlog_exit_clause_says_the_sweep_performs_the_move(self):
        """The contract is DATA and the sweep reads it — a clause that still
        says "a human moves a WORKBENCH or OPERATOR card" describes a pipeline
        that no longer exists."""
        exit_text = lane_contract.lane("Backlog")["clauses"]["exit"]["text"]
        assert "WORKBENCH" in exit_text and "OPERATOR" in exit_text
        assert "a human moves a WORKBENCH or OPERATOR card" not in exit_text
        assert "sweep promotes" in exit_text

    def test_the_todo_writers_clause_names_what_the_promoter_stamps(self):
        writers = lane_contract.lane("Todo")["clauses"]["writers"]["text"]
        assert reconcile.HAND_BUILT_LABEL in writers

    def test_the_rendered_document_matches_the_contract(self):
        """`docs/lane-contract.md` is rendered from the file — a clause edited
        without re-rendering is a document that disagrees with enforcement."""
        with open(lane_contract.DOC_PATH, encoding="utf-8") as fh:
            assert fh.read() == lane_contract.render_markdown()
