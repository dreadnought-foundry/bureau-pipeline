"""A proof's log read is a quoted phrase, and an empty read needs a control (DRE-6778).

A proof card told its run to read the console's log with
`aws logs filter-log-events --filter-pattern system-alert`. Written bare, the
pattern does not match the phrase: on 2026-10-10, for DRE-6597, the console
backend's application log group returned 0 events for 11:45 to 11:55 PT where
the quoted form, `'"system-alert"'`, returned 2 — and 0 against 6 for 11:35 to
12:00. A run following that card would have recorded "none was raised" on an
empty list that was wrong.

Three documents now carry the rule, and these tests hold them to ONE wording:
`briefs/proof.md` (the run that reads the log), `briefs/planner.md` (the
writer of the card) and `standards/plan-critic.md` (the two plan critics, which
read PROOF cards before the CEO does). A document that drifts from the others
fails here, naming the sentence it lost.

No script reads proof-card criteria for an unobservable shape yet
(`scripts/proof_and_demo.py` checks the card's label, verdict and closing line
only), so this card adds no mechanical check — the last test pins that each
document actually reaches the reader it is written for.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import assemble_context  # noqa: E402

PROOF_BRIEF = ROOT / "briefs" / "proof.md"
PLANNER_BRIEF = ROOT / "briefs" / "planner.md"
PLAN_CRITIC = ROOT / "standards" / "plan-critic.md"
DOCUMENTS = (PROOF_BRIEF, PLANNER_BRIEF, PLAN_CRITIC)

#: The rule, sentence by sentence, as all three documents must carry it. Each
#: document wraps them in its own reader's words, never in place of them.
RULE = (
    # The quoted-phrase rule.
    "A CloudWatch Logs filter pattern for a literal phrase is written as a "
    "quoted phrase, `'\"system-alert\"'`, never bare.",
    "A pattern containing a hyphen, colon or other non-alphanumeric character "
    "is never written bare.",
    # The positive control.
    "An empty log read is recorded as none only after the same pattern form "
    "has found one known-present line in a wider window — a positive control "
    "— and is otherwise recorded as `Not observed.`",
    # Where it is caught.
    "A PROOF card that prescribes a bare log filter pattern containing such a "
    "character is sent back when the plan is written.",
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
@pytest.mark.parametrize("sentence", RULE, ids=range(len(RULE)))
def test_every_document_carries_the_rule_word_for_word(path, sentence):
    assert _says(path, sentence), (
        f"{path.relative_to(ROOT)} does not carry the log filter rule "
        f"sentence {sentence!r} (DRE-6778) — the proof brief, the planner "
        f"brief and the plan-critic standard must read one wording"
    )


@pytest.mark.parametrize("path", DOCUMENTS, ids=lambda p: str(p.relative_to(ROOT)))
def test_every_document_names_the_card(path):
    assert _says(path, "DRE-6778"), (
        f"{path.relative_to(ROOT)} carries the log filter rule without naming "
        f"the card that set it"
    )


def test_the_proof_brief_carries_the_2026_10_10_measurement_as_its_example():
    body = _read(PROOF_BRIEF)
    assert _says(PROOF_BRIEF, "2026-10-10"), "the measurement's date is missing"
    assert "DRE-6597" in body, "the proof the measurement was taken for is missing"
    # Both windows, both forms: the bare count beside the quoted one.
    for window, bare, quoted in (
        ("11:45 to 11:55", "0 events", "2 events"),
        ("11:35 to 12:00", "0 events", "6 events"),
    ):
        row = re.search(
            rf"\|\s*{re.escape(window)}[^|\n]*\|\s*{re.escape(bare)}\s*\|"
            rf"\s*{re.escape(quoted)}\s*\|",
            body,
        )
        assert row, (
            f"briefs/proof.md has no measurement row for {window}: "
            f"{bare} bare against {quoted} quoted"
        )
    assert "--filter-pattern system-alert" in body, "the bare form is not shown"
    assert "--filter-pattern '\"system-alert\"'" in body, "the quoted form is not shown"


def test_the_plan_critic_rule_binds_both_critics():
    """A PROOF card is an epic child, so it reaches the first and the second
    critic — never the one-off stage alone. The rule must say so."""
    assert _says(PLAN_CRITIC, "Both critics read a PROOF card's log filter pattern"), (
        "standards/plan-critic.md must say the log filter rule is read by both "
        "the first and the second critic"
    )


def test_each_document_reaches_the_reader_it_is_written_for():
    """A rule in a document the reader is never handed is a rule nobody reads."""
    assert assemble_context.ROLE_BRIEF["proof"] == "proof.md"
    assert assemble_context.ROLE_BRIEF["planner"] == "planner.md"
    for role in ("plan-critic-pre", "plan-critic-post"):
        assert "plan-critic.md" in assemble_context.ROLE_STANDARDS[role], (
            f"{role} is not handed standards/plan-critic.md, so the log filter "
            f"rule there never reaches it"
        )
