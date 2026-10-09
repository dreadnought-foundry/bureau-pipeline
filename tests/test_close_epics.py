"""TDD for event-driven epic close (--close-epics) — the cron-drift fix.

PROBLEM: close_finished_epics has existed since 2026-06-11, but it runs ONLY
on the full cron sweep, which GitHub delivers 78-100 minutes apart in practice
(scheduled workflows are best-effort). The moment an epic actually becomes
all-Done is a precise EVENT — a merge flipping its LAST child to Done
(linear-sync.yml) — yet nothing closed it there. Live symptom (2026-06-15):
DRE-1496 sat "In Progress" with 9/9 children Done, reading "still working"
while the work had shipped (DRE-1552).

FIX UNDER TEST: reconcile.py main(close_only=True) — runs ONLY the epic-close
pass (this repo's active agent:planner epics → close_finished_epics),
skipping the PR backstops, promotion gate, and stale-card nudge loop, so
linear-sync.yml can invoke it the instant a merge lands. Epic-close is pure
Linear (LINEAR_API_KEY only). The cron sweep stays as the backstop.

Run: cd bureau-pipeline && python3 -m pytest tests/ -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")

import reconcile  # noqa: E402


def _phase_mocks():
    """Patch every sweep phase with recorders; close path returns cleanly."""
    return {
        "unstick_conflicts": MagicMock(),
        "retrigger_dead_heads": MagicMock(),
        "check_dependabot_capacity": MagicMock(),
        "fix_approved_but_red": MagicMock(),
        "active_cards": MagicMock(return_value=[]),
        "close_finished_epics": MagicMock(),
        "promote_ready": MagicMock(return_value=0),
    }


def test_close_only_runs_epic_close_and_nothing_else():
    """--close-epics must run close_finished_epics and skip every other phase.

    The merge hook carries only LINEAR_API_KEY — epic close is pure Linear.
    Backstops/promotion/nudges need gh or the WIP gate and stay out of this
    path (promotion has its own --promote-only hook on the same merge).
    """
    mocks = _phase_mocks()
    with patch.multiple(reconcile, **mocks):
        reconcile.main(close_only=True)

    mocks["close_finished_epics"].assert_called_once()
    mocks["promote_ready"].assert_not_called()
    mocks["unstick_conflicts"].assert_not_called()
    mocks["retrigger_dead_heads"].assert_not_called()
    mocks["fix_approved_but_red"].assert_not_called()


def test_close_only_passes_just_this_repos_active_epics():
    """Only THIS repo's epics are handed to the closer.

    Epic-ness is the SHAPE, not `agent:planner` (DRE-3044) — the containers
    below carry children, the way a real epic does, and the one-off wears the
    planner label the relay puts on every card out of Planning without that
    making it an epic.
    """
    mocks = _phase_mocks()
    mocks["active_cards"] = MagicMock(
        return_value=[
            {  # this repo, epic — included
                "identifier": "DRE-1496",
                "title": "[EPIC] the front door",
                "description": "**Repo:** agent-bureau\nepic",
                "children": {"nodes": [{"id": "kid-1"}]},
                "labels": {"nodes": [{"name": "agent:planner"}]},
            },
            {  # this repo, NOT an epic — excluded
                "identifier": "DRE-1508",
                "title": "trim the trailing slash",
                "description": "**Repo:** agent-bureau\nwork",
                "children": {"nodes": []},
                "labels": {"nodes": [{"name": "agent:planner"}]},
            },
            {  # other repo epic — excluded
                "identifier": "DRE-200",
                "title": "[EPIC] someone else's decomposition",
                "description": "**Repo:** atlas\nepic",
                "children": {"nodes": [{"id": "kid-2"}]},
                "labels": {"nodes": [{"name": "agent:planner"}]},
            },
        ]
    )
    with patch.multiple(reconcile, **mocks):
        reconcile.main(close_only=True)
    mocks["close_finished_epics"].assert_called_once_with({"DRE-1496"})


def test_full_sweep_still_closes_epics():
    """Default main() keeps epic-close in the full sweep (unchanged backstop)."""
    mocks = _phase_mocks()
    with patch.multiple(reconcile, **mocks):
        reconcile.main()
    mocks["close_finished_epics"].assert_called_once()


def _kids(*states):
    return {"issue": {"children": {"nodes": [{"state": {"name": s}} for s in states]}}}


def test_all_children_done_closes_epic():
    """Fixture: every child Done → epic moves to Done with a logged comment."""
    with patch.object(reconcile.linear_ops, "gql", return_value=_kids("Done", "Done", "Done")), \
        patch.object(reconcile.linear_ops, "cmd_state") as state, \
        patch.object(reconcile.linear_ops, "cmd_comment") as comment:
        reconcile.close_finished_epics({"DRE-1496"})
    state.assert_called_once_with("DRE-1496", "Done")
    comment.assert_called_once()


def test_one_child_not_done_leaves_epic_open():
    """Fixture: any non-terminal child → epic untouched (no state write)."""
    with patch.object(reconcile.linear_ops, "gql", return_value=_kids("Done", "In Progress", "Done")), \
        patch.object(reconcile.linear_ops, "cmd_state") as state, \
        patch.object(reconcile.linear_ops, "cmd_comment") as comment:
        reconcile.close_finished_epics({"DRE-1496"})
    state.assert_not_called()
    comment.assert_not_called()


def test_childless_epic_left_open():
    """An epic with zero children is never inferred closed (nothing to read)."""
    with patch.object(reconcile.linear_ops, "gql", return_value=_kids()), \
        patch.object(reconcile.linear_ops, "cmd_state") as state:
        reconcile.close_finished_epics({"DRE-9999"})
    state.assert_not_called()


def test_all_canceled_no_done_leaves_epic_open():
    """All children terminal but NONE Done → not a completion; stays open."""
    with patch.object(reconcile.linear_ops, "gql", return_value=_kids("Canceled", "Canceled")), \
        patch.object(reconcile.linear_ops, "cmd_state") as state:
        reconcile.close_finished_epics({"DRE-9999"})
    state.assert_not_called()


# ---------------------------------------------------------------------------
# DRE-3148: one Linear timeout on one epic must not kill the sweep. Every other
# step already shrugs off a failed Linear call; this was the one bare call.
# Live symptom (2026-09-06, portico run 33970765609): two sweeps in a row dead
# on `TimeoutError: The read operation timed out` inside close_finished_epics.
# ---------------------------------------------------------------------------
def _gql_timing_out_on(bad_epic: str, *, then=("Done", "Done")):
    """A fake `gql` keyed on the epic id: `bad_epic` times out the way a socket
    read does, every other epic answers with all-Done children."""

    def fake(query, variables=None):
        if variables and variables.get("id") == bad_epic:
            raise TimeoutError("The read operation timed out")
        return _kids(*then)

    return fake


def test_one_epic_timing_out_does_not_stop_the_others():
    """DRE-100's read times out; DRE-200 is still closed and the call returns."""
    with patch.object(reconcile.linear_ops, "gql", side_effect=_gql_timing_out_on("DRE-100")), \
        patch.object(reconcile.linear_ops, "cmd_state") as state, \
        patch.object(reconcile.linear_ops, "cmd_comment") as comment, \
        patch.object(reconcile, "advance_unblocked_epics") as advance_chain:
        reconcile.close_finished_epics({"DRE-100", "DRE-200"})  # sorted: DRE-100 first
    state.assert_called_once_with("DRE-200", "Done")
    comment.assert_called_once()
    advance_chain.assert_called_once_with("DRE-200")


