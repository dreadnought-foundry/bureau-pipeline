"""RED-first: the watchdog's line names the mark a card really carries (DRE-6424).

THE BUG (live, 2026-10-09 10:40 PT): DRE-6364, a `PROOF:` card with no
`hand-built` label, got `watchdog: DRE-6364 is labeled 'hand-built'`. Since
DRE-6218 and DRE-6225 `hand_built()` is true for four reasons — the CEO's
`hand-built` mark, another person mark (`operator-step`), a `PROOF:` title,
and a person's verdict on a card sitting in the lane it sends cards to — but
the line `flag_stranded` prints for it still spelled `HAND_BUILT_LABEL`. So the
log said a label was on the card when it was not, which is the reading the
CEO's 10-07 rule ("hand-built only when I say so") is trying to remove.

FIX UNDER TEST — `reconcile.hand_built_reason(card)` returns the reason
`hand_built()` answers true (None when it answers false), and the watchdog's
line prints that reason. Only a card that carries `hand-built` reads
`'hand-built'`; `hand_built()`'s own answer is unchanged.

Run: cd bureau-pipeline && python3 -m pytest tests/test_watchdog_names_the_mark.py -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/portico")
os.environ.setdefault("REPO_SLUG", "portico")
os.environ.setdefault("GH_TOKEN", "x")

import reconcile  # noqa: E402
import validate_card  # noqa: E402

# The harnesses are IMPORTED, not copied: each is the one statement of how its
# phase is driven offline.
import test_hand_built_not_stranded as stranded  # noqa: E402
import test_operator_card_promotion as promo  # noqa: E402
import test_planning_lane_strand as planning  # noqa: E402

HAND_WORK = "Hand-work"
HAND_BUILT = "hand-built"
OPERATOR_STEP = "operator-step"
NO_CODE = "no-code"


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    """The pins the imported harnesses' own autouse fixtures apply in their
    modules: this sweep owns portico, portico is on the rail, and the canonical
    snapshot is never fetched."""
    monkeypatch.setattr(reconcile, "REPO_SLUG", "portico")
    monkeypatch.setattr(validate_card, "VALID_SLUGS", {"portico", "atlas", "bureau-pipeline"})
    monkeypatch.setattr(reconcile, "live_rail_slugs",
                        lambda: frozenset({"portico", "atlas", "bureau-pipeline"}),
                        raising=False)


def _hand_built_card():
    return stranded._card(labels=("repo:portico", HAND_BUILT))


def _operator_step_card():
    return stranded._card(state=HAND_WORK, labels=("repo:portico", OPERATOR_STEP, NO_CODE))


def _proof_card():
    """DRE-6364's shape: a PROOF: card wearing `no-code` and no person mark."""
    card = stranded._card(state=HAND_WORK, labels=("repo:portico", "agent:ops", NO_CODE))
    card["title"] = "PROOF: on the live board nothing automatic applies hand-built"
    return card


def _workbench_card():
    """Carried to Hand-work on a WORKBENCH verdict, which marks nothing."""
    card = stranded._card(state=HAND_WORK, labels=("repo:portico",))
    card["comments"] = {"nodes": [{"body": promo.WORKBENCH}]}
    return card


def _watchdog_lines(card, capsys) -> list[str]:
    flagged, comment, add_label = stranded._run_watchdog([card], bodies=[])
    assert flagged == set()
    comment.assert_not_called()
    add_label.assert_not_called()
    return [ln for ln in capsys.readouterr().out.splitlines()
            if ln.startswith(f"watchdog: {card['identifier']} ")]


