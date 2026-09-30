"""RED-first: the sweep sees EVERY blocker, however many other relations a card
has (DRE-5379).

THE MEASUREMENT. 2026-09-30, live, with `inverseRelations(first: 100)`:
DRE-4580 held 22 inverse relations, 13 of them `blocks`, and the sweep's
`first: 20` page carried 11 of the 13; DRE-5136 held 26, 16 `blocks`, and the
page carried 11. The page counts every relation type — `related`,
`duplicate`, `blocks` — so a card with many `related` links loses blockers off
the end, and two readers go wrong on what is left:

  * `prose_blockers.undeclared_claims` calls a true `**Blocked by:**` line a
    defect and routes the card to Triage — twice by hand, twice straight back,
    on the day this was found;
  * `prose_blockers.relation_blockers`, the promotion gate itself, can miss a
    live blocker and promote a card early. That one is the dangerous one.

WHAT IS UNDER TEST:
  1. Both of the sweep's batched reads — `backlog_children` and the epic
     record — ask whether the relation page is full, and a card whose page IS
     full is read to the end by `complete_inverse_relations`. A card whose
     page is not full costs nothing extra.
  2. With the whole set in hand, a card holding 25 `related` and 3 `blocks`
     relations sees all 3: its prose naming them is not a defect, and the gate
     holds on the non-terminal ones.
  3. A read that is STILL full after its bound, or that fails, leaves the
     card's blockers UNKNOWN: said in the sweep log, and the card is neither
     refused nor promoted on it. An epic in that state holds its children and
     is not called a defect either.
  4. The board snapshot records whether a card's relation page filled, so the
     replay sees the cards the sweep would read to the end.

Run: cd bureau-pipeline && python3 -m pytest tests/test_inverse_relation_pages.py -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")
os.environ.setdefault("GH_TOKEN", "x")

import board_snapshot  # noqa: E402
import linear_ops  # noqa: E402
import prose_blockers  # noqa: E402
import reconcile  # noqa: E402
import routing_verdict  # noqa: E402

FLEET_VERDICT = routing_verdict.verdict_comment(
    "FLEET", "the acceptance criteria are unit-testable")

#: The three blockers, and where they sit among 25 `related` links: one inside
#: the first page of 20, two past it — the DRE-4580 shape, where the page held
#: some of the blockers and not the rest.
BLOCKERS = {"DRE-601": "Done", "DRE-602": "In Review", "DRE-603": "Todo"}
BLOCKER_AT = {5: "DRE-601", 22: "DRE-602", 27: "DRE-603"}


@pytest.fixture(autouse=True)
def _pin_and_reset(monkeypatch):
    monkeypatch.setattr(reconcile, "REPO_SLUG", "agent-bureau")
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()
    reconcile._stale_defects.clear()
    reconcile._card_skips.clear()
    reconcile.reset_sweep_cards()
    yield
    reconcile.reset_sweep_cards()


def _all_relations() -> list[dict]:
    """25 `related` + 3 `blocks`, in the order Linear would page them."""
    out, related = [], 0
    for position in range(28):
        blocker = BLOCKER_AT.get(position)
        if blocker:
            out.append({"type": "blocks", "issue": {
                "identifier": blocker, "state": {"name": BLOCKERS[blocker]}}})
        else:
            related += 1
            out.append({"type": "related", "issue": {
                "identifier": f"DRE-{700 + related}", "state": {"name": "Todo"}}})
    return out


def _page(nodes: list[dict], first: int, after: str | None) -> dict:
    start = int(after.split("-")[1]) if after else 0
    chunk = nodes[start:start + first]
    end = start + len(chunk)
    return {"nodes": chunk,
            "pageInfo": {"hasNextPage": end < len(nodes), "endCursor": f"cursor-{end}"}}


def _card(identifier="DRE-900", description="work", parent="DRE-800") -> dict:
    """A Backlog child eligible on every other ground, with the relation page
    its batched read would return: the first 20 of 28."""
    return {
        "identifier": identifier,
        "title": "a card with many related links",
        "description": "**Repo:** agent-bureau\n" + description,
        "createdAt": "2026-09-30T00:00:00.000Z",
        "parent": {"identifier": parent, "state": {"name": "In Progress"}} if parent else None,
        "children": {"nodes": []},
        "labels": {"nodes": [{"name": "repo:agent-bureau"}]},
        "comments": {"nodes": [{"body": FLEET_VERDICT}],
                     "pageInfo": {"hasNextPage": False, "endCursor": None}},
        "inverseRelations": _page(_all_relations(), reconcile.INVERSE_PAGE, None),
    }


class FakeLinear:
    """The batched Backlog read, the batched epic read, and the per-card read
    of the rest of a full relation page. Nothing else."""

    def __init__(self, cards=(), epics=(), *, relations=None, endless=False,
                 topup_error=None):
        self.cards = list(cards)
        self.epics = list(epics)
        self.relations = relations if relations is not None else _all_relations()
        #: a server that says "another page" forever, with fresh cursors
        self.endless = endless
        self.topup_error = topup_error
        self.queries: list[tuple[str, dict]] = []

    @property
    def topups(self) -> list[dict]:
        return [v for q, v in self.queries
                if "issue(id: $id)" in q and "inverseRelations" in q]

    def gql(self, query, variables=None):
        q = " ".join((query or "").split())
        v = variables or {}
        self.queries.append((q, v))
        if "issue(id: $id)" in q and "inverseRelations" in q:
            if self.topup_error is not None:
                raise self.topup_error
            first = int(q.split("inverseRelations(first: ")[1].split(",")[0].split(")")[0])
            if self.endless:
                n = int((v.get("after") or "cursor-0").split("-")[1])
                return {"issue": {"inverseRelations": {
                    "nodes": [{"type": "related", "issue": {
                        "identifier": f"DRE-{5000 + n}", "state": {"name": "Todo"}}}],
                    "pageInfo": {"hasNextPage": True, "endCursor": f"cursor-{n + 1}"}}}}
            return {"issue": {"inverseRelations": _page(self.relations, first, v.get("after"))}}
        if "issues(" in q and "$numbers" in q and "history(last: 50)" in q:
            return {"issues": {"nodes": self.epics,
                               "pageInfo": {"hasNextPage": False, "endCursor": None}}}
        if "issues(" in q and "Backlog" in q:
            return {"issues": {"nodes": self.cards,
                               "pageInfo": {"hasNextPage": False, "endCursor": None}}}
        raise AssertionError(f"unexpected query: {q[:160]}")


def _read_backlog(fake: FakeLinear) -> list[dict]:
    with patch.object(linear_ops, "gql", side_effect=fake.gql):
        return reconcile.backlog_children()


def _sweep(cards: list[dict]):
    """promote_ready over cards already read, with every write stubbed."""
    with patch.object(reconcile, "backlog_children", return_value=cards), \
        patch.object(reconcile, "epic_blockers_unmet", return_value=False), \
        patch.object(reconcile, "epic_records", return_value={}), \
        patch.object(reconcile.mid_epic, "last_green_light", return_value=None), \
        patch.object(reconcile, "epic_thread", return_value=[]), \
        patch.object(reconcile.plan_critic, "promotion_refusal", return_value=None), \
        patch.object(reconcile.mid_epic, "promotion_refusal", return_value=None), \
        patch.object(reconcile.linear_ops, "count_comments", return_value=0), \
        patch.object(reconcile.linear_ops, "cmd_advance") as advance, \
        patch.object(reconcile.linear_ops, "cmd_comment") as comment:
        promoted = reconcile.promote_ready(active_count=0)
    return promoted, advance, comment


# ---------------------------------------------------------------------------
# 1. The reads ask whether the page is full, and read a full one to the end
# ---------------------------------------------------------------------------
def _inverse_selection(query: str) -> str:
    q = " ".join(query.split())
    start = q.index("inverseRelations(")
    depth, i = 0, q.index("{", start)
    for i in range(i, len(q)):
        depth += {"{": 1, "}": -1}.get(q[i], 0)
        if depth == 0:
            break
    return q[start:i + 1]


@pytest.mark.parametrize("name, query", [
    ("backlog_children", None),
    ("EPIC_RECORD_GQL", reconcile.EPIC_RECORD_GQL),
    ("board_snapshot.CARD_QUERY", board_snapshot.CARD_QUERY),
])
def test_every_inverse_read_asks_whether_its_page_is_full(name, query):
    if query is None:
        fake = FakeLinear()
        _read_backlog(fake)
        query = fake.queries[0][0]
    selection = _inverse_selection(query)
    assert "pageInfo { hasNextPage" in selection, (name, selection)
    assert f"inverseRelations(first: {reconcile.INVERSE_PAGE})" in selection, name


def test_the_snapshot_reads_the_sweeps_own_page_depth():
    """One definition: the snapshot must not hold more, or less, of a card's
    relations than the sweep's first page does."""
    assert board_snapshot.INVERSE_PAGE == reconcile.INVERSE_PAGE


