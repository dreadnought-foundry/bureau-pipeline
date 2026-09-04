"""RED-first tests for DRE-3097 — the turn budget is not a literal 150.

THE INCIDENT (DRE-3088, 2026-09-04 08:45–10:03 PT). Three runs in a row died
at the 150-turn cap — $19.12, $19.12, $17.53 — and every one of them reached
`⏳ 3/5 implementation green` and died in the two steps after it (the commit
ordering for the TDD check, then the PR). The THIRD death came after the card
had already been split to XS: two edits inside two existing steps of
`plan.yml`, plus tests. Splitting did not help, because the cost was never the
size of the change — it was the size of the file the agent has to read and
re-read (`plan.yml` is ~1,850 lines), and the fixed 150 turns were spent
before the PR opened.

`agent-task.yml` carried `--max-turns 150` as a literal. There was no
per-card, per-size or per-repo knob, and the park receipt itself said the card
would sit there *"until a human splits it into smaller pieces (or raises the
turn budget)"* — offering a second option with no handle on it.

WHAT THIS FILE PINS

1. **A per-card budget.** A `turns:<n>` label from a small allowed set
   overrides the default; absent that, the `size:` label maps to a budget;
   absent both, the default is unchanged at 150.
2. **The allowed set is closed.** `turns:10000` is not a budget — the guard on
   spend is the run's own cap (the job wall clock), and the knob may only pick
   from a reviewed set of rungs. A card cannot vote itself an unbounded run.
3. **The receipt says which.** The chosen budget is printed in the
   `🧠 model-attempt` receipt as `turns=<n>`, so a run's own thread answers
   "what budget did this get" without opening Actions.
4. **The park receipt distinguishes the two causes.** Three deaths that each
   reached implementation green are a BUDGET problem and the receipt says so,
   naming the label to raise. Deaths that stalled early are a SIZE problem and
   the receipt says split, exactly as it does today.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
WORKFLOWS = ROOT / ".github" / "workflows"
sys.path.insert(0, str(SCRIPTS))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/test")
os.environ.setdefault("GH_TOKEN", "test")

import dead_run  # noqa: E402
import turn_budget  # noqa: E402


# --------------------------------------------------------------------------- #
# fixtures — a card thread, written the way the pipeline writes one            #
# --------------------------------------------------------------------------- #

def _attempt(run_id: int, turns: int = 150) -> str:
    """The `🧠 model-attempt` heartbeat agent-task.yml posts per run."""
    return (
        f"🧠 model-attempt: claude-opus-5 — engineer agent starting "
        f"(turns={turns}). preferred — the workhorse build model. "
        f"Run: https://github.com/dreadnought-foundry/x/actions/runs/{run_id}"
    )


def _thread(*runs: int, turns: int = 150) -> list[str]:
    """A card's comment thread, oldest→newest: one run per argument, each
    posting its phase receipts up to the milestone it reached and then dying
    at the turn cap. `runs` is the furthest `⏳ n/5` each run got to."""
    bodies: list[str] = []
    for i, reached in enumerate(runs, start=1):
        bodies.append(_attempt(i, turns))
        for n in range(1, reached + 1):
            bodies.append(f"⏳ {n}/5 phase {n}")
        bodies.append(
            f"🪦 {dead_run.TURN_TAG}: the agent ran out of steps — it hit the "
            f"150-turn cap after 151 turns and $19.12 and stopped before "
            f"opening a PR."
        )
    return bodies


#: DRE-3088 itself: three runs, each one reaching `⏳ 3/5 implementation
#: green`, each one dying in the two steps after it.
DRE_3088 = _thread(3, 3, 3)


# --------------------------------------------------------------------------- #
# 1 — label → budget, size → budget, both absent → 150                         #
# --------------------------------------------------------------------------- #

class BudgetFromLabelsTest(unittest.TestCase):

    def test_a_turns_label_sets_the_budget(self):
        """The headline knob: `turns:250` runs with 250 turns."""
        turns, why = turn_budget.budget_for(["repo:x", "agent:engineer", "turns:250"])
        self.assertEqual(250, turns)
        self.assertIn("turns:250", why)

    def test_every_allowed_rung_is_selectable_by_label(self):
        for n in turn_budget.allowed_budgets():
            with self.subTest(n=n):
                self.assertEqual(n, turn_budget.budget_for([f"turns:{n}"])[0])

    def test_the_size_label_sets_the_budget_when_no_turns_label_is_present(self):
        """XS/S → 150, M → 250, L → 400. The size a card was planned at is
        already on the board; a card that carries it should not have to carry
        a second label saying the same thing."""
        for size, expected in (("XS", 150), ("S", 150), ("M", 250), ("L", 400)):
            with self.subTest(size=size):
                turns, why = turn_budget.budget_for([f"size:{size}"])
                self.assertEqual(expected, turns)
                self.assertIn(f"size:{size}", why)

    def test_both_absent_is_the_unchanged_default(self):
        """The acceptance criterion that guards every card that carries
        neither label: nothing about today's behaviour changes."""
        turns, why = turn_budget.budget_for(["repo:bureau-pipeline", "agent:devops"])
        self.assertEqual(150, turns)
        self.assertEqual(150, turn_budget.DEFAULT_TURNS)
        self.assertIn("default", why.lower())

    def test_no_labels_at_all_is_the_default(self):
        self.assertEqual(150, turn_budget.budget_for([])[0])

    def test_the_turns_label_wins_over_the_size_label(self):
        """A `turns:` label is an explicit human decision about THIS card; the
        size is an estimate made before anyone had run it."""
        self.assertEqual(400, turn_budget.budget_for(["size:XS", "turns:400"])[0])

    def test_labels_are_matched_case_insensitively(self):
        self.assertEqual(250, turn_budget.budget_for(["Turns:250"])[0])
        self.assertEqual(250, turn_budget.budget_for(["Size:M"])[0])

    def test_a_budget_outside_the_allowed_set_is_refused_and_said_so(self):
        """THE COST CAP STAYS. The turn count is a knob, not a blank cheque: a
        card cannot label itself into a run nobody reviewed. An unrecognised
        value falls back to what the card would otherwise have got, and the
        note says which value was refused so it is visible rather than silent.
        """
        turns, why = turn_budget.budget_for(["turns:10000"])
        self.assertEqual(150, turns)
        self.assertIn("10000", why)
        self.assertNotIn(10000, turn_budget.allowed_budgets())

    def test_a_refused_budget_still_falls_through_to_the_size(self):
        turns, why = turn_budget.budget_for(["turns:999", "size:M"])
        self.assertEqual(250, turns)
        self.assertIn("999", why)

    def test_a_malformed_turns_label_never_raises(self):
        for label in ("turns:", "turns:abc", "turns:-5", "turns:250x"):
            with self.subTest(label=label):
                self.assertEqual(150, turn_budget.budget_for([label])[0])

    def test_the_allowed_set_is_small_and_bounded(self):
        """The set is the guard. Every rung is a reviewed spend decision in a
        config file, the same way membership of a model ladder is."""
        allowed = turn_budget.allowed_budgets()
        self.assertIn(turn_budget.DEFAULT_TURNS, allowed)
        self.assertLessEqual(len(allowed), 6)
        self.assertLessEqual(max(allowed), 400)
        self.assertEqual(sorted(allowed), list(allowed), "rungs must be ordered")

    def test_every_size_maps_onto_an_allowed_rung(self):
        allowed = set(turn_budget.allowed_budgets())
        for size, turns in turn_budget.size_map().items():
            with self.subTest(size=size):
                self.assertIn(turns, allowed)


