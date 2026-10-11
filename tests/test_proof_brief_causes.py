"""The proof brief gives the run the four causes, the row grammar and the one question (DRE-6609).

On 2026-10-10 nine proof runs each met a criterion they could not observe as
the card worded it, and each ended the same way: a record the critic approved,
a gate that declined it, a re-run that read the same thing, and a question an
operator composed by hand. The words the run needs exist in code — the row
grammar is `proof_record.CAUSE_OPENING` (DRE-6607), the four cause words are
`proof_reach.causes()` (DRE-6605), the question and the escalation file's cause
line are `proof_cause_park.py`'s (DRE-6612) — and the run reads none of it. It
reads `briefs/proof.md`.

So the brief quotes each of them, and these tests hold every quotation to the
constant it quotes: a misspelled cause word, a reworded grammar line, a
reworded question or an Identities section that drops the declaration fails
here, naming what drifted.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import console_escalation  # noqa: E402
import proof_cause_park  # noqa: E402
import proof_reach  # noqa: E402
import proof_record  # noqa: E402
from test_american_english_standard import BRITISH  # noqa: E402

BRIEF = ROOT / "briefs" / "proof.md"
HEADING = "## A row you cannot observe as worded"

#: Each cause word, and the cards of 2026-10-10 the brief names as its example.
EXAMPLES = {
    "out-of-reach": ("DRE-6543", "DRE-6431"),
    "state-gone": ("DRE-6431", "DRE-5662"),
    "misdescribed": ("DRE-6204",),
    "operator-step-unblocked": ("DRE-6543", "DRE-6478"),
}

#: The row grammar's placeholder, after the opening the gate reads.
ROW_TAIL = " <cause-word> — <what the run saw instead>"
#: The escalation file's cause line, as `proof_cause_park.cause_line` writes it.
CAUSE_LINE_ROW = proof_cause_park.CauseRow(
    "<criterion, cut to 70>", "<cause-word>", "<what was seen instead>")


def _text() -> str:
    return BRIEF.read_text(encoding="utf-8")


def _flat(text: str) -> str:
    return " ".join(text.split())


def _section(text: str, heading: str) -> str:
    """From `heading` to the next `## ` heading, or "" when it is missing."""
    at = text.find(f"\n{heading}\n")
    if at < 0:
        return ""
    rest = text[at + 1:]
    end = re.search(r"\n## ", rest[len(heading):])
    return rest if end is None else rest[:len(heading) + end.start() + 1]


def _causes() -> str:
    return _section(_text(), HEADING)


def _identities() -> str:
    return _section(_text(), "## Identities — each named for what it is used for")


def _list_heads(section: str) -> dict:
    """Each `- `<word>` …` line of the section, by the word it opens with."""
    return {m.group(1): m.group(0)
            for m in re.finditer(r"^- `([^`]+)`.*$", section, re.MULTILINE)}


def _indented(section: str) -> list:
    """The section's indented (code) lines, stripped."""
    return [line.strip() for line in section.splitlines()
            if line.startswith("    ") and line.strip()]


def test_the_section_follows_the_waiting_row_rule():
    text = _text()
    assert _causes(), f"briefs/proof.md has no `{HEADING}` section (DRE-6609)"
    waiting = text.find("(DRE-6488).**")
    assert 0 <= waiting < text.find(HEADING), (
        f"`{HEADING}` must come after the waiting-row rule (DRE-6488)")


def test_the_row_grammar_is_the_gates_opening_verbatim():
    grammar = proof_record.CAUSE_OPENING + ROW_TAIL
    assert grammar in _indented(_causes()), (
        f"the section does not quote the row grammar `{grammar}` on its own line — "
        f"`proof_record.CAUSE_OPENING` is {proof_record.CAUSE_OPENING!r}")


def test_the_grammar_line_reads_as_a_cause_row_for_every_word():
    """The quoted grammar, its placeholder filled, is a row the gate reads."""
    for word in proof_reach.causes():
        cell = f"{proof_record.CAUSE_OPENING} {word} — the 403 the run saw"
        assert proof_record.row_cause(cell) == word


def test_the_four_cause_words_are_spelled_as_the_declaration_spells_them():
    heads = _list_heads(_causes())
    assert set(heads) == set(proof_reach.causes()), (
        f"the section's cause list names {sorted(heads)}; "
        f"`proof_reach.causes()` declares {sorted(proof_reach.causes())}")
    assert set(EXAMPLES) == set(proof_reach.causes())


@pytest.mark.parametrize("word", sorted(EXAMPLES))
def test_each_cause_is_one_line_with_its_example_card(word):
    line = _list_heads(_causes()).get(word, "")
    for card in EXAMPLES[word]:
        assert card in line, f"the `{word}` line does not name {card} as its example"


