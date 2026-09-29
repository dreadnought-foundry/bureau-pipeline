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

    def test_is_error_records_model_marker_and_is_readable(self):
        # The requeue comment must still carry a model-error: marker — it counts
        # the death toward the shared hold cap and lets the board attribute the
        # last is_error death to a model. (Under DRE-1490 selection no longer
        # routes off this marker; it walks the availability ladder instead. The
        # marker stays for hold-cap accounting + board attribution.)
        d = dead_run.decide(0, is_error=True, error_model=OPUS)
        self.assertIn("model-error:", d.comments[0])
        self.assertIn(OPUS, d.comments[0])
        # The marker round-trips through the selector's reader.
        self.assertEqual(mf.last_error_model(d.comments), OPUS)

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


# --------------------------------------------------------------------------- #
# DRE-4366 — a turn-ceiling death is READ before it is retried                  #
# --------------------------------------------------------------------------- #

#: What the 400-turn ceiling leaves in a receipt (check_agent_result's clause).
FACTS = "the 400-turn cap after 401 turns and $21.40"

#: The three first-line prefixes the contract names, exactly.
REQUEUE_PREFIX = "🪦 turn-exhaustion-requeue:"
REPLAN_PREFIX = "✂️ turn-exhaustion-requeue → Planning:"
HOLD_PREFIX = "🚨 held-for-human (turn-exhaustion-requeue cap reached)"


def _turn(prior=0, **kw):
    return dead_run.decide(prior, turn_exhaustion=True, turn_facts=FACTS, **kw)


def _first_line(body: str) -> str:
    return body.split("\n", 1)[0]


class TurnDeathIsReadFirstTest(unittest.TestCase):
    """The three rules, in the card's order, off `last_progress` and
    `prior_dead` alone."""

    def test_past_implementation_green_the_first_death_requeues(self):
        d = _turn(0, last_progress=3)
        self.assertEqual("requeue", d.action)
        body = d.comments[0]
        self.assertTrue(body.startswith(REQUEUE_PREFIX), body)
        self.assertIn("⏳ 3/5", body)
        self.assertIn(FACTS, body)
        self.assertIn("budget, not size", body)
        self.assertIn("same budget", body)
        self.assertIn(dead_run.HOLD_LABEL, body, "it must say a second death parks")

    def test_a_later_marker_is_past_green_too(self):
        for reached in (4, 5):
            self.assertEqual("requeue", _turn(0, last_progress=reached).action)

    def test_before_implementation_green_the_card_goes_to_planning(self):
        d = _turn(0, last_progress=2)
        self.assertEqual("replan", d.action)
        body = d.comments[0]
        self.assertTrue(body.startswith(REPLAN_PREFIX), body)
        self.assertIn("size, not budget", body)
        self.assertIn("⏳ 2/5", body)
        self.assertIn("Planning", body)

    def test_no_marker_at_all_goes_to_planning(self):
        for none_reached in (None, 0):
            d = _turn(0, last_progress=none_reached)
            self.assertEqual("replan", d.action, none_reached)
            self.assertTrue(d.comments[0].startswith(REPLAN_PREFIX))

    def test_the_default_is_no_marker(self):
        """A caller that read nothing has no evidence the work finished."""
        self.assertEqual("replan", _turn(0).action)

    def test_a_replan_is_never_a_retry_whatever_the_count(self):
        for prior in (0, 1, 2, 5):
            self.assertEqual("replan", _turn(prior, last_progress=1).action)

    def test_the_second_death_past_green_holds(self):
        d = _turn(dead_run.TURN_REQUEUE_CAP, last_progress=3)
        self.assertEqual("hold", d.action)
        body = d.comments[0]
        self.assertTrue(body.startswith(HOLD_PREFIX), body)
        self.assertIn("budget, not size", body)
        self.assertIn(dead_run.HOLD_LABEL, body)
        self.assertIn("Backlog", body)

    def test_a_run_that_reached_green_is_never_diagnosed_as_split(self):
        for prior in (0, dead_run.TURN_REQUEUE_CAP):
            body = _turn(prior, last_progress=3).comments[0]
            self.assertNotIn("split", body.lower(), body)

    def test_every_turn_outcome_carries_the_tag_on_its_first_line(self):
        bodies = [
            _turn(0, last_progress=3).comments[0],
            _turn(0, last_progress=2).comments[0],
            _turn(1, last_progress=3).comments[0],
            _turn(0, last_progress=3, cancelled=True).comments[0],
            dead_run.park_unlanded_comment("https://r", dead_run.TURN_TAG),
        ]
        for body in bodies:
            self.assertIn(dead_run.TURN_TAG, _first_line(body), body)

    def test_the_replan_mark_is_the_contract_prefix(self):
        self.assertEqual(REPLAN_PREFIX, dead_run.REPLAN_MARK)


