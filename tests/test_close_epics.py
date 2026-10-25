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

import lane_contract  # noqa: E402
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
    if lane in reconcile.QUEUE_LANES:
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
    fake = _board(reconcile.QUEUE_LANE)
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
    state, comment, advance = _sweep(_board(reconcile.QUEUE_LANE,
                                            kids=("Done", "In Progress")))
    assert not _closed(state)
    advance.assert_not_called()


def test_a_backlog_epic_whose_children_are_all_canceled_stays_open(board_pins):
    """Every child Canceled and none Done is not a completion — in Backlog as
    in every other lane."""
    state, _, advance = _sweep(_board(reconcile.QUEUE_LANE,
                                      kids=("Canceled", "Canceled")))
    assert not _closed(state)
    advance.assert_not_called()


@pytest.mark.parametrize("lane", ["In Progress", "In Review"])
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
    Green Light is not read by the sweep at all, Triage only for the outage
    alarm (DRE-6576), and Hand-work is a person's. None is closed — and
    closing from Backlog buys no read of Green Light or Triage."""
    state, _, advance = _sweep(_board(lane))
    assert not _closed(state)
    advance.assert_not_called()

    without = _board(None)
    _sweep(without)
    with_epic = _board(lane)
    _sweep(with_epic)
    lanes_read = lambda f: [tuple(v["states"]) for v in f.board_variables]  # noqa: E731
    assert lanes_read(with_epic) == lanes_read(without)
    assert sum("Triage" in lanes for lanes in lanes_read(with_epic)) == 1, (
        "the one board read carries the outage card's lane (DRE-6576), and "
        "closing from Backlog buys no other read of it")
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
    state, advance = _merge_close(monkeypatch, reconcile.QUEUE_LANE)
    assert _closed(state), state.call_args_list
    advance.assert_called_once_with(EPIC)


def test_the_merge_path_closes_nothing_whose_parent_is_in_planning(board_pins, monkeypatch):
    """Planning is a lane the cron never closes from, so a merge does not
    either — the two sites read one constant."""
    state, advance = _merge_close(monkeypatch, "Planning")
    assert not _closed(state)
    advance.assert_not_called()


def test_epic_close_lanes_are_the_sweep_lanes_and_backlog():
    assert reconcile.EPIC_CLOSE_LANES == reconcile.SWEEP_STATES + reconcile.QUEUE_LANES
    # Less Todo, retiring since DRE-7129: an epic there is carried out of it.
    assert set(reconcile.EPIC_CLOSE_LANES) == {
        "In Progress", "In Review", *lane_contract.QUEUE_LANES}


def test_both_sites_read_epic_close_lanes(board_pins, monkeypatch):
    """Take Backlog out of the one constant and NEITHER site closes from it —
    so both read it, and neither keeps a lane list of its own."""
    monkeypatch.setattr(reconcile, "EPIC_CLOSE_LANES", reconcile.SWEEP_STATES)
    state, _, _ = _sweep(_board(reconcile.QUEUE_LANE))
    assert not _closed(state), "the full sweep closed from a lane the constant left out"
    state, _ = _merge_close(monkeypatch, reconcile.QUEUE_LANE)
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
        for lane in ("In Progress", "In Review", "Queued"):
            assert lane in doc, (fn.__name__, lane)


def test_the_lane_contract_says_a_finished_epic_leaves_the_queue():
    """The contract is what the board's lanes are read from, so the Queued
    exit names the close; `docs/lane-contract.md` is rendered from it and
    `tests/test_planning_classify.py` holds the render. The lane it replaced is
    a retiring entry with no clauses of its own (DRE-6753)."""
    import json

    contract = json.loads(
        (Path(__file__).resolve().parent.parent / "config" / "lane-contract.json")
        .read_text(encoding="utf-8"))
    queued = next(l for l in contract["lanes"] if l["name"] == "Queued")
    assert (
        "An epic here whose children are all closed, with at least one Done, "
        "is closed by the sweep"
    ) in queued["clauses"]["exit"]["text"]


# ---------------------------------------------------------------------------
# DRE-7948: an epic closes on the sweep pass after its last build card is Done.
# On 2026-10-25, 33 In Progress epics fleet-wide were built out with only
# proofs or person-marked cards open, and nothing closed them: the close asked
# for every child closed. DRE-7912 is the replay below — four builds Done and
# live since 10:38 PM PT on 2026-10-24, DRE-7917 (OPERATOR) in Hand-work and
# DRE-7919 (PROOF) in Queued. The close now reads the one built-out definition
# the release gate reads (`_not_built_out`, DRE-6407), off the pass-cached
# single-issue read `_blocker_epic`, and never off a widened epic record.
# ---------------------------------------------------------------------------
import epic_cap  # noqa: E402

BUILT = "DRE-7912"
BUILDS = ("DRE-7913", "DRE-7914", "DRE-7915", "DRE-7916")
OPERATOR_CARD, PROOF_CARD = "DRE-7917", "DRE-7919"


def _in_motion_kid(identifier, state, *, title="a build card", labels=(), grandkids=0):
    """One child as `epic_cap.IN_MOTION_NODE` selects it."""
    return {
        "identifier": identifier,
        "title": title,
        "state": {"name": state},
        "labels": {"nodes": [{"name": n} for n in labels],
                   "pageInfo": {"hasNextPage": False}},
        "children": {"nodes": [{"id": f"g{n}"} for n in range(grandkids)]},
    }


def _dre_7912_kids():
    return [
        *(_in_motion_kid(b, "Done", title=f"bureau-pipeline: build {b}") for b in BUILDS),
        _in_motion_kid(OPERATOR_CARD, "Hand-work",
                       title="bureau-pipeline: rotate the signing secret",
                       labels=("operator-step", "no-code")),
        _in_motion_kid(PROOF_CARD, "Queued",
                       title="PROOF: DRE-7912 observed live", labels=("no-code",)),
    ]


class BuiltOutLinear:
    """The two reads the close makes: the pass's batched epic record
    (`EPIC_RECORD_GQL`, children carrying only their state) and the single-issue
    read of `epic_cap.IN_MOTION_NODE` (titles and labels), counted."""

    def __init__(self, kids, *, state="In Progress", more=False, identifier=BUILT):
        self.identifier = identifier
        self.kids = list(kids)
        self.state = state
        self.more = more
        self.full_reads: list[str] = []

    def record(self):
        return {
            "identifier": self.identifier,
            "description": "**Repo:** bureau-pipeline\nan epic",
            "state": {"name": self.state},
            "children": {"nodes": [
                {"identifier": k["identifier"], "createdAt": "2026-10-24T00:00:00Z",
                 "state": k["state"]} for k in self.kids]},
            "history": {"nodes": []},
            "inverseRelations": {"nodes": []},
        }

    def gql(self, query, variables=None):
        v = variables or {}
        flat = " ".join(query.split())
        if "$numbers" in query:
            return {"issues": {"nodes": [self.record()],
                               "pageInfo": {"hasNextPage": False, "endCursor": None}}}
        if "issue(id: $id)" in flat and "labels(first: 10)" in flat:
            self.full_reads.append(v.get("id"))
            return {"issue": {
                "identifier": self.identifier, "title": f"[EPIC] {self.identifier}",
                "priority": 3, "createdAt": "2026-10-20T00:00:00Z",
                "state": {"name": self.state},
                "children": {"nodes": list(self.kids),
                             "pageInfo": {"hasNextPage": self.more}},
            }}
        raise reconcile.linear_ops.LinearError(f"BuiltOutLinear: unexpected {flat[:80]}")


def _close(fake, epics=(BUILT,), *, passes=1):
    with patch.object(reconcile.linear_ops, "gql", side_effect=fake.gql), \
            patch.object(reconcile.linear_ops, "cmd_state") as state, \
            patch.object(reconcile.linear_ops, "cmd_comment") as comment, \
            patch.object(reconcile, "advance_unblocked_epics") as advance_chain:
        closed = set()
        for _ in range(passes):
            closed |= reconcile.close_finished_epics(set(epics))
    return closed, state, comment, advance_chain


def test_dre_7912_closes_in_one_pass_and_names_its_follow_on_work():
    """The replay: four builds Done, the OPERATOR card in Hand-work and the
    PROOF card in Queued. One pass closes it, the receipt opens as every
    close's does, and both open cards are named as follow-on work."""
    fake = BuiltOutLinear(_dre_7912_kids())
    closed, state, comment, advance_chain = _close(fake)
    assert closed == {BUILT}
    state.assert_called_once_with(BUILT, "Done")
    comment.assert_called_once()
    epic, body = comment.call_args.args
    assert epic == BUILT
    assert body.startswith("🏁 Epic complete:"), body
    assert "follow-on work carries on by itself:" in body, body
    tail = body.split("follow-on work carries on by itself:", 1)[1]
    assert f"{OPERATOR_CARD} operator-step no-code" in tail or (
        OPERATOR_CARD in tail and "operator-step" in tail), body
    assert f"{PROOF_CARD} PROOF" in tail, body
    for build in BUILDS:
        assert build not in tail, body
    advance_chain.assert_called_once_with(BUILT)


