"""Every model on a ladder is one the pinned Claude Code can run (DRE-4852).

What this pins shut
-------------------
DRE-4836 (PR #508, merged 2026-09-24 21:00 PT) put `claude-opus-5-5` on top of
the workhorse ladder and on the advisory and judgement ladders' Opus rung. The
fleet's Claude Code was then pinned at 2.1.263 by
`.github/actions/install-claude-code`, and the API refused the new model from
that version:

    API Error: 400 Claude Code 2.1.263 does not support this model;
    version 2.1.280 or newer is required.

Every bureau-pipeline run that selected Opus 5.5 died on that line, with an
empty `modelUsage` and nothing else wrong. Nothing in the suite could see it:
the ladder validated, the prices validated, the effort level validated. What
no test asked was whether the binary the fleet installs can run the model the
ladder names.

So the question is asked here, as data: a model with a known minimum Claude
Code version may sit on a ladder only while the pinned installer meets it.
Raising the pin is what lets Opus 5.5 back on, and DRE-3417 did that on
2026-09-25 — 2.1.282, from `anthropics/claude-code-action` v1.0.234 — putting
the model back on the ladders in the same pull request. This file is what
fails if a model ever goes on a ladder before the pin can run it.

DRE-5116 (2026-09-28) added the second row, and it is a different kind of
floor. 2.1.282 did RUN `claude-sonnet-5-5` — the API accepted it, and a live
trial passed — but that binary had no registry entry for the model and ran it
on its unknown-model defaults: a 200K context window and a 32K output ceiling
(trial run 36479502126), where the model has 1M and 128K. The critic tops the
advisory ladder on it, so that is a fifth of its context gone with nothing
failing. 2.1.284 is the first release that knows the model (trial run
36481112286: contextWindow 1,000,000). The row names the oldest Claude Code
that runs the model AS the fleet means to run it, and the pin moved to 2.1.284
(claude-code-action v1.0.236) in the same PR.

The table below is the only place a minimum is written. Add a row when the API
starts refusing a model from an older Claude Code, with the version its 400
names.

DRE-5121 made the raise automatic, and the order it happens in needs a second
table. The model adoption workflow raises the pin FIRST, in its own trial-gated
pull request (`scripts/claude_code_pin.py apply`), and adopts the model on its
next run. For that day the candidate is on no ladder and `config/models.yaml`
does not know it, so its minimum cannot sit in `MINIMUM_CLAUDE_CODE`, whose rows
must name a model the config knows. It goes in `AHEAD_OF_ADOPTION` instead, and
the ladder check reads both — so the row guards the ladder from the moment the
adoption puts the model on one.
"""

import re
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "models.yaml"
INSTALLER = ROOT / ".github" / "actions" / "install-claude-code" / "action.yml"

sys.path.insert(0, str(ROOT / "scripts"))

import model_fallback as mf  # noqa: E402

# model id → the oldest Claude Code the API accepts it from, as the refusal
# itself names it. Opus 5.5's is from bureau-pipeline QA Review runs
# 36096103941 and 36098144053 (2026-09-24 21:50 and 22:20 PT).
MINIMUM_CLAUDE_CODE = {
    "claude-opus-5-5": "2.1.280",
    # Not a refusal: 2.1.282 runs it, on a 200K window. 2.1.284 is the first
    # Claude Code whose registry carries it (1M native, 128K output) — read off
    # the published binaries and confirmed by live trial run 36481112286.
    "claude-sonnet-5-5": "2.1.284",
}

# model id → the Claude Code an automatic pin raise was made for, ahead of the
# model's adoption (DRE-5121): the id is a Models API id no ladder names YET.
# `claude_code_pin.py apply` writes a row here for a model the config does not
# know, and into MINIMUM_CLAUDE_CODE for one it does.
AHEAD_OF_ADOPTION = {
}

_EXACT_VERSION = re.compile(r"^\d+\.\d+\.\d+$")


def _version(text: str) -> tuple[int, ...]:
    """`"2.1.263"` → `(2, 1, 263)`. Compared as numbers, never as strings:
    `"2.1.1000" < "2.1.280"` is True lexically and wrong."""
    return tuple(int(part) for part in text.split("."))


def _minimums() -> dict[str, str]:
    """Both tables, as the ladder check reads them. A model in both is held to
    the higher floor."""
    merged = dict(AHEAD_OF_ADOPTION)
    for model, floor in MINIMUM_CLAUDE_CODE.items():
        if model not in merged or _version(floor) > _version(merged[model]):
            merged[model] = floor
    return merged


def _pinned_version() -> str:
    """The version the shared install step hands the native installer."""
    action = yaml.safe_load(INSTALLER.read_text())
    return str(action["inputs"]["version"]["default"]).strip()


