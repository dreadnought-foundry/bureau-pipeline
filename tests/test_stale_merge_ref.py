"""A PR red on a fault `main` has since fixed (DRE-3138).

Origin (live, 2026-09-02 00:00–00:23 PT): agent-bureau PRs #2240 and #2241 both
went red on `Console backend (pytest)` because of a fault on `main` (DRE-2962).
The fix merged to `main` at 00:02 and both PRs stayed red, because their CI had
run against a merge ref computed before the fix and nothing recomputes one when
`main` moves. `gh run rerun` re-runs against the SAME merge commit, so only a
new head — an `update-branch` merge of `main` into the branch — gets a fresh
merge ref.

`scripts/stale_merge_ref.py` is the pure decision behind that refresh, in the
shape `inherited_failures.py` and `red_main_repair.py` already carry: pure
functions over GitHub payloads, no I/O, a CLI for humans, and every unreadable
input answering UNEVALUATED rather than a pass.

The three facts a refresh needs, and what each one rules out:

  * `main` has moved past the merge base (`behind_by > 0`) — otherwise there is
    nothing a refresh could change;
  * every failing check on the head also fails on the MERGE BASE — otherwise
    the PR has its own defect and the fix loop owns it;
  * every one of them is GREEN on the `main` TIP — otherwise `main` is still
    red, the Red-Main Repair loop owns it, and a refresh would only re-inherit
    the failure.
"""

import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from dataclasses import FrozenInstanceError, fields
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import stale_merge_ref as smr  # noqa: E402

MODULE = ROOT / "scripts" / "stale_merge_ref.py"

HEAD_SHA = "a" * 40
BASE_SHA = "b" * 40   # the merge base
MAIN_SHA = "c" * 40   # the tip of main
PYTEST = "Console backend (pytest)"


def runs(*pairs):
    """A check-runs payload in the shape `gh api` returns it. Each pair is
    (name, conclusion); a conclusion of None is a run still in flight."""
    return {
        "check_runs": [
            {
                "name": name,
                "status": "completed" if conclusion else "in_progress",
                "conclusion": conclusion,
            }
            for name, conclusion in pairs
        ]
    }


def compare(behind_by=3, base_sha=BASE_SHA, main_sha=MAIN_SHA):
    """The raw `GET repos/{repo}/compare/{base}...{head}` payload. It carries
    the merge base AND the tip of `main` (`base_commit`) in one read."""
    return {
        "status": "diverged",
        "behind_by": behind_by,
        "ahead_by": 2,
        "merge_base_commit": {"sha": base_sha},
        "base_commit": {"sha": main_sha},
    }


def tip(payload, sha=MAIN_SHA):
    """A one-commit window: the `main` tip alone, which answers exactly as
    the tip-only rule did before DRE-6513."""
    return [(sha, payload)]


def decide(**overrides):
    kwargs = {
        "compare": compare(),
        "head_checks": runs((PYTEST, "failure")),
        "base_checks": runs((PYTEST, "failure")),
        "main_window": tip(runs((PYTEST, "success"))),
        "receipts": [],
        "cap": 2,
    }
    kwargs.update(overrides)
    return smr.decide(**kwargs)


class ContractTest(unittest.TestCase):
    """The strings and the shape the sweep card and the console card bind to."""

    def test_the_tag_is_the_literal_the_act_registry_reads(self):
        self.assertEqual("stale-merge-ref-refresh", smr.REFRESH_TAG)
        # The registry reads `X_TAG = "..."` constants off the emitter file, so
        # the value must be a literal in the source, not a computed string.
        self.assertIn('REFRESH_TAG = "stale-merge-ref-refresh"',
                      MODULE.read_text(encoding="utf-8"))

    def test_the_marker_binds_the_refresh_to_a_main_commit(self):
        self.assertEqual(f"{smr.REFRESH_TAG} @{MAIN_SHA}", smr.marker(MAIN_SHA))

    def test_the_decision_is_a_frozen_dataclass_with_the_agreed_fields(self):
        decision = decide()
        self.assertEqual(
            ["action", "reason", "inherited", "base_sha", "main_sha",
             "behind_by", "evidence"],
            [f.name for f in fields(decision)],
        )
        with self.assertRaises(FrozenInstanceError):
            decision.action = "refresh"

    def test_the_actions_are_exactly_the_declared_eight(self):
        self.assertEqual(
            {"refresh", "current", "no-failure", "own", "main-still-red",
             "unevaluated", "already-refreshed", "cap-spent"},
            set(smr.ACTIONS),
        )

    def test_the_emitter_anchor_phrase_appears_exactly_once(self):
        # config/pipeline-acts.json will pin this phrase as the emitter anchor,
        # and pipeline_act.problems() fails on a count other than 1.
        self.assertEqual(
            1,
            MODULE.read_text(encoding="utf-8").count(smr.ANCHOR_PHRASE),
        )

    def test_the_anchor_phrase_is_the_agreed_wording(self):
        self.assertEqual(
            "refreshed the merge ref: the fault was on main, not in this "
            "pull request",
            smr.ANCHOR_PHRASE,
        )


