#!/usr/bin/env python3
"""Which other open pull requests a branch carries — the merge gate's stack
record (DRE-4103).

THE INCIDENT (2026-09-16 17:08 PT, agent-bureau). Three branches were
stacked, each built on the one below: #2582 (REQUEST_CHANGES) → #2583
(REQUEST_CHANGES) → #2585 (APPROVE), and #2585 was opened against `main`.
Merge Gate run 35165261547 read #2585's own verdicts, decided `merge`, and
the merge commit made all three heads reachable from `main` — so GitHub
marked the two rejected pull requests merged as well. Nobody merged them;
no gate run ever evaluated them. `linear-sync` closed all three cards Done.

The gate was right about the pull request it was given. It had no notion of
stacking at all: it never asked what ELSE the branch would bring into the
base. This module answers that, and `merge_gate.evaluate_stack` (condition
S) refuses the merge while any open pull request the branch carries lacks
an APPROVE bound to its own head.

"Carries" is read off GitHub's own records, never inferred:

  * The branch's commits are the `commits[]` of the three-dot compare
    `compare/{base}...{head}` the gate ALREADY fetches for content binding
    (DRE-2340) — exactly the commits a merge would bring into the base. The
    record is complete only when `total_commits` says so; a blipped (`{}`)
    or truncated (250-commit cap) record proves nothing (`branch_commits`
    returns None).
  * An open pull request is stacked under this one when its HEAD is one of
    those commits (`stacked_on`) — the case that makes GitHub mark it
    merged. A parent that has merged is no longer open, and a parent this
    branch targets directly (a properly retargeted stack) is its base, not
    one of its commits; neither is a blocker.

THE RESIDUAL, named rather than left to be found: a parent that moved on
after the child branched (a fix pushed onto it) has a head the child does
not carry, so the child's copy of the parent's OLDER commits is not seen
here. That merge lands those older commits without marking the parent
merged — the parent stays open and still passes its own gate. Seeing it
would cost one commit listing per open pull request on every gate wake.

The gatherer is the one seam here that touches the network. It never
raises and never fails the gate's step: an unreadable read is written INTO
the record, and the gate HOLDS on it — never "there are no open pull
requests" on the strength of a listing that failed.

Record shape (what `gather` writes and `read_stack` reads):

    {"readable": true,
     "open_prs": [{"number": 2582, "head_sha": "<40-hex>"}, …],
     "comments": {"2582": <gh api --paginate --slurp pages> | null, …}}

`comments` holds a thread only for a pull request this branch carries —
an unstacked branch, the ordinary case, costs exactly one listing call.
`null` is a thread the gatherer could not read. `{"readable": false,
"detail": …}` is a listing it could not read.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field

# The gh seam is stranded_fix's — one wrapper that turns every failure into
# `(None, detail)` rather than an exception, the contract this gatherer
# needs too. A second copy is how two readers of one API drift.
from stranded_fix import _gh  # noqa: F401  (patched in tests)

_SHA_LEN = 40


@dataclass(frozen=True)
class Stack:
    """The stack record as the merge gate sees it.

    `readable` False is the fail-closed state and carries `detail`, so the
    run log and the hold say WHY nothing could be proved. `open_prs` is
    `((number, head_sha), …)` for every open pull request in the repo;
    `comments` maps a stacked pull request's number to its comment pages,
    or to None when its thread could not be read.
    """

    readable: bool = True
    open_prs: tuple = ()
    comments: dict = field(default_factory=dict)
    detail: str = ""


def _unreadable(detail: str) -> Stack:
    return Stack(readable=False, detail=detail)


def read_stack(payload) -> Stack:
    """Parse the record `gather` writes. Anything that is not provably a
    stack record reads as UNREADABLE, which the gate holds on."""
    if not isinstance(payload, dict):
        return _unreadable("the stack record is not an object")
    if not payload.get("readable"):
        return _unreadable(str(payload.get("detail")
                               or "the stack record says it could not be read"))
    listed = payload.get("open_prs")
    if not isinstance(listed, list):
        return _unreadable("the stack record carries no open pull request list")
    open_prs = []
    for item in listed:
        number = item.get("number") if isinstance(item, dict) else None
        head = item.get("head_sha") if isinstance(item, dict) else None
        if (not isinstance(number, int) or isinstance(number, bool)
                or not isinstance(head, str) or len(head) != _SHA_LEN):
            return _unreadable("the stack record carries a shapeless pull request")
        open_prs.append((number, head))
    raw = payload.get("comments") or {}
    if not isinstance(raw, dict):
        return _unreadable("the stack record's comment threads are not a map")
    comments = {}
    for key, pages in raw.items():
        try:
            comments[int(key)] = pages
        except (TypeError, ValueError):
            return _unreadable("the stack record names a thread with no number")
    return Stack(open_prs=tuple(open_prs), comments=comments)


def branch_commits(compare):
    """Every commit a merge of this branch would bring into its base — the
    `commits[]` of the three-dot compare record — or None when the record
    cannot prove it is complete (a `{}` blip, a missing or non-integer
    `total_commits`, or GitHub's 250-commit cap truncating the list)."""
    if not isinstance(compare, dict):
        return None
    commits = compare.get("commits")
    total = compare.get("total_commits")
    if (not isinstance(commits, list) or not isinstance(total, int)
            or isinstance(total, bool) or len(commits) != total):
        return None
    shas = {c.get("sha") for c in commits if isinstance(c, dict)}
    if None in shas or len(shas) != total:
        return None
    return frozenset(shas)


def stacked_on(open_prs, pr_number, commits) -> list:
    """`[(number, head_sha), …]` of the OTHER open pull requests whose head
    this branch carries, in number order. One implementation, read by the
    gatherer (to know whose threads to fetch) and by the gate (to know whose
    verdicts to ask for) — if the two ever disagreed, the pull request the
    gate found and the gatherer did not would read UNKNOWN and hold."""
    try:
        own = int(pr_number)
    except (TypeError, ValueError):
        own = None
    return sorted(
        (number, head) for number, head in open_prs
        if number != own and head in commits
    )


def gather_stack(repo: str, pr_number, compare) -> dict:
    """The record merge-gate.yml hands `--stack-file`.

    One paginated listing of the repo's open pull requests, plus one thread
    read per pull request this branch carries. Never raises.
    """
    out, detail = _gh([
        "api", "--paginate", f"repos/{repo}/pulls?state=open&per_page=100",
        "--jq", ".[] | {number, head_sha: .head.sha}",
    ])
    if out is None:
        return {"readable": False,
                "detail": f"listing the open pull requests failed: {detail}"}
    open_prs = []
    for line in out.splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except ValueError:
            return {"readable": False,
                    "detail": "the open pull request listing was unparseable"}
        if not isinstance(item, dict):
            return {"readable": False,
                    "detail": "the open pull request listing was unparseable"}
        open_prs.append(item)
    record = {"readable": True, "open_prs": open_prs, "comments": {}}
    parsed = read_stack(record)
    commits = branch_commits(compare)
    if not parsed.readable or commits is None:
        # Nothing to fetch threads for: the gate holds on either state
        # itself, and says which.
        return record
    for number, _head in stacked_on(parsed.open_prs, pr_number, commits):
        thread, detail = _gh([
            "api", "--paginate", "--slurp",
            f"repos/{repo}/issues/{number}/comments?per_page=100",
        ])
        pages = None
        if thread is not None:
            try:
                pages = json.loads(thread)
            except ValueError:
                pages = None
        if pages is None:
            print(f"stacked_prs: #{number}'s thread unreadable: {detail}",
                  file=sys.stderr)
        record["comments"][str(number)] = pages
    return record


def _cmd_gather(args) -> int:
    try:
        with open(args.compare_file, encoding="utf-8") as fh:
            compare = json.load(fh)
    except (OSError, ValueError):
        compare = None
    record = gather_stack(args.repo, args.pr, compare)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(record, fh)
    if record.get("readable"):
        print(f"stacked_prs: {len(record['open_prs'])} open pull request(s); "
              f"stacked under #{args.pr}: "
              f"{', '.join('#' + n for n in record['comments']) or 'none'}")
    else:
        print(f"stacked_prs: stack unreadable — {record.get('detail')}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    gather = sub.add_parser("gather", help="write the merge gate's stack record")
    gather.add_argument("--repo", required=True)
    gather.add_argument("--pr", required=True)
    gather.add_argument("--compare-file", required=True,
                        help="the compare record the gate already fetched "
                             "(compare/{base}...{head}) — its commits[] are "
                             "what this branch carries")
    gather.add_argument("--out", required=True)
    gather.set_defaults(fn=_cmd_gather)
    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
