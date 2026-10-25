"""Scenario dependabot_flow — the live Dependabot vendor path (DRE-2100).

Consumes the REAL open Dependabot PR in the sandbox (the sandbox keeps a
stale pinned dependency so Dependabot keeps one filed — operator card
DRE-2097) and asserts the behaviors that produced most of the 2026-07-12
incidents:

  1. SELF-SKIP (DRE-2067): the pull_request review run triggered by actor
     dependabot[bot] gets GitHub's separate, EMPTY Dependabot secrets
     store — the reusable's job-if must skip it clean (a `skipped` check
     run on the head), never crash it red at the token mint. Judged on the
     RUN-attributed checks only: the head-bound record DRE-2291 added is
     review-named too, and its red means REQUEST_CHANGES, not a crash.
  2. DISPATCH ROUTE (DRE-2047/2053): the reconcile sweep's
     workflow_dispatch review — the real review path for dependabot heads
     — produces a REAL verdict bound to the PR's current head sha (any
     token; a genuine REQUEST_CHANGES is still a working route).
  3. RECEIPT LIFECYCLE (DRE-2049/2071): the sweep's worker-bot dispatch
     receipts are bounded per head sha — exactly one on the happy path,
     two only when a crashed review earned its bounded retry, and past
     the cap the sweep is looping (fail). A bound verdict with ZERO
     receipts on an untouched (single-parent) head means the receipted
     route was bypassed entirely (fail).

HONEST COVERAGE LIMITS — what this scenario can and cannot synthesize:

  * A Dependabot PR cannot be conjured by API, but a closed one can be
    brought back (DRE-7957). setup() labels the PR it consumes
    `harness-fixture` and tops its body with a do-not-close banner; when
    none is open it restores the labeled fixture's branch at its last
    head sha and reopens it as the harness's own identity, so closing it
    as a stale major is harmless. Only when no labeled fixture exists, or
    it was merged, does setup fail with the regeneration command named
    (`@dependabot recreate` on the newest closed Dependabot PR, or
    Dependabot's own schedule). The steady state keeps one open: the
    sandbox pin is chosen so the gate parks it as waiting-for-human, and
    an unmerged PR persists — so repeated back-to-back runs assert the
    SETTLED state cheaply instead of burning a critic run each time.
  * The crashed-review retry itself cannot be forced (we cannot crash
    the sandbox critic on demand). That path is unit-pinned in
    tests/test_dependabot_receipt_retry.py; live, it is observed only
    opportunistically (two receipts + a verdict = the retry recovered a
    real crash, and the scenario logs it).
  * `@dependabot rebase` is only vendor-guaranteed for commenters with
    write access; whether Dependabot obeys an App-bot's comment is not
    documented. The command is used ONLY for a DIRTY PR (where reconcile
    deliberately defers to Dependabot's own rebase), and a non-response
    fails with the manual fallback named.
  * The GATE's posture on this PR (major/unprovable → human;
    minor/patch → auto-merge) is gate_paths' business — asserted there.
"""

from __future__ import annotations

from harness import framework
from harness.framework import (
    ScenarioFailure,
    find_real_dependabot_pr,
    same_bot,
    sweep_leftovers,
    verdict_state,
)
from publish_review_check import CHECK_NAME as HEAD_BOUND_CHECK_NAME

# The reconcile sweep's receipt contract (reconcile.py posts these; the
# scenario only READS them). Literals repeated here because reconcile.py
# is not importable without a live env (REPO et al.) — the parity is
# unit-pinned against reconcile's own constants in
# tests/test_harness_dependabot_flow.py, so drift turns the suite red.
DISPATCH_TAG = "dependabot-review-dispatch"
RECEIPT_CAP = 2

# The sandbox reconcile stub is cron-paced (~15 min): the verdict wait
# budgets one full interval ON TOP of the critic's own budget.
RECONCILE_CRON_ALLOWANCE = 1200.0
# Dependabot usually reacts to a rebase command within a couple minutes.
REBASE_TIMEOUT = 600.0
# The self-skip run concludes in seconds (its job-if is evaluated before
# any step); this only needs to cover runner queueing.
CHECKS_TIMEOUT = 600.0

