"""RED-first tests: a card sent back to Planning gets a fresh routing verdict
(DRE-4884).

DRE-4724 was routed WORKBENCH on 2026-09-23 13:41 PT on a false phrase match.
On 2026-09-25 its wording was fixed, `hand-built` came off and it went back to
Planning; the critic passed it at 09:37 PT. The planning exit then refused to
stamp a new verdict — `_one_off_check` found the old WORKBENCH and said "a card
leaving Planning carries exactly one verdict" — and at 09:45 PT the sweep read
that old comment, put `hand-built` back and moved the card to where it started.
DRE-4496, DRE-6143 and DRE-5960 looped the same way.

The rule "exactly one verdict" stays. What changes is which verdicts COUNT:

  * a card that re-entered Planning after its verdict was written has that
    verdict RETIRED by the planning exit — on the record, as a comment naming
    the verdict, when it was written, when the card re-entered Planning and
    when it was retired — and the exit stamps the verdict it reads now;
  * `verdicts_on`, and so the promoter and every other reader, no longer
    counts a retired verdict;
  * a `hand-built` the OLD verdict put on comes off when the new verdict does
    not put it on, and is left alone when the retired verdict never applied it.

Run: cd bureau-pipeline && python3 -m pytest tests/test_verdict_retirement.py -v
"""
from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import planning_route  # noqa: E402
import planning_shape  # noqa: E402
import reconcile  # noqa: E402
import routing_verdict  # noqa: E402

from test_parentless_promotion import _Board, _card  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "dre-4724-replan-2026-10-07.json"
DRE_4724 = json.loads(FIXTURE.read_text(encoding="utf-8"))
CARD = DRE_4724["identifier"]
#: The card's real `blockedBy` relation, Done — its body declares it, and the
#: sweep sends a declaration the board does not hold to Triage (DRE-2676).
BLOCKER = "DRE-4688"
HAND_BUILT = reconcile.HAND_BUILT_LABEL

# The card's own clock, 2026-09 (UTC; PT is seven hours behind).
WORKBENCH_AT = "2026-09-23T20:41:00.000Z"    # 13:41 PT — the false match
HAND_WORK_AT = "2026-09-24T15:19:00.000Z"    # 08:19 PT — the sweep moved it
REPLANNED_AT = "2026-09-25T16:34:00.000Z"    # 09:34 PT — sent back to Planning
EXIT_AT = "2026-09-25T16:37:00.000Z"         # 09:37 PT — the critic passed it
SWEEP_AT = "2026-09-25T16:45:00.000Z"        # 09:45 PT — the next reconcile


def _move(at, frm, to):
    return {"at": at, "from": frm, "to": to}


#: DRE-4724's history up to the moment it was sent back to Planning.
SENT_TO_PLANNING = [
    _move("2026-09-23T20:41:05.000Z", "Planning", "Backlog"),
    _move(HAND_WORK_AT, "Backlog", "Hand-work"),
    _move(REPLANNED_AT, "Hand-work", "Planning"),
]


def _before() -> str:
    """DRE-4724's description as it was routed on 2026-09-23."""
    before = DRE_4724["description"].replace(
        DRE_4724["after_phrase"], DRE_4724["before_phrase"])
    assert before != DRE_4724["description"], "the fixture's phrase moved"
    return before


def _shape() -> str:
    return planning_shape.shape_comment("one-off", "one card and one pull request")


def _verdict_node(name: str, at: str, why: str = "because") -> dict:
    return {"body": routing_verdict.verdict_comment(name, why), "createdAt": at}


def _old_workbench() -> dict:
    """The verdict the 2026-09-23 exit wrote, read off the BEFORE text by the
    real routing check — not a hand-typed WORKBENCH."""
    verdict, reason = planning_route.mechanical_verdict(
        DRE_4724["title"], _before(), DRE_4724["labels"], shape="one-off")
    assert verdict == "WORKBENCH", "the before text no longer routes WORKBENCH"
    return {"body": routing_verdict.verdict_comment(verdict, reason),
            "createdAt": WORKBENCH_AT}


