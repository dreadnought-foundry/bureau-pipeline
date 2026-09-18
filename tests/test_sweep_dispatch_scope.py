"""RED-first: a card-done dispatch reads one card, not the whole board (DRE-3645).

THE MEASUREMENT. The relay fires a `repository_dispatch` of type `reconcile`
at a repo every time one of its cards reaches Done (agent-bureau
`cloud/relay/lambda_function.py`, `_maybe_trigger_reconcile`), with
`client_payload: {reason: "card-done", identifier: "DRE-…"}`. The stub calls
the reusable, and the reusable ran a FULL board pass — 67 to 92 Linear
requests — for each one. Measured on 2026-09-15 that was about 460 of the
fleet's 2,500 requests an hour; portico alone ran six in fourteen minutes.

A card going Done can change exactly two things: whether the cards it BLOCKS
are now promotable, and whether its PARENT epic is now finished. That is the
scoped pass `linear-sync.yml` already runs on a merge — `merge_sweep_gate.decide`
reads the card once and names the passes worth running, and each runs with
`MERGED_CARD` set so `reconcile.merged_card_scope` narrows it to that card's
dependents and parent (DRE-2930, DRE-3236). The dispatch cannot simply be
dropped: a `no-code` card the operator closes by hand reaches the board only
through the relay, never through a merge.

DRE-3640 already carries the payload into the sweep's environment as
`SWEEP_REASON` / `SWEEP_CARD`. What is under test here is the sweep READING
them:

  * `SWEEP_REASON=card-done` with `SWEEP_CARD` naming a card: `run([])` does
    what linear-sync's merge step does — the gate, then one scoped pass per
    flag it returns — and NOTHING else. No backstop, no watchdog, no Intake
    age-out, no nudge loop: those are the cron's.
  * Any other reason (`epic-activated`, empty, a word the sweep does not
    know), or card-done with no card: the full pass it runs today, and the
    one line says why.
  * The same fixture board, the same assertions `test_merge_sync_*` make in
    `test_sweep_request_cuts.py`, reached through `run()`.

Run: cd bureau-pipeline && python3 -m pytest tests/test_sweep_dispatch_scope.py -v
"""
from __future__ import annotations

import contextlib
import os
import sys
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
import merge_sweep_gate  # noqa: E402
import reconcile  # noqa: E402

# The merge fixture and its fake Linear, shared rather than copied: the
# acceptance criterion is "the same assertions over the same board", and a
# second copy of the board is the one that drifts. The autouse fixtures are
# imported by name so they apply here exactly as they do there.
from test_sweep_request_cuts import (  # noqa: E402
    MERGE_SYNC_BUDGET,
    FakeLinear,
    _busy_board,
    _clean_ledgers,  # noqa: F401 — autouse fixture
    _gh_read,
    _linear,
    _pin_live_snapshot,  # noqa: F401 — autouse fixture
    _pin_repo_slug,  # noqa: F401 — autouse fixture
    _pin_valid_slugs,  # noqa: F401 — autouse fixture
    _sweep_mocks,
)

MERGED = "DRE-1998"  # Done, blocks DRE-1000, the last open child of DRE-100
DEPENDENT = "DRE-1000"
EPIC = "DRE-100"

#: The card's contract (DRE-3645), the ceiling it names: the same number the
#: merge path is held to, which ALSO pays for card-done's own read.
CARD_DONE_BUDGET = 15
assert CARD_DONE_BUDGET <= MERGE_SYNC_BUDGET

#: The full pass's work that a card-done pass must never do. The backstops are
#: whatever `_sweep_mocks` stands in; these two are the full pass's own.
FULL_PASS_ONLY = ("flag_stranded", "report_intake_depth")


@pytest.fixture(autouse=True)
def _no_scope_in_the_environment(monkeypatch):
    """Every case sets exactly the scope it tests. A value inherited from the
    shell — or leaked by an earlier case — would narrow a pass nobody asked
    to narrow."""
    for name in ("SWEEP_REASON", "SWEEP_CARD", "MERGED_CARD"):
        monkeypatch.delenv(name, raising=False)


