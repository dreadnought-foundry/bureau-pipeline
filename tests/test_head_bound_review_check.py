"""RED-first tests for DRE-2291 — a manual re-review must land ON THE PR.

THE BUG (verified live, agent-bureau PR #2007, head 14934d98, 2026-08-07):
qa-review runs on two triggers. `pull_request` runs are attributed to the
PR's head, so their `call / review` check run lands on the head commit.
`workflow_dispatch` runs — the DOCUMENTED manual re-run, and the remedy
reconcile itself dispatches (DRE-2047, DRE-2282) — are attributed to the
DEFAULT BRANCH. GitHub creates a run's check runs against the run's own
head sha, so a dispatched review writes `call / review` onto main's tip and
NOTHING onto the PR head. Proof from the live API:

    run 31196775365 / 31196779419 / 31196783792  (event=workflow_dispatch)
      head_branch=main  head_sha=87eb0999…
      → `call / review` = success, on commit 87eb0999 (main), not on any PR

    PR #2007's only branch-attributed run is 31139346345 (event=pull_request,
    conclusion CANCELLED — killed by `concurrency: cancel-in-progress` when
    the manual dispatch was issued over it). Its `call / review` stayed
    CANCELLED on head 14934d98 even after a dispatched re-review posted a
    genuine `VERDICT: APPROVE @14934d98…`.

So a manual re-review LOOKS successful — a fresh APPROVE comment appears —
while the only per-head record of the review still says the review died.
Every reader of the head's checks is misled: the console's merge view, a
human deciding whether to merge by hand, `gh pr checks`, GitHub's
mergeStateStatus, and reconcile's own crashed-review sweep, which reads
`_review_checks_at_head` and therefore cannot see a dispatched review at
all — the recovery it performs can never clear the signal it reacts to.

Compounding it (the DRE-1826 hazard, already paid for once): today's
`call / review` conclusion reports whether the critic JOB finished, not what
the critic DECIDED. A REQUEST_CHANGES verdict shows a GREEN check.

FIX UNDER TEST — the review publishes its own check run, explicitly bound to
the head sha it reviewed (`steps.pr.outputs.sha`, captured before review
starts, DRE-1990), via scripts/publish_review_check.py:

  1. ONE check run per head, named so the merge gate and the reconcile sweep
     already recognise it as a review check. Created if absent, UPDATED in
     place if present — so a workflow_dispatch re-review overwrites exactly
     what a pull_request run would have written, and re-reviews can never
     accumulate contradictory records on one commit.
  2. The conclusion states the VERDICT, not the job's liveness: APPROVE →
     success, REQUEST_CHANGES → failure, crashed critic → failure. A crashed
     dispatched review therefore now leaves a crash record AT THE HEAD, which
     is the only place reconcile.recover_crashed_reviews looks.
  3. Written with a token that actually carries checks:write. The qa-bot App
     does NOT — `GET /apps/agent-bureau-qa-bot` reports checks:read (the
     dispatch App, agent-bureau-bot, reports checks:write). One extra API
     write per review is negligible against the DRE-1921 quota lesson, which
     was about the critic's whole review workload sharing the relay's bucket.
  4. The dependabot self-skip on the job `if` (DRE-2067) is untouched.

NOT changed, deliberately: the merge gate still requires a qa-bot-authored
`VERDICT: APPROVE` bound to the current head (merge_gate.py condition 2).
This check can only ever BLOCK a merge, never grant one.

Run: cd bureau-pipeline && python3 -m pytest tests/ -v
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/atlas")
os.environ.setdefault("REPO_SLUG", "atlas")
os.environ.setdefault("GH_TOKEN", "x")

import publish_review_check as prc  # noqa: E402
import reconcile  # noqa: E402

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"

REPO = "dreadnought-foundry/atlas"
SHA = "1" * 40
OTHER_SHA = "2" * 40

APPROVE = "VERDICT: APPROVE\n\n## Summary\nShips what the card asked for.\n"
REJECT = (
    "VERDICT: REQUEST_CHANGES\n\n## Summary\nThe list still looks empty.\n"
)


# ==========================================================================
# 1. The verdict, not the job's liveness, decides the check
# ==========================================================================
class TestVerdictDecidesTheCheck:
    def test_approve_is_a_green_check(self):
        conclusion, title, _ = prc.decide(real=True, verdict=APPROVE)
        assert conclusion == "success"
        assert "APPROVE" in title

    def test_request_changes_is_a_red_check_not_a_false_green(self):
        """The DRE-1826 hazard, in check form: two PRs were hand-merged on a
        GREEN `call / review` whose verdict was REQUEST_CHANGES, shipping a
        data-integrity regression. A check that reports the VERDICT must go
        red when the verdict rejects."""
        conclusion, title, _ = prc.decide(real=True, verdict=REJECT)
        assert conclusion == "failure", (
            "a REQUEST_CHANGES verdict must not render as a green check — "
            "that false green is what got two bad PRs hand-merged (DRE-1825)"
        )
        assert "REQUEST_CHANGES" in title

    def test_crashed_critic_is_a_red_check_the_sweep_can_recognise(self):
        """A crashed dispatched review left NOTHING at the head before this
        fix, so recover_crashed_reviews (which reads only head checks) was
        blind to it. The conclusion must be one the sweep already treats as
        a crash."""
        conclusion, title, summary = prc.decide(real=False, verdict="")
        assert conclusion in reconcile._REVIEW_CRASH_CONCLUSIONS, (
            "a crashed review's check must use a conclusion "
            "recover_crashed_reviews already reads as crashed — otherwise "
            "the DRE-2282 backstop still cannot see a dispatched crash"
        )
        assert "not a code rejection" in summary.lower(), (
            "DRE-1916 discipline: an infra crash is never framed as a "
            "rejection of the code"
        )
        assert "crash" in title.lower()

    def test_missing_verdict_line_fails_closed(self):
        """`real` came from check_critic_result.py, but a verdict body with
        no parseable VERDICT: line must never be guessed at."""
        conclusion, _, _ = prc.decide(real=True, verdict="## Summary\nlgtm\n")
        assert conclusion == "failure"

    def test_check_name_is_review_named_so_existing_readers_see_it(self):
        """reconcile._review_checks_at_head and
        reconcile.fix_approved_but_red both classify a check as review-owned
        by `name.endswith("review")`. The new check must join that class, or
        approved-but-red would dispatch a fix agent over the critic's own
        red check."""
        assert prc.CHECK_NAME.endswith("review"), prc.CHECK_NAME
        assert prc.CHECK_NAME != "call / review", (
            "the head-bound check must be distinct from the run's own "
            "`call / review`, which stays event-attributed"
        )


# ==========================================================================
# 2. Bound to the reviewed head, one record per head
# ==========================================================================
def _gh_stub(state):
    """subprocess.run stub for exactly the gh calls the publisher makes:
    the per-head lookup by check name, then one POST or one PATCH whose
    JSON payload rides stdin (`gh api --input -`)."""

    def fake_run(argv, **kwargs):
        assert argv[0] == "gh", f"unexpected call: {argv}"
        state["calls"].append(list(argv[1:]))
        record = {"argv": list(argv), "input": kwargs.get("input") or "{}"}
        if "-X" not in argv:  # the GET lookup
            return SimpleNamespace(
                returncode=state.get("lookup_rc", 0),
                stdout=state.get("lookup", "[]"),
                stderr="",
            )
        state[argv[argv.index("-X") + 1].lower()].append(record)
        rc = state.get("write_rc", 0)
        return SimpleNamespace(returncode=rc, stdout="{}", stderr="boom" if rc else "")

    return fake_run


def _publish(lookup="[]", real=True, verdict=APPROVE, sha=SHA, **kw):
    state = {"calls": [], "post": [], "patch": [], "lookup": lookup, **kw}
    with patch.object(prc.subprocess, "run", side_effect=_gh_stub(state)), \
         patch.object(prc.time, "sleep", lambda *_: None):
        state["rc"] = prc.publish(
            repo=REPO, sha=sha, real=real, verdict=verdict,
            run_url="https://example/run/1", event="workflow_dispatch",
        )
    return state


def _body(record):
    """The JSON payload the publisher handed `gh api --input -` on stdin."""
    return json.loads(record["input"])


class TestBoundToTheReviewedHead:
    def test_check_is_created_on_the_reviewed_sha(self):
        """ACCEPTANCE: the head sha the critic reviewed, never the sha the
        RUN happens to be attributed to. This is the whole bug."""
        state = _publish()
        assert len(state["post"]) == 1 and not state["patch"]
        payload = _body(state["post"][0])
        assert payload["head_sha"] == SHA, (
            "the check must be bound to the reviewed PR head — a "
            "workflow_dispatch run's own head is the default branch"
        )
        assert payload["name"] == prc.CHECK_NAME
        assert payload["status"] == "completed"
        assert payload["conclusion"] == "success"

    def test_a_dispatched_rereview_updates_the_same_check(self):
        """ACCEPTANCE: the manual door must update the record a
        pull_request run would have written, not add a second one. A head
        carrying two contradictory review checks is the stranding bug with
        extra steps."""
        state = _publish(
            lookup=json.dumps([{"id": 77}]), verdict=REJECT,
        )
        assert not state["post"], "an existing check at this head is UPDATED"
        assert len(state["patch"]) == 1
        argv = state["patch"][0]["argv"]
        assert any("/check-runs/77" in a for a in argv), argv
        assert _body(state["patch"][0])["conclusion"] == "failure"

    def test_the_lookup_is_scoped_to_this_head_and_this_name(self):
        state = _publish()
        get = state["calls"][0]
        assert any(f"commits/{SHA}/check-runs" in a for a in get), get
        assert any("check_name=" in a for a in get), (
            "the lookup must ask GitHub for THIS check name at THIS head — "
            "scanning every check on the commit invites a name collision"
        )

    def test_never_touches_any_other_commit(self):
        state = _publish(sha=OTHER_SHA)
        assert _body(state["post"][0])["head_sha"] == OTHER_SHA
        for call in state["calls"]:
            assert not any(SHA in a for a in call), (
                "the publisher must only ever write the sha it was given"
            )

    def test_summary_names_the_triggering_event_and_run(self):
        """The self-inflicted cancellation stays DETECTABLE: whoever reads
        the head can tell which run and which trigger produced this
        outcome."""
        payload = _body(_publish()["post"][0])
        text = payload["output"]["summary"] + payload["output"]["title"]
        assert "workflow_dispatch" in text
        assert "https://example/run/1" in text
        assert SHA in text


class TestFailsLoudlyNeverSilently:
    def test_a_transient_write_failure_is_retried(self):
        state = _publish(write_rc=1)
        assert len(state["post"]) > 1, (
            "the head-bound check is load-bearing — retry through a blip "
            "the way the verdict comment does"
        )

    def test_a_persistent_write_failure_exits_nonzero(self):
        """No silent killers: if the check cannot be written, the review run
        must go red rather than leave the head unmarked again."""
        assert _publish(write_rc=1)["rc"] != 0

    def test_an_unreadable_lookup_never_invents_an_update(self):
        """DRE-2034 read discipline: a 403 parsed as emptiness must not be
        read as 'no existing check' in a way that loses data — creating is
        the safe direction, but the failure must still be visible."""
        state = _publish(lookup="", lookup_rc=1)
        assert not state["patch"], "never PATCH an id we could not read"


# ==========================================================================
# 3. Workflow wiring — the step exists, on every trigger, with a real token
# ==========================================================================
def _qa_review() -> dict:
    return yaml.safe_load((WORKFLOWS / "qa-review.yml").read_text())


def _steps() -> list[dict]:
    return _qa_review()["jobs"]["review"]["steps"]


def _step(fragment: str) -> dict:
    for s in _steps():
        if fragment in (s.get("run") or "") or fragment in (s.get("name") or ""):
            return s
    raise AssertionError(
        f"no step in qa-review.yml matches {fragment!r} — "
        "the head-bound review check is not wired"
    )


class TestWorkflowWiring:
    def test_the_review_publishes_a_head_bound_check(self):
        step = _step("publish_review_check.py")
        assert "${{ steps.pr.outputs.sha }}" in step["run"], (
            "the check must be bound to the sha Resolve PR captured "
            "(DRE-1990), not to github.sha — on a workflow_dispatch run "
            "github.sha IS the default branch, which is the bug"
        )

    def test_it_runs_on_every_trigger_not_just_pull_request(self):
        """ACCEPTANCE: a workflow_dispatch re-review must write the check.
        The guard may only be the review decision + the always/cancelled
        discipline the neighbouring verdict step uses."""
        cond = " ".join((_step("publish_review_check.py").get("if") or "").split())
        assert "steps.decide.outputs.review == 'true'" in cond
        assert "event_name" not in cond, (
            f"nothing may scope this step to one trigger — that IS the "
            f"bug being fixed. Got: {cond!r}"
        )
        assert "always()" in cond and "!cancelled()" in cond, (
            "a crashed critic must still stamp its crash on the head"
        )

    def test_it_runs_after_the_verdict_is_posted(self):
        names = [s.get("name", "") for s in _steps()]
        post = next(i for i, n in enumerate(names) if "Post verdict" in n)
        pub = next(
            i for i, s in enumerate(_steps())
            if "publish_review_check.py" in (s.get("run") or "")
        )
        assert pub > post, (
            "the comment is the merge gate's credential and posts first; "
            "the check is the head-bound mirror of the same outcome"
        )

    def test_it_uses_a_token_that_actually_has_checks_write(self):
        """The qa-bot App reports checks:READ (`GET /apps/agent-bureau-qa-bot`,
        verified 2026-08-07), so steps.app.outputs.token cannot create a
        check run. The dispatch App reports checks:write; its secrets already
        reach every consuming repo via `secrets: inherit` (org-level,
        visibility all), so this needs no stub or App-permission change."""
        step = _step("publish_review_check.py")
        token = step["env"]["GH_TOKEN"]
        assert "steps.app.outputs.token" not in token, (
            "the qa-bot token has checks:read only — writing the check with "
            "it 403s on every review"
        )
        mint = _step("checks:write")
        assert mint["uses"].startswith("actions/create-github-app-token@")
        assert "BUREAU_APP_ID" in yaml.dump(mint["with"])
        assert f"steps.{mint['id']}.outputs.token" in token

    def test_the_dependabot_self_skip_is_preserved(self):
        """DRE-2067: the job gate must still lead with the actor guard, or
        every fleet dependabot PR shows a red crashed review again."""
        guard = (
            "(github.event_name != 'pull_request' || "
            "github.actor != 'dependabot[bot]')"
        )
        cond = " ".join((_qa_review()["jobs"]["review"].get("if") or "").split())
        assert cond == guard or cond.startswith(guard + " &&"), cond

    def test_the_check_write_cannot_grant_a_merge(self):
        """Guardrail: the merge gate's review credential stays the qa-bot
        verdict COMMENT bound to the head (merge_gate.py condition 2). This
        PR must not add the check to any merge-granting path."""
        gate = (Path(__file__).resolve().parents[1]
                / "scripts" / "merge_gate.py").read_text()
        assert prc.CHECK_NAME not in gate, (
            "merge_gate.py must not learn this check's name — a check the "
            "pipeline writes about itself is never a merge credential"
        )


# ==========================================================================
# 4. The sweep reads the head-bound check as authoritative
# ==========================================================================
class TestSweepReadsTheAuthoritativeCheck:
    def test_dre_2282_already_covered_cancelled(self):
        """Documenting the answer to 'does the crashed-review path handle a
        CANCELLED review?': yes, since DRE-2282. `concurrency:
        cancel-in-progress` makes cancelled the single most likely crash
        conclusion, so pin it."""
        assert "cancelled" in reconcile._REVIEW_CRASH_CONCLUSIONS
        assert "failure" in reconcile._REVIEW_CRASH_CONCLUSIONS
        assert "timed_out" in reconcile._REVIEW_CRASH_CONCLUSIONS

    def test_head_bound_check_wins_over_the_runs_own_check(self):
        """A dispatched re-review leaves the superseded pull_request run's
        `call / review` CANCELLED on the head forever. Once a head carries
        the authoritative check, THAT is the review's outcome — the stale
        run check must not keep speaking for the head."""
        checks = [
            ("completed", "cancelled", "call / review"),
            ("completed", "success", prc.CHECK_NAME),
        ]
        assert reconcile._authoritative_review_checks(checks) == [
            ("completed", "success", prc.CHECK_NAME)
        ]

    def test_without_the_head_bound_check_nothing_changes(self):
        """Every head reviewed before this ships keeps today's behaviour."""
        checks = [("completed", "cancelled", "call / review")]
        assert reconcile._authoritative_review_checks(checks) == checks

    def test_legacy_two_element_payloads_still_parse(self):
        """`_review_checks_at_head` gained a name element; the existing
        callers and fixtures pass pairs."""
        assert reconcile._authoritative_review_checks(
            [("completed", "failure")]
        ) == [("completed", "failure")]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
