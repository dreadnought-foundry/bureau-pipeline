"""RED-first tests for the unified dead-run hold cap (DRE-1354).

The regression this pins: an is_error death must increment the SAME hold cap as
a silent death. Before DRE-1354 an is_error failed the job and the medic re-ran
it on the same model, bypassing the counter — so DRE-1300 looped 18× against a
dead model. Now every death class (silent / hung / is_error) shares one cap, and
an is_error death records a `model-error:` marker so the requeue switches models.
"""

import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import dead_run  # noqa: E402
import model_fallback as mf  # noqa: E402

OPUS = "claude-opus-4-8"
FABLE = "claude-fable-5"


class CapTest(unittest.TestCase):
    def test_first_silent_death_requeues(self):
        d = dead_run.decide(0)
        self.assertEqual(d.action, "requeue")
        self.assertIn(dead_run.DEAD_TAG, d.comments[0])
        self.assertIn("1/3", d.comments[0])

    def test_cap_reached_holds(self):
        d = dead_run.decide(2)
        self.assertEqual(d.action, "hold")
        self.assertIn("held-for-human", d.comments[0])
        self.assertIn(dead_run.HOLD_LABEL, d.comments[0])

    def test_is_error_death_counts_toward_same_cap(self):
        # The whole point: an is_error death at the cap HOLDS, exactly like a
        # silent one — no more bypass-the-counter medic loop.
        d = dead_run.decide(2, is_error=True, error_model=OPUS)
        self.assertEqual(d.action, "hold")
        # And every is_error death increments the SAME dead-run-requeue tag, so
        # the next death sees a higher prior count.
        requeue = dead_run.decide(0, is_error=True, error_model=OPUS)
        self.assertEqual(requeue.action, "requeue")
        self.assertIn(dead_run.DEAD_TAG, requeue.comments[0])

    def test_is_error_records_model_marker_for_fallback(self):
        # The requeue comment must carry a model-error: marker so the NEXT
        # attempt's selector switches to the alternate model.
        d = dead_run.decide(0, is_error=True, error_model=OPUS)
        self.assertIn("model-error:", d.comments[0])
        self.assertIn(OPUS, d.comments[0])
        # Round-trip through the real selector: it must now pick Fable.
        self.assertEqual(mf.select_model("engineer", d.comments), FABLE)

    def test_hold_comment_names_both_models_tried(self):
        # AC: at the cap the hold comment names the model(s) tried.
        d = dead_run.decide(2, is_error=True, error_model=FABLE)
        self.assertIn(FABLE, d.comments[0])

    def test_silent_death_has_no_model_marker(self):
        d = dead_run.decide(0, is_error=False)
        self.assertNotIn("model-error:", d.comments[0])

    def test_cap_constant_matches_reconcile(self):
        # The shared cap must equal the reconcile sweep's default so both paths
        # hold at the same point (one unified counter, DRE-1403/1354).
        os.environ.setdefault("REPO", "test/test")
        os.environ.setdefault("GH_TOKEN", "x")
        os.environ.setdefault("LINEAR_API_KEY", "test-key")
        import reconcile
        self.assertEqual(dead_run.REQUEUE_CAP, reconcile.REQUEUE_CAP)


class CliTest(unittest.TestCase):
    SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "dead_run.py")

    def _run(self, *args):
        return subprocess.run(
            [sys.executable, self.SCRIPT, "decide", *args],
            capture_output=True, text=True,
        ).stdout

    def test_cli_requeue_action_on_first_line(self):
        out = self._run("0")
        self.assertEqual(out.splitlines()[0], "requeue")

    def test_cli_hold_at_cap(self):
        out = self._run("2")
        self.assertEqual(out.splitlines()[0], "hold")

    def test_cli_is_error_emits_marker(self):
        out = self._run("0", "--is-error", "--error-model", OPUS)
        self.assertEqual(out.splitlines()[0], "requeue")
        self.assertIn("model-error:", out)
        self.assertIn(OPUS, out)


if __name__ == "__main__":
    unittest.main()
