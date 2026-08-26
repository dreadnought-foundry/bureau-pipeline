"""The plan artifact, walked end to end (DRE-2720).

Unit-green is not live-working: this feature spans the planner agent, plan.yml
across two jobs, and a document portal repo. So this walks the whole life of
one UI epic's artifact using the ACTUAL shell from plan.yml — the `run:` blocks
are read out of the workflow and executed with the run's expressions
substituted, so a step that stops matching the script turns this red rather
than failing live on the first epic.

The walk:

  revision 1  planner writes it → check (--ui, because the cards carry
              **Design:** refs) → no previous version, so no version record →
              render → publish
  revision 2  the plan comes back from Green Light changed → check → version
              record generated against the PUBLISHED revision 1 and folded in
              → render → publish to THE SAME path, over the top

And the adversarial half — the shapes that must stop the run before the CEO's
time is spent: a section missing, KPIs as prose, and a UI epic offering a
screenshot where the mockup belongs.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml

REPO = os.path.join(os.path.dirname(__file__), "..")
SCRIPTS = os.path.join(REPO, "scripts")
WF = os.path.join(REPO, ".github", "workflows", "plan.yml")
sys.path.insert(0, SCRIPTS)

import plan_artifact as pa  # noqa: E402

EPIC = "DRE-2668"

# A UI epic's artifact, the shape the standard describes.
V1 = """# Wave 1.5 — the intake gate

## Business case

Cards arrive in every shape and the fleet builds them anyway. A gate in front
of the work is cheaper than the rework round behind it.

## KPIs

```kpis
[
  {"name": "Time to green light", "baseline": 4.0, "unit": "hours",
   "direction": "down", "target": 1.5},
  {"name": "Plans sent back for rework", "baseline": 6, "unit": "per week",
   "direction": "down", "target": 2}
]
```

Baselines measured over the fortnight to 2026-08-25.

## Risk assessment

A gate that bounces too hard stalls the board. Blast radius is one lane;
reversible by turning the guard off.

## Outcome

The CEO reads one artifact per epic and green-lights in minutes, not hours.

## Visual model

The Intake lane, as it will look.

```mockup
<div class="lane" style="background: var(--surface); color: var(--text)">
  <h3 style="font: var(--font-heading)">Intake</h3>
  <button style="background: var(--accent)">Groom</button>
</div>
```

## The cards

| Card | What it does |
| -- | -- |
| DRE-2718 | The Intake lane exists |
| DRE-2719 | Everything goes to Planning |

## Proof and demo

