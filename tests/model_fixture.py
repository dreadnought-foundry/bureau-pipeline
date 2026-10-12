"""The frozen model files, and a throwaway tree built from them (DRE-6859).

The console rewrites `config/models.yaml` on every save — which model leads a
group, which group an agent is in, which agents exist, which projects have a
model of their own — so a rule test that read the committed file went red on a
legitimate save. A RULE of the policy or of selection is proved against the
frozen copy in `tests/fixtures/model-config-frozen/` instead (its README holds
the two rules, and the refresh rule for when today's code stops accepting it).

This is a plain module, not a pytest fixture: the policy and selector tests are
`unittest.TestCase` classes. Every test reaches the fixture through it, and
derives every model id it expects from `fixture_config()` through `ladder()`,
never from a literal.

Since the selector selects on the home or the defaults in the code (DRE-6939),
a scenario is a home document rather than an edit of a tree's `models.yaml`:
`fixture_home()` is the frozen choices as one, `lead_with`, `move_agent` and
`with_project` edit a copy, and `home_env` or `fixture_module(..., doc=...)`
hands it to a tree (DRE-6972).
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(ROOT / "scripts"))
from unit_test_parts import discover  # noqa: E402

FIXTURE_NAME = "model-config-frozen"
FIXTURE_DIR = ROOT / "tests" / "fixtures" / FIXTURE_NAME

# What every rule test says when the fixture stops regenerating, so the whole
# suite fails with one sentence that names the remedy, not a traceback inside
# the generator.
STALE = (
    "the frozen model fixture no longer regenerates under today's code — "
    "re-freeze it in this pull request "
    f"({(FIXTURE_DIR / 'README.md').relative_to(ROOT).as_posix()}, the refresh rule)"
)

# A test file that names any of these can read the model files: the three files
# themselves, and the selector, the generator and the console's change script
# that read them. A test that names a rung id is in the set too
# (`model_reading_tests`), because it reaches the selector some other way.
MODEL_FILE_NAMES = (
    "models.yaml", "agents.yaml", "model-choices.json",
    "model_fallback", "sync_model_config", "model_config_change",
)


class FixtureStale(Exception):
    """The generator refused the frozen files under today's code."""


def fixture_config() -> dict:
    """The frozen `models.yaml`, parsed."""
    return yaml.safe_load(fixture_text())


def fixture_prices() -> dict:
    """The `prices` map of the frozen `model-prices.yaml`."""
    return yaml.safe_load((FIXTURE_DIR / "model-prices.yaml").read_text())["prices"]


def fixture_text() -> str:
    """The frozen `models.yaml` as text, for the tests that edit it as text."""
    return (FIXTURE_DIR / "models.yaml").read_text()


def fixture_home() -> dict:
    """The frozen home document, parsed — a fresh copy on every call."""
    return json.loads((FIXTURE_DIR / "home.json").read_text())


def _rung_ids(rungs) -> list[str]:
    return [r["model"] if isinstance(r, dict) else r for r in rungs or []]


def ladder(cfg_or_doc: dict, kind: str) -> list[str]:
    """The rung ids `kind` walks: `groups.<kind>.models` of a home document,
    or the ladder `kind` names in a raw config of the old shape."""
    if "ladders" not in cfg_or_doc:
        return _rung_ids(cfg_or_doc["groups"][kind]["models"])
    return _rung_ids(cfg_or_doc["ladders"][cfg_or_doc["kinds"][kind]["ladder"]])


# --------------------------------------------------------------------------- #
# Editing a home document — each returns a new one                             #
# --------------------------------------------------------------------------- #

def lead_with(doc: dict, group: str, model: str) -> dict:
    """`doc` with `model` first in `group`, the group's other models after it
    in their order."""
    new = copy.deepcopy(doc)
    models = new["groups"][group]["models"]
    new["groups"][group]["models"] = [model, *(m for m in models if m != model)]
    return new


def move_agent(doc: dict, agent: str, group: str) -> dict:
    """`doc` with `agent` in `group`."""
    new = copy.deepcopy(doc)
    new["agents"][agent] = group
    return new


def with_project(doc: dict, slug: str, group: str, model: str) -> dict:
    """`doc` with project `slug` trying `model` first in `group`, beside any
    choice the project already has."""
    new = copy.deepcopy(doc)
    new.setdefault("projects", {}).setdefault(slug, {})[group] = model
    return new


HOME_FILE_ENV = "BUREAU_MODEL_HOME_FILE"


def home_env(tree: Path, doc: dict, **extra) -> dict:
    """`doc` written to `tree/config/model-home.json`, and a copy of this
    process's environment with `BUREAU_MODEL_HOME_FILE` naming it, plus
    `extra` (such as `BUREAU_FAKE_AVAILABLE`). A value that is not text is
    written as JSON. This process's own environment is left alone."""
    path = Path(tree) / "config" / "model-home.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2) + "\n")
    env = dict(os.environ)
    env[HOME_FILE_ENV] = str(path)
    env.update({key: value if isinstance(value, str) else json.dumps(value)
                for key, value in extra.items()})
    return env


