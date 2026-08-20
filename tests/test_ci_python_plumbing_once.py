"""The guard: no job may grow its own pip install back (DRE-2589).

This is the python counterpart of the node guard agent-bureau carries in
test_ci_parallel_suites.py, and it is the thing that keeps the fix from
rotting. The defect it prevents is not hypothetical — it is the state this
card was filed against:

  * agent-bureau's ci.yml installed test tooling in THREE jobs, each with its
    own `pip install` and its own copy of the pins. `pytest==9.1.0` appeared
    twice. Bump one and CI stays green while two suites run different pytest
    majors in the same workflow.
  * this repo's own tests.yml ran three `pip install` lines: one from the
    pinned manifest and two of bare `pyyaml`, while requirements-dev.txt pins
    `PyYAML==6.0.3`. The bare installs took whatever PyPI served that morning,
    so a Dependabot bump of that pin exercised nothing (DRE-2039).

Both are the same defect class as `MAX_WIP` in three files: a fact with more
than one home, kept honest only by nobody happening to edit one copy.

The rules, in order of what they catch:

  1. A workflow may not run `pip install` at all. Installing is the shared
     action's job; a `run:` that installs is a second copy of the plumbing and
     is invisible to `scripts/ci_minutes_report.py` when it hides inside a test
     step.
  2. Every pinned distribution appears in exactly ONE file across the CI
     install surface. Two homes for one pin is the defect itself.
  3. A job that runs pytest must have set its environment up through the shared
     action, so its tooling came from the manifest rather than from the runner
     image or a bare install.

The detector is unit-tested against synthetic violating input at the bottom of
this file. A guard that cannot be shown to fire is a guard nobody can trust —
the Wave 0 lesson about a check whose output nobody reads.
"""

import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
ACTIONS = ROOT / ".github" / "actions"
PYTHON_ACTION_REF = ".github/actions/setup-python-cached"

# The action IS the installer. It is the one file exempt from rule 1 — and the
# exemption is a path, not a pattern, so a second installer cannot inherit it.
INSTALLER = ACTIONS / "setup-python-cached" / "action.yml"

# Any spelling of a pip install that has ever appeared in this estate:
# `pip install`, `pip -q install`, `pip3 install`, `python3 -m pip install`,
# and `uv pip install`.
PIP_INSTALL = re.compile(r"\bpip3?\b(?:\s+-[^\s]+)*\s+install\b")

# A BALANCED quoted span. An `echo "pip install skipped"` is a message, not an
# install, and a guard that fired on it would push the next editor to describe
# the mechanism less precisely — the opposite of what this file is for. Only
# balanced spans are removed, so an unterminated quote leaves the line intact
# and a real install can never hide behind one.
QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")

# A pinned distribution, as written in a requirements file or on a pip command
# line. Deliberately not matching `>=` or `~=`: this guard is about EXACT pins,
# which are the ones that must have exactly one home.
PIN = re.compile(r"(?<![\w.-])([A-Za-z][A-Za-z0-9._-]*)==([0-9][^\s,;'\"]*)")

# Files that can cause a python install. Docs and tests are excluded on
# purpose: a card describing `pytest==9.1.0` in prose, or a fixture asserting on
# one, installs nothing. Narrow enough to stay meaningful, wide enough that a
# new manifest cannot hide.
INSTALL_SURFACE = (
    ".github/workflows/*.yml",
    ".github/actions/*/action.yml",
    ".github/bureau/*",
    "requirements*.txt",
    "*/requirements*.txt",
    "*/*/requirements*.txt",
    "pyproject.toml",
)

# Floor against a vacuous sweep: a path typo that finds nothing would make every
# "no violations" assertion below pass for the wrong reason.
SURFACE_FLOOR = 20


def _normalise(distribution):
    """PyPI treats `PyYAML`, `pyyaml` and `py-yaml` as one name (PEP 503)."""
    return re.sub(r"[-_.]+", "-", distribution).lower()


