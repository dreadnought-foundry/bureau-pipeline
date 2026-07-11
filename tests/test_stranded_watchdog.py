"""RED-first tests: flag active-lane cards with no evidence of work (DRE-1993).

THE BUG (live incident, filed 2026-07-09): DRE-1978 sat in **Planning for
seven days with zero planner runs** — at the time its `repo:bureau-pipeline`
label routed nowhere, the relay never dispatched agent-plan, and nothing
anywhere alarms on "the board says work is happening but no workflow ever
started". The CEO discovered it by asking. Same silent-failure class as the
GitHub Actions budget block (runs die in seconds, cards strand) and quota
exhaustion (dispatch pauses; 2026-06-28 incident). bureau-pipeline is on the
rail now (DRE-1929), but budget blocks, quota exhaustion, relay outages, and
any FUTURE off-map repo all still strand cards silently.

FIX UNDER TEST — reconcile.flag_stranded(), run on every full sweep over the
ACTIVE lanes (Planning / Todo / In Progress — Planning was previously
invisible to the sweep entirely):
  (a) a card/epic whose repo has NO route in the routing snapshot
      (validate_card.VALID_SLUGS) can never be dispatched — comment
      "hand-build" + add the needs-human hold label, within one sweep;
  (b) a dispatchable card of THIS sweep's repo showing NO run receipt (the
      DRE-2032 🧠/⏳ proof-of-life comments — agent-task AND plan both post
      them) after WATCHDOG_MINUTES, or after a prior Todo-redispatch receipt
      (which resets updatedAt every cycle and would otherwise hide the
      strand forever) — comment "no run started" + the hold label.
  Each card is flagged ONCE — the WATCHDOG_TAG comment is the idempotency
  marker — and a card with any live/completed run receipt is never flagged
  (that is the dead-run requeue's territory, not this watchdog's).

Run: cd bureau-pipeline && python3 -m pytest tests/ -v
"""
from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")
os.environ.setdefault("GH_TOKEN", "x")

import reconcile  # noqa: E402
import validate_card  # noqa: E402


@pytest.fixture(autouse=True)
def _pin_repo_slug(monkeypatch):
    """reconcile.REPO_SLUG is bound at import; pin it so the watchdog
    recognises this test's agent-bureau cards regardless of collection order
    (same test-isolation hazard test_human_hold.py pins)."""
    monkeypatch.setattr(reconcile, "REPO_SLUG", "agent-bureau")


@pytest.fixture(autouse=True)
def _pin_valid_slugs(monkeypatch):
    """Pin the routing snapshot so 'ghost-product' stays off-map even if the
    real config/repo-map.json grows."""
    monkeypatch.setattr(
        validate_card, "VALID_SLUGS", {"agent-bureau", "atlas", "bureau-pipeline"}
    )


