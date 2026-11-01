"""No model choice is copied anywhere it used to be (DRE-6983).

A choice — which models a group runs on and in what order, which group an
agent is in, a group's label and purpose, a project's own model — lives in ONE
home: the `BUREAU_MODEL_CHOICES` variable the console writes, read by
`scripts/model_home.py`, or with no home the defaults in the code,
`CODE_DEFAULTS` in `scripts/model_fallback.py`. Before DRE-6982 and this card
the same choices were also copied into a config file, a roster, a generated
mirror, a JSON snapshot and the scripts and workflows that kept those copies in
step. Each copy was a place a person could edit believing it changed
something.

This guard names every former home of a copy, written out in full below so a
reader sees each place without running anything, and fails if a choice is
back in any of them:

  * `config/models.yaml` holds none of `kinds`, `ladders`, `default_ladder`,
    `agents`, `ungrouped` and `projects`;
  * `agents.yaml` has no `model:` and no `kind:` line, and no `BEGIN generated`
    marker;
  * `scripts/model_fallback.py` has no `BEGIN generated` marker, and exactly
    one document literal, `CODE_DEFAULTS`;
  * `config/model-choices.json`, `scripts/sync_model_config.py`,
    `scripts/model_config_change.py`, `scripts/model_home_carry.py`,
    `.github/workflows/model-config-change.yml` and
    `.github/workflows/model-home-carry.yml` do not exist;
  * no `.py` file under `scripts/` other than `scripts/model_fallback.py` holds
    a list or tuple literal of two or more model ids priced in
    `config/model-prices.yaml`, and no YAML or JSON file under `config/` holds
    such a list outside `models.yaml`'s `excluded` and `retired`.

Each check is a function of a tree's root, so the tests below prove each one
goes red in a throwaway tree with a copy put back. agent-bureau's half of the
guard is its `scripts/test_no_choice_copies.py` (DRE-8022).
"""
from __future__ import annotations

import ast
import json
import re
import shutil
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]

# --------------------------------------------------------------------------- #
# Every former home of a copy, in full                                         #
# --------------------------------------------------------------------------- #

# The config file the choices were written in until this card. It keeps only
# code-side facts (`session_agents`, `about`, `effort`, `review_separation`,
# `discovery`, `retired`, `excluded`, `rules`).
MODELS_YAML = "config/models.yaml"
CHOICE_KEYS = ("kinds", "ladders", "default_ladder", "agents", "ungrouped", "projects")

# The roster, which carried a generated `kind:` and `model:` per agent until
# DRE-6982.
AGENTS_YAML = "agents.yaml"

# The selector, whose `CODE_DEFAULTS` was a generated region until DRE-6982 and
# is now the defaults' one home: the one document literal allowed anywhere.
SELECTOR = "scripts/model_fallback.py"
DEFAULTS_NAME = "CODE_DEFAULTS"

# The files that held or carried a copy, each deleted before this card: the
# console's JSON snapshot, the generator, the reviewed-change writer and its
# workflow, and the carry from the home back into the file and its workflow.
GONE = (
    "config/model-choices.json",
    "scripts/sync_model_config.py",
    "scripts/model_config_change.py",
    "scripts/model_home_carry.py",
    ".github/workflows/model-config-change.yml",
    ".github/workflows/model-home-carry.yml",
)

# Where a ladder literal may still be: the selector's own `CODE_DEFAULTS`, and
# the two lists of `models.yaml` that are code-side facts, not choices.
PRICES = "config/model-prices.yaml"
SCRIPTS = "scripts"
CONFIG = "config"
LADDER_ALLOWED_SCRIPT = SELECTOR
LADDER_ALLOWED_CONFIG = ((MODELS_YAML, "excluded"), (MODELS_YAML, "retired"))

GENERATED_MARKER = "BEGIN generated"

# A `model:` or `kind:` line of YAML — a key, never a comment that names one.
_ROSTER_CHOICE_LINE = re.compile(r"^\s*(?:-\s+)?(model|kind)\s*:")


# --------------------------------------------------------------------------- #
# The checks — each returns one line per copy found, empty when there is none  #
# --------------------------------------------------------------------------- #

