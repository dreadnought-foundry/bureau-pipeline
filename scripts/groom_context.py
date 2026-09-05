#!/usr/bin/env python3
"""The groomer's context pack — what we are already doing (DRE-3150).

The groomer sequences Intake against the clock and the card's own text
(`groomer.py`). It has never read what is IN FLIGHT, so "is this worth doing
now" has been answered without the one fact that decides it: an epic already
running that this card belongs to, an initiative whose objective it serves, a
pull request that merged last week and did it already, a card cancelled a
fortnight ago for the reason this one repeats.

This module is that half of the question, and it is deliberately the boring
half: **rows in, pack out.** No Linear key, no `gh`, no clock of its own —
`pack()` is pure, so the judgement it feeds (`groom_judgement.py`) can be
tested over a 250-card population without a network and without a model. The
readers that FETCH those rows are a thin seam at the bottom of this file, each
one in its own try/except, because a source that cannot be read must degrade to
"we could not read this" and never to "there is nothing there"
(`standards/console-honesty.md` rule 2).

## What is in the pack

  * **The epics In Progress**, each with the first paragraph of its plan — the
    CEO-readable summary the standard already puts first
    (`standards/card-quality.md`).
  * **The initiatives** and their current objective lines.
  * **The merged pull requests of the last 14 days** — title, card, repo.
  * **The Done and Canceled cards of the last 30 days** — id, title, and the
    reason line where somebody wrote one.

## Bounded, and stated

Every section carries a cap. A section over its cap is truncated **newest
first** — the newest rows are the ones that decide a batch — and the cut is
recorded in `pack["truncated"]` AND written into the rendered prompt. A prompt
that silently drops the oldest half of a fortnight's merges tells the model
less than it thinks it is telling it, and the model has no way of knowing.
That is the same failure as `issues(first: 100)` with no cursor (DRE-2681),
one layer up: the reader's world quietly becomes page one.

CLI:

    python3 scripts/groom_context.py pack [--out pack.json]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess  # nosec B404 — a fixed-arg `gh` read, argv list, never a shell
import sys
import unicodedata
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import linear_ops  # noqa: E402

# --------------------------------------------------------------------------- #
# the shape of the pack                                                        #
# --------------------------------------------------------------------------- #

#: The four sections, in the order the prompt states them. Named once here:
#: the proposal's `judgement.pack` block is derived from this tuple
#: (`summary()`), so a section renamed here moves in both places or fails.
SECTIONS = ("epics_in_progress", "initiatives", "merged_prs", "closed_cards")

#: How far back each time-bounded section reaches, in days. The card's own
#: numbers (DRE-3150): a fortnight of merges is what "recently shipped" means
#: at this cadence, and a month of closures is what "we already decided this"
#: means.
MERGED_PR_DAYS = 14
CLOSED_CARD_DAYS = 30

#: The cap per section. Bounded because the pack rides in ONE prompt beside a
#: census of the whole population, and an unbounded pack is how that call stops
#: fitting on the day Intake is busiest. Loose rather than tight: a row too
#: many costs tokens, a row too few costs the judgement this exists for.
CAPS = {
    "epics_in_progress": 20,
    "initiatives": 10,
    "merged_prs": 40,
    "closed_cards": 60,
}

#: How much of a plan paragraph or an objective line survives into the prompt.
#: The first paragraph of a plan is a summary; a plan whose first paragraph is
#: a page is a plan that did not summarise, and the pack is not the place to
#: fix that.
PARAGRAPH_CHARS = 400

# A REASON opens its own line and names its target — the same anchored grammar
# `groomer.superseded_by` and `blocker_prose` use, and for the same reason: a
# bare substring match over prose read a dependency out of "neither depends on
# the other" and froze five cards for five days (DRE-2670). A mention is not a
# declaration.
_REASON_LINE = re.compile(
    r"^\s*(?:[-*+]\s*)?(?:\*\*)?\s*"
    r"(?:superseded\s+by|reason|closed\s+because|canceled\s+because|"
    r"cancelled\s+because)"
    r"\s*:?\s*(?:\*\*)?\s*(.+?)\s*$",
    re.I | re.M,
)

# A comment the pipeline wrote ABOUT a card, rather than a plan FOR it. The
# thread of a live epic opens with receipts — heartbeats (`⏳`), actor markers
# (`🤖`), routing verdicts (`🧭`), groom proposals (`🧺`) — and the plan is the
# first comment that is prose.
#
# Read off the leading character's Unicode category rather than off a list of
# markers: every receipt in this pipeline is anchored on a symbol, and a list
# would be a second copy of `pipeline_act`'s registry that nothing keeps
# current. `⏳` is U+23F3, nowhere near the emoji block a range would have
# covered — which is exactly how a hand-written range misses the marker that
# was added last.
_VERDICT_LEAD = re.compile(r"^\s*(?:VERDICT:|QA Critic|QA Verifier)")


def _is_machine_comment(body: str | None) -> bool:
    text = (body or "").strip()
    if not text:
        return True
    if _VERDICT_LEAD.match(text):
        return True
    return unicodedata.category(text[0]).startswith("S")


_CARD_REF = re.compile(r"\b([A-Z][A-Z0-9]*-\d+)\b")


class ContextError(RuntimeError):
    """A pack that cannot be composed. Raised rather than defaulted: a
    judgement run against a pack nobody could build is a judgement made against
    a blank page, and it would look exactly like a confident one."""


# --------------------------------------------------------------------------- #
# reading one row                                                              #
# --------------------------------------------------------------------------- #


def first_paragraph(text: str | None, *, width: int = PARAGRAPH_CHARS) -> str:
    """The first block of prose in `text`, as one line.

    Headings, horizontal rules and blank leaders are skipped: an epic whose
    plan opens `# Plan` has its summary on the line after, and a "first
    paragraph" that returned the heading would put the word `Plan` in front of
    the model as the whole of what that epic is doing.
    """
    lines = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "---", "===", "```", "|")):
            if lines:
                break
            continue
        lines.append(line)
    joined = " ".join(" ".join(lines).split())
    return joined if len(joined) <= width else joined[: width - 1] + "…"


def first_line(text: str | None, *, width: int = PARAGRAPH_CHARS) -> str:
    """The first prose LINE of `text` — what an initiative's current objective
    is written as. Not the paragraph: an objective that runs to a second line
    has said the objective on the first one and is elaborating on it."""
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "---", "===", "```", "|")):
            continue
        line = " ".join(line.split())
        return line if len(line) <= width else line[: width - 1] + "…"
    return ""


def plan_paragraph(row: dict) -> str:
    """The first paragraph of an epic's plan.

    Three places it can come from, in order of how directly they say "this is
    the plan": an explicit plan comment the caller selected, the epic's comment
    thread (the first comment that is prose rather than a receipt), and the
    description — whose first prose paragraph IS the plan summary by the
    card-quality standard.
    """
    for key in ("plan", "plan_comment"):
        found = first_paragraph(row.get(key))
        if found:
            return found
    for body in row.get("comments") or ():
        if _is_machine_comment(body):
            continue
        found = first_paragraph(body)
        if found:
            return found
    return first_paragraph(row.get("description"))


def reason_line(description: str | None) -> str | None:
    """What a closed card says it was closed FOR, or None.

    None covers "nobody wrote one", which is most of them. A guessed reason is
    worse than no reason: the model is being shown this so it can recognise a
    card it is about to recommend for the same fate.
    """
    for found in _REASON_LINE.findall(description or ""):
        text = " ".join(found.split())
        if text:
            return text if len(text) <= PARAGRAPH_CHARS else text[: PARAGRAPH_CHARS - 1] + "…"
    return None


def card_of(title: str | None, *, branch: str | None = None) -> str | None:
    """The card a pull request belongs to, off its title or head branch."""
    for text in (title, branch):
        match = _CARD_REF.search(text or "")
        if match:
            return match.group(1)
    return None


def _repo_of(row: dict) -> str:
    repo = row.get("repo")
    if isinstance(repo, str) and repo:
        return repo.split("/")[-1]
    named = ((row.get("repository") or {}) if isinstance(row.get("repository"), dict)
             else {}).get("nameWithOwner") or ""
    return named.split("/")[-1] or "(unknown repo)"


# --------------------------------------------------------------------------- #
# the pack                                                                     #
# --------------------------------------------------------------------------- #


def _moment(iso: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


def _within(iso: str | None, now: str, days: int) -> bool:
    """Is `iso` inside the last `days` of `now`?

    A row with no readable timestamp is OUT. The consequence is a pack that is
    slightly smaller than it could have been, which is the reversible answer —
    the other way round puts undated rows in front of the model as recent news.
    """
    moment, anchor = _moment(iso), _moment(now)
    if moment is None or anchor is None:
        return False
    return moment >= anchor - timedelta(days=days)


def _newest_first(rows: list[dict], key: str) -> list[dict]:
    return sorted(rows, key=lambda r: (_moment(r.get(key)) or _EPOCH), reverse=True)


_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _cap(name: str, rows: list[dict], caps: dict, truncated: dict) -> list[dict]:
    limit = caps.get(name)
    if limit is None or len(rows) <= limit:
        return rows
    truncated[name] = {"kept": limit, "of": len(rows)}
    return rows[:limit]


def pack(*, epics=(), initiatives=(), merged_prs=(), closed_cards=(),
         now: str | None = None, caps: dict | None = None,
         unread=()) -> dict:
    """The context pack, from rows. Pure: no key, no network, no clock.

    `now` anchors both windows, so a fixture is never at the mercy of the day
    the suite happens to run — the same reason `groomer.propose` takes one.
    """
    now = now or _now()
    caps = dict(CAPS if caps is None else caps)
    truncated: dict = {}

    epic_rows = [
        {"identifier": row.get("identifier"), "title": row.get("title") or "",
         "plan": plan_paragraph(row)}
        for row in epics
    ]
    initiative_rows = [
        {"name": row.get("name") or "",
         "objective": first_line(row.get("objective")
                                 or row.get("description"))}
        for row in initiatives
    ]
    pr_rows = [
        {"title": " ".join((row.get("title") or "").split()),
         "card": row.get("card") or card_of(row.get("title"),
                                            branch=row.get("headRefName")),
         "repo": _repo_of(row),
         "url": row.get("url") or "",
         "merged_at": row.get("merged_at") or row.get("mergedAt") or ""}
        for row in merged_prs
        if _within(row.get("merged_at") or row.get("mergedAt"), now,
                   MERGED_PR_DAYS)
    ]
    closed_rows = [
        {"identifier": row.get("identifier"), "title": row.get("title") or "",
         "state": ((row.get("state") or {}).get("name")
                   if isinstance(row.get("state"), dict) else row.get("state")),
         "reason": reason_line(row.get("description")),
         "closed_at": row.get("completedAt") or row.get("canceledAt")
         or row.get("closed_at") or ""}
        for row in closed_cards
        if _within(row.get("completedAt") or row.get("canceledAt")
                   or row.get("closed_at"), now, CLOSED_CARD_DAYS)
    ]

    built = {
        "generated_at": now,
        "windows": {"merged_prs": MERGED_PR_DAYS,
                    "closed_cards": CLOSED_CARD_DAYS},
        "caps": caps,
        "epics_in_progress": _cap("epics_in_progress", list(epic_rows), caps,
                                  truncated),
        "initiatives": _cap("initiatives", list(initiative_rows), caps,
                            truncated),
        "merged_prs": _cap("merged_prs", _newest_first(pr_rows, "merged_at"),
                           caps, truncated),
        "closed_cards": _cap("closed_cards",
                             _newest_first(closed_rows, "closed_at"), caps,
                             truncated),
        "truncated": truncated,
        # Sources this pack could not read at all. Named rather than rendered
        # as an empty section: "nothing merged this fortnight" and "we could
        # not ask GitHub" are different facts with different weights, and only
        # one of them should make a card look stale.
        "unread": sorted(unread),
    }
    return built


def summary(built: dict) -> dict:
    """The pack as the proposal records it: a count per section, and what was
    cut. The proposal is a comment the CEO reads and an artifact the sibling
    cards parse — neither wants the pack's whole text a second time."""
    out = {name: len(built.get(name) or ()) for name in SECTIONS}
    out["truncated"] = sorted(built.get("truncated") or {})
    return out


