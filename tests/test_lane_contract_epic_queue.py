"""The lane contract names the epic-cap queue (DRE-5155, epic DRE-5129).

An epic the CEO approves while the fleet is at its cap of epics in motion
waits in `Green Light` until the sweep starts it. The entrance clause says how
it got there — the `epic-queued` label and the receipt of that name — and the
exit clause says how it leaves: the order the sweep starts waiting epics in,
the re-approved epic that skips the line, and the epic that leaves the line
any other way. Each fact is written once, in the clause it belongs to, so the
test counts occurrences rather than looking for a phrase somewhere.
"""

import json
import os

CONTRACT = os.path.join(os.path.dirname(__file__), "..", "config", "lane-contract.json")


def green_light_clauses():
    with open(CONTRACT, encoding="utf-8") as fh:
        doc = json.load(fh)
    lane = next(entry for entry in doc["lanes"] if entry["name"] == "Green Light")
    clauses = lane["clauses"]
    return clauses["entrance"]["text"], clauses["exit"]["text"]


def test_the_entrance_names_the_queue_label_once():
    entrance, _ = green_light_clauses()
    assert entrance.count("epic-queued") == 1


def test_the_exit_names_the_queue_label_once():
    _, exit_text = green_light_clauses()
    assert exit_text.count("epic-queued") == 1


def test_each_queue_rule_is_written_exactly_once_across_both_clauses():
    entrance, exit_text = green_light_clauses()
    both = entrance + "\n" + exit_text
    for phrase in (
        "wait in line",
        "highest Linear Priority",
        "re-approved after a mid-epic amendment",
        "loses the",
    ):
        assert both.count(phrase) == 1, phrase


def test_the_entrance_names_the_receipt_and_what_the_epic_waits_for():
    entrance, _ = green_light_clauses()
    assert "⏸️ receipt of that same name stating the epic's place in line" in entrance
    assert "wait in line for the sweep" in entrance
