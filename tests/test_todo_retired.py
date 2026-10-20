"""Todo is retired (DRE-7660, epic DRE-7072) — the last step of the protocol
the lane contract defines for any retirement (DRE-2726): the board caught up,
and the entry goes.

The code stopped writing the lane first (the flip, DRE-7120), the contract
then marked it `retiring` and the sweep drained it (DRE-7129), and the
operator deleted the state in Linear on 2026-10-19 with no card in it
(DRE-7131). Its `board_action` asked for the last step by name: delete the
entry, which `board.retired_entry_is_deleted` requires once the state is
gone. These tests pin that it is gone — from the retiring set, from the start
lanes, from the sweep's reads and from the rendered document — and that the
clause which asked for it is quiet now.

`epic_todo_gate.TODO` is read off the contract, so it is None, and the
epic-in-Todo rule (DRE-5316, DRE-5319, DRE-5347) is KEPT as a no-op: the
gate refuses nothing, and the sweep's two readers —
`reconcile.drain_retiring_lanes` and `reconcile.carry_epics_out_of_todo` —
read no such lane and write nothing, without error.

Then the sweep: the old name survives in no string constant under `scripts/`
(docstrings included), in no non-comment line of a shell script or workflow,
and nowhere in `config/` — the retiring entry that was its one exemption is
deleted. `docs/`, `architecture/`, `tests/fixtures/` and comments are history
and are not swept. The needle is assembled from two fragments so this file
never matches itself.
"""
import json
import os
import subprocess  # nosec B404 — fixed-arg calls to git and this repo's CLI only
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")

import epic_todo_gate  # noqa: E402
import lane_contract  # noqa: E402
import linear_ops  # noqa: E402
import ready_lane_writers  # noqa: E402
import reconcile  # noqa: E402
import validate_card  # noqa: E402
# The sweep's readers are the Backlog retirement's, imported rather than
# restated: one sweep, two needles.
from test_backlog_retired import (  # noqa: E402
    CONFIG,
    ROOT,
    SCRIPTS,
    WORKFLOWS,
    _read,
    python_constants,
    tracked_files,
    uncommented_lines,
)
import step_shell  # noqa: E402

#: The retired lane name, assembled so this sweep never matches its own source.
OLD_LANE = "To" + "do"
NEW_LANE = "In Progress"
OLD_QUEUE = "Back" + "log"
EPIC = "DRE-7072"
CONTRACT = "config/lane-contract.json"


# --------------------------------------------------------------------------- #
# the sweep                                                                    #
# --------------------------------------------------------------------------- #


def config_offenders(text: str, needle: str) -> list:
    """Line numbers in a config file naming `needle`. Every one is an offender:
    the retiring entry that was the one exemption is deleted, and no shim
    marker exempts a line, because the lane retired with no alias."""
    return [i for i, line in enumerate(text.splitlines(), 1) if needle in line]


def sweep(root: str = ROOT, needle: str = OLD_LANE, files=None) -> list:
    """`path:line` for every place the old name survives that this card forbids.

    `files` is the repo-relative list to read; by default every tracked file
    under the three swept trees.
    """
    if files is None:
        files = tracked_files(SCRIPTS) + tracked_files(WORKFLOWS) + tracked_files(CONFIG)
    hits = []
    for rel in files:
        path = os.path.join(root, rel)
        text = _read(path)
        if text is None or needle not in text:
            continue
        if rel.startswith(SCRIPTS):
            lines = (python_constants(text, needle) if rel.endswith(".py")
                     else uncommented_lines(text, needle))
        elif rel.startswith(WORKFLOWS):
            if rel.endswith((".yml", ".yaml")):
                text = step_shell.workflow_source(path, root=root)
            lines = uncommented_lines(text, needle)
        elif rel.startswith(CONFIG):
            lines = config_offenders(text, needle)
        else:
            lines = []
        hits += [f"{rel}:{n}" for n in lines]
    return hits


def _with_the_entry(doc: dict) -> dict:
    """`doc` with the deleted entry put back, the way it shipped until DRE-7660
    — so a test can show what the shipped contract no longer does."""
    doc = json.loads(json.dumps(doc))
    doc["lanes"].append({
        "name": OLD_LANE, "status": "retiring", "retired_by": EPIC,
        "replaced_by": NEW_LANE, "reason": "r", "board_action": "b",
    })
    return doc


