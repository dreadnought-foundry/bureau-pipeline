"""A one-off critic that decided nothing is read once more before anything goes
to the CEO (DRE-7491).

DRE-6673's proof record (bureau-pipeline #985) found two cards that reached
Green Light from the one-off plan critic, and on a re-read only one of them was
a false arrival:

  * DRE-2498, 2026-10-13 08:45:48 PT, round 2: the critic's result file was
    empty (`result=NO_RESULT`, run 38424453807). The CEO was asked "Re-run the
    review, or settle the card yourself?", and the next read (round 3, run
    38435049584) passed it. A re-run would have reached the same pass without
    him. That is the arrival this file replays, and it now re-runs.
  * DRE-7066, 2026-10-12 14:07:29 PT, round 1: a `QUESTION` about an exception
    to one of the CEO's own standing rules. It was his decision, and DRE-6359's
    rule sent it to him rightly. This file replays it as a regression guard:
    it still escalates, with the question, unchanged.

The rule: one automatic re-run per unbroken run of no-results, read off the
card's own round records. A second no-result in a row escalates, says the
review failed twice, and names both runs.

The replay text below was copied off the two cards' threads on 2026-10-18, so
nothing is fetched. Every row was written by the pipeline.

Run: cd bureau-pipeline && python3 -m pytest tests/test_plan_critic_one_off_no_result_rerun.py -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import yaml

ROOT = os.path.join(os.path.dirname(__file__), "..")
SCRIPTS = os.path.join(ROOT, "scripts")
WF = os.path.join(ROOT, ".github", "workflows", "plan.yml")
STANDARD = os.path.join(ROOT, "standards", "plan-critic.md")

sys.path.insert(0, SCRIPTS)

import console_escalation  # noqa: E402
import linear_ops  # noqa: E402
import plan_critic as pc  # noqa: E402
import plan_run  # noqa: E402
import planning_escalation  # noqa: E402
import review_rerun as rr  # noqa: E402

# --- DRE-2498 -----------------------------------------------------------------

DRE_2498 = "DRE-2498"
#: Round 1's record, verbatim, `created_at` 2026-10-13T15:39:09.601Z.
DRE_2498_ROUND_1 = (
    "plan-critic: stage=one-off round=1 result=SEND_BACK collisions=0 — The "
    "share panel does not know which documents are linked-out, so the count "
    "and list need a server change the card never mentions, and a build agent "
    "cannot tell where the work stops."
)
#: Round 2's run, whose result file was empty.
DRE_2498_RUN = "38424453807"
#: Round 2's record as it was written at 2026-10-13T15:45:44.680Z — no run id,
#: because the field did not exist yet.
DRE_2498_ROUND_2_AS_WRITTEN = (
    "plan-critic: stage=one-off round=2 result=NO_RESULT collisions=0")
#: Round 2's record as this card's code writes it.
DRE_2498_ROUND_2_AS_NOW_WRITTEN = (
    f"plan-critic: stage=one-off round=2 result=NO_RESULT collisions=0 "
    f"run={DRE_2498_RUN}")
#: A STAND-IN run id. DRE-2498 never had a second no-result in a row; the
#: second-no-result case is built from the first, and this id is invented.
STAND_IN_SECOND_RUN = "38424453999"

# --- DRE-7066 -----------------------------------------------------------------

DRE_7066 = "DRE-7066"
DRE_7066_QUESTION = (
    "The cheapest model that can review the test harness's practice pull "
    "requests is one you ruled out of the fleet on 2026-10-08 — may the test "
    "harness alone use it, or should we cut how often the practice reviews "
    "run instead?"
)
#: Its FINDING line, as the escalation showed it — cut off there with `…`,
#: and used as shown.
DRE_7066_FINDING = (
    "The only cheaper model that is allowed to review these practice pull "
    "requests is the one you excluded everywhere (Haiku 5.5). The other cheap "
    "model (Sonnet 5.5) is barred here, because the harness's practice pull "
    "requests have no card to say which model built them, so the reviewer is "
    "not allowed to…"
)


def row(body: str, created_at: str = "", trusted: bool = True) -> dict:
    """One comment as `dump-comments --with-authors` hands it over."""
    return {"body": body, "authored_by_pipeline": trusted,
            "created_at": created_at}


def dre_2498_thread() -> list[dict]:
    return [row(DRE_2498_ROUND_1, "2026-10-13T15:39:09.601Z")]


def ran_out_execution(turns: int = 37) -> dict:
    """The action's execution file for a read cut off at its ceiling."""
    return {"subtype": pc.TURN_CAP_SUBTYPE, "num_turns": turns,
            "is_error": True}