def test_an_open_buildable_child_keeps_the_epic_open():
    kids = _dre_7912_kids()
    kids[3] = _in_motion_kid(BUILDS[3], "In Review", title="bureau-pipeline: the last build")
    closed, state, comment, advance_chain = _close(BuiltOutLinear(kids))
    assert closed == set()
    state.assert_not_called()
    comment.assert_not_called()
    advance_chain.assert_not_called()


def test_a_children_page_not_read_to_the_end_keeps_the_epic_open():
    """The unread children might be cards left to build."""
    closed, state, _, _ = _close(BuiltOutLinear(_dre_7912_kids(), more=True))
    assert closed == set()
    state.assert_not_called()


def test_a_roll_up_with_an_open_child_epic_and_a_proof_stays_open():
    """Roll-up parents are unchanged: an open child epic is not built out,
    even when every other child is Done or a proof."""
    kids = [
        _in_motion_kid("DRE-7921", "Done", title="[EPIC] slice one", grandkids=1),
        _in_motion_kid("DRE-7922", "In Progress", title="[EPIC] slice two", grandkids=1),
        _in_motion_kid("DRE-7923", "Queued", title="PROOF: the roll-up works live"),
    ]
    closed, state, _, _ = _close(BuiltOutLinear(kids))
    assert closed == set()
    state.assert_not_called()


