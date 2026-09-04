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

    def __init__(self, *cards):
        self.cards = list(cards)
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
            reconcile.mid_epic, "last_green_light", return_value=GREEN_LIGHT
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
