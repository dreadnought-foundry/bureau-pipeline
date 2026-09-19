"""RED-first tests: a rate-limited read inside the stale-merge-ref check
degrades, it does not fail the sweep (DRE-4278).

THE INCIDENT (2026-09-19, both of the day's Reconcile runs): GitHub refused the
shared App installation mid-sweep at the hourly limit —
`gh api repos/dreadnought-foundry/bureau-pipeline/compare/main...62aeca1a`
answered `HTTP 403: API rate limit exceeded for installation ID 123249480`
inside `refresh_stale_merge_refs`, evaluating pull request #445. The refusal
landed on the READ-failure ledger, `reconcile: 0 write / 1 read failure(s)`
flipped the exit code to 1, and the run went red — while every other phase
finished normally and the branch-listing read, refused by the very same bucket,
had already degraded cleanly under DRE-4214.

THE SCOPE (the CEO's signed answer on the card, 2026-09-18 22:51 PT): apply the
DRE-4214 pattern to the merge-conflict check, so a rate-limit refusal skips that
one check with a note instead of failing the whole sweep.

FIX UNDER TEST — the same three-part shape `card_branches` already carries:
  - a `ReconcileRateLimited` refusal on ANY read the stale-merge-ref check makes
    for a pull request (the `compare`, and the three `check-runs` payloads it
    addresses off it) degrades: one `_degraded` entry, one `DEGRADED:` line, and
    the pull request is left exactly as it was found;
  - the note names the pull request it could not evaluate and says the next
    sweep retries the check — nothing is moved, labelled or commented on, on
    either the pull request or its card, because of the refusal;
  - every OTHER read failure — a 502, a permission 403, a malformed payload —
    is a reason nothing handles and stays on `_read_failures`: red run, medic,
    card, exactly as DRE-2034 left it.

`tests/test_degraded_step_is_not_a_red_run.py` owns the branch listing (the
DRE-4214 read this one follows); it is re-pinned here through the same fixtures
so the two reads are visibly one pattern.

Run: cd bureau-pipeline && python3 -m pytest tests/test_stale_merge_ref_rate_limit_degrades.py -v
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import reconcile  # noqa: E402

#: GitHub's answer on 2026-09-19, verbatim from the card.
RATE_LIMITED = (
    "gh: HTTP 403: API rate limit exceeded for installation ID 123249480 "
    "(https://api.github.com/repos/dreadnought-foundry/bureau-pipeline/"
    "compare/main...62aeca1a8175ae9911a68f299537e07416a9f744)"
)
#: The OTHER 403 — a permission failure nothing in the sweep handles.
NOT_PERMITTED = "gh: HTTP 403: Resource not accessible by integration"
#: And a plain server error, the shape DRE-2034's pin already used.
SERVER_ERROR = "gh: HTTP 502: Bad gateway"

PR_NUMBER = 445
CARD = "DRE-4270"
BRANCH = f"agent/{CARD}-the-branch-that-was-behind"
HEAD = "62aeca1a8175ae9911a68f299537e07416a9f744"
BASE = "b" * 40  # the merge base — `main` before it moved
MAIN = "c" * 40
RED_CHECK = "scripts unit tests"


@pytest.fixture(autouse=True)
def _clean_sweep_state(monkeypatch):
    """Pin the import-time bindings and clear every ledger before AND after,
    so a leftover entry cannot colour another test's sweep."""
    monkeypatch.setattr(reconcile, "REPO", "dreadnought-foundry/bureau-pipeline")
    monkeypatch.setattr(reconcile, "REPO_SLUG", "bureau-pipeline")
    monkeypatch.setattr(reconcile, "_gh_read_retries_spent", 0, raising=False)
    _clear_ledgers()
    yield
    _clear_ledgers()


def _clear_ledgers() -> None:
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()
    reconcile._stale_defects.clear()
    degraded = getattr(reconcile, "_degraded", None)
    if degraded is not None:
        degraded.clear()


def _no_sleep():
    return mock.patch.object(reconcile.time, "sleep", side_effect=lambda s: None)


# --------------------------------------------------------------------------
# fixtures: the payloads GitHub actually returns, and the ones it refuses
# --------------------------------------------------------------------------
def _pr(number=PR_NUMBER, branch=BRANCH, sha=HEAD):
    return {
        "number": number,
        "headRefName": branch,
        "headRefOid": sha,
        "baseRefName": "main",
        "mergeStateStatus": "BLOCKED",
        "isDraft": False,
        "comments": [],
    }


def _compare(base_sha=BASE, main_sha=MAIN):
    return {
        "behind_by": 2,
        "ahead_by": 3,
        "merge_base_commit": {"sha": base_sha},
        "base_commit": {"sha": main_sha},
    }


