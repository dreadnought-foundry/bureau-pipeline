"""RED-first: an approved epic is released when its blocker epic is BUILT OUT,
not when it is Done (DRE-6407).

THE INCIDENT. 2026-10-09's sweep logs: `epic-gate: DRE-6061 is held by a formal
blockedBy relation on DRE-6059 (In Progress) — not Done`. DRE-6059's only open
work was its proof card, itself deadlocked, and about 34 cards of an approved
epic sat still behind it. DRE-6022 behind DRE-6021 froze about 14 more. The
code dependencies were already written as card-level `blockedBy` relations, and
the card gate below the epic gate honors every one of them.

THE RULE, two of them, one per caller of `reconcile.epic_blockers_unmet`:

  1. The promotion gate (`promote_ready`) releases the Backlog children of an
     epic the CEO has already approved. There an In Progress blocker epic
     counts as cleared once it is BUILT OUT: In Progress, its children read to
     the end, at least one child, no `epic_cap.buildable()` child and no open
     child epic — `epic_cap.counts_against_cap(record,
     count_rollup_parents=True)` is false. A proof card and a card a person
     builds by hand do not hold.
  2. The auto-advance (`advance_unblocked_epics`) moves a Backlog epic to
     Planning to be PLANNED (DRE-6653). It is not held by a proof either
     (DRE-7831): a built-out In Progress blocker epic counts as cleared, and
     the planner can still read the blocker's proof record when it plans.

The blocker's children come from ONE single-issue read per distinct In
Progress blocker per pass, selecting `epic_cap.IN_MOTION_NODE` — never by
widening the pass's epic record, whose weight
`tests/test_growth_rides_the_record.py` pins.

Run: cd bureau-pipeline && python3 -m pytest tests/test_epic_release_at_build_done.py -v
"""
from __future__ import annotations

import copy
import inspect
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")

import epic_cap  # noqa: E402
import linear_ops  # noqa: E402
import reconcile  # noqa: E402
import routing_verdict  # noqa: E402

AT = "2026-10-09T08:00:00.000Z"
FLEET_VERDICT = routing_verdict.verdict_comment(
    "FLEET", "the acceptance criteria are unit-testable")

#: The waiting epic W, the blocking epic B, and a second blocking epic A.
W, B, A = "DRE-800", "DRE-700", "DRE-600"
#: W's two Backlog children: one free, one blocked by a card In Review.
FREE, TIED, TIE = "DRE-901", "DRE-902", "DRE-950"


@pytest.fixture(autouse=True)
def _fresh_pass(monkeypatch):
    """One pass per test, and this test's cards are this repo's."""
    monkeypatch.setattr(reconcile, "REPO_SLUG", "agent-bureau")
    reconcile._write_failures.clear()
    reconcile.reset_sweep_cards()
    yield
    reconcile.reset_sweep_cards()


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------
def _epic_record(identifier, *, blocked_by=(), state="In Progress", kids=()):
    """W as the pass's epic record carries it (`EPIC_RECORD_GQL`'s shape)."""
    return {
        "identifier": identifier,
        "description": "**Repo:** agent-bureau\nan epic",
        "state": {"name": state},
        "children": {"nodes": [
            {"identifier": i, "createdAt": AT, "state": {"name": s}}
            for i, s in kids
        ]},
        "history": {"nodes": []},
        "inverseRelations": {"nodes": [
            {"type": "blocks", "issue": {"identifier": i, "state": {"name": s}}}
            for i, s in blocked_by
        ]},
    }


def _kid(identifier, state, *, title="a build card", labels=(), grandkids=0):
    """One child as `epic_cap.IN_MOTION_NODE` selects it."""
    return {
        "identifier": identifier,
        "title": title,
        "state": {"name": state},
        "labels": {"nodes": [{"name": n} for n in labels],
                   "pageInfo": {"hasNextPage": False}},
        "children": {"nodes": [{"id": f"g{n}"} for n in range(grandkids)]},
    }


def _proof(identifier="DRE-703", state="Backlog"):
    return _kid(identifier, state, title="PROOF: the blocking epic works live")


