"""RED-first: the hygiene agent's holds lane (DRE-6180).

`scripts/hygiene_holds.py` finds every card carrying `needs-human` in the
leg's scope — in any lane, archived issues included, read for itself through
`ctx.linear` because holds outlive the five lanes the board read covers — and
lifts the hold whose reason has cleared:

  * the universal `card-closed` lift, on any card in Done or Canceled, stamped
    or not — how the 207 stale labels come off;
  * `run-started`, a 🧠 or ⏳ run receipt newer than a `stranded-no-run` stamp;
  * `unpark-marker`, asked of `hold.lift_due` for `dead-run-cap` and
    `turn-cap-park`.

A lift is the label off and the agent's own receipt (`hyg-hold-cleared`), and
an archived card is unarchived for the two writes and re-archived after them.
`new-head` and `repo-on-rail` need a lane move, which lands with DRE-6273; until
then they are a `Left` row and nothing is written. A `manual` hold is never
lifted outside Done or Canceled. A pass lifts at most
`HYGIENE_HOLDS_MAX_LIFTS` holds and carries the rest.

The fixtures are built here, in the shape the lane's own query answers: an
issue with `identifier`, `state`, `archivedAt`, `labels`, `children` and the
comment window, newest first as Linear answers it.

Run: cd bureau-pipeline && python3 -m pytest tests/test_hygiene_holds.py -v
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import hold  # noqa: E402
import hygiene  # noqa: E402
import linear_ops  # noqa: E402

MODULE_PATH = ROOT / "scripts" / "hygiene_holds.py"

#: 21:05 UTC on 2026-10-08 is 14:05 in Pacific Daylight Time.
NOW = datetime(2026, 10, 8, 21, 5, tzinfo=UTC)
CLOCK = "14:05 PT"
HOME = "dreadnought-foundry"
SUMMARY = "DRE-900"
SHA = "a" * 40
OTHER_SHA = "b" * 40
CAP_VAR = "HYGIENE_HOLDS_MAX_LIFTS"
DRE_6273 = "lift needs a lane move — lands with DRE-6273"
LABEL = "needs-human"
ACT = "hygiene-hold-clear"
TAG = "hyg-hold-cleared"

RESET = "♻️ dead-run-budget-reset: un-parked by a human — the label is off"


def load_lane():
    if not MODULE_PATH.exists():
        return None
    spec = importlib.util.spec_from_file_location("hygiene_holds", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lane = load_lane()


def need_lane():
    assert lane is not None, f"{MODULE_PATH.name} does not exist yet"
    return lane


@pytest.fixture(autouse=True)
def _no_cap(monkeypatch):
    monkeypatch.delenv(CAP_VAR, raising=False)


# --------------------------------------------------------------------------- #
# fixtures                                                                     #
# --------------------------------------------------------------------------- #


def stamp(reason, at="none", by="scripts/reconcile.py"):
    return hold.stamp_line(reason, at, by)


def hyg_cleared():
    """The hygiene agent's own lift receipt, as an earlier pass posted it."""
    return ("🧹 hygiene: hyg-hold-cleared — reason=review-cap-spent because=new-head"
            " · 09:00 PT\nevidence: stamp at=" + SHA)


def issue(ident, lane_name, comments=(), *, repo="bureau-pipeline", labels=(LABEL,),
          archived=None, start=None, partial=False, children=()):
    """One candidate, in the shape the lane's query answers. `comments` is
    oldest→newest; each one is a body, or a `(body, createdAt)` pair. The
    window is stored newest first, as Linear answers it."""
    start = start or datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    nodes = []
    for index, comment in enumerate(comments):
        body, at = comment if isinstance(comment, tuple) else (
            comment, (start + timedelta(hours=index)).isoformat().replace("+00:00", "Z"))
        nodes.append({"body": body, "createdAt": at, "user": {"id": "fleet"}})
    names = list(labels) + ([f"repo:{repo}"] if repo else [])
    return {
        "id": f"uuid-{ident}",
        "identifier": ident,
        "title": f"card {ident}",
        "archivedAt": archived,
        "state": {"name": lane_name},
        "labels": {"nodes": [{"name": n} for n in names]},
        "children": {"nodes": [{"identifier": c} for c in children]},
        "comments": {"pageInfo": {"hasNextPage": partial, "endCursor": None},
                     "nodes": list(reversed(nodes))},
    }


