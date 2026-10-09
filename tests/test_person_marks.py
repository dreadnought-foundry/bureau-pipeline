"""A person's card is read off the vocabulary's person marks (DRE-6225).

Until this card "a person builds this, nothing is dispatched" was read off one
string, `hand-built`, in seven places, and the two score readers decided "needs
a person" by asking whether `hand-built` was in a verdict's `marks`. The CEO's
rule of 2026-10-07 is that `hand-built` is his mark alone and nothing automatic
applies it, so the OPERATOR verdict's marker is about to change to
`operator-step` — a data change in `config/routing-verdicts.json`, made by a
sibling card.

If the readers still asked for the string, the flip would break three things at
once: every OPERATOR card the sweep promotes afterwards is alarmed as stranded
in Hand-work, `critic_score.reference_problems` refuses its own contaminated
dimension, and `planner_score.claimed_route` silently calls OPERATOR
dispatchable. So every assertion here runs against BOTH vocabularies: the
shipped file, and an in-memory copy with OPERATOR's marks
`["operator-step", "no-code"]` and WORKBENCH's `[]` — the flip, made before it
ships, so the flip is proved to be data.

And the one thing this card DOES change under today's file: a `PROOF:` card is
taken by the proof run, not the operator, so it never receives a person marker.
Stamped or promoted OPERATOR, it receives `no-code` alone (`card_marks`).

Run: cd bureau-pipeline && python3 -m pytest tests/test_person_marks.py -v
"""
from __future__ import annotations

import copy
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/portico")
os.environ.setdefault("REPO_SLUG", "portico")
os.environ.setdefault("GH_TOKEN", "x")

import critic_score  # noqa: E402
import linear_ops  # noqa: E402
import planner_score  # noqa: E402
import proof_and_demo  # noqa: E402
import reconcile  # noqa: E402
import routing_verdict  # noqa: E402
import validate_card  # noqa: E402

# Spelled literally on purpose: these are the labels as they exist in Linear,
# and a test must fail if a constant drifts to something Linear does not have.
HAND_BUILT = "hand-built"
OPERATOR_STEP = "operator-step"
NO_CODE = "no-code"

SHIPPED = "shipped"
FLIPPED = "flipped"


def _flipped() -> dict:
    """The shipped vocabulary with the sibling card's flip applied in memory:
    OPERATOR marks `operator-step` + `no-code`, WORKBENCH marks nothing."""
    doc = copy.deepcopy(routing_verdict.load())
    for record in doc["verdicts"]:
        if record["name"] == "OPERATOR":
            record["marks"] = [OPERATOR_STEP, NO_CODE]
        if record["name"] == "WORKBENCH":
            record["marks"] = []
    return doc


@pytest.fixture(params=[SHIPPED, FLIPPED])
def vocabulary(request, monkeypatch):
    """Every reader that calls `routing_verdict.load()` reads `request.param`'s
    vocabulary for the length of the test."""
    if request.param == FLIPPED:
        doc = _flipped()
        monkeypatch.setattr(routing_verdict, "load", lambda path=None: doc)
    return request.param


@pytest.fixture
def flipped(monkeypatch):
    doc = _flipped()
    monkeypatch.setattr(routing_verdict, "load", lambda path=None: doc)
    return doc


@pytest.fixture(autouse=True)
def _pin_sweep(monkeypatch):
    monkeypatch.setattr(reconcile, "REPO_SLUG", "portico")
    monkeypatch.setattr(validate_card, "VALID_SLUGS", {"portico", "bureau-pipeline"})
    monkeypatch.setattr(reconcile, "live_rail_slugs",
                        lambda: frozenset({"portico", "bureau-pipeline"}),
                        raising=False)
    reconcile._write_failures.clear()
    yield
    reconcile._write_failures.clear()


# --------------------------------------------------------------------------
# the vocabulary's API
# --------------------------------------------------------------------------
class TestTheLabels:
    def test_the_ceos_mark_is_declared_once_and_aliased(self):
        assert routing_verdict.HAND_BUILT_LABEL == HAND_BUILT
        assert reconcile.HAND_BUILT_LABEL is routing_verdict.HAND_BUILT_LABEL
        assert critic_score.CONTAMINATED_MARK is routing_verdict.HAND_BUILT_LABEL

    def test_the_operator_marker_is_declared_before_the_config_carries_it(self):
        assert routing_verdict.OPERATOR_STEP_LABEL == OPERATOR_STEP


