"""Readiness rules as data keep a card that is not ready off the Planning list
(DRE-6665).

The groomer's batch was the first twenty cards of the rules' order, and
nothing in the rules asked whether a card was ready to be planned. On
2026-10-10 a person removed five such cards from proposal `a4f198411b02` by
hand. This file pins the deterministic rule set that now runs before the batch
is cut, with no model call, and the "Not ready" list on the page that carries
the reason and the condition on every row.

What it pins, each a way the rules could quietly stop holding:

  * the replay of the recorded morning holds the cards a person removed, and
    keeps the ones they kept;
  * a Not ready card takes no slot, is listed once and in one list, and costs
    no verify call;
  * the Cancel list outranks Not ready, and Not ready outranks the read's
    decline;
  * every rule lets its card go the morning its condition holds;
  * the named cards are read in two bounded steps, and a read that fails holds
    the card rather than guessing;
  * the data file and the evaluators agree, and the page round-trips.

Run: cd bureau-pipeline && python3 -m pytest tests/test_groom_readiness.py -v
"""
from __future__ import annotations

import dataclasses
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import answer_into_card  # noqa: E402
import epic_cap  # noqa: E402
import groom_judgement  # noqa: E402
import groom_readiness  # noqa: E402
import groom_verify_agent  # noqa: E402
import groomer  # noqa: E402

from test_epic_cap import KNOWN_ANSWERED_WEIGHT, _node_weight  # noqa: E402
from test_groomer_population import CYCLES, card  # noqa: E402

V = groom_judgement.Verdict
NOW = "2026-09-20T12:00:00.000Z"
FIXTURE = ROOT / "tests" / "fixtures" / "groom-readiness-a4f198411b02.json"
DATA = ROOT / "config" / "groom-readiness.json"
DOC = ROOT / "docs" / "groomer.md"
BRIEF = ROOT / "briefs" / "groomer.md"

