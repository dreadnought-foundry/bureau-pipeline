#!/usr/bin/env python3
"""The check before the proposal is posted — is any proposed card already done
or superseded? (DRE-4966)

The groomer reads each card's own description and nothing else about it. On
2026-09-26 that put DRE-2897 on the Planning list, although the evidence that
it was finished sat in three places the groomer never looked: a 2026-09-05
comment on the card saying it was superseded and not to be built from, PR #431,
and DRE-3230's description ("Supersedes the parentless DRE-2897"). The same
proposal offered to cancel DRE-3526 as superseded by DRE-4630 — a card still in
Intake that nobody had approved.

So before the proposal is posted this reads each card it would put in front of
the CEO against that evidence. **Deterministic, and no model call**: three
reads and a handful of anchored patterns, in seconds.

## What it reads, per card

The morning's Planning list plus `SPARE` cards after it, in proposal order:

  * **its comments** — one that DECLARES the card superseded, done, or not to
    be built (`_DECLARES`) is evidence, quoted;
  * **merged pull requests** — searched for the quoted `"DRE-N"` with the
    Bureau App token the Groom step carries as `GH_TOKEN` (DRE-4964's
    contract), per fleet owner. A merged PR that is FOR the card is evidence:
    its title names the card, or its body carries the card's Linear link or a
    closing line naming it (`_pr_is_for`). A body that only mentions the card
    is not — this card's own pull request names four cards it does not
    deliver, and each of them would be proposed for cancellation the next
    morning;
  * **other cards** — a card a Linear search finds, a `related` card, or a
    sibling under the same parent, whose text says it supersedes, replaces,
    absorbs or covers this one (`_covers`) and which is itself a real
    replacement (below).

The Cancel list's own rows are read the other way round: **a Cancel that says
"superseded by X" stands only if X is Done, a merged PR, or approved and in
flight.** Otherwise it is rejected, and the card stays on the Planning list
with the reason named. A replacement the check could not read does not stand
either — Cancel is the direction that is hard to undo.

## What it does with what it finds

A card with evidence moves to the Cancel list with the first piece of it as
the reason, and the next spare takes its slot — walked in proposal order, so a
spare the morning never reaches is recorded and NOT cancelled early (DRE-4727:
a card outside the set waits its turn). `groomer.verify_proposal` re-proposes
with the answer, so the proposal id covers the checked lists.

**A source it could not read is said, never passed off as clean.** Each card
carries the sources that were unread for it, a card with no evidence and an
unread source is `unread` rather than `clean`, and a Planning card the check
never reached is `not_checked`. The page says all three.

The record (`proposal["verification"]`) carries, per card, the verdict, the
source and the evidence, so the proof card and the audit read it rather than
repeating the reads.
"""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import groom_context  # noqa: E402 — the merged-PR reader DRE-4964 built
import planning_escalation  # noqa: E402 — the plain-English write guard

#: Cards read past the morning's Planning list, in proposal order — the slots a
#: failed card hands on are filled from these.
SPARE = 10

#: The three sources, in the order evidence is taken from them.
SOURCES = ("comments", "merged_prs", "other_cards")

#: How the page names each source.
SOURCE_NAMES = {
    "comments": "the card's comments",
    "merged_prs": "merged pull requests",
    "other_cards": "other cards",
}

#: The verdicts one record can carry. `cancel`, `clean` and `unread` are a
#: checked card's; the three `cancel-*` are a Cancel row's replacement read.
VERDICTS = ("cancel", "clean", "unread", "cancel-stands", "cancel-rejected",
            "cancel-kept")

#: Lanes a replacement card may be in and count as real. `Done` is done; the
#: rest are past Planning — a card only reaches them with a routing verdict,
#: which is what "approved" means on this board (`config/lane-contract.json`).
DONE = ("Done",)
IN_FLIGHT = ("Backlog", "Todo", "In Progress", "In Review")

#: GitHub search allows five boolean operators a query, so five cards a query.
SEARCH_CHUNK = 5

#: How much of a comment the reason quotes.
QUOTE_CHARS = 300

