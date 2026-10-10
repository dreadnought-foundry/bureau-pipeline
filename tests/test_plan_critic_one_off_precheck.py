"""A one-off card that states no acceptance criteria goes to the planner's
rewrite before the critic's word is read, never to Green Light (DRE-6380).

DRE-4109, 2026-09-17: the one-off critic passed round 2 at 02:54:25Z, and
three seconds later `planning_route.py exit` refused the card with "the card
states no acceptance criteria, so there is no exit condition to route on" and
parked it on the CEO, where no judgement was being asked. A card written
wrong is a revision, never a decision — so the one-off decision step reads
the card BEFORE it reads the critic's result, through the exit's own routing
read (`routing_verdict.route`), and a card that read sends back on its
criteria is handed to the planner's rewrite whatever the critic wrote.

  * the precheck is `route()` itself: a label or a title that routes the card
    decides first, exactly as the exit decides;
  * the record says `result=SEND_BACK` with the precheck's finding as its
    reason, so the bound counts the round and the next round's prior finding
    is the missing criteria, never the critic's pass sentence;
  * the planner is handed the precheck's finding first, so a rewrite under a
    critic PASS is never told "0 in total";
  * a plain `decide` reads nothing — no flag, no precheck, no Linear request;
  * a card that could not be read is skipped, said once, and the critic's
    word decides as before.

Run: cd bureau-pipeline && python3 -m pytest tests/test_plan_critic_one_off_precheck.py -v
"""

from __future__ import annotations

import contextlib
import inspect
import io
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
CONTRACT = os.path.join(ROOT, "config", "lane-contract.json")
STANDARD = os.path.join(ROOT, "standards", "plan-critic.md")
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "send-back-findings.json")

sys.path.insert(0, SCRIPTS)

import critic_score  # noqa: E402
import linear_ops  # noqa: E402
import plan_critic as pc  # noqa: E402
import routing_verdict  # noqa: E402
import send_back_class  # noqa: E402

CARD = "DRE-4109"
SKIP_LINE = "one-off precheck: the card was not read"

#: A "Pipeline failure: …" card as the pipeline files one — prose, and no
#: `- [ ]` item anywhere.
NO_CRITERIA = (
    "The plan run for DRE-4100 failed at the critic step.\n\n"
    "Run: https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/1\n"
)
WITH_CRITERIA = NO_CRITERIA + (
    "\n## Acceptance criteria\n\n- [ ] the plan run for DRE-4100 is green\n"
)
PLAIN_TITLE = "Pipeline failure: plan run for DRE-4100"
PASS_SENTENCE = ("one pull request of work an agent can build unattended, "
                 "with nothing in it that is a decision")
QUESTION_TEXT = ("should the failed run be retried, or the card closed as a "
                 "duplicate of the one already open")
SEND_BACK_FIRST = "the card names no file the fix lands in"
NUMBERED = ["the run link points at a run that was deleted",
            "the title names the wrong card"]


def card(description=NO_CRITERIA, title=PLAIN_TITLE, labels=(),
         has_children=False) -> dict:
    return {"title": title, "description": description,
            "labels": list(labels), "has_children": has_children}


def expected_finding(description=NO_CRITERIA) -> str:
    return pc.one_line(routing_verdict.criteria_verdict(description)[1])


def pass_text(sentence=PASS_SENTENCE) -> str:
    return pc.result_line(pc.PASS, sentence) + "\n"


def bare_pass_text() -> str:
    return pc.result_line(pc.PASS, "") + "\n"


def question_text(q=QUESTION_TEXT) -> str:
    return pc.result_line(pc.QUESTION, q) + "\n"


def send_back_text(first=SEND_BACK_FIRST, rest=NUMBERED) -> str:
    lines = [pc.result_line(pc.SEND_BACK, first)]
    lines += [f"{i}. {f}" for i, f in enumerate(rest, 1)]
    return "\n".join(lines) + "\n"


def pipeline(*bodies) -> list[dict]:
    return [{"body": b, "authored_by_pipeline": True} for b in bodies]


def read_outputs(path: str) -> dict:
    """`$GITHUB_OUTPUT`, one-line keys and heredoc blocks both."""
    out: dict = {}
    if not os.path.exists(path):
        return out
    lines = open(path, encoding="utf-8").read().splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if "<<" in line and "=" not in line.split("<<", 1)[0]:
            key, delim = line.split("<<", 1)
            body = []
            i += 1
            while i < len(lines) and lines[i] != delim:
                body.append(lines[i])
                i += 1
            out.setdefault(key, "\n".join(body))
        elif "=" in line:
            key, value = line.split("=", 1)
            out.setdefault(key, value)
        i += 1
    return out