class TestPersonMarks:
    def test_the_shipped_vocabulary(self):
        assert routing_verdict.person_marks() == (HAND_BUILT, NO_CODE)

    def test_the_flipped_vocabulary(self, flipped):
        assert routing_verdict.person_marks() == (OPERATOR_STEP, NO_CODE, HAND_BUILT)
        assert routing_verdict.person_marks(flipped) == (OPERATOR_STEP, NO_CODE, HAND_BUILT)

    def test_the_doc_argument_is_read(self):
        assert routing_verdict.person_marks(_flipped()) == (OPERATOR_STEP, NO_CODE, HAND_BUILT)

    def test_a_verdict_nobody_acts_on_contributes_nothing(self):
        """FLEET's actor is a workflow, so a mark on it is not a person's."""
        doc = copy.deepcopy(routing_verdict.load())
        for record in doc["verdicts"]:
            if record["name"] == "FLEET":
                record["marks"] = ["fleet-only"]
        assert "fleet-only" not in routing_verdict.person_marks(doc)

    @pytest.mark.parametrize("name, expected", [
        ("FLEET", False), ("WORKBENCH", True), ("OPERATOR", True),
        ("PARKED", False), ("NEEDS WORK", False),
    ])
    def test_is_person_verdict(self, vocabulary, name, expected):
        assert routing_verdict.is_person_verdict(name) is expected


class TestRetirementLifts:
    def test_the_shipped_file(self):
        assert routing_verdict.retirement_lifts() == (HAND_BUILT,)
        assert routing_verdict.RETIREMENT_LIFTS == (HAND_BUILT,)

    def test_the_flipped_file(self, flipped):
        assert routing_verdict.retirement_lifts() == (OPERATOR_STEP, HAND_BUILT)

    def test_a_retirement_after_the_flip_lifts_operator_step(self, flipped):
        assert routing_verdict.lifted_marks(["OPERATOR"], "FLEET") == (OPERATOR_STEP,)
        assert routing_verdict.lifted_marks(["OPERATOR"], "OPERATOR") == ()

    def test_a_retirement_today_still_lifts_hand_built(self):
        assert routing_verdict.lifted_marks(["OPERATOR"], "FLEET") == (HAND_BUILT,)

    def test_the_retirement_note_names_what_it_lifts(self, flipped):
        note = routing_verdict.retirement_comment(
            [{"body": routing_verdict.verdict_comment("OPERATOR", "a deploy"),
              "createdAt": "2026-10-01T00:00:00Z"}],
            "2026-10-02T00:00:00Z", now=datetime(2026, 10, 3, tzinfo=UTC))
        assert f"`{OPERATOR_STEP}`" in note


class TestCardMarks:
    def test_a_proof_card_keeps_no_code_alone(self, vocabulary):
        assert routing_verdict.card_marks("OPERATOR", "PROOF: anything") == (NO_CODE,)

    def test_a_workbench_proof_card_receives_nothing(self, vocabulary):
        assert routing_verdict.card_marks("WORKBENCH", "PROOF: anything") == ()

    @pytest.mark.parametrize("title", ["  proof: lower case", "Proof: mixed"])
    def test_the_proof_reader_is_proof_and_demos(self, title):
        assert proof_and_demo.is_proof(title)
        assert routing_verdict.card_marks("OPERATOR", title) == (NO_CODE,)

    def test_any_other_card_receives_every_mark(self, vocabulary):
        assert routing_verdict.card_marks("OPERATOR", "OPERATOR: deploy it") == \
            routing_verdict.marks("OPERATOR")
        assert routing_verdict.card_marks("WORKBENCH", "fix the console") == \
            routing_verdict.marks("WORKBENCH")

    def test_a_title_that_mentions_proof_is_not_a_proof(self):
        assert routing_verdict.card_marks("OPERATOR", "Write the PROOF: record") == \
            routing_verdict.marks("OPERATOR")

    def test_the_receipt_names_the_marks_the_card_receives(self):
        proof = routing_verdict.hand_built_promotion("OPERATOR", title="PROOF: x")
        assert f"`{NO_CODE}`" in proof and f"`{HAND_BUILT}`" not in proof
        other = routing_verdict.hand_built_promotion("OPERATOR", title="OPERATOR: x")
        assert f"`{HAND_BUILT}`" in other and f"`{NO_CODE}`" in other
        assert routing_verdict.hand_built_promotion(
            "WORKBENCH", title="PROOF: x").endswith("nothing was dispatched.")