# --------------------------------------------------------------------------- #
# the entry is gone                                                            #
# --------------------------------------------------------------------------- #


class TestTheEntryIsGone:
    def test_the_retiring_set_holds_only_the_queues_old_name(self):
        assert lane_contract.lane_names("retiring") == (OLD_QUEUE,)

    def test_no_entry_of_any_status_carries_the_name(self):
        assert [e for e in lane_contract.load()["lanes"] if e["name"] == OLD_LANE] == []
        for status in ("live", "retiring", "arriving"):
            with pytest.raises(lane_contract.UnknownLane):
                lane_contract.lane(OLD_LANE, status=status)

    def test_the_queues_old_name_is_unchanged(self):
        entry = lane_contract.lane(OLD_QUEUE, status="retiring")
        assert entry["retired_by"] == "DRE-4125"
        assert entry["replaced_by"] == "Queued"
        assert "This entry STAYS" in entry["board_action"]

    def test_it_is_not_a_start_lane(self):
        assert lane_contract.START_LANES == (NEW_LANE,)
        assert lane_contract.start_lanes() == (NEW_LANE,)
        assert OLD_LANE not in lane_contract.DISPATCH_FROM

    def test_it_takes_no_alias(self):
        assert OLD_LANE not in lane_contract.aliases()

    def test_the_sweep_reads_it_nowhere(self):
        assert OLD_LANE not in reconcile.RETIRING_LANES
        assert OLD_LANE not in reconcile.BOARD_READ_LANES
        assert reconcile.RETIRING_LANES == (OLD_QUEUE,)


class TestTheHarness:
    def board(self):
        """The board as Linear has it now: every lane the contract carries,
        and no state for the old start lane."""
        board = {name: 0 for name in lane_contract.lane_names()}
        board.update({name: 0 for name in lane_contract.lane_names("retiring")})
        return board

    def test_the_offline_check_passes(self):
        env = {k: v for k, v in os.environ.items() if k != "LINEAR_API_KEY"}
        done = subprocess.run(  # nosec B603 — fixed argv, no shell
            [sys.executable, os.path.join(ROOT, "scripts", "lane_contract.py"), "check"],
            capture_output=True, text=True, cwd=ROOT, env=env,
        )
        assert done.returncode in (0, 1), done.stdout + done.stderr
        assert "Traceback" not in done.stderr, done.stderr
        offenders = [
            line for line in done.stdout.splitlines()
            if line.lstrip().startswith("[FAIL")
            and "the board could not be read" not in line
        ]
        assert offenders == [], offenders

    def test_the_vocabulary_rule_passes_on_this_checkout(self):
        report = lane_contract.check(vocabulary=lane_contract.pipeline_vocabulary())
        failed = {f.clause_id for f in report.failures()}
        assert "pipeline.vocabulary_is_contract_lanes" not in failed

    def test_with_the_state_gone_the_deleted_entry_clause_is_quiet(self):
        board = self.board()
        assert OLD_LANE not in board
        problems = lane_contract.ASSERTIONS["board.retired_entry_is_deleted"](
            lane_contract._Inputs(contract=lane_contract.load(), board=board,
                                  console=None, vocabulary=None, root=ROOT))
        assert problems == []
        report = lane_contract.check(board=board, vocabulary=())
        failed = {f.clause_id for f in report.failures()}
        assert "board.retired_entry_is_deleted" not in failed
        assert "board.retiring_lane_is_empty" not in failed

    def test_the_clause_named_the_entry_while_it_stood(self):
        # The guard on the guard: the same board, read against the contract as
        # it shipped before this card, is the failure this card answered.
        report = lane_contract.check(
            contract=_with_the_entry(lane_contract.load()), board=self.board(),
            vocabulary=())
        failures = [f for f in report.failures()
                    if f.clause_id == "board.retired_entry_is_deleted"]
        assert failures and any(repr(OLD_LANE) in str(f) for f in failures), failures


class TestTheDocument:
    def test_the_rendered_document_names_it_nowhere(self):
        with open(lane_contract.DOC_PATH, encoding="utf-8") as fh:
            text = fh.read()
        assert text == lane_contract.render_markdown()
        assert f"### {OLD_LANE}" not in text
        assert f"| Start lanes — a started card sits in one | {NEW_LANE} |" in text


