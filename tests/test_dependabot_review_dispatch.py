"""RED-first tests for DRE-2047 — dependabot PRs get their review via
workflow_dispatch, never via their own doomed pull_request events.

THE BUG (live, 2026-07-11, this repo's PRs #93–#96, e.g. run 29168433294):
GitHub's security model hands a workflow run triggered by dependabot[bot]'s
pull_request events the DEPENDABOT secrets store — a separate store that is
EMPTY for us — plus a read-only GITHUB_TOKEN. `secrets: inherit` therefore
passes nothing, the reusable qa-review.yml fails its required-secret
validation, and every `call / review` job dies with ZERO steps executed.
No sha-bound verdict can ever exist, so the merge gate waits forever on
grouped minor/patch bumps (DRE-2039's auto-merge lane).

FIX UNDER TEST (three legs):
  1. pr-review.yml's `call` job SKIPS dependabot-actor'd pull_request runs
     (job-level `if`, evaluated before the reusable is invoked — the only
     point early enough, since the failure is required-secret validation)
     while workflow_dispatch runs are untouched. self-linear-sync.yml gets
     the same guard: dependabot also closes its own superseded PRs, the
     same empty-store death. NOT pull_request_target — no full-secrets
     context is ever attached to a dependabot-triggered event.
  2. reconcile.review_dependabot_prs(), a new PR-level sweep backstop:
     an open dependabot-authored PR whose CURRENT head lacks a sha-bound
     critic verdict gets the review workflow dispatched via
     `workflow_dispatch pr_number=N` (full secrets, reviews the PR ref) —
     bounded ONCE per head sha by a worker-bot receipt comment, so a
     crashed critic run doesn't re-dispatch every sweep; a rebase changes
     the sha and re-arms. Forged receipts (non-worker authors) are
     invisible (DRE-1998 discipline).
  3. reconcile.review_workflow() resolves the DISPATCHABLE critic stub per
     repo: product repos name it qa-review.yml; in bureau-pipeline that
     filename is the reusable itself (workflow_call-only — dispatching it
     422s), so the stub is pr-review.yml. The In QA re-review nudges use
     the same resolution.

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
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import reconcile  # noqa: E402

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
PIPELINE = "dreadnought-foundry/bureau-pipeline"

SHA = "a" * 40
OLD_SHA = "b" * 40

# getattr with a literal default: on the unfixed code the constant does not
# exist yet — the behavioral tests must go RED on missing dispatches, not
# die at collection time.
TAG = getattr(reconcile, "DEPENDABOT_DISPATCH_TAG", "dependabot-review-dispatch")


@pytest.fixture(autouse=True)
def _selfhost_repo(monkeypatch):
    monkeypatch.setattr(reconcile, "REPO", "dreadnought-foundry/bureau-pipeline")
    monkeypatch.setattr(reconcile, "REPO_SLUG", "bureau-pipeline")
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()
    yield
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()


def _pr(
    branch="dependabot/github_actions/actions-minor-patch-0f5a1b2c3d",
    author="dependabot",
    sha=SHA,
    comments=(),
    merge_state="CLEAN",
    number=93,
):
    return {
        "number": number,
        "headRefName": branch,
        "author": {"login": author},
        "headRefOid": sha,
        "mergeStateStatus": merge_state,
        "comments": list(comments),
    }


def _verdict(sha):
    """A genuine qa-bot verdict comment, sha-bound (DRE-1990 shape)."""
    return {
        "author": {"login": "agent-bureau-qa-bot"},
        "body": f"🔎 QA Critic — VERDICT: APPROVE @{sha}\n\nRoutine grouped bump.",
    }


def _receipt(sha, author="agent-bureau-bot"):
    """The sweep's own once-per-sha dispatch receipt (worker-bot authored)."""
    return {
        "author": {"login": author},
        "body": f"🔁 {TAG} @{sha}: critic dispatched via workflow_dispatch (DRE-2047)",
    }


