"""TDD for the parentless promotion path (DRE-2735).

`promote_ready` refused every card with no parent — `if not parent or
parent["state"]["name"] not in EPIC_ACTIVE_STATES: continue`. That was correct
while every card the sweep saw was an epic's child, and it becomes a hole the
moment "one-off" is a first-class size that goes straight to Backlog (DRE-2719):
the one-off is the most common thing anyone files, nothing escalates it BY
DESIGN, and it would sit in Backlog forever — re-planned by the relevance-decay
rule and landed right back in the same hole, a loop that burns a planner run per
cycle and ships nothing. The same hole swallows the OPERATOR and WORKBENCH
verdicts DRE-2724 routes into Backlog to await their turn.

FIX UNDER TEST: the gate becomes verdict-driven where there is no parent, and
parentage-driven where there is one.

* **Has a parent** — the parent's state still gates it, exactly as before.
  `EPIC_ACTIVE_STATES` is untouched and DRE-1893's reasoning stands: a child
  must not build while its epic is unapproved.
* **No parent** — the verdict IS the approval. A one-off's green light happened
  at Planning, which is the design's whole claim about why a one-off needs no
  CEO review. No verdict, no approval, no promotion.

Deliberately NOT fixed by giving one-offs a synthetic "misc" parent: an epic
that is permanently In Progress approves nothing, and it would collide with the
epic-growth KPI, which counts children.

Run: cd bureau-pipeline && python3 -m pytest tests/test_parentless_promotion.py -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")

import reconcile  # noqa: E402
import routing_verdict  # noqa: E402

GREEN_LIGHT = "2026-08-01T00:00:00.000Z"
CREATED = "2026-07-01T00:00:00.000Z"  # before the green light: never a mid-epic addition

FLEET = routing_verdict.verdict_comment("FLEET", "the acceptance criteria are unit-testable")
WORKBENCH = routing_verdict.verdict_comment("WORKBENCH", "it drives a live auth flow")


def _card(
    *,
    identifier: str = "DRE-2735",
    parent_state: str | None = "In Progress",
    comments=(),
    blocked_by: str = "",
    labels=("repo:bureau-pipeline", "agent:engineer"),
):
    """A Backlog card. `parent_state=None` makes it a PARENTLESS one-off."""
    description = "Trim the trailing slash from the health endpoint."
    if blocked_by:
        description += f"\n\n**Blocked by:** {blocked_by}"
    return {
        "id": f"uuid-{identifier}",
        "identifier": identifier,
        "title": "Trim the trailing slash from the health endpoint",
        "description": description,
        "createdAt": CREATED,
        "parent": (
            None
            if parent_state is None
            else {"identifier": "DRE-2700", "state": {"name": parent_state}}
        ),
        "labels": {"nodes": [{"name": name} for name in labels]},
        "comments": {"nodes": [{"body": b} for b in comments]},
        "inverseRelations": {"nodes": []},
    }


class _Board:
    """`reconcile.promote_ready` over a Backlog roster, with every gate that is
    not under test held open: WIP has room, epic blockers are clear, the epic is
    green-lit, and every formal blocker reads Done."""

    def __init__(self, *cards, blocker_state: str = "Done"):
        self.cards = list(cards)
        self.blocker_state = blocker_state
        self.advanced: list[tuple[str, str, str]] = []
        self.posted: list[tuple[str, str]] = []
        self.lanes = {c["identifier"]: ["Backlog"] for c in self.cards}
        self.epic_gate_reads: list[str] = []
        self.green_light_reads: list[str] = []

    def promote(self, active_count: int = 0) -> int:
        def advance(ident, to_state, from_states):
            self.advanced.append((ident, to_state, from_states))
            self.lanes.setdefault(ident, []).append(to_state)

        def epic_gate(epic):
            self.epic_gate_reads.append(epic)
            return False

        def green_light(_ops, epic):
            self.green_light_reads.append(epic)
            return GREEN_LIGHT

        with patch.object(reconcile, "REPO_SLUG", "bureau-pipeline"), patch.object(
            reconcile, "backlog_children", return_value=self.cards
        ), patch.object(
            reconcile, "epic_blockers_unmet", side_effect=epic_gate
        ), patch.object(
            reconcile.mid_epic, "last_green_light", side_effect=green_light
        ), patch.object(
            reconcile, "card_state", return_value=self.blocker_state
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
    yield
    reconcile._write_failures.clear()


# --------------------------------------------------------------------------
# The four cells: {parent, no parent} × {verdict, no verdict}
# --------------------------------------------------------------------------
class TestTheFourCells:
    """The whole decision table, each cell asserted by DESTINATION — the only
    thing that matters to a card sitting in Backlog is which lane it ends in."""

    def test_parent_active_with_fleet_verdict_promotes_to_todo(self):
        board = _Board(_card(parent_state="In Progress", comments=[FLEET]))
        assert board.promote() == 1
        assert board.lane_of("DRE-2735") == "Todo"

    def test_parent_active_without_a_verdict_still_promotes_to_todo(self):
        """Unchanged by this card: a verdictless CHILD promotes exactly as
        before, because Backlog's "it carries a verdict" clause is enforced from
        Phase 5 and refusing the whole verdictless board today would freeze it."""
        board = _Board(_card(parent_state="In Progress", comments=[]))
        assert board.promote() == 1
        assert board.lane_of("DRE-2735") == "Todo"

    def test_parentless_with_fleet_verdict_promotes_to_todo(self):
        """The new behaviour: no parent, so the verdict IS the approval."""
        board = _Board(_card(parent_state=None, comments=[FLEET]))
        assert board.promote() == 1
        assert board.lane_of("DRE-2735") == "Todo"
        assert board.advanced == [("DRE-2735", "Todo", "Backlog")]

    def test_parentless_without_a_verdict_stays_in_backlog(self):
        """Nothing has approved it: no epic a human moved, and no verdict."""
        board = _Board(_card(parent_state=None, comments=[]))
        assert board.promote() == 0
        assert board.lane_of("DRE-2735") == "Backlog"
        assert board.advanced == []


# --------------------------------------------------------------------------
# DRE-1893 is unchanged — this test fails if the parent check is deleted
# --------------------------------------------------------------------------
class TestTheParentCheckSurvives:
    def test_inactive_parent_is_refused_even_carrying_a_fleet_verdict(self):
        """The adversarial case for a verdict-driven rewrite: the card carries
        the verdict that would promote a one-off, and its epic is NOT approved.
        A gate that simply dropped the parent check promotes this card — which
        is exactly the "a child must not build while its epic is unapproved"
        behaviour DRE-1893 exists for."""
        for inactive in ("Backlog", "Planning", "Green Light", "Done"):
            board = _Board(_card(parent_state=inactive, comments=[FLEET]))
            assert board.promote() == 0, f"epic in {inactive} must not promote its child"
            assert board.lane_of("DRE-2735") == "Backlog"

    def test_active_states_are_untouched(self):
        assert reconcile.EPIC_ACTIVE_STATES == ("Todo", "In Progress")


# --------------------------------------------------------------------------
# The refusals are distinguishable in the sweep's own output
# --------------------------------------------------------------------------
class TestTheRefusalsAreDistinguishable:
    def test_the_two_refusals_read_differently(self, capsys):
        """"parent not active" and "no verdict at all" are different facts with
        different next actions — a human reading the sweep log must not have to
        guess which one held the card (console-honesty rule 1)."""
        _Board(_card(identifier="DRE-1", parent_state="Planning", comments=[FLEET])).promote()
        parent_out = capsys.readouterr().out

        _Board(_card(identifier="DRE-2", parent_state=None, comments=[])).promote()
        verdict_out = capsys.readouterr().out

        assert "DRE-1" in parent_out and "parent" in parent_out.lower()
        assert "DRE-2700" in parent_out, "the log must name the epic that held it"
        assert "DRE-2" in verdict_out
        assert routing_verdict.NO_VERDICT_TAG in verdict_out
        # The distinguishing property, stated as such: the verdict refusal must
        # not read as a parent problem, and vice versa.
        assert routing_verdict.NO_VERDICT_TAG not in parent_out
        assert "parent" not in verdict_out.split("routing-no-verdict")[1].split("\n")[0]

    def test_the_refusal_is_surfaced_on_the_card_exactly_once(self):
        """The DEAD_TAG shape: an invisible refusal is silent accretion."""
        card = _card(parent_state=None, comments=[])
        board = _Board(card)
        board.promote()
        board.promote()  # a second sweep, the card unchanged
        tagged = [b for b in board.comments_on("DRE-2735") if routing_verdict.NO_VERDICT_TAG in b]
        assert len(tagged) == 1

    def test_the_refusal_names_the_way_out(self):
        board = _Board(_card(parent_state=None, comments=[]))
        board.promote()
        notice = board.comments_on("DRE-2735")[0]
        assert "routing_verdict.py" in notice and "FLEET" in notice


# --------------------------------------------------------------------------
# A one-off is not exempt from anything else
# --------------------------------------------------------------------------
class TestAOneOffPassesEveryOtherGate:
    def test_a_non_fleet_verdict_is_still_refused(self):
        """WORKBENCH needs a person at an interactive session — the whole point
        of the verdict is that only FLEET is dispatched."""
        board = _Board(_card(parent_state=None, comments=[WORKBENCH]))
        assert board.promote() == 0
        assert board.lane_of("DRE-2735") == "Backlog"
        assert any(
            routing_verdict.NOT_FLEET_TAG in b for b in board.comments_on("DRE-2735")
        )

    def test_an_unmet_blocker_still_holds_it(self):
        board = _Board(
            _card(parent_state=None, comments=[FLEET], blocked_by="DRE-9"),
            blocker_state="In Progress",
        )
        assert board.promote() == 0
        assert board.lane_of("DRE-2735") == "Backlog"

    def test_a_met_blocker_lets_it_through(self):
        board = _Board(
            _card(parent_state=None, comments=[FLEET], blocked_by="DRE-9"),
            blocker_state="Done",
        )
        assert board.promote() == 1

    def test_the_wip_cap_still_applies(self):
        board = _Board(_card(parent_state=None, comments=[FLEET]))
        assert board.promote(active_count=reconcile.MAX_WIP) == 0

    def test_a_held_one_off_is_never_auto_promoted(self):
        board = _Board(
            _card(
                parent_state=None,
                comments=[FLEET],
                labels=("repo:bureau-pipeline", "agent:engineer", reconcile.HOLD_LABEL),
            )
        )
        assert board.promote() == 0

    def test_no_epic_read_is_paid_for_a_parentless_card(self):
        """There is no epic to gate on and no green light to read — asking
        Linear for either would be a query about a card that does not exist."""
        board = _Board(_card(parent_state=None, comments=[FLEET]))
        board.promote()
        assert board.epic_gate_reads == []
        assert board.green_light_reads == []


# --------------------------------------------------------------------------
# The sweep reports the new path rather than leaving it to be inferred
# --------------------------------------------------------------------------
class TestTheSweepReportsParentlessPromotions:
    def test_the_count_of_parentless_promotions_is_printed(self, capsys):
        board = _Board(
            _card(identifier="DRE-1", parent_state=None, comments=[FLEET]),
            _card(identifier="DRE-2", parent_state=None, comments=[FLEET]),
            _card(identifier="DRE-3", parent_state="In Progress", comments=[FLEET]),
        )
        assert board.promote() == 3
        out = capsys.readouterr().out
        summary = [l for l in out.splitlines() if l.startswith("promotion: 3 card(s)")]
        assert summary, out
        assert "2 parentless" in summary[0]

    def test_zero_is_reported_too(self, capsys):
        """Reported every sweep, not only when it fires: "no line" and "none
        promoted" must not be the same rendering."""
        board = _Board(_card(parent_state="In Progress", comments=[FLEET]))
        board.promote()
        out = capsys.readouterr().out
        assert "0 parentless" in out

    def test_the_promotion_comment_says_what_actually_approved_it(self):
        """The standing comment claims "parent epic active", which is false on a
        card that has no parent."""
        board = _Board(_card(parent_state=None, comments=[FLEET]))
        board.promote()
        note = [b for b in board.comments_on("DRE-2735") if "Auto-promoted" in b]
        assert note, board.posted
        assert "parent epic active" not in note[0]
        assert "FLEET" in note[0]