def _blocker(identifier, kids, *, state="In Progress", more=False):
    """B as its single-issue read returns it (`epic_cap.IN_MOTION_NODE`)."""
    return {
        "identifier": identifier,
        "title": f"[EPIC] bureau-pipeline: {identifier}",
        "priority": 3,
        "createdAt": AT,
        "state": {"name": state},
        "children": {"nodes": list(kids), "pageInfo": {"hasNextPage": more}},
    }


#: B with every build card Done and only its proof open.
B_BUILT_OUT = _blocker(B, [_kid("DRE-701", "Done"), _kid("DRE-702", "Done"), _proof()])


def _child(identifier, *, blocked_by=()):
    """A Backlog child of W, eligible on every ground but the ones under test."""
    return {
        "identifier": identifier,
        "description": "**Repo:** agent-bureau\nwork",
        "createdAt": AT,
        "parent": {"identifier": W, "state": {"name": "In Progress"}},
        "labels": {"nodes": [{"name": "size:M"}]},
        "comments": {"nodes": [{"body": FLEET_VERDICT}]},
        "inverseRelations": {"nodes": [
            {"type": "blocks", "issue": {"identifier": i, "state": {"name": s}}}
            for i, s in blocked_by
        ]},
    }


def _is_blocker_read(query: str) -> bool:
    """The gate's single-issue read of a blocker: `issue(id:)` selecting the
    In Progress node's child labels — a shape nothing else here asks for."""
    flat = " ".join(query.split())
    return "issue(id: $id)" in flat and "labels(first: 10)" in flat


class FakeLinear:
    """The reads the gate, the close and the auto-advance make."""

    def __init__(self, epics=(), blockers=(), *, blocker_error=None,
                 forward=None, states=None):
        self.epics = {e["identifier"]: e for e in epics}
        self.blockers = {b["identifier"]: b for b in blockers}
        self.blocker_error = blocker_error
        self.forward = dict(forward or {})
        self.states = dict(states or {})
        self.blocker_reads: list[str] = []
        self.queries: list[str] = []

    def gql(self, query, variables=None):
        v = variables or {}
        self.queries.append(query)
        flat = " ".join(query.split())
        if "$numbers" in query:
            wanted = {int(n) for n in v.get("numbers") or ()}
            return {"issues": {
                "nodes": [r for i, r in self.epics.items()
                          if int(i.split("-")[1]) in wanted],
                "pageInfo": {"hasNextPage": False, "endCursor": None},
            }}
        if _is_blocker_read(query):
            self.blocker_reads.append(v.get("id"))
            if self.blocker_error is not None:
                raise self.blocker_error
            return {"issue": self.blockers.get(v.get("id"))}
        if "relations(first: 20)" in flat:
            return {"issue": {"relations": {"nodes": [
                {"type": "blocks", "issue": {"identifier": d}}
                for d in self.forward.get(v.get("id"), ())
            ]}}}
        if flat.endswith("issue(id: $id) { state { name } } }"):
            return {"issue": {"state": {"name": self.states[v.get("id")]}}}
        raise linear_ops.LinearError(f"FakeLinear: unexpected query {flat[:80]}")


def _w_record(*blocked_by):
    return _epic_record(W, blocked_by=blocked_by,
                        kids=((FREE, "Backlog"), (TIED, "Backlog")))


def _sweep(fake, children=None):
    """One promotion pass over W's children; returns the cards moved to FLEET's
    lane, In Progress since the flip (DRE-7120)."""
    children = children if children is not None else [
        _child(FREE), _child(TIED, blocked_by=((TIE, "In Review"),)),
    ]
    # W was activated: its thread carries the activate route's note, the start
    # the epic cap's promotion hold reads (DRE-6493).
    activated = [{"body": f"{epic_cap.ACTIVATED_NOTE} (2 children)",
                  "authored_by_pipeline": True, "created_at": None}]
    with patch.object(linear_ops, "gql", side_effect=fake.gql), \
            patch.object(reconcile, "backlog_children", return_value=children), \
            patch.object(reconcile, "epic_thread", return_value=activated), \
            patch.object(linear_ops, "cmd_advance") as advance, \
            patch.object(linear_ops, "cmd_comment"), \
            patch.object(routing_verdict, "lane_moves", return_value=[]):
        reconcile.promote_ready(active_count=0)
    return [c.args[0] for c in advance.call_args_list
            if c.args[1] == routing_verdict.destination("FLEET")]


def _gate_lines(out: str) -> list[str]:
    return [line for line in out.splitlines() if line.startswith("epic-gate:")]


