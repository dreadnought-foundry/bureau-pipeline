"""RED-first tests: the shape classifier reads the seam tells (DRE-3394).

DRE-3244 wrote the rule — *a plan whose later cards depend on OBSERVING its
earlier cards live is not one epic but two* — and DRE-3391 landed it in
`standards/card-quality.md`, `briefs/planner.md` and the shape vocabulary.
Nothing READ it. DRE-3164 was filed as one epic with thirteen build cards, an
`[OPERATOR]` card in the middle of the chain and a surfaces table saying the
relay joins *"second, after a week of clean console releases"*, and the
classifier would have stamped it `epic` — because the only tells it appended to
its prompt were the SIZE tells, and the only thing it read off the body was
nothing at all.

WHAT THIS PINS, one section per acceptance criterion:

  A. The seam tells are READ out of the standard, the way the size tells are:
     `seam_tells` / `seam_block`, the list bounded at the first `### `, a
     renamed headline travelling into the prompt, and `problems()` refusing a
     standard that lost the section or a brief that stopped naming `seam`.
  B. `seam_evidence` is a deterministic, under-reporting read of a card body —
     the phrases real cards write, each named with the card it was read from,
     outside fenced code. None for the four DRE-3013 probes; None for a body
     whose only match is inside a fence.
  C. The decision. An `epic` answer over a seam becomes `wave`, with the seam
     named in the reason. A `wave` answer carries the same suffix. A `one-off`
     is NEVER upgraded — a one-off with a seam is a contradiction the model has
     to resolve, and an upgrade here would be exactly the default DRE-2843
     refuses. No seam anywhere and the decision is byte-for-byte what it was.
  D. The stamp then carries the seam in its `**Why:**` line — that is "the seam
     named in the 🧩 reason" — and the hand stamp is still the override.

The fixture is DRE-3164's own body as it stood on 2026-09-05, before the split
that the seam rule was written from. A body invented for a test proves the
reader reads what the test wrote; this one proves it reads the card the rule
came from.

Run: cd bureau-pipeline && python3 -m pytest tests/test_planning_classify_seam.py -v
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import planning_classify  # noqa: E402
import planning_shape  # noqa: E402

STANDARD = ROOT / "standards" / "card-quality.md"
BRIEF = ROOT / "briefs" / "planner.md"
SEAM_FIXTURE = ROOT / "tests" / "fixtures" / "dre-3164-epic-2026-09-05.json"
PROBES = ROOT / "tests" / "fixtures" / "planning-probes-dre3013.json"

MODEL = "claude-opus-5"

#: The literal the contract shares with `planning_route`, the wave planner and
#: anybody reading the card: the stamp's **Why:** line carries it, followed by
#: the seam.
MARK = "observation-gated seam:"

_WHY_LINE = re.compile(r"^\*\*Why:\*\* (.+)$", re.MULTILINE)


# --------------------------------------------------------------------------- #
# the fixtures, and the Linear stand-in                                        #
# --------------------------------------------------------------------------- #

def _fixture(card_id: str) -> dict:
    with open(SEAM_FIXTURE, encoding="utf-8") as fh:
        cards = json.load(fh)["cards"]
    return next(c for c in cards if c["card"] == card_id)


def _probe_bodies() -> list[str]:
    with open(PROBES, encoding="utf-8") as fh:
        return [p["body"] for p in json.load(fh)["probes"]]


def _answer(shape=None, why="the plan holds together as one set of cards",
            tells=(1,), seam=None, decision=False, question=None) -> str:
    """A model answer in the format the brief asks for. `seam` is omitted
    entirely when it is None — the classifier must not need the key to exist."""
    payload = {"shape": shape, "why": why, "tells": list(tells),
               "decision": decision}
    if seam is not None:
        payload["seam"] = seam
    if question is not None:
        payload["question"] = question
    return json.dumps(payload)


class _Lops:
    """Every Linear seam `planning_classify` touches, and nothing else."""

    def __init__(self, card: dict, bodies=()):
        self.card = card
        self.bodies = list(bodies)
        self.comments: list[str] = []
        self.labels: list[str] = []
        self.states: list[str] = []

    def gql(self, query, variables=None):
        return {
            "issue": {
                "identifier": self.card["card"],
                "title": self.card["title"],
                "description": self.card["body"],
                "labels": {"nodes": [{"name": n} for n in self.card["labels"]]},
                "children": {"nodes": []},
            }
        }

    def comment_bodies(self, identifier):
        return list(self.bodies)

    def count_comments(self, identifier, needle, **kwargs):
        return sum(1 for body in self.bodies if needle in body)

    def cmd_comment(self, identifier, body, *flags):
        self.comments.append(body)
        self.bodies.append(body)

    def add_label(self, identifier, label):
        self.labels.append(label)

    def cmd_state(self, identifier, lane, *flags):
        self.states.append(lane)


def _caller(answer: str):
    def call(model, prompt):
        call.seen.append((model, prompt))
        return answer

    call.seen = []
    return call


def _never_called(model, prompt):  # pragma: no cover - the assertion is the point
    raise AssertionError("the model was called when the card was already classified")


def _why_line(body: str) -> str:
    found = _WHY_LINE.search(body)
    assert found, f"the stamp carries no **Why:** line:\n{body}"
    return found.group(1)


# ===========================================================================
# A. the seam tells are read out of the standard
# ===========================================================================
class TestTheSeamTellsAreRead:
    def test_the_seam_tells_come_from_the_standard(self):
        tells = planning_classify.seam_tells()
        assert tells, "no seam tells were read out of standards/card-quality.md"
        # DRE-3244 wrote four, and `problems()` refuses fewer than three.
        assert len(tells) >= 4
        assert 1 in tells and "clean" in tells[1].lower()

    def test_the_list_stops_at_the_first_subheading(self):
        """The worked example under `### ` opens no numbered list, but the
        bound is the same one `_tells_section` keeps: a scan that ran on would
        read the next section's items as seam tests."""
        section = planning_classify.seam_block()
        assert "The worked example" not in section
        assert "DRE-3218" in section, "the reasoning under each tell is the half"

    def test_a_renamed_seam_tell_travels_into_the_prompt(self):
        edited = STANDARD.read_text(encoding="utf-8").replace(
            "**An operator card in the middle of the chain.**",
            "**A human act in the middle of the chain.**",
        )
        assert "human act" in planning_classify.seam_tells(edited)[2]

    def test_the_prompt_carries_the_seam_tests_after_the_size_tests(self):
        prompt = planning_classify.prompt_for({
            "identifier": "DRE-3164", "title": "t", "description": "d",
        })
        assert "## The seam tests" in prompt
        assert prompt.index("## The size tests") < prompt.index("## The seam tests")
        for headline in planning_classify.seam_tells().values():
            assert headline in prompt, (
                f"the prompt never carries the seam tell {headline!r}"
            )

    def test_a_standard_with_no_seam_section_is_a_problem(self):
        text = STANDARD.read_text(encoding="utf-8").replace(
            "## When a plan is two epics — the observation-gated seam (DRE-3244)",
            "## When a plan is two epics",
        )
        with pytest.raises(planning_classify.ClassifyError):
            planning_classify.seam_tells(text)

    def test_fewer_than_three_seam_tells_is_a_problem(self, monkeypatch):
        monkeypatch.setattr(planning_classify, "seam_tells",
                            lambda text=None: {1: "one", 2: "two"})
        found = planning_classify.problems()
        assert any("seam" in problem for problem in found), (
            "a standard that lost its seam tells must be named as a problem"
        )

    def test_the_brief_must_name_the_seam_key(self):
        assert "seam" in planning_classify.ANSWER_KEYS
        assert "`seam`" in planning_classify.brief_prompt(), (
            "briefs/planner.md never names the 'seam' key the parser reads"
        )

    def test_the_sources_still_compose(self):
        assert planning_classify.problems() == []

    def test_the_check_command_exits_zero(self):
        done = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "planning_classify.py"), "check"],
            capture_output=True, text=True, check=False, cwd=str(ROOT),
        )
        assert done.returncode == 0, done.stdout + done.stderr