def _is_committed(path):
    """Exclude runtime checkouts that live inside the workspace but are not
    part of the repo. `.bureau-pipeline/` is this very repo, re-checked-out by
    the agent workflows — counting its requirements-dev.txt would report every
    pin as having two homes, which is a false red with a very confusing
    message. Nothing under a top-level dot-directory other than `.github` is
    swept."""
    top = path.relative_to(ROOT).parts[0]
    return not top.startswith(".") or top == ".github"


def _surface():
    found = []
    for pattern in INSTALL_SURFACE:
        found.extend(p for p in ROOT.glob(pattern) if p.is_file() and _is_committed(p))
    return sorted(set(found))


def _run_scripts(doc):
    """Every `run:` script in a workflow document, as (job, step, script)."""
    for job_name, job in (doc.get("jobs") or {}).items():
        if not isinstance(job, dict):
            continue
        for step in job.get("steps") or []:
            if isinstance(step, dict) and "run" in step:
                yield job_name, str(step.get("name") or step.get("id") or "?"), str(step["run"])


def _code_lines(text):
    """Lines that could actually install something.

    A whole-line `#` comment cannot: both YAML and requirements files use it,
    and the shared action's own header explains this defect by quoting
    `pytest==9.1.0` from agent-bureau's ci.yml. Counting that as a second home
    for the pin would make the guard un-writable — the only fix would be to
    stop documenting what it prevents. Inline trailing comments are NOT
    stripped, deliberately: they sit on a line that does run.
    """
    return [line for line in text.splitlines() if not line.lstrip().startswith("#")]


def find_pip_installs(text):
    """Lines in `text` that install with pip. The detector rule 1 rests on."""
    return [
        line.strip()
        for line in _code_lines(text)
        if PIP_INSTALL.search(QUOTED.sub(" ", line))
    ]


def find_pins(text):
    """Normalised distribution names pinned with `==` in `text`."""
    return {
        _normalise(name)
        for line in _code_lines(text)
        for name, _ in PIN.findall(line)
    }


class SweepIsNotVacuous(unittest.TestCase):
    def test_the_install_surface_is_found(self):
        found = _surface()
        self.assertGreaterEqual(
            len(found),
            SURFACE_FLOOR,
            f"the install-surface sweep found only {len(found)} files — a glob "
            f"went stale and every assertion in this file would pass for the "
            f"wrong reason",
        )

    def test_the_pinned_manifest_is_on_the_surface(self):
        names = {p.name for p in _surface()}
        self.assertIn(
            "requirements-dev.txt",
            names,
            "the repo's pinned manifest is not on the swept surface, so rule 2 "
            "would never see the pins it exists to count",
        )


class NoJobInstallsItsOwnTooling(unittest.TestCase):
    """Rule 1 — the shared action is the only installer."""

    def test_no_workflow_runs_pip_install(self):
        offenders = []
        for path in sorted(WORKFLOWS.glob("*.yml")):
            doc = yaml.safe_load(path.read_text())
            for job, step, script in _run_scripts(doc):
                for line in find_pip_installs(script):
                    offenders.append(f"{path.name}: job {job!r}, step {step!r}: {line}")
        self.assertEqual(
            offenders,
            [],
            "these workflow steps install python packages themselves instead of "
            "calling " + PYTHON_ACTION_REF + " — that is a second copy of the "
            "plumbing, it carries its own pins, and its cost is invisible to "
            "scripts/ci_minutes_report.py when it hides inside a test step:\n  "
            + "\n  ".join(offenders),
        )

    def test_only_the_shared_action_installs(self):
        offenders = []
        for path in sorted(ACTIONS.glob("*/action.yml")):
            if path == INSTALLER:
                continue
            for line in find_pip_installs(path.read_text()):
                offenders.append(f"{path.relative_to(ROOT)}: {line}")
        self.assertEqual(
            offenders, [], f"a second composite action installs python packages: {offenders}"
        )

    def test_the_shared_action_is_actually_the_installer(self):
        # The inverse of the rule above: if the exempt file stopped installing,
        # rule 1 would be trivially satisfiable by installing nowhere at all.
        self.assertTrue(INSTALLER.is_file(), f"{INSTALLER} is missing")
        self.assertTrue(
            find_pip_installs(INSTALLER.read_text()),
            f"{INSTALLER.name} no longer installs anything — every job would "
            f"run against the runner image's python",
        )


