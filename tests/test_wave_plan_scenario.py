"""The wave route, walked end to end (DRE-2845).

Unit-green is not live-working: this feature spans the planner agent,
`scripts/wave_plan.py`, the standard it reads out of the pipeline checkout, and
the steps in plan.yml that run it. So this walks one wave-shaped card's plan
using the ACTUAL shell from plan.yml — the `run:` blocks are read out of the
workflow and executed with the run's expressions substituted, against a
`.bureau-pipeline/` checkout laid out the way a product repo's run lays it out.
A step that stops matching the script turns this red rather than failing live
on the first wave.

The walk:

  written   the planner writes one markdown file → the run's own check passes
            it → the run's own render produces the page, with the epics as a
            table and the hardened header the epic artifact ships

And the adversarial half — the shapes that must stop the run rather than reach
anyone: a section missing, epics committed to out of dependency order, a number
nobody sourced, a citation that resolves to nothing, and the standard itself
absent from the checkout (where the answer must be a refusal, never a quiet
pass on rules the checker remembered).
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPTS = os.path.join(REPO, "scripts")
STANDARDS = os.path.join(REPO, "standards")
WF = os.path.join(REPO, ".github", "workflows", "plan.yml")

WAVE = "DRE-2668"

PLAN = """# Wave 2 — the front door

## 1. The research, with provenance

The planner ran on 40 cards in the fortnight to 2026-08-25 — counted from the
thread on DRE-2720 and the branch logic in `scripts/planning_route.py`.

7 of those runs produced no artifact at all (unverified) — the run records were
pruned before anyone counted them.

## 2. Where the research contradicted the wave

We set out to green-light a whole wave at once. The evidence went the other
way: two of Wave 1.5's cards were rewritten while it ran (DRE-2846), so the
approval is narrowed to the shape and the order.

## 3. The decisions still open

Does a wave own its epics' green lights, or does each epic own its own? The CEO
decides before the second epic starts. Undecided today, and written down here
rather than discovered by whoever hits it first.

## 4. What the plan cuts

out of scope: a console surface for wave progress — nothing reads the record
yet, so the surface would render an empty box and someone would trust it.

## 5. Every phase, and how it will be proven in production

Phase one lands the checker. Proven in production by the first wave-shaped card
whose plan run refuses a plan with a missing section — visible on that card's
own thread, not in a test run.

```epics
[
  {"key": "standard", "title": "The standard moves where agents read it",
   "depends_on": []},
  {"key": "route", "title": "The wave route and its checker",
   "depends_on": ["standard"]},
  {"key": "commitment", "title": "Each epic gets its own green light",
   "depends_on": ["route"]}
]
```

## 6. The KPIs, predicted before the run

```kpis
[
  {"name": "Wave plans refused for a missing section", "baseline": 0,
   "unit": "per wave", "direction": "up", "target": 1},
  {"name": "Waves approved with no written plan", "baseline": 1,
   "unit": "per wave", "direction": "down", "target": 0}
]
```

