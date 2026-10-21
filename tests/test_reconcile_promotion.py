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
`routing_verdict` reads, keeps the fleet off the pair when every card it is
blocked by is Done. The plan-time comment is built by calling
`proof_and_demo.stamps()`, never hand-written here: delete the stamp and this
test goes red, which is the whole point of it.

WHERE THE PAIR ENDS UP CHANGED IN DRE-3385, AND THE PROTECTION DID NOT. It used
to be held in `Backlog`, because OPERATOR named `Todo` as its destination and
nothing performed the move. The sweep performs it now — and stamps the verdict's
own marks, `hand-built` and `no-code`, before the card lands, which is what
keeps a build run off it (the relay refuses to dispatch an engineer at a
`hand-built` card entering Todo, DRE-3341). So the assertion moved from "the
card does not move" to "the card arrives marked, and nothing was dispatched".
The fact under test is the same one: an agent must not write the proof of its
own siblings' work.

DRE-3669 halved the SHAPE — the planner files one closing child now — and left
this seam alone. The fixture keeps both cards on purpose: it is the legacy epic
the check reads past, and the protection that holds a demo card in Backlog has
to survive the rule that stopped requiring one.

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
    # The closing line the gate requires since DRE-3669. Read off the module,
    # never retyped: a plan whose proof card does not carry it is bounced, so
    # it would never reach the sweep this file is about.
    f"{proof_and_demo.CLOSING_LINE}\n\n"
    "## Acceptance criteria\n\n"
    "- [ ] the stamp is read against the live repo and quoted in the card\n"
    "- [ ] the CEO closes this card after reading the record\n"
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

    def __init__(self, *cards, green_light=GREEN_LIGHT, epic_thread=(),
                 thread_error=None, held_minutes=None):
        self.cards = list(cards)
        self.green_light = green_light
        # How long the epic has carried its approval, when a test needs the
        # sweep's own clock to be a fixture rather than the wall clock.
        self.held_minutes = held_minutes
        # The epic's own comment thread, as `comment_records` reports it. The
        # second critic's release is read out of here (DRE-3059).
        self.epic_thread = list(epic_thread)
        self.thread_error = thread_error
        self.thread_reads: list[str] = []
        self.advanced: list[tuple[str, str, str]] = []
        self.posted: list[tuple[str, str]] = []
        self.labelled: list[tuple[str, str]] = []
        self.lanes = {c["identifier"]: ["Backlog"] for c in self.cards}

    def promote(self, active_count: int = 0) -> int:
        def advance(ident, to_state, from_states):
            self.advanced.append((ident, to_state, from_states))
            self.lanes.setdefault(ident, []).append(to_state)

        def read_thread(epic):
            self.thread_reads.append(epic)
            if self.thread_error is not None:
                raise self.thread_error
            return self.epic_thread

        aged = (patch.object(reconcile, "age_minutes",
                             return_value=self.held_minutes)
                if self.held_minutes is not None
                else patch.object(reconcile, "REPO_SLUG", "bureau-pipeline"))

        with aged, patch.object(reconcile, "REPO_SLUG", "bureau-pipeline"), patch.object(
            reconcile, "backlog_children", return_value=self.cards
        ), patch.object(
            reconcile, "epic_blockers_unmet", return_value=False
        ), patch.object(
            reconcile.mid_epic, "last_green_light", return_value=self.green_light
        ), patch.object(
            reconcile.linear_ops, "comment_records", side_effect=read_thread
        ), patch.object(
            reconcile, "card_state", return_value="Done"
        ), patch.object(
            # A hand-built card's marks go on before the move (DRE-3385) — and
            # on this pair they are the whole protection.
            reconcile.linear_ops, "add_label",
            side_effect=lambda i, label: self.labelled.append((i, label)),
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

    def labels_on(self, identifier: str) -> list[str]:
        return [l for i, l in self.labelled if i == identifier]


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


class TestThePairIsNotHandedToTheFleet:
    """WHAT CHANGED IN DRE-3385, and what did not.

    This class used to assert the pair stayed in `Backlog`. That was never the
    goal — `Backlog` was where the verdict's destination said `Todo` and nothing
    performed the move, and leaving a proof card there is how 33 of 40 Backlog
    cards came to be proof and operator cards on 2026-09-08. The sweep carries
    the pair to `Todo` now.

    The protection DRE-3039 bought is unchanged and is asserted below, one layer
    down: the pair arrives carrying OPERATOR's own marks — `hand-built` and
    `no-code` — which is what keeps a build run off it (the relay refuses to
    dispatch an engineer at a `hand-built` card entering Todo, DRE-3341), and
    the receipt says in words that nothing was dispatched. Strip the plan-time
    stamp and no marks are applied at all, which is the mutation this class
    exists for.
    """

    def test_the_pair_is_promoted_to_its_destination_when_its_siblings_are_done(self):
        # Hand-work since DRE-5322: a person's lane, read off the vocabulary.
        board = _Board(*_pair_in_backlog())
        assert board.promote() == 2
        assert board.lane_of(PROOF) == routing_verdict.destination("OPERATOR") == "Hand-work"
        assert board.lane_of(DEMO) == routing_verdict.destination("OPERATOR")

    def test_it_arrives_carrying_the_marks_that_keep_the_fleet_off_it(self):
        """The whole of the protection. An OPERATOR card in Todo WITHOUT these
        is a card the relay dispatches an engineer at — and the thing it would
        build is the proof of its own siblings' work."""
        board = _Board(*_pair_in_backlog())
        board.promote()
        # The proof card receives `card_marks` (DRE-6225): the proof run takes
        # it, not the operator, so `no-code` alone — a person's card to
        # `reconcile.hand_built` because it is a proof, never because of
        # `no-code` alone. The legacy demo card receives every mark.
        titles = {c["identifier"]: c["title"] for c in _planner_output()}
        for identifier in (PROOF, DEMO):
            assert board.labels_on(identifier) == list(
                routing_verdict.card_marks("OPERATOR", titles[identifier]))
        assert board.labels_on(PROOF) == ["no-code"]
        assert board.labels_on(DEMO) == list(routing_verdict.marks("OPERATOR"))

    def test_the_receipt_names_the_verdict_and_says_nothing_ran(self, capsys):
        board = _Board(*_pair_in_backlog())
        board.promote()
        for identifier in (PROOF, DEMO):
            posted = board.comments_on(identifier)
            assert len(posted) == 1, posted
            assert "OPERATOR" in posted[0]
            assert "operator" in posted[0]
            assert "nothing was dispatched" in posted[0]
        assert "hand-built" in capsys.readouterr().out

    def test_the_hand_built_promotions_do_not_spend_the_wip_budget(self, capsys):
        """Nothing is dispatched for either card, so neither takes a slot."""
        board = _Board(*_pair_in_backlog())
        board.promote(active_count=reconcile.MAX_WIP - 1)
        assert board.lane_of(PROOF) == routing_verdict.destination("OPERATOR")
        assert board.lane_of(DEMO) == routing_verdict.destination("OPERATOR")
        assert "budget spent" not in capsys.readouterr().out

    def test_without_the_plan_time_stamp_nothing_is_marked_at_all(self):
        """The mutation this test exists for. Same cards, same Done siblings,
        no verdict comment — and the card that would have been marked is not
        marked, which before this card meant it was handed to the fleet.

        It is refused outright now (DRE-3385 closed the verdictless-child gap),
        so the pair is held rather than dispatched either way — but the fact
        under test is the one the plan-time stamp is responsible for: delete
        `proof_and_demo.stamps()` and NOTHING puts `hand-built` on these cards.
        """
        records = {c["identifier"]: c for c in _planner_output()}
        board = _Board(*[_backlog_card(records[i], []) for i in (PROOF, DEMO)])
        assert board.promote() == 0
        assert board.labelled == []
        for identifier in (PROOF, DEMO):
            assert board.lane_of(identifier) == "Backlog"
            assert any(routing_verdict.NO_VERDICT_TAG in b
                       for b in board.comments_on(identifier))


class TestTheGateStillPromotesWork:
    def test_a_fleet_sibling_beside_the_pair_goes_to_in_progress_unmarked(self):
        """One sweep, two destinations for the work: the build card is handed to
        an agent in In Progress (DRE-7120), the pair is handed to a person in
        Hand-work (DRE-5322), and the marks still say which is which."""
        work = {c["identifier"]: c for c in _planner_output()}[WORK[0]]
        fleet = routing_verdict.verdict_comment(
            "FLEET", "the acceptance criteria are unit-testable")
        board = _Board(_backlog_card(work, [fleet]), *_pair_in_backlog())
        assert board.promote() == 3
        assert board.lane_of(WORK[0]) == "In Progress"
        assert board.labels_on(WORK[0]) == []
        for identifier in (PROOF, DEMO):
            assert board.lane_of(identifier) == routing_verdict.destination("OPERATOR")
            # A person's card, not necessarily `hand-built`: the proof card
            # receives `no-code` alone, and reads as a person's because its
            # title is a proof's (DRE-6225).
            title = {c["identifier"]: c["title"] for c in _planner_output()}[identifier]
            assert reconcile.hand_built({"title": title, "labels": {"nodes": [
                {"name": n} for n in board.labels_on(identifier)]}})


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

    def test_a_review_still_in_flight_is_logged_and_not_stamped_on_the_children(self):
        """The activate route posts its round record minutes after the CEO's
        approval. A sweep landing in that gap holds the children — that part is
        immediate — but must not stamp "the second critic has not passed it" on
        every child of a perfectly healthy epic and then promote them on the
        next sweep with the refusal left standing."""
        board = _Board(*_work_in_backlog(), green_light=APPROVED,
                       epic_thread=_epic_thread(), held_minutes=2)
        assert board.promote() == 0
        assert board.comments_on(WORK[0]) == []

    def test_a_hold_past_the_grace_window_is_surfaced_on_the_card_once(self):
        """DRE-3058's case: the route never ran at all, so nothing is coming.
        A refusal only the sweep log carries is a refusal nobody reads."""
        board = _Board(
            *_work_in_backlog(), green_light=APPROVED, epic_thread=_epic_thread(),
            held_minutes=reconcile.POST_CRITIC_GRACE_MINUTES + 1,
        )
        board.promote()
        board.promote()
        posted = board.comments_on(WORK[0])
        assert len(posted) == 1, posted
        assert plan_critic.POST_UNREAD_TAG in posted[0]
        assert EPIC in posted[0]

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
        assert board.lane_of(WORK[0]) == "In Progress"
        assert board.lane_of(WORK[1]) == "Backlog"

    def test_once_the_blocker_is_done_the_next_child_follows(self):
        board = _Board(
            *_work_in_backlog(), green_light=APPROVED,
            epic_thread=_epic_thread(
                plan_critic.marker(plan_critic.STAGE_POST, 1, plan_critic.PASS)),
        )
        assert board.promote() == 2
        assert board.lane_of(WORK[0]) == "In Progress"
        assert board.lane_of(WORK[1]) == "In Progress"


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

    def test_the_bound_parks_after_two_failed_rounds(self):
        """DRE-3088: two failed rounds at the second critic and the children
        stay put — the workflow parks the epic in Green Light with
        needs-human, and this gate must agree with it, or a cron sweep would
        promote the children of an epic the route just parked."""
        board = _Board(
            *_work_in_backlog(), green_light=APPROVED,
            epic_thread=_epic_thread(
                plan_critic.marker(plan_critic.STAGE_POST, 1,
                                   plan_critic.SEND_BACK, "first gap"),
                plan_critic.marker(plan_critic.STAGE_POST, 2,
                                   plan_critic.SEND_BACK, self.REASON),
            ),
        )
        assert board.promote() == 0
        posted = board.comments_on(WORK[0])
        assert self.REASON in posted[0]
        assert "first gap" in posted[0]


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
        board = _Board(*_work_in_backlog(), green_light=APPROVED,
                       thread_error=reconcile.linear_ops.LinearError("boom"))
        assert board.promote() == 2

    def test_the_epic_thread_is_read_once_per_epic_per_sweep(self):
        """Two children of one epic must not buy two reads of the same thread
        — the shape `green_light` and `epic_gate` already use (DRE-1772)."""
        board = _Board(
            *_work_in_backlog(), green_light=APPROVED,
            epic_thread=_epic_thread(
                plan_critic.marker(plan_critic.STAGE_POST, 1, plan_critic.PASS)),
        )
        assert board.promote() == 2
        assert board.thread_reads == [EPIC], board.thread_reads


class TestThereIsExactlyOnePromoter:
    """AC4 — the lane contract names ONE promoter into FLEET's lane (Todo until
    the flip, In Progress since DRE-7120).

    Two promoters for one event is the DRE-3038/DRE-3044 shape: two readers of
    one rule, only one of them gated. `plan.yml`'s activate route does not
    promote — it runs the sweep's own promoter (`reconcile.py --promote-only`)
    the second the critic passes, so the fast path and the cron sweep are the
    same gated code and cannot disagree about the same epic.
    """

    WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"

    def test_the_fleet_lanes_writers_clause_names_the_sweep_as_the_promoter(self):
        lane = routing_verdict.destination("FLEET")
        text = lane_contract.lane(lane)["clauses"]["writers"]["text"]
        assert "The sweep, which promotes a FLEET card here" in text, text
        # The gate that promotion passes is the queue's exit clause's to state.
        queue = lane_contract.lane(lane_contract.QUEUE_LANE)
        exit_text = queue["clauses"]["exit"]["text"]
        assert f"promotes a FLEET card to {lane}" in exit_text, exit_text
        assert "DRE-3059" in exit_text, exit_text
        # The old start lane retired (DRE-7129) and left the contract
        # (DRE-7660): no entry is left to permit a writer.
        for status in ("live", "retiring"):
            assert "To" + "do" not in lane_contract.lane_names(status=status)
        assert lane_contract.START_LANES == (lane,)

    def test_the_route_is_not_a_second_promoter(self):
        lane = routing_verdict.destination("FLEET")
        assert "reconcile.py" in lane_contract.lane_writers(lane)
        # plan.yml writes In Progress for one card only — the roll-up parent it
        # places there (DRE-4717) — and never promotes a FLEET card into it.
        text = lane_contract.lane(lane)["clauses"]["writers"]["text"]
        naming = [s for s in text.split(". ") if "plan.yml" in s]
        assert naming, text
        assert all("roll-up" in s and "FLEET" not in s for s in naming), naming

    def test_every_workflow_step_that_promotes_runs_the_one_promoter(self):
        """The mechanical half: a second promoter would have to appear here as
        a step that advances a card into FLEET's lane by some other means."""
        promoting = [
            (path.name, line.strip())
            for path in sorted(self.WORKFLOWS.glob("*.yml"))
            for line in path.read_text(encoding="utf-8").splitlines()
            if "--promote-only" in line and not line.strip().startswith("#")
        ]
        assert promoting, "nothing promotes at all — the grep has gone stale"
        for name, line in promoting:
            assert "reconcile.py" in line, f"{name}: {line}"


class TestTheRefusalSpeaksAtTheRightMoment:
    """When the sweep POSTS the refusal, as opposed to when it holds.

    The hold is immediate and needs no window — a plan nobody has read is a
    plan nobody has read, and it prints on every sweep from the first one. The
    window is only about noise: an epic whose second critic is still running
    is not an epic anyone needs told about (DRE-3059).
    """

    def test_a_send_back_speaks_at_once(self):
        """The critic already decided. Nothing is in flight to wait for."""
        assert reconcile.post_critic_hold_is_overdue(
            plan_critic.POST_SENT_BACK_TAG, "2026-09-10T12:04:00.000Z") is True

    def test_an_unread_plan_waits_out_the_grace_window(self):
        board = _Board(*_work_in_backlog(), green_light=APPROVED,
                       epic_thread=_epic_thread(),
                       held_minutes=reconcile.POST_CRITIC_GRACE_MINUTES - 1)
        assert board.promote() == 0
        assert board.comments_on(WORK[0]) == []

    def test_an_unreadable_green_light_does_not_silence_the_refusal(self):
        """Unknown must never be read as `still in flight` — that is the one
        direction that turns a quiet window into a permanent silence."""
        assert reconcile.post_critic_hold_is_overdue(
            plan_critic.POST_UNREAD_TAG, None) is True

    def test_the_hold_itself_is_printed_from_the_very_first_sweep(self, capsys):
        board = _Board(*_work_in_backlog(), green_light=APPROVED,
                       epic_thread=_epic_thread(), held_minutes=1)
        board.promote()
        assert "second critic has not passed it" in capsys.readouterr().out


class TestADeadReviewHoldsAndSaysSo:
    """DRE-3241. 2026-09-05, DRE-3164: round 2 of the second critic died at its
    turn ceiling, twice, and left no marker. Every sweep after it held DRE-3167
    on ROUND ONE's send-back — a finding the re-plan had already answered —
    and told the CEO to move an epic to the lane it was already in."""

    ROUND_ONE = "DRE-3213/DRE-3214 rewrite the same deploy-lag file as DRE-3060"

    def _thread(self):
        return _epic_thread(
            plan_critic.marker(plan_critic.STAGE_POST, 1,
                               plan_critic.SEND_BACK, self.ROUND_ONE),
            plan_critic.death_marker(
                stage=plan_critic.STAGE_POST, run="34008698027", attempt=2,
                step="posta", subtype="error_max_turns", turns=41, ceiling=40),
        )

    def test_a_dead_review_holds_the_children(self):
        board = _Board(*_work_in_backlog(), green_light=APPROVED,
                       epic_thread=self._thread())
        assert board.promote() == 0
        assert board.lane_of(WORK[0]) == "Backlog"

    def test_the_refusal_says_died_and_not_the_previous_send_back(self, capsys):
        board = _Board(*_work_in_backlog(), green_light=APPROVED,
                       epic_thread=self._thread())
        board.promote()
        out = capsys.readouterr().out
        assert f"promotion: {WORK[0]} is not being promoted" in out
        assert plan_critic.POST_DIED_TAG in out
        assert "died" in out
        assert self.ROUND_ONE not in out
        assert plan_critic.POST_SENT_BACK_TAG not in out

    def test_a_dead_review_is_surfaced_on_the_card_at_once(self):
        """Only the "no round at all" refusal waits out the grace window; a
        death is a fact the run already recorded, so it speaks on the first
        sweep — and once."""
        assert reconcile.post_critic_hold_is_overdue(
            plan_critic.POST_DIED_TAG, APPROVED) is True
        board = _Board(*_work_in_backlog(), green_light=APPROVED,
                       epic_thread=self._thread(), held_minutes=1)
        board.promote()
        board.promote()
        posted = board.comments_on(WORK[0])
        assert len(posted) == 1, posted
        assert plan_critic.POST_DIED_TAG in posted[0]
        assert "34008698027" in posted[0]
        # DRE-3289: the sweep names the run it is waiting on rather than asking
        # the CEO for a move — the review re-runs itself once.
        assert plan_critic.REAPPROVE_HOW not in posted[0]
        assert "yours to decide" in posted[0]
        assert self.ROUND_ONE not in posted[0]


# --------------------------------------------------------------------------
# DRE-6743: the sweep reads both queue names and writes through QUEUE_LANE.
#
# DRE-6741 put the switch in `lane_contract`: `QUEUE_LANES` is every name a
# queued card may sit under, `QUEUE_LANE` the one a writer writes. The sweep is
# the queue's heaviest reader, so it is converted on its own: a card in
# `Queued` is promoted exactly as one in `Backlog` is, and every lane and
# receipt it writes follows `QUEUE_LANE` — `Backlog` until DRE-6742 flipped the
# planning exit, `Queued` after, with no edit here.
# --------------------------------------------------------------------------
import ast  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RECONCILE_SOURCE = ROOT / "scripts" / "reconcile.py"
ACTS = ROOT / "config" / "pipeline-acts.json"

#: The lane a test writes the queue under when it needs the cutover's side.
QUEUED = "Queued"

#: Any state filter a board read can carry: one name, or a list of them.
_STATE_FILTER = re.compile(r'state: \{name: \{(eq|in): (\[[^\]]*\]|"[^"]*")\}\}')


def _one_off(identifier: str, lane: str) -> dict:
    """A parentless FLEET card, sitting in `lane`, with nothing blocking it."""
    return {
        "id": f"uuid-{identifier}",
        "identifier": identifier,
        "title": f"a one-off in {lane}",
        "description": "Trim the trailing slash from the health endpoint.",
        "createdAt": CREATED,
        "priority": 3,
        "parent": None,
        "children": {"nodes": []},
        "state": {"name": lane},
        "labels": {"nodes": [{"name": "repo:bureau-pipeline"},
                             {"name": "agent:engineer"}]},
        "comments": {"nodes": [{"body": routing_verdict.verdict_comment(
            "FLEET", "the acceptance criteria are unit-testable")}]},
        "inverseRelations": {"nodes": [], "pageInfo": {"hasNextPage": False}},
    }


class _QueueBoard:
    """Linear as the promotion read and the promotion write see it.

    The read serves only the cards whose lane the query's state filter names,
    so a read that asks for one name sees half the queue. The write is
    `cmd_advance`'s guard: a card moves only out of a lane it was handed."""

    def __init__(self, *cards):
        self.lanes = {c["identifier"]: c["state"]["name"] for c in cards}
        self.cards = {c["identifier"]: c for c in cards}
        self.queries: list[str] = []
        self.posted: list[tuple[str, str]] = []

    def gql(self, query, variables=None):
        self.queries.append(query)
        found = _STATE_FILTER.search(query)
        if "issues(" not in query or found is None:
            raise reconcile.linear_ops.LinearError(
                f"unexpected query {' '.join(query.split())[:80]}")
        kind, names = found.groups()
        wanted = set(json.loads(names)) if kind == "in" else {json.loads(names)}
        return {"issues": {
            "nodes": [c for i, c in self.cards.items() if self.lanes[i] in wanted],
            "pageInfo": {"hasNextPage": False, "endCursor": None},
        }}

    def advance(self, ident, to_state, from_states, *flags, **kw):
        if self.lanes[ident] not in from_states.split(","):
            return False
        self.lanes[ident] = to_state
        return True

    def promote(self) -> int:
        with patch.object(reconcile, "REPO_SLUG", "bureau-pipeline"), \
                patch.object(reconcile.bureau_read, "mode", return_value="off"), \
                patch.object(reconcile.linear_ops, "gql", side_effect=self.gql), \
                patch.object(reconcile.linear_ops, "cmd_advance",
                             side_effect=self.advance), \
                patch.object(reconcile.linear_ops, "cmd_comment",
                             side_effect=lambda i, b: self.posted.append((i, b))), \
                patch.object(reconcile.linear_ops, "count_comments", return_value=0):
            return reconcile.promote_ready(active_count=0)


class TestTheSweepReadsBothQueueNames:
    def test_a_fleet_card_in_either_queue_name_is_promoted_to_in_progress(self):
        """One FLEET card in `Queued`, one in `Backlog`: both reach In Progress. The
        read that asks for `Backlog` alone never sees the first, and a move
        guarded on `Backlog` alone refuses it."""
        board = _QueueBoard(_one_off("DRE-6801", QUEUED), _one_off("DRE-6802", "Backlog"))
        reconcile._write_failures.clear()
        try:
            assert board.promote() == 2
        finally:
            reconcile._write_failures.clear()
        assert board.lanes == {"DRE-6801": "In Progress", "DRE-6802": "In Progress"}

    def test_the_queue_names_come_from_the_contract(self):
        """`reconcile.QUEUE_LANE` and `QUEUE_LANES` are `lane_contract`'s own
        objects, re-exported — never a second definition in the sweep."""
        assert reconcile.QUEUE_LANE is lane_contract.QUEUE_LANE
        assert reconcile.QUEUE_LANES is lane_contract.QUEUE_LANES
        assert not hasattr(reconcile, "BACKLOG_LANE")
        tree = ast.parse(RECONCILE_SOURCE.read_text(encoding="utf-8"))
        assigned = {
            target.id
            for node in ast.walk(tree) if isinstance(node, (ast.Assign, ast.AnnAssign))
            for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
            if isinstance(target, ast.Name)
        }
        assert not assigned & {"QUEUE_LANE", "QUEUE_LANES"}

    def test_no_string_constant_in_the_sweep_names_the_old_lane(self):
        """A lane name in a string is a read or a write that does not follow
        the switch. Comments recording an incident are history and stay."""
        assert lane_contract._scan_python(str(RECONCILE_SOURCE), ["Backlog"]) == set()

    def test_every_board_read_of_the_queue_filters_on_every_queue_name(self):
        """The Linear reads, the door reads and the lane sets the sweep reads
        the queue through all carry every name in `QUEUE_LANES`."""
        listed = json.dumps(list(lane_contract.QUEUE_LANES))
        assert reconcile.QUEUE_STATE_FILTER == f"state: {{name: {{in: {listed}}}}}"
        assert reconcile.QUEUE_STATE_FILTER in reconcile._INTAKE_BLOCKED_QUERY
        for lanes in (reconcile.EPIC_CLOSE_LANES, reconcile.IDLE_LANES):
            assert set(lane_contract.QUEUE_LANES) <= set(lanes)
        asked = []
        with patch.object(reconcile.bureau_read, "board",
                          side_effect=lambda lanes, **kw: asked.append(list(lanes))):
            reconcile._door_backlog_read(None)
        assert asked == [list(lane_contract.QUEUE_LANES)]

    def test_the_door_keeps_a_card_under_either_queue_name(self):
        read = type("Read", (), {"nodes": [_one_off("DRE-6801", QUEUED),
                                           _one_off("DRE-6802", "Backlog"),
                                           _one_off("DRE-6803", "Todo")]})()
        with patch.object(reconcile, "_door_backlog_read", return_value=read), \
                patch.object(reconcile, "complete_inverse_relations"):
            cards = reconcile._door_backlog_children(None)
        assert [c["identifier"] for c in cards] == ["DRE-6801", "DRE-6802"]

    @pytest.mark.parametrize("lane", lane_contract.QUEUE_LANES)
    def test_a_child_waiting_under_either_name_is_in_flow(self, lane):
        """The Intake-blocker phase reads a waiting child of a running epic as
        in flow under either name, and its notice names the lane it is in."""
        child = {**_one_off("DRE-6804", lane),
                 "parent": {"identifier": EPIC, "state": {"name": "In Progress"}}}
        assert reconcile._in_flow(child) is True
        notice = reconcile.intake_blocker_notice(
            {"identifier": "DRE-6805", "title": "a blocker"}, child, titles=False)
        assert f"waiting in {lane} under {EPIC}" in notice

    @pytest.mark.parametrize("lane", lane_contract.QUEUE_LANES)
    def test_the_idle_check_sees_a_card_waiting_under_either_name(self, lane):
        """From the door: one card of this repo in the queue keeps the pass awake."""
        read = type("Read", (), {"nodes": [_one_off("DRE-6806", lane)]})()
        asked = []

        def board(lanes, **kw):
            asked.append(tuple(lanes))
            return read

        with patch.object(reconcile, "REPO_SLUG", "bureau-pipeline"), \
                patch.object(reconcile, "MAX_WIP", 3), \
                patch.object(reconcile.bureau_read, "mode", return_value="on"), \
                patch.object(reconcile.bureau_read, "enabled", return_value=True), \
                patch.object(reconcile.bureau_read, "board", side_effect=board):
            assert reconcile.sweep_idle() is None
        assert set(lane_contract.QUEUE_LANES) <= set(asked[0])


class TestTheSweepWritesThroughQueueLane:
    """Every lane the sweep writes the queue under is `QUEUE_LANE`: on the
    shipped contract that is `Queued` since DRE-6742 moved the planning exit,
    with no edit to the sweep, and with the exit put back it is `Backlog`,
    byte for byte the receipts before it."""

    @staticmethod
    def _park() -> tuple[list, list]:
        card = {"identifier": "DRE-6810", "state": {"name": "In Progress"},
                "labels": {"nodes": []}, "description": ""}
        states, posted = [], []
        with patch.object(reconcile.dead_run, "split_tried", return_value=True), \
                patch.object(reconcile.hold, "apply"), \
                patch.object(reconcile.linear_ops, "cmd_state",
                             side_effect=lambda i, lane, *f, **k: states.append((lane, *f))), \
                patch.object(reconcile.linear_ops, "cmd_comment",
                             side_effect=lambda i, b: posted.append(b)), \
                patch.object(reconcile.linear_ops, "cmd_advance"):
            reconcile.hand_dead_run_to_planner(card, reconcile.REQUEUE_CAP, [])
        return states, posted

    @staticmethod
    def _promote() -> list[str]:
        board = _QueueBoard(_one_off("DRE-6811", lane_contract.QUEUE_LANE))
        reconcile._write_failures.clear()
        try:
            board.promote()
        finally:
            reconcile._write_failures.clear()
        return [b for i, b in board.posted if i == "DRE-6811"]

    @staticmethod
    def _advance_epic() -> str:
        posted = []
        reconcile.reset_sweep_cards()
        with patch.object(reconcile.linear_ops, "cmd_advance", return_value=True), \
                patch.object(reconcile.linear_ops, "cmd_comment",
                             side_effect=lambda i, b: posted.append(b)):
            reconcile._return_epic_to_planning("DRE-6812", "blocker DRE-6813 is Done")
        reconcile.reset_sweep_cards()
        return posted[0]

    def test_the_park_and_the_receipts_follow_queue_lane(self, monkeypatch):
        # The shipped contract: the sweep writes `Queued` everywhere.
        assert lane_contract.QUEUE_LANE == QUEUED
        states, posted = self._park()
        assert states == [(QUEUED, "--park")]
        assert f"parked in {QUEUED} with the 'needs-human' label" in posted[0]
        assert "Backlog" not in posted[0]
        assert self._promote() == [
            f"🧹 Auto-promoted {QUEUED} → In Progress: no parent epic, a FLEET verdict, "
            "and all blockers Done."]
        assert self._advance_epic().startswith(
            f"🧹 Auto-advanced {QUEUED} → Planning: blocker DRE-6813 is Done")

        # The exit put back: the receipts name the old queue name again, as
        # they did before DRE-6742 (the promotion's destination is In Progress
        # since the flip, DRE-7120).
        monkeypatch.setattr(reconcile, "QUEUE_LANE", "Backlog")
        states, posted = self._park()
        assert states == [("Backlog", "--park")]
        assert "parked in Backlog with the 'needs-human' label" in posted[0]
        assert self._promote() == [
            "🧹 Auto-promoted Backlog → In Progress: no parent epic, a FLEET verdict, "
            "and all blockers Done."]
        assert self._advance_epic().startswith(
            "🧹 Auto-advanced Backlog → Planning: blocker DRE-6813 is Done")

    def test_the_two_receipts_are_anchored_on_the_source_not_the_lane(self):
        """`config/pipeline-acts.json` pins the two receipts by the line that
        emits them, and that line names `QUEUE_LANE`, never a lane — so the
        anchor does not move again at the flip."""
        doc = json.loads(ACTS.read_text(encoding="utf-8"))
        anchors = {d["anchor"] for d in doc["unconverted"]
                   if d["file"] == "scripts/reconcile.py"}
        assert "🧹 Auto-promoted {QUEUE_LANE} → {destination}" in anchors
        assert "🧹 Auto-advanced {QUEUE_LANE} → Planning" in anchors
        source = RECONCILE_SOURCE.read_text(encoding="utf-8")
        assert 'f"🧹 Auto-promoted {QUEUE_LANE} → {destination}: {reason}"' in source
        assert 'f"🧹 Auto-advanced {QUEUE_LANE} → Planning: {because}' in source
