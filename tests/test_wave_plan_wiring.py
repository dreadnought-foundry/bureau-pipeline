"""The wave route, wired into the plan run (DRE-2845).

DRE-2844 branched Planning three ways and handed the wave route off to
"whatever DRE-2845 builds". This is the far side of that hand-off, and
`scripts/wave_plan.py` is only worth anything if a real wave-shaped card
actually produces a plan and is refused when it does not. These tests pin the
rail:

  1. THE STANDARD ON THE RAIL — `standards/wave-plan.md` reaches the planner
     through `assemble_context.py`, the same way every other standard reaches
     an agent. A checker enforcing a standard the author never received is a
     send-back nobody can act on.
  2. THE RUN — plan.yml runs the wave route only for a wave-shaped card, tells
     the agent where to write the plan, and CHECKS that same file. The check
     is a gate: no `continue-on-error`, so an incomplete wave plan fails the
     run and the card stays in Planning owing a decomposition.
  3. NO NEW CAPABILITY — the wave planner is the planner: same tool list, same
     credentials, same untrusted-text fence around the card body.
  4. HONESTY — the run says where the plan is; it never invents a URL
     (standards/console-honesty.md rule 2).
"""

import os
import re
import sys
import unittest

import yaml

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
WF = os.path.join(REPO, ".github", "workflows", "plan.yml")
SCRIPTS = os.path.join(REPO, "scripts")
STANDARDS_INDEX = os.path.join(REPO, "standards", "README.md")
sys.path.insert(0, SCRIPTS)

import assemble_context as ac  # noqa: E402

# Where the agent writes the plan and where the check reads it. One string,
# asserted to be the same in both places — a check pointed at a path nothing
# writes passes every plan that was never written.
PLAN_PATH = "wave-plan.md"


def wf_src() -> str:
    return open(WF, encoding="utf-8").read()


def wf_steps() -> list[dict]:
    doc = yaml.safe_load(wf_src())
    return [s for job in doc["jobs"].values() for s in job.get("steps") or []]


def steps_matching(fragment: str) -> list[dict]:
    return [s for s in wf_steps()
            if fragment.lower() in (s.get("name") or "").lower()]


def step_named(fragment: str) -> dict:
    found = steps_matching(fragment)
    if not found:
        raise AssertionError(
            f"no step whose name contains {fragment!r}; have: "
            + ", ".join(repr(s.get("name")) for s in wf_steps()))
    return found[0]


def body_of(step: dict) -> str:
    return str(step.get("run") or (step.get("with") or {}).get("prompt") or "")


class TheStandardReachesTheAuthorTest(unittest.TestCase):
    def test_the_planner_receives_the_wave_plan_standard(self):
        self.assertIn(
            "wave-plan.md", ac.standards_for("planner"),
            "the planner writes the wave plan, so it must be handed the "
            "standard the checker enforces",
        )

    def test_the_index_says_the_planner_reads_it(self):
        index = open(STANDARDS_INDEX, encoding="utf-8").read()
        row = next((l for l in index.splitlines()
                    if re.match(r"\|\s*planner\s*\|", l)), "")
        self.assertIn(
            "wave-plan", row,
            "standards/README.md's per-role table is where a reader learns "
            "which standards a role gets — it must match assemble_context.py",
        )


class TheWaveRouteRunsTest(unittest.TestCase):
    def test_the_agent_writes_the_plan_the_check_reads(self):
        agent = step_named("write the wave plan")
        check = step_named("wave plan — check")
        self.assertIn(PLAN_PATH, body_of(agent))
        self.assertIn(PLAN_PATH, body_of(check))

    def test_the_check_runs_the_checker(self):
        self.assertIn("wave_plan.py check", body_of(step_named("wave plan — check")))

    def test_the_prompt_points_at_the_standard_not_at_a_list(self):
        prompt = body_of(step_named("write the wave plan"))
        self.assertIn("standards/wave-plan.md", prompt,
                      "the prompt names the standard the plan is written to")
        self.assertIn("wave_plan.py headings", prompt,
                      "the section titles come from the standard at run time, "
                      "never from a list typed into the workflow")

    def test_the_prompt_asks_for_the_epics_in_dependency_order(self):
        prompt = body_of(step_named("write the wave plan"))
        self.assertIn("epics", prompt)
        self.assertIn("dependency order", prompt)

    def test_every_wave_step_is_gated_on_the_wave_shape(self):
        waves = [s for s in wf_steps()
                 if re.search(r"wave (route|plan)", (s.get("name") or "").lower())]
        self.assertGreaterEqual(len(waves), 4, "the wave route is not wired")
        for s in waves:
            with self.subTest(step=s.get("name")):
                self.assertIn(
                    "route == 'wave'", str(s.get("if") or ""),
                    "a wave-route step that runs for any other shape would "
                    "demand a wave plan of a one-off",
                )

    def test_the_check_is_a_gate_not_a_warning(self):
        gates = steps_matching("wave plan — check")
        self.assertTrue(gates, "there is no wave-plan check to be a gate")
        for s in gates:
            self.assertNotIn(
                "continue-on-error", str(s),
                "the checker refuses; it does not warn (DRE-2845 AC)",
            )
            self.assertNotIn("|| true", body_of(s))


class NoNewCapabilityTest(unittest.TestCase):
    def test_the_wave_planner_gets_the_planners_tools(self):
        agent = step_named("write the wave plan")
        planner = step_named("Plan epic")
        def tools(step):
            args = str((step.get("with") or {}).get("claude_args") or "")
            m = re.search(r'--allowedTools\s+"([^"]+)"', args)
            return m.group(1) if m else None
        self.assertIsNotNone(tools(agent), "the wave step declares no tool list")
        self.assertEqual(tools(planner), tools(agent),
                         "the wave planner is the planner — same tools")

    def test_the_card_text_stays_behind_the_fence(self):
        prompt = body_of(step_named("write the wave plan"))
        self.assertIn("BEGIN UNTRUSTED CARD TEXT", prompt)
        self.assertIn("END UNTRUSTED CARD TEXT", prompt)
        self.assertIn("steps.card.outputs.description", prompt,
                      "the SANITIZED body, never the raw client payload")
        self.assertNotIn("client_payload.description", prompt)


class HonestyTest(unittest.TestCase):
    def test_the_announcement_points_at_something_real(self):
        body = body_of(step_named("wave plan — announce"))
        self.assertIn("actions/runs", body,
                      "with nothing published, say where the plan actually "
                      "is — never invent a URL (console-honesty rule 2)")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(not unittest.main(exit=False).result.wasSuccessful())
