"""Every workflow that reaches the planner cap hands it the console's number (DRE-5793).

THE CONTRACT. The console publishes how many planners may run at once as the
GitHub Actions repo variable `PLANNER_MAX_RUNNING` on each repo in the map
(epic DRE-5783), and `planner_queue.cap()` reads the environment variable of
the same name, falling back to `config/planner-queue.json` when it is empty or
unset. The plumbing between the two is one top-level `env:` per workflow:

    PLANNER_MAX_RUNNING: ${{ vars.PLANNER_MAX_RUNNING }}

In a reusable workflow `vars` are the CALLER's, so the console's per-repo write
reaches every job in that repo without a per-job line.

WHAT THIS PINS, read off the workflow files themselves:

* the files are DISCOVERED by what they run — any workflow whose code runs
  `planner_queue.py`, `scripts/reconcile.py` or `scripts/groomer.py`, the
  three readers of `cap()` — so a fifth workflow that starts running the sweep
  is caught here rather than remembered by a list;
* each of them carries the line above at the workflow level, exactly;
* no job or step in them sets `PLANNER_MAX_RUNNING` again — an override would
  silently reinstate a number nobody chose.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"

NAME = "PLANNER_MAX_RUNNING"
EXPR = "${{ vars.PLANNER_MAX_RUNNING }}"
READERS = ("planner_queue.py", "scripts/reconcile.py", "scripts/groomer.py")

# The four that run a reader today (DRE-5793). A floor for the discovery, so a
# renamed script cannot make every check below pass by finding nothing.
KNOWN = {"plan.yml", "reconcile.yml", "groomer.yml", "linear-sync.yml"}

# A shell write of the variable inside a `run:` script: `export X=…`,
# `X=… cmd`, or `echo "X=…" >> "$GITHUB_ENV"`.
_SHELL_ASSIGN = re.compile(rf"(?<![\w$]){NAME}=")


def _code(text: str) -> str:
    """The file without its comment lines — a comment that names a reader
    does not run it."""
    return "\n".join(line for line in text.splitlines()
                     if not line.lstrip().startswith("#"))


def runs_a_reader(text: str) -> bool:
    code = _code(text)
    return any(reader in code for reader in READERS)


def _overrides(node, path: str) -> list[str]:
    """Every place under `node` that sets the variable as a mapping key."""
    found = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == NAME:
                found.append(f"{path}.{key} = {value!r}")
            found.extend(_overrides(value, f"{path}.{key}"))
    elif isinstance(node, list):
        for i, item in enumerate(node):
            found.extend(_overrides(item, f"{path}[{i}]"))
    return found


def problems(text: str) -> list[str]:
    """What is wrong with one workflow's hand-off of the cap; empty when right."""
    out = []
    doc = yaml.safe_load(text) or {}
    top = doc.get("env")
    if not isinstance(top, dict) or NAME not in top:
        out.append(f"no top-level env carries {NAME}")
    elif top[NAME] != EXPR:
        out.append(f"top-level {NAME} is {top[NAME]!r}, not {EXPR!r}")
    out.extend(f"job or step sets it: {hit}"
               for hit in _overrides(doc.get("jobs") or {}, "jobs"))
    for line in _code(text).splitlines():
        if _SHELL_ASSIGN.search(line):
            out.append(f"a script writes it: {line.strip()!r}")
    return out


def _discovered() -> dict[str, str]:
    files = sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml"))
    texts = {p.name: p.read_text(encoding="utf-8") for p in files}
    return {name: text for name, text in texts.items() if runs_a_reader(text)}


# ── the live tree ───────────────────────────────────────────────────────────
def test_discovery_finds_at_least_the_four_known_readers():
    found = set(_discovered())
    assert KNOWN <= found, f"discovery lost {sorted(KNOWN - found)}"


def test_every_workflow_that_reaches_the_cap_hands_it_the_callers_variable():
    bad = {name: p for name, text in _discovered().items() if (p := problems(text))}
    assert not bad, bad


def test_the_line_sits_at_the_workflow_level_exactly_as_written():
    for name, text in _discovered().items():
        lines = text.splitlines()
        assert "env:" in lines, f"{name}: no top-level env:"
        assert f"  {NAME}: {EXPR}" in lines, name


# ── the checker catches what it claims to ───────────────────────────────────
_GOOD = f"""\
name: Example
on:
  workflow_call:
env:
  {NAME}: {EXPR}
jobs:
  sweep:
    runs-on: ubuntu-latest
    steps:
      - name: Sweep
        run: python3 .bureau-pipeline/scripts/reconcile.py --promote-only
"""


def test_a_correct_file_has_no_problems():
    assert runs_a_reader(_GOOD)
    assert problems(_GOOD) == []


def test_a_file_without_the_line_is_caught():
    text = _GOOD.replace(f"env:\n  {NAME}: {EXPR}\n", "")
    assert runs_a_reader(text)
    assert problems(text) == [f"no top-level env carries {NAME}"]


def test_a_top_level_number_is_caught():
    text = _GOOD.replace(EXPR, "'4'")
    assert problems(text) == [f"top-level {NAME} is '4', not {EXPR!r}"]


def test_a_job_level_override_is_caught():
    text = _GOOD.replace("    runs-on:", f"    env:\n      {NAME}: '6'\n    runs-on:")
    assert len(problems(text)) == 1
    assert "jobs.sweep.env" in problems(text)[0]


def test_a_step_level_override_is_caught():
    text = _GOOD.replace("        run:", f"        env:\n          {NAME}: '2'\n        run:")
    assert len(problems(text)) == 1
    assert "steps" in problems(text)[0]


def test_a_shell_write_is_caught_and_a_shell_read_is_not():
    write = _GOOD.replace("        run: python3",
                          f'        run: echo "{NAME}=5" >> "$GITHUB_ENV"; python3')
    assert any("a script writes it" in p for p in problems(write))
    read = _GOOD.replace("        run: python3", f'        run: echo "${NAME}"; python3')
    assert problems(read) == []


def test_a_reader_named_only_in_a_comment_is_not_discovered():
    text = "# scripts/groomer.py runs elsewhere\nname: X\non: push\njobs: {}\n"
    assert not runs_a_reader(text)


def test_each_reader_is_discovered():
    for reader in READERS:
        assert runs_a_reader(f"jobs:\n  a:\n    steps:\n      - run: python3 {reader}\n")
