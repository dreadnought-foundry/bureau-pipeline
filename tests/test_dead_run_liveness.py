"""RED-first tests: the dead-run requeue must never kill a LIVE build (DRE-2032).

THE BUG (live evidence, 2026-07-10 20:07-22:22Z, DRE-2023 on agent-bureau):
three consecutive builds each ran ~45 minutes with real progress receipts on
the card ("plan formed", "2/5 failing tests written"); the reconcile sweep
read In-Progress-no-PR as dead, requeued the card to Todo, and the fresh
dispatch CANCELLED the still-running build via the agent-task concurrency
group — run 29125285930 concluded cancelled at "Gate on agent result". After
three such loops the dead-run cap parked DRE-2023 needs-human: the safety
worked, but the watchdog CAUSED all three deaths. Any card whose build
legitimately needs longer than the sweep's patience could never ship.

FIX UNDER TEST:
  - agent-task.yml's "🧠 model-attempt" heartbeat carries the run URL, so the
    card itself maps to its Actions run (wiring test below).
  - reconcile.agent_run_alive(identifier): reads the newest model-attempt
    heartbeat off the card, asks GitHub for that run's status — a queued or
    in_progress run means ALIVE, regardless of elapsed time. When the run id
    or its status is unreadable, a fresh progress receipt (⏳/🧠 comment
    younger than the In Progress staleness window) is the no-GitHub-call
    proof-of-life fallback; with neither, the card is dead as before.
  - The sweep's In-Progress-no-PR branch consults agent_run_alive FIRST and
    leaves a live card completely alone — no requeue, no hold, no comment.
    A concluded run with no PR still requeues, and the DRE-1403 hold cap is
    unchanged.
  - self-agent-task.yml keeps cancel-in-progress: false on the per-card
    concurrency group, so even a wrong requeue QUEUES behind a live run
    instead of cancelling it (the DRE-2023 kill vector).

Run: cd bureau-pipeline && python3 -m pytest tests/ -v
"""
from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")
os.environ.setdefault("GH_TOKEN", "x")

import reconcile  # noqa: E402


@pytest.fixture(autouse=True)
def _pin_repo_slug(monkeypatch):
    """reconcile.REPO_SLUG is bound at import; pin it so the sweep recognises
    this test's agent-bureau cards regardless of collection order (same
    test-isolation hazard test_human_hold.py pins)."""
    monkeypatch.setattr(reconcile, "REPO_SLUG", "agent-bureau")