# --------------------------------------------------------------------------
# routing_verdict: the refusal a parentless card is judged by
# --------------------------------------------------------------------------
class TestParentlessPromotionRefusal:
    def test_no_verdict_is_a_refusal(self):
        refusal = routing_verdict.parentless_promotion_refusal("DRE-2735", [])
        assert refusal is not None
        assert routing_verdict.NO_VERDICT_TAG in refusal

    def test_a_fleet_verdict_is_not(self):
        assert routing_verdict.parentless_promotion_refusal("DRE-2735", [FLEET]) is None

    def test_a_non_fleet_verdict_keeps_its_own_refusal(self):
        """The wrong-destination refusal already exists and says where the card
        goes instead — do not replace it with "no verdict", which is a different
        fact."""
        refusal = routing_verdict.parentless_promotion_refusal("DRE-2735", [WORKBENCH])
        assert refusal is not None
        assert routing_verdict.NOT_FLEET_TAG in refusal
        assert routing_verdict.NO_VERDICT_TAG not in refusal

    def test_two_verdicts_still_raise_the_conflict_refusal(self):
        refusal = routing_verdict.parentless_promotion_refusal(
            "DRE-2735", [FLEET, WORKBENCH]
        )
        assert refusal is not None
        assert routing_verdict.NOT_FLEET_TAG in refusal

    def test_the_two_tags_are_distinct(self):
        assert routing_verdict.NO_VERDICT_TAG != routing_verdict.NOT_FLEET_TAG
        assert routing_verdict.NO_VERDICT_TAG != reconcile.mid_epic.NO_VERDICT_TAG


