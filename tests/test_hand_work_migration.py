"""RED-first: the one-time migration moves a person's work out of Todo into
Hand-work (DRE-5323, epic DRE-5240).

WHAT IS UNDER TEST, over a stubbed board — nothing here reaches Linear:
  * `census` puts each Todo card under exactly one of four classes, by
    precedence: epic, finished, person, build. A person card is marked as
    moving to Hand-work; an epic is named with the lane the sweep will carry it
    to; a finished card is quoted with its merge receipt.
  * `run` without `--apply` writes nothing; with it, one `cmd_state(card,
    "Hand-work")` and one `🧳 hand-work-migration:` comment per person card,
    and nothing at all on a build, epic or finished card.
  * An epic wearing `agent:planner` (with a child) is an epic, carried to In
    Progress when it was In Progress before Todo and to Planning otherwise; a
    one-off wearing `agent:planner` and `hand-built` is a person card.
  * A person card with a run receipt newer than its mark is skipped, a named
    card outside Todo is skipped, each with the reason printed; an unreadable
    Linear makes the script exit non-zero before any write.
  * The script is a declared writer of Hand-work, its comment is in the act
    registry, and no card id appears in its code.

Run: cd bureau-pipeline && python3 -m pytest tests/test_hand_work_migration.py -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import backlog_cutover  # noqa: E402 — `card_ids_in_code`, the "no allowlist" check
import hand_work_migration as hwm  # noqa: E402
import lane_contract  # noqa: E402
import linear_ops  # noqa: E402
import pipeline_act  # noqa: E402
import ready_lane_writers  # noqa: E402
import routing_verdict  # noqa: E402

HAND_WORK = "Hand-work"
HAND_BUILT = "hand-built"
NO_CODE = linear_ops.NO_CODE_LABEL
CREATED = "2026-09-01T00:00:00.000Z"


def _card(identifier, *, title="a card", labels=(), comments=(), children=0,
          history=(), state="Todo", parent=None):
    """A Todo card as the population query returns it. `comments` are
    (body, createdAt) oldest→newest; the API hands them back newest first."""
    nodes = [{"body": b, "createdAt": at, "user": None} for b, at in comments]
    return {
        "id": f"uuid-{identifier}",
        "identifier": identifier,
        "title": title,
        "createdAt": CREATED,
        "state": {"name": state},
        "labels": {"nodes": [{"name": n} for n in labels]},
        "parent": parent,
        "children": {"nodes": [{"id": f"kid-{i}"} for i in range(children)]},
        "history": {"nodes": list(history)},
        "comments": {"pageInfo": {"hasNextPage": False, "endCursor": None},
                     "nodes": list(reversed(nodes))},
    }


class FakeOps:
    """The write layer's surface the script touches, recording every write.
    `history` answers `epic_todo_gate.lane_before_todo`: id → lane before Todo."""

    def __init__(self, cards, history=None, unreadable=False):
        self.cards = list(cards)
        self.history = dict(history or {})
        self.unreadable = unreadable
        self.states: list[tuple] = []
        self.comments: list[tuple] = []
        self.state_result = True

    def gql_paged(self, query, variables=None, **kw):
        if self.unreadable:
            raise linear_ops.LinearError("linear error: 502")
        assert variables == {"lane": "Todo"}
        return list(self.cards)

    def gql(self, query, variables=None):
        before = self.history.get(variables["id"])
        nodes = ([{"fromState": {"name": before}, "toState": {"name": "Todo"}}]
                 if before else [])
        return {"issue": {"history": {"nodes": nodes}}}

    def cmd_state(self, identifier, state, *flags, **kw):
        self.states.append((identifier, state, kw))
        return self.state_result

    def cmd_comment(self, identifier, body, *flags):
        self.comments.append((identifier, body))
        return None


def _verdict(name):
    return (routing_verdict.verdict_comment(name, "a fixture reason"), "2026-09-02T00:00:00.000Z")


WORKBENCH_CARD = _card("DRE-1", title="PROOF: the thing works live",
                       labels=("repo:bureau-pipeline", HAND_BUILT),
                       comments=[_verdict("WORKBENCH")])
OPERATOR_CARD = _card("DRE-2", title="rotate the key",
                      labels=("repo:bureau-pipeline", HAND_BUILT, NO_CODE),
                      comments=[_verdict("OPERATOR")])
BUILD_CARD = _card("DRE-3", title="build the widget",
                   labels=("repo:bureau-pipeline", "agent:engineer"),
                   comments=[_verdict("FLEET")])
EPIC_CARD = _card("DRE-4", title="the epic", labels=("repo:bureau-pipeline", "agent:planner"),
                  children=2)
FINISHED_CARD = _card("DRE-5", title="merged, not closed",
                      labels=("repo:bureau-pipeline", HAND_BUILT),
                      comments=[("✅ Merged: https://github.com/x/y/pull/9",
                                 "2026-09-03T00:00:00.000Z")])
ONE_OFF_PLANNER_PERSON = _card("DRE-6", title="a one-off a person builds",
                               labels=("repo:bureau-pipeline", "agent:planner", HAND_BUILT),
                               comments=[_verdict("WORKBENCH")])


def _rows(cards, history=None):
    ops = FakeOps(cards, history)
    return hwm.census(cards, ops), ops


def _by_id(rows):
    return {r["identifier"]: r for r in rows}


# --------------------------------------------------------------------------
# 1: four classes, by precedence
# --------------------------------------------------------------------------
class TestTheCensus:
    def test_each_card_lands_in_exactly_one_class(self):
        rows, _ = _rows([WORKBENCH_CARD, OPERATOR_CARD, BUILD_CARD, EPIC_CARD,
                         FINISHED_CARD])
        got = {r["identifier"]: r["class"] for r in rows}
        assert got == {"DRE-1": "person", "DRE-2": "person", "DRE-3": "build",
                       "DRE-4": "epic", "DRE-5": "finished"}
        assert set(got.values()) <= set(hwm.CLASSES)

    def test_the_class_names_are_exactly_the_contracts(self):
        # In precedence order, and spelled as the proof card reads them.
        assert hwm.CLASSES == ("epic", "finished", "person", "build")
        assert hwm.UNCLASSIFIED == "unclassified"

    def test_a_person_card_is_marked_as_moving_to_hand_work(self):
        rows, _ = _rows([WORKBENCH_CARD])
        assert rows[0]["action"] == f"moves to {HAND_WORK}"

    def test_precedence_epic_beats_finished_beats_person(self):
        """A card that is several things at once is the first of them."""
        epic_and_merged = _card("DRE-7", title="[EPIC] both", labels=(HAND_BUILT,),
                                comments=[("✅ Merged: https://x/pull/1", CREATED)])
        merged_person = FINISHED_CARD
        rows, _ = _rows([epic_and_merged, merged_person])
        got = _by_id(rows)
        assert got["DRE-7"]["class"] == "epic"
        assert got["DRE-5"]["class"] == "finished"

    def test_a_card_whose_labels_cannot_be_read_is_unclassified(self):
        broken = _card("DRE-8")
        broken["labels"] = None
        rows, _ = _rows([broken])
        assert rows[0]["class"] == "unclassified"
        assert "left alone" in rows[0]["action"]

    def test_the_rendering_lists_each_class_with_labels_verdict_and_parent(self):
        parented = _card("DRE-9", labels=(HAND_BUILT,), comments=[_verdict("WORKBENCH")],
                         parent={"identifier": "DRE-100", "state": {"name": "In Progress"}})
        rows, _ = _rows([parented, BUILD_CARD])
        text = hwm.render(rows)
        assert "person (1):" in text and "build (1):" in text
        assert "DRE-9" in text and "verdict: WORKBENCH" in text and "parent: DRE-100" in text
        assert HAND_BUILT in text


# --------------------------------------------------------------------------
# 2: epics and finished cards are named, never written
# --------------------------------------------------------------------------
class TestEpicsAndFinishedCards:
    def test_an_approved_epic_is_carried_to_in_progress(self):
        rows, _ = _rows([EPIC_CARD], history={"DRE-4": "In Progress"})
        assert rows[0]["class"] == "epic"
        assert rows[0]["carry_to"] == "In Progress"
        assert "In Progress" in rows[0]["action"]

    def test_an_unapproved_epic_is_carried_to_planning(self):
        rows, _ = _rows([EPIC_CARD], history={"DRE-4": "Green Light"})
        assert rows[0]["carry_to"] == "Planning"

    def test_an_epic_gets_no_state_write_and_no_comment(self):
        rows, ops = _rows([EPIC_CARD], history={"DRE-4": "In Progress"})
        hwm.run(ops, rows, apply=True)
        assert ops.states == [] and ops.comments == []

    def test_agent_planner_alone_does_not_make_an_epic(self):
        rows, _ = _rows([ONE_OFF_PLANNER_PERSON])
        assert rows[0]["class"] == "person"

    @pytest.mark.parametrize("receipt", [
        "✅ Merged: https://github.com/x/y/pull/9",
        f"{linear_ops.MERGED_NOT_CLOSED_MARKER} — the operator closes this by hand",
    ])
    def test_a_finished_card_is_quoted_and_never_written(self, receipt):
        card = _card("DRE-5", labels=(HAND_BUILT,), comments=[(receipt, CREATED)])
        rows, ops = _rows([card])
        assert rows[0]["class"] == "finished"
        assert rows[0]["receipt"] == receipt.splitlines()[0].strip()
        assert receipt.splitlines()[0].strip() in rows[0]["action"]
        hwm.run(ops, rows, apply=True)
        assert ops.states == [] and ops.comments == []


# --------------------------------------------------------------------------
# 3: dry by default; --apply moves each person card once, with its comment
# --------------------------------------------------------------------------
class TestTheRun:
    def test_a_dry_run_writes_nothing(self):
        rows, ops = _rows([WORKBENCH_CARD, OPERATOR_CARD, BUILD_CARD])
        result = hwm.run(ops, rows, apply=False)
        assert ops.states == [] and ops.comments == []
        assert result["moved"] == []

    def test_apply_moves_each_person_card_once_and_comments_once(self):
        rows, ops = _rows([WORKBENCH_CARD, OPERATOR_CARD, BUILD_CARD])
        result = hwm.run(ops, rows, apply=True)
        assert [(i, s) for i, s, _ in ops.states] == [("DRE-1", HAND_WORK),
                                                        ("DRE-2", HAND_WORK)]
        assert [i for i, _ in ops.comments] == ["DRE-1", "DRE-2"]
        for _, body in ops.comments:
            assert body.startswith("🧳 hand-work-migration:")
        assert result["moved"] == ["DRE-1", "DRE-2"]

    def test_the_move_is_conditional_on_the_card_still_being_in_todo(self):
        rows, ops = _rows([WORKBENCH_CARD])
        hwm.run(ops, rows, apply=True)
        assert ops.states[0][2] == {"expect": ("Todo",)}

    def test_a_refused_move_posts_no_comment(self):
        rows, ops = _rows([WORKBENCH_CARD])
        ops.state_result = False
        result = hwm.run(ops, rows, apply=True)
        assert ops.comments == []
        assert result["refused"] == ["DRE-1"]

    def test_a_build_card_is_never_touched(self):
        rows, ops = _rows([BUILD_CARD])
        hwm.run(ops, rows, apply=True)
        assert ops.states == [] and ops.comments == []


# --------------------------------------------------------------------------
# 4: skips, each with its reason; unreadable is a refusal
# --------------------------------------------------------------------------
class TestSkips:
    def test_a_run_receipt_newer_than_the_mark_is_skipped(self):
        card = _card("DRE-10", labels=(HAND_BUILT,),
                     history=[{"createdAt": "2026-09-05T00:00:00.000Z",
                               "addedLabels": [{"name": HAND_BUILT}]}],
                     comments=[_verdict("WORKBENCH"),
                               ("⏳ run started", "2026-09-06T00:00:00.000Z")])
        rows, ops = _rows([card])
        assert rows[0]["class"] == "person"
        assert rows[0]["skip"] and "newer" in rows[0]["skip"]
        assert rows[0]["action"].startswith("skipped")
        hwm.run(ops, rows, apply=True)
        assert ops.states == []

    def test_a_run_receipt_older_than_the_mark_does_not_hold_it(self):
        card = _card("DRE-11", labels=(HAND_BUILT,),
                     history=[{"createdAt": "2026-09-07T00:00:00.000Z",
                               "addedLabels": [{"name": HAND_BUILT}]}],
                     comments=[("🧠 an old run", "2026-09-06T00:00:00.000Z"),
                               _verdict("WORKBENCH")])
        rows, ops = _rows([card])
        assert rows[0]["skip"] is None
        hwm.run(ops, rows, apply=True)
        assert [(i, s) for i, s, _ in ops.states] == [("DRE-11", HAND_WORK)]

    def test_a_named_card_outside_todo_is_skipped_with_the_reason(self, capsys):
        ops = FakeOps([WORKBENCH_CARD])
        with mock.patch.object(hwm, "linear_ops", ops):
            assert hwm.main(["run", "--only", "DRE-99", "--apply"]) == 0
        out = capsys.readouterr().out
        assert "skipped DRE-99: not in Todo" in out
        assert ops.states == [] and ops.comments == []

    def test_only_restricts_the_run_to_the_named_card(self):
        ops = FakeOps([WORKBENCH_CARD, OPERATOR_CARD])
        with mock.patch.object(hwm, "linear_ops", ops):
            assert hwm.main(["run", "--only", "dre-2", "--apply"]) == 0
        assert [(i, s) for i, s, _ in ops.states] == [("DRE-2", HAND_WORK)]

    def test_unreadable_linear_exits_non_zero_before_any_write(self, capsys):
        ops = FakeOps([WORKBENCH_CARD], unreadable=True)
        with mock.patch.object(hwm, "linear_ops", ops):
            assert hwm.main(["run", "--apply"]) != 0
        assert ops.states == [] and ops.comments == []
        assert "nothing was written" in capsys.readouterr().err

    def test_a_typo_is_refused_before_anything_is_read(self):
        ops = FakeOps([WORKBENCH_CARD], unreadable=True)
        with mock.patch.object(hwm, "linear_ops", ops):
            assert hwm.main(["run", "--only", "1234"]) == 2

    def test_census_and_dry_run_write_nothing_end_to_end(self, capsys):
        ops = FakeOps([WORKBENCH_CARD, EPIC_CARD], history={"DRE-4": "In Progress"})
        with mock.patch.object(hwm, "linear_ops", ops):
            assert hwm.main(["census"]) == 0
            assert hwm.main(["run"]) == 0
        assert ops.states == [] and ops.comments == []
        assert "dry run — nothing was written" in capsys.readouterr().out


# --------------------------------------------------------------------------
# 5: declared, registered, and no allowlist
# --------------------------------------------------------------------------
class TestDeclared:
    def test_it_has_its_own_glossary_key(self):
        entry = lane_contract.writers()["hand_work_migration.py"]
        assert entry["path"] == "scripts/hand_work_migration.py"

    def test_it_is_a_permitted_writer_of_hand_work(self):
        assert lane_contract.lane_writers(HAND_WORK) == (
            "reconcile.py", "linear_ops.py", "hand_work_migration.py", "operator")

    def test_the_ready_lane_check_attributes_its_write(self):
        mine = [w for w in ready_lane_writers.writes()
                if w.writer == "hand_work_migration.py"]
        assert {w.lane for w in mine} == {HAND_WORK}
        assert ready_lane_writers.writer_problems() == []

    def test_its_comment_site_is_in_the_act_registry(self):
        assert pipeline_act.main(["check"]) == 0
        source = (ROOT / "scripts" / "hand_work_migration.py").read_text()
        assert source.count("ops.cmd_comment(ident, migration_note(row))") == 1

    def test_no_card_id_appears_in_its_code(self):
        source = (ROOT / "scripts" / "hand_work_migration.py").read_text()
        assert backlog_cutover.card_ids_in_code(source) == []

    def test_the_rendered_contract_is_current(self):
        with open(lane_contract.DOC_PATH, encoding="utf-8") as fh:
            assert fh.read() == lane_contract.render_markdown()


# --------------------------------------------------------------------------
# 6: the record
# --------------------------------------------------------------------------
class TestTheDoc:
    DOC = ROOT / "docs" / "hand-work-migration.md"

    def test_it_says_how_to_run_it_and_what_each_class_gets(self):
        text = self.DOC.read_text()
        for phrase in ("hand_work_migration.py census", "hand_work_migration.py run",
                       "run --apply", "--only", "`person`", "`epic`", "`finished`",
                       "`build`", "`unclassified`", "🧳 hand-work-migration:"):
            assert phrase in text, phrase

    def test_it_records_the_board_before_the_run_with_pt_times(self):
        text = self.DOC.read_text()
        before = text.split("## Before the run", 1)[1].split("## The run", 1)[0]
        for phrase in ("DRE-3621", "15:24 PT", "2026-09-29", "08:49 PT", "2026-09-30",
                       "DRE-4541"):
            assert phrase in before, phrase

    def test_the_run_section_waits_for_the_proof(self):
        text = self.DOC.read_text()
        assert "## The run" in text
        assert "DRE-5349" in text.split("## The run", 1)[1]

    def test_it_says_why_the_groomer_still_finds_its_card(self):
        text = self.DOC.read_text()
        for phrase in ("GROOM_PROPOSAL_CARD", "card_is_open", "DRE-5348"):
            assert phrase in text, phrase
