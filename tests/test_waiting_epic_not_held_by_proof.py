"""RED-first: a waiting epic is never held by a proof (DRE-7831).

THE INCIDENT. 2026-10-23's 2:00 PM PT agent-bureau sweep still held the Queued
epic DRE-7522 behind DRE-7365, an In Progress epic whose only open child is its
PROOF card; DRE-6647 and DRE-6648 sat the same way behind DRE-6646. DRE-7695,
DRE-6976, DRE-7001 and DRE-7305 waited on a PROOF card named directly as their
blocker. An operator removed every one of those relations by hand.

THE RULE. The strict branch of `reconcile.epic_blockers_unmet`
(`release_at_build_done=False`, the auto-advance and the return pass) releases
two kinds of blocker, and every other blocker still waits for Done:

  1. An In Progress blocker EPIC that `_not_built_out` reads as built out —
     the epic test `card_relation_blockers` already makes (DRE-7774);
  2. A PROOF card whose parent epic is Done or built out by the same reading.
     Its title and parent come off one single-issue read per distinct blocker
     per pass, cleared by `reset_sweep_cards`.

The return pass's receipt names a released blocker as released, never as
`is In Progress`, and ends `and no blocker still holds it`.

Run: cd bureau-pipeline && python3 -m pytest tests/test_waiting_epic_not_held_by_proof.py -v
"""
from __future__ import annotations

import inspect
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")

import linear_ops  # noqa: E402
import reconcile  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
AT = "2026-10-23T21:00:00.000Z"

#: The waiting epic E, a blocker epic B and its proof, a PROOF card P named
#: directly as the blocker and P's parent epic, and an ordinary card.
E, B, B_PROOF = "DRE-8800", "DRE-8700", "DRE-8703"
P, P_PARENT, PLAIN = "DRE-8710", "DRE-8720", "DRE-8730"


@pytest.fixture(autouse=True)
def _fresh_pass(monkeypatch):
    monkeypatch.setattr(reconcile, "REPO_SLUG", "agent-bureau")
    reconcile.reset_sweep_cards()
    yield
    reconcile.reset_sweep_cards()


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------
def _waiting(*blocked_by, state=reconcile.QUEUE_LANE):
    """E as the pass's epic record carries it (`EPIC_RECORD_GQL`'s shape)."""
    return {
        "identifier": E,
        "description": "**Repo:** agent-bureau\nan epic still to be planned",
        "state": {"name": state},
        "children": {"nodes": []},
        "history": {"nodes": []},
        "comments": {"nodes": [], "pageInfo": {"hasNextPage": False}},
        "inverseRelations": {
            "nodes": [
                {"type": "blocks", "issue": {"identifier": i, "state": {"name": s}}}
                for i, s in blocked_by
            ],
            "pageInfo": {"hasNextPage": False, "endCursor": None},
        },
    }


def _kid(identifier, state, *, title="a build card"):
    """One child as `epic_cap.IN_MOTION_NODE` selects it."""
    return {
        "identifier": identifier,
        "title": title,
        "state": {"name": state},
        "labels": {"nodes": [], "pageInfo": {"hasNextPage": False}},
        "children": {"nodes": []},
    }


def _proof_kid(identifier, state="Backlog"):
    return _kid(identifier, state, title="PROOF: the blocking epic works live")


def _epic(identifier, kids, *, state="In Progress"):
    """A blocker epic as `_blocker_epic`'s read returns it."""
    return {
        "identifier": identifier,
        "title": f"[EPIC] agent-bureau: {identifier}",
        "priority": 3,
        "createdAt": AT,
        "state": {"name": state},
        "children": {"nodes": list(kids), "pageInfo": {"hasNextPage": False}},
    }


def _card(identifier, title, *, state="In Progress"):
    """A card as `_blocker_epic`'s read returns it: no children."""
    return {
        "identifier": identifier, "title": title, "priority": 3, "createdAt": AT,
        "state": {"name": state},
        "children": {"nodes": [], "pageInfo": {"hasNextPage": False}},
    }


def _proof_card(identifier=P, *, parent=P_PARENT, parent_state="In Progress",
                state="In Review"):
    """P as the new single-issue read returns it: its title and its parent."""
    return {
        "identifier": identifier,
        "title": "PROOF: the parent epic works on the live console",
        "state": {"name": state},
        "parent": ({"identifier": parent, "state": {"name": parent_state}}
                   if parent else None),
    }


