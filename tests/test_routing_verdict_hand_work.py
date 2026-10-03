"""WORKBENCH and OPERATOR point at Hand-work (DRE-5321, epic DRE-5240).

A person's work gets the person's lane. Todo is the build button: a card that
enters it makes the relay dispatch a run. Since DRE-3385 the sweep has carried
WORKBENCH and OPERATOR cards into Todo marked `hand-built`, so one lane meant
both "about to build" and "waiting on a person, for days". DRE-5320 made
`Hand-work` a live lane; this card points the two verdicts whose actor is a
person at it.

That makes "the lane the sweep promotes into" more than one lane, so the
`PROMOTION_LANE = "Todo"` constant goes. `sweep_promotes()` is read off the
lane contract instead: the destination is a work-segment lane other than the
lane the planning exit lands in (Backlog). `config_problems()` binds the
promoter, `reconcile.py`, to every lane the sweep carries a card to.
"""

import copy
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # the promotion suite's harness
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import lane_contract  # noqa: E402
import routing_verdict  # noqa: E402

HAND_WORK = "Hand-work"
SWEPT = ("FLEET", "WORKBENCH", "OPERATOR")
NOT_SWEPT = ("PARKED", "NEEDS WORK")


def _verdict_comment(name: str) -> str:
    return routing_verdict.verdict_comment(name, "a fixture reason")


def _lane_entry(doc, name):
    return next(entry for entry in doc["lanes"] if entry["name"] == name)


@pytest.fixture
def contract(monkeypatch):
    """A private copy of the committed contract, installed as the one every
    reader loads, so a test can break one fact of it."""
    doc = copy.deepcopy(lane_contract.load())
    monkeypatch.setitem(lane_contract._CACHE, lane_contract.CONTRACT_PATH, doc)
    return doc


# --------------------------------------------------------------------------- #
# the destinations                                                            #
# --------------------------------------------------------------------------- #


class TestTheDestinations:
    def test_a_persons_verdicts_land_in_hand_work(self):
        assert routing_verdict.destination("WORKBENCH") == HAND_WORK
        assert routing_verdict.destination("OPERATOR") == HAND_WORK

    def test_fleet_still_lands_in_todo(self):
        assert routing_verdict.destination("FLEET") == "Todo"

    def test_the_marks_actor_and_promotable_are_unchanged(self):
        assert routing_verdict.marks("WORKBENCH") == ("hand-built",)
        assert routing_verdict.marks("OPERATOR") == ("hand-built", "no-code")
        for name in ("WORKBENCH", "OPERATOR"):
            assert routing_verdict.actor(name) == "operator"
            assert routing_verdict.is_promotable(name) is False

    def test_hand_work_is_a_live_lane_the_promoter_and_the_actor_may_write(self):
        assert HAND_WORK in lane_contract.lane_names("live")
        writers = lane_contract.lane_writers(HAND_WORK)
        assert routing_verdict.PROMOTER in writers
        assert "operator" in writers


# --------------------------------------------------------------------------- #
# sweep_promotes and is_promotable, per verdict                               #
# --------------------------------------------------------------------------- #


