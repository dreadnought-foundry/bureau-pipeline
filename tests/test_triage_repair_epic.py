"""RED-first tests: an epic parked at the plan critic's bound goes back to
Planning once, with every finding in front of the planner (DRE-7181).

DRE-7072 parked at the second critic's bound at 4:25 PM PT on 2026-10-12 with
no `🔁 bound-rewrite:` receipt, and an operator moved it back to Planning at
5:39 PM PT, where both critics passed the fresh attempt. `triage_repair.py`
now makes that move itself, once per card: `select` answers `replan` for a
card with children that has had no bound rewrite, and `apply --action replan`
posts the bound-rewrite receipt (`plan_bound.post_receipt`) with every finding
the bound left open, lifts the hold, moves the card to Planning and posts
`🩹 triage-repair: replanned`, in that order.

Every scenario runs over `TriageRepairBoard`, so each asserts the lane a card
ends in and the comments it carries. DRE-7072's thread is the fixture
`tests/fixtures/dre-7072-bound-park-2026-10-12.json`, verbatim off Linear.

Run: cd bureau-pipeline && python3 -m pytest tests/test_triage_repair_epic.py -v
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

import linear_ops  # noqa: E402
import pipeline_act  # noqa: E402
import plan_bound  # noqa: E402
import plan_critic  # noqa: E402
import review_rerun  # noqa: E402
import triage_repair  # noqa: E402
from triage_repair_fakes import PERSON_USER, TriageRepairBoard  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "dre-7072-bound-park-2026-10-12.json"
CARD = "DRE-7072"
CHILDREN = ["DRE-7105", "DRE-7106", "DRE-7107", "DRE-7130"]

STAMP = "🔒 hold: reason=plan-critic-bound at=none lifts=manual by=plan.yml"
LIFT = ("🔓 hold lifted: reason=plan-critic-bound because=triage-repair "
        "by=triage_repair.py")


def _thread() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["comments"]


def _bound_round_note() -> str:
    """The second critic's round-3 note: the round that reached the bound."""
    return next(c["body"] for c in _thread()
                if c["body"].startswith("🛑 **Second critic")
                and "round 3:" in c["body"].splitlines()[0])


def _items(note: str) -> list[str]:
    return [line.split(". ", 1)[1] for line in note.splitlines()
            if line[:1].isdigit() and ". " in line]


#: Every finding the bound left open: the five of the round-3 note.
FINDINGS_7072 = _items(_bound_round_note())


def _epic(board: TriageRepairBoard, *, extra=(), card: str = CARD):
    """DRE-7072 as it stood parked: its thread, its children, held in Triage."""
    comments = [c["body"] if c["authored_by_pipeline"] else (c["body"], PERSON_USER)
                for c in _thread()]
    return board.add(card, comments=[*comments, *extra], children=CHILDREN,
                     title="Todo retires, and a card goes straight from Queued "
                           "to being built")


def _select(board, tmp_path, *extra) -> tuple[int, list]:
    out = tmp_path / "selected.json"
    with board.patched():
        rc = triage_repair.main(["select", "--out", str(out), *extra])
    return rc, json.loads(out.read_text(encoding="utf-8"))


def _claim(board, card=CARD) -> int:
    with board.patched():
        return triage_repair.main(["claim", card])


def _apply(board, card=CARD, *extra) -> int:
    with board.patched():
        return triage_repair.main(["apply", card, "--action", "replan", *extra])


def _bound_receipts(board, card=CARD) -> list[str]:
    return [b for b in board.bodies(card) if b.startswith(plan_bound.RECEIPT_KEY)]


def _repair_receipts(board, card=CARD) -> list[str]:
    return [b for b in board.bodies(card) if b.startswith(triage_repair.RECEIPT_KEY)]


def _records(board, card=CARD) -> list[dict]:
    with board.patched():
        return linear_ops.comment_records(card, whole_thread=True)


def _context_findings(records) -> list[str]:
    """What a fresh planning attempt reads: the numbered lines of
    `plan_bound.context_section`, each without its number and class word."""
    out = []
    for line in plan_bound.context_section(records).splitlines():
        match = plan_bound._NUMBERED.match(line.strip())
        if match:
            out.append(match.group("text"))
    return out


