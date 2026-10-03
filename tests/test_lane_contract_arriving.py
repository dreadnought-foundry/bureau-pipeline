"""The lane contract learns an ARRIVING lane, and declares Hand-work with it
(DRE-5315, epic DRE-5240) — then the operator flips Hand-work live (DRE-5320).

From phase 2 the harness fails three ways on a lane that is in one place and
not the others: `board.every_lane_exists` on a live lane Linear does not carry,
`board.every_state_is_named` on a Linear state the contract does not name, and
the console clause on a live lane the console does not list. A lane cannot
appear in all three at once, so it needs a status for the gap — `arriving`, the
mirror of `retiring`.

An arriving lane is NAMED (the board may grow the state without turning the
harness red), is not required to EXIST yet, is not required of the CONSOLE,
and is invisible to every reader that defaults to `live`. Its entry is written
complete, so the flip needs no further edit, and `_validate` holds an arriving
lane to everything a live one owes, plus the three keys a retiring one carries,
renamed for the direction.

DRE-5320 used that mechanism exactly once: the operator created the `Hand-work`
state on the board, then flipped the entry to live, deleting the three arriving
keys and changing nothing else. The shipped contract therefore no longer
carries an arriving lane, so the tests of the MECHANISM below run against
`arriving_doc()`, the shipped contract with Hand-work put back the way DRE-5315
declared it. The tests of the SHIPPED entry say it is live.
"""

import copy
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import lane_contract  # noqa: E402

HAND_WORK = "Hand-work"
EPIC = "DRE-5240"
ROOT = os.path.join(os.path.dirname(__file__), "..")

# The three keys DRE-5315's arriving entry carried, verbatim. DRE-5320's flip
# deleted them from the shipped file; they live on here so the arriving
# mechanism is still proven against a real entry rather than an invented one.
ARRIVING_KEYS = {
    "arriving_by": EPIC,
    "reason": (
        "The lane where a person's work waits. A WORKBENCH or OPERATOR card is "
        "built by a person, not a dispatched run, and today it sits in Todo beside "
        "the fleet's work, told apart only by its `hand-built` mark. Hand-work "
        "gives it a lane of its own, so Todo holds only what the fleet builds. "
        "Declared here before Linear has the state, so the board can grow it "
        "without turning the harness red; nothing routes to it until a later card "
        "flips its status to live."
    ),
    "board_action": (
        "create workflow state `Hand-work`, type `unstarted`, on the DRE team, "
        "positioned immediately after `Todo`; then a later card of DRE-5240 flips "
        "this entry's status to live"
    ),
}

# The sentence DRE-5275 wrote into Backlog's exit. This card rewrites only the
# verdict-destination sentences around it, so it must survive verbatim.
DRE_5275_SENTENCE = (
    "It promotes a FLEET card to Todo once the dependency gate clears, the WIP "
    "cap has room and — for a child of an epic — both critics passed the plan "
    "on its current planning attempt before the green light (DRE-3059, DRE-5268)."
)


def shipped():
    return lane_contract.load()


def raw_lane(doc, name):
    return next(entry for entry in doc["lanes"] if entry["name"] == name)


def arriving_doc():
    """The shipped contract with Hand-work as DRE-5315 declared it: arriving,
    with its three arriving keys, every clause unchanged."""
    doc = copy.deepcopy(shipped())
    entry = raw_lane(doc, HAND_WORK)
    rebuilt = {"name": HAND_WORK, "status": "arriving", **ARRIVING_KEYS}
    rebuilt.update({k: v for k, v in entry.items() if k not in ("name", "status")})
    doc["lanes"][doc["lanes"].index(entry)] = rebuilt
    return doc


def failed_rules(report):
    return {f.clause_id for f in report.failures()}


def live_board(doc, *extra):
    return {name: 0 for name in list(lane_contract.lane_names("live", doc)) + list(extra)}


# --------------------------------------------------------------------------- #
# the shipped entry: live since DRE-5320                                      #
# --------------------------------------------------------------------------- #


