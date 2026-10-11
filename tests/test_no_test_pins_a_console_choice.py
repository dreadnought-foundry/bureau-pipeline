"""No test pins a choice the console can change (DRE-6866).

The console rewrites `config/models.yaml` on every save: which model leads a
group, which group an agent is in, which agents exist, which projects have a
model of their own. A test that asserts one of those choices as a literal goes
red on a legitimate save. The rewrite cards (DRE-6860 to DRE-6864, DRE-6884,
DRE-6885) removed the pins that existed; this guard keeps new ones out.

It reads what the console can change today off the committed file — the rung
ids of every ladder, the `agents:` map, the `ungrouped` list and the kind
keys — and scans exactly the files `model_fixture.model_reading_tests(ROOT)`
names, the set the harness (DRE-6865) runs inside each changed copy. In each
file, every `assert` statement and every `self.assert*` call is a candidate,
with a module-level constant bound to a string literal read as that string.
Four shapes are findings:

* a ladder model id of the committed file;
* an agent name of `agents:` or `ungrouped` beside a kind key — an
  agent-to-group pair;
* a list or tuple literal of two or more agent names;
* the word `projects` beside `{}`.

An id on no ladder (`retired`, `excluded`) is not a finding. The only way past
the guard is `ALLOWED`, one entry per test with the reason its literal is not
a pin; an entry whose test is gone or produces no finding fails as stale, so
the list cannot grow silently.
"""
from __future__ import annotations

import ast
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

import model_fixture  # noqa: E402

LADDER_ID = "ladder id"
AGENT_GROUP = "agent-to-group pair"
AGENT_LIST = "list of agents"
EMPTY_PROJECTS = "empty projects"

# `"tests/<file>.py::<Class.test_name or test_name>"` -> why the literal in
# that test is not a pin. One entry per test; every entry is checked to still
# produce a finding.
ALLOWED: dict[str, str] = {}


@dataclass(frozen=True)
class Choices:
    """What the console can change, read off a tree's `config/models.yaml`."""

    ids: frozenset[str]
    agents: frozenset[str]
    kinds: frozenset[str]

    @classmethod
    def of(cls, tree: Path) -> "Choices":
        cfg = yaml.safe_load((tree / "config" / "models.yaml").read_text()) or {}
        ids = {r["model"] if isinstance(r, dict) else r
               for rungs in (cfg.get("ladders") or {}).values() for r in rungs or []}
        agents = set(cfg.get("agents") or {}) | set(cfg.get("ungrouped") or [])
        return cls(frozenset(ids), frozenset(agents), frozenset(cfg.get("kinds") or {}))

    def id_pattern(self) -> re.Pattern:
        # Longest first, and bounded, so `claude-opus-5` is not read inside
        # `claude-opus-5-5`, nor an excluded id inside a ladder id's name.
        names = sorted(self.ids, key=len, reverse=True)
        return re.compile(r"(?<![\w.-])(" + "|".join(map(re.escape, names)) + r")(?![\w.]|-\w)")


@dataclass(frozen=True, order=True)
class Finding:
    path: str
    line: int
    shape: str
    literal: str
    test: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.shape} — {self.literal}"

    @property
    def key(self) -> str:
        return f"{self.path}::{self.test}"


def _constants(module: ast.Module) -> dict[str, str]:
    """Module-level names bound to a string literal."""
    found = {}
    for node in module.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        else:
            continue
        if (isinstance(target, ast.Name) and isinstance(value, ast.Constant)
                and isinstance(value.value, str)):
            found[target.id] = value.value
    return found


def _is_assertion(node: ast.AST) -> bool:
    if isinstance(node, ast.Assert):
        return True
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name) and node.func.value.id == "self"
            and node.func.attr.startswith("assert"))


def _assertions(module: ast.Module):
    """Every assertion with the test it sits in: `Class.test_name`,
    `test_name`, or `<module>` outside any function."""
    def walk(node: ast.AST, scope: list[str], in_function: bool):
        for child in ast.iter_child_nodes(node):
            if _is_assertion(child):
                yield child, ".".join(scope) or "<module>"
            if isinstance(child, ast.ClassDef) and not in_function:
                yield from walk(child, scope + [child.name], False)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and not in_function:
                yield from walk(child, scope + [child.name], True)
            else:
                yield from walk(child, scope, in_function)
    yield from walk(module, [], False)


def _tests(module: ast.Module) -> set[str]:
    """The qualified names `_assertions` can attribute a finding to."""
    names = {"<module>"}

    def walk(node: ast.AST, scope: list[str]):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                names.add(".".join(scope + [child.name]))
                walk(child, scope + [child.name])
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names.add(".".join(scope + [child.name]))
    walk(module, [])
    return names


