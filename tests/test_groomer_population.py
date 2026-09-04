"""The groomer reads the WHOLE population and sequences it (DRE-2683).

The groomer's job is the one a per-card reader structurally cannot do: shape a
BATCH. A batch is not a list of individually-good cards — it is an ordered set,
and the order is the product.

Two failures this file pins:

  * **Page one is not the population.** `reconcile.backlog_children()` asked
    Linear for `issues(first: 100)` with no cursor, so the sweep's whole world
    was the first 100 rows of a 226-card Backlog and WHICH 126 it never saw was
    decided by Linear's default ordering (DRE-2681). A groomer that sequences
    page one has sequenced nothing. The fixture here is 150 cards and the card
    that must be found is the 150th — a 100-card fixture passes against the
    broken code and proves nothing.
  * **An epic split across cycles.** Eleven children of one Forms epic
    classified in three separate batches spreads one deliverable across three
    cycles for no reason. The epic is the atom of cycle assignment here.

Run: cd bureau-pipeline && python3 -m pytest tests/test_groomer_population.py -v
"""
from __future__ import annotations

import os
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")

import groomer  # noqa: E402

PAGE = 100


class FakeLinear:
    """Linear's real paging contract, in miniature (same fake as
    tests/test_backlog_pagination.py, which pins the sweep's version of this
    bug). A page is at most 100 nodes, and `pageInfo` is served ONLY when the
    query selects it — so a query that never asks gets page one and no way to
    know another exists."""

    def __init__(self, nodes: list[dict]):
        self.nodes = nodes
        self.cursors: list[str | None] = []
        self.queries: list[str] = []

    def gql(self, query: str, variables: dict | None = None) -> dict:
        variables = variables or {}
        self.queries.append(query)
        after = variables.get("after")
        self.cursors.append(after)
        start = int(after) if after else 0
        page = self.nodes[start:start + PAGE]
        conn: dict = {"nodes": page}
        if "pageInfo" in query:
            end = start + len(page)
            conn["pageInfo"] = {"hasNextPage": end < len(self.nodes),
                                "endCursor": str(end)}
        return {"issues": conn}

    def gql_paged(self, query, variables=None, *, connection="issues"):
        import linear_ops
        real = linear_ops.gql
        linear_ops.gql = self.gql
        try:
            return linear_ops.gql_paged(query, variables, connection=connection)
        finally:
            linear_ops.gql = real


def days_ago(days: float, *, anchor: str | None = None) -> str:
    """An ISO creation date `days` before now (or before `anchor`).

    The groomer measures its 14-day window against the clock (DRE-3096), so a
    fixture with a hardcoded creation date silently ages out of the batch the
    week after it is written — and the test then asserts the window, not the
    thing it was written for.
    """
    base = (datetime.fromisoformat(anchor.replace("Z", "+00:00")) if anchor
            else datetime.now(timezone.utc))
    return (base - timedelta(days=days)).isoformat().replace("+00:00", "Z")


# ONE timestamp for the whole fixture module: cards that share a creation
# instant share a creation DAY, which is what makes repo order the tie-break
# rather than the microsecond a fixture happened to be built in.
RECENT = days_ago(1)


def card(identifier, *, repo="portico", parent=None, created=RECENT,
         description="", title=None, project=None, priority=0):
    return {
        "identifier": identifier,
        "title": title or f"{identifier} does a thing",
        "description": description,
        "createdAt": created,
        "priority": priority,
        "state": {"name": "Intake"},
        "labels": {"nodes": [{"name": f"repo:{repo}"}, {"name": "agent:engineer"}]},
        "parent": {"identifier": parent, "title": f"[EPIC] {parent}"} if parent else None,
        "project": {"name": project} if project else None,
        "cycle": None,
        "inverseRelations": {"nodes": []},
    }


CYCLES = [
    {"number": 12, "id": "cyc-12", "startsAt": "2026-09-07T07:00:00.000Z",
     "endsAt": "2026-09-21T07:00:00.000Z"},
    {"number": 13, "id": "cyc-13", "startsAt": "2026-09-21T07:00:00.000Z",
     "endsAt": "2026-10-05T07:00:00.000Z"},
]