RULE_IDS = ("read-waits-on-open-card", "file-named-by-in-flight-card",
            "child-of-epic-not-in-progress", "undecided-question",
            "roll-up-parent")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _day(n: int) -> str:
    """A creation time `n` days before 2026-09-19 — a larger `n` is older."""
    return (datetime(2026, 9, 19, tzinfo=timezone.utc)
            - timedelta(days=n)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _pile(n: int, repo: str = "portico") -> list[dict]:
    """`n` cards, DRE-1 newest, so the rules' order is DRE-1, DRE-2, …"""
    return [card(f"DRE-{i}", repo=repo, created=_day(i)) for i in range(1, n + 1)]


def _under(c: dict, epic: str, state: str) -> dict:
    """`c` under `epic`, the parent selected as `POPULATION_QUERY` selects it."""
    c["parent"] = {"identifier": epic, "title": f"[EPIC] {epic}",
                   "state": {"name": state}}
    return c


def _kids(c: dict, *children) -> dict:
    """`c` with `children` — `(identifier, state, title)` each."""
    c["children"] = {"nodes": [{"identifier": i, "state": {"name": s},
                                "title": t} for i, s, t in children]}
    return c


def _lane_card(identifier: str, files=(), *, repo="portico",
               state="In Progress") -> dict:
    c = card(identifier, repo=repo,
             description=" ".join(f"`{f}`" for f in files))
    c["state"] = {"name": state}
    return c


def _light(identifier: str, state: str, *, title: str | None = None,
           child: bool = False) -> dict:
    """One record shaped as `NAMED_NODE` returns it."""
    return {"identifier": identifier,
            "title": title or f"{identifier} does a thing",
            "state": {"name": state},
            "children": {"nodes": [{"id": f"{identifier}-child"}] if child
                         else []}}


def _child(identifier: str, state: str, *, epic: bool = False,
           labels=()) -> dict:
    """One child as `epic_cap.IN_MOTION_NODE` returns it."""
    return {"identifier": identifier,
            "title": ("[EPIC] " if epic else "") + f"{identifier} work",
            "state": {"name": state},
            "labels": {"nodes": [{"name": n} for n in labels],
                       "pageInfo": {"hasNextPage": False}},
            "children": {"nodes": [{"id": "g"}] if epic else []}}


def _epic(identifier: str, children, *, state="In Progress",
          truncated=False) -> dict:
    """One record shaped as `epic_cap.IN_MOTION_NODE` returns it."""
    return {"identifier": identifier, "title": f"[EPIC] {identifier}",
            "priority": 0, "createdAt": _day(30), "state": {"name": state},
            "children": {"nodes": list(children),
                         "pageInfo": {"hasNextPage": truncated}}}


def _waits(depends: str, reason: str = "the read's reason") -> V:
    """A `now` verdict whose `depends on` line says `depends`."""
    return V("now", reason, None, {"why now": "it is time",
                                   "depends on": depends})


def _board(population=(), verdicts=None, *, lanes=None, named=None,
           epics=None) -> dict:
    return groom_readiness.board(
        groom_readiness.facts(lanes=lanes or {}, named=named or {},
                              epics=epics or {}),
        population=list(population), verdicts=verdicts)


def _rules(c: dict, board: dict) -> list[str]:
    return [row["rule"] for row in groom_readiness.evaluate(c, board)]


def _row(c: dict, board: dict, rule: str) -> dict:
    return next(r for r in groom_readiness.evaluate(c, board)
                if r["rule"] == rule)


def _propose(cards, *, readiness=None, capacity=20, judgement=None, **kw):
    return groomer.propose(cards, cycles=CYCLES, capacity=capacity, now=NOW,
                           judgement=judgement, readiness=readiness, **kw)


def _facts(**kw) -> dict:
    return groom_readiness.facts(lanes=kw.get("lanes") or {},
                                 named=kw.get("named") or {},
                                 epics=kw.get("epics") or {})


def _ids(rows) -> list[str]:
    return [r["identifier"] for r in rows]


QUESTION = ("## To decide before building\n\n"
            "* Whether the console keeps its own script.\n"
            "* Whether the website moves too.\n")


# --------------------------------------------------------------------------
# the replay of 2026-10-10 (proposal a4f198411b02)
# --------------------------------------------------------------------------
def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _replay(*, readiness: bool = True):
    """The recorded morning, proposed again over the board snapshot.

    The record carries the rules' order, not the cards' creation times, so
    each unit is given a creation day in that order — newest first — and each
    member a minute inside it, oldest child first: the order `sequence`
    computes back is the order recorded, which the first test asserts."""
    fx = _fixture()
    rec, board = fx["proposal"], fx["board"]
    moved = {c["identifier"] for lane in board["lanes"].values() for c in lane}
    rows = [r for r in rec["sequence"] if r["identifier"] not in moved]
    present = {r["identifier"] for r in rows}
    units: list[str] = []
    cards = []
    for r in rows:
        if r["unit"] not in units:
            units.append(r["unit"])
        k = units.index(r["unit"])
        minute = sum(1 for c in cards if c["_unit"] == r["unit"])
        created = (datetime(2026, 10, 9, tzinfo=timezone.utc)
                   - timedelta(days=k) + timedelta(minutes=minute))
        c = card(r["identifier"], repo=r["repo"], title=r["title"],
                 created=created.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                 description=board["bodies"].get(r["identifier"], ""),
                 priority={0: 1, 1: 2}.get(r["band"], 0))
        if r["repo"] == groomer.NO_REPO:
            c["labels"] = {"nodes": [{"name": "agent:engineer"}]}
        if r["epic"] and r["epic"] != r["identifier"]:
            c["parent"] = {"identifier": r["epic"], "title": f"[EPIC] {r['epic']}",
                           "state": {"name": "Intake" if r["epic"] in present
                                     else "Backlog"}}
        if r["identifier"] in board["children"]:
            c["children"] = {"nodes": board["children"][r["identifier"]]}
        c["_unit"] = r["unit"]
        cards.append(c)
    for c in cards:
        del c["_unit"]
    every = [{"identifier": r["identifier"]} for r in rec["sequence"]]
    verdicts = groom_judgement.parse(fx["answer"], every)
    for identifier, labels in fx["added"].items():
        v = verdicts[identifier]
        verdicts[identifier] = dataclasses.replace(
            v, reasons={**v.reasons, **labels})
    verdicts = {i: v for i, v in verdicts.items() if i in present}
    ids = {r["cycle"]: r["cycle_id"] for r in rec["sequence"] if r["cycle_id"]}
    cycles = [{"number": 15, "id": ids[15], "startsAt": "2026-10-12T07:00:00.000Z",
               "endsAt": "2026-10-26T07:00:00.000Z"},
              {"number": 16, "id": ids[16], "startsAt": "2026-10-26T07:00:00.000Z",
               "endsAt": "2026-11-09T07:00:00.000Z"}]
    facts = (groom_readiness.facts(lanes=board["lanes"], named=board["named"],
                                   epics=board["epics"]) if readiness else None)
    return groomer.propose(cards, cycles=cycles, capacity=rec["capacity"],
                           now=rec["generated_at"], judgement=verdicts,
                           held_cards=rec["held_before"], readiness=facts), rec


def test_the_replay_reproduces_the_recorded_order():
    proposal, rec = _replay(readiness=False)
    recorded = [r["identifier"] for r in rec["sequence"]
                if r["identifier"] != "DRE-3546"]
    assert _ids(proposal["sequence"]) == recorded


def test_the_replay_holds_the_cards_a_person_removed():
    proposal, _ = _replay()
    rows = proposal["outcomes"]["not-ready"]
    held = {(r["identifier"], r["rule"]) for r in rows}
    for expected in (("DRE-3602", "file-named-by-in-flight-card"),
                     ("DRE-4054", "read-waits-on-open-card"),
                     ("DRE-3857", "undecided-question"),
                     ("DRE-4072", "undecided-question"),
                     ("DRE-3749", "child-of-epic-not-in-progress"),
                     ("DRE-3750", "child-of-epic-not-in-progress")):
        assert expected in held, expected
    for row in rows:
        assert row["reason"].strip() and row["condition"].strip(), row
    by = {(r["identifier"], r["rule"]): r for r in rows}
    assert by[("DRE-3602", "file-named-by-in-flight-card")]["names"] == ["DRE-3546"]
    assert by[("DRE-3602", "file-named-by-in-flight-card")]["reason"] == (
        "names `release_row.py`, which DRE-3546 (In Progress) also names")
    assert by[("DRE-3602", "file-named-by-in-flight-card")]["condition"] == \
        "DRE-3546 is Done"
    rule1 = by[("DRE-4054", "read-waits-on-open-card")]
    assert rule1["names"] == ["DRE-6057"]
    assert rule1["reason"] == ("its ranked read waits on DRE-6057 (In Progress), "
                               "an epic with work left to build")
    assert rule1["condition"] == "DRE-6057 is built out or Done"
    for child in ("DRE-3749", "DRE-3750"):
        row = by[(child, "child-of-epic-not-in-progress")]
        assert row["names"] == ["DRE-2284"]
        assert row["reason"] == "its epic DRE-2284 is in Intake, not In Progress"
        assert row["condition"] == "DRE-2284 is In Progress"
    assert by[("DRE-3857", "undecided-question")]["reason"] == (
        'open question: "The console uses a pipeline access key and a '
        'two-phase cut …" (+1 more) under "To decide before building"')
    assert by[("DRE-4072", "undecided-question")]["reason"].endswith(
        '(+2 more) under "What to decide before building"')


def test_the_replay_keeps_the_epic_in_the_walk_and_the_cards_a_person_kept():
    proposal, _ = _replay()
    planning = _ids(proposal["outcomes"]["now"])
    not_ready = set(_ids(proposal["outcomes"]["not-ready"]))
    assert "DRE-2284" in planning, "the epic is offered as a unit of one"
    assert "DRE-2284" not in not_ready
    for kept in ("DRE-4415", "DRE-4391", "DRE-3833", "DRE-3832"):
        assert kept in planning, kept
    assert len(planning) == 20


def test_the_fixture_alters_one_fact_and_says_so():
    fx = _fixture()
    assert fx["added"] == {"DRE-4054": {"depends on": "DRE-6057"}}
    assert "one fact this fixture alters" in fx["_note"]
    assert "depends on: DRE-6057" in fx["_note"]
    assert fx["proposal"]["id"] == "a4f198411b02"
    epic = fx["board"]["epics"]["DRE-6057"]
    children = {c["identifier"]: c["state"]["name"]
                for c in epic["children"]["nodes"]}
    assert children == {"DRE-6059": "Done", "DRE-6061": "In Progress",
                        "DRE-6063": "In Progress", "DRE-6064": "In Progress",
                        "DRE-6105": "Backlog"}
    assert not any(epic_cap.buildable(c) for c in epic["children"]["nodes"])
    assert not groom_readiness.built_out(epic)


# --------------------------------------------------------------------------
# a Not ready card takes no slot
# --------------------------------------------------------------------------
def test_a_not_ready_card_takes_no_slot_and_the_next_card_fills_it():
    cards = _pile(21)
    cards[4]["description"] = QUESTION
    proposal = _propose(cards, readiness=_facts())
    planning = _ids(proposal["outcomes"]["now"])
    assert len(planning) == 20
    assert "DRE-5" not in planning
    assert planning[-1] == "DRE-21", "the twenty-first card filled the slot"
    assert _ids(proposal["outcomes"]["not-ready"]) == ["DRE-5"]


def test_a_card_the_in_flight_key_put_first_still_takes_no_slot():
    cards = _pile(21)
    oldest = _under(cards[-1], "DRE-90", "In Progress")
    oldest["description"] = QUESTION
    in_order = _ids(_propose(cards)["outcomes"]["now"])
    assert in_order[0] == "DRE-21", "the in-flight key placed it first"
    proposal = _propose(cards, readiness=_facts())
    assert "DRE-21" not in _ids(proposal["outcomes"]["now"])
    assert len(proposal["outcomes"]["now"]) == 20
    assert _ids(proposal["outcomes"]["not-ready"]) == ["DRE-21"]


# --------------------------------------------------------------------------
# one list, and only one
# --------------------------------------------------------------------------
def _one_not_ready_of_thirty():
    cards = _pile(30)
    cards[2]["description"] = QUESTION
    return _propose(cards, readiness=_facts(), capacity=10)


def test_a_not_ready_card_is_in_not_ready_and_no_other_list():
    proposal = _one_not_ready_of_thirty()
    for name in ("now", "not-now", "dead"):
        assert "DRE-3" not in _ids(proposal["outcomes"][name]), name
    assert _ids(proposal["outcomes"]["not-ready"]) == ["DRE-3"]
    seq = {r["identifier"]: r for r in proposal["sequence"]}["DRE-3"]
    assert seq["outcome"] == "not-ready"
    assert seq["cycle"] is None and seq["reconsidered_in"] is None


def test_the_page_lists_it_once_under_its_own_heading():
    proposal = _one_not_ready_of_thirty()
    text = groomer.render_proposal(proposal)
    assert text.count(groomer.NOT_READY_HEADING) == 1
    section = groomer._section(text, groomer.NOT_READY_HEADING)
    assert any("| DRE-3 |" in line for line in section)
    not_now = groomer._section(text, "## Not now — and when to come back")
    assert not_now, "the deferred cards are still listed"
    assert not any(re.search(r"\bDRE-3\b", line) for line in not_now)
    later = len(proposal["outcomes"]["not-now"])
    assert f"{later} cards are **not now**" in text
    assert later == 30 - 10 - 1
    assert sum(1 for line in text.splitlines()
               if re.match(r"^\|\s*\d+\s*\|\s*DRE-3\s*\|", line)) == 1


def test_annotate_leaves_a_not_ready_row_untouched():
    proposal = _one_not_ready_of_thirty()
    before = json.loads(json.dumps(proposal["outcomes"]["not-ready"]))
    groomer._annotate(proposal, None, None)
    assert proposal["outcomes"]["not-ready"] == before


def test_the_verify_step_never_targets_a_not_ready_card():
    class NoViewer:
        def viewer_id(self):
            return None

    proposal = _one_not_ready_of_thirty()
    targets = groom_verify_agent.targets(proposal, lops=NoViewer(), repo_map={})
    named = [t["card"] for t in targets]
    assert named, "the window still lists the Planning list and its spares"
    assert "DRE-3" not in named


def test_a_declined_card_that_trips_a_rule_is_not_ready_and_not_unranked():
    cards = _pile(5)
    cards[1]["description"] = QUESTION
    verdicts = {c["identifier"]: V("now", "fine") for c in cards}
    verdicts["DRE-2"] = V("unranked", groom_judgement.UNRANKED_REASON)
    proposal = _propose(cards, readiness=_facts(), judgement=verdicts)
    assert _ids(proposal["outcomes"]["not-ready"]) == ["DRE-2"]
    assert "DRE-2" not in _ids(proposal["outcomes"]["not-now"])
    assert "DRE-2" not in proposal["judgement"]["unranked"]
    assert "## Could not rank" not in groomer.render_proposal(proposal)


def test_assert_disjoint_refuses_one_card_in_not_ready_and_not_now():
    proposal = _one_not_ready_of_thirty()
    proposal["outcomes"]["not-now"].append(
        dict(proposal["outcomes"]["not-ready"][0]))
    with pytest.raises(groomer.ProposalContradiction):
        groomer.assert_disjoint(proposal)


def test_the_id_does_not_digest_the_not_ready_list():
    proposal = _one_not_ready_of_thirty()
    bare = {**proposal, "outcomes": {**proposal["outcomes"], "not-ready": []}}
    assert groomer.proposal_id(bare) == proposal["id"]


# --------------------------------------------------------------------------
# the Cancel list outranks Not ready
# --------------------------------------------------------------------------
def _in_flight_lane():
    return {"In Progress": [_lane_card("DRE-80", ["shared.py"])]}


def test_a_superseded_card_naming_an_in_flight_file_goes_on_the_cancel_list():
    cards = _pile(3)
    cards[0]["description"] = "Superseded by: DRE-70\n\nEdits `shared.py`."
    proposal = _propose(cards, readiness=_facts(lanes=_in_flight_lane()))
    assert _ids(proposal["outcomes"]["dead"]) == ["DRE-1"]
    assert proposal["outcomes"]["not-ready"] == []


def test_without_the_line_it_is_not_ready_naming_the_in_flight_card():
    cards = _pile(3)
    cards[0]["description"] = "Edits `shared.py`."
    proposal = _propose(cards, readiness=_facts(lanes=_in_flight_lane()))
    assert proposal["outcomes"]["dead"] == []
    row = proposal["outcomes"]["not-ready"][0]
    assert (row["identifier"], row["rule"], row["names"]) == (
        "DRE-1", "file-named-by-in-flight-card", ["DRE-80"])


def test_a_cancel_the_check_kept_is_evaluated_for_readiness():
    cards = _pile(3)
    cards[0]["description"] = "Superseded by: DRE-70\n\nEdits `shared.py`."
    proposal = _propose(cards, readiness=_facts(lanes=_in_flight_lane()),
                        verified={"keep": {"DRE-1": "DRE-70 is not done"}})
    assert proposal["outcomes"]["dead"] == []
    assert _ids(proposal["outcomes"]["not-ready"]) == ["DRE-1"]


def test_an_epic_the_refusal_kept_off_the_cancel_list_is_evaluated():
    cards = _pile(3)
    cards[0]["description"] = "Superseded by: DRE-70\n\nEdits `shared.py`."
    _kids(cards[0], ("DRE-71", "Todo", "a child"))
    proposal = _propose(cards, readiness=_facts(lanes=_in_flight_lane()))
    assert proposal["outcomes"]["dead"] == []
    assert _ids(proposal["outcomes"]["not-ready"]) == ["DRE-1"]


# --------------------------------------------------------------------------
# each rule lets its card go the morning its condition holds
# --------------------------------------------------------------------------
def test_rule_one_returns_the_card_once_the_named_card_is_done():
    c = card("DRE-1")
    verdicts = {"DRE-1": _waits("DRE-60")}
    held = _board([c], verdicts, named={"DRE-60": _light("DRE-60", "Todo")})
    assert _rules(c, held) == ["read-waits-on-open-card"]
    assert _row(c, held, "read-waits-on-open-card")["condition"] == \
        "DRE-60 is Done"
    done = _board([c], verdicts, named={"DRE-60": _light("DRE-60", "Done")})
    assert _rules(c, done) == []


def test_rule_one_returns_the_card_once_the_named_epic_is_built_out():
    c = card("DRE-1")
    verdicts = {"DRE-1": _waits("DRE-60")}
    named = {"DRE-60": _light("DRE-60", "In Progress", child=True)}
    left = _epic("DRE-60", [_child("DRE-61", "Todo")])
    built = _epic("DRE-60", [_child("DRE-61", "Done"),
                             _child("DRE-62", "Backlog", labels=("no-code",))])
    assert _rules(c, _board([c], verdicts, named=named,
                            epics={"DRE-60": left})) == ["read-waits-on-open-card"]
    assert _rules(c, _board([c], verdicts, named=named,
                            epics={"DRE-60": built})) == []


def test_rule_two_returns_the_card_once_the_in_flight_card_is_done():
    c = card("DRE-1", description="Edits `shared.py`.")
    held = _board([c], lanes={"In Progress": [_lane_card("DRE-80", ["shared.py"])]})
    assert _rules(c, held) == ["file-named-by-in-flight-card"]
    gone = _board([c], lanes={"In Progress": []})
    assert _rules(c, gone) == []


def test_rule_three_returns_the_child_once_its_epic_is_in_progress():
    c = _under(card("DRE-1"), "DRE-50", "Backlog")
    assert _rules(c, _board([c])) == ["child-of-epic-not-in-progress"]
    c = _under(card("DRE-1"), "DRE-50", "In Progress")
    assert _rules(c, _board([c])) == []


def test_rule_four_returns_the_card_once_the_heading_is_removed():
    c = card("DRE-1", description="Intro.\n\n" + QUESTION)
    assert _rules(c, _board([c])) == ["undecided-question"]
    c = card("DRE-1", description="Intro.\n\n* Whether the console keeps it.")
    assert _rules(c, _board([c])) == []


def test_rule_five_returns_the_card_once_no_child_is_an_epic():
    c = _kids(card("DRE-1"), ("DRE-2", "Todo", "[EPIC] a child epic"))
    assert _rules(c, _board([c])) == ["roll-up-parent"]
    assert _row(c, _board([c]), "roll-up-parent")["names"] == ["DRE-2"]
    c = _kids(card("DRE-1"), ("DRE-2", "Todo", "a child card"))
    assert _rules(c, _board([c])) == []


def test_nothing_is_remembered_between_mornings():
    """The same card, two boards: the second answer depends on the second
    board alone, whatever the first one said."""
    c = card("DRE-1", description=QUESTION)
    first = _propose([c], readiness=_facts())
    assert _ids(first["outcomes"]["not-ready"]) == ["DRE-1"]
    c = card("DRE-1", description="settled")
    second = _propose([c], readiness=_facts())
    assert second["outcomes"]["not-ready"] == []
    assert _ids(second["outcomes"]["now"]) == ["DRE-1"]


# --------------------------------------------------------------------------
# rule 1 reads an epic the way the sweep's epic gate does
# --------------------------------------------------------------------------
@pytest.mark.parametrize("children,truncated", [
    ([_child("DRE-61", "Todo")], False),                       # buildable
    ([_child("DRE-61", "Backlog", epic=True)], False),         # open child epic
    ([_child("DRE-61", "Done")], True),                        # page not read
])
def test_a_line_naming_an_epic_with_work_left_holds_the_card(children, truncated):
    c = card("DRE-1")
    board = _board([c], {"DRE-1": _waits("wait for DRE-60 to land")},
                   named={"DRE-60": _light("DRE-60", "In Progress", child=True)},
                   epics={"DRE-60": _epic("DRE-60", children,
                                          truncated=truncated)})
    row = _row(c, board, "read-waits-on-open-card")
    assert row["names"] == ["DRE-60"]
    assert row["reason"] == ("its ranked read waits on DRE-60 (In Progress), "
                             "an epic with work left to build")
    assert row["condition"] == "DRE-60 is built out or Done"


def test_an_epic_not_yet_in_progress_holds_the_card_with_no_second_read():
    c = card("DRE-1")
    board = _board([c], {"DRE-1": _waits("DRE-60")},
                   named={"DRE-60": _light("DRE-60", "Backlog",
                                           title="[EPIC] the next thing")})
    row = _row(c, board, "read-waits-on-open-card")
    assert row["condition"] == "DRE-60 is built out or Done"


def test_built_out_reads_exactly_as_the_sweeps_epic_gate():
    import reconcile

    cases = {
        "DRE-60": _epic("DRE-60", [_child("DRE-61", "Todo")]),
        "DRE-61": _epic("DRE-61", [_child("DRE-62", "Done")]),
        "DRE-62": _epic("DRE-62", [_child("DRE-63", "Backlog", epic=True)]),
        "DRE-63": _epic("DRE-63", [_child("DRE-64", "Done")], truncated=True),
        "DRE-64": _epic("DRE-64", []),
        "DRE-65": _epic("DRE-65", [_child("DRE-66", "Todo",
                                          labels=("no-code",))]),
    }
    for identifier, record in cases.items():
        reconcile._blocker_epics[identifier] = record
        try:
            gate = reconcile._not_built_out(identifier, "In Progress") is None
        finally:
            reconcile._blocker_epics.pop(identifier, None)
        assert groom_readiness.built_out(record) is gate, identifier


def test_the_fixture_epic_reads_the_same_through_the_sweeps_gate():
    import reconcile

    record = _fixture()["board"]["epics"]["DRE-6057"]
    reconcile._blocker_epics["DRE-6057"] = record
    try:
        assert reconcile._not_built_out("DRE-6057", "In Progress") is not None
    finally:
        reconcile._blocker_epics.pop("DRE-6057", None)
    assert groom_readiness.built_out(record) is False


def test_a_line_naming_a_built_out_or_done_epic_does_not_hold():
    c = card("DRE-1")
    named = {"DRE-60": _light("DRE-60", "In Progress", child=True)}
    built = _epic("DRE-60", [_child("DRE-61", "Done")])
    assert not epic_cap.counts_against_cap(built, count_rollup_parents=True)
    assert _rules(c, _board([c], {"DRE-1": _waits("DRE-60")}, named=named,
                            epics={"DRE-60": built})) == []
    done = {"DRE-60": _light("DRE-60", "Done", child=True)}
    assert _rules(c, _board([c], {"DRE-1": _waits("DRE-60")}, named=done)) == []


def test_a_child_whose_line_names_its_own_parent_is_ordered_in_flight():
    child = _under(card("DRE-1", created=_day(9)), "DRE-50", "In Progress")
    other = card("DRE-2", created=_day(1))
    verdicts = {"DRE-1": _waits("DRE-50"), "DRE-2": _waits("nothing")}
    facts = _facts(named={"DRE-50": _light("DRE-50", "In Progress", child=True)},
                   epics={"DRE-50": _epic("DRE-50", [_child("DRE-1", "Intake")])})
    assert _rules(child, groom_readiness.board(facts, population=[child, other],
                                               verdicts=verdicts)) == []
    assert groom_readiness.named_ids([child, other], verdicts) == []
    proposal = _propose([child, other], readiness=facts, judgement=verdicts)
    assert _ids(proposal["outcomes"]["now"]) == ["DRE-1", "DRE-2"]
    assert proposal["outcomes"]["now"][0]["serves"] == "DRE-50"


def test_a_line_naming_an_open_sibling_holds_until_the_sibling_is_done():
    child = _under(card("DRE-1"), "DRE-50", "In Progress")
    board = _board([child], {"DRE-1": _waits("DRE-51")},
                   named={"DRE-51": _light("DRE-51", "In Review")})
    row = _row(child, board, "read-waits-on-open-card")
    assert row["reason"] == "its ranked read waits on DRE-51 (In Review)"
    assert row["condition"] == "DRE-51 is Done"


def test_a_line_naming_a_canceled_card_does_not_hold():
    c = card("DRE-1")
    board = _board([c], {"DRE-1": _waits("DRE-60")},
                   named={"DRE-60": _light("DRE-60", "Canceled")})
    assert _rules(c, board) == []


def test_nothing_on_the_line_holds_nothing():
    c = card("DRE-1")
    assert _rules(c, _board([c], {"DRE-1": _waits("nothing")})) == []
    assert _rules(c, _board([c], {"DRE-1": V("now", "fine")})) == []


# --------------------------------------------------------------------------
# the named cards are read in two bounded steps
# --------------------------------------------------------------------------
class Reads:
    """Answers the light read and the epic read off `records`, recording
    every request. `fail` raises for a light request naming the id; `missing`
    returns no issue for it; `epic_fail` and `epic_missing` do the same for
    the epic read."""

    def __init__(self, records=None, epics=None, *, fail=(), missing=(),
                 epic_fail=(), epic_missing=()):
        self.records = records or {}
        self.epics = epics or {}
        self.fail, self.missing = set(fail), set(missing)
        self.epic_fail, self.epic_missing = set(epic_fail), set(epic_missing)
        self.light, self.heavy = [], []

    def gql(self, query, variables=None):
        variables = variables or {}
        if epic_cap.IN_MOTION_NODE in query:
            self.heavy.append((query, variables))
            identifier = variables["id"]
            if identifier in self.epic_fail:
                raise RuntimeError("linear error: the epic read failed")
            if identifier in self.epic_missing:
                return {"issue": None}
            return {"issue": self.epics.get(identifier)}
        self.light.append((query, variables))
        if self.fail & set(variables.values()):
            raise RuntimeError("linear error: the light read failed")
        out = {}
        for alias, identifier in variables.items():
            out[alias] = (None if identifier in self.missing else
                          self.records.get(identifier,
                                           _light(identifier, "Done")))
        return out


def _named_by(n: int):
    """`n` cards, each naming its own card on its `depends on` line."""
    cards = [card(f"DRE-{i}") for i in range(1, n + 1)]
    verdicts = {c["identifier"]: _waits(f"DRE-{1000 + i}")
                for i, c in enumerate(cards, 1)}
    return cards, verdicts


def test_120_named_ids_cost_three_light_requests_and_no_epic_read():
    cards, verdicts = _named_by(120)
    ids = groom_readiness.named_ids(cards, verdicts)
    assert len(ids) == 120
    lops = Reads()
    named = groom_readiness.read_named(lops, ids)
    epics = groom_readiness.read_epics(lops, named)
    assert [len(v) for _, v in lops.light] == [50, 50, 20]
    assert groom_readiness.NAMED_PAGE == 50
    node = " ".join(groom_readiness.NAMED_NODE.split())
    for query, variables in lops.light:
        flat = " ".join(query.split())
        assert " ".join(epic_cap.IN_MOTION_NODE.split()) not in flat
        assert "labels" not in flat and "history" not in flat
        aliases = re.findall(r"\bn\d+: issue\(id: \$i\d+\) \{ (.*?) \}(?= n\d+:| \}$)",
                             flat)
        assert len(aliases) == len(variables)
        assert all(selection == node for selection in aliases), aliases[:1]
    assert lops.heavy == [] and epics == {}


def test_an_in_progress_epic_costs_one_epic_read_however_many_name_it():
    cards = [card(f"DRE-{i}") for i in range(1, 6)]
    verdicts = {c["identifier"]: _waits("DRE-60") for c in cards}
    lops = Reads({"DRE-60": _light("DRE-60", "In Progress", child=True)},
                 {"DRE-60": _epic("DRE-60", [_child("DRE-61", "Todo")])})
    facts = groom_readiness.read(lops, cards, verdicts, lanes={})
    assert len(lops.light) == 1
    assert len(lops.heavy) == 1
    query, variables = lops.heavy[0]
    assert variables == {"id": "DRE-60"}
    assert "issue(id: $id)" in query and epic_cap.IN_MOTION_NODE in query
    board = groom_readiness.board(facts, population=cards, verdicts=verdicts)
    assert all(_rules(c, board) == ["read-waits-on-open-card"] for c in cards)


def test_a_named_card_with_no_child_or_not_in_progress_costs_no_second_read():
    cards = [card("DRE-1"), card("DRE-2"), card("DRE-3")]
    verdicts = {"DRE-1": _waits("DRE-60"), "DRE-2": _waits("DRE-61"),
                "DRE-3": _waits("DRE-62")}
    lops = Reads({"DRE-60": _light("DRE-60", "In Progress"),
                  "DRE-61": _light("DRE-61", "Backlog", child=True),
                  "DRE-62": _light("DRE-62", "Done", child=True)})
    groom_readiness.read(lops, cards, verdicts, lanes={})
    assert len(lops.light) == 1 and lops.heavy == []


def test_the_light_alias_weighs_little_against_the_yardstick():
    node = groom_readiness.NAMED_NODE
    assert " ".join(node.split()) == (
        "identifier title state { name } children(first: 1) { nodes { id } }")
    # Recomputed off the selection's own `first:`: the one child probe.
    assert _node_weight(node) == 1
    # The issue, its state object, the children connection and the probe.
    alias = 1 + node.count("{")
    assert alias <= 4
    request = groom_readiness.named_query(groom_readiness.NAMED_PAGE)
    assert _node_weight(request) == groom_readiness.NAMED_PAGE
    assert groom_readiness.NAMED_PAGE * alias <= KNOWN_ANSWERED_WEIGHT


def test_the_epic_read_carries_in_motion_node_as_the_sweep_does():
    assert epic_cap.IN_MOTION_NODE in groom_readiness.EPIC_QUERY
    assert "issue(id: $id)" in groom_readiness.EPIC_QUERY
    assert groom_readiness.EPIC_QUERY.count("issue(") == 1


# --------------------------------------------------------------------------
# a read that fails holds the card
# --------------------------------------------------------------------------
def _unread_row(lops, *, line="DRE-60"):
    c = card("DRE-1")
    verdicts = {"DRE-1": _waits(line)}
    facts = groom_readiness.read(lops, [c], verdicts, lanes={})
    proposal = _propose([c, card("DRE-2")], readiness=facts, judgement=verdicts)
    rows = proposal["outcomes"]["not-ready"]
    assert _ids(rows) == ["DRE-1"], rows
    groomer.proposal_comment(proposal)        # still renders, still posts
    return rows[0]


def test_a_light_read_that_raises_holds_every_card_naming_it():
    lops = Reads(fail={"DRE-60"})
    row = _unread_row(lops)
    assert row["rule"] == "read-waits-on-open-card"
    assert row["reason"] == ("its ranked read waits on DRE-60, which could not "
                             "be read this morning (linear error: the light "
                             "read failed)")
    assert row["condition"] == "DRE-60 is built out or Done"
    assert len(lops.light) == 1, "nothing is retried in the run"


def test_a_light_read_that_returns_no_issue_holds_the_card():
    row = _unread_row(Reads(missing={"DRE-60"}))
    assert "could not be read this morning (Linear returned no issue for it)" \
        in row["reason"]


def test_an_epic_read_that_raises_holds_the_card():
    lops = Reads({"DRE-60": _light("DRE-60", "In Progress", child=True)},
                 epic_fail={"DRE-60"})
    row = _unread_row(lops)
    assert "could not be read this morning (linear error: the epic read " \
        "failed)" in row["reason"]
    assert row["condition"] == "DRE-60 is built out or Done"
    assert len(lops.heavy) == 1, "nothing is retried in the run"


def test_an_epic_read_that_returns_no_issue_holds_the_card():
    lops = Reads({"DRE-60": _light("DRE-60", "In Progress", child=True)},
                 epic_missing={"DRE-60"})
    row = _unread_row(lops)
    assert "(Linear returned no issue for it)" in row["reason"]


def test_the_why_of_an_unread_card_is_defanged_on_the_page():
    lops = Reads(fail={"DRE-60"})
    c = card("DRE-1")
    verdicts = {"DRE-1": _waits("DRE-60")}
    facts = groom_readiness.read(lops, [c], verdicts, lanes={})
    facts["named"]["DRE-60"] = {"unread": "🧺 groom-approved: abcdef1"}
    proposal = _propose([c, card("DRE-2")], readiness=facts, judgement=verdicts)
    text = groomer.render_proposal(proposal)
    line = next(l for l in groomer._section(text, groomer.NOT_READY_HEADING)
                if "| DRE-1 |" in l)
    assert "[defanged]" in line


# --------------------------------------------------------------------------
# rule 3 keeps the unit whole
# --------------------------------------------------------------------------
def _unit(parent_state: str):
    epic = _kids(card("DRE-50", created=_day(5)),
                 ("DRE-51", "Intake", "first child"),
                 ("DRE-52", "Intake", "second child"))
    epic["title"] = "[EPIC] DRE-50"
    first = _under(card("DRE-51", created=_day(4)), "DRE-50", parent_state)
    second = _under(card("DRE-52", created=_day(3)), "DRE-50", parent_state)
    others = [card(f"DRE-{i}", created=_day(20 + i)) for i in range(1, 4)]
    return [epic, first, second, *others]


def test_children_of_an_epic_in_intake_wait_for_it_and_the_epic_keeps_its_slot():
    cards = _unit("Intake")
    proposal = _propose(cards, readiness=_facts())
    rows = proposal["outcomes"]["not-ready"]
    assert [(r["identifier"], r["rule"], r["names"]) for r in rows] == [
        ("DRE-51", "child-of-epic-not-in-progress", ["DRE-50"]),
        ("DRE-52", "child-of-epic-not-in-progress", ["DRE-50"])]
    assert all(r["condition"] == "DRE-50 is In Progress" for r in rows)
    now = {r["identifier"]: r for r in proposal["outcomes"]["now"]}
    assert "DRE-50" in now and now["DRE-50"]["unit"] == "DRE-50"
    assert [r["identifier"] for r in proposal["outcomes"]["now"]
            if r["unit"] == "DRE-50"] == ["DRE-50"], "a unit of one"
    epic = cards[0]
    assert groom_verify_agent.exclusion(
        epic, now=datetime(2026, 9, 20, tzinfo=timezone.utc)) == \
        "parent epic with 2 open children"


def test_children_of_an_epic_in_progress_are_ordered_in_flight():
    cards = _unit("In Progress")
    proposal = _propose(cards, readiness=_facts())
    assert proposal["outcomes"]["not-ready"] == []
    now = {r["identifier"]: r for r in proposal["outcomes"]["now"]}
    assert now["DRE-51"]["serves"] == "DRE-50"
    assert now["DRE-52"]["serves"] == "DRE-50"


def test_children_of_an_epic_in_backlog_wait_for_it():
    cards = _unit("Backlog")
    proposal = _propose(cards, readiness=_facts())
    assert [(r["identifier"], r["names"]) for r in
            proposal["outcomes"]["not-ready"]] == [("DRE-51", ["DRE-50"]),
                                                   ("DRE-52", ["DRE-50"])]


@pytest.mark.parametrize("state", ["Done", "Canceled", "Duplicate"])
def test_a_closed_parent_is_not_this_rule(state):
    c = _under(card("DRE-1"), "DRE-50", state)
    assert _rules(c, _board([c])) == []


# --------------------------------------------------------------------------
# rule 4 — an undecided question, quoted and sent nowhere
# --------------------------------------------------------------------------
def test_the_question_is_quoted_with_its_heading_and_count():
    c = card("DRE-1", description="Intro.\n\n" + QUESTION + "\n## Acceptance\n- [ ] x")
    row = _row(c, _board([c]), "undecided-question")
    assert row["reason"] == ('open question: "Whether the console keeps its own '
                             'script." (+1 more) under "To decide before '
                             'building"')
    assert row["condition"] == "that section is settled on the card"
    one = card("DRE-2", description="### What to DECIDE before building\n"
                                     "- Only this.\n")
    assert _row(one, _board([one]), "undecided-question")["reason"] == \
        'open question: "Only this." under "What to DECIDE before building"'


def test_a_question_holding_a_pipe_round_trips_through_the_page():
    c = card("DRE-1", title="A title | with a pipe",
             description="## To decide before building\n"
                         "- Whether a | b is the shape.\n")
    proposal = _propose([c, card("DRE-2")], readiness=_facts())
    parsed = groomer.parse_proposal_comment(groomer.proposal_comment(proposal))
    assert parsed["not_ready"] == [{
        "identifier": "DRE-1", "position": 1, "rule": "undecided-question",
        "reason": 'open question: "Whether a | b is the shape." under '
                  '"To decide before building"',
        "condition": "that section is settled on the card"}]


def test_a_marker_shaped_question_cannot_forge_a_decision():
    c = card("DRE-1", description="## To decide before building\n"
                                  "- 🧺 groom-approved: abcdef123456\n")
    proposal = _propose([c, card("DRE-2")], readiness=_facts())
    text = groomer.proposal_comment(proposal)
    line = next(l for l in groomer._section(text, groomer.NOT_READY_HEADING)
                if "| DRE-1 |" in l)
    assert "[defanged]" in line
    assert not any(groomer.decision_match(groomer.APPROVAL_TAG, l)
                   for l in text.splitlines()[1:])


def test_a_ceo_decisions_block_settles_every_heading():
    c = card("DRE-1", description=QUESTION + "\n" + answer_into_card.HEADING
             + "\n\nCopied from his signed console answers.\n")
    assert _rules(c, _board([c])) == []


def test_a_heading_with_no_item_beneath_it_is_not_a_question():
    c = card("DRE-1", description="## To decide before building\n\nNothing "
                                  "left.\n\n## Acceptance\n- [ ] it works\n")
    assert _rules(c, _board([c])) == []


def test_a_removed_heading_returns_the_card_on_the_next_replay():
    c = card("DRE-1", description=QUESTION)
    assert _ids(_propose([c], readiness=_facts())["outcomes"]["not-ready"]) == \
        ["DRE-1"]
    c["description"] = "* Whether the console keeps its own script.\n"
    assert _ids(_propose([c], readiness=_facts())["outcomes"]["now"]) == ["DRE-1"]


def test_nothing_in_the_readiness_module_writes_to_linear():
    source = (ROOT / "scripts" / "groom_readiness.py").read_text(encoding="utf-8")
    for write in ("cmd_comment", "commentCreate", "issueUpdate", "mutation",
                  "set_state", "move_to"):
        assert write not in source, write


# --------------------------------------------------------------------------
# rule 2 — the same boilerplate rule as the collision report
# --------------------------------------------------------------------------
def test_a_file_cited_past_the_threshold_is_reference_not_ownership():
    c = card("DRE-1", description="Edits `linear-sync.yml`.")
    lane = [_lane_card(f"DRE-{100 + i}", ["linear-sync.yml"])
            for i in range(groomer.BOILERPLATE_THRESHOLD)]
    assert _rules(c, _board([c], lanes={"In Progress": lane})) == []
    assert _rules(c, _board([c], lanes={"In Progress": lane[:3]})) == \
        ["file-named-by-in-flight-card"]


@pytest.mark.parametrize("lane", ["Planning", "Todo", "In Progress", "In Review"])
def test_every_in_flight_lane_is_read(lane):
    c = card("DRE-1", description="Edits `shared.py`.")
    other = _lane_card("DRE-80", ["shared.py"], state=lane)
    row = _row(c, _board([c], lanes={lane: [other]}),
               "file-named-by-in-flight-card")
    assert row["reason"] == f"names `shared.py`, which DRE-80 ({lane}) also names"


def test_another_intake_card_naming_the_file_is_not_this_rule():
    a = card("DRE-1", description="Edits `shared.py`.")
    b = card("DRE-2", description="Edits `shared.py`.")
    assert _rules(a, _board([a, b])) == []


# --------------------------------------------------------------------------
# the data file and the check
# --------------------------------------------------------------------------
def test_the_data_file_carries_the_five_evaluated_rules_and_the_parked_repo():
    rules = groom_readiness.load()
    ids = [r["id"] for r in rules]
    assert ids[:5] == list(RULE_IDS)
    assert len(ids) == 6
    parked = rules[5]
    assert parked["applied_by"] == "held_repo_rows"
    assert all(r["applied_by"] == "groom_readiness" for r in rules[:5])
    assert "leans-on-closed-card" not in ids
    for r in rules:
        assert r["reason"].strip() and r["condition"].strip(), r["id"]


def _check(path: Path | None = None) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(ROOT / "scripts" / "groom_readiness.py"), "check"]
    if path:
        cmd += ["--rules", str(path)]
    return subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)


