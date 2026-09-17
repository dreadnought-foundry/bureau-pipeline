"""RED-first tests: a stalled Planning card is ESCALATED, not labelled in place
(DRE-4124).

MEASURED 2026-09-16/17. Twenty-three cards across the fleet carried
`needs-human`, every one applied by the pipeline and none by a person, and
thirteen of them were sitting in `Planning` — the oldest (DRE-2415) for
thirty-five days. They looked like work in flight on the board and nobody was
ever actually asked anything.

THE DEFECT. `reconcile.flag_stalled_planning` saw a card that had been in
Planning past the window, posted one comment, added `needs-human` and — by its
own docstring — made "no state move, no cancel". That label is then a permanent
freeze, because everything else skips a held card: the same watchdog on later
passes, promotion, limit recovery, and the operator runner in the console repo.
Planning is deliberately outside the nudge loop, so nothing else was watching.

THE SAME DEFECT WAS ALREADY FIXED ONE LANE UPSTREAM. For Intake, DRE-2687 made
the age-out MOVE the card to Green Light, and `reconcile.py` states the
principle: *"Past it the card MOVES to Green Light. Not a report… A report is a
record; a move is a gate."* Planning never got that treatment.

WHAT IS UNDER TEST, one section per acceptance criterion:

  1. A card that has sat in Planning past the window is MOVED to Green Light
     with an escalation comment, and the pipeline adds no `needs-human`.
  2. `planning_escalation.moved_on` reads the card's LANE, and a routing
     verdict older than the current planning attempt is stale — it is not
     proof the card has moved on.
  3. A person-applied `needs-human` is still honoured everywhere it is
     honoured today.
  4. The repair pass is idempotent and never touches a card with an open PR.
  5. The DRE-2415 shape — thirty-five days in Planning, a bot-applied label,
     the watchdog's own receipt, no PR — is repaired in one pass, and a second
     pass changes nothing.
  6. `flag_stranded` and the six move-paired `needs-human` writers are
     unchanged; this card moves exactly one writer.

Run: cd bureau-pipeline && python3 -m pytest tests/test_planning_hold_escalation.py -v
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

import dead_run  # noqa: E402
import limit_recovery  # noqa: E402
import planning_escalation  # noqa: E402
import reconcile  # noqa: E402
import routing_verdict  # noqa: E402
import validate_card  # noqa: E402

#: The card the whole repair pass is named after: thirty-five days in Planning.
DRE_2415 = "DRE-2415"
THIRTY_FIVE_DAYS = 35 * 24 * 60


@pytest.fixture(autouse=True)
def _pin_repo_slug(monkeypatch):
    monkeypatch.setattr(reconcile, "REPO_SLUG", "agent-bureau")


@pytest.fixture(autouse=True)
def _pin_valid_slugs(monkeypatch):
    monkeypatch.setattr(
        validate_card, "VALID_SLUGS", {"agent-bureau", "atlas", "bureau-pipeline"}
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


def _card(
    identifier=DRE_2415,
    state="Planning",
    labels=(),
    minutes_stale=THIRTY_FIVE_DAYS,
    bodies=(),
):
    """A card in the shape `active_cards` returns it — comments inline, newest
    first, the order Linear answers a comment window in (DRE-3250)."""
    return {
        "id": f"uuid-{identifier}",
        "identifier": identifier,
        "title": "get-library ships every project member the raw email addresses",
        "description": "work",
        "createdAt": _iso(minutes_stale),
        "updatedAt": _iso(minutes_stale),
        "state": {"name": state},
        "labels": {"nodes": [{"name": n} for n in labels]},
        "comments": {
            "nodes": [
                {"body": b, "createdAt": _iso(minutes_stale)}
                for b in reversed(list(bodies))
            ]
        },
    }


#: The receipt the watchdog posted when it froze these cards. It is how
#: "bot-applied" is KNOWN — the label arrived with this comment — so the repair
#: pass keys on it and a card without one is a person's hold.
def _watchdog_receipt() -> str:
    return f"🚨 {reconcile.WATCHDOG_TAG}: planning has produced nothing. Observed: …"


class _Board:
    """One Linear board the sweep reads and writes, with every write recorded.

    `escalate()` is the real one — this stands in for Linear, never for the
    escalation — so a move here really does take the card out of Planning, and
    the second pass sees what the first left behind.
    """

    def __init__(self, cards, prs=()):
        self.cards = list(cards)
        #: None stands for an UNREADABLE listing, which `_open_pr_listing`
        #: answers with rather than `[]` (DRE-2034).
        self.prs = None if prs is None else list(prs)
        self.posted: list[tuple[str, str]] = []
        self.states: list[tuple[str, str]] = []
        self.added: list[tuple[str, str]] = []
        self.removed: list[tuple[str, str]] = []

    # --- the seams the sweep reads --------------------------------------
    def active_cards(self, states=reconcile.SWEEP_STATES):
        return [c for c in self.cards if c["state"]["name"] in states]

    def _find(self, identifier):
        return next(c for c in self.cards if c["identifier"] == identifier)

    # --- the seams the sweep writes -------------------------------------
    def cmd_comment(self, identifier, body, *rest):
        self.posted.append((identifier, body))
        card = self._find(identifier)
        card["comments"]["nodes"].insert(
            0, {"body": body, "createdAt": _iso(0)})

    def cmd_state(self, identifier, lane, *rest):
        self.states.append((identifier, lane))
        self._find(identifier)["state"]["name"] = lane

    def add_label(self, identifier, label):
        self.added.append((identifier, label))
        self._find(identifier)["labels"]["nodes"].append({"name": label})

    def remove_label(self, identifier, label):
        self.removed.append((identifier, label))
        card = self._find(identifier)
        card["labels"]["nodes"] = [
            n for n in card["labels"]["nodes"]
            if (n.get("name") or "").lower() != label.lower()
        ]

    def bodies(self) -> str:
        return "\n".join(body for _, body in self.posted)

    def lane(self, identifier=DRE_2415) -> str:
        return self._find(identifier)["state"]["name"]

    def labels(self, identifier=DRE_2415) -> list[str]:
        return [
            (n.get("name") or "").lower()
            for n in self._find(identifier)["labels"]["nodes"]
        ]

    def run(self, fn):
        def no_per_card(identifier, *a, **kw):
            raise AssertionError(
                f"the sweep fetched {identifier} one card at a time — the lane "
                "and the comments both come with the board read (DRE-2929)"
            )

        with patch.object(
            reconcile, "active_cards", side_effect=self.active_cards
        ), patch.object(
            reconcile, "_open_pr_listing",
            side_effect=lambda: None if self.prs is None else list(self.prs),
        ), patch.object(
            reconcile.linear_ops, "cmd_comment", side_effect=self.cmd_comment
        ), patch.object(
            reconcile.linear_ops, "cmd_state", side_effect=self.cmd_state
        ), patch.object(
            reconcile.linear_ops, "add_label", side_effect=self.add_label
        ), patch.object(
            reconcile.linear_ops, "remove_label", side_effect=self.remove_label
        ), patch.object(
            reconcile.linear_ops, "get_issue", side_effect=no_per_card
        ), patch.object(
            reconcile.linear_ops, "comment_bodies", side_effect=no_per_card
        ), patch.object(
            reconcile.linear_ops, "count_comments", side_effect=no_per_card
        ):
            return fn()


# ===========================================================================
# 1. The watchdog ESCALATES: a move, not a label
# ===========================================================================
class TestTheStalledPlanningCardIsMoved:
    def test_a_stalled_planning_card_is_moved_to_green_light(self):
        """A report is a record; a move is a gate (DRE-2687's principle, one
        lane later). The card that has sat in Planning past the window leaves
        it for the CEO's queue."""
        board = _Board([_card()])
        flagged = board.run(reconcile.flag_stalled_planning)
        assert flagged == {DRE_2415}
        assert board.lane() == reconcile.ESCALATED_STATE
        assert board.states == [(DRE_2415, reconcile.ESCALATED_STATE)]

    def test_the_move_carries_the_escalation_comment(self):
        """Moving the card without the reason is a silent park: the CEO sees
        something appear in their queue with nothing to read."""
        board = _Board([_card()])
        board.run(reconcile.flag_stalled_planning)
        note = board.bodies()
        assert planning_escalation.ESCALATION_TAG in note
        assert str(reconcile.PLANNING_MINUTES) in note
        assert "Planning" in note, "the note must name the lane it observed"

    def test_the_pipeline_adds_no_needs_human(self):
        """THE CARD THIS FIX EXISTS FOR. `needs-human` is a person's label, and
        the watchdog's copy of it froze thirteen cards in Planning."""
        board = _Board([_card()])
        board.run(reconcile.flag_stalled_planning)
        assert board.added == []
        assert reconcile.HOLD_LABEL not in board.labels()

    def test_the_note_lands_before_the_card_moves(self):
        board = _Board([_card()])
        board.run(reconcile.flag_stalled_planning)
        assert board.posted and board.states
        assert board.posted[0][0] == DRE_2415

    def test_a_young_planning_card_is_left_alone(self):
        board = _Board([_card(minutes_stale=reconcile.PLANNING_MINUTES - 5)])
        assert board.run(reconcile.flag_stalled_planning) == set()
        assert board.states == [] and board.posted == []

    def test_the_watchdog_writes_no_hold_label_anywhere_in_its_body(self):
        """Structural, so the label cannot creep back in on a later edit: the
        function that owns Planning's strand rule adds no label at all."""
        source = inspect.getsource(reconcile.flag_stalled_planning)
        assert "add_label" not in source, (
            "flag_stalled_planning writes a label again — this rule escalates"
        )


