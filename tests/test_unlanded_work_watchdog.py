"""RED-first tests: work that never became a pull request (DRE-2682).

THE BUG (live, 2026-08-22): DRE-2655's work was finished, tested and pushed to
`agent/DRE-2655-drift-count-out-of-the-pill` at 17:36 PT — and no pull request
was ever opened. CI, the critic, the merge gate and linear-sync all key off a
PULL REQUEST, so a pushed branch on its own is outside the entire system. The
card read "In Progress" for nineteen hours, which was true and carried no
information, and an operator found it by eye the next morning.

Every mechanism missed it for its own reason, and none of them is a bug on its
own: no PR means no gate at all; `unowned-branch-watchdog` covers the mirror
case (a PR on a wrongly-named branch) and nothing watches a correctly-named
branch that never became one; and `hand-built` suppresses the stranded-card
alarm BY DESIGN (DRE-2524), so the label that marks work as human-built also
switches off the only thing that would have said it had stopped.

FIX UNDER TEST — reconcile.flag_unlanded_work(), on every full sweep, two
halves that share one branch listing and one PR listing:

  (A) an `agent/DRE-*` branch carrying commits that are not on the default
      branch, for which NO pull request has ever been opened (open, closed or
      merged), gets ONE plain-English comment on its card after
      UNLANDED_MINUTES — naming the branch and the route out: open the PR, and
      the work meets the same gates as everything else.
  (C) a HAND-BUILT card in Todo / In Progress with nothing to point at — no
      card branch and no PR — is reported after HAND_IDLE_MINUTES. This is the
      alarm that replaces what the `hand-built` label suppresses: it measures
      the HAND thing (no branch, or a branch with no PR) rather than the FLEET
      thing (in Todo with no dispatched run, which for hand-built work is
      normal). Shipping the HAND verdict without it would make DRE-2655's
      failure mode MORE common, not less.

Both halves are ALERT-ONLY — one idempotent comment, no state move, no hold
label — and NEITHER may distinguish hand-built work from dispatched work by
git author: DRE-2655's commits were authored `agent-bureau-bot[bot]` and were
written by hand, DRE-2694's were authored by the operator and were not. Git
authorship misleads in both directions; the dispatch record and the pull
request are the facts that cannot be spoofed by a local git config.

Run: cd bureau-pipeline && python3 -m pytest tests/test_unlanded_work_watchdog.py -v
"""
from __future__ import annotations

import inspect
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/test")
os.environ.setdefault("REPO_SLUG", "test")
os.environ.setdefault("GH_TOKEN", "x")

import reconcile  # noqa: E402

BRANCH = "agent/DRE-2655-drift-count-out-of-the-pill"
CARD = "DRE-2655"
SHA = "b" * 40


@pytest.fixture(autouse=True)
def _pin_repo_slug(monkeypatch):
    """REPO_SLUG is bound at import; pin it so this sweep owns these cards
    regardless of collection order (test_human_hold.py's hazard)."""
    monkeypatch.setattr(reconcile, "REPO_SLUG", "test")


def _iso(minutes_ago: float) -> str:
    return (
        (datetime.now(UTC) - timedelta(minutes=minutes_ago))
        .isoformat()
        .replace("+00:00", "Z")
    )


def _card(identifier=CARD, state="In Progress", labels=("hand-built",),
          updated_min=999.0, description="**Repo:** test"):
    return {
        "identifier": identifier,
        "title": "a card",
        "description": description,
        "updatedAt": _iso(updated_min),
        "state": {"name": state},
        "labels": {"nodes": [{"name": n} for n in labels]},
    }


def _fake_gh(branches, pr_refs, pulls=None, compare=None, calls=None,
             listing=None, branches_unreadable=False):
    """Stub reconcile.gh / gh_read for the four reads the watchdog makes.

    branches: {branch name: head sha} — the `repos/:r/branches` listing.
    pr_refs:  head refs of every PR in ANY state (open, closed, merged).
    pulls:    {sha: raw gh output} for `commits/:sha/pulls` (default "[]").
    compare:  {branch: (ahead_by, minutes since last commit)} or a raw string.
    listing:  raw override for the `pr list` output (e.g. "" = unreadable).
    branches_unreadable: the branch listing raises the way gh_read does on a
              non-zero exit (403 / rate limit), rather than answering.
    """
    pulls = pulls or {}
    compare = compare or {}

    def gh(*args):
        if calls is not None:
            calls.append(args)
        if args[:2] == ("pr", "list"):
            if listing is not None:
                return listing
            return json.dumps([{"headRefName": r} for r in pr_refs])
        if args[0] == "api":
            path = args[1]
            if path == f"repos/{reconcile.REPO}":
                return "main"
            if path.endswith("/branches"):
                if branches_unreadable:
                    raise reconcile.ReconcileReadError(
                        "gh api branches failed rc=1: HTTP 403"
                    )
                return "\n".join(
                    json.dumps({"name": n, "sha": s})
                    for n, s in branches.items()
                )
            if "/commits/" in path and path.endswith("/pulls"):
                sha = path.split("/commits/", 1)[1].split("/", 1)[0]
                return pulls.get(sha, "[]")
            if "/compare/" in path:
                branch = path.split("...", 1)[-1]
                spec = compare.get(branch, (3, 999.0))
                if isinstance(spec, str):
                    return spec
                ahead, minutes = spec
                return json.dumps({
                    "ahead": ahead,
                    "last": _iso(minutes) if ahead else None,
                })
        return ""


    return gh