def test_the_check_passes_on_main():
    done = _check()
    assert done.returncode == 0, done.stdout + done.stderr


def test_the_check_fails_on_a_rule_with_no_evaluator(tmp_path):
    doc = json.loads(DATA.read_text(encoding="utf-8"))
    doc["rules"].append({"id": "leans-on-closed-card",
                         "applied_by": "groom_readiness",
                         "reason": "x", "condition": "y"})
    path = tmp_path / "rules.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    done = _check(path)
    assert done.returncode == 1
    assert "leans-on-closed-card" in done.stdout + done.stderr


def test_the_check_fails_on_an_evaluator_with_no_record(tmp_path):
    doc = json.loads(DATA.read_text(encoding="utf-8"))
    doc["rules"] = [r for r in doc["rules"] if r["id"] != "roll-up-parent"]
    path = tmp_path / "rules.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    done = _check(path)
    assert done.returncode == 1
    assert "roll-up-parent" in done.stdout + done.stderr


# --------------------------------------------------------------------------
# the page
# --------------------------------------------------------------------------
def test_the_section_heading_and_columns_are_the_contract():
    assert groomer.NOT_READY_HEADING == "## Not ready — and what would make it ready"
    assert groomer.NOT_READY_COLUMNS == \
        "| # | Card | Rule | Repo | Epic | Title | Reason | Ready when |"


