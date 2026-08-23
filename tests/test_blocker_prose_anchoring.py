"""TDD for anchored blocker-line detection in the reconcile sweep (DRE-2670).

`blockers_of()` read a blocker out of ANY line whose text happened to contain
"blocked by" / "serialize after" / "depends on" plus a DRE-N. Applied to an
EPIC by `epic_blockers_unmet()`, that turned well-written planning prose into a
dependency gate: epic DRE-2492 has ZERO formal `blockedBy` relations, yet two
sentences in its body named two of its OWN CHILDREN as blockers —

    "DRE-2496 lands first. B3 is formally blocked by it"
    "Both depend on DRE-2494 only - neither depends on the other, ..."

— so the epic was blocked by its children, the children could not promote until
the epic was unblocked, and the children were what would unblock it. Five
Backlog cards sat unpromotable for five days on ~480 consecutive GREEN sweeps.
The second line is the sharpest case: a sentence whose literal meaning is
"there are no dependencies here" was parsed as declaring one.

This module TDDs two behaviors:

  1. ANCHORING — a blocker line must DECLARE a dependency, not mention one: the
     phrase must open the line (after list/quote/bold markup) and be followed by
     a colon or the ids themselves. A mid-sentence mention yields no blockers.

  2. PROSE-ONLY IS VISIBLE — an epic held by a description line that no formal
     `blockedBy` relation corroborates says so in the log, distinguishably from
     one held by a real relation. A card frozen by its own documentation should
     say so.

Reconcile governs promotion for EVERY product repo, so a bug here breaks
everyone - hence test-first.

Run: cd bureau-pipeline && python3 -m pytest tests/test_blocker_prose_anchoring.py -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")

import reconcile  # noqa: E402


# The live epic body, verbatim in the parts that matter (DRE-2492). Both lines
# name a CHILD of the epic; neither declares a dependency ON the epic.
DRE_2492_PROSE = (
    "**Repo:** agent-bureau\n"
    "\n"
    "Wave 2 sequencing: DRE-2496 **lands first.** B3 is formally blocked by it, "
    "so it cannot start before the rail exists.\n"
    "\n"
    "Both depend on DRE-2494 only - neither depends on the other, and neither "
    "blocks anything in wave 4.\n"
    "\n"
    "DRE-2494 slipping delays everything in wave 3.\n"
)


@pytest.fixture(autouse=True)
def _pin_repo_slug(monkeypatch):
    """reconcile.REPO_SLUG is bound at import; pin it so promote_ready
    recognises this test's agent-bureau cards regardless of collection order."""
    monkeypatch.setattr(reconcile, "REPO_SLUG", "agent-bureau")


def _card(description, identifier="DRE-2492", parent=None):
    return {
        "identifier": identifier,
        "parent": {"identifier": parent} if parent else None,
        "description": description,
        "inverseRelations": {"nodes": []},
    }


# ---------------------------------------------------------------------------
# Behavior 1: anchoring - a mention is not a declaration
# ---------------------------------------------------------------------------
def test_the_live_epic_prose_yields_zero_blockers():
    """The regression fixture: DRE-2492's real body, unedited, is not blocked."""
    assert reconcile.blockers_of(_card(DRE_2492_PROSE)) == set()


def test_neither_depends_on_the_other_yields_zero_blockers():
    """A sentence that literally DENIES a dependency must not declare one."""
    card = _card("Both depend on DRE-2494 only - neither depends on the other.")
    assert reconcile.blockers_of(card) == set()


def test_mid_sentence_blocked_by_yields_zero_blockers():
    """"B3 is formally blocked by it" - a mention mid-line, not a declaration."""
    card = _card("DRE-2496 lands first. B3 is formally blocked by it, per the plan.")
    assert reconcile.blockers_of(card) == set()


def test_mid_sentence_serialize_after_yields_zero_blockers():
    card = _card("We should probably serialize after DRE-2494 ships, but not yet.")
    assert reconcile.blockers_of(card) == set()


@pytest.mark.parametrize(
    "line",
    [
        "**Blocked by:** DRE-9",
        "Blocked by: DRE-9",
        "blocked by: dre-9".upper(),
        "- **Blocked by:** DRE-9",
        "* Blocked by: DRE-9",
        "> **Blocked by:** DRE-9",
        "Blocked by DRE-9",
        "**Depends on:** DRE-9",
        "Depends on DRE-9",
        "Serialize after: DRE-9",
        "Serialize after DRE-9",
    ],
)
def test_declaring_forms_still_parse(line):
    """Anchoring must not cost us any real declaration form."""
    card = _card(f"Do the work.\n\n{line}\n\n## Acceptance criteria\n- [ ] x")
    assert reconcile.blockers_of(card) == {"DRE-9"}


def test_prose_tail_after_a_declaration_still_parses():
    """The DRE-1233 form: a declaring line whose ids sit in prose after it."""
    card = _card("Serialize after: all other DRE-1200 work", identifier="DRE-1233")
    assert reconcile.blockers_of(card) == {"DRE-1200"}


