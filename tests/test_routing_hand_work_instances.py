"""RED-first: the vocabulary stops routing WORKBENCH on a phrase and stops
marking `hand-built` (DRE-6227).

`criteria_verdict` routed a card WORKBENCH the moment one `- [ ]` line held one
of the `interactive` signal's ten phrases, and WORKBENCH's `marks` put
`hand-built` on it, so the sweep carried buildable code to Hand-work, where
nobody builds it. Six cards in a week: a button label (DRE-6164), a board
column's name (DRE-5960), a post-deploy note (DRE-6166), the proof brief's own
wording (DRE-6043), a two-line mypy fix (DRE-5952) and a token check
(DRE-6143). The CEO's rule of 2026-10-07: `hand-built` is applied only when he
asks for it, and nothing automatic applies it.

WHAT THIS PINS:

  1. The six cards, as `tests/fixtures/routing-hand-work-2026-10-07.json` holds
     them — the criteria read verbatim from Linear and, for the four reworded
     by hand, the criteria with the quoted phrase restored — route FLEET or to
     a judgement call. Never WORKBENCH, never NEEDS WORK.
  2. The fixture really carries the phrase: the retired signal, put back into
     an in-memory copy of the vocabulary, routes every restored card WORKBENCH
     on the phrase its verdict quoted. Without that, (1) could pass on criteria
     that never said the words.
  3. The shipped vocabulary: no `marks` list carries `hand-built`, OPERATOR
     marks `operator-step` + `no-code`, WORKBENCH marks nothing, the only
     criteria signal is `static_visual`, and the rendered document is current.
  4. The lane contract says the same, and declares the Hand-work migration's
     writes (DRE-6230) on exactly the three lanes it writes.
  5. A `PROOF:` card stamped through `proof_and_demo.write_stamps` receives
     `no-code` and nothing else.

Run: cd bureau-pipeline && python3 -m pytest tests/test_routing_hand_work_instances.py -v
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
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import lane_contract  # noqa: E402
import linear_ops  # noqa: E402
import proof_and_demo  # noqa: E402
import routing_verdict  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "routing-hand-work-2026-10-07.json"
CORPUS = ROOT / "tests" / "fixtures" / "routing-criteria-corpus-2026-08-31.json"
DOC = ROOT / "docs" / "routing-verdicts.md"

# Spelled literally on purpose: these are the labels as they exist in Linear.
HAND_BUILT = "hand-built"
OPERATOR_STEP = "operator-step"
NO_CODE = "no-code"

THE_SIX = ("DRE-6143", "DRE-5952", "DRE-6164", "DRE-6166", "DRE-5960", "DRE-6043")
REWORDED = ("DRE-6164", "DRE-6166", "DRE-5960", "DRE-6043")
MIGRATION = "hand_work_migration.py"


def _load(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


EVIDENCE = _load(FIXTURE)
CARDS = {card["identifier"]: card for card in EVIDENCE["cards"]}


def _body(criteria) -> str:
    """A card body carrying `criteria` as its checkbox items — the one shape
    `routing_verdict.acceptance_criteria` reads."""
    return "## Acceptance criteria\n\n" + "".join(f"- [ ] {line}\n" for line in criteria)


def _versions():
    """(identifier, which, criteria) for every set of criteria the fixture
    holds: the verbatim one for all six, the restored one for the four."""
    out = []
    for ident in THE_SIX:
        card = CARDS[ident]
        out.append((ident, "verbatim", card["criteria"]))
        if card.get("restored"):
            out.append((ident, "restored", card["restored"]["criteria"]))
    return out


VERSIONS = _versions()


def _retired_interactive() -> list:
    """The ten phrases the `interactive` signal carried, read off the corpus
    fixture's attestations — kept there, marked retired, as the record of why
    they were read. Never restated in this file."""
    attestations = _load(CORPUS)["attestations"]
    return [phrase for phrase, entry in attestations.items() if entry.get("retired")]


def _with_the_old_signal() -> dict:
    """An in-memory copy of the shipped vocabulary with the retired signal put
    back the way it shipped: WORKBENCH, read before `static_visual`."""
    doc = copy.deepcopy(routing_verdict.load())
    block = doc["criteria_signals"]
    block["signals"]["interactive"] = {
        "verdict": "WORKBENCH",
        "why": "The criterion names an interactive flow or live system state.",
        "phrases": _retired_interactive(),
    }
    block["order"] = ["interactive"] + [k for k in block["order"] if k != "interactive"]
    return doc


def vocabulary_before_dre_6227() -> dict:
    """The vocabulary as it shipped before this card: the retired signal back
    in place, WORKBENCH marking `hand-built` and OPERATOR `hand-built` +
    `no-code`. For suites that replay a card routed under it (DRE-4724's
    retirement, `tests/test_verdict_retirement.py`)."""
    doc = _with_the_old_signal()
    for entry in doc["verdicts"]:
        if entry["name"] == "WORKBENCH":
            entry["marks"] = [HAND_BUILT]
        if entry["name"] == "OPERATOR":
            entry["marks"] = [HAND_BUILT, NO_CODE]
    return doc


# ===========================================================================
# the fixture is what the card says it is
# ===========================================================================
class TestTheFixture:
    def test_it_carries_the_six_cards(self):
        assert tuple(CARDS) == THE_SIX

    @pytest.mark.parametrize("ident", THE_SIX)
    def test_each_card_names_the_phrase_its_verdict_quoted(self, ident):
        card = CARDS[ident]
        assert card["quoted_phrase"], ident
        assert f"a criterion says '{card['quoted_phrase']}'" in card["verdict_why"], ident
        assert card["criteria"], f"{ident} carries no criteria to route"

    def test_the_four_reworded_cards_are_restored_and_flagged(self):
        for ident in THE_SIX:
            restored = CARDS[ident].get("restored")
            if ident in REWORDED:
                assert restored and restored["reconstructed"] is True, ident
                assert restored["how"].strip(), ident
                assert restored["criteria"] != CARDS[ident]["criteria"], ident
            else:
                assert restored is None, f"{ident} was moved, not reworded"
                assert CARDS[ident]["reworded"] is False

    def test_it_quotes_the_ceo_s_rule_with_its_date(self):
        assert EVIDENCE["rule"]["on"] == "2026-10-07"
        assert HAND_BUILT in EVIDENCE["rule"]["says"]

    def test_the_retired_phrases_are_the_ten_the_signal_carried(self):
        assert sorted(_retired_interactive()) == sorted([
            "sign in", "sign out", "log in", "in production", "against the live",
            "on the live", "in the live product", "verified live", "by hand",
            "manually",
        ])


# ===========================================================================
# 1 + 2: the six cards route to the fleet or a judgement call, never to a person
# ===========================================================================
class TestTheSixCardsRoute:
    @pytest.mark.parametrize("ident,which,criteria", VERSIONS,
                             ids=[f"{i}-{w}" for i, w, _ in VERSIONS])
    def test_fleet_or_a_judgement_call(self, ident, which, criteria):
        card = CARDS[ident]
        decision = routing_verdict.route(card["title"], _body(criteria), card["labels"])
        assert decision.verdict in ("FLEET", None), (
            f"{ident} ({which}) routed {decision.verdict} — {decision.reason}")
        assert decision.verdict != "WORKBENCH"
        assert decision.verdict != "NEEDS WORK"
        if decision.verdict is None:
            assert decision.source == "judgement" and decision.needs_model is True

    @pytest.mark.parametrize("ident", REWORDED)
    def test_the_old_signal_routes_every_restored_card_workbench_on_its_phrase(self, ident):
        """The proof the fixture carries the phrase: under the retired signal
        the restored criteria route exactly as the card's verdict said."""
        card = CARDS[ident]
        doc = _with_the_old_signal()
        verdict, reason = routing_verdict.criteria_verdict(
            _body(card["restored"]["criteria"]), doc)
        assert verdict == "WORKBENCH", (ident, reason)
        assert f"a criterion says {card['quoted_phrase']!r}" in reason, (ident, reason)

    def test_the_old_signal_still_reads_the_card_that_was_moved_with_its_phrase(self):
        """DRE-5952 was moved, not reworded, and its criteria still say it."""
        card = CARDS["DRE-5952"]
        verdict, reason = routing_verdict.criteria_verdict(
            _body(card["criteria"]), _with_the_old_signal())
        assert verdict == "WORKBENCH"
        assert f"a criterion says {card['quoted_phrase']!r}" in reason

    def test_the_judgement_sentence_no_longer_names_an_interactive_flow(self):
        verdict, reason = routing_verdict.criteria_verdict(_body(CARDS["DRE-6143"]["criteria"]))
        assert verdict is None
        assert "interactive" not in reason


