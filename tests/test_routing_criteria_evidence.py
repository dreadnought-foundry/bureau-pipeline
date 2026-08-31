"""RED-first tests: the criteria signals are read off real cards, not imagined
ones (DRE-2831).

DRE-2724 shipped a mechanical rule with two halves. Run against live cards on
2026-08-29, the WORKBENCH half fired and the FLEET half did not: DRE-1829, a
real UI card with eleven mentions of design, render or screenshot, returned
`verdict: null, needs_model: true`. The cause was not the fallback — falling
through to a judgement call is the designed behaviour — it was that
`static_visual` matched phrases a card author IMAGINED writing. Six of its nine
phrases appear in ZERO of the 1,561 carded issues in this workspace.

The remedy is not a longer guessed list, which is the same defect made longer.
It is that a phrase may only be in the vocabulary if a real card contains it.

WHAT THIS PINS, one section per acceptance criterion of DRE-2831:

  1. Every phrase of every signal NAMES A REAL CARD, and the named card's own
     acceptance criterion — copied verbatim into the fixture — really matches
     it. An unattested phrase is a config problem, not a harmless addition.
  2. The phrases are exercised against REAL CARD BODIES read from the
     workspace, at least three of them, never bodies written for the test.
     DRE-1829 — the card DRE-2831 was written about — routes FLEET on the
     mechanical rule with no model asked, which is the observation DRE-2724's
     acceptance asks for and nobody could make.
  3. The widening does not cost the WORKBENCH half: DRE-2695 still routes
     WORKBENCH, and DRE-2308 — a real card that states a rendered outcome AND
     live-product verification — routes WORKBENCH rather than to the fleet.
  4. The phrases nobody writes are GONE, and the file records why each was
     rejected with the count that rejected it.

The evidence itself is `tests/fixtures/routing-criteria-corpus-2026-08-31.json`:
the corpus that was read, the verbatim criterion behind every phrase, the
measured before/after, and the real card bodies.

Run: cd bureau-pipeline && python3 -m pytest tests/test_routing_criteria_evidence.py -v
"""
from __future__ import annotations

import copy
import json
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import routing_verdict  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "routing-criteria-corpus-2026-08-31.json"
CARD_ID = re.compile(r"^[A-Z]{2,6}-\d+$")


def _evidence() -> dict:
    with open(FIXTURE, encoding="utf-8") as fh:
        return json.load(fh)


EVIDENCE = _evidence()
PHRASES = [
    (key, phrase)
    for key, signal in routing_verdict._signals()
    for phrase in signal["phrases"]
]
REAL_CARDS = EVIDENCE["cards"]


