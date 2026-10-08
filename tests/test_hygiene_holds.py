"""RED-first: the hygiene agent's holds lane (DRE-6180, DRE-6273).

`scripts/hygiene_holds.py` finds every card carrying `needs-human` in the
leg's scope — in any lane, archived issues included, read for itself through
`ctx.linear` because holds outlive the five lanes the board read covers — and
lifts the hold whose reason has cleared:

  * the universal `card-closed` lift, on any card in Done or Canceled, stamped
    or not — how the 207 stale labels come off;
  * `run-started`, a 🧠 or ⏳ run receipt newer than a `stranded-no-run` stamp;
  * `unpark-marker`, asked of `hold.lift_due` for `dead-run-cap` and
    `turn-cap-park`;
  * `new-head` (DRE-6273), the open pull request's head off the leg's listing
    differing from the stamped sha — and the card returned to In Review from
    Green Light (`review-cap-spent`) or Triage (`fix-dispute`,
    `unfixable-check`);
  * `repo-on-rail` (DRE-6273), the card's current `repo:` label on the rail —
    and the card returned from Triage to Planning.

A lift is the label off and the agent's own receipt (`hyg-hold-cleared`), and
an archived card is unarchived for the two writes and re-archived after them.
The two DRE-6273 lifts add a third write, the lane move, only from the lane
the hold parked the card in. A `manual` hold is never lifted outside Done or
Canceled. A pass lifts at most `HYGIENE_HOLDS_MAX_LIFTS` holds and carries the
rest.

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
# the lifts that move the card — DRE-6273                                      #
# --------------------------------------------------------------------------- #


PIPELINE_REPO = "dreadnought-foundry/bureau-pipeline"


def pull(ident, number, head, repo=PIPELINE_REPO):
    """One open pull request on the card's branch, in the leg's listing shape."""
    return {"number": number, "headRefName": f"agent/{ident}-x", "headRefOid": head,
            "title": f"feat({ident}): x", "body": "", "comments": []}


def pr_list(number, head, ident="DRE-10", repo=PIPELINE_REPO):
    return {repo: [pull(ident, number, head)]}


def lane_moves(action):
    return [w.lane for w in action.writes if w.kind == "linear_state"]


def assert_three_writes(action, because, destination):
    label, note, move = action.writes
    assert (label.kind, label.label, label.add) == ("linear_label", LABEL, False)
    assert note.kind == "linear_comment"
    assert receipt_head(note)["cause"].endswith(f"because={because}")
    assert (move.kind, move.lane, move.park) == ("linear_state", destination, False)


class TestNewHeadFromGreenLight:
    """`review-cap-spent` — the sweep's Green Light park (DRE-6181)."""

    def card(self, lane_name="Green Light", ident="DRE-10", repo="bureau-pipeline"):
        return issue(ident, lane_name, [stamp("review-cap-spent", SHA)], repo=repo)

    def test_the_stamped_head_still_open_yields_nothing(self):
        items, _, _ = plan(self.card(), prs=pr_list(42, SHA))
        assert items == []

    def test_a_new_head_is_three_writes_ending_in_in_review(self):
        items, _, _ = plan(self.card(), prs=pr_list(42, OTHER_SHA))
        [action] = actions(items)
        assert lefts(items) == []
        assert action.act == ACT and action.target == "DRE-10"
        assert action.cause == "reason=review-cap-spent because=new-head"
        assert first_line(action.writes[1]) == (
            f"🧹 hygiene: {TAG} — reason=review-cap-spent because=new-head · {CLOCK}")
        assert_three_writes(action, "new-head", "In Review")

    def test_the_receipt_names_the_stamped_sha_the_new_head_and_the_lane(self):
        items, _, _ = plan(self.card(), prs=pr_list(42, OTHER_SHA))
        [action] = actions(items)
        assert f"stamp at={SHA}" in action.evidence
        assert any(f"{PIPELINE_REPO}#42" in e and OTHER_SHA in e for e in action.evidence)
        assert "lane Green Light" in action.evidence

    def test_the_same_card_in_in_review_is_two_writes_and_no_move(self):
        items, _, _ = plan(self.card("In Review"), prs=pr_list(42, OTHER_SHA))
        [action] = actions(items)
        assert action.cause == "reason=review-cap-spent because=new-head"
        assert [w.kind for w in action.writes] == ["linear_label", "linear_comment"]

    def test_no_open_pull_request_yields_nothing(self):
        items, _, _ = plan(self.card())
        assert items == []

    def test_another_cards_pull_request_is_not_this_cards_head(self):
        items, _, _ = plan(self.card(), prs=pr_list(42, OTHER_SHA, ident="DRE-100"))
        assert items == []

    def test_the_newest_of_the_cards_pull_requests_is_its_head(self):
        prs = {PIPELINE_REPO: [pull("DRE-10", 41, OTHER_SHA), pull("DRE-10", 42, SHA)]}
        assert plan(self.card(), prs=prs)[0] == []

    def test_lift_due_is_asked_with_the_open_pull_requests_head(self, lift_due_calls):
        plan(self.card(), prs=pr_list(42, OTHER_SHA))
        [(asked, kwargs)] = lift_due_calls
        assert asked["reason"] == "review-cap-spent"
        assert kwargs["pr_head"] == OTHER_SHA and kwargs["lane"] == "Green Light"