class FakeLinear:
    """The read-only `ctx.linear`: the candidate query, answered page by page
    off `issues`, every query and its variables recorded."""

    def __init__(self, issues, page=50):
        self.issues = list(issues)
        self.page = page
        self.calls: list = []

    def __call__(self, query, variables=None):
        assert not query.lstrip().lower().startswith("mutation")
        variables = dict(variables or {})
        self.calls.append((query, variables))
        after = variables.get("after")
        begin = int(after) if after else 0
        chunk = self.issues[begin:begin + self.page]
        more = begin + self.page < len(self.issues)
        return {"issues": {"pageInfo": {"hasNextPage": more,
                                        "endCursor": str(begin + self.page) if more else None},
                           "nodes": chunk}}


def context(issues, *, owner=HOME, dry_run=False, gh=None):
    linear = FakeLinear(issues)
    ctx = hygiene.make_context(owner, gh=gh or (lambda argv: "[]"), linear=linear,
                               dry_run=dry_run, now=NOW, summary_card=SUMMARY)
    return ctx, linear


def plan(*issues, owner=HOME, prs=None):
    ctx, linear = context(issues, owner=owner)
    board = hygiene.Board(lanes={}, prs=prs or {})
    return need_lane().plan(board, ctx), ctx, linear


def actions(items, target=None):
    return [i for i in items if isinstance(i, hygiene.Action)
            and (target is None or i.target == target)]


def lefts(items, target=None):
    return [i for i in items if isinstance(i, hygiene.Left)
            and (target is None or i.target == target)]


def first_line(write):
    return (write.body or "").splitlines()[0]


def receipt_head(write):
    return hygiene.read_receipt(write.body)


@pytest.fixture
def lift_due_calls(monkeypatch):
    """`hold.lift_due`, recorded and passed through."""
    calls: list = []
    real = hold.lift_due

    def recording(stamp_, **kwargs):
        calls.append((stamp_, kwargs))
        return real(stamp_, **kwargs)

    monkeypatch.setattr(hold, "lift_due", recording)
    return calls


# --------------------------------------------------------------------------- #
# discovery                                                                    #
# --------------------------------------------------------------------------- #


class TestDiscovery:
    def test_the_core_discovers_it_by_the_glob(self):
        assert "hygiene_holds" in [m.__name__ for m in hygiene.discover()]

    def test_it_is_a_lane_module(self):
        module = need_lane()
        assert callable(module.plan)
        assert isinstance(module.LANE, str) and module.LANE


# --------------------------------------------------------------------------- #
# the candidate read                                                           #
# --------------------------------------------------------------------------- #


class TestTheCandidateRead:
    def test_the_query_includes_archived_issues_and_selects_archived_at(self):
        _, _, linear = plan(issue("DRE-1", "Done"))
        assert linear.calls, "the lane read no candidates"
        query = linear.calls[0][0]
        assert re.search(r"includeArchived:\s*true", query)
        assert re.search(r"\barchivedAt\b", query)

    def test_it_reads_every_card_carrying_the_label_in_any_lane(self):
        _, _, linear = plan()
        query = linear.calls[0][0]
        assert LABEL in query or LABEL in json.dumps(linear.calls[0][1])
        assert "state:" not in query  # no lane filter: holds outlive the five lanes

    def test_it_pages_fifty_at_a_time_to_the_end(self):
        many = [issue(f"DRE-{n}", "Done") for n in range(1, 121)]
        items, _, linear = plan(*many)
        assert len(linear.calls) == 3
        assert re.search(r"first:\s*50\b", linear.calls[0][0])
        assert [v.get("after") for _, v in linear.calls] == [None, "50", "100"]
        assert len(actions(items)) == 40  # the default cap
        assert len(lefts(items)) == 80

    def test_it_selects_the_shared_comment_window(self):
        _, _, linear = plan()
        assert linear_ops.COMMENT_WINDOW_GQL in linear.calls[0][0]

    def test_a_window_linear_cut_short_is_read_whole(self, monkeypatch):
        reads: list = []
        older = [{"body": stamp("dead-run-cap"), "created_at": "2026-09-01T12:00:00Z",
                  "authored_by_pipeline": True},
                 {"body": "talk", "created_at": "2026-09-01T13:00:00Z",
                  "authored_by_pipeline": True}]

        def whole(ident, *, whole_thread=False):
            reads.append((ident, whole_thread))
            return older

        monkeypatch.setattr(linear_ops, "comment_records", whole)
        cut = issue("DRE-1", "Canceled", ["talk"], partial=True)
        items, _, _ = plan(cut)
        assert reads == [("DRE-1", True)]
        [action] = actions(items)
        assert action.cause == "reason=dead-run-cap because=card-closed"

    def test_a_card_without_the_label_is_not_a_candidate(self):
        items, _, _ = plan(issue("DRE-1", "Done", labels=()))
        assert items == []


