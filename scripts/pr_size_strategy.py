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

  * 50 files / 5,000 changed lines. The largest review that has ever
    SUCCEEDED here is +4,092 across 29 files (#275); #290 ran 41 files. Both
    stay on the one-pass path they demonstrably survive, with headroom.
  * 200 files / 20,000 changed lines. Above #297 itself (118 / 17,537),
    deliberately: that PR must be reviewed, not refused. Also below
    GitHub's 300-file compare cap, so the refusal is never decided on
    truncated data.

GENERATED FILES ARE DISCOUNTED. A 25,000-line `package-lock.json` bump is
not a 25,000-line review — the critic reads the manifest and the lock is
noise. Counting it would push every dependency PR toward a path meant for
17k lines of hand-written code, and past the second threshold would refuse
them outright.

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
                        --pr-json-file /tmp/qa-size.json --pr 297
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

from sanitize_untrusted import _write_output

# ── thresholds (see the module docstring for where each number comes from) ──
LARGE_FILES = 50
LARGE_LINES = 5_000
OVERSIZED_FILES = 200
OVERSIZED_LINES = 20_000

#: (first attempt, retry) turn ceilings per strategy. The retry MUST stay
#: strictly higher — a retry that comes back with no more of the resource it
#: exhausted is a second invoice, not a recovery (DRE-2422). Pinned by
#: tests/test_critic_turn_budget.py, which now runs its whole assertion set
#: against every entry here.
TURN_BUDGET = {
    "standard": (80, 120),
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


def _int(value) -> int:
    """A count from an API record, or 0. Never raises — a malformed field
    must not be the thing that stops a review."""
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


def measure(compare: dict | None, pr_json: dict | None) -> dict:
    """Size the PR from both records; the larger signal wins.

    `compare` is GitHub's three-dot compare record (per-file, capped at 300
    files). `pr_json` is `gh pr view --json changedFiles,additions,deletions`
    (totals only, never truncated). The per-file record is what makes the
    generated-file discount possible; the totals are what makes the count
    honest above 300 files.
    """
    files_c = lines_c = gen_files = gen_lines = 0
    for entry in (compare or {}).get("files") or []:
        if not isinstance(entry, dict):
            continue
        changed = _int(entry.get("additions")) + _int(entry.get("deletions"))
        files_c += 1
        lines_c += changed
        if is_generated(entry.get("filename") or ""):
            gen_files += 1
            gen_lines += changed

    files_p = _int((pr_json or {}).get("changedFiles"))
    lines_p = _int((pr_json or {}).get("additions")) + _int(
        (pr_json or {}).get("deletions")
    )

    files = max(files_c, files_p)
    lines = max(lines_c, lines_p)
    return {
        "files": files,
        "lines": lines,
        # What the critic must actually read. The discount is a lower bound
        # when the compare record is truncated (generated files past entry
        # 300 are not seen), which errs toward reviewing, not refusing.
        "review_files": max(files - gen_files, 0),
        "review_lines": max(lines - gen_lines, 0),
        "generated_files": gen_files,
        "generated_lines": gen_lines,
        "truncated": files_c > 0 and files_p > files_c,
    }


def choose(m: dict) -> str:
    """The review strategy for a measurement. Decided on the REVIEWABLE
    size — see the generated-files note in the module docstring."""
    files = m.get("review_files", 0)
    lines = m.get("review_lines", 0)
    if files > OVERSIZED_FILES or lines > OVERSIZED_LINES:
        return "oversized"
    if files > LARGE_FILES or lines > LARGE_LINES:
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
    return (
        f"[qa-size] {m['files']:,} files / {m['lines']:,} changed lines "
        f"({m['review_files']:,} files / {m['review_lines']:,} lines "
        f"reviewable, {m['generated_files']:,} generated files discounted"
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
        )
    first, _ = turn_budget("large")
    return (
        f"LARGE-PULL-REQUEST REVIEW (this PR measures {size} — several "
        "times larger than any change reviewed here in one pass). Do NOT "
        f"attempt a single exhaustive `gh pr diff {n}` pass: at this size "
        "that is what made four previous reviews finish early and produce "
        "no verdict at all. Work from the FILE LIST instead:\n"
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
        f"(generated and lock files do not count toward it).\n\n"
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
    ap.add_argument("--pr", default="")
    args = ap.parse_args(argv)

    try:
        m = measure(_read_json(args.compare_file), _read_json(args.pr_json_file))
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
