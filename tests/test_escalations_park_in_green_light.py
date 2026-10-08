"""DRE-2776: an escalation is not a broken card — it parks in Green Light.

DRE-2722 moved every "a person is needed" park to `Triage` on the strength of
its own title. The criteria it was actually accepted against said something
narrower: Triage takes the UNROUTABLE and the HELD card, and `Green Light`
keeps what the lane it renamed held — which included the escalate-by-exception
question. Operator decision, 2026-08-27: escalations belong in `Green Light`,
the CEO's "needs you" queue. A real decision sitting in a lane people scan as a
defect list is how Triage rotted the first time (17 machine-created cards, zero
transitions, 2026-08-24).

The split this file pins, in one sentence: **a card parked because a human must
DECIDE goes to `Green Light`; a card parked because it went WRONG stays in
`Triage`.** So the engineer's escalate-by-exception path moves; the fix loop's
non-convergence park, the unfixable-check hold and the red-main repair card —
each of them a card that went wrong — do not, and stay pinned next door in
`test_green_light_rename.py`.

Four surfaces have to agree or the rule is only true in one of them: the shell
that actually moves the card, the prompt that tells the agent where its
question lands, the brief it reads when deciding to escalate, and the standard
that brief inherits. The review of this change found the standard updated and
the other three untouched — which is the whole reason this file exists.
"""
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import console_escalation  # noqa: E402
import linear_ops  # noqa: E402
import reconcile  # noqa: E402
import step_shell  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

#: Where a card goes when a human owes it a DECISION, and where it goes when it
#: is simply broken. Named apart because the whole card is the distinction.
DECISION_LANE = "Green Light"
BROKEN_CARD_LANE = "Triage"

AGENT_TASK = os.path.join(".github", "workflows", "agent-task.yml")
PLAN = os.path.join(".github", "workflows", "plan.yml")


def read(rel: str) -> str:
    return open(os.path.join(ROOT, rel), encoding="utf-8").read()


def report_step() -> str:
    m = re.search(
        r"name:\s*Report result to Linear(.*?)(?:\n      - name:|\Z)",
        step_shell.workflow_source(AGENT_TASK, root=ROOT),
        re.S,
    )
    assert m, "'Report result to Linear' step not found in agent-task.yml"
    return m.group(1)


def escalation_branch() -> str:
    """The escalate-by-exception branch — the shell that actually runs when an
    agent stops to ask the CEO a question."""
    m = re.search(
        r"elif \[ -f /tmp/agent-escalation\.txt \](.*?)\n\s*elif \[ -f /tmp/agent-blocker\.txt \]",
        report_step(),
        re.S,
    )
    assert m, "escalation branch not found in agent-task.yml"
    return m.group(1)


def escalate_prompt_item() -> str:
    """Step 6 of the build agent's own prompt — what the agent is TOLD happens
    to its question."""
    m = re.search(
        r"\n +6\. ESCALATE(.*?)\n +7\. ",
        step_shell.workflow_source(AGENT_TASK, root=ROOT),
        re.S,
    )
    assert m, "the ESCALATE prompt item was not found in agent-task.yml"
    return m.group(1)


def brief_escalation_section() -> str:
    """`briefs/engineer.md`'s "How to escalate" — the operational instructions
    an agent follows at escalation time, which is not the same document as the
    standard below."""
    m = re.search(r"### How to escalate(.*?)\n## ", read("briefs/engineer.md"), re.S)
    assert m, "'How to escalate' section not found in briefs/engineer.md"
    return m.group(1)


def plan_epic_gate_comment() -> str:
    """The comment block sitting directly above `plan.yml`'s `Plan → second
    critic` step — the plan route's last move since DRE-5284, which replaced
    `Epic → Green Light` — the justification the next editor of that routing
    reads first."""
    m = re.search(
        r"((?:^ *#.*\n)+) *- name: Plan → second critic\n",
        step_shell.workflow_source(PLAN, root=ROOT),
        re.M,
    )
    assert m, "the 'Plan → second critic' step's comment was not found in plan.yml"
    return m.group(1)


