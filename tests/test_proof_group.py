"""DRE-7284 — proof runs get their own Proof model group.

`agents.proof` names a `proof` kind, labeled Proof, whose ladder lists the
Builders' models in the same order. Nothing about a proof run's model changes
on the day it lands: the top of the Proof ladder is the top of the Builders
ladder. What changes is that the proof model can move alone — a reorder of the
Proof ladder moves the model chosen for `proof` and leaves every build role
that is not on it where it was.

Which group `proof` is in, and what each group runs, are choices the console
can change, and since DRE-6939 the selector reads them off the home, never off
a tree's `config/models.yaml`. So each check runs on a home document built
from the frozen one (`model_fixture.fixture_home()`): `proof` moved onto a
Proof group of its own, listing the models of the group it was in, in the same
order — and that document with the Proof group's last model led with. Each
check reads the group `agents.proof` names in the document, and the roles on
it, rather than assume today's mapping.

Each selection runs the selector's CLI in a tree `model_fixture.fixture_tree`
builds without the generator (DRE-8018), the document handed to it through
`home_env`, with every model up (`BUREAU_FAKE_AVAILABLE`) and no network
call. Every model id a check expects is read off the document, never typed
(DRE-6853), and the group's models and label are not asserted as literals:
they are choices the console can change (DRE-6866).

Run: cd bureau-pipeline && python3 -m pytest tests/test_proof_group.py -q
"""

from __future__ import annotations

import copy
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

import model_fixture  # noqa: E402
from model_fixture import fixture_home, fixture_tree, home_env, ladder, lead_with  # noqa: E402

PROOF = "proof"

# The six build roles the Builders group names, which a Proof-only change
# must leave alone.
BUILD_ROLES = ("engineer", "frontend", "devops", "database-architect", "fixer", "repairer")


def with_proof_group(doc: dict) -> dict:
    """`doc` with `proof` on a Proof group of its own, listing the models of
    the group it was in, in the same order — the day the group landed."""
    new = copy.deepcopy(doc)
    models = ladder(new, new["agents"][PROOF])
    new["groups"][PROOF] = {"label": "Proof",
                            "purpose": "Run a proof card's checks and record what it saw.",
                            "models": list(models)}
    return model_fixture.move_agent(new, PROOF, PROOF)


def proof_group(doc: dict) -> str:
    """The group `agents.proof` names in a home document."""
    return doc["agents"][PROOF]


def last_model_first(doc: dict) -> dict:
    """`doc` with the group proof is in led with that group's last model."""
    group = proof_group(doc)
    return lead_with(doc, group, ladder(doc, group)[-1])


def select(tree: Path, doc: dict, role: str) -> tuple[str, str]:
    """`(model, note)` from the tree's selector CLI on `doc`, the way a
    workflow's Select model step calls it."""
    note = tree / "note.txt"
    if note.exists():
        note.unlink()
    env = home_env(tree, doc, BUREAU_FAKE_AVAILABLE="{}")
    env.pop("BUREAU_MODEL_HOME_TOKEN", None)
    proc = subprocess.run(
        [sys.executable, str(tree / "scripts" / "model_fallback.py"), "select", role,
         "--explain-file", str(note)],
        cwd=tree, capture_output=True, text=True, env=env, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert "model-home: read" in proc.stderr, proc.stderr
    return proc.stdout.strip(), note.read_text().rstrip("\n")


@pytest.fixture(scope="module")
def base(tmp_path_factory) -> tuple[Path, dict]:
    """A tree, and the frozen document with proof on a Proof group."""
    tree = fixture_tree(tmp_path_factory.mktemp("base") / "tree", regenerate=False)
    return tree, with_proof_group(fixture_home())


@pytest.fixture(scope="module")
def reordered(tmp_path_factory, base) -> tuple[Path, dict]:
    """A tree, and that document with the Proof group's last model first."""
    tree = fixture_tree(tmp_path_factory.mktemp("reordered") / "tree", regenerate=False)
    return tree, last_model_first(base[1])


def test_a_proof_run_selects_the_first_model_of_the_kind_it_is_on(base):
    tree, doc = base
    group = proof_group(doc)
    top = ladder(doc, group)[0]
    chosen, note = select(tree, doc, PROOF)
    assert chosen == top
    assert f"{top} chosen for proof ({group} kind)" in note


def test_reordering_the_proof_ladder_moves_proof_and_only_the_build_roles_on_it(
        base, reordered):
    (base_tree, doc), (moved_tree, moved_doc) = base, reordered
    group = proof_group(doc)
    moved = ladder(moved_doc, group)[0]
    assert moved == ladder(doc, group)[-1]

    before, _ = select(base_tree, doc, PROOF)
    after, note = select(moved_tree, moved_doc, PROOF)
    assert after == moved
    assert after != before
    assert f"{moved} chosen for proof ({group} kind)" in note

    for role in BUILD_ROLES:
        if doc["agents"][role] == group:
            assert select(moved_tree, moved_doc, role)[0] == moved, role
        else:
            assert (select(moved_tree, moved_doc, role)[0]
                    == select(base_tree, doc, role)[0]), role
