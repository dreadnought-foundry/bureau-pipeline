"""The sweep runs the promotion stall clock — the epic's end-to-end test (DRE-4210).

DRE-4198's evidence: five Backlog cards in thirty hours were refused promotion
for the same reason on sweep after sweep, and the only way anyone found them
was by reading a sweep log. From outside, a card refused for a reason nothing
can clear read exactly like a card waiting its turn — Backlog holds both.

DRE-4207 built the clock (`promotion_stall`) and never touched Linear; this
file drives the wiring in `reconcile.promote_ready` over a fake board, sweeping
the same Backlog at t0, t0+15 min … t0+135 min with the sweep's clock a
fixture. Every count is ONE sweep's: `_stale_defects` and `_write_failures`
are cleared before each sweep, while the board's comments carry over — each
sweep reads back what the earlier ones posted, so `_surface_once` sees its own
receipt and `first_comment_at` dates it.

What it pins, case by case:

  1. A clocked refusal earns one `🚨 promotion-stalled:` receipt and a red
     ledger entry once it has stood two hours, and the declared waits beside
     it — a formal `blockedBy`, a PARKED verdict, an epic — never do.
  2. Held cards (`needs-human`, an open agent blocker) count toward the
     idle-board alarm and are never clocked per card.
  3. An undated record counts toward the idle-board line and never ages.
  4. A sweep that dispatched is not idle.
  5. The per-card clock does not depend on WIP.
  6. A refusal that recurred is clocked from its FIRST receipt — accepted on
     purpose, see the case's docstring.

Sites, as `promote_ready` stood when this was built: DRE-6427 had merged, so
`needs-human` is recorded at the stand-down skip (`is held for a human`) and
at the operator-step exit (`is held as an operator step but routed`); DRE-6448
had not, so `agent-blocker` is recorded at the `has an unresolved
agent-blocker — skipping` line.

There is no `wave-not-green-lit` refusal in `promote_ready` on `main` today:
card D — the epic the card describes as waiting in an approved wave — is
skipped by the epic test (`epics are promoted by humans`), which is what it
gets "exactly as today". Its three-day-old `wave-not-green-lit` receipt is on
its thread all the same, and never ages into an alarm.

Run: cd bureau-pipeline && python3 -m pytest tests/test_promotion_stall_scenario.py -v
"""
from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")

import dead_run  # noqa: E402
import hold  # noqa: E402
import plan_critic  # noqa: E402
import promotion_stall  # noqa: E402
import reconcile  # noqa: E402
import routing_verdict  # noqa: E402

T0 = datetime(2026, 10, 9, 15, 0, tzinfo=UTC)
SWEEPS = tuple(range(0, 136, 15))  # t0, t0+15 … t0+135: ten sweeps

EPIC = "DRE-4300"         # active, approved after the gate, second critic PASSed
EPIC_GRACE = "DRE-4290"   # active, approved five minutes before every sweep
APPROVED = "2026-09-10T12:04:00.000Z"
CREATED = "2026-07-01T00:00:00.000Z"  # before any green light: not mid-epic

A, B, C, D, E, F, G, H, A2, OP = (
    "DRE-4301", "DRE-4302", "DRE-4303", "DRE-4304", "DRE-4305",
    "DRE-4306", "DRE-4307", "DRE-4308", "DRE-4309", "DRE-4311",
)
SIBLING = "DRE-4310"  # B's blocker, In Review

FLEET = routing_verdict.verdict_comment("FLEET", "the acceptance criteria are unit-testable")
PARKED = routing_verdict.verdict_comment("PARKED", "deliberately not built this quarter")
NO_VERDICT_RECEIPT = f"🚨 {routing_verdict.NO_VERDICT_TAG}:"


