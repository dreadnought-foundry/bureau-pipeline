"""RED-first: the growth report and the green light read the pass's batched
epic record (DRE-3644 — the reconcile half of DRE-3643 that never landed).

THE MEASUREMENT. The live scheduled sweep of 2026-09-20 09:33 PT (run
35523090077) spent 56 Linear reads against a target of 30: `report_epic_growth`
26 of them — 13 active epics × (one `mid_epic._EPIC_QUERY` read + one
`comment_count` read) — and `promote_ready` 23. DRE-3643 shipped the `issue=`
seam in `mid_epic` (PR #414) so a caller holding the epic's record reads it for
free; DRE-3642's `epic_records()` already reads every one of those epics in ONE
paged request per pass. Nothing in `reconcile.py` passed the one to the other.

WHAT IS UNDER TEST:
  * `EPIC_RECORD_GQL` carries the epic's UUID and its first comment page, so
    the growth record and the comment count read off it.
  * A page of records stays inside the weight Linear is known to answer:
    `EPIC_RECORD_PAGE` × one record's node weight — computed off the query's
    own `first:`/`last:` numbers — is at or under `backlog_children`'s 100
    cards × a 50-comment window = 5,000 nodes.
  * `report_epic_growth` reads the batched record: no epic is read alone for
    its growth, and an epic under 250 comments costs no request for its count.
    One past its first page costs its count's pages and nothing else, so the
    near-cap warning (DRE-3343) still fires.
  * An epic the batch did not answer is read alone, as before, and the report
    says so on one line.
  * Every `mid_epic.last_green_light` call in the sweep passes the record —
    the per-epic green light inside `promote_ready`. A miss passes `None` and
    reads as it always has.

Run: cd bureau-pipeline && python3 -m pytest tests/test_growth_rides_the_record.py -v
"""
from __future__ import annotations

import math
import os
import re
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import linear_ops  # noqa: E402
import mid_epic  # noqa: E402
import reconcile  # noqa: E402

from test_sweep_request_cuts import (  # noqa: E402
    REPO_LABEL, _card, _epic, _parent_ref,
)

GREEN_LIGHT = "2026-09-10T08:00:00.000Z"
BEFORE = "2026-09-09T08:00:00.000Z"

#: The yardstick `EPIC_RECORD_PAGE`'s own comment names: `backlog_children`,
#: 100 cards × a 50-comment window, live since DRE-2929.
KNOWN_ANSWERED_WEIGHT = 100 * 50

#: One Linear page of the comment count (`linear_ops._COMMENT_COUNT_QUERY`).
COUNT_PAGE = 250


def _norm(query: str) -> str:
    return " ".join((query or "").split())


def _is_single_epic_read(query: str) -> bool:
    """`mid_epic._EPIC_QUERY` — the epic read alone, the read this card cuts.
    Told apart from the record's own per-epic fallback by the relations that
    only the record selects."""
    q = _norm(query)
    return (
        "issue(id: $id)" in q and "history(first: 50)" in q
        and "children(first: 250)" in q and "inverseRelations" not in q
    )


def _record(identifier: str, *, comments: int = 3, uuid: bool = True,
            children=(("DRE-2701", BEFORE),)) -> dict:
    """One epic in `EPIC_RECORD_GQL`'s shape, first comment page included."""
    record = {
        "identifier": identifier,
        "description": "",
        "state": {"name": "In Progress"},
        "children": {"nodes": [
            {"identifier": i, "createdAt": at, "state": {"name": "Todo"}}
            for i, at in children
        ]},
        "history": {"nodes": [
            {"createdAt": GREEN_LIGHT, "toState": {"name": "In Progress"}}
        ]},
        "inverseRelations": {"nodes": []},
        "comments": {
            "nodes": [{"id": f"c{n}"} for n in range(min(comments, COUNT_PAGE))],
            "pageInfo": {
                "hasNextPage": comments > COUNT_PAGE,
                "endCursor": f"c{min(comments, COUNT_PAGE) - 1}" if comments else None,
            },
        },
    }
    if uuid:
        record["id"] = f"uuid-{identifier}"
    return record


