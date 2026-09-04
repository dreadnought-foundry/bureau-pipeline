"""RED-first tests: the retroactive pass that empties Backlog into Intake
(DRE-2687).

THE RULE, decided by the CEO on 2026-08-26: **everything unstarted moves to
Intake and is re-planned before it is work again. There is no allowlist.** The
2026-08-23 exemption (D4) is withdrawn — DRE-2725's guard says a card with no
verdict cannot rest in any lane, so an "exempt" card is precisely a card the
guard would bounce on its first sweep. There were only ever two consistent
positions, and a permanent legacy exemption is the two-population board
DRE-2728 exists to prevent.

WHAT REPLACES THE EXEMPTION IS AN ORDERING, NOT A LIST. The seven cards inside
`promote_ready()`'s reach are live work, so they are classified FIRST — batch
one — and the run records which they were. Then newest-first through the rest
(D2, approved 2026-08-23: the newest cards are fresh enough in the operator's
memory that a wrong verdict is spotted instantly).

GRANDFATHERING IS NOT AN EXEMPTION. A card with a run receipt still ticking or
an open pull request is justified in its lane BY EVIDENCE and is left to
finish. The distinction is drawn on that evidence, never on a list of ids —
these tests fail if it is ever drawn on ids.

Run: cd bureau-pipeline && python3 -m pytest tests/test_backlog_cutover.py -v
"""
from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")
os.environ.setdefault("GH_TOKEN", "x")

import backlog_cutover as cutover  # noqa: E402
import reconcile  # noqa: E402
import routing_verdict  # noqa: E402


def _iso(minutes_ago: float) -> str:
    return (
        (datetime.now(UTC) - timedelta(minutes=minutes_ago))
        .isoformat()
        .replace("+00:00", "Z")
    )


def _card(
    identifier="DRE-2001",
    *,
    labels=("repo:portico", "agent:engineer"),
    parent=None,
    parent_state="In Progress",
    created_minutes_ago=10_000,
    comments=(),
    state="Backlog",
):
    return {
        "id": f"uuid-{identifier}",
        "identifier": identifier,
        "title": f"card {identifier}",
        "description": "work",
        "createdAt": _iso(created_minutes_ago),
        "state": {"name": state},
        "labels": {"nodes": [{"name": n} for n in labels]},
        "parent": (
            {"identifier": parent, "state": {"name": parent_state}} if parent else None
        ),
        "comments": {
            "nodes": [
                {"body": b, "createdAt": _iso(age)} for b, age in comments
            ]
        },
    }


class _Lops:
    """A Linear stand-in that records every write."""

    def __init__(self, population, occupancy=None):
        self.population = population
        self._occupancy = occupancy or {}
        self.advanced: list[tuple] = []
        self.comments: list[tuple] = []

    def gql_paged(self, query, variables=None):
        lane = (variables or {}).get("lane")
        return [c for c in self.population if c["state"]["name"] == lane]

    def gql(self, query, variables=None):
        lane = (variables or {}).get("lane")
        return {"issues": {"nodes": [], "pageInfo": {"hasNextPage": False}}} if lane else {}

    def cmd_advance(self, identifier, to_state, from_states_csv):
        self.advanced.append((identifier, to_state, from_states_csv))
        for card in self.population:
            if card["identifier"] == identifier and card["state"]["name"] in [
                s.strip() for s in from_states_csv.split(",")
            ]:
                card["state"]["name"] = to_state

    def cmd_comment(self, identifier, body):
        self.comments.append((identifier, body))


