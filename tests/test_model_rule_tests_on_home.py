"""The policy and schema rule tests prove their scenarios on home documents (DRE-6988).

The selector selects on the home or on the defaults in the code (DRE-6939), and
the generator that turned a tree's `config/models.yaml` into those defaults goes
in DRE-6982. A rule test that changed a choice by writing a tree's
`models.yaml` and regenerating would then test nothing. So each scenario in the
four files below is a document built from `model_fixture.fixture_home()`, and
no file names the generator, its literal renderer or its registry splicer.

The model ids those tests expect are read off the frozen document through
`model_fixture.ladder`, never typed: a bare model id outside a comment is
refused here too.

Run: cd bureau-pipeline && python3 -m pytest tests/test_model_rule_tests_on_home.py -q
"""
from __future__ import annotations

import io
import re
import tokenize
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

FILES = (
    "tests/test_model_policy.py",
    "tests/test_model_projects_schema.py",
    "tests/test_session_agents.py",
    "tests/test_no_test_pins_a_console_choice.py",
)

# The generator's module, the policy file's runner of it, and the two
# functions of it the schema and session tests called.
GENERATOR_NAMES = ("sync_model_config", "_run_sync", "render_literal", "splice_registry")

# A write of a tree's `config/models.yaml`, however the path is spelled.
TREE_CONFIG_WRITE = re.compile(
    r"""["']config["']\s*/\s*["']models\.yaml["']\s*\)?\s*\.write_(?:text|bytes)"""
    r"""|_write_config\(""")

BARE_MODEL_ID = re.compile(r"claude-[a-z]+-[0-9]")


def _source(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def bare_model_ids(source: str) -> list[tuple[int, str]]:
    """`(line, id)` for every bare model id in `source` outside a comment —
    in code, a string or a docstring alike."""
    found = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            continue
        for match in BARE_MODEL_ID.finditer(token.string):
            found.append((token.start[0] + token.string[:match.start()].count("\n"),
                          match.group(0)))
    return found


@pytest.mark.parametrize("rel", FILES)
def test_no_file_names_the_generator(rel):
    source = _source(rel)
    assert [name for name in GENERATOR_NAMES if name in source] == []


@pytest.mark.parametrize("rel", FILES)
def test_no_file_writes_a_trees_models_yaml(rel):
    lines = [f"{n}: {line.strip()}" for n, line in enumerate(_source(rel).splitlines(), 1)
             if TREE_CONFIG_WRITE.search(line)]
    assert lines == []


@pytest.mark.parametrize("rel", FILES)
def test_no_file_types_a_bare_model_id_outside_a_comment(rel):
    assert bare_model_ids(_source(rel)) == []


def test_the_id_check_skips_a_comment_and_nothing_else():
    """The check itself must bite, or the test above is vacuous."""
    model = "-".join(("claude", "opus", "9"))
    source = (
        f'"""A docstring naming {model}."""\n'
        f"# a comment naming {model}\n"
        f'PICKED = "{model}"  # and a trailing one\n'
        f"x = 1  # {model}\n"
    )
    assert bare_model_ids(source) == [(1, model), (3, model)]


def test_the_write_check_catches_both_spellings():
    assert TREE_CONFIG_WRITE.search(
        '(tree / "config" / "models.yaml").write_text(yaml.safe_dump(cfg))')
    assert TREE_CONFIG_WRITE.search("        _write_config(tree, cfg)")
    assert not TREE_CONFIG_WRITE.search(
        'yaml.safe_load((tree / "config" / "models.yaml").read_text())')
