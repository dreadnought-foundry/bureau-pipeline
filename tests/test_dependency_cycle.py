"""RED-first: the walk that finds a loop neither promotion gate can see alone
(DRE-3539).

2026-09-08 about 5:40 PM PT to 2026-09-10 6:41 AM PT: epic DRE-3421 was
`blockedBy` epic DRE-3420, and DRE-3420 could not finish because its lead
child DRE-3433 was `blockedBy` DRE-3428, a DRE-3421 child. The epic gate held
DRE-3428 behind DRE-3420, the card gate held DRE-3433 behind DRE-3428, both
printed a correct "held by" line on every pass, and ten cards stood for 38
hours until a person found the loop by hand.

`dependency_cycle` is the pure half: the pass's Queued candidates and its epic
records in, the loops out — no Linear access, the split `promotion_stall`
already runs. These tests drive it on the incident's own shape and on the
shapes that must NOT read as a loop.

Run: cd bureau-pipeline && python3 -m pytest tests/test_dependency_cycle.py -v
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import dependency_cycle  # noqa: E402

IN_PROGRESS = {"name": "In Progress"}


def _relations(*blockers: tuple[str, str]) -> dict:
    """An `inverseRelations` page: each `(identifier, state)` blocks the card."""
    return {
        "nodes": [{"type": "blocks",
                   "issue": {"identifier": ident, "state": {"name": state}}}
                  for ident, state in blockers],
        "pageInfo": {"hasNextPage": False},
    }


def _card(identifier: str, parent: str | None, *blockers: tuple[str, str],
          title: str | None = None, labels: tuple = ()) -> dict:
    """A Queued candidate in `backlog_children`' shape."""
    return {
        "identifier": identifier,
        "title": title or f"build piece {identifier}",
        "parent": {"identifier": parent, "state": IN_PROGRESS} if parent else None,
        "labels": {"nodes": [{"name": name} for name in labels]},
        "inverseRelations": _relations(*blockers),
    }


def _epic(identifier: str, *blockers: tuple[str, str]) -> dict:
    """An epic as the pass's epic record carries it."""
    return {
        "identifier": identifier,
        "description": "",
        "state": IN_PROGRESS,
        "inverseRelations": _relations(*blockers),
    }


def _incident(*, lead_title: str | None = None, crossing: bool = True) -> tuple[list, dict]:
    """DRE-3420/3421 on 2026-09-09: DRE-3421 `blockedBy` DRE-3420, DRE-3428
    a DRE-3421 child, DRE-3433 DRE-3420's lead child `blockedBy` DRE-3428."""
    lead = (("DRE-3428", "Queued"),) if crossing else ()
    candidates = [
        _card("DRE-3428", "DRE-3421"),
        _card("DRE-3433", "DRE-3420", *lead, title=lead_title),
    ]
    records = {
        "DRE-3420": _epic("DRE-3420"),
        "DRE-3421": _epic("DRE-3421", ("DRE-3420", "In Progress")),
    }
    return candidates, records


class TestTheIncidentIsALoop:
    def test_the_dre_3420_3421_shape_is_one_cycle(self):
        cycles = dependency_cycle.find_cycles(*_incident())
        assert len(cycles) == 1, cycles

    def test_the_four_identifiers_are_named_in_order_from_the_lowest(self):
        """DRE-3420 waits for its open child DRE-3433, which is `blockedBy`
        DRE-3428, which waits through its epic DRE-3421 for DRE-3420 — the
        epic a card waits through is named between the card and the blocker,
        so the same loop reads the same way every pass."""
        (cycle,) = dependency_cycle.find_cycles(*_incident())
        assert cycle.nodes == ("DRE-3420", "DRE-3433", "DRE-3428", "DRE-3421")

    def test_each_step_carries_its_edge_kind(self):
        (cycle,) = dependency_cycle.find_cycles(*_incident())
        assert [(s.source, s.target, s.kind) for s in cycle.steps] == [
            ("DRE-3420", "DRE-3433", dependency_cycle.CHILD_EDGE),
            ("DRE-3433", "DRE-3428", dependency_cycle.CARD_EDGE),
            ("DRE-3428", "DRE-3420", dependency_cycle.EPIC_EDGE),
        ]
        assert cycle.steps[2].epic == "DRE-3421"

    def test_the_edge_to_remove_is_the_epic_level_one_the_operator_deleted(self):
        (cycle,) = dependency_cycle.find_cycles(*_incident())
        assert cycle.remove == "DRE-3420 blocks DRE-3421"

    def test_the_epics_in_the_loop_are_both_epics(self):
        (cycle,) = dependency_cycle.find_cycles(*_incident())
        assert dependency_cycle.epics(cycle) == ["DRE-3420", "DRE-3421"]

    def test_the_line_names_every_step_and_the_edge(self):
        (cycle,) = dependency_cycle.find_cycles(*_incident())
        line = dependency_cycle.line(cycle)
        assert line.startswith("dependency-cycle: ")
        for said in ("DRE-3420 waits for its open child DRE-3433",
                     "DRE-3433 is blockedBy DRE-3428",
                     "DRE-3428 (child of DRE-3421) waits for DRE-3420 "
                     "(DRE-3421's blocker)",
                     "remove `DRE-3420 blocks DRE-3421`"):
            assert said in line, line
        assert "\n" not in line

    def test_the_input_order_does_not_change_the_answer(self):
        candidates, records = _incident()
        forward = dependency_cycle.find_cycles(candidates, records)
        backward = dependency_cycle.find_cycles(
            list(reversed(candidates)), dict(reversed(list(records.items()))))
        assert forward == backward