# --------------------------------------------------------------------------
# 1: NO EXEMPTION LIST — every Backlog card is a candidate for the move
# --------------------------------------------------------------------------
def _one_of_every_historically_exempt_class() -> list[dict]:
    """The four classes D4 exempted on 2026-08-23, plus an ordinary card.

    Every one of them must be a candidate now: the CEO withdrew the exemption
    on 2026-08-26 and the guard would have bounced all of them anyway.
    """
    return [
        # inside promote_ready()'s reach: a child of an active epic
        _card("DRE-2101", parent="DRE-2100", parent_state="In Progress"),
        # held by the phantom-blocker defect / needs-human
        _card("DRE-2102", labels=("repo:portico", "agent:engineer", "needs-human")),
        # an operator card
        _card("DRE-2103", labels=("repo:portico", "no-code")),
        # the paused DeltaSolv set
        _card("DRE-2104", labels=("repo:deltasolv", "agent:engineer")),
        # a card routed PARKED — deliberately not to be built
        _card(
            "DRE-2105",
            comments=((routing_verdict.verdict_comment("PARKED", "not now"), 500),),
        ),
        # an ordinary one-off
        _card("DRE-2106"),
    ]


def test_every_backlog_card_is_a_candidate_for_the_move():
    """THE AMENDED CRITERION: no exemption list exists. One card of every class
    D4 would have exempted, and all of them move."""
    cards = _one_of_every_historically_exempt_class()
    result = cutover.plan(cards)
    assert {c["identifier"] for c in result["move"]} == {
        c["identifier"] for c in cards
    }
    assert result["in_flight"] == []


def test_the_module_holds_no_hand_kept_card_list():
    """Structural: an allowlist would show up as card ids in the source. The
    docstring's DRE references are the record of the decision, so the scan is
    of CODE lines only."""
    source = (ROOT / "scripts" / "backlog_cutover.py").read_text(encoding="utf-8")
    ids = cutover.card_ids_in_code(source)
    assert ids == [], f"card identifiers hard-coded in the cutover's code: {ids}"


def test_the_scan_would_catch_a_planted_allowlist():
    """Guard the guard — the scan must fail on a list it is meant to catch."""
    planted = 'EXEMPT = ("DRE-2670", "DRE-2492")\n'
    assert cutover.card_ids_in_code(planted) == ["DRE-2670", "DRE-2492"]


# --------------------------------------------------------------------------
# 2: the promoter-reach cards go FIRST, and reach is read from live state
# --------------------------------------------------------------------------
def test_promoter_reach_cards_are_batch_one_and_are_recorded():
    reach = _card("DRE-2201", parent="DRE-2200", parent_state="In Progress",
                  created_minutes_ago=99_000)  # the OLDEST card in the set
    others = [_card(f"DRE-23{i:02d}", created_minutes_ago=1000 - i) for i in range(4)]
    result = cutover.plan([*others, reach])
    assert result["batch_one"] == ["DRE-2201"]
    assert result["move"][0]["identifier"] == "DRE-2201", (
        "live work is stranded for hours, not parked forever — it goes first "
        "even though newest-first would have put it last"
    )


def test_the_rest_are_ordered_newest_first():
    """D2, approved 2026-08-23: newest first, because a wrong verdict on a card
    the operator still remembers is spotted instantly."""
    cards = [
        _card("DRE-2301", created_minutes_ago=3000),
        _card("DRE-2302", created_minutes_ago=1000),
        _card("DRE-2303", created_minutes_ago=2000),
    ]
    result = cutover.plan(cards)
    assert [c["identifier"] for c in result["move"]] == [
        "DRE-2302", "DRE-2303", "DRE-2301",
    ]


def test_reach_is_computed_from_live_state_not_from_ids():
    """The exemption is replaced by an ordering, and the ordering is derived:
    the SAME card is in batch one or not depending on its epic's live lane."""
    active = _card("DRE-2401", parent="DRE-2400", parent_state="In Progress")
    assert cutover.in_promoter_reach(active) is True
    parked = _card("DRE-2401", parent="DRE-2400", parent_state="Planning")
    assert cutover.in_promoter_reach(parked) is False


def test_a_parentless_card_is_in_reach_only_with_a_promotable_verdict():
    """promote_ready()'s own rule for the one-off (DRE-2735): the verdict IS
    the approval, so a parentless card with a FLEET verdict is live work."""
    fleet = _card(
        "DRE-2402",
        comments=((routing_verdict.verdict_comment("FLEET", "buildable"), 500),),
    )
    assert cutover.in_promoter_reach(fleet) is True
    assert cutover.in_promoter_reach(_card("DRE-2403")) is False


