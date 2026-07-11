"""Replication + fix tests for the silent-dispatch-failure bug (PR #48 / DRE-1254).

THE BUG (live evidence, 2026-06-12):
  - The 04:16Z reconcile sweep printed
      "conflict: PR #48 (agent/DRE-1254-...) DIRTY — dispatching fix agent"
  - but NO Agent Fix run was created (run list gap 23:55Z -> 05:18Z manual).
  - Root cause chain: the sweep's GH_TOKEN is the minted bureau App token,
    which lacks Actions:write -> `gh workflow run` returns
    "HTTP 403: Resource not accessible by integration" -> reconcile.py's
    gh() helper (check=False, stderr discarded) swallows it -> the sweep
    reports success while the conflicted PR stays stuck forever.

DESIRED behavior (these tests express it; they FAIL on the unfixed code,
replicating the bug, and pass after the fix):
  1. A failed workflow-dispatch must be LOUD: unstick_conflicts raises
     (or records) a write failure instead of pretending success.
  2. Workflow-dispatch calls must use GH_DISPATCH_TOKEN (the workflow's
     github.token, which the calling stub grants actions:write) when set.

Run: cd bureau-pipeline && python3 -m pytest tests/ -v
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

os.environ.setdefault("REPO", "EveryBite/atlas")
os.environ.setdefault("REPO_SLUG", "atlas")

import reconcile  # noqa: E402


def _fake_run_factory(dispatch_rc: int, calls: list):
    """subprocess.run stub: healthy reads, failing/recording writes.

    - `gh run list`        -> no busy fix runs
    - `gh pr list`         -> one DIRTY agent PR (#48, the real shape)
    - `gh workflow run`    -> rc=dispatch_rc with the real 403 stderr;
                              the call (args, env) is recorded in `calls`.
    """

    def fake_run(argv, **kwargs):
        joined = " ".join(argv)
        if "run list" in joined.replace("gh ", "", 1)[:20] or (
            argv[1] == "run" and argv[2] == "list"
        ):
            return SimpleNamespace(returncode=0, stdout="[]", stderr="")
        if argv[1] == "pr" and argv[2] == "list":
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    '[{"number": 48, '
                    '"headRefName": "agent/DRE-1254-uncertainty-disclosure", '
                    '"mergeStateStatus": "DIRTY"}]'
                ),
                stderr="",
            )
        if argv[1] == "workflow" and argv[2] == "run":
            calls.append({"argv": argv, "env": kwargs.get("env")})
            return SimpleNamespace(
                returncode=dispatch_rc,
                stdout="",
                stderr=(
                    "HTTP 403: Resource not accessible by integration "
                    "(https://api.github.com/repos/EveryBite/atlas/actions/"
                    "workflows/agent-fix.yml/dispatches)"
                    if dispatch_rc
                    else ""
                ),
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    return fake_run


def test_failed_dispatch_is_loud_not_silent():
    """REPLICATION: a 403 on `gh workflow run` must not vanish.

    On the unfixed code this test FAILS — unstick_conflicts() swallows the
    403 and returns normally (the live PR #48 behavior). The fix must make
    the failure observable: raise ReconcileWriteError (preferred) so main()
    can mark the sweep run red and medic picks it up.
    """
    calls: list = []
    with patch.object(
        reconcile.subprocess, "run", side_effect=_fake_run_factory(1, calls)
    ), patch.object(reconcile, "card_parked_for_human", return_value=False):
        with pytest.raises(Exception) as exc_info:
            reconcile.unstick_conflicts()

    assert calls, "the DIRTY PR must trigger a dispatch attempt"
    assert "403" in str(exc_info.value) or "dispatch" in str(exc_info.value).lower(), (
        "the raised error must carry the gh stderr so the run log shows WHY"
    )


def test_successful_dispatch_does_not_raise():
    """Control: a clean dispatch keeps the sweep green."""
    calls: list = []
    with patch.object(
        reconcile.subprocess, "run", side_effect=_fake_run_factory(0, calls)
    ), patch.object(reconcile, "card_parked_for_human", return_value=False):
        reconcile.unstick_conflicts()  # must not raise
    assert len(calls) == 1


def test_dispatch_uses_dispatch_token_when_set():
    """Workflow-dispatch must run under GH_DISPATCH_TOKEN (github.token has
    actions:write via the calling stub) — not the App token in GH_TOKEN."""
    calls: list = []
    with patch.dict(os.environ, {"GH_DISPATCH_TOKEN": "ghs_dispatch", "GH_TOKEN": "ghs_app"}):
        with patch.object(
            reconcile.subprocess, "run", side_effect=_fake_run_factory(0, calls)
        ), patch.object(reconcile, "card_parked_for_human", return_value=False):
            reconcile.unstick_conflicts()
    assert calls, "dispatch attempt expected"
    env = calls[0]["env"]
    assert env is not None and env.get("GH_TOKEN") == "ghs_dispatch", (
        "gh workflow run must execute with GH_TOKEN=GH_DISPATCH_TOKEN "
        "(the token that actually holds actions:write)"
    )