# One checked card's reads: its comments, its relations, its parent's children
# and the cards a Linear search for its number finds. ONE request per card,
# because the whole fleet shares one Linear budget. The search term is the
# number alone: `searchIssues` answers a bare `DRE-N` with that card and
# nothing else (read live on 2026-09-27 — DRE-2897's search returned only
# itself, while "2897" found DRE-3230 among twenty).
CARD_QUERY = """query($id: String!, $term: String!) {
  issue(id: $id) {
    identifier
    comments(first: 50) { nodes { body createdAt } }
    relations(first: 20) { nodes {
      type relatedIssue { identifier title description state { name } } } }
    inverseRelations(first: 20) { nodes {
      type issue { identifier title description state { name } } } }
    parent { identifier children(first: 50) { nodes {
      identifier title description state { name } } } }
  }
  searchIssues(term: $term, first: 25) { nodes {
    identifier title description state { name } } }
}"""

TARGET_QUERY = """query($id: String!) {
  issue(id: $id) { identifier state { name } }
}"""

_CARD = re.compile(r"\bDRE-\d+\b")
_PR_URL = re.compile(r"https://github\.com/([\w.-]+)/([\w.-]+)/pull/(\d+)")

# A comment DECLARES the card finished — a mention is not a declaration, the
# lesson `groomer._SUPERSEDED_LINE` and the blocker grammar both learned
# (DRE-2670). Read per line, after the line's own markdown lead (`**`, `>`,
# `-`) is stripped: "Superseded" must OPEN a line; the others are phrases no
# sentence about something else writes.
_DECLARES = (
    re.compile(r"^superseded\b", re.I),
    re.compile(r"\b(?:do not|don't) build from\b", re.I),
    re.compile(r"\bnot to be built\b", re.I),
    re.compile(r"\bnothing left to build\b", re.I),
    re.compile(r"\b(?:this|it)\s+(?:card\s+|defect\s+|work\s+|bug\s+)?"
               r"(?:is|was|has been)\s+already\s+"
               r"(?:done|fixed|shipped|built|delivered|merged)\b", re.I),
)
_LINE_LEAD = re.compile(r"^[\s*_>#-]+")
_SUPERSEDED_BY = re.compile(
    r"\bsuperseded\s+by\s*:?\s*\**\s*(https://github\.com/\S+/pull/\d+|DRE-\d+)",
    re.I)


def _ref(identifier: str) -> str:
    """`DRE-12` and never `DRE-123` or `XDRE-12`."""
    return rf"(?<![A-Za-z0-9-]){re.escape(identifier)}(?!\d)"


def _declares(body: str) -> bool:
    for line in (body or "").splitlines():
        text = _LINE_LEAD.sub("", line).replace("**", "")
        if any(p.search(text) for p in _DECLARES):
            return True
    return False


def _quote(body: str) -> str:
    text = " ".join((body or "").replace("**", "").split())
    return text if len(text) <= QUOTE_CHARS else text[:QUOTE_CHARS - 1] + "…"


def _covers(identifier: str, text: str) -> re.Match | None:
    """A sentence saying another card supersedes, replaces, absorbs or covers
    this one. Naming it is not enough: an investigation card lists the
    follow-ups it filed, and a Done one would cancel every one of them."""
    return re.search(
        rf"\b(?:supersed(?:es|ing)|replaces|absorbs|covers|folds in)\b"
        rf"[^.\n]{{0,60}}?{_ref(identifier)}", text or "", re.I)


# A full stop ends a sentence only where whitespace or the text's end follows
# it: the `.` inside `groom_verify.py` or `v1.2` does not, or a path would be
# cut short before `_plain` could see it.
_STOP = re.compile(r"\.(?=\s|\Z)|\n")


def _sentence(text: str, match: re.Match) -> str:
    """The whole sentence `_covers` matched in (DRE-5305): back to the
    nearest full stop or newline before the match, on to the nearest after
    it, the full stop kept. The line's own markdown lead is not part of it."""
    start = 0
    for stop in _STOP.finditer(text, 0, match.start()):
        start = stop.end()
    after = _STOP.search(text, match.end())
    end = len(text) if after is None else (
        after.end() if after.group() == "." else after.start())
    return _LINE_LEAD.sub("", text[start:end]).strip()


