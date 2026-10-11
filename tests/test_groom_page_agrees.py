"""The morning's groom page says nothing about its own cards that its tables
contradict (DRE-6910).

The page of 2026-10-11 (proposal ab09ece4ddee, run 38141699804) said two such
things. Under `## Not now — and when to come back` it named DRE-2709 and
DRE-5700 as "sequenced normally" while its Not ready table held both. And its
`## Verified against main` section printed `partly-solved: 4` over a Rewrite
first list of three: the fourth, DRE-2982, was a spare with no Planning slot,
got no draft, and was named nowhere as partly solved. This file pins:

  * the "Named nothing" line names only cards the page sequences normally —
    on the Planning list or waiting under Not now — and a card the page
    holds, cancels or sends to Rewrite first is left out of it;
  * no card on the Not ready table appears under the Not now heading;
  * the number after `partly-solved:` is the number of cards on the Rewrite
    first list, and a partly-solved spare is left out of that count by name,
    with the page saying so;
  * a card superseded without a named successor that is NOT held still
    appears in the "Named nothing" line, as it always did.

Run: cd bureau-pipeline && python3 -m pytest tests/test_groom_page_agrees.py -v
"""
from __future__ import annotations

import copy
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import groomer  # noqa: E402
import groom_readiness  # noqa: E402

from test_groomer_population import CYCLES, card  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "groom-page-ab09ece4ddee.json"
DOC = ROOT / "docs" / "groomer.md"
NOT_NOW_HEADING = "## Not now — and when to come back"
HELD = ["DRE-2709", "DRE-5700"]
REWRITE = ["DRE-3603", "DRE-3549", "DRE-3514"]
SPARE = "DRE-2982"
QUESTION = ("## To decide before building\n\n"
            "* Whether the console keeps its own script.\n")
UNNAMED = "This card is superseded, I think.\n\n"


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _page(proposal: dict | None = None) -> str:
    return groomer.render_proposal(proposal or _fixture()["proposal"])


def _named_nothing(text: str) -> list[str]:
    return [line for line in text.splitlines()
            if line.startswith("Named nothing:")]


def _table_ids(text: str, heading: str) -> list[str]:
    return [line.split("|")[2].strip()
            for line in groomer._section(text, heading)
            if re.match(r"^\| \d+ \| DRE-\d+ \|", line)]


def _printed(text: str, verdict: str) -> int:
    block = groomer._section(text, groomer.VERIFIED_HEADING)
    [line] = [x for x in block if x.startswith(f"- {verdict}:")]
    return int(re.match(rf"^- {verdict}: (\d+)", line).group(1))


# --------------------------------------------------------------------------
# the fixture is the morning's record, unaltered
# --------------------------------------------------------------------------
def test_the_fixture_is_the_record_of_the_morning_and_says_so():
    fx = _fixture()
    assert fx["source"] == {"run": 38141699804,
                            "artifact": "groom-proposal-verified",
                            "file": "proposal-verified.json",
                            "workflow": "self-groomer.yml",
                            "proposal_id": "ab09ece4ddee"}
    prop = fx["proposal"]
    assert prop["id"] == "ab09ece4ddee"
    assert "Nothing is added or altered" in fx["_note"]
    assert prop["unstated_supersessions"] == HELD
    held = {r["identifier"] for r in prop["outcomes"][groomer.NOT_READY]}
    assert set(HELD) <= held
    assert [r["identifier"] for r in prop["outcomes"]["rewrite"]] == REWRITE
    assert prop["verify"]["counts"]["partly-solved"] == 4
    [spare] = [r for r in prop["outcomes"]["not-now"]
               if r["identifier"] == SPARE]
    assert spare["verify"]["verdict"] == "partly-solved"
    assert spare["reconsidered_in"] is not None


# --------------------------------------------------------------------------
# criterion 1 — a held card is never "sequenced normally"
# --------------------------------------------------------------------------
def test_the_replay_names_neither_held_card_as_sequenced_normally():
    text = _page()
    for line in text.splitlines():
        if "sequenced normally" in line:
            for identifier in HELD:
                assert identifier not in line, line


def test_the_replay_still_holds_both_cards_on_the_not_ready_table():
    text = _page()
    held = _table_ids(text, groomer.NOT_READY_HEADING)
    for identifier in HELD:
        assert identifier in held, identifier


