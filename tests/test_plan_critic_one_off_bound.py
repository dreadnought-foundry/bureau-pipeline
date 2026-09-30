"""The one-off route has a bound, and the loop it bounds runs through the CEO
(DRE-4058).

`MAX_ROUNDS = 2` is described in `plan_critic.py` as "the bound, on both loops",
and the epic route enforces it. The one-off route did not consult it, and the
module said so in its own words at the head of the one-off exit:

    There is no bound here either, because there is no loop to bound: one call
    per one-off classification, and the card either moves or parks.

THERE IS A LOOP. It runs through the CEO. The card parks in `Green Light`, he
answers and moves it back to `Planning`, and that is a NEW one-off
classification — a new unbounded single call, which finds the next problem and
parks it again. `one_off_decide(result, reason)` took no round count, so the
`round=N` in the posted marker was cosmetic on this route.

DRE-3879 is what that cost: five rounds, five different findings, two signed
console answers between them, rounds 4 and 5 six minutes apart on 2026-09-15 PT
and both after the CEO's 13:12 PT answer. Four of the five findings were real
and new — the critic is not inventing problems; it reports one headline finding
per pass and charges a full CEO round trip for each. DRE-3880 is the same shape
at three rounds. The real thread is
`tests/fixtures/dre-3879-one-off-rounds-2026-09-15.json` and every finding
asserted below is read out of it rather than invented here.

What these pin, in the order the card asks for them:

  * the count was ALREADY persisted and already computable — `send_backs` reads
    5 out of that thread — and the route simply never asked for it;
  * at or beyond the bound the one-off decision is a PARK distinct from the
    ordinary escalate, so the card stops being re-asked — in Triage, the
    operator's defect queue, since DRE-5376, because a revision loop that
    never converges is a defect and not a decision;
  * the bound is `MAX_ROUNDS` read from the one definition, so moving that
    constant moves this route with it;
  * what the card says at the bound names every finding raised so far, so
    one rewrite answers all of them;
  * below the bound a PASS proceeds, and a send-back goes back to the
    PLANNER to revise (DRE-5376) — never to the CEO, who cannot rewrite it;
  * a crash still escalates and still spends nothing, the epic route's rule;
  * the round number the marker POSTS is the number the bound READS, so the
    count can never quietly become cosmetic again.

Run: cd bureau-pipeline && python3 -m pytest tests/test_plan_critic_one_off_bound.py -v
"""

from __future__ import annotations

import inspect
import json
import os
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = os.path.join(os.path.dirname(__file__), "..")
SCRIPTS = os.path.join(ROOT, "scripts")
FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
WF = os.path.join(ROOT, ".github", "workflows", "plan.yml")

sys.path.insert(0, SCRIPTS)

import plan_critic as pc  # noqa: E402
import planning_escalation  # noqa: E402

CARD = "DRE-3879"


def thread() -> list[dict]:
    """DRE-3879's thread as `dump-comments --with-authors` hands it over —
    records, not strings, so `trusted_bodies` is exercised rather than bypassed.
    """
    with open(os.path.join(FIXTURES, "dre-3879-one-off-rounds-2026-09-15.json"),
              encoding="utf-8") as f:
        return json.load(f)["comments"]


def recorded_findings() -> list[str]:
    """The five findings DRE-3879's markers recorded, oldest first."""
    return [r["reason"] for r in pc.parse_markers(thread())
            if r["stage"] == pc.STAGE_ONE_OFF and r["result"] == pc.SEND_BACK]


