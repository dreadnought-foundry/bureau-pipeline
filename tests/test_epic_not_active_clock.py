"""A child waiting on an epic in Planning is clocked when its plan run is dead (DRE-8815).

On 2026-11-01 epic DRE-8698 entered Planning, and its plan run died in a shell
step after posting the plan. Nothing moved the epic on. Each of its nine
children carried the ordinary `🧾 promotion-wait: epic-not-active —` receipt,
`promotion_stall` read that group as a declared wait, and nothing else was said
for five hours.

This file pins the rule that ends it. The pure half is `promotion_stall`'s
epic record: the run table, the lane table, the clock (the later of the oldest
child receipt and the newest plan run's end) and the receipt's facts. The
sweep half drives `reconcile.promote_ready(clock_epics=True)` over a fake
board: nine children waiting on one epic, one receipt on the epic and none on
a child, one ledger entry per pass, and one console alert keyed on the epic.

Run: cd bureau-pipeline && python3 -m pytest tests/test_epic_not_active_clock.py -v
"""
from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")

import dead_run  # noqa: E402
import lane_contract  # noqa: E402
import promotion_stall  # noqa: E402
import promotion_wait  # noqa: E402
import reconcile  # noqa: E402
import standing_defect_alert  # noqa: E402
from promotion_stall import EpicWait, PlanRun  # noqa: E402

T0 = datetime(2026, 11, 2, 0, 45, tzinfo=UTC)  # Nov 1, 5:45 PM PT
NOW = T0 + timedelta(minutes=140)


def iso(at: datetime) -> str:
    return at.strftime("%Y-%m-%dT%H:%M:%SZ")


def ago(minutes: float) -> str:
    return iso(NOW - timedelta(minutes=minutes))


def failed(minutes_ago: float = 150, conclusion: str = "failure", run_id: str = "41363833540"):
    return PlanRun(run_id, "completed", conclusion, ago(minutes_ago))


def wait(*, lane="Planning", children=9, receipt=200.0, run=None) -> EpicWait:
    return EpicWait("DRE-8698", lane, children,
                    None if receipt is None else ago(receipt), run)


def registry_tags() -> list[str]:
    doc = json.loads((ROOT / "config" / "pipeline-acts.json").read_text(encoding="utf-8"))
    return [act["tag"] for act in doc["acts"]]


# --------------------------------------------------------------------------- #
# The pinned sets                                                             #
# --------------------------------------------------------------------------- #


def test_clocked_tags_are_the_six_of_before_plus_epic_not_active():
    assert promotion_stall.CLOCKED_TAGS == (
        "routing-no-verdict", "mid-epic-no-verdict",
        "plan-critic-post-unread", "plan-critic-post-sent-back",
        "plan-critic-post-died", "epic-cap-undecided", "epic-not-active",
    )


def test_of_the_promotion_wait_reasons_only_epic_not_active_is_clocked():
    clocked = [r for r in promotion_wait.REASONS if promotion_stall.clocked(r)]
    assert clocked == [promotion_wait.EPIC_NOT_ACTIVE]
    assert promotion_stall.EPIC_NOT_ACTIVE_TAG == promotion_wait.EPIC_NOT_ACTIVE


def test_the_other_declared_waits_stay_unclocked():
    for tag in ("wave-not-green-lit", "prose-blocker-no-relation"):
        assert promotion_stall.clocked(tag) is False, tag


# --------------------------------------------------------------------------- #
# The run table                                                               #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("conclusion", [
    "failure", "timed_out", "startup_failure", "cancelled", "action_required", "stale",
])
def test_a_completed_run_that_did_not_succeed_is_dead(conclusion):
    assert promotion_stall.run_dead(PlanRun("1", "completed", conclusion, ago(10))) is True


def test_no_run_found_is_dead():
    assert promotion_stall.run_dead(None) is True


@pytest.mark.parametrize("status", sorted(reconcile.IN_FLIGHT_STATUSES))
def test_a_run_still_going_is_not_dead(status):
    assert promotion_stall.run_dead(PlanRun("1", status, "", ago(10))) is False


@pytest.mark.parametrize("conclusion", ["success", "neutral", "skipped"])
def test_a_run_that_finished_well_is_not_dead(conclusion):
    assert promotion_stall.run_dead(PlanRun("1", "completed", conclusion, ago(10))) is False


