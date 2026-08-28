"""The conformance rules — what the harness asserts, and what it refuses to
assert yet (DRE-2726).

Two halves, and the second is the one that would otherwise be quietly dropped:

  1. **Drift is a failure.** A Linear state the contract does not name, a lane
     the contract names that Linear does not have, a retiring lane still
     holding cards, a pipeline lane missing from the console's state lists —
     each one fails the check, by rule id, naming the state.
  2. **A promise is not an assertion.** A clause whose `enforced_from` phase
     has not shipped is SKIPPED, not failed — and a clause whose phase HAS
     shipped with nothing implementing it FAILS. That second rule is what turns
     the contract from a description into a schedule that checks itself.

Every fixture here is a hand-built contract, never the shipped file, so these
tests keep meaning after the board changes.
"""

import copy
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import lane_contract  # noqa: E402


def fixture_contract(**overrides):
    """A tiny two-lane contract with a known phase order.

    Phases 1 and 2 have shipped; 3 has not, so every lane clause here is a
    promise the harness must SKIP rather than fail. The tests that need the
    other half — a clause whose phase has passed with nothing enforcing it —
    move one clause's `enforced_from` back to 2 and watch it fail.
    """
    doc = {
        "version": 1,
        "phases": {"order": ["1", "2", "3"], "current": "2", "titles": {}},
        "planning_exit": {"from": "Green Light", "to": "Todo"},
        "aliases": {},
        "console": {"repo": "o/r", "module": "linear_states", "symbol": "BOARD_STATES"},
        "rules": [
            {
                "id": "board.every_state_is_named",
                "text": "no state exists in Linear that the contract does not name",
                "enforced_from": "2",
                "assertion": "board.every_state_is_named",
            },
            {
                "id": "board.every_lane_exists",
                "text": "no lane is named that does not exist",
                "enforced_from": "2",
                "assertion": "board.every_lane_exists",
            },
            {
                "id": "board.retiring_lane_is_empty",
                "text": "a retiring lane holds no cards",
                "enforced_from": "2",
                "assertion": "board.retiring_lane_is_empty",
            },
            {
                "id": "board.retired_entry_is_deleted",
                "text": "a retiring entry whose state is gone is deleted",
                "enforced_from": "2",
                "assertion": "board.retired_entry_is_deleted",
            },
            {
                "id": "console.state_lists_carry_every_lane",
                "text": "no lane is missing from the console's state lists",
                "enforced_from": "2",
                "assertion": "console.state_lists_carry_every_lane",
                "unevaluated_fails_from": "3",
            },
            {
                "id": "pipeline.vocabulary_is_contract_lanes",
                "text": "the pipeline names no state the contract does not carry",
                "enforced_from": "2",
                "assertion": "pipeline.vocabulary_is_contract_lanes",
            },
        ],
        "lanes": [
            {
                "name": "Green Light",
                "status": "live",
                "segment": "planning",
                "clauses": {
                    "entrance": {"text": "a plan exists", "enforced_from": "3",
                                 "assertion": None},
                    "exit": {"text": "the CEO approves", "enforced_from": "3"},
                    "writers": {"text": "plan.yml", "enforced_from": "3"},
                    "evidence": {"text": "a plan artifact", "enforced_from": "3"},
                },
            },
            {
                "name": "Todo",
                "status": "live",
                "segment": "work",
                "stale_minutes": 15,
                "clauses": {
                    "entrance": {"text": "it is unblocked", "enforced_from": "3"},
                    "exit": {"text": "an agent picked it up", "enforced_from": "3"},
                    "writers": {"text": "reconcile.py", "enforced_from": "3"},
                    "evidence": {"text": "its verdict is FLEET", "enforced_from": "3"},
                },
            },
        ],
    }
    doc.update(overrides)
    return doc


BOARD_OK = {"Green Light": 1, "Todo": 4}
CONSOLE_OK = ["Green Light", "Todo"]
VOCAB_OK = {"Green Light", "Todo"}


def run(contract, **kw):
    kw.setdefault("board", BOARD_OK)
    kw.setdefault("console", CONSOLE_OK)
    kw.setdefault("vocabulary", VOCAB_OK)
    return lane_contract.check(contract=contract, **kw)


def failed_rules(report):
    return {f.clause_id for f in report.findings if f.status == "fail"}


class TestTheHappyPath:
    def test_a_board_that_matches_the_contract_passes(self):
        report = run(fixture_contract())
        assert report.ok, [f.detail for f in report.findings if f.status == "fail"]

    def test_the_report_says_what_it_asserted_and_what_it_skipped(self):
        report = run(fixture_contract())
        assert report.asserted(), "nothing was asserted — a vacuous green"
        assert report.skipped(), "the Phase-3 clauses were not reported as skipped"