# --------------------------------------------------------------------------- #
# the universal lift                                                           #
# --------------------------------------------------------------------------- #


class TestCardClosed:
    def test_a_done_card_with_no_stamp_is_lifted_as_manual(self):
        items, _, _ = plan(issue("DRE-1", "Done", ["talk"]))
        [action] = actions(items)
        assert lefts(items) == []
        assert action.act == ACT and action.target == "DRE-1"
        assert action.cause == "reason=manual because=card-closed"
        label, note = action.writes
        assert (label.kind, label.label, label.add) == ("linear_label", LABEL, False)
        assert note.kind == "linear_comment"
        assert first_line(note) == (f"🧹 hygiene: {TAG} — reason=manual because=card-closed"
                                    f" · {CLOCK}")
        assert "lane Done" in action.evidence

    def test_a_canceled_card_with_a_dead_run_cap_stamp_names_its_reason(self):
        items, _, _ = plan(issue("DRE-2", "Canceled", [stamp("dead-run-cap")]))
        [action] = actions(items)
        assert action.cause == "reason=dead-run-cap because=card-closed"
        assert [w.kind for w in action.writes] == ["linear_label", "linear_comment"]
        assert receipt_head(action.writes[1])["cause"] == action.cause
        assert "lane Canceled" in action.evidence

    def test_the_receipt_is_composed_by_the_core_and_keys_on_the_cause(self):
        items, _, _ = plan(issue("DRE-2", "Canceled", [stamp("dead-run-cap")]))
        [action] = actions(items)
        note = action.writes[1]
        assert note.body == hygiene.receipt(ACT, action.cause, action.evidence, NOW)
        assert hygiene.TAGS[ACT] == TAG

    def test_the_receipt_retires_the_stamp_it_lifted(self):
        items, _, _ = plan(issue("DRE-2", "Canceled", [stamp("dead-run-cap")]))
        [action] = actions(items)
        after = [stamp("dead-run-cap"), action.writes[1].body]
        assert hold.read_stamp(after) is None

    def test_the_receipt_carries_no_key_another_reader_counts(self):
        bodies = [stamp("dead-run-cap"), RESET, stamp("dead-run-cap")]
        items, _, _ = plan(issue("DRE-2", "Canceled", bodies, archived="2026-09-20T00:00:00Z"),
                           issue("DRE-3", "Todo", [stamp("stranded-no-run"), "⏳ 1/5 plan"]))
        tags = [row["tag"] for row in json.loads(
            (ROOT / "config" / "pipeline-acts.json").read_text())["acts"]]
        for action in actions(items):
            body = action.writes[[w.kind for w in action.writes].index("linear_comment")].body
            for key in hold.FORBIDDEN + ("dead-run-budget-reset",):
                assert key not in body, (action.target, key)
            for other in tags:
                if other != TAG:
                    assert other not in body, (action.target, other)
            assert not body.lstrip().startswith(hold.RUN_RECEIPT_PREFIXES)

    @pytest.mark.parametrize("reason,at", [
        ("review-cap-spent", SHA), ("no-route", "repo:legacy-site"),
        ("plan-critic-bound", "none"), ("epic-rereview-twice", "none"),
        ("fix-dispute", SHA), ("stranded-no-run", "none"),
    ])
    def test_every_reason_lifts_in_done(self, reason, at):
        items, _, _ = plan(issue("DRE-4", "Done", [stamp(reason, at)]))
        [action] = actions(items)
        assert action.cause == f"reason={reason} because=card-closed"
        assert lefts(items) == []


# --------------------------------------------------------------------------- #
# archived cards                                                               #
# --------------------------------------------------------------------------- #


ARCHIVED_AT = "2026-09-15T08:00:00Z"