class TestTheHandWorkEntry:
    def test_it_is_live_and_nothing_is_arriving(self):
        assert lane_contract.lane_names("arriving", shipped()) == ()
        assert HAND_WORK in lane_contract.lane_names()
        assert lane_contract.lane(HAND_WORK)["status"] == "live"

    def test_the_three_arriving_keys_are_gone(self):
        entry = raw_lane(shipped(), HAND_WORK)
        for key in ARRIVING_KEYS:
            assert key not in entry, key

    def test_every_live_reader_sees_it_and_it_has_no_stall_window(self):
        doc = shipped()
        flow = lane_contract.flow_lanes()
        assert flow[flow.index("Todo") + 1] == HAND_WORK
        assert HAND_WORK not in lane_contract.stale_minutes()
        assert HAND_WORK not in lane_contract.off_flow()
        assert {c.lane for c in lane_contract.clauses(doc)} >= {HAND_WORK}

    def test_it_sits_immediately_after_todo_in_the_work_segment(self):
        names = [entry["name"] for entry in shipped()["lanes"]]
        assert names[names.index("Todo") + 1] == HAND_WORK
        entry = raw_lane(shipped(), HAND_WORK)
        assert entry["segment"] == "work"
        assert "stale_minutes" not in entry

    def test_the_flip_changed_only_the_status(self):
        # Every key the live entry carries is one the arriving entry carried,
        # and the arriving keys are the only ones it dropped.
        live = raw_lane(shipped(), HAND_WORK)
        arriving = raw_lane(arriving_doc(), HAND_WORK)
        assert set(arriving) - set(live) == set(ARRIVING_KEYS)
        assert {k: v for k, v in arriving.items()
                if k not in ARRIVING_KEYS and k != "status"} == {
            k: v for k, v in live.items() if k != "status"}

    def test_its_writers_are_the_sweep_the_write_layer_the_migration_and_a_person(self):
        # DRE-5323 added the one-time migration that moved the person cards
        # already in Todo when the sweep started carrying them here.
        who = raw_lane(shipped(), HAND_WORK)["clauses"]["writers"]["who"]
        assert who == ["reconcile.py", "linear_ops.py", "hand_work_migration.py", "operator"]

    def test_its_entrance_and_evidence_ask_for_the_verdict(self):
        # planning_escalation.bypass_problems asks this of every work lane a
        # verdict can reach.
        clauses = raw_lane(shipped(), HAND_WORK)["clauses"]
        assert "verdict" in clauses["entrance"]["text"]
        assert "verdict" in clauses["evidence"]["text"]

    def test_its_clauses_state_the_rule(self):
        clauses = raw_lane(shipped(), HAND_WORK)["clauses"]
        entrance = clauses["entrance"]["text"]
        for phrase in ("WORKBENCH", "OPERATOR", "config/routing-verdicts.json",
                       "`hand-built`", "`no-code`", "before the move",
                       "WIP cap", "stall window", "never reported as stalled",
                       "An epic never enters"):
            assert phrase in entrance, phrase
        exit_text = clauses["exit"]["text"]
        for phrase in ("pull request", "In Review", "OPERATOR", "Done"):
            assert phrase in exit_text, phrase

    def test_the_board_must_now_carry_it(self):
        doc = shipped()
        board = live_board(doc)
        board.pop(HAND_WORK)
        report = lane_contract.check(
            contract=doc, board=board,
            console=list(lane_contract.lane_names("live", doc)), vocabulary=set(),
        )
        assert "board.every_lane_exists" in failed_rules(report)
        assert any(HAND_WORK in f.detail for f in report.failures())

    def test_the_board_and_console_carrying_it_is_clean(self):
        doc = shipped()
        report = lane_contract.check(
            contract=doc, board=live_board(doc),
            console=list(lane_contract.lane_names("live", doc)), vocabulary=set(),
        )
        assert report.ok, [f.detail for f in report.failures()]


# --------------------------------------------------------------------------- #
# the arriving mechanism, on Hand-work as DRE-5315 declared it                #
# --------------------------------------------------------------------------- #


