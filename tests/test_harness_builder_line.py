"""RED-first: the harness's probe fixtures write the builder's model line
(DRE-7619).

With the switch `on`, every pull request `bot_pr_flow` and `gate_paths` open
in the sandbox ends with the contract line

    🧠 model-attempt: <model-id> — harness agent starting (a fixture: …)

so the sandbox critic reads the probe as built on the `workhorse` ladder's
first rung and reviews it the way a real repo's known-builder review runs.
With the switch `off` — the default, and every run that names neither the
dispatch input nor the repository variable — the description is exactly what
it was before.

The switch is resolved once per run, by the driver: the dispatch input
`builder_line` first, then the repository variable `HARNESS_BUILDER_LINE`,
then `off`. Anything other than `on` or `off` is `off`, with one warning.
"""

import contextlib
import io
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import model_fallback  # noqa: E402
from harness import __main__ as harness_main  # noqa: E402
from harness import builder_line, framework  # noqa: E402
from harness.scenarios import bot_pr_flow, gate_paths  # noqa: E402
from test_harness_bot_pr_flow import QA, WORKER, FakeGitHub  # noqa: E402

WORKFLOW = ROOT / ".github" / "workflows" / "harness.yml"
LEVERS = ROOT / "docs" / "model-choice-levers.md"
HARNESS_DOC = ROOT / "docs" / "harness.md"

CONTRACT = (
    "🧠 model-attempt: {model} — harness agent starting (a fixture: no agent "
    "built this probe; it names the model a real build runs on)"
)


def _workhorse_first_rung() -> str:
    model_fallback.use_home()
    return model_fallback.LADDERS[model_fallback.KINDS[model_fallback.WORKHORSE_KIND]][0]


def _ctx(gh, state=None):
    run_id = framework.namespaced_run_id(framework.DEFAULT_NAMESPACE, "gha-1-1")
    ctx = framework.HarnessContext(
        gh=gh,
        gh_qa=gh,
        repo="dreadnought-foundry/bureau-harness",
        run_id=run_id,
        worker_login=WORKER,
        qa_login=QA,
        log=lambda *_: None,
    )
    if state is not None:
        ctx.state[builder_line.STATE_KEY] = state
    return ctx


def _opened_bodies(scenario, state):
    gh = FakeGitHub()
    ctx = _ctx(gh, state)
    scenario.setup(ctx)
    scenario.exercise(ctx)
    return ctx, [pr["body"] for pr in gh.prs.values()]


class ResolveTest(unittest.TestCase):
    def setUp(self):
        self.logged = []

    def resolve(self, input_value, variable_value):
        return builder_line.resolve(input_value, variable_value, log=self.logged.append)

    def test_the_dispatch_input_wins_over_the_variable(self):
        self.assertEqual(self.resolve("on", "off"), ("on", "input"))
        self.assertEqual(self.resolve("off", "on"), ("off", "input"))
        self.assertEqual(self.logged, [])

    def test_an_empty_input_falls_to_the_variable(self):
        self.assertEqual(self.resolve("", "on"), ("on", "variable"))
        self.assertEqual(self.resolve(None, "off"), ("off", "variable"))
        self.assertEqual(self.logged, [])

    def test_neither_set_is_off_by_default(self):
        self.assertEqual(self.resolve("", ""), ("off", "default"))
        self.assertEqual(self.resolve(None, None), ("off", "default"))
        self.assertEqual(self.logged, [])

    def test_an_unknown_value_is_off_with_one_warning(self):
        self.assertEqual(self.resolve("yes", "on"), ("off", "input"))
        self.assertEqual(len(self.logged), 1)
        self.assertTrue(self.logged[0].startswith("::warning::"), self.logged)
        self.assertIn("'yes'", self.logged[0])

    def test_an_unknown_variable_is_off_with_one_warning(self):
        self.assertEqual(self.resolve("", "true"), ("off", "variable"))
        self.assertEqual(len(self.logged), 1)
        self.assertTrue(self.logged[0].startswith("::warning::"), self.logged)

    def test_the_status_line(self):
        self.assertEqual(
            builder_line.status_line("on", "input"),
            "harness-builder-line: on (from input)",
        )
        self.assertEqual(
            builder_line.status_line("off", "default"),
            "harness-builder-line: off (from default)",
        )


