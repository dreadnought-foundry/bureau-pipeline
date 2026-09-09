"""Scenario agent_task_parses — the sandbox FIRES the workflow that builds
every card, and a job actually starts (DRE-3486).

THE GAP THIS CLOSES. `bureau-harness` installs four stubs — `ci.yml`,
`qa-review.yml`, `merge-gate.yml`, `reconcile.yml` — and, until the operator
half of DRE-3486, not `agent-task.yml`. So the one workflow that builds every
card in the fleet was the one workflow the pre-production gate never
installed, never parsed and never fired.

WHAT IT COST. On 2026-09-09 `agent-task.yml` became unparseable to GitHub: a
`run:` block carrying `${{ }}` is compiled into ONE expression, and that
step's script had reached ~23,260 characters against a hard 21,000 ceiling
(DRE-3484). The harness ran on that exact commit at 22:21 PT, PASSED, and
`promote-channel` advanced `stable` onto it. From 06:15 PT every
`agent-execute` dispatch in every repo on the channel was refused before a job
started — three hours and twenty minutes with no build lane.

WHY IT MUST FIRE, NOT MERELY INSTALL. GitHub validates a CALLED workflow only
at dispatch. A file can be valid YAML, pass every linter and every parse test
in this repo, and still be rejected at the moment it is used. Nothing short of
a real `repository_dispatch` reaches that check, so this rehearsal sends one.

WHAT A PASS MEANS, EXACTLY. A job started, and the run names
bureau-pipeline's own `agent-task.yml` as the workflow it compiled. That is
the whole claim: the file parses and the caller/callee contract holds. The
rehearsal never waits for the agent to build anything — it cancels the run it
started as soon as it has its answer, so a rehearsal costs a job start rather
than a build.

WHAT IT DOES NOT CLAIM (read this before tightening an assertion). The
sandbox's stubs ride `@main` on purpose, and GitHub resolves that ref AT
DISPATCH. Meanwhile `harness.yml` queues runs behind one another rather than
cancelling them (`cancel-in-progress: false`, DRE-3070), so a run can start
long after the push that triggered it. The commit GitHub compiled is
therefore routinely NOT the commit this run is stamping — always so on a pull
request's proving run, whose head is not on `main` at all. This rehearsal
RECORDS which commit was compiled and whether it is the commit under test; it
does not fail on a difference, because doing so would turn every boundary
pull request and every merge-burst push red on the pipeline's own design.
"""

from __future__ import annotations

from harness import framework
from harness.agent_scenario import CARD_URL, card_identifier
from harness.framework import ScenarioFailure, scenario_branch
from harness.github_api import GitHubError

#: The event the bureau-linear-relay fires at a product repo when a card
#: enters Todo. The rehearsal uses the same door on purpose.
DISPATCH_EVENT = "agent-execute"

#: What GitHub records as the run's `event` for that dispatch. The dispatch
#: TYPE is not the trigger event: a `repository_dispatch` of any type lists
#: under `event=repository_dispatch`, and the type survives only as the run's
#: `display_title`. Attempt 3 on PR #332 (2026-09-09 15:20 PT) asked the API
#: for `event=agent-execute`, read an empty page for ten minutes, and timed
#: out with the run it had fired sitting one filter away.
TRIGGER_EVENT = "repository_dispatch"

#: The sandbox stub the operator installs by hand (the operator half of
#: DRE-3486). Its shape matches its four siblings.
WORKFLOW_FILE = "agent-task.yml"

#: The callee the stub must reach, as `<owner>/<repo>/<path>` — WITHOUT the
#: ref. GitHub spells a `referenced_workflows` path with the ref attached,
#: `<owner>/<repo>/<path>@<ref>`, and ALSO carries `ref` as its own field, so
#: the two overlap. Compare on the part before the `@` (`callee_path`).
PIPELINE_REPO = "dreadnought-foundry/bureau-pipeline"
CALLEE_PATH = f"{PIPELINE_REPO}/.github/workflows/{WORKFLOW_FILE}"

#: The 2026-09-09 signature, NAMED rather than reported as a timeout. A
#: workflow GitHub refuses to compile concludes having started nothing, so a
#: completed run with zero jobs is that refusal and nothing else.
REFUSED_MESSAGE = (
    "agent-task.yml was refused before any job started — the workflow does "
    "not parse at this commit"
)

