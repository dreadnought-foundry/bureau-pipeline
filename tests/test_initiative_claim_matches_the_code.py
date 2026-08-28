"""What a missing `initiative:*` label actually costs, in the code's own words
(DRE-2681).

Four docstrings and comments said the same wrong thing: that the reconcile
dependency-gate scopes promotion to an initiative, so a child without an
`initiative:*` label never auto-promotes and stalls in Backlog. The word
`initiative` appears NOWHERE in `reconcile.py` — no gate reads it, and a missing
label does not stop promotion. They were wrong about the mechanism they
document, which is the expensive kind of wrong: it sends the next reader to a
gate that does not exist.

Two real things break without the label, and they are what the docs must say:

  1. Repo inference. `validate_card.infer_repo` step 2a uses the initiative
     label as the FIRST route to a repo for a card carrying no `repo:` label.
     Without it, inference falls through to the Linear project name, and if that
     fails too the card is bounced.
  2. The create seam. `child_problems` calls
     `missing(..., require_initiative=True)` and `linear_ops` enforces the same
     before creating a child — so a planner-created child missing the label is
     refused at creation.

Both halves are pinned here: the premise (reconcile really does not read the
label) and the corrected wording. If a future change makes reconcile
initiative-aware, the first test fails and the docs get revisited — which is the
point.

Run: cd bureau-pipeline && python3 -m pytest tests/test_initiative_claim_matches_the_code.py -v
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
os.environ.setdefault("LINEAR_API_KEY", "test-key")

import validate_card  # noqa: E402

# The exact attribution all four used. `reconcile.py`'s own mentions of "the
# dependency gate" never name reconcile — this phrase only ever introduced the
# false claim, so its absence is the check.
_FALSE_ATTRIBUTION = re.compile(r"reconcile dependency[- ]gate", re.IGNORECASE)


def _flat(name: str) -> str:
    return re.sub(r"\s+", " ", (SCRIPTS / name).read_text())


def test_reconcile_never_reads_the_initiative_label():
    """The premise. Every claim about the reconcile dependency-gate scoping
    promotion to an initiative rests on this being false."""
    assert "initiative" not in (SCRIPTS / "reconcile.py").read_text().lower()


def test_no_script_attributes_the_initiative_contract_to_the_reconcile_gate():
    offenders = sorted(
        p.name for p in SCRIPTS.glob("*.py") if _FALSE_ATTRIBUTION.search(_flat(p.name))
    )
    assert offenders == []


def test_missing_names_the_create_seam_as_the_real_consequence():
    doc = re.sub(r"\s+", " ", validate_card.missing.__doc__ or "")
    assert "require_initiative" in doc
    assert "refused at creation" in doc


def test_child_problems_names_the_create_seam_as_the_real_consequence():
    doc = re.sub(r"\s+", " ", validate_card.child_problems.__doc__ or "")
    assert "refused at creation" in doc


def test_parent_inherited_labels_names_repo_inference_and_the_create_seam():
    body = _flat("linear_ops.py")
    doc = body.split("def parent_inherited_labels", 1)[1].split('"""')[1]
    assert "infer_repo" in doc
    assert "require_initiative" in doc
