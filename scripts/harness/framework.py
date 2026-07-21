"""Scenario framework for the integration harness (DRE-2098).

Four-phase scenarios (setup / exercise / verify / cleanup) against the
LIVE sandbox repo — cleanup ALWAYS runs, and a cleanup failure fails the
scenario (a green run that leaves a mess is a fail: cleanup is what
proves the sandbox is usable for the next run).

Namespacing: every branch a scenario creates is
`agent/harness-<run-id>-<scenario>`. The `agent/` prefix is the shape
should_review_pr.py reviews; the `harness-` marker + run id make
leftovers from ANY crashed previous run identifiable, so sweep_leftovers
can mop them up without ever touching real work (`agent/DRE-n-*` must
never match — deleting a real agent's branch would destroy in-flight
card work).

Verdict analysis REUSES merge_gate.py's own parsing (authorship,
structured first-line marker, sha binding), so the harness's idea of "a
verdict bound to the head sha" is definitionally the real gate's.
"""

from __future__ import annotations

import re
import secrets
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import merge_gate

# The sweepable namespace. agent/ = reviewed by should_review_pr.py;
# harness- = ours to delete. A run id follows, then the scenario name.
HARNESS_BRANCH_PREFIX = "agent/harness-"

# Where probe files land in the sandbox (merged ones included) — the sweep
# clears this directory on the default branch, so a run that crashed after
# its merge but before its cleanup leaves nothing permanent behind.
PROBE_DIR = "harness_runs"

# Run ids land verbatim in branch names and file paths: lowercase
# alphanumerics and dashes only, nothing that could escape the namespace
# or the ref syntax.
_RUN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,60}$")


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


def scenario_branch(run_id: str, scenario_name: str) -> str:
    return f"{HARNESS_BRANCH_PREFIX}{validate_run_id(run_id)}-{scenario_name}"


def is_harness_ref(ref: Optional[str]) -> bool:
    """True iff `ref` is a branch the harness created (any run id). The
    predicate every sweep decision rides on — it must never match a real
    agent branch."""
    if not ref:
        return False
    return ref.removeprefix("refs/heads/").startswith(HARNESS_BRANCH_PREFIX)


@dataclass
class HarnessContext:
    """Everything a scenario needs: the client, the sandbox, the identities
    under test, and injectable time (unit tests never really sleep)."""

    gh: object
    repo: str
    run_id: str
    worker_login: str = ""
    qa_login: str = ""
    verdict_timeout: float = 1500.0  # ≥ the critic job's 25-minute budget
    merge_timeout: float = 1200.0
    poll_interval: float = 30.0
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


def sweep_leftovers(gh, repo: str, log=print) -> dict:
    """Mop up everything a CRASHED previous run left in the sandbox: open
    harness PRs, harness branches, and merged probe files on the default
    branch. Entirely best-effort per item — a leftover that cannot be
    removed (e.g. branch protection) is logged and skipped, because a
    namespaced leftover must never fail the NEXT run either."""
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
        try:
            gh.close_pr(repo, pr["number"])
            swept["prs_closed"] += 1
            log(f"sweep: closed leftover PR #{pr['number']} ({head})")
        except Exception as e:
            log(f"sweep: could not close PR #{pr['number']} ({e})")

    try:
        stale_branches = gh.matching_refs(repo, HARNESS_BRANCH_PREFIX)
    except Exception as e:
        log(f"sweep: could not list branches ({e}) — skipping branch sweep")
        stale_branches = []
    for branch in stale_branches:
        try:
            gh.delete_ref(repo, branch)
            swept["branches_deleted"] += 1
            log(f"sweep: deleted leftover branch {branch}")
        except Exception as e:
            log(f"sweep: could not delete branch {branch} ({e})")

    try:
        default, _ = gh.default_branch(repo)
        entries = gh.list_dir(repo, PROBE_DIR, default)
    except Exception as e:
        log(f"sweep: could not list {PROBE_DIR}/ ({e}) — skipping file sweep")
        entries = []
    for entry in entries:
        if entry.get("type") != "file":
            continue
        try:
            if gh.delete_file(
                repo, default, entry["path"],
                "chore(harness): sweep leftover probe file",
            ):
                swept["files_deleted"] += 1
                log(f"sweep: deleted leftover {entry['path']}")
        except Exception as e:
            log(f"sweep: could not delete {entry['path']} ({e})")

    log(f"sweep: {swept}")
    return swept


def verdict_state(comments, qa_login: str, head_sha: str) -> tuple[str, str]:
    """Classify the latest qa-authored critic comment relative to
    `head_sha`, using the real gate's own parsing:

      none            — no qa-authored verdict comment at all
      neutral         — critic could-not-run status (no structured verdict)
      stale           — a verdict, but bound to a different (or no) sha
      APPROVE / REQUEST_CHANGES / … — a verdict bound to THIS head
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
        return "stale", f"verdict bound to {sha}, head is {head_sha}"
    return token, line


def same_bot(a: Optional[str], b: Optional[str]) -> bool:
    """Login equality tolerant of the reserved "[bot]" suffix — REST
    merged_by.login carries it, the minted token's app-slug does not."""

    def norm(login):
        return (login or "").removesuffix("[bot]").lower()

    return bool(norm(a)) and norm(a) == norm(b)