# --------------------------------------------------------------------------
# Scenario — the one-off's whole journey, hand-walked as a test
# --------------------------------------------------------------------------
class TestTheOneOffJourney:
    """Intake → Planning → Backlog → promoted → dispatched, no human, and never
    through Green Light.

    The unit cells above prove the gate. This proves the ROUTE: the CEO files a
    one-off, Planning classifies it and stamps the verdict that lands it in
    Backlog, the sweep promotes it, and the Todo backstop dispatches it as an
    engineer run. Unit-green is not live-working, and this is the walk that the
    hole in `promote_ready` swallowed.
    """

    def test_a_one_off_reaches_a_dispatched_run_untouched_by_a_human(self):
        lanes = ["Intake"]
        writers: list[str] = []

        # Planning: the planner classifies. This card's criteria name neither
        # signal, so the route is a judgement call — answered FLEET at Planning
        # exit, which is where a one-off's green light happens.
        lanes.append("Planning")
        writers.append("plan.yml")
        decision = routing_verdict.route(
            "Trim the trailing slash from the health endpoint",
            "## Acceptance criteria\n- [ ] a unit test covers both spellings\n",
            ["repo:bureau-pipeline", "agent:engineer"],
        )
        assert decision.verdict in (None, "FLEET"), decision
        verdict = routing_verdict.verdict_comment(
            "FLEET", "the acceptance criteria are unit-testable"
        )
        assert routing_verdict.destination("FLEET") == "Todo"

        # Planning exit lands the parentless card in Backlog, carrying it.
        lanes.append("Backlog")
        writers.append("linear_ops.py")
        card = _card(parent_state=None, comments=[verdict])

        # The sweep promotes it. No human acted; nothing escalated it.
        board = _Board(card)
        assert board.promote() == 1
        lanes.append(board.lane_of("DRE-2735"))
        writers.append("reconcile.py")

        # The Todo backstop dispatches it — an engineer run, not a plan run.
        dispatched: dict = {}

        def fake_run(argv, **kw):
            with open(argv[argv.index("--input") + 1], encoding="utf-8") as fh:
                import json

                dispatched.update(json.load(fh))

            class _P:
                returncode = 0
                stderr = ""

            return _P()

        with patch.object(reconcile.subprocess, "run", side_effect=fake_run):
            assert reconcile.redispatch(card) is True
        writers.append("reconcile.py")

        assert lanes == ["Intake", "Planning", "Backlog", "Todo"]
        assert "Green Light" not in lanes
        assert set(writers) <= {"plan.yml", "linear_ops.py", "reconcile.py"}
        assert dispatched["event_type"] == "agent-execute"
        assert dispatched["client_payload"]["identifier"] == "DRE-2735"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
