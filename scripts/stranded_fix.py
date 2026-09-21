#!/usr/bin/env python3
"""A fix that ends up only on a merged branch (DRE-4486, stdlib only).

A fix run can still be working a pull request when the merge gate merges it.
When the fix finishes it pushes to the PR's branch — which has already
merged. The fix is on `origin`, reviewed and measured, and on no path to
`main`. The card reads Done. Nothing says otherwise.

## It has happened at least four times

| Where | PR merged | Fix pushed | Outcome |
| -- | -- | -- | -- |
| portico #611 (DRE-4183) | 08:34:12Z, by `agent-bureau-qa-bot`, 22s after its own APPROVE | `9f1b7542` at 08:43:39Z — **+9 min** | a live bug: every photo portal's sign-in page draws two stacked veils on a phone. Now DRE-4460 |
| portico #351 (DRE-2637) | 18:55:47Z | `72964abd` at 19:00:04Z — **+4 min** | harmless only by luck — later work redid the same fix |
| DRE-2227 → recovered as DRE-2989 | — | post-merge fix | *"stranded on its branch, and the card reads Done"* |
| recovered as DRE-2591 | — | — | *"stranded a better version on a dead branch"* |

Every one of those was handled as a RECOVERY. None filed the PREVENTION,
which is why it recurred — and why this module exists rather than a fifth
recovery card.

## The three halves, and why all three (CEO's signed answer, 2026-09-21)

The card offered `either` the gate refuses `or` the fix re-routes. The
answer was both, plus the detector:

  1. **The gate does not merge a pull request while a fix run is queued or
     running for it.** `read_lane` / `lane_refusal` here;
     `merge_gate.evaluate_fix_lane` is condition F. This is the half that
     closes the race. It is not airtight on its own — the gate reads the
     lane and merges moments later, so a run queued inside that window is
     still unseen — which is exactly why the other two exist.
  2. **A fix run that finds its pull request already merged does not push
     there.** `push_decision`, driven from a git `pre-push` hook
     agent-fix.yml installs into the PR checkout before the agent starts.
     `git push` is the act to refuse, and a pre-push hook is git's own seam
     for refusing it — a prompt instruction is not a mechanism.
  3. **A merged pull request whose head branch carries commits dated after
     the merge is reported once, naming them.** `detect`, swept by
     `reconcile.flag_stranded_fixes`.

## The tell the detector reads, and why it is not `delete_branch_on_merge`

The stale-branch audit of 2026-09-21 found these by noticing merged branches
that should not exist: portico has `delete_branch_on_merge` on, GitHub
deletes the branch at merge, and the late push RECREATES it. That tell is
real and it is repo-configuration-dependent — a repo with auto-delete off
leaves every merged branch standing.

So the detector reads the stronger fact underneath it: **a commit on the
branch, absent from the base, COMMITTED AFTER the merge**. Nothing but a
push after the merge can produce one. An ordinary un-deleted branch of a
squash merge is ahead of its base forever and every one of its commits
predates the merge, so it never alarms — the configuration decides how
visible the symptom is, never whether the finding is true.

## The fail directions, which are not the same in all three

  * The gate fails CLOSED. An unreadable lane record, or an in-flight run
    GitHub will not attribute, is `wait` — a wait costs one gate wake, and
    the direction that reads a blip as "no fix run" is the bug itself.
  * The hook fails OPEN. It runs on every push of every fix run, and a hook
    that refused on an API blip would break the loop it protects. It
    announces the unproven read rather than swallowing it.
  * The detector says NOTHING it cannot prove. No merge time, no commit
    date, no readable compare — no finding. It only ever ADDS an alarm, so
    a fabricated one is the only way it can do harm.

No I/O in the decisions: every function here takes a payload a caller has
already read, in the shape `gh` emits it. The `lane` subcommand is the one
exception and it is a GATHERER, not a decision — it shells out to `gh` so
merge-gate.yml stays inside GitHub's expression budget, and it never fails
the caller: an unreadable read is written into the record as such.

CLI:

    python3 stranded_fix.py lane --repo R --workflow agent-fix.yml --out F
    python3 stranded_fix.py guard --repo R --pr N        # the pre-push hook
    python3 stranded_fix.py route --repo R --pr N --compare-file F \\
        [--card DRE-N] [--pushed] --out-title T --out-body B
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ONE reader of "which pull request is this Agent Fix run working" (DRE-2908).
# The job name is the only place the number survives into the Actions API, and
# the expression that builds it lives in agent-fix.yml; re-deriving the parse
# here is how two readers come to disagree about the same run.
import fix_concurrency  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_MAP_PATH = os.path.join(ROOT, "config", "repo-map.json")

#: GitHub's `status` values for a workflow run that has not finished. Listed
#: rather than inverted off `completed`: an unknown future status must read as
#: NOT-in-flight nowhere, so the set that blocks a merge is the explicit one
#: and anything else is compared against `completed` by the caller.
IN_FLIGHT_STATUSES = frozenset({
    "queued", "in_progress", "requested", "waiting", "pending",
})

# push_decision's vocabulary.
PUSH = "push"
REFUSE = "refuse"
UNPROVEN = "unproven"


# --------------------------------------------------------------------------- #
# 1. the gate's record: what the Agent Fix lane is working right now           #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Lane:
    """The fix lane as the merge gate sees it.

    `readable` False is the fail-closed state and carries `detail` so the run
    log says WHY the gate waited. `by_pr` is exact and suppresses a merge;
    `unattributed` is a list of run ids GitHub would not attribute to any pull
    request and it only ever bounds — a run pending on its concurrency group
    lists zero jobs, and a repo riding a release tag older than DRE-2908 names
    its job without the number, so "unattributed" means "could be any pull
    request", never "not this one".
    """

    readable: bool = True
    by_pr: dict = field(default_factory=dict)
    unattributed: tuple = ()
    detail: str = ""


def read_lane(payload) -> Lane:
    """Parse the record `stranded_fix.py lane` writes.

    Anything that is not provably a lane record reads as UNREADABLE, which
    the gate treats as busy. The shape is deliberately ours rather than
    GitHub's: attributing a run needs its JOB names, which is a second API
    call per in-flight run, and the gathering belongs in one place.
    """
    if not isinstance(payload, dict):
        return Lane(readable=False, detail="the lane record is not an object")
    if not payload.get("readable"):
        return Lane(readable=False,
                    detail=str(payload.get("detail")
                               or "the lane record says it could not be read"))
    runs = payload.get("runs")
    if not isinstance(runs, list):
        return Lane(readable=False, detail="the lane record carries no run list")
    by_pr: dict = {}
    unattributed: list = []
    for item in runs:
        if not isinstance(item, dict):
            return Lane(readable=False,
                        detail="the lane record carries a shapeless run")
        if str(item.get("status") or "") not in IN_FLIGHT_STATUSES:
            continue
        jobs = item.get("jobs")
        number = fix_concurrency.pr_of_job_names(
            jobs if isinstance(jobs, list) else [])
        if number is None:
            unattributed.append(item.get("id"))
        else:
            by_pr.setdefault(number, item.get("id"))
    return Lane(by_pr=by_pr, unattributed=tuple(unattributed))


def lane_refusal(lane: Lane, pr_number) -> str | None:
    """Why this pull request must not be merged right now, or None.

    Named in words a run log can be read from: the old workflow-wide "fix
    agent busy" told a reader nothing about WHICH pull request was being
    worked, which is the DRE-2908 lesson this inherits.
    """
    if lane is None:
        return None  # the pre-DRE-4486 caller — this condition gates nothing
    if not lane.readable:
        return (
            f"the Agent Fix run listing could not be read ({lane.detail}) — "
            "waiting rather than merging a pull request a fix run may still "
            "be working; a fix that lands after the merge is stranded on a "
            "dead branch (DRE-4486)"
        )
    try:
        number = int(pr_number)
    except (TypeError, ValueError):
        number = None
    if number is None:
        # Nothing can be attributed without the number, so every in-flight
        # run is "could be this one". The workflow always passes it; this is
        # the shape drift arm, and it fails closed like every other one here.
        live = list(lane.by_pr.values()) + list(lane.unattributed)
        if live:
            return (
                f"{len(live)} Agent Fix run(s) are in flight and this gate "
                "was given no pull request number to attribute them to — "
                "nothing here can prove none of them is this pull request, "
                "so it waits rather than risk stranding a fix (DRE-4486)"
            )
        return None
    if number in lane.by_pr:
        return (
            f"Agent Fix run {lane.by_pr[number]} is queued or running FOR "
            f"#{number} — merging now would strand whatever it pushes on the "
            "merged branch (DRE-4486); waiting for the fix to land"
        )
    if lane.unattributed:
        ids = ", ".join(str(i) for i in lane.unattributed if i is not None)
        return (
            f"{len(lane.unattributed)} Agent Fix run(s) are in flight that "
            f"GitHub attributes to no pull request ({ids or 'no run ids'}) — "
            "unattributed is \"could be any pull request\", never \"not this "
            "one\", so the gate waits rather than risk stranding a fix "
            "(DRE-4486)"
        )
    return None


# --------------------------------------------------------------------------- #
# 2. the fix run: may this push go to this branch?                             #
# --------------------------------------------------------------------------- #


def push_decision(pr_record) -> tuple:
    """`(action, reason)` for a push about to land on the PR's head branch.

    `pr_record` is `gh pr view --json state,mergedAt` (or None when that read
    failed). MERGED refuses; everything else pushes. A CLOSED-but-unmerged
    pull request pushes on purpose — it can be reopened, and the CEO's answer
    names the merged case; widening it would refuse pushes nothing is wrong
    with.

    An unreadable record is UNPROVEN, which the hook allows. That is the one
    fail-open direction in this module and it is deliberate: this runs on
    every push of every fix run, and a hook that refused on an API blip would
    break the loop it exists to protect. The gate is the rule, this is the
    belt, `detect` is the net.
    """
    if not isinstance(pr_record, dict):
        return UNPROVEN, (
            "could not read this pull request's state, so nothing here can "
            "prove it has merged — allowing the push and saying so rather "
            "than breaking the fix loop on a blip (DRE-4486)"
        )
    state = str(pr_record.get("state") or "").upper()
    if state == "MERGED":
        merged_at = pr_record.get("mergedAt") or "an unrecorded time"
        return REFUSE, (
            f"this pull request merged at {merged_at}. Pushing to its head "
            "branch now puts the fix on origin and on no path to the default "
            "branch — that has happened four times (portico #611/DRE-4183, "
            "portico #351/DRE-2637, DRE-2227, DRE-2591) and every one of them "
            "was found by hand. The push is refused; the run files a card "
            "naming the work instead (DRE-4486)"
        )
    return PUSH, f"the pull request is {state or 'not merged'} — pushing"


def hook_status(action: str) -> int:
    """The exit code a `pre-push` hook returns for a decision. Only REFUSE
    stops git; UNPROVEN is an allowed push with a loud line in the log."""
    return 1 if action == REFUSE else 0


# --------------------------------------------------------------------------- #
# 3. the detector: a merged branch carrying commits dated after the merge      #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Stranded:
    """A finding: `commits` are on `branch`, absent from `base`, and dated
    after `merged_at`. Ordered as GitHub's compare returns them (oldest
    first) so the card reads chronologically."""

    pr: int
    branch: str
    base: str
    merged_at: str
    commits: tuple
    url: str = ""


def _instant(value):
    """A GitHub timestamp as an aware datetime, or None when it is not one.

    None is the whole fail-safe: a commit whose date cannot be read is never
    an orphan, because a branch alarmed on a missing field would alarm on
    every branch with one.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _committed(entry):
    """The committer date of one compare `commits[]` entry, or None.

    COMMITTER, never author: a cherry-pick or a rebase carries the original
    author date forward, and the question here is when the commit came to
    exist on this branch.
    """
    if not isinstance(entry, dict):
        return None
    commit = entry.get("commit")
    if not isinstance(commit, dict):
        return None
    committer = commit.get("committer")
    if not isinstance(committer, dict):
        return None
    return _instant(committer.get("date"))