def _copy_into(dest: Path) -> None:
    """Today's scripts, the frozen model files, and the repo's project slugs
    (a routing fact, not a console choice) into `dest`."""
    (dest / "scripts").mkdir(parents=True, exist_ok=True)
    (dest / "config").mkdir(parents=True, exist_ok=True)
    for path in sorted((ROOT / "scripts").glob("*.py")):
        shutil.copy2(path, dest / "scripts" / path.name)
    for name in ("models.yaml", "model-prices.yaml"):
        shutil.copy2(FIXTURE_DIR / name, dest / "config" / name)
    shutil.copy2(FIXTURE_DIR / "agents.yaml", dest / "agents.yaml")
    shutil.copy2(ROOT / "config" / "repo-map.json", dest / "config" / "repo-map.json")


def fixture_tree(dest: Path) -> Path:
    """A throwaway tree at `dest` running today's code on the frozen files, its
    generated regions regenerated from them. Raises `FixtureStale` when the
    generator refuses them."""
    dest = Path(dest)
    _copy_into(dest)
    proc = subprocess.run(
        [sys.executable, str(dest / "scripts" / "sync_model_config.py")],
        cwd=dest, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-15:])
        raise FixtureStale(
            f"{STALE}\n`sync_model_config.py` exited {proc.returncode}:\n{tail}")
    return dest


def fixture_module(tree: Path, name: str = "model_fallback", doc: dict | None = None):
    """`tree/scripts/<name>.py`, imported under a name of its own, so its
    module-level config is the tree's and the repo's imported module of the
    same name is left alone.

    The tree's own siblings answer its imports while it loads: whatever the
    session already imported under those names is set aside for the import and
    put back after it.

    With `doc`, the module has already read it: the selector reads the home at
    its first choice call, not at import (DRE-6939), so `doc` is written with
    `home_env`, `BUREAU_MODEL_HOME_FILE` names it, and the module's
    `use_home()` runs while the siblings are still in place — its reader is
    the tree's `model_home`. The environment is put back before this returns.
    With no `doc` nothing is read here: the module's first choice call reads
    whatever home the environment names, which in the suite is none.
    """
    scripts = (Path(tree) / "scripts").resolve()
    tag = hashlib.sha1(str(scripts).encode()).hexdigest()[:12]
    unique = f"_model_fixture_{tag}_{name}"
    siblings = {path.stem for path in scripts.glob("*.py")}

    def is_sibling(module: str) -> bool:
        return module.split(".")[0] in siblings

    saved = {m: sys.modules.pop(m) for m in list(sys.modules) if is_sibling(m)}
    sys.path.insert(0, str(scripts))
    try:
        spec = importlib.util.spec_from_file_location(unique, scripts / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[unique] = module
        spec.loader.exec_module(module)
        if doc is not None:
            _read_home(module, tree, doc)
    finally:
        sys.path.remove(str(scripts))
        for m in [m for m in sys.modules if is_sibling(m)]:
            del sys.modules[m]
        sys.modules.update(saved)
    return module


def _read_home(module, tree: Path, doc: dict) -> None:
    """`module.use_home()` with `BUREAU_MODEL_HOME_FILE` naming `doc`, the
    name put back as it was afterwards."""
    before = os.environ.get(HOME_FILE_ENV)
    os.environ[HOME_FILE_ENV] = home_env(tree, doc)[HOME_FILE_ENV]
    try:
        module.use_home()
    finally:
        if before is None:
            os.environ.pop(HOME_FILE_ENV, None)
        else:
            os.environ[HOME_FILE_ENV] = before


def model_reading_tests(tree: Path) -> list[Path]:
    """The test files under `tree` that can read the model files: those naming
    one of `MODEL_FILE_NAMES`, or a rung id of `tree/config/models.yaml`.

    The one derivation of that set: the harness (DRE-6865) runs exactly these
    inside each changed copy, and the guard (DRE-6866) scans exactly these for
    pinned ids. A test that reaches the selector through a workflow step and
    asserts a model id is in it whether or not it names a model file.
    """
    tree = Path(tree)
    cfg = yaml.safe_load((tree / "config" / "models.yaml").read_text()) or {}
    rungs = {m for rungs in (cfg.get("ladders") or {}).values() for m in _rung_ids(rungs)}
    tokens = (*MODEL_FILE_NAMES, *sorted(rungs))
    # The files `pytest tests` collects — the directories it skips are skipped
    # here by the same walk — narrowed to `test_*.py`.
    files = [tree / rel for rel in discover(tree) if Path(rel).name.startswith("test_")]
    return sorted(
        path for path in files
        if any(token in path.read_text(encoding="utf-8", errors="replace")
               for token in tokens))