def test_the_section_sits_after_the_cancel_list_and_before_verified():
    cards = _pile(4)
    cards[0]["description"] = "Superseded by: DRE-70"
    cards[1]["description"] = QUESTION
    proposal = _propose(cards, readiness=_facts())
    proposal["verify"] = {"counts": {}, "cost_usd": None,
                          "wall_clock_seconds": None}
    text = groomer.render_proposal(proposal)
    assert text.index(groomer.CANCEL_HEADING) < \
        text.index(groomer.NOT_READY_HEADING) < \
        text.index(groomer.VERIFIED_HEADING)


def test_render_and_parse_round_trip_the_table_with_a_piped_title():
    cards = _pile(4)
    cards[1]["title"] = "One | two"
    cards[1]["description"] = QUESTION
    cards[2] = _kids(cards[2], ("DRE-9", "Todo", "[EPIC] a child epic"))
    proposal = _propose(cards, readiness=_facts())
    parsed = groomer.parse_proposal_comment(groomer.proposal_comment(proposal))
    assert [(r["identifier"], r["position"], r["rule"])
            for r in parsed["not_ready"]] == [
        ("DRE-2", 1, "undecided-question"), ("DRE-3", 2, "roll-up-parent")]
    for got, row in zip(parsed["not_ready"], proposal["outcomes"]["not-ready"]):
        assert got["reason"] == row["reason"]
        assert got["condition"] == row["condition"]


