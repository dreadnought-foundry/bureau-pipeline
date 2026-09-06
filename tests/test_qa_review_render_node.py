"""The visual gate renders on the CONSUMER's node, not the runner's (DRE-3248).

`qa-review.yml`'s "Render affected screens" step runs `npm ci` in the consumer
repo's `console/web`. It never set node up, so it ran on whatever the hosted
runner shipped — and when the runner's default fell below the version
`console/web` requires, every render died before a single screenshot:

    npm error code EBADENGINE  Required: {"node":">=24 <25"}
                               Actual:  {"npm":"10.9.8","node":"v22.23.2"}
    [vqshots] status=degraded note=screenshot harness exited 1 (see logs)

One River's Phase 2 proof (DRE-3066) pushed a red banner onto five screens and
watched all five go uncompared. Nothing was red: the stage degraded, the critic
was told not to block, and the design-fidelity verdict was a skip wearing green.

Two halves, both pinned here:

  * **The shape.** No `npm` may be reachable in this workflow without a node
    setup before it, and that setup's version must come from a step that READ
    the consumer (no hard-coded major — the consumer declares it). The negative
    controls mutate the live workflow to prove each assertion actually bites.
  * **The source.** `consumer_node.py` is what reads it: `console/web/.nvmrc`
    first, then `engines.node` in `console/web/package.json`, and it declines
    rather than guessing when the project declares neither.
"""

import copy
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
QA_REVIEW = ROOT / ".github" / "workflows" / "qa-review.yml"
CONSUMER_NODE = ROOT / "scripts" / "consumer_node.py"

WEB_DIR = "console/web"
RENDER_STEP_ID = "vqshots"
# The fleet's shared node plumbing (DRE-2550). "Copy that shape, do not invent
# one" — a bare `actions/setup-node` is accepted by the shape check too, but
# the caching strategy and its cache-break rule live in the action.
SHARED_NODE_ACTION = "setup-node-cached"

# `npm ci`, `npm run shots`, `npx playwright install …` — anything that needs a
# node on PATH. Word-anchored so `# … npm ci …` prose in a comment line is not
# a match (comment lines are dropped before this runs).
_NPM_CALL = re.compile(r"(?<![\w./-])(npm|npx)\s")
# A `${{ steps.<id>.outputs.<name> }}` reference.
_STEP_OUTPUT = re.compile(r"\$\{\{\s*steps\.([A-Za-z0-9_-]+)\.outputs\.[A-Za-z0-9_-]+")


def load(path=QA_REVIEW):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def jobs(doc):
    return (doc.get("jobs") or {}).items()


def is_node_setup(step):
    """Does this step put a node on PATH?"""
    uses = step.get("uses") or ""
    return SHARED_NODE_ACTION in uses or uses.startswith("actions/setup-node")


