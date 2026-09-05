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


@pytest.fixture(autouse=True)
def fresh_sweep_board():
    _reset_sweep_board()
    _reset_linear_budget()
    yield
    _reset_sweep_board()
    _reset_linear_budget()