def _checks(conclusion):
    return {
        "total_count": 1,
        "check_runs": [
            {"name": RED_CHECK, "status": "completed", "conclusion": conclusion}
        ],
    }


#: head red, merge base red, `main` tip green — the one shape that refreshes.
_REFRESHABLE = {"head": "failure", "base": "failure", "main": "success"}

BRANCH_LISTING = json.dumps({"name": BRANCH, "sha": HEAD})


def _gh_answers(prs, refuse=(), stderr=RATE_LIMITED):
    """`answer_for(argv)` -> (rc, stdout, stderr) for the whole sweep.

    `refuse` names the reads GitHub declines with `stderr` on every attempt —
    `compare`, `head`/`base`/`main` (the three check-runs payloads) and
    `branches`. Everything else answers a refreshable pull request.
    """
    refuse = set(refuse)

    def answer(argv):
        if argv[:3] == ["gh", "pr", "list"]:
            return 0, json.dumps(prs), ""
        if argv[:2] != ["gh", "api"]:
            return 0, "", ""
        path = argv[2]
        if path.endswith("/branches"):
            if "branches" in refuse:
                return 1, "", stderr
            return 0, BRANCH_LISTING, ""
        if "/compare/" in path:
            if "compare" in refuse:
                return 1, "", stderr
            return 0, json.dumps(_compare()), ""
        if "/check-runs" in path:
            sha = path.split("/commits/")[1].split("/")[0]
            side = {HEAD: "head", BASE: "base"}.get(sha, "main")
            if side in refuse:
                return 1, "", stderr
            return 0, json.dumps(_checks(_REFRESHABLE[side])), ""
        return 0, "{}", ""

    return answer


def _check(state, refuse=(), stderr=RATE_LIMITED, prs=None):
    """Run `refresh_stale_merge_refs()` once, recording every gh call."""
    answer_for = _gh_answers(prs if prs is not None else [_pr()], refuse, stderr)

    def fake_run(argv, **kwargs):
        rc, out, err = answer_for(list(argv))
        if argv[1] == "api" and "-X" in argv and "PUT" in argv:
            state["puts"].append(list(argv))
            return SimpleNamespace(returncode=0, stdout="{}", stderr="")
        if argv[1] == "pr" and argv[2] == "comment":
            state["pr_comments"].append(list(argv))
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        state["reads"].append(list(argv))
        return SimpleNamespace(returncode=rc, stdout=out, stderr=err)

    with mock.patch.object(reconcile.subprocess, "run", side_effect=fake_run), \
            _no_sleep(), \
            mock.patch.object(reconcile, "card_parked_for_human", return_value=False), \
            mock.patch.object(
                reconcile.linear_ops, "cmd_comment",
                side_effect=lambda ident, *rest: state["card_comments"].append(ident)):
        reconcile.refresh_stale_merge_refs()
    return state


def _state():
    return {"reads": [], "puts": [], "pr_comments": [], "card_comments": []}


# --------------------------------------------------------------------------
# 1. the headline: the refused compare skips ONE check, with a note
# --------------------------------------------------------------------------
def test_a_rate_limited_compare_degrades_the_check_and_not_the_sweep():
    """ACCEPTANCE (the card's headline). The compare read behind the
    stale-merge-ref check is refused for quota on every attempt: the check is
    skipped, ONE degraded entry records it, and NEITHER failure ledger gains
    anything — so the sweep's exit code is untouched.

    On today's code this FAILS: the refusal lands on `_read_failures`, which is
    what turned both of 2026-09-19's sweeps red."""
    _check(_state(), refuse=["compare"])

    assert reconcile._read_failures == [], (
        "a rate-limit refusal the check handled must not turn the run red"
    )
    assert reconcile._write_failures == []
    assert len(reconcile._degraded) == 1, reconcile._degraded


def test_the_skip_note_names_the_pull_request_and_the_retry():
    """ACCEPTANCE: a plain one-line note that names the pull request it could
    not evaluate and says the check is retried next sweep. A reader of the run
    log learns which check did not run and that nobody owes it an action."""
    _check(_state(), refuse=["compare"])

    entry = reconcile._degraded[0]
    assert f"#{PR_NUMBER}" in entry, "the note names the PR it could not evaluate"
    assert "next sweep" in entry, "and says the check will be retried"
    assert "rate limit exceeded" in entry, "carrying GitHub's own reason"
    assert "\n" not in entry, "one line, not a paragraph"


