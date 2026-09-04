"""RED-first tests for DRE-3101: the promote receipt names a wiped probe.

When a concurrent harness run closes main's live probe PR out from under
it, main's run ends — and until now the only record of WHY was a run log.
On 2026-09-04 that cost the channel a day: main's probe PR #939 in the
sandbox was closed by the bot two minutes after DRE-3098's PR run started
its scenarios, main's run died on it, and diagnosing that meant reading
two run histories side by side.

A wipe is not a verdict on the commit — nothing about the trunk was ever
judged — so it takes the same shape DRE-3076 gave a dead sandbox: a
marker-prefixed receipt on the `integration-harness` status, its own
promote outcome, and a stop rather than a red trunk. The difference is
what the marker SAYS: *main's probe was wiped by run <id> (branch <name>)*,
so the next one is diagnosed from the receipt.

These tests must FAIL while a wiped probe is an ordinary scenario failure,
and PASS after.

Run: python3 -m pytest tests/test_promote_wiped_probe.py -v
"""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import promote_channel  # noqa: E402
from harness import framework, sandbox_health  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

SHA = "c" * 40

#: The live pair from the incident: main's probe, and the pull-request run
#: whose pre-namespacing sweep closed it.
MAIN_PROBE = 939
FOREIGN_RUN = "pr264-gha-33899093729-1"
FOREIGN_BRANCH = f"agent/harness-{FOREIGN_RUN}-gate_paths"


class _GH:
    """The two calls the wipe report makes, and nothing else."""

    def __init__(self, pr, refs=(), raises=False):
        self.pr = pr
        self.refs = list(refs)
        self.raises = raises

    def get_pr(self, repo, number):
        return self.pr

    def matching_refs(self, repo, prefix):
        if self.raises:
            raise RuntimeError("GitHub said no")
        return [r for r in self.refs if r.startswith(prefix)]


def _pr(number=MAIN_PROBE, **over):
    pr = {
        "number": number,
        "state": "closed",
        "merged": False,
        "head": {"ref": f"agent/harness-main-gha-1-1-gate_paths", "sha": "a" * 40},
    }
    pr.update(over)
    return pr


class RunIdOfAHarnessRefTest(unittest.TestCase):
    """Who did it, read off the branch the culprit left behind. Scenario
    names carry underscores and never dashes, so the run id is everything
    before the last dash."""

    def test_the_run_id_is_recovered_from_a_branch(self):
        self.assertEqual(
            framework.run_id_of_harness_ref(FOREIGN_BRANCH), FOREIGN_RUN
        )

    def test_a_dependabot_named_probe_is_read_the_same_way(self):
        self.assertEqual(
            framework.run_id_of_harness_ref(
                f"dependabot/harness-{FOREIGN_RUN}-gate_paths"
            ),
            FOREIGN_RUN,
        )

    def test_a_fully_qualified_ref_is_accepted(self):
        self.assertEqual(
            framework.run_id_of_harness_ref(f"refs/heads/{FOREIGN_BRANCH}"),
            FOREIGN_RUN,
        )

    def test_a_ref_that_is_not_ours_names_nobody(self):
        for ref in ("agent/DRE-3101-real-work", "dependabot/pip/urllib3-2.0.7", "", None):
            self.assertIsNone(framework.run_id_of_harness_ref(ref))


