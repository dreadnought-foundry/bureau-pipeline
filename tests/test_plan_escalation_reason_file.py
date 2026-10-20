"""The planner escalated: the run reads its reason file, not the child count
(DRE-7624).

DRE-7318 already had six children from an earlier planning when its planner
escalated on 2026-10-18. The run decided "the planner escalated" by counting
the epic's children, so it took the plan path, found no artifact and died on
`plan artifact: cannot read artifact` — the escalation step never ran, the
question never reached the CEO, and the sweep parked the card in Triage two
hours later. The signal is the file the planner writes when it escalates,
`planner-escalation.txt`. These tests pin it, read from the workflow file:

  1. A step right after the planner reads whether the reason file exists and
     is not empty, and says so as `steps.escalated.outputs.escalated`. It is
     run here, against real files, the way the runner runs it.
  2. The escalation step runs when the planner escalated, whatever the child
     count — and still runs for a planner that wrote nothing and created no
     children (DRE-5564 quotes its closing message).
  3. No step that reads `plan-artifact.md` runs when the planner escalated:
     the plan job is walked in order with the epic's six children in hand,
     and the `publish` job's gate is read the same way.
  4. A reason file AND a plan artifact is a contradiction: the step fails the
     run and names both files.
"""

import os
import subprocess
import sys
import tempfile
import unittest

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.join(HERE, "..")
WF = os.path.join(REPO, ".github", "workflows", "plan.yml")
sys.path.insert(0, HERE)

from test_planner_queue_wiring import Walk, truth  # noqa: E402

REASON = "planner-escalation.txt"
ARTIFACT = "plan-artifact.md"
ESCALATION = "Planner escalation — hand-planning parks for the CEO"
ESCALATED = "steps.escalated.outputs.escalated"
KIDS = "steps.kids.outputs.count"


def jobs() -> dict:
    with open(WF, encoding="utf-8") as fh:
        return yaml.safe_load(fh)["jobs"]


def plan_steps() -> list[dict]:
    return jobs()["plan"]["steps"]


def index_of(pred) -> int:
    for i, s in enumerate(plan_steps()):
        if pred(s):
            return i
    raise AssertionError("no such step in plan.yml's plan job")


def by_id(ident: str) -> dict:
    return plan_steps()[index_of(lambda s: s.get("id") == ident)]


def by_name(name: str) -> dict:
    return plan_steps()[index_of(lambda s: s.get("name") == name)]


def text_of(step: dict) -> str:
    return f"{step.get('run') or ''} {step.get('with') or ''}"


class PlanRoute(Walk):
    """The three-valued `if:` reader, with the job read as still green and the
    plan route's facts handed in by name."""

    def _name(self, text):
        if text in self.known:
            return self.known[text]
        return super()._name(text)

    def _call(self, name, args):
        if name in ("success", "always"):
            return True
        if name in ("cancelled", "failure"):
            return False
        return Walk._call(name, args)


def walk_from_escalated(known: dict) -> dict[str, bool | None]:
    """Walk the plan job from the step after `escalated`, in file order: step
    name → the truth of its gate. A step known to be skipped empties every
    later read of its outputs, the way the runner does."""
    facts = {"steps.route.outputs.mode": "plan", **known}
    walk, out = PlanRoute(facts, set()), {}
    start = index_of(lambda s: s.get("id") == "escalated")
    for s in plan_steps()[start + 1:]:
        t = truth(walk.evaluate(str(s.get("if") or "true")))
        out[s.get("name")] = t
        ident = s.get("id")
        if t is False and ident:
            walk.skipped.add(ident)
            for key in [k for k in walk.known if k.startswith(f"steps.{ident}.")]:
                del walk.known[key]
    return out


def run_escalated(reason: str | None, artifact: str | None):
    """Run the `escalated` step's script against a runner temp holding the
    files named. Returns (exit code, combined output, GITHUB_OUTPUT text)."""
    step = by_id("escalated")
    with tempfile.TemporaryDirectory() as tmp:
        if reason is not None:
            with open(os.path.join(tmp, REASON), "w") as fh:
                fh.write(reason)
        if artifact is not None:
            with open(os.path.join(tmp, ARTIFACT), "w") as fh:
                fh.write(artifact)
        out = os.path.join(tmp, "github-output")
        open(out, "w").close()
        script = step["run"].replace("${{ runner.temp }}", tmp)
        env = {**os.environ, "RUNNER_TEMP": tmp, "GITHUB_OUTPUT": out}
        env.update({k: str(v) for k, v in (step.get("env") or {}).items()})
        proc = subprocess.run(["bash", "-e", "-c", script], env=env, cwd=REPO,
                              capture_output=True, text=True, timeout=60)
        with open(out) as fh:
            return proc.returncode, proc.stdout + proc.stderr, fh.read()