# --------------------------------------------------------------------------- #
# the pack, as the model reads it                                              #
# --------------------------------------------------------------------------- #


def _truncation_note(built: dict, name: str) -> str:
    cut = (built.get("truncated") or {}).get(name)
    if not cut:
        return ""
    return (f" (showing the newest {cut['kept']} of {cut['of']} — the rest is "
            f"not in this prompt)")


def render(built: dict) -> str:
    """The pack as prompt text. Every cut is stated, never silent."""
    unread = set(built.get("unread") or ())
    windows = built.get("windows") or {}
    w = []
    add = w.append
    add("## What we are already doing")
    add("")
    for name, heading in (
        ("epics_in_progress", "Epics in progress"),
        ("initiatives", "Initiatives and their current objectives"),
        ("merged_prs",
         f"Merged in the last {windows.get('merged_prs', MERGED_PR_DAYS)} days"),
        ("closed_cards",
         f"Closed or cancelled in the last "
         f"{windows.get('closed_cards', CLOSED_CARD_DAYS)} days"),
    ):
        rows = built.get(name) or []
        add(f"### {heading}{_truncation_note(built, name)}")
        add("")
        if name in unread:
            add(f"- This section could not be read this run ({name}), so treat "
                f"it as unknown rather than as empty.")
        elif not rows:
            add("- Nothing.")
        else:
            for row in rows:
                add("- " + _row_line(name, row))
        add("")
    return "\n".join(w)


