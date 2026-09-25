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

  * A Dependabot PR cannot be conjured by API, and since 2026-09-25 the
    sandbox keeps no standing one. Until then the fixture was
    bureau-harness #1, a pytest 7→9 major bump open since 2026-07-21: it
    persisted precisely BECAUSE it was a major the gate parks as
    waiting-for-human. The operator closed it under the house rule that
    majors are never auto-filed (DRE-2064), alongside the card bringing
    the sandbox's own `dependabot.yml` to that shape (DRE-4829) — and
    with majors ignored what Dependabot files is a minor/patch bump,
    which is the arm the gate AUTO-MERGES. Nothing persists between runs
    any more.
    So when no genuine Dependabot PR is open, setup reports NOT
    EXERCISABLE (DRE-4841) and the three clauses below are simply not
    observed this run. That is not a verdict on the commit — this run
    gates `stable` and every `v*` tag, and failing it on a vendor
    artifact nobody can conjure held both on a policy decision instead
    of on a defect. When Dependabot DOES have one open (its monthly
    schedule, or before the gate merges it) the scenario observes the
    live path exactly as it always did.
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
    ScenarioNotExercisable,
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

# What a NOT EXERCISABLE run says out loud (DRE-4841). It has to carry three
# things, because it is all a reader of a GREEN run gets: which clauses went
# unobserved, what still pins them without the live PR, and why the fixture is
# absent — so a policy decision is not mistaken for a defect, and a defect is
# not mistaken for this.
NO_PR_REASON = (
    "no open genuine Dependabot PR in the sandbox, so the live vendor path "
    "was NOT observed this run: the self-skip on an empty Dependabot secrets "
    "store (DRE-2067), the reconcile dispatch route (DRE-2047/2053) and the "
    "receipt lifecycle (DRE-2049/2071) all need a pull request authored by "
    "dependabot[bot], which cannot be conjured by API. Nothing is claimed "
    "about them; the gate's dependabot decision table stays unit-pinned in "
    "tests/test_merge_gate_dependabot.py, the crashed-review retry in "
    "tests/test_dependabot_receipt_retry.py, and gate_paths still exercises "
    "the condition-D arm on a synthesized dependabot-named branch. This is "
    "NOT a verdict on the commit: the sandbox keeps no standing fixture since "
    "the house rule reached it (DRE-2064 ignores semver-majors and DRE-4829 "
    "applies that to bureau-harness, and a minor/patch bump is the arm the "
    "gate auto-merges), so no Dependabot PR persists between runs. The "
    "scenario observes the path on any run where one is open."
)


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
        pr = find_real_dependabot_pr(ctx.gh.list_open_prs(ctx.repo))
        if pr is None:
            raise ScenarioNotExercisable(NO_PR_REASON)
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
            review_runs = ctx.wait(
                f"the review check runs on {tracker['head']} to conclude",
                poll_review_runs,
                timeout=CHECKS_TIMEOUT,
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