def block_items(block: str) -> list[str]:
    items = []
    for line in (block or "").splitlines():
        head, _, rest = line.partition(". ")
        if head.isdigit():
            items.append(rest)
    return items


class _Run:
    """`plan_critic.py decide --stage one-off`, in process, so Linear's seam
    can be patched and counted."""

    def __init__(self, case):
        self.case = case
        self.tmp = tempfile.mkdtemp()
        self.n = 0

    def __call__(self, result_text, *, thread=(), card_file=None, title=None,
                 labels=None, read_card=False, epic=CARD, files=True) -> dict:
        self.n += 1
        p = lambda name: os.path.join(self.tmp, f"{self.n}-{name}")
        with open(p("result"), "w", encoding="utf-8") as f:
            f.write(result_text)
        argv = ["decide", "--stage", pc.STAGE_ONE_OFF,
                "--result-file", p("result"),
                "--github-output", p("out")]
        if files:
            argv += ["--note-file", p("note"), "--record-file", p("record")]
        if epic:
            argv += ["--epic", epic]
        if card_file is not None:
            with open(p("body"), "w", encoding="utf-8") as f:
                f.write(card_file)
            argv += ["--card-file", p("body")]
        if title is not None:
            argv += ["--card-title", title]
        if labels is not None:
            argv += ["--card-labels", labels]
        if read_card:
            argv += ["--read-card"]
        stdout = io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO(json.dumps(list(thread)))), \
                contextlib.redirect_stdout(stdout):
            rc = pc.main(argv)
        self.case.assertEqual(0, rc)
        read = lambda name: (open(p(name), encoding="utf-8").read()
                             if os.path.exists(p(name)) else "")
        outputs = read_outputs(p("out"))
        return {"outputs": outputs, "raw": read("out"), "note": read("note"),
                "record": read("record").strip(), "stdout": stdout.getvalue(),
                "items": block_items(outputs.get("findings", ""))}


def _gql_returning(description, title=PLAIN_TITLE, labels=()):
    return mock.patch.object(linear_ops, "gql", return_value={"issue": {
        "identifier": CARD, "title": title, "description": description,
        "labels": {"nodes": [{"name": n} for n in labels]},
        "children": {"nodes": []},
    }})


# --- The precheck is the exit's own routing read -------------------------------

class ThePrecheckIsRouteItself(unittest.TestCase):

    def test_a_card_with_no_criteria_gets_the_criteria_rules_reason(self):
        self.assertEqual(routing_verdict.criteria_verdict(NO_CRITERIA)[1],
                         pc.one_off_precheck(card()))

    def test_one_criterion_is_enough_to_pass_the_precheck(self):
        self.assertIsNone(pc.one_off_precheck(card(WITH_CRITERIA)))

    def test_a_role_label_decides_first(self):
        for label in ("agent:ops", "no-code"):
            self.assertIsNone(pc.one_off_precheck(card(labels=[label])), label)

    def test_a_title_convention_decides_first(self):
        self.assertIsNone(pc.one_off_precheck(card(
            title="SIGN-OFF (OPERATOR): rotate the CloudFront key group")))

    def test_a_labelled_card_proceeds_exactly_as_with_no_card(self):
        run = _Run(self)
        with_card = run(pass_text(), card_file=NO_CRITERIA, labels="agent:ops")
        without = run(pass_text())
        self.assertEqual(pc.PROCEED, with_card["outputs"]["action"])
        self.assertEqual(without["outputs"]["note"],
                         with_card["outputs"]["note"])
        self.assertEqual(without["note"], with_card["note"])

    def test_the_finding_agrees_with_the_send_back_classifier(self):
        reason = pc.one_off_precheck(card())
        self.assertEqual(send_back_class.REVISION,
                         send_back_class.classify(reason))
        rows = [r for r in json.load(open(FIXTURE, encoding="utf-8"))
                if r["card"] == CARD]
        self.assertEqual(1, len(rows))
        self.assertEqual("revision", rows[0]["expected"])
        self.assertTrue(reason.startswith(rows[0]["finding"]), reason)