class _Cli:
    """`plan_critic.py decide --stage one-off`, run as plan.yml runs it."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def decide(self, thread, result_text: str, *, card: str = DRE_2498,
               run_id: str | None = DRE_2498_RUN,
               execution: dict | None = None, ceiling: str = "36") -> dict:
        paths = {name: os.path.join(self.tmp, f"{name}.txt")
                 for name in ("result", "note", "record", "escalation", "out",
                              "execution")}
        for p in paths.values():
            if os.path.exists(p):
                os.unlink(p)
        with open(paths["result"], "w", encoding="utf-8") as f:
            f.write(result_text)
        argv = [sys.executable, os.path.join(SCRIPTS, "plan_critic.py"),
                "decide", "--stage", pc.STAGE_ONE_OFF, "--epic", card,
                "--result-file", paths["result"],
                "--github-output", paths["out"],
                "--note-file", paths["note"],
                "--record-file", paths["record"],
                "--escalation-file", paths["escalation"],
                "--ceiling", ceiling]
        if run_id is not None:
            argv += ["--run-id", run_id]
        if execution is not None:
            with open(paths["execution"], "w", encoding="utf-8") as f:
                json.dump(execution, f)
            argv += ["--execution-file", paths["execution"]]
        out = subprocess.run(argv, input=json.dumps(list(thread)),
                             capture_output=True, text=True)
        self.assertEqual(0, out.returncode, out.stderr)
        outputs = {}
        for line in open(paths["out"], encoding="utf-8"):
            if "=" in line and not line.startswith(" "):
                key, value = line.rstrip("\n").split("=", 1)
                outputs.setdefault(key, value)
        read = {name: (open(path, encoding="utf-8").read()
                       if os.path.exists(path) else None)
                for name, path in paths.items()}
        return {"outputs": outputs, **read}


# --- The decision --------------------------------------------------------------

class TheFirstNoResultIsReadAgain(unittest.TestCase):

    def test_rerun_is_a_fifth_action(self):
        self.assertEqual("rerun", pc.RERUN)
        self.assertIn(pc.RERUN, pc.ONE_OFF_ACTIONS)
        self.assertEqual(5, len(set(pc.ONE_OFF_ACTIONS)))

    def test_a_first_no_result_reruns(self):
        for result in (pc.NO_RESULT, "", "MAYBE"):
            action, note = pc.one_off_decide(result, rerun_spent=False)
            self.assertEqual(pc.RERUN, action, result)
            self.assertNotIn("goes to a person", note)

    def test_a_no_result_with_the_rerun_spent_escalates(self):
        self.assertEqual((pc.ESCALATE, pc.NO_CRITIC_NOTE),
                         pc.one_off_decide(pc.NO_RESULT, rerun_spent=True))

    def test_the_default_answers_what_it_answered_before(self):
        self.assertEqual(pc.one_off_decide(pc.NO_RESULT, rerun_spent=True),
                         pc.one_off_decide(pc.NO_RESULT))

    def test_a_read_that_ran_out_reruns_and_keeps_its_words(self):
        row_ = {"stage": pc.STAGE_ONE_OFF, "run": "1", "attempt": 1,
                "step": "oocritic", "subtype": pc.TURN_CAP_SUBTYPE,
                "turns": 36, "ceiling": 36}
        action, note = pc.one_off_decide(pc.NO_RESULT, "", 0, row_,
                                         rerun_spent=False)
        self.assertEqual(pc.RERUN, action)
        self.assertEqual(pc.one_off_ran_out_note(row_), note)
        self.assertEqual(pc.ESCALATE, pc.one_off_decide(
            pc.NO_RESULT, "", 0, row_, rerun_spent=True)[0])

    def test_a_decided_round_does_not_change(self):
        for args, kwargs in (
                ((pc.PASS, "one pull request"), {}),
                ((pc.SEND_BACK, "a defect", 0), {}),
                ((pc.SEND_BACK, "a defect", pc.MAX_ROUNDS), {}),
                ((pc.QUESTION, DRE_7066_QUESTION), {"finding": DRE_7066_FINDING}),
                ((pc.NO_RESULT,), {"precheck": "the card states no criteria"})):
            self.assertEqual(
                pc.one_off_decide(*args, **kwargs),
                pc.one_off_decide(*args, rerun_spent=False, **kwargs), args)


class TheRoundRecordCarriesTheRun(unittest.TestCase):

    def test_the_marker_writes_the_run_after_open_and_before_the_reason(self):
        self.assertEqual(
            "plan-critic: stage=post round=2 result=SEND_BACK collisions=0 "
            "open=1 run=42 — a reason",
            pc.marker(pc.STAGE_POST, 2, pc.SEND_BACK, "a reason", 0,
                      open_count=1, run_id="42"))

    def test_dre_2498s_round_2_as_this_card_writes_it(self):
        self.assertEqual(DRE_2498_ROUND_2_AS_NOW_WRITTEN,
                         pc.marker(pc.STAGE_ONE_OFF, 2, pc.NO_RESULT,
                                   run_id=DRE_2498_RUN))

    def test_the_run_is_read_back(self):
        rows = pc.parse_markers([row(DRE_2498_ROUND_2_AS_NOW_WRITTEN)])
        self.assertEqual(DRE_2498_RUN, rows[0]["run"])
        self.assertEqual(pc.NO_RESULT, rows[0]["result"])
        both = pc.parse_markers([row(pc.marker(
            pc.STAGE_POST, 1, pc.SEND_BACK, "why", 0, open_count=3,
            run_id="7"))])[0]
        self.assertEqual((3, "7", "why"),
                         (both["open"], both["run"], both["reason"]))

    def test_a_marker_from_before_the_field_parses_as_it_did(self):
        rows = pc.parse_markers([row(DRE_2498_ROUND_1),
                                 row(DRE_2498_ROUND_2_AS_WRITTEN)])
        self.assertEqual([None, None], [r["run"] for r in rows])
        self.assertEqual([pc.SEND_BACK, pc.NO_RESULT],
                         [r["result"] for r in rows])
        self.assertTrue(rows[0]["reason"].startswith("The share panel"))
        self.assertEqual("", rows[1]["reason"])


class TheTwiceFailedEscalation(unittest.TestCase):

    def _fits(self, text: str) -> None:
        self.assertEqual([], console_escalation.problems(text), text)
        self.assertIsNone(
            planning_escalation.refusal(console_escalation.split(text)[0]), text)

    def test_it_says_twice_and_names_both_runs(self):
        text = pc.one_off_escalation(
            pc.NO_RESULT, runs=(DRE_2498_RUN, STAND_IN_SECOND_RUN))
        self.assertIn("twice", text)
        self.assertIn(DRE_2498_RUN, text)
        self.assertIn(STAND_IN_SECOND_RUN, text)
        self.assertIn("nothing has checked", text)
        self._fits(text)

    def test_it_keeps_the_three_lines_and_the_two_choices(self):
        text = pc.one_off_escalation(
            pc.NO_RESULT, runs=(DRE_2498_RUN, STAND_IN_SECOND_RUN))
        esc = console_escalation.parse(text)
        self.assertIsNotNone(esc, text)
        self.assertIn("twice", esc.finding)
        self.assertNotEqual(pc.NO_RESULT_FINDING, esc.finding)
        self.assertEqual(pc.NO_RESULT_QUESTION, esc.question)
        for line in (console_escalation.FINDING_PREFIX,
                     console_escalation.QUESTION_PREFIX,
                     console_escalation.RECOMMENDATION_PREFIX):
            self.assertIn(line, text)
        for choice in pc.NO_RESULT_CHOICES:
            self.assertIn(choice.id, text)

    def test_an_unrecorded_earlier_run_is_said_as_not_recorded(self):
        text = pc.one_off_escalation(pc.NO_RESULT,
                                     runs=(None, STAND_IN_SECOND_RUN))
        self.assertIn("not recorded", text)
        self.assertIn(STAND_IN_SECOND_RUN, text)
        self.assertNotIn("None", text)
        self._fits(text)

    def test_it_replaces_the_ran_out_text(self):
        row_ = {"stage": pc.STAGE_ONE_OFF, "run": "1", "attempt": 1,
                "step": "oocritic", "subtype": pc.TURN_CAP_SUBTYPE,
                "turns": 36, "ceiling": 36}
        text = pc.one_off_escalation(pc.NO_RESULT, "", 0, (), row_,
                                     runs=("1", "2"))
        self.assertIn("twice", text)
        self.assertNotEqual(pc.one_off_ran_out_request(row_), text)

    def test_without_runs_nothing_changes(self):
        self.assertEqual(pc.one_off_escalation(pc.NO_RESULT, runs=()),
                         pc.one_off_escalation(pc.NO_RESULT))
        self.assertNotIn("twice", pc.one_off_escalation(pc.NO_RESULT))

    def test_a_question_is_untouched_by_runs(self):
        self.assertEqual(
            pc.one_off_escalation(pc.QUESTION, DRE_7066_QUESTION,
                                  finding=DRE_7066_FINDING),
            pc.one_off_escalation(pc.QUESTION, DRE_7066_QUESTION,
                                  finding=DRE_7066_FINDING, runs=("1", "2")))


# --- DRE-2498, replayed through the CLI ----------------------------------------

class DRE2498IsReadAgainRatherThanAskedAbout(_Cli, unittest.TestCase):

    def test_round_2s_empty_result_reruns(self):
        run = self.decide(dre_2498_thread(), "")
        self.assertEqual(pc.RERUN, run["outputs"]["action"])
        self.assertIsNone(run["escalation"],
                          "a re-run wrote a reason for the CEO")
        self.assertEqual(DRE_2498_ROUND_2_AS_NOW_WRITTEN, run["record"].strip())
        self.assertIn(f"run={DRE_2498_RUN}", run["record"])

    def test_the_note_says_the_critic_reads_it_again_and_nothing_is_for_the_ceo(self):
        note = self.decide(dre_2498_thread(), "")["note"]
        self.assertRegex(note, r"\bthe critic reads (?:this|the) card again\b")
        self.assertIn("Planning", note)
        self.assertIn("nothing here is for the CEO", note)
        self.assertNotIn("decision queue", note)
        self.assertNotIn("goes to a person", note)

    def test_a_rerun_spends_no_send_back_round(self):
        run = self.decide(dre_2498_thread(), "")
        thread = dre_2498_thread() + [row(run["record"].strip())]
        self.assertEqual(1, pc.send_backs(thread, pc.STAGE_ONE_OFF))

    def test_the_second_no_result_in_a_row_escalates_naming_both_runs(self):
        thread = dre_2498_thread() + [row(DRE_2498_ROUND_2_AS_NOW_WRITTEN)]
        run = self.decide(thread, "", run_id=STAND_IN_SECOND_RUN)
        self.assertEqual(pc.ESCALATE, run["outputs"]["action"])
        text = run["escalation"]
        self.assertIsNotNone(text)
        self.assertIn("twice", text)
        self.assertIn(DRE_2498_RUN, text)
        self.assertIn(STAND_IN_SECOND_RUN, text)
        self.assertIsNone(planning_escalation.refusal(
            console_escalation.split(text)[0]), text)
        self.assertIn(f"round=3 result={pc.NO_RESULT}", run["record"])
        self.assertIn(f"run={STAND_IN_SECOND_RUN}", run["record"])

    def test_an_earlier_record_without_a_run_says_not_recorded(self):
        thread = dre_2498_thread() + [row(DRE_2498_ROUND_2_AS_WRITTEN)]
        run = self.decide(thread, "", run_id=STAND_IN_SECOND_RUN)
        self.assertEqual(pc.ESCALATE, run["outputs"]["action"])
        self.assertIn("not recorded", run["escalation"])
        self.assertIn(STAND_IN_SECOND_RUN, run["escalation"])

    def test_after_the_ceo_sends_it_back_the_newest_no_result_still_escalates(self):
        """The loop limit: the escalation lands in Green Light, which only a
        person leaves, and the newest round record is still the no-result."""
        thread = dre_2498_thread() + [
            row(DRE_2498_ROUND_2_AS_NOW_WRITTEN),
            row(pc.death_marker(pc.STAGE_ONE_OFF, STAND_IN_SECOND_RUN, 1,
                                "oocritic", pc.TURN_CAP_SUBTYPE, 36, 36)),
            row("Build it.", trusted=False),
        ]
        run = self.decide(thread, "", run_id="38435049584")
        self.assertEqual(pc.ESCALATE, run["outputs"]["action"])

    def test_a_no_result_after_any_decided_round_reruns(self):
        for between in (pc.marker(pc.STAGE_ONE_OFF, 3, pc.SEND_BACK, "a defect"),
                        pc.marker(pc.STAGE_ONE_OFF, 3, pc.PASS, "fine"),
                        pc.marker(pc.STAGE_ONE_OFF, 3, pc.QUESTION, "which?")):
            thread = dre_2498_thread() + [row(DRE_2498_ROUND_2_AS_NOW_WRITTEN),
                                          row(between)]
            run = self.decide(thread, "", run_id=STAND_IN_SECOND_RUN)
            self.assertEqual(pc.RERUN, run["outputs"]["action"], between)

    def test_a_first_round_no_result_reruns(self):
        self.assertEqual(pc.RERUN,
                         self.decide([], "")["outputs"]["action"])

    def test_only_a_trusted_one_off_record_spends_the_rerun(self):
        for spoof in (row(DRE_2498_ROUND_2_AS_NOW_WRITTEN, trusted=False),
                      row(pc.marker(pc.STAGE_POST, 2, pc.NO_RESULT))):
            run = self.decide(dre_2498_thread() + [spoof], "")
            self.assertEqual(pc.RERUN, run["outputs"]["action"], spoof)

    def test_a_round_with_no_run_id_records_none(self):
        run = self.decide(dre_2498_thread(), "", run_id=None)
        self.assertEqual(pc.RERUN, run["outputs"]["action"])
        self.assertEqual(DRE_2498_ROUND_2_AS_WRITTEN, run["record"].strip())


class AReadThatRanOutIsReadAgainOnce(_Cli, unittest.TestCase):

    def test_the_first_ran_out_read_reruns_and_says_it_ran_out(self):
        run = self.decide(dre_2498_thread(), "", execution=ran_out_execution())
        self.assertEqual(pc.RERUN, run["outputs"]["action"])
        self.assertEqual("true", run["outputs"]["ran_out"],
                         "the tombstone step is gated on this, and it sizes "
                         "the re-run's ceiling")
        self.assertIn("ran out of turns", run["note"].lower())
        self.assertIn("36", run["note"])
        self.assertIsNone(run["escalation"])

    def test_the_second_in_a_row_escalates_naming_both_runs(self):
        thread = dre_2498_thread() + [row(DRE_2498_ROUND_2_AS_NOW_WRITTEN)]
        run = self.decide(thread, "", run_id=STAND_IN_SECOND_RUN,
                          execution=ran_out_execution())
        self.assertEqual(pc.ESCALATE, run["outputs"]["action"])
        self.assertEqual("true", run["outputs"]["ran_out"])
        self.assertIn("ran out of turns", run["note"].lower())
        self.assertIn("twice", run["escalation"])
        self.assertIn(DRE_2498_RUN, run["escalation"])
        self.assertIn(STAND_IN_SECOND_RUN, run["escalation"])


# --- DRE-7066, the regression guard --------------------------------------------

class DRE7066StillReachesTheCEO(_Cli, unittest.TestCase):

    def _result(self) -> str:
        return (pc.result_line(pc.QUESTION, DRE_7066_QUESTION) + "\n"
                f"FINDING: {DRE_7066_FINDING}\n")

    def test_the_question_escalates_with_the_question(self):
        run = self.decide([], self._result(), card=DRE_7066)
        self.assertEqual(pc.ESCALATE, run["outputs"]["action"])
        self.assertIn(DRE_7066_QUESTION, run["escalation"])
        self.assertNotIn("twice", run["escalation"])
        self.assertIn(f"result={pc.QUESTION}", run["record"])

    def test_the_escalation_is_what_it_was_before_this_card(self):
        run = self.decide([], self._result(), card=DRE_7066)
        self.assertEqual(
            pc.one_off_escalation(pc.QUESTION, DRE_7066_QUESTION,
                                  finding=DRE_7066_FINDING) + "\n",
            run["escalation"])


# --- The rail ------------------------------------------------------------------

RERUN_STEP = "One-off critic — read it again after no result"
DECISION_STEP = "One-off critic — decision"
TOMBSTONE_STEP = "One-off critic — the read that ran out of turns"


def _steps() -> list[dict]:
    doc = yaml.safe_load(open(WF, encoding="utf-8").read())
    return doc["jobs"]["plan"]["steps"]


def _index(name: str) -> int:
    for i, step in enumerate(_steps()):
        if str(step.get("name") or "") == name:
            return i
    raise AssertionError(f"no step named {name!r}")


class TheRailCarriesTheRerun(unittest.TestCase):

    def test_the_step_is_gated_on_rerun_alone(self):
        step = _steps()[_index(RERUN_STEP)]
        self.assertEqual(f"steps.oneoff.outputs.action == '{pc.RERUN}'",
                         str(step.get("if")).strip())

    def test_it_dispatches_a_planning_run_and_writes_no_lane(self):
        run = str(_steps()[_index(RERUN_STEP)].get("run"))
        self.assertIn("python3 .bureau-pipeline/scripts/review_rerun.py dispatch", run)
        self.assertIn('--epic "$CARD"', run)
        self.assertIn('--repo "$GITHUB_REPOSITORY"', run)
        self.assertIn(f"--reason {rr.REASON_ONE_OFF_RERUN}", run)
        self.assertIn("--route plan", run)
        self.assertNotIn("linear_ops.py state", run)
        self.assertNotIn("planning_escalation.py", run)

    def test_a_failed_dispatch_is_loud_and_red(self):
        run = str(_steps()[_index(RERUN_STEP)].get("run"))
        self.assertIn("🚨", run)
        self.assertIn("exit 1", run)

    def test_the_token_is_re_minted_first(self):
        i = _index(RERUN_STEP)
        step = _steps()[i]
        token = str((step.get("env") or {}).get("GH_TOKEN"))
        mint_id = token.split("steps.", 1)[1].split(".", 1)[0]
        mint = [(n, s) for n, s in enumerate(_steps()) if s.get("id") == mint_id]
        self.assertEqual(1, len(mint), token)
        n, s = mint[0]
        self.assertLess(n, i)
        self.assertIn("create-github-app-token", str(s.get("uses")))
        self.assertEqual(f"steps.oneoff.outputs.action == '{pc.RERUN}'",
                         str(s.get("if")).strip())

    def test_the_tombstone_is_posted_before_the_rerun_is_asked_for(self):
        """The re-run's ceiling is sized from the tombstone (DRE-4381), so it
        must be on the card before the next run can read it."""
        self.assertLess(_index(DECISION_STEP), _index(TOMBSTONE_STEP))
        self.assertLess(_index(TOMBSTONE_STEP), _index(RERUN_STEP))

    def test_the_decision_records_this_runs_id(self):
        run = str(_steps()[_index(DECISION_STEP)].get("run"))
        self.assertIn('--run-id "${{ github.run_id }}"', run)


class TheRerunReason(unittest.TestCase):

    def test_it_is_its_own_word_and_not_a_review_ask(self):
        self.assertEqual("one-off-rerun", rr.REASON_ONE_OFF_RERUN)
        self.assertNotIn(rr.REASON_ONE_OFF_RERUN, rr.REVIEW_ASKS)
        self.assertFalse(rr.is_review_ask(rr.REASON_ONE_OFF_RERUN))

    def test_it_dispatches_a_planning_run(self):
        sent = {}

        def gh(argv, **kwargs):
            sent.update(json.load(open(argv[argv.index("--input") + 1])))
            return subprocess.CompletedProcess(argv, 0, "", "")

        card = {"identifier": DRE_2498, "state": {"name": "Planning"},
                "labels": {"nodes": [{"name": "repo:portico"}]}}
        with mock.patch.object(linear_ops, "gql", return_value={"issue": card}), \
                mock.patch.object(plan_run.subprocess, "run", gh), \
                mock.patch.dict(os.environ, {"GITHUB_RUN_ID": "42"}):
            rc = rr.main(["dispatch", "--epic", DRE_2498, "--repo", "o/n",
                          "--reason", rr.REASON_ONE_OFF_RERUN,
                          "--route", "plan"])
        self.assertEqual(0, rc)
        self.assertEqual("agent-plan", sent["event_type"])
        self.assertEqual(rr.REASON_ONE_OFF_RERUN,
                         sent["client_payload"]["reason"])


class TheStandardSaysSo(unittest.TestCase):

    def test_the_one_off_section_describes_the_rerun(self):
        text = open(STANDARD, encoding="utf-8").read()
        section = text.split("## The same first critic reads a one-off", 1)[1]
        section = section.split("\n## ", 1)[0]
        self.assertIn("DRE-7491", section)
        self.assertIn("`rerun`", section)
        self.assertIn("second in a row", section)


if __name__ == "__main__":
    unittest.main()