class TheCountWasAlreadyThere(unittest.TestCase):
    """The defect was never a missing record. It was a record nobody read."""

    def test_the_markers_on_the_real_card_count_five_failed_rounds(self):
        counted = pc.send_backs(thread(), pc.STAGE_ONE_OFF)
        self.assertEqual(5, counted)
        self.assertGreater(counted, pc.MAX_ROUNDS,
                           "DRE-3879 ran past the bound the module declares")

    def test_the_five_findings_are_recoverable_from_the_markers(self):
        """The rewrite note below has to name every finding raised so far, and
        this is where they come from — the persisted markers, read by the same
        parser the count is read by. No second source and no second parser."""
        found = recorded_findings()
        self.assertEqual(5, len(found))
        for fragment in ("two different fixes",
                         "doesn't show up anywhere we can actually verify",
                         "trusted-branch list",
                         "which two branches",
                         "write the test before the fix"):
            self.assertTrue(any(fragment in f for f in found),
                            f"no recorded finding names {fragment!r}")

    def test_a_prose_note_quoting_its_findings_is_not_a_round(self):
        """The round-5 critic note in that thread lists four findings in prose.
        `_sole_record` is why it counts as nothing: five markers, five rounds,
        whatever else the thread says."""
        self.assertEqual(5, len(pc.parse_markers(thread())))

    def test_nothing_hands_a_one_off_a_fresh_budget(self):
        """The count is the CARD's whole history because no boundary is ever
        posted on this route — `cycle-start` belongs to a planning attempt and a
        one-off has none. A step that started posting one here would refund the
        budget on every run, which is this defect again wearing DRE-2721's
        clothes."""
        self.assertEqual(len(pc.trusted_bodies(thread())),
                         len(pc.current_cycle(thread(), CARD)))
        doc = yaml.safe_load(open(WF, encoding="utf-8").read())
        for job in doc["jobs"].values():
            for step in job.get("steps") or []:
                run = str(step.get("run") or "")
                if "cycle-start" not in run:
                    continue
                self.assertNotIn("one-off", str(step.get("if") or ""),
                                 f"{step.get('name')!r} opens a cycle on the "
                                 "one-off route")


class TheOneOffDecisionLearnsTheCount(unittest.TestCase):
    """AC1. `one_off_decide` could not see how many times the card had already
    been sent back: it had no parameter for one. That is the defect at its
    root."""

    def test_it_takes_the_count_of_prior_send_backs(self):
        params = inspect.signature(pc.one_off_decide).parameters
        self.assertTrue(
            any("send_back" in name or "round" in name for name in params),
            f"one_off_decide{inspect.signature(pc.one_off_decide)} cannot be "
            "told how many rounds this card has already spent")

    def test_at_the_bound_it_parks_rather_than_escalating_again(self):
        action, note = pc.one_off_decide(
            pc.SEND_BACK, recorded_findings()[-1],
            prior_send_backs=pc.MAX_ROUNDS - 1)
        self.assertEqual(pc.PARK, action)
        self.assertNotEqual(pc.ESCALATE, action)
        self.assertNotEqual(pc.REVISE, action)
        self.assertNotEqual(pc.PROCEED, action)
        self.assertTrue(note)

    def test_past_the_bound_it_still_parks(self):
        """DRE-3879's own position: five rounds spent, and a sixth send-back
        must not read as "the first one"."""
        action, _note = pc.one_off_decide(
            pc.SEND_BACK, recorded_findings()[-1],
            prior_send_backs=pc.send_backs(thread(), pc.STAGE_ONE_OFF))
        self.assertEqual(pc.PARK, action)

    def test_the_answer_is_not_constant_across_rounds(self):
        """The whole defect in one assertion: ten rounds used to answer
        `escalate` ten times, so nothing could ever end the loop. Below the
        bound a send-back is the planner's to revise (DRE-5376)."""
        answers = {n: pc.one_off_decide(pc.SEND_BACK, f"finding number {n}",
                                        prior_send_backs=n - 1)[0]
                   for n in range(1, 11)}
        self.assertGreater(len(set(answers.values())), 1, answers)
        for n, action in answers.items():
            expected = pc.REVISE if n < pc.MAX_ROUNDS else pc.PARK
            self.assertEqual(expected, action, f"round {n}")

    def test_the_park_is_its_own_action_and_not_a_renamed_one(self):
        self.assertNotIn(pc.PARK, (pc.PROCEED, pc.ESCALATE, pc.REVISE))


