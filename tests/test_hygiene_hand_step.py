"""RED-first: the hygiene agent's Hand-work class lane (DRE-8650).

`scripts/hygiene_hand_step.py` marks every operator step in `Hand-work` as one
a machine could do or one a person must, by `operator_step_kind.classify`. A
card is classed when it is in Hand-work, carries `operator-step` or a newest
routing verdict whose actor is the operator, wears neither class label, is not
a `PROOF:` card or a standing card in `config/hand-work-age.json`, and does not
wear the CEO's `hand-built` mark. Each is one `hygiene.Action`: the label
`hand-step:<class>`, then hygiene's own receipt naming the class, the kind and
the evidence. It moves no card and dispatches nothing.

Every board card here is in the `board_snapshot.CARD_QUERY` shape.

Run: cd bureau-pipeline && python3 -m pytest tests/test_hygiene_hand_step.py -v
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import hygiene  # noqa: E402
import pipeline_act  # noqa: E402
import routing_verdict  # noqa: E402

MODULE_PATH = ROOT / "scripts" / "hygiene_hand_step.py"

NOW = datetime(2026, 11, 2, 20, 0, tzinfo=UTC)
HOME = "dreadnought-foundry"
OWNERS = ("dreadnought-foundry", "EveryBite", "DeltaSolv")

ACT = "hygiene-hand-step-class"
TAG = "hyg-hand-step"
MACHINE = "hand-step:machine"
PERSON = "hand-step:person"

#: DRE-7328's title and its step line, as Linear answered them.
VARIABLE_TITLE = ("OPERATOR: set NO_RECORD_RETURN_MODE=enforce on the live relay, then "
                  "close this card through the console's Record the outcome")
VARIABLE_BODY = ("Turn the relay's no-record return on, live. The code ships behind "
                 "`NO_RECORD_RETURN_MODE`, which defaults to `off`, and only the bootstrap "
                 "deploy sets a relay environment variable.")


def load_lane():
    if not MODULE_PATH.exists():
        return None
    spec = importlib.util.spec_from_file_location("hygiene_hand_step", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lane = load_lane()


def verdict(name: str) -> str:
    return routing_verdict.verdict_comment(name, "synthetic reason")


def board_card(ident, lane_name="Hand-work", *, title=VARIABLE_TITLE,
               description=VARIABLE_BODY, labels=("operator-step", "no-code"),
               slug=None, comments=()):
    """One card in the `board_snapshot.CARD_QUERY` shape. Comments are given
    oldest-first and stored newest first, the way Linear answers the window."""
    names = list(labels) + ([f"repo:{slug}"] if slug else [])
    return {
        "id": f"uuid-{ident}",
        "identifier": ident,
        "title": title,
        "description": description,
        "createdAt": "2026-10-01T00:00:00.000Z",
        "updatedAt": "2026-11-02T00:00:00.000Z",
        "state": {"name": lane_name},
        "labels": {"nodes": [{"name": n} for n in names]},
        "parent": None,
        "children": {"nodes": []},
        "comments": {"pageInfo": {"hasNextPage": False, "endCursor": None},
                     "nodes": [{"body": b, "createdAt": "2026-11-01T00:00:00.000Z",
                                "user": None} for b in reversed(list(comments))]},
        "relations": {"pageInfo": {"hasNextPage": False}, "nodes": []},
        "inverseRelations": {"pageInfo": {"hasNextPage": False}, "nodes": []},
        "history": {"nodes": []},
    }


def whole(cards):
    """Each card's full description as `issue(id:)` answers it: the board's own
    text, for a card shorter than the list API's cut."""
    return {c["identifier"]: c["description"] for c in cards}


