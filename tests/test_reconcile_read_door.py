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
  * Intake, Planning and Green Light come from the door's `scope=fleet` read,
    and from Linear when the door declines that scope to this repo (DRE-5848);
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
        self.variables: list[dict] = []
        self.writes: list[tuple] = []
        self.refused: list[tuple] = []
        self.comments: list[tuple] = []
        self.labels: list[tuple] = []

    # reads ------------------------------------------------------------------
    def gql(self, query, variables=None):
        self.queries.append(query)
        self.variables.append(dict(variables or {}))
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
        self.variables.append({})
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
def door_at(monkeypatch, *door_cards, mode="on", repository="dreadnought-foundry/portico",
            **door_kw):
    world = {c["identifier"]: c for c in door_cards}
    with FakeIssuer() as issuer, FakeDoor(world, repository=repository, **door_kw) as door:
        for key, value in door_env(door_url=door.url, issuer=issuer, mode=mode,
                                   repository=repository).items():
            monkeypatch.setenv(key, value)
        yield door


def _advanced(linear):
    return [w[1] for w in linear.writes if w[0] == "advance" and w[2] == "Todo"]


def _backlog_reads(linear):
    return [q for q in linear.queries if 'eq: "Backlog"' in q]


def _lane_reads(linear):
    """The lanes of every paged board read Linear was asked for, in order."""
    return [v["states"] for q, v in zip(linear.queries, linear.variables)
            if "state: {name: {in: $states}}" in " ".join(q.split())]


def _fleet_asks(door):
    return [r["query"]["lanes"] for r in door.asked("/board")
            if r["query"].get("scope") == "fleet"]


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


def test_R7_R8_intake_and_planning_fall_back_to_linear_when_the_fleet_is_declined(monkeypatch,
                                                                                 capsys):
    """The door is asked `scope=fleet` for Intake and Planning (DRE-5848); a
    door that declines that scope to this repo gets exactly the one Linear
    read of the two lanes the sweep made before, and the work lanes still
    come from the door in the same pass."""
    linear = Linear(_c("DRE-1", "Intake"), _c("DRE-2", "Planning"), _c("DRE-3", "Todo"))
    with door_at(monkeypatch, _c("DRE-3", "Todo")) as door, wired(linear):
        door.decline_fleet("lane-not-held")
        intake = reconcile.active_cards(reconcile.INTAKE_LANE)
        planning = reconcile.active_cards(reconcile.PLANNING_LANE)
        work = reconcile.active_cards(reconcile.SWEEP_STATES)
    assert [c["identifier"] for c in intake] == ["DRE-1"]
    assert [c["identifier"] for c in planning] == ["DRE-2"]
    assert [c["identifier"] for c in work] == ["DRE-3"]
    repo_asks = [r["query"]["lanes"] for r in door.asked("/board")
                 if r["query"]["scope"] == "repo"]
    assert repo_asks == [",".join(reconcile.DOOR_WORK_LANES)]
    assert _fleet_asks(door) == ["Planning,Intake"]  # once, for both lanes
    # One Linear read, of exactly the two lanes the door declined.
    assert _lane_reads(linear) == [["Planning", "Intake"]]
    assert len(linear.queries) == 1
    assert reconcile.DOOR_LINEAR_LANES == ("Planning", "Intake")
    assert reconcile._door_sourced == {"DRE-3"}
    assert bureau_read.enabled()
    assert capsys.readouterr().out.count(
        "read-door: Intake/Planning read from Linear this pass — the door does not "
        "serve scope=fleet to this repo (lane-not-held)") == 1


def test_a_mixed_request_takes_each_lane_from_its_own_source(monkeypatch):
    linear = Linear(_c("DRE-2", "Planning"), _c("DRE-3", "In Progress", updated="x"))
    door_copy = _c("DRE-3", "In Progress")
    with door_at(monkeypatch, door_copy) as door, wired(linear):
        door.decline_fleet("lane-not-held")
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
                 "rerun_runner_lost_ci", "resync_desynced_heads", "restart_answered_blockers", "card_dependabot_prs",
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
        # DRE-6186: the planner already had it, so the cap parks.
        monkeypatch.setattr(reconcile.dead_run, "split_tried", lambda bodies: True)
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


def test_R14_a_work_lane_hold_never_blocks_the_lanes_linear_serves(monkeypatch):
    """A hold on the work-lane read is that read's: a door that declines the
    fleet to this repo still has Intake read from Linear in the same pass."""
    linear = Linear(_c("DRE-1", "Intake"))
    with door_at(monkeypatch) as door, wired(linear):
        door.routes["/board"] = lambda endpoint, query, headers: (
            door.unknown("lane-not-held") if query.get("scope") == "fleet"
            else door.unknown("linear-hold"))
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


