"""A Queued card whose blockers are all finished says why it waits (DRE-8646).

THE HOLE. `promote_ready` refuses a Queued card at several exits that only
log: the WIP budget, a hold, an epic that is not active, the epic gate, the
epic cap's queue. On 2026-11-01 DRE-7909 — an OPERATOR card, both blockers
Done — had sat in Queued for seven days, and the sweep log read the same
skip line on every pass while the card itself said nothing.

WHAT IS UNDER TEST, and every one of these fails before the fix:

  * `promotion_wait` is the vocabulary: the opener, the five reasons, the
    once-only key, the comment text, and `SAYS_WHY` — every opener a Queued
    card's thread may carry as the sweep's answer for why it has not moved;
  * one comment per card per reason, posted through `_surface_once` and
    never twice, at each of the five refusals, for a card whose blockers are
    all terminal;
  * at the budget exit the reason is read off facts already in hand — the
    hold, then the epic's lane, then the cap;
  * nothing is said for a card with a blocker still open, another repo's
    card, an epic, a card whose relations were not read to the end, a card a
    refusal already speaks for, a plan-critic refusal inside its grace, or a
    card a stamped hold already explains;
  * the opener and the reasons are declared in `config/pipeline-acts.json`.

Part 1 — a person's card under a hand-applied hold is carried to Hand-work —
is `tests/test_operator_hold_promotion.py`.

Run: python3 -m pytest tests/test_promotion_wait.py -v
"""
from __future__ import annotations

import copy
import os
import sys
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")

import check_act_receipts  # noqa: E402
import epic_cap  # noqa: E402
import hold  # noqa: E402
import mid_epic  # noqa: E402
import pipeline_act  # noqa: E402
import plan_critic  # noqa: E402
import promotion_wait  # noqa: E402
import prose_blockers  # noqa: E402
import reconcile  # noqa: E402
import routing_verdict  # noqa: E402

LABEL = hold.HOLD_LABEL
CARD = "DRE-7909"
EPIC = "DRE-8486"
SHA = "d" * 40

FLEET = routing_verdict.verdict_comment("FLEET", "the acceptance criteria are unit-testable")
OPERATOR = routing_verdict.verdict_comment("OPERATOR", "it is a deploy, not a diff")
PARKED = routing_verdict.verdict_comment("PARKED", "not now")
DEAD_RUN_STAMP = hold.stamp_line("dead-run-cap", "none", "scripts/reconcile.py")
OPENER = "🧾 promotion-wait:"


def _card(
    identifier: str = CARD,
    *,
    verdict: str | None = FLEET,
    bodies: tuple[str, ...] = (),
    blockers: dict[str, str] | None = None,
    labels=("repo:bureau-pipeline",),
    epic_lane: str | None = None,
    title: str = "Rotate the relay's signing secret",
    description: str = "Rotate the relay's signing secret and confirm a dispatch.",
    relations_unread: bool = False,
):
    """A Queued card as `backlog_children` hands it to the gate: its comments
    oldest→newest in `bodies` (stored newest first, Linear's order), its
    blockers as relations, and a parent epic in `epic_lane` when given."""
    thread = ([verdict] if verdict else []) + list(bodies)
    blockers = {"DRE-6426": "Done"} if blockers is None else blockers
    relations = {"nodes": [
        {"type": "blocks", "issue": {"identifier": ident, "state": {"name": state}}}
        for ident, state in blockers.items()
    ]}
    if relations_unread:
        relations["pageInfo"] = {"hasNextPage": True, "endCursor": "c"}
    return {
        "id": f"uuid-{identifier}",
        "identifier": identifier,
        "title": title,
        "description": description,
        "createdAt": "2026-10-01T00:00:00.000Z",
        "parent": ({"identifier": EPIC, "state": {"name": epic_lane}}
                   if epic_lane else None),
        "labels": {"nodes": [{"name": name} for name in labels]},
        "comments": {"nodes": [{"body": b} for b in reversed(thread)]},
        "inverseRelations": relations,
    }


