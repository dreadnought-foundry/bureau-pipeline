"""Two plan critics, one before the CEO reads the plan and one after (DRE-2721).

The card's premise: two passes asking DIFFERENT questions. The first reviews a
moving document and protects the CEO's attention; the second reviews a frozen
specification and asks what an agent will get wrong. If they asked the same
question the second would be waste, so "the two prompts are visibly different"
is a property this file asserts mechanically rather than a thing a reader is
asked to notice.

One section per acceptance criterion:

  1. A send-back carries a STATED REASON, and the reason survives the trip
     from the critic's result file to the step output the workflow reads.
  2. A pass proceeds — and nothing about the pre-stage claims post-stage sight.
  3. The post stage reviews the APPROVED text and says so in its own charter.
  4. THE BOUND. Two failed rounds at either critic and the plan proceeds to the
     CEO regardless, with the critic's stated reason attached. Unbounded is how
     17 cards sat in a lane for 27 days.
  5. The send-back RATE is computed from durable markers, so "how often does the
     second critic send a plan back" is readable over time instead of recalled.
  6. The two charters are different text, each carrying its own question, and
     neither is a copy of the other.

  D3. The post critic's charter states EXACTLY which epics it can see and which
      it cannot — never "consider other work" — and collisions caught here are
      counted separately from collisions found later, so the tripwire is
      measurable rather than remembered.

Plus the two rules the pipeline has paid for before: a crash is not a rejection
(standards/console-honesty.md rule 1), and nothing here may emit a string that
the merge gate reads as a QA verdict (standards/untrusted-content.md).

Run: cd bureau-pipeline && python3 -m pytest tests/test_plan_critic.py -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)

import design_parity  # noqa: E402
import plan_critic as pc  # noqa: E402


def _cards(*pairs):
    return [{"identifier": i, "body": b} for i, b in pairs]


GOOD_CARD = (
    "**Repo:** bureau-pipeline\n"
    "Add the send-back marker the next round reads.\n"
    "## Acceptance criteria\n"
    "- [ ] the marker parses back out of a comment thread\n"
)


class StagesAreTwoDifferentQuestions(unittest.TestCase):
    """AC6 — a reviewer can tell which critic is which without being told."""

    def test_both_stages_exist_and_are_the_only_two(self):
        self.assertEqual(tuple(pc.STAGES), (pc.STAGE_PRE, pc.STAGE_POST))

    def test_the_two_questions_are_not_the_same_question(self):
        self.assertNotEqual(pc.question(pc.STAGE_PRE), pc.question(pc.STAGE_POST))
        self.assertIn("CEO", pc.question(pc.STAGE_PRE))
        self.assertIn("missing", pc.question(pc.STAGE_POST).lower())

    def test_each_charter_carries_its_own_question_and_not_the_others(self):
        pre, post = pc.charter(pc.STAGE_PRE), pc.charter(pc.STAGE_POST)
        self.assertIn(pc.question(pc.STAGE_PRE), pre)
        self.assertNotIn(pc.question(pc.STAGE_POST), pre)
        self.assertIn(pc.question(pc.STAGE_POST), post)
        self.assertNotIn(pc.question(pc.STAGE_PRE), post)

    def test_the_charters_are_substantially_different_text(self):
        """Not a paraphrase of one another: if the second pass asked the first
        pass's question, the second pass is waste."""
        pre = set(pc.charter(pc.STAGE_PRE).split())
        post = set(pc.charter(pc.STAGE_POST).split())
        shared = len(pre & post) / len(pre | post)
        self.assertLess(shared, 0.6, "the two charters read as the same prompt")

    def test_the_pre_charter_says_intent_is_not_settled(self):
        """It protects attention and cannot do more than that — the charter has
        to say so, or the first critic starts doing the second's job."""
        pre = pc.charter(pc.STAGE_PRE).lower()
        self.assertIn("not settled", pre)

    def test_the_post_charter_says_the_plan_is_now_the_specification(self):
        post = pc.charter(pc.STAGE_POST).lower()
        self.assertIn("specification", post)
        self.assertIn("approved", post)

    def test_an_unknown_stage_fails_loudly(self):
        with self.assertRaises(KeyError):
            pc.charter("middle")


