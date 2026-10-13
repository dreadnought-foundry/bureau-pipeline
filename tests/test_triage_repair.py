"""RED-first tests: the one repair each plan-critic park in Triage gets (DRE-7178).

A card the plan critic's bound parks in Triage (`🔒 hold: reason=plan-critic-bound
… by=plan.yml`) had no next step: the hygiene agent's Triage rule 3 reported it
and alarmed after eight hours. On 2026-10-12 DRE-7066 and DRE-7164 sat there
about three hours each, and every finding on both was a defect in the card.
`scripts/triage_repair.py` decides the one automatic repair each such card gets
(`select`), claims it (`claim`), and applies what the planner wrote (`apply`).

Every scenario runs over `TriageRepairBoard`, which answers the `linear_ops`
calls from the state it holds, so each test asserts the lane a card ends in and
the comments it carries. The decision route runs the real
`planning_escalation.escalate` over the real `config/lane-contract.json`.

Run: cd bureau-pipeline && python3 -m pytest tests/test_triage_repair.py -v
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")

import planning_escalation  # noqa: E402
import send_back_class  # noqa: E402
import triage_repair  # noqa: E402
from triage_repair_fakes import PERSON_USER, TriageRepairBoard  # noqa: E402

STAMP = "🔒 hold: reason=plan-critic-bound at=none lifts=manual by=plan.yml"
LIFT = ("🔓 hold lifted: reason=plan-critic-bound because=triage-repair "
        "by=triage_repair.py")

#: DRE-7066's park note, verbatim off its thread (2026-10-13 01:03 UTC): the
#: "revision did not finish" note, with its eight findings.
NOTE_7066 = '🛑 The planner was asked to revise this card to answer the critic, and the revision did not finish — it wrote no revised card, or no note saying what it changed. The card parks in Triage for the operator; it has not been built and is not waiting on the CEO.\n\nWhat the critic found:\n\n1. The card says to give the test harness its own cheaper reviewer through a per-project exception in the model settings, but those settings only accept repos on the pipeline\'s routing list and the test harness is not on it, so as written an agent cannot build it.\n2. The card still leaves the model choice open ("the cheapest model and effort that still posts a real verdict") and offers "or run the probes less often." The CEO\'s answer (Haiku 5.5, harness only, real repos untouched) is not in the card text, so the planner must state it as the decision.\n3. That answer reached the card as a person\'s Linear comment relaying the CEO\'s chat, not as a signed console answer. I treated it as the stated direction, but it is not verified. It also overrides the CEO\'s signed 2026-10-08 exclusion of that model, so the build has to edit that exclusion too. The ca…\n4. "Files: decided at planning" was never filled in. The card needs a real file list: the model settings file and everything generated from it, plus the tests that currently assert the cheap model is on no list.\n5. Effort is set per model, not per project, and the cheap model has no effort entry, so it runs at the tool\'s default. "Cheapest effort" is therefore not something the card can set per repo. It should say what effort is acceptable, or drop that part.\n6. Two criteria cannot be checked before merge: "24 hours of receipts after release, with a per-review cost quoted" and "every harness scenario that asserts a verdict still passes." Both need the change live on main and the live harness running. They belong in a follow-up observation card, not as gate…\n7. The "if the probe frequency changes" criterion is now moot, because the CEO chose the model route. It should come out.\n8. Nothing says what to do if the cheap model cannot be run by the pinned tool version or does not post a bound verdict. The build agent should know the fallback (stop and report; do not widen the exception to real repos).\n\nFix the card and send it back through Planning, or take it out of the queue.'  # noqa: E501

#: DRE-7164's park note, verbatim (2026-10-13 00:56 UTC): the one-off bound,
#: `Every finding raised on this card so far (7)`.
NOTE_7164 = '🛑 **Pre-approval critic — before this is built** — two send-backs on this card — the bound. The planner\'s revision did not satisfy the critic, so the card parks in Triage for the operator with every finding raised so far named below. A revision loop that does not converge is a defect in the card, not a decision, so it is not put to the CEO.\n\nReason: The card names the new field on a screen-data type that does not exist, and the follow-up card (DRE-7166) is waiting on that exact name.\n\nEvery finding raised on this card so far (7), oldest first — one rewrite answers all of them, rather than one answer each:\n\n1. This is two pieces of work in one card: a behind-the-scenes change that works out whether a hold was later cleared, and a screen change that shows it. The screen piece reads what the first piece produces, so they should be separate cards, the first going first.\n2. The card names the new field on a screen-data type that does not exist, and the follow-up card (DRE-7166) is waiting on that exact name.\n3. The type is called "TrainYardTrain" in the card, but the console\'s data type for one train is actually called "YardTrain" ("TrainYardView" is the whole-page type). The other card reads this field by name, so the name has to be right.\n4. The card says a train that is "newer" supersedes a held one, but never says newer by what: the time of the decision, or the order of commits. The two can disagree, and the agent will have to pick.\n5. The test for a later promotion that does NOT contain the held commit says the result is "superseded or null". That is two answers. The card should say which one applies (superseded if a newer decision exists, null if not), so the test has one right result.\n6. The card does not say which answer wins when a hold both has a later promotion containing it and a newer decision. The incident test implies "passed" beats "superseded", but it should be stated.\n7. The card says to read pushes and stable-tag moves "the way the release page does", but the Train Yard\'s reader has neither read today. The build will need to add them (and cover them in the existing test fakes). That is buildable, but the card should say so, so the file list is honest.\n\nThis card is not going to the build queue, and it is not waiting on the CEO — it parks in Triage. Fix the card so one rewrite answers every finding above and send it back through Planning, or take it out of the queue.'  # noqa: E501

#: The marker plan.yml writes beside the bound's note.
MARKER_7164 = ("plan-critic: stage=one-off round=2 result=SEND_BACK collisions=0 — "
               "The card names the new field on a screen-data type that does not exist")


def _items(note: str) -> list[str]:
    return [line.split(". ", 1)[1] for line in note.splitlines()
            if line[:1].isdigit() and ". " in line]


FINDINGS_7066 = _items(NOTE_7066)
FINDINGS_7164 = _items(NOTE_7164)

DECISION_FINDING = ("The card asks for a nightly report and a weekly one without "
                    "picking one, and nobody has made that call.")
REVISION_FINDING = ("The card never names the file the report is written to, so "
                    "the build has to guess it.")
DECISION_NOTE = (
    "🛑 The planner was asked to revise this card to answer the critic, and the "
    "revision did not finish — it wrote no revised card, or no note saying what "
    "it changed. The card parks in Triage for the operator; it has not been "
    "built and is not waiting on the CEO.\n\nWhat the critic found:\n\n"
    f"1. {REVISION_FINDING}\n2. {DECISION_FINDING}\n\n"
    "Fix the card and send it back through Planning, or take it out of the queue."
)

QUESTION = (
    "Finding: the card asks for both a nightly report and a weekly one, and "
    "only one of them can be built.\n"
    "Question: Should the report go out every night, or once a week?\n"
    "Recommendation: once a week — the readers asked for fewer emails, and a "
    "weekly report is cheaper to run."
)
CHOICES = {
    "question": "Should the report go out every night, or once a week?",
    "context": "Only one of the two can be built.",
    "choices": [
        {"id": "weekly", "label": "once a week", "effect": "one report each Monday",
         "outcome": "replan"},
        {"id": "nightly", "label": "every night", "effect": "one report each night",
         "outcome": "replan"},
    ],
    "recommended": "weekly",
    "why": "the readers asked for fewer emails",
}

DESCRIPTION = "The card as it stood before the repair.\n\n## Acceptance criteria\n\n- [ ] one\n"
REVISED = "The card, rewritten to answer every finding.\n\n## Acceptance criteria\n\n- [ ] two\n"


def _answers(n: int) -> str:
    return "\n".join(f"{i}. Answer {i}: the card now says so in its own words."
                     for i in range(1, n + 1)) + "\n"


def _write(directory: Path, **files) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    names = {"revised": "revised-card.md", "answers": "repair-answers.md",
             "question": "question.md", "choices": "question-choices.json"}
    for key, text in files.items():
        if not isinstance(text, str):
            text = json.dumps(text)
        (directory / names[key]).write_text(text, encoding="utf-8")
    return directory


def _select(board, tmp_path, *extra) -> tuple[int, list]:
    out = tmp_path / "selected.json"
    with board.patched():
        rc = triage_repair.main(["select", "--out", str(out), *extra])
    return rc, json.loads(out.read_text(encoding="utf-8"))


def _claim(board, card) -> int:
    with board.patched():
        return triage_repair.main(["claim", card])


def _apply(board, card, action, directory) -> int:
    with board.patched():
        return triage_repair.main(["apply", card, "--action", action,
                                   "--dir", str(directory)])


def _parked(board, card, note, *, description=DESCRIPTION, **kw):
    return board.add(card, description=description, comments=[note, STAMP], **kw)


def _receipts(board, card) -> list[str]:
    return [b for b in board.bodies(card) if b.startswith("🩹 triage-repair:")]


# --------------------------------------------------------------------------- #
# the names                                                                    #
# --------------------------------------------------------------------------- #


class TestTheContract:
    def test_the_names_the_epic_shares(self):
        assert triage_repair.RECEIPT_KEY == "🩹 triage-repair:"
        assert triage_repair.OUTCOMES == ("started", "rewritten", "asked",
                                          "replanned", "split", "unfinished")
        assert triage_repair.ACTIONS == ("repair", "escalate", "replan", "spent", "skip")
        assert triage_repair.REPAIR_MAX_TURNS == 120
        assert triage_repair.DEFAULT_LIMIT == 3

    def test_the_lift_line_is_the_contracts(self):
        import hold
        assert "triage-repair" in hold.LIFT_BECAUSE
        assert hold.lift_line("plan-critic-bound", triage_repair.LIFT_BECAUSE,
                              triage_repair.LIFT_BY) == LIFT

    def test_the_lift_line_carries_no_registry_tag(self):
        """docs/holds.md: neither hold line may carry a tag in
        config/pipeline-acts.json, or a `tag in body` count reads it as that
        act's receipt. The act is named `triage-repair`; its tag is not."""
        import pipeline_act
        assert pipeline_act.tag("triage-repair") == triage_repair.TRIAGE_REPAIR_TAG
        for act in pipeline_act.acts():
            assert pipeline_act.tag(act) not in LIFT, act
        receipt = pipeline_act.receipt("triage-repair", "🩹 triage-repair: started — x")
        assert triage_repair.TRIAGE_REPAIR_TAG in receipt