# ===========================================================================
# The record: a retired verdict stays on the card and is no longer read
# ===========================================================================
class TestARetiredVerdictIsNotRead:
    def _retired(self, *names, at=WORKBENCH_AT):
        nodes = [_verdict_node(n, at, f"why {n}") for n in names]
        note = routing_verdict.retirement_comment(
            nodes, REPLANNED_AT, now=datetime(2026, 9, 25, 16, 37, tzinfo=UTC))
        return nodes, note

    def test_verdicts_on_no_longer_returns_a_retired_verdict(self):
        nodes, note = self._retired("WORKBENCH")
        bodies = [n["body"] for n in nodes] + [note]
        assert routing_verdict.verdicts_on(bodies) == ()
        assert routing_verdict.verdict_on(bodies) is None

    def test_the_retired_verdict_is_still_on_the_card(self):
        """Retiring never deletes or edits: the old decision is still there
        to read, and the note sits beside it."""
        nodes, note = self._retired("WORKBENCH")
        bodies = [n["body"] for n in nodes] + [note]
        assert bodies[0] == nodes[0]["body"]
        assert bodies[0].lstrip().startswith(routing_verdict.VERDICT_MARK)

    def test_the_note_names_the_verdict_the_time_and_the_reason(self):
        _, note = self._retired("WORKBENCH")
        assert note.startswith(
            f"{routing_verdict.RETIRED_MARK} {routing_verdict.RETIRED_TAG}:")
        assert "**WORKBENCH**" in note
        assert "2026-09-23 13:41 PT" in note, "when the verdict was written"
        assert "2026-09-25 09:34 PT" in note, "when the card re-entered Planning"
        assert "2026-09-25 09:37 PT" in note, "when it was retired"
        assert "re-entered Planning" in note

    def test_the_note_is_not_itself_read_as_a_verdict(self):
        """It quotes the verdict it retires. A reader that matched the marker
        anywhere would read the note back as the verdict."""
        _, note = self._retired("FLEET")
        assert routing_verdict.verdicts_on([note]) == ()

    def test_the_new_verdict_after_the_note_is_the_one_read(self):
        nodes, note = self._retired("WORKBENCH")
        fresh = routing_verdict.verdict_comment(
            "FLEET", "one PR", replaces=routing_verdict.retired_pairs(nodes))
        bodies = [n["body"] for n in nodes] + [note, fresh]
        assert routing_verdict.verdicts_on(bodies) == ("FLEET",)
        assert routing_verdict.verdict_on(bodies) == "FLEET"

    def test_the_read_does_not_depend_on_the_order_comments_arrive_in(self):
        """Some readers hold the API's newest-first window. A retirement read
        by position would revive the old verdict there."""
        nodes, note = self._retired("WORKBENCH")
        fresh = routing_verdict.verdict_comment(
            "FLEET", "one PR", replaces=routing_verdict.retired_pairs(nodes))
        oldest_first = [n["body"] for n in nodes] + [note, fresh]
        assert routing_verdict.verdict_on(list(reversed(oldest_first))) == "FLEET"

    def test_the_same_verdict_restamped_is_live_and_its_predecessor_is_not(self):
        """WORKBENCH retired and WORKBENCH written again, for the same reason,
        is one live verdict — the restamp is never mistaken for the one it
        replaces."""
        nodes, note = self._retired("WORKBENCH")
        again = routing_verdict.verdict_comment(
            "WORKBENCH", "why WORKBENCH", replaces=routing_verdict.retired_pairs(nodes))
        bodies = [n["body"] for n in nodes] + [note, again]
        assert routing_verdict.verdicts_on(bodies) == ("WORKBENCH",)
        assert routing_verdict.fingerprint(again) not in \
            routing_verdict.retired_fingerprints(bodies)

    def test_a_note_that_only_mentions_retirement_retires_nothing(self):
        """Anchored like every marker here: the note must OPEN the comment."""
        nodes, note = self._retired("WORKBENCH")
        quoted = "the operator said: " + note
        assert routing_verdict.verdicts_on([nodes[0]["body"], quoted]) == ("WORKBENCH",)

    def test_two_live_verdicts_are_still_a_conflict(self):
        """Retirement removes a verdict from the count; it does not make two
        live ones agree."""
        nodes, note = self._retired("PARKED")
        bodies = [n["body"] for n in nodes] + [
            note,
            routing_verdict.verdict_comment("FLEET", "one PR"),
            routing_verdict.verdict_comment("WORKBENCH", "a live flow"),
        ]
        with pytest.raises(routing_verdict.ConflictingVerdicts):
            routing_verdict.verdict_on(bodies)

    def test_the_newest_verdict_time_skips_a_retired_one(self):
        nodes, note = self._retired("WORKBENCH", at="2026-09-30T00:00:00.000Z")
        thread = [
            _verdict_node("FLEET", "2026-09-26T00:00:00.000Z", "fresh"),
            *nodes,
            {"body": note, "createdAt": "2026-09-30T00:05:00.000Z"},
        ]
        assert routing_verdict.newest_verdict_at(thread) == "2026-09-26T00:00:00.000Z"
        assert routing_verdict.newest_verdict_at(thread[1:]) is None


