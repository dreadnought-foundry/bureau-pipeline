"""RED-first tests for DRE-3101: a PR's harness run takes its cleanup rules
from main.

Observed 2026-09-04, 10:00 PT onward. DRE-3075 split the harness
concurrency groups and scoped the sandbox cleanup to a namespace — in
`harness.yml` AND in `scripts/harness/`, which a `pull_request` run reads
from the PR's own head (`ref: github.event.pull_request.head.sha`). Every
open branch cut before that merge therefore still ran the OLD,
un-namespaced sweep: it closed every harness PR and deleted every harness
branch in the shared sandbox, main's included. From 10:00 PT no main
proving run finished — five cancelled or hung — while PR runs kept passing,
because main has one slot and the pull requests have many. `stable` sat at
`a7bfa52` (08:23 PT) through ten merges.

So the driver a PR run drives the SHARED sandbox with cannot be the PR's
copy by default. It comes from `main` — the way the reusable workflows
re-checkout bureau-pipeline at `pipeline_ref` — and only from the PR when
the PR is what changes `scripts/harness/`, which is the one case where the
harness itself is what is under test. Then the receipt says so.

These tests must FAIL against a workflow that always drives from the PR's
head, and PASS after.

Run: python3 -m pytest tests/test_harness_driver_from_main.py -v
"""

import os
import sys
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import fix_concurrency  # noqa: E402
import harness_driver  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "harness.yml"

#: The PR whose run wiped main's probe PR #939 at 10:39 PT (DRE-3098's).
PR_NUMBER = 264


def _doc():
    assert WORKFLOW.is_file(), f"missing {WORKFLOW.name}"
    return yaml.safe_load(WORKFLOW.read_text())


def _steps(doc):
    return doc["jobs"]["harness"].get("steps") or []


def _step(doc, needle):
    """The step whose name contains `needle` — the wiring tests address
    steps by what they are for, never by index."""
    for step in _steps(doc):
        if needle.lower() in (step.get("name") or "").lower():
            return step
    raise AssertionError(f"no step named like {needle!r} in {WORKFLOW.name}")


def _index(doc, step):
    return _steps(doc).index(step)


