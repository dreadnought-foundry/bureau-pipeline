"""Scenario framework for the integration harness (DRE-2098).

Four-phase scenarios (setup / exercise / verify / cleanup) against the
LIVE sandbox repo — cleanup ALWAYS runs, and a cleanup failure fails the
scenario (a green run that leaves a mess is a fail: cleanup is what
proves the sandbox is usable for the next run).

Namespacing: every branch a scenario creates is
`agent/harness-<run-id>-<scenario>`, and every run id opens with its
NAMESPACE — `main-…` for a push to main and for a hand dispatch,
`pr<number>-…` for a pull request's proving run, `local-…` off the CLI.
The `agent/` prefix is the shape should_review_pr.py reviews; the
`harness-` marker + run id make leftovers from ANY crashed previous run
identifiable, so sweep_leftovers can mop them up without ever touching
real work (`agent/DRE-n-*` must never match — deleting a real agent's
branch would destroy in-flight card work).

The namespace is what lets main's run and a PR's run share the sandbox
(DRE-3075). They now hold separate concurrency slots, so a PR's 18-minute
proving run can no longer make five pushes to main queue behind it and
replace each other — but two live runs mean the sweep can no longer
delete every harness leftover it finds, because half of them belong to
the OTHER run. Each sweep collects its own namespace, and a foreign
namespace's leftover only once it is older than a whole harness run can
last (STALE_LEFTOVER_SECONDS), which is the one state in which it cannot
belong to a run that is still going.

Verdict analysis REUSES merge_gate.py's own parsing (authorship,
structured first-line marker, sha binding), so the harness's idea of "a
verdict bound to the head sha" is definitionally the real gate's.
"""

from __future__ import annotations

import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

import merge_gate
import promote_channel

# The sweepable namespace. agent/ = reviewed by should_review_pr.py;
# harness- = ours to delete. A run id follows, then the scenario name.
HARNESS_BRANCH_PREFIX = "agent/harness-"

# Second sweepable namespace (DRE-2100): gate_paths probes merge_gate's
# condition D with a dependabot-NAMED branch. The harness- marker keeps it
# ours to delete and disjoint from every genuine Dependabot branch —
# those are dependabot/<ecosystem>/..., and no ecosystem token starts
# with "harness-" (sweeping a real one would kill the vendor PR
# dependabot_flow exists to consume).
DEPENDABOT_HARNESS_BRANCH_PREFIX = "dependabot/harness-"

# Every prefix the sweep owns; is_harness_ref rides on this tuple.
HARNESS_BRANCH_PREFIXES = (HARNESS_BRANCH_PREFIX, DEPENDABOT_HARNESS_BRANCH_PREFIX)

# Where probe files land in the sandbox (merged ones included) — the sweep
# clears this directory on the default branch, so a run that crashed after
# its merge but before its cleanup leaves nothing permanent behind.
# DELIBERATELY not a Python identifier: setuptools flat-layout
# auto-discovery counts every top-level identifier-named directory as a
# package, and the sandbox's own `pip install -e .` died on the previous
# name ("Multiple top-level packages discovered in a flat-layout:
# ['harness_pkg', 'harness_runs']") — red CI on every probe PR, so the
# merge gate correctly never merged one (run 29795108949).
PROBE_DIR = "harness-runs"

# Prior probe homes: swept, never written. Run 29795108949's cleanup died
# on the token 401 and stranded a harness_runs/ file on the sandbox's
# default branch — poisoning CI for every branch cut from it until swept.
LEGACY_PROBE_DIRS = ("harness_runs",)

# Run ids land verbatim in branch names and file paths: lowercase
# alphanumerics and dashes only, nothing that could escape the namespace
# or the ref syntax.
_RUN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,60}$")

# The namespace opens the run id and a single dash separates the two, so a
# namespace may NOT contain that dash: were it allowed, namespace `pr` and
# namespace `pr-251` would have sweep prefixes where one matches the
# other's branches, and the isolation would hold only for the namespace
# pairs someone happened to think about. Without the delimiter the
# prefixes are pairwise disjoint for ANY two distinct namespaces, which is
# a property rather than a review. Hence `pr251`, while harness.yml's
# concurrency group — a different, GitHub-side key — spells it `pr-251`.
_NAMESPACE_RE = re.compile(r"^[a-z0-9]{1,24}$")

