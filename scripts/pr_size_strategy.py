#!/usr/bin/env python3
"""Pick the critic's review strategy from the PR's diff size (DRE-2466).

THE INCIDENT. Portico PR #297 (118 files, +16,909/-628) was reviewed exactly
the way a two-file change is: one exhaustive `gh pr diff` pass, under the
prompt's standing order to "examine the ENTIRE diff and list every blocking
finding in THIS verdict". Four executions across two run attempts, all
`subtype: success`, `is_error: false`, none within 30 turns of its ceiling,
all finishing in 2-3.5 minutes. $12.40 and no verdict file, four times. The
PR then merged with the review check red and no review at all.

WHAT IS ESTABLISHED: #297 is 4-6x larger than any PR ever successfully
reviewed in that repo (largest passing: #275 at +4,092 across 29 files),
PR #296 passed on identical config 15 hours earlier, and four-for-four
determinism on the largest PR in the repo's history makes scale the trigger.
WHAT IS NOT: *why* scale breaks it. Each run ended voluntarily, too fast to
have read 17k lines and nothing like context exhaustion (which ends
`error_max_turns` here). Wrapped up early, wrote an unrecognised format, or
answered in chat — the record cannot say which, and DRE-2465's gate
diagnostics are what will decide it. So this module changes the STRATEGY and
assumes no mechanism.

THE SHAPE OF THE ANSWER — a strategy switch, not a cap. Failing fast at
#297's size only converts $12 of doomed spend into $0: the PR still ends
unreviewed and the merge still holds, which is the state that got overridden
anyway. A large PR is exactly the one most worth reviewing.

  * standard  — today's behavior, byte for byte. One exhaustive pass.
  * large     — review from the CHANGED-FILE LIST with targeted per-file
                reads, exhaustive over the files actually reviewed, with the
                turn ceiling raised to match the work.
  * oversized — decline honestly, name the size, ask for a split. No
                inference is spent.

THRESHOLDS come from the repo's own history, not round numbers:

  * 10 files / 1,500 changed lines. Above every review the critic has been
    observed to FINISH and below the smallest one it demonstrably could
    not — the measurement is beside the constant below. Lowered from
    50 / 5,000 by DRE-2924.
  * 200 files / 20,000 changed lines. Above #297 itself (118 / 17,537),
    deliberately: that PR must be reviewed, not refused. Also below
    GitHub's 300-file compare cap, so the refusal is never decided on
    truncated data.

GENERATED FILES ARE DISCOUNTED. A 25,000-line `package-lock.json` bump is
not a 25,000-line review — the critic reads the manifest and the lock is
noise. Counting it would push every dependency PR toward a path meant for
17k lines of hand-written code, and past the second threshold would refuse
them outright.

SO ARE WHOLE-FILE REMOVALS (DRE-3995). agent-bureau #2571 removes the
retired v1 platform — 3,080 files, 2,094,946 deleted lines, plus 87 added
lines (a guard test and four doc pointers) — and was refused four times as
"too large to review". The review question for a REMOVED file is not what
its deleted lines said; it is whether anything still live imports, executes
or links into that path, and that is answered from the LIST of removed
paths, not by reading each deleted line. So a file whose status is `removed`
counts toward `removed_files`/`removed_lines` and not toward the reviewable
size, the critic's block carries the removed paths plus the standing
live-reference instruction, and ADDITIONS are never discounted — a pull
request that removes 3,000 files and adds 25,000 lines is still oversized,
because a removed file has no additions to hide behind.

Statuses come from the paginated PR files API (`/pulls/{n}/files`, 3,000
records max), because the compare record truncates at 300 and #2571 changes
3,080 files. Past that ceiling the tail's statuses are unknowable: its
DELETIONS are split the way the deletions we could see were split, its COUNT
is split the way the seen files were split, and its additions are always
review work. Neither split may ever claim the whole tail while the tail still
carries lines a removal cannot own. Read `measure()` for the arithmetic.

MEASURED FROM RECORDS THE WORKFLOW ALREADY FETCHES: the compare record
Resolve PR writes to /tmp/qa-compare.json for the content id (DRE-2340),
plus `gh pr view --json changedFiles,additions,deletions` for authoritative
totals — the compare record's `files[]` caps at 300 entries and does not
paginate (see verdict_content.py), and under-counting is the dangerous
direction. Both signals are read; the LARGER wins.

NEVER EXITS NON-ZERO. A sizing failure must degrade to `standard` (today's
behavior), never wedge the gate — repair_context.py's rule. The workflow
carries a static fallback on top of that.

CLI:
    pr_size_strategy.py --compare-file /tmp/qa-compare.json \
                        --pr-json-file /tmp/qa-size.json \
                        --repo owner/name --pr 297
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

from sanitize_untrusted import _write_output

# ── thresholds (see the module docstring for where each number comes from) ──

# THE ONE-PASS CEILING, AND THE MEASUREMENT THAT SET IT (DRE-2924).
# portico, the night of 2026-08-31 — five reviews, one repo, one config:
#
#     #361    4 files /   507 lines   APPROVED
#     #362    5 files /   975 lines   APPROVED
#     #363    6 files / 1,026 lines   APPROVED
#     #366    4 files /  ~700 lines   reviewed
#     #364   18 files / 2,059 lines   NO VERDICT, TWICE
#
# Everything the critic finished was <= 6 files / ~1,030 lines. #364 — the one
# pull request at 3x the files — is the only one it could not finish: attempt
# one spent 62 of its 80 turns and ~$2.56 and left nothing where the verdict
# should have been. Splitting it into #367 (11 files) and #368 (7 files)
# resolved it within the hour.
#
# The defect that measurement exposes: the threshold is expressed in files and
# lines, and the thing that actually runs out is TURNS. The previous pair,
# 50 / 5,000, was read off an older and larger sample (#275 at +4,092 across
# 29 files; #290 at 41 files) and it would happily route something 2.5x bigger
# than #364 to the same one-pass review #364 died in. 10 files / 1,500 lines
# is picked from the band between the two observations — above every completed
# review with headroom, well below the one that failed — and NOT from the old
# constant. Raising max_turns instead only moves the wall, and the review
# quality at turn 119 is not the quality at turn 20; above this line the
# file-list strategy runs, with a turn budget sized for it.
LARGE_FILES = 10
LARGE_LINES = 1_500
OVERSIZED_FILES = 200
OVERSIZED_LINES = 20_000

#: (first attempt, retry) turn ceilings per strategy. The retry MUST stay
#: strictly higher — a retry that comes back with no more of the resource it
#: exhausted is a second invoice, not a recovery (DRE-2422). Pinned by
#: tests/test_critic_turn_budget.py, which now runs its whole assertion set
#: against every entry here.
#:
#: `standard` went 80/120 → 100/140 with DRE-2785, which gave the critic
#: `WebSearch`/`WebFetch`. 80 was measured on a critic that could only reason
#: about an external claim from training data; checking one now costs a search
#: and a fetch per claim, on top of reading the whole diff. A crashed critic
#: writes no verdict and the pull request simply sits, so the ceiling moves
#: WITH the capability rather than after the first death.
#:
#: `large` is deliberately unchanged: it already runs at 150/200 — above every
#: build agent in the fleet — and 200 is the sane-ceiling bound
#: tests/test_critic_turn_budget.py holds every strategy to.
TURN_BUDGET = {
    "standard": (100, 140),
    "large": (150, 200),
}

#: Files whose diff lines are not review work. Machine-generated content:
#: the critic judges the manifest change that produced it, not the output.
_GENERATED_RE = re.compile(
    r"(^|/)("
    r"package-lock\.json|npm-shrinkwrap\.json|yarn\.lock|pnpm-lock\.yaml|"
    r"poetry\.lock|Pipfile\.lock|Cargo\.lock|go\.sum|composer\.lock|"
    r"Gemfile\.lock"
    r")$"
    r"|\.min\.(js|css)$"
    r"|\.snap$"
    r"|(^|/)(dist|build|vendor|node_modules)/",
    re.IGNORECASE,
)


def is_generated(path: str) -> bool:
    return bool(_GENERATED_RE.search(path or ""))


#: GitHub's own ceilings on the PR files API: 100 records per page, and at
#: most 3,000 records however many pages are asked for ("Responses include a
#: maximum of 3000 files"). The page loop stops at both — a loop with no
#: ceiling would page forever on a bigger diff, and asking past 3,000 returns
#: nothing anyway.
FILES_API_PAGE = 100
FILES_API_MAX = 3_000

#: How many removed paths the critic's block lists one by one before it names
#: their top-level directories instead. #2571 removes 3,075 files; pasting
#: that list into a prompt is exactly the context dump this module exists to
#: prevent, and the live-reference check is per DIRECTORY anyway.
REMOVED_PATHS_LISTED = 40

_REPO_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")


def fetch_pr_files(repo: str, pr: str,
                   max_files: int = FILES_API_MAX) -> list[dict]:
    """Per-file records from `GET /repos/{repo}/pulls/{n}/files`, paginated.

    The per-file STATUS is the whole point: the compare record the workflow
    already fetches carries the same field but truncates at 300 entries, and
    the pull request this exists for changes 3,080 files.

    Never raises and never exits: a missing `gh`, a 404, a rate limit or a
    malformed page all mean "no statuses", which is the behavior that was
    there before this call existed. Both arguments are validated before they
    reach a URL — the PR number arrives from workflow context and is digits,
    and this script must not be the place that assumption is first tested.
    """
    number = _pr_ref(pr)
    if number == "<n>" or not _REPO_RE.match(str(repo or "")):
        return []
    out: list[dict] = []
    for page in range(1, max_files // FILES_API_PAGE + 1):
        batch = _gh_json(
            f"repos/{repo}/pulls/{number}/files"
            f"?per_page={FILES_API_PAGE}&page={page}"
        )
        if not isinstance(batch, list) or not batch:
            break
        out.extend(entry for entry in batch if isinstance(entry, dict))
        if len(batch) < FILES_API_PAGE or len(out) >= max_files:
            break
    return out[:max_files]


def _gh_json(path: str):
    """One `gh api` call, decoded. None on any failure at all."""
    try:
        proc = subprocess.run(
            ["gh", "api", path],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout or "")
    except ValueError:
        return None


def _int(value) -> int:
    """A count from an API record, or 0. Never raises — a malformed field
    must not be the thing that stops a review."""
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


def measure(compare: dict | None, pr_json: dict | None,
            files: list[dict] | None = None) -> dict:
    """Size the PR from every record; the larger signal wins.

    `compare` is GitHub's three-dot compare record (per-file, capped at 300
    files). `pr_json` is `gh pr view --json changedFiles,additions,deletions`
    (totals only, never truncated). `files` is the paginated files API
    (per-file, capped at 3,000) and SUPERSEDES the compare record's per-file
    entries when it is given — it is the same shape and sees ten times as
    far. The per-file record is what makes the generated-file and removal
    discounts possible; the totals are what makes the count honest above
    3,000 files.

    THE TAIL. Past whichever per-file ceiling applied, some files were never
    seen. Their count and their aggregate lines are known (the totals minus
    what was seen); what is unknown is each one's status. Their ADDITIONS are
    counted as review work outright — a removed file has none, so an addition
    can never be hiding behind a removal. Their DELETIONS are split in the
    same ratio as the deletions that WERE seen, and their COUNT in the same
    ratio as the files that were seen: where nothing seen was a removal (the
    ordinary truncated pull request) the whole tail counts, exactly as before
    this card; where the visible pull request is a wall of removals, so is
    the tail. Both splits stop one file short of the whole tail whenever the
    tail still carries lines a removal cannot own, because those lines are in
    a file somebody has to read — otherwise the record says `0 files / 87
    lines reviewable`, which is not a state that exists. Nothing here is
    attributed when there is no per-file record at all — that degrades to
    today's arithmetic.
    """
    entries = files if files is not None else (compare or {}).get("files") or []
    seen_files = seen_add = seen_del = 0
    rm_files = rm_lines = rm_del = 0
    gen_files = gen_lines = 0
    removed_paths: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        path = entry.get("filename") or ""
        adds, dels = _int(entry.get("additions")), _int(entry.get("deletions"))
        seen_files += 1
        seen_add += adds
        seen_del += dels
        # A file is discounted once, by one reason: `removed` first, because
        # a removed lock file is a removal and subtracting it twice would
        # drive the reviewable size below zero.
        if str(entry.get("status") or "").strip().lower() == "removed":
            rm_files += 1
            rm_lines += adds + dels
            rm_del += dels
            removed_paths.append(path)
        elif is_generated(path):
            gen_files += 1
            gen_lines += adds + dels

    total_add = _int((pr_json or {}).get("additions"))
    total_del = _int((pr_json or {}).get("deletions"))
    files_p = _int((pr_json or {}).get("changedFiles"))

    files_n = max(seen_files, files_p)
    lines_n = max(seen_add + seen_del, total_add + total_del)

    # The unseen tail (see the docstring). `removed_share` is 0 whenever
    # nothing seen was removed, which is every pull request this pipeline
    # has ever sized.
    tail_files = max(files_n - seen_files, 0)
    tail_add = max(total_add - seen_add, 0)
    tail_del = max(total_del - seen_del, 0)
    removed_share = (rm_del / seen_del) if seen_del else 0.0
    tail_rm_lines = round(tail_del * removed_share)
    tail_rm_files = round(tail_files * (rm_files / seen_files)) if seen_files else 0
    # ...but never ALL of them while the tail still carries lines a removal
    # cannot own. Its additions, and whatever deletions the split above did
    # NOT attribute, live in files that are not removals — so at least one
    # unseen file is review work, and claiming otherwise prints a record that
    # contradicts itself: `0 files / 87 lines reviewable` cannot be a real
    # state, and #2571 produced exactly that (3,000 seen files all removals,
    # so the ratio attributed all 80 unseen files — including the five that
    # carry the 87 added lines — to removals). One file is the floor this can
    # PROVE; the true count is unknowable, which is why the log names the
    # attribution rather than passing it off as a count.
    if tail_add + max(tail_del - tail_rm_lines, 0) > 0:
        tail_rm_files = min(tail_rm_files, max(tail_files - 1, 0))

    return {
        "files": files_n,
        "lines": lines_n,
        # What the critic must actually read. The discount is a lower bound
        # when the per-file record is truncated (generated files past the
        # last entry are not seen), which errs toward reviewing, not
        # refusing.
        "review_files": max(files_n - gen_files - rm_files - tail_rm_files, 0),
        "review_lines": max(lines_n - gen_lines - rm_lines - tail_rm_lines, 0),
        "generated_files": gen_files,
        "generated_lines": gen_lines,
        # Whole-file removals: reported in full, discounted from the review,
        # and handed to the critic as a list of paths to check references
        # against (DRE-3995).
        "removed_files": rm_files,
        "removed_lines": rm_lines,
        "removed_paths": removed_paths,
        "unseen_files": tail_files,
        "unseen_removed_files": tail_rm_files,
        "unseen_removed_lines": tail_rm_lines,
        "per_file_records": seen_files,
        "truncated": seen_files > 0 and files_p > seen_files,
    }


def choose(m: dict) -> str:
    """The review strategy for a measurement. Decided on the REVIEWABLE
    size — see the generated-files and removals notes in the module
    docstring."""
    files = m.get("review_files", 0)
    lines = m.get("review_lines", 0)
    if files > OVERSIZED_FILES or lines > OVERSIZED_LINES:
        return "oversized"
    if files > LARGE_FILES or lines > LARGE_LINES:
        return "large"
    # A removal-heavy pull request is REVIEWABLE, and it is still not
    # one-pass material: the `standard` block orders `gh pr diff` read whole
    # and the removed lines are in that diff whether or not they are review
    # work. #2571's reviewable part is 87 lines and its diff is two million.
    # The file-list strategy reads the same five files without ever printing
    # the diff into the critic's context, so the removals decide the PATH
    # here while the reviewable size decides everything else.
    if m.get("removed_lines", 0) > LARGE_LINES:
        return "large"
    return "standard"


def why(m: dict, strategy: str) -> str:
    """One sentence naming the threshold that decided it — the run log has
    to say WHICH path was taken and WHY (the whole diagnosis of #297 came
    from reading run records)."""
    files, lines = m.get("review_files", 0), m.get("review_lines", 0)
    if strategy == "oversized":
        return (
            f"reviewable size {files:,} files / {lines:,} lines is past the "
            f"fail-fast threshold ({OVERSIZED_FILES:,} files / "
            f"{OVERSIZED_LINES:,} lines)"
        )
    if strategy == "large":
        # WHICH threshold decided it, truthfully. A removal-heavy pull
        # request reaches `large` with a reviewable size UNDER the one-pass
        # threshold, and a log line claiming otherwise is the kind of record
        # #297 was misdiagnosed from for a day (DRE-2465).
        if files <= LARGE_FILES and lines <= LARGE_LINES:
            return (
                f"reviewable size {files:,} files / {lines:,} lines is "
                f"within the one-pass threshold, but the "
                f"{m.get('removed_lines', 0):,} removed lines are in the "
                f"diff a single pass has to read — reviewing from the "
                f"changed-file list instead"
            )
        return (
            f"reviewable size {files:,} files / {lines:,} lines is past the "
            f"one-pass threshold ({LARGE_FILES:,} files / {LARGE_LINES:,} "
            f"lines) — reviewing from the changed-file list instead"
        )
    return (
        f"reviewable size {files:,} files / {lines:,} lines is within the "
        f"one-pass threshold ({LARGE_FILES:,} files / {LARGE_LINES:,} lines)"
    )


def turn_budget(strategy: str) -> tuple[int, int]:
    """(first attempt, retry) ceilings. An unknown strategy gets the
    standard budget — a typo must not hand the action an empty ceiling."""
    return TURN_BUDGET.get(strategy, TURN_BUDGET["standard"])


def summary_line(m: dict, strategy: str) -> str:
    # The removal clause appears exactly when removals decided something, so
    # a refusal that should not have happened is readable off one log line
    # (DRE-3995: four refusals of #2571 said only "3,080 files").
    removals = ""
    if m.get("removed_files"):
        removals = (
            f", {m['removed_files']:,} removed files / "
            f"{m['removed_lines']:,} removed lines discounted"
        )
        if m.get("unseen_removed_files"):
            removals += (
                f" + {m['unseen_removed_files']:,} of {m['unseen_files']:,} "
                f"files past the API's {FILES_API_MAX:,}-file ceiling "
                f"attributed to removals"
            )
    return (
        f"[qa-size] {m['files']:,} files / {m['lines']:,} changed lines "
        f"({m['review_files']:,} files / {m['review_lines']:,} lines "
        f"reviewable, {m['generated_files']:,} generated files discounted"
        f"{removals}"
        f"{', compare record truncated' if m.get('truncated') else ''}) "
        f"→ strategy: {strategy} — {why(m, strategy)}"
    )


def _size_phrase(m: dict) -> str:
    return f"{m['files']:,} files, {m['lines']:,} changed lines"


def _pr_ref(pr: str) -> str:
    """The PR number, digits only. It reaches this script from workflow
    context and is never anything else — this script must not be the place
    that assumption is first tested."""
    digits = re.sub(r"\D", "", str(pr or ""))
    return digits or "<n>"


#: Print ONE file's hunks out of the saved full diff. `index()`, not a
#: regex: a path is not a pattern (`.` and `+` are ordinary characters in
#: file names), and the git header line is `diff --git a/P b/P`, so the
#: match has to bracket the path on both sides. An instruction that silently
#: prints nothing would burn the very turns this strategy exists to save —
#: tests/test_critic_size_strategy.py runs this command for real.
PER_FILE_HUNKS = (
    "awk -v f=\"<path>\" '/^diff --git /{p = index($0, \" a/\" f \" b/\")} p' "
    "/tmp/qa-full.diff"
)

_EXHAUSTIVE = (
    "EXHAUSTIVE (mandatory): list every blocking finding you found in THIS "
    "verdict — do not ration findings across rounds. A re-review that "
    "unveils yet another pre-existing nit you could have caught earlier is "
    "a review failure, not diligence. (Origin: PR #7 took 6 rounds.)"
)


def _top_level(path: str) -> str:
    """The directory a removed path belongs to, for the collapsed listing.
    A file at the root is its own entry — `archive/` and `setup.py` are both
    things to check references against."""
    head, sep, _ = (path or "").partition("/")
    return f"{head}/" if sep else (head or "(root)")


def removal_context(m: dict) -> str:
    """What the critic is told about a pull request's whole-file removals.

    The size block above has already discounted them, so this block owes the
    critic two things: WHICH paths went, and the one question a removal
    actually raises. Reading 2,094,946 deleted lines is not that question —
    whether anything still live points at those paths is (DRE-3995).
    """
    count = _int(m.get("removed_files"))
    if not count:
        return ""
    paths = sorted({p for p in (m.get("removed_paths") or []) if p})
    if not paths:
        listing = "  (the paths were not readable — derive them from the diff)"
        head = f"The {count:,} removed paths:"
    elif len(paths) <= REMOVED_PATHS_LISTED:
        listing = "\n".join(f"  - {p}" for p in paths)
        head = f"The {count:,} removed paths:"
    else:
        tally: dict[str, int] = {}
        for path in paths:
            tally[_top_level(path)] = tally.get(_top_level(path), 0) + 1
        listing = "\n".join(
            f"  - {d} ({c:,} files)"
            for d, c in sorted(tally.items(), key=lambda kv: (-kv[1], kv[0]))
        )
        head = (
            f"The {count:,} removed paths, by top-level directory (too many "
            f"to list one by one):"
        )
    return (
        f"\n\nWHOLE-FILE REMOVALS: {count:,} files / "
        f"{_int(m.get('removed_lines')):,} lines, NOT counted in the size "
        "above. Those files are gone; what their deleted lines said is not "
        "the review, and you do not have to read them.\n"
        "THE CHECK THIS PULL REQUEST OWES YOU INSTEAD, and it is mandatory: "
        "confirm that nothing OUTSIDE the removed set still imports, "
        "executes, or links into these paths — search the head of the branch "
        "for each directory below and for the module names under it "
        "(imports, scripts and workflow `run:` lines, documentation links, "
        "config references). A removal that breaks a live reference is a "
        "BLOCKING finding. Every changed file that is NOT on this list is "
        "ordinary review work and gets your normal read — filter these paths "
        "out of the changed-file list and what remains is your review plan.\n"
        f"{head}\n{listing}"
    )


def strategy_context(strategy: str, m: dict, pr: str) -> str:
    """The REVIEW STRATEGY block injected into both critic prompts."""
    n = _pr_ref(pr)
    size = _size_phrase(m)
    if strategy != "large":
        return (
            f"STANDARD REVIEW (this PR measures {size} — within the size a "
            "single pass handles). Read the whole diff in one pass: "
            f"`gh pr diff {n}`. Examine the ENTIRE diff.\n"
            f"{_EXHAUSTIVE}"
            f"{removal_context(m)}"
        )
    first, _ = turn_budget("large")
    return (
        # The size claim has to hold at the BOTTOM of this band as well as
        # the top: DRE-2924 lowered the entry threshold to 10 files / 1,500
        # lines, and "several times larger than anything reviewed here" is
        # false of a 12-file pull request. What is true at every size above
        # the line is that one exhaustive pass has not been observed to
        # finish there.
        f"LARGE-PULL-REQUEST REVIEW (this PR measures {size} — past the "
        "size a single exhaustive pass has been observed to finish here). "
        f"Do NOT attempt a single exhaustive `gh pr diff {n}` pass: at this "
        "size that is what made previous reviews finish early, or run out "
        "of turns, and produce no verdict at all. Work from the FILE LIST "
        "instead:\n"
        f"  1. `gh pr diff {n} --name-only > /tmp/qa-files.txt` — the "
        "changed-file list. Read it. This is your review plan.\n"
        f"  2. `gh pr diff {n} > /tmp/qa-full.diff` — keep the diff ON DISK. "
        "Never print it whole into your context; that is the failure this "
        "strategy exists to avoid.\n"
        "  3. Triage the list by review risk, highest risk first: "
        "migrations and data handling, auth and security, money, deletions, "
        "config/CI/workflows, then everything else. Generated and lock "
        "files (package-lock.json, *.lock, snapshots, minified bundles) "
        "need no line-by-line read — check that the change which produced "
        "them is sane.\n"
        "  4. Review PER FILE, in that order. For one file's hunks:\n"
        f"     `{PER_FILE_HUNKS}`\n"
        "     Grep /tmp/qa-full.diff to locate a file, and Read the file "
        "itself when you need surrounding context.\n"
        "  5. Update /tmp/qa-verdict.md after each file you finish, so the "
        "verdict always reflects what you have found so far.\n"
        f"  6. Keep going until every file on the list is covered or you are "
        f"near your turn budget ({first} turns). Then finish the verdict.\n"
        f"{_EXHAUSTIVE} Here that requirement is scoped to the files you "
        "ACTUALLY reviewed: in `## For the fixing agent`, state which files "
        "you reviewed and which you did not review, so nobody mistakes a "
        "file you never opened for a clean one."
        f"{removal_context(m)}"
    )


def oversize_message(m: dict) -> str:
    """The PR comment + job-failure text for a PR too large to review.

    It says exactly why it stopped and nothing else. DRE-2465: the critic's
    failure notice blamed the credential when the reviewer had actually run,
    and that cost a day of credential-hunting — this path knows its reason,
    so it must not borrow anyone else's.
    """
    return (
        f"🔎 QA Critic — this pull request is too large to review "
        f"({_size_phrase(m)}).\n\n"
        f"No review was attempted, so there are no findings and this is NOT "
        f"a code rejection. The reviewer's working limit is "
        f"{OVERSIZED_FILES:,} files / {OVERSIZED_LINES:,} changed lines "
        f"(generated and lock files do not count toward it, and neither do "
        f"removed files — splitting off deletions will not help).\n\n"
        f"Split this change into smaller pull requests — each one "
        f"independently reviewable — and every part gets a full review. "
        f"The merge is held until a reviewer has actually read this change."
    )


def _read_json(path: str) -> dict:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            value = json.load(fh)
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--compare-file", default="",
                    help="GitHub compare record (Resolve PR already writes it)")
    ap.add_argument("--pr-json-file", default="",
                    help="gh pr view --json changedFiles,additions,deletions")
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""),
                    help="owner/name, for the paginated PR files API")
    ap.add_argument("--pr", default="")
    args = ap.parse_args(argv)

    try:
        compare_record = _read_json(args.compare_file)
        pr_json = _read_json(args.pr_json_file)
        m = measure(compare_record, pr_json)
        # The per-file STATUSES, when the compare record cannot carry them
        # all: it truncates at 300 entries and #2571 changes 3,080 files
        # (DRE-3995). Under that cap the compare record is complete and the
        # extra API calls are not spent; above it, every page costs one call
        # and a wrong refusal costs a pull request that cannot merge.
        if m["files"] > m["per_file_records"]:
            fetched = fetch_pr_files(args.repo, args.pr)
            if fetched:
                m = measure(compare_record, pr_json, files=fetched)
        strategy = choose(m)
    except Exception as exc:  # degrade to today's behavior, never wedge
        print(f"pr_size_strategy: sizing failed ({exc}) — falling back to the "
              "standard one-pass review", file=sys.stderr)
        m = measure(None, None)
        strategy = "standard"

    first, retry = turn_budget(strategy)
    # The chosen path and the measured size, in the run log — a strategy
    # switch nobody can see is unauditable, and #297 was diagnosed entirely
    # from run records. Mirrored into the job summary when there is one.
    print(summary_line(m, strategy))
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        try:
            with open(summary_path, "a", encoding="utf-8") as fh:
                fh.write(summary_line(m, strategy) + "\n")
        except OSError:
            pass

    outputs = {
        "strategy": strategy,
        "files": str(m["files"]),
        "lines": str(m["lines"]),
        "review_files": str(m["review_files"]),
        "review_lines": str(m["review_lines"]),
        "removed_files": str(m["removed_files"]),
        "removed_lines": str(m["removed_lines"]),
        "max_turns": str(first),
        "retry_max_turns": str(retry),
        # One rendering of the size, for anything that has to name it in
        # prose (the head-bound check's summary).
        "size_phrase": _size_phrase(m),
    }
    blocks = {
        "strategy_context": strategy_context(strategy, m, args.pr),
        "oversize_message": oversize_message(m),
        "summary": summary_line(m, strategy),
    }
    out_path = os.environ.get("GITHUB_OUTPUT")
    if not out_path:
        for name, value in blocks.items():
            print(f"--- {name} ---\n{value}")
        return 0
    with open(out_path, "a", encoding="utf-8") as fh:
        for name, value in outputs.items():
            fh.write(f"{name}={value}\n")
        for name, value in blocks.items():
            _write_output(fh, name, value)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
