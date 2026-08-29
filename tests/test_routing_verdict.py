"""RED-first tests: the verdict is a ROUTING decision, not a quality score
(DRE-2724).

A card leaving the planning segment answers one question — *who builds this,
and how* — and every answer sends it somewhere different. Framed as a score a
critic drifts toward marking things good so it looks useful; framed as routing
there is no good or bad, only a wrong destination, which shows up immediately.

WHAT THIS PINS, one section per acceptance criterion:

  1. Every card leaving Planning carries EXACTLY ONE verdict, machine-readable.
     A marker comment is the record; two different verdicts on one card is a
     conflict the reader raises rather than resolves, and the one write path
     refuses to add a second.
  2. A card whose acceptance names an INTERACTIVE FLOW routes WORKBENCH.
  3. A card whose acceptance names only STATIC VISUAL FIDELITY routes FLEET —
     `qa-review.yml` screenshots the changed screens and hands the critic both
     images (`visual_qa_context.py`), so a static comparison is FLEET-checkable.
     Screenshotting a screen is not driving a flow, and the split is the one
     this card had wrong first time round.
  4. A card labelled `agent:ops` is routed WITHOUT A MODEL BEING ASKED.
  5. Each title convention ships a MUTATION TEST whose fixture is an
     adversarial title — one that mentions the token without declaring it.
     `_BLOCKER_LINE` was a bare substring match over prose and it deadlocked a
     live epic for five days by matching "neither depends on the other"
     (DRE-2670, anchored 2026-08-23). We do not build a second one: the config
     refuses an unanchored pattern, and refuses a convention with no
     adversarial fixture.
  6. A PARKED card is NEVER REPORTED AS STALLED by any sweep.
  7. An EPIC is never given a buildability verdict — it gets a plan test.

Plus the two verdicts that had nowhere to go. OPERATOR and WORKBENCH used to
"enter Backlog and await their turn"; per DRE-2735 the turn never comes, so
each declares a destination lane and an actor, both drawn from the lane
contract rather than invented here.

Run: cd bureau-pipeline && python3 -m pytest tests/test_routing_verdict.py -v
"""
from __future__ import annotations

import inspect
import json
import os
import re
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import lane_contract  # noqa: E402
import reconcile  # noqa: E402
import routing_verdict  # noqa: E402

CONFIG = ROOT / "config" / "routing-verdicts.json"

# The five routes, written out ONCE here as the thing the module is compared
# against. Every other list in the pipeline is derived from the config file.
THE_FIVE = ("FLEET", "WORKBENCH", "OPERATOR", "PARKED", "NEEDS WORK")


# ===========================================================================
# The vocabulary: five routes, each with a destination and an actor
# ===========================================================================
class TestTheFiveRoutes:
    def test_the_config_is_json_the_stdlib_can_read(self):
        # JSON, not YAML, for the same reason the lane contract is: this module
        # is imported on the product-repo agent job, where there is no pip
        # install and PyYAML is not guaranteed.
        with open(CONFIG, encoding="utf-8") as fh:
            doc = json.load(fh)
        assert doc["verdicts"], "the vocabulary declares no verdicts"

    def test_exactly_the_five_routes_exist(self):
        assert routing_verdict.verdicts() == THE_FIVE

    def test_every_verdict_states_a_destination_and_an_actor(self):
        """The amendment's demand: OPERATOR and WORKBENCH both used to route to
        Backlog to await "its turn", and per DRE-2735 the turn never comes.
        Each needs a stated destination and a stated actor, or it is the
        one-off dead end wearing a different label."""
        for name in routing_verdict.verdicts():
            assert routing_verdict.destination(name), f"{name} goes nowhere"
            assert routing_verdict.actor(name), f"{name} names nobody to act"

    def test_every_destination_is_a_lane_the_contract_carries(self):
        live = set(lane_contract.lane_names(status="live"))
        for name in routing_verdict.verdicts():
            assert routing_verdict.destination(name) in live, (
                f"{name} routes to {routing_verdict.destination(name)!r}, which "
                "is not a lane in config/lane-contract.json"
            )

    def test_every_actor_is_a_writer_the_contract_permits(self):
        known = set(lane_contract.writers())
        for name in routing_verdict.verdicts():
            assert routing_verdict.actor(name) in known, (
                f"{name}'s actor {routing_verdict.actor(name)!r} is not in the "
                "lane contract's writer glossary"
            )

    def test_fleet_is_the_only_promotable_verdict(self):
        promotable = [v for v in routing_verdict.verdicts()
                      if routing_verdict.is_promotable(v)]
        assert promotable == ["FLEET"]

    def test_the_human_routes_carry_the_mark_that_stops_the_sweep(self):
        """WORKBENCH and OPERATOR land in a work lane owned by a person. The
        `hand-built` label is what already tells the sweep no dispatched run is
        coming (DRE-2524) — reuse it rather than invent a second signal."""
        for name in ("WORKBENCH", "OPERATOR"):
            assert reconcile.HAND_BUILT_LABEL in routing_verdict.marks(name)
        assert routing_verdict.marks("FLEET") == ()

    def test_an_operator_card_is_also_marked_no_code(self):
        assert "no-code" in routing_verdict.marks("OPERATOR")

    def test_an_unknown_verdict_is_raised_not_defaulted(self):
        with pytest.raises(routing_verdict.UnknownVerdict):
            routing_verdict.destination("PROBABLY FINE")