class RefreshTest(unittest.TestCase):
    def test_a_fault_main_has_fixed_is_a_refresh(self):
        decision = decide()
        self.assertEqual("refresh", decision.action)
        self.assertEqual([PYTEST], decision.inherited)
        self.assertEqual(MAIN_SHA, decision.main_sha,
                         "main_sha is the compare's base_commit.sha")
        self.assertEqual(BASE_SHA, decision.base_sha)
        self.assertEqual(3, decision.behind_by)
        self.assertTrue(decision.reason)
        self.assertEqual(1, len(decision.reason.splitlines()))

    def test_the_heads_spelling_and_order_survive(self):
        decision = decide(
            head_checks=runs(("Web unit tests", "failure"), (PYTEST, "failure")),
            base_checks=runs((PYTEST.lower(), "failure"),
                             ("web  unit tests", "failure")),
            main_window=tip(runs((PYTEST, "success"),
                                 ("Web unit tests", "success"))),
        )
        self.assertEqual("refresh", decision.action)
        self.assertEqual(["Web unit tests", PYTEST], decision.inherited)


class OwnDefectTest(unittest.TestCase):
    def test_a_check_green_on_the_merge_base_is_the_prs_own(self):
        decision = decide(base_checks=runs((PYTEST, "success")))
        self.assertEqual("own", decision.action)

    def test_own_even_when_main_has_moved_and_is_green(self):
        decision = decide(
            compare=compare(behind_by=42),
            head_checks=runs((PYTEST, "failure"), ("Web unit tests", "failure")),
            base_checks=runs((PYTEST, "failure")),
            main_window=tip(runs((PYTEST, "success"),
                                 ("Web unit tests", "success"))),
        )
        self.assertEqual("own", decision.action,
                         "one uninherited failure is enough — the fix loop owns it")

    def test_a_cancelled_base_run_proves_nothing(self):
        # The rule inherited_failures/unfixable_checks already apply: a
        # cancelled run never reported, so it cannot excuse a red head.
        decision = decide(base_checks=runs((PYTEST, "cancelled")))
        self.assertEqual("own", decision.action)


class MainStillRedTest(unittest.TestCase):
    def test_still_failing_on_the_main_tip_is_the_repair_loops_job(self):
        decision = decide(main_window=tip(runs((PYTEST, "failure"))))
        self.assertEqual("main-still-red", decision.action)
        self.assertIn(PYTEST, decision.reason)

    def test_no_completed_run_on_main_yet_is_unevaluated(self):
        decision = decide(main_window=tip(runs((PYTEST, None))))
        self.assertEqual("unevaluated", decision.action,
                         "main's CI is still running — try again next sweep")

    def test_absent_from_main_entirely_is_unevaluated_never_a_refresh(self):
        decision = decide(main_window=tip(runs(("Web unit tests", "success"))))
        self.assertEqual("unevaluated", decision.action)

    def test_a_completed_run_that_is_neither_green_nor_red_is_unevaluated(self):
        decision = decide(main_window=tip(runs((PYTEST, "skipped"))))
        self.assertEqual("unevaluated", decision.action)

    def test_one_red_and_one_unfinished_reports_the_red(self):
        decision = decide(
            head_checks=runs((PYTEST, "failure"), ("Web unit tests", "failure")),
            base_checks=runs((PYTEST, "failure"), ("Web unit tests", "failure")),
            main_window=tip(runs((PYTEST, "failure"), ("Web unit tests", None))),
        )
        self.assertEqual("main-still-red", decision.action,
                         "a definite red on main beats an unfinished run")


class NothingToDoTest(unittest.TestCase):
    def test_behind_by_zero_is_current_regardless_of_check_state(self):
        decision = decide(
            compare=compare(behind_by=0),
            main_window=tip(runs((PYTEST, "failure"))),
        )
        self.assertEqual("current", decision.action)
        self.assertEqual(0, decision.behind_by)

    def test_a_green_head_is_no_failure(self):
        decision = decide(head_checks=runs((PYTEST, "success")))
        self.assertEqual("no-failure", decision.action)
        self.assertEqual([], decision.inherited)

    def test_a_review_named_failing_check_is_not_ci(self):
        # `fix_approved_but_red` excludes these for the same reason: a critic
        # verdict check is a review outcome, not a CI result.
        decision = decide(head_checks=runs(("qa review", "failure")))
        self.assertEqual("no-failure", decision.action)

    def test_a_review_named_check_is_excluded_from_a_real_failing_set(self):
        decision = decide(
            head_checks=runs((PYTEST, "failure"), ("agent-bureau review", "failure")),
            base_checks=runs((PYTEST, "failure")),
            main_window=tip(runs((PYTEST, "success"))),
        )
        self.assertEqual("refresh", decision.action)
        self.assertEqual([PYTEST], decision.inherited,
                         "the review check never enters F, so it never has to "
                         "be inherited or green on main")


