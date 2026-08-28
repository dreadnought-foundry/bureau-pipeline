"""Structural repair resolves parents before children (DRE-2681).

A repair pass that walks the Backlog top to bottom fixes the children whose
parents already carry a label and silently fails the ones whose parents are
themselves unrepaired — even when the parent was one row further down and
repairable. On the 2026-08-23 census, 66 of the Backlog's cards carried no
`initiative:*` label: 26 could inherit from a parent and 40 could not — 26 had
no parent at all, and 14 sat under six unlabelled parents, four of which are
themselves Done or Canceled. "Fix the parent first" is not always available, so
the report has to separate the two cases rather than lump them as "failed".

What a missing `initiative:*` label actually costs is narrow, and this pass is
scoped to it: `validate_card.infer_repo` step 2a uses it as the first route to a
repo, and `missing(..., require_initiative=True)` refuses to CREATE a child
without it. `reconcile.py` never reads the label at all — see
tests/test_initiative_claim_matches_the_code.py.

Repair stays deterministic: labels, inheritance, slug validity. Nothing needing
judgment is repaired (D1, approved 2026-08-23).

Run: cd bureau-pipeline && python3 -m pytest tests/test_structural_repair.py -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")

import structural_repair  # noqa: E402


def _card(ident: str, labels: list[str], parent: dict | None = None,
          state: str = "Backlog") -> dict:
    return {
        "identifier": ident,
        "state": {"name": state},
        "labels": {"nodes": [{"name": n} for n in labels]},
        "parent": parent,
    }


def _parent(ident: str, labels: list[str], state: str = "In Progress") -> dict:
    return {
        "identifier": ident,
        "state": {"name": state},
        "labels": {"nodes": [{"name": n} for n in labels]},
    }


# --------------------------------------------------------------------------
# Ordering
# --------------------------------------------------------------------------
def test_parents_first_puts_a_parent_ahead_of_its_child():
    child = _card("DRE-101", [], parent=_parent("DRE-100", []))
    parent = _card("DRE-100", [], parent=None)
    order = [c["identifier"] for c in structural_repair.parents_first([child, parent])]
    assert order == ["DRE-100", "DRE-101"]


def test_parents_first_keeps_cards_whose_parent_is_absent():
    """Most parents are epics outside the Backlog set — those cards are roots
    here and must not be dropped."""
    a = _card("DRE-9", [], parent=_parent("DRE-1", ["initiative:bureau"]))
    b = _card("DRE-8", [], parent=None)
    order = [c["identifier"] for c in structural_repair.parents_first([a, b])]
    assert sorted(order) == ["DRE-8", "DRE-9"]
    assert len(order) == 2


def test_parents_first_survives_a_parent_cycle():
    """Linear cannot make one, but a pass that hangs the sweep on bad data is a
    worse failure than one that orders it oddly."""
    a = _card("DRE-1", [], parent=_parent("DRE-2", []))
    b = _card("DRE-2", [], parent=_parent("DRE-1", []))
    order = [c["identifier"] for c in structural_repair.parents_first([a, b])]
    assert sorted(order) == ["DRE-1", "DRE-2"]


# --------------------------------------------------------------------------
# Inheritance — the parents-first payoff
# --------------------------------------------------------------------------
def test_a_child_inherits_from_a_parent_repaired_in_the_SAME_pass():
    """The whole point. The census order lists the child FIRST, and its parent
    is unlabelled at read time — a top-to-bottom pass reports the child as
    unrepairable. Parents-first repairs DRE-100 from its own parent, then the
    child inherits the repaired value.

    MUTATION CHECK: drop parents_first from plan_repairs and DRE-101 lands in
    `gaps` as parent-unlabelled instead of being repaired.
    """
    child = _card("DRE-101", ["agent:engineer"], parent=_parent("DRE-100", []))
    mid = _card("DRE-100", ["agent:planner"],
                parent=_parent("DRE-10", ["initiative:bureau"]))
    repairs, gaps = structural_repair.plan_repairs([child, mid])
    assert gaps == []
    by_id = {r["identifier"]: r for r in repairs}
    assert by_id["DRE-100"]["label"] == "initiative:bureau"
    assert by_id["DRE-100"]["source"] == "DRE-10"
    assert by_id["DRE-101"]["label"] == "initiative:bureau"
    assert by_id["DRE-101"]["source"] == "DRE-100"


def test_a_card_that_already_has_an_initiative_is_left_alone():
    card = _card("DRE-1", ["initiative:deltasolv"],
                 parent=_parent("DRE-0", ["initiative:bureau"]))
    repairs, gaps = structural_repair.plan_repairs([card])
    assert repairs == []
    assert gaps == []


def test_inheritance_carries_the_parents_exact_label():
    card = _card("DRE-1", [], parent=_parent("DRE-0", ["initiative:deltasolv"]))
    repairs, _ = structural_repair.plan_repairs([card])
    assert repairs[0]["label"] == "initiative:deltasolv"


# --------------------------------------------------------------------------
# The two unrepairable cases, kept apart
# --------------------------------------------------------------------------
def test_a_parentless_card_is_reported_as_having_no_parent():
    card = _card("DRE-1", [], parent=None)
    repairs, gaps = structural_repair.plan_repairs([card])
    assert repairs == []
    assert [g["kind"] for g in gaps] == [structural_repair.NO_PARENT]
    assert "no parent" in gaps[0]["detail"]


def test_an_unlabelled_LIVE_parent_reads_as_fixable_first():
    """DRE-2459's shape: the parent carries no label but is still open, so
    labelling the parent IS the fix."""
    card = _card("DRE-1", [], parent=_parent("DRE-2459", [], state="In Progress"))
    _, gaps = structural_repair.plan_repairs([card])
    assert gaps[0]["kind"] == structural_repair.PARENT_UNLABELLED
    assert "DRE-2459" in gaps[0]["detail"]
    assert "carries no initiative" in gaps[0]["detail"]


def test_a_terminal_parent_is_reported_as_UNFIXABLE_first():
    """DRE-2441's shape: four of the six unlabelled parents are Done or
    Canceled, so "fix the parent first" is not available and saying so is the
    whole difference between the two lines of the report."""
    for state in ("Done", "Canceled"):
        card = _card("DRE-1", [], parent=_parent("DRE-2441", [], state=state))
        _, gaps = structural_repair.plan_repairs([card])
        assert gaps[0]["kind"] == structural_repair.PARENT_TERMINAL, state
        assert "DRE-2441" in gaps[0]["detail"]
        assert state in gaps[0]["detail"]
        assert "cannot be fixed first" in gaps[0]["detail"]


def test_the_two_unrepairable_classes_are_never_the_same_line():
    live = _card("DRE-1", [], parent=_parent("DRE-2459", [], state="In Progress"))
    dead = _card("DRE-2", [], parent=_parent("DRE-2441", [], state="Done"))
    _, gaps = structural_repair.plan_repairs([live, dead])
    details = {g["identifier"]: g["detail"] for g in gaps}
    assert details["DRE-1"] != details["DRE-2"]
    assert len({g["kind"] for g in gaps}) == 2


# --------------------------------------------------------------------------
# Slug validity — reported, never guessed
# --------------------------------------------------------------------------
def test_an_unknown_repo_slug_is_reported_and_not_rewritten():
    card = _card("DRE-1", ["initiative:bureau", "repo:nonsense"], parent=None)
    repairs, gaps = structural_repair.plan_repairs([card])
    assert repairs == []  # a wrong-repo guess needs judgment — D1 forbids it
    assert [g["kind"] for g in gaps] == [structural_repair.UNKNOWN_REPO]
    assert "repo:nonsense" in gaps[0]["detail"]


def test_a_known_repo_slug_is_not_reported():
    card = _card("DRE-1", ["initiative:bureau", "repo:agent-bureau"], parent=None)
    _, gaps = structural_repair.plan_repairs([card])
    assert gaps == []


# --------------------------------------------------------------------------
# The report — including the beyond-row-100 proof
# --------------------------------------------------------------------------
def _census(total: int, repairable_rows: tuple[int, ...] = ()) -> list[dict]:
    """`total` Backlog cards in census (fetch) order. Every row is already
    labelled except the rows named, which can inherit from a labelled parent."""
    cards = []
    for n in range(1, total + 1):
        if n in repairable_rows:
            cards.append(_card(f"DRE-{n}", [], parent=_parent("DRE-0", ["initiative:bureau"])))
        else:
            cards.append(_card(f"DRE-{n}", ["initiative:bureau"], parent=None))
    return cards


def test_the_report_names_a_repaired_card_beyond_row_100():
    """The production proof this phase is accepted against: a 150-row census
    whose repairable card is the 150th."""
    cards = _census(150, repairable_rows=(150,))
    repairs, gaps = structural_repair.plan_repairs(cards)
    report = structural_repair.format_report(cards, repairs, gaps)
    assert repairs[0]["identifier"] == "DRE-150"
    assert repairs[0]["row"] == 150
    assert "PROVEN" in report and "NOT PROVEN" not in report
    assert "DRE-150" in report
    assert "row 150" in report


def test_a_run_that_stayed_inside_the_first_100_rows_records_that_it_did_not_prove_it():
    """A run that touched only the first 100 rows has not proven this phase and
    must be recorded as not proving it — the report says so itself."""
    cards = _census(60, repairable_rows=(7,))
    repairs, gaps = structural_repair.plan_repairs(cards)
    report = structural_repair.format_report(cards, repairs, gaps)
    assert repairs[0]["identifier"] == "DRE-7"
    assert "NOT PROVEN" in report


def test_the_report_counts_the_whole_census_not_the_first_page():
    cards = _census(226, repairable_rows=(126,))
    repairs, gaps = structural_repair.plan_repairs(cards)
    report = structural_repair.format_report(cards, repairs, gaps)
    assert "226" in report


def test_the_report_lists_both_unrepairable_classes_separately():
    cards = [
        _card("DRE-1", [], parent=_parent("DRE-2459", [], state="In Progress")),
        _card("DRE-2", [], parent=_parent("DRE-2441", [], state="Done")),
        _card("DRE-3", [], parent=None),
    ]
    repairs, gaps = structural_repair.plan_repairs(cards)
    report = structural_repair.format_report(cards, repairs, gaps)
    assert "DRE-2459" in report and "DRE-2441" in report
    assert "cannot be fixed first" in report
    assert "carries no initiative" in report
    assert "no parent" in report


# --------------------------------------------------------------------------
# Applying the repair
# --------------------------------------------------------------------------
class FakeLinear:
    def __init__(self):
        self.added: list[tuple[str, str]] = []
        self.comments: list[tuple[str, str]] = []

    def add_label(self, identifier, label):
        self.added.append((identifier, label))

    def cmd_comment(self, identifier, body):
        self.comments.append((identifier, body))


def test_apply_writes_exactly_the_planned_labels():
    fake = FakeLinear()
    repairs = [{"identifier": "DRE-1", "label": "initiative:bureau",
                "source": "DRE-0", "row": 137}]
    with patch.object(structural_repair, "linear_ops", fake):
        applied = structural_repair.apply_repairs(repairs)
    assert applied == 1
    assert fake.added == [("DRE-1", "initiative:bureau")]
    assert fake.comments and "DRE-0" in fake.comments[0][1]


def test_a_dry_run_writes_nothing():
    fake = FakeLinear()
    cards = _census(150, repairable_rows=(150,))
    with patch.object(structural_repair, "linear_ops", fake), patch.object(
        structural_repair, "fetch_backlog", return_value=cards
    ):
        report = structural_repair.run(apply=False)
    assert fake.added == []
    assert fake.comments == []
    assert "DRE-150" in report


def test_the_run_repairs_the_card_beyond_row_100_it_reported():
    """Criterion 7 end to end: the same run that names a card past row 100 in
    its report is the run that writes that card's label."""
    fake = FakeLinear()
    cards = _census(150, repairable_rows=(150,))
    with patch.object(structural_repair, "linear_ops", fake), patch.object(
        structural_repair, "fetch_backlog", return_value=cards
    ):
        report = structural_repair.run(apply=True)
    assert fake.added == [("DRE-150", "initiative:bureau")]
    assert "PROVEN" in report and "NOT PROVEN" not in report


def test_fetch_backlog_follows_pagination_to_exhaustion():
    """The repair pass reads the same way the promoter now does — otherwise it
    cannot reach the card the criterion asks it to prove."""
    seen: list[str] = []

    def fake_paged(query, variables=None, **kw):
        seen.append(query)
        return [_card(f"DRE-{n}", [], parent=None) for n in range(1, 151)]

    with patch.object(structural_repair.linear_ops, "gql_paged", fake_paged):
        cards = structural_repair.fetch_backlog()
    assert len(cards) == 150
    assert "pageInfo" in seen[0] and "$after" in seen[0]