def _flat(text: str) -> str:
    return " ".join(text.split())


# --------------------------------------------------------------------------- #
# the fixture is DRE-7072's shape                                              #
# --------------------------------------------------------------------------- #


class TestTheShape:
    def test_parked_after_the_second_critics_third_round_with_no_receipt(self):
        thread = _thread()
        bodies = [c["body"] for c in thread]
        cycle = plan_critic.current_cycle_entries(thread, CARD)
        rows = plan_critic.parse_markers(cycle)
        assert (rows[-1]["stage"], rows[-1]["round"], rows[-1]["result"]) == (
            "post", 3, "SEND_BACK")
        assert STAMP in bodies
        assert bodies[-1].startswith("🛑 Parked in Triage")
        assert not plan_bound.granted(thread)
        assert len(FINDINGS_7072) == 5

    def test_the_park_note_itself_carries_no_findings_list(self):
        """Why the epic's findings are read off the bound ROUND: the
        `Parked in Triage` note says the bound's sentence, not its list."""
        assert triage_repair.park_findings(_thread()[-1]["body"]) == []


# --------------------------------------------------------------------------- #
# select                                                                       #
# --------------------------------------------------------------------------- #


class TestSelect:
    def test_dre_7072_answers_replan_with_every_finding(self, tmp_path, capsys):
        board = TriageRepairBoard()
        _epic(board)
        rc, rows = _select(board, tmp_path)
        assert rc == 0
        assert [r["card"] for r in rows] == [CARD]
        row = rows[0]
        assert row["action"] == "replan"
        assert [_flat(f) for f in row["findings"]] == [_flat(f) for f in FINDINGS_7072]
        assert set(row) == {"card", "title", "repo", "action", "why", "findings",
                            "decisions", "max_turns"}
        assert f"triage-repair: {CARD} → replan — " in capsys.readouterr().out
        assert board.events == []
        assert ("comment_records", CARD, True) in board.reads

    def test_a_pipeline_bound_rewrite_receipt_answers_spent(self, tmp_path, capsys):
        board = TriageRepairBoard()
        receipt = pipeline_act.receipt(
            "plan-bound-rewrite", plan_bound.receipt_body(["An earlier finding."]))
        _epic(board, extra=[receipt])
        _, rows = _select(board, tmp_path)
        assert rows == []
        out = capsys.readouterr().out
        assert f"triage-repair: {CARD} → spent — " in out
        assert "bound rewrite" in out

    def test_a_bound_rewrite_receipt_sixty_comments_back_answers_spent(
            self, tmp_path, capsys):
        board = TriageRepairBoard()
        receipt = pipeline_act.receipt(
            "plan-bound-rewrite", plan_bound.receipt_body(["An earlier finding."]))
        board.add(CARD, comments=[plan_critic.cycle_marker(CARD), receipt],
                  children=CHILDREN)
        for i in range(55):
            board.say(CARD, f"⏳ a later planning attempt, comment {i}")
        # It parked again: the bound round's note and record, the stamp, the note.
        board.say(CARD, _bound_round_note())
        board.say(CARD, "plan-critic: stage=post round=3 result=SEND_BACK "
                        "collisions=0 open=0 — a finding")
        board.say(CARD, STAMP)
        board.say(CARD, _thread()[-1]["body"])
        bodies = board.bodies(CARD)
        assert bodies[-60].startswith(plan_bound.RECEIPT_KEY)
        assert len(bodies) - bodies.index(receipt) == 60
        _, rows = _select(board, tmp_path)
        assert rows == []
        assert f"triage-repair: {CARD} → spent — " in capsys.readouterr().out
        assert ("comment_records", CARD, False) not in board.reads

    def test_a_bound_rewrite_receipt_from_a_person_grants_nothing(self, tmp_path):
        board = TriageRepairBoard()
        _epic(board, extra=[(plan_bound.RECEIPT_OPENING + "\n\n1. (revision) x",
                             PERSON_USER)])
        _, rows = _select(board, tmp_path)
        assert [r["action"] for r in rows] == ["replan"]

    def test_a_triage_repair_receipt_answers_spent(self, tmp_path, capsys):
        board = TriageRepairBoard()
        _epic(board, extra=["🩹 triage-repair: replanned — an earlier repair."])
        _, rows = _select(board, tmp_path)
        assert rows == []
        assert f"triage-repair: {CARD} → spent — " in capsys.readouterr().out

    def test_without_children_the_same_thread_is_not_replanned(self, tmp_path, capsys):
        """No children: the one-off reading is DRE-7178's and is unchanged —
        its park note carries no list, so it is skipped."""
        board = TriageRepairBoard()
        board.add("DRE-1", comments=[c["body"] for c in _thread()
                                     if c["authored_by_pipeline"]])
        _, rows = _select(board, tmp_path)
        assert rows == []
        assert "triage-repair: DRE-1 → skip — " in capsys.readouterr().out


