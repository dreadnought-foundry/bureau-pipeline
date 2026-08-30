"""RED-first tests: the planning shape vocabulary — one-off, epic, wave — as
data (DRE-2843).

Planning treats every card the same today: it owes a full plan artifact and it
stops for a green light. There is no way to say "this one is a one-liner" or
"this one is a whole wave", because the vocabulary does not exist anywhere a
machine can read. `config/planning-shapes.json` is that vocabulary, and it is
built to the shape of `config/routing-verdicts.json` rather than to a second
one invented here.

WHAT THIS PINS, one section per acceptance criterion:

  1. The file declares EXACTLY THREE shapes, each with meaning, destination,
     actor, promotability and marks.
  2. Every destination is a lane in `config/lane-contract.json`, and every
     actor is a permitted writer OF THAT LANE — asserted here, not by
     inspection. OPERATOR and WORKBENCH shipped without that binding and sent
     cards to a turn that never came (DRE-2735); PARKED named an actor its own
     destination lane forbids (DRE-2824). Both were config, both were caught by
     a check like this one.
  3. A reader returns the single shape stamped on a card.
  4. A card carrying TWO shapes is refused, with the reason naming BOTH.
  5. A card carrying NONE is distinguishable from a card carrying an
     UNRECOGNISED one — different faults, different messages, different tags.
  6. The file carries its own `_readme` explaining why shape and size are
     separate axes, and the axis split is enforced mechanically, not hoped for:
     `size:XS`…`size:XL` mean EFFORT, and a `size:L` one-off is legitimate.

Run: cd bureau-pipeline && python3 -m pytest tests/test_planning_shape.py -v
"""
from __future__ import annotations

import inspect
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import lane_contract  # noqa: E402
import linear_ops  # noqa: E402
import planning_shape  # noqa: E402
import reconcile  # noqa: E402

CONFIG = ROOT / "config" / "planning-shapes.json"

# The three shapes, written out ONCE here as the thing the module is compared
# against. Every other list in the pipeline is derived from the config file.
THE_THREE = ("one-off", "epic", "wave")


def _mutated() -> dict:
    """A private copy of the shipped vocabulary, for the mutation tests."""
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def _entry(doc: dict, name: str) -> dict:
    return next(e for e in doc["shapes"] if e["name"] == name)


# ===========================================================================
# 1: exactly three shapes, each with the five things a shape owes
# ===========================================================================
class TestTheThreeShapes:
    def test_the_config_is_json_the_stdlib_can_read(self):
        # JSON, not YAML, for the same reason the lane contract and the routing
        # vocabulary are: these readers run on a product repo's agent job, where
        # there is no pip install and PyYAML is not guaranteed.
        with open(CONFIG, encoding="utf-8") as fh:
            doc = json.load(fh)
        assert doc["shapes"], "the vocabulary declares no shapes"

    def test_exactly_the_three_shapes_exist(self):
        assert planning_shape.shapes() == THE_THREE

    @pytest.mark.parametrize("name", THE_THREE)
    def test_every_shape_states_all_five_things(self, name):
        """Meaning, destination, actor, promotability, marks. A shape missing
        one of them is a word, not a route."""
        assert planning_shape.means(name).strip(), f"{name} means nothing"
        assert planning_shape.destination(name), f"{name} goes nowhere"
        assert planning_shape.actor(name), f"{name} names nobody to act"
        assert isinstance(planning_shape.is_promotable(name), bool)
        assert isinstance(planning_shape.marks(name), tuple)

    def test_an_unknown_shape_is_raised_not_defaulted(self):
        """Defaulting an unknown shape to promotable dispatches work nobody
        classified; defaulting it to not-promotable freezes a card with nothing
        saying why."""
        with pytest.raises(planning_shape.UnknownShape):
            planning_shape.destination("saga")