class TestArchived:
    def test_an_archived_done_card_is_unarchived_for_the_writes_and_re_archived(self):
        card = issue("DRE-5", "Done", ["talk"], archived=ARCHIVED_AT)
        items, ctx, _ = plan(card)
        [action] = actions(items)
        kinds = [w.kind for w in action.writes]
        assert kinds == ["linear_archived", "linear_label", "linear_comment", "linear_archived"]
        unarchive, label, note, rearchive = action.writes
        assert unarchive.archived is False and rearchive.archived is True
        assert (label.label, label.add) == (LABEL, False)
        assert action.cause == "reason=manual because=card-closed"
        assert "archived" in note.body.splitlines()[1]
        assert "lane Done" in action.evidence
        assert any("archived" in e for e in action.evidence)
        assert unarchive == hygiene.linear_archived(card, archived=False)
        assert rearchive == hygiene.linear_archived(card, archived=True)
        for write in action.writes:
            hygiene.guard(write, ctx)

    def test_an_archived_canceled_card_with_a_stamp_takes_the_same_four_writes(self):
        card = issue("DRE-6", "Canceled", [stamp("turn-cap-park")], archived=ARCHIVED_AT)
        items, ctx, _ = plan(card)
        [action] = actions(items)
        assert [w.kind for w in action.writes] == [
            "linear_archived", "linear_label", "linear_comment", "linear_archived"]
        assert action.cause == "reason=turn-cap-park because=card-closed"
        for write in action.writes:
            hygiene.guard(write, ctx)

    def test_an_archived_triage_card_is_left_alone(self, lift_due_calls):
        card = issue("DRE-7", "Triage", [stamp("no-route", "repo:legacy-site")],
                     archived=ARCHIVED_AT)
        items, _, _ = plan(card)
        assert items == []
        assert lift_due_calls == []

    def test_an_archived_todo_card_whose_run_started_is_left_alone(self):
        card = issue("DRE-8", "Todo", [stamp("stranded-no-run"), "⏳ 1/5 plan"],
                     archived=ARCHIVED_AT)
        items, _, _ = plan(card)
        assert items == []

    def test_an_unarchived_card_takes_no_archive_write(self):
        items, _, _ = plan(issue("DRE-9", "Done"))
        [action] = actions(items)
        assert "linear_archived" not in [w.kind for w in action.writes]
        assert not any("archived" in e for e in action.evidence)


# --------------------------------------------------------------------------- #
# the lifts that need a lane move — DRE-6273                                   #
# --------------------------------------------------------------------------- #


def pr_list(number, head):
    return {"dreadnought-foundry/bureau-pipeline": [
        {"number": number, "headRefName": "agent/DRE-10-x", "headRefOid": head,
         "title": "feat(DRE-10): x", "body": "", "comments": []}]}


class TestLiftsThatNeedALaneMove:
    def test_a_green_light_review_cap_with_a_new_head_is_a_row_naming_dre_6273(
        self, lift_due_calls
    ):
        card = issue("DRE-10", "Green Light", [stamp("review-cap-spent", SHA)])
        items, _, _ = plan(card, prs=pr_list(42, OTHER_SHA))
        assert actions(items) == []
        [row] = lefts(items)
        assert row.target == "DRE-10" and row.why == DRE_6273
        assert lift_due_calls == []

    def test_a_triage_no_route_wearing_an_on_rail_label_is_the_same_row(self, lift_due_calls):
        card = issue("DRE-11", "Triage", [stamp("no-route", "repo:legacy-site")],
                     repo="bureau-pipeline")
        items, _, _ = plan(card)
        assert actions(items) == []
        [row] = lefts(items)
        assert row.why == DRE_6273
        assert lift_due_calls == []

    @pytest.mark.parametrize("reason", ["fix-dispute", "unfixable-check"])
    def test_a_triage_new_head_hold_is_the_row_and_lift_due_is_not_asked(
        self, reason, lift_due_calls
    ):
        card = issue("DRE-12", "Triage", [stamp(reason, SHA)])
        items, _, _ = plan(card, prs=pr_list(43, OTHER_SHA))
        assert actions(items) == []
        [row] = lefts(items)
        assert row.why == DRE_6273
        assert lift_due_calls == []

    def test_the_row_says_what_lifts_it(self):
        card = issue("DRE-12", "Triage", [stamp("fix-dispute", SHA)])
        [row] = lefts(plan(card)[0])
        assert row.recommendation.strip()


# --------------------------------------------------------------------------- #
# manual holds                                                                 #
# --------------------------------------------------------------------------- #


class TestManualHolds:
    @pytest.mark.parametrize("reason", ["plan-critic-bound", "epic-rereview-twice"])
    def test_a_triage_manual_stamp_yields_nothing_however_old_and_whatever_its_pr(
        self, reason
    ):
        old = datetime(2025, 1, 1, tzinfo=UTC)
        card = issue("DRE-13", "Triage",
                     [stamp(reason), "⏳ 1/5 plan", "talk"],
                     start=old)
        items, _, _ = plan(card, prs=pr_list(44, OTHER_SHA))
        assert items == []

    def test_a_backlog_card_with_the_label_and_no_stamp_yields_nothing(self):
        items, _, _ = plan(issue("DRE-14", "Backlog", ["talk", "⏳ 1/5 plan"]))
        assert items == []

    def test_a_manual_stamp_yields_nothing(self):
        items, _, _ = plan(issue("DRE-15", "In Review", [stamp("manual")]))
        assert items == []


