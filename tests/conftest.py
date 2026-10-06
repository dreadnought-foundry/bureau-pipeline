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


def _reset_workflow_states() -> None:
    """The Linear seam reads a team's workflow states once per PROCESS (Stage 2
    BP-3): in production one process is one run; in a test session one test's
    fake lanes and ids must never be the next test's."""
    linear_ops = sys.modules.get("linear_ops")
    reset = getattr(linear_ops, "reset_workflow_states", None)
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


def _empty_epic_queue(monkeypatch) -> None:
    """The sweep's two epic-queue phases read Linear directly, not through the
    pass's board snapshot (DRE-5152): `epic_cap.labeled_elsewhere` in every
    full sweep and `epic_cap.waiting_line` in the owner's. Some 47 suites run
    a whole `reconcile.main()` without faking `linear_ops.gql`, and under CI's
    key a direct read is refused — a read failure, which turns the pass red.
    None of them is about the epic line, so for them nobody is labeled and
    nobody is waiting, at no request. `fleet_state` needs no answer: it is read
    only after a line that is not empty. `test_epic_cap.py`,
    `test_epic_cap_sweep.py`, `test_off_rail_writers.py` and
    `test_sweep_real_board.py` put back what they mean."""
    epic_cap = sys.modules.get("epic_cap")
    for name in ("labeled_elsewhere", "waiting_line"):
        if getattr(epic_cap, name, None) is not None:
            monkeypatch.setattr(epic_cap, name, lambda: [])


def _no_ambient_event(monkeypatch) -> None:
    """The read door's client refuses to ask from a `pull_request` run (the
    door refuses those tokens by design, S7) — and the CI that runs this suite
    IS a `pull_request` run. A test that is about the event sets it itself; no
    test inherits the runner's."""
    monkeypatch.delenv("GITHUB_EVENT_NAME", raising=False)


def _own_runner_temp(monkeypatch, tmp_path_factory) -> None:
    """`scripts/read_once.py` keeps a job's reads in `$RUNNER_TEMP` (Stage 2
    fix #21) — and the CI that runs this suite IS a job, with one RUNNER_TEMP
    for the whole session. A harness that hands a step `os.environ` would let
    one test's cached answer stand in for the next test's stub. Each test gets
    an empty one of its own; a harness with several cases in one test gives
    each case its own as well. A fresh directory from the factory, never one
    inside the test's own `tmp_path`, where a test may make its own."""
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path_factory.mktemp("runner-temp")))


#: CI's own value for the Linear key (tests.yml sets `LINEAR_API_KEY: test-key`).
CI_LINEAR_KEY = "test-key"
#: The other Linear keys a job can carry; CI carries none of them.
OTHER_LINEAR_KEYS = ("LINEAR_API_KEY_FALLBACK", "LINEAR_PLANNER_KEY", "LINEAR_RELEASE_KEY")


def _no_live_linear_key(monkeypatch) -> None:
    """Every test starts with CI's Linear key, wherever the suite runs (DRE-5846).

    CI sets `LINEAR_API_KEY: test-key`, so a test that reaches Linear gets a
    refusal that costs nothing. An engineer agent runs this same suite with the
    FLEET key in its environment (agent-task.yml hands it over), and there the
    same test spends a real request — and some of them write. Measured on main
    1800714: 228 tests open 412 connections to api.linear.app. On 2026-10-04
    two agents' full runs sat on the two largest drains of the fleet's
    2,500-an-hour key (about 2,200 and 1,150 requests), which stopped every
    sweep in the fleet for an hour. A test that needs another key sets its own."""
    monkeypatch.setenv("LINEAR_API_KEY", CI_LINEAR_KEY)
    for name in OTHER_LINEAR_KEYS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def fresh_sweep_board(monkeypatch, tmp_path_factory):
    _no_live_linear_key(monkeypatch)
    _no_ambient_event(monkeypatch)
    _own_runner_temp(monkeypatch, tmp_path_factory)
    _lift_drain_slots(monkeypatch)
    _empty_epic_queue(monkeypatch)
    _reset_sweep_board()
    _reset_linear_budget()
    _reset_workflow_states()
    _reset_read_door()
    yield
    _reset_sweep_board()
    _reset_linear_budget()
    _reset_workflow_states()
    _reset_read_door()
