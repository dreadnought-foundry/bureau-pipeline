"""The Planning stall exit leaves an epic under review to the re-review watcher,
and parks every other stalled card in Triage, never Green Light (DRE-5286,
epic DRE-5268).

THE TWO WRONG DESTINATIONS. `reconcile.flag_stalled_planning` moved a Planning
card nothing had happened to for `PLANNING_MINUTES` into Green Light, saying
"planning has produced nothing". Under DRE-5268 that is wrong twice over:

  * an epic the plan route handed to the second critic sits in Planning with
    the first critic's PASS on it, and if the hand-off drops the watchdog put
    a plan the second critic never read in front of the CEO — the row the epic
    exists to forbid. That epic belongs to the re-review watcher (DRE-5278);
  * a card that stalled with no record at all has been read by no planner and
    no critic, so it is not a decision either. It belongs in Triage, the
    operator's queue.

THE RULES UNDER TEST:

  1. A stalled Planning card with no critic record on its current attempt is
     parked in Triage, the `🧹 planning-stall-park` note landing first and
     still being the newest comment when the move lands, no label added and no
     write naming Green Light.
  2. A stalled Planning epic whose current attempt carries a first-critic PASS
     and nothing after it, or any second-critic round or tombstone, is left in
     Planning with one line naming the watcher as its owner.
  3. One whose newest attempt record is a first-critic SEND_BACK is a stall.
  4. The planner line's rule runs before the age gate, so an epic under review
     that has waited in line past the bound is parked by THAT rule.
  5. The sweep hands `rereview_watch.report` this repo's Planning epics with
     children beside the active ones, and a lane reader that answers for them
     — and the skip in rule 2 covers no card outside that set, so an
     unlabelled or off-rail epic under review is parked, never stranded.

Run: cd bureau-pipeline && python3 -m pytest tests/test_planning_stall_leaves_reviews.py -v
"""
from __future__ import annotations

import inspect
import os
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")
os.environ.setdefault("GH_TOKEN", "x")

import dedupe_dispatch  # noqa: E402
import plan_critic  # noqa: E402
import planner_queue  # noqa: E402
import planning_escalation  # noqa: E402
import reconcile  # noqa: E402
import rereview_watch  # noqa: E402
import validate_card  # noqa: E402

CARD = "DRE-9301"
EPIC = "DRE-9300"
BOUND = 360  # the planner line's bound, pinned in test_planner_queue_escalation
STALE = 180  # past PLANNING_MINUTES (120), inside the line's bound

#: Who wrote a comment, as the board read's `user { id }` carries it.
FLEET = "fleet-user"
PERSON = "a-person"

GREEN_LIGHT = "Green Light"


@pytest.fixture(autouse=True)
def _pin_repo_slug(monkeypatch):
    monkeypatch.setattr(reconcile, "REPO_SLUG", "agent-bureau")


@pytest.fixture(autouse=True)
def _pin_valid_slugs(monkeypatch):
    monkeypatch.setattr(
        validate_card, "VALID_SLUGS", {"agent-bureau", "atlas", "bureau-pipeline"}
    )


@pytest.fixture(autouse=True)
def _pin_live_snapshot(monkeypatch):
    monkeypatch.setattr(
        reconcile, "live_rail_slugs",
        lambda: frozenset({"agent-bureau", "atlas", "bureau-pipeline"}),
        raising=False,
    )


@pytest.fixture(autouse=True)
def _clean_ledgers():
    reconcile._write_failures.clear()
    yield
    reconcile._write_failures.clear()


def _iso(minutes_ago: float) -> str:
    return (
        (datetime.now(UTC) - timedelta(minutes=minutes_ago))
        .isoformat()
        .replace("+00:00", "Z")
    )


# --- the records -------------------------------------------------------------
# Every record is composed by the module that owns its grammar, never written
# out here, so a grammar change cannot leave these tests reading a string
# nothing posts.
def _boundary(epic: str = EPIC) -> str:
    return plan_critic.cycle_marker(epic)


def _pre(result: str = plan_critic.PASS) -> str:
    reason = "the plan names no owner for step 3" if result == plan_critic.SEND_BACK else ""
    return plan_critic.marker(plan_critic.STAGE_PRE, 1, result, reason)