def test_timed_out_epic_is_named_in_the_log_with_the_error(capsys):
    """The skip is loud: stderr names the epic, the error, and that it was
    skipped this sweep — the next sweep recomputes the input, so nothing is
    lost, but a reader of the run log must be able to see it happened."""
    with patch.object(reconcile.linear_ops, "gql", side_effect=_gql_timing_out_on("DRE-100")), \
        patch.object(reconcile.linear_ops, "cmd_state"), \
        patch.object(reconcile.linear_ops, "cmd_comment"), \
        patch.object(reconcile, "advance_unblocked_epics"):
        reconcile.close_finished_epics({"DRE-100", "DRE-200"})
    err = capsys.readouterr().err
    assert "epic-close: could not close DRE-100" in err
    assert "The read operation timed out" in err
    assert "skipped this sweep" in err
    assert "DRE-200" not in err  # the epic that closed is not reported as a failure


def test_close_only_sweep_survives_a_linear_timeout():
    """The failing shape from the notice cannot recur: a timeout inside the
    epic-close pass leaves `main(close_only=True)` returning normally (exit 0),
    not dying with the traceback that killed two sweeps in a row."""
    mocks = _phase_mocks()
    del mocks["close_finished_epics"]  # the real one, under a timing-out Linear
    mocks["active_cards"] = MagicMock(
        return_value=[
            {
                "identifier": "DRE-100",
                "title": "[EPIC] the one Linear hangs on",
                "description": "**Repo:** agent-bureau\nepic",
                "children": {"nodes": [{"id": "kid-1"}]},
                "labels": {"nodes": [{"name": "agent:planner"}]},
            },
        ]
    )
    with patch.multiple(reconcile, **mocks), \
        patch.object(reconcile.linear_ops, "gql", side_effect=_gql_timing_out_on("DRE-100")), \
        patch.object(reconcile.linear_ops, "cmd_state") as state:
        reconcile.main(close_only=True)  # must not raise
    state.assert_not_called()


