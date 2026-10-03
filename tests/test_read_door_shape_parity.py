"""The read door's node shape IS the shape Reconcile reads from Linear (Stage 2 design §3).

Two halves, because either alone passes while the other drifts:

1. THE SELECTIONS. The field paths `bureau_read` requires of every door node
   are derived here from the GraphQL text `reconcile.py` actually SENDS —
   `_fetch_active_cards`, the Backlog read, and `merge_sweep_gate.QUERY` — so a
   field added to one of those queries tomorrow fails this file until the
   door's contract carries it too. A reader whose field the door does not
   serve would read `None` and decide on it: the `children(first: 1)` "no
   children" class of bug (DRE-3044), arriving through the door.

2. THE VALUES. One board rendered twice (`fixtures/read_door/parity_world.json`):
   as Linear's GraphQL answers it to those two reads, and as the door's
   envelope. The nodes the client returns, projected onto each read's own
   selection, equal what the Linear read hands the sweep — comment order
   (newest first), `user: null` for an app actor, `pageInfo` cursors, the
   relation page and its blockers' lanes.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/portico")
os.environ.setdefault("REPO_SLUG", "portico")

import bureau_read  # noqa: E402
import linear_ops  # noqa: E402
import merge_sweep_gate  # noqa: E402
import reconcile  # noqa: E402
from bureau_read_fakes import FakeDoor, FakeIssuer, door_env  # noqa: E402

WORLD = json.loads((ROOT / "tests" / "fixtures" / "read_door" / "parity_world.json").read_text())


def selection_tree(text: str) -> dict:
    """A GraphQL selection set as `{field: None | {…}}`, arguments dropped."""
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*|[{}()]|\$[A-Za-z_]+|\"[^\"]*\"|[:\[\]!,]", text)
    pos = 0

    def skip_args():
        nonlocal pos
        depth = 0
        while pos < len(tokens):
            tok = tokens[pos]
            pos += 1
            if tok == "(":
                depth += 1
            elif tok == ")":
                depth -= 1
                if depth == 0:
                    return

    def block() -> dict:
        nonlocal pos
        out: dict = {}
        last = None
        while pos < len(tokens):
            tok = tokens[pos]
            if tok == "}":
                pos += 1
                return out
            if tok == "(":
                skip_args()
                continue
            if tok == "{":
                pos += 1
                out[last] = block()
                continue
            pos += 1
            if re.match(r"[A-Za-z_]", tok):
                out[tok] = None
                last = tok
        return out

    return block()


def _node_selection(query: str, connection: str = "issues") -> dict:
    """The per-node selection of `connection`'s `nodes` in a sent query."""
    tree = selection_tree(query[query.index("{") + 1:])
    return tree[connection]["nodes"]