class BudgetTest(unittest.TestCase):
    def test_a_receipt_for_this_main_commit_means_already_refreshed(self):
        decision = decide(receipts=[f"body\n{smr.marker(MAIN_SHA)}\nmore"])
        self.assertEqual("already-refreshed", decision.action)

    def test_a_receipt_for_a_different_main_commit_does_not_block(self):
        decision = decide(receipts=[smr.marker("d" * 40)], cap=3)
        self.assertEqual("refresh", decision.action)

    def test_receipts_at_the_cap_are_spent(self):
        decision = decide(
            receipts=[smr.marker("d" * 40), smr.marker("e" * 40)], cap=2)
        self.assertEqual("cap-spent", decision.action)

    def test_a_cap_of_zero_is_the_operators_off_switch(self):
        decision = decide(cap=0)
        self.assertEqual("cap-spent", decision.action)

    def test_only_receipts_carrying_the_tag_are_counted(self):
        decision = decide(receipts=["an unrelated bot comment"], cap=1)
        self.assertEqual("refresh", decision.action)

    def test_comment_objects_are_read_as_well_as_bodies(self):
        decision = decide(receipts=[{"body": smr.marker(MAIN_SHA)}])
        self.assertEqual("already-refreshed", decision.action)


class UnreadableInputTest(unittest.TestCase):
    """Every unreadable input answers unevaluated — never a pass."""

    def test_an_unreadable_compare(self):
        for payload in ({}, [], {"behind_by": "three"}, "nope",
                        {"behind_by": 1, "base_commit": {}}):
            with self.subTest(payload=payload):
                self.assertEqual("unevaluated", decide(compare=payload).action)

    def test_an_unreadable_head_payload(self):
        self.assertEqual("unevaluated", decide(head_checks="nope").action)

    def test_an_unreadable_merge_base_payload(self):
        self.assertEqual("unevaluated", decide(base_checks=["nope"]).action)

    def test_an_unreadable_main_payload(self):
        self.assertEqual("unevaluated", decide(main_window=tip(42)).action)

    def test_unreadable_receipts(self):
        self.assertEqual("unevaluated", decide(receipts=None).action,
                         "we cannot tell whether we already refreshed")


class ReceiptDetailTest(unittest.TestCase):
    def setUp(self):
        self.detail = smr.receipt_detail(
            pr_number=2240,
            head_sha=HEAD_SHA,
            main_sha=MAIN_SHA,
            base_sha=BASE_SHA,
            inherited=[PYTEST],
            used=1,
            cap=2,
        )

    def test_it_opens_with_the_marker(self):
        self.assertTrue(self.detail.startswith(smr.marker(MAIN_SHA)),
                        self.detail.splitlines()[0])

    def test_it_names_the_main_commit_and_the_checks(self):
        self.assertIn(MAIN_SHA[:8], self.detail)
        self.assertIn(BASE_SHA[:8], self.detail)
        self.assertIn(PYTEST, self.detail)

    def test_it_says_the_branch_was_refreshed_with_update_branch(self):
        self.assertIn("update-branch", self.detail)
        self.assertIn("1/2", self.detail)

    def test_it_carries_the_anchor_phrase(self):
        self.assertIn(smr.ANCHOR_PHRASE, self.detail)

    def test_it_states_the_verdict_cost_honestly(self):
        lowered = self.detail.lower()
        self.assertIn("carries", lowered)
        self.assertIn("discharged", lowered)
        self.assertIn("verdict_content.py", self.detail)
        self.assertIn("DRE-2340", self.detail)

    def test_it_explains_that_the_new_head_is_a_merge_of_main(self):
        self.assertIn("merge of `main`", self.detail)

    def test_it_emits_no_verdict_marker(self):
        for forbidden in ("VERDICT:", "QA Critic", "QA Verifier"):
            self.assertNotIn(forbidden, self.detail)

    def test_it_is_not_blocker_shaped(self):
        # fix_context.py reads a bot comment whose FIRST line opens with 🛑 as
        # a prior fix-loop blocker. This is a recovery receipt, not a blocker.
        self.assertFalse(self.detail.splitlines()[0].startswith("🛑"))

    def test_the_whole_module_emits_no_verdict_marker(self):
        source = MODULE.read_text(encoding="utf-8")
        for forbidden in ("VERDICT:", "QA Critic", "QA Verifier"):
            self.assertNotIn(forbidden, source)


# --------------------------------------------------------------------------- #
# DRE-6513 — `main`'s own first-parent commits since the merge base            #
# --------------------------------------------------------------------------- #

S1 = "Console backend shard 1"
S2 = "Console backend shard 2"
OLD_SHA = "d" * 40    # a `main` merge older than the tip
OLDER_SHA = "e" * 40  # older still


