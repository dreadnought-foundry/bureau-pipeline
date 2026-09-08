"""What a missing `initiative:*` label actually costs, in the code's own words
(DRE-2681, narrowed by DRE-2874).

Four docstrings and comments said the same wrong thing: that the reconcile
dependency-gate scopes promotion to an initiative, so a child without an
`initiative:*` label never auto-promotes and stalls in Backlog. The word
`initiative` appears NOWHERE in `reconcile.py` — no gate reads it, and a missing
label does not stop promotion. They were wrong about the mechanism they
document, which is the expensive kind of wrong: it sends the next reader to a
gate that does not exist.

DRE-2681 corrected them to name two real consequences. DRE-2874 deleted one of
the two — `missing(..., require_initiative=True)`, the create-seam refusal —
because it was the one path that RAISED rather than falling back, and it would
have refused every planner-created child the moment the `initiative:*` labels
were culled. Exactly ONE consequence is left, and it is what the docs must say:

  Repo inference. `validate_card.infer_repo` uses the initiative label as the
  ONLY route to a repo for a card carrying no `repo:` label. Without it the
  card is bounced — since DRE-2874 there is no project-name-prefix fallback
  behind it, because a product's identity is not a substring of a display name
  anyone can rename.

Both halves are pinned here: the premise (reconcile really does not read the
label, and what it reads instead) and the corrected wording. If a future change
makes reconcile initiative-aware, the first test fails and the docs get
revisited — which is the point.

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


def _linear_ops_doc(func: str) -> str:
    return _flat("linear_ops.py").split(f"def {func}", 1)[1].split('"""')[1]


def test_reconcile_never_reads_the_initiative_label():
    """The premise. Every claim about the reconcile dependency-gate scoping
    promotion to an initiative rests on this being false."""
    assert "initiative" not in (SCRIPTS / "reconcile.py").read_text().lower()


def test_no_script_attributes_the_initiative_contract_to_the_reconcile_gate():
    offenders = sorted(
        p.name for p in SCRIPTS.glob("*.py") if _FALSE_ATTRIBUTION.search(_flat(p.name))
    )
    assert offenders == []


def test_no_script_still_requires_an_initiative_label_to_create_a_card():
    """DRE-2874. The switch is gone from every script, not merely unset at one
    call site — a live `require_initiative` is a refusal waiting to be re-armed
    the day the labels are culled."""
    offenders = sorted(
        p.name for p in SCRIPTS.glob("*.py") if "require_initiative" in _flat(p.name)
    )
    assert offenders == []


def test_no_script_infers_a_repo_from_a_project_name_prefix():
    """DRE-2874. No code path in this repo may route a card off the display name
    of the Linear project it happens to sit in. Checked as identifiers, not
    prose — the deletion is allowed to be explained, only not re-implemented."""
    offenders = sorted(
        p.name for p in SCRIPTS.glob("*.py")
        if re.search(r"_PROJECT_PREFIX|project_name", _flat(p.name))
    )
    assert offenders == []


# --- the three docstrings the correction is owed to (DRE-2874) ---------------
#
# Each one previously told the reader that a missing `initiative:*` label stalls
# a card in Backlog. Each must now say what `reconcile.py` ACTUALLY reads to
# promote — the card's `blockedBy` relations — and must not resurrect either
# deleted mechanism.

_CORRECTED = ("blockedBy",)
_FORBIDDEN = ("require_initiative", "refused at creation", "reconcile dependency")


def _assert_corrected(doc: str, where: str) -> None:
    flat = re.sub(r"\s+", " ", doc)
    for want in _CORRECTED:
        assert want in flat, f"{where} does not say what reconcile reads ({want})"
    for banned in _FORBIDDEN:
        assert banned.lower() not in flat.lower(), f"{where} still claims {banned!r}"


def test_missing_docstring_is_corrected():
    _assert_corrected(validate_card.missing.__doc__ or "", "validate_card.missing")


def test_child_problems_docstring_is_corrected():
    _assert_corrected(
        validate_card.child_problems.__doc__ or "", "validate_card.child_problems"
    )


def test_parent_inherited_labels_docstring_is_corrected():
    doc = _linear_ops_doc("parent_inherited_labels")
    _assert_corrected(doc, "linear_ops.parent_inherited_labels")
    # The one real consequence that survives must still be named.
    assert "infer_repo" in doc
