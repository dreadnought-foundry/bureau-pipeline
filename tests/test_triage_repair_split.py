"""RED-first tests: a Triage repair splits a card that is two jobs (DRE-7182).

Two of the four cards parked on 2026-10-12 had a finding saying they were two
jobs, and the operator split each by hand: DRE-7164 was narrowed and DRE-7166
filed beside it, and DRE-2994 was restamped `epic` and planned as one. The
planner now makes either move in the repair, through the seams that already
exist:

  * **siblings** — `revised-card.md` narrowed to the first piece, and each other
    piece written as `sibling-<n>.md`. `apply` files each one through the seam
    its parent decides: `mid_epic.discovery` (an addition) under an epic that is
    being built, `linear_ops.cmd_subissue` under any other parent, and
    `linear_ops.cmd_oneoff` with the card's own `repo:`, `agent:` and
    `initiative:` labels when there is none.
  * **an epic** — `reshape.txt` reading `epic`, stamped through
    `planning_shape.stamp` as `by: planner` with the model the run used.

Neither this file nor the code names the lane a filed sibling lands in: the
seam decides it, and the tests assert which seam was called and with what.

Every scenario runs over `TriageRepairBoard`; the question route runs the real
`planning_escalation.escalate` over the real `config/lane-contract.json`.

Run: cd bureau-pipeline && python3 -m pytest tests/test_triage_repair_split.py -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")

import linear_ops  # noqa: E402
import mid_epic  # noqa: E402
import planning_shape  # noqa: E402
import test_triage_repair as base  # noqa: E402
import test_triage_repair_workflow as wiring  # noqa: E402
import triage_repair  # noqa: E402
from triage_repair_fakes import TriageRepairBoard  # noqa: E402

MODEL = "claude-opus-5-5"
LABELS = ("needs-human", "repo:agent-bureau", "agent:engineer", "initiative:pipeline")

#: DRE-7164, narrowed to its first piece: the reader that works out whether a
#: hold was later cleared.
NARROWED = (
    "Work out, for every held train, whether a later promotion cleared it.\n\n"
    "**Files:** console/backend/train_yard.py, console/backend/tests/test_train_yard.py\n\n"
    "## Acceptance criteria\n\n- [ ] a held train a later promotion contains reads `passed`\n"
)
SIBLING_TITLE = "agent-bureau: the Train Yard shows whether a held train was later cleared"
SIBLING_BODY = (
    "Show the cleared-or-not answer on the Train Yard's held trains.\n\n"
    "**Files:** console/src/pages/TrainYard.tsx, console/src/pages/TrainYard.test.tsx\n\n"
    "## Acceptance criteria\n\n- [ ] a held train shows `passed` once a later promotion clears it\n\n"
    "**Blocked by:** DRE-7164\n"
)
SECOND_TITLE = "agent-bureau: the Train Yard's held trains link to the promotion that cleared them"
SECOND_BODY = (
    "Link each cleared held train to its promotion.\n\n"
    "**Files:** console/src/pages/TrainYard.tsx\n\n"
    "## Acceptance criteria\n\n- [ ] a cleared held train links to its promotion\n\n"
    "**Blocked by:** DRE-7164\n"
)

#: DRE-2994's park note, in the "revision did not finish" shape: its first
#: finding says the card is an epic of two pieces.
EPIC_FINDING = ("This card is now an epic of two separate pieces of work: the "
                "nightly reader and the page that shows it.")
NOTE_2994 = (
    "🛑 The planner was asked to revise this card to answer the critic, and the "
    "revision did not finish — it wrote no revised card, or no note saying what "
    "it changed. The card parks in Triage for the operator; it has not been "
    "built and is not waiting on the CEO.\n\nWhat the critic found:\n\n"
    f"1. {EPIC_FINDING}\n"
    "2. The card never names the file the report is written to, so the build "
    "has to guess it.\n\n"
    "Fix the card and send it back through Planning, or take it out of the queue."
)

#: The lane words a filed sibling might land in, spelled so this file does not
#: carry them either.
LANE_WORDS = ("Back" "log", "Que" "ued")


def _sibling(title: str, body: str) -> str:
    return f"# {title}\n\n{body}"


def _write(directory: Path, *, siblings=(), reshape=None, **files) -> Path:
    base._write(directory, **files)
    for n, text in enumerate(siblings, 1):
        (directory / f"sibling-{n}.md").write_text(text, encoding="utf-8")
    if reshape is not None:
        (directory / "reshape.txt").write_text(reshape, encoding="utf-8")
    return directory


def _apply(board, card, directory, *, action="repair", model=MODEL) -> int:
    argv = ["apply", card, "--action", action, "--dir", str(directory)]
    if model is not None:
        argv += ["--model", model]
    with board.patched():
        return triage_repair.main(argv)


class Seams:
    """The three filing seams, recorded. Each filing is a board event, so a
    test reads its place among the card's other writes. `fail_on(n)` refuses
    the n-th filing the way a Linear error does."""

    def __init__(self, board, monkeypatch):
        self.board = board
        self.calls: list[tuple] = []
        self.filed: list[str] = []
        self._next = 9001
        self._fail_at = None
        monkeypatch.setattr(linear_ops, "cmd_oneoff", self.oneoff)
        monkeypatch.setattr(linear_ops, "cmd_subissue", self.subissue)
        monkeypatch.setattr(mid_epic, "discovery", self.discovery)

    def fail_on(self, n: int) -> None:
        self._fail_at = n

    def _file(self, seam: str, title: str, call: tuple) -> str:
        self.calls.append(call)
        if self._fail_at == len(self.calls):
            raise linear_ops.LinearError("the create was refused")
        ident = f"DRE-{self._next}"
        self._next += 1
        self.filed.append(ident)
        self.board.events.append(("file", ident, f"{seam}: {title}"))
        return ident

    def oneoff(self, title, description_file, *flags):
        ident = self._file("oneoff", title, ("oneoff", title, description_file, flags))
        return {"id": f"id-{ident}", "identifier": ident, "url": f"https://x/{ident}"}

    def subissue(self, parent, title, description_file, *flags):
        ident = self._file("subissue", title,
                           ("subissue", parent, title, description_file, flags))
        return {"id": f"id-{ident}", "identifier": ident, "url": f"https://x/{ident}"}

    def discovery(self, lops, epic, *, kind, because, title=None, body=None,
                  labels=(), verdict=None, why=None):
        return self._file("discovery", title, ("discovery", epic, kind, because, title, body))

    def seams(self) -> list[str]:
        return [call[0] for call in self.calls]


@pytest.fixture
def board():
    return TriageRepairBoard()


@pytest.fixture
def seams(board, monkeypatch):
    return Seams(board, monkeypatch)


def _parked_7164(board, **kw):
    kw.setdefault("labels", LABELS)
    base._parked(board, "DRE-7164", base.NOTE_7164, **kw)
    assert base._claim(board, "DRE-7164") == 0
    return len(board.events)


def _kinds(board, before: int) -> list[str]:
    return [kind for kind, _card, _detail in board.events[before:]]


# --------------------------------------------------------------------------- #
# (a) siblings                                                                 #
# --------------------------------------------------------------------------- #


class TestSiblings:
    def test_dre_7164_shape_files_through_oneoff_then_lifts_moves_and_receipts(
            self, board, seams, tmp_path, capsys):
        before = _parked_7164(board)
        out = _write(tmp_path / "out", revised=NARROWED, answers=base._answers(7),
                     siblings=[_sibling(SIBLING_TITLE, SIBLING_BODY)])
        assert _apply(board, "DRE-7164", out) == 0

        # Its own writes, then the lift, the move and the receipt, in that order.
        assert _kinds(board, before) == ["description", "decisions-block", "file",
                                         "remove-label", "comment", "state", "comment"]
        assert board.description("DRE-7164") == NARROWED
        assert seams.seams() == ["oneoff"]
        _, title, body, flags = seams.calls[0]
        assert title == SIBLING_TITLE
        assert body.strip() == SIBLING_BODY.strip()
        assert "**Blocked by:** DRE-7164" in body
        assert not body.lstrip().startswith("#")
        assert flags == ("--label", "repo:agent-bureau", "--label", "agent:engineer",
                         "--label", "initiative:pipeline")

        assert board.lane("DRE-7164") == "Planning"
        assert "needs-human" not in board.labels("DRE-7164")
        receipt = board.newest("DRE-7164")
        assert receipt.splitlines()[0].startswith("🩹 triage-repair: split")
        assert "DRE-9001" in receipt and SIBLING_TITLE in receipt
        at = 0
        for n, finding in enumerate(base.FINDINGS_7164, 1):
            at = receipt.index(f"{n}. {finding}", at)
            at = receipt.index(f"Answer {n}: the card now says so", at)
        assert "triage-repair: DRE-7164 → split" in capsys.readouterr().out

    def test_the_receipt_key_reads_split_as_a_finished_outcome(self, board, seams, tmp_path):
        _parked_7164(board)
        out = _write(tmp_path / "out", revised=NARROWED, answers=base._answers(7),
                     siblings=[_sibling(SIBLING_TITLE, SIBLING_BODY)])
        assert _apply(board, "DRE-7164", out) == 0
        records = [{"body": b, "authored_by_pipeline": True} for b in board.bodies("DRE-7164")]
        assert triage_repair.outcome_of(triage_repair.receipts(records)[-1]) == "split"

    def test_every_sibling_is_filed_in_its_number_order(self, board, seams, tmp_path):
        before = _parked_7164(board)
        out = _write(tmp_path / "out", revised=NARROWED, answers=base._answers(7),
                     siblings=[_sibling(SIBLING_TITLE, SIBLING_BODY),
                               _sibling(SECOND_TITLE, SECOND_BODY)])
        assert _apply(board, "DRE-7164", out) == 0
        assert [call[1] for call in seams.calls] == [SIBLING_TITLE, SECOND_TITLE]
        assert _kinds(board, before).count("file") == 2
        receipt = board.newest("DRE-7164")
        assert receipt.index("DRE-9001") < receipt.index("DRE-9002")

    def test_a_card_under_an_epic_being_built_files_through_mid_epic_discovery(
            self, board, seams, tmp_path):
        board.add("DRE-7000", lane="In Progress", labels=("repo:agent-bureau", "agent:planner"),
                  children=["DRE-7164"], comments=[])
        _parked_7164(board, parent="DRE-7000")
        out = _write(tmp_path / "out", revised=NARROWED, answers=base._answers(7),
                     siblings=[_sibling(SIBLING_TITLE, SIBLING_BODY)])
        assert _apply(board, "DRE-7164", out) == 0
        assert seams.seams() == ["discovery"]
        _, epic, kind, because, title, body = seams.calls[0]
        assert (epic, kind, title) == ("DRE-7000", "addition", SIBLING_TITLE)
        assert because == base.FINDINGS_7164[0]
        assert body.strip() == SIBLING_BODY.strip()
        assert board.lane("DRE-7164") == "Planning"
        assert board.newest("DRE-7164").startswith("🩹 triage-repair: split")

    def test_a_card_under_any_other_parent_files_through_subissue(
            self, board, seams, tmp_path):
        board.add("DRE-7000", lane="Green Light", labels=("repo:agent-bureau", "agent:planner"),
                  children=["DRE-7164"], comments=[])
        _parked_7164(board, parent="DRE-7000")
        out = _write(tmp_path / "out", revised=NARROWED, answers=base._answers(7),
                     siblings=[_sibling(SIBLING_TITLE, SIBLING_BODY)])
        assert _apply(board, "DRE-7164", out) == 0
        assert seams.seams() == ["subissue"]
        _, parent, title, body, flags = seams.calls[0]
        assert (parent, title, flags) == ("DRE-7000", SIBLING_TITLE, ())
        assert body.strip() == SIBLING_BODY.strip()

    def test_a_sibling_that_fails_to_file_leaves_the_card_held_in_triage(
            self, board, seams, tmp_path, capsys):
        before = _parked_7164(board)
        seams.fail_on(2)
        out = _write(tmp_path / "out", revised=NARROWED, answers=base._answers(7),
                     siblings=[_sibling(SIBLING_TITLE, SIBLING_BODY),
                               _sibling(SECOND_TITLE, SECOND_BODY)])
        assert _apply(board, "DRE-7164", out) != 0
        assert board.lane("DRE-7164") == "Triage"
        assert "needs-human" in board.labels("DRE-7164")
        kinds = _kinds(board, before)
        assert "state" not in kinds and "remove-label" not in kinds
        # The sibling already filed stays, and the receipt names it.
        assert seams.filed == ["DRE-9001"]
        newest = board.newest("DRE-7164")
        assert newest.startswith("🩹 triage-repair: unfinished")
        assert "DRE-9001" in newest
        assert "sibling-2.md" in newest
        assert "not filed" in newest.lower()
        assert not [b for b in base._receipts(board, "DRE-7164")
                    if b.splitlines()[0].startswith("🩹 triage-repair: split")]
        assert "triage-repair: DRE-7164 → unfinished" in capsys.readouterr().out

    def test_a_question_beside_a_sibling_files_nothing_and_ends_in_green_light(
            self, board, seams, tmp_path):
        """The Contract: a non-empty question.md wins, whichever files stand
        beside it. The real `planning_escalation.escalate` moves the card."""
        _parked_7164(board)
        out = _write(tmp_path / "out", revised=NARROWED, answers=base._answers(7),
                     question=base.QUESTION, choices=base.CHOICES,
                     siblings=[_sibling(SIBLING_TITLE, SIBLING_BODY)])
        assert _apply(board, "DRE-7164", out) == 0
        assert seams.calls == []
        assert board.lane("DRE-7164") == "Green Light"
        assert board.newest("DRE-7164").startswith("🩹 triage-repair: asked")
        assert board.description("DRE-7164") == base.DESCRIPTION

    @pytest.mark.parametrize("bad, words", [
        ("no title line\n\n" + SIBLING_BODY, "sibling-1.md"),
        (f"# {SIBLING_TITLE}\n\n", "sibling-1.md"),
    ])
    def test_a_malformed_sibling_files_nothing(self, board, seams, tmp_path, bad, words):
        before = _parked_7164(board)
        out = _write(tmp_path / "out", revised=NARROWED, answers=base._answers(7),
                     siblings=[bad])
        assert _apply(board, "DRE-7164", out) != 0
        assert seams.calls == []
        assert _kinds(board, before) == ["comment"]
        newest = board.newest("DRE-7164")
        assert newest.startswith("🩹 triage-repair: unfinished")
        assert words in newest
        assert board.description("DRE-7164") == base.DESCRIPTION

    def test_siblings_with_no_narrowed_card_file_nothing(self, board, seams, tmp_path):
        before = _parked_7164(board)
        out = _write(tmp_path / "out", answers=base._answers(7),
                     siblings=[_sibling(SIBLING_TITLE, SIBLING_BODY)])
        assert _apply(board, "DRE-7164", out) != 0
        assert seams.calls == []
        assert _kinds(board, before) == ["comment"]
        assert "revised-card.md" in board.newest("DRE-7164")

    def test_siblings_on_an_escalate_card_are_not_its_outcome(self, board, seams, tmp_path):
        base._parked(board, "DRE-2", base.DECISION_NOTE, labels=LABELS)
        assert base._claim(board, "DRE-2") == 0
        out = _write(tmp_path / "out", revised=NARROWED, answers=base._answers(2),
                     siblings=[_sibling(SIBLING_TITLE, SIBLING_BODY)])
        assert _apply(board, "DRE-2", out, action="escalate") != 0
        assert seams.calls == []
        assert board.lane("DRE-2") == "Triage"
        assert board.newest("DRE-2").startswith("🩹 triage-repair: unfinished")


# --------------------------------------------------------------------------- #
# (b) an epic                                                                  #
# --------------------------------------------------------------------------- #


class TestReshape:
    def _parked(self, board, *extra):
        base._parked(board, "DRE-2994", NOTE_2994, labels=LABELS)
        for body in extra:
            board.say("DRE-2994", body)
        assert base._claim(board, "DRE-2994") == 0
        return len(board.events)

    def test_dre_2994_shape_is_stamped_epic_by_the_planner_and_moved_to_planning(
            self, board, seams, tmp_path, capsys):
        before = self._parked(board)
        out = _write(tmp_path / "out", answers=base._answers(2), reshape="epic\n")
        assert _apply(board, "DRE-2994", out) == 0
        bodies = board.bodies("DRE-2994")
        assert planning_shape.shape_on(bodies) == "epic"
        assert planning_shape.stamped_by(bodies) == ("planner", MODEL)
        assert ("comment_bodies", "DRE-2994", True) in board.reads
        assert seams.calls == []
        # The stamp and its mark, then the lift, the move, the receipt.
        kinds = _kinds(board, before)
        assert kinds == ["comment", "add-label", "remove-label", "comment", "state", "comment"]
        assert board.events[before][2].startswith("🧩 planning-shape: **epic**")
        assert board.lane("DRE-2994") == "Planning"
        assert "needs-human" not in board.labels("DRE-2994")
        assert board.description("DRE-2994") == base.DESCRIPTION
        receipt = board.newest("DRE-2994")
        assert receipt.splitlines()[0].startswith("🩹 triage-repair: split")
        assert "epic" in receipt
        assert f"1. {EPIC_FINDING}" in receipt
        assert "triage-repair: DRE-2994 → split" in capsys.readouterr().out

    def test_a_narrowed_description_beside_reshape_is_set_before_the_stamp(
            self, board, seams, tmp_path):
        before = self._parked(board)
        out = _write(tmp_path / "out", revised=base.REVISED, answers=base._answers(2),
                     reshape="epic")
        assert _apply(board, "DRE-2994", out) == 0
        assert _kinds(board, before)[:3] == ["description", "decisions-block", "comment"]
        assert board.description("DRE-2994") == base.REVISED
        assert planning_shape.shape_on(board.bodies("DRE-2994")) == "epic"

    def test_reshape_with_no_model_writes_nothing(self, board, seams, tmp_path):
        before = self._parked(board)
        out = _write(tmp_path / "out", answers=base._answers(2), reshape="epic")
        assert _apply(board, "DRE-2994", out, model=None) != 0
        assert _kinds(board, before) == ["comment"]
        newest = board.newest("DRE-2994")
        assert newest.startswith("🩹 triage-repair: unfinished")
        assert "--model" in newest
        assert planning_shape.shape_on(board.bodies("DRE-2994")) is None

    def test_reshape_naming_another_shape_writes_nothing(self, board, seams, tmp_path):
        before = self._parked(board)
        out = _write(tmp_path / "out", answers=base._answers(2), reshape="one-off")
        assert _apply(board, "DRE-2994", out) != 0
        assert _kinds(board, before) == ["comment"]
        assert "reshape.txt" in board.newest("DRE-2994")

    def test_siblings_and_reshape_together_write_nothing(self, board, seams, tmp_path):
        before = self._parked(board)
        out = _write(tmp_path / "out", revised=NARROWED, answers=base._answers(2),
                     reshape="epic", siblings=[_sibling(SIBLING_TITLE, SIBLING_BODY)])
        assert _apply(board, "DRE-2994", out) != 0
        assert seams.calls == []
        assert _kinds(board, before) == ["comment"]
        newest = board.newest("DRE-2994")
        assert newest.startswith("🩹 triage-repair: unfinished")
        assert "reshape.txt" in newest and "sibling-" in newest

    def test_a_card_the_classifier_already_stamped_stays_held_in_triage(
            self, board, seams, tmp_path):
        """One shape per card (`planning_shape.stamp_refusal`): a planner stamp
        already standing refuses the second, and the refusal is a failed write
        like any other — the card is not moved with its shape unsettled."""
        stamp = planning_shape.shape_comment("one-off", "label-routed",
                                             by="planner", model="label")
        before = self._parked(board, stamp)
        out = _write(tmp_path / "out", answers=base._answers(2), reshape="epic")
        assert _apply(board, "DRE-2994", out) != 0
        assert board.lane("DRE-2994") == "Triage"
        assert "needs-human" in board.labels("DRE-2994")
        assert "state" not in _kinds(board, before)
        newest = board.newest("DRE-2994")
        assert newest.startswith("🩹 triage-repair: unfinished")
        assert "already stamped one-off" in newest


# --------------------------------------------------------------------------- #
# the lane a sibling lands in is the seam's                                    #
# --------------------------------------------------------------------------- #


class TestNoLaneName:
    @pytest.mark.parametrize("path", [ROOT / "scripts" / "triage_repair.py",
                                      Path(__file__)])
    def test_no_landing_lane_is_named(self, path):
        text = path.read_text(encoding="utf-8")
        for word in LANE_WORDS:
            assert word not in text, f"{path.name} names {word}"


# --------------------------------------------------------------------------- #
# the workflow                                                                 #
# --------------------------------------------------------------------------- #


class TestTheWiring:
    def _apply_step(self) -> dict:
        return next(s for s in wiring._job("repair")["steps"]
                    if wiring._runs(s, "triage_repair.py apply"))

    def test_the_apply_step_reads_the_model_steps_output(self):
        env = self._apply_step().get("env") or {}
        assert env.get("MODEL") == "${{ steps.model.outputs.model }}"

    @pytest.mark.parametrize("action", ["repair", "escalate"])
    def test_the_selected_model_reaches_apply(self, tmp_path, action):
        row = wiring._row("DRE-7164", action)
        temp = tmp_path / "temp"
        argv = wiring._apply_argv(row, temp, tmp_path, model=MODEL)
        out = wiring._planner_dir(row, temp)
        assert argv == ["apply", "DRE-7164", "--action", action, "--dir", str(out),
                        "--model", MODEL]

    def test_replan_runs_no_model_and_passes_none(self, tmp_path):
        argv = wiring._apply_argv(wiring._row("DRE-3", "replan"), tmp_path / "temp", tmp_path)
        assert argv == ["apply", "DRE-3", "--action", "replan"]

    def test_the_prompt_names_both_shapes_and_their_files(self):
        prompt = wiring._agent()["with"]["prompt"]
        for name in ("sibling-<n>.md", "reshape.txt"):
            assert f"{wiring.OUT_DIR}/{name}" in prompt, name
        for words in ("more than one job", "# <title>", "**Files:**",
                      "## Acceptance criteria", "**Blocked by:**", "exactly `epic`",
                      "revised-card.md", "repair-answers.md", "question.md"):
            assert words in prompt, words

    def test_a_dry_run_files_nothing(self):
        """`apply` lives in the `repair` job, which a dry run never starts."""
        assert self._apply_step() in wiring._job("repair")["steps"]
        gate = wiring._job("repair")["if"]
        assert not wiring._evaluate(gate, {"needs.select.outputs.dry_run": "true",
                                           "needs.select.outputs.cards": "[{}]"})

    def test_the_workflow_route_stamps_the_model_it_selected(self, board, seams, tmp_path):
        base._parked(board, "DRE-2994", NOTE_2994, labels=LABELS)
        _, rows = base._select(board, tmp_path)
        assert [r["action"] for r in rows] == ["repair"]
        assert base._claim(board, "DRE-2994") == 0
        row, temp = rows[0], tmp_path / "temp"
        out = wiring._planner_dir(row, temp)
        _write(out, answers=base._answers(2), reshape="epic")
        argv = wiring._apply_argv(row, temp, tmp_path, model=MODEL)
        with board.patched():
            assert triage_repair.main(argv) == 0
        assert planning_shape.stamped_by(board.bodies("DRE-2994")) == ("planner", MODEL)
        assert board.lane("DRE-2994") == "Planning"