def orphaned(commits, merged_at) -> list:
    """The commits committed STRICTLY AFTER `merged_at`.

    Strictly: a commit at the merge instant is in the merge, not after it.
    An unreadable merge time or an unreadable commit date yields nothing —
    this function never guesses a commit into a finding.
    """
    merged = _instant(merged_at)
    if merged is None or not isinstance(commits, list):
        return []
    found = []
    for entry in commits:
        when = _committed(entry)
        if when is not None and when > merged:
            found.append(entry)
    return found


def detect(pr, compare) -> Stranded | None:
    """Is this merged pull request's head branch carrying a stranded fix?

    `pr` is `gh pr view --json number,headRefName,baseRefName,mergedAt,url`
    and `compare` is `GET compare/{base}...{branch}` — whole, because its
    `commits[]` is what the finding names.

    None means "nothing provable here", for every reason: not merged, no
    readable compare, the branch level with its base, or every commit on it
    dated at or before the merge (the ordinary un-deleted branch of a squash
    merge, which is ahead of its base forever and is not this class).
    """
    if not isinstance(pr, dict) or not isinstance(compare, dict):
        return None
    merged_at = pr.get("mergedAt")
    if not merged_at:
        return None
    ahead = compare.get("ahead_by")
    if isinstance(ahead, bool) or not isinstance(ahead, int) or ahead < 1:
        return None
    commits = orphaned(compare.get("commits"), merged_at)
    if not commits:
        return None
    try:
        number = int(pr.get("number"))
    except (TypeError, ValueError):
        return None
    branch, base = pr.get("headRefName") or "", pr.get("baseRefName") or ""
    if not branch or not base:
        return None
    return Stranded(number, branch, base, str(merged_at), tuple(commits),
                    str(pr.get("url") or ""))


