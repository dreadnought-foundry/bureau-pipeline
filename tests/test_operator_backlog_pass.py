"""RED-first: the one-time pass over today's manual operator holds (DRE-6429).

Operator cards filed before DRE-6428 sit in Backlog wearing `needs-human` +
`no-code` with no hold stamp, so `hold.reason_of` reads them `manual` and the
sweep of DRE-6427 leaves them alone by design. `hand_work_migration.py
operator-backlog` is the pass that converts them: dry run by default,
`--apply` to write, safe to run again.

WHAT IS UNDER TEST, over a fake board whose writes change what the next read
sees — nothing here reaches Linear. The real `hold.apply`, `hold.lift` and
`routing_verdict.stamp_card` run against it, so the lines on the card are the
ones those seams compose:
  * selection — `needs-human` + `no-code` in Backlog, not an epic, not a proof,
    and either no live stamp (`manual`) or an `operator-step` one;
  * the four lists — unblocked, waiting, due, not touched — each card with its
    repo, title and reason;
  * the writes `--apply` makes, in order, and the ones it never makes;
  * running it twice writes nothing the second time;
  * its `hold.apply` site has its row in `config/holds.json`, and its lane
    write is one the lane contract permits.

Run: cd bureau-pipeline && python3 -m pytest tests/test_operator_backlog_pass.py -v
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import hand_work_migration as hwm  # noqa: E402
import hold  # noqa: E402
import linear_ops  # noqa: E402
import ready_lane_writers  # noqa: E402
import routing_verdict  # noqa: E402

NEEDS_HUMAN = hold.HOLD_LABEL
NO_CODE = linear_ops.NO_CODE_LABEL
OPERATOR_STEP = routing_verdict.OPERATOR_STEP_LABEL
WRITER = "hand_work_migration.py"

#: The two lines the card fixes, exactly.
STAMP = ("🔒 hold: reason=operator-step at=none lifts=blockers-terminal "
         "by=hand_work_migration.py")
LIFT = ("🔓 hold lifted: reason=operator-step because=blockers-terminal "
        "by=hand_work_migration.py")
#: The stamp the create seam (DRE-6428) posts on a card it files held.
SEAM_STAMP = hold.stamp_line(hold.OPERATOR_STEP_REASON, None, "linear_ops.py")


def _verdict(name):
    return routing_verdict.verdict_comment(name, "a fixture reason")


class Board:
    """A fake Linear: cards whose labels, comments and lane every write
    changes, so a second pass reads what the first one left. `log` is every
    write in the order it was made."""

    def __init__(self, cards, *, relation_pages=None, unreadable=False):
        self.cards = {c["identifier"]: c for c in cards}
        self.relation_pages = dict(relation_pages or {})
        self.unreadable = unreadable
        self.moved: dict[str, str] = {}
        self.log: list[tuple] = []
        self._clock = 0

    # -- reads -----------------------------------------------------------
    def _node(self, card):
        comments = [{"body": b, "createdAt": f"2026-10-0{1 + i % 8}T00:00:{i:02d}.000Z",
                     "user": {"id": "u-fleet", "name": "fleet"}}
                    for i, b in enumerate(card["comments"])]
        relations = [{"type": "blocks",
                      "issue": {"identifier": ident, "state": {"name": state}}}
                     for ident, state in card["blockers"].items()]
        more = card["identifier"] in self.relation_pages
        return {
            "id": f"uuid-{card['identifier']}",
            "identifier": card["identifier"],
            "title": card["title"],
            "state": {"name": card["lane"]},
            "labels": {"nodes": [{"name": n} for n in card["labels"]]},
            "parent": card["parent"],
            "children": {"nodes": [{"id": f"kid-{i}"} for i in range(card["children"])]},
            "inverseRelations": {"pageInfo": {"hasNextPage": more,
                                              "endCursor": "c1" if more else None},
                                 "nodes": relations},
            "comments": {"pageInfo": {"hasNextPage": False, "endCursor": None},
                         "nodes": list(reversed(comments))},
        }

    def gql_paged(self, query, variables=None, **kw):
        if self.unreadable:
            raise linear_ops.LinearError("linear error: 502")
        assert "$after" in query
        label = (variables or {}).get("label")
        assert label == NEEDS_HUMAN, label
        return [self._node(c) for c in self.cards.values()
                if c["lane"] == "Backlog" and NEEDS_HUMAN in c["labels"]]

    def gql(self, query, variables=None):
        if self.unreadable:
            raise linear_ops.LinearError("linear error: 502")
        ident = variables["id"]
        card = self.cards[ident]
        if "inverseRelations" in query:
            rest = self.relation_pages.get(ident, {})
            return {"issue": {"inverseRelations": {
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "nodes": [{"type": "blocks",
                           "issue": {"identifier": i, "state": {"name": s}}}
                          for i, s in rest.items()]}}}
        if "comments" in query:
            return {"issue": self._node(card)}
        return {"issue": {"state": {"name": self.moved.get(ident, card["lane"])}}}

    def comment_bodies(self, identifier, **kw):
        return list(self.cards[identifier]["comments"])

    def get_issue(self, identifier, *a, **kw):
        return {"title": self.cards[identifier]["title"]}

    # -- writes ----------------------------------------------------------
    def add_label(self, identifier, label):
        labels = self.cards[identifier]["labels"]
        if label not in labels:
            labels.append(label)
        self.log.append(("add", identifier, label))

    def remove_label(self, identifier, label):
        labels = self.cards[identifier]["labels"]
        if label in labels:
            labels.remove(label)
        self.log.append(("remove", identifier, label))

    def cmd_comment(self, identifier, body, *flags):
        self.cards[identifier]["comments"].append(body)
        self.log.append(("comment", identifier, body.splitlines()[0]))

    def cmd_state(self, identifier, state, *flags, expect=None, **kw):
        card = self.cards[identifier]
        lane = self.moved.get(identifier, card["lane"])
        if expect is not None and lane not in expect:
            return False
        card["lane"] = state
        self.moved.pop(identifier, None)
        self.log.append(("state", identifier, state))
        return True

    def written(self, identifier):
        return [w for w in self.log if w[1] == identifier]

    def patches(self):
        stack = ExitStack()
        for name in ("gql_paged", "gql", "comment_bodies", "get_issue", "add_label",
                     "remove_label", "cmd_comment", "cmd_state"):
            stack.enter_context(mock.patch.object(linear_ops, name, getattr(self, name)))
        return stack


def _card(identifier, *, title="rotate the deploy key", lane="Backlog",
          labels=(NEEDS_HUMAN, NO_CODE, "repo:atlas"), comments=(), blockers=None,
          parent=None, children=0):
    return {"identifier": identifier, "title": title, "lane": lane,
            "labels": list(labels), "comments": list(comments),
            "blockers": dict(blockers or {}), "parent": parent, "children": children}


def _epic(lane):
    return {"identifier": "DRE-900", "state": {"name": lane}}


def _run(board, *argv):
    with board.patches():
        return hwm.main(["operator-backlog", *argv])


def _section(out, heading):
    """The lines under one of the four headings, up to the next heading."""
    lines = out.splitlines()
    starts = [i for i, line in enumerate(lines) if line.startswith(heading + " (")]
    assert len(starts) == 1, (heading, out)
    body = []
    for line in lines[starts[0] + 1:]:
        if not line.startswith(" "):
            break
        body.append(line)
    return "\n".join(body)


def _unblocked(identifier="DRE-1", **kw):
    """The first criterion's card: no stamp, an OPERATOR verdict, one Done blocker."""
    kw.setdefault("comments", [_verdict("OPERATOR")])
    kw.setdefault("blockers", {"DRE-50": "Done"})
    return _card(identifier, **kw)