# ── DRE-5848: Intake, Planning and Green Light through the fleet read ──────
# The sweep asks the door `scope=fleet` for the lanes it used to take from
# Linear, and the door's answer decides: a steward repo the console serves the
# fleet to spends no Linear request on them; any other repo — Portico today,
# and every repo while the console's `PIPELINE_READ_UNROUTED` is off — is
# declined, and reads them from Linear exactly as before. No repo is named in
# the code; these tests name them only to play each caller.

_STEWARDS = ("bureau-pipeline", "agent-bureau")


def _as_repo(monkeypatch, slug):
    repository = f"dreadnought-foundry/{slug}"
    monkeypatch.setattr(reconcile, "REPO_SLUG", slug)
    monkeypatch.setattr(reconcile, "REPO", repository)
    monkeypatch.setenv("REPO", repository)
    return repository


def _fleet_board(slug):
    label = f"repo:{slug}"
    return [
        _c("DRE-11", "Intake", labels=(label,)),
        _c("DRE-12", "Planning", labels=()),  # nobody's yet: the front path
        _c("DRE-13", "Green Light", labels=(label,)),
        _c("DRE-14", "In Progress", labels=(label,)),
    ]


@pytest.mark.parametrize("slug", _STEWARDS)
def test_a_repo_served_the_fleet_spends_no_linear_request_on_these_lanes(monkeypatch, capsys,
                                                                       slug):
    repository = _as_repo(monkeypatch, slug)
    board = _fleet_board(slug)
    linear = Linear(*board)
    with door_at(monkeypatch, *board, repository=repository) as door, wired(linear):
        reconcile.recover_limit_deaths()  # nothing to recover
        assert linear_ops.requests_made() == 0
        assert _fleet_asks(door) == ["Planning,Intake"]
    reconcile.reset_sweep_cards()
    with door_at(monkeypatch, *board, repository=repository) as door, wired(linear):
        assert reconcile.serve_planner_line() >= 0  # an empty line
        assert linear_ops.requests_made() == 0
        assert _fleet_asks(door) == ["Planning,Intake", "Green Light"]
    assert {"DRE-11", "DRE-12", "DRE-13", "DRE-14"} <= reconcile._door_sourced
    assert linear.queries == []
    captured = capsys.readouterr()
    assert "limit-recovery: skipped" not in captured.err
    assert "does not serve scope=fleet" not in captured.out


@pytest.mark.parametrize("decline,reason", [
    ({"reason": "lane-not-held"}, "lane-not-held"),
    ({"status": 403}, "refused"),
])
def test_a_repo_declined_the_fleet_reads_those_lanes_from_linear_as_before(monkeypatch, capsys,
                                                                         decline, reason):
    _as_repo(monkeypatch, "portico")
    board = _fleet_board("portico")
    linear = Linear(*board)
    with door_at(monkeypatch, *board) as door, wired(linear):
        door.decline_fleet(**decline)
        reconcile.recover_limit_deaths()
        reconcile.serve_planner_line()
        # The work lanes still come from the door, in the same pass.
        assert [c["identifier"] for c in reconcile.active_cards()] == ["DRE-14"]
    # Exactly the Linear reads the sweep made before: Intake and Planning in
    # one paged read, Green Light in the planner line's own.
    assert _lane_reads(linear) == [["Planning", "Intake"], ["Green Light"]]
    assert len(linear.queries) == 2
    assert _fleet_asks(door) == ["Planning,Intake", "Green Light"]  # one refusal each
    assert reconcile._door_sourced == {"DRE-14"}
    assert bureau_read.enabled() and bureau_read.disabled_reason() is None
    out = capsys.readouterr().out
    for lanes in ("Intake/Planning", "Green Light"):
        assert out.count(
            f"read-door: {lanes} read from Linear this pass — the door does not serve "
            f"scope=fleet to this repo ({reason})") == 1
    assert "read-door: board unknown" not in out


@pytest.mark.parametrize("status,reason", [(401, "refused"), (403, "refused"),
                                           (404, "not-found")])
