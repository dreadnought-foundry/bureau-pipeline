"""The lane contract says the merge gate parks and releases a card (DRE-4341).

A green, approved pull request GitHub refuses for a missing code-owner review
is a decision for the person the repository's rules name, so the gate moves
its card `In Review` → `Green Light` with the reason, and back once the
review lands — but only a card it parked itself. The contract is DATA the
guard, the sweep and the harness read, so a writer the contract does not name
is a writer nothing permits.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import lane_contract  # noqa: E402

CONTRACT = os.path.join(os.path.dirname(__file__), "..", "config", "lane-contract.json")


def lane(name):
    with open(CONTRACT, encoding="utf-8") as fh:
        doc = json.load(fh)
    return next(entry for entry in doc["lanes"] if entry["name"] == name)


def test_the_merge_gate_is_a_permitted_green_light_writer():
    assert "merge-gate.yml" in lane("Green Light")["clauses"]["writers"]["who"]


def test_green_light_names_the_code_owner_hold_as_an_entrance():
    text = lane("Green Light")["clauses"]["entrance"]["text"]
    assert (
        "the merge gate is holding a green, approved pull request for a "
        "required code-owner review" in text
    )


def test_in_review_names_the_gates_release_of_a_card_it_parked():
    text = lane("In Review")["clauses"]["entrance"]["text"]
    assert "code-owner review" in text
    assert "only a card it parked" in text


def test_the_writer_is_one_the_glossary_defines():
    assert "merge-gate.yml" in lane_contract.writers()


def test_the_rendered_document_carries_the_new_entrance():
    with open(os.path.join(os.path.dirname(__file__), "..", "docs", "lane-contract.md"),
              encoding="utf-8") as fh:
        doc = fh.read()
    assert "required code-owner review" in doc


# The console files the week's agent-failure report into Green Light as one
# `no-code` card (DRE-5018, on the CEO's DRE-4271 decision). The contract has
# to admit that writer and name the fifth kind of row BEFORE the console
# writes one, and a keeper walking the lane has to be able to recognize a
# report row mechanically, so each rule is written once and counted.

REPORT_LEAD = "Weekly agent report — Green Light kind weekly-report"


def test_the_console_is_a_permitted_green_light_writer():
    assert "console" in lane("Green Light")["clauses"]["writers"]["who"]


def test_the_console_is_a_glossary_writer_that_lives_in_agent_bureau():
    with open(CONTRACT, encoding="utf-8") as fh:
        entry = json.load(fh)["writers"]["console"]
    assert entry["path"] is None
    assert "agent-bureau" in entry["note"]
    assert "console" in lane_contract.writers()


def test_green_light_names_six_kinds_and_the_weekly_report_is_one():
    # Six since DRE-6414 added the sweep's question about an epic grown past
    # the size the CEO approved (`epic-growth`).
    entrance = lane("Green Light")["clauses"]["entrance"]
    assert "weekly-report" in entrance["kinds"]
    assert "epic-growth" in entrance["kinds"]
    assert len(entrance["kinds"]) == 6
    assert entrance["text"].startswith("Six kinds of row, and no other")


def test_the_weekly_report_has_no_arrival_record_in_this_repository():
    arrivals = lane("Green Light")["clauses"]["entrance"]["arrivals"]
    assert [a for a in arrivals if a["kind"] == "weekly-report"] == []


def test_the_report_rules_are_each_written_once_in_the_entrance():
    text = lane("Green Light")["clauses"]["entrance"]["text"]
    for phrase in ("weekly-report", "no sweep or keeper", "only the CEO closes it"):
        assert text.count(phrase) == 1, phrase


def test_the_entrance_and_the_evidence_both_carry_the_recognizer():
    clauses = lane("Green Light")["clauses"]
    assert REPORT_LEAD in clauses["entrance"]["text"]
    assert REPORT_LEAD in clauses["evidence"]["text"]
    for text in (clauses["entrance"]["text"], clauses["evidence"]["text"]):
        assert "What broke the agents — week ending <Saturday> (PT)" in text
        assert "no-code" in text