@pytest.mark.parametrize("lane", ["In Review", *lane_contract.QUEUE_LANES])
def test_the_build_done_close_is_an_in_progress_epics_alone(lane):
    """Today's rule holds in every close lane; the build-done case is one
    more, for an In Progress epic, and costs no read anywhere else."""
    fake = BuiltOutLinear(_dre_7912_kids(), state=lane)
    closed, state, _, _ = _close(fake)
    assert closed == set()
    state.assert_not_called()
    assert fake.full_reads == []


def test_every_child_canceled_and_none_done_still_does_not_close():
    kids = [_in_motion_kid("DRE-7931", "Canceled"),
            _in_motion_kid("DRE-7932", "Queued", title="PROOF: nothing shipped")]
    fake = BuiltOutLinear(kids)
    closed, state, _, _ = _close(fake)
    assert closed == set()
    state.assert_not_called()
    assert fake.full_reads == [], "no Done child: nothing to read"


def test_an_epic_whose_children_are_all_closed_spends_no_read():
    """Today's close answers off the pass's record alone."""
    fake = BuiltOutLinear([_in_motion_kid(b, "Done") for b in BUILDS])
    closed, _, comment, _ = _close(fake)
    assert closed == {BUILT}
    assert comment.call_args.args[1].startswith(
        "🏁 Epic complete: all 4 children are closed (4 done).")
    assert fake.full_reads == []


