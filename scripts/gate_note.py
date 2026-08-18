#!/usr/bin/env python3
"""Post a merge-gate status note EXACTLY once — including when runs race.

Origin (DRE-2508, caught live 2026-08-17 on bureau-pipeline #157).
merge-gate.yml's `human` arm guarded its "waiting for human merge" note
with a grep over `/tmp/comments.json` — a snapshot the run had fetched some
thirty lines earlier. That is check-then-act on a stale read: two gate
evaluations of ONE pull request (the CI `workflow_run` leg and the critic's
`issue_comment` leg are separate wakes) both read before either wrote,
neither saw the marker, and both posted. The integration harness asserts
exactly one such note and went red during a burst of five PRs — twelve gate
runs in twenty-five minutes. The doubled comment is cosmetic; a gate that
reds under load is not, because people learn to re-run it without reading.

The gate now also SERIALIZES its evaluations per pull request (merge-gate.yml
keys the evaluating job's concurrency group on one resolved PR number, the
same key for every event leg), so the overlap should not happen at all. This
module is the second, independent mechanism — it holds even when two writers
DO overlap, and unlike an Actions concurrency group it can be raced in a unit
test:

  1. READ AS LATE AS POSSIBLE — the record is fetched here, immediately
     before the write, not carried in from the top of the run.
  2. POST — no lock to take, no back-off. A legitimate first post is never
     delayed or dropped.
  3. CONVERGE — re-read, and if more than one note exists, keep the EARLIEST
     (lowest comment id) and delete the rest. The winner is a property of the
     data, so concurrent writers agree on it without coordinating; whoever
     sees the duplicate removes it, and a peer that got there first answers
     404, which is the happy path.

Scoped to the gate's OWN login throughout: a human quoting the marker on the
PR must not silence the honest state (the old grep matched any body), and the
gate must never delete a comment that is not its own.

An unreadable comments record DEFERS the note to the next gate wake rather
than posting blind — the gate wakes on every CI completion, every verdict,
and reconcile's ~15-minute nudge, so deferring costs a wake; posting blind
costs the duplicate this module exists to prevent.

Contract with merge-gate.yml: the body MUST contain the marker (the marker is
the idempotence key — a body that does not carry it re-posts on every wake,
the trap DRE-2340's carry note documents), and the note must carry no
verdict-shaped text: it is a status note, never an approval credential, and
it must not re-wake the gate's own issue_comment leg.
"""

from __future__ import annotations

import argparse
import json
import subprocess  # nosec B404 — fixed-arg calls to the gh CLI only
import sys

#: Pages of comments to walk. GitHub caps `per_page` at 100; ten pages is far
#: past any real PR and bounds a pathological loop.
MAX_PAGES = 10


class GateNoteError(RuntimeError):
    """A gh call on the note path failed — never swallowed into a fake empty
    record (the DRE-2034 class: a 403 is not "there are no comments")."""


def same_login(a: str | None, b: str | None) -> bool:
    """Login equality tolerant of the reserved "[bot]" suffix — the REST
    comment record carries it, a minted token's app-slug does not (the same
    tolerance scripts/harness/framework.py:same_bot applies)."""

    def norm(login):
        return (login or "").removesuffix("[bot]").lower()

    return bool(norm(a)) and norm(a) == norm(b)


def matching_notes(comments, marker: str, author: str) -> list:
    """The gate's own notes carrying `marker`, oldest first.

    Author-scoped on purpose: the idempotence key is the note the GATE
    posted, not the string appearing anywhere on the PR.
    """
    return sorted(
        (
            c
            for c in comments
            if same_login(((c.get("user") or {}).get("login")), author)
            and marker in (c.get("body") or "")
        ),
        key=lambda c: int(c.get("id") or 0),
    )