class ContractLineTest(unittest.TestCase):
    def test_the_line_reads_exactly_as_the_contract_says(self):
        self.assertEqual(
            builder_line.line(), CONTRACT.format(model=_workhorse_first_rung())
        )

    def test_the_model_is_read_from_the_workhorse_ladder_never_typed(self):
        ladder = ["claude-sonnet-5", "claude-opus-5-5"]
        with mock.patch.dict(model_fallback.LADDERS, {"workhorse": ladder}):
            self.assertEqual(builder_line.line(), CONTRACT.format(model="claude-sonnet-5"))

    def test_the_reviewer_card_reads_it_as_opus_today(self):
        self.assertEqual(_workhorse_first_rung(), "claude-opus-5-5")
        self.assertEqual(model_fallback.build_models([builder_line.line()]), ["claude-opus-5-5"])

    def test_the_line_read_inside_a_whole_probe_description(self):
        body = bot_pr_flow.pr_body("main-gha-1-1", "on")
        self.assertEqual(model_fallback.build_models([body]), ["claude-opus-5-5"])
        self.assertEqual(model_fallback.build_models([bot_pr_flow.pr_body("main-gha-1-1")]), [])


class BotPrFlowBodyTest(unittest.TestCase):
    def test_on_appends_the_line_once_as_the_last_line(self):
        _, bodies = _opened_bodies(bot_pr_flow.SCENARIO, "on")
        self.assertEqual(len(bodies), 1)
        body = bodies[0]
        lines = body.splitlines()
        self.assertEqual(lines[0], "What's new: none")
        self.assertEqual(lines[-1], builder_line.line())
        self.assertEqual(body.count("model-attempt:"), 1)

    def test_on_changes_nothing_but_the_appended_line(self):
        off = bot_pr_flow.pr_body("main-gha-1-1")
        on = bot_pr_flow.pr_body("main-gha-1-1", "on")
        self.assertEqual(on, off + "\n" + builder_line.line() + "\n")

    def test_off_and_unset_are_the_body_as_it_was(self):
        ctx = _ctx(FakeGitHub())
        expected = bot_pr_flow.pr_body(ctx.run_id)
        for state in ("off", None):
            _, bodies = _opened_bodies(bot_pr_flow.SCENARIO, state)
            self.assertEqual(bodies, [expected], state)
            self.assertNotIn("model-attempt", bodies[0])


class GatePathsBodyTest(unittest.TestCase):
    def test_on_every_leg_carries_the_line_once_as_its_last_line(self):
        _, bodies = _opened_bodies(gate_paths.SCENARIO, "on")
        self.assertEqual(len(bodies), len(gate_paths.LEGS))
        for body in bodies:
            lines = body.splitlines()
            self.assertEqual(lines[0], "What's new: none")
            self.assertEqual(lines[-1], builder_line.line())
            self.assertEqual(body.count("model-attempt:"), 1)

    def test_on_changes_nothing_but_the_appended_line(self):
        for leg in gate_paths.LEGS:
            off = gate_paths.pr_body("main-gha-1-1", leg)
            on = gate_paths.pr_body("main-gha-1-1", leg, "on")
            self.assertEqual(on, off + "\n" + builder_line.line() + "\n", leg)

    def test_off_and_unset_are_the_bodies_as_they_were(self):
        ctx = _ctx(FakeGitHub())
        expected = sorted(gate_paths.pr_body(ctx.run_id, leg) for leg in gate_paths.LEGS)
        for state in ("off", None):
            _, bodies = _opened_bodies(gate_paths.SCENARIO, state)
            self.assertEqual(sorted(bodies), expected, state)
            for body in bodies:
                self.assertNotIn("model-attempt", body)


class _RecordingScenario(framework.Scenario):
    name = "records_builder_line"
    seen = []

    def setup(self, ctx):
        print("[records_builder_line] scenario started")
        type(self).seen.append(ctx.state.get(builder_line.STATE_KEY))

    def exercise(self, ctx):
        pass

    def verify(self, ctx):
        pass

    def cleanup(self, ctx):
        pass


BASE_ENV = {
    "HARNESS_WORKER_TOKEN": "ghs-worker",
    "HARNESS_QA_TOKEN": "ghs-qa",
    "HARNESS_QA_LOGIN": QA,
    "HARNESS_WAIT_DEADLINE_MINUTES": "0",
}


def _drive(env, scenarios=2):
    """main() over `env`, running the recording scenario `scenarios` times
    (each run is its own HarnessContext); returns (exit code, stdout, seen)."""
    _RecordingScenario.seen = []
    out = io.StringIO()
    names = ",".join([_RecordingScenario.name] * scenarios)
    with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(
        harness_main, "discover",
        lambda: {_RecordingScenario.name: _RecordingScenario()},
    ), contextlib.redirect_stdout(out):
        code = harness_main.main(["--scenarios", names, "--run-id", "gha-1-1"])
    return code, out.getvalue(), list(_RecordingScenario.seen)


