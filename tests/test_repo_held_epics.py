"""RED-first: an approved epic in a repo held at max_wip 0 is told so once, and
its Queued children are held until the cap is restored (DRE-7754).

When a repo's WIP cap is 0 its sweep goes idle in one line and nothing reaches
the board. On 2026-10-19 that is how DRE-7600 sat approved in
agent-bureau-demo, its children in Queued looking like cards waiting their
turn. `scripts/repo_held_epics.py` is the module that ends the silence; a
sibling card wires it into the sweep.

WHAT IS UNDER TEST, over a fake board that answers the module's reads through
the `gql` it is handed and records every write — nothing here reaches Linear:

  * `hold_pass` on a held repo: one read when no epic of the repo is In
    Progress, two when one is, plus one whole-thread read per partial window.
    It holds each Queued child that waits on the cap (`wip-cap-held`), leaves
    a child held for another reason as it is, and posts one `repo-held`
    receipt unless one already stands — newer than the epic's green light and
    than every `cap-restored` lift line on its Queued children.
  * Unknown is never a write: a green light it cannot read, a whole-thread
    read that does not answer, or a thread still partial after it.
  * A PARKED child is neither held nor listed; a half-written hold (the label
    with no stamp) is left for a person and named.
  * `lift_pass` at a cap above 0: one read, and it lifts only live
    `wip-cap-held` stamps.
  * The registries: the act row, the hold reason, the proof-reach line.

Run: cd bureau-pipeline && python3 -m pytest tests/test_repo_held_epics.py -v
"""

from __future__ import annotations

import json
import os
import subprocess  # nosec B404 — runs this repo's own scripts
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import hold  # noqa: E402
import linear_ops  # noqa: E402
import pipeline_act  # noqa: E402
import repo_held_epics as rhe  # noqa: E402
import routing_verdict  # noqa: E402
import sweep_holds  # noqa: E402

SLUG = "agent-bureau-demo"
EPIC = "DRE-7600"
STUB = ".github/workflows/reconcile.yml"
STUB_COMMENT = (
    "HELD at 0 on 2026-09-08 by CEO decision — this sandbox builds nothing "
    "until the CEO lifts it. Restore it to resume building here."
)
HELD = sweep_holds.SweepHolds(
    cap=0, cap_source=f"the caller's reconcile stub {STUB}", stub_path=STUB,
    stub_comment=STUB_COMMENT, intake_hold=None, max_wip_held=True,
    intake_held=False, held=True,
)
UNHELD = HELD._replace(cap=4, max_wip_held=False, held=False)

APPROVED = "2026-10-19T10:00:00.000Z"
BEFORE = "2026-10-18T09:00:00.000Z"
LATER = "2026-10-20T08:00:00.000Z"
HOLD_LABEL = hold.HOLD_LABEL
WRITER = "scripts/repo_held_epics.py"
MANUAL_LISTING = "held (manual — needs-human with no hold stamp; a person lifts it)"


def _stamp(reason, at="none", by="scripts/linear_ops.py"):
    return hold.stamp_line(reason, at, by)


def _lift(reason="wip-cap-held", because="cap-restored"):
    return hold.lift_line(reason, because, WRITER)


def _receipt(epic=EPIC):
    return pipeline_act.receipt(
        "repo-held-notice",
        f"⏸️ repo-held: {epic} is approved, and {SLUG} builds nothing — its WIP cap is 0.",
    )


PARKED = routing_verdict.verdict_comment("PARKED", "deliberately not built yet")
FLEET = routing_verdict.verdict_comment("FLEET", "an ordinary code card")


