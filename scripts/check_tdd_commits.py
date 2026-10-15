#!/usr/bin/env python3
"""Enforce TDD commit discipline on a PR's commit list (stdlib only).

DRE-2022. bureau-pipeline's own PRs are hand-built (not dispatched), so the
rail's test-driven discipline — fail tests → implementation → checks → PR →
critic → merge — was enforced here only by convention in builder prompts plus
the critic's judgment. This makes it mechanical, the same way the merge gate
made verdicts mechanical: a cheap, deterministic check on the PR's commits,
no LLM call.

The rule (engineering standard: "commit the failing test FIRST"):

  • One sentence, `RULE` below, printed by every run and carried word for
    word by standards/engineering.md (DRE-3009, the operator's "we should
    always be doing TDD" of 2026-09-03): every code commit needs a test
    commit since the previous code commit — for the first code commit,
    since the branch began. Any code commit after the first may instead
    carry an exemption from `config/tdd-exemptions.json`. Until DRE-3009 the
    floor was once per BRANCH — one test commit strictly before the first
    code commit — and a critic holding the stricter, unenforced reading
    cost agent-bureau #2247 22 hours (DRE-3005). Read mechanically off the
    commit list (oldest first, merges skipped):
      – a CODE commit touches at least one path `classify_path` calls code;
      – a TEST commit touches a test path and no code path, so a commit
        carrying both is a code commit and is not its own test — history
        must SHOW the test existed before the fix;
      – "since the previous code commit" means strictly between the two.
    The pairing is positional. Whether a test is real is the critic's call.
  • The exemptions are DATA (`config/tdd-exemptions.json`, `parse_exemptions`).
    An entry's `match` is `deletion-only` — detected: every code path in the
    commit adds zero lines in `git diff-tree --numstat`, and a binary path is
    never one — or `trailer`, honored only when the commit claims it with a
    `TDD-Exempt: <id>` git trailer. An unknown id fails the commit. Neither
    ever covers the FIRST code commit, so every branch with code still
    carries a real RED commit before it, and a branch the per-change rule
    passes also passes the old floor. Who wrote the trailer is not checked:
    commit authorship is not attested (see the standards-sync note below).
    What keeps a claim honest is that it is printed on its commit's line
    (`claimed: refactor`), stays in the history, and is read by the critic
    against that entry's `critic_checks`.
  • A branch cannot exempt itself. When this script sits in the repository
    under check (bureau-pipeline's own `tdd` job: the two
    `rev-parse --show-toplevel` roots are equal) the list is read at the
    merge base; anywhere else — a product repo's `.bureau-pipeline/`, the
    shared action's fetched copy, the critic's `$PIPELINE_DIR` — it is the
    copy beside this script, which the pull request under review cannot
    edit. No flag decides it, so no workflow edit can switch it. A merge
    base with no list at all is judged by the once-per-branch floor it
    started under (`check_floor`), which is how this card's own pull request
    and every one already open when it merged are judged; merging the
    default branch in or rebasing moves a branch onto the per-change rule.
    A list that is present but unreadable, or holds a shape
    `parse_exemptions` refuses, exits 2.
  • Docs-only and ops-only PRs are exempt, classified by changed paths:
    docs = `docs/` + any `*.md` (README, standards/, briefs/) + a static
           design record (`.html`/`.md`/`.png`/`.jpg`/`.jpeg`/`.svg`/`.pen`/
           `.json` under `console/design/` or a root `design/`, DRE-3763, or
           under a root `architecture/`, DRE-5311) + plain-text evidence
           (`.txt`/`.log`) under a root `architecture/` only, DRE-7330;
    ops  = `.github/` + `config/` + `agents.yaml` + `models.json`
           (the frozen catalog snapshot — data derived from the
           vendor's model list, added by DRE-3879 on the CEO's signed answer
           of 2026-09-16; exactly that one path, matched exactly).
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
  • The onboarding routing PR's mirror regions are not code (DRE-7209). The
    onboarding process opens one routing PR here and one in agent-bureau,
    both on the literal branch `bot/onboard`, adding a repo to the map and to
    every file regenerated from it. What is regenerated is not those whole
    files: it is the lines between `BEGIN generated repo mirror` and
    `END generated repo mirror` in each of agent-bureau's five slug mirrors,
    and that region is all `make sync-mirrors CHECK=1` proves. So a path in
    `ONBOARD_ROUTING_PATHS` leaves `code` only for a change confined to that
    region, under the same fail-closed edges as the generated-region rule
    above; a logic edit beside the region is code. This repo's
    `scripts/validate_card.py` is not in the set, because the generated-
    region rule already proves and exempts its mirror region. The same
    attested opener decides it as for standards-sync — `agent-bureau-bot`,
    from `attested_pr_author` — plus the exact branch. It is NOT a PR
    exemption: any other path on that branch, or any other line of a routing
    path, is held to the rule like any other.
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
import re
import subprocess
import sys

import unfixable_checks

# The data file the exemptions live in, as a path from a repository root.
EXEMPTIONS_FILE = "config/tdd-exemptions.json"

# The rule, once. standards/engineering.md carries this sentence word for word
# and tests/test_verdict_evidence.py fails if the two differ.
RULE = (
    "Every code commit needs a test commit since the previous code commit — "
    "for the first code commit, since the branch began. Any code commit after "
    "the first may instead carry an exemption from "
    f"`{EXEMPTIONS_FILE}`."
)

FAILURE_MESSAGE = (
    "no test commit precedes the implementation — commit the RED test first"
)
LATER_FAILURE_MESSAGE = (
    "no test commit since the previous code commit — commit a RED test "
    "before it, or claim an exemption it qualifies for"
)
UNKNOWN_CLAIM_MESSAGE = (
    "a commit claims an exemption the list does not offer as a claim"
)

# The git trailer a commit claims an exemption with.
TRAILER_KEY = "TDD-Exempt"

# The two ways an entry can match, and the four keys every entry carries.
MATCH_DELETION = "deletion-only"
MATCH_TRAILER = "trailer"
MATCH_KINDS = (MATCH_DELETION, MATCH_TRAILER)
_ENTRY_KEYS = frozenset({"id", "match", "reason", "critic_checks"})
_TOP_KEYS = frozenset({"_readme", "exemptions"})
_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]*\Z")

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
# a root `design/` is Foundry's (the design master), deltasolv's, and that of every
# repo built from Foundry's `templates/company/` (what new repos are built from),
# while a `design/` folder inside application source is part of the app. And the
# EXTENSION decides, not the directory: `tokens.css` feeds the app
# build and `.ts`/`.tsx`/`.js`/`.py` are source wherever they sit, so only the
# record formats below move out of `code` — everything else there stays code.
#
# A root `architecture/` is a record directory by the same rule (DRE-5311).
# agent-bureau #2920, the release-brake proof record, was two `.md` files and
# four screenshots under `architecture/proofs/release-brake/`; the screenshots
# classified as code, and no RED test can precede an image. `architecture/` is
# where the fleet keeps decisions, forensics and proof records — measured on
# 2026-09-30 it held no source file in any repo — and the same prefix and
# extension limits apply, so a `.py`, `.ts` or `.css` there stays code.
_DESIGN_RECORD_PREFIXES = ("console/design/", "design/", "architecture/")
_DESIGN_RECORD_SUFFIXES = (
    ".html", ".md", ".png", ".jpg", ".jpeg", ".svg", ".pen", ".json",
)
# Plain-text evidence is a record under a root `architecture/` ONLY (DRE-7330).
# dreadnought-foundry/agent-bureau#3652, DRE-5830's proof record, failed on a commit adding a
# `.md`, an `.html` and `sunday-edition-2026-10-11.txt` — the first Sunday's
# actual email, saved as evidence — because `.txt` was not a record format;
# #3675 failed the same way on a `.txt` report. An email body, a log excerpt or
# a command's output saved beside a proof record is evidence the way a
# screenshot is. The design folders do not take these: they sit beside
# application builds, so text there stays code, and so does text anywhere
# outside `architecture/`.
_ARCHITECTURE_RECORD_PREFIX = "architecture/"
_ARCHITECTURE_TEXT_RECORD_SUFFIXES = (".txt", ".log")
_OPS_PREFIXES = (".github/", "config/")
# Root-level DATA files, matched by EXACT path — never a prefix, a suffix or a
# directory, so `console/models.json` and `models.json.bak` stay code.
#
# `models.json` is the frozen Anthropic catalog snapshot the console reads, so
# agent-bureau needs no Anthropic credential: a vendor's model list, derived
# rather than authored, and refreshed by hand on an ordinary pull request. It
# classified as `code` (neither a docs path nor an ops one), which made a
# regeneration unable to satisfy a check whose finding is the ORDER of
# commits that already exist — there is no RED test to write for a list
# somebody else publishes, and DRE-2694 means no added commit clears it.
#
# The CEO decided it on 2026-09-16 (signed console answer, DRE-3879): count
# `models.json` as data beside `config/` and `agents.yaml`, and NOTHING ELSE is
# exempted. The boundary is worth saying twice, because it is the half that is
# easy to lose: this list says which FILES need a RED test committed first, and
# never whether the rule applies. A pull request that refreshes the snapshot
# AND changes a `.py` still owes its test first, and no check is skipped —
# a red check or a REQUEST_CHANGES verdict still blocks the merge.
_OPS_FILES = frozenset({"agents.yaml", "models.json"})

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

# The onboarding routing PR (DRE-7209) — the literal branch the onboarding
# process opens its routing pull requests on, here and in agent-bureau, and the
# bot that must have OPENED one (GitHub-attested, the standards-sync signal).
ONBOARD_BRANCH = "bot/onboard"
ONBOARD_BOT = "agent-bureau-bot"

# The paths a routing PR changes, in either repo. The `config/` paths are ops
# already and are listed because the card names the whole set. The five code
# paths are the slug mirrors agent-bureau's `scripts/sync_repo_mirrors.py`
# lists in `SLUG_MIRRORS`, and only their repo-mirror region is regenerated —
# `make sync-mirrors CHECK=1` in agent-bureau's CI proves that region and
# nothing else in the file, so `is_routing_mirror_change` relieves only a
# change confined to it. `scripts/validate_card.py`, the card's other path
# here, is left to the generated-region rule (`_GENERATED_REGION_PROOFS`),
# which already exempts its mirror region. Exact paths, never a prefix: a
# neighboring file is held to the rule.
ONBOARD_ROUTING_PATHS = frozenset({
    # bureau-pipeline
    "config/repo-map.json",
    # agent-bureau
    "config/pipeline-channel.json",
    "config/linear-workspace.json",
    "console/backend/sources.py",
    "cloud/relay-gh/normalize.py",
    "console/web/src/components/wizard/new-agent/repos.ts",
    "console/web/src/components/flyout/cardMeta.ts",
    "demo/demo_library.py",
})

# The markers agent-bureau's mirror generator splices between — the region its
# `--check` proves (`scripts/sync_fallback_map.py` writes the same phrases).
_BEGIN_REPO_MIRROR = "BEGIN generated repo mirror"
_END_REPO_MIRROR = "END generated repo mirror"


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


def is_onboard_routing_pr(head_ref: str | None,
                          pr_author: str | None) -> bool:
    """True iff this is an onboarding routing PR (DRE-7209): opened by the
    bot — `pr_author` from `attested_pr_author`, the one signal here the PR's
    contents cannot write — on exactly `bot/onboard`. No commit-author
    condition: what narrows this one is `mark_routing_paths`, which moves
    only the routing set out of `code` and leaves every other path on the
    branch under the rule. Fail-closed on a missing opener or branch."""
    if not pr_author or _normalized_bot_login(pr_author) != ONBOARD_BOT:
        return False
    return (head_ref or "").strip() == ONBOARD_BRANCH


def mark_routing_paths(commits) -> list[dict]:
    """Each commit record with a `routing` list: its paths in
    `ONBOARD_ROUTING_PATHS`, which `commit_categories` reads as not code when
    `is_routing_mirror_change` holds for them. Call it only for a PR
    `is_onboard_routing_pr` admits."""
    return [
        {**c, "routing": [p for p in c["paths"] if p in ONBOARD_ROUTING_PATHS]}
        for c in commits
    ]


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


def _outside_generated_regions(
    source: str, begin: str = _BEGIN_GENERATED, end: str = _END_GENERATED
) -> list[str] | None:
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
            if end in line:
                inside = False
                outside.append(line)
            continue
        outside.append(line)
        if begin in line:
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


def is_routing_mirror_change(
    path: str, before: str | None, after: str | None
) -> bool:
    """True iff `path` is a routing path (DRE-7209) whose two versions differ
    ONLY inside its `BEGIN generated repo mirror` regions — the lines
    agent-bureau's `make sync-mirrors CHECK=1` proves.

    `is_generated_region_change` with the onboarding set in place of
    `_GENERATED_REGION_PROOFS` and the repo-mirror markers in place of the
    generic ones, and the same fail-closed edges: a path outside the set, a
    missing counterpart, Python that will not parse on either side, a missing
    or unterminated region, and a line changed outside a region or on a
    marker are all False. Non-`.py` mirrors (the `.ts` ones) are compared by
    lines alone; nothing here parses them."""
    if path not in ONBOARD_ROUTING_PATHS:
        return False
    if before is None or after is None:
        return False
    if path.endswith(".py") and (
        _executable_shape(before) is None or _executable_shape(after) is None
    ):
        return False
    outside_before = _outside_generated_regions(
        before, _BEGIN_REPO_MIRROR, _END_REPO_MIRROR)
    if outside_before is None:
        return False
    outside_after = _outside_generated_regions(
        after, _BEGIN_REPO_MIRROR, _END_REPO_MIRROR)
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
    under `console/design/`, a root `design/` or a root `architecture/`
    (DRE-5311). Like `is_test_path`, this only
    ever moves a path OUT of `code`; source and stylesheets under those
    directories stay code. The extension is compared case-insensitively — a
    screenshot saved as `.PNG` is the same record as one saved as `.png`.

    Under `architecture/` only, plain text — `.txt` and `.log`, a saved email
    or a log excerpt kept as evidence — is a record too (DRE-7330); the same
    extensions under the two design folders stay code."""
    lowered = path.lower()
    if path.startswith(_ARCHITECTURE_RECORD_PREFIX) and lowered.endswith(
        _ARCHITECTURE_TEXT_RECORD_SUFFIXES
    ):
        return True
    return (
        path.startswith(_DESIGN_RECORD_PREFIXES)
        and lowered.endswith(_DESIGN_RECORD_SUFFIXES)
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


def _classify_in_commit(commit, path: str) -> str:
    """`classify_path` for one of the commit's paths, reading its `sources`
    and — on an onboarding routing PR — its `routing` list: a routing path
    the rules call `code`, changed only inside its repo-mirror region, is
    regenerated and proved elsewhere, so it is docs, the way a generated
    region is (DRE-3896, DRE-7209)."""
    sources = commit.get("sources") or {}
    before, after = sources.get(path, (None, None))
    category = classify_path(path, before, after)
    if (
        category == "code"
        and path in (commit.get("routing") or ())
        and is_routing_mirror_change(path, before, after)
    ):
        return "docs"
    return category


def commit_categories(commit) -> set[str]:
    """Every category one commit touches. A commit may carry a `sources` map
    {path: (before, after)}; a commit without one classifies by path alone."""
    return {_classify_in_commit(commit, p) for p in commit["paths"]}


class ExemptionsError(ValueError):
    """The exemption list cannot be read, or holds a shape this check refuses.
    Cannot evaluate — exit 2, never a pass."""


def parse_exemptions(text: str) -> list[dict]:
    """The exemption list's text → its entries, or ExemptionsError.

    The file is an object holding `exemptions` (a list) and optionally
    `_readme`, and nothing else. Every entry has exactly `id`, `match`,
    `reason` and `critic_checks`, each a non-empty string; `id` is a
    lower-case token and unique; `match` is one of `MATCH_KINDS`. Anything
    else is refused rather than read around, because a list this check half
    understands is one a branch could hide an exemption in."""
    try:
        raw = json.loads(text)
    except ValueError as e:
        raise ExemptionsError(f"not JSON: {e}") from None
    if not isinstance(raw, dict):
        raise ExemptionsError("the top level must be an object")
    extra = set(raw) - _TOP_KEYS
    if extra:
        raise ExemptionsError(f"unknown top-level keys: {sorted(extra)}")
    entries = raw.get("exemptions")
    if not isinstance(entries, list):
        raise ExemptionsError("`exemptions` must be a list")
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != _ENTRY_KEYS:
            raise ExemptionsError(
                f"every entry has exactly the keys {sorted(_ENTRY_KEYS)}: "
                f"{entry!r}"
            )
        for key in sorted(_ENTRY_KEYS):
            value = entry[key]
            if not isinstance(value, str) or not value.strip():
                raise ExemptionsError(f"`{key}` must be a non-empty string: "
                                      f"{entry!r}")
        if not _ID_RE.match(entry["id"]):
            raise ExemptionsError(f"`id` must be a lower-case token: "
                                  f"{entry['id']!r}")
        if entry["match"] not in MATCH_KINDS:
            raise ExemptionsError(
                f"unknown `match` {entry['match']!r} on {entry['id']!r} — "
                f"one of {', '.join(MATCH_KINDS)}"
            )
        if entry["id"] in seen:
            raise ExemptionsError(f"duplicate id {entry['id']!r}")
        seen.add(entry["id"])
    return entries


def _code_paths(commit) -> list[str]:
    return [
        p for p in commit["paths"] if _classify_in_commit(commit, p) == "code"
    ]


def is_deletion_only(commit) -> bool:
    """True iff every code path in the commit adds zero lines.

    `added` is `git diff-tree --numstat`'s first column per path, None for a
    binary path (numstat prints `-`), and a binary path is never a deletion.
    A commit record with no `added` map is never one either — fail-closed.
    Lines added to a docs, ops or test path beside the deletion do not count:
    the rule is about the code paths."""
    added = commit.get("added") or {}
    paths = _code_paths(commit)
    return bool(paths) and all(
        type(added.get(p)) is int and added[p] == 0 for p in paths
    )


def judge_commits(commits, exemptions) -> list[dict]:
    """The per-change rule over an OLDEST-FIRST list of commit records.

    One judgement per commit: `commit`, `code` (is it a code commit), `ok`,
    `note` (which reason applied, for a code commit — what its classification
    line prints), `previous` (the code commit before it, None for the first)
    and `failure` (the headline it fails under, None when it passes).

    A record may carry `claims` (its `TDD-Exempt:` trailer values) and
    `added` (see `is_deletion_only`); one without either claims nothing and is
    never a deletion."""
    claimable = [e["id"] for e in exemptions if e["match"] == MATCH_TRAILER]
    detector = next(
        (e["id"] for e in exemptions if e["match"] == MATCH_DELETION), None
    )
    judged = []
    previous = None
    tested = False
    for c in commits:
        cats = commit_categories(c)
        if "code" not in cats:
            if "test" in cats:
                tested = True
            judged.append({"commit": c, "code": False, "ok": True,
                           "note": None, "previous": None, "failure": None})
            continue
        claims = list(c.get("claims") or [])
        unknown = [x for x in claims if x not in claimable]
        failure = None
        if unknown:
            failure = UNKNOWN_CLAIM_MESSAGE
            note = (f"FAILS: claims {', '.join(unknown)}, which the list "
                    f"does not offer — the ids a commit may claim are: "
                    f"{', '.join(claimable) or 'none'}")
        elif tested:
            note = ("test commit since the branch began" if previous is None
                    else "test commit since the previous code commit")
        elif previous is None:
            failure = FAILURE_MESSAGE
            note = "FAILS: no test commit since the branch began"
        elif detector and is_deletion_only(c):
            note = f"detected: {detector}"
        elif claims:
            note = f"claimed: {', '.join(claims)}"
        else:
            failure = LATER_FAILURE_MESSAGE
            note = ("FAILS: no test commit since the previous code commit "
                    f"{previous['sha'][:7]}")
        judged.append({"commit": c, "code": True, "ok": failure is None,
                       "note": note, "previous": previous,
                       "failure": failure})
        previous = c
        tested = False
    return judged


def check_commits(commits, exemptions=()) -> tuple[bool, str]:
    """The per-change rule (`RULE`) over an OLDEST-FIRST list of commit
    records (dicts with `sha`, `subject`, `paths`). Returns (ok, reason); a
    failure's reason is the headline of the FIRST failing commit — the
    per-commit detail is `failure_report`'s."""
    judged = judge_commits(commits, exemptions)
    if not any(j["code"] for j in judged):
        return True, "exempt: no non-test code changed (docs/ops/tests only)"
    failed = [j for j in judged if not j["ok"]]
    if failed:
        return False, failed[0]["failure"]
    return True, ("every code commit has a test commit since the previous "
                  "code commit, or an exemption")


def check_floor(commits) -> tuple[bool, str]:
    """The once-per-branch floor every branch was judged by before DRE-3009,
    and the one a branch whose merge base has no exemption list is still
    judged by: a test commit strictly before the first code commit. No claim
    is honored and no deletion detected."""
    first_code = next(
        (i for i, c in enumerate(commits) if "code" in commit_categories(c)),
        None,
    )
    if first_code is None:
        return True, "exempt: no non-test code changed (docs/ops/tests only)"
    if any("test" in commit_categories(c) for c in commits[:first_code]):
        return True, "a test commit precedes the first implementation commit"
    return False, FAILURE_MESSAGE


def _named(commit) -> str:
    return f"{commit['sha'][:7]} \"{commit['subject']}\""


def failure_report(judged, exemptions) -> str:
    """What a red run prints: each failing commit, the code commit it needed a
    test after, and every exemption it could have claimed as the exact
    trailer line to add. A gate that says "no" without "or one of these" is
    what produces disputes."""
    lines = []
    for j in judged:
        if j["ok"]:
            continue
        c = j["commit"]
        if j["failure"] == UNKNOWN_CLAIM_MESSAGE:
            lines.append(f"{_named(c)}: {j['note'][len('FAILS: '):]}")
        elif j["previous"] is None:
            lines.append(
                f"{_named(c)}: {FAILURE_MESSAGE}. It is the first code "
                "commit, so it needs a test commit since the branch began, "
                "and no exemption covers it."
            )
        else:
            lines.append(
                f"{_named(c)}: no test commit since the previous code commit "
                f"{_named(j['previous'])}."
            )
    lines.append("")
    lines.append(
        "A code commit after the first may instead claim one of these, as a "
        "trailer — the exact line, last in its commit message:"
    )
    for e in exemptions:
        if e["match"] == MATCH_TRAILER:
            lines.append(f"  {TRAILER_KEY}: {e['id']}    — {e['reason']}")
    for e in exemptions:
        if e["match"] == MATCH_DELETION:
            lines.append(f"  {e['id']} is detected, not claimed — {e['reason']}")
    lines.append(
        "A missing claim is cleared the way a wrong order is: by rewriting "
        "the branch — here, rewording that one commit's message (an "
        "interactive rebase, `reword`) and force-pushing. Adding a commit "
        "cannot clear it. The critic reads every claimed commit's diff "
        f"against that entry's `critic_checks` in {EXEMPTIONS_FILE}."
    )
    return "\n".join(lines)


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
    the file across the commit, so the AST-equivalence rule can see them,
    and likewise for any routing path (DRE-7209), `.ts` included, so its
    mirror region can be compared.
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
            if (p.endswith(".py") or p in ONBOARD_ROUTING_PATHS)
            and classify_path(p) == "code"
        }
        claims = _git(
            "log", "-1",
            f"--format=%(trailers:key={TRAILER_KEY},valueonly)", sha,
        ).splitlines()
        added = {}
        for line in _git(
            "diff-tree", "--no-commit-id", "--numstat", "-r", sha
        ).splitlines():
            count, _deleted, path = line.split("\t", 2)
            # `-` is a binary path: no line count, so never a deletion.
            added[path] = int(count) if count.isdigit() else None
        commits.append({
            "sha": sha,
            "subject": subject,
            "author": author,
            "paths": paths,
            "sources": sources,
            "claims": [v.strip() for v in claims if v.strip()],
            "added": added,
        })
    return commits


