"""The lane contract flips `Queued`, the queue lane that replaces Backlog, from
ARRIVING to LIVE (DRE-6740, epic DRE-6739) — the way DRE-5320 flipped Hand-work.

DRE-6710 declared the entry arriving, so the contract named the lane before the
board had it. DRE-6717 created the `unstarted` state on the DRE board, and
DRE-6726 observed it there; this card is the pull request that says so. The
flip changes the status and deletes the three arriving keys — `arriving_by`,
`reason` and `board_action` — and nothing else: the clauses are still Backlog's
word for word, apart from the places where the text names the lane itself.

Nothing else moves here either: Backlog stays live, every writer still writes
Backlog, the planning exit still lands in Backlog, and no alias is declared
yet. DRE-6741 adds the alias and DRE-6742 moves the planning exit.
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
ROOT = os.path.join(os.path.dirname(__file__), "..")

# The three keys DRE-6710's arriving entry carried, verbatim. The flip deletes
# them from the shipped file; they live on here so the flip is proven against
# the entry it replaced rather than an invented one.
ARRIVING_KEYS = {
    "arriving_by": EPIC,
    "reason": (
        "The queue a card waits in between the planning exit and its verdict's "
        "destination — the lane that replaces Backlog. Linear draws a "
        "`backlog`-type column first whatever its position, and cannot change a "
        "state's type after creation, so the queue moves to a lane of type "
        "`unstarted` rather than staying in Backlog. Declared here before Linear "
        "has the state, so the board can grow it without turning the harness "
        "red; nothing routes to it until a later card flips its status to live."
    ),
    "board_action": (
        "create workflow state `Queued`, type `unstarted`, on the DRE team, "
        "positioned after `Hand-work` (position 5750 in agent-bureau's "
        "`config/linear-workspace.json`); then DRE-6740, in DRE-4125's sibling "
        "epic DRE-6739, flips this entry's status to live"
    ),
}


def shipped():
    return lane_contract.load()


def raw_lane(doc, name):
    return next(entry for entry in doc["lanes"] if entry["name"] == name)


def renamed(value):
    """Backlog's text with the lane's own name swapped for Queued's — the only
    difference the two entries are permitted."""
    return json.loads(json.dumps(value).replace(BACKLOG, QUEUED))


def arriving_doc():
    """The shipped contract with Queued put back the way DRE-6710 declared it:
    arriving, with its three arriving keys, every clause unchanged."""
    doc = copy.deepcopy(shipped())
    index = next(i for i, entry in enumerate(doc["lanes"]) if entry["name"] == QUEUED)
    live = doc["lanes"][index]
    rebuilt = {"name": QUEUED, "status": "arriving", **ARRIVING_KEYS}
    rebuilt.update({k: v for k, v in live.items() if k not in ("name", "status")})
    doc["lanes"][index] = rebuilt
    return doc


def failed_rules(report):
    return {f.clause_id for f in report.failures()}


# --------------------------------------------------------------------------- #
# the entry                                                                   #
# --------------------------------------------------------------------------- #


class TestTheQueuedEntry:
    def test_it_is_live_in_the_work_segment(self):
        entry = raw_lane(shipped(), QUEUED)
        assert entry["status"] == "live"
        assert entry["segment"] == "work"

    def test_the_three_arriving_keys_are_gone(self):
        entry = raw_lane(shipped(), QUEUED)
        for key in ARRIVING_KEYS:
            assert key not in entry, key

    def test_it_carries_exactly_backlogs_keys(self):
        entry = raw_lane(shipped(), QUEUED)
        backlog = raw_lane(shipped(), BACKLOG)
        assert set(entry) == set(backlog)

    def test_the_flip_changed_the_status_and_deleted_the_arriving_keys_only(self):
        # Every key the live entry carries is one the arriving entry carried,
        # with the same value, and the arriving keys are the only ones dropped.
        live = raw_lane(shipped(), QUEUED)
        arriving = raw_lane(arriving_doc(), QUEUED)
        lane_contract._validate(arriving_doc(), "<arriving>")
        assert set(arriving) - set(live) == set(ARRIVING_KEYS)
        assert {k: v for k, v in arriving.items()
                if k not in ARRIVING_KEYS and k != "status"} == {
            k: v for k, v in live.items() if k != "status"
        }

    def test_its_clauses_are_backlogs_with_only_the_lanes_own_name_changed(self):
        # Field for field, every clause: text, enforced_from, assertion,
        # pending and the writers list. DRE-6710 pinned the arriving entry to
        # this; holding the live one to it is what proves the flip moved no
        # clause.
        queued = raw_lane(shipped(), QUEUED)["clauses"]
        backlog = raw_lane(shipped(), BACKLOG)["clauses"]
        assert set(queued) == set(lane_contract.CLAUSE_KINDS)
        for kind in lane_contract.CLAUSE_KINDS:
            assert queued[kind] == renamed(backlog[kind]), kind

    def test_the_one_place_backlog_names_itself_now_names_queued(self):
        backlog = raw_lane(shipped(), BACKLOG)["clauses"]["evidence"]["pending"]
        queued = raw_lane(shipped(), QUEUED)["clauses"]["evidence"]["pending"]
        assert "every Backlog card" in backlog
        assert "every Queued card" in queued
        assert BACKLOG not in json.dumps(raw_lane(shipped(), QUEUED)["clauses"])

    def test_it_sits_immediately_after_backlog(self):
        names = [entry["name"] for entry in shipped()["lanes"]]
        assert names[names.index(BACKLOG) + 1] == QUEUED


# --------------------------------------------------------------------------- #
# the readers                                                                 #
# --------------------------------------------------------------------------- #


class TestTheReaders:
    def test_live_readers_see_queued(self):
        assert QUEUED in lane_contract.lane_names()
        assert QUEUED in [entry["name"] for entry in lane_contract.lanes()]
        assert lane_contract.lane(QUEUED)["status"] == "live"

    def test_no_lane_is_arriving(self):
        assert lane_contract.lanes("arriving") == ()

    def test_it_flows_between_backlog_and_todo(self):
        flow = lane_contract.flow_lanes()
        assert flow[flow.index(BACKLOG) + 1] == QUEUED
        assert flow[flow.index(QUEUED) + 1] == "Todo"


# --------------------------------------------------------------------------- #
# what does not change                                                        #
# --------------------------------------------------------------------------- #


class TestNothingElseMoves:
    def test_backlog_is_still_live(self):
        assert BACKLOG in lane_contract.lane_names()
        assert lane_contract.lane(BACKLOG)["status"] == "live"

    def test_the_planning_exit_still_lands_in_backlog(self):
        assert lane_contract.planning_exit() == ("Green Light", BACKLOG)

    def test_no_alias_names_queued_yet(self):
        assert BACKLOG not in lane_contract.aliases()
        assert QUEUED not in lane_contract.aliases().values()


# --------------------------------------------------------------------------- #
# the harness now requires the state on the board                             #
# --------------------------------------------------------------------------- #


class TestTheHarness:
    def board(self, *names):
        return {name: 0 for name in names}

    def test_a_board_without_the_state_is_red(self):
        names = [n for n in lane_contract.lane_names() if n != QUEUED]
        report = lane_contract.check(board=self.board(*names), vocabulary=())
        assert "board.every_lane_exists" in failed_rules(report)

    def test_a_board_carrying_the_state_is_green(self):
        report = lane_contract.check(
            board=self.board(*lane_contract.lane_names()), vocabulary=()
        )
        assert not failed_rules(report) & {
            "board.every_lane_exists",
            "board.every_state_is_named",
        }

    def test_a_script_may_name_it(self):
        report = lane_contract.check(board=None, vocabulary=(QUEUED,))
        assert "pipeline.vocabulary_is_contract_lanes" not in failed_rules(report)


# --------------------------------------------------------------------------- #
# the rendered document                                                       #
# --------------------------------------------------------------------------- #


class TestTheDocument:
    def doc_text(self):
        with open(os.path.join(ROOT, "docs", "lane-contract.md"), encoding="utf-8") as fh:
            return fh.read()

    def test_the_document_lists_queued_among_the_live_lanes(self):
        text = self.doc_text()
        live = text.split("\n## The lanes\n", 1)[1].split("\n## ", 1)[0]
        assert f"\n### {QUEUED}\n" in live
        assert "\n## Arriving\n" not in text
        assert ARRIVING_KEYS["board_action"] not in text

    def test_the_document_is_the_render_of_the_contract(self):
        assert self.doc_text() == lane_contract.render_markdown()
