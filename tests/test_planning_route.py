"""RED-first tests: Planning branches three ways on the shape (DRE-2844).

Every card arriving in Planning today is planned as if it were an epic — it
owes a full plan artifact and it stops in Green Light for the CEO. For a
one-line config change that is a planner run, a document, and a human decision
spent on something nobody needed to decide.

`scripts/planning_route.py` is the branch. It reads the shape its sibling card
stamped (`planning_shape.py`, DRE-2843) and answers *which route does this card
take out of Planning* — and it answers it FROM `config/planning-shapes.json`,
which already declares every shape's destination and the actor accountable for
it there. Routing repeated in code is routing that drifts from the file the
lane contract binds.

WHAT THIS PINS, one section per acceptance criterion:

  1. A card stamped **one-off** leaves Planning for the build queue: no plan
     artifact, no Green Light stop, and nothing that reaches the CEO.
  2. A card stamped **epic** takes the existing artifact-and-children path,
     unchanged — asserted against the CURRENT behaviour of `plan.yml`, so this
     card cannot quietly alter it.
  3. A card stamped **roll-up** is handed to the planner to be split into
     child epics (DRE-4699; the plan.yml route on the other side of that
     hand-off is DRE-4718's — this card only hands off).
  4. A card with **no shape** takes no default route. It is refused, and the
     reason names the missing stamp.
  5. A one-off with **no parent epic** is OBSERVED being promoted — the
     DRE-2735 shape closed by test rather than trusted: `promote_ready` skips a
     parentless card that carries no routing verdict, so the most common card
     this design produces could loop Intake → Planning → Backlog forever,
     burning a planner run each cycle. The one-off route's own verdict is what
     closes it, and the pairing below proves the verdict is load-bearing.
  6. The three destinations are READ from `config/planning-shapes.json`:
     changing the file changes the routing.
  7. (DRE-3038) The one-off's routing verdict is READ FROM THE CARD and never
     defaulted: role label → title convention → acceptance criteria → NEEDS
     WORK when there are none, exactly as `routing_verdict.route()` decides for
     a child. A NEEDS WORK one-off takes the escalation exit rather than
     leaving Planning silently, and the reason on the verdict comment is the
     branch that actually decided.

Run: cd bureau-pipeline && python3 -m pytest tests/test_planning_route.py -v
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import lane_contract  # noqa: E402
import planning_route  # noqa: E402
import planning_shape  # noqa: E402
import routing_verdict  # noqa: E402

# The proven promotion harness, reused rather than rebuilt: it is the board
# DRE-2735 was fixed against, with every gate that is not under test held open.
from test_parentless_promotion import _Board, _card  # noqa: E402

CONFIG = ROOT / "config" / "planning-shapes.json"
WF = ROOT / ".github" / "workflows" / "plan.yml"

CARD = "DRE-2844"

# A real one-off body: one card, one pull request, with the checkbox criteria
# `routing_verdict` reads to decide who builds it.
ONE_OFF_TITLE = "Trim the trailing slash from the health endpoint"
ONE_OFF_BODY = (
    "The health endpoint answers on `/health/` and not on `/health`.\n\n"
    "## Acceptance criteria\n\n"
    "- [ ] `/health` answers 200 with the same body as `/health/`\n"
    "- [ ] A unit test covers both paths\n"
)
ONE_OFF_LABELS = ("repo:bureau-pipeline", "agent:engineer")


def _mutated() -> dict:
    """A private copy of the shipped vocabulary, for the mutation tests."""
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def _entry(doc: dict, name: str) -> dict:
    return next(e for e in doc["shapes"] if e["name"] == name)


def _stamp(name: str) -> str:
    """The comment that IS a shape, written by the module that owns it."""
    return planning_shape.shape_comment(name, "because the plan says so")


def _read_card(identifier: str = CARD, *, title: str = ONE_OFF_TITLE,
               description: str = ONE_OFF_BODY, labels=ONE_OFF_LABELS,
               has_children: bool = False) -> dict:
    """One card in the shape `critic_score.read_card` returns — the full read
    the routing check needs (a truncated description routes on the top of the
    card)."""
    return {
        "identifier": identifier,
        "title": title,
        "description": description,
        "labels": list(labels),
        "has_children": has_children,
    }


# ===========================================================================
# 1 / 3 / 6: three shapes, three routes, all of them read from the file
# ===========================================================================
class TestTheThreeRoutes:
    def test_every_shape_in_the_vocabulary_has_a_route(self):
        assert tuple(r.shape for r in planning_route.routes()) == planning_shape.shapes()

    def test_the_shipped_vocabulary_routes_cleanly(self):
        assert planning_route.route_problems() == []

    def test_a_one_off_owes_no_plan_artifact_and_no_green_light(self):
        route = planning_route.route_for("one-off")
        assert route.owes_plan_artifact is False
        assert route.owes_green_light is False
        assert route.reaches_ceo is False
        # And it is the one shape the sweep may promote — which is what "goes
        # straight to the build queue" means mechanically.
        assert route.promotable is True

    def test_the_one_off_destination_and_actor_come_from_the_file(self):
        route = planning_route.route_for("one-off")
        assert route.destination == planning_shape.destination("one-off")
        assert route.actor == planning_shape.actor("one-off")
        assert route.destination in lane_contract.lane_names(status="live")

    def test_an_epic_owes_the_artifact_and_the_green_light(self):
        """The existing path, asserted against the CURRENT behaviour: an epic
        stops in the CEO's queue and the artifact is what it stops on."""
        route = planning_route.route_for("epic")
        assert route.destination == "Green Light"
        assert route.actor == "operator"
        assert route.owes_green_light is True
        assert route.owes_plan_artifact is True
        assert route.promotable is False

    def test_a_roll_up_is_handed_off_and_stops_for_nobody(self):
        """A roll-up cannot be green-lit as one plan — it is split into child
        epics, each green-lit on its own. It goes to the planner, not the CEO."""
        route = planning_route.route_for("roll-up")
        assert route.destination == planning_shape.destination("roll-up")
        assert route.actor == planning_shape.actor("roll-up")
        assert route.owes_green_light is False
        assert route.owes_plan_artifact is False

    def test_an_unknown_shape_is_raised_not_defaulted(self):
        with pytest.raises(planning_shape.UnknownShape):
            planning_route.route_for("saga")


class TestTheRoutingComesFromTheFile:
    """Changing the file changes the routing. Each mutation below is a route
    the pipeline would really take if somebody edited the vocabulary."""

    def test_moving_a_shapes_destination_moves_the_route(self):
        doc = _mutated()
        _entry(doc, "one-off")["destination"] = "Todo"
        assert planning_route.route_for("one-off", doc).destination == "Todo"

    def test_a_route_stops_for_a_human_only_where_the_file_names_one(self):
        """The green light is not a per-shape constant in this module: it is
        the file naming a HUMAN as the actor accountable at the destination.
        Hand the epic to an agent in the file and the CEO stop goes with it."""
        doc = _mutated()
        _entry(doc, "epic")["actor"] = "plan.yml"
        _entry(doc, "epic")["destination"] = "Planning"
        route = planning_route.route_for("epic", doc)
        assert route.owes_green_light is False
        assert route.owes_plan_artifact is False

    def test_giving_a_one_off_a_human_actor_makes_it_stop(self):
        doc = _mutated()
        _entry(doc, "one-off")["destination"] = "Green Light"
        _entry(doc, "one-off")["actor"] = "operator"
        assert planning_route.route_for("one-off", doc).reaches_ceo is True

    def test_marks_and_promotability_are_the_files_too(self):
        doc = _mutated()
        _entry(doc, "one-off")["marks"] = ["hand-built"]
        _entry(doc, "one-off")["promotable"] = False
        route = planning_route.route_for("one-off", doc)
        assert route.marks == ("hand-built",)
        assert route.promotable is False

    def test_a_vocabulary_where_nobody_stops_for_a_human_is_a_problem(self):
        """Every route agentic means no epic ever reaches the CEO — the
        opposite failure to the one this card fixes, and just as silent."""
        doc = _mutated()
        _entry(doc, "epic")["actor"] = "plan.yml"
        problems = planning_route.route_problems(doc)
        assert problems, "a vocabulary with no human stop must be refused"
        assert any("green light" in p.lower() or "human" in p.lower() for p in problems)

    def test_a_vocabulary_where_two_shapes_stop_for_a_human_is_a_problem(self):
        doc = _mutated()
        _entry(doc, "one-off")["destination"] = "Green Light"
        _entry(doc, "one-off")["actor"] = "operator"
        assert planning_route.route_problems(doc), (
            "two shapes stopping for the CEO is the state this card removes"
        )

    def test_the_human_writer_is_one_the_lane_contract_defines(self):
        """If the glossary renames the human, this module must fail loudly
        rather than quietly deciding that nothing stops for anyone."""
        known = set(lane_contract.writers())
        assert set(planning_route.HUMAN_ACTORS) <= known


