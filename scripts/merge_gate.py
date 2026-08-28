#!/usr/bin/env python3
"""The merge gate's DECISION — the fleet's single highest-privilege call.

Extracted from inline shell in .github/workflows/merge-gate.yml (DRE-1992);
the pre-extraction shell is frozen at tests/fixtures/merge-gate.ba4305d.yml
and tests/test_merge_gate_decision_table.py proves this module reproduces
its decisions case-for-case. The workflow is now a thin caller: it gathers
the inputs from GitHub's own records and acts on this module's verdict —
no agent claims trusted, no human in the loop.

The conditions (all must pass), evaluated 0 → D → 1 → 2 → 3.

FRESHNESS IS NOT A GATE (DRE-2416, CEO decision 2026-08-20 recorded on
DRE-2597; the rule lives in agent-bureau's
`architecture/decisions/adr-one-writer-per-fact.md`). The fleet does not
require up-to-date branches, and THIS GATE is the single writer of that
rule — not branch protection, which cannot hold it fleet-wide (the
reference deployment `EveryBite/atlas` returns 403 on its protection
endpoint) and which reads `required_status_checks.strict=false` on every
repo checked anyway. Condition 0 used to be BRANCH CURRENCY (DRE-1924) and
returned `update`, which re-merged the base into the branch; the new head
restarted the full CI suite, and on a busy repo the base moved again
before that suite finished. Portico PR 268 (DRE-2393, 2026-08-12) burned
four full CI runs in thirteen minutes on unchanged source, all green, and
still read BLOCKED — one restart per merge to main, on every open branch.
DRE-2274 (currency evaluated last) had already cut that to one restart per
readiness cycle; this card removes the restart for the case that never
needed it. Condition 0 is now CONFLICT: the only state a re-merge can
actually change.

THE ACCEPTED COST, named rather than left to be discovered: two
individually-green PRs can break the base together — the `asana` class
DRE-1924 existed for. It is caught in minutes by the push-to-main run
going red plus medic.yml filing a repair card
(`architecture/decisions/adr-red-main-auto-repair.md`), and made rare by
the disjoint-file-ownership rule. Nothing else about the gate relaxes: red
CI, a missing verdict, a stale verdict and a standing REQUEST_CHANGES all
still block exactly as before.

D. DEPENDABOT POLICY (DRE-2039) — applies ONLY to `dependabot/**`
   branches; agent/repair branches skip straight to condition 0 even if
   their commit messages happen to contain update-type strings. Branch
   names are attacker-choosable, so the PR author must be the literal
   `dependabot[bot]` (GitHub-reserved suffix — unforgeable); anything else
   on a dependabot-named branch is `human`. The semver level is proven
   DETERMINISTICALLY from Dependabot's own machine-readable commit
   metadata — the `update-type: version-update:semver-<level>` lines it
   embeds in every version-update commit message (the signal the official
   dependabot/fetch-metadata action parses; grouped PRs list one entry per
   updated dependency). EVERY update must be semver-minor or semver-patch
   to proceed to conditions 0-3 (currency, CI, bound critic APPROVE and
   the verifier all still gate — this condition never merges by itself).
   Any semver-major — or NO provable level at all — is `human`: majors
   are a person's call, the gate posts the honest "waiting for human
   merge" state once and does nothing (no update-branch either). An empty
   commit record (listing-API blip → the workflow substitutes `[]`) is
   `wait`, fail-closed — never judge the semver level on unverifiable
   data, and never post the human state over a blip.

0. CONFLICT (DRE-2416) — GitHub's own `mergeStateStatus` for the PR. The
   literal `DIRTY` (a textual merge conflict) is `conflict`: the branch
   must be reconciled with its base before any answer can change, and
   `update-branch` cannot resolve a conflict — the FIX AGENT can, so the
   workflow dispatches it. Every other state proceeds; in particular
   `UNKNOWN` is GitHub still computing mergeability lazily (DRE-2121,
   which owns the re-read poll in reconcile), never a conflict — treating
   it as one would reintroduce the indefinite stall this card removes.
   This is a faithful move of the shell's `[ "$MSTATE" = "DIRTY" ]` arm
   into the tested decision, evaluated FIRST so it keeps the position it
   held in the shell: a conflicted branch reached the fix agent whatever
   its CI or verdict state, and still does.

   The branch-CURRENCY record (GET compare/{base}...{head_sha}, `.status`)
   is still read, but it no longer gates anything — a behind/diverged head
   merges like a current one, and the status is emitted as a `note=` so
   the run log says plainly that a behind-base head was merged on purpose.
   An unverifiable status is no longer `wait`: with currency out of the
   rule set, waiting on a record that decides nothing is pure starvation.
   The fail-closed direction that DOES still matter for that payload is
   the CONTENT id (DRE-2340): a blipped or truncated record yields no id,
   no carry, and a stale verdict then needs a fresh review.

1. CI — every check run on the PR's head SHA has completed green
   (conclusion success/skipped/neutral). The REVIEW workflow's own check
   runs are EXCLUDED: the critic's verdict COMMENT is the review's source
   of truth (condition 2), and a review run killed by an API blip must not
   deadlock the merge. Exclusion is by VERIFIED ORIGIN, never by name
   (DRE-1994): the old `endswith("review")` name test was attacker-nameable
   — check names come from PR-authored workflow files, so a failing job
   named `sneaky-review` was invisible to the all-green rule. Now a check
   run is excluded only if its check suite belongs to a workflow run that
   GitHub's own workflow-runs record attributes to an allowlisted review
   workflow FILE (path). GitHub gives every workflow run its own check
   suite, so a PR-authored workflow — whatever its jobs are named — can
   never place a check run inside the review workflow's suite. Residual
   (documented in tests/test_merge_gate_check_origin.py): a PR modifying
   the review stub at its own path still gets excluded — exactly the run
   class the exclusion targets, and exclusion grants no approval power
   (condition 2 still gates). No counted runs at all → wait (checks
   haven't reported yet).

2. QA Critic — the latest critic verdict comment is APPROVE, bound to the
   PR's current head:
   - AUTHORSHIP (DRE-1987 / #57): only comments authored by the qa-bot App
     count. GitHub reserves the "[bot]" suffix, so no user account can
     impersonate it; the workflow derives the login from the same App key
     it merges with (app-slug of the minted token).
   - SHA BINDING (DRE-1990 / #60): qa-review.yml embeds the reviewed
     commit on the verdict line (`VERDICT: <X> @<full-sha>`). A verdict
     whose SHA is MISSING (pre-DRE-1990, or the neutral could-not-run
     status) or STALE (≠ the current head) is NO verdict — fail-closed,
     the gate waits for a fresh review. Code pushed after a genuine
     APPROVE must not ride that approval into main (PRs #13/#25 did).
   - CONTENT BINDING (DRE-2340): a verdict whose SHA is stale STILL binds
     when the PR's own contribution is provably unchanged — see the note
     below. Everything else about the SHA arm is untouched.
   - The SHA check runs BEFORE the APPROVE check, so a stale
     REQUEST_CHANGES reads as "no verdict — wait", not "hold".

CONTENT BINDING (DRE-2340) — what a branch update may NOT destroy.
The old conditions 0 and 2 were each right and together they livelocked:
the gate updated a stale branch, the update moved the head, the head move
unbound the verdict the gate was waiting for, and the critic re-reviewed a
branch that was stale again before it finished. Portico PR #205
(2026-08-09) earned seven APPROVE verdicts in 47 minutes and threw six
away — six complete, fully paid-for reviews — because `main` took 30
commits from other work in that window. DRE-2274 (currency evaluated last)
converted that waste; DRE-2416 removed the gate-initiated update that
caused it. The carry still matters: a head moves whenever the FIX AGENT
reconciles a conflicted branch, and that merge must not throw away a
verdict for a diff it did not touch.

So a verdict now also binds the CONTENT it was earned against: the sha256
of the three-dot compare record's `files[]` (scripts/verdict_content.py,
which documents the two properties this rests on). When the verdict's SHA
is no longer the head, it binds ONLY when ALL of:

  1. the verdict line carries a `content:<64-hex>` id, and
  2. the gate computed a non-None id for the CURRENT head (from the same
     compare payload condition 0 reads — no extra API call, and no chance
     of being handed an id for a different head), and
  3. the two ids are equal, and
  4. the verdict's SHA appears in the PR's own commit record
     (--pr-commits-file) — proof the reviewed commit is still in THIS PR's
     history and was not rewritten away. Record unavailable → no carry.

Anything else is today's `wait` (critic) / `hold` (verifier), unchanged.
The guarantee is intact: you cannot add code without changing content, so
any change to the PR's own diff kills the verdict; the id comes from
GitHub's own record, not from anything the author writes; authorship still
filters first. The one thing given up, stated plainly: after a clean base
merge the critic no longer re-reads its own unchanged diff in the light of
the new base. That residual is bounded — every commit on the base carried
its own bound APPROVE, and interaction risk is what CI on the base branch
exists to catch (DRE-1924's own motivating `asana` incident was a CI catch,
not a critic catch).

3. QA Verifier — scope-gated stage; it may simply never have run:
   - ABSENT verdict → not a gate (falls through).
   - PRESENT verdict proves the PR is in Verifier scope, so a MISSING or
     STALE SHA must HOLD for a fresh verify (DRE-1990 asymmetry — treating
     it as absent would fail OPEN and merge code the Verifier never ran).
   - Bound to the current head: PASS proceeds; SKIP is advisory and
     proceeds too (DRE-1991 / #61 — the Verifier brief promises a SKIP
     never blocks); anything else (FAIL, neutral) holds.
   - Same authorship rule as the critic: a forged FAIL could stall merges,
     a forged PASS could mask a real FAIL.

STRUCTURED / ANCHORED verdict parsing (DRE-1992 scope note, 2026-07-09):
a comment merely QUOTING a verdict marker must not count as one. A comment
is a verdict comment only if its FIRST LINE starts with the marker
(optionally preceded by the producer's emoji — never by a quote prefix like
"> "), and the verdict token only counts in the structured position the
producers emit: `<marker> — VERDICT: <TOKEN> … @<sha>`. The old shell's
contains()/glob matching could be satisfied by quotation or prose; the
four sanctioned differences are documented as delta rows in
tests/test_merge_gate_decision_table.py.

Contract with merge-gate.yml:
  stdin/argv: --head-sha, --qa-login, --check-runs-file (the raw REST
    payload of GET /repos/{repo}/commits/{sha}/check-runs), --comments-file
    (the raw REST payload of GET /repos/{repo}/issues/{pr}/comments),
    --workflow-runs-file (the raw REST payload of
    GET /repos/{repo}/actions/runs?head_sha={sha} — the verified-origin
    record for the review-run exclusion), --compare-file (the raw payload
    of GET /repos/{repo}/compare/{base}...{head_sha} — the content-binding
    record, DRE-2340; its `.status` is reported as a note and gates
    nothing, DRE-2416), --merge-state (GitHub's own `mergeStateStatus` for
    the PR — the conflict record, DRE-2416), --review-workflows (optional
    comma-separated allowlist of review workflow paths), --head-branch /
    --pr-author / --pr-commits-file (the raw REST payload of GET
    pulls/{pr}/commits) — the dependabot-policy record (DRE-2039) AND the
    content-binding commit record (DRE-2340 condition 4), all optional;
    omitted = the pre-DRE-2039/2416 behavior for every caller that never
    passes them. The compare payload must NOT be trimmed (DRE-2340): its
    `files[]` is what the head's content id is computed from.
  stdout: zero or more `note=` lines, then exactly one `decision=` line
    (merge | conflict | wait | hold | human) and one `reason=` line (plain
    English), then — only when a verdict was honoured across a head change
    — a `carried=` line naming the reviewed commits and a
    `carried_content_id=` line, which the workflow turns into the PR
    comment that explains the carry.
  exit 0 = decided; exit 2 = malformed input (the job fails loudly and
    nothing merges — never fail open).

wait vs hold vs conflict vs human: `wait` means the gate expects a future
event to change the answer (CI finishing, a fresh review of the current
head); `hold` means an explicit negative verdict is standing
(REQUEST_CHANGES, Verifier FAIL) and only a new verdict lifts it;
`conflict` means the branch cannot merge until it is reconciled with its
base (DRE-2416) and the workflow dispatches the fix agent; `human` means
the gate will NEVER merge this PR (a dependabot major / unprovable semver
level — DRE-2039): the workflow posts that state once and stops. None of
the four merges.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from typing import Optional

# The verdict↔content binding lives in ONE module (DRE-2340, trap 4):
# should_review_pr and reconcile read it from here too, so the gate, the
# review-skip and the sweep can never drift about what a verdict binds.
from verdict_content import content_id, verdict_content_id  # noqa: F401

CRITIC_MARKER = "QA Critic"
VERIFIER_MARKER = "QA Verifier"

# Workflow FILES whose runs are the review stage — their check runs are
# excluded from condition 1 by verified origin (DRE-1994). Paths as GitHub
# records them on the workflow RUN, not names a PR can choose.
DEFAULT_REVIEW_WORKFLOWS = (
    ".github/workflows/qa-review.yml",  # the product-repo critic stub
    ".github/workflows/pr-review.yml",  # bureau-pipeline's own critic
)

# Green = completed with a conclusion GitHub treats as non-blocking.
GREEN_CONCLUSIONS = frozenset({"success", "skipped", "neutral"})

# GitHub compare/{base}...{head} status values: the head is current when it
# contains the base's tip, behind when the base has commits the head lacks.
# Since DRE-2416 this decides NOTHING — it only shapes the note that records
# a behind-base merge in the run log.
CURRENT_STATUSES = frozenset({"ahead", "identical"})
STALE_STATUSES = frozenset({"behind", "diverged"})

# GitHub's `mergeStateStatus` for a textual merge conflict (DRE-2416). The
# other states (CLEAN / UNSTABLE / BLOCKED / BEHIND / HAS_HOOKS / DRAFT /
# UNKNOWN) are not conflicts and never route to the fix agent — matching the
# shell arm this replaced, which tested this one literal.
CONFLICTING_MERGE_STATE = "DIRTY"

# Dependabot policy (DRE-2039). The author check anchors the leniency to
# the real Dependabot App — GitHub reserves the "[bot]" suffix, so no user
# account can wear this login; branch names, by contrast, are free text.
DEPENDABOT_BRANCH_PREFIX = "dependabot/"
DEPENDABOT_LOGIN = "dependabot[bot]"

# Dependabot's machine-readable semver signal: one `update-type:` line per
# updated dependency in the commit-message trailer (grouped PRs list them
# all) — the same record dependabot/fetch-metadata parses. Level captured
# permissively so an unknown future level is SEEN (and refused as
# not-provably-safe) rather than invisible.
_UPDATE_TYPE_RE = re.compile(
    r"^\s*update-type:\s*[\"']?version-update:semver-([a-z]+)[\"']?\s*$", re.M
)
MERGEABLE_UPDATE_TYPES = frozenset({"minor", "patch"})

# A full 40-hex SHA anywhere on the verdict line (`@<sha>`), as the
# producers append it. Abbreviated SHAs deliberately do not bind.
_SHA_RE = re.compile(r"@([0-9a-f]{40})")

# Anchor: the marker must OPEN the first line, allowing only the producer's
# short emoji/badge prefix ("🔎 ", "🧪 ") — never a markdown quote (">") or
# leading prose. This is what makes quoting a verdict inert.
_ANCHOR = r"^\s*(?:[^\w\s>]{1,4}\s+)?"
_HEAD_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _marker_re(marker: str) -> re.Pattern:
    return re.compile(_ANCHOR + re.escape(marker) + r"\b")


def _verdict_re(marker: str) -> re.Pattern:
    # The structured position the producers emit:
    #   { echo "🔎 QA Critic — $(head -1 verdict.md) @${REVIEWED_SHA}"; … }
    # i.e. `<marker> — VERDICT: <TOKEN>` (em-dash), optional trailing prose.
    return re.compile(_ANCHOR + re.escape(marker) + r"\s+—\s+VERDICT:\s*([A-Z_]+)")


@dataclass
class Decision:
    action: str  # merge | update | wait | hold | human
    reason: str
    notes: list = field(default_factory=list)
    #: Verdicts honoured across a head change (DRE-2340), as
    #: "critic@<reviewed-sha>" / "verifier@<reviewed-sha>". Empty on every
    #: ordinary decision. The workflow posts a PR comment when it is not —
    #: today `decision=update` posts nothing at all, which is why PR #205's
    #: six merge commits look inexplicable on the timeline.
    carried: list = field(default_factory=list)
    #: The head content id the carry was proved against; None when nothing
    #: was carried.
    content_id: Optional[str] = None


def first_line(body: Optional[str]) -> str:
    body = body or ""
    return body.splitlines()[0] if body else ""


def latest_verdict_comment(comments, qa_login: str, marker: str) -> Optional[str]:
    """Body of the LATEST comment that (a) is authored by the qa-bot App and
    (b) opens with the marker on its first line. None if no such comment —
    forged, human, deleted-account, and quoting/prose comments are invisible,
    not merely non-approving."""
    rx = _marker_re(marker)
    latest = None
    for c in comments:
        user = c.get("user") or {}
        if user.get("login") != qa_login:
            continue
        if not rx.match(first_line(c.get("body"))):
            continue
        latest = c.get("body") or ""
    return latest


def verdict_sha(line: str) -> Optional[str]:
    m = _SHA_RE.search(line)
    return m.group(1) if m else None


def verdict_token(line: str, marker: str) -> Optional[str]:
    """The structured verdict token (APPROVE / REQUEST_CHANGES / PASS /
    FAIL / SKIP), or None when the line carries no structured verdict
    (neutral could-not-run status, prose)."""
    m = _verdict_re(marker).match(line)
    return m.group(1) if m else None


def review_suite_ids(workflow_runs, review_workflows) -> frozenset:
    """Check-suite ids of the runs produced by the review workflow files —
    the verified-origin record (DRE-1994). GitHub attributes every workflow
    run to its workflow FILE (`path`) and gives it its own check suite
    (`check_suite_id`), so a check run ties back to its producing file by
    suite membership; the names a PR chooses for its jobs never enter into
    it. Runs without a suite id never match (a suite-less check run must
    not be excludable via a None match)."""
    return frozenset(
        r["check_suite_id"]
        for r in workflow_runs
        if r.get("path") in review_workflows
        and r.get("check_suite_id") is not None
    )


def dependabot_update_types(commits) -> list:
    """Every semver level named in the PR's commit messages (Dependabot's
    `update-type: version-update:semver-<level>` trailer lines), in order.
    Merge commits (e.g. the gate's own update-branch, DRE-1924) carry no
    trailer and contribute nothing."""
    levels = []
    for c in commits:
        message = ((c.get("commit") or {}).get("message")) or ""
        levels.extend(_UPDATE_TYPE_RE.findall(message))
    return levels


def evaluate_dependabot(head_branch, pr_author, commits) -> Optional[Decision]:
    """Condition D (DRE-2039). None = not a dependabot branch, or a genuine
    Dependabot PR whose EVERY update is provably semver-minor/patch —
    proceed to conditions 0-3 (which all still gate; this never merges by
    itself). `human` = the gate will never merge this PR (major /
    unprovable level / not really Dependabot); the workflow posts the
    honest waiting-for-human state once and stops — no update-branch, no
    fix-agent, nothing."""
    if not (head_branch or "").startswith(DEPENDABOT_BRANCH_PREFIX):
        return None
    if pr_author != DEPENDABOT_LOGIN:
        return Decision(
            "human",
            f"branch {head_branch!r} is dependabot-named but the PR author "
            f"is {pr_author!r}, not {DEPENDABOT_LOGIN} — the gate only "
            "auto-merges genuine Dependabot PRs; waiting for human merge",
        )
    if not commits:
        return Decision(
            "wait",
            "no commit record for the dependabot PR (listing blip?) — wait; "
            "never judge the semver level on unverifiable data",
        )
    levels = dependabot_update_types(commits)
    if not levels:
        return Decision(
            "human",
            "dependabot PR carries no machine-readable update-type metadata "
            "— cannot prove it is minor/patch-only; waiting for human merge",
        )
    unsafe = sorted({lv for lv in levels if lv not in MERGEABLE_UPDATE_TYPES})
    if unsafe:
        return Decision(
            "human",
            f"dependabot PR includes a semver-{'/'.join(unsafe)} update — "
            "major version bumps are a human decision; waiting for human merge",
        )
    return None


def evaluate_conflict(merge_state) -> Optional[Decision]:
    """Condition 0 (DRE-2416). None = the branch does not conflict with its
    base, proceed. `DIRTY` → `conflict`: only reconciling the branch can
    change the answer, and `update-branch` cannot resolve a textual
    conflict, so the workflow dispatches the fix agent.

    Evaluated FIRST, keeping the position the shell arm held: a conflicted
    branch reached the fix agent whatever its CI or verdict state. Every
    other state — including `UNKNOWN`, which is GitHub computing
    mergeability lazily (DRE-2121) rather than a conflict — proceeds; the
    default ('' — nothing passed) reproduces the pre-DRE-2416 behavior on
    this axis for every caller that never passes it.
    """
    if (merge_state or "").upper() == CONFLICTING_MERGE_STATE:
        return Decision(
            "conflict",
            "the branch has a merge conflict with its base — update-branch "
            "cannot resolve it; the fix agent reconciles the branch and a "
            "fresh review binds the result",
        )
    return None


def currency_note(compare_status) -> Optional[str]:
    """The audit line for a head that is behind its base (DRE-2416).

    Nobody in this pipeline reads diffs, so a merge that GitHub's own
    compare record calls `behind` has to say so in the run log. It is a
    NOTE, never a decision: the fleet does not require up-to-date branches
    and this gate is the single writer of that rule. None when the head is
    current (nothing to record) or the status is unverifiable (nothing
    provable to say).
    """
    if compare_status in STALE_STATUSES:
        return (
            f"head is {compare_status} relative to its base — merged as it "
            "stands: the fleet does not require up-to-date branches "
            "(DRE-2416), and CI on the base branch is what catches a "
            "green-alone-red-together interaction"
        )
    return None


def evaluate_checks(check_runs, review_suites=frozenset()) -> Optional[Decision]:
    """Condition 1. None = green, proceed. Only check runs sitting in a
    verified review workflow's check suite are excluded — an empty origin
    record (listing API blip) excludes nothing and the gate waits,
    fail-closed."""
    counted = [
        r
        for r in check_runs
        if (r.get("check_suite") or {}).get("id") not in review_suites
    ]
    total = len(counted)
    if total == 0:
        return Decision("wait", "no checks reported yet — wait")
    not_green = [
        r
        for r in counted
        if r.get("status") != "completed"
        or (r.get("conclusion") or "") not in GREEN_CONCLUSIONS
    ]
    if not_green:
        return Decision(
            "wait", f"{len(not_green)} of {total} check runs not green — wait"
        )
    return None


def commit_shas(pr_commits) -> frozenset:
    """The sha set of a `GET pulls/{pr}/commits` payload — the carry's
    condition 4 record.

    One implementation (DRE-2340, trap 4): the gate, the review-skip
    (should_review_pr) and the review-lane sweep (reconcile) all read the same
    payload shape, and a hand-rolled second copy is exactly how the three
    drifted into disagreeing about the same state. A blip substitute (`[]`),
    a payload that is not a list at all, or a shapeless entry contributes
    nothing, so the set comes out empty and carries_content refuses — fail
    closed, never a crash on the decision path.
    """
    if not isinstance(pr_commits, (list, tuple)):
        return frozenset()
    return frozenset(
        c.get("sha") for c in pr_commits
        if isinstance(c, dict) and c.get("sha")
    )


def carries_content(
    line: str, sha: str, head_content_id: Optional[str], pr_commit_shas
) -> bool:
    """DRE-2340: does a verdict for an OLDER commit still bind this head?

    True only when all four conditions hold (see the module docstring):
    the line carries a content id, the gate computed one for the current
    head, they are equal, and the reviewed commit is still in this PR's own
    commit record. Every unprovable case is False — the caller then takes
    today's stale-verdict path, unchanged.
    """
    if not head_content_id:
        return False  # truncated / blipped compare record → SHA binding only
    carried = verdict_content_id(line)
    if not carried or carried != head_content_id:
        return False
    if not pr_commit_shas:
        return False  # `[]` blip substitute — never carry on unverifiable data
    return sha in pr_commit_shas


def evaluate_critic(
    line: str,
    head_sha: str,
    head_content_id: Optional[str] = None,
    pr_commit_shas=frozenset(),
) -> Optional[Decision]:
    """Condition 2, given the first line of the latest counted critic
    comment ('' if none). None = APPROVE bound to head, proceed.

    The `sha == head_sha` path is unchanged, byte for byte — it is the
    common path. `head_content_id` / `pr_commit_shas` default to the
    no-content-binding case, which reproduces the pre-DRE-2340 behavior
    exactly for every caller that never passes them.
    """
    if not line:
        return Decision("wait", "no critic verdict yet — wait")
    sha = verdict_sha(line)
    if sha is None:
        return Decision(
            "wait",
            "critic verdict names no reviewed commit (pre-DRE-1990 format or "
            f"neutral status) — treated as NO verdict; waiting for a fresh "
            f"review of {head_sha}",
        )
    if sha != head_sha and not carries_content(
        line, sha, head_content_id, pr_commit_shas
    ):
        return Decision(
            "wait",
            f"critic verdict is for {sha} but head is now {head_sha} — stale; "
            "treated as NO verdict, waiting for a fresh review",
        )
    if verdict_token(line, CRITIC_MARKER) != "APPROVE":
        return Decision("hold", "latest verdict is not APPROVE — holding")
    return None


def evaluate_verifier(
    line: str,
    head_sha: str,
    head_content_id: Optional[str] = None,
    pr_commit_shas=frozenset(),
) -> tuple[Optional[Decision], str]:
    """Condition 3, given the first line of the latest counted verifier
    comment ('' if none). Returns (decision-or-None, advisory note);
    None = not a gate / satisfied, proceed.

    Carries exactly like the critic (DRE-2340, trap 1: carrying only the
    critic would trade the livelock for a VERIFIER DEADLOCK on any repo in
    verifier scope — a present-but-stale verifier verdict HOLDs, and a hold
    is not lifted by a fresh gate wake). The present-but-stale HOLD
    asymmetry is preserved for every case where the ids do not match.
    """
    if not line:
        return None, "no verifier verdict (verify out of scope / not run) — not a gate"
    sha = verdict_sha(line)
    if sha is None:
        return (
            Decision(
                "hold",
                "verifier verdict names no verified commit (pre-DRE-1990 "
                "format or neutral status) — holding for a fresh verify",
            ),
            "",
        )
    if sha != head_sha and not carries_content(
        line, sha, head_content_id, pr_commit_shas
    ):
        return (
            Decision(
                "hold",
                f"verifier verdict is for {sha} but head is now {head_sha} — "
                "stale; holding for a fresh verify",
            ),
            "",
        )
    token = verdict_token(line, VERIFIER_MARKER)
    if token == "PASS":
        return None, ""
    if token == "SKIP":
        return None, "verifier verdict is SKIP for the current head — advisory, not a gate"
    return Decision("hold", "latest verifier verdict is not PASS — holding"), ""


def decide(
    head_sha: str,
    qa_login: str,
    check_runs,
    comments,
    review_suites=frozenset(),
    compare_status=None,
    head_branch: str = "",
    pr_author: str = "",
    pr_commits=(),
    head_content_id: Optional[str] = None,
    merge_state: str = "",
) -> Decision:
    """The whole gate: conditions 0 → D → 1 → 2 → 3, first blocker wins.
    `review_suites` is the verified-origin record from review_suite_ids();
    the default (empty — nothing excluded) is the fail-closed direction.
    `head_branch` / `pr_author` / `pr_commits` are the dependabot-policy
    record (DRE-2039); the defaults reproduce the pre-DRE-2039 behavior
    exactly.

    `merge_state` is the conflict record — GitHub's own `mergeStateStatus`
    (DRE-2416). Condition 0 runs FIRST, exactly where the shell arm it
    replaces sat: a conflicted branch routes to the fix agent whatever its
    CI or verdict state says, and it does so BEFORE condition D so a
    conflicted Dependabot PR keeps its exemption (Dependabot rebases and
    recreates its own conflicts; the fix agent has no card to work).

    `compare_status` is the branch-currency record. Since DRE-2416 it
    decides NOTHING — behind, diverged, unverifiable and current all merge
    alike; a behind head is recorded as a note so the run log says so. The
    fleet does not require up-to-date branches and this gate is the single
    writer of that rule (CEO decision 2026-08-20, DRE-2597).

    `head_content_id` is the content-binding record (DRE-2340) — the id of
    the PR's own contribution at the CURRENT head, computed by main() from
    the compare payload. None (the default, and the fail-closed direction
    on a truncated or blipped record) means verdicts bind the head SHA
    alone. `pr_commits` doubles as the carry's condition 4."""
    blocked = evaluate_conflict(merge_state)
    if blocked:
        return blocked

    blocked = evaluate_dependabot(head_branch, pr_author, pr_commits)
    if blocked:
        return blocked

    blocked = evaluate_checks(check_runs, review_suites)
    if blocked:
        return blocked

    pr_commit_shas = commit_shas(pr_commits)
    # Verdicts honoured across a head change, collected as they are read so
    # the carry can be reported wherever the decision lands.
    carried = []

    critic_line = first_line(
        latest_verdict_comment(comments, qa_login, CRITIC_MARKER)
    )
    blocked = evaluate_critic(
        critic_line, head_sha, head_content_id, pr_commit_shas
    )
    if blocked:
        return blocked
    critic_sha = verdict_sha(critic_line)
    if critic_sha and critic_sha != head_sha:
        carried.append(f"critic@{critic_sha}")

    verifier_line = first_line(
        latest_verdict_comment(comments, qa_login, VERIFIER_MARKER)
    )
    blocked, note = evaluate_verifier(
        verifier_line, head_sha, head_content_id, pr_commit_shas
    )
    if blocked:
        return blocked
    verifier_sha = verdict_sha(verifier_line)
    if verifier_sha and verifier_sha != head_sha:
        carried.append(f"verifier@{verifier_sha}")

    def _decided(decision: Decision) -> Decision:
        """Attach the carry record to whatever the gate finally decides —
        the PR gets its explanation however the evaluation ends."""
        if carried:
            decision.carried = carried
            decision.content_id = head_content_id
        return decision

    if carried:
        reason = (
            f"CI green + critic APPROVE bound to {critic_sha or head_sha}, "
            f"carried to head {head_sha}: the PR's own changes are unchanged "
            f"(content:{head_content_id}) — merge as qa-bot"
        )
    else:
        reason = f"CI green + critic APPROVE bound to {head_sha} — merge as qa-bot"
    decision = _decided(Decision("merge", reason))
    if note:
        decision.notes.append(note)
    # DRE-2416: freshness is not a gate, but a behind-base merge is
    # deliberate and the run log has to say so.
    stale_note = currency_note(compare_status)
    if stale_note:
        decision.notes.append(stale_note)
    return decision


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--head-sha", required=True,
                        help="the PR's current headRefOid (full 40-hex)")
    parser.add_argument("--qa-login", required=True,
                        help="trusted verdict author, e.g. agent-bureau-qa-bot[bot]")
    parser.add_argument("--check-runs-file", required=True,
                        help="raw REST payload of GET commits/{sha}/check-runs")
    parser.add_argument("--comments-file", required=True,
                        help="raw REST payload of GET issues/{pr}/comments")
    parser.add_argument("--workflow-runs-file", required=True,
                        help="raw REST payload of GET actions/runs?head_sha=<sha> "
                             "— the verified-origin record for the review-run "
                             "exclusion (DRE-1994)")
    parser.add_argument("--compare-file", required=True,
                        help="raw REST payload of GET "
                             "compare/{base}...{head_sha} — the head's "
                             "content-binding record (DRE-2340, .files[]); "
                             "its .status is reported as a note and gates "
                             "nothing (DRE-2416). Must NOT be trimmed")
    parser.add_argument("--merge-state", default="",
                        help="GitHub's mergeStateStatus for the PR — the "
                             "conflict record (DRE-2416). DIRTY routes to "
                             "the fix agent; omitted claims no conflict")
    parser.add_argument("--review-workflows",
                        default=",".join(DEFAULT_REVIEW_WORKFLOWS),
                        help="comma-separated paths of the review workflow "
                             "files whose check runs are excluded from the "
                             "all-green rule")
    # Dependabot-policy record (DRE-2039) — optional; omitting all three
    # reproduces the pre-DRE-2039 behavior (no dependabot leniency, no
    # dependabot refusal).
    parser.add_argument("--head-branch", default="",
                        help="the PR's head branch ref (condition D applies "
                             "only to dependabot/** branches)")
    parser.add_argument("--pr-author", default="",
                        help="the PR author's login per GET pulls/{pr} "
                             ".user.login, e.g. dependabot[bot]")
    parser.add_argument("--pr-commits-file", default=None,
                        help="raw REST payload of GET pulls/{pr}/commits — "
                             "carries Dependabot's update-type metadata "
                             "(DRE-2039) and proves a carried verdict's "
                             "commit is still in this PR's history "
                             "(DRE-2340); an empty list is fail-closed")
    return parser