#: How long the rehearsal may wait for a job to appear. ONE sandbox wait
#: budget, never the 70-minute verdict deadline: a dispatch either produces a
#: run within a minute or two or the sandbox is not answering, and the whole
#: point of this rehearsal is to be fast enough to sit in the default sweep.
START_BUDGET_SECONDS = framework.WAIT_DEADLINE_SECONDS

#: How often to look. Faster than the sandbox default poll: the answer lands
#: in seconds and this is the run's first scenario.
POLL_SECONDS = 10.0


def start_budget(ctx) -> float:
    """The rehearsal's wait budget for this run.

    `ctx.wait_deadline` is the operator's dial and `0` switches the liveness
    check off entirely — that must not remove the bound here, or the rehearsal
    inherits the very timeout it exists to avoid.
    """
    deadline = getattr(ctx, "wait_deadline", 0) or 0
    return float(deadline) if deadline > 0 else START_BUDGET_SECONDS


def is_our_dispatch(run) -> bool:
    """True for a run that a `DISPATCH_EVENT` dispatch produced.

    GitHub titles a `repository_dispatch` run with its type, so that is the
    only place the type survives on the record. A record with no title at all
    is accepted rather than dropped: the listing is already scoped to the
    trigger event, and a missing field is not evidence of another type.
    """
    if not isinstance(run, dict):
        return False
    title = run.get("display_title")
    return title is None or title == DISPATCH_EVENT


def callee_path(entry) -> str:
    """The workflow an entry names, with any `@<ref>` suffix stripped.

    The live endpoint returns `path` WITH the ref attached — sandbox run
    34414467787 (2026-09-09, PR #332's own proving run) answered
    `dreadnought-foundry/bureau-pipeline/.github/workflows/agent-task.yml@main`
    beside `ref` — while the REST schema's example shows the bare path. An
    exact-equality comparison against the bare spelling rejected a correctly
    wired stub and reported it as "does not call this repo's reusable
    workflow", so the ref is stripped rather than assumed absent. Splitting on
    the FIRST `@` is safe: neither an owner, a repo nor a path segment may
    contain one.
    """
    if not isinstance(entry, dict):
        return ""
    return (entry.get("path") or "").split("@", 1)[0].strip()


def callee_reference(referenced) -> dict | None:
    """The `referenced_workflows` entry for bureau-pipeline's agent-task.yml,
    or None. `jobs > 0` alone would pass for a stub calling something else
    entirely, so the callee is half of what a dispatch proves."""
    for entry in referenced or ():
        if callee_path(entry) == CALLEE_PATH:
            return entry
    return None


def billing_note(timing) -> str:
    """One clause of corroboration for a zero-job refusal: GitHub bills no
    runner time at all for a run that never started a job. Absent or
    unreadable timing adds nothing — it is evidence, never the assertion."""
    if not isinstance(timing, dict) or "billable" not in timing:
        return ""
    if timing.get("billable"):
        return ""
    return " and GitHub billed no runner time for it (timing.billable is empty)"