def _run_factory(state):
    """subprocess.run stub covering exactly the gh calls this backstop makes:
    pr list (the scan), workflow run (the dispatch), pr comment (the receipt).
    """

    def fake_run(argv, **kwargs):
        assert argv[0] == "gh", f"unexpected call: {argv}"
        if argv[1] == "pr" and argv[2] == "list":
            return SimpleNamespace(
                returncode=0, stdout=json.dumps(state["prs"]), stderr=""
            )
        if argv[1] == "workflow" and argv[2] == "run":
            state["dispatches"].append(argv)
            rc = state.get("dispatch_rc", 0)
            return SimpleNamespace(
                returncode=rc, stdout="",
                stderr="HTTP 403: Resource not accessible by integration" if rc else "",
            )
        if argv[1] == "pr" and argv[2] == "comment":
            state["receipts"].append(argv)
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected gh call: {argv}")

    return fake_run


def _sweep(prs, dispatch_rc=0):
    state = {"prs": prs, "dispatches": [], "receipts": [], "dispatch_rc": dispatch_rc}
    with patch.object(reconcile.subprocess, "run", side_effect=_run_factory(state)):
        reconcile.review_dependabot_prs()
    return state


# --------------------------------------------------------------------------
# review_workflow(): the dispatchable critic stub differs in the self-host repo
# --------------------------------------------------------------------------
def test_review_workflow_resolves_per_repo(monkeypatch):
    monkeypatch.setattr(reconcile, "REPO_SLUG", "bureau-pipeline")
    assert reconcile.review_workflow() == "pr-review.yml", (
        "bureau-pipeline's qa-review.yml is the reusable (workflow_call-only, "
        "not dispatchable) — its critic stub is pr-review.yml"
    )
    monkeypatch.setattr(reconcile, "REPO_SLUG", "atlas")
    assert reconcile.review_workflow() == "qa-review.yml", (
        "product repos name their dispatchable critic stub qa-review.yml"
    )


def test_in_qa_nudge_dispatches_the_selfhost_stub():
    """main()'s In QA no-verdict nudge hardcoded qa-review.yml — a 422 in
    this repo, where that file cannot be dispatched. It must resolve through
    review_workflow()."""
    card = {
        "id": "uuid-2047",
        "identifier": "DRE-2047",
        "title": "dependabot review dispatch",
        "description": "**Repo:** bureau-pipeline\nwork",
        "state": {"name": "In QA"},
        "labels": {"nodes": [{"name": "agent:engineer"}]},
        "updatedAt": "2026-07-11T00:00:00Z",
    }
    pr = _pr(branch="agent/DRE-2047-dependabot-review-dispatch", author="agent-bureau-bot")
    mocks = {
        "unstick_conflicts": MagicMock(),
        "retrigger_dead_heads": MagicMock(),
        "fix_approved_but_red": MagicMock(),
        "retry_dead_fix_runs": MagicMock(),
        "close_finished_epics": MagicMock(),
        "promote_ready": MagicMock(return_value=0),
        "flag_stranded": MagicMock(return_value=set()),
        "age_minutes": MagicMock(return_value=999),
        "active_cards": MagicMock(return_value=[card]),
        "pr_for": MagicMock(return_value=pr),
        "_nudge": MagicMock(return_value=True),
    }
    if hasattr(reconcile, "review_dependabot_prs"):
        mocks["review_dependabot_prs"] = MagicMock()
    with patch.multiple(reconcile, **mocks), patch.object(reconcile.linear_ops, "cmd_comment"):
        reconcile.main()
    targets = [c.args[0] for c in mocks["_nudge"].call_args_list]
    assert targets == ["pr-review.yml"], (
        f"the In QA re-review nudge must dispatch this repo's critic stub "
        f"(pr-review.yml), got {targets}"
    )


