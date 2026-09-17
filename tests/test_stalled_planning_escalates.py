"""RED-first tests: a stalled Planning card is ESCALATED, not labelled in
place (DRE-4124).

THE MEASUREMENT, 2026-09-16/17: 23 cards across the fleet carried
`needs-human`, every one applied by the bot and none by a person, and 13 of
them were sitting in `Planning` — the oldest (DRE-2415) for 35 days. They read
as work in flight on the board and nobody was ever actually asked anything.

The cause is one writer. `reconcile.flag_stalled_planning` saw a card past the
Planning window, posted a comment, added `needs-human` — and by its own
docstring made "no state move, no cancel". That label is then a permanent
freeze, because everything downstream skips a held card: the watchdog itself on
later passes, promotion, limit recovery, and the console repo's operator
runner. Planning is deliberately outside the nudge loop, so nothing else was
watching either.

The same defect was already fixed one lane upstream. For Intake, DRE-2687 made
the age-out MOVE the card to Green Light, and said why: *"Past it the card
MOVES to Green Light. Not a report… A report is a record; a move is a gate."*
Planning never got that treatment. This is that treatment.

WHAT THIS PINS, one section per acceptance criterion:

  1. A card past the Planning window is MOVED to Green Light with an
     escalation comment, and the pipeline adds no `needs-human` to it.
  2. `planning_escalation.moved_on` reads the card's LANE. A routing verdict
     older than the current planning attempt is STALE and is not movement —
     it stood DRE-2415 down on a verdict from 09-08 while the card was plainly
     still in Planning. A verdict from the current attempt still stops the
     park (DRE-3604's five-second window is unchanged).
  3. `needs-human` stays a person's label: every reader that honours it today
     still honours it, and the six writers that pair it with a move into a
     lane a person watches are untouched. Only `flag_stalled_planning` changes.
  4. One sweep pass repairs the backlog the old behaviour created, is
     idempotent, and never touches a card with an open PR or a card whose hold
     no watchdog receipt explains.

Run: cd bureau-pipeline && python3 -m pytest tests/test_stalled_planning_escalates.py -v
"""
from __future__ import annotations

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
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import planning_escalation  # noqa: E402
import reconcile  # noqa: E402
import routing_verdict  # noqa: E402
import validate_card  # noqa: E402

#: The card the measurement is named after: 35 days in Planning, a bot-applied
#: hold, the watchdog's receipt, no pull request, and a routing verdict from
#: eight days before the escalation that was read as "it has moved on".
DRE_2415 = "DRE-2415"

WORKBENCH_WHY = "This one needs a live session at the console to check."


@pytest.fixture(autouse=True)
def _pin_repo_slug(monkeypatch):
    monkeypatch.setattr(reconcile, "REPO_SLUG", "bureau-pipeline")


@pytest.fixture(autouse=True)
def _pin_valid_slugs(monkeypatch):
    monkeypatch.setattr(
        validate_card, "VALID_SLUGS", {"agent-bureau", "atlas", "bureau-pipeline"}
    )


def _iso(minutes_ago: float) -> str:
    return (
        (datetime.now(UTC) - timedelta(minutes=minutes_ago))
        .isoformat()
        .replace("+00:00", "Z")
    )


def _watchdog_receipt(reason: str = "planning has produced nothing.") -> str:
    """The receipt the watchdog posts — the marker that makes a hold readable
    as bot-applied, composed through the one writer so the trailer is real."""
    import pipeline_act

    return pipeline_act.receipt(
        "card-stranded", f"🚨 {reconcile.WATCHDOG_TAG}: {reason}"
    )