def _canonical_ladders() -> dict[str, list[str]]:
    cfg = yaml.safe_load(CONFIG.read_text())
    return {
        name: [rung["model"] if isinstance(rung, dict) else rung for rung in rungs]
        for name, rungs in cfg["ladders"].items()
    }


def _unsupported(ladders: dict[str, list[str]], pinned: str) -> list[str]:
    found = []
    for name, models in ladders.items():
        for model in models:
            floor = _minimums().get(model)
            if floor and _version(pinned) < _version(floor):
                found.append(
                    f"ladders.{name}: {model} needs Claude Code {floor} or newer, "
                    f"and the fleet installs {pinned} "
                    "(.github/actions/install-claude-code `version` default) — "
                    "every run that selects it dies with a 400. Raise the pin "
                    "first (DRE-4862), or keep the model off the ladder."
                )
    return found


class PinnedVersionTest(unittest.TestCase):
    """The comparison only means something against an exact version."""

    def test_the_installer_pins_an_exact_version(self):
        pinned = _pinned_version()
        self.assertRegex(
            pinned, _EXACT_VERSION,
            "the install step must pin an exact Claude Code version; a channel "
            "such as `stable` or `latest` cannot be checked against a minimum",
        )

    def test_versions_compare_as_numbers(self):
        self.assertLess(_version("2.1.263"), _version("2.1.280"))
        self.assertGreater(_version("2.1.1000"), _version("2.1.280"))
        self.assertEqual(_version("2.1.280"), _version("2.1.280"))


class MinimumTableTest(unittest.TestCase):
    """A row that names nothing real is a guard that can never fire."""

    def test_every_row_is_a_model_this_config_knows(self):
        for model in MINIMUM_CLAUDE_CODE:
            self.assertIn(
                model, mf.KNOWN_MODELS,
                f"{model} is in MINIMUM_CLAUDE_CODE but config/models.yaml "
                "does not know it — check the id",
            )

    def test_every_minimum_is_an_exact_version(self):
        for model, floor in {**MINIMUM_CLAUDE_CODE, **AHEAD_OF_ADOPTION}.items():
            self.assertRegex(floor, _EXACT_VERSION, model)

    def test_every_ahead_row_is_a_model_id(self):
        # No config to check it against yet: the shape of an Anthropic id is
        # what is left to hold a typo to.
        for model in AHEAD_OF_ADOPTION:
            self.assertRegex(model, r"^claude-[a-z0-9][a-z0-9.-]*$")

    def test_an_ahead_row_guards_the_ladder_once_the_model_is_on_it(self):
        # The point of the table: the adoption that follows the raise cannot
        # land the model on a pin older than the one raised for it.
        AHEAD_OF_ADOPTION["claude-test-9"] = "2.1.290"
        try:
            errors = _unsupported({"workhorse": ["claude-test-9"]}, "2.1.284")
        finally:
            del AHEAD_OF_ADOPTION["claude-test-9"]
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("2.1.290", errors[0])


class LadderSupportTest(unittest.TestCase):
    """The check the card asks for: nothing on a ladder the binary cannot run."""

    def test_every_model_on_every_ladder_runs_on_the_pinned_claude_code(self):
        errors = _unsupported(_canonical_ladders(), _pinned_version())
        self.assertEqual(errors, [], "\n".join(errors))

    def test_the_generated_fallback_ladders_run_on_it_too(self):
        # The literal `_load_config` falls back to when the YAML is unreadable
        # is a ladder the fleet can run on as well.
        ladders = mf._FALLBACK_MODEL_CONFIG["ladders"]
        errors = _unsupported(ladders, _pinned_version())
        self.assertEqual(errors, [], "\n".join(errors))

    def test_the_check_fires_on_the_shape_that_broke(self):
        # The exact config DRE-4836 shipped, against the pin it shipped with.
        broke = {
            "workhorse": ["claude-opus-5-5", "claude-opus-5", "claude-sonnet-5"],
            "advisory": ["claude-sonnet-5", "claude-opus-5-5"],
            "judgement": ["claude-fable-5-1", "claude-opus-5-5", "claude-sonnet-4-6"],
        }
        errors = _unsupported(broke, "2.1.263")
        self.assertEqual(len(errors), 3, errors)
        self.assertTrue(all("2.1.280" in e and "2.1.263" in e for e in errors), errors)

    def test_a_pin_at_the_minimum_clears_it(self):
        ladders = {"workhorse": ["claude-opus-5-5", "claude-opus-5"]}
        self.assertEqual(_unsupported(ladders, "2.1.280"), [])


if __name__ == "__main__":
    unittest.main()
