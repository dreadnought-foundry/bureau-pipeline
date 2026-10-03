"""RED-first: the hygiene agent's Green Light lane (DRE-5372).

`scripts/hygiene_green_light.py` reads the CEO's decision queue once and sorts
each row by the newest pipeline receipt on it. Five kinds are mechanical, and
each goes back to `Planning` with its cause named:

  1. a dead planner — a `🪦 limit-death:` marker with `stage=plan`, and no
     `plan-critic: … result=PASS` record newer than it;
  2. a classifier transport failure — `🔌 planning-classify-transport`;
  3. a passed plan with a child that has no acceptance criteria — the
     children read once per such epic through `ctx.linear`;
  4. a `🙋 planning-escalation` asking for a split or for access an agent
     lacks — the planner's to answer, not the CEO's;
  5. a stall park — the 120-minute "planning has produced nothing" park, or
     the planner-line watchdog's.

Everything else in the lane is a real question: nothing is written and one
`Left` row carries the escalation's own recommendation. A second stall park
inside six hours of this agent's last stall re-send is a `Left` row too.

The fixture is `tests/fixtures/hygiene-green-light-2026-09-30.json`, in the
shape every hygiene lane fixture takes — `lanes`, `prs` and a `gh` map — plus
a `linear` map from an epic's identifier to what its children read answers.
Every receipt in it was composed by the module that writes it in the
pipeline, and every comment in it was written by the pipeline's own Linear
user, `PIPELINE_USER` — the one author whose receipts this lane reads.

Run: cd bureau-pipeline && python3 -m pytest tests/test_hygiene_green_light.py -v
"""
from __future__ import annotations

import ast
import copy
import importlib.util
import json
import os
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import dead_run  # noqa: E402
import hygiene  # noqa: E402
import plan_critic  # noqa: E402
import planning_escalation  # noqa: E402
import reconcile  # noqa: E402

MODULE_PATH = ROOT / "scripts" / "hygiene_green_light.py"
FIXTURE = ROOT / "tests" / "fixtures" / "hygiene-green-light-2026-09-30.json"

#: 21:05 UTC on 2026-09-30 is 14:05 in Pacific Daylight Time.
NOW = datetime(2026, 9, 30, 21, 5, tzinfo=UTC)
HOME = "dreadnought-foundry"
LANE = "Green Light"
#: Who the fleet key is — what `viewer { id }` answers, and the author of
#: every comment in the fixture.
PIPELINE_USER = "user-pipeline"
STRANGER = "user-stranger"


