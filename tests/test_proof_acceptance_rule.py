"""Which unmet proof rows the operator may accept, in one wording (DRE-6773).

The CEO ruled on 2026-10-10: a proof row may be accepted by operator decision
only where the mechanism was actually seen working and just the card's
wording, quoted text, timing, count or method of observing differs. A row
whose thing was never seen stays unmet. He rejected "accept all such rows" and
"accept none".

Before this card the standard and the proof brief admitted an accepted row
only when a later shipped decision had overtaken it (DRE-6244), so a record
following the CEO's rule could still be sent back by a critic reading the
standard — and the critic's own brief said nothing about accepted rows at all.

Three documents now carry the rule, and these tests hold them to ONE wording:
`standards/card-quality.md` (every builder and planner), `briefs/proof.md`
(the proof run) and `briefs/critic.md` (the reader who follows the link). A
document that drifts from the others fails here, naming the sentence it lost.

The reader's FORM does not change — `scripts/proof_record.py` still passes
only a Result cell that opens `ACCEPTED by operator decision` and links the
decision in the same cell, and `tests/test_proof_record.py` pins that. The
last test here re-states the two halves of the form against the reader, so a
widened rule in prose can never quietly become a widened reader.

DRE-7949 gave the acceptance to the record's critic: the run writes the row
`Not met. wording-only: seen “…” — differs: …`, and the critic's verdict at
the head accepts it in one `🔎 proof-row-accepted:` line. DRE-7954 rewrote
the two briefs around that, so they carry the rule with the critic as the
writer (`RULE`), and the standard keeps the operator's wording (`LEGACY_RULE`)
until the sibling card that rewrites it. The sentences that name no writer are
in both, so the three documents still read one rule.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import proof_record  # noqa: E402

STANDARD = ROOT / "standards" / "card-quality.md"
PROOF_BRIEF = ROOT / "briefs" / "proof.md"
CRITIC_BRIEF = ROOT / "briefs" / "critic.md"
DOCUMENTS = (STANDARD, PROOF_BRIEF, CRITIC_BRIEF)
#: The documents DRE-7954 rewrote around the critic's acceptance (DRE-7949).
BRIEFS = (PROOF_BRIEF, CRITIC_BRIEF)

#: The sentences that name no writer, as all three documents must carry them.
SHARED = (
    # Overtaken stays, as one named case of the acceptable half.
    "A criterion overtaken by a later shipped decision is one case of this.",
    # The never-acceptable half.
    "A row where the thing itself was not seen is never accepted: it stays "
    "unmet and waits for a new run or a reworded card.",
    # The five cases, in the CEO's order.
    "That covers a value that is only a default, a reader with no access, an "
    "event that has not happened, a missing positive control, and indirect "
    "evidence.",
    # The mixed row.
    "A row that is part shown and part not seen is not acceptable either.",
)

#: The rule, sentence by sentence, as the two briefs carry it since DRE-7954.
#: The sentences are the contract; each document wraps them in its own
#: reader's words around them, never in place of them.
RULE = (
    # The acceptable half.
    "A row may be accepted only where the mechanism was actually observed "
    "working and just the card's wording, quoted text, timing, count or "
    "method of observing differs.",
    # What an acceptance must contain.
    "The acceptance names what was seen, quoted from the record, and says "
    "what differs from the card's words.",
    # Who writes it.
    "The record's critic writes it, in its verdict — never the builder and "
    "never the proof run.",
) + SHARED

#: The rule as the standard still carries it, until the sibling card of
#: DRE-7954 rewrites the standard around the critic's acceptance.
LEGACY_RULE = (
    # The acceptable half.
    "A row may be accepted by operator decision only where the mechanism was "
    "actually observed working and just the card's wording, quoted text, "
    "timing, count or method of observing differs.",
    # Overtaken stays, as one named case of the acceptable half.
    "A criterion overtaken by a later shipped decision is one case of this.",
    # What an acceptance must contain.
    "The acceptance names what was seen, quoted from the record, says what "
    "differs from the card's words, and links the written decision.",
    # Who writes it.
    "The operator writes the decision and the row — never the builder and "
    "never the proof run.",
    # The never-acceptable half.
    "A row where the thing itself was not seen is never accepted: it stays "
    "unmet and waits for a new run or a reworded card.",
    # The five cases, in the CEO's order.
    "That covers a value that is only a default, a reader with no access, an "
    "event that has not happened, a missing positive control, and indirect "
    "evidence.",
    # The mixed row.
    "A row that is part shown and part not seen is not acceptable either.",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _loose(needle: str) -> re.Pattern:
    """`needle` with every run of whitespace made elastic — the documents
    hard-wrap and a sentence falling across a line break, or behind a
    blockquote marker, is not a document that stopped saying it."""
    return re.compile(r"[\s>]+".join(re.escape(w) for w in needle.split()))


def _says(path: Path, needle: str) -> bool:
    return _loose(needle).search(_read(path)) is not None


@pytest.mark.parametrize("path", DOCUMENTS, ids=lambda p: str(p.relative_to(ROOT)))
@pytest.mark.parametrize("sentence", SHARED, ids=range(len(SHARED)))
def test_every_document_carries_the_shared_sentences_word_for_word(path, sentence):
    assert _says(path, sentence), (
        f"{path.relative_to(ROOT)} does not carry the CEO's 2026-10-10 rule "
        f"sentence {sentence!r} (DRE-6773) — the standard, the proof brief "
        f"and the critic brief must read one wording"
    )


@pytest.mark.parametrize("path", BRIEFS, ids=lambda p: str(p.relative_to(ROOT)))
@pytest.mark.parametrize("sentence", RULE, ids=range(len(RULE)))
def test_both_briefs_carry_the_rule_word_for_word(path, sentence):
    assert _says(path, sentence), (
        f"{path.relative_to(ROOT)} does not carry the rule sentence {sentence!r} "
        f"as DRE-7954 wrote it — the proof brief and the critic brief read one wording"
    )


@pytest.mark.parametrize("sentence", LEGACY_RULE, ids=range(len(LEGACY_RULE)))
def test_the_standard_carries_the_rule_until_its_card(sentence):
    assert _says(STANDARD, sentence), (
        f"standards/card-quality.md does not carry the CEO's 2026-10-10 rule "
        f"sentence {sentence!r} (DRE-6773)"
    )


@pytest.mark.parametrize("path", BRIEFS, ids=lambda p: str(p.relative_to(ROOT)))
def test_neither_brief_gives_the_acceptance_to_the_operator(path):
    for stale in ("The operator writes the decision", "A row may be accepted by operator decision",
                  "accepted by the operator"):
        assert not _says(path, stale), (
            f"{path.relative_to(ROOT)} still gives the acceptance to the operator: {stale!r}"
        )


def test_the_standard_names_the_date_and_source_of_the_decision():
    assert _says(STANDARD, "The CEO decided on 2026-10-10"), (
        "standards/card-quality.md must name who decided the acceptance rule "
        "and when — the CEO, 2026-10-10"
    )
    assert _says(STANDARD, "DRE-6773")


@pytest.mark.parametrize("path", DOCUMENTS, ids=lambda p: str(p.relative_to(ROOT)))
def test_no_document_still_limits_acceptance_to_overtaken_alone(path):
    """The DRE-6244 wording made 'overtaken' the only acceptable case. It may
    survive as one case (`RULE[1]`), never again as the limit."""
    for stale in (
        "Use it only when a criterion has been overtaken by a later shipped decision",
        "An accepted row is used only when a criterion has been overtaken",
    ):
        assert not _says(path, stale), (
            f"{path.relative_to(ROOT)} still limits an accepted row to an "
            f"overtaken criterion: {stale!r}"
        )


def test_the_proof_run_records_unmet_rows_as_today_and_never_accepts():
    for needle in (
        "You never write an accepted row on your own judgment",
        "record `Not observed.` or `Not met.` with what you saw, and let the gate hold.",
    ):
        assert _says(PROOF_BRIEF, needle), (
            f"briefs/proof.md no longer tells the proof run {needle!r}"
        )


def test_the_critic_is_told_which_acceptance_to_send_back_and_which_not():
    for needle in (
        # A not-seen row accepted anyway is a finding.
        "An acceptance of a row whose thing was not seen is a blocking finding",
        # A shown-but-not-as-worded row is not held back for lacking the word.
        "Never refuse a row shown working but not as worded for failing to say "
        "\"overtaken\"",
        # An older record's accepted row: the critic follows the link the gate
        # only shape-checks.
        "open the linked decision",
    ):
        assert _says(CRITIC_BRIEF, needle), (
            f"briefs/critic.md does not carry the critic's half of the rule: {needle!r}"
        )


#: The critic's acceptance line, its placeholders as the critic brief writes
#: them (DRE-7949, DRE-7954).
ACCEPTANCE_LINE = (f"{proof_record.ACCEPTANCE_MARK} “<criterion cut to 70>” — seen: "
                   "“<quote>” — differs: <what> — reason: <sentence>")


def _code_lines(path: Path) -> list:
    return [line.strip() for line in _read(path).splitlines()
            if line.startswith("    ") and line.strip()]


def test_the_critic_brief_carries_the_acceptance_line():
    assert ACCEPTANCE_LINE in _code_lines(CRITIC_BRIEF), (
        f"briefs/critic.md does not quote the acceptance line `{ACCEPTANCE_LINE}` on its "
        f"own line — `proof_record.ACCEPTANCE_MARK` is {proof_record.ACCEPTANCE_MARK!r}")


def test_the_critic_brief_quotes_the_row_it_accepts():
    row = f"{proof_record.WORDING_OPENING} seen “<quote>” — differs: <what>"
    assert row in _code_lines(CRITIC_BRIEF), (
        f"briefs/critic.md does not quote the wording-only row `{row}`")


def test_the_acceptance_line_filled_is_one_the_gate_reads():
    criterion = "The sweep posts a proof-release: line naming the ready release"
    line = (ACCEPTANCE_LINE.replace("<criterion cut to 70>", criterion)
            .replace("<quote>", "release v41 ready").replace("<what>", "the line's prefix")
            .replace("<sentence>", "the line was seen with another prefix."))
    assert proof_record.acceptances(line) == [(criterion, "release v41 ready")]


def test_a_not_observed_row_is_never_accepted():
    for needle in (
        "You never write one for a `Not observed.` row",
        "or for a row that is part shown and part not seen",
    ):
        assert _says(CRITIC_BRIEF, needle), (
            f"briefs/critic.md does not say {needle!r}")


def test_the_critic_section_is_rewritten_around_the_acceptance_line():
    text = _read(CRITIC_BRIEF)
    heading = "## An accepted row in a proof record (DRE-6244, DRE-6773, DRE-7949)"
    assert heading in text, f"briefs/critic.md has no `{heading}` section"
    assert "## An accepted row in a proof record (DRE-6244, DRE-6773)\n" not in text


def test_the_critic_brief_points_at_the_standard_and_the_ruling():
    assert _says(CRITIC_BRIEF, "DRE-6773")
    assert _says(CRITIC_BRIEF, "2026-10-10")


class TestTheReadersFormIsUnchanged:
    """The rule widened in prose; the reader did not. Opening words and a
    linked decision in the same cell remain the whole of what passes."""

    LINK = ("https://linear.app/dreadnoughtfoundry/issue/DRE-6042/"
            "proof-a-proof-run#comment-abc123")

    def test_the_opening_is_still_accepted_by_operator_decision(self):
        assert proof_record.ACCEPTED_OPENING == "accepted by operator decision"

    def test_a_shown_but_not_as_worded_acceptance_still_needs_its_link(self):
        reason = ("seen: “job started at 14:02” — differs: the card quotes a "
                  "different log line")
        assert proof_record.row_accepted(
            f"ACCEPTED by operator decision — {reason} ({self.LINK})")
        assert not proof_record.row_accepted(
            f"ACCEPTED by operator decision — {reason}")