# ---------------------------------------------------------------------------
# DRE-4700: a parent whose children are themselves epics. Waves are retired;
# what is left of a split plan is a roll-up — child epics under a parent that
# never builds anything and closes when its children are Done. The sweep
# already carries both halves of that rule generically, one level at a time
# (`_close_epic_if_finished` and the epic skip in `promote_ready`); nothing
# tested them on a parent whose children are epics, so these do.
# ---------------------------------------------------------------------------
ROLL_UP = "DRE-4800"


def _child_epics(*states):
    """The roll-up's record as the epic read returns it: each child is an
    `[EPIC]`-titled card of its own, in the lane given."""
    return {"issue": {"children": {"nodes": [
        {
            "identifier": f"DRE-{4801 + n}",
            "title": f"[EPIC] bureau-pipeline: slice {n + 1}",
            "state": {"name": s},
        }
        for n, s in enumerate(states)
    ]}}}


def test_roll_up_whose_child_epics_are_all_done_closes():
    """(a) Three child epics all Done → the parent goes to Done with the
    `🏁 Epic complete` receipt, and the chain behind it is advanced."""
    with patch.object(reconcile.linear_ops, "gql",
                      return_value=_child_epics("Done", "Done", "Done")), \
        patch.object(reconcile.linear_ops, "cmd_state") as state, \
        patch.object(reconcile.linear_ops, "cmd_comment") as comment, \
        patch.object(reconcile, "advance_unblocked_epics") as advance_chain:
        reconcile.close_finished_epics({ROLL_UP})
    state.assert_called_once_with(ROLL_UP, "Done")
    comment.assert_called_once()
    epic, body = comment.call_args.args
    assert epic == ROLL_UP
    assert body.startswith("🏁 Epic complete: all 3 children are closed (3 done).")
    advance_chain.assert_called_once_with(ROLL_UP)


def test_roll_up_with_an_open_child_epic_stays_where_it_is():
    """(b) One child epic still In Progress → the parent is not moved and
    nothing is posted on it."""
    with patch.object(reconcile.linear_ops, "gql",
                      return_value=_child_epics("Done", "In Progress", "Done")), \
        patch.object(reconcile.linear_ops, "cmd_state") as state, \
        patch.object(reconcile.linear_ops, "cmd_comment") as comment, \
        patch.object(reconcile, "advance_unblocked_epics") as advance_chain:
        reconcile.close_finished_epics({ROLL_UP})
    state.assert_not_called()
    comment.assert_not_called()
    advance_chain.assert_not_called()


