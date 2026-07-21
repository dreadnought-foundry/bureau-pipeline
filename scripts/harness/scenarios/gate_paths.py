"""Scenario gate_paths — merge-gate semantics against real GitHub (DRE-2100).

Three synthesized PRs, exercised concurrently by the sandbox's real
critic and merge gate, plus an opportunistic look at the real Dependabot
PR:

  SKEW leg (agent/harness-…-gate_paths-skew): opened behind base (the
  harness advances the default branch right after branching). The gate's
  condition 0 (DRE-1924) must UPDATE the branch — performed as the
  qa-bot — and the resulting synchronize event's actor must pass the
  review allowlists (DRE-2037): a fresh verdict binds the updated head
  and the PR merges. A lockout here is exactly the 2026-07-12 class.

  NAMED leg (dependabot/harness-…-gate_paths-named): a worker-authored PR
  on a dependabot-NAMED branch, also behind base. Condition D (DRE-2039)
  makes it `human` — the gate posts the honest waiting-for-human state
  exactly ONCE and touches nothing: no update-branch (despite being
  behind), no merge. Two observable gate wakes bracket the once-only
  assertion (the CI workflow_run and this PR's own critic comment).

  STALE leg (agent/harness-…-gate_paths-stale): opened current. The
  instant the critic's bound APPROVE lands, the harness pushes a new
  commit — the verdict is now bound to a superseded head (DRE-1990). The
  gate must NOT merge until a fresh verdict binds the new head; the merge
  is then asserted to carry a bound APPROVE for the exact merged head.

  REAL-PR POSTURE: when the sandbox's genuine Dependabot PR is
  observable, its gate arm is asserted from its own update-type metadata
  (merge_gate's own parser): major/unprovable → waiting-for-human once +
  untouched + open; provable minor/patch with a bound APPROVE →
  auto-merged by the qa-bot.

HONEST COVERAGE LIMITS:
  * The genuine minor/patch AUTO-MERGE arm depends on what Dependabot
    filed — the sandbox pin is deliberately major-stale (so the fixture
    PR persists), which means that arm is usually asserted only as "held,
    not merged" and stays fully covered by the unit decision table
    (tests/test_merge_gate_dependabot.py). The merge mechanics themselves
    (conditions 0-3) are proven live by bot_pr_flow and the skew leg —
    condition D is the only extra hop, and its refusal side is proven
    live by the named leg.
  * The stale-race construction loses if the gate merges within the
    seconds between the APPROVE landing and the harness's push. The
    scenario then FAILS with the race named (rerun) — an honest miss,
    never silent pretend coverage. In practice the gate's Actions startup
    plus its checkout steps dwarf the harness's tight poll.
"""

from __future__ import annotations

from harness import framework
from harness.framework import (
    PROBE_DIR,
    ScenarioFailure,
    dependabot_scenario_branch,
    find_real_dependabot_pr,
    same_bot,
    scenario_branch,
    sweep_leftovers,
    verdict_state,
    wait_until,
)

import merge_gate

# The gate's own status literal (merge-gate.yml posts it AND greps it as
# its idempotence key) — unit-pinned against the workflow text, and the
# harness must never emit it anywhere it writes.
HUMAN_WAIT_MARKER = "Merge gate: waiting for human merge"

# Creation order: skew and named branch from the ORIGINAL base tip (the
# base advance lands after them, making both behind); stale branches from
# the advanced tip (it must be current for the race to be a pure
# verdict-binding test).
LEGS = ("skew", "named", "stale")

# Grace after the last observed gate wake before asserting it did nothing
# further (its run has to start, mint, checkout, and evaluate).
GATE_GRACE_SECONDS = 150.0
# The race window between the APPROVE landing and the gate's merge is the
# gate run's startup+checkout time (~30-60s) — poll far inside it.
STALE_POLL_INTERVAL = 5.0
# Chain-walk bound: consecutive gate updates only happen when base moves
# again mid-flow; more than a few means something is looping.
MAX_UPDATE_HOPS = 5