class _Board:
    """One Linear team, as much of it as these passes read and write.

    Every seam the sweep and the escalation touch is recorded rather than
    posted: the board read (with each card's comment window inline, the way
    `active_cards` really returns it since DRE-2929), the live issue read the
    escalation makes before it writes, and the four writes.
    """

    def __init__(self, cards):
        self.cards = list(cards)
        self.comments: dict[str, list[dict]] = {}
        self.posted: list[tuple[str, str]] = []
        self.states: list[tuple[str, str]] = []
        self.added: list[tuple[str, str]] = []
        self.removed: list[tuple[str, str]] = []
        self.prs: dict[str, dict] = {}
        for card in self.cards:
            self.comments[card["identifier"]] = [
                dict(node) for node in (card.get("comments") or {}).get("nodes", [])
            ]

    # --- the board's own verbs ------------------------------------------
    def card(self, ident: str) -> dict:
        return next(c for c in self.cards if c["identifier"] == ident)

    def lane(self, ident: str) -> str:
        return self.card(ident)["state"]["name"]

    def labels(self, ident: str) -> list[str]:
        return [n["name"].lower() for n in self.card(ident)["labels"]["nodes"]]

    def bodies(self, ident: str) -> list[str]:
        return [c["body"] for c in self.comments[ident]]

    # --- the seams ------------------------------------------------------
    def active_cards(self, states=reconcile.SWEEP_STATES):
        out = []
        for card in self.cards:
            if card["state"]["name"] not in states:
                continue
            # Newest-first inline, exactly as Linear serves the window: the
            # readers go through linear_ops.window_nodes to flip it.
            out.append(dict(card, comments={
                "pageInfo": {"hasNextPage": False},
                "nodes": list(reversed(self.comments[card["identifier"]])),
            }))
        return out

    def get_issue(self, ident, **kw):
        card = self.card(ident)
        return {
            "id": card["id"], "identifier": ident, "title": card["title"],
            "team": {"id": "team-id"}, "state": {"name": card["state"]["name"],
                                                 "type": "unstarted"},
            "labels": card["labels"], "children": {"nodes": []},
        }

    def cmd_comment(self, ident, body, *rest):
        self.posted.append((ident, body))
        self.comments[ident].append({"body": body, "createdAt": _iso(0)})

    def cmd_state(self, ident, state, *rest):
        self.states.append((ident, state))
        self.card(ident)["state"]["name"] = state

    def add_label(self, ident, label):
        self.added.append((ident, label))
        self.card(ident)["labels"]["nodes"].append({"name": label})

    def remove_label(self, ident, label):
        self.removed.append((ident, label))
        self.card(ident)["labels"]["nodes"] = [
            n for n in self.card(ident)["labels"]["nodes"]
            if n["name"].lower() != label.lower()
        ]

    def count_comments(self, ident, needle, **kw):
        return sum(1 for body in self.bodies(ident) if needle in body)

    def pr_for(self, ident):
        return self.prs.get(ident)

    def run(self, fn):
        with patch.object(
            reconcile, "active_cards", side_effect=self.active_cards
        ), patch.object(
            reconcile, "pr_for", side_effect=self.pr_for
        ), patch.object(
            reconcile.linear_ops, "get_issue", side_effect=self.get_issue
        ), patch.object(
            reconcile.linear_ops, "cmd_comment", side_effect=self.cmd_comment
        ), patch.object(
            reconcile.linear_ops, "cmd_state", side_effect=self.cmd_state
        ), patch.object(
            reconcile.linear_ops, "add_label", side_effect=self.add_label
        ), patch.object(
            reconcile.linear_ops, "remove_label", side_effect=self.remove_label
        ), patch.object(
            reconcile.linear_ops, "count_comments", side_effect=self.count_comments
        ), patch.object(
            reconcile.linear_ops, "comment_bodies",
            side_effect=lambda i: (_ for _ in ()).throw(AssertionError(
                f"{i}'s comments were fetched one card at a time — the bodies "
                "come inline with the board read (DRE-2929)"
            )),
        ):
            return fn()


