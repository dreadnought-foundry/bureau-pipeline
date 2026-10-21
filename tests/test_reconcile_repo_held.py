"""RED-first: the sweep tells a held repo's approved epic once, and lifts the
hold before its idle check is spent once the cap is restored (DRE-7755).

DRE-7754 built `repo_held_epics.hold_pass` and `lift_pass`. This suite runs
them where they live: inside `reconcile.main()`'s full pass, straight after
`sweep_idle()`.

  * Held at 0, as the live sweep runs: `MAX_WIP` is the stub's own "0",
    handed in as the input, and `GITHUB_WORKSPACE` is a checkout whose
    reconcile stub carries the hold comment. The idle line is printed, then
    the `repo-held:` lines, and the epic gets exactly one receipt quoting the
    stub's path and comment. A second pass over the board as the first left
    it writes nothing.
  * Held with no approved epic: one Linear request for `hold_pass`, no write.
  * Cap above 0 and not idle: `lift_pass` runs before the backstops and the
    board read, so the cards it lifts reach promotion unheld on this pass —
    whether the idle check was asked of Linear or of the read door. At the
    door, a Queued card held `wip-cap-held` counts as waiting, and one held
    for anything else is still skipped, as before.
  * Idle at a cap above 0, and every `--promote-only` pass, call neither.
  * A failed read prints one line and fails nothing; a failed write lands on
    `_write_failures`, as every other phase's does.

Nothing here reaches Linear or GitHub: one fake answers every query the
pass sends and records every write, and a query it does not know fails the
test by name.

Run: cd bureau-pipeline && python3 -m pytest tests/test_reconcile_repo_held.py -v
"""
from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau-demo")
os.environ.setdefault("REPO_SLUG", "agent-bureau-demo")
os.environ.setdefault("GH_TOKEN", "x")

import bureau_read  # noqa: E402
import hold  # noqa: E402
import linear_ops  # noqa: E402
import reconcile  # noqa: E402
import repo_held_epics as rhe  # noqa: E402
import routing_verdict  # noqa: E402
import validate_card  # noqa: E402
from bureau_read_fakes import FakeDoor, FakeIssuer, door_env  # noqa: E402
from bureau_read_fakes import card as door_card  # noqa: E402

SLUG = "agent-bureau-demo"
EPIC = "DRE-7600"
KIDS = ("DRE-7601", "DRE-7602")
STUB = ".github/workflows/reconcile.yml"
HOLD_COMMENT = (
    "HELD at 0 on 2026-09-08 by CEO decision — this sandbox builds nothing "
    "until the CEO lifts it. Restore it to resume building here."
)
USES = "dreadnought-foundry/bureau-pipeline/.github/workflows/reconcile.yml@stable"
APPROVED = "2026-10-19T10:00:00.000Z"
BEFORE = "2026-10-18T09:00:00.000Z"
FLEET = routing_verdict.verdict_comment("FLEET", "an ordinary code card")
WIP_CAP_STAMP = hold.stamp_line(rhe.HOLD_REASON, None, rhe.WRITER)
#: A hold the sweep stands down for that is not the cap's.
OTHER_REASON, OTHER_WRITER = "dead-run-cap", "scripts/dead_run.py"
OTHER_STAMP = hold.stamp_line(OTHER_REASON, None, OTHER_WRITER)