class NextRungTest(unittest.TestCase):
    """What the park receipt tells a human to label the card."""

    def test_the_next_rung_up_from_the_default(self):
        self.assertEqual(250, turn_budget.next_rung(150))

    def test_the_next_rung_up_from_a_raised_budget(self):
        self.assertEqual(400, turn_budget.next_rung(250))

    def test_the_top_rung_recommends_itself(self):
        """At the top there is nothing to raise to — the receipt must not
        invent a rung the selector would refuse."""
        top = max(turn_budget.allowed_budgets())
        self.assertEqual(top, turn_budget.next_rung(top))

    def test_the_label_it_names_is_one_the_selector_accepts(self):
        label = turn_budget.label_for(turn_budget.next_rung(150))
        self.assertEqual(250, turn_budget.budget_for([label])[0])


# --------------------------------------------------------------------------- #
# 2 — reading a card's own thread: progress markers and the budget it ran on   #
# --------------------------------------------------------------------------- #

class ThreadReadingTest(unittest.TestCase):

    def test_the_phase_receipts_of_each_run_are_grouped_by_run(self):
        """A run starts at its `🧠 model-attempt` heartbeat. Grouping is what
        makes "every dead run reached implementation green" answerable at
        all — the markers are otherwise one flat list."""
        self.assertEqual([3, 3, 3], turn_budget.runs_progress(DRE_3088))
        self.assertEqual([1, 2, 3], turn_budget.runs_progress(_thread(1, 2, 3)))

    def test_a_run_that_posted_no_phase_receipt_counts_as_zero(self):
        self.assertEqual([0, 2], turn_budget.runs_progress(_thread(0, 2)))

    def test_comments_before_the_first_run_are_not_a_run(self):
        thread = ["🧭 routing-verdict: FLEET", "⏳ 4/5 stray"] + _thread(3)
        self.assertEqual([3], turn_budget.runs_progress(thread))

    def test_the_budget_a_run_used_is_read_back_off_its_own_receipt(self):
        """The receipt is the record. A card already raised to 250 must be
        told to raise to 400, not back to 250."""
        self.assertEqual(250, turn_budget.current_budget(_thread(3, 3, turns=250)))

    def test_a_thread_with_no_receipt_reads_as_the_default(self):
        self.assertEqual(150, turn_budget.current_budget([]))
        self.assertEqual(150, turn_budget.current_budget(["nothing here"]))

    def test_the_phase_label_is_never_parsed_only_the_number(self):
        """The three build briefs spell phase 3 differently — "implementation
        green" (engineer), "code + synth green" (devops), "green" (the
        standard). The NUMBER is the contract; matching prose would make the
        diagnosis role-dependent."""
        self.assertEqual(3, turn_budget.progress_of("⏳ 3/5 code + synth green"))
        self.assertEqual(3, turn_budget.progress_of("⏳ 3/5 implementation green"))
        self.assertIsNone(turn_budget.progress_of("🤖 PR opened: https://x/1"))


