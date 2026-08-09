"""RED-first tests: un-parking a held card RESETS its dead-run death budget.

THE RECOVERY TRAP (found in production, 2026-08-09). The dead-run hold cap
(`REQUEUE_CAP = 2`, DRE-1403) is counted by substring-counting Linear comments:
`linear_ops.count_comments(<card>, "dead-run-requeue")` over `comments(last: 50)`.
NOTHING ever resets that count, and the HOLD comment itself
("🚨 held-for-human (dead-run-requeue cap reached)…") contains the tag, so it
counts too.

So when a human un-parks a held card — clears `needs-human`, moves it Backlog →
Todo — the card still carries every historical match. It re-holds on its very
FIRST subsequent death instead of getting a fresh set of attempts. Three good
portico cards (DRE-2308/2309/2310) landed in exactly this state: their deaths
were caused by a fleet-wide model misconfiguration, nothing about the cards was
wrong, and each now carries ~6 matching comments — a permanently exhausted
budget for work that was never given a fair attempt.

This is the same bug class the FIX loop already fixed (DRE-2018,
`fix_dead_run.consecutive_prior_deaths`: deaths are counted since the last
successful push, so a recovered outage episode does not pre-exhaust the cap for
an unrelated one later). The dead-run cap never got that treatment.

FIX UNDER TEST:
  * `dead_run.RESET_TAG` — an explicit un-park marker comment.
  * reset-aware counting in the ONE place counting lives,
    `linear_ops.count_comments(..., since=RESET_TAG)`: deaths recorded BEFORE
    the most recent reset marker no longer count.
  * `linear_ops.py unpark <DRE-N>` — the atomic operator verb: clear the hold
    label, post the reset marker, return the card to Todo.

Run: cd bureau-pipeline && python3 -m pytest tests/ -v
"""

from __future__ import annotations

import io
import os
import sys
from contextlib import redirect_stdout
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

import dead_run  # noqa: E402
import linear_ops  # noqa: E402
import reconcile  # noqa: E402


def _comments_payload(bodies):
    return {"issue": {"comments": {"nodes": [{"body": b} for b in bodies]}}}


def _death(n: int = 1) -> str:
    """A real requeue receipt (carries DEAD_TAG), as dead_run emits it."""
    return dead_run.decide(n - 1).comments[0]


def _hold() -> str:
    """The real hold comment — which ALSO contains DEAD_TAG (the trap)."""
    return dead_run.decide(dead_run.REQUEUE_CAP).comments[0]


# --------------------------------------------------------------------------
# the markers themselves: they must not be able to collide
# --------------------------------------------------------------------------
def test_reset_marker_exists_and_is_distinct_from_the_death_tag():
    assert dead_run.RESET_TAG
    assert dead_run.RESET_TAG != dead_run.DEAD_TAG


def test_marker_strings_cannot_collide_in_either_direction():
    """THE silent-breakage guard. Counting is substring-based, so if the reset
    marker CONTAINED the death tag, every reset would also register as a death
    (a card would un-park straight into a fresh death) — and if the death tag
    contained the reset marker, every death would wipe the budget it is meant
    to spend. Neither string may contain the other."""
    assert dead_run.DEAD_TAG not in dead_run.RESET_TAG, (
        f"{dead_run.RESET_TAG!r} contains the death tag {dead_run.DEAD_TAG!r} — "
        "the reset comment would count itself as a death"
    )
    assert dead_run.RESET_TAG not in dead_run.DEAD_TAG, (
        f"{dead_run.DEAD_TAG!r} contains the reset marker {dead_run.RESET_TAG!r} "
        "— every death would reset the budget it is meant to spend"
    )


def test_reset_comment_carries_the_reset_tag_and_no_death_tag():
    body = dead_run.reset_comment()
    assert dead_run.RESET_TAG in body
    assert dead_run.DEAD_TAG not in body, (
        "the reset comment must never be counted as a death"
    )