# ===========================================================================
# Which verdicts the exit retires: those written before the card came back
# ===========================================================================
class TestWhatIsRetired:
    def test_a_verdict_written_before_the_card_re_entered_planning(self):
        old = _old_workbench()
        assert routing_verdict.retiring([old], SENT_TO_PLANNING) == (old,)

    def test_a_verdict_written_on_this_trip_is_not(self):
        """A retried exit: the first pass stamped and crashed before the move.
        Its verdict is this trip's answer, and retiring it would turn a retry
        into a thread."""
        fresh = _verdict_node("FLEET", EXIT_AT)
        assert routing_verdict.retiring([fresh], SENT_TO_PLANNING) == ()

    def test_a_retired_verdict_is_not_retired_again(self):
        old = _old_workbench()
        note = {"body": routing_verdict.retirement_comment(
            [old], REPLANNED_AT, now=datetime(2026, 9, 25, 16, 37, tzinfo=UTC)),
            "createdAt": EXIT_AT}
        assert routing_verdict.retiring([old, note], SENT_TO_PLANNING) == ()

    def test_a_card_that_never_entered_planning_by_a_move_retires_nothing(self):
        """Created straight into Planning: its verdict can only be this trip's."""
        assert routing_verdict.retiring([_verdict_node("FLEET", EXIT_AT)], []) == ()

    def test_an_unreadable_history_retires_what_the_card_carries(self):
        """The exit runs on a card IN Planning. With no history, the one case
        a carried verdict is this trip's is a retried exit, and retiring that
        costs a note and an identical restamp; keeping a stale one costs the
        loop this card exists to end."""
        old = _old_workbench()
        assert routing_verdict.retiring([old], None) == (old,)

    def test_the_entry_is_the_newest_one_into_planning(self):
        moves = [
            _move("2026-09-01T00:00:00.000Z", "Intake", "Planning"),
            _move("2026-09-01T01:00:00.000Z", "Planning", "Backlog"),
            *SENT_TO_PLANNING,
        ]
        assert routing_verdict.planning_entered_at(moves) == REPLANNED_AT
        assert routing_verdict.planning_entered_at([]) is None


# ===========================================================================
# `hand-built`: lifted only when the old verdict put it on and the new one
# does not
# ===========================================================================
class TestHandBuiltComesOffOnlyWhenTheOldVerdictPutItOn:
    def test_the_lifted_label_is_the_one_the_sweep_reads(self):
        assert routing_verdict.RETIREMENT_LIFTS == (HAND_BUILT,)

    @pytest.mark.parametrize("retired", ["WORKBENCH", "OPERATOR"])
    @pytest.mark.parametrize("new", ["FLEET", "PARKED", "NEEDS WORK", None])
    def test_a_person_s_verdict_replaced_by_one_nobody_builds_by_hand(self, retired, new):
        assert routing_verdict.lifted_marks((retired,), new) == (HAND_BUILT,)

    @pytest.mark.parametrize("retired", ["WORKBENCH", "OPERATOR"])
    @pytest.mark.parametrize("new", ["WORKBENCH", "OPERATOR"])
    def test_a_person_s_verdict_replaced_by_another_keeps_it(self, retired, new):
        assert routing_verdict.lifted_marks((retired,), new) == ()

    @pytest.mark.parametrize("retired", ["FLEET", "PARKED", "NEEDS WORK"])
    @pytest.mark.parametrize("new", ["FLEET", "PARKED", "NEEDS WORK", None])
    def test_a_label_the_old_verdict_never_applied_is_left_alone(self, retired, new):
        """A person's own `hand-built`, on a card whose retired verdict never
        put it there, is not the pipeline's to remove."""
        assert routing_verdict.lifted_marks((retired,), new) == ()

    def test_no_code_is_never_lifted(self):
        """OPERATOR also marks `no-code`, which a person may have meant on its
        own; the card scopes the lift to `hand-built`."""
        assert "no-code" not in routing_verdict.lifted_marks(("OPERATOR",), "FLEET")


# ===========================================================================
# The refusal no longer tells a human to retire a verdict by hand
# ===========================================================================
class TestTheRefusalNamesTheMechanism:
    def test_it_does_not_ask_a_human_to_retire_one(self):
        existing = [routing_verdict.verdict_comment("FLEET", "unit-testable")]
        refusal = routing_verdict.stamp_refusal("WORKBENCH", existing)
        assert "let a human retire" not in refusal
        assert "Planning" in refusal
        assert "retire" in refusal

    def test_the_conflict_is_still_refused(self):
        existing = [routing_verdict.verdict_comment("FLEET", "unit-testable")]
        refusal = routing_verdict.stamp_refusal("WORKBENCH", existing)
        assert refusal is not None and "FLEET" in refusal