@pytest.mark.parametrize("run, clocked", [
    (failed(), True),
    (failed(conclusion="cancelled"), True),
    (failed(conclusion="timed_out"), True),
    (None, True),
    (PlanRun("1", "in_progress", "", ago(150)), False),
    (PlanRun("1", "queued", "", ago(150)), False),
    (PlanRun("1", "completed", "success", ago(150)), False),
    (PlanRun("1", "completed", "skipped", ago(150)), False),
])
def test_each_row_of_the_run_table_past_the_window(run, clocked):
    assert (promotion_stall.epic_stalled(wait(run=run), iso(NOW)) is not None) is clocked


# --------------------------------------------------------------------------- #
# The lane table                                                              #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("lane", [
    "Green Light", "Triage", "Intake", "Backlog", "Queued", "Hand-work",
    "In Review", "In Progress", "Done", "Canceled", "Duplicate",
])
def test_no_lane_but_planning_is_ever_clocked_even_with_a_failed_run(lane):
    record = wait(lane=lane, run=failed())
    assert promotion_stall.epic_stalled(record, iso(NOW)) is None
    assert promotion_stall.receipt_aged(record, iso(NOW)) is False


def test_planning_with_a_failed_run_past_the_window_is_clocked():
    assert promotion_stall.epic_stalled(wait(run=failed()), iso(NOW)) is not None


# --------------------------------------------------------------------------- #
# The clock                                                                   #
# --------------------------------------------------------------------------- #


def test_the_clock_is_the_later_of_the_receipt_and_the_runs_end():
    assert promotion_stall.epic_first_seen(wait(receipt=200, run=failed(150))) == ago(150)
    assert promotion_stall.epic_first_seen(wait(receipt=130, run=failed(300))) == ago(130)
    assert promotion_stall.epic_first_seen(wait(receipt=130, run=None)) == ago(130)


def test_the_age_is_the_clocks_and_reaches_stall_minutes():
    age = promotion_stall.epic_stalled(wait(receipt=300, run=failed(150)), iso(NOW))
    assert age == pytest.approx(150.0)


def test_a_rerun_restarts_the_clock_from_the_attempts_end():
    """The medic's one re-run: the run ended 30 minutes ago, the receipt is
    five hours old — the clock reads the run's end, so it is not yet stale."""
    record = wait(receipt=300, run=failed(30))
    assert promotion_stall.receipt_aged(record, iso(NOW)) is True
    assert promotion_stall.epic_stalled(record, iso(NOW)) is None


def test_under_the_window_is_not_clocked_and_stops_at_the_receipt_gate():
    record = wait(receipt=promotion_stall.STALL_MINUTES - 1, run=None)
    assert promotion_stall.receipt_aged(record, iso(NOW)) is False
    assert promotion_stall.epic_stalled(record, iso(NOW)) is None


def test_an_undated_record_is_never_stale():
    record = wait(receipt=None, run=failed(600))
    assert promotion_stall.epic_first_seen(record) is None
    assert promotion_stall.receipt_aged(record, iso(NOW)) is False
    assert promotion_stall.epic_stalled(record, iso(NOW)) is None


# --------------------------------------------------------------------------- #
# The receipt and the ledger line                                             #
# --------------------------------------------------------------------------- #


def test_the_notice_names_the_lane_the_children_and_the_dead_run():
    record = wait(run=failed(140))
    body = promotion_stall.epic_notice(record, iso(NOW))
    assert body.startswith(promotion_stall.STALL_MARK + " DRE-8698")
    assert "Planning" in body
    assert "9 children" in body
    assert lane_contract.QUEUE_LANE in body
    assert "41363833540" in body
    assert "failure" in body
    assert dead_run.pacific(datetime.fromisoformat(ago(140).replace("Z", "+00:00"))) in body
    assert "2.3 hours ago" in body


def test_the_notice_for_no_run_says_none_was_found_among_the_newest_50():
    body = promotion_stall.epic_notice(wait(run=None), iso(NOW))
    assert "no plan run" in body.lower()
    assert f"newest {promotion_stall.PLAN_RUNS_PAGE}" in body
    assert promotion_stall.PLAN_RUNS_PAGE == 50


def test_one_child_reads_in_the_singular():
    body = promotion_stall.epic_notice(wait(children=1, run=failed()), iso(NOW))
    assert "1 child " in body


