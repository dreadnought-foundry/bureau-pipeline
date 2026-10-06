"""bureau-pipeline's own workflows read the fleet's runner variables (DRE-5948).

Until DRE-5948 every job in a workflow that runs only in THIS repo was pinned
to the literal `ubuntu-latest` (DRE-3350): the repo is public, its minutes
bill at $0, and the org's Default runner group refused public repos, so a job
pointed at the Mac mini pool would have queued forever. The CEO reversed that
on 2026-10-05 (~20:16 PT, "yes, add the bureau-pipeline to run-ons"): this
repo was the fleet's largest GitHub user — 1,017 minutes from 16:00 to 20:10
PT that day — and its routing variables point at RunsOn since 20:17 PT. Its
reusables already followed them; twenty-nine `runs-on:` lines in its own
workflows did not.

The rule this file pins:

* No `runs-on: ubuntu-latest` line in `.github/workflows/` is bare. A job that
  must stay GitHub-hosted says why on the line itself,
  `runs-on: ubuntu-latest  # github-hosted: <the reason>`, so a grep finds
  every pin and its reason together. A pin with no stated reason moves. The
  check reads the file TEXT, because YAML drops the comment.
* Every job of this repo's own workflows reads one of the three chains the
  reusables read, byte for byte (`tests/test_runs_on_switchable.py`): the test
  suites — `tests.yml` and the two `smoke-setup-*-cached` workflows — read the
  build chain, `BUREAU_CI_RUNS_ON` first; the integration harness, which
  drives real agent runs for up to three hours, reads the long chain; every
  other job is a script of seconds to minutes and reads the short chain. A job
  added later without a lane decision lands on the short chain's expectation
  and fails here if it reads anything else.
* With no variable set every one of those jobs renders `ubuntu-latest`,
  computed below rather than asserted, so the change is inert until a variable
  is set and deleting the variables puts every job back on GitHub.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_runs_on_switchable import (  # noqa: E402
    BUILD_SWITCHABLE,
    HOSTED,
    SHORT_SWITCHABLE,
    SWITCHABLE,
    _is_reusable,
    _load,
    _runner_jobs,
    _workflow_files,
)
from test_short_runs_on_lane import render_runs_on  # noqa: E402

# The test suites: whole files, because every job in them is a suite or the
# roll-up of one.
OWN_BUILD_FILES = {
    "tests.yml",
    "smoke-setup-node-cached.yml",
    "smoke-setup-python-cached.yml",
}
# The jobs on the long chain, by name: the harness drives real agent runs
# with a 180-minute ceiling, which is the agent lane's shape, not a chore's.
OWN_LONG_JOBS = {"harness.yml": {"harness"}}
# Jobs pinned to `ubuntu-latest` with a reason on the line. None today; a pin
# is named here AND carries its `# github-hosted:` comment.
OWN_PINNED_JOBS: dict[str, set[str]] = {}

# A `runs-on:` whose value is the hosted label, quoted or bracketed or not.
_HOSTED_LINE = re.compile(
    r"^\s*runs-on:\s*\[?\s*['\"]?ubuntu-latest['\"]?\s*\]?\s*(?P<comment>#.*)?$"
)
_REASON = re.compile(r"#\s*github-hosted:\s*\S")


def bare_hosted_lines(text: str) -> list[tuple[int, str]]:
    """(line number, line) for every hosted `runs-on:` without a reason."""
    found = []
    for number, line in enumerate(text.splitlines(), start=1):
        match = _HOSTED_LINE.match(line)
        if match and not _REASON.search(match.group("comment") or ""):
            found.append((number, line.strip()))
    return found


def _own_files() -> list[Path]:
    return [p for p in _workflow_files() if not _is_reusable(_load(p))]


def own_expected_runs_on(name: str, job_id: str) -> str:
    """The one expression an own-workflow job's `runs-on` must be."""
    if job_id in OWN_PINNED_JOBS.get(name, set()):
        return HOSTED
    if name in OWN_BUILD_FILES:
        return BUILD_SWITCHABLE
    if job_id in OWN_LONG_JOBS.get(name, set()):
        return SWITCHABLE
    return SHORT_SWITCHABLE


# --------------------------------------------------------------------------
# no bare hosted line
# --------------------------------------------------------------------------
def test_the_checker_finds_a_bare_hosted_line() -> None:
    text = (
        "jobs:\n"
        "  a:\n"
        "    runs-on: ubuntu-latest\n"
        "  b:\n"
        "    runs-on: 'ubuntu-latest'   # cheap\n"
        "  c:\n"
        "    runs-on: [ubuntu-latest]\n"
    )
    assert [n for n, _ in bare_hosted_lines(text)] == [3, 5, 7]