# --------------------------------------------------------------------------- #
# select                                                                       #
# --------------------------------------------------------------------------- #


class TestSelect:
    def test_dre_7066_shape_answers_repair_with_all_eight(self, tmp_path, capsys):
        board = TriageRepairBoard()
        _parked(board, "DRE-7066", NOTE_7066)
        assert len(FINDINGS_7066) == 8
        assert all(send_back_class.classify(f) is None for f in FINDINGS_7066)
        rc, rows = _select(board, tmp_path)
        assert rc == 0
        assert [r["card"] for r in rows] == ["DRE-7066"]
        row = rows[0]
        assert row["action"] == "repair"
        assert row["findings"] == FINDINGS_7066
        assert row["decisions"] == []
        assert row["max_turns"] == 120
        assert row["repo"] == "dreadnought-foundry/agent-bureau"
        assert set(row) == {"card", "title", "repo", "action", "why", "findings",
                            "decisions", "max_turns"}
        assert "triage-repair: DRE-7066 → repair — " in capsys.readouterr().out
        assert board.events == []  # select writes nothing to Linear

    def test_dre_7164_shape_reads_the_newest_park_note_all_seven(self, tmp_path):
        older = DECISION_NOTE.replace(DECISION_FINDING, "An older finding.")
        board = TriageRepairBoard()
        board.add("DRE-7164", comments=[older, "🔒 hold: reason=plan-critic-bound "
                                        "at=none lifts=manual by=plan.yml",
                                        "🔓 hold lifted: reason=plan-critic-bound "
                                        "because=operator by=plan.yml",
                                        NOTE_7164, MARKER_7164, STAMP])
        _, rows = _select(board, tmp_path)
        assert len(FINDINGS_7164) == 7
        assert rows[0]["action"] == "repair"
        assert rows[0]["findings"] == FINDINGS_7164

    def test_the_thread_is_read_whole_never_the_board_window(self, tmp_path):
        board = TriageRepairBoard()
        _parked(board, "DRE-7066", NOTE_7066)
        _select(board, tmp_path)
        assert ("comment_records", "DRE-7066", True) in board.reads
        assert ("comment_records", "DRE-7066", False) not in board.reads

    def test_a_pipeline_receipt_anywhere_is_spent(self, tmp_path, capsys):
        board = TriageRepairBoard()
        board.add("DRE-1", comments=[
            "🩹 triage-repair: rewritten — an earlier repair.", NOTE_7066, STAMP])
        _, rows = _select(board, tmp_path)
        assert rows == []
        assert "triage-repair: DRE-1 → spent — " in capsys.readouterr().out

    def test_a_receipt_from_another_author_grants_nothing(self, tmp_path):
        board = TriageRepairBoard()
        board.add("DRE-1", comments=[
            ("🩹 triage-repair: rewritten — typed by a person.", PERSON_USER),
            NOTE_7066, STAMP])
        _, rows = _select(board, tmp_path)
        assert [r["action"] for r in rows] == ["repair"]

    def test_a_started_receipt_sixty_comments_back_is_spent(self, tmp_path, capsys):
        board = TriageRepairBoard()
        board.add("DRE-1", comments=[NOTE_7066, STAMP])
        board.say("DRE-1", "🩹 triage-repair: started — the planner is rewriting it.")
        for i in range(56):
            board.say("DRE-1", f"⏳ a later planning attempt, comment {i}")
        # It parked again: a new note and a new stamp among the 59 newer.
        board.say("DRE-1", NOTE_7066)
        board.say("DRE-1", "plan-critic: stage=one-off round=2 result=SEND_BACK collisions=0")
        board.say("DRE-1", STAMP)
        bodies = board.bodies("DRE-1")
        assert bodies[-60].startswith("🩹 triage-repair: started")
        _, rows = _select(board, tmp_path)
        assert rows == []
        assert "triage-repair: DRE-1 → spent — " in capsys.readouterr().out
        assert _claim(board, "DRE-1") == 3
        assert board.events == []
        assert ("comment_records", "DRE-1", False) not in board.reads

    def test_a_decision_finding_makes_it_escalate(self, tmp_path):
        assert send_back_class.classify(DECISION_FINDING) == send_back_class.DECISION
        board = TriageRepairBoard()
        _parked(board, "DRE-2", DECISION_NOTE)
        _, rows = _select(board, tmp_path)
        assert rows[0]["action"] == "escalate"
        assert rows[0]["findings"] == [REVISION_FINDING, DECISION_FINDING]
        assert rows[0]["decisions"] == [DECISION_FINDING]

    def test_a_card_with_children_is_skipped_and_not_written(self, tmp_path, capsys):
        board = TriageRepairBoard()
        _parked(board, "DRE-3", NOTE_7066, children=["DRE-31", "DRE-32"])
        _, rows = _select(board, tmp_path)
        assert rows == []
        assert "triage-repair: DRE-3 → skip — " in capsys.readouterr().out

    def test_a_park_note_with_no_numbered_list_is_skipped(self, tmp_path, capsys):
        board = TriageRepairBoard()
        _parked(board, "DRE-4", "🛑 Parked — the second critic produced no result twice.")
        _, rows = _select(board, tmp_path)
        assert rows == []
        assert "triage-repair: DRE-4 → skip — " in capsys.readouterr().out

    def test_only_plan_critic_bound_holds_in_triage_are_in_scope(self, tmp_path, capsys):
        board = TriageRepairBoard()
        board.add("DRE-5", comments=[NOTE_7066, STAMP.replace(
            "plan-critic-bound", "epic-rereview-twice")])
        board.add("DRE-6", labels=["repo:agent-bureau"], comments=[NOTE_7066, STAMP])
        board.add("DRE-7", lane="Planning", comments=[NOTE_7066, STAMP])
        board.add("DRE-8", comments=[NOTE_7066, STAMP, LIFT])
        _, rows = _select(board, tmp_path)
        assert rows == []
        assert "triage-repair:" not in capsys.readouterr().out

    def test_oldest_park_first_and_at_most_limit(self, tmp_path):
        board = TriageRepairBoard()
        for card in ("DRE-10", "DRE-11", "DRE-12", "DRE-13"):
            board.add(card, comments=[STAMP])
        # Parked newest first, so the oldest park is the last card added.
        for card in ("DRE-13", "DRE-12", "DRE-11", "DRE-10"):
            board.say(card, NOTE_7066)
            board.say(card, STAMP)
        _, rows = _select(board, tmp_path)
        assert [r["card"] for r in rows] == ["DRE-13", "DRE-12", "DRE-11"]
        _, rows = _select(board, tmp_path, "--limit", "1")
        assert [r["card"] for r in rows] == ["DRE-13"]