#: The backstops and phases that are not under test. Each reads GitHub, or a
#: fleet-wide lane this board does not stand in for; stood down, so what the
#: fake counts is the idle check, the two passes and the board reads.
STOOD_DOWN = (
    "drain_retiring_lanes", "unstick_conflicts", "refresh_stale_merge_refs",
    "retrigger_dead_heads", "resync_desynced_heads", "flag_no_checks_prs",
    "flag_unowned_prs", "name_unmovable_proof_records", "flag_unlanded_work",
    "flag_stranded_fixes", "fix_approved_but_red", "retry_dead_fix_runs",
    "redispatch_standing_verdicts", "recover_limit_deaths", "rerun_runner_lost_ci",
    "restart_answered_blockers", "card_dependabot_prs", "review_dependabot_prs",
    "recover_crashed_reviews", "report_fleet_reviewer_outage",
    "check_dependabot_capacity", "settle_repair_cards", "report_fix_concurrency",
    "report_evicted_fix_runs", "flag_stranded", "report_intake_depth",
    "advance_urgent_intake", "report_intake_blockers", "repair_frozen_planning_holds",
    "serve_planner_line", "release_groom_queue", "move_hand_built_to_review",
    "carry_epics_out_of_todo", "report_break_glass", "report_epic_growth",
    "close_finished_epics", "tend_epic_queue", "start_queued_epics",
    "alarm_held_proofs", "return_waiting_epics", "pr_for", "agent_run_alive",
    "redeliver_rescued_work", "build_run_refusal", "redispatch",
)


# --------------------------------------------------------------------------
# The caller's checkout
# --------------------------------------------------------------------------
def _checkout(tmp_path: Path, cap: str = '"0"') -> str:
    """A scratch checkout whose reconcile stub passes `max_wip: <cap>` under
    the hold comment, the shape agent-bureau-demo's stub has."""
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "reconcile.yml").write_text(
        "name: Reconcile\n"
        "on:\n"
        "  schedule:\n"
        "    - cron: \"*/15 * * * *\"\n"
        "jobs:\n"
        "  call:\n"
        f"    uses: {USES}\n"
        "    with:\n"
        "      pipeline_ref: stable\n"
        f"      # {HOLD_COMMENT}\n"
        f"      max_wip: {cap}\n"
        "    secrets: inherit\n"
    )
    return str(tmp_path)


def _as_the_live_sweep(monkeypatch, workspace: str, cap: str) -> None:
    """The reusable reconcile workflow hands the stub's literal in as
    `MAX_WIP`; the module-level cap was read from it at import."""
    monkeypatch.setenv("GITHUB_WORKSPACE", workspace)
    monkeypatch.setenv("MAX_WIP", cap)
    monkeypatch.delenv("BUREAU_MAX_WIP", raising=False)
    value, source = reconcile.cap_and_source(cap, workspace, None)
    monkeypatch.setattr(reconcile, "MAX_WIP", value)
    monkeypatch.setattr(reconcile, "MAX_WIP_SOURCE", source)