class TheBoundIsTheOneDefinition(unittest.TestCase):
    """AC2. `MAX_ROUNDS`, read from the one place it is declared — never a
    literal `2` on this route. Moving the constant has to move the route, which
    is the only test a "no second literal" claim can be given."""

    def setUp(self):
        self.declared = pc.MAX_ROUNDS

    def tearDown(self):
        pc.MAX_ROUNDS = self.declared

    def _action(self, prior: int) -> str:
        return pc.one_off_decide(pc.SEND_BACK, "a finding",
                                 prior_send_backs=prior)[0]

    def test_the_boundary_moves_with_the_constant(self):
        pc.MAX_ROUNDS = self.declared + 2
        self.assertEqual(pc.REVISE, self._action(self.declared - 1))
        self.assertEqual(pc.REVISE, self._action(self.declared))
        self.assertEqual(pc.PARK, self._action(self.declared + 1))

    def test_a_bound_of_one_parks_on_the_first_send_back(self):
        pc.MAX_ROUNDS = 1
        self.assertEqual(pc.PARK, self._action(0))

    def test_both_routes_spend_the_bound_on_the_same_arithmetic(self):
        """The epic route holds when `prior + 1` reaches MAX_ROUNDS; this route
        parks on the same count, so the two cannot drift."""
        for prior in range(0, self.declared + 3):
            epic_at_bound = pc.at_bound(
                pc.decide(pc.SEND_BACK, prior, "a finding",
                          stage=pc.STAGE_POST)[0], prior, pc.SEND_BACK)
            self.assertEqual(epic_at_bound,
                             self._action(prior) == pc.PARK,
                             f"prior={prior}")


class WhatTheCardSaysAtTheBound(unittest.TestCase):
    """AC3, as DRE-5376 re-routed it. The bound is no longer a question for the
    CEO — he can approve or park a card and cannot rewrite one — so the card
    parks in Triage for the operator, and every finding raised so far is named
    so one rewrite can answer all of them."""

    def test_the_note_names_the_lane_and_not_the_ceos_queue(self):
        _action, note = pc.one_off_decide(
            pc.SEND_BACK, recorded_findings()[-1],
            prior_send_backs=pc.MAX_ROUNDS - 1)
        self.assertIn(pc.BOUND_PARK_LANE, note)
        self.assertNotIn("Green Light", note)

    def test_it_says_how_many_times_the_card_came_back(self):
        _action, note = pc.one_off_decide(
            pc.SEND_BACK, "a finding", prior_send_backs=pc.MAX_ROUNDS - 1)
        self.assertIn(pc._count_word(pc.MAX_ROUNDS), note)

    def test_a_finding_the_critic_re_raises_is_listed_once(self):
        """DRE-3879's round 5 headline IS its round-5 marker reason, verbatim —
        the critic naming again what it already recorded is the normal case, and
        a list that says it twice reads as two open problems."""
        prior, this_round = recorded_findings()[:2], recorded_findings()[1:3]
        self.assertEqual(recorded_findings()[:3],
                         pc.every_finding_so_far(prior, this_round))

    def test_nothing_at_the_bound_is_handed_to_the_ceo(self):
        """No Green Light reason at all: the escalation text is asked only for
        an `escalate`, and the bound is not one."""
        self.assertNotEqual(pc.ESCALATE, pc.one_off_decide(
            pc.SEND_BACK, "a finding", prior_send_backs=pc.MAX_ROUNDS)[0])


class BelowTheBoundNothingMoved(unittest.TestCase):
    """AC4. The first send-back goes back to the planner (DRE-5376), and a
    pass is still a pass at any count."""

    def test_the_first_send_back_is_revised_by_the_planner(self):
        action, note = pc.one_off_decide(pc.SEND_BACK, recorded_findings()[0],
                                        prior_send_backs=0)
        self.assertEqual(pc.REVISE, action)
        self.assertIn("planner", note.lower())

    def test_a_pass_still_moves_the_card_however_many_rounds_it_cost(self):
        for prior in (0, pc.MAX_ROUNDS, 9):
            action, note = pc.one_off_decide(pc.PASS, prior_send_backs=prior)
            self.assertEqual(pc.PROCEED, action, prior)
            self.assertTrue(note)

    def test_the_default_is_the_first_round(self):
        """Callers that hand it no count are reading a first round: the count
        is an addition, not a new obligation."""
        self.assertEqual(pc.REVISE,
                         pc.one_off_decide(pc.SEND_BACK, "a finding")[0])
        self.assertEqual(pc.PROCEED, pc.one_off_decide(pc.PASS)[0])

    def test_only_a_pass_ever_moves_a_card(self):
        moved = {(r, prior)
                 for r in (pc.PASS, pc.SEND_BACK, pc.NO_RESULT, "WHATEVER")
                 for prior in (0, pc.MAX_ROUNDS, 5)
                 if pc.one_off_decide(r, "a finding",
                                      prior_send_backs=prior)[0] == pc.PROCEED}
        self.assertEqual({(pc.PASS, 0), (pc.PASS, pc.MAX_ROUNDS), (pc.PASS, 5)},
                         moved)


