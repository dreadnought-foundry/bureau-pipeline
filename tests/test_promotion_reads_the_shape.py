"""The sweep decides epic-ness from the SHAPE, never from `agent:planner`
(DRE-3044).

`promote_ready()` opened with a label test — "carries `agent:planner` — epics
are promoted by humans" — and that label is the one the relay REQUIRES before it
will dispatch the planner at all. So every card that went through the front door
came out of Planning wearing it, and every one-off among them was
un-promotable: the label it needed to get classified was the label that stopped
it being built. Observed on the demo repo's sweep (run 33828168589, 2026-09-03),
which skipped DRE-3018 and DRE-3020 as epics on every pass while both sat in
Backlog stamped `one-off`.

DRE-3038 fixed exactly this reading in `mid_epic.is_epic()` and used it from
`routing_verdict.route()`. `reconcile.promote_ready()` kept a SECOND spelling of
the same test and DRE-3038 never touched it. Two readers, one rule, one fixed —
this is the other one.

WHAT THIS PINS:

  * a card stamped `one-off` promotes whatever labels it wears, and its
    parentless gate (DRE-2735) is the thing that decides — the verdict, read off
    the card;
  * a card stamped `epic` is still skipped, with the human-promotes message;
  * a card nothing has stamped keeps today's answer, because `is_epic()` falls
    back to the two facts `validate_card.infer_agent_label` derives
    `agent:planner` FROM — `[EPIC]` in the title, or any children at all;
  * `promote_ready` does not read the label at all. Asserted over its source, so
    a second spelling cannot come back quietly.

Run: cd bureau-pipeline && python3 -m pytest tests/test_promotion_reads_the_shape.py -v
"""
from __future__ import annotations

import inspect
import os
import re
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")

import planning_shape  # noqa: E402
import reconcile  # noqa: E402
import routing_verdict  # noqa: E402

CREATED = "2026-07-01T00:00:00.000Z"

FLEET = routing_verdict.verdict_comment("FLEET", "the acceptance criteria are unit-testable")
WORKBENCH = routing_verdict.verdict_comment("WORKBENCH", "it drives a live auth flow")

# The stamps, built by the writer that stamps them — hand-writing the comment
# here would let the vocabulary drift without this file noticing.
ONE_OFF_STAMP = planning_shape.shape_comment("one-off", "one card, one pull request")
EPIC_STAMP = planning_shape.shape_comment("epic", "a decomposition the planner owns")

# The label at the heart of the defect: the relay requires it before it will
# dispatch the planner, so EVERY card off the front door wears it.
PLANNER = "agent:planner"


def _card(
    *,
    identifier: str = "DRE-3018",
    title: str = "PROOF FD-4b: a one-off card in planning with agent:planner",
    labels=("repo:bureau-pipeline", PLANNER),
    comments=(),
    children: int = 0,
    parent_state: str | None = None,
):
    """A Backlog card with every gate but the one under test held open.

    Defaults to the shape of the card that was observed stuck: parentless,
    wearing `agent:planner`, waiting on the sweep.
    """
    return {
        "id": f"uuid-{identifier}",
        "identifier": identifier,
        "title": title,
        "description": "Route the card and record what the sweep did with it.",
        "createdAt": CREATED,
        "parent": (
            None
            if parent_state is None
            else {"identifier": "DRE-3000", "state": {"name": parent_state}}
        ),
        "labels": {"nodes": [{"name": name} for name in labels]},
        "comments": {"nodes": [{"body": b} for b in comments]},
        "children": {"nodes": [{"id": f"kid-{n}"} for n in range(children)]},
        "inverseRelations": {"nodes": []},
    }