class TestWhatTheSweepCarries:
    @pytest.mark.parametrize("name", SWEPT)
    def test_the_sweep_carries_fleet_workbench_and_operator(self, name):
        assert routing_verdict.sweep_promotes(name) is True

    @pytest.mark.parametrize("name", NOT_SWEPT)
    def test_the_sweep_leaves_parked_and_needs_work(self, name):
        assert routing_verdict.sweep_promotes(name) is False

    @pytest.mark.parametrize("name", SWEPT + NOT_SWEPT)
    def test_only_fleet_is_dispatched(self, name):
        assert routing_verdict.is_promotable(name) is (name == "FLEET")

    def test_no_promotion_lane_constant_remains(self):
        assert not hasattr(routing_verdict, "PROMOTION_LANE")

    def test_the_sweep_lanes_are_read_off_the_vocabulary(self):
        assert routing_verdict.sweep_lanes() == ("Todo", HAND_WORK)

    def test_the_planning_exit_landing_lane_is_never_a_sweep_lane(self):
        landing = lane_contract.planning_exit()[1]
        assert landing == "Backlog"
        assert landing not in routing_verdict.sweep_lanes()

    def test_a_verdict_pointed_at_a_planning_lane_is_not_swept(self):
        doc = copy.deepcopy(routing_verdict.load())
        routing_verdict.record("WORKBENCH", doc)["destination"] = "Green Light"
        assert routing_verdict.sweep_promotes("WORKBENCH", doc) is False

    @pytest.mark.parametrize("lane", ["In Progress", "In Review", "Done"])
    def test_a_verdict_pointed_past_the_waiting_lanes_is_not_swept(self, lane):
        """The sweep promotes a card to where it WAITS to be built, never past
        it: a one-word edit sending WORKBENCH to Done must not have the sweep
        close Backlog cards nobody built (the critic on #703)."""
        doc = copy.deepcopy(routing_verdict.load())
        routing_verdict.record("WORKBENCH", doc)["destination"] = lane
        assert routing_verdict.sweep_promotes("WORKBENCH", doc) is False
        assert lane not in routing_verdict.sweep_lanes(doc)

    @pytest.mark.parametrize("lane", ["In Progress", "In Review", "Done"])
    def test_fleet_pointed_past_todo_is_a_config_problem(self, lane):
        doc = copy.deepcopy(routing_verdict.load())
        routing_verdict.record("FLEET", doc)["destination"] = lane
        problems = routing_verdict.config_problems(doc)
        assert any("FLEET" in p and lane in p for p in problems), problems


# --------------------------------------------------------------------------- #
# promotion_refusal                                                           #
# --------------------------------------------------------------------------- #


class TestThePromotionRefusal:
    @pytest.mark.parametrize("name", SWEPT)
    def test_a_card_the_sweep_carries_is_not_refused(self, name):
        assert routing_verdict.promotion_refusal("DRE-1", [_verdict_comment(name)]) is None

    def test_a_parked_card_is_refused(self):
        refusal = routing_verdict.promotion_refusal("DRE-1", [_verdict_comment("PARKED")])
        assert refusal is not None
        assert routing_verdict.NOT_FLEET_TAG in refusal

    def test_a_card_with_no_verdict_is_told_where_each_route_lands(self):
        refusal = routing_verdict.promotion_refusal("DRE-1", [])
        assert routing_verdict.NO_VERDICT_TAG in refusal
        assert "Todo" in refusal and HAND_WORK in refusal


# --------------------------------------------------------------------------- #
# the receipt names the lane it read                                          #
# --------------------------------------------------------------------------- #


class TestTheHandBuiltReceipt:
    """The receipt a person reads is the note wrapped in the promoter's header,
    `🧹 Auto-promoted Backlog → <lane>:`. It must name ONE lane — the lane the
    card was really moved to (the critic on #703: a note saying "your turn in
    Hand-work" under a `→ Todo` header sends the reader to an empty lane)."""

    LANES = ("Backlog", "Todo", HAND_WORK, "In Progress", "In Review", "Done")

    @pytest.mark.parametrize("name", ("WORKBENCH", "OPERATOR"))
    def test_the_note_names_no_lane_of_its_own(self, name):
        note = routing_verdict.hand_built_promotion(name)
        assert not [lane for lane in self.LANES if lane in note], note

    def test_fleet_has_no_hand_built_note(self):
        assert routing_verdict.hand_built_promotion("FLEET") is None

    @pytest.mark.parametrize("name", ("WORKBENCH", "OPERATOR"))
    def test_the_receipt_the_promoter_posts_names_the_lane_it_moved_the_card_to(self, name):
        """Built the way `promote_ready` builds it, over the promotion suite's
        own board: whatever lane the card lands in, the receipt names that lane
        and no other."""
        import test_operator_card_promotion as promo

        comment = promo.WORKBENCH if name == "WORKBENCH" else promo.OPERATOR
        board = promo._Board(promo._card(comments=[comment]))
        assert board.promote() == 1
        landed = board.lane_of("DRE-3385")
        receipt = board.receipt_for("DRE-3385")
        named = [lane for lane in self.LANES if lane != "Backlog" and lane in receipt]
        assert named == [landed], receipt


