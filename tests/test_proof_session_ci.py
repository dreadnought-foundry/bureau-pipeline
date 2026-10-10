"""The suite's CI has the browser `tests/test_proof_session.py` needs (DRE-6047).

The Playwright package is already in the suite's manifest: DRE-6025 pinned
`playwright==<version>` in `requirements-dev.txt`, which every part of
`tests.yml`'s `unit` job installs. Chromium is not a pip package, so the
`unit` job installs it in ONE step, between choosing the part's files and
running them, and only in the part whose file list holds the browser tests.

That is a script's decision, never an `on: paths:` filter, and it fails loud:
the step has no `if:` and no `continue-on-error`, so a part that holds the file
and cannot install the browser is red, never skipped.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
TESTS_YML = ROOT / ".github" / "workflows" / "tests.yml"
REQUIREMENTS = ROOT / "requirements-dev.txt"

STEP = "Install the proof browser (only in the part that runs its tests)"
BEFORE = "Choose this part's test files"
AFTER = "Unit tests"
BROWSER_TESTS = "tests/test_proof_session.py"
INSTALL = "python3 -m playwright install --with-deps chromium"


def _unit_steps() -> list:
    doc = yaml.safe_load(TESTS_YML.read_text(encoding="utf-8"))
    return doc["jobs"]["unit"]["steps"]


def _step(name: str) -> dict:
    found = [s for s in _unit_steps() if s.get("name") == name]
    assert len(found) == 1, f"the unit job has {len(found)} steps named {name!r}"
    return found[0]


def test_requirements_pin_playwright_exactly_once():
    lines = REQUIREMENTS.read_text(encoding="utf-8").splitlines()
    pins = [line for line in lines if re.match(r"\s*playwright\s*==", line, re.I)]
    assert len(pins) == 1, pins


def test_the_unit_job_installs_the_browser_between_choosing_and_running():
    names = [s.get("name") for s in _unit_steps()]
    assert STEP in names, f"no step named {STEP!r} in the unit job: {names}"
    assert names.index(BEFORE) < names.index(STEP) < names.index(AFTER), names
    assert names.index(STEP) == names.index(BEFORE) + 1
    assert names.index(AFTER) == names.index(STEP) + 1


def test_the_step_installs_chromium_only_where_the_browser_tests_run():
    run = _step(STEP)["run"]
    assert BROWSER_TESTS in run
    assert INSTALL in run
    # It decides from the part's own file list, the one `Unit tests` reads.
    assert "unit-part-files.txt" in run
    assert "unit-part-files.txt" in _step(AFTER)["run"]
    # The grep matches a whole line, so a file whose name merely contains the
    # browser test file's name never pulls the browser in.
    assert re.search(r"grep\s+-qx", run)


def test_the_step_fails_loud_and_is_never_skipped():
    step = _step(STEP)
    assert "if" not in step, "a part that holds the browser tests must install it"
    assert not step.get("continue-on-error"), "a failed install must be red"
    assert "|| true" not in step["run"]


def test_no_other_job_installs_the_browser():
    doc = yaml.safe_load(TESTS_YML.read_text(encoding="utf-8"))
    for name, job in doc["jobs"].items():
        if name == "unit":
            continue
        text = yaml.safe_dump(job.get("steps", []))
        assert "playwright install" not in text, name