def test_no_not_ready_row_renders_byte_for_byte_as_before():
    cards = _pile(25)
    before = groomer.render_proposal(_propose(cards))
    after = _propose(cards, readiness=_facts())
    assert after["outcomes"]["not-ready"] == []
    assert groomer.render_proposal(after) == before
    assert groomer.NOT_READY_HEADING not in before
    assert groomer.parse_proposal_comment(
        groomer.proposal_comment(after))["not_ready"] == []


def test_a_proposal_with_no_readiness_has_no_fourth_list():
    proposal = _propose(_pile(3))
    assert "not-ready" not in proposal["outcomes"]


# --------------------------------------------------------------------------
# depends_on_missing — the diagnostic of how much of the read rule 1 sees
# --------------------------------------------------------------------------
def test_the_judgement_block_counts_now_verdicts_with_no_depends_on_line():
    cards = _pile(4)
    verdicts = {"DRE-1": _waits("nothing"), "DRE-2": V("now", "fine"),
                "DRE-3": V("now", "fine", None, {"value": "some"}),
                "DRE-4": V("not-now", "later", "when X lands")}
    proposal = _propose(cards, readiness=_facts(), judgement=verdicts)
    assert proposal["judgement"]["depends_on_missing"] == 2
    assert "2 ranked now without a depends-on line" in \
        groomer.render_proposal(proposal)