def _strings(node: ast.AST, constants: dict[str, str]):
    """(node, string, the constant it came through or None) for every string
    in `node`, a name bound to a module-level string read as that string."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            yield sub, sub.value, None
        elif isinstance(sub, ast.Name) and sub.id in constants:
            yield sub, constants[sub.id], sub.id


def scan_file(path: Path, rel: str, choices: Choices) -> list[Finding]:
    """The findings in one test file."""
    source = path.read_text(encoding="utf-8", errors="replace")
    module = ast.parse(source, filename=rel)
    constants = _constants(module)
    ids = choices.id_pattern()
    found: set[Finding] = set()
    for assertion, test in _assertions(module):
        strings = list(_strings(assertion, constants))
        for node, text, via in strings:
            for model in dict.fromkeys(ids.findall(text)):
                literal = f"{model} (through {via})" if via else model
                found.add(Finding(rel, node.lineno, LADDER_ID, literal, test))
        agents = list(dict.fromkeys(t for _, t, _ in strings if t in choices.agents))
        kinds = list(dict.fromkeys(t for _, t, _ in strings if t in choices.kinds))
        if agents and kinds:
            found.add(Finding(rel, assertion.lineno, AGENT_GROUP,
                              ", ".join(agents + kinds), test))
        for node in ast.walk(assertion):
            if isinstance(node, (ast.List, ast.Tuple)):
                names = [t for e in node.elts for _, t, _ in _strings(e, constants)
                         if isinstance(e, (ast.Constant, ast.Name)) and t in choices.agents]
                if len(names) >= 2:
                    found.add(Finding(rel, node.lineno, AGENT_LIST, repr(names), test))
        segment = ast.get_source_segment(source, assertion) or ""
        if re.search(r"\bprojects\b", segment):
            for node in ast.walk(assertion):
                if isinstance(node, ast.Dict) and not node.keys:
                    found.add(Finding(rel, node.lineno, EMPTY_PROJECTS, "projects, {}", test))
    return sorted(found)


def scan(tree: Path) -> list[Finding]:
    """Every finding in the files `model_fixture.model_reading_tests(tree)`
    names, read against `tree`'s own `config/models.yaml`."""
    tree = Path(tree)
    choices = Choices.of(tree)
    found = []
    for path in model_fixture.model_reading_tests(tree):
        found += scan_file(path, path.relative_to(tree).as_posix(), choices)
    return sorted(found)


def guard(tree: Path, allowed: dict[str, str]) -> list[str]:
    """Every finding `allowed` does not cover, and every stale entry of
    `allowed`. Empty when the tree holds no pin."""
    tree = Path(tree)
    findings = scan(tree)
    problems = [str(f) for f in findings if f.key not in allowed]
    hit = {f.key for f in findings}
    for key in sorted(allowed):
        if key in hit:
            continue
        rel, _, test = key.partition("::")
        path = tree / rel
        exists = path.is_file() and test in _tests(ast.parse(path.read_text(encoding="utf-8")))
        why = "produces no finding" if exists else "names no test that exists"
        problems.append(f"stale ALLOWED entry {key}: {why}")
    return problems


# --------------------------------------------------------------------------- #
# The guard, on the real tree                                                  #
# --------------------------------------------------------------------------- #

def test_no_test_pins_a_console_choice():
    problems = guard(ROOT, ALLOWED)
    assert not problems, (
        f"{len(problems)} assertion(s) pin a choice the console can change, or "
        "an ALLOWED entry is stale. Derive the value from the config a test "
        "reads (tests/model_fixture.py), or add an ALLOWED entry saying why the "
        "literal is not a pin:\n" + "\n".join(problems))


def test_every_allowed_entry_carries_a_reason():
    for key, reason in ALLOWED.items():
        assert re.fullmatch(r"tests/[\w/]+\.py::[\w.<>]+", key), key
        assert len(reason.split()) >= 5 and reason.rstrip().endswith("."), (
            f"{key}: the reason must be a sentence saying why the literal is not a pin")


# --------------------------------------------------------------------------- #
# The guard's own correctness, on a planted tree                               #
# --------------------------------------------------------------------------- #

def _cfg() -> dict:
    return yaml.safe_load((ROOT / "config" / "models.yaml").read_text())


def _plant(tmp_path: Path, files: dict[str, str]) -> Path:
    """A tree holding the committed `config/models.yaml` and `files` under
    `tests/`."""
    (tmp_path / "config").mkdir()
    shutil.copy2(ROOT / "config" / "models.yaml", tmp_path / "config" / "models.yaml")
    (tmp_path / "tests").mkdir()
    for name, text in files.items():
        (tmp_path / "tests" / name).write_text(text)
    return tmp_path


def _line(text: str, marker: str) -> int:
    return next(n for n, line in enumerate(text.splitlines(), 1) if marker in line)


