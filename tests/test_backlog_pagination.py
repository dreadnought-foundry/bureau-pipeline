"""The sweep reads EVERY card, not the first page (DRE-2681).

`backlog_children()` and `active_cards()` asked Linear for `issues(first: 100)`
with no `pageInfo`, no `endCursor` and no cursor — so the promoter's whole world
was the first 100 rows Linear happened to return. The Backlog census on
2026-08-26 was 226 cards: 126 of them were not promotion candidates, not by
policy, not reported anywhere, and WHICH 126 was decided by Linear's default
ordering. The sweep log printed what it considered and never what it never saw.

The fixture here is 150 cards and the card that must be found is the 150th. A
100-card fixture passes against the broken code and proves nothing.

Run: cd bureau-pipeline && python3 -m pytest tests/test_backlog_pagination.py -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")

import linear_ops  # noqa: E402
import reconcile  # noqa: E402

PAGE = 100


class FakeLinear:
    """Linear's real paging contract, in miniature.

    A page is at most 100 nodes. `pageInfo` is served ONLY when the query asks
    for it — exactly like the API, so a query that never selects `pageInfo`
    gets the first page and no way to know another exists. The cursor is the
    offset as a string, which is opaque to the caller, as a real cursor is.
    """

    def __init__(self, nodes: list[dict]):
        self.nodes = nodes
        self.cursors: list[str | None] = []   # every `after` the caller sent
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
            conn["pageInfo"] = {
                "hasNextPage": end < len(self.nodes),
                "endCursor": str(end),
            }
        return {"issues": conn}


def _backlog_card(n: int) -> dict:
    return {
        "id": f"id-{n}",
        "identifier": f"DRE-{n}",
        "title": f"card {n}",
        "description": "",
        "createdAt": "2026-08-01T00:00:00Z",
        "parent": {"identifier": "DRE-1", "state": {"name": "In Progress"}},
        "labels": {"nodes": [{"name": "repo:bureau-pipeline"}]},
        "comments": {"nodes": []},
        "inverseRelations": {"nodes": []},
    }


def _active_card(n: int) -> dict:
    return {
        "id": f"id-{n}",
        "identifier": f"DRE-{n}",
        "title": f"card {n}",
        "description": "",
        "updatedAt": "2026-08-01T00:00:00Z",
        "state": {"name": "Todo"},
        "labels": {"nodes": [{"name": "repo:bureau-pipeline"}]},
    }


# The 150th card is the one that must be found — the whole point of the fixture.
THE_150TH = "DRE-150"


# --------------------------------------------------------------------------
# backlog_children — the promoter's candidate list
# --------------------------------------------------------------------------
def test_backlog_children_reads_past_the_first_page():
    """MUTATION CHECK: drop the cursor loop and this returns 100 cards ending at
    DRE-100 — the 150th is invisible, which is the live bug."""
    fake = FakeLinear([_backlog_card(n) for n in range(1, 151)])
    with patch.object(linear_ops, "gql", fake.gql):
        cards = reconcile.backlog_children()
    assert len(cards) == 150
    assert cards[-1]["identifier"] == THE_150TH
    assert THE_150TH in {c["identifier"] for c in cards}


def test_backlog_children_threads_the_cursor_it_was_given():
    """Page 2 is asked for with page 1's endCursor — not re-asked from the top,
    which would loop on the same 100 rows forever."""
    fake = FakeLinear([_backlog_card(n) for n in range(1, 151)])
    with patch.object(linear_ops, "gql", fake.gql):
        reconcile.backlog_children()
    assert fake.cursors == [None, "100"]


def test_backlog_children_query_asks_for_the_page_info_it_needs():
    fake = FakeLinear([_backlog_card(n) for n in range(1, 151)])
    with patch.object(linear_ops, "gql", fake.gql):
        reconcile.backlog_children()
    q = fake.queries[0]
    assert "pageInfo" in q and "hasNextPage" in q and "endCursor" in q


# --------------------------------------------------------------------------
# active_cards — the state sweep and the WIP count
# --------------------------------------------------------------------------
def test_active_cards_reads_past_the_first_page():
    fake = FakeLinear([_active_card(n) for n in range(1, 151)])
    with patch.object(linear_ops, "gql", fake.gql):
        cards = reconcile.active_cards()
    assert len(cards) == 150
    assert cards[-1]["identifier"] == THE_150TH


def test_active_cards_still_filters_by_the_states_it_was_asked_for():
    """Pagination must not drop the caller's variables on the follow-up pages —
    the watchdog passes WATCHDOG_LANES and gets a different lane set."""
    sent: list[dict] = []
    fake = FakeLinear([_active_card(n) for n in range(1, 151)])

    def spy(query, variables=None):
        sent.append(dict(variables or {}))
        return fake.gql(query, variables)

    with patch.object(linear_ops, "gql", spy):
        reconcile.active_cards(("Todo", "Planning"))
    assert len(sent) == 2
    assert all(v["states"] == ["Todo", "Planning"] for v in sent)


# --------------------------------------------------------------------------
# The pager itself
# --------------------------------------------------------------------------
def test_gql_paged_stops_on_a_repeated_cursor():
    """A server that keeps claiming hasNextPage with the SAME cursor must end
    the loop, not hang the sweep for its whole ten-minute timeout."""
    calls = {"n": 0}

    def stuck(query, variables=None):
        calls["n"] += 1
        return {
            "issues": {
                "nodes": [{"identifier": f"DRE-{calls['n']}"}],
                "pageInfo": {"hasNextPage": True, "endCursor": "same"},
            }
        }

    with patch.object(linear_ops, "gql", stuck):
        nodes = linear_ops.gql_paged("query($after: String) { issues { pageInfo } }")
    assert calls["n"] < 10
    assert nodes


def test_gql_paged_stops_when_the_cursor_is_missing():
    """hasNextPage true with no endCursor is unusable — return what we have
    rather than re-requesting page one forever."""
    def truncated(query, variables=None):
        return {
            "issues": {
                "nodes": [{"identifier": "DRE-1"}],
                "pageInfo": {"hasNextPage": True, "endCursor": None},
            }
        }

    with patch.object(linear_ops, "gql", truncated):
        nodes = linear_ops.gql_paged("query($after: String) { issues { pageInfo } }")
    assert [n["identifier"] for n in nodes] == ["DRE-1"]


def test_gql_paged_rejects_a_query_that_cannot_paginate():
    """The failure this card exists for is silent, so the pager refuses to be
    the silent one: a query with no `$after` can only ever return page one."""
    with pytest.raises(ValueError):
        linear_ops.gql_paged("query { issues(first: 100) { nodes { id } } }")
