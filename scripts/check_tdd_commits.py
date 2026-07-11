#!/usr/bin/env python3
"""Enforce TDD commit discipline on a PR's commit list (stdlib only).

DRE-2022. bureau-pipeline's own PRs are hand-built (not dispatched), so the
rail's test-driven discipline — fail tests → implementation → checks → PR →
critic → merge — was enforced here only by convention in builder prompts plus
the critic's judgment. This makes it mechanical, the same way the merge gate
made verdicts mechanical: a cheap, deterministic check on the PR's commits,
no LLM call.

The rule (engineering standard: "commit the failing test FIRST"):

  • At least one commit touching files under `tests/` must appear STRICTLY
    BEFORE the first commit that changes non-test code. Same-commit doesn't
    count — history must SHOW the test existed before the fix.
  • Docs-only and ops-only PRs are exempt, classified by changed paths:
    docs = `docs/` + any `*.md` (README, standards/, briefs/);
    ops  = `.github/` + `config/` + `agents.yaml`.
    Anything unrecognized counts as code — fail-closed, so a new source tree
    can't silently dodge the discipline.
  • Merge commits are skipped: merging an advanced main into the branch
    brings mainline commits that are not the PR's own work.

Head-of-PR test-suite greenness is NOT re-checked here — the Pipeline Tests
`unit` job already covers it and stays required.

Called from tests.yml's `tdd` job (pull_request events only):

    python3 scripts/check_tdd_commits.py "origin/$BASE_REF" "$HEAD_SHA"

Exit 0 → discipline holds (or the PR is exempt). Exit 1 → violation, with the
plain-language message on stdout. Exit 2 → cannot evaluate (git error) — fail
loud, never pass.
"""

from __future__ import annotations

import subprocess
import sys

FAILURE_MESSAGE = (
    "no test commit precedes the implementation — commit the RED test first"
)

# Path prefixes/names per category. Checked in order; first match wins, and
# anything unmatched is code (fail-closed).
_TEST_PREFIXES = ("tests/",)
_DOCS_PREFIXES = ("docs/",)
_OPS_PREFIXES = (".github/", "config/")
_OPS_FILES = frozenset({"agents.yaml"})


def classify_path(path: str) -> str:
    """One changed path → 'test' | 'docs' | 'ops' | 'code'."""
    if path.startswith(_TEST_PREFIXES):
        return "test"
    if path.startswith(_DOCS_PREFIXES) or path.endswith(".md"):
        return "docs"
    if path.startswith(_OPS_PREFIXES) or path in _OPS_FILES:
        return "ops"
    return "code"


def check_commits(commits) -> tuple[bool, str]:
    """Apply the ordering rule to an OLDEST-FIRST list of commit records
    (dicts with `sha`, `subject`, `paths`). Returns (ok, reason)."""
    first_code = next(
        (i for i, c in enumerate(commits)
         if any(classify_path(p) == "code" for p in c["paths"])),
        None,
    )
    if first_code is None:
        return True, "exempt: no non-test code changed (docs/ops/tests only)"
    if any(
        any(classify_path(p) == "test" for p in c["paths"])
        for c in commits[:first_code]
    ):
        return True, "a test commit precedes the first implementation commit"
    return False, FAILURE_MESSAGE


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True
    ).stdout


def pr_commits(base: str, head: str):
    """The PR's own commits, oldest first, each with its changed paths.
    `base..head` excludes everything already on the base branch, and
    --no-merges drops merge-from-main commits (not the PR's own work)."""
    shas = _git(
        "rev-list", "--reverse", "--topo-order", "--no-merges",
        f"{base}..{head}",
    ).split()
    commits = []
    for sha in shas:
        subject = _git("log", "-1", "--format=%s", sha).strip()
        paths = _git(
            "diff-tree", "--no-commit-id", "--name-only", "-r", sha
        ).split("\n")
        commits.append({
            "sha": sha,
            "subject": subject,
            "paths": [p for p in paths if p],
        })
    return commits


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: check_tdd_commits.py <base> <head>", file=sys.stderr)
        return 2
    base, head = argv
    try:
        commits = pr_commits(base, head)
    except subprocess.CalledProcessError as e:
        # Cannot evaluate ≠ pass: a broken checkout must be a red job.
        print(f"git failed: {e.stderr.strip()}", file=sys.stderr)
        return 2
    for c in commits:
        cats = sorted({classify_path(p) for p in c["paths"]}) or ["empty"]
        print(f"{c['sha'][:7]} [{','.join(cats)}] {c['subject']}")
    ok, reason = check_commits(commits)
    print(reason)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