def window(*pairs):
    """A `main_window`, tip first: each pair is (sha, check-runs payload)."""
    return list(pairs)


def commit(sha, *parents):
    """One entry of `GET repos/{repo}/commits?sha=…`, in the shape GitHub
    returns it: the sha and the parents, first parent first."""
    return {"sha": sha, "parents": [{"sha": p} for p in parents]}


class FirstParentWindowTest(unittest.TestCase):
    """The pure cut of the commit listing into `main`'s own merges."""

    def setUp(self):
        # m0 (tip) <- m1 <- m2 <- m3 (the merge base) <- m4, first parents.
        # Each merge brought in a pull request's commits through its SECOND
        # parent, and the listing (newest first, by date) holds those too.
        self.m = [f"{i:x}" * 40 for i in range(1, 6)]
        self.pr = [f"{i:x}" * 40 for i in range(10, 15)]
        m, pr = self.m, self.pr
        self.listing = [
            commit(m[0], m[1], pr[0]),
            commit(pr[0], pr[1]),
            commit(pr[1], m[2]),
            commit(m[1], m[2], pr[2]),
            commit(pr[2], m[3]),
            commit(m[2], m[3], pr[3]),
            commit(pr[3], pr[4]),
            commit(pr[4], m[3]),
            commit(m[3], m[4]),
            commit(m[4], "f" * 40),
        ]

    def test_only_first_parent_shas_tip_first_stopping_before_the_merge_base(self):
        got = smr.first_parent_window(self.listing, tip_sha=self.m[0],
                                      base_sha=self.m[3])
        self.assertEqual(self.m[:3], got)
        for sha in self.pr:
            self.assertNotIn(sha, got, "a pull request's own commit is never "
                                       "`main` itself")

    def test_it_stops_at_the_limit(self):
        got = smr.first_parent_window(self.listing, tip_sha=self.m[0],
                                      base_sha="9" * 40, limit=2)
        self.assertEqual(self.m[:2], got)

    def test_the_default_limit_is_main_window(self):
        self.assertEqual(12, smr.MAIN_WINDOW)
        chain = [f"{i:040x}" for i in range(1, 30)]
        listing = [commit(sha, nxt) for sha, nxt in zip(chain, chain[1:])]
        got = smr.first_parent_window(listing, tip_sha=chain[0],
                                      base_sha="9" * 40)
        self.assertEqual(chain[:smr.MAIN_WINDOW], got)

    def test_it_stops_when_the_next_first_parent_is_not_listed(self):
        # A merge base off the walk: the window runs until the listing ends.
        got = smr.first_parent_window(self.listing, tip_sha=self.m[0],
                                      base_sha="9" * 40)
        self.assertEqual(self.m, got, "m4's first parent is not in the listing")

    def test_a_tip_not_in_the_listing_is_an_empty_window(self):
        self.assertEqual([], smr.first_parent_window(
            self.listing, tip_sha="9" * 40, base_sha=self.m[3]))

    def test_a_listing_that_is_not_a_list_is_an_empty_window(self):
        for listing in ({"message": "Not Found"}, None, "nope", 42):
            with self.subTest(listing=listing):
                self.assertEqual([], smr.first_parent_window(
                    listing, tip_sha=self.m[0], base_sha=self.m[3]))


