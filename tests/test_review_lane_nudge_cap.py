"""The review-lane nudges are capped per head and hand the card to a person
at the cap (DRE-5231).

The nudge loop's `In Review` + open-PR branch re-triggers the merge gate when a
critic verdict is bound to the head, and the review when none is. Uncapped,
both fire every `STALE_MINUTES["In Review"]` minutes forever: a receipt touches
the card, the window runs out again, and nothing counts. A pull request with
APPROVE, green CI and a Verifier FAIL is the live case — the gate runs, decides
`hold` and declines, and the receipt used to say the opposite of the truth.

The cap is the CRASHED_REVIEW_RETRY_CAP shape: the card's own receipts are
counted per tag AND per head sha, a new commit re-arms the budget, and at the
cap nothing is dispatched — `needs-human` goes on with its `🔒 hold:` stamp,
one `🚨 review-nudge-cap` question says what stands and the way back, and the
card is parked In Review → Green Light (DRE-6181,
tests/test_review_cap_green_light.py).

The sweep is driven the way test_lane_fold_in_review.py drives it — the real
`main()` — over a board that remembers what each sweep posted and labelled, so
sweep N reads exactly the receipts sweeps 1..N-1 left.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")
os.environ.setdefault("GH_TOKEN", "x")

import reconcile  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

HEAD = "a" * 40
NEW_HEAD = "c" * 40
OLD_HEAD = "b" * 40
IDENT = "DRE-5231"
# The strings the merge gate reads as verdict credentials. A receipt the sweep
# writes must never carry one (standards/untrusted-content.md).
VERDICT_MARKERS = ("VERDICT:", "QA Critic", "QA Verifier")


@pytest.fixture(autouse=True)
def _clean_failure_state(monkeypatch):
    monkeypatch.setattr(reconcile, "REPO_SLUG", "agent-bureau")
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()
    yield
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()


def _qa(body):
    return {"author": {"login": reconcile.QA_BOT_LOGIN}, "body": body,
            "createdAt": "2026-09-30T00:00:00Z"}


def _pr(head=HEAD, critic=None, verifier=None, reviewed=None):
    """An open PR whose qa-bot thread carries the given verdict tokens, bound
    to `reviewed` (default `head`). No baseRefName, so the content carry is
    never asked about and verdict_bound reads the sha binding alone unless a
    test patches the carry in."""
    reviewed = reviewed or head
    comments = []
    if critic:
        comments.append(_qa(f"🔎 QA Critic — VERDICT: {critic} @{reviewed}\n\nok"))
    if verifier:
        comments.append(_qa(f"🧪 QA Verifier — VERDICT: {verifier} @{reviewed}\n\nno"))
    return {
        "number": 42,
        "headRefName": "agent/DRE-5231-review-nudge-cap",
        "state": "OPEN",
        "comments": comments,
        "headRefOid": head,
    }


class _Board:
    """One card that remembers every comment, label and lane move the sweep
    wrote, and the pull request notes it posted (DRE-6181)."""

    def __init__(self):
        self.comments: list[str] = []  # oldest -> newest
        self.labels: list[str] = []
        self.state = "In Review"
        self.pr_notes: list[str] = []

    def card(self):
        return {
            "identifier": IDENT,
            "description": "**Repo:** agent-bureau\nwork",
            "state": {"name": self.state},
            "labels": {"nodes": [{"name": n} for n in self.labels]},
            "updatedAt": "2026-09-30T00:00:00Z",
            # The API's order — newest first; window_nodes reverses it.
            "comments": {"nodes": [
                {"body": b, "createdAt": "2026-09-30T00:00:00Z"}
                for b in reversed(self.comments)
            ]},
        }

    def sweep(self, pr):
        """Run one full sweep; return (nudge mock, cmd_state mock, posted)."""
        posted: list[str] = []

        def comment(ident, body, *_flags):
            assert ident == IDENT
            posted.append(body)
            self.comments.append(body)

        def label(ident, name):
            assert ident == IDENT
            if name not in self.labels:
                self.labels.append(name)

        def advance(ident, to, from_csv, *_flags, **_kw):
            assert ident == IDENT
            if self.state in from_csv.split(","):
                self.state = to

        def pr_note(number, body):
            # Posted as the worker bot, so the next sweep's pull request
            # carries it the way GitHub would.
            self.pr_notes.append(body)
            pr["comments"].append({"author": {"login": reconcile.WORKER_BOT_LOGIN},
                                   "body": body})
            return True

        mocks = {
            "unstick_conflicts": MagicMock(),
            "retrigger_dead_heads": MagicMock(),
            "check_dependabot_capacity": MagicMock(),
            "fix_approved_but_red": MagicMock(),
            "close_finished_epics": MagicMock(),
            "promote_ready": MagicMock(return_value=0),
            "age_minutes": MagicMock(return_value=999),
            "pr_for": MagicMock(return_value=pr),
            "redispatch": MagicMock(return_value=True),
            "active_cards": MagicMock(return_value=[self.card()]),
            "flag_stranded": MagicMock(return_value=set()),
            "_nudge": MagicMock(return_value=True),
            # The PR-level backstops' silent reads answer "nothing" — the
            # same answer a real `gh` gives here with no token, without the
            # subprocess per read that makes a five-sweep test take a minute.
            "gh": MagicMock(return_value=""),
            "report_fix_concurrency": MagicMock(),
            "_post_pr_note": MagicMock(side_effect=pr_note),
        }
        with patch.multiple(reconcile, **mocks), patch.object(
            reconcile.linear_ops, "cmd_comment", side_effect=comment
        ), patch.object(
            reconcile.linear_ops, "add_label", side_effect=label
        ), patch.object(
            reconcile.linear_ops, "cmd_advance", side_effect=advance
        ), patch.object(reconcile.linear_ops, "cmd_state") as cmd_state:
            reconcile.main()
        self.last_pr_for = mocks["pr_for"]
        return mocks["_nudge"], cmd_state, posted


def _cap_notices(bodies):
    return [b for b in bodies if "🚨 review-nudge-cap" in b]


def _stamps(bodies):
    return [b for b in bodies if b.startswith("🔒 hold: reason=review-cap-spent")]


class TestTheCapConstant:
    def test_the_default_is_three_windows(self):
        assert reconcile.REVIEW_NUDGE_CAP == 3

    def test_the_operator_can_set_it_without_a_deploy(self):
        source = (ROOT / "scripts" / "reconcile.py").read_text(encoding="utf-8")
        assert (
            'REVIEW_NUDGE_CAP = int(os.environ.get("REVIEW_NUDGE_CAP", "3"))'
            in source
        )


class TestTheGateNudgeIsCapped:
    """APPROVE + Verifier FAIL on the head: the gate holds, every time."""

    def test_three_receipts_then_a_hand_off_then_a_held_card(self):
        board = _Board()
        pr = _pr(critic="APPROVE", verifier="FAIL")

        for n in (1, 2, 3):
            nudge, cmd_state, posted = board.sweep(pr)
            nudge.assert_called_once_with("merge-gate.yml", 42)
            cmd_state.assert_not_called()
            assert len(posted) == 1, posted
            receipt = posted[0]
            assert f"gate-nudge @{HEAD} ({n}/3)" in receipt
            assert "critic APPROVE, verifier FAIL" in receipt
            assert "merge gate re-triggered" in receipt
            assert reconcile.HOLD_LABEL not in board.labels

        # Sweep 4: the budget for this head is spent — held, asked, parked.
        nudge, cmd_state, posted = board.sweep(pr)
        nudge.assert_not_called()
        cmd_state.assert_not_called()
        assert board.state == "Green Light"  # DRE-6181: the CEO's queue
        assert reconcile.HOLD_LABEL in board.labels
        assert len(_stamps(posted)) == 1, posted
        notices = _cap_notices(posted)
        assert len(notices) == 1, posted
        assert len(posted) == 2, posted  # the stamp, then the question
        notice = notices[0]
        assert notice.startswith(f"🚨 review-nudge-cap PR #42 @{HEAD}:")
        assert "critic APPROVE" in notice and "verifier FAIL" in notice
        assert "3 re-trigger" in notice
        assert "declined" in notice
        assert len(board.pr_notes) == 1

        # Sweep 5: held — the sweep does not even look the PR up.
        nudge, cmd_state, posted = board.sweep(pr)
        nudge.assert_not_called()
        board.last_pr_for.assert_not_called()
        assert posted == []

    def test_the_notice_says_how_long_the_re_triggers_ran(self):
        board = _Board()
        pr = _pr(critic="APPROVE", verifier="FAIL")
        for _ in range(4):
            board.sweep(pr)
        notice = _cap_notices(board.comments)[0]
        # age_minutes is pinned at 999 by the harness: 16.65 hours.
        assert re.search(r"over 16\.\dh", notice), notice

    def test_the_receipt_names_none_when_no_verifier_ran(self):
        board = _Board()
        _, _, posted = board.sweep(_pr(critic="APPROVE"))
        assert "critic APPROVE, verifier none" in posted[0]


class TestAVerdictCarriedByContentIsNamed:
    """The head moved but the PR's content did not — a base merge. The critic's
    APPROVE is carried by content (DRE-2340), so verdict_bound is True and the
    gate is nudged; the receipt and the notice must name that APPROVE, never
    print "critic none" for the verdict the sweep is acting on."""

    def test_the_receipt_and_the_notice_name_the_earlier_head(self):
        board = _Board()
        pr = _pr(critic="APPROVE", verifier="FAIL", reviewed=OLD_HEAD)
        with patch.object(
            reconcile, "head_content_id_for", return_value="content"
        ), patch.object(
            reconcile, "pr_commit_shas_for", return_value=frozenset({OLD_HEAD})
        ), patch.object(
            reconcile.merge_gate, "carries_content", return_value=True
        ):
            assert reconcile.verdict_bound(pr)
            for n in (1, 2, 3):
                nudge, _, posted = board.sweep(pr)
                nudge.assert_called_once_with("merge-gate.yml", 42)
                receipt = posted[0]
                assert f"gate-nudge @{HEAD} ({n}/3)" in receipt
                assert (
                    f"critic APPROVE from earlier head {OLD_HEAD[:7]}, "
                    f"verifier FAIL from earlier head {OLD_HEAD[:7]}" in receipt
                ), receipt
                assert "none" not in receipt
                assert "bound to this head" not in receipt
            nudge, _, posted = board.sweep(pr)
        nudge.assert_not_called()
        notice = _cap_notices(posted)[0]
        assert f"critic APPROVE from earlier head {OLD_HEAD[:7]}" in notice
        assert "none" not in notice
        assert "bound to this head" not in notice


class TestTheReviewNudgeIsCapped:
    def test_three_receipts_then_the_same_hand_off(self):
        board = _Board()
        pr = _pr()

        for n in (1, 2, 3):
            nudge, cmd_state, posted = board.sweep(pr)
            nudge.assert_called_once_with("qa-review.yml", 42)
            assert len(posted) == 1, posted
            assert (
                f"review-nudge @{HEAD} ({n}/3) — no critic verdict bound to "
                "this head after 2h; review re-triggered." in posted[0]
            )

        nudge, cmd_state, posted = board.sweep(pr)
        nudge.assert_not_called()
        cmd_state.assert_not_called()
        assert board.state == "Green Light"
        assert reconcile.HOLD_LABEL in board.labels
        assert len(_cap_notices(posted)) == 1
        assert _cap_notices(posted)[0].startswith(
            f"🚨 review-nudge-cap PR #42 @{HEAD}:"
        )

        nudge, _, posted = board.sweep(pr)
        nudge.assert_not_called()
        assert posted == []

    def test_the_two_tags_are_counted_apart(self):
        # Three review nudges on a head do not spend the gate's budget: once
        # the critic speaks, the gate gets its own three.
        board = _Board()
        for _ in range(3):
            board.sweep(_pr())
        nudge, _, posted = board.sweep(_pr(critic="APPROVE"))
        nudge.assert_called_once_with("merge-gate.yml", 42)
        assert f"gate-nudge @{HEAD} (1/3)" in posted[0]


class TestANewHeadReArmsTheBudget:
    def test_gate_receipts_on_the_old_head_do_not_count(self):
        board = _Board()
        for _ in range(3):
            board.sweep(_pr(critic="APPROVE", verifier="FAIL"))
        nudge, _, posted = board.sweep(
            _pr(head=NEW_HEAD, critic="APPROVE", verifier="FAIL")
        )
        nudge.assert_called_once_with("merge-gate.yml", 42)
        assert f"gate-nudge @{NEW_HEAD} (1/3)" in posted[0]
        assert reconcile.HOLD_LABEL not in board.labels

    def test_review_receipts_on_the_old_head_do_not_count(self):
        board = _Board()
        for _ in range(3):
            board.sweep(_pr())
        nudge, _, posted = board.sweep(_pr(head=NEW_HEAD))
        nudge.assert_called_once_with("qa-review.yml", 42)
        assert f"review-nudge @{NEW_HEAD} (1/3)" in posted[0]
        assert reconcile.HOLD_LABEL not in board.labels


class TestTheNoticeIsPostedOncePerHead:
    def test_a_label_removed_with_the_head_unmoved_re_holds_without_a_second_notice(self):
        board = _Board()
        pr = _pr(critic="APPROVE", verifier="FAIL")
        for _ in range(4):
            board.sweep(pr)
        assert len(_cap_notices(board.comments)) == 1
        # A person, nothing pushed: the label off, the card back In Review.
        board.labels.remove(reconcile.HOLD_LABEL)
        board.state = "In Review"

        nudge, _, posted = board.sweep(pr)
        nudge.assert_not_called()
        assert reconcile.HOLD_LABEL in board.labels
        assert len(_stamps(posted)) == 1  # re-held under its reason
        assert board.state == "Green Light"  # and re-parked
        assert _cap_notices(posted) == []
        assert len(_cap_notices(board.comments)) == 1
        assert len(board.pr_notes) == 1


class TestTheCapIsOffAtZero:
    def test_zero_dispatches_nothing_and_hands_off_on_the_first_stale_sweep(
        self, monkeypatch, capsys
    ):
        monkeypatch.setattr(reconcile, "REVIEW_NUDGE_CAP", 0)
        board = _Board()
        nudge, cmd_state, posted = board.sweep(
            _pr(critic="APPROVE", verifier="FAIL")
        )
        nudge.assert_not_called()
        cmd_state.assert_not_called()
        assert board.state == "Green Light"
        assert reconcile.HOLD_LABEL in board.labels
        assert len(_cap_notices(posted)) == 1
        assert "REVIEW_NUDGE_CAP is 0 — the cap is off" in capsys.readouterr().out


class TestTheReceiptsCarryNoVerdictMarker:
    def test_no_body_the_sweep_wrote_reads_as_a_verdict(self):
        gate, review = _Board(), _Board()
        for _ in range(4):
            gate.sweep(_pr(critic="APPROVE", verifier="FAIL"))
            review.sweep(_pr())
        bodies = (gate.comments + review.comments
                  + gate.pr_notes + review.pr_notes)
        # Three receipts, a stamp and a question each, and one blocker each.
        assert len(bodies) == 12
        for body in bodies:
            for marker in VERDICT_MARKERS:
                assert marker not in body, body


class TestTheActRegistry:
    def _registry(self):
        return json.loads(
            (ROOT / "config" / "pipeline-acts.json").read_text(encoding="utf-8")
        )

    def test_the_three_sites_are_declared_together_in_place(self):
        anchors = [
            entry.get("anchor") for entry in self._registry()["unconverted"]
        ]
        gate = anchors.index("has not merged it; merge gate re-triggered")
        assert anchors[gate + 1] == "no critic verdict bound to this head after"
        # DRE-6181: the question and the pull request blocker replaced the
        # bare notice.
        assert anchors[gate + 2] == "cmd_comment(ident, review_question)"
        assert anchors[gate + 3] == "_post_pr_note(number, review_blocker)"
        # The old wording is gone, not left beside the new.
        assert "verdict present but merge never happened" not in anchors
        assert "no critic verdict after" not in anchors
        assert "Re-triggering again would not change that" not in anchors

    def test_every_receipt_site_is_accounted_for(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "check_act_receipts.py")],
            capture_output=True, text=True, cwd=ROOT,
        )
        assert result.returncode == 0, result.stdout + result.stderr


class TestTheDocumentation:
    def test_the_readme_sweep_section_names_the_cap_and_the_way_back(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        start = text.index("## The sweep reads every row (DRE-2681)")
        end = text.index("\n## ", start + 1)
        section = text[start:end]
        assert "REVIEW_NUDGE_CAP" in section
        assert "`needs-human`" in section
        # DRE-6181: parked in Green Light, back on a new head.
        assert "`Green Light`" in section
        assert "Operator decision" in section
        assert "new head" in section

    def test_the_branch_comment_leaves_the_hold_note_to_the_gate(self):
        source = (ROOT / "scripts" / "reconcile.py").read_text(encoding="utf-8")
        branch = source[source.index("elif state == REVIEW_LANE and is_open:"):]
        branch = branch[:branch.index("elif state == REVIEW_LANE and not is_open:")]
        assert "DRE-5228" in branch


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
