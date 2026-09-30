"""RED-first: the epic cap as data (DRE-5134).

The CEO capped the epics in motion at 15 on 2026-09-28. This card lands the
cap as data and the one module every later card of the epic reads:

  * `config/epic-cap.json` carries `cap` and `count_rollup_parents`, and
    `load()` refuses a file that does not say both, naming the path and key.
  * `scripts/epic_cap.py` holds the counting rule (`counts_against_cap`), the
    order of the line (`priority_rank`, `approval_time`, `queue_order`), the
    decision the gate makes (`decision`), the three paged reads the sweep and
    the gate make (`fleet_state`, `waiting_line`, `labeled_elsewhere`), the
    receipt bodies, and the `check` / `show` / `decide` CLI.

Nothing here reaches Linear: `linear_ops.gql` is faked, and the fake answers
each query off its own `first:` number, so the request counts are the ones
`linear_ops.gql_paged` really makes.

Run: cd bureau-pipeline && python3 -m pytest tests/test_epic_cap.py -v
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")

import epic_cap  # noqa: E402
import linear_ops  # noqa: E402

#: The yardstick `reconcile.EPIC_RECORD_PAGE` names: `backlog_children`, 100
#: cards × a 50-comment window, live since DRE-2929.
KNOWN_ANSWERED_WEIGHT = 100 * 50

IN_PROGRESS = "In Progress"
GREEN_LIGHT = "Green Light"


def _norm(query: str) -> str:
    return " ".join((query or "").split())


# --------------------------------------------------------------------------
# record builders, in the shape Linear answers
# --------------------------------------------------------------------------
def _child(identifier="DRE-1", title="bureau-pipeline: a card", *, grandchildren=0,
           state="Backlog"):
    return {
        "identifier": identifier,
        "title": title,
        "state": {"name": state},
        "children": {"nodes": [{"id": f"g{n}"} for n in range(grandchildren)]},
    }


def _history(*entries):
    """History entries, written in ASCENDING order — the order
    `history(last: 250)` returns them in (DRE-5034's fixture)."""
    return {"nodes": [{"createdAt": at, "toState": {"name": to}} for at, to in entries]}


def _epic(identifier="DRE-100", *, state=IN_PROGRESS, children=None, priority=3,
          created="2026-09-01T00:00:00.000Z", history=None, title=None):
    return {
        "identifier": identifier,
        "title": title or f"[EPIC] bureau-pipeline: {identifier}",
        "priority": priority,
        "createdAt": created,
        "state": {"name": state},
        "children": {"nodes": list(children if children is not None else [_child()])},
        "history": history if history is not None else _history(),
    }


def _approved(identifier, at, *, priority=3, created="2026-09-01T00:00:00.000Z"):
    """A waiting epic approved (moved to In Progress) at `at`."""
    return _epic(identifier, state=GREEN_LIGHT, priority=priority, created=created,
                 history=_history(("2026-09-01T01:00:00.000Z", "Planning"),
                                  ("2026-09-01T02:00:00.000Z", GREEN_LIGHT),
                                  (at, IN_PROGRESS),
                                  (at[:-5] + "5.000Z", GREEN_LIGHT)))


def _fleet(n_in_motion, waiting=(), cap=15):
    return {
        "cap": cap,
        "count_rollup_parents": False,
        "in_motion": [_epic(f"DRE-{1000 + n}") for n in range(n_in_motion)],
        "waiting": epic_cap.queue_order(list(waiting)),
    }


def _asked(identifier="DRE-900", *, priority=3, at="2026-09-20T10:00:00.000Z",
           child_states=("Backlog", "Backlog")):
    """The asked epic as `decide`'s single-issue read returns it."""
    record = _approved(identifier, at, priority=priority)
    record["state"] = {"name": IN_PROGRESS}
    record["children"] = {"nodes": [{"state": {"name": s}} for s in child_states]}
    return record


# --------------------------------------------------------------------------
# config/epic-cap.json and load()
# --------------------------------------------------------------------------
def test_the_file_carries_the_ceos_cap():
    with open(ROOT / "config" / "epic-cap.json", encoding="utf-8") as fh:
        doc = json.load(fh)
    assert doc["cap"] == 15
    assert doc["count_rollup_parents"] is False
    for key in ("decided", "decided_by", "why"):
        assert str(doc.get(key) or "").strip(), key


def test_the_path_is_resolved_off_the_scripts_own_directory():
    """A product-repo checkout finds it under `.bureau-pipeline/config/`."""
    here = Path(epic_cap.__file__).resolve().parent
    assert Path(epic_cap.CAP_PATH) == here.parent / "config" / "epic-cap.json"


def test_load_reads_the_real_file():
    doc = epic_cap.load()
    assert doc["cap"] == 15 and doc["count_rollup_parents"] is False


def _write(tmp_path, doc) -> str:
    path = tmp_path / "epic-cap.json"
    path.write_text(doc if isinstance(doc, str) else json.dumps(doc), encoding="utf-8")
    return str(path)


def test_load_names_the_path_on_a_missing_file(tmp_path):
    missing = str(tmp_path / "nowhere.json")
    with pytest.raises(epic_cap.EpicCapError, match=re.escape(missing)):
        epic_cap.load(missing)


def test_load_names_the_path_on_a_malformed_file(tmp_path):
    path = _write(tmp_path, "{not json")
    with pytest.raises(epic_cap.EpicCapError, match=re.escape(path)):
        epic_cap.load(path)


@pytest.mark.parametrize("doc,key", [
    ({"count_rollup_parents": False}, "cap"),
    ({"cap": 15}, "count_rollup_parents"),
    ({"cap": 0, "count_rollup_parents": False}, "cap"),
    ({"cap": -3, "count_rollup_parents": False}, "cap"),
    ({"cap": 15.0, "count_rollup_parents": False}, "cap"),
    ({"cap": "15", "count_rollup_parents": False}, "cap"),
    ({"cap": True, "count_rollup_parents": False}, "cap"),
    ({"cap": 15, "count_rollup_parents": "false"}, "count_rollup_parents"),
    ({"cap": 15, "count_rollup_parents": 0}, "count_rollup_parents"),
    ([15], "cap"),
])
def test_load_refuses_a_file_that_does_not_say_both_keys(tmp_path, doc, key):
    path = _write(tmp_path, doc)
    with pytest.raises(epic_cap.EpicCapError) as caught:
        epic_cap.load(path)
    assert path in str(caught.value) and key in str(caught.value)


def test_check_passes_on_the_real_file(capsys):
    assert epic_cap.main(["check"]) == 0
    assert "15" in capsys.readouterr().out


@pytest.mark.parametrize("doc,key", [
    ({"count_rollup_parents": False}, "cap"),
    ({"cap": 0, "count_rollup_parents": False}, "cap"),
    ({"cap": 1.5, "count_rollup_parents": False}, "cap"),
    ({"cap": 15, "count_rollup_parents": "no"}, "count_rollup_parents"),
])
def test_check_fails_naming_the_key(tmp_path, monkeypatch, capsys, doc, key):
    monkeypatch.setattr(epic_cap, "CAP_PATH", _write(tmp_path, doc))
    assert epic_cap.main(["check"]) == 2
    assert key in capsys.readouterr().err


# --------------------------------------------------------------------------
# the counting rule
# --------------------------------------------------------------------------
def test_an_in_progress_epic_with_a_plain_child_counts():
    assert epic_cap.counts_against_cap(_epic(children=[_child()]),
                                       count_rollup_parents=False)


def test_a_roll_up_parent_does_not_count():
    roll_up = _epic(children=[
        _child("DRE-2", "[EPIC] bureau-pipeline: part one"),
        _child("DRE-3", "bureau-pipeline: part two", grandchildren=1),
    ])
    assert not epic_cap.counts_against_cap(roll_up, count_rollup_parents=False)


def test_one_plain_child_under_a_roll_up_makes_it_count():
    mixed = _epic(children=[
        _child("DRE-2", "[EPIC] bureau-pipeline: part one"),
        _child("DRE-3", "bureau-pipeline: a card"),
    ])
    assert epic_cap.counts_against_cap(mixed, count_rollup_parents=False)


def test_a_green_light_epic_does_not_count():
    assert not epic_cap.counts_against_cap(_epic(state=GREEN_LIGHT),
                                           count_rollup_parents=False)


def test_an_in_progress_card_with_no_children_does_not_count():
    assert not epic_cap.counts_against_cap(_epic(children=[]),
                                           count_rollup_parents=False)


def test_with_count_rollup_parents_the_roll_up_counts():
    roll_up = _epic(children=[_child("DRE-2", "[EPIC] bureau-pipeline: part one")])
    assert epic_cap.counts_against_cap(roll_up, count_rollup_parents=True)


def test_the_flag_defaults_to_the_file():
    roll_up = _epic(children=[_child("DRE-2", "[EPIC] bureau-pipeline: part one")])
    assert not epic_cap.counts_against_cap(roll_up)


def test_a_child_is_an_epic_by_mid_epics_rule(monkeypatch):
    """Imported, never restated: a change to `mid_epic.is_epic` moves this."""
    seen = []

    def fake_is_epic(title, has_children, shape=None):
        seen.append((title, has_children))
        return True

    monkeypatch.setattr(epic_cap.mid_epic, "is_epic", fake_is_epic)
    assert not epic_cap.counts_against_cap(
        _epic(children=[_child("DRE-2", "plain", grandchildren=1)]),
        count_rollup_parents=False,
    )
    assert seen == [("plain", True)]


def test_in_motion_keeps_only_the_epics_that_count():
    epics = [
        _epic("DRE-1"),
        _epic("DRE-2", children=[]),
        _epic("DRE-3", state=GREEN_LIGHT),
        _epic("DRE-4", children=[_child("DRE-9", "[EPIC] x")]),
    ]
    got = epic_cap.in_motion(epics, count_rollup_parents=False)
    assert [e["identifier"] for e in got] == ["DRE-1"]


# --------------------------------------------------------------------------
# the order of the line
# --------------------------------------------------------------------------
def test_priority_rank_puts_none_after_low():
    ranks = [epic_cap.priority_rank(p) for p in (1, 2, 3, 4, 0, None)]
    assert ranks == sorted(ranks)
    assert epic_cap.priority_rank(0) > epic_cap.priority_rank(4)
    assert epic_cap.priority_rank(None) == epic_cap.priority_rank(0)


def test_approval_time_takes_the_older_of_two_approvals():
    epic = _epic(history=_history(
        ("2026-09-02T00:00:00.000Z", "Planning"),
        ("2026-09-03T00:00:00.000Z", GREEN_LIGHT),
        ("2026-09-04T00:00:00.000Z", IN_PROGRESS),
        ("2026-09-04T00:00:05.000Z", GREEN_LIGHT),
        ("2026-09-06T00:00:00.000Z", IN_PROGRESS),
    ))
    assert epic_cap.approval_time(epic) == "2026-09-04T00:00:00.000Z"


def test_approval_time_after_a_re_plan_is_the_new_approval():
    epic = _epic(history=_history(
        ("2026-09-04T00:00:00.000Z", IN_PROGRESS),
        ("2026-09-10T00:00:00.000Z", "Planning"),
        ("2026-09-11T00:00:00.000Z", GREEN_LIGHT),
        ("2026-09-12T00:00:00.000Z", IN_PROGRESS),
    ))
    assert epic_cap.approval_time(epic) == "2026-09-12T00:00:00.000Z"


def test_approval_time_falls_back_to_created_at():
    epic = _epic(created="2026-08-30T00:00:00.000Z", history=_history(
        ("2026-09-02T00:00:00.000Z", "Planning"),
        ("2026-09-03T00:00:00.000Z", GREEN_LIGHT),
    ))
    assert epic_cap.approval_time(epic) == "2026-08-30T00:00:00.000Z"
    assert epic_cap.approval_fell_back(epic)


def test_approval_time_falls_back_when_re_planned_and_not_yet_re_approved():
    epic = _epic(created="2026-08-30T00:00:00.000Z", history=_history(
        ("2026-09-04T00:00:00.000Z", IN_PROGRESS),
        ("2026-09-10T00:00:00.000Z", "Planning"),
    ))
    assert epic_cap.approval_time(epic) == "2026-08-30T00:00:00.000Z"


def test_queue_order_puts_high_before_medium_whatever_the_approval():
    medium_old = _approved("DRE-10", "2026-09-01T10:00:00.000Z", priority=3)
    high_new = _approved("DRE-11", "2026-09-20T10:00:00.000Z", priority=2)
    got = epic_cap.queue_order([medium_old, high_new])
    assert [e["identifier"] for e in got] == ["DRE-11", "DRE-10"]


def test_queue_order_puts_the_older_approval_first_at_equal_priority():
    newer = _approved("DRE-10", "2026-09-20T10:00:00.000Z")
    older = _approved("DRE-11", "2026-09-05T10:00:00.000Z")
    got = epic_cap.queue_order([newer, older])
    assert [e["identifier"] for e in got] == ["DRE-11", "DRE-10"]


def test_queue_order_puts_no_priority_after_low():
    none_old = _approved("DRE-10", "2026-09-01T10:00:00.000Z", priority=0)
    low_new = _approved("DRE-11", "2026-09-20T10:00:00.000Z", priority=4)
    got = epic_cap.queue_order([none_old, low_new])
    assert [e["identifier"] for e in got] == ["DRE-11", "DRE-10"]


def test_queue_order_breaks_a_full_tie_on_the_identifier():
    at = "2026-09-05T10:00:00.000Z"
    got = epic_cap.queue_order([_approved("DRE-100", at), _approved("DRE-99", at)])
    assert [e["identifier"] for e in got] == ["DRE-99", "DRE-100"]


def test_place_is_one_based_with_the_length_of_the_line():
    line = [
        _approved("DRE-10", "2026-09-20T10:00:00.000Z"),
        _approved("DRE-11", "2026-09-05T10:00:00.000Z", priority=1),
        _approved("DRE-12", "2026-09-06T10:00:00.000Z"),
    ]
    assert epic_cap.place("DRE-11", line) == (1, 3)
    assert epic_cap.place("DRE-10", line) == (3, 3)


def test_free_slots_never_goes_negative():
    assert epic_cap.free_slots(_fleet(14)) == 1
    assert epic_cap.free_slots(_fleet(15)) == 0
    assert epic_cap.free_slots(_fleet(45)) == 0


# --------------------------------------------------------------------------
# activated_before
# --------------------------------------------------------------------------
def _with_children(*states):
    return {"identifier": "DRE-900",
            "children": {"nodes": [{"state": {"name": s}} for s in states]}}


def test_a_done_child_among_backlog_ones_means_activated():
    assert epic_cap.activated_before(_with_children("Done", "Backlog", "Backlog"))


def test_a_child_in_review_means_activated():
    assert epic_cap.activated_before(_with_children("Backlog", "In Review"))


def test_all_backlog_children_mean_not_activated():
    assert not epic_cap.activated_before(_with_children("Backlog", "Backlog"))


def test_canceled_and_triage_children_do_not_mean_activated():
    assert not epic_cap.activated_before(_with_children("Backlog", "Canceled", "Triage"))


def test_the_activated_states_are_the_contract():
    assert epic_cap.ACTIVATED_STATES == ("Todo", "In Progress", "In Review", "Done")


# --------------------------------------------------------------------------
# decision
# --------------------------------------------------------------------------
def _decide(capsys, fleet, epic):
    answer = epic_cap.decision(fleet, epic["identifier"], epic)
    err = capsys.readouterr().err
    assert len([ln for ln in err.splitlines() if ln.strip()]) == 1, err
    return answer, err


def test_14_of_15_with_nobody_waiting_starts(capsys):
    answer, err = _decide(capsys, _fleet(14), _asked())
    assert answer == "start"
    assert "rule 4" in err


def test_15_of_15_queues(capsys):
    answer, err = _decide(capsys, _fleet(15), _asked())
    assert answer == "queue"
    assert "rule 2" in err and "15 of 15" in err


def test_45_of_15_queues(capsys):
    answer, err = _decide(capsys, _fleet(45), _asked())
    assert answer == "queue"
    assert "rule 2" in err and "45 of 15" in err


def test_an_epic_already_in_motion_is_not_counted_against_itself(capsys):
    fleet = _fleet(14)
    asked = _asked("DRE-900")
    fleet["in_motion"].append(_epic("DRE-900"))
    assert len(fleet["in_motion"]) == 15
    answer, err = _decide(capsys, fleet, asked)
    assert answer == "start"
    assert "rule 4" in err and "14 of 15" in err


def test_45_of_15_with_a_done_child_starts_on_the_amendment_exemption(capsys):
    answer, err = _decide(capsys, _fleet(45), _asked(child_states=("Done", "Backlog")))
    assert answer == "start"
    assert "rule 1" in err


def test_14_of_15_with_a_higher_priority_epic_waiting_queues(capsys):
    ahead = _approved("DRE-50", "2026-09-25T10:00:00.000Z", priority=2)
    answer, err = _decide(capsys, _fleet(14, [ahead]), _asked(priority=3))
    assert answer == "queue"
    assert "rule 3" in err and "DRE-50" in err


def test_14_of_15_with_a_lower_priority_epic_waiting_starts(capsys):
    behind = _approved("DRE-50", "2026-09-01T10:00:00.000Z", priority=4)
    answer, err = _decide(capsys, _fleet(14, [behind]), _asked(priority=3))
    assert answer == "start"
    assert "rule 4" in err


def test_14_of_15_with_an_older_equal_priority_epic_waiting_queues(capsys):
    older = _approved("DRE-50", "2026-09-01T10:00:00.000Z", priority=3)
    answer, err = _decide(capsys, _fleet(14, [older]),
                          _asked(priority=3, at="2026-09-20T10:00:00.000Z"))
    assert answer == "queue"
    assert "rule 3" in err and "DRE-50" in err


def test_rule_3_receipt_says_the_epic_ahead_takes_the_slot(capsys):
    ahead = _approved("DRE-50", "2026-09-25T10:00:00.000Z", priority=2)
    fleet = _fleet(14, [ahead])
    asked = _asked(priority=3)
    body = epic_cap.receipt_for(fleet, asked["identifier"], asked)
    capsys.readouterr()
    assert body.startswith("⏸️ epic-queued:")
    assert "DRE-50" in body and "slot is free" in body
    assert "place 2 of 2" in body


# --------------------------------------------------------------------------
# receipts
# --------------------------------------------------------------------------
def test_the_queued_receipt_says_everything_a_reader_needs():
    body = epic_cap.queued_receipt(3, 4, 15, 15)
    assert body.startswith("⏸️ epic-queued:")
    assert "place 3 of 4" in body
    assert "15 of 15 epics" in body
    assert "when one closes" in body
    assert "Urgent, High, Medium, Low" in body and "oldest approval first" in body
    assert "Change its Priority to move it up the line" in body
    assert "In Progress again neither starts it nor changes its place" in body
    assert epic_cap.QUEUED_ACT not in body  # the posting card adds the trailer


def test_the_rule_3_receipt_names_the_epic_ahead():
    body = epic_cap.queued_receipt(2, 2, 14, 15, ahead="DRE-50")
    assert body.startswith("⏸️ epic-queued:")
    assert "place 2 of 2" in body
    assert "DRE-50" in body and "slot is free" in body
    assert "starts it first" in body
    assert "14 of 15" not in body
    assert "In Progress again neither starts it nor changes its place" in body


def test_the_unread_receipt_names_the_reason():
    body = epic_cap.unread_receipt("linear error: HTTP 400 query too complex")
    assert body.startswith("⏸️ epic-queued:")
    assert "HTTP 400 query too complex" in body
    assert "could not be read" in body
    assert "next pass if there is room" in body
    assert "Priority and first approval decide its place" in body
    assert "In Progress again neither starts it nor changes its place" in body


def test_the_started_receipt():
    body = epic_cap.started_receipt(1, 15, 15)
    assert body.startswith("▶️ epic-started:")
    assert "15 of 15" in body


def test_the_contract_constants():
    assert epic_cap.QUEUED_LABEL == "epic-queued"
    assert epic_cap.QUEUED_TAG == "epic-queued"
    assert epic_cap.STARTED_TAG == "epic-started"
    assert epic_cap.REDISPATCHED_TAG == "epic-start-redispatched"
    assert epic_cap.QUEUED_ACT == "epic-approval-queued"
    assert epic_cap.STARTED_ACT == "epic-queue-started"
    assert epic_cap.REDISPATCHED_ACT == "epic-start-redispatched"
    assert epic_cap.IN_MOTION_PAGE == 8
    assert epic_cap.WAITING_PAGE == 16
    assert epic_cap.START_OWNER_SLUG == "bureau-pipeline"


# --------------------------------------------------------------------------
# the queries: shape and weight
# --------------------------------------------------------------------------
def _node_weight(selection: str) -> int:
    """One node's weight off the selection's OWN `first:`/`last:` numbers.

    The selections nest one connection inside the last, so each number
    multiplies the ones before it: `children(first: 250) { … children(first: 1)
    … }` is 250 children plus 250 × 1 grandchild probes."""
    weight, width = 0, 1
    for n in re.findall(r"\b(?:first|last):\s*(\d+)", selection):
        width *= int(n)
        weight += width
    return weight


@pytest.mark.parametrize("query", ["IN_MOTION_QUERY", "WAITING_QUERY", "LABELED_QUERY"])
def test_each_paged_query_declares_after_and_selects_page_info(query):
    q = _norm(getattr(epic_cap, query))
    assert "$after: String" in q and "after: $after" in q
    assert "pageInfo { hasNextPage endCursor }" in q


def test_the_in_motion_query_asks_for_its_page_and_its_fields():
    q = _norm(epic_cap.IN_MOTION_QUERY)
    assert f"issues(first: {epic_cap.IN_MOTION_PAGE}, after: $after," in q
    assert 'state: {name: {eq: "In Progress"}}' in q
    assert 'team: {key: {eq: "DRE"}}' in q
    for field in ("identifier title priority createdAt state { name }",
                  "children(first: 250) { nodes { identifier title "
                  "children(first: 1) { nodes { id } } } }"):
        assert field in q, field


def test_the_waiting_query_asks_for_its_page_and_its_fields():
    q = _norm(epic_cap.WAITING_QUERY)
    assert f"issues(first: {epic_cap.WAITING_PAGE}, after: $after," in q
    assert 'state: {name: {eq: "Green Light"}}' in q
    assert 'labels: {name: {eq: "epic-queued"}}' in q
    assert "history(last: 250) { nodes { createdAt toState { name } } }" in q


def test_an_in_motion_page_weighs_no_more_than_linear_answers():
    weight = _node_weight(epic_cap.IN_MOTION_NODE)
    assert weight == 500
    assert epic_cap.IN_MOTION_PAGE * weight <= KNOWN_ANSWERED_WEIGHT, (
        f"{epic_cap.IN_MOTION_PAGE} × {weight} = {epic_cap.IN_MOTION_PAGE * weight}"
    )


def test_a_waiting_page_weighs_no_more_than_linear_answers():
    weight = _node_weight(epic_cap.WAITING_NODE)
    assert weight == 250
    assert epic_cap.WAITING_PAGE * weight <= KNOWN_ANSWERED_WEIGHT, (
        f"{epic_cap.WAITING_PAGE} × {weight} = {epic_cap.WAITING_PAGE * weight}"
    )


def test_the_node_selections_are_the_ones_the_queries_carry():
    assert _norm(epic_cap.IN_MOTION_NODE) in _norm(epic_cap.IN_MOTION_QUERY)
    assert _norm(epic_cap.WAITING_NODE) in _norm(epic_cap.WAITING_QUERY)


def test_the_single_issue_read_selects_the_childrens_states():
    q = _norm(epic_cap.EPIC_QUERY)
    assert "issue(id: $id)" in q
    assert "priority createdAt" in q
    assert "history(last: 250) { nodes { createdAt toState { name } } }" in q
    assert "children(first: 250) { nodes { state { name } } }" in q


# --------------------------------------------------------------------------
# the reads, with linear_ops.gql faked
# --------------------------------------------------------------------------
class FakeLinear:
    """Serves each query off its own `first:` number, counting requests."""

    def __init__(self, *, in_progress=(), waiting=(), labeled=(), epics=(),
                 fail=None):
        self.in_progress = list(in_progress)
        self.waiting = list(waiting)
        self.labeled = list(labeled)
        self.epics = {e["identifier"]: e for e in epics}
        self.fail = fail
        self.queries: list[tuple[str, dict]] = []

    def kind(self, query: str) -> str:
        q = _norm(query)
        if "issue(id: $id)" in q:
            return "epic"
        if 'state: {name: {eq: "In Progress"}}' in q:
            return "in_motion"
        if 'state: {name: {eq: "Green Light"}}' in q:
            return "waiting"
        if "epic-queued" in q:
            return "labeled"
        raise AssertionError(f"unexpected query: {q}")

    def count(self, kind: str) -> int:
        return sum(1 for q, _ in self.queries if self.kind(q) == kind)

    def gql(self, query, variables=None):
        v = variables or {}
        self.queries.append((query, v))
        if self.fail:
            raise self.fail
        kind = self.kind(query)
        if kind == "epic":
            return {"issue": self.epics.get(v["id"])}
        rows = {"in_motion": self.in_progress, "waiting": self.waiting,
                "labeled": self.labeled}[kind]
        page = int(re.search(r"issues\(first:\s*(\d+)", query).group(1))
        start = int(v.get("after") or 0)
        chunk = rows[start:start + page]
        more = start + page < len(rows)
        return {"issues": {"nodes": chunk, "pageInfo": {
            "hasNextPage": more, "endCursor": str(start + page) if more else None}}}


@pytest.fixture
def board(monkeypatch):
    def install(**kw):
        fake = FakeLinear(**kw)
        monkeypatch.setattr(linear_ops, "gql", fake.gql)
        return fake
    return install


def _board_of_2026_09_28():
    """45 In Progress epics — 14 builds, 16 close-outs (whose children are all
    delivered and still count), 15 roll-ups — plus 10 cards being built."""
    epics = [_epic(f"DRE-{2000 + n}") for n in range(30)]
    epics += [_epic(f"DRE-{2100 + n}", children=[_child("DRE-1", "[EPIC] x")])
              for n in range(15)]
    cards = [_epic(f"DRE-{3000 + n}", children=[], title="bureau-pipeline: a card")
             for n in range(10)]
    return epics + cards


def test_fleet_state_pages_55_in_progress_issues_in_7_requests(board, capsys):
    waiting = [
        _approved("DRE-60", "2026-09-20T10:00:00.000Z", priority=3),
        _approved("DRE-61", "2026-09-21T10:00:00.000Z", priority=1),
        _approved("DRE-62", "2026-09-10T10:00:00.000Z", priority=3),
    ]
    fake = board(in_progress=_board_of_2026_09_28(), waiting=waiting)
    fleet = epic_cap.fleet_state()
    assert fake.count("in_motion") == 7
    assert fake.count("waiting") == 1
    assert fleet["cap"] == 15 and fleet["count_rollup_parents"] is False
    assert len(fleet["in_motion"]) == 30
    assert [e["identifier"] for e in fleet["waiting"]] == ["DRE-61", "DRE-62", "DRE-60"]
    err = capsys.readouterr().err
    assert "8 Linear request" in err
    assert "7" in err and "1" in err


def test_waiting_line_is_one_request_in_queue_order(board):
    fake = board(waiting=[
        _approved("DRE-60", "2026-09-20T10:00:00.000Z", priority=4),
        _approved("DRE-61", "2026-09-21T10:00:00.000Z", priority=2),
    ])
    line = epic_cap.waiting_line()
    assert len(fake.queries) == 1 and fake.count("waiting") == 1
    assert [e["identifier"] for e in line] == ["DRE-61", "DRE-60"]


def test_labeled_elsewhere_returns_only_the_ones_outside_green_light(board):
    fake = board(labeled=[
        {"identifier": "DRE-70", "state": {"name": GREEN_LIGHT}},
        {"identifier": "DRE-71", "state": {"name": "Planning"}},
        {"identifier": "DRE-72", "state": {"name": IN_PROGRESS}},
        {"identifier": "DRE-73", "state": {"name": "Backlog"}},
    ])
    got = epic_cap.labeled_elsewhere()
    assert [e["identifier"] for e in got] == ["DRE-71", "DRE-72", "DRE-73"]
    q = _norm(fake.queries[0][0])
    assert "issues(first: 100, after: $after," in q
    assert 'labels: {name: {eq: "epic-queued"}}' in q


# --------------------------------------------------------------------------
# the CLI
# --------------------------------------------------------------------------
def _fourteen_in_motion():
    return [_epic(f"DRE-{2000 + n}") for n in range(14)]


def test_decide_prints_start_and_exits_0(board, capsys):
    asked = _asked("DRE-900")
    fake = board(in_progress=_fourteen_in_motion(), epics=[asked])
    assert epic_cap.main(["decide", "--epic", "DRE-900"]) == 0
    out, err = capsys.readouterr()
    assert out == "start\n"
    assert "rule 4" in err
    epic_reads = [v for q, v in fake.queries if fake.kind(q) == "epic"]
    assert epic_reads == [{"id": "DRE-900"}]


def test_decide_prints_queue_and_writes_the_receipt(board, capsys, tmp_path):
    asked = _asked("DRE-900", priority=3, at="2026-09-20T10:00:00.000Z")
    waiting = [
        _approved("DRE-60", "2026-09-10T10:00:00.000Z", priority=3),
        _approved("DRE-61", "2026-09-25T10:00:00.000Z", priority=3),
        _approved("DRE-62", "2026-09-25T10:00:00.000Z", priority=1),
    ]
    in_motion = [_epic(f"DRE-{2000 + n}") for n in range(15)]
    board(in_progress=in_motion, waiting=waiting, epics=[asked])
    receipt = tmp_path / "receipt.md"
    assert epic_cap.main(["decide", "--epic", "DRE-900",
                          "--receipt-file", str(receipt)]) == 0
    out, err = capsys.readouterr()
    assert out == "queue\n"
    assert "rule 2" in err
    body = receipt.read_text(encoding="utf-8")
    assert body.startswith("⏸️ epic-queued:")
    assert "place 3 of 4" in body
    assert "15 of 15 epics" in body


def test_decide_writes_no_receipt_on_start(board, capsys, tmp_path):
    board(in_progress=_fourteen_in_motion(), epics=[_asked("DRE-900")])
    receipt = tmp_path / "receipt.md"
    assert epic_cap.main(["decide", "--epic", "DRE-900",
                          "--receipt-file", str(receipt)]) == 0
    assert capsys.readouterr().out == "start\n"
    assert not receipt.exists()


def test_decide_exits_2_when_the_cap_file_is_unreadable(board, monkeypatch, capsys,
                                                         tmp_path):
    missing = str(tmp_path / "nowhere.json")
    monkeypatch.setattr(epic_cap, "CAP_PATH", missing)
    fake = board(in_progress=_fourteen_in_motion(), epics=[_asked("DRE-900")])
    assert epic_cap.main(["decide", "--epic", "DRE-900"]) == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert missing in err
    assert fake.queries == []


def test_decide_exits_3_and_writes_the_unread_receipt(board, capsys, tmp_path):
    board(fail=linear_ops.LinearError("linear error: HTTP 400 query too complex"))
    receipt = tmp_path / "receipt.md"
    assert epic_cap.main(["decide", "--epic", "DRE-900",
                          "--receipt-file", str(receipt)]) == 3
    out, err = capsys.readouterr()
    assert out == ""
    assert "query too complex" in err
    body = receipt.read_text(encoding="utf-8")
    assert body == epic_cap.unread_receipt("linear error: HTTP 400 query too complex")


def test_decide_exits_3_and_writes_the_unread_receipt_when_the_key_is_missing(
        monkeypatch, capsys, tmp_path):
    # The real gql, as the gate runs it: inside Actions a missing key is a bare
    # KeyError from linear_ops.api_key(), not a LinearError.
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    receipt = tmp_path / "receipt.md"
    assert epic_cap.main(["decide", "--epic", "DRE-900",
                          "--receipt-file", str(receipt)]) == 3
    out, err = capsys.readouterr()
    assert out == ""
    assert "Linear could not be read" in err and "LINEAR_API_KEY" in err
    body = receipt.read_text(encoding="utf-8")
    assert body.startswith("⏸️ epic-queued:") and "LINEAR_API_KEY" in body


def test_decide_exits_3_when_linear_answers_with_something_not_json(board, capsys,
                                                                   tmp_path):
    board(fail=json.JSONDecodeError("Expecting value", "<html>", 0))
    receipt = tmp_path / "receipt.md"
    assert epic_cap.main(["decide", "--epic", "DRE-900",
                          "--receipt-file", str(receipt)]) == 3
    out, err = capsys.readouterr()
    assert out == "" and "Linear could not be read" in err
    assert receipt.read_text(encoding="utf-8").startswith("⏸️ epic-queued:")


def test_decide_still_surfaces_a_keyerror_that_is_not_the_missing_key(board):
    board(fail=KeyError("cap"))
    with pytest.raises(KeyError):
        epic_cap.main(["decide", "--epic", "DRE-900"])


def test_decide_says_the_receipt_could_not_be_written(board, capsys, tmp_path):
    board(fail=linear_ops.LinearError("linear error: HTTP 400 query too complex"))
    receipt = tmp_path / "no-such-dir" / "receipt.md"
    assert epic_cap.main(["decide", "--epic", "DRE-900",
                          "--receipt-file", str(receipt)]) == 3
    out, err = capsys.readouterr()
    assert out == ""
    assert "query too complex" in err
    assert "receipt could not be written" in err
    assert str(receipt) in err


def test_show_exits_3_when_the_key_is_missing(board, capsys):
    board(fail=KeyError("LINEAR_API_KEY"))
    assert epic_cap.main(["show"]) == 3
    out, err = capsys.readouterr()
    assert out == ""
    assert "Linear could not be read" in err and "LINEAR_API_KEY" in err


def test_decide_exits_3_when_the_asked_epic_is_not_found(board, capsys):
    board(in_progress=_fourteen_in_motion(), epics=[])
    assert epic_cap.main(["decide", "--epic", "DRE-900"]) == 3
    out, err = capsys.readouterr()
    assert out == "" and "DRE-900" in err


def test_show_prints_the_count_and_the_line(board, capsys):
    fell_back = _epic("DRE-61", state=GREEN_LIGHT, priority=2,
                      history=_history(("2026-09-02T00:00:00.000Z", "Planning")))
    board(in_progress=_fourteen_in_motion(), waiting=[
        _approved("DRE-60", "2026-09-20T10:00:00.000Z", priority=3),
        fell_back,
    ])
    assert epic_cap.main(["show"]) == 0
    out = capsys.readouterr().out
    assert "Epics in motion: 14 of 15" in out
    assert out.index("DRE-61") < out.index("DRE-60")
    marked = [ln for ln in out.splitlines()
              if "(approval time: no In Progress entry among the 250 oldest "
                 "history entries; using createdAt)" in ln]
    assert len(marked) == 1 and "DRE-61" in marked[0]