# ===========================================================================
# 4: a card with no shape takes no default route
# ===========================================================================
class TestACardWithNoShapeIsRefused:
    def test_a_stamped_card_routes(self):
        assert planning_route.decide(CARD, [_stamp("one-off")]).shape == "one-off"

    def test_no_stamp_is_refused_and_the_reason_names_the_missing_stamp(self):
        with pytest.raises(planning_route.Unroutable) as caught:
            planning_route.decide(CARD, ["just a comment about the weather"])
        notice = caught.value.notice
        assert planning_shape.NO_SHAPE_TAG in notice
        assert CARD in notice
        # The reason names the stamp that is missing, and how to write it —
        # a refusal nobody can act on is a card that stops moving.
        assert "planning_shape.py stamp" in notice
        for name in planning_shape.shapes():
            assert name in notice
        assert caught.value.tag == planning_shape.NO_SHAPE_TAG

    def test_the_refusal_carries_no_route(self):
        """No default, not even a defensible one: a one-off that was never
        classified would be dispatched to the fleet, and an epic that was never
        classified would take the CEO's time."""
        with pytest.raises(planning_route.Unroutable) as caught:
            planning_route.decide(CARD, [])
        assert getattr(caught.value, "route", None) is None

    def test_two_shapes_are_refused_naming_both(self):
        with pytest.raises(planning_route.Unroutable) as caught:
            planning_route.decide(CARD, [_stamp("one-off"), _stamp("epic")])
        assert caught.value.tag == planning_shape.TWO_SHAPES_TAG
        assert "one-off" in caught.value.notice and "epic" in caught.value.notice

    def test_an_unrecognised_word_is_a_different_refusal(self):
        """"Classified into a vocabulary that does not exist" and "not
        classified yet" are different faults with different next actions."""
        stamp = f"{planning_shape.SHAPE_MARK} {planning_shape.SHAPE_TAG}: **saga** — a word"
        with pytest.raises(planning_route.Unroutable) as caught:
            planning_route.decide(CARD, [stamp])
        assert caught.value.tag == planning_shape.UNKNOWN_SHAPE_TAG
        assert "saga" in caught.value.notice


# ===========================================================================
# 1: the one-off exit — checked, then straight to the build queue
# ===========================================================================
class TestTheOneOffExit:
    def test_it_leaves_for_the_destination_the_file_declares(self):
        exit_plan = planning_route.exit_plan(_read_card(), [_stamp("one-off")])
        assert exit_plan.destination == planning_shape.destination("one-off")
        assert exit_plan.route.shape == "one-off"

    def test_it_carries_the_routing_verdict_the_sweep_needs(self):
        """A one-off inherits no epic's approval, so its verdict IS the
        approval — and without one the sweep refuses to promote it (DRE-2735).

        The CARD decides it, never the shape (DRE-3038): this body states two
        criteria and neither of them names a person, so an unattended agent can
        satisfy the stated exit condition. Section 7 walks the branches.
        """
        exit_plan = planning_route.exit_plan(_read_card(), [_stamp("one-off")])
        assert exit_plan.verdict == "FLEET"
        assert exit_plan.reason.strip()

    def test_the_buildable_verdict_is_the_one_the_sweep_promotes(self):
        """Derived from the routing vocabulary, not named here: exactly one
        verdict is promotable, and that is the one a buildable one-off leaves
        with."""
        promotable = [v for v in routing_verdict.verdicts()
                      if routing_verdict.is_promotable(v)]
        assert planning_route.fleet_verdict() == promotable[0]
        assert len(promotable) == 1

    def test_a_card_that_already_carries_a_verdict_is_not_stamped_twice(self):
        already = routing_verdict.verdict_comment("FLEET", "decided at intake")
        exit_plan = planning_route.exit_plan(
            _read_card(), [_stamp("one-off"), already]
        )
        assert exit_plan.verdict is None
        assert "FLEET" in exit_plan.reason

    def test_the_mechanical_check_overrides_the_shape(self):
        """The check has to be able to say no. A title convention, an explicit
        role label, and a card with no exit condition at all each route
        somewhere other than the fleet — and the shape does not talk over them.
        (A criterion naming an interactive flow was the first of these until
        DRE-6227 retired that signal; the `DEMO:` title is how a card reaches
        WORKBENCH now.)"""
        assert planning_route.exit_plan(
            _read_card(title="DEMO: walk the board end to end"), [_stamp("one-off")]
        ).verdict == "WORKBENCH"

        assert planning_route.exit_plan(
            _read_card(labels=("repo:bureau-pipeline", "no-code")), [_stamp("one-off")]
        ).verdict == "OPERATOR"

        assert planning_route.exit_plan(
            _read_card(description="Just do the thing."), [_stamp("one-off")]
        ).verdict == "NEEDS WORK"

    def test_the_note_says_it_needs_no_plan_and_no_green_light(self):
        exit_plan = planning_route.exit_plan(_read_card(), [_stamp("one-off")])
        note = exit_plan.note.lower()
        assert "plan" in note and "green light" in note
        assert exit_plan.destination.lower() in note
        assert exit_plan.route.actor.lower() in note

    def test_the_note_is_plain_english(self):
        """Card text is read by the person who filed it. No code, no diffs, no
        commands, no file paths (standards/comms.md). The ACTOR is named as the
        vocabulary names it — `reconcile.py` is who picks the card up, and
        `planning_shape.shape_comment` already writes it the same way."""
        note = planning_route.exit_plan(_read_card(), [_stamp("one-off")]).note
        for forbidden in ("```", "python3", ".json", "scripts/", "config/"):
            assert forbidden not in note, f"the note must not contain {forbidden!r}"

    def test_the_epic_route_has_no_exit_here(self):
        """The epic's exit is the existing path in plan.yml — artifact,
        children, green light. Performing it from here would be a second
        implementation of the one thing this card must not alter."""
        with pytest.raises(ValueError):
            planning_route.exit_plan(_read_card(), [_stamp("epic")])


# ===========================================================================
# 3: the roll-up is handed off
# ===========================================================================
class TestTheRollUpIsHandedOff:
    def test_it_goes_where_the_file_sends_it_and_names_who_takes_it(self):
        exit_plan = planning_route.exit_plan(_read_card(), [_stamp("roll-up")])
        assert exit_plan.destination == planning_shape.destination("roll-up")
        assert planning_shape.actor("roll-up") in exit_plan.note

    def test_a_roll_up_is_given_no_routing_verdict(self):
        """A verdict answers "who builds this card". Nobody builds a roll-up —
        it is split into child epics, and it is never built itself."""
        exit_plan = planning_route.exit_plan(_read_card(), [_stamp("roll-up")])
        assert exit_plan.verdict is None

    def test_the_hand_off_never_reaches_the_ceo(self):
        assert planning_route.route_for("roll-up").reaches_ceo is False

    def test_the_note_says_it_is_split_into_child_epics(self):
        exit_plan = planning_route.exit_plan(_read_card(), [_stamp("roll-up")])
        assert "split into child epics under this card" in exit_plan.note
        assert not re.search(r"\bwaves?\b", exit_plan.note, re.I), (
            "the hand-off note still says wave — waves are retired"
        )
        assert "child epics" in exit_plan.reason


