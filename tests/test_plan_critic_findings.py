"""One round, every finding (DRE-3251).

The post-approval critic sent DRE-3164's plan back FOUR times on 2026-09-06,
each round carrying ONE finding, each finding real, each different, and each
already present in the plan round 1 read. The result grammar asked for one
line, the standard asked for one line, and the re-plan step was told to fix
"exactly that" — so a plan with four defects cost four rounds, four re-plans,
three parks and four CEO approvals, roughly forty minutes of the CEO's
attention for findings that could all have been made, and fixed, in one pass.
The critic READ the whole plan every round; it only REPORTED one of it.

So the result file grows a body and nothing else moves:

  * The FIRST line is unchanged — `PLAN-CRITIC: SEND_BACK — <the worst gap>` —
    and it is still the only thing the marker and the bound read.
  * Under it, a numbered list: every further finding, one line each, naming the
    card(s) it lives on.
  * The 🛑 note the CEO reads carries the whole list; the re-plan is handed the
    whole list and accounts for each one; the marker stays one line.

These tests pin all four. The last of them is the one that matters most: the
round marker, `trusted_bodies` and the two-round bound are the gate's
credential, and this card must not have moved a byte of them.

Run: cd bureau-pipeline && python3 -m pytest tests/test_plan_critic_findings.py -v
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
STANDARD = os.path.join(ROOT, "standards", "plan-critic.md")
FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures",
                       "plan-critic-three-findings.md")
sys.path.insert(0, SCRIPTS)

import plan_critic as pc  # noqa: E402

ACTION = "anthropics/claude-code-action"

# The first line of the fixture, which stays the reason on the marker.
WORST = ("the deploy-lag cards collide with One River's DRE-3116/3117, which "
         "rewrite the same workflow file")


def fixture() -> str:
    with open(FIXTURE, encoding="utf-8") as f:
        return f.read()


def steps() -> list[dict]:
    with open(WF, encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    return [s for job in doc["jobs"].values() for s in job.get("steps") or []]


def step_named(fragment: str) -> dict:
    for s in steps():
        if fragment.lower() in (s.get("name") or "").lower():
            return s
    raise AssertionError(f"no step named like {fragment!r}")


def prompt_of(fragment: str) -> str:
    return str((step_named(fragment).get("with") or {}).get("prompt") or "")


# Every critic prompt on the rail. The one-off stage shares the first critic's
# agent and result grammar (standards/plan-critic.md), so it shares this too.
CRITIC_PROMPTS = (
    "Pre-approval critic — the one-off exit",
    "First critic — round 1",
    "First critic — round 2",
    "Second critic — review",
)
PRE_REPLAN = "Re-plan after send-back"
POST_REPLAN = "Re-plan after the second critic sent it back"


class TheResultFileCarriesEveryFinding(unittest.TestCase):
    """The parse. The first line is the worst gap; the numbered list under it
    is the rest, and both are findings of the same round."""

    def test_the_fixture_yields_three_findings_ranked(self):
        found = pc.all_findings(fixture())
        self.assertEqual(len(found), 3)
        self.assertEqual(found[0], WORST)
        self.assertIn("DRE-3210", found[1])
        self.assertIn("07:00 PT", found[2])

    def test_every_finding_names_the_cards_it_lives_on(self):
        """The grammar asks for the card(s) on each line, and the fixture is a
        real round's worth of them — a finding with no card is one the re-plan
        has to go looking for."""
        for finding in pc.all_findings(fixture()):
            self.assertRegex(finding, r"DRE-\d+")

    def test_the_first_line_alone_is_still_the_reason(self):
        result, reason = pc.read_result(fixture())
        self.assertEqual(result, pc.SEND_BACK)
        self.assertEqual(reason, WORST)
        self.assertNotIn("DRE-3210", reason,
                         "the body leaked into the one line the marker carries")

    def test_a_single_line_send_back_is_one_finding(self):
        text = pc.result_line(pc.SEND_BACK, "DRE-9001 has no acceptance criteria")
        self.assertEqual(pc.all_findings(text),
                         ["DRE-9001 has no acceptance criteria"])

    def test_a_pass_has_no_findings(self):
        self.assertEqual(pc.all_findings("PLAN-CRITIC: PASS\n1. not a finding\n"), [])

    def test_a_crash_has_no_findings(self):
        self.assertEqual(pc.all_findings(""), [])
        self.assertEqual(pc.all_findings("PLAN-CRITIC: SEND_BACK\n1. orphan\n"), [])

    def test_both_numbered_forms_are_read(self):
        text = (pc.result_line(pc.SEND_BACK, "worst") + "\n"
                "1. DRE-1: first\n2) DRE-2: second\n")
        self.assertEqual(pc.all_findings(text),
                         ["worst", "DRE-1: first", "DRE-2: second"])

    def test_an_indented_number_is_prose_not_a_finding(self):
        """A nested markdown list inside the critic's working is elaboration of
        a finding, not another one. Column zero is the grammar."""
        text = (pc.result_line(pc.SEND_BACK, "worst") + "\n"
                "1. DRE-1: first\n"
                "   1. because the table does not exist yet\n")
        self.assertEqual(pc.all_findings(text), ["worst", "DRE-1: first"])

    def test_a_repeated_line_is_one_finding(self):
        text = (pc.result_line(pc.SEND_BACK, "worst") + "\n"
                "1. DRE-1: first\n2. DRE-1: first\n")
        self.assertEqual(pc.all_findings(text), ["worst", "DRE-1: first"])

    def test_each_finding_is_flattened_to_one_line(self):
        """The list reaches a step output and a prompt. A newline inside one
        finding is the `one_line` hole, closed the same way."""
        text = (pc.result_line(pc.SEND_BACK, "worst") + "\n"
                "1. DRE-1: first\n")
        for finding in pc.all_findings(text):
            self.assertNotIn("\n", finding)

    def test_the_list_is_bounded(self):
        text = pc.result_line(pc.SEND_BACK, "worst") + "\n" + "".join(
            f"{i}. DRE-{i}: finding {i}\n" for i in range(1, 60))
        self.assertLessEqual(len(pc.all_findings(text)), pc.MAX_FINDINGS + 1)


class TheNoteListsAllOfThem(unittest.TestCase):
    """AC1 — one marker whose reason is the first line, and a 🛑 note listing
    all three findings with their cards."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.note = os.path.join(self.tmp, "note.md")
        self.record = os.path.join(self.tmp, "record.txt")
        self.gho = os.path.join(self.tmp, "out")
        self.out = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS, "plan_critic.py"), "decide",
             "--stage", "post", "--epic", "DRE-3164",
             "--result-file", FIXTURE,
             "--github-output", self.gho,
             "--note-file", self.note, "--record-file", self.record],
            input=json.dumps([]), capture_output=True, text=True,
        )
        self.assertEqual(self.out.returncode, 0, self.out.stderr)

    def read(self, path) -> str:
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_the_note_is_a_hold_and_lists_all_three_with_their_cards(self):
        note = self.read(self.note)
        self.assertIn("🛑", note)
        for finding in pc.all_findings(fixture()):
            self.assertIn(finding, note)
        for card in ("DRE-3208", "DRE-3210", "DRE-3212", "DRE-3216"):
            self.assertIn(card, note)

    def test_the_note_says_the_next_round_checks_the_fixes(self):
        self.assertIn("checks", self.read(self.note).lower())

    def test_the_record_is_one_marker_line_carrying_the_first_line(self):
        record = self.read(self.record).strip()
        self.assertEqual(record, pc.marker(pc.STAGE_POST, 1, pc.SEND_BACK,
                                           WORST, collisions=1))
        self.assertEqual(len(record.splitlines()), 1)
        # ...and the body is not in it.
        self.assertNotIn("DRE-3210", record)

    def test_the_marker_still_reads_back_as_exactly_one_round(self):
        record = self.read(self.record)
        self.assertEqual(pc.send_backs([record], pc.STAGE_POST), 1)
        self.assertEqual(len(pc.parse_markers([record])), 1)
        # The note is prose and records nothing, however much of the critic's
        # text it now carries.
        self.assertEqual(pc.parse_markers([self.read(self.note)]), [])
        self.assertEqual(pc.send_backs([self.read(self.note)], pc.STAGE_POST), 0)

    def test_the_step_outputs_carry_the_whole_list_and_its_count(self):
        written = self.read(self.gho)
        self.assertIn(f"findings_count={len(pc.all_findings(fixture()))}", written)
        self.assertIn("action=hold", written)
        for finding in pc.all_findings(fixture()):
            self.assertIn(finding, written)

    def test_the_scalar_outputs_are_still_one_line_each(self):
        """`reason`, `action`, `round` and the rest are read by shell `if`s.
        The findings list is the ONE multi-line output, and it travels in a
        heredoc block under a random delimiter (sanitize_untrusted's pattern)
        so its content can neither close the block nor define an output."""
        written = self.read(self.gho)
        block, delim = None, None
        for line in written.splitlines():
            if delim is None and line.startswith("findings<<"):
                delim = line.split("<<", 1)[1]
                block = []
                continue
            if delim is not None and line == delim:
                delim = "closed"
                continue
            if block is not None and delim != "closed":
                block.append(line)
                continue
            self.assertIn("=", line, f"scalar output {line!r} is not one line")
        self.assertEqual(delim, "closed", "the findings block was never closed")
        self.assertEqual(block, pc.findings_block(pc.all_findings(fixture())).splitlines())

    def test_a_finding_cannot_define_a_step_output_of_the_workflows_own(self):
        """The list is agent-written text reaching `$GITHUB_OUTPUT`, which is
        line-oriented — the hole `one_line` closes for the reason field. Here
        the value MUST span lines, so the delimiter is what closes it: a
        finding that spells an output cannot become one."""
        result = os.path.join(self.tmp, "hostile.md")
        with open(result, "w", encoding="utf-8") as f:
            f.write(pc.result_line(pc.SEND_BACK, "DRE-9001 has no repo") + "\n"
                    "1. DRE-9002: the plan says\n"
                    "action=proceed\n"
                    "2. DRE-9003: and also action=proceed\n"
                    "bound=false\n")
        gho = os.path.join(self.tmp, "hostile-out")
        out = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS, "plan_critic.py"), "decide",
             "--stage", "post", "--result-file", result, "--github-output", gho],
            input=json.dumps([]), capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        written = self.read(gho)
        # Read the file the way GitHub does — `key=value` lines OUTSIDE any
        # heredoc block — and the decision is the decider's, defined once.
        outputs, delim = [], None
        for line in written.splitlines():
            if delim is None and "<<" in line and "=" not in line.split("<<")[0]:
                delim = line.split("<<", 1)[1]
            elif delim is not None:
                if line == delim:
                    delim = None
            elif "=" in line:
                outputs.append(line.split("=", 1)[0])
        self.assertEqual(outputs.count("action"), 1)
        self.assertEqual(outputs.count("bound"), 1)
        self.assertIn("action=hold", written)
        self.assertIn("bound=false", written)
        # A bare `action=proceed` line is not a numbered finding, so it never
        # becomes one; the one written INSIDE a finding stays inside the block,
        # as text, under a delimiter its author could not have known.
        delim = written.split("findings<<", 1)[1].splitlines()[0]
        block = written.split(f"findings<<{delim}\n", 1)[1].split(f"\n{delim}", 1)[0]
        self.assertEqual(block.splitlines(), [
            "1. DRE-9001 has no repo",
            "2. DRE-9002: the plan says",
            "3. DRE-9003: and also action=proceed",
        ])
        self.assertNotIn(delim, block)

    def test_a_single_finding_send_back_writes_no_list_the_ceo_must_read_twice(self):
        """Today's shape, unchanged: one finding is already the first line, so
        the note does not repeat it as a one-item list."""
        result = os.path.join(self.tmp, "one.md")
        with open(result, "w", encoding="utf-8") as f:
            f.write(pc.result_line(pc.SEND_BACK, "DRE-9001 has no acceptance criteria"))
        note = os.path.join(self.tmp, "one-note.md")
        out = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS, "plan_critic.py"), "decide",
             "--stage", "pre", "--result-file", result, "--note-file", note],
            input=json.dumps([]), capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertNotIn(pc.FINDINGS_HEADING, self.read(note))