# --------------------------------------------------------------------------- #
# the gate reads None, and every reader does nothing on it                     #
# --------------------------------------------------------------------------- #


class TestTheGateIsANoOp:
    def test_the_name_is_none(self):
        assert epic_todo_gate.retiring_start_lane() is None
        assert epic_todo_gate.TODO is None
        assert validate_card._TODO is None

    def test_it_still_follows_the_contract(self):
        # The reader is kept, not a literal None: a contract naming a retiring
        # lane the dispatch lane replaces is read again.
        doc = _with_the_entry(lane_contract.load())
        assert epic_todo_gate.retiring_start_lane(doc) == OLD_LANE

    @pytest.mark.parametrize("lane", [OLD_LANE, NEW_LANE, "Planning"])
    def test_no_write_of_an_epic_is_refused(self, lane):
        assert epic_todo_gate.refusal(
            "DRE-9001", lane, "[EPIC] bureau-pipeline: a plan", True, (), None,
        ) is None

    def test_the_write_seam_refuses_no_epic(self):
        with patch.object(linear_ops, "get_issue",
                          return_value={"state": {"name": "Planning"},
                                        "title": "[EPIC] a plan",
                                        "children": {"nodes": [{"id": "c"}]}}), \
                patch.object(linear_ops, "comment_bodies", return_value=[]), \
                patch.object(epic_todo_gate, "post_refusal") as posted, \
                patch.object(epic_todo_gate, "refusal",
                             wraps=epic_todo_gate.refusal) as refused:
            assert epic_todo_gate.decision(linear_ops, "DRE-9001", OLD_LANE) is None
        refused.assert_called_once()
        posted.assert_not_called()

    @pytest.mark.parametrize("module", [reconcile, validate_card, linear_ops,
                                        ready_lane_writers])
    def test_every_caller_reads_the_gates_name(self, module):
        text = _read(module.__file__)
        assert "epic_todo_gate.TODO" in text, module.__name__
        assert python_constants(text, OLD_LANE) == [], module.__name__


def _in_lane(ident, *, title="bureau-pipeline: a small fix", children=(),
             state=OLD_LANE):
    return {
        "id": f"uuid-{ident}",
        "identifier": ident,
        "title": title,
        "description": "**Repo:** agent-bureau\nwork",
        "updatedAt": "2026-10-19T00:00:00Z",
        "state": {"name": state},
        "labels": {"nodes": [{"name": "repo:agent-bureau"}]},
        "children": {"nodes": [{"id": c} for c in children]},
        "comments": {"nodes": []},
    }


class TestTheSweepsReaders:
    def test_the_drain_reads_only_the_queues_old_name(self):
        with patch.object(reconcile, "active_cards",
                          MagicMock(return_value=[])) as cards:
            reconcile.drain_retiring_lanes()
        assert set(cards.call_args.args[0]) == {OLD_QUEUE}

    def test_a_card_in_the_old_lane_is_not_drained(self):
        # A board that still answered with the old lane moves nothing: the
        # contract no longer says where it goes.
        cards = [_in_lane("DRE-9001"), _in_lane("DRE-9002", children=("c",))]
        with patch.object(reconcile, "active_cards", return_value=cards), \
                patch.object(reconcile.linear_ops, "cmd_advance") as advance, \
                patch.object(reconcile.linear_ops, "cmd_comment") as comment:
            reconcile.drain_retiring_lanes()
        advance.assert_not_called()
        comment.assert_not_called()

    def test_the_epic_carry_reads_nothing_and_writes_nothing(self, capsys):
        with patch.object(reconcile, "active_cards") as cards, \
                patch.object(reconcile.linear_ops, "cmd_state") as state, \
                patch.object(epic_todo_gate, "post_refusal") as posted:
            reconcile.carry_epics_out_of_todo()
        cards.assert_not_called()
        state.assert_not_called()
        posted.assert_not_called()
        assert "names no retiring start lane" in capsys.readouterr().out