# ===========================================================================
# B. the deterministic read of a card body
# ===========================================================================
class TestSeamEvidence:
    def test_dre3164_fires_on_the_clean_console_releases(self):
        evidence = planning_classify.seam_evidence(_fixture("DRE-3164")["body"])
        assert evidence, "the card the seam rule was written from reads as no seam"
        assert "clean console releases" in evidence
        assert len(evidence) <= 130, "the evidence is a sentence, not a section"

    @pytest.mark.parametrize("body,what", [
        ("The relay joins after seven clean console days.", "a clean-days count"),
        ("Blocked by the fourteen clean releases the console owes.",
         "a clean-releases count"),
        ("[OPERATOR] the identity grant and the flip.", "an operator card"),
        ("One supervised release, watched by a person.", "a supervised release"),
        ("The website joins after the first is proven.", "after the first is proven"),
        ("The switch-on happens after the seven days.", "a switch-on that waits"),
    ])
    def test_the_phrases_real_cards_write(self, body, what):
        assert planning_classify.seam_evidence(body), f"{what} read as no seam"

    def test_an_ordinary_epic_reads_as_no_seam(self):
        assert planning_classify.seam_evidence(_fixture("DRE-3394-EIGHT")["body"]) is None

    def test_the_dre3013_probes_read_as_no_seam(self):
        for body in _probe_bodies():
            assert planning_classify.seam_evidence(body) is None, (
                "a front-door probe reads as a seam — the phrase list has stopped "
                "under-reporting"
            )

    def test_a_match_inside_a_fence_is_not_evidence(self):
        body = (
            "The plan is one epic and nothing waits on anything.\n\n"
            "```\n"
            "# the example the standard quotes\n"
            "second, after a week of clean console releases\n"
            "[OPERATOR] one supervised release\n"
            "```\n\n"
            "Every card ships alone.\n"
        )
        assert planning_classify.seam_evidence(body) is None

    def test_the_evidence_is_the_sentence_that_fired(self):
        body = (
            "The console is the pilot and ships first.\n"
            "The relay joins second, after a week of clean console releases.\n"
            "The website is the same shape.\n"
        )
        evidence = planning_classify.seam_evidence(body)
        assert evidence == (
            "The relay joins second, after a week of clean console releases."
        )

    def test_an_empty_body_is_not_a_seam(self):
        assert planning_classify.seam_evidence("") is None
        assert planning_classify.seam_evidence(None) is None


