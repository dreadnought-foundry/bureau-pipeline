"""RED-first tests: a finding made mid-build must be able to join an approved
epic without a new green light (DRE-2739).

THE GAP: every route the intake design describes carries work arriving from
OUTSIDE — idea → Intake → Planning → green light → build. The highest-value
findings are made MID-BUILD, by whoever is already in the code, about an epic
that is already approved and already running. That finding has nowhere to go:
send it to Intake and it queues behind a planning cycle, add it silently and the
approved plan no longer describes the work. People choose the second one.

WHAT THIS PINS, one section per acceptance criterion:

  1. A card added to an In Progress epic is REFUSED PROMOTION until it carries a
     verdict. `reconcile.promote_ready` auto-promotes a Backlog child of an
     active epic on the next sweep — within fifteen minutes — so a card nobody
     has read dispatches an agent. Layer 1 is not waived.
  2. An ADDITION reaches Backlog and promotes WITHOUT passing through Green
     Light. That human decision was already made for this epic; re-asking on
     every second call site is how the queue becomes the bottleneck again.
  3. An AMENDMENT — the plan no longer describes the work — routes the epic back
     to Planning, and the epic's re-approval is observed.
  4. The one-line justification for addition-vs-amendment is required AT
     CREATION, not optional.
  5. The epic's artifact is updated in the SAME MOTION, and a card added without
     the artifact changing is surfaced.
  6. Green-lit card count and current card count are BOTH visible on the epic —
     approved at nine cards, running at fourteen. Silent accretion turns an
     approved scope into an unapproved one with no single decision being wrong.
  7. Creating a sub-issue OF A CARD is refused with a message naming the
     reclassification consequence. `validate_card.infer_agent_label` decides what
     a card IS from whether it has children, and `reconcile.promote_ready` skips
     every `agent:planner` card — so giving a card sub-issues silently converts
     it into an epic and stops it ever being promoted.

Run: cd bureau-pipeline && python3 -m pytest tests/test_mid_epic_discovery.py -v
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
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import linear_ops  # noqa: E402
import mid_epic  # noqa: E402
import reconcile  # noqa: E402

GREEN_LIGHT = "2026-08-20T09:00:00.000Z"
BEFORE = "2026-08-19T12:00:00.000Z"
AFTER = "2026-08-25T14:00:00.000Z"
LATER = "2026-08-26T14:00:00.000Z"


# ===========================================================================
# 4: the one-line justification is required AT CREATION, not optional
# ===========================================================================
class TestJustificationRequired:
    def test_a_kind_and_a_reason_together_are_clean(self):
        assert (
            mid_epic.classification_problem(
                mid_epic.ADDITION, "a second call site needs the same fix"
            )
            is None
        )

    def test_a_missing_kind_is_a_problem(self):
        problem = mid_epic.classification_problem(None, "a second call site")
        assert problem is not None
        assert mid_epic.ADDITION in problem and mid_epic.AMENDMENT in problem

    @pytest.mark.parametrize("reason", [None, "", "   ", "\n"])
    def test_a_missing_reason_is_a_problem(self, reason):
        problem = mid_epic.classification_problem(mid_epic.ADDITION, reason)
        assert problem is not None
        assert "justification" in problem.lower()

    def test_the_distinguishing_question_is_stated_not_left_to_taste(self):
        """Not size, effort or urgency — does the approved plan still describe
        what we are doing? A writer choosing a kind must be told the test."""
        problem = mid_epic.classification_problem("neither", "a reason")
        assert problem is not None
        assert "plan" in problem.lower()

    def test_no_card_is_created_when_the_classification_is_missing(self):
        """The refusal is at CREATION: the seam must not create the sibling and
        then ask. A refused motion touches Linear not at all."""
        ops = _FakeOps()
        with pytest.raises(mid_epic.DiscoveryRefused):
            mid_epic.discovery(ops, "DRE-2700", kind=mid_epic.ADDITION, because="",
                               title="a second call site", body="- work")
        assert ops.created == []
        assert ops.comments == []
        assert ops.descriptions == []

    def test_an_addition_without_a_card_to_create_is_refused(self):
        ops = _FakeOps()
        with pytest.raises(mid_epic.DiscoveryRefused):
            mid_epic.discovery(ops, "DRE-2700", kind=mid_epic.ADDITION,
                               because="a second call site", title=None, body=None)
        assert ops.created == []


# ===========================================================================
# 1: no verdict, no promotion — Layer 1 is not waived
# ===========================================================================
class TestVerdictBeforePromotion:
    def test_a_mid_epic_card_with_no_verdict_is_refused(self):
        refusal = mid_epic.promotion_refusal("DRE-2740", AFTER, GREEN_LIGHT, [])
        assert refusal is not None
        assert "verdict" in refusal.lower()

    def test_a_mid_epic_card_carrying_a_verdict_promotes(self):
        verdict = mid_epic.verdict_comment(
            mid_epic.ADDITION, "a second call site needs the same fix", "DRE-2700"
        )
        assert mid_epic.promotion_refusal("DRE-2740", AFTER, GREEN_LIGHT, [verdict]) is None

    def test_a_card_the_plan_anticipated_is_not_touched(self):
        """A child created BEFORE the green light was read when the epic was
        approved. The gate must not suddenly demand a verdict of the whole
        approved roster."""
        assert mid_epic.promotion_refusal("DRE-2701", BEFORE, GREEN_LIGHT, []) is None

    def test_an_unreadable_green_light_abstains_rather_than_refusing(self):
        """Console-honesty rule 1: unknown is unknown. If Linear cannot say when
        the epic was green-lit we cannot say the card came after it — and
        refusing every child on an unreadable read would freeze the fleet."""
        assert mid_epic.promotion_refusal("DRE-2740", AFTER, None, []) is None

    def test_the_verdict_marker_is_what_is_read_not_any_comment(self):
        """A chatty card is not an approved one."""
        assert mid_epic.promotion_refusal(
            "DRE-2740", AFTER, GREEN_LIGHT,
            ["🤖 dispatched", "looks fine to me", "⏳ 1/5 plan formed"],
        ) is not None

    def test_added_after_green_light_is_the_derivation(self):
        assert mid_epic.added_after_green_light(AFTER, GREEN_LIGHT) is True
        assert mid_epic.added_after_green_light(BEFORE, GREEN_LIGHT) is False
        assert mid_epic.added_after_green_light(AFTER, None) is None
        assert mid_epic.added_after_green_light(None, GREEN_LIGHT) is None

    # --- the wiring: the sweep itself refuses ------------------------------
    def test_the_sweep_does_not_promote_a_verdictless_mid_epic_card(self):
        board = _PromotionBoard(created_at=AFTER, comments=[])
        assert board.promote() == 0
        assert board.advanced == []
        assert board.surfaced_once(mid_epic.NO_VERDICT_TAG)

    def test_the_sweep_promotes_it_once_the_verdict_is_there(self):
        verdict = mid_epic.verdict_comment(
            mid_epic.ADDITION, "a second call site needs the same fix", "DRE-2700"
        )
        board = _PromotionBoard(created_at=AFTER, comments=[verdict])
        assert board.promote() == 1
        assert board.advanced == [("DRE-2740", "Todo", "Backlog")]

    def test_the_sweep_still_promotes_the_planned_roster(self):
        """Guard the guard: the same board with a card the plan anticipated
        promotes, so the refusal above is the verdict rule and not a harness
        that never promotes anything."""
        board = _PromotionBoard(created_at=BEFORE, comments=[])
        assert board.promote() == 1

    def test_the_backlog_query_asks_when_the_card_was_created(self):
        """The derivation is `createdAt` vs the epic's green light. If the
        promotion query stops fetching it the gate silently abstains on every
        card — which reads exactly like the bug this card fixes."""
        import inspect

        assert "createdAt" in inspect.getsource(reconcile.backlog_children)


# ===========================================================================
# 2: an addition reaches Backlog and promotes without a new green light
# ===========================================================================
class TestAdditionNeedsNoGreenLight:
    def test_the_addition_lands_in_backlog_with_a_verdict_and_promotes(self):
        ops = _FakeOps(epic_description="The epic.", children=[("DRE-2701", BEFORE)])
        ident = mid_epic.discovery(
            ops, "DRE-2700",
            kind=mid_epic.ADDITION,
            because="a second call site needs the same fix",
            title="fix the second call site",
            body="## What\n- the same fix, one call site along",
        )
        assert ident == "DRE-2740"
        assert ops.created_state("DRE-2740") == "Backlog"
        assert mid_epic.carries_verdict(ops.comments_on("DRE-2740"))

        board = _PromotionBoard(
            created_at=AFTER, comments=ops.comments_on("DRE-2740")
        )
        assert board.promote() == 1
        assert board.visited("DRE-2740") == ["Backlog", "Todo"]
        for lane in mid_epic.GREEN_LIGHT_LANES:
            assert lane not in board.visited("DRE-2740")

    def test_an_addition_never_moves_the_epic(self):
        """The green light is what is waived — the epic keeps running."""
        ops = _FakeOps(epic_description="The epic.", epic_state="In Progress")
        mid_epic.discovery(ops, "DRE-2700", kind=mid_epic.ADDITION,
                           because="a second call site needs the same fix",
                           title="fix the second call site", body="- work")
        assert ops.states == []
        assert ops.epic_state == "In Progress"


# ===========================================================================
# 3: an amendment routes the epic back to Planning, re-approval observed
# ===========================================================================
class TestAmendmentReturnsToPlanning:
    def test_an_amendment_moves_the_epic_to_planning_and_creates_no_card(self):
        ops = _FakeOps(epic_description="The epic.", epic_state="In Progress")
        ident = mid_epic.discovery(
            ops, "DRE-2700",
            kind=mid_epic.AMENDMENT,
            because="the fix must be split and its order reversed",
        )
        assert ident is None
        assert ops.created == []
        assert ("DRE-2700", mid_epic.AMENDMENT_STATE) in ops.states
        body = "\n".join(ops.comments_on("DRE-2700"))
        assert mid_epic.AMENDMENT_TAG in body
        assert "the fix must be split and its order reversed" in body

    def test_the_epic_records_that_it_owes_a_re_approval(self):
        ops = _FakeOps(epic_description="The epic.", epic_state="In Progress")
        mid_epic.discovery(ops, "DRE-2700", kind=mid_epic.AMENDMENT,
                           because="the fix must be split and its order reversed")
        artifact = mid_epic.parse_artifact(ops.epic_description)
        assert len(artifact["amendments"]) == 1
        assert artifact["amendments"][0]["re_green_lit"] is None

    def test_the_re_approval_is_observed_and_recorded(self):
        """The epic goes back to Planning, a human green-lights it again, and the
        sweep OBSERVES that — the epic is in an active lane again, read live,
        never an assumption that a return to Planning will be answered."""
        ops = _FakeOps(epic_description="The epic.", epic_state="In Progress")
        mid_epic.discovery(ops, "DRE-2700", kind=mid_epic.AMENDMENT,
                           because="the fix must be split and its order reversed")
        assert ops.epic_state == mid_epic.AMENDMENT_STATE

        ops.epic_state = "In Progress"   # the human re-green-lit it
        ops.green_lit_at = LATER         # ...and the history says when
        report = mid_epic.refresh_epic_growth(ops, "DRE-2700")

        assert report["re_approved"] == ["DRE-2700"]
        artifact = mid_epic.parse_artifact(ops.epic_description)
        assert artifact["amendments"][0]["re_green_lit"] == LATER
        assert mid_epic.REAPPROVAL_TAG in "\n".join(ops.comments_on("DRE-2700"))

    def test_an_amendment_still_waiting_is_not_reported_as_approved(self):
        """The epic is still sitting in Planning — nobody has re-green-lit it."""
        ops = _FakeOps(epic_description="The epic.", epic_state="In Progress")
        mid_epic.discovery(ops, "DRE-2700", kind=mid_epic.AMENDMENT,
                           because="the fix must be split and its order reversed")
        report = mid_epic.refresh_epic_growth(ops, "DRE-2700")
        assert report["re_approved"] == []
        assert mid_epic.parse_artifact(ops.epic_description)["amendments"][0][
            "re_green_lit"
        ] is None
        assert mid_epic.AWAITING_REAPPROVAL in ops.epic_description

    def test_the_re_approval_notice_is_posted_once_ever(self):
        ops = _FakeOps(epic_description="The epic.", epic_state="In Progress")
        mid_epic.discovery(ops, "DRE-2700", kind=mid_epic.AMENDMENT,
                           because="the fix must be split and its order reversed")
        ops.epic_state, ops.green_lit_at = "In Progress", LATER
        mid_epic.refresh_epic_growth(ops, "DRE-2700")
        mid_epic.refresh_epic_growth(ops, "DRE-2700")
        notices = [c for c in ops.comments_on("DRE-2700") if mid_epic.REAPPROVAL_TAG in c]
        assert len(notices) == 1


# ===========================================================================
# 5: the artifact moves in the same motion, and silent growth is surfaced
# ===========================================================================
class TestArtifactMovesWithTheCard:
    def test_the_addition_writes_the_epic_artifact_in_the_same_motion(self):
        ops = _FakeOps(epic_description="The epic.", children=[("DRE-2701", BEFORE)])
        mid_epic.discovery(ops, "DRE-2700", kind=mid_epic.ADDITION,
                           because="a second call site needs the same fix",
                           title="fix the second call site", body="- work")
        assert ops.descriptions, "the epic's artifact was never written"
        artifact = mid_epic.parse_artifact(ops.epic_description)
        assert artifact["additions"] == [
            {"id": "DRE-2740", "because": "a second call site needs the same fix"}
        ]

    def test_the_original_epic_body_survives_the_update(self):
        ops = _FakeOps(epic_description="# The epic\n\nThe CEO-readable summary.")
        mid_epic.discovery(ops, "DRE-2700", kind=mid_epic.ADDITION,
                           because="a second call site needs the same fix",
                           title="fix the second call site", body="- work")
        assert "The CEO-readable summary." in ops.epic_description

    def test_a_second_motion_appends_rather_than_replacing(self):
        ops = _FakeOps(epic_description="The epic.")
        mid_epic.discovery(ops, "DRE-2700", kind=mid_epic.ADDITION,
                           because="a second call site needs the same fix",
                           title="one", body="- work")
        mid_epic.discovery(ops, "DRE-2700", kind=mid_epic.ADDITION,
                           because="a third call site too",
                           title="two", body="- work")
        ids = [a["id"] for a in mid_epic.parse_artifact(ops.epic_description)["additions"]]
        assert ids == ["DRE-2740", "DRE-2741"]

    def test_a_card_added_without_the_artifact_changing_is_surfaced(self):
        """The hand-add: somebody creates the sibling in Linear directly. The
        epic grew and its plan did not move — say so."""
        ops = _FakeOps(
            epic_description="The epic.",
            children=[("DRE-2701", BEFORE), ("DRE-2740", AFTER)],
            green_lit_at=GREEN_LIGHT,
        )
        report = mid_epic.refresh_epic_growth(ops, "DRE-2700")
        assert report["unrecorded"] == ["DRE-2740"]
        assert mid_epic.UNRECORDED_TAG in "\n".join(ops.comments_on("DRE-2700"))

    def test_a_recorded_addition_is_not_surfaced(self):
        """Guard the guard: the surfacing must key on the artifact, not on the
        card being new."""
        ops = _FakeOps(epic_description="The epic.", children=[("DRE-2701", BEFORE)],
                       green_lit_at=GREEN_LIGHT)
        mid_epic.discovery(ops, "DRE-2700", kind=mid_epic.ADDITION,
                           because="a second call site needs the same fix",
                           title="fix the second call site", body="- work")
        report = mid_epic.refresh_epic_growth(ops, "DRE-2700")
        assert report["unrecorded"] == []

    def test_the_silent_growth_notice_is_posted_once_per_card(self):
        ops = _FakeOps(epic_description="The epic.",
                       children=[("DRE-2740", AFTER)], green_lit_at=GREEN_LIGHT)
        mid_epic.refresh_epic_growth(ops, "DRE-2700")
        mid_epic.refresh_epic_growth(ops, "DRE-2700")
        notices = [c for c in ops.comments_on("DRE-2700") if mid_epic.UNRECORDED_TAG in c]
        assert len(notices) == 1

    def test_nothing_is_surfaced_when_the_green_light_cannot_be_read(self):
        """Unknown is unknown: without the approval timestamp every child looks
        equally new, and accusing the whole roster of silent accretion is a
        confident wrong answer (console-honesty rule 2)."""
        ops = _FakeOps(epic_description="The epic.",
                       children=[("DRE-2740", AFTER)], green_lit_at=None)
        report = mid_epic.refresh_epic_growth(ops, "DRE-2700")
        assert report["unrecorded"] == []


# ===========================================================================
# 6: growth is legible — both counts, on the epic
# ===========================================================================
class TestGrowthIsLegible:
    def test_both_counts_are_rendered_on_the_epic(self):
        ops = _FakeOps(
            epic_description="The epic.",
            children=[(f"DRE-27{n:02d}", BEFORE) for n in range(9)]
            + [(f"DRE-28{n:02d}", AFTER) for n in range(5)],
            green_lit_at=GREEN_LIGHT,
        )
        report = mid_epic.refresh_epic_growth(ops, "DRE-2700")
        assert (report["green_lit"], report["current"]) == (9, 14)
        assert "9" in ops.epic_description and "14" in ops.epic_description
        parsed = mid_epic.parse_artifact(ops.epic_description)
        assert parsed["green_lit"] == 9
        assert parsed["current"] == 14

    def test_an_unknown_green_light_count_renders_unknown_not_zero(self):
        """A "green-lit at 0 cards" line invents an approval that never
        happened — the one conclusion the number must never fabricate."""
        line = mid_epic.growth_line("DRE-2700", None, 14)
        assert "unknown" in line.lower()
        assert "0" not in line.split("running")[0]

    def test_the_growth_line_says_both_numbers(self):
        line = mid_epic.growth_line("DRE-2700", 9, 14)
        assert "9" in line and "14" in line and "DRE-2700" in line

    def test_the_sweep_reports_growth_for_this_repos_active_epics(self):
        with patch.object(mid_epic, "refresh_epic_growth") as refresh:
            refresh.return_value = {
                "green_lit": 9, "current": 14, "unrecorded": [], "re_approved": [],
            }
            reconcile.report_epic_growth({"DRE-2700"})
        refresh.assert_called_once()
        assert refresh.call_args.args[1] == "DRE-2700"

    def test_a_growth_read_that_fails_never_fails_the_sweep(self):
        with patch.object(
            mid_epic, "refresh_epic_growth", side_effect=RuntimeError("linear down")
        ):
            reconcile.report_epic_growth({"DRE-2700"})  # must not raise


# ===========================================================================
# 7: a card has no children — the sub-issue is refused, loudly
# ===========================================================================
class TestACardHasNoChildren:
    def test_a_sub_issue_of_a_plain_card_is_refused(self):
        refusal = mid_epic.subissue_refusal(
            "deploy-console.sh records a mutable tag", ["agent:engineer", "repo:atlas"],
            has_children=False,
        )
        assert refusal is not None

    def test_the_refusal_names_the_reclassification_consequence(self):
        """"Refused" is not enough — the writer's next move is a suffix or a
        sub-issue, and both are wrong. Say what giving a card children DOES."""
        refusal = mid_epic.subissue_refusal(
            "a plain card", ["agent:engineer"], has_children=False
        )
        low = refusal.lower()
        assert "agent:planner" in low
        assert "promot" in low, "the consequence is that it stops being promoted"
        assert "sibling" in low, "the refusal must name the route that works"

    @pytest.mark.parametrize(
        "title,labels,has_children",
        [
            ("[EPIC] the intake front door", ["agent:planner"], False),
            ("the intake front door", ["agent:planner"], False),
            ("[epic] lowercase counts too", ["agent:engineer"], False),
            ("an epic that already has children", ["agent:engineer"], True),
        ],
    )
    def test_a_real_epic_is_not_refused(self, title, labels, has_children):
        assert mid_epic.subissue_refusal(title, labels, has_children) is None

    def test_the_epic_test_matches_the_classifier_that_causes_the_harm(self):
        """The refusal exists because `validate_card.infer_agent_label` reads
        children as epic-ness. If the two ever disagree the guard is guarding
        the wrong thing."""
        import validate_card

        assert validate_card.infer_agent_label("a plain card", False, []) == "agent:engineer"
        assert validate_card.infer_agent_label("a plain card", True, []) == "agent:planner"

    # --- the wiring: the create seam itself refuses ------------------------
    def test_cmd_subissue_refuses_a_card_parent_and_creates_nothing(self):
        with patch.object(linear_ops, "get_issue") as get_issue, patch.object(
            linear_ops, "_issue_label_names", return_value=["agent:engineer", "repo:atlas",
                                                            "initiative:bureau"]
        ), patch.object(
            linear_ops, "_issue_has_children", return_value=False
        ), patch.object(linear_ops, "gql") as gql:
            get_issue.return_value = {
                "id": "uuid-2716", "identifier": "DRE-2716",
                "title": "a plain card", "team": {"id": "team-1"},
                "labels": {"nodes": [{"name": "agent:engineer"}]},
            }
            with pytest.raises(linear_ops.LinearError) as err:
                linear_ops.cmd_subissue("DRE-2716", "a finding", "## What\n- work")
        assert "agent:planner" in str(err.value)
        gql.assert_not_called()

    def test_cmd_subissue_still_creates_a_child_of_a_real_epic(self):
        """Guard the guard: the planner's own path must be untouched."""
        with patch.object(linear_ops, "get_issue") as get_issue, patch.object(
            linear_ops, "_issue_label_names",
            return_value=["agent:planner", "repo:atlas", "initiative:bureau"],
        ), patch.object(
            linear_ops, "_issue_has_children", return_value=False
        ), patch.object(linear_ops, "state_id", return_value="state-backlog"
        ), patch.object(linear_ops, "_team_label_ids", return_value=["l1"]
        ), patch.object(linear_ops, "gql") as gql:
            get_issue.return_value = {
                "id": "uuid-2700", "identifier": "DRE-2700",
                "title": "[EPIC] the front door", "team": {"id": "team-1"},
                "labels": {"nodes": [{"name": "agent:planner"}]},
            }
            gql.return_value = {
                "issueCreate": {
                    "success": True,
                    "issue": {"id": "uuid-2740", "identifier": "DRE-2740",
                              "url": "https://linear.app/x/DRE-2740"},
                }
            }
            created = linear_ops.cmd_subissue("DRE-2700", "a child", "## What\n- work")
        assert gql.called
        assert created["identifier"] == "DRE-2740", (
            "the mid-epic route needs the identifier it just created so it can "
            "record the growth on the epic in the same motion"
        )