# ===========================================================================
# 1: exactly one verdict, machine-readable
# ===========================================================================
class TestExactlyOneVerdict:
    def test_a_verdict_comment_is_machine_readable(self):
        body = routing_verdict.verdict_comment("FLEET", "the criteria are unit-testable")
        assert routing_verdict.verdict_on([body]) == "FLEET"

    def test_a_verdict_comment_states_the_destination_and_the_actor(self):
        body = routing_verdict.verdict_comment("WORKBENCH", "it drives an auth flow")
        assert routing_verdict.destination("WORKBENCH") in body
        assert routing_verdict.actor("WORKBENCH") in body

    def test_a_verdict_comment_requires_a_reason(self):
        with pytest.raises(routing_verdict.RoutingError):
            routing_verdict.verdict_comment("NEEDS WORK", "   ")

    def test_a_card_with_no_verdict_reads_as_none(self):
        assert routing_verdict.verdict_on(["🤖 dispatched", "looks fine"]) is None

    def test_the_marker_must_OPEN_the_comment_not_merely_appear_in_it(self):
        """The adversarial case, and it is the one that bites: the sweep's own
        refusal notice NAMES the verdict it is refusing. A reader that matched
        anywhere in the body would read that notice as the verdict itself."""
        chatty = (
            "🚨 routing-not-fleet: this card's routing-verdict: WORKBENCH says a "
            "person builds it, so the sweep is not promoting it."
        )
        assert routing_verdict.verdict_on([chatty]) is None

    def test_two_different_verdicts_are_a_conflict_not_a_pick(self):
        bodies = [
            routing_verdict.verdict_comment("FLEET", "unit-testable"),
            routing_verdict.verdict_comment("WORKBENCH", "drives an auth flow"),
        ]
        with pytest.raises(routing_verdict.ConflictingVerdicts):
            routing_verdict.verdict_on(bodies)

    def test_the_same_verdict_twice_is_still_one_verdict(self):
        body = routing_verdict.verdict_comment("PARKED", "deliberately not built")
        assert routing_verdict.verdict_on([body, body]) == "PARKED"

    def test_every_route_round_trips_through_a_comment(self):
        for name in THE_FIVE:
            body = routing_verdict.verdict_comment(name, "because")
            assert routing_verdict.verdict_on([body]) == name

    def test_the_write_path_refuses_a_second_conflicting_verdict(self):
        existing = [routing_verdict.verdict_comment("FLEET", "unit-testable")]
        problem = routing_verdict.stamp_refusal("WORKBENCH", existing)
        assert problem is not None and "FLEET" in problem

    def test_the_write_path_refuses_a_duplicate_stamp(self):
        existing = [routing_verdict.verdict_comment("FLEET", "unit-testable")]
        assert routing_verdict.stamp_refusal("FLEET", existing) is not None

    def test_the_write_path_is_clean_on_a_fresh_card(self):
        assert routing_verdict.stamp_refusal("FLEET", []) is None


