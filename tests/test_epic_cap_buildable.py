"""RED-first: an epic takes a slot only while it has a card left to build (DRE-5918).

The CEO, 2026-10-05: "An epic takes a slot only while it has a card left to
build. Once only proof, check or hand-done cards remain, it stops taking a
slot. A parent epic counts once, not again for each child epic under it."

Before this card `counts_against_cap` counted an In Progress epic if ANY child
was not itself an epic — Done and Canceled children included — and the
in-motion read never fetched a child's state or labels. On 10-05 it counted 17
epics when five had a card anyone would build, and four approvals queued
behind the twelve that were only waiting on a proof or a check.

The rule now, one definition: an In Progress epic counts iff it has at least
one BUILDABLE child — open (not Done, Canceled or Duplicate), not itself an
epic, not titled `PROOF:`, not labeled `hand-built` or `no-code`.

Run: cd bureau-pipeline && python3 -m pytest tests/test_epic_cap_buildable.py -v
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
# `reconcile` reads these at import; only its HAND_BUILT_LABEL is used here.
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import epic_cap  # noqa: E402
import linear_ops  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "dre-5918-in-progress-epics-2026-10-05.json"

IN_PROGRESS = "In Progress"


def _child(identifier="DRE-1", title="bureau-pipeline: a card", *, state="Backlog",
           labels=(), grandchildren=0, labels_truncated=False):
    return {
        "identifier": identifier,
        "title": title,
        "state": {"name": state},
        "labels": {"nodes": [{"name": n} for n in labels],
                   "pageInfo": {"hasNextPage": labels_truncated}},
        "children": {"nodes": [{"id": f"g{n}"} for n in range(grandchildren)]},
    }


def _epic(identifier="DRE-100", *children, truncated=False, state=IN_PROGRESS):
    return {
        "identifier": identifier,
        "title": f"[EPIC] bureau-pipeline: {identifier}",
        "state": {"name": state},
        "children": {"nodes": list(children),
                     "pageInfo": {"hasNextPage": truncated}},
    }


def _counts(epic, *, rollups=False) -> bool:
    return epic_cap.counts_against_cap(epic, count_rollup_parents=rollups)


DONE_PLAIN = _child("DRE-2", "bureau-pipeline: shipped", state="Done")


# --------------------------------------------------------------------------
# the board of 2026-10-05
# --------------------------------------------------------------------------
def _fixture() -> dict:
    with open(FIXTURE, encoding="utf-8") as fh:
        return json.load(fh)


def test_the_fixture_holds_the_17_epics_the_10_05_read_counted():
    epics = _fixture()["epics"]
    assert sorted(e["identifier"] for e in epics) == sorted(
        f"DRE-{n}" for n in (2713, 3530, 4267, 4271, 4498, 4626, 4721, 4723, 4963,
                             5129, 5365, 5415, 5464, 5577, 5783, 5852, 5863))
    assert all(e["state"]["name"] == IN_PROGRESS for e in epics)
    # Every one of them has an ordinary child, Done or not: the old rule
    # (`any(not _child_is_epic(c))`) counted all 17.
    assert all(any(not (c["children"]["nodes"] or "[epic]" in c["title"].lower())
                   for c in e["children"]["nodes"]) for e in epics)


def test_of_the_17_only_the_five_with_a_card_left_to_build_are_in_motion():
    doc = _fixture()
    got = epic_cap.in_motion(doc["epics"], count_rollup_parents=False)
    assert [e["identifier"] for e in got] == [
        "DRE-4626", "DRE-5129", "DRE-5464", "DRE-5852", "DRE-5863"]
    assert [e["identifier"] for e in got] == doc["in_motion_today"]


def test_a_parent_counts_once_not_again_for_its_child_epic():
    """DRE-4267 counted over DRE-4271, DRE-5577 over DRE-5783. Neither parent
    has a buildable card of its own, and neither child epic has one either."""
    epics = {e["identifier"]: e for e in _fixture()["epics"]}
    for parent, child in (("DRE-4267", "DRE-4271"), ("DRE-5577", "DRE-5783")):
        assert child in {c["identifier"] for c in epics[parent]["children"]["nodes"]}
        assert not _counts(epics[parent]), parent
        assert not _counts(epics[child]), child


def test_fleet_state_reads_the_10_05_board_as_five_of_fifteen(monkeypatch, capsys):
    epics = _fixture()["epics"]

    def gql(query, variables=None):
        if "Green Light" in query:
            return {"issues": {"nodes": [], "pageInfo": {"hasNextPage": False}}}
        start = int((variables or {}).get("after") or 0)
        chunk = epics[start:start + epic_cap.IN_MOTION_PAGE]
        more = start + epic_cap.IN_MOTION_PAGE < len(epics)
        return {"issues": {"nodes": chunk, "pageInfo": {
            "hasNextPage": more,
            "endCursor": str(start + epic_cap.IN_MOTION_PAGE) if more else None}}}

    monkeypatch.setattr(linear_ops, "gql", gql)
    assert epic_cap.main(["show"]) == 0
    out = capsys.readouterr().out
    assert "Epics in motion: 5 of 15" in out
    for identifier in ("DRE-4626", "DRE-5129", "DRE-5464", "DRE-5852", "DRE-5863"):
        assert identifier in out


# --------------------------------------------------------------------------
# the rule, one child at a time
# --------------------------------------------------------------------------
@pytest.mark.parametrize("only_open_child", [
    _child("DRE-3", "PROOF: the mechanism observed live", labels=("agent:ops",)),
    _child("DRE-3", "proof: lower case is still a proof", labels=("agent:ops",)),
    _child("DRE-3", "bureau-pipeline: a seven-day check", labels=("hand-built",)),
    _child("DRE-3", "bureau-pipeline: an operator step", labels=("no-code",)),
    _child("DRE-3", "bureau-pipeline: an operator step", labels=("No-Code",)),
    _child("DRE-3", "[EPIC] bureau-pipeline: part two"),
    _child("DRE-3", "bureau-pipeline: part two", grandchildren=1),
], ids=["proof", "proof-lower-case", "hand-built", "no-code", "no-code-cased",
        "child-epic-by-title", "child-epic-by-probe"])
def test_an_epic_whose_only_open_child_is_not_buildable_does_not_count(only_open_child):
    assert not _counts(_epic("DRE-100", DONE_PLAIN, only_open_child))


def test_a_child_epic_by_its_shape_stamp_does_not_count():
    stamped = {**_child("DRE-3", "bureau-pipeline: part two"), "shape": "roll-up"}
    assert not _counts(_epic("DRE-100", stamped))


def test_one_open_buildable_card_makes_it_count():
    assert _counts(_epic("DRE-100", DONE_PLAIN,
                         _child("DRE-3", "PROOF: observed", labels=("hand-built",)),
                         _child("DRE-4", "bureau-pipeline: the card left to build",
                                state="Todo", labels=("agent:engineer",))))


@pytest.mark.parametrize("state", ["Backlog", "Todo", "In Progress", "In Review",
                                   "Triage", "Green Light", "Hand-work", "Planning"])
def test_every_open_state_holds_the_slot(state):
    assert _counts(_epic("DRE-100", _child("DRE-3", state=state)))


@pytest.mark.parametrize("state", ["Done", "Canceled", "Duplicate"])
def test_closed_children_never_make_it_count(state):
    closed = [_child(f"DRE-{n}", "bureau-pipeline: a card", state=state,
                     labels=("agent:engineer",)) for n in range(3, 6)]
    assert not _counts(_epic("DRE-100", *closed))
    assert not _counts(_epic("DRE-100", *closed), rollups=True)


def test_needs_human_alone_is_still_a_card_somebody_builds():
    """DRE-4626's last card, DRE-5899: `needs-human` without `no-code`."""
    assert _counts(_epic("DRE-100", _child(
        "DRE-3", "portico: a comment-only change", state="Triage",
        labels=("agent:engineer", "needs-human"))))