def _card(
    identifier="DRE-4124",
    state="Planning",
    labels=(),
    minutes_stale=None,
    comments=(),
):
    if minutes_stale is None:
        minutes_stale = reconcile.PLANNING_MINUTES + 5
    return {
        "id": f"uuid-{identifier}",
        "identifier": identifier,
        "title": "a card planning never classified",
        "description": "work",
        "updatedAt": _iso(minutes_stale),
        "state": {"name": state},
        "labels": {"nodes": [{"name": n} for n in labels]},
        "children": {"nodes": []},
        "comments": {"nodes": list(comments)},
    }


# ===========================================================================
# 1. The stalled card is MOVED, not labelled in place
# ===========================================================================
class TestTheWatchdogEscalatesInsteadOfLabelling:
    def test_a_card_past_the_window_is_moved_to_the_decision_queue(self):
        """The headline. A report is a record; a move is a gate — the sentence
        DRE-2687 already wrote for Intake, applied one lane along."""
        board = _Board([_card()])
        flagged = board.run(reconcile.flag_stalled_planning)

        assert flagged == {"DRE-4124"}
        assert board.states == [("DRE-4124", planning_escalation.destination())]
        assert board.lane("DRE-4124") == "Green Light"

    def test_the_move_carries_an_escalation_comment(self):
        board = _Board([_card()])
        board.run(reconcile.flag_stalled_planning)

        note = "\n".join(body for _, body in board.posted)
        assert planning_escalation.ESCALATION_TAG in note
        assert planning_escalation.destination() in note

    def test_the_pipeline_adds_no_needs_human_to_it(self):
        """The whole defect in one assertion: the label is what froze 13 cards,
        because every reader downstream skips a held card."""
        board = _Board([_card()])
        board.run(reconcile.flag_stalled_planning)

        assert board.added == [], (
            "the watchdog labelled the card — a held card is skipped by "
            "promotion, by limit recovery, by the operator runner and by this "
            "watchdog's own next pass"
        )
        assert reconcile.HOLD_LABEL not in board.labels("DRE-4124")

    def test_the_question_lands_before_the_card_moves(self):
        """`escalate`'s rule, inherited: a move without the question is a
        silent park, and the CEO sees something appear with nothing to answer."""
        board = _Board([_card()])
        order: list[str] = []
        with patch.object(reconcile.linear_ops, "cmd_comment",
                          side_effect=lambda i, b, *a: order.append("comment")):
            with patch.object(reconcile.linear_ops, "cmd_state",
                              side_effect=lambda i, s, *a: order.append("state")):
                board.run(reconcile.flag_stalled_planning)
        assert order and order.index("comment") < order.index("state")

    def test_the_receipt_is_still_posted_so_the_hold_stays_readable(self):
        """The watchdog's own receipt is the once-ever idempotency marker AND
        the evidence that a hold was bot-applied — the repair pass below reads
        exactly this comment. It must survive the change."""
        board = _Board([_card()])
        board.run(reconcile.flag_stalled_planning)

        assert any(
            reconcile.WATCHDOG_TAG in body for _, body in board.posted
        ), "the watchdog stopped leaving its receipt"

    def test_the_notice_no_longer_tells_the_reader_to_remove_a_label(self):
        """The old reason closed by telling the reader to clear `needs-human`.
        Nothing applies one any more, so that sentence would be an instruction
        to undo something that never happened."""
        board = _Board([_card()])
        board.run(reconcile.flag_stalled_planning)
        note = "\n".join(body for _, body in board.posted)
        assert f"remove the '{reconcile.HOLD_LABEL}'" not in note

    def test_a_card_under_the_window_is_still_left_alone(self):
        board = _Board([_card(minutes_stale=reconcile.PLANNING_MINUTES - 5)])
        assert board.run(reconcile.flag_stalled_planning) == set()
        assert board.states == [] and board.posted == []

    def test_the_escalation_is_once_ever(self):
        """The WATCHDOG_TAG comment is still the idempotency marker, and the
        card has left Planning anyway — a second pass must write nothing."""
        board = _Board([_card()])
        board.run(reconcile.flag_stalled_planning)
        writes = (list(board.posted), list(board.states))
        board.card("DRE-4124")["state"]["name"] = "Planning"  # dragged back
        board.card("DRE-4124")["updatedAt"] = _iso(reconcile.PLANNING_MINUTES + 5)
        board.run(reconcile.flag_stalled_planning)
        assert (board.posted, board.states) == writes


