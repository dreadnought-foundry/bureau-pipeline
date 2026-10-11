"""The lane contract declares `Queued`, the queue lane that replaces Backlog, as
an ARRIVING lane (DRE-6710, epic DRE-4125) — the way DRE-5315 declared Hand-work.

Linear draws a `backlog`-type column first whatever its position, and cannot
change a state's type once it exists, so the queue moves to a new state of type
`unstarted`. The contract learns its name before the board has it: arriving, so
the harness stays green while the state does not exist, and invisible to every
reader that defaults to `live`.

The entry's clauses are Backlog's word for word, apart from the places where the
text names the lane itself, so the later flip (DRE-6740) changes one word and
deletes the three arriving keys. Nothing else moves in this card: Backlog stays
live, the planning exit still lands in Backlog, and no alias is declared yet
(DRE-6741 does both).
"""

import copy
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import lane_contract  # noqa: E402

QUEUED = "Queued"
BACKLOG = "Backlog"
EPIC = "DRE-4125"
ARRIVING_KEYS = ("arriving_by", "reason", "board_action")
ROOT = os.path.join(os.path.dirname(__file__), "..")


def shipped():
    return lane_contract.load()


def raw_lane(doc, name):
    return next(entry for entry in doc["lanes"] if entry["name"] == name)


def renamed(value):
    """Backlog's text with the lane's own name swapped for Queued's — the only
    change the card permits between the two entries."""
    return json.loads(json.dumps(value).replace(BACKLOG, QUEUED))


def flipped_doc():
    """The shipped contract with Queued flipped live the way DRE-5320 flipped
    Hand-work: the status changed and the three arriving keys deleted."""
    doc = copy.deepcopy(shipped())
    entry = raw_lane(doc, QUEUED)
    entry["status"] = "live"
    for key in ARRIVING_KEYS:
        del entry[key]
    return doc


def failed_rules(report):
    return {f.clause_id for f in report.failures()}


# --------------------------------------------------------------------------- #
# the entry                                                                   #
# --------------------------------------------------------------------------- #


class TestTheQueuedEntry:
    def test_it_is_arriving_in_the_work_segment(self):
        entry = raw_lane(shipped(), QUEUED)
        assert entry["status"] == "arriving"
        assert entry["segment"] == "work"
        assert entry["arriving_by"] == EPIC

    def test_it_carries_exactly_backlogs_keys_plus_the_three_arriving_ones(self):
        entry = raw_lane(shipped(), QUEUED)
        backlog = raw_lane(shipped(), BACKLOG)
        assert set(entry) == set(backlog) | set(ARRIVING_KEYS)

    def test_its_clauses_are_backlogs_with_only_the_lanes_own_name_changed(self):
        # Field for field, every clause: text, enforced_from, assertion,
        # pending and the writers list. A later edit to Backlog's clauses that
        # is not copied here fails this test, which is what keeps the flip a
        # one-word change.
        queued = raw_lane(shipped(), QUEUED)["clauses"]
        backlog = raw_lane(shipped(), BACKLOG)["clauses"]
        assert set(queued) == set(lane_contract.CLAUSE_KINDS)
        for kind in lane_contract.CLAUSE_KINDS:
            assert queued[kind] == renamed(backlog[kind]), kind

    def test_the_one_place_backlog_names_itself_now_names_queued(self):
        # The rename is not vacuous: Backlog's evidence clause names the lane,
        # and Queued's names Queued there instead.
        backlog = raw_lane(shipped(), BACKLOG)["clauses"]["evidence"]["pending"]
        queued = raw_lane(shipped(), QUEUED)["clauses"]["evidence"]["pending"]
        assert "every Backlog card" in backlog
        assert "every Queued card" in queued
        assert BACKLOG not in json.dumps(raw_lane(shipped(), QUEUED)["clauses"])

    def test_the_reason_says_why_the_queue_leaves_a_backlog_type_state(self):
        reason = raw_lane(shipped(), QUEUED)["reason"]
        assert "`unstarted`" in reason
        assert "`backlog`" in reason
        assert "first" in reason
        assert "cannot change a state's type" in reason

    def test_the_board_action_names_the_operator_step(self):
        action = raw_lane(shipped(), QUEUED)["board_action"]
        for phrase in (
            "create workflow state `Queued`",
            "type `unstarted`",
            "on the DRE team",
            "after `Hand-work`",
            "5750",
            "config/linear-workspace.json",
            "flips this entry's status to live",
        ):
            assert phrase in action, phrase

    def test_it_sits_immediately_after_backlog_so_the_flip_lands_it_in_flow_order(self):
        # The file's own order is the flow order once a lane is live, and the
        # queue it replaces sits between Green Light and Todo.
        names = [entry["name"] for entry in shipped()["lanes"]]
        assert names[names.index(BACKLOG) + 1] == QUEUED