# --------------------------------------------------------------------------- #
# the card the finding is routed onto                                          #
# --------------------------------------------------------------------------- #


def slug_for_repo(repo) -> str | None:
    """The `repo:` slug for a `owner/name`, off `config/repo-map.json` — the
    same snapshot the relay routes on and validate_card derives VALID_SLUGS
    from. None for a repo the map does not carry, and the caller then files
    nothing rather than a card no lane can route."""
    try:
        with open(REPO_MAP_PATH, encoding="utf-8") as fh:
            table = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(table, dict):
        return None
    for slug, full in table.items():
        if full == repo:
            return slug
    return None


def card_title(slug: str, branch: str) -> str:
    """The card's title, and its idempotency key.

    Keyed on the BRANCH, so one branch is reported once and a second branch
    that strands the same way still speaks — the `unlanded-work-watchdog`
    reasoning, for the same reason. Prefixed with the repo slug because
    standards/card-quality.md makes the `<slug>: …` prefix and the `repo:`
    label one fact written twice, and both planner seams refuse a
    disagreement between them.
    """
    return f"{slug}: a fix commit is stranded on the merged branch {branch}"


def _subject(entry) -> str:
    commit = entry.get("commit") if isinstance(entry, dict) else None
    message = (commit or {}).get("message") or ""
    return message.splitlines()[0] if message else "(no commit message)"