# --------------------------------------------------------------------------- #
# claim                                                                        #
# --------------------------------------------------------------------------- #


class TestClaim:
    def test_claim_posts_started_with_the_description_fenced(self):
        board = TriageRepairBoard()
        _parked(board, "DRE-7066", NOTE_7066, description="Has ``` a fence inside.\n")
        assert _claim(board, "DRE-7066") == 0
        newest = board.newest("DRE-7066")
        assert newest.splitlines()[0].startswith("🩹 triage-repair: started")
        assert "````\nHas ``` a fence inside.\n" in newest
        assert [k for k, _ in board.writes()] == ["comment"]

    def test_claim_exits_3_on_a_pipeline_receipt_and_posts_nothing(self):
        board = TriageRepairBoard()
        board.add("DRE-1", comments=["🩹 triage-repair: asked — earlier.", NOTE_7066, STAMP])
        assert _claim(board, "DRE-1") == 3
        assert board.events == []

    def test_another_authors_receipt_does_not_stop_the_claim(self):
        board = TriageRepairBoard()
        board.add("DRE-1", comments=[("🩹 triage-repair: started — not ours.", PERSON_USER),
                                     NOTE_7066, STAMP])
        assert _claim(board, "DRE-1") == 0
        assert board.newest("DRE-1").startswith("🩹 triage-repair: started")