# --------------------------------------------------------------------------- #
# 3 — budget, not size: the diagnosis                                          #
# --------------------------------------------------------------------------- #

class DiagnosisTest(unittest.TestCase):

    def test_three_implementation_green_deaths_are_a_budget_problem(self):
        """DRE-3088 itself. The work finishes; the run does not."""
        self.assertTrue(turn_budget.budget_not_size([3, 3, 3]))

    def test_runs_that_got_steadily_further_are_a_budget_problem(self):
        self.assertTrue(turn_budget.budget_not_size([1, 2, 3]))

    def test_deaths_that_stall_before_implementation_green_are_a_size_problem(self):
        """The DRE-2838 shape: capable runs that never finished the work.
        More turns buys more of the same; the card has to get smaller."""
        self.assertFalse(turn_budget.budget_not_size([1, 1]))
        self.assertFalse(turn_budget.budget_not_size([2, 2, 2]))

    def test_a_run_that_went_BACKWARDS_is_not_evidence_of_a_budget(self):
        """"Every dead run reached the same or a later marker" is the test. A
        run that got less far than the one before it is not a card being
        squeezed by a ceiling — it is variance, and variance is not a
        diagnosis."""
        self.assertFalse(turn_budget.budget_not_size([3, 1]))

    def test_one_death_never_diagnoses_anything(self):
        """The hold only fires on the SECOND turn-cap death. One data point
        cannot say "every run" — and the receipt would be claiming a pattern
        from a single sample."""
        self.assertFalse(turn_budget.budget_not_size([3]))
        self.assertFalse(turn_budget.budget_not_size([]))

    def test_the_diagnosis_names_the_label_to_raise_to(self):
        d = turn_budget.diagnose(DRE_3088)
        self.assertTrue(d["budget_not_size"])
        self.assertEqual([3, 3, 3], d["runs"])
        self.assertEqual(150, d["current"])
        self.assertEqual("turns:250", d["label"])

    def test_a_card_already_at_250_is_told_to_raise_to_400(self):
        d = turn_budget.diagnose(_thread(3, 3, turns=250))
        self.assertTrue(d["budget_not_size"])
        self.assertEqual("turns:400", d["label"])

    def test_an_early_stalling_card_diagnoses_size(self):
        d = turn_budget.diagnose(_thread(1, 1, 2))
        self.assertFalse(d["budget_not_size"])