# A review RUN that concluded red = the DRE-2047/2067 crash. Only the
# run-attributed checks are read against this (see review_check_runs) — the
# head-bound record's conclusion is a verdict, not a liveness report.
RED_CONCLUSIONS = frozenset({"failure", "timed_out"})

NO_PR_GUIDANCE = (
    "no open genuine Dependabot PR in the sandbox — one cannot be conjured "
    "by API. Regenerate: comment `@dependabot recreate` on the newest "
    "closed Dependabot PR (or wait for the schedule; the sandbox's stale "
    "pin makes Dependabot re-file), then re-run the harness."
)

# The sandbox's standing Dependabot PR is a semver-major bump nobody means
# to merge, so it reads as stale debt and gets closed (2026-09-24 and
# 2026-10-24, the second turning main red for four hours — DRE-7957). The
# label is how setup() knows a closed one is its own to reopen; the banner
# is for the person about to close it.
FIXTURE_LABEL = "harness-fixture"
FIXTURE_BANNER = (
    "HARNESS FIXTURE — the integration harness needs this PR open. "
    "Do not close or merge."
)


def _labels(pr) -> list[str]:
    return [(label or {}).get("name") or "" for label in pr.get("labels") or []]


def find_closed_fixture(prs):
    """The closed genuine Dependabot PR carrying FIXTURE_LABEL that setup()
    may reopen, from REST list shapes — newest first, an unmerged one over a
    merged one (a merged PR never reopens). None when no labeled fixture is
    closed. "Genuine" is find_real_dependabot_pr's own test."""
    labeled = [
        pr
        for pr in prs
        if pr.get("state") == "closed"
        and FIXTURE_LABEL in _labels(pr)
        and framework.find_real_dependabot_pr([pr]) is not None
    ]
    labeled.sort(
        key=lambda pr: (
            not (pr.get("merged_at") or pr.get("merged")),
            pr.get("number", 0),
        ),
        reverse=True,
    )
    return labeled[0] if labeled else None


def rebase_command() -> str:
    return "@dependabot rebase"


def review_check_runs(check_runs) -> list:
    """The review RUNS among a head's check runs, by job name — the checks
    that report whether the event-driven run survived.

    Name matching is fine HERE — this is a harness observation inside a
    sandbox whose workflows we author, not a security gate (the gate's own
    review-run exclusion is by verified origin, DRE-1994, which needs the
    actions:read permission neither harness App has).

    The head-bound `QA critic review` record is EXCLUDED, because it is not
    a run at all (DRE-2291): publish_review_check.py writes it from inside a
    review that reached its end, and its conclusion reports the VERDICT —
    `REQUEST_CHANGES` is a red check on a review route that worked
    perfectly. reconcile draws the same line on this surface from the other
    side (`_authoritative_review_checks`), and refuses to call a head
    crashed while a verdict binds it. What the critic SAID is the verdict
    wait's question, one clause below, which can tell a rejection from a
    crash where a check conclusion cannot.
    """
    return [
        r for r in check_runs
        if "review" in ((r.get("name") or "").lower())
        and (r.get("name") or "") != HEAD_BOUND_CHECK_NAME
    ]


def receipt_count(comments, worker_login: str, head_sha: str) -> int:
    """Worker-bot dispatch receipts covering `head_sha`, mirroring
    reconcile.dependabot_receipt_count over REST comment shapes: forged
    authors are invisible (DRE-1998), superseded-sha receipts don't count
    (a rebase re-arms the budget)."""
    return sum(
        1
        for c in comments
        if same_bot(((c.get("user") or {}).get("login")), worker_login)
        and DISPATCH_TAG in (c.get("body") or "")
        and head_sha in (c.get("body") or "")
    )


