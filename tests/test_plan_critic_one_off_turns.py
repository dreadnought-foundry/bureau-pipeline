"""The one-off critic gets enough turns to finish, and when it runs out it says
so (DRE-4381).

The critic that reads a single card before it is built ran at a LITERAL
`--max-turns 20`. On 2026-09-20 it ran out of turns twice on one card,
DRE-4378 — 21 turns and $0.69 the second time (Agent Plan run 35523230304:
`error_max_turns`, "Reached maximum number of turns (20)") — wrote no verdict
either time, and the card parked in the CEO's Green Light queue with the
sentence *"the reader did not answer at all, so nothing has checked this
card"*.

That sentence is untrue, and the untruth is the expensive half: the reader was
working and was cut off. The CEO answered, the card came back, and the same
thing happened again — because nothing about a re-run changes the ceiling, and
a turn ceiling is DETERMINISTIC.

What this file pins, in the order the card asks for it:

  * a result of `error_max_turns` produces a card note that NAMES the ceiling
    and says the critic ran out of turns — the RED test;
  * the ceiling is sized from the card rather than written as a literal: a base
    plus an allowance per path on the card's `**Files:**` line, floored and
    capped, the post-approval critic's own shape (`post_review_turns`), and a
    card naming five files gets at least 40;
  * a critic that was NEVER REACHED still reports the no-answer wording, word
    for word — the two failures are different facts and stay different;
  * after a turn-ceiling death the next read takes the next ceiling up, ONCE,
    so a CEO's answer is not spent on a repeat of the same death;
  * the rail carries all of it — no literal on the step, the ceiling
    interpolated from the step that sizes it, and the death recorded where the
    next run reads it.

Run: cd bureau-pipeline && python3 -m pytest tests/test_plan_critic_one_off_turns.py -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = os.path.join(os.path.dirname(__file__), "..")
SCRIPTS = os.path.join(ROOT, "scripts")
WF = os.path.join(ROOT, ".github", "workflows", "plan.yml")

sys.path.insert(0, SCRIPTS)

import plan_critic as pc  # noqa: E402
import planning_escalation  # noqa: E402

CARD = "DRE-4378"

#: The `**Files:**` line of this very card, verbatim — the standard DRE-4359
#: asks every card to carry, parenthetical and all.
FILES_LINE = (
    "**Files:** .github/workflows/plan.yml, scripts/plan_critic.py, "
    "tests/test_plan_critic.py (or the existing test file for the one-off "
    "critic's result handling)"
)


def ran_out(ceiling: int = 36, turns: int | None = None,
            subtype: str | None = None) -> dict:
    """A death row as `plan_critic.hit_the_turn_cap` reads one — the shape the
    decision is handed off the action's own execution file."""
    return {
        "stage": pc.STAGE_ONE_OFF,
        "run": "35523230304",
        "attempt": 1,
        "step": "oocritic",
        "subtype": pc.TURN_CAP_SUBTYPE if subtype is None else subtype,
        "turns": ceiling if turns is None else turns,
        "ceiling": ceiling,
    }


def tombstone(ceiling: int, run: str = "35523230304",
              stage: str | None = None, subtype: str | None = None,
              turns: int | None = None, trusted: bool = True) -> dict:
    """One tombstone comment on the card, as `dump-comments --with-authors`
    hands it over — a RECORD, so `trusted_bodies` is exercised rather than
    bypassed."""
    body = pc.death_marker(
        pc.STAGE_ONE_OFF if stage is None else stage, run, 1, "oocritic",
        pc.TURN_CAP_SUBTYPE if subtype is None else subtype,
        ceiling if turns is None else turns, ceiling)
    return {"body": body, "authored_by_pipeline": trusted}