# The namespace of a run nobody told: a CLI run against the sandbox.
DEFAULT_NAMESPACE = "local"

# When a leftover from ANOTHER namespace may be collected. A run cannot
# outlive its own job timeout (harness.yml `timeout-minutes: 180`), so
# anything older than twice that ceiling belongs to no live run — and
# scoping the sweep must not make a crashed run's mess immortal: a PR that
# crashed and then merged leaves a namespace nothing would ever sweep
# again. Pinned to the workflow's own cap by
# tests/test_harness_main_slot.py.
STALE_LEFTOVER_SECONDS = 6 * 60 * 60


# How long the shipped critic is ALLOWED to take: qa-review.yml's `review`
# job wall clock (65 minutes since DRE-2466 — attempt 1, the mandatory
# backoff and the retry are 42.8 of them on their own). A verdict can
# legitimately land at any moment inside that clock, so a shorter wait here
# does not test the pipeline, it races it: the driver gives up, the verdict
# posts minutes later, and the harness reports FAIL on a healthy run. That
# is what run 33274348041 was — gate_paths' named leg timed out with the PR
# untouched and the gate's own waiting-for-human state already posted.
#
# Pinned to the workflow by tests/test_harness_wiring.py, because the drift
# it cost us was silent: the wait was written when that cap was 25 minutes
# and simply stayed there when the cap moved.
CRITIC_JOB_BUDGET_SECONDS = 65 * 60
# The critic's clock starts when its RUN starts. Before that come the
# minting steps, two checkouts, and whatever queue the sandbox's own runners
# are in — a burst of harness PRs opens three reviews at once.
CRITIC_STARTUP_ALLOWANCE_SECONDS = 5 * 60
VERDICT_TIMEOUT_SECONDS = float(
    CRITIC_JOB_BUDGET_SECONDS + CRITIC_STARTUP_ALLOWANCE_SECONDS
)
MERGE_TIMEOUT_SECONDS = 1200.0
POLL_INTERVAL_SECONDS = 30.0

# The per-wait DEADLINE (DRE-3076) — the thing the budgets above are not.
#
# Those budgets answer "how long may a healthy pipeline take?", and they have
# to be generous: the critic's own job clock is 65 minutes. What they cannot
# answer is "is anything still happening at all?", so on 2026-09-03 a scenario
# waiting on a sweep that had died at 20:27 PT on a Linear rate limit waited
# out the JOB's `timeout-minutes: 180` — no promotion for three hours, on the
# night the channel was busiest.
#
# So this is not a shorter cap; a shorter cap would report FAIL on a healthy
# slow review (run 33274348041, DRE-2466). It is a CHECKPOINT: every this many
# seconds a wait stops and asks a different question — did the sandbox's own
# sweep / gate / linear-sync just fail? — and only a YES ends it. A healthy
# sandbox keeps the full budget. The passing PR run does all its scenarios in
# 18 minutes, so a single silent wait past ten is already a fault worth
# looking at.
WAIT_DEADLINE_SECONDS = 10 * 60.0

#: The driver's exit code for "the SANDBOX blocked this run" — distinct from 1
#: (a scenario failed, which is a statement about the commit) and 2 (bad
#: invocation). Nothing is proven either way; the next run re-proves.
BLOCKED_EXIT = 3


class HarnessTimeout(Exception):
    """A polled condition never became true within its budget."""


class SandboxBlocked(Exception):
    """The sandbox's own machinery is failing — the harness has been waiting on
    a corpse.

    NOT a verdict on the commit under test, and the whole point of having its
    own type: a rate-limited sandbox is not a failed commit. `.cause` is the
    marker-prefixed quote of the sandbox's last failure, which becomes the
    promote receipt.
    """

    def __init__(self, message: str, cause: str = ""):
        super().__init__(message)
        self.cause = cause or message


class ScenarioFailure(Exception):
    """A scenario assertion failed — the pipeline did not do what the
    happy path promises."""


def new_run_id() -> str:
    return f"local-{time.strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"


