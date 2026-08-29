""""Not now" is a first-class outcome, and "dead" is only ever a recommendation
(DRE-2683).

Two rules with the same root — the groomer's opinion is not an action:

  * **"Not now" must be recordable, distinct from "no".** A card can be
    well-formed, wanted, and correctly left alone for a month. Without a
    "later", Intake becomes a pass/fail funnel and the only way to say "later"
    is to say "no".
  * **The groomer recommends and never cancels.** Cancelling is destructive and
    belongs to the operator. In the 2026-08-22 sweep the recommendation, the
    decision and the execution were three separate steps, and the executing
    agent caught an error in its own brief precisely because it was working
    from an explicit list rather than its own judgement. Every dead
    recommendation therefore NAMES the card or merged PR that replaced it — a
    recommendation nobody can check is a recommendation nobody should act on.

Run: cd bureau-pipeline && python3 -m pytest tests/test_groomer_outcomes.py -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import groomer  # noqa: E402

from test_groomer_population import CYCLES, card  # noqa: E402


# --------------------------------------------------------------------------
# the vocabulary
# --------------------------------------------------------------------------
def test_the_three_outcomes_are_distinct_and_later_is_one_of_them():
    assert set(groomer.OUTCOMES) == {"now", "not-now", "dead"}
    assert groomer.OUTCOMES["not-now"] != groomer.OUTCOMES["dead"], (
        "'later' and 'no' must not be the same answer"
    )


def test_every_outcome_is_documented_with_its_meaning():
    doc = (ROOT / "docs" / "groomer.md").read_text(encoding="utf-8")
    for name, meaning in groomer.OUTCOMES.items():
        assert name in doc, f"outcome {name!r} is not in docs/groomer.md"
        assert meaning.split(".")[0] in doc, f"{name!r}'s meaning is not documented"


# --------------------------------------------------------------------------
# "not now"
# --------------------------------------------------------------------------
def test_a_card_left_for_later_says_when_it_is_reconsidered():
    cards = [card(f"DRE-{n:03d}") for n in range(30)]
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=10, batch_cycles=1)
    later = proposal["outcomes"]["not-now"]
    assert later, "nothing was deferred; the fixture is too small"
    for row in later:
        assert row["reconsidered_in"] > proposal["batch"]["cycles"][-1]
        assert row["identifier"] not in {r["identifier"]
                                         for r in proposal["outcomes"]["dead"]}


# --------------------------------------------------------------------------
# "dead" — a recommendation, with the thing that replaced it named
# --------------------------------------------------------------------------
def test_a_named_superseder_is_read_off_the_card():
    assert groomer.superseded_by("**Superseded by:** DRE-2719") == "DRE-2719"
    assert groomer.superseded_by("- Superseded by: DRE-2719 (the classification)") \
        == "DRE-2719"
    pr = "https://github.com/dreadnought-foundry/portico/pull/412"
    assert groomer.superseded_by(f"Superseded by: {pr}") == pr


def test_prose_that_merely_mentions_supersession_is_not_a_declaration():
    """The same anchoring rule the blocker line learned the hard way: a bare
    substring match over prose froze five cards for five days on the sentence
    'neither depends on the other' (DRE-2670). A declaration opens its own
    line and names its target."""
    assert groomer.superseded_by("This work was probably superseded by the "
                                 "wave-1.5 rewrite somewhere") is None
    assert groomer.superseded_by("Superseded by: the newer plan") is None


def test_a_dead_recommendation_always_names_what_replaced_it():
    cards = [card("DRE-1", description="**Superseded by:** DRE-2719"),
             card("DRE-2")]
    proposal = groomer.propose(cards, cycles=CYCLES)
    dead = proposal["outcomes"]["dead"]
    assert [r["identifier"] for r in dead] == ["DRE-1"]
    assert dead[0]["superseded_by"] == "DRE-2719"
    assert all(row.get("superseded_by") for row in dead)


def test_an_unnamed_supersession_is_reported_not_guessed():
    cards = [card("DRE-1", description="This card is superseded, I think."),
             card("DRE-2")]
    proposal = groomer.propose(cards, cycles=CYCLES)
    assert proposal["outcomes"]["dead"] == []
    assert "DRE-1" in proposal["unstated_supersessions"], (
        "a supersession with nothing named is a gap to report, never a "
        "cancellation to infer"
    )
    assert "DRE-1" in {r["identifier"] for r in proposal["sequence"]}


def test_a_dead_card_is_never_given_a_cycle():
    cards = [card("DRE-1", description="**Superseded by:** DRE-2719")]
    cards += [card(f"DRE-{n}") for n in range(2, 6)]
    proposal = groomer.propose(cards, cycles=CYCLES)
    assert "DRE-1" not in {r["identifier"] for r in proposal["outcomes"]["now"]}
    assert "DRE-1" not in {r["identifier"] for r in proposal["outcomes"]["not-now"]}
    seq = {r["identifier"]: r for r in proposal["sequence"]}
    assert seq["DRE-1"]["cycle"] is None
    assert seq["DRE-1"]["outcome"] == "dead"


def test_the_render_names_the_superseder_on_every_dead_line():
    cards = [card("DRE-1", description="**Superseded by:** DRE-2719"),
             card("DRE-2")]
    text = groomer.render_proposal(groomer.propose(cards, cycles=CYCLES))
    for line in text.splitlines():
        if line.startswith("- DRE-1"):
            assert "DRE-2719" in line
            break
    else:                                       # pragma: no cover - guard
        raise AssertionError("the dead recommendation is not in the proposal")
