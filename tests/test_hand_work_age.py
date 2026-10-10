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
  * the CEO's own `hand-built` card and a standing card never alarm;
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
