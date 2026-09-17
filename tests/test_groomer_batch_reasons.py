"""Every batch card's reasons, in full and labelled (DRE-3764).

The console's chevron (DRE-3757) opens a groom batch row and shows why that
card is the right one to add. It can only show what the proposal carries, and
until this card that was one "Why" line cut at ninety characters. So the ranked
read is asked for five labelled reasons on every card it puts in the batch —
**why now, value, effort, if skipped, depends on** — and the proposal writes
them where a reader, human or machine, can find them:

  * the read returns them per card, each one through the same plain-English
    guard the one-line reason goes through; a refused one is DROPPED and the
    card is counted in `withheld`, and nothing is written in its place;
  * the proposal comment carries them in their own section AFTER the batch
    table, uncut, so the table the console and the drain already read does not
    move;
  * the JSON artifact carries the same strings from the same source;
  * a rules-only run (`--no-judgement`) writes none of it and renders byte for
    byte what it rendered before this card.

Run: cd bureau-pipeline && python3 -m pytest tests/test_groomer_batch_reasons.py -v
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")

import groom_judgement  # noqa: E402
import groomer  # noqa: E402

from test_groomer import CYCLES, GOLDEN, NOW, card, judged, ranked  # noqa: E402
from test_groomer_render import section  # noqa: E402

GOLDEN_RENDER = ROOT / "tests" / "fixtures" / "groom_rules_only_render.md"

# One `now` line carrying all five labels, written the way the brief asks for
# them. The reason is deliberately longer than the ninety characters the batch
# table cuts at, so "uncut" is a claim with a way of being wrong.
LONG_REASON = ("the console cannot ship the chevron until the proposal carries "
               "the reasons behind every card in the batch")
LABELLED = (f"DRE-1 | now | {LONG_REASON} "
            "| why now: the epic it serves is halfway through "
            "| value: the CEO can read a batch without asking anyone "
            "| effort: a day, and no new moving parts "
            "| if skipped: the chevron opens on an empty row "
            "| depends on: nothing outside this batch")


def labelled_section(text: str) -> str:
    """The labelled-reasons section of a rendered proposal, heading excluded."""
    return section(text, groomer.BATCH_REASONS_HEADING)


def card_block(text: str, identifier: str) -> str:
    """One card's block of that section — its `### DRE-N` heading excluded."""
    body = labelled_section(text)
    assert f"### {identifier}\n" in body, f"{identifier} has no block"
    return body.split(f"### {identifier}\n", 1)[1].split("\n### ", 1)[0]


# --------------------------------------------------------------------------
# the read returns them
# --------------------------------------------------------------------------
def test_the_ranked_read_returns_five_labelled_reasons_for_a_batch_card():
    cards = [card("DRE-1"), card("DRE-2")]
    verdicts = judged(cards, "\n".join([LABELLED, ranked(["DRE-2"])])).verdicts
    assert verdicts["DRE-1"].outcome == "now"
    assert verdicts["DRE-1"].reasons == {
        "why now": "the epic it serves is halfway through",
        "value": "the CEO can read a batch without asking anyone",
        "effort": "a day, and no new moving parts",
        "if skipped": "the chevron opens on an empty row",
        "depends on": "nothing outside this batch",
    }, "the five labelled reasons are not read off the answer line"
    # …and the labels come back in the order the page writes them, so the
    # section's grammar does not depend on the order the model wrote them in.
    assert list(verdicts["DRE-1"].reasons) == list(groom_judgement.REASON_LABELS)


def test_a_line_that_labels_nothing_still_ranks_and_carries_no_reasons():
    """The five are an addition, not a new requirement: a `now` line without
    them is the answer this parser has always read."""
    cards = [card("DRE-1")]
    verdict = judged(cards, ranked(["DRE-1"])).verdicts["DRE-1"]
    assert verdict.outcome == "now" and verdict.reason
    assert verdict.reasons == {}


def test_a_label_the_answer_leaves_out_is_absent_and_nothing_stands_in_for_it():
    cards = [card("DRE-1")]
    answer = ("DRE-1 | now | it finishes the epic "
              "| why now: the epic it serves is halfway through "
              "| effort: a day")
    verdict = judged(cards, answer).verdicts["DRE-1"]
    assert set(verdict.reasons) == {"why now", "effort"}


def test_the_brief_asks_for_every_label_the_parser_reads():
    """The prompt and the parser are one contract, the same way the outcomes
    are: a brief that stopped asking for a label is a brief the parser would
    keep waiting on."""
    prompt = groom_judgement.brief_prompt()
    for label in groom_judgement.REASON_LABELS:
        assert label in prompt.lower(), f"the prompt never asks for {label!r}"
    assert groom_judgement.problems() == []