# --------------------------------------------------------------------------- #
# apply — the rewrite                                                          #
# --------------------------------------------------------------------------- #


class TestApplyRewritten:
    def test_dre_7066_end_to_end(self, tmp_path, capsys):
        board = TriageRepairBoard()
        _parked(board, "DRE-7066", NOTE_7066)
        _, rows = _select(board, tmp_path)
        assert rows[0]["action"] == "repair"
        assert _claim(board, "DRE-7066") == 0
        before = len(board.events)
        out = _write(tmp_path / "out", revised=REVISED, answers=_answers(8))
        assert _apply(board, "DRE-7066", "repair", out) == 0
        writes = board.writes("DRE-7066")[before:]
        assert [k for k, _ in writes] == ["description", "decisions-block",
                                          "remove-label", "comment", "state", "comment"]
        assert writes[0][1] == REVISED
        assert writes[2][1] == "needs-human"
        assert writes[3][1].splitlines()[0] == LIFT
        assert writes[4][1] == "Planning"
        receipt = writes[5][1]
        assert receipt.splitlines()[0].startswith("🩹 triage-repair: rewritten")
        at = 0
        for n, finding in enumerate(FINDINGS_7066, 1):
            at = receipt.index(f"{n}. {finding}", at)
            at = receipt.index(f"Answer {n}: the card now says so", at)
        assert board.lane("DRE-7066") == "Planning"
        assert "needs-human" not in board.labels("DRE-7066")
        assert board.description("DRE-7066") == REVISED
        assert "triage-repair: DRE-7066 → rewritten" in capsys.readouterr().out

    def test_a_rewrite_for_an_escalate_card_is_unfinished(self, tmp_path):
        board = TriageRepairBoard()
        _parked(board, "DRE-2", DECISION_NOTE)
        _claim(board, "DRE-2")
        before = len(board.events)
        out = _write(tmp_path / "out", revised=REVISED, answers=_answers(2))
        assert _apply(board, "DRE-2", "escalate", out) != 0
        assert [k for k, _ in board.writes("DRE-2")[before:]] == ["comment"]
        assert board.newest("DRE-2").startswith("🩹 triage-repair: unfinished")
        assert board.lane("DRE-2") == "Triage"
        assert "needs-human" in board.labels("DRE-2")
        assert board.description("DRE-2") == DESCRIPTION

    def test_a_repair_with_no_answers_is_unfinished(self, tmp_path):
        board = TriageRepairBoard()
        _parked(board, "DRE-1", NOTE_7066)
        out = _write(tmp_path / "out", revised=REVISED)
        assert _apply(board, "DRE-1", "repair", out) != 0
        assert [k for k, _ in board.writes("DRE-1")] == ["comment"]
        newest = board.newest("DRE-1")
        assert newest.startswith("🩹 triage-repair: unfinished")
        assert "repair-answers.md" in newest

    def test_a_failed_move_after_the_lift_is_unfinished_naming_it(self, tmp_path, capsys):
        board = TriageRepairBoard()
        _parked(board, "DRE-1", NOTE_7066)
        board.fail("state", "Planning")
        out = _write(tmp_path / "out", revised=REVISED, answers=_answers(8))
        assert _apply(board, "DRE-1", "repair", out) != 0
        newest = board.newest("DRE-1")
        assert newest.startswith("🩹 triage-repair: unfinished")
        assert "move the card to Planning" in newest
        for made in ("set the description", "write the Decisions block", "lift the hold"):
            assert made in newest
        assert not [b for b in _receipts(board, "DRE-1") if "rewritten" in b.splitlines()[0]]
        assert board.lane("DRE-1") == "Triage"
        assert "move the card to Planning" in capsys.readouterr().err

    def test_a_failed_set_description_lifts_and_moves_nothing(self, tmp_path):
        board = TriageRepairBoard()
        _parked(board, "DRE-1", NOTE_7066)
        board.fail("description")
        out = _write(tmp_path / "out", revised=REVISED, answers=_answers(8))
        assert _apply(board, "DRE-1", "repair", out) != 0
        kinds = [k for k, _ in board.writes("DRE-1")]
        assert kinds == ["comment"]
        assert "set the description" in board.newest("DRE-1")
        assert "needs-human" in board.labels("DRE-1")
        assert board.lane("DRE-1") == "Triage"