# --------------------------------------------------------------------------- #
# the vocabulary checks itself against the contract                           #
# --------------------------------------------------------------------------- #


class TestTheCheck:
    def test_the_committed_vocabulary_checks_out(self):
        assert routing_verdict.config_problems() == []

    def test_the_cli_check_passes(self, capsys):
        assert routing_verdict.main(["check"]) == 0

    def test_a_sweep_lane_the_promoter_may_not_write_fails(self, contract):
        writers = _lane_entry(contract, HAND_WORK)["clauses"]["writers"]
        writers["who"] = [w for w in writers["who"] if w != routing_verdict.PROMOTER]
        problems = routing_verdict.config_problems()
        assert any(HAND_WORK in p and routing_verdict.PROMOTER in p for p in problems), problems

    def test_a_destination_that_is_not_live_fails(self, contract):
        entry = _lane_entry(contract, HAND_WORK)
        entry["status"] = "arriving"
        entry.update(arriving_by="DRE-5240", reason="fixture", board_action="create it")
        problems = routing_verdict.config_problems()
        assert any("WORKBENCH" in p and HAND_WORK in p and "live" in p for p in problems), problems
        assert any("OPERATOR" in p and HAND_WORK in p for p in problems), problems

    def test_the_todo_binding_is_still_checked(self, contract):
        writers = _lane_entry(contract, "Todo")["clauses"]["writers"]
        writers["who"] = [w for w in writers["who"] if w != routing_verdict.PROMOTER]
        problems = routing_verdict.config_problems()
        assert any("'Todo'" in p and routing_verdict.PROMOTER in p for p in problems), problems


# --------------------------------------------------------------------------- #
# the rendered document and the standards                                     #
# --------------------------------------------------------------------------- #

ROOT = os.path.join(os.path.dirname(__file__), "..")


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


class TestTheDocuments:
    def test_the_rendered_doc_is_current_and_shows_hand_work(self):
        text = _read("docs/routing-verdicts.md")
        assert text == routing_verdict.render_markdown()
        for name in ("WORKBENCH", "OPERATOR"):
            row = next(line for line in text.splitlines()
                       if line.startswith(f"| **{name}**") or line.startswith(f"| {name}"))
            assert HAND_WORK in row, row

    @pytest.mark.parametrize("rel", ["standards/card-quality.md", "briefs/planner.md",
                                     "standards/architecture.md"])
    def test_no_standard_sends_a_persons_card_to_todo(self, rel):
        for line in _read(rel).splitlines():
            if ("WORKBENCH" in line or "OPERATOR" in line) and "Todo" in line:
                lowered = line.lower()
                assert "fleet" in lowered or "not todo" in lowered or "instead of todo" in lowered, (
                    f"{rel}: {line.strip()}")

    @pytest.mark.parametrize("rel", ["standards/card-quality.md", "briefs/planner.md",
                                     "standards/architecture.md"])
    def test_each_standard_names_hand_work(self, rel):
        assert HAND_WORK in _read(rel)

    def test_the_lane_contract_no_longer_says_a_persons_card_goes_to_todo(self):
        """The contract is what the pipeline enforces (standards/architecture.md),
        so its clauses move with the vocabulary in the same PR (the critic on
        #703): Backlog's exit and Todo's entrance stop saying Todo is where a
        WORKBENCH or OPERATOR card goes today."""
        clauses = {
            ("Backlog", "exit"): lane_contract.lane("Backlog")["clauses"]["exit"],
            ("Todo", "entrance"): lane_contract.lane("Todo")["clauses"]["entrance"],
        }
        for where, clause in clauses.items():
            for key in ("text", "pending"):
                said = clause.get(key) or ""
                assert "Todo today" not in said, (where, key)
                assert "Today that is FLEET for a dispatched agent run, and WORKBENCH" not in said
                assert "once that lane is live" not in said, (where, key)
            assert HAND_WORK in clause["text"], where
