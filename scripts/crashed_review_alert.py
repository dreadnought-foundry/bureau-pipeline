#!/usr/bin/env python3
"""A review stuck at its retry cap is a console alert naming its card
(DRE-8690).

On 2026-11-01 two reviews that died behind the Claude limit — agent-bureau PR
#4462 (DRE-8667) and PR #4472 (DRE-8553) — stayed stuck for two hours after
the limit lifted. The sweep wrote "the reviewer is down and a human must fix
it" into its log every 15 minutes and posted `🚨 reviewer-down` on each card
once, and nothing acted on either. The one console alert, the fleet's
`reviewer_outage:fleet`, names no card, so the open-alerts card rule (DRE-8630)
gave it `card: null`, and it cleared itself correctly once reviews worked
again — with two pull requests still stranded.

`settle(capped, repo_slug, unread, …)` is the crashed-review pass's last step,
handed every head that reached `reconcile._report_reviewer_down` this pass.
It raises one WARN alert per card and resolves this repo's alerts whose review
is, on positive evidence, no longer stuck.

## The alert

Notice key `reviewer-down:<repo>:<card>` (console kind
`pipeline_notice:reviewer-down:<repo>:DRE-<n>`, which the open-alerts card rule
reads as the card), severity WARN, raised through `pipeline_notice.raise_notice`
with no fallback card. The title opens `<card>: PR #<n> @<head, 8 characters>
—`, because the open-alerts read serves the title and not the body, and the
resolve below reads the PR and head back from it. The body names the PR link,
the short head, the crashed run's link and the operator's remedy, the one used
on 11-01: `gh run rerun <run> --failed --repo <owner/repo>`. `action_url` is
the pull request. A repeat raise costs nothing: the door refreshes the text and
delivers nothing again. A raise the door cannot take is logged and never fails
the sweep.

## Skipped is not recovered

The crashed-review pass passes over a capped head when a review dispatch is in
flight anywhere in the repo, when a check is live, or when the pull request is
`DIRTY`. A head merely absent from this pass's capped list therefore proves
nothing, and resolving on absence would let a sweep that skipped every head
resolve every alert — and the next sweep's raise would carry a new
`raised_at`, restarting the Alarm Repair's two-hour clock so it never came.

So an open alert of this repo whose card is not on the capped list is resolved
only when the pull request its title names

* is no longer open (closed or merged),
* has a head that no longer starts with the title's sha, or
* has a verdict bound to that head (`reconcile.verdict_bound`).

The pull request is read off the sweep's own listing, or with one `gh pr view`
when it is not there. A read that fails resolves nothing for that alert; so
does a pull request in `unread`, the ones this pass holds only part of; and an
unreadable `bureau_read.open_alerts()` resolves nothing at all.

## The lines

Each opens with `TAG`, on stdout, and `settle` returns them in order:

    crashed-review-alert: <raised|repeat|lost> card=<DRE-n>
    crashed-review-alert: raise failed card=<DRE-n> — <reason>
    crashed-review-alert: resolved card=<DRE-n> — <evidence>
    crashed-review-alert: card=<DRE-n> PR #<n> still stuck — kept open
    crashed-review-alert: card=<DRE-n> PR #<n> unreadable — <reason>; kept open
    crashed-review-alert: open alerts unreadable — <reason>; nothing resolved this pass

## The vendor boundary (`standards/vendor-boundaries.md`)

* Q1, the actor: the reconcile reusable already raises notices
  (`standing-defect`, `reviewer_outage:fleet`), and the console door admits it.
* Q2, the credentials: the sweep job already holds `id-token: write` and
  `BUREAU_READ_URL`, the two the notice client and the read door need.
* Q3, a retry: a re-run repeats the same raise, and the door answers `repeat`.
* Q4, the limits: the door refuses a key outside `^[A-Za-z0-9_.:-]{1,120}$`,
  and `reviewer-down:<repo>:DRE-<n>` is inside it.
* Q5, a crash mid-flow: a crash between the raise and the resolve leaves an
  open alert, which the next pass resolves on the same evidence.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from typing import NamedTuple

import bureau_read
import pipeline_notice

TAG = "crashed-review-alert:"
NOTICE_PREFIX = "reviewer-down"
SEVERITY = "WARN"
_CARD = re.compile(r"^DRE-\d+$")
#: What the title opens with, read back: `<card>: PR #<n> @<sha8> —`.
_TITLE = re.compile(r"^DRE-\d+: PR #(\d+) @([0-9a-f]{8}) —")


class Capped(NamedTuple):
    """One head that reached `_report_reviewer_down` this pass."""

    card: str
    pr: int
    head: str
    #: The newest crashed review run at the head; "" when it could not be read.
    run_id: str


def notice_key(repo_slug: str, card: str) -> str:
    return f"{NOTICE_PREFIX}:{repo_slug}:{card}"


def pr_url(repo: str, number: int) -> str:
    return f"https://github.com/{repo}/pull/{number}"


def run_url(repo: str, run_id: str) -> str:
    return f"https://github.com/{repo}/actions/runs/{run_id}"


def title(stuck: Capped) -> str:
    return (f"{stuck.card}: PR #{stuck.pr} @{stuck.head[:8]} — the review is stuck "
            "at its retry cap and nothing will re-run it")


def parse_title(text: str) -> tuple[int, str] | None:
    """(PR number, short head) off a title `title()` wrote, or None."""
    found = _TITLE.match(text or "")
    return (int(found.group(1)), found.group(2)) if found else None


def body(repo: str, stuck: Capped) -> str:
    short = stuck.head[:8]
    if stuck.run_id:
        run = f"The crashed run: {run_url(repo, stuck.run_id)}."
        command = f"gh run rerun {stuck.run_id} --failed --repo {repo}"
    else:
        run = ("The crashed run could not be read off the head; `gh run list "
               f"--commit {stuck.head} --repo {repo}` names it.")
        command = f"gh run rerun <run> --failed --repo {repo}"
    return (
        f"The review of {pr_url(repo, stuck.pr)} at head {short} crashed, and "
        f"the sweep's one automatic re-run crashed too. {run}\n\n"
        "This is not a code rejection: no verdict was posted, so the merge gate "
        "cannot fire on this commit, and the sweep will not retry it again.\n\n"
        "Once the reviewer can run again, re-run the crashed review — the "
        f"remedy used on 2026-11-01:\n\n    {command}\n\n"
        "A new commit on the pull request re-arms the review too. This alert "
        "resolves itself when the pull request closes, its head moves, or a "
        "verdict lands on this head."
    )


def _open_alerts(answer: dict, repo_slug: str) -> list[tuple[str, str]]:
    """(card, title) of this repo's open reviewer-down alerts, in the read's order."""
    prefix = f"pipeline_notice:{notice_key(repo_slug, '')}"
    found = []
    for row in answer.get("alerts") or []:
        kind = row.get("kind") if isinstance(row, dict) else None
        if isinstance(kind, str) and kind.startswith(prefix):
            card = kind[len(prefix):]
            if _CARD.match(card) and card not in [c for c, _ in found]:
                found.append((card, str(row.get("title") or "")))
    return found


def _recovered(pr: dict, short: str, bound: Callable[[dict], bool]) -> str | None:
    """The positive evidence that this review is no longer stuck, or None."""
    state = str(pr.get("state") or "OPEN").upper()
    if state != "OPEN":
        return f"the pull request is {state.lower()}"
    if not str(pr.get("headRefOid") or "").startswith(short):
        return f"its head moved past {short}"
    if bound(pr):
        return f"a verdict is bound to {short}"
    return None


def settle(capped: Iterable[Capped], repo_slug: str, unread: Iterable[int] = (), *,
           repo: str, prs: list[dict], read_pr: Callable[[int], dict],
           bound: Callable[[dict], bool]) -> list[str]:
    """Raise one alert per card `capped` names, resolve this repo's alerts
    whose review is no longer stuck, and return the lines printed.

    `repo` is `<owner>/<repo>`, `prs` the sweep's open-PR listing,
    `read_pr(number)` reads one pull request the listing does not hold (and
    raises when it cannot), `bound(pr)` is `reconcile.verdict_bound`, and
    `unread` the pull request numbers this pass could not read whole."""
    lines: list[str] = []

    def say(text: str) -> None:
        line = f"{TAG} {text}"
        print(line, flush=True)
        lines.append(line)

    by_card: dict[str, Capped] = {}
    for stuck in capped:
        by_card.setdefault(stuck.card, stuck)
    for card, stuck in by_card.items():
        try:
            outcome = pipeline_notice.raise_notice(
                notice_key(repo_slug, card), severity=SEVERITY, title=title(stuck),
                body=body(repo, stuck), action_url=pr_url(repo, stuck.pr),
                action_label="Open the pull request", fallback=None)
        except Exception as e:  # noqa: BLE001 — an alert never fails the sweep
            say(f"raise failed card={card} — {type(e).__name__}: {e}")
            continue
        say(f"{outcome} card={card}")

    try:
        answer = bureau_read.open_alerts()
    except bureau_read.ReadUnknown as e:
        say(f"open alerts unreadable — {e}; nothing resolved this pass")
        return lines
    unread = set(unread)
    listed = {p.get("number"): p for p in prs or []}
    for card, text in _open_alerts(answer, repo_slug):
        if card in by_card:
            continue  # still at the cap this pass: raised above, never resolved
        named = parse_title(text)
        if named is None:
            continue  # no PR to read evidence off — nothing to resolve on
        number, short = named
        if number in unread:
            say(f"card={card} PR #{number} unreadable — this pass holds only part "
                "of it; kept open")
            continue
        try:
            pr = listed.get(number) or read_pr(number)
            evidence = _recovered(pr, short, bound)
        except Exception as e:  # noqa: BLE001 — a failed read resolves nothing
            say(f"card={card} PR #{number} unreadable — {type(e).__name__}: {e}; "
                "kept open")
            continue
        if evidence is None:
            say(f"card={card} PR #{number} still stuck — kept open")
            continue
        outcome = pipeline_notice.resolve_notice(notice_key(repo_slug, card))
        say(f"{outcome} card={card} — {evidence}")
    return lines