class TestNewHeadFromTriage:
    """`fix-dispute` and `unfixable-check` — the fix loop's Triage park
    (DRE-6179)."""

    @pytest.mark.parametrize("reason", ["fix-dispute", "unfixable-check"])
    def test_the_stamped_head_still_open_yields_nothing(self, reason):
        card = issue("DRE-12", "Triage", [stamp(reason, SHA)])
        assert plan(card, prs=pr_list(43, SHA, ident="DRE-12"))[0] == []

    @pytest.mark.parametrize("reason", ["fix-dispute", "unfixable-check"])
    def test_a_new_head_is_three_writes_ending_in_in_review(self, reason):
        card = issue("DRE-12", "Triage", [stamp(reason, SHA)])
        items, _, _ = plan(card, prs=pr_list(43, OTHER_SHA, ident="DRE-12"))
        [action] = actions(items)
        assert lefts(items) == []
        assert action.cause == f"reason={reason} because=new-head"
        assert_three_writes(action, "new-head", "In Review")
        assert "lane Triage" in action.evidence

    @pytest.mark.parametrize("reason", ["fix-dispute", "unfixable-check"])
    def test_no_open_pull_request_yields_nothing(self, reason):
        card = issue("DRE-12", "Triage", [stamp(reason, SHA)])
        assert plan(card)[0] == []

    @pytest.mark.parametrize("lane_name", ["Backlog", "Todo", "In Progress", "In Review",
                                           "Green Light", "Planning"])
    @pytest.mark.parametrize("reason", ["fix-dispute", "unfixable-check"])
    def test_any_lane_but_triage_is_two_writes_and_no_move(self, reason, lane_name):
        card = issue("DRE-12", lane_name, [stamp(reason, SHA)])
        items, _, _ = plan(card, prs=pr_list(43, OTHER_SHA, ident="DRE-12"))
        [action] = actions(items)
        assert action.cause == f"reason={reason} because=new-head"
        assert [w.kind for w in action.writes] == ["linear_label", "linear_comment"]

    def test_a_review_cap_stamp_in_triage_does_not_move(self):
        # Only the lane the hold parked the card in is the lane a lift moves
        # it out of: the sweep parks `review-cap-spent` in Green Light.
        card = issue("DRE-12", "Triage", [stamp("review-cap-spent", SHA)])
        [action] = actions(plan(card, prs=pr_list(43, OTHER_SHA, ident="DRE-12"))[0])
        assert lane_moves(action) == []


