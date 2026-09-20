"""RED-first: every sweep says what each phase cost it in Linear requests
(DRE-3639).

THE GAP. A live sweep prints one `linear-budget:` line as it exits, and that
is the whole of what it says about its spend. Measured read-only on
2026-09-12 against this repo's board, one full pass cost 65 Linear reads: 18
in `promote_ready`, 18 in `report_epic_growth`, 15 in the Intake age-out
DRE-4141 has since deleted, 9 in `close_finished_epics`, 4 for the board itself
and 1 for the break-glass count. None of that was readable from the run log, so a cut could not show its
effect live and a regression could not be placed.

WHAT IS UNDER TEST:
  * `linear_ops.requests_made()` reads out the count `gql` already keeps in
    its budget ledger, and never resets it.
  * A full sweep prints `sweep-spend: <phase> <n> request(s)` once per phase
    that made a request, `<n>` being what that phase actually spent — measured
    here by an independent wrapper round the phase, off the fake's own counter.
  * A phase that made no request prints nothing; the total prints always,
    including when it is zero.
  * The last line of every pass is
    `sweep-spend: total <n> request(s) over <k> phase(s)`, `<n>` the fake's
    own total and `<k>` the number of phase lines above it.
  * The event-driven modes print the same lines for the phases they run:
    `--promote-only` prints the gate and the total, and nothing else.

Both lines go to STDOUT, unlike the `linear-budget:` trailer, which stays on
stderr for the workflows that read this script's stdout through `$(...)`.

Run: cd bureau-pipeline && python3 -m pytest tests/test_sweep_spend_lines.py -v
"""
from __future__ import annotations

import contextlib
import functools
import json
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

import linear_ops  # noqa: E402
import reconcile  # noqa: E402
import validate_card  # noqa: E402

#: The phases a full pass runs that this suite measures independently. Each is
#: a function `main()` calls by name, so it can be wrapped from outside and
#: charged without the implementation's help — which is the whole point: a test
#: that read the same counter the same way would prove only that arithmetic
#: works.
#: An Intake card far past any window the lane ever had. A literal since
#: DRE-4141: the sweep has no opinion about how old an Intake card is any
#: more, so a fixture that asked the code for one would have nothing to ask.
_THIRTY_DAYS = 30 * 24 * 60


MEASURED_PHASES = (
    "flag_stranded",
    "report_intake_depth",
    "close_finished_epics",
    "promote_ready",
)


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
        reconcile,
        "live_rail_slugs",
        lambda: frozenset({"agent-bureau", "atlas", "bureau-pipeline"}),
        raising=False,
    )


@pytest.fixture(autouse=True)
def _clean_ledgers(monkeypatch):
    monkeypatch.delenv("MERGED_CARD", raising=False)
    ledgers = (
        reconcile._write_failures,
        reconcile._read_failures,
        reconcile._stale_defects,
    )
    for ledger in ledgers:
        ledger.clear()
    yield
    for ledger in ledgers:
        ledger.clear()


def _iso(minutes_ago: float) -> str:
    return (
        (datetime.now(UTC) - timedelta(minutes=minutes_ago))
        .isoformat()
        .replace("+00:00", "Z")
    )


def _card(
    identifier="DRE-3639",
    state="Todo",
    labels=("repo:agent-bureau",),
    minutes_stale=600.0,
    bodies=(),
):
    """A card in the shape `active_cards` returns it — comments inline."""
    return {
        "id": f"uuid-{identifier}",
        "identifier": identifier,
        "title": "a card whose sweep must say what it cost",
        "description": "work",
        "createdAt": _iso(minutes_stale),
        "updatedAt": _iso(minutes_stale),
        "state": {"name": state},
        "labels": {"nodes": [{"name": n} for n in labels]},
        # NEWEST FIRST, the order Linear answers a comment window in (DRE-3250).
        "comments": {"nodes": [{"body": b} for b in reversed(list(bodies))]},
    }


_PER_CARD_COMMENTS = "query($id: String!) { issue(id: $id) {"
_BOARD_READ = "state: {name: {in: $states}}"
_BACKLOG_READ = 'state: {name: {eq: "Backlog"}}'