def _toplevel(directory: str | None = None) -> str | None:
    """The root of the git repository holding `directory` (the working
    directory when None), or None when it is not inside one."""
    p = subprocess.run(
        ["git", "-C", directory or ".", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True,
    )
    if p.returncode != 0 or not p.stdout.strip():
        return None
    return os.path.realpath(p.stdout.strip())


def runs_from_repo_under_check() -> bool:
    """True iff this script sits in the repository it is checking — the root
    of the repository its own file is in equals the root of the one the
    working directory is in. That is bureau-pipeline's own `tdd` job (and a
    local run in a bureau-pipeline checkout). Every other caller runs it from
    somewhere the pull request cannot edit: a product repo's
    `.bureau-pipeline/` checkout (its own repository), the shared action's
    fetched copy (no `.git` at all) and the critic's `$PIPELINE_DIR`."""
    here = _toplevel(os.path.dirname(os.path.abspath(__file__)))
    return here is not None and here == _toplevel()


def load_exemptions(base: str, head: str) -> tuple[list[dict] | None, str]:
    """(entries, the line saying where they came from), or (None, the line
    saying why the once-per-branch floor applies).

    Raises ExemptionsError for a list that is present but cannot be used, and
    CalledProcessError when git cannot compute the merge base — both exit 2."""
    if runs_from_repo_under_check():
        merge_base = _git("merge-base", base, head).strip()
        listed = _git("ls-tree", "--name-only", merge_base, "--",
                      EXEMPTIONS_FILE).strip()
        if not listed:
            return None, (
                f"{EXEMPTIONS_FILE} did not exist at the merge base "
                f"{merge_base[:7]}, so this branch is judged by the "
                "once-per-branch floor it started under: a test commit "
                "strictly before the first code commit, no claim honored and "
                "no deletion detected. Merging the default branch in, or "
                "rebasing onto it, moves the branch onto the per-change rule."
            )
        try:
            text = _git("show", f"{merge_base}:{EXEMPTIONS_FILE}")
        except UnicodeDecodeError as e:
            raise ExemptionsError(f"not UTF-8: {e}") from None
        return parse_exemptions(text), (
            f"exemptions: {EXEMPTIONS_FILE} as it stood at the merge base "
            f"{merge_base[:7]} — a branch is judged by the list it started "
            "from"
        )
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        EXEMPTIONS_FILE,
    )
    # The copy beside the script ships in the same commit as the script, so
    # a missing one is a broken checkout — never the floor.
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except (OSError, UnicodeDecodeError) as e:
        raise ExemptionsError(f"cannot read {path}: {e}") from None
    return parse_exemptions(text), (
        f"exemptions: {path}, the copy beside this script"
    )


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
    def line(c):
        cats = sorted(commit_categories(c)) or ["empty"]
        return f"{c['sha'][:7]} [{','.join(cats)}] {c['subject']}"

    # The branch: explicit when the caller threads it, else GitHub's own
    # pull_request variable, so a fleet repo running this from
    # `.bureau-pipeline/scripts/` gets the exemption without a workflow edit.
    # The opener is read the same way — threaded, else from the event payload.
    head_ref = os.environ.get("HEAD_REF") or os.environ.get("GITHUB_HEAD_REF")
    opener = attested_pr_author()
    if is_standards_sync_pr(head_ref, opener,
                            [c.get("author") for c in commits]):
        for c in commits:
            print(line(c))
        print(f"exempt: the nightly standards-sync PR — opened by "
              f"{STANDARDS_SYNC_BOT}[bot] (GitHub-attested), on branch "
              f"{STANDARDS_SYNC_BRANCH}, every commit authored by that bot; "
              f"generated manifests with no behaviour of their own to "
              f"RED-test")
        return 0
    if is_onboard_routing_pr(head_ref, opener):
        # Not an exemption of the PR: only a routing path's mirror region
        # leaves `code`, and everything else is judged below as usual.
        commits = mark_routing_paths(commits)
        print(f"routing: the onboarding routing PR — opened by "
              f"{ONBOARD_BOT}[bot] (GitHub-attested), on branch "
              f"{ONBOARD_BRANCH}; a routing path changed only inside its "
              f"generated repo-mirror region is not code here, and any "
              f"other line or path is held to the rule")
    try:
        exemptions, source = load_exemptions(base, head)
    except subprocess.CalledProcessError as e:
        print(f"git failed: {e.stderr.strip()}", file=sys.stderr)
        return 2
    except ExemptionsError as e:
        # A list that is present but unusable is never read as "no list".
        print(f"cannot evaluate: {EXEMPTIONS_FILE}: {e}", file=sys.stderr)
        return 2
    print(source)
    if exemptions is None:
        for c in commits:
            print(line(c))
        ok, reason = check_floor(commits)
        print(reason)
        report = None
    else:
        print(f"rule: {RULE}")
        judged = judge_commits(commits, exemptions)
        for j in judged:
            print(line(j["commit"]) + (f" — {j['note']}" if j["code"] else ""))
        ok, reason = check_commits(commits, exemptions)
        print(reason)
        report = None if ok else failure_report(judged, exemptions)
    if not ok:
        if report:
            print(report)
        # DRE-2694: tell the writer, here at CI time, that adding a commit
        # cannot clear this — the cheapest place the discipline is knowable.
        entry = unfixable_checks.match(unfixable_checks.TDD_CHECK_NAME)
        print(unfixable_checks.remedy_block(entry))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