def load_lane():
    if not MODULE_PATH.exists():
        return None
    spec = importlib.util.spec_from_file_location("hygiene_green_light", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lane = load_lane()


def fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class FixtureGh:
    """The read-only `gh`: this lane reads none, and `gh pr list` is the
    core's own read, answered from the fixture's `prs`."""

    def __init__(self, doc: dict):
        self.answers = dict(doc["gh"])
        self.prs = doc["prs"]
        self.calls: list = []

    def __call__(self, argv):
        argv = [str(a) for a in argv]
        assert hygiene.read_gh_refusal(argv) is None
        if argv[:3] == ["gh", "pr", "list"]:
            return json.dumps(self.prs.get(argv[argv.index("--repo") + 1], []))
        key = " ".join(argv)
        self.calls.append(key)
        if key not in self.answers:
            raise AssertionError(f"the fixture's gh map has no answer for {key!r}")
        return self.answers[key]


def fixture_gql(doc: dict):
    """`linear_ops.gql`, answered from the fixture's `linear` map by the
    issue the query names — and `viewer { id }` with the pipeline's user."""

    def gql(query, variables=None):
        if "viewer" in query:
            return {"viewer": {"id": PIPELINE_USER}}
        ident = (variables or {}).get("id")
        if ident not in doc["linear"]:
            raise AssertionError(f"the fixture's linear map has no answer for {ident!r}")
        return copy.deepcopy(doc["linear"][ident])

    return gql


def context(doc, *, now=NOW, dry_run=False):
    gh = FixtureGh(doc)
    linear = hygiene.read_only_linear(fixture_gql(doc))
    ctx = hygiene.make_context(HOME, gh=gh, linear=linear, dry_run=dry_run,
                               now=now, summary_card="DRE-900")
    return ctx, gh


def plan(doc=None, *, now=NOW):
    doc = doc if doc is not None else fixture()
    ctx, _gh = context(doc, now=now)
    board = hygiene.Board(lanes=doc["lanes"], prs=doc["prs"])
    assert lane is not None, f"{MODULE_PATH.name} does not exist yet"
    return lane.plan(board, ctx), ctx


def actions(items, target=None):
    return [i for i in items if isinstance(i, hygiene.Action)
            and (target is None or i.target == target)]


def lefts(items, target=None):
    return [i for i in items if isinstance(i, hygiene.Left)
            and (target is None or i.target == target)]


def card(doc, ident):
    return next(c for c in doc["lanes"][LANE] if c["identifier"] == ident)


def push_comment(doc, ident, body, when: datetime, *, author=PIPELINE_USER):
    """A newer comment on the card: Linear answers the window newest first.
    `author=None` is an integration's comment, which Linear gives no user."""
    card(doc, ident)["comments"]["nodes"].insert(0, {
        "body": body, "createdAt": when.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "user": {"id": author} if author is not None else None})


def children_reads(ctx):
    return [q for q in ctx.linear.calls if "children" in q]


def first_line(action):
    return next(w for w in action.writes if w.kind == "linear_comment").body.splitlines()[0]


def resent(items, ident):
    found = actions(items, ident)
    assert len(found) == 1, f"{ident}: {found}"
    action = found[0]
    assert action.act == "hygiene-resend-to-planning"
    assert [w.kind for w in action.writes] == ["linear_comment", "linear_state"]
    assert action.writes[1].lane == "Planning" and action.writes[1].park is False
    assert action.writes[1].card == ident and action.writes[0].card == ident
    assert first_line(action).startswith(f"🧹 hygiene: hyg-resent-to-planning — {action.cause} · ")
    assert not lefts(items, ident)
    return action


EXPECTED_CAUSES = {
    "DRE-9101": "planner died, limit-death kind=claude stage=plan at 2026-09-30 05:41 PT",
    "DRE-9102": "planner died, limit-death kind=linear stage=plan at 2026-09-30 05:44 PT",
    "DRE-9103": "classifier transport failure at 2026-09-30 06:21 PT",
    "DRE-9104": "child DRE-9142 has no acceptance criteria",
    "DRE-9105": "escalation asks for split, posted 2026-09-30 07:31 PT",
    "DRE-9106": "stall park at 2026-09-30 09:12 PT",
    "DRE-9107": "stall park at 2026-09-30 10:26 PT",
}
EXPECTED_LEFT = ("DRE-9108", "DRE-9109")


# --------------------------------------------------------------------------- #
# the lane's shape                                                             #
# --------------------------------------------------------------------------- #


class TestTheLane:
    def test_its_heading_is_the_lane(self):
        assert lane is not None, f"{MODULE_PATH.name} does not exist yet"
        assert lane.LANE == "Green Light"

    def test_the_core_discovers_it_by_the_glob(self):
        assert "hygiene_green_light" in [m.__name__ for m in hygiene.discover()]

    def test_the_fixture_sorts_into_seven_resends_and_two_rows_left(self):
        items, _ctx = plan()
        assert {a.target: a.cause for a in actions(items)} == EXPECTED_CAUSES
        assert len(actions(items)) == len(EXPECTED_CAUSES)
        assert sorted(r.target for r in lefts(items)) == list(EXPECTED_LEFT)
        assert {i.lane for i in items} == {"Green Light"}

    def test_a_card_outside_this_legs_scope_is_never_read(self):
        doc = fixture()
        foreign = copy.deepcopy(card(doc, "DRE-9101"))
        foreign["identifier"] = "DRE-9199"
        foreign["labels"]["nodes"][0]["name"] = "repo:atlas"
        doc["lanes"][LANE].append(foreign)
        items, _ctx = plan(doc)
        assert not [i for i in items if i.target == "DRE-9199"]

    def test_the_standing_summary_card_is_never_touched(self):
        doc = fixture()
        summary = copy.deepcopy(card(doc, "DRE-9101"))
        summary["identifier"] = "DRE-900"
        doc["lanes"][LANE].append(summary)
        items, _ctx = plan(doc)
        assert not [i for i in items if i.target == "DRE-900"]


# --------------------------------------------------------------------------- #
# (1) a dead planner                                                           #
# --------------------------------------------------------------------------- #


class TestADeadPlanner:
    def test_a_limit_death_in_the_plan_stage_is_resent_with_two_writes(self):
        items, _ctx = plan()
        action = resent(items, "DRE-9101")
        assert len(action.writes) == 2
        assert "limit-death" in action.cause and "05:41 PT" in action.cause
        assert first_line(action).startswith(
            "🧹 hygiene: hyg-resent-to-planning — planner died, limit-death")

    def test_a_pass_older_than_the_death_does_not_save_it(self):
        items, _ctx = plan()
        resent(items, "DRE-9102")

    def test_the_same_card_with_a_newer_pass_yields_nothing(self):
        doc = fixture()
        push_comment(doc, "DRE-9101", plan_critic.marker("pre", 1, "PASS"),
                     datetime(2026, 9, 30, 13, 30, tzinfo=UTC))
        items, _ctx = plan(doc)
        assert not [i for i in items if i.target == "DRE-9101"]

    def test_a_limit_death_in_another_stage_is_not_a_dead_planner(self):
        doc = fixture()
        push_comment(doc, "DRE-9101",
                     dead_run.limit_marker("claude", "build", None, "37110000009"),
                     datetime(2026, 9, 30, 13, 30, tzinfo=UTC))
        items, _ctx = plan(doc)
        assert not actions(items, "DRE-9101")

    def test_the_evidence_names_the_dead_run(self):
        items, _ctx = plan()
        assert "run 37110000001" in actions(items, "DRE-9101")[0].evidence


# --------------------------------------------------------------------------- #
# (2) a classifier transport failure                                           #
# --------------------------------------------------------------------------- #


class TestATransportFailure:
    def test_the_card_is_resent_with_the_transport_failure_named(self):
        items, _ctx = plan()
        action = resent(items, "DRE-9103")
        assert action.cause == "classifier transport failure at 2026-09-30 06:21 PT"
        assert "classifier transport failure" in first_line(action)

    def test_the_tag_is_the_one_planning_escalation_writes(self):
        assert planning_escalation.TRANSPORT_TAG == "planning-classify-transport"
        body = card(fixture(), "DRE-9103")["comments"]["nodes"][0]["body"]
        assert body.startswith("🔌 planning-classify-transport:")


# --------------------------------------------------------------------------- #
# (3) a passed plan with a child missing criteria                              #
# --------------------------------------------------------------------------- #


class TestAChildWithoutCriteria:
    def test_the_epic_is_resent_naming_the_child(self):
        items, _ctx = plan()
        action = resent(items, "DRE-9104")
        assert action.cause == "child DRE-9142 has no acceptance criteria"
        assert "DRE-9142" in first_line(action)

    def test_the_children_are_read_exactly_once_in_the_pass(self):
        items, ctx = plan()
        assert actions(items, "DRE-9104")
        assert len(children_reads(ctx)) == 1

    def test_the_answer_is_plan_critics_own(self):
        doc = fixture()
        children = doc["linear"]["DRE-9104"]["issue"]["children"]["nodes"]
        named = plan_critic.cards_without_acceptance(
            [{"identifier": c["identifier"], "body": c["description"]} for c in children])
        assert named == ["DRE-9142"]

    def test_every_child_with_criteria_leaves_a_passed_plan_alone(self):
        doc = fixture()
        for child in doc["linear"]["DRE-9104"]["issue"]["children"]["nodes"]:
            child["description"] += "\n## Acceptance criteria\n\n- [ ] done\n"
        items, ctx = plan(doc)
        assert not [i for i in items if i.target == "DRE-9104"]
        assert len(children_reads(ctx)) == 1

    def test_a_card_whose_newest_receipt_is_not_a_pass_reads_no_children(self):
        doc = fixture()
        doc["lanes"][LANE] = [c for c in doc["lanes"][LANE] if c["identifier"] != "DRE-9104"]
        _items, ctx = plan(doc)
        assert children_reads(ctx) == []

    def test_a_children_read_that_fails_skips_that_epic_and_no_other(self, capsys):
        doc = fixture()
        del doc["linear"]["DRE-9104"]
        ctx, _gh = context(doc)

        def failing(query, variables=None):
            if "viewer" in query:
                return {"viewer": {"id": PIPELINE_USER}}
            raise RuntimeError("Linear answered 502")

        ctx.linear = failing
        items = lane.plan(hygiene.Board(lanes=doc["lanes"], prs=doc["prs"]), ctx)
        assert not [i for i in items if i.target == "DRE-9104"]
        assert {a.target for a in actions(items)} == set(EXPECTED_CAUSES) - {"DRE-9104"}
        assert "DRE-9104 skipped" in capsys.readouterr().err

    def test_a_read_the_core_refuses_is_never_swallowed(self):
        doc = fixture()
        ctx, _gh = context(doc)

        def refusing(query, variables=None):
            raise hygiene.Forbidden("ctx.linear is read-only")

        ctx.linear = refusing
        with pytest.raises(hygiene.Forbidden):
            lane.plan(hygiene.Board(lanes=doc["lanes"], prs=doc["prs"]), ctx)


# --------------------------------------------------------------------------- #
# (4) a split or access escalation, and a real question                       #
# --------------------------------------------------------------------------- #

ACCESS = (
    "The work needs a credential for the payments provider's live account, and no "
    "agent holds one — an operator has to run this part by hand.\n\nRecommendation: "
    "route it to the operator as a hand-built card."
)


class TestAnEscalation:
    def test_a_split_escalation_is_resent_with_the_reason_quoted(self):
        items, _ctx = plan()
        action = resent(items, "DRE-9105")
        assert action.cause == "escalation asks for split, posted 2026-09-30 07:31 PT"
        comment = action.writes[0].body
        assert "too big for one pull request and must be split" in comment
        assert any(e.startswith("reason ") for e in action.evidence)

    def test_an_access_escalation_is_resent(self):
        doc = fixture()
        push_comment(doc, "DRE-9109", planning_escalation.escalation_comment("DRE-9109", ACCESS),
                     datetime(2026, 9, 30, 11, 15, tzinfo=UTC))
        items, _ctx = plan(doc)
        action = resent(items, "DRE-9109")
        assert action.cause == "escalation asks for access, posted 2026-09-30 04:15 PT"
        assert "no agent holds one" in action.writes[0].body

    def test_a_business_question_is_left_with_its_own_recommendation(self):
        items, _ctx = plan()
        assert not actions(items, "DRE-9109")
        rows = lefts(items, "DRE-9109")
        assert len(rows) == 1
        assert rows[0].recommendation == (
            "keep the old page live for one week, because a broken partner link "
            "costs more than a week of overlap.")
        assert "partner sign-up page" in rows[0].why or "question" in rows[0].why

    @pytest.mark.parametrize("line", [
        "**Recommendation:** close it on Friday.",
        "**Recommendation**: close it on Friday.",
        "Recommended — close it on Friday.",
        "We can close it on Friday or Monday. I recommend close it on Friday.",
    ])
    def test_the_recommendation_reads_without_its_label_or_markup(self, line):
        doc = fixture()
        push_comment(doc, "DRE-9109", planning_escalation.escalation_comment(
            "DRE-9109", f"Should the partner page close on Friday or on Monday?\n\n{line}"),
            datetime(2026, 9, 30, 11, 15, tzinfo=UTC))
        items, _ctx = plan(doc)
        recommendation = lefts(items, "DRE-9109")[0].recommendation
        assert recommendation.endswith("close it on Friday.")
        assert "*" not in recommendation and not recommendation.lower().startswith("recommend")

    def test_a_question_that_states_no_recommendation_says_so(self):
        doc = fixture()
        push_comment(doc, "DRE-9109", planning_escalation.escalation_comment(
            "DRE-9109", "Should the partner page close on Friday or on Monday?"),
            datetime(2026, 9, 30, 11, 15, tzinfo=UTC))
        items, _ctx = plan(doc)
        rows = lefts(items, "DRE-9109")
        assert len(rows) == 1 and "no recommendation" in rows[0].recommendation

    @pytest.mark.parametrize("reason", [
        "This card is too big for one pull request.\n\nRecommendation: split it.",
        "The work must be split: the reader and the report will not fit in one pull "
        "request.\n\nRecommendation: two cards.",
        "Split the card into two pull requests, the reader first.\n\nRecommendation: "
        "two cards.",
        "The reader and the report are more than one pull request of work.\n\n"
        "Recommendation: two cards.",
    ])
    def test_a_split_said_another_way_is_still_a_split(self, reason):
        assert lane.asks_for(reason) == "split"

    @pytest.mark.parametrize("reason", [
        "The work needs a credential for the payments provider's live account, and "
        "the fleet does not have it.\n\nRecommendation: an operator card.",
        "This card requires write access to the billing bucket. The pipeline has no "
        "access to that bucket.\n\nRecommendation: an operator card.",
        "The work needs the partner's API token, and only the operator holds that."
        "\n\nRecommendation: an operator card.",
    ])
    def test_an_access_said_another_way_is_still_access(self, reason):
        assert lane.asks_for(reason) == "access"

    @pytest.mark.parametrize("reason", [
        "Should we cap partner uploads at 5 MB? Some videos are too large for mobile."
        "\n\nRecommendation: cap at 5 MB.",
        "Is a launch to all 40 customers at once too broad a pilot, or should we start "
        "with five?\n\nRecommendation: five.",
        "Should the fleet use the new billing token for refunds? An agent lacks the "
        "finance context.\n\nRecommendation: yes.",
        "Should we split this work into two phases, pilot partners first and everyone "
        "else after a month, or ship it all at once?\n\nRecommendation: two phases.",
        "This epic should be split by region or by product line? Both are valid."
        "\n\nRecommendation: by region.",
        "Should the card be split into a partner release and a public one, or go out "
        "as one launch?\n\nRecommendation: one launch.",
        "Does the work need partner access? No agent has met the partner yet."
        "\n\nRecommendation: ask the partner first.",
    ])
    def test_a_business_question_in_the_same_words_is_still_a_question(self, reason):
        assert lane.asks_for(reason) is None
        doc = fixture()
        push_comment(doc, "DRE-9109", planning_escalation.escalation_comment("DRE-9109", reason),
                     datetime(2026, 9, 30, 11, 15, tzinfo=UTC))
        items, _ctx = plan(doc)
        assert not actions(items, "DRE-9109")
        rows = lefts(items, "DRE-9109")
        assert len(rows) == 1
        assert rows[0].recommendation == reason.rsplit("Recommendation: ", 1)[1]

    def test_a_question_that_mentions_access_for_customers_is_still_a_question(self):
        doc = fixture()
        push_comment(doc, "DRE-9109", planning_escalation.escalation_comment(
            "DRE-9109", "Should partners get access to the new portal on day one, or "
                        "after a week of moderators using it?\n\nRecommendation: after "
                        "a week."),
            datetime(2026, 9, 30, 11, 15, tzinfo=UTC))
        items, _ctx = plan(doc)
        assert not actions(items, "DRE-9109")
        assert lefts(items, "DRE-9109")[0].recommendation == "after a week."


# --------------------------------------------------------------------------- #
# (5) a stall park, and the lane's own stop                                    #
# --------------------------------------------------------------------------- #


class TestAStallPark:
    @pytest.mark.parametrize("ident, cause", [
        ("DRE-9106", "stall park at 2026-09-30 09:12 PT"),
        ("DRE-9107", "stall park at 2026-09-30 10:26 PT"),
    ])
    def test_each_stall_park_is_resent_with_the_parks_own_time(self, ident, cause):
        items, _ctx = plan()
        action = resent(items, ident)
        assert action.cause == cause

    def test_the_openings_it_reads_are_the_watchdogs_own(self):
        assert reconcile.stalled_planning_reason().startswith(lane.STALL_OPENINGS[0])
        assert reconcile.waiting_too_long_reason(180).startswith(lane.STALL_OPENINGS[1])

    def test_a_second_park_inside_six_hours_is_left_naming_both(self):
        items, _ctx = plan()
        assert not actions(items, "DRE-9108")
        rows = lefts(items, "DRE-9108")
        assert len(rows) == 1
        assert "stall park at 2026-09-30 08:10 PT" in rows[0].why
        assert "stall park at 2026-09-30 12:40 PT" in rows[0].why

    def test_the_same_second_park_after_six_hours_is_resent(self):
        items, _ctx = plan(now=NOW + timedelta(hours=1, minutes=5))
        action = resent(items, "DRE-9108")
        assert action.cause == "stall park at 2026-09-30 12:40 PT"

    def test_the_stop_counts_only_a_stall_resend(self):
        doc = fixture()
        nodes = card(doc, "DRE-9108")["comments"]["nodes"]
        for node in nodes:
            if node["body"].startswith("🧹 hygiene:"):
                node["body"] = hygiene.receipt(
                    "hygiene-resend-to-planning", "classifier transport failure at "
                    "2026-09-30 08:10 PT", ["transport comment 2026-09-30T15:10:00Z"],
                    datetime(2026, 9, 30, 16, 5, tzinfo=UTC))
        items, _ctx = plan(doc)
        resent(items, "DRE-9108")


# --------------------------------------------------------------------------- #
# whose receipts it reads                                                      #
# --------------------------------------------------------------------------- #

LATER = datetime(2026, 9, 30, 13, 30, tzinfo=UTC)


class TestWhoseReceiptsItReads:
    """Anyone with comment access can post a receipt's line. Only the
    pipeline's own user writes a receipt this lane acts on (DRE-2721)."""

    @pytest.mark.parametrize("body", [
        dead_run.limit_marker("claude", "plan", None, "37110000666"),
        "🔌 planning-classify-transport: DRE-9109 was not classified this run.",
        planning_escalation.escalation_comment(
            "DRE-9109", "This card is too big for one pull request and must be split."
                        "\n\nRecommendation: split it."),
        planning_escalation.escalation_comment("DRE-9109", reconcile.stalled_planning_reason()),
    ])
    @pytest.mark.parametrize("author", [STRANGER, None])
    def test_a_strangers_receipt_never_moves_a_real_question(self, body, author):
        doc = fixture()
        push_comment(doc, "DRE-9109", body, LATER, author=author)
        items, _ctx = plan(doc)
        assert not actions(items, "DRE-9109")
        rows = lefts(items, "DRE-9109")
        assert len(rows) == 1
        assert rows[0].recommendation.startswith("keep the old page live for one week")

    def test_a_strangers_pass_never_saves_a_dead_planner(self):
        doc = fixture()
        push_comment(doc, "DRE-9101", plan_critic.marker("pre", 1, "PASS"), LATER,
                     author=STRANGER)
        items, _ctx = plan(doc)
        assert resent(items, "DRE-9101").cause == EXPECTED_CAUSES["DRE-9101"]

    def test_a_strangers_stall_resend_never_holds_a_stall_park(self):
        doc = fixture()
        push_comment(doc, "DRE-9106", hygiene.receipt(
            "hygiene-resend-to-planning", "stall park at 2026-09-30 05:00 PT",
            ["stall park comment 2026-09-30T12:00:00Z"], NOW), NOW - timedelta(hours=1),
            author=STRANGER)
        items, _ctx = plan(doc)
        assert resent(items, "DRE-9106").cause == EXPECTED_CAUSES["DRE-9106"]

    def test_the_pipelines_user_is_read_once_in_the_pass(self):
        _items, ctx = plan()
        assert len([q for q in ctx.linear.calls if "viewer" in q]) == 1

    def test_a_lane_with_no_card_reads_nothing(self):
        doc = fixture()
        doc["lanes"][LANE] = []
        items, ctx = plan(doc)
        assert items == [] and ctx.linear.calls == []

    @pytest.mark.parametrize("answer", [RuntimeError("Linear answered 502"),
                                        {"viewer": None}])
    def test_no_pipeline_user_means_no_action_this_pass(self, answer, capsys):
        doc = fixture()
        ctx, _gh = context(doc)
        children = fixture_gql(doc)

        def gql(query, variables=None):
            if "viewer" not in query:
                return children(query, variables)
            if isinstance(answer, Exception):
                raise answer
            return answer

        ctx.linear = hygiene.read_only_linear(gql)
        items = lane.plan(hygiene.Board(lanes=doc["lanes"], prs=doc["prs"]), ctx)
        assert items == []
        assert "skipped this pass" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# causes, writes and the guard, over the whole fixture                         #
# --------------------------------------------------------------------------- #

_STAMP = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2} PT")
_COUNT = re.compile(r"\b\d+\s*(?:\+\s*)?(?:time|times|attempt|attempts|round|rounds|minute|"
                    r"minutes|hour|hours|card|cards|child|children)\b", re.I)