def test_an_epic_is_never_in_the_promoters_reach():
    """The sweep never promotes an `agent:planner` card."""
    epic = _card("DRE-2404", labels=("repo:portico", "agent:planner"),
                 parent="DRE-2400", parent_state="In Progress")
    assert cutover.in_promoter_reach(epic) is False


def test_reach_uses_the_promoters_own_epic_states():
    """One fact, one source: if reconcile changes what counts as an activated
    epic, this fails rather than the cutover quietly disagreeing with the
    promoter it is named after."""
    assert cutover.EPIC_ACTIVE_STATES == reconcile.EPIC_ACTIVE_STATES


# --------------------------------------------------------------------------
# 3: in-flight is drawn on EVIDENCE
# --------------------------------------------------------------------------
def test_a_card_with_an_open_pull_request_is_not_moved():
    card = _card("DRE-2501")
    result = cutover.plan([card], open_pr_refs=("agent/DRE-2501-a-thing",))
    assert result["move"] == []
    assert result["in_flight"][0]["identifier"] == "DRE-2501"
    assert "pull request" in result["in_flight"][0]["why"]


def test_a_card_with_a_live_run_receipt_is_not_moved():
    card = _card("DRE-2502", comments=(("⏳ 2/5 failing tests written", 5),))
    result = cutover.plan([card])
    assert result["move"] == []
    assert "receipt" in result["in_flight"][0]["why"]


def test_an_old_run_receipt_is_not_evidence_of_anything_in_flight():
    """The distinction that matters: almost every legacy card was dispatched
    once. A receipt from months ago is history, not a live run — reading it as
    in-flight would re-create the exemption by accident."""
    card = _card("DRE-2503", comments=(("⏳ 2/5 failing tests written", 90_000),))
    result = cutover.plan([card])
    assert [c["identifier"] for c in result["move"]] == ["DRE-2503"]


def test_the_proof_of_life_prefixes_are_the_sweeps_own():
    """One fact, one source: the receipts that mean 'a run is going' are the
    sweep's, and this fails if the sweep's set ever changes underneath."""
    assert cutover.LIFE_PREFIXES == reconcile._LIFE_PREFIXES


def test_a_sweep_receipt_is_not_proof_of_life():
    """Only ⏳ and 🧠 count — the sweep's own 🪦/🧹/🚨 receipts would make every
    card it ever touched look alive."""
    card = _card("DRE-2504", comments=(("🧹 Reconcile: parked in Backlog", 5),))
    result = cutover.plan([card])
    assert [c["identifier"] for c in result["move"]] == ["DRE-2504"]


def test_the_pr_match_is_anchored_to_the_whole_card_id():
    """DRE-2025: `agent/DRE-25010-other` must not attribute to DRE-2501."""
    card = _card("DRE-2501")
    result = cutover.plan([card], open_pr_refs=("agent/DRE-25010-other",))
    assert [c["identifier"] for c in result["move"]] == ["DRE-2501"]


def test_an_unreadable_pr_lookup_refuses_the_run():
    """DRE-2034: an unreadable answer is not 'no PR'. Acting on it would yank a
    live card out from under an open pull request."""
    def boom(_cmd):
        raise cutover.card_pr.PrLookupError("gh exploded")

    with pytest.raises(cutover.CutoverUnreadable):
        cutover.open_pr_refs(("portico",), run=boom)


def test_open_pr_refs_reads_only_open_pull_requests():
    seen: list[list[str]] = []

    def run(cmd):
        seen.append(cmd)
        return json.dumps([{"headRefName": "agent/DRE-2501-a-thing"}])

    refs = cutover.open_pr_refs(("portico",), run=run)
    assert refs == {"agent/DRE-2501-a-thing"}
    assert "--state" in seen[0] and "open" in seen[0]
    assert "dreadnought-foundry/portico" in seen[0]