def test_roll_up_in_backlog_is_never_promoted(monkeypatch, capsys):
    """(c) The same parent as a Backlog promotion candidate, one child epic
    still open → skipped with the epics-are-promoted-by-humans line, and
    nothing is written for it: no move (the move to Todo IS the dispatch),
    no receipt, no refusal notice."""
    monkeypatch.setattr(reconcile, "REPO_SLUG", "agent-bureau")
    parent = {
        "identifier": ROLL_UP,
        "title": "[EPIC] bureau-pipeline: the roll-up",
        "description": "**Repo:** agent-bureau\nchild epics under this one",
        "labels": {"nodes": [{"name": "agent:planner"}]},
        "children": {"nodes": [{"id": "DRE-4801"}]},
        "comments": {"nodes": []},
        "inverseRelations": {"nodes": []},
    }
    with patch.object(reconcile.linear_ops, "gql",
                      return_value=_child_epics("Done", "In Progress", "Done")), \
        patch.object(reconcile.linear_ops, "cmd_advance") as advance, \
        patch.object(reconcile.linear_ops, "cmd_state") as state, \
        patch.object(reconcile.linear_ops, "cmd_comment") as comment:
        promoted = reconcile.promote_ready(0, candidates=[parent])
    assert promoted == 0
    advance.assert_not_called()
    state.assert_not_called()
    comment.assert_not_called()
    out = capsys.readouterr().out
    assert (
        f"promotion: {ROLL_UP} is an epic — epics are promoted by humans, "
        "never by the sweep; skipping"
    ) in out


# ---------------------------------------------------------------------------
# DRE-6410: an epic whose children are all closed closes from Backlog too.
# DRE-4819 sat in Backlog with all six children Done and nothing closed it: the
# full sweep only handed the closer the epics in Todo, In Progress and In
# Review, and the merge path refused its parent's lane. The lanes an epic
# closes from are declared once, `EPIC_CLOSE_LANES`, and read at both sites.
# ---------------------------------------------------------------------------
import ast  # noqa: E402
import contextlib  # noqa: E402
import inspect  # noqa: E402

import pytest  # noqa: E402

import merge_sweep_gate  # noqa: E402
import test_sweep_request_budget as budget  # noqa: E402
import validate_card  # noqa: E402

FINISHED = ("Done", "Canceled", "Done")
EPIC = "DRE-4819"


@pytest.fixture
def board_pins(monkeypatch):
    """The pins `test_sweep_request_budget` applies to every one of its
    sweeps, here only for the tests that run one."""
    monkeypatch.setattr(reconcile, "REPO_SLUG", "agent-bureau")
    monkeypatch.setattr(
        validate_card, "VALID_SLUGS", {"agent-bureau", "atlas", "bureau-pipeline"})
    monkeypatch.setattr(
        reconcile, "live_rail_slugs",
        lambda: frozenset({"agent-bureau", "atlas", "bureau-pipeline"}), raising=False)
    monkeypatch.delenv("MERGED_CARD", raising=False)
    for ledger in (reconcile._write_failures, reconcile._read_failures,
                   reconcile._stale_defects):
        ledger.clear()
    yield
    for ledger in (reconcile._write_failures, reconcile._read_failures,
                   reconcile._stale_defects):
        ledger.clear()


def _board(lane: str | None, kids=FINISHED) -> budget.EpicBoard:
    """The budget's fixed board, plus EPIC in `lane` (None: no epic at all)."""
    active, backlog = budget._fixed_board()
    if lane is None:
        return budget.EpicBoard(active=active, backlog=backlog)
    epic = budget._epic(EPIC, state=lane)
    if lane == reconcile.BACKLOG_LANE:
        backlog = backlog + [epic]
    else:
        active = active + [epic]
    return budget.EpicBoard(active=active, backlog=backlog, kids={EPIC: kids})


def _sweep(fake):
    """One full sweep over `fake`; what it moved, said, and advanced."""
    advance = MagicMock()
    try:
        budget._run_closing_sweep(fake, advance=advance)
    except SystemExit:  # a red sweep still ran every phase
        pass
    return fake.state, fake.comment, advance