def test_the_brief_contract_check_names_a_label_that_went_missing(monkeypatch):
    stripped = (ROOT / "briefs" / "groomer.md").read_text(encoding="utf-8") \
        .replace("if skipped", "")
    monkeypatch.setattr(groom_judgement, "_read", lambda path: stripped)
    found = " ".join(groom_judgement.problems())
    assert "if skipped" in found, (
        "a brief that stopped asking for a labelled reason is not reported"
    )


# --------------------------------------------------------------------------
# the proposal carries them, per card
# --------------------------------------------------------------------------
def test_every_batch_row_carries_the_labelled_reasons_the_read_gave_it():
    cards = [card("DRE-1"), card("DRE-2")]
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=5, now=NOW,
                               judgement=judged(cards, "\n".join(
                                   [LABELLED, ranked(["DRE-2"])])))
    rows = {row["identifier"]: row for row in proposal["outcomes"]["now"]}
    assert rows["DRE-1"]["reasons"]["value"] == \
        "the CEO can read a batch without asking anyone"
    assert rows["DRE-2"]["reasons"] == {}, (
        "a card the read gave no labelled reasons for gets none invented"
    )


def test_the_section_renders_every_batch_card_with_its_reasons_uncut():
    cards = [card("DRE-1"), card("DRE-2")]
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=5, now=NOW,
                               judgement=judged(cards, "\n".join(
                                   [LABELLED, ranked(["DRE-2"])])))
    text = groomer.render_proposal(proposal)
    body = labelled_section(text)
    for identifier in ("DRE-1", "DRE-2"):
        assert f"### {identifier}" in body, (
            f"{identifier} is in the batch and has no block in the section"
        )
    block = card_block(text, "DRE-1")
    assert f"- **Why:** {LONG_REASON}" in block, (
        "the full Why line is cut, or absent, in the section"
    )
    assert LONG_REASON not in section(text, "## The batch, in order"), (
        "the fixture's reason is short enough for the table to carry whole, so "
        "'uncut' proves nothing"
    )
    for label, shown in (
            ("why now", "the epic it serves is halfway through"),
            ("value", "the CEO can read a batch without asking anyone"),
            ("effort", "a day, and no new moving parts"),
            ("if skipped", "the chevron opens on an empty row"),
            ("depends on", "nothing outside this batch")):
        assert f"- **{groom_judgement.REASON_LABELS[label]}:** {shown}" in block


def test_a_batch_card_with_no_labelled_reasons_still_gets_its_why():
    cards = [card("DRE-1")]
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=5, now=NOW,
                               judgement=judged(cards, ranked(["DRE-1"])))
    block = card_block(groomer.render_proposal(proposal), "DRE-1")
    assert "- **Why:** the model wanted it" in block
    for display in groom_judgement.REASON_LABELS.values():
        assert f"**{display}:**" not in block


def test_the_section_comes_after_the_batch_table_and_leaves_it_alone():
    """The table is what the console (`groom_proposal._BATCH_ROW`) and the
    drain read back, so the new section goes after it and adds no row to it."""
    cards = [card("DRE-1"), card("DRE-2")]
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=5, now=NOW,
                               judgement=judged(cards, "\n".join(
                                   [LABELLED, ranked(["DRE-2"])])))
    text = groomer.render_proposal(proposal)
    assert text.index("## The batch, in order") < \
        text.index(groomer.BATCH_REASONS_HEADING)
    record = groomer.parse_proposal_comment(groomer.proposal_comment(proposal))
    assert [row["identifier"] for row in record["batch"]] == ["DRE-1", "DRE-2"]
    assert record["id"] == proposal["id"]


# --------------------------------------------------------------------------
# the guard drops, and never substitutes
# --------------------------------------------------------------------------
def test_a_refused_labelled_reason_is_dropped_and_the_card_is_withheld():
    cards = [card("DRE-1"), card("DRE-2")]
    answer = ("DRE-1 | now | it finishes the epic "
              "| why now: the epic it serves is halfway through "
              "| value: it lands `render_proposal` in scripts/groomer.py "
              "| effort: a day")
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=5, now=NOW,
                               judgement=judged(cards, "\n".join(
                                   [answer, ranked(["DRE-2"])])))
    row = next(r for r in proposal["outcomes"]["now"]
               if r["identifier"] == "DRE-1")
    assert set(row["reasons"]) == {"why now", "effort"}, (
        "the technical value line was not dropped"
    )
    assert proposal["judgement"]["withheld"] == ["DRE-1"]
    block = card_block(groomer.render_proposal(proposal), "DRE-1")
    assert "**Value:**" not in block, "the dropped label still has a line"
    assert groomer.WITHHELD_REASON not in block, (
        "a placeholder sentence was written in place of the dropped reason"
    )
    assert "scripts/groomer.py" not in block