class TheStepThatReadsTheReasonFile(unittest.TestCase):
    def test_it_sits_right_after_the_planner_finishes(self):
        done = index_of(lambda s: s.get("id") == "plan_done")
        here = index_of(lambda s: s.get("id") == "escalated")
        self.assertEqual(here, done + 1,
                         "the reason file is read the moment the planner is done")
        self.assertLess(here, index_of(lambda s: s.get("name") == ESCALATION))
        self.assertIn("mode == 'plan'", str(by_id("escalated").get("if")))

    def test_a_written_reason_is_an_escalation(self):
        rc, said, out = run_escalated("We need the CEO to choose.", None)
        self.assertEqual(rc, 0, said)
        self.assertIn("escalated=true", out.splitlines())

    def test_no_reason_file_is_no_escalation(self):
        for artifact in (None, "# the plan\n"):
            with self.subTest(artifact=artifact):
                rc, said, out = run_escalated(None, artifact)
                self.assertEqual(rc, 0, said)
                self.assertIn("escalated=false", out.splitlines())

    def test_an_empty_reason_file_is_no_escalation(self):
        rc, said, out = run_escalated("", None)
        self.assertEqual(rc, 0, said)
        self.assertIn("escalated=false", out.splitlines())

    def test_a_reason_and_a_plan_fail_the_run_and_name_both_files(self):
        rc, said, out = run_escalated("We need the CEO to choose.", "# the plan\n")
        self.assertNotEqual(rc, 0, "a contradiction must fail the run")
        self.assertIn(REASON, said)
        self.assertIn(ARTIFACT, said)
        self.assertNotIn("escalated=true", out.splitlines(),
                         "the step must not pick one")
        self.assertNotIn("escalated=false", out.splitlines(),
                         "the step must not pick one")


class TheEscalationStepReadsTheReasonFile(unittest.TestCase):
    def test_its_gate_does_not_hang_on_the_child_count_alone(self):
        gate = str(by_name(ESCALATION).get("if"))
        self.assertIn(ESCALATED, gate)

    def test_an_epic_with_children_that_escalated_reaches_it(self):
        """DRE-7318: six children from an earlier planning, a reason file and
        no plan artifact."""
        reach = walk_from_escalated({KIDS: "6", ESCALATED: "true"})
        self.assertIs(reach[ESCALATION], True)
        self.assertIn("planning_escalation.py escalate", by_name(ESCALATION)["run"])

    def test_a_childless_epic_with_no_reason_still_escalates(self):
        """Today's behavior (DRE-2848, DRE-5564): no cards, no reason, the
        closing message is quoted."""
        reach = walk_from_escalated({KIDS: "0", ESCALATED: "false"})
        self.assertIs(reach[ESCALATION], True)
        run = by_name(ESCALATION)["run"]
        self.assertIn("--execution-file", run)

    def test_a_plan_with_children_and_no_reason_does_not_escalate(self):
        """The non-vacuous twin: the gate still tells a plan from a question."""
        reach = walk_from_escalated({KIDS: "6", ESCALATED: "false"})
        self.assertIs(reach[ESCALATION], False)

    def test_validating_the_children_cannot_fail_an_escalation(self):
        """The old children were checked when they were made; an escalating
        planner wrote none, and its question must reach the escalation step."""
        run = by_id("kids")["run"]
        self.assertIn(ESCALATED, str(by_id("kids").get("env") or {}) + run)


class NothingReadsThePlanWhenThePlannerEscalated(unittest.TestCase):
    def _artifact_readers(self) -> list[str]:
        start = index_of(lambda s: s.get("id") == "escalated")
        return [s.get("name") for s in plan_steps()[start + 1:]
                if ARTIFACT in text_of(s)]

    def test_there_are_artifact_readers_to_check(self):
        self.assertIn("Plan artifact — check", self._artifact_readers())
        self.assertIn("Plan artifact — upload source", self._artifact_readers())

    def test_no_artifact_reader_runs_when_the_planner_escalated(self):
        reach = walk_from_escalated({KIDS: "6", ESCALATED: "true"})
        for name in self._artifact_readers():
            with self.subTest(step=name):
                self.assertIs(reach[name], False)

    def test_no_children_bearing_step_runs_when_the_planner_escalated(self):
        """Every plan-only step that gates on the child count — the proof
        card, the UI read, the first critic, the routing verdicts — is a step
        about a plan, and there is none."""
        reach = walk_from_escalated({KIDS: "6", ESCALATED: "true"})
        gated = [s.get("name") for s in plan_steps() if KIDS in str(s.get("if") or "")
                 and s.get("name") != ESCALATION]
        self.assertTrue(gated)
        for name in gated:
            with self.subTest(step=name):
                self.assertIs(reach.get(name), False)

    def test_the_artifact_readers_still_run_for_a_plan(self):
        """The non-vacuous twin: with no reason file, the plan path is open."""
        reach = walk_from_escalated({KIDS: "6", ESCALATED: "false"})
        self.assertIs(reach["Plan artifact — check"], True)
        self.assertIs(reach["Proof card — the epic's last child"], True)

    def test_the_publish_job_is_skipped_when_the_planner_escalated(self):
        self.assertEqual(jobs()["plan"]["outputs"].get("escalated"),
                         "${{ steps.escalated.outputs.escalated }}")
        gate = str(jobs()["publish"].get("if") or "")
        base = {"needs.plan.outputs.mode": "plan", "needs.plan.outputs.kids": "6",
                "needs.plan.outputs.replanned": ""}
        for escalated, expected in (("true", False), ("false", True)):
            with self.subTest(escalated=escalated):
                walk = PlanRoute({**base, "needs.plan.outputs.escalated": escalated}, set())
                self.assertIs(truth(walk.evaluate(gate)), expected)


if __name__ == "__main__":
    unittest.main()