def _row_line(name: str, row: dict) -> str:
    if name == "epics_in_progress":
        return (f"{row.get('identifier')} — {row.get('title')}"
                + (f" · {row['plan']}" if row.get("plan") else ""))
    if name == "initiatives":
        return (f"{row.get('name')}"
                + (f" — {row['objective']}" if row.get("objective") else ""))
    if name == "merged_prs":
        return (f"{row.get('card') or '(no card)'} · {row.get('repo')} — "
                f"{row.get('title')}")
    return (f"{row.get('identifier')} [{row.get('state')}] — {row.get('title')}"
            + (f" · reason: {row['reason']}" if row.get("reason") else ""))


# --------------------------------------------------------------------------- #
# the readers — the only part that touches a network                           #
# --------------------------------------------------------------------------- #

# Every query below is a row `linear_ops` already reads; they live here so the
# pure builder above stays the thing the tests drive. Each reader is called
# inside its own try/except by `read_pack`, so ONE unreachable source degrades
# to a named gap and never to a groomer that cannot run.

EPICS_QUERY = """query($states: [String!]!, $after: String) {
  issues(first: 50, after: $after, filter: {
    team: {key: {eq: "DRE"}}, state: {name: {in: $states}}
  }) {
    nodes { identifier title description state { name }
            children(first: 1) { nodes { id } } }
    pageInfo { hasNextPage endCursor }
  }
}"""