# --------------------------------------------------------------------------
# rule 1, released: the blocker's only open child is its proof
# --------------------------------------------------------------------------
def test_a_built_out_blocker_releases_the_free_child_and_the_card_gate_still_holds(capsys):
    """The card's first scenario, end to end through the promotion pass."""
    fake = FakeLinear([_w_record((B, "In Progress"))], [B_BUILT_OUT])
    moved = _sweep(fake)
    out = capsys.readouterr().out
    assert moved == [FREE], out
    assert (
        f"promotion: {TIED} is held by {TIE} (In Review), declared by a formal "
        "blockedBy relation" in out
    ), out
    lines = _gate_lines(out)
    assert any("**released at build-done**" in line and B in line for line in lines), lines
    released = next(line for line in lines if "released at build-done" in line)
    assert "has no buildable child left" in released
    assert "DRE-703" in released and "PROOF" in released, released


def test_the_blocker_is_read_once_per_pass_and_the_reset_drops_it(capsys):
    """One single-issue read per distinct In Progress blocker per sweep, cached
    for the pass the way the epic record is, and dropped with it."""
    fake = FakeLinear([_w_record((B, "In Progress"))], [B_BUILT_OUT])
    with patch.object(linear_ops, "gql", side_effect=fake.gql):
        assert reconcile.epic_blockers_unmet(W) is False
        assert reconcile.epic_blockers_unmet(W) is False
        assert fake.blocker_reads == [B]
        reconcile.reset_sweep_cards()
        assert reconcile.epic_blockers_unmet(W) is False
    assert fake.blocker_reads == [B, B]


def test_a_hand_built_card_does_not_hold_and_is_named_among_the_open(capsys):
    """A card a person finishes by hand closes on that person's timetable, like
    the proof: B is built out, and the line names it."""
    hand = _kid("DRE-704", "Backlog", labels=(epic_cap.HAND_BUILT_LABEL,))
    fake = FakeLinear(
        [_w_record((B, "In Progress"))],
        [_blocker(B, [_kid("DRE-701", "Done"), hand, _proof()])],
    )
    moved = _sweep(fake)
    out = capsys.readouterr().out
    assert FREE in moved, out
    released = [line for line in _gate_lines(out) if "**released at build-done**" in line]
    assert released, out
    assert "DRE-704" in released[0] and "DRE-703" in released[0], released


# --------------------------------------------------------------------------
# rule 1, held: each of the five conditions, out loud
# --------------------------------------------------------------------------
def _assert_held(capsys, fake, *needles):
    moved = _sweep(fake)
    out = capsys.readouterr().out
    assert moved == [], out
    lines = [line for line in _gate_lines(out) if "**not built out**" in line]
    assert lines, out
    assert B in lines[0], lines
    for needle in needles:
        assert needle in lines[0], lines
    assert "released at build-done" not in out
    return lines[0]


def test_an_open_build_card_holds_and_is_named(capsys):
    """B with one build card still in Backlog: nothing of W moves."""
    fake = FakeLinear(
        [_w_record((B, "In Progress"))],
        [_blocker(B, [_kid("DRE-701", "Done"), _kid("DRE-702", "Backlog"), _proof()])],
    )
    _assert_held(capsys, fake, "a buildable child is open", "DRE-702")


def test_an_open_child_epic_holds(capsys):
    """A roll-up parent releases nothing until it is Done."""
    fake = FakeLinear(
        [_w_record((B, "In Progress"))],
        [_blocker(B, [_kid("DRE-701", "Done"),
                      _kid("DRE-705", "In Progress", title="[EPIC] part two", grandkids=1),
                      _proof()])],
    )
    _assert_held(capsys, fake, "an open child epic", "DRE-705")


def test_a_blocker_with_no_children_holds(capsys):
    fake = FakeLinear([_w_record((B, "In Progress"))], [_blocker(B, [])])
    _assert_held(capsys, fake, "it has no children")


def test_a_children_page_not_read_to_the_end_holds(capsys):
    fake = FakeLinear(
        [_w_record((B, "In Progress"))],
        [_blocker(B, [_kid("DRE-701", "Done"), _proof()], more=True)],
    )
    _assert_held(capsys, fake, "its children page was not read to the end")