# ===========================================================================
# C. the decision
# ===========================================================================
class TestTheDecision:
    def test_parse_reads_the_seam_the_model_named(self):
        decision = planning_classify.parse(
            _answer(shape="epic", seam="cards 9-13 wait on the first release"),
            model=MODEL,
        )
        assert decision.seam == "cards 9-13 wait on the first release"

    @pytest.mark.parametrize("seam", [None, "", "   ", 17, [], {}])
    def test_no_readable_seam_is_None(self, seam):
        decision = planning_classify.parse(
            _answer(shape="epic", seam=seam), model=MODEL)
        assert decision.seam is None

    def test_an_epic_over_a_seam_becomes_a_wave(self):
        card = _fixture("DRE-3164")
        decision = planning_classify.classify(
            {"identifier": card["card"], "title": card["title"],
             "description": card["body"]},
            call=_caller(_answer(shape="epic", why="thirteen build cards")),
            model=MODEL,
        )
        assert decision.shape == "wave"
        assert MARK in decision.why
        assert "clean console releases" in decision.why
        assert "DRE-3244" in decision.why, (
            "the reason must cite the rule that files this as two epics"
        )

    def test_a_wave_the_model_named_carries_the_same_suffix(self):
        card = _fixture("DRE-3164")
        seam = "the relay and the website wait on a week of clean console releases"
        decision = planning_classify.classify(
            {"identifier": card["card"], "title": card["title"],
             "description": card["body"]},
            call=_caller(_answer(shape="wave", seam=seam)),
            model=MODEL,
        )
        assert decision.shape == "wave"
        assert f"{MARK} {seam}" in decision.why
        assert decision.seam == seam

    def test_the_model_named_seam_outranks_the_evidence(self):
        card = _fixture("DRE-3164")
        seam = "cards 9-13 wait on watching the first supervised release"
        decision = planning_classify.classify(
            {"identifier": card["card"], "title": card["title"],
             "description": card["body"]},
            call=_caller(_answer(shape="epic", seam=seam)),
            model=MODEL,
        )
        assert f"{MARK} {seam}" in decision.why

    def test_a_one_off_is_never_upgraded(self):
        """A one-off with a seam is a contradiction the MODEL has to resolve.
        Upgrading it here would be the silent default DRE-2843 refuses."""
        card = _fixture("DRE-3164")
        decision = planning_classify.classify(
            {"identifier": card["card"], "title": card["title"],
             "description": card["body"]},
            call=_caller(_answer(shape="one-off", why="one pull request")),
            model=MODEL,
        )
        assert decision.shape == "one-off"
        assert MARK not in decision.why

    def test_a_refusal_over_a_seam_is_still_a_refusal(self):
        card = _fixture("DRE-3164")
        decision = planning_classify.classify(
            {"identifier": card["card"], "title": card["title"],
             "description": card["body"]},
            call=_caller(_answer(shape=None, question="which is it?")),
            model=MODEL,
        )
        assert decision.escalates and decision.shape is None

    def test_no_seam_leaves_the_decision_byte_for_byte(self):
        card = _fixture("DRE-3394-EIGHT")
        decision = planning_classify.classify(
            {"identifier": card["card"], "title": card["title"],
             "description": card["body"]},
            call=_caller(_answer(shape="epic", why="eight cards, each shippable",
                                 tells=(1, 4))),
            model=MODEL,
        )
        assert decision.shape == "epic"
        assert decision.seam is None
        assert decision.why == "eight cards, each shippable"