class ChooseTheDriverTest(unittest.TestCase):
    """The decision, with no I/O — the workflow gathers GitHub's records and
    acts on the verdict (the `promote_channel.py` / `release_gate.py` shape)."""

    def test_a_pull_request_that_leaves_the_harness_alone_drives_from_main(self):
        choice = harness_driver.choose(
            "pull_request",
            ["scripts/reconcile.py", "tests/test_reconcile.py"],
        )
        self.assertEqual(choice.source, harness_driver.SOURCE_MAIN)

    def test_a_pull_request_that_changes_the_harness_drives_from_itself(self):
        choice = harness_driver.choose(
            "pull_request",
            ["scripts/harness/framework.py"],
        )
        self.assertEqual(choice.source, harness_driver.SOURCE_PR)

    def test_a_scenario_file_is_the_harness_too(self):
        choice = harness_driver.choose(
            "pull_request", ["scripts/harness/scenarios/gate_paths.py"]
        )
        self.assertEqual(choice.source, harness_driver.SOURCE_PR)

    def test_a_path_that_merely_looks_like_the_harness_is_not_the_harness(self):
        # The guard has to be the DIRECTORY, not the word: this card adds
        # `scripts/harness_driver.py` beside `scripts/harness/`, and the
        # harness tests are named `tests/test_harness_*.py`. A prefix match
        # on "scripts/harness" alone would send every one of them down the
        # PR's-own-copy path — which is the bug this card exists to close.
        choice = harness_driver.choose(
            "pull_request",
            [
                "scripts/harness_driver.py",
                "tests/test_harness_main_slot.py",
                "docs/harness.md",
            ],
        )
        self.assertEqual(choice.source, harness_driver.SOURCE_MAIN)

    def test_a_push_to_main_drives_from_the_ref_under_test(self):
        # main's own run IS main: re-checking-out main over it would be a
        # no-op at best and, on a dispatch of an older `pipeline_ref`, would
        # silently test something else.
        for event in ("push", "workflow_dispatch"):
            self.assertEqual(
                harness_driver.choose(event, []).source,
                harness_driver.SOURCE_HEAD,
                f"{event} must drive from what it checked out",
            )

    def test_an_unreadable_file_list_still_takes_the_pr_at_its_word(self):
        # Fail SAFE, not closed: an empty list from a failed `gh api` call
        # means "we could not see a harness change", and driving from main
        # is the conservative answer — it can only lose the PR's own harness
        # edits, never let an old sweep loose on the shared sandbox.
        self.assertEqual(
            harness_driver.choose("pull_request", []).source,
            harness_driver.SOURCE_MAIN,
        )

    def test_every_source_carries_its_own_receipt_phrase(self):
        seen = set()
        for source in (
            harness_driver.SOURCE_MAIN,
            harness_driver.SOURCE_PR,
            harness_driver.SOURCE_HEAD,
        ):
            phrase = harness_driver.RECEIPTS[source]
            self.assertTrue(phrase.strip(), f"{source} has no receipt phrase")
            seen.add(phrase)
        self.assertEqual(len(seen), 3, "two sources share a receipt phrase")

    def test_the_pr_receipt_says_the_harness_is_what_is_under_test(self):
        phrase = harness_driver.RECEIPTS[harness_driver.SOURCE_PR].lower()
        self.assertIn("under test", phrase)

    def test_a_receipt_fits_inside_a_commit_status_description(self):
        # GitHub clamps a status description at 140 characters and the state
        # goes in front of the phrase.
        for phrase in harness_driver.RECEIPTS.values():
            self.assertLessEqual(len("integration harness: failure — " + phrase), 140)

    def _run_cli(self, changed, event_name="pull_request"):
        import io
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "gh-output")
            summary = os.path.join(tmp, "gh-summary")
            old = dict(os.environ)
            os.environ.update(GITHUB_OUTPUT=out, GITHUB_STEP_SUMMARY=summary)
            try:
                rc = harness_driver.main(
                    ["--event-name", event_name], stdin=io.StringIO(changed)
                )
            finally:
                os.environ.clear()
                os.environ.update(old)
            return rc, open(out).read(), open(summary).read()

    def test_the_cli_publishes_the_source_and_the_receipt(self):
        rc, written, _ = self._run_cli("scripts/reconcile.py\n")
        self.assertEqual(rc, 0)
        self.assertIn(f"source={harness_driver.SOURCE_MAIN}\n", written)
        self.assertIn(
            f"receipt={harness_driver.RECEIPTS[harness_driver.SOURCE_MAIN]}\n",
            written,
        )
        # One line per key: `$GITHUB_OUTPUT` is a key=value file, so a
        # newline inside a value would be a second KEY.
        for line in written.splitlines():
            self.assertIn("=", line)

    def test_the_cli_says_in_the_run_summary_which_driver_ran(self):
        _, _, summary = self._run_cli("scripts/harness/framework.py\n")
        self.assertIn(harness_driver.RECEIPTS[harness_driver.SOURCE_PR], summary)

    def test_the_cli_reads_the_event_it_is_given(self):
        _, written, _ = self._run_cli("", event_name="push")
        self.assertIn(f"source={harness_driver.SOURCE_HEAD}\n", written)