@pytest.mark.parametrize("lane", ["Backlog", "Planning", "Green Light"])
def test_an_unapproved_blocker_holds_without_a_read(capsys, lane):
    """A blocker that has built nothing holds, as today — and its state comes
    off the relation, so no read is spent on it."""
    fake = FakeLinear([_w_record((B, lane))], [B_BUILT_OUT])
    _assert_held(capsys, fake, f"({lane})", "not In Progress")
    assert fake.blocker_reads == []


@pytest.mark.parametrize("lane", ["Backlog", "Planning", "Green Light"])
def test_a_blocker_whose_own_read_is_not_in_progress_holds(capsys, lane):
    """The relation says In Progress, the blocker's own read says it moved back:
    the read wins, and a stale relation releases nothing."""
    fake = FakeLinear(
        [_w_record((B, "In Progress"))],
        [_blocker(B, [_kid("DRE-701", "Done"), _kid("DRE-702", "Done"), _proof()],
                  state=lane)],
    )
    _assert_held(capsys, fake, f"its state now reads {lane}", "not In Progress")
    assert fake.blocker_reads == [B]


def test_an_unreadable_blocker_holds_with_the_reason(capsys):
    fake = FakeLinear(
        [_w_record((B, "In Progress"))], [B_BUILT_OUT],
        blocker_error=linear_ops.LinearError("Linear timed out"),
    )
    _assert_held(capsys, fake, "its children could not be read", "Linear timed out")


def test_a_done_blocker_is_met_without_a_read(capsys):
    fake = FakeLinear([_w_record((B, "Done"))], [B_BUILT_OUT])
    with patch.object(linear_ops, "gql", side_effect=fake.gql):
        assert reconcile.epic_blockers_unmet(W) is False
    assert fake.blocker_reads == []


# --------------------------------------------------------------------------
# the reuse: `epic_cap.buildable` is the one definition
# --------------------------------------------------------------------------
def test_the_gate_reads_epic_caps_buildable_and_writes_no_second_one(capsys, monkeypatch):
    """Answer `buildable` true for the proof and the gate holds; false for
    every child and it releases. Nothing in reconcile restates the rule."""
    fake = FakeLinear([_w_record((B, "In Progress"))], [B_BUILT_OUT])
    monkeypatch.setattr(
        epic_cap, "buildable",
        lambda child: (child.get("title") or "").startswith("PROOF:"),
    )
    with patch.object(linear_ops, "gql", side_effect=fake.gql):
        assert reconcile.epic_blockers_unmet(W) is True
    assert "**not built out**" in capsys.readouterr().out

    reconcile.reset_sweep_cards()
    monkeypatch.setattr(epic_cap, "buildable", lambda child: False)
    blocked = _blocker(B, [_kid("DRE-702", "Backlog"), _proof()])
    fake = FakeLinear([_w_record((B, "In Progress"))], [blocked])
    with patch.object(linear_ops, "gql", side_effect=fake.gql):
        assert reconcile.epic_blockers_unmet(W) is False
    assert "**released at build-done**" in capsys.readouterr().out

    source = (Path(reconcile.__file__)).read_text(encoding="utf-8")
    assert "def buildable" not in source
    assert "def _buildable" not in source


def test_the_pass_record_is_not_widened():
    """The blocker's children ride their own read, never `EPIC_RECORD_GQL`."""
    selection = " ".join(reconcile.EPIC_RECORD_GQL.split())
    assert "labels(first: 10)" not in selection
    assert "title" not in selection


# --------------------------------------------------------------------------
# rule 2: the auto-advance is not held by a proof (DRE-7831)
# --------------------------------------------------------------------------
def _seam(a_state, a_kids):
    b_done = _epic_record(B, kids=(("DRE-701", "Done"), ("DRE-702", "Done")))
    w = _epic_record(W, state="Backlog", blocked_by=((A, a_state), (B, "Done")))
    return FakeLinear(
        [b_done, w], [_blocker(A, a_kids, state=a_state)],
        forward={B: (W,)}, states={W: "Backlog"},
    )


