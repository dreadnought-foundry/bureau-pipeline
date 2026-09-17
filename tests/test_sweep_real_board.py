"""RED-first: the request ceiling is measured on the real board — every phase
of one sweep, replayed over a committed board (DRE-3641).

THE DEFECT THIS REPLACES. `tests/test_sweep_request_cuts.py` says a busy sweep
costs at most 30 Linear reads, and it is not wrong about its own fixture: that
board is hand-built, carries no wave-committed cards and no aged Intake cards,
and 18 of the sweep's phases are mocked out of the pass entirely. A live pass
costs 65 requests here and 92 on agent-bureau. A ceiling that passes on a board
nobody sweeps is the defect; this one runs EVERY phase, live, over a committed
board, with only GitHub stubbed.

WHICH BOARD. The card named the snapshot DRE-3638 committed. DRE-3918 deleted
it — it was the real Linear board, every card's text, in a PUBLIC repository —
and left `board_snapshot.synthetic()` in its place, in the snapshot's exact
contract, naming this replay as its reader. So the board replayed here is
`synthetic()`: the same shapes a real board has (epics with a dozen children,
relations both ways, exhausted comment windows, null descriptions, a card in
every lane) and no real words. `tests/test_no_real_board_in_repo.py` is the
guard that keeps it that way.

WHAT IS UNDER TEST:
  * One full `reconcile.main()` over that board — no `reconcile` function
    mocked, every backstop and every phase live — spends at most
    `REAL_BOARD_SWEEP_BUDGET` Linear READ requests. Only the GitHub seams
    (`gh_read`, `gh`, `gh_dispatch`, `gh_actions_read`, `_nudge`) and the
    Linear writes are stubbed, the way the existing suite stubs them.
  * The failure prints a per-phase table — reads attributed to the `main()`
    function that spent them, off the sweep's own `sweep-spend:` lines
    (DRE-3639), the way the read-only measurement on 2026-09-12 attributed
    them — so the next reader sees where the spend went rather than a number.
  * The sweep's DECISIONS over that board — which cards it promotes, holds,
    escalates and closes — equal the list recorded in
    `tests/fixtures/board-snapshot-synthetic.decisions.json`. The ceiling
    alone can be met by a sweep that does less work; this is what makes such a
    cut fail by name.
  * The replay runs in under `SECONDS_CEILING` seconds and touches no network:
    `urllib.request.urlopen` is never called.

THE CEILING IS HONEST, NOT ASPIRATIONAL. `REAL_BOARD_SWEEP_BUDGET` is what
this replay measured on the day it landed. Each cut sibling lowers it to what
IT measures, ending at 30. A ceiling that fails on the real board before the
cuts exist is just a red build.

Run: cd bureau-pipeline && python3 -m pytest tests/test_sweep_real_board.py -v
Re-measure and re-record (what a cut sibling runs once its cut is green):
     cd bureau-pipeline && python3 tests/test_sweep_real_board.py --record
"""
from __future__ import annotations

import contextlib
import functools
import io
import json
import os
import sys
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import board_snapshot  # noqa: E402
import linear_ops  # noqa: E402
import reconcile  # noqa: E402
import validate_card  # noqa: E402

# The fake Linear is IMPORTED, never copied: it is the one statement of how
# Linear answers this pipeline's reads, and a second copy would drift the day a
# query changes shape. This subclass adds only the shapes the cuts suite's
# hand-built board never provoked.
from test_sweep_request_cuts import FakeLinear  # noqa: E402