class TestTheArrivingEntry:
    def test_the_fixture_is_the_arriving_declaration(self):
        doc = arriving_doc()
        lane_contract._validate(doc, "<arriving>")
        assert lane_contract.lane_names("arriving", doc) == (HAND_WORK,)
        step = raw_lane(doc, HAND_WORK)["board_action"]
        for phrase in ("`Hand-work`", "`unstarted`", "DRE team", "immediately after `Todo`"):
            assert phrase in step, phrase

    def test_no_live_reader_sees_an_arriving_lane(self):
        doc = arriving_doc()
        assert HAND_WORK not in lane_contract.lane_names("live", doc)
        assert HAND_WORK not in lane_contract.flow_lanes(doc)
        assert HAND_WORK not in lane_contract.stale_minutes(doc)
        assert HAND_WORK not in lane_contract.off_flow(doc)
        assert all(c.lane != HAND_WORK for c in lane_contract.clauses(doc))

    def test_the_flip_to_live_needs_no_further_edit(self):
        doc = arriving_doc()
        raw_lane(doc, HAND_WORK)["status"] = "live"
        lane_contract._validate(doc, "<flipped>")
        report = lane_contract.check(
            contract=doc,
            board=live_board(doc),
            console=list(lane_contract.lane_names("live", doc)),
            vocabulary=set(),
        )
        assert report.ok, [f.detail for f in report.failures()]
        assert HAND_WORK in lane_contract.flow_lanes(doc)


# --------------------------------------------------------------------------- #
# the conformance rules                                                       #
# --------------------------------------------------------------------------- #


class TestTheHarnessToleratesTheGap:
    def test_absent_from_board_and_console_raises_nothing(self):
        doc = arriving_doc()
        report = lane_contract.check(
            contract=doc,
            board=live_board(doc),
            console=list(lane_contract.lane_names("live", doc)),
            vocabulary=set(),
        )
        assert report.ok, [f.detail for f in report.failures()]
        assert not any(HAND_WORK in f.detail for f in report.failures())

    def test_present_on_board_and_console_raises_nothing(self):
        doc = arriving_doc()
        report = lane_contract.check(
            contract=doc,
            board=live_board(doc, HAND_WORK),
            console=list(lane_contract.lane_names("live", doc)) + [HAND_WORK],
            vocabulary=set(),
        )
        assert report.ok, [f.detail for f in report.failures()]

    def test_an_unnamed_state_still_fails(self):
        doc = arriving_doc()
        report = lane_contract.check(
            contract=doc,
            board=live_board(doc, HAND_WORK, "Hand Work"),
            console=list(lane_contract.lane_names("live", doc)),
            vocabulary=set(),
        )
        assert "board.every_state_is_named" in failed_rules(report)
        assert any("'Hand Work'" in f.detail for f in report.failures())

    def test_a_live_lane_missing_from_the_board_still_fails(self):
        doc = arriving_doc()
        board = live_board(doc, HAND_WORK)
        board.pop("Todo")
        report = lane_contract.check(
            contract=doc, board=board,
            console=list(lane_contract.lane_names("live", doc)), vocabulary=set(),
        )
        assert "board.every_lane_exists" in failed_rules(report)

    def test_the_console_may_name_a_retiring_lane_too(self):
        doc = copy.deepcopy(shipped())
        doc["lanes"].append({
            "name": "Old Lane", "status": "retiring", "retired_by": "DRE-9999",
            "reason": "folded", "board_action": "archive it",
        })
        report = lane_contract.check(
            contract=doc,
            board=live_board(doc, "Old Lane"),
            console=list(lane_contract.lane_names("live", doc)) + ["Old Lane"],
            vocabulary=set(),
        )
        assert "console.state_lists_carry_every_lane" not in failed_rules(report)

    def test_the_console_still_may_not_name_a_word_no_lane_carries(self):
        doc = shipped()
        report = lane_contract.check(
            contract=doc,
            board=live_board(doc),
            console=list(lane_contract.lane_names("live", doc)) + ["HOLD"],
            vocabulary=set(),
        )
        assert "console.state_lists_carry_every_lane" in failed_rules(report)