def validate_run_id(run_id: str) -> str:
    if not isinstance(run_id, str) or not _RUN_ID_RE.match(run_id):
        raise ValueError(
            f"unsafe run id {run_id!r}: need lowercase [a-z0-9-], "
            f"3-61 chars, starting alphanumeric"
        )
    return run_id


def validate_namespace(namespace: str) -> str:
    if not isinstance(namespace, str) or not _NAMESPACE_RE.match(namespace):
        raise ValueError(
            f"unsafe harness namespace {namespace!r}: need lowercase "
            f"[a-z0-9], 1-24 chars and NO dash (the dash separates the "
            f"namespace from the run id)"
        )
    return namespace


def namespaced_run_id(namespace: str, base: str) -> str:
    """`base`, guaranteed to open with `namespace`. Idempotent, so the
    local default (`new_run_id()` already yields `local-…`) is unchanged
    and a re-run cannot double the prefix."""
    ns = validate_namespace(namespace)
    if base.startswith(f"{ns}-"):
        return validate_run_id(base)
    return validate_run_id(f"{ns}-{base}")


def namespace_branch_prefixes(namespace: str) -> tuple:
    """The branch prefixes ONE namespace owns — what its sweep may delete."""
    ns = validate_namespace(namespace)
    return tuple(f"{prefix}{ns}-" for prefix in HARNESS_BRANCH_PREFIXES)


def probe_file_prefix(namespace: str) -> str:
    """Probe files are `<run-id>-<scenario>.md`, so the run id's namespace
    is the file name's prefix too."""
    return f"{validate_namespace(namespace)}-"


def scenario_branch(run_id: str, scenario_name: str) -> str:
    return f"{HARNESS_BRANCH_PREFIX}{validate_run_id(run_id)}-{scenario_name}"


def dependabot_scenario_branch(run_id: str, scenario_name: str) -> str:
    """A dependabot-NAMED harness branch (condition-D shaped, DRE-2100) —
    still inside the sweepable namespace, never a genuine Dependabot ref."""
    return (
        f"{DEPENDABOT_HARNESS_BRANCH_PREFIX}{validate_run_id(run_id)}-{scenario_name}"
    )


def is_harness_ref(ref: Optional[str]) -> bool:
    """True iff `ref` is a branch the harness created (any run id). The
    predicate every sweep decision rides on — it must never match a real
    agent branch, nor a genuine Dependabot branch."""
    if not ref:
        return False
    return ref.removeprefix("refs/heads/").startswith(HARNESS_BRANCH_PREFIXES)


def is_own_harness_ref(ref: Optional[str], namespace: str) -> bool:
    """True iff `ref` is a branch THIS namespace's runs created. The sweep
    rides on this one: a ref that is a harness ref but not our own belongs
    to another run, which may still be using it (DRE-3075)."""
    if not ref:
        return False
    return ref.removeprefix("refs/heads/").startswith(
        namespace_branch_prefixes(namespace)
    )


