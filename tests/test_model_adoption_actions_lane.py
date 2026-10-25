"""The model adoption workflow's cards are created in their lane, never moved
into one (DRE-7113).

A card CREATED in a lane has no lane before it, and only a move into the
dispatch lane from a waiting lane is a build dispatch
(`lane_contract.is_build_dispatch`, mirrored in the relay by DRE-7107). The
record card and the question card are each created straight into their lane
and nothing moves them afterwards, which is what keeps a build run off both.

Run: cd bureau-pipeline && python3 -m pytest tests/test_model_adoption_actions_lane.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import lane_contract  # noqa: E402
import model_adoption as ma  # noqa: E402
import model_adoption_actions as maa  # noqa: E402
import test_model_adoption_actions as base  # noqa: E402


class OnlyCreates(base.FakeOps):
    """The seam with no way to move a card: any call beyond the ones the
    module makes — a state write, a move — fails the test that made it."""

    def __getattr__(self, name):
        raise AssertionError(f"model_adoption_actions called ops.{name}: only "
                             "create_card may place the card in a lane")


def _only_create_placed_it(ops):
    (made,) = ops.created
    assert [call for call, _ in ops.calls if call == "create_card"] == ["create_card"]
    assert not lane_contract.is_build_dispatch(None, made["lane"])
    return made


@pytest.mark.parametrize("applied, lane", [
    ({"outcome": "written", "written_at": "2026-11-02T07:40:00Z", "sentences": []},
     maa.RECORD_LANE),
    (None, maa.UNWRITTEN_LANE),
], ids=["written", "nothing-written"])
def test_the_record_card_is_created_in_its_lane(applied, lane):
    decision = base.decisions(claude_sonnet_6=(2, 10))[base.SONNET6]
    assert decision["rule"] == ma.RULE_ADOPT
    ops = OnlyCreates()
    maa.open_record_card(decision, run_url=base.RUN_URL, ops=ops, applied=applied)
    assert _only_create_placed_it(ops)["lane"] == lane


def test_the_question_card_is_created_in_its_lane():
    decision = base.decisions(claude_opus_6=(6, 30))[base.OPUS6]
    assert decision["rule"] == ma.RULE_ASK
    ops = OnlyCreates()
    maa.open_question_card(decision, ops=ops)
    assert _only_create_placed_it(ops)["lane"] == maa.QUESTION_LANE


@pytest.mark.parametrize("lane", [maa.RECORD_LANE, maa.UNWRITTEN_LANE,
                                  maa.QUESTION_LANE])
def test_no_lane_it_files_into_is_dispatched_by_creation(lane):
    # And none of them is the dispatch lane, so even a move into one from a
    # waiting lane would start no build.
    assert not lane_contract.is_build_dispatch(None, lane)
    for waiting in lane_contract.DISPATCH_FROM:
        assert not lane_contract.is_build_dispatch(waiting, lane)
