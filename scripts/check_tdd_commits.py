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
    docs = `docs/` + any `*.md` (README, standards/, briefs/) + a static
           design record (`.html`/`.md`/`.png`/`.jpg`/`.jpeg`/`.svg`/`.pen`/
           `.json` under `console/design/` or a root `design/`, DRE-3763);
    ops  = `.github/` + `config/` + `agents.yaml`.
    Anything unrecognized counts as code — fail-closed, so a new source tree
    can't silently dodge the discipline.
  • A `.py` file whose change is documentation is docs too (DRE-2409), and
    that is decided by CONTENT, not by path: parse the commit's parent and
    child versions, strip every docstring from both, and compare the
    resulting syntax trees. Identical trees ⇒ nothing executable changed ⇒
    docs. This repo keeps its architecture narrative in module docstrings,
    so path-only classification demanded a RED test for a prose paragraph
    (live: bp #145 / DRE-2409, one commit, `scripts/reconcile.py`, +7/-0) —
    and the only test you can write for a paragraph is a vacuous one, which
    the engineering standard separately bans. The rule is ungameable: any
    real behaviour change moves the AST. It is also NOT a label — a
    `docs-only` PR label would be a bypass the build agent could award
    itself. Everything about it stays fail-closed: a file added or deleted
    by the PR has no counterpart version and is code; source that fails to
    parse on either side is code; a docstring edit riding beside a real edit
    is code; non-`.py` paths are never AST-compared.
  • Dependabot-authored PRs are exempt (DRE-2049): a dependency bump has no
    behavior of its own to RED-test — its proof is the whole suite running
    against the bumped pins (the `unit` job installs from the manifest).
    The author arrives via the PR_AUTHOR env var, GitHub-attested identity
    (never the spoofable branch name). Live origin: bp #93's critic-APPROVEd
    pyyaml minor could not auto-merge behind a permanently red TDD check.
  • The nightly standards-sync PR is exempt (DRE-3885), matched by BRANCH
    AND AUTHOR together — head branch `bot/standards-sync` and every one of
    the PR's own commits authored by `agent-bureau-bot[bot]`. agent-bureau's
    `standards-sync.yml` opens one PR a night carrying two generated plugin
    manifests and one instructions file; the manifests classify as code (they
    are neither docs nor ops paths), so the gate failed every night on a
    change with no behaviour of its own to RED-test, and the finding is the
    commit ORDER, which no added commit clears (DRE-2694). Both halves are
    load-bearing, and the CEO chose this shape over the path alternative on
    2026-09-14: a path exemption for `plugins/**` would let real plugin code
    skip the discipline, the branch alone would exempt whatever anyone pushes
    there, and the author alone would exempt most of the fleet's PRs — this
    bot authors nearly all of them. The branch arrives via HEAD_REF, or via
    GITHUB_HEAD_REF, which GitHub itself sets on every `pull_request` run, so
    a fleet repo whose own workflow was never edited still gets the
    exemption; with neither set (a pre-push local run) the check proceeds.
  • Merge commits are skipped: merging an advanced main into the branch
    brings mainline commits that are not the PR's own work.

Head-of-PR test-suite greenness is NOT re-checked here — the Pipeline Tests
`unit` job already covers it and stays required.

Called from tests.yml's `tdd` job (pull_request events only):

    python3 scripts/check_tdd_commits.py "origin/$BASE_REF" "$HEAD_SHA"

…and, one rung earlier, by the writer against a branch that has not been
pushed yet — the build agent's own step 4b in agent-task.yml, and the same
line in standards/engineering.md for everyone who never sees that prompt:

    python3 scripts/check_tdd_commits.py origin/<default-branch> HEAD

Same rule, same exit codes; the difference is only what a red answer costs.
Before the push it costs a local rebase. After it, only a human rewriting the
branch can clear it (see below).

Exit 0 → discipline holds (or the PR is exempt). Exit 1 → violation, with the
plain-language message on stdout. Exit 2 → cannot evaluate (git error) — fail
loud, never pass.

DRE-2694: a violation also prints WHY no commit can clear it and what does.
The finding is the ORDER of commits that already exist, so the fix loop —
which can add commits but not reorder them — has no path to green here, and
saying nothing about that cost PR #176 three hours and two review rounds. The
wording comes from `unfixable_checks.py`, the one registry of checks with no
add-a-commit path, which the fix loop reads to escalate on the FIRST attempt
instead of attempting and blocking.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys

import unfixable_checks

FAILURE_MESSAGE = (
    "no test commit precedes the implementation — commit the RED test first"
)

# Path prefixes/names per category. Checked in order; first match wins, and
# anything unmatched is code (fail-closed).
#
# TEST paths are matched by DIRECTORY SEGMENT and by filename convention, not by
# one top-level prefix (DRE-2741). This check was written for bureau-pipeline's
# own PRs, where the tests live in a top-level `tests/`, and then applied to the
# whole fleet — where they do not. Measured on agent-bureau: `infra/test/`,
# `console/backend/tests/`, `cloud/relay/tests/` and `infra/*.test.ts` ALL
# classified as `code`, so no branch in that repo could satisfy the gate however
# correctly it was built. Combined with DRE-2694 (this check is unfixable by
# adding a commit) that left PRs permanently stuck on something no amount of
# right behaviour could clear.
_TEST_SEGMENTS = frozenset({"tests", "test", "spec", "__tests__"})
_TEST_SUFFIXES = (
    ".test.ts", ".test.tsx", ".test.js", ".test.jsx",
    ".spec.ts", ".spec.tsx", ".spec.js", ".spec.jsx",
    "_test.py", "_test.go", "_test.rb",
)
_DOCS_PREFIXES = ("docs/",)
# Static design records are docs too (DRE-3763). agent-bureau #2523 was one
# commit adding one CEO-approved design page under `console/design/screens/`,
# and it classified as code — a failure no added commit can clear (DRE-2694),
# for a file whose only possible RED test is a vacuous one. A design record
# documents a decision, the way `docs/` does.
#
# Two limits keep this fail-closed. The directory is matched by PREFIX, not by
# segment the way test trees are: `console/design/` is agent-bureau's shape and
# a root `design/` is project-template's (what new repos are built from) and
# deltasolv's, while a `design/` folder inside application source is part of the
# app. And the EXTENSION decides, not the directory: `tokens.css` feeds the app
# build and `.ts`/`.tsx`/`.js`/`.py` are source wherever they sit, so only the
# record formats below move out of `code` — everything else there stays code.
_DESIGN_RECORD_PREFIXES = ("console/design/", "design/")
_DESIGN_RECORD_SUFFIXES = (
    ".html", ".md", ".png", ".jpg", ".jpeg", ".svg", ".pen", ".json",
)
_OPS_PREFIXES = (".github/", "config/")
_OPS_FILES = frozenset({"agents.yaml"})


# The nightly standards-sync PR (DRE-3885) — the branch and the author it must
# BOTH match. Neither half is sufficient on its own; see the module docstring.
STANDARDS_SYNC_BRANCH = "bot/standards-sync"
STANDARDS_SYNC_BOT = "agent-bureau-bot"


def _normalized_bot_login(login: str) -> str:
    """One actor's three spellings collapsed to one: GitHub surfaces a Bot
    identity as "<name>" (GraphQL), "<name>[bot]" (REST / the Actions event
    shape / the git author line a workflow commit carries) or "app/<name>"
    (gh's bot marker)."""
    return login.strip().removeprefix("app/").removesuffix("[bot]")


def is_dependabot_author(login: str | None) -> bool:
    """True iff the PR author login is dependabot[bot]. Same normalization
    as reconcile.is_dependabot_pr — all three spellings are the same actor.
    Exact match on the normalized login, so a user account NAMED to look like
    the bot doesn't dodge the discipline."""
    if not login:
        return False
    return _normalized_bot_login(login) == "dependabot"


def is_standards_sync_author(author: str | None) -> bool:
    """True iff `author` is the standards-sync bot, exactly. Same
    normalization and the same exact match as the dependabot exemption, so
    neither a pool bot (`agent-bureau-bot-3`) nor the merging identity
    (`agent-bureau-qa-bot`) nor an account named to resemble either inherits
    the exemption."""
    if not author:
        return False
    return _normalized_bot_login(author) == STANDARDS_SYNC_BOT


def is_standards_sync_pr(head_ref: str | None, authors) -> bool:
    """True iff this is the nightly standards-sync PR: head branch exactly
    `bot/standards-sync` AND every one of the PR's own commits authored by the
    sync bot.

    Fail-closed on both sides. An unknown branch (neither HEAD_REF nor
    GITHUB_HEAD_REF set) is not a match, and an EMPTY commit list is not
    either — `all()` over nothing is True, which would exempt a PR whose
    commits could not be read."""
    authors = list(authors)
    if not authors:
        return False
    if (head_ref or "").strip() != STANDARDS_SYNC_BRANCH:
        return False
    return all(is_standards_sync_author(a) for a in authors)


class _DocstringStripper(ast.NodeTransformer):
    """Drop the leading string expression from every scope that can hold a
    docstring. What survives is the module's executable shape."""

    def _strip(self, node):
        self.generic_visit(node)
        body = getattr(node, "body", None)
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            node.body = body[1:]
        return node

    visit_Module = _strip
    visit_FunctionDef = _strip
    visit_AsyncFunctionDef = _strip
    visit_ClassDef = _strip


def _executable_shape(source: str) -> str | None:
    """Python source → a canonical string of its docstring-free syntax tree,
    or None if it will not parse. `ast.dump` omits line/column attributes, so
    inserting prose (which shifts every line below it) leaves the shape
    untouched — that is the whole point."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        # ValueError covers source containing null bytes; RecursionError,
        # pathologically nested expressions. Unparseable ≠ unchanged.
        return None
    try:
        return ast.dump(_DocstringStripper().visit(tree))
    except RecursionError:
        return None


def is_docs_only_python_change(before: str | None, after: str | None) -> bool:
    """True iff two versions of a Python file differ ONLY in documentation.

    `before`/`after` are the file's contents on either side of the commit, or
    None when the file does not exist there (added or deleted by the commit).
    Missing counterpart, or source that will not parse on either side, is
    False — the caller then falls through to `code`, which is the fail-closed
    answer.

    Comments ride the same path as docstrings, by design: `#` comments never
    reach the AST, so a comment-only edit compares equal and lands in `docs`.
    That is intended — a comment is prose with no executable effect, exactly
    like a docstring, and demanding a RED test for one yields the same
    vacuous test. Pure reformatting compares equal for the same reason and
    for the same intended reason: it changes nothing that can be tested.
    Anything that alters behaviour — a literal, an argument, an order of
    statements — alters the tree and stays `code`."""
    if before is None or after is None:
        return False
    shape_before = _executable_shape(before)
    if shape_before is None:
        return False
    shape_after = _executable_shape(after)
    if shape_after is None:
        return False
    return shape_before == shape_after


def is_test_path(path: str) -> bool:
    """True iff `path` is a test by its DIRECTORY or by its FILENAME.

    Deliberately narrow at the edges. A directory counts only when its own name
    says it holds tests, and a filename only when it follows a runner's
    discovery convention (pytest's `test_*.py`, Go's `*_test.go`, jest's
    `*.test.ts`). Merely LIVING beside a suite is not enough — otherwise the
    discipline could be dodged by parking implementation next to one.

    This only ever moves a path OUT of `code`, so it cannot make the gate
    stricter and cannot let implementation masquerade as a test. The
    fail-closed default for everything unrecognized is unchanged.
    """
    *dirs, name = path.split("/")
    if any(d in _TEST_SEGMENTS for d in dirs):
        return True
    return name.startswith("test_") or name.endswith(_TEST_SUFFIXES)


def is_design_record(path: str) -> bool:
    """True iff `path` is a static design record (DRE-3763): a record format
    under `console/design/` or a root `design/`. Like `is_test_path`, this only
    ever moves a path OUT of `code`; source and stylesheets under those
    directories stay code. The extension is compared case-insensitively — a
    screenshot saved as `.PNG` is the same record as one saved as `.png`."""
    return (
        path.startswith(_DESIGN_RECORD_PREFIXES)
        and path.lower().endswith(_DESIGN_RECORD_SUFFIXES)
    )


def classify_path(
    path: str, before: str | None = None, after: str | None = None
) -> str:
    """One changed path → 'test' | 'docs' | 'ops' | 'code'.

    `before`/`after` are the file's two versions across the commit, supplied
    only for `.py` paths the path rules would otherwise call `code`. Omitting
    them keeps the pre-DRE-2409 path-only answer, which is the strict one."""
    if is_test_path(path):
        return "test"
    if (
        path.startswith(_DOCS_PREFIXES)
        or path.endswith(".md")
        or is_design_record(path)
    ):
        return "docs"
    if path.startswith(_OPS_PREFIXES) or path in _OPS_FILES:
        return "ops"
    if path.endswith(".py") and is_docs_only_python_change(before, after):
        return "docs"
    return "code"


def commit_categories(commit) -> set[str]:
    """Every category one commit touches. A commit may carry a `sources` map
    {path: (before, after)}; a commit without one classifies by path alone."""
    sources = commit.get("sources") or {}
    return {
        classify_path(p, *sources.get(p, (None, None)))
        for p in commit["paths"]
    }


def check_commits(commits) -> tuple[bool, str]:
    """Apply the ordering rule to an OLDEST-FIRST list of commit records
    (dicts with `sha`, `subject`, `paths`). Returns (ok, reason)."""
    first_code = next(
        (i for i, c in enumerate(commits) if "code" in commit_categories(c)),
        None,
    )
    if first_code is None:
        return True, "exempt: no non-test code changed (docs/ops/tests only)"
    if any("test" in commit_categories(c) for c in commits[:first_code]):
        return True, "a test commit precedes the first implementation commit"
    return False, FAILURE_MESSAGE


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True
    ).stdout


def _blob(rev: str, path: str) -> str | None:
    """`<rev>:<path>` as text, or None if it isn't there / isn't text.

    Deliberately does NOT raise: a file the commit ADDS has no parent
    version and a file it DELETES has no child version, and neither is a
    broken checkout. None flows into is_docs_only_python_change, which
    answers False, which lands the path on `code`."""
    p = subprocess.run(
        ["git", "show", f"{rev}:{path}"], capture_output=True
    )
    if p.returncode != 0:
        return None
    try:
        return p.stdout.decode("utf-8")
    except UnicodeDecodeError:
        return None


def pr_commits(base: str, head: str):
    """The PR's own commits, oldest first, each with its changed paths, its
    AUTHOR (the git author name, which is what the standards-sync exemption
    reads — per commit, so one foreign commit on that branch ends it) and —
    for the `.py` paths a path-only read would call code — both versions of
    the file across the commit, so the AST-equivalence rule can see them.
    `base..head` excludes everything already on the base branch, and
    --no-merges drops merge-from-main commits (not the PR's own work).
    Merges being excluded, `<sha>^` is the one unambiguous parent."""
    shas = _git(
        "rev-list", "--reverse", "--topo-order", "--no-merges",
        f"{base}..{head}",
    ).split()
    commits = []
    for sha in shas:
        subject = _git("log", "-1", "--format=%s", sha).strip()
        author = _git("log", "-1", "--format=%an", sha).strip()
        paths = _git(
            "diff-tree", "--no-commit-id", "--name-only", "-r", sha
        ).split("\n")
        paths = [p for p in paths if p]
        sources = {
            p: (_blob(f"{sha}^", p), _blob(sha, p))
            for p in paths
            if p.endswith(".py") and classify_path(p) == "code"
        }
        commits.append({
            "sha": sha,
            "subject": subject,
            "author": author,
            "paths": paths,
            "sources": sources,
        })
    return commits


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: check_tdd_commits.py <base> <head>", file=sys.stderr)
        return 2
    base, head = argv
    if is_dependabot_author(os.environ.get("PR_AUTHOR")):
        print("exempt: dependabot-authored dependency PR — no RED test to "
              "demand; the unit job runs the suite against the bumped pins")
        return 0
    try:
        commits = pr_commits(base, head)
    except subprocess.CalledProcessError as e:
        # Cannot evaluate ≠ pass: a broken checkout must be a red job.
        print(f"git failed: {e.stderr.strip()}", file=sys.stderr)
        return 2
    for c in commits:
        cats = sorted(commit_categories(c)) or ["empty"]
        print(f"{c['sha'][:7]} [{','.join(cats)}] {c['subject']}")
    # The branch: explicit when the caller threads it, else GitHub's own
    # pull_request variable, so a fleet repo running this from
    # `.bureau-pipeline/scripts/` gets the exemption without a workflow edit.
    head_ref = os.environ.get("HEAD_REF") or os.environ.get("GITHUB_HEAD_REF")
    if is_standards_sync_pr(head_ref, [c.get("author") for c in commits]):
        print(f"exempt: the nightly standards-sync PR — branch "
              f"{STANDARDS_SYNC_BRANCH}, every commit authored by "
              f"{STANDARDS_SYNC_BOT}[bot]; generated manifests with no "
              f"behaviour of their own to RED-test")
        return 0
    ok, reason = check_commits(commits)
    print(reason)
    if not ok:
        # DRE-2694: tell the writer, here at CI time, that adding a commit
        # cannot clear this — the cheapest place the discipline is knowable.
        entry = unfixable_checks.match(unfixable_checks.TDD_CHECK_NAME)
        print(unfixable_checks.remedy_block(entry))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