class Board:
    """A fake Linear: the module's three queries through `gql`, the whole-thread
    read, and the writes `hold.apply`, `hold.lift` and the receipt make."""

    def __init__(self):
        self.cards: dict[str, dict] = {}
        self.reads: list[str] = []
        self.writes: list[tuple] = []
        self.fail_stamps = 0
        self.whole_fails: set[str] = set()
        self.still_partial: set[str] = set()
        self._tick = 0

    # -- fixtures ----------------------------------------------------------- #

    def add(self, identifier, *, lane="Queued", labels=(), comments=(),
            parent=None, title="a card", history=(), children=False):
        """`comments` are (body, createdAt) oldest→newest."""
        self.cards[identifier] = {
            "id": f"uuid-{identifier}", "identifier": identifier, "title": title,
            "lane": lane, "labels": list(labels),
            "comments": [{"body": b, "createdAt": at} for b, at in comments],
            "parent": parent, "history": list(history), "children": children,
        }
        return self.cards[identifier]

    def epic(self, identifier=EPIC, *, repo=f"repo:{SLUG}", comments=(),
             history=((APPROVED, "In Progress"),), lane="In Progress"):
        return self.add(
            identifier, lane=lane, labels=[repo, "agent:planner"], comments=comments,
            title=f"[EPIC] {identifier}", children=True,
            history=[{"createdAt": at, "toState": {"name": name}} for at, name in history],
        )

    def child(self, identifier, *, epic=EPIC, repo=f"repo:{SLUG}", labels=(),
              comments=((FLEET, BEFORE),), lane="Queued"):
        return self.add(identifier, lane=lane, labels=[repo, *labels],
                        comments=comments, parent=epic)

    def filler(self, identifier, n, at=LATER):
        self.cards[identifier]["comments"] += [
            {"body": f"note {i}", "createdAt": at} for i in range(n)]

    # -- reads -------------------------------------------------------------- #

    @staticmethod
    def _wears(card, needle):
        return any(needle.lower() in name.lower() for name in card["labels"])

    def _window(self, card):
        newest_first = list(reversed(card["comments"]))
        return {"pageInfo": {"hasNextPage": len(newest_first) > linear_ops.COMMENT_WINDOW,
                             "endCursor": "c1"},
                "nodes": [dict(n, user={"id": "bot"})
                          for n in newest_first[:linear_ops.COMMENT_WINDOW]]}

    def _node(self, card):
        return {
            "id": card["id"], "identifier": card["identifier"], "title": card["title"],
            "state": {"name": card["lane"]},
            "labels": {"nodes": [{"name": n} for n in card["labels"]]},
            "children": {"nodes": [{"id": "x"}] if card["children"] else []},
            "parent": ({"identifier": card["parent"],
                        "id": self.cards[card["parent"]]["id"]}
                       if card["parent"] in self.cards else None),
            "history": {"nodes": card["history"]},
            "comments": self._window(card),
        }

    def gql(self, query, variables=None):
        variables = variables or {}
        self.reads.append(query)
        if query == rhe.EPICS_QUERY:
            found = [c for c in self.cards.values()
                     if c["lane"] in variables["states"]
                     and self._wears(c, variables["needle"])]
        elif query == rhe.CHILDREN_QUERY:
            ids = set(variables["ids"])
            found = [c for c in self.cards.values()
                     if c["lane"] in variables["states"] and c["parent"]
                     and self.cards[c["parent"]]["id"] in ids]
        elif query == rhe.HELD_QUERY:
            found = [c for c in self.cards.values()
                     if c["lane"] in variables["states"]
                     and self._wears(c, variables["needle"])
                     and any(n.lower() == HOLD_LABEL for n in c["labels"])]
        else:  # pragma: no cover — a query the module must not send
            raise AssertionError(f"unexpected query: {query[:80]}")
        return {"issues": {"nodes": [self._node(c) for c in found],
                           "pageInfo": {"hasNextPage": False, "endCursor": None}}}

    def timeline(self, identifier, *, whole_thread=False):
        assert whole_thread, "the module reads a thread whole or not at all"
        self.reads.append(f"whole:{identifier}")
        if identifier in self.whole_fails:
            raise linear_ops.LinearError(f"{identifier}: 502 from Linear")
        nodes = self.cards[identifier]["comments"]
        if identifier in self.still_partial:
            nodes = nodes[-linear_ops.COMMENT_WINDOW:]
        return [{"body": n["body"], "createdAt": n["createdAt"]} for n in nodes]

    # -- writes ------------------------------------------------------------- #

    def _now(self):
        self._tick += 1
        return f"2026-10-21T00:00:{self._tick:02d}.000Z"

    def add_label(self, identifier, label):
        self.writes.append(("label+", identifier, label))
        if label not in self.cards[identifier]["labels"]:
            self.cards[identifier]["labels"].append(label)

    def remove_label(self, identifier, label):
        self.writes.append(("label-", identifier, label))
        self.cards[identifier]["labels"] = [
            n for n in self.cards[identifier]["labels"] if n.lower() != label.lower()]

    def cmd_comment(self, identifier, body, *flags):
        if body.startswith(hold.STAMP_PREFIX) and self.fail_stamps:
            self.fail_stamps -= 1
            raise linear_ops.LinearError("commentCreate: 500 from Linear")
        self.writes.append(("comment", identifier, body))
        self.cards[identifier]["comments"].append(
            {"body": body, "createdAt": self._now()})
        return None

    # -- what the tests read ------------------------------------------------ #

    def comments_on(self, identifier, prefix=""):
        return [b for (kind, card, b) in self.writes
                if kind == "comment" and card == identifier and b.startswith(prefix)]

    def receipts(self, identifier=EPIC):
        return self.comments_on(identifier, "⏸️ repo-held:")

    def stamps(self, identifier):
        return self.comments_on(identifier, hold.STAMP_PREFIX)

    def held(self, identifier):
        return HOLD_LABEL in self.cards[identifier]["labels"]

    def query_reads(self):
        return [r for r in self.reads if not r.startswith("whole:")]

    def whole_reads(self):
        return [r[len("whole:"):] for r in self.reads if r.startswith("whole:")]


