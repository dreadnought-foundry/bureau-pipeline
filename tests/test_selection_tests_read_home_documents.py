"""DRE-6989 — the selection rule tests prove their scenarios on home documents.

Since DRE-6939 the selector selects on the home or on the defaults in the code,
never on the choices written in a tree's `config/models.yaml`. The three files
below used to change a choice by rewriting that file and then running the
generator, so the tree's code said it too. Each scenario is now a document
built from `model_fixture.fixture_home()` with the shared helpers and handed to
a tree with `home_env` or `fixture_module(..., doc=...)` (DRE-6972).

This guard keeps the old route out of the three files:

* none names the generator or defines its own `make_tree`, `lead_with` or
  `with_project` — the shared helpers are the only ones;
* none writes a tree's `config/models.yaml` to change a choice. A write of
  that file is admitted in two shapes only: a constant that is not a config
  at all (a truncated checkout), or inside `_write_code_side`, which refuses
  a config whose choices differ from the frozen ones;
* none spells a model id outside a comment: every id is read off the frozen
  document or the frozen `models.yaml`.

Run: cd bureau-pipeline && python3 -m pytest tests/test_selection_tests_read_home_documents.py -q
"""
from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

FILES = (
    "tests/test_model_choice_policy.py",
    "tests/test_project_model_exceptions.py",
    "tests/test_review_separation.py",
)

GENERATOR = "sync_model_config"
OWN_HELPERS = ("make_tree", "lead_with", "with_project")
CODE_SIDE_WRITER = "_write_code_side"
BARE_ID = re.compile(r"claude-[a-z]+-[0-9]")


def _source(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# The scans                                                                    #
# --------------------------------------------------------------------------- #

def generator_mentions(text: str) -> list[int]:
    """The lines of `text` that name the generator."""
    return [n for n, line in enumerate(text.splitlines(), 1) if GENERATOR in line]


def own_helpers(text: str) -> list[str]:
    """Every function `text` defines under a shared helper's name."""
    return sorted(node.name for node in ast.walk(ast.parse(text))
                  if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                  and node.name in OWN_HELPERS)


def _is_a_config(value: str) -> bool:
    try:
        return isinstance(yaml.safe_load(value), dict)
    except yaml.YAMLError:
        return False


def choice_writes(text: str) -> list[int]:
    """The lines of `text` that write a `models.yaml` in a shape that can
    change a choice: anything but a constant that is not a config, outside
    `_write_code_side`."""
    found: list[int] = []

    def walk(node: ast.AST, inside_writer: bool) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            inside_writer = inside_writer or node.name == CODE_SIDE_WRITER
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("write_text", "write_bytes")
                and "models.yaml" in (ast.get_source_segment(text, node.func.value) or "")
                and not inside_writer):
            arg = node.args[0] if node.args else None
            constant = (isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                        and not _is_a_config(arg.value))
            if not constant:
                found.append(node.lineno)
        for child in ast.iter_child_nodes(node):
            walk(child, inside_writer)

    walk(ast.parse(text), False)
    return found


def bare_ids(text: str) -> list[tuple[int, str]]:
    """`(line, id)` for every model id spelled in `text` outside a comment —
    docstrings and strings included."""
    found = []
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        if tok.type == tokenize.COMMENT:
            continue
        for match in BARE_ID.finditer(tok.string):
            line = tok.start[0] + tok.string.count("\n", 0, match.start())
            found.append((line, match.group(0)))
    return found


# --------------------------------------------------------------------------- #
# The three files                                                              #
# --------------------------------------------------------------------------- #

def test_none_of_the_three_names_the_generator():
    found = {rel: generator_mentions(_source(rel)) for rel in FILES}
    assert not any(found.values()), found


def test_none_of_the_three_defines_its_own_tree_or_document_helpers():
    found = {rel: own_helpers(_source(rel)) for rel in FILES}
    assert not any(found.values()), found


def test_none_of_the_three_writes_a_trees_models_yaml_to_change_a_choice():
    found = {rel: choice_writes(_source(rel)) for rel in FILES}
    assert not any(found.values()), found


def test_none_of_the_three_spells_a_model_id_outside_a_comment():
    found = {rel: bare_ids(_source(rel)) for rel in FILES}
    assert not any(found.values()), found


# --------------------------------------------------------------------------- #
# The scans find what they look for                                            #
# --------------------------------------------------------------------------- #

def test_the_generator_scan_finds_a_named_generator():
    text = f"import os\nsubprocess.run(['scripts/{GENERATOR}.py'])\n"
    assert generator_mentions(text) == [2]


def test_the_helper_scan_finds_an_own_make_tree_and_lead_with():
    text = "def make_tree(base):\n    pass\n\ndef lead_with(cfg):\n    pass\n"
    assert own_helpers(text) == ["lead_with", "make_tree"]
    assert own_helpers("from model_fixture import lead_with\n") == []


def test_the_write_scan_finds_a_dumped_config_and_admits_a_broken_file():
    dumped = ("def f(tree, cfg):\n"
              "    (tree / 'config' / 'models.yaml').write_text(yaml.safe_dump(cfg))\n")
    assert choice_writes(dumped) == [2]
    as_text = ("def f(tree):\n"
               "    (tree / 'config' / 'models.yaml').write_text('ladders: {}\\n')\n")
    assert choice_writes(as_text) == [2]
    broken = ("def f(tree):\n"
              "    (tree / 'config' / 'models.yaml').write_text('{{ not yaml\\n')\n")
    assert choice_writes(broken) == []
    guarded = (f"def {CODE_SIDE_WRITER}(tree, cfg):\n"
               "    (tree / 'config' / 'models.yaml').write_text(yaml.safe_dump(cfg))\n")
    assert choice_writes(guarded) == []


def test_the_id_scan_reads_strings_and_docstrings_and_skips_comments():
    model = "-".join(("claude", "sonnet", "5"))
    text = (f'"""The scenario.\n\nBuilt on {model}."""\n'
            f"# {model} in a comment is history\n"
            f"MODEL = '{model}'\n")
    assert bare_ids(text) == [(3, model), (5, model)]