class Sweep:
    """One `run()` over the merge fixture, with every seam that would reach
    GitHub stubbed and every Linear WRITE recorded, not counted. The Linear
    READS stay real, against `FakeLinear`, which counts them."""

    def __init__(self, fake: FakeLinear):
        self.fake = fake
        self.backstops: dict[str, mock.MagicMock] = {}
        self.state = None
        self.comment = None

    def run(self, argv=()):
        with contextlib.ExitStack() as stack:
            for m in _sweep_mocks():
                self.backstops[m.attribute] = stack.enter_context(m)
            for name in FULL_PASS_ONLY:
                self.backstops[name] = stack.enter_context(
                    mock.patch.object(reconcile, name, return_value=set())
                )
            stack.enter_context(mock.patch.object(reconcile, "MAX_WIP", 1000))
            stack.enter_context(
                mock.patch.object(reconcile, "gh_read", side_effect=_gh_read)
            )
            stack.enter_context(
                mock.patch.object(reconcile, "verdict_bound", return_value=True)
            )
            stack.enter_context(mock.patch.object(reconcile, "_nudge", return_value=False))
            stack.enter_context(_linear(self.fake))
            self.state = stack.enter_context(patch.object(linear_ops, "cmd_state"))
            self.comment = stack.enter_context(patch.object(linear_ops, "cmd_comment"))
            reconcile.run(list(argv))
        return self

    @property
    def called(self) -> list[str]:
        return sorted(name for name, m in self.backstops.items() if m.called)

    @property
    def spoken_to(self) -> list[str]:
        return [c.args[0] for c in self.comment.call_args_list]

    @property
    def moves(self) -> list[tuple]:
        return [c.args for c in self.state.call_args_list]


def _scope_lines(out: str) -> list[str]:
    return [ln for ln in out.splitlines() if ln.startswith("sweep-scope:")]


def _card_done(monkeypatch, card: str | None = MERGED) -> None:
    monkeypatch.setenv("SWEEP_REASON", "card-done")
    if card is not None:
        monkeypatch.setenv("SWEEP_CARD", card)


# --------------------------------------------------------------------------
# 1: card-done with a card is the merge path's scoped pass, reached by run()
# --------------------------------------------------------------------------
def test_the_fixture_is_a_card_done_that_justifies_both_passes():
    """Guard the guard: every number below is measured on a Done that DID
    unblock a card and DID finish its epic, so both scoped passes really run
    — a budget met by a pass that did nothing proves nothing."""
    fake = FakeLinear(_busy_board(merged=MERGED))
    with _linear(fake):
        assert merge_sweep_gate.decide(MERGED) == [
            merge_sweep_gate.PROMOTE, merge_sweep_gate.CLOSE_EPICS,
        ]


def test_a_card_done_dispatch_stays_inside_the_request_budget(monkeypatch):
    _card_done(monkeypatch)
    fake = Sweep(FakeLinear(_busy_board(merged=MERGED))).run().fake
    assert fake.requests <= CARD_DONE_BUDGET, (
        f"one card-done dispatch spent {fake.requests} Linear read requests, "
        f"budget {CARD_DONE_BUDGET} ({fake.whole_backlog_reads} whole-Backlog "
        f"page(s)) — the pass is not scoped to the card that went Done"
    )


def test_a_card_done_dispatch_never_walks_the_whole_backlog(monkeypatch):
    _card_done(monkeypatch)
    fake = Sweep(FakeLinear(_busy_board(merged=MERGED))).run().fake
    assert fake.whole_backlog_reads == 0, (
        f"{fake.whole_backlog_reads} whole-Backlog page(s) read for ONE card "
        "going Done — the promotion must be scoped to that card's dependents"
    )


