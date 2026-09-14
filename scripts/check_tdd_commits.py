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
  • A `.py` change confined to a GENERATED region is generated, not authored
    (DRE-3896), and is docs too — but ONLY in a file some generator's `--check`
    actually proves. A region is the lines strictly between a line
    containing `BEGIN generated` and the next line containing `END generated`
    — the markers `scripts/sync_model_config.py` already writes, read
    generically here. The live shape: an automated adoption PR edits
    `config/models.yaml` and reruns that generator, which rewrites the
    `_FALLBACK_MODEL_CONFIG` literal in `scripts/model_fallback.py`. That
    literal moves the AST, so the docstring rule cannot exempt it, and there
    is no author to write a RED test for a rendered literal — a failure
    DRE-2694 makes unfixable by adding a commit.
    **The exemption is safe because the region's content is proved
    elsewhere:** `python3 scripts/sync_model_config.py --check` fails when a
    generated region does not match its canonical render, and it runs in CI
    (the unit suite asserts it on every PR), so code cannot hide in a region.
    That proof is the whole argument, so the classifier REQUIRES it rather
    than asserting it: the exempt paths are a closed map in
    `_GENERATED_REGION_PROOFS`, each named with the command that proves it,
    and a generated-region change on any other path is `code`. Markers alone
    must never carry the exemption — they are comment lines any commit can
    write, so one ordinary reviewed commit adding them to an arbitrary file
    would otherwise buy that file a permanent, untested edit channel strictly
    inside them, with no generator anywhere near it. That is the opposite of
    the docstring rule, whose safety is intrinsic (any real change moves the
    AST) and so needs no allowlist.
    Fail-closed exactly like the docstring rule: a path with no proving
    generator, a change touching one line outside the region, an edit to a
    marker line itself, a region with no closing marker, and source that will
    not parse on either side all stay `code`.
  • Dependabot-authored PRs are exempt (DRE-2049): a dependency bump has no
    behavior of its own to RED-test — its proof is the whole suite running
    against the bumped pins (the `unit` job installs from the manifest).
    The author arrives via the PR_AUTHOR env var, GitHub-attested identity
    (never the spoofable branch name). Live origin: bp #93's critic-APPROVEd
    pyyaml minor could not auto-merge behind a permanently red TDD check.
  • The nightly standards-sync PR is exempt (DRE-3885). agent-bureau's
    `standards-sync.yml` opens one PR a night carrying two generated plugin
    manifests and one instructions file; the manifests classify as code (they
    are neither docs nor ops paths), so the gate failed every night on a
    change with no behaviour of its own to RED-test, and the finding is the
    commit ORDER, which no added commit clears (DRE-2694).
    WHAT DECIDES IT is the same class of signal as dependabot's, for the same
    reason: the GitHub-ATTESTED login that opened the PR must be the sync bot.
    It arrives on PR_AUTHOR where a workflow threads it, else straight out of
    the `pull_request` event payload the runner writes at GITHUB_EVENT_PATH —
    agent-bureau, where the nightly PR is opened, runs this checker from
    `.bureau-pipeline/scripts/` through a workflow this repo cannot edit.
    Two further conditions NARROW the exemption to that one job: head branch
    exactly `bot/standards-sync` (HEAD_REF, else GITHUB_HEAD_REF, which GitHub
    sets itself on every `pull_request` run), and every one of the PR's own
    commits carrying the bot's git author line, so a human commit riding along
    on that branch ends it. Neither narrowing is a CREDENTIAL: a branch name
    is chosen by whoever pushes it and `git commit --author` asks nobody's
    permission, so both are forgeable by anyone who can open a PR. The
    attested opener is what makes the exemption safe, and it is refused
    outright when that identity cannot be read (a pre-push local run). The CEO
    chose an identity exemption over the path alternative on 2026-09-14: a
    path exemption for `plugins/**` would let real plugin code skip the
    discipline forever, and the bot's identity alone would exempt most of the
    fleet's PRs — it authors nearly all of them.
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
import json
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

