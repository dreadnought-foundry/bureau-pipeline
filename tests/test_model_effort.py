"""Every run asks for the effort level `config/models.yaml` declares for the
model it runs on, and none for a model it declares nothing for (DRE-4836).

What this pins shut
-------------------
Opus 5.5 defaults to effort `medium`, one level BELOW Claude Opus 5's `high`.
So the adoption is not just a ladder edit: a fleet that swapped the id and said
nothing about effort would think one level less on every card, on every repo,
with nothing in any log saying so. The CEO's decision of 2026-09-24 is "we
should be using the new opus 5.5 models on high effort" — the level is set
EXPLICITLY, never left to the model's default and never left to the harness's.

The shape, mirroring how the model id itself is handled (DRE-2316): the level
is DATA in `config/models.yaml`, one `effort:` entry per model that needs one;
`model_fallback.py` is the one reader; every workflow step that chooses a model
also emits the `--effort` argument for it, and every `claude_args` block that
passes `--model` passes that argument beside it. A model with no declared
effort gets no argument at all — the adoption changes Opus 5.5 and nothing
else.

Which model leads which ladder, and which level each declares, is the
console's to change (DRE-6862). So the rule — the effort written is the one
`effort:` declares for the model chosen, and empty for a model with no entry —
is proved on the frozen fixture (`tests/model_fixture.py`) and its CLI, with
every expected id and level read off that fixture. What still reads the
committed file is what must hold for any saved state. `tests/test_model_config.py`
is the sibling that pins the canonical file and its generated mirrors.
"""

import copy
import glob
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "models.yaml"
PRICES = ROOT / "config" / "model-prices.yaml"
WORKFLOWS = sorted(glob.glob(str(ROOT / ".github" / "workflows" / "*.yml")))

sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import model_fallback as mf  # noqa: E402
import model_fixture  # noqa: E402

# Ids priced in config/model-prices.yaml, which the console never writes: a
# declared vendor price names the model it is the price of (PriceTest).
OPUS55 = "claude-opus-5-5"
OPUS = "claude-opus-5"
SONNET5 = "claude-sonnet-5"
SONNET55 = "claude-sonnet-5-5"

# The frozen config every rule test reads its expected rungs and levels off.
FIXTURE = model_fixture.fixture_config()

# The selector loaded from a throwaway tree of the frozen files, and that
# tree's CLI — set by setUpModule.
fx = None
FIXTURE_CLI = None
_TREE = None


def setUpModule():
    global fx, FIXTURE_CLI, _TREE
    _TREE = tempfile.TemporaryDirectory()
    tree = model_fixture.fixture_tree(Path(_TREE.name) / "tree")
    fx = model_fixture.fixture_module(tree)
    FIXTURE_CLI = tree / "scripts" / "model_fallback.py"


def tearDownModule():
    _TREE.cleanup()


def _canonical() -> dict:
    return yaml.safe_load(CONFIG.read_text())


def _walk(role: str) -> list:
    """The rungs `role` walks in the frozen config: its group's ladder."""
    return model_fixture.ladder(FIXTURE, FIXTURE["agents"][role])


def _fixture_rungs() -> set:
    return {m for kind in FIXTURE["kinds"] for m in model_fixture.ladder(FIXTURE, kind)}


class LadderTest(unittest.TestCase):
    """What must hold for the committed file whatever the console saved."""

    def test_the_canonical_config_still_validates(self):
        self.assertEqual(mf.policy_errors(_canonical()), [])

    def test_every_rung_names_a_reason(self):
        # The file's own style: a rung is a decision, and the decision carries
        # its justification beside it.
        cfg = _canonical()
        for name, rungs in cfg["ladders"].items():
            for rung in rungs:
                self.assertTrue(
                    str(rung.get("reason") or "").strip(),
                    f"ladders.{name}: {rung.get('model')} has no reason",
                )