# ===========================================================================
# D. the stamp carries the seam, and the hand stamp still wins
# ===========================================================================
class TestTheStamp:
    def test_the_epic_answer_over_dre3164_stamps_a_wave(self):
        card = _fixture("DRE-3164")
        assert card["expect"] == "wave"
        lops = _Lops(card)
        decision = planning_classify.run(
            lops, card["card"],
            call=_caller(_answer(shape="epic", why="thirteen build cards", tells=(1,))),
            model=MODEL,
        )
        assert decision.shape == "wave"
        assert planning_shape.shape_on(lops.bodies) == "wave"
        assert lops.labels == list(planning_shape.marks("wave"))

        why = _why_line(lops.comments[0])
        assert MARK in why, "the 🧩 stamp does not name the seam"
        assert "clean console releases" in why, (
            "the stamp does not carry the evidence sentence that fired"
        )

    def test_the_named_seam_reaches_the_why_line(self):
        card = _fixture("DRE-3164")
        seam = "the relay and the website wait on a week of clean console releases"
        lops = _Lops(card)
        decision = planning_classify.run(
            lops, card["card"],
            call=_caller(_answer(shape="wave", seam=seam, why="two epics in order")),
            model=MODEL,
        )
        assert decision.shape == "wave"
        assert planning_shape.shape_on(lops.bodies) == "wave"
        assert f"{MARK} {seam}" in _why_line(lops.comments[0])

    def test_the_ordinary_epic_why_line_is_what_it_always_was(self):
        """Byte-identical: the size-tests suffix `stamp_why` has always written,
        and not one word of seam anywhere."""
        card = _fixture("DRE-3394-EIGHT")
        assert card["expect"] == "epic"
        lops = _Lops(card)
        decision = planning_classify.run(
            lops, card["card"],
            call=_caller(_answer(shape="epic", why="eight cards, each shippable",
                                 tells=(1, 4))),
            model=MODEL,
        )
        assert decision.shape == "epic"
        assert planning_shape.shape_on(lops.bodies) == "epic"

        why = _why_line(lops.comments[0])
        assert "seam" not in why.lower()
        before = planning_classify.stamp_why(planning_classify.Decision(
            shape="epic", why="eight cards, each shippable", tells=(1, 4),
        ))
        assert why == before, "the no-seam stamp is not what it was before DRE-3394"
        assert "size tests checked:" in why

    def test_the_hand_stamp_still_pre_empts_the_classifier(self):
        card = _fixture("DRE-3164")
        lops = _Lops(card, bodies=[
            planning_shape.shape_comment("epic", "the operator's call")])
        decision = planning_classify.run(
            lops, card["card"], call=_never_called, model=MODEL)
        assert decision.shape == "epic" and decision.already is True
        assert lops.comments == [], "nothing writes over an existing stamp"

    def test_a_one_off_answer_over_a_seam_stamps_a_one_off(self):
        card = _fixture("DRE-3164")
        lops = _Lops(card)
        decision = planning_classify.run(
            lops, card["card"],
            call=_caller(_answer(shape="one-off", why="one pull request")),
            model=MODEL,
        )
        assert decision.shape == "one-off"
        assert planning_shape.shape_on(lops.bodies) == "one-off"
        assert MARK not in lops.comments[0]
