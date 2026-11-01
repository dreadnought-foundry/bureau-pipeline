"""A critic's acceptance of a row whose criterion holds a pipe is matched (DRE-8560).

THE BUG: a markdown table cell writes a pipe as `\\|`, and `_cells` reads it
back as the `|` it stands for. The critic quotes the criterion as the record's
source writes it, escape and all, and `_naming` compared that quote to the
unescaped parse with exact equality — `(ring\\|outline)` is not
`(ring|outline)` — so the acceptance named nothing and the row stayed unmet.
DRE-8386's QA critic accepted such a row on 2026-10-31 and the gate declined
the record anyway; an operator signed it by hand. A pipe past character 70 was
cut away before the compare, so the defect came and went with where one
character fell.

THE RULE UNDER TEST: both sides are normalized in one place before the compare
— `\\|` read as `|`, whitespace collapsed — and the compare is still equality
with the row's own criterion, never a substring, so an acceptance never names
another row. An acceptance in the bound verdict that names no row at all is
said in `shortfall` against each candidate row it could have meant, so it is
no longer indistinguishable from no acceptance.

Run: cd bureau-pipeline && python3 -m pytest tests/test_proof_accept_escaped_pipe.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import proof_record  # noqa: E402

HEAD = "cd34" * 10
VERDICT_URL = "https://github.com/dreadnought-foundry/agent-bureau/pull/3460#issuecomment-9101"
RECORD_PATH = "architecture/proofs/standard-parts-wave-2.md"

SEEN = "focus: 2px solid var(--ring) on all 14 controls"
WORDING = (f"Not met. wording-only: seen “{SEEN}” — differs: the card says "
           "a ring, the stylesheet draws an outline")

#: A criterion as the record's source writes it — the pipe escaped, before
#: character 70 — and as `_cells` reads it back.
SHORT_SOURCE = r"Every control shows its focus (ring\|outline) on keyboard focus"
SHORT = SHORT_SOURCE.replace("\\|", "|")
#: The pipe before character 70, the criterion longer than 70.
EARLY_SOURCE = (r"Every control shows its focus (ring\|outline) on keyboard focus, "
                "in both themes, on every page the console renders")
EARLY = EARLY_SOURCE.replace("\\|", "|")
#: The pipe well past character 70.
LATE_SOURCE = ("Every control in the standard parts shows its focus indicator on "
               r"keyboard focus, drawn as a (ring\|outline) in both themes")
LATE = LATE_SOURCE.replace("\\|", "|")
#: A second candidate row, so an acceptance can name the wrong one.
NEIGHBOR = "Every dialog traps focus until it closes"
NEIGHBOR_SEEN = "Tab from the last field returns to the first"
NEIGHBOR_WORDING = (f"Not met. wording-only: seen “{NEIGHBOR_SEEN}” — differs: the card "
                    "says traps, the record read cycles")


def table(*rows) -> str:
    lines = ["| Criterion | Result |", "|---|---|"]
    lines += [f"| {c} | {r} |" for c, r in rows]
    lines.append("| The record is merged to main | Pending |")
    return "# Proof: standard parts, wave 2\n\n" + "\n".join(lines) + "\n"


def acceptance(named: str, seen: str = SEEN) -> str:
    return (f"🔎 proof-row-accepted: “{named}” — seen: “{seen}” — differs: ring versus "
            "outline — reason: The indicator draws on every control; only its name differs.")


def verdict(*lines) -> dict:
    body = "\n".join([f"🔎 QA Critic — VERDICT: APPROVE @{HEAD}", "", "The record holds.",
                      "", *lines])
    return {"body": body, "url": VERDICT_URL}


def shortfall(text, *lines):
    return proof_record.shortfall(proof_record.Record(RECORD_PATH, text, None),
                                  verdict(*lines))


def test_the_parse_reads_the_escaped_pipe_as_a_pipe():
    # The fixture's premise: the row the reader holds carries a real pipe.
    assert proof_record.criterion_rows(table((SHORT_SOURCE, WORDING)))[0] == (SHORT, WORDING)


def test_an_acceptance_quoting_the_escaped_pipe_accepts_its_row():
    text = table((SHORT_SOURCE, WORDING))
    read = proof_record.reading(text, verdict(acceptance(SHORT_SOURCE)))
    assert read.unmet == []
    assert (SHORT, WORDING) in read.accepted
    assert (SHORT, VERDICT_URL) in read.checked
    assert shortfall(text, acceptance(SHORT_SOURCE)) is None


def test_an_acceptance_quoting_the_pipe_unescaped_still_accepts_its_row():
    text = table((SHORT_SOURCE, WORDING))
    read = proof_record.reading(text, verdict(acceptance(SHORT)))
    assert read.unmet == [] and (SHORT, VERDICT_URL) in read.checked


def test_whitespace_runs_in_the_quote_are_collapsed():
    named = SHORT_SOURCE.replace(" on keyboard", "   on\tkeyboard")
    read = proof_record.reading(table((SHORT_SOURCE, WORDING)), verdict(acceptance(named)))
    assert read.unmet == []


def test_a_pipe_before_character_seventy_matches_cut_either_way():
    assert len(EARLY) > 70 and EARLY.index("|") < 70
    text = table((EARLY_SOURCE, WORDING))
    # Cut as the source writes it, escape and all.
    from_source = proof_record._cut(EARLY_SOURCE, 70)
    # Cut as the reader holds it, the pipe escaped back as the source spells it.
    from_parse = proof_record._cut(EARLY, 70).replace("|", "\\|")
    assert from_source != from_parse
    for named in (from_source, from_parse):
        read = proof_record.reading(text, verdict(acceptance(named)))
        assert read.unmet == [], named
        assert (EARLY, VERDICT_URL) in read.checked, named
        assert shortfall(text, acceptance(named)) is None, named


def test_a_pipe_past_character_seventy_still_matches():
    assert LATE.index("|") > 70
    text = table((LATE_SOURCE, WORDING))
    named = proof_record._cut(LATE_SOURCE, 70)
    read = proof_record.reading(text, verdict(acceptance(named)))
    assert read.unmet == [] and (LATE, VERDICT_URL) in read.checked


def test_the_whole_criterion_uncut_is_still_not_the_cut():
    # Normalized, not loosened: a long criterion is named by its cut only.
    text = table((EARLY_SOURCE, WORDING))
    read = proof_record.reading(text, verdict(acceptance(EARLY_SOURCE)))
    assert read.unmet == [(EARLY, WORDING)]


def test_an_acceptance_naming_a_different_row_does_not_accept_this_one():
    text = table((SHORT_SOURCE, WORDING), (NEIGHBOR, NEIGHBOR_WORDING))
    read = proof_record.reading(text, verdict(acceptance(NEIGHBOR, NEIGHBOR_SEEN)))
    assert read.unmet == [(SHORT, WORDING)]
    assert read.checked == [(NEIGHBOR, VERDICT_URL)]


def test_part_of_the_criterion_is_not_the_criterion():
    text = table((SHORT_SOURCE, WORDING))
    for named in (r"(ring\|outline)", r"Every control shows its focus (ring\|outline)",
                  SHORT_SOURCE + " and more"):
        read = proof_record.reading(text, verdict(acceptance(named)))
        assert read.unmet == [(SHORT, WORDING)], named
        assert read.checked == [], named


def test_shortfall_names_the_row_an_unmatched_acceptance_leaves_unmet():
    text = table((SHORT_SOURCE, WORDING))
    stray = r"Every control shows its focus (ring\|outline) on hover"
    why = shortfall(text, acceptance(stray))
    assert why is not None
    assert SHORT in why
    assert proof_record.UNMATCHED in why
    assert "on hover" in why
    # With no acceptance at all the row reads as it always has.
    plain = shortfall(text)
    assert plain is not None and SHORT in plain
    assert proof_record.UNMATCHED not in plain


def test_an_acceptance_that_matched_its_row_is_not_said_against_another():
    text = table((SHORT_SOURCE, WORDING), (NEIGHBOR, NEIGHBOR_WORDING))
    why = shortfall(text, acceptance(SHORT_SOURCE))
    assert why is not None and NEIGHBOR in why
    assert proof_record.UNMATCHED not in why