class TestCauses:
    def test_no_cause_carries_the_pass_time_or_a_count(self):
        for now in (NOW, NOW + timedelta(hours=3)):
            items, _ctx = plan(now=now)
            for action in actions(items):
                assert hygiene.pt(now) not in action.cause, action.cause
                assert dead_run.pacific(now) not in action.cause, action.cause
                assert not _COUNT.search(action.cause), action.cause

    def test_every_time_in_a_cause_is_a_receipts_own(self):
        doc = fixture()
        stamps = {dead_run.pacific(datetime.fromisoformat(n["createdAt"].replace("Z", "+00:00")))
                  for c in doc["lanes"][LANE] for n in c["comments"]["nodes"]}
        items, _ctx = plan(doc)
        seen = 0
        for action in actions(items):
            for stamp in _STAMP.findall(action.cause):
                assert stamp in stamps, action.cause
                seen += 1
        assert seen == 6

    def test_a_later_pass_reads_the_same_causes(self):
        first, _ctx = plan(now=NOW)
        later, _ctx = plan(now=NOW + timedelta(minutes=55))
        assert ({a.target: a.cause for a in actions(first)}
                == {a.target: a.cause for a in actions(later)})


ALLOWED = {"linear_comment", "linear_state"}
WRITE_CONSTRUCTORS = {"linear_state", "linear_comment", "linear_label", "linear_relation",
                      "gh_dispatch", "gh_rerun", "gh_update_branch", "gh_pr_close",
                      "gh_pr_comment"}


