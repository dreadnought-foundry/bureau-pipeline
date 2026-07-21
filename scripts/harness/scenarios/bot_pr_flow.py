"""Scenario bot_pr_flow — the happy path, end to end, in the sandbox.

As the worker bot, push a namespaced `agent/harness-…` branch and open a
PR; assert the REAL critic posts a verdict bound to the head sha (the
actor identities pass every allowed_bots gate and token mint on the way);
assert the REAL merge gate merges the PR as the qa-bot (author ≠ merger,
enforced by App identity); assert cleanup leaves the sandbox default
branch usable for the next run. Nothing GitHub-side is mocked.

THE LINEAR-SIDE DECISION (the card asks for it explicitly): harness
branches carry NO `DRE-n` card reference — should_review_pr.py reviews
them via the `agent/` prefix alone, and every Linear touchpoint in the
pipeline then no-ops deterministically: qa-review's card comment is
guarded on a non-empty card ref, linear-sync extracts its card from the
branch name and finds none, and reconcile only sweeps Linear cards (none
of which reference harness PRs). No permanent harness card, no sandbox
Linear stubs, zero Linear writes — the harness cannot spam real cards
because it never addresses one.

The probe change is a single markdown record under harness-runs/ — no
code, so nothing the sandbox's CI could collect, import, or accumulate
behavior from (the dir name is deliberately NOT a Python identifier:
setuptools flat-layout discovery claimed the old `harness_runs` as a
second top-level package and broke the sandbox build, run 29795108949);
the PR body says plainly what it is so the adversarial
critic judges a coherent, honest, cardless change (the shape
review_card_context.py already defines for DRE-less branches).
"""

from __future__ import annotations

from harness import framework
from harness.framework import (
    PROBE_DIR,
    ScenarioFailure,
    scenario_branch,
    same_bot,
    sweep_leftovers,
    verdict_state,
    wait_until,
)


def probe_path(run_id: str) -> str:
    return f"{PROBE_DIR}/{run_id}-bot_pr_flow.md"


def probe_markdown(run_id: str) -> str:
    return (
        f"# Integration-harness probe — run {run_id}\n"
        "\n"
        "This file was committed by bureau-pipeline's integration harness\n"
        "(`scripts/harness/`, scenario `bot_pr_flow`) to exercise the live\n"
        "review-and-merge rail in this sandbox repository: worker-bot\n"
        "authorship, the adversarial review, and the gate's qa-bot merge.\n"
        "\n"
        "It records nothing and changes no behavior. The harness deletes it\n"
        "during cleanup; if it is still here, a run crashed mid-flight and\n"
        "the next run's sweep will remove it.\n"
    )


def pr_title(run_id: str) -> str:
    return f"test(harness): live-rail probe {run_id}"


def pr_body(run_id: str) -> str:
    return (
        f"Automated integration-harness probe (run `{run_id}`), opened by\n"
        "bureau-pipeline's harness suite (`scripts/harness/`, scenario\n"
        "`bot_pr_flow`) to prove the live pipeline end to end in this\n"
        "sandbox: the worker bot authors this PR, the adversarial reviewer\n"
        "reviews it, and the merge gate lands it under its normal rules.\n"
        "\n"
        f"The diff is one markdown record under `{PROBE_DIR}/` — no code,\n"
        "no behavior change, removed again by harness cleanup. This branch\n"
        "carries no Linear card on purpose: the harness never touches real\n"
        "cards.\n"
    )