def test_a_refused_fleet_scope_is_unknown_for_that_read_and_the_door_stays_in_use(
        monkeypatch, status, reason):
    with door_at(monkeypatch, _c("DRE-3", "Todo")) as door:
        door.decline_fleet(status=status)
        with pytest.raises(bureau_read.ReadUnknown) as caught:
            bureau_read.board(["Intake"], scope="fleet", max_age=bureau_read.BOARD_MAX_AGE)
        assert caught.value.reason == reason
        assert not caught.value.unavailable and not caught.value.skip
        assert bureau_read.enabled()
        served = bureau_read.board(["Todo"], max_age=bureau_read.BOARD_MAX_AGE)
        assert [n["identifier"] for n in served.nodes] == ["DRE-3"]
        # A token the door refuses on this repo's own scope still stops it.
        door.routes["/board"] = (status, {"error": {"code": "X"}})
        with pytest.raises(bureau_read.ReadUnknown) as stopped:
            bureau_read.board(["Todo"], max_age=bureau_read.BOARD_MAX_AGE)
        assert stopped.value.unavailable
        assert not bureau_read.enabled()


def test_a_fleet_read_that_fails_stops_the_door_and_says_it_once(monkeypatch, capsys):
    linear = Linear(_c("DRE-1", "Intake"), _c("DRE-3", "Todo"))
    with door_at(monkeypatch, _c("DRE-3", "Todo")) as door, wired(linear):
        door.fleet = (503, {"error": {"code": "CLOSED"}})
        intake = reconcile.active_cards(reconcile.INTAKE_LANE)
        work = reconcile.active_cards()
    assert [c["identifier"] for c in intake] == ["DRE-1"]
    assert [c["identifier"] for c in work] == ["DRE-3"]
    assert not bureau_read.enabled()
    out = capsys.readouterr().out
    assert out.count("read-door: board unknown (") == 2  # the fleet read, then the work lanes
    assert "does not serve scope=fleet" not in out


def test_a_fleet_read_told_linear_is_held_skips_with_no_linear_call(monkeypatch):
    linear = Linear(_c("DRE-1", "Intake"), _c("DRE-3", "Todo"))
    with door_at(monkeypatch, _c("DRE-1", "Intake"), _c("DRE-3", "Todo")) as door, \
            wired(linear):
        door.fleet = door.unknown("linear-hold")
        for _ in range(2):
            with pytest.raises(reconcile.BoardHeld):
                reconcile.active_cards(reconcile.INTAKE_LANE)
        # The hold is that read's: the work lanes are still served.
        assert [c["identifier"] for c in reconcile.active_cards()] == ["DRE-3"]
    assert linear.queries == []
    assert _fleet_asks(door) == ["Planning,Intake"]  # remembered for the pass


def test_green_light_is_its_own_fleet_read(monkeypatch):
    """A door that holds Intake and Planning but not Green Light still serves
    the first two: each lane group is asked, and falls back, on its own."""
    board = _fleet_board("portico")
    linear = Linear(*board)
    with door_at(monkeypatch, *board,
                 held_lanes=set(reconcile.SWEPT_LANES)) as door, wired(linear):
        reconcile.serve_planner_line()
    assert _lane_reads(linear) == [["Green Light"]]
    assert _fleet_asks(door) == ["Planning,Intake", "Green Light"]
    assert {"DRE-11", "DRE-12"} <= reconcile._door_sourced
    assert "DRE-13" not in reconcile._door_sourced


@pytest.mark.parametrize("mode", ["off", "shadow"])
def test_off_and_shadow_make_no_fleet_read_and_spend_what_they_spent(monkeypatch, mode):
    board = _fleet_board("portico")
    linear = Linear(*board)
    with door_at(monkeypatch, *board, mode=mode) as door, wired(linear):
        reconcile.recover_limit_deaths()
        reconcile.serve_planner_line()
    assert _fleet_asks(door) == []
    assert _lane_reads(linear) == [list(reconcile.SWEPT_LANES), ["Green Light"]]
    assert reconcile._door_sourced == set()


@pytest.mark.parametrize("mode", ["off", "shadow"])
def test_off_and_shadow_read_green_light_through_active_cards(monkeypatch, mode):
    """The planner line's Green Light read goes through `active_cards` outside
    `on`, as it did before the fleet read — the seam the sweep's other tests
    stand in for. A stand-in there answers it, and Linear is never asked."""
    board = _fleet_board("portico")
    asked = []

    def stand_in(states=reconcile.SWEPT_LANES):
        asked.append(tuple(states))
        return [c for c in board if c["state"]["name"] in states]

    monkeypatch.setattr(reconcile, "active_cards", stand_in)
    linear = Linear()
    with door_at(monkeypatch, *board, mode=mode) as door, wired(linear):
        reconcile.serve_planner_line()
    assert ("Green Light",) in asked
    assert linear.queries == []
    assert _fleet_asks(door) == []