class TestEpicFindings:
    def test_a_single_finding_bound_round_reads_its_record(self):
        """A round with one finding prints no list (`findings_section`), so
        its record's reason is the finding."""
        records = [
            {"body": plan_critic.cycle_marker(CARD), "authored_by_pipeline": True},
            {"body": "🛑 **Second critic** — round 3: the bound.\n\nReason: The one gap.",
             "authored_by_pipeline": True},
            {"body": "plan-critic: stage=post round=3 result=SEND_BACK collisions=0 "
                     "open=0 — The one gap.", "authored_by_pipeline": True},
            {"body": STAMP, "authored_by_pipeline": True},
            {"body": "🛑 Parked in Triage with needs-human for an operator.",
             "authored_by_pipeline": True},
        ]
        assert triage_repair.epic_findings(records, CARD) == ["The one gap."]

    def test_an_earlier_attempts_round_is_never_read(self):
        records = [
            {"body": "plan-critic: stage=post round=3 result=SEND_BACK collisions=0 "
                     "open=0 — An answered gap.", "authored_by_pipeline": True},
            {"body": plan_critic.cycle_marker(CARD), "authored_by_pipeline": True},
            {"body": STAMP, "authored_by_pipeline": True},
            {"body": "🛑 Parked in Triage with needs-human for an operator.",
             "authored_by_pipeline": True},
        ]
        assert triage_repair.epic_findings(records, CARD) == []

    def test_a_park_after_a_round_that_passed_reads_the_park_note(self):
        """The newest record is not a send-back (the revision did not pass the
        plan's gates, say): the park note's own list is what stands."""
        note = ("🛑 The planner was asked to revise this card to answer the "
                "critic, and the revision did not finish.\n\nWhat the critic "
                "found:\n\n1. First gap.\n2. Second gap.\n")
        records = [
            {"body": plan_critic.cycle_marker(CARD), "authored_by_pipeline": True},
            {"body": "plan-critic: stage=post round=1 result=SEND_BACK collisions=0 "
                     "— An answered gap.", "authored_by_pipeline": True},
            {"body": "plan-critic: stage=post round=2 result=PASS collisions=0",
             "authored_by_pipeline": True},
            {"body": STAMP, "authored_by_pipeline": True},
            {"body": note, "authored_by_pipeline": True},
        ]
        assert triage_repair.epic_findings(records, CARD) == ["First gap.", "Second gap."]


# --------------------------------------------------------------------------- #
# apply --action replan                                                        #
# --------------------------------------------------------------------------- #


