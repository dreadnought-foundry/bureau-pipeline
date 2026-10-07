#!/usr/bin/env python3
"""How a fix round that pushed nothing ended, read before any escalation is written (DRE-6018).

THE FAULT (twice on 2026-10-06 PT). The Report step treated every fix round
that pushed nothing as a dispute a person had to settle, whatever the pull
request said by the time the round ended:

  * DRE-5654 (portico #931). The critic approved at 10:46 and 10:52. A fix run
    dispatched at 10:51 for an older round wrote "Nothing to fix … No human
    decision is needed: let the review of head 75ef866d finish."
  * DRE-5969 (agent-bureau #3274). The critic approved at 15:13 and the
    Verifier passed at 15:14, newer than the round the fix run was answering.

Both cards still got "🙋 The fix agent disagrees with the reviewer's blocking
finding … This needs your call", `needs-human` and a move to Triage. The
operator moved each back by hand, and on #3274 the medic refused to retry the
failed CI because of the label.

THE RULE, in one sentence: only a real disagreement with an open blocking
finding at the current head reaches a person. `classify` reads the thread as
it stands when the run ends and answers one of three words:

  * `approved` — the critic's newest structured verdict is APPROVE at the
    current head, and it is not the verdict this run was sent with. A run
    sent WITH an APPROVE (the sweep's approved-but-red dispatch) is answering
    red checks, not a finding, so its blocker escalates as it always did.
  * `nothing-to-fix` — the fixer's own handoff opens with "Nothing to fix"
    (or DRE-5659's "Nothing for the fixer to fix"), and no blocking finding
    is open at the current head.
  * `escalate` — everything else, routed exactly as before this card.

A blocking finding is open at the head when the critic's newest structured
verdict is REQUEST_CHANGES there (or names no commit at all), or the
Verifier's newest structured verdict is FAIL there. The fixer's word never
closes a finding the critic or the Verifier still holds open.

FAIL-CLOSED. An unreadable thread or an unknown head answers `escalate`: a
read that failed must never silence a real dispute.

THE QUIET LINE. A quiet exit posts one line on the pull request (`quiet_line`)
and nothing on the card. It opens with neither 🛑 nor 🔧, so the fix loop does
not read it as a blocker waiting on an answer (`fix_context.BLOCKER_PREFIX`)
or as a pushed attempt. It does carry "pushed no new commit" and the head, so
it counts toward the convergence halt (`fix_convergence.is_no_progress`): two
quiet rounds on one commit stop the loop, and the approved-but-red sweep
cannot dispatch quiet runs forever.

Verdicts are read with the merge gate's own grammar (`merge_gate`), from the
critic's identity only, so a quoted or planted verdict is not one.

Tests: tests/test_fix_exit_classified.py.

CLI (the seam scripts/report_fix_result.sh uses):

    fix_exit.py classify --comments-json F --head SHA --verdict-file F \\
                         --text-file F --attempt N --out F
        prints the word on stdout's first line; writes the quiet line to
        --out only when the word is not `escalate`. Never exits non-zero on
        an unreadable input — that is an answer (`escalate`), not an error.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from typing import Optional

from fix_concurrency import QA_BOT_LOGIN
from merge_gate import (
    CRITIC_MARKER,
    VERIFIER_MARKER,
    first_line,
    flatten_pages,
    opens_with_marker,
    verdict_sha,
    verdict_token,
)

ESCALATE = "escalate"
APPROVED = "approved"
NOTHING = "nothing-to-fix"

#: What the quiet line opens with. Not 🛑 (a blocker) and not 🔧 (a push).
QUIET_PREFIX = "🔕"

#: The fixer's own "nothing to fix", read from the opening words of its
#: handoff only. The second form is DRE-5659's wording on portico #883.
_NOTHING_RE = re.compile(
    r"^[\s*_#>`]*nothing (?:to fix|for the fixer to fix)\b", re.IGNORECASE)

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


@dataclass
class Exit:
    kind: str
    why: str


def _latest_structured(comments, marker: str, qa_login: str) -> Optional[str]:
    """First line of the newest critic-identity comment that opens with
    `marker` AND carries a structured verdict token. A neutral status line
    ("could not run") is not a verdict and does not hide the one before it."""
    latest = None
    for c in comments:
        if (c.get("user") or {}).get("login") != qa_login:
            continue
        body = c.get("body") or ""
        if not opens_with_marker(body, marker):
            continue
        line = first_line(body)
        if verdict_token(line, marker):
            latest = line
    return latest


def says_nothing_to_fix(text: str) -> bool:
    return bool(_NOTHING_RE.match(text or ""))


def classify(comments, head: str, fetched_verdict: str = "", text: str = "",
             qa_login: str = QA_BOT_LOGIN) -> Exit:
    """How this round ended. `comments` is the thread as it stands now (None
    when it could not be read), `head` the pull request's head now,
    `fetched_verdict` the critic verdict the run was sent with, and `text`
    what the fixer handed back."""
    if comments is None:
        return Exit(ESCALATE, "the pull request thread could not be read")
    if not _SHA_RE.match(head or ""):
        return Exit(ESCALATE, "the pull request's head could not be read")

    critic = _latest_structured(comments, CRITIC_MARKER, qa_login)
    critic_token = verdict_token(critic, CRITIC_MARKER) if critic else None
    critic_sha = verdict_sha(critic) if critic else None
    verifier = _latest_structured(comments, VERIFIER_MARKER, qa_login)

    if critic_token == "REQUEST_CHANGES" and critic_sha in (head, None):
        return Exit(ESCALATE, "the critic's blocking finding is open at the head")
    if verifier and verdict_token(verifier, VERIFIER_MARKER) == "FAIL" \
            and verdict_sha(verifier) == head:
        return Exit(ESCALATE, "the Verifier's failure is open at the head")

    if critic_token == "APPROVE" and critic_sha == head:
        sent_with = first_line(fetched_verdict or "")
        if sent_with.strip() != critic.strip():
            return Exit(APPROVED, "the critic approved the head after this "
                                  "round's verdict")
    if says_nothing_to_fix(text):
        return Exit(NOTHING, "the fix agent found nothing to fix and no "
                             "blocking finding is open at the head")
    return Exit(ESCALATE, "no evidence the round's finding is closed")


def quiet_line(kind: str, attempt, head: str) -> str:
    """The one line a quiet exit posts. Plain English, no thread text."""
    because = {
        APPROVED: "the critic has approved this commit since the review this "
                  "round was sent to fix",
        NOTHING: "the fix agent found nothing to fix, and no blocking finding "
                 "is open on this commit",
    }[kind]
    return (f"{QUIET_PREFIX} Fix attempt {attempt} pushed no new commit "
            f"(head `{(head or '')[:8]}`) and none is needed: {because}. "
            "No person is needed, and the card stays where it is.")


def _read(path: Optional[str]) -> str:
    if not path:
        return ""
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def _thread(path: str):
    """The thread as a flat comment list, or None when it is not one."""
    try:
        return flatten_pages(json.loads(_read(path)))
    except ValueError:
        return None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    cl = sub.add_parser("classify", help="classify a fix round that pushed nothing")
    cl.add_argument("--comments-json", required=True)
    cl.add_argument("--head", required=True)
    cl.add_argument("--verdict-file", default="")
    cl.add_argument("--text-file", default="")
    cl.add_argument("--attempt", required=True)
    cl.add_argument("--out", required=True)
    cl.add_argument("--qa-login", default=QA_BOT_LOGIN)
    args = parser.parse_args(argv)

    result = classify(_thread(args.comments_json), args.head,
                      _read(args.verdict_file), _read(args.text_file),
                      args.qa_login)
    if result.kind != ESCALATE:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(quiet_line(result.kind, args.attempt, args.head))
    sys.stdout.write(f"{result.kind}\n{result.why}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