class PriceTest(unittest.TestCase):
    """The declared price — what "no dearer" is read from (DRE-3895)."""

    def test_opus_5_5_is_declared_at_4_and_20(self):
        prices = yaml.safe_load(PRICES.read_text())["prices"]
        self.assertIn(OPUS55, prices, "an id with no price can never be adopted")
        entry = prices[OPUS55]
        self.assertEqual(float(entry["input"]), 4.00)
        self.assertEqual(float(entry["output"]), 20.00)
        self.assertTrue(str(entry.get("source") or "").startswith("http"))
        self.assertTrue(entry.get("declared"), "a figure needs the date a human wrote it")

    def test_sonnet_5_5_is_declared_at_the_price_of_sonnet_5(self):
        # DRE-5116's adoption rests on this: the same price on both sides.
        prices = yaml.safe_load(PRICES.read_text())["prices"]
        self.assertIn(SONNET55, prices, "an id with no price can never be adopted")
        entry = prices[SONNET55]
        self.assertEqual(float(entry["input"]), 2.00)
        self.assertEqual(float(entry["output"]), 10.00)
        self.assertTrue(str(entry.get("source") or "").startswith("http"))
        self.assertTrue(entry.get("declared"))
        new, old = mf.declared_prices()[SONNET55], mf.declared_prices()[SONNET5]
        self.assertLessEqual(new["input"], old["input"])
        self.assertLessEqual(new["output"], old["output"])

    def test_opus_5_5_is_no_dearer_than_the_opus_5_it_replaces(self):
        # The whole adoption rule in one assertion: same family, newer, and at
        # or below the rung it takes over on BOTH sides.
        prices = mf.declared_prices()
        new, old = prices[OPUS55], prices[OPUS]
        self.assertLessEqual(new["input"], old["input"])
        self.assertLessEqual(new["output"], old["output"])


class DeclaredEffortTest(unittest.TestCase):
    """The level itself: data in the config, one reader — proved on the
    frozen fixture, every level read off its `effort:` block."""

    def test_the_fixture_declares_a_level_and_leaves_a_rung_without_one(self):
        # Guard: both halves of the rule below need a case to run on.
        self.assertTrue(FIXTURE.get("effort"), "the fixture declares no effort")
        self.assertTrue(_fixture_rungs() - set(FIXTURE["effort"]))

    def test_effort_for_reads_the_declared_level_of_every_model(self):
        for model, level in FIXTURE["effort"].items():
            with self.subTest(model=model):
                self.assertEqual(fx.effort_for(model), level)

    def test_a_model_with_no_declared_effort_gets_no_argument(self):
        # The adoption changes Opus 5.5 and nothing else: every other model
        # keeps whatever the harness would have used, so nobody's spend moves
        # on a card this one was not about.
        for model in sorted(_fixture_rungs() - set(FIXTURE["effort"])):
            with self.subTest(model=model):
                self.assertIsNone(fx.effort_for(model))

    def test_the_generated_mirror_carries_the_declared_levels(self):
        # The degrade path is the whole point of the mirror: a checkout whose
        # YAML will not parse still runs the ladders — and must still ask for
        # the level, or the fall-back-to-literal path silently drops to medium.
        self.assertEqual(fx._FALLBACK_MODEL_CONFIG["effort"], FIXTURE["effort"])

    def test_an_unknown_level_is_refused(self):
        cfg = copy.deepcopy(FIXTURE)
        cfg["effort"] = {_walk("engineer")[0]: "maximum-effort"}
        errors = fx.policy_errors(cfg)
        self.assertTrue(any("maximum-effort" in e for e in errors), errors)

    def test_an_effort_entry_for_a_model_nothing_runs_is_refused(self):
        # A level declared for an id no ladder names is a level nothing applies
        # — and the likeliest cause is a typo in the id the fleet is meant to
        # be running at `high`.
        cfg = copy.deepcopy(FIXTURE)
        cfg["effort"] = dict(cfg.get("effort") or {}, **{"claude-opus-5-55": "high"})
        errors = fx.policy_errors(cfg)
        self.assertTrue(any("claude-opus-5-55" in e for e in errors), errors)