# ===========================================================================
# 5: the DRE-2735 shape, closed by observation
# ===========================================================================
class TestTheOneOffIsActuallyPromoted:
    """Not "2735 is Done, so this works" — the promotion is exercised.

    `promote_ready` skipped every card without a parent epic in an active
    state. A one-off has no parent by construction, so the most common card
    this design produces would have looped Intake → Planning → Backlog forever.
    """

    def _one_off_in_backlog(self, comments):
        card = _card(identifier=CARD, parent_state=None, comments=comments)
        card["title"] = ONE_OFF_TITLE
        card["description"] = ONE_OFF_BODY
        return card

    def test_a_parentless_one_off_carrying_its_route_verdict_reaches_in_progress(self):
        exit_plan = planning_route.exit_plan(_read_card(), [_stamp("one-off")])
        stamped = routing_verdict.verdict_comment(exit_plan.verdict, exit_plan.reason)
        board = _Board(self._one_off_in_backlog([_stamp("one-off"), stamped]))

        assert board.promote() == 1
        assert board.lane_of(CARD) == "In Progress"
        assert board.advanced == [
            (CARD, "In Progress", ",".join(lane_contract.QUEUE_LANES))]

    def test_the_same_card_without_that_verdict_does_not(self):
        """The pairing that makes the test above mean something: the verdict
        the one-off route writes is what closes the hole, so a route that
        stopped writing it would fail here."""
        board = _Board(self._one_off_in_backlog([_stamp("one-off")]))
        assert board.promote() == 0
        assert board.lane_of(CARD) == "Backlog"

    def test_the_verdict_the_route_writes_is_one_the_sweep_promotes(self):
        exit_plan = planning_route.exit_plan(_read_card(), [_stamp("one-off")])
        assert routing_verdict.is_promotable(exit_plan.verdict)
        assert routing_verdict.parentless_promotion_refusal(
            CARD, [routing_verdict.verdict_comment(exit_plan.verdict, exit_plan.reason)]
        ) is None


# ===========================================================================
# The CLI — the seam the run actually calls, where the writes happen
# ===========================================================================
#: When these cards' comments were posted — a planning attempt that ended
#: before this process started, which is what a card's history is by the time
#: the exit command reaches it. Nothing here turns on a CURRENT verdict; the
#: freshness case lives in `tests/test_planning_escalation.py` (DRE-4124).
COMMENTED_AT = "2026-09-08T11:02:00-07:00"


class _Card:
    """One card, with the CLI's writes recorded rather than posted.

    `_cmd_decide`/`_cmd_exit` import `linear_ops` inside the function body, so
    patching the module object is what reaches them.
    """

    def __init__(self, comments, *, description: str = ONE_OFF_BODY,
                 labels=ONE_OFF_LABELS, lane: str = "Planning",
                 blocked_by=(), has_children: bool = False, history=(),
                 relations_fail: bool = False, history_fail: bool = False):
        self.comments = list(comments)
        self.description = description
        self.labels = list(labels)
        #: Where the board says the card is — the escalation re-reads this
        #: before it parks (DRE-3654).
        self.lane = lane
        #: What an epic's wait reads (DRE-6651): its formal blockers as
        #: `{"identifier", "state"}` rows, whether it has children, and its
        #: lane history newest first — and whether either read fails.
        self.blocked_by = [dict(row) for row in blocked_by]
        self.has_children = has_children
        self.history = [dict(row) for row in history]
        self.relations_fail = relations_fail
        self.history_fail = history_fail
        self.posted: list[tuple[str, str]] = []
        self.labelled: list[tuple[str, str]] = []
        self.states: list[tuple[str, str]] = []

    def run(self, fn):
        import critic_score
        import linear_ops

        def post(identifier, body):
            self.posted.append((identifier, body))
            self.comments.append(body)

        def move(identifier, lane, *flags):
            self.states.append((identifier, lane))
            self.lane = lane

        def read(identifier, **kw):
            return {
                "id": "issue-id", "identifier": identifier, "title": "a card",
                "team": {"id": "team-id"},
                "state": {"name": self.lane, "type": "unstarted"},
                "labels": {"nodes": [{"name": n} for n in self.labels]},
                "children": {"nodes": []},
            }

        def gql(query, variables=None, **kw):
            # The one single-issue read the epic wait makes (DRE-6651): its
            # formal relations with their states, and whether it has children.
            # Anything else is a read nothing here expected.
            if "inverseRelations" not in query:
                raise AssertionError(f"an unstubbed Linear read: {query[:80]!r}")
            if self.relations_fail:
                raise linear_ops.LinearError("Linear is down")
            return {"issue": {
                "identifier": (variables or {}).get("id"),
                "children": {"nodes": [{"id": "child"}] if self.has_children else []},
                "inverseRelations": {
                    "pageInfo": {"hasNextPage": False},
                    "nodes": [
                        {"type": "blocks", "issue": {
                            "identifier": row["identifier"],
                            "state": {"name": row["state"]}}}
                        for row in self.blocked_by
                    ],
                },
            }}

        def history(identifier, **kw):
            if self.history_fail:
                raise linear_ops.LinearError("Linear is down")
            return list(self.history)

        with patch.object(
            linear_ops, "comment_bodies",
            side_effect=lambda i, **_kw: list(self.comments)
        ), patch.object(
            # The escalation reads the thread WITH its times (DRE-4124) — it
            # has to tell a verdict from this planning attempt from one a spent
            # attempt left behind. Same window, same query, `createdAt` kept.
            linear_ops, "comment_timeline",
            side_effect=lambda i, **_kw: [
                {"body": body, "createdAt": COMMENTED_AT}
                for body in self.comments
            ],
        ), patch.object(
            # The exit reads the lane history only for a card carrying a
            # verdict, to tell one from an earlier trip (DRE-4884). None of
            # these cards was ever moved INTO Planning, so none is retired;
            # `tests/test_verdict_retirement.py` covers the ones that were.
            routing_verdict, "lane_moves", side_effect=lambda i: [],
        ), patch.object(
            # The escalation reads it too, once a note is on the thread, to
            # tell this attempt's note from a spent one (DRE-6490) — the same
            # card, never moved INTO Planning, so no attempt opens there. The
            # epic wait reads it for an earlier green light (DRE-6651).
            linear_ops, "lane_history", side_effect=history,
        ), patch.object(
            linear_ops, "gql", side_effect=gql,
        ), patch.object(
            linear_ops, "cmd_comment", side_effect=post
        ), patch.object(
            linear_ops, "add_label",
            side_effect=lambda i, label: self.labelled.append((i, label)),
        ), patch.object(
            linear_ops, "cmd_state", side_effect=move,
        ), patch.object(
            linear_ops, "get_issue", side_effect=read,
        ), patch.object(
            linear_ops, "count_comments",
            side_effect=lambda i, needle, **kw: sum(
                1 for body in self.comments if needle in body
            ),
        ), patch.object(
            critic_score, "read_card",
            side_effect=lambda lops, i: _read_card(
                i, description=self.description, labels=self.labels
            ),
        ):
            return fn()

    def bodies(self) -> str:
        return "\n".join(body for _, body in self.posted)


class TestTheExitCommand:
    def test_a_one_off_is_stamped_told_and_moved_in_one_pass(self):
        card = _Card([_stamp("one-off")])
        assert card.run(lambda: planning_route.main(["exit", CARD])) == 0

        assert routing_verdict.verdict_on([b for _, b in card.posted]) == "FLEET"
        assert planning_route.ROUTE_TAG in card.bodies()
        assert card.states == [(CARD, planning_shape.destination("one-off"))]

    def test_the_route_note_is_not_read_back_as_a_second_shape(self):
        """The note names the shape it is describing. A reader that matched the
        marker anywhere in a body would read the note back as a stamp — and a
        card with two stamps is refused."""
        card = _Card([_stamp("one-off")])
        card.run(lambda: planning_route.main(["exit", CARD]))
        assert planning_shape.shape_on(card.comments) == "one-off"

    def test_re_running_the_exit_writes_nothing_twice(self):
        """Vendor boundary Q3/Q5: a run that crashed after the comments and
        before the move is retried, and a retry must converge rather than turn
        one decision into a thread."""
        card = _Card([_stamp("one-off")])
        card.run(lambda: planning_route.main(["exit", CARD]))
        first = list(card.posted)
        card.run(lambda: planning_route.main(["exit", CARD]))

        assert card.posted == first, "the second pass posted a comment again"
        assert card.states == [(CARD, "Queued"), (CARD, "Queued")]

    def test_a_roll_up_is_moved_and_given_no_verdict(self):
        card = _Card([_stamp("roll-up")])
        assert card.run(lambda: planning_route.main(["exit", CARD])) == 0
        assert card.states == [(CARD, planning_shape.destination("roll-up"))]
        assert routing_verdict.verdicts_on([b for _, b in card.posted]) == ()

    def test_the_marks_the_shape_declares_are_applied(self):
        card = _Card([_stamp("roll-up")])
        card.run(lambda: planning_route.main(["exit", CARD]))
        assert [label for _, label in card.labelled] == list(planning_shape.marks("roll-up"))