# --------------------------------------------------------------------------- #
# 4 — the park receipt's two texts                                             #
# --------------------------------------------------------------------------- #

#: The exact wording the card asks the budget receipt to carry.
BUDGET_PHRASE = "budget, not size"

#: What the receipt has always said, and must keep saying when the evidence
#: does not support the budget reading.
SPLIT_PHRASE = "splits it into smaller pieces"


def _hold(**kwargs) -> str:
    """The turn-cap hold receipt: the SECOND turn exhaustion on a card."""
    d = dead_run.decide(
        dead_run.TURN_REQUEUE_CAP,
        turn_exhaustion=True,
        turn_facts="the 150-turn cap after 151 turns and $17.53",
        **kwargs,
    )
    assert d.action == "hold", d.action
    return d.comments[0]


class ParkReceiptTest(unittest.TestCase):

    def test_three_implementation_green_deaths_are_reported_as_a_budget_problem(self):
        body = _hold(budget_not_size=True, raise_to="turns:250")
        self.assertIn(BUDGET_PHRASE, body)
        self.assertIn("turns:250", body)
        self.assertNotIn(SPLIT_PHRASE, body)

    def test_the_budget_receipt_says_the_work_finishes_but_the_run_does_not(self):
        """The sentence the card specifies, because it is the sentence that
        stops the next reader re-splitting a card that is already XS."""
        body = _hold(budget_not_size=True, raise_to="turns:250")
        self.assertIn("the work finishes but the run does not", body)

    def test_the_split_receipt_is_unchanged_when_the_evidence_says_size(self):
        body = _hold()
        self.assertIn(SPLIT_PHRASE, body)
        self.assertNotIn(BUDGET_PHRASE, body)

    def test_both_receipts_keep_the_marker_the_medic_and_the_ledger_read(self):
        """`medic_retry.HELD_RECEIPT_MARK` and `split_ledger.TURN_HOLD_MARK`
        both match on this prefix. A receipt that reworded it would be a park
        the medic no longer honours — it would retry a turn-cap death, which
        is the DRE-2954 incident."""
        import medic_retry
        import split_ledger

        for body in (_hold(), _hold(budget_not_size=True, raise_to="turns:250")):
            self.assertIn(medic_retry.HELD_RECEIPT_MARK, body)
            self.assertIn(split_ledger.TURN_HOLD_MARK, body)
            self.assertIn(dead_run.TURN_TAG, body)
            self.assertIn(dead_run.HOLD_LABEL, body)

    def test_the_requeue_receipt_after_ONE_death_still_offers_both_remedies(self):
        """The first death has no pattern to read, so it must not pick a
        side — it says the next death decides."""
        d = dead_run.decide(0, turn_exhaustion=True, turn_facts="the 150-turn cap")
        self.assertEqual("requeue", d.action)
        self.assertIn("turn budget", d.comments[0])

    def test_the_budget_receipt_survives_a_diagnosis_with_no_label(self):
        """Fail-soft: an unreadable thread must never crash the park."""
        body = _hold(budget_not_size=True)
        self.assertIn(BUDGET_PHRASE, body)