# --------------------------------------------------------------------------
# the two writers apply card_marks
# --------------------------------------------------------------------------
class _Linear:
    """`linear_ops`' writes and the one card read the stamp makes."""

    def __init__(self, titles):
        self.titles = dict(titles)
        self.labelled: list[tuple[str, str]] = []
        self.reads: list[str] = []

    def get_issue(self, identifier, **_):
        self.reads.append(identifier)
        return {"identifier": identifier, "title": self.titles[identifier]}

    def __enter__(self):
        self._patches = [
            patch.object(linear_ops, "comment_bodies", return_value=[]),
            patch.object(linear_ops, "cmd_comment"),
            patch.object(linear_ops, "add_label",
                         side_effect=lambda i, label: self.labelled.append((i, label))),
            patch.object(linear_ops, "get_issue", side_effect=self.get_issue),
        ]
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.stop()

    def labels_on(self, identifier):
        return tuple(label for i, label in self.labelled if i == identifier)


class TestTheStamp:
    def test_a_proof_card_stamped_operator_receives_card_marks(self, vocabulary):
        with _Linear({"DRE-1": "PROOF: the gate held"}) as board:
            assert routing_verdict.stamp_card("DRE-1", "OPERATOR", "a proof") == 0
        assert board.labels_on("DRE-1") == (NO_CODE,)
        assert board.reads == ["DRE-1"]

    def test_an_operator_card_receives_every_mark(self, vocabulary):
        with _Linear({"DRE-2": "OPERATOR: deploy it"}) as board:
            routing_verdict.stamp_card("DRE-2", "OPERATOR", "a deploy")
        assert board.labels_on("DRE-2") == routing_verdict.marks("OPERATOR")

    def test_a_title_the_caller_passes_is_not_read_again(self):
        with _Linear({}) as board:
            routing_verdict.stamp_card("DRE-3", "OPERATOR", "a proof", title="PROOF: x")
        assert board.reads == []
        assert board.labels_on("DRE-3") == (NO_CODE,)

    def test_proof_and_demo_writes_card_marks_with_three_arguments(self, vocabulary):
        """`write_stamps` is not edited: it calls the stamp with three
        arguments, and the stamp reads the title itself."""
        children = [
            {"identifier": "DRE-9001", "title": "Build piece 1",
             "body": "Add it.\n\n## Acceptance criteria\n\n- [ ] it renders\n",
             "labels": ["repo:portico", "agent:engineer"], "blocked_by": []},
            {"identifier": "DRE-9091", "title": "PROOF: the gate refused a real epic",
             "body": f"Read it.\n\n{proof_and_demo.CLOSING_LINE}\n\n"
                     "## Acceptance criteria\n\n- [ ] the record is read\n",
             "labels": ["repo:portico", "agent:ops"], "blocked_by": ["DRE-9001"]},
        ]
        titles = {c["identifier"]: c["title"] for c in children}
        with _Linear(titles) as board:
            assert proof_and_demo.write_stamps(children) == 1
        assert board.labels_on("DRE-9091") == routing_verdict.card_marks(
            "OPERATOR", titles["DRE-9091"]) == (NO_CODE,)


class TestThePromotion:
    def _promote(self, title):
        import test_reconcile_promotion as harness

        record = {
            "identifier": "DRE-7001", "title": title,
            "body": harness.PROOF_BODY, "labels": list(harness.PAIR_LABELS),
            "blocked_by": [],
        }
        card = harness._backlog_card(
            record, [routing_verdict.verdict_comment("OPERATOR", "a person does it")])
        board = harness._Board(card)
        assert board.promote() == 1
        return board

    def test_a_proof_card_promoted_operator_receives_no_code_alone(self, vocabulary):
        board = self._promote("PROOF: the stamp was written")
        assert tuple(board.labels_on("DRE-7001")) == (NO_CODE,)
        receipt = board.comments_on("DRE-7001")[0]
        assert f"`{NO_CODE}`" in receipt
        assert f"`{HAND_BUILT}`" not in receipt and f"`{OPERATOR_STEP}`" not in receipt

    def test_any_other_operator_card_receives_every_mark(self, vocabulary):
        board = self._promote("OPERATOR: rotate the key")
        assert tuple(board.labels_on("DRE-7001")) == routing_verdict.marks("OPERATOR")