# ===========================================================================
# 2. `moved_on` reads the LANE, and a stale verdict is not movement
# ===========================================================================
class TestAStaleVerdictIsNotMovement:
    def _issue(self, lane):
        return {"identifier": DRE_2415, "state": {"name": lane}}

    def _verdict_at(self, days_ago: float) -> dict:
        return {
            "body": routing_verdict.verdict_comment("WORKBENCH", WORKBENCH_WHY),
            "createdAt": _iso(days_ago * 1440),
        }

    def test_dre_2415_a_verdict_older_than_the_attempt_leaves_the_card_ours(self):
        """THE CARD THIS FIX EXISTS FOR. On 2026-09-16 23:42 PT the escalation
        stood DRE-2415 down on a verdict from 09-08 while the board plainly
        showed the card in Planning — so nothing was parked, nothing was asked,
        and the card stayed exactly where it had been for 35 days."""
        assert planning_escalation.moved_on(
            self._issue(planning_escalation.ORIGIN),
            [self._verdict_at(8)],
            attempt_since=_iso(120),
        ) is None

    def test_a_card_that_actually_left_planning_has_moved_on(self):
        where = planning_escalation.moved_on(
            self._issue("In Progress"),
            [self._verdict_at(8)],
            attempt_since=_iso(120),
        )
        assert where is not None and "In Progress" in where

    def test_the_lane_alone_is_enough_with_no_verdict_at_all(self):
        where = planning_escalation.moved_on(
            self._issue("In Review"), [], attempt_since=_iso(120))
        assert where is not None and "In Review" in where

    def test_a_verdict_from_the_current_attempt_still_stops_the_park(self):
        """DRE-3604 is unchanged: a verdict stamped seconds ago on a card the
        board still shows in Planning means classification has just answered
        and the move is on its way. The clock separates that from a verdict
        nothing has acted on in 35 days."""
        where = planning_escalation.moved_on(
            self._issue(planning_escalation.ORIGIN),
            [self._verdict_at(0)],
            attempt_since=_iso(120),
        )
        assert where is not None and "WORKBENCH" in where

    def test_a_verdict_that_cannot_be_dated_is_stale_when_a_clock_is_given(self):
        """Unknown is never a pass (`in_escalation_segment`'s rule, applied to
        a clock): a verdict that cannot be shown to belong to this attempt
        cannot be read as this attempt answering."""
        assert planning_escalation.moved_on(
            self._issue(planning_escalation.ORIGIN),
            [routing_verdict.verdict_comment("WORKBENCH", WORKBENCH_WHY)],
            attempt_since=_iso(120),
        ) is None

    def test_without_a_clock_the_reading_is_exactly_as_it_was(self):
        """Every caller that does not know when the current attempt began gets
        the DRE-3654 behaviour byte for byte — a verdict is movement."""
        where = planning_escalation.moved_on(
            self._issue(planning_escalation.ORIGIN),
            [routing_verdict.verdict_comment("WORKBENCH", WORKBENCH_WHY)],
        )
        assert where is not None and "WORKBENCH" in where

    def test_bare_bodies_and_dated_records_read_the_same_without_a_clock(self):
        bodies = [routing_verdict.verdict_comment("FLEET", "An agent can do this.")]
        records = [{"body": bodies[0], "createdAt": _iso(1)}]
        issue = self._issue(planning_escalation.ORIGIN)
        assert planning_escalation.moved_on(issue, bodies) == \
            planning_escalation.moved_on(issue, records)

    def test_the_watchdog_hands_the_escalation_the_attempt_it_measured(self):
        """End to end, on the DRE-2415 shape: a card in Planning carrying an
        eight-day-old verdict is ESCALATED by the sweep, not stood down."""
        board = _Board([_card(
            identifier=DRE_2415,
            comments=[{
                "body": routing_verdict.verdict_comment("WORKBENCH", WORKBENCH_WHY),
                "createdAt": _iso(8 * 1440),
            }],
        )])
        assert board.run(reconcile.flag_stalled_planning) == {DRE_2415}
        assert board.states == [(DRE_2415, planning_escalation.destination())]
        assert planning_escalation.STOOD_DOWN_TAG not in "\n".join(
            body for _, body in board.posted)

    def test_the_attempt_window_is_the_one_the_age_gate_measured(self):
        """Derived from the lane's own stall window, never a second number: the
        sweep has just measured this card as idle for PLANNING_MINUTES, so
        anything older than that window belongs to an attempt that ended."""
        since = reconcile.planning_attempt_since()
        measured = reconcile.age_minutes(since)
        assert abs(measured - reconcile.PLANNING_MINUTES) < 1