_LEG_PURPOSE = {
    "skew": (
        "opened deliberately BEHIND its base: the merge gate must update\n"
        "this branch (as the qa-bot), the follow-up review must pass the\n"
        "actor allowlists, and the gate then merges normally.\n"
    ),
    "named": (
        "a worker-authored PR on a dependabot-NAMED branch: the merge\n"
        "gate must refuse to auto-handle it (only the real dependabot[bot]\n"
        "earns the dependency policy), post its honest waiting state once,\n"
        "and otherwise leave it alone. Harness cleanup closes it.\n"
    ),
    "stale": (
        "after the first approval lands, the harness immediately pushes a\n"
        "follow-up commit, making that approval stale: the merge gate must\n"
        "hold until a fresh review covers the new commit.\n"
    ),
}


def leg_branch(run_id: str, leg: str) -> str:
    if leg == "named":
        return dependabot_scenario_branch(run_id, "gate_paths-named")
    return scenario_branch(run_id, f"gate_paths-{leg}")


def probe_path(run_id: str, leg: str) -> str:
    return f"{PROBE_DIR}/{run_id}-gate_paths-{leg}.md"


def base_advance_path(run_id: str) -> str:
    return f"{PROBE_DIR}/{run_id}-gate_paths-base-advance.md"


def probe_markdown(run_id: str, leg: str) -> str:
    return (
        f"# Integration-harness probe — run {run_id}, leg {leg}\n"
        "\n"
        "Committed by bureau-pipeline's integration harness\n"
        "(`scripts/harness/`, scenario `gate_paths`) to exercise the live\n"
        f"merge gate. This leg is {_LEG_PURPOSE[leg]}"
        "\n"
        "It records nothing and changes no behavior. The harness deletes it\n"
        "during cleanup; if it is still here, a run crashed mid-flight and\n"
        "the next run's sweep will remove it.\n"
    )


def base_advance_markdown(run_id: str) -> str:
    return (
        f"# Integration-harness base-advance marker — run {run_id}\n"
        "\n"
        "Committed directly to the default branch by scenario `gate_paths`\n"
        "so the just-branched probe PRs are measurably behind their base\n"
        "(the merge gate's skew-guard input). Deleted again by cleanup or\n"
        "the next run's sweep.\n"
    )


def pr_title(run_id: str, leg: str) -> str:
    return f"test(harness): gate-paths {leg} probe {run_id}"


def pr_body(run_id: str, leg: str) -> str:
    return (
        f"Automated integration-harness probe (run `{run_id}`, scenario\n"
        "`gate_paths`, leg `" + leg + "`), opened by bureau-pipeline's\n"
        "harness suite to prove the live merge gate's semantics in this\n"
        f"sandbox. This leg is {_LEG_PURPOSE[leg]}"
        "\n"
        f"The diff is one markdown record under `{PROBE_DIR}/` — no code,\n"
        "no behavior change, removed again by harness cleanup. This branch\n"
        "carries no Linear card on purpose: the harness never touches real\n"
        "cards.\n"
    )


def _human_wait_comments(comments, qa_login: str) -> list:
    """The gate's waiting-for-human status notes — qa-authored only, so a
    quoted or forged marker never satisfies (or spams) the assertion."""
    return [
        c
        for c in comments
        if same_bot(((c.get("user") or {}).get("login")), qa_login)
        and HUMAN_WAIT_MARKER in (c.get("body") or "")
    ]


