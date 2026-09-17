"""RED-first: ONE read per pass for every active epic (DRE-3642).

THE MEASUREMENT. Read-only on 2026-09-12, on this repo's board: the sweep
bought one children read per active epic inside `close_finished_epics` (9), and
one relations read per epic with Backlog children inside the epic-level gate
(9, via `_fetch_epic_relations`). Both scale with the number of active epics,
and that number is what grew — ~20 per pass when DRE-3236 measured it, 65 here
and 92 on agent-bureau by 2026-09-12. The board read already carries every
epic; what it does not carry is the epic's children states, its relations and
its history, so each consumer bought its own read per epic.

WHAT IS UNDER TEST — `reconcile.epic_records(identifiers)`:
  * ONE paged read of the named epics, whatever the count, selecting the union
    of what every per-epic reader needs; cached for the pass and dropped by
    `reset_sweep_cards()`.
  * `_close_epic_if_finished` reads its children states off the record, and
    `_fetch_epic_relations` reads `identifier`, `description` and
    `inverseRelations` off it — so the close and the gate together cost ONE
    read for the whole board, not two per epic.
  * The per-epic isolation DRE-3148 and DRE-1772 built is UNCHANGED: an epic
    missing from the record — unreadable, or an identifier Linear did not
    answer — is skipped by the close and fail-safe blocked by the gate, both
    out loud, exactly as an unreadable read is today.
  * A batched read that RAISES falls back to the per-epic reads, once, and the
    sweep still completes. The one exception is a spent Linear quota: fanning
    a rate-limited batch out into nine per-epic retries cannot succeed and
    deepens the exhaustion (DRE-1921), so that one is not retried at all.

Run: cd bureau-pipeline && python3 -m pytest tests/test_epic_records.py -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")
os.environ.setdefault("GH_TOKEN", "x")

import linear_ops  # noqa: E402
import reconcile  # noqa: E402

AT = "2026-09-12T08:00:00.000Z"

#: Nine active epics — the count the 2026-09-12 measurement found, so the
#: assertions below are about the number that was actually being paid.
NINE = [f"DRE-{700 + n}" for n in range(9)]


def _record(
    identifier: str,
    *,
    children: tuple[str, ...] = (),
    description: str = "",
    blocked_by: tuple[tuple[str, str], ...] = (),
    state: str = "In Progress",
) -> dict:
    """One epic record in the contract's exact shape."""
    return {
        "identifier": identifier,
        "description": description,
        "state": {"name": state},
        "children": {"nodes": [
            {"identifier": f"{identifier}-kid-{n}", "createdAt": AT,
             "state": {"name": s}}
            for n, s in enumerate(children)
        ]},
        "history": {"nodes": [{"createdAt": AT, "toState": {"name": "In Progress"}}]},
        "inverseRelations": {"nodes": [
            {"type": "blocks", "issue": {"identifier": i, "state": {"name": s}}}
            for i, s in blocked_by
        ]},
    }


class FakeLinear:
    """The two query shapes `epic_records` makes, and nothing else.

    `batch_error` fails the paged read the way Linear hiccupping does;
    `per_epic_error` maps an identifier to the error its single-issue read
    raises, the way DRE-3148's timeout did.
    """

    def __init__(self, records, *, batch_error=None, per_epic_error=None):
        self.records = {r["identifier"]: r for r in records}
        self.batch_error = batch_error
        self.per_epic_error = dict(per_epic_error or {})
        self.queries: list[tuple[str, dict]] = []

    @property
    def requests(self) -> int:
        return len(self.queries)

    @property
    def batched(self) -> int:
        return sum(1 for q, _ in self.queries if "$numbers" in q)

    @property
    def per_epic(self) -> int:
        return sum(1 for q, _ in self.queries if "issue(id: $id)" in q)

    def gql(self, query, variables=None):
        v = variables or {}
        self.queries.append((query, v))
        if "$numbers" in query:
            if self.batch_error is not None:
                raise self.batch_error
            wanted = {int(n) for n in v.get("numbers") or ()}
            nodes = [
                r for ident, r in self.records.items()
                if int(ident.split("-")[1]) in wanted
            ]
            return {"issues": {
                "nodes": nodes,
                "pageInfo": {"hasNextPage": False, "endCursor": None},
            }}
        ident = v.get("id")
        if ident in self.per_epic_error:
            raise self.per_epic_error[ident]
        return {"issue": self.records.get(ident)}


@pytest.fixture(autouse=True)
def _fresh_pass():
    """The record is a PASS cache: in production one process is one sweep, in a
    test session one process is hundreds. (`tests/conftest.py` resets it around
    every test too; this says so where the reader is.)"""
    reconcile.reset_sweep_cards()
    yield
    reconcile.reset_sweep_cards()


