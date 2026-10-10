"""RED-first: the hygiene agent's Triage lane (DRE-5410).

`scripts/hygiene_triage.py` reads the broken-card lane once and fixes the
mechanical defects the standard already names, returning the card to
`Backlog` — never `Todo` — with the cause named:

  1. a prose blocker with no relation — the relation is added, which makes the
     sentence true, and a card carrying a routing verdict returns to Backlog;
  2. a dependency loop a Done card already broke — the card returns to
     Backlog with the loop named;
  3. a retired-repo card — marked `operator-step` and parked in Backlog;
  4. a proof with an open pull request — moved to In Review;
  5. a card whose parent epic is Canceled or Duplicate — canceled;
  6. a card held `no-route` (DRE-6190) — its newest `🔒 hold:` stamp says so,
     and the holds lane owns its lift: one `Left` row and no other rule;
  7. a card held on a planning reason (DRE-6451) — `plan-critic-bound` or
     `epic-rereview-twice`: one `Left` row naming the reason, its age in
     Triage and the park note, and once past `HYGIENE_TRIAGE_ALARM_HOURS` an
     alarm receipt the core's (tag, cause) key posts once.

Everything else is a `Left` row naming what a person must do.

The fixture is `tests/fixtures/hygiene-triage-2026-09-30.json`, in the shape
every hygiene lane fixture takes: `lanes`, `prs` and a `gh` map from an argv
joined with single spaces to the stdout the fake `ctx.gh` answers. The ids a
prose line claims that are not on the board are answered by the fake
`ctx.linear` below, from `ISSUES`.

Run: cd bureau-pipeline && python3 -m pytest tests/test_hygiene_triage.py -v
"""
from __future__ import annotations

import ast
import copy
import importlib.util
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import hygiene  # noqa: E402
import linear_ops  # noqa: E402
import reconcile  # noqa: E402
import routing_verdict  # noqa: E402

MODULE_PATH = ROOT / "scripts" / "hygiene_triage.py"
FIXTURE = ROOT / "tests" / "fixtures" / "hygiene-triage-2026-09-30.json"

#: 21:05 UTC on 2026-09-30 is 14:05 in Pacific Daylight Time.
NOW = datetime(2026, 9, 30, 21, 5, tzinfo=UTC)
HOME = "dreadnought-foundry"
BP = "dreadnought-foundry/bureau-pipeline"
SUMMARY = "DRE-900"

#: The cards a prose line claims that the board read does not carry, and the
#: lane each is in. Anything else answers Linear's own "Entity not found".
ISSUES = {"DRE-4301": "Done", "DRE-4302": "Done"}


