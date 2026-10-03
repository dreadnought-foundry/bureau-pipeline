"""Test isolation for the sweep's per-sweep board snapshot (DRE-2929).

`reconcile` reads the whole swept board once per sweep and serves all four
callers from that one read. In production the snapshot's lifetime is a sweep —
`main()` drops it on entry and the process ends when the sweep does. In a test
run there is one process and hundreds of sweeps, so a snapshot left behind by
one test is a board another test never asked for and cannot see it got.

Reset before AND after each test, and only if `reconcile` was actually
imported: this file must not pull the module (and its environment
requirements) into a test session that has no use for it.
"""
from __future__ import annotations

import sys

import pytest


def _reset_sweep_board() -> None:
    reconcile = sys.modules.get("reconcile")
    reset = getattr(reconcile, "reset_sweep_cards", None)
    if reset is not None:
        reset()


def _reset_linear_budget() -> None:
    """The Linear seam's budget ledger and its rate-limit stop are PROCESS
    state (DRE-3202): in production one process is one run. In a test session
    one process is hundreds of runs, and several suites drive a RATELIMITED
    response through `linear_ops.gql` on purpose — without this reset the
    first of them would arm the stop and every later `gql` call in the
    session would be refused without touching the transport."""
    linear_ops = sys.modules.get("linear_ops")
    reset = getattr(linear_ops, "_reset_budget_state", None)
    if reset is not None:
        reset()


def _reset_read_door() -> None:
    """The read door's client keeps PROCESS state (Stage 2 BP-2): the mode it
    read once, its counters, whether the door stopped answering, its token. In
    production one process is one run; in a test session one test's `shadow`
    or dead door must not become the next test's."""
    bureau_read = sys.modules.get("bureau_read")
    reset = getattr(bureau_read, "reset_for_tests", None)
    if reset is not None:
        reset()


def _lift_drain_slots(monkeypatch) -> None:
    """The groom drain reads the planner slot ledger before it moves a card
    (DRE-5326), and releases no more cards than there are free slots. Every
    drain fixture written before that moves fifteen cards through a fake that
    carries no ledger, and none of them is about slots — so for them the read
    answers "no limit". `test_groomer_drain_slots.py` is where the slots are
    tested, and each test there puts back the slots it means."""
    groomer = sys.modules.get("groomer")
    if getattr(groomer, "free_planner_slots", None) is not None:
        monkeypatch.setattr(groomer, "free_planner_slots", lambda lops: None)


def _no_ambient_event(monkeypatch) -> None:
    """The read door's client refuses to ask from a `pull_request` run (the
    door refuses those tokens by design, S7) — and the CI that runs this suite
    IS a `pull_request` run. A test that is about the event sets it itself; no
    test inherits the runner's."""
    monkeypatch.delenv("GITHUB_EVENT_NAME", raising=False)


@pytest.fixture(autouse=True)
def fresh_sweep_board(monkeypatch):
    _no_ambient_event(monkeypatch)
    _lift_drain_slots(monkeypatch)
    _reset_sweep_board()
    _reset_linear_budget()
    _reset_read_door()
    yield
    _reset_sweep_board()
    _reset_linear_budget()
    _reset_read_door()
