"""The second critic's hold notice stays quiet while a planner is held, and
only then (DRE-8618, epic DRE-8539).

THE RULE THAT WENT. While an approved epic's activate run waited in the
planner line (DRE-5177), `reconcile.post_critic_hold_is_overdue` logged the
"the second critic has not passed it" notice instead of posting it to the
children: telling the CEO to re-run a review that is merely queued is the
wrong instruction. The cap and its line are gone
(`architecture/decisions/adr-planner-cap-removed.md`).

THE RULE UNDER TEST. A planner now waits only when the planners' Linear
allowance is low, and says so with a `🪫 planner-hold: held` receipt
(DRE-8616). So the notice is logged and not posted while
`planner_allowance.held(bodies, now)` is true, and only then: an epic whose
newest planner-hold receipt is `started`, a `held` past its `until`, or no
planner-hold receipt at all is timed from the green light exactly as before.
A decision or a death speaks at once whatever the receipt says.

Every receipt is composed by `planner_allowance.format_receipt`, the module
that owns the grammar, so a grammar change cannot leave this suite reading a
string nothing posts.

Run: cd bureau-pipeline && python3 -m pytest tests/test_planner_hold_notices.py -v
"""
from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")
os.environ.setdefault("GH_TOKEN", "x")

import plan_critic  # noqa: E402
import planner_allowance  # noqa: E402
import reconcile  # noqa: E402
import routing_verdict  # noqa: E402

EPIC = "DRE-9200"
CHILD = "DRE-9201"
GREEN_LIT = 45  # minutes ago: past POST_CRITIC_GRACE_MINUTES (30)
UNREAD = plan_critic.POST_UNREAD_TAG


def _iso(minutes_ago: float) -> str:
    return ((datetime.now(UTC) - timedelta(minutes=minutes_ago))
            .isoformat().replace("+00:00", "Z"))


def _hold(state: str, minutes_ago: float = 5.0, *, until_in: float = 60.0) -> str:
    """One planner-hold receipt on the epic, `minutes_ago` old; a `held` is
    live for `until_in` more minutes (negative: expired)."""
    at = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    if state == "held":
        return planner_allowance.format_receipt(
            "held", card=EPIC, run="38100000001", repo="dreadnought-foundry/agent-bureau",
            remaining=312, limit=5000, at=at, next_read=at + timedelta(minutes=5),
            every=5, until=datetime.now(UTC) + timedelta(minutes=until_in))
    return planner_allowance.format_receipt(
        "started", card=EPIC, run="38100000001", repo="dreadnought-foundry/agent-bureau",
        remaining=1400, limit=5000, at=at, because="hold-max")


def test_the_grace_is_the_one_these_tests_are_written_around():
    assert reconcile.POST_CRITIC_GRACE_MINUTES == 30


# --------------------------------------------------------------------------- #
# the gate                                                                     #
# --------------------------------------------------------------------------- #


class TestTheGate:
    def test_a_live_held_receipt_is_logged_not_posted(self, capsys):
        assert reconcile.post_critic_hold_is_overdue(
            UNREAD, _iso(GREEN_LIT), [_hold("held")]) is False
        out = capsys.readouterr().out
        assert "held by the planners' Linear allowance" in out
        assert "the second critic's review has not started" in out

    def test_held_is_read_off_the_newest_receipt(self):
        """A `started` after the `held` ends the hold: timed as before."""
        bodies = [_hold("held", 20), _hold("started", 2)]
        assert reconcile.post_critic_hold_is_overdue(
            UNREAD, _iso(GREEN_LIT), bodies) is True

    @pytest.mark.parametrize("bodies", [
        [_hold("started", 2)],
        [_hold("held", 70, until_in=-5)],
        [],
        ["an unrelated comment"],
    ], ids=["started", "expired-held", "nothing", "no-receipt"])
    def test_otherwise_it_is_timed_from_the_green_light_as_before(self, bodies):
        assert reconcile.post_critic_hold_is_overdue(
            UNREAD, _iso(GREEN_LIT), bodies) is True
        assert reconcile.post_critic_hold_is_overdue(
            UNREAD, _iso(10), bodies) is False
        assert reconcile.post_critic_hold_is_overdue(UNREAD, None, bodies) is True

    @pytest.mark.parametrize("tag", [plan_critic.POST_SENT_BACK_TAG,
                                     plan_critic.POST_DIED_TAG])
    def test_a_decision_or_a_death_speaks_at_once_even_while_held(self, tag):
        assert reconcile.post_critic_hold_is_overdue(
            tag, _iso(GREEN_LIT), [_hold("held")]) is True


