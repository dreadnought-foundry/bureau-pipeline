#!/usr/bin/env python3
"""A dependabot pull request's Linear card, and the join between the two (DRE-3665).

Everything the board tracks has a card. Dependabot pull requests did not: the
merge gate and the fix loop match `dependabot/*` beside `agent/*`, the critic
reviews them and the gate merges them — but the card id the rest of the
pipeline reads lives in the HEAD REF (`agent/DRE-<n>-<slug>`), and dependabot
cannot name its branch after a card. So the console's Pull Requests row read
"CARD —" for atlas #154, the journey had nothing to advance, and on merge
nothing went Done. The CEO, 2026-09-12: "Right now, our pen bots are not adding
cards to things. They should."

THE JOIN. The reconcile sweep (`reconcile.card_dependabot_prs`, fleet Linear
key) files ONE card per dependabot pull request and then PREPENDS one
machine-written line to the pull request's body:

    **Card:** DRE-1234 — filed by the reconcile sweep for this dependabot pull
    request (DRE-3665); linear-sync and the console read the card id here.

That line is the whole join. It is honoured ONLY on a `dependabot/*` head
authored by `dependabot[bot]`, and only on the first lines of the body — so a
`DRE-<n>` that appears inside dependabot's quoted third-party release notes is
never read as a card (`marker_card`), and a human pull request's body remains
prose, not provenance (DRE-2027: "card references in PR titles/bodies never
transition cards" stays true for every branch a person can name).

Its readers, each pinned by tests/test_dependabot_card.py:

  * `linear-sync.yml`'s Card → Done step — the fenced `DRE-3665` arm calls
    `dependabot_card.py card-from-pr` when the head ref carries no card.
  * `reconcile.card_dependabot_prs` — reads it back for idempotency (one card
    per pull request), and to close the card when the pull request closes.
  * agent-bureau's console derives a row's card from the head ref alone
    (`ingest._card_tag`, `pr_merge._card_for_pr`, `sources.prs_to_merge_events`)
    and is taught this line by its own sibling card; the pull request body it
    would read is the same one this module writes.

Why the body and not a renamed branch, an attachment or a card field: the
branch cannot be renamed; a Linear attachment or field is readable only by
asking Linear, which the console's ingest does per pull request row and the
merge event's shell step does not do at all. The body rides with the pull
request into every reader that already has it. Its one weakness — dependabot
REGENERATES a grouped pull request's title and body when the group changes,
wiping the line — is covered by the sweep: a dependabot pull request with no
marker is looked up in Linear by its URL (`linear_ops.find_by_pr_url`) before
any card is filed, so a wiped line is re-stamped, never re-filed.

Stdlib only, and no sibling imports at module level: the workflow runs the CLI
from a bare checkout, and `reconcile.py` cannot be imported without a live
environment.
"""

from __future__ import annotations

import os
import re
import sys

#: The label that says "automation filed this". Not `hand-built` — that label
#: means a PERSON does the work and no dispatched run is coming; this card's
#: work is a vendor bot's pull request that the pipeline reviews and merges
#: like any other. Read by `reconcile.automation_card`, which keeps these cards
#: out of the nudge loop (whose no-PR branch would requeue one into Todo and
#: dispatch an agent onto a dependency bump) and out of the WIP base.
LABEL = "automation"

#: The role label the card gate requires (`validate_card.missing` wants an
#: `agent:*` label on every card). A dependency bump is pipeline plumbing.
ROLE_LABEL = "agent:devops"

#: Where the card lands: the lane a carded pull request in review sits in.
#: Never Intake, Planning or Todo — there is nothing to plan or dispatch; the
#: pull request already exists and the critic and gate already handle it.
LANE = "In Review"

#: Dependabot's fixed branch namespace (`merge_gate.DEPENDABOT_BRANCH_PREFIX`,
#: `reconcile.DEPENDABOT_BRANCH_PREFIX` — the same literal, pinned equal).
BRANCH_PREFIX = "dependabot/"

#: The first lines of the pull request body the marker may sit on. The sweep
#: writes it as line 1; anything deeper is dependabot's own text (which quotes
#: third-party release notes verbatim) and is never read as a card.
MARKER_WINDOW_LINES = 3