def context(owner=HOME, full=None, reads=None):
    """`full` is each card's whole description, the one read the lane makes for
    itself; a card it does not hold fails that read. `reads` collects the ids
    read."""
    full = dict(full or {})

    def issue(query, variables=None):
        ident = (variables or {}).get("id")
        if reads is not None:
            reads.append(ident)
        if ident not in full:
            raise hygiene.linear_ops.LinearError(f"synthetic: {ident} could not be read")
        return {"issue": {"identifier": ident, "description": full[ident]}}
    return hygiene.make_context(owner, gh=lambda argv: "[]",
                                linear=hygiene.read_only_linear(issue),
                                now=NOW, summary_card="DRE-900")


def board_of(cards):
    lanes: dict = {}
    for c in cards:
        lanes.setdefault(c["state"]["name"], []).append(c)
    return hygiene.Board(lanes=lanes, prs={})


def plan(cards, owner=HOME, full=None, reads=None):
    assert lane is not None, f"{MODULE_PATH.name} does not exist yet"
    return lane.plan(board_of(cards),
                     context(owner, full=whole(cards) if full is None else full,
                             reads=reads))


def actions(items):
    return [i for i in items if isinstance(i, hygiene.Action)]


def the_action(items, ident):
    found = actions(items)
    assert len(found) == 1, found
    action = found[0]
    assert action.act == ACT and action.target == ident
    return action


# --------------------------------------------------------------------------- #
# the main case                                                                #
# --------------------------------------------------------------------------- #


class TestTheMainCase:
    def test_one_action_adds_the_class_label_then_posts_one_receipt(self):
        action = the_action(plan([board_card("DRE-7328")]), "DRE-7328")
        assert [w.kind for w in action.writes] == ["linear_label", "linear_comment"]
        label, comment = action.writes
        assert (label.card, label.label, label.add) == ("DRE-7328", MACHINE, True)
        assert comment.card == "DRE-7328"
        assert comment.body.startswith(f"🧹 hygiene: {TAG} —")

    def test_the_receipt_names_the_class_the_kind_and_the_evidence(self):
        action = the_action(plan([board_card("DRE-7328")]), "DRE-7328")
        lines = action.writes[1].body.splitlines()
        assert lines[0] == (f"🧹 hygiene: {TAG} — machine, kind variable · "
                            f"{hygiene.pt(NOW)}")
        assert lines[1] == "evidence: environment variable"
        assert action.cause == "machine, kind variable"
        assert action.evidence == ["environment variable"]

    def test_the_receipt_is_hygienes_own_and_carries_the_acts_trailer(self):
        action = the_action(plan([board_card("DRE-7328")]), "DRE-7328")
        body = action.writes[1].body
        assert body == hygiene.receipt(ACT, action.cause, action.evidence, NOW)
        fields = pipeline_act.read_trailer(body)
        assert fields["act"] == ACT and fields["tag"] == TAG

    def test_a_person_card_takes_the_person_label(self):
        card = board_card("DRE-7874",
                          title="OPERATOR: install the launcher on the operator's Mac",
                          description="This is that refresh, done on the Mac.")
        action = the_action(plan([card]), "DRE-7874")
        assert action.writes[0].label == PERSON
        lines = action.writes[1].body.splitlines()
        assert lines[0] == (f"🧹 hygiene: {TAG} — person, kind person · {hygiene.pt(NOW)}")
        assert lines[1] == "evidence: the operator's Mac"

    def test_no_phrase_is_a_person_saying_no_machine_kind_matched(self):
        card = board_card("DRE-500", title="agent-bureau: agree the onboarding wording",
                          description="Read the notes and say what was decided.")
        action = the_action(plan([card]), "DRE-500")
        assert action.writes[0].label == PERSON
        assert action.writes[1].body.splitlines()[1] == "evidence: no machine kind matched"

    def test_a_verdict_whose_actor_is_the_operator_qualifies_without_the_label(self):
        assert routing_verdict.actor("WORKBENCH") == "operator"
        card = board_card("DRE-501", labels=(), comments=[verdict("WORKBENCH")])
        action = the_action(plan([card]), "DRE-501")
        assert action.writes[0].label == MACHINE

    def test_every_writer_and_guard_accepts_it(self):
        card = board_card("DRE-7328")
        ctx = context(full=whole([card]))
        action = the_action(plan([card]), "DRE-7328")
        for write in action.writes:
            hygiene.guard(write, ctx)