@pytest.fixture
def board():
    b = Board()
    with mock.patch.object(linear_ops, "add_label", b.add_label), \
            mock.patch.object(linear_ops, "remove_label", b.remove_label), \
            mock.patch.object(linear_ops, "cmd_comment", b.cmd_comment), \
            mock.patch.object(linear_ops, "comment_timeline", b.timeline), \
            mock.patch.object(linear_ops, "gql", b.gql):
        yield b


def _three_children(board):
    board.epic()
    board.child("DRE-7601")
    board.child("DRE-7602", labels=[HOLD_LABEL],
                comments=((FLEET, BEFORE), (_stamp("operator-step"), BEFORE)))
    board.child("DRE-7603")


def _hold_pass(board, holds=HELD):
    return rhe.hold_pass(SLUG, holds, gql=board.gql)


def _lift_pass(board, cap=3):
    return rhe.lift_pass(SLUG, cap, gql=board.gql)


# --------------------------------------------------------------------------- #
# hold_pass                                                                    #
# --------------------------------------------------------------------------- #


class TestHeldAndApproved:
    def test_one_receipt_and_two_holds(self, board):
        _three_children(board)
        lines = _hold_pass(board)

        (receipt,) = board.receipts()
        first = receipt.splitlines()[0]
        assert first == (f"⏸️ repo-held: {EPIC} is approved, and {SLUG} builds "
                         "nothing — its WIP cap is 0.")
        assert STUB in receipt and STUB_COMMENT in receipt
        assert "DRE-7601 — held (wip-cap-held)" in receipt
        assert "DRE-7603 — held (wip-cap-held)" in receipt
        assert "DRE-7602 — held (operator-step)" in receipt
        assert "max_wip" in receipt.split("DRE-7603", 1)[1]
        assert pipeline_act.read_trailer(receipt)["act"] == "repo-held-notice"

        for child in ("DRE-7601", "DRE-7603"):
            assert board.held(child)
            (stamp,) = board.stamps(child)
            assert stamp == (f"🔒 hold: reason=wip-cap-held at=none "
                             f"lifts=cap-restored by={WRITER}")
        assert board.stamps("DRE-7602") == []
        assert hold.reason_of(board.cards["DRE-7602"]["labels"],
                              [c["body"] for c in board.cards["DRE-7602"]["comments"]]
                              ) == "operator-step"
        assert lines == [f"repo-held: {EPIC} — notice posted; held DRE-7601, DRE-7603"]

    def test_every_read_comes_before_any_write(self, board):
        _three_children(board)
        order = []
        board_gql = board.gql
        board_comment = board.cmd_comment

        def gql(query, variables=None):
            order.append("read")
            return board_gql(query, variables)

        def comment(identifier, body, *flags):
            order.append("write")
            return board_comment(identifier, body, *flags)

        with mock.patch.object(linear_ops, "cmd_comment", comment):
            rhe.hold_pass(SLUG, HELD, gql=gql)
        assert "read" not in order[order.index("write"):]

    def test_the_receipt_follows_the_holds(self, board):
        _three_children(board)
        _hold_pass(board)
        kinds = [(w[0], w[1]) for w in board.writes]
        assert kinds[-1] == ("comment", EPIC)


class TestExactlyOneNotice:
    def test_a_second_pass_writes_nothing(self, board):
        _three_children(board)
        _hold_pass(board)
        board.writes.clear()
        lines = _hold_pass(board)
        assert board.writes == []
        assert lines == [f"repo-held: {EPIC} — notice already stands; held none"]

    def test_a_new_child_is_held_under_a_standing_notice(self, board):
        _three_children(board)
        _hold_pass(board)
        board.child("DRE-7604")
        board.writes.clear()
        lines = _hold_pass(board)
        assert board.receipts() == []
        assert board.held("DRE-7604")
        assert lines == [f"repo-held: {EPIC} — notice already stands; held DRE-7604"]


class TestReceiptPastTheWindow:
    def test_the_whole_thread_finds_the_receipt(self, board):
        board.epic(comments=((_receipt(), "2026-10-19T11:00:00.000Z"),))
        board.filler(EPIC, linear_ops.COMMENT_WINDOW)
        board.child("DRE-7601", labels=[HOLD_LABEL],
                    comments=((FLEET, BEFORE),
                              (_stamp("wip-cap-held", by=WRITER), APPROVED)))
        lines = _hold_pass(board)
        assert board.whole_reads() == [EPIC]
        assert board.writes == []
        assert lines == [f"repo-held: {EPIC} — notice already stands; held none"]

    def test_judged_by_the_window_alone_it_would_post_again(self, board):
        # The same board with the receipt pushed out of the window: the
        # window by itself carries no receipt, so the whole read is the proof.
        board.epic(comments=((_receipt(), "2026-10-19T11:00:00.000Z"),))
        board.filler(EPIC, linear_ops.COMMENT_WINDOW)
        window = board._window(board.cards[EPIC])
        assert linear_ops.window_is_partial(window)
        assert not any(n["body"].startswith("⏸️ repo-held:") for n in window["nodes"])