# --------------------------------------------------------------------------
# review_dependabot_prs(): the sweep is the real review path for dependabot
# --------------------------------------------------------------------------
def test_dependabot_pr_with_no_verdict_gets_exactly_one_review_dispatch():
    """ACCEPTANCE: a fresh dependabot PR (no verdict, no receipt) →
    the sweep dispatches the critic stub via workflow_dispatch pr_number=N
    and posts ONE worker-bot receipt carrying the full head sha."""
    state = _sweep([_pr()])
    assert len(state["dispatches"]) == 1, "exactly one review dispatch expected"
    argv = state["dispatches"][0]
    assert "pr-review.yml" in argv, (
        f"must dispatch the self-host critic stub, got {argv}"
    )
    assert "pr_number=93" in argv
    assert len(state["receipts"]) == 1, "one once-per-sha receipt must be posted"
    body = state["receipts"][0][state["receipts"][0].index("--body") + 1]
    assert TAG in body and SHA in body, (
        "the receipt must carry the dispatch tag and the FULL head sha — "
        "that pair is the once-per-sha bound"
    )


def test_receipt_for_current_head_suppresses_redispatch():
    """A crashed critic run must not loop: the sweep dispatches once per
    head sha, keyed on its own receipt."""
    state = _sweep([_pr(comments=[_receipt(SHA)])])
    assert state["dispatches"] == [], (
        "a worker-bot receipt for the CURRENT head sha means already "
        "dispatched — no second dispatch"
    )


def test_rebase_rearms_the_dispatch():
    """dependabot force-pushes rebases — a receipt for an OLD sha must not
    suppress the review of the new head."""
    state = _sweep([_pr(comments=[_receipt(OLD_SHA)])])
    assert len(state["dispatches"]) == 1


def test_forged_receipt_does_not_suppress_the_review():
    """DRE-1998 discipline: only worker-bot-authored receipts count. A
    receipt-shaped comment planted by anyone else must be invisible."""
    state = _sweep([_pr(comments=[_receipt(SHA, author="mallory")])])
    assert len(state["dispatches"]) == 1


def test_bound_verdict_suppresses_dispatch():
    state = _sweep([_pr(comments=[_verdict(SHA)])])
    assert state["dispatches"] == [], (
        "a qa-bot verdict bound to the current head means the review already "
        "happened — nothing to dispatch"
    )


def test_stale_verdict_does_not_suppress_dispatch():
    """A verdict bound to a superseded head is no verdict (DRE-1990) — the
    new head still needs its review."""
    state = _sweep([_pr(comments=[_verdict(OLD_SHA)])])
    assert len(state["dispatches"]) == 1


def test_non_dependabot_prs_are_untouched():
    """ACCEPTANCE: agent/operator PRs keep their event-driven review — this
    backstop must never dispatch (or comment) for them."""
    state = _sweep([
        _pr(branch="agent/DRE-2047-x", author="agent-bureau-bot", number=97),
        _pr(branch="feat/DRE-2001-y", author="operator", number=98),
    ])
    assert state["dispatches"] == [] and state["receipts"] == []


def test_human_authored_dependabot_named_branch_is_untouched():
    """A human's branch merely NAMED dependabot/... triggers pull_request
    runs with NORMAL secrets — the event-driven review works, and a sweep
    dispatch would double-review it."""
    state = _sweep([_pr(author="alice")])
    assert state["dispatches"] == []


@pytest.mark.parametrize("login", ["dependabot", "dependabot[bot]", "app/dependabot"])
def test_every_dependabot_author_login_shape_counts(login):
    """gh surfaces a Bot author's login as "dependabot" (GraphQL),
    "dependabot[bot]" (REST shape) or "app/dependabot" (gh bot marker) —
    all three are the same actor and must dispatch."""
    state = _sweep([_pr(author=login)])
    assert len(state["dispatches"]) == 1, f"login shape {login!r} must count"


def test_dirty_dependabot_pr_is_skipped():
    """dependabot rebases its own conflicts; the rebase changes the head sha
    and re-arms — reviewing the doomed pre-rebase head is pure waste."""
    state = _sweep([_pr(merge_state="DIRTY")])
    assert state["dispatches"] == []


