#!/usr/bin/env python3
"""The `wrong-repo` class resolves itself by the repo label (DRE-6446).

DRE-3242 was built at agent-bureau because its `repo:` label said so; the
agent found the file lives only in bureau-pipeline, said so in its blocker,
changed the label itself, and the card was still skipped as blocked for three
days because nothing read the blocker again. This is the action module the
sweep's resolver (DRE-6508) imports by the name `config/blocker-classes.json`
gives the class. It changes no sweep code and posts nothing: the resolver
posts the receipt after a non-None return.

`resolve` reads three facts:

1. The repo the blocker run was dispatched at — the LAST
   `Run: https://github.com/<owner>/<repo>/actions/runs/<id>` URL on the
   newest marker, the one the poster appends; any owner, the repo lower-cased
   the way the workflows compute `REPO_SLUG`.
2. The slug the blocker names — the `repo: <slug>` line the poster keeps as
   the reason's FIRST line, or, with no such line, the ONE rail slug
   (`validate_card.VALID_SLUGS`) the reason mentions as a whole token other
   than the dispatched repo.
3. The card's lane and labels, read LIVE (`get_issue(fresh=True)`) right
   before deciding, never off `card["labels"]`.

and acts on one of three readings:

* A — the live labels carry no label for the dispatched repo and exactly one
  other `repo:` label: a person changed it, the blocker is spent. Nothing is
  written.
* B — the live labels carry the dispatched repo's label, the blocker names
  exactly one other rail slug, the title's `<slug>:` prefix does not disagree,
  and the thread carries no earlier wrong-repo relabel receipt: the label is
  swapped ADD FIRST, so a card is never left with no `repo:` label. A refused
  write propagates; a half-done swap reads as B again on the next pass and
  finishes itself (`add_label` is a no-op on a label the card carries).
* C — anything else is a person's call: None. One swap per card, so a card
  whose files span two repositories is never swapped back and forth.

A card no longer in Backlog raises `blocker_class.NotNow` before any write.
The card is never moved: the right repo's sweep promotes it on its own pass.
"""

from __future__ import annotations

import re

import blocker_class
import linear_ops
import validate_card

#: The resolver's receipt for this class's act, matched by its words alone.
RELABEL_RECEIPT = "class=wrong-repo action=relabeled"

_RUN = re.compile(r"Run: https://github\.com/[^/\s]+/([^/\s]+)/actions/runs/\d+")
_STAMP = re.compile(r"repo:\s*(\S+)", re.IGNORECASE)


def dispatched_repo(body: str | None) -> str | None:
    """The repo the run in the marker's LAST `Run:` URL was dispatched at,
    lower-cased, or None for a legacy marker that carries no run URL."""
    found = _RUN.findall(body or "")
    return found[-1].lower() if found else None


def mentions(text: str | None, slug: str) -> bool:
    """`slug` appears in `text` as a whole token — never inside
    `agent-bureau-demo` or the `.bureau-pipeline/` checkout path."""
    pattern = rf"(?<![\w.-]){re.escape(slug)}(?![\w-])"
    return re.search(pattern, text or "", re.IGNORECASE) is not None


def named_slug(reason: str | None, dispatched: str) -> str | None:
    """The one rail slug other than `dispatched` the blocker names, or None."""
    first = (reason or "").split("\n", 1)[0].strip()
    stamp = _STAMP.fullmatch(first)
    if stamp:
        slug = stamp.group(1).lower()
        return slug if slug in validate_card.VALID_SLUGS and slug != dispatched else None
    named = [slug for slug in sorted(validate_card.VALID_SLUGS)
             if slug != dispatched and mentions(reason, slug)]
    return named[0] if len(named) == 1 else None


def _thread(card: dict) -> list:
    """The card's comment bodies, oldest→newest — the whole thread when the
    window is partial, so a relabel receipt older than it is still read."""
    comments = card.get("comments")
    if linear_ops.window_is_partial(comments):
        return linear_ops.comment_bodies(card["identifier"], whole_thread=True)
    return [node.get("body") or "" for node in linear_ops.window_nodes(comments)]


def resolve(card: dict, reason: str, *, repo: str) -> tuple[str, str] | None:
    """`("relabeled", note)` when the blocker is spent or the label was
    swapped, None when it is a person's call. `repo` is the sweep's own; the
    dispatched repo is read off the run URL instead, because the run that
    wrote the blocker is the one that was sent to the wrong place."""
    identifier = card["identifier"]
    live = linear_ops.get_issue(identifier, fresh=True)
    lane = (live.get("state") or {}).get("name")
    if lane != "Backlog":
        raise blocker_class.NotNow(f"the card left Backlog — it is in {lane}")

    bodies = _thread(card)
    found = blocker_class.newest_marker(bodies)
    dispatched = dispatched_repo(found.body) if found else None
    if dispatched is None:
        return None

    names = [node.get("name") or "" for node in (live.get("labels") or {}).get("nodes") or []]
    slugs = set(validate_card._repo_label_slugs(names))
    others = sorted(slugs - {dispatched})
    if dispatched not in slugs:
        if len(others) != 1:
            return None
        return ("relabeled", f"already relabeled by a person — label is now "
                             f"repo:{others[0]}, changed after the run at {dispatched}")

    if any(RELABEL_RECEIPT in body for body in bodies):
        return None
    new = named_slug(reason, dispatched)
    if new is None:
        return None
    # A third repo label beside the two the swap touches is not ours to judge.
    if any(slug != new for slug in others):
        return None
    prefix = validate_card.title_repo_slug(live.get("title") or card.get("title") or "")
    if prefix is not None and prefix != new:
        return None

    old = dispatched
    linear_ops.add_label(card["identifier"], f"repo:{new}")
    linear_ops.remove_label(card["identifier"], f"repo:{old}")
    return ("relabeled", f"repo:{old} → repo:{new}, named by the blocker")
