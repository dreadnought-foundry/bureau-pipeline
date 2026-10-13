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

THE LADDERS IT HOLDS ARE THE ONES THE READER ANSWERS (DRE-6971). The choices
leave `config/models.yaml` (DRE-6983): a run selects on the home, else on the
defaults in the code (DRE-6939). So the ladders and project choices checked
here are read through `model_fallback.CONFIG`, twice — on the defaults in the
code, with no home set, and with `BUREAU_MODEL_HOME_FILE` naming a document
rendered by `model_home.render_defaults()` — and never out of the file. A
model only the home names stays in `AHEAD_OF_ADOPTION` until the defaults in
the code name it: `model_fallback.KNOWN_MODELS`, which the first table's rows
are held to, is code-side and reads no home.
"""

import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / ".github" / "actions" / "install-claude-code" / "action.yml"

sys.path.insert(0, str(ROOT / "scripts"))

import model_fallback as mf  # noqa: E402
import model_home  # noqa: E402

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


def _choices_read(document: dict | None = None) -> list[dict]:
    """The choices the reader answers, read through `model_fallback.CONFIG`
    twice (DRE-6971): with no home set, which is the defaults in the code, and
    with `BUREAU_MODEL_HOME_FILE` naming `document` — by default the one
    `render_defaults()` renders. The name and the read are put back as they
    were found."""
    reads = []
    saved = os.environ.get(model_home.FILE_ENV)
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "model-home.json"
        home.write_text(json.dumps(document or model_home.render_defaults()))
        try:
            for path in (None, home):
                os.environ.pop(model_home.FILE_ENV, None)
                if path is not None:
                    os.environ[model_home.FILE_ENV] = str(path)
                mf.reset_home()
                status = mf.use_home()
                assert status == ("not set" if path is None else "read"), status
                reads.append({"ladders": {name: list(models) for name, models
                                          in mf.CONFIG["ladders"].items()},
                              "projects": {slug: dict(entry) for slug, entry
                                           in mf.CONFIG["projects"].items()}})
        finally:
            os.environ.pop(model_home.FILE_ENV, None)
            if saved is not None:
                os.environ[model_home.FILE_ENV] = saved
            mf.reset_home()
    return reads


def _canonical_ladders(document: dict | None = None) -> dict[str, list[str]]:
    """Every ladder either read answers, each one's models in order."""
    ladders: dict[str, list[str]] = {}
    for read in _choices_read(document):
        for name, models in read["ladders"].items():
            ladders[name] = list(dict.fromkeys([*ladders.get(name, []), *models]))
    return ladders


def _canonical_exceptions() -> dict[str, list[str]]:
    """Every `projects.<slug>.<kind>` model either read answers, keyed by that
    path (DRE-6785): since 2026-10-10 an exception may name a model on no
    ladder, so the ladder check alone no longer covers what a run selects."""
    exceptions: dict[str, list[str]] = {}
    for read in _choices_read():
        for slug, entry in read["projects"].items():
            for kind, model in (entry or {}).items():
                path = f"projects.{slug}.{kind}"
                exceptions[path] = list(dict.fromkeys([*exceptions.get(path, []), model]))
    return exceptions


def _unsupported(ladders: dict[str, list[str]], pinned: str,
                 prefix: str = "ladders.") -> list[str]:
    """Every model the pin cannot run, named `<prefix><key>` — `ladders.<name>`
    for a ladder, and `""` for `_canonical_exceptions`' already-full paths."""
    remedy = ("keep the model off the ladder" if prefix == "ladders."
              else "choose another model for that project")
    found = []
    for name, models in ladders.items():
        for model in models:
            floor = _minimums().get(model)
            if floor and _version(pinned) < _version(floor):
                found.append(
                    f"{prefix}{name}: {model} needs Claude Code {floor} or newer, "
                    f"and the fleet installs {pinned} "
                    "(.github/actions/install-claude-code `version` default) — "
                    "every run that selects it dies with a 400. Raise the pin "
                    f"first (DRE-4862), or {remedy}."
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
                f"{model} is in MINIMUM_CLAUDE_CODE but the defaults in the "
                "code do not know it — check the id, or move the row to "
                "AHEAD_OF_ADOPTION",
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

    def test_the_ladders_held_are_the_ones_the_reader_answers(self):
        # Read through the reader, never out of config/models.yaml (DRE-6971):
        # every ladder of the defaults in the code is here, model for model.
        ladders = _canonical_ladders()
        for name, models in mf.code_defaults()["ladders"].items():
            self.assertEqual(ladders.get(name), models, name)

    def test_a_model_the_home_puts_on_a_ladder_is_held_too(self):
        # The read with the document named is one of the two the check walks,
        # so a model only the home puts on the Builders ladder is in it.
        doc = model_home.render_defaults()
        doc["groups"]["workhorse"]["models"].append("claude-fable-5-1")
        self.assertNotIn("claude-fable-5-1", mf.code_defaults()["ladders"]["workhorse"])
        self.assertIn("claude-fable-5-1", _canonical_ladders(doc)["workhorse"])

    def test_every_model_on_every_ladder_runs_on_the_pinned_claude_code(self):
        errors = _unsupported(_canonical_ladders(), _pinned_version())
        self.assertEqual(errors, [], "\n".join(errors))

    def test_the_generated_fallback_ladders_run_on_it_too(self):
        # The defaults in the code, which a run selects on when no home
        # answers, are ladders the fleet can run on as well — read through
        # their accessor, whatever the literal behind it is called.
        ladders = mf.code_defaults()["ladders"]
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


class ProjectExceptionSupportTest(unittest.TestCase):
    """A project's exception may name a model on no ladder (DRE-6785), so a
    hand-edited `projects.<slug>.<kind>` is held to the pin here, in CI. The
    console's paths refuse such a model before the policy is asked."""

    def test_every_project_exception_runs_on_the_pinned_claude_code(self):
        errors = _unsupported(_canonical_exceptions(), _pinned_version(), prefix="")
        self.assertEqual(errors, [], "\n".join(errors))

    def test_an_exception_the_pin_cannot_run_is_refused_by_its_path(self):
        # One release past the live pin, never a literal: `claude_code_pin.py
        # apply` raises this file's pin, and a fixed floor it reaches stops
        # being one the pin cannot run.
        pinned = _pinned_version()
        major, minor, patch = _version(pinned)
        floor = f"{major}.{minor}.{patch + 1}"
        AHEAD_OF_ADOPTION["claude-test-9"] = floor
        try:
            exceptions = {**_canonical_exceptions(),
                          "projects.atlas.workhorse": ["claude-test-9"]}
            errors = _unsupported(exceptions, pinned, prefix="")
        finally:
            del AHEAD_OF_ADOPTION["claude-test-9"]
        self.assertEqual(len(errors), 1, errors)
        self.assertTrue(errors[0].startswith(
            f"projects.atlas.workhorse: claude-test-9 needs Claude Code {floor}"),
            errors[0])
        self.assertIn(pinned, errors[0])


if __name__ == "__main__":
    unittest.main()