def _post(result: str = plan_critic.SEND_BACK) -> str:
    return plan_critic.marker(plan_critic.STAGE_POST, 1, result,
                              "the rollback step is missing")


def _tombstone() -> str:
    return plan_critic.death_marker(plan_critic.STAGE_POST, "36000000001", 1,
                                    "post", "error_max_turns", 141, 140)


def _waiting(minutes_ago: float, card: str = EPIC) -> str:
    return planner_queue.format_receipt(
        "waiting", card=card, run=f"run-waiting-{int(minutes_ago)}",
        repo="dreadnought-foundry/agent-bureau", trigger="Planning",
        at=_iso(minutes_ago), place=2, of=5,
    )


def _card(identifier=CARD, state="Planning", labels=(), minutes_stale=STALE,
          bodies=(), children=0):
    """A card as `active_cards` returns it: comments inline, NEWEST FIRST (the
    order Linear answers a `comments(first:)` window in), each carrying its
    author the way `COMMENT_FIELDS` selects it. A body is a string (written by
    the pipeline) or a `(body, author)` pair."""
    nodes = []
    for n, entry in enumerate(bodies):
        body, author = entry if isinstance(entry, tuple) else (entry, FLEET)
        parsed = planner_queue.parse_receipt(body)
        created = parsed.at if parsed else _iso(minutes_stale + len(bodies) - n)
        nodes.append({"body": body, "createdAt": created, "user": {"id": author}})
    return {
        "id": f"uuid-{identifier}",
        "identifier": identifier,
        "title": "a card in Planning",
        "description": "work",
        "createdAt": _iso(minutes_stale),
        "updatedAt": _iso(minutes_stale),
        "state": {"name": state},
        "labels": {"nodes": [{"name": n} for n in labels]},
        "children": {"nodes": [{"id": f"child-{i}"} for i in range(children)]},
        "comments": {"nodes": list(reversed(nodes))},
    }


#: This repo's label: the watcher is handed only this repo's epics, so only
#: they can be left to it (`reconcile._watched_planning_epic`).
MINE = ("repo:agent-bureau",)


def _epic(*bodies, minutes_stale=STALE, children=2, labels=MINE, **kw):
    return _card(identifier=EPIC, minutes_stale=minutes_stale,
                 bodies=bodies, children=children, labels=labels, **kw)


