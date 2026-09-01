"""RED-first tests: the sweep's Linear request count must not scale with the
board (DRE-2929).

THE INCIDENT (2026-09-01). The reconcile sweep spent one Linear request per
card, per sweep, per repo, fetching comment bodies it usually threw away. Four
repos' sweeps together exhausted the 2,500/hour workspace quota and every card
in the fleet stopped for seven hours. The peak hour was ~85% of the ceiling
with every unmeasured term at its floor, so this is structural rather than a
bad day — and `flag_stalled_planning` was the pathological term: it has no repo
filter at all, so every repo's sweep paid one request for every Planning card
on the whole board, including repos with no candidates.

WHAT IS UNDER TEST:
  * `active_cards` selects `comments(last: 50)` inline — the shape
    `backlog_children` already uses — so one paged read replaces every per-card
    comment fetch in both watchdogs.
  * `flag_stalled_planning` filters by repo, the way `flag_stranded` does: a
    card labelled for another repo is that repo's sweep's business.
  * The age gate runs BEFORE anything reads the card's comments, in both
    watchdogs.
  * `active_cards()` is read ONCE per sweep, shared across its call sites —
    `SWEPT_LANES` already unions the lane sets those callers ask for.
  * A whole sweep over a fixed fake board stays inside a stated request budget,
    and tripling the board does not move it. The next N+1 fails this suite
    rather than the quota.

None of these tests may change WHICH cards the sweep promotes, holds or
comments on — this card is cost only.

Run: cd bureau-pipeline && python3 -m pytest tests/test_sweep_request_budget.py -v
"""
from __future__ import annotations

import contextlib
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")
os.environ.setdefault("GH_TOKEN", "x")

import reconcile  # noqa: E402
import validate_card  # noqa: E402


@pytest.fixture(autouse=True)
def _pin_repo_slug(monkeypatch):
    """reconcile.REPO_SLUG is bound at import; pin it so these cards are
    recognised regardless of collection order (the hazard test_human_hold.py
    pins for the same reason)."""
    monkeypatch.setattr(reconcile, "REPO_SLUG", "agent-bureau")


@pytest.fixture(autouse=True)
def _pin_valid_slugs(monkeypatch):
    monkeypatch.setattr(
        validate_card, "VALID_SLUGS", {"agent-bureau", "atlas", "bureau-pipeline"}
    )


@pytest.fixture(autouse=True)
def _pin_live_snapshot(monkeypatch):
    """No live gh fetch from a test run (DRE-2260's canonical-snapshot
    re-check)."""
    monkeypatch.setattr(
        reconcile,
        "live_rail_slugs",
        lambda: frozenset({"agent-bureau", "atlas", "bureau-pipeline"}),
        raising=False,
    )


@pytest.fixture(autouse=True)
def _clean_ledgers():
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()
    reconcile._stale_defects.clear()
    yield
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()
    reconcile._stale_defects.clear()


def _iso(minutes_ago: float) -> str:
    return (
        (datetime.now(UTC) - timedelta(minutes=minutes_ago))
        .isoformat()
        .replace("+00:00", "Z")
    )


def _card(
    identifier="DRE-2929",
    state="Todo",
    labels=("repo:agent-bureau",),
    minutes_stale=45.0,
    bodies=(),
):
    """A card in the shape `active_cards` returns it — comments included,
    because that is the whole point of this card."""
    return {
        "id": f"uuid-{identifier}",
        "identifier": identifier,
        "title": "a card the sweep must not pay per-card for",
        "description": "work",
        "createdAt": _iso(minutes_stale),
        "updatedAt": _iso(minutes_stale),
        "state": {"name": state},
        "labels": {"nodes": [{"name": n} for n in labels]},
        "comments": {"nodes": [{"body": b} for b in bodies]},
    }


# The single-issue comment query every per-card fetch in the pipeline sends
# (linear_ops.comment_bodies / count_comments). Recognised by its filter so the
# fake can count it separately from the board reads — a fetch of this shape,
# once per card, is exactly what exhausted the quota.
_PER_CARD_COMMENTS = "query($id: String!) { issue(id: $id) {"
_BOARD_READ = "state: {name: {in: $states}}"
_BACKLOG_READ = 'state: {name: {eq: "Backlog"}}'