class TheShellThatMovesTheCardTest(unittest.TestCase):
    """Half one: the live code path. Prose can say anything; this is the line
    that hands Linear a lane name."""

    def test_the_escalation_branch_advances_to_the_decision_lane(self):
        self.assertIn(f'"{DECISION_LANE}"', escalation_branch())

    def test_the_broken_card_lane_is_gone_from_the_branch_entirely(self):
        # Not just "does not advance to Triage": the literal must be absent
        # from the branch, comment included. The retired rule was restated in
        # the inline comment beside the command, which is exactly where the
        # next reader re-derives it from.
        self.assertNotIn(BROKEN_CARD_LANE, escalation_branch())

    def test_the_branch_still_posts_the_question_before_moving_the_card(self):
        # Moving the card without the question is a silent park: the CEO sees a
        # card appear in their queue with nothing to answer.
        branch = escalation_branch()
        self.assertLess(
            branch.index("linear_ops.py comment"),
            branch.index(f'"{DECISION_LANE}"'),
        )

    def test_the_prompt_names_the_same_lane_the_branch_uses(self):
        # A prompt that names a different lane than the shell is a lie the
        # agent repeats to the CEO in its own escalation note.
        item = escalate_prompt_item()
        self.assertIn(DECISION_LANE, item)
        self.assertNotIn(BROKEN_CARD_LANE, item)


class TheInstructionsTheAgentReadsTest(unittest.TestCase):
    """Half two: the documents. The brief is what an agent consults at the
    moment it decides to escalate — the standard it inherits is one hop
    further away, and the two disagreeing is how this bug shipped."""

    def test_the_brief_routes_escalations_to_the_decision_lane(self):
        self.assertIn(DECISION_LANE, brief_escalation_section())

    def test_the_brief_does_not_route_escalations_to_the_broken_card_lane(self):
        self.assertNotIn(BROKEN_CARD_LANE, brief_escalation_section())

    def test_the_standard_routes_escalations_to_the_decision_lane(self):
        self.assertIn(
            f"parks the card in the **`{DECISION_LANE}`** lane",
            read("standards/card-quality.md"),
        )

    def test_the_readme_describes_the_same_route(self):
        m = re.search(
            r"stops and\s+asks only by exception(.*?)\n\n", read("README.md"), re.S
        )
        assert m, "the README's escalate-by-exception paragraph was not found"
        self.assertIn(DECISION_LANE, m.group(1))
        self.assertNotIn(BROKEN_CARD_LANE, m.group(1))


class ThePlannerStepsOwnExplanationTest(unittest.TestCase):
    """The plan workflow's own routing was never wrong — epics have always gone
    to `Green Light`. The comment justifying it was: it told the next reader
    that stuck-agent escalations park in Triage, the exact rule this card
    reverses, sitting on top of the step most likely to be edited when this area
    changes next. A confident wrong explanation beside correct code is how the
    original mismatch shipped, so pin the explanation too.
    """

    #: Naming `Triage` in this comment is FINE — drawing the contrast is the
    #: point, so a flat `assertNotIn` would ban the correct wording along with
    #: the wrong one. What is banned is ATTRIBUTING an agent's escalation to it:
    #: the two ideas inside one sentence.
    ESCALATION_WORDS = re.compile(r"escalat|stuck", re.I)

    def _sentences(self) -> list[str]:
        prose = " ".join(
            line.lstrip().lstrip("#").strip()
            for line in plan_epic_gate_comment().splitlines()
        )
        return re.split(r"(?<=\.)\s+", prose)

    def test_no_sentence_routes_an_escalation_to_the_broken_card_lane(self):
        offenders = [
            s for s in self._sentences()
            if BROKEN_CARD_LANE in s and self.ESCALATION_WORDS.search(s)
        ]
        self.assertEqual(
            [], offenders,
            f"the plan workflow still explains escalations as parking in "
            f"{BROKEN_CARD_LANE!r}: {offenders}. They park in "
            f"{DECISION_LANE!r}; {BROKEN_CARD_LANE} is the went-wrong lane.",
        )

    def test_the_comment_still_explains_the_lane_the_step_moves_to(self):
        # The cheap way to pass the guard above is to delete the comment. The
        # step's justification has to survive the correction.
        self.assertIn(DECISION_LANE, plan_epic_gate_comment())