def _held(**kw):
    """A card under a hand-applied `needs-human` with no stamp."""
    return _card(labels=("repo:bureau-pipeline", LABEL), **kw)


class _Board:
    """`reconcile.promote_ready` over a Queued roster, every write recorded in
    one ordered list: ("label"|"unlabel"|"advance"|"comment", card, what).
    The epic-side reads are stubs a test sets: `epic_gate` (the epic gate's
    answer), `cap` (the cap's answer to `ask_cap_for_released_epic`), and
    `critic` (the second critic's refusal, with its tag)."""

    def __init__(self, *cards, epic_gate: bool = False, cap: str | None = None,
                 critic: tuple[str, str] | None = None, critic_overdue: bool = True):
        self.cards = list(cards)
        self.writes: list[tuple[str, str, str]] = []
        self.epic_gate = epic_gate
        self.cap = cap
        self.critic = critic
        self.critic_overdue = critic_overdue

    def promote(self, active_count: int = 0, *, door: set[str] | None = None) -> int:
        def record(kind):
            return lambda ident, what, *rest: self.writes.append((kind, ident, what))

        live = {c["identifier"]: copy.deepcopy(c) for c in self.cards}
        critic = self.critic
        cap_refusal = "epic cap: no decision on record" if self.cap else None
        patches = [
            (reconcile, "REPO_SLUG", "bureau-pipeline"),
            (reconcile, "backlog_children", {"return_value": self.cards}),
            (reconcile, "epic_blockers_unmet", {"return_value": self.epic_gate}),
            (reconcile, "epic_records", {"return_value": {}}),
            (reconcile, "_door_epic_threads", {"return_value": None}),
            (reconcile, "epic_thread", {"return_value": []}),
            (reconcile, "whole_epic_thread", {"return_value": []}),
            (reconcile.mid_epic, "last_green_light",
             {"return_value": "2026-09-01T00:00:00.000Z"}),
            (reconcile.plan_critic, "promotion_refusal",
             {"return_value": critic[1] if critic else None}),
            (reconcile.plan_critic, "refusal_tag",
             {"side_effect": lambda refusal: critic[0] if critic else None}),
            (reconcile, "post_critic_hold_is_overdue", {"return_value": self.critic_overdue}),
            (reconcile.epic_cap, "promotion_refusal", {"return_value": cap_refusal}),
            (reconcile, "undecided_hold_is_overdue", {"return_value": True}),
            (reconcile, "ask_cap_for_released_epic", {"return_value": self.cap}),
            (reconcile.routing_verdict, "lane_moves", {"return_value": []}),
            (reconcile, "_door_sourced", set(door or ())),
            (reconcile, "_fetch_backlog_linear",
             {"side_effect": lambda ids: [live[i] for i in ids if i in live]}),
            (reconcile.linear_ops, "cmd_advance",
             {"side_effect": lambda ident, to, frm: self.writes.append(("advance", ident, to))}),
            (reconcile.linear_ops, "add_label", {"side_effect": record("label")}),
            (reconcile.linear_ops, "remove_label", {"side_effect": record("unlabel")}),
            (reconcile.linear_ops, "cmd_comment", {"side_effect": record("comment")}),
            (reconcile.linear_ops, "count_comments", {"side_effect": lambda i, needle, **kw: sum(
                1 for k, ci, body in self.writes
                if k == "comment" and ci == i and needle in body)}),
            (reconcile.linear_ops, "first_comment_at", {"return_value": None}),
        ]
        with ExitStack() as stack:
            for target, name, how in patches:
                if isinstance(how, dict):
                    stack.enter_context(patch.object(target, name, **how))
                else:
                    stack.enter_context(patch.object(target, name, how))
            return reconcile.promote_ready(active_count=active_count, ask_cap=True)

    def on(self, identifier: str = CARD) -> list[tuple[str, str]]:
        return [(k, what) for k, ci, what in self.writes if ci == identifier]

    def comments_on(self, identifier: str = CARD) -> list[str]:
        return [what for k, what in self.on(identifier) if k == "comment"]

    def waits_on(self, identifier: str = CARD) -> list[str]:
        """The `promotion-wait` comments on the card."""
        return [c for c in self.comments_on(identifier) if c.startswith(OPENER)]