class ARoundTheCriticNeverDecidedIsNotAFailedRound(unittest.TestCase):
    """AC5. The epic route's rule, expressed the same way: a crash is not a
    rejection, so it escalates and spends nothing."""

    def test_a_crash_at_the_bound_still_escalates_rather_than_parking(self):
        for result in (pc.NO_RESULT, "", "MAYBE"):
            action, note = pc.one_off_decide(
                result, prior_send_backs=pc.MAX_ROUNDS + 3)
            self.assertEqual(pc.ESCALATE, action, result)
            self.assertEqual(pc.NO_CRITIC_NOTE, note, result)

    def test_a_reason_less_send_back_is_a_crash_and_never_a_park(self):
        """`read_result` already reads one as NO_RESULT; the decision agrees,
        so a critic that wrote a bare header cannot spend the CEO's budget."""
        action, _note = pc.one_off_decide(pc.SEND_BACK, "",
                                          prior_send_backs=pc.MAX_ROUNDS)
        self.assertEqual(pc.ESCALATE, action)
        self.assertEqual(pc.NO_RESULT, pc.read_result(
            pc.result_line(pc.SEND_BACK))[0])

    def test_a_crash_round_is_not_counted_by_the_reader_either(self):
        crashes = [pc.marker(pc.STAGE_ONE_OFF, n, pc.NO_RESULT)
                   for n in (1, 2, 3)]
        self.assertEqual(0, pc.send_backs(crashes, pc.STAGE_ONE_OFF))

    def test_the_epic_route_still_proceeds_on_a_crash(self):
        self.assertEqual("proceed", pc.decide(pc.NO_RESULT, 0)[0])


