#!/usr/bin/env python3
"""The `branch-without-pr` class opens or finds the card's pull request (DRE-6447).

DRE-6056's run pushed its finished work to `agent/DRE-6056-retire-split-ledger`,
wrote a blocker instead of opening the pull request, and the card sat in
Backlog as blocked until a person opened the pull request by hand. The push
rescue (`push_rescue.py`) opens a pull request for a card's branch, but only
inside the build run, and a blocker note is one of its `STOP_NOTES`: it reads
the note as the agent choosing not to open one. This module overrules that for
this one class, from the sweep, and changes nothing in the push rescue. The
sweep's resolver (DRE-6508) imports it by the name `config/blocker-classes.json`
gives the class. It posts nothing: the resolver writes the receipt after a
non-`None` return.

## Two kinds of note, two kinds of pull request

The kind is read off the live thread — the newest marker in the card's comment
window — never off the reason:

  * STAMPED (`class=branch-without-pr`, the reporter's stamp off the agent's
    own first line, DRE-6444): the work is finished and only the push or the
    pull request failed. It gets a READY pull request, which the critic and
    the merge gate check like any other, and the card moves to In Review.
  * LEGACY (no `class=`, read as this class by its wording): the agent may
    have held the branch on purpose — DRE-6056's did. It gets a DRAFT, which
    the critic does not review and the gate does not merge, and the card moves
    nowhere: the `None` return hands it to the ask, which asks in Green Light
    whether the held work ships. Marking the draft ready is that decision.

## What `resolve` reads, in order

The default branch; the card's `agent/<card>-…` branches on origin (the newest
by its last commit when there are several — never a branch named in the note);
the card's counting pull request (`card_pr.find`, with the draft flag); a pull
request on the branch a person closed unmerged; and the compare. Any of the
module's own reads that is unreadable is `NotNow`, never "no branch" or
"nothing to open" (DRE-2034). Then:

  * open and ready → the In Review move, `pr-found`;
  * open draft, merged, closed unmerged, no branch, nothing ahead → `None`,
    a person's call — a merged card left open is linear-sync's decision or
    its miss, and the open marker is the gate's only brake on re-dispatching
    it (DRE-2316);
  * none → `gh pr create`, ready or `--draft`; a refused create is a
    `RuntimeError` carrying GitHub's last line, caught per card by the
    resolver's call site.

Every lane move is read back before it is reported: a card another writer
carried to In Review first (`qa-review.yml`, when a ready pull request opens)
passes, because the read checks where the card IS.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import blocker_class  # noqa: E402
import card_pr  # noqa: E402
import linear_ops  # noqa: E402
import push_rescue  # noqa: E402

#: The two seams a test replaces: `gh` calls, and the pull-request lookup.
_run = push_rescue._subprocess_run
_find = card_pr.find

#: The card's Linear URL, built as `plan_run.py` builds it — the card dict
#: carries none.
CARD_URL = "https://linear.app/dreadnoughtfoundry/issue/{identifier}"

#: `push_rescue._pr_body`'s closing sentence, the same words.
CLOSING = ("Nothing here asserts the card is finished: read the diff and the "
           "checks, and expect no author-written summary.")

#: How much of the agent's note the body and the receipt's note quote.
BODY_QUOTE = 500
NOTE_QUOTE = 120

#: The jq the compare is read through — `reconcile._flag_one_unlanded_branch`'s
#: shape, so a 250-commit compare is two fields, not the whole payload.
_COMPARE_JQ = "{ahead_by: .ahead_by, last: ([.commits[].commit.committer.date] | last)}"


def _last_line(text: str) -> str:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _read(argv: list, what: str, identifier: str) -> str:
    """`argv`'s stdout, or `NotNow` naming `what` when `gh` refused."""
    code, out, err = _run(argv)
    if code != 0:
        raise blocker_class.NotNow(f"{what} unreadable for {identifier}: {_last_line(err)}")
    return out


def _read_json(argv: list, what: str, identifier: str, kind: type):
    out = _read(argv, what, identifier)
    try:
        parsed = json.loads(out or "")
    except ValueError:
        parsed = None
    if not isinstance(parsed, kind):
        raise blocker_class.NotNow(f"{what} unreadable for {identifier}: "
                                   f"not a JSON {kind.__name__}")
    return parsed


def is_legacy(card: dict) -> bool:
    """True when the newest marker in the card's live window carries no
    `class=`. `NotNow` when the window holds no marker at all: the resolver
    found one seconds ago, so the thread changed under this module."""
    prefix = blocker_class.load()["marker"]
    for node in reversed(linear_ops.window_nodes(card.get("comments"))):
        body = (node.get("body") or "").lstrip()
        if body.startswith(prefix):
            return blocker_class.marker_class(body) is None
    raise blocker_class.NotNow(
        f"no open {prefix.rstrip(':')} marker in the live thread of {card['identifier']}")


def _compare(repo: str, base: str, branch: str, identifier: str) -> dict:
    compared = _read_json(
        ["gh", "api", f"repos/{repo}/compare/{base}...{branch}", "--jq", _COMPARE_JQ],
        "the compare", identifier, dict)
    if not isinstance(compared.get("ahead_by"), int):
        raise blocker_class.NotNow(f"the compare unreadable for {identifier}: no ahead_by")
    return compared


