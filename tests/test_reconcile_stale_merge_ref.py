"""RED-first tests for DRE-3144 — refresh a PR's merge ref once when `main`
has fixed the fault it is red on.

THE BUG (live, 2026-09-02 00:00-00:23 PT). agent-bureau PRs #2240 and #2241
went red on `Console backend (pytest)` because of a fault on `main`
(DRE-2962). The fix merged to `main` at 00:02. Both stayed red anyway: their
CI had run against a merge ref computed BEFORE the fix, and nothing in the
pipeline recomputes a merge ref when `main` moves. `gh run rerun` re-runs the
same jobs against the SAME merge commit, so only a new head helps. Both moved
only when an operator ran `gh pr update-branch` by hand at 00:23 — #2240 with
a critic APPROVE already sitting behind a failure that no longer existed.

FIX UNDER TEST — `reconcile.refresh_stale_merge_refs()`, a FULL-sweep backstop
sitting beside the conflict sweep. The DECISION is not retested here: it is
`scripts/stale_merge_ref.py` (DRE-3138) and `tests/test_stale_merge_ref.py`
owns it. What this file pins is the WRAPPER — which PRs it reads, what it
writes, under which token, how often, and what it says afterwards:

  1. behind_by > 0 + head red only on checks red on the merge base and green
     on the `main` tip -> exactly one `PUT …/update-branch` carrying
     `expected_head_sha` = the head that was read, then ONE PR receipt and
     ONE card receipt, both composed through `pipeline_act.receipt()`.
  2. Idempotent per `main` commit: a receipt already carrying
     `stale-merge-ref-refresh @<main sha>`, or `behind_by == 0`, writes
     nothing at all.
  3. A failure that is GREEN on the merge base is the PR's own defect (the
     fix loop owns it); a failure still RED on the `main` tip belongs to the
     Red-Main Repair loop. Neither is refreshed.
  4. Budgets: STALE_MERGE_REFRESH_CAP lifetime refreshes per PR (0 is the
     operator's fleet-wide off switch), STALE_MERGE_REFRESH_SWEEP_CAP per
     sweep, oldest PR first — every refresh is a full CI run and possibly a
     critic run (the DRE-2049 burst lesson).
  5. Skipped before any compare read: non-`agent/`, draft, DIRTY (the
     conflict sweep owns it) and human-parked PRs. An unreadable compare or
     check-runs read is UNEVALUATED — skipped, never refreshed (DRE-2034).
  6. The DRE-1254 discipline on the write: a failed PUT posts no receipt,
     lands in `_write_failures` and takes the sweep red; a receipt that fails
     after a 202 is recorded too, and the PUT is not repeated.
  7. Vendor boundary Q1 (`standards/vendor-boundaries.md`): the PUT and the
     PR comment run under the sweep's default `GH_TOKEN` — the App token,
     whose `synchronize` event `qa-review.yml`'s `allowed_bots` admits. A
     `GH_DISPATCH_TOKEN`-authored update would fire NO workflows at all.

Run: cd bureau-pipeline && python3 -m pytest tests/test_reconcile_stale_merge_ref.py -v
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")
os.environ.setdefault("GH_TOKEN", "ghs_app")

import pipeline_act  # noqa: E402
import reconcile  # noqa: E402
import stale_merge_ref  # noqa: E402

HEAD = "a" * 40
MAIN = "c" * 40
BASE = "b" * 40  # the merge base — `main` before it moved
OTHER_MAIN = "d" * 40

RED_CHECK = "Console backend (pytest)"
ACT = "merge-ref-refreshed"


@pytest.fixture(autouse=True)
def _product_repo(monkeypatch):
    monkeypatch.setattr(reconcile, "REPO", "dreadnought-foundry/agent-bureau")
    monkeypatch.setattr(reconcile, "REPO_SLUG", "agent-bureau")
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()
    yield
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()


# --------------------------------------------------------------------------
# fixtures: the payloads GitHub actually returns
# --------------------------------------------------------------------------
def _pr(number=2240, sha=HEAD, branch="agent/DRE-2962-console", comments=(),
        mstate="BLOCKED", draft=False, base="main"):
    return {
        "number": number,
        "headRefName": branch,
        "headRefOid": sha,
        "baseRefName": base,
        "mergeStateStatus": mstate,
        "isDraft": draft,
        "comments": list(comments),
    }


def _compare(behind=2, base_sha=BASE, main_sha=MAIN):
    return {
        "behind_by": behind,
        "ahead_by": 3,
        "merge_base_commit": {"sha": base_sha},
        "base_commit": {"sha": main_sha},
    }


def _checks(*runs):
    return {"total_count": len(runs), "check_runs": list(runs)}


def _red(name=RED_CHECK):
    return {"name": name, "status": "completed", "conclusion": "failure"}


def _green(name=RED_CHECK):
    return {"name": name, "status": "completed", "conclusion": "success"}


def _refresh_receipt(main_sha=MAIN, author="agent-bureau-bot"):
    """The sweep's own receipt, as the next sweep reads it back."""
    return {
        "author": {"login": author},
        "body": pipeline_act.receipt(ACT, stale_merge_ref.receipt_detail(
            pr_number=2240, head_sha=HEAD, main_sha=main_sha, base_sha=BASE,
            inherited=[RED_CHECK], used=1, cap=3,
        )),
    }