class WindowRuleTest(unittest.TestCase):
    """The table in DRE-6513: a check not red on the merge base is still
    `main`'s fault when `main` went red on it since and is green again."""

    def test_green_base_older_red_newer_green_is_a_refresh(self):
        decision = decide(
            base_checks=runs((PYTEST, "success")),
            main_window=window((MAIN_SHA, runs((PYTEST, "success"))),
                               (OLD_SHA, runs((PYTEST, "failure")))),
        )
        self.assertEqual("refresh", decision.action, decision.reason)
        self.assertEqual([PYTEST], decision.inherited)
        self.assertEqual(
            [{"check": PYTEST, "base_red": False, "red_sha": OLD_SHA,
              "green_sha": MAIN_SHA}],
            decision.evidence,
        )

    def test_a_merge_base_with_no_run_at_all_is_the_same(self):
        decision = decide(
            base_checks=runs(("Web unit tests", "success")),
            main_window=window((MAIN_SHA, runs((PYTEST, "success"))),
                               (OLD_SHA, runs((PYTEST, "failure")))),
        )
        self.assertEqual("refresh", decision.action, decision.reason)
        self.assertEqual(OLD_SHA, decision.evidence[0]["red_sha"])
        self.assertEqual(MAIN_SHA, decision.evidence[0]["green_sha"])

    def test_the_newest_red_and_the_newest_green_are_the_evidence(self):
        decision = decide(
            base_checks=runs(),
            main_window=window((MAIN_SHA, runs()),
                               (OLD_SHA, runs((PYTEST, "success"))),
                               (OLDER_SHA, runs((PYTEST, "failure"))),
                               ("f" * 40, runs((PYTEST, "failure")))),
        )
        self.assertEqual("refresh", decision.action, decision.reason)
        self.assertEqual(
            [{"check": PYTEST, "base_red": False, "red_sha": OLDER_SHA,
              "green_sha": OLD_SHA}],
            decision.evidence,
        )

    def test_a_red_base_names_the_merge_base_as_the_red(self):
        decision = decide()
        self.assertEqual("refresh", decision.action)
        self.assertEqual(
            [{"check": PYTEST, "base_red": True, "red_sha": BASE_SHA,
              "green_sha": MAIN_SHA}],
            decision.evidence,
        )

    def test_evidence_is_empty_on_every_answer_but_refresh(self):
        for overrides in ({"base_checks": runs((PYTEST, "success"))},
                          {"main_window": tip(runs((PYTEST, "failure")))},
                          {"main_window": tip(runs((PYTEST, None)))}):
            with self.subTest(overrides=overrides):
                decision = decide(**overrides)
                self.assertNotEqual("refresh", decision.action)
                self.assertEqual([], decision.evidence)

    def test_never_red_in_the_window_is_own(self):
        for label, commits in (
            ("green", window((MAIN_SHA, runs((PYTEST, "success"))),
                             (OLD_SHA, runs((PYTEST, "success"))))),
            ("silent", window((MAIN_SHA, runs()), (OLD_SHA, runs()))),
            ("cancelled", window((MAIN_SHA, runs((PYTEST, "success"))),
                                 (OLD_SHA, runs((PYTEST, "cancelled"))))),
        ):
            for base in (runs((PYTEST, "success")), runs()):
                with self.subTest(window=label, base=base):
                    decision = decide(base_checks=base, main_window=commits)
                    self.assertEqual("own", decision.action, decision.reason)
                    self.assertNotIn("green on the merge base", decision.reason)

    def test_the_own_reason_says_what_was_read(self):
        decision = decide(
            base_checks=runs((PYTEST, "success")),
            main_window=window((MAIN_SHA, runs((PYTEST, "success"))),
                               (OLD_SHA, runs((PYTEST, "success")))),
        )
        self.assertEqual("own", decision.action)
        self.assertIn(PYTEST, decision.reason)
        self.assertIn(f"not red on merge base `{BASE_SHA[:8]}`", decision.reason)
        self.assertIn("never red on the 2 `main` commits since", decision.reason)
        self.assertEqual(1, len(decision.reason.splitlines()))

    def test_newest_non_silent_red_is_main_still_red(self):
        commits = window((MAIN_SHA, runs((PYTEST, None))),
                         (OLD_SHA, runs((PYTEST, "failure"))),
                         (OLDER_SHA, runs((PYTEST, "success"))))
        for base in (runs((PYTEST, "failure")), runs((PYTEST, "success"))):
            with self.subTest(base=base):
                decision = decide(base_checks=base, main_window=commits)
                self.assertEqual("main-still-red", decision.action,
                                 decision.reason)
                self.assertIn(PYTEST, decision.reason)

    def test_an_in_flight_tip_reads_the_commit_behind_it(self):
        commits = window((MAIN_SHA, runs((PYTEST, None))),
                         (OLD_SHA, runs((PYTEST, "success"))))
        decision = decide(base_checks=runs((PYTEST, "failure")),
                          main_window=commits)
        self.assertEqual("refresh", decision.action, decision.reason)
        self.assertEqual(OLD_SHA, decision.evidence[0]["green_sha"])

        decision = decide(
            base_checks=runs((PYTEST, "success")),
            main_window=commits + [(OLDER_SHA, runs((PYTEST, "failure")))],
        )
        self.assertEqual("refresh", decision.action, decision.reason)
        self.assertEqual(OLDER_SHA, decision.evidence[0]["red_sha"])
        self.assertEqual(OLD_SHA, decision.evidence[0]["green_sha"])

    def test_a_red_base_and_a_silent_window_is_unevaluated(self):
        decision = decide(main_window=window((MAIN_SHA, runs((PYTEST, None))),
                                             (OLD_SHA, runs())))
        self.assertEqual("unevaluated", decision.action)


class CombinedChecksTest(unittest.TestCase):
    """Per-check answers combine in today's order: own, main-still-red,
    unevaluated, and refresh only when every check is fixed."""

    OWN_X, RED_X, OPEN_X, FIXED_X = "own x", "red x", "open x", "fixed x"

    def _decide(self, names):
        head = runs(*[(n, "failure") for n in names])
        base = runs((self.OWN_X, "success"), (self.RED_X, "failure"),
                    (self.OPEN_X, "failure"), (self.FIXED_X, "failure"))
        tip_runs = runs((self.OWN_X, "success"), (self.RED_X, "failure"),
                        (self.OPEN_X, None), (self.FIXED_X, "success"))
        return decide(head_checks=head, base_checks=base,
                      main_window=tip(tip_runs))

    def test_one_own_check_makes_the_pull_request_own(self):
        decision = self._decide([self.OPEN_X, self.RED_X, self.OWN_X])
        self.assertEqual("own", decision.action)

    def test_without_it_main_still_red_wins(self):
        decision = self._decide([self.OPEN_X, self.RED_X])
        self.assertEqual("main-still-red", decision.action)

    def test_an_unevaluated_check_stops_a_fixed_one(self):
        decision = self._decide([self.FIXED_X, self.OPEN_X])
        self.assertEqual("unevaluated", decision.action)

    def test_every_check_fixed_is_a_refresh(self):
        decision = self._decide([self.FIXED_X])
        self.assertEqual("refresh", decision.action)