# ===========================================================================
# 2: every destination is a lane, every actor a permitted writer OF THAT LANE
# ===========================================================================
class TestBoundToTheLaneContract:
    def test_every_destination_is_a_lane_the_contract_carries(self):
        live = set(lane_contract.lane_names(status="live"))
        for name in planning_shape.shapes():
            assert planning_shape.destination(name) in live, (
                f"{name} routes to {planning_shape.destination(name)!r}, which "
                "is not a lane in config/lane-contract.json"
            )

    def test_every_actor_is_a_writer_the_contract_defines(self):
        known = set(lane_contract.writers())
        for name in planning_shape.shapes():
            assert planning_shape.actor(name) in known, (
                f"{name}'s actor {planning_shape.actor(name)!r} is not in the "
                "lane contract's writer glossary"
            )

    @pytest.mark.parametrize("name", THE_THREE)
    def test_every_actor_is_a_permitted_writer_of_its_own_destination(self, name):
        """The STRONG half, and the one DRE-2824 was written from: being in the
        glossary somewhere is not being allowed to write the lane this shape
        sends the card to."""
        permitted = set(lane_contract.lane_writers(planning_shape.destination(name)))
        assert planning_shape.actor(name) in permitted, (
            f"{name} goes to {planning_shape.destination(name)!r}, whose "
            f"permitted writers are {sorted(permitted)} — its actor "
            f"{planning_shape.actor(name)!r} is not among them"
        )

    def test_a_destination_that_is_not_a_lane_is_a_config_problem(self):
        doc = _mutated()
        _entry(doc, "one-off")["destination"] = "Somewhere"
        problems = planning_shape.config_problems(doc)
        assert any("one-off" in p and "Somewhere" in p for p in problems), problems

    def test_an_actor_the_destination_forbids_is_a_config_problem(self):
        """The mutation that matters: `operator` is a real writer and is not a
        `Backlog` writer, so a one-off landed in Backlog "for the operator"
        names somebody who may not legally write the lane."""
        doc = _mutated()
        _entry(doc, "one-off")["actor"] = "operator"
        problems = planning_shape.config_problems(doc)
        assert any(
            "one-off" in p and "operator" in p and "Backlog" in p for p in problems
        ), f"a forbidden actor passed the check: {problems}"

    def test_an_actor_outside_the_glossary_is_a_config_problem(self):
        doc = _mutated()
        _entry(doc, "epic")["actor"] = "the-ceo"
        problems = planning_shape.config_problems(doc)
        assert any("epic" in p and "the-ceo" in p for p in problems), problems

    def test_a_shape_missing_a_field_is_a_config_problem(self):
        for key in ("means", "destination", "actor", "why"):
            doc = _mutated()
            _entry(doc, "wave")[key] = "  "
            assert any(
                "wave" in p and key in p for p in planning_shape.config_problems(doc)
            ), f"an empty {key!r} passed the check"

    def test_a_shape_that_does_not_say_whether_it_promotes_is_a_config_problem(self):
        doc = _mutated()
        _entry(doc, "epic").pop("promotable", None)
        assert any(
            "epic" in p and "promot" in p for p in planning_shape.config_problems(doc)
        )

    def test_a_duplicate_shape_name_is_a_config_problem(self):
        doc = _mutated()
        doc["shapes"].append(dict(_entry(doc, "epic")))
        assert any("twice" in p for p in planning_shape.config_problems(doc))

    def test_the_shipped_vocabulary_has_no_problems_at_all(self):
        assert planning_shape.config_problems() == []


# ===========================================================================
# 1 (cont.): promotability and marks are the existing signals, not new ones
# ===========================================================================
class TestPromotabilityAndMarks:
    def test_the_one_off_is_the_only_shape_the_sweep_may_promote(self):
        promotable = [n for n in planning_shape.shapes() if planning_shape.is_promotable(n)]
        assert promotable == ["one-off"], (
            "an epic and a wave are moved by the people who approve them; the "
            "sweep promotes neither"
        )

    def test_more_than_one_promotable_shape_is_a_config_problem(self):
        doc = _mutated()
        _entry(doc, "epic")["promotable"] = True
        assert any("promot" in p for p in planning_shape.config_problems(doc))

    @pytest.mark.parametrize("name", ["epic", "wave"])
    def test_the_planned_shapes_carry_the_mark_that_already_stops_the_sweep(self, name):
        """`agent:planner` is the existing signal that a card is a container the
        planner owns — reuse it rather than invent a second one, exactly as
        WORKBENCH reuses `hand-built` (DRE-2724)."""
        assert "agent:planner" in planning_shape.marks(name)

    def test_the_mark_this_rests_on_really_stops_the_sweep(self):
        """The claim above is only true while the sweep honours the label. If
        `promote_ready` stops skipping `agent:planner`, marking an epic with it
        stops meaning "not promotable" and this vocabulary is lying."""
        source = inspect.getsource(reconcile.promote_ready)
        assert "agent:planner" in source, (
            "reconcile.promote_ready no longer skips agent:planner cards — the "
            "epic and wave marks no longer mean what this file says they mean"
        )

    def test_a_one_off_carries_no_marks(self):
        assert planning_shape.marks("one-off") == ()