# ===========================================================================
# The whole path: DRE-4724 through the real exit and the real promotion
# ===========================================================================
class _Linear:
    """One card on a faked Linear: comments with times, labels, a lane and a
    lane history. The exit and the sweep read and write through it."""

    def __init__(self, comments, *, labels, description, moves, lane="Planning"):
        self.nodes = [dict(c) for c in comments]
        self.labels = list(labels)
        self.description = description
        self.moves = list(moves)
        self.lane = lane
        self.now = EXIT_AT
        self.posted: list[str] = []
        self.removed: list[str] = []
        self.added: list[str] = []

    def bodies(self):
        return [n["body"] for n in self.nodes]

    def exit(self):
        import critic_score
        import linear_ops

        def post(identifier, body):
            self.posted.append(body)
            self.nodes.append({"body": body, "createdAt": self.now})

        def add(identifier, label):
            self.added.append(label)
            if label not in self.labels:
                self.labels.append(label)

        def remove(identifier, label):
            self.removed.append(label)
            self.labels = [x for x in self.labels if x.lower() != label.lower()]

        def move(identifier, lane, *flags):
            self.moves.append(_move(self.now, self.lane, lane))
            self.lane = lane

        def card(lops, identifier):
            return {"identifier": identifier, "title": DRE_4724["title"],
                    "description": self.description, "labels": list(self.labels),
                    "has_children": False}

        with patch.object(linear_ops, "comment_bodies",
                          side_effect=lambda i, **kw: self.bodies()), \
             patch.object(linear_ops, "comment_timeline",
                          side_effect=lambda i, **kw: [dict(n) for n in self.nodes]), \
             patch.object(linear_ops, "cmd_comment", side_effect=post), \
             patch.object(linear_ops, "add_label", side_effect=add), \
             patch.object(linear_ops, "remove_label", side_effect=remove), \
             patch.object(linear_ops, "cmd_state", side_effect=move), \
             patch.object(linear_ops, "count_comments",
                          side_effect=lambda i, needle, **kw: sum(
                              1 for b in self.bodies() if needle in b)), \
             patch.object(critic_score, "read_card", side_effect=card), \
             patch.object(routing_verdict, "lane_moves",
                          side_effect=lambda i: list(self.moves)), \
             patch.object(planning_route, "_now",
                          side_effect=lambda: datetime.fromisoformat(
                              self.now.replace("Z", "+00:00"))):
            return planning_route.main(["exit", CARD])

    def sweep(self):
        """The next reconcile, over the card as the exit left it. Comments go
        in as Linear returns them — newest first."""
        assert self.lane == "Backlog"
        backlog = _card(identifier=CARD, parent_state=None, labels=self.labels,
                        blocked_by=BLOCKER)
        backlog["title"] = DRE_4724["title"]
        backlog["description"] = self.description
        backlog["comments"] = {"nodes": list(reversed(self.nodes))}
        board = _Board(backlog)
        with patch.object(reconcile.routing_verdict, "lane_moves",
                          side_effect=lambda i: list(self.moves)):
            promoted = board.promote()
        for _, label in board.labelled:
            if label not in self.labels:
                self.labels.append(label)
        if promoted:
            self.lane = board.lane_of(CARD)
        return board


def _dre_4724_sent_back(**kw) -> _Linear:
    """DRE-4724 at 09:34 PT on 2026-09-25: the old WORKBENCH on the card, the
    wording fixed, and the card in Planning."""
    defaults = dict(
        comments=[{"body": _shape(), "createdAt": "2026-09-23T20:30:00.000Z"},
                  _old_workbench()],
        labels=DRE_4724["labels"],
        description=DRE_4724["description"],
        moves=SENT_TO_PLANNING,
    )
    defaults.update(kw)
    return _Linear(**defaults)