class BotPrFlow(framework.Scenario):
    name = "bot_pr_flow"

    # ── setup: a clean sandbox, whatever the previous run did ────────────
    def setup(self, ctx):
        ctx.state["swept"] = sweep_leftovers(ctx.gh, ctx.repo, ctx.log)
        base, base_sha = ctx.gh.default_branch(ctx.repo)
        ctx.state["base"], ctx.state["base_sha"] = base, base_sha
        ctx.log(f"[{self.name}] base {base}@{base_sha}")

    # ── exercise: the worker bot authors agent-shaped work ───────────────
    def exercise(self, ctx):
        branch = scenario_branch(ctx.run_id, self.name)
        ctx.state["branch"] = branch
        ctx.gh.create_ref(ctx.repo, branch, ctx.state["base_sha"])
        head_sha = ctx.gh.put_file(
            ctx.repo,
            branch,
            probe_path(ctx.run_id),
            probe_markdown(ctx.run_id),
            f"test(harness): probe record {ctx.run_id}",
        )
        pr = ctx.gh.create_pr(
            ctx.repo,
            head=branch,
            base=ctx.state["base"],
            title=pr_title(ctx.run_id),
            body=pr_body(ctx.run_id),
        )
        ctx.state["pr"] = pr["number"]
        ctx.state["head_sha"] = head_sha
        ctx.log(f"[{self.name}] opened PR #{pr['number']} ({branch}@{head_sha})")

    # ── verify: real critic verdict, real qa-bot merge ───────────────────
    def verify(self, ctx):
        number, head_sha = ctx.state["pr"], ctx.state["head_sha"]
        last = {"state": "none", "detail": ""}

        def poll_verdict():
            comments = ctx.gh.list_comments(ctx.repo, number)
            state, detail = verdict_state(comments, ctx.qa_login, head_sha)
            last.update(state=state, detail=detail)
            if state == "REQUEST_CHANGES":
                # A real, bound verdict that can never become the happy
                # path — fail fast with the critic's own words nearby.
                raise ScenarioFailure(
                    f"critic verdict is REQUEST_CHANGES for {head_sha} — "
                    f"the happy path is broken (PR #{number}): {detail}"
                )
            return state == "APPROVE" or None

        try:
            wait_until(
                f"a qa-authored critic APPROVE bound to {head_sha} on PR #{number}",
                poll_verdict,
                timeout=ctx.verdict_timeout,
                interval=ctx.poll_interval,
                clock=ctx.clock,
                sleep=ctx.sleep,
            )
        except framework.HarnessTimeout as e:
            raise ScenarioFailure(
                f"{e}; last verdict state: {last['state']} ({last['detail']})"
            ) from e
        ctx.log(f"[{self.name}] bound APPROVE verdict on PR #{number}")

        def poll_merged():
            pr = ctx.gh.get_pr(ctx.repo, number)
            if pr.get("merged"):
                return pr
            if pr.get("state") == "closed":
                raise ScenarioFailure(
                    f"PR #{number} was closed without merging — the merge "
                    "gate never landed it"
                )
            return None

        merged = wait_until(
            f"the merge gate merging PR #{number}",
            poll_merged,
            timeout=ctx.merge_timeout,
            interval=ctx.poll_interval,
            clock=ctx.clock,
            sleep=ctx.sleep,
        )
        merged_by = (merged.get("merged_by") or {}).get("login") or ""
        if not same_bot(merged_by, ctx.qa_login):
            raise ScenarioFailure(
                f"PR #{number} was merged by {merged_by!r}, not the qa-bot "
                f"({ctx.qa_login!r}) — the author≠merger identity split "
                "did not hold"
            )
        ctx.log(f"[{self.name}] PR #{number} merged by {merged_by}")

    # ── cleanup: leave the default branch usable for the next run ────────
    def cleanup(self, ctx):
        gh, repo = ctx.gh, ctx.repo
        number = ctx.state.get("pr")
        if number is not None:
            pr = gh.get_pr(repo, number)
            if pr.get("state") == "open":
                gh.close_pr(repo, number)
                ctx.log(f"[{self.name}] cleanup: closed PR #{number}")
        branch = ctx.state.get("branch")
        if branch:
            gh.delete_ref(repo, branch)

        # The merged probe file comes off the default branch again. Best
        # effort: if branch protection forbids the direct delete, the file
        # is a uniquely-named inert leftover and the next run's sweep
        # retries it — never fail the run over tidiness.
        base = ctx.state.get("base")
        if base:
            try:
                gh.delete_file(
                    repo, base, probe_path(ctx.run_id),
                    f"chore(harness): cleanup probe record {ctx.run_id}",
                )
            except Exception as e:
                ctx.log(f"[{self.name}] cleanup: probe delete skipped ({e})")

        # The load-bearing assertions: the sandbox is usable for the NEXT
        # run — default branch readable, no harness PRs left open.
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


SCENARIO = BotPrFlow()