def test_the_text_after_the_dash_is_what_the_run_did_see():
    section = _flat(_causes())
    assert "the text after the dash is what the run DID see" in section
    for seen in ("403", "Done", "pool of three"):
        assert seen in section, f"the section gives no `{seen}` example of what was seen"


def test_a_capability_the_run_lacks_is_out_of_reach_never_waiting_for():
    assert ("A capability the run lacks is `out-of-reach` and never `waiting for`"
            in _flat(_causes()))


def test_the_run_never_rewords_and_never_accepts():
    section = _flat(_causes())
    assert "You never reword a row" in section
    assert "never write an accepted row" in section
    assert "DRE-6244" in section, "the section does not say DRE-6244's acceptance stands"


def test_the_question_is_the_modules_verbatim():
    q = f"{console_escalation.QUESTION_PREFIX} {proof_cause_park.QUESTION}"
    assert q in _indented(_causes()), (
        f"the section does not quote the question line {q!r} — "
        f"`proof_cause_park.QUESTION` is {proof_cause_park.QUESTION!r}")


def test_the_three_lines_are_console_escalations():
    lines = _indented(_causes())
    for prefix in (console_escalation.FINDING_PREFIX, console_escalation.QUESTION_PREFIX,
                   console_escalation.RECOMMENDATION_PREFIX):
        assert any(line.startswith(prefix) for line in lines), (
            f"the section's file shape has no `{prefix}` line")


def test_the_cause_line_is_the_one_the_writer_writes():
    template = proof_cause_park.cause_line(CAUSE_LINE_ROW)
    assert template in _indented(_causes()), (
        f"the section does not quote the cause line `{template}` on its own line")


def test_the_cause_line_is_read_back_by_the_writer():
    """Every `cause:` line the section shows, its word filled in, is one
    `rows_from_lines` reads — the worked example included."""
    lines = [line for line in _indented(_causes())
             if line.startswith(proof_cause_park.CAUSE_PREFIX)]
    assert len(lines) >= 2, "the section shows no worked cause line"
    filled = [line.replace("<cause-word>", "misdescribed") for line in lines]
    rows = proof_cause_park.rows_from_lines(filled)
    assert len(rows) == len(lines)
    assert all(r.seen for r in rows), "a cause line names nothing seen instead"


def test_the_worked_file_parses_as_the_three_lines():
    lines = _indented(_causes())
    worked = [line for line in lines if "<" not in line]
    esc = console_escalation.parse("\n".join(worked))
    assert esc is not None, "the section's worked file does not carry the three lines"
    assert esc.question == proof_cause_park.QUESTION
    assert esc.recommendation, "the worked file's Recommendation names no pick"


def test_the_question_is_written_once_after_the_pull_request_opens():
    section = _flat(_causes())
    assert "`/tmp/agent-escalation.txt` once for the card" in section
    assert "push the record and open its pull request" in section


def test_a_cause_row_and_a_press_row_are_one_file():
    section = _flat(_causes())
    assert "A cause row and a press row in one record are one file" in section
    assert "the press lines keep their shape" in section


def test_the_workflow_holds_a_cause_card_in_review_for_the_operator():
    section = _flat(_causes())
    assert ("Once the record pull request is open, the workflow holds the card "
            "`In Review` for the operator and posts the operator's question") in section
    assert "only a press or a decision parks the card in `Green Light`" in section
    # The sentence DRE-6840 retired: a cause file no longer parks in Green Light.
    assert ("posts the hold and the question from that file and parks the card in "
            "`Green Light`") not in section


def test_the_question_line_tells_a_cause_file_from_a_decision():
    section = _flat(_causes())
    assert ("That Question line, word for word, is what tells the workflow a cause "
            "file from a decision") in section


def test_the_lanes_paragraph_parks_only_a_press_or_a_decision():
    lanes = _flat(_section(_text(), "## The lanes, which you never write"))
    assert lanes, "briefs/proof.md has no lanes section"
    assert ("your escalation file parks it in `Green Light`, the CEO's \"needs you\" "
            "queue, only when it names a press or a decision") in lanes


def test_the_identities_section_names_the_declaration():
    section = _flat(_identities())
    assert section, "briefs/proof.md has no Identities section"
    assert f"`{proof_reach.DECLARATION}`" in section, (
        f"the Identities section does not name `{proof_reach.DECLARATION}`, "
        f"the one declaration of what the run can read")
    assert "the record's `## Identities` section is read within that reach" in section


def test_the_section_is_american_english():
    section = _causes()
    assert section, f"briefs/proof.md has no `{HEADING}` section"
    words = set(re.findall(r"[a-z]+", section.lower()))
    assert not words & set(BRITISH), f"British spellings: {sorted(words & set(BRITISH))}"