class SelectorCliTest(unittest.TestCase):
    """The CLI the workflows actually call, run in the fixture tree."""

    def _run(self, *args, available=None):
        env = dict(os.environ)
        env["BUREAU_FAKE_AVAILABLE"] = json.dumps(available or {})
        proc = subprocess.run(
            [sys.executable, str(FIXTURE_CLI), *args],
            capture_output=True, text=True, env=env,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout.strip()

    def _select_down_to(self, role, rung, path):
        """`select <role> --effort-file <path>` with every rung above `rung`
        on its walk unavailable, so the walk lands on `rung`."""
        walk = _walk(role)
        above = walk[:walk.index(rung)]
        return self._run(
            "select", role, "--effort-file", str(path),
            available={m: m not in above for m in walk},
        )

    def test_effort_command_prints_the_level(self):
        for model, level in FIXTURE["effort"].items():
            with self.subTest(model=model):
                self.assertEqual(self._run("effort", model), level)

    def test_effort_command_prints_nothing_for_an_undeclared_model(self):
        # Empty, exit 0 — the shell writes `effort_arg=` and the step passes no
        # argument, which is how a model with no declared level stays untouched.
        for model in sorted(_fixture_rungs() - set(FIXTURE["effort"])):
            with self.subTest(model=model):
                self.assertEqual(self._run("effort", model), "")
        self.assertEqual(self._run("effort", "claude-who"), "")

    def test_select_writes_the_effort_of_the_model_it_chose(self):
        declared = [m for m in _walk("engineer") if m in FIXTURE["effort"]]
        self.assertTrue(declared, "no rung of the walk declares a level")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "effort.txt"
            model = self._select_down_to("engineer", declared[0], path)
            self.assertEqual(model, declared[0])
            self.assertEqual(path.read_text().strip(), FIXTURE["effort"][declared[0]])

    def test_select_writes_an_empty_effort_when_it_fell_to_another_rung(self):
        # The fall is the case that matters: the run lands on a rung that
        # declares no level, below one that does, and it must NOT inherit the
        # level of anything above it.
        walk = _walk("engineer")
        bare = [
            m for i, m in enumerate(walk)
            if m not in FIXTURE["effort"]
            and any(above in FIXTURE["effort"] for above in walk[:i])
        ]
        self.assertTrue(bare, "no undeclared rung sits below a declared one")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "effort.txt"
            model = self._select_down_to("engineer", bare[0], path)
            self.assertEqual(model, bare[0])
            self.assertEqual(path.read_text().strip(), "")


class WorkflowWiringTest(unittest.TestCase):
    """Every run that reaches a model passes the level with it.

    Read off the workflow files rather than listed here: a step added later
    that chooses a model and forgets the level is the whole failure mode, and a
    hand-written list of today's steps would not see it.
    """

    # `--model <something>` inside a claude_args block, and every step id that
    # `something` comes from when it is an interpolated step output — one, or
    # one per branch of an expression that chooses between two (the critic's
    # shadow replay, DRE-7193). The effort argument is read the same way, so
    # each branch's model must have its level from the same step.
    _MODEL_EXPR = re.compile(r"--model\s+\$\{\{(.*?)\}\}")
    _MODEL_STEP = re.compile(r"\bsteps\.([A-Za-z0-9_]+)\.outputs\.model\b")
    _MODEL_ARG_WHOLE = re.compile(r"\$\{\{\s*steps\.([A-Za-z0-9_]+)\.outputs\.model_arg")
    _EFFORT_ARG = re.compile(r"\bsteps\.([A-Za-z0-9_]+)\.outputs\.effort_arg\b")

    def _model_steps(self, block: str) -> set[str]:
        steps = set(self._MODEL_ARG_WHOLE.findall(block))
        for expr in self._MODEL_EXPR.findall(block):
            steps |= set(self._MODEL_STEP.findall(expr))
        return steps

    def test_a_two_branch_model_needs_both_branches_levels(self):
        # The reading the critic's shadow replay is held to: a model chosen
        # between two steps, and an effort that names only one of them, is a
        # level computed for a model the other branch does not run.
        both = ("--model ${{ inputs.shadow && steps.a.outputs.model || steps.b.outputs.model }}\n"
                "${{ inputs.shadow && steps.a.outputs.effort_arg || steps.b.outputs.effort_arg }}")
        self.assertEqual(self._model_steps(both), {"a", "b"})
        self.assertEqual(set(self._EFFORT_ARG.findall(both)), {"a", "b"})
        one = ("--model ${{ inputs.shadow && inputs.model || steps.b.outputs.model }}\n"
               "${{ inputs.shadow && steps.a.outputs.effort_arg || steps.b.outputs.effort_arg }}")
        self.assertNotEqual(self._model_steps(one), set(self._EFFORT_ARG.findall(one)))

    def _claude_args_blocks(self):
        """`(path, line_no, block_text)` for every `claude_args:` block."""
        for path in WORKFLOWS:
            lines = Path(path).read_text().splitlines()
            for i, line in enumerate(lines):
                if not line.strip().startswith("claude_args:"):
                    continue
                indent = len(line) - len(line.lstrip())
                block = []
                for nxt in lines[i + 1:]:
                    if nxt.strip() and (len(nxt) - len(nxt.lstrip())) <= indent:
                        break
                    block.append(nxt)
                yield path, i + 1, "\n".join(block)

    def test_there_are_claude_args_blocks_to_check(self):
        # Guard against a regex that silently matches nothing.
        self.assertGreater(len(list(self._claude_args_blocks())), 10)

    def test_every_claude_args_block_passes_the_effort_of_its_own_model(self):
        for path, line, block in self._claude_args_blocks():
            where = f"{Path(path).name}:{line}"
            model_steps = self._model_steps(block)
            effort_steps = set(self._EFFORT_ARG.findall(block))
            if model_steps:
                self.assertEqual(
                    model_steps, effort_steps,
                    f"{where}: passes --model from {sorted(model_steps)} but the "
                    f"effort argument comes from {sorted(effort_steps)} — the "
                    "level must be the one computed for the model this step runs",
                )
            else:
                # A literal / input model (the trial run) still asks for a level.
                self.assertTrue(
                    effort_steps,
                    f"{where}: no effort argument — a run that sets none takes "
                    "Opus 5.5's own default, which is `medium`",
                )

    def test_every_step_that_chooses_a_model_also_emits_its_effort(self):
        writes_model = re.compile(r'echo "model(?:_arg)?=')
        for path in WORKFLOWS:
            text = Path(path).read_text()
            steps = re.split(r"\n(?=      - name: )", text)
            for step in steps:
                if not writes_model.search(step):
                    continue
                name = (re.search(r"- name: (.+)", step) or [None, "?"])[1]
                self.assertIn(
                    'echo "effort_arg=', step,
                    f"{Path(path).name}: step {name!r} writes a model output "
                    "with no effort_arg beside it",
                )

    def test_no_workflow_hardcodes_an_effort_level(self):
        # Same rule as the model id (test_model_config.py:
        # test_no_workflow_hardcodes_a_model_id): the level is config, read
        # through the selector, never a literal a file can drift on.
        literal = re.compile(r"--effort\s+(?!\$)")
        for path in WORKFLOWS:
            for i, line in enumerate(Path(path).read_text().splitlines(), 1):
                self.assertIsNone(
                    literal.search(line),
                    f"{Path(path).name}:{i}: hardcoded effort level — declare it "
                    "in config/models.yaml and read it through model_fallback",
                )


if __name__ == "__main__":
    unittest.main()
