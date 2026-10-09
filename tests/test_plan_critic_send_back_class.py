"""The one-off exit classifies the critic's finding before it routes it (DRE-6359).

DRE-3879 made the round trip through the CEO five times, and two of those
findings named something the card had to SAY — an unverifiable claim, two
branch names he had already given — rather than a choice only he could make.
Since DRE-5376 a `SEND_BACK` goes to the planner's rewrite, so the one result
that can still carry bookkeeping to his queue is a `QUESTION`.

So on a `QUESTION` the exit asks `send_back_class.route_class` of the critic's
FINDING (the `FINDING:` line, else the header reason), and:

  * `revision` → `revise`: the planner rewrites the card, nothing is written
    to Green Light, the round is recorded as `result=SEND_BACK` with the
    finding as its reason, and it spends the bound like any send-back;
  * `decision`, or a finding the classifier cannot place → `escalate`,
    exactly as before — under-asking a real decision is the worse failure;
  * a `SEND_BACK` is never re-read: it stays the planner's `revise`.

`send-back-classes` is the live re-run: every one-off round on a card's WHOLE
thread, classified.

Run: cd bureau-pipeline && python3 -m pytest tests/test_plan_critic_send_back_class.py -v
"""

from __future__ import annotations

import contextlib
import inspect
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
SCRIPTS = os.path.join(ROOT, "scripts")
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "send-back-findings.json")
STANDARD = os.path.join(ROOT, "standards", "plan-critic.md")
CONTRACT = os.path.join(ROOT, "config", "lane-contract.json")

sys.path.insert(0, SCRIPTS)

import linear_ops  # noqa: E402
import plan_critic as pc  # noqa: E402
import send_back_class as sbc  # noqa: E402


def _finding(card: str, round_: int) -> str:
    with open(FIXTURE, encoding="utf-8") as f:
        for r in json.load(f):
            if r["card"] == card and r["round"] == round_:
                return r["finding"]
    raise KeyError((card, round_))


#: DRE-3879's six send-back findings, verbatim — rounds 1, 3 and 5 decisions,
#: 2, 4 and 6 revisions.
R = {n: _finding("DRE-3879", n) for n in range(1, 7)}
#: ...and the round 7 PASS that ends its live thread.
R7_PASS = ("every choice this card used to leave open is now a written-down, "
           "verified decision from the CEO, and the instructions match how the "
           "automation actually works.")
D = {n: _finding("DRE-3880", n) for n in range(1, 4)}

#: A question as the critic asks it — matching neither vocabulary, so only
#: the finding beside it can move the classification.
QUESTION = ("Which two branch names may merge on their own — the ones you "
            "already gave, or new ones?")
#: A finding no phrase in either list places.
UNPLACEABLE = "the card mentions a rollout date in passing"
#: Two further findings, numbered under the header.
FURTHER = ["the second criterion names a test file that does not exist",
           "the files line omits the workflow the card edits"]


def question_file(header: str, finding: str = "", further=()) -> str:
    lines = [f"{pc.RESULT_PREFIX} {pc.QUESTION} — {header}"]
    if finding:
        lines.append(f"{pc.FINDING_LINE_PREFIX} {finding}")
    lines += [""] + [f"{i}. {item}" for i, item in enumerate(further, 1)]
    return "\n".join(lines) + "\n"


def _read_block(raw: str, name: str) -> str:
    """A step output out of a `$GITHUB_OUTPUT` file — `name=value`, or the
    heredoc form `name<<DELIM` … `DELIM` a multi-line value takes."""
    lines = raw.splitlines()
    for i, line in enumerate(lines):
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1]
        if line.startswith(f"{name}<<"):
            delim = line.split("<<", 1)[1]
            end = lines.index(delim, i + 1)
            return "\n".join(lines[i + 1:end])
    return ""


def _items(block: str) -> list[str]:
    return [re.sub(r"^\d+\.\s+", "", line) for line in block.splitlines()]


# --- The decision -------------------------------------------------------------

