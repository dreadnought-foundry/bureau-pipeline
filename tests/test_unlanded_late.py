"""RED-first: work that never reached a pull request is listed in one late-hold
alert once it passes its due date (DRE-7966).

THE GAP: `flag_unlanded_work` posts one `🚨 unlanded-work-watchdog` comment per
branch (`_flag_one_unlanded_branch`) or per hand-built card with no branch
(`_flag_hand_built_idle`) and never speaks again. The comment is the chase;
nothing said when the wait was late, and the weekly report DRE-7960 counted
1,060.6 hours stood still on this hold kind in one week.

FIX UNDER TEST — `hold_deadline.settle("unlanded-work-watchdog", …)` once per
pass, after both halves:

  * the branch half judges a branch the watchdog would flag on its last
    commit's time, after the lane checks and BEFORE the "already posted"
    check, and `_flag_one_unlanded_branch` returns a `LateHold`, the
    `UNLANDED_UNREAD` constant or None;
  * the card half judges a hand-built card with no branch and no pull request
    on its `no branch` notice's `createdAt` — read off the board's comment
    window when it is whole and off the whole thread otherwise — BEFORE the
    `updatedAt` gate, and never lists a standing card;
  * anything unread makes the pass incomplete, and a pass whose listings were
    not read calls `settle` not at all.

The comments themselves are untouched, and their own suites pin them.

Run: cd bureau-pipeline && python3 -m pytest tests/test_unlanded_late.py -v
"""
from __future__ import annotations

import ast
import inspect
import json
import os
import sys
import textwrap
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/test")
os.environ.setdefault("REPO_SLUG", "test")
os.environ.setdefault("GH_TOKEN", "x")

import hold_deadline  # noqa: E402
import pipeline_act  # noqa: E402
import pipeline_notice  # noqa: E402
import reconcile  # noqa: E402

TAG = "unlanded-work-watchdog"
KEY = f"hold-late:test:{TAG}"
BRANCH = "agent/DRE-2655-drift-count-out-of-the-pill"
BRANCH_CARD = "DRE-2655"
OTHER = "agent/DRE-2656-another-piece"
OTHER_CARD = "DRE-2656"
SHA = "b" * 40
OTHER_SHA = "c" * 40
CARD = "DRE-7777"
STANDING = "DRE-5774"


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    """This sweep owns `test`; the read door is off; the ledgers start clean."""
    monkeypatch.setattr(reconcile, "REPO_SLUG", "test")
    monkeypatch.setattr(reconcile, "REPO", "dreadnought-foundry/test")
    monkeypatch.setattr(reconcile, "_default_branch", None)
    reconcile._write_failures.clear()
    reconcile._degraded.clear()
    yield
    reconcile._write_failures.clear()
    reconcile._degraded.clear()


def _iso(hours_ago: float) -> str:
    return ((datetime.now(UTC) - timedelta(hours=hours_ago))
            .strftime("%Y-%m-%dT%H:%M:%S.000Z"))


def _node(body: str, hours_ago: float) -> dict:
    return {"body": body, "createdAt": _iso(hours_ago)}


def _notice(hours_ago: float) -> dict:
    """The card half's own `no branch` notice, as `_flag_hand_built_idle`
    posts it, `hours_ago` hours ago."""
    return _node(pipeline_act.receipt("work-never-landed", (
        f"🚨 {TAG} no branch: this card has sat in In Progress for 200 minutes "
        "with nothing to point at: no branch, no pull request.")), hours_ago)


def _card(ident=CARD, *, nodes=(), partial=False, updated_hours=0.1,
          labels=("hand-built",)) -> dict:
    """A hand-built card as the board read carries it — its comment window
    newest first, and `pageInfo` saying whether older comments lie beyond."""
    window = sorted(nodes, key=lambda n: n["createdAt"], reverse=True)
    return {
        "identifier": ident,
        "title": "a card",
        "description": "**Repo:** test",
        "updatedAt": _iso(updated_hours),
        "state": {"name": "In Progress"},
        "labels": {"nodes": [{"name": n} for n in labels]},
        "comments": {"nodes": window, "pageInfo": {"hasNextPage": partial}},
    }