class TestWhatIsNotALoop:
    def test_a_serial_epic_dependency_is_not_a_cycle(self):
        """DRE-3421 `blockedBy` DRE-3420 and no card edge back: DRE-3420's
        children build, it finishes, DRE-3421 follows. Ordinary work."""
        assert dependency_cycle.find_cycles(*_incident(crossing=False)) == []

    def test_an_epic_and_its_own_child_are_not_a_two_node_loop(self):
        """There is no child -> own-parent edge: a child waits for its
        parent's BLOCKERS, never for its parent."""
        candidates = [_card("DRE-3433", "DRE-3420")]
        records = {"DRE-3420": _epic("DRE-3420")}
        assert dependency_cycle.find_cycles(candidates, records) == []

    def test_a_blocked_epic_and_its_own_child_are_not_a_loop_either(self):
        candidates = [_card("DRE-3428", "DRE-3421")]
        records = {"DRE-3421": _epic("DRE-3421", ("DRE-3420", "In Progress"))}
        assert dependency_cycle.find_cycles(candidates, records) == []

    def test_a_crossing_edge_onto_a_proof_card_is_not_a_loop(self):
        """A proof never keeps its epic from being built out (DRE-6407), so
        DRE-3420 does not wait for a proof child and the epic gate releases."""
        candidates, records = _incident(
            lead_title="PROOF: the stamp on main was written by the script")
        assert dependency_cycle.find_cycles(candidates, records) == []

    def test_a_crossing_edge_onto_a_card_outside_queued_is_not_a_loop(self):
        """DRE-3428 sits in Planning, so it is not a candidate: the gate is not
        holding it, and a loop through it is not this deadlock."""
        candidates, records = _incident()
        candidates = [c for c in candidates if c["identifier"] != "DRE-3428"]
        assert dependency_cycle.find_cycles(candidates, records) == []

    def test_an_epic_whose_record_was_not_read_carries_no_blockers(self):
        candidates, records = _incident()
        del records["DRE-3421"]
        assert dependency_cycle.find_cycles(candidates, records) == []

    def test_a_terminal_blocker_is_no_edge(self):
        candidates, records = _incident()
        records["DRE-3421"] = _epic("DRE-3421", ("DRE-3420", "Done"))
        assert dependency_cycle.find_cycles(candidates, records) == []

    def test_a_child_marked_for_a_person_is_no_edge(self):
        candidates, records = _incident()
        candidates[1]["labels"]["nodes"].append({"name": "no-code"})
        assert dependency_cycle.find_cycles(candidates, records) == []


class TestACardOnlyLoopIsCaughtToo:
    def test_two_cards_blocked_by_each_other_are_one_cycle_naming_a_card_edge(self):
        candidates = [
            _card("DRE-501", None, ("DRE-502", "Queued")),
            _card("DRE-502", None, ("DRE-501", "Queued")),
        ]
        (cycle,) = dependency_cycle.find_cycles(candidates, {})
        assert cycle.nodes == ("DRE-501", "DRE-502")
        assert [s.kind for s in cycle.steps] == [dependency_cycle.CARD_EDGE] * 2
        # The first card-level edge met from the lowest identifier.
        assert cycle.remove == "DRE-502 blocks DRE-501"
        assert dependency_cycle.epics(cycle) == []

    def test_the_lowest_identifier_is_read_by_number_not_by_text(self):
        candidates = [
            _card("DRE-99", None, ("DRE-100", "Queued")),
            _card("DRE-100", None, ("DRE-99", "Queued")),
        ]
        (cycle,) = dependency_cycle.find_cycles(candidates, {})
        assert cycle.nodes == ("DRE-99", "DRE-100")

    def test_two_separate_loops_are_two_cycles_lowest_first(self):
        candidates = [
            _card("DRE-701", None, ("DRE-702", "Queued")),
            _card("DRE-702", None, ("DRE-701", "Queued")),
            _card("DRE-601", None, ("DRE-602", "Queued")),
            _card("DRE-602", None, ("DRE-601", "Queued")),
        ]
        cycles = dependency_cycle.find_cycles(candidates, {})
        assert [c.nodes for c in cycles] == [("DRE-601", "DRE-602"),
                                            ("DRE-701", "DRE-702")]


class TestTheNoticeOnTheEpic:
    def test_the_pacific_date_is_the_calendar_day_in_pt(self):
        # 2026-09-10 06:30 UTC is still the evening of 2026-09-09 in PT.
        assert dependency_cycle.pacific_date("2026-09-10T06:30:00+00:00") == "2026-09-09"
        assert dependency_cycle.pacific_date("2026-09-10T08:00:00+00:00") == "2026-09-10"

    def test_the_notice_opens_with_its_dated_tag(self):
        (cycle,) = dependency_cycle.find_cycles(*_incident())
        tag = dependency_cycle.notice_tag("2026-09-09")
        assert tag == "dependency-cycle: 2026-09-09"
        body = dependency_cycle.notice(cycle, "2026-09-09")
        assert body.startswith(tag)
        assert "DRE-3420 blocks DRE-3421" in body
        assert "never" in body  # the sweep edits no relation, and says so


class TestTheWalkIsPure:
    def test_the_module_imports_neither_linear_ops_nor_reconcile(self):
        """No Linear access in this module, by construction: the walk is
        handed what the pass already read."""
        tree = ast.parse((ROOT / "scripts" / "dependency_cycle.py").read_text())
        imported = {alias.name.split(".")[0]
                    for node in ast.walk(tree) if isinstance(node, ast.Import)
                    for alias in node.names}
        imported |= {node.module.split(".")[0]
                     for node in ast.walk(tree)
                     if isinstance(node, ast.ImportFrom) and node.module}
        assert not imported & {"linear_ops", "reconcile"}, imported