# ===========================================================================
# 6: shape is not size — a `size:L` one-off is legitimate
# ===========================================================================
class TestShapeIsNotSize:
    def test_the_file_explains_the_two_axes_in_its_own_readme(self):
        """AC6. The next reader must not merge them, and the place they will
        look is the file itself."""
        readme = "\n".join(planning_shape.load()["_readme"]).lower()
        assert "size" in readme and "effort" in readme
        assert "shape" in readme
        assert "size:" in readme, "the readme must name the label prefix it is not"

    @pytest.mark.parametrize("name", THE_THREE)
    def test_no_shape_is_named_with_the_size_prefix(self, name):
        assert not name.startswith("size")
        assert name == name.lower()

    def test_a_shape_named_with_the_size_prefix_is_a_config_problem(self):
        """Mechanical, so the merge cannot happen quietly later: `size:` is
        taken, it means effort, and putting two questions behind one word is
        the naming failure the Plan/Entitlement rule exists to prevent
        (DRE-1494)."""
        doc = _mutated()
        _entry(doc, "wave")["name"] = "size:XL"
        assert any("size" in p for p in planning_shape.config_problems(doc))

    def test_a_shape_that_applies_a_size_label_is_a_config_problem(self):
        """The other half of the same axis: a shape stamp must never write
        effort onto a card."""
        doc = _mutated()
        _entry(doc, "epic")["marks"].append("size:XL")
        assert any("size" in p for p in planning_shape.config_problems(doc))

    def test_a_size_label_on_the_card_does_not_disturb_the_shape_read(self):
        """A `size:L` one-off is legitimate — the two axes are read from
        different places and neither shadows the other."""
        body = planning_shape.shape_comment("one-off", "one card, one PR")
        assert planning_shape.shape_on([body, "size:L", "🏷️ size:XL applied"]) == "one-off"


# ===========================================================================
# 3: a reader returns the single shape stamped on a card
# ===========================================================================
class TestExactlyOneShape:
    def test_a_shape_comment_is_machine_readable(self):
        body = planning_shape.shape_comment("one-off", "one card, one PR")
        assert planning_shape.shape_on([body]) == "one-off"

    @pytest.mark.parametrize("name", THE_THREE)
    def test_every_shape_round_trips_through_a_comment(self, name):
        body = planning_shape.shape_comment(name, "because")
        assert planning_shape.shape_on([body]) == name

    def test_a_shape_comment_states_the_destination_and_the_actor(self):
        body = planning_shape.shape_comment("epic", "it is a set of cards")
        assert planning_shape.destination("epic") in body
        assert planning_shape.actor("epic") in body

    def test_a_shape_comment_requires_a_reason(self):
        with pytest.raises(planning_shape.ShapeError):
            planning_shape.shape_comment("wave", "   ")

    def test_a_card_with_no_shape_reads_as_none(self):
        assert planning_shape.shape_on(["🤖 dispatched", "looks like an epic to me"]) is None

    def test_the_marker_must_OPEN_the_comment_not_merely_appear_in_it(self):
        """The adversarial case, and the one that bites: this module's own fault
        notices NAME the shape they are complaining about. A reader that matched
        anywhere in the body would read a complaint back as the stamp."""
        chatty = (
            "🚨 planning-two-shapes: this card carries planning-shape: epic and "
            "planning-shape: wave, so nothing is reading a shape off it."
        )
        assert planning_shape.shape_on([chatty]) is None

    def test_the_same_shape_twice_is_still_one_shape(self):
        body = planning_shape.shape_comment("wave", "a programme of epics")
        assert planning_shape.shape_on([body, body]) == "wave"

    def test_a_stamp_written_in_mixed_case_still_reads(self):
        """Tolerant on read, strict on write — a human retyping the marker in
        Linear must not silently produce a card with no shape."""
        assert planning_shape.shape_on(["🧩 planning-shape: **One-Off** — a one-liner"]) == "one-off"