class AQuestionIsClassifiedByItsFinding(unittest.TestCase):

    def test_a_revision_finding_is_revised_and_says_why(self):
        action, note = pc.one_off_decide(pc.QUESTION, QUESTION, 0, finding=R[4])
        # The action word is what gates plan.yml's `escalate` step: anything
        # but `escalate` writes nothing to Green Light.
        self.assertEqual(pc.REVISE, action)
        self.assertNotEqual(pc.ESCALATE, action)
        self.assertIn(sbc.why(R[4]), note)

    def test_the_note_says_the_critic_wrote_question_and_quotes_it(self):
        _, note = pc.one_off_decide(pc.QUESTION, QUESTION, 0, finding=R[4])
        self.assertIn(pc.QUESTION, note)
        self.assertIn(f"the critic asked the CEO: {QUESTION}", note)

    def test_a_decision_finding_escalates_unchanged(self):
        self.assertEqual((pc.ESCALATE, QUESTION),
                         pc.one_off_decide(pc.QUESTION, QUESTION, 0,
                                           finding=R[1]))

    def test_with_no_finding_the_reason_is_classified(self):
        action, note = pc.one_off_decide(pc.QUESTION, R[4])
        self.assertEqual(pc.REVISE, action)
        self.assertIn(sbc.why(R[4]), note)

    def test_an_unplaceable_finding_goes_to_the_ceo(self):
        """Uncertain goes to the CEO: under-asking a genuine decision is the
        worse failure — a decision rewritten as card text ships somebody's
        guess, while a revision asked as a decision costs one round of his
        time (`send_back_class.UNCERTAIN_GOES_TO`)."""
        self.assertIsNone(sbc.classify(UNPLACEABLE))
        self.assertEqual((pc.ESCALATE, QUESTION),
                         pc.one_off_decide(pc.QUESTION, QUESTION, 0,
                                           finding=UNPLACEABLE))

    def test_a_send_back_is_never_re_read(self):
        for n in range(0, pc.MAX_ROUNDS + 1):
            for finding in (R[1], R[4], UNPLACEABLE):
                self.assertEqual(
                    pc.one_off_decide(pc.SEND_BACK, finding, n),
                    pc.one_off_decide(pc.SEND_BACK, finding, n, finding=R[1]))
                self.assertEqual(
                    pc.one_off_decide(pc.SEND_BACK, finding, n),
                    pc.one_off_decide(pc.SEND_BACK, finding, n, finding=R[4]))
        # A decision-class send-back is still the planner's.
        self.assertEqual(pc.REVISE, pc.one_off_decide(pc.SEND_BACK, R[1])[0])

    def test_a_reclassified_round_spends_the_bound(self):
        action, note = pc.one_off_decide(pc.QUESTION, QUESTION,
                                         pc.MAX_ROUNDS - 1, finding=R[4])
        self.assertEqual(pc.PARK, action)
        self.assertIn(pc.QUESTION, note)
        self.assertIn(pc.BOUND_PARK_LANE, note)

    def test_a_decision_question_at_the_bound_still_escalates(self):
        self.assertEqual(pc.ESCALATE, pc.one_off_decide(
            pc.QUESTION, QUESTION, pc.MAX_ROUNDS - 1, finding=R[1])[0])


# --- The list the planner is handed -------------------------------------------

class TheFindingsListIsBuiltFromTheDecidedResult(unittest.TestCase):

    def test_a_question_header_with_numbered_lines(self):
        text = question_file(R[4], further=FURTHER)
        self.assertEqual([R[4]] + FURTHER,
                         pc.all_findings(text, decided=pc.SEND_BACK))

    def test_the_finding_line_comes_first(self):
        text = question_file(QUESTION, R[4], FURTHER)
        self.assertEqual([R[4]] + FURTHER,
                         pc.all_findings(text, decided=pc.SEND_BACK))

    def test_a_pass_and_a_bare_file_have_none(self):
        passed = f"{pc.RESULT_PREFIX} {pc.PASS} — {R7_PASS}\n1. {FURTHER[0]}\n"
        self.assertEqual([], pc.all_findings(passed, decided=pc.SEND_BACK))
        self.assertEqual([], pc.all_findings(f"1. {FURTHER[0]}\n",
                                             decided=pc.SEND_BACK))
        self.assertEqual([], pc.all_findings("", decided=pc.SEND_BACK))

    def test_a_send_back_header_is_unchanged(self):
        text = (f"{pc.RESULT_PREFIX} {pc.SEND_BACK} — {R[1]}\n\n"
                + "\n".join(f"{i}. {f}" for i, f in enumerate(FURTHER, 1)))
        self.assertEqual(pc.all_findings(text),
                         pc.all_findings(text, decided=pc.SEND_BACK))

    def test_without_decided_a_question_still_has_none(self):
        for text in (question_file(R[4], further=FURTHER),
                     question_file(QUESTION, R[4], FURTHER)):
            self.assertEqual([], pc.all_findings(text))

    def test_no_empty_or_duplicate_item(self):
        text = question_file(R[4], further=[R[4], FURTHER[0]])
        self.assertEqual([R[4], FURTHER[0]],
                         pc.all_findings(text, decided=pc.SEND_BACK))