CLOSED_QUERY = """query($since: DateTimeOrDuration!, $after: String) {
  issues(first: 100, after: $after, filter: {
    team: {key: {eq: "DRE"}},
    state: {type: {in: ["completed", "canceled"]}},
    updatedAt: {gt: $since}
  }) {
    nodes { identifier title description state { name }
            completedAt canceledAt }
    pageInfo { hasNextPage endCursor }
  }
}"""

INITIATIVES_QUERY = """query {
  initiatives(first: 50) { nodes { name description status } }
}"""

# Only merges, only the window, only this org. `gh search prs` is one call for
# every repo in the fleet — a per-repo `gh pr list` would be one call per repo
# and the same answer.
PR_SEARCH_LIMIT = "100"


def _since(now: str, days: int) -> str:
    anchor = _moment(now) or datetime.now(timezone.utc)
    return (anchor - timedelta(days=days)).isoformat().replace("+00:00", "Z")


def read_epics(lops) -> list[dict]:
    """The epics in flight, with their plan paragraph and comment thread.

    An epic is a card WITH CHILDREN — Linear-native parent/child, never a label
    (`standards/card-quality.md`) — so the child count decides it here rather
    than a title convention.
    """
    import plan_critic

    nodes = lops.gql_paged(
        EPICS_QUERY, {"states": list(plan_critic.IN_FLIGHT_EPIC_STATES)})
    out = []
    for node in nodes:
        if not ((node.get("children") or {}).get("nodes")):
            continue
        out.append({
            "identifier": node.get("identifier"),
            "title": node.get("title") or "",
            "description": node.get("description") or "",
            "comments": lops.comment_bodies(node.get("identifier")),
        })
    return out