def test_a_backlog_card_with_25_related_and_3_blocks_sees_all_three_blockers():
    card = _card()
    assert len(prose_blockers.relation_ids(card)) == 1, (
        "the fixture must reproduce the defect: the first page holds 1 of 3"
    )
    [read] = _read_backlog(FakeLinear([card]))
    assert prose_blockers.relation_ids(read) == set(BLOCKERS)
    assert not prose_blockers.relations_unknown(read)


def test_an_epic_record_with_25_related_and_3_blocks_sees_all_three_blockers():
    epic = {
        "id": "uuid-800", "identifier": "DRE-800", "description": "epic",
        "state": {"name": "In Progress"}, "children": {"nodes": []},
        "history": {"nodes": []},
        "inverseRelations": _page(_all_relations(), reconcile.INVERSE_PAGE, None),
        "comments": {"nodes": [], "pageInfo": {"hasNextPage": False}},
    }
    fake = FakeLinear(epics=[epic])
    with patch.object(linear_ops, "gql", side_effect=fake.gql):
        record = reconcile.epic_records(["DRE-800"])["DRE-800"]
    assert prose_blockers.relation_ids(record) == set(BLOCKERS)
    assert prose_blockers.relation_blockers(record) == {"DRE-602", "DRE-603"}


def test_a_page_that_is_not_full_costs_no_extra_read():
    card = _card()
    card["inverseRelations"] = _page(_all_relations()[:3], reconcile.INVERSE_PAGE, None)
    fake = FakeLinear([card])
    _read_backlog(fake)
    assert fake.topups == []


