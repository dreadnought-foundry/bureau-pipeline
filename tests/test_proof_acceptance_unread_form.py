"""An acceptance line the gate cannot read for its form is named in the decline (DRE-9182).

THE GAP: the critic's `🔎 proof-row-accepted:` line must run `” — differs:`
straight after the seen quote. On 2026-11-03 the critic's verdict on
agent-bureau #4669 put each row's location in parentheses after the seen
quote — `seen: “const Icon = item.icon” (`EngineNav.tsx:81`) — differs: …`.
`_ACCEPTANCE` matched neither line, `reading()` held both rows unmet, and the
gate's decline named the rows and said nothing of the two lines it could not
read. Nobody learned the acceptances existed and were refused for their form.

THE RULE UNDER TEST: a line of the bound critic verdict that opens with
`ACCEPTANCE_MARK` and that `_ACCEPTANCE` does not read is named in the
decline, cut to 70, as not read because of its form. The grammar is not
loosened: such a line still accepts nothing. The critic brief says, in one
sentence, that nothing may come between the seen quote's closing `”` and
` — differs:`.

Run: cd bureau-pipeline && python3 -m pytest tests/test_proof_acceptance_unread_form.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import merge_gate  # noqa: E402
import proof_record  # noqa: E402

CRITIC_BRIEF = ROOT / "briefs" / "critic.md"

HEAD = "9318eb2" + "0" * 33
VERDICT_URL = "https://github.com/dreadnought-foundry/agent-bureau/pull/4669#issuecomment-9201"
RECORD_PATH = "architecture/proofs/standard-parts-wave-3.md"
PROOF_BRANCH = "agent/DRE-8986-proof-record"

CRITERION = ("At that tag, a search of `console/web/src/pages/` for a component "
             "declaration finds none")
SEEN = "const Icon = item.icon"
WORDING = (f"Not met. wording-only: seen “{SEEN}” — differs: the search also counts "
           "a function-local alias")
OTHER = "The record reads the nine merges' diffs under the four parts folders"
OTHER_SEEN = "console/web/src/components/molecules/foldLine.pin.test.ts"
OTHER_WORDING = (f"Not met. wording-only: seen “{OTHER_SEEN}” — differs: one merge "
                 "changed a test file")

#: The sentence the critic brief must carry, word for word.
FORM_SENTENCE = ("Nothing may come between the seen quote's closing `”` and "
                 "` — differs:`: a location goes inside the differs or reason part.")


def table(*rows) -> str:
    lines = ["| Criterion | Result |", "|---|---|"]
    lines += [f"| {c} | {r} |" for c, r in rows]
    lines.append("| The record is merged to main | Pending |")
    return "# Proof: standard parts, wave 3\n\n" + "\n".join(lines) + "\n"


RECORD = table((CRITERION, WORDING), (OTHER, OTHER_WORDING))


def acceptance(criterion=CRITERION, seen=f"“{SEEN}”") -> str:
    return (f"{proof_record.ACCEPTANCE_MARK} “{proof_record._cut(criterion, 70)}” — "
            f"seen: {seen} — differs: the card's search also counts two indented "
            "aliases — reason: The record's own pattern prints exactly those lines.")


#: The #4669 shapes: a location in parentheses after the seen quote, and two
#: quotes each with its location.
PARENTHETICAL = acceptance(OTHER, f"“{OTHER_SEEN}” (`modified`, +2/-2)")
TWO_QUOTES = acceptance(seen=f"“{SEEN}” (`components/engine/EngineNav.tsx:81`) and "
                             "“const Icon = PACE_ICON[status]” "
                             "(`components/engine/StatusAtom.tsx:63`)")


def verdict(*lines, url=VERDICT_URL) -> dict:
    body = "\n".join([f"🔎 QA Critic — VERDICT: APPROVE @{HEAD}", "", "The record holds.",
                      "", *lines])
    return {"body": body, "url": url}


def shortfall(text, *lines, url=VERDICT_URL):
    return proof_record.shortfall(proof_record.Record(RECORD_PATH, text, None),
                                  verdict(*lines, url=url))


def test_the_4669_shapes_are_not_read_as_acceptances():
    # The fixture's premise, and the grammar not loosened: neither line accepts.
    for line in (PARENTHETICAL, TWO_QUOTES):
        assert proof_record.acceptances(line) == [], line
    read = proof_record.reading(RECORD, verdict(PARENTHETICAL, TWO_QUOTES))
    assert read.accepted == [] and read.checked == []
    assert read.unmet == [(CRITERION, WORDING), (OTHER, OTHER_WORDING)]


def test_the_same_lines_in_their_form_accept_both_rows():
    good = [acceptance(), acceptance(OTHER, f"“{OTHER_SEEN}”")]
    read = proof_record.reading(RECORD, verdict(*good))
    assert read.unmet == [] and len(read.checked) == 2
    assert read.unread == []


def test_the_reading_holds_each_unread_line():
    read = proof_record.reading(RECORD, verdict(PARENTHETICAL, TWO_QUOTES))
    assert read.unread == [PARENTHETICAL, TWO_QUOTES]


def test_the_decline_names_each_unread_line_cut_to_seventy():
    why = shortfall(RECORD, PARENTHETICAL, TWO_QUOTES)
    assert why is not None and "2 row(s) not met" in why
    assert proof_record.UNREAD_FORM in why
    for line in (PARENTHETICAL, TWO_QUOTES):
        assert len(line) > 70
        assert f"“{proof_record._cut(line, 70)}”" in why, line
        assert line not in why


def test_the_merge_gates_hold_says_it():
    decision = merge_gate.evaluate_proof_record(
        PROOF_BRANCH, {"applies": True, "path": RECORD_PATH, "text": RECORD, "detail": None},
        verdict(PARENTHETICAL))
    assert decision is not None and decision.action == "hold"
    assert proof_record.UNREAD_FORM in decision.reason
    assert proof_record._cut(PARENTHETICAL, 70) in decision.reason


def test_a_well_formed_line_beside_an_unread_one_is_not_named_unread():
    good = acceptance()
    why = shortfall(RECORD, good, PARENTHETICAL)
    assert why is not None and "1 row(s) not met" in why
    assert proof_record._cut(PARENTHETICAL, 70) in why
    assert proof_record._cut(good, 70) not in why


def test_no_unread_line_says_nothing_of_form():
    assert proof_record.UNREAD_FORM not in shortfall(RECORD)
    assert proof_record.UNREAD_FORM not in shortfall(RECORD, acceptance())


def test_only_the_bound_verdict_is_read_for_form():
    # A verdict with no comment link accepts nothing, so it reports nothing either.
    assert proof_record.UNREAD_FORM not in shortfall(RECORD, PARENTHETICAL, url="")
    # A line written into the record is the reading run's own, not the checker's.
    own = RECORD + "\n" + PARENTHETICAL + "\n"
    assert proof_record.reading(own).unread == []
    # A line that does not open its line is not an acceptance line at all.
    assert proof_record.reading(RECORD, verdict("> " + PARENTHETICAL)).unread == []


def test_the_critic_brief_says_nothing_comes_after_the_seen_quote():
    text = " ".join(CRITIC_BRIEF.read_text(encoding="utf-8").split())
    assert FORM_SENTENCE in text, (
        f"briefs/critic.md does not carry the acceptance line's form sentence: {FORM_SENTENCE!r}")
