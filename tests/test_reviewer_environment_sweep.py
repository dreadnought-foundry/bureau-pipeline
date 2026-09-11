"""RED-first tests for DRE-3431 — the reconcile sweep holds a crashed review
when the cause is the RUNNER'S ENVIRONMENT, and releases the held heads by
itself once the environment is fixed.

WHAT WAS WRONG. `recover_crashed_reviews()` already bounds a crashed review to
ONE automatic re-dispatch per head sha (`CRASHED_REVIEW_RETRY_CAP`, DRE-2282)
and, past it, reports `reviewer-down` on the linked card. On 2026-09-08
(DRE-3416) the floating `claude-code-action@v1` tag moved, every Claude-running
job in the fleet died about thirteen seconds in, and that report was wrong in
two ways at once:

  * it named NO cause and NO check — it told the operator to "check the
    critic's auth/token", which was the one thing that was not wrong; and
  * nothing ever re-armed the held heads. Four pull requests waited for a hand
    push or a hand dispatch after the pin landed.

WHAT IS UNDER TEST. The sweep reads the cause where it already reads — the
medic's evidence note on the linked card (DRE-3430), through
`reviewer_environment.evidence_for_head` — and:

  1. at the retry cap with evidence for this head, posts the HOLD
     (`reviewer_environment.post_hold`) instead of `reviewer-down`;
  2. with NO evidence, runs the `reviewer-down` path exactly as before
     (`tests/test_crashed_review_recovery.py` is the byte-for-byte pin);
  3. while an unreleased hold stands on ANY open PR in this repository, holds a
     newly crashed head that carries evidence IMMEDIATELY, without spending its
     own re-dispatch — the second identical failure anywhere on this runner is
     the proof;
  4. releases held heads on either signal, read per sweep from the listing the
     sweep already fetches: a qa-bot `QA Critic` comment carrying a `VERDICT:`
     line on any open PR in this repository after the hold, or a comment on the
     held PR whose whole body is the re-run act; and
  5. re-dispatches the released heads through the existing `eligible` list —
     oldest PR first, paced by `CRASHED_REVIEW_SWEEP_CAP`, receipted by the
     existing `crashed-review-redispatch` receipt — so a head that crashes
     again after a release gets a SECOND hold (`again after release`), never a
     third dispatch.

Every string belongs to `scripts/reviewer_environment.py` (DRE-3428): the tag
counted, the cause read, the one hold writer, the release act. The sweep
composes no hold body of its own, and these tests read those strings off that
module rather than restating them — a restated contract is a second contract.

Run: cd bureau-pipeline && python3 -m pytest tests/test_reviewer_environment_sweep.py -v
"""
from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")
os.environ.setdefault("GH_TOKEN", "x")

import pipeline_act  # noqa: E402
import reconcile  # noqa: E402
import review_rerun  # noqa: E402
import reviewer_environment as renv  # noqa: E402

SHA = "c0ffee" * 6 + "abcd"
OLD_SHA = "b" * 40
CARD = "DRE-3431"
BRANCH = f"agent/{CARD}-widget"
SIGNATURE = renv.SIGNATURES[0]  # native-binary-missing, DRE-3416's own

CRASHED = '[["completed", "failure"]]'
NO_REVIEW_CHECKS = "[]"

_BASE = datetime(2026, 9, 11, 6, 0, tzinfo=UTC)