# --------------------------------------------------------------------------
# 4: the move itself, and what it records
# --------------------------------------------------------------------------
def test_a_dry_run_writes_nothing():
    lops = _Lops(_one_of_every_historically_exempt_class())
    result = cutover.run(lops, cutover.plan(lops.population), apply=False)
    assert lops.advanced == [] and lops.comments == []
    assert result["moved"] == []


def test_apply_moves_backlog_to_intake_and_says_why_on_each_card():
    cards = _one_of_every_historically_exempt_class()
    lops = _Lops(cards)
    plan = cutover.plan(cards)
    result = cutover.run(lops, plan, apply=True)
    assert len(result["moved"]) == len(cards)
    for identifier, to_state, from_states in lops.advanced:
        assert (to_state, from_states) == ("Intake", "Backlog"), (
            "the move is guarded on the lane it read, so a card that left "
            "Backlog mid-run is not dragged back"
        )
    assert {i for i, _b in lops.comments} == {c["identifier"] for c in cards}
    body = lops.comments[0][1]
    assert cutover.CUTOVER_TAG in body
    assert "Intake" in body


def test_a_limit_bounds_one_run_and_keeps_the_order():
    cards = _one_of_every_historically_exempt_class()
    lops = _Lops(cards)
    plan = cutover.plan(cards)
    result = cutover.run(lops, plan, apply=True, limit=2)
    assert [c["identifier"] for c in result["moved"]] == [
        c["identifier"] for c in plan["move"][:2]
    ]


def test_occupancy_is_recorded_before_and_after():
    """The amended criterion: Backlog occupancy immediately before and
    immediately after the cutover, on the card."""
    before = {"Backlog": 220, "Intake": 0}
    after = {"Backlog": 0, "Intake": 220}
    note = cutover.record_note(before, after, {"moved": [{"identifier": "DRE-2106"}],
                                               "batch_one": ["DRE-2101"],
                                               "in_flight": []})
    assert "220" in note and "0" in note
    assert "DRE-2101" in note, "the run records which cards were batch one"
    assert cutover.CUTOVER_TAG in note


def test_the_run_records_which_cards_were_batch_one():
    cards = _one_of_every_historically_exempt_class()
    plan = cutover.plan(cards)
    lops = _Lops(cards)
    result = cutover.run(lops, plan, apply=True)
    assert result["batch_one"] == ["DRE-2101"]


def test_the_cutover_never_writes_a_terminal_lane():
    """The groomer's rule, and for the same reason: this moves cards into
    Intake and nowhere else."""
    assert cutover.CUTOVER_TO == "Intake"
    assert cutover.CUTOVER_FROM == "Backlog"


def test_occupancy_counts_the_lanes_the_cutover_touches():
    population = [
        _card("DRE-2601"),
        _card("DRE-2602"),
        _card("DRE-2603", state="Intake"),
    ]
    lops = _Lops(population)
    counts = cutover.occupancy(lops)
    assert counts["Backlog"] == 2 and counts["Intake"] == 1


# --------------------------------------------------------------------------
# 5: --only — the cutover rehearsed on named cards, through the real path
# --------------------------------------------------------------------------
# DRE-3034: `--limit 1` takes whichever real card the plan puts first, so a
# throwaway probe placed in Backlog could not be moved alone and the front
# door's move-in path could not be observed before the real run. `--only`
# restricts the population to the cards named on the command line. Everything
# else is unchanged — the in-flight test, the reason posted BEFORE the move,
# the from-lane guard — and the occupancy record says plainly that it was a
# rehearsal, so it can never be read as the cutover's.


def _five() -> list[dict]:
    """Five ordinary Backlog cards, DRE-2700 the oldest, DRE-2704 the newest."""
    return [
        _card(f"DRE-27{i:02d}", created_minutes_ago=5000 - i * 100) for i in range(5)
    ]