# --------------------------------------------------------------------------
# One Linear
# --------------------------------------------------------------------------
class Board:
    """Every query a full pass sends, answered from `cards`; every write
    recorded and applied, so a second pass reads what the first left."""

    def __init__(self):
        self.cards: dict[str, dict] = {}
        self.queries: list[str] = []
        self.writes: list[tuple] = []
        self.fail_reads = False
        self.fail_comments = False
        self._tick = 0

    @property
    def requests(self) -> int:
        return len(self.queries)

    def add(self, identifier, lane, *, labels=(), comments=(), parent=None,
            children=False, history=()):
        self.cards[identifier] = {
            "identifier": identifier, "lane": lane, "labels": list(labels),
            "comments": [{"body": b, "createdAt": at} for b, at in comments],
            "parent": parent, "children": children,
            "history": [{"createdAt": at, "toState": {"name": n}} for at, n in history],
        }

    def epic(self, identifier=EPIC, kids=KIDS, *, kid_labels=(), kid_comments=()):
        self.add(identifier, "In Progress", labels=[f"repo:{SLUG}"], children=True,
                 history=((APPROVED, "In Progress"),))
        for kid in kids:
            self.add(kid, "Queued", labels=[f"repo:{SLUG}", *kid_labels],
                     comments=((FLEET, BEFORE), *kid_comments), parent=identifier)

    # -- reads ------------------------------------------------------------ #
    def node(self, c):
        newest_first = list(reversed(c["comments"]))
        parent = self.cards.get(c["parent"]) if c["parent"] else None
        return {
            "id": f"uuid-{c['identifier']}", "identifier": c["identifier"],
            "title": f"Card {c['identifier']}", "description": "work",
            "createdAt": BEFORE, "updatedAt": BEFORE, "priority": 0,
            "state": {"name": c["lane"]},
            "labels": {"nodes": [{"name": n} for n in c["labels"]]},
            "parent": ({"id": f"uuid-{parent['identifier']}",
                        "identifier": parent["identifier"],
                        "state": {"name": parent["lane"]}} if parent else None),
            "children": {"nodes": [{"id": "kid"}] if c["children"] else []},
            "history": {"nodes": c["history"]},
            "comments": {"pageInfo": {"hasNextPage": False, "endCursor": None},
                         "nodes": [dict(n, user={"id": "bot"}) for n in newest_first]},
            "inverseRelations": {"pageInfo": {"hasNextPage": False}, "nodes": []},
            "relations": {"pageInfo": {"hasNextPage": False}, "nodes": []},
        }

    def _page(self, cards):
        return {"issues": {"nodes": [self.node(c) for c in cards],
                           "pageInfo": {"hasNextPage": False, "endCursor": None}}}

    @staticmethod
    def _wears(c, needle):
        return any(needle.lower() in n.lower() for n in c["labels"])

    def gql(self, query, variables=None):
        self.queries.append(query)
        v = variables or {}
        if self.fail_reads and query in (rhe.EPICS_QUERY, rhe.CHILDREN_QUERY, rhe.HELD_QUERY):
            raise linear_ops.LinearError("linear error from issues: 502")
        cards = list(self.cards.values())
        if query in (rhe.EPICS_QUERY, reconcile.IDLE_QUERY):
            return self._page([c for c in cards if c["lane"] in v["states"]
                               and self._wears(c, v["needle"])])
        if query == rhe.CHILDREN_QUERY:
            ids = set(v["ids"])
            return self._page([c for c in cards if c["lane"] in v["states"]
                               and c["parent"] and f"uuid-{c['parent']}" in ids])
        if query == rhe.HELD_QUERY:
            return self._page([c for c in cards if c["lane"] in v["states"]
                               and self._wears(c, v["needle"])
                               and hold.HOLD_LABEL in c["labels"]])
        q = " ".join(query.split())
        if reconcile.QUEUE_STATE_FILTER in q:
            return self._page([c for c in cards if c["lane"] in reconcile.QUEUE_LANES])
        if "state: {name: {in: $states}}" in q:
            return self._page([c for c in cards if c["lane"] in v["states"]])
        raise AssertionError(f"a Linear read this pass must not send: {q[:160]}")

    # -- writes ----------------------------------------------------------- #
    def add_label(self, identifier, label, *a, **k):
        self.writes.append(("label+", identifier, label))
        if label not in self.cards[identifier]["labels"]:
            self.cards[identifier]["labels"].append(label)

    def remove_label(self, identifier, label, *a, **k):
        self.writes.append(("label-", identifier, label))
        self.cards[identifier]["labels"] = [
            n for n in self.cards[identifier]["labels"] if n.lower() != label.lower()]

    def cmd_comment(self, identifier, body, *flags):
        if self.fail_comments:
            raise linear_ops.LinearError("commentCreate: 500 from Linear")
        self._tick += 1
        self.writes.append(("comment", identifier, body))
        self.cards[identifier]["comments"].append(
            {"body": body, "createdAt": f"2026-10-21T00:00:{self._tick:02d}.000Z"})

    # -- what the tests read ---------------------------------------------- #
    def receipts(self, identifier=EPIC):
        return [b for kind, card, b in self.writes
                if kind == "comment" and card == identifier and b.startswith(rhe.RECEIPT_PREFIX)]

    def module_reads(self):
        return [q for q in self.queries
                if q in (rhe.EPICS_QUERY, rhe.CHILDREN_QUERY, rhe.HELD_QUERY)]

    def held(self, identifier):
        return reconcile.held(self.node(self.cards[identifier]))