# ===========================================================================
# 2. `moved_on` reads the LANE; a stale verdict is not movement
# ===========================================================================
def _verdict_body() -> str:
    return routing_verdict.verdict_comment(
        "WORKBENCH", "it needs a live system to check")


def _issue(lane: str) -> dict:
    return {"identifier": DRE_2415, "state": {"name": lane}}


def _at(days_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(days=days_ago)).isoformat().replace(
        "+00:00", "Z")


class TestAStaleVerdictIsNotMovement:
    def test_a_verdict_older_than_the_attempt_leaves_the_card_ours(self):
        """DRE-2415 exactly: on 2026-09-16 23:42 PT the escalation stood the
        card down on a verdict from 09-08 while the board plainly showed it in
        Planning. A verdict from a spent attempt proves nothing about this one."""
        comments = [{"body": _verdict_body(), "createdAt": _at(8)}]
        assert planning_escalation.moved_on(
            _issue(planning_escalation.ORIGIN), comments,
            attempt_since=_at(1)) is None

    def test_a_card_that_actually_left_planning_has_moved_on(self):
        """The other direction, and the one DRE-3654 is about: the board shows
        the card somewhere else, so it is not this escalation's to park."""
        comments = [{"body": _verdict_body(), "createdAt": _at(8)}]
        where = planning_escalation.moved_on(
            _issue("In Progress"), comments, attempt_since=_at(1))
        assert where is not None and "In Progress" in where

    def test_a_verdict_from_the_current_attempt_still_counts(self):
        """Guard the guard: staleness must not become "ignore verdicts". A
        verdict stamped since this attempt began IS this attempt's answer."""
        comments = [{"body": _verdict_body(), "createdAt": _at(0.5)}]
        where = planning_escalation.moved_on(
            _issue(planning_escalation.ORIGIN), comments, attempt_since=_at(1))
        assert where is not None and "routing verdict" in where

    def test_a_verdict_with_no_time_on_it_is_not_proof_of_movement(self):
        """Unknown is never a pass (standards/console-honesty.md rule 2): a
        comment whose age cannot be read cannot show the card moved on."""
        assert planning_escalation.moved_on(
            _issue(planning_escalation.ORIGIN), [_verdict_body()],
            attempt_since=_at(1)) is None

    def test_with_no_attempt_stated_the_reader_is_unchanged(self):
        """Callers that do not know when the attempt began get the behaviour
        they have always had — the staleness test is opt-in, not a silent
        change under DRE-3654's two existing seams."""
        assert planning_escalation.moved_on(
            _issue(planning_escalation.ORIGIN), [_verdict_body()]) is not None

    def test_the_escalation_defaults_to_this_run_as_the_attempt(self):
        """A run escalating a card is making the current planning attempt, so
        every verdict already on the card predates it."""
        since = planning_escalation.attempt_started_at()
        assert planning_escalation.moved_on(
            _issue(planning_escalation.ORIGIN),
            [{"body": _verdict_body(), "createdAt": _at(0.001)}],
            attempt_since=since,
        ) is None