# --------------------------------------------------------------------------- #
# the evidence a receipt can carry                                             #
# --------------------------------------------------------------------------- #


class TestTheEvidenceLine:
    def test_a_line_break_in_the_quoted_phrase_becomes_a_space(self):
        card = board_card("DRE-502", title="synthetic",
                          description="The operator does it from their own\nsession.")
        action = the_action(plan([card]), "DRE-502")
        assert action.writes[1].body.splitlines()[1] == "evidence: from their own session"

    def test_a_middle_dot_in_the_quoted_phrase_becomes_a_space(self, monkeypatch):
        monkeypatch.setattr(lane.operator_step_kind, "classify",
                            lambda title, description: ("machine", "script", "run·it"))
        action = the_action(plan([board_card("DRE-503")]), "DRE-503")
        assert action.writes[1].body.splitlines()[1] == "evidence: run it"
        assert "·" not in action.cause


# --------------------------------------------------------------------------- #
# the card read whole                                                          #
# --------------------------------------------------------------------------- #

#: Linear's list API cuts `description` here (`board_snapshot`).
LIST_CUT = 500
#: Prose that names no phrase of either side, to push a phrase past the cut.
FILLER = "The relay keeps its records for a month and the console reads them. " * 9


def cut(full: dict) -> list:
    """Board cards whose description is the list API's, cut at `LIST_CUT`."""
    return [board_card(ident, title="synthetic", description=text[:LIST_CUT])
            for ident, text in full.items()]


class TestTheCardIsReadWhole:
    def test_a_machine_phrase_past_the_cut_is_read_from_the_whole_card(self):
        full = {"DRE-510": FILLER + "Then set the relay environment variable."}
        assert len(FILLER) > LIST_CUT
        action = the_action(plan(cut(full), full=full), "DRE-510")
        assert action.writes[0].label == MACHINE
        assert action.cause == "machine, kind variable"
        assert action.evidence == ["environment variable"]

    def test_a_person_phrase_past_the_cut_wins_over_a_machine_phrase_before_it(self):
        full = {"DRE-511": ("Run bash infra/rotate.sh first. " + FILLER
                            + "The operator does it from their own signed-in session.")}
        action = the_action(plan(cut(full), full=full), "DRE-511")
        assert action.writes[0].label == PERSON
        assert action.cause == "person, kind person"
        assert action.evidence == ["own signed-in session"]

    def test_a_card_whose_whole_read_fails_is_skipped_never_classed_from_the_cut(
            self, capsys):
        cards = cut({"DRE-512": "Run bash infra/rotate.sh first. " + FILLER})
        assert plan(cards, full={}) == []
        assert "DRE-512" in capsys.readouterr().err

    def test_one_failed_read_does_not_skip_the_card_beside_it(self):
        full = {"DRE-513": "Run bash infra/rotate.sh first."}
        cards = cut(full) + cut({"DRE-514": "Run bash infra/rotate.sh first."})
        the_action(plan(cards, full=full), "DRE-513")

    def test_only_a_card_that_qualifies_is_read(self):
        reads: list = []
        cards = [board_card("DRE-7328"),
                 board_card("DRE-515", labels=("operator-step", MACHINE)),
                 board_card("DRE-516", "Queued")]
        the_action(plan(cards, reads=reads), "DRE-7328")
        assert reads == ["DRE-7328"]

    def test_the_read_is_the_cards_own_description_and_reads_only(self):
        reads: list = []
        ctx = context(full=whole([board_card("DRE-7328")]), reads=reads)
        lane.plan(board_of([board_card("DRE-7328")]), ctx)
        assert reads == ["DRE-7328"]
        assert len(ctx.linear.calls) == 1
        assert "description" in ctx.linear.calls[0]
        assert "mutation" not in ctx.linear.calls[0]


# --------------------------------------------------------------------------- #
# what is not classed                                                          #
# --------------------------------------------------------------------------- #


