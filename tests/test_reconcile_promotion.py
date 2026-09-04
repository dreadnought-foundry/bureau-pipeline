"""The pair the planner wrote is the pair the sweep refuses (DRE-3039).

This is the scenario test for the seam between plan time and build time. Every
half of it was already green on its own and the whole was broken:

  * `proof_and_demo.py check` COMPUTED a verdict for the proof card and the
    demo card, printed a one-line summary, and stamped nothing.
  * `routing_verdict.promotion_refusal()` returns None for a child carrying no
    verdict — "a CHILD with NO verdict promotes exactly as it did before".
  * the relay reads only `agent:planner` on a Todo entry, and `agent-task.yml`
    has no label guard.

So the moment the epic's build children reached Done, the sweep promoted
`PROOF: …` — `agent:engineer`, a `Files:` line naming the document — and an
engineer agent wrote the proof of its own siblings' work. DRE-2746 says a proof
the fleet can close by merging its own code is not a proof.

WHAT THIS PINS: the verdict `proof_and_demo` computes, written as the comment
`routing_verdict` reads, holds the pair in Backlog when every card it is
blocked by is Done — and the refusal the sweep prints NAMES that verdict. The
plan-time comment is built by calling `proof_and_demo.stamps()`, never
hand-written here: delete the stamp and this test goes red, which is the whole
point of it.

The second half of this file is the same seam one gate earlier (DRE-3059): the
sweep releases an epic's children only once the SECOND critic has passed the
plan, read out of the `plan-critic: stage=post` marker the critic already
writes. Same shape, same failure — a rule with two readers and only one of them
gated.

Run: cd bureau-pipeline && python3 -m pytest tests/test_reconcile_promotion.py -v
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

import lane_contract  # noqa: E402
import plan_critic  # noqa: E402
import proof_and_demo  # noqa: E402
import reconcile  # noqa: E402
import routing_verdict  # noqa: E402

EPIC = "DRE-3019"
GREEN_LIGHT = "2026-08-01T00:00:00.000Z"
CREATED = "2026-07-01T00:00:00.000Z"  # before the green light: not a mid-epic addition

WORK = ("DRE-3026", "DRE-3027", "DRE-3028")
PROOF = "DRE-3031"
DEMO = "DRE-3032"

PAIR_LABELS = ("repo:bureau-pipeline", "agent:ops", "initiative:bureau")

PROOF_BODY = (
    "Read the stamp on main and record what it said.\n\n"
    "## Acceptance criteria\n\n"
    "- [ ] the stamp is read against the live repo and quoted in the card\n"
)

DEMO_BODY = (
    "Show the CEO the repo saying when it was last exercised.\n\n"
    "## Acceptance criteria\n\n"
    "- [ ] the CEO is walked through the README stamp\n"
)


def _planner_output():
    """The epic's children as `linear_ops.py children-detail` hands them to the
    check — three build cards, then the two that close the epic."""
    work = [
        {
            "identifier": ident,
            "title": f"Build piece {n}",
            "body": "Add it.\n\n## Acceptance criteria\n\n- [ ] the file renders\n",
            "labels": ["repo:bureau-pipeline", "agent:engineer", "initiative:bureau"],
            "blocked_by": [],
        }
        for n, ident in enumerate(WORK, 1)
    ]
    return work + [
        {
            "identifier": PROOF,
            "title": "PROOF: the stamp on main was written by the script",
            "body": PROOF_BODY,
            "labels": list(PAIR_LABELS),
            "blocked_by": list(WORK),
        },
        {
            "identifier": DEMO,
            "title": "DEMO: show the CEO the demo repo",
            "body": DEMO_BODY,
            "labels": list(PAIR_LABELS),
            "blocked_by": list(WORK) + [PROOF],
        },
    ]


def _verdict_comments():
    """What plan time actually left on each card. Computed by the check, never
    written out here — the coupling under test IS that the sweep reads what the
    check wrote."""
    return {
        identifier: routing_verdict.verdict_comment(verdict, why)
        for identifier, verdict, why in proof_and_demo.stamps(_planner_output())
    }


def _backlog_card(record, comments):
    """One of the planner's cards as the SWEEP sees it: in Backlog, under an
    active epic, with its `blockedBy` relations resolved to Done."""
    description = record["body"]
    blockers = record["blocked_by"]
    if blockers:
        description += "\n\n**Blocked by:** " + ", ".join(blockers)
    return {
        "id": f"uuid-{record['identifier']}",
        "identifier": record["identifier"],
        "title": record["title"],
        "description": description,
        "createdAt": CREATED,
        "parent": {"identifier": EPIC, "state": {"name": "In Progress"}},
        "labels": {"nodes": [{"name": name} for name in record["labels"]]},
        "comments": {"nodes": [{"body": b} for b in comments]},
        "inverseRelations": {"nodes": [
            {"type": "blocks",
             "issue": {"identifier": b, "state": {"name": "Done"}}}
            for b in blockers
        ]},
    }


class _Board:
    """`reconcile.promote_ready` over a Backlog roster with every gate that is
    not under test held open: WIP has room, the epic is green-lit and active,
    its own blockers are clear, and every formal blocker reads Done.

    Copied in shape from tests/test_parentless_promotion.py — same sweep, same
    seams, so the two read the same way.
    """

    def __init__(self, *cards, green_light=GREEN_LIGHT, epic_thread=()):
        self.cards = list(cards)
        self.green_light = green_light
        # The epic's own comment thread, as `comment_records` reports it. The
        # second critic's release is read out of here (DRE-3059).
        self.epic_thread = list(epic_thread)
        self.advanced: list[tuple[str, str, str]] = []
        self.posted: list[tuple[str, str]] = []
        self.lanes = {c["identifier"]: ["Backlog"] for c in self.cards}

    def promote(self, active_count: int = 0) -> int:
        def advance(ident, to_state, from_states):
            self.advanced.append((ident, to_state, from_states))
            self.lanes.setdefault(ident, []).append(to_state)

        with patch.object(reconcile, "REPO_SLUG", "bureau-pipeline"), patch.object(
            reconcile, "backlog_children", return_value=self.cards
        ), patch.object(
            reconcile, "epic_blockers_unmet", return_value=False
        ), patch.object(
            reconcile.mid_epic, "last_green_light", return_value=self.green_light
        ), patch.object(
            reconcile.linear_ops, "comment_records", return_value=self.epic_thread
        ), patch.object(
            reconcile, "card_state", return_value="Done"
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
            return reconcile.promote_ready(active_count=active_count)

    def lane_of(self, identifier: str) -> str:
        return self.lanes[identifier][-1]

    def comments_on(self, identifier: str) -> list[str]:
        return [b for i, b in self.posted if i == identifier]


@pytest.fixture(autouse=True)
def _clear_write_failures():
    reconcile._write_failures.clear()
    yield
    reconcile._write_failures.clear()


def _pair_in_backlog():
    """The proof and demo cards, stamped at plan time, every sibling Done."""
    comments = _verdict_comments()
    records = {c["identifier"]: c for c in _planner_output()}
    return [
        _backlog_card(records[i], [comments[i]]) for i in (PROOF, DEMO)
    ]


class TestThePairIsNotPromoted:
    def test_the_build_children_being_done_does_not_release_the_pair(self):
        board = _Board(*_pair_in_backlog())
        assert board.promote() == 0
        assert board.lane_of(PROOF) == "Backlog"
        assert board.lane_of(DEMO) == "Backlog"
        assert board.advanced == []

    def test_the_refusal_names_the_verdict(self, capsys):
        board = _Board(*_pair_in_backlog())
        board.promote()
        out = capsys.readouterr().out
        for identifier in (PROOF, DEMO):
            assert identifier in out
            assert "OPERATOR" in out
        assert routing_verdict.NOT_FLEET_TAG in out

    def test_the_refusal_is_surfaced_on_the_card_itself(self):
        """A refusal nobody can see is the silent-accretion problem wearing a
        different hat — the sweep posts it once, naming where the card goes."""
        board = _Board(*_pair_in_backlog())
        board.promote()
        for identifier in (PROOF, DEMO):
            posted = board.comments_on(identifier)
            assert len(posted) == 1, posted
            assert "OPERATOR" in posted[0]
            assert "operator" in posted[0]

    def test_without_the_plan_time_stamp_the_pair_promotes(self):
        """The mutation this test exists for. Same cards, same Done siblings,
        no verdict comment — and the sweep hands both to the fleet, which is
        exactly what was happening before this card."""
        records = {c["identifier"]: c for c in _planner_output()}
        board = _Board(*[_backlog_card(records[i], []) for i in (PROOF, DEMO)])
        assert board.promote() == 2
        assert board.lane_of(PROOF) == "Todo"
        assert board.lane_of(DEMO) == "Todo"


class TestTheGateStillPromotesWork:
    def test_a_fleet_sibling_beside_the_pair_still_goes_to_todo(self):
        """The refusal is about the verdict on the card, not about the sweep
        having stopped: a build card in the same roster promotes."""
        work = {c["identifier"]: c for c in _planner_output()}[WORK[0]]
        fleet = routing_verdict.verdict_comment(
            "FLEET", "the acceptance criteria are unit-testable")
        board = _Board(_backlog_card(work, [fleet]), *_pair_in_backlog())
        assert board.promote() == 1
        assert board.lane_of(WORK[0]) == "Todo"
        assert board.lane_of(PROOF) == "Backlog"
        assert board.lane_of(DEMO) == "Backlog"


# --------------------------------------------------------------------------- #
# DRE-3059 — the second critic releases the children, and nothing else does    #
# --------------------------------------------------------------------------- #

APPROVED = "2026-09-10T12:04:00.000Z"  # after plan_critic.GATED_FROM: gated
FLEET_VERDICT = routing_verdict.verdict_comment(
    "FLEET", "the acceptance criteria are unit-testable")


def _pipeline_comment(body: str) -> dict:
    """A comment on the EPIC that the pipeline itself wrote, as
    `linear_ops.comment_records` reports it. The round markers are this gate's
    credential, so authorship travels with them (DRE-2721)."""
    return {"body": body, "authored_by_pipeline": True}


def _epic_thread(*markers) -> list[dict]:
    return [_pipeline_comment(plan_critic.cycle_marker(EPIC))] + [
        _pipeline_comment(m) for m in markers
    ]


def _work_in_backlog(*, first_blocker_done=True):
    """The epic's three build cards in Backlog, each carrying a FLEET verdict.

    `first_blocker_done=False` leaves the second card's blocker open, so the
    roster has a dependency ORDER to be promoted in rather than a flat set.
    """
    records = {c["identifier"]: c for c in _planner_output()}
    first = _backlog_card(records[WORK[0]], [FLEET_VERDICT])
    second = _backlog_card(records[WORK[1]], [FLEET_VERDICT])
    second["description"] += "\n\n**Blocked by:** " + WORK[0]
    second["inverseRelations"]["nodes"].append({
        "type": "blocks",
        "issue": {"identifier": WORK[0],
                  "state": {"name": "Done" if first_blocker_done else "Backlog"}},
    })
    return [first, second]


class TestAnUnreadPlanReleasesNothing:
    """The observed incident, as a test.

    2026-09-03 20:04:07 PT, reconcile run 33831833887 on agent-bureau-demo:
    DRE-3026 and DRE-3027 were promoted eighty-two seconds after the CEO
    approved their epic, with no second-critic verdict on it, because none had
    run. `promote_ready()` asked whether the parent epic was active and never
    whether the plan had been read since it was approved.
    """

    def test_children_stay_in_backlog_with_no_post_critic_marker(self):
        board = _Board(*_work_in_backlog(),
                       green_light=APPROVED, epic_thread=_epic_thread())
        assert board.promote() == 0
        assert board.lane_of(WORK[0]) == "Backlog"
        assert board.lane_of(WORK[1]) == "Backlog"
        assert board.advanced == []

    def test_the_refusal_names_the_epic_and_the_missing_critic(self, capsys):
        board = _Board(*_work_in_backlog(),
                       green_light=APPROVED, epic_thread=_epic_thread())
        board.promote()
        out = capsys.readouterr().out
        assert WORK[0] in out
        assert EPIC in out
        assert "second critic has not passed it" in out
        assert "holding" in out

    def test_the_refusal_is_surfaced_on_the_card_once(self):
        board = _Board(*_work_in_backlog(),
                       green_light=APPROVED, epic_thread=_epic_thread())
        board.promote()
        board.promote()
        posted = board.comments_on(WORK[0])
        assert len(posted) == 1, posted
        assert plan_critic.POST_UNREAD_TAG in posted[0]

    def test_a_pre_stage_pass_is_not_the_release(self):
        """The critic that ran before the CEO read the plan is not the one
        that says the specification is buildable."""
        board = _Board(
            *_work_in_backlog(), green_light=APPROVED,
            epic_thread=_epic_thread(
                plan_critic.marker(plan_critic.STAGE_PRE, 1, plan_critic.PASS)),
        )
        assert board.promote() == 0


class TestAPassReleasesTheChildrenInOrder:
    def test_a_post_pass_promotes_the_unblocked_child_and_holds_the_blocked_one(self):
        """AC2 — the same epic, after `stage=post result=PASS`: the children
        promote, and the dependency gate still decides the ORDER."""
        board = _Board(
            *_work_in_backlog(first_blocker_done=False), green_light=APPROVED,
            epic_thread=_epic_thread(
                plan_critic.marker(plan_critic.STAGE_POST, 1, plan_critic.PASS)),
        )
        assert board.promote() == 1
        assert board.lane_of(WORK[0]) == "Todo"
        assert board.lane_of(WORK[1]) == "Backlog"

    def test_once_the_blocker_is_done_the_next_child_follows(self):
        board = _Board(
            *_work_in_backlog(), green_light=APPROVED,
            epic_thread=_epic_thread(
                plan_critic.marker(plan_critic.STAGE_POST, 1, plan_critic.PASS)),
        )
        assert board.promote() == 2
        assert board.lane_of(WORK[0]) == "Todo"
        assert board.lane_of(WORK[1]) == "Todo"


class TestAFailHoldsWithTheCriticsReason:
    REASON = "DRE-3028 migrates a table and no card runs the migration"

    def _board(self, result):
        return _Board(
            *_work_in_backlog(), green_light=APPROVED,
            epic_thread=_epic_thread(
                plan_critic.marker(plan_critic.STAGE_POST, 1, result, self.REASON)),
        )

    def test_a_fail_holds_the_children(self):
        board = self._board("FAIL")
        assert board.promote() == 0
        assert board.lane_of(WORK[0]) == "Backlog"

    def test_the_critics_reason_is_on_the_refusal(self):
        board = self._board("FAIL")
        board.promote()
        posted = board.comments_on(WORK[0])
        assert len(posted) == 1, posted
        assert self.REASON in posted[0]
        assert plan_critic.POST_SENT_BACK_TAG in posted[0]

    def test_a_send_back_holds_the_same_way(self):
        board = self._board(plan_critic.SEND_BACK)
        assert board.promote() == 0
        assert self.REASON in board.comments_on(WORK[0])[0]

    def test_the_bound_still_releases_after_two_failed_rounds(self):
        """Two failed rounds and the plan proceeds regardless — the gate must
        not turn DRE-2721's bound into an unbounded hold."""
        board = _Board(
            *_work_in_backlog(), green_light=APPROVED,
            epic_thread=_epic_thread(
                plan_critic.marker(plan_critic.STAGE_POST, 1,
                                   plan_critic.SEND_BACK, "first gap"),
                plan_critic.marker(plan_critic.STAGE_POST, 2,
                                   plan_critic.SEND_BACK, self.REASON),
            ),
        )
        assert board.promote() == 2