def card_body(*, repo: str, stranded: Stranded, card: str = "",
              pushed: bool = True) -> str:
    """The one-off card's description, in the shape validate_card.py gates.

    Carries no verdict-shaped text (standards/untrusted-content.md — those
    strings are an approval credential and only the critic writes one) and no
    blocker-shaped opening line.
    """
    commits = "\n".join(
        f"- `{(c.get('sha') or '')[:8]}` — {_subject(c)}"
        f" ({((c.get('commit') or {}).get('committer') or {}).get('date') or 'undated'})"
        for c in stranded.commits
    )
    where = (
        f"They are on `origin/{stranded.branch}` and on no path to "
        f"`{stranded.base}`."
        if pushed else
        "The push was refused before it reached `origin`, so this work exists "
        "nowhere but the run that produced it — it has to be rebuilt, not "
        "recovered."
    )
    link = f" ({stranded.url})" if stranded.url else ""
    origin = f" It was the fix run for {card}." if card else ""
    return f"""A fix landed on `{stranded.branch}` after pull request #{stranded.pr}{link} had already merged at {stranded.merged_at}, so `{stranded.base}` never received it.{origin}

{where} Nothing downstream can see them: the card that owned the pull request reads Done, CI has run, the reviewer has approved, and the work is not in the product.

## The commits

{commits}

## Why this card exists rather than a hand recovery

This is the fifth time in the class (portico #611 / DRE-4183, which became the live bug DRE-4460; portico #351 / DRE-2637; DRE-2227, recovered as DRE-2989; DRE-2591). The first four were each found by a person looking at stale branches — this one was found by the pipeline, in minutes, which is the whole point of DRE-4486.

## Acceptance criteria

- [ ] the commits above are re-applied onto `{stranded.base}` through an ordinary pull request, or this card records why they should not be
- [ ] the branch `{stranded.branch}` is deleted once its content is on `{stranded.base}` or has been judged unwanted
"""


# --------------------------------------------------------------------------- #
# the gatherer — the one seam here that touches the network                    #
# --------------------------------------------------------------------------- #


