#!/usr/bin/env python3
"""The CONTENT a verdict was earned against — one module, one algorithm.

DRE-2340. A QA verdict binds the commit the critic read (DRE-1990), and at
the time this landed the merge gate also updated a branch that was behind
its base (DRE-1924). Both were right. Together, on a busy repo, they
livelocked: every gate-initiated `update-branch` moved the head, so the
verdict bound to the old head stopped binding, and the critic ran again —
against a branch that was stale again before the review it triggered could
finish. Portico PR #205 (2026-08-09) earned seven APPROVE verdicts in 47
minutes and threw six away; `main` took 30 commits from other work in that
window, one every 95 seconds.

The fix is to bind the verdict to the CONTENT the critic reviewed rather
than to the commit SHA that happened to carry it. A merge of the base into
the branch does not change the PR's own contribution, so it must not
destroy the verdict.

STILL LOAD-BEARING AFTER DRE-2416, which removed the gate's own
update-branch: the head still moves whenever the FIX AGENT reconciles a
conflicted branch, and that merge must not throw away a verdict for a diff
it did not touch. The gate, should_review_pr and reconcile all read this
one module so they cannot drift about what a verdict binds.

TWO PROPERTIES — the whole design rests on them, so they are proved
empirically against a real git repository in
tests/test_verdict_content_binding.py (GitCompareSemanticsTest) rather than
taken on faith from the REST docs, which do not state the first explicitly:

  1. `compare/{base}...{head}` is the THREE-DOT comparison. Its `files[]` is
     the diff from `merge_base(base, head)` to `head` — i.e. exactly the
     PR's own contribution, which is exactly what the critic reads and
     exactly what lands on the base branch. The base's own movement never
     appears in it.

  2. It is A FUNCTION OF `head` ALONE. For a fixed head H,
     `merge_base(X, H)` is the same commit for every X descending from the
     last base commit H already contains. So the id computed at review time
     and the id computed at decision time agree by construction — no clock,
     no race, no shared state, nothing to synchronise.

WHAT THIS DOES NOT WEAKEN (DRE-1990). It remains impossible to merge code
the critic has not effectively approved: any change to the PR's own
contribution — one added line, a conflict resolved by inserting new code in
the merge commit, a reverted base change, a base commit touching a file the
branch also touches — changes at least one blob SHA in the three-dot file
set, changes the id, and kills the verdict. You cannot add code without
changing content. The id is computed by the GATE, from GitHub's own compare
record; nothing the PR author controls feeds it, and authorship (DRE-1987)
is untouched — a non-qa-bot comment carrying a perfect id is still
invisible. CI still re-runs on the merged result, and CI on the base branch
is the backstop for what a stale head cannot see (DRE-2416).

KNOWN RESIDUAL, named rather than left to be discovered: the compare
record's `files[]` carries no FILE MODE, so a change that flips only a
mode (chmod +x) leaves the filename, status and blob sha identical and
therefore does not move the id. A push doing nothing but that would carry
its verdict. It is bounded — no code changes, CI still re-runs on the
merged result, and a fresh review would show the mode line in the diff —
and it cannot be closed from this record, which has no mode field to hash.

FAIL CLOSED, ALWAYS. `content_id()` returns None — never a partial hash —
whenever the compare record is not provably complete. None means "no
content binding; fall back to today's SHA binding", which is the strictly
safer answer. The load-bearing case is the 300-file cap: GitHub shows "up
to 300 changed files for the entire comparison" and does NOT paginate that
array, so on a larger PR anything past file 300 could change without
changing the id. That is the one way unreviewed code could ride in.

Used by scripts/merge_gate.py (the decision), scripts/should_review_pr.py
(skip the re-review) and scripts/reconcile.py (has_verdict) — all three
through THIS module. DRE-1998 already had to fix one independent copy of a
verdict read; there is exactly one implementation here.

CLI (for the workflow producers, which have the compare record in a file):

    python3 verdict_content.py --compare-file /tmp/compare.json

Prints the 64-hex id on stdout, or nothing when the record yields no id.
Always exits 0: "no id" is a normal answer that degrades to SHA binding,
never a workflow failure.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys

#: GitHub's documented cap on the compare record's `files[]` — "up to 300
#: changed files for the entire comparison" — which it does NOT paginate.
#: At or past the cap the array may be truncated, so no complete hash of
#: the PR's contribution can be taken. Fail closed (trap 3).
MAX_COMPARE_FILES = 300

#: The compare statuses GitHub reports. Anything else (including the `{}`
#: blip substitute the workflows write) means the record is unreadable, so
#: it yields no id — the same fail-closed direction merge_gate's condition
#: 0 takes on an unverifiable status.
COMPARE_STATUSES = frozenset({"ahead", "behind", "diverged", "identical"})

#: The field the producers append after the bound SHA, as the LAST thing
#: on the verdict line:
#:   🔎 QA Critic — VERDICT: APPROVE @<40-hex> content:<64-hex>
#:
#: Anchored to END OF LINE on purpose, not merely `\b`-delimited. The
#: middle of that line is `head -1` of a file the critic AGENT wrote, and
#: the critic reads an attacker-authored diff — so a prompt injection could
#: try to make it emit its own `content:<id>` earlier on the line, naming
#: the fingerprint of code it intends to push next. The producers append
#: this field LAST, so the end-anchored match always reads the WORKFLOW's
#: id and never the agent's. A future producer that appends anything after
#: it makes this read nothing at all — the fail-closed direction (back to
#: SHA binding).
#:
#: The 64-hex run is exact, so a truncated or over-long value reads as NO
#: field rather than a partial one. It cannot disturb merge_gate's
#: `_SHA_RE` (a search for `@<40-hex>`) or its anchored `_verdict_re`,
#: and carries no second `@<40-hex>` (trap 2).
_CONTENT_RE = re.compile(r"\bcontent:([0-9a-f]{64})\s*$")

#: Field separators for the canonical form. \x00 cannot appear in a path,
#: a status or a blob sha, so no two distinct file sets can serialise to
#: the same bytes.
_FIELD_SEP = "\x00"
_RECORD_SEP = b"\x1e"


def verdict_content_id(line: str | None) -> str | None:
    """The content id a verdict line carries, or None when it carries none.

    None is the pre-DRE-2340 verdict format — every verdict in flight on
    the fleet the day this ships — and it means exactly what it meant
    before: this verdict binds a SHA and nothing else.
    """
    if not line:
        return None
    m = _CONTENT_RE.search(line)
    return m.group(1) if m else None


def content_id(compare_payload) -> str | None:
    """A sha256 over the PR's own contribution, or None if not provable.

    `compare_payload` is the raw JSON of
    `GET /repos/{repo}/compare/{base}...{head}` — the record the merge gate
    already fetches, so this costs no API call on the decision path.

    The digest covers the sorted, canonicalised
    `(filename, previous_filename or "", status, sha)` tuples of `files[]`:
    the blob sha catches any content change, the filename and
    previous_filename catch moves, and the status catches a same-blob
    add/delete/modify flip. Sorting makes the id independent of the order
    GitHub happens to list the files in.

    None (no content binding — fall back to SHA binding) whenever the
    record is not provably complete:

      * `status` is not one of ahead/behind/diverged/identical — an
        unreadable record, including the workflows' `{}` blip substitute;
      * `merge_base_commit.sha` is absent — without a merge base there is
        no proof this is the three-dot comparison at all;
      * `files` is missing or not a list;
      * `len(files) >= 300` — the uncounted cap (see MAX_COMPARE_FILES);
      * any entry is missing `filename` or `sha`.
    """
    if not isinstance(compare_payload, dict):
        return None
    if compare_payload.get("status") not in COMPARE_STATUSES:
        return None
    merge_base = compare_payload.get("merge_base_commit")
    if not isinstance(merge_base, dict) or not merge_base.get("sha"):
        return None
    files = compare_payload.get("files")
    if not isinstance(files, list):
        return None
    if len(files) >= MAX_COMPARE_FILES:
        return None

    rows = []
    for entry in files:
        if not isinstance(entry, dict):
            return None
        filename = entry.get("filename")
        sha = entry.get("sha")
        if not filename or not sha:
            return None
        rows.append((
            str(filename),
            str(entry.get("previous_filename") or ""),
            str(entry.get("status") or ""),
            str(sha),
        ))

    digest = hashlib.sha256()
    for row in sorted(rows):
        digest.update(_FIELD_SEP.join(row).encode("utf-8"))
        digest.update(_RECORD_SEP)
    return digest.hexdigest()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--compare-file", required=True,
                    help="raw REST payload of GET compare/{base}...{head}")
    args = ap.parse_args(argv)
    try:
        with open(args.compare_file) as f:
            payload = json.load(f)
    except (OSError, ValueError) as e:
        # An unreadable record is "no id", the same as an incomplete one:
        # the caller degrades to SHA binding, which is today's behaviour.
        print(f"verdict_content: cannot read compare record: {e}",
              file=sys.stderr)
        return 0
    cid = content_id(payload)
    if cid:
        print(cid)
    else:
        print("verdict_content: compare record yields no content id — the "
              "verdict will bind the head SHA alone", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