# --- The decision, end to end through the CLI ---------------------------------

class TheDecideCli(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _run(self, result_text, *, files=("note", "record", "out"),
             thread=None):
        paths = {name: os.path.join(self.tmp, f"{name}.txt")
                 for name in ("result",) + tuple(files)}
        for p in paths.values():
            if os.path.exists(p):
                os.unlink(p)
        with open(paths["result"], "w", encoding="utf-8") as f:
            f.write(result_text)
        argv = [sys.executable, os.path.join(SCRIPTS, "plan_critic.py"),
                "decide", "--stage", pc.STAGE_ONE_OFF, "--epic", "DRE-3879",
                "--result-file", paths["result"]]
        flags = {"note": "--note-file", "record": "--record-file",
                 "out": "--github-output"}
        for name in files:
            argv += [flags[name], paths[name]]
        out = subprocess.run(argv, input=json.dumps(thread or []),
                             capture_output=True, text=True)
        self.assertEqual(0, out.returncode, out.stderr)
        read = {name: (open(paths[name], encoding="utf-8").read()
                       if os.path.exists(paths[name]) else "")
                for name in files}
        return {"stdout": out.stdout, **read}

    def test_the_record_carries_the_finding_never_the_question(self):
        run = self._run(question_file(QUESTION, R[4]))
        lines = run["record"].splitlines()
        self.assertEqual(
            [f"plan-critic: stage=one-off round=1 result=SEND_BACK "
             f"collisions=0 — {R[4]}"], lines)
        self.assertNotIn(QUESTION, run["record"])
        self.assertEqual([R[4]], pc.send_back_findings(lines, pc.STAGE_ONE_OFF))
        self.assertEqual(1, pc.send_backs(lines, pc.STAGE_ONE_OFF))
        reason = [ln for ln in run["note"].splitlines()
                  if ln.startswith("Reason:")]
        self.assertEqual([f"Reason: {R[4]}"], reason)
        self.assertIn(f"the critic asked the CEO: {QUESTION}", run["note"])
        self.assertIn(pc.QUESTION, run["note"])

    def test_with_no_finding_line_the_reason_is_recorded(self):
        run = self._run(question_file(R[4]))
        self.assertEqual(
            f"plan-critic: stage=one-off round=1 result=SEND_BACK "
            f"collisions=0 — {R[4]}", run["record"].strip())

    def test_a_decision_question_records_question_as_today(self):
        run = self._run(question_file(QUESTION, R[1]))
        self.assertEqual(
            f"plan-critic: stage=one-off round=1 result=QUESTION "
            f"collisions=0 — {QUESTION}", run["record"].strip())

    def test_the_next_round_after_a_reclassified_one_parks(self):
        first = self._run(question_file(QUESTION, R[4]))
        thread = [first["note"].strip(), first["record"].strip()]
        run = self._run(question_file(QUESTION, R[6]), thread=thread)
        self.assertEqual(pc.PARK, _read_block(run["out"], "action"))
        self.assertIn("result=SEND_BACK", run["record"])
        for finding in (R[4], R[6]):
            self.assertIn(finding, run["note"])

    def test_the_outputs_hand_the_planner_the_finding_first(self):
        run = self._run(question_file(R[4], further=FURTHER))
        self.assertEqual(pc.REVISE, _read_block(run["out"], "action"))
        self.assertEqual("3", _read_block(run["out"], "findings_count"))
        block = _read_block(run["out"], "findings")
        self.assertEqual([R[4]] + FURTHER, _items(block))
        # `result` and `reason` stay the critic's own words.
        self.assertEqual(pc.QUESTION, _read_block(run["out"], "result"))

        # ...and an unfinished revision names it on the park note.
        findings = os.path.join(self.tmp, "findings.md")
        with open(findings, "w", encoding="utf-8") as f:
            f.write(block)
        park = os.path.join(self.tmp, "park.md")
        empty = os.path.join(self.tmp, "empty.md")
        open(empty, "w").close()
        out = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS, "plan_critic.py"),
             "revision-outcome", "--card", "DRE-3879",
             "--question-file", empty, "--description-file", empty,
             "--summary-file", empty, "--findings-file", findings,
             "--park-file", park],
            capture_output=True, text=True)
        self.assertEqual(0, out.returncode, out.stderr)
        self.assertIn(R[4], open(park, encoding="utf-8").read())

    def test_an_escalated_question_writes_no_findings_as_today(self):
        run = self._run(question_file(R[1], further=FURTHER))
        self.assertEqual(pc.ESCALATE, _read_block(run["out"], "action"))
        self.assertEqual("0", _read_block(run["out"], "findings_count"))

    def test_a_realistic_question_file(self):
        run = self._run(
            question_file(QUESTION, R[4])
            + "RECOMMENDATION: write the two names in — they were given\n"
              "OPTION 1 (Recommended): write them in — the card says them "
              "⟶ proceed\n",
            files=("out",))
        self.assertEqual(pc.REVISE, _read_block(run["out"], "action"))
        self.assertEqual(R[4], _items(_read_block(run["out"], "findings"))[0])

    def test_with_no_files_it_prints_the_note_and_the_record(self):
        run = self._run(question_file(R[4]), files=())
        self.assertIn(pc.QUESTION, run["stdout"])
        self.assertIn("revision", run["stdout"])
        self.assertIn("result=SEND_BACK", run["stdout"])
        self.assertNotIn("action=", run["stdout"])

        run = self._run(question_file(QUESTION, R[1]), files=())
        self.assertIn(QUESTION, run["stdout"])
        self.assertIn("result=QUESTION", run["stdout"])
        self.assertNotIn("action=", run["stdout"])