class TestReapproved:
    def test_a_newer_green_light_gets_a_new_receipt(self, board):
        board.epic(comments=((_receipt(), "2026-10-19T11:00:00.000Z"),),
                   history=((APPROVED, "In Progress"), (BEFORE, "Planning"),
                            (LATER, "In Progress")))
        board.child("DRE-7601", labels=[HOLD_LABEL],
                    comments=((FLEET, BEFORE),
                              (_stamp("wip-cap-held", by=WRITER), APPROVED)))
        lines = _hold_pass(board)
        assert len(board.receipts()) == 1
        assert board.stamps("DRE-7601") == []
        assert lines == [f"repo-held: {EPIC} — notice posted; held none"]

    def test_an_older_green_light_leaves_the_receipt_standing(self, board):
        board.epic(comments=((_receipt(), LATER),))
        board.child("DRE-7601", labels=[HOLD_LABEL],
                    comments=((FLEET, BEFORE),
                              (_stamp("wip-cap-held", by=WRITER), LATER)))
        assert _hold_pass(board) == [
            f"repo-held: {EPIC} — notice already stands; held none"]
        assert board.writes == []


class TestReheldAfterALift:
    def test_a_lift_newer_than_the_receipt_gets_a_new_receipt(self, board):
        board.epic(comments=((_receipt(), "2026-10-19T11:00:00.000Z"),))
        board.child("DRE-7601", comments=(
            (FLEET, BEFORE),
            (_stamp("wip-cap-held", by=WRITER), "2026-10-19T11:00:00.000Z"),
            (_lift(), LATER)))
        board.child("DRE-7602", comments=(
            (FLEET, BEFORE),
            (_stamp("wip-cap-held", by=WRITER), "2026-10-19T11:00:00.000Z"),
            (_lift(), "2026-10-19T12:00:00.000Z")))
        lines = _hold_pass(board)
        assert len(board.receipts()) == 1
        assert board.held("DRE-7601") and board.held("DRE-7602")
        assert len(board.stamps("DRE-7601")) == 1 and len(board.stamps("DRE-7602")) == 1
        assert lines == [f"repo-held: {EPIC} — notice posted; held DRE-7601, DRE-7602"]

    def test_a_lift_line_of_another_reason_does_not_count(self, board):
        board.epic(comments=((_receipt(), "2026-10-19T11:00:00.000Z"),))
        board.child("DRE-7601", comments=(
            (FLEET, BEFORE), (_lift("operator-step", "blockers-terminal"), LATER)))
        lines = _hold_pass(board)
        assert board.receipts() == []
        assert lines == [f"repo-held: {EPIC} — notice already stands; held DRE-7601"]


class TestUnknownIsNeverAWrite:
    def test_no_green_light_in_the_history(self, board):
        board.epic(history=((BEFORE, "Planning"), (APPROVED, "Green Light")))
        board.child("DRE-7601")
        lines = _hold_pass(board)
        assert board.writes == []
        assert lines == [f"repo-held: {EPIC} — green light unreadable; nothing written"]

    def test_a_whole_thread_read_that_raises(self, board):
        board.epic()
        board.filler(EPIC, linear_ops.COMMENT_WINDOW + 1)
        board.child("DRE-7601")
        board.whole_fails.add(EPIC)
        lines = _hold_pass(board)
        assert board.writes == []
        assert lines == [f"repo-held: {EPIC} — comments of {EPIC} unreadable; nothing written"]

    def test_a_childs_whole_thread_read_that_raises(self, board):
        board.epic()
        board.child("DRE-7601")
        board.child("DRE-7602")
        board.filler("DRE-7602", linear_ops.COMMENT_WINDOW + 1, at=BEFORE)
        board.whole_fails.add("DRE-7602")
        lines = _hold_pass(board)
        assert board.writes == []
        assert lines == [
            f"repo-held: {EPIC} — comments of DRE-7602 unreadable; nothing written"]

    def test_a_thread_still_partial_after_its_whole_read(self, board):
        board.epic()
        board.filler(EPIC, linear_ops.COMMENT_WINDOW + 5)
        board.child("DRE-7601")
        board.still_partial.add(EPIC)
        lines = _hold_pass(board)
        assert board.writes == []
        assert lines == [f"repo-held: {EPIC} — comments of {EPIC} unreadable; nothing written"]

    def test_one_unknown_epic_does_not_stop_another(self, board):
        board.epic()
        board.child("DRE-7601")
        board.epic("DRE-7700", history=((BEFORE, "Planning"),))
        board.child("DRE-7701", epic="DRE-7700")
        lines = _hold_pass(board)
        assert len(board.receipts(EPIC)) == 1 and board.receipts("DRE-7700") == []
        assert board.held("DRE-7601") and not board.held("DRE-7701")
        assert sorted(lines) == sorted([
            f"repo-held: {EPIC} — notice posted; held DRE-7601",
            "repo-held: DRE-7700 — green light unreadable; nothing written",
        ])