class TestRepoOnRail:
    """`no-route` — the sweep's Triage park (DRE-6177), lifted by the card's
    current `repo:` label and never by the stamp's qualifier."""

    def test_a_label_corrected_onto_the_rail_lifts_to_planning(self):
        card = issue("DRE-11", "Triage", [stamp("no-route", "repo:legacy-site")],
                     repo="portico")
        items, _, _ = plan(card)
        [action] = actions(items)
        assert lefts(items) == []
        assert action.cause == "reason=no-route because=repo-on-rail"
        assert_three_writes(action, "repo-on-rail", "Planning")

    def test_the_receipt_names_the_stamp_the_live_label_and_the_lane(self):
        card = issue("DRE-11", "Triage", [stamp("no-route", "repo:legacy-site")],
                     repo="portico")
        [action] = actions(plan(card)[0])
        assert "stamp at=repo:legacy-site" in action.evidence
        assert any("repo:portico" in e for e in action.evidence)
        assert "lane Triage" in action.evidence
        evidence_line = action.writes[1].body.splitlines()[1]
        for fact in ("repo:legacy-site", "repo:portico", "lane Triage"):
            assert fact in evidence_line

    def test_a_repo_none_stamp_lifts_once_an_on_rail_label_is_added(self):
        card = issue("DRE-11", "Triage", [stamp("no-route", "repo:none")], repo="portico")
        [action] = actions(plan(card)[0])
        assert action.cause == "reason=no-route because=repo-on-rail"
        assert lane_moves(action) == ["Planning"]

    def test_the_label_unchanged_off_the_rail_yields_nothing(self):
        card = issue("DRE-11", "Triage", [stamp("no-route", "repo:legacy-site")],
                     repo="legacy-site")
        assert plan(card)[0] == []

    def test_a_stamped_slug_now_on_the_rail_lifts_nothing_while_the_label_is_off_it(self):
        card = issue("DRE-11", "Triage", [stamp("no-route", "repo:portico")],
                     repo="legacy-site")
        assert plan(card)[0] == []

    def test_no_repo_label_at_all_yields_nothing(self):
        card = issue("DRE-11", "Triage", [stamp("no-route", "repo:none")], repo=None)
        assert plan(card)[0] == []

    @pytest.mark.parametrize("lane_name", ["Backlog", "Todo", "Planning", "Green Light",
                                           "In Review"])
    def test_any_lane_but_triage_is_two_writes_and_no_move(self, lane_name):
        card = issue("DRE-11", lane_name, [stamp("no-route", "repo:legacy-site")],
                     repo="portico")
        [action] = actions(plan(card)[0])
        assert action.cause == "reason=no-route because=repo-on-rail"
        assert [w.kind for w in action.writes] == ["linear_label", "linear_comment"]

    def test_lift_due_is_asked_with_the_live_labels_and_the_rail(self, lift_due_calls):
        card = issue("DRE-11", "Triage", [stamp("no-route", "repo:legacy-site")],
                     repo="portico")
        plan(card)
        [(asked, kwargs)] = lift_due_calls
        assert asked["reason"] == "no-route"
        assert "repo:portico" in kwargs["labels"]
        assert "portico" in kwargs["rail_slugs"]


class TestSpentStampsOfTheMovingLifts:
    def green_light(self, *after):
        return issue("DRE-10", "Green Light", [stamp("review-cap-spent", SHA), *after])

    def triage(self, *after):
        return issue("DRE-11", "Triage", [stamp("no-route", "repo:legacy-site"), *after],
                     repo="portico")

    def spent_review_cap(self):
        return self.green_light(hold.lift_line("review-cap-spent", "operator",
                                               "scripts/linear_ops.py"))

    def spent_no_route(self):
        return self.triage(hyg_cleared())

    def test_a_lift_line_spends_a_review_cap_stamp_whose_head_moved(self, lift_due_calls):
        items, _, _ = plan(self.spent_review_cap(), prs=pr_list(42, OTHER_SHA))
        assert actions(items) == []
        [row] = lefts(items)
        assert row.target == "DRE-10" and "manual" in row.why
        assert lift_due_calls == []

    def test_a_cleared_receipt_spends_a_no_route_stamp_on_an_on_rail_card(self, lift_due_calls):
        items, _, _ = plan(self.spent_no_route())
        assert actions(items) == []
        [row] = lefts(items)
        assert row.target == "DRE-11" and "manual" in row.why
        assert lift_due_calls == []

    def test_a_fresh_stamp_after_the_lift_line_lifts_again(self):
        card = self.green_light(
            hold.lift_line("review-cap-spent", "operator", "scripts/linear_ops.py"),
            stamp("review-cap-spent", SHA))
        [action] = actions(plan(card, prs=pr_list(42, OTHER_SHA))[0])
        assert lane_moves(action) == ["In Review"]

    def test_a_fresh_stamp_after_the_cleared_receipt_lifts_again(self):
        card = self.triage(hyg_cleared(), stamp("no-route", "repo:legacy-site"))
        [action] = actions(plan(card)[0])
        assert lane_moves(action) == ["Planning"]