# ── DRE-5848: a move decided on a fleet-served card is conditional ─────────


def test_limit_recovery_of_a_fleet_served_planning_card_is_conditional(monkeypatch):
    stuck = _c("DRE-511", "Planning")
    linear = Linear(_c("DRE-511", "Triage"))
    seen = {}

    def recover(lops, now, account, room, *, rerun, move, dispatch, cards):
        assert "DRE-511" in {c["identifier"] for c in cards}
        try:
            move("DRE-511", "Intake")
        except Exception as e:  # noqa: BLE001
            seen["error"] = str(e)
        return []

    monkeypatch.setattr(reconcile.limit_recovery, "recover", recover)
    with door_at(monkeypatch, stuck), wired(linear):
        reconcile.recover_limit_deaths()
    assert linear.writes == []
    assert linear.refused == [("DRE-511", "Intake", ("Planning",), ())]
    assert "left 'Planning'" in seen["error"]


def _stalled(lane):
    return card("DRE-521", lane, labels=(), comments=(FLEET,), updated=_OLD)


def test_the_planning_watchdog_parks_a_fleet_served_card_conditionally(monkeypatch):
    linear = Linear(_stalled("Planning"))
    with door_at(monkeypatch, _stalled("Planning")), wired(linear):
        assert reconcile.flag_stalled_planning() == {"DRE-521"}
    assert [ident for ident, _body in linear.comments] == ["DRE-521"]
    assert linear.writes == [("state", "DRE-521", reconcile.PARKED_STATE, (),
                              ("Planning",), ())]


def test_the_planning_watchdog_leaves_a_card_that_moved_since_the_fleet_read(monkeypatch):
    linear = Linear(_stalled("Green Light"))
    with door_at(monkeypatch, _stalled("Planning")), wired(linear):
        assert reconcile.flag_stalled_planning() == set()
    assert linear.writes == [] and linear.comments == []  # no receipt, no move


def _urgent(lane):
    return _c("DRE-531", lane, priority=reconcile.URGENT_PRIORITY,
              updated="2026-10-05T12:00:00.000Z")


def _urgent_linear(monkeypatch, live_lane):
    linear = Linear(_urgent(live_lane))
    raised = "2026-10-05T11:00:00.000Z"

    def gql(query, variables=None):
        if "history(first: 50)" in query:
            linear.queries.append(query)
            linear.variables.append(dict(variables or {}))
            return {"issues": {"nodes": [{
                "id": "uuid-DRE-531", "identifier": "DRE-531",
                "priority": reconcile.URGENT_PRIORITY, "createdAt": raised, "parent": None,
                "history": {"nodes": [{"createdAt": raised, "fromPriority": 3,
                                       "toPriority": reconcile.URGENT_PRIORITY,
                                       "toState": None}]}}],
                "pageInfo": {"hasNextPage": False, "endCursor": None}}}
        return linear.gql(query, variables)

    monkeypatch.setattr(reconcile, "_urgent_exclusions", lambda: (set(), ""))
    monkeypatch.setattr(reconcile, "_recent_urgent_moves", lambda now: set())
    return linear, gql


def test_the_urgent_fast_path_moves_a_fleet_served_card_still_in_intake(monkeypatch):
    linear, gql = _urgent_linear(monkeypatch, "Intake")
    with door_at(monkeypatch, _urgent("Intake")), wired(linear), \
            mock.patch.object(linear_ops, "gql", side_effect=gql):
        assert reconcile.advance_urgent_intake() == {"DRE-531"}
    assert [ident for ident, _body in linear.comments] == ["DRE-531"]
    assert linear.writes == [("advance", "DRE-531", "Planning", "Intake")]


def test_the_urgent_fast_path_leaves_a_card_that_moved_since_the_fleet_read(monkeypatch):
    linear, gql = _urgent_linear(monkeypatch, "Backlog")
    with door_at(monkeypatch, _urgent("Intake")), wired(linear), \
            mock.patch.object(linear_ops, "gql", side_effect=gql):
        assert reconcile.advance_urgent_intake() == set()
    assert linear.comments == []  # the receipt is a claim: never for a card that left
    assert linear.writes == [] and linear.refused == []


def _queued(lane):
    return _c("DRE-541", lane, labels=(REPO_LABEL, reconcile.groomer.QUEUED_LABEL))