def models_yaml_copies(root: Path) -> list[str]:
    """Each choice key `config/models.yaml` holds."""
    raw = yaml.safe_load((root / MODELS_YAML).read_text()) or {}
    return [f"{MODELS_YAML}: holds {key!r}, a choice" for key in CHOICE_KEYS
            if key in raw]


def agents_yaml_copies(root: Path) -> list[str]:
    """Each `model:` or `kind:` line of `agents.yaml`, and each generated
    marker in it."""
    found = []
    for number, line in enumerate((root / AGENTS_YAML).read_text().splitlines(), 1):
        if line.lstrip().startswith("#"):
            if GENERATED_MARKER in line:
                found.append(f"{AGENTS_YAML}:{number}: a {GENERATED_MARKER!r} marker")
            continue
        key = _ROSTER_CHOICE_LINE.match(line)
        if key:
            found.append(f"{AGENTS_YAML}:{number}: a {key.group(1)}: line")
        elif GENERATED_MARKER in line:
            found.append(f"{AGENTS_YAML}:{number}: a {GENERATED_MARKER!r} marker")
    return found


def _is_ladder_display(node) -> bool:
    return isinstance(node, (ast.List, ast.Tuple))


def document_literals(tree: ast.AST) -> list[ast.Dict]:
    """Every dict display in `tree` that is a choices document written out: a
    `"ladders"` key whose value is a dict display of list or tuple displays."""
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if (isinstance(key, ast.Constant) and key.value == "ladders"
                    and isinstance(value, ast.Dict) and value.values
                    and all(_is_ladder_display(v) for v in value.values)):
                found.append(node)
                break
    return found


def selector_copies(root: Path) -> list[str]:
    """A generated marker in the selector, and every document literal in it
    but the one `CODE_DEFAULTS` is assigned."""
    text = (root / SELECTOR).read_text()
    found = [f"{SELECTOR}:{n}: a {GENERATED_MARKER!r} marker"
             for n, line in enumerate(text.splitlines(), 1) if GENERATED_MARKER in line]
    tree = ast.parse(text)
    defaults = [
        node.value for node in ast.walk(tree)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        and any(isinstance(t, ast.Name) and t.id == DEFAULTS_NAME
                for t in (node.targets if isinstance(node, ast.Assign) else [node.target]))
    ]
    literals = document_literals(tree)
    if len(literals) != 1 or [id(d) for d in defaults] != [id(literals[0])]:
        found.append(
            f"{SELECTOR}: {len(literals)} document literal(s) at line(s) "
            f"{[d.lineno for d in literals]} — exactly one is allowed, "
            f"{DEFAULTS_NAME}")
    return found


def gone_files_present(root: Path) -> list[str]:
    """Each file of `GONE` that exists."""
    return [f"{rel}: exists, and it held or carried a copy" for rel in GONE
            if (root / rel).exists()]


def priced_ids(root: Path) -> set[str]:
    """The model ids `config/model-prices.yaml` declares a price for."""
    return set((yaml.safe_load((root / PRICES).read_text()) or {}).get("prices") or {})