@pytest.fixture(autouse=True)
def _clear_write_failures():
    reconcile._write_failures.clear()
    yield
    reconcile._write_failures.clear()


FULL = reconcile.MAX_WIP  # an active count that spends the whole budget


def _opens(comment: str, reason: str, card: str = CARD) -> bool:
    return comment.startswith(f"{OPENER} {reason} — {card} ")


# --------------------------------------------------------------------------- #
# 1. the vocabulary                                                           #
# --------------------------------------------------------------------------- #


class TestTheVocabulary:
    def test_the_five_reasons(self):
        assert promotion_wait.REASONS == (
            "wip-cap", "held", "epic-not-active", "epic-gate", "epic-cap-queued")

    def test_the_opener(self):
        assert promotion_wait.OPENER == "🧾 promotion-wait"

    def test_the_key_is_the_reason_s_own_first_line_head(self):
        for reason in promotion_wait.REASONS:
            assert promotion_wait.key(reason) == f"🧾 promotion-wait: {reason} —"

    def test_no_reason_s_key_is_inside_another_s(self):
        keys = [promotion_wait.key(r) for r in promotion_wait.REASONS]
        for a in keys:
            for b in keys:
                assert a == b or a not in b

    def test_an_unknown_reason_has_no_key(self):
        with pytest.raises(ValueError):
            promotion_wait.key("busy")

    @pytest.mark.parametrize("reason", promotion_wait.REASONS)
    def test_the_first_line_is_the_contract_s(self, reason):
        text = promotion_wait.notice(reason, CARD, epic=EPIC, epic_lane="Planning",
                                     active=8, cap=8)
        first = text.splitlines()[0]
        assert first.startswith(f"🧾 promotion-wait: {reason} — {CARD} ")
        assert first.rstrip().endswith(".")
        assert "What it waits on:" in text and "What releases it:" in text

    def test_the_held_notice_names_the_label_and_its_removal(self):
        text = promotion_wait.notice("held", CARD)
        assert f"`{LABEL}`" in text and "no hold stamp" in text
        assert "removing" in text.split("What releases it:")[1]

    def test_the_epic_notice_names_the_epic_and_its_lane(self):
        text = promotion_wait.notice("epic-not-active", CARD, epic=EPIC, epic_lane="Planning")
        assert EPIC in text.splitlines()[0] and "Planning" in text.splitlines()[0]
        assert "In Progress" in text.split("What releases it:")[1]

    def test_the_wip_notice_names_the_count(self):
        text = promotion_wait.notice("wip-cap", CARD, active=9, cap=8)
        assert "9 of 8" in text


class TestSaysWhy:
    def test_it_names_every_existing_answer_by_its_constant_s_own_value(self):
        expected = {
            routing_verdict.NO_VERDICT_TAG, routing_verdict.NOT_FLEET_TAG,
            routing_verdict.STALE_VERDICT_TAG, mid_epic.NO_VERDICT_TAG,
            epic_cap.UNDECIDED_TAG, plan_critic.POST_UNREAD_TAG,
            plan_critic.POST_SENT_BACK_TAG, plan_critic.POST_DIED_TAG,
            prose_blockers.CARD_TAG, reconcile.BAD_REF_TAG,
            reconcile.BLOCKER_MARKER, hold.STAMP_PREFIX,
        }
        for value in expected:
            assert value in promotion_wait.SAYS_WHY, value

    def test_it_holds_exactly_the_contract_s_strings(self):
        assert set(promotion_wait.SAYS_WHY) == {
            "🧾 promotion-wait", "🔒 hold:", "🛑 Agent blocked",
            "routing-no-verdict", "routing-not-fleet", "stale-verdict",
            "mid-epic-no-verdict", "epic-cap-undecided", "plan-critic-post-unread",
            "plan-critic-post-sent-back", "plan-critic-post-died",
            "prose-blocker-no-relation", "unresolvable-blocker-reference",
        }
        assert len(promotion_wait.SAYS_WHY) == 13
        assert isinstance(promotion_wait.SAYS_WHY, tuple)

    def test_the_hold_s_entry_keeps_its_colon(self):
        assert "🔒 hold:" in promotion_wait.SAYS_WHY