class FakeLinear:
    """A Linear that answers the sweep's reads and counts every request. It is
    the counter under test: `linear_ops.requests_made` is pointed at
    `self.requests` for the pass, so what the sweep prints has to match what
    this object saw."""

    def __init__(self, active=(), backlog=()):
        self.active = list(active)
        self.backlog = list(backlog)
        self.queries: list[str] = []

    @property
    def requests(self) -> int:
        return len(self.queries)

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
def _linear(fake: FakeLinear):
    """Point every Linear read at `fake`, stub every write, and make the seam's
    own request count the fake's count — `requests_made()` is what the sweep
    reads, and in a test the transport under it is this object."""
    with patch.object(linear_ops, "gql", side_effect=fake.gql), \
            patch.object(linear_ops, "requests_made", lambda: fake.requests), \
            patch.object(linear_ops, "cmd_comment"), \
            patch.object(linear_ops, "cmd_advance"), \
            patch.object(linear_ops, "cmd_state"), \
            patch.object(linear_ops, "add_label"), \
            patch.object(linear_ops, "remove_label"):
        reconcile.reset_sweep_cards()
        yield


def _sweep_mocks(extra=()):
    """Every seam a full sweep touches that would reach GitHub. The Linear
    reads under test stay REAL — both watchdogs, the Intake gate, the promotion
    gate and the epic close — or this measures nothing.

    Four of these read the BOARD as well as GitHub — `recover_limit_deaths`,
    `card_dependabot_prs`, `report_fleet_reviewer_outage` and, since DRE-4411,
    `settle_repair_cards` — and every one of them runs in `main()`'s backstop
    loop, ahead of the explicit `board_read` phase. They are stood down here for
    both reasons at once: unstubbed they would shell out to `gh`, and left real
    the FIRST of them to reach `active_cards()` pays for the pass's one shared
    board read (DRE-2929) — which is the whole cost
    `test_the_board_read_is_its_own_phase` below stands the rest of the pass
    down to observe. Live, that payer is `recover_limit_deaths`: it is the
    earliest of the four and the only one that reads the board unconditionally.
    So a new backstop calling `active_cards()` belongs in this tuple, and
    leaving one out moves the snapshot's cost onto that backstop's name in THIS
    fixture, where everything ahead of it is a stub — never in a live sweep."""
    names = (
        "drain_retiring_lanes", "unstick_conflicts", "refresh_stale_merge_refs",
        "retrigger_dead_heads", "flag_no_checks_prs", "flag_unowned_prs",
        "flag_unlanded_work", "fix_approved_but_red", "retry_dead_fix_runs",
        "redispatch_standing_verdicts", "recover_limit_deaths",
        "restart_answered_blockers", "review_dependabot_prs",
        "card_dependabot_prs", "recover_crashed_reviews",
        "report_fleet_reviewer_outage", "check_dependabot_capacity",
        "settle_repair_cards",
        "report_break_glass", "report_fix_concurrency",
        "report_evicted_fix_runs", "report_epic_growth",
    ) + tuple(extra)
    return [mock.patch.object(reconcile, name) for name in names]


def _instrument(stack, fake: FakeLinear, spent: dict, *names: str):
    """Charge each named phase from OUTSIDE the sweep: the fake's own count
    before and after the call. This is the number the printed line has to
    equal."""
    def wrap(name: str, real):
        @functools.wraps(real)
        def wrapper(*a, **kw):
            before = fake.requests
            try:
                return real(*a, **kw)
            finally:
                spent[name] = spent.get(name, 0) + (fake.requests - before)
        return wrapper

    for name in names:
        real = getattr(reconcile, name)
        spent.setdefault(name, 0)
        stack.enter_context(mock.patch.object(reconcile, name, wrap(name, real)))


