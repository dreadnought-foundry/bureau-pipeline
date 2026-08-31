"""Progressive commitment, wired into the rail (DRE-2846).

`scripts/wave_commitment.py` is only worth anything if the run that writes a
wave plan records the commitment, the sweep reads that record, and the standard
the CEO and the planner are held to says what the record means. These tests pin
the rail:

  1. THE RUN — plan.yml records the commitment from the same wave plan it just
     checked, and every step is gated on the wave shape.
  2. THE SWEEP READS IT — reconcile imports the module, refuses a
     committed-in-sequence card in `promote_ready`, and sends an epic whose
     turn has come to the lane that owes a plan artifact rather than onward
     into the build path.
  3. THE STANDARD — `standards/wave-plan.md` states that a wave's approval is
     the shape and the order and nothing more, names the recorded state, and
     says a reorder or a drop needs no re-approval. Anything the standard
     stops saying is a rule the pipeline enforces from memory.
  4. NO SECOND VOCABULARY — the lanes this module uses are derived from the
     lane contract and the shape vocabulary, never typed in.
"""

import os
import re
import sys
import unittest

import yaml

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
WF = os.path.join(REPO, ".github", "workflows", "plan.yml")
SCRIPTS = os.path.join(REPO, "scripts")
STANDARD = os.path.join(REPO, "standards", "wave-plan.md")
sys.path.insert(0, SCRIPTS)
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import lane_contract  # noqa: E402
import reconcile  # noqa: E402
import wave_commitment as wc  # noqa: E402

PLAN_PATH = "wave-plan.md"


def wf_steps() -> list:
    doc = yaml.safe_load(open(WF, encoding="utf-8").read())
    return [s for job in doc["jobs"].values() for s in job.get("steps") or []]


def step_named(fragment: str) -> dict:
    for s in wf_steps():
        if fragment.lower() in (s.get("name") or "").lower():
            return s
    raise AssertionError(
        f"no step whose name contains {fragment!r}; have: "
        + ", ".join(repr(s.get("name")) for s in wf_steps()))


def body_of(step: dict) -> str:
    return str(step.get("run") or (step.get("with") or {}).get("prompt") or "")


class TheRunRecordsTheCommitmentTest(unittest.TestCase):
    def test_the_run_records_the_commitment_from_the_plan_it_checked(self):
        step = step_named("record the commitment")
        self.assertIn("wave_commitment.py record", body_of(step))
        self.assertIn(PLAN_PATH, body_of(step),
                      "the commitment is recorded from the plan this run "
                      "wrote, never from a file nothing produced")

    def test_it_is_gated_on_the_wave_shape(self):
        self.assertIn("route == 'wave'",
                      str(step_named("record the commitment").get("if") or ""),
                      "recording a wave's commitment on a one-off would "
                      "invent a wave nobody planned")

    def test_it_runs_after_the_check_not_before(self):
        names = [(s.get("name") or "") for s in wf_steps()]
        check = next(i for i, n in enumerate(names) if "wave plan — check" in n.lower())
        record = next(i for i, n in enumerate(names) if "record the commitment" in n.lower())
        self.assertGreater(record, check,
                           "a commitment recorded from an unchecked plan "
                           "commits the wave to epics the gate would refuse")

    def test_the_turn_lands_the_epic_where_the_run_parks_it_for_the_ceo(self):
        """The other half of the turn, closed mechanically: the lane a turn
        sends an epic to is the one plan.yml runs the planner in, and that run
        ends by parking the epic in the decision lane on its fresh artifact.
        Without this the claim 'it arrives in Green Light on its own' would be
        prose."""
        self.assertIn(wc.decision_lane(), body_of(step_named("Epic → Green Light")))
        route = body_of(step_named("Route — plan or activate"))
        self.assertIn(wc.turn_lane(), route,
                      "the planner run's own plan route puts the epic in the "
                      "lane a turn sends it to")

    def test_the_announcement_still_says_nothing_is_approved(self):
        body = body_of(step_named("wave plan — announce"))
        self.assertIn("own", body.lower())
        self.assertTrue(
            re.search(r"nothing is approved|not(hing)? green.?lit", body, re.I),
            "the run must not let a wave plan read as an approval of its epics",
        )