class HumanParkGateTest(unittest.TestCase):
    """The dispatch gate (DRE-2024) asks exactly one question: does a human owe
    this card an action before automation may act again? Both queues answer
    yes, so both have to gate. Recognising only one of them is the DeltaSolv
    PR #120 loop — an identical doomed fix run every sweep, forever."""

    def _parked(self, lane: str) -> bool:
        payload = {"issue": {"state": {"name": lane}, "labels": {"nodes": []}}}
        with mock.patch.object(linear_ops, "gql", return_value=payload):
            return reconcile.card_parked_for_human("DRE-2009")

    def test_a_card_awaiting_a_decision_is_human_parked(self):
        self.assertTrue(self._parked(DECISION_LANE))

    def test_a_broken_card_is_still_human_parked(self):
        self.assertTrue(self._parked(BROKEN_CARD_LANE))

    def test_a_working_lane_is_not_human_parked(self):
        self.assertFalse(self._parked("In QA"))

    def test_both_queues_are_declared_in_one_place(self):
        # One tuple, so a third human queue is onboarded by editing a list and
        # not by finding every `== PARKED_STATE` in the sweep.
        self.assertEqual(
            (BROKEN_CARD_LANE, DECISION_LANE), reconcile.PARKED_STATES
        )


#: The file the agent writes its question to, and the one the branch completes.
ESCALATION_FILE = "/tmp/agent-escalation.txt"  # nosec B108 — the prompt's own path

#: Who the completed Recommendation line says stated nothing (DRE-3911).
BUILD_AGENT = "the build agent"

REPORT_SCRIPT = os.path.join("scripts", "report_agent_result.sh")


def complete_command() -> str:
    """The one shell line in the escalation branch that runs `complete`, its
    `||` fallback included — the exact text the step executes."""
    found = [line.strip() for line in escalation_branch().split("\n")
             if "console_escalation.py complete" in line]
    assert len(found) == 1, f"expected one `complete` line, found {found}"
    return found[0]


def run_complete(text: str, *, renderer: bool = True) -> str:
    """Stage `text` as the agent's question and run the branch's own
    `complete` line through bash, from a directory where `.bureau-pipeline` is
    this checkout — or, with `renderer=False`, where it is missing."""
    td = tempfile.mkdtemp()
    try:
        if renderer:
            os.symlink(ROOT, os.path.join(td, ".bureau-pipeline"))
        staged = os.path.join(td, "agent-escalation.txt")
        with open(staged, "w", encoding="utf-8") as fh:
            fh.write(text)
        line = complete_command().replace(ESCALATION_FILE, shlex.quote(staged))
        proc = subprocess.run(["bash", "-c", "set -e\n" + line], cwd=td,
                              capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
        return proc.stdout
    finally:
        shutil.rmtree(td, ignore_errors=True)


class TheQuestionCarriesTheThreeLinesTest(unittest.TestCase):
    """DRE-3911: the question the CEO reads carries the three declared lines.
    The branch points at `console_escalation.py complete`; it never names a
    prefix itself."""

    def test_the_branch_completes_the_question_before_it_posts_and_moves(self):
        branch = escalation_branch()
        at = branch.index("console_escalation.py complete")
        self.assertLess(branch.index("🙋"), at)
        self.assertLess(at, branch.index("linear_ops.py comment"))
        self.assertLess(at, branch.index(f'"{DECISION_LANE}"'))

    def test_the_complete_line_names_the_file_and_the_build_agent(self):
        words = shlex.split(complete_command().split("||")[0])
        self.assertEqual(
            ["python3", ".bureau-pipeline/scripts/console_escalation.py",
             "complete", ESCALATION_FILE], words[:4])
        self.assertEqual(BUILD_AGENT, words[words.index("--who") + 1])
        self.assertTrue(words[words.index("--question") + 1].endswith("?"))

    def test_the_file_is_cat_only_as_the_fallback(self):
        branch = escalation_branch()
        cats = [m.start() for m in
                re.finditer(re.escape(f"cat {ESCALATION_FILE}"), branch)]
        self.assertEqual(1, len(cats), branch)
        self.assertTrue(branch[:cats[0]].rstrip().endswith("||"), branch)
        self.assertTrue(complete_command().endswith(
            f"|| cat {ESCALATION_FILE}"))

    def test_the_branch_names_no_prefix(self):
        branch = escalation_branch()
        for _, prefix in console_escalation._LINES:
            self.assertNotIn(prefix, branch)
        self.assertNotIn(console_escalation.NONE_GIVEN, branch)


class TheCompleteCommandRunsTest(unittest.TestCase):
    """The branch's own `complete` line, run through bash on staged files."""

    PROSE = ("The export the old console read still ships, and nothing in "
             "this repository reads it any more.\n\n"
             "Keeping it costs a test suite; dropping it breaks anyone outside "
             "the repository who still reads it.\n\n"
             "Should the build keep the old export, or drop it now?\n")

    def test_plain_prose_parks_with_none_given(self):
        out = run_complete(self.PROSE)
        self.assertTrue(out.startswith(self.PROSE.rstrip()), out)
        last = out.rstrip("\n").split("\n")[-3:]
        self.assertEqual([], console_escalation.problems("\n".join(last)), last)
        self.assertEqual([], console_escalation.problems(out), out)
        esc = console_escalation.parse("\n".join(last))
        self.assertEqual(
            "Should the build keep the old export, or drop it now?",
            esc.question)
        self.assertIsNone(esc.recommendation)
        recommendation = last[2][len(console_escalation.RECOMMENDATION_PREFIX):]
        self.assertEqual(
            "none given — the build agent stated no recommendation",
            recommendation.strip())

    def test_a_question_that_carries_the_three_lines_comes_back_unchanged(self):
        lines = console_escalation.render(console_escalation.Escalation(
            finding="The old export still ships and nothing here reads it.",
            question="Should the build drop the old export?",
            recommendation="Drop it",
            why="nothing in the fleet reads it",
        ))
        text = "The export is dead weight.\n\n" + lines
        # `print` ends the last line; not one byte of the body changes.
        self.assertEqual(text + "\n", run_complete(text))

    def test_a_renderer_that_cannot_run_still_posts_the_question(self):
        self.assertEqual(self.PROSE, run_complete(self.PROSE, renderer=False))


def _git(*args: str) -> str | None:
    try:
        done = subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                              text=True, check=False)
    except OSError:
        return None
    return done.stdout if done.returncode == 0 else None