def script_ladder_literals(root: Path) -> list[str]:
    """Each list or tuple literal of two or more priced ids in a `.py` file
    under `scripts/`, the selector set aside."""
    priced = priced_ids(root)
    found = []
    for path in sorted((root / SCRIPTS).rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if rel == LADDER_ALLOWED_SCRIPT:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if not _is_ladder_display(node):
                continue
            ids = [e.value for e in node.elts
                   if isinstance(e, ast.Constant) and e.value in priced]
            if len(ids) >= 2:
                found.append(f"{rel}:{node.lineno}: a ladder literal {ids}")
    return found


def _rung_id(item):
    if isinstance(item, str):
        return item
    if isinstance(item, dict) and isinstance(item.get("model"), str):
        return item["model"]
    return None


def _priced_lists(value, path, priced):
    """`(path, ids)` for each list under `value` holding two or more priced
    ids, each a bare id or a `{model: …}` rung."""
    if isinstance(value, list):
        ids = [i for i in map(_rung_id, value) if i in priced]
        if len(ids) >= 2:
            yield path, ids
        for index, item in enumerate(value):
            yield from _priced_lists(item, (*path, index), priced)
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _priced_lists(item, (*path, key), priced)


def config_ladder_lists(root: Path) -> list[str]:
    """Each list of two or more priced ids in a YAML or JSON file under
    `config/`, `models.yaml`'s `excluded` and `retired` set aside."""
    priced = priced_ids(root)
    found = []
    for path in sorted((root / CONFIG).rglob("*")):
        if path.suffix in (".yaml", ".yml"):
            data = yaml.safe_load(path.read_text())
        elif path.suffix == ".json":
            data = json.loads(path.read_text())
        else:
            continue
        rel = path.relative_to(root).as_posix()
        for where, ids in _priced_lists(data, (), priced):
            if where and (rel, where[0]) in LADDER_ALLOWED_CONFIG and len(where) == 1:
                continue
            spelled = ".".join(str(part) for part in where) or "(top)"
            found.append(f"{rel}: {spelled} is a ladder {ids}")
    return found


def choice_copies(root: Path) -> list[str]:
    """Every copy of a choice in the tree at `root`."""
    root = Path(root)
    return [
        *models_yaml_copies(root),
        *agents_yaml_copies(root),
        *selector_copies(root),
        *gone_files_present(root),
        *script_ladder_literals(root),
        *config_ladder_lists(root),
    ]


# --------------------------------------------------------------------------- #
# The guard on this tree                                                       #
# --------------------------------------------------------------------------- #

def test_no_choice_is_copied_anywhere_it_used_to_be():
    assert choice_copies(ROOT) == []


def test_the_one_document_literal_is_the_defaults_in_the_code():
    tree = ast.parse((ROOT / SELECTOR).read_text())
    [literal] = document_literals(tree)
    assert literal.lineno > 1
    assert selector_copies(ROOT) == []


def test_the_defaults_in_the_code_still_carry_their_choices():
    # CODE_DEFAULTS is the defaults' one home, so the ladder literals the scan
    # allows there are really there: the scan is not passing on an empty file.
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import model_fallback as mf

    defaults = mf.code_defaults()
    for key in ("ladders", "kinds", "agents", "groups"):
        assert defaults[key], key
    assert any(len(rungs) >= 2 for rungs in defaults["ladders"].values())


# --------------------------------------------------------------------------- #
# Each check goes red when a copy is put back, in a throwaway tree             #
# --------------------------------------------------------------------------- #

@pytest.fixture()
def tree(tmp_path) -> Path:
    """The files the guard reads, copied from this tree."""
    dest = tmp_path / "tree"
    shutil.copytree(ROOT / SCRIPTS, dest / SCRIPTS,
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / CONFIG, dest / CONFIG)
    shutil.copy2(ROOT / AGENTS_YAML, dest / AGENTS_YAML)
    (dest / ".github" / "workflows").mkdir(parents=True)
    assert choice_copies(dest) == []
    return dest


def _ladder(root: Path, n: int = 3) -> list[str]:
    """`n` priced ids of the tree, in a fixed order."""
    return sorted(priced_ids(root))[:n]


def test_a_model_line_in_the_roster_is_found(tree):
    path = tree / AGENTS_YAML
    lines = path.read_text().splitlines(keepends=True)
    at = next(i for i, line in enumerate(lines) if line.lstrip().startswith("- name:"))
    indent = lines[at][: len(lines[at]) - len(lines[at].lstrip())]
    lines.insert(at + 1, f"{indent}  model: {_ladder(tree)[0]}\n")
    path.write_text("".join(lines))
    found = choice_copies(tree)
    assert found and all("a model: line" in line for line in found), found


def test_a_kind_line_in_the_roster_is_found(tree):
    path = tree / AGENTS_YAML
    lines = path.read_text().splitlines(keepends=True)
    at = next(i for i, line in enumerate(lines) if line.lstrip().startswith("- name:"))
    indent = lines[at][: len(lines[at]) - len(lines[at].lstrip())]
    lines.insert(at + 1, f"{indent}  kind: workhorse\n")
    path.write_text("".join(lines))
    assert any("a kind: line" in line for line in choice_copies(tree))


def test_a_generated_marker_in_the_roster_is_found(tree):
    path = tree / AGENTS_YAML
    path.write_text(path.read_text() + f"# {GENERATED_MARKER} model\n")
    assert any(GENERATED_MARKER in line for line in agents_yaml_copies(tree))


def test_a_comment_naming_a_model_line_is_not_a_copy(tree):
    path = tree / AGENTS_YAML
    path.write_text(path.read_text() + "# the `model:` and `kind:` lines are gone\n")
    assert choice_copies(tree) == []


@pytest.mark.parametrize("key", CHOICE_KEYS)
def test_a_choice_key_in_models_yaml_is_found(tree, key):
    path = tree / MODELS_YAML
    path.write_text(path.read_text() + f"\n{key}: {{}}\n")
    assert choice_copies(tree) == [f"{MODELS_YAML}: holds {key!r}, a choice"]


def test_ladders_in_models_yaml_is_found_as_a_key_and_as_a_ladder(tree):
    path = tree / MODELS_YAML
    rungs = _ladder(tree)
    path.write_text(path.read_text() + "\nladders:\n  workhorse:\n"
                    + "".join(f"    - model: {m}\n      reason: x\n" for m in rungs))
    found = choice_copies(tree)
    assert f"{MODELS_YAML}: holds 'ladders', a choice" in found
    assert f"{MODELS_YAML}: ladders.workhorse is a ladder {rungs}" in found


def test_a_generated_marker_in_the_selector_is_found(tree):
    path = tree / SELECTOR
    path.write_text(path.read_text().replace(
        f"{DEFAULTS_NAME} = {{",
        f"# {GENERATED_MARKER} code defaults\n{DEFAULTS_NAME} = {{", 1))
    found = choice_copies(tree)
    assert found and all(GENERATED_MARKER in line for line in found), found


def test_a_second_document_literal_in_the_selector_is_found(tree):
    path = tree / SELECTOR
    path.write_text(path.read_text() + (
        f"\nSECOND = {{'ladders': {{'workhorse': {_ladder(tree)!r}}}}}\n"))
    found = selector_copies(tree)
    assert found and "exactly one is allowed" in found[0], found


def test_the_defaults_literal_renamed_is_found(tree):
    path = tree / SELECTOR
    text = path.read_text().replace(f"{DEFAULTS_NAME} = {{", "OTHER_DEFAULTS = {", 1)
    path.write_text(text + f"\n{DEFAULTS_NAME} = OTHER_DEFAULTS\n")
    assert any("exactly one is allowed" in line for line in selector_copies(tree))


@pytest.mark.parametrize("rel", GONE)
def test_each_deleted_file_put_back_is_found(tree, rel):
    path = tree / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}\n")
    assert choice_copies(tree) == [f"{rel}: exists, and it held or carried a copy"]