class AgentTaskParses(framework.Scenario):
    name = "agent_task_parses"

    # In the DEFAULT sweep, deliberately. The gate that advances `stable` is
    # the default sweep, so an opt-in rehearsal would leave the channel
    # exactly as uncovered as it was on 2026-09-09. It is affordable there
    # because it never waits for a build: it asserts that a job STARTED and
    # cancels the run.
    requires_agent = False

    # ── setup: is the stub even installed? ───────────────────────────────
    def setup(self, ctx):
        ctx.state["identifier"] = card_identifier(ctx.run_id, self.name)
        # Every run of this workflow that existed BEFORE the dispatch, so the
        # one the dispatch produces is identifiable afterwards (a
        # repository_dispatch answers 204 with no run id).
        ctx.state["known_runs"] = {
            run.get("id") for run in self.workflow_runs(ctx)
        }
        ctx.log(
            f"[{self.name}] {len(ctx.state['known_runs'])} prior "
            f"{WORKFLOW_FILE} run(s) in {ctx.repo}; card "
            f"{ctx.state['identifier']}"
        )

    def workflow_runs(self, ctx) -> list:
        """This workflow's runs that OUR kind of dispatch produced.

        Listed by the trigger event GitHub records (`repository_dispatch`),
        then narrowed to the dispatch type by `display_title` — the stub
        fires on `agent-execute` only, but a run of another type would still
        list here if the stub ever grew a second `types:` entry.
        """
        try:
            runs = ctx.gh.list_workflow_runs_for(
                ctx.repo, WORKFLOW_FILE, event=TRIGGER_EVENT
            )
        except GitHubError as e:
            if e.status == 404:
                raise ScenarioFailure(
                    f"{ctx.repo} has no {WORKFLOW_FILE} stub — the operator "
                    f"half of DRE-3486 installs it by hand, the same shape as "
                    f"its four siblings, and the rehearsal cannot fire without "
                    f"it. This is a MISSING STUB, not a workflow that fails to "
                    f"parse."
                ) from e
            raise
        return [run for run in runs if is_our_dispatch(run)]

    # ── exercise: the relay's own door ───────────────────────────────────
    def exercise(self, ctx):
        # The relay's payload, key for key (scripts/plan_run.py `payload`).
        # The stub groups its concurrency on `identifier` and agent-task.yml
        # reads title/description/url, so a thinner payload would exercise a
        # shape nothing in the fleet sends.
        identifier = ctx.state["identifier"]
        ctx.gh.repository_dispatch(
            ctx.repo,
            DISPATCH_EVENT,
            {
                "card_id": identifier,
                "identifier": identifier,
                "title": "harness: prove agent-task.yml parses at this commit",
                "description": (
                    "Fired by bureau-pipeline's integration harness "
                    f"(`scripts/harness/`, scenario `{self.name}`) to prove "
                    "that GitHub can still compile agent-task.yml. The "
                    "rehearsal asserts only that a job started and cancels "
                    "this run immediately — there is no work here to do."
                ),
                "labels": ["agent:engineer"],
                "url": CARD_URL,
            },
        )
        ctx.log(
            f"[{self.name}] fired {DISPATCH_EVENT} at {ctx.repo} "
            f"(card {identifier})"
        )

    # ── verify: a job started, on our workflow ───────────────────────────
    def verify(self, ctx):
        budget = start_budget(ctx)
        known = ctx.state.get("known_runs") or set()

        def poll_run():
            for run in self.workflow_runs(ctx):
                if run.get("id") not in known:
                    return run
            return None

        run = ctx.wait(
            f"the {DISPATCH_EVENT} dispatch to produce a {WORKFLOW_FILE} run "
            f"in {ctx.repo}",
            poll_run,
            timeout=budget,
            interval=POLL_SECONDS,
        )
        run_id = run.get("id")
        ctx.state["run_id"] = run_id
        ctx.log(
            f"[{self.name}] dispatch produced run {run_id} "
            f"({run.get('html_url') or ''})"
        )

        def poll_started():
            record = ctx.gh.get_workflow_run(ctx.repo, run_id) or run
            ctx.state["run_record"] = record
            envelope = ctx.gh.list_run_jobs(ctx.repo, run_id) or {}
            jobs = envelope.get("jobs") or []
            total = int(envelope.get("total_count") or len(jobs))
            if total > 0:
                ctx.state["jobs"] = total
                return record
            if (record.get("status") or "") == "completed":
                # FAST, not at the deadline: the run is over, so no job is
                # ever starting on it and a longer wait only buys a worse
                # message. This is the exact 2026-09-09 signature.
                raise ScenarioFailure(self.refusal(ctx, record, run_id))
            return None

        record = ctx.wait(
            f"a job to start on {WORKFLOW_FILE} run {run_id}",
            poll_started,
            timeout=budget,
            interval=POLL_SECONDS,
        )
        ctx.log(
            f"[{self.name}] {ctx.state['jobs']} job(s) started on run {run_id} "
            f"— agent-task.yml compiled"
        )
        self.assert_callee(ctx, record, run_id)

    def refusal(self, ctx, record, run_id) -> str:
        try:
            timing = ctx.gh.run_timing(ctx.repo, run_id)
        except Exception as e:  # evidence, never the assertion
            ctx.log(f"[{self.name}] could not read run {run_id} timing ({e})")
            timing = None
        return (
            f"{REFUSED_MESSAGE}. Sandbox run {run_id} "
            f"({record.get('html_url') or ctx.repo}) concluded "
            f"{record.get('conclusion') or 'completed'!r} with 0 jobs"
            f"{billing_note(timing)}. That is the 2026-09-09 signature "
            f"(DRE-3484): a `run:` block carrying `${{{{ }}}}` compiles into "
            f"one expression, and past ~21,000 characters GitHub refuses the "
            f"whole file at dispatch. Read the run's own annotation before "
            f"concluding the YAML is at fault: a required secret this stub "
            f"does not provide refuses a called workflow the same way, with "
            f"the same zero jobs (DRE-2047/2067)."
        )

    def assert_callee(self, ctx, record, run_id) -> None:
        """Which reusable workflow did the sandbox actually compile, and at
        which commit? A started job on somebody else's workflow proves
        nothing about ours."""
        entry = callee_reference(record.get("referenced_workflows"))
        if entry is None:
            named = sorted(
                (e or {}).get("path", "")
                for e in record.get("referenced_workflows") or ()
            )
            raise ScenarioFailure(
                f"run {run_id} started a job but never referenced "
                f"{CALLEE_PATH} — the sandbox's {WORKFLOW_FILE} stub does not "
                f"call this repo's reusable workflow, so the run proves "
                f"nothing about it (referenced: {named or 'nothing'})"
            )
        parsed = (entry.get("sha") or "").strip()
        if not parsed:
            raise ScenarioFailure(
                f"run {run_id} references {CALLEE_PATH} at "
                f"{entry.get('ref') or 'an unnamed ref'} with no resolved "
                f"commit — there is no evidence of WHICH agent-task.yml "
                f"GitHub compiled"
            )
        ctx.state["parsed_sha"] = parsed
        ctx.state["parsed_ref"] = entry.get("ref") or ""

        tested = (getattr(ctx, "tested_sha", "") or "").strip()
        covers = bool(tested) and parsed == tested
        ctx.state["covers_commit_under_test"] = covers
        if covers:
            note = (
                f"agent-task.yml parses at {parsed[:12]} — the commit under "
                f"test"
            )
        elif tested:
            note = (
                f"agent-task.yml parses at {parsed[:12]} "
                f"({ctx.state['parsed_ref'] or 'the ref the stub pins'}), "
                f"which is NOT the commit under test {tested[:12]}. The "
                f"sandbox rides @main and GitHub resolves that ref at "
                f"dispatch, while harness.yml queues runs rather than "
                f"cancelling them — so a pull request's head, and a commit "
                f"overtaken by a merge burst, are compiled as whatever @main "
                f"was at dispatch time. Recorded, not failed."
            )
        else:
            note = (
                f"agent-task.yml parses at {parsed[:12]}; this run was not "
                f"told which commit it is proving (HARNESS_TESTED_SHA unset — "
                f"a local CLI run)"
            )
        ctx.state["coverage_note"] = note
        ctx.log(f"[{self.name}] {note}")

    # ── cleanup: do not leave a build agent running ──────────────────────
    def cleanup(self, ctx):
        gh, repo = ctx.gh, ctx.repo
        run_id = ctx.state.get("run_id")
        if run_id is not None:
            try:
                gh.cancel_workflow_run(repo, run_id)
                ctx.log(f"[{self.name}] cleanup: cancelled run {run_id}")
            except Exception as e:
                # Best effort by design: a run that already finished, or a
                # cancel GitHub refuses, is not a reason to fail a rehearsal
                # whose question is already answered.
                ctx.log(f"[{self.name}] cleanup: cancel run {run_id} ({e})")

        # A cancel is not instantaneous, so tidy anything the dispatched run
        # got far enough to create. Scoped to THIS card's branch prefix, never
        # a namespace-wide sweep: a sibling scenario may be mid-flight on its
        # own branches in the same namespace (DRE-3075).
        prefix = scenario_branch(ctx.run_id, self.name)
        try:
            for pr in gh.list_open_prs(repo):
                head = (pr.get("head") or {}).get("ref") or ""
                if head.startswith(prefix):
                    gh.close_pr(repo, pr["number"])
                    ctx.log(f"[{self.name}] cleanup: closed PR #{pr['number']}")
        except Exception as e:
            ctx.log(f"[{self.name}] cleanup: PR tidy-up ({e})")
        try:
            for branch in gh.matching_refs(repo, prefix):
                gh.delete_ref(repo, branch)
                ctx.log(f"[{self.name}] cleanup: deleted branch {branch}")
        except Exception as e:
            ctx.log(f"[{self.name}] cleanup: branch tidy-up ({e})")


SCENARIO = AgentTaskParses()