def _groom_queue(monkeypatch):
    monkeypatch.setattr(reconcile, "REPO_SLUG", reconcile.epic_cap.START_OWNER_SLUG)
    monkeypatch.setenv(reconcile.GROOM_CARD_ENV, "DRE-9")
    monkeypatch.setattr(reconcile.groomer, "decision_records", lambda *a, **k: [])
    monkeypatch.setattr(reconcile.groomer, "queue_standing", lambda records: [
        {"identifier": "DRE-541", "id": "p1", "place": 1}])
    monkeypatch.setattr(reconcile.groomer, "released_record", lambda changes: "released")


def test_a_groom_release_of_a_fleet_served_card_is_conditional(monkeypatch):
    """The release writes in two halves since DRE-3326 — the card's
    groom-moved note, then its lane — so a door-decided card is re-read live
    before the first half, and one that has left Intake gets neither."""
    _groom_queue(monkeypatch)
    linear = Linear(_queued("Canceled"))
    with door_at(monkeypatch, _queued("Intake")), wired(linear):
        assert reconcile.release_groom_queue(1) == []
    assert linear.writes == [] and linear.comments == []
    assert "get_issue DRE-541" in linear.queries


def test_a_groom_release_of_a_fleet_served_card_still_in_intake_lands(monkeypatch):
    _groom_queue(monkeypatch)
    linear = Linear(_queued("Intake"))
    with door_at(monkeypatch, _queued("Intake")), wired(linear):
        assert reconcile.release_groom_queue(1) == ["DRE-541"]
    assert linear.writes == [("state", "DRE-541", reconcile.GROOM_RELEASE_TO, (),
                              ("Intake",), ())]


# ── E-O7: off is the sweep before the door ──────────────────────────────────


def test_off_never_asks_the_door_and_spends_what_it_always_spent(monkeypatch):
    a = _c("DRE-101", "Backlog")
    linear = Linear(a)
    with door_at(monkeypatch, a, mode="off") as door, wired(linear):
        assert reconcile.promote_ready(active_count=0) == 1
    assert door.requests == []
    assert len(linear.queries) == 1  # the Backlog read; no re-check
    assert "updatedAt" not in linear.queries[0]


# ── The NO-ROUTE watchdog keeps its input with the door on (PR #687, item 2) ──
# `flag_stranded` is fleet-wide: every repo's sweep looks for a card in Todo or
# In Progress that NO repo can pick up — no `repo:` label, or a slug off the
# routing map. The door serves only this repo's tenant and stores no card
# without a `repo:` label (design D1), so with every repo `on` such a card would
# be flagged by no sweep, and the shadow comparison (this repo's cards only)
# could never show it. The off-map class reads Todo and In Progress from Linear.

_OLD = "2026-09-01T00:00:00.000Z"


def test_with_the_door_on_an_unlabeled_card_in_todo_is_still_flagged(monkeypatch):
    ours = _c("DRE-310", "In Progress", updated=_OLD)
    orphan = card("DRE-311", "Todo", labels=(), comments=(FLEET,), updated=_OLD)
    linear = Linear(ours, orphan)
    with door_at(monkeypatch, ours), wired(linear):
        flagged = reconcile.flag_stranded()
    assert "DRE-311" in flagged
    assert ("DRE-311", reconcile.HOLD_LABEL) in linear.labels


def test_with_the_door_on_an_off_map_slug_in_progress_is_still_flagged(monkeypatch):
    """A label naming a repo the routing map does not carry is unroutable too;
    the door, scoped to this repo's tenant, never serves it."""
    ours = _c("DRE-312", "In Progress", updated=_OLD)
    stray = card("DRE-313", "In Progress", labels=("repo:nowhere",), comments=(FLEET,),
                 updated=_OLD)
    linear = Linear(ours, stray)
    with door_at(monkeypatch, ours), wired(linear), \
            mock.patch.object(reconcile, "live_rail_slugs",
                              return_value=frozenset({"portico"})):
        flagged = reconcile.flag_stranded()
    assert "DRE-313" in flagged


def test_with_the_door_on_another_routable_repos_card_is_not_this_sweeps(monkeypatch):
    """The Linear read is for the off-map class only: a card another repo can
    pick up is that repo's sweep's to check."""
    ours = _c("DRE-314", "In Progress", updated=_OLD)
    theirs = card("DRE-315", "Todo", labels=("repo:atlas",), comments=(), updated=_OLD)
    linear = Linear(ours, theirs)
    monkeypatch.setattr(reconcile.validate_card, "VALID_SLUGS", {"portico", "atlas"})
    with door_at(monkeypatch, ours), wired(linear):
        flagged = reconcile.flag_stranded()
    assert "DRE-315" not in flagged