# --------------------------------------------------------------------------
# 1: an unblocked manual hold
# --------------------------------------------------------------------------
class TestUnblocked:
    def test_the_dry_run_lists_it_unblocked_and_writes_nothing(self, capsys):
        board = Board([_unblocked(title="raise the Actions budget")])
        assert _run(board) == 0
        out = capsys.readouterr().out
        listed = _section(out, "unblocked")
        assert "DRE-1" in listed and "atlas" in listed
        assert "raise the Actions budget" in listed
        assert board.log == []
        assert "dry run — nothing was written. Re-run with --apply." in out

    def test_apply_marks_stamps_lifts_and_moves_it_in_that_order(self):
        board = Board([_unblocked()])
        assert _run(board, "--apply") == 0
        log = board.written("DRE-1")
        step = log.index(("add", "DRE-1", OPERATOR_STEP))
        stamp = log.index(("comment", "DRE-1", STAMP))
        lift = log.index(("comment", "DRE-1", LIFT))
        move = log.index(("state", "DRE-1", "Hand-work"))
        assert step < stamp < lift < move
        card = board.cards["DRE-1"]
        assert NEEDS_HUMAN not in card["labels"]
        assert OPERATOR_STEP in card["labels"] and card["lane"] == "Hand-work"
        assert hold.reason_of(card["labels"], card["comments"]) is None

    def test_the_move_is_conditional_on_backlog(self):
        seen = []
        board = Board([_unblocked()])
        real = board.cmd_state

        def spy(identifier, state, *flags, **kw):
            seen.append(kw.get("expect"))
            return real(identifier, state, *flags, **kw)

        board.cmd_state = spy
        _run(board, "--apply")
        assert seen == [("Backlog",)]

    def test_a_card_that_moved_since_the_read_is_refused_untouched(self, capsys):
        board = Board([_unblocked()])
        board.moved["DRE-1"] = "Todo"
        _run(board, "--apply")
        assert board.log == []
        assert "refused DRE-1" in capsys.readouterr().out

    def test_one_with_no_live_verdict_is_stamped_operator_before_the_move(self):
        board = Board([_unblocked(comments=[])])
        assert _run(board, "--apply") == 0
        log = board.written("DRE-1")
        verdicts = [i for i, w in enumerate(log) if w[0] == "comment"
                    and w[2].startswith(f"{routing_verdict.VERDICT_MARK} "
                                        f"{routing_verdict.VERDICT_TAG}: **OPERATOR**")]
        assert len(verdicts) == 1
        assert log.index(("comment", "DRE-1", STAMP)) < verdicts[0]
        assert verdicts[0] < log.index(("comment", "DRE-1", LIFT))
        assert verdicts[0] < log.index(("state", "DRE-1", "Hand-work"))
        assert routing_verdict.verdict_on(board.cards["DRE-1"]["comments"]) == "OPERATOR"

    def test_one_with_an_operator_verdict_is_not_restamped(self):
        board = Board([_unblocked()])
        _run(board, "--apply")
        verdicts = routing_verdict.verdicts_on(board.cards["DRE-1"]["comments"])
        assert verdicts == ("OPERATOR",)
        assert sum(b.startswith(routing_verdict.VERDICT_MARK)
                   for b in board.cards["DRE-1"]["comments"]) == 1

    def test_a_workbench_verdict_is_carried_too(self, capsys):
        board = Board([_unblocked(comments=[_verdict("WORKBENCH")])])
        _run(board)
        assert "DRE-1" in _section(capsys.readouterr().out, "unblocked")