class TestParkedAndBlocked:
    def test_a_parked_child_is_neither_held_nor_listed(self, board):
        board.epic()
        board.child("DRE-7601", comments=((PARKED, BEFORE),))
        board.child("DRE-7602")  # blocked by an open sibling: held all the same
        lines = _hold_pass(board)
        (receipt,) = board.receipts()
        assert "DRE-7601" not in receipt
        assert "DRE-7602 — held (wip-cap-held)" in receipt
        assert not board.held("DRE-7601") and board.held("DRE-7602")
        assert lines == [f"repo-held: {EPIC} — notice posted; held DRE-7602"]

    def test_a_parked_verdict_past_the_window_still_counts(self, board):
        board.epic()
        board.child("DRE-7601", comments=((PARKED, BEFORE),))
        board.filler("DRE-7601", linear_ops.COMMENT_WINDOW, at=BEFORE)
        lines = _hold_pass(board)
        assert board.whole_reads() == ["DRE-7601"]
        assert board.writes == []
        assert lines == [
            f"repo-held: {EPIC} — no Queued child waits on the cap; nothing written"]

    def test_an_epic_whose_only_child_is_parked_gets_nothing(self, board):
        board.epic()
        board.child("DRE-7601", comments=((PARKED, BEFORE),))
        lines = _hold_pass(board)
        assert board.writes == []
        assert lines == [
            f"repo-held: {EPIC} — no Queued child waits on the cap; nothing written"]

    def test_an_epic_with_no_queued_child_gets_nothing(self, board):
        board.epic()
        board.child("DRE-7601", lane="In Review")
        lines = _hold_pass(board)
        assert board.writes == []
        assert lines == [
            f"repo-held: {EPIC} — no Queued child waits on the cap; nothing written"]


class TestHalfWrittenHold:
    def test_the_failed_stamp_raises_and_is_then_left_for_a_person(self, board):
        board.epic()
        board.child("DRE-7601")
        board.child("DRE-7602")
        board.fail_stamps = 1
        with pytest.raises(linear_ops.LinearError):
            _hold_pass(board)
        assert board.held("DRE-7601") and board.stamps("DRE-7601") == []
        assert board.receipts() == []

        board.writes.clear()
        lines = _hold_pass(board)
        assert board.stamps("DRE-7601") == []
        assert ("label+", "DRE-7601", HOLD_LABEL) not in board.writes
        (receipt,) = board.receipts()
        assert f"DRE-7601 — {MANUAL_LISTING}" in receipt
        assert "DRE-7602 — held (wip-cap-held)" in receipt
        assert (f"repo-held: {EPIC} — DRE-7601 wears needs-human with no hold "
                "stamp; left for a person") in lines
        assert f"repo-held: {EPIC} — notice posted; held DRE-7602" in lines

        board.writes.clear()
        again = _hold_pass(board)
        assert board.writes == []
        assert (f"repo-held: {EPIC} — DRE-7601 wears needs-human with no hold "
                "stamp; left for a person") in again

        board.writes.clear()
        lifted = _lift_pass(board, cap=2)
        assert board.held("DRE-7601")
        assert ("label-", "DRE-7601", HOLD_LABEL) not in board.writes
        assert lifted == ["repo-held: lifted DRE-7602 (cap-restored, max_wip 2)"]

    def test_a_person_stamped_manual_hold_is_listed_by_its_reason(self, board):
        board.epic()
        board.child("DRE-7601", labels=[HOLD_LABEL],
                    comments=((FLEET, BEFORE), (_stamp("manual"), BEFORE)))
        lines = _hold_pass(board)
        (receipt,) = board.receipts()
        assert "DRE-7601 — held (manual)" in receipt
        assert MANUAL_LISTING not in receipt
        assert lines == [f"repo-held: {EPIC} — notice posted; held none"]


class TestUnheld:
    def test_neither_pass_touches_an_unheld_repo(self, board):
        _three_children(board)
        assert _hold_pass(board, UNHELD) == []
        assert board.reads == [] and board.writes == []
        assert _lift_pass(board, cap=4) == []
        assert board.writes == []
        assert board.receipts() == []

    def test_lift_pass_at_cap_zero_reads_nothing(self, board):
        _three_children(board)
        assert _lift_pass(board, cap=0) == []
        assert board.reads == [] and board.writes == []