class FakeLinear:
    """A Linear that answers the sweep's reads and counts every request.

    It honours the lane filter the way Linear does, so a query for one lane set
    never sees another's cards — the property the sweep's correctness rests on.
    """

    def __init__(self, active=(), backlog=()):
        self.active = list(active)
        self.backlog = list(backlog)
        self.queries: list[str] = []

    # -- counters ---------------------------------------------------------
    @property
    def requests(self) -> int:
        return len(self.queries)

    @property
    def board_reads(self) -> int:
        return sum(1 for q in self.queries if _BOARD_READ in q)

    @property
    def per_card_reads(self) -> int:
        return sum(1 for q in self.queries if q.lstrip().startswith(_PER_CARD_COMMENTS))

    # -- the seam ---------------------------------------------------------
    def gql(self, query, variables=None):
        self.queries.append(query)
        if _BOARD_READ in query:
            wanted = set((variables or {}).get("states") or ())
            nodes = [c for c in self.active if c["state"]["name"] in wanted]
            return {"issues": {"nodes": nodes, "pageInfo": {"hasNextPage": False}}}
        if _BACKLOG_READ in query:
            return {
                "issues": {
                    "nodes": list(self.backlog),
                    "pageInfo": {"hasNextPage": False},
                }
            }
        if query.lstrip().startswith(_PER_CARD_COMMENTS):
            ident = (variables or {}).get("id")
            for card in self.active + self.backlog:
                if card["identifier"] == ident:
                    return {"issue": {"comments": card["comments"]}}
            return {"issue": {"comments": {"nodes": []}}}
        raise AssertionError(f"unexpected Linear query: {query}")


@contextlib.contextmanager
def _linear(fake):
    """Point every Linear read at `fake` and stub every write."""
    with patch.object(
        reconcile.linear_ops, "gql", side_effect=fake.gql
    ), patch.object(
        reconcile.linear_ops, "cmd_comment"
    ), patch.object(
        reconcile.linear_ops, "cmd_advance"
    ), patch.object(
        reconcile.linear_ops, "cmd_state"
    ), patch.object(
        reconcile.linear_ops, "add_label"
    ):
        reconcile.reset_sweep_cards()
        yield


# --------------------------------------------------------------------------
# 1: the board read carries the comments
# --------------------------------------------------------------------------
def test_active_cards_selects_the_cards_comments_inline():
    """The shape `backlog_children` already uses (`comments(last: 50)`), so the
    watchdogs read bodies off the card they were handed."""
    fake = FakeLinear()
    with _linear(fake):
        reconcile.active_cards(reconcile.WATCHDOG_LANES)
    assert fake.board_reads == 1
    assert "comments(last: 50)" in fake.queries[0], (
        "active_cards must select the card's comments inline — without them "
        "every watchdog pays one request per card"
    )


def test_card_comment_bodies_reads_the_query_the_card_came_from():
    """Same list `linear_ops.comment_bodies` returns, oldest→newest, for zero
    requests."""
    card = _card(bodies=("first", "second"))
    assert reconcile.card_comment_bodies(card) == ["first", "second"]
    assert reconcile.card_comment_bodies(_card()) == []


# --------------------------------------------------------------------------
# 2: neither watchdog issues a per-card comment fetch
# --------------------------------------------------------------------------
def _watchdog_requests(card_count: int) -> FakeLinear:
    """Run the whole watchdog (both passes) over `card_count` Todo cards and
    `card_count` Planning cards, and report what it cost."""
    active = [
        _card(f"DRE-{9000 + n}", state="Todo", minutes_stale=45.0)
        for n in range(card_count)
    ] + [
        _card(f"DRE-{9500 + n}", state="Planning", minutes_stale=6000.0)
        for n in range(card_count)
    ]
    fake = FakeLinear(active=active)
    with _linear(fake):
        reconcile.flag_stranded()
    return fake