# ===========================================================================
# 4 (precedence 1): a label routes without a model being asked
# ===========================================================================
class TestLabelsAreReadFirst:
    def test_agent_ops_routes_operator_with_no_model(self):
        decision = routing_verdict.route(
            "Rotate the CloudFront key group",
            "## Acceptance criteria\n- [ ] the key group exists in prod",
            ["repo:agent-bureau", "agent:ops"],
        )
        assert decision.verdict == "OPERATOR"
        assert decision.source == "label"
        assert decision.needs_model is False

    def test_no_code_routes_operator_with_no_model(self):
        decision = routing_verdict.route(
            "Run the tenant backfill", "- [ ] rows backfilled", ["no-code"]
        )
        assert decision.verdict == "OPERATOR"
        assert decision.needs_model is False

    def test_the_label_match_is_exact_not_a_prefix(self):
        """`no-codegen` is not `no-code`. Same mistake class as the substring
        blocker match, one field over."""
        assert routing_verdict.label_verdict(["no-codegen"]) is None
        assert routing_verdict.label_verdict(["agent:opsgenie"]) is None

    def test_a_label_beats_the_criteria_rule(self):
        """Precedence is strict: the critic is not paid to rediscover what the
        card already says."""
        decision = routing_verdict.route(
            "Rotate the key",
            "## Acceptance criteria\n- [ ] the screen renders identically to the design",
            ["agent:ops"],
        )
        assert decision.verdict == "OPERATOR"
        assert decision.source == "label"


# ===========================================================================
# 5 (precedence 2): title conventions, anchored, each with a mutation fixture
# ===========================================================================
class TestTitleConventions:
    def test_there_is_at_least_one_convention(self):
        assert routing_verdict.title_conventions()

    @pytest.mark.parametrize(
        "convention",
        routing_verdict.title_conventions(),
        ids=lambda c: c["pattern"],
    )
    def test_every_pattern_is_anchored_at_the_start(self, convention):
        """Never a substring search. `_BLOCKER_LINE` in reconcile.py WAS a bare
        substring match over prose and it deadlocked a live epic for five days
        by matching "neither depends on the other" (DRE-2670) — it was anchored
        on 2026-08-23. Do not build a second one."""
        assert convention["pattern"].startswith("^"), (
            f"{convention['pattern']!r} is not anchored — a title convention "
            "that matches mid-string is the DRE-2670 bug in a new field"
        )

    @pytest.mark.parametrize(
        "convention",
        routing_verdict.title_conventions(),
        ids=lambda c: c["verdict"],
    )
    def test_every_convention_matches_its_own_example(self, convention):
        assert routing_verdict.title_verdict(convention["example"]) == convention["verdict"]

    @pytest.mark.parametrize(
        "convention",
        routing_verdict.title_conventions(),
        ids=lambda c: c["verdict"],
    )
    def test_every_convention_ships_an_adversarial_fixture(self, convention):
        """THE MUTATION TEST, data-driven so a new convention cannot be added
        without one: the fixture is a title that MENTIONS the token without
        DECLARING it."""
        assert convention.get("adversarial"), (
            f"the {convention['verdict']} convention ships no adversarial title"
        )
        for title in convention["adversarial"]:
            assert routing_verdict.title_verdict(title) is None, (
                f"{title!r} merely mentions the token — it must not match"
            )

    def test_the_config_check_refuses_an_unanchored_pattern(self):
        """Guard the guard: the validator is what stops the next convention
        being written as a substring match."""
        doc = routing_verdict.load()
        broken = json.loads(json.dumps(doc))
        broken["title_conventions"][0]["pattern"] = "SIGN-OFF"
        problems = routing_verdict.config_problems(broken)
        assert any("anchor" in p for p in problems), problems

    def test_the_config_check_refuses_a_convention_with_no_adversarial_fixture(self):
        doc = routing_verdict.load()
        broken = json.loads(json.dumps(doc))
        broken["title_conventions"][0].pop("adversarial", None)
        problems = routing_verdict.config_problems(broken)
        assert any("adversarial" in p for p in problems), problems

    def test_the_committed_config_is_clean(self):
        assert routing_verdict.config_problems() == []

    def test_a_title_convention_routes_with_no_model(self):
        convention = routing_verdict.title_conventions()[0]
        decision = routing_verdict.route(convention["example"], "- [ ] done", [])
        assert decision.source == "title"
        assert decision.needs_model is False