class TestTheDecideCommand:
    def test_a_stamped_card_writes_its_route_to_the_step_outputs(self, tmp_path):
        out = tmp_path / "out.txt"
        card = _Card([_stamp("one-off")])
        assert card.run(
            lambda: planning_route.main(["decide", CARD, "--github-output", str(out)])
        ) == 0
        written = dict(
            line.split("=", 1) for line in out.read_text().splitlines() if line
        )
        assert written["route"] == "one-off"
        assert written["destination"] == planning_shape.destination("one-off")
        assert written["refused"] == "false"
        assert written["plan_artifact"] == "false"
        assert card.posted == [], "reading a shape must not write to the card"

    def test_an_unclassified_card_is_told_once_and_routes_nowhere(self, tmp_path):
        """Refused, not defaulted — and not a red run either: a card nobody has
        classified is owed a message, not a failed workflow that summons the
        medic."""
        out = tmp_path / "out.txt"
        card = _Card([])
        assert card.run(
            lambda: planning_route.main(["decide", CARD, "--github-output", str(out)])
        ) == 0
        written = dict(
            line.split("=", 1) for line in out.read_text().splitlines() if line
        )
        assert written["refused"] == "true"
        assert written["route"] == ""
        assert planning_shape.NO_SHAPE_TAG in card.bodies()

        # Told once: the sweep re-triggers Planning, and a fault must not
        # become a thread.
        card.run(lambda: planning_route.main(["decide", CARD]))
        assert len(card.posted) == 1


# ===========================================================================
# The run itself: plan.yml branches three ways, and the epic path is untouched
# ===========================================================================
def _jobs() -> dict:
    return yaml.safe_load(WF.read_text(encoding="utf-8"))["jobs"]


def _steps() -> list:
    return [s for job in _jobs().values() for s in job.get("steps") or []]


def _step(fragment: str) -> dict:
    for step in _steps():
        if fragment.lower() in (step.get("name") or "").lower():
            return step
    raise AssertionError(
        f"no step whose name contains {fragment!r}; have: "
        + ", ".join(repr(s.get("name")) for s in _steps())
    )


def _index(fragment: str) -> int:
    steps = _jobs()["plan"]["steps"]
    for i, step in enumerate(steps):
        if fragment.lower() in (step.get("name") or "").lower():
            return i
    raise AssertionError(f"no step named {fragment!r} in the plan job")


class TestPlanYmlBranchesThreeWays:
    def test_the_shape_is_read_before_anything_routes(self):
        step = _step("Planning shape")
        assert step.get("id") == "shape"
        assert "planning_route.py" in step["run"] and "decide" in step["run"]
        # After the card-validation gate, before the plan/activate route.
        assert "steps.gate.outputs.bounced" in step["if"]
        assert _index("Planning shape") < _index("Route — plan or activate")

    def test_each_of_the_three_shapes_has_its_own_gated_step(self):
        assert "steps.shape.outputs.route == 'epic'" in _step("Route — plan or activate")["if"]
        assert "steps.shape.outputs.route == 'one-off'" in _step("One-off route")["if"]
        # DRE-4718: the roll-up's hand-off, gated on the shape name DRE-4699
        # stamps, still exits through planning_route.py.
        hand_off = _step("Roll-up route — hand off")
        assert hand_off["if"] == "steps.shape.outputs.route == 'roll-up'"
        assert "planning_route.py exit" in hand_off["run"]

    def test_the_one_off_step_names_no_lane_of_its_own(self):
        """The destination is the file's. A lane written into the YAML is the
        routing repeated in code that this card exists to remove."""
        for fragment in ("One-off route",):
            body = _step(fragment)["run"]
            for lane in lane_contract.lane_names(status="live"):
                assert lane not in body, (
                    f"the {fragment} step names the lane {lane!r} — the "
                    "destination comes from config/planning-shapes.json"
                )

    def test_no_roll_up_step_names_a_lane_of_its_own(self):
        """DRE-4718: the roll-up's lanes are written by planning_route.py (the
        hand-off) and epic_split.py (the activation), each reading them off
        the vocabulary and the contract — never by the YAML around them."""
        rollup = [s for s in _steps()
                  if (s.get("name") or "").startswith("Roll-up route")]
        assert rollup, "plan.yml carries no Roll-up route step"
        for step in rollup:
            body = step.get("run") or ""
            for lane in lane_contract.lane_names(status="live"):
                assert lane not in body, (
                    f"the {step['name']!r} step names the lane {lane!r} — the "
                    "destination comes from the script it calls"
                )

    def test_a_one_off_run_writes_no_artifact_and_asks_one_model(self):
        """Everything the epic route owes — the planner, both critics, the
        artifact check, the publish job — hangs off the plan/activate mode that
        only the epic route sets. A one-off run therefore writes no plan
        artifact and never runs the planner.

        Two steps run on a critic's DECISION rather than on the mode directly,
        so the chain is walked: the decision steps themselves are mode-gated.

        The roll-up route's own agent (DRE-4718) hangs off the SHAPE instead —
        there is no mode on that branch — so an agent step qualifies either
        way, as long as the shape it waits for is not the one-off.

        DRE-3041 narrowed this from "asks no model" to "asks one". A one-off
        that no model reads is a one-off nothing judges: the shape stamp was
        the only reader on the fast path, and a business decision stamped
        `one-off` was routed FLEET and would have been built. The pre-approval
        critic is the one agent step every one-off run reaches.

        DRE-5376 added the one other: when that critic sends the card back, the
        planner revises it. That step runs ONLY behind the one-off decision's
        `revise` action, never on the shape alone, so a one-off the critic
        passes, escalates or parks still asks exactly one model. Any other
        agent step reachable from the one-off route fails here.
        """
        for producer in ("First critic — round 1 decision", "Second critic — decision"):
            assert "steps.route.outputs.mode" in _step(producer)["if"], (
                f"{producer!r} must itself be mode-gated, or the steps that "
                "hang off it escape the branch"
            )
        # The revision hangs off the one-off decision, so the chain is walked
        # here too: the decision itself runs on the one-off shape alone.
        decision = _step("One-off critic — decision")
        assert decision["id"] == "oneoff"
        assert re.findall(r"steps\.shape\.outputs\.route == '([a-z-]+)'",
                          decision["if"]) == ["one-off"]
        revise_gate = "steps.oneoff.outputs.action == 'revise'"
        gated = ("steps.route.outputs.mode", "steps.pre1.outputs", "steps.post1.outputs")
        on_the_one_off_route = []
        for step in _steps():
            if str(step.get("uses", "")).startswith("anthropics/claude-code-action"):
                condition = step.get("if", "")
                shapes = re.findall(
                    r"steps\.shape\.outputs\.route == '([a-z-]+)'", condition)
                if shapes == ["one-off"] or "steps.oneoff.outputs" in condition:
                    on_the_one_off_route.append(step.get("name"))
                    continue
                assert any(g in condition for g in gated) or (
                    shapes and "one-off" not in shapes), (
                    f"agent step {step.get('name')!r} is gated on neither the "
                    f"mode nor a shape a one-off cannot be"
                )
        assert on_the_one_off_route == [
            "Pre-approval critic — the one-off exit",
            "One-off revision — the planner answers the critic",
        ], (
            "exactly two agent steps may run on the one-off route — the "
            "pre-approval critic (DRE-3041) and the planner's revision behind "
            f"its send-back (DRE-5376); found {on_the_one_off_route}"
        )
        assert _step("One-off revision — the planner answers the critic")["if"] \
            == revise_gate, (
                "the one-off revision must run on the critic's revise decision "
                "and nothing else, or a one-off the critic passed asks a second "
                "model"
            )
        for fragment in ("Plan artifact — check", "Plan artifact — upload source"):
            assert "steps.route.outputs.mode == 'plan'" in _step(fragment)["if"]
        publish = _jobs()["publish"]["if"]
        assert "needs.plan.outputs.mode == 'plan'" in publish

    def test_the_epic_still_stops_where_the_vocabulary_says_it_stops(self):
        """The epic path is UNCHANGED — the same step still hands Linear the
        same lane, which `test_green_light_rename.py` has pinned since the
        rename. What this adds is the binding: that lane is the one the shape
        vocabulary declares, so moving `epic` in the file turns this red rather
        than letting the two drift apart in silence.
        """
        # Since DRE-5284 the plan route hands the plan to the second critic and
        # writes no lane; the vocabulary's destination is written by the review
        # route's one Green Light step, after both critics passed it.
        handoff = _step("Plan → second critic")
        assert "linear_ops.py state" not in handoff["run"]
        assert "review_rerun.py dispatch" in handoff["run"]
        destination = planning_route.route_for("epic").destination
        writers = [s.get("name") for s in _steps()
                   if f'state "$EPIC" "{destination}"' in (s.get("run") or "")
                   and "steps.route.outputs.mode == 'review'" in str(s.get("if") or "")]
        assert writers == ["Epic → Green Light — both critics passed"], writers

    def test_the_plan_and_activate_split_is_untouched(self):
        """The CEO's two verbs still decide plan vs activate: the shape says
        WHICH route, never whether an approved epic activates."""
        route = _step("Route — plan or activate")["run"]
        assert "trigger_state" in route
        assert "mode=activate" in route and "mode=plan" in route

    def test_only_the_approval_move_activates(self):
        """DRE-3100: the activate branch reads the lane the card ENTERED and
        activates on In Progress alone. "Not Triage, and children exist" let a
        Planning entry activate DRE-3060 twice on 2026-09-04."""
        route = _step("Route — plan or activate")["run"]
        assert '"$FROM" = "in progress"' in route
        assert '"$FROM" != "triage"' not in route