class TestWhatIsNotClassed:
    @pytest.mark.parametrize("label", [MACHINE, PERSON, "Hand-Step:Machine"])
    def test_a_card_already_wearing_a_class_label(self, label):
        card = board_card("DRE-600", labels=("operator-step", label))
        assert plan([card]) == []

    def test_a_proof_card(self):
        card = board_card("DRE-601", title="PROOF: the operator steps are classed")
        assert plan([card]) == []

    def test_a_standing_card(self):
        standing = json.loads((ROOT / "config" / "hand-work-age.json").read_text("utf-8"))
        ident = standing["standing"][0]["card"]
        assert plan([board_card(ident)]) == []

    @pytest.mark.parametrize("mark", ["hand-built", "Hand-Built"])
    def test_a_hand_built_card(self, mark):
        card = board_card("DRE-602", labels=("operator-step", mark))
        assert plan([card]) == []

    @pytest.mark.parametrize("lane_name", ["Queued", "In Progress", "In Review", "Intake",
                                           "Green Light", "Triage"])
    def test_a_card_in_any_other_lane(self, lane_name):
        assert plan([board_card("DRE-603", lane_name)]) == []

    def test_a_hand_work_card_with_no_operator_mark_and_no_verdict(self):
        assert plan([board_card("DRE-604", labels=("no-code",))]) == []

    def test_a_card_whose_newest_verdict_is_not_the_operators(self):
        # WORKBENCH first, then FLEET: the newest decides.
        assert routing_verdict.actor("FLEET") != "operator"
        card = board_card("DRE-605", labels=(),
                          comments=[verdict("WORKBENCH"), verdict("FLEET")])
        assert plan([card]) == []

    def test_a_card_whose_newest_verdict_is_the_operators_after_another(self):
        card = board_card("DRE-606", labels=(),
                          comments=[verdict("FLEET"), verdict("OPERATOR")])
        the_action(plan([card]), "DRE-606")

    def test_the_standing_summary_card(self):
        assert plan([board_card("DRE-900")]) == []

    def test_proof_standing_and_hand_built_beside_one_that_is_classed(self):
        standing = json.loads((ROOT / "config" / "hand-work-age.json").read_text("utf-8"))
        cards = [board_card("DRE-601", title="PROOF: synthetic"),
                 board_card(standing["standing"][0]["card"]),
                 board_card("DRE-602", labels=("operator-step", "hand-built")),
                 board_card("DRE-607")]
        assert [a.target for a in actions(plan(cards))] == ["DRE-607"]


# --------------------------------------------------------------------------- #
# which leg                                                                    #
# --------------------------------------------------------------------------- #


class TestWhichLeg:
    @pytest.mark.parametrize("slug,ident", [("agent-bureau", "DRE-7328"),
                                            ("portico", "DRE-7301")])
    def test_classed_by_the_dreadnought_foundry_leg_and_no_other(self, slug, ident):
        card = board_card(ident, slug=slug)
        legs = {owner: actions(plan([card], owner=owner)) for owner in OWNERS}
        assert [a.target for a in legs[HOME]] == [ident]
        assert legs["EveryBite"] == [] and legs["DeltaSolv"] == []

    def test_scope_is_hygienes_in_scope_unchanged(self):
        card = board_card("DRE-608", slug="atlas")
        assert [a.target for a in actions(plan([card], owner="EveryBite"))] == ["DRE-608"]
        assert actions(plan([card], owner=HOME)) == []


# --------------------------------------------------------------------------- #
# through the core — once, and nothing else                                    #
# --------------------------------------------------------------------------- #