# ===========================================================================
# 1: every phrase names a real card, and that card really contains it
# ===========================================================================
class TestEveryPhraseIsAttested:
    @pytest.mark.parametrize("key,phrase", PHRASES, ids=[f"{k}:{p}" for k, p in PHRASES])
    def test_every_phrase_names_at_least_one_real_card(self, key, phrase):
        """The rule DRE-2831 asks for: no phrase without a card that writes it."""
        cards = routing_verdict.phrase_evidence(phrase, key)
        assert cards, (
            f"the {key!r} phrase {phrase!r} names no real card — a phrase nobody "
            "writes is a rule that never fires (DRE-2831)"
        )
        for identifier in cards:
            assert CARD_ID.match(identifier), (
                f"{identifier!r} is not a card identifier; the evidence must "
                "name cards a reader can open"
            )

    @pytest.mark.parametrize("key,phrase", PHRASES, ids=[f"{k}:{p}" for k, p in PHRASES])
    def test_the_named_card_really_contains_the_phrase(self, key, phrase):
        """The binding that makes the evidence non-vacuous: the fixture carries
        the card's acceptance criterion VERBATIM, and the vocabulary's own
        matcher must fire on it. A phrase attested by a card that does not
        contain it is a citation, not evidence."""
        attested = EVIDENCE["attestations"].get(phrase)
        assert attested, f"the fixture records no real criterion for {phrase!r}"
        named = set(routing_verdict.phrase_evidence(phrase, key))
        matcher = routing_verdict._phrase_re(phrase)
        proven = set()
        for example in attested["examples"]:
            assert matcher.search(example["criterion"]), (
                f"{phrase!r} does not match the criterion the fixture cites for "
                f"{example['card']}: {example['criterion']!r}"
            )
            proven.add(example["card"])
        assert named & proven, (
            f"the vocabulary cites {sorted(named)} for {phrase!r} but the fixture "
            f"proves only {sorted(proven)} — the config and the evidence must "
            "name the same cards"
        )

    def test_an_unattested_phrase_is_a_config_problem(self):
        """The mutation: add back one of the phrases DRE-2831 found, with no
        card behind it, and the check must name it."""
        doc = copy.deepcopy(routing_verdict.load())
        doc["criteria_signals"]["signals"]["static_visual"]["phrases"].append("pixel-perfect")
        problems = routing_verdict.config_problems(doc)
        assert any("pixel-perfect" in problem for problem in problems), (
            "a phrase with no card behind it passed the check — that is exactly "
            f"how the shipped list grew to nine phrases nobody writes: {problems}"
        )

    def test_evidence_that_names_no_card_at_all_is_a_config_problem(self):
        doc = copy.deepcopy(routing_verdict.load())
        doc["criteria_signals"]["signals"]["static_visual"]["evidence"]["phrases"]["renders"] = {
            "cards": 184,
            "examples": [],
        }
        assert any("renders" in problem for problem in routing_verdict.config_problems(doc))

    def test_the_shipped_vocabulary_has_no_problems_at_all(self):
        assert routing_verdict.config_problems() == []

    def test_the_evidence_names_the_corpus_it_was_read_from(self):
        """"The evidence is named" — which cards were read, and when."""
        for key, signal in routing_verdict._signals():
            block = signal.get("evidence") or {}
            assert block.get("read_on"), f"the {key!r} signal does not say when it was read"
            assert block.get("corpus"), f"the {key!r} signal does not say what was read"
            assert str(EVIDENCE["corpus"]["with_checkbox_criteria"]) in json.dumps(block), (
                f"the {key!r} signal's evidence does not say how many cards were "
                "read, so a reader cannot weigh it"
            )