# --------------------------------------------------------------------------
# 2: waiting — open blockers, or a parent not In Progress
# --------------------------------------------------------------------------
class TestWaiting:
    def test_an_open_blocker_is_named(self, capsys):
        board = Board([_unblocked(blockers={"DRE-50": "Done", "DRE-51": "In Progress"})])
        _run(board)
        listed = _section(capsys.readouterr().out, "waiting")
        assert "DRE-1" in listed and "DRE-51" in listed
        assert "DRE-50" not in listed

    def test_apply_marks_and_stamps_it_and_nothing_else(self):
        board = Board([_unblocked(blockers={"DRE-51": "Todo"})])
        assert _run(board, "--apply") == 0
        log = board.written("DRE-1")
        assert ("add", "DRE-1", OPERATOR_STEP) in log
        assert [w for w in log if w[0] == "comment"] == [("comment", "DRE-1", STAMP)]
        assert not [w for w in log if w[0] in ("state", "remove")]
        card = board.cards["DRE-1"]
        assert card["lane"] == "Backlog" and NEEDS_HUMAN in card["labels"]
        assert hold.reason_of(card["labels"], card["comments"]) == hold.OPERATOR_STEP_REASON

    def test_a_parent_not_in_progress_is_waiting_on_its_lane(self, capsys):
        board = Board([_unblocked(parent=_epic("Backlog"))])
        _run(board)
        listed = _section(capsys.readouterr().out, "waiting")
        assert "DRE-1" in listed and "DRE-900" in listed and "Backlog" in listed

    def test_a_parent_in_progress_does_not_hold_it(self, capsys):
        board = Board([_unblocked(parent=_epic("In Progress"))])
        _run(board)
        assert "DRE-1" in _section(capsys.readouterr().out, "unblocked")

    def test_relations_are_read_to_the_end(self, capsys):
        board = Board([_unblocked()], relation_pages={"DRE-1": {"DRE-77": "In Review"}})
        _run(board)
        listed = _section(capsys.readouterr().out, "waiting")
        assert "DRE-1" in listed and "DRE-77" in listed