class Recorder:
    """`hygiene.send`, applying each write to the board document the way
    Linear would, so the next pass reads what this one wrote."""

    def __init__(self, board_doc):
        self.board_doc = board_doc
        self.sent: list = []

    def card(self, ident):
        return next(c for cards in self.board_doc["lanes"].values() for c in cards
                    if c["identifier"] == ident)

    def __call__(self, write, ctx):
        self.sent.append(write)
        card = self.card(write.card)
        if write.kind == "linear_label":
            card["labels"]["nodes"].append({"name": write.label})
        elif write.kind == "linear_comment":
            card["comments"]["nodes"].insert(0, {"body": write.body,
                                                 "createdAt": "2026-11-02T20:00:00.000Z",
                                                 "user": None})
        else:
            raise AssertionError(f"the lane sent a {write.kind} write")


def leg_context(board_doc):
    return context(full=whole(board_doc["lanes"]["Hand-work"]))


def only_this_lane(monkeypatch):
    monkeypatch.setattr(hygiene, "discover", lambda lane_dir=None: [lane])


class TestThroughTheCore:
    def test_a_second_pass_over_the_same_board_plans_nothing(self, monkeypatch):
        only_this_lane(monkeypatch)
        board_doc = {"lanes": {"Hand-work": [board_card("DRE-7328")]}}
        recorder = Recorder(board_doc)
        monkeypatch.setattr(hygiene, "send", recorder)
        first = hygiene.run_leg(board_doc, leg_context(board_doc))
        assert [a["outcome"] for a in first["actions"]] == ["executed"]
        assert [w.kind for w in recorder.sent] == ["linear_label", "linear_comment"]
        second = hygiene.run_leg(board_doc, leg_context(board_doc))
        assert second["actions"] == [] and second["left"] == []
        assert len(recorder.sent) == 2

    def test_a_receipt_with_no_label_is_suppressed_by_tag_and_cause(self, monkeypatch):
        only_this_lane(monkeypatch)
        receipt = hygiene.receipt(ACT, "machine, kind variable", ["environment variable"],
                                  NOW)
        board_doc = {"lanes": {"Hand-work": [board_card("DRE-7328", comments=[receipt])]}}
        recorder = Recorder(board_doc)
        monkeypatch.setattr(hygiene, "send", recorder)
        ledger = hygiene.run_leg(board_doc, leg_context(board_doc))
        assert [a["outcome"] for a in ledger["actions"]] == ["suppressed"]
        assert recorder.sent == []

    def test_it_moves_no_card_and_dispatches_nothing(self, monkeypatch):
        only_this_lane(monkeypatch)
        cards = [board_card("DRE-7328"),
                 board_card("DRE-7874", title="install it on the operator's Mac")]
        board_doc = {"lanes": {"Hand-work": cards}}
        recorder = Recorder(board_doc)
        monkeypatch.setattr(hygiene, "send", recorder)
        hygiene.run_leg(board_doc, leg_context(board_doc))
        assert {w.kind for w in recorder.sent} == {"linear_label", "linear_comment"}
        assert all(c["state"]["name"] == "Hand-work" for c in board_doc["lanes"]["Hand-work"])

    def test_the_lane_is_discovered_by_the_glob(self):
        names = [m.__name__ for m in hygiene.discover()]
        assert "hygiene_hand_step" in names


# --------------------------------------------------------------------------- #
# the registry                                                                 #
# --------------------------------------------------------------------------- #


class TestTheRegistry:
    def test_the_act_and_its_tag_are_in_the_core_table(self):
        assert hygiene.TAGS[ACT] == TAG

    def test_the_act_is_declared_in_the_registry(self):
        doc = json.loads((ROOT / "config" / "pipeline-acts.json").read_text("utf-8"))
        rows = [r for r in doc["acts"] if r["name"] == ACT]
        assert len(rows) == 1
        row = rows[0]
        assert row["tag"] == TAG
        assert row["emits"] == {"file": "scripts/hygiene.py", "anchor": f'"{TAG}"'}
        assert row["state"] == "unchanged" and row["next_actor"] == "operator"
        assert row["discharges"] is None and row["cadence_s"] is None

    def test_the_lane_names_the_act_the_registry_declares(self):
        assert lane.ACT == ACT
        assert lane.LABELS == {"machine": MACHINE, "person": PERSON}