def _fake_gh(branches, pr_refs, pulls, compare, listing):
    def gh(*args):
        if args[:2] == ("pr", "list"):
            if isinstance(listing, Exception):
                raise listing
            if listing is not None:
                return listing
            return json.dumps([{"headRefName": r} for r in pr_refs])
        if args[0] == "api":
            path = args[1]
            if path == f"repos/{reconcile.REPO}":
                return "main"
            if path.endswith("/branches"):
                if isinstance(branches, Exception):
                    raise branches
                return "\n".join(json.dumps({"name": n, "sha": s})
                                 for n, s in branches.items())
            if "/commits/" in path and path.endswith("/pulls"):
                sha = path.split("/commits/", 1)[1].split("/", 1)[0]
                return pulls.get(sha, "[]")
            if "/compare/" in path:
                branch = path.split("...", 1)[-1]
                spec = compare.get(branch, (3, 25.0))
                if isinstance(spec, str):
                    return spec
                ahead, hours = spec
                return json.dumps({"ahead": ahead, "last": _iso(hours) if ahead else None})
        return ""
    return gh


class Pass:
    """One `flag_unlanded_work` pass against stubbed GitHub, Linear and the
    console's notice door."""

    def __init__(self, *, branches=None, pr_refs=(), pulls=None, compare=None,
                 listing=None, cards=(), states=None, bodies=None, threads=None):
        self.branches = {BRANCH: SHA} if branches is None else branches
        self.pr_refs, self.listing = pr_refs, listing
        self.pulls, self.compare = pulls or {}, compare or {}
        self.cards, self.states = list(cards), states or {}
        self.bodies, self.threads = bodies or {}, threads or {}
        self.comments: list[tuple[str, str]] = []
        self.raised: list[tuple[str, dict]] = []
        self.resolved: list[str] = []
        self.thread_reads: list[str] = []

    def _state(self, ident):
        value = self.states.get(ident, "In Progress")
        if isinstance(value, Exception):
            raise value
        return value

    def _bodies(self, ident, **_kw):
        value = self.bodies.get(ident, [])
        if isinstance(value, Exception):
            raise value
        return list(value)

    def _thread(self, ident, *_needs, whole=False):
        self.thread_reads.append(ident)
        value = self.threads.get(ident, [])
        if isinstance(value, Exception):
            raise value
        return list(value), "viewer"

    def _raise(self, key, **kw):
        self.raised.append((key, kw))
        return "raised"

    def _resolve(self, key, **_kw):
        self.resolved.append(key)
        return "resolved"

    def run(self):
        fake = _fake_gh(self.branches, self.pr_refs, self.pulls, self.compare,
                        self.listing)
        settle = self.settle = mock.Mock(wraps=hold_deadline.settle)
        with mock.patch.object(reconcile, "gh", side_effect=fake), \
                mock.patch.object(reconcile, "gh_read", side_effect=fake), \
                mock.patch.object(reconcile, "active_cards", return_value=self.cards), \
                mock.patch.object(reconcile, "card_state", side_effect=self._state), \
                mock.patch.object(reconcile.linear_ops, "comment_bodies",
                                  side_effect=self._bodies), \
                mock.patch.object(reconcile.linear_ops, "_thread_and_viewer",
                                  side_effect=self._thread), \
                mock.patch.object(reconcile.linear_ops, "cmd_comment",
                                  side_effect=lambda i, b: self.comments.append((i, b))), \
                mock.patch.object(pipeline_notice, "raise_notice", side_effect=self._raise), \
                mock.patch.object(pipeline_notice, "resolve_notice", side_effect=self._resolve), \
                mock.patch.object(hold_deadline, "settle", settle):
            reconcile.flag_unlanded_work()
        return self

    # what the pass handed `settle`
    @property
    def listed(self) -> list[str]:
        self.settle.assert_called_once()
        return [h.ident for h in self.settle.call_args.args[2]]

    @property
    def complete(self) -> bool:
        self.settle.assert_called_once()
        return self.settle.call_args.kwargs["complete"]


# --------------------------------------------------------------------------- #
# the branch half                                                             #
# --------------------------------------------------------------------------- #