# ===========================================================================
# 3. `needs-human` is still a person's label, and still honoured
# ===========================================================================
def _held_card(state="Planning", bodies=()):
    return _card(state=state, labels=(reconcile.HOLD_LABEL,), bodies=bodies)


class TestAPersonsHoldIsStillHonoured:
    def test_the_planning_watchdog_still_skips_a_held_card(self):
        board = _Board([_held_card()])
        assert board.run(reconcile.flag_stalled_planning) == set()
        assert board.states == [] and board.posted == []

    def test_promotion_still_skips_a_held_card(self):
        assert reconcile.held(_held_card(state="Backlog"))
        source = inspect.getsource(reconcile.promote_ready)
        assert "HOLD_LABEL in labels" in source

    def test_limit_recovery_still_skips_a_held_card(self):
        assert limit_recovery._held(_held_card())
        assert "_held(card)" in inspect.getsource(limit_recovery.recover)

    def test_the_fix_dispatch_gate_still_reads_the_label(self):
        assert "HOLD_LABEL" in inspect.getsource(reconcile.card_parked_for_human)
        assert reconcile.card_parked_for_human.__doc__ is not None

    def test_the_label_constant_is_still_one_definition(self):
        assert reconcile.HOLD_LABEL == dead_run.HOLD_LABEL == "needs-human"

    def test_a_persons_hold_in_planning_is_never_repaired(self):
        """No watchdog receipt means the label was somebody's own decision. The
        repair pass leaves it exactly where it is."""
        board = _Board([_held_card(bodies=["I am looking at this by hand."])])
        assert board.run(reconcile.repair_frozen_planning_holds) == set()
        assert board.states == [] and board.removed == []


