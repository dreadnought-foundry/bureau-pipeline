"""The sweep names a proof record nothing can move (DRE-7742).

On 2026-10-20 four proof records were open with nothing able to move them: two
cards in Queued, which the proof dispatcher does not read, one carrying
`needs-human`, and one pull request that modified a record instead of adding
one, so the gate read no record at all. These tests stand each shape up, show
the sweep names it on the card at most once a day, and show a normal record —
In Review, or in Hand-work as a draft with a hold — is not named.
"""
from __future__ import annotations

import inspect
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import hold  # noqa: E402
import linear_ops  # noqa: E402
import pipeline_notice  # noqa: E402
import proof_record_stuck  # noqa: E402
import reconcile  # noqa: E402

NOW = datetime(2026, 10, 20, 18, 0, tzinfo=timezone.utc)
RECORD = [{"path": "docs/thing-proof-2026-10.md", "changeType": "ADDED"}]
MODIFIED = [{"path": "docs/thing-proof-2026-10.md", "changeType": "MODIFIED"}]
HOLD = ("🔬 proof-waiting: the release is not cut — needs the release train's "
        "next cut")


def _iso(when: datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def _pr(number=3876, card="DRE-5357", *, draft=False, files=RECORD, hours=60):
    return {"number": number, "url": f"https://github.com/o/r/pull/{number}",
            "headRefName": f"agent/{card}-proof-record", "isDraft": draft,
            "createdAt": _iso(NOW - timedelta(hours=hours)), "files": files}


def _comment(body, hours_ago=1.0):
    return {"body": body, "createdAt": _iso(NOW - timedelta(hours=hours_ago)),
            "user": {"id": "pipeline"}}


def _card(ident="DRE-5357", lane="In Review", labels=(), comments=()):
    return {"identifier": ident, "state": {"name": lane},
            "labels": {"nodes": [{"name": n} for n in labels]},
            "comments": {"pageInfo": {"hasNextPage": False},
                         # Linear answers newest first; window_nodes reverses it.
                         "nodes": list(reversed(list(comments)))}}


class World:
    """GitHub's open pull requests and Linear's cards, every write recorded."""

    def __init__(self, prs, cards):
        self.prs = prs
        self.cards = {c["identifier"]: c for c in cards}
        self.comment = mock.MagicMock()
        self.raised = mock.MagicMock(return_value="raised")
        self.resolved = mock.MagicMock(return_value="not-open")

    def gh(self, *args):
        assert args[:2] == ("pr", "list"), args
        return json.dumps(self.prs)

    def gql(self, query, variables=None):
        card = self.cards.get((variables or {}).get("id"))
        if card is None:
            raise linear_ops.LinearError("no such card")
        return {"issue": card}

    def run(self):
        with mock.patch.object(reconcile, "gh", side_effect=self.gh), \
                mock.patch.object(linear_ops, "gql", side_effect=self.gql), \
                mock.patch.object(linear_ops, "cmd_comment", self.comment), \
                mock.patch.object(pipeline_notice, "raise_notice", self.raised), \
                mock.patch.object(pipeline_notice, "resolve_notice", self.resolved):
            return proof_record_stuck.sweep(now=NOW)

    def bodies(self):
        return [c.args[1] for c in self.comment.call_args_list]


#: The four shapes, each as (pull request, card, the words its line carries).
SHAPES = {
    "queued": (_pr(), _card(lane="Queued"), ("Queued", "does not read")),
    "needs-human": (_pr(), _card(labels=("needs-human",)), ("needs-human",)),
    "draft-no-hold": (_pr(draft=True), _card(), ("draft", "no hold")),
    "no-record": (_pr(files=MODIFIED), _card(),
                  ("adds no .md file under docs/ or architecture/",)),
}


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_each_shape_is_named_on_the_card(shape):
    pr, card, words = SHAPES[shape]
    world = World([pr], [card])
    assert world.run() == []
    assert world.comment.call_count == 1
    ident, body = world.comment.call_args.args
    assert ident == "DRE-5357"
    assert body.startswith(f"{proof_record_stuck.MARK} #3876:")
    for word in words:
        assert word in body
    # Each line says what moves it.
    assert "moves it" in body


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_each_shape_is_named_at_most_once_a_day(shape):
    pr, card, _ = SHAPES[shape]
    told = f"{proof_record_stuck.MARK} #3876: said before"
    within = _card(lane=card["state"]["name"],
                   labels=[n["name"] for n in card["labels"]["nodes"]],
                   comments=[_comment(told, hours_ago=23)])
    world = World([pr], [within])
    assert world.run() == []
    assert world.comment.call_count == 0

    stale = _card(lane=card["state"]["name"],
                  labels=[n["name"] for n in card["labels"]["nodes"]],
                  comments=[_comment(told, hours_ago=25)])
    world = World([pr], [stale])
    assert world.run() == []
    assert world.comment.call_count == 1


def test_a_note_for_another_pull_request_is_not_the_once():
    told = f"{proof_record_stuck.MARK} #387: another record"
    world = World([_pr()], [_card(lane="Queued", comments=[_comment(told)])])
    world.run()
    assert world.comment.call_count == 1


def test_two_shapes_on_one_record_are_two_lines_in_one_note():
    world = World([_pr(files=MODIFIED)], [_card(lane="Queued")])
    world.run()
    assert world.comment.call_count == 1
    body = world.bodies()[0]
    lines = [ln for ln in body.splitlines() if ln.startswith("- ")]
    assert len(lines) == 2
    assert "Queued" in lines[0] and "moves it" in lines[0]
    assert "adds no .md file" in lines[1] and "moves it" in lines[1]


def test_a_normal_record_in_review_is_not_named():
    world = World([_pr()], [_card(lane="In Review")])
    assert world.run() == []
    assert world.comment.call_count == 0
    assert world.raised.call_count == 0


def test_a_draft_in_hand_work_with_a_hold_is_not_named():
    world = World([_pr(draft=True, hours=200)],
                  [_card(lane="Hand-work", comments=[_comment(HOLD)])])
    assert world.run() == []
    assert world.comment.call_count == 0
    assert world.raised.call_count == 0


def test_a_hold_a_later_observation_discharged_is_no_hold():
    observed = "🔬 proof-observed: the release was cut"
    world = World([_pr(draft=True)],
                  [_card(lane="Hand-work",
                         comments=[_comment(HOLD, 5), _comment(observed, 2)])])
    world.run()
    assert world.comment.call_count == 1
    assert "draft" in world.bodies()[0]


def test_the_review_cap_park_in_green_light_is_the_dispatchers():
    stamp = hold.stamp_line("review-cap-spent", "a" * 40, "scripts/reconcile.py")
    world = World([_pr()], [_card(lane="Green Light", labels=("needs-human",),
                                  comments=[_comment(stamp)])])
    world.run()
    assert world.comment.call_count == 0


def test_a_branch_that_is_not_a_proof_record_is_not_read():
    other = dict(_pr(), headRefName="agent/DRE-5357-something-else")
    world = World([other], [_card(lane="Queued")])
    world.run()
    assert world.comment.call_count == 0


def test_past_72_hours_it_raises_an_operator_alert():
    world = World([_pr(hours=80)], [_card(lane="Queued")])
    assert world.run() == []
    assert world.raised.call_count == 1
    call = world.raised.call_args
    assert call.args[0].startswith("proof_record_stuck:")
    assert call.args[0].endswith(":DRE-5357")
    assert call.kwargs["severity"] == "WARN"
    assert "#3876" in call.kwargs["body"]
    assert "72 hours" in world.bodies()[0]


def test_before_72_hours_there_is_no_alert():
    world = World([_pr(hours=71)], [_card(lane="Queued")])
    world.run()
    assert world.comment.call_count == 1
    assert world.raised.call_count == 0


def test_a_record_once_named_and_now_moving_resolves_its_alert():
    told = f"{proof_record_stuck.MARK} #3876: said before"
    world = World([_pr(hours=100)], [_card(lane="In Review", comments=[_comment(told, 30)])])
    world.run()
    assert world.comment.call_count == 0
    assert world.raised.call_count == 0
    assert world.resolved.call_count == 1
    assert world.resolved.call_args.args[0].endswith(":DRE-5357")


def test_an_alert_the_door_lost_is_a_failure():
    world = World([_pr(hours=80)], [_card(lane="Queued")])
    world.raised.return_value = "lost"
    failures = world.run()
    assert len(failures) == 1 and "DRE-5357" in failures[0]


def test_a_card_that_cannot_be_read_names_nothing(capsys):
    world = World([_pr()], [])
    assert world.run() == []
    assert world.comment.call_count == 0
    assert "could not be read" in capsys.readouterr().out


def test_a_note_that_does_not_land_is_a_failure():
    world = World([_pr()], [_card(lane="Queued")])
    world.comment.side_effect = RuntimeError("Linear said no")
    failures = world.run()
    assert len(failures) == 1 and "#3876" in failures[0]


def test_the_sweep_runs_it_as_a_backstop():
    assert "name_unmovable_proof_records," in inspect.getsource(reconcile.main)
    with mock.patch.object(proof_record_stuck, "sweep", return_value=["boom"]):
        before = list(reconcile._write_failures)
        try:
            reconcile.name_unmovable_proof_records()
            assert reconcile._write_failures[len(before):] == ["boom"]
        finally:
            del reconcile._write_failures[len(before):]