# --------------------------------------------------------------------------
# 1: one read, for every epic the pass asks about
# --------------------------------------------------------------------------
def test_nine_epics_cost_one_paged_read():
    """The whole point: the count of reads does not follow the count of epics."""
    fake = FakeLinear([_record(i) for i in NINE])
    with patch.object(linear_ops, "gql", side_effect=fake.gql):
        records = reconcile.epic_records(NINE)
    assert sorted(records) == sorted(NINE)
    assert fake.batched == 1, [q for q, _ in fake.queries]
    assert fake.requests == 1


def test_the_record_carries_exactly_the_contracted_fields():
    """The contract the growth sibling reads. Exactly these keys — a reader
    that needs a field nobody selected reads `None` and decides on it."""
    fake = FakeLinear([_record("DRE-700", children=("Done",))])
    with patch.object(linear_ops, "gql", side_effect=fake.gql):
        record = reconcile.epic_records(["DRE-700"])["DRE-700"]
    assert set(record) == {
        "identifier", "description", "state", "children", "history",
        "inverseRelations",
    }
    selection = " ".join(reconcile.EPIC_RECORD_GQL.split())
    for field in (
        "identifier", "description", "state { name }",
        "children(first: 250) { nodes { identifier createdAt state { name } } }",
        "history(last: 50) { nodes { createdAt toState { name } } }",
        "inverseRelations(first: 20)",
    ):
        assert field in selection, selection


def test_the_second_ask_is_served_from_the_pass_and_the_reset_drops_it():
    """Cached for the pass — and only for the pass: a sweep that inherited the
    last one's epics would close an epic off a board an hour old."""
    fake = FakeLinear([_record(i) for i in NINE])
    with patch.object(linear_ops, "gql", side_effect=fake.gql):
        reconcile.epic_records(NINE)
        reconcile.epic_records(NINE)
        reconcile.epic_records(NINE[:3])
        assert fake.batched == 1
        reconcile.reset_sweep_cards()
        reconcile.epic_records(NINE)
    assert fake.batched == 2


def test_only_the_epics_not_already_cached_are_read():
    """A second ask that names new epics reads the NEW ones, not the board."""
    fake = FakeLinear([_record(i) for i in NINE])
    with patch.object(linear_ops, "gql", side_effect=fake.gql):
        reconcile.epic_records(NINE[:4])
        reconcile.epic_records(NINE)
    assert fake.batched == 2
    assert sorted(fake.queries[-1][1]["numbers"]) == [
        int(i.split("-")[1]) for i in NINE[4:]
    ]


def test_an_identifier_linear_did_not_answer_is_absent_never_empty():
    """Absent, never present as an empty record: a record with no children
    reads as a childless epic, and a childless epic is a different fact from
    an epic Linear did not answer for."""
    fake = FakeLinear([_record("DRE-700")])
    with patch.object(linear_ops, "gql", side_effect=fake.gql):
        records = reconcile.epic_records(["DRE-700", "DRE-701"])
    assert "DRE-701" not in records
    assert list(records) == ["DRE-700"]
    assert reconcile.epic_record_gap("DRE-701")


# --------------------------------------------------------------------------
# 2: the close reads the record
# --------------------------------------------------------------------------
def test_the_close_reads_children_states_off_the_record():
    """Nine epics, one read, and the finished one still closes."""
    fake = FakeLinear([
        _record(i, children=("Done", "In Progress")) for i in NINE[1:]
    ] + [_record(NINE[0], children=("Done", "Done"))])
    with patch.object(linear_ops, "gql", side_effect=fake.gql), \
            patch.object(linear_ops, "cmd_state") as state, \
            patch.object(linear_ops, "cmd_comment"), \
            patch.object(reconcile, "advance_unblocked_epics"):
        reconcile.close_finished_epics(set(NINE))
    assert fake.requests == 1, [q for q, _ in fake.queries]
    state.assert_called_once_with(NINE[0], "Done")


def test_the_close_skips_an_epic_absent_from_the_record_out_loud(capsys):
    """DRE-3148's isolation, unchanged: the epic Linear did not answer for is
    named in the log and skipped this sweep; every other epic is closed."""
    fake = FakeLinear([_record("DRE-701", children=("Done", "Done"))])
    with patch.object(linear_ops, "gql", side_effect=fake.gql), \
            patch.object(linear_ops, "cmd_state") as state, \
            patch.object(linear_ops, "cmd_comment"), \
            patch.object(reconcile, "advance_unblocked_epics"):
        reconcile.close_finished_epics({"DRE-700", "DRE-701"})
    err = capsys.readouterr().err
    assert "epic-close: could not close DRE-700" in err
    assert "skipped this sweep" in err
    assert "DRE-701" not in err
    state.assert_called_once_with("DRE-701", "Done")