def test_a_count_of_zero_leaves_the_line_unchanged():
    cards = _pile(2)
    verdicts = {"DRE-1": _waits("nothing"), "DRE-2": _waits("nothing")}
    proposal = _propose(cards, readiness=_facts(), judgement=verdicts)
    assert proposal["judgement"]["depends_on_missing"] == 0
    line = groomer._receipt_line(proposal)
    assert "depends-on" not in line
    proposal["judgement"].pop("depends_on_missing")
    assert groomer._receipt_line(proposal) == line


# --------------------------------------------------------------------------
# the brief and the doc
# --------------------------------------------------------------------------
def test_the_brief_asks_for_the_card_id_or_nothing_and_still_parses():
    text = BRIEF.read_text(encoding="utf-8")
    section = text.split(groom_judgement.PROMPT_HEADING, 1)[1]
    assert re.search(r"depends on\*\*.*card id", section, re.S)
    assert "`nothing`" in section
    assert groom_judgement.problems() == []


def test_the_doc_states_the_rules_and_their_precedence():
    text = " ".join(DOC.read_text(encoding="utf-8").split())
    assert groomer.NOT_READY_HEADING in text
    for rule in (*RULE_IDS, "parked repo"):
        assert rule in text, rule
    for phrase in ("Readiness outranks the in-flight key",
                   "runs before the verify step",
                   "The Cancel list outranks Not ready",
                   "readiness outranks the read's decline",
                   "listed with its question quoted and sent nowhere",
                   "never for the card's own parent",
                   "`NAMED_PAGE`", "could not be read holds the card",
                   "`depends_on_missing`",
                   "Canceled clears the blocker",
                   "nothing is remembered",
                   "readiness is read per card",
                   "a unit leaves Intake epic first",
                   "the unit is the atom for order and cycle assignment"):
        assert phrase in text, phrase
    assert "DRE-6665, not yet in this file" not in text


def test_the_population_read_carries_child_titles_for_the_roll_up_rule():
    assert ("children(first: 50) { nodes { identifier title state { name } } }"
            in " ".join(groomer.POPULATION_QUERY.split()))