# --- The decision: a revision, whatever the critic wrote -----------------------

class ACardWithNoCriteriaIsRevised(unittest.TestCase):

    def setUp(self):
        self.run = _Run(self)
        self.finding = expected_finding()

    def test_a_critic_pass_is_revised_and_recorded_as_a_send_back(self):
        run = self.run(pass_text(), card_file=NO_CRITERIA)
        self.assertEqual(pc.REVISE, run["outputs"]["action"])
        rec = pc.parse_markers([run["record"]])[0]
        self.assertEqual(pc.SEND_BACK, rec["result"])
        self.assertEqual(self.finding, rec["reason"])
        self.assertNotIn(PASS_SENTENCE, run["record"])
        self.assertIn(f"Reason: {self.finding}", run["note"])
        self.assertIn("no acceptance criteria", run["note"])
        self.assertIn("before the critic's word was read", run["note"])

    def test_the_step_outputs_keep_the_critics_own_word(self):
        run = self.run(pass_text(), card_file=NO_CRITERIA)
        self.assertEqual(pc.PASS, run["outputs"]["result"])
        self.assertEqual("false", run["outputs"]["ran_out"])

    def test_at_the_bound_it_parks_with_the_finding_in_the_merged_list(self):
        earlier = "the card names no file the fix lands in"
        thread = pipeline(*[pc.marker(pc.STAGE_ONE_OFF, n, pc.SEND_BACK,
                                      earlier if n == 1 else f"finding {n}")
                            for n in range(1, pc.MAX_ROUNDS)])
        run = self.run(pass_text(), card_file=NO_CRITERIA, thread=thread)
        self.assertEqual(pc.PARK, run["outputs"]["action"])
        self.assertEqual("true", run["outputs"]["bound"])
        self.assertIn(self.finding, run["items"])
        self.assertEqual(earlier, run["items"][0], "oldest first")
        self.assertIn(self.finding, run["note"])
        rec = pc.parse_markers([run["record"]])[0]
        self.assertEqual((pc.SEND_BACK, self.finding),
                         (rec["result"], rec["reason"]))

    def test_the_round_is_counted_on_the_next_read(self):
        run = self.run(pass_text(), card_file=NO_CRITERIA)
        thread = pipeline(run["note"].strip(), run["record"])
        self.assertEqual(1, pc.send_backs(pc.current_cycle(thread, CARD),
                                          pc.STAGE_ONE_OFF))

    def test_a_critic_question_is_still_revised_and_follows_the_finding(self):
        run = self.run(question_text(), card_file=NO_CRITERIA)
        self.assertEqual(pc.REVISE, run["outputs"]["action"])
        note = run["note"]
        self.assertIn(self.finding, note)
        self.assertIn(QUESTION_TEXT, note)
        self.assertLess(note.index(f"1. {self.finding}"),
                        note.index(f"2. {QUESTION_TEXT}"))


# --- What the planner is handed ----------------------------------------------

class ThePlannerIsHandedThePrecheckFirst(unittest.TestCase):

    def setUp(self):
        self.run = _Run(self)
        self.finding = expected_finding()

    def _items(self, result_text):
        run = self.run(result_text, card_file=NO_CRITERIA)
        self.assertEqual(str(len(run["items"])),
                         run["outputs"]["findings_count"])
        return run["items"]

    def test_under_a_pass_the_finding_alone(self):
        self.assertEqual([self.finding], self._items(pass_text()))

    def test_under_a_bare_pass_and_a_missing_header_no_empty_item(self):
        for text in (bare_pass_text(), "", "the critic wrote no header\n"):
            self.assertEqual([self.finding], self._items(text), repr(text))

    def test_under_a_question_the_finding_then_the_question(self):
        self.assertEqual([self.finding, QUESTION_TEXT],
                         self._items(question_text()))

    def test_under_a_send_back_the_finding_then_the_critics_in_order(self):
        self.assertEqual([self.finding, SEND_BACK_FIRST] + NUMBERED,
                         self._items(send_back_text()))

    def test_a_send_back_repeating_the_finding_lists_it_once(self):
        self.assertEqual([self.finding] + NUMBERED,
                         self._items(send_back_text(first=self.finding)))

    def test_an_unfinished_revision_names_the_finding_in_its_park_note(self):
        run = self.run(pass_text(), card_file=NO_CRITERIA)
        tmp = tempfile.mkdtemp()
        files = {}
        for name, text in (("question", ""), ("description", ""),
                           ("summary", ""),
                           ("findings", run["outputs"]["findings"])):
            files[name] = os.path.join(tmp, f"{name}.md")
            with open(files[name], "w", encoding="utf-8") as f:
                f.write(text)
        park = os.path.join(tmp, "park.md")
        out = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS, "plan_critic.py"),
             "revision-outcome", "--card", CARD,
             "--question-file", files["question"],
             "--description-file", files["description"],
             "--summary-file", files["summary"],
             "--findings-file", files["findings"],
             "--park-file", park,
             "--github-output", os.path.join(tmp, "out")],
            capture_output=True, text=True)
        self.assertEqual(0, out.returncode, out.stderr)
        self.assertIn(self.finding, open(park, encoding="utf-8").read())


