"""RED-first: the flip — FLEET routes to In Progress, and the start lane is In
Progress, so promotion and dispatch are one move (DRE-7120, epic: the Todo
retirement).

Every case here reads the SHIPPED contract and vocabulary: no fixture contract,
no monkeypatched destination. Linear is faked at the seams the sweep's own
suites fake it at (`test_operator_card_promotion._Board` for the promoter,
`test_promoted_not_started._Sweep` for the nudge loop), and the planner's guard
is driven through `dedupe_dispatch.py plan-gate` the way plan.yml runs it
(`test_plan_gate_hands_back_build`).

WHAT IS UNDER TEST:
  1. THE PROMOTION — an unblocked FLEET child of an approved epic, with room
     under the cap, goes `Queued → In Progress` under the receipt
     `🧹 Auto-promoted Queued → In Progress`; a blocked one, one over the cap
     and one whose epic is not approved stay in `Queued`; WORKBENCH and
     OPERATOR go to `Hand-work`; the WIP count after the pass is the count the
     same board gave before the flip plus the cards promoted.
  2. A ONE-OFF WEARING `agent:planner` — no children, a `one-off` stamp — is
     promoted the same way and counted in the cap. A card with an `epic` stamp
     never is.
  3. THE GAP — the relay that is live today, before DRE-7107, answering the
     move into In Progress: (a) a card without `agent:planner` gets nothing
     from it and is started once by the sweep's not-started re-fire; (b) a
     one-off wearing the label is sent to the planner, whose guard hands it
     back as one build. Neither goes to Planning; each gets one
     `agent-execute`.
  4. THE CONFIG NAMES NO `Todo` outside Todo's own lane entry.

Run: cd bureau-pipeline && python3 -m pytest tests/test_build_starts_in_progress.py -v
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
# Collected early, so these defaults become the process's: they match the
# suites that read `reconcile.REPO` off the environment (agent-bureau), and the
# fixture below patches the repo each case here runs as. The helper modules
# imported below default to portico, and set nothing once these are set.
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")
os.environ.setdefault("GH_TOKEN", "x")

import lane_contract  # noqa: E402
import planning_shape  # noqa: E402
import plan_run  # noqa: E402
import reconcile  # noqa: E402
import routing_verdict  # noqa: E402

import test_operator_card_promotion as promo  # noqa: E402
import test_plan_gate_hands_back_build as gate  # noqa: E402
import test_promoted_not_started as sweep  # noqa: E402

START = lane_contract.START_MINUTES
RECEIPT = f"🧹 Auto-promoted {lane_contract.QUEUE_LANE} → In Progress"
ONE_OFF = planning_shape.shape_comment("one-off", "one card, one pull request")
EPIC_STAMP = planning_shape.shape_comment("epic", "three pull requests with a contract")
REDISPATCHED = "🧹 Reconcile: card was promoted and no run started — re-dispatched."


@pytest.fixture(autouse=True)
def _clean_sweep_state(monkeypatch):
    monkeypatch.setattr(reconcile, "REPO", "dreadnought-foundry/portico")
    monkeypatch.setattr(reconcile, "REPO_SLUG", "portico")
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()
    reconcile.reset_sweep_cards()
    yield
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()
    reconcile.reset_sweep_cards()


def _queued(identifier, *, comments=(promo.FLEET,), labels=("agent:engineer",), **kw):
    """A Queued card as `backlog_children` hands it to the gate, in the repo
    `test_operator_card_promotion._Board` promotes for."""
    return promo._card(identifier=identifier, comments=comments,
                       labels=("repo:bureau-pipeline",) + tuple(labels), **kw)


_Board = promo._Board


# --------------------------------------------------------------------------
# 1: the promotion, against the shipped contract and vocabulary
# --------------------------------------------------------------------------
class TestTheShippedRoute:
    def test_the_contract_and_vocabulary_ship_the_flip(self):
        assert lane_contract.START_LANE == "In Progress"
        assert lane_contract.START_LANES == ("In Progress", "Todo")
        assert routing_verdict.destination("FLEET") == "In Progress"
        assert routing_verdict.BUILD_LANE == lane_contract.DISPATCH_LANE
        assert routing_verdict.sweep_lanes() == ("In Progress", "Hand-work")

    def test_an_unblocked_child_of_an_approved_epic_goes_to_in_progress(self):
        board = _Board(_queued("DRE-11"))
        assert board.promote() == 1
        assert board.lane_of("DRE-11") == "In Progress"
        assert board.receipt_for("DRE-11").startswith(RECEIPT + ": ")

    def test_a_blocked_child_stays_in_queued(self):
        blocked = _queued("DRE-12")
        blocked["inverseRelations"] = {"nodes": [
            {"type": "blocks", "issue": {"identifier": "DRE-9", "state": {"name": "In Progress"}}}
        ]}
        board = _Board(blocked)
        with patch.object(reconcile, "card_state", return_value="In Progress"):
            assert board.promote() == 0
        assert board.writes == []

    def test_one_over_the_cap_stays_in_queued(self):
        with patch.object(reconcile, "MAX_WIP", 1):
            board = _Board(_queued("DRE-11"), _queued("DRE-16"))
            assert board.promote(active_count=0) == 1
        assert board.lane_of("DRE-11") == "In Progress"
        assert [w for w in board.writes if w[1] == "DRE-16"] == []

    def test_an_unapproved_epics_child_stays_in_queued(self):
        board = _Board(_queued("DRE-13", parent_state="Green Light"))
        assert board.promote() == 0
        assert board.writes == []

    @pytest.mark.parametrize("verdict", [promo.WORKBENCH, promo.OPERATOR])
    def test_a_persons_card_goes_to_hand_work(self, verdict):
        board = _Board(_queued("DRE-14", comments=(verdict,)))
        assert board.promote() == 1
        assert board.lane_of("DRE-14") == "Hand-work"
        assert not board.receipt_for("DRE-14").startswith(RECEIPT)

    def test_the_wip_count_is_the_old_count_plus_the_cards_promoted(self):
        """The board before the pass, the same board after it, and the board
        the old route would have left — the same cards sitting in Todo."""
        before = [sweep._card("In Progress", identifier="DRE-1"),
                  sweep._card("In Review", identifier="DRE-2")]
        board = _Board(_queued("DRE-11"), _queued("DRE-17"),
                       _queued("DRE-14", comments=(promo.OPERATOR,)))
        assert board.promote(active_count=len(before)) == 3
        promoted = [i for i in ("DRE-11", "DRE-17") if board.lane_of(i) == "In Progress"]
        assert promoted == ["DRE-11", "DRE-17"]
        after = before + [sweep._card(board.lane_of(i), identifier=i) for i in promoted]
        old = before + [sweep._card("Todo", identifier=i) for i in promoted]
        with patch.object(reconcile, "repo_epics", return_value=set()):
            count = lambda b: reconcile.wip_count(reconcile.wip_base(b))  # noqa: E731
            assert count(after) == count(before) + len(promoted) == count(old)


# --------------------------------------------------------------------------
# 2: a one-off wearing `agent:planner`, and a stamped epic
# --------------------------------------------------------------------------
class TestAOneOffWearingThePlannerLabel:
    def _one_off(self, identifier="DRE-21"):
        return _queued(identifier, comments=(ONE_OFF, promo.FLEET),
                       labels=("agent:planner",))

    def test_it_is_promoted_with_the_same_receipt(self):
        board = _Board(self._one_off())
        assert board.promote() == 1
        assert board.lane_of("DRE-21") == "In Progress"
        assert board.receipt_for("DRE-21").startswith(RECEIPT + ": ")

    def test_it_is_counted_in_the_cap(self):
        with patch.object(reconcile, "MAX_WIP", 1):
            board = _Board(self._one_off("DRE-21"), _queued("DRE-22"))
            assert board.promote(active_count=0) == 1
        assert board.lane_of("DRE-21") == "In Progress"
        assert [w for w in board.writes if w[1] == "DRE-22"] == []
        with patch.object(reconcile, "repo_epics", return_value=set()):
            card = sweep._card("In Progress", identifier="DRE-21", labels=("agent:planner",))
            assert reconcile.wip_count(reconcile.wip_base([card])) == 1

    def test_a_card_with_an_epic_stamp_is_never_promoted(self):
        board = _Board(_queued("DRE-23", comments=(EPIC_STAMP, promo.FLEET),
                               labels=("agent:planner",)))
        board.promote()
        assert [w for w in board.writes if w[0] == "advance"] == []


# --------------------------------------------------------------------------
# 3: the gap — the relay live today answers the move into In Progress
# --------------------------------------------------------------------------
def _relay_today(card: dict, to_lane: str) -> list:
    """What agent-bureau's `cloud/relay/lambda_function.py` at `main` before
    DRE-7107 dispatches for a card entering `to_lane`, as
    `(event_type, client_payload)` pairs. Only the move under test is modelled:
    into In Progress, `_is_epic_activation(state_name, labels)` reads the
    `agent:planner` label alone — True sends the planner, with
    `trigger_state` "in progress", and one reconcile pass for the epic it
    takes the card to be; False is a build run moving its own card, and the
    relay sends nothing."""
    assert to_lane == "In Progress", to_lane
    labels = [n["name"] for n in card["labels"]["nodes"]]
    if "agent:planner" not in labels:
        return []
    return [
        (plan_run.PLAN_EVENT, {
            "card_id": card["id"], "identifier": card["identifier"],
            "title": card["title"], "description": card["description"],
            "labels": labels,
            "url": f"https://linear.app/dreadnoughtfoundry/issue/{card['identifier']}",
            "trigger_state": "in progress",
        }),
        ("reconcile", {"reason": "epic-activated", "card": card["identifier"]}),
    ]


class TestTheGapBeforeTheNewRelayIsLive:
    def test_a_card_without_the_planner_label_is_started_once_by_the_refire(self):
        ident = "DRE-31"
        board = _Board(_queued(ident))
        assert board.promote() == 1
        assert board.lane_of(ident) == "In Progress"
        promoted = board.comments_on(ident)
        card = sweep._card("In Progress", identifier=ident)
        assert _relay_today(card, "In Progress") == []

        def in_progress(idle, *, refusal="", extra=()):
            comments = [(b, idle) for b in promoted] + list(extra)
            return sweep._Sweep(
                sweep._card("In Progress", identifier=ident, idle=idle, comments=comments),
                refusal=refusal,
            ).run()

        first = in_progress(START + 1)
        assert first.refired(ident) == [(ident, plan_run.EXECUTE_EVENT)]
        second = in_progress(
            START + 16, refusal=f"Agent Task run 41 for {ident} is in_progress",
            extra=[(b, 15) for b in first.bodies(ident)],
        )
        assert second.refired(ident) == []
        for run in (first, second):
            assert run.deaths(ident) == []
            assert run.moves == []
        assert board.writes == [("advance", ident, "In Progress")]
        executes = first.refired(ident) + second.refired(ident)
        assert [e for _, e in executes] == [plan_run.EXECUTE_EVENT]

    def test_a_planner_labelled_one_off_is_handed_back_as_one_build(
        self, monkeypatch, tmp_path, capsys
    ):
        ident = gate.CARD
        board = _Board(_queued(ident, comments=(ONE_OFF, promo.FLEET),
                               labels=("agent:planner",)))
        assert board.promote() == 1
        assert board.lane_of(ident) == "In Progress"
        promoted = [b for b in board.comments_on(ident)]
        card = sweep._card("In Progress", identifier=ident, labels=("agent:planner",))
        card["id"], card["title"], card["description"] = (
            "uuid-7777", "Fix the widget", "Do the thing.")
        answered = _relay_today(card, "In Progress")
        assert [event for event, _ in answered] == [plan_run.PLAN_EVENT, "reconcile"]
        plan_payload = answered[0][1]
        assert plan_payload["trigger_state"] == "in progress"
        assert "agent:planner" in plan_payload["labels"]

        lin, gh = gate.FakeLinear(comments=[gate._stamp("one-off")]), gate.FakeGitHub()
        outputs = gate._plan_gate(monkeypatch, tmp_path, lin, gh, payload=plan_payload)
        assert outputs["skip"] == "true"
        assert gh.events == [plan_run.EXECUTE_EVENT]
        assert lin.writes == [], "the plan run writes no lane"
        capsys.readouterr()

        def in_progress(idle, *, refusal=""):
            comments = [(ONE_OFF, 600)] + [(b, idle) for b in promoted] + [
                (b, idle - 1) for b in lin.posted]
            return sweep._Sweep(
                sweep._card("In Progress", identifier=ident, idle=idle,
                            labels=("agent:planner",), comments=comments),
                refusal=refusal,
            ).run()

        monkeypatch.setenv("SWEEP_REASON", answered[1][1]["reason"])
        monkeypatch.setenv("SWEEP_CARD", ident)
        activated = in_progress(2, refusal=f"Agent Task run 42 for {ident} is in_progress")
        monkeypatch.delenv("SWEEP_REASON")
        monkeypatch.delenv("SWEEP_CARD")
        later = in_progress(START + 5, refusal=f"Agent Task run 42 for {ident} is in_progress")
        for run in (activated, later):
            assert run.refired(ident) == []
            assert run.deaths(ident) == []
            assert run.moves == []
        assert board.writes == [("advance", ident, "In Progress")]
        assert "Planning" not in [w[2] for w in board.writes]
        assert gh.events.count(plan_run.EXECUTE_EVENT) == 1


# --------------------------------------------------------------------------
# 4: the four config files name no `Todo` outside Todo's own lane entry
# --------------------------------------------------------------------------
CONFIGS = ("lane-contract.json", "routing-verdicts.json", "holds.json", "pipeline-acts.json")
# Assembled, so this file does not name the lane where a repo-wide scan reads.
LANE = "To" + "do"
WORD = re.compile(rf"\b{LANE}\b")


def _strings(node, path=()):
    """(path, string) for every string in a JSON document, keys excluded."""
    if isinstance(node, str):
        yield path, node
    elif isinstance(node, dict):
        for key, value in node.items():
            yield from _strings(value, path + (key,))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _strings(value, path + (i,))


def _quotes_its_file(doc, path, text) -> bool:
    """An `anchor` is a byte-for-byte quote of the source file its row names
    — the receipt that file emits — so it changes only with that file. The
    retirement card owns those files (DRE-7129); a quote of one is not prose
    this card rewords."""
    if not path or path[-1] != "anchor":
        return False
    row = doc
    for key in path[:-1]:
        row = row[key]
    source = ROOT / (row.get("file") or "")
    return source.is_file() and text in source.read_text(encoding="utf-8")


@pytest.mark.parametrize("name", CONFIGS)
def test_no_string_names_the_lane_outside_its_own_entry(name):
    doc = json.loads((ROOT / "config" / name).read_text(encoding="utf-8"))
    own = None
    if name == "lane-contract.json":
        own = next(i for i, e in enumerate(doc["lanes"]) if e["name"] == LANE)
    found = [
        "/".join(map(str, path)) for path, text in _strings(doc)
        if WORD.search(text)
        and not (own is not None and path[:2] == ("lanes", own))
        and not _quotes_its_file(doc, path, text)
    ]
    assert found == [], f"{name} names {LANE} at {found}"


def test_the_scan_sees_the_lane_entry_it_exempts():
    """Guard the guard: Todo's own entry still names it, so the exemption is
    what lets that entry through — not a scan that reads nothing."""
    doc = lane_contract.load()
    own = next(e for e in doc["lanes"] if e["name"] == LANE)
    assert any(WORD.search(text) for _, text in _strings(own))
    assert own["replaced_by"] == "In Progress"
    assert own["status"] == "live"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