#: What ONE full sweep may spend on Linear READS over the committed board, with
#: no phase mocked. MEASURED BY THIS REPLAY ON 2026-09-17: 52 requests — the
#: number the pass really spent that day, not the 30 the hand-built ceiling
#: says and not a round number anybody hoped for. (A live pass on this repo's
#: own board cost 65 on 2026-09-12 and 92 on agent-bureau; the replay lands on
#: the same order because the board it replays is sized to that one.)
#:
#: It was 66 until DRE-3642 batched the epic reads: the epic close cost nine
#: children reads and the epic gate six relations reads over this board, and
#: the two now share ONE paged read of the epics they name. The card projected
#: 50 off the LIVE board's nine-and-nine; the replay's board gives the gate six
#: epics with Backlog children rather than nine, so 66 − 9 − 6 + 1 = 52 is what
#: this pass really spends. The ceiling is what was measured, never what was
#: projected — a number nobody measured is the defect this file replaced.
#:
#: It is the ONLY place the real-board ceiling lives. Each cut sibling lowers
#: it to what IT measures, ending at 30.
REAL_BOARD_SWEEP_BUDGET = 52

#: The replay is a CI test, not a benchmark: the card's 30 seconds, asserted so
#: a sweep that starts walking the board per card fails here rather than slowing
#: every run by a minute.
SECONDS_CEILING = 30.0

#: The decisions this sweep makes over that board, recorded beside the test.
DECISIONS_PATH = ROOT / "tests" / "fixtures" / "board-snapshot-synthetic.decisions.json"

#: The repo the replay sweeps as. The board's cards carry `repo:bureau-pipeline`.
SLUG = "bureau-pipeline"

#: How big the replayed board is: the card count DRE-3638's snapshot recorded
#: for the real 2026-09-12 board (406 cards in the lanes the sweep reads). The
#: ceiling is a number about SCALE — the sweep's spend rises with the board —
#: so the stand-in board is sized to the one it stands in for, rather than to
#: `board_snapshot.SYNTHETIC_CARDS`, which is the size that suite's own fixture
#: tests happen to want.
REAL_BOARD_CARDS = 406


# --------------------------------------------------------------------------
# The board, and the Linear that serves it
# --------------------------------------------------------------------------
class ReplayLinear(FakeLinear):
    """`FakeLinear` over a board SNAPSHOT, answering the query shapes a full
    sweep makes that a hand-built board never provokes.

    Every one of them is a read the cuts suite's fixture never reached because
    18 phases were mocked out of its pass: the epic's history read, the
    label-filtered break-glass count, the Backlog read scoped by identifier,
    and the pass's batched epic record. The base class answers the rest,
    and counts every request — including the ones handled here, which are
    appended to the same ledger exactly once.
    """

    def __init__(self, snapshot: dict):
        super().__init__(snapshot["cards"])
        self.snapshot = snapshot

    @property
    def reads(self) -> int:
        """Requests that read. A mutation reaching here at all is a write the
        replay failed to stub, and `test_every_linear_write_is_stubbed` says so
        by name rather than letting it ride in the ceiling."""
        return sum(
            1 for q, _ in self.queries if "mutation" not in " ".join(q.split())
        )

    def gql(self, query, variables=None):
        q = " ".join((query or "").split())
        v = variables or {}
        if "issues(" in q and "labels" in q and "$labels" in q:
            # the break-glass count: issues filtered by label, not by lane
            self.queries.append((query, v))
            return {"issues": {"nodes": [], "pageInfo": {"hasNextPage": False}}}
        return super().gql(query, variables)


# --------------------------------------------------------------------------
# The decisions: what the sweep would DO to the board, off its write seams
# --------------------------------------------------------------------------
#: The two lanes an escalation reaches, named by the sweep rather than here:
#: the CEO's queue (the Intake age-out) and the broken-card lane (the
#: prose-blocker defect route).
_ESCALATION_LANES = (
    reconcile.ESCALATED_STATE, reconcile.prose_blockers.DEFECT_LANE,
)


