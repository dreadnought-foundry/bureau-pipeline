"""RED-first: a limit-death marker that has stood past its clock turns the
sweep red as a STANDING DEFECT, never as a write failure (DRE-4208).

`limit_recovery.recover` returns an `ERROR:` line in two cases now: a
re-entry that did not land (a write failure — the medic reads it as a crash
and reruns) and a marker that has stood past LIMIT_DEATH_CLOCK_MINUTES
(nothing failed, a card has stood too long). The medic reads a run red on
`_stale_defects` alone as deliberate — class `standing_defect`, no rerun, no
diagnosis agent (DRE-6467) — so the clock's line belongs there, matched on
`limit_recovery.STOOD_PHRASE` and never on a restated string.

Run: cd bureau-pipeline && python3 -m pytest tests/test_limit_death_clock_ledger.py -v
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")
os.environ.setdefault("GH_TOKEN", "x")

import limit_recovery  # noqa: E402
import reconcile  # noqa: E402

CLOCK = "ERROR: limit-recovery DRE-1: the limit-death marker has stood 7.0 hours"
FAILED = "ERROR: limit-recovery DRE-2: build re-entry did not land: boom"


@pytest.fixture(autouse=True)
def _ledgers(monkeypatch):
    monkeypatch.setattr(reconcile, "REPO_SLUG", "agent-bureau")
    monkeypatch.setattr(reconcile, "REPO", "dreadnought-foundry/agent-bureau")
    reconcile._write_failures.clear()
    reconcile._stale_defects.clear()
    yield
    reconcile._write_failures.clear()
    reconcile._stale_defects.clear()


def _sweep(capsys):
    with patch.object(reconcile, "active_cards", return_value=[]), \
         patch.object(reconcile.limit_recovery, "recover", return_value=[CLOCK, FAILED]):
        reconcile.recover_limit_deaths()
    return capsys.readouterr().out


def test_the_clock_line_is_a_standing_defect_and_a_failed_write_is_a_write_failure(capsys):
    out = _sweep(capsys)
    assert reconcile._stale_defects == [CLOCK]
    assert reconcile._write_failures == [FAILED]
    assert CLOCK in out and FAILED in out


def test_the_routing_follows_the_constant_not_a_restated_string(monkeypatch, capsys):
    monkeypatch.setattr(limit_recovery, "STOOD_PHRASE", "re-entry did not land")
    _sweep(capsys)
    assert reconcile._stale_defects == [FAILED]
    assert reconcile._write_failures == [CLOCK]


def test_the_sweep_does_not_spell_the_phrase_itself():
    import inspect

    source = inspect.getsource(reconcile.recover_limit_deaths)
    assert "limit_recovery.STOOD_PHRASE" in source
    assert "marker has stood" not in source
