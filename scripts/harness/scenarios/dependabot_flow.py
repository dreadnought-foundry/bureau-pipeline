"""Scenario dependabot_flow — the live Dependabot vendor path (DRE-2100).

Consumes the REAL open Dependabot PR in the sandbox (the sandbox keeps a
stale pinned dependency so Dependabot keeps one filed — operator card
DRE-2097) and asserts the behaviors that produced most of the 2026-07-12
incidents:

  1. SELF-SKIP (DRE-2067): the pull_request review run triggered by actor
     dependabot[bot] gets GitHub's separate, EMPTY Dependabot secrets
     store — the reusable's job-if must skip it clean (a `skipped` check
     run on the head), never crash it red at the token mint.
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

  * A Dependabot PR cannot be conjured by API. If none is open, setup
    fails with the regeneration command named (`@dependabot recreate` on
    the newest closed Dependabot PR, or Dependabot's own schedule). The
    steady state keeps one open: the sandbox pin is chosen so the gate
    parks it as waiting-for-human, and an unmerged PR persists — so
    repeated back-to-back runs assert the SETTLED state cheaply instead
    of burning a critic run each time.
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
    wait_until,
)

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

# A review-shaped check run that concluded red = the DRE-2047/2067 crash.
RED_CONCLUSIONS = frozenset({"failure", "timed_out"})

NO_PR_GUIDANCE = (
    "no open genuine Dependabot PR in the sandbox — one cannot be conjured "
    "by API. Regenerate: comment `@dependabot recreate` on the newest "
    "closed Dependabot PR (or wait for the schedule; the sandbox's stale "
    "pin makes Dependabot re-file), then re-run the harness."
)


def rebase_command() -> str:
    return "@dependabot rebase"


def review_check_runs(check_runs) -> list:
    """The review-stage check runs among a head's check runs, by job name.
    Name matching is fine HERE — this is a harness observation inside a
    sandbox whose workflows we author, not a security gate (the gate's own
    review-run exclusion is by verified origin, DRE-1994, which needs the
    actions:read permission neither harness App has)."""
    return [r for r in check_runs if "review" in ((r.get("name") or "").lower())]


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
        ctx.state["swept"] = sweep_leftovers(ctx.gh, ctx.repo, ctx.log)
        pr = find_real_dependabot_pr(ctx.gh.list_open_prs(ctx.repo))
        if pr is None:
            raise ScenarioFailure(NO_PR_GUIDANCE)
        detail = ctx.gh.get_pr(ctx.repo, pr["number"])
        ctx.state["number"] = detail["number"]
        ctx.state["head"] = detail["head"]["sha"]
        ctx.state["dirty"] = detail.get("mergeable_state") == "dirty"
        ctx.log(
            f"[{self.name}] consuming Dependabot PR #{detail['number']} "
            f"({detail['head']['ref']}@{ctx.state['head']}"
            f"{', DIRTY' if ctx.state['dirty'] else ''})"
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
            ctx.state["head"] = wait_until(
                f"dependabot rebasing PR #{number}",
                poll_rebased,
                timeout=REBASE_TIMEOUT,
                interval=ctx.poll_interval,
                clock=ctx.clock,
                sleep=ctx.sleep,
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
        qa_gh = ctx.gh_qa or ctx.gh
        tracker = {"head": ctx.state["head"], "state": "none", "detail": ""}

        # 1. SELF-SKIP: the dependabot-actor pull_request review run on
        # this head must have concluded — skipped, never red.
        def poll_review_runs():
            runs = review_check_runs(qa_gh.list_check_runs(ctx.repo, tracker["head"]))
            if runs and all(r.get("status") == "completed" for r in runs):
                return runs
            return None

        try:
            review_runs = wait_until(
                f"the review check runs on {tracker['head']} to conclude",
                poll_review_runs,
                timeout=CHECKS_TIMEOUT,
                interval=ctx.poll_interval,
                clock=ctx.clock,
                sleep=ctx.sleep,
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
                f"review run crashed red on the dependabot head "
                f"{tracker['head']}: "
                f"{[(r.get('name'), r.get('conclusion')) for r in red]} — "
                "the DRE-2047/2067 class (empty Dependabot secrets store "
                "must self-skip, never crash)"
            )
        head_commit = ctx.gh.get_commit(ctx.repo, tracker["head"])
        gate_updated = len(head_commit.get("parents") or []) >= 2
        if gate_updated:
            # A merge-commit head was update-branched by the gate: its
            # synchronize actor is the qa-bot with NORMAL secrets, so a
            # success review here is the DRE-2037 path, not a miswire.
            ctx.log(
                f"[{self.name}] head {tracker['head'][:8]} is a gate-update "
                "merge commit — self-skip not observable on it (qa-actor "
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
            wait_until(
                f"a sha-bound verdict on PR #{number} via the reconcile "
                "dispatch route",
                poll_verdict,
                timeout=ctx.verdict_timeout + RECONCILE_CRON_ALLOWANCE,
                interval=ctx.poll_interval,
                clock=ctx.clock,
                sleep=ctx.sleep,
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
        leftovers = [
            pr["number"]
            for pr in ctx.gh.list_open_prs(ctx.repo)
            if framework.is_harness_ref((pr.get("head") or {}).get("ref", ""))
        ]
        if leftovers:
            raise ScenarioFailure(
                f"open harness PRs left behind after cleanup: {leftovers}"
            )
        ctx.log(f"[{self.name}] cleanup complete; default branch @{tip}")


SCENARIO = DependabotFlow()