class TestAdmitsManualHold:
    @pytest.mark.parametrize("verdict", ["OPERATOR", "WORKBENCH"])
    def test_a_person_s_card_under_a_hand_applied_hold(self, verdict):
        bodies = [routing_verdict.verdict_comment(verdict, "a person builds it")]
        assert promotion_wait.admits_manual_hold([LABEL], bodies) is True

    def test_a_fleet_card_is_not(self):
        assert promotion_wait.admits_manual_hold([LABEL], [FLEET]) is False

    def test_a_stamped_hold_is_not(self):
        assert promotion_wait.admits_manual_hold([LABEL], [OPERATOR, DEAD_RUN_STAMP]) is False

    def test_no_hold_is_not(self):
        assert promotion_wait.admits_manual_hold(["repo:x"], [OPERATOR]) is False

    def test_no_verdict_is_not(self):
        assert promotion_wait.admits_manual_hold([LABEL], []) is False

    def test_two_verdicts_are_not(self):
        assert promotion_wait.admits_manual_hold([LABEL], [OPERATOR, FLEET]) is False

    def test_labels_as_linear_nodes(self):
        assert promotion_wait.admits_manual_hold([{"name": LABEL}], [OPERATOR]) is True


# --------------------------------------------------------------------------- #
# 2. one comment per card per reason                                          #
# --------------------------------------------------------------------------- #


class TestEachReasonIsSaidOnce:
    def _once(self, board, reason, active_count=0):
        board.promote(active_count)
        waits = board.waits_on()
        assert len(waits) == 1, board.comments_on()
        assert _opens(waits[0], reason), waits[0]
        assert ("advance", "In Progress") not in board.on()
        before = list(board.writes)
        board.promote(active_count)
        assert board.writes == before, "a second pass posted again"

    def test_wip_cap(self):
        self._once(_Board(_card()), "wip-cap", FULL)

    def test_held(self):
        self._once(_Board(_held()), "held")

    def test_epic_not_active(self):
        self._once(_Board(_card(epic_lane="Planning")), "epic-not-active")

    def test_epic_gate(self):
        self._once(_Board(_card(epic_lane="In Progress"), epic_gate=True), "epic-gate")

    def test_epic_cap_queued(self):
        self._once(_Board(_card(epic_lane="In Progress"), cap="queue"), "epic-cap-queued")

    def test_the_held_comment_names_the_label_as_what_holds_and_releases_it(self):
        board = _Board(_held())
        board.promote()
        (text,) = board.waits_on()
        waits, releases = text.split("What releases it:")
        assert f"`{LABEL}`" in waits and "removing" in releases and f"`{LABEL}`" in releases

    def test_the_epic_comment_names_the_epic_and_its_lane(self):
        board = _Board(_card(epic_lane="Planning"))
        board.promote()
        (text,) = board.waits_on()
        assert f"epic {EPIC}, which is in Planning" in text.splitlines()[0]

    def test_the_wip_comment_names_the_count(self):
        board = _Board(_card())
        board.promote(FULL)
        (text,) = board.waits_on()
        assert f"{FULL} of {reconcile.MAX_WIP}" in text

    def test_each_reason_is_its_own_key(self):
        """Told `wip-cap` under the spent budget, then `epic-gate` on the first
        pass with room — once each."""
        board = _Board(_card(epic_lane="In Progress"), epic_gate=True)
        board.promote(FULL)
        board.promote(0)
        board.promote(0)
        waits = board.waits_on()
        assert [w.split(" — ")[0] for w in waits] == [
            f"{OPENER} wip-cap", f"{OPENER} epic-gate"]