The harness replays a hostile card through the gate; the demo is one epic
walked end to end on the board.
"""

# Revision two, after Green Light feedback: one KPI re-baselined, one added,
# the business case rewritten. Everything else untouched.
V2 = V1.replace(
    '{"name": "Time to green light", "baseline": 4.0, "unit": "hours",\n'
    '   "direction": "down", "target": 1.5},',
    '{"name": "Time to green light", "baseline": 3.2, "unit": "hours",\n'
    '   "direction": "down", "target": 1.5},\n'
    '  {"name": "Cards bounced at intake", "baseline": 0, "unit": "per week",\n'
    '   "direction": "up", "target": 10},',
).replace(
    "A gate in front\nof the work is cheaper than the rework round behind it.",
    "A gate in front of the work is cheaper than the rework round behind it,\n"
    "and the fortnight of rework we just paid for is the evidence.",
)

# The cards the planner created — the evidence the run reads to decide the
# epic is UI work. A **Design:** ref counts; prose about a screen does not.
UI_CHILD_BODIES = (
    "Build the Intake lane.\n"
    "**Design:** console/design/images/screens/desktop/board.png\n"
    "## Acceptance criteria\n- [ ] the lane renders\n"
)
BACKEND_CHILD_BODIES = (
    "Harden the relay's HMAC check. The board screen is unaffected.\n"
    "## Acceptance criteria\n- [ ] replays are dropped\n"
)


def step_run(fragment: str) -> str:
    """The `run:` shell of a plan.yml step, by name fragment."""
    doc = yaml.safe_load(open(WF).read())
    for job in doc["jobs"].values():
        for s in job.get("steps") or []:
            if fragment.lower() in (s.get("name") or "").lower():
                if "run" not in s:
                    raise AssertionError(f"step {fragment!r} has no run: block")
                return s["run"]
    raise AssertionError(f"no step named like {fragment!r} in plan.yml")


class ArtifactLifecycleTest(unittest.TestCase):
    """One epic, two revisions, the workflow's own commands."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.portal = os.path.join(self.tmp, ".plan-portal")
        # The pipeline checkout the workflow calls into.
        self.pipeline = os.path.join(self.tmp, ".bureau-pipeline")
        os.makedirs(self.pipeline)
        shutil.copytree(SCRIPTS, os.path.join(self.pipeline, "scripts"))
        self.artifact = os.path.join(self.tmp, "plan-artifact.md")

    def _shell(self, fragment: str, ui: str = "true"):
        """Run a plan.yml step's shell with the run's expressions resolved."""
        script = step_run(fragment)
        script = script.replace("${{ runner.temp }}", self.tmp)
        script = script.replace(
            "${{ github.event.client_payload.identifier }}", EPIC)
        script = script.replace(
            "${{ steps.uiepic.outputs.ui == 'true' && '--ui' || '' }}",
            "--ui" if ui == "true" else "")
        # Any expression left is one this walk does not model — fail loudly
        # rather than run a mangled command line.
        leftover = re.findall(r"\$\{\{[^}]*\}\}", script)
        self.assertEqual(leftover, [], f"unmodelled expressions in {fragment!r}")
        return subprocess.run(["bash", "-e", "-c", script], cwd=self.tmp,
                              capture_output=True, text=True)

    def _write(self, text):
        with open(self.artifact, "w") as f:
            f.write(text)

    def test_two_revisions_land_on_one_address(self):
        # --- revision one -------------------------------------------------
        self._write(V1)

        # The UI signal comes off the cards, not off the plan's say-so.
        self.assertTrue(pa.is_ui_epic(UI_CHILD_BODIES))
        self.assertFalse(pa.is_ui_epic(BACKEND_CHILD_BODIES))

        r = self._shell("Plan artifact — check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

        # Nothing published yet: no version record, and none invented.
        r = self._shell("Plan artifact — version record")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("no version record", r.stdout)
        self.assertNotIn("## Version record", open(self.artifact).read())

        r = self._shell("Plan artifact — render plan.html")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        page_v1 = open(os.path.join(self.tmp, "plan.html")).read()
        # The mockup is LIVE markup under the design tokens — the thing a
        # screenshot cannot be, and the thing the "Add document" button would
        # have stripped.
        self.assertIn('<button style="background: var(--accent)">', page_v1)
        self.assertIn("tokens.css", page_v1)

        pa.publish(V1, EPIC, self.portal)
        url = pa.stable_url(EPIC, "https://portico.example/docs")

        # --- revision two, after Green Light feedback ---------------------
        self._write(V2)
        r = self._shell("Plan artifact — check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

        r = self._shell("Plan artifact — version record")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("version record folded in", r.stdout)
        merged = open(self.artifact).read()
        self.assertIn("## Version record", merged)
        # Generated, and specific: the added KPI, the re-measured baseline,
        # and which sections the CEO does NOT have to re-read.
        record = pa.sections(merged)["version record"]
        self.assertIn('added "Cards bounced at intake"', record)
        self.assertIn("4.0 → 3.2", record)
        self.assertIn("Risk assessment", record.split("Unchanged", 1)[1])
        # Folding it in must not disturb the seven.
        self.assertEqual(pa.missing_sections(merged), [])

        r = self._shell("Plan artifact — render plan.html")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        page_v2 = open(os.path.join(self.tmp, "plan.html")).read()

        pa.publish(merged, EPIC, self.portal)

        # The address did not move, and it serves revision two.
        self.assertEqual(url, pa.stable_url(EPIC, "https://portico.example/docs"))
        self.assertEqual(sorted(os.listdir(os.path.join(self.portal, "plans"))),
                         ["dre-2668"])
        served = open(os.path.join(self.portal, pa.stable_path(EPIC))).read()
        self.assertIn("Cards bounced at intake", served)
        self.assertIn("Version record", served)

        # An anchor a comment was bound to in revision one still resolves in
        # revision two, even though an earlier section grew.
        for anchor in ('id="outcome-p1"', 'id="visual-model-mockup"',
                       'id="risk-assessment-p1"'):
            self.assertIn(anchor, page_v1)
            self.assertIn(anchor, page_v2)

        # And the numbers survive into the page, so a close-out reads the
        # prediction off the published artifact.
        payload = served.split('id="kpi-data"', 1)[1].split(">", 1)[1] \
                        .split("</script>")[0]
        self.assertEqual(
            sorted(k["name"] for k in json.loads(payload)),
            ["Cards bounced at intake", "Plans sent back for rework",
             "Time to green light"],
        )

    def test_the_closeout_diffs_the_published_prediction(self):
        # The end of the loop the KPI block exists for: the wave closes, the
        # numbers are measured, and nobody has to remember what was promised.
        pa.publish(V2, EPIC, self.portal)
        published = pa.published_source(self.portal, EPIC)
        result = pa.closeout(published, [
            {"name": "Time to green light", "observed": 1.2},
            {"name": "Cards bounced at intake", "observed": 14},
            {"name": "Agent runs per day", "observed": 40},
        ])
        by_name = {k["name"]: k for k in result["kpis"]}
        self.assertTrue(by_name["Time to green light"]["as_predicted"])
        self.assertTrue(by_name["Cards bounced at intake"]["target_met"])
        # Predicted and never measured — the plan still owes an answer.
        self.assertEqual(result["unmeasured"], ["Plans sent back for rework"])
        # Measured and never predicted — O10's story case, named by the tool
        # rather than argued about at the close-out.
        self.assertEqual(result["unpredicted"], ["Agent runs per day"])


class ArtifactStopsTheRunTest(unittest.TestCase):
    """The adversarial half: shapes that must fail the check, because each one
    would otherwise reach the CEO looking complete."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.pipeline = os.path.join(self.tmp, ".bureau-pipeline")
        os.makedirs(self.pipeline)
        shutil.copytree(SCRIPTS, os.path.join(self.pipeline, "scripts"))
        self.artifact = os.path.join(self.tmp, "plan-artifact.md")

    def _check(self, text, ui="true"):
        with open(self.artifact, "w") as f:
            f.write(text)
        script = step_run("Plan artifact — check")
        script = script.replace("${{ runner.temp }}", self.tmp)
        script = script.replace(
            "${{ steps.uiepic.outputs.ui == 'true' && '--ui' || '' }}",
            "--ui" if ui == "true" else "")
        return subprocess.run(["bash", "-e", "-c", script], cwd=self.tmp,
                              capture_output=True, text=True)

    def test_a_missing_section_stops_the_run(self):
        r = self._check(V1.replace("## Risk assessment", "## Notes"))
        self.assertEqual(r.returncode, 1)
        self.assertIn("risk assessment", (r.stdout + r.stderr).lower())

    def test_kpis_as_prose_stop_the_run(self):
        prose = re.sub(r"```kpis.*?```",
                       "We expect review time to come down a lot.",
                       V1, flags=re.DOTALL)
        r = self._check(prose)
        self.assertEqual(r.returncode, 1)
        self.assertIn("kpis", (r.stdout + r.stderr).lower())

    def test_a_screenshot_instead_of_a_mockup_stops_a_ui_epic(self):
        shot = re.sub(r"```mockup.*?```",
                      "![the intake lane](console/design/images/screens/"
                      "desktop/board.png)", V1, flags=re.DOTALL)
        r = self._check(shot)
        self.assertEqual(r.returncode, 1)
        self.assertIn("mockup", (r.stdout + r.stderr).lower())

    def test_a_hex_coded_mockup_stops_the_run(self):
        # Not built from tokens.css means it does not inherit the design
        # system, and the fleet would build to a spec that already diverged.
        hexed = V1.replace("var(--surface)", "#101014") \
                  .replace("var(--text)", "#eeeeee") \
                  .replace("var(--font-heading)", "16px sans-serif") \
                  .replace("var(--accent)", "#3b82f6")
        r = self._check(hexed)
        self.assertEqual(r.returncode, 1)
        self.assertIn("token", (r.stdout + r.stderr).lower())

    def test_a_backend_epic_passes_without_a_mockup(self):
        # The gate must not force a mockup onto work that has no screens —
        # that is how a check gets turned off.
        backend = re.sub(r"The Intake lane, as it will look\.\n\n```mockup.*?```",
                         "Not applicable — this epic ships no UI.",
                         V1, flags=re.DOTALL)
        r = self._check(backend, ui="false")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