# Generated-region markers (DRE-3896), read as SUBSTRINGS of a line so the one
# rule covers every generator's comment syntax and wording. These are the
# markers `scripts/sync_model_config.py` already writes — e.g.
# `# --- BEGIN generated model config (from config/models.yaml) ---` — and the
# contract is deliberately generic: a line containing BEGIN opens a region, the
# next line containing END closes it.
_BEGIN_GENERATED = "BEGIN generated"
_END_GENERATED = "END generated"

# The ONLY paths whose generated regions are exempt, each mapped to the command
# that PROVES the region's content in CI. The exemption's entire safety argument
# is that proof — so the classifier demands the proof exists rather than taking
# the markers' word for it.
#
# Marker-shape alone is not a proof and must never be read as one: markers are
# ordinary comment lines any commit can write. A file could carry them in one
# perfectly reviewable commit and then, in every commit after, have the code
# inside them hand-edited with no test and no generator watching — a standing,
# permanent bypass of this entire check for that file. So the substring rule
# below answers "is this change confined to a region", and this map answers the
# question that actually matters: "does anything prove what the region says".
# Both must hold.
#
# Adding an entry is a `.py` change to this file, which is `code` — so it takes
# a RED test first and a critic's read, which is exactly the bar a new TDD
# exemption should clear. Each command must run on every PR (both below are
# asserted by the unit suite: tests/test_model_config.py and
# tests/test_sync_fallback_map.py), because a proof that does not run proves
# nothing.
_GENERATED_REGION_PROOFS = {
    "scripts/model_fallback.py": "python3 scripts/sync_model_config.py --check",
    "scripts/validate_card.py": "python3 scripts/sync_fallback_map.py --check",
}


# The nightly standards-sync PR (DRE-3885) — the bot that must have OPENED it
# (GitHub-attested) and the branch that narrows the exemption to that one job.
# See the module docstring for which of the two is load-bearing, and why.
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


def names_standards_sync_bot(name: str | None) -> bool:
    """True iff `name` spells the standards-sync bot, exactly — same
    normalization and the same exact match as the dependabot exemption, so
    neither a pool bot (`agent-bureau-bot-3`) nor the merging identity
    (`agent-bureau-qa-bot`) nor `not-agent-bureau-bot` matches.

    A NAME, not an identity: what this answers depends entirely on where the
    string came from. From `attested_pr_author` it is GitHub saying who opened
    the PR; from a commit's `%an` line it is a string the committer typed."""
    if not name:
        return False
    return _normalized_bot_login(name) == STANDARDS_SYNC_BOT