def read_initiatives(lops) -> list[dict]:
    nodes = (lops.gql(INITIATIVES_QUERY).get("initiatives") or {}).get("nodes") or []
    return [{"name": n.get("name") or "", "description": n.get("description") or ""}
            for n in nodes]


def read_closed_cards(lops, *, now: str, days: int = CLOSED_CARD_DAYS) -> list[dict]:
    return lops.gql_paged(CLOSED_QUERY, {"since": _since(now, days)})


def read_merged_prs(*, now: str, org: str = "dreadnought-foundry",
                    days: int = MERGED_PR_DAYS, run=None) -> list[dict]:
    """The org's merged pull requests inside the window, via `gh`.

    Read-path only and LOUD: a non-zero exit raises, so `read_pack` records the
    section as unread instead of handing the model an empty fortnight.
    """
    run = run or _gh_json
    since = (_since(now, days) or "")[:10]
    out = run([
        "search", "prs", "--owner", org, "--merged", "--merged-at", f">={since}",
        "--limit", PR_SEARCH_LIMIT, "--json", "title,url,repository,createdAt",
    ])
    try:
        rows = json.loads(out or "[]")
    except ValueError as e:
        raise ContextError(f"gh search prs returned unreadable JSON: {e}") from e
    # `gh search prs` does not serve mergedAt; the window is already applied by
    # the query, so the row's own createdAt only orders the newest-first cut.
    for row in rows:
        row.setdefault("merged_at", row.get("createdAt") or "")
    return rows


def _gh_json(args: list[str]) -> str:
    done = subprocess.run(  # nosec B603 B607 — fixed-arg gh call, shell=False
        ["gh", *args], capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise ContextError(
            f"gh {' '.join(args)} failed rc={done.returncode}: "
            f"{done.stderr.strip()[:400]}")
    return done.stdout


def read_pack(lops=None, *, now: str | None = None, org: str | None = None,
              run=None) -> dict:
    """Fetch every source and build the pack. A source that fails is NAMED."""
    lops = lops or linear_ops
    now = now or _now()
    org = org or (os.environ.get("REPO") or "dreadnought-foundry/x").split("/")[0]
    rows: dict = {name: [] for name in SECTIONS}
    unread: list[str] = []
    for name, reader in (
        ("epics_in_progress", lambda: read_epics(lops)),
        ("initiatives", lambda: read_initiatives(lops)),
        ("merged_prs", lambda: read_merged_prs(now=now, org=org, run=run)),
        ("closed_cards", lambda: read_closed_cards(lops, now=now)),
    ):
        try:
            rows[name] = reader()
        except Exception as e:  # noqa: BLE001 — an unreadable source is a gap
            print(f"groom context: {name} could not be read: {e}",
                  file=sys.stderr)
            unread.append(name)
    return pack(epics=rows["epics_in_progress"], initiatives=rows["initiatives"],
                merged_prs=rows["merged_prs"], closed_cards=rows["closed_cards"],
                now=now, unread=unread)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p_pack = sub.add_parser("pack", help="build the context pack and print it")
    p_pack.add_argument("--out", help="write the pack JSON here")
    args = parser.parse_args(argv)

    built = read_pack()
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(built, fh, indent=2)
        print(f"wrote {args.out}")
    print(render(built))
    return 0


if __name__ == "__main__":                                  # pragma: no cover
    sys.exit(main())