def _closed(state) -> bool:
    return (EPIC, "Done") in [c.args for c in state.call_args_list]


def test_a_finished_epic_in_backlog_is_closed_by_one_full_sweep(board_pins):
    """The epic is in Backlog and in no swept lane — the shape that stayed
    open — and one full sweep closes it with the same receipt and advances
    the chain behind it."""
    fake = _board(reconcile.BACKLOG_LANE)
    assert all(c["identifier"] != EPIC for c in fake.active)
    state, comment, advance = _sweep(fake)
    assert _closed(state), state.call_args_list
    bodies = [c.args[1] for c in comment.call_args_list if c.args[0] == EPIC]
    assert any(
        b.startswith("🏁 Epic complete: all 3 children are closed (2 done).")
        for b in bodies
    ), bodies
    advance.assert_any_call(EPIC)


def test_a_backlog_epic_with_an_open_child_stays_open(board_pins):
    state, comment, advance = _sweep(_board(reconcile.BACKLOG_LANE,
                                            kids=("Done", "In Progress")))
    assert not _closed(state)
    advance.assert_not_called()


def test_a_backlog_epic_whose_children_are_all_canceled_stays_open(board_pins):
    """Every child Canceled and none Done is not a completion — in Backlog as
    in every other lane."""
    state, _, advance = _sweep(_board(reconcile.BACKLOG_LANE,
                                      kids=("Canceled", "Canceled")))
    assert not _closed(state)
    advance.assert_not_called()


@pytest.mark.parametrize("lane", ["Todo", "In Progress", "In Review"])
def test_a_finished_epic_in_a_sweep_lane_still_closes(board_pins, lane):
    """Guard the guard: the same fixture, in the lanes the sweep has always
    closed from, closes — so the refusals below are the lane's and not a
    harness that never sees a write."""
    state, _, advance = _sweep(_board(lane))
    assert _closed(state), state.call_args_list
    advance.assert_any_call(EPIC)


@pytest.mark.parametrize(
    "lane", ["Intake", "Planning", "Green Light", "Triage", "Hand-work"])
def test_a_finished_epic_outside_the_close_lanes_is_left_alone(board_pins, lane):
    """Intake, Planning and Green Light hold an epic whose plan is in motion;
    Triage and Green Light are not read by the sweep at all, and Hand-work is
    a person's. None is closed — and closing from Backlog buys no read of
    Green Light or Triage."""
    state, _, advance = _sweep(_board(lane))
    assert not _closed(state)
    advance.assert_not_called()

    without = _board(None)
    _sweep(without)
    with_epic = _board(lane)
    _sweep(with_epic)
    lanes_read = lambda f: [tuple(v["states"]) for v in f.board_variables]  # noqa: E731
    assert lanes_read(with_epic) == lanes_read(without)
    assert not any("Triage" in lanes for lanes in lanes_read(with_epic))
    assert sum("Green Light" in lanes for lanes in lanes_read(with_epic)) == 1, (
        "the planner line's Green Light read is the only one")


class MergeBoard(budget.EpicBoard):
    """An `EpicBoard` that also answers the merge gate's read of the merged
    card (`merge_sweep_gate.QUERY`) — its parent, in `parent_state`."""

    def __init__(self, merged, parent_state, kids=FINISHED):
        super().__init__(kids={EPIC: kids})
        self.merged, self.parent_state, self.parent_kids = merged, parent_state, kids

    def gql(self, query, variables=None):
        if query != merge_sweep_gate.QUERY:
            return super().gql(query, variables)
        self.queries.append(query)
        return {"issue": {
            "identifier": self.merged, "state": {"name": "Done"},
            "relations": {"pageInfo": {"hasNextPage": False}, "nodes": []},
            "parent": {
                "identifier": EPIC, "state": {"name": self.parent_state},
                "children": {"pageInfo": {"hasNextPage": False}, "nodes": [
                    {"identifier": f"{EPIC}-kid-{n}", "state": {"name": s}}
                    for n, s in enumerate(self.parent_kids)
                ]},
            },
        }}