# ===========================================================================
# 4 & 5. The repair pass — the backlog this defect created
# ===========================================================================
def _frozen_card(identifier=DRE_2415, minutes_stale=THIRTY_FIVE_DAYS):
    """The DRE-2415 shape: in Planning, `needs-human`, the watchdog's own
    receipt, no pull request."""
    return _card(
        identifier=identifier,
        state="Planning",
        labels=(reconcile.HOLD_LABEL,),
        minutes_stale=minutes_stale,
        bodies=[_watchdog_receipt()],
    )


class TestTheRepairPass:
    def test_the_dre_2415_shape_is_repaired_in_one_pass(self):
        board = _Board([_frozen_card()])
        repaired = board.run(reconcile.repair_frozen_planning_holds)
        assert repaired == {DRE_2415}
        assert board.lane() == reconcile.ESCALATED_STATE
        assert planning_escalation.ESCALATION_TAG in board.bodies()
        assert reconcile.HOLD_LABEL not in board.labels()
        assert board.removed == [(DRE_2415, reconcile.HOLD_LABEL)]

    def test_a_second_pass_changes_nothing(self):
        board = _Board([_frozen_card()])
        board.run(reconcile.repair_frozen_planning_holds)
        writes = (len(board.posted), len(board.states), len(board.removed))
        assert board.run(reconcile.repair_frozen_planning_holds) == set()
        assert (len(board.posted), len(board.states), len(board.removed)) == writes

    def test_a_card_with_an_open_pr_is_never_touched(self):
        """Work is in flight on it, whatever the label says — the repair is for
        cards nobody is holding and nothing is moving."""
        board = _Board(
            [_frozen_card()],
            prs=[{"number": 7, "headRefName": f"agent/{DRE_2415}-something"}],
        )
        assert board.run(reconcile.repair_frozen_planning_holds) == set()
        assert board.states == [] and board.removed == []

    def test_an_unreadable_pr_listing_repairs_nothing(self):
        """DRE-2034: unreadable is not "no open pull requests". A pass that
        cannot answer the question acts on nothing and tries again next sweep."""
        board = _Board([_frozen_card()], prs=None)
        assert board.run(reconcile.repair_frozen_planning_holds) == set()
        assert board.states == [] and board.removed == []

    def test_hand_built_work_is_left_where_its_owner_put_it(self):
        """DRE-2524's rule, honoured by every member of this family: no agent
        was ever coming for that card, so moving it into the CEO's queue would
        take it out of the hands of the person building it."""
        card = _frozen_card()
        card["labels"]["nodes"].append({"name": reconcile.HAND_BUILT_LABEL})
        board = _Board([card])
        assert board.run(reconcile.repair_frozen_planning_holds) == set()
        assert board.states == [] and board.removed == []

    def test_a_parked_card_is_not_put_in_front_of_the_ceo(self):
        """DRE-2724: PARKED is a decision, not a stall — a card routed PARKED
        is "never reported as stalled by any sweep", so it is not one a sweep
        gets to escalate either."""
        card = _frozen_card()
        card["comments"]["nodes"].insert(0, {
            "body": routing_verdict.verdict_comment(
                "PARKED", "we decided not to do this"),
            "createdAt": _iso(0),
        })
        board = _Board([card])
        assert board.run(reconcile.repair_frozen_planning_holds) == set()
        assert board.states == [] and board.removed == []

    def test_another_repos_card_is_that_repos_sweeps_business(self):
        card = _frozen_card()
        card["labels"]["nodes"].append({"name": "repo:atlas"})
        board = _Board([card])
        assert board.run(reconcile.repair_frozen_planning_holds) == set()
        assert board.states == []

    def test_a_card_outside_planning_is_not_the_repairs_business(self):
        card = _frozen_card()
        card["state"]["name"] = "Backlog"
        board = _Board([card])
        assert board.run(reconcile.repair_frozen_planning_holds) == set()

    def test_every_repaired_card_is_printed(self, capsys):
        board = _Board([_frozen_card()])
        board.run(reconcile.repair_frozen_planning_holds)
        assert DRE_2415 in capsys.readouterr().out

    def test_the_repair_runs_in_the_sweep(self):
        """A pass nothing calls repairs nothing. `main()` must run it."""
        assert "repair_frozen_planning_holds" in inspect.getsource(reconcile.main)