def _run_sweep(
    fake: FakeLinear, *, spent: dict | None = None, mocks=(), replace=(), **main_kw
):
    """One pass over `fake`, with the GitHub seams stubbed. Returns nothing;
    the caller reads the lines off capsys. `replace` stands a named seam in
    for itself with a real function, where a stub that spends nothing would
    measure nothing."""
    replaced = {fn.__name__ for fn in replace}
    with contextlib.ExitStack() as stack:
        for m in _sweep_mocks(n for n in mocks if n not in replaced):
            stack.enter_context(m)
        for fn in replace:
            stack.enter_context(mock.patch.object(reconcile, fn.__name__, fn))
        stack.enter_context(mock.patch.object(reconcile, "MAX_WIP", 1000))
        stack.enter_context(mock.patch.object(reconcile, "redispatch", return_value=True))
        stack.enter_context(mock.patch.object(reconcile, "_nudge", return_value=False))
        if "pr_for" not in replaced:
            stack.enter_context(mock.patch.object(reconcile, "pr_for", return_value=None))
        stack.enter_context(_linear(fake))
        if spent is not None:
            _instrument(stack, fake, spent, *MEASURED_PHASES)
        with contextlib.suppress(SystemExit):
            reconcile.main(**main_kw)


def _spend_lines(capsys) -> list[str]:
    return [
        line for line in capsys.readouterr().out.splitlines()
        if line.startswith("sweep-spend:")
    ]


def _phase_lines(lines: list[str]) -> dict[str, int]:
    """The per-phase lines as `{phase: count}`, asserting the exact wording of
    each — `sweep-spend: <phase> <n> request(s)`."""
    out: dict[str, int] = {}
    for line in lines:
        if line.startswith("sweep-spend: total "):
            continue
        parts = line.split()
        assert len(parts) == 4 and parts[3] == "request(s)", (
            f"a phase line must read `sweep-spend: <phase> <n> request(s)`: {line!r}"
        )
        assert parts[1] not in out, f"phase {parts[1]} printed twice: {lines}"
        out[parts[1]] = int(parts[2])
    return out


def _total_line(lines: list[str]) -> tuple[int, int]:
    """The total as `(requests, phases)`, asserting it is the LAST line."""
    assert lines, "a pass must print its total even when it spent nothing"
    total = lines[-1]
    parts = total.split()
    assert parts[:2] == ["sweep-spend:", "total"] and parts[3] == "request(s)" \
        and parts[4] == "over" and parts[6] == "phase(s)", (
        "the last line of a pass must read `sweep-spend: total <n> request(s) "
        f"over <k> phase(s)`: {total!r}"
    )
    return int(parts[2]), int(parts[5])


def _busy_board(scale: int = 1):
    """A board whose every card is one the sweep must LOOK at: stale Todo
    cards, stale Planning cards, old Intake cards, and another repo's Backlog.
    The shape `test_sweep_request_budget` measures its budget over."""
    active = []
    for n in range(4 * scale):
        active.append(_card(f"DRE-{7000 + n}", state="Todo", minutes_stale=600.0))
        active.append(_card(f"DRE-{7300 + n}", state="Planning", minutes_stale=6000.0))
        active.append(_card(f"DRE-{7600 + n}", state="Intake", labels=(),
                            minutes_stale=_THIRTY_DAYS))
    backlog = [
        _card(f"DRE-{8000 + n}", state="Backlog", labels=("repo:atlas",))
        for n in range(4 * scale)
    ]
    return active, backlog