def test_the_once_key_carries_the_epic_and_the_run_and_opens_the_notice():
    one = wait(run=failed(run_id="111"))
    two = wait(run=failed(run_id="222"))
    none = wait(run=None)
    keys = [promotion_stall.epic_once_key(r) for r in (one, two, none)]
    assert len(set(keys)) == 3
    for record, key in zip((one, two, none), keys):
        assert "DRE-8698" in key
        assert promotion_stall.epic_notice(record, iso(NOW)).startswith(key)
    assert "111" in keys[0] and "222" in keys[1] and "no-run" in keys[2]


def test_the_notice_carries_no_other_acts_tag():
    tags = [t for t in registry_tags() if t != promotion_stall.STALL_TAG]
    for record in (wait(run=failed()), wait(run=None)):
        body = promotion_stall.epic_notice(record, iso(NOW))
        for tag in tags:
            assert tag not in body, tag


def test_the_ledger_line_names_the_epic_for_the_standing_defect_alert():
    record = wait(run=failed(150))
    line = promotion_stall.epic_ledger_line(record, 150.0)
    assert line.startswith(f"{promotion_stall.LEDGER_STALL_OPENER}DRE-8698:")
    assert standing_defect_alert.card_of(line) == "DRE-8698"
    assert "9 children" in line and "41363833540" in line and "2.5h" in line
    assert "\n" not in line
    assert "newest 50" in promotion_stall.epic_ledger_line(wait(run=None), 150.0)


# --------------------------------------------------------------------------- #
# The sweep                                                                   #
# --------------------------------------------------------------------------- #

EPIC, OTHER = "DRE-8698", "DRE-8699"
PLAN_RUN = "41363833540"
SUCCESS_RUN = "41363833999"
CHILDREN = tuple(f"DRE-{8725 + i}" for i in range(9))
OTHER_CHILDREN = tuple(f"DRE-{8740 + i}" for i in range(9))
STALL_MARK = promotion_stall.STALL_MARK
WAIT_KEY = promotion_wait.key(promotion_wait.EPIC_NOT_ACTIVE)


class _Clock(datetime):
    frozen = T0

    @classmethod
    def now(cls, tz=None):
        return cls.frozen


def _child(identifier, epic=EPIC, lane="Planning", repo="bureau-pipeline"):
    return {
        "id": f"uuid-{identifier}",
        "identifier": identifier,
        "title": f"Build piece {identifier}",
        "description": "Add it.\n\n## Acceptance criteria\n\n- [ ] it renders\n",
        "createdAt": "2026-10-01T00:00:00.000Z",
        "parent": {"identifier": epic, "state": {"name": lane}},
        "labels": {"nodes": [{"name": f"repo:{repo}"}]},
        "comments": [],
        "inverseRelations": {"nodes": []},
    }


