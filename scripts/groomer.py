#!/usr/bin/env python3
"""The groomer — it sequences the whole Intake population into cycles, and the
CEO approves each batch before anything leaves (DRE-2683).

Nothing else in the system answers *how much, in what order, all at once*. The
critic answers whether one card can be built — the expensive question, asked one
card at a time. Planning (DRE-2719) answers whether one card is still wanted.
Both read a single card. **A batch is not a list of individually-good cards**,
and the facts that decide a batch only exist BETWEEN cards: two cards editing one
file, eleven children of one epic that belong in one cycle, a repo that waits
three months because another repo goes first.

The independent Forms review (DRE-2649) found file collisions nobody had
spotted — cards scheduled as if independent that both land in
`Thread.tsx` — and something reading one card at a time structurally cannot see
that. This module is the reader that sees the set.

## What it does, in order

  1. **Reads the whole lane.** Paginated (`linear_ops.gql_paged`). The sweep's
     `issues(first: 100)` with no cursor made its world the first 100 rows of a
     226-card Backlog, and WHICH rows it never saw was decided by Linear's
     default ordering (DRE-2681). A groomer that sequences page one has
     sequenced nothing, so completeness is asserted: every card in the
     population comes out carrying exactly one outcome.
  2. **Groups into units.** An epic and its children are ONE unit. Classifying
     eleven children of one Forms epic in three separate batches spreads one
     deliverable across three cycles for no reason, so the epic — not the
     card — is the atom of cycle assignment.
  3. **Finds the collisions.** Two cards citing the same file become an ORDER
     between those two cards, reported with the file that caused it.
  4. **Sequences.** Portico first (the business priority), subject to the
     constraints above, deterministically.
  5. **Assigns cycles.** Linear's own primitive — cycles are enabled and cycle
     11 is running, so "which cycle" is expressible today without inventing a
     container.
  6. **Proposes.** The batch, its order, what is deferred to when, what is
     recommended dead and what replaced it, and — said out loud rather than
     discovered — which repos wait and roughly how long.

## Three outcomes, and only the first one moves

`now`, `not-now`, `dead`. **"Not now" is first-class**: a card can be
well-formed, wanted, and correctly left alone for a month, and without a "later"
the only way to say it is to say "no". **"Dead" is a recommendation and never an
action** — cancelling is destructive and belongs to the operator. Every dead
recommendation names the card or merged PR that replaced it, because a
recommendation nobody can check is one nobody should act on. In the 2026-08-22
sweep the recommendation, the decision and the execution were three separate
steps, and the executing agent caught an error in its own brief precisely
because it was working from an explicit list rather than its own judgement.

## The approval gate

`propose` writes nothing. `drain` moves the approved batch out of Intake and
into Planning, and refuses unless the CEO has approved THIS batch: the proposal
id is derived from the batch's own contents, so a population that moved
produces a different id and the old approval stops applying — the same
sha-binding idea the merge gate uses for a verdict. An approval written by the
pipeline's own Linear identity is refused: a gate the proposer can pass by
itself is not a gate.

The cadence is D5, approved 2026-08-23: **on demand, until the groomer's
judgement has been audited.** The cost is stated rather than hidden — on demand
means it runs when someone remembers, and this programme's thesis is that
anything relying on remembering eventually does not happen. Revisit once the
calls have been checked against a real batch.

CLI:

    python3 scripts/groomer.py census  [--lane Intake]
    python3 scripts/groomer.py propose [--lane Intake] [--capacity 20]
                                       [--batch-cycles 1] [--priority portico]
                                       [--out proposal.json] [--post DRE-N]
    python3 scripts/groomer.py drain   --card DRE-N [same shaping flags]

`drain` re-derives the proposal from live state and acts only if the approval
on the card names the batch it just derived.
"""

from __future__ import annotations

import argparse
import hashlib
import heapq
import itertools
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import linear_ops  # noqa: E402

# --------------------------------------------------------------------------- #
# vocabulary                                                                   #
# --------------------------------------------------------------------------- #

# The three answers the groomer is allowed to give. Held here, in one place, so
# "later" cannot quietly collapse into "no": the moment those are the same
# answer, Intake is a pass/fail funnel again.
OUTCOMES = {
    "now": (
        "In the approved batch. Carries a cycle and a position in it, and it "
        "is the only outcome that moves a card."
    ),
    "not-now": (
        "Wanted, and deliberately not this batch. Names the cycle it is "
        "reconsidered in — this is 'later', and it is not 'no'."
    ),
    "dead": (
        "Recommended for cancellation, and never cancelled here. Names the "
        "card or merged PR that superseded it; the operator decides and the "
        "operator executes."
    ),
}

# The lane the drain writes into: Intake's exit is a classification, and
# Planning is what produces one (DRE-2719).
DRAIN_TO = "Planning"