class TestBranchHalf:
    def test_a_branch_a_day_and_an_hour_old_is_listed_and_raised_once(self):
        p = Pass(compare={BRANCH: (3, 25.0)}).run()
        assert p.settle.call_args.args[:2] == (TAG, "test")
        assert p.listed == [BRANCH_CARD]
        assert p.complete is True
        assert [k for k, _ in p.raised] == [KEY]
        assert p.raised[0][1]["severity"] == "WARN"
        assert p.raised[0][1]["title"] == hold_deadline.TITLE.format(n=1, tag=TAG, repo="test")
        assert f"waits on a pull request from {BRANCH}" in p.raised[0][1]["body"]
        assert p.resolved == []

    def test_the_clock_is_the_last_commit(self):
        p = Pass(compare={BRANCH: (3, 25.0)}).run()
        hold = p.settle.call_args.args[2][0]
        assert hold == hold_deadline.LateHold(
            BRANCH_CARD, hold.started, f"a pull request from {BRANCH}")
        age = datetime.now(UTC) - hold.started
        assert timedelta(hours=24.9) < age < timedelta(hours=25.1)

    def test_a_branch_twenty_three_hours_old_is_not_listed(self):
        p = Pass(compare={BRANCH: (3, 23.0)}).run()
        assert p.listed == []
        assert p.complete is True
        assert p.raised == []
        assert p.resolved == [KEY]

    def test_its_comment_is_still_posted_beside_the_late_list(self):
        p = Pass(compare={BRANCH: (3, 25.0)}).run()
        assert [i for i, _ in p.comments] == [BRANCH_CARD]

    def test_a_branch_notified_long_ago_is_still_judged(self):
        posted = f"🚨 {TAG} branch {BRANCH}: the branch carries 3 commit(s)"
        p = Pass(compare={BRANCH: (3, 400.0)}, bodies={BRANCH_CARD: [posted]}).run()
        assert p.comments == [], "the marker still skips the comment"
        assert p.listed == [BRANCH_CARD]
        assert [k for k, _ in p.raised] == [KEY]

    def test_a_branch_whose_pull_request_now_exists_is_not_listed(self):
        p = Pass(compare={BRANCH: (3, 400.0)}, pr_refs=(BRANCH,)).run()
        assert p.listed == []
        assert p.resolved == [KEY]

    def test_a_pull_request_older_than_the_listing_is_not_listed(self):
        p = Pass(compare={BRANCH: (3, 400.0)}, pulls={SHA: "[12]"}).run()
        assert p.listed == []
        assert p.complete is True

    @pytest.mark.parametrize("state", ["Done", "Canceled"])
    def test_a_finished_cards_branch_is_not_listed(self, state):
        p = Pass(compare={BRANCH: (3, 400.0)}, states={BRANCH_CARD: state}).run()
        assert p.listed == []
        assert p.complete is True

    def test_a_branch_with_nothing_of_its_own_is_not_listed(self):
        p = Pass(compare={BRANCH: (0, 400.0)}).run()
        assert p.listed == []
        assert p.complete is True


class TestBranchReturns:
    """`_flag_one_unlanded_branch` says what it found."""

    def _one(self, **kw):
        p = Pass(**kw)
        fake = _fake_gh(p.branches, p.pr_refs, p.pulls, p.compare, None)
        with mock.patch.object(reconcile, "gh", side_effect=fake), \
                mock.patch.object(reconcile, "card_state", side_effect=p._state), \
                mock.patch.object(reconcile.linear_ops, "comment_bodies",
                                  side_effect=p._bodies), \
                mock.patch.object(reconcile.linear_ops, "cmd_comment"):
            return reconcile._flag_one_unlanded_branch({"name": BRANCH, "sha": SHA}, set())

    def test_late_returns_a_late_hold(self):
        found = self._one(compare={BRANCH: (3, 25.0)})
        assert isinstance(found, hold_deadline.LateHold)
        assert (found.ident, found.waits_on) == (BRANCH_CARD, f"a pull request from {BRANCH}")

    def test_unread_returns_the_constant(self):
        assert reconcile.UNLANDED_UNREAD == "unread"
        assert self._one(pulls={SHA: ""}) == reconcile.UNLANDED_UNREAD

    def test_not_late_returns_none(self):
        assert self._one(compare={BRANCH: (3, 23.0)}) is None


# --------------------------------------------------------------------------- #
# the card half                                                               #
# --------------------------------------------------------------------------- #