class TestWhatItMayWrite:
    def test_no_action_writes_in_progress_done_or_canceled(self):
        items, _ctx = plan()
        states = [w.lane for a in actions(items) for w in a.writes if w.kind == "linear_state"]
        assert states and set(states) == {"Planning"}
        assert not {"In Progress", "Done", "Canceled"} & set(states)

    def test_only_a_comment_and_a_state_write_are_returned(self):
        items, _ctx = plan()
        assert {w.kind for a in actions(items) for w in a.writes} == ALLOWED

    def test_the_module_calls_no_other_constructor(self):
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        used = {n.attr for n in ast.walk(tree)
                if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
                and n.value.id == "hygiene" and n.attr in WRITE_CONSTRUCTORS}
        assert used == ALLOWED

    def test_the_module_never_names_in_progress(self):
        assert "In Progress" not in MODULE_PATH.read_text(encoding="utf-8")

    def test_the_cores_guard_admits_every_write(self):
        items, ctx = plan()
        for action in actions(items):
            for write in action.writes:
                hygiene.guard(write, ctx)

    def test_the_cores_guard_refuses_a_forged_in_progress_write(self):
        doc = fixture()
        ctx, _gh = context(doc)
        with pytest.raises(hygiene.Forbidden):
            hygiene.guard(hygiene.linear_state(card(doc, "DRE-9109"), "In Progress"), ctx)

    def test_a_forged_in_progress_action_stops_the_whole_leg(self, monkeypatch):
        doc = fixture()

        class Forger:
            __name__ = "forger"

            @staticmethod
            def plan(board, ctx):
                items = lane.plan(board, ctx)
                target = card(doc, "DRE-9109")
                return items + [hygiene.Action(
                    lane=LANE, target="DRE-9109", act="hygiene-resend-to-planning",
                    cause="forged", evidence=["forged"],
                    writes=[hygiene.linear_state(target, "In Progress")])]

        sent: list = []
        monkeypatch.setattr(hygiene, "send", lambda write, ctx: sent.append(write))
        monkeypatch.setattr(hygiene, "discover", lambda lane_dir=None: [Forger])
        ctx, _gh = context(doc)
        with pytest.raises(hygiene.Forbidden):
            hygiene.run_leg({"lanes": doc["lanes"]}, ctx)
        assert sent == []

    def test_the_cores_static_scan_passes_over_it(self):
        spec = importlib.util.spec_from_file_location(
            "test_hygiene_core", ROOT / "tests" / "test_hygiene.py")
        core_tests = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(core_tests)
        assert MODULE_PATH.exists()
        assert core_tests.scan(MODULE_PATH) == []