class ThePostedRoundIsTheNumberTheBoundReads(unittest.TestCase):
    """AC7, and the reason this file exists rather than a patch: the marker's
    `round=N` was cosmetic on this route. The CLI is run over DRE-3879's real
    thread and the number it POSTS is read back by the same reader the bound
    uses."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _decide(self, records, result_text) -> dict:
        paths = {name: os.path.join(self.tmp, f"{name}.txt")
                 for name in ("result", "note", "record", "escalation", "out")}
        with open(paths["result"], "w", encoding="utf-8") as f:
            f.write(result_text)
        out = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS, "plan_critic.py"), "decide",
             "--stage", pc.STAGE_ONE_OFF, "--epic", CARD,
             "--result-file", paths["result"],
             "--github-output", paths["out"],
             "--note-file", paths["note"],
             "--record-file", paths["record"],
             "--escalation-file", paths["escalation"]],
            input=json.dumps(records), capture_output=True, text=True)
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

    def test_the_round_it_posts_is_the_round_the_bound_reads(self):
        """DRE-3879's history is send-backs only, so the round number and the
        spent budget are the same number — and the marker the run writes is fed
        straight back to `send_backs` to prove it, rather than compared against
        a number this test typed."""
        records = thread()
        run = self._decide(records, pc.result_line(
            pc.SEND_BACK, "a sixth finding nobody has answered yet"))
        posted = int(run["outputs"]["round"])
        self.assertEqual(pc.send_backs(records, pc.STAGE_ONE_OFF) + 1, posted)
        after = records + [{"body": run["record"].strip(),
                            "authored_by_pipeline": True}]
        self.assertEqual(posted, pc.send_backs(after, pc.STAGE_ONE_OFF))

    def test_the_sixth_round_parks_in_triage(self):
        run = self._decide(thread(), pc.result_line(
            pc.SEND_BACK, "a sixth finding nobody has answered yet"))
        self.assertEqual(pc.PARK, run["outputs"]["action"])
        self.assertEqual("true", run["outputs"]["bound"])
        self.assertEqual("", run["escalation"],
                         "the bound wrote a reason for the CEO's queue")
        self.assertIn(pc.BOUND_PARK_LANE, run["note"])
        self.assertIn(pc.SEND_BACK, run["record"])

    def test_the_note_on_the_card_carries_every_finding_so_far(self):
        run = self._decide(thread(), pc.result_line(
            pc.SEND_BACK, "a sixth finding nobody has answered yet"))
        for finding in recorded_findings():
            self.assertIn(finding, run["note"])

    def test_the_first_round_is_revised_end_to_end(self):
        run = self._decide([], pc.result_line(
            pc.SEND_BACK, recorded_findings()[0]))
        self.assertEqual(pc.REVISE, run["outputs"]["action"])
        self.assertEqual("1", run["outputs"]["round"])
        self.assertEqual("", run["escalation"])

    def test_a_crash_advances_the_round_number_and_not_the_bound(self):
        """The two numbers are different questions and the run says so: three
        dead rounds later the next send-back is still the FIRST failed one."""
        crashes = [{"body": pc.marker(pc.STAGE_ONE_OFF, n, pc.NO_RESULT),
                    "authored_by_pipeline": True} for n in (1, 2, 3)]
        run = self._decide(crashes, pc.result_line(
            pc.SEND_BACK, recorded_findings()[0]))
        self.assertEqual("4", run["outputs"]["round"])
        self.assertEqual(pc.REVISE, run["outputs"]["action"])
        self.assertEqual("false", run["outputs"]["bound"])

    def test_a_pass_after_a_spent_bound_still_reaches_the_build_queue(self):
        """The recovery path: the CEO rewrites the card, the critic reads it
        again and passes it. Nothing about the spent budget blocks a card that
        is now good — which is why this route needs no cycle boundary."""
        run = self._decide(thread(), pc.result_line(
            pc.PASS, "one pull request, and the decisions are all made on it"))
        self.assertEqual(pc.PROCEED, run["outputs"]["action"])
        self.assertEqual("", run["escalation"])


class TheRailCarriesThePark(unittest.TestCase):
    """A park nothing acts on is a stranded card. Every action this route can
    answer has to reach a step in plan.yml: the question parks through
    DRE-2848's one seam, and the bound parks in Triage (DRE-5376)."""

    def _steps(self) -> list[dict]:
        doc = yaml.safe_load(open(WF, encoding="utf-8").read())
        return [s for job in doc["jobs"].values() for s in job.get("steps") or []]

    def _step_named(self, fragment: str) -> dict:
        for step in self._steps():
            if fragment.lower() in str(step.get("name") or "").lower():
                return step
        raise AssertionError(f"no step named like {fragment!r}")

    def test_every_action_that_does_not_move_the_card_reaches_a_step(self):
        gates = {pc.ESCALATE: "One-off critic — escalate",
                 pc.PARK: "One-off critic — park in Triage"}
        for action, name in gates.items():
            gate = str(self._step_named(name).get("if") or "")
            self.assertIn(f"== '{action}'", gate,
                          f"nothing in plan.yml acts on {action!r}")

    def test_the_question_still_uses_the_one_escalation_seam(self):
        step = self._step_named("One-off critic — escalate")
        self.assertIn("planning_escalation.py escalate", str(step.get("run")))
        self.assertIn("--reason-file", str(step.get("run")))

    def test_the_move_step_is_still_gated_on_a_pass_alone(self):
        gate = str(self._step_named("One-off route — checked on the way out")
                   .get("if") or "")
        self.assertIn(f"steps.oneoff.outputs.action == '{pc.PROCEED}'", gate)
        self.assertNotIn(pc.PARK, gate)
        self.assertNotIn(pc.REVISE, gate)


