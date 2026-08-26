"""The plan artifact, wired into the plan run (DRE-2720).

scripts/plan_artifact.py is only worth anything if a real epic actually
produces one. These tests pin the rail:

  1. The STANDARD — standards/plan-artifact.md exists, rides the same
     assemble_context.py rail as its siblings, and names the load-bearing
     conventions the planner and the critic both act on (the seven sections,
     the ```kpis grammar, the mockup rule, the stable path).
  2. The BRIEF — briefs/planner.md points the planner at the artifact, so
     plans are AUTHORED to it rather than only reviewed against it.
  3. The RUN — plan.yml tells the planner where to write the artifact, then
     checks it, generates the version record against the published previous
     version, renders plan.html, uploads it as a build output, and publishes
     it. The planner itself gains no new capability: the artifact is an
     OUTPUT, and the tool list it is handed is unchanged (DRE-2729 pins that
     list against agents.yaml, so a widened planner would fail there too).
  4. HONESTY — with no portal configured the run must not invent a URL
     (console-honesty rule 2). The publish step is conditional and the
     comment says plainly where the artifact is.
"""

import os
import re
import sys
import unittest

import yaml

REPO = os.path.join(os.path.dirname(__file__), "..")
WF = os.path.join(REPO, ".github", "workflows", "plan.yml")
STANDARD = os.path.join(REPO, "standards", "plan-artifact.md")
PLANNER_BRIEF = os.path.join(REPO, "briefs", "planner.md")
SCRIPTS = os.path.join(REPO, "scripts")
sys.path.insert(0, SCRIPTS)

import assemble_context as ac  # noqa: E402


def wf_src() -> str:
    return open(WF).read()


def wf_steps() -> list[dict]:
    doc = yaml.safe_load(wf_src())
    return doc["jobs"]["plan"]["steps"]


def step_named(fragment: str) -> dict:
    for s in wf_steps():
        if fragment.lower() in (s.get("name") or "").lower():
            return s
    raise AssertionError(
        f"no step whose name contains {fragment!r}; have: "
        + ", ".join(repr(s.get('name')) for s in wf_steps())
    )


class StandardOnTheRailTest(unittest.TestCase):
    def test_standard_file_exists(self):
        self.assertTrue(os.path.isfile(STANDARD),
                        "standards/plan-artifact.md must exist")

    def test_planner_and_critic_receive_the_standard(self):
        # The planner writes the artifact; the critic judges one. Both need
        # the same definition of what a complete artifact is, from one file.
        for role in ("planner", "critic"):
            self.assertIn("plan-artifact.md", ac.standards_for(role),
                          f"{role} must receive the plan-artifact standard")

    def test_standard_names_the_load_bearing_conventions(self):
        body = open(STANDARD).read()
        for needle, why in [
            ("```kpis", "the machine-readable KPI fence"),
            ("```mockup", "the live-mockup fence"),
            ("Business case", "the first required section"),
            ("Proof and demo", "the last required section"),
            ("console/design/tokens.css", "where a mockup gets its tokens"),
            ("scripts/plan_artifact.py", "the mechanical form of the check"),
            ("plans/<epic>/", "the stable publish path"),
        ]:
            self.assertIn(needle, body, f"standard must name {why}: {needle!r}")

    def test_standard_names_all_seven_sections(self):
        import plan_artifact as pa  # noqa: E402

        body = open(STANDARD).read().lower()
        for section in pa.REQUIRED_SECTIONS:
            self.assertIn(section, body,
                          f"standard must name the {section!r} section")

    def test_standard_carries_the_kpi_field_contract(self):
        # name / baseline / direction are what a close-out diffs. If the
        # standard drifts from the checker, planners write blocks that fail.
        body = open(STANDARD).read()
        for field in ("name", "baseline", "direction"):
            self.assertIn(f"`{field}`", body,
                          f"standard must name the {field!r} KPI field")

    def test_planner_brief_points_at_the_artifact(self):
        body = open(PLANNER_BRIEF).read()
        self.assertIn("plan-artifact.md", body,
                      "briefs/planner.md must point at the standard")


