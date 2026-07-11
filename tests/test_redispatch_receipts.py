"""RED-first tests: the redispatch receipt must be HONEST (DRE-2034).

THE BUG: redispatch() fires `gh api repos/<repo>/dispatches` through the
silent gh() helper — exit code and stderr discarded — and main() posts the
"🧹 … re-dispatched" receipt unconditionally. A 403'd dispatch still tells
the CEO the card was restarted while nothing runs: the DRE-1254 false-receipt
class, one layer up from the `gh workflow run` fix that already shipped.

FIX UNDER TEST:
  - redispatch() returns True only on a confirmed rc=0 dispatch; a failure
    is recorded in the sweep's failure list (run goes red for medic) instead
    of vanishing.
  - main()'s Todo branch gates the 🧹 receipt on that return value; a failed
    dispatch posts an honest failure comment instead.
  - The dispatch runs under the default App token — NOT GH_DISPATCH_TOKEN,
    which carries actions:write for `gh workflow run` but only contents:read
    (the repository_dispatch API needs the App token's contents:write).

Run: cd bureau-pipeline && python3 -m pytest tests/ -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")
os.environ.setdefault("GH_TOKEN", "x")

import reconcile  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_failure_state(monkeypatch):
    monkeypatch.setattr(reconcile, "REPO_SLUG", "agent-bureau")
    # getattr: _read_failures is the API under test — on the unfixed code it
    # does not exist yet, and the fixture must not mask the behavioral RED.
    reconcile._write_failures.clear()
    getattr(reconcile, "_read_failures", []).clear()
    yield
    reconcile._write_failures.clear()
    getattr(reconcile, "_read_failures", []).clear()


def _todo_card():
    return {
        "id": "uuid-2034",
        "identifier": "DRE-2034",
        "title": "reconcile: gh read failures abort loudly",
        "description": "**Repo:** agent-bureau\nwork",
        "state": {"name": "Todo"},
        "labels": {"nodes": [{"name": "agent:engineer"}]},
        "updatedAt": "2026-06-28T00:00:00Z",
    }


def _dispatch_run_factory(rc: int, calls: list):
    def fake_run(argv, **kwargs):
        assert argv[0] == "gh" and argv[1] == "api" and "/dispatches" in argv[2], (
            f"unexpected gh call: {argv}"
        )
        calls.append({"argv": argv, "env": kwargs.get("env")})
        return SimpleNamespace(
            returncode=rc,
            stdout="",
            stderr="HTTP 403: Resource not accessible by integration" if rc else "",
        )

    return fake_run


# --------------------------------------------------------------------------
# redispatch(): confirmed success vs recorded failure
# --------------------------------------------------------------------------
def test_failed_dispatch_returns_false_and_records_the_failure():
    """On the unfixed code this FAILS: the 403 vanishes into the silent gh()
    and nothing lands in the failure list, so the sweep stays green."""
    calls: list = []
    with patch.object(
        reconcile.subprocess, "run", side_effect=_dispatch_run_factory(1, calls)
    ):
        ok = reconcile.redispatch(_todo_card())
    assert calls, "the dispatch must be attempted"
    assert ok is False
    assert reconcile._write_failures, (
        "a failed dispatch must be recorded so the sweep run goes red"
    )
    assert "403" in reconcile._write_failures[0]


def test_successful_dispatch_returns_true():
    calls: list = []
    with patch.object(
        reconcile.subprocess, "run", side_effect=_dispatch_run_factory(0, calls)
    ):
        ok = reconcile.redispatch(_todo_card())
    assert ok is True
    assert not reconcile._write_failures


def test_redispatch_uses_the_app_token_not_the_dispatch_token():
    """repository_dispatch needs contents:write — the App token in GH_TOKEN.
    GH_DISPATCH_TOKEN (the stub's github.token) is contents:read and would
    403 here; it exists only for `gh workflow run` (actions:write)."""
    calls: list = []
    with patch.dict(
        os.environ, {"GH_DISPATCH_TOKEN": "ghs_dispatch", "GH_TOKEN": "ghs_app"}
    ), patch.object(
        reconcile.subprocess, "run", side_effect=_dispatch_run_factory(0, calls)
    ):
        reconcile.redispatch(_todo_card())
    env = calls[0]["env"]
    assert env is None or env.get("GH_TOKEN") != "ghs_dispatch", (
        "redispatch must run under the default App token"
    )


# --------------------------------------------------------------------------
# main()'s Todo branch: the receipt follows the dispatch's real outcome
# --------------------------------------------------------------------------
def _sweep_mocks(extra=None):
    m = {
        "unstick_conflicts": MagicMock(),
        "retrigger_dead_heads": MagicMock(),
        "fix_approved_but_red": MagicMock(),
        # DRE-2018 added a fourth sweep backstop; mock it out with its
        # siblings so these receipt tests exercise only the Todo-redispatch
        # path, not the fix-retry backstop's own gh calls (covered by
        # test_reconcile_retries_dead_fix_runs.py).
        "retry_dead_fix_runs": MagicMock(),
        "close_finished_epics": MagicMock(),
        "promote_ready": MagicMock(return_value=0),
        "age_minutes": MagicMock(return_value=999),  # always stale
        "pr_for": MagicMock(return_value=None),  # no PR — the redispatch case
    }
    if extra:
        m.update(extra)
    return m


def test_success_receipt_posts_only_on_confirmed_dispatch():
    mocks = _sweep_mocks({
        "active_cards": MagicMock(return_value=[_todo_card()]),
        "redispatch": MagicMock(return_value=True),
    })
    with patch.multiple(reconcile, **mocks), patch.object(
        reconcile.linear_ops, "cmd_state"
    ), patch.object(reconcile.linear_ops, "cmd_comment") as cmd_comment:
        reconcile.main()
    bodies = [c.args[1] for c in cmd_comment.call_args_list]
    assert any("re-dispatched" in b for b in bodies)


def test_failed_dispatch_posts_honest_failure_not_a_success_receipt():
    """ACCEPTANCE: failed redispatch → no 🧹 success comment; an honest
    failure comment posts instead. On the unfixed code this FAILS — the
    receipt posts unconditionally."""
    mocks = _sweep_mocks({
        "active_cards": MagicMock(return_value=[_todo_card()]),
        "redispatch": MagicMock(return_value=False),
    })
    with patch.multiple(reconcile, **mocks), patch.object(
        reconcile.linear_ops, "cmd_state"
    ), patch.object(reconcile.linear_ops, "cmd_comment") as cmd_comment:
        reconcile.main()
    bodies = [c.args[1] for c in cmd_comment.call_args_list]
    assert not any("re-dispatched" in b for b in bodies), (
        "a failed dispatch must never post the success receipt"
    )
    assert bodies and any("fail" in b.lower() for b in bodies), (
        "the failure must be surfaced honestly on the card"
    )


def test_failed_dispatch_end_to_end_goes_red_with_no_false_receipt():
    """Same scenario through the REAL redispatch(): the 403 is recorded and
    the sweep exits red — medic's failed-workflow path picks it up."""
    calls: list = []
    mocks = _sweep_mocks({
        "active_cards": MagicMock(return_value=[_todo_card()]),
    })
    with patch.multiple(reconcile, **mocks), patch.object(
        reconcile.subprocess, "run", side_effect=_dispatch_run_factory(1, calls)
    ), patch.object(reconcile.linear_ops, "cmd_state"), patch.object(
        reconcile.linear_ops, "cmd_comment"
    ) as cmd_comment:
        with pytest.raises(SystemExit) as exc_info:
            reconcile.main()
    assert exc_info.value.code
    bodies = [c.args[1] for c in cmd_comment.call_args_list]
    assert not any("re-dispatched" in b for b in bodies)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