class TestWhichReasonAtTheBudgetExit:
    def test_three_fleet_cards_each_get_their_own(self):
        held = _card("DRE-7001", labels=("repo:bureau-pipeline", LABEL))
        epic = _card("DRE-7002", epic_lane="Planning")
        plain = _card("DRE-7003")
        board = _Board(held, epic, plain)
        assert board.promote(FULL) == 0
        for ident, reason in (("DRE-7001", "held"), ("DRE-7002", "epic-not-active"),
                              ("DRE-7003", "wip-cap")):
            waits = board.waits_on(ident)
            assert len(waits) == 1, (ident, board.comments_on(ident))
            assert _opens(waits[0], reason, ident), waits[0]

    def test_a_held_card_under_an_inactive_epic_is_told_held(self):
        board = _Board(_held(epic_lane="Planning"))
        board.promote(FULL)
        (text,) = board.waits_on()
        assert _opens(text, "held")

    def test_a_stamped_hold_gets_nothing_there(self):
        board = _Board(_held(bodies=(DEAD_RUN_STAMP,)))
        board.promote(FULL)
        assert board.writes == []

    def test_a_card_with_no_verdict_is_not_told_wip_cap(self):
        board = _Board(_card(verdict=None))
        board.promote(FULL)
        assert board.waits_on() == []

    def test_a_parked_card_is_not_told_wip_cap(self):
        board = _Board(_card(verdict=PARKED))
        board.promote(FULL)
        assert board.waits_on() == []


# --------------------------------------------------------------------------- #
# 3. nothing said where nothing is owed                                       #
# --------------------------------------------------------------------------- #


OPEN_BLOCKER = {"DRE-6426": "Done", "DRE-6500": "In Progress"}


class TestABlockerStillOpenIsSaidNothing:
    @pytest.mark.parametrize("make, active, kw", [
        (_card, FULL, {}),
        (_held, FULL, {}),
        (_held, 0, {}),
        (_card, 0, {"epic_lane": "Planning"}),
        (_card, FULL, {"epic_lane": "Planning"}),
    ], ids=["budget", "budget-held", "hold", "epic-not-active", "budget-epic"])
    def test_no_comment(self, make, active, kw):
        board = _Board(make(blockers=OPEN_BLOCKER, **kw))
        board.promote(active)
        assert board.waits_on() == []

    def test_no_comment_at_the_epic_gate(self):
        board = _Board(_card(blockers=OPEN_BLOCKER, epic_lane="In Progress"), epic_gate=True)
        board.promote()
        assert board.waits_on() == []

    def test_no_comment_at_the_cap_queue(self):
        board = _Board(_card(blockers=OPEN_BLOCKER, epic_lane="In Progress"), cap="queue")
        board.promote()
        assert board.waits_on() == []