def _closed_by_a_person(repo: str, branch: str, identifier: str) -> bool:
    """A pull request on `branch` closed unmerged — the push rescue's own
    `--head` listing, `--state closed`. `card_pr.find` does not count one, and
    without this read every closed pull request would get a successor."""
    rows = _read_json(
        ["gh", "pr", "list", "--repo", repo, "--head", branch, "--state", "closed",
         "--json", "number,url,mergedAt", "--limit", "5"],
        "the closed pull-request listing", identifier, list)
    return any(isinstance(row, dict) and not row.get("mergedAt") for row in rows)


def pr_body(card: dict, reason: str, branch: str, *, legacy: bool) -> str:
    """The body, built the way `push_rescue._pr_body` builds one: the card id
    and its Linear URL first, so linear-sync and the critic key on it; then
    what opened it and the agent's own note; then the closing sentence."""
    identifier = card["identifier"]
    # Every quoted line carries `>`, a blank one too, so the quote stays inside
    # the second paragraph whatever paragraphs the note has.
    quoted = "\n".join(f"> {line}".rstrip()
                       for line in (reason or "")[:BODY_QUOTE].splitlines())
    if legacy:
        second = (
            f"OPENED AS A DRAFT BY THE SWEEP — the agent held this branch: the build "
            f"agent's blocker note carried no class and was read as "
            f"`branch-without-pr` by its wording, so the agent may have held "
            f"`{branch}` on purpose. Nobody has decided this work should ship. The "
            f"sweep has asked that question on the card in Green Light, and marking "
            f"this draft ready for review is that decision. The agent's note:\n{quoted}"
        )
    else:
        second = (
            f"OPENED BY THE SWEEP, not by the agent — read this before approving: "
            f"the build agent reported its work pushed to `{branch}` and the pull "
            f"request unopened (a `branch-without-pr` blocker). The agent's note:\n"
            f"{quoted}"
        )
    return (f"{identifier} — {CARD_URL.format(identifier=identifier)}\n\n"
            f"{second}\n\n"
            f"The commits are the agent's — only this pull request is not. {CLOSING}\n")


def _to_in_review(identifier: str) -> None:
    linear_ops.cmd_advance(identifier, "In Review", "Backlog", held=True)
    lane = (linear_ops.get_issue(identifier, fresh=True).get("state") or {}).get("name")
    if lane != "In Review":
        raise blocker_class.NotNow(f"In Review move did not land — the card is in {lane}")


def resolve(card: dict, reason: str, *, repo: str) -> tuple[str, str] | None:
    """Open or find the pull request for the card's newest branch. See the
    module docstring."""
    identifier = card["identifier"]
    legacy = is_legacy(card)

    base = _read(["gh", "api", f"repos/{repo}", "--jq", ".default_branch"],
                 "the default branch", identifier).strip()
    if not base:
        raise blocker_class.NotNow(f"the default branch unreadable for {identifier}: empty")
    refs = _read_json(
        ["gh", "api", f"repos/{repo}/git/matching-refs/heads/agent/{identifier}-"],
        "the branch listing", identifier, list)
    branches = [row["ref"][len("refs/heads/"):] for row in refs
                if isinstance(row, dict) and str(row.get("ref") or "").startswith("refs/heads/")]
    if not branches:
        return None

    compared: dict = {}
    if len(branches) > 1:
        for name in branches:
            compared[name] = _compare(repo, base, name, identifier)
        branch = max(branches, key=lambda name: compared[name].get("last") or "")
    else:
        branch = branches[0]

    try:
        pr = _find(identifier, branch=branch, repo=repo,
                   fields=card_pr.PR_FIELDS + ",isDraft")
    except card_pr.PrLookupError as e:
        first = (str(e).splitlines() or [""])[0]
        raise blocker_class.NotNow(f"pull-request state unreadable for {branch}: {first}") from e
    if pr is not None:
        if card_pr.pr_state(pr) == card_pr.OPEN and not pr.get("isDraft"):
            _to_in_review(identifier)
            return "pr-found", f"{pr.get('url')} was already open"
        # A draft is work waiting on somebody's decision; a merged card left
        # open is linear-sync's call or its miss. Either way a person's.
        return None

    if _closed_by_a_person(repo, branch, identifier):
        return None
    if branch not in compared:
        compared[branch] = _compare(repo, base, branch, identifier)
    if compared[branch]["ahead_by"] < 1:
        return None

    code, out, err = _run(
        ["gh", "pr", "create", "--repo", repo, "--base", base, "--head", branch,
         "--title", f"feat({identifier}): {card['title']}",
         "--body", pr_body(card, reason, branch, legacy=legacy)]
        + (["--draft"] if legacy else []))
    if code != 0:
        raise RuntimeError(f"gh pr create refused {branch}: {_last_line(err)}")
    if legacy:
        # The hand-off to the ask: nothing moved, nothing posted.
        return None
    url = _last_line(out)
    _to_in_review(identifier)
    return ("pr-opened", f"{url} opened from {branch} — the agent's note: "
                         f"{push_rescue.one_line(reason, NOTE_QUOTE)}")