# ===========================================================================
# 2 and 3 (precedence 3): read what the acceptance criteria actually require
# ===========================================================================
INTERACTIVE_CARD = """\
The console signs you out every hour.

## Acceptance criteria
- [ ] sign in, force the access token past `exp`, and confirm the session
      continues with no prompt
"""

VISUAL_CARD = """\
The board columns do not match the design.

## Acceptance criteria
- [ ] the rendered board matches the design PNG at desktop width
- [ ] the column header spacing is pixel-identical to the design
"""

PLAIN_CARD = """\
Widen alembic_version.

## Acceptance criteria
- [ ] the column is varchar(255)
- [ ] a single-head test guards the chain
"""


class TestTheAcceptanceCriteriaAreTheRule:
    def test_an_interactive_flow_routes_workbench(self):
        decision = routing_verdict.route("Refresh the session", INTERACTIVE_CARD, [])
        assert decision.verdict == "WORKBENCH"
        assert decision.source == "criteria"

    def test_static_visual_fidelity_routes_fleet(self):
        """`qa-review.yml` runs a visual-QA stage (DRE-1481): it installs
        chromium via Playwright, screenshots the changed screens, and hands the
        critic both the design PNG and the render. Screenshotting a screen is
        not driving a flow."""
        decision = routing_verdict.route("Fix the board columns", VISUAL_CARD, [])
        assert decision.verdict == "FLEET"
        assert decision.source == "criteria"

    def test_the_visual_stage_this_rests_on_really_exists(self):
        """The claim above is only true while the stage is live. If the critic
        stops being handed both images, static visual fidelity stops being
        FLEET-checkable and this rule is wrong."""
        source = (ROOT / "scripts" / "visual_qa_context.py").read_text(encoding="utf-8")
        assert "Read BOTH images and compare" in source

    def test_a_static_visual_criterion_that_mentions_signing_in_still_routes_fleet(self):
        """THE ADVERSARIAL CASE for the split this card had wrong. "the sign-in
        screen renders identically to the design" names a screen, not a flow."""
        card = (
            "## Acceptance criteria\n"
            "- [ ] the sign-in screen renders identically to the design PNG\n"
        )
        assert routing_verdict.route("Restyle the login screen", card, []).verdict == "FLEET"

    def test_a_card_with_no_interactive_or_visual_signal_is_a_judgement_call(self):
        decision = routing_verdict.route("Widen alembic_version", PLAIN_CARD, [])
        assert decision.verdict is None
        assert decision.source == "judgement"
        assert decision.needs_model is True

    def test_a_card_with_no_acceptance_criteria_needs_work(self):
        """Route on the card's own stated exit condition. No exit condition, no
        route — and the missing thing is named."""
        decision = routing_verdict.route("Make search better", "Search is slow.", [])
        assert decision.verdict == "NEEDS WORK"
        assert "acceptance criteria" in decision.reason.lower()
        assert decision.needs_model is False

    def test_criteria_inside_a_code_fence_are_not_criteria(self):
        card = (
            "## Acceptance criteria\n"
            "```\n"
            "- [ ] sign in and confirm the session continues\n"
            "```\n"
        )
        assert routing_verdict.acceptance_criteria(card) == []

    def test_only_checkbox_items_count_as_criteria(self):
        """Prose that happens to describe a flow is not the card's stated exit
        condition — the same mention-versus-declaration line DRE-2670 turned on."""
        card = (
            "Users sign in and then walk through the wizard by hand today.\n\n"
            "## Acceptance criteria\n"
            "- [ ] the wizard state machine has unit tests\n"
        )
        assert routing_verdict.route("Wizard state machine", card, []).verdict != "WORKBENCH"

    @pytest.mark.parametrize(
        "criterion",
        [
            "- [ ] sign in, force the token past expiry, and confirm the session holds",
            "- [ ] confirm in production that the alert fires",
            "- [ ] walk through the invite flow and confirm the email arrives",
        ],
    )
    def test_live_state_and_interactive_criteria_all_route_workbench(self, criterion):
        card = f"## Acceptance criteria\n{criterion}\n"
        assert routing_verdict.route("A card", card, []).verdict == "WORKBENCH"

    @pytest.mark.parametrize(
        "criterion",
        [
            "- [ ] the sign-in screen renders identically to the design",
            "- [ ] the login button matches the design PNG",
            "- [ ] the production config is read from an env var",
        ],
    )
    def test_criteria_that_merely_mention_the_words_do_not_route_workbench(self, criterion):
        card = f"## Acceptance criteria\n{criterion}\n"
        assert routing_verdict.route("A card", card, []).verdict != "WORKBENCH"