# --------------------------------------------------------------------------
# the whole population, not a page of it
# --------------------------------------------------------------------------
def test_the_population_query_can_paginate():
    assert "$after" in groomer.POPULATION_QUERY
    assert "pageInfo" in groomer.POPULATION_QUERY
    assert "endCursor" in groomer.POPULATION_QUERY


def test_read_population_walks_past_the_first_page():
    nodes = [card(f"DRE-{n:04d}") for n in range(150)]
    fake = FakeLinear(nodes)
    got = groomer.read_population(fake, lane="Intake")
    assert len(got) == 150, "the groomer read a page, not the population"
    assert got[-1]["identifier"] == "DRE-0149"
    assert fake.cursors[0] is None and len(fake.cursors) > 1, "no second page requested"


def test_the_lane_is_the_filter_and_defaults_to_intake():
    fake = FakeLinear([card("DRE-1")])
    groomer.read_population(fake)
    assert fake.queries, "no query issued"
    # The lane travels as a variable, never interpolated into the query string.
    assert "Intake" not in fake.queries[0]


# --------------------------------------------------------------------------
# every card gets exactly one outcome — the completeness invariant
# --------------------------------------------------------------------------
def _portico_population():
    """A shape like the live one: an epic with children, loose cards, and
    another repo waiting behind them."""
    cards = [card(f"DRE-1{n:02d}", parent="DRE-900") for n in range(11)]
    cards += [card(f"DRE-2{n:02d}", parent="DRE-901") for n in range(9)]
    cards += [card(f"DRE-3{n:02d}") for n in range(24)]           # parentless portico
    cards += [card(f"DRE-4{n:02d}", repo="agent-bureau") for n in range(30)]
    return cards


def test_every_card_in_the_population_gets_exactly_one_outcome():
    proposal = groomer.propose(_portico_population(), cycles=CYCLES)
    seen = [row["identifier"]
            for name in groomer.OUTCOMES
            for row in proposal["outcomes"][name]]
    assert len(seen) == len(set(seen)), "a card carries two outcomes"
    assert set(seen) == {c["identifier"] for c in _portico_population()}, (
        "a card in the population got no outcome — the groomer dropped it "
        "silently, which is exactly what reading page one did"
    )


# --------------------------------------------------------------------------
# Portico first, epics kept whole
# --------------------------------------------------------------------------
def test_portico_is_sequenced_before_the_other_repos():
    proposal = groomer.propose(_portico_population(), cycles=CYCLES)
    order = proposal["sequence"]
    portico = [r["position"] for r in order if r["repo"] == "portico"]
    others = [r["position"] for r in order if r["repo"] != "portico"]
    assert max(portico) < min(others), (
        "Portico is the business priority — every Portico card is sequenced "
        "before the first card of any other repo"
    )


def test_an_epics_children_are_never_split_across_cycles():
    proposal = groomer.propose(_portico_population(), cycles=CYCLES, capacity=12)
    by_epic: dict[str, set] = {}
    for row in proposal["sequence"]:
        if row["epic"]:
            by_epic.setdefault(row["epic"], set()).add(row["cycle"])
    assert by_epic, "the fixture has epics; the sequence recorded none"
    for epic, cycles in by_epic.items():
        assert len(cycles) == 1, f"{epic}'s children landed in cycles {sorted(cycles)}"


def test_an_epics_children_are_contiguous_in_the_order():
    proposal = groomer.propose(_portico_population(), cycles=CYCLES)
    positions: dict[str, list[int]] = {}
    for row in proposal["sequence"]:
        if row["epic"]:
            positions.setdefault(row["epic"], []).append(row["position"])
    for epic, pos in positions.items():
        pos.sort()
        assert pos == list(range(pos[0], pos[0] + len(pos))), (
            f"{epic}'s children are interleaved with other work"
        )


