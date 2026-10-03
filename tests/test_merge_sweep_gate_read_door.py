"""Linear Sync's merge-sweep gate on the read door (Stage 2 BP-7, fix #15).

On every merge, `linear-sync.yml` asks `merge_sweep_gate.py` which scoped
passes the merge can have made worth running. Before this card that question
was always one Linear read (`merge_sweep_gate.QUERY`). In `on` it is answered
by the console's read door — the SAME rebuild Reconcile's merge path makes
(`/cards?ids=<merged>` + `/cards/<merged>/dependents?lanes=Backlog`, BP-2) —
and costs no Linear request.

The bar each test holds:
  * `off` is the gate before the door: one Linear read, the same flags, the
    door never asked (E-O7);
  * `on` decides from the door with no Linear request; a door that cannot
    answer whole falls back to the Linear read; `linear-hold` runs no pass and
    makes no Linear call (item 34);
  * the merged card's OWN lane is never a door fact: `card-done` wrote it
    seconds before the gate runs, and the door's copy predates that write
    (`door-older`). A Backlog dependent still runs the promotion pass, whose
    live re-check (item 45) is the gate on every promotion;
  * the epic half cannot be decided from the door (it serves no child
    states), so a live parent falls open to the close pass, as an unknown
    always has here;
  * `shadow` reads both, prints `read-door-diff:` lines, decides on Linear;
  * one rebuild, shared: Reconcile's merge path asks the gate's.
"""
from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/portico")
os.environ.setdefault("REPO_SLUG", "portico")

import bureau_read  # noqa: E402
import linear_ops  # noqa: E402
import merge_sweep_gate as gate  # noqa: E402
import reconcile  # noqa: E402
import routing_verdict  # noqa: E402
from test_reconcile_read_door import Linear, _advanced, _c, door_at, wired  # noqa: E402