# ===========================================================================
# 3: the shipped vocabulary
# ===========================================================================
class TestTheVocabulary:
    def test_no_verdict_marks_hand_built(self):
        for name in routing_verdict.verdicts():
            assert HAND_BUILT not in routing_verdict.marks(name), name

    def test_operator_marks_operator_step_and_no_code(self):
        assert routing_verdict.marks("OPERATOR") == (OPERATOR_STEP, NO_CODE)
        assert routing_verdict.marks("OPERATOR")[0] == routing_verdict.OPERATOR_STEP_LABEL
        assert NO_CODE == linear_ops.NO_CODE_LABEL

    @pytest.mark.parametrize("name", ["FLEET", "WORKBENCH", "PARKED", "NEEDS WORK"])
    def test_every_other_verdict_marks_nothing(self, name):
        assert routing_verdict.marks(name) == ()

    def test_the_only_criteria_signal_is_static_visual(self):
        block = routing_verdict.load()["criteria_signals"]
        assert block["order"] == ["static_visual"]
        assert "interactive" not in block["signals"]
        assert [key for key, _ in routing_verdict._signals()] == ["static_visual"]

    def test_the_person_marks_are_read_off_the_landed_data(self):
        assert routing_verdict.person_marks() == (OPERATOR_STEP, NO_CODE, HAND_BUILT)

    def test_the_readme_quotes_the_ceo_s_rule_with_its_date(self):
        readme = " ".join(routing_verdict.load()["_readme"])
        assert "2026-10-07" in readme
        assert "only when he asks for it" in readme
        assert "nothing automatic applies it" in readme

    def test_the_criteria_readme_says_a_post_ship_observation_never_holds_the_build(self):
        readme = " ".join(routing_verdict.load()["criteria_signals"]["_readme"])
        assert "proof observation" in readme and "follow-up card" in readme

    def test_no_verdict_leans_on_hand_built(self):
        for name in routing_verdict.verdicts():
            entry = routing_verdict.record(name)
            assert HAND_BUILT not in entry["means"], name
            assert HAND_BUILT not in entry["why"], name

    def test_workbench_says_the_criteria_no_longer_produce_it(self):
        entry = routing_verdict.record("WORKBENCH")
        said = (entry["means"] + " " + entry["why"]).lower()
        assert "demo:" in said
        assert "criteria" in said and "no longer" in said

    def test_the_check_passes(self):
        assert routing_verdict.config_problems() == []
        assert routing_verdict.main(["check"]) == 0

    def test_the_rendered_document_is_its_render(self):
        assert DOC.read_text(encoding="utf-8") == routing_verdict.render_markdown()
        assert "### interactive" not in routing_verdict.render_markdown()

    def test_the_receipt_names_the_marks_it_applied(self):
        operator = routing_verdict.hand_built_promotion("OPERATOR")
        assert f"Marked `{OPERATOR_STEP}`, `{NO_CODE}`." in operator
        assert HAND_BUILT not in operator
        workbench = routing_verdict.hand_built_promotion("WORKBENCH")
        assert "No mark was applied." in workbench
        assert HAND_BUILT not in workbench
        proof = routing_verdict.hand_built_promotion("OPERATOR", title="PROOF: it held")
        assert f"Marked `{NO_CODE}`." in proof