def _merge_close(monkeypatch, parent_state):
    """`main(close_only=True)` the way linear-sync runs it after a merge."""
    monkeypatch.setenv("MERGED_CARD", "DRE-4826")
    fake = MergeBoard("DRE-4826", parent_state)
    advance = MagicMock()
    with budget._linear(fake), \
            patch.object(reconcile, "start_queued_epics"), \
            patch.object(reconcile, "advance_unblocked_epics", advance), \
            patch.object(reconcile.linear_ops, "cmd_state") as state, \
            patch.object(reconcile.linear_ops, "cmd_comment"):
        reconcile.main(close_only=True)
    return state, advance


def test_the_merge_path_closes_a_finished_parent_in_backlog(board_pins, monkeypatch):
    """DRE-4819's last child closed on a merge: this is the site that would
    have closed it."""
    state, advance = _merge_close(monkeypatch, reconcile.BACKLOG_LANE)
    assert _closed(state), state.call_args_list
    advance.assert_called_once_with(EPIC)


def test_the_merge_path_closes_nothing_whose_parent_is_in_planning(board_pins, monkeypatch):
    """Planning is a lane the cron never closes from, so a merge does not
    either — the two sites read one constant."""
    state, advance = _merge_close(monkeypatch, "Planning")
    assert not _closed(state)
    advance.assert_not_called()


def test_epic_close_lanes_are_the_sweep_lanes_and_backlog():
    assert reconcile.EPIC_CLOSE_LANES == reconcile.SWEEP_STATES + (reconcile.BACKLOG_LANE,)
    assert set(reconcile.EPIC_CLOSE_LANES) == {"Todo", "In Progress", "In Review", "Backlog"}


def test_both_sites_read_epic_close_lanes(board_pins, monkeypatch):
    """Take Backlog out of the one constant and NEITHER site closes from it —
    so both read it, and neither keeps a lane list of its own."""
    monkeypatch.setattr(reconcile, "EPIC_CLOSE_LANES", reconcile.SWEEP_STATES)
    state, _, _ = _sweep(_board(reconcile.BACKLOG_LANE))
    assert not _closed(state), "the full sweep closed from a lane the constant left out"
    state, _ = _merge_close(monkeypatch, reconcile.BACKLOG_LANE)
    assert not _closed(state), "the merge path closed from a lane the constant left out"


def test_the_merge_path_no_longer_reads_swept_lanes_for_the_close():
    """The text of the `--close-only` branch: it names EPIC_CLOSE_LANES, and
    SWEPT_LANES — the union the cron READS, Intake, Planning and Hand-work
    among it — is not what it closes from."""
    tree = ast.parse(inspect.getsource(reconcile.main.__wrapped__))
    branch = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.If) and isinstance(node.test, ast.Name)
        and node.test.id == "close_only"
    )
    names = {n.id for n in ast.walk(branch) if isinstance(n, ast.Name)}
    assert "EPIC_CLOSE_LANES" in names
    assert "SWEPT_LANES" not in names


def test_the_docstrings_name_the_lanes_an_epic_closes_from():
    for fn in (reconcile.close_finished_epics, reconcile._close_epic_if_finished):
        doc = fn.__doc__ or ""
        assert "EPIC_CLOSE_LANES" in doc, fn.__name__
        for lane in ("Todo", "In Progress", "In Review", "Backlog"):
            assert lane in doc, (fn.__name__, lane)


def test_the_lane_contract_says_a_finished_epic_leaves_backlog():
    """The contract is what the board's lanes are read from, so the Backlog
    exit names the close; `docs/lane-contract.md` is rendered from it and
    `tests/test_planning_classify.py` holds the render."""
    import json

    contract = json.loads(
        (Path(__file__).resolve().parent.parent / "config" / "lane-contract.json")
        .read_text(encoding="utf-8"))
    backlog = next(l for l in contract["lanes"] if l["name"] == "Backlog")
    assert (
        "An epic here whose children are all closed, with at least one Done, "
        "is closed by the sweep"
    ) in backlog["clauses"]["exit"]["text"]
