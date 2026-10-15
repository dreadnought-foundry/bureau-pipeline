"""DRE-7284 — proof runs get their own Proof model group.

`agents.proof` names a `proof` kind, labeled Proof, whose ladder lists the
Builders' models in the same order. Nothing about a proof run's model changes
on the day it lands: the top of the Proof ladder is the top of the Builders
ladder. What changes is that the proof model can move alone — a reorder of the
Proof ladder moves the model chosen for `proof` and leaves every build role
where it was.

Each selection runs the selector's CLI against a temporary copy of the real
config, built and regenerated the way `tests/test_model_choice_levers.py`
builds one, with every model up (`BUREAU_FAKE_AVAILABLE`) and no network
call. Every model id a check expects is read off the copy, never typed
(DRE-6853).

Run: cd bureau-pipeline && python3 -m pytest tests/test_proof_group.py -q
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]

HOME_NAMES = ("BUREAU_MODEL_HOME_TOKEN", "BUREAU_MODEL_HOME_FILE")

# The config files a tree carries beside its scripts, as the levers test
# copies them: `model-choices.json` is one of the generator's targets.
CONFIG_FILES = ("models.yaml", "model-prices.yaml", "repo-map.json", "model-choices.json")

# The six build roles the Builders group names, which a Proof-only change
# must leave alone.
BUILD_ROLES = ("engineer", "frontend", "devops", "database-architect", "fixer", "repairer")


def real_raw() -> dict:
    return yaml.safe_load((ROOT / "config" / "models.yaml").read_text())


def ladder_ids(raw: dict, kind: str) -> list[str]:
    """The model ids `kind` walks in a raw config."""
    spec = raw["kinds"][kind]
    name = spec["ladder"] if isinstance(spec, dict) else spec
    return [r["model"] if isinstance(r, dict) else r for r in raw["ladders"][name]]


def build_tree(dest: Path, edit: Callable[[dict], None] | None = None) -> Path:
    """Today's scripts and a copy of the real config at `dest`, its
    `models.yaml` edited by `edit` when one is given, and the generated
    mirrors regenerated from it by the real generator."""
    dest = Path(dest)
    (dest / "scripts").mkdir(parents=True, exist_ok=True)
    (dest / "config").mkdir(parents=True, exist_ok=True)
    for path in sorted((ROOT / "scripts").glob("*.py")):
        shutil.copy2(path, dest / "scripts" / path.name)
    for name in CONFIG_FILES:
        shutil.copy2(ROOT / "config" / name, dest / "config" / name)
    shutil.copy2(ROOT / "agents.yaml", dest / "agents.yaml")
    if edit is not None:
        raw = yaml.safe_load((dest / "config" / "models.yaml").read_text())
        edit(raw)
        (dest / "config" / "models.yaml").write_text(yaml.safe_dump(raw, sort_keys=False))
    proc = subprocess.run(
        [sys.executable, str(dest / "scripts" / "sync_model_config.py")],
        cwd=dest, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return dest


def tree_raw(tree: Path) -> dict:
    return yaml.safe_load((tree / "config" / "models.yaml").read_text())


def child_env() -> dict:
    """This environment with no home name, and every model up."""
    env = {k: v for k, v in os.environ.items() if k not in HOME_NAMES}
    env["BUREAU_FAKE_AVAILABLE"] = "{}"
    return env


def select(tree: Path, role: str) -> tuple[str, str]:
    """`(model, note)` from the tree's selector CLI, the way a workflow's
    Select model step calls it."""
    note = tree / "note.txt"
    if note.exists():
        note.unlink()
    proc = subprocess.run(
        [sys.executable, str(tree / "scripts" / "model_fallback.py"), "select", role,
         "--explain-file", str(note)],
        cwd=tree, capture_output=True, text=True, env=child_env(), timeout=120)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip(), note.read_text().rstrip("\n")


def last_rung_first(raw: dict) -> None:
    """The Proof ladder of a raw config with its last rung moved first."""
    rungs = raw["ladders"][raw["kinds"]["proof"]["ladder"]]
    rungs.insert(0, rungs.pop())


@pytest.fixture(scope="module")
def base(tmp_path_factory) -> Path:
    """The copy as committed, regenerated."""
    return build_tree(tmp_path_factory.mktemp("base"))


@pytest.fixture(scope="module")
def reordered(tmp_path_factory) -> Path:
    """The copy with the Proof ladder's last rung moved first, regenerated."""
    return build_tree(tmp_path_factory.mktemp("reordered"), last_rung_first)


def test_the_proof_kind_is_labeled_proof_and_walks_its_own_ladder():
    raw = real_raw()
    assert raw["kinds"]["proof"]["ladder"] == "proof"
    assert raw["kinds"]["proof"]["label"] == "Proof"
    assert "proof" in raw["ladders"]
    assert raw["agents"]["proof"] == "proof"


def test_the_proof_ladder_is_the_builders_models_in_the_same_order():
    raw = real_raw()
    assert ladder_ids(raw, "proof") == ladder_ids(raw, "workhorse")


def test_a_proof_run_selects_the_builders_first_model_from_the_proof_kind(base):
    top = ladder_ids(tree_raw(base), "workhorse")[0]
    chosen, note = select(base, "proof")
    assert chosen == top
    assert f"{top} chosen for proof (proof kind)" in note


def test_reordering_the_proof_ladder_moves_proof_and_no_build_role(base, reordered):
    moved = ladder_ids(tree_raw(reordered), "proof")[0]
    assert moved == ladder_ids(tree_raw(base), "proof")[-1]

    before, _ = select(base, "proof")
    after, note = select(reordered, "proof")
    assert after == moved
    assert after != before
    assert f"{moved} chosen for proof (proof kind)" in note

    for role in BUILD_ROLES:
        assert select(reordered, role)[0] == select(base, role)[0], role
