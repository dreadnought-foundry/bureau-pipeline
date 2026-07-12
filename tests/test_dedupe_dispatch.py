"""RED-first tests: a duplicate agent-execute dispatch must SKIP, not build (DRE-2057).

THE BUG (recurring class, 5 known occurrences): a single card's Todo
transition sometimes produces TWO agent-execute dispatches ~60s apart
(webhook double-fire / relay retry). The stub's per-card concurrency group
serializes them — cancel-in-progress stays false (DRE-2032) — so the dup
doesn't die, it QUEUES and builds the card AGAIN after the first run
finishes: twin PRs, one hand-closed after the other merges. Live evidence:
DRE-2053 → runs 15:07:09Z + 15:08:07Z → PRs #102 (merged) + #103 (dup,
closed); prior twins #76, #83, #90, #97.

FIX UNDER TEST (defense at the consumer — wins fleet-wide):
  - scripts/dedupe_dispatch.py, per the merge_gate.py pattern: a pure
    decide() core the workflow calls through a thin `gate <DRE-N>` CLI.
    Skip when EITHER (a) the card's newest 🧠 model-attempt heartbeat maps
    to a run that GitHub says is still queued/in_progress and is NOT this
    run (the DRE-2032 card→run mapping), or (b) an OPEN agent PR for the
    card already exists. A re-dispatch after the PR closed/merged still
    builds (the rebuild case); unreadable answers PROCEED (fail-open — a
    missed skip is exactly the status quo, a false skip strands the card).
  - agent-task.yml runs the guard right after the card-validation gate and
    every downstream step skips on `steps.dedupe.outputs.skip == 'true'`,
    so a dup run exits clean: no branch, no PR, no dead-run requeue.

Run: cd bureau-pipeline && python3 -m pytest tests/ -v
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import dedupe_dispatch  # noqa: E402

RUN_URL = "https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/111"

HEARTBEAT = f"🧠 model-attempt: claude-opus-4-8 — engineer agent starting. Run: {RUN_URL}"


def _status(answer: str):
    """A run_status lookup double: answers .status for any run id."""
    return lambda run_id: answer


def _no_status_calls(run_id):
    raise AssertionError(f"run_status must not be consulted (asked about {run_id})")


# --------------------------------------------------------------------------
# decide() — the skip decision (existing-run / existing-PR / clean cases)
# --------------------------------------------------------------------------
def test_open_agent_pr_skips():
    """Case (b), the twin-PR vector itself: an OPEN agent PR for the card
    means the dup must not build a second one."""
    d = dedupe_dispatch.decide(
        "DRE-2053", "222", [], _no_status_calls,
        [{"number": 102, "headRefName": "agent/DRE-2053-allowed-bots-github-actions"}],
    )
    assert d.skip is True
    assert "#102" in d.reason


def test_live_run_skips():
    """Case (a): the card's newest heartbeat maps to ANOTHER run GitHub says
    is in_progress — a concurrent dup must skip."""
    d = dedupe_dispatch.decide(
        "DRE-2053", "222", [HEARTBEAT], _status("in_progress"), []
    )
    assert d.skip is True
    assert "111" in d.reason


def test_queued_run_skips():
    """Queued dups must SKIP, not run later (the card's explicit demand)."""
    d = dedupe_dispatch.decide("DRE-2053", "222", [HEARTBEAT], _status("queued"), [])
    assert d.skip is True


def test_clean_card_proceeds():
    """First dispatch: no heartbeat, no open PR → build."""
    d = dedupe_dispatch.decide("DRE-2053", "222", [], _no_status_calls, [])
    assert d.skip is False


def test_own_run_heartbeat_proceeds():
    """A job re-run reuses its run id — our OWN heartbeat is never a dup,
    and GitHub must not even be asked about it."""
    d = dedupe_dispatch.decide(
        "DRE-2053", "111", [HEARTBEAT], _no_status_calls, []
    )
    assert d.skip is False


def test_completed_run_proceeds():
    """Rebuild case: the previous attempt CONCLUDED (PR closed/merged, or a
    dead run) — a fresh Todo dispatch must still build."""
    d = dedupe_dispatch.decide(
        "DRE-2053", "222", [HEARTBEAT], _status("completed"), []
    )
    assert d.skip is False


def test_unreadable_run_status_proceeds():
    """Fail-open: an API blip ('' status) must never skip — a false skip
    strands the card; a missed skip is exactly the status quo."""
    d = dedupe_dispatch.decide("DRE-2053", "222", [HEARTBEAT], _status(""), [])
    assert d.skip is False


def test_newest_heartbeat_wins():
    """Two attempts on the card: GitHub is asked about the NEWEST attempt's
    run, not an older concluded one (mirror of reconcile.agent_run_alive)."""
    old = ("🧠 model-attempt: claude-fable-5 — engineer agent starting. "
           "Run: https://github.com/o/r/actions/runs/99")
    asked = []

    def spy(run_id):
        asked.append(run_id)
        return "in_progress"

    d = dedupe_dispatch.decide("DRE-2053", "222", [old, HEARTBEAT], spy, [])
    assert d.skip is True
    assert asked == ["111"]


def test_closed_pr_is_invisible():
    """Only OPEN PRs gate — the caller feeds `gh pr list --state open`, so a
    merged/closed twin never appears here; pin that an unrelated open PR for
    ANOTHER card does not skip this one."""
    d = dedupe_dispatch.decide(
        "DRE-2053", "222", [], _no_status_calls,
        [{"number": 104, "headRefName": "agent/DRE-2056-self-stub-dispatch-parity"}],
    )
    assert d.skip is False


def test_near_miss_identifier_does_not_match():
    """DRE-205 must not match agent/DRE-2053-* (the DRE-1034 vs DRE-10345
    class reconcile.pr_for anchors against)."""
    d = dedupe_dispatch.decide(
        "DRE-205", "222", [], _no_status_calls,
        [{"number": 102, "headRefName": "agent/DRE-2053-allowed-bots"}],
    )
    assert d.skip is False


def test_non_agent_branch_does_not_match():
    """A repair/* or other non-agent PR mentioning the card is not the
    card's agent PR."""
    d = dedupe_dispatch.decide(
        "DRE-2053", "222", [], _no_status_calls,
        [{"number": 50, "headRefName": "repair/DRE-2053-red-main"}],
    )
    assert d.skip is False


def test_heartbeat_without_run_url_proceeds():
    """Legacy heartbeat with no run URL: nothing verifiable to skip on."""
    body = "🧠 model-attempt: claude-fable-5 — engineer agent starting."
    d = dedupe_dispatch.decide("DRE-2053", "222", [body], _no_status_calls, [])
    assert d.skip is False


# --------------------------------------------------------------------------
# heartbeat-contract parity with reconcile (DRE-2032's card→run mapping)
# --------------------------------------------------------------------------
def test_heartbeat_contract_matches_reconcile():
    """dedupe_dispatch reads the SAME 🧠 heartbeat reconcile.agent_run_alive
    reads — marker or regex drift would silently blind one of them."""
    import reconcile

    assert dedupe_dispatch.RUN_MARKER == reconcile.RUN_MARKER
    assert dedupe_dispatch._RUN_ID.pattern == reconcile._RUN_ID.pattern


# --------------------------------------------------------------------------
# cmd_gate — the thin CLI: outputs, receipt, fail-open
# --------------------------------------------------------------------------
@pytest.fixture()
def _gh_output(tmp_path, monkeypatch):
    out = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setenv("GITHUB_RUN_ID", "222")
    monkeypatch.setenv("GITHUB_REPOSITORY", "dreadnought-foundry/bureau-pipeline")
    return out


def test_cmd_gate_skip_emits_output_and_receipt(_gh_output):
    """A skipped dup writes skip=true to $GITHUB_OUTPUT and logs a receipt
    comment on the card (the 'logged' half of the acceptance)."""
    with patch.object(
        dedupe_dispatch.linear_ops, "comment_bodies", return_value=[HEARTBEAT]
    ), patch.object(
        dedupe_dispatch, "_run_status", return_value="in_progress"
    ), patch.object(
        dedupe_dispatch, "_open_prs", return_value=[]
    ), patch.object(
        dedupe_dispatch.linear_ops, "cmd_comment"
    ) as receipt:
        dedupe_dispatch.cmd_gate("DRE-2053")
    assert "skip=true" in _gh_output.read_text()
    receipt.assert_called_once()
    body = receipt.call_args[0][1]
    # Machine-marker prefix: reconcile's blocker gate treats any NON-machine
    # comment as a human reply — a wrong prefix here would re-arm parked
    # blocker loops (reconcile._AGENT_COMMENT_PREFIXES).
    assert body.startswith("🤖"), "skip receipt must carry a machine marker prefix"


def test_cmd_gate_clean_emits_skip_false(_gh_output):
    with patch.object(
        dedupe_dispatch.linear_ops, "comment_bodies", return_value=[]
    ), patch.object(
        dedupe_dispatch, "_open_prs", return_value=[]
    ), patch.object(
        dedupe_dispatch.linear_ops, "cmd_comment"
    ) as receipt:
        dedupe_dispatch.cmd_gate("DRE-2053")
    assert "skip=false" in _gh_output.read_text()
    receipt.assert_not_called()


def test_cmd_gate_fails_open_on_linear_error(_gh_output):
    """An unreadable card must PROCEED (skip=false), never crash the build."""
    with patch.object(
        dedupe_dispatch.linear_ops, "comment_bodies",
        side_effect=RuntimeError("linear 500"),
    ), patch.object(
        dedupe_dispatch, "_open_prs", return_value=[]
    ):
        dedupe_dispatch.cmd_gate("DRE-2053")
    assert "skip=false" in _gh_output.read_text()


def test_cmd_gate_receipt_failure_still_skips(_gh_output):
    """Reporting must never block the guard: a failed receipt post still
    emits skip=true (the dup must not build just because Linear blipped)."""
    with patch.object(
        dedupe_dispatch.linear_ops, "comment_bodies", return_value=[]
    ), patch.object(
        dedupe_dispatch, "_open_prs",
        return_value=[{"number": 102, "headRefName": "agent/DRE-2053-x"}],
    ), patch.object(
        dedupe_dispatch.linear_ops, "cmd_comment",
        side_effect=RuntimeError("linear 500"),
    ):
        dedupe_dispatch.cmd_gate("DRE-2053")
    assert "skip=true" in _gh_output.read_text()


# --------------------------------------------------------------------------
# agent-task.yml wiring: the guard runs after the gate, and EVERY downstream
# step honors the skip — a leak in Report would dead-run-requeue the card.
# --------------------------------------------------------------------------
def _agent_task():
    return yaml.safe_load(
        (ROOT / ".github" / "workflows" / "agent-task.yml").read_text()
    )


def _steps():
    return _agent_task()["jobs"]["execute"]["steps"]


def _step(name):
    matches = [s for s in _steps() if s.get("name") == name]
    assert len(matches) == 1, f"expected exactly one {name!r} step"
    return matches[0]


def test_dedupe_step_exists_and_calls_the_script():
    step = _step("Duplicate-dispatch guard")
    assert step.get("id") == "dedupe"
    assert "dedupe_dispatch.py" in step["run"]
    assert "client_payload.identifier" in step["run"]


def test_dedupe_runs_after_gate_and_before_in_progress():
    names = [s.get("name") or "" for s in _steps()]
    gate = names.index("Card-validation gate")
    dedupe = names.index("Duplicate-dispatch guard")
    in_prog = names.index("Card → In Progress")
    assert gate < dedupe < in_prog, (
        "the guard must run after the card resolves and before any state "
        "mutation or build step"
    )


def test_dedupe_skipped_for_bounced_cards():
    assert "steps.gate.outputs.bounced" in str(_step("Duplicate-dispatch guard").get("if", ""))


@pytest.mark.parametrize(
    "name",
    [
        "Select model",
        "Assemble agent context",
        "Sanitize untrusted card text",
        "Card → In Progress",
        "Implement card",
        "Gate on agent result",
        "Report result to Linear",
    ],
)
def test_downstream_steps_honor_the_skip(name):
    """Every step after the guard must carry the skip condition. The Report
    step especially: without it a skipped dup reads as no-PR-no-blocker and
    dead_run.decide() requeues the card to Todo — a fresh dup loop."""
    cond = str(_step(name).get("if", ""))
    assert re.search(r"steps\.dedupe\.outputs\.skip\s*!=\s*'true'", cond), (
        f"step {name!r} must skip when the duplicate-dispatch guard says skip"
    )


def test_report_and_result_gate_keep_always():
    """The skip guard must not cost the death-reporting path: the always()
    that catches a crashed Implement step stays."""
    for name in ("Gate on agent result", "Report result to Linear"):
        assert "always()" in str(_step(name).get("if", ""))


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