class _Board:
    """One Linear board the watchdog reads and writes, every write recorded in
    order. It stands in for Linear and never for the sweep's own logic.

    `comment_records` is answered the way a sweep's pass answers it: off the
    board read's own window, with `authored_by_pipeline` true exactly for the
    fleet user — so a thread read costs no request and a stranger's comment
    is not a record."""

    def __init__(self, *cards):
        self.cards = list(cards)
        self.posted: list[tuple[str, str]] = []
        self.states: list[tuple[str, str]] = []
        self.added: list[tuple[str, str]] = []
        self.events: list[tuple[str, str]] = []
        #: The card's newest comment at the moment each state write landed.
        self.newest_at_move: list[str] = []
        self.thread_reads: list[str] = []

    def active_cards(self, states=reconcile.SWEEP_STATES):
        return [c for c in self.cards if c["state"]["name"] in states]

    def _find(self, identifier):
        return next(c for c in self.cards if c["identifier"] == identifier)

    def cmd_comment(self, identifier, body, *rest):
        self.posted.append((identifier, body))
        self.events.append(("comment", identifier))
        self._find(identifier)["comments"]["nodes"].insert(
            0, {"body": body, "createdAt": _iso(0), "user": {"id": FLEET}})

    def cmd_state(self, identifier, lane, *rest):
        self.states.append((identifier, lane))
        self.events.append(("state", identifier))
        nodes = self._find(identifier)["comments"]["nodes"]
        self.newest_at_move.append(nodes[0]["body"] if nodes else "")
        self._find(identifier)["state"]["name"] = lane

    def add_label(self, identifier, label):
        self.added.append((identifier, label))

    def comment_records(self, identifier, **kw):
        self.thread_reads.append(identifier)
        return [
            {"body": n.get("body") or "",
             "authored_by_pipeline": (n.get("user") or {}).get("id") == FLEET,
             "created_at": n.get("createdAt")}
            for n in reversed(self._find(identifier)["comments"]["nodes"])
        ]

    def lane(self, identifier=CARD) -> str:
        return self._find(identifier)["state"]["name"]

    def notes(self, identifier=CARD) -> list[str]:
        return [b for i, b in self.posted
                if i == identifier and dedupe_dispatch.STALL_PARK_TAG in b]

    def run(self, fn, records=None):
        def no_per_card(identifier, *a, **kw):
            raise AssertionError(
                f"the sweep fetched {identifier} one card at a time — the "
                "comments come with the board read (DRE-2929)")

        def no_question(*a, **kw):
            raise AssertionError(
                "the stall exit called planning_escalation.escalate — that "
                "seam is the planner's business question, and Green Light")

        with patch.object(
            reconcile, "active_cards", side_effect=self.active_cards
        ), patch.object(
            reconcile, "_open_pr_listing", side_effect=lambda: []
        ), patch.object(
            planning_escalation, "escalate", side_effect=no_question
        ), patch.object(
            reconcile.linear_ops, "cmd_comment", side_effect=self.cmd_comment
        ), patch.object(
            reconcile.linear_ops, "cmd_state", side_effect=self.cmd_state
        ), patch.object(
            reconcile.linear_ops, "add_label", side_effect=self.add_label
        ), patch.object(
            reconcile.linear_ops, "comment_records",
            side_effect=records or self.comment_records,
        ), patch.object(
            reconcile.linear_ops, "get_issue", side_effect=no_per_card
        ), patch.object(
            reconcile.linear_ops, "comment_bodies", side_effect=no_per_card
        ), patch.object(
            reconcile.linear_ops, "count_comments", side_effect=no_per_card
        ):
            return fn()

    def watch(self, **kw):
        """The WHOLE watchdog: `flag_stranded` owns both passes."""
        return self.run(reconcile.flag_stranded, **kw)


OWNER_LINE = "watchdog: {} is with the critics — the re-review watcher owns it"


# ===========================================================================
# 1. No critic record: parked in Triage, never Green Light
# ===========================================================================
class TestAStalledCardParksInTriage:
    def test_it_is_moved_to_triage(self):
        board = _Board(_card())
        assert board.watch() == {CARD}
        assert board.states == [(CARD, reconcile.PARKED_STATE)]
        assert reconcile.PARKED_STATE == "Triage"
        assert board.lane() == "Triage"

    def test_no_write_names_green_light(self):
        board = _Board(_card())
        board.watch()
        assert all(lane != GREEN_LIGHT for _, lane in board.states)

    def test_no_label_is_added(self):
        board = _Board(_card())
        board.watch()
        assert board.added == []

    def test_the_note_lands_before_the_move(self):
        board = _Board(_card())
        board.watch()
        assert board.events == [("comment", CARD), ("state", CARD)]

    def test_the_note_is_the_newest_comment_when_the_move_lands(self):
        """The relay dispatches a plan run the moment an `agent:planner` card
        enters Triage, and the plan-gate refuses it only because the card's
        NEWEST comment carries the tag (DRE-5277). Anything posted between
        the note and the move would re-plan the card on every stall."""
        board = _Board(_card(labels=("agent:planner",)))
        board.watch()
        (newest,) = board.newest_at_move
        assert dedupe_dispatch.parked_for_a_person([], newest)

    def test_the_note_opens_with_the_broom_and_carries_the_tag(self):
        board = _Board(_card())
        board.watch()
        (note,) = board.notes()
        assert note.startswith(f"🧹 {dedupe_dispatch.STALL_PARK_TAG}")

    def test_the_note_is_written_for_the_operator(self):
        board = _Board(_card())
        board.watch()
        (note,) = board.notes()
        assert str(reconcile.PLANNING_MINUTES) in note
        assert "Triage" in note
        assert "operator" in note
        assert "Planning" in note, "the way back is named"
        assert "in front of you" not in note
        assert planning_escalation.ESCALATION_TAG not in note

    def test_the_planners_question_seam_is_not_called(self):
        """`_Board.run` makes `planning_escalation.escalate` raise: reaching
        it would turn this into a failure rather than a park."""
        board = _Board(_card())
        assert board.watch() == {CARD}
        assert reconcile._write_failures == []

    def test_the_tag_is_read_from_dedupe_dispatch_never_written_here(self):
        source = (ROOT / "scripts" / "reconcile.py").read_text(encoding="utf-8")
        assert dedupe_dispatch.STALL_PARK_TAG not in source
        assert "dedupe_dispatch.STALL_PARK_TAG" in source


