"""The plan-time rule for a PROOF criterion, as the standard and the planner state it (DRE-6608).

Nine proofs stood open on the night of 2026-10-09 to 10-10 because a criterion
asked for something a dispatched proof run could never observe, or read the
result of an operator step it was not blocked on. DRE-6605 declared what a
proof run can read once, in `config/proof-reach.json`, with
`scripts/proof_reach.py` as its one reader. This card writes the rule into the
two documents a person reads before writing such a criterion:
`standards/card-quality.md` (the contract) and `briefs/planner.md` (the writer
of the card).

Every assertion binds a document to something that is not the document: the
cause words are read off `proof_reach.causes()`, the lane off
`config/lane-contract.json`, the British spellings off the American English
standard's own test. Only the nine card ids are written here, once, because
they are the reason and no file of the pipeline lists them as data.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import proof_reach  # noqa: E402
from test_american_english_standard import BRITISH  # noqa: E402

STANDARD = ROOT / "standards" / "card-quality.md"
PLANNER = ROOT / "briefs" / "planner.md"
LANE_CONTRACT = ROOT / "config" / "lane-contract.json"

#: The proof-card heading both documents carry, and the passage under it.
PROOF_HEADING = "## Every epic ends with a proof card (DRE-2746, halved by DRE-3669)"
STANDARD_HEADING = (
    "### A PROOF criterion the run cannot observe is caught when the plan is "
    "written (DRE-6603)"
)
PLANNER_HEADING = "### Write every PROOF criterion as a reading the run can make (DRE-6603)"
PASSAGES = {STANDARD: STANDARD_HEADING, PLANNER: PLANNER_HEADING}

#: The nine proofs that stood open on 2026-10-10. The epic's text says eight
#: and lists these nine; the ids are the count.
NINE = (
    "DRE-6543", "DRE-6431", "DRE-6442", "DRE-6042", "DRE-6364",
    "DRE-5377", "DRE-5662", "DRE-6204", "DRE-5409",
)

DECLARATION = proof_reach.DECLARATION
GREEN_LIGHT = "Green Light"
IN_REVIEW = "In Review"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _loose(needle: str) -> re.Pattern:
    """`needle` with every run of whitespace made elastic — the documents
    hard-wrap at 80 columns and a phrase falling across a line break is not a
    document that stopped saying it."""
    return re.compile(r"\s+".join(re.escape(w) for w in needle.split()), re.I)


def _section(body: str, heading: str) -> tuple[int, int]:
    """Where `heading` opens in `body` and where its section ends — the next
    heading of the same or a higher level."""
    at = body.find(heading + "\n")
    assert at >= 0, f"no {heading!r} heading"
    level = len(heading) - len(heading.lstrip("#"))
    end = re.compile(rf"^#{{1,{level}}} ", re.M).search(body, at + len(heading))
    return at, end.start() if end else len(body)


def _passage(path: Path) -> str:
    body = _read(path)
    at, end = _section(body, PASSAGES[path])
    return body[at:end]


def _rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


DOCS = pytest.mark.parametrize("path", tuple(PASSAGES), ids=_rel)


def test_the_nine_are_nine_distinct_cards():
    assert len(NINE) == 9 and len(set(NINE)) == 9


def test_the_declaration_carries_the_cause_words_the_documents_name():
    words = proof_reach.causes()
    assert len(words) == proof_reach.CAUSE_COUNT, (
        f"{DECLARATION} carries {len(words)} cause words; the reader expects "
        f"{proof_reach.CAUSE_COUNT}"
    )


@DOCS
def test_the_passage_opens_under_the_proof_card_heading(path):
    body = _read(path)
    proof_at, proof_end = _section(body, PROOF_HEADING)
    at = body.find(PASSAGES[path] + "\n")
    assert at >= 0, f"{_rel(path)} carries no {PASSAGES[path]!r} heading"
    assert proof_at < at < proof_end, (
        f"{_rel(path)}'s {PASSAGES[path]!r} sits outside its "
        f"{PROOF_HEADING!r} section"
    )


@DOCS
@pytest.mark.parametrize("word", tuple(proof_reach.causes()))
def test_every_cause_word_appears_exactly_as_spelled(path, word):
    assert f"`{word}`" in _passage(path), (
        f"{_rel(path)}'s passage does not name the cause `{word}` as "
        f"{DECLARATION} spells it"
    )


@DOCS
def test_the_passage_names_the_declaration(path):
    assert DECLARATION in _passage(path), (
        f"{_rel(path)}'s passage does not name {DECLARATION}, where what a "
        f"proof run can read is declared"
    )


@pytest.mark.parametrize("card", NINE)
def test_the_standard_names_each_of_the_nine(card):
    assert re.search(rf"\b{card}\b", _passage(STANDARD)), (
        f"standards/card-quality.md's passage no longer names {card}, one of "
        f"the nine proofs that are the rule's reason"
    )


def test_the_standard_names_the_date():
    assert "2026-10-10" in _passage(STANDARD)


@pytest.mark.parametrize("word", tuple(proof_reach.causes()))
def test_the_standard_explains_each_cause_with_one_of_the_nine(word):
    """One line per cause word — the list item that opens with it — and that
    line names one of the nine as its example."""
    item = re.search(
        rf"^- `{re.escape(word)}`[^\n]*(?:\n  [^\n]*)*", _passage(STANDARD), re.M
    )
    assert item, f"the standard has no list item opening with `{word}`"
    assert any(re.search(rf"\b{card}\b", item.group(0)) for card in NINE), (
        f"the standard explains `{word}` with none of the nine cards as its example"
    )


def test_the_standard_puts_the_record_to_the_operator_in_in_review_once():
    """A record whose unmet rows all carry a cause is the operator's to settle,
    held in In Review under a hold (DRE-6837) — never put to the CEO in Green
    Light. The standard and the planner's brief say so the same day."""
    lanes = {lane["name"] for lane in json.loads(_read(LANE_CONTRACT))["lanes"]}
    assert IN_REVIEW in lanes, f"the lane contract no longer carries {IN_REVIEW}"
    passage = _passage(STANDARD)
    assert IN_REVIEW in passage, "the standard's passage names no lane for the record"
    assert _loose("one writer").search(passage), (
        "the standard's passage does not say the question is put through one writer"
    )
    assert _loose("never re-run").search(passage), (
        "the standard's passage does not say the record is never re-run"
    )
    assert _loose(f"never to the CEO in {GREEN_LIGHT}").search(passage), (
        "the standard's passage does not say the record is never put to the CEO "
        f"in {GREEN_LIGHT}"
    )
    planner = _passage(PLANNER)
    assert _loose(IN_REVIEW).search(planner), (
        "briefs/planner.md's passage does not say the row is held in In Review"
    )
    assert not _loose(f"as a question in {GREEN_LIGHT}").search(planner), (
        f"briefs/planner.md's passage still puts the row to the CEO in {GREEN_LIGHT}"
    )


