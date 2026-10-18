"""The lane contract declares how a build starts (DRE-7105).

Today a card waits in `Queued`, the sweep promotes it to `Todo`, the relay
dispatches a build when a card enters `Todo`, and the build run moves it to
`In Progress`. After the Todo retirement the sweep promotes straight to
`In Progress` and that move IS the dispatch. `build_start` in
`config/lane-contract.json` names the facts that change, so every reader can
learn them first and one later card — the flip — can turn them, in the shape
DRE-6741 gave `QUEUE_LANE` / `QUEUE_LANES` for the queue's cutover.

These tests pin the five names the sibling cards read, prove each is DERIVED
from the file rather than listed, and prove the check refuses every malformed
`build_start` the card names, one fixture each, by field.
"""

import copy
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import lane_contract  # noqa: E402


CONTRACT = os.path.join(
    os.path.dirname(__file__), "..", "config", "lane-contract.json"
)

# The queue's old name, spelled apart so DRE-2722-style sweeps for a retired
# name find it only where it is declared.
OLD_QUEUE = "Back" + "log"


def shipped():
    return json.loads(open(CONTRACT, encoding="utf-8").read())


def raw_lane(doc, name):
    return next(entry for entry in doc["lanes"] if entry["name"] == name)


def build_start_failures(doc):
    report = lane_contract.check(contract=doc, vocabulary=set())
    return [f for f in report.failures() if f.clause_id.startswith("build_start")]


class TestTheShippedContract:
    def test_the_block_is_exactly_what_the_siblings_share(self):
        block = shipped()["build_start"]
        assert block["lane"] == "In Progress"
        assert block["dispatch_lane"] == "In Progress"
        assert block["dispatch_from"] == [
            "Intake", "Planning", "Green Light", "Queued", "Triage",
        ]
        assert block["start_minutes"] == 15
        assert block["why"] and all(line.strip() for line in block["why"])

    def test_start_lane_is_in_progress_since_the_flip(self):
        # The flip (DRE-7120) set it to the dispatch lane.
        assert lane_contract.start_lane() == "In Progress"
        assert lane_contract.START_LANE == "In Progress"

    def test_dispatch_lane_is_in_progress(self):
        assert lane_contract.dispatch_lane() == "In Progress"
        assert lane_contract.DISPATCH_LANE == "In Progress"

    def test_start_lanes_are_the_dispatch_lane_then_the_lane_it_replaces(self):
        assert lane_contract.start_lanes() == ("In Progress", "Todo")
        assert lane_contract.START_LANES == ("In Progress", "Todo")

    def test_start_minutes_is_fifteen_the_old_start_lanes_stall_window(self):
        # It equaled Todo's stall window, which went with Todo's live entry
        # when the lane began retiring (DRE-7129).
        assert lane_contract.start_minutes() == 15
        assert lane_contract.START_MINUTES == 15
        assert "Todo" not in lane_contract.stale_minutes()

    def test_dispatch_from_is_the_five_lanes_and_the_queues_old_name(self):
        assert set(lane_contract.DISPATCH_FROM) == {
            "Intake", "Planning", "Green Light", "Queued", "Triage", OLD_QUEUE,
        }
        assert len(lane_contract.DISPATCH_FROM) == 6
        assert lane_contract.dispatch_from() == lane_contract.DISPATCH_FROM

    def test_todo_is_retiring_and_declares_what_replaces_it(self):
        # DRE-7129: the second step of its retirement.
        todo = lane_contract.lane("Todo", status="retiring")
        assert todo["replaced_by"] == "In Progress"
        assert "Todo" not in lane_contract.lane_names()

    def test_the_offline_check_raises_nothing_against_build_start(self):
        report = lane_contract.check(vocabulary=lane_contract.pipeline_vocabulary())
        assert not [f for f in report.failures() if f.clause_id.startswith("build_start")]
        assert any(
            f.clause_id == "build_start" and f.status == "pass" for f in report.findings
        ), "the check never looked at build_start — a vacuous green"