@dataclass
class HarnessContext:
    """Everything a scenario needs: the client, the sandbox, the identities
    under test, and injectable time (unit tests never really sleep)."""

    gh: object
    repo: str
    run_id: str
    # The sandbox namespace this run owns (DRE-3075) — `main`, `pr<number>`
    # or `local`. Every sweep is scoped to it, and `run_id` opens with it.
    namespace: str = DEFAULT_NAMESPACE
    worker_login: str = ""
    qa_login: str = ""
    # Second client for reads only the qa App is proven to have (check-runs
    # — merge-gate.yml's own read path); None = fall back to the worker
    # client and let a permission refusal surface loudly.
    gh_qa: object = None
    # Third client, scoped to the CONSOLE's repository (DRE-2726). The lane
    # contract's console-parity clause reads that repo's own state lists; the
    # token is minted separately because it is a different installation. None
    # = the clause reports UNEVALUATED, which the contract turns into a hard
    # failure from the phase it names — never a silent pass.
    gh_console: object = None
    # The worker-bot installation token, for the one thing the REST client
    # cannot do for a scenario: hand a real build agent a sandbox clone it can
    # push (DRE-2490). Never logged — agent_run builds every log line from the
    # repo slug, never from the clone URL.
    worker_token: str = ""
    verdict_timeout: float = VERDICT_TIMEOUT_SECONDS  # ≥ the critic's own cap
    merge_timeout: float = MERGE_TIMEOUT_SECONDS
    poll_interval: float = POLL_INTERVAL_SECONDS
    # How often a wait stops to ask whether the sandbox is still alive
    # (DRE-3076). 0 disables the check — the operator's escape hatch, and the
    # pre-DRE-3076 behaviour.
    wait_deadline: float = WAIT_DEADLINE_SECONDS
    # `(description, elapsed) -> quote | None`, from sandbox_health.probe. None
    # here means no probe is wired and every wait runs its full budget.
    sandbox_probe: Optional[Callable] = None
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    log: Callable = print
    state: dict = field(default_factory=dict)  # per-run scratch, phase→phase

    def wait(self, description, poll, timeout, interval=None):
        """THE way a scenario waits for the sandbox to do something.

        Every wait goes through here rather than calling `wait_until` directly,
        because the deadline is not something a call site should have to
        remember: the wait somebody adds next year is exactly the one that
        would sit for the job's three-hour ceiling
        (`tests/test_harness_sandbox_deadline.py` pins the absence of bare
        calls in `scenarios/`).
        """
        return wait_until(
            description,
            poll,
            timeout=timeout,
            interval=self.poll_interval if interval is None else interval,
            clock=self.clock,
            sleep=self.sleep,
            deadline=self.wait_deadline,
            on_deadline=self.sandbox_probe,
        )


@dataclass
class ScenarioResult:
    scenario: str
    ok: bool
    failed_phase: Optional[str] = None
    errors: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    #: The sandbox's quoted last failure when THIS scenario died on a dead
    #: sandbox rather than on anything the commit did (DRE-3076). Set means
    #: "not proven either way"; the driver stops the run and the promote
    #: receipt says blocked, not failed.
    blocked: Optional[str] = None


class Scenario:
    """Base scenario: override any subset of the four phases."""

    name = ""

    # True for scenarios that spend a REAL build-agent run (DRE-2490). They
    # cost model minutes and money, so they are opt-in: the default sweep
    # (empty `--scenarios`) skips them and they are selected by name. See
    # __main__.select_names — harness.yml's check run holds this repo's merge
    # gate, and five agent runs per boundary PR would hold every merge.
    requires_agent = False

    def setup(self, ctx: HarnessContext) -> None: ...

    def exercise(self, ctx: HarnessContext) -> None: ...

    def verify(self, ctx: HarnessContext) -> None: ...

    def cleanup(self, ctx: HarnessContext) -> None: ...


def run_scenario(scenario: Scenario, ctx: HarnessContext) -> ScenarioResult:
    """Run one scenario. setup → exercise → verify stop at the first
    failure; cleanup runs REGARDLESS and its failure is recorded without
    masking the primary error."""
    result = ScenarioResult(scenario=scenario.name, ok=True)
    for phase in ("setup", "exercise", "verify"):
        ctx.log(f"[{scenario.name}] {phase}")
        try:
            getattr(scenario, phase)(ctx)
        except SandboxBlocked as e:
            # Recorded apart from a failure ON PURPOSE: the scenario did not
            # find anything wrong with the commit — it never got to look.
            result.ok = False
            result.failed_phase = phase
            result.blocked = e.cause
            result.errors.append(f"{phase}: blocked by the sandbox: {e}")
            break
        except Exception as e:  # any failure: record, stop progressing
            result.ok = False
            result.failed_phase = phase
            result.errors.append(f"{phase}: {type(e).__name__}: {e}")
            break
    ctx.log(f"[{scenario.name}] cleanup")
    try:
        scenario.cleanup(ctx)
    except Exception as e:
        result.ok = False
        result.failed_phase = result.failed_phase or "cleanup"
        result.errors.append(f"cleanup: {type(e).__name__}: {e}")
    return result