def _die(msg: str) -> "NoReturn":  # noqa: F821
    print(f"merge_gate: {msg}", file=sys.stderr)
    sys.exit(2)


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    if not _HEAD_SHA_RE.match(args.head_sha or ""):
        _die(f"--head-sha must be a full 40-hex SHA, got {args.head_sha!r}")
    # The [bot] suffix is GitHub-reserved; an empty or non-App login here
    # means the token minting step broke — fail loud, never fail open.
    if not args.qa_login.endswith("[bot]") or len(args.qa_login) <= len("[bot]"):
        _die(f"--qa-login must be a GitHub App login (…[bot]), got {args.qa_login!r}")

    try:
        with open(args.check_runs_file) as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        _die(f"cannot read check runs: {e}")
    check_runs = payload.get("check_runs") if isinstance(payload, dict) else payload
    if not isinstance(check_runs, list):
        _die("check-runs payload has no check_runs list")

    try:
        with open(args.comments_file) as f:
            comments = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        _die(f"cannot read comments: {e}")
    if not isinstance(comments, list):
        _die("comments payload is not a list")

    try:
        with open(args.workflow_runs_file) as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        _die(f"cannot read workflow runs: {e}")
    workflow_runs = (
        payload.get("workflow_runs") if isinstance(payload, dict) else payload
    )
    if not isinstance(workflow_runs, list):
        _die("workflow-runs payload has no workflow_runs list")
    review_paths = frozenset(
        p.strip() for p in args.review_workflows.split(",") if p.strip()
    )
    review_suites = review_suite_ids(workflow_runs, review_paths)

    try:
        with open(args.compare_file) as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        _die(f"cannot read compare record: {e}")
    if not isinstance(payload, dict):
        _die("compare payload is not an object")
    # `{}` (the workflow's blip substitute) yields None. Since DRE-2416 that
    # gates nothing — it just means there is no behind-base note to make.
    compare_status = payload.get("status")
    # DRE-2340: the head's content id comes from the payload the gate has
    # ALREADY read — no new API call on the decision path, and no way for
    # the caller to hand the gate an id computed for a different head. A
    # trimmed, truncated (300-file cap) or blipped record yields None, and
    # None means verdicts bind the head SHA alone, exactly as before.
    head_content_id = content_id(payload)

    pr_commits = []
    if args.pr_commits_file:
        try:
            with open(args.pr_commits_file) as f:
                pr_commits = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            _die(f"cannot read PR commits: {e}")
        if not isinstance(pr_commits, list):
            _die("pr-commits payload is not a list")

    decision = decide(
        args.head_sha, args.qa_login, check_runs, comments, review_suites,
        compare_status, args.head_branch, args.pr_author, pr_commits,
        head_content_id, args.merge_state,
    )
    for note in decision.notes:
        print(f"note={note}")
    print(f"decision={decision.action}")
    print(f"reason={decision.reason}")
    # DRE-2340: only when a verdict was honoured across a head change. The
    # workflow turns these two lines into the PR comment that explains it —
    # nobody reads diffs here, so the audit trail has to explain itself.
    if decision.carried:
        print(f"carried={','.join(decision.carried)}")
        print(f"carried_content_id={decision.content_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
