"""Reconcile reading through the read door (Stage 2 BP-2, #6a and the reconcile side of #15).

Every scenario in the Stage 2 scenarios document's Reconcile table (§1c,
R1–R16) is a test here, run against a REAL HTTP fake door
(`tests/bureau_read_fakes.py`) and a fake Linear that holds the truth "now".
The two worlds differ on purpose: the door is a snapshot, Linear is live, and
what is under test is which one each decision trusts.

The bar each scenario holds:
  * a promotion decided on any door fact re-reads the card's whole predicate
    live first, and is refused when the live card no longer passes (items 32,
    37, 45) — a relation added after the poll, a reopened blocker, a hold label,
    an epic sent back;
  * a state write decided on door data is from-lane-conditional (item 33);
  * a door that cannot answer whole falls back to Linear, except
    `linear-hold`, which skips (item 34);
  * Intake and Planning stay on Linear (6a);
  * the mode is read once (item 35); shadow compares and decides on Linear
    (item 36);
  * `off` is byte-identical to the sweep before the door existed (E-O7).
"""
from __future__ import annotations

import contextlib
import copy
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/portico")
os.environ.setdefault("REPO_SLUG", "portico")

import bureau_read  # noqa: E402
import epic_todo_gate  # noqa: E402
import linear_ops  # noqa: E402
import merge_sweep_gate  # noqa: E402
import mid_epic  # noqa: E402
import plan_critic  # noqa: E402
import reconcile  # noqa: E402
import routing_verdict  # noqa: E402
from bureau_read_fakes import FakeDoor, FakeIssuer, card, door_env  # noqa: E402

FLEET = routing_verdict.verdict_comment("FLEET", "the acceptance criteria are unit-testable")
REPO_LABEL = "repo:portico"
_ACTIVE_FIELDS = ("id", "identifier", "title", "description", "updatedAt", "priority",
                  "state", "labels", "children", "comments")


def _c(ident, lane, **kw):
    kw.setdefault("labels", (REPO_LABEL,))
    kw.setdefault("comments", (FLEET,))
    return card(ident, lane, **kw)