class UnusableWindowTest(unittest.TestCase):
    def test_an_unusable_window_is_unevaluated(self):
        for label, commits in (
            ("not a list", 42),
            ("a dict", {MAIN_SHA: runs((PYTEST, "success"))}),
            ("empty", []),
            ("unreadable payload", window((MAIN_SHA, runs((PYTEST, None))),
                                          (OLD_SHA, "nope"))),
            ("not a pair", [MAIN_SHA]),
            ("wrong first sha", window((OLD_SHA, runs((PYTEST, "success"))))),
        ):
            with self.subTest(window=label):
                decision = decide(main_window=commits)
                self.assertEqual("unevaluated", decision.action,
                                 decision.reason)

    def test_an_unreadable_merge_base_comes_before_the_window(self):
        decision = decide(base_checks=["nope"], main_window=42)
        self.assertEqual("unevaluated", decision.action)
        self.assertIn("merge base", decision.reason)

    def test_the_answers_before_the_payloads_keep_their_order(self):
        self.assertEqual("current", decide(compare=compare(behind_by=0),
                                           main_window=42).action)
        self.assertEqual("no-failure", decide(head_checks=runs(),
                                              main_window=[]).action)


class ReceiptEvidenceTest(unittest.TestCase):
    def setUp(self):
        self.evidence = [
            {"check": PYTEST, "base_red": True, "red_sha": BASE_SHA,
             "green_sha": MAIN_SHA},
            {"check": S1, "base_red": False, "red_sha": OLDER_SHA,
             "green_sha": OLD_SHA},
        ]
        self.kwargs = dict(pr_number=3457, head_sha=HEAD_SHA, main_sha=MAIN_SHA,
                           base_sha=BASE_SHA, inherited=[PYTEST, S1], used=1,
                           cap=3)
        self.detail = smr.receipt_detail(**self.kwargs, evidence=self.evidence)

    def test_it_opens_with_the_marker(self):
        self.assertEqual(smr.marker(MAIN_SHA), self.detail.splitlines()[0])

    def test_the_anchor_phrase_appears_once(self):
        self.assertEqual(1, self.detail.count(smr.ANCHOR_PHRASE))

    def test_it_emits_no_verdict_marker(self):
        for forbidden in ("VERDICT:", "QA Critic", "QA Verifier"):
            self.assertNotIn(forbidden, self.detail)

    def test_the_opening_sentence_says_main_was_red_too(self):
        self.assertIn(
            "red on 2 check(s) that `main` was red on too and is green on now",
            self.detail)

    def test_one_bullet_per_check_naming_what_was_read(self):
        bullets = [ln for ln in self.detail.splitlines() if ln.startswith("- ")]
        self.assertEqual(2, len(bullets))
        self.assertIn(PYTEST, bullets[0])
        self.assertIn(f"red on the merge base `{BASE_SHA[:8]}`, green on `main` "
                      f"at `{MAIN_SHA[:8]}`", bullets[0])
        self.assertIn(S1, bullets[1])
        self.assertIn(f"not red on the merge base `{BASE_SHA[:8]}`, but `main` "
                      f"went red on it at `{OLDER_SHA[:8]}` and is green on it "
                      f"at `{OLD_SHA[:8]}`", bullets[1])

    def test_the_anchor_and_cost_paragraphs_do_not_change(self):
        plain = smr.receipt_detail(**self.kwargs)
        tail = plain.split("\n\n")[-2:]
        self.assertEqual(tail, self.detail.split("\n\n")[-2:])

    def test_without_evidence_the_body_is_todays_text(self):
        plain = smr.receipt_detail(**self.kwargs)
        self.assertEqual(plain, smr.receipt_detail(**self.kwargs, evidence=None))
        self.assertIn("that were red on its merge base", plain)


# --------------------------------------------------------------------------- #
# The four agent-bureau pull requests of 2026-10-09, from the card's tables    #
# --------------------------------------------------------------------------- #

def _sha(prefix):
    return prefix + "0" * (40 - len(prefix))