# ===========================================================================
# 3. `needs-human` stays a person's label — one writer changes, six do not
# ===========================================================================
_ATTACH = re.compile(r"(?:linear_ops\.)?add_label\s*\(|linear_ops\.py add-label")
_HOLD = re.compile(r"HOLD_LABEL|needs-human")
_MOVE = re.compile(r"cmd_state\(|cmd_advance\(|write_state\(|"
                   r"linear_ops\.py (?:state|advance) ")


def _py_sites(path: Path):
    lines = path.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if not _ATTACH.search(line) or line.lstrip().startswith("def "):
            continue
        start = next(
            (j for j in range(i, -1, -1) if re.match(r"def \w+", lines[j])), None)
        if start is None:
            continue
        end = next(
            (j for j in range(start + 1, len(lines))
             if re.match(r"(def |class )", lines[j])), len(lines))
        block = "\n".join(lines[start:end])
        if not (_HOLD.search(line) or _HOLD.search(block)):
            continue
        yield (
            f"scripts/{path.name}",
            re.match(r"def (\w+)", lines[start]).group(1),
            "\n".join(lines[max(0, i - 6):i + 7]),
        )


def _yml_sites(path: Path):
    lines = path.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if not (_ATTACH.search(line) and _HOLD.search(line)):
            continue
        step = next(
            (re.match(r"\s*- name: (.+)", lines[j]).group(1).strip()
             for j in range(i, -1, -1) if re.match(r"\s*- name: (.+)", lines[j])),
            "<top>")
        yield (
            f".github/workflows/{path.name}",
            step,
            "\n".join(lines[max(0, i - 6):i + 7]),
        )


def _hold_label_writers():
    """Every place the pipeline attaches `needs-human`, DISCOVERED.

    A list would only ever count the ones somebody remembered — the same
    reason `planning_escalation.label_census` discovers rather than enumerates.
    """
    sites = []
    for path in sorted((ROOT / "scripts").glob("*.py")):
        sites.extend(_py_sites(path))
    for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        sites.extend(_yml_sites(path))
    return sites


#: The census as the card names it, minus the one writer this card touches.
#: Six writers pair the label with a move into a lane a person watches;
#: `flag_stranded` is the seventh and its no-route cards belong in Triage
#: rather than Green Light, so it is a separate card and is unchanged here.
MOVE_PAIRED = {
    ("scripts/dead_run.py", "park"),
    ("scripts/reconcile.py", "main"),
    (".github/workflows/agent-fix.yml",
     "Escalate checks the loop structurally cannot fix"),
    (".github/workflows/agent-fix.yml", "Report"),
    (".github/workflows/plan.yml", "Second critic — the review died"),
    (".github/workflows/plan.yml", "Second critic sent the plan back"),
}
IN_PLACE = {("scripts/reconcile.py", "flag_stranded")}