@pytest.mark.parametrize("card_count", [1, 12, 60])
def test_neither_watchdog_fetches_comments_per_card(card_count):
    """THE COST BUG: one request per card, per sweep, per repo."""
    fake = _watchdog_requests(card_count)
    assert fake.per_card_reads == 0, (
        f"{fake.per_card_reads} per-card comment fetch(es) for {card_count} "
        "cards — the bodies come with the board read now"
    )


def test_watchdog_request_count_is_independent_of_card_count():
    """The regression this card exists to make impossible: a bigger board must
    cost the same number of requests."""
    small = _watchdog_requests(2).requests
    large = _watchdog_requests(60).requests
    assert small == large, (
        f"2 cards cost {small} request(s) and 60 cost {large} — the sweep is "
        "still paying per card"
    )


# --------------------------------------------------------------------------
# 3: the Planning watchdog has a repo filter
# --------------------------------------------------------------------------
def test_planning_watchdog_ignores_another_repos_cards():
    """A sweep for repo A must make no request on account of repo B's Planning
    cards, and must not speak on them either — that is B's sweep's job, exactly
    as it already is in flag_stranded."""
    mine = _card("DRE-1000", state="Planning", labels=("repo:agent-bureau",),
                 minutes_stale=6000.0)
    theirs = _card("DRE-1001", state="Planning", labels=("repo:atlas",),
                   minutes_stale=6000.0)
    fake = FakeLinear(active=[mine, theirs])
    with _linear(fake), patch.object(
        reconcile.routing_verdict, "is_parked", side_effect=lambda b: False
    ) as parked, patch.object(
        reconcile.linear_ops, "cmd_comment"
    ) as comment:
        flagged = reconcile.flag_stalled_planning()
    assert flagged == {"DRE-1000"}, "this repo's stalled Planning card still flags"
    assert parked.call_count == 1, (
        "another repo's Planning card must not be examined at all — its "
        "comments are a request this sweep has no business paying for"
    )
    assert [c.args[0] for c in comment.call_args_list] == ["DRE-1000"]


def test_planning_watchdog_still_sees_a_card_with_no_repo_label():
    """The whole front path arrives in Planning without a `repo:` label —
    assigning one is what Planning does — so the filter must skip only cards
    labelled for a DIFFERENT repo (flag_stranded's exact rule)."""
    unlabelled = _card("DRE-1002", state="Planning", labels=(), minutes_stale=6000.0)
    fake = FakeLinear(active=[unlabelled])
    with _linear(fake):
        flagged = reconcile.flag_stalled_planning()
    assert flagged == {"DRE-1002"}


# --------------------------------------------------------------------------
# 4: the age gate runs before any comment read
# --------------------------------------------------------------------------
def test_stranded_watchdog_gates_on_age_before_reading_comments():
    """A card too young to flag must never have its comments examined."""
    young = _card("DRE-1003", state="Todo",
                  minutes_stale=reconcile.WATCHDOG_MINUTES - 5)
    fake = FakeLinear(active=[young])
    with _linear(fake), patch.object(
        reconcile.routing_verdict, "is_parked", side_effect=lambda b: False
    ) as parked:
        reconcile.flag_stranded()
    assert parked.call_count == 0, (
        "the age gate must run before anything reads the card's comments"
    )


def test_planning_watchdog_gates_on_age_before_reading_comments():
    young = _card("DRE-1004", state="Planning",
                  minutes_stale=reconcile.PLANNING_MINUTES - 5)
    fake = FakeLinear(active=[young])
    with _linear(fake), patch.object(
        reconcile.routing_verdict, "is_parked", side_effect=lambda b: False
    ) as parked:
        reconcile.flag_stalled_planning()
    assert parked.call_count == 0, (
        "the age gate must run before anything reads the card's comments"
    )


# --------------------------------------------------------------------------
# 5: one board read per sweep, across every call site
# --------------------------------------------------------------------------
def _sweep_mocks():
    """Every seam a full sweep touches that would reach GitHub. The Linear
    reads under test — both watchdogs, the Intake gate, the nudge loop's own
    read and the promotion gate — stay REAL, or this measures nothing."""
    return [
        mock.patch.object(reconcile, name)
        for name in (
            "drain_retiring_lanes", "unstick_conflicts", "retrigger_dead_heads",
            "flag_no_checks_prs", "flag_unowned_prs", "flag_unlanded_work",
            "fix_approved_but_red", "retry_dead_fix_runs",
            "restart_answered_blockers", "review_dependabot_prs",
            "recover_crashed_reviews", "check_dependabot_capacity",
            "close_finished_epics", "report_break_glass",
            "report_fix_concurrency", "report_evicted_fix_runs",
            "report_epic_growth",
        )
    ]