def wait_until(description, poll, timeout, interval, clock=time.monotonic,
               sleep=time.sleep, deadline=None, on_deadline=None):
    """Poll until `poll()` returns truthy (that value is returned) or
    `timeout` seconds elapse (HarnessTimeout, naming what was awaited).
    Exceptions from poll() propagate — scenarios use that to fail fast on
    a state that can never become the awaited one.

    `on_deadline(description, elapsed)` is the sandbox-liveness question
    (DRE-3076), asked every `deadline` seconds and once more when the budget
    expires. It returns a quote when the SANDBOX has failed, and that ends the
    wait with `SandboxBlocked` — the run stops there rather than waiting out
    the job's ceiling. It returns None for healthy and for unknown alike, and
    the wait then keeps its full budget: a slow critic is not a dead sandbox.

    Scenarios call `HarnessContext.wait`, which wires both from the context;
    this signature is the mechanism, not the call site.
    """
    start = clock()
    # `deadline <= 0` is the operator's off switch and means no liveness check
    # AT ALL, expiry included — the pre-DRE-3076 behaviour. `deadline=None`
    # keeps the check but only at expiry.
    probing = bool(on_deadline) and (deadline is None or deadline > 0)
    next_check = deadline if (probing and deadline) else None
    while True:
        value = poll()
        if value:
            return value
        elapsed = clock() - start
        expired = elapsed >= timeout
        if probing and (expired or (next_check is not None and elapsed >= next_check)):
            cause = on_deadline(description, elapsed)
            if cause:
                raise SandboxBlocked(
                    f"{cause} — gave up after {elapsed:.0f}s waiting for "
                    f"{description}",
                    cause=cause,
                )
            if next_check is not None:
                next_check = elapsed + deadline
        if expired:
            raise HarnessTimeout(
                f"timed out after {timeout:.0f}s waiting for {description}"
            )
        sleep(interval)


def leftover_pr_numbers(gh, repo: str, namespace: str) -> list:
    """The open harness PRs of THIS namespace — every scenario's closing
    "the sandbox is usable for the next run" assertion.

    Scoped for the same reason the sweep is (DRE-3075): another lane's
    harness PRs are open because that run is still using them, and a
    cleanup check that counted those would fail a perfectly healthy run
    for its neighbour's work. One definition, four scenarios.
    """
    return [
        pr["number"]
        for pr in gh.list_open_prs(repo)
        if is_own_harness_ref((pr.get("head") or {}).get("ref", ""), namespace)
    ]


def _age_seconds(stamp, now: float) -> Optional[float]:
    """Seconds since an ISO8601 REST timestamp, or None when there isn't
    one to read. None means UNKNOWN, and unknown is never old enough."""
    if not stamp:
        return None
    try:
        when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return now - when.timestamp()


def _is_stale(stamp, now: float) -> bool:
    age = _age_seconds(stamp, now)
    return age is not None and age >= STALE_LEFTOVER_SECONDS


def _collectable(gh, repo, ref, path, now, log) -> bool:
    """May this sweep delete a leftover belonging to ANOTHER namespace?

    Only once it is older than any live run can be. Every failure to
    establish that answers NO: an unreadable date is not a licence to
    delete a branch a concurrent run may be pushing to right now.
    """
    try:
        stamp = gh.last_commit_date(repo, ref, path)
    except Exception as e:
        log(f"sweep: could not date {path or ref} ({e}) — leaving it alone")
        return False
    return _is_stale(stamp, now)