# ===========================================================================
# 7: an epic is never given a buildability verdict
# ===========================================================================
EPIC_BODY = """\
The wave that makes intake honest.

## Acceptance criteria
- [ ] every child ships behind a green harness
"""


class TestAnEpicGetsAPlanTest:
    def test_an_epic_is_never_given_a_buildability_verdict(self):
        """"Could an agent build this unattended" is meaningless for a card the
        planner owns."""
        for title, labels, children in (
            ("[EPIC] Wave 1.5", [], False),
            ("Wave 1.5", ["agent:planner"], False),
            ("Wave 1.5", [], True),
        ):
            decision = routing_verdict.route(title, EPIC_BODY, labels, has_children=children)
            assert decision.verdict is None, f"{title!r} was given {decision.verdict}"
            assert decision.source == "epic"

    def test_an_epic_carrying_a_role_label_is_still_not_routed(self):
        """Precedence 1 must not fire on an epic: an epic labelled `agent:ops`
        is an epic, not an operator card."""
        decision = routing_verdict.route(
            "[EPIC] The ops wave", EPIC_BODY, ["agent:ops"], has_children=True
        )
        assert decision.verdict is None
        assert decision.source == "epic"

    def test_a_well_formed_epic_passes_its_plan_test(self):
        assert routing_verdict.plan_test(
            EPIC_BODY,
            [["repo:bureau-pipeline", "agent:engineer"],
             ["repo:bureau-pipeline", "agent:devops"]],
        ) == ()

    def test_an_epic_with_no_children_fails_the_plan_test(self):
        missing = routing_verdict.plan_test(EPIC_BODY, [])
        assert any("child" in m for m in missing), missing

    def test_a_child_with_no_inheritable_labels_fails_the_plan_test(self):
        missing = routing_verdict.plan_test(EPIC_BODY, [["agent:engineer"], []])
        assert any("label" in m for m in missing), missing

    def test_an_epic_with_no_acceptance_criterion_for_the_set_fails(self):
        missing = routing_verdict.plan_test(
            "A wave.", [["repo:bureau-pipeline", "agent:engineer"]]
        )
        assert any("acceptance" in m for m in missing), missing

    def test_the_plan_test_never_asks_whether_an_agent_could_build_it(self):
        """The findings name plan defects — children, labels, a criterion for
        the set. None of the five buildability routes may appear."""
        missing = routing_verdict.plan_test("A wave.", [])
        for finding in missing:
            for name in THE_FIVE:
                assert name not in finding


# ===========================================================================
# 6: a PARKED card is never reported as stalled by any sweep
# ===========================================================================
PARKED_COMMENT = routing_verdict.verdict_comment(
    "PARKED", "well-formed and deliberately not to be built until the wave closes"
)


class TestParkedIsNeverStalled:
    def test_is_parked_reads_the_marker(self):
        assert routing_verdict.is_parked([PARKED_COMMENT]) is True
        assert routing_verdict.is_parked(["🤖 dispatched", "parked, I think"]) is False

    def test_the_stranded_watchdog_leaves_a_parked_card_alone(self):
        board = _StallBoard("Todo", [PARKED_COMMENT])
        assert board.run(reconcile.flag_stranded) == set()
        assert board.posted == [] and board.labelled == []

    def test_the_planning_watchdog_leaves_a_parked_card_alone(self):
        board = _StallBoard("Planning", [PARKED_COMMENT])
        assert board.run(reconcile.flag_stalled_planning) == set()
        assert board.posted == [] and board.labelled == []

    def test_guard_the_guard_an_unparked_card_in_the_same_shape_is_flagged(self):
        assert _StallBoard("Todo", []).run(reconcile.flag_stranded) == {"DRE-2799"}
        assert _StallBoard("Planning", []).run(reconcile.flag_stalled_planning) == {"DRE-2799"}

    def test_every_stall_reporter_consults_the_parked_check(self):
        """Structural, so a THIRD stall sweep cannot be added that skips it —
        the criterion says "any sweep", not "the two that exist today"."""
        reporters = [
            name for name in dir(reconcile)
            if name.startswith("flag_stranded") or name.startswith("flag_stalled")
        ]
        assert len(reporters) >= 2, reporters
        for name in reporters:
            source = inspect.getsource(getattr(reconcile, name))
            assert "is_parked" in source, (
                f"reconcile.{name} reports stalls without consulting "
                "routing_verdict.is_parked — a PARKED card would be reported"
            )