# --- A card with criteria changes nothing --------------------------------------

class ACardWithCriteriaChangesNothing(unittest.TestCase):

    def test_every_result_kind_decides_as_with_no_card(self):
        run = _Run(self)
        for text in (pass_text(), question_text(), send_back_text()):
            with_card = run(text, card_file=WITH_CRITERIA)
            without = run(text)
            for key in ("action", "note", "findings_count", "findings"):
                self.assertEqual(without["outputs"].get(key),
                                 with_card["outputs"].get(key), (key, text))
            self.assertEqual(without["note"], with_card["note"], text)
            self.assertEqual(without["record"], with_card["record"], text)


# --- No flag, no precheck ------------------------------------------------------

class APlainDecideReadsNothing(unittest.TestCase):

    def test_no_read_and_no_precheck_line_for_any_result(self):
        run = _Run(self)
        for text in (pass_text(), bare_pass_text(), question_text(),
                     send_back_text(), ""):
            with mock.patch.object(critic_score, "read_card") as read, \
                    mock.patch.object(linear_ops, "gql") as gql:
                got = run(text, epic="DRE-3879", files=False)
            read.assert_not_called()
            gql.assert_not_called()
            self.assertNotIn("one-off precheck:", got["stdout"], text)

    def test_a_plain_pass_still_proceeds(self):
        self.assertEqual(pc.PROCEED, _Run(self)(pass_text())["outputs"]["action"])


# --- A card that could not be read --------------------------------------------

class AnUnreadCardIsSkipped(unittest.TestCase):

    def setUp(self):
        self.run = _Run(self)

    def _skipped(self, got):
        self.assertEqual(1, got["stdout"].count(SKIP_LINE), got["stdout"])
        self.assertIn("the critic's word stands", got["stdout"])
        self.assertEqual(pc.PROCEED, got["outputs"]["action"])

    def test_an_empty_and_a_whitespace_only_body(self):
        for body in ("", "  \n\t\n"):
            self._skipped(self.run(pass_text(), card_file=body))

    def test_the_skip_line_is_printed_once_and_only_when_asked(self):
        got = self.run(pass_text(), card_file=WITH_CRITERIA)
        self.assertNotIn("one-off precheck:", got["stdout"])


class TheLiveRead(unittest.TestCase):

    def setUp(self):
        self.run = _Run(self)

    def test_a_criteria_less_card_read_live_is_revised(self):
        with _gql_returning(NO_CRITERIA) as gql:
            got = self.run(pass_text(), read_card=True)
        self.assertEqual(pc.REVISE, got["outputs"]["action"])
        self.assertEqual(CARD, gql.call_args[0][1]["id"])
        self.assertEqual([expected_finding()], got["items"])

    def test_it_is_read_through_critic_scores_single_issue_read(self):
        with mock.patch.object(critic_score, "read_card",
                               return_value=card()) as read:
            got = self.run(pass_text(), read_card=True)
        self.assertIs(linear_ops, read.call_args[0][0])
        self.assertEqual(CARD, read.call_args[0][1])
        self.assertEqual(pc.REVISE, got["outputs"]["action"])

    def test_a_read_that_raised_is_skipped(self):
        with mock.patch.object(linear_ops, "gql",
                               side_effect=linear_ops.LinearError("401")):
            got = self.run(pass_text(), read_card=True)
        self.assertEqual(1, got["stdout"].count(SKIP_LINE), got["stdout"])
        self.assertEqual(pc.PROCEED, got["outputs"]["action"])

    def test_an_empty_description_read_live_is_skipped(self):
        with _gql_returning(""):
            got = self.run(pass_text(), read_card=True)
        self.assertEqual(1, got["stdout"].count(SKIP_LINE), got["stdout"])
        self.assertEqual(pc.PROCEED, got["outputs"]["action"])

    def test_read_card_with_no_epic_is_skipped(self):
        with mock.patch.object(linear_ops, "gql") as gql:
            got = self.run(pass_text(), read_card=True, epic=None)
        gql.assert_not_called()
        self.assertEqual(1, got["stdout"].count(SKIP_LINE), got["stdout"])
        self.assertEqual(pc.PROCEED, got["outputs"]["action"])

    def test_the_card_file_wins_over_the_live_read(self):
        with mock.patch.object(linear_ops, "gql") as gql:
            got = self.run(pass_text(), card_file=NO_CRITERIA, read_card=True)
        gql.assert_not_called()
        self.assertEqual(pc.REVISE, got["outputs"]["action"])


