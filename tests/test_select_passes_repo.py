"""DRE-6637 — every workflow step that picks a model names its project.

DRE-6634 gave `model_fallback.py select`, `planning_classify.py classify` and
`planner_capacity.py decide` a `--repo` flag: a project's exception in
`config/models.yaml` leads the walk only on a run that says which project it is
serving. This holds every invocation of those three in `.github/workflows/` to
passing it.

The sites are DISCOVERED, never listed: every step of every workflow is read,
its backslash continuations joined, and each invocation found by regex. A
hand-kept list would prove only that the sites somebody remembered still carry
the flag; the site nobody remembered is the one a project's exception never
reaches.

Every site passes `--repo "$GITHUB_REPOSITORY"`, because a reusable workflow
runs in the calling repo's context and the stub lives in the repo whose card it
serves. The one exception is the groomer's verifier: one groomer run serves
cards from many repos, so its host repo is the wrong project there, and the
step reads the card's own repo off the matrix row as `REPOSITORY`. The
Triage Repair run (DRE-7179) is the same shape: one run here repairs cards
from every repo, and its planner selects for the card's own repo, read off the
matrix row's `repo`.

The scenario at the end runs the selector CLI the way those steps do, on this
repo's own `owner/name`, with the availability probe stubbed
(`BUREAU_FAKE_AVAILABLE`) so nothing reaches the network.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
MODEL_FALLBACK = ROOT / "scripts" / "model_fallback.py"

# Each invocation is the script name, an optional closing quote (qa-review runs
# the scripts from "$PIPELINE_DIR"/scripts/…), then the subcommand.
INVOCATIONS = {
    "select": re.compile(r'model_fallback\.py"?\s+select\b'),
    "classify": re.compile(r'planning_classify\.py"?\s+classify\b'),
    "decide": re.compile(r'planner_capacity\.py"?\s+decide\b'),
}
# The least each kind may number, so a deleted site is caught too.
AT_LEAST = {"select": 13, "classify": 1, "decide": 4}

FLEET_FLAG = '--repo "$GITHUB_REPOSITORY"'
GROOMER_FLAG = '--repo "$REPOSITORY"'
GROOMER_REPOSITORY = "${{ matrix.repository }}"
#: The workflows whose one run serves cards from many repos, and the matrix
#: field each binds REPOSITORY to.
MANY_REPOS = {"groomer.yml": GROOMER_REPOSITORY,
              "triage-repair.yml": "${{ matrix.repo }}"}


def _join_continuations(script: str) -> str:
    """One shell command per line: a trailing backslash joins the next line."""
    return re.sub(r"\\\n\s*", " ", script)


def _steps(workflow: dict):
    for job_name, job in (workflow.get("jobs") or {}).items():
        for index, step in enumerate((job or {}).get("steps") or []):
            yield job_name, index, step


def _sites() -> list[dict]:
    """Every invocation of the three CLIs in every workflow step's `run:`."""
    found = []
    for path in WORKFLOWS:
        workflow = yaml.safe_load(path.read_text())
        for job_name, index, step in _steps(workflow):
            run = step.get("run")
            if not isinstance(run, str):
                continue
            for line in _join_continuations(run).splitlines():
                if line.lstrip().startswith("#"):
                    continue
                for kind, pattern in INVOCATIONS.items():
                    match = pattern.search(line)
                    if match:
                        found.append({
                            "kind": kind,
                            "file": path.name,
                            "where": f"{path.name} job {job_name} step "
                                     f"{step.get('name') or index}",
                            "command": line[match.start():],
                            "run": run,
                            "env": step.get("env") or {},
                        })
    return found


SITES = _sites()


def _expected_flag(site: dict) -> str:
    if site["kind"] == "select" and site["file"] in MANY_REPOS:
        return GROOMER_FLAG
    return FLEET_FLAG


