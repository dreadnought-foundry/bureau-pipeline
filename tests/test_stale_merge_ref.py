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


def decide(**overrides):
    kwargs = {
        "compare": compare(),
        "head_checks": runs((PYTEST, "failure")),
        "base_checks": runs((PYTEST, "failure")),
        "main_checks": runs((PYTEST, "success")),
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
             "behind_by"],
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
            main_checks=runs((PYTEST, "success"), ("Web unit tests", "success")),
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
            main_checks=runs((PYTEST, "success"), ("Web unit tests", "success")),
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
        decision = decide(main_checks=runs((PYTEST, "failure")))
        self.assertEqual("main-still-red", decision.action)
        self.assertIn(PYTEST, decision.reason)

    def test_no_completed_run_on_main_yet_is_unevaluated(self):
        decision = decide(main_checks=runs((PYTEST, None)))
        self.assertEqual("unevaluated", decision.action,
                         "main's CI is still running — try again next sweep")

    def test_absent_from_main_entirely_is_unevaluated_never_a_refresh(self):
        decision = decide(main_checks=runs(("Web unit tests", "success")))
        self.assertEqual("unevaluated", decision.action)

    def test_a_completed_run_that_is_neither_green_nor_red_is_unevaluated(self):
        decision = decide(main_checks=runs((PYTEST, "skipped")))
        self.assertEqual("unevaluated", decision.action)

    def test_one_red_and_one_unfinished_reports_the_red(self):
        decision = decide(
            head_checks=runs((PYTEST, "failure"), ("Web unit tests", "failure")),
            base_checks=runs((PYTEST, "failure"), ("Web unit tests", "failure")),
            main_checks=runs((PYTEST, "failure"), ("Web unit tests", None)),
        )
        self.assertEqual("main-still-red", decision.action,
                         "a definite red on main beats an unfinished run")


class NothingToDoTest(unittest.TestCase):
    def test_behind_by_zero_is_current_regardless_of_check_state(self):
        decision = decide(
            compare=compare(behind_by=0),
            main_checks=runs((PYTEST, "failure")),
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
            main_checks=runs((PYTEST, "success")),
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
        self.assertEqual("unevaluated", decide(main_checks=42).action)

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