Both baselines are counted from the plan runs to 2026-08-29: there is no
checker today, so nothing has ever been refused, and the last wave was approved
on a title.
"""


def step_run(fragment: str) -> str:
    """The `run:` shell of a plan.yml step, by name fragment."""
    doc = yaml.safe_load(open(WF, encoding="utf-8").read())
    for job in doc["jobs"].values():
        for s in job.get("steps") or []:
            if fragment.lower() in (s.get("name") or "").lower():
                if "run" not in s:
                    raise AssertionError(f"step {fragment!r} has no run: block")
                return s["run"]
    raise AssertionError(f"no step named like {fragment!r} in plan.yml")


class WaveRouteWalkTest(unittest.TestCase):
    """One wave-shaped card, the workflow's own commands."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        # The pipeline checkout the workflow calls into. The STANDARD is part
        # of it: the checker reads the requirements from there, not from a copy
        # the product repo carries.
        self.pipeline = os.path.join(self.tmp, ".bureau-pipeline")
        os.makedirs(self.pipeline)
        shutil.copytree(SCRIPTS, os.path.join(self.pipeline, "scripts"))
        shutil.copytree(STANDARDS, os.path.join(self.pipeline, "standards"))
        self.plan = os.path.join(self.tmp, "wave-plan.md")

    def _shell(self, fragment: str):
        script = step_run(fragment)
        script = script.replace("${{ runner.temp }}", self.tmp)
        script = script.replace(
            "${{ github.event.client_payload.identifier }}", WAVE)
        leftover = re.findall(r"\$\{\{[^}]*\}\}", script)
        self.assertEqual(leftover, [], f"unmodelled expressions in {fragment!r}")
        return subprocess.run(["bash", "-e", "-c", script], cwd=self.tmp,
                              capture_output=True, text=True)

    def _write(self, text):
        with open(self.plan, "w", encoding="utf-8") as f:
            f.write(text)

    # --- the plan the standard describes ---------------------------------

    def test_a_plan_written_to_the_standard_passes_and_renders(self):
        self._write(PLAN)

        checked = self._shell("Wave plan — check")
        self.assertEqual(0, checked.returncode,
                         checked.stdout + checked.stderr)

        rendered = self._shell("Wave plan — render")
        self.assertEqual(0, rendered.returncode,
                         rendered.stdout + rendered.stderr)
        page = open(os.path.join(self.tmp, "wave-plan.html"),
                    encoding="utf-8").read()
        # The epics it commits to, in order, as something the CEO can read.
        for title in ("The standard moves where agents read it",
                      "The wave route and its checker",
                      "Each epic gets its own green light"):
            self.assertIn(title, page)
        self.assertLess(page.index("The standard moves"),
                        page.index("The wave route and its checker"),
                        "the page must keep the dependency order the plan "
                        "committed to")
        # The same hardened shell the epic artifact ships — one renderer.
        self.assertIn("script-src 'none'", page)

    # --- the shapes that must stop the run --------------------------------

    def test_a_missing_section_stops_the_run_and_is_named(self):
        cut = PLAN.split("## 4. What the plan cuts")[0] + \
            "## 5." + PLAN.split("## 5.", 1)[1]
        self._write(cut)
        out = self._shell("Wave plan — check")
        self.assertNotEqual(0, out.returncode,
                            "an incomplete wave plan must fail the run")
        self.assertIn("What the plan cuts", out.stdout + out.stderr)

    def test_epics_out_of_dependency_order_stop_the_run(self):
        broken = PLAN.replace(
            '{"key": "standard", "title": "The standard moves where agents read it",\n'
            '   "depends_on": []},\n',
            "")
        broken = broken.replace(
            '{"key": "commitment", "title": "Each epic gets its own green light",\n'
            '   "depends_on": ["route"]}',
            '{"key": "commitment", "title": "Each epic gets its own green light",\n'
            '   "depends_on": ["route"]},\n'
            '  {"key": "standard", "title": "The standard moves where agents read it",\n'
            '   "depends_on": []}')
        self._write(broken)
        out = self._shell("Wave plan — check")
        self.assertNotEqual(0, out.returncode)
        self.assertIn("dependency order", out.stdout + out.stderr)

    def test_an_unsourced_number_stops_the_run(self):
        self._write(PLAN.replace(
            "7 of those runs produced no artifact at all (unverified) — the run "
            "records were\npruned before anyone counted them.",
            "7 of those runs produced no artifact at all."))
        out = self._shell("Wave plan — check")
        self.assertNotEqual(0, out.returncode)
        self.assertIn("unverified", out.stdout + out.stderr)

    def test_a_citation_that_does_not_resolve_stops_the_run(self):
        self._write(PLAN.replace("`scripts/planning_route.py`",
                                 "`scripts/planning_router.py`"))
        out = self._shell("Wave plan — check")
        self.assertNotEqual(0, out.returncode,
                            "a citation that does not check out is worse than "
                            "an absent one — it must refuse")
        self.assertIn("planning_router.py", out.stdout + out.stderr)

    def test_the_standard_absent_from_the_checkout_refuses(self):
        """The requirements come from the pipeline checkout. With the standard
        gone the answer is a refusal — never a quiet pass on the rules the
        checker happened to remember."""
        self._write(PLAN)
        os.remove(os.path.join(self.pipeline, "standards", "wave-plan.md"))
        out = self._shell("Wave plan — check")
        self.assertNotEqual(0, out.returncode)
        self.assertIn("wave-plan.md", out.stdout + out.stderr)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(not unittest.main(exit=False).result.wasSuccessful())