class TestScope:
    def test_the_repo_label_is_matched_the_way_routing_matches_it(self, board):
        board.epic(repo="repo:dreadnought-foundry/Agent-Bureau-Demo")
        board.child("DRE-7601", repo="repo:Agent-Bureau-Demo")
        lines = _hold_pass(board)
        assert len(board.receipts()) == 1 and board.held("DRE-7601")
        assert lines == [f"repo-held: {EPIC} — notice posted; held DRE-7601"]

    def test_a_longer_slug_containing_this_one_is_another_repo(self, board):
        board.epic(repo=f"repo:{SLUG}-two")
        board.child("DRE-7601", repo=f"repo:{SLUG}-two")
        lines = _hold_pass(board)
        assert board.writes == []
        assert lines == [f"repo-held: no approved epic of {SLUG} is In Progress; "
                         "nothing written"]

    def test_a_one_off_in_progress_is_not_an_epic(self, board):
        board.add("DRE-7650", lane="In Progress", labels=[f"repo:{SLUG}"])
        lines = _hold_pass(board)
        assert len(board.query_reads()) == 1
        assert lines == [f"repo-held: no approved epic of {SLUG} is In Progress; "
                         "nothing written"]

    def test_a_child_of_another_repo_is_not_held(self, board):
        board.epic()
        board.child("DRE-7601")
        board.child("DRE-7602", repo="repo:portico")
        _hold_pass(board)
        (receipt,) = board.receipts()
        assert "DRE-7602" not in receipt and not board.held("DRE-7602")

    def test_another_repos_epic_with_a_child_here_is_not_read(self, board):
        board.epic("DRE-7800", repo="repo:portico")
        board.child("DRE-7801", epic="DRE-7800")
        lines = _hold_pass(board)
        assert board.writes == []
        assert lines == [f"repo-held: no approved epic of {SLUG} is In Progress; "
                         "nothing written"]


# --------------------------------------------------------------------------- #
# lift_pass                                                                    #
# --------------------------------------------------------------------------- #


class TestLift:
    def test_only_wip_cap_held_children_are_lifted(self, board):
        board.epic()
        board.child("DRE-7601", labels=[HOLD_LABEL], comments=(
            (FLEET, BEFORE), (_stamp("wip-cap-held", by=WRITER), APPROVED)))
        board.child("DRE-7602", labels=[HOLD_LABEL], comments=(
            (FLEET, BEFORE), (_stamp("operator-step"), BEFORE)))
        board.child("DRE-7603", labels=[HOLD_LABEL], comments=((FLEET, BEFORE),))
        board.child("DRE-7604", labels=[HOLD_LABEL], comments=(
            (FLEET, BEFORE), (_stamp("wip-cap-held", by=WRITER), APPROVED)))
        board.child("DRE-7605", labels=[HOLD_LABEL], comments=(
            (FLEET, BEFORE), (_stamp("wip-cap-held", by=WRITER), BEFORE),
            (_lift(), APPROVED), (_stamp("dead-run-cap"), LATER)))
        lines = _lift_pass(board, cap=3)
        assert not board.held("DRE-7601") and not board.held("DRE-7604")
        assert board.held("DRE-7602") and board.held("DRE-7603") and board.held("DRE-7605")
        for child in ("DRE-7601", "DRE-7604"):
            assert board.comments_on(child, hold.LIFT_PREFIX) == [
                f"🔓 hold lifted: reason=wip-cap-held because=cap-restored by={WRITER}"]
        assert lines == ["repo-held: lifted DRE-7601 (cap-restored, max_wip 3)",
                         "repo-held: lifted DRE-7604 (cap-restored, max_wip 3)"]
        assert len(board.reads) == 1

    def test_a_partial_window_is_read_whole_before_the_lift(self, board):
        board.epic()
        board.child("DRE-7601", labels=[HOLD_LABEL], comments=(
            (FLEET, BEFORE), (_stamp("wip-cap-held", by=WRITER), APPROVED)))
        board.filler("DRE-7601", linear_ops.COMMENT_WINDOW)
        lines = _lift_pass(board, cap=1)
        assert board.whole_reads() == ["DRE-7601"]
        assert not board.held("DRE-7601")
        assert lines == ["repo-held: lifted DRE-7601 (cap-restored, max_wip 1)"]

    def test_an_unreadable_whole_thread_is_not_lifted(self, board):
        board.epic()
        board.child("DRE-7601", labels=[HOLD_LABEL], comments=(
            (FLEET, BEFORE), (_stamp("wip-cap-held", by=WRITER), APPROVED)))
        board.filler("DRE-7601", linear_ops.COMMENT_WINDOW)
        board.whole_fails.add("DRE-7601")
        lines = _lift_pass(board, cap=1)
        assert board.writes == [] and board.held("DRE-7601")
        assert lines == ["repo-held: DRE-7601 — comments of DRE-7601 unreadable; "
                         "nothing written"]

    def test_another_repos_wip_cap_hold_is_not_lifted_here(self, board):
        board.epic("DRE-7800", repo=f"repo:{SLUG}-two")
        board.child("DRE-7801", epic="DRE-7800", repo=f"repo:{SLUG}-two",
                    labels=[HOLD_LABEL], comments=(
                        (FLEET, BEFORE), (_stamp("wip-cap-held", by=WRITER), APPROVED)))
        assert _lift_pass(board, cap=2) == []
        assert board.held("DRE-7801")

    def test_a_failed_read_raises_before_any_write(self, board):
        def broken(query, variables=None):
            raise linear_ops.LinearError("502 from Linear")

        with pytest.raises(linear_ops.LinearError):
            rhe.lift_pass(SLUG, 2, gql=broken)
        with pytest.raises(linear_ops.LinearError):
            rhe.hold_pass(SLUG, HELD, gql=broken)
        assert board.writes == []