class PlanRunProducesTheArtifactTest(unittest.TestCase):
    """AC1 — a real epic produces the artifact, because the run demands it."""

    ARTIFACT_PATH = "${{ runner.temp }}/plan-artifact.md"

    def test_the_prompt_names_the_artifact_path(self):
        prompt = step_named("Plan epic")["with"]["prompt"]
        self.assertIn(self.ARTIFACT_PATH, prompt,
                      "the planner must be told exactly where to write it")

    def test_the_prompt_names_the_standard(self):
        prompt = step_named("Plan epic")["with"]["prompt"]
        self.assertIn("plan-artifact", prompt)

    def test_the_check_runs_against_the_artifact_the_planner_wrote(self):
        step = step_named("Plan artifact — check")
        self.assertIn("plan_artifact.py check", step["run"])

    def test_the_check_can_fail_the_run(self):
        # A plan whose artifact is missing sections must not reach Green
        # Light: the check is a gate, not a log line.
        step = step_named("Plan artifact — check")
        self.assertNotIn("|| true", step["run"])
        self.assertNotIn("continue-on-error", step)

    def test_the_artifact_steps_only_run_on_the_plan_route(self):
        # The activate route runs no agent, so there is no artifact to check.
        for name in ("Plan artifact — check", "Plan artifact — render",
                     "Plan artifact — version record"):
            self.assertIn("steps.route.outputs.mode == 'plan'",
                          step_named(name)["if"], f"{name} must be plan-only")


class KpiAndMockupGatesTest(unittest.TestCase):
    """AC2/AC5 — the KPI block and the UI mockup are enforced by the run."""

    def test_the_ui_epic_signal_comes_from_the_cards_design_refs(self):
        # Not from the planner's own say-so: a UI epic is one whose cards
        # carry **Design:** refs, which is evidence the run can read.
        step = step_named("Plan artifact — UI epic")
        self.assertIn("child-descriptions", step["run"])
        self.assertIn("plan_artifact.py ui-epic", step["run"])

    def test_the_check_passes_the_ui_flag_when_the_epic_is_ui(self):
        run = step_named("Plan artifact — check")["run"]
        self.assertIn("--ui", run)
        self.assertIn("steps.uiepic.outputs.ui", run)


class VersionRecordTest(unittest.TestCase):
    """AC4 — a second version says what changed, and no human writes it."""

    def test_the_version_record_is_generated_against_the_published_version(self):
        step = step_named("Plan artifact — version record")
        self.assertIn("plan_artifact.py version-record", step["run"])
        self.assertIn("--portal", step["run"])

    def test_the_version_record_is_folded_into_the_rendered_artifact(self):
        # Generated and then dropped on the floor would be worse than none.
        run = " ".join(s.get("run") or "" for s in wf_steps())
        self.assertIn("with-version-record", run)


class PublishTest(unittest.TestCase):
    """AC3 — published at a stable URL; updating it does not move the URL."""

    def test_plan_html_is_a_build_output(self):
        # The card's cheap route: the planner writes markdown, the RUN turns
        # it into plan.html and publishes it — no new planner capability.
        step = step_named("Plan artifact — upload")
        self.assertIn("actions/upload-artifact", step["uses"])

    def test_the_publish_step_uses_the_stable_path_helper(self):
        step = step_named("Plan artifact — publish")
        self.assertIn("plan_artifact.py publish", step["run"])
        self.assertIn("--epic", step["run"])

    def test_publish_is_skipped_when_no_portal_is_configured(self):
        # console-honesty rule 2: absent data renders as absent. With no
        # portal repo there is no URL, and the run must not pretend.
        step = step_named("Plan artifact — publish")
        self.assertIn("PLAN_PORTAL_REPO", str(step.get("if", "")) + str(step))

    def test_publish_goes_through_a_committed_route_not_an_upload_button(self):
        # Portico's "Add document" button strips scripts — a mockup uploaded
        # that way renders looking complete while being dead. The publish is
        # a commit to the portal repo, which is the pipeline route.
        src = wf_src()
        self.assertIn("actions/checkout", src)
        step = step_named("Plan artifact — publish")
        self.assertIn("git push", step["run"])

    def test_the_epic_comment_carries_the_artifact_link(self):
        # The CEO's entry point. AC6 needs an address to anchor against.
        step = step_named("Plan artifact — announce")
        self.assertIn("linear_ops.py comment", step["run"])


class PlannerStaysHardenedTest(unittest.TestCase):
    """The artifact is an output, not a new capability (the card's own
    reasoning for the cheap route)."""

    def test_the_planner_tool_list_is_unchanged(self):
        args = step_named("Plan epic")["with"]["claude_args"]
        tools = re.search(r'--allowedTools\s+"([^"]+)"', args).group(1)
        self.assertEqual(
            sorted(t.strip() for t in tools.split(",")),
            ["Bash", "Edit", "Glob", "Grep", "Read", "Write"],
            "the planner gains no tool from this card — plan.html is a build "
            "output produced by the RUN, not by the agent",
        )


if __name__ == "__main__":
    unittest.main()