def test_failed_dispatch_posts_no_receipt_and_is_recorded():
    """A 403'd dispatch must stay honest (DRE-1254 class): no receipt —
    the next sweep retries — and the failure recorded so the run goes red."""
    state = _sweep([_pr()], dispatch_rc=1)
    assert state["receipts"] == [], "no receipt without a confirmed dispatch"
    assert reconcile._write_failures, "the failed dispatch must be recorded"


def test_main_runs_the_dependabot_backstop():
    """The backstop must be wired into the full sweep alongside its siblings."""
    mocks = {
        "unstick_conflicts": MagicMock(),
        "retrigger_dead_heads": MagicMock(),
        "fix_approved_but_red": MagicMock(),
        "retry_dead_fix_runs": MagicMock(),
        "review_dependabot_prs": MagicMock(),
        "close_finished_epics": MagicMock(),
        "promote_ready": MagicMock(return_value=0),
        "flag_stranded": MagicMock(return_value=set()),
        "active_cards": MagicMock(return_value=[]),
    }
    with patch.multiple(reconcile, **mocks):
        reconcile.main()
    assert mocks["review_dependabot_prs"].called, (
        "main()'s full sweep must run review_dependabot_prs with the other "
        "PR-level backstops"
    )


# --------------------------------------------------------------------------
# Workflow wiring: the doomed dependabot-actor'd runs are skipped at the stub
# --------------------------------------------------------------------------
def _load(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text())


def _on(doc: dict) -> dict:
    on = doc.get("on", doc.get(True))
    return on if isinstance(on, dict) else {}


def _call_job(doc: dict) -> dict:
    jobs = doc.get("jobs") or {}
    assert len(jobs) == 1, "a stub has exactly one job"
    return next(iter(jobs.values()))


DEPENDABOT_GUARD = "github.actor != 'dependabot[bot]'"


class TestStubGuards:
    def test_pr_review_skips_dependabot_pull_request_events(self):
        """The reusable's required-secret validation fails BEFORE any step
        when the store is empty, so the skip must sit on the stub's call
        job — the only evaluation point early enough."""
        cond = _call_job(_load("pr-review.yml")).get("if") or ""
        assert DEPENDABOT_GUARD in cond, (
            "pr-review.yml's call job must skip dependabot-actor'd runs — "
            "they get the empty Dependabot secrets store and die at "
            "required-secret validation with zero steps (DRE-2047)"
        )

    def test_pr_review_guard_never_blocks_the_dispatched_review(self):
        """The workflow_dispatch path IS the real dependabot review — the
        guard must scope its actor check to pull_request events only."""
        cond = _call_job(_load("pr-review.yml")).get("if") or ""
        assert "github.event_name != 'pull_request'" in cond, (
            "the guard must let non-pull_request triggers (workflow_dispatch, "
            "the sweep's dispatch path) through unconditionally"
        )

    def test_linear_sync_skips_dependabot_closed_events(self):
        """dependabot closes its own superseded PRs — the same empty-store
        death on the closed event. Same guard, same reason."""
        cond = _call_job(_load("self-linear-sync.yml")).get("if") or ""
        assert DEPENDABOT_GUARD in cond

    def test_every_pull_request_stub_calling_a_reusable_is_guarded(self):
        """Structural (DRE-2028 style): ANY stub that (a) fires on
        pull_request events and (b) calls a bureau-pipeline reusable — which
        all require secrets — dies the empty-store death under a dependabot
        actor. Every present and FUTURE such stub (e.g. a verify stub, per
        the card's mirror clause) must carry the guard."""
        for path in sorted(WORKFLOWS.glob("*.yml")):
            doc = yaml.safe_load(path.read_text())
            on = _on(doc)
            if "workflow_call" in on or "pull_request" not in on:
                continue
            jobs = doc.get("jobs") or {}
            for jname, job in jobs.items():
                if not (job.get("uses") or "").startswith(PIPELINE):
                    continue
                assert DEPENDABOT_GUARD in (job.get("if") or ""), (
                    f"{path.name} job {jname!r} calls a secrets-requiring "
                    f"reusable on pull_request events without the dependabot "
                    f"guard — dependabot-actor'd runs will die red with zero "
                    f"steps (DRE-2047)"
                )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