class FakeLinear:
    """The reads the growth report and the green light can make, counted.

    `unanswered` names epics the batch leaves out — Linear answering for some
    of the numbers and not others — while a single read of one still answers.
    """

    def __init__(self, records, *, unanswered=()):
        self.records = {r["identifier"]: r for r in records}
        self.unanswered = set(unanswered)
        self.queries: list[tuple[str, dict]] = []

    def of(self, predicate) -> list[tuple[str, dict]]:
        return [(q, v) for q, v in self.queries if predicate(_norm(q))]

    @property
    def single_epic_reads(self) -> list[str]:
        return [v.get("id") for q, v in self.queries if _is_single_epic_read(q)]

    @property
    def count_pages(self) -> int:
        return len(self.of(lambda q: "comments(filter" in q))

    def gql(self, query, variables=None):
        v = variables or {}
        self.queries.append((query, v))
        q = _norm(query)
        if "$numbers" in q:
            wanted = {int(n) for n in v.get("numbers") or ()}
            nodes = [
                r for ident, r in self.records.items()
                if int(ident.split("-")[1]) in wanted and ident not in self.unanswered
            ]
            return {"issues": {"nodes": nodes,
                               "pageInfo": {"hasNextPage": False, "endCursor": None}}}
        if "comments(filter" in q:
            uuid = v["id"]
            record = next(r for r in self.records.values() if r.get("id") == uuid)
            total = record["_total"]
            start = int(v.get("after") or 0)
            chunk = range(start, min(start + COUNT_PAGE, total))
            more = start + COUNT_PAGE < total
            return {"comments": {
                "nodes": [{"id": f"c{n}"} for n in chunk],
                "pageInfo": {"hasNextPage": more,
                             "endCursor": str(start + COUNT_PAGE) if more else None},
            }}
        if "issue(id: $id)" in q:
            return {"issue": self.records.get(v.get("id"))}
        raise AssertionError(f"unexpected Linear query: {q[:120]}")


def _epic_with_total(identifier: str, total: int) -> dict:
    record = _record(identifier, comments=total)
    record["_total"] = total
    return record


def _growth(fake: FakeLinear, epics) -> list:
    with patch.object(linear_ops, "gql", side_effect=fake.gql), \
            patch.object(linear_ops, "set_description"), \
            patch.object(linear_ops, "cmd_comment"), \
            patch.object(linear_ops, "count_comments", return_value=1):
        return reconcile.report_epic_growth(set(epics))


# --------------------------------------------------------------------------
# 1: the record carries what the growth reader needs
# --------------------------------------------------------------------------
def test_the_record_selects_the_uuid_and_the_first_comment_page():
    q = _norm(reconcile.EPIC_RECORD_GQL)
    assert q.startswith("id identifier "), q
    assert (
        "comments(first: 250) { nodes { id } pageInfo { hasNextPage endCursor } }"
        in q
    ), q
    # the superset, not a replacement: every existing reader's fields remain
    for field in ("description", "state { name }",
                  "children(first: 250) { nodes { identifier createdAt state { name } } }",
                  "history(first: 50) { nodes { createdAt toState { name } } }",
                  "inverseRelations(first: 20)"):
        assert field in q, field


# --------------------------------------------------------------------------
# 2: a page stays inside the weight Linear is known to answer
# --------------------------------------------------------------------------
def _record_weight() -> int:
    """One record's node weight, off the query's OWN `first:`/`last:` numbers —
    never restated, so a connection widened later raises it here."""
    return sum(
        int(n) for n in re.findall(r"\b(?:first|last):\s*(\d+)", reconcile.EPIC_RECORD_GQL)
    )


def test_a_page_of_records_weighs_no_more_than_the_query_linear_answers():
    weight = _record_weight()
    assert weight > 0
    assert reconcile.EPIC_RECORD_PAGE >= 1
    assert reconcile.EPIC_RECORD_PAGE * weight <= KNOWN_ANSWERED_WEIGHT, (
        f"{reconcile.EPIC_RECORD_PAGE} records a page × {weight} nodes a record = "
        f"{reconcile.EPIC_RECORD_PAGE * weight}, over the {KNOWN_ANSWERED_WEIGHT} "
        "nodes backlog_children is known to be answered at"
    )


def test_the_batched_read_asks_for_that_page_size():
    """The constant is what the query asks for, not a number beside it."""
    assert f"issues(first: {reconcile.EPIC_RECORD_PAGE}," in _norm(
        reconcile._EPIC_RECORDS_QUERY
    )


# --------------------------------------------------------------------------
# 3: the growth report reads the record
# --------------------------------------------------------------------------
def test_the_growth_report_reads_no_epic_alone_when_the_batch_answers():
    epics = [f"DRE-{900 + n}" for n in range(5)]
    fake = FakeLinear([_epic_with_total(e, 12) for e in epics])
    assert _growth(fake, epics) == []
    assert fake.single_epic_reads == [], "an epic was read alone for its growth"
    assert fake.count_pages == 0, "a count the first page already answered was re-read"
    assert len(fake.queries) == 1, [(_norm(q)[:80]) for q, _ in fake.queries]


