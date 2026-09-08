#!/usr/bin/env python3
"""Red-main auto-repair: the dispatch decision (DRE-1927, stdlib only).

adr-red-main-auto-repair. When a product repo's CI completes with
conclusion=failure on the DEFAULT branch, red-main-repair.yml calls this
script BEFORE any agent spins up. It encodes guardrails 2 (no crash-loop)
and 3 (concurrency lock) as a deterministic decision — the workflow only
gathers the inputs from GitHub's own records and acts on the output:

  * Classify first (guardrail 2). A failure whose logs carry an infra
    fingerprint — the medic's rate-limit/auth signatures (medic_classify.py,
    the DRE-1921 discipline) plus runner-flake shapes, plus the integration
    harness's own sandbox-block receipt — is NOT a code failure a fix agent
    can fix. The repair backs off entirely: no agent, no retry (the medic
    already owns the retry-once; a rate-limit resets on its own).
  * Bounded attempts, keyed by the failing SHA (guardrail 2). At most 2
    repair attempts per distinct failing head SHA, tracked mechanically:
    the repair/<sha> (and repair/<sha>-2) branch + its PR ARE the attempt
    record — no external state. Budget exhausted → escalate=true (the
    workflow raises a deduplicated plain-English triage card), never a
    third swing.
  * One repair in flight per repo (guardrail 3). Any OPEN repair/* PR makes
    a new failure event a no-op — the in-flight repair's merge re-runs CI
    on main and either clears the newer failure or produces a fresh event.
  * Debounce by SHA (guardrail 3). The repair/<sha> branch already existing
    (agent still building, or died pre-PR) makes a duplicate event a no-op.
  * Fail-closed. Unreadable attempt records mean NO dispatch (a blind
    dispatch could double-run a repair; the next failure event retries with
    fresh records), and only a validated full 40-hex SHA ever becomes a
    branch name.

CLI (stdout appends verbatim to $GITHUB_OUTPUT; humans read stderr):

    red_main_repair.py decide \
        --conclusion <c> --head-branch <b> --default-branch <d> \
        --head-sha <sha> --log-file <f> --refs-file <f> --pulls-file <f> \
        [--workflow-name <name>]

  --refs-file  raw REST payload of GET git/matching-refs/heads/repair/
  --pulls-file raw REST payload of GET pulls?state=all&per_page=100
  --workflow-name  which workflow went red; scopes the harness's
                   sandbox-block receipt. Optional — absent means "not the
                   harness", i.e. the behaviour before DRE-3076's receipt
                   was read here.

Prints go=, branch=, attempt=, escalate=, reason= lines; exit 0 on every
decision (including the fail-closed ones). Anything genuinely unexpected
raises and fails the job loudly — the medic sees it (never fail open).
"""

from __future__ import annotations

import argparse
import json
import re
import sys

import medic_classify
import promote_channel

# The full infra fingerprint set for the repair trigger: the medic's
# rate-limit/auth signatures are the single source of truth (a signature
# added there must not silently miss here), extended with runner-flake
# shapes a rerun MIGHT clear but a fix agent can never fix. Unlike the
# medic's critic-scoped verdict, repair backs off on these for ANY main-CI
# failure — re-running against an exhausted limit deepens it, and there is
# no code change that un-flakes a runner.
INFRA_SIGNATURES = medic_classify._INFRA_SIGNATURES + (
    re.compile(r"lost communication with the server", re.I),
    re.compile(r"runner has received a shutdown signal", re.I),
    re.compile(r"no space left on device", re.I),
)

# The harness's own "the SANDBOX blocked this run" receipt (DRE-3076), written
# by scripts/harness/__main__.py and read by the release channel — one
# constant, never a second copy of the string.
SANDBOX_BLOCKED_MARKER = promote_channel.BLOCKED_MARKER

# Which workflow's log may be believed when it carries that marker. Scoped the
# way medic_classify scopes its own neutral marker (_is_qa_review), and for the
# same reason: tests/test_harness_sandbox_deadline.py carries "harness blocked:"
# FIXTURES, so a red unit suite quotes the string in its diff — and a red unit
# suite is exactly the failure a fix agent exists for. Only the harness saying
# it about itself means the sandbox was down.
_HARNESS_WORKFLOW = re.compile(r"integration harness", re.I)

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def is_sandbox_blocked(workflow_name: str, log_text: str) -> bool:
    """True iff the INTEGRATION HARNESS ended on its sandbox-block receipt.

    A block is not a verdict: the sandbox died before the scenarios could
    judge the commit ("NOT proven and NOT disproven; the next run re-proves
    it"). There is no diff that turns it green — run 34258403698 was a Linear
    quota exhausted in the sandbox, and the window resetting was the fix —
    so dispatching a fix agent at it spends a run on an innocent commit.
    """
    if not _HARNESS_WORKFLOW.search(workflow_name or ""):
        return False
    return SANDBOX_BLOCKED_MARKER in (log_text or "")


def is_infra_failure(log_text: str, workflow_name: str = "") -> bool:
    """True iff the failed run's logs carry an infra fingerprint — a failure
    class where dispatching a fix agent burns quota without fixing anything.

    `workflow_name` is optional and defaults to "not the harness": a caller
    pinned to an older reusable workflow omits it, and missing information
    must fall toward the CURRENT behaviour. A wrongly-dispatched agent costs
    one run; a wrongly-suppressed one leaves main red with nothing watching.
    """
    text = log_text or ""
    if medic_classify.CRITIC_NEUTRAL_MARKER in text:
        return True
    if is_sandbox_blocked(workflow_name, text):
        return True
    return any(sig.search(text) for sig in INFRA_SIGNATURES)