def _decisions(advance, state, label, refusals) -> dict[str, list[str]]:
    """The four decisions the sweep made, read off the stubbed writes.

      promote  — a Backlog card advanced to Todo by the dependency gate.
      hold     — a card told why it is NOT being promoted (the refusal's own
                 tag, so a cut that changes the reason fails too), or parked
                 behind the hold label.
      escalate — a card moved into the CEO's queue or into the broken-card
                 lane: the Intake age-out, the prose-blocker defect route, and
                 Planning's stall escalation.
      close    — a card or epic moved to Done.

    BOTH move seams are read, and that is not tidiness (DRE-4124). The Intake
    age-out advances (`cmd_advance`); `planning_escalation.escalate` writes the
    lane directly (`cmd_state`), because it parks a card rather than walking it
    along the rail. Reading only the first is what made 68 stalled Planning
    cards — held under `needs-human` until this card, escalated after it —
    drop out of the record entirely rather than move buckets. A decision this
    file cannot see is a decision a cut can delete for free, which is the one
    thing the record exists to stop.
    """
    promote, hold, escalate, close = [], [], [], []
    for call in advance.call_args_list:
        ident, to = call.args[0], call.args[1]
        if to == "Todo":
            promote.append(ident)
        elif to in _ESCALATION_LANES:
            escalate.append(f"{ident} → {to}")
    for call in state.call_args_list:
        ident, to = call.args[0], call.args[1]
        if to == "Done":
            close.append(ident)
        elif to in _ESCALATION_LANES:
            escalate.append(f"{ident} → {to}")
        elif to == "Backlog" and "--park" in call.args[2:]:
            hold.append(f"{ident} parked")
    for call in label.call_args_list:
        if call.args[1:2] == (reconcile.HOLD_LABEL,):
            hold.append(f"{call.args[0]} {reconcile.HOLD_LABEL}")
    hold.extend(f"{ident} {tag}" for ident, tag in refusals)
    return {
        "promote": sorted(set(promote)),
        "hold": sorted(set(hold)),
        "escalate": sorted(set(escalate)),
        "close": sorted(set(close)),
    }


class Replay:
    """One replayed sweep: what it spent, where, what it decided, how long it
    took, and whether it reached the network."""

    def __init__(
        self, fake, decisions, spend_lines, seconds, urlopen, stubbed, printed,
        spy_delegates,
    ):
        self.fake = fake
        self.decisions = decisions
        self.spend_lines = spend_lines
        self.seconds = seconds
        self.urlopen = urlopen
        #: `(reconcile, linear_ops)` — the names that were mocks during the pass.
        self.stubbed = stubbed
        #: Everything the pass printed, so a test can check it reached its end.
        self.printed = printed
        #: Whether the refusal recorder wrapped the real `_surface_once`.
        self.spy_delegates = spy_delegates

    @property
    def requests(self) -> int:
        """READS. Writes are stubbed and not counted, the convention
        `test_sweep_request_budget` set and `test_sweep_request_cuts` kept: a
        write is work the pass did — a promotion, an epic close, a receipt —
        and it scales with what happened, never with the board."""
        return self.fake.reads

    @property
    def mutations(self) -> list[str]:
        return [q for q, _ in self.fake.queries if "mutation" in " ".join(q.split())]

    @property
    def phases(self) -> dict[str, int]:
        """The per-phase attribution, off the sweep's own `sweep-spend:` lines
        — the same phase names the live run log carries."""
        out: dict[str, int] = {}
        for line in self.spend_lines:
            parts = line.split()
            if parts[1] == "total":
                continue
            out[parts[1]] = int(parts[2])
        return out

    def table(self) -> str:
        """The per-phase table, printed on failure: every phase that spent a
        request, dearest first, and what the lines do not account for."""
        rows = sorted(self.phases.items(), key=lambda kv: (-kv[1], kv[0]))
        width = max([len(name) for name, _ in rows] or [1])
        lines = [f"  {name.ljust(width)}  {n:>4}" for name, n in rows]
        attributed = sum(self.phases.values())
        lines.append(f"  {'(unattributed)'.ljust(width)}  {self.requests - attributed:>4}")
        lines.append(f"  {'TOTAL'.ljust(width)}  {self.requests:>4}")
        return "\n".join(["  phase" + " " * (width - 5) + "  reads"] + lines)


