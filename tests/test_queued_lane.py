"""The lane contract flips `Queued`, the queue lane that replaces Backlog, from
ARRIVING to LIVE (DRE-6740, epic DRE-6739) — the way DRE-5320 flipped Hand-work.

DRE-6710 declared the entry arriving, so the contract named the lane before the
board had it. DRE-6717 created the `unstarted` state on the DRE board, and
DRE-6726 observed it there; this card is the pull request that says so. The
flip changes the status and deletes the three arriving keys — `arriving_by`,
`reason` and `board_action` — and nothing else: the clauses are still Backlog's
word for word, apart from the places where the text names the lane itself.

Nothing else moved there either: Backlog stayed live, every writer still wrote
Backlog, and the planning exit still landed in Backlog. DRE-6741 added the alias
`Backlog → Queued` and opened each of Backlog's clauses with the sentence that
says the lane is being replaced.

DRE-6742 moves the planning exit. `planning_exit.to` reads `Queued`, so
`lane_contract.QUEUE_LANE` does, and every writer the earlier cards converted
writes `Queued` at once; the routing and planning vocabularies' destinations
move with it. Its precondition is pinned here: no string constant in any
script still names `Backlog`, so no script reader is left reading only the old
name when the writes move. Backlog stays live and stays an alias, so a card
still under the old name is read as queued.

DRE-6753 retires Backlog. Its entry is now `retiring`, carrying `retired_by`,
`replaced_by`, `reason` and `board_action` and no clauses — the clauses it had
were Queued's, word for word, and live on there. The alias stays, so a card
still under the old name is still read as queued, and the sweep's drain moves
it into Queued. `tests/test_backlog_retired.py` pins the retirement itself.
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


# DRE-6741's wording, which said the exit had not moved yet. Nothing may still
# say it once the exit has moved.
NOT_YET = "the planning exit still lands here until DRE-6742 moves it"


def shipped():
    return lane_contract.load()


def raw_lane(doc, name):
    return next(entry for entry in doc["lanes"] if entry["name"] == name)


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

    def test_it_carries_a_live_lanes_keys(self):
        entry = raw_lane(shipped(), QUEUED)
        assert set(entry) == {"name", "status", "segment", "clauses"}
        assert set(entry["clauses"]) == set(lane_contract.CLAUSE_KINDS)

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

    def test_its_clauses_name_queued_and_never_the_lane_it_replaced(self):
        # The clauses were Backlog's word for word, with the lane's own name
        # swapped (DRE-6740). Backlog now carries none (DRE-6753), so Queued's
        # are the only copy, and they name the old lane nowhere.
        queued = raw_lane(shipped(), QUEUED)["clauses"]
        assert "every Queued card" in queued["evidence"]["pending"]
        assert BACKLOG not in json.dumps(queued)

    def test_it_sits_immediately_after_the_retiring_entry(self):
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

    def test_it_flows_between_green_light_and_todo(self):
        # Backlog sat between them until it began retiring (DRE-6753).
        flow = lane_contract.flow_lanes()
        assert BACKLOG not in flow
        assert flow[flow.index("Green Light") + 1] == QUEUED
        assert flow[flow.index(QUEUED) + 1] == "Todo"


# --------------------------------------------------------------------------- #
# the alias, and the lane it replaced (DRE-6741, DRE-6753)                     #
# --------------------------------------------------------------------------- #


class TestBacklogIsReplaced:
    def test_backlog_is_still_an_alias_for_queued(self):
        assert lane_contract.aliases()[BACKLOG] == QUEUED

    def test_backlog_is_retiring_and_no_longer_live(self):
        assert BACKLOG not in lane_contract.lane_names()
        assert lane_contract.lane(BACKLOG, status="retiring")["replaced_by"] == QUEUED

    def test_backlog_carries_no_clauses_and_names_no_writer(self):
        assert "clauses" not in raw_lane(shipped(), BACKLOG)

    def test_no_queued_clause_says_it_is_being_replaced(self):
        clauses = raw_lane(shipped(), QUEUED)["clauses"]
        for kind in lane_contract.CLAUSE_KINDS:
            assert "Being replaced" not in clauses[kind]["text"], kind


# --------------------------------------------------------------------------- #
# the harness now requires the state on the board                             #
# --------------------------------------------------------------------------- #


class TestTheHarness:
    def board(self, *names):
        return {name: 0 for name in names}

    def test_a_board_without_the_state_is_red(self):
        names = [n for n in lane_contract.lane_names() if n != QUEUED] + [BACKLOG]
        report = lane_contract.check(board=self.board(*names), vocabulary=())
        assert "board.every_lane_exists" in failed_rules(report)

    def test_a_board_carrying_the_state_is_green(self):
        report = lane_contract.check(
            board=self.board(*lane_contract.lane_names(), BACKLOG), vocabulary=()
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


# --------------------------------------------------------------------------- #
# the flip (DRE-6742)                                                         #
# --------------------------------------------------------------------------- #


class TestThePlanningExitMoves:
    def test_the_planning_exit_lands_in_queued(self):
        assert lane_contract.planning_exit() == ("Green Light", QUEUED)

    def test_writers_write_queued(self):
        assert lane_contract.QUEUE_LANE == QUEUED
        assert lane_contract.queue_lane() == QUEUED

    def test_readers_still_read_both_names(self):
        assert lane_contract.QUEUE_LANES == (QUEUED, BACKLOG)

    def test_no_lane_still_says_the_exit_has_not_moved(self):
        text = json.dumps(shipped()["lanes"])
        assert NOT_YET not in text
        assert "every writer still writes this lane" not in text

    def test_the_document_says_it_too(self):
        with open(os.path.join(ROOT, "docs", "lane-contract.md"), encoding="utf-8") as fh:
            text = fh.read()
        assert f"**Green Light → {QUEUED}**" in text
        assert NOT_YET not in text


class TestTheVocabulariesMoveWithIt:
    """The routing and planning vocabularies name the lane the exit lands in,
    so they move in the same pull request — a PARKED card or a one-off sent to
    a lane the writers no longer write is the split this card exists to stop."""

    def test_a_parked_verdict_lands_in_queued(self):
        import routing_verdict

        assert routing_verdict.destination("PARKED") == QUEUED

    def test_a_one_off_lands_in_queued(self):
        import planning_shape

        assert planning_shape.destination("one-off") == QUEUED


# --------------------------------------------------------------------------- #
# the precondition: no script reads the old name alone                         #
# --------------------------------------------------------------------------- #


def _scripts():
    base = os.path.join(ROOT, "scripts")
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for filename in sorted(filenames):
            if filename.endswith(".py"):
                yield os.path.join(dirpath, filename)


class TestNoScriptNamesTheOldLane:
    """The flip's precondition (DRE-6742). Every reader tests membership in
    `QUEUE_LANES`, read off the alias, and every writer writes `QUEUE_LANE`,
    read off the planning exit — so no string constant in any script names
    `Backlog` at all. Read with `lane_contract._scan_python`, the contract's
    own vocabulary reader, so a docstring counts and a `#` comment recording an
    incident does not. A literal `Backlog` left in a script is a reader that
    stops seeing the queue, or a writer that does not move, the day the exit
    lands in `Queued`."""

    def test_the_scan_sees_every_script(self):
        names = {os.path.basename(path) for path in _scripts()}
        assert {"reconcile.py", "lane_contract.py", "routing_verdict.py",
                "groom_readiness.py"} <= names

    def test_the_scan_finds_a_literal_when_there_is_one(self, tmp_path):
        # The guard on the guard: a scan that finds nothing anywhere would
        # pass the next test over a tree full of literals.
        probe = tmp_path / "probe.py"
        probe.write_text('LANES = ("Planning", "Backlog")\n', encoding="utf-8")
        assert lane_contract._scan_python(str(probe), [BACKLOG]) == {BACKLOG}

    def test_no_string_constant_in_any_script_names_backlog(self):
        named = sorted(
            os.path.relpath(path, ROOT) for path in _scripts()
            if lane_contract._scan_python(path, [BACKLOG])
        )
        assert named == [], named


class TestTheGroomerReadsTheQueueUnderEitherName:
    """`groom_readiness.py` is the script the earlier cards missed: its lanes
    named `Backlog` and never `Queued`, so a card the moved writers file in
    `Queued` would read as finished to a `depends on` line, and an epic there
    as already started. Converted here the way the rest were."""

    def test_a_dependency_in_the_queue_is_open_under_either_name(self):
        import groom_readiness

        for lane in lane_contract.QUEUE_LANES:
            assert lane in groom_readiness.OPEN_STATES, lane

    def test_a_parent_in_the_queue_has_not_started_under_either_name(self):
        import groom_readiness

        for lane in lane_contract.QUEUE_LANES:
            assert lane in groom_readiness.PARENT_NOT_STARTED, lane


class TestTheRegistriesNameTheLaneTheWritersWrite:
    """The holds and acts that describe a converted writer's lane say Queued
    once it writes Queued (DRE-6742). An incident recorded as it happened —
    a card parked in Backlog on a date — is history and stays as it was."""

    @staticmethod
    def _config(name):
        with open(os.path.join(ROOT, "config", name), encoding="utf-8") as fh:
            return json.load(fh)

    def test_the_operator_step_lift_sends_queued_to_hand_work(self):
        entry = self._config("holds.json")["reasons"]["operator-step"]
        assert entry["lift_sends"] == "Queued to Hand-work"
        assert "Backlog" not in entry["why"]

    def test_the_acts_whose_writer_moved_name_queued(self):
        doc = self._config("pipeline-acts.json")
        acts = {act["tag"]: act for act in doc["acts"]}
        for tag, field in (("roll-up-split", "means"),
                           ("hyg-triage-returned", "means"),
                           ("hyg-triage-returned", "why"),
                           ("promotion-stalled", "means")):
            assert "Queued" in acts[tag][field], (tag, field)
            assert "Backlog" not in acts[tag][field], (tag, field)

    def test_the_sweeps_two_queue_receipts_say_queued(self):
        # DRE-6743 anchored both on `{QUEUE_LANE}`; what they mean moves here.
        doc = self._config("pipeline-acts.json")
        for anchor in ("🧹 Auto-advanced {QUEUE_LANE} → Planning",
                       "🧹 Auto-promoted {QUEUE_LANE} → {destination}"):
            rows = [u for u in doc["unconverted"] if u["anchor"] == anchor]
            assert len(rows) == 1, anchor
            assert "Queued" in rows[0]["means"], anchor
            assert "Backlog" not in rows[0]["means"], anchor