def _state(prs, compare=None, head=None, base=None, main=None, put_rc=0,
           comment_rc=0):
    """Everything the fake `gh` answers with, keyed the way the sweep asks."""
    return {
        "prs": list(prs),
        "compare": compare if compare is not None else _compare(),
        "head": head if head is not None else _checks(_red()),
        "base": base if base is not None else _checks(_red()),
        "main": main if main is not None else _checks(_green()),
        "put_rc": put_rc,
        "comment_rc": comment_rc,
        "gh": [],
        "puts": [],
        "pr_comments": [],
        "card_comments": [],
    }


def _run_factory(state):
    """The gh calls this backstop makes, and nothing else. Every call records
    the env it was handed, so the token question is answerable by test."""

    def fake_run(argv, **kwargs):
        assert argv[0] == "gh", f"unexpected call: {argv}"
        env = kwargs.get("env")
        state["gh"].append({"argv": list(argv), "env": env})
        if argv[1] == "pr" and argv[2] == "list":
            return SimpleNamespace(
                returncode=0, stdout=json.dumps(state["prs"]), stderr="")
        if argv[1] == "pr" and argv[2] == "comment":
            body = argv[argv.index("--body") + 1]
            state["pr_comments"].append({"argv": list(argv), "body": body,
                                         "env": env})
            return SimpleNamespace(
                returncode=state["comment_rc"], stdout="",
                stderr="HTTP 403" if state["comment_rc"] else "")
        if argv[1] == "api" and "-X" in argv and "PUT" in argv:
            state["puts"].append({"argv": list(argv), "env": env})
            return SimpleNamespace(
                returncode=state["put_rc"],
                stdout="" if state["put_rc"] else
                       '{"message":"Updating pull request branch."}',
                stderr="HTTP 422" if state["put_rc"] else "")
        if argv[1] == "api" and "/compare/" in argv[2]:
            return _answer(state["compare"])
        if argv[1] == "api" and "/check-runs" in argv[2]:
            sha = argv[2].split("/commits/")[1].split("/")[0]
            # Anything that is neither the head nor the merge base IS the
            # `main` tip — the flap fixture walks several of them.
            key = {HEAD: "head", BASE: "base"}.get(sha, "main")
            return _answer(state[key])
        raise AssertionError(f"unexpected gh call: {argv}")

    def _answer(payload):
        if isinstance(payload, Exception):
            return SimpleNamespace(returncode=1, stdout="", stderr="HTTP 502")
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload),
                               stderr="")

    return fake_run


def _sweep(state, parked=False, cap=None, sweep_cap=None):
    """Run refresh_stale_merge_refs() once against `state`."""
    patches = []
    if cap is not None:
        patches.append(patch.object(reconcile, "STALE_MERGE_REFRESH_CAP", cap))
    if sweep_cap is not None:
        patches.append(patch.object(
            reconcile, "STALE_MERGE_REFRESH_SWEEP_CAP", sweep_cap))
    with patch.object(
        reconcile.subprocess, "run", side_effect=_run_factory(state)
    ), patch.object(
        reconcile, "card_parked_for_human", return_value=parked
    ), patch.object(
        reconcile.linear_ops, "cmd_comment",
        side_effect=lambda ident, *rest: state["card_comments"].append(
            (ident, rest[0] if rest else "")),
    ):
        for p in patches:
            p.start()
        try:
            reconcile.refresh_stale_merge_refs()
        finally:
            for p in reversed(patches):
                p.stop()
    return state


