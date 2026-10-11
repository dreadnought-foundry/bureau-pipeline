"""RED-first: a person's card in Hand-work gets an age limit and one alarm (DRE-6409).

THE GAP (2026-10-09): `flag_stranded` skips every card `hand_built_reason`
answers for, and says so — `watchdog: <card> is labeled 'operator-step' — no
dispatched run is expected, … not a strand` (DRE-6424). Skipping the RUN check
is right: no run is expected. But nothing replaced the clock. The 10:23 PT
bureau-pipeline sweep printed that line for 18 cards in one pass and raised
nothing, and a card can sit in Hand-work for days.

FIX UNDER TEST — for a card IN Hand-work, judged only by its own repo's sweep:

  * `config/hand-work-age.json` declares the limit (24 hours, the operator's
    ruling of 2026-10-09) and the standing cards that never alarm;
  * the clock is the card's ENTRY to Hand-work — the sweep's own
    `🧹 Auto-promoted Backlog → Hand-work:` receipt in the comment window,
    else the newest move to Hand-work in `routing_verdict.lane_moves`;
  * past the limit, an operator's card (`operator-step`, or a WORKBENCH
    verdict with no mark) and a `PROOF:` card whose proof run is not in
    flight get ONE `hand-work-overdue` comment per entry, for the operator —
    no label, no lane move, nothing in Green Light;
  * the CEO's own `hand-built` card and a standing card never alarm, and
    without a receipt cost no history read (the real-board ceiling);
  * every Hand-work line keeps DRE-6424's text and appends one tail.

Run: cd bureau-pipeline && python3 -m pytest tests/test_hand_work_age.py -v
"""
from __future__ import annotations

import json
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

import dead_run  # noqa: E402
import pipeline_act  # noqa: E402
import proof_dispatch  # noqa: E402
import proof_run_state  # noqa: E402
import reconcile  # noqa: E402
import routing_verdict  # noqa: E402
import validate_card  # noqa: E402

import test_operator_card_promotion as promo  # noqa: E402

CONFIG = ROOT / "config" / "hand-work-age.json"
ACT = "hand-work-overdue"
HAND_WORK = "Hand-work"
OPERATOR_STEP = "operator-step"
HAND_BUILT = "hand-built"
NO_CODE = "no-code"
PROMOTED = "🧹 Auto-promoted Backlog → Hand-work: routed **OPERATOR** — a person builds this; nothing was dispatched."
TODAY = ("— no dispatched run is expected, so a missing run receipt and an "
         "off-rail repo are both normal here, not a strand")


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    """This sweep owns portico, portico is on the rail, nothing is fetched."""
    monkeypatch.setattr(reconcile, "REPO_SLUG", "portico")
    monkeypatch.setattr(reconcile, "REPO", "dreadnought-foundry/portico")
    monkeypatch.setattr(validate_card, "VALID_SLUGS", {"portico", "atlas"})
    monkeypatch.setattr(reconcile, "live_rail_slugs",
                        lambda: frozenset({"portico", "atlas"}), raising=False)
    reconcile._write_failures.clear()
    reconcile._stale_defects.clear()
    yield
    reconcile._write_failures.clear()
    reconcile._stale_defects.clear()


def _at(hours_ago: float) -> datetime:
    return datetime.now(UTC) - timedelta(hours=hours_ago)