def npm_calls(step):
    """Every non-comment line of the step's `run:` that invokes npm/npx."""
    out = []
    for line in (step.get("run") or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if _NPM_CALL.search(stripped):
            out.append(stripped)
    return out


def step_name(step, index):
    return step.get("name") or step.get("id") or step.get("uses") or f"step {index}"


def _reads_the_consumer(step):
    """Does this step read the consumer repo's own declaration?

    It must run `consumer_node.py` against the consumer's web project — the
    one place that reads `.nvmrc` / `engines.node`. A step that echoes a
    number is not reading anything.
    """
    run = step.get("run") or ""
    return "consumer_node.py" in run and WEB_DIR in run


def version_source_violations(job_id, steps):
    """Every node setup in the job whose version is not sourced from the consumer."""
    out = []
    by_id = {s.get("id"): s for s in steps if s.get("id")}
    for i, step in enumerate(steps):
        if not is_node_setup(step):
            continue
        with_ = step.get("with") or {}
        pins = [str(with_.get(k) or "") for k in ("node-version", "node-version-file")]
        given = [p for p in pins if p.strip()]
        if not given:
            out.append(
                f"{job_id}: {step_name(step, i)} sets node up with neither "
                f"node-version nor node-version-file"
            )
            continue
        for pin in given:
            refs = _STEP_OUTPUT.findall(pin)
            if not refs:
                out.append(
                    f"{job_id}: {step_name(step, i)} pins node to the literal "
                    f"{pin!r} — the CONSUMER declares its node version, this "
                    f"workflow must not carry a second copy of it"
                )
                continue
            for ref in refs:
                producer = by_id.get(ref)
                if producer is None:
                    out.append(
                        f"{job_id}: {step_name(step, i)} reads steps.{ref}.outputs, "
                        f"but no step in the job has that id"
                    )
                elif not _reads_the_consumer(producer):
                    out.append(
                        f"{job_id}: {step_name(step, i)} takes its node version "
                        f"from step {ref!r}, which never reads the consumer's "
                        f"{WEB_DIR} declaration"
                    )
    return out


def unguarded_npm_violations(job_id, steps):
    """Every npm/npx call reachable with no node setup earlier in the job."""
    out = []
    setup_before = False
    for i, step in enumerate(steps):
        for call in npm_calls(step):
            if not setup_before:
                out.append(
                    f"{job_id}: {step_name(step, i)} runs `{call}` with no node "
                    f"setup before it — it inherits whatever node the hosted "
                    f"runner shipped"
                )
        if is_node_setup(step):
            setup_before = True
    return out


def render_node_violations(doc):
    """Every DRE-3248 violation in one parsed workflow. Empty list == clean."""
    out = []
    for job_id, job in jobs(doc):
        steps = (job or {}).get("steps") or []
        out += unguarded_npm_violations(job_id, steps)
        out += version_source_violations(job_id, steps)
    return out


class LiveWorkflowTest(unittest.TestCase):
    """The shipped qa-review.yml satisfies the rule."""

    def setUp(self):
        self.doc = load()
        self.steps = self.doc["jobs"]["review"]["steps"]

    def test_the_workflow_is_clean(self):
        self.assertEqual(render_node_violations(self.doc), [])

    def test_the_render_step_still_exists(self):
        # Guards every other assertion here: if the step is renamed away, the
        # checks below would pass by matching nothing.
        ids = [s.get("id") for s in self.steps]
        self.assertIn(RENDER_STEP_ID, ids)

    def test_the_render_stage_actually_runs_npm(self):
        # Non-vacuity: `unguarded_npm_violations` only bites where npm is run.
        calls = [c for s in self.steps for c in npm_calls(s)]
        self.assertTrue(calls, "no npm/npx call found — the guard proves nothing")

    def test_a_node_setup_precedes_the_render_step(self):
        idx = {s.get("id"): i for i, s in enumerate(self.steps) if s.get("id")}
        render_at = idx[RENDER_STEP_ID]
        setups = [i for i, s in enumerate(self.steps) if is_node_setup(s)]
        self.assertTrue(
            any(i < render_at for i in setups),
            "the render step has no node setup before it — this is the defect "
            "DRE-3248 exists to close",
        )

    def test_the_setup_uses_the_fleet_s_shared_node_plumbing(self):
        setups = [s for s in self.steps if is_node_setup(s)]
        self.assertTrue(
            any(SHARED_NODE_ACTION in (s.get("uses") or "") for s in setups),
            "DRE-2550's shared node plumbing is the fleet's one node setup — "
            "copy that shape, do not invent one",
        )

    def test_the_shared_action_comes_from_the_pinned_pipeline_checkout(self):
        # A `uses:` ref cannot carry an expression, so the ONLY way this
        # workflow can honour its caller's pipeline_ref (DRE-2026) is to run
        # the action out of the checkout that already threads it.
        setups = [s for s in self.steps if SHARED_NODE_ACTION in (s.get("uses") or "")]
        for step in setups:
            self.assertTrue(
                step["uses"].startswith("./.bureau-pipeline/"),
                f"{step['uses']!r} does not come from the pipeline checkout — a "
                f"literal @ref here would run another channel's action",
            )

    def test_the_node_setup_targets_the_consumer_s_web_project(self):
        setups = [s for s in self.steps if SHARED_NODE_ACTION in (s.get("uses") or "")]
        self.assertTrue(setups)
        for step in setups:
            self.assertEqual((step.get("with") or {}).get("working-directory"), WEB_DIR)

    def test_no_step_hardcodes_a_node_major(self):
        for i, step in enumerate(self.steps):
            with_ = step.get("with") or {}
            for key in ("node-version", "node-version-file"):
                value = str(with_.get(key) or "")
                if value.strip():
                    self.assertIn(
                        "${{", value,
                        f"{step_name(step, i)} hard-codes {key}={value!r}; the "
                        f"consumer declares its node version",
                    )

    def test_the_render_step_reports_the_node_it_rendered_on(self):
        # Acceptance criterion 1: the step LOGS the version it set up. A render
        # that silently runs on the wrong node is the whole failure mode.
        render = next(s for s in self.steps if s.get("id") == RENDER_STEP_ID)
        run = render.get("run") or ""
        self.assertIn("node --version", run)

    def test_the_visual_stage_never_fails_the_job(self):
        # The stage is best-effort by design; a node setup that could not run
        # must degrade the verdict, never wedge the gate.
        for step in self.steps:
            if is_node_setup(step) or _reads_the_consumer(step):
                self.assertTrue(
                    step.get("continue-on-error") is True,
                    f"{step.get('name')!r} can fail the review job",
                )


class NegativeControlTest(unittest.TestCase):
    """Each mutation of the live workflow must be CAUGHT.

    Without these the assertions above could be passing because they match
    nothing — the exact way this defect survived since the runner's default
    fell below 24.
    """

    def setUp(self):
        self.doc = load()

    def _steps(self, doc):
        return doc["jobs"]["review"]["steps"]

    def test_removing_the_node_setup_is_caught(self):
        doc = copy.deepcopy(self.doc)
        steps = self._steps(doc)
        doc["jobs"]["review"]["steps"] = [s for s in steps if not is_node_setup(s)]
        violations = render_node_violations(doc)
        self.assertTrue(violations)
        self.assertTrue(any("no node setup before it" in v for v in violations))

    def test_a_hardcoded_node_version_is_caught(self):
        doc = copy.deepcopy(self.doc)
        for step in self._steps(doc):
            if is_node_setup(step):
                step["with"] = dict(step.get("with") or {})
                step["with"]["node-version"] = "24"
                step["with"]["node-version-file"] = ""
        violations = render_node_violations(doc)
        self.assertTrue(any("literal" in v for v in violations))

    def test_a_node_setup_placed_after_the_render_is_caught(self):
        doc = copy.deepcopy(self.doc)
        steps = self._steps(doc)
        setups = [s for s in steps if is_node_setup(s)]
        rest = [s for s in steps if not is_node_setup(s)]
        doc["jobs"]["review"]["steps"] = rest + setups
        self.assertTrue(
            any("no node setup before it" in v for v in render_node_violations(doc))
        )

    def test_a_version_from_a_step_that_never_read_the_consumer_is_caught(self):
        doc = copy.deepcopy(self.doc)
        steps = self._steps(doc)
        for step in steps:
            if step.get("id") and _reads_the_consumer(step):
                step["run"] = 'echo "version=24" >> "$GITHUB_OUTPUT"'
        self.assertTrue(
            any("never reads the consumer" in v for v in render_node_violations(doc))
        )


class ConsumerNodeTest(unittest.TestCase):
    """`consumer_node.py` — the one place the consumer's declaration is read."""

    def _project(self, nvmrc=None, package=None):
        td = tempfile.mkdtemp()
        web = Path(td) / WEB_DIR
        web.mkdir(parents=True)
        if nvmrc is not None:
            (web / ".nvmrc").write_text(nvmrc, encoding="utf-8")
        if package is not None:
            (web / "package.json").write_text(package, encoding="utf-8")
        return td

    def _run(self, workdir):
        proc = subprocess.run(
            [sys.executable, str(CONSUMER_NODE), WEB_DIR],
            cwd=workdir, capture_output=True, text=True,
        )
        out = {}
        for line in proc.stdout.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                out[k] = v
        return proc, out

    def test_nvmrc_is_read_first_and_passed_as_a_version_file(self):
        proc, out = self._run(self._project(
            nvmrc="24\n",
            package='{"engines": {"node": ">=22 <23"}}',
        ))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(out["setup"], "true")
        self.assertEqual(out["version"], "24")
        # setup-node reads the file itself, so the version is never copied out
        # of it into this workflow (the action documents the preference).
        self.assertEqual(out["node-version-file"], f"{WEB_DIR}/.nvmrc")
        self.assertEqual(out["node-version"], "")
        self.assertIn(".nvmrc", out["source"])

    def test_engines_node_is_the_fallback(self):
        proc, out = self._run(self._project(package='{"engines": {"node": ">=24 <25"}}'))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(out["setup"], "true")
        self.assertEqual(out["version"], "24")
        self.assertEqual(out["node-version"], "24")
        self.assertEqual(out["node-version-file"], "")
        self.assertIn("engines.node", out["source"])

    def test_a_caret_range_resolves_to_its_major(self):
        _, out = self._run(self._project(package='{"engines": {"node": "^22.11.0"}}'))
        self.assertEqual(out["node-version"], "22")

    def test_a_project_declaring_nothing_declines_rather_than_guessing(self):
        proc, out = self._run(self._project(package='{"name": "web"}'))
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(out.get("setup"), "false")
        # The reason is for the log and, through it, the degraded note.
        self.assertTrue(proc.stderr.strip())

    def test_a_missing_project_declines(self):
        td = tempfile.mkdtemp()
        proc, out = self._run(td)
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(out.get("setup"), "false")

    def test_an_empty_nvmrc_falls_through_to_engines(self):
        _, out = self._run(self._project(
            nvmrc="\n", package='{"engines": {"node": ">=24 <25"}}'))
        self.assertEqual(out["node-version"], "24")
        self.assertEqual(out["node-version-file"], "")

    def test_unparseable_package_json_declines(self):
        proc, out = self._run(self._project(package="{not json"))
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(out.get("setup"), "false")

    def test_consumer_text_cannot_inject_extra_step_outputs(self):
        # `source` carries consumer-authored text into GITHUB_OUTPUT. A newline
        # there would be a second output line of the consumer's choosing.
        proc, out = self._run(self._project(
            package='{"engines": {"node": "24\\nsetup=false\\nversion=99"}}'))
        self.assertEqual(out["setup"], "true")
        self.assertEqual(out["version"], "24")
        self.assertNotIn("\n", out["source"])
        # One line per declared output and not one more — a second `setup=`
        # line would be the consumer writing this workflow's step outputs.
        self.assertEqual(len(proc.stdout.strip().splitlines()), 5)

    def test_shell_metacharacters_never_survive_into_an_output(self):
        # `source` is read back by later steps and lands in a log line. A
        # quote or a `$` there is an expansion in whatever shell carries it.
        _, out = self._run(self._project(
            package='{"engines": {"node": ">=24 $(id) `id` \\"x\\" ;rm -rf /"}}'))
        self.assertEqual(out["version"], "24")
        for char in ("$", "`", '"', "'", ";", "(", ")"):
            self.assertNotIn(char, out["source"], f"{char!r} survived")


if __name__ == "__main__":
    unittest.main()