# Terminal lanes the drain refuses outright. The groomer recommends; it does
# not cancel. Stated as data so the refusal is testable rather than implied by
# the absence of code.
NEVER_WRITES = ("Canceled", "Duplicate", "Done")

MARK = "🧺"
PROPOSAL_TAG = "groom-proposal"
APPROVAL_TAG = "groom-approved"

# Said in the proposal AND in docs/groomer.md, from one string, because
# somebody will otherwise read cycle assignment as a return to sprint planning.
CYCLE_IS_NOT_SPRINT_PLANNING = (
    "Assigning cards to cycles is not a return to sprint planning. The cycle is "
    "the OKR heartbeat — a reporting rhythm, not a capacity commitment — and it "
    "still reports what moved. What the groomer needs from it is a native "
    "container for an ORDER, which Linear already has and nobody has to build."
)

# Portico is the business priority. Everything else follows, alphabetically, so
# the sequence groups a repo's work rather than interleaving three of them.
REPO_PRIORITY = ("portico",)
DEFAULT_CAPACITY = 20
DEFAULT_CYCLE_DAYS = 14
NO_REPO = "(no repo label)"

# A path cited by more than this many cards is REFERENCE, not ownership: a
# branch-rule banner naming `.github/workflows/linear-sync.yml` sits on nineteen
# live cards and none of them edits it, and treating that as a collision
# serialises the whole batch on a boilerplate line.
#
# Tuned against the live population on 2026-08-29, and deliberately loose. At 5
# it discarded `responses_lib.ts` (8 cards) — a file the Forms review named as a
# REAL collision — so a tight threshold buys a shorter report by hiding the
# findings this exists to make. A false collision costs one ordering constraint;
# a missed one costs a merge conflict.
BOILERPLATE_THRESHOLD = 12


class NotApproved(RuntimeError):
    """The batch has no CEO approval, so nothing leaves Intake. Raised BEFORE
    any write: a gate that refuses after moving three cards is not a gate."""


class WillNotCancel(RuntimeError):
    """The drain was pointed at a terminal lane. The groomer recommends and
    never cancels — cancelling is the operator's, as a separate step."""


# --------------------------------------------------------------------------- #
# reading the population                                                       #
# --------------------------------------------------------------------------- #

# The lane travels as a VARIABLE, never interpolated into the query text, and
# the query declares $after / selects pageInfo because `gql_paged` refuses one
# that cannot paginate.
POPULATION_QUERY = """query($lane: String!, $after: String) {
  issues(first: 100, after: $after, filter: {state: {name: {eq: $lane}}}) {
    nodes {
      identifier title description createdAt
      state { name }
      labels { nodes { name } }
      parent { identifier title }
      project { name }
      cycle { number }
      inverseRelations(first: 20) { nodes {
        type issue { identifier state { name } }
      } }
    }
    pageInfo { hasNextPage endCursor }
  }
}"""

CYCLES_QUERY = """query {
  cycles(first: 50) { nodes { id number startsAt endsAt completedAt } }
}"""

SET_CYCLE = """mutation($id: String!, $input: IssueUpdateInput!) {
  issueUpdate(id: $id, input: $input) { success }
}"""


def read_population(lops, lane: str = "Intake") -> list[dict]:
    """Every card in `lane`, followed to exhaustion."""
    return lops.gql_paged(POPULATION_QUERY, {"lane": lane})


def read_cycles(lops, *, now: str | None = None) -> list[dict]:
    """The cycles a batch may be placed into: the ones that have not started.

    The running cycle is excluded on purpose — dropping a freshly approved batch
    into a cycle that is already half over reports work as belonging to a period
    it could not have been done in.
    """
    now = now or _now()
    out = []
    for node in (lops.gql(CYCLES_QUERY)["cycles"]["nodes"] or []):
        if node.get("completedAt") or (node.get("startsAt") or "") <= now:
            continue
        out.append({"number": node["number"], "id": node["id"],
                    "startsAt": node.get("startsAt"), "endsAt": node.get("endsAt")})
    return sorted(out, key=lambda c: c["number"])


def repo_of(card: dict) -> str:
    for label in ((card.get("labels") or {}).get("nodes") or []):
        name = label.get("name") or ""
        if name.startswith("repo:"):
            return name.split(":", 1)[1]
    return NO_REPO


def census(cards: list[dict]) -> dict:
    return dict(sorted(Counter(repo_of(c) for c in cards).items(),
                       key=lambda kv: (-kv[1], kv[0])))


# --------------------------------------------------------------------------- #
# supersession — read, never inferred                                          #
# --------------------------------------------------------------------------- #