class DeadRunCliTest(unittest.TestCase):
    """The workflow calls `dead_run.py decide` and reads line 1 for the
    branch. The diagnosis has to survive that seam."""

    def _decide(self, comments, prior=dead_run.TURN_REQUEUE_CAP):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "comments.json"
            path.write_text(json.dumps(comments))
            out = subprocess.run(
                [sys.executable, str(SCRIPTS / "dead_run.py"), "decide", str(prior),
                 "--turn-exhaustion", "--comments-file", str(path)],
                capture_output=True, text=True, check=True,
            ).stdout
        action, _, body = out.partition("\n\n")
        return action.strip(), body

    def test_the_cli_reads_the_thread_and_reports_a_budget_problem(self):
        action, body = self._decide(DRE_3088)
        self.assertEqual("hold", action)
        self.assertIn(BUDGET_PHRASE, body)
        self.assertIn("turns:250", body)

    def test_the_cli_reports_a_size_problem_on_early_deaths(self):
        action, body = self._decide(_thread(1, 1))
        self.assertEqual("hold", action)
        self.assertIn(SPLIT_PHRASE, body)

    def test_an_unreadable_comments_file_falls_back_to_the_split_text(self):
        """A missing/garbled thread is not evidence of a budget problem. The
        safe direction is the message the pipeline has always sent."""
        out = subprocess.run(
            [sys.executable, str(SCRIPTS / "dead_run.py"), "decide", "1",
             "--turn-exhaustion", "--comments-file", "/nonexistent/x.json"],
            capture_output=True, text=True, check=True,
        ).stdout
        self.assertIn(SPLIT_PHRASE, out)