# ===========================================================================
# 4: a card carrying two shapes is refused, and the reason names both
# ===========================================================================
class TestTwoShapesAreRefused:
    def _two(self):
        return [
            planning_shape.shape_comment("one-off", "one card, one PR"),
            planning_shape.shape_comment("wave", "a programme of epics"),
        ]

    def test_two_shapes_are_a_conflict_not_a_pick(self):
        with pytest.raises(planning_shape.ConflictingShapes):
            planning_shape.shape_on(self._two())

    def test_the_conflict_names_both_shapes(self):
        with pytest.raises(planning_shape.ConflictingShapes) as caught:
            planning_shape.shape_on(self._two())
        message = str(caught.value)
        assert "one-off" in message and "wave" in message

    def test_the_fault_notice_names_both_shapes(self):
        fault = planning_shape.fault("DRE-1", self._two())
        assert fault is not None
        assert "one-off" in fault and "wave" in fault

    def test_the_write_path_refuses_a_second_conflicting_shape(self):
        existing = [planning_shape.shape_comment("one-off", "one card, one PR")]
        problem = planning_shape.stamp_refusal("epic", existing)
        assert problem is not None and "one-off" in problem

    def test_the_write_path_refuses_a_duplicate_stamp(self):
        existing = [planning_shape.shape_comment("epic", "it is a set of cards")]
        assert planning_shape.stamp_refusal("epic", existing) is not None

    def test_the_write_path_is_clean_on_a_fresh_card(self):
        assert planning_shape.stamp_refusal("epic", []) is None

    def test_the_write_path_refuses_a_shape_the_vocabulary_does_not_carry(self):
        with pytest.raises(planning_shape.UnknownShape):
            planning_shape.stamp_refusal("saga", [])


# ===========================================================================
# 5: none and unrecognised are different faults with different messages
# ===========================================================================
SAGA = "🧩 planning-shape: **saga** — several epics that share a database"


class TestNoneIsNotUnrecognised:
    def test_a_card_with_no_shape_reads_none_and_a_card_with_a_bad_one_raises(self):
        assert planning_shape.shape_on([]) is None
        with pytest.raises(planning_shape.UnknownShape):
            planning_shape.shape_on([SAGA])

    def test_the_unrecognised_name_is_reported_back(self):
        assert planning_shape.unrecognised_on([SAGA]) == ("saga",)
        assert planning_shape.unrecognised_on([]) == ()

    def test_the_two_faults_carry_different_tags(self):
        none = planning_shape.fault("DRE-1", [])
        unknown = planning_shape.fault("DRE-1", [SAGA])
        assert planning_shape.fault_tag(none) == planning_shape.NO_SHAPE_TAG
        assert planning_shape.fault_tag(unknown) == planning_shape.UNKNOWN_SHAPE_TAG
        assert planning_shape.NO_SHAPE_TAG != planning_shape.UNKNOWN_SHAPE_TAG

    def test_the_two_faults_say_different_things(self):
        none = planning_shape.fault("DRE-1", [])
        unknown = planning_shape.fault("DRE-1", [SAGA])
        assert none != unknown
        assert "saga" not in none, "a card with no shape must not be told about a word"
        assert "saga" in unknown, "the unrecognised word is the whole point of the message"

    def test_the_unrecognised_fault_names_the_shapes_that_do_exist(self):
        unknown = planning_shape.fault("DRE-1", [SAGA])
        for name in THE_THREE:
            assert name in unknown

    def test_the_no_shape_fault_says_how_to_stamp_one(self):
        none = planning_shape.fault("DRE-1", [])
        assert "DRE-1" in none
        assert "planning_shape.py" in none, "the fix belongs in the message"

    def test_a_conflict_is_a_third_fault_with_a_third_tag(self):
        both = [
            planning_shape.shape_comment("epic", "a set of cards"),
            planning_shape.shape_comment("wave", "a programme of epics"),
        ]
        conflict = planning_shape.fault("DRE-1", both)
        assert planning_shape.fault_tag(conflict) == planning_shape.TWO_SHAPES_TAG
        assert planning_shape.TWO_SHAPES_TAG not in (
            planning_shape.NO_SHAPE_TAG,
            planning_shape.UNKNOWN_SHAPE_TAG,
        )

    def test_a_well_shaped_card_has_no_fault_at_all(self):
        body = planning_shape.shape_comment("one-off", "one card, one PR")
        assert planning_shape.fault("DRE-1", [body]) is None

    def test_a_fault_tag_is_read_off_the_notice_not_guessed(self):
        assert planning_shape.fault_tag("🤖 something else entirely") is None
        assert planning_shape.fault_tag(None) is None

    def test_an_unrecognised_shape_alongside_a_real_one_is_still_read(self):
        """The recognised stamp is the decision; an unknown word next to it is
        noise, and the fault says so rather than losing the real shape."""
        body = planning_shape.shape_comment("one-off", "one card, one PR")
        assert planning_shape.shape_on([body, SAGA]) == "one-off"
        fault = planning_shape.fault("DRE-1", [body, SAGA])
        assert fault is not None and "saga" in fault