def test_a_ladder_literal_in_another_script_is_found(tree):
    rungs = _ladder(tree)
    (tree / SCRIPTS / "planted.py").write_text(f"LADDER = {rungs!r}\n")
    assert choice_copies(tree) == [f"scripts/planted.py:1: a ladder literal {rungs}"]


def test_a_second_ladder_literal_in_a_script_that_has_none_is_found(tree):
    rungs = tuple(_ladder(tree, 2))
    path = tree / SCRIPTS / "model_home.py"
    path.write_text(path.read_text() + f"\n_SPARE = {rungs!r}\n")
    found = choice_copies(tree)
    assert len(found) == 1 and found[0].startswith("scripts/model_home.py:"), found


def test_one_priced_id_in_a_list_is_not_a_ladder(tree):
    (tree / SCRIPTS / "planted.py").write_text(
        f"ONE = [{_ladder(tree)[0]!r}, 'not-a-model']\n")
    assert choice_copies(tree) == []


def test_a_ladder_list_in_a_config_json_is_found(tree):
    rungs = _ladder(tree)
    (tree / CONFIG / "model-choices-again.json").write_text(
        json.dumps({"groups": {"workhorse": {"models": rungs}}}))
    assert choice_copies(tree) == [
        f"config/model-choices-again.json: groups.workhorse.models is a ladder {rungs}"]


def test_excluded_and_retired_may_list_priced_ids(tree):
    path = tree / MODELS_YAML
    raw = yaml.safe_load(path.read_text())
    rungs = _ladder(tree, 2)
    raw["excluded"] = [{"model": m, "reason": "x"} for m in rungs]
    raw["retired"] = list(rungs)
    path.write_text(yaml.safe_dump(raw))
    assert choice_copies(tree) == []