def test_a_truncated_children_page_counts_rather_than_guess():
    """More children than the page reads: the unread ones might be cards left
    to build, and a slot held wrongly costs a wait while a slot freed wrongly
    breaks the cap."""
    assert _counts(_epic("DRE-100", DONE_PLAIN, truncated=True))


def test_a_truncated_label_page_with_no_mark_read_counts():
    assert _counts(_epic("DRE-100", _child("DRE-3", labels=("agent:ops",),
                                           labels_truncated=True)))


def test_a_truncated_label_page_that_read_the_mark_does_not_count():
    assert not _counts(_epic("DRE-100", _child("DRE-3", labels=("hand-built",),
                                               labels_truncated=True)))


def test_with_count_rollup_parents_an_open_child_epic_takes_the_slot():
    """`count_rollup_parents` keeps its meaning for roll-ups: a parent whose
    open children are epics counts when the file says roll-ups do."""
    parent = _epic("DRE-100", DONE_PLAIN, _child("DRE-3", "[EPIC] part two"))
    assert not _counts(parent, rollups=False)
    assert _counts(parent, rollups=True)


def test_with_count_rollup_parents_a_proof_alone_still_does_not_count():
    parent = _epic("DRE-100", _child("DRE-3", "PROOF: observed"))
    assert not _counts(parent, rollups=True)