# A DECLARATION opens its own line and names its target. The blocker line learned
# this the hard way: a bare substring match over prose read a dependency out of
# the sentence "neither depends on the other" and froze five cards for five days
# (DRE-2670). A mention is not a declaration.
_SUPERSEDED_LINE = re.compile(
    r"^\s*(?:[-*+]\s*)?(?:\*\*)?\s*superseded\s+by\s*:?\s*(?:\*\*)?\s*(.+?)\s*$",
    re.I | re.M,
)
# A card SAYING it is superseded, near the start of a line — the shape of a
# declaration someone wrote about this card, as opposed to the word appearing
# deep inside a paragraph about something else (a card quoting "none marked
# superseded" is discussing ADRs, not itself).
_MENTIONS_SUPERSESSION = re.compile(r"^.{0,40}?\bsupersed(?:e|es|ed|ing)\b", re.I | re.M)
_CARD_REF = re.compile(r"\b(DRE-\d+)\b")
_PR_URL = re.compile(r"https://github\.com/\S+/pull/\d+")


def superseded_by(description: str | None) -> str | None:
    """The card or merged PR a `Superseded by:` line names, or None.

    None covers both "nothing was declared" and "something was declared and
    named nothing" — the second is reported separately (`unstated_supersessions`)
    rather than guessed at, because a dead recommendation nobody can check is a
    recommendation nobody should act on.
    """
    for line in _SUPERSEDED_LINE.findall(description or ""):
        pr = _PR_URL.search(line)
        if pr:
            return pr.group(0)
        card = _CARD_REF.search(line)
        if card:
            return card.group(1)
    return None


def supersession_gap(description: str | None) -> bool:
    """The card talks about being superseded but names nothing checkable."""
    text = description or ""
    return bool(_MENTIONS_SUPERSESSION.search(text)) and superseded_by(text) is None


# --------------------------------------------------------------------------- #
# collisions — the fact that only exists between two cards                     #
# --------------------------------------------------------------------------- #

_EXTENSIONS = (
    "ts|tsx|js|jsx|mjs|cjs|py|rb|go|java|php|vue|svelte|css|scss|html|md|"
    "json|ya?ml|toml|ini|cfg|sql|sh|tf"
)
# Backticked only: a path in prose is usually a description, a path in code
# ticks is a declaration. Trailing line references (`render.ts:341`) are kept
# out of the name.
_FILE_REF = re.compile(rf"`([A-Za-z0-9_@./-]+\.(?:{_EXTENSIONS}))(?::[\d,\s:-]*)?`")


def file_references(description: str | None) -> set[str]:
    """The files a card says it touches, by BASENAME.

    Basenames because cards cite the same file at different depths — one writes
    `Thread.tsx`, the next `rails/CommentsRail/Thread.tsx`. Comparing full paths
    would miss the collision that costs a merge conflict.
    """
    return {ref.split("/")[-1] for ref in _FILE_REF.findall(description or "")}


def blockers_of(card: dict) -> set[str]:
    """Cards that must come before this one: formal `blocks` relations plus the
    description's own `Blocked by:` declaration (parsed by linear_ops, so both
    readers agree on what counts as a declaration)."""
    found = set()
    for rel in ((card.get("inverseRelations") or {}).get("nodes") or []):
        if rel.get("type") == "blocks":
            issue = rel.get("issue") or {}
            if (issue.get("state") or {}).get("name") not in ("Done", "Canceled",
                                                              "Duplicate"):
                found.add(issue.get("identifier"))
    found |= set(linear_ops.parse_blocked_by(card.get("description") or ""))
    found.discard(card.get("identifier"))
    parent = (card.get("parent") or {}).get("identifier")
    found.discard(parent)                    # an epic never blocks its own child
    return {f for f in found if f}


def collision_report(cards: list[dict], *,
                     threshold: int = BOILERPLATE_THRESHOLD) -> dict:
    """Every pair of cards that names the same file, with a direction.

    Also returns what it could NOT see: the paths it discarded as boilerplate
    (with their counts) and the cards that name no files at all. Five of the
    eight collisions DRE-2649 found are invisible to this method because the
    cards name no files — a coverage gap that is stated is one somebody can
    close, and one that is silent is the whole failure this card exists for.
    """
    refs = {c["identifier"]: file_references(c.get("description")) for c in cards}
    by_id = {c["identifier"]: c for c in cards}
    counts: Counter = Counter()
    for names in refs.values():
        counts.update(names)
    boilerplate = {name: n for name, n in counts.items() if n > threshold}

    pairs = []
    for a, b in itertools.combinations(sorted(refs, key=_card_sort_key), 2):
        # SAME REPO ONLY. A shared basename across two repositories is not a
        # collision — `package.json` in portico and `package.json` in deltasolv
        # are different files that can never conflict. Measured live on
        # 2026-08-29: cross-repo matches on `CLAUDE.md`, `repo-map.json` and
        # `deploy.sh` pulled an agent-bureau card ahead of most of Portico,
        # breaking the one ordering rule the batch has.
        if repo_of(by_id[a]) != repo_of(by_id[b]):
            continue
        shared = (refs[a] & refs[b]) - set(boilerplate)
        if not shared:
            continue
        before, after = _order_of(by_id[a], by_id[b])
        pairs.append({"before": before, "after": after, "files": sorted(shared),
                      "why": _collision_why(by_id[before], by_id[after], shared)})
    return {
        "pairs": pairs,
        "boilerplate": dict(sorted(boilerplate.items())),
        "unreadable": sorted((i for i, names in refs.items() if not names),
                             key=_card_sort_key),
    }


