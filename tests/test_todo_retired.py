"""Todo retires (DRE-7129, epic DRE-7072) — the second step of the protocol
the lane contract defines for any retirement (DRE-2726), in the shape Backlog
just took (DRE-6753, `test_backlog_retired.py`).

The code stopped writing the lane first: the flip (DRE-7120) sent a promoted
FLEET card from Queued straight to In Progress, and that move is the dispatch,
so a lane between them only made the board show two waiting lanes. This card
is the second step and the declaration that drives it: the contract marks the
lane `retiring`, replaced by In Progress, and `reconcile.drain_retiring_lanes`
moves every card still there into In Progress on every repo's sweep — except
an epic, which the sweep's epic carry takes out by its own rule, so a drag can
never approve one. The board step is the third (the operator card after this
one): delete the state in Linear, then delete the entry.

`epic_todo_gate.TODO` stops being a literal: it is the retiring lane whose
`replaced_by` is the dispatch lane, read off the contract, and every caller
reads that name. These tests pin the entry, the drain and that name, and then
the sweep: the old name survives in no string constant under `scripts/`
(docstrings included), in no non-comment line of a shell script or workflow,
and in `config/` only inside the lane's own retiring entry. `docs/`,
`architecture/`, `tests/fixtures/` and comments are history and are not
swept. The needle is assembled from two fragments so this file never matches
itself.
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
    entry_span,
    python_constants,
    tracked_files,
    uncommented_lines,
)
import step_shell  # noqa: E402

#: The retiring lane name, assembled so this sweep never matches its own source.
OLD_LANE = "To" + "do"
NEW_LANE = "In Progress"
EPIC = "DRE-7072"
CONTRACT = "config/lane-contract.json"


# --------------------------------------------------------------------------- #
# the sweep                                                                    #
# --------------------------------------------------------------------------- #


def config_offenders(text: str, needle: str) -> list:
    """Line numbers in a config file naming `needle` outside its own retiring
    entry. No shim marker exempts a line here: the lane retires with no alias,
    because In Progress is a live lane and the old name is not another
    spelling of it."""
    first, last = entry_span(text, needle)
    return [
        i for i, line in enumerate(text.splitlines(), 1)
        if needle in line and not first <= i <= last
    ]


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


# --------------------------------------------------------------------------- #
# the entry                                                                    #
# --------------------------------------------------------------------------- #


def raw_entry():
    return next(e for e in lane_contract.load()["lanes"] if e["name"] == OLD_LANE)


class TestTheContractMarksItRetiring:
    def test_the_retiring_set_holds_it_beside_the_queues_old_name(self):
        retiring = [entry["name"] for entry in lane_contract.lanes("retiring")]
        assert OLD_LANE in retiring
        assert "Back" + "log" in retiring

    def test_it_is_replaced_by_in_progress_and_retired_by_the_epic(self):
        entry = lane_contract.lane(OLD_LANE, status="retiring")
        assert entry["replaced_by"] == NEW_LANE == lane_contract.DISPATCH_LANE
        assert entry["retired_by"] == EPIC

    def test_it_carries_a_reason_and_a_board_action_and_nothing_else(self):
        entry = raw_entry()
        assert set(entry) == {
            "name", "status", "retired_by", "replaced_by", "reason", "board_action",
        }
        reason = entry["reason"]
        assert "Queued" in reason and NEW_LANE in reason
        assert "is the dispatch" in reason
        assert "two waiting lanes" in reason
        assert "reconcile.drain_retiring_lanes" in reason
        assert "epic" in reason

    def test_the_board_action_deletes_the_state_and_then_the_entry(self):
        step = raw_entry()["board_action"]
        assert "Delete" in step
        assert "team workflow settings" in step
        for sibling in ("Queued", "Intake", "Triage", "Hand-work"):
            assert sibling in step
        assert "unstarted" in step
        assert "board.retiring_lane_is_empty" in step
        assert "board.retired_entry_is_deleted" in step

    def test_the_live_lanes_no_longer_include_it(self):
        assert OLD_LANE not in lane_contract.lane_names()
        assert OLD_LANE not in lane_contract.flow_lanes()
        assert NEW_LANE in lane_contract.lane_names()

    def test_it_is_still_a_start_lane_until_the_entry_goes(self):
        assert lane_contract.START_LANES == (NEW_LANE, OLD_LANE)
        assert lane_contract.start_lanes() == (NEW_LANE, OLD_LANE)

    def test_it_takes_no_alias(self):
        # In Progress is a live lane; the old name is not another spelling of
        # it, and a reader resolving it there would read a waiting card as one
        # whose build started.
        assert OLD_LANE not in lane_contract.aliases()

    def test_the_drain_reads_it(self):
        draining = {
            e["name"]: e["replaced_by"]
            for e in lane_contract.lanes("retiring") if e.get("replaced_by")
        }
        assert draining[OLD_LANE] == NEW_LANE
        assert OLD_LANE in reconcile.RETIRING_LANES
        assert OLD_LANE in reconcile.BOARD_READ_LANES


class TestTheHarness:
    def board(self, **counts):
        board = {name: 0 for name in lane_contract.lane_names()}
        board.update({name: 0 for name in lane_contract.lane_names("retiring")})
        board.update(counts)
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

    def test_a_card_left_there_is_named(self):
        inputs = lane_contract._Inputs(
            contract=lane_contract.load(), board=self.board(**{OLD_LANE: 3}),
            console=None, vocabulary=None, root=ROOT,
        )
        problems = lane_contract.ASSERTIONS["board.retiring_lane_is_empty"](inputs)
        assert len(problems) == 1 and repr(OLD_LANE) in problems[0]

    def test_once_linear_drops_the_state_the_entry_must_go(self):
        board = self.board()
        board.pop(OLD_LANE)
        report = lane_contract.check(board=board, vocabulary=())
        failures = [
            f for f in report.failures()
            if f.clause_id == "board.retired_entry_is_deleted"
        ]
        assert failures and any(repr(OLD_LANE) in str(f) for f in failures), failures


class TestTheDocument:
    def test_the_rendered_document_carries_the_retiring_section(self):
        with open(lane_contract.DOC_PATH, encoding="utf-8") as fh:
            text = fh.read()
        assert text == lane_contract.render_markdown()
        assert f"### {OLD_LANE} — retired by {EPIC}" in text
        live = text.split("\n## The lanes\n", 1)[1].split("\n## ", 1)[0]
        assert f"\n### {OLD_LANE}\n" not in live


# --------------------------------------------------------------------------- #
# the gate's name is read off the contract                                     #
# --------------------------------------------------------------------------- #


class TestTheGateReadsTheName:
    def test_the_name_is_the_retiring_lane_the_dispatch_lane_replaces(self):
        assert epic_todo_gate.TODO == OLD_LANE
        assert epic_todo_gate.retiring_start_lane() == OLD_LANE

    def test_it_follows_the_contract_not_a_literal(self):
        doc = json.loads(json.dumps(lane_contract.load()))
        for entry in doc["lanes"]:
            if entry["name"] == OLD_LANE:
                entry["name"] = "Waiting Room"
        assert epic_todo_gate.retiring_start_lane(doc) == "Waiting Room"

    def test_a_retiring_lane_replaced_by_another_lane_is_not_it(self):
        doc = json.loads(json.dumps(lane_contract.load()))
        doc["lanes"] = [e for e in doc["lanes"] if e["name"] != OLD_LANE]
        # The queue's old name retires into Queued, never the dispatch lane.
        assert epic_todo_gate.retiring_start_lane(doc) is None

    @pytest.mark.parametrize("module", [reconcile, validate_card, linear_ops,
                                        ready_lane_writers])
    def test_every_caller_reads_the_gates_name(self, module):
        text = _read(module.__file__)
        assert "epic_todo_gate.TODO" in text, module.__name__
        assert python_constants(text, OLD_LANE) == [], module.__name__


# --------------------------------------------------------------------------- #
# the drain                                                                    #
# --------------------------------------------------------------------------- #


def _in_lane(ident, *, title="bureau-pipeline: a small fix", children=(),
             state=OLD_LANE):
    return {
        "id": f"uuid-{ident}",
        "identifier": ident,
        "title": title,
        "description": "**Repo:** agent-bureau\nwork",
        "updatedAt": "2026-10-18T00:00:00Z",
        "state": {"name": state},
        "labels": {"nodes": [{"name": "repo:agent-bureau"}]},
        "children": {"nodes": [{"id": c} for c in children]},
        "comments": {"nodes": []},
    }


def _drain(cards):
    def active_cards(states=()):
        return [c for c in cards if c["state"]["name"] in states]

    with patch.object(reconcile, "active_cards", side_effect=active_cards), \
            patch.object(reconcile.linear_ops, "cmd_advance") as advance, \
            patch.object(reconcile.linear_ops, "cmd_comment") as comment:
        reconcile.drain_retiring_lanes()
    return advance, comment


class TestTheDrain:
    def test_a_card_in_the_lane_moves_to_in_progress_with_the_comment(self):
        advance, comment = _drain([_in_lane("DRE-9001")])
        advance.assert_called_once_with("DRE-9001", NEW_LANE, OLD_LANE)
        ident, note = comment.call_args.args
        assert ident == "DRE-9001"
        assert note.startswith(
            f"🧹 Reconcile: the '{OLD_LANE}' lane is being retired ({EPIC})"
        )
        assert f"moved to '{NEW_LANE}'" in note

    def test_an_epic_in_the_lane_is_left_for_the_carry(self):
        advance, comment = _drain([
            _in_lane("DRE-9002", children=("child",)),
            _in_lane("DRE-9003", title="[EPIC] bureau-pipeline: a plan"),
        ])
        advance.assert_not_called()
        comment.assert_not_called()

    def test_an_epic_in_the_queues_old_name_still_drains_to_queued(self):
        # Queued approves nothing, so the exception is the dispatch lane's alone.
        old_queue = "Back" + "log"
        advance, _ = _drain([_in_lane("DRE-9004", children=("c",), state=old_queue)])
        advance.assert_called_once_with("DRE-9004", "Queued", old_queue)

    def test_an_empty_lane_moves_nothing(self):
        advance, comment = _drain([])
        advance.assert_not_called()
        comment.assert_not_called()

    def test_the_drain_reads_both_retiring_lanes(self):
        with patch.object(reconcile, "active_cards",
                          MagicMock(return_value=[])) as cards:
            reconcile.drain_retiring_lanes()
        assert set(cards.call_args.args[0]) == {"Back" + "log", OLD_LANE}


# --------------------------------------------------------------------------- #
# the sweep over the tree                                                      #
# --------------------------------------------------------------------------- #


class TestTheSweep:
    """The old name survives nowhere the pipeline reads it as a lane."""

    def test_no_script_workflow_or_config_line_names_the_old_lane(self):
        offenders = sweep()
        assert offenders == [], (
            f"the retiring lane name survives at: {offenders}. The start lane "
            f"is {NEW_LANE!r}; a script reads the old name as "
            "epic_todo_gate.TODO, and a config line may keep it only inside "
            "its own retiring entry."
        )

    def test_the_only_config_survivor_is_the_entry(self):
        text = _read(os.path.join(ROOT, CONTRACT))
        first, last = entry_span(text, OLD_LANE)
        assert first > 0, "the retiring entry was not found"
        others = [
            f for f in tracked_files(CONFIG)
            if f != CONTRACT and OLD_LANE in (_read(os.path.join(ROOT, f)) or "")
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

    def test_a_config_line_outside_the_entry_is_caught(self, tmp_path):
        hits = self.plant(tmp_path, "config/probe.json", json.dumps(
            {"anchor": f"fixed by the {OLD_LANE} gate"}, indent=2))
        assert hits == ["config/probe.json:2"]

    def test_a_shim_marker_exempts_nothing_here(self, tmp_path):
        line = (f'{{"from": "{OLD_LANE}", "to": "{NEW_LANE}", '
                f'"shim": "lane-rename-shim {EPIC}"}}\n')
        assert self.plant(tmp_path, "config/probe.json", line) == ["config/probe.json:1"]

    def test_the_retiring_entry_is_exempt_and_a_live_one_is_not(self, tmp_path):
        retiring = {"lanes": [{"name": OLD_LANE, "status": "retiring",
                               "reason": f"{OLD_LANE} goes"}]}
        assert self.plant(tmp_path, "config/probe.json",
                          json.dumps(retiring, indent=2)) == []
        live = {"lanes": [{"name": OLD_LANE, "status": "live",
                           "reason": f"{OLD_LANE} stays"}]}
        hits = self.plant(tmp_path, "config/probe.json", json.dumps(live, indent=2))
        assert len(hits) == 2, hits

    def test_the_entry_span_is_read_off_the_shipped_file(self):
        text = _read(os.path.join(ROOT, CONTRACT))
        first, last = entry_span(text, OLD_LANE)
        block = "\n".join(text.splitlines()[first - 1:last])
        assert json.loads(block.rstrip().rstrip(",")) == raw_entry()