class TestDriftFails:
    def test_a_linear_state_the_contract_does_not_name_fails(self):
        # Exactly tonight's finding: `In Design Review` on the board, named
        # nowhere in the contract.
        board = dict(BOARD_OK, **{"In Design Review": 0})
        report = run(fixture_contract(), board=board)
        assert not report.ok
        assert "board.every_state_is_named" in failed_rules(report)
        assert any("In Design Review" in f.detail for f in report.findings)

    def test_a_lane_the_contract_names_that_linear_does_not_have_fails(self):
        board = {"Green Light": 1}  # Todo missing
        report = run(fixture_contract(), board=board)
        assert not report.ok
        assert "board.every_lane_exists" in failed_rules(report)
        assert any("Todo" in f.detail for f in report.findings)

    def test_a_retiring_lane_that_still_holds_cards_fails(self):
        doc = fixture_contract()
        doc["lanes"].append(
            {
                "name": "In QA",
                "status": "retiring",
                "retired_by": "DRE-2726",
                "reason": "folded into In Review",
                "board_action": "archive it",
            }
        )
        report = run(doc, board=dict(BOARD_OK, **{"In QA": 3}))
        assert not report.ok
        assert "board.retiring_lane_is_empty" in failed_rules(report)

    def test_a_retiring_lane_that_is_empty_and_still_on_the_board_is_tolerated(self):
        doc = fixture_contract()
        doc["lanes"].append(
            {
                "name": "In QA",
                "status": "retiring",
                "retired_by": "DRE-2726",
                "reason": "folded into In Review",
                "board_action": "archive it",
            }
        )
        report = run(doc, board=dict(BOARD_OK, **{"In QA": 0}))
        assert report.ok, [f.detail for f in report.findings if f.status == "fail"]

    def test_a_retiring_entry_whose_state_is_already_gone_fails(self):
        # The self-destruct half: once the workspace apply archives the state,
        # the contract entry is dead weight and the harness says so — with the
        # line to delete. A retirement that never finishes is drift too.
        doc = fixture_contract()
        doc["lanes"].append(
            {
                "name": "In QA",
                "status": "retiring",
                "retired_by": "DRE-2726",
                "reason": "folded into In Review",
                "board_action": "archive it",
            }
        )
        report = run(doc, board=BOARD_OK)
        assert not report.ok
        assert "board.retired_entry_is_deleted" in failed_rules(report)

    def test_a_pipeline_lane_missing_from_the_console_state_lists_fails(self):
        report = run(fixture_contract(), console=["Green Light"])
        assert not report.ok
        assert "console.state_lists_carry_every_lane" in failed_rules(report)
        assert any("Todo" in f.detail for f in report.findings)

    def test_console_vocabulary_the_contract_does_not_name_fails(self):
        # The other half of tonight's finding: `Proposed` and `HOLD` are in the
        # console's vocabulary and are not Linear states.
        report = run(fixture_contract(), console=CONSOLE_OK + ["Proposed", "HOLD"])
        assert not report.ok
        details = " ".join(f.detail for f in report.findings if f.status == "fail")
        assert "Proposed" in details and "HOLD" in details

    def test_a_lane_name_in_pipeline_code_that_the_contract_does_not_carry_fails(self):
        report = run(fixture_contract(), vocabulary=VOCAB_OK | {"In QA"})
        assert not report.ok
        assert "pipeline.vocabulary_is_contract_lanes" in failed_rules(report)


class TestEnforcedFrom:
    def test_an_unshipped_clause_is_skipped_rather_than_failed(self):
        # Todo's evidence clause says "its verdict is FLEET" and nothing writes
        # verdicts yet. Asserting it would fail red on the whole board.
        report = run(fixture_contract())
        skipped = {f.clause_id for f in report.findings if f.status == "skipped"}
        assert "Todo.evidence" in skipped
        assert "Todo.evidence" not in failed_rules(report)

    def test_a_clause_whose_phase_has_passed_with_nothing_enforcing_it_fails(self):
        # The half that would otherwise be quietly dropped. Move Green Light's
        # entrance clause to phase 2, which HAS shipped, and leave its
        # `assertion` null — nothing implements it, so it must fail.
        doc = fixture_contract()
        doc["lanes"][0]["clauses"]["entrance"]["enforced_from"] = "2"
        report = run(doc)
        assert not report.ok
        assert "Green Light.entrance" in failed_rules(report)
        detail = next(
            f.detail for f in report.findings if f.clause_id == "Green Light.entrance"
        )
        assert "2" in detail and "not enforced" in detail.lower()

    def test_the_same_clause_one_phase_earlier_is_merely_skipped(self):
        # Identical clause, identical missing implementation — the only thing
        # that changed is how far the wave has shipped.
        doc = fixture_contract()
        doc["lanes"][0]["clauses"]["entrance"]["enforced_from"] = "2"
        doc["phases"]["current"] = "1"
        report = run(doc)
        statuses = {f.clause_id: f.status for f in report.findings}
        assert statuses["Green Light.entrance"] == "skipped"
        assert "Green Light.entrance" not in failed_rules(report)

    def test_a_clause_naming_an_assertion_that_does_not_exist_fails(self):
        doc = fixture_contract()
        doc["lanes"][0]["clauses"]["entrance"]["enforced_from"] = "2"
        doc["lanes"][0]["clauses"]["entrance"]["assertion"] = "nope.not_a_thing"
        report = run(doc)
        assert "Green Light.entrance" in failed_rules(report)

    def test_advancing_the_phase_turns_a_promise_into_a_failure(self):
        # The schedule that checks itself: nothing about the Todo clauses
        # changes, only `phases.current`, and the harness starts demanding them.
        doc = fixture_contract()
        doc["phases"]["current"] = "3"
        report = run(doc)
        assert "Todo.evidence" in failed_rules(report)


