"""The sweep's stranded watchdog says why it holds (DRE-6177, epic DRE-6172).

`flag_stranded` used to stamp the bare `needs-human` label for two different
facts — no run ever started on a routable card, or the card's repo is not on
the dispatch rail — and nothing on the card said which. Now both holds carry a
`🔒 hold:` stamp from `scripts/hold.py` (DRE-6173), posted right after the
label:

  * NO RUN keeps its exact behavior — label, receipt, no state move — and its
    receipt names the way back: re-send the card, and the hold lifts itself on
    the next run receipt.
  * NO ROUTE is a mechanical fix, not a decision, so the card goes to Triage,
    the operator's queue, with `reason=no-route at=repo:<slug>` on it. The move
    is from-lane-conditional (`Todo,In Progress`), and a card the read door's
    board put in its lane is read live once first (`_door_guard`): one that
    left the lane since gets no receipt, no label and no stamp.

The lane contract carries the epic's clause text (Triage in, Triage out, In
Review in) and its rendered page is regenerated with it.

Run: python3 -m pytest tests/test_hold_sweep_writers.py -v
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
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")
os.environ.setdefault("GH_TOKEN", "x")

import hold  # noqa: E402
import lane_contract  # noqa: E402
import pipeline_act  # noqa: E402
import reconcile  # noqa: E402
import validate_card  # noqa: E402

BUNDLED = {"agent-bureau", "atlas", "bureau-pipeline"}
CANONICAL = frozenset(BUNDLED)

NO_RUN_STAMP = "🔒 hold: reason=stranded-no-run at=none lifts=run-started by=reconcile.py"


def no_route_stamp(slug: str) -> str:
    return f"🔒 hold: reason=no-route at=repo:{slug} lifts=repo-on-rail by=reconcile.py"


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    """This repo's sweep, a pinned snapshot, a canonical snapshot equal to it,
    and a GitHub that lists no build runs — no live read anywhere."""
    monkeypatch.setattr(reconcile, "REPO_SLUG", "agent-bureau")
    monkeypatch.setattr(validate_card, "VALID_SLUGS", set(BUNDLED))
    monkeypatch.setattr(reconcile, "live_rail_slugs", lambda: CANONICAL)

    def read(args):
        if args[0] == "run" and args[1] == "list":
            return "[]", None
        return "", None

    monkeypatch.setattr(reconcile, "_actions_read", read)
    monkeypatch.setattr(reconcile, "flag_stalled_planning", lambda: set())


def _iso(minutes_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat().replace(
        "+00:00", "Z")


def _card(identifier="DRE-7001", state="Todo", labels=("repo:agent-bureau",),
          minutes_stale=45.0, bodies=()):
    return {
        "id": f"uuid-{identifier}",
        "identifier": identifier,
        "title": "a stranded card",
        "description": "work",
        "updatedAt": _iso(minutes_stale),
        "state": {"name": state},
        "labels": {"nodes": [{"name": n} for n in labels]},
        "comments": {"nodes": [{"body": b} for b in bodies]},
    }


def _sweep(cards, *, live_lane=None):
    """Run `flag_stranded` over `cards` with `linear_ops` patched. Every write
    lands on one recorder, in order: `("comment", ident, body)`,
    `("label", ident, label)`, `("advance", ident, to, from_csv)`,
    `("state", ident, to)`. `live_lane` is the lane a fresh read answers."""
    writes: list[tuple] = []
    reads: list[str] = []

    def by_lane(states=reconcile.SWEEP_STATES):
        return [c for c in cards if c["state"]["name"] in states]

    def get_issue(ident, *, fresh=False):
        reads.append(ident)
        card = next(c for c in cards if c["identifier"] == ident)
        return {"identifier": ident,
                "state": {"name": live_lane or card["state"]["name"]},
                "labels": card["labels"]}

    with patch.object(reconcile, "active_cards", side_effect=by_lane), \
         patch.object(reconcile.linear_ops, "get_issue", side_effect=get_issue), \
         patch.object(reconcile.linear_ops, "cmd_comment",
                      side_effect=lambda i, b: writes.append(("comment", i, b))), \
         patch.object(reconcile.linear_ops, "add_label",
                      side_effect=lambda i, label: writes.append(("label", i, label))), \
         patch.object(reconcile.linear_ops, "cmd_advance",
                      side_effect=lambda i, to, frm, *f, **k: writes.append(
                          ("advance", i, to, frm))), \
         patch.object(reconcile.linear_ops, "cmd_state",
                      side_effect=lambda i, to, *f, **k: writes.append(("state", i, to))):
        flagged = reconcile.flag_stranded()
    return flagged, writes, reads


def _comments(writes):
    return [w[2] for w in writes if w[0] == "comment"]


def _receipt(writes):
    found = [b for b in _comments(writes) if f"🚨 {reconcile.WATCHDOG_TAG}:" in b]
    assert len(found) == 1, f"one card-stranded receipt, not {len(found)}: {writes}"
    return found[0]


# --------------------------------------------------------------------------
# NO RUN — the label, the stamp, no state move
# --------------------------------------------------------------------------


def test_a_no_run_card_gets_the_label_then_the_stamp_and_no_move():
    flagged, writes, _ = _sweep([_card()])
    assert flagged == {"DRE-7001"}
    kinds = [w[0] for w in writes]
    assert "advance" not in kinds and "state" not in kinds, writes
    label = writes.index(("label", "DRE-7001", reconcile.HOLD_LABEL))
    # The stamp is the first line of its own comment, right after the label.
    assert writes[label + 1] == ("comment", "DRE-7001", NO_RUN_STAMP)
    # The card-stranded receipt is still posted, before the hold, as today.
    receipt = _receipt(writes)
    assert writes.index(("comment", "DRE-7001", receipt)) < label


def test_the_no_run_stamp_reads_back_as_its_reason():
    _, writes, _ = _sweep([_card()])
    labels = ["repo:agent-bureau", reconcile.HOLD_LABEL]
    assert hold.reason_of(labels, _comments(writes)) == "stranded-no-run"


def test_the_no_run_receipt_names_the_resend_and_the_lift():
    _, writes, _ = _sweep([_card()])
    body = _receipt(writes)
    anchor = pipeline_act.record("card-stranded")["emits"]["anchor"]
    assert anchor in body, "the act's declared anchor phrase must still stand"
    assert "out of Todo and back" in body
    assert "linear_ops.py unpark" in body
    assert "lifts itself on the first run receipt newer than" in body
    assert "nothing on the card needs clearing" in body
    # The sentence comes after the anchor phrase, never before it.
    assert body.index(anchor) < body.index("out of Todo and back")


# --------------------------------------------------------------------------
# NO ROUTE — the label, the stamp, the move to Triage
# --------------------------------------------------------------------------


@pytest.mark.parametrize("state", ["Todo", "In Progress"])
def test_a_no_route_card_is_held_stamped_and_moved_to_triage(state):
    card = _card(state=state, labels=("repo:ghost-product",))
    flagged, writes, _ = _sweep([card])
    assert flagged == {"DRE-7001"}
    receipt = _receipt(writes)
    stamp = no_route_stamp("ghost-product")
    assert writes == [
        ("comment", "DRE-7001", receipt),
        ("label", "DRE-7001", reconcile.HOLD_LABEL),
        ("comment", "DRE-7001", stamp),
        ("advance", "DRE-7001", "Triage", "Todo,In Progress"),
    ]


def test_a_card_with_no_repo_label_is_stamped_repo_none():
    flagged, writes, _ = _sweep([_card(labels=())])
    assert flagged == {"DRE-7001"}
    assert ("comment", "DRE-7001", no_route_stamp("none")) in writes
    assert ("advance", "DRE-7001", "Triage", "Todo,In Progress") in writes


def test_a_slug_the_stamp_cannot_carry_is_stamped_repo_none():
    """A malformed label is no route either; the hold still says so, rather
    than the stamp refusing it after the receipt is already posted."""
    flagged, writes, _ = _sweep([_card(labels=("repo:Ghost Product",))])
    assert flagged == {"DRE-7001"}
    assert ("comment", "DRE-7001", no_route_stamp("none")) in writes
    assert writes[-1] == ("advance", "DRE-7001", "Triage", "Todo,In Progress")


def test_the_no_route_stamp_reads_back_as_its_reason():
    _, writes, _ = _sweep([_card(labels=("repo:ghost-product",))])
    labels = ["repo:ghost-product", reconcile.HOLD_LABEL]
    assert hold.reason_of(labels, _comments(writes)) == "no-route"


def test_a_slug_only_the_canonical_snapshot_carries_is_left_alone(monkeypatch):
    """A stale pin, not a dead route (DRE-2260) — exactly as before."""
    monkeypatch.setattr(reconcile, "live_rail_slugs",
                        lambda: CANONICAL | {"newly-onboarded"})
    flagged, writes, _ = _sweep([_card(labels=("repo:newly-onboarded",))])
    assert flagged == set()
    assert writes == []


def test_the_no_route_receipt_names_both_fixes_and_the_lift():
    _, writes, _ = _sweep([_card(labels=("repo:ghost-product",))])
    body = _receipt(writes)
    assert body.startswith(f"🚨 {reconcile.WATCHDOG_TAG}:")
    assert "repo:" in body and "label" in body
    assert "config/repo-map.json" in body
    assert "lifts itself on the next hygiene pass" in body
    assert "nothing on the card needs clearing" in body
    assert pipeline_act.read_trailer(body) is not None


@pytest.mark.parametrize("labels", [("repo:ghost-product",), ("repo:agent-bureau",)],
                         ids=["no-route", "no-run"])
def test_each_receipt_is_the_card_stranded_act_and_spends_no_stamp(labels):
    """Both bodies carry the `card-stranded` trailer, and neither reads as a
    re-send or as anything that retires the stamp posted after it."""
    _, writes, _ = _sweep([_card(labels=labels)])
    body = _receipt(writes)
    assert (pipeline_act.read_trailer(body) or {}).get("act") == "card-stranded"
    assert reconcile._TODO_REDISPATCH_NOTE not in body
    assert hold.HYG_CLEARED_TAG not in body
    assert reconcile.dead_run.RESET_TAG not in body
    assert not body.startswith(hold.LIFT_PREFIX)


# --------------------------------------------------------------------------
# The read door: a card the door's board put in its lane is read live once
# --------------------------------------------------------------------------


@pytest.fixture
def door_read(monkeypatch):
    """Mark the card as decided on the read door's facts (`_door_sourced`)."""
    monkeypatch.setattr(reconcile, "_door_sourced", {"DRE-7001"})