def test_declaration_and_denial_in_one_body():
    """A body with BOTH a real declaration and innocuous prose keeps only the
    declaration - the anchor must not be all-or-nothing per body."""
    card = _card(
        "**Blocked by:** DRE-9\n"
        "\n"
        "Note that DRE-11 depends on nothing here, and neither depends on the other.\n"
    )
    assert reconcile.blockers_of(card) == {"DRE-9"}


def test_relation_blockers_are_untouched_by_anchoring():
    """Formal relations are the source of truth and never went through the regex."""
    card = _card("Prose that mentions DRE-11 depends on nobody.")
    card["inverseRelations"]["nodes"] = [
        {"type": "blocks", "issue": {"identifier": "DRE-9", "state": {"name": "In Progress"}}}
    ]
    assert reconcile.blockers_of(card) == {"DRE-9"}


# ---------------------------------------------------------------------------
# Behavior 1 at the epic gate: DRE-2492's children are promotable again
# ---------------------------------------------------------------------------
def _epic_2492():
    return {
        "identifier": "DRE-2492",
        "description": DRE_2492_PROSE,
        "inverseRelations": {"nodes": []},  # zero formal blockedBy relations
    }


def test_epic_held_by_its_own_prose_is_no_longer_blocked():
    with patch.object(reconcile, "_fetch_epic_relations", return_value=_epic_2492()):
        assert reconcile.epic_blockers_unmet("DRE-2492") is False


def test_children_of_the_prose_jammed_epic_promote():
    """End-to-end through promote_ready with the epic's REAL body: the five
    Backlog children promote with no edit to the epic's description."""
    reconcile._write_failures.clear()
    kids = [
        {
            "identifier": ident,
            "description": "**Repo:** agent-bureau\nwork",
            "parent": {"identifier": "DRE-2492", "state": {"name": "In Progress"}},
            "labels": {"nodes": [{"name": "size:M"}]},
            "comments": {"nodes": []},
            "inverseRelations": {"nodes": []},
        }
        for ident in ("DRE-2494", "DRE-2496", "DRE-2497", "DRE-2498", "DRE-2650")
    ]
    with patch.object(reconcile, "backlog_children", return_value=kids), patch.object(
        reconcile, "_fetch_epic_relations", return_value=_epic_2492()
    ), patch.object(reconcile.linear_ops, "cmd_advance") as advance, patch.object(
        reconcile.linear_ops, "cmd_comment"
    ):
        promoted = reconcile.promote_ready(active_count=0)
    assert promoted == 5
    assert advance.call_count == 5


def test_epic_declaring_a_real_blocker_in_prose_still_holds():
    """Control: anchoring must not disarm the gate. An epic whose body DECLARES
    a blocker that is not Done still holds its children."""
    epic = {
        "identifier": "DRE-800",
        "description": "**Repo:** agent-bureau\n**Blocked by:** DRE-700\nepic B",
        "inverseRelations": {"nodes": []},
    }
    with patch.object(reconcile, "_fetch_epic_relations", return_value=epic), patch.object(
        reconcile, "card_state", return_value="In Progress"
    ):
        assert reconcile.epic_blockers_unmet("DRE-800") is True


# ---------------------------------------------------------------------------
# Behavior 2: prose-only holds are distinguishable in the log
# ---------------------------------------------------------------------------
def test_prose_only_hold_is_logged_as_prose_only(capsys):
    """No formal relation corroborates the declaration -> say so, loudly."""
    epic = {
        "identifier": "DRE-800",
        "description": "**Repo:** agent-bureau\n**Blocked by:** DRE-700\nepic B",
        "inverseRelations": {"nodes": []},
    }
    with patch.object(reconcile, "_fetch_epic_relations", return_value=epic), patch.object(
        reconcile, "card_state", return_value="In Progress"
    ):
        assert reconcile.epic_blockers_unmet("DRE-800") is True
    out = capsys.readouterr().out
    assert "DRE-800" in out and "DRE-700" in out
    assert "prose" in out.lower()
    assert "no formal" in out.lower()


def test_relation_hold_is_not_logged_as_prose(capsys):
    """A real relation reads differently from a phantom - different fact,
    different fix."""
    epic = {
        "identifier": "DRE-800",
        "description": "**Repo:** agent-bureau\nepic B",
        "inverseRelations": {
            "nodes": [
                {"type": "blocks", "issue": {"identifier": "DRE-700", "state": {"name": "In Progress"}}}
            ]
        },
    }
    with patch.object(reconcile, "_fetch_epic_relations", return_value=epic):
        assert reconcile.epic_blockers_unmet("DRE-800") is True
    out = capsys.readouterr().out
    assert "DRE-700" in out
    assert "relation" in out.lower()
    assert "prose" not in out.lower()