PROMOTE, CLOSE = gate.PROMOTE, gate.CLOSE_EPICS


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in ("BUREAU_READ", "BUREAU_READ_URL", "BUREAU_PIPELINE_REF", "MERGED_CARD",
                 "SWEEP_REASON", "SWEEP_CARD",
                 "ACTIONS_ID_TOKEN_REQUEST_URL", "ACTIONS_ID_TOKEN_REQUEST_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(reconcile, "REPO_SLUG", "portico")
    monkeypatch.setattr(reconcile, "MAX_WIP", 5)
    monkeypatch.setattr(routing_verdict, "lane_moves", lambda ident: [])
    for ledger in (reconcile._write_failures, reconcile._read_failures,
                   reconcile._stale_defects, reconcile._card_skips):
        ledger.clear()
    bureau_read.reset_for_tests()
    reconcile.reset_sweep_cards()
    yield
    bureau_read.reset_for_tests()
    reconcile.reset_sweep_cards()


def _gate_reads(linear: Linear) -> list[str]:
    return [q for q in linear.queries if "relatedIssue" in q]


# ── off: the gate before the door (E-O7) ────────────────────────────────────


def test_off_reads_linear_once_and_never_asks_the_door(monkeypatch):
    merged = _c("DRE-801", "Done")
    dep = _c("DRE-802", "Backlog", blockers=(("DRE-801", "Done"),))
    linear = Linear(merged, dep)
    with door_at(monkeypatch, merged, dep, mode="off") as door, wired(linear):
        assert gate.decide("DRE-801") == [PROMOTE]
    assert door.requests == []
    assert linear.requests == 1 and len(_gate_reads(linear)) == 1


def test_off_with_nothing_unblocked_is_still_one_read_and_no_pass(monkeypatch):
    merged = _c("DRE-801", "Done")
    linear = Linear(merged)
    with door_at(monkeypatch, merged, mode="off") as door, wired(linear):
        assert gate.decide("DRE-801") == []
    assert door.requests == [] and linear.requests == 1


# ── on: decided from the door, no Linear request ────────────────────────────


def test_on_a_backlog_dependent_runs_the_promotion_with_no_linear_request(monkeypatch):
    merged = _c("DRE-801", "Done")
    dep = _c("DRE-802", "Backlog", blockers=(("DRE-801", "Done"),))
    linear = Linear(merged, dep)
    with door_at(monkeypatch, merged, dep) as door, wired(linear):
        assert gate.decide("DRE-801") == [PROMOTE]
    assert linear.requests == 0
    assert door.asked("/cards/DRE-801/dependents")[0]["query"]["lanes"] == "Backlog"
    assert door.asked("/cards")[0]["query"]["ids"] == "DRE-801"


def test_on_the_merged_cards_own_lane_is_not_read_from_the_door(monkeypatch):
    """`card-done` wrote Done seconds ago; the door's copy still says In Review.
    Read as a fact, that stale lane would make every merge in `on` promote
    nothing — silently, and FRESH, so no fallback would ever catch it."""
    merged_door = _c("DRE-801", "In Review")
    merged_live = _c("DRE-801", "Done")
    dep = _c("DRE-802", "Backlog", blockers=(("DRE-801", "In Review"),))
    linear = Linear(merged_live, dep)
    with door_at(monkeypatch, merged_door, dep), wired(linear):
        assert gate.decide("DRE-801") == [PROMOTE]
    assert linear.requests == 0


def test_on_no_backlog_dependent_and_no_parent_runs_no_pass_for_free(monkeypatch):
    merged = _c("DRE-801", "Done")
    # A dependent outside Backlog is not one the promotion can move.
    started = _c("DRE-803", "In Progress", blockers=(("DRE-801", "Done"),))
    linear = Linear(merged, started)
    with door_at(monkeypatch, merged, started), wired(linear):
        assert gate.decide("DRE-801") == []
    assert linear.requests == 0


def test_on_a_live_parent_falls_open_to_the_close_pass(monkeypatch):
    """The door serves no child states, so whether this merge finished the epic
    is unknown — and unknown has always run the pass here. The pass reads the
    epic's children live before it closes anything."""
    merged = _c("DRE-801", "Done", parent="DRE-800", parent_lane="In Progress")
    linear = Linear(merged)
    with door_at(monkeypatch, merged), wired(linear):
        assert gate.decide("DRE-801") == [CLOSE]
    assert linear.requests == 0


def test_on_a_closed_parent_needs_no_pass(monkeypatch):
    merged = _c("DRE-801", "Done", parent="DRE-800", parent_lane="Done")
    linear = Linear(merged)
    with door_at(monkeypatch, merged), wired(linear):
        assert gate.decide("DRE-801") == []
    assert linear.requests == 0


# ── on: what the door cannot answer ─────────────────────────────────────────


def test_on_an_unknown_answer_falls_back_to_todays_linear_read(monkeypatch, capsys):
    merged = _c("DRE-801", "Done")
    dep = _c("DRE-802", "Backlog", blockers=(("DRE-801", "Done"),))
    linear = Linear(merged, dep)
    with door_at(monkeypatch, merged, dep) as door, wired(linear):
        door.routes["dependents"] = door.unknown("relations-stale")
        assert gate.decide("DRE-801") == [PROMOTE]
    assert len(_gate_reads(linear)) == 1
    assert "read from Linear" in capsys.readouterr().err


def test_on_a_card_the_door_does_not_hold_falls_back(monkeypatch):
    merged = _c("DRE-801", "Done")
    linear = Linear(merged)
    with door_at(monkeypatch) , wired(linear):  # an empty world: 404
        assert gate.decide("DRE-801") == []
    assert len(_gate_reads(linear)) == 1


def test_on_a_closed_door_falls_back(monkeypatch):
    merged = _c("DRE-801", "Done")
    dep = _c("DRE-802", "Backlog", blockers=(("DRE-801", "Done"),))
    linear = Linear(merged, dep)
    with door_at(monkeypatch, merged, dep) as door, wired(linear):
        door.routes["/cards"] = (503, {"error": {"code": "DOOR_CLOSED"}})
        assert gate.decide("DRE-801") == [PROMOTE]
    assert len(_gate_reads(linear)) == 1


def test_on_linear_hold_runs_no_pass_and_makes_no_linear_call(monkeypatch, capsys):
    merged = _c("DRE-801", "Done")
    dep = _c("DRE-802", "Backlog", blockers=(("DRE-801", "Done"),))
    linear = Linear(merged, dep)
    with door_at(monkeypatch, merged, dep) as door, wired(linear):
        door.routes["/cards"] = door.unknown("linear-hold")
        assert gate.decide("DRE-801") == []
    assert linear.requests == 0
    assert "linear-hold" in capsys.readouterr().err


# ── shadow: compared, decided on Linear ─────────────────────────────────────


def test_shadow_decides_on_linear_and_prints_the_comparison(monkeypatch, capsys):
    merged = _c("DRE-801", "Done")
    dep_live = _c("DRE-802", "Backlog", blockers=(("DRE-801", "Done"),))
    linear = Linear(merged, dep_live)
    with door_at(monkeypatch, merged, mode="shadow") as door, wired(linear):
        # The door has not seen the relation yet: it lists no dependent.
        assert gate.decide("DRE-801") == [PROMOTE]
    assert door.asked("/cards/DRE-801/dependents")
    assert len(_gate_reads(linear)) == 1
    err = capsys.readouterr().err
    assert "read-door-diff: door-older dependents DRE-801 dependents" in err
    assert "read-door-diff: dependents compared 1 card(s)" in err


# ── stdout is the flags and nothing else ────────────────────────────────────
# `linear-sync.yml` runs `SWEEPS=$(merge_sweep_gate.py "$CARD")` and then
# `for SWEEP in $SWEEPS` — every word on stdout becomes a reconcile.py argument.
# A door line printed there would run `reconcile.py read-door:` (a full pass).


@pytest.mark.parametrize("mode,route", [
    ("on", None), ("on", "unknown"), ("on", "hold"), ("on", "closed"),
    ("shadow", None), ("shadow", "unknown"),
])
def test_the_gates_stdout_carries_only_flags_in_every_mode(monkeypatch, capsys, mode, route):
    merged = _c("DRE-801", "Done", parent="DRE-800")
    dep = _c("DRE-802", "Backlog", blockers=(("DRE-801", "Done"),))
    linear = Linear(merged, _c("DRE-802", "Backlog", blockers=(("DRE-801", "Done"),
                                                               ("DRE-804", "Done"))))
    with door_at(monkeypatch, merged, dep, mode=mode) as door, wired(linear):
        if route == "unknown":
            door.routes["dependents"] = door.unknown("relations-stale")
        elif route == "hold":
            door.routes["/cards"] = door.unknown("linear-hold")
        elif route == "closed":
            door.routes["/cards"] = (503, {"error": {"code": "DOOR_CLOSED"}})
        assert gate.main(["merge_sweep_gate.py", "DRE-801"]) == 0
    out = capsys.readouterr().out
    assert set(out.split()) <= set(gate.ALL_SWEEPS), out


# ── one rebuild, shared ─────────────────────────────────────────────────────


def test_reconcile_asks_the_gates_rebuild_and_keeps_no_copy(monkeypatch):
    asked = []

    def fake(merged):
        asked.append(merged)
        return None

    monkeypatch.setattr(gate, "door_card", fake)
    reconcile._door_dependents("DRE-801")
    assert asked == ["DRE-801"]
    source = inspect.getsource(reconcile)
    assert '"relatedIssue": {"identifier"' not in source
    assert "def _shadow_compare_dependents" not in source


def test_reconcile_still_skips_on_linear_hold_through_the_shared_rebuild(monkeypatch):
    merged = _c("DRE-801", "Done")
    linear = Linear(merged)
    with door_at(monkeypatch, merged) as door, wired(linear):
        door.routes["/cards"] = door.unknown("linear-hold")
        with pytest.raises(reconcile.BoardHeld):
            reconcile._door_dependents("DRE-801")
    assert reconcile._door_hold == ["linear-hold"]
    assert linear.requests == 0


# ── the whole merge path, in-process (`reconcile.sweep_scope`) ──────────────


def test_a_dispatched_card_done_pass_decides_on_the_door_and_promotes_after_a_live_read(
        monkeypatch):
    merged_door = _c("DRE-801", "In Review")
    merged_live = _c("DRE-801", "Done")
    dep = _c("DRE-802", "Backlog", blockers=(("DRE-801", "Done"),))
    linear = Linear(merged_live, dep)
    with door_at(monkeypatch, merged_door, dep), wired(linear):
        monkeypatch.setenv("SWEEP_REASON", "card-done")
        monkeypatch.setenv("SWEEP_CARD", "DRE-801")
        reconcile.run([])
    assert _advanced(linear) == ["DRE-802"]
    assert _gate_reads(linear) == []  # neither the gate nor the scope read Linear
    # The one Linear read on the whole path is the dependent's live re-check.
    assert [q for q in linear.queries if 'eq: "Backlog"' in q] == linear.queries


def test_on_a_dependent_blocked_live_is_still_held(monkeypatch):
    merged = _c("DRE-801", "Done")
    dep_door = _c("DRE-802", "Backlog", blockers=(("DRE-801", "Done"),))
    dep_live = _c("DRE-802", "Backlog", blockers=(("DRE-801", "Done"), ("DRE-803", "Todo")))
    linear = Linear(merged, dep_live, _c("DRE-803", "Todo"))
    with door_at(monkeypatch, merged, dep_door), wired(linear):
        monkeypatch.setenv("SWEEP_REASON", "card-done")
        monkeypatch.setenv("SWEEP_CARD", "DRE-801")
        reconcile.run([])
    assert _advanced(linear) == []


def test_the_gate_still_never_writes(monkeypatch):
    merged = _c("DRE-801", "Done", parent="DRE-800")
    dep = _c("DRE-802", "Backlog", blockers=(("DRE-801", "Done"),))
    linear = Linear(merged, dep)
    with door_at(monkeypatch, merged, dep), wired(linear):
        gate.decide("DRE-801")
    assert linear.writes == [] and linear.comments == [] and linear.labels == []
    assert linear_ops is gate.linear_ops