def test_only_restricts_the_population_to_the_named_cards():
    """The card's test: a population of five, two named, exactly those two
    planned — in the run's own order, not the order they were typed."""
    cards = _five()
    result = cutover.plan(cards, only=["DRE-2701", "DRE-2703"])
    assert [c["identifier"] for c in result["move"]] == ["DRE-2703", "DRE-2701"]
    assert result["population"] == 2
    assert result["only"] == ["DRE-2701", "DRE-2703"]
    assert result["not_in_backlog"] == []


def test_without_only_the_whole_population_is_planned_as_before():
    """Guard the guard: the filter must be the reason the other three are
    absent above, not something else."""
    result = cutover.plan(_five())
    assert len(result["move"]) == 5
    assert result["only"] is None


def test_an_only_run_moves_exactly_the_named_cards_and_touches_nothing_else():
    cards = _five()
    lops = _Lops(cards)
    plan = cutover.plan(cards, only=["DRE-2700", "DRE-2704"])
    result = cutover.run(lops, plan, apply=True)
    assert [i for i, _t, _f in lops.advanced] == ["DRE-2704", "DRE-2700"]
    assert {i for i, _b in lops.comments} == {"DRE-2700", "DRE-2704"}
    assert [c["identifier"] for c in result["moved"]] == ["DRE-2704", "DRE-2700"]
    for identifier, to_state, from_states in lops.advanced:
        assert (to_state, from_states) == ("Intake", "Backlog"), (
            f"{identifier}: the from-lane guard holds on an --only run too"
        )
    for card in cards:
        expected = (
            "Intake" if card["identifier"] in {"DRE-2700", "DRE-2704"} else "Backlog"
        )
        assert card["state"]["name"] == expected


def test_the_reason_is_posted_before_the_move_on_an_only_run():
    """Unchanged by --only: the 🚚 reason is on the card whatever the move
    does next."""
    cards = _five()
    lops = _Lops(cards)
    cutover.run(lops, cutover.plan(cards, only=["DRE-2702"]), apply=True)
    identifier, body = lops.comments[0]
    assert (identifier, lops.advanced[0][0]) == ("DRE-2702", "DRE-2702")
    assert body.startswith(cutover.MARK)
    assert cutover.CUTOVER_TAG in body


def test_a_named_card_that_is_not_in_backlog_is_reported_and_never_moved():
    """It is reported as not being in Backlog and skipped — never moved from
    wherever it actually is."""
    elsewhere = _card("DRE-2801", state="Todo")
    lops = _Lops([*_five(), elsewhere])
    plan = cutover.plan(
        cutover.read_population(lops), only=["DRE-2701", "DRE-2801"]
    )
    assert plan["not_in_backlog"] == ["DRE-2801"]
    assert [c["identifier"] for c in plan["move"]] == ["DRE-2701"]
    cutover.run(lops, plan, apply=True)
    assert [i for i, _t, _f in lops.advanced] == ["DRE-2701"]
    assert elsewhere["state"]["name"] == "Todo"


def test_an_only_run_still_holds_an_in_flight_named_card_back_and_says_why():
    cards = _five()
    cards[1]["comments"]["nodes"].append(
        {"body": "⏳ 2/5 failing tests written", "createdAt": _iso(5)}
    )
    plan = cutover.plan(cards, only=["DRE-2701", "DRE-2703"])
    assert [c["identifier"] for c in plan["move"]] == ["DRE-2703"]
    assert plan["in_flight"][0]["identifier"] == "DRE-2701"
    assert "receipt" in plan["in_flight"][0]["why"]


def test_only_ids_are_normalised_and_deduped():
    """The operator types this at 2am; case and stray spaces are theirs to get
    wrong, and a card named twice is one card."""
    assert cutover.only_ids([" dre-2704 ", "DRE-2700", "DRE-2704"]) == [
        "DRE-2704", "DRE-2700",
    ]


