"""The model files as they shipped on 2026-10-11, for the tests that pin
behavior on one particular file (DRE-6899).

The CEO changes a group's default model from the console, so the committed
`config/models.yaml` leads each ladder with whatever he last saved. A test of
how selection, the policy, the change script, the adoption's edit or the
effort table behaves reads the frozen copy under
`tests/fixtures/model-config-2026-10-11/` through this module instead, and a
console save can never turn it red. `tests/fixtures/model-config-2026-10-11/README.md`
names the commit the copy was taken from.

The scripts read their config off their own file's path, so running the real
code on the copy means a throwaway tree: `fixture_tree` lays the copy into
`config/` beside the scripts, and `fixture_selector` imports the selector from
such a tree under a name of its own, leaving the module every other test
imports alone.
"""
from __future__ import annotations

import atexit
import importlib.util
import shutil
import subprocess
import sys
import tempfile
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "model-config-2026-10-11"

#: Where each fixture file sits in a tree.
PLACES = {
    "models.yaml": "config/models.yaml",
    "model-prices.yaml": "config/model-prices.yaml",
    "agents.yaml": "agents.yaml",
    "model-choices.json": "config/model-choices.json",
}

#: What a fixture tree carries from the repository: the selector, the
#: generator and the change script it renders a refused move's reason with,
#: and the routing snapshot a `projects:` key is validated against (not frozen:
#: the fixture's one project exception names a repo it has always held).
COPIED = (
    "scripts/model_fallback.py",
    "scripts/sync_model_config.py",
    "scripts/model_config_change.py",
    "scripts/github_output.py",
    "config/repo-map.json",
)


def fixture_path(name: str = "models.yaml") -> Path:
    return FIXTURE / name


def fixture_text(name: str = "models.yaml") -> str:
    return fixture_path(name).read_text()


def fixture_config() -> dict:
    """The 2026-10-11 `config/models.yaml`, parsed."""
    return yaml.safe_load(fixture_text())


def fixture_prices() -> dict:
    """The 2026-10-11 declared prices, as `model_fallback.declared_prices`
    reads them."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import model_fallback

    return model_fallback.declared_prices(fixture_path("model-prices.yaml"))


def rung_ids(rungs) -> list[str]:
    return [rung["model"] if isinstance(rung, dict) else rung for rung in rungs or []]


def first_models(raw: dict) -> dict[str, str]:
    """Group → the first model of its ladder, as `raw` writes it."""
    return {kind: rung_ids(raw["ladders"][spec["ladder"]])[0]
            for kind, spec in raw["kinds"].items()}


def fixture_tree(dest: Path, extra: tuple[str, ...] = (), *, sync: bool = True) -> Path:
    """`dest` holding the fixture's four files where the scripts read them, the
    repo paths in `COPIED` and any `extra` ones. With `sync`, the
    generator runs once so `scripts/model_fallback.py`'s generated literal is
    the fixture's too, and the tree passes `--check`."""
    for rel in (*COPIED, *extra):
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / rel, out)
    for name, rel in PLACES.items():
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(FIXTURE / name, out)
    if sync:
        proc = subprocess.run(
            [sys.executable, str(dest / "scripts" / "sync_model_config.py")],
            capture_output=True, text=True)
        assert proc.returncode == 0, (proc.stdout, proc.stderr)
    return dest


@lru_cache(maxsize=None)
def _shared_tree() -> Path:
    base = Path(tempfile.mkdtemp(prefix="model-config-2026-10-11-"))
    atexit.register(shutil.rmtree, base, ignore_errors=True)
    return fixture_tree(base)


def shared_tree() -> Path:
    """One fixture tree per test process, for tests that only read it."""
    return _shared_tree()


@lru_cache(maxsize=None)
def fixture_selector():
    """`scripts/model_fallback.py` imported from the shared fixture tree, under
    the name `model_fallback_2026_10_11` — its config is the fixture's, and the
    `model_fallback` every other test imports is untouched. It imports nothing
    of the repository's, which is what makes a second copy safe. Cached, so it
    is one module per process and a test that clears its availability cache
    clears this one."""
    path = shared_tree() / "scripts" / "model_fallback.py"
    spec = importlib.util.spec_from_file_location("model_fallback_2026_10_11", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