def test_a_card_read_to_the_end_costs_one_request_for_a_hundred_relations():
    fake = FakeLinear([_card()])
    _read_backlog(fake)
    assert len(fake.topups) == 1, fake.topups


# ---------------------------------------------------------------------------
# 2. With the whole set: no false defect, and the gate holds on the live ones
# ---------------------------------------------------------------------------
def test_prose_naming_all_three_blockers_is_not_a_defect():
    card = _card(description="**Blocked by:** DRE-601, DRE-602, DRE-603")
    [read] = _read_backlog(FakeLinear([card]))
    assert prose_blockers.undeclared_claims(read) == set()
    assert prose_blockers.relation_blockers(read) == {"DRE-602", "DRE-603"}


def test_the_sweep_holds_the_card_on_its_live_blockers_and_never_calls_it_a_defect(capsys):
    card = _card(description="**Blocked by:** DRE-601, DRE-602, DRE-603")
    cards = _read_backlog(FakeLinear([card]))
    promoted, advance, comment = _sweep(cards)
    assert promoted == 0
    advance.assert_not_called()          # not promoted, and not moved to Triage
    assert not any(prose_blockers.CARD_TAG in c.args[1]
                   for c in comment.call_args_list)
    out = capsys.readouterr().out
    assert "DRE-900 is held by DRE-602 (In Review)" in out


def test_a_blocker_past_the_first_page_still_holds_the_card():
    """The dangerous half: DRE-603 is the 28th relation. The first page alone
    would promote this card with a live blocker."""
    card = _card()
    promoted, advance, _ = _sweep(_read_backlog(FakeLinear([card])))
    assert promoted == 0
    advance.assert_not_called()


# ---------------------------------------------------------------------------
# 3. Still full after the bound, or unreadable: UNKNOWN, neither refused nor
#    promoted
# ---------------------------------------------------------------------------
def test_a_page_still_full_after_the_bound_is_unknown_and_the_read_stops():
    fake = FakeLinear([_card()], endless=True)
    [read] = _read_backlog(fake)
    assert len(fake.topups) == reconcile.INVERSE_TOPUP_PAGES
    assert prose_blockers.relations_unknown(read)


def test_an_unreadable_rest_of_the_page_is_unknown_and_the_sweep_continues(capsys):
    fake = FakeLinear([_card()], topup_error=linear_ops.LinearError("boom"))
    [read] = _read_backlog(fake)
    assert prose_blockers.relations_unknown(read)
    assert "DRE-900" in capsys.readouterr().err