def test_reset_comment_accepts_an_operator_note():
    body = dead_run.reset_comment("fleet-wide model misconfiguration, not the card")
    assert "fleet-wide model misconfiguration" in body
    assert dead_run.DEAD_TAG not in body


def test_hold_comment_still_carries_the_death_tag():
    """Regression pin, not an endorsement: the hold comment DOES contain the
    tag (it is the third death's own receipt). That is exactly why an explicit
    reset — rather than 'stop tagging the hold' — is the fix."""
    assert dead_run.DEAD_TAG in _hold()


# --------------------------------------------------------------------------
# reset-aware counting — one definition, in linear_ops.count_comments
# --------------------------------------------------------------------------
def test_deaths_before_a_reset_are_ignored():
    bodies = [_death(1), _death(2), _hold(), dead_run.reset_comment()]
    with patch.object(linear_ops, "gql", return_value=_comments_payload(bodies)):
        n = linear_ops.count_comments(
            "DRE-2308", dead_run.DEAD_TAG, since=dead_run.RESET_TAG
        )
    assert n == 0, "an un-parked card starts from a clean budget"


def test_deaths_after_a_reset_are_counted():
    bodies = [_death(1), _hold(), dead_run.reset_comment(), _death(1), _death(2)]
    with patch.object(linear_ops, "gql", return_value=_comments_payload(bodies)):
        n = linear_ops.count_comments(
            "DRE-2308", dead_run.DEAD_TAG, since=dead_run.RESET_TAG
        )
    assert n == 2


def test_only_the_most_recent_reset_counts():
    bodies = [
        _death(1),
        dead_run.reset_comment(),
        _death(1),
        _death(2),
        dead_run.reset_comment("second un-park"),
        _death(1),
    ]
    with patch.object(linear_ops, "gql", return_value=_comments_payload(bodies)):
        n = linear_ops.count_comments(
            "DRE-2308", dead_run.DEAD_TAG, since=dead_run.RESET_TAG
        )
    assert n == 1


def test_a_card_with_no_reset_behaves_exactly_as_today():
    """NO REGRESSION: without a reset marker the answer is the plain count —
    identical with and without the `since` argument."""
    bodies = [_death(1), "🤖 PR opened", _death(2), _hold()]
    with patch.object(linear_ops, "gql", return_value=_comments_payload(bodies)):
        plain = linear_ops.count_comments("DRE-2308", dead_run.DEAD_TAG)
        with_since = linear_ops.count_comments(
            "DRE-2308", dead_run.DEAD_TAG, since=dead_run.RESET_TAG
        )
    assert plain == 3, "two requeues + the hold receipt all carry the tag"
    assert with_since == plain


def test_reset_marker_is_never_itself_counted_as_a_death():
    """Even asking for the plain (since-less) death count, a card whose only
    comment is a reset marker has died zero times."""
    bodies = [dead_run.reset_comment(), dead_run.reset_comment("again")]
    with patch.object(linear_ops, "gql", return_value=_comments_payload(bodies)):
        assert linear_ops.count_comments("DRE-2308", dead_run.DEAD_TAG) == 0
        assert (
            linear_ops.count_comments(
                "DRE-2308", dead_run.DEAD_TAG, since=dead_run.RESET_TAG
            )
            == 0
        )


def test_generic_counting_is_untouched_by_the_since_argument():
    """count_comments stays the generic helper (MERGED_NOT_CLOSED_MARKER,
    BAD_REF_TAG, …): with no `since` it is a plain substring count."""
    with patch.object(
        linear_ops, "gql", return_value=_comments_payload(["a x a", None, "x"])
    ):
        assert linear_ops.count_comments("DRE-1", "x") == 2