def _pr_is_for(identifier: str, item: dict) -> bool:
    """Is this merged PR FOR the card — not merely about it?"""
    if re.search(_ref(identifier), item.get("title") or ""):
        return True
    body = item.get("body") or ""
    if re.search(rf"/issue/{re.escape(identifier)}(?!\d)", body):
        return True
    return bool(re.search(
        rf"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?|implements?|card)\b"
        rf"\s*:?\s*\**\s*{_ref(identifier)}", body, re.I))


def _plain(text: str, fallback: str) -> str:
    """ONE guard for text the CEO reads (`planning_escalation.refusal`): a
    quote carrying code is replaced by a sentence saying where it is."""
    return text if planning_escalation.refusal(text) is None else fallback


# --------------------------------------------------------------------------- #
# the window                                                                   #
# --------------------------------------------------------------------------- #

def window(proposal: dict, spare: int = SPARE) -> tuple[list[str], list[str]]:
    """The Planning list in position order, and the next `spare` cards after
    it in the proposal's own sequence — a card the read declined has no slot
    to give and is skipped."""
    planning = [r["identifier"] for r in
                sorted(proposal["outcomes"]["now"], key=lambda r: r["position"])]
    spares = [r["identifier"] for r in proposal["sequence"]
              if r.get("outcome") == "not-now" and r.get("cycle") is not None]
    return planning, spares[:spare]


# --------------------------------------------------------------------------- #
# the reads                                                                    #
# --------------------------------------------------------------------------- #

def read_card(lops, identifier: str) -> dict:
    number = identifier.rsplit("-", 1)[-1]
    data = lops.gql(CARD_QUERY, {"id": identifier, "term": number})
    issue = (data or {}).get("issue")
    if not issue:
        raise LookupError(f"{identifier} was not found")
    return {"issue": issue,
            "search": ((data.get("searchIssues") or {}).get("nodes") or [])}


def _search(run, query: str) -> list[dict]:
    items: list[dict] = []
    page = 1
    while True:
        doc = groom_context._api(
            run, ["-X", "GET", "search/issues", "-f", f"q={query}",
                  "-f", f"per_page={groom_context.PR_SEARCH_PER_PAGE}",
                  "-f", f"page={page}"], "search/issues")
        found = doc.get("items") or []
        items.extend(found)
        read = page * groom_context.PR_SEARCH_PER_PAGE
        if (len(found) < groom_context.PR_SEARCH_PER_PAGE
                or read >= int(doc.get("total_count") or 0)
                or read >= groom_context.PR_SEARCH_CEILING):
            return items
        page += 1


def merged_mentions(identifiers: list[str], *, run=None,
                    owners=None) -> tuple[dict, dict, list[str]]:
    """`({card: [merged PR for it]}, {card: why the search was not read},
    [owner whose search answered])`.

    One search per owner per `SEARCH_CHUNK` cards, each card quoted. An owner
    the token's installation cannot see leaves every card unread — a search
    that could not look everywhere cannot say "nothing merged" — and the
    owners it could see are still searched, so evidence found there counts.

    The third value is the owners for which at least one search chunk
    answered (DRE-5317): `[]` when nothing was searched — no token, an
    installation that could not be read, or every search raising — which is
    the morning of 2026-09-29, and the one `apply` stops the post on.
    """
    found: dict[str, list[dict]] = {i: [] for i in identifiers}
    gaps: dict[str, str] = {}
    searched: list[str] = []
    if not identifiers:
        return found, gaps, searched
    if not (os.environ.get(groom_context.TOKEN_ENV) or "").strip():
        why = (f"no {groom_context.TOKEN_ENV} in the environment — the Groom "
               f"step hands the check the Bureau App token under that name")
        return found, {i: why for i in identifiers}, searched
    run = run or groom_context._gh_json
    try:
        owners = (list(owners) if owners is not None
                  else groom_context.fleet_owners())
        visible = groom_context.installed_owners(run)
    except Exception as e:  # noqa: BLE001 — an unread source is named, not fatal
        return found, {i: f"the installation could not be read: {e}"
                       for i in identifiers}, searched
    blind = [o for o in owners if o.lower() not in visible]
    if blind:
        why = ("the Bureau App token's installation cannot see "
               + ", ".join(blind))
        gaps.update({i: why for i in identifiers})
    for owner in owners:
        if owner in blind:
            continue
        for start in range(0, len(identifiers), SEARCH_CHUNK):
            chunk = identifiers[start:start + SEARCH_CHUNK]
            query = (" OR ".join(f'"{i}"' for i in chunk)
                     + f" is:pr is:merged user:{owner}")
            try:
                items = _search(run, query)
            except Exception as e:  # noqa: BLE001
                for i in chunk:
                    gaps.setdefault(i, f"the search of {owner} failed: {e}")
                continue
            if owner not in searched:
                searched.append(owner)
            for item in items:
                if not (item.get("pull_request") or {}).get("merged_at"):
                    continue
                for i in chunk:
                    if _pr_is_for(i, item) and item not in found[i]:
                        found[i].append(item)
    return found, gaps, searched


