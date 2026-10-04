"""RED-first tests: a roll-up's child epic is not a roll-up because of its own
gate text (DRE-5800).

Proof DRE-4753, pass 2 (`docs/roll-up-proof-dre4680.md`): fixture DRE-5770 was
split correctly into child epics DRE-5771 and DRE-5773. Each child then entered
Planning, and the classifier stamped each CHILD `roll-up` too. The model said
`epic` — DRE-5773's own reason reads "so it is not a roll-up" — but
`seam_evidence`, the deterministic floor, matched "seven clean days" in the
sentence the split planner itself writes into every child to name the gate in
front of it. The children re-entered the split route and neither was planned.

WHAT THIS PINS, one section per acceptance criterion:

  A. A child epic of a roll-up — its parent is stamped `roll-up` — is never
     made a roll-up by the floor reading the gate text in its description. The
     model's `epic` stands, through `classify` and through the stamp `run`
     writes.
  B. The fixture is a child description carrying that gate text, and the test
     first proves the floor DOES fire on it, so the pass is not vacuous.
  C. A real roll-up — DRE-3164, the parent too big for one epic, with no
     roll-up above it — is still stamped `roll-up` by the same floor. A card
     whose parent is an ordinary epic, or whose parent's stamp cannot be read,
     keeps the floor too. And a child the MODEL reads as a roll-up of its own is
     still one: nesting is allowed (`standards/card-quality.md`).

Run: cd bureau-pipeline && python3 -m pytest tests/test_planning_classify_roll_up_child.py -v
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import planning_classify  # noqa: E402
import planning_shape  # noqa: E402

SEAM_FIXTURE = ROOT / "tests" / "fixtures" / "dre-3164-epic-2026-09-05.json"

MODEL = "claude-opus-5"
MARK = "observation-gated seam:"
PARENT = "DRE-5770"

_WHY_LINE = re.compile(r"^\*\*Why:\*\* (.+)$", re.MULTILINE)

#: A child the split route files, written the way the roll-up planner wrote
#: DRE-5773: its slice first, then the gate in front of it — which is the
#: parent's seam, between the siblings, and not one inside this child.
CHILD = {
    "card": "DRE-5773",
    "title": ("[EPIC] whats-new-digest-feed: the feed and the weekly email "
              "digest, after seven clean days live"),
    "labels": ["agent:planner", "repo:agent-bureau-demo"],
    "body": (
        "The second surface of DRE-5770: a feed of what shipped, a weekly "
        "email digest of it, the job that sends the digest, and an opt-in and "
        "unsubscribe for it. It is planned in detail only after the panel from "
        "DRE-5771 has run seven clean days live following its supervised "
        "release.\n"
        "\n"
        "## Acceptance criteria\n"
        "\n"
        "- [ ] A plan for the feed and the digest is green-lit on its own.\n"
    ),
}


def _fixture(card_id: str) -> dict:
    with open(SEAM_FIXTURE, encoding="utf-8") as fh:
        cards = json.load(fh)["cards"]
    return next(c for c in cards if c["card"] == card_id)


def _answer(shape, why, tells=(1,), seam=None) -> str:
    payload = {"shape": shape, "why": why, "tells": list(tells),
               "decision": False}
    if seam is not None:
        payload["seam"] = seam
    return json.dumps(payload)


def _caller(answer: str):
    def call(model, prompt):
        return answer
    return call


#: The model's own reading of DRE-5773, as the proof recorded it.
CHILD_SAYS_EPIC = _answer(
    "epic",
    "the seven-clean-days wait sits in front of the whole card, already cut by "
    "the parent as the DRE-5771 blocker, not between its own pieces, so it is "
    "not a roll-up",
)


class _Lops:
    """Linear, keyed by card: the card read, its parent, and every thread."""

    def __init__(self, card: dict, *, parent: str | None = None, threads=None):
        self.card = card
        self.parent = parent
        self.threads = {k: list(v) for k, v in (threads or {}).items()}
        self.comments: list[str] = []
        self.labels: list[str] = []

    def gql(self, query, variables=None):
        if "parent" in query:
            return {"issue": {"parent": (
                {"identifier": self.parent} if self.parent else None)}}
        return {
            "issue": {
                "identifier": self.card["card"],
                "title": self.card["title"],
                "description": self.card["body"],
                "labels": {"nodes": [{"name": n} for n in self.card["labels"]]},
                "children": {"nodes": []},
            }
        }

    def comment_bodies(self, identifier, *, whole_thread=False):
        return list(self.threads.get(identifier, []))

    def count_comments(self, identifier, needle, **kwargs):
        return sum(1 for b in self.threads.get(identifier, []) if needle in b)

    def cmd_comment(self, identifier, body, *flags):
        self.comments.append(body)
        self.threads.setdefault(identifier, []).append(body)

    def add_label(self, identifier, label):
        self.labels.append(label)

    def cmd_state(self, identifier, lane, *flags):  # pragma: no cover
        raise AssertionError("classifying a card moves no card")


def _stamped(shape: str) -> list[str]:
    return [planning_shape.shape_comment(shape, "the planner's reading")]


def _why_line(body: str) -> str:
    found = _WHY_LINE.search(body)
    assert found, f"the stamp carries no **Why:** line:\n{body}"
    return found.group(1)


def _as_card(card: dict, **extra) -> dict:
    return dict({"identifier": card["card"], "title": card["title"],
                 "description": card["body"]}, **extra)


# ===========================================================================
# B. the fixture carries the gate text the floor reads
# ===========================================================================
class TestTheFixture:
    def test_the_floor_fires_on_the_childs_gate_text(self):
        """Without this the tests below could pass on a body the floor never
        matched, and prove nothing about the floor."""
        evidence = planning_classify.seam_evidence(CHILD["body"])
        assert evidence and "seven clean days" in evidence

    def test_with_no_roll_up_above_it_the_same_body_is_a_roll_up(self):
        """The defect as the proof saw it: the floor alone upgrades the
        model's `epic` on this body."""
        decision = planning_classify.classify(
            _as_card(CHILD), call=_caller(CHILD_SAYS_EPIC), model=MODEL)
        assert decision.shape == "roll-up"