class WipedProbeIsBlockedNotFailedTest(unittest.TestCase):
    """`probe_pr` already ends the wait (the red-main repair, 2026-09-04).
    What it owes now is the RECEIPT — the run that did it, named."""

    def test_a_closed_unmerged_probe_blocks_the_run(self):
        gh = _GH(_pr(), refs=[FOREIGN_BRANCH])
        with self.assertRaises(framework.SandboxBlocked) as caught:
            framework.probe_pr(gh, "o/r", MAIN_PROBE, namespace="main")
        # The run log still says what a reader needs at the failure site.
        message = str(caught.exception)
        self.assertIn(f"#{MAIN_PROBE}", message)
        self.assertIn("closed", message)
        self.assertIn("sweep", message)

    def test_the_receipt_names_the_run_and_the_branch_that_wiped_it(self):
        gh = _GH(_pr(), refs=[FOREIGN_BRANCH])
        with self.assertRaises(framework.SandboxBlocked) as caught:
            framework.probe_pr(gh, "o/r", MAIN_PROBE, namespace="main")
        cause = caught.exception.cause
        self.assertTrue(
            cause.startswith(promote_channel.WIPED_MARKER),
            f"the receipt must open with the marker: {cause!r}",
        )
        self.assertIn("main's probe", cause)
        self.assertIn(f"#{MAIN_PROBE}", cause)
        self.assertIn(f"run {FOREIGN_RUN}", cause)
        self.assertIn(f"branch {FOREIGN_BRANCH}", cause)

    def test_our_own_branches_are_never_named_as_the_culprit(self):
        # The wiped run's own scenario branches are still in the sandbox.
        # Naming one of those would send a reader to the victim.
        gh = _GH(_pr(), refs=["agent/harness-main-gha-1-1-bot_pr_flow"])
        with self.assertRaises(framework.SandboxBlocked) as caught:
            framework.probe_pr(gh, "o/r", MAIN_PROBE, namespace="main")
        cause = caught.exception.cause
        self.assertNotIn("main-gha-1-1-bot_pr_flow", cause)
        self.assertTrue(cause.startswith(promote_channel.WIPED_MARKER))

    def test_a_culprit_that_cannot_be_named_is_not_invented(self):
        for gh in (
            _GH(_pr(), refs=[]),
            _GH(_pr(), raises=True),
        ):
            with self.assertRaises(framework.SandboxBlocked) as caught:
                framework.probe_pr(gh, "o/r", MAIN_PROBE, namespace="main",
                                   log=lambda *a, **k: None)
            cause = caught.exception.cause
            self.assertTrue(cause.startswith(promote_channel.WIPED_MARKER))
            self.assertIn("could not", cause.lower())

    def test_several_foreign_runs_are_counted_not_silently_dropped(self):
        # Naming one of three as though the other two had been ruled out is
        # the console-honesty failure in miniature. The receipt names one
        # (the deterministic pick) and says how many more it could not.
        gh = _GH(_pr(), refs=[
            FOREIGN_BRANCH,
            "agent/harness-pr91-gha-33900000000-1-bot_pr_flow",
            "dependabot/harness-pr91-gha-33900000000-1-gate_paths",
        ])
        with self.assertRaises(framework.SandboxBlocked) as caught:
            framework.probe_pr(gh, "o/r", MAIN_PROBE, namespace="main")
        self.assertIn("2 more foreign run(s)", caught.exception.cause)

    def test_one_foreign_run_is_named_without_a_count(self):
        gh = _GH(_pr(), refs=[FOREIGN_BRANCH])
        with self.assertRaises(framework.SandboxBlocked) as caught:
            framework.probe_pr(gh, "o/r", MAIN_PROBE, namespace="main")
        self.assertNotIn("more foreign run", caught.exception.cause)

    def test_the_same_sandbox_state_yields_the_same_receipt(self):
        refs = [FOREIGN_BRANCH, "agent/harness-pr91-gha-33900000000-1-bot_pr_flow"]
        seen = set()
        for order in (refs, list(reversed(refs))):
            with self.assertRaises(framework.SandboxBlocked) as caught:
                framework.probe_pr(
                    _GH(_pr(), refs=order), "o/r", MAIN_PROBE, namespace="main"
                )
            seen.add(caught.exception.cause)
        self.assertEqual(len(seen), 1, "the receipt depends on listing order")

    def test_an_open_probe_and_a_merged_probe_are_untouched(self):
        for pr in (_pr(state="open", merged=False), _pr(merged=True)):
            gh = _GH(pr, refs=[FOREIGN_BRANCH])
            self.assertIs(
                framework.probe_pr(gh, "o/r", MAIN_PROBE, namespace="main"), pr
            )

    def test_the_scenario_result_carries_the_receipt_not_just_an_error(self):
        # `__main__` writes `blocked_reason` from `result.blocked`, and that
        # output IS the status description. A wipe recorded only in
        # `result.errors` never reaches the channel.
        gh = _GH(_pr(), refs=[FOREIGN_BRANCH])

        class _Wiped(framework.Scenario):
            name = "wiped"

            def verify(self, ctx):
                framework.probe_pr(ctx.gh, ctx.repo, MAIN_PROBE, namespace=ctx.namespace)

        result = framework.run_scenario(
            _Wiped(),
            framework.HarnessContext(
                gh=gh, repo="o/r", run_id="main-gha-1-1", namespace="main",
                log=lambda *a, **k: None,
            ),
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.failed_phase, "verify")
        self.assertTrue(result.blocked.startswith(promote_channel.WIPED_MARKER))

    def test_the_receipt_survives_githubs_clamp_with_its_marker(self):
        gh = _GH(_pr(), refs=[FOREIGN_BRANCH])
        with self.assertRaises(framework.SandboxBlocked) as caught:
            framework.probe_pr(gh, "o/r", MAIN_PROBE, namespace="main")
        line = sandbox_health.receipt_line(caught.exception.cause)
        self.assertLessEqual(len(line), sandbox_health.RECEIPT_LIMIT)
        self.assertTrue(line.startswith(promote_channel.WIPED_MARKER))