#: `main`'s merges at the 16:04 PT sweep, newest first: (sha, pytest, s1, s2).
#: None is no run, "…" is a run still going.
MAIN_ROWS = [
    ("21ca627e", None, "…", "…"),
    ("60f9cb2a", "success", "success", "success"),
    ("c4290653", None, None, None),
    ("f3cb2096", "success", "success", "success"),
    ("3d8e7550", "success", "success", "success"),
    ("b62cc42a", "success", "success", "success"),
    ("2ee5fbd5", "success", None, None),
    ("9ee21f36", "failure", "failure", "success"),
    ("66529000", "failure", "success", "failure"),
    ("7406667f", "success", "success", "success"),
    ("d8dd87ce", "success", "success", "success"),
    ("1832a23d", "success", "success", "success"),
]


def _row_runs(row):
    _sha_, *conclusions = row
    pairs = []
    for name, conclusion in zip((PYTEST, S1, S2), conclusions):
        if conclusion == "…":
            pairs.append((name, None))
        elif conclusion:
            pairs.append((name, conclusion))
    return runs(*pairs)


def _listing():
    """The `commits?sha=<tip>` listing: every row's first parent is the next
    row, its second parent a pull request commit that is also listed, and
    the chain runs on past row 11 into commits the listing still holds."""
    shas = [_sha(row[0]) for row in MAIN_ROWS] + [_sha(f"f{i:02d}")
                                                  for i in range(3)]
    listing = []
    for i, sha in enumerate(shas[:-1]):
        side = _sha(f"ad{i:02d}")
        listing.append(commit(sha, shas[i + 1], side))
        listing.append(commit(side, shas[i + 1]))
    listing.append(commit(shas[-1], _sha("ffff")))
    return listing


#: PR -> (head failing set, merge-base sha, merge-base check runs)
OCTOBER_9 = {
    3457: ([PYTEST, S1], _sha("66529000"), _row_runs(MAIN_ROWS[8])),
    3444: ([PYTEST, S2], _sha("66529000"), _row_runs(MAIN_ROWS[8])),
    3428: ([PYTEST, S1], _sha("95c94b8d"), runs()),
    3397: ([PYTEST, S2], _sha("9e950995"),
           runs((PYTEST, "success"), (PYTEST, "failure"), (S2, "success"))),
}