def test_the_skip_note_reaches_the_run_log_as_one_degraded_line(capsys):
    """The DRE-4214 shape, verbatim: one `DEGRADED:` line in the run log — the
    whole of what a step that handled its fault owes."""
    _check(_state(), refuse=["compare"])

    out = capsys.readouterr()
    lines = [ln for ln in (out.out + out.err).splitlines()
             if ln.startswith("DEGRADED:")]
    assert len(lines) == 1, f"exactly one DEGRADED line, got {lines!r}"
    assert f"#{PR_NUMBER}" in lines[0]
    assert "next sweep" in lines[0]


def test_nothing_is_moved_labelled_or_commented_on_for_the_refusal():
    """ACCEPTANCE: degrading is not guessing. With the compare unreadable the
    check writes NOTHING — no update-branch, no pull-request receipt, no card
    comment — because it never reached a decision."""
    state = _check(_state(), refuse=["compare"])

    assert state["puts"] == [], "no merge ref is refreshed off a read that failed"
    assert state["pr_comments"] == []
    assert state["card_comments"] == []


def test_the_rest_of_the_pass_still_runs():
    """ACCEPTANCE: the sweep finishes the rest of its pass. One pull request's
    read is refused, and the NEXT one is evaluated and refreshed normally —
    the skip is scoped to the check that could not read, not to the phase."""
    # Two pull requests with distinct heads, so the compare path names which
    # one is being read: only #445's is refused.
    first_head, second_head = HEAD, "d" * 40
    prs = [_pr(number=PR_NUMBER, sha=first_head),
           _pr(number=446, branch=f"agent/{CARD}-second", sha=second_head)]

    def gh(argv):
        if argv[:3] == ["gh", "pr", "list"]:
            return 0, json.dumps(prs), ""
        if argv[:2] != ["gh", "api"]:
            return 0, "", ""
        path = argv[2]
        if "/compare/" in path:
            if first_head in path:
                return 1, "", RATE_LIMITED
            return 0, json.dumps(_compare()), ""
        if "/check-runs" in path:
            sha = path.split("/commits/")[1].split("/")[0]
            side = {second_head: "head", BASE: "base"}.get(sha, "main")
            return 0, json.dumps(_checks(_REFRESHABLE[side])), ""
        return 0, "{}", ""

    state = _state()

    def fake_run(argv, **kwargs):
        if argv[1] == "api" and "-X" in argv and "PUT" in argv:
            state["puts"].append(list(argv))
            return SimpleNamespace(returncode=0, stdout="{}", stderr="")
        if argv[1] == "pr" and argv[2] == "comment":
            state["pr_comments"].append(list(argv))
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        rc, out, err = gh(list(argv))
        return SimpleNamespace(returncode=rc, stdout=out, stderr=err)

    with mock.patch.object(reconcile.subprocess, "run", side_effect=fake_run), \
            _no_sleep(), \
            mock.patch.object(reconcile, "card_parked_for_human", return_value=False), \
            mock.patch.object(reconcile.linear_ops, "cmd_comment"):
        reconcile.refresh_stale_merge_refs()

    assert len(state["puts"]) == 1, "the readable pull request is still refreshed"
    assert f"repos/{reconcile.REPO}/pulls/446/update-branch" in state["puts"][0]
    assert len(reconcile._degraded) == 1, reconcile._degraded
    assert f"#{PR_NUMBER}" in reconcile._degraded[0]
    assert reconcile._read_failures == []


# --------------------------------------------------------------------------
# 2. the same refusal in the check's other reads, and in the branch listing
# --------------------------------------------------------------------------
@pytest.mark.parametrize("side", ["head", "base", "main"])
def test_a_rate_limited_check_runs_read_degrades_the_same_way(side):
    """ACCEPTANCE: the compare is not the only read this check makes for a
    pull request — it addresses three `check-runs` payloads off it, drawn from
    the same bucket. All four degrade identically or the incident simply moves
    one read along."""
    state = _check(_state(), refuse=[side])

    assert reconcile._read_failures == [], f"the {side} check-runs read"
    assert reconcile._write_failures == []
    assert len(reconcile._degraded) == 1, reconcile._degraded
    assert f"#{PR_NUMBER}" in reconcile._degraded[0]
    assert state["puts"] == [] and state["card_comments"] == []


def test_the_branch_listing_degrades_the_same_way():
    """The DRE-4214 read, re-pinned through this file's fixtures: the sweep's
    other per-pull-request read answers the same refusal the same way. Owned by
    tests/test_degraded_step_is_not_a_red_run.py; asserted here so the pattern
    the merge-conflict check now follows is visible beside it."""
    answer_for = _gh_answers([], refuse=["branches"])

    def fake_run(argv, **kwargs):
        rc, out, err = answer_for(list(argv))
        return SimpleNamespace(returncode=rc, stdout=out, stderr=err)

    with mock.patch.object(reconcile.subprocess, "run", side_effect=fake_run), \
            _no_sleep():
        assert reconcile.card_branches() is None

    assert reconcile._write_failures == [] and reconcile._read_failures == []
    assert len(reconcile._degraded) == 1
    assert "branch listing" in reconcile._degraded[0]