def _order_of(a: dict, b: dict) -> tuple[str, str]:
    """Which of two colliding cards goes first. A recorded relation decides it;
    otherwise the older card, which is arbitrary but explicit — the point is
    that SOME order exists and is written down with its reason."""
    if b["identifier"] in blockers_of(a):
        return b["identifier"], a["identifier"]
    if a["identifier"] in blockers_of(b):
        return a["identifier"], b["identifier"]
    key = (_created(a), _card_sort_key(a["identifier"]))
    other = (_created(b), _card_sort_key(b["identifier"]))
    return ((a["identifier"], b["identifier"]) if key <= other
            else (b["identifier"], a["identifier"]))


def _collision_why(before: dict, after: dict, shared: set[str]) -> str:
    files = ", ".join(sorted(shared))
    if after["identifier"] in blockers_of(before) or \
            before["identifier"] in blockers_of(after):
        return f"both touch {files}; a recorded blocks relation sets the order"
    return f"both touch {files}; older card first, and the order is recorded"


# --------------------------------------------------------------------------- #
# units — the epic is the atom                                                 #
# --------------------------------------------------------------------------- #

def units(cards: list[dict]) -> list[dict]:
    """Cards grouped into the things a cycle is filled with: an epic with all
    of its children present, or a single parentless card."""
    grouped: dict[str, list[dict]] = {}
    for card in sorted(cards, key=lambda c: _card_sort_key(c["identifier"])):
        parent = (card.get("parent") or {}).get("identifier")
        grouped.setdefault(parent or card["identifier"], []).append(card)

    out = []
    for key, members in grouped.items():
        # The first member that HAS a parent, not the first member: when the
        # epic card is itself in the lane it joins its own unit — which is
        # right, it should move with its children — and it is the one card in
        # the group with no parent to read the epic off.
        epic = next(((m.get("parent") or {}).get("identifier") for m in members
                     if (m.get("parent") or {}).get("identifier")), None)
        repos = Counter(repo_of(c) for c in members)
        repo = sorted(repos.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        out.append({
            "key": key,
            "epic": epic,
            "repo": repo,
            "created": min(_created(c) for c in members),
            "cards": [c["identifier"] for c in members],
        })
    return sorted(out, key=lambda u: (u["created"], u["key"]))


# --------------------------------------------------------------------------- #
# sequencing                                                                   #
# --------------------------------------------------------------------------- #

def _repo_rank(cards: list[dict], priority) -> dict:
    rest = sorted({repo_of(c) for c in cards} - set(priority))
    return {name: i for i, name in enumerate(list(priority) + rest)}


def _break_cycles(keys: list[str], edges: set[tuple[str, str]], rank,
                  broken: list | None = None) -> set[tuple[str, str]]:
    """Drop the back-edges that make the constraint graph cyclic, FIRST.

    Three Portico epics constrain each other in a loop on the live board
    (DRE-2492 ↔ DRE-2628 ↔ DRE-2629, 2026-08-29). Leaving the loop in and
    breaking it only when the sort runs out of ready nodes is what happened
    first: nothing in the tangle was ever "ready", so the sort emptied every
    other repo first and the highest-priority work in the population came out
    at position 118 of 147. A cycle is a planning question — two things that
    each have to go first — and answering it late silently re-prioritises
    everything else.

    Deterministic: nodes and successors are walked in rank order, so the edge
    dropped is always the one that closes the loop against the ranked order.
    """
    adjacency = {k: [] for k in keys}
    for before, after in edges:
        if before in adjacency and after in adjacency:
            adjacency[before].append(after)
    for key in adjacency:
        adjacency[key].sort(key=lambda k: (rank(k), k))

    kept = set(edges)
    state = {k: 0 for k in keys}                     # 0 unseen, 1 on stack, 2 done
    for root in sorted(keys, key=lambda k: (rank(k), k)):
        if state[root]:
            continue
        stack = [(root, iter(adjacency[root]))]
        state[root] = 1
        while stack:
            node, children = stack[-1]
            nxt = next(children, None)
            if nxt is None:
                state[node] = 2
                stack.pop()
                continue
            if state[nxt] == 1:                      # a back edge: the loop
                kept.discard((node, nxt))
                if broken is not None:
                    broken.append({
                        "dropped": [node, nxt],
                        "why": "each constrains the other; the ranked order wins",
                    })
                continue
            if state[nxt] == 0:
                state[nxt] = 1
                stack.append((nxt, iter(adjacency[nxt])))
    return kept


def _topo(keys: list[str], edges: set[tuple[str, str]], rank,
          broken: list | None = None) -> list[str]:
    """Kahn's algorithm with a priority heap: the constraints decide what is
    POSSIBLE, the rank decides what happens first among the possible.

    A cycle in the graph — A must precede B and B must precede A, which happens
    when a collision and a recorded relation disagree — is broken by rank and
    RECORDED. An order that silently drops a constraint is worse than one that
    says which constraint it could not honour.
    """
    edges = _break_cycles(keys, edges, rank, broken)
    incoming = {k: set() for k in keys}
    outgoing = {k: set() for k in keys}
    for before, after in edges:
        if before in incoming and after in incoming and before != after:
            incoming[after].add(before)
            outgoing[before].add(after)
    heap = [(rank(k), k) for k in keys if not incoming[k]]
    heapq.heapify(heap)
    order = []
    remaining = set(keys)
    while remaining:
        if not heap:                       # a constraint cycle: break it, loudly
            stuck = min(remaining, key=lambda k: (rank(k), k))
            for before in list(incoming[stuck]):
                incoming[stuck].discard(before)
                outgoing[before].discard(stuck)
                if broken is not None:
                    broken.append({"dropped": [before, stuck],
                                   "why": "mutual constraints; ranked order wins"})
            heapq.heappush(heap, (rank(stuck), stuck))
            continue
        _, key = heapq.heappop(heap)
        if key not in remaining:
            continue
        remaining.discard(key)
        order.append(key)
        for nxt in sorted(outgoing[key]):
            incoming[nxt].discard(key)
            if not incoming[nxt] and nxt in remaining:
                heapq.heappush(heap, (rank(nxt), nxt))
    return order


def sequence(cards: list[dict], *, collisions: dict | None = None,
             repo_priority=REPO_PRIORITY, broken: list | None = None) -> list[dict]:
    """The population as ONE order: unit before unit, card before card.

    Portico first, then everything else — subject to the constraints, so a card
    another repo's work collides with is pulled forward rather than silently
    scheduled beside it.
    """
    collisions = collisions if collisions is not None else collision_report(cards)
    by_id = {c["identifier"]: c for c in cards}
    unit_list = units(cards)
    unit_of = {cid: u["key"] for u in unit_list for cid in u["cards"]}
    ranks = _repo_rank(cards, repo_priority)

    constraints = [(p["before"], p["after"]) for p in collisions["pairs"]]
    for card in cards:
        for blocker in blockers_of(card):
            if blocker in by_id:
                constraints.append((blocker, card["identifier"]))

    unit_edges = {(unit_of[b], unit_of[a]) for b, a in constraints
                  if unit_of[b] != unit_of[a]}
    unit_index = {u["key"]: u for u in unit_list}

    def unit_rank(key):
        unit = unit_index[key]
        return (ranks[unit["repo"]], unit["created"], _card_sort_key(key))

    ordered_units = _topo([u["key"] for u in unit_list], unit_edges, unit_rank,
                          broken)

    rows = []
    position = 0
    for key in ordered_units:
        unit = unit_index[key]
        inner_edges = {(b, a) for b, a in constraints
                       if unit_of.get(b) == key and unit_of.get(a) == key}

        def card_rank(cid):
            return (_created(by_id[cid]), _card_sort_key(cid))

        for cid in _topo(list(unit["cards"]), inner_edges, card_rank, broken):
            position += 1
            rows.append({
                "identifier": cid,
                "title": by_id[cid].get("title") or "",
                "position": position,
                "unit": key,
                "epic": unit["epic"],
                "repo": repo_of(by_id[cid]),
                "project": ((by_id[cid].get("project") or {}) or {}).get("name"),
            })
    return rows


def cycle_plan(rows: list[dict], cycles: list[dict], capacity: int) -> list[dict]:
    """Fill cycles in sequence order, never splitting a unit.

    A unit larger than the capacity gets a cycle to itself rather than being
    cut in half: eleven children of one Forms epic spread over three cycles is
    one deliverable reported three times.
    """
    slots = _slots(cycles)
    index, used = 0, 0
    placed = {}
    for _, group in itertools.groupby(rows, key=lambda r: r["unit"]):
        members = list(group)
        if used and used + len(members) > capacity:
            index, used = index + 1, 0
        number, cycle_id, projected = slots(index)
        for row in members:
            placed[row["identifier"]] = (number, cycle_id, projected)
        used += len(members)
        if used >= capacity:
            index, used = index + 1, 0
    out = []
    for row in rows:
        number, cycle_id, projected = placed[row["identifier"]]
        out.append({**row, "cycle": number, "cycle_id": cycle_id,
                    "projected": projected})
    return out


def _slots(cycles: list[dict]):
    known = list(cycles)
    last = known[-1]["number"] if known else 0

    def slot(index: int):
        if index < len(known):
            c = known[index]
            return c["number"], c["id"], False
        return last + (index - len(known) + 1), None, True

    return slot


def cycle_days(cycles: list[dict]) -> int:
    for c in cycles:
        try:
            start = datetime.fromisoformat(c["startsAt"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(c["endsAt"].replace("Z", "+00:00"))
        except (AttributeError, KeyError, ValueError):
            continue
        days = (end - start).days
        if days > 0:
            return days
    return DEFAULT_CYCLE_DAYS


# --------------------------------------------------------------------------- #
# the proposal                                                                 #
# --------------------------------------------------------------------------- #

def propose(cards: list[dict], *, cycles: list[dict], capacity: int = DEFAULT_CAPACITY,
            batch_cycles: int = 1, repo_priority=REPO_PRIORITY,
            lane: str = "Intake", now: str | None = None) -> dict:
    """The whole population, sequenced, with one outcome per card."""
    dead, live = [], []
    unstated = []
    for card in sorted(cards, key=lambda c: _card_sort_key(c["identifier"])):
        target = superseded_by(card.get("description"))
        if target:
            dead.append({"identifier": card["identifier"],
                         "title": card.get("title") or "",
                         "repo": repo_of(card), "superseded_by": target})
            continue
        if supersession_gap(card.get("description")):
            unstated.append(card["identifier"])
        live.append(card)

    collisions = collision_report(live)
    broken: list = []
    rows = cycle_plan(sequence(live, collisions=collisions, broken=broken,
                               repo_priority=repo_priority), cycles, capacity)

    batch_numbers = sorted({r["cycle"] for r in rows})[:batch_cycles]
    now_rows, later_rows = [], []
    for row in rows:
        if row["cycle"] in batch_numbers:
            now_rows.append({k: row[k] for k in
                             ("identifier", "title", "position", "cycle",
                              "cycle_id", "unit", "epic", "repo", "projected")})
        else:
            later_rows.append({"identifier": row["identifier"],
                               "title": row["title"], "repo": row["repo"],
                               "reconsidered_in": row["cycle"],
                               "projected": row["projected"]})

    sequence_rows = [{**r, "outcome": ("now" if r["cycle"] in batch_numbers
                                       else "not-now")} for r in rows]
    sequence_rows += [{"identifier": d["identifier"], "title": d["title"],
                       "position": None, "unit": None, "epic": None,
                       "repo": d["repo"], "project": None, "cycle": None,
                       "cycle_id": None, "projected": False, "outcome": "dead"}
                      for d in dead]

    proposal = {
        "generated_at": now or _now(),
        "lane": lane,
        "population": len(cards),
        "census": census(cards),
        "capacity": capacity,
        "cycle_days": cycle_days(cycles),
        "batch": {"cycles": batch_numbers, "cards": len(now_rows)},
        "repo_order": [r for r in _repo_rank(live, repo_priority)],
        "sequence": sequence_rows,
        "outcomes": {"now": now_rows, "not-now": later_rows, "dead": dead},
        "collisions": collisions,
        "unhonoured_constraints": broken,
        "unstated_supersessions": unstated,
    }
    proposal["deprioritised"] = _deprioritised(proposal)
    proposal["id"] = proposal_id(proposal)
    return proposal


def _deprioritised(proposal: dict) -> list[dict]:
    """Which repos are waiting, and roughly how long — derived from the
    sequence, not asserted by hand. Portico first means agent-bureau and
    bureau-pipeline wait, and that is said out loud rather than discovered by
    someone expecting their card to move."""
    in_batch = {row["repo"] for row in proposal["outcomes"]["now"]}
    first_cycle = min(proposal["batch"]["cycles"], default=0)
    days = proposal["cycle_days"]
    rows = []
    waiting: dict[str, list[int]] = {}
    for row in proposal["outcomes"]["not-now"]:
        if row["repo"] in in_batch:
            continue
        waiting.setdefault(row["repo"], []).append(row["reconsidered_in"])
    for repo, numbers in waiting.items():
        starts = min(numbers)
        rows.append({
            "repo": repo,
            "cards": len(numbers),
            "first_cycle": starts,
            "weeks": round((starts - first_cycle) * days / 7),
        })
    return sorted(rows, key=lambda r: (-r["cards"], r["repo"]))


def proposal_id(proposal: dict) -> str:
    """A digest of the BATCH — its cards, their order and their cycles.

    The id is what an approval names, so an approval binds to a batch the way a
    critic verdict binds to a head sha: re-run the groomer after the population
    moves and the id changes, which retires the old approval instead of letting
    it authorise a batch nobody read.
    """
    payload = json.dumps({
        "lane": proposal["lane"],
        "batch": [[r["identifier"], r["position"], r["cycle"]]
                  for r in sorted(proposal["outcomes"]["now"],
                                  key=lambda r: r["position"])],
    }, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:12]


def approval_comment(pid: str) -> str:
    return f"{MARK} {APPROVAL_TAG}: {pid}"


def proposal_comment(proposal: dict) -> str:
    return (f"{MARK} {PROPOSAL_TAG}: {proposal['id']}\n\n"
            + render_proposal(proposal))


# --------------------------------------------------------------------------- #
# the proposal, as the CEO reads it                                            #
# --------------------------------------------------------------------------- #

def render_proposal(proposal: dict) -> str:
    batch = proposal["outcomes"]["now"]
    cycles = ", ".join(str(n) for n in proposal["batch"]["cycles"])
    w = []
    add = w.append
    add(f"# Groom proposal `{proposal['id']}` — cycle {cycles}")
    add("")
    add(f"{len(batch)} cards of {proposal['population']} in {proposal['lane']} "
        f"are proposed for cycle {cycles}, in the order below. Nothing moves "
        f"until you approve it.")
    add("")
    add("**To approve:** comment `" + approval_comment(proposal["id"])
        + "` on this card. Anything else — including a comment that mentions "
          "the marker — leaves the batch where it is.")
    add("")
    add("## The population")
    add("")
    add("| Repo | Cards |")
    add("| -- | -- |")
    for repo, count in proposal["census"].items():
        add(f"| {repo} | {count} |")
    add("")
    add("## The batch, in order")
    add("")
    add("Order: " + " → ".join(proposal.get("repo_order") or []) + ". Within a "
        "repo the oldest unit goes first, and an epic and its children are one "
        "unit — unless a collision or a recorded blocks relation says otherwise, "
        "in which case the constraint wins.")
    add("")
    add("| # | Card | Repo | Epic | Title |")
    add("| -- | -- | -- | -- | -- |")
    for row in sorted(batch, key=lambda r: r["position"]):
        add(f"| {row['position']} | {row['identifier']} | {row['repo']} | "
            f"{row['epic'] or '—'} | {_trim(row['title'])} |")
    add("")
    add("## Collisions, and what the order does about them")
    add("")
    pairs = proposal["collisions"]["pairs"]
    if pairs:
        for pair in pairs:
            add(f"- {pair['before']} before {pair['after']} — {pair['why']}")
    else:
        add("- None found in this population.")
    add("")
    unreadable = proposal["collisions"]["unreadable"]
    if unreadable:
        add(f"Collision cover: {len(unreadable)} card(s) name no file, so a "
            f"collision involving one of them is invisible to this read — "
            + ", ".join(unreadable[:20])
            + ("…" if len(unreadable) > 20 else "") + ".")
        add("")
    if proposal["collisions"]["boilerplate"]:
        cited = ", ".join(f"{name} ({n} cards)" for name, n
                          in proposal["collisions"]["boilerplate"].items())
        add(f"Read as reference rather than ownership: {cited}.")
        add("")
    if proposal.get("unhonoured_constraints"):
        add(f"{len(proposal['unhonoured_constraints'])} constraint(s) point both "
            f"ways and could not all be honoured — the ranked order won, and "
            f"each one is in the JSON. Two cards that each have to go first is a "
            f"planning question, not an ordering one.")
        add("")
    add("## What waits, and roughly how long")
    add("")
    if proposal["deprioritised"]:
        for row in proposal["deprioritised"]:
            add(f"- **{row['repo']}** — {_plural(row['cards'], 'card')}, first "
                f"one in cycle {row['first_cycle']}, roughly "
                f"{_plural(row['weeks'], 'week')} out.")
    else:
        add("- Nothing: every repo has work in this batch.")
    add("")
    later = proposal["outcomes"]["not-now"]
    add(f"{len(later)} cards are **not now** — wanted, deliberately not this "
        f"batch, each with the cycle it is reconsidered in. That is 'later', "
        f"and it is not 'no'.")
    add("")
    add("## Recommended dead — your call, not ours")
    add("")
    if proposal["outcomes"]["dead"]:
        for row in proposal["outcomes"]["dead"]:
            add(f"- {row['identifier']} — superseded by {row['superseded_by']} "
                f"· {_trim(row['title'])}")
        add("")
        add("The groomer never cancels. Cancelling is destructive and stays "
            "yours, as a separate step.")
    else:
        add("- None.")
    if proposal["unstated_supersessions"]:
        add("")
        add("Named nothing: "
            + ", ".join(proposal["unstated_supersessions"])
            + " say they are superseded without naming what replaced them, so "
              "they are sequenced normally rather than recommended dead.")
    add("")
    add("## On cycles")
    add("")
    add(CYCLE_IS_NOT_SPRINT_PLANNING)
    add("")
    return "\n".join(w)


# --------------------------------------------------------------------------- #
# the approval gate                                                            #
# --------------------------------------------------------------------------- #

# The marker must OPEN the comment — the same anchoring the routing verdict uses,
# for the same reason: a reader that matched it anywhere would read a sentence
# ABOUT approving as an approval.
_APPROVAL_LINE = re.compile(rf"^\s*(?:{MARK}\s*)?{APPROVAL_TAG}\s*:\s*([0-9a-f]{{6,}})")


def approval_problem(proposal: dict, records: list[dict]) -> str | None:
    """Why this batch is not approved, or None if it is."""
    pid = proposal_id(proposal)
    named = []
    for record in records:
        match = _APPROVAL_LINE.match((record.get("body") or "").strip())
        if not match:
            continue
        if match.group(1) != pid:
            named.append(match.group(1))
            continue
        if record.get("authored_by_pipeline"):
            return ("the only approval of this batch was written by the "
                    "pipeline's own Linear identity — the proposer cannot "
                    "approve its own proposal")
        return None
    if named:
        return (f"the approvals on this card name batch(es) {', '.join(named)}; "
                f"this batch is {pid} — the population moved since it was read, "
                f"so it needs a fresh look")
    return (f"no comment on this card opens with `{approval_comment(pid)}` — "
            f"nothing leaves Intake without the CEO approving this batch")


def drain(lops, proposal: dict, *, card: str, to: str = DRAIN_TO) -> dict:
    """Move the APPROVED batch out of Intake, in the proposed order.

    Refuses — before any write — a terminal destination, a missing or foreign
    approval, and a cycle Linear does not carry.
    """
    if to in NEVER_WRITES:
        raise WillNotCancel(
            f"the drain will not write {to!r}: the groomer recommends and never "
            f"cancels, and cancelling stays the operator's own step")
    problem = approval_problem(proposal, lops.comment_records(card))
    if problem:
        raise NotApproved(problem)

    rows = sorted(proposal["outcomes"]["now"], key=lambda r: r["position"])
    missing = [r["identifier"] for r in rows if not r.get("cycle_id")]
    if missing:
        raise ValueError(
            f"{len(missing)} card(s) are assigned to a projected cycle Linear "
            f"does not carry ({', '.join(missing[:5])}…) — create the cycle "
            f"first; the groomer will not invent one")

    moved = []
    for row in rows:
        issue = lops.get_issue(row["identifier"])
        lops.gql(SET_CYCLE, {"id": issue["id"],
                             "input": {"cycleId": row["cycle_id"]}})
        lops.cmd_state(row["identifier"], to)
        moved.append(row["identifier"])
    return {"moved": moved, "to": to, "proposal": proposal_id(proposal)}


# --------------------------------------------------------------------------- #
# helpers                                                                      #
# --------------------------------------------------------------------------- #

def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _created(card: dict) -> str:
    return card.get("createdAt") or ""


def _card_sort_key(identifier: str):
    """`DRE-9` before `DRE-11`: a lexical sort of identifiers is deterministic
    but reads as arbitrary in a proposal a human has to follow."""
    match = re.search(r"(\d+)$", identifier or "")
    return (int(match.group(1)) if match else 0, identifier or "")


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" + ("" if count == 1 else "s")


def _trim(text: str, width: int = 60) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= width else text[:width - 1] + "…"


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #

def _shaping(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--lane", default="Intake")
    parser.add_argument("--capacity", type=int, default=DEFAULT_CAPACITY)
    parser.add_argument("--batch-cycles", type=int, default=1)
    parser.add_argument("--priority", default=",".join(REPO_PRIORITY),
                        help="comma-separated repo slugs, highest first")


def _build(args) -> dict:
    cards = read_population(linear_ops, args.lane)
    cycles = read_cycles(linear_ops)
    return propose(cards, cycles=cycles, capacity=args.capacity,
                   batch_cycles=args.batch_cycles, lane=args.lane,
                   repo_priority=tuple(p for p in args.priority.split(",") if p))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_census = sub.add_parser("census", help="what is in the lane, by repo")
    p_census.add_argument("--lane", default="Intake")

    p_propose = sub.add_parser("propose", help="sequence the lane; write nothing")
    _shaping(p_propose)
    p_propose.add_argument("--out", help="write the proposal JSON here")
    p_propose.add_argument("--post", help="post the proposal to this card")

    p_drain = sub.add_parser("drain", help="move the APPROVED batch onward")
    _shaping(p_drain)
    p_drain.add_argument("--card", required=True,
                         help="the card the proposal and its approval live on")

    args = parser.parse_args(argv)

    if args.command == "census":
        cards = read_population(linear_ops, args.lane)
        print(json.dumps({"lane": args.lane, "population": len(cards),
                          "by_repo": census(cards)}, indent=2))
        return 0

    proposal = _build(args)

    if args.command == "propose":
        if args.out:
            with open(args.out, "w", encoding="utf-8") as fh:
                json.dump(proposal, fh, indent=2)
            print(f"wrote {args.out} ({proposal['id']})")
        if args.post:
            linear_ops.cmd_comment(args.post, proposal_comment(proposal))
        print(render_proposal(proposal))
        return 0

    try:
        result = drain(linear_ops, proposal, card=args.card)
    except (NotApproved, WillNotCancel, ValueError) as e:
        print(f"groomer: refused — {e}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":                                  # pragma: no cover
    sys.exit(main())