#: B with its one build card Done and only its proof open.
B_BUILT_OUT = _epic(B, [_kid("DRE-8701", "Done"), _proof_kid(B_PROOF)])
#: P's parent, built out: its build card Done and P itself open.
PARENT_BUILT_OUT = _epic(P_PARENT, [_kid("DRE-8721", "Done"), _proof_kid(P, "In Review")])


def _is_epic_read(flat: str) -> bool:
    return "issue(id: $id)" in flat and "labels(first: 10)" in flat


def _is_card_read(flat: str) -> bool:
    return "issue(id: $id)" in flat and "parent {" in flat and "labels(" not in flat


class FakeLinear:
    """The batched epic record, `_blocker_epic`'s read, and the new read of a
    blocker's title and parent — each failing on its own when told to."""

    def __init__(self, waiting, *, epics=(), cards=(), epic_errors=None, card_errors=None):
        self.waiting = waiting
        self.epics = {e["identifier"]: e for e in epics}
        self.cards = {c["identifier"]: c for c in cards}
        self.epic_errors = dict(epic_errors or {})
        self.card_errors = dict(card_errors or {})
        self.epic_reads: list[str] = []
        self.card_reads: list[str] = []

    def gql(self, query, variables=None):
        v = variables or {}
        flat = " ".join(query.split())
        if "$numbers" in query:
            return {"issues": {"nodes": [self.waiting],
                               "pageInfo": {"hasNextPage": False, "endCursor": None}}}
        ident = v.get("id")
        if _is_epic_read(flat):
            self.epic_reads.append(ident)
            if ident in self.epic_errors:
                raise self.epic_errors[ident]
            return {"issue": self.epics.get(ident)}
        if _is_card_read(flat):
            self.card_reads.append(ident)
            if ident in self.card_errors:
                raise self.card_errors[ident]
            return {"issue": self.cards.get(ident)}
        raise linear_ops.LinearError(f"FakeLinear: unexpected query {flat[:80]}")


def _return_pass(fake):
    """One return pass over E; returns (advance, comment) recorders."""
    with patch.object(linear_ops, "gql", side_effect=fake.gql), \
            patch.object(linear_ops, "cmd_advance", return_value=True) as advance, \
            patch.object(linear_ops, "cmd_comment") as comment:
        reconcile.return_waiting_epics({E})
    return advance, comment


def _gate_lines(out: str) -> list[str]:
    return [line for line in out.splitlines() if line.startswith(f"epic-gate: {E}")]


def _receipts(comment) -> list[str]:
    return [c.args[1] for c in comment.call_args_list if c.args[0] == E]


def _assert_held(capsys, fake, *needles):
    advance, comment = _return_pass(fake)
    out = capsys.readouterr().out
    advance.assert_not_called()
    assert _receipts(comment) == [], out
    lines = _gate_lines(out)
    assert lines, out
    held = " | ".join(lines)
    for needle in needles:
        assert needle in held, lines
    assert "released" not in held, lines
    return lines


# --------------------------------------------------------------------------
# rule 1: a built-out In Progress blocker epic releases the waiting epic
# --------------------------------------------------------------------------
def test_a_built_out_blocker_epic_returns_the_waiting_epic_to_planning(capsys):
    """E's only blocker is B, In Progress, one build card Done and only its
    proof open. On main, E stays in Queued under `waits for Done`."""
    fake = FakeLinear(_waiting((B, "In Progress")), epics=[B_BUILT_OUT])
    advance, _ = _return_pass(fake)
    out = capsys.readouterr().out
    advance.assert_called_once_with(E, "Planning", reconcile.QUEUE_FROM)
    released = [line for line in _gate_lines(out) if "released at build-done" in line]
    assert released, out
    assert B in released[0], released
    assert f"open: {B_PROOF} PROOF" in released[0], released
    assert "waits for Done" not in out, out
    assert fake.epic_reads == [B]
    assert fake.card_reads == []  # rule 1 settled B: no second read


def test_the_return_pass_receipt_names_the_released_blocker_as_built_out(capsys):
    """The receipt on the first criterion's E: B is named as built out, never
    as `is In Progress`, and the trailing clause is the new one."""
    fake = FakeLinear(_waiting((B, "In Progress")), epics=[B_BUILT_OUT])
    _, comment = _return_pass(fake)
    receipts = _receipts(comment)
    assert len(receipts) == 1, receipts
    assert receipts[0].startswith(
        f"🧹 Auto-advanced {reconcile.QUEUE_LANE} → Planning: blocker {B} is built "
        f"out (open: {B_PROOF} PROOF), and no blocker still holds it."
    ), receipts[0]
    assert "In Progress" not in receipts[0].split("\n")[0], receipts[0]
    assert "every blocker is now closed" not in receipts[0]


