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
  * …except a step the clock has already killed once (DRE-4674). A timeout
    can be infrastructure ONCE. The same step — same workflow file path,
    same job name, same step name — timing out again is the code: the suite
    outgrew the limit, and no amount of waiting fixes that. `--history-file`
    carries the runs this one may be compared against (written by
    `repair_history.py`), and a repeat there turns the backoff into a
    `repair` that names the job, the step, the limit and the commits.
  * Bounded attempts, keyed by the failing SHA (guardrail 2). At most 2
    repair attempts per distinct failing head SHA, tracked mechanically:
    the repair branch (`repair/DRE-<n>-<sha12>` since DRE-3533, or the
    cardless `repair/<sha>` fallback) and its PR ARE the attempt record —
    no external state, and both shapes count as the same repair. Budget
    exhausted → escalate=true (the workflow raises a deduplicated
    plain-English triage card), never a third swing.
  * One repair in flight per repo (guardrail 3). Any OPEN repair/* PR makes
    a new failure event a no-op — the in-flight repair's merge re-runs CI
    on main and either clears the newer failure or produces a fresh event.
  * Debounce by SHA (guardrail 3). A repair branch for this SHA already
    existing (agent still building, or died pre-PR) makes a duplicate event
    a no-op.
  * Fail-closed. Unreadable attempt records mean NO dispatch (a blind
    dispatch could double-run a repair; the next failure event retries with
    fresh records), and only a validated full 40-hex SHA ever becomes a
    branch name.

CLI (stdout appends verbatim to $GITHUB_OUTPUT; humans read stderr):

    red_main_repair.py decide \
        --conclusion <c> --head-branch <b> --default-branch <d> \
        --head-sha <sha> --log-file <f> --refs-file <f> --pulls-file <f> \
        [--workflow-name <name>] [--history-file <f>]

  --refs-file  raw REST payload of GET git/matching-refs/heads/repair/
  --pulls-file raw REST payload of GET pulls?state=all&per_page=100
  --workflow-name  which workflow went red; scopes the harness's
                   sandbox-block receipt. Optional — absent means "not the
                   harness", i.e. the behaviour before DRE-3076's receipt
                   was read here.
  --history-file   the recent-runs document `repair_history.py` writes.
                   Optional, and a file that is missing, marker-filled or
                   malformed reads as "no history" — today's behaviour. It
                   is never read as "repeated" (DRE-4674).

Emits go=, branch=, attempt=, escalate=, reason= and the timeout_* detail
(job / step / limit / commits) through `github_output`, so a
value that grows a second line rides a heredoc delimiter instead of killing
the step (DRE-4202); exit 0 on every decision (including the fail-closed
ones). Anything genuinely unexpected raises and fails the job loudly — the
medic sees it (never fail open).
"""

from __future__ import annotations

import argparse
import json
import re
import sys

import github_output
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


# --------------------------------------------------------------------------- #
# The clock, and whether it has killed this step before (DRE-4674)             #
# --------------------------------------------------------------------------- #

#: What GitHub's own runner writes when a step or a job runs past its
#: `timeout-minutes`: `##[error]The action 'Test' has timed out after 12
#: minutes`. Those are the vendor's words, and that line is the ONLY place the
#: limit appears at all — the jobs API reports the step as `failure`, never as
#: "the clock ran out". The capture is what the fix agent and the human are
#: told the limit is.
TIMEOUT_MARKER = re.compile(r"has timed out after(?:\s+([^\r\n.]+))?", re.I)

#: A step whose own conclusion is one of these MAY be the one the clock took.
#: `skipped` is not here on purpose: the steps after a timeout are skipped, and
#: counting them would make every later step in the file a "timed-out step" and
#: match a repeat on any of them.
_TIMED_OUT_STEP_CONCLUSIONS = ("failure", "cancelled", "canceled", "timed_out")

#: The job conclusion GitHub uses when it stopped the job on the clock itself.
#: It states the timeout without a log line, which is the half of the rule a
#: log fetch failure must not take away.
_TIMED_OUT_JOB_CONCLUSION = "timed_out"


def timeout_limit(log_text: str) -> str:
    """The limit the runner printed ("12 minutes"), or "" if it printed none."""
    found = TIMEOUT_MARKER.search(log_text or "")
    return (found.group(1) or "").strip() if found else ""


def is_step_timeout(log_text: str) -> bool:
    """Did the clock end this run? The "a timeout can be infrastructure ONCE"
    half of DRE-4674 — a lone one still backs off, and it is this predicate
    that makes it back off rather than dispatch."""
    return bool(TIMEOUT_MARKER.search(log_text or ""))


def _job_log(log_text: str, job_name: str) -> str:
    """The lines of a `gh run view --log-failed` dump that belong to one job.

    That dump prefixes every line with the job's own name and a tab, so one
    job's lines are selectable — and must be selected: a run's failed jobs all
    land in one file, and reading one job's clock onto another job's step is
    how a repeat gets invented out of two unrelated failures.

    When no line names the job and the dump IS job-prefixed, that silence is
    itself the answer — this job's lines are not in the file — and the job
    gets an empty log rather than every other job's. Only a log with no
    prefixing at all (an empty fetch, or a log gathered in some other shape)
    is read whole, because there is nothing there to attribute it by.
    """
    text = log_text or ""
    lines = text.splitlines()
    if job_name:
        mine = [ln for ln in lines if job_name in ln]
        if mine:
            return "\n".join(mine)
        if any("\t" in ln for ln in lines):
            return ""
    return text


def timed_out_steps(entry) -> dict:
    """`{(workflow path, job name, step name): the limit as printed}` for one
    run of the history document.

    A step counts as timed out when its own conclusion is a failure or a
    cancellation AND either its job's log carries the runner's timeout line or
    GitHub concluded the whole job `timed_out`.
    """
    if not isinstance(entry, dict):
        return {}
    path = entry.get("workflow_path") or ""
    log = entry.get("log") or ""
    found = {}
    for job in entry.get("jobs") or ():
        if not isinstance(job, dict):
            continue
        name = job.get("name") or ""
        job_log = _job_log(log, name)
        on_the_clock = (
            (job.get("conclusion") or "").lower() == _TIMED_OUT_JOB_CONCLUSION
            or is_step_timeout(job_log)
        )
        if not on_the_clock:
            continue
        limit = timeout_limit(job_log)
        for step in job.get("steps") or ():
            if not isinstance(step, dict):
                continue
            if (step.get("conclusion") or "").lower() in _TIMED_OUT_STEP_CONCLUSIONS:
                found[(path, name, step.get("name") or "")] = limit
    return found


def repeated_timeout(history) -> dict | None:
    """The step the clock has now killed twice, or None.

    The rule, whole: the CURRENT run timed out on a step, and that same step —
    same workflow file path, same job name, same step name, compared exactly —
    timed out on one of the runs the history carries (this run's previous
    attempt, or one of the 3 prior runs on the default branch).

    None is the answer for everything else, including every shape of unusable
    history: a failed fetch is "nothing is known", never "repeated".
    """
    if not isinstance(history, dict):
        return None
    current = history.get("current")
    if not isinstance(current, dict):
        return None
    here = timed_out_steps(current)
    if not here:
        return None
    prior = history.get("prior")
    prior = [e for e in prior if isinstance(e, dict)] if isinstance(prior, list) else []

    for triple, limit in here.items():
        commits = [current.get("head_sha") or ""]
        matched = False
        for entry in prior:
            earlier = timed_out_steps(entry)
            if triple not in earlier:
                continue
            matched = True
            limit = limit or earlier[triple]
            commits.append(entry.get("head_sha") or "")
        if not matched:
            continue
        _, job, step = triple
        return {
            "timeout_job": job,
            "timeout_step": step,
            "timeout_limit": limit,
            # Deduplicated, order kept: a repeat found on this run's PREVIOUS
            # ATTEMPT is the same commit twice, and naming it twice reads as
            # two commits to whoever picks the card up.
            "timeout_commits": ", ".join(
                dict.fromkeys(sha for sha in commits if sha)
            ),
        }
    return None


def load_history(path: str):
    """The gathered history document, or None for "no history".

    None is the fail-safe answer and the whole of the rule's third clause: a
    missing file, `repair_history.py`'s fetch-failed marker, malformed JSON —
    anything the gather step could not produce reads as "nothing is known
    about earlier runs", which lands the decision exactly where it lands
    today. It is never read as "repeated".
    """
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


#: How much of the failing sha a card-named branch carries. Twelve characters
#: is git's own unambiguous-abbreviation territory and keeps the ref readable
#: beside the card id; the full sha stays in the card body and the PR body, and
#: `qa-review.yml` resolves the short one back through the commits API before
#: asking for that commit's runs (the runs API answers 0 for an abbreviation).
SHA_CHARS = 12


def repair_branch(sha: str, attempt: int, card: str | None = None) -> str:
    """The attempt's branch name.

    With a card (the normal path since DRE-3533): `repair/DRE-<n>-<sha12>`,
    suffixed `-N` from attempt 2. The card id in the head ref is what closes
    the card on merge — `linear-sync` reads it there, exactly as it does out of
    an `agent/` ref — and what shows the pull request's card on the console.

    Without one: `repair/<sha>`, the pre-DRE-3533 shape. That is the FALLBACK
    for a repair whose card could not be filed (Linear down or rate-limited)
    and the shape every repair branch already in the fleet has, so every reader
    of these refs still has to accept it.
    """
    stem = f"{card}-{sha[:SHA_CHARS]}" if card else sha
    return f"repair/{stem}" if attempt == 1 else f"repair/{stem}-{attempt}"


def _sha_record_re(sha: str) -> re.Pattern:
    """Refs that ARE an attempt at this commit — both branch shapes.

    The attempt record, the debounce and the 2-attempt budget all count these,
    so a card-named branch and a bare-sha branch at the same commit must count
    as the same repair. They can legitimately mix: attempt 1 files its card,
    attempt 2 runs while Linear is down (or the reverse).
    """
    stem = (
        rf"(?:DRE-[0-9]+-{re.escape(sha[:SHA_CHARS])}|{re.escape(sha)})"
    )
    return re.compile(rf"^repair/{stem}(?:-[0-9]+)?$")


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
    history=None,
) -> dict:
    """The whole trigger decision. `refs` is an iterable of existing branch
    names (plain, e.g. "repair/<sha>"); `pulls` an iterable of dicts with
    head_ref / state ("open"|"closed") / merged (bool) covering repair PRs
    of ANY state; `history` the document `repair_history.py` gathers, or None
    for "nothing is known about earlier runs". Returns go / branch / attempt /
    escalate / reason plus the timeout_* detail."""

    # Every decision answers these, so the workflow can read them without
    # asking which branch it came down: a key the decision omitted would
    # interpolate an empty expression into a card or a prompt.
    detail = {"timeout_job": "", "timeout_step": "", "timeout_limit": "",
              "timeout_commits": ""}

    def noop(reason: str, escalate: bool = False) -> dict:
        return {"go": False, "branch": "", "attempt": 0,
                "escalate": escalate, "reason": reason, **detail}

    if conclusion != "failure":
        return noop("not-a-failure")
    if not head_branch or head_branch != default_branch:
        # Branch CI failures already route through agent-fix and the medic.
        return noop("not-default-branch")
    if not _SHA_RE.match(head_sha or ""):
        return noop("bad-head-sha")

    # DRE-4674. The clock gets exactly one free pass. A repeat outranks the
    # backoff — including an infra fingerprint in the same log — because a
    # step that has now blown the same limit twice is the one failure class
    # where waiting is provably not the fix, and the run that waits is the run
    # nobody watches. Everything below (debounce, budget, the in-flight lock)
    # then applies to it exactly as it does to any other dispatch.
    repeat = repeated_timeout(history)
    if repeat:
        detail = {**detail, **repeat}
    elif is_infra_failure(log_text, workflow_name) or is_step_timeout(log_text):
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
        return _dispatch(branch, 2, repeat, detail)
    return _dispatch(repair_branch(head_sha, 1), 1, repeat, detail)


def _dispatch(branch: str, attempt: int, repeat, detail: dict) -> dict:
    """A go, named for WHY it is a go: `repair` when the clock has killed this
    step before (DRE-4674), `dispatch` for every ordinary red main."""
    return {"go": True, "branch": branch, "attempt": attempt,
            "escalate": False, "reason": "repair" if repeat else "dispatch",
            **detail}


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


def outputs(decision: dict) -> str:
    """The block the workflow appends to `$GITHUB_OUTPUT` for `decision`.

    Every key goes through the safe writer, not only `reason`: `branch` and
    `attempt` were single-line on the run that died by accident rather than by
    construction, and the next key added here inherits the safety instead of
    owing a review of whether its value can wrap (DRE-4202).
    """
    return github_output.render([
        ("go", "true" if decision["go"] else "false"),
        ("branch", decision["branch"]),
        ("attempt", decision["attempt"]),
        ("escalate", "true" if decision["escalate"] else "false"),
        ("reason", decision["reason"]),
        # DRE-4674: what the repeat was, so the agent starts at the named step
        # with the named clock and the budget-exhausted card hands a human the
        # same four facts. Empty on every decision that is not a repeat.
        ("timeout_job", decision["timeout_job"]),
        ("timeout_step", decision["timeout_step"]),
        ("timeout_limit", decision["timeout_limit"]),
        ("timeout_commits", decision["timeout_commits"]),
    ])


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
    # Optional: the runs this one may be compared against (DRE-4674). Absent,
    # unreadable or marker-filled ⇒ "no history" ⇒ today's behaviour.
    d.add_argument("--history-file", default="")
    args = parser.parse_args(argv)

    # Stdout is the output FILE for this step, so the decision is made with
    # that channel shut: anything this module or anything it imports prints
    # would otherwise be a line the runner has to read as an output (DRE-4202).
    with github_output.only_outputs():
        records = _load_records(args.refs_file, args.pulls_file)
        if records is None:
            decision = {"go": False, "branch": "", "attempt": 0,
                        "escalate": False, "reason": "records-unreadable",
                        "timeout_job": "", "timeout_step": "",
                        "timeout_limit": "", "timeout_commits": ""}
            print("repair decide: attempt records unreadable — fail-closed, no "
                  "dispatch (the next failure event retries with fresh records)",
                  file=sys.stderr)
        else:
            refs, pulls = records
            history = load_history(args.history_file)
            if args.history_file and history is None:
                print("repair decide: no usable repair history — the clock "
                      "rule falls back to today's behaviour (DRE-4674)",
                      file=sys.stderr)
            decision = decide(
                conclusion=args.conclusion,
                head_branch=args.head_branch,
                default_branch=args.default_branch,
                head_sha=args.head_sha,
                log_text=_read_text(args.log_file),
                refs=refs,
                pulls=pulls,
                workflow_name=args.workflow_name,
                history=history,
            )
            print(f"repair decide: {decision['reason']}"
                  + (f" → {decision['branch']}" if decision["go"] else ""),
                  file=sys.stderr)
            if decision["timeout_step"]:
                print(f"repair decide: the step "
                      f"{decision['timeout_step']!r} of job "
                      f"{decision['timeout_job']!r} has now run past its "
                      f"limit ({decision['timeout_limit'] or 'unstated'}) on "
                      f"{decision['timeout_commits']}", file=sys.stderr)

    sys.stdout.write(outputs(decision))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
