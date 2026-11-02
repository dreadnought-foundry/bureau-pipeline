"""The fix loop's half of the critic's design question (DRE-8735, rule 4c of DRE-8708).

When the critic's REQUEST_CHANGES verdict carries the `🎨 design-question:`
block, the finding is the CEO's to answer. The fix agent pushes nothing and
writes the block into its blocker file, and `report_fix_result.sh` used to park
that like any other blocker: the `fix-dispute` hold and a move to Triage, the
broken-card lane, where a real decision sat in the failure DRE-2776 removed.

Now `park_for_question` takes it, keyed on the one word
`design_answer.py state` prints for the part and path the block names:

  * `none` / `unknown` — the question goes on the card (the block's first line,
    then the three lines `console_escalation.render` writes), then the card
    moves to Green Light. No hold, no `needs-human`.
  * `asked` — the card moves to Green Light, and nothing is asked twice.
  * `answered` — nothing parks; `design_answer.py handback` hands his answer
    back to the critic.
  * `handed-back` — a broken loop, so Triage under `fix-dispute`, as before.

A verdict without the block, or with its lines out of order, parks exactly as
before. Every case runs the real script with `gh`, `linear_ops.py`, `hold.py`
and `design_answer.py` stubbed, logging in one file so the order of the writes
is read, not assumed.

Run: python3 -m pytest tests/test_design_question_park.py -v
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
SCRIPT = os.path.join(SCRIPTS, "report_fix_result.sh")
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import console_escalation  # noqa: E402
import design_answer  # noqa: E402
import fix_handoff  # noqa: E402
import step_shell  # noqa: E402
from test_act_emission_scenario import (  # noqa: E402
    CARD,
    PR,
    PRE_SHA,
    REPO,
    _checkout,
    _report_env,
)
from test_fix_exit_classified import (  # noqa: E402
    GH_STUB,
    QA,
    RECEIPT_PATHS,
    critic,
    rest,
)

PART = "Button"
PART_PATH = "web/src/components/atoms/Button.tsx"
FIRST_LINE = f"{design_answer.QUESTION_MARK} `{PART}` at `{PART_PATH}`"
FINDING = "The settings page builds its own button instead of the shared one."
QUESTION = "Should the settings page use the shared Button, or is a second button wanted?"
ANSWER = "Use the shared Button"
WHY = "one button keeps every page looking and behaving the same"

FINDING_LINE = f"{console_escalation.FINDING_PREFIX} {FINDING}"
QUESTION_LINE = f"{console_escalation.QUESTION_PREFIX} {QUESTION}"
RECOMMENDATION_LINE = f"{console_escalation.RECOMMENDATION_PREFIX} {ANSWER} — {WHY}"

#: The block as the critic writes it (the shared contract), inside its verdict.
BLOCK = "\n".join((FIRST_LINE, FINDING_LINE, QUESTION_LINE, RECOMMENDATION_LINE))
VERDICT = (critic("REQUEST_CHANGES", PRE_SHA) + "\n\n"
           "1. The settings page re-implements a part the design system ships.\n\n"
           + BLOCK + "\n")
#: The same lines with the Question before the Finding.
OUT_OF_ORDER = (critic("REQUEST_CHANGES", PRE_SHA) + "\n\n" + "\n".join(
    (FIRST_LINE, QUESTION_LINE, FINDING_LINE, RECOMMENDATION_LINE)) + "\n")
#: An ordinary finding, no block.
PLAIN = critic("REQUEST_CHANGES", PRE_SHA) + "\n\n1. The test is vacuous.\n"

#: The fix agent's blocker file: the block, word for word (step 6).
BLOCKER = "The finding is the CEO's to answer.\n\n" + BLOCK

#: The question the card must carry: the block's first line, then the three
#: lines `console_escalation.render` writes.
EXPECTED_QUESTION = FIRST_LINE + "\n" + console_escalation.render(
    console_escalation.Escalation(FINDING, QUESTION, ANSWER, WHY))

GREEN_LIGHT = "Green Light"
TRIAGE = "Triage"

#: `design_answer.py`, stubbed: logs its argv beside the Linear calls and
#: answers `state` with the word the test chose.
DESIGN_ANSWER_STUB = """#!/usr/bin/env python3
import json, os, sys
open({log!r}, 'a').write(json.dumps(['design_answer.py'] + sys.argv[1:]) + '\\n')
if sys.argv[1:2] == ['state']:
    print(os.environ.get('DQ_STATE', 'none'))