class TestTheFleetDoesNotFreezeOnTheDayThisMerges:
    def test_an_epic_approved_before_the_cutoff_promotes_with_no_marker(self):
        """`GREEN_LIGHT` is 2026-08-01, before `plan_critic.GATED_FROM`. The
        epics already in flight when this lands have no marker to find, and
        never will."""
        board = _Board(*_work_in_backlog(), epic_thread=_epic_thread())
        assert board.promote() == 2

    def test_an_unreadable_epic_thread_abstains_rather_than_freezing(self):
        """A Linear read that failed is not a critic that refused. Refusing
        every child of every epic on an unreadable thread would freeze the
        board (standards/console-honesty.md rule 1)."""
        board = _Board(*_work_in_backlog(), green_light=APPROVED)
        with patch.object(reconcile.linear_ops, "comment_records",
                          side_effect=reconcile.linear_ops.LinearError("boom")):
            assert board.promote() == 2

    def test_the_epic_thread_is_read_once_per_epic_per_sweep(self):
        """Six children of one epic must not buy six reads of the same thread
        — the shape `green_light` and `epic_gate` already use (DRE-1772)."""
        board = _Board(
            *_work_in_backlog(), green_light=APPROVED,
            epic_thread=_epic_thread(
                plan_critic.marker(plan_critic.STAGE_POST, 1, plan_critic.PASS)),
        )
        with patch.object(reconcile.linear_ops, "comment_records",
                          return_value=board.epic_thread) as reader:
            board.promote()
        assert reader.call_count == 1, reader.call_args_list