class CancelledTurnDeathKeepsItsTagTest(unittest.TestCase):
    """A job-timeout kill that the step classifies as turn exhaustion used to
    answer `defer` with a receipt carrying no tag at all — a death the ledger
    and the count never saw."""

    def test_the_action_is_still_defer(self):
        self.assertEqual("defer", _turn(0, last_progress=3, cancelled=True).action)
        self.assertEqual("defer", _turn(0, last_progress=1, cancelled=True).action)

    def test_the_receipt_carries_the_turn_tag_and_never_the_dead_tag(self):
        body = _turn(0, last_progress=1, cancelled=True).comments[0]
        self.assertIn(dead_run.TURN_TAG, _first_line(body))
        self.assertNotIn(dead_run.DEAD_TAG, body)
        self.assertFalse(body.startswith(("⏳", "🧠")), body)

    def test_an_ordinary_cancel_is_unchanged(self):
        body = dead_run.decide(0, cancelled=True).comments[0]
        self.assertNotIn(dead_run.TURN_TAG, body)
        self.assertNotIn(dead_run.DEAD_TAG, body)


class TheWallOutranksTheReadingTest(unittest.TestCase):
    """The `limit` route stays above the turn-exhaustion branch."""

    WALL = dead_run.LimitDeath(kind="claude", stage="build", reset=None,
                               run_id="35500000000")

    def test_a_limit_death_is_a_limit_whatever_the_reading(self):
        for reached in (None, 0, 2, 3, 5):
            for prior in (0, dead_run.TURN_REQUEUE_CAP):
                d = _turn(prior, last_progress=reached, limit=self.WALL)
                self.assertEqual("limit", d.action, (reached, prior))
                self.assertNotIn(dead_run.TURN_TAG, d.comments[0])


class DecideSignatureTest(unittest.TestCase):
    """`last_progress` is the one fact this card adds, and every parameter
    `decide()` accepts is read by a branch."""

    def _params(self):
        import inspect

        return list(inspect.signature(dead_run.decide).parameters)

    def test_last_progress_is_a_parameter(self):
        self.assertIn("last_progress", self._params())

    def test_no_fact_for_a_rule_that_does_not_exist(self):
        for gone in ("at_top", "budget_not_size", "raise_to"):
            self.assertNotIn(gone, self._params())

    def test_every_parameter_is_read(self):
        import ast
        import inspect
        import textwrap

        tree = ast.parse(textwrap.dedent(inspect.getsource(dead_run.decide)))
        func = tree.body[0]
        read = {
            node.id
            for stmt in func.body
            for node in ast.walk(stmt)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
        }
        unread = [p for p in self._params() if p not in read]
        self.assertEqual([], unread)


def _thread(*runs):
    """A card thread: one `🧠 model-attempt` block per run, each reaching the
    given `⏳ n/5` marker."""
    labels = {1: "plan formed", 2: "failing tests written",
              3: "implementation green", 4: "local checks", 5: "PR opened"}
    bodies = ["a human's comment quoting ⏳ 4/5 before any run"]
    for i, reached in enumerate(runs, start=1):
        bodies.append(f"🧠 model-attempt: claude-opus-5 — engineer agent "
                      f"starting (turns=400). Run: https://r/{i}")
        for n in range(1, reached + 1):
            bodies.append(f"⏳ {n}/5 {labels[n]}")
    return bodies