class TestOncePerPlanningAttempt:
    def test_a_note_already_on_this_attempt_is_not_posted_again(self):
        """The crash between the two writes: the note landed, the move did
        not. The retry re-asserts the move and posts nothing."""
        note = reconcile.stall_park_note(CARD, reconcile.stalled_planning_reason())
        board = _Board(_card(bodies=[_boundary(CARD), note]))
        board.run(lambda: reconcile.escalate_out_of_planning(
            board.cards[0], reconcile.stalled_planning_reason()))
        assert board.posted == []
        assert board.states == [(CARD, reconcile.PARKED_STATE)]

    def test_a_note_from_a_spent_attempt_does_not_silence_this_one(self):
        note = reconcile.stall_park_note(CARD, reconcile.stalled_planning_reason())
        board = _Board(_card(bodies=[note, _boundary(CARD)]))
        board.run(lambda: reconcile.escalate_out_of_planning(
            board.cards[0], reconcile.stalled_planning_reason()))
        assert len(board.notes()) == 1
        assert board.states == [(CARD, reconcile.PARKED_STATE)]

    def test_a_failed_move_is_recorded_and_reads_as_not_moved(self):
        board = _Board(_card())

        def refuse(identifier, lane, *rest):
            raise reconcile.linear_ops.LinearError("Linear said no")

        with patch.object(reconcile.linear_ops, "cmd_comment"), patch.object(
            reconcile.linear_ops, "cmd_state", side_effect=refuse
        ):
            moved = reconcile.escalate_out_of_planning(
                board.cards[0], reconcile.stalled_planning_reason())
        assert moved is False
        assert reconcile._write_failures


# ===========================================================================
# 2. Under review: left to the re-review watcher
# ===========================================================================
class TestAnEpicUnderReviewIsLeftToTheWatcher:
    def test_a_first_critic_pass_and_nothing_after_is_left(self, capsys):
        """The dropped hand-off: the second critic never read this plan."""
        board = _Board(_epic(_boundary(), _pre(plan_critic.PASS)))
        assert board.watch() == set()
        assert board.posted == [] and board.states == []
        assert board.lane(EPIC) == "Planning"
        out = capsys.readouterr().out
        assert out.splitlines().count(OWNER_LINE.format(EPIC)) == 1, out

    def test_a_second_critic_round_is_left(self, capsys):
        board = _Board(_epic(_boundary(), _pre(), _post()))
        assert board.watch() == set()
        assert board.states == []
        assert OWNER_LINE.format(EPIC) in capsys.readouterr().out

    def test_a_second_critic_tombstone_is_left(self, capsys):
        board = _Board(_epic(_boundary(), _pre(), _tombstone()))
        assert board.watch() == set()
        assert board.states == []
        assert OWNER_LINE.format(EPIC) in capsys.readouterr().out

    def test_the_predicate_is_the_watchers_own(self):
        """Guard the fixtures: each one is what `under_review` says it is."""
        board = _Board(_epic(_boundary(), _pre()))
        records = board.comment_records(EPIC)
        assert rereview_watch.under_review(records, EPIC) is True