def test_the_growth_report_reads_nothing_for_epics_already_in_the_pass_record():
    """On a full sweep the close has already read them: the report spends 0."""
    epics = ["DRE-901", "DRE-902"]
    fake = FakeLinear([_epic_with_total(e, 12) for e in epics])
    with patch.object(linear_ops, "gql", side_effect=fake.gql):
        reconcile.epic_records(epics)
    before = len(fake.queries)
    _growth(fake, epics)
    assert len(fake.queries) == before


def test_an_epic_past_its_first_comment_page_costs_only_its_count_pages():
    """The near-cap epic is the one the warning exists for: its count is paged
    (`linear_ops.comment_count`, DRE-3343), and that is ALL it costs."""
    fake = FakeLinear([_epic_with_total("DRE-910", 1_850),
                       _epic_with_total("DRE-911", 12)])
    near = _growth(fake, ["DRE-910", "DRE-911"])
    assert near == [("DRE-910", 1_850)]
    assert fake.single_epic_reads == []
    assert fake.count_pages == math.ceil(1_850 / COUNT_PAGE)
    assert len(fake.queries) == 1 + fake.count_pages, (
        "the batch and the count's own pages, nothing else"
    )


def test_an_epic_the_batch_did_not_answer_is_read_alone_and_says_so(capsys):
    fake = FakeLinear([_epic_with_total("DRE-920", 12), _epic_with_total("DRE-921", 12)],
                      unanswered={"DRE-921"})
    _growth(fake, ["DRE-920", "DRE-921"])
    assert fake.single_epic_reads == ["DRE-921"]
    out = capsys.readouterr().out
    line = f"epic-growth: DRE-921 read alone — {reconcile.epic_record_gap('DRE-921')}"
    assert line in out, out
    assert "DRE-920 read alone" not in out
    # and it is still reported, off the read it made
    assert mid_epic.growth_line("DRE-921", 1, 1) in out


def test_a_batch_that_cannot_be_read_at_all_never_fails_the_growth_report(capsys):
    """A KPI read never fails the sweep (DRE-2739): with the batch gone, every
    epic is read alone, as before this card."""
    fake = FakeLinear([_epic_with_total("DRE-930", 12)])
    with patch.object(reconcile, "epic_records", side_effect=RuntimeError("down")):
        _growth(fake, ["DRE-930"])
    assert fake.single_epic_reads == ["DRE-930"]
    assert "epic-growth: DRE-930 read alone — " in capsys.readouterr().out


# --------------------------------------------------------------------------
# 4: the green light reads the record
# --------------------------------------------------------------------------
class _GreenLightSpy:
    """`mid_epic.last_green_light` itself, called through: what each call was
    handed is recorded, and the answer is the real one."""

    def __init__(self):
        self.calls: list[tuple[str, dict | None]] = []
        self.real = mid_epic.last_green_light

    def __call__(self, ops, epic, *, issue=None):
        self.calls.append((epic, issue))
        return self.real(ops, epic, issue=issue)


class BoardLinear(FakeLinear):
    """The cards `promote_ready` is handed, plus the epic record for each."""

    def __init__(self, cards, **kw):
        self.cards = {c["identifier"]: c for c in cards}
        records = [_record(c["identifier"]) for c in cards]
        super().__init__(records, **kw)


def _promote(fake, candidates, spy) -> None:
    # The slug the borrowed card fixture labels its cards with.
    with patch.object(reconcile, "REPO_SLUG", REPO_LABEL.split(":", 1)[1]), \
            patch.object(linear_ops, "gql", side_effect=fake.gql), \
            patch.object(reconcile.mid_epic, "last_green_light", spy), \
            patch.object(reconcile, "epic_thread", return_value=[]), \
            patch.object(reconcile, "_surface_once"), \
            patch.object(linear_ops, "cmd_comment"), \
            patch.object(linear_ops, "cmd_advance"), \
            patch.object(linear_ops, "add_label"):
        reconcile.promote_ready(0, candidates=candidates)


def test_the_gates_green_light_reads_the_record():
    epic = _epic("DRE-940")
    child = _card("DRE-941", parent=_parent_ref(epic))
    fake = BoardLinear([epic])
    spy = _GreenLightSpy()
    _promote(fake, [child], spy)
    assert ("DRE-940", fake.records["DRE-940"]) in spy.calls, spy.calls
    assert fake.single_epic_reads == []