def test_the_parentless_cards_are_sequenced_explicitly():
    """The loose population is where the judgement is. A run that orders only
    the epic'd cards has not done the job."""
    proposal = groomer.propose(_portico_population(), cycles=CYCLES)
    loose = [r for r in proposal["sequence"]
             if r["repo"] == "portico" and not r["epic"]]
    assert len(loose) == 24
    assert all(isinstance(r["position"], int) for r in loose)
    assert len({r["position"] for r in loose}) == 24


# --------------------------------------------------------------------------
# an ORDER, not a ranked list
# --------------------------------------------------------------------------
def test_positions_are_a_total_order_over_the_population():
    proposal = groomer.propose(_portico_population(), cycles=CYCLES)
    positions = sorted(r["position"] for r in proposal["sequence"])
    assert positions == list(range(1, len(positions) + 1)), (
        "the output must be an explicit order between the cards — every card "
        "has one position and no two share it"
    )


def test_the_sequence_is_deterministic_whatever_order_linear_returns():
    cards = _portico_population()
    shuffled = cards[:]
    random.Random(2683).shuffle(shuffled)
    a = groomer.propose(cards, cycles=CYCLES)
    b = groomer.propose(shuffled, cycles=CYCLES)
    assert [r["identifier"] for r in a["sequence"]] == \
           [r["identifier"] for r in b["sequence"]]
    assert groomer.proposal_id(a) == groomer.proposal_id(b)


# --------------------------------------------------------------------------
# cycles are the container, and the batch is one bite
# --------------------------------------------------------------------------
def test_the_batch_covers_only_the_cycles_it_was_asked_for():
    proposal = groomer.propose(_portico_population(), cycles=CYCLES,
                               capacity=12, batch_cycles=1)
    assert proposal["batch"]["cycles"] == [12]
    assert {r["cycle"] for r in proposal["outcomes"]["now"]} == {12}
    assert proposal["outcomes"]["not-now"], "everything fit — the fixture is too small"


def test_cycles_beyond_the_ones_linear_has_are_marked_projected():
    """Linear carries cycles 12 and 13 today. A 74-card sequence runs past
    them, and a projected cycle is labelled — the drain must never try to
    write one, because it has no id."""
    proposal = groomer.propose(_portico_population(), cycles=CYCLES, capacity=12)
    projected = [r for r in proposal["sequence"] if r["projected"]]
    assert projected, "the fixture overflows the known cycles; nothing was projected"
    assert all(r["cycle"] > 13 for r in projected)
    assert all(r["cycle_id"] is None for r in projected)


def test_the_proposal_states_what_is_deprioritised_and_for_how_long():
    proposal = groomer.propose(_portico_population(), cycles=CYCLES, capacity=12)
    waiting = {row["repo"]: row for row in proposal["deprioritised"]}
    assert "agent-bureau" in waiting, (
        "Portico first means the other repos wait — say it out loud rather "
        "than letting it be discovered"
    )
    row = waiting["agent-bureau"]
    assert row["cards"] == 30
    assert row["first_cycle"] > 12
    assert row["weeks"] >= 2, "roughly how long, in weeks, from the cycle length"
    text = groomer.render_proposal(proposal)
    assert "agent-bureau" in text and "week" in text


def test_the_render_says_why_a_cycle_is_not_sprint_planning():
    proposal = groomer.propose(_portico_population(), cycles=CYCLES)
    assert groomer.CYCLE_IS_NOT_SPRINT_PLANNING in groomer.render_proposal(proposal)


def test_an_epic_card_in_the_lane_moves_with_its_own_children():
    """The live shape on 2026-08-29: epic DRE-2628 sits in the lane alongside
    its eleven children. It joins their unit — an epic that drains a cycle
    ahead of its own children is the split this rule exists to prevent — and
    the unit still reports which epic it is."""
    cards = [card("DRE-900", title="[EPIC] Forms")]
    cards += [card(f"DRE-9{n:02d}", parent="DRE-900") for n in range(10, 14)]
    proposal = groomer.propose(cards, cycles=CYCLES)
    rows = {r["identifier"]: r for r in proposal["sequence"]}
    assert rows["DRE-900"]["unit"] == "DRE-900"
    assert {r["epic"] for r in rows.values()} == {"DRE-900"}
    assert len({r["cycle"] for r in rows.values()}) == 1