def test_the_door_guard_names_the_lane_the_decision_read(door_read):
    card = _card(state="In Progress", labels=("repo:ghost-product",))
    assert reconcile._door_guard(card) == {"expect": ("In Progress",), "labels_absent": ()}


def test_a_door_read_no_route_card_that_left_its_lane_gets_nothing(door_read):
    card = _card(state="Todo", labels=("repo:ghost-product",))
    flagged, writes, reads = _sweep([card], live_lane="In Review")
    assert reads == ["DRE-7001"], "one live read before the first write"
    assert writes == [], "no receipt, no label, no stamp, no move"
    assert flagged == set()


def test_a_door_read_no_route_card_still_in_its_lane_is_held_and_moved(door_read):
    card = _card(state="Todo", labels=("repo:ghost-product",))
    flagged, writes, reads = _sweep([card], live_lane="Todo")
    assert reads == ["DRE-7001"]
    assert flagged == {"DRE-7001"}
    assert ("label", "DRE-7001", reconcile.HOLD_LABEL) in writes
    assert ("comment", "DRE-7001", no_route_stamp("ghost-product")) in writes
    assert writes[-1] == ("advance", "DRE-7001", "Triage", "Todo,In Progress")


def test_a_linear_read_no_route_card_costs_no_live_read():
    """`{}` on Linear's read: the move is the from-lane-conditional advance
    alone, with no request before it."""
    _, writes, reads = _sweep([_card(labels=("repo:ghost-product",))])
    assert reads == []
    assert writes[-1] == ("advance", "DRE-7001", "Triage", "Todo,In Progress")