class TestHoldThenLift:
    def test_a_restored_cap_releases_what_the_hold_pass_held(self, board):
        _three_children(board)
        _hold_pass(board)
        lines = _lift_pass(board, cap=2)
        assert not board.held("DRE-7601") and not board.held("DRE-7603")
        assert board.held("DRE-7602")
        assert lines == ["repo-held: lifted DRE-7601 (cap-restored, max_wip 2)",
                         "repo-held: lifted DRE-7603 (cap-restored, max_wip 2)"]
        # Back to 0: held again, and a fresh receipt names the new hold.
        board.writes.clear()
        lines = _hold_pass(board)
        assert len(board.receipts()) == 1
        assert lines == [f"repo-held: {EPIC} — notice posted; held DRE-7601, DRE-7603"]


# --------------------------------------------------------------------------- #
# the request count                                                            #
# --------------------------------------------------------------------------- #


class TestRequestCount:
    def test_no_epic_is_one_read_and_no_write(self, board):
        board.child("DRE-7601", epic=None)
        lines = _hold_pass(board)
        assert board.reads == [rhe.EPICS_QUERY]
        assert board.writes == []
        assert lines == [f"repo-held: no approved epic of {SLUG} is In Progress; "
                         "nothing written"]

    def test_an_epic_is_two_reads_with_no_partial_window(self, board):
        _three_children(board)
        _hold_pass(board)
        assert board.reads == [rhe.EPICS_QUERY, rhe.CHILDREN_QUERY]

    def test_one_partial_window_is_three(self, board):
        _three_children(board)
        board.filler("DRE-7601", linear_ops.COMMENT_WINDOW, at=BEFORE)
        _hold_pass(board)
        assert len(board.reads) == 3
        assert board.whole_reads() == ["DRE-7601"]

    def test_lift_pass_is_one_read(self, board):
        _three_children(board)
        _lift_pass(board, cap=2)
        assert board.reads == [rhe.HELD_QUERY]

    def test_the_epic_read_asks_for_this_repo_in_progress(self, board):
        seen = []

        def gql(query, variables=None):
            seen.append(variables)
            return board.gql(query, variables)

        rhe.hold_pass(SLUG, HELD, gql=gql)
        assert seen[0]["needle"] == SLUG and seen[0]["states"] == ["In Progress"]
        for field in ("history(first: 50)", linear_ops.COMMENT_WINDOW_GQL,
                      "containsIgnoreCase"):
            assert field in rhe.EPICS_QUERY
        assert linear_ops.COMMENT_WINDOW_GQL in rhe.CHILDREN_QUERY
        assert linear_ops.COMMENT_WINDOW_GQL in rhe.HELD_QUERY
        assert HOLD_LABEL in rhe.HELD_QUERY


# --------------------------------------------------------------------------- #
# the two words                                                                #
# --------------------------------------------------------------------------- #


class TestTheTwoWords:
    def test_the_hold_lines_never_carry_the_act_tag(self, board):
        _three_children(board)
        _hold_pass(board)
        _lift_pass(board, cap=2)
        stamps = board.stamps("DRE-7601") + board.stamps("DRE-7603")
        lifts = (board.comments_on("DRE-7601", hold.LIFT_PREFIX)
                 + board.comments_on("DRE-7603", hold.LIFT_PREFIX))
        assert len(stamps) == 2 and len(lifts) == 2
        for line in stamps + lifts:
            assert "repo-held" not in line, line
            assert "wip-cap-held" in line
        (receipt,) = board.receipts()
        assert receipt.splitlines()[0].startswith("⏸️ repo-held:")

    def test_the_constants(self):
        assert rhe.LOG_PREFIX == "repo-held:"
        assert rhe.TAG == "repo-held"
        assert rhe.ACT == "repo-held-notice"
        assert rhe.HOLD_REASON == "wip-cap-held"
        assert rhe.LIFT_KIND == "cap-restored"
        assert rhe.WRITER == WRITER
        assert rhe.TAG != rhe.HOLD_REASON
        assert rhe.TAG not in rhe.HOLD_REASON and rhe.TAG not in rhe.LIFT_KIND

    def test_every_line_opens_with_the_log_prefix(self, board):
        _three_children(board)
        board.epic("DRE-7700", history=((BEFORE, "Planning"),))
        board.child("DRE-7701", epic="DRE-7700")
        lines = _hold_pass(board) + _lift_pass(board, cap=2)
        assert lines and all(line.startswith(rhe.LOG_PREFIX) for line in lines)