# --------------------------------------------------------------------------
# One pass
# --------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _demo(monkeypatch):
    for name in ("BUREAU_READ", "BUREAU_READ_URL", "MERGED_CARD", "BUREAU_MAX_WIP",
                 "ACTIONS_ID_TOKEN_REQUEST_URL", "ACTIONS_ID_TOKEN_REQUEST_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(reconcile, "REPO_SLUG", SLUG)
    monkeypatch.setattr(validate_card, "VALID_SLUGS", {SLUG, "portico"})
    for ledger in (reconcile._write_failures, reconcile._read_failures,
                   reconcile._stale_defects):
        ledger.clear()
    bureau_read.reset_for_tests()
    yield
    bureau_read.reset_for_tests()
    for ledger in (reconcile._write_failures, reconcile._read_failures,
                   reconcile._stale_defects):
        ledger.clear()


class Promotion:
    """`promote_ready`, recorded: whether it ran, and whether each card was
    still held at that moment."""

    def __init__(self, board: Board, watch=KIDS):
        self.board = board
        self.watch = watch
        self.calls = 0
        self.held_then: dict[str, bool] = {}

    def __call__(self, *a, **k):
        self.calls += 1
        self.held_then = {i: self.board.held(i) for i in self.watch if i in self.board.cards}
        return 0


def _pass(board: Board, *, promote_only=False, promotion=None, spies=()):
    """One `reconcile.main()` over `board`. Returns the spies by name."""
    promotion = promotion or Promotion(board)
    with contextlib.ExitStack() as stack:
        for name in STOOD_DOWN:
            stack.enter_context(mock.patch.object(reconcile, name))
        stack.enter_context(mock.patch.object(reconcile, "promote_ready", promotion))
        stack.enter_context(mock.patch.object(
            reconcile, "rereview_watch_scope", return_value=(set(), lambda e: None)))
        stack.enter_context(mock.patch.object(
            reconcile.subprocess, "run",
            side_effect=AssertionError("a real subprocess on this pass")))
        # The GitHub listings the PR-side phases read first: empty, so they
        # find no pull request to act on.
        for listing in ("_dependabot_pr_listing", "_open_pr_listing", "_build_runs_in_flight"):
            stack.enter_context(mock.patch.object(reconcile, listing, return_value=[]))
        stack.enter_context(mock.patch.object(linear_ops, "gql", side_effect=board.gql))
        stack.enter_context(mock.patch.object(linear_ops, "requests_made",
                                              lambda: board.requests))
        stack.enter_context(mock.patch.object(linear_ops, "add_label", board.add_label))
        stack.enter_context(mock.patch.object(linear_ops, "remove_label", board.remove_label))
        stack.enter_context(mock.patch.object(linear_ops, "cmd_comment", board.cmd_comment))
        stack.enter_context(mock.patch.object(linear_ops, "cmd_advance"))
        stack.enter_context(mock.patch.object(linear_ops, "cmd_state"))
        stack.enter_context(mock.patch.object(
            linear_ops, "comment_timeline",
            side_effect=AssertionError("no thread here is past its window")))
        spied = {name: stack.enter_context(mock.patch.object(
            rhe, name, wraps=getattr(rhe, name))) for name in spies}
        reconcile.main(promote_only=promote_only)
    spied["promotion"] = promotion
    return spied


def _out_lines(capsys, prefix):
    return [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith(prefix)]


# --------------------------------------------------------------------------
# Held at 0
# --------------------------------------------------------------------------
def test_held_at_zero_as_the_live_sweep_runs_posts_one_notice(monkeypatch, tmp_path, capsys):
    _as_the_live_sweep(monkeypatch, _checkout(tmp_path), "0")
    board = Board()
    board.epic()
    _pass(board)
    out = capsys.readouterr().out.splitlines()
    idle = [ln for ln in out if ln.startswith("idle:")]
    assert idle == [ln for ln in idle if "the WIP cap is 0" in ln] and len(idle) == 1
    held = [ln for ln in out if ln.startswith(rhe.LOG_PREFIX)]
    assert held == [f"{rhe.LOG_PREFIX} {EPIC} — notice posted; held {', '.join(KIDS)}"]
    # The idle line first, then the pass's own lines: both calls come AFTER
    # `sweep_idle()`.
    assert out.index(idle[0]) < out.index(held[0])
    receipts = board.receipts()
    assert len(receipts) == 1
    assert f"`{STUB}`" in receipts[0]
    assert HOLD_COMMENT in receipts[0]
    for kid in KIDS:
        assert board.held(kid)
    assert reconcile._write_failures == []
    assert f"sweep-spend: post_repo_held_notice {len(board.module_reads())} request(s)" in out


def test_a_second_held_pass_says_the_notice_already_stands(monkeypatch, tmp_path, capsys):
    _as_the_live_sweep(monkeypatch, _checkout(tmp_path), "0")
    board = Board()
    board.epic()
    _pass(board)
    capsys.readouterr()
    writes = len(board.writes)
    _pass(board)
    lines = _out_lines(capsys, rhe.LOG_PREFIX)
    assert lines == [f"{rhe.LOG_PREFIX} {EPIC} — notice already stands; held none"]
    assert len(board.writes) == writes
    assert len(board.receipts()) == 1


def test_held_with_no_approved_epic_reads_once_and_writes_nothing(monkeypatch, tmp_path, capsys):
    _as_the_live_sweep(monkeypatch, _checkout(tmp_path), "0")
    board = Board()
    board.add("DRE-7700", "Queued", labels=[f"repo:{SLUG}"], comments=((FLEET, BEFORE),))
    _pass(board)
    assert board.module_reads() == [rhe.EPICS_QUERY]
    assert board.writes == []
    out = capsys.readouterr().out
    assert f"{rhe.LOG_PREFIX} no approved epic of {SLUG} is In Progress; nothing written" in out
    assert "sweep-spend: post_repo_held_notice 1 request(s)" in out


def test_a_held_pass_never_lifts(monkeypatch, tmp_path):
    _as_the_live_sweep(monkeypatch, _checkout(tmp_path), "0")
    board = Board()
    board.epic()
    spied = _pass(board, spies=("hold_pass", "lift_pass"))
    assert spied["hold_pass"].call_count == 1
    assert spied["lift_pass"].call_count == 0
    slug, holds = spied["hold_pass"].call_args.args
    assert slug == SLUG and holds.max_wip_held and holds.stub_path == STUB


def test_a_failed_read_on_a_held_pass_says_so_and_fails_nothing(monkeypatch, tmp_path, capsys):
    _as_the_live_sweep(monkeypatch, _checkout(tmp_path), "0")
    board = Board()
    board.epic()
    board.fail_reads = True
    _pass(board)
    lines = _out_lines(capsys, rhe.LOG_PREFIX)
    assert len(lines) == 1 and "could not be read" in lines[0], lines
    assert board.writes == []
    assert reconcile._write_failures == []


def test_a_failed_write_on_a_held_pass_is_a_write_failure(monkeypatch, tmp_path):
    _as_the_live_sweep(monkeypatch, _checkout(tmp_path), "0")
    board = Board()
    board.epic()
    board.fail_comments = True
    with pytest.raises(SystemExit):
        _pass(board)
    assert len(reconcile._write_failures) == 1
    assert "repo-held" in reconcile._write_failures[0]


# --------------------------------------------------------------------------
# Cap above 0
# --------------------------------------------------------------------------
def _held_kids(board: Board, reason=rhe.HOLD_REASON, by=rhe.WRITER):
    stamp = hold.stamp_line(reason, None, by)
    board.epic(kid_labels=[hold.HOLD_LABEL], kid_comments=((stamp, APPROVED),))


def test_unheld_at_eight_writes_nothing_and_promotes_as_before(monkeypatch, tmp_path, capsys):
    _as_the_live_sweep(monkeypatch, _checkout(tmp_path, '"8"'), "8")
    board = Board()
    board.epic()
    spied = _pass(board, spies=("hold_pass", "lift_pass"))
    assert spied["hold_pass"].call_count == 0
    assert spied["lift_pass"].call_count == 1
    assert board.writes == []
    assert board.module_reads() == [rhe.HELD_QUERY]
    assert spied["promotion"].calls == 1
    assert spied["promotion"].held_then == {kid: False for kid in KIDS}
    out = capsys.readouterr().out
    assert not [ln for ln in out.splitlines() if ln.startswith("idle:")]
    assert "sweep-spend: lift_wip_cap_holds 1 request(s)" in out


def test_restored_with_the_linear_idle_check_lifts_before_promotion(monkeypatch, tmp_path, capsys):
    _as_the_live_sweep(monkeypatch, _checkout(tmp_path, '"8"'), "8")
    board = Board()
    _held_kids(board)
    spied = _pass(board, spies=("lift_pass",))
    out = capsys.readouterr().out.splitlines()
    assert not [ln for ln in out if ln.startswith("idle:")]
    assert board.queries[0] == reconcile.IDLE_QUERY
    assert board.queries[1] == rhe.HELD_QUERY  # after the idle check, before the board read
    assert spied["lift_pass"].call_args.args == (SLUG, 8)
    assert [ln for ln in out if ln.startswith(rhe.LOG_PREFIX)] == [
        f"{rhe.LOG_PREFIX} lifted {kid} (cap-restored, max_wip 8)" for kid in KIDS]
    assert spied["promotion"].calls == 1
    assert spied["promotion"].held_then == {kid: False for kid in KIDS}
    for kid in KIDS:
        assert hold.HOLD_LABEL not in board.cards[kid]["labels"]


def test_restored_lifts_only_wip_cap_holds(monkeypatch, tmp_path):
    _as_the_live_sweep(monkeypatch, _checkout(tmp_path, '"8"'), "8")
    board = Board()
    _held_kids(board, reason=OTHER_REASON, by=OTHER_WRITER)
    spied = _pass(board)
    assert board.writes == []
    assert spied["promotion"].held_then == {kid: True for kid in KIDS}


def test_idle_at_a_cap_above_zero_does_not_lift(monkeypatch, tmp_path, capsys):
    _as_the_live_sweep(monkeypatch, _checkout(tmp_path, '"8"'), "8")
    board = Board()
    board.add("DRE-7700", "Planning", labels=[f"repo:{SLUG}"])
    spied = _pass(board, spies=("hold_pass", "lift_pass"))
    assert len(_out_lines(capsys, "idle:")) == 1
    assert spied["lift_pass"].call_count == 0
    assert spied["hold_pass"].call_count == 0
    assert board.module_reads() == []


def test_a_failed_lift_read_says_so_and_fails_nothing(monkeypatch, tmp_path, capsys):
    _as_the_live_sweep(monkeypatch, _checkout(tmp_path, '"8"'), "8")
    board = Board()
    _held_kids(board)
    board.fail_reads = True
    spied = _pass(board)
    lines = _out_lines(capsys, rhe.LOG_PREFIX)
    assert len(lines) == 1 and "could not be read" in lines[0], lines
    assert board.writes == []
    assert reconcile._write_failures == []
    assert spied["promotion"].calls == 1


def test_a_failed_lift_write_is_a_write_failure(monkeypatch, tmp_path):
    _as_the_live_sweep(monkeypatch, _checkout(tmp_path, '"8"'), "8")
    board = Board()
    _held_kids(board)
    board.fail_comments = True
    with pytest.raises(SystemExit):
        _pass(board)
    assert any("wip-cap" in f for f in reconcile._write_failures), reconcile._write_failures


def test_a_promote_only_pass_calls_neither(monkeypatch, tmp_path):
    for cap in ("0", "8"):
        _as_the_live_sweep(monkeypatch, _checkout(tmp_path / cap, f'"{cap}"'), cap)
        board = Board()
        _held_kids(board)
        spied = _pass(board, promote_only=True, spies=("hold_pass", "lift_pass"))
        assert spied["hold_pass"].call_count == 0, cap
        assert spied["lift_pass"].call_count == 0, cap
        assert board.module_reads() == [], cap


# --------------------------------------------------------------------------
# The read door's idle check
# --------------------------------------------------------------------------
def _door_world(stamp):
    """This repo's only Queued cards, wearing needs-human under `stamp`."""
    return {kid: door_card(kid, "Queued", labels=(f"repo:{SLUG}", hold.HOLD_LABEL),
                           parent=EPIC, comments=(FLEET, *((stamp,) if stamp else ())))
            for kid in KIDS}


@contextlib.contextmanager
def _door(monkeypatch, world):
    repository = f"dreadnought-foundry/{SLUG}"
    with FakeIssuer() as issuer, FakeDoor(world, repository=repository) as door:
        for key, value in door_env(door_url=door.url, issuer=issuer,
                                   repository=repository).items():
            monkeypatch.setenv(key, value)
        door.decline_fleet("lane-not-held")
        yield door


def test_at_the_door_a_wip_cap_held_card_is_waiting(monkeypatch, tmp_path):
    _as_the_live_sweep(monkeypatch, _checkout(tmp_path, '"8"'), "8")
    with _door(monkeypatch, _door_world(WIP_CAP_STAMP)):
        assert reconcile.sweep_idle() is None


@pytest.mark.parametrize("stamp", [None, OTHER_STAMP], ids=["manual", OTHER_REASON])
def test_at_the_door_any_other_hold_is_still_skipped(monkeypatch, tmp_path, stamp):
    """`manual` — the label with no live stamp — and a reason the sweep honors
    are skipped exactly as before: the pass is idle."""
    _as_the_live_sweep(monkeypatch, _checkout(tmp_path, '"8"'), "8")
    with _door(monkeypatch, _door_world(stamp)):
        assert reconcile.sweep_idle() is not None


def test_restored_with_the_door_idle_check_lifts_the_cards(monkeypatch, tmp_path, capsys):
    _as_the_live_sweep(monkeypatch, _checkout(tmp_path, '"8"'), "8")
    board = Board()
    _held_kids(board)
    with _door(monkeypatch, _door_world(WIP_CAP_STAMP)) as door:
        spied = _pass(board, spies=("lift_pass",))
        assert door.asked("/board")
    out = capsys.readouterr().out.splitlines()
    assert not [ln for ln in out if ln.startswith("idle:")]
    assert reconcile.IDLE_QUERY not in board.queries  # the door answered it
    assert spied["lift_pass"].call_count == 1
    assert [ln for ln in out if ln.startswith(rhe.LOG_PREFIX)] == [
        f"{rhe.LOG_PREFIX} lifted {kid} (cap-restored, max_wip 8)" for kid in KIDS]
    for kid in KIDS:
        assert hold.HOLD_LABEL not in board.cards[kid]["labels"]


def test_with_the_door_a_manual_hold_leaves_the_pass_idle_and_unlifted(monkeypatch, tmp_path,
                                                                       capsys):
    _as_the_live_sweep(monkeypatch, _checkout(tmp_path, '"8"'), "8")
    board = Board()
    board.epic(kid_labels=[hold.HOLD_LABEL])
    with _door(monkeypatch, _door_world(None)):
        spied = _pass(board, spies=("lift_pass",))
    assert len(_out_lines(capsys, "idle:")) == 1
    assert spied["lift_pass"].call_count == 0
    assert board.writes == []