# --------------------------------------------------------------------------
# 1: the seam's own counter
# --------------------------------------------------------------------------
def test_requests_made_reads_out_the_count_gql_already_keeps(monkeypatch):
    """The count is `gql`'s, read out — not a second ledger that can disagree
    with the `linear-budget:` line about the same hour."""
    monkeypatch.setenv("LINEAR_API_KEY", "test-key")
    assert linear_ops.requests_made() == 0

    class _Resp:
        headers = {"x-ratelimit-requests-remaining": "2400"}

        def read(self):
            return json.dumps({"data": {"ok": True}}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    monkeypatch.setattr(linear_ops.urllib.request, "urlopen", lambda *a, **k: _Resp())
    linear_ops.gql("query { viewer { id } }")
    linear_ops.gql("query { viewer { id } }")
    assert linear_ops.requests_made() == 2


def test_requests_made_never_resets_the_ledger():
    """Reading the count must not zero it: the exit line is computed from the
    same number, and a reader that could reset it would make a sweep's whole
    spend disappear from the fleet's log."""
    linear_ops._budget["calls"] = 7
    assert [linear_ops.requests_made() for _ in range(3)] == [7, 7, 7]
    assert linear_ops._budget["calls"] == 7


# --------------------------------------------------------------------------
# 2: a full sweep prints one line per spending phase
# --------------------------------------------------------------------------
def test_a_full_sweep_prints_what_each_phase_spent(capsys):
    """Every phase that made a request names itself and its count, and the
    count is the one an independent wrapper round that phase measured."""
    spent: dict[str, int] = {}
    fake = FakeLinear(*_busy_board())
    _run_sweep(fake, spent=spent)
    lines = _spend_lines(capsys)
    printed = _phase_lines(lines)

    charged = {name: n for name, n in spent.items() if n > 0}
    assert charged, (
        "the fixture must make some phase spend a request, or this test "
        f"asserts nothing: {spent}"
    )
    for name, n in charged.items():
        assert printed.get(name) == n, (
            f"phase {name} made {n} request(s) and the sweep printed "
            f"{printed.get(name)}: {lines}"
        )


def test_a_full_sweeps_total_is_the_whole_passs_spend(capsys):
    """The total is what the pass spent, and it counts the phase lines above
    it — so a reader can add the lines up and see what is unattributed."""
    fake = FakeLinear(*_busy_board())
    _run_sweep(fake)
    lines = _spend_lines(capsys)
    total, phases = _total_line(lines)
    assert total == fake.requests, (
        f"the pass spent {fake.requests} request(s) and reported {total}: {lines}"
    )
    assert phases == len(lines) - 1, (
        f"the total counts {phases} phase(s) over {len(lines) - 1} phase line(s)"
    )
    assert sum(_phase_lines(lines).values()) <= total


def test_a_phase_that_spent_nothing_prints_no_line(capsys):
    """A quiet phase adds no noise. `close_finished_epics` over a board with no
    epics runs and spends nothing — it must not print a zero."""
    spent: dict[str, int] = {}
    fake = FakeLinear(*_busy_board())
    _run_sweep(fake, spent=spent)
    lines = _spend_lines(capsys)
    quiet = [name for name, n in spent.items() if n == 0]
    assert quiet, "the fixture must run a phase that spends nothing"
    printed = _phase_lines(lines)
    for name in quiet:
        assert name not in printed, (
            f"phase {name} spent nothing and still printed a line: {lines}"
        )


def test_the_total_prints_even_when_the_pass_spent_nothing(capsys):
    """Always the total, including zero: a pass that says nothing at all is
    indistinguishable from a pass whose spend nobody instrumented."""
    fake = FakeLinear()
    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.object(reconcile, "unstick_conflicts"))
        stack.enter_context(_linear(fake))
        reconcile.main(conflicts_only=True)
    lines = _spend_lines(capsys)
    assert lines == ["sweep-spend: total 0 request(s) over 0 phase(s)"], lines


def test_a_phase_reports_the_number_it_spent_not_a_constant(capsys):
    """Guard the guard: every phase in the fixture above happens to cost one
    request, so a line hard-coding `1` would pass it. A backstop that makes
    three reads says three, under its own function's name."""
    def flag_no_checks_prs():  # the name is the phase name, by the contract
        for _ in range(3):
            # A lane outside SWEPT_LANES is a real read every time — the cache
            # serves the union it read and nothing else (DRE-2929).
            reconcile.active_cards(("Duplicate",))

    fake = FakeLinear(*_busy_board())
    _run_sweep(fake, replace=(flag_no_checks_prs,))
    printed = _phase_lines(_spend_lines(capsys))
    assert printed.get("flag_no_checks_prs") == 3, (
        f"a backstop that made three requests must say three: {printed}"
    )