def test_the_walk_finds_at_least_every_site_the_card_counted():
    counts = {kind: sum(1 for s in SITES if s["kind"] == kind) for kind in INVOCATIONS}
    for kind, least in AT_LEAST.items():
        assert counts[kind] >= least, (
            f"found {counts[kind]} `{kind}` invocation(s) in .github/workflows/, "
            f"expected at least {least} — a site was deleted or the walk missed it"
        )


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_the_step_walk_misses_no_invocation_in_the_file(path):
    """The YAML walk reads only steps' `run:` blocks; a raw-text count of the
    same patterns proves no invocation sits anywhere the walk does not look."""
    text = _join_continuations(path.read_text())
    for kind, pattern in INVOCATIONS.items():
        raw = sum(len(pattern.findall(line)) for line in text.splitlines()
                  if not line.lstrip().startswith("#"))
        walked = sum(1 for s in SITES if s["file"] == path.name and s["kind"] == kind)
        assert walked == raw, (
            f"{path.name}: {raw} `{kind}` invocation(s) in the text, "
            f"{walked} found in steps' run blocks"
        )


@pytest.mark.parametrize("site", SITES, ids=lambda s: f"{s['where']}:{s['kind']}")
def test_every_site_passes_its_project(site):
    flag = _expected_flag(site)
    assert flag in site["command"], (
        f"{site['where']}: `{site['kind']}` does not pass {flag} — a project's "
        f"exception never reaches this run.\n  {site['command']}"
    )
    assert site["command"].count("--repo") == 1, (
        f"{site['where']}: `{site['kind']}` passes --repo more than once.\n"
        f"  {site['command']}"
    )


@pytest.mark.parametrize("site", SITES, ids=lambda s: f"{s['where']}:{s['kind']}")
def test_the_flag_sits_on_its_own_continuation_line(site):
    """Its own line, so no existing line of the step changes — the role still
    follows `select` where WorkflowModelPinTest looks for it."""
    lines = [line.strip() for line in site["run"].splitlines() if "--repo" in line]
    assert lines, f"{site['where']}: no --repo line"
    for line in lines:
        assert line == f"{_expected_flag(site)} \\", (
            f"{site['where']}: --repo is not on a continuation line of its own: {line!r}"
        )


def test_the_groomer_verifier_names_the_card_s_repo_not_the_host():
    groomer = [s for s in SITES if s["file"] == "groomer.yml" and s["kind"] == "select"]
    assert groomer, "groomer.yml has no `model_fallback.py select` site"
    for site in groomer:
        assert GROOMER_FLAG in site["command"], (
            f"{site['where']}: the groomer serves many repos — its verifier must "
            f"pass the matrix row's repo, {GROOMER_FLAG}\n  {site['command']}"
        )
        assert FLEET_FLAG not in site["command"], (
            f"{site['where']}: $GITHUB_REPOSITORY is the groomer's host repo, "
            f"never the card's"
        )
        assert site["env"].get("REPOSITORY") == GROOMER_REPOSITORY, (
            f"{site['where']}: the step's env must bind REPOSITORY to "
            f"{GROOMER_REPOSITORY}, got {site['env'].get('REPOSITORY')!r}"
        )


def test_select_on_this_repo_names_its_project_in_the_note(tmp_path):
    """The scenario: the CLI the steps call, given the `owner/name` a step's
    $GITHUB_REPOSITORY carries, resolves it to the slug and says so."""
    note = tmp_path / "why.txt"
    env = dict(os.environ, BUREAU_FAKE_AVAILABLE=json.dumps({}))
    proc = subprocess.run(
        [sys.executable, str(MODEL_FALLBACK), "select", "engineer",
         "--repo", "dreadnought-foundry/bureau-pipeline",
         "--explain-file", str(note)],
        capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip(), "select printed no model"
    assert "project bureau-pipeline" in note.read_text(), note.read_text()


def test_the_triage_repair_planner_names_the_card_s_repo_not_the_host():
    """DRE-7179: the run lives here and repairs cards from every repo, so
    $GITHUB_REPOSITORY is bureau-pipeline, never the card's project."""
    sites = [s for s in SITES if s["file"] == "triage-repair.yml" and s["kind"] == "select"]
    assert sites, "triage-repair.yml has no `model_fallback.py select` site"
    for site in sites:
        assert GROOMER_FLAG in site["command"]
        assert FLEET_FLAG not in site["command"]
        assert site["env"].get("REPOSITORY") == MANY_REPOS["triage-repair.yml"]