def _changed_names(module, before: dict) -> tuple[str, ...]:
    """Every name in `module` that is not the object it was in `before`.

    Identity, not `isinstance(..., Mock)`: a stub does not have to be a mock to
    replace a phase, and the card's claim is about what this replay CHANGED
    about the sweep, not about which library it changed it with.
    """
    return tuple(sorted(
        name for name, value in vars(module).items() if before.get(name) is not value
    ))


def _gh_read(*args):
    """Every GitHub listing the sweep makes: no pull requests, no branches.

    The board is what is under measurement; a PR fixture would add GitHub
    behaviour the ceiling is not about.
    """
    return "[]"


@contextlib.contextmanager
def _github_stubbed():
    """The five GitHub seams, and nothing else. Every `reconcile` function
    stays live — that is the whole point of this replay."""
    with patch.object(reconcile, "gh_read", side_effect=_gh_read), \
            patch.object(reconcile, "gh", return_value=""), \
            patch.object(reconcile, "gh_dispatch"), \
            patch.object(reconcile, "gh_actions_read", return_value=None), \
            patch.object(reconcile, "_nudge", return_value=False):
        yield


#: The sweep's own process-level ledgers. Cleared around the replay: in
#: production one process is one pass, and a ledger this pass filled would
#: otherwise be read by whatever suite runs next in the same session.
_LEDGERS = (
    reconcile._write_failures, reconcile._read_failures,
    reconcile._stale_defects, reconcile._card_skips,
)


def _run_replay() -> Replay:
    """One full sweep over the committed board, measured."""
    for ledger in _LEDGERS:
        ledger.clear()
    fake = ReplayLinear(board_snapshot.synthetic(REAL_BOARD_CARDS))
    refusals: list[tuple[str, str]] = []
    real_surface = reconcile._surface_once

    @functools.wraps(real_surface)
    def surface_once(identifier, tag, notice):
        refusals.append((identifier, str(tag)))
        return real_surface(identifier, tag, notice)

    before_reconcile = dict(vars(reconcile))
    before_linear = dict(vars(linear_ops))
    out = io.StringIO()
    with contextlib.ExitStack() as stack:
        enter = stack.enter_context
        enter(_github_stubbed())
        enter(patch.object(reconcile, "REPO_SLUG", SLUG))
        # The WIP cap is a per-repo INPUT, not a phase: this repo's stub runs
        # at the default 8, and a board with more than eight active cards
        # saturates it, so the dependency gate returns before it reads the
        # Backlog at all. Measuring that would be measuring a gate that never
        # ran — the live 2026-09-12 pass spent 18 requests in it. Raised the
        # way `test_sweep_request_cuts` raises it, so the gate is exercised.
        enter(patch.object(reconcile, "MAX_WIP", 1000))
        enter(patch.object(reconcile, "_surface_once", surface_once))
        enter(patch.object(
            validate_card, "VALID_SLUGS", {"agent-bureau", "atlas", SLUG}))
        enter(patch.object(linear_ops, "gql", side_effect=fake.gql))
        # The sweep attributes a phase's spend to the difference between two
        # readings of its own ledger, and that ledger counts what the REAL seam
        # sent. With the seam replaced by the fake, the fake's count is the
        # pass's count — the same substitution `test_sweep_spend_lines` makes.
        enter(patch.object(linear_ops, "requests_made", lambda: fake.requests))
        enter(patch.object(linear_ops, "cmd_comment"))
        advance = enter(patch.object(linear_ops, "cmd_advance"))
        state = enter(patch.object(linear_ops, "cmd_state"))
        label = enter(patch.object(linear_ops, "add_label"))
        enter(patch.object(linear_ops, "remove_label"))
        # The growth record's write, stubbed like the rest (DRE-3643 writes it
        # only when the record MOVED). On this board no epic carries the region
        # yet, so every epic would take one — a `get_issue` and an
        # `issueUpdate` apiece for a record a live epic already has.
        enter(patch.object(linear_ops, "set_description"))
        urlopen = enter(patch.object(urllib.request, "urlopen"))
        # WHAT THIS REPLAY CHANGED, read off the modules themselves while the
        # pass is live — never a list this file keeps. "No phase is mocked" is
        # the card's central claim, and a claim checked against its own
        # declaration is not checked at all: a stub added later would be added
        # to the list too.
        stubbed = (
            _changed_names(reconcile, before_reconcile),
            _changed_names(linear_ops, before_linear),
        )
        # …and that the one non-mock replacement really is a spy: it wraps the
        # function it stands in for, so the refusals are recorded BY watching
        # the real one rather than instead of it. Checked here, while the patch
        # is live — outside the stack the name is the real function again and
        # the question cannot be asked.
        spy_delegates = (
            reconcile._surface_once.__wrapped__ is before_reconcile["_surface_once"]
        )
        reconcile.reset_sweep_cards()
        started = time.monotonic()
        with contextlib.redirect_stdout(out), contextlib.suppress(SystemExit):
            reconcile.main()
        seconds = time.monotonic() - started
    for ledger in _LEDGERS:
        ledger.clear()
    printed = out.getvalue().splitlines()
    lines = [l for l in printed if l.startswith("sweep-spend:")]
    return Replay(
        fake, _decisions(advance, state, label, refusals), lines, seconds,
        urlopen, stubbed, printed, spy_delegates,
    )