def _state_of(states):
    """card_state stub: a mapped Exception is RAISED, so a test can stub an
    unreadable card as well as a state."""
    def state(identifier):
        value = states.get(identifier, "In Progress")
        if isinstance(value, Exception):
            raise value
        return value
    return state


def sweep(branches=None, pr_refs=(), pulls=None, compare=None, bodies=(),
          cards=(), states=None, listing=None, branches_unreadable=False):
    """Run flag_unlanded_work() once against a stubbed GitHub + Linear.

    Returns (comments, gh_calls, add_label mock).
    """
    branches = {BRANCH: SHA} if branches is None else branches
    states = states or {}
    comments, gh_calls = [], []
    fake_gh = _fake_gh(branches, pr_refs, pulls, compare, gh_calls, listing,
                       branches_unreadable)
    with mock.patch.object(
        reconcile, "gh", side_effect=fake_gh,
    ), mock.patch.object(
        # The branch listing goes through the LOUD helper, so the stub must
        # answer there too — and raise there, when the listing is unreadable.
        reconcile, "gh_read", side_effect=fake_gh,
    ), mock.patch.object(
        reconcile, "active_cards", return_value=list(cards),
    ), mock.patch.object(
        reconcile, "card_state", side_effect=_state_of(states),
    ), mock.patch.object(
        reconcile.linear_ops, "comment_bodies", return_value=list(bodies),
    ), mock.patch.object(
        reconcile.linear_ops, "cmd_comment",
        side_effect=lambda ident, body: comments.append((ident, body)),
    ), mock.patch.object(
        reconcile.linear_ops, "add_label",
    ) as add_label:
        reconcile.flag_unlanded_work()
    return comments, gh_calls, add_label


# --------------------------------------------------------------------------
# A: a pushed branch that never became a pull request
# --------------------------------------------------------------------------

def test_branch_with_commits_and_no_pr_is_reported_on_its_card():
    comments, _, _ = sweep()
    assert len(comments) == 1
    ident, body = comments[0]
    assert ident == CARD
    assert BRANCH in body


def test_the_report_names_the_route_out_pr_then_the_same_gates():
    """A refusal is only half a gate: the notice must say where the work
    goes and what finishing looks like."""
    body = sweep()[0][0][1]
    assert "pull request" in body.lower()
    assert "critic" in body.lower() and "merge gate" in body.lower()


def test_the_report_is_machine_marked_and_greppable():
    body = sweep()[0][0][1]
    assert body.startswith("🚨")
    assert reconcile.UNLANDED_TAG in body


def test_a_branch_with_an_open_pr_is_not_reported():
    assert sweep(pr_refs=[BRANCH])[0] == []


def test_a_branch_whose_pr_was_merged_and_closed_is_not_reported():
    """The PR listing is read `--state all` on purpose: a squash-merged
    branch is still 'ahead' of the default branch forever, so an open-only
    listing would alarm on every merged branch that was not deleted."""
    calls = []
    comments, calls, _ = sweep(pr_refs=[BRANCH])
    assert comments == []
    listing = [a for a in calls if a[:2] == ("pr", "list")]
    assert listing and "all" in listing[0], listing


def test_a_pr_outside_the_listing_window_still_silences_the_branch():
    """Confirmed per candidate against the commit's own PR list — the
    listing is a cheap filter, not the proof."""
    assert sweep(pulls={SHA: "[413]"})[0] == []


def test_an_unreadable_pr_check_reports_nothing():
    """A 403 is not 'this branch has no PR' (DRE-2034)."""
    assert sweep(pulls={SHA: ""})[0] == []