class TestEverythingElseIsAStall:
    def test_a_first_critic_send_back_as_the_newest_record_is_a_stall(self):
        board = _Board(_epic(_boundary(), _pre(plan_critic.SEND_BACK)))
        assert board.watch() == {EPIC}
        assert board.states == [(EPIC, reconcile.PARKED_STATE)]

    def test_a_pass_from_a_spent_attempt_is_a_stall(self):
        board = _Board(_epic(_boundary(), _pre(), _post(), _boundary()))
        assert board.watch() == {EPIC}
        assert board.states == [(EPIC, reconcile.PARKED_STATE)]

    def test_a_pass_nobody_in_the_pipeline_wrote_is_a_stall(self):
        """The record's credential is the pipeline's authorship: a PASS
        anyone else posts puts nothing under review."""
        board = _Board(_epic(_boundary(), (_pre(), PERSON)))
        assert board.watch() == {EPIC}
        assert board.states == [(EPIC, reconcile.PARKED_STATE)]

    def test_a_card_without_children_is_a_stall_whatever_it_carries(self):
        """The watcher is handed Planning epics WITH children (rule 5), so the
        skip covers exactly those: a card the watcher is never shown is never
        left to it, and its thread is not read."""
        board = _Board(_card(bodies=[_boundary(CARD), _pre()]))
        assert board.watch() == {CARD}
        assert board.states == [(CARD, reconcile.PARKED_STATE)]
        assert board.thread_reads == []

    @pytest.mark.parametrize("labels", [(), ("repo:not-on-rail",)],
                             ids=["unlabelled", "off-rail"])
    def test_an_epic_under_review_no_watcher_is_handed_is_a_stall(
            self, labels, capsys):
        """This sweep keeps an unlabelled or off-rail epic as everybody's, but
        no repo's watcher is handed it — so leaving it to "the watcher" would
        strand it in Planning with nobody chasing it. It is parked instead,
        and its thread is not read."""
        board = _Board(_epic(_boundary(), _pre(), labels=labels))
        assert board.watch() == {EPIC}
        assert board.states == [(EPIC, reconcile.PARKED_STATE)]
        assert board.thread_reads == []
        assert OWNER_LINE.format(EPIC) not in capsys.readouterr().out

    def test_a_young_epic_costs_no_thread_read(self):
        board = _Board(_epic(_boundary(), _pre(), minutes_stale=10))
        assert board.watch() == set()
        assert board.thread_reads == []

    def test_an_unreadable_thread_parks_nothing_this_sweep(self, capsys):
        board = _Board(_epic(_boundary(), _pre()))

        def unreadable(identifier, **kw):
            raise reconcile.linear_ops.LinearError("timed out")

        assert board.watch(records=unreadable) == set()
        assert board.posted == [] and board.states == []
        assert EPIC in capsys.readouterr().err


# ===========================================================================
# 4. The line's rule runs first
# ===========================================================================
class TestTheLinesRuleRunsBeforeTheReviewSkip:
    def test_an_epic_under_review_past_the_lines_bound_is_parked_by_the_line(self, capsys):
        board = _Board(_epic(_boundary(), _pre(), _waiting(BOUND + 1),
                             minutes_stale=BOUND + 1))
        records = board.comment_records(EPIC)
        assert rereview_watch.under_review(records, EPIC) is True
        assert board.watch() == {EPIC}
        assert board.states == [(EPIC, reconcile.PARKED_STATE)]
        (note,) = board.notes(EPIC)
        assert "waiting in line for a planner" in note
        assert OWNER_LINE.format(EPIC) not in capsys.readouterr().out

    def test_the_lines_release_lands_before_the_note(self):
        """The park ends the card's place in line (DRE-5378), and that release
        is a comment: posted after the note it would be the newest comment on
        the card when it enters Triage, and the plan-gate would let the relay
        re-plan it."""
        board = _Board(_epic(_waiting(BOUND + 1), minutes_stale=BOUND + 1,
                             labels=MINE + ("agent:planner",)))
        assert board.watch() == {EPIC}
        (newest,) = board.newest_at_move
        assert dedupe_dispatch.STALL_PARK_TAG in newest
        released = [b for _, b in board.posted
                    if planner_queue.parse_receipt(b) is not None]
        assert len(released) == 1