def test_a_value_that_is_not_a_card_id_is_refused():
    """A typo must not be reported as 'not in Backlog', which would read as a
    fact about the board."""
    with pytest.raises(ValueError):
        cutover.only_ids(["not-a-card"])


# --- the record: a rehearsal is never mistaken for the cutover -------------
def _record(**overrides) -> str:
    result = {
        "moved": [{"identifier": "DRE-2700"}, {"identifier": "DRE-2704"}],
        "batch_one": [],
        "in_flight": [],
    }
    result.update(overrides)
    return cutover.record_note(
        {"Backlog": 220, "Intake": 0}, {"Backlog": 218, "Intake": 2}, result
    )


def test_the_record_for_an_only_run_says_rehearsal_and_names_the_cards():
    note = _record(only=["DRE-2700", "DRE-2704"], not_in_backlog=[])
    assert "rehearsal" in note.lower()
    assert "DRE-2700" in note and "DRE-2704" in note
    assert "220" in note and "218" in note


def test_the_rehearsal_record_cannot_be_mistaken_for_the_cutovers():
    """The acceptance criterion: an --only record must not read as the
    cutover's, and anything asking whether the cutover has run keeps getting
    the same answer."""
    rehearsal = _record(only=["DRE-2700"], not_in_backlog=[])
    assert cutover.CUTOVER_HEADLINE not in rehearsal
    assert "the cutover ran" not in rehearsal.lower()
    assert "the cutover has not run" in rehearsal.lower()


def test_the_full_cutover_record_is_unchanged():
    """The other half of the same guard: without --only this is still the
    cutover's own record, and it says nothing about a rehearsal."""
    note = _record(batch_one=["DRE-2101"])
    assert cutover.CUTOVER_HEADLINE in note
    assert "rehearsal" not in note.lower()


def test_the_rehearsal_record_names_a_card_that_was_not_in_backlog():
    note = _record(
        moved=[], only=["DRE-2801"], not_in_backlog=["DRE-2801"]
    )
    assert "DRE-2801" in note
    assert cutover.CUTOVER_FROM in note


# --- the CLI: the command the operator actually types ----------------------
def _cli(monkeypatch, lops):
    monkeypatch.setattr(cutover, "linear_ops", lops)
    monkeypatch.setattr(cutover, "open_pr_refs", lambda *a, **k: set())


def test_the_cli_rehearses_on_one_named_card_and_records_it(monkeypatch):
    """`run --apply --only DRE-<probe> --record DRE-N`: one card moves with the
    🚚 reason on it, nothing else is touched, and the record is a rehearsal."""
    cards = _five()
    lops = _Lops(cards)
    _cli(monkeypatch, lops)
    assert cutover.main(
        ["run", "--apply", "--only", "dre-2702", "--record", "DRE-3013"]
    ) == 0
    assert [i for i, _t, _f in lops.advanced] == ["DRE-2702"]
    reason = [b for i, b in lops.comments if i == "DRE-2702"]
    assert reason and reason[0].startswith(cutover.MARK)
    recorded = [b for i, b in lops.comments if i == "DRE-3013"]
    assert len(recorded) == 1
    assert "rehearsal" in recorded[0].lower() and "DRE-2702" in recorded[0]


def test_the_cli_plan_takes_only_and_writes_nothing(monkeypatch, capsys):
    lops = _Lops(_five())
    _cli(monkeypatch, lops)
    assert cutover.main(["plan", "--only", "DRE-2701", "DRE-2703"]) == 0
    out = capsys.readouterr().out
    assert "DRE-2703" in out and "DRE-2701" in out
    assert "DRE-2700" not in out
    assert lops.advanced == [] and lops.comments == []


def test_the_cli_refuses_a_value_that_is_not_a_card_id(monkeypatch):
    """And refuses it before it reads or writes anything."""
    lops = _Lops(_five())
    _cli(monkeypatch, lops)
    assert cutover.main(["run", "--apply", "--only", "nonsense"]) == 2
    assert lops.advanced == [] and lops.comments == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