def test_an_unreadable_pr_listing_reports_nothing():
    assert sweep(listing="")[0] == []


def test_an_unreadable_branch_listing_reports_nothing():
    """The mirror of the PR listing, and the one that alarms on a CARD.

    A failed `/branches` read must never read as "this repo has no branches":
    _flag_hand_built_idle would then see an empty `with_branch` and tell a
    person "no branch, no pull request" on a card whose branch the sweep
    simply could not see — the exact false alarm this watchdog exists to
    prevent, one level up (DRE-2034 discipline).
    """
    comments, _, _ = sweep(
        branches={BRANCH: SHA}, cards=[_card()], branches_unreadable=True,
    )
    assert comments == []


def test_an_unreadable_branch_listing_never_reaches_the_hand_built_half():
    """Not merely quiet — the card sweep must not run at all, so no card can
    be judged against a listing that was never read."""
    with mock.patch.object(reconcile, "_flag_hand_built_idle") as idle:
        sweep(cards=[_card()], branches_unreadable=True)
    idle.assert_not_called()


def test_a_confirmed_empty_branch_listing_still_reports_the_hand_built_card():
    """The other side of the same coin: a repo that genuinely has no card
    branches is a readable answer, and the hand-built alarm must still fire.
    Without this, failing closed would silence the half entirely."""
    comments, _, _ = sweep(branches={}, cards=[_card()])
    assert len(comments) == 1
    assert comments[0][0] == CARD


def test_a_branch_with_no_commits_of_its_own_is_not_reported():
    assert sweep(compare={BRANCH: (0, 999.0)})[0] == []


def test_an_unreadable_compare_reports_nothing():
    assert sweep(compare={BRANCH: ""})[0] == []


def test_a_branch_inside_the_bounded_interval_is_not_reported():
    """Someone may be mid-push; the interval is what makes this a stall
    rather than a race."""
    fresh = reconcile.UNLANDED_MINUTES - 1
    assert sweep(compare={BRANCH: (3, fresh)})[0] == []


def test_a_branch_past_the_bounded_interval_is_reported():
    stale = reconcile.UNLANDED_MINUTES + 1
    assert len(sweep(compare={BRANCH: (3, stale)})[0]) == 1


def test_a_non_card_branch_is_ignored():
    assert sweep(branches={"docs/forms-critic-findings": SHA})[0] == []
    assert sweep(branches={"main": SHA})[0] == []


def test_a_card_branch_carrying_no_card_id_is_ignored():
    assert sweep(branches={"agent/scaffold-guard-lint": SHA})[0] == []


def test_a_branch_whose_card_is_terminal_is_not_reported():
    """Done/Canceled cards are route F/I's business, not this alarm's."""
    for state in ("Done", "Canceled", "Duplicate"):
        assert sweep(states={CARD: state})[0] == [], state


def test_an_unreadable_card_state_reports_nothing():
    """Linear being unreadable is not evidence the card is live."""
    comments, _, _ = sweep(states={CARD: RuntimeError("linear down")})
    assert comments == []


def test_reported_once_per_branch():
    marker = f"{reconcile.UNLANDED_TAG} branch {BRANCH}:"
    assert sweep(bodies=[f"🚨 {marker} said already"])[0] == []


def test_a_second_branch_on_the_same_card_still_speaks():
    """The marker is keyed per BRANCH: a card whose first branch was reported
    must not go silent when a new branch strands the same way."""
    other = "agent/DRE-2655-second-attempt"
    marker = f"{reconcile.UNLANDED_TAG} branch {BRANCH}:"
    comments, _, _ = sweep(
        branches={BRANCH: SHA, other: "c" * 40},
        bodies=[f"🚨 {marker} said already"],
    )
    assert [b for _, b in comments if other in b]


def test_the_hand_built_label_does_not_silence_this_half():
    """The point of the card: `hand-built` suppresses the stranded alarm by
    design, and a pushed branch with no PR is exactly the failure that
    suppression hid."""
    comments, _, _ = sweep(cards=[_card(labels=("hand-built",))])
    assert len(comments) >= 1
    assert any(BRANCH in b for _, b in comments)


def test_the_watchdog_never_adds_a_hold_label():
    """Alert-only, both halves: the hold label stands down repairs and
    permanently blocks promotion — noticing is not parking."""
    _, _, add_label = sweep(cards=[_card(updated_min=999.0)])
    assert not add_label.called


