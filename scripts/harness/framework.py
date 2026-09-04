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


class HarnessTimeout(Exception):
    """A polled condition never became true within its budget."""


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
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    log: Callable = print
    state: dict = field(default_factory=dict)  # per-run scratch, phase→phase


@dataclass
class ScenarioResult:
    scenario: str
    ok: bool
    failed_phase: Optional[str] = None
    errors: list = field(default_factory=list)
    notes: list = field(default_factory=list)


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
               sleep=time.sleep):
    """Poll until `poll()` returns truthy (that value is returned) or
    `timeout` seconds elapse (HarnessTimeout, naming what was awaited).
    Exceptions from poll() propagate — scenarios use that to fail fast on
    a state that can never become the awaited one."""
    start = clock()
    while True:
        value = poll()
        if value:
            return value
        if clock() - start >= timeout:
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