def test_the_nudge_loop_is_one_phase(capsys):
    """`nudge_loop` is the contract's name for the per-card walk: one line for
    the whole loop, because a line per card is a log nobody greps. Every read
    the loop makes is charged to it."""
    walked: list[str] = []

    def pr_for(ident):  # the loop's own read, one request a card
        walked.append(ident)
        reconcile.active_cards(("Duplicate",))
        return None

    active, backlog = _busy_board()
    active += [
        _card(f"DRE-{9000 + n}", state=reconcile.REVIEW_LANE,
              minutes_stale=reconcile.STALE_MINUTES[reconcile.REVIEW_LANE] + 60)
        for n in range(2)
    ]
    fake = FakeLinear(active, backlog)
    _run_sweep(fake, replace=(pr_for,))
    printed = _phase_lines(_spend_lines(capsys))
    assert len(walked) == 2, f"the loop must reach both stale cards: {walked}"
    assert printed.get("nudge_loop") == 2, (
        f"the loop spent two requests and the sweep printed {printed}"
    )


def test_the_board_read_is_its_own_phase(capsys):
    """`board_read` is one of the contract's phase names. The board snapshot is
    read once per pass and shared (DRE-2929), so the line appears when this is
    the phase that paid for it — with the watchdogs that usually read it first
    stood down, that is here. `repair_frozen_planning_holds` (DRE-4124) is the
    third of them: it runs on the same board read, so it pays for the snapshot
    whenever the two before it do not."""
    fake = FakeLinear(*_busy_board())
    _run_sweep(fake, mocks=("flag_stranded", "report_intake_depth",
                            "repair_frozen_planning_holds"))
    printed = _phase_lines(_spend_lines(capsys))
    assert printed.get("board_read") == 1, (
        f"the board read must be charged to `board_read`: {printed}"
    )


# --------------------------------------------------------------------------
# 3: the event-driven modes print the same lines for what they run
# --------------------------------------------------------------------------
def test_promote_only_prints_the_gate_and_the_total_only(capsys):
    """The gate is the whole of what `--promote-only` runs, so the gate and the
    total are the whole of what it says."""
    fake = FakeLinear(*_busy_board())
    _run_sweep(fake, promote_only=True)
    lines = _spend_lines(capsys)
    printed = _phase_lines(lines)
    assert list(printed) == ["promote_ready"], (
        f"--promote-only must print the gate and nothing else: {lines}"
    )
    total, phases = _total_line(lines)
    assert (total, phases) == (fake.requests, 1)


def test_close_epics_prints_the_phase_it_runs(capsys):
    """`--close-epics` is the epic close, scope read included — one phase, and
    the total."""
    fake = FakeLinear(*_busy_board())
    with _linear(fake):
        reconcile.main(close_only=True)
    lines = _spend_lines(capsys)
    assert list(_phase_lines(lines)) == ["close_finished_epics"], lines
    assert _total_line(lines) == (fake.requests, 1)


def test_conflicts_only_charges_the_backstop_it_runs(capsys):
    """And `--conflicts-only` is the DIRTY-PR backstop, under its own name."""
    def unstick_conflicts():
        reconcile.active_cards(("Duplicate",))

    fake = FakeLinear(*_busy_board())
    with contextlib.ExitStack() as stack:
        stack.enter_context(
            mock.patch.object(reconcile, "unstick_conflicts", unstick_conflicts)
        )
        stack.enter_context(_linear(fake))
        reconcile.main(conflicts_only=True)
    lines = _spend_lines(capsys)
    assert _phase_lines(lines) == {"unstick_conflicts": 1}, lines
    assert _total_line(lines) == (1, 1)


def test_promote_only_charges_the_gate_what_the_gate_spent(capsys):
    """Guard the guard: the one line must carry a real number, not a zero that
    happens to satisfy the shape."""
    spent: dict[str, int] = {}
    fake = FakeLinear(*_busy_board())
    _run_sweep(fake, spent=spent, promote_only=True)
    printed = _phase_lines(_spend_lines(capsys))
    assert printed["promote_ready"] == spent["promote_ready"] > 0