# --------------------------------------------------------------------------- #
# idempotency, through the core's key                                          #
# --------------------------------------------------------------------------- #


def run(doc, monkeypatch, *, now=NOW):
    """One leg through the core, this lane alone, the write seam recorded."""
    sent: list = []
    monkeypatch.setattr(hygiene, "send", lambda write, ctx: sent.append(write))
    monkeypatch.setattr(hygiene, "discover", lambda lane_dir=None: [lane])
    ctx, _gh = context(doc, now=now)
    return sent, hygiene.run_leg({"taken_at": "2026-09-30T21:00:00Z",
                                  "lanes": doc["lanes"]}, ctx)


def add_receipts(doc, sent, when: datetime):
    """Every receipt the pass posted, now on its card — and every card still
    in Green Light, as if each state write had not landed."""
    for write in sent:
        if write.kind == "linear_comment":
            push_comment(doc, write.card, write.body, when)


class TestOneResendPerCause:
    def test_a_second_pass_over_a_card_still_in_green_light_sends_nothing(self, monkeypatch):
        doc = fixture()
        sent, ledger = run(doc, monkeypatch)
        executed = {a["target"] for a in ledger["actions"] if a["outcome"] == "executed"}
        assert executed == set(EXPECTED_CAUSES)
        assert len(sent) == 2 * len(EXPECTED_CAUSES)
        add_receipts(doc, sent, NOW)
        # Half an hour on: DRE-9108's earlier re-send is still inside the stop.
        sent2, ledger2 = run(doc, monkeypatch, now=NOW + timedelta(minutes=30))
        assert sent2 == []
        assert {a["outcome"] for a in ledger2["actions"]} == {"suppressed"}
        assert {a["target"] for a in ledger2["actions"]} == set(EXPECTED_CAUSES)
        assert sorted(r["target"] for r in ledger2["left"]) == list(EXPECTED_LEFT)

    def test_a_new_death_after_the_resend_is_a_new_cause(self, monkeypatch):
        doc = fixture()
        sent, _ledger = run(doc, monkeypatch)
        add_receipts(doc, sent, NOW)
        push_comment(doc, "DRE-9101",
                     dead_run.limit_marker("claude", "plan", None, "37110000077"),
                     NOW + timedelta(minutes=10))
        sent2, ledger2 = run(doc, monkeypatch, now=NOW + timedelta(minutes=30))
        assert {a["target"] for a in ledger2["actions"] if a["outcome"] == "executed"} == {
            "DRE-9101"}
        assert [w.kind for w in sent2] == ["linear_comment", "linear_state"]

    def test_dry_run_sends_nothing(self, monkeypatch, capsys):
        doc = fixture()
        sent: list = []
        monkeypatch.setattr(hygiene, "send", lambda write, ctx: sent.append(write))
        monkeypatch.setattr(hygiene, "discover", lambda lane_dir=None: [lane])
        ctx, _gh = context(doc, dry_run=True)
        ledger = hygiene.run_leg({"lanes": doc["lanes"]}, ctx)
        assert sent == []
        assert {a["outcome"] for a in ledger["actions"]} == {"would"}
        assert "would: state DRE-9101 → Planning" in capsys.readouterr().out