class DriverTest(unittest.TestCase):
    def _status_lines(self, out):
        return [ln for ln in out.splitlines() if ln.startswith("harness-builder-line:")]

    def test_the_input_turns_it_on_and_every_scenario_sees_it(self):
        code, out, seen = _drive({
            **BASE_ENV,
            "HARNESS_BUILDER_LINE_INPUT": "on",
            "HARNESS_BUILDER_LINE_VAR": "off",
        })
        self.assertEqual(code, 0, out)
        self.assertEqual(self._status_lines(out), ["harness-builder-line: on (from input)"])
        self.assertEqual(seen, ["on", "on"])

    def test_the_variable_decides_when_the_input_is_empty(self):
        code, out, seen = _drive({
            **BASE_ENV,
            "HARNESS_BUILDER_LINE_INPUT": "",
            "HARNESS_BUILDER_LINE_VAR": "on",
        })
        self.assertEqual(code, 0, out)
        self.assertEqual(self._status_lines(out), ["harness-builder-line: on (from variable)"])
        self.assertEqual(seen, ["on", "on"])

    def test_neither_is_off_by_default(self):
        code, out, seen = _drive(dict(BASE_ENV))
        self.assertEqual(code, 0, out)
        self.assertEqual(self._status_lines(out), ["harness-builder-line: off (from default)"])
        self.assertEqual(seen, ["off", "off"])

    def test_the_line_is_logged_once_before_any_scenario_starts(self):
        _, out, _ = _drive({**BASE_ENV, "HARNESS_BUILDER_LINE_VAR": "on"})
        lines = out.splitlines()
        status = lines.index("harness-builder-line: on (from variable)")
        started = lines.index("[records_builder_line] scenario started")
        self.assertLess(status, started)

    def test_an_unknown_value_is_off_with_one_warning_per_run(self):
        code, out, seen = _drive({**BASE_ENV, "HARNESS_BUILDER_LINE_VAR": "maybe"})
        self.assertEqual(code, 0, out)
        warnings = [ln for ln in out.splitlines() if ln.startswith("::warning::")]
        self.assertEqual(len(warnings), 1, out)
        self.assertEqual(self._status_lines(out), ["harness-builder-line: off (from variable)"])
        self.assertEqual(seen, ["off", "off"])


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text(encoding="utf-8")
        self.doc = yaml.safe_load(self.text)

    def test_the_dispatch_input_is_declared_with_an_empty_default(self):
        on = self.doc.get("on", self.doc.get(True))
        inputs = on["workflow_dispatch"]["inputs"]
        self.assertIn("builder_line", inputs)
        spec = inputs["builder_line"]
        self.assertEqual(spec.get("default"), "")
        self.assertEqual(spec.get("type", "string"), "string")
        self.assertFalse(spec.get("required", False))

    def test_the_scenarios_step_carries_both_env_lines(self):
        steps = [
            step
            for job in self.doc["jobs"].values()
            for step in job.get("steps", [])
            if step.get("name") == "Run harness scenarios"
        ]
        self.assertEqual(len(steps), 1)
        env = steps[0]["env"]
        self.assertEqual(env["HARNESS_BUILDER_LINE_INPUT"], "${{ inputs.builder_line }}")
        self.assertEqual(env["HARNESS_BUILDER_LINE_VAR"], "${{ vars.HARNESS_BUILDER_LINE }}")
        self.assertIn("HARNESS_BUILDER_LINE_INPUT: ${{ inputs.builder_line }}", self.text)
        self.assertIn("HARNESS_BUILDER_LINE_VAR: ${{ vars.HARNESS_BUILDER_LINE }}", self.text)

    def test_the_driver_reads_the_names_the_workflow_sets(self):
        self.assertEqual(builder_line.INPUT_ENV, "HARNESS_BUILDER_LINE_INPUT")
        self.assertEqual(builder_line.VARIABLE_ENV, "HARNESS_BUILDER_LINE_VAR")


class DocsTest(unittest.TestCase):
    def _row(self):
        for line in LEVERS.read_text(encoding="utf-8").splitlines():
            if line.startswith("| `DRE-7187` |"):
                return [cell.strip() for cell in line.strip().strip("|").split("|")]
        self.fail("no DRE-7187 row")

    def test_the_levers_row_names_the_variable_as_the_one_step_back(self):
        cells = self._row()
        self.assertEqual(cells[2], "`model-attempt line`")
        self.assertEqual(cells[3], "`critic`")
        self.assertEqual(cells[4], "`posted` → `not posted`")
        self.assertEqual(cells[7], "`this build's model could not be read`")
        self.assertIn("`HARNESS_BUILDER_LINE`", cells[5])
        self.assertIn("`off`", cells[5])
        self.assertIn("revert", cells[5].lower())
        self.assertIn("next harness run that starts", cells[6])

    def test_the_harness_doc_describes_the_switch_and_the_dependabot_limit(self):
        text = HARNESS_DOC.read_text(encoding="utf-8")
        self.assertIn("HARNESS_BUILDER_LINE", text)
        self.assertIn("builder_line", text)
        self.assertIn("harness-builder-line:", text)
        self.assertIn("dependabot_flow", text.split("HARNESS_BUILDER_LINE", 1)[1])


if __name__ == "__main__":
    unittest.main()