# ===========================================================================
# The promoter reads the verdict: WORKBENCH must not be dispatched
# ===========================================================================
class TestThePromoterRoutesOnTheVerdict:
    def test_a_fleet_card_promotes(self):
        board = _PromotionBoard([routing_verdict.verdict_comment("FLEET", "unit-testable")])
        assert board.promote() == 1
        assert board.advanced == [("DRE-2799", "Todo", "Backlog")]

    @pytest.mark.parametrize("verdict", ["WORKBENCH", "OPERATOR", "PARKED", "NEEDS WORK"])
    def test_a_non_fleet_card_is_not_dispatched(self, verdict):
        board = _PromotionBoard([routing_verdict.verdict_comment(verdict, "because")])
        assert board.promote() == 0
        assert board.advanced == []

    def test_the_refusal_names_the_destination_and_the_actor_once(self):
        board = _PromotionBoard(
            [routing_verdict.verdict_comment("WORKBENCH", "it drives an auth flow")]
        )
        board.promote()
        assert board.surfaced_once(routing_verdict.NOT_FLEET_TAG)
        notice = "\n".join(b for _, b in board.posted)
        assert routing_verdict.destination("WORKBENCH") in notice
        assert routing_verdict.actor("WORKBENCH") in notice

    def test_a_card_with_no_verdict_yet_still_promotes(self):
        """Backlog's "it carries a verdict" clause is enforced from Phase 5.
        Until the second critic writes one on every card, a verdictless card
        promotes exactly as it did before — this change refuses a WRONG
        destination, it does not freeze the board."""
        assert _PromotionBoard([]).promote() == 1

    def test_a_conflicting_pair_of_verdicts_is_refused_not_guessed(self):
        board = _PromotionBoard([
            routing_verdict.verdict_comment("FLEET", "unit-testable"),
            routing_verdict.verdict_comment("WORKBENCH", "drives an auth flow"),
        ])
        assert board.promote() == 0

    def test_the_backlog_query_still_fetches_the_comments_the_verdict_lives_in(self):
        assert "comments(last: 50)" in inspect.getsource(reconcile.backlog_children)


# ===========================================================================
# The route is written down
# ===========================================================================
class TestTheRouteIsWrittenDown:
    def test_the_standard_names_the_five_routes(self):
        text = (ROOT / "standards" / "card-quality.md").read_text(encoding="utf-8")
        for name in THE_FIVE:
            assert name in text, f"{name} is not in the card-quality standard"

    def test_the_lane_contract_no_longer_waits_on_this_card_for_the_vocabulary(self):
        """Four of the contract's Backlog and Todo clauses named DRE-2724 as the
        thing they were waiting on. The vocabulary exists now, so a `pending`
        that still reads "waiting on DRE-2724" is the drift this wave keeps
        arriving at. Naming the card as what SHIPPED is fine — what may not
        survive is the clause saying it is still owed."""
        stale = ("DRE-2724", "DRE-2724 writes routing verdicts")
        for clause in lane_contract.clauses(lane_contract.load()):
            if clause.lane in ("Backlog", "Todo"):
                assert (clause.pending or "").strip() not in stale, clause.id

    def test_the_lane_contract_carries_the_vocabulary_it_used_to_wait_for(self):
        contract = lane_contract.load()
        backlog = lane_contract.lane("Backlog", contract=contract)
        todo = lane_contract.lane("Todo", contract=contract)
        assert "routing verdict" in backlog["clauses"]["entrance"]["text"]
        # Todo now holds work for BOTH actors: a dispatched run and a person.
        for name in ("FLEET", "WORKBENCH", "OPERATOR"):
            assert name in todo["clauses"]["entrance"]["text"]
        assert reconcile.HAND_BUILT_LABEL in todo["clauses"]["entrance"]["text"]

    def test_no_claim_is_made_that_a_specific_card_proved_the_need(self):
        """DRE-2695 was cited as a card that "could not have closed". It closed:
        attempt 2 opened PR #2133 and the card went Done. There is no baseline
        yet, and pretending otherwise is the failure this wave exists to
        remove."""
        for path in (CONFIG, ROOT / "scripts" / "routing_verdict.py",
                     ROOT / "docs" / "routing-verdicts.md"):
            assert "DRE-2695" not in path.read_text(encoding="utf-8"), path

    def test_the_blocker_regex_is_described_in_the_past_tense(self):
        """`_BLOCKER_LINE` is anchored — it stopped being a bare substring match
        on 2026-08-23, a day before this card was written. Say "was"."""
        source = (ROOT / "scripts" / "routing_verdict.py").read_text(encoding="utf-8")
        if "_BLOCKER_LINE" in source:
            assert re.search(r"_BLOCKER_LINE\b[^.]*\bWAS\b", source), (
                "the reference to _BLOCKER_LINE must say it WAS a substring "
                "match — it was anchored by DRE-2670 before this card existed"
            )