# --------------------------------------------------------------------------
# The lane contract carries the epic's clause text
# --------------------------------------------------------------------------


def _lane(name):
    with open(ROOT / "config" / "lane-contract.json", encoding="utf-8") as fh:
        doc = json.load(fh)
    return next(entry for entry in doc["lanes"] if entry["name"] == name)


def test_triage_entrance_names_the_no_route_park():
    text = _lane("Triage")["clauses"]["entrance"]["text"]
    assert "a card with no dispatch route" in text
    assert "is not a key of `config/repo-map.json`" in text
    assert "`🔒 hold: reason=no-route`" in text
    assert "left alone by the hygiene agent's Triage lane while the hold stands" in text
    assert "lifted by the hygiene agent's holds lane" in text


def test_triage_exit_names_both_lifts_and_where_each_sends_the_card():
    text = _lane("Triage")["clauses"]["exit"]["text"]
    assert "returns the card to Backlog, never Todo" in text, "the old rule stands"
    assert "holds lane lifts a hold whose condition has cleared" in text
    assert "a `no-route` hold to Planning" in text
    assert "a `fix-dispute` or `unfixable-check` hold to In Review" in text
    assert "new head" in text


def test_in_review_entrance_names_the_one_further_move_out_of_green_light():
    text = _lane("In Review")["clauses"]["entrance"]["text"]
    assert "merge gate's release (DRE-4341)" in text, "the gate's release stands"
    assert "one more move out of Green Light into this lane" in text
    assert "`🔒 hold: reason=review-cap-spent`" in text
    assert "standing unspent" in text
    assert "moves the card nowhere by itself" in text
    assert "returns a card from Triage" in text


def test_the_rendered_page_matches_the_contract():
    with open(ROOT / "docs" / "lane-contract.md", encoding="utf-8") as fh:
        page = fh.read()
    assert page == lane_contract.render_markdown()
    assert "reason=no-route" in page
    assert "reason=review-cap-spent" in page


# --------------------------------------------------------------------------
# The registry row does not change: `hold.py check` is the proof
# --------------------------------------------------------------------------


def test_the_registry_still_names_every_site_exactly_once():
    assert hold.problems() == []


def test_the_watchdog_writes_through_hold_apply():
    sites = [s for s in hold.discover()
             if s.file == "scripts/reconcile.py" and s.scope == "flag_stranded"]
    assert len(sites) == 1
    assert sites[0].source.startswith("hold.apply(")