# ===========================================================================
# 2 and 3: real card bodies, not invented ones
# ===========================================================================
class TestRealCardsRouteMechanically:
    def test_at_least_three_real_card_bodies_are_exercised(self):
        assert len(REAL_CARDS) >= 3
        for card in REAL_CARDS:
            assert card["description"].strip(), f"{card['identifier']} has no body"
            assert routing_verdict.acceptance_criteria(card["description"]), (
                f"{card['identifier']} carries no checkbox criteria, so it proves "
                "nothing about a rule that reads them"
            )

    @pytest.mark.parametrize(
        "card", REAL_CARDS, ids=[c["identifier"] for c in REAL_CARDS]
    )
    def test_the_real_card_routes_as_the_evidence_says(self, card):
        decision = routing_verdict.route(card["title"], card["description"], [])
        expect = card["expect"]
        assert decision.verdict == expect["verdict"], (
            f"{card['identifier']} ({card['why_this_card']}) routed "
            f"{decision.verdict} — {decision.reason}"
        )
        assert decision.source == expect["source"]
        assert decision.needs_model is expect["needs_model"]

    def test_the_card_this_was_written_about_routes_fleet_with_no_model(self):
        """DRE-2724's acceptance: *a card whose acceptance names only static
        visual fidelity is observed routed FLEET*. Run against DRE-1829 on
        2026-08-29 it was not. This is that observation, on that card."""
        card = next(c for c in REAL_CARDS if c["identifier"] == "DRE-1829")
        decision = routing_verdict.route(card["title"], card["description"], [])
        assert decision.verdict == "FLEET"
        assert decision.source == "criteria"
        assert decision.needs_model is False

    def test_the_workbench_half_still_fires_on_the_card_it_fired_on(self):
        """DRE-2724's other live observation, unchanged by the widening."""
        card = next(c for c in REAL_CARDS if c["identifier"] == "DRE-2695")
        assert routing_verdict.route(card["title"], card["description"], []).verdict == "WORKBENCH"

    def test_a_rendered_outcome_verified_in_the_live_product_is_not_fleet(self):
        """THE ADVERSARIAL CASE for the widening. DRE-2308's criteria state a
        rendered outcome — cream on green — and state that it is verified in
        the running product. Interactive is read first for exactly this: a card
        that needs a person must not be handed to the fleet because it also
        names a colour."""
        card = next(c for c in REAL_CARDS if c["identifier"] == "DRE-2308")
        decision = routing_verdict.route(card["title"], card["description"], [])
        assert decision.verdict == "WORKBENCH"

    def test_the_fallthrough_to_a_judgement_call_is_still_real(self):
        """The honest half of DRE-2831: widening is not routing everything. A
        real UI card whose criteria name no rendered outcome still asks a
        model, and the vocabulary does not pretend otherwise."""
        card = next(c for c in REAL_CARDS if c["expect"]["verdict"] is None)
        decision = routing_verdict.route(card["title"], card["description"], [])
        assert decision.needs_model is True
        assert decision.source == "judgement"

    def test_the_measured_effect_is_recorded_not_asserted_from_memory(self):
        """The evidence names what changed across the whole corpus, so the next
        reader can re-run it rather than trust this PR."""
        measured = EVIDENCE["measured"]["all_carded_issues"]
        assert measured["after"]["FLEET"] > measured["before"]["FLEET"] * 5
        assert measured["after"]["judgement"] < measured["before"]["judgement"]
        assert EVIDENCE["measured"]["workbench_lost"] == 0


# ===========================================================================
# 4: the phrases nobody writes are gone, and the file says why
# ===========================================================================
class TestThePhrasesNobodyWritesAreGone:
    @pytest.mark.parametrize(
        "phrase",
        [
            "matches the design",
            "pixel-identical",
            "pixel-perfect",
            "visual parity",
            "design png",
            "render identically",
        ],
    )
    def test_the_shipped_static_visual_phrases_that_matched_nothing_are_gone(self, phrase):
        live = routing_verdict.load()["criteria_signals"]["signals"]["static_visual"]["phrases"]
        assert phrase not in live, (
            f"{phrase!r} matched 0 of {EVIDENCE['corpus']['with_checkbox_criteria']} "
            "carded cards in this workspace — keeping it is keeping the defect"
        )
        assert phrase in EVIDENCE["rejected"], (
            f"{phrase!r} was dropped with no reason recorded; a rejection nobody "
            "can read is a phrase somebody re-adds next quarter"
        )

    def test_every_rejected_phrase_records_the_count_that_rejected_it(self):
        for phrase, note in EVIDENCE["rejected"].items():
            assert isinstance(note.get("cards"), int)
            assert note.get("why", "").strip(), f"{phrase!r} was rejected with no reason"

    def test_no_rejected_phrase_is_still_in_the_vocabulary(self):
        live = {p for _, signal in routing_verdict._signals() for p in signal["phrases"]}
        assert not (live & set(EVIDENCE["rejected"])), (
            "a phrase is both in the vocabulary and on the rejected list"
        )

    def test_the_generic_verbs_that_do_not_discriminate_were_tested_and_rejected(self):
        """`shows` appears in 189 carded cards — and in `synth shows`, `the log
        shows`, `launchctl print ... shows disabled=1`. Frequency is not
        evidence on its own; the phrase has to tell a screen from a CLI."""
        assert EVIDENCE["rejected"]["shows"]["cards"] > 100
        assert "shows" not in {
            p for _, signal in routing_verdict._signals() for p in signal["phrases"]
        }