# --------------------------------------------------------------------------- #
# what does not change                                                        #
# --------------------------------------------------------------------------- #


class TestNothingElseMoves:
    def test_live_readers_do_not_see_queued_and_still_see_backlog(self):
        assert QUEUED not in lane_contract.lane_names()
        assert QUEUED not in lane_contract.flow_lanes()
        assert QUEUED in lane_contract.lane_names("arriving")
        assert BACKLOG in lane_contract.lane_names()
        assert lane_contract.lane(BACKLOG)["status"] == "live"

    def test_the_planning_exit_still_lands_in_backlog(self):
        assert lane_contract.planning_exit() == ("Green Light", BACKLOG)

    def test_no_alias_names_queued_yet(self):
        assert BACKLOG not in lane_contract.aliases()
        assert QUEUED not in lane_contract.aliases().values()


# --------------------------------------------------------------------------- #
# the harness stays green while the board does not have it                    #
# --------------------------------------------------------------------------- #


class TestTheHarness:
    BOARD_RULES = {
        "board.every_lane_exists",
        "board.every_state_is_named",
        "pipeline.vocabulary_is_contract_lanes",
    }

    def board(self, *extra):
        return {name: 0 for name in list(lane_contract.lane_names()) + list(extra)}

    def test_a_board_without_the_state_is_green(self):
        report = lane_contract.check(board=self.board(), vocabulary=())
        assert not failed_rules(report) & self.BOARD_RULES

    def test_a_board_that_grew_the_state_is_green(self):
        report = lane_contract.check(board=self.board(QUEUED), vocabulary=())
        assert not failed_rules(report) & self.BOARD_RULES

    def test_a_script_may_name_it_ahead_of_the_flip(self):
        report = lane_contract.check(board=self.board(), vocabulary=(QUEUED,))
        assert "pipeline.vocabulary_is_contract_lanes" not in failed_rules(report)


# --------------------------------------------------------------------------- #
# the flip                                                                    #
# --------------------------------------------------------------------------- #


class TestTheFlip:
    def test_flipping_it_live_needs_no_further_edit(self):
        doc = flipped_doc()
        lane_contract._validate(doc, "<flipped>")
        live = raw_lane(doc, QUEUED)
        backlog = raw_lane(doc, BACKLOG)
        assert {k: v for k, v in live.items() if k != "name"} == renamed(
            {k: v for k, v in backlog.items() if k != "name"}
        )

    def test_once_flipped_it_flows_between_backlog_and_todo(self):
        flow = lane_contract.flow_lanes(flipped_doc())
        assert flow[flow.index(BACKLOG) + 1] == QUEUED
        assert flow[flow.index(QUEUED) + 1] == "Todo"


# --------------------------------------------------------------------------- #
# the rendered document                                                       #
# --------------------------------------------------------------------------- #


class TestTheDocument:
    def doc_text(self):
        with open(os.path.join(ROOT, "docs", "lane-contract.md"), encoding="utf-8") as fh:
            return fh.read()

    def test_the_document_lists_queued_under_arriving(self):
        text = self.doc_text()
        arriving = text.split("## Arriving", 1)[1].split("\n## ", 1)[0]
        assert f"### {QUEUED} — arriving by {EPIC}" in arriving
        assert raw_lane(shipped(), QUEUED)["board_action"] in arriving

    def test_the_document_is_the_render_of_the_contract(self):
        assert self.doc_text() == lane_contract.render_markdown()