_MARKER_PREFIX = "**Card:** "
_MARKER_RE = re.compile(r"^\*\*Card:\*\*\s+(DRE-\d+)\b", re.IGNORECASE)

#: Version lines dependabot writes at the top of every body, before the quoted
#: release notes: "Bumps X from A to B" / "Bumps the G group with N updates: …"
#: / "Updates `X` from A to B". These are the card's "ecosystem/group and the
#: versions"; the `<details>` blocks that follow are not.
_VERSION_LINE_RE = re.compile(r"^(Bumps|Updates)\b")
_VERSION_LINES_CAP = 24


def is_dependabot_login(login: str | None) -> bool:
    """True iff `login` is dependabot[bot]. GitHub surfaces a Bot login as
    "dependabot" (GraphQL), "dependabot[bot]" (REST / the Actions event) or
    "app/dependabot" (gh's bot marker) — the same normalization
    `reconcile.is_dependabot_pr` and `check_tdd_commits.is_dependabot_author`
    apply; tests pin the three to each other."""
    return (login or "").removeprefix("app/").removesuffix("[bot]") == "dependabot"


def is_dependabot_pr(head_ref: str | None, author_login: str | None) -> bool:
    """A dependabot-named head AND dependabot's authorship. A human's branch
    merely named `dependabot/...` is not dependabot's (merge_gate condition D)."""
    return (head_ref or "").startswith(BRANCH_PREFIX) and is_dependabot_login(author_login)


def marker_line(identifier: str) -> str:
    """The one line the sweep prepends. Its shape IS the join: change it and
    every reader changes with it (they all parse through `marker_card`)."""
    return (
        f"{_MARKER_PREFIX}{identifier} — filed by the reconcile sweep for this "
        "dependabot pull request (DRE-3665); linear-sync and the console read "
        "the card id here."
    )


def marker_card(body: str | None) -> str | None:
    """The card the body's marker names (upper-cased), or None.

    Read off the first MARKER_WINDOW_LINES lines only, anchored at line start.
    A `DRE-<n>` anywhere else — dependabot quotes release notes, and a vendor's
    changelog may say anything — is not a card."""
    for line in (body or "").replace("\r\n", "\n").split("\n")[:MARKER_WINDOW_LINES]:
        m = _MARKER_RE.match(line.strip())
        if m:
            return m.group(1).upper()
    return None


def card_from_pr(head_ref: str | None, author_login: str | None, body: str | None) -> str | None:
    """THE READER: the card a dependabot pull request is joined to, or None.

    None for anything that is not a dependabot pull request — a human's
    `fix/*` branch whose body says "part of DRE-99" gets exactly the DRE-2027
    answer it always got — and None for a dependabot pull request the sweep
    has not stamped yet."""
    if not is_dependabot_pr(head_ref, author_login):
        return None
    return marker_card(body)


def stamped_body(identifier: str, body: str | None) -> str:
    """The pull request body with the marker as its first line. Idempotent:
    an existing marker (same card or another) is replaced, never stacked."""
    lines = (body or "").replace("\r\n", "\n").split("\n")
    head = lines[:MARKER_WINDOW_LINES]
    kept = [ln for ln in head if not _MARKER_RE.match(ln.strip())] + lines[MARKER_WINDOW_LINES:]
    rest = "\n".join(kept).lstrip("\n")
    return marker_line(identifier) + "\n\n" + rest


def _one_line(text: str | None) -> str:
    return " ".join(str(text or "").split())


def card_title(repo_slug: str, pr_title: str | None) -> str:
    """`<slug>: <pull request title>` — the title prefix the card gate pairs
    with the `repo:` label (`validate_card.repo_title_mismatch`, DRE-3278)."""
    return f"{repo_slug}: {_one_line(pr_title) or 'dependency update'}"


def card_labels(repo_slug: str) -> list[str]:
    """Every label the card carries, `repo:<slug>` first. Deliberately NOT
    `hand-built` (see LABEL) and no `initiative:*` — a product's dependency
    bump is that product's work, and the label is read nowhere the sweep
    routes (DRE-2874)."""
    return [f"repo:{repo_slug}", LABEL, ROLE_LABEL]