class TheCliReadsTheThreadTest(unittest.TestCase):
    """`dead_run.main` derives `last_progress` from --comments-file, and a
    replan receipt carries the run's markers and the hand-back."""

    def _main(self, thread, prior="0", handback=None, extra=()):
        import contextlib
        import io
        import json
        import tempfile
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            comments = os.path.join(tmp, "comments.json")
            with open(comments, "w", encoding="utf-8") as fh:
                json.dump(thread, fh)
            handback_path = os.path.join(tmp, "agent-handback.txt")
            if handback is not None:
                with open(handback_path, "w", encoding="utf-8") as fh:
                    fh.write(handback)
            buf = io.StringIO()
            with mock.patch.object(dead_run, "HANDBACK_PATH", handback_path):
                with contextlib.redirect_stdout(buf):
                    rc = dead_run.main(["decide", prior, "--turn-exhaustion",
                                        "--comments-file", comments, *extra])
        self.assertEqual(0, rc)
        action, _, body = buf.getvalue().partition("\n\n")
        return action.strip(), body

    def test_the_last_run_past_green_requeues(self):
        action, body = self._main(_thread(3))
        self.assertEqual("requeue", action)
        self.assertIn("⏳ 3/5", body)

    def test_the_last_run_is_read_not_the_furthest_one(self):
        action, _ = self._main(_thread(4, 1))
        self.assertEqual("replan", action)

    def test_a_replan_lists_this_runs_markers_only(self):
        action, body = self._main(_thread(5, 2))
        self.assertEqual("replan", action)
        self.assertTrue(body.startswith(REPLAN_PREFIX))
        self.assertIn("⏳ 1/5 plan formed", body)
        self.assertIn("⏳ 2/5 failing tests written", body)
        self.assertNotIn("⏳ 3/5", body, "an earlier run's marker is not this run's")
        self.assertNotIn("⏳ 4/5", body, "a comment before any run is not a marker")

    def test_a_replan_attaches_the_hand_back_the_agent_wrote(self):
        note = "1. the ledger counter\n2. the medic refusal\n"
        _, body = self._main(_thread(2), handback=note)
        self.assertIn("1. the ledger counter", body)
        self.assertIn("2. the medic refusal", body)

    def test_no_hand_back_means_no_hand_back_section(self):
        _, with_note = self._main(_thread(2), handback="1. a piece\n")
        _, without = self._main(_thread(2))
        self.assertIn("1. a piece", with_note)
        self.assertNotIn("1. a piece", without)
        self.assertTrue(without.startswith(REPLAN_PREFIX))

    def test_a_run_with_no_marker_says_so(self):
        action, body = self._main(_thread(0))
        self.assertEqual("replan", action)
        self.assertIn("no progress marker", body)

    def test_a_second_death_past_green_holds(self):
        action, body = self._main(_thread(3, 3), prior="1")
        self.assertEqual("hold", action)
        self.assertTrue(body.startswith(HOLD_PREFIX))

    def test_the_hand_back_never_rides_on_a_requeue(self):
        _, body = self._main(_thread(3), handback="1. a piece\n")
        self.assertNotIn("1. a piece", body)

    def test_an_unreadable_thread_is_no_evidence_the_work_finished(self):
        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            dead_run.main(["decide", "0", "--turn-exhaustion",
                           "--comments-file", "/nonexistent/comments.json"])
        self.assertEqual("replan", buf.getvalue().splitlines()[0])


class TurnNotedTest(unittest.TestCase):
    """A turn-cap death that takes one of the agent's own exits (escalation,
    blocker) or a no-decision path (unreadable PR state, a rescue delivery)
    still leaves the tag, on a receipt that moves nothing."""

    def test_the_noted_receipt_carries_the_tag_on_its_first_line(self):
        body = dead_run.turn_noted_comment("escalation", FACTS, "https://r")
        self.assertIn(dead_run.TURN_TAG, _first_line(body))
        self.assertIn(FACTS, body)
        self.assertNotIn(dead_run.DEAD_TAG, body)

    def test_the_cli_prints_it(self):
        out = subprocess.run(
            [sys.executable, CliTest.SCRIPT, "turn-noted", "--exit", "blocker",
             "--run-url", "https://r"],
            capture_output=True, text=True, check=True,
        ).stdout
        self.assertIn(dead_run.TURN_TAG, out.splitlines()[0])
        self.assertIn("https://r", out)


if __name__ == "__main__":
    unittest.main()