class EveryPinHasExactlyOneHome(unittest.TestCase):
    """Rule 2 — the acceptance criterion, mechanically."""

    def test_no_pinned_tool_appears_in_two_files(self):
        homes = {}
        for path in _surface():
            for name in find_pins(path.read_text()):
                homes.setdefault(name, []).append(str(path.relative_to(ROOT)))
        duplicated = {n: sorted(f) for n, f in homes.items() if len(set(f)) > 1}
        self.assertEqual(
            duplicated,
            {},
            "these pins have more than one home, so bumping one leaves the other "
            "silently behind and two jobs run different versions of the same "
            "tool in the same workflow: " + repr(duplicated),
        )

    def test_the_repo_actually_pins_something(self):
        # Without this, a repo that pinned nothing anywhere would pass rule 2.
        pins = set()
        for path in _surface():
            pins |= find_pins(path.read_text())
        self.assertTrue(
            pins,
            "the sweep found no pinned distributions at all — either the "
            "manifest lost its pins or the PIN pattern stopped matching",
        )


class EveryPythonSuiteUsesTheSharedAction(unittest.TestCase):
    """Rule 3 — tooling comes from the manifest, not from the image."""

    @staticmethod
    def _jobs_running_pytest(doc):
        for job_name, job in (doc.get("jobs") or {}).items():
            if not isinstance(job, dict):
                continue
            steps = job.get("steps") or []
            runs_pytest = any(
                isinstance(s, dict) and re.search(r"\bpytest\b", str(s.get("run", "")))
                for s in steps
            )
            if runs_pytest:
                yield job_name, steps

    def test_a_job_that_runs_pytest_set_python_up_through_the_action(self):
        offenders = []
        checked = 0
        for path in sorted(WORKFLOWS.glob("*.yml")):
            doc = yaml.safe_load(path.read_text())
            for job_name, steps in self._jobs_running_pytest(doc):
                checked += 1
                uses = [str(s.get("uses", "")) for s in steps if isinstance(s, dict)]
                if not any(PYTHON_ACTION_REF in u for u in uses):
                    offenders.append(f"{path.name}: job {job_name!r}")
        self.assertEqual(
            offenders,
            [],
            "these jobs run pytest without setting python up through "
            + PYTHON_ACTION_REF
            + " — their tooling comes from the runner image or from an install "
            "this guard cannot see: " + repr(offenders),
        )
        self.assertGreater(
            checked, 0, "no job in this repo runs pytest — the sweep went vacuous"
        )


class TheInstallIsMeasurable(unittest.TestCase):
    """Rule 4 — the cost has to land under a name a human chose.

    Measured, not assumed. GitHub's jobs API reports a composite action as ONE
    step: run 32421767876 called setup-python-cached three times and
    `GET /actions/jobs/96595119245` returned the CALLER's step names
    ("FIRST call — expect a miss and a real install"), never the action's own
    "Install dependencies (pip install)". So the action's internal step name is
    a log affordance, and the thing `scripts/ci_minutes_report.py` can actually
    attribute cost to is the caller's step name.

    That makes the acceptance criterion — "the pip install is a named step, and
    its cost is reported" — a rule about CALLERS: an unnamed `uses:` is
    reported as "Run ./.github/actions/setup-python-cached", which is the same
    unmeasurable state as an install buried inside a test step, just with a
    different label.
    """

    @staticmethod
    def _callers():
        for path in sorted(WORKFLOWS.glob("*.yml")):
            doc = yaml.safe_load(path.read_text())
            for job_name, job in (doc.get("jobs") or {}).items():
                if not isinstance(job, dict):
                    continue
                for step in job.get("steps") or []:
                    if isinstance(step, dict) and PYTHON_ACTION_REF in str(
                        step.get("uses", "")
                    ):
                        yield path.name, job_name, step

    def test_every_caller_names_its_step(self):
        offenders = [
            f"{fname}: job {job!r}"
            for fname, job, step in self._callers()
            if not str(step.get("name") or "").strip()
        ]
        self.assertEqual(
            offenders,
            [],
            "these steps call the shared action without a name:, so the jobs "
            "API reports their cost as 'Run ./.github/actions/"
            "setup-python-cached' — the install stays unattributable, which is "
            "the state this card exists to end: " + repr(offenders),
        )

    def test_the_sweep_found_callers(self):
        self.assertTrue(
            list(self._callers()),
            "no workflow calls the shared action — the rule above is vacuous",
        )