def at(minutes: float) -> str:
    """t0 + `minutes`, as Linear writes a `createdAt`."""
    return (T0 + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


def _comment(body: str, minutes: float) -> dict:
    return {"body": body, "createdAt": at(minutes), "user": {"id": "u-person"}}


def _card(identifier, *, comments=(), parent=EPIC, parent_state="In Progress",
          labels=(), title=None, blockers=()):
    return {
        "id": f"uuid-{identifier}",
        "identifier": identifier,
        "title": title or f"Build piece {identifier}",
        "description": "Add it.\n\n## Acceptance criteria\n\n- [ ] it renders\n",
        "createdAt": CREATED,
        "parent": ({"identifier": parent, "state": {"name": parent_state}}
                   if parent else None),
        "labels": {"nodes": [{"name": n} for n in ("repo:bureau-pipeline", *labels)]},
        "comments": list(comments),
        "inverseRelations": {"nodes": [
            {"type": "blocks", "issue": {"identifier": b, "state": {"name": s}}}
            for b, s in blockers
        ]},
    }


def card_a(identifier=A):
    """A child of an active, released epic, carrying no routing verdict."""
    return _card(identifier)


def card_b():
    """FLEET, held only by a formal `blockedBy` to a sibling In Review."""
    return _card(B, comments=[_comment(FLEET, -600)],
                 blockers=[(SIBLING, "In Review")])


def card_c():
    """Routed PARKED — and told so three days ago, so this sweep says nothing."""
    receipt = routing_verdict.promotion_refusal(C, [PARKED])
    return _card(C, comments=[_comment(PARKED, -4400), _comment(receipt, -4320)])


def card_d():
    """An epic waiting its wave, carrying a three-day-old wave receipt."""
    return _card(D, parent=None, title="[EPIC] the second wave",
                 comments=[_comment("🚨 wave-not-green-lit: DRE-4304 waits for "
                                    "its predecessor in the approved wave.", -4320)])


def card_e():
    """A child of an epic whose second critic is still inside its grace."""
    return _card(E, parent=EPIC_GRACE)


def card_f():
    """FLEET, unblocked, its epic released: promoted and dispatched."""
    return _card(F, comments=[_comment(FLEET, -600)])


def card_g():
    """FLEET, unblocked, held for a human by a pipeline park stamped t0-90."""
    stamp = hold.stamp_line("dead-run-cap", None, "reconcile.py")
    return _card(G, labels=(reconcile.HOLD_LABEL,),
                 comments=[_comment(FLEET, -600), _comment(stamp, -90)])


def card_h():
    """An unresolved `🛑 Agent blocked` marker dated t0-30."""
    return _card(H, comments=[
        _comment(FLEET, -600),
        _comment(f"{reconcile.BLOCKER_MARKER}: the fixture repo has no schema", -30),
    ])


def _thread(*markers):
    return [{"body": plan_critic.cycle_marker(EPIC), "authored_by_pipeline": True}] + [
        {"body": m, "authored_by_pipeline": True} for m in markers
    ]


class _Clock(datetime):
    """`reconcile.datetime` with `now()` pinned to the sweep's fixture time."""

    frozen = T0

    @classmethod
    def now(cls, tz=None):
        return cls.frozen


class _Sweeps:
    """`promote_ready` over the same Backlog, sweep after sweep.

    Shaped after `tests/test_reconcile_promotion.py`'s `_Board`: every gate
    not under test is held open. What differs is time — the clock is a fixture
    per sweep, every posted comment carries the sweep's time, and later sweeps
    read the comments back, as the real board read would.
    """

    def __init__(self, *cards, first_seen_error=None):
        self.cards = list(cards)
        self.first_seen_error = first_seen_error
        self.posted: list[tuple[str, str, str]] = []   # (identifier, body, createdAt)
        self.advanced: list[tuple[str, str]] = []
        self.defects: dict[int, list[str]] = {}
        self.out: dict[int, str] = {}
        self.err: dict[int, str] = {}

    def comments(self, identifier):
        base = next(c["comments"] for c in self.cards if c["identifier"] == identifier)
        return list(base) + [
            {"body": b, "createdAt": t, "user": {"id": "u-pipeline"}}
            for i, b, t in self.posted if i == identifier
        ]

    def _board(self):
        """The Backlog as the query returns it: comments NEWEST-first, the
        order `linear_ops.window_nodes` reverses."""
        moved = {i for i, _ in self.advanced}
        return [
            {**c, "comments": {"nodes": self.comments(c["identifier"])[::-1]}}
            for c in self.cards if c["identifier"] not in moved
        ]

    def sweep(self, minutes, capsys, active_count=0):
        now = T0 + timedelta(minutes=minutes)
        _Clock.frozen = now
        reconcile._stale_defects.clear()
        reconcile._write_failures.clear()

        def green_light(_ops, epic, issue=None):
            if epic == EPIC_GRACE:
                return (now - timedelta(minutes=5)).isoformat()
            return APPROVED

        def epic_thread(epic, *a, **k):
            if epic == EPIC:
                return _thread(plan_critic.marker(plan_critic.STAGE_POST, 1, plan_critic.PASS))
            return _thread()

        def count(identifier, needle, **_kw):
            return sum(1 for c in self.comments(identifier) if needle in (c["body"] or ""))

        def first_at(identifier, needle):
            if self.first_seen_error is not None:
                raise self.first_seen_error
            for c in self.comments(identifier):
                if needle in (c["body"] or ""):
                    return c["createdAt"]
            return None

        def post(identifier, body):
            self.posted.append((identifier, body, now.isoformat()))

        with patch.object(reconcile, "REPO_SLUG", "bureau-pipeline"), \
                patch.object(reconcile, "datetime", _Clock), \
                patch.object(reconcile, "backlog_children", return_value=self._board()), \
                patch.object(reconcile, "epic_records", return_value={}), \
                patch.object(reconcile, "_door_epic_threads", return_value=None), \
                patch.object(reconcile, "epic_blockers_unmet", return_value=False), \
                patch.object(reconcile.mid_epic, "last_green_light", side_effect=green_light), \
                patch.object(reconcile.linear_ops, "comment_records", side_effect=epic_thread), \
                patch.object(reconcile.routing_verdict, "lane_moves", return_value=[]), \
                patch.object(reconcile, "card_state", return_value="Done"), \
                patch.object(reconcile.linear_ops, "add_label"), \
                patch.object(reconcile.linear_ops, "cmd_advance",
                             side_effect=lambda i, to, frm: self.advanced.append((i, to))), \
                patch.object(reconcile.linear_ops, "cmd_comment", side_effect=post), \
                patch.object(reconcile.linear_ops, "count_comments", side_effect=count), \
                patch.object(reconcile.linear_ops, "first_comment_at", side_effect=first_at):
            capsys.readouterr()
            promoted = reconcile.promote_ready(active_count=active_count)
            captured = capsys.readouterr()
        self.defects[minutes] = list(reconcile._stale_defects)
        self.out[minutes] = captured.out
        self.err[minutes] = captured.err
        return promoted

    def run(self, capsys, active_count=0, sweeps=SWEEPS):
        for minutes in sweeps:
            self.sweep(minutes, capsys, active_count)
        return self

    def posted_on(self, identifier, opener=""):
        return [(b, t) for i, b, t in self.posted
                if i == identifier and b.startswith(opener)]

    def idle_line(self, minutes):
        lines = [ln for ln in self.out[minutes].splitlines()
                 if ln.startswith(promotion_stall.IDLE_LINE_OPENER)]
        assert len(lines) <= 1, lines
        return lines[0] if lines else None


@pytest.fixture(autouse=True)
def _clean_ledgers():
    reconcile._stale_defects.clear()
    reconcile._write_failures.clear()
    yield
    reconcile._stale_defects.clear()
    reconcile._write_failures.clear()


def _stall_entry(identifier):
    return f"{promotion_stall.LEDGER_STALL_OPENER}{identifier}:"


def _idle_entry():
    return f"{promotion_stall.LEDGER_IDLE_OPENER}{reconcile.MAX_WIP}:"


# --------------------------------------------------------------------------- #
# Case 1 — the per-card clock, with the declared waits beside it               #
# --------------------------------------------------------------------------- #


class TestCase1ThePerCardClock:
    @pytest.fixture
    def board(self, capsys):
        return _Sweeps(card_a(), card_b(), card_c(), card_d()).run(capsys)

    def test_the_refusal_receipt_is_posted_at_t0_and_never_again(self, board):
        assert [t for _, t in board.posted_on(A, NO_VERDICT_RECEIPT)] == [T0.isoformat()]

    def test_the_idle_board_line_prints_on_every_sweep_naming_one_card_a(self, board):
        for minutes in SWEEPS:
            line = board.idle_line(minutes)
            assert line is not None, minutes
            assert line.startswith(f"{promotion_stall.IDLE_LINE_OPENER}{reconcile.MAX_WIP}:")
            assert " 1 card in Backlog" in line, line
            assert A in line, line
            for other in (B, C, D):
                assert other not in line, line

    def test_the_ledger_counts_sweep_by_sweep(self, board):
        for minutes in (0, 15, 30, 45):
            assert board.defects[minutes] == [], minutes
        for minutes in (60, 75, 90, 105):
            entries = board.defects[minutes]
            assert len(entries) == 1, (minutes, entries)
            assert entries[0].startswith(_idle_entry()), entries
            assert A in entries[0]
        for minutes in (120, 135):
            entries = board.defects[minutes]
            assert len(entries) == 2, (minutes, entries)
            assert sum(e.startswith(_idle_entry()) for e in entries) == 1, entries
            assert sum(e.startswith(_stall_entry(A)) for e in entries) == 1, entries
            log = board.out[minutes] + board.err[minutes]
            for entry in entries:
                assert f"ERROR: {entry}" in log, (entry, log)

    def test_the_stall_receipt_is_posted_once_at_t0_plus_120(self, board):
        stalls = board.posted_on(A, promotion_stall.STALL_MARK)
        assert [t for _, t in stalls] == [(T0 + timedelta(minutes=120)).isoformat()]
        body = stalls[0][0]
        assert routing_verdict.NO_VERDICT_TAG in body
        # The registry's trailer — the receipt is the declared act.
        assert "promotion-stalled" in body.split("\n\n")[-1]

    def test_the_declared_waits_are_never_receipted_ledgered_or_counted(self, board):
        for identifier in (B, C, D):
            assert board.posted_on(identifier) == [], identifier
            for minutes in SWEEPS:
                assert not any(identifier in e for e in board.defects[minutes])

    def test_d_is_skipped_as_today_on_every_sweep(self, board):
        for minutes in SWEEPS:
            assert f"promotion: {D} is an epic" in board.out[minutes]

    def test_the_declared_waits_alone_print_no_idle_line(self, capsys):
        board = _Sweeps(card_b(), card_c(), card_d()).run(capsys)
        for minutes in SWEEPS:
            assert board.idle_line(minutes) is None, board.out[minutes]
            assert board.defects[minutes] == []
        assert board.posted == []


# --------------------------------------------------------------------------- #
# Case 2 — held cards are the alarm, never clocked                             #
# --------------------------------------------------------------------------- #


class TestCase2HeldCardsAreTheAlarm:
    """H is held at the `has an unresolved agent-blocker — skipping` site:
    DRE-6448 had not merged when this was built, so that is the site that
    exists. G is held at the `is held for a human` stand-down skip."""

    @pytest.fixture
    def board(self, capsys):
        return _Sweeps(card_g(), card_h(), card_b()).run(capsys)

    def test_both_are_skipped_with_todays_lines_on_every_sweep(self, board):
        for minutes in SWEEPS:
            out = board.out[minutes]
            assert f"promotion: {G} is held for a human" in out
            assert f"promotion: {H} has an unresolved agent-blocker — skipping" in out

    def test_neither_is_ever_receipted(self, board):
        assert board.posted_on(G) == []
        assert board.posted_on(H) == []
        assert board.posted == []

    def test_the_idle_line_names_two_cards_and_the_lowest(self, board):
        for minutes in SWEEPS:
            line = board.idle_line(minutes)
            assert line is not None, minutes
            assert " 2 cards in Backlog" in line, line
            assert f"lowest {min(G, H)}" in line, line
            assert B not in line

    def test_one_entry_every_sweep_naming_g_whose_stamp_is_oldest(self, board):
        for minutes in SWEEPS:
            entries = board.defects[minutes]
            assert len(entries) == 1, (minutes, entries)
            assert entries[0].startswith(_idle_entry())
            assert f"on {G}" in entries[0], entries[0]
            assert B not in entries[0]
        # 90 minutes at t0: the stamp's age, not the marker's.
        assert "is 1.5h old" in board.defects[0][0]

    def test_hs_first_seen_is_its_markers_created_at(self, capsys):
        board = _Sweeps(card_h()).run(capsys, sweeps=(0, 30))
        # The marker is 30 minutes old at t0 and 60 at t0+30: the entry
        # appears exactly when the marker turns IDLE_BOARD_MINUTES old.
        assert board.defects[0] == []
        assert len(board.defects[30]) == 1
        assert f"on {H}" in board.defects[30][0]
        assert "is 1.0h old" in board.defects[30][0]


def test_a_card_held_as_an_operator_step_but_routed_fleet_is_a_needs_human_record(capsys):
    """DRE-6427's second exit: the card stays in Backlog wearing the label, so
    it is a `needs-human` record dated by its stamp — counted, never clocked."""
    stamp = hold.stamp_line(hold.OPERATOR_STEP_REASON, None, "reconcile.py")
    card = _card(OP, labels=(reconcile.HOLD_LABEL,),
                 comments=[_comment(FLEET, -600), _comment(stamp, -90)])
    board = _Sweeps(card).run(capsys, sweeps=(0,))
    assert f"promotion: {OP} is held as an operator step but routed" in board.out[0]
    assert board.posted == []
    assert len(board.defects[0]) == 1
    assert board.defects[0][0].startswith(_idle_entry())
    assert f"on {OP}" in board.defects[0][0]


# --------------------------------------------------------------------------- #
# Case 3 — unknown age stands but never ages                                   #
# --------------------------------------------------------------------------- #


class TestCase3UnknownAgeNeverAges:
    def test_e_counts_toward_the_line_and_never_names_the_entry(self, capsys):
        board = _Sweeps(card_a(), card_e()).run(capsys)
        assert board.posted_on(E) == []
        for minutes in SWEEPS:
            line = board.idle_line(minutes)
            assert line is not None and " 2 cards in Backlog" in line, line
        for minutes in (0, 15, 30, 45):
            assert not any(e.startswith(_idle_entry()) for e in board.defects[minutes])
        for minutes in SWEEPS[4:]:
            idle = [e for e in board.defects[minutes] if e.startswith(_idle_entry())]
            assert len(idle) == 1, (minutes, board.defects[minutes])
            assert f"on {A}" in idle[0]
            assert E not in idle[0]

    def test_e_alone_prints_the_line_and_never_turns_red(self, capsys):
        board = _Sweeps(card_e()).run(capsys)
        for minutes in SWEEPS:
            assert f"promotion: {E} is not being promoted" in board.out[minutes]
            assert board.idle_line(minutes) is not None, minutes
            assert board.defects[minutes] == [], minutes
        assert board.posted == []


# --------------------------------------------------------------------------- #
# Case 4 — a sweep that dispatched is not idle                                 #
# --------------------------------------------------------------------------- #


def test_case_4_a_sweep_that_dispatched_is_not_idle(capsys):
    board = _Sweeps(card_a(), card_f())
    assert board.sweep(0, capsys) == 1
    assert (F, "Todo") in board.advanced
    assert f"(WIP 0+1/{reconcile.MAX_WIP})" in board.out[0]
    assert board.idle_line(0) is None
    assert promotion_stall.IDLE_LINE_OPENER not in board.out[0]
    assert board.defects[0] == []
    assert len(board.posted_on(A, NO_VERDICT_RECEIPT)) == 1


# --------------------------------------------------------------------------- #
# Case 5 — the per-card clock does not depend on WIP                           #
# --------------------------------------------------------------------------- #


def test_case_5_the_per_card_clock_does_not_depend_on_wip(capsys):
    board = _Sweeps(card_a()).run(capsys, active_count=1)
    assert [t for _, t in board.posted_on(A, NO_VERDICT_RECEIPT)] == [T0.isoformat()]
    stalls = board.posted_on(A, promotion_stall.STALL_MARK)
    assert [t for _, t in stalls] == [(T0 + timedelta(minutes=120)).isoformat()]
    assert f"({1}/{reconcile.MAX_WIP} on this sweep)" in stalls[0][0]
    for minutes in SWEEPS:
        assert board.idle_line(minutes) is None, minutes
        assert not any(e.startswith(_idle_entry()) for e in board.defects[minutes])
    for minutes in SWEEPS[:8]:
        assert board.defects[minutes] == [], minutes
    for minutes in (120, 135):
        assert len(board.defects[minutes]) == 1, board.defects[minutes]
        assert board.defects[minutes][0].startswith(_stall_entry(A))


# --------------------------------------------------------------------------- #
# Case 6 — the recurrence acceptance, asserted                                 #
# --------------------------------------------------------------------------- #


def test_case_6_a_recurring_refusal_is_clocked_from_its_first_receipt(capsys):
    """THE ACCEPTED BEHAVIOR (DRE-4210, item 2), written as a test.

    `_surface_once` posts a refusal once, ever, and the clock reads the oldest
    comment carrying the tag — the same substring match the idempotency keys
    on. So a refusal that cleared (a person stamped the verdict) and later
    recurred (the verdict is gone again) is clocked from its FIRST receipt and
    can be alarmed on the first sweep it recurs. That is wanted: the card is
    really refused again for a reason a person must clear. `notice` names the
    receipt's time, so the age it states is the age of something on the card,
    not a claim of an unbroken wait."""
    first = routing_verdict.promotion_refusal(A2, [])
    card = _card(A2, comments=[
        _comment(first, -180),
        _comment("Stamped the verdict on this one by hand — should go now.", -120),
    ])
    board = _Sweeps(card)
    board.sweep(0, capsys)
    assert f"promotion: {A2} is not being promoted" in board.out[0]
    # Nothing new under the old tag: the old receipt still carries it.
    assert board.posted_on(A2, NO_VERDICT_RECEIPT) == []
    stalls = board.posted_on(A2, promotion_stall.STALL_MARK)
    assert len(stalls) == 1, board.posted
    body = stalls[0][0]
    assert dead_run.pacific(T0 - timedelta(minutes=180)) in body, body
    assert "3.0 hours" in body, body
    assert any(e.startswith(_stall_entry(A2)) for e in board.defects[0]), board.defects[0]


def test_an_older_comment_that_merely_mentions_the_tag_sets_the_age(capsys):
    """The second half of the acceptance: a person's comment quoting the tag
    is a comment carrying it, so it dates the clock exactly as it already
    suppresses the receipt today."""
    card = _card(A2, comments=[
        _comment(f"Why is this {routing_verdict.NO_VERDICT_TAG}? Planning stamps it.", -150),
    ])
    board = _Sweeps(card)
    board.sweep(0, capsys)
    assert board.posted_on(A2, NO_VERDICT_RECEIPT) == []
    stalls = board.posted_on(A2, promotion_stall.STALL_MARK)
    assert len(stalls) == 1, board.posted
    assert "2.5 hours" in stalls[0][0]


# --------------------------------------------------------------------------- #
# An unreadable first receipt is unknown — never stale, never fatal            #
# --------------------------------------------------------------------------- #


def test_a_first_seen_read_that_raises_is_said_once_and_treated_as_unknown(capsys):
    board = _Sweeps(card_a(), card_b(),
                    first_seen_error=reconcile.linear_ops.LinearError("Linear said 502"))
    board.run(capsys, sweeps=(0, 135))
    for minutes in (0, 135):
        log = board.out[minutes] + board.err[minutes]
        said = [ln for ln in log.splitlines() if "Linear said 502" in ln]
        assert len(said) == 1, said
        assert A in said[0]
        assert board.posted_on(A, promotion_stall.STALL_MARK) == []
        assert board.defects[minutes] == []
        line = board.idle_line(minutes)
        assert line is not None and A in line, board.out[minutes]
        # The sweep went on past A: B, after it, was still read and said.
        assert f"promotion: {B} is held by" in board.out[minutes]
        assert "card(s) promoted" in board.out[minutes]
