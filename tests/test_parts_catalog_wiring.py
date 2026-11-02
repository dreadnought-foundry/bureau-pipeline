"""DRE-8720 (DRE-8708 rule 7): a UI-building build run starts with the parts catalog.

`standards/design-system.md` § "Builders get the parts list up front" says
every agent that builds UI receives the repo's catalog of existing parts, with
the file each ships at, computed at run time by
`design_parts.py catalog` (DRE-8717). `agent-task.yml` builds a card under two
UI-building roles, `engineer` and `frontend`, and its `Assemble agent context`
step is where their context is written. These tests execute that step's block
AS WRITTEN — the way `tests/test_critic_context_root.py` runs the critic's —
against throwaway product repos:

  * a repo with a declaration gets the block, every part with its "ships at"
    path, and the step's log carries the `design-parts:` and `PARTS CATALOG:`
    lines;
  * a repo with no design system gets `PARTS CATALOG: none — <reason>`;
  * a `devops` run gets no block at all;
  * a pipeline checkout with no catalog script still succeeds, and the context
    carries a `PARTS CATALOG: UNKNOWN — …` line in its place.

Both briefs then say what to do with it: pick from the catalog first.
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

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "agent-task.yml"
sys.path.insert(0, str(ROOT / "scripts"))

import step_shell  # noqa: E402

CARD = "DRE-77"
CONTEXT = ".bureau-pipeline/agent-context.md"
UI_ROLES = ("engineer", "frontend")
HEADING = "## The parts catalog — pick from it first"


def _ctx_step() -> dict:
    steps = yaml.safe_load(step_shell.workflow_source(WORKFLOW))["jobs"]["execute"]["steps"]
    return next(s for s in steps if s.get("id") == "ctx")


def _script(role: str) -> str:
    values = {"steps.gate.outputs.role": role}
    return re.sub(r"\$\{\{\s*([^}]+?)\s*\}\}", lambda m: values[m.group(1)],
                  step_shell.step_shell(_ctx_step()))


def _pipeline(tmp_path: Path, *, catalog_script: bool) -> Path:
    """A pipeline checkout made of links into this one — so the context the
    step writes lands in the throwaway tree — with or without the catalog
    script."""
    pipeline = tmp_path / "pipeline"
    scripts = pipeline / "scripts"
    scripts.mkdir(parents=True)
    for entry in ROOT.iterdir():
        if entry.name not in ("scripts", ".git"):
            (pipeline / entry.name).symlink_to(entry)
    for entry in (ROOT / "scripts").iterdir():
        if catalog_script or entry.name != "design_parts.py":
            (scripts / entry.name).symlink_to(entry)
    return pipeline


def _repo_with_parts(work: Path) -> None:
    decl = work / ".github" / "bureau" / "design-system.json"
    decl.parent.mkdir(parents=True)
    decl.write_text(json.dumps({"parts": {
        "atoms": ["src/ui/atoms"], "molecules": ["src/ui/molecules"],
        "organisms": [], "templates": []}}))
    atoms = work / "src" / "ui" / "atoms"
    atoms.mkdir(parents=True)
    (atoms / "Button.tsx").write_text("export const Button = () => null;\n")
    (atoms / "Button.test.tsx").write_text("// a test, never a part\n")
    notice = work / "src" / "ui" / "molecules" / "Notice"
    notice.mkdir(parents=True)
    (notice / "index.tsx").write_text("export const Notice = () => null;\n")


def run_ctx(tmp_path: Path, role: str, *, parts: bool = True,
            catalog_script: bool = True) -> tuple[subprocess.CompletedProcess, str]:
    """Run the step's shell as Actions runs it (`bash -e`) in a product repo;
    answer (process, the assembled context)."""
    work = tmp_path / "work"
    work.mkdir()
    (work / ".bureau-pipeline").symlink_to(_pipeline(tmp_path, catalog_script=catalog_script))
    if parts:
        _repo_with_parts(work)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "python3").symlink_to(sys.executable)
    env = {
        "PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}",
        "HOME": str(tmp_path),
        "CARD": CARD,
        "BUREAU_CARD_SNAPSHOT": str(tmp_path / "absent-snapshot.json"),
    }
    script = _script(role)
    assert "${{" not in script, "an unresolved expression would reach the shell"
    proc = subprocess.run(  # nosec B603 B607 — this repo's own step, fixed argv
        ["bash", "-e", "-c", script], cwd=work, env=env,
        capture_output=True, text=True, timeout=120, check=False,
    )
    context = work / CONTEXT
    return proc, context.read_text() if context.exists() else ""


@pytest.mark.parametrize("role", UI_ROLES)
def test_a_ui_role_gets_the_repos_parts_with_where_each_ships(tmp_path, role):
    proc, text = run_ctx(tmp_path, role)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "PARTS CATALOG: 2 part(s) from .github/bureau/design-system.json" in text
    assert "- Button (atoms) — ships at src/ui/atoms/Button.tsx" in text
    assert "- Notice (molecules) — ships at src/ui/molecules/Notice/index.tsx" in text
    assert "Button.test" not in text
    # After what people said on the card, never before the brief.
    assert text.index("SPOKEN STATUS:") < text.index("PARTS CATALOG:")
    # The log: the script's own stderr line, and the echoed block head.
    assert "design-parts: 2 part(s) from .github/bureau/design-system.json" in proc.stderr
    assert re.search(r"^PARTS CATALOG: 2 part\(s\)", proc.stdout, re.M), proc.stdout


@pytest.mark.parametrize("role", UI_ROLES)
def test_a_repo_with_no_design_system_reads_none(tmp_path, role):
    proc, text = run_ctx(tmp_path, role, parts=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    lines = [l for l in text.splitlines() if l.startswith("PARTS CATALOG:")]
    assert lines == ["PARTS CATALOG: none — no .github/bureau/design-system.json "
                     "and no design/LOCK.json"]
    assert "design-parts: none — " in proc.stderr


def test_a_devops_run_gets_no_catalog(tmp_path):
    proc, text = run_ctx(tmp_path, "devops")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "===== BEGIN briefs/devops.md =====" in text
    assert "PARTS CATALOG:" not in text
    assert "design-parts:" not in proc.stderr


@pytest.mark.parametrize("role", UI_ROLES)
def test_a_missing_catalog_script_never_fails_the_step(tmp_path, role):
    proc, text = run_ctx(tmp_path, role, catalog_script=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    lines = [l for l in text.splitlines() if l.startswith("PARTS CATALOG:")]
    assert len(lines) == 1, text[-600:]
    assert lines[0].startswith("PARTS CATALOG: UNKNOWN — "), lines[0]
    assert "- Button" not in text


@pytest.mark.parametrize("brief", ["frontend.md", "engineer.md"])
def test_both_briefs_say_pick_from_the_catalog_first(brief):
    text = (ROOT / "briefs" / brief).read_text()
    assert text.count(HEADING) == 1, f"{brief} must carry {HEADING!r} once"
    section = text.split(HEADING, 1)[1].split("\n## ", 1)[0]
    flat = " ".join(section.split())
    assert "`PARTS CATALOG:`" in flat
    assert "ships at" in flat
    assert re.search(r"[Pp]ick from the catalog first", flat)
    assert "variant" in flat
    assert re.search(r"new part only when nothing in the catalog", flat)
    assert "declared folder" in flat and "tokens" in flat
    assert "standards/design-system.md" in flat
    assert "UI code in a repo whose design system exists" in flat