# ===========================================================================
# 7: the one-off's verdict is READ FROM THE CARD, never defaulted (DRE-3038)
# ===========================================================================
#
# `is_epic()` used to answer "epic" for any card carrying `agent:planner`, and
# every card the relay dispatches to `plan.yml` from Planning carries it. So
# `routing_verdict.route()` answered "epic, no verdict" for every one-off, the
# role-label → title → criteria precedence never ran, and `_one_off_check` fell
# to a fixed FLEET sentence. DRE-3018 and DRE-3020 were both stamped that way
# with zero `- [ ]` items between them.
FD4B_TITLE = (
    "PROOF-FD-4b — a one-off card in Planning WITH agent:planner: expect a "
    "routing verdict and Backlog, never Green Light (throwaway, safe to cancel)"
)
FD4B_LABELS = ("repo:agent-bureau-demo", "agent:planner")
FD4B_BODY = (ROOT / "tests" / "fixtures" / "dre-3018-fd-4b-one-off-probe.md").read_text(
    encoding="utf-8"
)

# The sentence `_one_off_check` stamped on every one-off whatever the card said.
# Quoted here so its removal is ASSERTED rather than remembered.
DEFAULT_SENTENCE = (
    "the card is shaped one-off — one card, one pull request — and nothing in "
    "its acceptance criteria says a person has to drive it"
)


def _fd4b(criterion: str | None = None) -> dict:
    """DRE-3018's real body, optionally given the one criterion under test."""
    body = FD4B_BODY
    if criterion:
        body += f"\n\n## Acceptance criteria\n\n- [ ] {criterion}\n"
    return _read_card(
        "DRE-3018", title=FD4B_TITLE, description=body, labels=FD4B_LABELS
    )


class TestTheOneOffVerdictIsReadFromTheCard:
    def test_the_probe_body_as_filed_routes_needs_work(self):
        """DRE-3018 carries zero `- [ ]` items, and `criteria_verdict()` has
        always documented that as NEEDS WORK — "no exit condition to route
        on". It was stamped FLEET."""
        plan = planning_route.exit_plan(_fd4b(), [_stamp("one-off")])
        assert plan.verdict == "NEEDS WORK"
        assert "no acceptance criteria" in plan.reason.lower()

    def test_one_criterion_a_person_can_close_routes_to_the_fleet(self):
        plan = planning_route.exit_plan(
            _fd4b("the README names the date the demo pipeline was last exercised"),
            [_stamp("one-off")],
        )
        assert plan.verdict == planning_route.fleet_verdict()
        assert plan.escalation is None

    def test_a_criterion_naming_live_state_routes_to_the_fleet(self):
        """The card's own example: a one-off whose criteria say "observed in
        production" or "by hand". It routed WORKBENCH on the `interactive`
        signal until DRE-6227 retired it — six code cards in a week went to a
        lane where nobody builds them on a phrase like these. A criterion met
        only by watching the change run after it ships is a proof observation
        or a follow-up card, never a reason to hold the build, so the one-off
        is read like any other and goes to the fleet."""
        for criterion in ("the new README line is observed in production",
                          "the date is checked by hand against the last run"):
            plan = planning_route.exit_plan(_fd4b(criterion), [_stamp("one-off")])
            assert plan.verdict == "FLEET", criterion
            assert routing_verdict.is_promotable(plan.verdict), criterion

    def test_an_explicit_role_label_still_wins_over_the_criteria(self):
        """Precedence 1, on a card wearing `agent:planner` — the leg that never
        ran, because the label was read as epic-ness before anything else."""
        card = _read_card(
            "DRE-3018", title=FD4B_TITLE,
            description=FD4B_BODY + "\n\n- [ ] the README names the date\n",
            labels=(*FD4B_LABELS, "no-code"),
        )
        plan = planning_route.exit_plan(card, [_stamp("one-off")])
        assert plan.verdict == "OPERATOR"

    def test_the_reason_names_the_criterion_it_read(self):
        """The verdict comment is what a reader gets. It names the branch that
        actually decided — and where the criteria decided, the criterion."""
        criterion = "the README names the date the demo pipeline was last exercised"
        plan = planning_route.exit_plan(_fd4b(criterion), [_stamp("one-off")])
        assert criterion in plan.reason
        assert criterion in routing_verdict.verdict_comment(plan.verdict, plan.reason)

    def test_two_one_offs_with_different_criteria_get_different_reasons(self):
        """"Never a fixed sentence" is the property, so two cards that differ
        only in their criteria must not read back identically."""
        first = planning_route.exit_plan(
            _fd4b("the README names the date"), [_stamp("one-off")])
        second = planning_route.exit_plan(
            _fd4b("the health endpoint answers 200"), [_stamp("one-off")])
        assert first.verdict == second.verdict
        assert first.reason != second.reason

    def test_the_default_fleet_sentence_is_gone_from_the_module(self):
        source = (ROOT / "scripts" / "planning_route.py").read_text(encoding="utf-8")
        # Quotes and line breaks removed, so a sentence re-split across two
        # string literals does not slip past this.
        flat = " ".join(source.replace('"', " ").replace("'", " ").split())
        needle = " ".join(DEFAULT_SENTENCE.replace('"', " ").split())
        assert not (needle in flat), (
            "planning_route.py still carries the fixed FLEET sentence — the "
            "reason on a verdict is the branch that actually decided"
        )