# --------------------------------------------------------------------------
# 3. the other half of the scope: an unhandled reason still fails the run
# --------------------------------------------------------------------------
@pytest.mark.parametrize("stderr,why", [
    (NOT_PERMITTED, "a permission 403 is a wrong token, and waiting never fixes it"),
    (SERVER_ERROR, "a 502 is not a quota refusal and nothing in the sweep handles it"),
])
def test_a_github_error_that_is_not_a_rate_limit_still_fails_the_sweep(stderr, why):
    """ACCEPTANCE: unchanged from today (DRE-2034). Only the ONE fault the read
    seam classifies and handles degrades; every other reason stays on the read
    ledger, takes the sweep red and reaches the medic — a wrong token must never
    leave this check blind under green runs."""
    state = _check(_state(), refuse=["compare"], stderr=stderr)

    assert len(reconcile._read_failures) == 1, why
    assert f"#{PR_NUMBER}" in reconcile._read_failures[0]
    assert reconcile._degraded == [], (
        "an unhandled reason is a failure, not a degrade"
    )
    assert state["puts"] == []


# --------------------------------------------------------------------------
# 4. the run: green when the only fault was the rate limit
# --------------------------------------------------------------------------
_BACKSTOPS_STUBBED = (
    "drain_retiring_lanes", "unstick_conflicts", "retrigger_dead_heads",
    "flag_no_checks_prs", "flag_unowned_prs", "flag_unlanded_work",
    "fix_approved_but_red", "retry_dead_fix_runs", "redispatch_standing_verdicts",
    "recover_limit_deaths", "restart_answered_blockers", "card_dependabot_prs",
    "review_dependabot_prs", "recover_crashed_reviews",
    "report_fleet_reviewer_outage", "check_dependabot_capacity",
    "report_intake_depth", "repair_frozen_planning_holds", "close_finished_epics",
    "report_break_glass", "report_fix_concurrency", "report_evicted_fix_runs",
)


def _sweep(answer_for):
    """main() with every backstop but the stale-merge-ref check stubbed, every
    `gh` answered by `answer_for`, and Linear answering an empty board."""
    mocks = {name: mock.MagicMock() for name in _BACKSTOPS_STUBBED}
    mocks.update({
        "flag_stranded": mock.MagicMock(return_value=set()),
        "promote_ready": mock.MagicMock(return_value=0),
        "report_epic_growth": mock.MagicMock(return_value=[]),
        "active_cards": mock.MagicMock(return_value=[]),
        "card_parked_for_human": mock.MagicMock(return_value=False),
    })

    def fake_run(argv, **kwargs):
        rc, out, err = answer_for(list(argv))
        return SimpleNamespace(returncode=rc, stdout=out, stderr=err)

    with mock.patch.multiple(reconcile, **mocks), \
            mock.patch.object(reconcile.subprocess, "run", side_effect=fake_run), \
            _no_sleep(), \
            mock.patch.object(reconcile.linear_ops, "open_pass"), \
            mock.patch.object(reconcile.linear_ops, "cmd_comment"), \
            mock.patch.object(reconcile.linear_ops, "comment_bodies", return_value=[]):
        reconcile.main()


def test_the_sweep_exits_zero_when_the_only_fault_was_the_rate_limit(capsys):
    """ACCEPTANCE, end to end: this is 2026-09-19's sweep. Every other phase
    ran; the stale-merge-ref check could not read #445's compare and said so.
    The run must NOT exit 1 — and must say, once, that a step degraded."""
    _sweep(_gh_answers([_pr()], refuse=["compare"]))  # no SystemExit is the assertion

    assert reconcile._read_failures == [] and reconcile._write_failures == []
    assert len(reconcile._degraded) == 1
    out = capsys.readouterr().out
    summary = [ln for ln in out.splitlines()
               if "degraded" in ln and ln.startswith("reconcile:")]
    assert len(summary) == 1, f"one summary line for the degraded steps, got {summary!r}"


def test_the_same_sweep_still_exits_red_on_an_unhandled_read_failure():
    """The control: swap the quota refusal for a permission 403 and the sweep
    goes red exactly as it does today, naming the read failure."""
    with pytest.raises(SystemExit) as exc_info:
        _sweep(_gh_answers([_pr()], refuse=["compare"], stderr=NOT_PERMITTED))
    assert "1 read" in str(exc_info.value)
    assert reconcile._degraded == []
