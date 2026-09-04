"""RED-first: the promote receipt says BLOCKED BY SANDBOX, not "harness failed".

DRE-3076, second acceptance criterion. `promote-channel.yml` prints one line
into the run summary and that line is the whole answer to "why is the channel
not moving?". Before this card it had exactly two things to say about a red
harness — *the stamp reports failure* — and that sentence is wrong in the case
that actually stalled the channel on 2026-09-03: the harness never got to judge
the commit at all, because the SANDBOX was rate-limited.

The distinction is not cosmetic. "harness failed" reads as *this commit is
bad* — a thing to investigate, a thing that stays bad until someone changes
the code. "blocked by sandbox" reads as *not proven yet* — the next run
re-proves it, and on 2026-09-03 the very next run did, in eleven minutes.

The marker is written by the harness driver and read here; one constant, so a
receipt that stops being recognised is a red test rather than a silent
regression to the wrong sentence.
"""

import json
import os
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import promote_channel  # noqa: E402
from harness import sandbox_health  # noqa: E402

WORKFLOW = ROOT / ".github" / "workflows" / "promote-channel.yml"
SHA = "c" * 40


def _combined(state, description=""):
    return {
        "statuses": [
            {"context": "some-other-check", "state": "success"},
            {
                "context": promote_channel.STATUS_CONTEXT,
                "state": state,
                "description": description,
            },
        ]
    }


class BlockedReceiptTest(unittest.TestCase):
    def _reason(self, combined):
        promote, reason = promote_channel.evaluate(
            combined, SHA, hold=None, ancestry="ahead"
        )
        self.assertFalse(promote, "a blocked run proves nothing")
        return reason

    def test_a_blocked_run_reads_as_not_proven_yet(self):
        quote = (
            f"{promote_channel.BLOCKED_MARKER} sandbox Reconcile failed at "
            "2026-09-03T20:27:11Z: rate limited: 2500 requests/hour exhausted"
        )
        reason = self._reason(_combined("failure", quote))
        self.assertIn("blocked", reason.lower())
        self.assertIn("sandbox", reason.lower())
        # The sandbox's own words survive into the receipt.
        self.assertIn("rate limited: 2500 requests/hour exhausted", reason)
        # And it is explicit that the commit was never judged.
        self.assertIn("next run", reason.lower())

    def test_an_ordinary_red_harness_still_reads_as_a_harness_failure(self):
        """The negative half — without it the new branch could swallow every
        genuine failure into a reassuring sentence."""
        reason = self._reason(_combined("failure", "integration harness: failure"))
        self.assertNotIn("blocked", reason.lower())
        self.assertIn(promote_channel.STATUS_CONTEXT, reason)

    def test_a_missing_stamp_is_unchanged(self):
        reason = self._reason({"statuses": []})
        self.assertIn("no stamp at all", reason)

    def test_a_blocked_stamp_never_promotes_even_marked_success(self):
        """Belt and braces: the marker means "not proven", whatever state a
        future writer pairs it with."""
        quote = f"{promote_channel.BLOCKED_MARKER} sandbox Merge Gate failed"
        promote, reason = promote_channel.evaluate(
            _combined("success", quote), SHA, hold=None, ancestry="ahead"
        )
        self.assertFalse(promote, reason)

    def test_the_hold_still_wins_over_the_blocked_receipt(self):
        """Order is load-bearing: a deliberately paused channel reads as
        paused, never as broken and never as blocked."""
        quote = f"{promote_channel.BLOCKED_MARKER} sandbox Reconcile failed"
        _, reason = promote_channel.evaluate(
            _combined("failure", quote), SHA, hold="cutting v6", ancestry="ahead"
        )
        self.assertIn("HELD", reason)

    def test_the_marker_is_what_the_harness_actually_writes(self):
        """One constant, both ends. A receipt the reader does not recognise is
        the wrong sentence printed with full confidence."""
        failure = sandbox_health.SandboxFailure(
            workflow="Reconcile (reusable)",
            run_id=91,
            url="https://github.com/x/y/actions/runs/91",
            when="2026-09-03T20:27:11Z",
            conclusion="failure",
            text="rate limited: 2500 requests/hour exhausted",
            classification="linear_ratelimited",
        )
        line = sandbox_health.receipt_line(failure.quote())
        self.assertTrue(line.startswith(promote_channel.BLOCKED_MARKER))
        _, reason = promote_channel.evaluate(
            _combined("failure", line), SHA, hold=None, ancestry="ahead"
        )
        self.assertIn("blocked", reason.lower())

    def test_the_receipt_survives_githubs_description_clamp(self):
        """GitHub silently truncates a status description at 140 characters.
        The marker leads the line, so a clamped receipt is still recognised."""
        long_quote = sandbox_health.receipt_line(
            f"{promote_channel.BLOCKED_MARKER} sandbox Reconcile failed: "
            + "detail " * 100
        )
        self.assertLessEqual(len(long_quote), 140)
        _, reason = promote_channel.evaluate(
            _combined("failure", long_quote[:140]), SHA, hold=None,
            ancestry="ahead",
        )
        self.assertIn("blocked", reason.lower())


class ReceiptIsSurfacedTest(unittest.TestCase):
    """The reason has to reach a human. promote-channel.yml prints it to the
    step summary — an accurate sentence nobody sees is the same no-op."""

    def _doc(self):
        return yaml.safe_load(WORKFLOW.read_text())

    def test_the_decision_reason_reaches_the_run_summary(self):
        steps = self._doc()["jobs"]["promote"]["steps"]
        summaries = [
            s for s in steps
            if "GITHUB_STEP_SUMMARY" in (s.get("run") or "")
            and "steps.decide.outputs.reason" in (s.get("run") or "")
        ]
        self.assertTrue(summaries, "the receipt must be printed to the summary")

    def test_the_statuses_the_decision_reads_carry_their_descriptions(self):
        """The block is carried IN the description, so the fetch must not
        --jq the field away."""
        steps = self._doc()["jobs"]["promote"]["steps"]
        fetch = [s for s in steps if "combined_status.json" in (s.get("run") or "")]
        self.assertTrue(fetch)
        self.assertNotIn("--jq", fetch[0]["run"])


class DecisionCliTest(unittest.TestCase):
    def test_the_cli_prints_and_exits_zero_on_a_block(self):
        """A refusal is ordinary — the caller branches on `promote`, so a
        blocked run must not crash the promote workflow."""
        import io
        import contextlib
        import tempfile

        quote = f"{promote_channel.BLOCKED_MARKER} sandbox Reconcile failed"
        with tempfile.TemporaryDirectory() as tmp:
            statuses = os.path.join(tmp, "s.json")
            with open(statuses, "w") as fh:
                json.dump(_combined("failure", quote), fh)
            out = os.path.join(tmp, "out.txt")
            os.environ["GITHUB_OUTPUT"] = out
            try:
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    code = promote_channel.main(
                        ["--sha", SHA, "--statuses-file", statuses,
                         "--ancestry", "ahead"]
                    )
            finally:
                os.environ.pop("GITHUB_OUTPUT", None)
            self.assertEqual(code, 0)
            self.assertIn("blocked", buf.getvalue().lower())
            written = open(out).read()
            self.assertIn("promote=false", written)
            # One line per key: a reason with a newline in it would corrupt
            # the output file the workflow parses.
            self.assertEqual(len(written.strip().splitlines()), 2, written)


if __name__ == "__main__":
    unittest.main()