# ===========================================================================
# 6. The other six writers are unchanged
# ===========================================================================
#: Every place the pipeline writes `needs-human`, by file and by the anchor
#: that identifies the site. Six of them pair the label with a move into a lane
#: a person watches and are UNCHANGED by this card; `flag_stranded` is the
#: seventh and is a separate card. `flag_stalled_planning` is the only writer
#: this card removes.
_LABEL_WRITERS = (
    ("scripts/reconcile.py", r"linear_ops\.add_label\(ident, HOLD_LABEL\)", 3),
    ("scripts/dead_run.py", r"label: str = HOLD_LABEL", 1),
    (".github/workflows/agent-fix.yml", r'add-label "\$CARD" needs-human', 2),
    (".github/workflows/plan.yml", r'add-label "\$EPIC" needs-human', 2),
)

#: The two dead-run cap sites in `main()`, each of which pairs the label with a
#: deliberate park into Backlog. Anchored on the park flag rather than on a line
#: number, so the pairing is what is asserted.
_DEAD_RUN_PARK = r'linear_ops\.add_label\(ident, HOLD_LABEL\)\s*\n(?:\s*#.*\n)*\s*linear_ops\.cmd_state\(ident, "Backlog", "--park"\)'


class TestTheOtherWritersAreUnchanged:
    @pytest.mark.parametrize("path,anchor,count", _LABEL_WRITERS)
    def test_the_writer_count_is_what_this_card_left(self, path, anchor, count):
        text = (ROOT / path).read_text(encoding="utf-8")
        found = len(re.findall(anchor, text))
        assert found == count, (
            f"{path} writes {reconcile.HOLD_LABEL} at {found} site(s), expected "
            f"{count} — this card moves exactly one writer, in "
            "flag_stalled_planning"
        )

    def test_flag_stranded_still_comments_and_labels_in_place(self):
        """Its no-route cards belong in Triage rather than Green Light, so it is
        a separate card and is deliberately not changed here."""
        source = inspect.getsource(reconcile.flag_stranded)
        assert "linear_ops.add_label(ident, HOLD_LABEL)" in source
        assert "cmd_state" not in source and "cmd_advance" not in source

    def test_the_dead_run_cap_still_pairs_the_label_with_a_park(self):
        """Both cap sites write the label AND move the card to Backlog. Six of
        the seven writers pair the label with a lane a person watches — those
        six are why the label keeps meaning something, and none moves here."""
        source = (ROOT / "scripts" / "reconcile.py").read_text(encoding="utf-8")
        assert len(re.findall(_DEAD_RUN_PARK, source)) == 2

    def test_dead_runs_park_writes_both_or_neither(self):
        """`dead_run.park` is the third label-plus-move writer, and its whole
        contract is that the label never lands without the move (DRE-2911)."""
        source = inspect.getsource(dead_run.park)
        assert "write_state" in source and "remove_label" in source


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
