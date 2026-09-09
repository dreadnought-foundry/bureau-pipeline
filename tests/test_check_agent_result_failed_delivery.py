"""RED-first tests: a dead PR check never reads as "not dead" (DRE-3262).

THE INCIDENT'S SECOND HALF (2026-09-06, agent-bureau run 34045232203, DRE-3165).
The rescue push was refused with 400 on both of its own fresh mints, so no
branch and no pull request existed. The SAME expired token then made the Report
step's PR-state read a 401, and that read got the last word:

    ⚠️ Could not read this card's PR state from GitHub (API error), so this run
    is NOT being recorded as a dead agent — the reconcile sweep will re-check.

Every sentence of it is defensible on its own (DRE-2034: an unreadable answer is
NOT "no PR") and the conclusion was still false in the one way that mattered —
the run HAD failed to deliver, and the pipeline knew it from the disk. So
nothing re-dispatched, nothing said the rescue had failed, and the card sat In
Progress with no PR and no run until a person went looking.

What is pinned here:

  * `check_agent_result.is_failed_delivery()` — with `work_on_runner=true` and
    a push GitHub did not accept, the run IS a failed delivery, whatever the PR
    read answers. A FACT from the runner's disk outranks a failed lookup.
  * the Report step reaching that answer BEFORE the unreadable-PR branch, so
    the failed read cannot be the last word.
  * the reconcile sweep's re-check reading the ARTIFACT FACT off the card —
    the `rescue-push-failed` marker naming the artifact and the run — instead of
    concluding "dead agent, requeue" from an absent pull request. A requeue
    there rebuilds from nothing the work the artifact is holding.

Run: cd bureau-pipeline && python3 -m pytest tests/test_check_agent_result_failed_delivery.py -v
"""

from __future__ import annotations

import contextlib
import os
import re
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")
os.environ.setdefault("GH_TOKEN", "x")

import check_agent_result  # noqa: E402
import deliver_rescue  # noqa: E402
import reconcile  # noqa: E402

AGENT_TASK = ROOT / ".github" / "workflows" / "agent-task.yml"
CHECK = ROOT / "scripts" / "check_agent_result.py"

CARD = "DRE-3165"
RUN_ID = "34045232203"
ARTIFACT = "rescue-DRE-3165.patch"


# --------------------------------------------------------------------------
# 1. the predicate: the disk outranks the lookup
# --------------------------------------------------------------------------
def test_work_on_the_runner_and_a_400_is_a_failed_delivery():
    assert check_agent_result.is_failed_delivery(
        work_on_runner=True, push_status="400", pushed=False
    ) is True


def test_a_refusal_with_no_readable_status_is_still_a_failed_delivery():
    """`http_status` answers "" for a message it cannot name — an unnamed
    refusal is not a delivery."""
    assert check_agent_result.is_failed_delivery(
        work_on_runner=True, push_status="", pushed=False
    ) is True


def test_a_delivered_push_is_not_a_failed_delivery():
    """`local_work` stays true after a SUCCESSFUL rescue push — it is computed
    before the push — so the predicate must read the push's own outcome or
    every rescued run reports a failure."""
    assert check_agent_result.is_failed_delivery(
        work_on_runner=True, push_status="", pushed=True
    ) is False


def test_a_status_of_200_is_a_delivery():
    assert check_agent_result.is_failed_delivery(
        work_on_runner=True, push_status="200", pushed=False
    ) is False


def test_no_work_on_the_runner_is_never_a_failed_delivery():
    """Every ordinary run: the agent pushed its own work and this whole path
    must be invisible to it."""
    for status in ("", "400", "200"):
        assert check_agent_result.is_failed_delivery(
            work_on_runner=False, push_status=status, pushed=False
        ) is False