class TheSweepReadsTheRecordTest(unittest.TestCase):
    def test_reconcile_imports_the_module(self):
        self.assertIs(reconcile.wave_commitment, wc)

    def test_promote_ready_consults_the_commitment(self):
        src = open(os.path.join(SCRIPTS, "reconcile.py"), encoding="utf-8").read()
        promote = src.split("def promote_ready", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("wave_commitment.promotion_refusal", promote)

    def test_the_turn_goes_to_the_artifact_lane_not_onward(self):
        src = open(os.path.join(SCRIPTS, "reconcile.py"), encoding="utf-8").read()
        advance = src.split("def advance_unblocked_epics", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("wave_commitment.turn_arrival", advance)

    def test_a_committed_epic_reaching_its_turn_goes_to_the_artifact_lane(self):
        """The sweep, run for real. Its predecessor is Done and its turn has
        come — and it goes to the lane that owes a plan artifact, never onward
        into the build path and never to Triage, which is the broken-card lane
        and not a turn."""
        from unittest.mock import patch

        record = wc.commitment_comment(
            "DRE-2719",
            {"key": "route", "title": "The wave route", "depends_on": ["standard"],
             "status": wc.COMMITTED},
            position=2, total=3)
        answers = [
            {"issue": {"relations": {"nodes": [
                {"type": "blocks", "issue": {"identifier": "DRE-2901"}}]}}},
            {"issue": {"id": "u", "identifier": "DRE-2901", "title": "route",
                       "description": "x",
                       "labels": {"nodes": [{"name": "agent:planner"}]},
                       "children": {"nodes": []}}},
        ]
        with patch.object(reconcile.linear_ops, "gql", side_effect=answers), \
            patch.object(reconcile.linear_ops, "comment_bodies", return_value=[record]), \
            patch.object(reconcile.mid_epic, "last_green_light", return_value=None), \
            patch.object(reconcile, "card_state", return_value="Backlog"), \
            patch.object(reconcile, "epic_blockers_unmet", return_value=False), \
            patch.object(reconcile, "redispatch", return_value=True) as dispatch, \
            patch.object(reconcile.linear_ops, "cmd_advance") as advance, \
            patch.object(reconcile.linear_ops, "cmd_comment") as comment:
            reconcile.advance_unblocked_epics("DRE-2900")
        advance.assert_called_once_with("DRE-2901", wc.turn_lane(), "Backlog")
        self.assertIn("plan artifact", comment.mock_calls[0].args[1].lower())
        dispatch.assert_called_once()

    def test_the_turn_starts_the_planner_rather_than_relying_on_a_lane(self):
        """Nothing dispatches off the lane that owes a plan artifact — Triage
        happened to, which is the only reason the old path used it. The turn
        asks for the run, and says honestly when it did not start."""
        from unittest.mock import patch

        answers = [
            {"issue": {"relations": {"nodes": [
                {"type": "blocks", "issue": {"identifier": "DRE-2901"}}]}}},
            {"issue": {"id": "u", "identifier": "DRE-2901", "title": "route",
                       "description": "x",
                       "labels": {"nodes": [{"name": "agent:planner"}]},
                       "children": {"nodes": []}}},
        ]
        record = wc.commitment_comment(
            "DRE-2719",
            {"key": "route", "title": "The wave route", "depends_on": ["standard"],
             "status": wc.COMMITTED},
            position=2, total=3)
        with patch.object(reconcile.linear_ops, "gql", side_effect=answers), \
            patch.object(reconcile.linear_ops, "comment_bodies", return_value=[record]), \
            patch.object(reconcile.mid_epic, "last_green_light", return_value=None), \
            patch.object(reconcile, "card_state", return_value="Backlog"), \
            patch.object(reconcile, "epic_blockers_unmet", return_value=False), \
            patch.object(reconcile, "redispatch", return_value=False), \
            patch.object(reconcile.linear_ops, "cmd_advance"), \
            patch.object(reconcile.linear_ops, "cmd_comment") as comment:
            reconcile.advance_unblocked_epics("DRE-2900")
        self.assertIn("could NOT be started", comment.mock_calls[0].args[1])

    def test_an_epic_that_already_has_a_plan_is_not_re_dispatched(self):
        """`plan.yml` routes an epic with children to ACTIVATE — which
        green-lights it and promotes its children. That is the blank cheque
        this card removes, so the turn never asks for that run."""
        from unittest.mock import patch

        answers = [
            {"issue": {"relations": {"nodes": [
                {"type": "blocks", "issue": {"identifier": "DRE-2901"}}]}}},
            {"issue": {"id": "u", "identifier": "DRE-2901", "title": "route",
                       "description": "x",
                       "labels": {"nodes": [{"name": "agent:planner"}]},
                       "children": {"nodes": [{"identifier": "DRE-2950"}]}}},
        ]
        record = wc.commitment_comment(
            "DRE-2719",
            {"key": "route", "title": "The wave route", "depends_on": ["standard"],
             "status": wc.COMMITTED},
            position=2, total=3)
        with patch.object(reconcile.linear_ops, "gql", side_effect=answers), \
            patch.object(reconcile.linear_ops, "comment_bodies", return_value=[record]), \
            patch.object(reconcile.mid_epic, "last_green_light", return_value=None), \
            patch.object(reconcile, "card_state", return_value="Backlog"), \
            patch.object(reconcile, "epic_blockers_unmet", return_value=False), \
            patch.object(reconcile, "redispatch") as dispatch, \
            patch.object(reconcile.linear_ops, "cmd_advance"), \
            patch.object(reconcile.linear_ops, "cmd_comment"):
            reconcile.advance_unblocked_epics("DRE-2900")
        dispatch.assert_not_called()

    def test_the_sweep_never_sends_a_committed_epic_to_an_active_lane(self):
        """Its turn is a turn to be PLANNED, not a turn to be built: an
        active lane is the green light, and the wave's approval was not it."""
        arrival = wc.turn_arrival(
            "DRE-2900",
            [wc.commitment_comment("DRE-2719",
                                   {"key": "route", "title": "The wave route",
                                    "depends_on": ["standard"],
                                    "status": wc.COMMITTED},
                                   position=2, total=3)],
        )
        self.assertIsNotNone(arrival)
        self.assertNotIn(arrival.lane, reconcile.EPIC_ACTIVE_STATES)


class OneAskForThePlannerRunTest(unittest.TestCase):
    """Two paths send an epic to the lane that owes a plan artifact — the
    sweep, when the predecessor reaches Done, and the wave commitment itself,
    for the epic that has no predecessor to wait for. Nothing dispatches off
    that lane, so BOTH have to ask for the run, and a second copy of the ask is
    how one of them silently stops asking."""

    def source_of(self, module: str, func: str) -> str:
        src = open(os.path.join(SCRIPTS, module), encoding="utf-8").read()
        return src.split(f"def {func}", 1)[1].split("\ndef ", 1)[0]

    def test_there_is_one_place_that_asks(self):
        import plan_run

        self.assertTrue(callable(plan_run.note))

    def test_the_sweeps_turn_asks_through_it(self):
        self.assertIn("plan_run.note",
                      self.source_of("reconcile.py", "_plan_run_note"))

    def test_the_waves_own_turn_asks_through_it(self):
        self.assertIn("plan_run.note",
                      self.source_of("wave_commitment.py", "advance"))

    def test_the_commit_step_carries_what_a_dispatch_needs(self):
        """`gh api repos/<repo>/dispatches` needs the App token and the repo.
        Without them the first epic of every wave is moved and never started —
        and the step would stay green while it happened."""
        env = step_named("commit the approved wave").get("env") or {}
        self.assertIn("GH_TOKEN", env)
        self.assertIn("REPO", env)


class NoSecondVocabularyTest(unittest.TestCase):
    def test_the_lanes_are_derived_not_typed(self):
        src = open(os.path.join(SCRIPTS, "wave_commitment.py"), encoding="utf-8").read()
        body = src.split('"""', 2)[2]  # past the module docstring
        for lane in ("Green Light", "Planning"):
            for literal in (f'"{lane}"', f"'{lane}'"):
                self.assertNotIn(
                    literal, body,
                    f"{lane} is written into wave_commitment.py — the lane "
                    "comes from config/lane-contract.json and the shape "
                    "vocabulary, or it drifts from them",
                )

    def test_the_turn_lane_is_a_live_lane(self):
        self.assertIn(wc.turn_lane(), lane_contract.lane_names(status="live"))
        self.assertIn(wc.decision_lane(), lane_contract.lane_names(status="live"))

    def test_the_sweep_may_write_the_lane_it_sends_the_epic_to(self):
        """A destination its writer may not touch is the DRE-2824 dead end."""
        self.assertIn("reconcile.py", lane_contract.lane_writers(wc.turn_lane()))


class TheStandardSaysItTest(unittest.TestCase):
    def setUp(self):
        self.text = open(STANDARD, encoding="utf-8").read()

    def test_it_names_the_recorded_state(self):
        self.assertIn(wc.COMMITTED, self.text)

    def test_it_says_the_approval_is_the_shape_and_the_order(self):
        self.assertTrue(
            re.search(r"shape and .{0,10}order", self.text, re.I),
            "the standard must say what approving a wave actually approves",
        )

    def test_it_says_a_reorder_or_a_drop_needs_no_re_approval(self):
        self.assertTrue(
            re.search(r"(reorder|drop)", self.text, re.I),
            "the standard must say the sequence can change inside an "
            "approved wave",
        )
        self.assertIn("wave_commitment.py", self.text)

    def test_it_adds_no_new_numbered_requirement(self):
        """`wave_plan.py` parses `### N.` out of this file as the sections a
        plan must carry. A sixth-and-a-half requirement added here would
        silently fail every wave plan already written."""
        import wave_plan

        self.assertEqual([r.number for r in wave_plan.requirements()],
                         [1, 2, 3, 4, 5, 6])

    def test_every_relative_link_still_resolves(self):
        for target in re.findall(r"\]\(\s*([^)\s#]+)", self.text):
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            path = os.path.normpath(
                os.path.join(os.path.dirname(STANDARD), target.split("#", 1)[0]))
            self.assertTrue(os.path.exists(path), f"dead link: {target}")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(not unittest.main(exit=False).result.wasSuccessful())