def post_once(api, marker: str, body: str, author: str, log=print) -> dict:
    """Ensure exactly one `marker` note authored by `author` exists.

    Returns the outcome as a dict: `action` is one of `standing` (one was
    already there — nothing written), `posted` (this run's note is the one
    that survives), `deduped` (this run raced, lost the tie-break, and
    removed its own note), or `deferred` (the record was unreadable).
    """
    if marker not in body:
        raise ValueError(
            "the note body must carry the marker — it IS the idempotence key, "
            "and a body without it re-posts on every gate wake"
        )
    try:
        standing = matching_notes(api.list_comments(), marker, author)
    except GateNoteError as e:
        # Fail-closed on the WRITE: unreadable is not "there is none".
        log(f"gate-note: comments unreadable ({e}) — deferring to the next wake")
        return {"action": "deferred", "id": None, "deleted": []}
    if standing:
        return {"action": "standing", "id": standing[0]["id"], "deleted": []}

    mine = api.create_comment(body)
    mine_id = int(mine["id"])

    # Converge. A listing that has not caught up with our own write must not
    # make us the invisible duplicate, so our note is always a candidate.
    try:
        seen = {
            int(c["id"]): c
            for c in matching_notes(api.list_comments(), marker, author)
        }
    except GateNoteError as e:
        log(f"gate-note: posted, but the re-read failed ({e}) — no dedupe pass")
        return {"action": "posted", "id": mine_id, "deleted": []}
    seen[mine_id] = mine

    winner = min(seen)
    deleted = []
    for comment_id in sorted(seen):
        if comment_id == winner:
            continue
        try:
            api.delete_comment(comment_id)
            deleted.append(comment_id)
        except GateNoteError as e:
            # A racing peer deleting the same loser first answers 404. That
            # is convergence, not failure — and a cosmetic note must never
            # red the gate run.
            log(f"gate-note: could not remove duplicate {comment_id}: {e}")
    return {
        "action": "posted" if winner == mine_id else "deduped",
        "id": winner,
        "deleted": deleted,
    }


class GateComments:
    """The live comments record for one PR, through the gh CLI."""

    def __init__(self, repo: str, pr: int | str):
        self.repo, self.pr = repo, str(pr)

    def _gh(self, *args: str, stdin: str | None = None) -> str:
        # B603/B607: fixed-arg gh call, shell=False; "gh" resolves via PATH
        # on the runner by design.
        p = subprocess.run(  # nosec B603 B607
            ["gh", *args], capture_output=True, text=True, check=False,
            input=stdin,
        )
        if p.returncode != 0:
            raise GateNoteError(
                f"gh {' '.join(args)} failed rc={p.returncode}: "
                f"{p.stderr.strip()[:400]}"
            )
        return p.stdout

    def list_comments(self) -> list:
        """Every comment on the PR — paginated, because the marker sitting on
        page two would read as "not posted yet" and duplicate it."""
        out = []
        for page in range(1, MAX_PAGES + 1):
            path = (
                f"repos/{self.repo}/issues/{self.pr}/comments"
                f"?per_page=100&page={page}"
            )
            batch = json.loads(self._gh("api", path) or "[]")
            out.extend(batch)
            if len(batch) < 100:
                break
        return out

    def create_comment(self, body: str) -> dict:
        return json.loads(
            self._gh(
                "api", "--method", "POST",
                f"repos/{self.repo}/issues/{self.pr}/comments",
                "--input", "-",
                stdin=json.dumps({"body": body}),
            )
        )

    def delete_comment(self, comment_id) -> None:
        self._gh(
            "api", "--method", "DELETE",
            f"repos/{self.repo}/issues/comments/{comment_id}",
        )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", required=True, help="owner/name")
    p.add_argument("--pr", required=True, help="pull request number")
    p.add_argument("--author", required=True,
                   help="the gate's own login — the note is scoped to it")
    p.add_argument("--marker", required=True,
                   help="the idempotence key; must appear in the body")
    p.add_argument("--body-file", required=True,
                   help="file holding the note body")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    with open(args.body_file, encoding="utf-8") as fh:
        body = fh.read()
    result = post_once(
        GateComments(args.repo, args.pr), args.marker, body, args.author
    )
    print(f"gate-note: {result['action']} (id={result['id']})")
    if result["deleted"]:
        print(f"gate-note: removed duplicates {result['deleted']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
