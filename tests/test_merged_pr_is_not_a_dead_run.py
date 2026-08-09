"""RED-first tests: a card whose PR MERGED is never a dead run (DRE-2316).

THE BUG (live, 2026-08-08, DRE-2316 on bureau-pipeline). PR #137 merged at
22:22:19. Ten seconds later, at 22:22:29, the agent-task Report step posted

    "🪦 dead-run-requeue: agent died with no PR and no blocker note"

and requeued the card to Todo. A second agent was dispatched onto work that had
already shipped; it correctly found nothing to do and wrote a blocker, which
parked the card in Backlog. A whole agent run burned, and finished work looked
stalled.

WHY the step saw "no PR": it asked

    gh pr list --head "$BRANCH" --json url

and `gh pr list` defaults to `--state open`. The PR existed — it had just
MERGED — so it was invisible to the only question the death check asked. The
reconcile sweep already learned this (`pr_for` passes `--state all`); the
run's own Report step never did.

FIX UNDER TEST — ONE shared predicate, `scripts/card_pr.py`, instead of a
lookup re-typed at each call site:

  * `has_work_pr(pr)` — a PR counts as work when it is OPEN **or** MERGED.
  * `find(card, branch=...)` — always `--state all`, always confirmed against a
    \\b-anchored card match on the head ref, so the DRE-1343 (empty --head
    returns a stranger's PR) and DRE-2025 (DRE-142 matches agent/DRE-1428-*)
    attribution bugs cannot come back through the new seam.
  * a READ FAILURE is not "no PR" (the DRE-2034 lesson): the CLI exits 3 and
    the caller defers instead of counting a death.
  * every remaining `gh pr list` in the repo names its `--state` explicitly, so
    the next call site someone adds cannot inherit the default-open trap
    silently.

Run: cd bureau-pipeline && python3 -m pytest tests/ -v
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import card_pr  # noqa: E402
import dead_run  # noqa: E402
import reconcile  # noqa: E402

CARD = "DRE-2316"
BRANCH = f"agent/{CARD}-dead-run-terminal-race"
MERGED_PR = {
    "number": 137,
    "url": "https://github.com/dreadnought-foundry/bureau-pipeline/pull/137",
    "headRefName": BRANCH,
    "state": "MERGED",
}
OPEN_PR = dict(MERGED_PR, number=138, state="OPEN")
CLOSED_PR = dict(MERGED_PR, number=139, state="CLOSED")


class _FakeGh:
    """Records every gh argument vector and answers from a canned table."""

    def __init__(self, by_head=None, by_search=None, fail=False):
        self.by_head = by_head if by_head is not None else []
        self.by_search = by_search if by_search is not None else []
        self.calls = []
        self.fail = fail

    def __call__(self, args):
        self.calls.append(list(args))
        if self.fail:
            raise card_pr.PrLookupError("gh: HTTP 403 rate limit exceeded")
        if "--search" in args:
            return json.dumps(self.by_search)
        return json.dumps(self.by_head)


# --------------------------------------------------------------------------
# the predicate itself
# --------------------------------------------------------------------------
def test_a_merged_pr_counts_as_work():
    """THE load-bearing assertion. `--state open` semantics answer False here,
    and that False is what requeued DRE-2316 onto a second agent."""
    assert card_pr.has_work_pr(MERGED_PR) is True


def test_an_open_pr_counts_as_work():
    assert card_pr.has_work_pr(OPEN_PR) is True


def test_no_pr_is_not_work():
    assert card_pr.has_work_pr(None) is False


def test_a_closed_unmerged_pr_is_not_work():
    """Unchanged behaviour: an abandoned PR leaves the card requeueable — the
    reconcile In QA cap (DRE-2034) is built on exactly that."""
    assert card_pr.has_work_pr(CLOSED_PR) is False


# --------------------------------------------------------------------------
# lookup: state all, anchored attribution, newest wins
# --------------------------------------------------------------------------
def test_lookup_finds_a_merged_pr():
    gh = _FakeGh(by_head=[MERGED_PR])
    found = card_pr.find(CARD, branch=BRANCH, repo="o/r", run=gh)
    assert found is not None and found["number"] == 137
    assert card_pr.has_work_pr(found)


def test_every_lookup_query_asks_for_all_states():
    gh = _FakeGh(by_head=[], by_search=[MERGED_PR])
    card_pr.find(CARD, branch=BRANCH, repo="o/r", run=gh)
    assert gh.calls, "the lookup must actually query gh"
    for args in gh.calls:
        assert "--state" in args and args[args.index("--state") + 1] == "all", (
            f"a PR-existence query that omits --state all cannot see a merged PR: {args}"
        )


def test_lookup_falls_back_to_the_card_search_when_the_branch_ref_is_gone():
    """A merge deletes the head branch, so `git branch -r` can stop resolving
    it. The card search (head:agent/DRE-N) still finds the merged PR."""
    gh = _FakeGh(by_head=[], by_search=[MERGED_PR])
    found = card_pr.find(CARD, branch="", repo="o/r", run=gh)
    assert found is not None and found["number"] == 137


def test_lookup_never_attributes_another_cards_pr():
    """DRE-1343: `gh pr list --head ""` returns the repo's newest open PR, and
    the step claimed it. The anchored confirm makes that impossible here."""
    stranger = dict(OPEN_PR, headRefName="agent/DRE-1366-something-else")
    gh = _FakeGh(by_head=[stranger], by_search=[stranger])
    assert card_pr.find(CARD, branch="", repo="o/r", run=gh) is None


def test_lookup_is_anchored_against_the_longer_card_id():
    """DRE-2025: DRE-142 is a prefix of DRE-1428."""
    longer = dict(OPEN_PR, headRefName="agent/DRE-1428-pipeline-ref-threading")
    gh = _FakeGh(by_head=[longer], by_search=[longer])
    assert card_pr.find("DRE-142", branch="", repo="o/r", run=gh) is None


def test_newest_attempt_wins():
    """An older merged PR must not shadow a newer open one (reconcile's rule,
    now shared)."""
    gh = _FakeGh(by_head=[MERGED_PR, OPEN_PR])
    found = card_pr.find(CARD, branch=BRANCH, repo="o/r", run=gh)
    assert found["number"] == 138


def test_branch_only_lookup_needs_an_exact_head_match():
    """red-main-repair's branches carry no card id, so the card anchor cannot
    apply — the head ref must match exactly instead of loosely."""
    repair = dict(OPEN_PR, headRefName="repair/red-main-9911")
    gh = _FakeGh(by_head=[repair])
    assert card_pr.find(branch="repair/red-main-9911", repo="o/r", run=gh) is not None
    assert card_pr.find(branch="repair/red-main-1", repo="o/r", run=gh) is None


def test_a_read_failure_raises_instead_of_reading_as_no_pr():
    """DRE-2034: a 403 parsed as "no PR" is how healthy cards got yanked."""
    gh = _FakeGh(fail=True)
    with pytest.raises(card_pr.PrLookupError):
        card_pr.find(CARD, branch=BRANCH, repo="o/r", run=gh)


# --------------------------------------------------------------------------
# the CLI the workflows call
# --------------------------------------------------------------------------
def _cli(*args, run=None):
    """Invoke card_pr.main() with a stubbed gh and capture stdout + exit code."""
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with patch.object(card_pr, "_gh_json", side_effect=run or _FakeGh()):
        with redirect_stdout(buf):
            rc = card_pr.main(list(args))
    return rc, buf.getvalue().strip()


def test_cli_prints_state_and_url_for_a_merged_pr():
    rc, out = _cli("find", CARD, "--branch", BRANCH, run=_FakeGh(by_head=[MERGED_PR]))
    assert rc == 0
    assert out.split("\t") == ["MERGED", MERGED_PR["url"]]


def test_cli_prints_nothing_when_there_is_genuinely_no_pr():
    rc, out = _cli("find", CARD, "--branch", BRANCH, run=_FakeGh())
    assert rc == 0
    assert out == ""


def test_cli_exits_3_when_the_answer_is_unreadable():
    """Distinct from "no PR": the caller must be able to tell them apart."""
    rc, out = _cli("find", CARD, "--branch", BRANCH, run=_FakeGh(fail=True))
    assert rc == 3
    assert out == ""


# --------------------------------------------------------------------------
# wiring: agent-task.yml asks the shared predicate, and a merged PR never
# reaches the dead-run branch
# --------------------------------------------------------------------------
WORKFLOWS = ROOT / ".github" / "workflows"
AGENT_TASK = WORKFLOWS / "agent-task.yml"


def report_step() -> str:
    src = AGENT_TASK.read_text()
    m = re.search(r"name:\s*Report result to Linear(.*?)(?:\n      - name:|\Z)", src, re.S)
    assert m, "'Report result to Linear' step not found"
    return m.group(1)


def report_code() -> str:
    """The report step with its `#` comment lines stripped — the comments
    quote the old broken command on purpose (that history is the point)."""
    return "\n".join(
        line for line in report_step().splitlines() if not line.lstrip().startswith("#")
    )


def test_report_step_uses_the_shared_predicate():
    assert "card_pr.py" in report_step()


def test_report_step_no_longer_calls_gh_pr_list_directly():
    assert "gh pr list" not in report_code()


def test_report_step_has_a_merged_branch_that_never_declares_a_death():
    step = report_step()
    assert "MERGED" in step, "the report step must recognise a merged PR"
    merged_branch = re.search(
        r'if \[ "\$PR_STATE" = "MERGED" \]; then(.*?)\n          elif ', step, re.S
    )
    assert merged_branch, "no MERGED branch in the report step"
    body = merged_branch.group(1)
    assert "dead_run.py" not in body
    assert dead_run.DEAD_TAG not in body
    assert '"Todo"' not in body, "a merged card must never be requeued to Todo"


def test_report_step_defers_when_the_pr_state_is_unreadable():
    step = report_step()
    assert "UNREADABLE" in step
    unreadable = re.search(
        r'elif \[ "\$PR_STATE" = "UNREADABLE" \]; then(.*?)\n          else', step, re.S
    )
    assert unreadable, "no unreadable-lookup branch in the report step"
    body = unreadable.group(1)
    assert "dead_run.py" not in body
    assert dead_run.DEAD_TAG not in body


def test_the_gate_step_treats_an_unreadable_lookup_as_evidence():
    """check_agent_result fails the job on "no branch, no PR, no note" — an
    unreadable lookup must not be allowed to produce that verdict."""
    src = AGENT_TASK.read_text()
    gate = re.search(r"name: Gate on agent result(.*?)\n      - name:", src, re.S)
    assert gate, "gate step not found"
    assert "card_pr.py" in gate.group(1)
    assert "unreadable" in gate.group(1).lower()


# --------------------------------------------------------------------------
# enumeration guard: no call site may inherit gh's default --state open
# --------------------------------------------------------------------------
def test_no_workflow_runs_gh_pr_list_without_naming_its_state():
    offenders = []
    for wf in sorted(WORKFLOWS.glob("*.yml")):
        for line in wf.read_text().splitlines():
            if "gh pr list" not in line or line.lstrip().startswith("#"):
                continue
            if "--state" not in line:
                offenders.append(f"{wf.name}: {line.strip()}")
    assert not offenders, (
        "`gh pr list` defaults to --state open, which cannot see a merged PR "
        "(DRE-2316). Name the state explicitly or call card_pr.py:\n"
        + "\n".join(offenders)
    )


def test_no_script_lists_prs_without_naming_its_state():
    offenders = []
    for script in sorted((ROOT / "scripts").glob("*.py")):
        src = script.read_text()
        for m in re.finditer(r'"pr",\s*"list"', src):
            window = src[m.start(): m.start() + 400]
            if '"--state"' not in window:
                offenders.append(f"{script.name}:{src[:m.start()].count(chr(10)) + 1}")
    assert not offenders, (
        "a PR list built without an explicit --state inherits gh's default "
        f"(open only) — DRE-2316: {offenders}"
    )


def test_reconcile_shares_the_one_predicate():
    assert reconcile.card_pr is card_pr
    assert reconcile.pr_for.__module__ == "reconcile"


# --------------------------------------------------------------------------
# the sweep agrees: In Progress with a MERGED PR closes the card, never
# requeues it
# --------------------------------------------------------------------------
def _card(state):
    return {
        "id": "uuid",
        "identifier": CARD,
        "title": "raced card",
        "description": "**Repo:** bureau-pipeline\nwork",
        "state": {"name": state},
        "labels": {"nodes": []},
        "updatedAt": "2026-08-08T00:00:00Z",
    }


@pytest.fixture(autouse=True)
def _clean_failure_state(monkeypatch):
    monkeypatch.setattr(reconcile, "REPO_SLUG", "bureau-pipeline")
    reconcile._write_failures.clear()
    getattr(reconcile, "_read_failures", []).clear()
    yield
    reconcile._write_failures.clear()
    getattr(reconcile, "_read_failures", []).clear()


@pytest.mark.parametrize("state", ["In Progress", "In QA"])
def test_sweep_never_requeues_a_card_whose_pr_merged(state):
    mocks = {
        "unstick_conflicts": MagicMock(),
        "retrigger_dead_heads": MagicMock(),
        "check_dependabot_capacity": MagicMock(),
        "fix_approved_but_red": MagicMock(),
        "flag_no_checks_prs": MagicMock(),
        "review_dependabot_prs": MagicMock(),
        "recover_crashed_reviews": MagicMock(),
        "retry_dead_fix_runs": MagicMock(),
        "close_finished_epics": MagicMock(),
        "promote_ready": MagicMock(return_value=0),
        "age_minutes": MagicMock(return_value=999),
        "pr_for": MagicMock(return_value=MERGED_PR),
        "agent_run_alive": MagicMock(return_value=False),
        "redispatch": MagicMock(return_value=True),
        "flag_stranded": MagicMock(return_value=set()),
        "active_cards": MagicMock(return_value=[_card(state)]),
    }
    with patch.multiple(reconcile, **mocks), patch.object(
        reconcile.linear_ops, "count_comments", return_value=0
    ), patch.object(reconcile.linear_ops, "add_label"), patch.object(
        reconcile.linear_ops, "cmd_state"
    ) as cmd_state, patch.object(
        reconcile.linear_ops, "cmd_comment"
    ) as cmd_comment:
        reconcile.main()
    moves = [c.args[1] for c in cmd_state.call_args_list]
    assert moves == ["Done"], f"a merged PR closes the card; got {moves}"
    bodies = " ".join(str(c.args[1]) for c in cmd_comment.call_args_list)
    assert dead_run.DEAD_TAG not in bodies


def test_card_pr_module_is_executable_as_a_cli():
    """The workflows shell out to it; a syntax/usage break must be loud."""
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "card_pr.py")],
        capture_output=True, text=True,
    )
    assert proc.returncode == 2
    assert "usage" in (proc.stdout + proc.stderr).lower()