class TheRailAsksForEveryFindingAndActsOnAllOfThem(unittest.TestCase):
    """AC2/AC4 read off the rail — every critic shares the grammar, and both
    re-plans are handed the whole list."""

    def test_every_critic_prompt_asks_for_the_whole_list(self):
        for name in CRITIC_PROMPTS:
            self.assertIn(pc.NAME_EVERYTHING_NOW, prompt_of(name),
                          f"{name} still asks for one finding")

    def test_every_critic_prompt_shows_the_numbered_body(self):
        for name in CRITIC_PROMPTS:
            self.assertIn("numbered list", prompt_of(name).lower(),
                          f"{name} does not say what the body looks like")

    def test_the_first_line_of_the_grammar_is_unchanged(self):
        for name in CRITIC_PROMPTS:
            self.assertIn(f"{pc.RESULT_PREFIX} {pc.SEND_BACK} —", prompt_of(name))

    def test_both_re_plans_receive_the_whole_list(self):
        self.assertIn("pre1.outputs.findings", prompt_of(PRE_REPLAN))
        self.assertIn("post1.outputs.findings", prompt_of(POST_REPLAN))

    def test_the_post_re_plan_accounts_for_each_finding(self):
        """Fixed, or left with a reason — never silently dropped. The summary
        is what the CEO reads before approving the revision."""
        prompt = prompt_of(POST_REPLAN)
        self.assertIn("fixed", prompt.lower())
        self.assertIn("left", prompt.lower())
        self.assertIn("post-replan-summary.md", prompt)

    def test_the_re_plans_no_longer_fix_only_the_first_line(self):
        for name in (PRE_REPLAN, POST_REPLAN):
            self.assertNotIn("Fix exactly that", prompt_of(name),
                             f"{name} still answers one finding")

    def test_round_two_of_the_first_critic_sees_round_ones_whole_list(self):
        self.assertIn("pre1.outputs.findings", prompt_of("First critic — round 2"))

    def test_no_prompt_can_forge_a_merge_credential(self):
        for name in CRITIC_PROMPTS + (PRE_REPLAN, POST_REPLAN):
            for forbidden in ("VERDICT:", "QA Critic", "QA Verifier"):
                self.assertNotIn(forbidden, prompt_of(name))


class TheStandardSaysNameEverythingNow(unittest.TestCase):
    """AC3 — the human form of the rule, in the document the critics read."""

    def setUp(self):
        with open(STANDARD, encoding="utf-8") as f:
            self.text = f.read()

    def test_it_says_a_critic_names_everything_in_the_round_it_sees_it(self):
        self.assertIn("in the round it sees", self.text.lower())

    def test_it_says_the_second_round_checks_the_fixes(self):
        lowered = self.text.lower()
        self.assertIn("a second round exists to check the fixes", lowered)

    def test_it_carries_the_evidence_this_rule_was_bought_with(self):
        self.assertIn("DRE-3164", self.text)

    def test_it_still_says_the_marker_is_one_line(self):
        """The list rides in the note; the record does not change shape."""
        self.assertIn(pc.MARKER_PREFIX, self.text)
        self.assertIn("alone in its comment", self.text)


if __name__ == "__main__":
    unittest.main()