def test_the_auto_advance_moves_w_when_its_other_blocker_has_only_its_proof_open(capsys):
    """W is a Backlog epic still to be planned, blocked by A (only its proof
    open) and by B. B closing advances W: A is built out, and an open proof
    never holds back the next parts (DRE-7831). The hook's receipt names only
    the blocker that just closed."""
    fake = _seam("In Progress", [_kid("DRE-601", "Done"), _proof("DRE-603")])
    with patch.object(linear_ops, "gql", side_effect=fake.gql), \
            patch.object(linear_ops, "cmd_state") as state, \
            patch.object(linear_ops, "cmd_advance") as advance, \
            patch.object(linear_ops, "cmd_comment") as comment:
        assert reconcile._close_epic_if_finished(B) is True
    out = capsys.readouterr().out
    state.assert_called_once_with(B, "Done")
    advance.assert_called_once_with(W, "Planning", reconcile.QUEUE_FROM)
    lines = [line for line in _gate_lines(out) if W in line]
    released = [line for line in lines if "released at build-done" in line]
    assert released and A in released[0], lines
    assert "DRE-603 PROOF" in released[0], released
    assert fake.blocker_reads == [A]
    to_w = [c.args[1] for c in comment.call_args_list if c.args[0] == W]
    assert len(to_w) == 1, to_w
    assert to_w[0].startswith(
        "🧹 Auto-advanced Queued → Planning: blocker epic DRE-700 is Done, and no "
        "blocker still holds it."
    ), to_w[0]
    # The receipt's fixed second paragraph says a person approves the plan
    # (→ In Progress); the line that names blockers never says it.
    first = to_w[0].split("\n")[0]
    assert "In Progress" not in first, to_w[0]
    assert "every blocker is now closed" not in to_w[0], to_w[0]
    assert A not in to_w[0], to_w[0]


def test_the_auto_advance_moves_w_once_every_blocker_is_done(capsys):
    fake = _seam("Done", [_kid("DRE-601", "Done"), _proof("DRE-603", "Done")])
    with patch.object(linear_ops, "gql", side_effect=fake.gql), \
            patch.object(linear_ops, "cmd_state"), \
            patch.object(linear_ops, "cmd_advance") as advance, \
            patch.object(linear_ops, "cmd_comment") as comment:
        assert reconcile._close_epic_if_finished(B) is True
    advance.assert_called_once_with(W, "Planning", reconcile.QUEUE_FROM)
    to_w = [c.args[1] for c in comment.call_args_list if c.args[0] == W]
    assert len(to_w) == 1 and to_w[0].startswith("🧹 Auto-advanced Queued → Planning:"), to_w


def test_the_strict_rule_is_asked_for_by_name():
    """The promotion gate keeps the one-argument call; the auto-advance asks
    for the strict rule explicitly."""
    with patch.object(linear_ops, "gql", return_value={"issue": {"relations": {
                "nodes": [{"type": "blocks", "issue": {"identifier": W}}]}}}), \
            patch.object(reconcile, "card_state", return_value="Backlog"), \
            patch.object(reconcile, "epic_blockers_unmet", return_value=True) as gate, \
            patch.object(linear_ops, "cmd_advance"):
        reconcile.advance_unblocked_epics(B)
    gate.assert_called_once_with(W, release_at_build_done=False)


# --------------------------------------------------------------------------
# DRE-7774: the CARD gate gets the same rule — a card blocked by an approved
# epic is released once that epic is built out, its proof still open
# --------------------------------------------------------------------------
#: The card under the gate (DRE-7666's shape), the epic it is blocked by
#: (DRE-7685's), that epic's proof (DRE-7711's), and an ordinary card blocker.
CARD, EPIC_BLOCKER, ITS_PROOF, PLAIN = "DRE-1666", "DRE-1685", "DRE-1711", "DRE-1700"

#: Tonight's shape (2026-10-22): eight build cards Done, the PROOF In Review.
TONIGHT = _blocker(
    EPIC_BLOCKER,
    [_kid(f"DRE-16{n:02d}", "Done") for n in range(86, 94)]
    + [_proof(ITS_PROOF, "In Review")],
)


def _queued(identifier=CARD, *, blocked_by=((EPIC_BLOCKER, "In Progress"),), parent=True):
    """A Queued card the promotion gate considers: a child of the approved
    epic W, or a parentless card."""
    card = _child(identifier, blocked_by=blocked_by)
    if not parent:
        card["parent"] = None
    return card