# --------------------------------------------------------------------------- #
# apply — the question, through the real escalation                           #
# --------------------------------------------------------------------------- #


def _escalation_notes(board, card):
    return [b for b in board.bodies(card) if planning_escalation.ESCALATION_TAG in b]


def _stand_downs(board, card):
    return [b for b in board.bodies(card) if planning_escalation.STOOD_DOWN_TAG in b]


class TestApplyAsked:
    def _decision_card(self, tmp_path):
        board = TriageRepairBoard()
        _parked(board, "DRE-2", DECISION_NOTE)
        _, rows = _select(board, tmp_path)
        assert rows[0]["action"] == "escalate"
        assert _claim(board, "DRE-2") == 0
        return board

    def _asked_end_state(self, board, card):
        assert board.lane(card) == "Green Light"
        assert "needs-human" not in board.labels(card)
        assert board.description(card) == DESCRIPTION
        notes = _escalation_notes(board, card)
        assert len(notes) == 1
        assert f"```{planning_escalation.CHOICES_FENCE}" in notes[0]
        assert not _stand_downs(board, card)
        assert board.newest(card).startswith("🩹 triage-repair: asked")

    def test_the_decision_route_ends_in_green_light(self, tmp_path, capsys):
        board = self._decision_card(tmp_path)
        out = _write(tmp_path / "out", question=QUESTION, choices=CHOICES)
        assert _apply(board, "DRE-2", "escalate", out) == 0
        self._asked_end_state(board, "DRE-2")
        kinds = [k for k, _ in board.writes("DRE-2")]
        # claim, then the lift (label, line), the note, the move, the receipt.
        assert kinds == ["comment", "remove-label", "comment", "comment", "state", "comment"]
        assert "triage-repair: DRE-2 → asked" in capsys.readouterr().out

    def test_without_from_lane_the_same_run_stays_in_triage(self, tmp_path, monkeypatch):
        board = self._decision_card(tmp_path)
        real = planning_escalation.escalate

        def as_before(*args, from_lane=None, **kw):
            return real(*args, **kw)

        monkeypatch.setattr(planning_escalation, "escalate", as_before)
        out = _write(tmp_path / "out", question=QUESTION, choices=CHOICES)
        assert _apply(board, "DRE-2", "escalate", out) != 0
        assert board.lane("DRE-2") == "Triage"
        assert not _escalation_notes(board, "DRE-2")
        assert board.newest("DRE-2").startswith("🩹 triage-repair: unfinished")

    @pytest.mark.parametrize("with_rewrite", [False, True])
    def test_a_repair_card_whose_planner_asked_ends_the_same(self, tmp_path, with_rewrite):
        board = TriageRepairBoard()
        _parked(board, "DRE-7066", NOTE_7066)
        _claim(board, "DRE-7066")
        files = {"question": QUESTION, "choices": CHOICES}
        if with_rewrite:
            files.update(revised=REVISED, answers=_answers(8))
        out = _write(tmp_path / "out", **files)
        assert _apply(board, "DRE-7066", "repair", out) == 0
        self._asked_end_state(board, "DRE-7066")
        assert "description" not in [k for k, _ in board.writes("DRE-7066")]
        set_aside = "set aside" in board.newest("DRE-7066")
        assert set_aside is with_rewrite

    def test_a_card_moved_on_before_apply_stands_down_unfinished(self, tmp_path):
        board = self._decision_card(tmp_path)
        board.move("DRE-2", "In Progress")
        out = _write(tmp_path / "out", question=QUESTION, choices=CHOICES)
        assert _apply(board, "DRE-2", "escalate", out) != 0
        assert board.lane("DRE-2") == "In Progress"
        assert len(_stand_downs(board, "DRE-2")) == 1
        newest = board.newest("DRE-2")
        assert newest.startswith("🩹 triage-repair: unfinished")
        assert "it is in In Progress" in newest and "lift the hold" in newest
        assert not [b for b in _receipts(board, "DRE-2") if "asked" in b.splitlines()[0]]

    def test_a_refused_question_is_unfinished_before_any_write(self, tmp_path):
        board = self._decision_card(tmp_path)
        before = len(board.events)
        out = _write(tmp_path / "out", question="Should `reconcile.py` read the flag?")
        assert planning_escalation.refusal("Should `reconcile.py` read the flag?")
        assert _apply(board, "DRE-2", "escalate", out) != 0
        assert [k for k, _ in board.writes("DRE-2")[before:]] == ["comment"]
        assert board.newest("DRE-2").startswith("🩹 triage-repair: unfinished")
        assert board.lane("DRE-2") == "Triage"
        assert "needs-human" in board.labels("DRE-2")