# --------------------------------------------------------------------------
# 3: due — and the second pass
# --------------------------------------------------------------------------
def _stamped(identifier="DRE-2", **kw):
    kw.setdefault("labels", (NEEDS_HUMAN, NO_CODE, OPERATOR_STEP, "repo:portico"))
    kw.setdefault("comments", [_verdict("OPERATOR"), SEAM_STAMP])
    kw.setdefault("blockers", {"DRE-60": "Done", "DRE-61": "Canceled"})
    kw.setdefault("parent", _epic("In Progress"))
    return _card(identifier, **kw)


class TestDue:
    def test_a_stamped_card_with_terminal_blockers_is_due(self, capsys):
        board = Board([_stamped()])
        _run(board)
        listed = _section(capsys.readouterr().out, "due")
        assert "DRE-2" in listed and "portico" in listed
        assert board.log == []

    def test_apply_lifts_and_moves_it_with_no_second_stamp(self):
        board = Board([_stamped()])
        assert _run(board, "--apply") == 0
        log = board.written("DRE-2")
        assert [w for w in log if w[0] == "comment"] == [("comment", "DRE-2", LIFT)]
        assert log.index(("comment", "DRE-2", LIFT)) < log.index(("state", "DRE-2", "Hand-work"))
        assert board.cards["DRE-2"]["comments"].count(SEAM_STAMP) == 1
        assert NEEDS_HUMAN not in board.cards["DRE-2"]["labels"]

    def test_with_an_open_blocker_it_is_not_touched_and_not_written(self, capsys):
        board = Board([_stamped(blockers={"DRE-60": "Done", "DRE-62": "In Progress"})])
        assert _run(board, "--apply") == 0
        listed = _section(capsys.readouterr().out, "not touched")
        assert "DRE-2" in listed and "DRE-62" in listed
        assert board.log == []

    def test_a_second_apply_over_the_first_board_writes_nothing(self, capsys):
        board = Board([_unblocked(), _unblocked("DRE-3", blockers={"DRE-51": "Todo"}),
                       _stamped()])
        assert _run(board, "--apply") == 0
        assert board.log
        capsys.readouterr()
        board.log.clear()
        assert _run(board, "--apply") == 0
        assert board.log == []
        listed = _section(capsys.readouterr().out, "not touched")
        assert "DRE-3" in listed and "DRE-51" in listed


