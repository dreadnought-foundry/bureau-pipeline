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
WATCHDOG lanes (Todo / In Progress — Planning has its own rule since
DRE-2736; see tests/test_planning_lane_strand.py):
  (a) a card/epic whose repo has NO route in the routing snapshot
      (validate_card.VALID_SLUGS) can never be dispatched — comment
      "hand-build" + add the needs-human hold label, once the card is past
      WATCHDOG_MINUTES (DRE-2736: a card is genuinely unroutable for a real
      interval between creation and the Todo gate's repair pass, and
      flagging on the first sweep races that repair);
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


# The canonical @main snapshot the NO-ROUTE adjudication re-checks (DRE-2260).
# Pinned to the same set as _pin_valid_slugs so the off-map cards above
# (ghost-product) still adjudicate as truly off-rail — hermetically, with no
# live gh fetch from the test run. Individual DRE-2260 tests override it.
_LIVE_SNAPSHOT = frozenset({"agent-bureau", "atlas", "bureau-pipeline"})

# The real fetcher, captured before the autouse pin below replaces the module
# attribute — the parse tests exercise it directly against a stubbed gh().
_REAL_LIVE_RAIL_SLUGS = getattr(reconcile, "live_rail_slugs", None)


@pytest.fixture(autouse=True)
def _pin_live_snapshot(monkeypatch):
    # raising=False: harmless before the attribute exists (the RED commit).
    monkeypatch.setattr(
        reconcile, "live_rail_slugs", lambda: _LIVE_SNAPSHOT, raising=False
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
    `bodies`; returns (result, cmd_comment mock, add_label mock).

    The active_cards stub honours the lane filter it is handed, the way
    Linear does — since DRE-2736 the watchdog reads two lane sets (its own
    WATCHDOG_LANES, and Planning under its own rule) and a card must be
    judged by the pass whose lane it actually sits in.
    """
    def by_lane(states=reconcile.SWEEP_STATES):
        return [c for c in cards if c["state"]["name"] in states]

    with patch.object(
        reconcile, "active_cards", side_effect=by_lane
    ) as active, patch.object(
        reconcile.linear_ops, "comment_bodies", return_value=list(bodies)
    ), patch.object(
        reconcile.linear_ops, "cmd_comment"
    ) as comment, patch.object(
        reconcile.linear_ops, "add_label"
    ) as add_label:
        result = reconcile.flag_stranded()
    lanes = [c.args[0] for c in active.call_args_list]
    assert reconcile.WATCHDOG_LANES in lanes, (
        "the watchdog must sweep its own lane list"
    )
    return result, comment, add_label


# --------------------------------------------------------------------------
# case (a): no repo-map route — flagged once the grace period is up
# --------------------------------------------------------------------------
def test_no_route_card_flagged_past_the_grace_period():
    card = _card(labels=("repo:ghost-product",), minutes_stale=45)
    flagged, comment, add_label = _run_watchdog([card])
    assert flagged == {"DRE-1978"}
    body = comment.call_args.args[1]
    assert body.startswith(f"🚨 {reconcile.WATCHDOG_TAG}:")
    assert "hand-built" in body
    add_label.assert_called_once_with("DRE-1978", reconcile.HOLD_LABEL)


def test_missing_repo_label_counts_as_no_route():
    card = _card(labels=(), minutes_stale=45)
    flagged, comment, _ = _run_watchdog([card])
    assert flagged == {"DRE-1978"}
    assert "hand-built" in comment.call_args.args[1]


# DRE-2736: the NO-ROUTE class had NO age gate at all — it fired on the first
# sweep that saw a card, however fresh. But a card IS unroutable for a real
# interval between creation and the Todo gate's repair pass (validate_card
# infers a missing repo label and repairs it), so flagging on sight races that
# repair and parks a card the pipeline was seconds from fixing — permanently,
# because the hold label makes promote_ready skip the card forever.
@pytest.mark.parametrize(
    "labels", [("repo:ghost-product",), ()], ids=["off-map-slug", "no-repo-label"]
)
def test_fresh_unroutable_card_survives_one_sweep(labels):
    """A card created two minutes ago gets the same grace the no-run class
    always had: one sweep is not evidence of a strand."""
    card = _card(labels=labels, minutes_stale=2)
    flagged, comment, add_label = _run_watchdog([card])
    assert flagged == set()
    comment.assert_not_called()
    add_label.assert_not_called()


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


def test_planning_cards_are_not_this_sweeps_business(monkeypatch):
    """DRE-2736: the no-run class must never judge a Planning card — a card in
    Planning owes a classification, not a run receipt. The DRE-1978 shape (an
    epic parked in Planning for seven days) is still surfaced, by Planning's
    own rule: tests/test_planning_lane_strand.py."""
    assert "Planning" not in reconcile.WATCHDOG_LANES
    seen = []
    monkeypatch.setattr(reconcile, "flag_stalled_planning", lambda: seen.append(1) or set())
    card = _card(
        state="Planning",
        labels=("repo:agent-bureau", "agent:planner"),
        minutes_stale=7 * 24 * 60,
    )
    flagged, comment, add_label = _run_watchdog([card], bodies=[])
    assert flagged == set()
    comment.assert_not_called()
    add_label.assert_not_called()
    assert seen == [1], "the Planning lane must still be swept, by its own rule"


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
# DRE-2260: a stale sweep must never park live work
#
# THE BUG (live incident, 2026-08-04): atlas's and deltasolv's sweeps ride the
# v4 release tag, whose routing snapshot predates portico's onboarding
# (DRE-2086), so both computed routable=False for every portico card and the
# NO-ROUTE branch parked nine of them needs-human — including DRE-2180, which
# an engineer agent was actively building (🧠 + ⏳ receipts on the card) and
# which then sat five hours on a conflicted PR because HOLD_LABEL makes
# unstick_conflicts skip the card silently. Two defects: the proof-of-life
# guard only covered the NO-RUN class, and a pinned sweep was allowed to
# adjudicate a slug it had never heard of.
# --------------------------------------------------------------------------
def test_no_route_card_with_run_receipt_never_flagged():
    """Proof-of-life must cover the NO-ROUTE class too: the repo is absent
    from this sweep's VALID_SLUGS, but a 🧠 receipt says an agent IS building
    — flagging would park live work and disable the conflict backstop."""
    card = _card(labels=("repo:ghost-product",), minutes_stale=999)
    flagged, comment, add_label = _run_watchdog(
        [card],
        bodies=["🧠 model-attempt: claude-fable-5 — engineer agent starting."],
    )
    assert flagged == set()
    comment.assert_not_called()
    add_label.assert_not_called()


def test_slug_onboarded_after_this_snapshot_left_alone(monkeypatch):
    """A sweep only makes a NO-ROUTE claim about a slug it can actually
    adjudicate: portico is missing from THIS sweep's pinned snapshot but
    present in the canonical @main map — onboarded after the pin, not off
    the rail. Left alone entirely."""
    monkeypatch.setattr(
        reconcile, "live_rail_slugs", lambda: _LIVE_SNAPSHOT | {"portico"}
    )
    card = _card(labels=("repo:portico",), minutes_stale=999)
    flagged, comment, add_label = _run_watchdog([card], bodies=[])
    assert flagged == set()
    comment.assert_not_called()
    add_label.assert_not_called()


def test_unreadable_live_snapshot_defers_no_route(monkeypatch):
    """When the canonical map can't be read, the sweep cannot tell 'dead
    route' from 'stale pin' — it must defer to a later sweep (15 minutes
    away), never convert a transient read failure into a permanent false
    park."""
    monkeypatch.setattr(reconcile, "live_rail_slugs", lambda: None)
    card = _card(labels=("repo:ghost-product",), minutes_stale=999)
    flagged, comment, add_label = _run_watchdog([card], bodies=[])
    assert flagged == set()
    comment.assert_not_called()
    add_label.assert_not_called()


def test_label_less_card_needs_no_live_snapshot(monkeypatch):
    """A card with NO repo label can never route however fresh any snapshot
    is — the unreadable-map deferral must not swallow this class."""
    monkeypatch.setattr(reconcile, "live_rail_slugs", lambda: None)
    card = _card(labels=(), minutes_stale=999)
    flagged, comment, _ = _run_watchdog([card], bodies=[])
    assert flagged == {"DRE-1978"}
    assert "hand-built" in comment.call_args.args[1]


def test_no_route_reason_names_the_snapshot_read():
    """When NO-ROUTE does fire, the comment names the slug set this sweep
    was reading, so a stale pin is diagnosable from the card itself."""
    card = _card(labels=("repo:ghost-product",), minutes_stale=999)
    _, comment, _ = _run_watchdog([card], bodies=[])
    body = comment.call_args.args[1]
    for slug in ("agent-bureau", "atlas", "bureau-pipeline"):
        assert slug in body, f"reason must name the snapshot ({slug} missing)"


def test_dre_2180_regression_stale_sweep_never_parks_a_live_build():
    """The incident, byte-for-byte: this sweep's slug set has no `portico`
    (the autouse pins — snapshot AND fallback both predate the onboarding),
    the card is In Progress, a 🧠 model-attempt and a ⏳ phase receipt are
    already posted, and the sweep arrives seconds later. Expected: no flag,
    no comment, no label."""
    card = _card(
        identifier="DRE-2180",
        state="In Progress",
        labels=("repo:portico",),
        minutes_stale=0.3,
    )
    flagged, comment, add_label = _run_watchdog(
        [card],
        bodies=[
            "🧠 model-attempt: claude-fable-5 — engineer agent starting. "
            "Run: https://github.com/dreadnought-foundry/portico/actions/runs/222",
            "⏳ 1/5 spec read, plan formed",
        ],
    )
    assert flagged == set()
    comment.assert_not_called()
    add_label.assert_not_called()


def test_live_rail_slugs_reads_the_canonical_snapshot(monkeypatch):
    """live_rail_slugs() fetches config/repo-map.json from bureau-pipeline
    @main (the published mirror of the relay's SSM map) and returns its slug
    keys, lowercased."""
    seen = []

    def fake_gh(*args):
        seen.append(args)
        return '{"portico": "dreadnought-foundry/portico", "Atlas": "EveryBite/atlas"}'

    monkeypatch.setattr(reconcile, "gh", fake_gh)
    assert _REAL_LIVE_RAIL_SLUGS() == frozenset({"portico", "atlas"})
    joined = " ".join(seen[0])
    assert "dreadnought-foundry/bureau-pipeline" in joined
    assert "config/repo-map.json" in joined


@pytest.mark.parametrize("raw", ["", "not json", "[]", "{}"])
def test_live_rail_slugs_unreadable_is_none(monkeypatch, raw):
    """Empty/garbage/non-dict answers all mean 'could not read' — None, the
    defer signal, never an empty slug set (which would flag everything)."""
    monkeypatch.setattr(reconcile, "gh", lambda *a: raw)
    assert _REAL_LIVE_RAIL_SLUGS() is None


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
# lane visibility: the watchdog sweeps its own lanes and Planning separately;
# the nudge loop is unchanged
# --------------------------------------------------------------------------
def test_active_cards_takes_a_states_filter():
    """Each lane set is its own query: the watchdog lanes (DRE-2736: Planning
    is NOT one of them), Planning under its own rule, and the nudge loop's
    default — byte-identical to the pre-DRE-1993 sweep (no Planning in the
    nudge loop, no Planning cards counted against the WIP cap)."""
    seen = []

    def spy_gql(query, variables=None):
        seen.append(variables)
        return {"issues": {"nodes": []}}

    with patch.object(reconcile.linear_ops, "gql", side_effect=spy_gql):
        reconcile.active_cards(reconcile.WATCHDOG_LANES)
        reconcile.active_cards(reconcile.PLANNING_LANE)
        reconcile.active_cards()
    assert seen[0] == {"states": ["Todo", "In Progress"]}
    assert seen[1] == {"states": ["Planning"]}
    assert seen[2] == {"states": ["Todo", "In Progress", "In Review"]}


# --------------------------------------------------------------------------
# main() wiring: full sweep only, and a flag this sweep suppresses the nudge
# --------------------------------------------------------------------------
def _full_sweep_mocks(extra=None):
    m = {
        "unstick_conflicts": MagicMock(),
        "retrigger_dead_heads": MagicMock(),
        "check_dependabot_capacity": MagicMock(),
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