def lane_says(target: str, lane: str | None) -> tuple[bool, str]:
    """Is a card in `lane` a real replacement? `(True, why)` for Done or
    approved and in flight; `(False, why)` for anything else."""
    lane = lane or "an unknown lane"
    if lane in DONE:
        return True, f"{target} is Done"
    if lane in IN_FLIGHT:
        return True, f"{target} is approved and in {lane}"
    if lane in ("Canceled", "Duplicate"):
        return False, f"{target} was closed as {lane}, so nothing replaced it"
    return False, f"{target} is in {lane} and has not been approved"


class _Replacements:
    """Is X a real replacement — Done, a merged PR, or approved and in flight?
    Each target read once per check."""

    def __init__(self, lops, run):
        self.lops, self.run = lops, run
        self.seen: dict[str, tuple[bool, str]] = {}

    def __call__(self, target: str) -> tuple[bool, str]:
        if target not in self.seen:
            self.seen[target] = (self._pr(target) if target.startswith("http")
                                 else self._card(target))
        return self.seen[target]

    def _card(self, target: str) -> tuple[bool, str]:
        try:
            issue = (self.lops.gql(TARGET_QUERY, {"id": target}) or {}).get("issue")
        except Exception:  # noqa: BLE001 — unread, and so not a replacement
            return False, f"{target} could not be read this run"
        if not issue:
            return False, f"{target} could not be read this run"
        return lane_says(target, (issue.get("state") or {}).get("name"))

    def _pr(self, url: str) -> tuple[bool, str]:
        parts = _PR_URL.match(url)
        if not parts:
            return False, f"{url} is not a pull request the check can read"
        owner, repo, number = parts.groups()
        try:
            doc = groom_context._api(
                self.run or groom_context._gh_json,
                [f"repos/{owner}/{repo}/pulls/{number}"], "the pull request")
        except Exception:  # noqa: BLE001 — unread, and so not a replacement
            return False, f"the pull request {url} could not be read this run"
        if doc.get("merged_at"):
            return True, f"the pull request {url} is merged"
        return False, f"the pull request {url} is not merged"


# --------------------------------------------------------------------------- #
# evidence                                                                     #
# --------------------------------------------------------------------------- #

def comment_evidence(identifier: str, detail: dict, real) -> list[dict]:
    out = []
    for node in ((detail["issue"].get("comments") or {}).get("nodes") or []):
        body = node.get("body") or ""
        if not _declares(body):
            continue
        named = _SUPERSEDED_BY.search(body)
        if named and not real(named.group(1))[0]:
            continue
        quote = _quote(body)
        day = (node.get("createdAt") or "")[:10]
        when = f" ({day})" if day else ""
        out.append({
            "source": "comments", "quote": quote, "at": node.get("createdAt"),
            "text": _plain(f'a comment on the card{when} says: "{quote}"',
                           f"a comment on the card{when} says it is "
                           f"superseded or already done; its words are in "
                           f"the proposal record"),
        })
    return out