class _Board:
    """`promote_ready` over a Queued lane of children waiting on epics, with
    the epics, their threads and the plan stub's runs as fixtures."""

    def __init__(self, *, epics=None, runs=None, jobs=None, listing_error=None,
                 jobs_error=None, epic_comments=None):
        self.children: list[dict] = []
        # epic -> {"lane": ..., "labels": [...]}
        self.epics = epics or {EPIC: {"lane": "Planning", "labels": ["repo:bureau-pipeline"]}}
        for epic, facts in self.epics.items():
            kids = CHILDREN if epic == EPIC else OTHER_CHILDREN
            for kid in kids:
                self.children.append(_child(kid, epic, facts["lane"],
                                            facts.get("child_repo", "bureau-pipeline")))
        # Newest first, as `gh run list` answers.
        self.runs = runs if runs is not None else [
            {"databaseId": int(PLAN_RUN), "status": "completed", "conclusion": "failure",
             "createdAt": iso(T0 - timedelta(hours=2)), "updatedAt": iso(T0),
             "url": f"https://github.com/x/actions/runs/{PLAN_RUN}"},
        ]
        self.jobs = jobs if jobs is not None else {PLAN_RUN: [f"call / bureau-card: {EPIC}"]}
        self.listing_error = listing_error
        self.jobs_error = jobs_error
        self.epic_comments = epic_comments or {}
        self.posted: list[tuple[str, str, str]] = []
        self.issue_reads: list[str] = []
        self.listings = 0
        self.defects: list[str] = []
        self.unread: list[str] = []
        self.out = ""
        self.err = ""

    def comments(self, identifier):
        base = list(self.epic_comments.get(identifier, []))
        return base + [{"body": b, "createdAt": t, "user": {"id": "u-pipeline"}}
                       for i, b, t in self.posted if i == identifier]

    def _queued(self):
        return [{**c, "comments": {"nodes": self.comments(c["identifier"])[::-1]}}
                for c in self.children]

    def _actions_read(self, args):
        if args[:2] == ("run", "list"):
            self.listings += 1
            assert "--workflow" in args and args[args.index("--workflow") + 1] == "Agent Plan"
            if self.listing_error:
                return None, self.listing_error
            return json.dumps(self.runs), None
        if args[0] == "api" and args[1].endswith("/jobs"):
            run_id = args[1].split("/")[-2]
            if self.jobs_error:
                return None, self.jobs_error
            return json.dumps({"jobs": [{"name": n} for n in self.jobs.get(run_id, [])]}), None
        raise AssertionError(f"unexpected Actions read {args}")

    def sweep(self, minutes, capsys, *, clock_epics=True):
        now = T0 + timedelta(minutes=minutes)
        _Clock.frozen = now
        reconcile._stale_defects.clear()
        reconcile._write_failures.clear()
        reconcile._ledger_unread.clear()
        reconcile.reset_sweep_cards()

        def count(identifier, needle, **_kw):
            return sum(1 for c in self.comments(identifier) if needle in (c["body"] or ""))

        def first_at(identifier, needle):
            for c in self.comments(identifier):
                if needle in (c["body"] or ""):
                    return c["createdAt"]
            return None

        def post(identifier, body):
            self.posted.append((identifier, body, iso(now)))

        def get_issue(identifier, **_kw):
            self.issue_reads.append(identifier)
            facts = self.epics[identifier]
            return {"identifier": identifier, "state": {"name": facts["lane"]},
                    "labels": {"nodes": [{"name": n} for n in facts["labels"]]}}

        def records(identifier, **_kw):
            return [{"body": c["body"], "authored_by_pipeline": True,
                     "created_at": c["createdAt"]} for c in self.comments(identifier)]

        kwargs = {"clock_epics": True} if clock_epics else {}
        with patch.object(reconcile, "REPO_SLUG", "bureau-pipeline"), \
                patch.object(reconcile, "datetime", _Clock), \
                patch.object(reconcile, "backlog_children", return_value=self._queued()), \
                patch.object(reconcile, "epic_records", return_value={}), \
                patch.object(reconcile, "_door_epic_threads", return_value=None), \
                patch.object(reconcile, "_actions_read", side_effect=self._actions_read), \
                patch.object(reconcile.linear_ops, "get_issue", side_effect=get_issue), \
                patch.object(reconcile.linear_ops, "comment_records", side_effect=records), \
                patch.object(reconcile.linear_ops, "cmd_comment", side_effect=post), \
                patch.object(reconcile.linear_ops, "cmd_advance"), \
                patch.object(reconcile.linear_ops, "add_label"), \
                patch.object(reconcile.linear_ops, "count_comments", side_effect=count), \
                patch.object(reconcile.linear_ops, "first_comment_at", side_effect=first_at):
            capsys.readouterr()
            reconcile.promote_ready(active_count=0, **kwargs)
            captured = capsys.readouterr()
        self.defects = list(reconcile._stale_defects)
        self.unread = list(reconcile._ledger_unread)
        self.out, self.err = captured.out, captured.err
        return self

    def run(self, capsys, *sweeps, **kwargs):
        for minutes in sweeps or (0, 125):
            self.sweep(minutes, capsys, **kwargs)
        return self

    def stalls_on(self, identifier):
        return [b for i, b, _ in self.posted if i == identifier and b.startswith(STALL_MARK)]

    def entries_for(self, epic):
        return [e for e in self.defects
                if e.startswith(f"{promotion_stall.LEDGER_STALL_OPENER}{epic}:")]


@pytest.fixture(autouse=True)
def _clean_ledgers():
    for ledger in (reconcile._stale_defects, reconcile._write_failures,
                   reconcile._ledger_unread):
        ledger.clear()
    yield
    for ledger in (reconcile._stale_defects, reconcile._write_failures,
                   reconcile._ledger_unread):
        ledger.clear()
    reconcile.reset_sweep_cards()


def _stalled(board, epic=EPIC):
    return bool(board.stalls_on(epic)) or bool(board.entries_for(epic))