def sweep_leftovers(gh, repo: str, namespace: str, log=print, now=None) -> dict:
    """Mop up everything a CRASHED previous run of THIS namespace left in
    the sandbox: open harness PRs, harness branches, and merged probe files
    on the default branch. Entirely best-effort per item — a leftover that
    cannot be removed (e.g. branch protection) is logged and skipped,
    because a namespaced leftover must never fail the NEXT run either.

    Another namespace's leftovers are left alone unless they are older than
    STALE_LEFTOVER_SECONDS (DRE-3075): main's run and a PR's proving run
    now hold separate concurrency slots and can be in the sandbox at the
    same time, so an unconditional sweep would delete the branches the
    other run is mid-scenario on.
    """
    ns = validate_namespace(namespace)
    now = (now or time.time)()
    swept = {"branches_deleted": 0, "prs_closed": 0, "files_deleted": 0}

    try:
        open_prs = gh.list_open_prs(repo)
    except Exception as e:
        log(f"sweep: could not list open PRs ({e}) — skipping PR sweep")
        open_prs = []
    for pr in open_prs:
        head = (pr.get("head") or {}).get("ref", "")
        if not is_harness_ref(head):
            continue
        # `created_at` rides along on the list shape — no second call to
        # date a foreign PR.
        if not is_own_harness_ref(head, ns) and not _is_stale(
            pr.get("created_at"), now
        ):
            log(f"sweep: PR #{pr['number']} ({head}) belongs to another run — left")
            continue
        try:
            gh.close_pr(repo, pr["number"])
            swept["prs_closed"] += 1
            log(f"sweep: closed leftover PR #{pr['number']} ({head})")
        except Exception as e:
            log(f"sweep: could not close PR #{pr['number']} ({e})")

    # Listed across ALL namespaces on purpose: a namespace nothing will
    # ever run again (a PR that crashed and then merged) still owes the
    # sandbox its cleanup, and the age check is what makes taking it safe.
    stale_branches = []
    for prefix in HARNESS_BRANCH_PREFIXES:
        try:
            stale_branches.extend(gh.matching_refs(repo, prefix))
        except Exception as e:
            log(f"sweep: could not list {prefix}* branches ({e}) — skipping")
    for branch in stale_branches:
        if not is_own_harness_ref(branch, ns) and not _collectable(
            gh, repo, branch, None, now, log
        ):
            log(f"sweep: branch {branch} belongs to another run — left")
            continue
        try:
            gh.delete_ref(repo, branch)
            swept["branches_deleted"] += 1
            log(f"sweep: deleted leftover branch {branch}")
        except Exception as e:
            log(f"sweep: could not delete branch {branch} ({e})")

    try:
        default, _ = gh.default_branch(repo)
    except Exception as e:
        log(f"sweep: could not resolve the default branch ({e}) — skipping file sweep")
        default = None
    entries = []
    for probe_dir in (PROBE_DIR, *LEGACY_PROBE_DIRS) if default else ():
        try:
            entries.extend(gh.list_dir(repo, probe_dir, default))
        except Exception as e:
            log(f"sweep: could not list {probe_dir}/ ({e}) — skipping file sweep")
    own_file = probe_file_prefix(ns)
    for entry in entries:
        if entry.get("type") != "file":
            continue
        path = entry["path"]
        # A LEGACY dir is never written any more, so whatever is in one
        # predates namespacing and belongs to no live run.
        #
        # The agent scenarios' seed files are named for what they contain
        # rather than for the run (unverified_claim imports one BY NAME to
        # run a shipped test against it), so they read as foreign here and
        # a crashed run's seed waits for the staleness path. Harmless: the
        # next run of that scenario overwrites the file in place, and those
        # scenarios are opt-in by name, which only ever happens under
        # `main`.
        legacy = not path.startswith(f"{PROBE_DIR}/")
        if (
            not legacy
            and not path.rsplit("/", 1)[-1].startswith(own_file)
            and not _collectable(gh, repo, default, path, now, log)
        ):
            log(f"sweep: {path} belongs to another run — left")
            continue
        try:
            if gh.delete_file(
                repo, default, path,
                "chore(harness): sweep leftover probe file",
            ):
                swept["files_deleted"] += 1
                log(f"sweep: deleted leftover {path}")
        except Exception as e:
            log(f"sweep: could not delete {path} ({e})")

    log(f"sweep[{ns}]: {swept}")
    return swept


#: The gate's carry receipt (DRE-2340), as posted by merge-gate.yml. A verdict
#: whose sha no longer matches the head still COVERS that head when the gate
#: published one of these for it — see `verdict_state`.
CARRY_MARKER = "Merge gate: carried verdict content:"


def carried_to_head(comments, qa_login: str, head_sha: str,
                    content_id: str | None) -> bool:
    """Did the gate publish a receipt carrying `content_id` onto `head_sha`?

    Author-checked with the same `same_bot` rule every other credential in
    here uses: the receipt is what lets a verdict outlive its sha, so a
    receipt anyone could forge would be a merge anyone could force.
    """
    if not content_id:
        return False  # a pre-DRE-2340 verdict binds a sha and nothing else
    needle = f"{CARRY_MARKER}{content_id}"
    for c in comments or ():
        if not same_bot((c.get("user") or {}).get("login"), qa_login):
            continue
        body = c.get("body") or ""
        if needle in body and head_sha in body:
            return True
    return False