# --------------------------------------------------------------------------
# criterion 2 — nothing on the Not ready table sits under Not now
# --------------------------------------------------------------------------
def test_no_card_on_the_not_ready_table_appears_under_not_now():
    text = _page()
    held = set(_table_ids(text, groomer.NOT_READY_HEADING))
    assert held, "the replay holds cards; the fixture is wrong"
    section = "\n".join(groomer._section(text, NOT_NOW_HEADING))
    assert section, "the replay has a Not now section; the fixture is wrong"
    under = set(re.findall(r"\bDRE-\d+\b", section))
    assert not held & under, sorted(held & under)


# --------------------------------------------------------------------------
# criterion 3 — the partly-solved count is the cards listed for it
# --------------------------------------------------------------------------
def test_the_partly_solved_count_equals_the_cards_listed_for_it():
    text = _page()
    listed = _table_ids(text, groomer.REWRITE_HEADING)
    assert listed == REWRITE
    assert _printed(text, "partly-solved") == len(listed)


def test_the_page_says_the_spare_is_left_out_of_the_count_and_names_it():
    text = _page()
    block = groomer._section(text, groomer.VERIFIED_HEADING)
    [line] = [x for x in block if SPARE in x]
    assert line.startswith("Not counted as partly-solved above")
    assert "spare" in line and "no Planning slot" in line
    assert "no draft" in line
    # It names the spare once, and no Rewrite first card beside it.
    assert not any(identifier in line for identifier in REWRITE)


def test_a_morning_with_no_partly_solved_spare_prints_the_count_and_no_note():
    prop = _fixture()["proposal"]
    for row in prop["outcomes"]["not-now"]:
        if row["identifier"] == SPARE:
            row["verify"]["verdict"] = "still-needed"
    text = _page(prop)
    assert _printed(text, "partly-solved") == len(REWRITE)
    assert not any(line.startswith("Not counted as partly-solved")
                   for line in text.splitlines())


def test_a_record_written_before_the_rewrite_list_prints_its_count_as_before():
    prop = _fixture()["proposal"]
    del prop["outcomes"]["rewrite"]
    text = _page(prop)
    assert _printed(text, "partly-solved") == 4
    assert not any(line.startswith("Not counted as partly-solved")
                   for line in text.splitlines())


# --------------------------------------------------------------------------
# criterion 4 — an unheld card still appears in the line, as today
# --------------------------------------------------------------------------
def test_an_unheld_unstated_supersession_still_appears_in_named_nothing():
    cards = [card(f"DRE-{n}") for n in range(1, 31)]
    cards[0]["description"] = UNNAMED                  # in the batch
    cards[1]["description"] = UNNAMED + QUESTION       # held Not ready
    cards[24]["description"] = UNNAMED                 # waits, not this batch
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=10,
                               readiness=groom_readiness.facts(
                                   lanes={}, named={}, epics={}))
    assert proposal["unstated_supersessions"] == ["DRE-1", "DRE-2", "DRE-25"]
    assert [r["identifier"] for r in
            proposal["outcomes"][groomer.NOT_READY]] == ["DRE-2"]
    [line] = _named_nothing(groomer.render_proposal(proposal))
    assert line == ("Named nothing: DRE-1, DRE-25 say they are superseded "
                    "without naming what replaced them, so they are "
                    "sequenced normally rather than proposed for Cancel.")


def test_the_line_is_absent_when_every_card_it_would_name_is_held():
    text = _page()
    assert _named_nothing(text) == []


def test_a_card_the_verify_step_moved_off_the_order_is_not_named():
    prop = _fixture()["proposal"]
    dead = prop["outcomes"]["dead"][0]["identifier"]
    batch = sorted(prop["outcomes"]["now"],
                   key=lambda r: r["position"])[0]["identifier"]
    prop["unstated_supersessions"] = [batch, REWRITE[0], dead, *HELD]
    [line] = _named_nothing(_page(prop))
    assert line.startswith(f"Named nothing: {batch} say ")
    for identifier in (REWRITE[0], dead, *HELD):
        assert identifier not in line, identifier


# --------------------------------------------------------------------------
# the doc says both
# --------------------------------------------------------------------------
def test_the_doc_says_what_the_line_and_the_count_name():
    doc = " ".join(DOC.read_text(encoding="utf-8").split())
    assert "DRE-6910" in doc
    assert ("names only a card on the Planning list or waiting under Not now"
            in doc)
    assert ("`partly-solved` counts the Rewrite first list only" in doc)
