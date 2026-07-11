"""RED-first tests: a gh read failure is an ERROR, never "no PR" (DRE-2034).

THE BUG (live twice, 2026-06-28): reconcile's gh() helper discards exit code
and stderr, and pr_for parses `json.loads(out or "[]")` — so a 403/rate-limit/
network failure on the PR lookup is byte-identical to "this card has no PR".
Downstream that fabricated emptiness drives the Todo redispatch, the In
Progress dead-run requeue, and the In QA→Todo requeue: healthy cards get
yanked around because GitHub blinked.

Second lookup bug: pr_for scanned the newest-100 PR list, so an old card's
PR could fall off the window and read as missing — same false "no PR", no
API failure required.

FIX UNDER TEST:
  - pr_for reads via a LOUD helper: rc!=0 raises ReconcileReadError carrying
    the gh stderr, instead of returning fabricated emptiness.
  - pr_for looks the PR up by HEAD BRANCH (search `head:agent/DRE-N`), so age
    can't hide it; the newest-100 scan survives only as a fallback for search
    index lag. Among matches the highest PR number (newest attempt) wins.
  - main() treats a ReconcileReadError per card as report-and-skip: no state
    change, no comment, no dispatch off the unreadable answer — and the sweep
    exits red so medic picks it up.

Run: cd bureau-pipeline && python3 -m pytest tests/ -v
"""
from __future__ import annotations

import json
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
    """Pin REPO_SLUG (bound at import; collection-order hazard, same as
    test_dead_run_liveness) and clear the module-level failure lists before
    AND after — a leftover read failure would turn unrelated sweeps red."""
    monkeypatch.setattr(reconcile, "REPO_SLUG", "agent-bureau")
    # getattr: _read_failures is the API under test — on the unfixed code it
    # does not exist yet, and the fixture must not mask the behavioral RED.
    reconcile._write_failures.clear()
    getattr(reconcile, "_read_failures", []).clear()
    yield
    reconcile._write_failures.clear()
    getattr(reconcile, "_read_failures", []).clear()


def _ok(stdout: str):
    return SimpleNamespace(returncode=0, stdout=stdout, stderr="")


def _pr(number, branch, state="OPEN"):
    return {
        "number": number,
        "headRefName": branch,
        "state": state,
        "comments": [],
        "headRefOid": "a" * 40,
    }


# --------------------------------------------------------------------------
# pr_for: a failed gh read RAISES — it is not an empty result
# --------------------------------------------------------------------------
def test_pr_lookup_403_raises_instead_of_reading_as_no_pr():
    """REPLICATION of the 2026-06-28 incident: rc=1 + 403 stderr must raise,
    carrying the stderr so the run log shows WHY. On the unfixed code this
    FAILS — pr_for returns None (\"no PR\") and the sweep acts on it."""

    def fake_run(argv, **kwargs):
        assert argv[0] == "gh"
        return SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="HTTP 403: API rate limit exceeded for installation ID 123",
        )

    with patch.object(reconcile.subprocess, "run", side_effect=fake_run):
        with pytest.raises(reconcile.ReconcileReadError) as exc_info:
            reconcile.pr_for("DRE-2034")
    assert "403" in str(exc_info.value), "the error must carry the gh stderr"


def test_pr_lookup_success_still_returns_the_pr():
    """Control: a clean read still finds the card's PR."""

    def fake_run(argv, **kwargs):
        return _ok(json.dumps([_pr(7, "agent/DRE-2034-loud-gh-reads")]))

    with patch.object(reconcile.subprocess, "run", side_effect=fake_run):
        pr = reconcile.pr_for("DRE-2034")
    assert pr is not None and pr["number"] == 7


# --------------------------------------------------------------------------
# pr_for: head-branch lookup, not a newest-100 scan
# --------------------------------------------------------------------------
def test_head_branch_lookup_finds_pr_older_than_the_newest_100_window():
    """An old card's PR that fell off the newest-100 list must still be
    found: the lookup must query BY HEAD BRANCH (search head:agent/DRE-N),
    which the plain list scan cannot do."""
    old_pr = _pr(7, "agent/DRE-1000-ancient-card")
    newest_100 = [_pr(1000 + i, f"agent/DRE-{2000 + i}-newer") for i in range(100)]

    def fake_run(argv, **kwargs):
        assert argv[0] == "gh" and argv[1] == "pr" and argv[2] == "list"
        if "--search" in argv:
            query = argv[argv.index("--search") + 1]
            assert "head:" in query and "DRE-1000" in query, (
                f"the search must target the card's head branch, got {query!r}"
            )
            return _ok(json.dumps([old_pr]))
        return _ok(json.dumps(newest_100))  # the window the old PR fell off

    with patch.object(reconcile.subprocess, "run", side_effect=fake_run):
        pr = reconcile.pr_for("DRE-1000")
    assert pr is not None and pr["number"] == 7, (
        "pr_for must find the PR by head branch, not scan the newest-100 window"
    )