# ── DRE-5850: the promotion gate's epic threads, whole, from the door ──────
# `promote_ready` reads each In Progress epic's whole thread WITH authorship
# (`epic_thread` → `comment_records`) for the second critic's PASS marker. A
# thread past fifty comments missed the pass cache and was paged from Linear
# every pass. In `on` it is asked of the door in ONE `comments=all` read for
# every epic the gate names; only what the door cannot prove whole goes to
# Linear, and the viewer — this process's own key — is still Linear's.

_EPIC_A, _EPIC_B = "DRE-900", "DRE-950"


def _epic_thread_nodes(ident, count):
    """`count` comments, oldest first, every third one somebody else's."""
    return [{"body": f"{ident} comment {i}",
             "createdAt": f"2026-09-{1 + i // 60:02d}T{(i // 60) % 24:02d}:{i % 60:02d}:00.000Z",
             "user": {"id": "fleet-user" if i % 3 else "a-person"}}
            for i in range(count)]


def _epic(ident, count):
    node = _c(ident, "In Progress", title=f"[EPIC] {ident}", children=True, comments=())
    node["comments"] = {"pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": list(reversed(_epic_thread_nodes(ident, count)))}
    return node


class ThreadLinear(Linear):
    """Linear with the epics' threads behind it: the newest fifty plus the
    viewer in one read, then 100-comment pages toward the oldest — exactly
    what `linear_ops._fetch_thread` asks for."""

    def __init__(self, *cards, threads, viewer="fleet-user"):
        super().__init__(*cards)
        self.threads = threads  # identifier -> nodes, oldest first
        self.viewer = viewer

    def _page(self, ident, start, size):
        newest_first = list(reversed(self.threads[ident]))
        nodes = newest_first[start:start + size]
        more = start + size < len(newest_first)
        return {"pageInfo": {"hasNextPage": more,
                             "endCursor": f"{ident}@{start + size}" if more else None},
                "nodes": copy.deepcopy(nodes)}

    def gql(self, query, variables=None):
        q = " ".join(query.split())
        v = variables or {}
        if q == "query { viewer { id } }":
            self.queries.append(query)
            self.variables.append({})
            return {"viewer": {"id": self.viewer}}
        if "viewer { id }" in q and "issue(id: $id)" in q:
            self.queries.append(query)
            self.variables.append(dict(v))
            return {"viewer": {"id": self.viewer},
                    "issue": {"comments": self._page(v["id"], 0, linear_ops.COMMENT_WINDOW)}}
        if "comments(first: 100, after: $after)" in q:
            self.queries.append(query)
            self.variables.append(dict(v))
            start = int(v["after"].split("@")[1])
            return {"issue": {"comments": self._page(v["id"], start, 100)}}
        return super().gql(query, variables)

    def thread_reads(self, ident=None):
        """The Linear requests spent on epic threads: the viewer, the window,
        the older pages — of `ident` alone when named (the viewer excluded)."""
        out = []
        for q, v in zip(self.queries, self.variables):
            q = " ".join(q.split())
            if "viewer { id }" not in q and "comments(first: 100, after" not in q:
                continue
            if ident is None or v.get("id") == ident:
                out.append(q)
        return out


def _epic_world():
    kids = [_c("DRE-901", "Backlog", parent=_EPIC_A), _c("DRE-951", "Backlog", parent=_EPIC_B)]
    epics = [_epic(_EPIC_A, 60), _epic(_EPIC_B, 120)]
    threads = {_EPIC_A: _epic_thread_nodes(_EPIC_A, 60),
               _EPIC_B: _epic_thread_nodes(_EPIC_B, 120)}
    return kids, epics, threads


def _gate_stubs(monkeypatch):
    """Everything the gate reads about an epic EXCEPT its thread, stood in:
    what is under test is where the thread comes from. The second critic's
    gate is recorded and passes, so every child reaches it."""
    seen: dict[str, list] = {}
    monkeypatch.setattr(reconcile, "epic_records", lambda ids: {})
    monkeypatch.setattr(reconcile, "epic_blockers_unmet", lambda epic: False)
    monkeypatch.setattr(mid_epic, "last_green_light",
                        lambda *a, **k: "2026-08-01T00:00:00.000Z")
    monkeypatch.setattr(mid_epic, "promotion_refusal", lambda *a, **k: None)

    def refusal(ident, epic, green_lit_at, records, **kw):
        seen[epic] = copy.deepcopy(records)
        return None

    monkeypatch.setattr(plan_critic, "promotion_refusal", refusal)
    return seen