def attested_pr_author(env=None) -> str | None:
    """The login GitHub says opened this PR — the one signal here the PR's own
    contents cannot write.

    Two sources, both the same field. `PR_AUTHOR` is
    `github.event.pull_request.user.login`, threaded by the workflow (tests.yml
    here, the `pr-author` input on the shared tdd-commit-check action in the
    fleet). Where nothing threads it, the field is read straight out of the
    event payload the runner writes at GITHUB_EVENT_PATH — agent-bureau, where
    the nightly standards-sync PR is opened, runs this checker from
    `.bureau-pipeline/scripts/` through a workflow this repo cannot edit.

    Fail-closed: no variable, no file, unreadable JSON or a payload that is not
    a pull_request event all return None, and every exemption keyed on this is
    refused without it."""
    env = os.environ if env is None else env
    login = (env.get("PR_AUTHOR") or "").strip()
    if login:
        return login
    path = env.get("GITHUB_EVENT_PATH")
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            login = json.load(fh)["pull_request"]["user"]["login"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return login.strip() if isinstance(login, str) else None


def is_standards_sync_pr(head_ref: str | None, pr_author: str | None,
                         commit_authors) -> bool:
    """True iff this is the nightly standards-sync PR. Three conditions, all
    required, and only the first one is an IDENTITY:

      1. `pr_author` — the GitHub-attested login that opened the PR, from
         `attested_pr_author` — is the sync bot. This is the whole of the
         security: the PR opener does not get to choose what GitHub attests.
      2. the head branch is exactly `bot/standards-sync`;
      3. every one of the PR's own commits carries the bot's git author line.

    (2) and (3) NARROW the exemption to the one nightly job — they keep the
    bot's ordinary PRs, which are most of the fleet's, under the discipline,
    and they end the exemption the moment a human's commit rides along on that
    branch. Neither is a credential: a branch name is chosen by whoever pushes
    it, and `git commit --author` authenticates nothing.

    Fail-closed everywhere. No attested author (a pre-push local run) is not a
    match, an unknown branch (neither HEAD_REF nor GITHUB_HEAD_REF set) is not
    a match, and an EMPTY commit list is not either — `all()` over nothing is
    True, which would exempt a PR whose commits could not be read."""
    if not names_standards_sync_bot(pr_author):
        return False
    if (head_ref or "").strip() != STANDARDS_SYNC_BRANCH:
        return False
    commit_authors = list(commit_authors)
    if not commit_authors:
        return False
    return all(names_standards_sync_bot(a) for a in commit_authors)


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


def _outside_generated_regions(source: str) -> list[str] | None:
    """Every line of `source` that is NOT inside a generated region, with the
    marker lines kept, or None if there is no usable region.

    None on two shapes, both fail-closed: a file with no `BEGIN generated`
    line at all (nothing here is generated), and a region whose END marker is
    missing (an unterminated region would otherwise swallow the rest of the
    file and exempt every line below the marker).

    Keeping the markers in the returned list is what makes an edit to a marker
    a difference: the markers delimit the proof, so a moved one describes a
    region the generator never wrote."""
    lines = source.splitlines()
    outside: list[str] = []
    inside = False
    saw_region = False
    for line in lines:
        if inside:
            if _END_GENERATED in line:
                inside = False
                outside.append(line)
            continue
        outside.append(line)
        if _BEGIN_GENERATED in line:
            inside = True
            saw_region = True
    if inside or not saw_region:
        return None
    return outside


def has_generated_region_proof(path: str) -> bool:
    """True iff `path` is a file whose generated regions are PROVED by a
    command this repo runs on every PR (`_GENERATED_REGION_PROOFS`).

    Exact path match, never a prefix or a suffix: the proof is per-file, so a
    neighbouring file in the same directory is not covered by it."""
    return path in _GENERATED_REGION_PROOFS


def is_generated_region_change(
    path: str, before: str | None, after: str | None
) -> bool:
    """True iff `path` is a file with a generator proving its regions AND its
    two versions differ ONLY inside those regions (DRE-3896).

    `before`/`after` are the file's contents on either side of the commit, or
    None when the file does not exist there. Same contract and the same
    fail-closed edges as `is_docs_only_python_change`: a missing counterpart
    is False, and source that will not parse on either side is False — a
    syntax error is never waved through as "generated".

    Everything outside the regions is compared line for line, markers
    included, so a change that touches one line outside a region, or a marker
    line itself, is authored code and stays `code`.

    The `path` test comes first and is the load-bearing one. Without it the
    markers alone would carry the exemption, and markers are comment lines
    anybody can write: one ordinary commit adds them to any file, and every
    edit strictly inside them afterwards skips the RED-test requirement
    forever, with no generator and no `--check` anywhere near that file."""
    if not has_generated_region_proof(path):
        return False
    if before is None or after is None:
        return False
    if _executable_shape(before) is None or _executable_shape(after) is None:
        return False
    outside_before = _outside_generated_regions(before)
    if outside_before is None:
        return False
    outside_after = _outside_generated_regions(after)
    if outside_after is None:
        return False
    return outside_before == outside_after


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
    if path.endswith(".py") and (
        is_docs_only_python_change(before, after)
        or is_generated_region_change(path, before, after)
    ):
        # Documentation by content (DRE-2409) or a generated region (DRE-3896)
        # — neither has an author who could write a RED test for it.
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
    # The opener is read the same way — threaded, else from the event payload.
    head_ref = os.environ.get("HEAD_REF") or os.environ.get("GITHUB_HEAD_REF")
    if is_standards_sync_pr(head_ref, attested_pr_author(),
                            [c.get("author") for c in commits]):
        print(f"exempt: the nightly standards-sync PR — opened by "
              f"{STANDARDS_SYNC_BOT}[bot] (GitHub-attested), on branch "
              f"{STANDARDS_SYNC_BRANCH}, every commit authored by that bot; "
              f"generated manifests with no behaviour of their own to "
              f"RED-test")
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