class Linear:
    """Linear as it is NOW. Reads served off `cards`; writes applied to them
    with the same guards the real seam has, so a refused write is visible."""

    def __init__(self, *cards):
        self.cards = {c["identifier"]: copy.deepcopy(c) for c in cards}
        self.queries: list[str] = []
        self.writes: list[tuple] = []
        self.refused: list[tuple] = []
        self.comments: list[tuple] = []
        self.labels: list[tuple] = []

    # reads ------------------------------------------------------------------
    def gql(self, query, variables=None):
        self.queries.append(query)
        q = " ".join(query.split())
        v = variables or {}
        if "state: {name: {in: $states}}" in q:
            nodes = [{k: copy.deepcopy(c[k]) for k in _ACTIVE_FIELDS}
                     for c in self.cards.values() if c["state"]["name"] in v["states"]]
            return {"issues": {"nodes": nodes,
                               "pageInfo": {"hasNextPage": False, "endCursor": None}}}
        if 'state: {name: {eq: "Backlog"}}' in q:
            numbers = v.get("numbers")
            nodes = [copy.deepcopy(c) for c in self.cards.values()
                     if c["state"]["name"] == "Backlog"
                     and (numbers is None or int(c["identifier"].split("-")[1]) in numbers)]
            return {"issues": {"nodes": nodes,
                               "pageInfo": {"hasNextPage": False, "endCursor": None}}}
        if "relatedIssue" in q:  # merge_sweep_gate.QUERY
            return {"issue": FakeDoor(self.cards).dependents_node(v["id"])}
        raise AssertionError(f"unexpected Linear query: {q[:160]}")

    def get_issue(self, ident, *, fresh=False):
        self.queries.append(f"get_issue {ident}")
        c = self.cards[ident]
        return {"identifier": ident, "state": dict(c["state"]),
                "labels": copy.deepcopy(c["labels"])}

    @property
    def requests(self) -> int:
        return len(self.queries)

    # writes -----------------------------------------------------------------
    def cmd_state(self, ident, lane, *flags, expect=None, labels_absent=()):
        c = self.cards[ident]
        names = {n["name"].lower() for n in c["labels"]["nodes"]}
        if (expect is not None and c["state"]["name"] not in expect) or (
                names & {x.lower() for x in labels_absent}):
            self.refused.append((ident, lane, expect, tuple(labels_absent)))
            return False
        self.writes.append(("state", ident, lane, flags, expect, tuple(labels_absent)))
        c["state"] = {"name": lane}
        return True

    def cmd_advance(self, ident, to, from_csv, *flags):
        c = self.cards[ident]
        if c["state"]["name"] not in [s.strip() for s in from_csv.split(",")]:
            self.refused.append((ident, to, from_csv))
            return
        self.writes.append(("advance", ident, to, from_csv))
        c["state"] = {"name": to}

    def cmd_comment(self, ident, body, *flags):
        self.comments.append((ident, body))

    def add_label(self, ident, label):
        self.labels.append((ident, label))
        self.cards[ident]["labels"]["nodes"].append({"name": label})

    def remove_label(self, ident, label):
        pass


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in ("BUREAU_READ", "BUREAU_READ_URL", "BUREAU_PIPELINE_REF", "MERGED_CARD",
                 "ACTIONS_ID_TOKEN_REQUEST_URL", "ACTIONS_ID_TOKEN_REQUEST_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(reconcile, "REPO_SLUG", "portico")
    monkeypatch.setattr(reconcile, "MAX_WIP", 5)
    # The stale-verdict gate reads the lane history from Linear — not the
    # door's business; no moves means the verdict still stands.
    monkeypatch.setattr(routing_verdict, "lane_moves", lambda ident: [])
    for ledger in (reconcile._write_failures, reconcile._read_failures,
                   reconcile._stale_defects, reconcile._card_skips):
        ledger.clear()
    bureau_read.reset_for_tests()
    reconcile.reset_sweep_cards()
    yield
    bureau_read.reset_for_tests()
    reconcile.reset_sweep_cards()


@contextlib.contextmanager
def wired(linear: Linear):
    with mock.patch.object(linear_ops, "gql", side_effect=linear.gql), \
            mock.patch.object(linear_ops, "get_issue", side_effect=linear.get_issue), \
            mock.patch.object(linear_ops, "cmd_state", side_effect=linear.cmd_state), \
            mock.patch.object(linear_ops, "cmd_advance", side_effect=linear.cmd_advance), \
            mock.patch.object(linear_ops, "cmd_comment", side_effect=linear.cmd_comment), \
            mock.patch.object(linear_ops, "add_label", side_effect=linear.add_label), \
            mock.patch.object(linear_ops, "remove_label", side_effect=linear.remove_label), \
            mock.patch.object(linear_ops, "requests_made", lambda: linear.requests):
        linear_ops.open_pass()
        try:
            yield
        finally:
            linear_ops.reset_pass_cache()


@contextlib.contextmanager
def door_at(monkeypatch, *door_cards, mode="on", **door_kw):
    world = {c["identifier"]: c for c in door_cards}
    with FakeIssuer() as issuer, FakeDoor(world, **door_kw) as door:
        for key, value in door_env(door_url=door.url, issuer=issuer, mode=mode).items():
            monkeypatch.setenv(key, value)
        yield door


def _advanced(linear):
    return [w[1] for w in linear.writes if w[0] == "advance" and w[2] == "Todo"]


def _backlog_reads(linear):
    return [q for q in linear.queries if 'eq: "Backlog"' in q]


# ── R1–R4: the live re-check before every promotion (items 32, 37) ──────────


def test_a_door_candidate_that_still_passes_is_promoted_after_one_live_read(monkeypatch):
    a = _c("DRE-101", "Backlog")
    linear = Linear(a)
    with door_at(monkeypatch, a) as door, wired(linear):
        assert reconcile.promote_ready(active_count=0) == 1
    assert _advanced(linear) == ["DRE-101"]
    assert len(_backlog_reads(linear)) == 1  # the re-check, by number, and nothing else
    assert door.asked("/board")[0]["query"]["lanes"] == "Backlog"


def test_R1_relation_added_after_the_poll_blocks_promotion_via_live_recheck(monkeypatch):
    door_a = _c("DRE-101", "Backlog")  # the poll has not seen the relation yet
    live_a = _c("DRE-101", "Backlog", blockers=(("DRE-102", "In Progress"),))
    linear = Linear(live_a, _c("DRE-102", "In Progress"))
    with door_at(monkeypatch, door_a), wired(linear):
        assert reconcile.promote_ready(active_count=0) == 0
    assert _advanced(linear) == []


def test_R2_a_reopened_blocker_reads_its_current_lane(monkeypatch):
    door_a = _c("DRE-101", "Backlog", blockers=(("DRE-102", "Done"),))
    live_a = _c("DRE-101", "Backlog", blockers=(("DRE-102", "Todo"),))  # rebuilt
    linear = Linear(live_a, _c("DRE-102", "Todo"))
    with door_at(monkeypatch, door_a), wired(linear):
        assert reconcile.promote_ready(active_count=0) == 0
    assert _advanced(linear) == []


def test_R3_a_hold_label_added_after_the_snapshot_is_not_promoted(monkeypatch, capsys):
    door_a = _c("DRE-101", "Backlog")
    live_a = _c("DRE-101", "Backlog", labels=(REPO_LABEL, "needs-human"))
    linear = Linear(live_a)
    with door_at(monkeypatch, door_a), wired(linear):
        assert reconcile.promote_ready(active_count=0) == 0
    assert _advanced(linear) == []
    assert "the live re-check refused it" in capsys.readouterr().out


def test_R4_an_epic_sent_back_after_the_snapshot_holds_its_children(monkeypatch):
    door_a = _c("DRE-101", "Backlog", parent="DRE-100", parent_lane="In Progress")
    live_a = _c("DRE-101", "Backlog", parent="DRE-100", parent_lane="Green Light")
    linear = Linear(live_a)
    monkeypatch.setattr(reconcile, "epic_records", lambda ids: {})
    monkeypatch.setattr(reconcile, "epic_blockers_unmet", lambda epic: False)
    monkeypatch.setattr(reconcile, "epic_thread", lambda epic: [])
    monkeypatch.setattr(mid_epic, "last_green_light", lambda *a, **k: "2026-08-01T00:00:00.000Z")
    monkeypatch.setattr(mid_epic, "promotion_refusal", lambda *a, **k: None)
    monkeypatch.setattr(plan_critic, "promotion_refusal", lambda *a, **k: None)
    with door_at(monkeypatch, door_a), wired(linear):
        assert reconcile.promote_ready(active_count=0) == 0
    assert _advanced(linear) == []
    # Control: the same child under a still-active epic IS promoted.
    reconcile.reset_sweep_cards()
    bureau_read.reset_for_tests()
    linear = Linear(door_a)
    with door_at(monkeypatch, door_a), wired(linear):
        assert reconcile.promote_ready(active_count=0) == 1


def test_a_card_that_left_backlog_since_the_snapshot_is_not_promoted(monkeypatch):
    door_a = _c("DRE-101", "Backlog")
    linear = Linear(_c("DRE-101", "Todo"))
    with door_at(monkeypatch, door_a), wired(linear):
        assert reconcile.promote_ready(active_count=0) == 0
    assert _advanced(linear) == []


def test_an_unstored_blocker_is_read_to_its_end_live(monkeypatch):
    """F19 on the door side is `hasNextPage: true`; the sweep reads that page
    live (as it does Linear's own full page) — never `state: null`."""
    door_a = _c("DRE-101", "Backlog", relations_partial=True)
    live_a = _c("DRE-101", "Backlog")
    linear = Linear(live_a)
    topups = []

    def gql(query, variables=None):
        if "inverseRelations(first: 100" in " ".join(query.split()):
            topups.append(variables["id"])
            linear.queries.append(query)
            return {"issue": {"inverseRelations": {
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "nodes": [{"type": "blocks",
                           "issue": {"identifier": "DRE-900", "state": {"name": "In Progress"}}}]}}}
        return linear.gql(query, variables)

    with door_at(monkeypatch, door_a), wired(linear), \
            mock.patch.object(linear_ops, "gql", side_effect=gql):
        assert reconcile.promote_ready(active_count=0) == 0
    assert topups == ["DRE-101"]
    assert _advanced(linear) == []


# ── R5, R7, R8: lanes ───────────────────────────────────────────────────────


def test_R5_a_lane_the_door_does_not_hold_falls_back_never_an_empty_lane(monkeypatch, capsys):
    busy = [_c(f"DRE-{n}", "In Review") for n in (11, 12, 13, 14, 15)]
    linear = Linear(*busy, *(_c(f"DRE-{n}", "Backlog") for n in (21, 22)))
    with door_at(monkeypatch, *busy, held_lanes={"Todo", "In Progress", "Backlog"}), \
            wired(linear):
        base = reconcile.wip_count(reconcile.wip_base(reconcile.active_cards()))
        assert base == 5
        assert reconcile.promote_ready(active_count=base) == 0
    assert _advanced(linear) == []
    assert "read from Linear this pass" in capsys.readouterr().out


def test_R7_R8_intake_and_planning_stay_on_linear(monkeypatch):
    linear = Linear(_c("DRE-1", "Intake"), _c("DRE-2", "Planning"), _c("DRE-3", "Todo"))
    with door_at(monkeypatch, _c("DRE-3", "Todo")) as door, wired(linear):
        intake = reconcile.active_cards(reconcile.INTAKE_LANE)
        planning = reconcile.active_cards(reconcile.PLANNING_LANE)
        work = reconcile.active_cards(reconcile.SWEEP_STATES)
    assert [c["identifier"] for c in intake] == ["DRE-1"]
    assert [c["identifier"] for c in planning] == ["DRE-2"]
    assert [c["identifier"] for c in work] == ["DRE-3"]
    asked = [r["query"]["lanes"] for r in door.asked("/board")]
    assert asked == [",".join(reconcile.DOOR_WORK_LANES)]
    assert "Intake" not in asked[0] and "Planning" not in asked[0]
    # One Linear read, of exactly the two lanes the door does not serve.
    assert len(linear.queries) == 1
    assert reconcile.DOOR_LINEAR_LANES == ("Planning", "Intake")


def test_a_mixed_request_takes_each_lane_from_its_own_source(monkeypatch):
    linear = Linear(_c("DRE-2", "Planning"), _c("DRE-3", "In Progress", updated="x"))
    door_copy = _c("DRE-3", "In Progress")
    with door_at(monkeypatch, door_copy), wired(linear):
        got = reconcile.active_cards(reconcile.SWEEP_STATES + reconcile.PLANNING_LANE)
    by_id = {c["identifier"]: c for c in got}
    assert set(by_id) == {"DRE-2", "DRE-3"}
    assert by_id["DRE-3"]["updatedAt"] == door_copy["updatedAt"]  # the door's copy
    assert reconcile._door_sourced == {"DRE-3"}


# ── R6, R16 and the rest of item 33: conditional writes ─────────────────────


def _nudge_stubs(stack, *, dead=0):
    for name in ("flag_stranded", "report_intake_depth", "advance_urgent_intake",
                 "repair_frozen_planning_holds", "serve_planner_line",
                 "release_groom_queue", "move_hand_built_to_review",
                 "close_finished_epics", "carry_epics_out_of_todo",
                 "report_break_glass", "report_epic_growth", "report_fix_concurrency",
                 "report_evicted_fix_runs", "drain_retiring_lanes", "unstick_conflicts",
                 "refresh_stale_merge_refs", "retrigger_dead_heads", "flag_no_checks_prs",
                 "flag_unowned_prs", "flag_unlanded_work", "flag_stranded_fixes",
                 "fix_approved_but_red", "retry_dead_fix_runs",
                 "redispatch_standing_verdicts", "recover_limit_deaths",
                 "restart_answered_blockers", "card_dependabot_prs",
                 "review_dependabot_prs", "recover_crashed_reviews",
                 "report_fleet_reviewer_outage", "check_dependabot_capacity",
                 "settle_repair_cards", "promote_ready"):
        stack.enter_context(mock.patch.object(reconcile, name, return_value=set()))
    stack.enter_context(mock.patch.object(reconcile, "rereview_watch_scope",
                                          return_value=(set(), lambda e: None)))
    stack.enter_context(mock.patch.object(reconcile.rereview_watch, "report"))
    stack.enter_context(mock.patch.object(reconcile, "pr_for", return_value=None))
    stack.enter_context(mock.patch.object(reconcile, "agent_run_alive", return_value=False))
    stack.enter_context(mock.patch.object(reconcile, "redeliver_rescued_work",
                                          return_value=False))
    stack.enter_context(mock.patch.object(linear_ops, "count_comments", return_value=dead))


def _stale(ident, lane, **kw):
    return _c(ident, lane, updated="2026-01-01T00:00:00.000Z", **kw)


def test_R6_a_dead_run_requeue_on_door_data_is_from_lane_conditional(monkeypatch):
    stuck = _stale("DRE-301", "In Progress")
    linear = Linear(stuck)
    with door_at(monkeypatch, stuck), wired(linear), contextlib.ExitStack() as stack:
        _nudge_stubs(stack)
        reconcile.main()
    assert linear.writes == [("state", "DRE-301", "Todo", (), ("In Progress",),
                              ("needs-human", "hand-built"))]
    assert any(reconcile.DEAD_TAG in body for _i, body in linear.comments)


def test_R6_a_card_that_moved_on_since_the_door_read_is_not_requeued(monkeypatch):
    stuck = _stale("DRE-301", "In Progress")
    linear = Linear(_stale("DRE-301", "In Review"))  # the agent opened its PR
    with door_at(monkeypatch, stuck), wired(linear), contextlib.ExitStack() as stack:
        _nudge_stubs(stack)
        reconcile.main()
    assert linear.writes == []
    assert linear.refused and linear.refused[0][0] == "DRE-301"
    assert not any(reconcile.DEAD_TAG in body for _i, body in linear.comments)


def test_a_requeue_refused_for_a_new_hold_label_posts_no_receipt(monkeypatch):
    stuck = _stale("DRE-301", "In Progress")
    linear = Linear(_stale("DRE-301", "In Progress", labels=(REPO_LABEL, "needs-human")))
    with door_at(monkeypatch, stuck), wired(linear), contextlib.ExitStack() as stack:
        _nudge_stubs(stack)
        reconcile.main()
    assert linear.writes == [] and linear.comments == []


def test_a_park_on_door_data_checks_the_lane_before_the_label(monkeypatch):
    stuck = _stale("DRE-301", "In Progress")
    linear = Linear(_stale("DRE-301", "In Review"))
    with door_at(monkeypatch, stuck), wired(linear), contextlib.ExitStack() as stack:
        _nudge_stubs(stack, dead=reconcile.REQUEUE_CAP)
        reconcile.main()
    assert linear.labels == []  # never `needs-human` on a card the park won't move
    assert linear.writes == []


def test_a_park_on_door_data_that_still_holds_parks_conditionally(monkeypatch):
    stuck = _stale("DRE-301", "In Progress")
    linear = Linear(stuck)
    with door_at(monkeypatch, stuck), wired(linear), contextlib.ExitStack() as stack:
        _nudge_stubs(stack, dead=reconcile.REQUEUE_CAP)
        reconcile.main()
    assert linear.labels == [("DRE-301", reconcile.HOLD_LABEL)]
    assert linear.writes == [("state", "DRE-301", "Backlog", ("--park",),
                              ("In Progress",), ())]


def test_off_mode_writes_exactly_as_before(monkeypatch):
    stuck = _stale("DRE-301", "In Progress")
    linear = Linear(stuck)
    with wired(linear), contextlib.ExitStack() as stack:
        _nudge_stubs(stack)
        reconcile.main()
    # The unconditional call it always was: no expectation keywords at all.
    assert linear.writes == [("state", "DRE-301", "Todo", (), None, ())]


def test_R16_carrying_an_epic_out_of_todo_is_conditional(monkeypatch):
    epic = _c("DRE-400", "Todo", title="[EPIC] a thing", children=True)
    linear = Linear(_c("DRE-400", "In Progress", title="[EPIC] a thing", children=True))
    monkeypatch.setattr(epic_todo_gate, "lane_before_todo", lambda lops, ident: "In Progress")
    posted = []
    monkeypatch.setattr(epic_todo_gate, "post_refusal", lambda *a: posted.append(a))
    with door_at(monkeypatch, epic), wired(linear):
        reconcile.carry_epics_out_of_todo()
    assert linear.writes == []
    assert linear.refused == [("DRE-400", "In Progress", ("Todo",), ())]
    assert posted == []


def test_R16_an_epic_still_in_todo_is_carried(monkeypatch):
    epic = _c("DRE-400", "Todo", title="[EPIC] a thing", children=True)
    linear = Linear(epic)
    monkeypatch.setattr(epic_todo_gate, "lane_before_todo", lambda lops, ident: "In Progress")
    monkeypatch.setattr(epic_todo_gate, "post_refusal", lambda *a: None)
    with door_at(monkeypatch, epic), wired(linear):
        reconcile.carry_epics_out_of_todo()
    assert linear.writes == [("state", "DRE-400", "In Progress", (), ("Todo",), ())]


def test_limit_recovery_moves_on_door_data_are_conditional(monkeypatch):
    stuck = _c("DRE-501", "In Progress")
    linear = Linear(_c("DRE-501", "In Review"))
    seen = {}

    def recover(lops, now, account, room, *, rerun, move, dispatch, cards):
        try:
            move("DRE-501", "Todo")
        except Exception as e:  # noqa: BLE001
            seen["error"] = str(e)
        return []

    monkeypatch.setattr(reconcile.limit_recovery, "recover", recover)
    with door_at(monkeypatch, stuck), wired(linear):
        reconcile.recover_limit_deaths()
    assert linear.writes == []
    assert "left 'In Progress'" in seen["error"]


def test_a_dependabot_cancel_on_door_data_is_conditional(monkeypatch):
    door_card = _c("DRE-601", "In Review")
    linear = Linear(_c("DRE-601", "Done"))
    monkeypatch.setattr(reconcile, "off_rail", lambda: False)
    monkeypatch.setattr(reconcile, "_dependabot_pr_listing", lambda: [
        {"number": 7, "url": "https://x/7", "state": "CLOSED", "body": "x",
         "headRefName": "dependabot/pip/x"}])
    monkeypatch.setattr(reconcile.dependabot_card, "marker_card", lambda body: "DRE-601")
    with door_at(monkeypatch, door_card), wired(linear):
        reconcile.card_dependabot_prs()
    assert linear.writes == [] and linear.comments == []


# ── R9, R11: shadow and the mode ────────────────────────────────────────────


def test_R9_shadow_decides_on_linear_and_classes_a_lane_move_door_older(monkeypatch, capsys):
    door_copy = _c("DRE-701", "Todo", updated="2026-10-02T19:00:00.000Z")
    live = _c("DRE-701", "In Progress", updated="2026-10-02T20:01:00.000Z")
    linear = Linear(live)
    with door_at(monkeypatch, door_copy, mode="shadow") as door, wired(linear):
        got = reconcile.active_cards()
    assert [c["state"]["name"] for c in got] == ["In Progress"]  # Linear's answer
    assert reconcile._door_sourced == set()  # nothing decided on the door
    out = capsys.readouterr().out
    assert "read-door-diff: door-older board DRE-701 lane" in out
    assert "1 explained (door-older), 0 unexplained" in out
    assert door.asked("/board")


def test_shadow_counts_a_same_stamp_difference_as_unexplained(monkeypatch, capsys):
    door_copy = _c("DRE-701", "Todo")
    live = _c("DRE-701", "Todo", labels=(REPO_LABEL, "needs-human"))
    linear = Linear(live)
    with door_at(monkeypatch, door_copy, mode="shadow"), wired(linear):
        reconcile.active_cards()
    out = capsys.readouterr().out
    assert "read-door-diff: same-stamp-different-value board DRE-701 labels" in out
    assert "0 explained (door-older), 1 unexplained" in out


def test_shadow_compares_the_backlog_and_promotes_on_linear_without_a_recheck(monkeypatch, capsys):
    a = _c("DRE-101", "Backlog")
    linear = Linear(a)
    with door_at(monkeypatch, a, mode="shadow"), wired(linear):
        assert reconcile.promote_ready(active_count=0) == 1
    assert len(_backlog_reads(linear)) == 1  # the read itself; no live re-check
    assert "updatedAt" in _backlog_reads(linear)[0]
    assert "read-door-diff: backlog compared 1 card(s): 0 explained" in capsys.readouterr().out


def test_R11_the_mode_is_read_once_per_run(monkeypatch):
    a = _c("DRE-701", "Todo")
    linear = Linear(a)
    with door_at(monkeypatch, a, mode="shadow"), wired(linear):
        reconcile.active_cards()
        monkeypatch.setenv("BUREAU_READ", "on")
        reconcile.reset_sweep_cards()  # a second pass in the same process
        reconcile.active_cards()
        assert bureau_read.mode() == "shadow"
        assert reconcile._door_sourced == set()


# ── R12–R14: a door that fails, throttles, or says Linear is held ───────────


def test_R12_door_dies_mid_sweep_and_the_promotion_rechecks_live(monkeypatch):
    work = _c("DRE-201", "In Progress")
    candidate = _c("DRE-101", "Backlog")
    linear = Linear(work, candidate)
    with door_at(monkeypatch, work) as door, wired(linear):
        door.routes["/board"] = lambda endpoint, query, headers: (
            (503, {"error": {"code": "CLOSED"}}) if query.get("lanes") == "Backlog"
            else (200, door.envelope([work], lanes=query["lanes"].split(","))))
        base = reconcile.wip_count(reconcile.wip_base(reconcile.active_cards()))
        assert reconcile.promote_ready(active_count=base) == 1
    # The candidates came from Linear (the fallback), the WIP from the door —
    # a decision with a door input, so the predicate was read live too.
    assert len(_backlog_reads(linear)) == 2
    assert bureau_read.exit_line().startswith(
        "read-door: served 1 unknown 0 unavailable 1 (mode on)")


def test_R13_a_throttled_door_falls_back_when_the_key_is_free(monkeypatch):
    a = _c("DRE-201", "Todo")
    linear = Linear(a)
    with door_at(monkeypatch, a) as door, wired(linear):
        door.routes["/board"] = (429, {"error": {"code": "THROTTLED"}})
        got = reconcile.active_cards()
    assert [c["identifier"] for c in got] == ["DRE-201"]
    assert len(linear.queries) == 1


def test_R13_a_throttled_door_with_the_key_held_skips(monkeypatch):
    a = _c("DRE-201", "Todo")
    linear = Linear(a)
    monkeypatch.setitem(linear_ops._budget, "refused_after", 2)
    with door_at(monkeypatch, a) as door, wired(linear):
        door.routes["/board"] = (429, {"error": {"code": "THROTTLED"}})
        with pytest.raises(reconcile.BoardHeld):
            reconcile.active_cards()
    assert linear.queries == []


def test_R14_linear_hold_skips_the_board_phases_with_no_linear_call(monkeypatch, capsys):
    a = _c("DRE-101", "Backlog")
    linear = Linear(a, *(_c(f"DRE-{n}", "In Progress") for n in range(11, 14)))
    with door_at(monkeypatch, a) as door, wired(linear):
        door.routes["/board"] = door.unknown("linear-hold")
        reconcile.main(promote_only=True)  # returns normally: not a red run
    assert linear.queries == []
    assert _advanced(linear) == []
    err = capsys.readouterr().err
    assert "read-door: skipped board_read this pass" in err
    assert "read-door: skipped promote_ready this pass" in err
    assert len(door.asked("/board")) == 1  # the hold is remembered for the pass


def test_R14_a_hold_never_blocks_the_lanes_linear_serves(monkeypatch):
    linear = Linear(_c("DRE-1", "Intake"))
    with door_at(monkeypatch) as door, wired(linear):
        door.routes["/board"] = door.unknown("linear-hold")
        with pytest.raises(reconcile.BoardHeld):
            reconcile.active_cards()
        assert [c["identifier"] for c in reconcile.active_cards(reconcile.INTAKE_LANE)] == ["DRE-1"]


def test_a_full_sweep_under_a_hold_is_not_red_and_promotes_nothing(monkeypatch, capsys):
    linear = Linear(_c("DRE-101", "Backlog"), _stale("DRE-301", "In Progress"))
    with door_at(monkeypatch) as door, wired(linear), contextlib.ExitStack() as stack:
        door.routes["/board"] = door.unknown("linear-hold")
        _nudge_stubs(stack)
        stack.enter_context(mock.patch.object(reconcile, "promote_ready",
                                              side_effect=AssertionError("promoted")))
        reconcile.main()
    assert linear.writes == [] and linear.comments == []
    assert reconcile._write_failures == [] and reconcile._read_failures == []
    assert "read-door: skipped" in capsys.readouterr().err


# ── R15 / item 45: the merge path ───────────────────────────────────────────


def test_R15_the_merge_path_reads_dependents_from_the_door_and_rechecks_live(monkeypatch):
    merged = _c("DRE-801", "Done")
    dep = _c("DRE-802", "Backlog", blockers=(("DRE-801", "Done"),))
    linear = Linear(merged, dep)
    monkeypatch.setenv("MERGED_CARD", "DRE-801")
    with door_at(monkeypatch, merged, dep) as door, wired(linear):
        monkeypatch.setenv("MERGED_CARD", "DRE-801")
        reconcile.main(promote_only=True)
    assert door.asked("/cards/DRE-801/dependents")
    assert door.asked("/cards")[-1]["query"]["ids"] == "DRE-802"
    assert not any("relatedIssue" in q for q in linear.queries)  # no Linear scope read
    assert _advanced(linear) == ["DRE-802"]
    assert len(_backlog_reads(linear)) == 1  # the dependent's own live re-check


def test_R15_a_dependent_blocked_live_is_held_on_the_merge_path(monkeypatch):
    merged = _c("DRE-801", "Done")
    dep_door = _c("DRE-802", "Backlog", blockers=(("DRE-801", "Done"),))
    dep_live = _c("DRE-802", "Backlog", blockers=(("DRE-801", "Done"), ("DRE-803", "Todo")))
    linear = Linear(merged, dep_live, _c("DRE-803", "Todo"))
    with door_at(monkeypatch, merged, dep_door), wired(linear):
        monkeypatch.setenv("MERGED_CARD", "DRE-801")
        reconcile.main(promote_only=True)
    assert _advanced(linear) == []


# ── E-O7: off is the sweep before the door ──────────────────────────────────


def test_off_never_asks_the_door_and_spends_what_it_always_spent(monkeypatch):
    a = _c("DRE-101", "Backlog")
    linear = Linear(a)
    with door_at(monkeypatch, a, mode="off") as door, wired(linear):
        assert reconcile.promote_ready(active_count=0) == 1
    assert door.requests == []
    assert len(linear.queries) == 1  # the Backlog read; no re-check
    assert "updatedAt" not in linear.queries[0]