def test_a_card_done_dispatch_still_acts_on_the_dependent_and_the_epic(monkeypatch):
    """Scoping must not lose the work: the unblocked dependent is evaluated
    (told why it is held, or promoted) and the finished epic closes."""
    _card_done(monkeypatch)
    sweep = Sweep(FakeLinear(_busy_board(merged=MERGED))).run()
    assert DEPENDENT in sweep.spoken_to, "the unblocked dependent was never evaluated"
    assert (EPIC, "Done") in sweep.moves, "the card's finished epic was not closed"
    # The 259 other Backlog cards are the cron's business, not this Done's.
    assert not any(
        ident.startswith("DRE-10") and ident not in (EPIC, DEPENDENT)
        for ident in sweep.spoken_to
    )


def test_a_card_done_pass_runs_no_backstop_watchdog_or_age_out(monkeypatch):
    """Those are the cron's. A card going Done changes nothing any of them
    reads, and running them on every Done is the full price the card removes."""
    _card_done(monkeypatch)
    sweep = Sweep(FakeLinear(_busy_board(merged=MERGED))).run()
    assert sweep.called == [], (
        f"a card-done pass ran {sweep.called} — the full pass's work, bought "
        "again for one card"
    )


def test_a_card_done_pass_prints_its_one_scope_line(monkeypatch, capsys):
    _card_done(monkeypatch)
    Sweep(FakeLinear(_busy_board(merged=MERGED))).run()
    assert _scope_lines(capsys.readouterr().out) == [
        f"sweep-scope: card-done {MERGED} — scoped to its dependents and parent "
        "(2 pass(es))"
    ]


def test_the_card_is_read_case_insensitively_and_stripped(monkeypatch, capsys):
    """The payload is the relay's `identifier`; a stray space or a lowercase
    key must not turn a scoped pass back into the full price."""
    _card_done(monkeypatch, card="  dre-1998 \n")
    sweep = Sweep(FakeLinear(_busy_board(merged=MERGED))).run()
    assert sweep.called == []
    assert (EPIC, "Done") in sweep.moves
    assert f"sweep-scope: card-done {MERGED} " in capsys.readouterr().out


def test_the_scoped_pass_does_not_leave_its_card_in_the_environment(monkeypatch):
    """`MERGED_CARD` is how the scope reaches `merged_card_scope`. Left behind,
    it would scope the next pass in the same process to a card nobody named."""
    _card_done(monkeypatch)
    Sweep(FakeLinear(_busy_board(merged=MERGED))).run()
    assert "MERGED_CARD" not in os.environ


def test_the_gate_decides_which_passes_run(monkeypatch, capsys):
    """The SAME gate the merge path runs, not a second reading of it: a Done
    that unblocked nothing and finished nothing runs no pass at all — and says
    so, rather than going quiet."""
    _card_done(monkeypatch, card="DRE-1999")  # Done, blocks nothing, epic still open
    fake = FakeLinear(_busy_board(merged=MERGED))
    fake.cards[MERGED]["state"] = {"name": "In Progress"}  # its sibling is still open
    sweep = Sweep(fake).run()
    assert sweep.called == []
    assert fake.whole_backlog_reads == 0 and fake.board_reads == 0
    assert (EPIC, "Done") not in sweep.moves
    assert _scope_lines(capsys.readouterr().out) == [
        "sweep-scope: card-done DRE-1999 — scoped to its dependents and parent "
        "(0 pass(es))"
    ]


def test_an_unreadable_card_falls_open_exactly_as_the_gate_does(monkeypatch):
    """"We could not look" is not "nothing to do" — the gate's own rule. The
    gate returns both passes, each pass's own scope read fails too and it
    runs unscoped, as it did before DRE-3236. Still never the backstops: the
    cron owns those whatever the gate could read."""
    _card_done(monkeypatch)
    fake = FakeLinear(_busy_board(merged=MERGED))
    real = fake.gql

    def flaky(query, variables=None):
        if "relations(first: 50)" in query and (variables or {}).get("id") == MERGED:
            fake.queries.append((query, variables or {}))
            raise linear_ops.LinearError("card read exploded")
        return real(query, variables)

    fake.gql = flaky
    sweep = Sweep(fake).run()
    assert fake.whole_backlog_reads >= 1, "the unreadable card did not fall open"
    assert sweep.called == []