class TestApplyReplanned:
    def test_dre_7072_end_to_end(self, tmp_path, capsys):
        board = TriageRepairBoard()
        _epic(board)
        _, rows = _select(board, tmp_path)
        assert rows[0]["action"] == "replan"
        assert _claim(board) == 0
        before = len(board.events)
        assert _apply(board) == 0
        writes = board.writes(CARD)[before:]
        assert [k for k, _ in writes] == ["comment", "remove-label", "comment",
                                          "state", "comment"]
        # 1. the bound-rewrite receipt, every finding numbered
        receipt = writes[0][1]
        assert receipt.startswith(plan_bound.RECEIPT_KEY)
        assert pipeline_act.read_trailer(receipt)["act"] == "plan-bound-rewrite"
        records = _records(board)
        assert _context_findings(records) == [_flat(f) for f in FINDINGS_7072]
        assert plan_bound.granted(records)
        # 2. the lift, because=triage-repair
        assert writes[1][1] == "needs-human"
        assert writes[2][1].splitlines()[0] == LIFT
        # 3. the move to Planning
        assert writes[3][1] == "Planning"
        # 4. the outcome receipt, naming every finding, LAST
        outcome = writes[4][1]
        assert outcome.splitlines()[0].startswith("🩹 triage-repair: replanned")
        assert triage_repair.outcome_of(outcome) == "replanned"
        at = 0
        for n, finding in enumerate(FINDINGS_7072, 1):
            at = outcome.index(f"{n}. {_flat(finding)}", at)
        assert board.lane(CARD) == "Planning"
        assert "needs-human" not in board.labels(CARD)
        assert board.description(CARD) == "The card as it stood.\n"
        assert f"triage-repair: {CARD} → replanned" in capsys.readouterr().out

    def test_after_the_replan_the_card_reads_spent(self, tmp_path, capsys):
        board = TriageRepairBoard()
        _epic(board)
        _claim(board)
        assert _apply(board) == 0
        # Parked again by the fresh attempt's critics.
        board.move(CARD, "Triage")
        board.cards[CARD].labels.append("needs-human")
        board.say(CARD, STAMP)
        capsys.readouterr()
        _, rows = _select(board, tmp_path)
        assert rows == []
        assert f"triage-repair: {CARD} → spent — " in capsys.readouterr().out

    def test_a_failed_bound_rewrite_receipt_lifts_and_moves_nothing(
            self, tmp_path, capsys):
        board = TriageRepairBoard()
        _epic(board)
        _claim(board)
        before = len(board.events)
        board.fail("comment", plan_bound.RECEIPT_KEY)
        assert _apply(board) != 0
        writes = board.writes(CARD)[before:]
        assert [k for k, _ in writes] == ["comment"]
        newest = board.newest(CARD)
        assert newest.startswith("🩹 triage-repair: unfinished")
        assert triage_repair.outcome_of(newest) == "unfinished"
        assert "post the bound-rewrite receipt" in newest
        assert "Writes already made: none" in newest
        assert board.lane(CARD) == "Triage"
        assert "needs-human" in board.labels(CARD)
        assert not _bound_receipts(board)
        assert f"triage-repair: {CARD} → unfinished" in capsys.readouterr().out

    def test_a_failed_move_is_unfinished_naming_the_writes_made(self, tmp_path):
        board = TriageRepairBoard()
        _epic(board)
        _claim(board)
        board.fail("state", "Planning")
        assert _apply(board) != 0
        newest = board.newest(CARD)
        assert newest.startswith("🩹 triage-repair: unfinished")
        assert "move the card to Planning" in newest
        for made in ("post the bound-rewrite receipt", "lift the hold"):
            assert made in newest.split("Writes already made:", 1)[1]
        assert not [b for b in _repair_receipts(board)
                    if triage_repair.outcome_of(b) == "replanned"]
        assert board.lane(CARD) == "Triage"

    def test_a_bound_rewrite_granted_since_select_is_unfinished(self, tmp_path):
        """The planner has had its rewrite by the time apply runs: a second
        receipt is never posted, and nothing is lifted or moved."""
        board = TriageRepairBoard()
        _epic(board)
        _claim(board)
        board.say(CARD, pipeline_act.receipt(
            "plan-bound-rewrite", plan_bound.receipt_body(["A finding."])))
        before = len(board.events)
        assert _apply(board) != 0
        assert [k for k, _ in board.writes(CARD)[before:]] == ["comment"]
        assert len(_bound_receipts(board)) == 1
        assert board.newest(CARD).startswith("🩹 triage-repair: unfinished")
        assert board.lane(CARD) == "Triage"
        assert "needs-human" in board.labels(CARD)

    def test_an_apply_with_no_claim_posts_nothing(self):
        board = TriageRepairBoard()
        _epic(board)
        assert _apply(board) != 0
        assert board.events == []
        assert board.lane(CARD) == "Triage"

    def test_a_card_moved_out_of_triage_after_the_claim_is_not_replanned(self):
        board = TriageRepairBoard()
        _epic(board)
        _claim(board)
        board.move(CARD, "Planning")
        before = len(board.events)
        assert _apply(board) != 0
        assert [k for k, _ in board.writes(CARD)[before:]] == ["comment"]
        assert not _bound_receipts(board)
        assert "no longer in Triage" in board.newest(CARD)

    def test_the_cli_takes_replan_without_a_dir(self):
        assert "replan" in triage_repair.APPLIED
        board = TriageRepairBoard()
        _epic(board)
        _claim(board)
        with board.patched():
            assert triage_repair.main(["apply", CARD, "--action", "replan"]) == 0