def _gh(args) -> tuple:
    """`(stdout, None)` or `(None, detail)`. Never raises: every caller here
    records an unreadable read into its output rather than failing a run."""
    try:
        done = subprocess.run(["gh", *args], capture_output=True, text=True,
                              timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"{type(exc).__name__}: {exc}"
    if done.returncode != 0:
        return None, (done.stderr or done.stdout or "").strip()[:200]
    return done.stdout, None


#: The self-host repo names its dispatchable fix stub differently, because
#: there `agent-fix.yml` IS the workflow_call-only reusable. The same
#: resolution `reconcile.fix_workflow()` and merge-gate.yml's conflict route
#: make — kept here so the gatherer's caller does not have to spell it, which
#: is one fewer thing for a consumer stub to get wrong.
SELF_HOST_REPO = "dreadnought-foundry/bureau-pipeline"


def fix_workflow(repo: str) -> str:
    return "self-agent-fix.yml" if repo == SELF_HOST_REPO else "agent-fix.yml"


def gather_lane(repo: str, workflow: str) -> dict:
    """The lane record merge-gate.yml hands `--fix-lane-file`.

    One listing, plus one job-name read per IN-FLIGHT run — so an idle lane,
    which is the ordinary case, costs exactly one call. `readable: false` is
    a first-class answer: the gate fails closed on it, which is cheaper by
    far than the alternative this card exists to end.
    """
    out, detail = _gh([
        "run", "list", "--repo", repo, "--workflow", workflow,
        "--limit", "20", "--json", "status,databaseId",
    ])
    if out is None:
        return {"readable": False,
                "detail": f"listing {workflow} runs failed: {detail}"}
    try:
        listed = json.loads(out or "[]")
    except ValueError:
        return {"readable": False,
                "detail": f"unparseable run listing for {workflow}"}
    if not isinstance(listed, list):
        return {"readable": False,
                "detail": f"unparseable run listing for {workflow}"}
    runs = []
    for item in listed:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "")
        if status not in IN_FLIGHT_STATUSES:
            continue
        run_id = item.get("databaseId")
        jobs_out, jobs_detail = _gh([
            "api", f"repos/{repo}/actions/runs/{run_id}/jobs",
            "--jq", "[.jobs[].name]",
        ])
        if jobs_out is None:
            # An in-flight run we cannot name is not "not this pull request".
            # It goes in UNATTRIBUTED, which waits — the same fail-closed
            # direction as an unreadable listing, scoped to one run.
            print(f"stranded_fix: run {run_id} job names unreadable: "
                  f"{jobs_detail}", file=sys.stderr)
            runs.append({"id": run_id, "status": status, "jobs": []})
            continue
        try:
            names = json.loads(jobs_out or "[]")
        except ValueError:
            names = []
        runs.append({"id": run_id, "status": status,
                     "jobs": names if isinstance(names, list) else []})
    return {"readable": True, "runs": runs}


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #


#: `git log` fields in the order `local_compare` reads them back, separated by
#: a unit separator so a commit subject containing anything at all survives.
_LOG_FORMAT = "%H\x1f%cI\x1f%s"


def local_compare(base_ref: str, cwd: str | None = None) -> dict:
    """A compare-shaped record of `base_ref..HEAD` read from the LOCAL clone.

    The remote compare cannot answer this on the fix run's own path: when the
    pre-push guard refuses, the commits never reach `origin` and GitHub has
    nothing to compare — but the work exists in the workspace and the card
    has to name it, or the run dies holding the only record of it.

    Same keys as GitHub's compare payload, so `detect` has ONE reader.
    Returns an empty-but-readable record when git cannot answer, which
    `detect` reads as "nothing provable".
    """
    try:
        done = subprocess.run(
            ["git", "log", f"--format={_LOG_FORMAT}", f"{base_ref}..HEAD"],
            capture_output=True, text=True, timeout=60, cwd=cwd,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"stranded_fix: git log failed: {exc}", file=sys.stderr)
        return {"ahead_by": 0, "commits": []}
    if done.returncode != 0:
        print(f"stranded_fix: git log failed: {done.stderr.strip()[:200]}",
              file=sys.stderr)
        return {"ahead_by": 0, "commits": []}
    commits = []
    for line in done.stdout.splitlines():
        parts = line.split("\x1f", 2)
        if len(parts) != 3:
            continue
        sha, date, subject = parts
        commits.append({"sha": sha,
                        "commit": {"message": subject,
                                   "committer": {"date": date}}})
    # `git log` is newest-first; GitHub's compare is oldest-first, and the
    # card reads chronologically.
    commits.reverse()
    return {"ahead_by": len(commits), "commits": commits}


def _cmd_local_compare(args) -> int:
    record = local_compare(args.base)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(record, fh)
    print(f"stranded_fix: {record['ahead_by']} commit(s) beyond {args.base}")
    return 0