def test_near_miss_branch_never_matches():
    """Anchoring pin across the rewrite: DRE-1034 must not resolve to
    DRE-10345's branch, whatever fuzzy matches the search returns."""
    near_miss = [_pr(9, "agent/DRE-10345-other-card")]

    def fake_run(argv, **kwargs):
        return _ok(json.dumps(near_miss))

    with patch.object(reconcile.subprocess, "run", side_effect=fake_run):
        assert reconcile.pr_for("DRE-1034") is None


def test_newest_attempt_wins_over_an_older_merged_pr():
    """A card can leave several branches behind (requeued attempts). The
    HIGHEST PR number is the current attempt — an older merged PR must not
    shadow a newer open one (it would flip the card to Done under an open
    PR)."""
    matches = [
        _pr(5, "agent/DRE-1034-first-attempt", state="MERGED"),
        _pr(9, "agent/DRE-1034-retry", state="OPEN"),
    ]

    def fake_run(argv, **kwargs):
        return _ok(json.dumps(matches))

    with patch.object(reconcile.subprocess, "run", side_effect=fake_run):
        pr = reconcile.pr_for("DRE-1034")
    assert pr is not None and pr["number"] == 9


# --------------------------------------------------------------------------
# the sweep on a failed read: report loudly, act on NOTHING
# --------------------------------------------------------------------------
def _sweep_mocks(extra=None):
    m = {
        "unstick_conflicts": MagicMock(),
        "retrigger_dead_heads": MagicMock(),
        "fix_approved_but_red": MagicMock(),
        "close_finished_epics": MagicMock(),
        "promote_ready": MagicMock(return_value=0),
        "age_minutes": MagicMock(return_value=999),  # always stale
        "redispatch": MagicMock(return_value=True),
    }
    if extra:
        m.update(extra)
    return m


def _card(state, identifier="DRE-2034"):
    return {
        "identifier": identifier,
        "description": "**Repo:** agent-bureau\nwork",
        "state": {"name": state},
        "labels": {"nodes": []},
        "updatedAt": "2026-06-28T00:00:00Z",
    }


def test_sweep_403_requeues_nothing_and_goes_red():
    """ACCEPTANCE: simulated 403 on the PR lookup → the sweep reports the
    error and exits red, requeues nothing, posts no receipt. On the unfixed
    code this FAILS: the 403 reads as \"no PR\", the stale Todo card is
    re-dispatched, a 🧹 receipt posts, and the sweep exits green."""

    def fake_run(argv, **kwargs):
        if argv[0] == "gh" and argv[1] == "pr" and argv[2] == "list":
            return SimpleNamespace(
                returncode=1, stdout="", stderr="HTTP 403: Forbidden"
            )
        raise AssertionError(f"unexpected gh call: {argv}")

    mocks = _sweep_mocks({
        "active_cards": MagicMock(return_value=[_card("Todo")]),
    })
    with patch.multiple(reconcile, **mocks), patch.object(
        reconcile.subprocess, "run", side_effect=fake_run
    ), patch.object(reconcile.linear_ops, "cmd_state") as cmd_state, patch.object(
        reconcile.linear_ops, "cmd_comment"
    ) as cmd_comment, patch.object(reconcile.linear_ops, "add_label") as add_label:
        with pytest.raises(SystemExit) as exc_info:
            reconcile.main()

    assert exc_info.value.code, "the sweep must exit red so medic picks it up"
    mocks["redispatch"].assert_not_called()
    cmd_state.assert_not_called()
    cmd_comment.assert_not_called()
    add_label.assert_not_called()


def test_sweep_403_on_one_card_still_sweeps_the_others():
    """One unreadable card must not abort the whole sweep — the other cards'
    reads are independent; only the exit code goes red at the end."""
    calls = {"n": 0}

    def flaky_pr_for(identifier):
        calls["n"] += 1
        if identifier == "DRE-2034":
            raise reconcile.ReconcileReadError("gh pr list failed rc=1: HTTP 403")
        return None  # a genuinely PR-less Todo card

    mocks = _sweep_mocks({
        "active_cards": MagicMock(
            return_value=[_card("Todo"), _card("Todo", identifier="DRE-2040")]
        ),
        "pr_for": MagicMock(side_effect=flaky_pr_for),
    })
    with patch.multiple(reconcile, **mocks), patch.object(
        reconcile.linear_ops, "cmd_state"
    ), patch.object(reconcile.linear_ops, "cmd_comment"):
        with pytest.raises(SystemExit):
            reconcile.main()

    assert calls["n"] == 2, "the healthy card must still be swept"
    mocks["redispatch"].assert_called_once()  # DRE-2040 only
    assert mocks["redispatch"].call_args.args[0]["identifier"] == "DRE-2040"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