# --------------------------------------------------------------------------- #
# plan_bound.post_receipt — the receipt half of `_rewrite`                     #
# --------------------------------------------------------------------------- #


class TestPostReceipt:
    def _capture(self, monkeypatch, problem=None, raises=None):
        posted = []

        def cmd_comment(card, body, *flags):
            if raises is not None:
                raise raises
            posted.append((card, body))
            return problem

        monkeypatch.setattr(plan_bound.linear_ops, "cmd_comment", cmd_comment)
        return posted

    def test_it_posts_the_receipt_and_answers_none(self, monkeypatch):
        posted = self._capture(monkeypatch)
        found = ["The card doesn't say which lane.", "A step nothing runs."]
        assert plan_bound.post_receipt("DRE-1", found) is None
        assert posted == [("DRE-1", pipeline_act.receipt(
            "plan-bound-rewrite", plan_bound.receipt_body(found)))]

    @pytest.mark.parametrize("problem,raises", [
        ("comment cap reached", None),
        (None, linear_ops.LinearError("HTTP 500")),
    ])
    def test_it_answers_the_problem_when_the_post_fails(self, monkeypatch,
                                                        problem, raises):
        self._capture(monkeypatch, problem=problem, raises=raises)
        said = plan_bound.post_receipt("DRE-1", ["A finding."])
        assert said and ("comment cap" in said or "HTTP 500" in said)

    def test_rewrite_posts_through_post_receipt_after_its_dispatch(self, monkeypatch):
        order = []
        monkeypatch.setattr(review_rerun, "_cmd_dispatch",
                            lambda ns: order.append(("dispatch", ns.epic)) or 0)
        real = plan_bound.post_receipt

        def spy(card, found, *args, **kw):
            order.append(("post_receipt", card))
            return real(card, found, *args, **kw)

        monkeypatch.setattr(plan_bound, "post_receipt", spy)
        posted = self._capture(monkeypatch)
        found = ["A finding."]
        assert plan_bound._rewrite("DRE-1", "o/r", found) == plan_bound.EXIT_HANDLED
        assert order == [("dispatch", "DRE-1"), ("post_receipt", "DRE-1")]
        assert posted == [("DRE-1", pipeline_act.receipt(
            "plan-bound-rewrite", plan_bound.receipt_body(found)))]

    def test_rewrite_still_crashes_on_a_failed_post(self, monkeypatch, capsys):
        monkeypatch.setattr(review_rerun, "_cmd_dispatch", lambda ns: 0)
        self._capture(monkeypatch, problem="comment cap reached")
        assert plan_bound._rewrite("DRE-1", "o/r", ["A finding."]) == plan_bound.EXIT_CRASH
        assert "comment cap reached" in capsys.readouterr().err

    def test_the_receipt_is_still_emitted_from_plan_bound(self):
        source = (ROOT / "scripts" / "plan_bound.py").read_text(encoding="utf-8")
        assert source.count('pipeline_act.receipt("plan-bound-rewrite"') == 1
        repair = (ROOT / "scripts" / "triage_repair.py").read_text(encoding="utf-8")
        assert "plan-bound-rewrite" not in repair
        assert "plan_bound.post_receipt(" in repair