def _all_reads(door):
    return [r["query"] for r in door.asked("/cards") if r["query"].get("comments") == "all"]


def _linear_rows(monkeypatch):
    """What `comment_records` handed the gate when Linear served both threads
    — the sweep before this card, in `off`."""
    kids, epics, threads = _epic_world()
    seen = _gate_stubs(monkeypatch)
    linear = ThreadLinear(*kids, *epics, threads=threads)
    bureau_read.reset_for_tests()
    reconcile.reset_sweep_cards()
    with door_at(monkeypatch, *kids, *epics, mode="off"), wired(linear):
        assert reconcile.promote_ready(active_count=0) == 2
    bureau_read.reset_for_tests()
    reconcile.reset_sweep_cards()
    return seen, linear


def test_whole_epic_threads_come_from_the_door_and_only_the_viewer_from_linear(monkeypatch,
                                                                                capsys):
    expected, _ = _linear_rows(monkeypatch)
    kids, epics, threads = _epic_world()
    seen = _gate_stubs(monkeypatch)
    linear = ThreadLinear(*kids, *epics, threads=threads)
    with door_at(monkeypatch, *kids, *epics) as door, wired(linear):
        assert reconcile.promote_ready(active_count=0) == 2
        # One Linear request on epic threads, pinned on the sweep's own meter:
        # the viewer. The rest of the pass is the Backlog children's live
        # re-checks, one each.
        assert linear.thread_reads() == ["query { viewer { id } }"]
        assert linear_ops.requests_made() == 1 + len(_backlog_reads(linear))
    assert len(_backlog_reads(linear)) == 2
    assert _all_reads(door) == [{"ids": f"{_EPIC_A},{_EPIC_B}", "comments": "all",
                                 "relations": "0"}]
    # The same rows the gate got from Linear: bodies, authorship, stamps.
    assert seen == expected
    assert len(seen[_EPIC_B]) == 120
    assert {r["authored_by_pipeline"] for r in seen[_EPIC_A]} == {True, False}
    assert capsys.readouterr().out.count(
        "promotion: epic threads — 2 from the door in one read, 0 read from Linear "
        "(none)") == 1


def test_a_thread_the_door_cannot_prove_whole_is_read_from_linear_and_the_rest_reasked(
        monkeypatch, capsys):
    expected, _ = _linear_rows(monkeypatch)
    kids, epics, threads = _epic_world()
    seen = _gate_stubs(monkeypatch)
    linear = ThreadLinear(*kids, *epics, threads=threads)
    with door_at(monkeypatch, *kids, *epics) as door, wired(linear):
        door.incomplete_threads = {_EPIC_A}
        assert reconcile.promote_ready(active_count=0) == 2
    # Asked twice at most: everything, then what the first answer did not name.
    assert [r["ids"] for r in _all_reads(door)] == [f"{_EPIC_A},{_EPIC_B}", _EPIC_B]
    # The incomplete epic from Linear exactly as today: the newest fifty (with
    # the viewer), then one older page for the other ten.
    reads_a = linear.thread_reads(_EPIC_A)
    assert len(reads_a) == 2
    assert "comments(first: 50)" in reads_a[0] and "after: $after" in reads_a[1]
    assert linear.thread_reads(_EPIC_B) == []
    assert seen == expected
    assert bureau_read.enabled()
    assert capsys.readouterr().out.count(
        "promotion: epic threads — 1 from the door in one read, 1 read from Linear "
        f"(thread-incomplete: {_EPIC_A})") == 1


def _today_thread_reads(monkeypatch):
    _seen, linear = _linear_rows(monkeypatch)
    return linear.thread_reads()


@pytest.mark.parametrize("answer,reason", [
    ("stale", "stale"),
    ("missing-field", "missing-field"),
    (404, "not-found"),
    (503, "door unavailable"),
])
def test_any_other_door_answer_reads_every_epic_thread_from_linear_as_today(
        monkeypatch, capsys, answer, reason):
    today = _today_thread_reads(monkeypatch)
    expected, _ = _linear_rows(monkeypatch)
    kids, epics, threads = _epic_world()
    seen = _gate_stubs(monkeypatch)
    linear = ThreadLinear(*kids, *epics, threads=threads)
    with door_at(monkeypatch, *kids, *epics) as door, wired(linear):
        door.routes["/cards"] = (door.unknown(answer) if isinstance(answer, str)
                                 else (answer, {"error": {"code": "X"}}))
        assert reconcile.promote_ready(active_count=0) == 2
    assert len(_all_reads(door)) == 1  # one ask, no second read
    assert linear.thread_reads() == today
    assert len(today) == 4  # each epic: the newest fifty with the viewer, one older page
    assert seen == expected
    assert capsys.readouterr().out.count(
        f"promotion: epic threads — 0 from the door in one read, 2 read from Linear "
        f"({reason})") == 1


