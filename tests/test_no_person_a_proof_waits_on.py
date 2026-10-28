"""No person a proof waits on, in the three proof-facing documents (DRE-7955, epic DRE-7944).

A proof row, a proof hold and a proof card wait on nobody but the pipeline,
and on the CEO only for a business decision of his. DRE-7773 took "the CEO
reads and closes this card" out of the documents. DRE-7954 took the operator
out of the proof run's own brief. This card finishes the job for the planner's
brief and the card standard, which state the new rules once:

- an epic closes on the sweep pass after its last build card is Done, and its
  open PROOF and OPERATOR cards carry on under it (DRE-7948);
- the record's critic accepts a wording-only row in its verdict, and a person
  never does (DRE-7949);
- a record whose unmet rows all carry a cause, or are history rows, closes its
  card with one fix card in Intake (DRE-7952);
- a press only a person can make after merge is an OPERATOR card the PROOF is
  blocked by, never a row, and the plan critic refuses the row (DRE-7950).

The phrases are matched the way DRE-7954's test matches them: lowercased,
backticks removed, whitespace collapsed. Before this card the planner's brief
and the standard said them; the proof brief had stopped with DRE-7954.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
# `reconcile` reads `os.environ["REPO"]` at import, and `proof_dispatch`
# imports it; `test_proof_press_rule.py` sets it the same way.
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import epic_cap  # noqa: E402
import plan_proof_reach  # noqa: E402
import proof_dispatch  # noqa: E402
import proof_gap  # noqa: E402
import proof_record  # noqa: E402

PROOF = ROOT / "briefs" / "proof.md"
PLANNER = ROOT / "briefs" / "planner.md"
STANDARD = ROOT / "standards" / "card-quality.md"
DOCUMENTS = (PROOF, PLANNER, STANDARD)

PROOF_CARD_HEADING = "## Every epic ends with a proof card"
EPICS_HEADING = "## Epics"

#: The phrases no proof-facing document may say — identical to DRE-7954's list
#: (`tests/test_proof_press_rule.py`'s `NO_PERSON_WAIT`).
NO_PERSON_WAIT = (
    "waiting for the operator's press",
    "accepted by operator decision",
    "put to the operator",
    "held for the operator",
    "the operator writes the decision",
    "for the operator's decision",
    "holds the card for the operator",
    "the operator runs a proof",
)

#: The CEO's row, read from the plan critic's own list.
CEO_ROW = plan_proof_reach.PERSON_ROWS[1]
BUSINESS = "business decision"

#: Each name the standard states, the card that owns it, and the module that
#: holds it — read there, never restated as code here.
OWNERS = (
    ("DRE-7948", "epic_cap.follow_on", epic_cap, "follow_on"),
    ("DRE-7949", "ACCEPTANCE_MARK", proof_record, "ACCEPTANCE_MARK"),
    ("DRE-7949", "WORDING_OPENING", proof_record, "WORDING_OPENING"),
    ("DRE-7952", "proof_gap.file_fix_card", proof_gap, "file_fix_card"),
    ("DRE-7950", "FINDING_PERSON_PRESS", plan_proof_reach, "FINDING_PERSON_PRESS"),
    ("DRE-7951", "RECHECK_REASON", proof_dispatch, "RECHECK_REASON"),
    ("DRE-7951", "LAST_TIMED_REASON", proof_dispatch, "LAST_TIMED_REASON"),
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _flat(text: str) -> str:
    return " ".join(text.split())


def _plain(text: str) -> str:
    """Lowercased, backticks removed, whitespace collapsed (DRE-7954)."""
    return _flat(text.replace("`", "")).lower()


def _section(path: Path, heading: str) -> str:
    """From the line opening `heading` to the next `## ` heading of `path`."""
    found = re.search(rf"^{re.escape(heading)}.*?(?=^## )", _read(path), re.S | re.M)
    assert found, f"{path.name} has no `{heading}` section"
    return found.group(0)


def _paragraphs(text: str) -> list[str]:
    """The text cut at blank lines, each paragraph flattened."""
    return [_flat(p) for p in re.split(r"\n\s*\n", text) if p.strip()]


def _says_together(text: str, *needles: str) -> bool:
    """One paragraph of `text` carries every needle."""
    return any(all(n in p for n in needles) for p in _paragraphs(text))


def _name(path: Path) -> str:
    return str(path.relative_to(ROOT))


def test_the_list_is_dre_7954s():
    import test_proof_press_rule
    assert NO_PERSON_WAIT == test_proof_press_rule.NO_PERSON_WAIT


@pytest.mark.parametrize("path", DOCUMENTS, ids=_name)
@pytest.mark.parametrize("phrase", NO_PERSON_WAIT)
def test_no_document_names_a_person_a_proof_waits_on(path, phrase):
    count = _plain(_read(path)).count(phrase)
    assert count == 0, (
        f"{_name(path)} still says {phrase!r} ({count}×) — a proof row, hold or card waits "
        "on nobody but the pipeline, and on the CEO only for a business decision of his")


@pytest.mark.parametrize("path", (STANDARD, PLANNER), ids=_name)
def test_the_ceos_press_stays_for_his_business_decisions(path):
    text = _read(path)
    assert CEO_ROW in _flat(text), f"{_name(path)} no longer quotes `{CEO_ROW}`"
    assert _says_together(_section(path, PROOF_CARD_HEADING), CEO_ROW, BUSINESS), (
        f"{_name(path)}'s proof section no longer scopes `{CEO_ROW}` to a {BUSINESS} of his")
    for para in _paragraphs(text):
        if CEO_ROW not in para:
            continue
        assert BUSINESS in para or "Step that needs the CEO:" in para, (
            f"{_name(path)} quotes `{CEO_ROW}` in a paragraph about neither a {BUSINESS} "
            f"of his nor the plan's `Step that needs the CEO:` check: {para[:160]!r}")


@pytest.mark.parametrize("card,name,module,attr", OWNERS, ids=[o[1] for o in OWNERS])
def test_the_standard_names_each_owner_beside_its_card(card, name, module, attr):
    assert hasattr(module, attr), f"{module.__name__} no longer defines {attr}"
    text = _read(STANDARD)
    assert card in text, f"standards/card-quality.md does not name {card}"
    assert _says_together(text, f"`{name}`", card), (
        f"standards/card-quality.md does not name `{name}` beside its owner {card}")


class TestTheStandard:
    """`standards/card-quality.md` — the one full statement."""

    def test_an_epic_closes_once_its_build_cards_are_done(self):
        epics = _section(STANDARD, EPICS_HEADING)
        assert _says_together(epics, "DRE-7948", "last build card is Done",
                              "`epic_cap.follow_on`"), (
            "the standard's Epics section does not say an epic closes on the pass "
            "after its last build card is Done (DRE-7948)")
        for needle in ("PROOF", "OPERATOR", "promoted and dispatched", "close on their own"):
            assert _says_together(epics, "DRE-7948", needle), (
                f"the standard's epic close does not say {needle!r} of the follow-on cards")
        assert _says_together(epics, "Roll-up", "every child is Done"), (
            "the standard no longer says a roll-up parent closes when every child is Done")

    def test_the_records_critic_accepts_a_wording_only_row(self):
        proof = _section(STANDARD, PROOF_CARD_HEADING)
        assert _says_together(proof, proof_record.WORDING_OPENING, "DRE-7949"), (
            f"the standard does not name the `{proof_record.WORDING_OPENING}` row")
        assert _says_together(proof, proof_record.ACCEPTANCE_MARK, "record's critic"), (
            f"the standard does not say the record's critic accepts the row with a "
            f"`{proof_record.ACCEPTANCE_MARK}` line")
        assert _says_together(proof, "a person never does"), (
            "the standard does not say a person never accepts a row")

    def test_a_gap_closes_with_one_fix_card_in_intake(self):
        proof = _section(STANDARD, PROOF_CARD_HEADING)
        assert _says_together(proof, "DRE-7952", "one fix card", "`Intake`",
                              "`proof_gap.file_fix_card`"), (
            "the standard does not say a record of cause or history rows closes its "
            "card with one fix card in Intake (DRE-7952)")
        assert "DRE-6837" not in proof, "the standard still puts a gap record to a person"

    def test_an_after_merge_press_is_an_operator_card_the_proof_is_blocked_by(self):
        proof = _section(STANDARD, PROOF_CARD_HEADING)
        assert _says_together(proof, "OPERATOR card", "blocked by", "never a row",
                              "DRE-7950"), (
            "the standard does not say a press only a person can make after merge is an "
            "OPERATOR card the PROOF is blocked by, never a row (DRE-7950)")
        assert _says_together(proof, "`FINDING_PERSON_PRESS`", "refuses"), (
            "the standard does not say the plan critic refuses the person's row")

    def test_which_row_a_press_gets_keeps_two_rows(self):
        proof = _section(STANDARD, PROOF_CARD_HEADING)
        which = next((p for p in _paragraphs(proof)
                      if p.startswith("**Which row a press gets.**")), None)
        assert which, "the standard has no `Which row a press gets` paragraph"
        assert "`Sandbox press:`" in which and f"`{CEO_ROW}`" in which
        assert "operator's row" not in which, "the operator's row is still a row a press gets"

    def test_the_pipeline_dispatches_the_proof_and_the_holds_name_no_person(self):
        proof = _section(STANDARD, PROOF_CARD_HEADING)
        assert _says_together(proof, "2026-09-30", "pipeline dispatches",
                              "as soon as its build cards finish"), (
            "the standard does not say the pipeline dispatches a proof as soon as its "
            "build cards finish")
        assert _says_together(proof, "`🔬 proof-waiting`", "never a person"), (
            "the standard does not say the proof's hold waits on no person")


class TestThePlannerBrief:
    """`briefs/planner.md` — how the planner writes the proof card."""

    def setup_method(self):
        self.proof = _section(PLANNER, PROOF_CARD_HEADING)

    def test_an_after_merge_step_is_its_own_operator_card(self):
        assert _says_together(self.proof, "OPERATOR card", "`needs-human`", "`no-code`",
                              "`agent:devops`"), (
            "the planner brief does not write an after-merge person-step as its own "
            "OPERATOR card labeled needs-human, no-code and agent:devops")
        assert _says_together(self.proof, "`**Blocked by:**`", "reading of its result"), (
            "the planner brief does not block the PROOF on that card and read its result")

    def test_never_a_waiting_row_on_a_person(self):
        assert _says_together(self.proof, "Never write a waiting row on a person"), (
            "the planner brief does not forbid a waiting row on a person")

    def test_every_waiting_row_is_stamped(self):
        assert _says_together(self.proof, "waiting row", "stamped", "[not-before="), (
            "the planner brief does not say every waiting row the run writes is stamped")

    def test_the_operators_row_paragraph_is_gone(self):
        assert "DRE-6663" not in self.proof, (
            "the planner brief still carries the DRE-6638/DRE-6663 operator's-row paragraph")

    def test_the_held_repo_check_keeps_the_ceos_press(self):
        held = _section(PLANNER, "## A held repo: lifting the hold is a step that needs the CEO")
        assert _says_together(held, "DRE-7523", f"`{CEO_ROW}`"), (
            f"the planner brief's DRE-7523 paragraph no longer names `{CEO_ROW}`")