def _compare_reads(state):
    return [c for c in state["gh"] if "/compare/" in " ".join(c["argv"])]


# --------------------------------------------------------------------------
# 1. the headline: one refresh, one PR receipt, one card receipt
# --------------------------------------------------------------------------
def test_main_fixed_the_fault_so_the_merge_ref_is_refreshed_once():
    """ACCEPTANCE (the epic's headline): behind_by 2, head red only on a
    check also red on the merge base and green on the `main` tip -> exactly
    one update-branch PUT carrying the head that was read."""
    state = _sweep(_state([_pr()]))
    assert len(state["puts"]) == 1, (
        "exactly one update-branch call — the whole remedy #2240 waited 21 "
        "minutes for a human to type"
    )
    argv = state["puts"][0]["argv"]
    assert f"repos/{reconcile.REPO}/pulls/2240/update-branch" in argv
    assert f"expected_head_sha={HEAD}" in argv, (
        "expected_head_sha makes a concurrent push a 422, never an update of "
        "a head we never read"
    )


def test_both_receipts_carry_the_trailer_and_the_main_bound_marker():
    """ACCEPTANCE: one PR receipt and one card receipt, both ending in the
    `📎 pipeline-act: merge-ref-refreshed …` trailer and both carrying
    `stale-merge-ref-refresh @<main sha>` — the idempotency key the next
    sweep reads back."""
    state = _sweep(_state([_pr()]))
    assert len(state["pr_comments"]) == 1, "one receipt on the pull request"
    assert len(state["card_comments"]) == 1, "mirrored onto the linked card"
    ident, card_body = state["card_comments"][0]
    assert ident == "DRE-2962", "the receipt lands on the branch's card"
    for body in (state["pr_comments"][0]["body"], card_body):
        assert stale_merge_ref.marker(MAIN) in body, (
            "the marker binds the refresh to the `main` commit it moved onto"
        )
        trailer = pipeline_act.read_trailer(body)
        assert trailer and trailer["act"] == ACT
        assert trailer["tag"] == stale_merge_ref.REFRESH_TAG
        assert body.rstrip().endswith(pipeline_act.trailer(ACT))


def test_the_two_receipts_are_the_same_body():
    state = _sweep(_state([_pr()]))
    assert state["pr_comments"][0]["body"] == state["card_comments"][0][1], (
        "the console reads the card; it must read what the PR reads"
    )


# --------------------------------------------------------------------------
# 2. idempotent per `main` commit
# --------------------------------------------------------------------------
def test_a_receipt_for_this_main_sha_stops_the_next_sweep():
    """ACCEPTANCE: the same PR on the next sweep, receipt present for that
    `main` sha -> no PUT, no comment."""
    state = _sweep(_state([_pr(comments=[_refresh_receipt()])]))
    assert state["puts"] == [], "at most one refresh per `main` commit"
    assert state["pr_comments"] == [] and state["card_comments"] == []


def test_a_current_branch_is_never_refreshed():
    """ACCEPTANCE: behind_by 0 -> no PUT, no comment. After a refresh the
    head moves and `behind_by` reads 0, so the rule cannot fire twice for one
    `main` commit even before the receipt is readable."""
    state = _sweep(_state([_pr()], compare=_compare(behind=0)))
    assert state["puts"] == []
    assert state["pr_comments"] == [] and state["card_comments"] == []


def test_a_forged_receipt_cannot_suppress_the_refresh():
    """DRE-1998 discipline: only WORKER-BOT receipts are read back, so a
    forger cannot freeze a branch behind a fake refresh."""
    state = _sweep(_state([_pr(comments=[_refresh_receipt(author="mallory")])]))
    assert len(state["puts"]) == 1


# --------------------------------------------------------------------------
# 3. whose fault is it — the two failures that are NOT refreshed
# --------------------------------------------------------------------------
def test_a_failure_green_on_the_merge_base_is_the_prs_own_defect():
    """ACCEPTANCE: the fix loop owns it — no PUT, no comment."""
    state = _sweep(_state([_pr()], base=_checks(_green())))
    assert state["puts"] == []
    assert state["pr_comments"] == [] and state["card_comments"] == []