def _script_before_and_after() -> tuple[str, str] | None:
    """The script before and after DRE-3911: the commit that first ran
    `console_escalation.py complete` against its parent, or — before that
    commit exists — the working tree against its merge base with `main`."""
    log = _git("log", "--format=%H", "--reverse", "-S",
               "console_escalation.py complete", "--", REPORT_SCRIPT)
    first = (log or "").split()
    if first:
        before = _git("show", f"{first[0]}^:{REPORT_SCRIPT}")
        after = _git("show", f"{first[0]}:{REPORT_SCRIPT}")
        if before is not None and after is not None:
            return before, after
    base = (_git("merge-base", "HEAD", "origin/main") or "").strip()
    if base:
        before = _git("show", f"{base}:{REPORT_SCRIPT}")
        if before is not None:
            return before, read(REPORT_SCRIPT)
    return None


_ESCALATION_REGION = re.compile(
    r"(\nelif \[ -f /tmp/agent-escalation\.txt \].*?)(?=\nelif \[ -f /tmp/agent-blocker\.txt \])",
    re.S)
_DEAD_RUN_BLOCK = re.compile(r"\nelse\n  # The dead-run or turn-cap decision.*?\nfi\n", re.S)


class NoOtherBranchChangedTest(unittest.TestCase):
    """DRE-3911 edits the escalation branch and nothing else in the script —
    above all not the dead-run block DRE-6178 owns."""

    def _pair(self) -> tuple[str, str]:
        pair = _script_before_and_after()
        if pair is None:
            # The unit job checks out full history and sets this, so there the
            # missing history is a failure, never a green skip.
            if os.environ.get("BUREAU_REQUIRE_GIT_HISTORY"):
                self.fail("no git history for the script, and this job requires it")
            self.skipTest("no git history for the script (a shallow checkout)")
        return pair

    def test_the_escalation_branch_is_what_changed(self):
        before, after = self._pair()
        self.assertNotIn("console_escalation.py complete",
                         _ESCALATION_REGION.search(before).group(1))
        self.assertIn("console_escalation.py complete",
                      _ESCALATION_REGION.search(after).group(1))

    def test_everything_outside_the_escalation_branch_is_unchanged(self):
        before, after = self._pair()
        self.assertEqual(_ESCALATION_REGION.sub("", before),
                         _ESCALATION_REGION.sub("", after))

    def test_the_dead_run_block_is_byte_identical(self):
        before, after = self._pair()
        self.assertEqual(_DEAD_RUN_BLOCK.search(before).group(0),
                         _DEAD_RUN_BLOCK.search(after).group(0))


if __name__ == "__main__":
    unittest.main()