def moving_fixture():
    """The DRE-6273 fixtures, each card in its own lane."""
    return [
        issue("DRE-110", "Green Light", [stamp("review-cap-spent", SHA)]),
        issue("DRE-112", "Triage", [stamp("fix-dispute", SHA)]),
        issue("DRE-113", "Triage", [stamp("unfixable-check", SHA)]),
        issue("DRE-111", "Triage", [stamp("no-route", "repo:legacy-site")], repo="portico"),
        issue("DRE-116", "Triage", [stamp("no-route", "repo:legacy-site")],
              repo="legacy-site"),
        issue("DRE-117", "In Review", [stamp("review-cap-spent", SHA)]),
        issue("DRE-118", "Green Light", [stamp("review-cap-spent", SHA),
                                         hold.lift_line("review-cap-spent", "operator",
                                                        "scripts/linear_ops.py")]),
        issue("DRE-119", "Triage", [stamp("no-route", "repo:legacy-site"), hyg_cleared()],
              repo="portico"),
    ]


def moving_prs():
    return {PIPELINE_REPO: [pull(ident, n, OTHER_SHA) for n, ident in enumerate(
        ("DRE-110", "DRE-112", "DRE-113", "DRE-117", "DRE-118"), start=50)]}


class TestNoRowNamesThisCard:
    def test_every_row_is_a_manual_hold_or_a_carry(self, monkeypatch):
        monkeypatch.setenv(CAP_VAR, "3")
        items, _, _ = plan(*moving_fixture(), *every_fixture(), prs=moving_prs())
        assert actions(items)
        for row in lefts(items):
            assert ("manual" in row.why
                    or row.why == "over the per-pass cap of 3 — carried to the next pass"), row
            assert "DRE-6273" not in row.why

    def test_the_lane_no_longer_names_this_card_as_a_later_landing(self):
        assert "lands with DRE-6273" not in MODULE_PATH.read_text(encoding="utf-8")


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
        assert "DRE-6273" not in row.why
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


def listing(prs):
    """The read-only `gh` answering the leg's `gh pr list` off `prs`."""
    def gh(argv):
        repo = argv[argv.index("--repo") + 1] if "--repo" in argv else None
        return json.dumps(prs.get(repo, []))
    return gh