def _run_sweep(fake) -> FakeLinear:
    with contextlib.ExitStack() as stack:
        for m in _sweep_mocks():
            stack.enter_context(m)
        stack.enter_context(_linear(fake))
        reconcile.main()
    return fake


def _fixed_board(scale: int = 1):
    """A board whose every card is one the sweep must LOOK at: stale Todo
    cards with no run receipt (the watchdog's own case, which then makes the
    nudge loop skip them), stale Planning cards, and aged Intake cards."""
    active = []
    for n in range(4 * scale):
        active.append(_card(f"DRE-{7000 + n}", state="Todo", minutes_stale=600.0))
        active.append(_card(f"DRE-{7300 + n}", state="Planning",
                            minutes_stale=6000.0))
        active.append(_card(f"DRE-{7600 + n}", state="Intake", labels=(),
                            minutes_stale=reconcile.INTAKE_MAX_AGE_MINUTES + 600))
    backlog = [
        _card(f"DRE-{8000 + n}", state="Backlog", labels=("repo:atlas",))
        for n in range(4 * scale)
    ]
    return active, backlog


def test_the_board_is_read_once_per_sweep():
    """Four call sites — both watchdogs, the Intake gate and the nudge loop —
    and SWEPT_LANES already unions the lanes they ask for, so one read serves
    all of them."""
    active, backlog = _fixed_board()
    fake = _run_sweep(FakeLinear(active=active, backlog=backlog))
    assert fake.board_reads == 1, (
        f"{fake.board_reads} board reads in one sweep — active_cards() must be "
        "read once and shared"
    )


def test_a_lane_outside_the_swept_union_still_gets_its_own_read():
    """The cache serves the union it read. A caller asking for a lane outside
    SWEPT_LANES must still get a real query rather than a silently empty
    answer — `drain_retiring_lanes` is exactly that caller."""
    outside = _card("DRE-1005", state="Duplicate")
    fake = FakeLinear(active=[outside])
    with _linear(fake):
        reconcile.active_cards(reconcile.WATCHDOG_LANES)
        got = reconcile.active_cards(("Duplicate",))
    assert [c["identifier"] for c in got] == ["DRE-1005"]
    assert fake.board_reads == 2


# --------------------------------------------------------------------------
# 6: the budget itself
# --------------------------------------------------------------------------
# What one full sweep may spend on Linear, over ANY board. Two paged reads —
# the active lanes and the Backlog — plus at most INTAKE_ESCALATION_CAP stated-
# reason reads, which are capped per sweep by construction. Raising this number
# is a decision, and it is made here rather than discovered by a quota
# exhaustion at 11am (2026-09-01: 2,500/hr, seven hours of a stopped fleet).
SWEEP_REQUEST_BUDGET = 2 + reconcile.INTAKE_ESCALATION_CAP


def test_one_sweep_stays_within_the_request_budget():
    active, backlog = _fixed_board()
    fake = _run_sweep(FakeLinear(active=active, backlog=backlog))
    assert fake.requests <= SWEEP_REQUEST_BUDGET, (
        f"one sweep spent {fake.requests} Linear requests, budget "
        f"{SWEEP_REQUEST_BUDGET}: {fake.queries}"
    )


def test_the_budget_does_not_move_when_the_board_triples():
    """The structural claim, stated as a test: sweep cost is a function of the
    LANES, not of the cards in them."""
    small = _run_sweep(FakeLinear(*_fixed_board(1))).requests
    large = _run_sweep(FakeLinear(*_fixed_board(3))).requests
    assert small == large, (
        f"a board of 12 active cards cost {small} request(s) and one of 36 "
        f"cost {large} — the sweep still scales with the board"
    )
