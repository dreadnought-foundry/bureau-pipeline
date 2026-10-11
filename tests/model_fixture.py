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
"""
from __future__ import annotations

import hashlib
import importlib.util
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


def _rung_ids(rungs) -> list[str]:
    return [r["model"] if isinstance(r, dict) else r for r in rungs or []]


def ladder(cfg: dict, kind: str) -> list[str]:
    """The rung ids of the ladder `kind` walks in the raw config `cfg`."""
    return _rung_ids(cfg["ladders"][cfg["kinds"][kind]["ladder"]])


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


def fixture_module(tree: Path, name: str = "model_fallback"):
    """`tree/scripts/<name>.py`, imported under a name of its own, so its
    module-level config is the tree's and the repo's imported module of the
    same name is left alone.

    The tree's own siblings answer its imports while it loads: whatever the
    session already imported under those names is set aside for the import and
    put back after it.
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
    finally:
        sys.path.remove(str(scripts))
        for m in [m for m in sys.modules if is_sibling(m)]:
            del sys.modules[m]
        sys.modules.update(saved)
    return module


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