def test_the_last_50_window_is_unchanged():
    """The reset changes WHICH comments count, never HOW MANY are fetched."""
    seen = []

    def _spy(query, variables=None):
        seen.append(query)
        return _comments_payload([])

    with patch.object(linear_ops, "gql", side_effect=_spy):
        linear_ops.count_comments("DRE-1", dead_run.DEAD_TAG)
        linear_ops.count_comments("DRE-1", dead_run.DEAD_TAG, since=dead_run.RESET_TAG)
    assert len(seen) == 2
    for query in seen:
        assert "comments(last: 50)" in query


def test_full_recovery_restores_the_whole_budget():
    """END TO END, the DRE-2308 shape: a card carrying a full exhausted history
    plus a reset gets all three attempts back."""
    history = [_death(1), _death(2), _hold(), dead_run.reset_comment()]
    with patch.object(linear_ops, "gql", return_value=_comments_payload(history)):
        prior = linear_ops.count_comments(
            "DRE-2308", dead_run.DEAD_TAG, since=dead_run.RESET_TAG
        )
    assert dead_run.decide(prior).action == "requeue"
    assert dead_run.decide(prior + 1).action == "requeue"
    assert dead_run.decide(prior + 2).action == "hold"


# --------------------------------------------------------------------------
# CLI: count-comments --since, and the operator's `unpark` verb
# --------------------------------------------------------------------------
def test_cli_count_comments_accepts_since_flag():
    bodies = [_death(1), _hold(), dead_run.reset_comment(), _death(1)]
    with patch.object(linear_ops, "gql", return_value=_comments_payload(bodies)):
        buf = io.StringIO()
        with redirect_stdout(buf):
            linear_ops.cmd_count_comments(
                "DRE-2308", dead_run.DEAD_TAG, "--since", dead_run.RESET_TAG
            )
    assert buf.getvalue().strip() == "1"


def test_cli_count_comments_without_since_is_unchanged():
    bodies = [_death(1), _hold(), dead_run.reset_comment(), _death(1)]
    with patch.object(linear_ops, "gql", return_value=_comments_payload(bodies)):
        buf = io.StringIO()
        with redirect_stdout(buf):
            linear_ops.cmd_count_comments("DRE-2308", dead_run.DEAD_TAG)
    assert buf.getvalue().strip() == "3"


def test_unpark_is_registered_as_a_cli_verb():
    source = (ROOT / "scripts" / "linear_ops.py").read_text()
    assert '"unpark": cmd_unpark' in source, (
        "the operator needs a single command to un-park a held card"
    )
    assert "unpark <DRE-N>" in source, "document the verb in the module docstring"


def test_unpark_clears_the_label_posts_the_reset_and_returns_to_todo():
    order = []
    with patch.object(
        linear_ops, "remove_label", side_effect=lambda *a: order.append(("label", a))
    ) as remove_label, patch.object(
        linear_ops, "cmd_comment", side_effect=lambda *a: order.append(("comment", a))
    ) as cmd_comment, patch.object(
        linear_ops, "cmd_state", side_effect=lambda *a: order.append(("state", a))
    ) as cmd_state:
        linear_ops.cmd_unpark("DRE-2308")
    remove_label.assert_called_once_with("DRE-2308", dead_run.HOLD_LABEL)
    cmd_state.assert_called_once_with("DRE-2308", "Todo")
    body = cmd_comment.call_args.args[1]
    assert dead_run.RESET_TAG in body
    assert dead_run.DEAD_TAG not in body
    assert [step for step, _ in order] == ["label", "comment", "state"], (
        "the reset marker must land BEFORE the Todo move — the Todo transition "
        "re-dispatches the card, and that run reads the death count"
    )


def test_unpark_passes_an_operator_note_through():
    with patch.object(linear_ops, "remove_label"), patch.object(
        linear_ops, "cmd_state"
    ), patch.object(linear_ops, "cmd_comment") as cmd_comment:
        linear_ops.cmd_unpark("DRE-2309", "model misconfiguration, card is fine")
    assert "model misconfiguration, card is fine" in cmd_comment.call_args.args[1]