def test_a_spent_quota_is_not_retried_card_by_card():
    cards = [_card("DRE-900"), _card("DRE-901"), _card("DRE-902")]
    fake = FakeLinear(cards, topup_error=linear_ops.LinearRateLimited("dry"))
    read = _read_backlog(fake)
    assert len(fake.topups) == 1
    assert all(prose_blockers.relations_unknown(c) for c in read)


def test_an_unknown_card_is_neither_refused_nor_promoted(capsys):
    """Its prose names a blocker the first page does not hold — the exact
    shape that was called a defect — and the rest could not be read. Nothing
    is decided on half a page."""
    card = _card(description="**Blocked by:** DRE-602")
    fake = FakeLinear([card], endless=True)
    cards = _read_backlog(fake)
    capsys.readouterr()
    promoted, advance, comment = _sweep(cards)
    assert promoted == 0
    advance.assert_not_called()          # neither Todo nor Triage
    comment.assert_not_called()          # and no refusal said on the card
    out = capsys.readouterr().out
    assert "DRE-900" in out and "UNKNOWN" in out
    assert prose_blockers.UNKNOWN_TAG in out


def test_an_unknown_card_with_no_prose_is_not_promoted_either():
    """The other half: the first page holds no live blocker, and that is not
    the same as the card having none."""
    relations = [r for r in _all_relations() if r["type"] == "related"]
    relations.insert(24, {"type": "blocks", "issue": {
        "identifier": "DRE-603", "state": {"name": "Todo"}}})
    card = _card()
    card["inverseRelations"] = _page(relations, reconcile.INVERSE_PAGE, None)
    assert prose_blockers.relation_blockers(card) == set()
    cards = _read_backlog(FakeLinear([card], relations=relations, endless=True))
    promoted, advance, _ = _sweep(cards)
    assert promoted == 0
    advance.assert_not_called()


def test_an_epic_whose_relations_are_unknown_holds_its_children_without_a_defect(capsys):
    epic = {
        "identifier": "DRE-800",
        "description": "**Repo:** agent-bureau\n**Blocked by:** DRE-602",
        "inverseRelations": {
            "nodes": _all_relations()[:reconcile.INVERSE_PAGE],
            "pageInfo": {"hasNextPage": True, "endCursor": "cursor-20"},
        },
    }
    with patch.object(reconcile, "_fetch_epic_relations", return_value=epic), \
        patch.object(reconcile.linear_ops, "first_comment_at", return_value=None), \
        patch.object(reconcile.linear_ops, "count_comments", return_value=0), \
        patch.object(reconcile.linear_ops, "cmd_comment") as comment:
        assert reconcile.epic_blockers_unmet("DRE-800") is True
    comment.assert_not_called()
    out = capsys.readouterr().out
    assert "DRE-800" in out and "UNKNOWN" in out
    assert not reconcile._stale_defects


def test_relations_unknown_reads_the_page_and_nothing_else():
    assert not prose_blockers.relations_unknown({})
    assert not prose_blockers.relations_unknown({"inverseRelations": {"nodes": []}})
    assert not prose_blockers.relations_unknown(
        {"inverseRelations": {"nodes": [], "pageInfo": {"hasNextPage": False}}})
    assert prose_blockers.relations_unknown(
        {"inverseRelations": {"nodes": [], "pageInfo": {"hasNextPage": True}}})


def test_the_unknown_tag_and_the_defect_tags_never_contain_one_another():
    tags = (prose_blockers.UNKNOWN_TAG, prose_blockers.CARD_TAG, prose_blockers.EPIC_TAG)
    for a in tags:
        for b in tags:
            if a != b:
                assert a not in b, (a, b)


# ---------------------------------------------------------------------------
# 4. The snapshot records whether the page filled
# ---------------------------------------------------------------------------
def test_the_snapshot_keeps_whether_the_relation_page_filled():
    raw = _card()
    raw.update(id="uuid", updatedAt="2026-09-30T00:00:00.000Z",
               state={"name": "Backlog"}, relations={"nodes": []},
               history={"nodes": []})
    scrubbed = board_snapshot.scrub_card(raw)
    assert scrubbed["inverseRelations"]["pageInfo"] == {"hasNextPage": True}
    assert prose_blockers.relations_unknown(scrubbed)