# --------------------------------------------------------------------------- #
# run-started                                                                  #
# --------------------------------------------------------------------------- #


class TestRunStarted:
    def test_no_run_receipt_newer_than_the_stamp_yields_nothing(self):
        items, _, _ = plan(issue("DRE-20", "Todo", [stamp("stranded-no-run"), "talk"]))
        assert items == []

    @pytest.mark.parametrize("receipt", ["⏳ 1/5 plan — continuing within 400 turns",
                                         "🧠 model-attempt: claude-opus-5-5 turns=400"])
    def test_a_run_receipt_newer_than_the_stamp_lifts_with_no_lane_move(self, receipt):
        items, _, _ = plan(issue("DRE-21", "Todo", [stamp("stranded-no-run"), receipt]))
        [action] = actions(items)
        assert action.cause == "reason=stranded-no-run because=run-started"
        assert [w.kind for w in action.writes] == ["linear_label", "linear_comment"]
        assert receipt_head(action.writes[1])["cause"].endswith("because=run-started")
        assert lefts(items) == []

    def test_a_run_receipt_older_than_the_stamp_does_not_count(self):
        items, _, _ = plan(issue("DRE-22", "Todo", ["⏳ 1/5 plan", stamp("stranded-no-run")]))
        assert items == []

    def test_lift_due_is_asked_with_the_live_stamp_and_the_lane(self, lift_due_calls):
        plan(issue("DRE-21", "Todo", [stamp("stranded-no-run"), "⏳ 1/5 plan"]))
        [(asked, kwargs)] = lift_due_calls
        assert asked["reason"] == "stranded-no-run"
        assert kwargs["lane"] == "Todo"
        assert kwargs["bodies"][-1] == "⏳ 1/5 plan"


# --------------------------------------------------------------------------- #
# unpark-marker                                                                #
# --------------------------------------------------------------------------- #


class TestUnparkMarker:
    def test_no_reset_marker_newer_than_the_stamp_yields_nothing(self):
        items, _, _ = plan(issue("DRE-30", "Backlog", [stamp("dead-run-cap", by="scripts/dead_run.py")]))
        assert items == []

    def test_a_fresh_stamp_after_the_reset_is_a_live_re_park_and_is_not_lifted(
        self, lift_due_calls
    ):
        # The reset spent the first stamp; the second is the card parked again
        # after the reset's budget was spent. `hold.lift_due` reads the marker
        # newer than THAT stamp, and there is none, so the re-park holds.
        bodies = [stamp("dead-run-cap", by="scripts/dead_run.py"), RESET,
                  stamp("dead-run-cap", by="scripts/dead_run.py")]
        items, _, _ = plan(issue("DRE-31", "Backlog", bodies))
        assert items == []
        [(asked, kwargs)] = lift_due_calls
        assert asked["reason"] == "dead-run-cap"
        assert kwargs["lane"] == "Backlog"

    @pytest.mark.parametrize("reason", ["dead-run-cap", "turn-cap-park"])
    def test_an_unpark_marker_lift_is_the_label_and_the_receipt_and_no_lane_move(
        self, monkeypatch, reason
    ):
        monkeypatch.setattr(hold, "lift_due", lambda stamp_, **kw: "unpark-marker")
        bodies = [stamp(reason, by="scripts/dead_run.py")]
        items, _, _ = plan(issue("DRE-32", "Backlog", bodies))
        [action] = actions(items)
        assert action.cause == f"reason={reason} because=unpark-marker"
        assert [w.kind for w in action.writes] == ["linear_label", "linear_comment"]
        assert receipt_head(action.writes[1])["cause"].endswith("because=unpark-marker")


# --------------------------------------------------------------------------- #
# a spent stamp                                                                #
# --------------------------------------------------------------------------- #


def lifted_line():
    return hold.lift_line("stranded-no-run", "operator", "scripts/linear_ops.py")