def _captured(fn, *args, **kw) -> str:
    sent = []

    def gql(query, variables=None):
        sent.append(query)
        return {"issues": {"nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}}

    with mock.patch.object(linear_ops, "gql", side_effect=gql):
        fn(*args, **kw)
    assert len(sent) == 1
    return sent[0]


def _merge(*trees: dict) -> dict:
    out: dict = {}
    for tree in trees:
        for key, sub in tree.items():
            if isinstance(sub, dict) and isinstance(out.get(key), dict):
                out[key] = _merge(out[key], sub)
            else:
                out[key] = sub if sub is not None else out.get(key)
    return out


ACTIVE = _node_selection(_captured(reconcile._fetch_active_cards, ("Todo",)))
BACKLOG = _node_selection(_captured(reconcile._fetch_backlog_linear, None))
BACKLOG_ONE = _node_selection(_captured(reconcile._fetch_backlog_linear, ["DRE-1"]))


def test_the_parser_reads_a_selection():
    assert selection_tree("a b(first: 1) { c { d } } e") == {
        "a": None, "b": {"c": {"d": None}}, "e": None}


def test_the_door_carries_every_field_the_board_read_selects():
    assert _merge(bureau_read.CARD_FIELDS, ACTIVE) == bureau_read.CARD_FIELDS


def test_the_door_carries_every_field_the_backlog_read_selects():
    door = {**bureau_read.CARD_FIELDS, **bureau_read.RELATION_FIELDS}
    assert _merge(door, BACKLOG) == door
    assert BACKLOG_ONE == BACKLOG  # the merge path's read is the same shape


def test_the_door_serves_no_field_neither_read_selects():
    """The contract is exactly the union — a field nobody reads is a field the
    door must keep fresh for nothing."""
    assert _merge(ACTIVE, BACKLOG) == {**bureau_read.CARD_FIELDS, **bureau_read.RELATION_FIELDS}


def _shape_of(value):
    """A value's field tree, the way `selection_tree` writes a selection."""
    if isinstance(value, dict):
        return {key: _shape_of(sub) for key, sub in value.items()}
    if isinstance(value, list):
        merged: dict = {}
        for item in value:
            merged = _merge(merged, _shape_of(item) or {})
        return merged or None
    return None


def test_the_dependents_answer_is_board_nodes_and_reconcile_rebuilds_the_merge_query(
        monkeypatch):
    """`/cards/{id}/dependents` answers the cards `id` blocks, as board nodes
    (AB-1's shape — reconciled with it, 2026-10-02). `merged_card_scope` reads
    it plus `/cards?ids=<id>` and rebuilds the ONE card in exactly
    `merge_sweep_gate.QUERY`'s selection, so every reader downstream of the
    merge-sweep gate reads the shape it always read."""
    merged = {**WORLD["door_work"]["issues"]["nodes"][0], "state": {"name": "Done"}}
    dependent = WORLD["door_backlog"]["issues"]["nodes"][0]
    monkeypatch.setattr(bureau_read, "mode", lambda: "on")
    monkeypatch.setattr(bureau_read, "enabled", lambda: True)
    monkeypatch.setattr(bureau_read, "cards", lambda ids, **kw: bureau_read.DoorRead(
        nodes=[merged]))
    monkeypatch.setattr(bureau_read, "dependents", lambda ident, **kw: bureau_read.DoorRead(
        nodes=[dependent]))
    built = reconcile._door_dependents("DRE-1001")
    query = selection_tree(merge_sweep_gate.QUERY[merge_sweep_gate.QUERY.index("{") + 1:])
    assert _shape_of(built) == query["issue"]
    assert [d["identifier"] for d in merge_sweep_gate.dependents(built)] == ["DRE-1003"]
    assert built["relations"]["pageInfo"]["hasNextPage"] is False
    # The parent's children are not read: said as unknown, never as none.
    assert built["parent"]["children"]["pageInfo"]["hasNextPage"] is True


def test_the_relation_shape_is_the_gates_inline_relations():
    tree = selection_tree(reconcile.INVERSE_RELATIONS_GQL)
    assert tree["inverseRelations"] == bureau_read.RELATION_FIELDS["inverseRelations"]


def test_the_comment_window_is_linear_ops_one_window():
    tree = selection_tree(linear_ops.COMMENT_WINDOW_GQL)
    assert tree["comments"] == bureau_read.CARD_FIELDS["comments"]


# ── the values ──────────────────────────────────────────────────────────────


def _project(node, shape):
    """`node` cut down to `shape`'s paths — what a reader of that selection sees."""
    if shape is None or node is None:
        return node
    out = {}
    for key, sub in shape.items():
        value = node.get(key)
        if key == "nodes" and isinstance(sub, dict):
            out[key] = [_project(item, sub) for item in value]
        else:
            out[key] = _project(value, sub)
    return out


@pytest.fixture
def door(monkeypatch):
    for name in ("BUREAU_READ", "BUREAU_READ_URL", "BUREAU_PIPELINE_REF"):
        monkeypatch.delenv(name, raising=False)
    bureau_read.reset_for_tests()
    with FakeIssuer() as issuer, FakeDoor() as fake:
        for key, value in door_env(door_url=fake.url, issuer=issuer, mode="on").items():
            monkeypatch.setenv(key, value)
        yield fake
    bureau_read.reset_for_tests()


def _linear_read(fn, connection_key, *args):
    with mock.patch.object(linear_ops, "gql", return_value={"issues": WORLD[connection_key]}):
        return fn(*args)


def test_the_door_board_equals_what_the_board_read_hands_the_sweep(door):
    door.routes["/board"] = (200, WORLD["door_work"])
    served = bureau_read.board(reconcile.DOOR_WORK_LANES, max_age=120).nodes
    linear = _linear_read(reconcile._fetch_active_cards, "linear_active", reconcile.DOOR_WORK_LANES)
    assert [_project(n, ACTIVE) for n in served] == [_project(n, ACTIVE) for n in linear]


def test_the_door_backlog_equals_what_the_backlog_read_hands_the_gate(door):
    door.routes["/board"] = (200, WORLD["door_backlog"])
    served = bureau_read.board(["Backlog"], max_age=120, relations=True).nodes
    linear = _linear_read(reconcile._fetch_backlog_linear, "linear_backlog", None)
    assert [_project(n, BACKLOG) for n in served] == [_project(n, BACKLOG) for n in linear]


def test_the_sweeps_readers_answer_the_same_off_either_rendering(door):
    """Not just equal bytes: the gate's own readers agree on both."""
    door.routes["/board"] = (200, WORLD["door_backlog"])
    served = bureau_read.board(["Backlog"], max_age=120, relations=True).nodes[0]
    linear = _linear_read(reconcile._fetch_backlog_linear, "linear_backlog", None)[0]
    for reader in (reconcile.card_comment_bodies, reconcile.card_repo,
                   reconcile.prose_blockers.relation_blockers,
                   reconcile.prose_blockers.relations_unknown,
                   reconcile.has_unresolved_blocker):
        assert reader(served) == reader(linear), reader.__name__
    assert linear_ops.window_is_partial(served["comments"]) is True