class TheCeilingIsSizedFromTheCard(unittest.TestCase):
    """AC2. The ceiling is arithmetic over what the card says it touches, in
    the post-approval critic's own shape: a base, an allowance, a floor and a
    cap."""

    def test_a_card_naming_five_files_gets_at_least_forty_turns(self):
        body = ("## The change\n\n**Files:** a/one.py, a/two.py, a/three.py, "
                "a/four.py, a/five.py\n")
        self.assertEqual(5, pc.files_named(body))
        self.assertGreaterEqual(pc.one_off_turns(5), 40)
        self.assertGreaterEqual(pc.one_off_ceiling(pc.files_named(body), [])[0], 40)

    def test_it_reads_this_cards_own_files_line(self):
        """Three paths and a parenthetical — the prose in the brackets names no
        file and must not be counted as one."""
        self.assertEqual(3, pc.files_named(FILES_LINE))

    def test_a_card_with_no_files_line_still_clears_the_ceiling_that_died(self):
        self.assertEqual(0, pc.files_named("no files line anywhere here"))
        self.assertEqual(pc.ONE_OFF_TURNS_FLOOR, pc.one_off_turns(0))
        self.assertGreater(pc.ONE_OFF_TURNS_FLOOR, 20,
                           "the floor must clear the ceiling DRE-4378 died at")

    def test_the_allowance_scales_with_the_paths_and_stops_at_the_cap(self):
        sized = [pc.one_off_turns(n) for n in range(0, 40)]
        self.assertEqual(sized, sorted(sized), "more files must never mean fewer turns")
        self.assertEqual(pc.ONE_OFF_TURNS_CAP, pc.one_off_turns(1000))
        self.assertGreater(pc.one_off_turns(6), pc.one_off_turns(2))

    def test_the_shape_is_base_plus_allowance(self):
        for n in (2, 5, 7):
            self.assertEqual(
                max(pc.ONE_OFF_TURNS_FLOOR,
                    min(pc.ONE_OFF_TURNS_CAP,
                        pc.ONE_OFF_TURNS_BASE + pc.ONE_OFF_TURNS_PER_FILE * n)),
                pc.one_off_turns(n))

    def test_an_unreadable_count_is_the_default_and_never_zero(self):
        """Unknown is not zero (standards/console-honesty.md rule 2), and the
        default is derived rather than a second constant."""
        self.assertEqual(pc.ONE_OFF_TURNS_DEFAULT, pc.one_off_turns(5))
        for unknown in (None, "", "lots", -1):
            self.assertEqual(pc.ONE_OFF_TURNS_DEFAULT, pc.one_off_turns(unknown))

    def test_a_files_line_is_read_however_it_is_written(self):
        for line in ("**Files:** one/a.py, two/b.py",
                     "Files: one/a.py two/b.py",
                     "- **Files:** one/a.py, two/b.py"):
            self.assertEqual(2, pc.files_named(line), line)

    def test_a_path_named_twice_is_one_file(self):
        self.assertEqual(1, pc.files_named("**Files:** a/one.py, a/one.py"))