def test_a_failure_still_red_on_the_main_tip_is_not_refreshed():
    """ACCEPTANCE: the Red-Main Repair loop owns it, and a refresh would only
    re-inherit the failure."""
    state = _sweep(_state([_pr()], main=_checks(_red())))
    assert state["puts"] == []
    assert state["pr_comments"] == [] and state["card_comments"] == []


def test_mains_check_still_running_is_not_a_green_light():
    """`main`'s CI unfinished is an unfinished sentence — try again next
    sweep, never refresh onto it."""
    state = _sweep(_state([_pr()], main=_checks(
        {"name": RED_CHECK, "status": "in_progress", "conclusion": None})))
    assert state["puts"] == []


# --------------------------------------------------------------------------
# 4. the budgets
# --------------------------------------------------------------------------
def test_main_flapping_three_times_spends_exactly_the_lifetime_cap():
    """ACCEPTANCE: three distinct `main` shas, each fixing the check, with
    STALE_MERGE_REFRESH_CAP=3 -> three refreshes, then none."""
    seen = []
    receipts: list = []
    for index, main_sha in enumerate(("1" * 40, "2" * 40, "3" * 40, "4" * 40)):
        state = _state([_pr(comments=list(receipts))],
                       compare=_compare(main_sha=main_sha))
        state["main"] = _checks(_green())
        with patch.object(
            reconcile.subprocess, "run", side_effect=_run_factory(state)
        ), patch.object(
            reconcile, "card_parked_for_human", return_value=False
        ), patch.object(
            reconcile, "STALE_MERGE_REFRESH_CAP", 3
        ), patch.object(
            reconcile.linear_ops, "cmd_comment",
            side_effect=lambda i, *r: state["card_comments"].append((i, r[0])),
        ):
            reconcile.refresh_stale_merge_refs()
        seen.append(len(state["puts"]))
        if state["pr_comments"]:
            receipts.append({"author": {"login": "agent-bureau-bot"},
                             "body": state["pr_comments"][0]["body"]})
    assert seen == [1, 1, 1, 0], (
        f"three refreshes then none at the lifetime cap of 3 — got {seen}"
    )


def test_cap_zero_is_the_operators_fleet_wide_off_switch():
    """ACCEPTANCE: STALE_MERGE_REFRESH_CAP=0 -> no refresh at all."""
    state = _sweep(_state([_pr()]), cap=0)
    assert state["puts"] == []
    assert state["pr_comments"] == [] and state["card_comments"] == []


def test_the_default_lifetime_cap_is_three():
    assert reconcile.STALE_MERGE_REFRESH_CAP == 3
    assert reconcile.STALE_MERGE_REFRESH_SWEEP_CAP == 3


def test_refreshes_are_paced_per_sweep_oldest_pr_first():
    """ACCEPTANCE: at most 3 refreshes in one sweep, oldest PR first — every
    refresh is a full CI run and possibly a critic run (DRE-2049)."""
    prs = [_pr(number=n, branch=f"agent/DRE-{n}-widget")
           for n in (2251, 2249, 2240, 2241, 2247)]
    state = _sweep(_state(prs))
    numbers = [int(a.split("/pulls/")[1].split("/")[0])
               for p in state["puts"] for a in p["argv"] if "/pulls/" in a]
    assert numbers == [2240, 2241, 2247], (
        f"three paced refreshes per sweep, oldest first — got {numbers}"
    )
    assert len(state["pr_comments"]) == 3


# --------------------------------------------------------------------------
# 5. who is skipped, and before which read
# --------------------------------------------------------------------------
@pytest.mark.parametrize("pr,why", [
    (_pr(branch="dependabot/pip/urllib3-2.5.0"), "not an agent branch"),
    (_pr(branch="repair/red-main"), "not a card branch"),
    (_pr(draft=True), "a draft"),
    (_pr(mstate="DIRTY"), "DIRTY — the conflict sweep owns it"),
])
def test_ineligible_prs_are_skipped_before_any_compare_read(pr, why):
    """ACCEPTANCE: skipped BEFORE the compare read — four `gh api` calls per
    PR is the cost this gate exists to avoid paying on a PR it cannot act on."""
    state = _sweep(_state([pr]))
    assert state["puts"] == [], why
    assert _compare_reads(state) == [], (
        f"{why}: no compare should have been read at all"
    )