def pr_evidence(prs: list[dict]) -> list[dict]:
    return [{"source": "merged_prs", "url": item.get("html_url") or "",
             "title": item.get("title") or "",
             "text": f"already done — the merged pull request "
                     f"{item.get('html_url') or ''} is for this card"}
            for item in prs]


def card_evidence(identifier: str, detail: dict, real) -> list[dict]:
    issue = detail["issue"]
    candidates = list(detail.get("search") or [])
    for rel in ((issue.get("relations") or {}).get("nodes") or []):
        if rel.get("type") == "related":
            candidates.append(rel.get("relatedIssue") or {})
    for rel in ((issue.get("inverseRelations") or {}).get("nodes") or []):
        if rel.get("type") == "related":
            candidates.append(rel.get("issue") or {})
    parent = issue.get("parent") or {}
    candidates += ((parent.get("children") or {}).get("nodes") or [])

    out, seen = [], {identifier}
    for node in candidates:
        other = node.get("identifier")
        if not other or other in seen:
            continue
        seen.add(other)
        text = f"{node.get('title') or ''}\n{node.get('description') or ''}"
        said = _covers(identifier, text)
        if not said:
            continue
        # The lane the read already carries — no second request for it.
        lane = (node.get("state") or {}).get("name")
        ok, why = lane_says(other, lane) if lane else real(other)
        if not ok:
            continue
        # The reason names the card and quotes what it says, so the CEO can
        # tell "Supersedes DRE-N" from a looser match without opening it.
        status = why.removeprefix(f"{other} is ")
        quote = _quote(_sentence(text, said))
        out.append({"source": "other_cards", "card": other, "quote": quote,
                    "text": _plain(f'{other} ({status}) says: "{quote}"',
                                   f"{other} ({status}) says it supersedes "
                                   f"or covers this card; its words are in "
                                   f"the proposal record")})
    return out


# --------------------------------------------------------------------------- #
# the check                                                                    #
# --------------------------------------------------------------------------- #

def _cancel_target(row: dict) -> str | None:
    """The replacement a Cancel row names: the description's own `Superseded
    by:` target, else the first other card or pull request its reason names.
    """
    if row.get("superseded_by"):
        return row["superseded_by"]
    text = " ".join(str(row.get(k) or "") for k in ("evidence", "reason"))
    pr = _PR_URL.search(text)
    if pr:
        return pr.group(0)
    for ref in _CARD.findall(text):
        if ref != row.get("identifier"):
            return ref
    return None


