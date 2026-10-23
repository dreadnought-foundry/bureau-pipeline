"""Every medic diagnosis class has one declared next step (DRE-7792).

The diagnosis agent in medic.yml writes one word, `resume` or `hold`
(DRE-7526), and its prompt names four categories nothing downstream reads.
The weekly stop report reads every diagnosis into five classes. This card
makes those five classes the medic's own vocabulary — `config/
medic-next-steps.json`, one record per class — and ships the one reader the
medic will call, `scripts/medic_next_step.py`. No workflow is wired here: that
is DRE-7793, DRE-7799, DRE-7794 and DRE-7805.

Each test names the behavior it pins; reverting the reader or the data turns
it red.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import console_escalation
import dead_run
import lane_contract
import medic_next_step as mns
import medic_retry
import pipeline_act
import planning_escalation

SCRIPT = os.path.join(ROOT, "scripts", "medic_next_step.py")
CONFIG = os.path.join(ROOT, "config", "medic-next-steps.json")
RUN_ID = "38412906115"
OTHER_RUN = "38412900000"
RUN_URL = f"https://github.com/dreadnought-foundry/atlas/actions/runs/{RUN_ID}"
CLEAN_REPORT = (
    "The build stopped because the card asked for a change the tests do not "
    "allow. A person should decide which of the two is right.\n\nDetail: x"
)


def _cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, SCRIPT, *args], check=False,
                          capture_output=True,
                          text=True, cwd=ROOT, timeout=60)


def _marker(run_id: str = RUN_ID) -> str:
    return dead_run.limit_marker("claude", "build",
                                 datetime(2026, 10, 23, 5, tzinfo=UTC), run_id)


class _TmpDir(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, name: str, text: str) -> str:
        path = self.tmp / name
        path.write_text(text, encoding="utf-8")
        return str(path)


# ── the contract ───────────────────────────────────────────────────────────


class TheContractTest(unittest.TestCase):
    def test_the_five_classes_in_order(self):
        self.assertEqual(mns.CLASSES, ("transient-infrastructure", "bad-card",
                                       "bad-code", "platform-fault",
                                       "unreadable"))

    def test_the_four_steps(self):
        self.assertEqual(mns.STEPS, ("requeue-once", "send-to-planning",
                                     "fix-path", "ask-person"))

    def test_the_shipped_table(self):
        table = mns.load()
        self.assertEqual(list(table), list(mns.CLASSES))
        expect = {
            "transient-infrastructure": ("requeue-once", "ask-person"),
            "bad-card": ("send-to-planning", "ask-person"),
            "bad-code": ("fix-path", "ask-person"),
            "platform-fault": ("ask-person", None),
            "unreadable": ("ask-person", None),
        }
        for cls, (step, fallback) in expect.items():
            record = table[cls]
            self.assertEqual(record["step"], step, cls)
            self.assertEqual(record["fallback"], fallback, cls)
            self.assertTrue(record["means"].strip(), cls)
            if fallback is None:
                self.assertIsNone(record["fallback_when"], cls)
            else:
                self.assertTrue(record["fallback_when"], cls)

    def test_the_receipt_tag_is_disjoint_from_the_other_medic_tags(self):
        for other in ("dead-run-requeue", "medic-retry-declined"):
            self.assertNotIn(other, mns.RECEIPT_TAG)
            self.assertNotIn(mns.RECEIPT_TAG, other)


# ── check ──────────────────────────────────────────────────────────────────


class CheckTest(_TmpDir):
    def _table(self) -> dict:
        with open(CONFIG, encoding="utf-8") as f:
            return json.load(f)

    def test_check_passes_on_the_shipped_file(self):
        self.assertEqual(mns.check(), [])
        result = _cli("check")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_check_fails_naming_a_removed_class(self):
        table = self._table()
        del table["bad-card"]
        path = self.write("t.json", json.dumps(table))
        problems = mns.check(path)
        self.assertTrue(any("bad-card" in p for p in problems), problems)
        result = _cli("check", "--file", path)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("bad-card", result.stdout + result.stderr)

    def test_check_fails_naming_a_class_added_to_CLASSES_with_no_record(self):
        with mock.patch.object(mns, "CLASSES", mns.CLASSES + ("brand-new",)):
            problems = mns.check()
        self.assertTrue(any("brand-new" in p for p in problems), problems)

    def test_check_fails_on_a_record_for_an_unknown_class(self):
        table = self._table()
        table["mystery"] = dict(table["unreadable"])
        problems = mns.check(self.write("t.json", json.dumps(table)))
        self.assertTrue(any("mystery" in p for p in problems), problems)

    def test_check_fails_on_a_step_outside_STEPS(self):
        table = self._table()
        table["bad-code"]["step"] = "rewrite-everything"
        problems = mns.check(self.write("t.json", json.dumps(table)))
        self.assertTrue(any("rewrite-everything" in p for p in problems),
                        problems)

    def test_check_fails_on_a_fallback_outside_STEPS(self):
        table = self._table()
        table["bad-code"]["fallback"] = "give-up"
        problems = mns.check(self.write("t.json", json.dumps(table)))
        self.assertTrue(any("give-up" in p for p in problems), problems)

    def test_check_fails_on_a_fallback_with_no_fallback_when(self):
        table = self._table()
        table["platform-fault"]["fallback"] = "requeue-once"
        problems = mns.check(self.write("t.json", json.dumps(table)))
        self.assertTrue(any("platform-fault" in p and "fallback_when" in p
                            for p in problems), problems)

    def test_check_fails_on_unreadable_json(self):
        result = _cli("check", "--file", self.write("t.json", "{nope"))
        self.assertNotEqual(result.returncode, 0)


# ── read_class ─────────────────────────────────────────────────────────────


class ReadClassTest(_TmpDir):
    def test_each_class_word_reads_trimmed_and_lowercased(self):
        for cls in mns.CLASSES:
            path = self.write("v.txt", f"  \n{cls.upper()}\n\n")
            self.assertEqual(mns.read_class(path), cls)

    def test_a_missing_file_is_unreadable(self):
        self.assertEqual(mns.read_class(str(self.tmp / "absent.txt")),
                         "unreadable")

    def test_an_empty_file_is_unreadable(self):
        self.assertEqual(mns.read_class(self.write("v.txt", "  \n")),
                         "unreadable")

    def test_the_old_words_are_unreadable(self):
        for word in ("resume", "hold"):
            self.assertEqual(mns.read_class(self.write("v.txt", word)),
                             "unreadable", word)

    def test_two_words_are_unreadable(self):
        self.assertEqual(mns.read_class(self.write("v.txt", "two words")),
                         "unreadable")
        self.assertEqual(
            mns.read_class(self.write("v.txt", "bad-code\nbad-card")),
            "unreadable")

    def test_an_unknown_word_is_unreadable(self):
        self.assertEqual(mns.read_class(self.write("v.txt", "cosmic-rays")),
                         "unreadable")

    def test_undecodable_bytes_are_unreadable(self):
        path = self.tmp / "v.txt"
        path.write_bytes(b"\xff\xfe\x00bad")
        self.assertEqual(mns.read_class(str(path)), "unreadable")

    def test_the_cli_prints_the_class(self):
        result = _cli("read", "--file", self.write("v.txt", "bad-code\n"))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "bad-code")


# ── decide ─────────────────────────────────────────────────────────────────


def _decide(cls, **kw):
    args = {"kind": "card", "resume_spent": False, "has_open_pr": True,
            "planning_spent": False}
    args.update(kw)
    return mns.decide(cls, **args)


class DecideTest(unittest.TestCase):
    def test_every_class_decides_to_its_declared_step(self):
        expect = {
            "transient-infrastructure": "requeue-once",
            "bad-card": "send-to-planning",
            "bad-code": "fix-path",
            "platform-fault": "ask-person",
            "unreadable": "ask-person",
        }
        for cls, step in expect.items():
            got = _decide(cls)
            self.assertEqual(got.step, step, cls)
            self.assertEqual(got.cls, cls)
            self.assertTrue(got.reason.strip())

    def test_transient_falls_back_only_when_the_resume_is_spent(self):
        self.assertEqual(_decide("transient-infrastructure",
                                 resume_spent=True).step, "ask-person")
        self.assertEqual(_decide("transient-infrastructure",
                                 has_open_pr=False,
                                 planning_spent=True).step, "requeue-once")

    def test_bad_code_falls_back_only_without_an_open_pr(self):
        self.assertEqual(_decide("bad-code", has_open_pr=False).step,
                         "ask-person")
        self.assertEqual(_decide("bad-code", resume_spent=True,
                                 planning_spent=True).step, "fix-path")

    def test_bad_card_falls_back_only_when_planning_is_spent(self):
        self.assertEqual(_decide("bad-card", planning_spent=True).step,
                         "ask-person")
        self.assertEqual(_decide("bad-card", resume_spent=True,
                                 has_open_pr=False).step, "send-to-planning")

    def test_the_reason_says_which(self):
        declared = _decide("bad-code").reason
        fell_back = _decide("bad-code", has_open_pr=False).reason
        self.assertNotEqual(declared, fell_back)
        self.assertIn("ask-person", fell_back)
        self.assertNotIn("\n", declared + fell_back)

    def test_a_non_card_kind_decides_nothing(self):
        for kind in ("repair-card", "notice", "unknown"):
            for cls in mns.CLASSES:
                self.assertIsNone(_decide(cls, kind=kind), (kind, cls))

    def test_an_unknown_class_decides_as_unreadable(self):
        self.assertEqual(_decide("cosmic-rays").step, "ask-person")

    def test_next_step_is_frozen(self):
        step = _decide("bad-code")
        with self.assertRaises(AttributeError):
            step.step = "ask-person"

    def test_the_cli_prints_the_step_and_the_reason(self):
        result = _cli("decide", "--class", "bad-code", "--kind", "card")
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.strip().splitlines()
        self.assertEqual(lines[0], "step=ask-person")  # no --has-open-pr
        result = _cli("decide", "--class", "bad-code", "--kind", "card",
                       "--has-open-pr")
        self.assertEqual(result.stdout.splitlines()[0], "step=fix-path")
        result = _cli("decide", "--class", "bad-card", "--kind", "card",
                       "--planning-spent")
        self.assertEqual(result.stdout.splitlines()[0], "step=ask-person")
        result = _cli("decide", "--class", "transient-infrastructure",
                       "--kind", "card", "--resume-spent")
        self.assertEqual(result.stdout.splitlines()[0], "step=ask-person")

    def test_the_cli_prints_step_none_for_a_non_card_kind(self):
        result = _cli("decide", "--class", "bad-code", "--kind", "notice")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.splitlines()[0], "step=none")


# ── receipt ────────────────────────────────────────────────────────────────


class ReceiptTest(_TmpDir):
    FINDING = "The build stopped because a test failed."

    def test_the_first_line_is_the_contract_line(self):
        step = _decide("bad-card")
        body = mns.receipt(step, run_url=RUN_URL, finding=self.FINDING)
        first = body.split("\n", 1)[0]
        self.assertEqual(first, f"🩺 medic-next-step: class=bad-card "
                                f"step=send-to-planning run={RUN_ID}")
        self.assertEqual(mns.parse_receipt(body),
                         {"class": "bad-card", "step": "send-to-planning",
                          "run": RUN_ID, "moved": True})

    def test_the_paragraph_names_the_class_what_was_done_and_the_finding(self):
        body = mns.receipt(_decide("transient-infrastructure"),
                           run_url=RUN_URL, finding=self.FINDING)
        paragraph = body.split("\n", 1)[1]
        self.assertIn("transient-infrastructure", paragraph)
        self.assertIn(self.FINDING, paragraph)
        self.assertIn(RUN_URL, paragraph)
        self.assertIn("re-ran", paragraph)

    def test_moved_no_ends_the_first_line_and_names_the_lane(self):
        body = mns.receipt(_decide("bad-card"), run_url=RUN_URL,
                           finding=self.FINDING, moved=False,
                           lane="In Progress")
        first, paragraph = body.split("\n", 1)
        self.assertTrue(first.endswith(" moved=no"), first)
        self.assertIn("In Progress", paragraph)
        self.assertEqual(mns.parse_receipt(body)["moved"], False)
        self.assertEqual(mns.parse_receipt(body)["step"], "send-to-planning")

    def test_fix_path_never_carries_moved_no(self):
        body = mns.receipt(_decide("bad-code"), run_url=RUN_URL,
                           finding=self.FINDING, moved=False, lane="In Review")
        self.assertFalse(body.split("\n", 1)[0].endswith("moved=no"))

    def test_parse_receipt_refuses_other_bodies(self):
        self.assertIsNone(mns.parse_receipt("🩺 diagnosis\n\nwords"))
        self.assertIsNone(mns.parse_receipt(""))
        self.assertIsNone(mns.parse_receipt(None))

    def test_the_cli_writes_the_receipt_from_the_report(self):
        report = self.write("r.md", CLEAN_REPORT)
        result = _cli("receipt", "--class", "bad-card", "--step",
                      "send-to-planning", "--run-url", RUN_URL,
                      "--report", report, "--moved-no", "--lane", "Queued")
        self.assertEqual(result.returncode, 0, result.stderr)
        parsed = mns.parse_receipt(result.stdout)
        self.assertEqual(parsed, {"class": "bad-card",
                                  "step": "send-to-planning", "run": RUN_ID,
                                  "moved": False})
        self.assertIn("Queued", result.stdout)
        self.assertIn(mns.finding(report), result.stdout)


# ── comment_bodies and waiting ─────────────────────────────────────────────


class CommentBodiesTest(_TmpDir):
    def _snapshot(self, bodies: list[str]) -> str:
        facts = {
            "state": "In Progress",
            "labels": ["repo:atlas"],
            "comments": [{"body": b, "created_at": "2026-10-23T05:00:00.000Z"}
                         for b in bodies],
        }
        path = str(self.tmp / "medic-card.json")
        medic_retry.write_snapshot(path, "DRE-1", facts)
        return path

    def test_the_dump_comments_array(self):
        path = self.write("c.json", json.dumps(["one", "two"]))
        self.assertEqual(mns.comment_bodies(path), ["one", "two"])

    def test_the_retry_gate_snapshot(self):
        path = self._snapshot(["one", "two"])
        self.assertEqual(mns.comment_bodies(path), ["one", "two"])

    def test_a_snapshot_entry_that_is_not_a_body_reads_empty(self):
        path = self.write("c.json", json.dumps(
            {"comments": [{"body": "one"}, "loose", {"body": 3}, {}]}))
        self.assertEqual(mns.comment_bodies(path), ["one", "", "", ""])

    def test_unreadable_shapes_answer_none(self):
        for text in ("{nope", json.dumps({"state": "x"}),
                     json.dumps({"comments": "x"}),
                     json.dumps([{"body": "one"}]), json.dumps("one")):
            self.assertIsNone(mns.comment_bodies(self.write("c.json", text)),
                              text)
        self.assertIsNone(mns.comment_bodies(str(self.tmp / "absent.json")))


class WaitingTest(CommentBodiesTest):
    def test_a_standing_marker_is_waiting(self):
        self.assertTrue(mns.waiting(["hello", _marker(), "a person speaks"]))

    def test_a_later_medic_receipt_supersedes_it(self):
        self.assertFalse(mns.waiting([_marker(), "🩺 diagnosis\n\nwords"]))

    def test_a_later_act_trailer_supersedes_it(self):
        later = "words\n\n" + pipeline_act.trailer("card-stranded")
        self.assertFalse(mns.waiting([_marker(), later]))

    def test_the_newest_marker_counts(self):
        self.assertTrue(mns.waiting([_marker(OTHER_RUN), "🩺 x", _marker()],
                                    run_id=RUN_ID))

    def test_run_id_must_name_the_markers_run(self):
        self.assertTrue(mns.waiting([_marker()], run_id=RUN_ID))
        self.assertFalse(mns.waiting([_marker()], run_id=OTHER_RUN))

    def test_no_marker_is_not_waiting(self):
        self.assertFalse(mns.waiting([]))
        self.assertFalse(mns.waiting(["hello"]))

    def _cli_waiting(self, path: str, *extra: str) -> str:
        result = _cli("waiting", "--comments-file", path, *extra)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def test_the_cli_reads_the_array(self):
        path = self.write("c.json", json.dumps([_marker()]))
        self.assertEqual(self._cli_waiting(path), "waiting=true")
        self.assertEqual(self._cli_waiting(path, "--run-id", RUN_ID),
                         "waiting=true")
        self.assertEqual(self._cli_waiting(path, "--run-id", OTHER_RUN),
                         "waiting=false")
        path = self.write("c.json", json.dumps([_marker(), "🩺 later"]))
        self.assertEqual(self._cli_waiting(path), "waiting=false")

    def test_the_cli_reads_the_snapshot_written_by_the_retry_gate(self):
        path = self._snapshot(["hello", _marker()])
        self.assertEqual(self._cli_waiting(path), "waiting=true")
        self.assertEqual(self._cli_waiting(path, "--run-id", RUN_ID),
                         "waiting=true")
        self.assertEqual(self._cli_waiting(path, "--run-id", OTHER_RUN),
                         "waiting=false")
        path = self._snapshot([_marker(), "🩺 later"])
        self.assertEqual(self._cli_waiting(path), "waiting=false")

    def test_the_cli_says_false_for_a_missing_or_bad_file(self):
        self.assertEqual(self._cli_waiting(str(self.tmp / "absent.json")),
                         "waiting=false")
        self.assertEqual(self._cli_waiting(self.write("c.json", "{nope")),
                         "waiting=false")


# ── planning_spent ─────────────────────────────────────────────────────────


class PlanningSpentTest(CommentBodiesTest):
    def _receipt(self, cls, moved=True, **kw) -> str:
        return mns.receipt(_decide(cls, **kw), run_url=RUN_URL,
                           finding="A finding.", moved=moved, lane="Queued")

    def test_true_after_a_send_to_planning_receipt(self):
        self.assertTrue(mns.planning_spent(["hi", self._receipt("bad-card"),
                                            "later"]))

    def test_false_after_one_that_did_not_move(self):
        self.assertFalse(mns.planning_spent(
            [self._receipt("bad-card", moved=False)]))

    def test_false_after_a_receipt_for_another_step(self):
        self.assertFalse(mns.planning_spent([
            self._receipt("transient-infrastructure"),
            self._receipt("bad-code"),
            self._receipt("bad-card", planning_spent=True),
        ]))

    def test_false_on_an_empty_thread(self):
        self.assertFalse(mns.planning_spent([]))

    def test_a_receipt_quoted_below_the_first_line_does_not_count(self):
        body = "a person quoting\n" + self._receipt("bad-card")
        self.assertFalse(mns.planning_spent([body]))

    def _cli_spent(self, path: str) -> str:
        result = _cli("planning-spent", "--comments-file", path)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def test_the_cli(self):
        path = self.write("c.json", json.dumps([self._receipt("bad-card")]))
        self.assertEqual(self._cli_spent(path), "planning_spent=true")
        path = self._snapshot([self._receipt("bad-card", moved=False)])
        self.assertEqual(self._cli_spent(path), "planning_spent=false")
        self.assertEqual(self._cli_spent(str(self.tmp / "absent.json")),
                         "planning_spent=unknown")
        self.assertEqual(self._cli_spent(self.write("c.json", "{nope")),
                         "planning_spent=unknown")


# ── finding ────────────────────────────────────────────────────────────────


class FindingTest(_TmpDir):
    def test_the_reports_first_sentence_on_one_line(self):
        path = self.write("r.md", "The build stopped\nbecause a test failed. "
                                  "Then more.\n\nDetail.")
        self.assertEqual(mns.finding(path),
                         "The build stopped because a test failed.")

    def test_a_leading_heading_is_not_the_finding(self):
        path = self.write("r.md", "## Diagnosis\n\nThe build stopped. More.")
        self.assertEqual(mns.finding(path), "The build stopped.")

    def test_a_missing_report(self):
        self.assertEqual(mns.finding(str(self.tmp / "absent.md")),
                         mns.NO_REPORT_FINDING)

    def test_an_empty_report(self):
        self.assertEqual(mns.finding(self.write("r.md", " \n\n")),
                         mns.NO_REPORT_FINDING)

    def test_the_exact_no_report_words(self):
        self.assertEqual(
            mns.NO_REPORT_FINDING,
            "The medic could not finish its diagnosis of this failure, so it "
            "wrote no report — the failed run is linked below.")

    def test_the_cli(self):
        result = _cli("finding", "--report", self.write("r.md", CLEAN_REPORT))
        self.assertEqual(result.stdout.strip(), CLEAN_REPORT.split(". ")[0]
                         + ".")
        result = _cli("finding", "--report", str(self.tmp / "absent.md"))
        self.assertEqual(result.stdout.strip(), mns.NO_REPORT_FINDING)


# ── question ───────────────────────────────────────────────────────────────


CLEAN_QUESTION = {
    "finding": "The card asks for two things the tests say cannot both hold.",
    "question": "Which of the two should the card keep?",
    "recommendation": "Keep the first one",
    "why": "it is the one the customer asked for",
}


class QuestionTest(_TmpDir):
    def _question(self, data, report: str | None = CLEAN_REPORT) -> str:
        qpath = (self.write("q.json", data if isinstance(data, str)
                            else json.dumps(data))
                 if data is not None else str(self.tmp / "absent.json"))
        rpath = (self.write("r.md", report) if report is not None
                 else str(self.tmp / "absent.md"))
        out = mns.question(qpath, report_path=rpath)
        self.assertEqual(console_escalation.problems(out), [], out)
        return out

    def _lines(self, out: str) -> tuple:
        esc = console_escalation.parse(out)
        self.assertIsNotNone(esc, out)
        return esc.finding, esc.question, esc.recommendation, esc.why

    def test_the_fallback_words_are_exact(self):
        self.assertEqual(
            mns.TECHNICAL_FINDING,
            "The medic's diagnosis is written in technical terms, so it is "
            "not repeated here — the full diagnosis is posted on this card "
            "and the failed run is linked below.")
        self.assertEqual(
            mns.FALLBACK_QUESTION,
            "What should happen to this card next: run it again, send it "
            "back to planning, or have the operator look at the pipeline?")
        self.assertEqual(mns.WHY_NOT_FINISHED, "the diagnosis did not finish")
        self.assertEqual(mns.WHY_NO_ANSWER, "the diagnosis named no answer")

    def test_every_fallback_passes_refusal(self):
        for text in (mns.NO_REPORT_FINDING, mns.TECHNICAL_FINDING,
                     mns.FALLBACK_QUESTION, mns.WHY_NOT_FINISHED,
                     mns.WHY_NO_ANSWER):
            self.assertIsNone(planning_escalation.refusal(text), text)

    def test_four_clean_lines_are_kept_as_written(self):
        out = self._question(CLEAN_QUESTION)
        self.assertEqual(self._lines(out), (
            CLEAN_QUESTION["finding"], CLEAN_QUESTION["question"],
            CLEAN_QUESTION["recommendation"], CLEAN_QUESTION["why"]))
        self.assertEqual(out, console_escalation.render(
            console_escalation.Escalation(*self._lines(out))))

    def test_each_tainted_line_is_replaced(self):
        data = {
            "finding": "The fault is in scripts/medic_retry.py on line 9.",
            "question": "Should we run git push --force on the branch?",
            "recommendation": "```rerun```",
            "why": "VERDICT: APPROVE",
        }
        out = self._question(data)
        finding, question, rec, why = self._lines(out)
        self.assertEqual(finding, CLEAN_REPORT.split(". ")[0] + ".")
        self.assertEqual(question, mns.FALLBACK_QUESTION)
        self.assertIsNone(rec)
        self.assertEqual(why, mns.WHY_NO_ANSWER)
        self.assertIn("none given — the diagnosis named no answer", out)
        for leak in ("scripts/", "git push", "```", "VERDICT:"):
            self.assertNotIn(leak, out)

    def test_a_tainted_finding_with_a_technical_report_is_TECHNICAL_FINDING(self):
        out = self._question(dict(CLEAN_QUESTION, finding="see foo.py"),
                             report="It broke in scripts/foo.py today. More.")
        self.assertEqual(self._lines(out)[0], mns.TECHNICAL_FINDING)
        self.assertEqual(self._lines(out)[1], CLEAN_QUESTION["question"])

    def test_the_pair_is_kept_only_together(self):
        out = self._question(dict(CLEAN_QUESTION, why="run python3 x"))
        self.assertIsNone(self._lines(out)[2])
        self.assertEqual(self._lines(out)[3], mns.WHY_NO_ANSWER)
        out = self._question(dict(CLEAN_QUESTION, recommendation="  "))
        self.assertIsNone(self._lines(out)[2])

    def test_a_blank_or_non_string_line_is_replaced(self):
        out = self._question(dict(CLEAN_QUESTION, question=" ", finding=7))
        finding, question, _, _ = self._lines(out)
        self.assertEqual(question, mns.FALLBACK_QUESTION)
        self.assertEqual(finding, CLEAN_REPORT.split(". ")[0] + ".")

    def test_every_line_is_replaced_for_an_unreadable_file(self):
        for data in (None, "{nope", json.dumps([CLEAN_QUESTION])):
            out = self._question(data)
            self.assertEqual(self._lines(out), (
                CLEAN_REPORT.split(". ")[0] + ".", mns.FALLBACK_QUESTION,
                None, mns.WHY_NO_ANSWER), data)

    def test_no_report_and_no_question(self):
        out = self._question(None, report=None)
        self.assertEqual(self._lines(out), (
            mns.NO_REPORT_FINDING, mns.FALLBACK_QUESTION, None,
            mns.WHY_NOT_FINISHED))
        self.assertIn("none given — the diagnosis did not finish", out)

    def test_each_replaced_field_is_named_on_stderr(self):
        data = dict(CLEAN_QUESTION, question="run git status now")
        qpath = self.write("q.json", json.dumps(data))
        result = _cli("question", "--file", qpath, "--report",
                      self.write("r.md", CLEAN_REPORT))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("question", result.stderr)
        self.assertIn("a command", result.stderr)
        self.assertNotIn("finding", result.stderr)
        self.assertNotIn("git status", result.stdout)
        check = self.write("out.txt", result.stdout)
        self.assertEqual(subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts",
                                          "console_escalation.py"),
             "check", check], check=False, capture_output=True,
             timeout=60).returncode, 0)

    def test_the_cli_with_a_missing_file_names_every_field(self):
        result = _cli("question", "--file", str(self.tmp / "absent.json"),
                      "--report", str(self.tmp / "absent.md"))
        self.assertEqual(result.returncode, 0)
        for field in ("finding", "question", "recommendation", "why"):
            self.assertIn(field, result.stderr)
        self.assertIn(mns.NO_REPORT_FINDING, result.stdout)


# ── from_lanes ─────────────────────────────────────────────────────────────


class FromLanesTest(unittest.TestCase):
    def _expected(self) -> list:
        lanes = [*lane_contract.START_LANES, lane_contract.QUEUE_LANE,
                 "In Review"]
        return list(dict.fromkeys(lanes))

    def test_the_lanes_in_order(self):
        self.assertEqual(mns.from_lanes(), self._expected())

    def test_they_are_read_off_the_contract(self):
        with mock.patch.object(lane_contract, "START_LANES",
                               ("Started", "Queued")), \
             mock.patch.object(lane_contract, "QUEUE_LANE", "Queued"):
            self.assertEqual(mns.from_lanes(),
                             ["Started", "Queued", "In Review"])

    def test_the_cli_joins_them_with_commas(self):
        result = _cli("from-lanes")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), ",".join(self._expected()))


if __name__ == "__main__":
    unittest.main()