def test_a_human_parked_card_is_skipped_before_any_compare_read():
    """The DRE-2024 discipline: a human owes this card an action, so no
    automation touches its branch."""
    state = _sweep(_state([_pr()]), parked=True)
    assert state["puts"] == []
    assert _compare_reads(state) == []


def test_an_unreadable_compare_is_unevaluated_never_refreshed():
    """ACCEPTANCE (DRE-2034): a 403/502 is not "nothing to do" — skip, record,
    never act on fabricated data."""
    state = _sweep(_state([_pr()], compare=RuntimeError("502")))
    assert state["puts"] == []
    assert state["pr_comments"] == [] and state["card_comments"] == []
    assert reconcile._read_failures, (
        "an unreadable read is recorded so the sweep exits red and medic sees it"
    )


@pytest.mark.parametrize("side", ["head", "base", "main"])
def test_an_unreadable_check_runs_read_is_unevaluated(side):
    state = _sweep(_state([_pr()], **{side: RuntimeError("502")}))
    assert state["puts"] == []
    assert state["pr_comments"] == [] and state["card_comments"] == []


# --------------------------------------------------------------------------
# 6. the write discipline (DRE-1254)
# --------------------------------------------------------------------------
def test_a_failed_put_posts_no_receipt_and_takes_the_sweep_red():
    """ACCEPTANCE: PUT non-zero -> no receipt anywhere, `_write_failures`
    non-empty. A receipt for a refresh that never happened is the DRE-1254
    false-receipt class."""
    state = _sweep(_state([_pr()], put_rc=1))
    assert len(state["puts"]) == 1
    assert state["pr_comments"] == [] and state["card_comments"] == []
    assert reconcile._write_failures
    assert "2240" in reconcile._write_failures[0]


def test_a_failed_put_makes_main_exit_non_zero():
    """The write ledger is what turns the Actions run red for medic."""
    state = _state([_pr()], put_rc=1)
    mocks = {name: MagicMock() for name in (
        "drain_retiring_lanes", "unstick_conflicts", "retrigger_dead_heads",
        "flag_no_checks_prs", "flag_unowned_prs", "flag_unlanded_work",
        "fix_approved_but_red", "retry_dead_fix_runs",
        "restart_answered_blockers", "review_dependabot_prs",
        "recover_crashed_reviews", "check_dependabot_capacity",
        "escalate_aged_intake", "close_finished_epics", "promote_ready",
        "report_break_glass", "report_epic_growth", "report_fix_concurrency",
        "report_evicted_fix_runs",
    )}
    mocks["flag_stranded"] = MagicMock(return_value=set())
    mocks["active_cards"] = MagicMock(return_value=[])
    with patch.multiple(reconcile, **mocks), patch.object(
        reconcile.subprocess, "run", side_effect=_run_factory(state)
    ), patch.object(
        reconcile, "card_parked_for_human", return_value=False
    ), patch.object(reconcile.linear_ops, "cmd_comment"):
        with pytest.raises(SystemExit):
            reconcile.main()


def test_a_receipt_that_fails_after_a_202_is_recorded_and_the_put_not_repeated():
    """ACCEPTANCE: the refresh DID happen — record the failed post, never
    re-PUT. The next sweep reads behind_by 0 and does nothing."""
    state = _sweep(_state([_pr()], comment_rc=1))
    assert len(state["puts"]) == 1, "the PUT is not repeated after a bad post"
    assert reconcile._write_failures
    assert any("2240" in f for f in reconcile._write_failures)


# --------------------------------------------------------------------------
# 7. vendor boundary Q1 — which identity makes the write
# --------------------------------------------------------------------------
def test_the_put_and_the_receipt_run_under_the_default_app_token():
    """ACCEPTANCE + premortem Q1: the resulting `pull_request: synchronize`
    must initiate as agent-bureau-bot, which qa-review.yml's allowed_bots
    admits. A `GH_DISPATCH_TOKEN` (github.token) authored update fires NO
    workflows at all, which is the whole point of the refresh."""
    state = _state([_pr()])
    with patch.dict(os.environ, {"GH_DISPATCH_TOKEN": "ghs_dispatch",
                                 "GH_TOKEN": "ghs_app"}), patch.object(
        reconcile.subprocess, "run", side_effect=_run_factory(state)
    ), patch.object(
        reconcile, "card_parked_for_human", return_value=False
    ), patch.object(
        reconcile.linear_ops, "cmd_comment",
        side_effect=lambda i, *r: state["card_comments"].append((i, r[0])),
    ):
        reconcile.refresh_stale_merge_refs()
    for call in state["puts"] + state["pr_comments"]:
        env = call["env"]
        assert env is None or env.get("GH_TOKEN") != "ghs_dispatch", (
            "the update-branch PUT and its receipt run under the App token"
        )