class TestANeedsWorkOneOffDoesNotLeaveSilently:
    """DRE-2848: hand-planning is an escalation, and a one-off nothing can
    route is exactly that — the CEO (or the classifier, DRE-3029) gets it back
    with the reason, instead of a card landing in Backlog carrying a verdict
    that says it is not buildable."""

    def test_it_takes_the_escalation_exit(self):
        import planning_escalation

        plan = planning_route.exit_plan(_fd4b(), [_stamp("one-off")])
        assert plan.escalation is not None
        assert plan.destination == planning_escalation.destination()
        assert plan.destination != planning_shape.destination("one-off")

    def test_the_module_says_it_can_write_that_lane(self):
        """`ready_lane_writers.py` (DRE-2859) reads `destinations()` for every
        write whose lane is computed at the call site. `_cmd_exit` can now put
        a card in the escalation lane, so the census has to say so."""
        import planning_escalation

        assert planning_escalation.destination() in planning_route.destinations()

    def test_the_reason_it_escalates_with_is_fit_for_the_ceo(self):
        import planning_escalation

        plan = planning_route.exit_plan(_fd4b(), [_stamp("one-off")])
        assert planning_escalation.refusal(plan.escalation) is None, (
            "the escalation reason is put in front of the CEO — no code, no "
            "file paths, no commands"
        )

    def test_a_verdict_that_routes_to_a_person_still_leaves_normally(self):
        """WORKBENCH and OPERATOR are answers, not absences: somebody picks the
        card up in the build queue. Only "not buildable as written" comes back
        to the planning segment."""
        plan = planning_route.exit_plan(
            _fd4b("the new README line is observed in production"),
            [_stamp("one-off")],
        )
        assert plan.escalation is None
        assert plan.destination == planning_shape.destination("one-off")


class TestTheExitCommandEscalates:
    def test_a_criteria_less_one_off_is_escalated_and_never_stamped(self):
        import planning_escalation

        card = _Card([_stamp("one-off")], description=FD4B_BODY, labels=FD4B_LABELS)
        assert card.run(lambda: planning_route.main(["exit", CARD])) == 0

        assert routing_verdict.verdicts_on([b for _, b in card.posted]) == (), (
            "a card that cannot be routed carries no verdict — it has not left "
            "the planning segment"
        )
        assert planning_escalation.ESCALATION_TAG in card.bodies()
        assert planning_route.ROUTE_TAG not in card.bodies(), (
            "the route note says the card needs no green light, which is the "
            "opposite of what just happened"
        )
        assert card.states == [(CARD, planning_escalation.destination())]
        assert card.labelled == []

    def test_re_running_the_escalation_writes_nothing_twice(self):
        card = _Card([_stamp("one-off")], description=FD4B_BODY, labels=FD4B_LABELS)
        card.run(lambda: planning_route.main(["exit", CARD]))
        first = list(card.posted)
        card.run(lambda: planning_route.main(["exit", CARD]))
        assert card.posted == first

    def test_a_card_that_already_left_planning_is_left_there(self, capsys):
        """DRE-3654: the exit route's escalation is the same `escalate()`, so
        a card someone moved to be built before the run reached this step is
        not dragged back — and the run log says which way it went."""
        import planning_escalation

        card = _Card([_stamp("one-off")], description=FD4B_BODY,
                     labels=FD4B_LABELS, lane="In Progress")
        assert card.run(lambda: planning_route.main(["exit", CARD])) == 0
        assert card.states == []
        assert planning_escalation.STOOD_DOWN_TAG in card.bodies()
        out = capsys.readouterr().out
        assert "In Progress" in out
        assert "It is escalated to" not in out, out

    def test_a_buildable_one_off_is_still_stamped_and_moved(self):
        """Guard the guard: the ordinary path is untouched."""
        card = _Card([_stamp("one-off")])
        assert card.run(lambda: planning_route.main(["exit", CARD])) == 0
        assert routing_verdict.verdict_on([b for _, b in card.posted]) == "FLEET"
        assert card.states == [(CARD, planning_shape.destination("one-off"))]


# ===========================================================================
# 8: a card a declaration routes to a PERSON is routed before the critic
#    (DRE-5805)
# ===========================================================================
#
# DRE-5349 wore `agent:ops` and `no-code` and was stamped one-off — rightly:
# one card, one pull request. The pre-approval critic was then asked whether
# an agent could build it unattended, honestly answered no, and that no was a
# SEND_BACK: the card was revised for nothing, the revision did not finish, and
# it parked in Triage. The label precedence that would have routed it to a
# person ran only in the exit, which only the critic's pass reaches. So
# `decide` runs that same read on a one-off and says when it lands on a person.
#
# The fixtures carry only labels the router reads. `hand-built` is a mark the
# sweep stamps once a verdict has routed a card, not an input, so it is not one.
CLASSIFIED = ("repo:bureau-pipeline", "agent:planner")
ROUTINE_CRITERIA = (
    "## Acceptance criteria\n\n"
    "- [ ] the health endpoint answers 200 on both paths\n"
)


def _routing_vocabulary() -> dict:
    return json.loads((ROOT / "config" / "routing-verdicts.json").read_text(encoding="utf-8"))


def _decided(card: _Card, tmp_path) -> dict:
    out = tmp_path / "out.txt"
    assert card.run(
        lambda: planning_route.main(["decide", CARD, "--github-output", str(out)])
    ) == 0
    return dict(line.split("=", 1) for line in out.read_text().splitlines() if line)


class TestAPersonsCardIsRoutedBeforeTheCritic:
    def _person(self, labels, description=ROUTINE_CRITERIA, **kw) -> str:
        return planning_route.person_verdict(
            _read_card(description=description, labels=labels), **kw)

    def _assert_a_person(self, verdict: str):
        assert verdict, "the mechanical read routed this card to nobody"
        assert routing_verdict.actor(verdict) in planning_route.HUMAN_ACTORS

    def test_a_one_off_wearing_agent_ops_answers_operator_before_the_critic(self, tmp_path):
        labels = CLASSIFIED + ("agent:ops",)
        verdict = self._person(labels)
        assert verdict == "OPERATOR"
        self._assert_a_person(verdict)

        card = _Card([_stamp("one-off")], description=ROUTINE_CRITERIA, labels=labels)
        written = _decided(card, tmp_path)
        assert written[planning_route.PERSON_OUTPUT] == "OPERATOR"
        assert written["route"] == "one-off", "the shape is not the defect — it stays one-off"
        assert card.posted == [] and card.states == [], (
            "decide reads; the exit is still the one writer"
        )

    def test_a_one_off_wearing_no_code_answers_operator_before_the_critic(self, tmp_path):
        labels = CLASSIFIED + ("no-code",)
        verdict = self._person(labels)
        assert verdict == "OPERATOR"
        self._assert_a_person(verdict)

        card = _Card([_stamp("one-off")], description=ROUTINE_CRITERIA, labels=labels)
        assert _decided(card, tmp_path)[planning_route.PERSON_OUTPUT] == "OPERATOR"

    def test_an_engineer_one_off_answers_no_person_and_goes_to_the_critic(self, tmp_path):
        labels = CLASSIFIED + ("agent:engineer",)
        assert planning_route.mechanical_verdict(
            "a card", ROUTINE_CRITERIA, labels, shape="one-off")[0] == "FLEET"
        assert self._person(labels) == ""

        card = _Card([_stamp("one-off")], description=ROUTINE_CRITERIA, labels=labels)
        written = _decided(card, tmp_path)
        assert planning_route.PERSON_OUTPUT in written, (
            "the output is always written, so the gate never reads a stale one"
        )
        assert written[planning_route.PERSON_OUTPUT] == ""

    def test_a_card_sent_back_to_planning_answers_no_person(self):
        """NEEDS WORK is the read's answer for a card with no exit condition,
        and its actor is the planning run — the critic still reads it."""
        assert planning_route.mechanical_verdict(
            "a card", "Just do the thing.", CLASSIFIED, shape="one-off")[0] == "NEEDS WORK"
        assert self._person(CLASSIFIED, description="Just do the thing.") == ""

    def test_who_is_a_person_is_read_off_the_vocabulary(self):
        """Derived, never listed: hand OPERATOR to an agent in the file and an
        `agent:ops` card is a person's no longer, so the critic reads it."""
        doc = _routing_vocabulary()
        entry = next(v for v in doc["verdicts"] if v["name"] == "OPERATOR")
        entry["actor"] = routing_verdict.actor("FLEET")
        assert self._person(CLASSIFIED + ("agent:ops",), doc=doc) == ""

    def test_a_card_that_cannot_be_read_goes_to_the_critic(self, tmp_path):
        """The read fails open to today's absence: the critic still reads the
        card, and nothing is routed on a read that did not happen."""
        import critic_score

        card = _Card([_stamp("one-off")], labels=CLASSIFIED + ("agent:ops",))
        out = tmp_path / "out.txt"

        def failing():
            with patch.object(critic_score, "read_card",
                              side_effect=RuntimeError("Linear is down")):
                return planning_route.main(["decide", CARD, "--github-output", str(out)])
        assert card.run(failing) == 0
        written = dict(line.split("=", 1) for line in out.read_text().splitlines() if line)
        assert written[planning_route.PERSON_OUTPUT] == ""
        assert written["route"] == "one-off"

    def test_only_a_one_off_is_read(self, tmp_path):
        """An epic is never given a buildability verdict, and a roll-up is
        split before anything is built — neither is read, and neither is a
        person's card here."""
        import critic_score

        for shape in ("epic", "roll-up"):
            card = _Card([_stamp(shape)], labels=CLASSIFIED + ("agent:ops",))
            reads = []
            out = tmp_path / f"{shape}.txt"

            def decide():
                with patch.object(critic_score, "read_card",
                                  side_effect=lambda lops, i: reads.append(i)):
                    return planning_route.main(["decide", CARD, "--github-output", str(out)])
            assert card.run(decide) == 0
            written = dict(line.split("=", 1) for line in out.read_text().splitlines() if line)
            assert written[planning_route.PERSON_OUTPUT] == "", shape
            assert reads == [], f"decide read the card for a {shape}"

    def test_the_exit_stamps_the_verdict_the_label_decided(self):
        """From the decision on, nothing changes: the exit stamps OPERATOR,
        lands the card where every one-off lands, and the sweep carries it to
        a person as it carries any OPERATOR card."""
        card = _Card([_stamp("one-off")], description=ROUTINE_CRITERIA,
                     labels=CLASSIFIED + ("agent:ops",))
        assert card.run(lambda: planning_route.main(["exit", CARD])) == 0
        assert routing_verdict.verdict_on([b for _, b in card.posted]) == "OPERATOR"
        assert card.states == [(CARD, planning_shape.destination("one-off"))]