class TheRunThatRanOutOfTurnsSaysSo(unittest.TestCase):
    """AC1 — the RED test. A one-off read cut off at its ceiling produces a
    note that says what happened and names the number, instead of claiming
    nothing read the card."""

    def test_the_note_says_it_ran_out_and_names_the_ceiling(self):
        action, note = pc.one_off_decide(pc.NO_RESULT, "", 0, ran_out=ran_out(36))
        self.assertEqual(pc.ESCALATE, action)
        self.assertIn("ran out of turns", note.lower())
        self.assertIn("36", note)
        self.assertNotIn("did not answer", note.lower())

    def test_the_note_says_it_is_not_a_judgement_on_the_card(self):
        _, note = pc.one_off_decide(pc.NO_RESULT, "", 0, ran_out=ran_out(36))
        self.assertIn("not a judgement", note.lower())

    def test_the_escalation_the_ceo_reads_says_the_same_thing(self):
        text = pc.one_off_escalation(pc.NO_RESULT, "", 0, (), ran_out=ran_out(36))
        self.assertIn("ran out", text.lower())
        self.assertIn("36", text)
        self.assertNotIn("did not answer at all", text)
        self.assertIn("not a judgement", text.lower())

    def test_the_escalation_is_fit_to_put_in_front_of_the_ceo(self):
        text = pc.one_off_escalation(pc.NO_RESULT, "", 0, (), ran_out=ran_out(36))
        self.assertEqual((), planning_escalation.jargon(text))
        self.assertTrue(text.rstrip().endswith("?"), "one ask, as the closing line")

    def test_it_says_the_next_read_gets_more_room(self):
        text = pc.one_off_escalation(pc.NO_RESULT, "", 0, (), ran_out=ran_out(36))
        self.assertIn("more room", text.lower())

    def test_an_unknown_ceiling_is_said_as_unknown_and_never_as_a_number(self):
        _, note = pc.one_off_decide(pc.NO_RESULT, "", 0,
                                    ran_out=ran_out(36) | {"ceiling": None})
        self.assertIn("ran out of turns", note.lower())
        self.assertNotIn("None", note)

    def test_a_run_that_finished_at_its_ceiling_reads_as_the_turn_cap_too(self):
        """`hit_the_turn_cap`'s second reading (DRE-3501) — the numbers decide,
        not the enum, and this route reads it through the same predicate."""
        row = ran_out(36, turns=37, subtype="success")
        self.assertTrue(pc.hit_the_turn_cap(row))
        _, note = pc.one_off_decide(pc.NO_RESULT, "", 0, ran_out=row)
        self.assertIn("ran out of turns", note.lower())

    def test_a_decided_round_is_untouched_by_the_ceiling(self):
        """A critic that WROTE its verdict decided, whatever the action said
        afterwards: the turn cap only ever changes what a no-result says."""
        self.assertEqual(
            pc.one_off_decide(pc.PASS, "", 0),
            pc.one_off_decide(pc.PASS, "", 0, ran_out=ran_out(36)))
        self.assertEqual(
            pc.one_off_decide(pc.SEND_BACK, "this is a decision", 0),
            pc.one_off_decide(pc.SEND_BACK, "this is a decision", 0,
                              ran_out=ran_out(36)))

    def test_no_wording_here_can_forge_a_merge_credential(self):
        for text in (pc.one_off_decide(pc.NO_RESULT, "", 0, ran_out=ran_out(36))[1],
                     pc.one_off_escalation(pc.NO_RESULT, "", 0, (),
                                           ran_out=ran_out(36))):
            for marker in ("VERDICT:", "QA Critic", "QA Verifier"):
                self.assertNotIn(marker, text)


class AReaderThatWasNeverReachedIsUnchanged(unittest.TestCase):
    """AC3. A genuine no-answer — the model was never reached — keeps today's
    wording, word for word. The two failures have different next actions and
    must not be told as one."""

    def test_the_note_is_word_for_word_what_it_always_was(self):
        self.assertEqual((pc.ESCALATE, pc.NO_CRITIC_NOTE),
                         pc.one_off_decide(pc.NO_RESULT))
        self.assertEqual((pc.ESCALATE, pc.NO_CRITIC_NOTE),
                         pc.one_off_decide(pc.NO_RESULT, "", 0, ran_out=None))

    def test_the_escalation_is_word_for_word_what_it_always_was(self):
        text = pc.one_off_escalation(pc.NO_RESULT)
        self.assertIn("the reader did not answer at all, so nothing has "
                      "checked this card", text)
        self.assertEqual(text, pc.one_off_escalation(pc.NO_RESULT, "", 0, (),
                                                     ran_out=None))

    def test_a_death_that_is_not_the_turn_cap_is_not_a_turn_cap(self):
        """An auth death — one turn, no ceiling reached — is the no-answer
        case and keeps the no-answer words."""
        row = ran_out(36, turns=1, subtype="error_during_execution")
        self.assertFalse(pc.hit_the_turn_cap(row))
        self.assertEqual(pc.NO_CRITIC_NOTE,
                         pc.one_off_decide(pc.NO_RESULT, "", 0, ran_out=row)[1])