class PromoteReceiptNamesTheWipeTest(unittest.TestCase):
    """`promote_channel.evaluate` reads the marker, the way it reads
    DRE-3076's — a wipe is neither a proof nor a disproof of the trunk."""

    def _statuses(self, description):
        return {
            "statuses": [
                {"context": promote_channel.STATUS_CONTEXT,
                 "state": "failure",
                 "description": description}
            ]
        }

    def _receipt(self):
        return (
            f"{promote_channel.WIPED_MARKER} main's probe PR #{MAIN_PROBE} was "
            f"wiped by run {FOREIGN_RUN} (branch {FOREIGN_BRANCH})"
        )

    def test_the_outcome_is_its_own_name(self):
        decision = promote_channel.evaluate(
            self._statuses(self._receipt()), SHA, branch="main",
            conclusion="failure", ancestry="ahead",
        )
        self.assertFalse(decision.promote)
        self.assertEqual(decision.outcome, promote_channel.OUTCOME_PROBE_WIPED)

    def test_the_reason_names_the_run_and_the_branch(self):
        decision = promote_channel.evaluate(
            self._statuses(self._receipt()), SHA, branch="main",
            conclusion="failure", ancestry="ahead",
        )
        self.assertIn(FOREIGN_RUN, decision.reason)
        self.assertIn(FOREIGN_BRANCH, decision.reason)

    def test_a_wipe_is_not_reported_as_a_red_trunk(self):
        # The whole point: `harness-failed` sends someone to read a diff
        # nothing ever judged.
        decision = promote_channel.evaluate(
            self._statuses(self._receipt()), SHA, branch="main",
            conclusion="failure", ancestry="ahead",
        )
        self.assertNotEqual(decision.outcome, promote_channel.OUTCOME_FAILED)
        self.assertNotEqual(decision.outcome, promote_channel.OUTCOME_BLOCKED)

    def test_an_ordinary_harness_failure_is_untouched(self):
        decision = promote_channel.evaluate(
            self._statuses("integration harness: failure"), SHA, branch="main",
            conclusion="failure", ancestry="ahead",
        )
        self.assertEqual(decision.outcome, promote_channel.OUTCOME_FAILED)

    def test_a_dead_sandbox_still_reads_as_a_dead_sandbox(self):
        decision = promote_channel.evaluate(
            self._statuses(f"{promote_channel.BLOCKED_MARKER} sandbox reconcile "
                           f"failure at 2026-09-04T10:39:00Z: rate limited"),
            SHA, branch="main", conclusion="failure", ancestry="ahead",
        )
        self.assertEqual(decision.outcome, promote_channel.OUTCOME_BLOCKED)

    def test_a_held_channel_still_reads_as_held(self):
        # Order: the branch, then the hold, then the markers — a hold is a
        # statement about the channel and outranks any run's receipt.
        decision = promote_channel.evaluate(
            self._statuses(self._receipt()), SHA, branch="main",
            hold="operator: pausing for the release cut",
        )
        self.assertEqual(decision.outcome, promote_channel.OUTCOME_HELD)

    def test_a_pr_head_run_is_still_never_a_candidate(self):
        decision = promote_channel.evaluate(
            self._statuses(self._receipt()), SHA, branch="agent/DRE-3101-x",
        )
        self.assertEqual(decision.outcome, promote_channel.OUTCOME_NOT_MAIN)

    def test_the_marker_is_recognised_after_the_clamp(self):
        long = sandbox_health.receipt_line(self._receipt() + " " + "x" * 400)
        self.assertIsNotNone(promote_channel.wiped_probe(self._statuses(long)))


class TheReceiptVocabularyIsWrittenDownTest(unittest.TestCase):
    """A receipt vocabulary nobody wrote down is one nobody can read at 2am
    — `docs/self-hosting.md` carries the same table the code emits."""

    def test_the_doc_names_the_new_outcome(self):
        doc = (ROOT / "docs" / "self-hosting.md").read_text()
        self.assertIn(promote_channel.OUTCOME_PROBE_WIPED, doc)

    def test_the_harness_readme_describes_the_wipe_receipt(self):
        readme = (ROOT / "scripts" / "harness" / "README.md").read_text()
        self.assertIn(promote_channel.WIPED_MARKER, readme)


if __name__ == "__main__":
    unittest.main()