# --------------------------------------------------------------------------
# 8. where the backstop runs
# --------------------------------------------------------------------------
def test_the_full_sweep_runs_the_rule_right_after_the_conflict_sweep():
    """The cron sweep is where `main` is green — and the rule sits beside the
    conflict sweep because they answer the same "why is this PR stuck"."""
    order: list = []
    mocks = {name: MagicMock(side_effect=lambda n=name: order.append(n))
             for name in (
                 "drain_retiring_lanes", "unstick_conflicts",
                 "refresh_stale_merge_refs", "retrigger_dead_heads",
                 "flag_no_checks_prs", "flag_unowned_prs", "flag_unlanded_work",
                 "fix_approved_but_red", "retry_dead_fix_runs",
                 "restart_answered_blockers", "review_dependabot_prs",
                 "recover_crashed_reviews", "check_dependabot_capacity",
                 "escalate_aged_intake")}
    mocks["flag_stranded"] = MagicMock(return_value=set())
    mocks["active_cards"] = MagicMock(return_value=[])
    for quiet in ("close_finished_epics", "promote_ready",
                  "report_break_glass", "report_epic_growth",
                  "report_fix_concurrency", "report_evicted_fix_runs"):
        mocks[quiet] = MagicMock()
    with patch.multiple(reconcile, **mocks):
        reconcile.main()
    assert "refresh_stale_merge_refs" in order
    assert order.index("refresh_stale_merge_refs") == \
        order.index("unstick_conflicts") + 1, (
        "immediately after the conflict sweep"
    )


def test_the_merge_event_sweep_does_not_run_the_rule():
    """`--conflicts-only` fires on the merge event, where `main`'s CI on the
    fixing commit has not finished — the decision would read `unevaluated`
    anyway, and a per-merge fan-out of update-branch PUTs is exactly the
    burst DRE-2049 warns about."""
    with patch.object(reconcile, "unstick_conflicts") as unstick, \
         patch.object(reconcile, "refresh_stale_merge_refs") as refresh:
        reconcile.main(conflicts_only=True)
    unstick.assert_called_once_with()
    refresh.assert_not_called()


# --------------------------------------------------------------------------
# 9. the contract with the module and the registry
# --------------------------------------------------------------------------
def test_the_tag_is_an_alias_never_a_second_literal():
    """The literal lives ONCE, in `stale_merge_ref.py` — the file the act
    registry declares as the emitter and reads the constant off."""
    assert reconcile.STALE_MERGE_REF_TAG is stale_merge_ref.REFRESH_TAG
    source = (Path(__file__).resolve().parent.parent
              / "scripts" / "reconcile.py").read_text(encoding="utf-8")
    assert f'"{stale_merge_ref.REFRESH_TAG}"' not in source, (
        "reconcile.py must not re-spell the tag as a literal"
    )


def test_the_registry_declares_the_act_the_way_the_contract_says():
    """The contract shared with DRE-3138 and DRE-3137, checked as data."""
    entry = pipeline_act.record(ACT)
    assert entry["tag"] == stale_merge_ref.REFRESH_TAG
    assert entry["kind"] == "recovery"
    assert entry["state"] == "dispatched"
    assert entry["next_actor"] == "qa-review.yml"
    assert entry["subscriber"] == "qa-review.yml"
    assert entry["discharges"] is None
    assert entry["adopted"] is True
    assert entry["emits"]["file"] == "scripts/stale_merge_ref.py"
    assert entry["emits"]["anchor"] == stale_merge_ref.ANCHOR_PHRASE
    for cited in ("#2240", "#2241"):
        assert cited in entry["why"], (
            "the row names the incident it was written from"
        )