# ===========================================================================
# 4: the lane contract
# ===========================================================================
def _clauses():
    for entry in lane_contract.load()["lanes"]:
        for kind, clause in (entry.get("clauses") or {}).items():
            yield entry["name"], kind, clause


# What the old clauses said the sweep stamps: a list of marks with
# `hand-built` in it, a mark set the sweep applies.
_STAMPS_HAND_BUILT = re.compile(
    r"(marks[^.]{0,80}`hand-built`|`hand-built`, plus `no-code`|"
    r"`hand-built` and `no-code` on an OPERATOR card|marked hand-built one)"
)


class TestTheLaneContract:
    def test_the_check_passes(self):
        """Every clause that can be asserted without a live board; the
        `board.*` clauses need Linear and are the CLI's job."""
        report = lane_contract.check(vocabulary=lane_contract.pipeline_vocabulary())
        offline = [f for f in report.failures() if not f.clause_id.startswith("board.")]
        assert offline == [], report.text()

    def test_the_rendered_document_matches(self):
        with open(lane_contract.DOC_PATH, encoding="utf-8") as fh:
            assert fh.read() == lane_contract.render_markdown()

    def test_no_clause_says_the_sweep_stamps_hand_built(self):
        for lane, kind, clause in _clauses():
            for key in ("text", "pending"):
                said = clause.get(key) or ""
                assert not _STAMPS_HAND_BUILT.search(said), (lane, kind, key)

    @pytest.mark.parametrize("lane,kind,key", [
        ("Hand-work", "entrance", "text"),
        ("Hand-work", "writers", "text"),
        ("Hand-work", "evidence", "text"),
        ("Backlog", "exit", "text"),
        ("Todo", "entrance", "pending"),
        ("Todo", "exit", "text"),
        ("Todo", "evidence", "text"),
    ])
    def test_the_clauses_that_named_the_marks_name_operator_step(self, lane, kind, key):
        said = lane_contract.lane(lane)["clauses"][kind][key]
        assert f"`{OPERATOR_STEP}`" in said, (lane, kind, key)

    def test_the_hand_work_entrance_states_the_rule(self):
        said = lane_contract.lane("Hand-work")["clauses"]["entrance"]["text"]
        assert "only when he asks for it" in said
        assert "nothing automatic applies it" in said

    def test_the_migration_is_a_writer_of_exactly_three_lanes(self):
        writing = [entry["name"] for entry in lane_contract.load()["lanes"]
                   if MIGRATION in ((entry.get("clauses") or {}).get("writers") or {}).get("who", [])]
        assert sorted(writing) == ["Backlog", "Hand-work", "Planning"]

    @pytest.mark.parametrize("lane,phrase", [
        ("Planning", "no critic pass"),
        ("Backlog", "fresh FLEET verdict"),
        ("Hand-work", "OUT of this lane"),
    ])
    def test_each_lane_says_why_the_migration_writes_it(self, lane, phrase):
        text = lane_contract.lane(lane)["clauses"]["writers"]["text"]
        assert "DRE-6230" in text and phrase in text, lane