class TestTheVocabulary:
    def test_a_script_may_name_the_arriving_lane(self, tmp_path, monkeypatch):
        (tmp_path / "later.py").write_text(
            'linear_ops.cmd_state(card, "Hand-work")\n', encoding="utf-8"
        )
        monkeypatch.setattr(lane_contract, "VOCABULARY_PATHS", (str(tmp_path),))
        found = lane_contract.pipeline_vocabulary(arriving_doc())
        assert HAND_WORK in found
        doc = arriving_doc()
        report = lane_contract.check(
            contract=doc, board=live_board(doc),
            console=list(lane_contract.lane_names("live", doc)), vocabulary=found,
        )
        assert "pipeline.vocabulary_is_contract_lanes" not in failed_rules(report)


# --------------------------------------------------------------------------- #
# validation                                                                  #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("key", ["arriving_by", "reason", "board_action", "segment"])
def test_an_arriving_lane_missing_a_key_is_refused_by_name(key):
    doc = arriving_doc()
    raw_lane(doc, HAND_WORK).pop(key)
    with pytest.raises(lane_contract.ContractError) as err:
        lane_contract._validate(doc, "<fixture>")
    assert key in str(err.value)
    assert HAND_WORK in str(err.value)


@pytest.mark.parametrize("kind", lane_contract.CLAUSE_KINDS)
def test_an_arriving_lane_missing_a_clause_is_refused_by_name(kind):
    doc = arriving_doc()
    raw_lane(doc, HAND_WORK)["clauses"].pop(kind)
    with pytest.raises(lane_contract.ContractError) as err:
        lane_contract._validate(doc, "<fixture>")
    assert kind in str(err.value)
    assert HAND_WORK in str(err.value)


def test_an_unknown_status_is_still_refused():
    doc = copy.deepcopy(shipped())
    raw_lane(doc, HAND_WORK)["status"] = "coming-soon"
    with pytest.raises(lane_contract.ContractError) as err:
        lane_contract._validate(doc, "<fixture>")
    assert "arriving" in str(err.value)


# --------------------------------------------------------------------------- #
# the clauses DRE-5315 rewrote, and the rendered document                     #
# --------------------------------------------------------------------------- #


class TestTheRewrittenClauses:
    def test_todo_entrance_defers_to_the_vocabulary(self):
        clause = lane_contract.lane("Todo")["clauses"]["entrance"]
        text = clause["text"]
        assert "config/routing-verdicts.json" in text
        assert "never this sentence" in text
        assert EPIC in text and HAND_WORK in text
        assert EPIC in clause["pending"]

    def test_backlog_exit_defers_to_the_vocabulary_and_keeps_dre_5275(self):
        clause = lane_contract.lane("Backlog")["clauses"]["exit"]
        text = clause["text"]
        assert DRE_5275_SENTENCE in text
        assert "config/routing-verdicts.json" in text
        assert "never this sentence" in text
        assert EPIC in text and HAND_WORK in text
        assert EPIC in clause["pending"]


class TestTheDocument:
    def committed(self):
        with open(lane_contract.DOC_PATH, encoding="utf-8") as fh:
            return fh.read()

    def test_the_committed_document_draws_hand_work_in_the_flow(self):
        text = self.committed()
        flow = text.split("## The flow", 1)[1].split("## Off the flow", 1)[0]
        assert f"| {HAND_WORK} | work |" in flow
        assert f"### {HAND_WORK}\n" in text
        assert "## Arriving" not in text

    def test_the_rewritten_clauses_are_rendered(self):
        text = self.committed()
        assert lane_contract.lane("Todo")["clauses"]["entrance"]["text"] in text
        assert lane_contract.lane("Backlog")["clauses"]["exit"]["text"] in text

    def test_an_arriving_lane_renders_its_own_section_and_stays_out_of_the_flow(self):
        text = lane_contract.render_markdown(arriving_doc())
        assert "## Arriving" in text
        assert f"### {HAND_WORK} — arriving by {EPIC}" in text
        assert ARRIVING_KEYS["board_action"] in text
        assert ARRIVING_KEYS["reason"] in text
        flow = text.split("## The flow", 1)[1].split("## Off the flow", 1)[0]
        assert HAND_WORK not in flow

    def test_the_section_is_absent_when_nothing_is_arriving(self):
        assert "## Arriving" not in lane_contract.render_markdown(shipped())