def test_unpark_does_not_use_park_semantics():
    """unpark's state move is an ORDINARY Todo transition — `--park` is only
    ever for a deliberate Backlog hold (DRE-1885)."""
    with patch.object(linear_ops, "remove_label"), patch.object(
        linear_ops, "cmd_comment"
    ), patch.object(linear_ops, "cmd_state") as cmd_state:
        linear_ops.cmd_unpark("DRE-2310")
    assert "--park" not in cmd_state.call_args.args


# --------------------------------------------------------------------------
# wiring: both reconcile death counts and the agent-task shell are reset-aware
# --------------------------------------------------------------------------
def test_reconcile_shares_the_one_death_tag_definition():
    assert reconcile.DEAD_TAG == dead_run.DEAD_TAG
    assert reconcile.RESET_TAG == dead_run.RESET_TAG


@pytest.fixture(autouse=True)
def _clean_failure_state():
    reconcile._write_failures.clear()
    getattr(reconcile, "_read_failures", []).clear()
    yield
    reconcile._write_failures.clear()
    getattr(reconcile, "_read_failures", []).clear()


def _sweep_mocks(card, extra=None):
    m = {
        "unstick_conflicts": MagicMock(),
        "retrigger_dead_heads": MagicMock(),
        "check_dependabot_capacity": MagicMock(),
        "fix_approved_but_red": MagicMock(),
        "close_finished_epics": MagicMock(),
        "promote_ready": MagicMock(return_value=0),
        "age_minutes": MagicMock(return_value=999),
        "pr_for": MagicMock(return_value=None),
        "redispatch": MagicMock(return_value=True),
        "flag_stranded": MagicMock(return_value=set()),
        "active_cards": MagicMock(return_value=[card]),
    }
    if extra:
        m.update(extra)
    return m


def _card(state):
    return {
        "identifier": "DRE-2308",
        "description": "**Repo:** agent-bureau\nwork",
        "state": {"name": state},
        "labels": {"nodes": []},
        "updatedAt": "2026-08-09T00:00:00Z",
    }


@pytest.mark.parametrize(
    ("state", "extra"),
    [
        ("In Progress", {"agent_run_alive": MagicMock(return_value=False)}),
        ("In QA", None),
    ],
)
def test_reconcile_counts_deaths_since_the_reset(state, extra):
    """BOTH sweep death-count sites must be reset-aware, or an un-parked card
    still re-holds on its first death through the reconcile path."""
    mocks = _sweep_mocks(_card(state), extra)
    with patch.multiple(reconcile, **mocks), patch.object(
        reconcile.linear_ops, "count_comments", return_value=0
    ) as count_comments, patch.object(
        reconcile.linear_ops, "add_label"
    ), patch.object(reconcile.linear_ops, "cmd_state"), patch.object(
        reconcile.linear_ops, "cmd_comment"
    ):
        reconcile.main()
    dead_calls = [
        c for c in count_comments.call_args_list if dead_run.DEAD_TAG in c.args
    ]
    assert dead_calls, f"the {state} branch must count deaths"
    for call in dead_calls:
        assert call.kwargs.get("since") == dead_run.RESET_TAG, (
            f"the {state} dead-run count ignores the budget reset"
        )


def test_agent_task_report_step_counts_deaths_since_the_reset():
    doc = yaml.safe_load((ROOT / ".github" / "workflows" / "agent-task.yml").read_text())
    steps = doc["jobs"]["execute"]["steps"]
    matches = [s for s in steps if s.get("name") == "Report result to Linear"]
    assert len(matches) == 1
    run = matches[0]["run"]
    assert "count-comments" in run
    assert f"--since \"{dead_run.RESET_TAG}\"" in run or (
        f"--since '{dead_run.RESET_TAG}'" in run
    ), "the in-run death count must skip everything before the budget reset"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