@pytest.mark.parametrize(
    "work,status,pushed,expected",
    [
        ("true", "400", "false", "failed"),
        ("true", "", "true", "delivered"),
        ("false", "400", "false", "delivered"),
        ("", "", "", "delivered"),
    ],
)
def test_the_cli_answers_the_shell_in_one_word(work, status, pushed, expected):
    """The form the workflow calls: a shell branch never re-derives this."""
    out = subprocess.run(
        [sys.executable, str(CHECK), "delivery", "--work-on-runner", work,
         "--push-status", status, "--pushed", pushed],
        capture_output=True, text=True,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == expected


def test_the_classifier_still_calls_it_a_credential_expiry():
    """DRE-3043's class is unchanged — this card adds a second question about
    the same fact, it does not re-answer the first."""
    assert check_agent_result.classify_death(
        None, work_on_runner=True
    ) == check_agent_result.DEATH_CREDENTIAL_EXPIRY


# --------------------------------------------------------------------------
# 2. the workflow: the failed read is not the last word
# --------------------------------------------------------------------------
def report_step() -> str:
    src = AGENT_TASK.read_text()
    m = re.search(
        r"name:\s*Report result to Linear(.*?)(?:\n      - name:|\Z)", src, re.S
    )
    assert m, "'Report result to Linear' step not found"
    return "\n".join(
        line for line in m.group(1).splitlines()
        if not line.lstrip().startswith("#")
    )


def test_the_step_asks_the_shared_predicate_rather_than_testing_it_inline():
    assert "check_agent_result.py delivery" in report_step()


def test_the_failed_delivery_branch_precedes_the_unreadable_branch():
    step = report_step()
    assert step.index("FAILED_DELIVERY") < step.index('"$PR_STATE" = "UNREADABLE"')


def test_the_gate_step_is_told_the_work_is_on_the_runner():
    """`check_agent_result.py <exec> <branch> <pr> <blocker>` fails the job on
    "no branch, no PR, no note". A failed delivery has no branch and no PR by
    definition, and it is not a silent death — the medic must not be summoned
    to re-run a run that did the work."""
    src = AGENT_TASK.read_text()
    gate = re.search(r"name: Gate on agent result(.*?)\n      - name:", src, re.S)
    assert gate, "gate step not found"
    assert "rescue.outputs.local_work" in gate.group(1), (
        "the gate must know the work is on the runner"
    )


# --------------------------------------------------------------------------
# 3. the sweep's re-check reads the artifact fact
# --------------------------------------------------------------------------
def _iso(minutes_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat().replace(
        "+00:00", "Z"
    )


MARKER = deliver_rescue.announcement(
    CARD, artifact=ARTIFACT, run_id=RUN_ID, status="400", mints=2
)


def _card(bodies=(), state="In Progress", identifier=CARD):
    return {
        "id": f"uuid-{identifier}",
        "identifier": identifier,
        "title": "console release train",
        "description": "**Repo:** agent-bureau",
        "updatedAt": _iso(999.0),
        "state": {"name": state},
        "labels": {"nodes": [{"name": "repo:agent-bureau"}]},
        "comments": {"nodes": [
            {"body": b, "createdAt": _iso(10)} for b in reversed(list(bodies))
        ]},
    }


def _sweep(cards, dispatch_ok=True, prior=0):
    """main() with every unrelated backstop stubbed, so these tests see only
    the In-Progress-no-PR branch. Mirrors tests/test_hand_built_not_stranded.py."""
    reconcile._write_failures.clear()
    mocks = {
        "unstick_conflicts": mock.MagicMock(),
        "retrigger_dead_heads": mock.MagicMock(),
        "flag_no_checks_prs": mock.MagicMock(),
        "flag_unowned_prs": mock.MagicMock(),
        "flag_unlanded_work": mock.MagicMock(),
        "fix_approved_but_red": mock.MagicMock(),
        "retry_dead_fix_runs": mock.MagicMock(),
        "restart_answered_blockers": mock.MagicMock(),
        "review_dependabot_prs": mock.MagicMock(),
        "recover_crashed_reviews": mock.MagicMock(),
        "check_dependabot_capacity": mock.MagicMock(),
        "close_finished_epics": mock.MagicMock(),
        "promote_ready": mock.MagicMock(return_value=0),
        "flag_stranded": mock.MagicMock(return_value=set()),
        "escalate_aged_intake": mock.MagicMock(return_value=set()),
        "report_break_glass": mock.MagicMock(),
        "report_epic_growth": mock.MagicMock(),
        "report_fix_concurrency": mock.MagicMock(),
        "report_evicted_fix_runs": mock.MagicMock(),
        "recover_limit_deaths": mock.MagicMock(),
        "drain_retiring_lanes": mock.MagicMock(),
        "refresh_stale_merge_refs": mock.MagicMock(),
        "active_cards": mock.MagicMock(return_value=list(cards)),
        "pr_for": mock.MagicMock(return_value=None),
        "agent_run_alive": mock.MagicMock(return_value=False),
        "redispatch": mock.MagicMock(return_value=True),
        "gh_dispatch": mock.MagicMock(
            side_effect=None if dispatch_ok
            else reconcile.ReconcileWriteError("dispatch refused")
        ),
    }
    with mock.patch.multiple(reconcile, **mocks), mock.patch.object(
        reconcile.linear_ops, "cmd_state"
    ) as cmd_state, mock.patch.object(
        reconcile.linear_ops, "cmd_comment"
    ) as cmd_comment, mock.patch.object(
        reconcile.linear_ops, "add_label"
    ) as add_label, mock.patch.object(
        reconcile.linear_ops, "cmd_advance"
    ), mock.patch.object(
        reconcile.linear_ops, "count_comments", return_value=prior
    ), mock.patch.object(reconcile.linear_ops, "open_pass"):
        # A refused dispatch is a WRITE FAILURE and the sweep exits nonzero for
        # the medic (DRE-1254) — the exit is the point of that test, not a
        # reason it cannot assert what the sweep did on its way out.
        with contextlib.suppress(SystemExit):
            reconcile.main()
    reconcile._write_failures.clear()
    return SimpleNamespace(
        cmd_state=cmd_state, cmd_comment=cmd_comment, add_label=add_label,
        dispatch=mocks["gh_dispatch"],
    )


def test_the_sweep_dispatches_the_delivery_instead_of_rebuilding():
    """The card's own comment says where the work is. Requeueing to Todo throws
    it away and pays for it twice."""
    s = _sweep([_card(bodies=["⏳ 5/5 branch ready", MARKER])])
    s.cmd_state.assert_not_called()
    assert s.dispatch.called, "the delivery must be re-dispatched off the marker"
    argv = " ".join(str(a) for a in s.dispatch.call_args.args)
    assert f"run_id={RUN_ID}" in argv
    assert deliver_rescue.delivery_workflow(reconcile.REPO) in argv


def test_the_sweep_says_so_on_the_card():
    s = _sweep([_card(bodies=[MARKER])])
    bodies = [c.args[1] for c in s.cmd_comment.call_args_list]
    assert any(deliver_rescue.DISPATCH_TAG in b for b in bodies), bodies
    assert any(ARTIFACT in b for b in bodies), bodies


def test_a_card_with_no_marker_is_requeued_exactly_as_before():
    """The control. This path is the dead-run requeue and it must still work —
    DRE-2023's whole point is that a genuinely lost run gets another go."""
    s = _sweep([_card(bodies=["⏳ 1/5 plan formed"])])
    s.cmd_state.assert_called_once()
    assert s.cmd_state.call_args.args[1] == "Todo"
    s.dispatch.assert_not_called()


def test_a_delivered_card_is_not_dispatched_again():
    s = _sweep([_card(bodies=[
        MARKER,
        f"🚚 {deliver_rescue.DELIVERED_TAG}: run {RUN_ID}'s artifact {ARTIFACT} "
        f"is now https://github.com/o/r/pull/9",
    ])])
    s.dispatch.assert_not_called()


def test_a_delivery_that_already_failed_once_falls_back_to_the_requeue():
    """Bounded, like every dispatch at a vendor boundary (DRE-1921): if the
    follow-up ran and the card still has no PR, rebuilding is the only remedy
    left and the sweep must not dispatch forever."""
    s = _sweep([_card(bodies=[MARKER])], prior=1)
    s.dispatch.assert_not_called()
    s.cmd_state.assert_called_once()
    assert s.cmd_state.call_args.args[1] == "Todo"


def test_a_refused_dispatch_falls_back_to_the_requeue():
    """A comment claiming a re-trigger that 403'd is the DRE-1254 false-receipt
    class — the sweep must fall through to the behaviour it had."""
    s = _sweep([_card(bodies=[MARKER])], dispatch_ok=False)
    s.cmd_state.assert_called_once()
    assert s.cmd_state.call_args.args[1] == "Todo"