# --------------------------------------------------------------------------- #
# the same rule through promote_ready                                          #
# --------------------------------------------------------------------------- #

FLEET_VERDICT = routing_verdict.verdict_comment(
    "FLEET", "the acceptance criteria are unit-testable")


def _pipeline_comment(body: str) -> dict:
    return {"body": body, "authored_by_pipeline": True}


def _child() -> dict:
    return {
        "id": f"uuid-{CHILD}",
        "identifier": CHILD,
        "title": "Build piece one",
        "description": "Add it.\n\n## Acceptance criteria\n\n- [ ] it renders\n",
        "createdAt": _iso(60 * 24),  # before the green light: not an addition
        "parent": {"identifier": EPIC, "state": {"name": "In Progress"}},
        "labels": {"nodes": [{"name": n} for n in
                             ("repo:bureau-pipeline", "agent:engineer")]},
        "comments": {"nodes": [{"body": FLEET_VERDICT}]},
        "inverseRelations": {"nodes": []},
    }


class _Promotion:
    """`promote_ready` over one Backlog child of an In Progress epic whose
    second critic has not passed it, every gate not under test held open —
    the shape of tests/test_reconcile_promotion.py's board."""

    def __init__(self, epic_bodies):
        self.thread = [_pipeline_comment(plan_critic.cycle_marker(EPIC))] + [
            _pipeline_comment(b) for b in epic_bodies]
        self.posted: list[tuple[str, str]] = []
        self.advanced: list[str] = []

    def promote(self):
        with patch.object(reconcile, "REPO_SLUG", "bureau-pipeline"), patch.object(
            reconcile, "backlog_children", return_value=[_child()]
        ), patch.object(
            reconcile, "epic_blockers_unmet", return_value=False
        ), patch.object(
            reconcile.mid_epic, "last_green_light", return_value=_iso(GREEN_LIT),
        ), patch.object(
            reconcile.linear_ops, "comment_records",
            side_effect=lambda epic: self.thread,
        ), patch.object(
            reconcile, "card_state", return_value="Done"
        ), patch.object(
            reconcile.linear_ops, "add_label", side_effect=lambda *a: None
        ), patch.object(
            reconcile.linear_ops, "cmd_advance",
            side_effect=lambda ident, *a: self.advanced.append(ident),
        ), patch.object(
            reconcile.linear_ops, "cmd_comment",
            side_effect=lambda i, b: self.posted.append((i, b)),
        ), patch.object(
            reconcile.linear_ops, "count_comments",
            side_effect=lambda i, needle, **kw: sum(
                1 for pi, pb in self.posted if pi == i and needle in pb),
        ):
            return reconcile.promote_ready(active_count=0)

    def unread_notices(self) -> list[str]:
        return [b for i, b in self.posted if i == CHILD and UNREAD in b]


class TestThePromotionGate:
    def test_a_held_epic_holds_its_child_and_posts_nothing(self, capsys):
        board = _Promotion([_hold("held")])
        assert board.promote() == 0
        assert board.advanced == []
        assert board.unread_notices() == []
        assert "held by the planners' Linear allowance" in capsys.readouterr().out

    @pytest.mark.parametrize("bodies", [[_hold("started", 2)], []],
                             ids=["started", "no-receipt"])
    def test_a_started_or_unheld_epic_posts_the_notice_once(self, bodies):
        board = _Promotion(bodies)
        assert board.promote() == 0
        board.promote()
        assert board.advanced == []
        notices = board.unread_notices()
        assert len(notices) == 1, notices
        assert EPIC in notices[0]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