class TestThereIsExactlyOnePromoter:
    """AC4 — the lane contract's Todo writers clause names ONE promoter.

    Two promoters for one event is the DRE-3038/DRE-3044 shape: two readers of
    one rule, only one of them gated. `plan.yml`'s activate route does not
    promote — it runs the sweep's own promoter (`reconcile.py --promote-only`)
    the second the critic passes, so the fast path and the cron sweep are the
    same gated code and cannot disagree about the same epic.
    """

    WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"

    def test_the_todo_writers_clause_names_the_sweep_as_the_promoter(self):
        text = lane_contract.lane("Todo")["clauses"]["writers"]["text"]
        assert "promote_ready" in text, text
        assert "DRE-3059" in text, text

    def test_the_route_is_not_a_second_writer_of_todo(self):
        who = lane_contract.lane_writers("Todo")
        assert "reconcile.py" in who
        assert "plan.yml" not in who, who

    def test_every_workflow_step_that_promotes_runs_the_one_promoter(self):
        """The mechanical half: a second promoter would have to appear here as
        a step that advances a card into Todo by some other means."""
        promoting = [
            (path.name, line.strip())
            for path in sorted(self.WORKFLOWS.glob("*.yml"))
            for line in path.read_text(encoding="utf-8").splitlines()
            if "--promote-only" in line and not line.strip().startswith("#")
        ]
        assert promoting, "nothing promotes at all — the grep has gone stale"
        for name, line in promoting:
            assert "reconcile.py" in line, f"{name}: {line}"
