#!/usr/bin/env python3
"""The record pull request's body, rebuilt from the record (DRE-7813).

A proof record's pull request body carries the card's criterion table
(briefs/proof.md, "The pull request"). A resumed run amends the record in a new
commit and pushes, and opens no second pull request — so nothing rewrote the
body, and the critic read the first run's table beside an amended record. On
agent-bureau #3589 (DRE-6663, 2026-10-23) the critic sent a PASS record back on
"The PR body is stale and contradicts the record" for the fourth round
running, until the operator rewrote the body by hand.

`refresh` is proof-task.yml's `Refresh the record pull request's body` step,
run after every proof run, first or resumed: it finds the open pull request of
`agent/<CARD>-proof-record`, reads the record at that pull request's head —
the pushed one — the way the merge gate does (`proof_record.fetch`), renders
the body in the brief's shape and writes it with `gh pr edit --body-file`. The
model never edits the body.

Exit 0 when the body was written or no record pull request is open — a run
that opened none has no body to keep. Exit 1 on anything else: a lookup GitHub
refused, a record not found or with no status line or criterion table, an edit
refused. A body nobody refreshed is the defect this exists for, so the step
goes red rather than passing silently.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import proof_record  # noqa: E402 — the branch rule, the finder and the table, spelled once

#: The record's status line (briefs/proof.md, "The record", item 2).
_STATUS = re.compile(r"^\s*\*\*Status:")
_FENCE = proof_record._FENCE


def status_line(text: str) -> str | None:
    """The record's first `**Status: …**` line outside fenced code, as
    written, or None."""
    fenced = False
    for line in (text or "").splitlines():
        if _FENCE.match(line):
            fenced = not fenced
        elif not fenced and _STATUS.match(line):
            return line.strip()
    return None


def render(card: str, card_url: str, record: str) -> str:
    """The body in the brief's shape: `Proof record for <card>`,
    `What's new: none`, the card URL, then the record's status line and its
    criterion table, line for line. ValueError for a record missing either."""
    status = status_line(record)
    if status is None:
        raise ValueError("the record has no **Status:** line")
    table = proof_record.criterion_table(record)
    if table is None:
        raise ValueError("the record has no criterion table")
    return "\n".join([f"Proof record for {card}", "What's new: none", card_url,
                      "", status, "", *table]) + "\n"


def _fail(why: str) -> int:
    print(f"::error::proof_pr_body: {proof_record._one_line(why)}", file=sys.stderr)
    return 1


def refresh(repo: str, card: str, card_url: str, gh=None) -> int:
    """Rewrite the open record pull request's body from its record at head."""
    gh = gh or proof_record._gh
    branch = f"agent/{card}-proof-record"
    try:
        prs = json.loads(gh(["pr", "list", "--repo", repo, "--head", branch,
                             "--state", "open", "--json", "number,headRefOid"]) or "null")
        if not isinstance(prs, list):
            return _fail(f"the open pull requests of {branch} could not be read")
        if not prs:
            print(f"proof_pr_body: no open pull request for {branch} — no body to refresh")
            return 0
        number, head = str(prs[0]["number"]), prs[0]["headRefOid"]
        view = json.loads(gh(["pr", "view", number, "--repo", repo, "--json", "files"])
                          or "null")
    except (RuntimeError, ValueError, TypeError, KeyError, IndexError) as exc:
        return _fail(f"the record pull request of {branch} could not be read: {exc}")
    files = view.get("files") if isinstance(view, dict) else None
    record = proof_record.fetch(repo, files, head, gh)
    if record.text is None:
        return _fail(f"#{number}: {record.detail}")
    try:
        body = render(card, card_url, record.text)
    except ValueError as exc:
        return _fail(f"#{number}: {record.path} at {head}: {exc}")
    with tempfile.NamedTemporaryFile("w", suffix=".md", encoding="utf-8") as fh:
        fh.write(body)
        fh.flush()
        try:
            gh(["pr", "edit", number, "--repo", repo, "--body-file", fh.name])
        except RuntimeError as exc:
            return _fail(f"#{number}: the body could not be written: {exc}")
    print(f"proof_pr_body: #{number}'s body rebuilt from {record.path} at {head}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    cmd = sub.add_parser("refresh", help="rewrite the record pull request's body")
    cmd.add_argument("--repo", required=True)
    cmd.add_argument("--card", required=True)
    cmd.add_argument("--card-url", required=True)
    args = parser.parse_args(argv)
    return refresh(args.repo, args.card, args.card_url)


if __name__ == "__main__":
    sys.exit(main())