# --------------------------------------------------------------------------
# 4: never listed, and listed but never written
# --------------------------------------------------------------------------
class TestNeverSelected:
    BOARD = staticmethod(lambda: Board([
        _unblocked("DRE-10", comments=[_verdict("OPERATOR"),
                                       hold.stamp_line("turn-cap-park", None, "dead_run.py")]),
        _unblocked("DRE-11", title="[EPIC] the operator steps", children=2),
        _unblocked("DRE-12", title="PROOF: the operator steps ran"),
        _unblocked("DRE-13", labels=(NEEDS_HUMAN, "repo:atlas")),
        _unblocked("DRE-14", comments=[_verdict("FLEET")]),
    ]))

    def test_another_reason_an_epic_a_proof_and_no_no_code_are_never_listed(self, capsys):
        board = self.BOARD()
        _run(board)
        out = capsys.readouterr().out
        for ident in ("DRE-10", "DRE-11", "DRE-12", "DRE-13"):
            assert f"{ident} " not in out, ident

    def test_a_fleet_card_is_not_touched_and_never_written(self, capsys):
        board = self.BOARD()
        assert _run(board, "--apply") == 0
        listed = _section(capsys.readouterr().out, "not touched")
        assert "DRE-14" in listed and "FLEET" in listed
        assert board.log == []


# --------------------------------------------------------------------------
# 5: the CLI, the refusals, and the registry
# --------------------------------------------------------------------------
class TestTheCommand:
    def test_help_works(self):
        done = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "hand_work_migration.py"),
             "operator-backlog", "--help"], capture_output=True, text=True, check=False)
        assert done.returncode == 0, done.stderr
        assert "--apply" in done.stdout

    def test_the_dry_run_prints_all_four_headings(self, capsys):
        _run(Board([]))
        out = capsys.readouterr().out
        for heading in ("unblocked", "waiting", "due", "not touched"):
            assert f"\n{heading} (" in "\n" + out, heading

    def test_unreadable_linear_exits_non_zero_before_any_write(self, capsys):
        board = Board([_unblocked()], unreadable=True)
        assert _run(board, "--apply") != 0
        assert board.log == []
        assert "nothing was written" in capsys.readouterr().err

    def test_it_reads_the_contract_names_off_the_modules(self):
        source = (ROOT / "scripts" / "hand_work_migration.py").read_text()
        assert "hold.OPERATOR_STEP_REASON" in source
        assert "hold.BLOCKERS_TERMINAL" in source
        assert '"operator-step"' not in source and '"blockers-terminal"' not in source


class TestRegistered:
    def _rows(self):
        doc = json.loads((ROOT / "config" / "holds.json").read_text())
        return [r for r in doc["sites"] if r["file"] == "scripts/hand_work_migration.py"]

    def test_its_hold_apply_site_has_one_row(self):
        rows = self._rows()
        assert len(rows) == 1
        row = rows[0]
        assert [e["reason"] for e in row["reasons"]] == ["operator-step"]
        assert row["reasons"][0]["lifts"] == "blockers-terminal"
        assert row["reasons"][0]["tried_first"].startswith("none — ")
        assert row["readers"] == ["fix-dispatch", "medic", "limit-recovery"]
        sites = [s for s in hold.discover() if s.file == "scripts/hand_work_migration.py"]
        assert len(sites) == 1 and hold.row_matches(row, sites[0])

    def test_the_hold_check_passes(self):
        assert hold.problems() == []

    def test_its_hand_work_write_is_permitted(self):
        mine = [w for w in ready_lane_writers.writes() if w.writer == WRITER]
        assert "Hand-work" in {w.lane for w in mine}
        assert ready_lane_writers.writer_problems() == []