# ===========================================================================
# A. a roll-up's child keeps the model's epic
# ===========================================================================
class TestARollUpsChild:
    def test_classify_keeps_the_epic_the_model_read(self):
        decision = planning_classify.classify(
            _as_card(CHILD, roll_up_parent=PARENT),
            call=_caller(CHILD_SAYS_EPIC), model=MODEL)
        assert decision.shape == "epic"
        assert MARK not in decision.why

    def test_the_reason_says_why_the_gate_did_not_count(self):
        decision = planning_classify.classify(
            _as_card(CHILD, roll_up_parent=PARENT),
            call=_caller(CHILD_SAYS_EPIC), model=MODEL)
        assert PARENT in decision.why, (
            "a reader of the stamp must see the parent roll-up the gate "
            "text belongs to"
        )

    def test_a_seam_the_model_names_does_not_upgrade_its_own_epic(self):
        """The model echoing the gate as `seam` while answering `epic` is the
        same text the split planner wrote; the shape it chose stands."""
        decision = planning_classify.classify(
            _as_card(CHILD, roll_up_parent=PARENT),
            call=_caller(_answer(
                "epic", "one epic, the gate is in front of it",
                seam="waits on seven clean days of DRE-5771 live")),
            model=MODEL)
        assert decision.shape == "epic"
        assert MARK not in decision.why

    def test_run_stamps_the_child_epic(self):
        lops = _Lops(CHILD, parent=PARENT, threads={PARENT: _stamped("roll-up")})
        decision = planning_classify.run(
            lops, CHILD["card"], call=_caller(CHILD_SAYS_EPIC), model=MODEL)
        assert decision.shape == "epic"
        assert planning_shape.shape_on(lops.threads[CHILD["card"]]) == "epic"
        assert lops.labels == list(planning_shape.marks("epic"))
        assert MARK not in _why_line(lops.comments[0])

    def test_a_child_the_model_reads_as_a_roll_up_is_still_one(self):
        """Nesting is allowed: a child can be a roll-up in turn — on the
        model's reading of it, never on the floor's."""
        seam = "the send job waits on a supervised release of the digest"
        lops = _Lops(CHILD, parent=PARENT, threads={PARENT: _stamped("roll-up")})
        decision = planning_classify.run(
            lops, CHILD["card"],
            call=_caller(_answer("roll-up", "two epics again", seam=seam)),
            model=MODEL)
        assert decision.shape == "roll-up"
        assert f"{MARK} {seam}" in _why_line(lops.comments[0])


# ===========================================================================
# C. a real roll-up, and every card not under one, keeps the floor
# ===========================================================================
class TestTheFloorStillHolds:
    def test_a_real_roll_up_is_still_stamped_roll_up(self):
        card = _fixture("DRE-3164")
        assert card["expect"] == "roll-up"
        lops = _Lops(card, parent=None)
        decision = planning_classify.run(
            lops, card["card"],
            call=_caller(_answer("epic", "thirteen build cards")), model=MODEL)
        assert decision.shape == "roll-up"
        assert planning_shape.shape_on(lops.threads[card["card"]]) == "roll-up"
        assert "clean console releases" in _why_line(lops.comments[0])

    def test_a_parent_that_is_an_ordinary_epic_keeps_the_floor(self):
        lops = _Lops(CHILD, parent=PARENT, threads={PARENT: _stamped("epic")})
        decision = planning_classify.run(
            lops, CHILD["card"], call=_caller(CHILD_SAYS_EPIC), model=MODEL)
        assert decision.shape == "roll-up"

    def test_an_unstamped_parent_keeps_the_floor(self):
        lops = _Lops(CHILD, parent=PARENT, threads={PARENT: []})
        decision = planning_classify.run(
            lops, CHILD["card"], call=_caller(CHILD_SAYS_EPIC), model=MODEL)
        assert decision.shape == "roll-up"

    def test_a_parent_with_two_stamps_keeps_the_floor(self):
        """A parent whose shape cannot be read is not known to be a roll-up,
        and the floor is what it always was."""
        lops = _Lops(CHILD, parent=PARENT, threads={
            PARENT: _stamped("roll-up") + _stamped("epic")})
        decision = planning_classify.run(
            lops, CHILD["card"], call=_caller(CHILD_SAYS_EPIC), model=MODEL)
        assert decision.shape == "roll-up"