def test_the_checker_admits_a_pin_that_names_its_reason() -> None:
    text = (
        "jobs:\n"
        "  a:\n"
        "    runs-on: ubuntu-latest  # github-hosted: proves GitHub's cache\n"
        "  b:\n"
        f"    runs-on: {SHORT_SWITCHABLE}\n"
    )
    assert bare_hosted_lines(text) == []


def test_the_checker_refuses_a_pin_with_an_empty_reason() -> None:
    text = "jobs:\n  a:\n    runs-on: ubuntu-latest  # github-hosted:\n"
    assert [n for n, _ in bare_hosted_lines(text)] == [3]


@pytest.mark.parametrize("path", _workflow_files(), ids=lambda p: p.name)
def test_no_workflow_has_a_bare_hosted_runs_on(path: Path) -> None:
    bare = bare_hosted_lines(path.read_text())
    assert not bare, (
        f"{path.name} pins `ubuntu-latest` without saying why. Read a runner "
        f"variable with the `ubuntu-latest` fallback instead, or keep the pin "
        f"with `# github-hosted: <reason>` on the line: "
        + "; ".join(f"line {n}: {line}" for n, line in bare)
    )


# --------------------------------------------------------------------------
# every own job reads its lane
# --------------------------------------------------------------------------
@pytest.mark.parametrize("path", _own_files(), ids=lambda p: p.name)
def test_every_own_job_reads_its_lane(path: Path) -> None:
    wrong = [
        f"{job_id}: {runs_on!r} (expected "
        f"{own_expected_runs_on(path.name, job_id)!r})"
        for job_id, runs_on in _runner_jobs(_load(path))
        if runs_on != own_expected_runs_on(path.name, job_id)
    ]
    assert not wrong, (
        f"{path.name} runs only in bureau-pipeline; its jobs read the fleet's "
        f"runner variables on the build, long or short chain: {wrong}"
    )


def test_the_lane_table_names_real_own_jobs() -> None:
    """A renamed job or file must not leave the table pointing at nothing —
    that would silently move the job to the short chain's expectation."""
    own = {p.name: dict(_runner_jobs(_load(p))) for p in _own_files()}
    for name in OWN_BUILD_FILES:
        assert own.get(name), f"{name} is not an own workflow with jobs"
    for table in (OWN_LONG_JOBS, OWN_PINNED_JOBS):
        for name, jobs in table.items():
            assert jobs <= set(own.get(name, {})), (name, jobs)


def test_a_pinned_own_job_says_why_on_its_line() -> None:
    for name, jobs in OWN_PINNED_JOBS.items():
        text = (WORKFLOWS / name).read_text()
        reasons = sum(
            1 for line in text.splitlines()
            if _HOSTED_LINE.match(line) and _REASON.search(line)
        )
        assert reasons >= len(jobs), f"{name}: {jobs} pinned without a reason"


# --------------------------------------------------------------------------
# inert until a variable is set
# --------------------------------------------------------------------------
@pytest.mark.parametrize("path", _own_files(), ids=lambda p: p.name)
def test_with_no_variables_set_every_own_job_resolves_hosted(path: Path) -> None:
    for job_id, runs_on in _runner_jobs(_load(path)):
        rendered = [runs_on] if runs_on == HOSTED else render_runs_on(runs_on, {})
        assert rendered == [HOSTED], f"{path.name}:{job_id} rendered {rendered}"


_ALL_SET = {
    "BUREAU_CI_RUNS_ON": '["ci-pool"]',
    "BUREAU_RUNS_ON": '["agent-pool"]',
    "BUREAU_SHORT_RUNS_ON": '["short-pool"]',
}


@pytest.mark.parametrize("path", _own_files(), ids=lambda p: p.name)
def test_each_own_job_follows_its_own_variable(path: Path) -> None:
    """With every variable set, a suite lands on CI's pool, the harness on the
    agent pool and a chore on the short pool — the lanes are distinct."""
    want = {
        BUILD_SWITCHABLE: ["ci-pool"],
        SWITCHABLE: ["agent-pool"],
        SHORT_SWITCHABLE: ["short-pool"],
    }
    for job_id, runs_on in _runner_jobs(_load(path)):
        expected = own_expected_runs_on(path.name, job_id)
        if expected == HOSTED:
            continue
        assert render_runs_on(runs_on, _ALL_SET) == want[expected], (
            f"{path.name}:{job_id}"
        )


def test_an_unset_lane_variable_falls_back_to_bureau_runs_on() -> None:
    only_long = {"BUREAU_RUNS_ON": '["agent-pool"]'}
    for expr in (BUILD_SWITCHABLE, SHORT_SWITCHABLE, SWITCHABLE):
        assert render_runs_on(expr, only_long) == ["agent-pool"], expr