def branch_facts(head_ref: str | None) -> tuple[str, str]:
    """(ecosystem, update) off `dependabot/<ecosystem>/[<dir>/]<update>` — the
    vendor's own encoding of what the pull request bumps."""
    parts = [p for p in (head_ref or "").split("/") if p]
    if len(parts) < 3 or parts[0] != BRANCH_PREFIX.rstrip("/"):
        return ("", "")
    return (parts[1], parts[-1])


def version_lines(body: str | None) -> list[str]:
    """Dependabot's own "Bumps … / Updates … from A to B" lines, read OUTSIDE
    the quoted `<details>` blocks (a grouped bump interleaves one "Updates"
    line and one block per dependency), whitespace-collapsed and capped."""
    out: list[str] = []
    depth = 0
    for line in (body or "").replace("\r\n", "\n").split("\n"):
        stripped = line.strip()
        if stripped.startswith("<details"):
            depth += 1
            continue
        if stripped.startswith("</details"):
            depth = max(0, depth - 1)
            continue
        if depth == 0 and _VERSION_LINE_RE.match(line):
            out.append(_one_line(line))
            if len(out) >= _VERSION_LINES_CAP:
                break
    return out


def card_body(pr: dict, repo_slug: str) -> str:
    """The card, in the plain English a person reads on the board. Carries
    the pull request URL delimited by `<…>` so `linear_ops.find_by_pr_url` can
    confirm an exact match (a `contains` search would read `.../pull/12` in
    `.../pull/123`)."""
    url = str(pr.get("url") or "")
    number = pr.get("number")
    head = str(pr.get("headRefName") or "")
    ecosystem, update = branch_facts(head)
    versions = version_lines(pr.get("body"))
    lines = [
        f"**Repo:** {repo_slug}",
        f"**Pull request:** <{url}>",
        "",
        "## What this is",
        "",
        f"Dependabot opened pull request #{number} — `{_one_line(pr.get('title'))}` "
        f"— on branch `{head}`."
        + (f" Ecosystem `{ecosystem}`, update `{update}`." if ecosystem else ""),
        "",
    ]
    if versions:
        lines += ["What it bumps, in dependabot's words:", ""]
        lines += [f"- {v}" for v in versions]
        lines += [""]
    lines += [
        "This card exists so the dependency bump is on the board like everything "
        "else: it opened with the pull request, the pull request's first line "
        "names it, and it closes when the pull request does.",
        "",
        "## What happens next",
        "",
        "- The critic reviews the pull request and the merge gate merges a green "
        "minor/patch bump on its own; a major, or an unprovable level, waits for "
        "a person (DRE-2039).",
        "- Merged → this card goes Done through the ordinary merge path.",
        "- Closed without merging (dependabot superseded it, or a person closed "
        "it) → the sweep cancels this card. A re-opened pull request keeps this "
        "same card; it stays Canceled until a merge moves it to Done.",
        "",
        "## Acceptance criteria",
        "",
        "- [ ] The pull request merges and this card goes Done on the merge.",
        "",
        "## Not in this card",
        "",
        "- Any other dependabot pull request — each one gets its own card.",
        "- Reviewing or fixing the bump: the critic and the merge gate do that "
        "on the pull request itself.",
    ]
    return "\n".join(lines) + "\n"


def cancel_note(pr_url: str) -> str:
    """What the card is told when its pull request closes unmerged."""
    return (
        f"🚫 Dependabot's pull request closed without merging: {pr_url} — card "
        "canceled by the reconcile sweep (DRE-3665). If the pull request is "
        "re-opened it keeps this card; a merge then moves it to Done."
    )


def main(argv: list[str]) -> int:
    """`card-from-pr`: print the joined card for the pull request described
    by HEAD_REF / PR_AUTHOR / PR_BODY, or nothing. Exit 0 either way — the
    caller's empty-CARD branch is the "no automatic Done" path."""
    if argv[:1] != ["card-from-pr"]:
        print("usage: dependabot_card.py card-from-pr  (reads HEAD_REF, PR_AUTHOR, PR_BODY)",
              file=sys.stderr)
        return 2
    card = card_from_pr(
        os.environ.get("HEAD_REF"), os.environ.get("PR_AUTHOR"), os.environ.get("PR_BODY")
    )
    if card:
        print(card)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