class TestNothingIsPosted:
    @pytest.mark.parametrize("active", [0, FULL])
    def test_another_repo_s_card(self, active):
        board = _Board(_card(labels=("repo:agent-bureau", LABEL)))
        board.promote(active)
        assert board.writes == []

    @pytest.mark.parametrize("active", [0, FULL])
    def test_an_epic(self, active):
        board = _Board(_held(title="[EPIC] Rotate every secret"))
        board.promote(active)
        assert board.waits_on() == []

    @pytest.mark.parametrize("make, active, kw", [
        (_card, FULL, {}),
        (_held, 0, {}),
        (_card, 0, {"epic_lane": "Planning"}),
    ], ids=["budget", "hold", "epic-not-active"])
    def test_relations_not_read_to_the_end(self, make, active, kw):
        board = _Board(make(relations_unread=True, **kw))
        board.promote(active)
        assert board.waits_on() == []

    @pytest.mark.parametrize("verdict, tag", [
        (None, routing_verdict.NO_VERDICT_TAG),
        (PARKED, routing_verdict.NOT_FLEET_TAG),
    ])
    def test_a_card_its_routing_refusal_already_speaks_for(self, verdict, tag):
        board = _Board(_card(verdict=verdict))
        board.promote()
        first = board.comments_on()
        assert len(first) == 1 and tag in first[0]
        board.promote(FULL)
        board.promote()
        assert board.comments_on() == first

    def test_a_plan_critic_refusal_inside_its_grace(self, capsys):
        critic = (plan_critic.POST_UNREAD_TAG,
                  f"🚦 {plan_critic.POST_UNREAD_TAG}: the second critic has not read the plan")
        board = _Board(_card(epic_lane="In Progress"), critic=critic, critic_overdue=False)
        board.promote()
        assert board.writes == []
        assert f"promotion: {CARD} is not being promoted — 🚦 {plan_critic.POST_UNREAD_TAG}" \
            in capsys.readouterr().out

    def test_a_stamped_hold_the_sweep_stands_down_for(self, capsys):
        board = _Board(_held(bodies=(DEAD_RUN_STAMP,)))
        board.promote()
        assert board.writes == []
        assert (f"promotion: {CARD} is held for a human ('{LABEL}' label, "
                "reason=dead-run-cap) — never auto-promoted; skipping") in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# 4. a person's card under a hand-applied hold: the gates after the hold line #
# --------------------------------------------------------------------------- #


def _operator_held(**kw):
    return _held(verdict=OPERATOR, **kw)


class TestAPersonsHeldCardMeetsEveryLaterGate:
    def test_an_epic_in_planning_holds_it_and_says_so(self):
        board = _Board(_operator_held(epic_lane="Planning"))
        assert board.promote() == 0
        assert not [w for w in board.on() if w[0] == "advance"]
        (text,) = board.waits_on()
        assert _opens(text, "epic-not-active")

    def test_an_undeclared_prose_claim_gets_the_existing_refusal(self):
        board = _Board(_operator_held(
            epic_lane="In Progress", description="Rotate it.\n\nBlocked by: DRE-9999\n"))
        assert board.promote() == 0
        assert ("advance", "Hand-work") not in board.on()
        comments = board.comments_on()
        assert len(comments) == 1 and prose_blockers.CARD_TAG in comments[0]
        assert board.waits_on() == []

    def test_an_open_agent_blocker_holds_it_and_nothing_more_is_said(self):
        card = _operator_held(bodies=(f"{reconcile.BLOCKER_MARKER}: the deploy key is missing",))
        assert reconcile.open_agent_blocker(card) is not None
        board = _Board(card)
        assert board.promote() == 0
        assert board.writes == []


# --------------------------------------------------------------------------- #
# 5. the registry declares it                                                 #
# --------------------------------------------------------------------------- #


class TestTheRegistryDeclaresIt:
    def _row(self):
        rows = [d for d in check_act_receipts.declarations()
                if d.get("opener") == f"{promotion_wait.OPENER}:"]
        assert len(rows) == 1, rows
        return rows[0]

    def test_a_not_an_act_report_names_the_opener_and_the_reasons(self):
        row = self._row()
        assert row["kind"] == "not-an-act"
        assert tuple(row["reasons"]) == promotion_wait.REASONS
        assert row["means"].strip() and row["why"].strip()

    def test_the_row_matches_exactly_one_site(self):
        row = self._row()
        hits = [s for s in check_act_receipts.sites() if check_act_receipts._matches(row, s)]
        assert len(hits) == 1, hits

    def test_both_checks_pass(self):
        assert pipeline_act.problems() == []
        assert check_act_receipts.problems() == []

    def test_no_act_tag_is_inside_the_opener(self):
        for name in pipeline_act.acts():
            assert pipeline_act.tag(name) not in promotion_wait.OPENER