class TestDre4724LeavesPlanningWithAFreshVerdict:
    def test_the_exit_retires_workbench_and_stamps_fleet(self):
        card = _dre_4724_sent_back()
        assert card.exit() == 0

        assert routing_verdict.verdict_on(card.bodies()) == "FLEET"
        notes = [b for b in card.posted
                 if b.startswith(f"{routing_verdict.RETIRED_MARK} {routing_verdict.RETIRED_TAG}:")]
        assert len(notes) == 1
        assert "**WORKBENCH**" in notes[0] and "re-entered Planning" in notes[0]
        assert "2026-09-23 13:41 PT" in notes[0]
        assert card.lane == "Backlog"

    def test_the_retirement_is_written_before_the_new_verdict(self):
        card = _dre_4724_sent_back()
        card.exit()
        kinds = [("retired" if b.startswith(routing_verdict.RETIRED_MARK)
                  else "verdict" if b.startswith(routing_verdict.VERDICT_MARK)
                  else "other") for b in card.posted]
        assert kinds.index("retired") < kinds.index("verdict")

    def test_the_next_sweep_promotes_it_to_todo_without_hand_built(self):
        """The acceptance criterion end to end: before → WORKBENCH, after →
        FLEET, and the sweep at 09:45 PT carries it to Todo, unmarked."""
        card = _dre_4724_sent_back(labels=[*DRE_4724["labels"], HAND_BUILT])
        card.exit()
        assert HAND_BUILT in card.removed
        assert HAND_BUILT not in card.labels

        card.now = SWEEP_AT
        board = card.sweep()
        assert board.advanced == [(CARD, "Todo", "Backlog")]
        assert card.lane == "Todo"
        assert board.labelled == [], "the sweep put a mark back on"
        assert HAND_BUILT not in card.labels

    def test_without_the_retirement_the_card_loops(self):
        """The pairing that makes the test above mean something: the same card
        with its old verdict LIVE goes back to Hand-work marked hand-built."""
        backlog = _card(identifier=CARD, parent_state=None,
                        labels=DRE_4724["labels"], blocked_by=BLOCKER)
        backlog["description"] = DRE_4724["description"]
        backlog["comments"] = {"nodes": list(reversed(
            [{"body": _shape(), "createdAt": "2026-09-23T20:30:00.000Z"},
             _old_workbench()]))}
        board = _Board(backlog)
        moves = [*SENT_TO_PLANNING, _move(EXIT_AT, "Planning", "Backlog")]
        with patch.object(reconcile.routing_verdict, "lane_moves",
                          side_effect=lambda i: list(moves)):
            board.promote()
        assert board.advanced == [(CARD, "Hand-work", "Backlog")]
        assert (CARD, HAND_BUILT) in board.labelled

    def test_re_running_the_exit_writes_nothing_twice(self):
        card = _dre_4724_sent_back()
        card.exit()
        first = list(card.posted)
        card.lane = "Planning"
        card.now = "2026-09-25T16:38:00.000Z"
        card.exit()
        assert card.posted == first
        assert routing_verdict.verdict_on(card.bodies()) == "FLEET"


class TestAReplanThatRoutesWorkbenchAgain:
    def test_one_live_workbench_and_hand_built_stays(self):
        card = _dre_4724_sent_back(
            description=_before(), labels=[*DRE_4724["labels"], HAND_BUILT])
        assert card.exit() == 0

        live = routing_verdict.verdicts_on(card.bodies())
        assert live == ("WORKBENCH",)
        assert sum(1 for b in card.bodies()
                   if b.startswith(routing_verdict.VERDICT_MARK)) == 2, (
            "the old WORKBENCH stays on the record beside the new one")
        assert HAND_BUILT not in card.removed
        assert HAND_BUILT in card.labels

        card.now = SWEEP_AT
        board = card.sweep()
        assert board.advanced == [(CARD, "Hand-work", "Backlog")]


class TestAPersonsHandBuiltIsLeftAlone:
    def test_a_retired_parked_verdict_takes_no_label_with_it(self):
        """The retired verdict never applied `hand-built`, so the one on the
        card is somebody's own and stays."""
        card = _dre_4724_sent_back(
            comments=[{"body": _shape(), "createdAt": "2026-09-23T20:30:00.000Z"},
                      _verdict_node("PARKED", WORKBENCH_AT, "not now")],
            labels=[*DRE_4724["labels"], HAND_BUILT])
        card.exit()
        assert routing_verdict.verdict_on(card.bodies()) == "FLEET"
        assert HAND_BUILT not in card.removed
        assert HAND_BUILT in card.labels


class TestANeedsWorkReplanRetiresToo:
    def test_the_escalation_path_retires_and_lifts(self):
        """A card the re-plan cannot route does not leave Planning — it parks
        for a person — and the old verdict must not ride along with it."""
        card = _dre_4724_sent_back(
            description="Just do the thing.",
            labels=[*DRE_4724["labels"], HAND_BUILT])
        with patch("planning_escalation.escalate") as escalate:
            escalate.return_value.parked = True
            assert card.exit() == 0
        assert routing_verdict.verdicts_on(card.bodies()) == ()
        assert any(b.startswith(routing_verdict.RETIRED_MARK) for b in card.posted)
        assert HAND_BUILT in card.removed