def test_the_close_reads_each_epic_at_most_once_a_pass():
    """Two close passes in one sweep over an epic that stays open: one
    `_blocker_epic` read, and the next pass (after the reset) one more."""
    kids = _dre_7912_kids()
    kids[0] = _in_motion_kid(BUILDS[0], "In Review")
    fake = BuiltOutLinear(kids)
    _close(fake, passes=2)
    assert fake.full_reads == [BUILT]
    reconcile.reset_sweep_cards()
    _close(fake)
    assert fake.full_reads == [BUILT, BUILT]


@pytest.mark.parametrize("child", [
    _in_motion_kid(BUILDS[0], lane_contract.DISPATCH_LANE),
    _in_motion_kid("DRE-7922", lane_contract.DISPATCH_LANE,
                   title="[EPIC] slice two", grandkids=1),
])
def test_a_child_in_the_dispatch_lane_keeps_the_epic_open_for_no_read(child):
    """A card in the dispatch lane is a build in flight — a proof or a
    person's card is never dispatched there — so the pass's record alone says
    the epic is not built out, and the one-read-a-pass contract holds for
    every epic mid-build (DRE-3642)."""
    kids = _dre_7912_kids()
    kids[0] = child
    fake = BuiltOutLinear(kids)
    closed, state, _, _ = _close(fake)
    assert closed == set()
    state.assert_not_called()
    assert fake.full_reads == []


def test_an_unreadable_full_read_skips_the_epic_out_loud(capsys):
    fake = BuiltOutLinear(_dre_7912_kids())

    def failing(query, variables=None):
        if "labels(first: 10)" in query:
            raise TimeoutError("The read operation timed out")
        return fake.gql(query, variables)

    with patch.object(reconcile.linear_ops, "gql", side_effect=failing), \
            patch.object(reconcile.linear_ops, "cmd_state") as state, \
            patch.object(reconcile, "advance_unblocked_epics"):
        assert reconcile.close_finished_epics({BUILT}) == set()
    state.assert_not_called()
    err = capsys.readouterr().err
    assert f"epic-close: could not close {BUILT}" in err
    assert "timed out" in err


def test_the_close_reads_follow_on_the_one_complement_of_buildable():
    """`epic_cap.follow_on` is the complement of `buildable()` among open
    non-epic children — not a new definition of built out."""
    kids = {k["identifier"]: k for k in _dre_7912_kids()}
    assert epic_cap.follow_on(kids[OPERATOR_CARD])
    assert epic_cap.follow_on(kids[PROOF_CARD])
    assert not epic_cap.follow_on(kids[BUILDS[0]]), "a Done child is not open"
    assert not epic_cap.follow_on(_in_motion_kid("DRE-1", "Queued")), "buildable"
    assert not epic_cap.follow_on(
        _in_motion_kid("DRE-2", "In Progress", title="[EPIC] child", grandkids=1))
    for child in [*kids.values(), _in_motion_kid("DRE-1", "Queued"),
                  _in_motion_kid("DRE-2", "Queued", title="[EPIC] x", grandkids=1)]:
        open_non_epic = epic_cap._is_open(child) and not epic_cap._child_is_epic(child)
        assert epic_cap.follow_on(child) == (open_non_epic and not epic_cap.buildable(child))


def test_the_lane_contract_states_the_build_done_close():
    import json

    contract = json.loads(
        (Path(__file__).resolve().parent.parent / "config" / "lane-contract.json")
        .read_text(encoding="utf-8"))
    lanes = {l["name"]: l for l in contract["lanes"]}
    exit_text = lanes["In Progress"]["clauses"]["exit"]["text"]
    assert "on the sweep pass after its last build card is Done" in exit_text
    assert "follow-on work" in exit_text
    assert "DRE-7948" in exit_text
    queued = lanes["Queued"]["clauses"]["exit"]["text"]
    assert "follow-on" in queued and "DRE-7948" in queued