def check(proposal: dict, *, lops, run=None, owners=None,
          spare: int = SPARE) -> dict:
    """Read the proposal's cards against the evidence; decide nothing about
    order. Returns what `groomer.propose(verified=…)` applies — `cancel`
    (card → reason) and `keep` (card → why its Cancel was rejected) — beside
    the per-card record and the unread sources."""
    planning, spares = window(proposal, spare)
    candidates = planning + spares
    real = _Replacements(lops, run)
    prs, pr_gaps, searched = merged_mentions(candidates, run=run,
                                             owners=owners)

    rows: list[dict] = []
    unread: dict[str, dict] = {}

    def gap(source: str, identifier: str, why: str) -> None:
        entry = unread.setdefault(source, {"cards": [], "why": why})
        entry["cards"].append(identifier)

    for identifier in candidates:
        evidence, missing = [], []
        try:
            detail = read_card(lops, identifier)
        except Exception as e:  # noqa: BLE001 — an unread source is named
            detail = None
            for source in ("comments", "other_cards"):
                gap(source, identifier, f"Linear did not answer: {e}")
                missing.append(source)
        if detail is not None:
            evidence += comment_evidence(identifier, detail, real)
        if identifier in pr_gaps:
            gap("merged_prs", identifier, pr_gaps[identifier])
            missing.append("merged_prs")
        evidence += pr_evidence(prs.get(identifier) or [])
        if detail is not None:
            evidence += card_evidence(identifier, detail, real)
        if evidence:
            verdict, source = "cancel", evidence[0]["source"]
        else:
            verdict, source = ("unread" if missing else "clean"), None
        rows.append({"identifier": identifier,
                     "list": "planning" if identifier in planning else "spare",
                     "verdict": verdict, "source": source,
                     "evidence": evidence,
                     "unread": sorted(missing, key=SOURCES.index),
                     "acted": False})

    # The walk: a failed card hands its slot to the next card in order, and
    # nothing past the last slot is acted on — it waits its turn.
    cancel: dict[str, str] = {}
    slots, filled = len(planning), 0
    for row in rows:
        if filled >= slots:
            break
        if row["verdict"] == "cancel":
            cancel[row["identifier"]] = row["evidence"][0]["text"]
            row["acted"] = True
        else:
            filled += 1

    keep: dict[str, str] = {}
    for dead in sorted(proposal["outcomes"]["dead"],
                       key=lambda r: r.get("position") or 0):
        identifier = dead["identifier"]
        target = _cancel_target(dead)
        if target is None:
            verdict, why = "cancel-kept", ("it names no replacement card or "
                                           "pull request the check can read")
        else:
            ok, why = real(target)
            verdict = "cancel-stands" if ok else "cancel-rejected"
            if not ok:
                keep[identifier] = (f"kept on the Planning list — the Cancel "
                                    f"said superseded by {target}, and {why}")
        rows.append({"identifier": identifier, "list": "cancel",
                     "verdict": verdict, "source": "replacement",
                     "evidence": [{"source": "replacement", "target": target,
                                   "text": why}],
                     "unread": [], "acted": verdict == "cancel-rejected"})

    return {"cards": rows, "cancel": cancel, "keep": keep, "unread": unread,
            "checked": [r["identifier"] for r in rows], "spare": spare,
            "merged_prs_searched": searched}


def unread_result(proposal: dict, why: str, *, spare: int = SPARE) -> dict:
    """What `check` would have answered had it read nothing: every card it
    would have read is `unread` on every source, and nothing moves.

    The answer for a check that FAILED rather than one that found a source
    unreadable — a morning with no proposal costs the CEO a day, and a
    proposal that says plainly it was not checked costs nothing that is not
    written on it. `merged_prs_searched` is `None`, never `[]`: a check that
    did not run searched nowhere for a different reason, and must not read
    as a token that could see nothing (DRE-5317).
    """
    planning, spares = window(proposal, spare)
    rows = [{"identifier": i,
             "list": "planning" if i in planning else "spare",
             "verdict": "unread", "source": None, "evidence": [],
             "unread": list(SOURCES), "acted": False}
            for i in planning + spares]
    ids = [r["identifier"] for r in rows]
    return {"cards": rows, "cancel": {}, "keep": {},
            "unread": {source: {"cards": list(ids), "why": why}
                       for source in SOURCES} if ids else {},
            "checked": ids, "spare": spare, "merged_prs_searched": None}


def settle(proposal: dict, result: dict) -> dict:
    """Write the check's record onto the proposal it produced.

    `not_checked` is every card on the final Planning list the check never
    read — the spares ran out — so the page can say so rather than let it
    pass for clean. The record is NOT in the id's digest: the id already
    covers the lists the check changed, and a quote that reads differently on
    a re-run must not retire an approval of the same lists.
    """
    checked = set(result["checked"])
    planning = [r["identifier"] for r in
                sorted(proposal["outcomes"]["now"], key=lambda r: r["position"])]
    # A Cancel the guard refused (DRE-5309) was not moved: `propose` kept the
    # card where the order put it and listed it under `cancels_refused`.
    refused = {r["identifier"] for r in proposal.get("cancels_refused") or []}
    proposal["verification"] = {
        "spare": result["spare"],
        "cards": result["cards"],
        "unread": result["unread"],
        "moved_to_cancel": [i for i in result["cancel"] if i not in refused],
        "cancels_rejected": [i for i in result["keep"]],
        "not_checked": [i for i in planning if i not in checked],
        "merged_prs_searched": result.get("merged_prs_searched"),
    }
    return proposal