# ===========================================================================
# Test doubles
# ===========================================================================
class _FakeOps:
    """A tiny stand-in for the `linear_ops` MODULE, which is how every
    Linear-touching seam in this codebase takes its I/O (break_glass,
    validate_card._bounce). Only the verbs mid_epic actually uses."""

    def __init__(self, epic_description="The epic.", epic_state="In Progress",
                 children=(), green_lit_at=GREEN_LIGHT):
        self.epic_description = epic_description
        self.epic_state = epic_state
        self.children = list(children)
        self.green_lit_at = green_lit_at
        self.created: list[dict] = []
        self.comments: list[tuple[str, str]] = []
        self.states: list[tuple[str, str]] = []
        self.descriptions: list[tuple[str, str]] = []
        self._next = 2740
        self.LinearError = linear_ops.LinearError

    # --- the verbs --------------------------------------------------------
    def cmd_subissue(self, parent, title, body, *flags):
        ident = f"DRE-{self._next}"
        self._next += 1
        issue = {"id": f"uuid-{ident}", "identifier": ident,
                 "url": f"https://linear.app/x/{ident}", "state": "Backlog",
                 "parent": parent, "title": title}
        self.created.append(issue)
        self.children.append((ident, AFTER))
        return issue

    def cmd_comment(self, identifier, body):
        self.comments.append((identifier, body))

    def cmd_state(self, identifier, state, *flags):
        self.states.append((identifier, state))
        if identifier == "DRE-2700":
            self.epic_state = state

    def set_description(self, identifier, body):
        self.descriptions.append((identifier, body))
        self.epic_description = body

    def count_comments(self, identifier, needle, **kw):
        return sum(1 for i, b in self.comments if i == identifier and needle in b)

    def comment_bodies(self, identifier):
        return self.comments_on(identifier)

    def gql(self, query, variables=None):
        """The epic as Linear answers it: body, lane, children, and the history
        the green light is read out of. `green_lit_at=None` is an epic whose
        approval Linear cannot tell us about."""
        history = (
            [{"createdAt": self.green_lit_at, "toState": {"name": "In Progress"}}]
            if self.green_lit_at
            else []
        )
        return {
            "issue": {
                "identifier": "DRE-2700",
                "description": self.epic_description,
                "state": {"name": self.epic_state},
                "children": {
                    "nodes": [
                        {"identifier": i, "createdAt": at} for i, at in self.children
                    ]
                },
                "history": {"nodes": history},
            }
        }

    # --- assertions helpers ----------------------------------------------
    def comments_on(self, identifier):
        return [b for i, b in self.comments if i == identifier]

    def created_state(self, identifier):
        return next(c["state"] for c in self.created if c["identifier"] == identifier)