def verdict_state(comments, qa_login: str, head_sha: str) -> tuple[str, str]:
    """Classify the latest qa-authored critic comment relative to
    `head_sha`, using the real gate's own parsing:

      none            — no qa-authored verdict comment at all
      neutral         — critic could-not-run status (no structured verdict)
      stale           — a verdict that does not cover this head
      APPROVE / REQUEST_CHANGES / … — a verdict that COVERS this head

    "Covers" is not "bound to". Since DRE-2340 a verdict survives a head change
    when the PR's own three-dot diff is byte-identical — a gate-initiated base
    merge moves the head without touching what the PR contributes — and the
    gate publishes a receipt saying so. Reading only the sha made this function
    call those legitimate merges stale: gate_paths failed its stale and skew
    legs on every run from 2026-08-10 until this was fixed, red for every PR in
    the repo regardless of what it changed.

    The RECEIPT is the evidence, deliberately. Recomputing the content id here
    would re-implement the gate's judgment and could agree with a bug in it;
    requiring the gate's published reason means an unexplained carry still
    reads as stale, which is the property the scenario exists to protect.
    """
    body = merge_gate.latest_verdict_comment(
        comments, qa_login, merge_gate.CRITIC_MARKER
    )
    if body is None:
        return "none", "no qa-authored verdict comment"
    line = merge_gate.first_line(body)
    token = merge_gate.verdict_token(line, merge_gate.CRITIC_MARKER)
    if token is None:
        return "neutral", line
    sha = merge_gate.verdict_sha(line)
    if sha != head_sha:
        content_id = merge_gate.verdict_content_id(line)
        if carried_to_head(comments, qa_login, head_sha, content_id):
            return token, (f"{line} — carried to {head_sha} on "
                           f"content:{content_id}")
        return "stale", f"verdict bound to {sha}, head is {head_sha}"
    return token, line


def run_id_of_harness_ref(ref: Optional[str]) -> Optional[str]:
    """The run id embedded in a harness branch, or None if the ref is not
    one of ours.

    A harness branch is `<prefix><run-id>-<scenario>` and scenario names
    carry underscores and never dashes (`bot_pr_flow`, `gate_paths`), so
    the run id is everything before the LAST dash. That is what lets a wipe
    receipt name the culprit: the run id opens with the namespace, so
    `agent/harness-pr264-gha-33899093729-1-gate_paths` says which pull
    request's run was in the sandbox.
    """
    if not is_harness_ref(ref):
        return None
    tail = ref.removeprefix("refs/heads/")
    for prefix in HARNESS_BRANCH_PREFIXES:
        if tail.startswith(prefix):
            tail = tail.removeprefix(prefix)
            break
    if "-" not in tail:
        return None
    run_id = tail.rsplit("-", 1)[0]
    return run_id if _RUN_ID_RE.match(run_id) else None


def foreign_harness_refs(gh, repo: str, namespace: str, log=print) -> list:
    """Harness branches in the sandbox belonging to ANOTHER namespace.

    Read only to name a culprit, never to act, so every failure answers
    with an empty list: an unnameable run is reported as unnameable, never
    guessed at (`standards/console-honesty.md` rule 1 — unknown is shown as
    unknown).
    """
    ns = validate_namespace(namespace)
    refs = []
    for prefix in HARNESS_BRANCH_PREFIXES:
        try:
            refs.extend(gh.matching_refs(repo, prefix) or [])
        except Exception as e:
            log(f"wipe report: could not list {prefix}* branches ({e})")
    return [r for r in refs if is_harness_ref(r) and not is_own_harness_ref(r, ns)]