def _iso(minutes_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat().replace(
        "+00:00", "Z"
    )


def _card(
    identifier="DRE-1978",
    state="Todo",
    labels=("repo:agent-bureau",),
    minutes_stale=45.0,
):
    return {
        "id": f"uuid-{identifier}",
        "identifier": identifier,
        "title": "a stranded card",
        "description": "work",
        "updatedAt": _iso(minutes_stale),
        "state": {"name": state},
        "labels": {"nodes": [{"name": n} for n in labels]},
    }


def _run_watchdog(cards, bodies=()):
    """Run flag_stranded over `cards` with the card's comments mocked to
    `bodies`; returns (result, cmd_comment mock, add_label mock)."""
    with patch.object(
        reconcile, "active_cards", return_value=list(cards)
    ) as active, patch.object(
        reconcile.linear_ops, "comment_bodies", return_value=list(bodies)
    ), patch.object(
        reconcile.linear_ops, "cmd_comment"
    ) as comment, patch.object(
        reconcile.linear_ops, "add_label"
    ) as add_label:
        result = reconcile.flag_stranded()
    assert active.call_args.args[0] == reconcile.WATCHDOG_LANES, (
        "the watchdog must sweep its own lane list (Planning included)"
    )
    return result, comment, add_label


# --------------------------------------------------------------------------
# case (a): no repo-map route — flagged within one sweep, however fresh
# --------------------------------------------------------------------------
def test_no_route_card_flagged_within_one_sweep():
    card = _card(labels=("repo:ghost-product",), minutes_stale=2)
    flagged, comment, add_label = _run_watchdog([card])
    assert flagged == {"DRE-1978"}
    body = comment.call_args.args[1]
    assert body.startswith(f"🚨 {reconcile.WATCHDOG_TAG}:")
    assert "hand-built" in body
    add_label.assert_called_once_with("DRE-1978", reconcile.HOLD_LABEL)


def test_no_route_epic_in_planning_flagged():
    """The DRE-1978 shape as filed: an EPIC parked in Planning whose repo
    routes nowhere — epics count, and Planning is a watchdog lane."""
    card = _card(
        state="Planning",
        labels=("repo:ghost-product", "agent:planner"),
        minutes_stale=7 * 24 * 60,
    )
    flagged, comment, add_label = _run_watchdog([card])
    assert flagged == {"DRE-1978"}
    assert "hand-built" in comment.call_args.args[1]


def test_missing_repo_label_counts_as_no_route():
    card = _card(labels=(), minutes_stale=2)
    flagged, comment, _ = _run_watchdog([card])
    assert flagged == {"DRE-1978"}
    assert "hand-built" in comment.call_args.args[1]


# --------------------------------------------------------------------------
# case (b): dispatchable but no run ever started
# --------------------------------------------------------------------------
def test_dispatchable_todo_card_with_no_run_after_30min_flagged():
    card = _card(minutes_stale=45)
    flagged, comment, add_label = _run_watchdog([card], bodies=[])
    assert flagged == {"DRE-1978"}
    body = comment.call_args.args[1]
    assert body.startswith(f"🚨 {reconcile.WATCHDOG_TAG}:")
    assert "no agent run" in body
    add_label.assert_called_once_with("DRE-1978", reconcile.HOLD_LABEL)


def test_young_dispatchable_card_not_flagged():
    """Under WATCHDOG_MINUTES with no redispatch receipt: the dispatch gets
    time to start a run before anyone alarms."""
    card = _card(minutes_stale=10)
    flagged, comment, add_label = _run_watchdog([card], bodies=[])
    assert flagged == set()
    comment.assert_not_called()
    add_label.assert_not_called()


def test_redispatch_receipt_counts_as_elapsed_time():
    """The Todo-redispatch receipt bumps updatedAt every ~15-minute cycle, so
    a silently-failing dispatch loop never LOOKS 30 minutes stale. A prior
    receipt with still no proof-of-life IS the 30-minute evidence."""
    card = _card(minutes_stale=3)  # just bumped by the receipt itself
    flagged, comment, _ = _run_watchdog(
        [card],
        bodies=["🧹 Reconcile: card sat in Todo with no run — re-dispatched."],
    )
    assert flagged == {"DRE-1978"}
    assert "no agent run" in comment.call_args.args[1]


@pytest.mark.parametrize(
    "receipt",
    [
        "🧠 model-attempt: claude-fable-5 — engineer agent starting. "
        "Run: https://github.com/o/r/actions/runs/111",
        "🧠 model-attempt: claude-opus-4-8 — planner agent starting.",
        "⏳ 2/5 failing tests written",
    ],
)
def test_run_receipt_suppresses_flag(receipt):
    """A card with a live/completed matching run is NEVER flagged — a run
    receipt (the DRE-2032 proof-of-life prefixes) is that evidence, and a
    run that started-then-died is the dead-run requeue's case, not ours."""
    card = _card(minutes_stale=999)
    flagged, comment, add_label = _run_watchdog([card], bodies=[receipt])
    assert flagged == set()
    comment.assert_not_called()
    add_label.assert_not_called()


def test_planning_epic_with_no_planner_run_flagged():
    """DRE-1978's watchdog class with the repo NOW routable: an epic in
    Planning where agent-plan never ran must still alarm."""
    card = _card(
        state="Planning",
        labels=("repo:agent-bureau", "agent:planner"),
        minutes_stale=7 * 24 * 60,
    )
    flagged, comment, _ = _run_watchdog([card], bodies=[])
    assert flagged == {"DRE-1978"}
    assert "no agent run" in comment.call_args.args[1]


def test_routable_epic_in_todo_not_flagged():
    """Epics past Planning are containers — no run ever targets them, so
    'no run receipts' is their NORMAL state, not a strand."""
    card = _card(
        state="Todo",
        labels=("repo:agent-bureau", "agent:planner"),
        minutes_stale=999,
    )
    flagged, comment, add_label = _run_watchdog([card], bodies=[])
    assert flagged == set()
    comment.assert_not_called()
    add_label.assert_not_called()


def test_other_repos_routable_cards_left_to_their_own_sweep():
    """atlas's run receipts live on atlas's rail — its own sweep owns the
    no-run check; this sweep must not cross-flag."""
    card = _card(labels=("repo:atlas",), minutes_stale=999)
    flagged, comment, add_label = _run_watchdog([card], bodies=[])
    assert flagged == set()
    comment.assert_not_called()
    add_label.assert_not_called()


# --------------------------------------------------------------------------
# idempotency + hold interplay
# --------------------------------------------------------------------------
def test_resweep_never_duplicates_the_flag():
    """The WATCHDOG_TAG comment is the once-ever marker: a re-sweep (even
    after a human removed the hold label) posts nothing."""
    card = _card(labels=("repo:ghost-product",), minutes_stale=999)
    flagged, comment, add_label = _run_watchdog(
        [card], bodies=[f"🚨 {reconcile.WATCHDOG_TAG}: repo ghost-product …"]
    )
    assert flagged == set()
    comment.assert_not_called()
    add_label.assert_not_called()


def test_held_card_untouched():
    card = _card(labels=("repo:ghost-product", reconcile.HOLD_LABEL), minutes_stale=999)
    flagged, comment, add_label = _run_watchdog([card])
    assert flagged == set()
    comment.assert_not_called()
    add_label.assert_not_called()


def test_watchdog_comment_is_machine_marked_not_proof_of_life():
    """🚨 must stay a machine marker (never clears the DRE-1585 blocker
    guard) and must never read as an alive agent to agent_run_alive — or
    the watchdog's own comment would suppress the dead-run requeue."""
    assert "🚨" in reconcile._AGENT_COMMENT_PREFIXES
    assert not "🚨".startswith(reconcile._LIFE_PREFIXES)
    card = _card(labels=("repo:ghost-product",))
    _, comment, _ = _run_watchdog([card])
    assert comment.call_args.args[1].startswith("🚨")


# --------------------------------------------------------------------------
# lane visibility: the watchdog sweeps Planning; the nudge loop is unchanged
# --------------------------------------------------------------------------
def test_active_cards_takes_a_states_filter():
    """active_cards(WATCHDOG_LANES) must query Planning; the default stays
    byte-identical to the pre-DRE-1993 sweep (no Planning in the nudge loop,
    no Planning cards counted against the WIP cap)."""
    seen = []

    def spy_gql(query, variables=None):
        seen.append(variables)
        return {"issues": {"nodes": []}}

    with patch.object(reconcile.linear_ops, "gql", side_effect=spy_gql):
        reconcile.active_cards(reconcile.WATCHDOG_LANES)
        reconcile.active_cards()
    assert seen[0] == {"states": ["Planning", "Todo", "In Progress"]}
    assert seen[1] == {"states": ["Todo", "In Progress", "In QA", "In Review"]}


# --------------------------------------------------------------------------
# main() wiring: full sweep only, and a flag this sweep suppresses the nudge
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
    }
    if extra:
        m.update(extra)
    return m


