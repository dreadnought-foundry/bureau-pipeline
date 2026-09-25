"""Claude Opus 5.5 tops the workhorse ladder, and every run on it asks for
effort `high` (DRE-4836).

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

`tests/test_model_config.py` is the sibling that pins the canonical file and
its generated mirrors; this one pins the effort level and its wiring.
"""

import glob
import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "models.yaml"
PRICES = ROOT / "config" / "model-prices.yaml"
MODEL_FALLBACK = ROOT / "scripts" / "model_fallback.py"
WORKFLOWS = sorted(glob.glob(str(ROOT / ".github" / "workflows" / "*.yml")))

sys.path.insert(0, str(ROOT / "scripts"))

import model_fallback as mf  # noqa: E402

OPUS55 = "claude-opus-5-5"
OPUS = "claude-opus-5"
SONNET5 = "claude-sonnet-5"
SONNET46 = "claude-sonnet-4-6"
FABLE51 = "claude-fable-5-1"

# The one level the CEO named. Spelled once here so a test that drifts to
# another level is a diff a reviewer sees rather than a constant moving.
HIGH = "high"


def _canonical() -> dict:
    return yaml.safe_load(CONFIG.read_text())


def _ladder(cfg: dict, name: str) -> list:
    return [rung["model"] for rung in cfg["ladders"][name]]


class LadderTest(unittest.TestCase):
    """The ladders as the card adopts them, and the file still validating."""

    def test_workhorse_ladder_is_opus_5_5_then_opus_5_then_sonnet_5(self):
        # The "newer versions auto" rule (CEO, 2026-09-14): a same-family newer
        # version at the same or lower price is adopted through a normal PR.
        # Opus 5 keeps the rung below it, so a run that cannot get 5.5 falls
        # back to the model the fleet has been building on.
        cfg = _canonical()
        self.assertEqual(
            _ladder(cfg, cfg["default_ladder"]), [OPUS55, OPUS, SONNET5]
        )

    def test_the_opus_rung_of_the_other_two_ladders_is_opus_5_5(self):
        # Their FIRST rungs are unchanged — Sonnet 5 still tops the advisory
        # ladder and Fable 5.1 the judgement one. Only the Opus fallback moves.
        cfg = _canonical()
        self.assertEqual(_ladder(cfg, "advisory"), [SONNET5, OPUS55])
        self.assertEqual(_ladder(cfg, "judgement"), [FABLE51, OPUS55, SONNET46])

    def test_review_separation_sends_a_sonnet_5_build_to_opus_5_5(self):
        # DRE-3892's carry-forward, arriving: a same-family adoption moves the
        # overlapping rung on both ladders at once, and a rule left naming the
        # old id is refused as stale. Reviewers stay on Sonnet 5 for everything
        # else — only the substitute for a Sonnet-5 BUILD is named here.
        cfg = _canonical()
        rules = cfg["review_separation"]["rules"]
        self.assertEqual([r["built_on"] for r in rules], [SONNET5])
        self.assertEqual(rules[0]["reviewers_use"], OPUS55)

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

    def test_the_new_rungs_cite_the_card_and_the_price(self):
        cfg = _canonical()
        reasons = " ".join(
            rung["reason"]
            for rungs in cfg["ladders"].values()
            for rung in rungs
            if rung["model"] == OPUS55
        )
        self.assertIn("DRE-4836", reasons)
        self.assertIn("$4", reasons)


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

    def test_opus_5_5_is_no_dearer_than_the_opus_5_it_replaces(self):
        # The whole adoption rule in one assertion: same family, newer, and at
        # or below the rung it takes over on BOTH sides.
        prices = mf.declared_prices()
        new, old = prices[OPUS55], prices[OPUS]
        self.assertLessEqual(new["input"], old["input"])
        self.assertLessEqual(new["output"], old["output"])