# --------------------------------------------------------------------------
# 2: everything else is the full pass it runs today, and says why
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "env,reason",
    [
        ({"SWEEP_REASON": "card-done"}, "card-done"),
        ({"SWEEP_REASON": "card-done", "SWEEP_CARD": "   "}, "card-done"),
        ({"SWEEP_REASON": "epic-activated", "SWEEP_CARD": MERGED}, "epic-activated"),
        ({"SWEEP_REASON": "something-new", "SWEEP_CARD": MERGED}, "something-new"),
        ({}, "schedule"),
        ({"SWEEP_REASON": "", "SWEEP_CARD": ""}, "schedule"),
    ],
    ids=["card-done-no-card", "card-done-blank-card", "epic-activated",
         "unrecognised", "unset", "empty"],
)
def test_anything_but_card_done_with_a_card_is_the_full_pass(
    monkeypatch, capsys, env, reason
):
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    sweep = Sweep(FakeLinear(_busy_board(merged=MERGED))).run()
    assert set(sweep.called) >= {"report_intake_depth", "flag_stranded",
                                 "drain_retiring_lanes", "unstick_conflicts"}, (
        f"only {sweep.called} ran — a full pass must run the full pass's work"
    )
    lines = _scope_lines(capsys.readouterr().out)
    assert len(lines) == 1, lines
    assert lines[0].startswith(f"sweep-scope: full pass ({reason}"), lines[0]


def test_an_event_mode_flag_is_never_widened_or_narrowed_by_the_scope(monkeypatch):
    """The scope narrows a FULL pass. A caller that already asked for one mode
    — linear-sync's `--conflicts-only`, the merge path's `--promote-only` —
    gets that mode, whatever the environment says."""
    _card_done(monkeypatch)
    with mock.patch.object(merge_sweep_gate, "decide") as decide:
        sweep = Sweep(FakeLinear(_busy_board(merged=MERGED))).run(["--conflicts-only"])
    assert not decide.called
    assert sweep.called == ["unstick_conflicts"]


# --------------------------------------------------------------------------
# 3: the decision itself
# --------------------------------------------------------------------------
def test_sweep_scope_returns_the_card_and_the_gates_passes(monkeypatch):
    _card_done(monkeypatch, card=" dre-1998 ")
    with mock.patch.object(merge_sweep_gate, "decide",
                           return_value=[merge_sweep_gate.PROMOTE]) as decide:
        scope = reconcile.sweep_scope()
    decide.assert_called_once_with(MERGED)
    assert scope.reason == "card-done"
    assert scope.card == MERGED
    assert list(scope.passes) == [merge_sweep_gate.PROMOTE]


@pytest.mark.parametrize("reason", ["", "epic-activated", "Card-Done-ish"])
def test_sweep_scope_for_a_full_pass_names_no_card_and_reads_nothing(monkeypatch, reason):
    monkeypatch.setenv("SWEEP_REASON", reason)
    monkeypatch.setenv("SWEEP_CARD", MERGED)
    with mock.patch.object(merge_sweep_gate, "decide") as decide:
        scope = reconcile.sweep_scope()
    assert not decide.called, "a full pass has no card to read"
    assert scope.card is None
    assert scope.reason == (reason or "schedule")


def test_both_variables_stay_optional():
    """`REQUIRED_ENV` is what `check_reconcile_env` holds every call site to.
    Adding either would make every linear-sync and plan call site red for a
    variable only the dispatched sweep ever sets."""
    assert "SWEEP_REASON" not in reconcile.REQUIRED_ENV
    assert "SWEEP_CARD" not in reconcile.REQUIRED_ENV