class October9Test(unittest.TestCase):
    """#3444, #3457, #3397 and #3428 sat red on failures `main` had fixed,
    and the sweep called three of them their own. Each one is a refresh."""

    def _decide(self, number):
        failing, base_sha, base = OCTOBER_9[number]
        tip_sha = _sha(MAIN_ROWS[0][0])
        shas = smr.first_parent_window(_listing(), tip_sha=tip_sha,
                                       base_sha=base_sha)
        rows = {_sha(row[0]): row for row in MAIN_ROWS}
        return shas, smr.decide(
            compare=compare(behind_by=40, base_sha=base_sha, main_sha=tip_sha),
            head_checks=runs(*[(n, "failure") for n in failing]),
            base_checks=base,
            main_window=[(sha, _row_runs(rows[sha])) for sha in shas],
            receipts=[],
            cap=3,
        )

    def test_the_windows_are_the_rows_the_card_names(self):
        rows = [_sha(row[0]) for row in MAIN_ROWS]
        self.assertEqual(rows[:8], self._decide(3457)[0])
        self.assertEqual(rows[:8], self._decide(3444)[0])
        self.assertEqual(rows[:12], self._decide(3428)[0])
        self.assertEqual(rows[:12], self._decide(3397)[0])

    def test_all_four_are_refreshed(self):
        for number in OCTOBER_9:
            with self.subTest(pr=number):
                _shas, decision = self._decide(number)
                self.assertEqual("refresh", decision.action, decision.reason)
                self.assertEqual(OCTOBER_9[number][0], decision.inherited)

    def test_the_evidence_names_what_was_read(self):
        red, green = _sha("9ee21f36"), _sha("60f9cb2a")
        merge = _sha("66529000")
        expect = {
            3457: [(PYTEST, True, merge), (S1, False, red)],
            3444: [(PYTEST, True, merge), (S2, True, merge)],
            3428: [(PYTEST, False, red), (S1, False, red)],
            3397: [(PYTEST, True, _sha("9e950995")), (S2, False, merge)],
        }
        for number, rows in expect.items():
            with self.subTest(pr=number):
                _shas, decision = self._decide(number)
                self.assertEqual(
                    [{"check": c, "base_red": b, "red_sha": r,
                      "green_sha": green} for c, b, r in rows],
                    decision.evidence,
                )


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def _file(self, name, payload, raw=None):
        path = self.tmp / name
        path.write_text(raw if raw is not None else json.dumps(payload))
        return str(path)

    def _run(self, *, compare_payload=None, head=None, base=None, main=None,
             head_raw=None, extra=()):
        argv = [
            "decide",
            "--compare-file", self._file("compare.json",
                                         compare_payload or compare()),
            "--checks-file", self._file(
                "head.json", head or runs((PYTEST, "failure")), raw=head_raw),
            "--base-checks-file", self._file(
                "base.json", base or runs((PYTEST, "failure"))),
            "--main-checks-file", self._file(
                "main.json", main or runs((PYTEST, "success"))),
            *extra,
        ]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = smr.main(argv)
        return code, out.getvalue().splitlines(), err.getvalue()

    def test_the_action_is_stdout_line_one_and_the_reason_is_stderr(self):
        code, lines, err = self._run()
        self.assertEqual(0, code)
        self.assertEqual("refresh", lines[0])
        self.assertIn(PYTEST, err)

    def test_every_decision_exits_zero(self):
        code, lines, _ = self._run(main=runs((PYTEST, "failure")))
        self.assertEqual(0, code)
        self.assertEqual("main-still-red", lines[0])

    def test_an_unreadable_head_payload_exits_two(self):
        code, _, err = self._run(head_raw="{not json")
        self.assertEqual(2, code, "the head's own red checks are the subject")
        self.assertIn("head", err.lower())

    def test_an_unreadable_base_payload_is_unevaluated_at_exit_zero(self):
        base = self._file("broken-base.json", None, raw="{not json")
        code, lines, _ = self._run(extra=["--base-checks-file", base])
        self.assertEqual(0, code)
        self.assertEqual("unevaluated", lines[0])

    def test_receipts_file_is_read(self):
        receipts = self._file("receipts.json", [smr.marker(MAIN_SHA)])
        code, lines, _ = self._run(extra=["--receipts-file", receipts])
        self.assertEqual(0, code)
        self.assertEqual("already-refreshed", lines[0])

    def test_the_cap_is_settable(self):
        code, lines, _ = self._run(extra=["--cap", "0"])
        self.assertEqual(0, code)
        self.assertEqual("cap-spent", lines[0])

    def test_an_unreadable_receipts_file_is_unevaluated(self):
        receipts = self._file("broken-receipts.json", None, raw="{not json")
        code, lines, _ = self._run(extra=["--receipts-file", receipts])
        self.assertEqual(0, code)
        self.assertEqual("unevaluated", lines[0])


    def test_a_window_file_is_read_tip_first(self):
        window_file = self._file("window.json", [
            {"sha": MAIN_SHA, "check_runs": runs((PYTEST, "success"))["check_runs"]},
            {"sha": OLD_SHA, "check_runs": runs((PYTEST, "failure"))["check_runs"]},
        ])
        argv = [
            "decide",
            "--compare-file", self._file("compare.json", compare()),
            "--checks-file", self._file("head.json", runs((PYTEST, "failure"))),
            "--base-checks-file", self._file("base.json",
                                             runs((PYTEST, "success"))),
            "--main-window-file", window_file,
        ]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = smr.main(argv)
        self.assertEqual(0, code)
        self.assertEqual("refresh", out.getvalue().splitlines()[0],
                         err.getvalue())

    def test_an_unreadable_window_file_is_unevaluated(self):
        for raw in ("{not json", '{"sha": "x"}', '[{"sha": "x"}]', "[42]"):
            with self.subTest(raw=raw):
                argv = [
                    "decide",
                    "--compare-file", self._file("compare.json", compare()),
                    "--checks-file", self._file("head.json",
                                                runs((PYTEST, "failure"))),
                    "--base-checks-file", self._file("base.json",
                                                     runs((PYTEST, "failure"))),
                    "--main-window-file", self._file("w.json", None, raw=raw),
                ]
                out = io.StringIO()
                with contextlib.redirect_stdout(out), \
                        contextlib.redirect_stderr(io.StringIO()):
                    code = smr.main(argv)
                self.assertEqual(0, code)
                self.assertEqual("unevaluated", out.getvalue().splitlines()[0])

    def test_both_main_flags_together_are_refused(self):
        window_file = self._file("window.json", [
            {"sha": MAIN_SHA, "check_runs": []}])
        with self.assertRaises(SystemExit) as raised, \
                contextlib.redirect_stderr(io.StringIO()):
            self._run(extra=["--main-window-file", window_file])
        self.assertEqual(2, raised.exception.code)

    def test_neither_main_flag_is_refused(self):
        argv = [
            "decide",
            "--compare-file", self._file("compare.json", compare()),
            "--checks-file", self._file("head.json", runs((PYTEST, "failure"))),
            "--base-checks-file", self._file("base.json",
                                             runs((PYTEST, "failure"))),
        ]
        with self.assertRaises(SystemExit) as raised, \
                contextlib.redirect_stderr(io.StringIO()):
            smr.main(argv)
        self.assertEqual(2, raised.exception.code)


class NoIoTest(unittest.TestCase):
    """The decision is pure: the module reaches for no network and no
    subprocess, exactly as inherited_failures.py and red_main_repair.py do."""

    def test_the_module_imports_nothing_that_leaves_the_process(self):
        source = MODULE.read_text(encoding="utf-8")
        for forbidden in ("import subprocess", "import urllib", "import requests",
                          "import socket", "import http"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