# ===========================================================================
# 5: a proof card's stamp
# ===========================================================================
class TestTheProofStamp:
    def test_a_proof_card_stamped_through_write_stamps_receives_no_code_alone(self):
        from test_person_marks import _Linear

        children = [
            {"identifier": "DRE-9001", "title": "Build piece 1",
             "body": "Add it.\n\n## Acceptance criteria\n\n- [ ] it renders\n",
             "labels": ["repo:portico", "agent:engineer"], "blocked_by": []},
            {"identifier": "DRE-9091", "title": "PROOF: the gate refused a real epic",
             "body": f"Read it.\n\n{proof_and_demo.CLOSING_LINE}\n\n"
                     "## Acceptance criteria\n\n- [ ] the record is read\n",
             "labels": ["repo:portico", "agent:ops"], "blocked_by": ["DRE-9001"]},
        ]
        with _Linear({c["identifier"]: c["title"] for c in children}) as board:
            assert proof_and_demo.write_stamps(children) == 1
        assert board.labels_on("DRE-9091") == (NO_CODE,)

    def test_the_confirming_verdicts_are_still_workbench_and_operator(self, capsys):
        assert proof_and_demo.confirming_verdicts() == ("WORKBENCH", "OPERATOR")
        assert proof_and_demo.main(["vocabulary"]) == 0
        assert "WORKBENCH, OPERATOR" in capsys.readouterr().out