def wiped_probe_cause(number, namespace: str, foreign_refs) -> str:
    """The promote receipt for a probe another run closed (DRE-3101).

    Marker first, because GitHub clamps a status description at 140
    characters and `promote_channel.evaluate` has to recognise a clamped
    receipt (`sandbox_health.receipt_line` elides the middle for exactly
    this reason).
    """
    head = (
        f"{promote_channel.WIPED_MARKER} {namespace}'s probe PR #{number} was "
        f"wiped by "
    )
    named = [(run_id_of_harness_ref(ref), ref) for ref in foreign_refs or []]
    named = [(run_id, ref) for run_id, ref in named if run_id]
    if not named:
        return (
            f"{head}a concurrent harness run — could not name which: no other "
            f"namespace's branch was left in the sandbox to read it from"
        )
    # Newest first, so a sandbox holding several foreign runs names the one
    # most likely to still be in it. Ties are broken by the ref itself, so
    # the receipt is the same string on a re-run.
    run_id, ref = sorted(named, reverse=True)[0]
    return f"{head}run {run_id} (branch {ref})"


def probe_pr(gh, repo: str, number, namespace: str = DEFAULT_NAMESPACE,
             log=print) -> dict:
    """The probe PR, with the ONE state a polling wait cannot recover from
    named where it happens: closed, unmerged, gone out from under the wait.

    A wait that polls for a COMMENT — a critic verdict, the gate's status
    note — reads only `list_comments`, so it cannot tell "the pipeline has
    not answered yet" from "the pull request this wait is about no longer
    exists". Both look like an empty list, and the first is worth waiting
    the full budget for while the second can never resolve. So the wait
    sits out its budget and then reports the pipeline it was waiting on.

    That is run 33899093729, red main: at 17:36 a concurrent PR harness run
    — driving the sandbox from its own head, which predated the namespaced
    sweep (DRE-3075) — closed main's live `gate_paths` probe PR #929 and
    deleted its branch. Main's named leg spent 70 of the run's 76 minutes
    waiting for a critic comment on a closed PR and failed with `timed out
    after 4200s waiting for a critic comment on PR #929`. The sandbox's
    critic was healthy throughout: a PR-lane harness run reviewed the same
    leg's PR in minutes at 18:16.

    A MERGE is not this state. It is closure by the pipeline doing its job,
    and it is the success the skew and stale legs wait for, so it passes
    through untouched.

    DRE-3101 gave the ending its RECEIPT. A wiped probe says nothing about
    the commit under test — the run never got to judge it — so it is
    `SandboxBlocked`, the shape DRE-3076 gave a dead sandbox, and its cause
    names the run that did it (`wiped_probe_cause`). Before that, the only
    record was a run log, and diagnosing 2026-09-04 meant reading two run
    histories side by side to learn which pull request's sweep had closed
    main's probe.
    """
    pr = gh.get_pr(repo, number)
    if pr.get("merged") or pr.get("state") == "open":
        return pr
    raise SandboxBlocked(
        f"probe PR #{number} is {pr.get('state')!r} and not merged — it was "
        "closed out from under this wait (a concurrent harness run's sweep, "
        "or a human). Nothing more can happen on it, so waiting longer would "
        "only time out against whatever the pipeline was asked for",
        cause=wiped_probe_cause(
            number, namespace, foreign_harness_refs(gh, repo, namespace, log)
        ),
    )


def find_real_dependabot_pr(prs) -> Optional[dict]:
    """The OLDEST open PR that is genuinely Dependabot's, from REST list
    shapes: dependabot/-named head, NOT the harness's own dependabot-named
    namespace, and authored by the literal dependabot[bot] (GitHub-reserved
    suffix — unforgeable; branch names are free text). Reuses merge_gate's
    own constants so "genuine" means exactly what condition D means."""
    genuine = [
        pr
        for pr in prs
        if ((pr.get("head") or {}).get("ref") or "").startswith(
            merge_gate.DEPENDABOT_BRANCH_PREFIX
        )
        and not is_harness_ref(pr["head"]["ref"])
        and ((pr.get("user") or {}).get("login") or "") == merge_gate.DEPENDABOT_LOGIN
    ]
    return min(genuine, key=lambda p: p.get("number", 0)) if genuine else None


def same_bot(a: Optional[str], b: Optional[str]) -> bool:
    """Login equality tolerant of the reserved "[bot]" suffix — REST
    merged_by.login carries it, the minted token's app-slug does not."""

    def norm(login):
        return (login or "").removesuffix("[bot]").lower()

    return bool(norm(a)) and norm(a) == norm(b)