class TestIsBuildDispatch:
    @pytest.mark.parametrize(
        "from_lane, to_lane, expected",
        [
            ("Queued", "In Progress", True),
            (OLD_QUEUE, "In Progress", True),
            ("Green Light", "In Progress", True),
            ("Triage", "In Progress", True),
            ("Intake", "In Progress", True),
            ("Planning", "In Progress", True),
            ("Todo", "In Progress", False),
            ("In Review", "In Progress", False),
            ("Hand-work", "In Progress", False),
            (None, "In Progress", False),
            ("Queued", "Todo", False),
        ],
    )
    def test_the_table(self, from_lane, to_lane, expected):
        assert lane_contract.is_build_dispatch(from_lane, to_lane) is expected

    def test_the_destination_resolves_through_the_aliases(self):
        doc = shipped()
        doc["aliases"]["entries"].append(
            {"from": "Building", "to": "In Progress", "shim": "test"}
        )
        assert lane_contract.is_build_dispatch("Queued", "Building", contract=doc)

    def test_it_reads_dispatch_from_off_the_contract(self):
        doc = shipped()
        doc["build_start"]["dispatch_from"].remove("Triage")
        assert not lane_contract.is_build_dispatch("Triage", "In Progress", contract=doc)


class TestDerivedNotListed:
    def test_start_lane_follows_the_flip(self):
        doc = shipped()
        doc["build_start"]["lane"] = "In Progress"
        assert lane_contract.start_lane(doc) == "In Progress"

    def test_start_lanes_narrow_when_todos_entry_is_deleted(self):
        doc = shipped()
        doc["build_start"]["lane"] = "In Progress"
        doc["lanes"] = [entry for entry in doc["lanes"] if entry["name"] != "Todo"]
        assert lane_contract.start_lanes(doc) == ("In Progress",)

    def test_start_lanes_read_a_retiring_lane_that_names_the_dispatch_lane(self):
        doc = shipped()
        raw_lane(doc, "Todo").update(
            {"status": "retiring", "retired_by": "DRE-1", "reason": "r", "board_action": "b"}
        )
        assert lane_contract.start_lanes(doc) == ("In Progress", "Todo")

    def test_dispatch_from_drops_the_old_name_with_its_alias(self):
        doc = shipped()
        doc["aliases"]["entries"] = [
            entry for entry in doc["aliases"]["entries"] if entry["to"] != "Queued"
        ]
        assert OLD_QUEUE not in lane_contract.dispatch_from(doc)
        assert "Queued" in lane_contract.dispatch_from(doc)

    def test_dispatch_from_takes_no_alias_for_a_lane_it_does_not_name(self):
        doc = shipped()
        doc["build_start"]["dispatch_from"].remove("Queued")
        assert OLD_QUEUE not in lane_contract.dispatch_from(doc)

    def test_start_minutes_is_read_off_the_block(self):
        doc = shipped()
        doc["build_start"]["start_minutes"] = 40
        assert lane_contract.start_minutes(doc) == 40


def _malformed(field, value):
    doc = shipped()
    doc["build_start"][field] = value
    return doc