class WorkflowTakesTheDriverFromMainTest(unittest.TestCase):
    """The wiring: the step exists, it is gated on the decision, and it lands
    before the scenarios run."""

    def setUp(self):
        self.doc = _doc()

    def test_a_step_decides_which_harness_drives_the_run(self):
        step = _step(self.doc, "which scripts/harness")
        self.assertEqual(step.get("id"), "driver")
        self.assertIn("harness_driver.py", step.get("run") or "")

    def test_the_decision_is_made_from_the_prs_own_changed_files(self):
        run = _step(self.doc, "which scripts/harness").get("run") or ""
        self.assertIn("pulls/", run, "the changed-file list is the PR's own")
        self.assertIn("files", run)

    def test_mains_copy_is_checked_out_and_installed_over_the_prs(self):
        checkout = _step(self.doc, "take scripts/harness/ from main")
        self.assertEqual(checkout.get("uses", "").split("@")[0], "actions/checkout")
        with_ = checkout.get("with") or {}
        self.assertEqual(with_.get("ref"), "main")
        self.assertEqual(
            with_.get("sparse-checkout"), harness_driver.DRIVER_DIR.rstrip("/"),
            "check out the driver directory alone — nothing else moves",
        )
        self.assertTrue(with_.get("path"), "a second checkout needs its own path")

        install = _step(self.doc, "install main's scripts/harness")
        run = install.get("run") or ""
        self.assertIn(with_["path"], run)
        self.assertIn(harness_driver.DRIVER_DIR.rstrip("/"), run)

    def test_both_steps_are_gated_on_the_decision(self):
        for name in ("take scripts/harness/ from main", "install main's scripts/harness"):
            expr = _step(self.doc, name).get("if")
            self.assertTrue(expr, f"{name!r} runs unconditionally")
            ctx = {"steps": {"driver": {"outputs": {"source": harness_driver.SOURCE_MAIN}}}}
            self.assertTrue(
                fix_concurrency.evaluate(expr, ctx),
                f"{name!r} would not run when the decision says main",
            )
            for other in (harness_driver.SOURCE_PR, harness_driver.SOURCE_HEAD):
                ctx = {"steps": {"driver": {"outputs": {"source": other}}}}
                self.assertFalse(
                    fix_concurrency.evaluate(expr, ctx),
                    f"{name!r} would overwrite the driver on a {other!r} run",
                )

    def test_the_swap_happens_before_the_scenarios_run(self):
        scenarios = next(
            s for s in _steps(self.doc) if "python3 -m harness" in (s.get("run") or "")
        )
        self.assertLess(
            _index(self.doc, _step(self.doc, "install main's scripts/harness")),
            _index(self.doc, scenarios),
            "main's driver has to be in place before the sandbox is touched",
        )

    def test_the_job_may_read_the_pull_requests_file_list(self):
        # Premortem Q2: the decision step calls the pulls API with the
        # workflow's own token, and a token without this scope returns an
        # empty list — which reads as "no harness change" and is silent.
        self.assertEqual(self.doc["permissions"].get("pull-requests"), "read")

    def test_the_driver_directory_is_the_one_the_pr_trigger_watches(self):
        # `on:` parses as the YAML boolean True — the two spellings are the
        # same key and neither is worth depending on.
        triggers = self.doc.get("on") or self.doc.get(True)
        paths = triggers["pull_request"]["paths"]
        self.assertIn(f"{harness_driver.DRIVER_DIR}**", paths)


class TheReceiptSaysWhichDriverRanTest(unittest.TestCase):
    """`integration-harness` is the stamp release-gate.yml and
    promote-channel.yml both read, and it is where a reader learns whether
    the run proved the PR's harness or main's."""

    def setUp(self):
        self.doc = _doc()

    def test_the_stamp_description_carries_the_driver_receipt(self):
        stamp = _step(self.doc, "stamp tested sha")
        env = stamp.get("env") or {}
        self.assertIn(
            "steps.driver.outputs.receipt",
            " ".join(str(v) for v in env.values()),
            "the stamp cannot say which driver ran without reading the receipt",
        )
        self.assertIn("DESCRIPTION", stamp.get("run") or "")

    def test_a_blocked_reason_still_wins_the_description(self):
        # DRE-3076's receipt is the more urgent fact and the 140-character
        # clamp is shared, so the driver note never displaces it.
        run = _step(self.doc, "stamp tested sha").get("run") or ""
        self.assertIn("BLOCKED_REASON", run)
        self.assertLess(
            run.index("BLOCKED_REASON"), run.index("DRIVER_RECEIPT"),
            "the blocked reason must be tested before the driver note",
        )


if __name__ == "__main__":
    unittest.main()