class TestGuardAndScope:
    def test_every_write_passes_the_real_guard(self):
        items, ctx, _ = plan(*every_fixture(), *moving_fixture(), prs=moving_prs())
        writes = [w for a in actions(items) for w in a.writes]
        assert writes
        assert {"In Review", "Planning"} <= {w.lane for w in writes}
        for write in writes:
            hygiene.guard(write, ctx)

    def test_the_only_lane_moves_are_the_moving_lifts_from_their_park(self):
        items, _, _ = plan(*every_fixture(), *moving_fixture(), prs=moving_prs())
        kinds = {w.kind for a in actions(items) for w in a.writes}
        assert kinds <= {"linear_label", "linear_comment", "linear_archived", "linear_state"}
        moves = {a.target: lane_moves(a) for a in actions(items) if lane_moves(a)}
        assert moves == {"DRE-11": ["Planning"], "DRE-110": ["In Review"],
                         "DRE-111": ["Planning"], "DRE-112": ["In Review"],
                         "DRE-113": ["In Review"]}

    def test_the_lane_move_is_the_last_write(self):
        items, _, _ = plan(*moving_fixture(), prs=moving_prs())
        for action in actions(items):
            kinds = [w.kind for w in action.writes]
            assert "linear_state" not in kinds[:-1]
            assert kinds[:2] == ["linear_label", "linear_comment"]

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

    def test_a_moving_lift_outside_the_legs_scope_yields_nothing(self):
        atlas = issue("DRE-71", "Green Light", [stamp("review-cap-spent", SHA)], repo="atlas")
        prs = {"EveryBite/atlas": [pull("DRE-71", 7, OTHER_SHA)]}
        assert plan(atlas, prs=prs)[0] == []
        items, ctx, _ = plan(atlas, owner="EveryBite", prs=prs)
        [action] = actions(items)
        assert lane_moves(action) == ["In Review"]
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
        ctx, _ = context(every_fixture() + moving_fixture(), gh=listing(moving_prs()))
        ledger = hygiene.run_leg({"lanes": {}}, ctx)
        assert ledger["actions"] and all(a["outcome"] == "executed" for a in ledger["actions"])
        assert {w.kind for w in sent} <= {"linear_label", "linear_comment", "linear_archived",
                                          "linear_state"}
        assert {w.lane for w in sent if w.kind == "linear_state"} == {"In Review", "Planning"}

    def test_the_core_discovers_one_holds_lane(self):
        assert [m.__name__ for m in hygiene.discover()].count("hygiene_holds") == 1


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

    def test_a_dry_run_names_each_moving_lifts_lane_move_and_sends_nothing(
        self, monkeypatch, capsys
    ):
        def refuse(write, ctx):
            raise AssertionError(f"a dry run sent {write.describe()}")

        monkeypatch.setattr(hygiene, "discover", lambda lane_dir=None: [need_lane()])
        monkeypatch.setattr(hygiene, "send", refuse)
        monkeypatch.setattr(linear_ops, "comment_records", lambda ident, **k: [])
        monkeypatch.setenv("HYGIENE_DRY_RUN", "1")
        ctx, _ = context(moving_fixture(), gh=listing(moving_prs()),
                         dry_run=os.environ["HYGIENE_DRY_RUN"] == "1")
        ledger = hygiene.run_leg({"lanes": {}}, ctx)
        out = capsys.readouterr().out
        assert ledger["actions"] and all(a["outcome"] == "would" for a in ledger["actions"])
        assert ("would: comment DRE-110: 🧹 hygiene: hyg-hold-cleared — "
                "reason=review-cap-spent because=new-head") in out
        assert "would: state DRE-110 → In Review" in out
        assert ("would: comment DRE-111: 🧹 hygiene: hyg-hold-cleared — "
                "reason=no-route because=repo-on-rail") in out
        assert "would: state DRE-111 → Planning" in out

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
        spent = issue("DRE-12", "Triage", [stamp("fix-dispute", SHA), hyg_cleared()])
        items, _, _ = plan(*cap_fixture(), spent)
        assert [a.target for a in actions(items)] == ["DRE-90"]
        [row] = lefts(items, "DRE-12")
        assert "manual" in row.why

    def test_a_cap_of_two_takes_the_moving_lifts_ahead_of_the_closed_backlog(
        self, monkeypatch
    ):
        base = datetime(2026, 9, 1, tzinfo=UTC)

        def at(days):
            return (base + timedelta(days=days)).isoformat().replace("+00:00", "Z")

        green = issue("DRE-210", "Green Light", [(stamp("review-cap-spent", SHA), at(9))])
        triage = issue("DRE-211", "Triage", [(stamp("no-route", "repo:legacy-site"), at(4))],
                       repo="portico")
        done = [issue("DRE-201", "Done", [(stamp("dead-run-cap"), at(1))]),
                issue("DRE-202", "Done", [(stamp("turn-cap-park"), at(2))]),
                issue("DRE-203", "Done", ["talk"])]
        monkeypatch.setenv(CAP_VAR, "2")
        items, _, _ = plan(*done, green, triage, prs=pr_list(60, OTHER_SHA, ident="DRE-210"))
        lifted = actions(items)
        assert [a.target for a in lifted] == ["DRE-211", "DRE-210"]
        assert [lane_moves(a) for a in lifted] == [["Planning"], ["In Review"]]
        rows = lefts(items)
        assert sorted(r.target for r in rows) == ["DRE-201", "DRE-202", "DRE-203"]
        for row in rows:
            assert row.why == "over the per-pass cap of 2 — carried to the next pass"

    @pytest.mark.parametrize("value", ["", "  "])
    def test_an_empty_value_is_the_default(self, monkeypatch, value):
        monkeypatch.setenv(CAP_VAR, value)
        items, _, _ = plan(*cap_fixture())
        assert len(actions(items)) == 6 and lefts(items) == []

    @pytest.mark.parametrize("value", ["0", "-3", "forty", "2.5", "1e3"])
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

    def test_it_names_the_five_lifts_this_lane_makes(self):
        section = self.text().split("## How a hold lifts", 1)[-1]
        for kind in ("`card-closed`", "`run-started`", "`unpark-marker`",
                     "`new-head`", "`repo-on-rail`"):
            assert kind in section, kind

    def test_it_says_where_each_lift_sends_the_card(self):
        section = self.text().split("## How a hold lifts", 1)[-1]
        assert "Green Light to In Review" in section
        assert "Triage to In Review" in section
        assert "Triage to Planning" in section
        assert "moves nothing" in section
        assert "third write" in section

    def test_it_says_the_no_route_lift_reads_the_current_label(self):
        section = self.text().split("## How a hold lifts", 1)[-1]
        assert "current `repo:` label" in section
        assert "never the stamp" in section

    def test_it_no_longer_says_the_two_lifts_land_later(self):
        text = self.text()
        assert "lands with DRE-6273" not in text
        assert "lift needs a lane move" not in text
        assert "Until then a card whose live" not in text

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