def _card_sweep(fake, cards, *, door=True):
    """One promotion pass over `cards`, the live re-read included when `door`
    (every card door-sourced, so `live_promotion_refusal` reads each again)."""
    live = {c["identifier"]: copy.deepcopy(c) for c in cards}
    activated = [{"body": f"{epic_cap.ACTIVATED_NOTE} (1 children)",
                  "authored_by_pipeline": True, "created_at": None}]
    with patch.object(linear_ops, "gql", side_effect=fake.gql), \
            patch.object(reconcile, "backlog_children", return_value=cards), \
            patch.object(reconcile, "_door_sourced",
                         {c["identifier"] for c in cards} if door else set()), \
            patch.object(reconcile, "_fetch_backlog_linear",
                         side_effect=lambda ids, **kw: [live[i] for i in ids if i in live]), \
            patch.object(reconcile, "epic_thread", return_value=activated), \
            patch.object(linear_ops, "cmd_advance") as advance, \
            patch.object(linear_ops, "cmd_comment"), \
            patch.object(routing_verdict, "lane_moves", return_value=[]):
        reconcile.promote_ready(active_count=0)
    return [c.args[0] for c in advance.call_args_list
            if c.args[1] == routing_verdict.destination("FLEET")]


def _promotion_lines(out: str, card: str = CARD) -> list[str]:
    return [line for line in out.splitlines()
            if line.startswith("promotion:") and card in line]


def _w_free():
    return _epic_record(W, kids=((CARD, "Queued"),))


@pytest.mark.parametrize("parent", [True, False], ids=["epic-child", "parentless"])
def test_tonights_shape_is_released_at_build_done_through_the_live_reread(capsys, parent):
    """The replay: a Queued card blocked by an In Progress epic whose eight
    build cards are Done and whose PROOF is In Review is promoted, the live
    re-read agrees, and the line names the rule. Today it is held."""
    fake = FakeLinear([_w_free()], [TONIGHT])
    moved = _card_sweep(fake, [_queued(parent=parent)])
    out = capsys.readouterr().out
    assert moved == [CARD], out
    released = [line for line in _promotion_lines(out) if "released at build-done" in line]
    assert released, out
    assert released[0].startswith(
        f"promotion: {CARD}'s blocker {EPIC_BLOCKER} (In Progress) is released at build-done:"
    ), released
    assert "open: " in released[0] and ITS_PROOF in released[0] and "PROOF" in released[0]
    assert "live re-check refused" not in out, out
    assert "is held by" not in out, out
    # One read of the blocker per pass: the live re-read asks the cache.
    assert fake.blocker_reads == [EPIC_BLOCKER]


def test_an_epic_blocker_with_a_build_card_open_holds_and_names_it(capsys):
    """The same card, the blocker with one build card still open: held, and
    the line says not built out and names that card."""
    kids = [_kid(f"DRE-16{n:02d}", "Done") for n in range(86, 93)]
    kids += [_kid("DRE-1693", "In Progress"), _proof(ITS_PROOF, "Backlog")]
    fake = FakeLinear([_w_free()], [_blocker(EPIC_BLOCKER, kids)])
    moved = _card_sweep(fake, [_queued()])
    out = capsys.readouterr().out
    assert moved == [], out
    held = [line for line in _promotion_lines(out) if "is held by" in line]
    assert held, out
    assert (
        f"promotion: {CARD} is held by {EPIC_BLOCKER} (In Progress), declared by a "
        "formal blockedBy relation — not built out: a buildable child is open: DRE-1693"
        " — skipping"
    ) == held[0], held
    assert "released at build-done" not in out


def test_an_unreadable_epic_blocker_holds_with_the_reason(capsys):
    fake = FakeLinear([_w_free()], [TONIGHT],
                      blocker_error=linear_ops.LinearError("Linear timed out"))
    moved = _card_sweep(fake, [_queued()])
    out = capsys.readouterr().out
    assert moved == [], out
    held = [line for line in _promotion_lines(out) if "is held by" in line]
    assert held and "Linear timed out" in held[0], out
    assert held[0].endswith(" — skipping"), held