class TestCardHalf:
    def test_a_notice_a_day_and_an_hour_old_is_listed_despite_a_fresh_comment(self):
        card = _card(nodes=[_notice(25.0), _node("still on it", 0.1)], updated_hours=0.1)
        p = Pass(branches={}, cards=[card]).run()
        assert p.listed == [CARD]
        assert p.complete is True
        assert [k for k, _ in p.raised] == [KEY]
        assert "waits on a branch and a pull request" in p.raised[0][1]["body"]
        assert p.comments == [], "updatedAt still gates the notice"
        assert p.thread_reads == [], "a whole window is read at no request"

    def test_the_clock_is_the_oldest_notice(self):
        card = _card(nodes=[_notice(30.0), _notice(25.0)], updated_hours=0.1)
        p = Pass(branches={}, cards=[card]).run()
        hold = p.settle.call_args.args[2][0]
        age = datetime.now(UTC) - hold.started
        assert timedelta(hours=29.9) < age < timedelta(hours=30.1)
        assert hold.waits_on == "a branch and a pull request"

    def test_a_notice_twenty_three_hours_old_is_not_listed(self):
        card = _card(nodes=[_notice(23.0)], updated_hours=0.1)
        p = Pass(branches={}, cards=[card]).run()
        assert p.listed == []
        assert p.resolved == [KEY]

    def test_the_clock_is_never_updated_at(self):
        """No notice at all on a card idle for days: nothing to time — the
        pass posts the notice, and the late list starts from it."""
        card = _card(nodes=[_node("picked up", 100.0)], updated_hours=100.0)
        p = Pass(branches={}, cards=[card]).run()
        assert [i for i, _ in p.comments] == [CARD]
        assert p.listed == []

    def test_a_standing_card_is_never_listed(self):
        card = _card(STANDING, nodes=[_notice(400.0)], updated_hours=0.1)
        p = Pass(branches={}, cards=[card]).run()
        assert p.listed == []
        assert p.complete is True

    def test_a_card_with_a_branch_is_the_branch_halfs(self):
        card = _card(BRANCH_CARD, nodes=[_notice(400.0)], updated_hours=0.1)
        p = Pass(compare={BRANCH: (3, 2.0)}, cards=[card]).run()
        assert p.listed == []

    def test_a_partial_window_is_read_whole(self):
        card = _card(nodes=[_node("newest", 0.1)], partial=True, updated_hours=0.1)
        whole = [_notice(25.0), _node("newest", 0.1)]
        p = Pass(branches={}, cards=[card], threads={CARD: whole}).run()
        assert p.thread_reads == [CARD]
        assert p.listed == [CARD]
        assert p.complete is True


# --------------------------------------------------------------------------- #
# a short read                                                                #
# --------------------------------------------------------------------------- #


def _unread_branch(case: str) -> dict:
    return {
        "pulls": dict(pulls={SHA: ""}),
        "pulls-garbled": dict(pulls={SHA: "not json"}),
        "compare": dict(compare={BRANCH: ""}),
        "compare-garbled": dict(compare={BRANCH: "not json"}),
        "card-state": dict(states={BRANCH_CARD: RuntimeError("linear down")}),
        "evaluation": dict(bodies={BRANCH_CARD: RuntimeError("linear exploded")}),
    }[case]


class TestShortRead:
    CASES = ["pulls", "pulls-garbled", "compare", "compare-garbled",
             "card-state", "evaluation"]

    @pytest.mark.parametrize("case", CASES)
    def test_an_unread_branch_makes_the_pass_incomplete(self, case):
        p = Pass(**_unread_branch(case)).run()
        assert p.listed == []
        assert p.complete is False
        assert p.raised == [] and p.resolved == [], "nothing raised, nothing resolved"

    @pytest.mark.parametrize("case", CASES)
    def test_another_late_branch_is_still_raised(self, case):
        kw = _unread_branch(case)
        kw.setdefault("compare", {})[OTHER] = (2, 30.0)
        p = Pass(branches={BRANCH: SHA, OTHER: OTHER_SHA}, **kw).run()
        assert p.listed == [OTHER_CARD]
        assert p.complete is False
        assert [k for k, _ in p.raised] == [KEY]
        assert p.raised[0][1]["body"].endswith(hold_deadline.UNREAD_LINE)

    def test_an_evaluation_that_raises_is_still_recorded(self):
        Pass(**_unread_branch("evaluation")).run()
        assert any("unlanded watchdog on branch" in f for f in reconcile._write_failures)

    def test_a_card_whose_whole_thread_read_raises_makes_the_pass_incomplete(self):
        card = _card(nodes=[_node("newest", 0.1)], partial=True, updated_hours=0.1)
        p = Pass(branches={}, cards=[card],
                 threads={CARD: RuntimeError("linear down")}).run()
        assert p.listed == []
        assert p.complete is False
        assert p.raised == [] and p.resolved == []

    def test_with_another_late_the_card_read_failure_still_raises(self):
        card = _card(nodes=[_node("newest", 0.1)], partial=True, updated_hours=0.1)
        p = Pass(compare={BRANCH: (3, 30.0)}, cards=[card],
                 threads={CARD: RuntimeError("linear down")}).run()
        assert p.listed == [BRANCH_CARD]
        assert p.complete is False
        assert [k for k, _ in p.raised] == [KEY]

    def test_a_card_whose_evaluation_raises_makes_the_pass_incomplete(self):
        card = _card(nodes=[_notice(25.0)], updated_hours=0.1)
        with mock.patch.object(reconcile, "hand_built_reason",
                               side_effect=RuntimeError("boom")):
            p = Pass(branches={}, cards=[card]).run()
        assert p.complete is False
        assert any(f"unlanded watchdog on {CARD}" in f for f in reconcile._write_failures)