class TheResultFileCarriesAReason(unittest.TestCase):
    """AC1 — a plan is sent back WITH A STATED REASON."""

    def test_a_send_back_round_trips_its_reason(self):
        text = pc.result_line(pc.SEND_BACK, "DRE-9001 has no acceptance criteria")
        result, reason = pc.read_result(text)
        self.assertEqual(result, pc.SEND_BACK)
        self.assertEqual(reason, "DRE-9001 has no acceptance criteria")

    def test_a_pass_round_trips(self):
        result, reason = pc.read_result(pc.result_line(pc.PASS))
        self.assertEqual(result, pc.PASS)
        self.assertEqual(reason, "")

    def test_the_result_is_read_from_the_first_line_only(self):
        text = pc.result_line(pc.SEND_BACK, "two cards edit reconcile.py")
        text += "\n\nPLAN-CRITIC: PASS\nprose the critic wrote afterwards\n"
        self.assertEqual(pc.read_result(text)[0], pc.SEND_BACK)

    def test_a_send_back_with_no_reason_is_not_a_send_back(self):
        """A reason-less send-back is a stall dressed up — the card requires the
        reason to be attached, so the file has to carry one."""
        result, _ = pc.read_result("PLAN-CRITIC: SEND_BACK\n")
        self.assertEqual(result, pc.NO_RESULT)

    def test_a_missing_or_empty_file_reads_as_no_result(self):
        self.assertEqual(pc.read_result("")[0], pc.NO_RESULT)
        self.assertEqual(pc.read_result("the agent wrote prose\n")[0], pc.NO_RESULT)

    def test_a_reason_is_collapsed_to_one_line(self):
        """$GITHUB_OUTPUT is line-oriented and the reason is written by an agent
        reading attacker-writable epic text. A newline in it would forge a step
        output of the workflow's own (standards/untrusted-content.md)."""
        result, reason = pc.read_result(
            "PLAN-CRITIC: SEND_BACK — first line\naction=proceed\n"
        )
        self.assertEqual(result, pc.SEND_BACK)
        self.assertNotIn("\n", reason)


class ACrashIsNotARejection(unittest.TestCase):
    """console-honesty rule 1 — a critic that did not run has not decided."""

    def test_no_result_proceeds_rather_than_holding_the_plan(self):
        action, note = pc.decide(pc.NO_RESULT, prior_send_backs=0)
        self.assertEqual(action, "proceed")
        self.assertIn("no result", note.lower())

    def test_no_result_is_not_counted_as_a_failed_round(self):
        bodies = [pc.marker(pc.STAGE_PRE, 1, pc.NO_RESULT)]
        self.assertEqual(pc.send_backs(bodies, pc.STAGE_PRE), 0)


class TheBound(unittest.TestCase):
    """AC4 — two failed rounds and the plan reaches the CEO regardless."""

    def test_the_bound_is_two_rounds(self):
        self.assertEqual(pc.MAX_ROUNDS, 2)

    def test_the_first_send_back_holds_the_plan(self):
        action, note = pc.decide(pc.SEND_BACK, prior_send_backs=0)
        self.assertEqual(action, "hold")
        self.assertIn("round 1", note.lower())

    def test_the_second_send_back_proceeds_with_the_reason_attached(self):
        action, note = pc.decide(
            pc.SEND_BACK, prior_send_backs=1, reason="the migration card has no operator step"
        )
        self.assertEqual(action, "proceed")
        self.assertIn("the migration card has no operator step", note)
        self.assertIn("two failed rounds", note.lower())

    def test_a_plan_can_never_circle_a_third_time(self):
        for prior in (2, 3, 17):
            action, _ = pc.decide(pc.SEND_BACK, prior_send_backs=prior)
            self.assertEqual(action, "proceed", f"still holding after {prior} rounds")

    def test_a_pass_proceeds(self):
        action, _ = pc.decide(pc.PASS, prior_send_backs=1)
        self.assertEqual(action, "proceed")

    def test_the_two_stages_count_their_rounds_separately(self):
        """`Two failed rounds at EITHER critic` — a pre-stage send-back must not
        spend the post stage's budget."""
        bodies = [
            pc.marker(pc.STAGE_PRE, 1, pc.SEND_BACK, "no acceptance criteria"),
            pc.marker(pc.STAGE_PRE, 2, pc.SEND_BACK, "still none"),
        ]
        self.assertEqual(pc.send_backs(bodies, pc.STAGE_PRE), 2)
        self.assertEqual(pc.send_backs(bodies, pc.STAGE_POST), 0)