@pytest.fixture(scope="session")
def replay() -> Replay:
    """The replay runs ONCE for this module: it is a full sweep over a
    406-card board, and every test below reads that same pass."""
    return _run_replay()


def _recorded() -> dict:
    return json.loads(DECISIONS_PATH.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# 1: the ceiling
# --------------------------------------------------------------------------
def test_a_full_sweep_over_the_real_board_stays_inside_the_budget(replay):
    """The card's ceiling, measured on the board the sweep really sweeps."""
    assert replay.requests <= REAL_BOARD_SWEEP_BUDGET, (
        f"one full sweep over the committed board spent {replay.requests} "
        f"Linear read request(s), budget {REAL_BOARD_SWEEP_BUDGET}. Where it "
        f"went:\n{replay.table()}"
    )


def test_the_failure_names_the_phase_that_spent_it(replay):
    """The table is the point: a total nobody can place is a number, not a
    finding. Every phase in it is a `reconcile` function `main()` called."""
    assert replay.phases, (
        f"the replay attributed nothing to any phase: {replay.spend_lines}"
    )
    for name in replay.phases:
        assert callable(getattr(reconcile, name, None)) or name in (
            "board_read", "nudge_loop"
        ), f"phase {name!r} is not a function main() calls"
    assert sum(replay.phases.values()) <= replay.requests
    table = replay.table()
    assert "TOTAL" in table and str(replay.requests) in table


# --------------------------------------------------------------------------
# 2: no phase is mocked
# --------------------------------------------------------------------------
def test_every_linear_write_is_stubbed(replay):
    """The ceiling counts READS. A write that reached the fake is one the
    replay failed to stub — it would ride in the total and, on the one board
    this repo has, would also be a write nobody asked for."""
    assert not replay.mutations, (
        f"{len(replay.mutations)} Linear write(s) reached the board: "
        f"{sorted({' '.join(q.split())[:80] for q in replay.mutations})}"
    )


def test_no_reconcile_function_is_stubbed(replay):
    """THE CARD'S CENTRAL CLAIM, checked against the modules rather than
    against a list this file keeps: while the pass ran, the only things about
    `reconcile` that were not the real thing were the five GitHub seams, two
    config VALUES, and one call-through spy. Every backstop, watchdog, gate and
    report is the real function — which is what `test_sweep_request_cuts`
    cannot say, with 18 of them mocked out of its pass."""
    in_reconcile, in_linear = replay.stubbed
    assert set(in_reconcile) == {
        # GitHub, and only GitHub
        "gh", "gh_actions_read", "gh_dispatch", "gh_read", "_nudge",
        # values the run takes from its environment, not phases
        "MAX_WIP", "REPO_SLUG",
        # the refusal recorder, which calls the real one (asserted below)
        "_surface_once",
    }, f"the replay changed something else about the sweep: {in_reconcile}"
    assert replay.spy_delegates, (
        "the refusal recorder replaced `_surface_once` instead of wrapping it "
        "— then the holds below are this test's behaviour, not the sweep's"
    )
    assert set(in_linear) == {
        # the read seam and the counter that reads its ledger
        "gql", "requests_made",
        # the writes, stubbed the way the existing suite stubs them
        "cmd_comment", "cmd_advance", "cmd_state",
        "add_label", "remove_label", "set_description",
    }, f"a Linear READER other than the seam was stubbed: {in_linear}"


def test_the_pass_ran_to_its_end(replay):
    """A sweep that died in phase three would spend little and decide little,
    and both numbers below would read as a win. The pass prints one line when
    it has finished its own work, and the total is the last thing it prints."""
    assert any(l.startswith("sweep complete:") for l in replay.printed), (
        "the pass never reached its end: " + "\n".join(replay.printed[-8:])
    )
    assert replay.spend_lines[-1].startswith("sweep-spend: total ")


def test_the_epic_reads_do_not_follow_the_number_of_epics(replay):
    """DRE-3642's cut, measured where the ceiling is: however many epics are
    active, the epic close and the epic gate's relations are ONE read between
    them — the batched record — and not one per epic each.

    The guard on the guard is the identifier count: a pass that batched one
    epic would satisfy "one read" and prove nothing, so the read has to be
    asked for the whole active set.
    """
    batched = [
        v for q, v in replay.fake.queries
        if "$numbers" in q and "inverseRelations" in q
    ]
    assert len(batched) == 1, f"{len(batched)} batched epic read(s) in one pass"
    assert len(batched[0]["numbers"]) >= 9, (
        f"the record was read for {len(batched[0]['numbers'])} epic(s) — the "
        "2026-09-12 board had nine active, and a one-epic batch proves nothing"
    )
    assert replay.phases.get("close_finished_epics", 0) <= 1, replay.table()
    per_epic = [
        q for q, _ in replay.fake.queries
        if "issue(id: $id)" in q and "inverseRelations" in q
    ]
    assert not per_epic, (
        f"{len(per_epic)} per-epic relations read(s) survived the cut"
    )


def test_the_expensive_phases_really_ran(replay):
    """Guard the guard: a ceiling met by a pass that skipped the expensive
    phases measures nothing. The phases the 2026-09-12 measurement found the
    spend in are in this pass's own attribution, and the Intake gate — which
    spends nothing here, because the board read already carried the comments it
    needs — is proved by the cards it moved instead."""
    for name in ("promote_ready", "report_epic_growth", "close_finished_epics",
                 "nudge_loop"):
        assert name in replay.phases, (
            f"{name} spent nothing — it did not run: {replay.spend_lines}"
        )
    assert any(
        d.endswith(f"→ {reconcile.ESCALATED_STATE}") for d in replay.decisions["escalate"]
    ), f"the Intake age-out moved nothing: {replay.decisions['escalate']}"


# --------------------------------------------------------------------------
# 3: the decisions
# --------------------------------------------------------------------------
def _decision_diff(made: dict, recorded: dict) -> str:
    """What moved, by name and by key — never the two 233-line lists side by
    side, which is a wall nobody reads."""
    out = []
    for key in sorted(set(made) | set(recorded)):
        mine, theirs = set(made.get(key, ())), set(recorded.get(key, ()))
        for label, gap in (("no longer", theirs - mine), ("newly", mine - theirs)):
            if gap:
                shown = sorted(gap)
                out.append(
                    f"  {key}: {len(gap)} {label} decided — "
                    + ", ".join(shown[:8]) + (", …" if len(shown) > 8 else "")
                )
    return "\n".join(out)


def test_the_sweeps_decisions_match_the_recorded_ones(replay):
    """The ceiling alone can be met by a sweep that does less work. This is
    what makes such a cut fail by name — and what makes a cut that changes no
    decision pass, which is the whole point of the cut siblings."""
    recorded = _recorded()["decisions"]
    # A boolean, so the failure is the diff below and not pytest's own
    # side-by-side repr of two 233-line lists.
    unchanged = replay.decisions == recorded
    assert unchanged, (
        "the sweep's decisions over the committed board changed:\n"
        + _decision_diff(replay.decisions, recorded)
        + f"\n(re-record with the replay if the change is intended: {DECISIONS_PATH.name})"
    )


def test_the_recorded_decisions_say_something(replay):
    """Guard the guard: four empty lists would pass the comparison above
    against a sweep that did nothing at all. The recorded holds are the weight
    — one line per card the gate refused, naming the card AND the reason — so a
    cut that meets the ceiling by not evaluating the Backlog fails by name.

    `promote` and `close` are recorded EMPTY on this board and that is a pinned
    decision too: no epic here carries a second-critic round and no one-off
    carries a verdict the fleet wrote, so nothing is promotable, and no lane
    the board holds is Done, so no epic is finished. A cut that started
    promoting cards under those conditions fails here as loudly.
    """
    recorded = _recorded()["decisions"]
    assert set(recorded) == {"promote", "hold", "escalate", "close"}
    assert len(recorded["hold"]) > 100, recorded["hold"][:5]
    assert recorded["escalate"], "the recorded escalations are empty"
    for entry in recorded["hold"]:
        ident, _, reason = entry.partition(" ")
        assert ident.startswith("DRE-") and reason, entry


# --------------------------------------------------------------------------
# 4: it is a test, not an expedition
# --------------------------------------------------------------------------
def test_the_replay_runs_in_under_thirty_seconds(replay):
    assert replay.seconds < SECONDS_CEILING, (
        f"the replay took {replay.seconds:.1f}s, ceiling {SECONDS_CEILING}s"
    )


def test_the_replay_never_touches_the_network(replay):
    assert not replay.urlopen.called, (
        f"the replay reached the network {replay.urlopen.call_count} time(s) — "
        "every Linear request must be served by the fake"
    )


# --------------------------------------------------------------------------
# Re-recording, for the cut siblings
# --------------------------------------------------------------------------
def _record() -> int:
    """Measure the replay and write both halves of its record: the decisions
    file, and the number a cut sibling puts in `REAL_BOARD_SWEEP_BUDGET`.

    Here rather than in a script of its own, because the thing that measures
    and the thing that records must be the same pass: a recorder that built the
    board its own way would record a board the ceiling was never measured over.
    """
    replay = _run_replay()
    DECISIONS_PATH.write_text(
        json.dumps(
            {
                "record": "what one full reconcile sweep DECIDES over the "
                          "replayed board: which cards it promotes, holds, "
                          "escalates and closes",
                "produced_by": "tests/test_sweep_real_board.py --record",
                "board": f"board_snapshot.synthetic({REAL_BOARD_CARDS}) — the "
                         "board DRE-3918 left in place of the real 2026-09-12 "
                         "snapshot, sized to the card count that snapshot "
                         "recorded",
                "recorded_on": datetime.now(UTC).date().isoformat(),
                "no_request_count_here": "the ceiling lives once, in "
                                         "REAL_BOARD_SWEEP_BUDGET; a second "
                                         "number in this file could disagree "
                                         "with it",
                "decisions": replay.decisions,
            },
            indent=1,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"recorded {DECISIONS_PATH.relative_to(ROOT)}: "
          + ", ".join(f"{k} {len(v)}" for k, v in replay.decisions.items()))
    print(f"REAL_BOARD_SWEEP_BUDGET is {REAL_BOARD_SWEEP_BUDGET}; this pass "
          f"spent {replay.requests}:")
    print(replay.table())
    return 0


if __name__ == "__main__":
    if sys.argv[1:] != ["--record"]:
        sys.exit("usage: python3 tests/test_sweep_real_board.py --record")
    sys.exit(_record())