class TestNeedsHumanStaysAPersonsLabel:
    def test_the_stalled_planning_watchdog_is_no_longer_a_writer(self):
        where = {(f, sym) for f, sym, _ in _hold_label_writers()}
        assert ("scripts/reconcile.py", "flag_stalled_planning") not in where

    def test_the_census_is_exactly_the_six_plus_flag_stranded(self):
        """Discovered, then pinned: a seventh writer added in a hurry fails
        this build rather than freezing another 23 cards quietly."""
        assert {(f, sym) for f, sym, _ in _hold_label_writers()} == \
            MOVE_PAIRED | IN_PLACE

    def test_every_remaining_writer_but_one_pairs_the_label_with_a_move(self):
        for file, sym, window in _hold_label_writers():
            if (file, sym) in IN_PLACE:
                continue
            assert _MOVE.search(window), (
                f"{file}:{sym} labels a card and moves it nowhere — that is "
                "the shape DRE-4124 removed from flag_stalled_planning"
            )

    def test_flag_stranded_is_unchanged_comment_and_label_no_move(self):
        """Behavioural, not structural: the other in-place writer keeps its
        shape exactly. Its no-route cards belong in Triage rather than in the
        CEO's decision queue, so it is a separate card."""
        board = _Board([_card(
            identifier="DRE-1978", state="Todo",
            labels=("repo:ghost-product",),
            minutes_stale=reconcile.WATCHDOG_MINUTES + 999,
        )])
        with patch.object(reconcile, "live_rail_slugs",
                          return_value=frozenset({"bureau-pipeline"})):
            flagged = board.run(reconcile.flag_stranded)
        assert flagged == {"DRE-1978"}
        assert board.added == [("DRE-1978", reconcile.HOLD_LABEL)]
        assert board.states == [], "flag_stranded moved a card — it must not"

    def test_a_person_applied_hold_is_still_skipped_by_this_watchdog(self):
        board = _Board([_card(labels=(reconcile.HOLD_LABEL,))])
        assert board.run(reconcile.flag_stalled_planning) == set()
        assert board.posted == [] and board.states == []

    def test_a_person_applied_hold_is_still_skipped_by_promotion(self):
        card = _card(identifier="DRE-9001", state="Backlog",
                     labels=("repo:bureau-pipeline", "agent:engineer",
                             reconcile.HOLD_LABEL))
        assert reconcile.held(card)
        with patch.object(reconcile, "backlog_children", return_value=[card]), \
                patch.object(reconcile.linear_ops, "cmd_state") as move:
            reconcile.promote_ready(active_count=0)
        move.assert_not_called()

    def test_a_person_applied_hold_is_still_skipped_by_limit_recovery(self):
        import limit_recovery

        assert limit_recovery._held(
            {"labels": {"nodes": [{"name": reconcile.HOLD_LABEL}]}})

    def test_the_label_itself_is_untouched(self):
        import dead_run

        assert reconcile.HOLD_LABEL == dead_run.HOLD_LABEL == "needs-human"


# ===========================================================================
# 4. One sweep pass repairs the backlog the old behaviour created
# ===========================================================================
def _frozen_card(identifier=DRE_2415, days=35, labels=(), comments=None):
    """The DRE-2415 shape: 35 days in Planning, a bot-applied hold, the
    watchdog's own receipt, and no pull request."""
    return _card(
        identifier=identifier,
        state="Planning",
        labels=(reconcile.HOLD_LABEL, *labels),
        minutes_stale=days * 1440,
        comments=[{"body": _watchdog_receipt(), "createdAt": _iso(days * 1440)}]
        if comments is None else comments,
    )