class TestTheOldReceiptIsStillRead:
    def test_the_receipt_the_old_lane_carried_is_matched(self):
        # Posted before DRE-7108 named no lane; cards still carry it.
        old = f"🧹 Reconcile: card sat in {OLD_LANE} with no run — re-dispatched."
        assert any(note in old for note in reconcile._TODO_REDISPATCH_NOTES)

    def test_no_note_names_a_lane_the_contract_lost(self):
        for note in reconcile._TODO_REDISPATCH_NOTES:
            assert "None" not in note and OLD_LANE not in note, note


# --------------------------------------------------------------------------- #
# the sweep over the tree                                                      #
# --------------------------------------------------------------------------- #


class TestTheSweep:
    """The old name survives nowhere the pipeline reads it as a lane."""

    def test_no_script_workflow_or_config_line_names_the_old_lane(self):
        offenders = sweep()
        assert offenders == [], (
            f"the retired lane name survives at: {offenders}. The start lane "
            f"is {NEW_LANE!r}; a script reads the old name as "
            "epic_todo_gate.TODO, which is None, and no config line may keep it."
        )

    def test_no_config_file_names_it(self):
        others = [
            f for f in tracked_files(CONFIG)
            if OLD_LANE in (_read(os.path.join(ROOT, f)) or "")
        ]
        assert others == [], others


class TestTheSweepCatchesWhatItMust:
    """The guard on the guard: a sweep that finds nothing anywhere would pass
    over a tree full of the old name."""

    def plant(self, tmp_path, rel, text):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return sweep(root=str(tmp_path), files=[rel])

    def test_a_string_constant_in_a_script_is_caught(self, tmp_path):
        hits = self.plant(tmp_path, "scripts/probe.py",
                          f'LANES = ("Queued", "{OLD_LANE}")\n')
        assert hits == ["scripts/probe.py:1"]

    def test_a_docstring_in_a_script_is_caught(self, tmp_path):
        hits = self.plant(tmp_path, "scripts/probe.py",
                          f'def f():\n    """Moves a card to {OLD_LANE}."""\n')
        assert hits == ["scripts/probe.py:2"]

    def test_an_f_string_fragment_is_caught(self, tmp_path):
        hits = self.plant(tmp_path, "scripts/probe.py",
                          f'X = f"found in {OLD_LANE} as {{who}}"\n')
        assert hits == ["scripts/probe.py:1"]

    def test_a_comment_in_a_script_is_history(self, tmp_path):
        hits = self.plant(tmp_path, "scripts/probe.py",
                          f"# DRE-3621 sat in {OLD_LANE} seventeen days\nX = 1\n")
        assert hits == []

    def test_a_shell_line_is_caught_and_its_comment_is_not(self, tmp_path):
        hits = self.plant(tmp_path, "scripts/probe.sh",
                          f'# parks in {OLD_LANE}\nlinear state "$CARD" "{OLD_LANE}"\n')
        assert hits == ["scripts/probe.sh:2"]

    def test_a_workflow_line_is_caught_and_its_comment_is_not(self, tmp_path):
        hits = self.plant(tmp_path, ".github/workflows/probe.yml", (
            "jobs:\n  a:\n    steps:\n      - run: |\n"
            f"          # the card sat in {OLD_LANE}\n"
            f'          linear_ops.py advance "$CARD" "In Progress" "{OLD_LANE}"\n'
        ))
        assert hits == [".github/workflows/probe.yml:6"]

    def test_a_config_line_is_caught(self, tmp_path):
        hits = self.plant(tmp_path, "config/probe.json", json.dumps(
            {"anchor": f"fixed by the {OLD_LANE} gate"}, indent=2))
        assert hits == ["config/probe.json:2"]

    def test_a_retiring_entry_is_no_longer_exempt(self, tmp_path):
        retiring = {"lanes": [{"name": OLD_LANE, "status": "retiring",
                               "reason": f"{OLD_LANE} goes"}]}
        hits = self.plant(tmp_path, "config/probe.json", json.dumps(retiring, indent=2))
        assert len(hits) == 2, hits

    def test_a_shim_marker_exempts_nothing_here(self, tmp_path):
        line = (f'{{"from": "{OLD_LANE}", "to": "{NEW_LANE}", '
                f'"shim": "lane-rename-shim {EPIC}"}}\n')
        assert self.plant(tmp_path, "config/probe.json", line) == ["config/probe.json:1"]