class TestSpentStamps:
    SPENT = {
        "lift-line": ("DRE-40", "Todo",
                      [stamp("stranded-no-run"), lifted_line(), "⏳ 1/5 plan"]),
        "budget-reset": ("DRE-41", "Backlog",
                         [stamp("dead-run-cap", by="scripts/dead_run.py"), RESET]),
        "hyg-hold-cleared": ("DRE-42", "Green Light",
                             [stamp("review-cap-spent", SHA), hyg_cleared()]),
    }

    @pytest.mark.parametrize("which", sorted(SPENT))
    def test_a_spent_stamp_lifts_nothing_outside_done_or_canceled(self, which, lift_due_calls):
        ident, lane_name, bodies = self.SPENT[which]
        items, _, _ = plan(issue(ident, lane_name, bodies), prs=pr_list(45, OTHER_SHA))
        assert actions(items) == []
        [row] = lefts(items)
        assert row.target == ident
        assert "manual" in row.why
        assert row.why != DRE_6273 and "DRE-6273" not in row.why
        assert lift_due_calls == []

    @pytest.mark.parametrize("which", sorted(SPENT))
    def test_a_done_card_over_a_spent_stamp_is_lifted_as_manual(self, which, lift_due_calls):
        ident, _, bodies = self.SPENT[which]
        items, _, _ = plan(issue(ident, "Done", bodies))
        [action] = actions(items)
        assert action.cause == "reason=manual because=card-closed"
        assert lift_due_calls == []


# --------------------------------------------------------------------------- #
# the guard, the scope, and the kinds of write                                 #
# --------------------------------------------------------------------------- #


def every_fixture():
    return [
        issue("DRE-1", "Done", ["talk"]),
        issue("DRE-2", "Canceled", [stamp("dead-run-cap")]),
        issue("DRE-5", "Done", ["talk"], archived=ARCHIVED_AT),
        issue("DRE-6", "Canceled", [stamp("turn-cap-park")], archived=ARCHIVED_AT),
        issue("DRE-7", "Triage", [stamp("no-route", "repo:legacy-site")], archived=ARCHIVED_AT),
        issue("DRE-10", "Green Light", [stamp("review-cap-spent", SHA)]),
        issue("DRE-11", "Triage", [stamp("no-route", "repo:legacy-site")]),
        issue("DRE-12", "Triage", [stamp("fix-dispute", SHA)]),
        issue("DRE-13", "Triage", [stamp("plan-critic-bound")]),
        issue("DRE-14", "Backlog", ["talk"]),
        issue("DRE-20", "Todo", [stamp("stranded-no-run")]),
        issue("DRE-21", "Todo", [stamp("stranded-no-run"), "⏳ 1/5 plan"]),
        issue("DRE-22", "Todo", ["⏳ 1/5 plan", stamp("stranded-no-run")]),
        issue("DRE-30", "Backlog", [stamp("dead-run-cap")]),
        issue("DRE-40", "Todo", [stamp("stranded-no-run"), lifted_line(), "⏳ 1/5 plan"]),
        issue("DRE-50", "Done", ["talk"], children=("DRE-51",)),
        issue("DRE-60", "Done", ["talk"], repo=None),
    ]


class TestGuardAndScope:
    def test_every_write_passes_the_real_guard(self):
        items, ctx, _ = plan(*every_fixture())
        writes = [w for a in actions(items) for w in a.writes]
        assert writes
        for write in writes:
            hygiene.guard(write, ctx)

    def test_it_never_proposes_a_lane_move(self):
        items, _, _ = plan(*every_fixture())
        kinds = {w.kind for a in actions(items) for w in a.writes}
        assert kinds <= {"linear_label", "linear_comment", "linear_archived"}
        assert "linear_state" not in kinds

    def test_every_label_write_takes_the_hold_off_and_nothing_else(self):
        items, _, _ = plan(*every_fixture())
        labels = [w for a in actions(items) for w in a.writes if w.kind == "linear_label"]
        assert labels and all((w.label, w.add) == (LABEL, False) for w in labels)

    def test_a_card_outside_the_legs_scope_yields_nothing(self):
        atlas = issue("DRE-70", "Done", ["talk"], repo="atlas")
        items, _, _ = plan(atlas)
        assert items == []
        items, ctx, _ = plan(atlas, owner="EveryBite")
        [action] = actions(items)
        for write in action.writes:
            hygiene.guard(write, ctx)

    def test_an_unlabeled_card_is_the_home_legs(self):
        items, _, _ = plan(issue("DRE-60", "Done", ["talk"], repo=None))
        assert [a.target for a in actions(items)] == ["DRE-60"]
        assert plan(issue("DRE-60", "Done", ["talk"], repo=None), owner="EveryBite")[0] == []

    def test_the_summary_card_is_never_a_candidate(self):
        items, _, _ = plan(issue(SUMMARY, "Done", ["talk"]))
        assert items == []

    def test_the_whole_leg_runs_with_the_real_guard(self, monkeypatch):
        sent: list = []
        monkeypatch.setattr(hygiene, "discover", lambda lane_dir=None: [need_lane()])
        monkeypatch.setattr(hygiene, "send", lambda write, ctx: sent.append(write))
        monkeypatch.setattr(linear_ops, "comment_records", lambda ident, **k: [])
        ctx, _ = context(every_fixture())
        ledger = hygiene.run_leg({"lanes": {}}, ctx)
        assert ledger["actions"] and all(a["outcome"] == "executed" for a in ledger["actions"])
        assert {w.kind for w in sent} <= {"linear_label", "linear_comment", "linear_archived"}