class TestTheReason:
    def test_the_ceos_mark(self):
        assert reconcile.hand_built_reason(_hand_built_card()) == f"labeled '{HAND_BUILT}'"

    def test_another_person_mark(self):
        assert reconcile.hand_built_reason(_operator_step_card()) == f"labeled '{OPERATOR_STEP}'"

    def test_the_mark_is_named_as_the_card_carries_it(self):
        card = stranded._card(labels=("repo:portico", "Hand-Built"))
        assert reconcile.hand_built_reason(card) == "labeled 'Hand-Built'"

    def test_a_proof_card_with_neither(self):
        assert reconcile.hand_built_reason(_proof_card()) == "a PROOF: card"

    def test_a_person_verdict_in_its_lane(self):
        assert reconcile.hand_built_reason(_workbench_card()) == "routed WORKBENCH to Hand-work"

    def test_a_proof_card_that_wears_the_mark_names_the_mark(self):
        """The label is really there, so naming it is true."""
        card = _proof_card()
        card["labels"]["nodes"].append({"name": HAND_BUILT})
        assert reconcile.hand_built_reason(card) == f"labeled '{HAND_BUILT}'"

    def test_no_reason_when_hand_built_is_false(self):
        for card in (
            stranded._card(labels=("repo:portico",)),
            stranded._card(labels=("repo:portico", NO_CODE)),
            # A person's verdict out of its lane: handed to the fleet.
            dict(_workbench_card(), state={"name": "Todo"}),
            # A fleet verdict in Hand-work is no person's.
            dict(_workbench_card(), comments={"nodes": [{"body": promo.FLEET}]}),
        ):
            assert reconcile.hand_built_reason(card) is None
            assert not reconcile.hand_built(card)

    @pytest.mark.parametrize("make", [
        _hand_built_card, _operator_step_card, _proof_card, _workbench_card,
    ])
    def test_hand_built_is_the_reason_being_there(self, make):
        assert reconcile.hand_built(make()) is True
        assert reconcile.hand_built_reason(make())


class TestTheWatchdogLine:
    """One per case, as the card asks: each line names its own reason, and
    none but the first says 'hand-built'."""

    def test_a_card_with_hand_built(self, capsys):
        (line,) = _watchdog_lines(_hand_built_card(), capsys)
        assert line.startswith("watchdog: DRE-2499 is labeled 'hand-built' — ")

    def test_a_card_with_operator_step(self, capsys):
        (line,) = _watchdog_lines(_operator_step_card(), capsys)
        assert line.startswith("watchdog: DRE-2499 is labeled 'operator-step' — ")
        assert HAND_BUILT not in line

    def test_a_proof_card_with_neither(self, capsys):
        (line,) = _watchdog_lines(_proof_card(), capsys)
        assert line.startswith("watchdog: DRE-2499 is a PROOF: card — ")
        assert HAND_BUILT not in line

    def test_a_card_in_hand_work_by_a_person_verdict(self, capsys):
        (line,) = _watchdog_lines(_workbench_card(), capsys)
        assert line.startswith("watchdog: DRE-2499 is routed WORKBENCH to Hand-work — ")
        assert HAND_BUILT not in line

    def test_the_rest_of_the_line_is_unchanged(self, capsys):
        (line,) = _watchdog_lines(_proof_card(), capsys)
        assert line.endswith(
            "— no dispatched run is expected, so a missing run receipt and an "
            "off-rail repo are both normal here, not a strand"
        )


class TestThePlanningWatchdogLine:
    """flag_stalled_planning printed the same hard-coded label for the same
    function's answer, so it names the reason too."""

    def _lines(self, card, capsys):
        flagged, comment, _, state = planning._run_watchdog([card])
        assert flagged == set()
        comment.assert_not_called()
        state.assert_not_called()
        return [ln for ln in capsys.readouterr().out.splitlines()
                if ln.startswith(f"watchdog: {card['identifier']} ")]

    def test_a_proof_card_in_planning(self, capsys):
        card = planning._card(labels=("repo:portico", NO_CODE), minutes_stale=6000.0)
        card["title"] = "PROOF: it held"
        (line,) = self._lines(card, capsys)
        assert line.startswith("watchdog: DRE-2736 is a PROOF: card — the pipeline")
        assert HAND_BUILT not in line

    def test_a_hand_built_card_in_planning(self, capsys):
        card = planning._card(labels=("repo:portico", HAND_BUILT), minutes_stale=6000.0)
        (line,) = self._lines(card, capsys)
        assert line.startswith("watchdog: DRE-2736 is labeled 'hand-built' — the pipeline")


def test_no_watchdog_line_hard_codes_the_label():
    source = (ROOT / "scripts" / "reconcile.py").read_text()
    assert source.count("is labeled '{HAND_BUILT_LABEL}'") == 0