# ===========================================================================
# The vocabulary is written down where the next reader will look
# ===========================================================================
class TestTheVocabularyIsWrittenDown:
    def test_the_config_readme_names_the_new_data_file(self):
        text = (ROOT / "config" / "README.md").read_text(encoding="utf-8")
        assert "planning-shapes.json" in text

    def test_a_fourth_shape_would_be_a_data_edit_and_nothing_else(self):
        """The vocabulary is the config file, not a list in the module. A
        reader that named its own members would be a second vocabulary, and the
        two would drift the first time one was edited."""
        doc = _mutated()
        doc["shapes"].append({
            "name": "spike",
            "means": "a timeboxed investigation that produces a finding, not a PR.",
            "destination": "Planning",
            "actor": "plan.yml",
            "promotable": False,
            "marks": ["agent:planner"],
            "why": "a fixture — proof the reader reads the file rather than itself.",
        })
        assert planning_shape.shapes(doc) == THE_THREE + ("spike",)
        assert planning_shape.config_problems(doc) == []
        assert planning_shape.destination("spike", doc) == "Planning"

    def test_a_document_passed_in_is_the_document_read(self):
        """`None` means "read the shipped file", and nothing else does. A doc
        that is merely EMPTY was still passed in deliberately — silently
        answering from the real config instead would answer a question nobody
        asked, which is the guessing this module exists to refuse."""
        with pytest.raises(planning_shape.ShapeError):
            planning_shape.shapes({})


# ===========================================================================
# The CLI — the half a caller actually runs, and where the shape gets lost
# ===========================================================================
class _Card:
    """One card's comments, with the CLI's writes recorded rather than posted.

    `_cmd_read`/`_cmd_stamp` import `linear_ops` inside the function body, so
    patching the module object is what reaches them.
    """

    def __init__(self, comments):
        self.comments = list(comments)
        self.posted: list[tuple[str, str]] = []
        self.labelled: list[tuple[str, str]] = []

    def run(self, fn):
        with patch.object(
            linear_ops, "comment_bodies", side_effect=lambda i: list(self.comments)
        ), patch.object(
            linear_ops, "cmd_comment",
            side_effect=lambda i, b: self.posted.append((i, b)),
        ), patch.object(
            linear_ops, "add_label",
            side_effect=lambda i, label: self.labelled.append((i, label)),
        ):
            return fn()