MALFORMED = {
    "dispatch lane is not a lane": ("dispatch_lane", _malformed("dispatch_lane", "Building")),
    "dispatch lane is a planning lane": ("dispatch_lane", _malformed("dispatch_lane", "Planning")),
    "dispatch lane is off the flow": ("dispatch_lane", _malformed("dispatch_lane", "Triage")),
    "dispatch lane is retiring": ("dispatch_lane", _malformed("dispatch_lane", OLD_QUEUE)),
    "lane is neither the dispatch lane nor replaced by it": ("lane", _malformed("lane", "Queued")),
    "lane does not exist": ("lane", _malformed("lane", "Building")),
    "dispatch from names a lane that does not exist": (
        "dispatch_from", _malformed("dispatch_from", ["Queued", "Building"])),
    "dispatch from names Hand-work": ("dispatch_from", _malformed("dispatch_from", ["Queued", "Hand-work"])),
    "dispatch from names In Review": ("dispatch_from", _malformed("dispatch_from", ["Queued", "In Review"])),
    "dispatch from names Done": ("dispatch_from", _malformed("dispatch_from", ["Queued", "Done"])),
    "dispatch from names the dispatch lane": (
        "dispatch_from", _malformed("dispatch_from", ["Queued", "In Progress"])),
    "dispatch from is not a list": ("dispatch_from", _malformed("dispatch_from", "Queued")),
    "start minutes is zero": ("start_minutes", _malformed("start_minutes", 0)),
    "start minutes is negative": ("start_minutes", _malformed("start_minutes", -15)),
    "start minutes is a float": ("start_minutes", _malformed("start_minutes", 15.0)),
    "start minutes is a string": ("start_minutes", _malformed("start_minutes", "15")),
    "start minutes is a bool": ("start_minutes", _malformed("start_minutes", True)),
    "start minutes is missing": ("start_minutes", _malformed("start_minutes", None)),
}


class TestTheCheckRefusesAMalformedBlock:
    @pytest.mark.parametrize("case", sorted(MALFORMED))
    def test_refused_with_a_message_naming_the_field(self, case):
        field, doc = MALFORMED[case]
        failures = build_start_failures(doc)
        assert failures, f"{case}: the check accepted it"
        named = [f for f in failures if f.clause_id == f"build_start.{field}"]
        assert named, [f.detail for f in failures]
        assert all(f"build_start.{field}" in f.detail for f in named)
        assert not lane_contract.check(contract=doc, vocabulary=set()).ok

    def test_an_arriving_dispatch_lane_is_refused_on_that_field_alone(self):
        # Hand-work becomes the dispatch lane and the start lane, so `lane` is
        # fine because it IS the dispatch lane, and dispatch_from does not
        # name Hand-work. The one thing wrong is that Hand-work is not live.
        # (Todo played this part until it began retiring, DRE-7129: a
        # retiring entry carries no clauses for an arriving one to owe.)
        doc = shipped()
        raw_lane(doc, "Hand-work")["status"] = "arriving"
        raw_lane(doc, "Hand-work").update({"arriving_by": "x", "reason": "r", "board_action": "b"})
        doc["build_start"].update({"lane": "Hand-work", "dispatch_lane": "Hand-work"})
        failures = build_start_failures(doc)
        assert [f.clause_id for f in failures] == ["build_start.dispatch_lane"]

    def test_the_shipped_block_is_accepted(self):
        assert build_start_failures(shipped()) == []

    def test_the_flipped_block_is_accepted(self):
        doc = shipped()
        doc["build_start"]["lane"] = "In Progress"
        assert build_start_failures(doc) == []

    def test_the_cli_check_names_the_field(self, monkeypatch, capsys):
        doc = _malformed("start_minutes", 0)
        real = lane_contract.check
        monkeypatch.setattr(
            lane_contract, "check", lambda **kw: real(**{**kw, "contract": doc})
        )
        monkeypatch.delenv("LINEAR_API_KEY", raising=False)
        assert lane_contract.main(["check"]) == 1
        assert "build_start.start_minutes" in capsys.readouterr().out


class TestTheDocument:
    def test_the_render_says_how_a_build_starts(self):
        text = lane_contract.render_markdown()
        assert "## How a build starts" in text
        section = text.split("## How a build starts", 1)[1].split("\n## ", 1)[0]
        for name in ("Todo", "In Progress", "Intake", "Triage", "15 min"):
            assert name in section
        for line in shipped()["build_start"]["why"]:
            assert line in section

    def test_the_render_reads_the_block(self):
        doc = copy.deepcopy(lane_contract.load())
        doc["build_start"]["start_minutes"] = 41
        assert "41 min" in lane_contract.render_markdown(doc)

    def test_a_contract_with_no_block_renders_no_section(self):
        doc = copy.deepcopy(lane_contract.load())
        doc.pop("build_start")
        assert "## How a build starts" not in lane_contract.render_markdown(doc)