# --------------------------------------------------------------------------- #
# a whole pass                                                                #
# --------------------------------------------------------------------------- #


class TestThePass:
    def test_nothing_late_and_everything_read_resolves_once(self):
        card = _card(nodes=[_notice(2.0)], updated_hours=0.1)
        p = Pass(compare={BRANCH: (3, 2.0)}, cards=[card]).run()
        assert p.listed == []
        assert p.complete is True
        assert p.resolved == [KEY]
        assert p.raised == []

    @pytest.mark.parametrize("listing", ["", "not json", RuntimeError("gh down")])
    def test_an_unreadable_pull_request_listing_settles_nothing(self, listing):
        p = Pass(compare={BRANCH: (3, 400.0)}, listing=listing).run()
        p.settle.assert_not_called()
        assert p.raised == [] and p.resolved == []

    @pytest.mark.parametrize("fault", [
        reconcile.ReconcileReadError("gh api branches failed rc=1: HTTP 403"),
        reconcile.ReconcileRateLimited("gh api branches: API rate limit exceeded"),
    ])
    def test_an_unreadable_branch_listing_settles_nothing(self, fault):
        p = Pass(branches=fault, cards=[_card(nodes=[_notice(400.0)])]).run()
        p.settle.assert_not_called()
        assert p.raised == [] and p.resolved == []

    def test_a_held_board_settles_nothing(self):
        p = Pass(compare={BRANCH: (3, 400.0)})
        with mock.patch.object(reconcile, "_unlanded_door_lanes",
                               side_effect=reconcile.BoardHeld("linear-hold")), \
                pytest.raises(reconcile.BoardHeld):
            p.run()
        p.settle.assert_not_called()
        assert p.raised == [] and p.resolved == []

    def test_one_settle_for_both_halves(self):
        card = _card(nodes=[_notice(26.0)], updated_hours=0.1)
        p = Pass(compare={BRANCH: (3, 25.0)}, cards=[card]).run()
        assert sorted(p.listed) == sorted([BRANCH_CARD, CARD])
        assert len(p.raised) == 1
        assert p.raised[0][1]["title"] == hold_deadline.TITLE.format(n=2, tag=TAG, repo="test")

    def test_the_settle_line_is_printed(self, capsys):
        Pass(compare={BRANCH: (3, 25.0)}).run()
        out = capsys.readouterr().out
        assert (f"hold-deadline: {TAG} repo=test late=1 cards={BRANCH_CARD} "
                "complete=yes outcome=raised") in out


# --------------------------------------------------------------------------- #
# the docstrings say it                                                       #
# --------------------------------------------------------------------------- #


def _doc(fn) -> str:
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    return " ".join((ast.get_docstring(tree.body[0]) or "").split())


@pytest.mark.parametrize("fn", ["flag_unlanded_work", "_flag_one_unlanded_branch",
                                "_flag_hand_built_idle"])
def test_the_docstrings_name_the_clocks_the_read_and_the_exclusions(fn):
    doc = _doc(getattr(reconcile, fn))
    assert "DRE-7966" in doc
    for words in ("last commit", "createdAt", "window_is_partial", "whole thread",
                  "incomplete", "standing"):
        assert words in doc, f"{fn}'s docstring does not name {words!r}"


def test_settle_is_called_once_in_the_watchdog():
    src = inspect.getsource(reconcile.flag_unlanded_work)
    assert src.count("hold_deadline.settle(") == 1
    assert "settle(" not in inspect.getsource(reconcile._flag_hand_built_idle)
    assert "settle(" not in inspect.getsource(reconcile._flag_one_unlanded_branch)