class GatePaths(framework.Scenario):
    name = "gate_paths"

    # ── setup ────────────────────────────────────────────────────────────
    def setup(self, ctx):
        ctx.state["swept"] = sweep_leftovers(ctx.gh, ctx.repo, ctx.log)
        base, base_sha = ctx.gh.default_branch(ctx.repo)
        ctx.state["base"], ctx.state["base_sha"] = base, base_sha
        ctx.log(f"[{self.name}] base {base}@{base_sha}")

    # ── exercise: three probe PRs in their starting positions ────────────
    def exercise(self, ctx):
        gh, repo, rid = ctx.gh, ctx.repo, ctx.run_id
        legs = ctx.state.setdefault("legs", {})

        # Skew and named branch from the CURRENT tip…
        for leg in ("skew", "named"):
            branch = leg_branch(rid, leg)
            gh.create_ref(repo, branch, ctx.state["base_sha"])
            head = gh.put_file(
                repo, branch, probe_path(rid, leg), probe_markdown(rid, leg),
                f"test(harness): gate-paths {leg} probe {rid}",
            )
            legs[leg] = {"branch": branch, "h1": head}

        # …then the base advances, making both measurably behind it.
        base2 = gh.put_file(
            repo, ctx.state["base"], base_advance_path(rid),
            base_advance_markdown(rid),
            f"test(harness): gate-paths base advance {rid}",
        )
        ctx.state["base2"] = base2
        ctx.log(f"[{self.name}] base advanced to {base2} — skew/named now behind")

        for leg in ("skew", "named"):
            pr = gh.create_pr(
                repo,
                head=legs[leg]["branch"],
                base=ctx.state["base"],
                title=pr_title(rid, leg),
                body=pr_body(rid, leg),
            )
            legs[leg]["pr"] = pr["number"]

        # The stale leg branches from the ADVANCED tip: current with base,
        # so the only thing between its APPROVE and a merge is binding.
        branch = leg_branch(rid, "stale")
        gh.create_ref(repo, branch, base2)
        head = gh.put_file(
            repo, branch, probe_path(rid, "stale"), probe_markdown(rid, "stale"),
            f"test(harness): gate-paths stale probe {rid}",
        )
        pr = gh.create_pr(
            repo, head=branch, base=ctx.state["base"],
            title=pr_title(rid, "stale"), body=pr_body(rid, "stale"),
        )
        legs["stale"] = {"branch": branch, "h1": head, "pr": pr["number"]}
        for leg in LEGS:
            ctx.log(
                f"[{self.name}] opened {leg} PR #{legs[leg]['pr']} "
                f"({legs[leg]['branch']}@{legs[leg]['h1']})"
            )

    # ── verify ───────────────────────────────────────────────────────────
    def verify(self, ctx):
        # The stale race is time-critical (it must be watching when the
        # first APPROVE lands) — it runs first; the skew and named legs
        # cook in the background meanwhile.
        self._verify_stale(ctx)
        self._verify_skew(ctx)
        self._verify_named(ctx)
        self._verify_real_pr(ctx)

    # -- stale leg: verdict binding (DRE-1990) ----------------------------
    def _verify_stale(self, ctx):
        leg = ctx.state["legs"]["stale"]
        number = leg["pr"]
        tracker = {"head": leg["h1"], "state": "none", "detail": ""}

        def poll_approve():
            pr = ctx.gh.get_pr(ctx.repo, number)
            if pr.get("merged"):
                raise ScenarioFailure(
                    f"stale leg: PR #{number} merged before the stale push "
                    "landed — the race was lost and the binding property "
                    "was NOT exercised this run (rerun the harness)"
                )
            if pr.get("state") == "closed":
                raise ScenarioFailure(
                    f"stale leg: PR #{number} closed without merging"
                )
            if pr["head"]["sha"] != tracker["head"]:
                tracker["head"] = pr["head"]["sha"]
                ctx.log(
                    f"[{self.name}] stale: head moved to "
                    f"{tracker['head'][:8]} (gate update) — re-arming the race"
                )
            comments = ctx.gh.list_comments(ctx.repo, number)
            state, detail = verdict_state(comments, ctx.qa_login, tracker["head"])
            tracker["state"], tracker["detail"] = state, detail
            if state == "REQUEST_CHANGES":
                raise ScenarioFailure(
                    f"stale leg: critic REQUEST_CHANGES on the probe — "
                    f"cannot run the race (PR #{number}): {detail}"
                )
            return state == "APPROVE" or None

        try:
            wait_until(
                f"a bound APPROVE to race on PR #{number}",
                poll_approve,
                timeout=ctx.verdict_timeout,
                interval=min(ctx.poll_interval, STALE_POLL_INTERVAL),
                clock=ctx.clock,
                sleep=ctx.sleep,
            )
        except framework.HarnessTimeout as e:
            raise ScenarioFailure(
                f"{e}; last verdict state: {tracker['state']} "
                f"({tracker['detail']})"
            ) from e

        approved_sha = tracker["head"]
        # The APPROVE is live — push NOW, before the gate's run can act on
        # it, so the verdict becomes bound to a superseded head.
        try:
            pushed = ctx.gh.put_file(
                ctx.repo, leg["branch"], probe_path(ctx.run_id, "stale"),
                probe_markdown(ctx.run_id, "stale")
                + f"\nSecond commit — makes the APPROVE for {approved_sha} stale.\n",
                f"test(harness): gate-paths stale second commit {ctx.run_id}",
            )
        except Exception as e:
            pr = ctx.gh.get_pr(ctx.repo, number)
            if pr.get("merged") or pr.get("state") == "closed":
                raise ScenarioFailure(
                    f"stale leg: the gate merged PR #{number} at "
                    f"{approved_sha} before the stale push landed — race "
                    "lost, binding not exercised this run (rerun)"
                ) from e
            raise
        ctx.log(
            f"[{self.name}] stale: pushed {pushed[:8]} — the APPROVE for "
            f"{approved_sha[:8]} is now stale; the gate must hold"
        )

        def poll_merged():
            pr = ctx.gh.get_pr(ctx.repo, number)
            if pr.get("merged"):
                return pr
            if pr.get("state") == "closed":
                raise ScenarioFailure(
                    f"stale leg: PR #{number} closed without merging"
                )
            state, detail = verdict_state(
                ctx.gh.list_comments(ctx.repo, number),
                ctx.qa_login, pr["head"]["sha"],
            )
            if state == "REQUEST_CHANGES":
                raise ScenarioFailure(
                    f"stale leg: critic REQUEST_CHANGES on the re-review "
                    f"(PR #{number}): {detail}"
                )
            return None

        merged = wait_until(
            f"the gate merging PR #{number} on a FRESH verdict",
            poll_merged,
            timeout=ctx.verdict_timeout + ctx.merge_timeout,
            interval=ctx.poll_interval,
            clock=ctx.clock,
            sleep=ctx.sleep,
        )
        final_head = merged["head"]["sha"]
        state, detail = verdict_state(
            ctx.gh.list_comments(ctx.repo, number), ctx.qa_login, final_head
        )
        if final_head == approved_sha or state != "APPROVE":
            raise ScenarioFailure(
                f"stale leg: PR #{number} merged at {final_head} whose "
                f"latest verdict is {state} ({detail}) — a stale APPROVE "
                "rode into the default branch (the DRE-1990 class)"
            )
        merged_by = (merged.get("merged_by") or {}).get("login") or ""
        if not same_bot(merged_by, ctx.qa_login):
            raise ScenarioFailure(
                f"stale leg: PR #{number} merged by {merged_by!r}, not the "
                f"qa-bot ({ctx.qa_login!r})"
            )
        ctx.log(f"[{self.name}] stale: held on the stale verdict, merged fresh")

    # -- skew leg: currency update as qa-bot, no lockout ------------------
    def _verify_skew(self, ctx):
        leg = ctx.state["legs"]["skew"]
        number, h1 = leg["pr"], leg["h1"]

        def poll_moved():
            pr = ctx.gh.get_pr(ctx.repo, number)
            if pr.get("state") == "closed" and not pr.get("merged"):
                raise ScenarioFailure(
                    f"skew leg: PR #{number} closed without merging"
                )
            return pr if (pr["head"]["sha"] != h1 or pr.get("merged")) else None

        moved = wait_until(
            f"the gate updating the behind-base PR #{number}",
            poll_moved,
            timeout=ctx.merge_timeout,
            interval=ctx.poll_interval,
            clock=ctx.clock,
            sleep=ctx.sleep,
        )
        if moved["head"]["sha"] == h1:
            raise ScenarioFailure(
                f"skew leg: PR #{number} merged while still behind base — "
                "the currency guard (condition 0) never updated it"
            )
        # Walk the update chain: every hop from the current head back to
        # our commit must be a 2-parent update merge performed by the
        # qa-bot (base may move more than once mid-flow — e.g. the stale
        # leg merging its probe — so one hop is not guaranteed).
        cur = moved["head"]["sha"]
        for _ in range(MAX_UPDATE_HOPS):
            commit = ctx.gh.get_commit(ctx.repo, cur)
            parents = [p.get("sha") for p in commit.get("parents") or []]
            if len(parents) != 2:
                raise ScenarioFailure(
                    f"skew leg: head {cur} is not an update-branch merge "
                    f"commit (parents {parents}) — something other than the "
                    "gate moved this branch"
                )
            author = ((commit.get("author") or {}).get("login")) or ""
            committer = ((commit.get("committer") or {}).get("login")) or ""
            if not (same_bot(author, ctx.qa_login) or same_bot(committer, ctx.qa_login)):
                raise ScenarioFailure(
                    f"skew leg: update commit {cur} was performed by "
                    f"author={author!r}/committer={committer!r}, not the "
                    "qa-bot — the skew-guard must update as the merging "
                    "identity"
                )
            cur = parents[0]  # first parent = the previous head
            if cur == h1:
                break
        else:
            raise ScenarioFailure(
                f"skew leg: could not walk the update chain from "
                f"{moved['head']['sha']} back to {h1} within "
                f"{MAX_UPDATE_HOPS} hops"
            )
        ctx.log(f"[{self.name}] skew: gate updated the branch as the qa-bot")

        def poll_merged():
            pr = ctx.gh.get_pr(ctx.repo, number)
            if pr.get("merged"):
                return pr
            if pr.get("state") == "closed":
                raise ScenarioFailure(
                    f"skew leg: PR #{number} closed without merging"
                )
            state, detail = verdict_state(
                ctx.gh.list_comments(ctx.repo, number),
                ctx.qa_login, pr["head"]["sha"],
            )
            if state == "REQUEST_CHANGES":
                raise ScenarioFailure(
                    f"skew leg: critic REQUEST_CHANGES after the update "
                    f"(PR #{number}): {detail}"
                )
            return None

        try:
            merged = wait_until(
                f"the updated PR #{number} re-reviewing and merging",
                poll_merged,
                timeout=ctx.verdict_timeout + ctx.merge_timeout,
                interval=ctx.poll_interval,
                clock=ctx.clock,
                sleep=ctx.sleep,
            )
        except framework.HarnessTimeout as e:
            raise ScenarioFailure(
                f"{e} — the qa-bot's synchronize event produced no fresh "
                "bound verdict: the actor allowlists locked the review out "
                "(the DRE-2037 class)"
            ) from e
        final_head = merged["head"]["sha"]
        state, detail = verdict_state(
            ctx.gh.list_comments(ctx.repo, number), ctx.qa_login, final_head
        )
        if state != "APPROVE":
            raise ScenarioFailure(
                f"skew leg: merged head {final_head} has no bound APPROVE "
                f"({state}: {detail})"
            )
        merged_by = (merged.get("merged_by") or {}).get("login") or ""
        if not same_bot(merged_by, ctx.qa_login):
            raise ScenarioFailure(
                f"skew leg: PR #{number} merged by {merged_by!r}, not the "
                f"qa-bot ({ctx.qa_login!r})"
            )
        ctx.log(f"[{self.name}] skew: no lockout — fresh verdict, qa-bot merge")

    # -- named leg: human once, hands off ---------------------------------
    def _verify_named(self, ctx):
        leg = ctx.state["legs"]["named"]
        number, h1 = leg["pr"], leg["h1"]

        def _pr_untouched():
            pr = ctx.gh.get_pr(ctx.repo, number)
            if pr.get("merged"):
                raise ScenarioFailure(
                    f"named leg: the gate MERGED PR #{number} — a "
                    "dependabot-named branch not authored by dependabot[bot] "
                    "must never be auto-merged (condition D broke)"
                )
            if pr["head"]["sha"] != h1:
                raise ScenarioFailure(
                    f"named leg: the gate TOUCHED PR #{number} (head moved "
                    f"{h1} → {pr['head']['sha']}) — a human-decision PR gets "
                    "no update-branch, even behind base"
                )
            return pr

        def poll_human():
            _pr_untouched()
            comments = ctx.gh.list_comments(ctx.repo, number)
            return comments if _human_wait_comments(comments, ctx.qa_login) else None

        wait_until(
            f"the waiting-for-human state on PR #{number}",
            poll_human,
            timeout=ctx.merge_timeout,
            interval=ctx.poll_interval,
            clock=ctx.clock,
            sleep=ctx.sleep,
        )

        # A second, observable gate wake: this PR's own critic comment
        # (should_review_pr admits dependabot/** branches). Then a grace
        # period for that wake's evaluation, and the once-only assertion.
        def poll_critic():
            _pr_untouched()
            comments = ctx.gh.list_comments(ctx.repo, number)
            state, _ = verdict_state(comments, ctx.qa_login, h1)
            return comments if state != "none" else None

        wait_until(
            f"a critic comment on PR #{number} (the second gate wake)",
            poll_critic,
            timeout=ctx.verdict_timeout,
            interval=ctx.poll_interval,
            clock=ctx.clock,
            sleep=ctx.sleep,
        )
        ctx.sleep(GATE_GRACE_SECONDS)

        pr = _pr_untouched()
        if pr.get("state") != "open":
            raise ScenarioFailure(
                f"named leg: PR #{number} is {pr.get('state')} — the honest "
                "waiting state means the PR stays open for a human"
            )
        waits = _human_wait_comments(
            ctx.gh.list_comments(ctx.repo, number), ctx.qa_login
        )
        if len(waits) != 1:
            raise ScenarioFailure(
                f"named leg: {len(waits)} waiting-for-human comments on "
                f"PR #{number} — the gate must post the state exactly once "
                "(idempotent across re-evaluations)"
            )
        ctx.log(f"[{self.name}] named: human state posted once, PR untouched")

    # -- real dependabot PR: opportunistic posture check ------------------
    def _verify_real_pr(self, ctx):
        pr = find_real_dependabot_pr(ctx.gh.list_open_prs(ctx.repo))
        if pr is None:
            # It may have JUST auto-merged (a provable minor/patch pin) —
            # dependabot_flow owns enforcing the fixture's existence.
            ctx.log(
                f"[{self.name}] no open genuine Dependabot PR to observe — "
                "the genuine-PR gate arms were not exercisable this run "
                "(dependabot_flow enforces the fixture; the decision table "
                "is unit-pinned in test_merge_gate_dependabot.py)"
            )
            return
        number = pr["number"]
        detail = ctx.gh.get_pr(ctx.repo, number)
        head = detail["head"]["sha"]
        levels = merge_gate.dependabot_update_types(
            ctx.gh.list_pr_commits(ctx.repo, number)
        )
        comments = ctx.gh.list_comments(ctx.repo, number)
        state, _ = verdict_state(comments, ctx.qa_login, head)
        provable = bool(levels) and all(
            lv in merge_gate.MERGEABLE_UPDATE_TYPES for lv in levels
        )

        if provable:
            if state == "APPROVE":
                merged = wait_until(
                    f"the minor/patch Dependabot PR #{number} auto-merging",
                    lambda: (
                        (p := ctx.gh.get_pr(ctx.repo, number)).get("merged")
                        and p or None
                    ),
                    timeout=ctx.merge_timeout,
                    interval=ctx.poll_interval,
                    clock=ctx.clock,
                    sleep=ctx.sleep,
                )
                merged_by = (merged.get("merged_by") or {}).get("login") or ""
                if not same_bot(merged_by, ctx.qa_login):
                    raise ScenarioFailure(
                        f"real PR #{number} auto-merged by {merged_by!r}, "
                        f"not the qa-bot"
                    )
                ctx.log(
                    f"[{self.name}] real PR: minor/patch auto-merge arm "
                    "exercised live — NOTE: the sandbox pin is now current; "
                    "the operator must re-stale it for Dependabot to keep "
                    "filing"
                )
            else:
                if ctx.gh.get_pr(ctx.repo, number).get("merged"):
                    raise ScenarioFailure(
                        f"real PR #{number} merged WITHOUT a bound APPROVE "
                        f"(verdict state {state})"
                    )
                ctx.log(
                    f"[{self.name}] real PR: provable minor/patch but "
                    f"verdict state is {state} — held correctly; the "
                    "auto-merge arm was not exercisable this run"
                )
            return

        # Major / unprovable: the honest human arm.
        waits = _human_wait_comments(comments, ctx.qa_login)
        if not waits:
            if state in ("none", "neutral", "stale"):
                ctx.log(
                    f"[{self.name}] real PR: no bound verdict yet, so no "
                    "gate wake to observe — human-state arm not exercisable "
                    "this run"
                )
                return
            # A bound verdict exists, so the gate HAS woken — the state
            # comment must appear (allow one more evaluation's latency).
            waits = _human_wait_comments(
                wait_until(
                    f"the waiting-for-human state on the real PR #{number}",
                    lambda: (
                        c := ctx.gh.list_comments(ctx.repo, number)
                    ) and _human_wait_comments(c, ctx.qa_login) and c or None,
                    timeout=ctx.merge_timeout,
                    interval=ctx.poll_interval,
                    clock=ctx.clock,
                    sleep=ctx.sleep,
                ),
                ctx.qa_login,
            )
        if len(waits) != 1:
            raise ScenarioFailure(
                f"real PR #{number}: {len(waits)} waiting-for-human comments "
                "— the gate must post the state exactly once"
            )
        commit = ctx.gh.get_commit(ctx.repo, head)
        if len(commit.get("parents") or []) >= 2:
            raise ScenarioFailure(
                f"real PR #{number}: head {head} is a merge commit — the "
                "gate update-branched a major/unprovable Dependabot PR it "
                "must never touch"
            )
        current = ctx.gh.get_pr(ctx.repo, number)
        if current.get("merged") or current.get("state") != "open":
            raise ScenarioFailure(
                f"real PR #{number} is not open — a major/unprovable "
                "Dependabot PR must stay honestly parked for a human"
            )
        ctx.log(
            f"[{self.name}] real PR: genuine major/unprovable → human arm "
            "exercised live (posted once, untouched, open)"
        )

    # ── cleanup ──────────────────────────────────────────────────────────
    def cleanup(self, ctx):
        gh, repo = ctx.gh, ctx.repo
        for leg in LEGS:
            info = (ctx.state.get("legs") or {}).get(leg) or {}
            number = info.get("pr")
            if number is not None:
                pr = gh.get_pr(repo, number)
                if pr.get("state") == "open":
                    gh.close_pr(repo, number)
                    ctx.log(f"[{self.name}] cleanup: closed {leg} PR #{number}")
            if info.get("branch"):
                gh.delete_ref(repo, info["branch"])

        # Merged probe files and the base-advance marker come off the
        # default branch again — best-effort, like bot_pr_flow (a
        # protected default leaves uniquely-named inert files for the
        # next run's sweep).
        base = ctx.state.get("base")
        if base:
            paths = [base_advance_path(ctx.run_id)] + [
                probe_path(ctx.run_id, leg) for leg in LEGS
            ]
            for path in paths:
                try:
                    gh.delete_file(
                        repo, base, path,
                        f"chore(harness): cleanup gate-paths record {ctx.run_id}",
                    )
                except Exception as e:
                    ctx.log(f"[{self.name}] cleanup: {path} delete skipped ({e})")

        _, tip = gh.default_branch(repo)
        if not tip:
            raise ScenarioFailure("default branch has no readable tip sha")
        leftovers = [
            pr["number"]
            for pr in gh.list_open_prs(repo)
            if framework.is_harness_ref((pr.get("head") or {}).get("ref", ""))
        ]
        if leftovers:
            raise ScenarioFailure(
                f"open harness PRs left behind after cleanup: {leftovers}"
            )
        ctx.log(f"[{self.name}] cleanup complete; default branch @{tip}")


SCENARIO = GatePaths()