# ===========================================================================
# 5. The watcher is handed the Planning epics
# ===========================================================================
class TestTheWatcherIsHandedPlanningEpics:
    def _report(self, board, epics):
        """What the sweep's phase hands `rereview_watch.report`: the scope
        it computes off the board read (the phase's call is pinned below and
        by test_rereview_watch's wiring test)."""
        with patch.object(reconcile, "active_cards",
                          side_effect=board.active_cards):
            watched, lane_of = reconcile.rereview_watch_scope(epics)
        return {"epics": set(watched), "lane": lane_of}

    def test_a_planning_epic_with_children_is_handed_with_its_lane(self):
        board = _Board(_epic(labels=("repo:agent-bureau",)),
                       _card(identifier="DRE-9302", state="In Progress",
                             labels=("repo:agent-bureau",), children=1))
        handed = self._report(board, {"DRE-9302"})
        assert handed["epics"] == {EPIC, "DRE-9302"}
        assert handed["lane"](EPIC) == "Planning"
        assert handed["lane"]("DRE-9302") == "In Progress"

    def test_a_planning_card_without_children_is_not_handed(self):
        board = _Board(_card(labels=("repo:agent-bureau",), children=0))
        handed = self._report(board, set())
        assert CARD not in handed["epics"]

    def test_another_repos_planning_epic_is_not_handed(self):
        board = _Board(_epic(labels=("repo:atlas",)))
        handed = self._report(board, set())
        assert EPIC not in handed["epics"]

    @pytest.mark.parametrize("labels", [
        MINE, (), ("repo:not-on-rail",), ("repo:atlas",),
        MINE + (reconcile.dependabot_card.LABEL,),
    ], ids=["mine", "unlabelled", "off-rail", "another-repo", "automation"])
    @pytest.mark.parametrize("children", [0, 2])
    def test_every_card_the_skip_leaves_to_the_watcher_is_handed_to_it(
            self, labels, children):
        """The invariant the skip rests on: `_with_the_critics` truthy implies
        the card is in the watcher's scope — or nobody owns it."""
        board = _Board(_epic(_boundary(), _pre(), labels=labels,
                             children=children))
        (card,) = board.cards
        left = board.run(lambda: reconcile._with_the_critics(card))
        handed = self._report(board, set())
        if left:
            assert EPIC in handed["epics"]
        assert bool(left) == (labels == MINE and children > 0)

    def test_the_sweep_hands_the_scope_to_the_watcher_inside_its_phase(self):
        source = inspect.getsource(reconcile.main)
        phase = source.index('_phase("report_rereview_missing")')
        block = source[phase:phase + 500]
        assert "rereview_watch_scope(epics)" in block
        assert "rereview_watch.report(watched, epic_thread, lane_of)" in block

    def test_the_scope_reads_only_the_board_this_sweep_already_read(self):
        """Both lanes are inside SWEPT_LANES, so `active_cards` serves them
        from the one snapshot and the watcher's scope costs no request."""
        assert set(reconcile.SWEEP_STATES + reconcile.PLANNING_LANE) <= set(
            reconcile.SWEPT_LANES)


# ===========================================================================
# The words that change with the destination
# ===========================================================================
class TestTheWordsFollowTheDestination:
    def test_reconcile_no_longer_asks_the_planners_question(self):
        source = (ROOT / "scripts" / "reconcile.py").read_text(encoding="utf-8")
        assert "planning_escalation.escalate" not in source
        assert planning_escalation.destination() == GREEN_LIGHT

    def test_post_approval_is_gone(self):
        source = (ROOT / "scripts" / "reconcile.py").read_text(encoding="utf-8")
        assert not re.search(r"post-approval", source, re.IGNORECASE)

    def test_the_hold_message_names_the_second_critic(self, capsys):
        waiting = planner_queue.format_receipt(
            "waiting", card=EPIC, run="run-1",
            repo="dreadnought-foundry/agent-bureau", trigger="In Progress",
            at=_iso(40), place=1, of=2)
        assert reconcile.post_critic_hold_is_overdue(
            plan_critic.POST_UNREAD_TAG, _iso(45), [waiting]) is False
        out = capsys.readouterr().out
        assert "the second critic's review has not started" in out

    @pytest.mark.parametrize("reason", [
        lambda: reconcile.stalled_planning_reason(),
        lambda: reconcile.FROZEN_PLANNING_REASON,
        lambda: reconcile.waiting_too_long_reason(BOUND + 1),
    ])
    def test_no_reason_says_it_is_in_front_of_the_ceo(self, reason):
        assert "in front of you" not in reason()

    def test_the_line_reason_keeps_its_last_sentence(self):
        assert reconcile.waiting_too_long_reason(BOUND + 1).endswith(
            "Sending it back through Planning gives it a fresh place in line.")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
