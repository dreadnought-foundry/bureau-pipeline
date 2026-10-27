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

Since DRE-7954 (epic DRE-7944) a cause row asks nobody: a record whose unmet
rows are all gaps closes its card on its approved merge with one fix card
(DRE-7952, DRE-7953), so the brief writes no question for one. The same file
holds the brief's other row grammars to the constants that read them: the
wording-only row the critic accepts (DRE-7949), the stamped waiting row and
the two dispatch reasons a waiting row is re-read on (DRE-7951).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import os  # noqa: E402

# `reconcile` reads `os.environ["REPO"]` at import, and `proof_dispatch`
# imports it; `test_proof_record.py` sets it the same way.
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import linear_ops  # noqa: E402
import proof_cause_park  # noqa: E402
import proof_dispatch  # noqa: E402
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
#: The wording-only row's tail after `proof_record.WORDING_OPENING` (DRE-7949).
WORDING_TAIL = " seen “<quote>” — differs: <what>"
#: The stamped waiting row, its event and stamp placeholders as the brief
#: writes them (DRE-7316, DRE-7951).
WAITING_GRAMMAR = (f"{proof_record.WAITING_OPENING} <the event> "
                   f"[{linear_ops.NOT_BEFORE}<ISO 8601 time with UTC offset>]: "
                   "<what it would show>")
#: The row a last timed re-run writes for an event that still has not come.
LAST_TIMED_ROW = (f"{proof_record.CAUSE_OPENING} misdescribed — the event <event> "
                  "had not come by <PT>")


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
    assert "wording-only" in section, (
        "the section does not point a row seen working at the wording-only row")


def test_a_cause_row_asks_nobody():
    """DRE-7954: the run writes no question for a cause row — the record
    closes its card on its approved merge with one fix card (DRE-7952)."""
    section = _flat(_causes())
    assert "**Nothing is asked.**" in _causes()
    assert "write no escalation file for a cause row" in section
    assert "one fix card" in section and "DRE-7952" in section
    assert proof_cause_park.QUESTION not in section, (
        "the section still quotes the cause question nobody is asked")
    assert "Accept each row as out of date" not in section


def test_the_section_writes_no_escalation_shape():
    lines = _indented(_causes())
    assert not [line for line in lines if line.startswith(proof_cause_park.CAUSE_PREFIX)], (
        "the section still shows a `cause:` line for an escalation file")
    assert not [line for line in lines if line.startswith("❓")], (
        "the section still shows a question line")


def test_the_lanes_paragraph_parks_only_the_ceos_press_or_a_decision():
    lanes = _flat(_section(_text(), "## The lanes, which you never write"))
    assert lanes, "briefs/proof.md has no lanes section"
    assert ("your escalation file parks it in `Green Light`, the CEO's \"needs you\" "
            "queue, only when it names the CEO's press or a decision") in lanes
    assert "for the operator" not in lanes


def test_the_wording_only_grammar_is_the_readers():
    grammar = proof_record.WORDING_OPENING + WORDING_TAIL
    assert grammar in _indented(_text()), (
        f"briefs/proof.md does not quote the wording-only row `{grammar}` on its own "
        f"line — `proof_record.WORDING_OPENING` is {proof_record.WORDING_OPENING!r}")
    filled = (grammar.replace("<quote>", "job started at 14:02")
              .replace("<what>", "the card quotes another log line"))
    assert proof_record.row_wording(filled)


def test_the_critics_acceptance_is_named_and_never_the_runs():
    text = _flat(_text())
    assert f"`{proof_record.ACCEPTANCE_MARK}`" in text, (
        "briefs/proof.md does not name the critic's acceptance line "
        f"`{proof_record.ACCEPTANCE_MARK}`")
    assert "the record's critic accepts" in text


def test_every_waiting_row_is_stamped_in_the_readers_grammar():
    lines = _indented(_text())
    assert WAITING_GRAMMAR in lines, (
        f"briefs/proof.md does not quote the stamped waiting row `{WAITING_GRAMMAR}`")
    assert "Every waiting row carries" in _flat(_text())
    worked = [line for line in lines
              if line.startswith(proof_record.WAITING_OPENING) and "<" not in line]
    assert worked, "briefs/proof.md shows no worked stamped waiting row"
    for line in worked:
        assert proof_record.row_not_before(line) is not None, (
            f"the worked waiting row {line!r} carries no stamp the reader reads")


def test_the_brief_names_the_two_reasons_a_waiting_row_is_re_read_on():
    text = _flat(_text())
    recheck = proof_dispatch.RECHECK_REASON.format(sha7="<sha7>")
    last = proof_dispatch.LAST_TIMED_REASON.format(at="<PT>", sha7="<sha7>")
    for reason in (recheck, last):
        assert f"`{reason}`" in text, f"briefs/proof.md does not name the reason `{reason}`"


def test_the_last_timed_rerun_writes_a_misdescribed_cause_row():
    assert LAST_TIMED_ROW in _indented(_text()), (
        f"briefs/proof.md does not quote the last timed re-run's row `{LAST_TIMED_ROW}`")
    filled = (LAST_TIMED_ROW.replace("<event>", "the weekly report")
              .replace("<PT>", "2026-10-20 09:00 PT"))
    assert proof_record.row_cause(filled) == "misdescribed"
    assert proof_record.row_waiting(filled) is None


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