def _cmd_slug(args) -> int:
    slug = slug_for_repo(args.repo)
    if slug is None:
        print(f"stranded_fix: {args.repo} is not in config/repo-map.json",
              file=sys.stderr)
        return 1
    print(slug)
    return 0


def _cmd_lane(args) -> int:
    record = gather_lane(args.repo, args.workflow or fix_workflow(args.repo))
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(record, fh)
    if record.get("readable"):
        print(f"stranded_fix: {len(record['runs'])} Agent Fix run(s) in flight")
    else:
        print(f"stranded_fix: lane unreadable — {record.get('detail')}")
    return 0


def _cmd_guard(args) -> int:
    out, detail = _gh(["pr", "view", str(args.pr), "--repo", args.repo,
                       "--json", "state,mergedAt"])
    record = None
    if out is not None:
        try:
            record = json.loads(out)
        except ValueError:
            record = None
    else:
        print(f"stranded_fix: {detail}", file=sys.stderr)
    action, reason = push_decision(record)
    stream = sys.stderr if action != PUSH else sys.stdout
    print(f"stranded_fix: {action} — {reason}", file=stream)
    return hook_status(action)


def _cmd_route(args) -> int:
    """Compose the title and body of the card a stranded fix is routed onto.

    Writes nothing to Linear — the caller does that through `linear_ops.py`,
    which is the one seam that talks to the board. Exit 1 means "no finding",
    so a caller can gate its card creation on the exit code alone.
    """
    try:
        with open(args.compare_file, encoding="utf-8") as fh:
            compare = json.load(fh)
    except (OSError, ValueError) as exc:
        print(f"stranded_fix: cannot read the compare record: {exc}",
              file=sys.stderr)
        return 1
    out, detail = _gh(["pr", "view", str(args.pr), "--repo", args.repo,
                       "--json", "number,headRefName,baseRefName,mergedAt,url"])
    if out is None:
        print(f"stranded_fix: cannot read the pull request: {detail}",
              file=sys.stderr)
        return 1
    try:
        pr = json.loads(out)
    except ValueError:
        print("stranded_fix: the pull request record did not parse",
              file=sys.stderr)
        return 1
    found = detect(pr, compare)
    if found is None:
        print("stranded_fix: nothing stranded here")
        return 1
    slug = slug_for_repo(args.repo)
    if slug is None:
        print(f"stranded_fix: {args.repo} is not in config/repo-map.json — "
              "no card can be routed for it", file=sys.stderr)
        return 1
    with open(args.out_title, "w", encoding="utf-8") as fh:
        fh.write(card_title(slug, found.branch))
    with open(args.out_body, "w", encoding="utf-8") as fh:
        fh.write(card_body(repo=args.repo, stranded=found, card=args.card,
                           pushed=args.pushed == "yes"))
    print(f"stranded_fix: {len(found.commits)} commit(s) stranded on "
          f"{found.branch} after #{found.pr} merged")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    lane = sub.add_parser("lane", help="write the merge gate's fix-lane record")
    lane.add_argument("--repo", required=True)
    lane.add_argument("--workflow", default="",
                      help="the repo's agent-fix stub filename; derived from "
                           "--repo when omitted")
    lane.add_argument("--out", required=True)
    lane.set_defaults(fn=_cmd_lane)

    guard = sub.add_parser("guard", help="the pre-push hook's decision")
    guard.add_argument("--repo", required=True)
    guard.add_argument("--pr", required=True)
    guard.set_defaults(fn=_cmd_guard)

    route = sub.add_parser("route", help="compose the stranded-fix card")
    route.add_argument("--repo", required=True)
    route.add_argument("--pr", required=True)
    route.add_argument("--compare-file", required=True)
    route.add_argument("--card", default="")
    route.add_argument("--pushed", choices=("yes", "no"), default="yes",
                       help="did the commits reach origin? `yes` is the "
                            "detector's case (they are on a dead branch); "
                            "`no` is the guard's (the push was refused and "
                            "the work has to be rebuilt)")
    route.add_argument("--out-title", required=True)
    route.add_argument("--out-body", required=True)
    route.set_defaults(fn=_cmd_route)

    local = sub.add_parser("local-compare",
                           help="a compare record read from the local clone")
    local.add_argument("--base", required=True, help="e.g. origin/main")
    local.add_argument("--out", required=True)
    local.set_defaults(fn=_cmd_local_compare)

    slug = sub.add_parser("slug", help="the repo: slug for a owner/name")
    slug.add_argument("--repo", required=True)
    slug.set_defaults(fn=_cmd_slug)

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
