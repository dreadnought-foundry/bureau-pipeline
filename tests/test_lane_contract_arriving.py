"""The lane contract learns an ARRIVING lane, and declares Hand-work with it
(DRE-5315, epic DRE-5240).

From phase 2 the harness fails three ways on a lane that is in one place and
not the others: `board.every_lane_exists` on a live lane Linear does not carry,
`board.every_state_is_named` on a Linear state the contract does not name, and
the console clause on a live lane the console does not list. A lane cannot
appear in all three at once, so it needs a status for the gap — `arriving`, the
mirror of `retiring`.

An arriving lane is NAMED (the board may grow the state without turning the
harness red), is not required to EXIST yet, is not required of the CONSOLE,
and is invisible to every reader that defaults to `live` — so nothing routes to
`Hand-work` until a later card flips the one word that makes it live. Its
entry is written complete for exactly that reason: the flip must need no
further edit, so `_validate` holds an arriving lane to everything a live one
owes, plus the three keys a retiring one carries, renamed for the direction.
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


def failed_rules(report):
    return {f.clause_id for f in report.failures()}


def live_board(doc, *extra):
    return {name: 0 for name in list(lane_contract.lane_names("live", doc)) + list(extra)}


# --------------------------------------------------------------------------- #
# the entry                                                                   #
# --------------------------------------------------------------------------- #


class TestTheHandWorkEntry:
    def test_it_is_declared_arriving(self):
        assert lane_contract.lane_names("arriving", shipped()) == (HAND_WORK,)
        assert lane_contract.lane(HAND_WORK, status="arriving")["status"] == "arriving"

    def test_no_live_reader_sees_it(self):
        doc = shipped()
        with pytest.raises(lane_contract.UnknownLane):
            lane_contract.lane(HAND_WORK)
        assert HAND_WORK not in lane_contract.lane_names()
        assert HAND_WORK not in lane_contract.flow_lanes()
        assert HAND_WORK not in lane_contract.stale_minutes()
        assert HAND_WORK not in lane_contract.off_flow()
        assert all(c.lane != HAND_WORK for c in lane_contract.clauses(doc))

    def test_it_sits_immediately_after_todo_in_the_work_segment(self):
        names = [entry["name"] for entry in shipped()["lanes"]]
        assert names[names.index("Todo") + 1] == HAND_WORK
        entry = raw_lane(shipped(), HAND_WORK)
        assert entry["segment"] == "work"
        assert "stale_minutes" not in entry

    def test_it_records_the_epic_the_reason_and_the_board_step(self):
        entry = raw_lane(shipped(), HAND_WORK)
        assert entry["arriving_by"] == EPIC
        assert entry["reason"].strip()
        step = entry["board_action"]
        for phrase in ("`Hand-work`", "`unstarted`", "DRE team", "immediately after `Todo`"):
            assert phrase in step, phrase

    def test_its_writers_are_the_sweep_the_write_layer_and_a_person(self):
        who = raw_lane(shipped(), HAND_WORK)["clauses"]["writers"]["who"]
        assert who == ["reconcile.py", "linear_ops.py", "operator"]

    def test_its_entrance_and_evidence_ask_for_the_verdict(self):
        # planning_escalation.bypass_problems asks this of every work lane a
        # verdict can reach; written now so the flip to live passes it.
        clauses = raw_lane(shipped(), HAND_WORK)["clauses"]
        assert "verdict" in clauses["entrance"]["text"]
        assert "verdict" in clauses["evidence"]["text"]

    def test_its_clauses_state_the_rule_once_live(self):
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

    def test_the_flip_to_live_needs_no_further_edit(self):
        doc = copy.deepcopy(shipped())
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
        doc = shipped()
        report = lane_contract.check(
            contract=doc,
            board=live_board(doc),
            console=list(lane_contract.lane_names("live", doc)),
            vocabulary=set(),
        )
        assert report.ok, [f.detail for f in report.failures()]
        assert not any(HAND_WORK in f.detail for f in report.failures())

    def test_present_on_board_and_console_raises_nothing(self):
        doc = shipped()
        report = lane_contract.check(
            contract=doc,
            board=live_board(doc, HAND_WORK),
            console=list(lane_contract.lane_names("live", doc)) + [HAND_WORK],
            vocabulary=set(),
        )
        assert report.ok, [f.detail for f in report.failures()]

    def test_an_unnamed_state_still_fails(self):
        doc = shipped()
        report = lane_contract.check(
            contract=doc,
            board=live_board(doc, HAND_WORK, "Hand Work"),
            console=list(lane_contract.lane_names("live", doc)),
            vocabulary=set(),
        )
        assert "board.every_state_is_named" in failed_rules(report)
        assert any("'Hand Work'" in f.detail for f in report.failures())

    def test_a_live_lane_missing_from_the_board_still_fails(self):
        doc = shipped()
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
        found = lane_contract.pipeline_vocabulary()
        assert HAND_WORK in found
        doc = shipped()
        report = lane_contract.check(
            contract=doc, board=live_board(doc),
            console=list(lane_contract.lane_names("live", doc)), vocabulary=found,
        )
        assert "pipeline.vocabulary_is_contract_lanes" not in failed_rules(report)


# --------------------------------------------------------------------------- #
# validation                                                                  #
# --------------------------------------------------------------------------- #


def _arriving(doc):
    return raw_lane(doc, HAND_WORK)


@pytest.mark.parametrize("key", ["arriving_by", "reason", "board_action", "segment"])
def test_an_arriving_lane_missing_a_key_is_refused_by_name(key):
    doc = copy.deepcopy(shipped())
    _arriving(doc).pop(key)
    with pytest.raises(lane_contract.ContractError) as err:
        lane_contract._validate(doc, "<fixture>")
    assert key in str(err.value)
    assert HAND_WORK in str(err.value)


@pytest.mark.parametrize("kind", lane_contract.CLAUSE_KINDS)
def test_an_arriving_lane_missing_a_clause_is_refused_by_name(kind):
    doc = copy.deepcopy(shipped())
    _arriving(doc)["clauses"].pop(kind)
    with pytest.raises(lane_contract.ContractError) as err:
        lane_contract._validate(doc, "<fixture>")
    assert kind in str(err.value)
    assert HAND_WORK in str(err.value)


def test_an_unknown_status_is_still_refused():
    doc = copy.deepcopy(shipped())
    _arriving(doc)["status"] = "coming-soon"
    with pytest.raises(lane_contract.ContractError) as err:
        lane_contract._validate(doc, "<fixture>")
    assert "arriving" in str(err.value)


# --------------------------------------------------------------------------- #
# the clauses this card rewrites, and the rendered document                   #
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

    def test_the_arriving_section_is_rendered(self):
        text = self.committed()
        assert "## Arriving" in text
        assert f"### {HAND_WORK} — arriving by {EPIC}" in text
        assert raw_lane(shipped(), HAND_WORK)["board_action"] in text
        assert raw_lane(shipped(), HAND_WORK)["reason"] in text

    def test_the_rewritten_clauses_are_rendered(self):
        text = self.committed()
        assert lane_contract.lane("Todo")["clauses"]["entrance"]["text"] in text
        assert lane_contract.lane("Backlog")["clauses"]["exit"]["text"] in text

    def test_the_arriving_lane_is_not_in_the_flow_table(self):
        flow = self.committed().split("## The flow", 1)[1].split("## Off the flow", 1)[0]
        assert HAND_WORK not in flow

    def test_the_section_is_absent_when_nothing_is_arriving(self):
        doc = copy.deepcopy(shipped())
        doc["lanes"] = [e for e in doc["lanes"] if e.get("status") != "arriving"]
        assert "## Arriving" not in lane_contract.render_markdown(doc)