def test_a_terminal_blocker_keeps_its_state_beside_a_released_one(capsys):
    fake = FakeLinear(_waiting((B, "In Progress"), ("DRE-8650", "Done")),
                      epics=[B_BUILT_OUT])
    advance, comment = _return_pass(fake)
    advance.assert_called_once()
    (receipt,) = _receipts(comment)
    first = receipt.split("\n")[0]
    assert "DRE-8650 is Done" in first, first
    assert f"{B} is built out (open: {B_PROOF} PROOF)" in first, first
    assert first.endswith("and no blocker still holds it."), first


def test_an_epic_with_a_buildable_child_open_still_holds_and_names_it(capsys):
    blocked = _epic(B, [_kid("DRE-8701", "Done"), _kid("DRE-8702", "Backlog"),
                        _proof_kid(B_PROOF)])
    fake = FakeLinear(_waiting((B, "In Progress")), epics=[blocked])
    _assert_held(capsys, fake, B, "not built out", "a buildable child is open: DRE-8702")


def test_an_ordinary_in_progress_card_still_waits_for_done(capsys):
    """A blocker `mid_epic.is_epic` does not read as an epic — no `[EPIC]`, no
    children — is no epic to be built out, and it is not a proof."""
    fake = FakeLinear(
        _waiting((PLAIN, "In Progress")),
        epics=[_card(PLAIN, "agent-bureau: a build card")],
        cards=[{"identifier": PLAIN, "title": "agent-bureau: a build card",
                "state": {"name": "In Progress"},
                "parent": {"identifier": P_PARENT, "state": {"name": "In Progress"}}}],
    )
    _assert_held(capsys, fake, PLAIN, "waits for Done")


def test_an_ordinary_card_in_review_still_waits_for_done(capsys):
    fake = FakeLinear(
        _waiting((PLAIN, "In Review")),
        cards=[{"identifier": PLAIN, "title": "agent-bureau: a build card",
                "state": {"name": "In Review"}, "parent": None}],
    )
    _assert_held(capsys, fake, PLAIN, "(In Review)", "waits for Done")
    assert fake.epic_reads == []


# --------------------------------------------------------------------------
# rule 2: a PROOF card whose epic is built out (or Done) releases it
# --------------------------------------------------------------------------
def test_a_proof_of_a_built_out_epic_returns_the_waiting_epic_to_planning(capsys):
    """E's only blocker is P, In Review, whose parent is built out. On main, E
    stays in Queued under `waits for Done`."""
    fake = FakeLinear(_waiting((P, "In Review")), epics=[PARENT_BUILT_OUT],
                      cards=[_proof_card()])
    advance, comment = _return_pass(fake)
    out = capsys.readouterr().out
    advance.assert_called_once_with(E, "Planning", reconcile.QUEUE_FROM)
    released = [line for line in _gate_lines(out) if "released" in line]
    assert released, out
    assert f"{P} is a proof of a built-out epic, {P_PARENT}" in released[0], released
    (receipt,) = _receipts(comment)
    assert receipt.startswith(
        f"🧹 Auto-advanced {reconcile.QUEUE_LANE} → Planning: blocker {P} is a "
        f"proof of built-out epic {P_PARENT}, and no blocker still holds it."
    ), receipt
    assert "In Review" not in receipt.split("\n")[0], receipt
    assert fake.card_reads == [P]
    assert fake.epic_reads == [P_PARENT]


def test_a_proof_of_a_done_epic_releases_without_reading_the_epic(capsys):
    fake = FakeLinear(_waiting((P, "In Review")),
                      cards=[_proof_card(parent_state="Done")])
    advance, _ = _return_pass(fake)
    out = capsys.readouterr().out
    advance.assert_called_once_with(E, "Planning", reconcile.QUEUE_FROM)
    assert any(f"{P} is a proof of a built-out epic, {P_PARENT}" in line
               for line in _gate_lines(out)), out
    assert fake.epic_reads == []