class DeclaredEffortTest(unittest.TestCase):
    """The level itself: data in the canonical file, one reader."""

    def test_the_config_declares_high_for_opus_5_5(self):
        cfg = _canonical()
        self.assertEqual(cfg.get("effort", {}).get(OPUS55), HIGH)

    def test_effort_for_reads_it(self):
        self.assertEqual(mf.effort_for(OPUS55), HIGH)

    def test_a_model_with_no_declared_effort_gets_no_argument(self):
        # The adoption changes Opus 5.5 and nothing else: every other model
        # keeps whatever the harness would have used, so nobody's spend moves
        # on a card this one was not about.
        for model in (SONNET5, OPUS, FABLE51, SONNET46):
            self.assertIsNone(mf.effort_for(model), model)

    def test_the_generated_mirror_carries_the_level(self):
        # The degrade path is the whole point of the mirror: a checkout whose
        # YAML will not parse still runs the ladders — and must still ask for
        # the level, or the fall-back-to-literal path silently drops to medium.
        self.assertEqual(mf._FALLBACK_MODEL_CONFIG["effort"].get(OPUS55), HIGH)

    def test_an_unknown_level_is_refused(self):
        cfg = _canonical()
        cfg["effort"] = {OPUS55: "maximum-effort"}
        errors = mf.policy_errors(cfg)
        self.assertTrue(any("maximum-effort" in e for e in errors), errors)

    def test_an_effort_entry_for_a_model_nothing_runs_is_refused(self):
        # A level declared for an id no ladder names is a level nothing applies
        # — and the likeliest cause is a typo in the id the fleet is meant to
        # be running at `high`.
        cfg = _canonical()
        cfg["effort"] = dict(cfg.get("effort") or {}, **{"claude-opus-5-55": HIGH})
        errors = mf.policy_errors(cfg)
        self.assertTrue(any("claude-opus-5-55" in e for e in errors), errors)


class SelectorCliTest(unittest.TestCase):
    """The CLI the workflows actually call."""

    def _run(self, *args, available=None):
        env = dict(os.environ)
        env["BUREAU_FAKE_AVAILABLE"] = json.dumps(available or {})
        proc = subprocess.run(
            [sys.executable, str(MODEL_FALLBACK), *args],
            capture_output=True, text=True, env=env,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout.strip()

    def test_effort_command_prints_the_level(self):
        self.assertEqual(self._run("effort", OPUS55), HIGH)

    def test_effort_command_prints_nothing_for_an_undeclared_model(self):
        # Empty, exit 0 — the shell writes `effort_arg=` and the step passes no
        # argument, which is how a model with no declared level stays untouched.
        self.assertEqual(self._run("effort", SONNET5), "")
        self.assertEqual(self._run("effort", "claude-who"), "")

    def test_select_writes_the_effort_of_the_model_it_chose(self):
        with tempdir() as tmp:
            path = tmp / "effort.txt"
            model = self._run(
                "select", "engineer", "--effort-file", str(path),
                available={OPUS55: True},
            )
            self.assertEqual(model, OPUS55)
            self.assertEqual(path.read_text().strip(), HIGH)

    def test_select_writes_an_empty_effort_when_it_fell_to_another_rung(self):
        # The fall is the case that matters: Opus 5.5 unavailable, the run
        # lands on Opus 5, and it must NOT inherit 5.5's level.
        with tempdir() as tmp:
            path = tmp / "effort.txt"
            model = self._run(
                "select", "engineer", "--effort-file", str(path),
                available={OPUS55: False, OPUS: True},
            )
            self.assertEqual(model, OPUS)
            self.assertEqual(path.read_text().strip(), "")


class WorkflowWiringTest(unittest.TestCase):
    """Every run that reaches a model passes the level with it.

    Read off the workflow files rather than listed here: a step added later
    that chooses a model and forgets the level is the whole failure mode, and a
    hand-written list of today's steps would not see it.
    """

    # `--model <something>` inside a claude_args block, and the step id that
    # `something` comes from when it is an interpolated step output.
    _MODEL_ARG = re.compile(r"--model\s+\$\{\{\s*steps\.([A-Za-z0-9_]+)\.outputs")
    _MODEL_ARG_WHOLE = re.compile(r"\$\{\{\s*steps\.([A-Za-z0-9_]+)\.outputs\.model_arg")
    _EFFORT_ARG = re.compile(r"\$\{\{\s*steps\.([A-Za-z0-9_]+)\.outputs\.effort_arg")

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
            model_steps = set(self._MODEL_ARG.findall(block)) | set(
                self._MODEL_ARG_WHOLE.findall(block)
            )
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


class _tempdir:
    def __enter__(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        return Path(self._tmp.name)

    def __exit__(self, *exc):
        self._tmp.cleanup()
        return False


def tempdir():
    return _tempdir()


if __name__ == "__main__":
    unittest.main()