# --------------------------------------------------------------------------
# 3: the epic gate reads the record
# --------------------------------------------------------------------------
def test_the_gate_reads_relations_off_the_record():
    """The epic-level gate's own read (DRE-1772), off the same record: an epic
    held by a formal `blockedBy` relation is still held, for no read of its
    own."""
    fake = FakeLinear([
        _record("DRE-700"),
        _record("DRE-701", blocked_by=(("DRE-700", "In Progress"),)),
    ])
    with patch.object(linear_ops, "gql", side_effect=fake.gql):
        reconcile.epic_records(["DRE-700", "DRE-701"])
        assert reconcile.epic_blockers_unmet("DRE-701") is True
        assert reconcile.epic_blockers_unmet("DRE-700") is False
    assert fake.requests == 1, [q for q, _ in fake.queries]


def test_the_close_and_the_gate_share_one_read():
    """The card's arithmetic: two consumers, nine epics, one request between
    them — where the pass used to pay eighteen."""
    fake = FakeLinear([_record(i, children=("In Progress",)) for i in NINE])
    with patch.object(linear_ops, "gql", side_effect=fake.gql), \
            patch.object(linear_ops, "cmd_state"), \
            patch.object(linear_ops, "cmd_comment"):
        reconcile.close_finished_epics(set(NINE))
        for epic in NINE:
            assert reconcile.epic_blockers_unmet(epic) is False
    assert fake.requests == 1, [q for q, _ in fake.queries]


def test_the_gate_blocks_an_epic_absent_from_the_record_out_loud(capsys):
    """DRE-1772's fail-safe, unchanged: an epic whose relations cannot be read
    holds its children, and says which epic and why."""
    fake = FakeLinear([_record("DRE-701")])
    with patch.object(linear_ops, "gql", side_effect=fake.gql):
        assert reconcile.epic_blockers_unmet("DRE-700") is True
    assert "epic-gate: could not read relations for DRE-700" in capsys.readouterr().out


# --------------------------------------------------------------------------
# 4: the batch that fails
# --------------------------------------------------------------------------
def test_a_batched_read_that_raises_falls_back_to_the_per_epic_reads(capsys):
    """A Linear hiccup on the batch must never silently block every epic: the
    pass pays the old per-epic price for this one sweep and decides exactly
    what it would have decided."""
    fake = FakeLinear(
        [_record(i, children=("Done", "Done")) for i in NINE],
        batch_error=TimeoutError("The read operation timed out"),
    )
    with patch.object(linear_ops, "gql", side_effect=fake.gql), \
            patch.object(linear_ops, "cmd_state") as state, \
            patch.object(linear_ops, "cmd_comment"), \
            patch.object(reconcile, "advance_unblocked_epics"):
        reconcile.close_finished_epics(set(NINE))
    assert fake.batched == 1
    assert fake.per_epic == len(NINE)
    assert sorted(c.args[0] for c in state.call_args_list) == sorted(NINE)
    assert "The read operation timed out" in capsys.readouterr().err


def test_the_fallback_is_paid_once_per_pass():
    """Once. The second consumer reads the records the fallback filled, and
    the epic the fallback could not read is not asked for again either."""
    fake = FakeLinear(
        [_record(i) for i in NINE[1:]],
        batch_error=ValueError("Linear said something unparseable"),
    )
    with patch.object(linear_ops, "gql", side_effect=fake.gql):
        reconcile.epic_records(NINE)
        before = fake.requests
        reconcile.epic_records(NINE)
    assert fake.requests == before
    assert fake.per_epic == len(NINE)


def test_one_epic_failing_its_fallback_read_does_not_cost_the_others():
    """The fallback keeps DRE-3148's isolation too: one timing-out epic is a
    gap in the record, not a dead phase."""
    fake = FakeLinear(
        [_record(i, children=("Done",)) for i in NINE],
        batch_error=TimeoutError("the batch timed out"),
        per_epic_error={NINE[0]: TimeoutError("The read operation timed out")},
    )
    with patch.object(linear_ops, "gql", side_effect=fake.gql):
        records = reconcile.epic_records(NINE)
    assert NINE[0] not in records
    assert sorted(records) == sorted(NINE[1:])
    assert "The read operation timed out" in reconcile.epic_record_gap(NINE[0])


def test_a_rate_limited_batch_is_not_retried_at_all():
    """The ONE error a retry must not follow (DRE-1921): a spent quota cannot
    answer nine more requests, and asking deepens the exhaustion. Every epic is
    a gap, the sweep's fail-safes hold, and the quota is left alone."""
    fake = FakeLinear(
        [_record(i) for i in NINE],
        batch_error=linear_ops.LinearRateLimited("rate limited: 2500 requests/hour"),
    )
    with patch.object(linear_ops, "gql", side_effect=fake.gql):
        records = reconcile.epic_records(NINE)
    assert records == {}
    assert fake.per_epic == 0
    assert "rate limited" in reconcile.epic_record_gap(NINE[0])