class TheEscalationSeamKeepsItsRewriteNote(unittest.TestCase):
    """`planning_escalation.escalate(rewrite=True)` is the seam's own
    capability and is unchanged — DRE-5376 only stopped the one-off route
    reaching it, because the bound now parks in Triage. What the seam says
    when it IS asked for a rewrite is still pinned here."""

    REASON = ("This card has been round-tripped two separate times, and the "
              "card itself has to be rewritten.")

    def _parked(self) -> str:
        return planning_escalation.escalation_comment(
            CARD, self.REASON, rewrite=True)

    def test_it_does_not_tell_him_to_answer_a_card_it_just_said_to_rewrite(self):
        text = self._parked()
        self.assertNotIn("Answer it here", text)
        self.assertNotIn("waiting on judgement", text)
        self.assertNotIn("the reasoning itself is the deliverable", text)

    def test_it_opens_with_its_own_mark(self):
        self.assertTrue(self._parked().startswith(
            planning_escalation.REWRITE_MARK))
        self.assertNotEqual(planning_escalation.REWRITE_MARK,
                            planning_escalation.ESCALATION_MARK)

    def test_the_question_park_is_word_for_word_what_it_always_was(self):
        asked = planning_escalation.escalation_comment(CARD, self.REASON)
        self.assertIn("the reasoning itself is the deliverable", asked)
        self.assertIn("it is correct and waiting on judgement", asked)
        self.assertIn("Answer it here and move the card back", asked)
        self.assertTrue(asked.startswith(planning_escalation.ESCALATION_MARK))

    def test_the_seam_that_parks_the_card_carries_the_flag(self):
        posted: list[str] = []
        lops = _Lops(posted)
        outcome = planning_escalation.escalate(
            lops, CARD, self.REASON, rewrite=True)
        self.assertTrue(outcome.parked)
        self.assertEqual(1, len(posted))
        self.assertNotIn("Answer it here", posted[0])
        self.assertEqual([(CARD, planning_escalation.destination())],
                         lops.states)

    def test_no_step_in_the_workflow_asks_for_it(self):
        doc = yaml.safe_load(open(WF, encoding="utf-8").read())
        for job in doc["jobs"].values():
            for step in job.get("steps") or []:
                self.assertNotIn("--rewrite", str(step.get("run") or ""),
                                 step.get("name"))


class _Lops:
    """Enough of `linear_ops` for `escalate()`: a card still in Planning, with
    no prior escalation and no routing verdict, so the park is the live path."""

    def __init__(self, posted: list[str]):
        self.posted = posted
        self.states: list[tuple[str, str]] = []

    def get_issue(self, identifier, fresh=False):
        return {"state": {"name": planning_escalation.ORIGIN}}

    def comment_bodies(self, identifier):
        return []

    def comment_timeline(self, identifier):
        """The read `escalate()` actually makes when no caller hands it
        comments (DRE-4124): the thread WITH each comment's `createdAt`, so a
        verdict from this planning attempt can be told from a spent one."""
        return []

    def count_comments(self, identifier, tag):
        return 0

    def cmd_comment(self, identifier, body):
        self.posted.append(body)

    def cmd_state(self, identifier, lane):
        self.states.append((identifier, lane))


class TheStandardSaysTheRouteIsBounded(unittest.TestCase):
    """The standard is what a headless critic and a future author read. A
    module that bounds a loop the standard still calls unbounded is a document
    that will be believed over the code."""

    def _standard(self) -> str:
        with open(os.path.join(ROOT, "standards", "plan-critic.md"),
                  encoding="utf-8") as f:
            return f.read()

    def test_it_names_the_bound_on_the_one_off_route(self):
        text = self._standard()
        self.assertIn("round trip", text.lower())
        self.assertIn("DRE-5376", text)
        self.assertIn(pc.BOUND_PARK_LANE, text)

    def test_the_module_no_longer_claims_there_is_no_loop(self):
        with open(os.path.join(SCRIPTS, "plan_critic.py"), encoding="utf-8") as f:
            source = f.read()
        for stale in ("There is no bound here either",
                      "No bound and no loop on this route",
                      "The one-off route has no round bound"):
            self.assertNotIn(stale, source)


if __name__ == "__main__":
    unittest.main()