class TheRerunTakesTheNextCeilingUpOnce(unittest.TestCase):
    """AC4. The re-run a CEO's answer brings back gets the next ceiling up —
    once. An answer spent on a repeat of the same death is the whole cost this
    card was written about."""

    def test_a_first_read_is_sized_from_the_card_alone(self):
        turns, why = pc.one_off_ceiling(3, [])
        self.assertEqual(pc.one_off_turns(3), turns)
        self.assertIn("file", why)

    def test_after_a_turn_ceiling_death_the_next_read_gets_more(self):
        died_at = pc.one_off_turns(3)
        turns, why = pc.one_off_ceiling(3, [tombstone(died_at)])
        self.assertGreater(turns, died_at)
        self.assertEqual(pc.one_off_retry_ceiling(died_at), turns)
        self.assertIn(str(died_at), why)

    def test_the_raise_happens_exactly_once(self):
        died_at = pc.one_off_turns(3)
        raised = pc.one_off_retry_ceiling(died_at)
        again, _ = pc.one_off_ceiling(
            3, [tombstone(died_at, run="1"), tombstone(raised, run="2")])
        self.assertEqual(raised, again,
                         "the one raise is spent — a third read does not raise again")
        self.assertGreaterEqual(again, pc.one_off_turns(3),
                                "and it never drops back below the sized ceiling")

    def test_the_raise_is_capped(self):
        self.assertEqual(pc.ONE_OFF_TURNS_RETRY_CAP,
                         pc.one_off_retry_ceiling(pc.ONE_OFF_TURNS_RETRY_CAP * 4))
        self.assertGreaterEqual(pc.ONE_OFF_TURNS_RETRY_CAP, pc.ONE_OFF_TURNS_CAP)

    def test_an_unknown_ceiling_is_sized_from_the_default_not_from_zero(self):
        self.assertEqual(pc.one_off_retry_ceiling(pc.ONE_OFF_TURNS_DEFAULT),
                         pc.one_off_retry_ceiling(None))
        self.assertGreater(pc.one_off_retry_ceiling(None), 0)

    def test_a_death_that_was_not_the_turn_cap_raises_nothing(self):
        turns, _ = pc.one_off_ceiling(
            3, [tombstone(48, subtype="error_during_execution", turns=1)])
        self.assertEqual(pc.one_off_turns(3), turns)

    def test_another_stages_death_raises_nothing(self):
        """The post-approval review's tombstones live on epics and are read by
        `review_rerun`; one on this card must not move this ceiling."""
        turns, _ = pc.one_off_ceiling(3, [tombstone(100, stage=pc.STAGE_POST)])
        self.assertEqual(pc.one_off_turns(3), turns)

    def test_a_tombstone_the_pipeline_did_not_write_raises_nothing(self):
        """The credential is `trusted_bodies`': anyone with comment access on
        the card can post the line, and a raised ceiling is spend."""
        turns, _ = pc.one_off_ceiling(3, [tombstone(48, trusted=False)])
        self.assertEqual(pc.one_off_turns(3), turns)

    def test_a_tombstone_sharing_its_comment_with_prose_raises_nothing(self):
        record = tombstone(48)
        record["body"] = "the critic died, here is why:\n" + record["body"]
        turns, _ = pc.one_off_ceiling(3, [record])
        self.assertEqual(pc.one_off_turns(3), turns)