def _iso(when: datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _node(body: str, hours_ago: float) -> dict:
    return {"body": body, "createdAt": _iso(_at(hours_ago)), "user": {"id": "bot"}}


def _alarm(ident: str, hours_ago: float) -> dict:
    return _node(pipeline_act.receipt(ACT, (
        f"🚨 hand-work-overdue: {ident} has waited in Hand-work 25 hours, past "
        "its 24 — the operator's to look at.\nKind: earlier")), hours_ago)


def _card(identifier="DRE-7001", *, labels=("repo:portico", OPERATOR_STEP, NO_CODE),
          title="an operator step", state=HAND_WORK, nodes=None,
          entered: float | None = 25.5) -> dict:
    """A card whose window holds the promotion receipt `entered` hours ago
    (none when `entered` is None), plus `nodes` — oldest first, as a person
    reads them; the API's newest-first order is applied here."""
    window = []
    if entered is not None:
        window.append(_node(PROMOTED, entered))
    window.extend(nodes or [])
    window.sort(key=lambda n: n["createdAt"])
    return {
        "id": f"uuid-{identifier}",
        "identifier": identifier,
        "title": title,
        "description": "work",
        "updatedAt": _iso(_at(0.1)),
        "state": {"name": state},
        "labels": {"nodes": [{"name": n} for n in labels]},
        "comments": {"nodes": list(reversed(window))},
    }


def _proof_card(**kw) -> dict:
    kw.setdefault("labels", ("repo:portico", NO_CODE))
    return _card(title="PROOF: the board shows the age", **kw)


def _workbench_card(**kw) -> dict:
    nodes = kw.pop("nodes", [])
    return _card(labels=("repo:portico",), nodes=[_node(promo.WORKBENCH, 40.0), *nodes],
                 **kw)


def _reading(state: str, lines=("the reading's own line",)):
    return SimpleNamespace(state=state, lines=list(lines), dispatches=1,
                           run_id=None, receipts=[], record_pr=None)


def _sweep(cards, capsys, *, moves=None, reading=None, comment_effect=None):
    """Run `flag_stranded` over `cards`. Returns a namespace of every seam the
    hand-work branch may touch, and the watchdog lines it printed."""
    lane_moves = mock.Mock(return_value=moves)
    run_state = mock.Mock(return_value=reading or _reading("none"))
    thread = mock.Mock(return_value=([], "viewer-id"))
    gql = mock.Mock(side_effect=AssertionError("no direct Linear read here"))
    with patch.object(reconcile, "active_cards", return_value=list(cards)), \
         patch.object(reconcile.linear_ops, "comment_bodies", return_value=[]), \
         patch.object(reconcile.linear_ops, "cmd_comment",
                      side_effect=comment_effect) as comment, \
         patch.object(reconcile.linear_ops, "add_label") as add_label, \
         patch.object(reconcile.linear_ops, "cmd_advance") as advance, \
         patch.object(reconcile.linear_ops, "_thread_and_viewer", thread), \
         patch.object(reconcile.linear_ops, "gql", gql), \
         patch.object(routing_verdict, "lane_moves", lane_moves), \
         patch.object(proof_run_state, "reading", run_state):
        flagged = reconcile.flag_stranded()
    out = capsys.readouterr().out.splitlines()
    return SimpleNamespace(
        flagged=flagged, comment=comment, add_label=add_label, advance=advance,
        lane_moves=lane_moves, reading=run_state, thread=thread, gql=gql,
        lines={c["identifier"]: [ln for ln in out
                                 if ln.startswith(f"watchdog: {c['identifier']} ")]
               for c in cards},
    )


def _line(got, ident="DRE-7001") -> str:
    (line,) = got.lines[ident]
    return line


def _nothing_written(got):
    got.comment.assert_not_called()
    got.add_label.assert_not_called()
    got.advance.assert_not_called()
    assert got.flagged == set()


# --------------------------------------------------------------------------- #
# the number, declared as data                                                #
# --------------------------------------------------------------------------- #


class TestTheConfig:
    def test_hours_and_the_standing_cards(self):
        doc = json.loads(CONFIG.read_text())
        assert doc["hours"] == 24
        assert {s["card"] for s in doc["standing"]} == {"DRE-4541", "DRE-5774"}
        assert len(doc["standing"]) == 2
        for entry in doc["standing"]:
            assert entry["why"].strip(), f"{entry['card']}: a standing card says why"

    def test_the_sweep_reads_the_number_from_the_file(self, tmp_path, monkeypatch, capsys):
        doc = json.loads(CONFIG.read_text())
        doc["hours"] = 30
        moved = tmp_path / "hand-work-age.json"
        moved.write_text(json.dumps(doc))
        monkeypatch.setattr(reconcile, "HAND_WORK_AGE_CONFIG", str(moved))
        got = _sweep([_card()], capsys)
        _nothing_written(got)
        assert "; in Hand-work 25h of 30, alarms at " in _line(got)

        doc["hours"] = 24
        moved.write_text(json.dumps(doc))
        got = _sweep([_card()], capsys)
        got.comment.assert_called_once()


# --------------------------------------------------------------------------- #
# the operator's card                                                         #
# --------------------------------------------------------------------------- #


class TestAnOperatorCard:
    def test_past_24_hours_it_alarms_once(self, capsys):
        got = _sweep([_card()], capsys)
        got.comment.assert_called_once()
        ident, body = got.comment.call_args.args
        assert ident == "DRE-7001"
        first, second, *_ = body.split("\n")
        assert first == ("🚨 hand-work-overdue: DRE-7001 has waited in Hand-work "
                         "25 hours, past its 24 — the operator's to look at.")
        assert "operator-step" in second
        assert body.endswith(pipeline_act.trailer(ACT))
        got.add_label.assert_not_called()
        got.advance.assert_not_called()
        assert got.flagged == set()
        line = _line(got)
        assert line.startswith("watchdog: DRE-7001 is labeled 'operator-step' " + TODAY)
        assert "; in Hand-work 25h, past 24 — alarmed " in line

    def test_the_second_pass_writes_nothing(self, capsys):
        alarm = _alarm("DRE-7001", 1.5)
        card = _card(nodes=[alarm])
        got = _sweep([card], capsys)
        _nothing_written(got)
        alarmed = reconcile._pt(alarm["createdAt"])
        assert _line(got).endswith(f"; in Hand-work 25h, past 24 — alarmed {alarmed}")

    def test_at_23_hours_it_waits(self, capsys):
        card = _card(entered=23.5)
        got = _sweep([card], capsys)
        _nothing_written(got)
        entered = datetime.fromisoformat(
            card["comments"]["nodes"][-1]["createdAt"].replace("Z", "+00:00"))
        due = dead_run.pacific(entered + timedelta(hours=24))
        line = _line(got)
        assert line.startswith("watchdog: DRE-7001 is labeled 'operator-step' " + TODAY)
        assert line.endswith(f"; in Hand-work 23h of 24, alarms at {due}")

    def test_a_card_that_came_back_is_a_new_entry(self, capsys):
        """An alarm older than the newest entry belongs to the last stay."""
        card = _card(entered=25.5, nodes=[_node(PROMOTED, 60.0), _alarm("DRE-7001", 30.0)])
        got = _sweep([card], capsys)
        got.comment.assert_called_once()

    def test_a_workbench_card_with_no_mark_alarms_the_same_way(self, capsys):
        got = _sweep([_workbench_card()], capsys)
        got.comment.assert_called_once()
        body = got.comment.call_args.args[1]
        assert body.startswith("🚨 hand-work-overdue: DRE-7001 has waited in "
                               "Hand-work 25 hours, past its 24 — the operator's "
                               "to look at.\n")
        assert "WORKBENCH" in body.split("\n")[1]
        got.add_label.assert_not_called()
        assert _line(got).startswith("watchdog: DRE-7001 is routed WORKBENCH to Hand-work ")


# --------------------------------------------------------------------------- #
# the PROOF: card                                                             #
# --------------------------------------------------------------------------- #


class TestAProofCard:
    @pytest.mark.parametrize("state", proof_dispatch.FREE)
    def test_a_free_run_alarms_and_quotes_the_reading(self, state, capsys):
        got = _sweep([_proof_card()], capsys,
                     reading=_reading(state, (f"{state} — read off the thread",)))
        got.comment.assert_called_once()
        body = got.comment.call_args.args[1]
        assert body.startswith("🚨 hand-work-overdue: DRE-7001 has waited in Hand-work 25 hours")
        assert f"{state} — read off the thread" in body.split("\n")[1]
        got.add_label.assert_not_called()

    def test_the_reading_is_the_dispatchers_own(self, capsys):
        got = _sweep([_proof_card()], capsys)
        got.thread.assert_called_once_with("DRE-7001", "body", "user", "createdAt",
                                           whole=True)
        args, kwargs = got.reading.call_args
        assert args[:4] == ("dreadnought-foundry/portico", "DRE-7001", [], "viewer-id")
        assert kwargs["read"] is proof_dispatch.github_read

    def test_a_run_in_flight_does_not_alarm(self, capsys):
        got = _sweep([_proof_card()], capsys, reading=_reading("running"))
        _nothing_written(got)
        assert _line(got).endswith(
            "; in Hand-work 25h, past 24 — no alarm: its proof run is in flight")

    def test_an_unknown_reading_does_not_alarm(self, capsys):
        got = _sweep([_proof_card()], capsys, reading=_reading("unknown"))
        _nothing_written(got)
        assert _line(got).endswith(
            "; in Hand-work 25h, past 24 — no alarm: its proof run could not be read")

    def test_a_young_proof_card_costs_no_reading(self, capsys):
        got = _sweep([_proof_card(entered=10.0)], capsys)
        _nothing_written(got)
        got.reading.assert_not_called()
        got.thread.assert_not_called()
        assert "; in Hand-work 10h of 24, alarms at " in _line(got)

    def test_an_alarmed_proof_card_costs_no_reading(self, capsys):
        got = _sweep([_proof_card(nodes=[_alarm("DRE-7001", 1.0)])], capsys)
        _nothing_written(got)
        got.reading.assert_not_called()
        got.thread.assert_not_called()


# --------------------------------------------------------------------------- #
# the cards that never alarm                                                  #
# --------------------------------------------------------------------------- #


class TestNeverAlarms:
    def test_the_ceos_own_hand_built_card(self, capsys):
        card = _card(labels=("repo:portico", HAND_BUILT), entered=100.0)
        got = _sweep([card], capsys)
        _nothing_written(got)
        got.reading.assert_not_called()
        line = _line(got)
        assert line.startswith("watchdog: DRE-7001 is labeled 'hand-built' " + TODAY)
        assert line.endswith(
            "; in Hand-work 100h — never alarms (the CEO's own hand-built card)")

    @pytest.mark.parametrize("ident", ["DRE-4541", "DRE-5774"])
    def test_a_standing_card(self, ident, capsys):
        card = _card(ident, entered=100.0)
        got = _sweep([card], capsys)
        _nothing_written(got)
        assert _line(got, ident).endswith("; in Hand-work 100h — never alarms (a standing card)")


# --------------------------------------------------------------------------- #
# the clock                                                                   #
# --------------------------------------------------------------------------- #


class TestTheEntryTime:
    def test_the_receipt_costs_no_history_read(self, capsys):
        got = _sweep([_card()], capsys)
        got.lane_moves.assert_not_called()

    def test_no_receipt_reads_the_newest_move_to_hand_work(self, capsys):
        moves = [
            {"at": _iso(_at(100.0)), "from": "Backlog", "to": HAND_WORK},
            {"at": _iso(_at(50.0)), "from": HAND_WORK, "to": "Todo"},
            {"at": _iso(_at(25.5)), "from": "Todo", "to": HAND_WORK},
        ]
        got = _sweep([_card(entered=None)], capsys, moves=moves)
        got.lane_moves.assert_called_once_with("DRE-7001")
        got.comment.assert_called_once()
        assert "25 hours" in got.comment.call_args.args[1].split("\n")[0]

    def test_the_newest_move_wins_in_any_order(self, capsys):
        moves = [
            {"at": _iso(_at(10.0)), "from": "Todo", "to": HAND_WORK},
            {"at": _iso(_at(100.0)), "from": "Backlog", "to": HAND_WORK},
        ]
        got = _sweep([_card(entered=None)], capsys, moves=moves)
        _nothing_written(got)
        assert "; in Hand-work 10h of 24, alarms at " in _line(got)

    @pytest.mark.parametrize("ident,labels,why", [
        ("DRE-7001", ("repo:portico", HAND_BUILT), "the CEO's own hand-built card"),
        ("DRE-4541", ("repo:portico", OPERATOR_STEP), "a standing card"),
    ])
    def test_a_card_that_never_alarms_costs_no_history_read(self, ident, labels,
                                                            why, capsys):
        """The real-board ceiling (DRE-3641): no receipt, no request."""
        moves = [{"at": _iso(_at(100.0)), "from": "Backlog", "to": HAND_WORK}]
        got = _sweep([_card(ident, labels=labels, entered=None)], capsys, moves=moves)
        _nothing_written(got)
        got.lane_moves.assert_not_called()
        assert _line(got, ident).endswith(f"; in Hand-work — never alarms ({why})")

    def test_an_unreadable_history_is_not_judged(self, capsys):
        got = _sweep([_card(entered=None)], capsys, moves=None)
        _nothing_written(got)
        assert _line(got).endswith("; entry time could not be read — not judged")


# --------------------------------------------------------------------------- #
# whose sweep, and which lane                                                 #
# --------------------------------------------------------------------------- #


class TestScope:
    def test_another_repos_sweep_reads_and_writes_nothing(self, monkeypatch, capsys):
        monkeypatch.setattr(reconcile, "REPO_SLUG", "atlas")
        for card in (_card(), _proof_card(), _card(entered=None)):
            got = _sweep([card], capsys, moves=[])
            _nothing_written(got)
            got.lane_moves.assert_not_called()
            got.reading.assert_not_called()
            got.thread.assert_not_called()
            got.gql.assert_not_called()
            assert _line(got).endswith(TODAY)

    def test_a_todo_card_is_unchanged(self, capsys):
        got = _sweep([_card(state="Todo")], capsys)
        _nothing_written(got)
        got.lane_moves.assert_not_called()
        assert _line(got).endswith(TODAY)


class TestTheSweepStaysGreen:
    def test_an_alarm_adds_no_failure_and_no_defect(self, capsys):
        got = _sweep([_card()], capsys)
        got.comment.assert_called_once()
        assert reconcile._write_failures == []
        assert reconcile._stale_defects == []

    def test_a_failed_alarm_write_is_a_write_failure(self, capsys):
        got = _sweep([_card()], capsys,
                     comment_effect=reconcile.linear_ops.LinearError("refused"))
        got.comment.assert_called_once()
        assert len(reconcile._write_failures) == 1
        assert "DRE-7001" in reconcile._write_failures[0]
        assert reconcile._stale_defects == []


# --------------------------------------------------------------------------- #
# the act and the lane contract                                               #
# --------------------------------------------------------------------------- #


class TestTheRegistry:
    def test_the_row(self):
        row = pipeline_act.record(ACT)
        assert row["tag"] == ACT
        assert row["kind"] == "hold"
        assert row["state"] == "unchanged"
        assert row["next_actor"] == "operator"
        assert row["subscriber"] == "reconcile.yml"
        assert row["cadence_s"] is None
        assert row["emits"]["file"] == "scripts/reconcile.py"
        assert "scripts/reconcile.py" in row["cadence_why"]

    def test_it_sits_before_the_final_row(self):
        """The card's placement. Since DRE-4210 the final row is
        `promotion-stalled`, appended after `reviewer-outage-fleet-wide`,
        which tests/test_reviewer_down.py pins after its sibling only."""
        names = list(pipeline_act.acts())
        assert names[-2] == ACT
        assert names.index("reviewer-outage-fleet-wide") < names.index(ACT)


def test_the_lane_contract_says_so():
    doc = json.loads((ROOT / "config" / "lane-contract.json").read_text())
    (lane,) = [ln for ln in doc["lanes"] if ln["name"] == HAND_WORK]
    text = lane["clauses"]["entrance"]["text"]
    assert "stall window" in text and "never reported as stalled" in text
    assert "config/hand-work-age.json" in text
    assert "`hand-work-overdue`" in text
    assert "standing card" in text and "`hand-built`" in text


# --------------------------------------------------------------------------- #
# DRE-6852: a PROOF: card held In Review for the operator                     #
# --------------------------------------------------------------------------- #
#
# DRE-6837, DRE-6841 and DRE-6842 leave a stopped proof card in `In Review`
# for the operator, and `In Review` had no clock: `WATCHDOG_LANES` is Todo,
# Hand-work and In Progress, and the review nudge reads pull requests, not
# holds. `alarm_held_proofs` gives a held proof card the Hand-work bound and
# the Hand-work receipt, off the comment window the board read carries.

import hold  # noqa: E402
import linear_ops  # noqa: E402
import proof_cause_park  # noqa: E402
import proof_record  # noqa: E402

REVIEW = "In Review"
CAUSE_NEEDS = (f"{proof_cause_park.CAUSE_HOLD_OPENING} 1 row(s) the run cannot "
               "observe as worded (cause: out-of-reach)")
PRESS_NEEDS = "the operator's press: set the sandbox project's Builders to Sonnet 5.5"
EVENT_NEEDS = "the weekly report of 2026-10-18"
CEO_NEEDS = f"{proof_record.CEO_PRESS}: approve the release on the console"
OBSERVED = linear_ops.proof_observed_line("the board showed the age at 09:00 PT")
RERUN = ("🔬 proof-run: dispatched a proof run at 09:00 PT — the record was "
         "amended (re-run 1 of 2)")
HEAD = "a" * 40
STAMP = hold.stamp_line("review-cap-spent", HEAD, "reconcile.py")
HELD_OPENING = "🚨 hand-work-overdue: DRE-7001 has waited In Review for the operator"


def _hold(needs: str, hours_ago: float, observed="the board shows the age") -> dict:
    return _node(linear_ops.proof_waiting_line(observed, needs), hours_ago)


def _first(node: dict) -> str:
    return node["body"].split("\n", 1)[0]


def _review_card(identifier="DRE-7001", *, nodes=(), labels=("repo:portico", NO_CODE),
                 title="PROOF: the board shows the age", state=REVIEW) -> dict:
    """A proof card In Review, its window holding `nodes` (oldest first) and
    no promotion receipt — the In Review clock never reads one."""
    return _card(identifier, labels=labels, title=title, state=state,
                 nodes=list(nodes), entered=None)


def _clock(node: dict) -> datetime:
    return datetime.fromisoformat(node["createdAt"].replace("Z", "+00:00"))


def _held_sweep(cards, capsys, *, comment_effect=None):
    """Run `alarm_held_proofs` over `cards`, the way `main` hands it `mine`.
    Every Linear read is a failure here: the pass reads only the window."""
    gql = mock.Mock(side_effect=AssertionError("no Linear read of its own"))
    thread = mock.Mock(side_effect=AssertionError("no thread read of its own"))
    lane_moves = mock.Mock(side_effect=AssertionError("no history read of its own"))
    with patch.object(reconcile.linear_ops, "cmd_comment",
                      side_effect=comment_effect) as comment, \
         patch.object(reconcile.linear_ops, "add_label") as add_label, \
         patch.object(reconcile.linear_ops, "remove_label") as remove_label, \
         patch.object(reconcile.linear_ops, "cmd_advance") as advance, \
         patch.object(reconcile.linear_ops, "cmd_state") as state, \
         patch.object(reconcile.linear_ops, "_thread_and_viewer", thread), \
         patch.object(reconcile.linear_ops, "comment_bodies", thread), \
         patch.object(reconcile.linear_ops, "gql", gql), \
         patch.object(routing_verdict, "lane_moves", lane_moves):
        reconcile.alarm_held_proofs(list(cards))
    out = [ln for ln in capsys.readouterr().out.splitlines()
           if ln.startswith("held-proof:")]
    return SimpleNamespace(
        comment=comment, add_label=add_label, remove_label=remove_label,
        advance=advance, state=state, gql=gql, thread=thread, out=out,
        lines={c["identifier"]: [ln for ln in out
                                 if ln.startswith(f"held-proof: {c['identifier']} ")]
               for c in cards},
    )


def _held_line(got, ident="DRE-7001") -> str:
    (line,) = got.lines[ident]
    return line


def _wrote_nothing(got):
    got.comment.assert_not_called()
    got.add_label.assert_not_called()
    got.remove_label.assert_not_called()
    got.advance.assert_not_called()
    got.state.assert_not_called()


def _kind(line: str) -> str:
    return f"Kind: a PROOF: card held for the operator — {proof_record._cut(line, 120)}."


def _alarmed_once(got, held_by: dict, hours=25):
    got.comment.assert_called_once()
    ident, body = got.comment.call_args.args
    assert ident == "DRE-7001"
    first, second, *_ = body.split("\n")
    assert first == (f"{HELD_OPENING} {hours} hours, past its 24 — the "
                     "operator's to look at.")
    assert second == _kind(_first(held_by))
    assert body.endswith(pipeline_act.trailer(ACT))
    got.add_label.assert_not_called()
    got.remove_label.assert_not_called()
    got.advance.assert_not_called()
    got.state.assert_not_called()


class TestAHeldProofAlarms:
    def test_a_cause_hold_past_24_hours_alarms_once(self, capsys):
        cause = _hold(CAUSE_NEEDS, 25.5)
        got = _held_sweep([_review_card(nodes=[cause])], capsys)
        _alarmed_once(got, cause)
        line = _held_line(got)
        assert line.startswith("held-proof: DRE-7001 held In Review for the "
                               "operator 25h, past 24 — alarmed ")
        assert line.endswith(f"(clock: its cause hold of {reconcile._pt(cause['createdAt'])})")

    def test_the_second_pass_writes_nothing(self, capsys):
        cause = _hold(CAUSE_NEEDS, 25.5)
        alarm = _node(pipeline_act.receipt(ACT, f"{HELD_OPENING} 25 hours, past its 24 "
                                                "— the operator's to look at."), 1.0)
        got = _held_sweep([_review_card(nodes=[cause, alarm])], capsys)
        _wrote_nothing(got)
        assert (f"25h, past 24 — alarmed {reconcile._pt(alarm['createdAt'])}"
                in _held_line(got))

    def test_an_alarm_older_than_the_hold_belongs_to_another_wait(self, capsys):
        cause = _hold(CAUSE_NEEDS, 25.5)
        got = _held_sweep([_review_card(nodes=[_alarm("DRE-7001", 40.0), cause])], capsys)
        _alarmed_once(got, cause)

    def test_at_23_hours_it_waits_and_says_when(self, capsys):
        cause = _hold(CAUSE_NEEDS, 23.5)
        got = _held_sweep([_review_card(nodes=[cause])], capsys)
        _wrote_nothing(got)
        due = dead_run.pacific(_clock(cause) + timedelta(hours=24))
        assert _held_line(got) == (
            f"held-proof: DRE-7001 held In Review for the operator 23h of 24, "
            f"alarms at {due} (clock: its cause hold of {reconcile._pt(cause['createdAt'])})")

    def test_the_bound_is_the_hand_work_files_number(self, tmp_path, monkeypatch, capsys):
        doc = json.loads(CONFIG.read_text())
        doc["hours"] = 30
        moved = tmp_path / "hand-work-age.json"
        moved.write_text(json.dumps(doc))
        monkeypatch.setattr(reconcile, "HAND_WORK_AGE_CONFIG", str(moved))
        got = _held_sweep([_review_card(nodes=[_hold(CAUSE_NEEDS, 25.5)])], capsys)
        _wrote_nothing(got)
        assert " 25h of 30, alarms at " in _held_line(got)

    def test_a_standing_review_cap_stamp_alarms(self, capsys):
        stamp = _node(STAMP, 25.5)
        card = _review_card(nodes=[stamp], labels=("repo:portico", hold.HOLD_LABEL))
        got = _held_sweep([card], capsys)
        _alarmed_once(got, stamp)
        assert _held_line(got).endswith(
            f"(clock: its review-cap-spent stamp of {reconcile._pt(stamp['createdAt'])})")

    def test_a_spent_review_cap_stamp_does_not(self, capsys):
        lifted = _node(hold.lift_line("review-cap-spent", "new-head", "reconcile.py"), 2.0)
        for labels in (("repo:portico",), ("repo:portico", hold.HOLD_LABEL)):
            card = _review_card(nodes=[_node(STAMP, 25.5), lifted], labels=labels)
            got = _held_sweep([card], capsys)
            _wrote_nothing(got)
            assert got.out == ["held-proof: nothing held"]

    def test_an_operators_press_hold_alarms(self, capsys):
        press = _hold(PRESS_NEEDS, 25.5)
        got = _held_sweep([_review_card(nodes=[press])], capsys)
        _alarmed_once(got, press)
        assert _held_line(got).endswith(
            f"(clock: its operator's-press hold of {reconcile._pt(press['createdAt'])})")

    def test_a_hold_naming_the_operators_press_first_is_the_operators(self, capsys):
        """The dispatcher joins several events with `; ` in row order: the
        needs half is read by its opening only."""
        press = _hold(f"{PRESS_NEEDS}; {EVENT_NEEDS}", 25.5)
        got = _held_sweep([_review_card(nodes=[press])], capsys)
        _alarmed_once(got, press)


class TestAnEventHold:
    def test_an_event_hold_never_alarms(self, capsys):
        got = _held_sweep([_review_card(nodes=[_hold(EVENT_NEEDS, 25.5)])], capsys)
        _wrote_nothing(got)
        assert _held_line(got) == (
            "held-proof: DRE-7001 not held for the operator — its hold waits on "
            f"an event ({EVENT_NEEDS}), not the operator")
        assert got.out[-1] == "held-proof: nothing held"

    def test_a_hold_naming_an_event_first_is_the_events(self, capsys):
        mixed = _hold(f"{EVENT_NEEDS}; {PRESS_NEEDS}", 25.5)
        got = _held_sweep([_review_card(nodes=[mixed])], capsys)
        _wrote_nothing(got)
        assert "waits on an event" in _held_line(got)

    @pytest.mark.parametrize("needs,kind", [(CAUSE_NEEDS, "cause hold"),
                                            (PRESS_NEEDS, "operator's-press hold")])
    def test_dre_6694s_shape_alarms_on_the_operators_hold(self, needs, kind, capsys):
        """The cause (or press) hold first, the dispatcher's event hold after
        it: the newer event hold neither replaces the clock nor spends it."""
        first = _hold(needs, 25.5)
        event = _hold(EVENT_NEEDS, 24.5)
        got = _held_sweep([_review_card(nodes=[first, event])], capsys)
        _alarmed_once(got, first)
        assert _held_line(got).endswith(
            f"(clock: its {kind} of {reconcile._pt(first['createdAt'])})")

    def test_dre_6694s_shape_under_the_bound_alarms_on_neither_pass(self, capsys):
        cause = _hold(CAUSE_NEEDS, 23.5)
        event = _hold(EVENT_NEEDS, 22.5)
        due = dead_run.pacific(_clock(cause) + timedelta(hours=24))
        for _ in range(2):
            got = _held_sweep([_review_card(nodes=[cause, event])], capsys)
            _wrote_nothing(got)
            assert _held_line(got) == (
                f"held-proof: DRE-7001 held In Review for the operator 23h of 24, "
                f"alarms at {due} (clock: its cause hold of "
                f"{reconcile._pt(cause['createdAt'])})")


class TestASpentHold:
    def test_an_observation_after_the_hold_spends_it(self, capsys):
        cause = _hold(CAUSE_NEEDS, 25.5)
        got = _held_sweep([_review_card(nodes=[cause, _node(OBSERVED, 3.0)])], capsys)
        _wrote_nothing(got)
        assert _held_line(got) == (
            "held-proof: DRE-7001 not held for the operator — its cause hold of "
            f"{reconcile._pt(cause['createdAt'])} is spent: a 🔬 proof-observed "
            "followed it")

    def test_a_re_run_after_the_hold_spends_it(self, capsys):
        assert proof_run_state.receipt(RERUN) is not None
        cause = _hold(CAUSE_NEEDS, 30.0)
        got = _held_sweep([_review_card(nodes=[cause, _node(RERUN, 20.0)])], capsys)
        _wrote_nothing(got)
        assert _held_line(got) == (
            "held-proof: DRE-7001 not held for the operator — its cause hold of "
            f"{reconcile._pt(cause['createdAt'])} is spent: re-run dispatched after it")

    def test_a_re_run_spends_an_operators_press_hold_too(self, capsys):
        press = _hold(PRESS_NEEDS, 30.0)
        got = _held_sweep([_review_card(nodes=[press, _node(RERUN, 20.0)])], capsys)
        _wrote_nothing(got)
        assert _held_line(got).endswith("is spent: re-run dispatched after it")

    def test_a_newer_cause_hold_is_the_clock(self, capsys):
        old = _hold(CAUSE_NEEDS, 30.0)
        new = _hold(CAUSE_NEEDS, 5.0, observed="the second row")
        got = _held_sweep([_review_card(nodes=[old, _node(RERUN, 20.0), new])], capsys)
        _wrote_nothing(got)
        due = dead_run.pacific(_clock(new) + timedelta(hours=24))
        assert _held_line(got) == (
            f"held-proof: DRE-7001 held In Review for the operator 5h of 24, "
            f"alarms at {due} (clock: its cause hold of {reconcile._pt(new['createdAt'])})")

    def test_a_newer_operators_hold_with_no_re_run_is_the_clock(self, capsys):
        old = _hold(CAUSE_NEEDS, 30.0)
        new = _hold(PRESS_NEEDS, 5.0)
        got = _held_sweep([_review_card(nodes=[old, new])], capsys)
        _wrote_nothing(got)
        assert " 5h of 24, alarms at " in _held_line(got)


class TestNeverAlarmsInReview:
    def test_a_hold_on_the_ceos_press_is_the_return_branchs(self, capsys):
        got = _held_sweep([_review_card(nodes=[_hold(CEO_NEEDS, 25.5)])], capsys)
        _wrote_nothing(got)
        assert _held_line(got) == (
            "held-proof: DRE-7001 not held for the operator — its hold waits on "
            "the CEO's press: the return branch's")

    @pytest.mark.parametrize("ident,labels,why", [
        ("DRE-4541", ("repo:portico", NO_CODE), "a standing card"),
        ("DRE-7001", ("repo:portico", HAND_BUILT), "the CEO's own hand-built card"),
    ])
    def test_a_standing_or_hand_built_card(self, ident, labels, why, capsys):
        card = _review_card(ident, nodes=[_hold(CAUSE_NEEDS, 25.5)], labels=labels)
        got = _held_sweep([card], capsys)
        _wrote_nothing(got)
        assert _held_line(got, ident) == (
            f"held-proof: {ident} held In Review for the operator 25h — never "
            f"alarms ({why})")
        assert "held-proof: nothing held" not in got.out

    def test_no_card_held_says_so(self, capsys):
        got = _held_sweep([_review_card(nodes=[_node("a plain comment", 30.0)])], capsys)
        _wrote_nothing(got)
        assert got.out == ["held-proof: nothing held"]
        got = _held_sweep([], capsys)
        assert got.out == ["held-proof: nothing held"]

    @pytest.mark.parametrize("state", ["In Progress", "Green Light", HAND_WORK])
    def test_another_lane_is_not_this_pass(self, state, capsys):
        card = _review_card(nodes=[_hold(CAUSE_NEEDS, 25.5)], state=state)
        got = _held_sweep([card], capsys)
        _wrote_nothing(got)
        assert got.out == ["held-proof: nothing held"]

    def test_a_card_that_is_not_a_proof_is_not_this_pass(self, capsys):
        card = _review_card(nodes=[_hold(CAUSE_NEEDS, 25.5)], title="a feature")
        got = _held_sweep([card], capsys)
        _wrote_nothing(got)
        assert got.out == ["held-proof: nothing held"]


class TestHeldProofScope:
    def test_another_repos_card_is_neither_read_nor_written(self, capsys):
        card = _review_card(nodes=[_hold(CAUSE_NEEDS, 25.5)],
                            labels=("repo:atlas", NO_CODE))
        got = _held_sweep([card], capsys)
        _wrote_nothing(got)
        got.gql.assert_not_called()
        got.thread.assert_not_called()
        assert got.out == ["held-proof: nothing held"]

    def test_the_pass_makes_no_linear_read(self, capsys):
        got = _held_sweep([_review_card(nodes=[_hold(CAUSE_NEEDS, 25.5)])], capsys)
        got.comment.assert_called_once()
        got.gql.assert_not_called()
        got.thread.assert_not_called()

    def test_an_alarm_adds_no_failure_and_no_defect(self, capsys):
        got = _held_sweep([_review_card(nodes=[_hold(CAUSE_NEEDS, 25.5)])], capsys)
        got.comment.assert_called_once()
        assert reconcile._write_failures == []
        assert reconcile._stale_defects == []

    def test_a_failed_alarm_write_is_a_write_failure(self, capsys):
        got = _held_sweep([_review_card(nodes=[_hold(CAUSE_NEEDS, 25.5)])], capsys,
                          comment_effect=reconcile.linear_ops.LinearError("refused"))
        got.comment.assert_called_once()
        assert len(reconcile._write_failures) == 1
        assert "DRE-7001" in reconcile._write_failures[0]
        assert reconcile._stale_defects == []
        assert " 25h, past 24 — the alarm could not be posted" in _held_line(got)


class TestTheSweepRunsIt:
    def test_main_runs_it_after_the_board_read_over_mine(self, monkeypatch, capsys):
        """A full sweep: the pass runs after `board_read`, is handed this repo's
        cards, spends no request, and posts the one alarm."""
        import test_sweep_spend_lines as spend

        cause = _hold(CAUSE_NEEDS, 25.5)
        ours = _review_card(nodes=[cause])
        theirs = _review_card("DRE-7002", nodes=[_hold(CAUSE_NEEDS, 25.5)],
                              labels=("repo:atlas", NO_CODE))
        fake = spend.FakeLinear([ours, theirs])
        events: list = []
        real_epics, real_pass = reconcile.repo_epics, reconcile.alarm_held_proofs

        def epics(cards):
            events.append("board_read")
            return real_epics(cards)

        alarms: list = []

        def held_pass(cards):
            before = fake.requests
            events.append(("alarm_held_proofs", [c["identifier"] for c in cards]))
            # The harness stubs every Linear write; this is its stub, read here.
            comment = reconcile.linear_ops.cmd_comment
            seen = len(comment.call_args_list)
            try:
                return real_pass(cards)
            finally:
                events.append(("spent", fake.requests - before))
                alarms.extend(c.args for c in comment.call_args_list[seen:])

        monkeypatch.setattr(reconcile, "repo_epics", epics)
        monkeypatch.setattr(reconcile, "alarm_held_proofs", held_pass)
        monkeypatch.setattr(reconcile, "_idle_pass", [])
        monkeypatch.setattr(reconcile, "sweep_idle", lambda: None)
        spend._run_sweep(fake, mocks=("flag_stranded", "move_hand_built_to_review"))
        assert ("alarm_held_proofs", ["DRE-7001"]) in events
        called = events.index(("alarm_held_proofs", ["DRE-7001"]))
        assert "board_read" in events[:called]
        assert ("spent", 0) in events
        assert len(alarms) == 1 and alarms[0][0] == "DRE-7001"
        assert alarms[0][1].startswith(HELD_OPENING)

    def test_main_wraps_it_in_its_own_phase_after_board_read(self):
        source = (ROOT / "scripts" / "reconcile.py").read_text()
        body = source[source.index("\ndef main("):]
        assert body.index('_phase("board_read"') < body.index('_phase("alarm_held_proofs")')


def test_the_config_readme_names_the_in_review_kind():
    readme = json.loads(CONFIG.read_text())["_readme"]
    assert "In Review" in readme
    assert "`PROOF:` card held `In Review` for the operator" in readme
    assert "alarm_held_proofs" in readme