def _todo_card():
    return {
        "identifier": "DRE-1978",
        "description": "**Repo:** agent-bureau\nwork",
        "state": {"name": "Todo"},
        "labels": {"nodes": []},
        "updatedAt": "2026-07-09T09:00:00Z",
    }


def test_sweep_does_not_redispatch_a_card_flagged_this_sweep():
    """MUTATION CHECK: the nudge loop reads labels fetched BEFORE the
    watchdog added the hold label — without the flagged-set skip, the very
    same sweep re-dispatches the card it just parked for a human."""
    reconcile._write_failures.clear()
    mocks = _full_sweep_mocks({
        "active_cards": MagicMock(return_value=[_todo_card()]),
        "flag_stranded": MagicMock(return_value={"DRE-1978"}),
        "redispatch": MagicMock(),
    })
    with patch.multiple(reconcile, **mocks), patch.object(
        reconcile.linear_ops, "cmd_comment"
    ) as cmd_comment, patch.object(reconcile.linear_ops, "cmd_state"):
        reconcile.main()
    mocks["redispatch"].assert_not_called()
    cmd_comment.assert_not_called()


def test_sweep_still_redispatches_unflagged_todo_cards():
    """The watchdog must not neuter the existing Todo requeue for cards it
    did NOT flag."""
    reconcile._write_failures.clear()
    mocks = _full_sweep_mocks({
        "active_cards": MagicMock(return_value=[_todo_card()]),
        "flag_stranded": MagicMock(return_value=set()),
        "redispatch": MagicMock(),
    })
    with patch.multiple(reconcile, **mocks), patch.object(
        reconcile.linear_ops, "cmd_comment"
    ), patch.object(reconcile.linear_ops, "cmd_state"):
        reconcile.main()
    mocks["redispatch"].assert_called_once()


def test_full_sweep_runs_the_watchdog():
    reconcile._write_failures.clear()
    mocks = _full_sweep_mocks({
        "active_cards": MagicMock(return_value=[]),
        "flag_stranded": MagicMock(return_value=set()),
    })
    with patch.multiple(reconcile, **mocks):
        reconcile.main()
    mocks["flag_stranded"].assert_called_once()


def test_promote_only_skips_the_watchdog():
    """promote_only is the event-driven fast path (plan.yml / linear-sync)
    with only LINEAR_API_KEY — the watchdog belongs to the full sweep."""
    reconcile._write_failures.clear()
    mocks = _full_sweep_mocks({
        "active_cards": MagicMock(return_value=[]),
        "flag_stranded": MagicMock(return_value=set()),
    })
    with patch.multiple(reconcile, **mocks):
        reconcile.main(promote_only=True)
    mocks["flag_stranded"].assert_not_called()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