class TurnBudgetCliTest(unittest.TestCase):
    """`turn_budget.py select` is what the workflow runs. STDOUT is only the
    number — the workflow does `TURNS=$(… select …)`."""

    def _select(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "turn_budget.py"), "select", *args],
            capture_output=True, text=True, check=True,
        )

    def test_stdout_is_only_the_number(self):
        p = self._select("--labels", "repo:x,agent:engineer,turns:250")
        self.assertEqual("250", p.stdout.strip())

    def test_the_note_goes_to_the_explain_file_not_to_stdout(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "why.txt"
            p = self._select("--labels", "size:M", "--explain-file", str(path))
            self.assertEqual("250", p.stdout.strip())
            self.assertIn("size:M", path.read_text())

    def test_no_labels_prints_the_default(self):
        self.assertEqual("150", self._select("--labels", "").stdout.strip())

    def test_an_unreadable_card_degrades_to_the_default_and_exits_zero(self):
        """A Linear read that fails must cost a run its RAISE, never its run.
        The selector fails open to today's behaviour."""
        env = dict(os.environ, LINEAR_API_KEY="")
        p = subprocess.run(
            [sys.executable, str(SCRIPTS / "turn_budget.py"), "select", "DRE-1"],
            capture_output=True, text=True, env=env,
        )
        self.assertEqual(0, p.returncode, p.stderr)
        self.assertEqual("150", p.stdout.strip())


# --------------------------------------------------------------------------- #
# 5 — the wiring: agent-task.yml actually uses it                              #
# --------------------------------------------------------------------------- #

def _step(workflow: str, job: str, ref: str) -> dict:
    """The step with `id: <ref>`, or failing that the one named `<ref>`. Steps
    the workflow itself reads outputs from carry ids; the Report step does
    not, and giving it one purely to be findable from a test is a change to
    the workflow for the test's convenience."""
    doc = yaml.safe_load((WORKFLOWS / workflow).read_text())
    steps = doc["jobs"][job]["steps"]
    for step in steps:
        if step.get("id") == ref:
            return step
    for step in steps:
        if step.get("name") == ref:
            return step
    raise AssertionError(
        f"no step id/name {ref!r} in {workflow} job {job!r} — if the step was "
        f"renamed, update this test rather than deleting it"
    )


class WiringTest(unittest.TestCase):

    def test_the_turn_cap_is_no_longer_a_literal(self):
        """The whole card in one assertion: `--max-turns 150` was a literal
        with no per-card handle on it."""
        args = _step("agent-task.yml", "execute", "claude")["with"]["claude_args"]
        self.assertNotRegex(args, r"--max-turns\s+\d+\s*$|--max-turns\s+\d+\s")
        self.assertRegex(args, r"--max-turns\s+\$\{\{\s*steps\.model\.outputs\.turns")

    def test_the_cap_falls_back_to_the_default_if_the_step_wrote_nothing(self):
        """A bare `--max-turns` would be handed to claude-code-action as a
        flag with no value. The expression carries the default inline."""
        args = _step("agent-task.yml", "execute", "claude")["with"]["claude_args"]
        self.assertIn(str(turn_budget.DEFAULT_TURNS), args)

    def test_the_budget_is_chosen_where_the_model_is_chosen(self):
        """One step reads the card's labels, so a run cannot select a model
        under one reading of the card and a budget under another."""
        step = _step("agent-task.yml", "execute", "model")
        self.assertIn("turn_budget.py", step["run"])
        self.assertIn("turns=", step["run"])
        self.assertIn("LINEAR_API_KEY", yaml.dump(step.get("env") or {}))

    def test_the_model_attempt_receipt_prints_the_budget(self):
        """The acceptance criterion: `turns=250` visible on the card."""
        step = _step("agent-task.yml", "execute", "inprogress")
        self.assertIn("🧠 model-attempt:", step["run"])
        self.assertIn("turns=${{ steps.model.outputs.turns }}", step["run"])

    def test_the_receipt_the_dedupe_guard_reads_is_still_parseable(self):
        """dedupe_dispatch pulls the run id out of this exact string. Adding
        the budget must not move the run URL out of its reach."""
        import dedupe_dispatch

        body = _attempt(33999, 250)
        self.assertTrue(body.lstrip().startswith(dedupe_dispatch.RUN_MARKER))
        self.assertEqual("33999", dedupe_dispatch.heartbeat_run_id([body]))
        self.assertEqual(250, turn_budget.current_budget([body]))

    def test_the_report_step_hands_the_thread_to_the_dead_run_decision(self):
        """Without the thread there is no diagnosis — the receipt would fall
        back to "split" on every card, including the ones it is wrong for."""
        run = _step("agent-task.yml", "execute", "Report result to Linear")["run"]
        self.assertIn("dump-comments", run)
        self.assertIn("--comments-file", run)

    def test_the_cost_guard_is_untouched(self):
        """"Cost cap stays: a larger budget never exceeds the run's cap." The
        guard on spend is the job's wall clock, and this card does not move
        it — the turn count is the knob, the clock is the guard."""
        doc = yaml.safe_load((WORKFLOWS / "agent-task.yml").read_text())
        self.assertEqual(120, doc["jobs"]["execute"]["timeout-minutes"])

    def test_the_config_is_a_file_in_this_checkout(self):
        """Same constraint as models.yaml and repo-map.json: the workflows
        that read it run with no credentials for a private lookup."""
        self.assertTrue(turn_budget.CONFIG_PATH.exists())
        json.loads(turn_budget.CONFIG_PATH.read_text())

    def test_the_config_README_documents_the_file(self):
        readme = (ROOT / "config" / "README.md").read_text()
        self.assertIn(turn_budget.CONFIG_PATH.name, readme)


class ConfigDegradationTest(unittest.TestCase):
    """An unreadable config must never be the thing that stops a build."""

    def test_an_unreadable_config_degrades_to_the_default_map(self):
        turn_budget.clear_config_cache()
        with tempfile.TemporaryDirectory() as tmp:
            broken = Path(tmp) / "turn-budgets.json"
            broken.write_text("{ this is not json")
            cfg = turn_budget.load_config(broken)
        self.assertEqual(150, turn_budget.default_budget(cfg))
        self.assertEqual(150, turn_budget.budget_for(["size:XS"], cfg)[0])
        turn_budget.clear_config_cache()

    def test_a_missing_config_degrades_to_the_default_map(self):
        cfg = turn_budget.load_config(Path("/nonexistent/turn-budgets.json"))
        self.assertEqual(150, turn_budget.budget_for([], cfg)[0])
        self.assertEqual(250, turn_budget.budget_for(["turns:250"], cfg)[0])


class StandardIsCurrentTest(unittest.TestCase):
    """A change that contradicts a document updates that document in the same
    PR. `standards/card-quality.md` told every reader that a second turn-cap
    park IS the signal to split — which is now only one of its two answers."""

    def test_the_split_standard_names_the_budget_answer(self):
        text = (ROOT / "standards" / "card-quality.md").read_text()
        self.assertIn("turns:", text)
        self.assertIn(BUDGET_PHRASE, text)


if __name__ == "__main__":
    unittest.main()