sys.stderr.write('design-answer: stubbed\\n')
"""


def report(*, verdict: str, state: str = "none", blocker: str = BLOCKER,
           card: str = CARD):
    """Run the real report_fix_result.sh on a blocker. Returns
    (proc, gh writes, the ordered card-side calls)."""
    for path in RECEIPT_PATHS:
        if os.path.exists(path):
            os.remove(path)
    with tempfile.TemporaryDirectory() as td:
        base = _checkout(td)
        log = os.path.join(td, "calls.jsonl")
        for name, body in (
                ("linear_ops.py", "#!/usr/bin/env python3\nimport json, sys\n"
                 f"open({log!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n"),
                ("hold.py", "#!/usr/bin/env python3\nimport json, sys\n"
                 f"open({log!r}, 'a').write(json.dumps(['hold.py'] + sys.argv[1:]) + '\\n')\n"),
                ("design_answer.py", DESIGN_ANSWER_STUB.format(log=log))):
            with open(os.path.join(base, "scripts", name), "w", encoding="utf-8") as fh:
                fh.write(body)
        with open(os.path.join(base, "critic-verdict.md"), "w", encoding="utf-8") as fh:
            fh.write(verdict)
        binary = os.path.join(td, "bin")
        os.makedirs(binary)
        with open(os.path.join(binary, "gh"), "w") as fh:
            fh.write(GH_STUB)
        os.chmod(os.path.join(binary, "gh"), 0o755)
        thread_file = os.path.join(td, "thread.json")
        with open(thread_file, "w", encoding="utf-8") as fh:
            json.dump([[rest(QA, verdict)]], fh)
        paths = fix_handoff.open_handoff(td, REPO, PR, PRE_SHA,
                                         legacy_dir=os.path.join(td, "legacy"))
        with open(paths["blocker"], "w", encoding="utf-8") as fh:
            fh.write(blocker + "\n")
        gh_log = os.path.join(td, "gh.jsonl")
        env = dict(os.environ, PATH=binary + os.pathsep + os.environ["PATH"],
                   CLASSIFICATION="", DISPATCH_TOKEN="test", GH_LOG=gh_log,
                   GH_HEAD=PRE_SHA, GH_THREAD=thread_file, GH_THREAD_FAILS="0",
                   GH_CHECKS="", DQ_STATE=state, **_report_env(td, "fix"))
        env["CARD"] = card
        proc = subprocess.run(["bash", SCRIPT], cwd=td, env=env,
                              capture_output=True, text=True, timeout=120)

        def lines(path):
            if not os.path.exists(path):
                return []
            with open(path, encoding="utf-8") as fh:
                return [json.loads(line) for line in fh.read().splitlines()]

        return proc, lines(gh_log), lines(log)


def card_comments(calls):
    return [c for c in calls if c[:1] == ["comment"]]


def moves(calls, lane):
    return [c for c in calls if c[:1] in (["advance"], ["state"]) and lane in c[2:3]]


def holds(calls):
    return [c for c in calls if c[:2] == ["hold.py", "apply"]]


def needs_human(calls):
    return [c for c in calls if "needs-human" in c]


def design_calls(calls, command):
    return [c for c in calls if c[:2] == ["design_answer.py", command]]


def pr_comments(writes):
    return [w["body"] for w in writes if w["kind"] == "comment"]


class OK:
    def ran(self, result):
        proc, writes, calls = result
        self.assertEqual(0, proc.returncode, proc.stderr + proc.stdout)
        return writes, calls


class TheQuestionIsAskedInGreenLightTest(OK, unittest.TestCase):
    """`none`: asked on the card, then moved to Green Light — never Triage."""

    def test_the_question_goes_on_the_card_then_the_card_moves(self):
        _, calls = self.ran(report(verdict=VERDICT, state="none"))
        asked = card_comments(calls)
        self.assertEqual([["comment", CARD, EXPECTED_QUESTION]], asked, calls)
        moved = moves(calls, GREEN_LIGHT)
        self.assertEqual(1, len(moved), calls)
        self.assertEqual(["advance", CARD, GREEN_LIGHT], moved[0][:3])
        self.assertTrue(moved[0][3].startswith("In Review,"), moved[0])
        # The comment before the move: a card in the queue with nothing to answer
        # is a silent park.
        self.assertLess(calls.index(asked[0]), calls.index(moved[0]), calls)

    def test_no_dispute_hold_no_needs_human_no_triage(self):
        _, calls = self.ran(report(verdict=VERDICT, state="none"))
        self.assertEqual([], holds(calls))
        self.assertEqual([], needs_human(calls))
        self.assertEqual([], moves(calls, TRIAGE))

    def test_the_state_is_read_for_the_part_and_path_the_block_names(self):
        _, calls = self.ran(report(verdict=VERDICT, state="none"))
        self.assertEqual(
            [["design_answer.py", "state", CARD, "--part", PART, "--path", PART_PATH]],
            design_calls(calls, "state"))
        self.assertEqual([], design_calls(calls, "handback"))

    def test_the_question_declares_the_three_lines(self):
        _, calls = self.ran(report(verdict=VERDICT, state="none"))
        body = card_comments(calls)[0][2]
        self.assertEqual(FIRST_LINE, body.split("\n")[0])
        self.assertEqual([], console_escalation.problems(body), body)
        esc = console_escalation.parse(body)
        self.assertEqual((FINDING, QUESTION, ANSWER, WHY),
                         (esc.finding, esc.question, esc.recommendation, esc.why))

    def test_the_pull_request_is_told_and_holds_no_dispute(self):
        writes, _ = self.ran(report(verdict=VERDICT, state="none"))
        bodies = pr_comments(writes)
        self.assertEqual(1, len(bodies), bodies)
        self.assertIn(f"`{PART}` at `{PART_PATH}`", bodies[0])
        self.assertIn(GREEN_LIGHT, bodies[0])
        self.assertNotIn("fix-attempt-blocked", bodies[0])
        self.assertFalse(bodies[0].startswith("🛑"), bodies[0])


class AnUnreadableThreadAsksAnywayTest(OK, unittest.TestCase):
    """`unknown`: a second copy is visible; a lost question is a stall nobody sees."""

    def test_unknown_asks_and_moves_as_none_does(self):
        _, calls = self.ran(report(verdict=VERDICT, state="unknown"))
        asked = card_comments(calls)
        self.assertEqual([["comment", CARD, EXPECTED_QUESTION]], asked, calls)
        moved = moves(calls, GREEN_LIGHT)
        self.assertEqual(1, len(moved), calls)
        self.assertLess(calls.index(asked[0]), calls.index(moved[0]), calls)
        self.assertEqual([], holds(calls))
        self.assertEqual([], moves(calls, TRIAGE))


class AnAskedQuestionIsNotAskedTwiceTest(OK, unittest.TestCase):

    def test_asked_moves_to_green_light_and_posts_nothing_on_the_card(self):
        _, calls = self.ran(report(verdict=VERDICT, state="asked"))
        self.assertEqual([], card_comments(calls), calls)
        self.assertEqual(1, len(moves(calls, GREEN_LIGHT)), calls)
        self.assertEqual([], holds(calls))
        self.assertEqual([], moves(calls, TRIAGE))
        self.assertEqual([], design_calls(calls, "handback"))


class AnAnsweredQuestionIsHandedBackTest(OK, unittest.TestCase):

    def test_answered_parks_nothing_and_runs_the_hand_back(self):
        writes, calls = self.ran(report(verdict=VERDICT, state="answered"))
        self.assertEqual([["design_answer.py", "handback", CARD, "--repo", REPO]],
                         design_calls(calls, "handback"), calls)
        self.assertEqual([], [c for c in calls if c[:1] in (["advance"], ["state"])])
        self.assertEqual([], holds(calls))
        self.assertEqual([], needs_human(calls))
        self.assertEqual([], card_comments(calls))
        # The re-review is the hand-back's own dispatch, never this script's.
        self.assertEqual([], [w for w in writes if w["kind"] == "dispatch"])


class AQuestionRaisedAgainIsABrokenLoopTest(OK, unittest.TestCase):

    def test_handed_back_parks_in_triage_under_fix_dispute(self):
        _, calls = self.ran(report(verdict=VERDICT, state="handed-back"))
        self.assertEqual(1, len(moves(calls, TRIAGE)), calls)
        self.assertEqual([], moves(calls, GREEN_LIGHT))
        held = holds(calls)
        self.assertEqual(1, len(held), calls)
        self.assertEqual(["hold.py", "apply", CARD, "--reason", "fix-dispute"], held[0][:5])
        self.assertEqual([], design_calls(calls, "handback"))
        # The ordinary blocker receipt, not the question.
        self.assertFalse(any(c[2].startswith(design_answer.QUESTION_MARK)
                             for c in card_comments(calls)), calls)


class EveryOtherBlockerParksAsBeforeTest(OK, unittest.TestCase):

    def assert_triage_as_before(self, verdict):
        writes, calls = self.ran(report(verdict=verdict, state="none"))
        self.assertEqual([], design_calls(calls, "state"), calls)
        self.assertEqual([], moves(calls, GREEN_LIGHT), calls)
        self.assertEqual(1, len(moves(calls, TRIAGE)), calls)
        held = holds(calls)
        self.assertEqual(1, len(held), calls)
        self.assertEqual(["hold.py", "apply", CARD, "--reason", "fix-dispute"], held[0][:5])
        bodies = pr_comments(writes)
        self.assertEqual(1, len(bodies), bodies)
        self.assertTrue(bodies[0].startswith("🛑 Fix attempt 2 blocked: "), bodies[0])

    def test_a_verdict_without_the_block(self):
        self.assert_triage_as_before(PLAIN)

    def test_a_block_with_its_lines_out_of_order(self):
        self.assert_triage_as_before(OUT_OF_ORDER)

    def test_a_block_missing_a_line(self):
        self.assert_triage_as_before(
            critic("REQUEST_CHANGES", PRE_SHA) + "\n\n"
            + "\n".join((FIRST_LINE, FINDING_LINE, RECOMMENDATION_LINE)) + "\n")

    def test_a_block_in_a_verdict_that_is_not_request_changes(self):
        self.assert_triage_as_before(VERDICT.replace("REQUEST_CHANGES cause:defect",
                                                     "APPROVE", 1))

    def test_no_card_takes_the_ordinary_path(self):
        _, calls = self.ran(report(verdict=VERDICT, state="none", card=""))
        self.assertEqual([], design_calls(calls, "state"), calls)
        self.assertEqual([], moves(calls, GREEN_LIGHT), calls)


# --------------------------------------------------------------------------
# the fix prompt
# --------------------------------------------------------------------------
AGENT_FIX = os.path.join(".github", "workflows", "agent-fix.yml")


def blocker_item() -> str:
    """Step 6 of the fix agent's prompt, up to 6b."""
    m = re.search(r"\n +6\. If a finding is unfixable(.*?)\n +6b\. ",
                  step_shell.workflow_source(AGENT_FIX, root=ROOT), re.S)
    assert m, "step 6 of the fix prompt was not found in agent-fix.yml"
    return " ".join(m.group(1).split())


class TheFixPromptSaysWhoAnswersTest(unittest.TestCase):

    def test_step_6_hands_the_design_question_to_the_ceo(self):
        item = blocker_item()
        self.assertIn(
            f"A finding that carries the `{design_answer.QUESTION_MARK}` block is "
            "the CEO's to answer, not yours: push nothing, and write the "
            "blocker file with the block's four lines in it word for word",
            item)


if __name__ == "__main__":
    unittest.main()