# ===========================================================================
# DRE-6651: an epic still to be planned whose formal blocker is open waits in
# Queued (Backlog until DRE-6750) instead of asking the CEO
# ===========================================================================
#
# DRE-6589 reached Planning blocked by DRE-6587 (In Progress), the planner
# could only ask "wait, or plan now?", and the CEO answered a question the
# board already held the answer to. `decide` reads the relation, the children
# and the lane history beside the shape and says `wait`; `plan.yml` posts the
# receipt, moves the epic to Queued and dispatches no planner.
REPLAY = ROOT / "tests" / "fixtures" / "green-light-2026-10-10" / "dre-6589.json"
REPLAY_KEYS = {"card", "arrival", "for", "as_of", "lane", "labels", "comments"}
#: The `Route — plan or activate` gate at `main` before this card.
ROUTE_GATE = (
    "steps.gate.outputs.bounced != 'true' && steps.dedupe.outputs.skip != 'true' "
    "&& steps.slot.outputs.admitted == 'true' && steps.shape.outputs.route == 'epic'"
)
#: The `decide` step's gate, which `planning_escalation.workflow_problems`
#: reads: unchanged by this card.
DECIDE_GATE = (
    "steps.gate.outputs.bounced != 'true' && steps.dedupe.outputs.skip != 'true' "
    "&& steps.slot.outputs.admitted == 'true' && steps.classify.outputs.escalate != 'true' "
    "&& steps.classify.outputs.requeue != 'true'"
)
WAIT_STEP = "Epic waits on its blocker — Queued, no planner"
WAIT_GATE = "steps.shape.outputs.route == 'epic' && steps.shape.outputs.wait == 'true'"
WAIT_ACT = "epic-waits-on-blocker"


def _replay() -> dict:
    return json.loads(REPLAY.read_text(encoding="utf-8"))


def _replayed(tmp_path, capsys, **overrides) -> tuple:
    """DRE-6589 as `decide` read it, with Linear answering the fixture's
    relations, children and history — `overrides` changes any of them."""
    doc = _replay()
    relations = doc["relations"]
    kw = {
        "blocked_by": relations["blocked_by"],
        "has_children": relations["has_children"],
        "history": relations["history"],
        "labels": doc["labels"],
        "lane": doc["lane"],
    }
    kw.update(overrides)
    card = _Card([c["body"] for c in doc["comments"]], **kw)
    out = tmp_path / "out.txt"
    capsys.readouterr()
    assert card.run(
        lambda: planning_route.main(["decide", doc["card"], "--github-output", str(out)])
    ) == 0
    said = capsys.readouterr()
    written = dict(line.split("=", 1) for line in out.read_text().splitlines() if line)
    return written, said.out + said.err, card


class TestTheReplayFixture:
    def test_the_fixture_carries_the_shared_contract_and_the_relations_extra(self):
        doc = _replay()
        assert REPLAY_KEYS <= set(doc)
        assert doc["card"] == "DRE-6589"
        assert doc["arrival"] == "blocked-epic"
        assert doc["for"] == "planner"
        assert doc["lane"] == "Planning"
        assert doc["as_of"].endswith("Z")
        for row in doc["comments"]:
            assert set(row) == {"body", "authored_by_pipeline", "created_at"}
            assert row["created_at"] <= doc["as_of"]
        relations = doc["relations"]
        assert relations["parent"] == "DRE-6585"
        assert relations["has_children"] is False
        assert relations["blocked_by"] == [{"identifier": "DRE-6587", "state": "In Progress"}]
        assert isinstance(relations["history"], list)
        assert not [row for row in relations["history"]
                    if (row.get("toState") or {}).get("name") == "In Progress"]
        assert planning_shape.shape_on([c["body"] for c in doc["comments"]]) == "epic"