def test_an_ordinary_in_progress_blocker_holds_with_todays_line(capsys):
    """A card blocked by a card waits for that card exactly as today."""
    plain = {"identifier": PLAIN, "title": "a build card", "priority": 3,
             "createdAt": AT, "state": {"name": "In Progress"},
             "children": {"nodes": [], "pageInfo": {"hasNextPage": False}}}
    fake = FakeLinear([_w_free()], [plain])
    moved = _card_sweep(fake, [_queued(blocked_by=((PLAIN, "In Progress"),))])
    out = capsys.readouterr().out
    assert moved == [], out
    assert (
        f"promotion: {CARD} is held by {PLAIN} (In Progress), declared by a formal "
        "blockedBy relation — skipping"
    ) in out.splitlines(), out
    assert "not built out" not in out and "released at build-done" not in out


@pytest.mark.parametrize("lane", ["Queued", "Planning", "Green Light"])
def test_an_epic_blocker_not_in_progress_holds_with_todays_line_and_no_read(capsys, lane):
    fake = FakeLinear([_w_free()], [TONIGHT])
    moved = _card_sweep(fake, [_queued(blocked_by=((EPIC_BLOCKER, lane),))])
    out = capsys.readouterr().out
    assert moved == [], out
    assert (
        f"promotion: {CARD} is held by {EPIC_BLOCKER} ({lane}), declared by a formal "
        "blockedBy relation — skipping"
    ) in out.splitlines(), out
    assert fake.blocker_reads == []


def test_a_released_epic_does_not_free_a_card_another_blocker_still_holds(capsys):
    """Every blocker is asked: the built-out epic releases, the ordinary card
    In Review still holds, and the held line names only that one."""
    fake = FakeLinear([_w_free()], [TONIGHT])
    card = _queued(blocked_by=((EPIC_BLOCKER, "In Progress"), (TIE, "In Review")))
    moved = _card_sweep(fake, [card])
    out = capsys.readouterr().out
    assert moved == [], out
    held = [line for line in _promotion_lines(out) if "is held by" in line]
    assert held == [
        f"promotion: {CARD} is held by {TIE} (In Review), declared by a formal "
        "blockedBy relation — skipping"
    ], out


def test_the_live_reread_asks_the_same_helper(capsys):
    """`live_promotion_refusal` releases what the gate releases, and holds an
    epic blocker with work left with the same reason."""
    fake = FakeLinear([], [TONIGHT])
    card = _queued()
    with patch.object(linear_ops, "gql", side_effect=fake.gql), \
            patch.object(reconcile, "_fetch_backlog_linear",
                         side_effect=lambda ids, **kw: [copy.deepcopy(card)]):
        _, why = reconcile.live_promotion_refusal(card, [FLEET_VERDICT])
    assert why is None, why

    reconcile.reset_sweep_cards()
    open_kid = _blocker(EPIC_BLOCKER, [_kid("DRE-1686", "Backlog"), _proof(ITS_PROOF)])
    fake = FakeLinear([], [open_kid])
    with patch.object(linear_ops, "gql", side_effect=fake.gql), \
            patch.object(reconcile, "_fetch_backlog_linear",
                         side_effect=lambda ids, **kw: [copy.deepcopy(card)]):
        _, why = reconcile.live_promotion_refusal(card, [FLEET_VERDICT])
    assert why is not None and "not built out" in why and "DRE-1686" in why, why
    assert EPIC_BLOCKER in why


def test_the_card_gate_writes_no_second_definition_of_built_out():
    """One reading of an epic blocker, shared by both card-gate reads, built on
    `_not_built_out` and `_open_children`."""
    source = Path(reconcile.__file__).read_text(encoding="utf-8")
    assert source.count("def _not_built_out") == 1
    helper = reconcile.card_relation_blockers
    body = inspect.getsource(helper)
    assert "_not_built_out(" in body and "_open_children(" in body
    assert "counts_against_cap" not in body and "buildable(" not in body
    for caller in (reconcile.promote_ready, reconcile.live_promotion_refusal):
        assert "card_relation_blockers(" in inspect.getsource(caller), caller.__name__


def test_the_standard_says_a_card_blocked_by_an_epic_is_released_at_build_done():
    text = (Path(__file__).resolve().parent.parent / "standards" / "card-quality.md"
            ).read_text(encoding="utf-8")
    start = text.index("**When the epic behind a blocker moves (DRE-6407).**")
    paragraph = " ".join(text[start:text.index("\n\n", start)].split())
    assert "A card blocked by an approved epic" in paragraph, paragraph
    assert "DRE-7774" in paragraph