class TheDetectorFires(unittest.TestCase):
    """A guard that cannot be shown to fire is a guard nobody can trust.

    These feed the detectors the exact text this card was filed against, so the
    "no violations" assertions above cannot be passing because the patterns
    match nothing.
    """

    def test_it_catches_every_spelling_of_a_pip_install(self):
        for line in (
            "pip install ruff==0.15.22",
            "pip -q install pyyaml",
            "pip3 install -r requirements.txt",
            "python3 -m pip install --quiet pytest==9.1.0",
            "  pip install --upgrade pip",
        ):
            self.assertTrue(find_pip_installs(line), f"missed: {line!r}")

    def test_it_does_not_fire_on_prose_or_on_other_installers(self):
        for line in (
            "npm ci",
            "# installing from the pinned manifest is what makes a bump real",
            "apt-get install -y jq",
            "echo 'pip is not run here'",
        ):
            self.assertEqual(find_pip_installs(line), [], f"false positive: {line!r}")

    def test_it_catches_the_agent_bureau_duplication(self):
        lint = "pip install ruff==0.15.22"
        backend = "pip install pytest==9.1.0 pytest-asyncio==1.4.0 pytest-cov==7.1.0"
        relay = "pip install pytest==9.1.0 PyYAML==6.0.3"
        self.assertEqual(find_pins(lint), {"ruff"})
        shared = find_pins(backend) & find_pins(relay)
        self.assertEqual(
            shared,
            {"pytest"},
            "the pin detector cannot see the duplicate pytest pin that is the "
            "reason this card exists",
        )

    def test_pin_names_are_normalised_the_way_pypi_normalises_them(self):
        # `PyYAML==6.0.3` in one file and `pyyaml==6.0.3` in another is still
        # two homes for one fact.
        self.assertEqual(find_pins("PyYAML==6.0.3"), find_pins("pyyaml==6.0.3"))
        self.assertEqual(find_pins("pytest_asyncio==1.4.0"), {"pytest-asyncio"})

    def test_a_message_about_pip_is_not_an_install_but_a_real_one_is(self):
        # The smoke workflow asserts "pip install skipped" in an echo. That is
        # a message. An unterminated quote must NOT hide a real install.
        self.assertEqual(
            find_pip_installs('echo "warm call: hit, pip install skipped"'), []
        )
        self.assertEqual(
            find_pip_installs('{ echo "::error::pip install did nothing"; exit 1; }'), []
        )
        self.assertTrue(find_pip_installs('pip install -r "$MANIFEST"'))
        self.assertTrue(find_pip_installs('echo "starting\npip install evil'))

    def test_a_commented_out_pin_is_not_a_home_but_a_live_one_is(self):
        # The distinction the sweep depends on. If comment-stripping were
        # applied too widely, rule 2 would stop seeing real pins entirely.
        self.assertEqual(find_pins("  # bumped from pytest==9.1.0 last week"), set())
        self.assertEqual(find_pins("pytest==9.1.0  # bumped last week"), {"pytest"})
        self.assertEqual(find_pip_installs("  # pip install pytest"), [])
        self.assertTrue(find_pip_installs("pip install pytest  # tooling"))

    def test_a_range_is_not_a_pin(self):
        self.assertEqual(find_pins("pytest>=9.1"), set())
        self.assertEqual(find_pins("pytest~=9.1.0"), set())


if __name__ == "__main__":
    unittest.main()