def test_one_failing_branch_does_not_silence_the_others():
    other = "agent/DRE-2699-scaffold-guard-lint"

    def boom(ident, body):
        if ident == CARD:
            raise RuntimeError("linear exploded")

    posted = []
    fake_gh = _fake_gh({BRANCH: SHA, other: "c" * 40}, (), None, None,
                       None, None)
    with mock.patch.object(
        reconcile, "gh", side_effect=fake_gh,
    ), mock.patch.object(
        reconcile, "gh_read", side_effect=fake_gh,
    ), mock.patch.object(
        reconcile, "active_cards", return_value=[],
    ), mock.patch.object(
        reconcile, "card_state", return_value="In Progress",
    ), mock.patch.object(
        reconcile.linear_ops, "comment_bodies", return_value=[],
    ), mock.patch.object(
        reconcile.linear_ops, "cmd_comment",
        side_effect=lambda i, b: boom(i, b) or posted.append(i),
    ):
        reconcile.flag_unlanded_work()
    assert posted == ["DRE-2699"]
    assert reconcile._write_failures, "the failure was swallowed silently"
    reconcile._write_failures.clear()


# --------------------------------------------------------------------------
# C: a hand-built card with nothing to point at — the replacement alarm
# --------------------------------------------------------------------------

def test_hand_built_card_with_no_branch_and_no_pr_is_reported():
    comments, _, _ = sweep(branches={}, cards=[_card()])
    assert len(comments) == 1
    ident, body = comments[0]
    assert ident == CARD
    assert "no branch" in body.lower()
    assert f"agent/{CARD}-" in body, "the notice must name the branch to open"


def test_hand_built_card_inside_its_interval_is_not_reported():
    young = reconcile.HAND_IDLE_MINUTES - 1
    assert sweep(branches={}, cards=[_card(updated_min=young)])[0] == []


def test_hand_built_card_with_a_branch_is_left_to_the_branch_half():
    """One alarm per card: the branch half already names the specific gap."""
    comments, _, _ = sweep(cards=[_card()])
    assert len(comments) == 1
    assert BRANCH in comments[0][1]


def test_hand_built_card_with_a_pr_is_not_reported():
    comments, _, _ = sweep(branches={}, pr_refs=[BRANCH], cards=[_card()])
    assert comments == []


def test_a_fleet_card_with_nothing_to_point_at_is_left_to_flag_stranded():
    """No double alarm: a dispatched card with no run receipt is
    flag_stranded's case and has been since DRE-1993."""
    assert sweep(branches={}, cards=[_card(labels=())])[0] == []


def test_a_held_hand_built_card_is_not_reported():
    """A card already in a human's queue owes an action either way."""
    assert sweep(
        branches={}, cards=[_card(labels=("hand-built", "needs-human"))],
    )[0] == []


def test_a_hand_built_card_of_another_repo_is_left_to_that_sweep():
    assert sweep(
        branches={}, cards=[_card(description="**Repo:** portico")],
    )[0] == []


def test_the_hand_built_half_is_reported_once():
    marker = f"{reconcile.UNLANDED_TAG} no branch:"
    assert sweep(
        branches={}, cards=[_card()], bodies=[f"🚨 {marker} said already"],
    )[0] == []


def test_only_the_working_lanes_are_swept_for_hand_built_idleness():
    """A Backlog card owes nobody a branch."""
    _, calls, _ = sweep(branches={}, cards=[_card()])
    fake_gh = _fake_gh({}, (), None, None, None, None)
    with mock.patch.object(
        reconcile, "gh", side_effect=fake_gh,
    ), mock.patch.object(
        reconcile, "gh_read", side_effect=fake_gh,
    ), mock.patch.object(
        reconcile, "active_cards", return_value=[],
    ) as active, mock.patch.object(
        reconcile.linear_ops, "comment_bodies", return_value=[],
    ), mock.patch.object(
        reconcile.linear_ops, "cmd_comment",
    ):
        reconcile.flag_unlanded_work()
    active.assert_called_once_with(reconcile.WATCHDOG_LANES)


# --------------------------------------------------------------------------
# The constraint that spans both halves: authorship is never the signal
# --------------------------------------------------------------------------

def test_no_read_asks_github_who_authored_anything():
    """DRE-2655's commits wore the bot's identity and were hand-written;
    DRE-2694's wore the operator's and were not. Any check that tells HAND
    from FLEET by git author is wrong in both directions."""
    _, calls, _ = sweep(cards=[_card()])
    assert calls, "the watchdog made no reads at all"
    for args in calls:
        joined = " ".join(args).lower()
        assert "author" not in joined, args
        assert "user.login" not in joined, args


def test_the_watchdog_runs_on_every_full_sweep():
    assert "flag_unlanded_work" in inspect.getsource(reconcile.main)
