"""A PROOF enters Hand-work before its epic closes, and the standard says so (DRE-8233).

DRE-7956's row 2 asked for the proof to enter Hand-work after DRE-7944 entered
Done. It never could: a PROOF is blocked by every build card, so the merge of
the last one promotes it (`--promote-only` in `.github/workflows/linear-sync.yml`),
and the sweep closes the epic on its next pass (DRE-7948). The dispatcher
accepts either epic state on purpose (`proof_dispatch.EPIC_ACTIVE`). No product
change is owed; the sentence in the standard that read as if promotion waits
for the close is what misled the row.

These tests hold the DRE-7948 paragraph of `standards/card-quality.md` to the
corrected statement, the rule a PROOF criterion follows, and the replacement
row quoted word for word. Phrases are matched the way
`test_no_person_a_proof_waits_on.py` matches them: lowercased, backticks
removed, whitespace collapsed.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
# `reconcile` reads `os.environ["REPO"]` at import, and `proof_dispatch`
# imports it; `test_no_person_a_proof_waits_on.py` sets it the same way.
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import proof_dispatch  # noqa: E402

STANDARD = ROOT / "standards" / "card-quality.md"
RECORD = ROOT / "docs" / "epic-closes-at-build-done-proof-2026-10.md"
OPENING = "An epic closes once its last build card is Done"

#: The row DRE-7956 should have carried, as DRE-8233 words it.
REPLACEMENT_ROW = (
    "This card's Linear history shows it entering Hand-work no earlier than the "
    "last of the epic's build cards entering Done, and the epic's history shows "
    "it entering Done while this card was not Done (all three times quoted in "
    "PT). Its first `🔬 proof-run: dispatched` receipt is later than its "
    "Hand-work entry, and the `proof-dispatch:` line naming this card "
    "`dispatched` is quoted from that sweep run's log, with the run's id."
)

#: Row 2 as DRE-7956 worded it — the merged record keeps it as history.
DRE_7956_ROW = "This card's Linear history shows it entering Hand-work after DRE-7944 entered Done."


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("`", "")).strip().lower()


def _paragraph() -> str:
    """The DRE-7948 paragraph of the standard, flattened."""
    text = STANDARD.read_text(encoding="utf-8")
    found = [p for p in re.split(r"\n\s*\n", text) if OPENING in p]
    assert len(found) == 1, f"standards/card-quality.md has {len(found)} paragraphs opening {OPENING!r}"
    return _flat(found[0])


def test_a_proof_enters_hand_work_on_its_last_build_cards_merge():
    para = _paragraph()
    assert "a proof is promoted to hand-work by the merge of its last build card" in para
    assert "before the sweep's next pass closes the epic" in para
    assert "enters hand-work while the epic is still in progress" in para


def test_the_proof_run_may_be_dispatched_under_either_epic_state():
    para = _paragraph()
    assert "dispatched under either epic state" in para
    for state in proof_dispatch.EPIC_ACTIVE:
        assert _flat(state) in para, f"the paragraph does not name the {state!r} epic state"
    assert "epic_active" in para, "the paragraph does not point at EPIC_ACTIVE"


def test_a_proof_criterion_never_asks_for_hand_work_after_the_epic_is_done():
    para = _paragraph()
    assert ("a proof criterion never asks for the proof to enter hand-work, "
            "or to be dispatched, after its epic entered done") in para
    assert "dre-7956" in para and "dre-8233" in para


def test_the_replacement_row_is_quoted_word_for_word():
    assert _flat(REPLACEMENT_ROW) in _paragraph()


def test_the_paragraph_no_longer_says_promotion_waits_for_the_close():
    para = _paragraph()
    assert "are still promoted and dispatched" not in para
    assert "under the closed epic, are" not in para


def test_dre_7956s_record_is_history_and_keeps_its_row():
    assert DRE_7956_ROW in RECORD.read_text(encoding="utf-8")
