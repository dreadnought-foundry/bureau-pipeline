"""RED-first: a held proof and a Hand-work card still waiting at their due date
are listed in one late-hold alert (DRE-7965).

THE GAP: `alarm_held_proofs` posts one `🚨 hand-work-overdue` comment per
operator hold at 24 hours and never speaks again, and it does not read a proof
hold that waits on an event at all. `_hand_work_age`, from `flag_stranded`,
does the same for the Hand-work lane. `hold_deadline.settle` (DRE-7964) is the
one writer of a late-hold alert, and nothing in the sweep called it.

FIX UNDER TEST:

  * `_proof_wait_clock(card)` reads a `PROOF:` card's open holds over its
    whole thread — the window when it is the whole thread, one paged read when
    it is partial — and names the earliest clock among the qualifying holds:
    every open hold but the CEO's press and a time-only hold whose time is
    still ahead; a passed time-only hold's clock is the later of its post and
    its time;
  * `alarm_held_proofs` lists the late ones under `proof-waiting`, and
    `flag_stranded` lists the alarmed Hand-work cards past their entry's due
    date under `hand-work-overdue`, leaving out every `PROOF:` card with a
    proof-waiting clock — each calls `settle` once, after its loop;
  * an unjudged card makes that site's pass incomplete, and a pass whose read
    was skipped — idle, held, `promote_only` — calls `settle` not at all.

Run: cd bureau-pipeline && python3 -m pytest tests/test_hold_deadline_sweep.py -v
"""
from __future__ import annotations

import contextlib
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/portico")
os.environ.setdefault("REPO_SLUG", "portico")
os.environ.setdefault("GH_TOKEN", "x")

import hold_deadline  # noqa: E402
import linear_ops  # noqa: E402
import pipeline_act  # noqa: E402
import pipeline_notice  # noqa: E402
import proof_cause_park  # noqa: E402
import proof_record  # noqa: E402
import proof_run_state  # noqa: E402
import reconcile  # noqa: E402
import routing_verdict  # noqa: E402
import validate_card  # noqa: E402

PROOF_TAG = "proof-waiting"
HAND_TAG = "hand-work-overdue"
REVIEW = "In Review"
HAND_WORK = "Hand-work"
OPERATOR_STEP = "operator-step"
NO_CODE = "no-code"
PROMOTED = ("🧹 Auto-promoted Backlog → Hand-work: routed **OPERATOR** — a person "
            "builds this; nothing was dispatched.")
CAUSE_NEEDS = (f"{proof_cause_park.CAUSE_HOLD_OPENING} 1 row(s) the run cannot "
               "observe as worded (cause: out-of-reach)")
EVENT_NEEDS = "the weekly report of 2026-10-18"
CEO_NEEDS = f"{proof_record.CEO_PRESS}: approve the release on the console"
SHIPPED_NEEDS = "DRE-1234 shipped"
OBSERVED = linear_ops.proof_observed_line("the board showed the age at 09:00 PT")


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    """This sweep owns portico, portico is on the rail, nothing is fetched."""
    monkeypatch.setattr(reconcile, "REPO_SLUG", "portico")
    monkeypatch.setattr(reconcile, "REPO", "dreadnought-foundry/portico")
    monkeypatch.setattr(validate_card, "VALID_SLUGS", {"portico", "atlas"})
    monkeypatch.setattr(reconcile, "live_rail_slugs",
                        lambda: frozenset({"portico", "atlas"}), raising=False)
    monkeypatch.setattr(reconcile, "_idle_pass", [])
    reconcile._write_failures.clear()
    reconcile._stale_defects.clear()
    yield
    reconcile._write_failures.clear()
    reconcile._stale_defects.clear()


# --------------------------------------------------------------------------- #
# the board's cards                                                           #
# --------------------------------------------------------------------------- #


def _at(hours_ago: float) -> datetime:
    return datetime.now(UTC) - timedelta(hours=hours_ago)