def repair_branch(sha: str, attempt: int) -> str:
    """The attempt's branch name: repair/<sha> for 1, repair/<sha>-N after."""
    return f"repair/{sha}" if attempt == 1 else f"repair/{sha}-{attempt}"


def _sha_record_re(sha: str) -> re.Pattern:
    return re.compile(rf"^repair/{re.escape(sha)}(?:-[0-9]+)?$")


def decide(
    *,
    conclusion: str,
    head_branch: str,
    default_branch: str,
    head_sha: str,
    log_text: str,
    refs,
    pulls,
    workflow_name: str = "",
) -> dict:
    """The whole trigger decision. `refs` is an iterable of existing branch
    names (plain, e.g. "repair/<sha>"); `pulls` an iterable of dicts with
    head_ref / state ("open"|"closed") / merged (bool) covering repair PRs
    of ANY state. Returns go / branch / attempt / escalate / reason."""

    def noop(reason: str, escalate: bool = False) -> dict:
        return {"go": False, "branch": "", "attempt": 0,
                "escalate": escalate, "reason": reason}

    if conclusion != "failure":
        return noop("not-a-failure")
    if not head_branch or head_branch != default_branch:
        # Branch CI failures already route through agent-fix and the medic.
        return noop("not-default-branch")
    if not _SHA_RE.match(head_sha or ""):
        return noop("bad-head-sha")
    if is_infra_failure(log_text, workflow_name):
        return noop("infra-backoff")

    record_re = _sha_record_re(head_sha)
    pulls = list(pulls)
    records = {r for r in refs if record_re.match(r)}
    records |= {p["head_ref"] for p in pulls if record_re.match(p["head_ref"])}

    if any(p["merged"] for p in pulls if record_re.match(p["head_ref"])):
        # A re-run of the original failed run after the fix merged.
        return noop("already-repaired")
    if any(p["state"] == "open" and p["head_ref"].startswith("repair/")
           for p in pulls):
        # One repair in flight per repo — regardless of which SHA it targets.
        return noop("repair-in-flight")

    attempts = len(records)
    if attempts >= 2:
        return noop("budget-exhausted", escalate=True)
    if attempts == 1:
        closed_unmerged = any(
            p["state"] == "closed" and not p["merged"]
            for p in pulls if record_re.match(p["head_ref"])
        )
        branch = repair_branch(head_sha, 2)
        if not closed_unmerged or branch in records:
            # Branch exists with no definitively-failed PR: the first agent
            # is still building (concurrency queues us behind it) or died
            # pre-PR (its run failed loudly; the medic owns that).
            return noop("duplicate-event")
        return {"go": True, "branch": branch, "attempt": 2,
                "escalate": False, "reason": "dispatch"}
    return {"go": True, "branch": repair_branch(head_sha, 1), "attempt": 1,
            "escalate": False, "reason": "dispatch"}


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #

def _read_text(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def _load_records(refs_file: str, pulls_file: str):
    """Normalize the raw REST payloads → (refs, pulls), or None on ANY
    malformed input (the fail-closed direction: no records, no dispatch)."""
    try:
        with open(refs_file) as f:
            raw_refs = json.load(f)
        with open(pulls_file) as f:
            raw_pulls = json.load(f)
        refs = [
            (r.get("ref") or "").removeprefix("refs/heads/")
            for r in raw_refs
        ]
        pulls = [
            {
                "head_ref": (p.get("head") or {}).get("ref") or "",
                "state": p.get("state") or "",
                "merged": bool(p.get("merged_at") or p.get("merged")),
            }
            for p in raw_pulls
        ]
    except (OSError, ValueError, AttributeError, TypeError):
        return None
    return refs, pulls


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("decide")
    d.add_argument("--conclusion", required=True)
    d.add_argument("--head-branch", required=True)
    d.add_argument("--default-branch", required=True)
    d.add_argument("--head-sha", required=True)
    d.add_argument("--log-file", required=True)
    d.add_argument("--refs-file", required=True)
    d.add_argument("--pulls-file", required=True)
    # Optional: which workflow went red, so the sandbox-block receipt is only
    # believed from the harness. Absent ⇒ "not the harness" ⇒ today's
    # behaviour, which is the safe direction for missing information.
    d.add_argument("--workflow-name", default="")
    args = parser.parse_args(argv)

    records = _load_records(args.refs_file, args.pulls_file)
    if records is None:
        decision = {"go": False, "branch": "", "attempt": 0,
                    "escalate": False, "reason": "records-unreadable"}
        print("repair decide: attempt records unreadable — fail-closed, no "
              "dispatch (the next failure event retries with fresh records)",
              file=sys.stderr)
    else:
        refs, pulls = records
        decision = decide(
            conclusion=args.conclusion,
            head_branch=args.head_branch,
            default_branch=args.default_branch,
            head_sha=args.head_sha,
            log_text=_read_text(args.log_file),
            refs=refs,
            pulls=pulls,
            workflow_name=args.workflow_name,
        )
        print(f"repair decide: {decision['reason']}"
              + (f" → {decision['branch']}" if decision["go"] else ""),
              file=sys.stderr)

    print(f"go={'true' if decision['go'] else 'false'}")
    print(f"branch={decision['branch']}")
    print(f"attempt={decision['attempt']}")
    print(f"escalate={'true' if decision['escalate'] else 'false'}")
    print(f"reason={decision['reason']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
