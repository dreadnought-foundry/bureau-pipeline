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
