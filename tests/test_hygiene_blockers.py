"""RED-first: the hygiene agent's blocker-owner lane (DRE-8648).

`scripts/hygiene_blockers.py` finds every card that has sat more than 48 hours
in `Intake` or `Hand-work` while a `blocks` relation names an open card, and
plans one comment on that blocker card. The comment names the card's owner —
its assignee, or its creator when nobody is assigned — and lists every open
card it holds up, directly and through chains of relations, each with its
title, lane and wait. An owner who is not a person (the fleet's own user, an
identity `config/linear-identities.json` declares, an app, a user with no
name) is never addressed: the notice reads `no owner to tell`, and a `Left`
row says the same.

The board read carries no owner and no state for a held card, and a held card
in `Queued` is not on the board at all, so the lane reads for itself through
`ctx.linear`: one `CARDS_QUERY` per chain level, at most `CHAIN_READS` of
them. A read that fails plans nothing that pass.

Every board card here is in the `board_snapshot.CARD_QUERY` shape, and every
answer the fake `ctx.linear` gives is in the `CARDS_QUERY` shape.

Run: cd bureau-pipeline && python3 -m pytest tests/test_hygiene_blockers.py -v
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import hygiene  # noqa: E402
import pipeline_act  # noqa: E402

MODULE_PATH = ROOT / "scripts" / "hygiene_blockers.py"

#: 20:00 UTC on 2026-11-01 is 12:00 in Pacific Standard Time (DST ended at
#: 02:00 that morning).
NOW = datetime(2026, 11, 1, 20, 0, tzinfo=UTC)
PT = ZoneInfo("America/Los_Angeles")
HOME = "dreadnought-foundry"

TAG = "hyg-blocker-owner"
ACT = "hygiene-blocker-owner-notice"
MAY_BE_SHORT = "Some cards this one holds up could not be read, so this list may be short."
NO_OWNER = "no owner to tell"

DANA = {"id": "user-dana", "name": "Dana Reyes", "displayName": "dana", "app": False}
FLEET = {"id": "user-fleet", "name": "Agent-Bureau", "displayName": "agent-bureau",
         "app": False}
TOOLS = {"id": "user-tools", "name": "bureau-tools", "displayName": "bureau-tools",
         "app": False}
APP = {"id": "user-app", "name": "Some Integration", "displayName": "integration",
       "app": True}


def load_lane():
    if not MODULE_PATH.exists():
        return None
    spec = importlib.util.spec_from_file_location("hygiene_blockers", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lane = load_lane()


def iso(when: datetime) -> str:
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def ago(hours: float) -> datetime:
    return NOW - timedelta(hours=hours)


# --------------------------------------------------------------------------- #
# builders                                                                     #
# --------------------------------------------------------------------------- #


def board_card(ident, lane_name, *, blocks=(), entered=None, created=None, slug=None,
               comments=()):
    """One card in the `board_snapshot.CARD_QUERY` shape. `entered` is its
    move into `lane_name` on the history read; None leaves the history empty."""
    created = created or ago(24 * 30)
    history = [] if entered is None else [
        {"createdAt": iso(entered - timedelta(days=3)), "toState": {"name": "Planning"}},
        {"createdAt": iso(entered), "toState": {"name": lane_name}},
    ]
    return {
        "id": f"uuid-{ident}",
        "identifier": ident,
        "title": f"synthetic {ident}",
        "description": "synthetic",
        "createdAt": iso(created),
        "updatedAt": iso(NOW),
        "state": {"name": lane_name},
        "labels": {"nodes": [{"name": f"repo:{slug}"}] if slug else []},
        "parent": None,
        "children": {"nodes": []},
        "comments": {"pageInfo": {"hasNextPage": False, "endCursor": None},
                     "nodes": [{"body": b, "createdAt": iso(NOW), "user": None}
                               for b in reversed(list(comments))]},
        "relations": {"pageInfo": {"hasNextPage": False}, "nodes": [
            {"type": "blocks", "issue": {"identifier": ident},
             "relatedIssue": {"identifier": h}} for h in blocks]},
        "inverseRelations": {"pageInfo": {"hasNextPage": False}, "nodes": []},
        "history": {"nodes": history},
    }


def node(ident, lane_name, *, blocks=(), entered=None, created=None, assignee=None,
         creator=None, more=False, title=None, related=()):
    """One card as `CARDS_QUERY` answers it. `related` adds relations of other
    types, which hold nobody up."""
    created = created or ago(24 * 30)
    history = [] if entered is None else [
        {"createdAt": iso(entered - timedelta(days=2)), "toState": {"name": "Planning"}},
        {"createdAt": iso(entered), "toState": {"name": lane_name}},
    ]
    return {
        "identifier": ident,
        "title": title or f"synthetic {ident}",
        "createdAt": iso(created),
        "state": {"name": lane_name},
        "history": {"nodes": history},
        "assignee": assignee,
        "creator": creator,
        "relations": {"pageInfo": {"hasNextPage": more}, "nodes": [
            *({"type": "blocks", "relatedIssue": {"identifier": h}} for h in blocks),
            *({"type": "related", "relatedIssue": {"identifier": h}} for h in related),
        ]},
    }


class FakeLinear:
    """`linear_ops.gql`, answering `CARDS_QUERY` from a map of nodes. Each
    call records the identifiers it asked for, in the order asked."""

    def __init__(self, nodes, *, raises=None):
        self.nodes = {n["identifier"]: n for n in nodes}
        self.asked: list = []
        self.raises = raises

    def __call__(self, query, variables=None):
        assert query == lane.CARDS_QUERY
        numbers = list((variables or {}).get("numbers") or [])
        self.asked.append(sorted(f"DRE-{int(n)}" for n in numbers))
        if self.raises is not None:
            raise self.raises
        found = [self.nodes[f"DRE-{int(n)}"] for n in numbers if f"DRE-{int(n)}" in self.nodes]
        return {"issues": {"nodes": json.loads(json.dumps(found)),
                           "pageInfo": {"hasNextPage": False, "endCursor": None}}}


class PagedLinear(FakeLinear):
    """`FakeLinear` answering `PAGE` cards a request, as Linear does, the
    cursor naming where the next page starts — or no cursor at all when
    `cursor` is False. Each call records the `$after` it carried."""

    def __init__(self, nodes, *, cursor=True):
        super().__init__(nodes)
        self.afters: list = []
        self.cursor = cursor

    def __call__(self, query, variables=None):
        assert len(self.afters) < 20, "the read did not stop"
        after = (variables or {}).get("after")
        self.afters.append(after)
        found = super().__call__(query, variables)["issues"]["nodes"]
        start = int(after) if after else 0
        more = start + lane.PAGE < len(found)
        return {"issues": {"nodes": found[start:start + lane.PAGE],
                           "pageInfo": {"hasNextPage": more,
                                        "endCursor": (str(start + lane.PAGE)
                                                      if more and self.cursor else None)}}}


def plan(cards, nodes, *, owner=HOME, raises=None, now=NOW, fake=None):
    assert lane is not None, f"{MODULE_PATH.name} does not exist yet"
    fake = fake or FakeLinear(nodes, raises=raises)
    ctx = hygiene.make_context(owner, gh=lambda argv: "[]",
                               linear=hygiene.read_only_linear(fake),
                               now=now, summary_card="DRE-900")
    lanes: dict = {}
    for c in cards:
        lanes.setdefault(c["state"]["name"], []).append(c)
    board = hygiene.Board(lanes=lanes, prs={})
    return lane.plan(board, ctx), fake, ctx, board


def actions(items):
    return [i for i in items if isinstance(i, hygiene.Action)]


def lefts(items):
    return [i for i in items if isinstance(i, hygiene.Left)]


def the_notice(items, ident):
    found = actions(items)
    assert len(found) == 1, found
    action = found[0]
    assert action.act == ACT and action.target == ident
    assert [w.kind for w in action.writes] == ["linear_comment"]
    assert action.writes[0].card == ident
    return action, action.writes[0].body


def text_of(body):
    """The notice without its machine trailer — what a person reads."""
    return body.split("\n\n📎 ")[0]


def held_line(body, ident):
    lines = [ln for ln in text_of(body).splitlines() if ln.startswith(f"- {ident} ")]
    assert len(lines) == 1, (ident, body)
    return lines[0]


def pt_day(when: datetime) -> str:
    return when.astimezone(PT).strftime("%Y-%m-%d")


# --------------------------------------------------------------------------- #
# the main case                                                                #
# --------------------------------------------------------------------------- #


def main_case(**blocker):
    entered = ago(49)
    cards = [board_card("DRE-100", "Intake", blocks=("DRE-201", "DRE-202"),
                        entered=entered),
             board_card("DRE-202", "In Progress", blocks=("DRE-303",), entered=ago(5))]
    nodes = [node("DRE-100", "Intake", blocks=("DRE-201", "DRE-202"), entered=entered,
                  assignee=blocker.get("assignee", DANA), creator=blocker.get("creator", TOOLS)),
             node("DRE-201", "Queued", entered=ago(24 * 12), title="Ship the importer"),
             node("DRE-202", "In Progress", blocks=("DRE-303",), entered=ago(5),
                  title="Wire the API"),
             node("DRE-303", "Green Light", entered=ago(30), title="Decide the rollout")]
    return cards, nodes, entered


class TestTheMainCase:
    def test_one_notice_on_the_blocker_naming_its_owner_and_every_held_card(self):
        cards, nodes, entered = main_case()
        items, fake, ctx, _board = plan(cards, nodes)
        action, body = the_notice(items, "DRE-100")
        first = body.splitlines()[0]
        assert first.startswith(f"🧹 hygiene: {TAG} —")
        assert first == (f"🧹 hygiene: {TAG} — DRE-100 in Intake since {pt_day(entered)}"
                         f" · {hygiene.pt(NOW)}")
        assert action.cause == f"DRE-100 in Intake since {pt_day(entered)}"
        assert "Dana Reyes" in text_of(body)
        assert "@" not in text_of(body)
        assert NO_OWNER not in body
        assert "Queued" in held_line(body, "DRE-201")
        assert "Ship the importer" in held_line(body, "DRE-201")
        assert "12 days" in held_line(body, "DRE-201")
        assert "In Progress" in held_line(body, "DRE-202")
        assert "Wire the API" in held_line(body, "DRE-202")
        assert "5 hours" in held_line(body, "DRE-202")
        assert "Green Light" in held_line(body, "DRE-303")
        assert "Decide the rollout" in held_line(body, "DRE-303")
        assert MAY_BE_SHORT not in body
        assert not lefts(items)

    def test_a_card_held_through_a_chain_names_the_card_it_is_held_through(self):
        cards, nodes, _ = main_case()
        _action, body = the_notice(plan(cards, nodes)[0], "DRE-100")
        assert held_line(body, "DRE-303").endswith(", held through DRE-202")
        for direct in ("DRE-201", "DRE-202"):
            assert "held through" not in held_line(body, direct)

    def test_it_says_the_two_ways_on(self):
        cards, nodes, _ = main_case()
        _action, body = the_notice(plan(cards, nodes)[0], "DRE-100")
        text = text_of(body)
        assert "finish or cancel DRE-100" in text
        assert "remove the relation" in text

    def test_one_request_per_chain_level_and_none_after_the_chain_ends(self):
        cards, nodes, _ = main_case()
        _items, fake, ctx, _board = plan(cards, nodes)
        assert fake.asked == [["DRE-100"], ["DRE-201", "DRE-202"], ["DRE-303"]]
        assert len(ctx.linear.calls) == 3

    def test_a_cycle_between_two_held_cards_ends_the_read(self):
        entered = ago(60)
        cards = [board_card("DRE-100", "Intake", blocks=("DRE-201",), entered=entered)]
        nodes = [node("DRE-100", "Intake", blocks=("DRE-201",), entered=entered, assignee=DANA),
                 node("DRE-201", "Queued", blocks=("DRE-202",), entered=ago(10)),
                 node("DRE-202", "Queued", blocks=("DRE-201",), entered=ago(10))]
        items, fake, _ctx, _board = plan(cards, nodes)
        assert fake.asked == [["DRE-100"], ["DRE-201"], ["DRE-202"]]
        _action, body = the_notice(items, "DRE-100")
        held_line(body, "DRE-201")
        held_line(body, "DRE-202")
        assert MAY_BE_SHORT not in body

    def test_the_notice_passes_the_core_guard_and_reads_back_whole(self):
        cards, nodes, _ = main_case()
        items, _fake, ctx, _board = plan(cards, nodes)
        action, body = the_notice(items, "DRE-100")
        hygiene.guard(action.writes[0], ctx)
        assert hygiene.read_receipt(body) == {"tag": TAG, "cause": action.cause,
                                              "clock": hygiene.pt(NOW)}
        assert pipeline_act.read_trailer(body)["act"] == ACT

    def test_it_reads_through_ctx_linear_and_never_the_board_query(self):
        import board_snapshot

        cards, nodes, _ = main_case()
        _items, _fake, ctx, _board = plan(cards, nodes)
        assert ctx.linear.calls and all(q == lane.CARDS_QUERY for q in ctx.linear.calls)
        assert lane.CARDS_QUERY != board_snapshot.CARD_QUERY


class TestTheQuery:
    def test_it_selects_what_the_card_names(self):
        query = " ".join(lane.CARDS_QUERY.split())
        for part in ('issues(first: 50, after: $after, filter: {team: {key: {eq: "DRE"}}, '
                     'number: {in: $numbers}})',
                     "identifier title createdAt state { name }",
                     "history(last: 50) { nodes { createdAt toState { name } } }",
                     "assignee { id name displayName app }",
                     "creator { id name displayName app }",
                     "relations(first: 50) { pageInfo { hasNextPage } nodes { type "
                     "relatedIssue { identifier } } }"):
            assert part in query, part

    def test_the_bounds(self):
        assert lane.CHAIN_READS == 6
        assert lane.HOURS == 48
        assert lane.MAY_BE_SHORT == MAY_BE_SHORT
        assert lane.NO_OWNER == NO_OWNER


# --------------------------------------------------------------------------- #
# an incomplete read                                                           #
# --------------------------------------------------------------------------- #


class TestAnIncompleteRead:
    def test_a_held_card_with_more_relations_than_one_page_says_the_list_may_be_short(self):
        cards, nodes, _ = main_case()
        nodes[2]["relations"]["pageInfo"]["hasNextPage"] = True
        _action, body = the_notice(plan(cards, nodes)[0], "DRE-100")
        for ident in ("DRE-201", "DRE-202", "DRE-303"):
            held_line(body, ident)
        assert text_of(body).splitlines()[-1] == MAY_BE_SHORT

    def test_the_blocker_with_more_relations_than_one_page_says_so_too(self):
        cards, nodes, _ = main_case()
        nodes[0]["relations"]["pageInfo"]["hasNextPage"] = True
        _action, body = the_notice(plan(cards, nodes)[0], "DRE-100")
        assert text_of(body).splitlines()[-1] == MAY_BE_SHORT

    def test_a_chain_past_chain_reads_is_cut_and_says_so(self):
        entered = ago(72)
        chain = [f"DRE-{500 + i}" for i in range(1, 9)]
        cards = [board_card("DRE-100", "Intake", blocks=(chain[0],), entered=entered)]
        nodes = [node("DRE-100", "Intake", blocks=(chain[0],), entered=entered, assignee=DANA)]
        for here, nxt in zip(chain, chain[1:] + [None]):
            nodes.append(node(here, "Queued", blocks=(nxt,) if nxt else (), entered=ago(10)))
        items, fake, _ctx, _board = plan(cards, nodes)
        assert len(fake.asked) == lane.CHAIN_READS
        _action, body = the_notice(items, "DRE-100")
        for ident in chain[:lane.CHAIN_READS - 1]:
            held_line(body, ident)
        for ident in chain[lane.CHAIN_READS - 1:]:
            assert f"- {ident} " not in body
        assert text_of(body).splitlines()[-1] == MAY_BE_SHORT

    @staticmethod
    def wide_case():
        """DRE-100 blocks ten cards, and each of those blocks six more: sixty
        cards on the third level, more than one page of `CARDS_QUERY`."""
        entered = ago(60)
        middle = [f"DRE-{201 + i}" for i in range(10)]
        cards = [board_card("DRE-100", "Intake", blocks=middle, entered=entered)]
        nodes = [node("DRE-100", "Intake", blocks=middle, entered=entered, assignee=DANA)]
        deep: list = []
        for i, ident in enumerate(middle):
            mine = [f"DRE-{301 + 6 * i + j}" for j in range(6)]
            deep += mine
            nodes.append(node(ident, "Queued", blocks=mine, entered=ago(10)))
        nodes += [node(ident, "Queued", entered=ago(10)) for ident in deep]
        return cards, nodes, middle, deep

    def test_a_level_of_more_than_one_page_is_read_to_its_last_page(self):
        cards, nodes, middle, deep = self.wide_case()
        assert len(deep) > lane.PAGE
        fake = PagedLinear(nodes)
        _action, body = the_notice(plan(cards, nodes, fake=fake)[0], "DRE-100")
        assert fake.afters == [None, None, None, str(lane.PAGE)]
        assert fake.asked[3] == fake.asked[2]  # the second page asks the same cards
        assert "holds up 70 open cards:" in text_of(body)
        for ident in middle + deep:
            held_line(body, ident)
        assert MAY_BE_SHORT not in body

    def test_a_page_that_says_there_is_more_but_gives_no_cursor_ends_the_read(self):
        cards, nodes, middle, deep = self.wide_case()
        fake = PagedLinear(nodes, cursor=False)
        _action, body = the_notice(plan(cards, nodes, fake=fake)[0], "DRE-100")
        assert fake.afters == [None, None, None]
        for ident in middle + deep[:lane.PAGE]:
            held_line(body, ident)
        for ident in deep[lane.PAGE:]:
            assert f"- {ident} " not in body
        assert text_of(body).splitlines()[-1] == MAY_BE_SHORT

    def test_a_read_that_raises_plans_nothing_that_pass(self, capsys):
        cards, nodes, _ = main_case()
        items, fake, _ctx, _board = plan(cards, nodes, raises=RuntimeError("Linear 502"))
        assert items == []
        assert len(fake.asked) == 1
        assert "Linear 502" in capsys.readouterr().err

    def test_a_failure_on_a_later_level_plans_nothing_for_any_blocker(self):
        cards, nodes, _ = main_case()
        cards.append(board_card("DRE-110", "Hand-work", blocks=("DRE-211",), entered=ago(80)))
        nodes += [node("DRE-110", "Hand-work", blocks=("DRE-211",), entered=ago(80),
                       assignee=DANA),
                  node("DRE-211", "Queued", entered=ago(3))]

        class FailSecond(FakeLinear):
            def __call__(self, query, variables=None):
                if self.asked:
                    self.asked.append(["second"])
                    raise RuntimeError("timed out")
                return super().__call__(query, variables)

        fake = FailSecond(nodes)
        ctx = hygiene.make_context(HOME, gh=lambda argv: "[]",
                                   linear=hygiene.read_only_linear(fake), now=NOW,
                                   summary_card="DRE-900")
        lanes: dict = {}
        for c in cards:
            lanes.setdefault(c["state"]["name"], []).append(c)
        assert lane.plan(hygiene.Board(lanes=lanes, prs={}), ctx) == []


# --------------------------------------------------------------------------- #
# what does not qualify                                                        #
# --------------------------------------------------------------------------- #


class TestWhatDoesNotQualify:
    def test_the_same_card_at_47_hours_plans_nothing_and_reads_nothing(self):
        cards, nodes, _ = main_case()
        entered = ago(47)
        cards[0] = board_card("DRE-100", "Intake", blocks=("DRE-201", "DRE-202"),
                              entered=entered)
        items, fake, _ctx, _board = plan(cards, nodes)
        assert items == []
        assert fake.asked == []

    @pytest.mark.parametrize("closed", ["Done", "Canceled", "Duplicate"])
    def test_a_card_blocking_only_closed_cards_plans_nothing(self, closed):
        entered = ago(100)
        cards = [board_card("DRE-100", "Intake", blocks=("DRE-201", "DRE-202"),
                            entered=entered)]
        nodes = [node("DRE-100", "Intake", blocks=("DRE-201", "DRE-202"), entered=entered,
                      assignee=DANA),
                 node("DRE-201", "Done", blocks=("DRE-303",)),
                 node("DRE-202", closed),
                 node("DRE-303", "Queued")]
        items, _fake, _ctx, _board = plan(cards, nodes)
        assert items == []

    @pytest.mark.parametrize("assignee", [DANA, None])
    def test_a_closed_card_beside_an_open_one_is_not_listed_and_ends_its_chain(self, assignee):
        entered = ago(100)
        cards = [board_card("DRE-100", "Intake", blocks=("DRE-201", "DRE-202"),
                            entered=entered)]
        nodes = [node("DRE-100", "Intake", blocks=("DRE-201", "DRE-202"), entered=entered,
                      assignee=assignee),
                 node("DRE-201", "Queued", entered=ago(10)),
                 node("DRE-202", "Done", blocks=("DRE-303",)),
                 node("DRE-303", "Queued", entered=ago(10))]
        items, fake, _ctx, _board = plan(cards, nodes)
        _action, body = the_notice(items, "DRE-100")
        assert "holds up 1 open card:" in text_of(body)
        held_line(body, "DRE-201")
        for ident in ("DRE-202", "DRE-303"):
            assert f"- {ident} " not in body
        assert fake.asked == [["DRE-100"], ["DRE-201", "DRE-202"]]
        assert MAY_BE_SHORT not in body
        rows = lefts(items)
        if assignee is None:
            assert len(rows) == 1 and "holds up 1 open card(s)" in rows[0].recommendation
        else:
            assert rows == []

    def test_a_card_with_no_blocks_relation_is_never_read(self):
        cards = [board_card("DRE-100", "Intake", entered=ago(100))]
        items, fake, _ctx, _board = plan(cards, [])
        assert items == [] and fake.asked == []

    def test_a_relation_of_another_type_holds_nobody_up(self):
        entered = ago(100)
        cards = [board_card("DRE-100", "Intake", blocks=("DRE-201",), entered=entered)]
        nodes = [node("DRE-100", "Intake", blocks=("DRE-201",), entered=entered,
                      assignee=DANA, related=("DRE-209",)),
                 node("DRE-201", "Queued"), node("DRE-209", "Queued")]
        _action, body = the_notice(plan(cards, nodes)[0], "DRE-100")
        held_line(body, "DRE-201")
        assert "DRE-209" not in body

    def test_a_card_in_another_lane_is_not_this_lanes(self):
        cards = [board_card("DRE-100", "In Progress", blocks=("DRE-201",), entered=ago(100))]
        items, fake, _ctx, _board = plan(cards, [node("DRE-201", "Queued")])
        assert items == [] and fake.asked == []


class TestHandWork:
    def test_a_hand_work_card_over_48_hours_blocking_a_queued_card_plans_one_notice(self):
        entered = ago(50)
        cards = [board_card("DRE-120", "Hand-work", blocks=("DRE-221",), entered=entered,
                            slug="bureau-pipeline")]
        nodes = [node("DRE-120", "Hand-work", blocks=("DRE-221",), entered=entered,
                      assignee=DANA),
                 node("DRE-221", "Queued", entered=ago(26))]
        action, body = the_notice(plan(cards, nodes)[0], "DRE-120")
        assert body.splitlines()[0].startswith(
            f"🧹 hygiene: {TAG} — DRE-120 in Hand-work since {pt_day(entered)} · ")
        assert "Queued" in held_line(body, "DRE-221")
        assert action.lane == "Hand-work"

    def test_a_standing_card_plans_none(self):
        standing = json.loads((ROOT / "config" / "hand-work-age.json").read_text())["standing"]
        ident = standing[0]["card"]
        cards = [board_card(ident, "Hand-work", blocks=("DRE-221",), entered=ago(500))]
        nodes = [node(ident, "Hand-work", blocks=("DRE-221",), entered=ago(500),
                      assignee=DANA),
                 node("DRE-221", "Queued")]
        items, fake, _ctx, _board = plan(cards, nodes)
        assert items == [] and fake.asked == []


# --------------------------------------------------------------------------- #
# once per stay                                                                #
# --------------------------------------------------------------------------- #


class TestOncePerStay:
    def test_a_second_pass_over_the_same_board_is_suppressed_by_the_key(self):
        cards, nodes, _ = main_case()
        items, *_ = plan(cards, nodes)
        _action, body = the_notice(items, "DRE-100")
        # The first pass's receipt is on the card when the next pass reads it,
        # an hour later.
        cards[0]["comments"]["nodes"].insert(0, {"body": body, "createdAt": iso(NOW),
                                                 "user": None})
        again, _fake, _ctx, board = plan(cards, nodes, now=NOW + timedelta(hours=1))
        second, _ = the_notice(again, "DRE-100")
        assert hygiene._suppressed(board, second)

    def test_a_card_that_left_and_came_back_later_is_told_again(self):
        cards, nodes, entered = main_case()
        items, *_ = plan(cards, nodes)
        _first, body = the_notice(items, "DRE-100")
        back = entered + timedelta(days=4)
        later = back + timedelta(hours=50)
        cards[0] = board_card("DRE-100", "Intake", blocks=("DRE-201", "DRE-202"),
                              entered=back, comments=[body])
        nodes[0] = node("DRE-100", "Intake", blocks=("DRE-201", "DRE-202"), entered=back,
                        assignee=DANA)
        again, _fake, _ctx, board = plan(cards, nodes, now=later)
        second, _ = the_notice(again, "DRE-100")
        assert second.cause == f"DRE-100 in Intake since {pt_day(back)}"
        assert not hygiene._suppressed(board, second)


# --------------------------------------------------------------------------- #
# the owner                                                                    #
# --------------------------------------------------------------------------- #


def assert_no_owner(items, ident):
    _action, body = the_notice(items, ident)
    text = text_of(body)
    assert NO_OWNER in text
    for name in ("Dana Reyes", "Agent-Bureau", "bureau-tools", "Some Integration"):
        assert name not in text, name
    for held in ("DRE-201", "DRE-202", "DRE-303"):
        held_line(body, held)
    rows = lefts(items)
    assert len(rows) == 1
    assert rows[0].target == ident and rows[0].why == NO_OWNER


class TestTheOwner:
    def test_the_creator_is_named_when_nobody_is_assigned(self):
        cards, nodes, _ = main_case(assignee=None, creator=DANA)
        _action, body = the_notice(plan(cards, nodes)[0], "DRE-100")
        assert "Dana Reyes" in text_of(body)

    def test_the_assignee_wins_over_the_creator(self):
        other = {"id": "user-sam", "name": "Sam Ortiz", "displayName": "sam", "app": False}
        cards, nodes, _ = main_case(assignee=other, creator=DANA)
        _action, body = the_notice(plan(cards, nodes)[0], "DRE-100")
        assert "Sam Ortiz" in text_of(body) and "Dana Reyes" not in text_of(body)

    def test_the_display_name_is_used_when_the_name_is_null(self):
        cards, nodes, _ = main_case(assignee={**DANA, "name": None})
        _action, body = the_notice(plan(cards, nodes)[0], "DRE-100")
        assert "dana" in text_of(body) and NO_OWNER not in body

    def test_the_fleets_own_user_is_no_owner_to_tell(self):
        cards, nodes, _ = main_case(assignee=FLEET, creator=DANA)
        assert_no_owner(plan(cards, nodes)[0], "DRE-100")

    def test_the_operator_tools_creator_with_nobody_assigned_is_no_owner_to_tell(self):
        cards, nodes, _ = main_case(assignee=None, creator=TOOLS)
        assert_no_owner(plan(cards, nodes)[0], "DRE-100")

    def test_an_app_creator_with_nobody_assigned_is_no_owner_to_tell(self):
        cards, nodes, _ = main_case(assignee=None, creator=APP)
        items = plan(cards, nodes)[0]
        _action, body = the_notice(items, "DRE-100")
        assert body.splitlines()[0].startswith(f"🧹 hygiene: {TAG} —")
        assert_no_owner(items, "DRE-100")

    def test_a_user_with_no_name_at_all_is_no_owner_to_tell(self):
        cards, nodes, _ = main_case(assignee={"id": "x", "name": None, "displayName": None,
                                              "app": False})
        assert_no_owner(plan(cards, nodes)[0], "DRE-100")

    def test_an_owner_that_cannot_be_read_is_no_owner_to_tell(self):
        cards, nodes, _ = main_case(assignee=None, creator=None)
        assert_no_owner(plan(cards, nodes)[0], "DRE-100")

    def test_every_declared_identity_is_not_a_person(self):
        doc = json.loads((ROOT / "config" / "linear-identities.json").read_text())
        for row in doc["identities"]:
            user = {"id": "u", "name": None, "displayName": row["display_name"], "app": False}
            cards, nodes, _ = main_case(assignee=user)
            assert_no_owner(plan(cards, nodes)[0], "DRE-100")


# --------------------------------------------------------------------------- #
# the entry date                                                               #
# --------------------------------------------------------------------------- #


class TestTheEntryDate:
    def test_a_card_created_straight_into_intake_is_dated_by_its_creation(self):
        created = ago(49)
        cards = [board_card("DRE-100", "Intake", blocks=("DRE-201",), created=created)]
        nodes = [node("DRE-100", "Intake", blocks=("DRE-201",), created=created,
                      assignee=DANA),
                 node("DRE-201", "Queued", entered=ago(20))]
        action, _body = the_notice(plan(cards, nodes)[0], "DRE-100")
        assert action.cause == f"DRE-100 in Intake since {pt_day(created)}"

    def test_the_newest_move_into_the_lane_is_the_entry(self):
        old, new = ago(24 * 20), ago(60)
        c = board_card("DRE-100", "Intake", blocks=("DRE-201",), entered=new)
        c["history"]["nodes"].insert(0, {"createdAt": iso(old), "toState": {"name": "Intake"}})
        nodes = [node("DRE-100", "Intake", blocks=("DRE-201",), entered=new, assignee=DANA),
                 node("DRE-201", "Queued")]
        action, _body = the_notice(plan([c], nodes)[0], "DRE-100")
        assert action.cause == f"DRE-100 in Intake since {pt_day(new)}"

    def test_a_held_card_with_no_move_waits_from_its_creation(self):
        entered = ago(60)
        cards = [board_card("DRE-100", "Intake", blocks=("DRE-201",), entered=entered)]
        nodes = [node("DRE-100", "Intake", blocks=("DRE-201",), entered=entered, assignee=DANA),
                 node("DRE-201", "Queued", created=ago(24 * 9))]
        _action, body = the_notice(plan(cards, nodes)[0], "DRE-100")
        assert "9 days" in held_line(body, "DRE-201")


# --------------------------------------------------------------------------- #
# which leg                                                                    #
# --------------------------------------------------------------------------- #


class TestWhichLeg:
    @pytest.mark.parametrize("slug", [None, "agent-bureau"])
    def test_planned_only_by_the_home_leg(self, slug):
        entered = ago(60)
        cards = [board_card("DRE-100", "Intake", blocks=("DRE-201",), entered=entered,
                            slug=slug)]
        nodes = [node("DRE-100", "Intake", blocks=("DRE-201",), entered=entered,
                      assignee=DANA),
                 node("DRE-201", "Queued")]
        home, *_ = plan(cards, nodes, owner=HOME)
        the_notice(home, "DRE-100")
        for owner in ("EveryBite", "DeltaSolv"):
            items, fake, _ctx, _board = plan(cards, nodes, owner=owner)
            assert items == [] and fake.asked == []

    def test_a_held_card_in_another_owners_repo_is_still_listed(self):
        # The read carries no labels: a held card is listed whatever its repo,
        # here a DeltaSolv card on the board in Intake.
        entered = ago(60)
        cards = [board_card("DRE-100", "Intake", blocks=("DRE-201",), entered=entered),
                 board_card("DRE-201", "Intake", entered=ago(5), slug="deltasolv")]
        nodes = [node("DRE-100", "Intake", blocks=("DRE-201",), entered=entered,
                      assignee=DANA),
                 node("DRE-201", "Intake", entered=ago(5))]
        _action, body = the_notice(plan(cards, nodes, owner=HOME)[0], "DRE-100")
        assert "Intake" in held_line(body, "DRE-201")


# --------------------------------------------------------------------------- #
# the core's side of it                                                        #
# --------------------------------------------------------------------------- #


class TestTheCore:
    def test_the_board_read_covers_intake(self):
        assert "Intake" in hygiene.LANES
        assert "Intake" in hygiene.board_lanes()

    def test_hygiene_still_never_writes_a_card_into_intake(self):
        assert "Intake" in hygiene.REFUSED_LANES
        assert "Intake" not in hygiene.destinations()

    def test_the_act_and_its_tag_are_in_the_one_table(self):
        assert hygiene.TAGS[ACT] == TAG

    def test_the_act_is_declared_in_the_registry(self):
        acts = json.loads((ROOT / "config" / "pipeline-acts.json").read_text())["acts"]
        row = next(r for r in acts if r["name"] == ACT)
        assert row["tag"] == TAG
        assert row["adopted"] is True
        assert row["emits"] == {"file": "scripts/hygiene.py", "anchor": f'"{TAG}"'}
        assert row["subscriber"] == "hygiene.yml"
        assert pipeline_act.problems() == []

    def test_a_receipt_may_carry_lines_after_its_evidence(self):
        body = hygiene.receipt(ACT, "DRE-1 in Intake since 2026-10-01", ["entered Intake x"],
                               NOW, ["Dana —", "- DRE-2 — a title", "", MAY_BE_SHORT])
        lines = body.splitlines()
        assert lines[1] == "evidence: entered Intake x"
        assert lines[2] == ""
        assert text_of(body).splitlines()[-1] == MAY_BE_SHORT
        assert hygiene.receipt(*hygiene._receipt_parts(body)) == body
        hygiene.linear_comment(board_card("DRE-1", "Intake"), body)

    def test_a_note_line_cannot_carry_a_newline(self):
        with pytest.raises(ValueError):
            hygiene.receipt(ACT, "DRE-1 in Intake since 2026-10-01", ["x"], NOW, ["a\nb"])

    def test_a_receipt_with_no_notes_is_unchanged(self):
        body = hygiene.receipt("hygiene-hold-clear", "cause", ["x"], NOW)
        assert body == pipeline_act.receipt(
            "hygiene-hold-clear", f"🧹 hygiene: hyg-hold-cleared — cause · {hygiene.pt(NOW)}"
                                  "\nevidence: x")

    def test_a_line_no_composition_writes_is_still_refused(self):
        # The notes stand behind one blank line; a line pressed against the
        # evidence is not a shape `receipt` writes.
        body = hygiene.receipt(ACT, "DRE-1 in Intake since 2026-10-01", ["x"], NOW, ["a"])
        forged = body.replace("\nevidence: x\n", "\nevidence: x\nextra line\n")
        assert forged != body
        with pytest.raises(hygiene.Forbidden):
            hygiene.linear_comment(board_card("DRE-1", "Intake"), forged)