class TestNineChildrenOnADeadPlanRun:
    @pytest.fixture
    def board(self, capsys):
        return _Board().run(capsys)

    def test_every_child_carries_its_ordinary_wait_receipt(self, board):
        for kid in CHILDREN:
            assert [b for i, b, _ in board.posted if i == kid and b.startswith(WAIT_KEY)], kid

    def test_one_receipt_on_the_epic_and_none_on_any_child(self, board):
        stalls = board.stalls_on(EPIC)
        assert len(stalls) == 1
        body = stalls[0]
        assert "9 children" in body
        assert PLAN_RUN in body and "failure" in body and "Planning" in body
        assert "promotion-stalled" in body.split("\n\n")[-1]  # the registry trailer
        for kid in CHILDREN:
            assert board.stalls_on(kid) == [], kid

    def test_one_ledger_entry_names_the_epic(self, board):
        entries = board.entries_for(EPIC)
        assert len(entries) == 1
        assert "9 children" in entries[0]
        assert f"ERROR: {entries[0]}" in board.err
        assert not [e for e in board.defects
                    if any(e.startswith(f"{promotion_stall.LEDGER_STALL_OPENER}{k}:")
                           for k in CHILDREN)]

    def test_a_second_sweep_posts_nothing_new_and_still_appends_one_entry(self, board, capsys):
        board.sweep(140, capsys)
        assert len(board.stalls_on(EPIC)) == 1
        assert len(board.entries_for(EPIC)) == 1

    def test_the_first_sweep_under_the_window_says_nothing(self, capsys):
        board = _Board().sweep(0, capsys)
        assert board.stalls_on(EPIC) == [] and board.entries_for(EPIC) == []
        # Cheapest first: under the window, neither the epic nor Actions is read.
        assert board.issue_reads == [] and board.listings == 0

    def test_the_console_alert_is_one_raise_keyed_on_the_epic(self, board):
        raised = []

        def raise_notice(key, **kw):
            raised.append((key, kw))
            return "raised"

        with patch.object(standing_defect_alert.pipeline_notice, "raise_notice",
                          side_effect=raise_notice), \
                patch.object(standing_defect_alert.bureau_read, "open_alerts",
                             return_value={"alerts": []}), \
                patch.object(standing_defect_alert.pipeline_notice, "resolve_notice") as resolve:
            standing_defect_alert.settle(board.defects, "bureau-pipeline", unread=board.unread)
        assert [k for k, _ in raised] == [f"standing-defect:bureau-pipeline:{EPIC}"]
        assert raised[0][1]["fallback"] is None  # files no Linear card
        resolve.assert_not_called()


def test_a_promote_only_pass_never_clocks_the_epic(capsys):
    board = _Board().run(capsys, 0, 125, clock_epics=False)
    assert not _stalled(board)
    assert board.issue_reads == [] and board.listings == 0


def test_side_by_side_a_failed_run_is_clocked_and_a_successful_one_is_not(capsys):
    planning = {"lane": "Planning", "labels": ["repo:bureau-pipeline"]}
    board = _Board(
        epics={EPIC: dict(planning), OTHER: dict(planning)},
        runs=[
            {"databaseId": int(SUCCESS_RUN), "status": "completed", "conclusion": "success",
             "createdAt": iso(T0 - timedelta(hours=1)), "updatedAt": iso(T0), "url": ""},
            {"databaseId": int(PLAN_RUN), "status": "completed", "conclusion": "failure",
             "createdAt": iso(T0 - timedelta(hours=2)), "updatedAt": iso(T0), "url": ""},
        ],
        jobs={SUCCESS_RUN: [f"call / bureau-card: {OTHER}"],
              PLAN_RUN: [f"call / bureau-card: {EPIC}"]},
    ).run(capsys)
    assert _stalled(board, EPIC)
    assert not _stalled(board, OTHER)
    # One listing for the pass, however many epics are past the gate — and
    # none on the first sweep, whose epics were under it.
    assert board.listings == 1


def _one_run(status, conclusion):
    return [{"databaseId": int(PLAN_RUN), "status": status, "conclusion": conclusion,
             "createdAt": iso(T0 - timedelta(hours=2)), "updatedAt": iso(T0), "url": ""}]


def test_a_canceled_run_is_clocked(capsys):
    board = _Board(runs=_one_run("completed", "cancelled")).run(capsys)
    assert len(board.stalls_on(EPIC)) == 1 and len(board.entries_for(EPIC)) == 1
    assert "cancelled" in board.stalls_on(EPIC)[0]