def test_one_card_whose_reasons_were_refused_twice_is_withheld_once():
    cards = [card("DRE-1")]
    answer = ("DRE-1 | now | it finishes the epic "
              "| value: see scripts/groomer.py "
              "| effort: run `make test`")
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=5, now=NOW,
                               judgement=judged(cards, answer))
    assert proposal["judgement"]["withheld"] == ["DRE-1"]


# --------------------------------------------------------------------------
# the JSON artifact, from the same source
# --------------------------------------------------------------------------
def test_the_json_artifact_carries_the_same_strings_the_section_shows(tmp_path):
    cards = [card("DRE-1"), card("DRE-2")]
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=5, now=NOW,
                               judgement=judged(cards, "\n".join(
                                   [LABELLED, ranked(["DRE-2"])])))
    out = tmp_path / "proposal.json"
    out.write_text(json.dumps(proposal, indent=2), encoding="utf-8")
    written = json.loads(out.read_text(encoding="utf-8"))
    block = card_block(groomer.render_proposal(proposal), "DRE-1")
    row = next(r for r in written["outcomes"]["now"]
               if r["identifier"] == "DRE-1")
    assert row["reasons"], "the artifact carries no labelled reasons"
    for label, text in row["reasons"].items():
        assert f"- **{groom_judgement.REASON_LABELS[label]}:** {text}" in block
    assert row["reason"] == LONG_REASON
    # …and the same row in `sequence`, which is the other list the console
    # reads, carries the same strings rather than a second reading of them.
    seq = next(r for r in written["sequence"] if r["identifier"] == "DRE-1")
    assert seq["reasons"] == row["reasons"]


# --------------------------------------------------------------------------
# the rules-only page has not moved
# --------------------------------------------------------------------------
def test_a_rules_only_proposal_renders_byte_for_byte_what_it_rendered_before():
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    built = groomer.propose(
        golden["cards"], cycles=golden["cycles"], capacity=golden["capacity"],
        batch_cycles=golden["batch_cycles"], now=golden["now"], judgement=None)
    text = groomer.render_proposal(built)
    assert groomer.BATCH_REASONS_HEADING not in text
    assert text == GOLDEN_RENDER.read_text(encoding="utf-8"), (
        "the rules-only page moved, so the audit card (DRE-3151) can no longer "
        "compare two readings of one population"
    )


def test_a_rules_only_row_carries_no_labelled_reasons_in_the_json():
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    built = groomer.propose(
        golden["cards"], cycles=golden["cycles"], capacity=golden["capacity"],
        batch_cycles=golden["batch_cycles"], now=golden["now"], judgement=None)
    for row in built["outcomes"]["now"]:
        assert row["reasons"] == {}


# --------------------------------------------------------------------------
# the grammar the console parses
# --------------------------------------------------------------------------
def test_the_sections_grammar_is_fixed_for_the_readers_that_parse_it():
    """DRE-3757's chevron renders labelled lines; a follow-up teaches
    `groom_proposal.py` to read them. Both need one shape, so it is pinned
    here rather than left to whatever the renderer happens to emit."""
    cards = [card("DRE-1"), card("DRE-2")]
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=5, now=NOW,
                               judgement=judged(cards, "\n".join(
                                   [LABELLED, ranked(["DRE-2"])])))
    text = groomer.render_proposal(proposal)
    assert groomer.BATCH_REASONS_HEADING == "## Why each card is in the batch"
    lines = labelled_section(text).splitlines()
    heads = [l for l in lines if l.startswith("###")]
    assert heads == ["### DRE-1", "### DRE-2"], (
        "one `### <card id>` heading per batch card, in batch order"
    )
    labels = [re.match(r"^- \*\*([^*]+):\*\* (\S.*)$", l) for l in lines
              if l.startswith("- ")]
    assert labels and all(labels), "every reason line is `- **Label:** text`"
    assert {m.group(1) for m in labels} <= (
        {"Why"} | set(groom_judgement.REASON_LABELS.values())), (
        "the section writes a label outside the vocabulary the reader knows"
    )
    assert groom_judgement.REASON_LABELS == {
        "why now": "Why now", "value": "Value", "effort": "Effort",
        "if skipped": "If skipped", "depends on": "Depends on",
    }, "the labels are the five DRE-3764 names, in that order"