def _iso(minutes_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat().replace(
        "+00:00", "Z"
    )


RUN_URL = "https://github.com/dreadnought-foundry/agent-bureau/actions/runs/29125285930"


def _comments_payload(nodes):
    """Linear comments query payload: [(body, createdAt-minutes-ago), ...]."""
    return {
        "issue": {
            "comments": {
                "nodes": [{"body": b, "createdAt": _iso(age)} for b, age in nodes]
            }
        }
    }


def _gh_run_status(status: str):
    """A reconcile.gh double: answers the actions/runs/<id> status read."""

    def fake_gh(*args):
        if args[0] == "api" and "/actions/runs/" in args[1]:
            return status
        raise AssertionError(f"unexpected gh call: {args}")

    return fake_gh


# --------------------------------------------------------------------------
# reconcile.agent_run_alive — run status is authoritative
# --------------------------------------------------------------------------
def test_in_progress_run_is_alive():
    """The DRE-2023 case: heartbeat maps to run 29125285930, GitHub says the
    run is in_progress → ALIVE, no matter how stale the card looks."""
    payload = _comments_payload([
        (f"🧠 model-attempt: claude-fable-5 — engineer agent starting. Run: {RUN_URL}", 70),
        ("⏳ 2/5 failing tests written", 65),
    ])
    with patch.object(reconcile.linear_ops, "gql", return_value=payload), patch.object(
        reconcile, "gh", side_effect=_gh_run_status("in_progress")
    ):
        assert reconcile.agent_run_alive("DRE-2023") is True


def test_queued_run_is_alive():
    payload = _comments_payload([
        (f"🧠 model-attempt: claude-opus-4-8 — engineer agent starting. Run: {RUN_URL}", 70),
    ])
    with patch.object(reconcile.linear_ops, "gql", return_value=payload), patch.object(
        reconcile, "gh", side_effect=_gh_run_status("queued")
    ):
        assert reconcile.agent_run_alive("DRE-2023") is True


def test_completed_run_is_dead_despite_fresh_receipts():
    """A CONCLUDED run with no PR is the real dead case — a fresh receipt
    must not shadow GitHub's authoritative 'completed' answer."""
    payload = _comments_payload([
        (f"🧠 model-attempt: claude-fable-5 — engineer agent starting. Run: {RUN_URL}", 20),
        ("⏳ 2/5 failing tests written", 5),
    ])
    with patch.object(reconcile.linear_ops, "gql", return_value=payload), patch.object(
        reconcile, "gh", side_effect=_gh_run_status("completed")
    ):
        assert reconcile.agent_run_alive("DRE-2023") is False


def test_run_status_read_asks_github_for_the_heartbeat_run_id():
    """The status read must hit actions/runs/<the id parsed from the newest
    heartbeat> — not a list endpoint, not another card's run."""
    payload = _comments_payload([
        ("🧠 model-attempt: claude-fable-5 — engineer agent starting. "
         "Run: https://github.com/o/r/actions/runs/111", 300),
        ("🪦 dead-run-requeue: agent died — requeued to Todo (dead run 1/3).", 200),
        (f"🧠 model-attempt: claude-opus-4-8 — engineer agent starting. Run: {RUN_URL}", 70),
    ])
    calls = []

    def spy_gh(*args):
        calls.append(args)
        return "in_progress"

    with patch.object(reconcile.linear_ops, "gql", return_value=payload), patch.object(
        reconcile, "gh", side_effect=spy_gh
    ):
        assert reconcile.agent_run_alive("DRE-2023") is True
    assert len(calls) == 1
    assert "/actions/runs/29125285930" in calls[0][1], (
        "must query the NEWEST attempt's run, not an older attempt's"
    )


# --------------------------------------------------------------------------
# reconcile.agent_run_alive — receipt fallback (no GitHub answer available)
# --------------------------------------------------------------------------
def test_fresh_receipt_is_alive_when_run_id_missing():
    """Legacy heartbeat with no run URL: a receipt younger than the staleness
    window is proof of life without a GitHub call."""
    payload = _comments_payload([
        ("🧠 model-attempt: claude-fable-5 — engineer agent starting.", 90),
        ("⏳ 2/5 failing tests written", 10),
    ])
    with patch.object(reconcile.linear_ops, "gql", return_value=payload), patch.object(
        reconcile, "gh", side_effect=AssertionError("no run id -> no gh call")
    ):
        assert reconcile.agent_run_alive("DRE-2023") is True


def test_stale_receipts_are_dead_when_run_id_missing():
    """No run URL and every receipt older than the window → dead (the
    pre-DRE-2032 requeue behaviour is preserved for genuinely lost runs)."""
    payload = _comments_payload([
        ("🧠 model-attempt: claude-fable-5 — engineer agent starting.", 300),
        ("⏳ 2/5 failing tests written", 200),
    ])
    with patch.object(reconcile.linear_ops, "gql", return_value=payload), patch.object(
        reconcile, "gh", side_effect=AssertionError("no run id -> no gh call")
    ):
        assert reconcile.agent_run_alive("DRE-2023") is False


def test_unreadable_run_status_falls_back_to_fresh_receipt():
    """An API blip (gh returns '') must not requeue on uncertainty when a
    fresh receipt says the agent was just alive."""
    payload = _comments_payload([
        (f"🧠 model-attempt: claude-fable-5 — engineer agent starting. Run: {RUN_URL}", 70),
        ("⏳ 3/5 implementation green", 8),
    ])
    with patch.object(reconcile.linear_ops, "gql", return_value=payload), patch.object(
        reconcile, "gh", side_effect=_gh_run_status("")
    ):
        assert reconcile.agent_run_alive("DRE-2023") is True


def test_unreadable_run_status_with_stale_receipts_is_dead():
    payload = _comments_payload([
        (f"🧠 model-attempt: claude-fable-5 — engineer agent starting. Run: {RUN_URL}", 300),
        ("⏳ 2/5 failing tests written", 200),
    ])
    with patch.object(reconcile.linear_ops, "gql", return_value=payload), patch.object(
        reconcile, "gh", side_effect=_gh_run_status("")
    ):
        assert reconcile.agent_run_alive("DRE-2023") is False


def test_sweep_receipts_are_not_proof_of_life():
    """🪦/🧹/🚨 are the SWEEP's own comments — a fresh one must not read as
    an alive agent (that would suppress the requeue forever, since every
    requeue posts one)."""
    payload = _comments_payload([
        ("🧠 model-attempt: claude-fable-5 — engineer agent starting.", 300),
        ("🪦 dead-run-requeue: agent died — requeued to Todo (dead run 1/3).", 5),
        ("🧹 Reconcile: card sat in Todo with no run — re-dispatched.", 4),
    ])
    with patch.object(reconcile.linear_ops, "gql", return_value=payload), patch.object(
        reconcile, "gh", side_effect=AssertionError("no run id -> no gh call")
    ):
        assert reconcile.agent_run_alive("DRE-2023") is False


def test_no_comments_at_all_is_dead():
    payload = _comments_payload([])
    with patch.object(reconcile.linear_ops, "gql", return_value=payload):
        assert reconcile.agent_run_alive("DRE-2023") is False


# --------------------------------------------------------------------------
# the sweep branch: a live run suppresses the dead-run path entirely
# --------------------------------------------------------------------------
def _full_sweep_mocks(extra=None):
    m = {
        "unstick_conflicts": MagicMock(),
        "retrigger_dead_heads": MagicMock(),
        "fix_approved_but_red": MagicMock(),
        "close_finished_epics": MagicMock(),
        "promote_ready": MagicMock(return_value=0),
        "age_minutes": MagicMock(return_value=999),  # always stale
        "pr_for": MagicMock(return_value=None),  # no PR
        # DRE-1993: the stranded-card watchdog runs on every full sweep and
        # would make real Linear calls on these mocked cards; stub it out —
        # these tests exercise the dead-run requeue, not the watchdog.
        "flag_stranded": MagicMock(return_value=set()),
    }
    if extra:
        m.update(extra)
    return m


def _inprogress_card():
    return {
        "identifier": "DRE-2023",
        "description": "**Repo:** agent-bureau\nwork",
        "state": {"name": "In Progress"},
        "labels": {"nodes": []},
        "updatedAt": "2026-07-10T20:07:00Z",
    }


def test_sweep_leaves_live_card_alone():
    """MUTATION CHECK: delete the agent_run_alive consult in main()'s
    In-Progress-no-PR branch and this requeues DRE-2023 to Todo — the exact
    kill vector of 2026-07-10 (the Todo transition re-dispatches, and the
    fresh run cancels the live one via the concurrency group)."""
    reconcile._write_failures.clear()
    mocks = _full_sweep_mocks({
        "active_cards": MagicMock(return_value=[_inprogress_card()]),
        "agent_run_alive": MagicMock(return_value=True),
    })
    with patch.multiple(reconcile, **mocks), patch.object(
        reconcile.linear_ops, "count_comments", return_value=0
    ), patch.object(reconcile.linear_ops, "add_label") as add_label, patch.object(
        reconcile.linear_ops, "cmd_state"
    ) as cmd_state, patch.object(reconcile.linear_ops, "cmd_comment") as cmd_comment:
        reconcile.main()
    cmd_state.assert_not_called()
    cmd_comment.assert_not_called()
    add_label.assert_not_called()


def test_sweep_leaves_live_card_alone_even_at_the_cap():
    """'A live run = not dead, regardless of elapsed time' — and regardless of
    how many PRIOR attempts died. A live run at the cap must not be parked."""
    reconcile._write_failures.clear()
    mocks = _full_sweep_mocks({
        "active_cards": MagicMock(return_value=[_inprogress_card()]),
        "agent_run_alive": MagicMock(return_value=True),
    })
    with patch.multiple(reconcile, **mocks), patch.object(
        reconcile.linear_ops, "count_comments", return_value=2
    ), patch.object(reconcile.linear_ops, "add_label") as add_label, patch.object(
        reconcile.linear_ops, "cmd_state"
    ) as cmd_state, patch.object(reconcile.linear_ops, "cmd_comment"):
        reconcile.main()
    cmd_state.assert_not_called()
    add_label.assert_not_called()


def test_sweep_still_requeues_dead_card_below_cap():
    """A concluded run with no PR still requeues (the real dead case)."""
    reconcile._write_failures.clear()
    mocks = _full_sweep_mocks({
        "active_cards": MagicMock(return_value=[_inprogress_card()]),
        "agent_run_alive": MagicMock(return_value=False),
    })
    with patch.multiple(reconcile, **mocks), patch.object(
        reconcile.linear_ops, "count_comments", return_value=1
    ), patch.object(reconcile.linear_ops, "add_label") as add_label, patch.object(
        reconcile.linear_ops, "cmd_state"
    ) as cmd_state, patch.object(reconcile.linear_ops, "cmd_comment"):
        reconcile.main()
    cmd_state.assert_called_once_with("DRE-2023", "Todo")
    add_label.assert_not_called()


def test_sweep_still_holds_dead_card_at_cap():
    """The DRE-1403 hold cap is unchanged for genuinely dead runs."""
    reconcile._write_failures.clear()
    mocks = _full_sweep_mocks({
        "active_cards": MagicMock(return_value=[_inprogress_card()]),
        "agent_run_alive": MagicMock(return_value=False),
    })
    with patch.multiple(reconcile, **mocks), patch.object(
        reconcile.linear_ops, "count_comments", return_value=2
    ), patch.object(reconcile.linear_ops, "add_label") as add_label, patch.object(
        reconcile.linear_ops, "cmd_state"
    ) as cmd_state, patch.object(reconcile.linear_ops, "cmd_comment"):
        reconcile.main()
    add_label.assert_called_once_with("DRE-2023", reconcile.HOLD_LABEL)
    cmd_state.assert_called_once_with("DRE-2023", "Backlog", "--park")


# --------------------------------------------------------------------------
# wiring over the shells: the heartbeat carries the run URL; the stub's
# concurrency group cannot cancel a live run
# --------------------------------------------------------------------------
def _agent_task_step(name):
    doc = yaml.safe_load((ROOT / ".github" / "workflows" / "agent-task.yml").read_text())
    steps = doc["jobs"]["execute"]["steps"]
    matches = [s for s in steps if s.get("name") == name]
    assert len(matches) == 1, f"expected exactly one {name!r} step"
    return matches[0]


def test_model_attempt_heartbeat_carries_the_run_url():
    """The sweep maps card → run through the heartbeat: the 'Card → In
    Progress' step's model-attempt comment must embed this run's URL."""
    run_block = _agent_task_step("Card → In Progress")["run"]
    attempt_line = next(
        (ln for ln in run_block.splitlines() if "model-attempt" in ln), ""
    )
    assert "/actions/runs/${{ github.run_id }}" in attempt_line, (
        "the 🧠 model-attempt heartbeat must carry the run URL so "
        "reconcile.agent_run_alive can find the run (DRE-2032)"
    )


def test_self_stub_requeue_queues_instead_of_cancelling():
    """Pin the anti-kill-vector: the per-card concurrency group must NOT
    cancel in progress — a (wrong) requeue's dispatch then queues behind the
    live run instead of cancelling it mid-build (run 29125285930's fate)."""
    doc = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "self-agent-task.yml").read_text()
    )
    conc = doc["concurrency"]
    assert "client_payload.identifier" in conc["group"]
    assert conc["cancel-in-progress"] is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