def test_a_run_never_found_is_clocked_and_says_so(capsys):
    board = _Board(jobs={PLAN_RUN: ["call / bureau-card: DRE-1"]}).run(capsys)
    stalls = board.stalls_on(EPIC)
    assert len(stalls) == 1 and len(board.entries_for(EPIC)) == 1
    assert "newest 50" in stalls[0]
    assert "no-run" in stalls[0].splitlines()[0]


def test_a_run_still_going_is_not_clocked(capsys):
    board = _Board(runs=_one_run("in_progress", "")).run(capsys)
    assert not _stalled(board)
    assert board.unread == []


def test_a_successful_run_is_not_clocked(capsys):
    board = _Board(runs=_one_run("completed", "success")).run(capsys)
    assert not _stalled(board)


def test_an_unreadable_listing_is_not_clocked_and_is_named_unread(capsys):
    board = _Board(listing_error="rc=1: HTTP 502").run(capsys)
    assert not _stalled(board)
    assert any(EPIC in u for u in board.unread), board.unread
    assert sum(1 for ln in board.err.splitlines()
               if ln.startswith("ERROR:") and EPIC in ln) == 1, board.err


def test_an_unreadable_jobs_read_is_not_clocked_and_is_named_unread(capsys):
    board = _Board(jobs_error="rc=1: HTTP 403").run(capsys)
    assert not _stalled(board)
    assert any(EPIC in u for u in board.unread), board.unread


@pytest.mark.parametrize("lane, labels", [
    ("Green Light", ["repo:bureau-pipeline"]),
    ("Green Light", ["repo:bureau-pipeline", "epic-queued"]),
    ("Triage", ["repo:bureau-pipeline"]),
    ("Intake", ["repo:bureau-pipeline"]),
    ("Done", ["repo:bureau-pipeline"]),
])
def test_an_epic_out_of_planning_is_never_clocked_even_with_a_failed_run(capsys, lane, labels):
    board = _Board(epics={EPIC: {"lane": lane, "labels": labels}}).run(capsys)
    assert not _stalled(board)
    assert board.listings == 0


@pytest.mark.parametrize("label", [reconcile.HOLD_LABEL, reconcile.HAND_BUILT_LABEL])
def test_an_epic_a_person_owns_is_not_clocked(capsys, label):
    board = _Board(epics={EPIC: {"lane": "Planning",
                                 "labels": ["repo:bureau-pipeline", label]}}).run(capsys)
    assert not _stalled(board)


def _limit_marker(reset: datetime) -> list[dict]:
    body = dead_run.limit_marker("claude", "plan", reset, "41363833540")
    return [{"body": body, "createdAt": iso(T0 - timedelta(minutes=10)),
             "user": {"id": "u-pipeline"}}]


def test_an_epic_inside_a_limit_death_wait_is_not_clocked(capsys):
    board = _Board(epic_comments={EPIC: _limit_marker(T0 + timedelta(hours=3))}).run(capsys)
    assert not _stalled(board)


def test_an_epic_past_its_limit_death_wait_is_clocked(capsys):
    board = _Board(epic_comments={EPIC: _limit_marker(T0 - timedelta(hours=3))}).run(capsys)
    assert _stalled(board)


def test_an_epic_of_another_repo_is_not_clocked_by_this_sweep(capsys):
    board = _Board(epics={EPIC: {"lane": "Planning", "labels": ["repo:portico"]}}).run(capsys)
    assert not _stalled(board)
    assert board.listings == 0


def test_children_of_another_repo_still_count_toward_this_repos_epic(capsys):
    board = _Board(epics={EPIC: {"lane": "Planning", "labels": ["repo:bureau-pipeline"],
                                 "child_repo": "portico"}})
    # Another repo's sweep posts their wait receipts; this one only reads them.
    for kid in CHILDREN:
        board.posted.append((kid, f"{WAIT_KEY} {kid} waits on its epic.", iso(T0)))
    board.sweep(125, capsys)
    assert len(board.stalls_on(EPIC)) == 1
    assert "9 children" in board.stalls_on(EPIC)[0]


def test_an_epic_with_no_routable_repo_label_is_clocked_by_no_sweep_and_says_so(capsys):
    board = _Board(epics={EPIC: {"lane": "Planning", "labels": []}}).run(capsys)
    assert not _stalled(board)
    lines = [ln for ln in board.out.splitlines() if EPIC in ln and "no sweep" in ln]
    assert len(lines) == 1, board.out