class TheSeamDre6454RewritesStaysOneLine(unittest.TestCase):

    def test_prior_is_one_line_directly_before_the_call(self):
        src = [ln.strip() for ln in
               inspect.getsource(pc._cmd_decide).splitlines()]
        calls = [i for i, ln in enumerate(src)
                 if "= one_off_decide(" in ln]
        self.assertEqual(1, len(calls), calls)
        i = calls[0]
        self.assertEqual("prior = send_backs(cycle, args.stage)", src[i - 1])
        self.assertRegex(src[i], r"one_off_decide\(result, reason, prior\b")


# --- The live re-run ----------------------------------------------------------

def _rec(round_: int, result: str, reason: str) -> str:
    return pc.marker(pc.STAGE_ONE_OFF, round_, result, reason)


DRE_3879 = ([_rec(n, pc.SEND_BACK, R[n]) for n in range(1, 7)]
            + [_rec(7, pc.PASS, R7_PASS)])
DRE_3880 = [_rec(n, pc.SEND_BACK, D[n]) for n in range(1, 4)]


def _classes(bodies, card="DRE-3879", whole_only=False):
    """Run `send-back-classes` over a fake thread; returns (rows, the fake)."""
    def fake(identifier, *, whole_thread=False):
        return list(bodies) if whole_thread or not whole_only else bodies[-50:]

    spy = mock.Mock(side_effect=fake)
    records = mock.Mock(side_effect=AssertionError("comment_records was read"))
    out = io.StringIO()
    with mock.patch.object(linear_ops, "comment_bodies", spy), \
            mock.patch.object(linear_ops, "comment_records", records), \
            contextlib.redirect_stdout(out):
        code = pc.main(["send-back-classes", card])
    assert code == 0
    records.assert_not_called()
    rows = [ln.split("\t") for ln in out.getvalue().splitlines()
            if ln.startswith("round ")]
    return rows, spy


