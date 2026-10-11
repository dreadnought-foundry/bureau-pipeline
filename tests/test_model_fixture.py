"""The frozen model fixture and its one helper (DRE-6859).

Every model rule test used to read the committed `config/models.yaml`, so a
legitimate console save (Builders to another model, an agent moved between
groups, a project exception) turned rule tests red. `tests/model_fixture.py`
gives those tests a frozen copy of the three model files, a throwaway tree
built from it under today's code, and the one derivation of which test files
can read the model files at all. These tests prove the helper does what the
sibling cards rely on, and they read no git history: the harness copies the
tree without `.git`.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "scripts"))

import model_fixture  # noqa: E402
from model_fixture import (  # noqa: E402
    FIXTURE_DIR,
    FIXTURE_NAME,
    FixtureStale,
    fixture_config,
    fixture_module,
    fixture_prices,
    fixture_text,
    fixture_tree,
    ladder,
    model_reading_tests,
)

README = FIXTURE_DIR / "README.md"
STALE_SENTENCE = (
    "the frozen model fixture no longer regenerates under today's code — "
    "re-freeze it in this pull request "
    "(tests/fixtures/model-config-frozen/README.md, the refresh rule)"
)


def _ids(rungs) -> list[str]:
    return [r["model"] if isinstance(r, dict) else r for r in rungs]


@pytest.fixture(scope="module")
def tree(tmp_path_factory) -> Path:
    """One fixture tree, shared by the read-only checks below."""
    return fixture_tree(tmp_path_factory.mktemp("fixture") / "tree")


# --------------------------------------------------------------------------- #
# 1. The fixture directory and its README                                      #
# --------------------------------------------------------------------------- #

def test_the_fixture_lives_where_its_name_says():
    assert FIXTURE_NAME == "model-config-frozen"
    assert FIXTURE_DIR == ROOT / "tests" / "fixtures" / FIXTURE_NAME


def test_the_four_files_exist():
    for name in ("models.yaml", "model-prices.yaml", "agents.yaml", "README.md"):
        assert (FIXTURE_DIR / name).is_file(), name


def test_the_readme_names_the_commit_and_the_date_it_was_copied_from():
    text = README.read_text()
    assert re.search(r"\b[0-9a-f]{40}\b", text), "no 40-character commit sha"
    assert re.search(r"\b\d{4}-\d{2}-\d{2}\b", text), "no date"


def test_the_readme_carries_both_rules():
    text = " ".join(README.read_text().split())
    # Rule 1: what a test may read the model files for.
    assert "tested against this frozen copy" in text
    assert "never from a literal" in text
    assert "No test asserts a choice the console can change" in text
    # Rule 2: the refresh rule, in the words a stale fixture says.
    assert "the refresh rule" in text
    assert "re-freeze it in this pull request" in text
    assert "needs no re-freeze" in text


def test_the_frozen_config_is_after_session_agents_landed():
    assert "session_agents" in fixture_config()


# --------------------------------------------------------------------------- #
# 2. The readers                                                               #
# --------------------------------------------------------------------------- #

def test_the_readers_read_the_fixture_not_the_repo():
    text = (FIXTURE_DIR / "models.yaml").read_text()
    assert fixture_text() == text
    assert fixture_config() == yaml.safe_load(text)
    prices = yaml.safe_load((FIXTURE_DIR / "model-prices.yaml").read_text())
    assert fixture_prices() == prices["prices"]


def test_ladder_is_the_rung_ids_of_a_kinds_ladder():
    cfg = {
        "kinds": {"builders": {"ladder": "build"}},
        "ladders": {"build": [{"model": "m-1", "reason": "x"}, "m-2"]},
    }
    assert ladder(cfg, "builders") == ["m-1", "m-2"]
    cfg = fixture_config()
    for kind, spec in cfg["kinds"].items():
        assert ladder(cfg, kind) == _ids(cfg["ladders"][spec["ladder"]])


# --------------------------------------------------------------------------- #
# 3. The tree                                                                  #
# --------------------------------------------------------------------------- #

def test_the_tree_holds_todays_code_and_the_frozen_files(tree):
    for path in sorted((ROOT / "scripts").glob("*.py")):
        assert (tree / "scripts" / path.name).is_file(), path.name
    for name in ("models.yaml", "model-prices.yaml"):
        assert (tree / "config" / name).read_bytes() == (FIXTURE_DIR / name).read_bytes()
    assert (tree / "config" / "repo-map.json").read_bytes() == (
        ROOT / "config" / "repo-map.json").read_bytes()
    assert (tree / "config" / "model-choices.json").is_file()


def test_the_tree_is_in_sync(tree):
    proc = subprocess.run(
        [sys.executable, "scripts/sync_model_config.py", "--check"],
        cwd=tree, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_the_tree_passes_the_policy(tree):
    mf = fixture_module(tree)
    raw = yaml.safe_load((tree / "config" / "models.yaml").read_text())
    assert mf.policy_errors(raw) == []


def test_the_module_runs_on_the_fixture_and_leaves_the_repo_module_alone(tree):
    import model_fallback as repo_mf

    before = sys.modules["model_fallback"]
    mf = fixture_module(tree)
    assert Path(mf.__file__).resolve().parent == (tree / "scripts").resolve()
    assert mf is not repo_mf
    cfg = fixture_config()
    assert mf.CONFIG["ladders"] == {
        name: _ids(rungs) for name, rungs in cfg["ladders"].items()}
    assert sys.modules["model_fallback"] is before
    assert Path(repo_mf.__file__).resolve() == ROOT / "scripts" / "model_fallback.py"


def test_a_module_from_one_tree_is_not_a_module_from_another(tmp_path, tree):
    other = fixture_tree(tmp_path / "other")
    assert fixture_module(other).__name__ != fixture_module(tree).__name__


# --------------------------------------------------------------------------- #
# 4. Independent of today's choices                                            #
# --------------------------------------------------------------------------- #

def test_the_tree_selects_what_its_own_config_says(tmp_path):
    dest = fixture_tree(tmp_path / "tree")
    path = dest / "config" / "models.yaml"
    raw = yaml.safe_load(path.read_text())
    workhorse = raw["kinds"][raw["agents"]["engineer"]]["ladder"]
    rungs = raw["ladders"][workhorse]
    second = _ids(rungs)[1]
    rungs[0], rungs[1] = rungs[1], rungs[0]
    path.write_text(yaml.safe_dump(raw, sort_keys=False))
    proc = subprocess.run([sys.executable, "scripts/sync_model_config.py"],
                          cwd=dest, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    mf = fixture_module(dest)
    assert mf.select("engineer", probe=lambda m: True, clock=lambda: 0.0) == second


# --------------------------------------------------------------------------- #
# 5. The refresh rule is what a stale fixture says                             #
# --------------------------------------------------------------------------- #

def test_a_generator_that_fails_raises_fixture_stale(tmp_path, monkeypatch):
    copy = model_fixture._copy_into

    def copy_then_stub(dest: Path) -> None:
        copy(dest)
        (dest / "scripts" / "sync_model_config.py").write_text(
            "import sys\n"
            "print('stub generator: refusing the frozen config', file=sys.stderr)\n"
            "sys.exit(1)\n")

    monkeypatch.setattr(model_fixture, "_copy_into", copy_then_stub)
    with pytest.raises(FixtureStale) as caught:
        fixture_tree(tmp_path / "tree")
    message = str(caught.value)
    assert STALE_SENTENCE in message
    assert "tests/fixtures/model-config-frozen/README.md" in message
    assert "stub generator: refusing the frozen config" in message


# --------------------------------------------------------------------------- #
# 6. Which test files can read the model files                                 #
# --------------------------------------------------------------------------- #

def _plant(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_model_reading_tests_on_a_planted_tree(tmp_path):
    _plant(tmp_path, "config/models.yaml", yaml.safe_dump({
        "ladders": {"solo": [{"model": "vendor-model-9", "reason": "x"}]}}))
    rung = _plant(tmp_path, "tests/test_rung.py", 'MODEL = "vendor-model-9"\n')
    selector = _plant(tmp_path, "tests/nested/test_selector.py", "import model_fallback\n")
    generator = _plant(tmp_path, "tests/test_generator.py",
                       'CMD = ["python3", "scripts/sync_model_config.py"]\n')
    _plant(tmp_path, "tests/test_unrelated.py", "def test_nothing():\n    pass\n")
    _plant(tmp_path, "tests/node_modules/test_skipped.py", "import model_fallback\n")
    _plant(tmp_path, "tests/.hidden/test_hidden.py", 'MODEL = "vendor-model-9"\n')
    _plant(tmp_path, "tests/helper.py", "import model_fallback\n")

    assert model_reading_tests(tmp_path) == sorted([rung, selector, generator])


@pytest.mark.parametrize("token", [
    "models.yaml", "agents.yaml", "model-choices.json",
    "model_fallback", "sync_model_config", "model_config_change",
])
def test_each_named_file_puts_a_test_in_the_set(tmp_path, token):
    _plant(tmp_path, "config/models.yaml", yaml.safe_dump({"ladders": {"solo": ["m-1"]}}))
    named = _plant(tmp_path, "tests/test_named.py", f"# reads {token}\n")
    assert model_reading_tests(tmp_path) == [named]


def test_model_reading_tests_on_the_repo_finds_the_selector_through_a_workflow():
    found = model_reading_tests(ROOT)
    assert ROOT / "tests" / "test_plan_model_death_swing.py" in found
    assert ROOT / "tests" / "test_model_fixture.py" in found
    assert found == sorted(found)