class TestTheRepairPass:
    def test_the_frozen_card_is_escalated_and_the_label_removed(self):
        board = _Board([_frozen_card()])
        repaired = board.run(reconcile.repair_planning_holds)

        assert repaired == {DRE_2415}
        assert board.lane(DRE_2415) == planning_escalation.destination()
        assert planning_escalation.ESCALATION_TAG in "\n".join(
            body for _, body in board.posted)
        assert board.removed == [(DRE_2415, reconcile.HOLD_LABEL)]
        assert reconcile.HOLD_LABEL not in board.labels(DRE_2415)

    def test_a_second_pass_changes_nothing(self):
        board = _Board([_frozen_card()])
        board.run(reconcile.repair_planning_holds)
        writes = (list(board.posted), list(board.states), list(board.removed))

        assert board.run(reconcile.repair_planning_holds) == set()
        assert (board.posted, board.states, board.removed) == writes

    def test_a_card_with_an_open_pr_is_never_touched(self):
        board = _Board([_frozen_card()])
        board.prs[DRE_2415] = {"number": 7, "state": "OPEN", "url": "u"}

        assert board.run(reconcile.repair_planning_holds) == set()
        assert board.states == [] and board.removed == [] and board.posted == []

    def test_a_closed_pr_does_not_shield_the_card(self):
        """Guard the guard: only an OPEN pull request means somebody is on it."""
        board = _Board([_frozen_card()])
        board.prs[DRE_2415] = {"number": 7, "state": "CLOSED", "url": "u"}

        assert board.run(reconcile.repair_planning_holds) == {DRE_2415}

    def test_a_hold_no_watchdog_receipt_explains_is_a_persons_hold(self):
        """The whole basis for calling a hold bot-applied is that the label
        arrived with the watchdog's receipt. Without one it is somebody's
        deliberate hold and the pass leaves it alone."""
        board = _Board([_frozen_card(comments=[
            {"body": "Holding this myself until we have talked to the client.",
             "createdAt": _iso(1440)},
        ])])

        assert board.run(reconcile.repair_planning_holds) == set()
        assert board.states == [] and board.removed == []

    def test_a_planning_card_with_no_hold_is_not_the_passs_business(self):
        board = _Board([_card(comments=[
            {"body": _watchdog_receipt(), "createdAt": _iso(1440)}])])

        assert board.run(reconcile.repair_planning_holds) == set()
        assert board.states == []

    def test_a_held_card_outside_planning_is_not_the_passs_business(self):
        """Six other writers pair the label with a move into Backlog or
        Triage. This pass repairs the ONE shape that had no move."""
        board = _Board([_frozen_card()])
        board.card(DRE_2415)["state"]["name"] = "Backlog"

        assert board.run(reconcile.repair_planning_holds) == set()
        assert board.states == []

    def test_the_escalation_carries_what_the_watchdog_said_at_the_time(self):
        board = _Board([_frozen_card(comments=[{
            "body": _watchdog_receipt(
                "planning has produced nothing. Observed: this card has sat in "
                "Planning with nothing posted or changed on it."),
            "createdAt": _iso(35 * 1440),
        }])])
        board.run(reconcile.repair_planning_holds)
        note = "\n".join(body for _, body in board.posted)
        assert "planning has produced nothing" in note

    def test_the_note_the_ceo_reads_carries_no_code(self):
        board = _Board([_frozen_card()])
        board.run(reconcile.repair_planning_holds)
        note = "\n".join(body for _, body in board.posted)
        for leaked in (".py", "```", "git ", "add_label("):
            assert leaked not in note, f"the note leaks {leaked!r}"
        for marker in ("VERDICT:", "QA Critic", "QA Verifier"):
            assert marker not in note

    def test_the_pass_is_wired_into_the_sweep(self):
        """A repair nothing runs is a report, and a report is what this card
        replaces."""
        import inspect

        assert "repair_planning_holds" in inspect.getsource(reconcile.main)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