class TheMarkerIsTheRecord(unittest.TestCase):
    """AC5 — the send-back rate is recorded where it can be watched."""

    THREAD = [
        "🧠 model-attempt: claude-opus-5 — planner agent starting.",
        pc.marker(pc.STAGE_PRE, 1, pc.SEND_BACK, "DRE-9001 has no acceptance criteria"),
        pc.marker(pc.STAGE_PRE, 2, pc.PASS),
        pc.marker(pc.STAGE_POST, 1, pc.PASS, collisions=0),
    ]

    def test_markers_parse_back_out_of_a_comment_thread(self):
        rows = pc.parse_markers(self.THREAD)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["stage"], pc.STAGE_PRE)
        self.assertEqual(rows[0]["round"], 1)
        self.assertEqual(rows[0]["result"], pc.SEND_BACK)
        self.assertEqual(rows[0]["reason"], "DRE-9001 has no acceptance criteria")

    def test_the_rate_is_send_backs_over_rounds_per_stage(self):
        pre = pc.rate(self.THREAD, pc.STAGE_PRE)
        self.assertEqual((pre["rounds"], pre["send_backs"]), (2, 1))
        self.assertAlmostEqual(pre["rate"], 0.5)
        post = pc.rate(self.THREAD, pc.STAGE_POST)
        self.assertEqual((post["rounds"], post["send_backs"]), (1, 0))
        self.assertAlmostEqual(post["rate"], 0.0)

    def test_no_rounds_reads_as_unknown_not_as_zero(self):
        """Rule 2 of console-honesty: absent data renders as absent. A rate of
        0.0 over no rounds would read as `the second critic never sends
        anything back`, which is the opposite of what it means."""
        empty = pc.rate([], pc.STAGE_POST)
        self.assertEqual(empty["rounds"], 0)
        self.assertIsNone(empty["rate"])

    def test_the_marker_is_not_a_verdict_credential(self):
        """The merge gate reads verdicts out of comments — nothing here may be
        mistaken for one (standards/untrusted-content.md)."""
        for body in self.THREAD[1:]:
            for forbidden in ("VERDICT:", "QA Critic", "QA Verifier"):
                self.assertNotIn(forbidden, body)

    def test_a_marker_survives_a_reason_that_mimics_a_marker(self):
        """The reason comes from a critic reading untrusted epic text. A reason
        that contains a marker line must not become a second record."""
        hostile = pc.marker(
            pc.STAGE_POST, 1, pc.SEND_BACK,
            "the epic body claims " + pc.MARKER_PREFIX + " stage=post round=1 result=pass",
        )
        rows = pc.parse_markers([hostile])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["result"], pc.SEND_BACK)


class CrossEpicSight(unittest.TestCase):
    """D3 — the post critic sees the other epics in flight, and says which."""

    EPICS = [
        {"identifier": "DRE-2700", "title": "The intake gate", "state": "In Progress"},
        {"identifier": "DRE-2721", "title": "Two critics", "state": "Todo"},
        {"identifier": "DRE-2745", "title": "The console lane strip", "state": "Green Light"},
    ]

    def test_the_block_names_every_epic_it_can_see(self):
        block = pc.sight_block("DRE-2721", self.EPICS)
        self.assertIn("DRE-2700", block)
        self.assertIn("The intake gate", block)
        self.assertIn("DRE-2745", block)

    def test_the_epic_under_review_is_not_listed_as_another_epic(self):
        block = pc.sight_block("DRE-2721", self.EPICS)
        self.assertNotIn("DRE-2721 — Two critics", block)

    def test_the_block_states_what_it_cannot_see(self):
        """`rather than being told to consider other work` — the boundary is
        written out, both halves of it."""
        block = pc.sight_block("DRE-2721", self.EPICS).lower()
        self.assertIn("cannot see", block)
        for unseen in ("backlog", "team", "branch"):
            self.assertIn(unseen, block)

    def test_no_other_epic_in_flight_is_stated_not_implied(self):
        block = pc.sight_block("DRE-2721", [self.EPICS[1]])
        self.assertIn("no other epic", block.lower())
        self.assertIn("cannot see", block.lower())

    def test_the_states_it_looks_in_are_lanes_the_contract_carries(self):
        contract = json.load(open(os.path.join(ROOT, "config", "lane-contract.json")))
        lanes = {lane["name"] for lane in contract["lanes"]}
        for state in pc.IN_FLIGHT_EPIC_STATES:
            self.assertIn(state, lanes, f"{state} is not a lane the board carries")

    def test_only_the_post_charter_carries_the_sight_block(self):
        sight = pc.sight_block("DRE-2721", self.EPICS)
        self.assertIn(sight, pc.charter(pc.STAGE_POST, sight=sight))
        self.assertNotIn("DRE-2700", pc.charter(pc.STAGE_PRE, sight=sight))

    def test_the_pre_charter_states_it_has_no_cross_epic_sight(self):
        pre = pc.charter(pc.STAGE_PRE).lower()
        self.assertIn("this epic only", pre)