# --------------------------------------------------------------------------- #
# dry run and the summary                                                      #
# --------------------------------------------------------------------------- #


class TestDryRun:
    def test_a_dry_run_prints_the_actions_and_sends_nothing(self, monkeypatch, capsys):
        def refuse(write, ctx):
            raise AssertionError(f"a dry run sent {write.describe()}")

        monkeypatch.setattr(hygiene, "discover", lambda lane_dir=None: [need_lane()])
        monkeypatch.setattr(hygiene, "send", refuse)
        monkeypatch.setattr(linear_ops, "comment_records", lambda ident, **k: [])
        monkeypatch.setenv("HYGIENE_DRY_RUN", "1")
        ctx, _ = context(every_fixture(), dry_run=os.environ["HYGIENE_DRY_RUN"] == "1")
        ledger = hygiene.run_leg({"lanes": {}}, ctx)
        out = capsys.readouterr().out
        assert ledger["actions"] and all(a["outcome"] == "would" for a in ledger["actions"])
        assert "would: label DRE-1 − needs-human" in out
        assert "would: comment DRE-1: 🧹 hygiene: hyg-hold-cleared" in out
        assert "would: unarchive DRE-5" in out and "would: archive DRE-5" in out

    def test_the_summary_digest_changes_when_a_lift_is_proposed(self, monkeypatch):
        monkeypatch.setattr(hygiene, "discover", lambda lane_dir=None: [need_lane()])
        monkeypatch.setattr(hygiene, "send", lambda write, ctx: None)
        monkeypatch.setattr(linear_ops, "comment_records", lambda ident, **k: [])
        quiet, _ = context([issue("DRE-14", "Backlog", ["talk"])], dry_run=True)
        busy, _ = context([issue("DRE-14", "Backlog", ["talk"]),
                           issue("DRE-1", "Done", ["talk"])], dry_run=True)
        before = hygiene.render_summary([hygiene.run_leg({"lanes": {}}, quiet)], NOW)
        after = hygiene.render_summary([hygiene.run_leg({"lanes": {}}, busy)], NOW)
        assert before.splitlines()[0] != after.splitlines()[0]
        assert "DRE-1" in after


# --------------------------------------------------------------------------- #
# the per-pass cap                                                             #
# --------------------------------------------------------------------------- #


def cap_fixture():
    base = datetime(2026, 9, 1, tzinfo=UTC)

    def at(days):
        return (base + timedelta(days=days)).isoformat().replace("+00:00", "Z")

    return [
        issue("DRE-81", "Done", [(stamp("dead-run-cap"), at(5))]),
        issue("DRE-82", "Done", [(stamp("turn-cap-park"), at(1))]),
        issue("DRE-83", "Done", ["talk"]),
        issue("DRE-80", "Done", [(stamp("manual"), at(3))]),
        issue("DRE-9", "Done", ["talk"]),
        issue("DRE-90", "Todo", [(stamp("stranded-no-run"), at(20)), ("⏳ 1/5 plan", at(21))]),
    ]