class TestTheReadCommand:
    def test_a_clean_card_reads_the_whole_record(self, capsys):
        card = _Card([planning_shape.shape_comment("wave", "a programme of epics")])
        assert card.run(lambda: planning_shape._cmd_read("DRE-1")) == 0
        out = capsys.readouterr()
        assert json.loads(out.out) == {
            "shape": "wave",
            "means": planning_shape.means("wave"),
            "destination": planning_shape.destination("wave"),
            "actor": planning_shape.actor("wave"),
            "promotable": planning_shape.is_promotable("wave"),
            "marks": list(planning_shape.marks("wave")),
        }
        assert out.err == "", "a card with nothing wrong with it says nothing"

    def test_a_stray_stamp_beside_a_real_one_does_not_lose_the_shape(self, capsys):
        """The recognised stamp IS the decision (`shape_on`), so a read that
        bailed on the noise beside it would report a fully classified card as
        unclassified — and anything downstream would stall on it."""
        body = planning_shape.shape_comment("one-off", "one card, one PR")
        card = _Card([body, SAGA])
        assert card.run(lambda: planning_shape._cmd_read("DRE-1")) == 0
        out = capsys.readouterr()
        assert json.loads(out.out)["shape"] == "one-off"
        assert "saga" in out.err, "the stray word is still worth saying"

    def test_an_unclassified_card_fails_the_read(self, capsys):
        card = _Card(["🤖 dispatched", "looks like an epic to me"])
        assert card.run(lambda: planning_shape._cmd_read("DRE-1")) == 1
        out = capsys.readouterr()
        assert out.out == "", "nothing may be printed as if it were a shape"
        assert planning_shape.fault_tag(out.err) == planning_shape.NO_SHAPE_TAG

    def test_a_card_stamped_only_with_an_unknown_word_fails_the_read(self, capsys):
        card = _Card([SAGA])
        assert card.run(lambda: planning_shape._cmd_read("DRE-1")) == 1
        out = capsys.readouterr()
        assert out.out == ""
        assert planning_shape.fault_tag(out.err) == planning_shape.UNKNOWN_SHAPE_TAG

    def test_two_shapes_fail_the_read_rather_than_one_being_picked(self, capsys):
        card = _Card([
            planning_shape.shape_comment("epic", "a set of cards"),
            planning_shape.shape_comment("wave", "a programme of epics"),
        ])
        assert card.run(lambda: planning_shape._cmd_read("DRE-1")) == 1
        out = capsys.readouterr()
        assert out.out == ""
        assert planning_shape.fault_tag(out.err) == planning_shape.TWO_SHAPES_TAG


class TestTheStampCommand:
    def test_stamping_a_clean_card_writes_the_comment_and_the_marks(self, capsys):
        card = _Card([])
        assert card.run(
            lambda: planning_shape._cmd_stamp("DRE-1", "epic", "a set of cards")
        ) == 0
        assert [identifier for identifier, _ in card.posted] == ["DRE-1"]
        assert planning_shape.shape_on([body for _, body in card.posted]) == "epic"
        assert [label for _, label in card.labelled] == list(planning_shape.marks("epic"))

    def test_a_card_already_stamped_is_refused_and_nothing_is_written(self, capsys):
        card = _Card([planning_shape.shape_comment("wave", "a programme of epics")])
        assert card.run(
            lambda: planning_shape._cmd_stamp("DRE-1", "epic", "a set of cards")
        ) == 1
        assert card.posted == [] and card.labelled == []
        assert "wave" in capsys.readouterr().err

    def test_a_stamp_written_over_a_stray_word_reads_back(self, capsys):
        """The round trip the two halves have to agree on: an unrecognised stray
        does not stop a real stamp being WRITTEN (`stamp_refusal` reads
        recognised shapes only), so it must not stop that same stamp being READ
        a moment later."""
        card = _Card([SAGA])
        assert card.run(
            lambda: planning_shape._cmd_stamp("DRE-1", "one-off", "one card, one PR")
        ) == 0
        card.comments.extend(body for _, body in card.posted)
        capsys.readouterr()
        assert card.run(lambda: planning_shape._cmd_read("DRE-1")) == 0
        assert json.loads(capsys.readouterr().out)["shape"] == "one-off"


class TestTheCommandLine:
    def test_check_is_the_default_command_and_the_shipped_file_passes(self, capsys):
        assert planning_shape.main([]) == 0
        assert "0 problem(s)" in capsys.readouterr().out
        assert planning_shape.main(["check"]) == 0

    def test_read_reaches_the_read_command(self, capsys):
        card = _Card([planning_shape.shape_comment("epic", "a set of cards")])
        assert card.run(lambda: planning_shape.main(["read", "DRE-1"])) == 0
        assert json.loads(capsys.readouterr().out)["shape"] == "epic"

    def test_stamp_reaches_the_stamp_command_and_owes_a_reason(self):
        card = _Card([])
        assert card.run(
            lambda: planning_shape.main(
                ["stamp", "DRE-1", "wave", "--why", "a programme of epics"]
            )
        ) == 0
        assert planning_shape.shape_on([body for _, body in card.posted]) == "wave"
        with pytest.raises(SystemExit):
            planning_shape.main(["stamp", "DRE-1", "wave"])


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