def _at(minutes: float) -> str:
    return (_BASE + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


@pytest.fixture(autouse=True)
def _repo(monkeypatch):
    monkeypatch.setattr(reconcile, "REPO", "dreadnought-foundry/agent-bureau")
    monkeypatch.setattr(reconcile, "REPO_SLUG", "agent-bureau")
    reconcile.reset_sweep_cards()
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()
    renv.POST_FAILURES.clear()
    yield
    reconcile.reset_sweep_cards()
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()
    renv.POST_FAILURES.clear()


# --------------------------------------------------------------------------- #
# the payloads, composed through the modules that own them                     #
# --------------------------------------------------------------------------- #


def _pr(number=2370, sha=SHA, branch=BRANCH, comments=(), mstate="CLEAN",
        draft=False):
    return {
        "number": number,
        "headRefName": branch,
        "headRefOid": sha,
        "mergeStateStatus": mstate,
        "isDraft": draft,
        "comments": list(comments),
    }


def _note(sha=SHA, signature=SIGNATURE):
    """The medic's evidence note on the card (DRE-3430), composed by the module
    that writes it — never a hand-typed lookalike."""
    return renv.evidence_note(signature, sha, "https://github.com/x/y/runs/1")


def _hold_body(sha=SHA, count=2, signature=SIGNATURE):
    return pipeline_act.receipt(
        renv.HOLD_ACT, renv.hold_receipt(signature, sha, count)
    )


def _hold_comment(at, sha=SHA, count=2, author="agent-bureau-bot"):
    return {"author": {"login": author}, "body": _hold_body(sha, count),
            "createdAt": at}


def _redispatch_comment(at, sha=SHA, author="agent-bureau-bot"):
    return {
        "author": {"login": author},
        "body": (f"🔁 {reconcile.CRASHED_REVIEW_DISPATCH_TAG} @{sha}: the "
                 "review run for this head crashed and the sweep re-dispatched"),
        "createdAt": at,
    }


def _verdict_comment(at, sha=SHA, author="agent-bureau-qa-bot"):
    return {
        "author": {"login": author},
        "body": f"🔎 QA Critic — VERDICT: APPROVE @{sha}\n\nLooks solid.",
        "createdAt": at,
    }


def _act_comment(at, body=review_rerun.RERUN_REVIEW_ACT, author="ceo"):
    return {"author": {"login": author}, "body": body, "createdAt": at}


def _ok(stdout=""):
    return SimpleNamespace(returncode=0, stdout=stdout, stderr="")


class _World:
    """One repository, as the sweep sees it: the open-PR listing, the review
    check runs at every head, the workflow_dispatch runs in flight, and the
    linked cards' Linear comments.

    Writes land back in the world, which is what makes a REPLAY possible: the
    hold receipt the sweep posts on the pull request is the counter the next
    sweep reads, and the mirror on the card is what the console reads. A test
    that threw those away could not tell "held once" from "held every sweep".
    """

    def __init__(self, prs, cards=None, checks=CRASHED, runs=(), pr_rc=0):
        self.prs = list(prs)
        self.cards = {k: list(v) for k, v in (cards or {}).items()}
        #: One jq'd review-check payload for every head, or a per-sha mapping
        #: when a test needs one PR crashed and another quiet.
        self.checks = checks
        self.runs = list(runs)
        self.pr_rc = pr_rc
        self.gh_calls: list = []
        self.nudges: list = []
        self.pr_posts: list = []
        self.card_posts: list = []
        #: Well past every seeded timestamp, so a write is always newer than
        #: the history it lands on — which is the real ordering and the one the
        #: hold/release comparisons turn on.
        self.clock = 1000.0

    # --- the clock: every write is newer than everything already there ----
    def _stamp(self) -> str:
        self.clock += 1
        return _at(self.clock)

    def _find(self, number):
        return next(p for p in self.prs if p["number"] == int(number))

    # --- the gh surface ---------------------------------------------------
    def _run(self, argv, **_kwargs):
        assert argv[0] == "gh", f"unexpected call: {argv}"
        self.gh_calls.append(tuple(argv[1:]))
        if argv[1:3] == ["pr", "list"]:
            return _ok(json.dumps(self.prs))
        if argv[1] == "api" and "/check-runs" in argv[2]:
            if isinstance(self.checks, dict):
                sha = argv[2].split("/commits/")[1].split("/")[0]
                return _ok(self.checks.get(sha, CRASHED))
            return _ok(self.checks)
        if argv[1:3] == ["run", "list"]:
            return _ok(json.dumps(self.runs))
        if argv[1:3] == ["pr", "comment"]:
            body = argv[argv.index("--body") + 1]
            number = int(argv[3])
            self.pr_posts.append((number, body))
            if self.pr_rc == 0:
                self._find(number)["comments"].append({
                    "author": {"login": "agent-bureau-bot"},
                    "body": body, "createdAt": self._stamp(),
                })
                return _ok()
            return SimpleNamespace(returncode=self.pr_rc, stdout="", stderr="nope")
        if argv[1] == "api":
            return _ok("")
        raise AssertionError(f"unexpected gh call: {argv}")

    # --- Linear -----------------------------------------------------------
    def _bodies(self, identifier):
        return list(self.cards.get(identifier, []))

    def _comment(self, identifier, body):
        self.card_posts.append((identifier, body))
        self.cards.setdefault(identifier, []).append(body)

    # --- one sweep --------------------------------------------------------
    def sweep(self, capsys=None):
        """Run `recover_crashed_reviews()` once, from a cleared pass cache."""
        reconcile.reset_sweep_cards()
        before = (len(self.nudges), len(self.pr_posts), len(self.card_posts))

        def nudge(workflow, pr_number):
            self.nudges.append((workflow, pr_number))
            return True

        with patch.object(reconcile.subprocess, "run", side_effect=self._run), \
            patch.object(renv.subprocess, "run", side_effect=self._run), \
            patch.object(reconcile, "_nudge", side_effect=nudge), \
            patch.object(reconcile.linear_ops, "comment_bodies",
                         side_effect=self._bodies), \
            patch.object(reconcile.linear_ops, "cmd_comment",
                         side_effect=self._comment):
            reconcile.recover_crashed_reviews()
        out = capsys.readouterr().out if capsys else ""
        return SimpleNamespace(
            nudges=self.nudges[before[0]:],
            pr_posts=self.pr_posts[before[1]:],
            card_posts=self.card_posts[before[2]:],
            log=out,
        )


def _holds(posts):
    return [b for _, b in posts if renv.HOLD_TAG in b]


def _redispatch_receipts(posts):
    return [b for _, b in posts if reconcile.CRASHED_REVIEW_DISPATCH_TAG in b]


# --------------------------------------------------------------------------- #
# 1. the replay — agent-bureau #2370's history, as the sweep sees it           #
# --------------------------------------------------------------------------- #


class TestTheIncidentReplay:
    def test_one_retry_then_one_hold_and_never_a_third_dispatch(self, capsys):
        """ACCEPTANCE: crash at head H with an evidence note → sweep 1
        re-dispatches (receipt). The re-dispatch crashes and the medic leaves a
        SECOND note → exactly ONE hold receipt on the pull request and ONE on
        the card. Sweeps 2, 3 and 4 dispatch nothing: `_nudge` is not called."""
        world = _World([_pr()], cards={CARD: [_note()]})

        first = world.sweep(capsys)
        assert len(first.nudges) == 1, (
            "the FIRST crash still earns its one automatic re-dispatch — a "
            "transient must not be held on the strength of one note"
        )
        assert len(_redispatch_receipts(first.pr_posts)) == 1
        assert _holds(first.pr_posts) == [] and _holds(first.card_posts) == []

        # the re-dispatch crashed too, and the medic left its second note
        world.cards[CARD].append(_note())

        second = world.sweep(capsys)
        assert second.nudges == [], (
            "the cap is spent and the cause is the runner's environment — "
            "hold, never a second dispatch"
        )
        assert len(_holds(second.pr_posts)) == 1, "one hold receipt on the PR"
        assert len(_holds(second.card_posts)) == 1, "one mirror on the card"

        for n in (3, 4, 5):
            later = world.sweep(capsys)
            assert later.nudges == [], f"sweep {n} must not dispatch"
            assert _holds(later.pr_posts) == [], (
                f"sweep {n} must not re-post a hold that already stands"
            )
            assert _holds(later.card_posts) == []

        assert len(_holds(world.pr_posts)) == 1, (
            "ONE hold receipt for this head across the whole incident"
        )
        assert len(_holds(world.card_posts)) == 1

    def test_the_hold_names_the_cause_and_the_check(self):
        """DRE-3416's whole cost was a report that named neither. The body is
        `reviewer_environment`'s, so this asserts the sweep posted THAT body —
        never one of its own."""
        world = _World([_pr(comments=[_redispatch_comment(_at(0))])],
                       cards={CARD: [_note()]})
        world.sweep()
        body = _holds(world.pr_posts)[0]
        assert body == _hold_body(SHA, 2), (
            "the sweep posts the ONE writer's body, byte for byte"
        )
        assert SIGNATURE.slug in body and SIGNATURE.check in body
        assert renv.CRITIC_UNAVAILABLE_MARKER not in body, (
            "a hold is mirrored to the card and the fleet detector counts "
            "crashes — never holds (DRE-3435)"
        )
        assert world.card_posts[0][0] == CARD

    def test_the_reviewer_down_report_is_not_posted_when_the_cause_is_known(self):
        """The two are alternatives, not a pair: a head held with the cause
        named must not also carry the report that names none."""
        world = _World([_pr(comments=[_redispatch_comment(_at(0))])],
                       cards={CARD: [_note()]})
        world.sweep()
        assert not any(
            reconcile.REVIEWER_DOWN_TAG in b for _, b in world.card_posts
        ), "the hold REPLACES reviewer-down for this class"


# --------------------------------------------------------------------------- #
# 2. a transient never holds                                                   #
# --------------------------------------------------------------------------- #


def test_one_crash_then_a_bound_verdict_holds_nothing():
    """ACCEPTANCE: one crash and then a verdict bound to the same head — no
    hold, no re-dispatch, nothing posted anywhere. The environment blipped and
    the retry worked, which is exactly what the one retry is for."""
    world = _World(
        [_pr(comments=[_redispatch_comment(_at(0)), _verdict_comment(_at(1))])],
        cards={CARD: [_note()]},
    )
    result = world.sweep()
    assert result.nudges == []
    assert result.pr_posts == [] and result.card_posts == []


# --------------------------------------------------------------------------- #
# 3. release signal (a) — a verdict anywhere in this repository                #
# --------------------------------------------------------------------------- #


class TestTheVerdictRelease:
    def _held_a_and_b(self):
        """PR A held on a crashed head; PR B an ordinary open pull request in
        the same repository, its own review never having crashed — it is here
        only to carry the verdict that proves the reviewer is back."""
        return _World(
            [_pr(number=2370, comments=[_redispatch_comment(_at(0)),
                                        _hold_comment(_at(1))]),
             _pr(number=2380, branch="agent/DRE-3432-other", sha=OLD_SHA)],
            cards={CARD: [_note(), _note()], "DRE-3432": []},
            checks={OLD_SHA: NO_REVIEW_CHECKS},
        )

    def test_a_verdict_on_another_pr_releases_the_held_head_once(self, capsys):
        """ACCEPTANCE: a hold on PR A, then a `VERDICT:` comment on PR B created
        after it — the next sweep re-dispatches PR A exactly once with a
        `crashed-review-redispatch` receipt, and the sweep after that dispatches
        nothing more."""
        world = self._held_a_and_b()
        # the reviewer is back: a verdict lands on PR B, after A's hold
        world._find(2380)["comments"].append(_verdict_comment(_at(2), OLD_SHA))

        released = world.sweep(capsys)
        assert [n for _, n in released.nudges] == [2370], (
            "the held head re-joins the dispatch queue on the first verdict"
        )
        assert len(_redispatch_receipts(released.pr_posts)) == 1, (
            "the release re-dispatch is receipted by the EXISTING tag — that "
            "receipt is the counter that lets a repeat crash hold again"
        )
        assert _holds(released.pr_posts) == []

        after = world.sweep(capsys)
        assert after.nudges == [], (
            "the re-dispatch receipt is newer than the hold, so the head is "
            "back on the ordinary bounded path — one dispatch, not one a sweep"
        )

    def test_the_log_line_names_the_signal_that_released_it(self, capsys):
        world = self._held_a_and_b()
        world._find(2380)["comments"].append(_verdict_comment(_at(2), OLD_SHA))
        log = world.sweep(capsys).log
        assert "2370" in log and "released" in log.lower()
        assert "verdict" in log.lower(), (
            "an operator reading the sweep log must see WHICH signal fired"
        )

    def test_a_verdict_older_than_the_hold_releases_nothing(self):
        """The verdict has to be NEWER than the hold, or every held head would
        release itself on the review history that preceded the outage."""
        world = _World(
            [_pr(number=2370, comments=[_redispatch_comment(_at(0)),
                                        _hold_comment(_at(3))]),
             _pr(number=2380, branch="agent/DRE-3432-other", sha=OLD_SHA,
                 comments=[_verdict_comment(_at(1), OLD_SHA)])],
            cards={CARD: [_note(), _note()]},
            checks={OLD_SHA: NO_REVIEW_CHECKS},
        )
        assert world.sweep().nudges == []

    def test_a_forged_verdict_releases_nothing(self):
        """DRE-1998 discipline: only the qa-bot's own QA Critic comments are
        visible, so nobody can release a hold by writing one."""
        world = self._held_a_and_b()
        world._find(2380)["comments"].append(
            _verdict_comment(_at(2), OLD_SHA, author="mallory")
        )
        assert world.sweep().nudges == []


# --------------------------------------------------------------------------- #
# 4. release signal (b) — the operator's re-run act on the held PR             #
# --------------------------------------------------------------------------- #


class TestTheOperatorAct:
    def test_the_whole_body_act_releases_the_hold_exactly_once(self, capsys):
        """ACCEPTANCE: a hold, then a comment on the held PR whose whole body is
        `▶️ re-run the review` — exactly one re-dispatch. This is the route for
        a repository with nothing else left to review."""
        world = _World(
            [_pr(comments=[_redispatch_comment(_at(0)), _hold_comment(_at(1)),
                           _act_comment(_at(2))])],
            cards={CARD: [_note(), _note()]},
        )
        released = world.sweep(capsys)
        assert [n for _, n in released.nudges] == [2370]
        assert len(_redispatch_receipts(released.pr_posts)) == 1
        assert "re-run" in released.log.lower()
        assert world.sweep(capsys).nudges == [], "one dispatch, not one a sweep"

    def test_the_same_string_inside_a_sentence_releases_nothing(self):
        """ACCEPTANCE: the WHOLE body or it is not the act — a notice that
        happens to quote the line would otherwise release every hold that
        ever stood (`review_rerun.is_rerun_act`'s rule)."""
        world = _World(
            [_pr(comments=[
                _redispatch_comment(_at(0)), _hold_comment(_at(1)),
                _act_comment(_at(2), body=(
                    f"I think we should {review_rerun.RERUN_REVIEW_ACT} once "
                    "the pin lands."
                )),
            ])],
            cards={CARD: [_note(), _note()]},
        )
        assert world.sweep().nudges == []

    def test_the_act_is_accepted_from_any_author(self):
        """The DRE-3428 contract: any author, because the bounded harm is one
        review dispatch — the same caveat `_review_checks_at_head` accepts."""
        world = _World(
            [_pr(comments=[_redispatch_comment(_at(0)), _hold_comment(_at(1)),
                           _act_comment(_at(2), author="some-human")])],
            cards={CARD: [_note(), _note()]},
        )
        assert [n for _, n in world.sweep().nudges] == [2370]


# --------------------------------------------------------------------------- #
# 5. a released head that crashes again                                        #
# --------------------------------------------------------------------------- #


def test_a_released_head_that_crashes_again_holds_again_never_dispatches(capsys):
    """ACCEPTANCE: a released head that crashes again with evidence gets one
    further hold whose body reads `again after release`, and no dispatch until a
    NEW release signal arrives."""
    world = _World(
        [_pr(comments=[_redispatch_comment(_at(0)), _hold_comment(_at(1)),
                       _act_comment(_at(2))])],
        cards={CARD: [_note(), _note()]},
    )
    released = world.sweep(capsys)
    assert [n for _, n in released.nudges] == [2370]

    # the released re-dispatch crashed the same way; the medic notes it again
    world.cards[CARD].append(_note())

    again = world.sweep(capsys)
    assert again.nudges == [], "no third dispatch"
    holds = _holds(again.pr_posts)
    assert len(holds) == 1, "one further hold"
    assert "again after release" in holds[0], (
        "the operator must be told the release rule fired and the runner is "
        "STILL broken — that is a different story from the first hold"
    )
    assert len(_holds(again.card_posts)) == 1

    quiet = world.sweep(capsys)
    assert quiet.nudges == [] and _holds(quiet.pr_posts) == [], (
        "nothing moves until a new release signal"
    )


def test_a_new_release_signal_re_arms_a_twice_held_head(capsys):
    """The second hold is a hold, not a park: a fresh signal releases it."""
    world = _World(
        [_pr(comments=[_redispatch_comment(_at(0)), _hold_comment(_at(1)),
                       _act_comment(_at(2))])],
        cards={CARD: [_note(), _note()]},
    )
    world.sweep(capsys)
    world.cards[CARD].append(_note())
    world.sweep(capsys)  # held again after release
    # the operator types it again, AFTER the second hold
    world._find(2370)["comments"].append(_act_comment(world._stamp()))
    assert [n for _, n in world.sweep(capsys).nudges] == [2370]


# --------------------------------------------------------------------------- #
# 6. a standing hold holds the next crash for free                             #
# --------------------------------------------------------------------------- #


def test_a_crash_with_evidence_beside_a_standing_hold_is_held_immediately(capsys):
    """ACCEPTANCE: a crashed head with evidence while an unreleased hold stands
    on ANOTHER pull request is held immediately, with zero dispatches for it.

    One runner, one environment: the retry this head would spend has already
    been spent next door and has already crashed. The second identical failure
    anywhere on this runner is the proof."""
    world = _World(
        [_pr(number=2370, comments=[_redispatch_comment(_at(0)),
                                    _hold_comment(_at(1))]),
         _pr(number=2380, branch="agent/DRE-3432-other", sha=OLD_SHA)],
        cards={CARD: [_note(), _note()],
               "DRE-3432": [_note(sha=OLD_SHA)]},
    )
    result = world.sweep(capsys)
    assert result.nudges == [], "zero dispatches for the newly crashed head"
    held = [(n, b) for n, b in result.pr_posts if renv.HOLD_TAG in b]
    assert [n for n, _ in held] == [2380], (
        "the newly crashed head is held; the one already held is not re-posted"
    )
    assert ("DRE-3432", held[0][1]) in result.card_posts


def test_the_free_hold_needs_evidence_of_its_own(capsys):
    """A crash with no named cause is NOT this class, however broken the runner
    next door is: it takes its own one re-dispatch, the way it always did."""
    world = _World(
        [_pr(number=2370, comments=[_redispatch_comment(_at(0)),
                                    _hold_comment(_at(1))]),
         _pr(number=2380, branch="agent/DRE-3432-other", sha=OLD_SHA)],
        cards={CARD: [_note(), _note()], "DRE-3432": []},
    )
    result = world.sweep(capsys)
    assert [n for _, n in result.nudges] == [2380]
    assert _holds(result.pr_posts) == []


def test_no_standing_hold_means_the_first_crash_still_spends_its_retry():
    """The free hold is gated on a hold STANDING somewhere. Once the release
    fires, the next crash is a first crash again."""
    world = _World(
        [_pr(number=2370, comments=[_redispatch_comment(_at(0)),
                                    _hold_comment(_at(1)),
                                    _act_comment(_at(2))]),
         _pr(number=2380, branch="agent/DRE-3432-other", sha=OLD_SHA)],
        cards={CARD: [_note(), _note()], "DRE-3432": [_note(sha=OLD_SHA)]},
    )
    dispatched = sorted(n for _, n in world.sweep().nudges)
    assert dispatched == [2370, 2380], (
        "2370 is released and 2380 is a first crash — both dispatch"
    )


# --------------------------------------------------------------------------- #
# 7. no evidence = the behaviour that was already here                         #
# --------------------------------------------------------------------------- #


class TestTheNoEvidencePathIsUntouched:
    def test_the_cap_still_reports_reviewer_down_with_no_note(self):
        """ACCEPTANCE: a crashed head with NO evidence note behaves exactly as
        before — one re-dispatch, then `reviewer-down`."""
        world = _World([_pr(comments=[_redispatch_comment(_at(0))])],
                       cards={CARD: []})
        result = world.sweep()
        assert result.nudges == []
        assert len(result.card_posts) == 1
        assert reconcile.REVIEWER_DOWN_TAG in result.card_posts[0][1]
        assert _holds(result.pr_posts) == [] and _holds(result.card_posts) == []

    def test_a_note_bound_to_an_older_head_is_history(self):
        """The sha binding is the whole point: a crash on a superseded commit
        does not hold the current one."""
        world = _World([_pr(comments=[_redispatch_comment(_at(0))])],
                       cards={CARD: [_note(sha=OLD_SHA)]})
        result = world.sweep()
        assert reconcile.REVIEWER_DOWN_TAG in result.card_posts[0][1]

    def test_a_branch_with_no_card_still_reports_the_way_it_did(self, capsys):
        """No card means nowhere to read a cause AND nowhere to mirror a hold —
        the pre-existing warning is what happens."""
        world = _World([_pr(branch="agent/nocard", sha=SHA,
                            comments=[_redispatch_comment(_at(0))])])
        result = world.sweep(capsys)
        assert result.card_posts == [] and _holds(result.pr_posts) == []
        assert "carries no card id" in result.log


# --------------------------------------------------------------------------- #
# 8. pacing                                                                    #
# --------------------------------------------------------------------------- #


def test_release_redispatches_are_paced_oldest_first_with_the_tail_logged(capsys):
    """ACCEPTANCE: release re-dispatches go through the existing `eligible`
    list, so they are capped per sweep by `CRASHED_REVIEW_SWEEP_CAP`, oldest PR
    first, with the deferred tail logged (the DRE-2049 burst lesson — every
    re-dispatch is a full critic run)."""
    numbers = [2410, 2370, 2400, 2380, 2390]
    prs = []
    cards = {}
    for n in numbers:
        card = f"DRE-{n}"
        prs.append(_pr(number=n, branch=f"agent/{card}-x",
                       comments=[_redispatch_comment(_at(0)),
                                 _hold_comment(_at(1)),
                                 _act_comment(_at(2))]))
        cards[card] = [_note(), _note()]
    world = _World(prs, cards=cards)
    result = world.sweep(capsys)
    assert [n for _, n in result.nudges] == sorted(numbers)[
        : reconcile.CRASHED_REVIEW_SWEEP_CAP
    ], "oldest PR first, capped per sweep"
    deferred = len(numbers) - reconcile.CRASHED_REVIEW_SWEEP_CAP
    assert f"{deferred} eligible PR(s) deferred" in result.log


# --------------------------------------------------------------------------- #
# 9. the contract: the sweep restates nothing and writes through one writer     #
# --------------------------------------------------------------------------- #


class TestTheContract:
    def test_the_sweep_imports_the_tag_and_never_spells_it(self):
        source = (ROOT / "scripts" / "reconcile.py").read_text("utf-8")
        assert "import reviewer_environment" in source
        assert f'"{renv.HOLD_TAG}"' not in source, (
            "the tag is `reviewer_environment.HOLD_TAG`, imported — a second "
            "spelling is a second thing to reword"
        )
        assert f'"{review_rerun.RERUN_REVIEW_ACT}"' not in source, (
            "the release act is `reviewer_environment.is_release_act`'s"
        )

    def test_the_sweep_composes_no_hold_body_of_its_own(self):
        """`post_hold` is the ONE writer, and `hold_receipt` the one body."""
        source = (ROOT / "scripts" / "reconcile.py").read_text("utf-8")
        assert "reviewer_environment.post_hold(" in source
        assert "reviewer_environment.hold_receipt(" in source
        assert f'receipt("{renv.HOLD_ACT}"' not in source, (
            "reconcile must not compose this act — reviewer_environment does"
        )

    def test_a_failed_hold_post_lands_on_the_fail_loudly_rail(self):
        """A hold nobody can see is a stall nobody can act on: the failure is
        recorded so the sweep exits 1 and the medic sees it (DRE-1254)."""
        world = _World([_pr(comments=[_redispatch_comment(_at(0))])],
                       cards={CARD: [_note()]}, pr_rc=1)
        world.sweep()
        assert any("runner-environment" in f or "hold" in f
                   for f in reconcile._write_failures), reconcile._write_failures


class TestTheSiblingBackstopIsUntouched:
    def test_the_fleet_outage_backstop_still_runs_right_after_this_one(self):
        """ACCEPTANCE: DRE-3435's `report_fleet_reviewer_outage` still sits
        immediately after `recover_crashed_reviews` in `main()`'s backstop
        tuple — the two share the one open-PR listing, so the order is
        load-bearing and this card leaves it alone."""
        source = (ROOT / "scripts" / "reconcile.py").read_text("utf-8")
        main = source[source.index("def main("):]
        tuple_text = main[main.index("for backstop in ("):]
        tuple_text = tuple_text[: tuple_text.index("\n        ):")]
        names = [
            line.strip().rstrip(",")
            for line in tuple_text.splitlines()
            if line.strip().rstrip(",").isidentifier()
        ]
        assert names.index("report_fleet_reviewer_outage") == (
            names.index("recover_crashed_reviews") + 1
        ), names

    def test_both_backstops_still_share_one_listing(self):
        """One `gh pr list` between the two, run in `main()`'s order."""
        reconcile.reset_sweep_cards()
        reconcile._swept_cards = []
        calls: list = []

        def fake_gh(*args):
            calls.append(tuple(args))
            if args[:2] == ("pr", "list"):
                return json.dumps([_pr(comments=[_verdict_comment(_at(1))])])
            return "[]"

        with patch.object(reconcile, "gh", side_effect=fake_gh), \
            patch.object(reconcile, "verdict_bound", return_value=True), \
            patch.object(reconcile.linear_ops, "gql", side_effect=AssertionError):
            reconcile.recover_crashed_reviews()
            reconcile.report_fleet_reviewer_outage()
        assert sum(1 for c in calls if c[:2] == ("pr", "list")) == 1, calls


# --------------------------------------------------------------------------- #
# 10. the cause read lives in the module that writes the note                  #
# --------------------------------------------------------------------------- #


class TestTheCauseIsReadWhereItIsWritten:
    def test_the_signature_comes_back_off_the_note(self):
        assert renv.signature_from_evidence([_note()], SHA) is SIGNATURE

    def test_the_newest_note_for_the_head_wins(self):
        other = renv.by_slug("credential-refused")
        found = renv.signature_from_evidence([_note(), _note(signature=other)], SHA)
        assert found is other, "the newest note is the current cause"

    def test_a_note_for_another_head_is_not_this_head_s_cause(self):
        assert renv.signature_from_evidence([_note(sha=OLD_SHA)], SHA) is None

    def test_no_notes_is_no_cause(self):
        assert renv.signature_from_evidence([], SHA) is None
        assert renv.signature_from_evidence(None, SHA) is None

    def test_the_hold_receipt_is_never_read_back_as_a_cause(self):
        """A hold carries no evidence marker, so it can never be mistaken for
        the note that justifies one."""
        assert renv.signature_from_evidence([_hold_body()], SHA) is None

    def test_a_slug_the_table_does_not_carry_is_refused_not_guessed(self):
        """A hold receipt naming the wrong cause sends an operator to the wrong
        check — DRE-3416's whole cost. An unreadable cause is reported, never
        invented."""
        mangled = _note().replace(SIGNATURE.slug, "something-else", 1)
        assert renv.signature_from_evidence([mangled], SHA) is None

    def test_the_sweep_never_parses_the_note_itself(self):
        """The contract: `reviewer_environment` owns the note's shape, so the
        sweep names neither the evidence marker nor the note's punctuation."""
        source = (ROOT / "scripts" / "reconcile.py").read_text("utf-8")
        assert f'"{renv.EVIDENCE_MARKER}"' not in source
        assert renv.EVIDENCE_MARKER not in source


def test_an_unreadable_cause_falls_through_to_reviewer_down():
    """The paired behaviour, end to end: the cap is spent, a note exists but
    names a cause this pipeline cannot resolve — so the report that names none
    is still better than a hold that names the wrong one."""
    world = _World(
        [_pr(comments=[_redispatch_comment(_at(0))])],
        cards={CARD: [_note().replace(SIGNATURE.slug, "not-a-signature", 1)]},
    )
    result = world.sweep()
    assert _holds(result.pr_posts) == []
    assert reconcile.REVIEWER_DOWN_TAG in result.card_posts[0][1]