class _PromotionBoard:
    """`reconcile.promote_ready` over ONE Backlog child of an In Progress epic,
    with every other gate (WIP, epic blockers, card blockers) open — so the only
    thing that can hold the card is the mid-epic verdict rule."""

    def __init__(self, created_at, comments=()):
        self.card = {
            "id": "uuid-2740",
            "identifier": "DRE-2740",
            "title": "fix the second call site",
            "description": "## What\n- the same fix, one call site along",
            "createdAt": created_at,
            "parent": {"identifier": "DRE-2700", "state": {"name": "In Progress"}},
            "labels": {"nodes": [{"name": "repo:bureau-pipeline"},
                                 {"name": "agent:engineer"}]},
            "comments": {"nodes": [{"body": b} for b in comments]},
            "inverseRelations": {"nodes": []},
        }
        self.advanced: list[tuple[str, str, str]] = []
        self.posted: list[tuple[str, str]] = []
        self.lanes = {"DRE-2740": ["Backlog"]}

    def promote(self) -> int:
        def advance(ident, to_state, from_states):
            self.advanced.append((ident, to_state, from_states))
            self.lanes.setdefault(ident, []).append(to_state)

        with patch.object(reconcile, "REPO_SLUG", "bureau-pipeline"), patch.object(
            reconcile, "backlog_children", return_value=[self.card]
        ), patch.object(
            reconcile, "epic_blockers_unmet", return_value=False
        ), patch.object(
            reconcile.mid_epic, "last_green_light", return_value=GREEN_LIGHT
        ), patch.object(
            reconcile.linear_ops, "cmd_advance", side_effect=advance
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

    def visited(self, identifier):
        return self.lanes.get(identifier, [])

    def surfaced_once(self, tag):
        return len([b for i, b in self.posted if tag in b]) == 1


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