class _Board:
    """`reconcile.promote_ready` over a Backlog roster, with every gate that is
    not under test held open."""

    def __init__(self, *cards):
        self.cards = list(cards)
        self.advanced: list[tuple[str, str, str]] = []
        self.posted: list[tuple[str, str]] = []
        self.lanes = {c["identifier"]: ["Backlog"] for c in self.cards}

    def promote(self, active_count: int = 0) -> int:
        def advance(ident, to_state, from_states):
            self.advanced.append((ident, to_state, from_states))
            self.lanes.setdefault(ident, []).append(to_state)

        with patch.object(reconcile, "REPO_SLUG", "bureau-pipeline"), patch.object(
            reconcile, "backlog_children", return_value=self.cards
        ), patch.object(
            reconcile, "epic_blockers_unmet", return_value=False
        ), patch.object(
            reconcile.mid_epic, "last_green_light", return_value=None
        ), patch.object(
            reconcile, "card_state", return_value="Done"
        ), patch.object(
            reconcile.linear_ops, "cmd_advance", side_effect=advance
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


@pytest.fixture(autouse=True)
def _clear_write_failures():
    reconcile._write_failures.clear()
    reconcile._card_skips.clear()
    yield
    reconcile._write_failures.clear()
    reconcile._card_skips.clear()


def _lines_naming(capsys, identifier):
    return [ln for ln in capsys.readouterr().out.splitlines() if identifier in ln]


# ===========================================================================
# 1: the observed card — stamped one-off, wearing agent:planner, FLEET
# ===========================================================================
class TestTheOneOffIsPromoted:
    def test_a_one_off_wearing_agent_planner_is_promoted(self):
        """DRE-3018 itself: `one-off`, FLEET, parentless, `agent:planner`. The
        label it needed to get classified must not be the label that stops it
        being built."""
        board = _Board(_card(comments=[ONE_OFF_STAMP, FLEET]))
        assert board.promote() == 1
        assert board.lane_of("DRE-3018") == "Todo"
        assert board.advanced == [("DRE-3018", "Todo", "Backlog")]

    def test_the_receipt_says_a_one_off_was_promoted(self):
        board = _Board(_card(comments=[ONE_OFF_STAMP, FLEET]))
        board.promote()
        receipt = board.comments_on("DRE-3018")
        assert len(receipt) == 1
        assert "no parent epic" in receipt[0]

    def test_the_sweep_counts_it_as_a_parentless_one_off(self, capsys):
        """The line the run log was reporting `0 parentless one-off(s)` on."""
        board = _Board(_card(comments=[ONE_OFF_STAMP, FLEET]))
        assert board.promote() == 1
        summary = [ln for ln in capsys.readouterr().out.splitlines() if "promoted" in ln]
        assert any("1 parentless one-off" in ln for ln in summary), summary


# ===========================================================================
# 2: the parentless gate is what decides a one-off — read off the card
# ===========================================================================
class TestTheParentlessGateStillDecides:
    def test_a_one_off_with_no_verdict_is_refused_for_the_verdict_not_the_label(self, capsys):
        """A one-off inherits no epic's approval, so its verdict IS the approval
        (DRE-2735). Refused — but for the RIGHT fact: "routed nowhere" and "this
        is an epic" are different facts with different next actions."""
        board = _Board(_card(comments=[ONE_OFF_STAMP]))
        assert board.promote() == 0
        assert board.lane_of("DRE-3018") == "Backlog"
        held = _lines_naming(capsys, "DRE-3018")
        assert len(held) == 1, f"expected exactly one line, got {held}"
        assert "epics are promoted by humans" not in held[0]
        assert routing_verdict.NO_VERDICT_TAG in held[0]
        assert any(
            routing_verdict.NO_VERDICT_TAG in b for b in board.comments_on("DRE-3018")
        )

    def test_a_one_off_routed_workbench_is_refused_as_workbench(self, capsys):
        """The wrong-destination refusal, reached through the same path: a
        person builds this one, and the card says so."""
        board = _Board(_card(comments=[ONE_OFF_STAMP, WORKBENCH]))
        assert board.promote() == 0
        held = _lines_naming(capsys, "DRE-3018")
        assert len(held) == 1, f"expected exactly one line, got {held}"
        assert routing_verdict.NOT_FLEET_TAG in held[0]
        assert "WORKBENCH" in held[0]


# ===========================================================================
# 3: an epic is still skipped, with the human-promotes message
# ===========================================================================
class TestAnEpicIsStillSkipped:
    def test_a_card_stamped_epic_is_skipped_with_the_human_promotes_message(self, capsys):
        """AC2. The fixture wears `agent:planner`, exactly as a real epic does —
        what skips it now is the stamp, and the message is the one that was
        always printed."""
        board = _Board(
            _card(identifier="DRE-3019", title="the front door",
                  comments=[EPIC_STAMP, FLEET])
        )
        assert board.promote() == 0
        assert board.lane_of("DRE-3019") == "Backlog"
        held = _lines_naming(capsys, "DRE-3019")
        assert len(held) == 1, f"expected exactly one line, got {held}"
        assert "epics are promoted by humans, never by the sweep" in held[0]

    def test_an_unstamped_card_titled_epic_is_still_skipped(self, capsys):
        """A card nothing has classified keeps today's answer: `[EPIC]` in the
        title is one of the two facts `validate_card.infer_agent_label` derives
        `agent:planner` from in the first place. No label on this fixture, so
        the title is the only thing that can hold it."""
        board = _Board(
            _card(identifier="DRE-3020", title="[EPIC] the front door",
                  labels=("repo:bureau-pipeline",), comments=[FLEET])
        )
        assert board.promote() == 0
        held = _lines_naming(capsys, "DRE-3020")
        assert len(held) == 1, f"expected exactly one line, got {held}"
        assert "epics are promoted by humans, never by the sweep" in held[0]

    def test_an_unstamped_card_with_children_is_still_skipped(self, capsys):
        """The other of the two facts, and the invariant
        `mid_epic.subissue_refusal` rests on: give a card sub-issues and it
        stops being promoted. Again no label — only the children can hold it."""
        board = _Board(
            _card(identifier="DRE-3021", title="a plain title", children=2,
                  labels=("repo:bureau-pipeline",), comments=[FLEET])
        )
        assert board.promote() == 0
        held = _lines_naming(capsys, "DRE-3021")
        assert len(held) == 1, f"expected exactly one line, got {held}"
        assert "epics are promoted by humans, never by the sweep" in held[0]

    def test_the_stamp_beats_the_title(self):
        """`is_epic()` reads the stamp FIRST: a card classified `one-off` is a
        one-off even if somebody left `[EPIC]` in its title."""
        board = _Board(
            _card(identifier="DRE-3022", title="[EPIC] the front door",
                  comments=[ONE_OFF_STAMP, FLEET])
        )
        assert board.promote() == 1
        assert board.lane_of("DRE-3022") == "Todo"


# ===========================================================================
# 4: the label decides nothing here any more — AC3, over the source
# ===========================================================================
class TestTheLabelDecidesNothing:
    def test_promote_ready_never_reads_agent_planner(self):
        """AC3: `grep -n "agent:planner" scripts/reconcile.py` finds no
        promotion decision. Asserted over the function's own source so a second
        spelling of the rule cannot come back quietly."""
        source = inspect.getsource(reconcile.promote_ready)
        assert PLANNER not in source, (
            "promote_ready reads the agent:planner label again — that label says "
            "the planner OWNS the card, and every card the relay dispatches to "
            "plan.yml from Planning carries it (DRE-3044)"
        )

    def test_the_one_epic_helper_is_the_one_mid_epic_left(self):
        """One helper for "is this an epic", shared with
        `routing_verdict.route()` — not a second spelling in the sweep."""
        assert "mid_epic.is_epic" in inspect.getsource(reconcile.card_is_epic)

    def test_a_plain_card_wearing_the_label_is_no_longer_an_epic(self):
        """Nothing has stamped it, it has no children and no `[EPIC]` title, so
        it is not an epic however it is labelled — which is the whole defect."""
        board = _Board(
            _card(identifier="DRE-3023", title="Trim the trailing slash",
                  labels=("repo:bureau-pipeline", PLANNER), comments=[FLEET])
        )
        assert board.promote() == 1
        assert board.lane_of("DRE-3023") == "Todo"


# ===========================================================================
# 5: the candidates query has to CARRY the facts the gate reads
# ===========================================================================
class TestTheQueryCarriesWhatTheGateReads:
    def test_backlog_children_selects_children(self):
        """`is_epic` asks whether the card has children, so the query that
        builds the candidate list has to select them — a gate reading a field
        the query never fetched answers "no children" for every epic."""
        source = inspect.getsource(reconcile.backlog_children)
        assert re.search(r"\bchildren\s*\(", source), (
            "backlog_children does not SELECT children — the function is only "
            "named after them, and the gate would read 'no children' for every "
            "epic on the board"
        )

    def test_a_card_fetched_without_children_is_not_read_as_an_epic(self):
        """Absent is absent: a fixture (or a query) with no `children` key must
        not blow up or invent one."""
        bare = _card(identifier="DRE-3024", title="a plain title")
        bare.pop("children")
        assert reconcile.card_is_epic(bare) is False