def load_lane():
    if not MODULE_PATH.exists():
        return None
    spec = importlib.util.spec_from_file_location("hygiene_triage", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lane = load_lane()


def fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class FixtureGh:
    """The read-only `gh` a lane sees, answering ONLY from the fixture's `gh`
    map — a read the map does not hold fails the test. The core's own open
    pull request list is answered from the fixture's `prs`."""

    def __init__(self, doc: dict):
        self.answers = dict(doc["gh"])
        self.prs = doc["prs"]
        self.calls: list = []

    def __call__(self, argv):
        argv = [str(a) for a in argv]
        refusal = hygiene.read_gh_refusal(argv)
        assert refusal is None, f"the lane asked ctx.gh for a write: {refusal}"
        if argv[:3] == ["gh", "pr", "list"] and "open" in argv:
            return json.dumps(self.prs.get(argv[argv.index("--repo") + 1], []))
        key = " ".join(argv)
        self.calls.append(key)
        if key not in self.answers:
            raise AssertionError(f"the fixture's gh map has no answer for {key!r}")
        return self.answers[key]


class FixtureLinear:
    """The read-only `ctx.linear`: one issue read by identifier, answered from
    `ISSUES`, and Linear's own refusal for an id that names no card."""

    def __init__(self, issues=None):
        self.issues = dict(ISSUES if issues is None else issues)
        self.calls: list = []

    def __call__(self, query, variables=None):
        assert not query.lstrip().lower().startswith("mutation")
        ident = (variables or {}).get("id")
        self.calls.append(ident)
        if ident not in self.issues:
            raise linear_ops.LinearError(
                "linear error from https://api.linear.app/graphql: "
                "[{'message': 'Entity not found: Issue', "
                "'extensions': {'code': 'INPUT_ERROR'}}]")
        return {"issue": {"identifier": ident, "state": {"name": self.issues[ident]},
                          "children": {"nodes": []}}}


def context(doc, *, linear=None):
    gh = FixtureGh(doc)
    linear = linear if linear is not None else FixtureLinear()
    ctx = hygiene.make_context(HOME, gh=gh, linear=linear, dry_run=False, now=NOW,
                               summary_card=SUMMARY)
    return ctx, gh, linear


def plan(doc=None, *, linear=None):
    doc = doc if doc is not None else fixture()
    ctx, gh, linear = context(doc, linear=linear)
    lanes = {name: [c for c in cards if c.get("identifier") != SUMMARY]
             for name, cards in doc["lanes"].items()}
    board = hygiene.Board(lanes=lanes, prs=doc["prs"])
    assert lane is not None, f"{MODULE_PATH.name} does not exist yet"
    return lane.plan(board, ctx), ctx, gh, linear


def actions(items, target=None):
    return [i for i in items if isinstance(i, hygiene.Action)
            and (target is None or i.target == target)]


def lefts(items, target=None):
    return [i for i in items if isinstance(i, hygiene.Left)
            and (target is None or i.target == target)]


def card(doc, ident):
    return next(c for cards in doc["lanes"].values() for c in cards
                if c["identifier"] == ident)


def kinds(action):
    return [w.kind for w in action.writes]


def comment_of(action):
    return next(w for w in action.writes if w.kind == "linear_comment").body


def the_action(items, target):
    found = actions(items, target)
    assert len(found) == 1, found
    return found[0]


# --------------------------------------------------------------------------- #
# the lane's shape                                                             #
# --------------------------------------------------------------------------- #


class TestTheLane:
    def test_its_heading_is_the_triage_lane(self):
        assert lane is not None, f"{MODULE_PATH.name} does not exist yet"
        assert lane.LANE == "Triage"

    def test_the_core_discovers_it_by_the_glob(self):
        names = [m.__name__ for m in hygiene.discover()]
        assert "hygiene_triage" in names

    def test_every_row_it_returns_takes_its_heading(self):
        items, _ctx, _gh, _linear = plan()
        assert items
        assert {i.lane for i in items} == {"Triage"}

    def test_another_owners_card_and_the_summary_card_are_never_touched(self):
        items, _ctx, _gh, _linear = plan()
        targets = {i.target for i in items}
        assert "DRE-4461" not in targets  # repo:atlas — the EveryBite leg's
        assert SUMMARY not in targets

    def test_cards_outside_triage_are_never_rows(self):
        items, _ctx, _gh, _linear = plan()
        assert not {"DRE-4405", "DRE-4413"} & {i.target for i in items}


# --------------------------------------------------------------------------- #
# (1) a prose blocker with no relation                                         #
# --------------------------------------------------------------------------- #


class TestAProseBlocker:
    def test_a_done_card_named_in_prose_with_a_verdict_is_three_writes_in_order(self):
        doc = fixture()
        items, _ctx, _gh, _linear = plan(doc)
        action = the_action(items, "DRE-4401")
        assert action.act == "hygiene-triage-return"
        assert kinds(action) == ["linear_relation", "linear_comment", "linear_state"]
        relation, note, state = action.writes
        assert relation.card == "DRE-4401" and relation.blocked_by == "DRE-4301"
        assert action.cause == "relation added, blocked by DRE-4301"
        first = note.body.splitlines()[0]
        assert first.startswith("🧹 hygiene: hyg-triage-returned — relation added, blocked by DRE-4301")
        assert state.lane == "Backlog" and state.park is False
        assert lefts(items, "DRE-4401") == []

    def test_the_writes_carry_the_board_card_never_a_bare_id(self):
        doc = fixture()
        items, _ctx, _gh, _linear = plan(doc)
        for write in the_action(items, "DRE-4401").writes:
            assert write.card_id == card(doc, "DRE-4401")["id"]
            assert write.children == ()

    def test_the_same_card_with_no_verdict_gets_the_relation_no_move_and_a_left_row(self):
        items, _ctx, _gh, _linear = plan()
        found = actions(items, "DRE-4402")
        writes = [w for a in found for w in a.writes]
        relations = [w for w in writes if w.kind == "linear_relation"]
        assert [(w.card, w.blocked_by) for w in relations] == [("DRE-4402", "DRE-4301")]
        assert not [w for w in writes if w.kind == "linear_state"]
        rows = lefts(items, "DRE-4402")
        assert len(rows) == 1
        assert "verdict" in rows[0].why

    def test_a_claim_on_the_board_resolves_without_a_linear_read(self):
        items, _ctx, _gh, linear = plan()
        action = the_action(items, "DRE-4404")
        assert kinds(action) == ["linear_relation", "linear_comment", "linear_state"]
        assert action.writes[0].blocked_by == "DRE-4405"
        assert action.cause == "relation added, blocked by DRE-4405"
        assert "DRE-4405" not in linear.calls

    def test_an_id_that_resolves_nowhere_is_no_write_and_one_left_row(self):
        items, _ctx, _gh, _linear = plan()
        assert actions(items, "DRE-4403") == []
        rows = lefts(items, "DRE-4403")
        assert len(rows) == 1
        assert "DRE-4999" in rows[0].why
        assert "reword" in rows[0].recommendation and "author" in rows[0].recommendation

    def test_each_id_off_the_board_is_read_once_per_pass(self):
        _items, _ctx, _gh, linear = plan()
        assert sorted(linear.calls) == ["DRE-4301", "DRE-4999"]

    def test_a_partly_resolvable_line_adds_what_resolves_and_moves_nothing(self):
        doc = fixture()
        card(doc, "DRE-4401")["description"] = card(doc, "DRE-4401")["description"].replace(
            "DRE-4301", "DRE-4301, DRE-4999")
        items, _ctx, _gh, _linear = plan(doc)
        writes = [w for a in actions(items, "DRE-4401") for w in a.writes]
        assert [w.blocked_by for w in writes if w.kind == "linear_relation"] == ["DRE-4301"]
        assert not [w for w in writes if w.kind == "linear_state"]
        assert len(lefts(items, "DRE-4401")) == 1

    def test_a_linear_read_that_fails_is_not_an_id_that_resolves_nowhere(self, capsys):
        def failing(query, variables=None):
            raise linear_ops.LinearError("linear error from https://api.linear.app/graphql: HTTP 502")

        items, _ctx, _gh, _linear = plan(linear=failing)
        assert actions(items, "DRE-4401") == [] and lefts(items, "DRE-4401") == []
        assert lefts(items, "DRE-4403") == []
        assert "DRE-4401 skipped" in capsys.readouterr().err
        assert actions(items, "DRE-4404")  # resolved on the board, no read needed

    def test_nothing_rewrites_a_description(self):
        items, _ctx, _gh, _linear = plan()
        assert {w.kind for a in actions(items) for w in a.writes} <= {
            "linear_relation", "linear_comment", "linear_state", "linear_label"}


# --------------------------------------------------------------------------- #
# (2) a dependency loop                                                        #
# --------------------------------------------------------------------------- #


class TestADependencyLoop:
    def test_a_loop_with_a_done_card_returns_the_open_card_with_the_loop_named(self):
        items, _ctx, _gh, _linear = plan()
        action = the_action(items, "DRE-4411")
        assert action.act == "hygiene-triage-return"
        assert action.cause == "loop DRE-4411 → DRE-4310 → DRE-4411 broken, DRE-4310 is Done"
        assert kinds(action) == ["linear_comment", "linear_state"]
        assert action.writes[1].lane == "Backlog" and action.writes[1].park is False
        assert comment_of(action).splitlines()[0].startswith(
            "🧹 hygiene: hyg-triage-returned — loop DRE-4411 → DRE-4310 → DRE-4411 broken")
        assert lefts(items, "DRE-4411") == []

    def test_a_loop_of_open_cards_is_no_write_and_one_left_row_naming_every_card(self):
        items, _ctx, _gh, _linear = plan()
        assert actions(items, "DRE-4412") == []
        rows = lefts(items, "DRE-4412")
        assert len(rows) == 1
        assert "DRE-4412" in rows[0].why and "DRE-4413" in rows[0].why

    def test_a_three_card_loop_is_read_through_the_board(self):
        doc = fixture()
        mid = copy.deepcopy(card(doc, "DRE-4413"))
        mid["identifier"], mid["id"] = "DRE-4414", mid["id"][:-4] + "4414"
        mid["inverseRelations"]["nodes"] = [
            {"type": "blocks", "issue": {"identifier": "DRE-4413", "state": {"name": "Todo"}}}]
        card(doc, "DRE-4413")["inverseRelations"]["nodes"] = [
            {"type": "blocks", "issue": {"identifier": "DRE-4412", "state": {"name": "Triage"}}}]
        card(doc, "DRE-4412")["inverseRelations"]["nodes"] = [
            {"type": "blocks", "issue": {"identifier": "DRE-4414", "state": {"name": "Todo"}}}]
        doc["lanes"]["Todo"].append(mid)
        items, _ctx, _gh, _linear = plan(doc)
        rows = lefts(items, "DRE-4412")
        assert len(rows) == 1
        assert all(i in rows[0].why for i in ("DRE-4412", "DRE-4413", "DRE-4414"))

    def test_a_broken_loop_on_a_card_with_no_verdict_is_left_unmoved(self):
        doc = fixture()
        card(doc, "DRE-4411")["comments"]["nodes"] = []
        items, _ctx, _gh, _linear = plan(doc)
        assert not [w for a in actions(items, "DRE-4411") for w in a.writes
                    if w.kind == "linear_state"]
        assert len(lefts(items, "DRE-4411")) == 1


# --------------------------------------------------------------------------- #
# (3) a retired-repo card                                                      #
# --------------------------------------------------------------------------- #


class TestARetiredRepo:
    @pytest.mark.parametrize("ident, cause", [
        ("DRE-4421", "retired repo legacy-site"),
        ("DRE-4422", "archived repo dreadnought-foundry/agent-bureau-demo"),
    ])
    def test_it_is_marked_operator_step_and_parked(self, ident, cause):
        items, ctx, _gh, _linear = plan()
        action = the_action(items, ident)
        assert action.act == "hygiene-triage-return"
        assert action.cause == cause
        assert kinds(action) == ["linear_label", "linear_comment", "linear_state"]
        label, note, state = action.writes
        # DRE-6228: a repo that is gone is the operator's to re-point — an
        # operator step. `hand-built` is the CEO's mark, never a writer's.
        assert (label.label, label.add) == (routing_verdict.OPERATOR_STEP_LABEL, True)
        assert label.label == "operator-step"
        assert all(getattr(w, "label", None) != routing_verdict.HAND_BUILT_LABEL
                   for w in action.writes)
        assert note.body.splitlines()[0].startswith(f"🧹 hygiene: hyg-triage-returned — {cause}")
        assert (state.lane, state.park) == ("Backlog", True)
        for write in action.writes:
            hygiene.guard(write, ctx)

    def test_a_live_repo_is_read_once_and_is_not_retired(self):
        _items, _ctx, gh, _linear = plan()
        assert gh.calls.count(f"gh api repos/{BP}") == 1


# --------------------------------------------------------------------------- #
# (4) a proof with an open pull request                                        #
# --------------------------------------------------------------------------- #


class TestAProofWithAnOpenPullRequest:
    def test_it_moves_to_in_review_under_a_receipt_naming_the_pull_request(self):
        items, ctx, _gh, _linear = plan()
        action = the_action(items, "DRE-4431")
        assert action.act == "hygiene-review-move"
        assert action.cause == "open pull request #688"
        assert kinds(action) == ["linear_comment", "linear_state"]
        assert comment_of(action).splitlines()[0].startswith(
            "🧹 hygiene: hyg-moved-to-review — open pull request #688")
        assert action.writes[1].lane == "In Review"
        for write in action.writes:
            hygiene.guard(write, ctx)

    def test_a_proof_with_no_pull_request_gets_nothing_from_this_rule(self):
        items, _ctx, _gh, _linear = plan()
        assert actions(items, "DRE-4432") == []

    def test_a_merged_pull_request_is_not_an_open_one(self):
        doc = fixture()
        key = next(k for k in doc["gh"] if k.endswith("head:agent/DRE-4431"))
        doc["gh"][key] = doc["gh"][key].replace('"OPEN"', '"MERGED"')
        items, _ctx, _gh, _linear = plan(doc)
        assert actions(items, "DRE-4431") == []


# --------------------------------------------------------------------------- #
# (5) a moot card                                                              #
# --------------------------------------------------------------------------- #


class TestAMootCard:
    def test_a_card_under_a_canceled_epic_is_canceled_with_the_parent_named(self):
        items, ctx, _gh, _linear = plan()
        action = the_action(items, "DRE-4441")
        assert action.act == "hygiene-card-cancel"
        assert action.cause == "parent DRE-4440 is Canceled"
        assert kinds(action) == ["linear_comment", "linear_state"]
        assert comment_of(action).splitlines()[0].startswith(
            "🧹 hygiene: hyg-card-canceled — parent DRE-4440 is Canceled")
        assert action.writes[1].lane == "Canceled"
        for write in action.writes:
            hygiene.guard(write, ctx)

    def test_a_duplicate_parent_is_named_as_one(self):
        doc = fixture()
        card(doc, "DRE-4441")["parent"]["state"]["name"] = "Duplicate"
        items, _ctx, _gh, _linear = plan(doc)
        assert the_action(items, "DRE-4441").cause == "parent DRE-4440 is Duplicate"

    def test_the_same_card_given_children_is_refused_by_the_cores_guard(self):
        doc = fixture()
        moot = card(doc, "DRE-4441")
        moot["children"]["nodes"] = [{"id": "c-1", "identifier": "DRE-4442",
                                      "createdAt": "2026-09-21T16:00:00.000Z",
                                      "state": {"name": "Todo"}}]
        ctx, _gh, _linear = context(doc)
        with pytest.raises(hygiene.Forbidden):
            hygiene.guard(hygiene.linear_state(moot, "Canceled"), ctx)
        items, _ctx, _gh, _linear = plan(doc)
        assert not [w for a in actions(items, "DRE-4441") for w in a.writes
                    if w.kind == "linear_state" and w.lane == "Canceled"]
        assert len(lefts(items, "DRE-4441")) == 1


# --------------------------------------------------------------------------- #
# (6) a card held no-route (DRE-6190)                                          #
# --------------------------------------------------------------------------- #

NO_ROUTE_STAMP = "🔒 hold: reason=no-route at=repo:legacy-site lifts=repo-on-rail by=reconcile.py"


def comment(body, at="2026-09-30T19:00:00.000Z"):
    return {"body": body, "createdAt": at, "user": {"id": "u-synthetic"}}


class TestAHeldNoRouteCard:
    def test_the_fixture_card_carries_the_label_and_the_stamp_newest(self):
        held = card(fixture(), "DRE-4423")
        assert {"repo:legacy-site", "needs-human"} <= {n["name"] for n in held["labels"]["nodes"]}
        assert held["comments"]["nodes"][-1]["body"] == NO_ROUTE_STAMP

    def test_it_is_one_left_row_and_no_write(self):
        items, _ctx, _gh, _linear = plan()
        assert actions(items, "DRE-4423") == []
        rows = lefts(items, "DRE-4423")
        assert len(rows) == 1
        row = rows[0]
        assert row.why == ("held no-route on repo:legacy-site — the slug is not on the "
                           "dispatch rail")
        assert "repo:" in row.recommendation
        assert "config/repo-map.json" in row.recommendation
        assert "holds lane" in row.recommendation
        assert row.recommendation == (
            "correct the card's repo: label to a slug on the rail, or add the repo to "
            "config/repo-map.json if it should route; the holds lane reads the card's "
            "current label, lifts the hold and sends the card to Planning on its next "
            "pass, and nothing on the card needs clearing")

    @pytest.mark.parametrize("ident", ["DRE-4421", "DRE-4422"])
    def test_the_same_slug_and_the_archived_repo_with_no_stamp_are_still_retired(self, ident):
        items, _ctx, _gh, _linear = plan()
        action = the_action(items, ident)
        assert action.act == "hygiene-triage-return"
        assert kinds(action) == ["linear_label", "linear_comment", "linear_state"]
        assert action.writes[0].label == "operator-step"
        assert (action.writes[2].lane, action.writes[2].park) == ("Backlog", True)

    def test_a_newer_stamp_of_another_reason_decides_and_the_card_is_retired(self):
        doc = fixture()
        card(doc, "DRE-4423")["comments"]["nodes"].append(comment(
            "🔒 hold: reason=dead-run-cap at=none lifts=unpark-marker by=dead_run.py"))
        items, _ctx, _gh, _linear = plan(doc)
        action = the_action(items, "DRE-4423")
        assert action.cause == "retired repo legacy-site"
        assert kinds(action) == ["linear_label", "linear_comment", "linear_state"]
        assert lefts(items, "DRE-4423") == []

    def test_the_stamp_without_the_label_is_retired_as_today(self):
        doc = fixture()
        held = card(doc, "DRE-4423")
        held["labels"]["nodes"] = [n for n in held["labels"]["nodes"] if n["name"] != "needs-human"]
        items, _ctx, _gh, _linear = plan(doc)
        assert the_action(items, "DRE-4423").cause == "retired repo legacy-site"
        assert lefts(items, "DRE-4423") == []

    def test_a_spent_stamp_is_no_hold_and_the_card_is_read_as_today(self):
        doc = fixture()
        card(doc, "DRE-4423")["comments"]["nodes"].append(comment(
            "🔓 hold lifted: reason=no-route because=operator by=hold.py"))
        items, _ctx, _gh, _linear = plan(doc)
        assert the_action(items, "DRE-4423").cause == "retired repo legacy-site"

    def test_a_canceled_parent_outranks_the_hold(self):
        doc = fixture()
        card(doc, "DRE-4423")["parent"] = copy.deepcopy(card(doc, "DRE-4441")["parent"])
        items, _ctx, _gh, _linear = plan(doc)
        action = the_action(items, "DRE-4423")
        assert action.act == "hygiene-card-cancel"
        assert action.cause == "parent DRE-4440 is Canceled"
        assert action.writes[-1].lane == "Canceled"
        assert lefts(items, "DRE-4423") == []

    def test_a_prose_blocker_on_a_held_card_gets_no_relation_and_no_move(self):
        doc = fixture()
        card(doc, "DRE-4423")["description"] = card(doc, "DRE-4401")["description"]
        items, _ctx, _gh, _linear = plan(doc)
        assert actions(items, "DRE-4423") == []
        rows = lefts(items, "DRE-4423")
        assert len(rows) == 1 and "no-route" in rows[0].why

    def test_a_stamp_at_repo_none_says_the_card_wears_no_repo_label(self):
        doc = fixture()
        held = card(doc, "DRE-4423")
        held["labels"]["nodes"] = [n for n in held["labels"]["nodes"]
                                   if not n["name"].startswith("repo:")]
        held["comments"]["nodes"][-1]["body"] = NO_ROUTE_STAMP.replace(
            "at=repo:legacy-site", "at=repo:none")
        items, _ctx, _gh, _linear = plan(doc)
        rows = lefts(items, "DRE-4423")
        assert len(rows) == 1
        assert rows[0].why == "held no-route — the card wears no repo: label"


# --------------------------------------------------------------------------- #
# everything else — a row for a person                                         #
# --------------------------------------------------------------------------- #


class TestLeftForAPerson:
    def test_a_plan_parked_with_needs_human_is_a_left_row(self):
        items, _ctx, _gh, _linear = plan()
        assert actions(items, "DRE-4451") == []
        rows = lefts(items, "DRE-4451")
        assert len(rows) == 1
        assert "needs-human" in rows[0].why

    def test_a_card_with_no_repo_label_is_a_left_row_naming_the_label(self):
        doc = fixture()
        bare = card(doc, "DRE-4432")
        bare["labels"]["nodes"] = [{"name": "agent:engineer"}]
        bare["title"] = "a card nobody labeled"
        items, _ctx, _gh, _linear = plan(doc)
        rows = lefts(items, "DRE-4432")
        assert len(rows) == 1 and "repo:" in rows[0].why

    def test_every_triage_card_in_scope_is_an_action_or_a_left_row(self):
        doc = fixture()
        items, ctx, _gh, _linear = plan(doc)
        scoped = {c["identifier"] for c in hygiene.Board(lanes=doc["lanes"]).cards(ctx, "Triage")}
        assert {i.target for i in items} == scoped


# --------------------------------------------------------------------------- #
# the whole fixture                                                            #
# --------------------------------------------------------------------------- #

#: What the fixture yielded before DRE-6451 added its two planning-held cards,
#: and what those cards add — the first must not move.
EXPECTED_BEFORE = {
    "DRE-4401": "hygiene-triage-return",
    "DRE-4402": "hygiene-cause-name",
    "DRE-4404": "hygiene-triage-return",
    "DRE-4411": "hygiene-triage-return",
    "DRE-4421": "hygiene-triage-return",
    "DRE-4422": "hygiene-triage-return",
    "DRE-4431": "hygiene-review-move",
    "DRE-4441": "hygiene-card-cancel",
}
LEFT_BEFORE = ["DRE-4402", "DRE-4403", "DRE-4412", "DRE-4423", "DRE-4432", "DRE-4451"]
PLANNING_HELD = ("DRE-4424", "DRE-4425")
EXPECTED = {**EXPECTED_BEFORE, "DRE-4424": "hygiene-triage-alarm"}
LEFT = sorted(LEFT_BEFORE + list(PLANNING_HELD))

_CLOCK = re.compile(r"\b\d{1,2}:\d{2}\b|\bPT\b|\bUTC\b")
_COUNT = re.compile(r"\b\d+\s+(?:time|times|attempt|attempts|round|rounds|minute|minutes|"
                    r"hour|hours|card|cards|relation|relations|id|ids)\b", re.I)


class TestTheWholeFixture:
    def test_the_fixture_yields_exactly_these_actions_and_left_rows(self):
        items, _ctx, _gh, _linear = plan()
        assert {a.target: a.act for a in actions(items)} == EXPECTED
        assert len(actions(items)) == len(EXPECTED)
        assert sorted(r.target for r in lefts(items)) == LEFT

    def test_every_card_before_the_planning_holds_yields_what_it_yielded(self):
        items, _ctx, _gh, _linear = plan()
        before = [i for i in items if i.target not in PLANNING_HELD]
        assert {a.target: a.act for a in actions(before)} == EXPECTED_BEFORE
        assert len(actions(before)) == len(EXPECTED_BEFORE)
        assert sorted(r.target for r in lefts(before)) == LEFT_BEFORE

    def test_no_action_writes_todo_in_progress_or_done(self):
        items, ctx, _gh, _linear = plan()
        lanes = [w.lane for a in actions(items) for w in a.writes if w.kind == "linear_state"]
        assert lanes
        assert not {"Todo", "In Progress", "Done"} & set(lanes)
        for action in actions(items):
            for write in action.writes:
                hygiene.guard(write, ctx)

    def test_no_cause_carries_a_count_or_a_clock(self):
        items, _ctx, _gh, _linear = plan()
        for action in actions(items):
            assert not _CLOCK.search(action.cause), action.cause
            assert not _COUNT.search(action.cause), action.cause
            assert "·" not in action.cause

    def test_every_receipt_carries_its_actions_cause_and_evidence(self):
        items, _ctx, _gh, _linear = plan()
        for action in actions(items):
            head = hygiene.read_receipt(comment_of(action))
            assert head["tag"] == hygiene.TAGS[action.act]
            assert head["cause"] == action.cause
            assert action.evidence

    def test_every_gh_read_is_answered_from_the_fixture_and_none_repeats(self):
        doc = fixture()
        _items, _ctx, gh, _linear = plan(doc)
        assert len(gh.calls) == len(set(gh.calls))
        assert set(gh.calls) == set(doc["gh"])


# --------------------------------------------------------------------------- #
# idempotency, through the core's key                                          #
# --------------------------------------------------------------------------- #


def run(doc, monkeypatch, linear=None):
    """One leg through the core, this lane alone, the write seam recorded."""
    sent: list = []
    monkeypatch.setattr(hygiene, "send", lambda write, ctx: sent.append(write))
    monkeypatch.setattr(hygiene, "discover", lambda lane_dir=None: [lane])
    ctx, _gh, _linear = context(doc, linear=linear)
    board_doc = {"taken_at": "2026-09-30T21:00:00Z", "lanes": doc["lanes"]}
    return sent, hygiene.run_leg(board_doc, ctx)


def add_receipts(doc, sent):
    """Every receipt the pass posted, now in its card's comment window."""
    for write in sent:
        if write.kind == "linear_comment":
            card(doc, write.card)["comments"]["nodes"].append(
                {"body": write.body, "createdAt": "2026-09-30T21:05:00.000Z",
                 "user": {"id": "u-hygiene"}})


class TestIdempotency:
    def test_a_second_pass_over_an_unchanged_fixture_sends_nothing(self, monkeypatch):
        doc = fixture()
        sent, ledger = run(doc, monkeypatch)
        assert {a["target"] for a in ledger["actions"] if a["outcome"] == "executed"} == set(EXPECTED)
        add_receipts(doc, sent)
        sent2, ledger2 = run(doc, monkeypatch)
        assert sent2 == []
        assert {a["outcome"] for a in ledger2["actions"]} == {"suppressed"}

    def test_the_same_card_back_for_a_second_undeclared_id_is_fixed_again(self, monkeypatch):
        doc = fixture()
        sent, _ledger = run(doc, monkeypatch)
        add_receipts(doc, sent)
        back = card(doc, "DRE-4401")
        # The relation the first pass added is on the board now, and the
        # author's line has grown a second id nothing stands behind.
        back["inverseRelations"]["nodes"].append(
            {"type": "blocks", "issue": {"identifier": "DRE-4301", "state": {"name": "Done"}}})
        back["description"] = back["description"].replace("DRE-4301", "DRE-4301, DRE-4302")
        sent2, ledger2 = run(doc, monkeypatch)
        again = [a for a in ledger2["actions"] if a["target"] == "DRE-4401"]
        assert [(a["outcome"], a["cause"]) for a in again] == [
            ("executed", "relation added, blocked by DRE-4302")]
        relations = [w.blocked_by for w in sent2
                     if w.kind == "linear_relation" and w.card == "DRE-4401"]
        assert relations == ["DRE-4302"]
        assert [w.lane for w in sent2 if w.kind == "linear_state" and w.card == "DRE-4401"] == [
            "Backlog"]

    def test_a_card_back_under_a_parent_in_a_new_state_is_a_new_cause(self, monkeypatch):
        doc = fixture()
        sent, _ledger = run(doc, monkeypatch)
        add_receipts(doc, sent)
        card(doc, "DRE-4441")["parent"]["state"]["name"] = "Duplicate"
        _sent2, ledger2 = run(doc, monkeypatch)
        again = [a for a in ledger2["actions"] if a["target"] == "DRE-4441"]
        assert [(a["outcome"], a["cause"]) for a in again] == [
            ("executed", "parent DRE-4440 is Duplicate")]


# --------------------------------------------------------------------------- #
# (7) a card held on a planning reason (DRE-6451)                              #
# --------------------------------------------------------------------------- #

ALARM_VAR = "HYGIENE_TRIAGE_ALARM_HOURS"
CRITIC_STAMP = "🔒 hold: reason=plan-critic-bound at=none lifts=manual by=plan.yml"
REREVIEW_STAMP = ("🔒 hold: reason=epic-rereview-twice at=none lifts=manual "
                  "by=rereview_watch.py")
PARKED = "🛑 Parked in Triage with needs-human for an operator — "
ALARM_CAUSE = "held plan-critic-bound past the 8-hour bound in Triage"
WAY_BACK = ("read the park note's own way back — clear needs-human and move the card to "
            "Planning for a fresh planning attempt, or answer the Green Light question "
            "when the exit asked one")


def park_note(doc, ident):
    return card(doc, ident)["comments"]["nodes"][-2]["body"]


@pytest.fixture(autouse=True)
def _no_alarm_override(monkeypatch):
    monkeypatch.delenv(ALARM_VAR, raising=False)


class TestAPlanningHeldCard:
    def test_the_reasons_and_the_bound_are_named_once(self):
        assert lane.PLANNING_REASONS == ("plan-critic-bound", "epic-rereview-twice")
        assert lane.DEFAULT_ALARM_HOURS == 8
        assert lane.alarm_hours() == 8

    @pytest.mark.parametrize("ident, stamp, entered", [
        ("DRE-4424", CRITIC_STAMP, "2026-09-30T06:00:00.000Z"),
        ("DRE-4425", REREVIEW_STAMP, "2026-09-30T19:00:00.000Z"),
    ])
    def test_the_fixture_cards_carry_the_label_the_park_note_and_the_stamp_newest(
            self, ident, stamp, entered):
        held = card(fixture(), ident)
        assert {"agent:planner", "repo:bureau-pipeline", "needs-human"} <= {
            n["name"] for n in held["labels"]["nodes"]}
        assert held["comments"]["nodes"][-1]["body"] == stamp
        assert held["comments"]["nodes"][-2]["body"].startswith(PARKED)
        assert {"createdAt": entered, "toState": {"name": "Triage"}} in held["history"]["nodes"]

    def test_a_card_past_the_bound_is_one_row_and_one_alarm(self):
        doc = fixture()
        items, ctx, _gh, _linear = plan(doc)
        rows = lefts(items, "DRE-4424")
        assert len(rows) == 1
        assert rows[0].why.startswith("held plan-critic-bound for 15 hours in Triage — ")
        assert rows[0].why == ("held plan-critic-bound for 15 hours in Triage — "
                               + park_note(doc, "DRE-4424")[:160])
        assert rows[0].recommendation == WAY_BACK
        action = the_action(items, "DRE-4424")
        assert action.act == "hygiene-triage-alarm"
        assert action.cause == ALARM_CAUSE
        assert action.evidence == [CRITIC_STAMP, "entered Triage 2026-09-30T06:00:00Z"]
        assert kinds(action) == ["linear_comment"]
        assert not [w for w in action.writes if w.kind in ("linear_state", "linear_label")]
        assert comment_of(action).splitlines()[0] == (
            f"🧹 hygiene: hyg-triage-aged — {ALARM_CAUSE} · 14:05 PT")
        for write in action.writes:
            hygiene.guard(write, ctx)

    def test_a_card_inside_the_bound_is_one_row_and_no_alarm(self):
        doc = fixture()
        items, _ctx, _gh, _linear = plan(doc)
        assert actions(items, "DRE-4425") == []
        rows = lefts(items, "DRE-4425")
        assert len(rows) == 1
        assert rows[0].why.startswith("held epic-rereview-twice for 2 hours in Triage — ")
        assert rows[0].recommendation == WAY_BACK

    def test_the_row_carries_the_park_notes_first_line_trimmed_to_160_characters(self):
        doc = fixture()
        items, _ctx, _gh, _linear = plan(doc)
        note = park_note(doc, "DRE-4425")
        first = note.splitlines()[0]
        assert "\n" in note and len(first) <= 160
        assert lefts(items, "DRE-4425")[0].why.endswith(" — " + first)
        assert len(park_note(doc, "DRE-4424")) > 160
        why = lefts(items, "DRE-4424")[0].why
        assert why.endswith(" — " + park_note(doc, "DRE-4424")[:160])

    def test_the_newest_triage_entry_decides_the_age(self):
        doc = fixture()
        card(doc, "DRE-4424")["history"]["nodes"].insert(
            0, {"createdAt": "2026-09-30T20:00:00.000Z", "toState": {"name": "Triage"}})
        items, _ctx, _gh, _linear = plan(doc)
        assert lefts(items, "DRE-4424")[0].why.startswith(
            "held plan-critic-bound for 1 hour in Triage — ")
        assert actions(items, "DRE-4424") == []

    def test_a_lower_bound_alarms_the_younger_card_too(self, monkeypatch):
        monkeypatch.setenv(ALARM_VAR, "1")
        items, _ctx, _gh, _linear = plan()
        action = the_action(items, "DRE-4425")
        assert action.act == "hygiene-triage-alarm"
        assert action.cause == "held epic-rereview-twice past the 1-hour bound in Triage"
        assert action.evidence == [REREVIEW_STAMP, "entered Triage 2026-09-30T19:00:00Z"]
        assert kinds(action) == ["linear_comment"]
        assert the_action(items, "DRE-4424").cause == (
            "held plan-critic-bound past the 1-hour bound in Triage")

    @pytest.mark.parametrize("value", ["0", "abc", "-3"])
    def test_a_bound_that_is_not_a_positive_number_raises(self, monkeypatch, value):
        monkeypatch.setenv(ALARM_VAR, value)
        with pytest.raises(ValueError, match=ALARM_VAR):
            lane.alarm_hours()
        with pytest.raises(ValueError, match=ALARM_VAR):
            plan()

    def test_no_triage_entry_read_takes_the_age_off_the_stamp_comment(self):
        doc = fixture()
        held = card(doc, "DRE-4424")
        held["history"]["nodes"] = [n for n in held["history"]["nodes"]
                                    if n["toState"]["name"] != "Triage"]
        held["comments"]["nodes"][-1]["createdAt"] = "2026-09-30T10:00:00.000Z"
        items, _ctx, _gh, _linear = plan(doc)
        assert lefts(items, "DRE-4424")[0].why.startswith(
            "held plan-critic-bound for 11 hours in Triage — ")
        action = the_action(items, "DRE-4424")
        assert action.cause == ALARM_CAUSE
        assert action.evidence == [CRITIC_STAMP, "stamped 2026-09-30T10:00:00Z"]

    def test_neither_read_is_age_unknown_and_never_an_alarm(self):
        doc = fixture()
        held = card(doc, "DRE-4424")
        held["history"]["nodes"] = []
        del held["comments"]["nodes"][-1]["createdAt"]
        items, _ctx, _gh, _linear = plan(doc)
        rows = lefts(items, "DRE-4424")
        assert len(rows) == 1
        assert "age unknown" in rows[0].why and "hours" not in rows[0].why
        assert rows[0].why.startswith("held plan-critic-bound ")
        assert actions(items, "DRE-4424") == []

    def test_it_reads_before_the_retired_repo_rule(self):
        doc = fixture()
        held = card(doc, "DRE-4424")
        held["labels"]["nodes"] = [{"name": "repo:legacy-site"} if n["name"].startswith("repo:")
                                   else n for n in held["labels"]["nodes"]]
        items, _ctx, _gh, _linear = plan(doc)
        assert the_action(items, "DRE-4424").act == "hygiene-triage-alarm"
        assert lefts(items, "DRE-4424")[0].why.startswith("held plan-critic-bound for ")

    def test_a_canceled_parent_outranks_the_planning_hold(self):
        doc = fixture()
        card(doc, "DRE-4424")["parent"] = copy.deepcopy(card(doc, "DRE-4441")["parent"])
        items, _ctx, _gh, _linear = plan(doc)
        assert the_action(items, "DRE-4424").act == "hygiene-card-cancel"
        assert lefts(items, "DRE-4424") == []

    def test_the_stamp_without_the_label_is_no_planning_hold(self):
        doc = fixture()
        held = card(doc, "DRE-4424")
        held["labels"]["nodes"] = [n for n in held["labels"]["nodes"]
                                   if n["name"] != "needs-human"]
        items, _ctx, _gh, _linear = plan(doc)
        assert actions(items, "DRE-4424") == []
        assert not lefts(items, "DRE-4424")[0].why.startswith("held ")

    @pytest.mark.parametrize("newer", [
        "🔓 hold lifted: reason=plan-critic-bound because=operator by=hold.py",
        "🔒 hold: reason=dead-run-cap at=none lifts=unpark-marker by=dead_run.py",
    ])
    def test_a_spent_stamp_or_a_newer_reason_is_no_planning_hold(self, newer):
        doc = fixture()
        card(doc, "DRE-4424")["comments"]["nodes"].append(comment(newer))
        items, _ctx, _gh, _linear = plan(doc)
        assert actions(items, "DRE-4424") == []
        rows = lefts(items, "DRE-4424")
        assert len(rows) == 1 and rows[0].why == "parked with needs-human — a person owns it"


ALARM_RECEIPT = (f"🧹 hygiene: hyg-triage-aged — {ALARM_CAUSE} · 14:05 PT\n"
                 f"evidence: {CRITIC_STAMP}, entered Triage 2026-09-30T06:00:00Z")


class TestTheAlarmIsSaidOnce:
    """The suppression is the core's (tag, cause) key, driven through
    `hygiene.run_leg` — the lane carries no key of its own."""

    def test_a_card_already_alarmed_is_suppressed_by_the_core(self, monkeypatch):
        doc = fixture()
        card(doc, "DRE-4424")["comments"]["nodes"].append(
            comment(ALARM_RECEIPT, at="2026-09-30T14:05:00.000Z"))
        sent, ledger = run(doc, monkeypatch)
        mine = [a for a in ledger["actions"] if a["target"] == "DRE-4424"]
        assert [(a["act"], a["cause"], a["outcome"]) for a in mine] == [
            ("hygiene-triage-alarm", ALARM_CAUSE, "suppressed")]
        assert [w for w in sent if w.card == "DRE-4424"] == []
        assert "DRE-4424" in {row["target"] for row in ledger["left"]}

        add_receipts(doc, sent)
        sent2, ledger2 = run(doc, monkeypatch)
        assert [w for w in sent2 if w.card == "DRE-4424"] == []
        assert [a["outcome"] for a in ledger2["actions"] if a["target"] == "DRE-4424"] == [
            "suppressed"]

        items, _ctx, _gh, _linear = plan(doc)
        assert the_action(items, "DRE-4424").cause == ALARM_CAUSE

    def test_the_first_pass_posts_one_receipt_and_the_second_none(self, monkeypatch):
        doc = fixture()
        sent, ledger = run(doc, monkeypatch)
        mine = [w for w in sent if w.card == "DRE-4424"]
        assert [w.kind for w in mine] == ["linear_comment"]
        assert [a["outcome"] for a in ledger["actions"] if a["target"] == "DRE-4424"] == [
            "executed"]
        add_receipts(doc, sent)
        sent2, _ledger2 = run(doc, monkeypatch)
        assert [w for w in sent2 if w.card == "DRE-4424"] == []


# --------------------------------------------------------------------------- #
# the console learns the tag first                                             #
# --------------------------------------------------------------------------- #

CONSUMERS = ROOT / "scripts" / "check_act_consumers.py"


def console_copy(path, *, aged: bool) -> None:
    """A console `receipts.py` that carries every tag the live registry
    declares — with or without this card's `hyg-triage-aged`."""
    rows = json.loads((ROOT / "config" / "pipeline-acts.json").read_text("utf-8"))["acts"]
    known = [(r["tag"], r["kind"]) for r in rows if aged or r["tag"] != "hyg-triage-aged"]
    path.write_text("ACTS = {\n" + "".join(f"    {t!r}: {k!r},\n" for t, k in known) + "}\n",
                    encoding="utf-8")


def consumers_check(console):
    env = {k: v for k, v in os.environ.items()
           if k not in ("BUREAU_CONSOLE_TOKEN", "BUREAU_CONSOLE_ACTS_FILE")}
    env["BUREAU_CONSOLE_ACTS_FILE"] = str(console)
    return subprocess.run([sys.executable, str(CONSUMERS), "check"],
                          capture_output=True, text=True, env=env)


class TestTheConsoleLearnsTheTagFirst:
    def test_the_registry_declares_the_alarm_beside_the_hygiene_rows(self):
        rows = json.loads((ROOT / "config" / "pipeline-acts.json").read_text("utf-8"))["acts"]
        names = [r["name"] for r in rows]
        row = rows[names.index("hygiene-triage-alarm")]
        assert (row["tag"], row["kind"], row["state"], row["next_actor"]) == (
            "hyg-triage-aged", "hold", "unchanged", "operator")
        assert row["emits"] == {"file": "scripts/hygiene.py", "anchor": '"hyg-triage-aged"'}
        assert names[names.index("hygiene-triage-alarm") - 1].startswith("hygiene-")
        assert names[-1] != "hygiene-triage-alarm"
        assert hygiene.TAGS["hygiene-triage-alarm"] == "hyg-triage-aged"

    def test_a_console_that_carries_the_tag_passes(self, tmp_path):
        console = tmp_path / "receipts.py"
        console_copy(console, aged=True)
        out = consumers_check(console)
        assert out.returncode == 0, out.stdout + out.stderr

    def test_a_console_without_the_tag_fails_naming_it_and_the_console_fix(self, tmp_path):
        console = tmp_path / "receipts.py"
        console_copy(console, aged=False)
        out = consumers_check(console)
        assert out.returncode == 1, out.stdout + out.stderr
        assert "hygiene-triage-alarm" in out.stdout
        assert "add 'hyg-triage-aged' (kind 'hold') to ACTS" in out.stdout


# --------------------------------------------------------------------------- #
# what it may write, and how it reads                                          #
# --------------------------------------------------------------------------- #

ALLOWED = {"linear_state", "linear_comment", "linear_label", "linear_relation"}
WRITE_CONSTRUCTORS = ALLOWED | {"gh_dispatch", "gh_rerun", "gh_update_branch",
                                "gh_pr_close", "gh_pr_comment"}


class TestWhatItMayWrite:
    def test_the_module_calls_only_the_linear_constructors(self):
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        used = {n.attr for n in ast.walk(tree)
                if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
                and n.value.id == "hygiene" and n.attr in WRITE_CONSTRUCTORS}
        assert used == ALLOWED

    def test_it_reads_hold_only_through_read_stamp(self):
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        used = {n.attr for n in ast.walk(tree)
                if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
                and n.value.id == "hold"}
        assert used == {"read_stamp"}

    def test_the_cores_static_scan_passes_over_it(self):
        spec = importlib.util.spec_from_file_location(
            "test_hygiene_core", ROOT / "tests" / "test_hygiene.py")
        core_tests = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(core_tests)
        assert MODULE_PATH.exists()
        assert core_tests.scan(MODULE_PATH) == []


class TestAReadThatFails:
    def test_a_failed_gh_read_skips_that_card_and_no_other(self, capsys):
        doc = fixture()
        key = next(k for k in doc["gh"] if k.endswith("head:agent/DRE-4431"))
        ctx, gh, _linear = context(doc)

        def failing(argv):
            if " ".join(argv) == key:
                raise RuntimeError("gh exited 1: HTTP 502")
            return gh(argv)

        ctx.gh = failing
        items = lane.plan(hygiene.Board(lanes=doc["lanes"], prs=doc["prs"]), ctx)
        assert actions(items, "DRE-4431") == [] and lefts(items, "DRE-4431") == []
        assert {a.target for a in actions(items)} == set(EXPECTED) - {"DRE-4431"}
        assert "DRE-4431 skipped" in capsys.readouterr().err

    def test_a_read_the_core_refuses_is_never_swallowed(self):
        doc = fixture()
        ctx, _gh, _linear = context(doc)

        def refusing(argv):
            raise hygiene.Forbidden("ctx.gh is read-only")

        ctx.gh = refusing
        with pytest.raises(hygiene.Forbidden):
            lane.plan(hygiene.Board(lanes=doc["lanes"], prs=doc["prs"]), ctx)