class TestABlockedEpicWaits:
    def test_dre_6589_waits_on_dre_6587(self, tmp_path, capsys):
        written, said, card = _replayed(tmp_path, capsys)
        assert written["route"] == "epic"
        assert written["wait"] == "true"
        assert written["waits_on"] == "DRE-6587 (In Progress)"
        assert "DRE-6587 (In Progress)" in said
        assert card.posted == [] and card.states == [], (
            "decide reads; plan.yml's wait step is the writer"
        )

    def test_the_wait_line_names_the_queue_the_wait_step_parks_in(
            self, tmp_path, capsys, monkeypatch):
        """DRE-6747: the epic waits in `QUEUE_LANE`, read when the line is
        printed — so flipping the planning exit moves the words with it."""
        monkeypatch.setattr(lane_contract, "QUEUE_LANE", "Queued")
        _, said, _ = _replayed(tmp_path, capsys)
        assert "DRE-6589 waits in Queued — " in said

    def test_the_same_epic_is_planned_once_its_blocker_is_done(self, tmp_path, capsys):
        written, _, _ = _replayed(
            tmp_path, capsys,
            blocked_by=[{"identifier": "DRE-6587", "state": "Done"}])
        assert written["route"] == "epic"
        assert written["wait"] == "false"
        assert written["waits_on"] == ""

    @pytest.mark.parametrize("state", ["Canceled", "Duplicate"])
    def test_every_terminal_blocker_is_met(self, tmp_path, capsys, state):
        written, _, _ = _replayed(
            tmp_path, capsys,
            blocked_by=[{"identifier": "DRE-6587", "state": state}])
        assert written["wait"] == "false"

    def test_an_epic_with_no_blockers_is_planned(self, tmp_path, capsys):
        written, _, _ = _replayed(tmp_path, capsys, blocked_by=[])
        assert written["wait"] == "false"

    def test_every_open_blocker_is_named_with_its_state(self, tmp_path, capsys):
        written, _, _ = _replayed(tmp_path, capsys, blocked_by=[
            {"identifier": "DRE-6587", "state": "In Progress"},
            {"identifier": "DRE-6500", "state": "Done"},
            {"identifier": "DRE-6401", "state": "Todo"},
        ])
        assert written["wait"] == "true"
        assert written["waits_on"] == "DRE-6401 (Todo), DRE-6587 (In Progress)"

    def test_an_amended_epic_with_children_and_a_green_light_is_not_held(self, tmp_path, capsys):
        """DRE-6407 releases an approved epic at its blocker's build-done, and
        `mid_epic.py discovery --kind amendment` sends exactly such an epic
        back to Planning. Holding it in Backlog would park running work with
        nothing to bring it back."""
        history = [
            {"createdAt": "2026-10-10T12:00:00.000Z", "toState": {"name": "Planning"}},
            {"createdAt": "2026-10-09T08:00:00.000Z", "toState": {"name": "In Progress"}},
            {"createdAt": "2026-10-08T08:00:00.000Z", "toState": {"name": "Green Light"}},
        ]
        written, said, _ = _replayed(tmp_path, capsys, has_children=True, history=history)
        assert written["wait"] == "false"
        assert written["waits_on"] == ""
        assert "children" in said
        assert "In Progress" in said and "green light" in said

    def test_a_green_light_alone_is_enough_to_plan(self, tmp_path, capsys):
        history = [{"createdAt": "2026-10-09T08:00:00.000Z", "toState": {"name": "In Progress"}}]
        written, said, _ = _replayed(tmp_path, capsys, history=history)
        assert written["wait"] == "false"
        assert "green light" in said

    def test_children_alone_are_enough_to_plan(self, tmp_path, capsys):
        written, said, _ = _replayed(tmp_path, capsys, has_children=True)
        assert written["wait"] == "false"
        assert "children" in said

    def test_a_history_read_that_fails_holds_nothing(self, tmp_path, capsys):
        written, said, _ = _replayed(tmp_path, capsys, history_fail=True)
        assert written["route"] == "epic"
        assert written["wait"] == "false"
        assert "history" in said and "Linear is down" in said

    def test_a_relation_or_children_read_that_fails_holds_nothing(self, tmp_path, capsys):
        written, said, _ = _replayed(tmp_path, capsys, relations_fail=True)
        assert written["route"] == "epic"
        assert written["wait"] == "false"
        assert "relations" in said and "Linear is down" in said

    def test_a_one_off_never_waits_and_reads_no_relations(self, tmp_path, capsys):
        card = _Card([_stamp("one-off")], blocked_by=[
            {"identifier": "DRE-6587", "state": "In Progress"}], relations_fail=True)
        written = _decided(card, tmp_path)
        assert written["route"] == "one-off"
        assert written["wait"] == "false"


def _gate(step: dict) -> str:
    return " ".join(str(step.get("if") or "").split())


class TestPlanYmlParksAWaitingEpic:
    def test_the_wait_step_sits_between_the_shape_and_the_route(self):
        assert _index("Planning shape") < _index(WAIT_STEP) < _index("Route — plan or activate")
        assert _gate(_step(WAIT_STEP)) == WAIT_GATE

    def test_the_wait_step_posts_the_receipt_then_moves_the_epic_to_queued(self):
        # DRE-6750: the write and the receipt name the queue by its new name.
        run = _step(WAIT_STEP)["run"]
        assert f"--act={WAIT_ACT}" in run
        assert "⏸️ epic-waits:" in run
        assert "waits in Queued on its formal blocker(s)" in run
        assert "steps.shape.outputs.waits_on" in json.dumps(_step(WAIT_STEP))
        assert 'linear_ops.py state "$EPIC" "Queued"' in run
        assert run.index(f"--act={WAIT_ACT}") < run.index('state "$EPIC" "Queued"')
        assert "Backlog" not in run
        for absent in ("plan-cycle", "cycle-start", "open_attempt", "Plan heartbeat"):
            assert absent not in run

    def test_route_carries_the_one_new_clause(self):
        assert _gate(_step("Route — plan or activate")) == (
            ROUTE_GATE + " && steps.shape.outputs.wait != 'true'")

    def test_the_decide_step_keeps_its_gate(self):
        assert _gate(_step("Planning shape")) == DECIDE_GATE
        import planning_escalation
        assert planning_escalation.workflow_problems(WF.read_text(encoding="utf-8")) == []

    def test_no_other_step_reads_the_wait(self):
        readers = [s.get("name") for s in _steps()
                   if "steps.shape.outputs.wait" in json.dumps(s)]
        assert sorted(readers) == sorted([WAIT_STEP, "Route — plan or activate"])

    def test_route_is_the_only_other_step_keyed_on_the_epic_shape(self):
        keyed = [s.get("name") for s in _steps()
                 if "steps.shape.outputs.route == 'epic'" in _gate(s)]
        assert sorted(keyed) == sorted([WAIT_STEP, "Route — plan or activate"])

    def test_every_mode_keyed_step_sits_behind_the_gated_route(self):
        """No planner, no `plan-cycle: start`, no heartbeat, no critic: each of
        them keys on `steps.route.outputs.mode`, which `Route` alone writes and
        which is unset when the wait skips it."""
        steps = _jobs()["plan"]["steps"]
        route = _index("Route — plan or activate")
        assert steps[route].get("id") == "route"
        mode_keyed = [i for i, s in enumerate(steps)
                      if "steps.route.outputs.mode" in _gate(s)]
        assert mode_keyed and min(mode_keyed) > route
        # The `plan-cycle: start` boundary is written by `Route`, or by a step
        # keyed on the mode it writes.
        writers = [s for s in steps if "cycle-start" in (s.get("run") or "")]
        assert writers[0].get("id") == "route"
        for step in writers[1:]:
            assert "steps.route.outputs.mode" in _gate(step), step.get("name")
        # Every planner and critic step keys on the mode, or on a shape the
        # wait never reads (the one-off and roll-up routes, and the one-off
        # critic's decision behind them).
        for step in steps:
            if str(step.get("uses", "")).startswith("anthropics/claude-code-action"):
                gate = _gate(step)
                assert ("steps.route.outputs.mode" in gate
                        or "steps.shape.outputs.route == 'one-off'" in gate
                        or "steps.shape.outputs.route == 'roll-up'" in gate
                        or "steps.oneoff.outputs" in gate
                        or "steps.oorevised.outputs" in gate
                        or "steps.pre1.outputs" in gate
                        or "steps.post1.outputs" in gate), step.get("name")
        assert _gate(_step("Plan heartbeat")) == "steps.route.outputs.mode == 'plan'"

    def test_the_planner_slot_is_still_claimed_before_and_released_always(self):
        assert _index("Planner slot — claim or wait") < _index("Planning shape")
        assert _index("Classify the card") < _index("Planning shape")
        assert _gate(_step("Planner slot — release")) == (
            "always() && steps.slot.outputs.admitted == 'true'")


class TestTheWaitActIsDeclared:
    def test_the_act_row_is_in_the_shape_of_epic_approval_queued(self):
        acts = json.loads((ROOT / "config" / "pipeline-acts.json").read_text(encoding="utf-8"))
        rows = [a for a in acts["acts"] if a["name"] == WAIT_ACT]
        assert len(rows) == 1
        row = rows[0]
        assert row["tag"] == "epic-waits"
        assert row["kind"] == "hold"
        assert row["state"] == "held"
        assert row["next_actor"] == "reconcile.py"
        assert row["discharges"] is None
        assert row["subscriber"] == "plan.yml"
        assert row["cadence_s"] is None and row["cadence_why"].strip()
        assert row["emits"]["file"] == ".github/workflows/plan.yml"
        assert f"--act={WAIT_ACT}" in row["emits"]["anchor"]

    def test_the_act_means_names_the_queue_the_receipt_names(self):
        # DRE-6750: `means` is what the console says for this act, so it
        # names the lane the receipt and the write name.
        acts = json.loads((ROOT / "config" / "pipeline-acts.json").read_text(encoding="utf-8"))
        row = next(a for a in acts["acts"] if a["name"] == WAIT_ACT)
        assert "waits in Queued" in row["means"]
        assert "Backlog" not in row["means"]

    def test_the_registry_checks_clean(self):
        import pipeline_act
        assert pipeline_act.problems() == []