class TestThePerPassCap:
    def test_the_default_is_forty(self):
        assert need_lane().DEFAULT_MAX_LIFTS == 40
        assert need_lane().MAX_LIFTS_VAR == CAP_VAR

    def test_a_cap_of_two_lifts_the_open_card_first_then_the_oldest_stamp(self, monkeypatch):
        monkeypatch.setenv(CAP_VAR, "2")
        items, _, _ = plan(*cap_fixture())
        assert [a.target for a in actions(items)] == ["DRE-90", "DRE-82"]
        rows = lefts(items)
        assert sorted(r.target for r in rows) == ["DRE-80", "DRE-81", "DRE-83", "DRE-9"]
        for row in rows:
            assert row.why == "over the per-pass cap of 2 — carried to the next pass"
            assert "wait" in row.recommendation

    def test_unset_every_lift_is_an_action_and_nothing_is_carried(self):
        items, _, _ = plan(*cap_fixture())
        assert [a.target for a in actions(items)] == [
            "DRE-90", "DRE-82", "DRE-80", "DRE-81", "DRE-9", "DRE-83"]
        assert lefts(items) == []

    def test_the_order_is_open_lifts_then_stamped_closed_then_unstamped_by_identifier(
        self, monkeypatch
    ):
        monkeypatch.setenv(CAP_VAR, "100")
        items, _, _ = plan(*cap_fixture())
        assert [a.target for a in actions(items)] == [
            "DRE-90", "DRE-82", "DRE-80", "DRE-81", "DRE-9", "DRE-83"]

    def test_the_carry_does_not_hide_a_row_of_another_kind(self, monkeypatch):
        monkeypatch.setenv(CAP_VAR, "1")
        items, _, _ = plan(*cap_fixture(), issue("DRE-12", "Triage", [stamp("fix-dispute", SHA)]))
        assert [a.target for a in actions(items)] == ["DRE-90"]
        assert [r.why for r in lefts(items, "DRE-12")] == [DRE_6273]

    @pytest.mark.parametrize("value", ["0", "-3", "forty", "2.5", ""])
    def test_a_value_that_is_not_a_positive_integer_raises_naming_the_variable(
        self, monkeypatch, value
    ):
        monkeypatch.setenv(CAP_VAR, value)
        with pytest.raises(ValueError, match=CAP_VAR):
            plan(*cap_fixture())

    def test_the_cap_is_read_before_any_candidate(self, monkeypatch):
        monkeypatch.setenv(CAP_VAR, "nope")
        ctx, linear = context(cap_fixture())
        with pytest.raises(ValueError):
            need_lane().plan(hygiene.Board(lanes={}), ctx)
        assert linear.calls == []


# --------------------------------------------------------------------------- #
# the act, the registry and the page                                           #
# --------------------------------------------------------------------------- #


def act_row():
    doc = json.loads((ROOT / "config" / "pipeline-acts.json").read_text())
    return next((r for r in doc["acts"] if r["name"] == ACT), None)


class TestTheAct:
    def test_the_core_table_carries_the_act(self):
        assert hygiene.TAGS[ACT] == TAG

    def test_the_registry_row(self):
        row = act_row()
        assert row is not None, "config/pipeline-acts.json has no hygiene-hold-clear row"
        assert row["tag"] == TAG
        assert row["kind"] == "recovery"
        assert row["state"] == "unchanged"
        assert row["next_actor"] == "reconcile.py"
        assert row["subscriber"] == "hygiene.yml"
        assert row["cadence_s"] is None and row["cadence_why"].strip()
        assert row["adopted"] is True
        assert row["emits"] == {"file": "scripts/hygiene.py", "anchor": f'"{TAG}"'}

    def test_the_receipt_reads_as_the_act(self):
        import pipeline_act
        body = hygiene.receipt(ACT, "reason=manual because=card-closed", ["lane Done"], NOW)
        fields = pipeline_act.read_trailer(body)
        assert fields["act"] == ACT and fields["tag"] == TAG


class TestThePage:
    def text(self):
        return (ROOT / "docs" / "holds.md").read_text(encoding="utf-8")

    def test_it_says_how_a_hold_lifts_and_names_the_receipt(self):
        text = self.text()
        assert "## How a hold lifts" in text
        assert "scripts/hygiene_holds.py" in text
        assert f"`{TAG}`" in text and f"`{ACT}`" in text
        assert "reason=<code> because=<lift-kind>" in text

    def test_it_names_the_three_lifts_this_lane_makes_and_the_two_it_leaves(self):
        section = self.text().split("## How a hold lifts", 1)[-1]
        for kind in ("`card-closed`", "`run-started`", "`unpark-marker`",
                     "`new-head`", "`repo-on-rail`"):
            assert kind in section, kind
        assert "DRE-6273" in section
        assert DRE_6273 in section

    def test_it_says_a_manual_hold_waits_for_a_person(self):
        section = self.text().split("## How a hold lifts", 1)[-1]
        for reason in ("`epic-rereview-twice`", "`plan-critic-bound`", "`manual`"):
            assert reason in section
        assert "no stamp" in section
        assert "Done or Canceled" in section

    def test_it_names_the_cap_its_default_its_order_and_the_carry(self):
        section = self.text().split("## How a hold lifts", 1)[-1]
        assert f"`{CAP_VAR}`" in section
        assert "40" in section
        assert "oldest stamp first" in section
        assert "over the per-pass cap of <n> — carried to the next pass" in section

    def test_it_says_archived_issues_are_read_and_re_archived(self):
        section = self.text().split("## How a hold lifts", 1)[-1]
        assert "includeArchived" in section
        assert "unarchived" in section and "re-archived" in section