def test_an_in_progress_proof_of_a_built_out_epic_releases(capsys):
    """An In Progress PROOF is read by the epic test first, is no epic, and
    then by rule 2."""
    fake = FakeLinear(
        _waiting((P, "In Progress")),
        epics=[_card(P, "PROOF: the parent epic works"), PARENT_BUILT_OUT],
        cards=[_proof_card(state="In Progress")],
    )
    advance, _ = _return_pass(fake)
    out = capsys.readouterr().out
    advance.assert_called_once()
    assert any("is a proof of a built-out epic" in line for line in _gate_lines(out)), out


def test_a_proof_whose_parent_has_a_buildable_child_still_holds(capsys):
    parent = _epic(P_PARENT, [_kid("DRE-8722", "In Progress"), _proof_kid(P, "In Review")])
    fake = FakeLinear(_waiting((P, "In Review")), epics=[parent], cards=[_proof_card()])
    _assert_held(capsys, fake, P, P_PARENT, "a buildable child is open: DRE-8722")


def test_a_proof_with_no_parent_still_holds(capsys):
    fake = FakeLinear(_waiting((P, "In Review")), cards=[_proof_card(parent=None)])
    _assert_held(capsys, fake, P, "no parent")


def test_a_proof_whose_own_read_fails_holds_with_the_reason(capsys):
    fake = FakeLinear(_waiting((P, "In Review")), epics=[PARENT_BUILT_OUT],
                      card_errors={P: linear_ops.LinearError("Linear timed out")})
    _assert_held(capsys, fake, P, "could not be read", "Linear timed out")


def test_a_proof_whose_parent_cannot_be_read_holds_with_the_reason(capsys):
    fake = FakeLinear(_waiting((P, "In Review")), cards=[_proof_card()],
                      epic_errors={P_PARENT: linear_ops.LinearError("parent read refused")})
    _assert_held(capsys, fake, P, P_PARENT, "parent read refused")


def test_the_blocker_read_is_once_per_pass_and_the_reset_drops_it(capsys):
    fake = FakeLinear(_waiting((P, "In Review")), epics=[PARENT_BUILT_OUT],
                      cards=[_proof_card()])
    with patch.object(linear_ops, "gql", side_effect=fake.gql):
        assert reconcile.epic_blockers_unmet(E, release_at_build_done=False) is False
        assert reconcile.epic_blockers_unmet(E, release_at_build_done=False) is False
        assert fake.card_reads == [P]
        reconcile.reset_sweep_cards()
        assert reconcile.epic_blockers_unmet(E, release_at_build_done=False) is False
    assert fake.card_reads == [P, P]


def test_a_released_blocker_does_not_free_an_epic_another_blocker_holds(capsys):
    fake = FakeLinear(
        _waiting((B, "In Progress"), (PLAIN, "In Review")),
        epics=[B_BUILT_OUT],
        cards=[{"identifier": PLAIN, "title": "a build card",
                "state": {"name": "In Review"}, "parent": None}],
    )
    lines = _assert_held(capsys, fake, PLAIN, "waits for Done")
    assert all(B not in line for line in lines), lines


# --------------------------------------------------------------------------
# one definition, and the record the change touches
# --------------------------------------------------------------------------
def test_the_strict_rule_reads_the_card_gates_epic_test():
    """The strict branch asks `card_relation_blockers`, so there is one test
    of an epic and one definition of built out."""
    body = inspect.getsource(reconcile.epic_blockers_unmet)
    assert "card_relation_blockers(" in body
    source = Path(reconcile.__file__).read_text(encoding="utf-8")
    assert source.count("def _not_built_out") == 1
    assert "counts_against_cap" not in body and "is_epic(" not in body


def test_the_standard_says_a_waiting_epic_is_not_held_by_a_proof():
    text = (ROOT / "standards" / "card-quality.md").read_text(encoding="utf-8")
    start = text.index("**When the epic behind a blocker moves (DRE-6407).**")
    paragraph = " ".join(text[start:text.index("\n\n", start)].split())
    assert "DRE-7831" in paragraph, paragraph
    assert "not held by a proof" in paragraph, paragraph
    assert "proof included" not in paragraph, paragraph


def test_the_acts_row_says_no_blocker_still_holds_the_waiting_epic():
    acts = json.loads((ROOT / "config" / "pipeline-acts.json").read_text(encoding="utf-8"))
    rows = acts["unconverted"]
    (row,) = [r for r in rows if r.get("anchor") == "🧹 Auto-advanced {QUEUE_LANE} → Planning"]
    assert "no blocker still holds" in row["means"], row["means"]
    assert "proof" in row["means"], row["means"]