def _iso(when: datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _parse(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def _node(body: str, hours_ago: float) -> dict:
    return {"body": body, "createdAt": _iso(_at(hours_ago)), "user": {"id": "bot"}}


def _hold(needs: str, hours_ago: float, observed="the board shows the age") -> dict:
    return _node(linear_ops.proof_waiting_line(observed, needs), hours_ago)


def _until(hours_ago: float) -> str:
    """The `needs` half of a time-only hold whose time is `hours_ago` hours
    ago (negative: still ahead)."""
    when = _at(hours_ago).replace(microsecond=0)
    return f"{linear_ops.NOT_BEFORE}{when.isoformat()}"


def _alarm(ident: str, hours_ago: float) -> dict:
    return _node(pipeline_act.receipt(HAND_TAG, (
        f"🚨 hand-work-overdue: {ident} has waited in Hand-work 25 hours, past "
        "its 24 — the operator's to look at.\nKind: earlier")), hours_ago)


def _card(ident="DRE-7101", *, title="PROOF: the board shows the age",
          state=REVIEW, nodes=(), labels=("repo:portico", NO_CODE),
          partial=False, parent=None) -> dict:
    """A card as the board read carries it: `nodes` oldest first, put in the
    API's newest-first order, and a `pageInfo` saying whether older comments
    lie beyond the window."""
    window = sorted(nodes, key=lambda n: n["createdAt"])
    card = {
        "id": f"uuid-{ident}",
        "identifier": ident,
        "title": title,
        "description": "work",
        "updatedAt": _iso(_at(0.1)),
        "state": {"name": state},
        "labels": {"nodes": [{"name": n} for n in labels]},
        "comments": {"nodes": list(reversed(window)),
                     "pageInfo": {"hasNextPage": partial, "endCursor": None}},
    }
    if parent is not None:
        card["parent"] = parent
    return card


def _hand_card(ident="DRE-7201", *, entered: float | None = 80.0, nodes=(),
               labels=("repo:portico", OPERATOR_STEP, NO_CODE),
               title="an operator step") -> dict:
    window = [_node(PROMOTED, entered)] if entered is not None else []
    return _card(ident, title=title, state=HAND_WORK, nodes=[*window, *nodes],
                 labels=labels)


# --------------------------------------------------------------------------- #
# the door, and the two sites                                                 #
# --------------------------------------------------------------------------- #


@contextlib.contextmanager
def _door():
    """The notice door, stubbed: every raise and resolve is recorded."""
    calls = SimpleNamespace(raised=[], resolved=[])

    def raise_notice(key, **kw):
        calls.raised.append(SimpleNamespace(key=key, **kw))
        return "raised"

    def resolve_notice(key, **kw):
        calls.resolved.append(key)
        return "resolved"

    with patch.object(pipeline_notice, "raise_notice", side_effect=raise_notice), \
         patch.object(pipeline_notice, "resolve_notice", side_effect=resolve_notice):
        yield calls


def _reading(state: str):
    return SimpleNamespace(state=state, lines=["the reading's own line"], dispatches=1,
                           run_id=None, receipts=[], record_pr=None)


@contextlib.contextmanager
def _seams(*, thread=None, comment_effect=None, moves=None, reading=None):
    """Every Linear seam either site may touch, stubbed. The whole-thread read
    is a failure unless the test hands one in."""
    seams = SimpleNamespace(
        thread=thread or mock.Mock(side_effect=AssertionError("no whole-thread read")),
        gql=mock.Mock(side_effect=AssertionError("no direct Linear read")),
        lane_moves=mock.Mock(return_value=moves),
        reading=mock.Mock(return_value=reading or _reading("none")),
    )
    with patch.object(linear_ops, "cmd_comment", side_effect=comment_effect) as comment, \
         patch.object(linear_ops, "add_label"), \
         patch.object(linear_ops, "remove_label"), \
         patch.object(linear_ops, "cmd_advance"), \
         patch.object(linear_ops, "cmd_state"), \
         patch.object(linear_ops, "comment_bodies", return_value=[]), \
         patch.object(linear_ops, "_thread_and_viewer", seams.thread), \
         patch.object(linear_ops, "gql", seams.gql), \
         patch.object(routing_verdict, "lane_moves", seams.lane_moves), \
         patch.object(proof_run_state, "reading", seams.reading):
        seams.comment = comment
        yield seams


def _deadline_lines(capsys) -> list[str]:
    return [ln for ln in capsys.readouterr().out.splitlines()
            if ln.startswith(hold_deadline.TAG)]


def _held_pass(cards, capsys, **kw):
    """`alarm_held_proofs` over `cards`, the way `main` hands it `mine`."""
    board_read = kw.pop("board_read", True)
    with _door() as door, _seams(**kw) as seams:
        if board_read:
            reconcile.alarm_held_proofs(list(cards))
        else:
            reconcile.alarm_held_proofs(list(cards), board_read=False)
    return SimpleNamespace(door=door, seams=seams, lines=_deadline_lines(capsys))


def _stranded_pass(cards, capsys, **kw):
    """`flag_stranded` over a board of `cards`."""
    with _door() as door, _seams(**kw) as seams, \
         patch.object(reconcile, "active_cards", return_value=list(cards)):
        reconcile.flag_stranded()
    return SimpleNamespace(door=door, seams=seams, lines=_deadline_lines(capsys))


def _raised(got, tag: str):
    """The one raise of `tag`'s key, or None."""
    key = hold_deadline.notice_key("portico", tag)
    found = [c for c in got.door.raised if c.key == key]
    assert len(found) <= 1, f"{key} raised {len(found)} times"
    return found[0] if found else None


def _listed(got, tag: str) -> list[str]:
    """The cards the raise of `tag` lists, in its body's order."""
    call = _raised(got, tag)
    if call is None:
        return []
    return [ln.split(" — ", 1)[0] for ln in call.body.splitlines()
            if ln != hold_deadline.UNREAD_LINE]


def _line_of(got, tag: str, ident: str) -> str:
    (line,) = [ln for ln in _raised(got, tag).body.splitlines() if ln.startswith(ident)]
    return line


def _since(when: datetime) -> str:
    return f"since {hold_deadline._pt(when)}"


# --------------------------------------------------------------------------- #
# proof-waiting — which holds qualify                                         #
# --------------------------------------------------------------------------- #


class TestProofWaitingLists:
    def test_an_operator_hold_older_than_72_hours_is_listed_and_raised_once(self, capsys):
        hold = _hold(CAUSE_NEEDS, 80.0)
        got = _held_pass([_card(nodes=[hold])], capsys)
        call = _raised(got, PROOF_TAG)
        assert call is not None
        assert call.severity == "WARN"
        assert call.title == "1 proof-waiting hold(s) past their due date in portico"
        assert _listed(got, PROOF_TAG) == ["DRE-7101"]
        assert _since(_parse(hold["createdAt"])) in call.body
        assert proof_record._cut(hold["body"].split("\n", 1)[0], 120) in call.body
        assert got.door.resolved == []

    def test_an_event_wait_older_than_72_hours_is_listed(self, capsys):
        got = _held_pass([_card(nodes=[_hold(EVENT_NEEDS, 80.0)])], capsys)
        assert _listed(got, PROOF_TAG) == ["DRE-7101"]

    def test_a_hold_on_the_ceos_press_is_not(self, capsys):
        got = _held_pass([_card(nodes=[_hold(CEO_NEEDS, 80.0)])], capsys)
        assert _raised(got, PROOF_TAG) is None

    def test_a_time_only_hold_whose_time_is_ahead_is_not(self, capsys):
        got = _held_pass([_card(nodes=[_hold(_until(-5.0), 80.0)])], capsys)
        assert _raised(got, PROOF_TAG) is None

    def test_a_card_in_green_light_is_not(self, capsys):
        card = _card(state="Green Light", nodes=[_hold(CAUSE_NEEDS, 80.0)])
        got = _held_pass([card], capsys)
        assert _raised(got, PROOF_TAG) is None

    def test_a_card_that_is_not_a_proof_is_not(self, capsys):
        card = _card(title="a build card", nodes=[_hold(CAUSE_NEEDS, 80.0)])
        got = _held_pass([card], capsys)
        assert _raised(got, PROOF_TAG) is None

    def test_another_repos_card_is_not(self, capsys):
        card = _card(labels=("repo:atlas", NO_CODE), nodes=[_hold(CAUSE_NEEDS, 80.0)])
        got = _held_pass([card], capsys)
        assert _raised(got, PROOF_TAG) is None

    @pytest.mark.parametrize("ident,labels", [
        ("DRE-4541", ("repo:portico", NO_CODE)),
        ("DRE-7101", ("repo:portico", NO_CODE, "hand-built")),
    ])
    def test_a_standing_or_hand_built_card_is_not(self, ident, labels, capsys):
        card = _card(ident, labels=labels, nodes=[_hold(CAUSE_NEEDS, 80.0)])
        got = _held_pass([card], capsys)
        assert _raised(got, PROOF_TAG) is None

    def test_a_hold_an_observation_discharged_is_not(self, capsys):
        card = _card(nodes=[_hold(EVENT_NEEDS, 80.0), _node(OBSERVED, 60.0)])
        got = _held_pass([card], capsys)
        assert _raised(got, PROOF_TAG) is None


class TestProofWaitingClock:
    def test_71_hours_is_not_late(self, capsys):
        got = _held_pass([_card(nodes=[_hold(CAUSE_NEEDS, 71.0)])], capsys)
        assert _raised(got, PROOF_TAG) is None

    def test_a_newer_hold_does_not_move_the_clock(self, capsys):
        oldest = _hold(EVENT_NEEDS, 80.0)
        card = _card(nodes=[oldest, _hold(CAUSE_NEEDS, 5.0)])
        got = _held_pass([card], capsys)
        assert _listed(got, PROOF_TAG) == ["DRE-7101"]
        assert _since(_parse(oldest["createdAt"])) in _line_of(got, PROOF_TAG, "DRE-7101")

    def test_a_newer_hold_on_a_71_hour_card_leaves_it_unlisted(self, capsys):
        card = _card(nodes=[_hold(EVENT_NEEDS, 71.0), _hold(CAUSE_NEEDS, 1.0)])
        got = _held_pass([card], capsys)
        assert _raised(got, PROOF_TAG) is None

    def test_a_time_only_hold_whose_time_passed_2_hours_ago_is_not(self, capsys):
        card = _card(nodes=[_hold(_until(2.0), 240.0)])
        got = _held_pass([card], capsys)
        assert _raised(got, PROOF_TAG) is None

    def test_a_time_only_hold_whose_time_passed_73_hours_ago_is_listed_from_it(
            self, capsys):
        needs = _until(73.0)
        card = _card(nodes=[_hold(needs, 240.0)])
        got = _held_pass([card], capsys)
        assert _listed(got, PROOF_TAG) == ["DRE-7101"]
        when = proof_record.time_only(linear_ops.proof_waiting_line("x", needs))
        assert _since(when) in _line_of(got, PROOF_TAG, "DRE-7101")

    @pytest.mark.parametrize("after,listed", [(-1.0, False), (71.0, False), (73.0, True)])
    def test_dre_7951s_twelve_hour_hold(self, after, listed, capsys):
        """`— needs not-before=<T>`, posted 12 hours before `T`: ahead, and 71
        hours after `T`, it is not listed; 73 hours after, still open, it is,
        with `T` as its since."""
        needs = _until(after)
        card = _card(nodes=[_hold(needs, after + 12.0)])
        got = _held_pass([card], capsys)
        assert _listed(got, PROOF_TAG) == (["DRE-7101"] if listed else [])
        if listed:
            when = proof_record.time_only(linear_ops.proof_waiting_line("x", needs))
            assert _since(when) in _line_of(got, PROOF_TAG, "DRE-7101")

    @pytest.mark.parametrize("hours,listed", [(73.0, True), (71.0, False)])
    def test_dre_7953s_shipped_hold(self, hours, listed, capsys):
        got = _held_pass([_card(nodes=[_hold(SHIPPED_NEEDS, hours)])], capsys)
        assert _listed(got, PROOF_TAG) == (["DRE-7101"] if listed else [])
        if listed:
            assert "DRE-1234 shipped" in _line_of(got, PROOF_TAG, "DRE-7101")

    @pytest.mark.parametrize("lane", ["Queued", HAND_WORK, REVIEW])
    def test_an_epic_that_is_done_changes_nothing(self, lane, capsys):
        nodes = [_hold(CAUSE_NEEDS, 80.0)]
        done = _card(state=lane, nodes=nodes,
                     parent={"identifier": "DRE-7000", "state": {"name": "Done"}})
        open_ = _card("DRE-7102", state=lane, nodes=nodes,
                      parent={"identifier": "DRE-7000", "state": {"name": "In Progress"}})
        got = _held_pass([done, open_], capsys)
        assert _listed(got, PROOF_TAG) == ["DRE-7101", "DRE-7102"]
        assert (_line_of(got, PROOF_TAG, "DRE-7101").split(" — ", 1)[1]
                == _line_of(got, PROOF_TAG, "DRE-7102").split(" — ", 1)[1])


class TestThePartialWindow:
    def test_the_whole_thread_is_read_once_and_judged(self, capsys):
        old = _hold(CAUSE_NEEDS, 80.0)
        card = _card(partial=True, nodes=[_node("a newer comment", 1.0)])
        thread = mock.Mock(return_value=([old, _node("a newer comment", 1.0)], "viewer-id"))
        got = _held_pass([card], capsys, thread=thread)
        thread.assert_called_once_with("DRE-7101", "body", "user", "createdAt", whole=True)
        assert _listed(got, PROOF_TAG) == ["DRE-7101"]
        assert _since(_parse(old["createdAt"])) in _line_of(got, PROOF_TAG, "DRE-7101")

    def test_a_window_that_is_the_whole_thread_costs_no_read(self, capsys):
        card = _card(nodes=[_hold(CAUSE_NEEDS, 80.0)])
        got = _held_pass([card], capsys)
        got.seams.thread.assert_not_called()
        assert _listed(got, PROOF_TAG) == ["DRE-7101"]

    def test_an_unreadable_thread_leaves_the_pass_incomplete(self, capsys):
        card = _card(partial=True, nodes=[_node("a newer comment", 1.0)])
        thread = mock.Mock(side_effect=linear_ops.LinearError("rate-limited"))
        with patch.object(hold_deadline, "settle", wraps=hold_deadline.settle) as settle:
            got = _held_pass([card], capsys, thread=thread)
        settle.assert_called_once()
        assert settle.call_args.args[:3] == (PROOF_TAG, "portico", [])
        assert settle.call_args.kwargs["complete"] is False
        assert got.door.raised == [] and got.door.resolved == []

    def test_another_late_card_is_still_raised_with_the_unread_line(self, capsys):
        unread = _card(partial=True, nodes=[_node("a newer comment", 1.0)])
        late = _card("DRE-7102", nodes=[_hold(CAUSE_NEEDS, 80.0)])
        thread = mock.Mock(side_effect=linear_ops.LinearError("rate-limited"))
        got = _held_pass([unread, late], capsys, thread=thread)
        assert _listed(got, PROOF_TAG) == ["DRE-7102"]
        assert _raised(got, PROOF_TAG).body.endswith(hold_deadline.UNREAD_LINE)

    def test_a_card_that_raises_is_unjudged_and_the_loop_goes_on(self, capsys):
        late = _card("DRE-7102", nodes=[_hold(CAUSE_NEEDS, 80.0)])
        real = reconcile._proof_wait_clock

        def clock(card, **kw):
            if card["identifier"] == "DRE-7101":
                raise RuntimeError("a malformed thread")
            return real(card, **kw)

        with patch.object(reconcile, "_proof_wait_clock", side_effect=clock):
            got = _held_pass([_card(nodes=[_hold(CAUSE_NEEDS, 10.0)]), late], capsys)
        assert _listed(got, PROOF_TAG) == ["DRE-7102"]
        assert _raised(got, PROOF_TAG).body.endswith(hold_deadline.UNREAD_LINE)
        assert any("DRE-7101" in f for f in reconcile._write_failures)


class TestProofWaitingSettles:
    def test_nothing_late_on_a_complete_read_resolves_once(self, capsys):
        got = _held_pass([_card(nodes=[_hold(CAUSE_NEEDS, 10.0)])], capsys)
        assert got.door.raised == []
        assert got.door.resolved == [hold_deadline.notice_key("portico", PROOF_TAG)]
        assert len(got.lines) == 1
        assert got.lines[0].startswith("hold-deadline: proof-waiting repo=portico late=0")

    def test_a_board_that_was_not_read_settles_nothing(self, capsys):
        got = _held_pass([], capsys, board_read=False)
        assert got.door.raised == [] and got.door.resolved == []
        assert got.lines == []

    def test_the_24_hour_alarm_is_still_posted_as_today(self, capsys):
        got = _held_pass([_card(nodes=[_hold(CAUSE_NEEDS, 80.0)])], capsys)
        got.seams.comment.assert_called_once()
        assert got.seams.comment.call_args.args[1].startswith(
            "🚨 hand-work-overdue: DRE-7101 has waited In Review for the operator 80 hours")


# --------------------------------------------------------------------------- #
# hand-work-overdue                                                           #
# --------------------------------------------------------------------------- #


class TestHandWorkOverdue:
    def test_an_alarmed_card_past_72_hours_is_listed(self, capsys):
        card = _hand_card(entered=80.0, nodes=[_alarm("DRE-7201", 50.0)])
        got = _stranded_pass([card], capsys)
        call = _raised(got, HAND_TAG)
        assert call is not None
        assert call.title == "1 hand-work-overdue hold(s) past their due date in portico"
        assert _listed(got, HAND_TAG) == ["DRE-7201"]
        entered = _parse(card["comments"]["nodes"][-1]["createdAt"])
        assert _since(entered) in call.body
        got.seams.comment.assert_not_called()

    def test_the_alarm_it_posts_on_this_pass_counts(self, capsys):
        got = _stranded_pass([_hand_card(entered=80.0)], capsys)
        got.seams.comment.assert_called_once()
        assert _listed(got, HAND_TAG) == ["DRE-7201"]

    def test_71_hours_is_not_late(self, capsys):
        card = _hand_card(entered=71.0, nodes=[_alarm("DRE-7201", 40.0)])
        got = _stranded_pass([card], capsys)
        assert _raised(got, HAND_TAG) is None
        assert got.door.resolved == [hold_deadline.notice_key("portico", HAND_TAG)]

    def test_an_alarm_from_an_earlier_stay_does_not_count(self, capsys):
        """The old alarm is not this entry's: with this entry's alarm refused,
        nothing is listed, and the pass is short."""
        card = _hand_card(entered=80.0, nodes=[_node(PROMOTED, 200.0),
                                               _alarm("DRE-7201", 150.0)])
        got = _stranded_pass([card], capsys,
                             comment_effect=linear_ops.LinearError("refused"))
        got.seams.comment.assert_called_once()
        assert _raised(got, HAND_TAG) is None
        assert got.door.resolved == []

    @pytest.mark.parametrize("ident,labels", [
        ("DRE-4541", ("repo:portico", OPERATOR_STEP, NO_CODE)),
        ("DRE-7201", ("repo:portico", "hand-built")),
    ])
    def test_a_standing_or_hand_built_card_is_not(self, ident, labels, capsys):
        card = _hand_card(ident, labels=labels, entered=200.0,
                          nodes=[_alarm(ident, 150.0)])
        got = _stranded_pass([card], capsys)
        assert _raised(got, HAND_TAG) is None
        assert got.door.resolved == [hold_deadline.notice_key("portico", HAND_TAG)]

    def test_one_line_and_one_call(self, capsys):
        cards = [_hand_card(entered=80.0, nodes=[_alarm("DRE-7201", 50.0)]),
                 _hand_card("DRE-7202", entered=90.0, nodes=[_alarm("DRE-7202", 60.0)])]
        with patch.object(hold_deadline, "settle", wraps=hold_deadline.settle) as settle:
            got = _stranded_pass(cards, capsys)
        settle.assert_called_once()
        assert _listed(got, HAND_TAG) == ["DRE-7202", "DRE-7201"]
        assert got.lines == [
            "hold-deadline: hand-work-overdue repo=portico late=2 "
            "cards=DRE-7202,DRE-7201 complete=yes outcome=raised"]


class TestHandWorkUnjudged:
    LATE = staticmethod(lambda: _hand_card("DRE-7209", entered=90.0,
                                           nodes=[_alarm("DRE-7209", 60.0)]))

    def _unreadable_entry(self):
        return _hand_card(entered=None), {"moves": None}

    def _unposted_alarm(self):
        return _hand_card(entered=80.0), {
            "comment_effect": linear_ops.LinearError("refused")}

    def _unread_proof_run(self):
        card = _hand_card(entered=80.0, title="PROOF: the board shows the age",
                          labels=("repo:portico", NO_CODE))
        return card, {"reading": _reading("unknown"),
                      "thread": mock.Mock(return_value=([], "viewer-id"))}

    @pytest.mark.parametrize("make", ["_unreadable_entry", "_unposted_alarm",
                                      "_unread_proof_run"])
    def test_alone_it_neither_raises_nor_resolves(self, make, capsys):
        card, kw = getattr(self, make)()
        with patch.object(hold_deadline, "settle", wraps=hold_deadline.settle) as settle:
            got = _stranded_pass([card], capsys, **kw)
        settle.assert_called_once()
        assert settle.call_args.args[0] == HAND_TAG
        assert settle.call_args.kwargs["complete"] is False
        assert got.door.raised == [] and got.door.resolved == []

    @pytest.mark.parametrize("make", ["_unreadable_entry", "_unposted_alarm",
                                      "_unread_proof_run"])
    def test_beside_a_late_card_it_raises_with_that_card(self, make, capsys):
        card, kw = getattr(self, make)()
        got = _stranded_pass([card, self.LATE()], capsys, **kw)
        assert _listed(got, HAND_TAG) == ["DRE-7209"]
        assert _raised(got, HAND_TAG).body.endswith(hold_deadline.UNREAD_LINE)


# --------------------------------------------------------------------------- #
# _hand_work_age's `judged`                                                   #
# --------------------------------------------------------------------------- #


class TestJudged:
    def _age(self, card, **kw):
        judged: list = []
        with _seams(**kw):
            tail = reconcile._hand_work_age(card, "labeled 'operator-step'", judged=judged)
        return tail, judged

    def test_an_alarmed_entry(self):
        card = _hand_card(entered=80.0, nodes=[_alarm("DRE-7201", 50.0)])
        _, judged = self._age(card)
        entered = card["comments"]["nodes"][-1]["createdAt"]
        alarm = card["comments"]["nodes"][0]["createdAt"]
        assert judged == [("DRE-7201", entered, alarm)]

    def test_a_young_entry_is_judged_unalarmed(self):
        card = _hand_card(entered=10.0)
        _, judged = self._age(card)
        assert judged == [("DRE-7201", card["comments"]["nodes"][-1]["createdAt"], None)]

    def test_an_unreadable_entry(self):
        tail, judged = self._age(_hand_card(entered=None), moves=None)
        assert tail == "; entry time could not be read — not judged"
        assert judged == [("DRE-7201", None, None)]

    def test_a_standing_card_is_not_judged(self):
        _, judged = self._age(_hand_card("DRE-4541", entered=80.0))
        assert judged == []

    def test_without_it_the_tail_is_todays(self):
        card = _hand_card(entered=80.0, nodes=[_alarm("DRE-7201", 50.0)])
        with _seams():
            tail = reconcile._hand_work_age(card, "labeled 'operator-step'")
        alarmed = reconcile._pt(card["comments"]["nodes"][0]["createdAt"])
        assert tail == f"; in Hand-work 80h, past 24 — alarmed {alarmed}"


# --------------------------------------------------------------------------- #
# the two sites on one card                                                   #
# --------------------------------------------------------------------------- #


class TestNeverListedTwice:
    def _proof_in_hand_work(self, hold_hours: float):
        return _hand_card("DRE-7301", entered=90.0, title="PROOF: the board shows the age",
                          labels=("repo:portico", NO_CODE),
                          nodes=[_alarm("DRE-7301", 60.0), _hold(EVENT_NEEDS, hold_hours)])

    def _both(self, card, capsys, order):
        with _door() as door, _seams(), \
             patch.object(reconcile, "active_cards", return_value=[card]):
            for site in order:
                if site == "flag_stranded":
                    reconcile.flag_stranded()
                else:
                    reconcile.alarm_held_proofs([card])
        return SimpleNamespace(door=door, lines=_deadline_lines(capsys))

    @pytest.mark.parametrize("order", [("flag_stranded", "alarm_held_proofs"),
                                       ("alarm_held_proofs", "flag_stranded")])
    def test_past_both_clocks_it_is_a_proof_waiting_hold_only(self, order, capsys):
        got = self._both(self._proof_in_hand_work(80.0), capsys, order)
        assert _listed(got, PROOF_TAG) == ["DRE-7301"]
        assert _listed(got, HAND_TAG) == []
        assert len(got.lines) == 2

    @pytest.mark.parametrize("order", [("flag_stranded", "alarm_held_proofs"),
                                       ("alarm_held_proofs", "flag_stranded")])
    def test_a_hold_not_yet_late_is_listed_under_neither(self, order, capsys):
        got = self._both(self._proof_in_hand_work(10.0), capsys, order)
        assert got.door.raised == []

    def test_a_proof_card_whose_holds_cannot_be_read_leaves_the_pass_incomplete(
            self, capsys):
        """A hold the reading raises on is one unjudged card, as it is in
        `alarm_held_proofs` — never the end of `flag_stranded`."""
        card = self._proof_in_hand_work(80.0)
        with patch.object(reconcile, "_proof_wait_clock",
                          side_effect=ValueError("a hold with no time")), \
             patch.object(hold_deadline, "settle", wraps=hold_deadline.settle) as settle:
            got = self._both(card, capsys, ("flag_stranded",))
        settle.assert_called_once()
        assert settle.call_args.kwargs["complete"] is False
        assert _listed(got, HAND_TAG) == []
        assert any("DRE-7301" in f for f in reconcile._write_failures)

    def test_with_no_hold_the_entry_clock_lists_it(self, capsys):
        card = _hand_card("DRE-7301", entered=90.0, title="PROOF: the board shows the age",
                          labels=("repo:portico", NO_CODE), nodes=[_alarm("DRE-7301", 60.0)])
        got = self._both(card, capsys, ("flag_stranded",))
        assert _listed(got, HAND_TAG) == ["DRE-7301"]


# --------------------------------------------------------------------------- #
# the sweep                                                                   #
# --------------------------------------------------------------------------- #


class TestTheSweep:
    """A whole pass over a board holding nothing late: both sites settle once
    each; an idle, held or promote-only pass settles nothing."""

    def _pass(self, monkeypatch, *, idle=None, held=False, cards=None, queued=(),
              queue_held=False, **main_kw):
        import test_sweep_spend_lines as spend

        monkeypatch.setattr(reconcile, "sweep_idle", lambda: idle)
        fake = spend.FakeLinear(cards or [_card(nodes=[_hold(CAUSE_NEEDS, 10.0)])],
                                backlog=queued)
        settle = mock.Mock(wraps=hold_deadline.settle)
        replace = ()
        if held:
            def active_cards(*a, **kw):
                raise reconcile.BoardHeld("the door says Linear is held")
            active_cards.__name__ = "active_cards"
            replace = (active_cards,)
        if queue_held:
            def backlog_children(*a, **kw):
                raise reconcile.BoardHeld("the door says Linear is held")
            backlog_children.__name__ = "backlog_children"
            replace = (backlog_children,)
        with _door(), patch.object(hold_deadline, "settle", settle):
            spend._run_sweep(fake, mocks=("move_hand_built_to_review",), replace=replace,
                             **main_kw)
        return settle

    def test_a_full_pass_settles_each_site_once(self, monkeypatch, capsys):
        settle = self._pass(monkeypatch)
        assert sorted(c.args[0] for c in settle.call_args_list) == [HAND_TAG, PROOF_TAG]
        assert all(c.kwargs["complete"] is True for c in settle.call_args_list)
        lines = _deadline_lines(capsys)
        assert len(lines) == 2

    def test_a_proof_card_in_hand_work_is_read_by_the_proof_waiting_list(
            self, monkeypatch, capsys):
        """`mine` holds In Progress and In Review only; the board read carries
        Hand-work too, and a proof's event wait there is listed under
        `proof-waiting` — never under `hand-work-overdue`, never under neither."""
        card = _hand_card("DRE-7301", entered=90.0, title="PROOF: the board shows the age",
                          labels=("repo:portico", NO_CODE),
                          nodes=[_alarm("DRE-7301", 60.0), _hold(EVENT_NEEDS, 80.0)])
        settle = self._pass(monkeypatch, cards=[card])
        lists = {c.args[0]: [h.ident for h in c.args[2]] for c in settle.call_args_list}
        assert lists == {PROOF_TAG: ["DRE-7301"], HAND_TAG: []}

    def test_a_proof_card_in_queued_is_read_by_the_proof_waiting_list(
            self, monkeypatch, capsys):
        """The board read does not carry Queued; promotion reads it later in
        the same pass, and the late list is judged off that read — a proof's
        event wait there is listed under `proof-waiting`, at no request of
        its own."""
        card = _card("DRE-7401", state="Queued", nodes=[_hold(EVENT_NEEDS, 80.0)])
        settle = self._pass(monkeypatch, queued=[card])
        (proof,) = [c for c in settle.call_args_list if c.args[0] == PROOF_TAG]
        assert [h.ident for h in proof.args[2]] == ["DRE-7401"]
        assert proof.kwargs["complete"] is True

    def test_a_pass_whose_queued_read_was_skipped_is_not_complete(
            self, monkeypatch, capsys):
        """A Queued lane nobody read is not an empty one: the proof-waiting
        list settles incomplete, so a standing notice is never resolved off it."""
        settle = self._pass(monkeypatch, queue_held=True)
        (proof,) = [c for c in settle.call_args_list if c.args[0] == PROOF_TAG]
        assert proof.kwargs["complete"] is False

    def test_the_24_hour_alarm_keeps_todays_lanes(self, monkeypatch, capsys):
        """A CEO's-press hold on a proof card in Hand-work was never in
        `mine`, so it was never alarmed: reading Hand-work for the late list
        posts no new alarm."""
        card = _hand_card("DRE-7301", entered=2.0, title="PROOF: the board shows the age",
                          labels=("repo:portico", NO_CODE), nodes=[_hold(CEO_NEEDS, 30.0)])
        got = _held_pass([card], capsys)
        got.seams.comment.assert_not_called()
        assert got.door.resolved == [hold_deadline.notice_key("portico", PROOF_TAG)]

    def test_an_idle_pass_settles_nothing(self, monkeypatch, capsys):
        settle = self._pass(monkeypatch, idle="nothing in motion")
        settle.assert_not_called()

    def test_a_held_pass_settles_nothing(self, monkeypatch, capsys):
        settle = self._pass(monkeypatch, held=True)
        settle.assert_not_called()

    def test_a_promote_only_pass_settles_nothing(self, monkeypatch, capsys):
        settle = self._pass(monkeypatch, promote_only=True)
        settle.assert_not_called()


# --------------------------------------------------------------------------- #
# the record                                                                  #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("fn", ["alarm_held_proofs", "flag_stranded", "_proof_wait_clock"])
def test_the_docstring_says_what_is_late_on_which_clock(fn):
    doc = getattr(reconcile, fn).__doc__ or ""
    assert "DRE-7965" in doc
    assert "late" in doc and "clock" in doc and "complete" in doc