def test_each_shape_is_reported_by_file_and_line(tmp_path):
    cfg = _cfg()
    model = model_fixture.ladder(cfg, next(iter(cfg["kinds"])))[0]
    agent, kind = next(iter(cfg["agents"].items()))
    first, second = list(cfg["agents"])[:2]
    text = (
        '"""Reads config/models.yaml."""\n'
        "class PlantedTest:\n"
        "    def test_model(self):\n"
        f'        self.assertEqual(pick(), "{model}")  # shape-1\n'
        "    def test_group(self):\n"
        f'        self.assertEqual(cfg["agents"]["{agent}"], "{kind}")  # shape-2\n'
        "\n"
        "def test_agents():\n"
        f'    assert names() == ["{first}", "{second}"]  # shape-3\n'
        "\n"
        "def test_projects():\n"
        '    assert cfg["projects"] == {}  # shape-4\n'
    )
    tree = _plant(tmp_path, {"test_planted.py": text})

    problems = guard(tree, {})

    at = "tests/test_planted.py"
    assert problems == [
        f"{at}:{_line(text, 'shape-1')}: {LADDER_ID} — {model}",
        f"{at}:{_line(text, 'shape-2')}: {AGENT_GROUP} — {agent}, {kind}",
        f"{at}:{_line(text, 'shape-3')}: {AGENT_LIST} — {[first, second]!r}",
        f"{at}:{_line(text, 'shape-4')}: {EMPTY_PROJECTS} — projects, {{}}",
    ]
    assert [f.key for f in scan(tree)] == [
        f"{at}::PlantedTest.test_model", f"{at}::PlantedTest.test_group",
        f"{at}::test_agents", f"{at}::test_projects",
    ]


def test_an_id_reached_through_a_module_constant_is_caught(tmp_path):
    cfg = _cfg()
    model = model_fixture.ladder(cfg, next(iter(cfg["kinds"])))[-1]
    text = (
        '"""Reads config/models.yaml."""\n'
        f'PINNED = "{model}"\n'
        "\n"
        "def test_pick():\n"
        "    assert pick() == PINNED  # here\n"
    )
    tree = _plant(tmp_path, {"test_planted.py": text})

    assert guard(tree, {}) == [
        f"tests/test_planted.py:{_line(text, '# here')}: {LADDER_ID} — {model} (through PINNED)"]


def test_a_file_naming_only_a_ladder_id_is_scanned(tmp_path):
    cfg = _cfg()
    model = model_fixture.ladder(cfg, list(cfg["kinds"])[-1])[0]
    text = f'def test_pick():\n    assert pick() == "{model}"\n'
    assert not any(name in text for name in model_fixture.MODEL_FILE_NAMES)
    tree = _plant(tmp_path, {"test_step.py": text})

    assert guard(tree, {}) == [f"tests/test_step.py:2: {LADDER_ID} — {model}"]


def test_an_assertion_reading_both_sides_from_the_config_is_not_reported(tmp_path):
    text = (
        '"""Reads config/models.yaml."""\n'
        "def test_first_rung(cfg, kind, agent):\n"
        '    ladder = cfg["ladders"][cfg["kinds"][kind]["ladder"]]\n'
        '    assert ladder[0] == cfg["ladders"][cfg["kinds"][cfg["agents"][agent]]["ladder"]][0]\n'
        '    assert sorted(cfg["projects"]) == sorted(choices["projects"])\n'
    )
    tree = _plant(tmp_path, {"test_derived.py": text})

    assert guard(tree, {}) == []


def test_an_id_on_no_ladder_is_not_reported(tmp_path):
    cfg = _cfg()
    off = [e["model"] for e in (cfg.get("retired") or []) + (cfg.get("excluded") or [])]
    assert off
    text = '"""Reads config/models.yaml."""\ndef test_off():\n' + "".join(
        f'    assert retired() == "{model}"\n' for model in off)
    tree = _plant(tmp_path, {"test_off.py": text})

    assert guard(tree, {}) == []


def test_an_allowed_test_passes_and_a_stale_entry_fails(tmp_path):
    cfg = _cfg()
    model = model_fixture.ladder(cfg, next(iter(cfg["kinds"])))[0]
    text = (
        '"""Reads config/models.yaml."""\n'
        "class PlantedTest:\n"
        "    def test_message(self):\n"
        f'        self.assertIn("{model}", error())\n'
        "    def test_clean(self):\n"
        "        self.assertTrue(error())\n"
    )
    tree = _plant(tmp_path, {"test_planted.py": text})
    reason = "The policy error is asserted to name the offending id."
    at = "tests/test_planted.py"

    assert guard(tree, {f"{at}::PlantedTest.test_message": reason}) == []
    assert guard(tree, {
        f"{at}::PlantedTest.test_message": reason,
        f"{at}::PlantedTest.test_gone": reason,
        f"{at}::PlantedTest.test_clean": reason,
        "tests/test_missing.py::test_anything": reason,
    }) == [
        "stale ALLOWED entry tests/test_missing.py::test_anything: names no test that exists",
        f"stale ALLOWED entry {at}::PlantedTest.test_clean: produces no finding",
        f"stale ALLOWED entry {at}::PlantedTest.test_gone: names no test that exists",
    ]