def test_the_standard_carries_the_blocked_by_rule():
    passage = _passage(STANDARD)
    assert "`blockedBy`" in passage
    assert _loose("never read as an operator step").search(passage), (
        "the standard's passage does not say a PROOF card is never read as an "
        "operator step"
    )


def test_the_standard_leaves_the_accepted_row_rule_standing():
    passage = _passage(STANDARD)
    assert "DRE-6244" in passage and _loose("stands unchanged").search(passage), (
        "the standard's passage does not say DRE-6244's accepted-row rule stands"
    )


def test_the_planner_writes_a_blocked_by_line_for_an_operator_step():
    passage = _passage(PLANNER)
    assert "`**Blocked by:**`" in passage
    assert _loose("even across epics").search(passage)


@pytest.mark.parametrize("phrase", (
    "another organization",
    "repo variable",
    "step summary",
    "outside the epic",
))
def test_the_planner_is_told_what_never_to_write(phrase):
    assert _loose(phrase).search(_passage(PLANNER)), (
        f"briefs/planner.md's passage does not tell the planner about {phrase!r}"
    )


@DOCS
def test_the_passage_is_american_english(path):
    passage = _passage(path)
    found = [w for w in BRITISH if re.search(rf"\b{w}\b", passage, re.I)]
    assert not found, f"{_rel(path)}'s passage uses British spellings: {found}"