def test_with_count_rollup_parents_a_closed_child_epic_does_not_count():
    parent = _epic("DRE-100", _child("DRE-3", "[EPIC] part two", state="Done"))
    assert not _counts(parent, rollups=True)


def test_the_proof_title_is_proof_and_demos_rule(monkeypatch):
    """Imported, never restated: a change to `proof_and_demo.is_proof` moves this."""
    seen = []

    def fake_is_proof(title):
        seen.append(title)
        return True

    monkeypatch.setattr(epic_cap.proof_and_demo, "is_proof", fake_is_proof)
    assert not _counts(_epic("DRE-100", _child("DRE-3", "bureau-pipeline: plain")))
    assert seen == ["bureau-pipeline: plain"]


def test_the_marks_are_the_pipelines_own_strings():
    import reconcile
    assert epic_cap.HAND_BUILT_LABEL == reconcile.HAND_BUILT_LABEL
    assert set(epic_cap.UNBUILT_LABELS) == {reconcile.HAND_BUILT_LABEL,
                                            linear_ops.NO_CODE_LABEL}
    assert epic_cap.CLOSED_STATES == ("Done", "Canceled", "Duplicate")


# --------------------------------------------------------------------------
# the person marks are the vocabulary's (DRE-6226)
# --------------------------------------------------------------------------
def _flipped(monkeypatch) -> None:
    """The vocabulary with OPERATOR's marker flipped in memory — `operator-step`
    + `no-code` — the shape `tests/test_person_marks.py` proves the flip with."""
    import copy

    import routing_verdict
    doc = copy.deepcopy(routing_verdict.load())
    for record in doc["verdicts"]:
        if record["name"] == "OPERATOR":
            record["marks"] = ["operator-step", "no-code"]
        if record["name"] == "WORKBENCH":
            record["marks"] = []
    monkeypatch.setattr(routing_verdict, "load", lambda path=None: doc)


def test_the_ceos_mark_is_the_routing_vocabularys_own():
    import routing_verdict
    assert epic_cap.HAND_BUILT_LABEL is routing_verdict.HAND_BUILT_LABEL


@pytest.mark.parametrize("label", ["operator-step", "Operator-Step", "hand-built", "no-code"])
def test_an_operator_step_child_holds_no_slot_once_the_vocabulary_marks_it(monkeypatch, label):
    _flipped(monkeypatch)
    child = _child("DRE-3", "bureau-pipeline: a deploy", labels=(label,))
    assert not epic_cap.buildable(child)
    assert not _counts(_epic("DRE-100", DONE_PLAIN, child))


def test_the_unbuilt_labels_are_read_off_the_vocabulary_at_read_time(monkeypatch):
    import routing_verdict
    assert epic_cap.UNBUILT_LABELS == routing_verdict.person_marks()
    _flipped(monkeypatch)
    assert set(epic_cap.UNBUILT_LABELS) == {"operator-step", "no-code", "hand-built"}


#: The three readers DRE-6226 points at `routing_verdict.person_marks`.
PERSON_MARK_READERS = ("epic_cap.py", "hygiene_done.py", "groom_verify_agent.py")


def _code_strings(path: Path) -> list:
    """Every string constant in the module that is not a docstring."""
    import ast
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
    return [node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and id(node) not in docstrings]


@pytest.mark.parametrize("module", PERSON_MARK_READERS)
def test_no_reader_spells_the_ceos_mark_itself(module):
    path = ROOT / "scripts" / module
    text = path.read_text(encoding="utf-8")
    quoted = [q for q in ('"hand-built"', "'hand-built'") if q in text]
    assert quoted == [], module
    # Nor inside a longer string, a regex alternative included.
    assert not [s for s in _code_strings(path) if "hand-built" in s]


def test_an_operator_step_child_is_a_build_under_the_shipped_vocabulary():
    # Today the file does not mark OPERATOR `operator-step`, so the label is
    # nobody's mark yet — the reader follows the data, not the string.
    child = _child("DRE-3", "bureau-pipeline: a deploy", labels=("operator-step",))
    assert epic_cap.buildable(child)


# --------------------------------------------------------------------------
# the receipts still read true
# --------------------------------------------------------------------------
def test_the_queued_receipt_says_a_slot_opens_when_building_ends_not_when_the_epic_closes():
    body = epic_cap.queued_receipt(3, 4, 15, 15)
    assert "when one closes" not in body
    assert "15 of 15 epics are in motion" in body
    assert "card left to build" in body
    # The line's order is unchanged.
    assert "Urgent, High, Medium, Low" in body and "oldest approval first" in body


def test_the_started_receipt_still_reads_true():
    body = epic_cap.started_receipt(1, 6, 15)
    assert "a slot opened and this epic was next in line (place 1)" in body
    assert "6 of 15 epics are now in motion" in body