def test_a_door_already_stopped_this_run_is_not_asked_for_epic_threads(monkeypatch, capsys):
    today = _today_thread_reads(monkeypatch)
    kids, epics, threads = _epic_world()
    _gate_stubs(monkeypatch)
    linear = ThreadLinear(*kids, *epics, threads=threads)
    with door_at(monkeypatch, *kids, *epics) as door, wired(linear):
        door.routes["/board"] = (503, {"error": {"code": "CLOSED"}})
        assert reconcile.promote_ready(active_count=0) == 2
    assert _all_reads(door) == []
    assert linear.thread_reads() == today
    assert ("promotion: epic threads — 0 from the door in one read, 2 read from Linear "
            "(door unavailable)") in capsys.readouterr().out


def test_linear_hold_on_the_thread_read_skips_the_promotion_with_no_linear_call(monkeypatch,
                                                                                 capsys):
    kids, epics, threads = _epic_world()
    _gate_stubs(monkeypatch)
    linear = ThreadLinear(*kids, *epics, threads=threads)
    with door_at(monkeypatch, *kids, *epics) as door, wired(linear):
        door.routes["/cards"] = door.unknown("linear-hold")
        reconcile.main(promote_only=True)  # returns normally: not a red run
    assert linear.queries == []
    assert _advanced(linear) == []
    assert len(_all_reads(door)) == 1
    assert "read-door: skipped promote_ready this pass — the door says linear-hold" in (
        capsys.readouterr().err)


@pytest.mark.parametrize("mode", ["off", "shadow"])
def test_off_and_shadow_make_no_thread_read_and_spend_what_they_spent(monkeypatch, capsys,
                                                                     mode):
    today = _today_thread_reads(monkeypatch)
    kids, epics, threads = _epic_world()
    _gate_stubs(monkeypatch)
    linear = ThreadLinear(*kids, *epics, threads=threads)
    with door_at(monkeypatch, *kids, *epics, mode=mode) as door, wired(linear):
        assert reconcile.promote_ready(active_count=0) == 2
    assert _all_reads(door) == []
    assert door.asked("/cards") == []
    assert linear.thread_reads() == today
    assert "promotion: epic threads" not in capsys.readouterr().out


def test_an_epic_thread_already_whole_in_the_pass_is_not_asked_again(monkeypatch, capsys):
    """An epic under fifty comments came whole with the work-lane board read:
    the door is asked only for the one past the window."""
    kids, _epics, threads = _epic_world()
    small = _epic(_EPIC_A, 12)
    threads[_EPIC_A] = _epic_thread_nodes(_EPIC_A, 12)
    big = _epic(_EPIC_B, 120)
    _gate_stubs(monkeypatch)
    linear = ThreadLinear(*kids, small, big, threads=threads)
    with door_at(monkeypatch, *kids, small, big) as door, wired(linear):
        reconcile.active_cards()  # the work-lane board: the epics' newest fifty
        assert reconcile.promote_ready(active_count=0) == 2
    assert [r["ids"] for r in _all_reads(door)] == [_EPIC_B]
    assert linear.thread_reads() == ["query { viewer { id } }"]
    assert ("promotion: epic threads — 1 from the door in one read, 0 read from Linear "
            "(none)") in capsys.readouterr().out


def test_when_every_thread_is_incomplete_the_door_is_asked_once(monkeypatch, capsys):
    today = _today_thread_reads(monkeypatch)
    kids, epics, threads = _epic_world()
    _gate_stubs(monkeypatch)
    linear = ThreadLinear(*kids, *epics, threads=threads)
    with door_at(monkeypatch, *kids, *epics) as door, wired(linear):
        door.incomplete_threads = {_EPIC_A, _EPIC_B}
        assert reconcile.promote_ready(active_count=0) == 2
    assert len(_all_reads(door)) == 1  # nothing left to re-ask
    assert linear.thread_reads() == today
    assert ("promotion: epic threads — 0 from the door in one read, 2 read from Linear "
            f"(thread-incomplete: {_EPIC_A}, {_EPIC_B})") in capsys.readouterr().out