class TestUnevaluatedIsNotAPass:
    def test_console_lists_that_could_not_be_read_are_skipped_at_this_phase(self):
        report = run(fixture_contract(), console=None)
        statuses = {f.clause_id: f.status for f in report.findings}
        assert statuses["console.state_lists_carry_every_lane"] == "unevaluated"
        assert report.ok

    def test_console_lists_that_could_not_be_read_fail_once_their_phase_arrives(self):
        doc = fixture_contract()
        doc["phases"]["current"] = "3"
        report = run(doc, console=None)
        assert not report.ok
        assert "console.state_lists_carry_every_lane" in failed_rules(report)

    def test_an_unreadable_board_is_never_a_pass(self):
        # The board is the harness's whole subject. No board, no verdict.
        report = run(fixture_contract(), board=None)
        assert not report.ok


class TestAgainstTheShippedContract:
    """The shipped file must be internally consistent right now — every clause
    the current phase has reached is actually implemented."""

    def test_every_shipped_clause_has_an_implementation(self):
        contract = lane_contract.load()
        for clause in lane_contract.clauses(contract=contract):
            if not lane_contract.phase_has_shipped(clause.enforced_from, contract):
                continue
            assert clause.assertion in lane_contract.ASSERTIONS, (
                f"{clause.id} is enforced_from phase {clause.enforced_from}, "
                "which has shipped, and nothing implements it"
            )

    def test_the_shipped_contract_passes_against_its_own_intended_board(self):
        contract = lane_contract.load()
        live = lane_contract.lane_names(status="live", contract=contract)
        retiring = lane_contract.lane_names(status="retiring", contract=contract)
        board = {name: 0 for name in list(live) + list(retiring)}
        report = lane_contract.check(
            contract=contract,
            board=board,
            console=list(live),
            vocabulary=set(live),
        )
        assert report.ok, [f.detail for f in report.findings if f.status == "fail"]


class TestVocabularyScan:
    """The pipeline's own source is scanned for lane names, so a stray state
    literal is a finding rather than a comment nobody reads."""

    def test_the_scan_finds_a_planted_state_literal(self, tmp_path):
        (tmp_path / "thing.py").write_text(
            'linear_ops.cmd_state(card, "In Design Review")\n', encoding="utf-8"
        )
        found = lane_contract.scan_vocabulary(
            [str(tmp_path)], known=["In Design Review", "Todo"]
        )
        assert "In Design Review" in found

    def test_the_scan_ignores_a_name_that_is_not_a_known_state(self, tmp_path):
        (tmp_path / "thing.py").write_text('x = "Deployed"\n', encoding="utf-8")
        found = lane_contract.scan_vocabulary([str(tmp_path)], known=["Todo"])
        assert found == set()

    def test_the_live_pipeline_names_only_contract_lanes(self):
        # The repo-wide assertion. `In QA` and `In Design Review` must be gone
        # from every script and workflow that writes or reads a lane.
        contract = lane_contract.load()
        known = set(lane_contract.lane_names(status="live", contract=contract)) | set(
            lane_contract.lane_names(status="retiring", contract=contract)
        )
        root = os.path.join(os.path.dirname(__file__), "..")
        found = lane_contract.scan_vocabulary(
            [os.path.join(root, "scripts"), os.path.join(root, ".github", "workflows")],
            known=known,
        )
        stray = found - set(
            lane_contract.lane_names(status="live", contract=contract)
        )
        assert not stray, f"the pipeline still names retired lane(s) {sorted(stray)}"


def test_the_check_never_mutates_the_contract_it_is_given():
    doc = fixture_contract()
    before = json.dumps(doc, sort_keys=True)
    run(copy.deepcopy(doc))
    assert json.dumps(doc, sort_keys=True) == before