class SendBackClasses(unittest.TestCase):

    def test_dre_3879_reads_as_the_epic_says(self):
        rows, _ = _classes(DRE_3879)
        self.assertEqual(
            [("round 1", sbc.DECISION), ("round 2", sbc.REVISION),
             ("round 3", sbc.DECISION), ("round 4", sbc.REVISION),
             ("round 5", sbc.DECISION), ("round 6", sbc.REVISION),
             ("round 7", "pass")],
            [(r[0], r[1]) for r in rows])
        self.assertEqual(["round 4", sbc.REVISION, sbc.why(R[4]), R[4]],
                         rows[3])
        self.assertEqual(["round 7", "pass", "—", R7_PASS], rows[6])

    def test_dre_3880_reads_as_the_epic_says(self):
        rows, _ = _classes(DRE_3880, "DRE-3880")
        self.assertEqual([sbc.REVISION, sbc.DECISION, sbc.REVISION],
                         [r[1] for r in rows])

    def test_it_reads_the_whole_thread_as_strings(self):
        _, spy = _classes(DRE_3879)
        spy.assert_called_once_with("DRE-3879", whole_thread=True)

    def test_the_oldest_rounds_of_a_long_thread_are_read(self):
        chatter = [f"comment {i}" for i in range(60 - len(DRE_3879))]
        rows, _ = _classes(DRE_3879 + chatter, whole_only=True)
        self.assertEqual([f"round {n}" for n in range(1, 8)],
                         [r[0] for r in rows])

    def test_a_question_is_classified_and_a_no_result_is_not(self):
        rows, _ = _classes([_rec(1, pc.QUESTION, R[4]),
                            _rec(2, pc.NO_RESULT, "the critic crashed")])
        self.assertEqual(["round 1", sbc.REVISION, sbc.why(R[4]), R[4]],
                         rows[0])
        self.assertEqual(["round 2", "no-result", "—", "the critic crashed"],
                         rows[1])

    def test_other_stages_are_not_read(self):
        rows, _ = _classes([pc.marker(pc.STAGE_PRE, 1, pc.SEND_BACK, R[4])]
                           + DRE_3880, "DRE-3880")
        self.assertEqual(3, len(rows))


# --- The words around it ------------------------------------------------------

class TheStandardSaysIt(unittest.TestCase):

    def setUp(self):
        with open(STANDARD, encoding="utf-8") as f:
            self.text = " ".join(f.read().split())

    def test_the_question_paragraph_states_both_rules(self):
        self.assertIn(
            "A `QUESTION` whose finding names something the card must say "
            "goes back to the planner's rewrite, like a `SEND_BACK`, and "
            "nothing is written to `Green Light` (DRE-6359).", self.text)
        self.assertIn(
            "A `QUESTION` whose finding is a decision, or that the classifier "
            "cannot place, goes to the CEO.", self.text)

    def test_the_bound_paragraph_no_longer_says_a_question_spends_nothing(self):
        self.assertNotIn("A `QUESTION` spends nothing", self.text)
        self.assertIn(
            "A `QUESTION` read as a revision spends a round like a "
            "`SEND_BACK`, and one left with the CEO spends nothing — it is a "
            "decision, not a failed revision.", self.text)


class TheImportRunsOneWay(unittest.TestCase):

    def test_plan_critic_imports_the_classifier(self):
        src = inspect.getsource(pc)
        self.assertRegex(src, r"(?m)^import send_back_class\b")
        self.assertIs(sbc, pc.send_back_class)

    def test_the_classifier_does_not_import_plan_critic(self):
        src = inspect.getsource(sbc)
        self.assertNotRegex(src, r"(?m)^\s*(import|from)\s+plan_critic\b")


CLAUSE = ("critic QUESTION whose finding names card text is read as a revision "
          "and answered by the planner's rewrite, and a QUESTION whose finding "
          "is a decision, or that the classifier cannot place, is the "
          "escalation (DRE-6359)")


class TheLaneContractSaysIt(unittest.TestCase):

    def _clause(self, lane: str, kind: str) -> str:
        with open(CONTRACT, encoding="utf-8") as f:
            contract = json.load(f)
        for entry in contract["lanes"]:
            if entry["name"] == lane:
                return entry["clauses"][kind]["text"]
        raise KeyError(lane)

    def test_green_light_entrance_b(self):
        self.assertIn(CLAUSE, self._clause("Green Light", "entrance"))

    def test_planning_exit(self):
        self.assertIn(CLAUSE, self._clause("Planning", "exit"))


if __name__ == "__main__":
    unittest.main()