# --------------------------------------------------------------------------
# the sweep's readers
# --------------------------------------------------------------------------
def _iso(minutes_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat().replace(
        "+00:00", "Z")


def _card(labels, identifier="DRE-6301", state="Hand-work"):
    return {
        "id": f"uuid-{identifier}",
        "identifier": identifier,
        "title": "rotate the key",
        "description": "work",
        "updatedAt": _iso(999),
        "state": {"name": state},
        "labels": {"nodes": [{"name": n} for n in labels]},
    }


def _watchdog(cards):
    with patch.object(reconcile, "active_cards", return_value=list(cards)), \
            patch.object(reconcile.linear_ops, "comment_bodies", return_value=[]), \
            patch.object(reconcile.linear_ops, "cmd_comment") as comment, \
            patch.object(reconcile.linear_ops, "add_label"), \
            patch.object(reconcile.linear_ops, "cmd_advance"):
        flagged = reconcile.flag_stranded()
    return flagged, comment


class TestHandBuilt:
    def test_operator_step_is_a_persons_card_after_the_flip(self, flipped):
        assert reconcile.hand_built(_card(["repo:portico", OPERATOR_STEP]))
        assert reconcile.hand_built(_card(["repo:portico", "Operator-Step"]))

    def test_operator_step_is_nobodys_mark_today(self):
        assert not reconcile.hand_built(_card(["repo:portico", OPERATOR_STEP]))

    def test_the_ceos_mark_reads_under_both(self, vocabulary):
        assert reconcile.hand_built(_card(["repo:portico", "Hand-Built"]))

    def test_an_unmarked_card_is_not(self, vocabulary):
        assert not reconcile.hand_built(_card(["repo:portico"]))

    def test_an_operator_card_promoted_after_the_flip_is_not_stranded(self, flipped):
        flagged, comment = _watchdog([_card(["repo:portico", OPERATOR_STEP, NO_CODE])])
        assert flagged == set()
        comment.assert_not_called()

    def test_operator_step_without_the_flip_is_still_flagged(self):
        """Control: the guard reads the vocabulary, not a new literal."""
        flagged, _ = _watchdog([_card(["repo:portico", OPERATOR_STEP])])
        assert flagged == {"DRE-6301"}

    def test_an_automation_card_with_no_run_receipt_is_not_stranded(self):
        """The writer sibling re-labels a model record card `automation`; no
        run is coming for it, and it is not a person's either."""
        flagged, comment = _watchdog([_card(["repo:portico", "automation"])])
        assert flagged == set()
        comment.assert_not_called()

    def test_the_door_guards_read_person_marks(self, flipped):
        """`labels_absent` on the nudge loop's moves into Todo is the
        vocabulary's person marks, so a card a person took in the meantime is
        never moved back into the build queue."""
        source = (ROOT / "scripts" / "reconcile.py").read_text()
        assert "labels_absent=(HOLD_LABEL, HAND_BUILT_LABEL)" not in source
        assert source.count("labels_absent=(HOLD_LABEL, *routing_verdict.person_marks())") >= 3


# --------------------------------------------------------------------------
# the two score readers
# --------------------------------------------------------------------------
class TestTheScoreReaders:
    def test_the_critic_reads_operator_as_needing_a_person(self, vocabulary):
        assert critic_score.judgement_of("OPERATOR", HAND_BUILT) == "needs-a-person"
        assert critic_score.judgement_of("WORKBENCH", HAND_BUILT) == "needs-a-person"
        assert critic_score.judgement_of("FLEET", HAND_BUILT) == "dispatchable"

    def test_the_critics_reference_holds_under_both(self, vocabulary):
        assert critic_score.reference_problems() == []

    def test_the_planner_reads_operator_as_needing_a_person(self, vocabulary):
        assert planner_score.claimed_route("OPERATOR") == "needs-a-person"
        assert planner_score.claimed_route("WORKBENCH") == "needs-a-person"
        assert planner_score.claimed_route("FLEET") == "dispatchable"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