# --- The seams other cards rely on ---------------------------------------------

class TheSeams(unittest.TestCase):

    def test_prior_stays_one_line_directly_before_the_decision_call(self):
        src = [ln.strip() for ln in
               inspect.getsource(pc._cmd_decide).splitlines()]
        priors = [i for i, ln in enumerate(src)
                  if ln == "prior = send_backs(since_bound_rewrite(cycle), args.stage)"]
        calls = [i for i, ln in enumerate(src) if "= one_off_decide(" in ln]
        self.assertEqual(1, len(calls), calls)
        self.assertIn(calls[0] - 1, priors)
        call = " ".join(src[calls[0]:calls[0] + 3])
        self.assertRegex(call, r"one_off_decide\(result, reason, prior\b")
        self.assertIn("precheck=", call,
                      "the precheck path does not go through the same call")

    def test_the_precheck_path_passes_prior_through(self):
        for n in (0, pc.MAX_ROUNDS - 1):
            got = pc.one_off_decide(pc.PASS, PASS_SENTENCE, n, None,
                                    precheck=expected_finding())
            want = pc.one_off_decide(pc.SEND_BACK, expected_finding(), n, None)
            self.assertEqual(want[0], got[0], n)
            self.assertIn(want[1], got[1])

    def test_the_turn_ceiling_steps_body_file_is_never_read(self):
        src = open(os.path.join(SCRIPTS, "plan_critic.py"),
                   encoding="utf-8").read()
        self.assertNotIn("one-off-card-body.md", src)


class TheWorkflowTurnsTheReadOn(unittest.TestCase):

    def setUp(self):
        self.steps = yaml.safe_load(open(WF, encoding="utf-8"))["jobs"]["plan"]["steps"]

    def test_the_oneoff_step_passes_read_card_after_epic(self):
        step = next(s for s in self.steps if s.get("id") == "oneoff")
        lines = [ln.strip() for ln in step["run"].splitlines()]
        i = lines.index('--epic "$CARD" \\')
        self.assertEqual("--read-card \\", lines[i + 1])
        self.assertIn("plan_critic.py decide", " ".join(lines[:i]))

    def test_no_other_step_carries_the_flag(self):
        carrying = [s.get("id") or s.get("name") for s in self.steps
                    if "--read-card" in (s.get("run") or "")]
        self.assertEqual(["oneoff"], carrying)


class TheWordsSayTheSame(unittest.TestCase):

    def test_the_standard_says_it_in_its_one_off_section(self):
        text = open(STANDARD, encoding="utf-8").read()
        self.assertRegex(" ".join(text.split()),
                         r"no acceptance criteria[^.]*labels and title do not "
                         r"route[^.]*planner's rewrite before the critic's "
                         r"word is read")

    def test_the_lane_contract_planning_exit_says_it(self):
        doc = json.load(open(CONTRACT, encoding="utf-8"))
        lane = next(l for l in doc["lanes"] if l["name"] == "Planning")
        text = lane["clauses"]["exit"]["text"]
        self.assertIn("goes to the planner's rewrite before the critic's word "
                      "is read (DRE-6380)", text)
        self.assertIn("takes the escalation exit below only when the card "
                      "could not be read", text)
        self.assertNotIn("one whose card names no exit condition to route on "
                         "takes the escalation exit below instead (DRE-3038)",
                         text)


if __name__ == "__main__":
    unittest.main()