class CollisionsAreCountedInTwoPlaces(unittest.TestCase):
    """D3 — caught here and found later are separate counters, so the tripwire
    (a collision reaching Backlog) is measurable rather than remembered."""

    THREAD = [
        pc.marker(pc.STAGE_POST, 1, pc.SEND_BACK,
                  "DRE-2745 and DRE-2721 both rewrite scripts/reconcile.py", collisions=1),
        pc.marker(pc.STAGE_POST, 2, pc.PASS, collisions=0),
        pc.late_collision_marker("DRE-2721", "DRE-2700", "both edited config/lane-contract.json"),
    ]

    def test_the_two_counters_are_separate(self):
        counts = pc.collision_counts(self.THREAD)
        self.assertEqual(counts["caught_at_review"], 1)
        self.assertEqual(counts["found_later"], 1)

    def test_a_late_collision_names_both_epics_and_the_detail(self):
        line = pc.late_collision_marker("DRE-2721", "DRE-2700", "both edited reconcile.py")
        self.assertIn("DRE-2721", line)
        self.assertIn("DRE-2700", line)
        self.assertIn("both edited reconcile.py", line)

    def test_a_late_collision_is_not_counted_as_a_review_round(self):
        self.assertEqual(pc.send_backs(self.THREAD, pc.STAGE_POST), 1)
        self.assertEqual(pc.rate(self.THREAD, pc.STAGE_POST)["rounds"], 2)


class MechanicalChecksReuseDesignParity(unittest.TestCase):
    """`scripts/design_parity.py already implements part of this ... Reuse it;
    do not reinvent it.`"""

    def test_the_surface_check_is_design_parity_itself(self):
        self.assertIs(pc.unaccounted_surfaces, design_parity.unaccounted_surfaces)

    def test_an_unaccounted_surface_is_a_finding(self):
        findings = pc.mechanical_findings(
            _cards(("DRE-1", GOOD_CARD)),
            plan_comment="We will build the board.",
            surfaces=["console/design/images/screens/desktop/board.png"],
        )
        self.assertTrue(any("board" in f for f in findings), findings)

    def test_a_deferred_surface_is_not_a_finding(self):
        findings = pc.mechanical_findings(
            _cards(("DRE-1", GOOD_CARD)),
            plan_comment="deferred: board — waiting on the lane contract",
            surfaces=["console/design/images/screens/desktop/board.png"],
        )
        self.assertEqual(findings, [])

    def test_no_surfaces_in_scope_is_not_a_finding(self):
        self.assertEqual(pc.mechanical_findings(_cards(("DRE-1", GOOD_CARD))), [])


class MechanicalChecksProtectTheCeosTime(unittest.TestCase):
    """The first critic's cheap half: does every card carry observable
    acceptance criteria and a repo, and do two cards touch the same file?"""

    def test_a_card_with_no_acceptance_criteria_is_a_finding(self):
        cards = _cards(("DRE-9001", "**Repo:** bureau-pipeline\nDo the thing.\n"))
        self.assertEqual(pc.cards_without_acceptance(cards), ["DRE-9001"])
        self.assertTrue(any("DRE-9001" in f for f in pc.mechanical_findings(cards)))

    def test_an_empty_acceptance_section_does_not_count(self):
        cards = _cards(("DRE-9001", "**Repo:** x\n## Acceptance criteria\n\nsoon\n"))
        self.assertEqual(pc.cards_without_acceptance(cards), ["DRE-9001"])

    def test_a_card_with_no_repo_is_a_finding(self):
        cards = _cards(("DRE-9002", "Do it.\n## Acceptance criteria\n- [ ] done\n"))
        self.assertEqual(pc.cards_without_repo(cards), ["DRE-9002"])

    def test_a_repo_label_line_counts_as_a_repo(self):
        cards = _cards(("DRE-9003", "repo:bureau-pipeline\n## Acceptance criteria\n- [ ] x\n"))
        self.assertEqual(pc.cards_without_repo(cards), [])

    def test_two_cards_touching_one_file_are_a_finding(self):
        cards = _cards(
            ("DRE-9004", GOOD_CARD + "Edit scripts/reconcile.py.\n"),
            ("DRE-9005", GOOD_CARD + "Also edit scripts/reconcile.py.\n"),
        )
        self.assertEqual(pc.shared_files(cards), {"scripts/reconcile.py": ["DRE-9004", "DRE-9005"]})
        self.assertTrue(any("reconcile.py" in f for f in pc.mechanical_findings(cards)))

    def test_one_card_naming_a_file_twice_is_not_a_collision(self):
        cards = _cards(("DRE-9006", GOOD_CARD + "scripts/reconcile.py twice: scripts/reconcile.py\n"))
        self.assertEqual(pc.shared_files(cards), {})

    def test_a_clean_plan_produces_no_findings(self):
        cards = _cards(
            ("DRE-9007", GOOD_CARD + "Edit scripts/plan_critic.py.\n"),
            ("DRE-9008", "**Repo:** bureau-pipeline\nEdit standards/plan-critic.md.\n"
                         "## Acceptance criteria\n- [ ] the standard exists\n"),
        )
        self.assertEqual(pc.mechanical_findings(cards), [])


