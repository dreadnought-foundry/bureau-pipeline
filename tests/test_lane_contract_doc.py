"""The documentation RENDERS from the contract (DRE-2726).

The card's claim is that the document and the enforcement are the same object.
That only holds if the document is generated: `docs/lane-contract.md` is
rendered from `config/lane-contract.json`, and this test fails the build when
the committed file and the render disagree — the same shape as every other
derived-not-remembered check in this repo.

The amendment adds one requirement to the render: each clause shows its phase,
so a reader can tell what is LIVE from what is PROMISED.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import lane_contract  # noqa: E402


DOC = os.path.join(os.path.dirname(__file__), "..", "docs", "lane-contract.md")


def committed():
    with open(DOC, encoding="utf-8") as fh:
        return fh.read()


def test_the_committed_document_is_the_render():
    assert committed() == lane_contract.render_markdown(), (
        "docs/lane-contract.md is stale — regenerate it with "
        "`python3 scripts/lane_contract.py render`"
    )


def test_the_document_is_generated_and_says_so():
    head = committed().splitlines()[:6]
    assert any("lane_contract.py render" in line for line in head), (
        "the rendered doc must name the command that regenerates it"
    )


def test_every_live_lane_appears():
    text = committed()
    for name in lane_contract.lane_names(status="live"):
        assert name in text


def test_every_clause_shows_its_phase_and_whether_it_is_live_or_promised():
    text = committed()
    for clause in lane_contract.clauses():
        assert clause.text.split(".")[0][:40] in text, f"{clause.id} text missing"
    # A reader must be able to tell the two apart without reading the JSON.
    assert "Phase" in text
    assert "live" in text and "promised" in text


def test_a_retiring_lane_is_shown_with_the_board_step_it_is_waiting_on():
    text = committed()
    for lane in lane_contract.lanes(status="retiring"):
        assert lane["name"] in text
        assert lane["board_action"][:40] in text


def test_the_render_changes_when_the_contract_changes():
    # Non-vacuous: the render must actually read the data.
    doc = lane_contract.load()
    import copy

    mutated = copy.deepcopy(doc)
    mutated["lanes"][0]["clauses"]["entrance"]["text"] = "a completely different rule"
    assert lane_contract.render_markdown(mutated) != lane_contract.render_markdown(doc)