class DependabotFlow(framework.Scenario):
    name = "dependabot_flow"

    # ── setup: a clean sandbox + the vendor's PR located ─────────────────
    def setup(self, ctx):
        ctx.state["swept"] = sweep_leftovers(ctx.gh, ctx.repo, ctx.namespace, ctx.log)
        ctx.state["reopened"] = False
        pr = find_real_dependabot_pr(ctx.gh.list_open_prs(ctx.repo))
        if pr is None:
            pr = self._reopen_fixture(ctx)
        detail = ctx.gh.get_pr(ctx.repo, pr["number"])
        self._mark_fixture(ctx, detail)
        ctx.state["number"] = detail["number"]
        ctx.state["head"] = detail["head"]["sha"]
        ctx.state["dirty"] = detail.get("mergeable_state") == "dirty"
        ctx.log(
            f"[{self.name}] consuming Dependabot PR #{detail['number']} "
            f"({detail['head']['ref']}@{ctx.state['head']}"
            f"{', DIRTY' if ctx.state['dirty'] else ''})"
        )

    def _reopen_fixture(self, ctx) -> dict:
        """No open Dependabot PR: bring the labeled fixture back (DRE-7957).

        Restores its head branch at the last head sha the closed PR records
        (Dependabot deletes the branch seconds after a close) and reopens
        it, never needing Dependabot to re-file. Every write goes out as
        `ctx.gh`, the harness's own identity — the sandbox is public, so a
        person's name must never appear on it. Raises NO_PR_GUIDANCE when no
        labeled fixture exists, and names the merge when it was merged.

        The reopen is itself a `pull_request` event (the sandbox stub lists
        `reopened`) whose actor is the worker bot, not dependabot[bot], so
        the reusable reviews it for real. `ctx.state["reopened"]` is how
        verify() knows to wait that review out and to accept the verdict it
        posts with no dispatch receipt before it.
        """
        fixture = find_closed_fixture(ctx.gh.list_prs(ctx.repo, state="closed"))
        if fixture is None:
            raise ScenarioFailure(NO_PR_GUIDANCE)
        number = fixture["number"]
        if fixture.get("merged_at") or fixture.get("merged"):
            raise ScenarioFailure(
                f"the harness fixture #{number} was merged, and a merged PR "
                f"cannot be reopened — {NO_PR_GUIDANCE}"
            )
        ref, sha = fixture["head"]["ref"], fixture["head"]["sha"]
        if ref not in ctx.gh.matching_refs(ctx.repo, ref):
            ctx.gh.create_ref(ctx.repo, ref, sha)
        ctx.gh.reopen_pr(ctx.repo, number)
        ctx.state["reopened"] = True
        # Re-applied although the fixture was found by it: the reopen is the
        # moment it was just closed as debt, so it is marked again then.
        ctx.gh.add_labels(ctx.repo, number, [FIXTURE_LABEL])
        ctx.log(f"[{self.name}] reopened fixture #{number}")
        return fixture

    def _mark_fixture(self, ctx, pr) -> None:
        """Label and banner the PR this run consumes, each only when it is
        missing, so a marked fixture costs no write at all."""
        number = pr["number"]
        if FIXTURE_LABEL not in _labels(pr):
            ctx.gh.add_labels(ctx.repo, number, [FIXTURE_LABEL])
        body = pr.get("body") or ""
        if not body.startswith(FIXTURE_BANNER):
            ctx.gh.update_pr_body(
                ctx.repo, number,
                f"{FIXTURE_BANNER}\n\n{body}" if body else FIXTURE_BANNER,
            )

    # ── exercise: only a DIRTY PR needs vendor help ──────────────────────
    def exercise(self, ctx):
        if not ctx.state["dirty"]:
            return  # consume the PR as-found; nothing to regenerate
        # Reconcile deliberately skips DIRTY dependabot PRs — Dependabot
        # rebases its own conflicts. Ask it to (the card's on-demand
        # regeneration mechanism) and wait for the fresh head.
        number, old_head = ctx.state["number"], ctx.state["head"]
        ctx.gh.create_comment(ctx.repo, number, rebase_command())
        ctx.log(f"[{self.name}] PR #{number} is DIRTY — posted {rebase_command()!r}")

        def poll_rebased():
            pr = ctx.gh.get_pr(ctx.repo, number)
            return pr["head"]["sha"] if pr["head"]["sha"] != old_head else None

        try:
            ctx.state["head"] = ctx.wait(
                f"dependabot rebasing PR #{number}",
                poll_rebased,
                timeout=REBASE_TIMEOUT,
            )
        except framework.HarnessTimeout as e:
            raise ScenarioFailure(
                f"{e}. Dependabot did not react to the App-bot's rebase "
                "command (not vendor-guaranteed — a documented coverage "
                "limit); rebase the PR by hand or via a write-access user, "
                "then re-run the harness."
            ) from e
        ctx.log(f"[{self.name}] rebased to {ctx.state['head']}")

    # ── verify: self-skip clean → receipted route → bounded receipts ─────
    def verify(self, ctx):
        number = ctx.state["number"]
        reopened = ctx.state.get("reopened", False)
        qa_gh = ctx.gh_qa or ctx.gh
        tracker = {"head": ctx.state["head"], "state": "none", "detail": ""}
        checks_timeout = CHECKS_TIMEOUT
        if reopened:
            # The reopen started a real review as the worker bot (see
            # _reopen_fixture): one of the runs below takes a critic's time,
            # not a self-skip's seconds.
            checks_timeout += ctx.verdict_timeout
            ctx.log(
                f"[{self.name}] fixture reopened this run — waiting out the "
                "review the reopen started (worker-bot actor, normal secrets)"
            )

        # 1. SELF-SKIP: the dependabot-actor pull_request review run on
        # this head must have concluded — skipped, never red.
        def poll_review_runs():
            runs = review_check_runs(qa_gh.list_check_runs(ctx.repo, tracker["head"]))
            if runs and all(r.get("status") == "completed" for r in runs):
                return runs
            return None

        try:
            review_runs = ctx.wait(
                f"the review check runs on {tracker['head']} to conclude",
                poll_review_runs,
                timeout=checks_timeout,
            )
        except framework.HarnessTimeout as e:
            raise ScenarioFailure(
                f"{e}; a dependabot-pushed head with no concluded review "
                "check run means the review stub never fired for it"
            ) from e
        red = [
            r for r in review_runs
            if (r.get("conclusion") or "") in RED_CONCLUSIONS
        ]
        if red:
            raise ScenarioFailure(
                f"review RUN crashed red on the dependabot head "
                f"{tracker['head']}: "
                f"{[(r.get('name'), r.get('conclusion')) for r in red]} — "
                "the DRE-2047/2067 class (empty Dependabot secrets store "
                "must self-skip, never crash)"
            )
        head_commit = ctx.gh.get_commit(ctx.repo, tracker["head"])
        gate_updated = len(head_commit.get("parents") or []) >= 2
        if gate_updated:
            # A merge-commit head had its base merged in by a bot (the fix
            # agent since DRE-2416, the gate's own update-branch before
            # it): the synchronize actor is a bot with NORMAL secrets, so a
            # success review here is the DRE-2037 path, not a miswire.
            ctx.log(
                f"[{self.name}] head {tracker['head'][:8]} is a base-merge "
                "commit — self-skip not observable on it (bot-actor "
                "synchronize reviews run with normal secrets)"
            )
        elif not any(r.get("conclusion") == "skipped" for r in review_runs):
            raise ScenarioFailure(
                f"no skipped review run on the dependabot-pushed head "
                f"{tracker['head']} "
                f"({[(r.get('name'), r.get('conclusion')) for r in review_runs]}) "
                "— a 'success' here would mean the event-driven run REVIEWED "
                "with the empty Dependabot secrets store, which cannot "
                "happen; the self-skip guard (DRE-2067) is miswired"
            )
        ctx.log(f"[{self.name}] self-skip clean on {tracker['head'][:8]}")

        # 2. DISPATCH ROUTE: a real verdict bound to the CURRENT head.
        # Dependabot may rebase spontaneously mid-wait — follow the head.
        def poll_verdict():
            pr = ctx.gh.get_pr(ctx.repo, number)
            if pr["head"]["sha"] != tracker["head"]:
                tracker["head"] = pr["head"]["sha"]
                ctx.log(
                    f"[{self.name}] head moved to {tracker['head'][:8]} "
                    "(dependabot re-armed) — re-targeting the verdict wait"
                )
            comments = ctx.gh.list_comments(ctx.repo, number)
            tracker["comments"] = comments
            receipts = receipt_count(comments, ctx.worker_login, tracker["head"])
            if receipts > RECEIPT_CAP:
                raise ScenarioFailure(
                    f"{receipts} dispatch receipts for head "
                    f"{tracker['head'][:8]} exceed the cap ({RECEIPT_CAP}) — "
                    "the sweep is looping instead of stopping on the "
                    "fail-loudly rail (DRE-2071 bound broken)"
                )
            state, detail = verdict_state(comments, ctx.qa_login, tracker["head"])
            tracker["state"], tracker["detail"] = state, detail
            return state not in ("none", "neutral", "stale") or None

        try:
            ctx.wait(
                f"a sha-bound verdict on PR #{number} via the reconcile "
                "dispatch route",
                poll_verdict,
                timeout=ctx.verdict_timeout + RECONCILE_CRON_ALLOWANCE,
            )
        except framework.HarnessTimeout as e:
            raise ScenarioFailure(
                f"{e}; last verdict state: {tracker['state']} "
                f"({tracker['detail']}) — the head is frozen without a "
                "review, the exact class the outcome-aware receipts "
                "(DRE-2071) exist to prevent"
            ) from e
        ctx.log(
            f"[{self.name}] bound verdict on {tracker['head'][:8]}: "
            f"{tracker['state']}"
        )

        # 3. RECEIPT LIFECYCLE on the verdict-bearing head.
        receipts = receipt_count(
            tracker["comments"], ctx.worker_login, tracker["head"]
        )
        if receipts == 0:
            commit = ctx.gh.get_commit(ctx.repo, tracker["head"])
            if len(commit.get("parents") or []) >= 2:
                ctx.log(
                    f"[{self.name}] no dispatch receipt on the gate-updated "
                    "head — expected: its verdict came from the qa-actor "
                    "synchronize review (DRE-2037 path), not the dispatch "
                    "route"
                )
            elif reopened:
                ctx.log(
                    f"[{self.name}] no dispatch receipt on the reopened "
                    "fixture's head — expected: its verdict came from the "
                    "review the harness's own reopen started, not the "
                    "dispatch route (DRE-7957)"
                )
            else:
                raise ScenarioFailure(
                    f"bound verdict on {tracker['head'][:8]} with ZERO "
                    "dispatch receipts — the verdict did not come via the "
                    "receipted reconcile route this scenario exists to prove"
                )
        elif receipts == 1:
            ctx.log(f"[{self.name}] exactly one dispatch receipt (happy path)")
        else:
            ctx.log(
                f"[{self.name}] {receipts} receipts + a verdict — a crashed "
                "dispatched review earned its bounded retry live (DRE-2071)"
            )

    # ── cleanup: the vendor's PR is NOT ours to clean ────────────────────
    def cleanup(self, ctx):
        # This scenario creates no branches, files, or PRs — and it must
        # NEVER close the Dependabot PR or delete its branch (that is the
        # sandbox's standing fixture; closing it would not even end it,
        # vendor-boundaries Q3). Only prove the sandbox stays usable.
        _, tip = ctx.gh.default_branch(ctx.repo)
        if not tip:
            raise ScenarioFailure("default branch has no readable tip sha")
        # This run's own namespace only: the other lane's harness PRs
        # are open because that run is still using them (DRE-3075).
        leftovers = framework.leftover_pr_numbers(
            ctx.gh, ctx.repo, ctx.namespace
        )
        if leftovers:
            raise ScenarioFailure(
                f"open harness PRs left behind after cleanup: {leftovers}"
            )
        ctx.log(f"[{self.name}] cleanup complete; default branch @{tip}")


SCENARIO = DependabotFlow()