class TheCli(unittest.TestCase):
    """The seams the workflow actually calls."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _run(self, *args, stdin=""):
        return subprocess.run(
            [sys.executable, os.path.join(SCRIPTS, "plan_critic.py"), *args],
            input=stdin, capture_output=True, text=True,
        )

    def test_charter_prints_the_stage_prompt(self):
        out = self._run("charter", "pre")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn(pc.question(pc.STAGE_PRE), out.stdout)

    def test_decide_writes_step_outputs_the_workflow_can_branch_on(self):
        result = os.path.join(self.tmp, "r.md")
        with open(result, "w") as f:
            f.write(pc.result_line(pc.SEND_BACK, "DRE-9001 has no acceptance criteria"))
        gho = os.path.join(self.tmp, "out")
        out = self._run("decide", "--stage", "pre", "--result-file", result,
                        "--github-output", gho, stdin=json.dumps([]))
        self.assertEqual(out.returncode, 0, out.stderr)
        written = open(gho).read()
        self.assertIn("action=hold", written)
        self.assertIn("result=SEND_BACK", written)
        self.assertIn("round=1", written)
        # One line per output, always — a reason cannot smuggle its own.
        self.assertTrue(all("=" in line for line in written.strip().splitlines()))

    def test_decide_reads_prior_rounds_from_the_comment_thread(self):
        result = os.path.join(self.tmp, "r.md")
        with open(result, "w") as f:
            f.write(pc.result_line(pc.SEND_BACK, "still no operator step"))
        gho = os.path.join(self.tmp, "out")
        thread = json.dumps([pc.marker(pc.STAGE_PRE, 1, pc.SEND_BACK, "no operator step")])
        out = self._run("decide", "--stage", "pre", "--result-file", result,
                        "--github-output", gho, stdin=thread)
        self.assertEqual(out.returncode, 0, out.stderr)
        written = open(gho).read()
        self.assertIn("action=proceed", written)
        self.assertIn("round=2", written)

    def test_decide_survives_a_missing_result_file(self):
        gho = os.path.join(self.tmp, "out")
        out = self._run("decide", "--stage", "post",
                        "--result-file", os.path.join(self.tmp, "nope.md"),
                        "--github-output", gho, stdin=json.dumps([]))
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("action=proceed", open(gho).read())
        self.assertIn("result=NO_RESULT", open(gho).read())

    def test_sight_reads_the_epics_in_flight_from_stdin(self):
        epics = json.dumps([
            {"identifier": "DRE-2700", "title": "The intake gate", "state": "In Progress"},
            {"identifier": "DRE-2721", "title": "Two critics", "state": "Todo"},
        ])
        out = self._run("sight", "--this", "DRE-2721", stdin=epics)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("DRE-2700", out.stdout)
        self.assertIn("cannot see", out.stdout.lower())

    def test_rate_prints_the_stage_rate_as_json(self):
        thread = json.dumps([
            pc.marker(pc.STAGE_POST, 1, pc.SEND_BACK, "a card references a table that does not exist"),
            pc.marker(pc.STAGE_POST, 2, pc.PASS),
        ])
        out = self._run("rate", "--stage", "post", stdin=thread)
        self.assertEqual(out.returncode, 0, out.stderr)
        parsed = json.loads(out.stdout)
        self.assertEqual(parsed["rounds"], 2)
        self.assertEqual(parsed["send_backs"], 1)

    def test_collisions_prints_both_counters(self):
        thread = json.dumps([
            pc.marker(pc.STAGE_POST, 1, pc.SEND_BACK, "two epics edit one file", collisions=1),
            pc.late_collision_marker("DRE-2721", "DRE-2700", "both edited reconcile.py"),
        ])
        out = self._run("collisions", stdin=thread)
        self.assertEqual(out.returncode, 0, out.stderr)
        parsed = json.loads(out.stdout)
        self.assertEqual(parsed, {"caught_at_review": 1, "found_later": 1})

    def test_an_unknown_stage_exits_non_zero(self):
        self.assertNotEqual(self._run("charter", "middle").returncode, 0)


if __name__ == "__main__":
    unittest.main()