# ===========================================================================
# harnesses
# ===========================================================================
def _card(state, comments):
    return {
        "id": "uuid-2799",
        "identifier": "DRE-2799",
        "title": "a card",
        "description": "## Acceptance criteria\n- [ ] it works",
        "createdAt": "2026-08-01T00:00:00.000Z",
        "updatedAt": "2026-01-01T00:00:00.000Z",  # ancient: past every window
        "state": {"name": state},
        "parent": {"identifier": "DRE-2700", "state": {"name": "In Progress"}},
        "labels": {"nodes": [{"name": "repo:bureau-pipeline"},
                             {"name": "agent:engineer"}]},
        "comments": {"nodes": [{"body": b} for b in comments]},
        "inverseRelations": {"nodes": []},
    }


class _StallBoard:
    """One ancient card in a swept lane, with every other exemption closed — so
    the only thing that can spare it is its routing verdict."""

    def __init__(self, state, comments):
        self.card = _card(state, comments)
        self.comments = list(comments)
        self.posted: list[tuple[str, str]] = []
        self.labelled: list[tuple[str, str]] = []

    def run(self, fn):
        with patch.object(reconcile, "REPO_SLUG", "bureau-pipeline"), patch.object(
            reconcile, "active_cards", return_value=[self.card]
        ), patch.object(
            reconcile.linear_ops, "comment_bodies", return_value=self.comments
        ), patch.object(
            reconcile.linear_ops, "cmd_comment",
            side_effect=lambda i, b: self.posted.append((i, b)),
        ), patch.object(
            reconcile.linear_ops, "add_label",
            side_effect=lambda i, label: self.labelled.append((i, label)),
        ):
            # flag_stranded folds in flag_stalled_planning, which skips this
            # card on its own `state != "Planning"` clause — no patch needed.
            return fn()


class _PromotionBoard:
    """`reconcile.promote_ready` over ONE Backlog child of an active epic, with
    every other gate open — so the only thing that can hold the card is its
    routing verdict."""

    def __init__(self, comments):
        self.card = _card("Backlog", comments)
        self.advanced: list[tuple[str, str, str]] = []
        self.posted: list[tuple[str, str]] = []

    def promote(self) -> int:
        with patch.object(reconcile, "REPO_SLUG", "bureau-pipeline"), patch.object(
            reconcile, "backlog_children", return_value=[self.card]
        ), patch.object(
            reconcile, "epic_blockers_unmet", return_value=False
        ), patch.object(
            reconcile.mid_epic, "last_green_light", return_value=None
        ), patch.object(
            reconcile.linear_ops, "cmd_advance",
            side_effect=lambda i, to, frm: self.advanced.append((i, to, frm)),
        ), patch.object(
            reconcile.linear_ops, "cmd_comment",
            side_effect=lambda i, b: self.posted.append((i, b)),
        ), patch.object(
            reconcile.linear_ops, "count_comments",
            side_effect=lambda i, needle, **kw: sum(
                1 for pi, pb in self.posted if pi == i and needle in pb
            ),
        ):
            return reconcile.promote_ready(active_count=0)

    def surfaced_once(self, tag):
        return len([b for i, b in self.posted if tag in b]) == 1


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