# --------------------------------------------------------------------------- #
# the registries                                                               #
# --------------------------------------------------------------------------- #


def _act_row():
    (row,) = [a for a in json.loads((ROOT / "config" / "pipeline-acts.json")
                                    .read_text(encoding="utf-8"))["acts"]
              if a["name"] == "repo-held-notice"]
    return row


class TestTheRegistries:
    def test_the_act_row(self):
        row = _act_row()
        assert row["tag"] == "repo-held" and row["kind"] == "hold"
        assert row["state"] == "held" and row["next_actor"] == "operator"
        assert row["subscriber"] == "reconcile.yml"
        assert row["adopted"] is True and row["discharges"] is None
        assert row["cadence_s"] is None and row["cadence_why"].strip()
        assert row["means"].strip()
        for name in ("DRE-7671", "DRE-7600", "DRE-7751"):
            assert name in row["why"], name
        assert row["emits"]["file"] == WRITER
        source = (ROOT / WRITER).read_text(encoding="utf-8")
        assert source.count(row["emits"]["anchor"]) == 1

    def test_pipeline_act_check_passes(self):
        out = subprocess.run([sys.executable, str(ROOT / "scripts" / "pipeline_act.py"),
                              "check"], capture_output=True, text=True, cwd=ROOT)
        assert out.returncode == 0, out.stdout + out.stderr

    def test_the_receipt_is_composed_through_the_registry(self):
        import check_act_receipts

        assert check_act_receipts.problems() == []
        sites = [s for s in check_act_receipts.python_sites()
                 if s.path.endswith("repo_held_epics.py")]
        assert [s.composed_as for s in sites] == ["repo-held-notice"]

    def test_the_hold_reason_and_its_site(self):
        doc = json.loads((ROOT / "config" / "holds.json").read_text(encoding="utf-8"))
        assert doc["reasons"]["wip-cap-held"]["lifts"] == "cap-restored"
        assert doc["reasons"]["wip-cap-held"]["lift_sends"] == "nothing moves"
        assert "cap-restored" in doc["lift_kinds"]
        (row,) = [r for r in doc["sites"] if r["file"] == WRITER]
        assert row["readers"] == ["sweep"]
        assert [e["reason"] for e in row["reasons"]] == ["wip-cap-held"]
        assert hold.problems() == []

    def test_the_lift_is_the_sweeps_alone(self):
        stamp = hold.read_stamp([hold.stamp_line("wip-cap-held", None, WRITER)])
        assert stamp["lifts"] == "cap-restored"
        assert hold.lift_due(stamp, lane="Queued", labels=[HOLD_LABEL], pr_head=None,
                             rail_slugs=(), bodies=[stamp["line"], "🧠 run"]) is None
        assert hold.lift_due(stamp, lane="Done", labels=[], pr_head=None,
                             rail_slugs=(), bodies=[]) == "card-closed"

    def test_the_proof_reach_log_line(self):
        doc = json.loads((ROOT / "config" / "proof-reach.json").read_text(encoding="utf-8"))
        (entry,) = [e for e in doc["log_lines"] if e["tag"] == "repo-held:"]
        assert entry["printed_by"] == WRITER and entry["constant"] == "LOG_PREFIX"
        assert "max_wip 0" in entry["when"]

    def test_the_pages_carry_the_sections(self):
        holds_page = (ROOT / "docs" / "holds.md").read_text(encoding="utf-8")
        assert "`wip-cap-held`" in holds_page and "`cap-restored`" in holds_page
        assert "| `wip-cap-held` | `cap-restored` |" in holds_page
        acts_page = (ROOT / "docs" / "pipeline-acts.md").read_text(encoding="utf-8")
        assert "`repo-held`" in acts_page and "`repo-held-notice`" in acts_page

    def test_import_is_safe(self):
        env = {k: v for k, v in os.environ.items()
               if k not in ("LINEAR_API_KEY", "REPO", "REPO_SLUG")}
        out = subprocess.run(
            [sys.executable, "-c", "import repo_held_epics"],
            capture_output=True, text=True, cwd=ROOT / "scripts", env=env)
        assert out.returncode == 0, out.stderr