class TheCliCarriesIt(unittest.TestCase):
    """End to end through the two commands plan.yml actually runs, because the
    note the card gets is written by the CLI and not by the functions above."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _decide(self, result_text: str, execution: dict | None,
                ceiling: str = "36", records=()) -> dict:
        paths = {name: os.path.join(self.tmp, f"{name}.txt")
                 for name in ("result", "note", "record", "escalation", "out",
                              "execution")}
        with open(paths["result"], "w", encoding="utf-8") as f:
            f.write(result_text)
        argv = [sys.executable, os.path.join(SCRIPTS, "plan_critic.py"), "decide",
                "--stage", pc.STAGE_ONE_OFF, "--epic", CARD,
                "--result-file", paths["result"],
                "--github-output", paths["out"],
                "--note-file", paths["note"],
                "--record-file", paths["record"],
                "--escalation-file", paths["escalation"],
                "--ceiling", ceiling]
        if execution is not None:
            with open(paths["execution"], "w", encoding="utf-8") as f:
                json.dump(execution, f)
            argv += ["--execution-file", paths["execution"]]
        out = subprocess.run(argv, input=json.dumps(list(records)),
                             capture_output=True, text=True)
        self.assertEqual(0, out.returncode, out.stderr)
        outputs = {}
        for line in open(paths["out"], encoding="utf-8"):
            if "=" in line:
                key, value = line.rstrip("\n").split("=", 1)
                outputs[key] = value
        read = {name: (open(path, encoding="utf-8").read()
                       if os.path.exists(path) else "")
                for name, path in paths.items()}
        return {"outputs": outputs, **read}

    def test_a_run_cut_off_at_its_ceiling_says_so_on_the_card(self):
        run = self._decide("", {"subtype": pc.TURN_CAP_SUBTYPE, "num_turns": 21,
                                "is_error": True})
        self.assertEqual(pc.ESCALATE, run["outputs"]["action"])
        self.assertEqual("true", run["outputs"]["ran_out"])
        self.assertIn("ran out of turns", run["note"].lower())
        self.assertIn("36", run["note"])
        self.assertIn("ran out", run["escalation"].lower())
        self.assertNotIn("did not answer at all", run["escalation"])

    def test_a_reader_never_reached_writes_the_words_it_always_wrote(self):
        run = self._decide("", None)
        self.assertEqual(pc.ESCALATE, run["outputs"]["action"])
        self.assertEqual("false", run["outputs"]["ran_out"])
        self.assertIn(pc.NO_CRITIC_NOTE, run["note"])
        self.assertIn("the reader did not answer at all", run["escalation"])

    def test_a_missing_execution_file_is_not_a_turn_cap(self):
        run = self._decide("", None, ceiling="36")
        self.assertEqual("false", run["outputs"]["ran_out"])

    def test_a_pass_still_reaches_the_build_queue_and_records_no_death(self):
        run = self._decide(pc.result_line(pc.PASS, "one pull request of work"),
                           {"subtype": pc.TURN_CAP_SUBTYPE, "num_turns": 36,
                            "is_error": True})
        self.assertEqual(pc.PROCEED, run["outputs"]["action"])
        self.assertEqual("false", run["outputs"]["ran_out"])
        self.assertEqual("", run["escalation"])

    def test_the_epic_route_is_untouched(self):
        """`--ceiling` and `--execution-file` are this route's; the epic route
        decides exactly as it did."""
        paths = {name: os.path.join(self.tmp, f"epic-{name}.txt")
                 for name in ("result", "note", "out")}
        with open(paths["result"], "w", encoding="utf-8") as f:
            f.write(pc.result_line(pc.SEND_BACK, "the plan names no proof card"))
        out = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS, "plan_critic.py"), "decide",
             "--stage", pc.STAGE_PRE, "--epic", "DRE-1",
             "--result-file", paths["result"],
             "--github-output", paths["out"],
             "--note-file", paths["note"]],
            input="[]", capture_output=True, text=True)
        self.assertEqual(0, out.returncode, out.stderr)
        self.assertNotIn("ran_out=", open(paths["out"], encoding="utf-8").read())

    def _turns(self, description: str, records=()) -> dict:
        body = os.path.join(self.tmp, "body.md")
        out_path = os.path.join(self.tmp, "turns-out.txt")
        with open(body, "w", encoding="utf-8") as f:
            f.write(description)
        out = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS, "plan_critic.py"),
             "one-off-turns", "--description-file", body,
             "--github-output", out_path],
            input=json.dumps(list(records)), capture_output=True, text=True)
        self.assertEqual(0, out.returncode, out.stderr)
        outputs = {}
        for line in open(out_path, encoding="utf-8"):
            if "=" in line:
                key, value = line.rstrip("\n").split("=", 1)
                outputs[key] = value
        return outputs

    def test_the_ceiling_command_sizes_from_the_cards_files_line(self):
        outputs = self._turns(f"Some prose.\n\n{FILES_LINE}\n")
        self.assertEqual(str(pc.one_off_turns(3)), outputs["max_turns"])

    def test_the_ceiling_command_raises_after_a_death_on_the_card(self):
        died_at = pc.one_off_turns(3)
        outputs = self._turns(f"{FILES_LINE}\n", [tombstone(died_at)])
        self.assertEqual(str(pc.one_off_retry_ceiling(died_at)),
                         outputs["max_turns"])

    def test_the_ceiling_command_never_writes_an_empty_number(self):
        """The workflow interpolates this into `claude_args`, and a bare
        `--max-turns` is a run that never starts."""
        for description in ("", "no files line", FILES_LINE):
            outputs = self._turns(description)
            self.assertTrue(outputs["max_turns"].isdigit(), description)


class TheRailCarriesTheCeiling(unittest.TestCase):
    """AC2's other half. A number that is right in the module and a literal on
    the step is the defect this card names."""

    def _steps(self) -> list:
        doc = yaml.safe_load(open(WF, encoding="utf-8").read())
        return [s for job in doc["jobs"].values() for s in job.get("steps") or []]

    def _step_named(self, fragment: str) -> dict:
        for step in self._steps():
            if fragment.lower() in str(step.get("name") or "").lower():
                return step
        raise AssertionError(f"no step named like {fragment!r}")

    def _index(self, fragment: str) -> int:
        for i, step in enumerate(self._steps()):
            if fragment.lower() in str(step.get("name") or "").lower():
                return i
        raise AssertionError(f"no step named like {fragment!r}")

    def test_no_literal_max_turns_20_remains_anywhere(self):
        source = open(WF, encoding="utf-8").read()
        self.assertFalse("--max-turns 20" in source,
                         "plan.yml still writes `--max-turns 20` as a literal")

    def test_the_one_off_critic_takes_the_sized_ceiling(self):
        args = str(self._step_named("Pre-approval critic — the one-off exit")
                   .get("with", {}).get("claude_args") or "")
        self.assertIn("steps.ooturns.outputs.max_turns", args)
        self.assertNotIn("--max-turns 20", args)

    def test_the_ceiling_step_runs_on_the_one_off_route_and_before_the_critic(self):
        step = self._step_named("One-off critic — turn ceiling")
        self.assertEqual("ooturns", step.get("id"))
        self.assertIn("one-off", str(step.get("if")))
        self.assertIn("plan_critic.py one-off-turns", str(step.get("run")))
        self.assertLess(self._index("One-off critic — turn ceiling"),
                        self._index("Pre-approval critic — the one-off exit"))

    def test_the_ceiling_step_reads_the_card_into_a_file_and_no_prompt(self):
        """The card body is untrusted text. It reaches this step as a FILE —
        `linear_ops.py description`, the way the visual-QA stage reads one —
        and never as the SANITIZED step output, which exists to be
        interpolated into a prompt and may appear only inside the sentinel
        fence (tests/test_untrusted_content_wiring.py)."""
        step = self._step_named("One-off critic — turn ceiling")
        run = str(step.get("run"))
        self.assertIn("linear_ops.py description", run)
        self.assertIn("--description-file", run)
        self.assertNotIn("steps.card.outputs.description",
                         run + json.dumps(step.get("env") or {}))

    def test_the_fallback_ceiling_is_the_modules_default(self):
        run = str(self._step_named("One-off critic — turn ceiling").get("run"))
        self.assertIn(f"max_turns={pc.ONE_OFF_TURNS_DEFAULT}", run)

    def test_the_decision_is_told_what_the_run_died_of(self):
        run = str(self._step_named("One-off critic — decision").get("run"))
        self.assertIn("--execution-file", run)
        self.assertIn("--ceiling", run)
        self.assertIn("steps.ooturns.outputs.max_turns", run)

    def test_the_death_is_recorded_where_the_next_run_reads_it(self):
        step = self._step_named("One-off critic — the read that ran out")
        gate = str(step.get("if"))
        self.assertIn("steps.oneoff.outputs.ran_out == 'true'", gate)
        run = str(step.get("run"))
        self.assertIn("plan_critic.py died", run)
        self.assertIn(f"--stage {pc.STAGE_ONE_OFF}", run)
        self.assertIn("linear_ops.py comment", run)

    def test_the_tombstone_is_posted_alone_in_its_own_comment(self):
        """A record is only a credential while nothing else shares its comment
        (`plan_critic._sole_record`), so the step posts the record file and
        nothing else with it."""
        run = str(self._step_named("One-off critic — the read that ran out")
                  .get("run"))
        self.assertEqual(1, run.count("linear_ops.py comment"))
        self.assertIn("--record-file", run)

    def test_the_death_record_is_posted_after_the_decision_reads_the_thread(self):
        self.assertLess(self._index("One-off critic — decision"),
                        self._index("One-off critic — the read that ran out"))


if __name__ == "__main__":
    unittest.main()
