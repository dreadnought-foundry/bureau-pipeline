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


@pytest.fixture(autouse=True)
def fresh_sweep_board():
    _reset_sweep_board()
    yield
    _reset_sweep_board()
