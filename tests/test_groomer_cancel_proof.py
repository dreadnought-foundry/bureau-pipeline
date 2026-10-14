"""Every Cancel row carries its proof on the row, a Cancel with none is
refused, and the page names what each verdict rested on (DRE-7241).

A Cancel row from the verify agent carried its `file:line` lines in its
Reason cell, but a row from the other three writers could carry nothing
checkable: a ranked-read Cancel whose evidence the plain-English guard
withheld went on the list with the withheld sentence as its whole reason.
This file pins:

  * a `proof` list on every Cancel row, from each of the four sources, and a
    Reason cell that ends with each proof line — seven cells per row, read
    back with the split DRE-4682's reader uses;
  * no proof, no Cancel: a ranked-read Cancel with no showable pointer keeps
    its place and is listed under `cancels_refused`;
  * an epic is canceled only on the verify agent's check — a card with any
    child is refused from the line, the check and the ranked read, and every
    row carries `children` beside `open_children`;
  * the verify agent's proof items, with their `fact` lines, and its
    `rested_on` on its Cancel row;
  * `rested on:` on the verdict line and in "What remains", and the partial
    pull request said in words;
  * the recorded morning of 2026-10-10 replays with its one Cancel row and
    the id `main` computes.

Run: cd bureau-pipeline && python3 -m pytest tests/test_groomer_cancel_proof.py -v
"""
from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import groom_verify_agent as gva  # noqa: E402
import groomer  # noqa: E402

from test_groom_verify import FakeGh, FakeLinear, OWNERS, other  # noqa: E402
from test_groomer import CYCLES, NOW, card, judged, ranked  # noqa: E402
from test_groomer_two_lists import cancel_rows  # noqa: E402

REPO_MAP = {"portico": "dreadnought-foundry/portico"}
FIXTURE = ROOT / "tests" / "fixtures" / "groom-rewrite-first-a4f198411b02.json"
#: `groom_verify_agent.apply` then `proposal_id` over the fixture, on `main`
#: at b9e41ec6, before this card.
REPLAY_ID_ON_MAIN = "ae45d1116967"

LINE = "DRE-201"       # its own `Superseded by:` line
JUDGED = "DRE-202"     # the ranked read's likely-done
CHECKED = "DRE-203"    # the check found a sibling saying it is superseded
VERIFIED = "DRE-204"   # the verify agent answered obsolete

POINTER = "pull request 274 merged it"
#: A pointer the plain-English guard refuses: code, not outcomes.
TECHNICAL = "scripts/groomer.py:1610 returns `WITHHELD_REASON`"
CODE_PROOF = {"file": "src/intake/census.ts", "line": 41,
              "quote": "export function census(cards) {"}
DONE_PROOF = {"done_card": "DRE-9", "pr": "dreadnought-foundry/portico#77",
              "covers": "whole", "quote": "it shipped the census form",
              "fact": "DRE-9 → dreadnought-foundry/portico#77, merged "
                      "2026-10-01 — ship the census form",
              "covers_check": "paths"}


def _lane(*, children=None) -> list[dict]:
    """Ten cards, DRE-201 the newest. `children` is the `children` field
    every one of the first four carries, when given."""
    cards = [card(f"DRE-{200 + n}", days=n) for n in range(1, 11)]
    cards[0]["description"] = "Some context.\nSuperseded by: DRE-4700\n"
    if children is not None:
        for c in cards[:4]:
            c["children"] = {"nodes": [
                {"identifier": f"DRE-9{c['identifier'][4:]}{n}",
                 "state": {"name": s}} for n, s in enumerate(children)]}
    return cards


def _answer(cards, pointer=POINTER) -> str:
    return "\n".join(
        f"{JUDGED} | likely-done | the work already happened | {pointer}"
        if c["identifier"] == JUDGED else ranked([c["identifier"]])
        for c in cards)


def _linear() -> FakeLinear:
    return FakeLinear({CHECKED: {"siblings": [
        other(CHECKED, "Intake"),
        other("DRE-4701", "Done", f"This card supersedes {CHECKED}.")]}},
        lanes={"DRE-4700": "Done", "DRE-4701": "Done"})


def _checked(cards, *, pointer=POINTER, capacity=8) -> dict:
    """The proposal as `_build` writes it: proposed, checked, proposed again."""
    return groomer.verify_proposal(
        cards, dict(cycles=CYCLES, capacity=capacity, now=NOW,
                    judgement=judged(cards, _answer(cards, pointer))),
        lops=_linear(), run=FakeGh(), owners=OWNERS)


def _doc(card_id, verdict, proof, **extra) -> dict:
    return {"card": card_id, "verdict": verdict, "summary": "One sentence.",
            "proof": proof, "reason": None, "cost_usd": 0.1,
            "duration_ms": 1000, "model": "test-model", "lookup": "ok",
            **extra}


def _apply(prop: dict, found: dict) -> dict:
    rows = [{"card": c} for c in found]
    return gva.apply(copy.deepcopy(prop), found, rows, repo_map=REPO_MAP)


def _four_sources() -> dict:
    prop = _checked(_lane())
    return _apply(prop, {VERIFIED: _doc(VERIFIED, gva.OBSOLETE, [CODE_PROOF])})


def _dead(prop) -> dict:
    return {r["identifier"]: r for r in prop["outcomes"]["dead"]}


def _refused(prop) -> dict:
    return {r["identifier"]: r for r in prop.get("cancels_refused") or []}


def _planning(prop) -> list[str]:
    return [r["identifier"] for r in
            sorted(prop["outcomes"]["now"], key=lambda r: r["position"])]


# --------------------------------------------------------------------------
# criterion 1 — four sources, four rows, each Reason cell ending in proof
# --------------------------------------------------------------------------
def test_four_sources_render_four_cancel_rows_each_ending_with_a_proof_line():
    after = _four_sources()
    dead = _dead(after)
    assert {i: r["source"] for i, r in dead.items()} == {
        LINE: groomer.DEAD_FROM_LINE, JUDGED: groomer.DEAD_FROM_JUDGEMENT,
        CHECKED: groomer.DEAD_FROM_CHECK, VERIFIED: gva.CANCEL_SOURCE}
    rows = cancel_rows(groomer.render_proposal(after))
    assert sorted(r["identifier"] for r in rows) == sorted(dead)
    for row in rows:
        record = dead[row["identifier"]]
        assert len(row["cells"]) == 7, row["cells"]
        assert record["proof"], f"{row['identifier']} carries no proof"
        lines = [groomer.proof_line(p) for p in record["proof"]]
        assert row["reason"].endswith(lines[-1]), (row["reason"], lines)
        for line in lines:
            assert line in row["reason"], (row["identifier"], line)


def test_each_source_writes_its_own_proof_item():
    dead = _dead(_four_sources())
    assert dead[LINE]["proof"] == [{"source": "the card's Superseded by line",
                                    "quote": "Superseded by: DRE-4700"}]
    assert dead[JUDGED]["proof"] == [{"source": "the ranked read",
                                      "quote": POINTER}]
    [check] = dead[CHECKED]["proof"]
    assert check["source"] == "the deterministic check"
    assert check["quote"] == dead[CHECKED]["check_reason"]
    assert dead[VERIFIED]["proof"] == [CODE_PROOF]


def test_the_reason_ends_with_its_proof_and_is_never_the_withheld_sentence():
    for row in _four_sources()["outcomes"]["dead"]:
        assert row["reason"] != groomer.WITHHELD_REASON
        assert row["reason"].endswith(groomer.proof_line(row["proof"][-1]))


def test_the_cancel_table_keeps_its_heading_and_seven_columns():
    text = groomer.render_proposal(_four_sources())
    assert groomer.CANCEL_COLUMNS == ("| # | Card | Pri | Repo | Epic | "
                                      "Title | Reason |")
    body = text.split(groomer.CANCEL_HEADING + "\n", 1)[1].split("\n## ", 1)[0]
    table = [ln for ln in body.splitlines() if ln.startswith("|")]
    assert table[0] == groomer.CANCEL_COLUMNS
    assert table[1] == "| -- | -- | -- | -- | -- | -- | -- |"


def test_a_row_written_without_its_proof_lines_still_renders_them():
    """The render ends each Reason cell with the row's proof lines whoever
    wrote the reason, and never twice."""
    after = _four_sources()
    for row in after["outcomes"]["dead"]:
        if row["source"] == groomer.DEAD_FROM_JUDGEMENT:
            row["reason"] = "the work already happened"
    rows = {r["identifier"]: r for r in
            cancel_rows(groomer.render_proposal(after))}
    line = groomer.proof_line({"source": "the ranked read", "quote": POINTER})
    assert rows[JUDGED]["reason"] == f"the work already happened; {line}"
    for identifier, row in rows.items():
        for item in _dead(after)[identifier]["proof"]:
            assert row["reason"].count(groomer.proof_line(item)) == 1


# --------------------------------------------------------------------------
# criterion 2 — no proof, no Cancel
# --------------------------------------------------------------------------
def test_a_likely_done_whose_pointer_the_guard_withholds_is_not_canceled():
    assert groomer._showable(TECHNICAL) is False
    cards = _lane()
    prop = groomer.propose(cards, cycles=CYCLES, capacity=8, now=NOW,
                           judgement=judged(cards, _answer(cards, TECHNICAL)))
    assert JUDGED not in _dead(prop)
    assert JUDGED in _planning(prop), "the refused card lost its slot"
    assert groomer.NO_CANCEL_PROOF == "no proof on the row"
    assert _refused(prop)[JUDGED] == {
        "identifier": JUDGED, "refusal": groomer.NO_CANCEL_PROOF,
        "source": groomer.DEAD_FROM_JUDGEMENT}
    seq = {r["identifier"]: r for r in prop["sequence"]}
    assert seq[JUDGED]["outcome"] == "now"
    text = groomer.render_proposal(prop)
    section = text.split("## Cancels refused", 1)[1].split("\n## ", 1)[0]
    assert JUDGED in section and "no proof on the row" in section
    assert TECHNICAL not in text


def test_a_withheld_pointer_outside_the_batch_keeps_its_wait():
    cards = _lane()
    prop = groomer.propose(cards, cycles=CYCLES, capacity=1, now=NOW,
                           judgement=judged(cards, _answer(cards, TECHNICAL)))
    later = {r["identifier"]: r for r in prop["outcomes"]["not-now"]}
    assert JUDGED in later and later[JUDGED]["reconsidered_in"] is not None
    assert JUDGED not in _dead(prop)


def test_cancel_proof_refusal_refuses_an_empty_proof_from_every_source():
    for source in (groomer.DEAD_FROM_LINE, groomer.DEAD_FROM_CHECK,
                   groomer.DEAD_FROM_JUDGEMENT, gva.CANCEL_SOURCE):
        for proof in ([], None, [{"source": "x", "quote": "  "}]):
            row = {"identifier": "DRE-9", "source": source, "children": 0}
            if proof is not None:
                row["proof"] = proof
            assert groomer.cancel_proof_refusal(row) == groomer.NO_CANCEL_PROOF
        assert groomer.cancel_proof_refusal(
            {"identifier": "DRE-9", "source": source, "children": 0,
             "proof": [{"source": "x", "quote": "a line"}]}) is None


# --------------------------------------------------------------------------
# criterion 3 — an epic is canceled only on the verify agent's check
# --------------------------------------------------------------------------
def _assert_epic_refused(prop, identifier, source):
    assert groomer.EPIC_CANCEL_REFUSED == ("an epic is canceled only on the "
                                           "verify agent's check")
    assert identifier not in _dead(prop), f"{source} canceled an epic"
    assert identifier in _planning(prop), "the refused epic lost its slot"
    assert _refused(prop)[identifier] == {
        "identifier": identifier, "refusal": groomer.EPIC_CANCEL_REFUSED,
        "source": source}
    row = next(r for r in prop["outcomes"]["now"]
               if r["identifier"] == identifier)
    assert row["children"] == 2 and row["open_children"] == 0


def test_an_epic_with_two_done_children_is_refused_off_its_superseded_line():
    prop = groomer.propose(_lane(children=("Done", "Done")), cycles=CYCLES,
                           capacity=8, now=NOW)
    _assert_epic_refused(prop, LINE, groomer.DEAD_FROM_LINE)


def test_an_epic_with_two_done_children_is_refused_off_the_check():
    prop = _checked(_lane(children=("Done", "Done")))
    _assert_epic_refused(prop, CHECKED, groomer.DEAD_FROM_CHECK)


def test_an_epic_with_two_done_children_is_refused_off_the_ranked_read():
    prop = _checked(_lane(children=("Done", "Done")))
    _assert_epic_refused(prop, JUDGED, groomer.DEAD_FROM_JUDGEMENT)
    text = groomer.render_proposal(prop)
    section = text.split("## Cancels refused", 1)[1].split("\n## ", 1)[0]
    for identifier in (LINE, CHECKED, JUDGED):
        assert identifier in section
    assert groomer.EPIC_CANCEL_REFUSED in section


def test_the_same_cards_with_no_children_go_on_the_cancel_list():
    prop = _checked(_lane())
    dead = _dead(prop)
    assert {LINE, JUDGED, CHECKED} <= set(dead)
    assert prop["cancels_refused"] == []
    assert all(dead[i]["children"] == 0 for i in (LINE, JUDGED, CHECKED))


def test_an_open_child_is_still_refused_as_before():
    prop = groomer.propose(_lane(children=("Done", "In Progress")),
                           cycles=CYCLES, capacity=8, now=NOW)
    assert _refused(prop)[LINE]["refusal"] == "DRE-201 is an epic with 1 open child"


def test_the_verify_agent_may_still_cancel_an_epic_whose_children_are_closed():
    prop = _checked(_lane(children=("Done", "Done")))
    after = _apply(prop, {VERIFIED: _doc(VERIFIED, gva.DONE_ELSEWHERE,
                                         [DONE_PROOF])})
    assert VERIFIED in _dead(after)
    assert VERIFIED not in _refused(after)


def test_every_row_carrying_open_children_carries_children():
    prop = _checked(_lane(children=("Done", "Done")), capacity=6)
    named = 0
    for name in ("now", "not-now", "dead"):
        for row in prop["outcomes"][name]:
            if "open_children" in row:
                assert "children" in row, f"a {name} row has no child count"
                named += 1
    for row in prop["sequence"]:
        assert "children" in row and "open_children" in row
    assert named == len(prop["sequence"])
    seq = {r["identifier"]: r for r in prop["sequence"]}
    assert seq[LINE]["children"] == 2 and seq["DRE-210"]["children"] == 0


def test_child_count_counts_every_child_in_any_state():
    node = {"children": {"nodes": [
        {"identifier": "DRE-1", "state": {"name": "Done"}},
        {"identifier": "DRE-2", "state": {"name": "Canceled"}},
        {"identifier": "DRE-3", "state": {"name": "Todo"}}]}}
    assert groomer.child_count(node) == 3
    assert groomer.child_count(card("DRE-1")) == 0
    assert groomer.child_count({"children": None}) == 0


def test_cancel_proof_refusal_reads_the_children_count_per_source():
    proof = [{"source": "x", "quote": "a line"}]
    for source in (groomer.DEAD_FROM_LINE, groomer.DEAD_FROM_CHECK,
                   groomer.DEAD_FROM_JUDGEMENT):
        assert groomer.cancel_proof_refusal(
            {"identifier": "DRE-9", "source": source, "proof": proof,
             "children": 1}) == groomer.EPIC_CANCEL_REFUSED
    assert groomer.cancel_proof_refusal(
        {"identifier": "DRE-9", "source": gva.CANCEL_SOURCE, "proof": proof,
         "children": 3}) is None


def test_the_children_count_is_not_in_the_proposal_id():
    prop = _checked(_lane(children=("Done", "Done")))
    stripped = copy.deepcopy(prop)
    for rows in list(stripped["outcomes"].values()) + [stripped["sequence"]]:
        for row in rows:
            row.pop("children", None)
    assert groomer.proposal_id(stripped) == prop["id"]


# --------------------------------------------------------------------------
# criterion 4 — the verify agent's proof, facts and rested_on on its row
# --------------------------------------------------------------------------
def test_a_verify_agent_cancel_carries_its_proof_facts_and_rested_on():
    prop = _checked(_lane())
    after = _apply(prop, {VERIFIED: _doc(
        VERIFIED, gva.DONE_ELSEWHERE, [DONE_PROOF],
        rested_on=["done-work", "thread"])})
    row = _dead(after)[VERIFIED]
    assert row["proof"] == [DONE_PROOF]
    assert row["proof"][0]["fact"] == DONE_PROOF["fact"]
    assert row["rested_on"] == ["done-work", "thread"]
    reason = {r["identifier"]: r["reason"] for r in
              cancel_rows(groomer.render_proposal(after))}[VERIFIED]
    assert DONE_PROOF["fact"] in reason
    assert reason.endswith(groomer.proof_line(DONE_PROOF))
    # …and in the JSON the record is written as.
    assert json.loads(json.dumps(after))["outcomes"]["dead"]


# --------------------------------------------------------------------------
# criterion 5 — rested on, and the partial pull request, on the page
# --------------------------------------------------------------------------
def test_a_verified_planning_row_says_what_its_verdict_rested_on():
    prop = _checked(_lane())
    target = "DRE-205"
    assert target in _planning(prop)
    after = _apply(prop, {target: _doc(
        target, gva.STILL_NEEDED, [{**DONE_PROOF, "covers": "part"}],
        rested_on=["done-work", "thread"])})
    row = next(r for r in after["outcomes"]["now"]
               if r["identifier"] == target)
    assert row["verify"]["rested_on"] == ["done-work", "thread"]
    text = groomer.render_proposal(after)
    block = text.split(f"### {target}\n", 1)[1].split("\n\n", 1)[0]
    [line] = [ln for ln in block.splitlines()
              if ln.startswith("Verified against main:")]
    assert "rested on: done-work, thread" in line


def test_the_verdict_line_names_rested_on_only_when_the_verdict_carries_it():
    mark = {"verdict": "still-needed", "summary": "Still wanted.",
            "proof": [CODE_PROOF]}
    assert "rested on" not in groomer._verdict_line(mark)
    assert "rested on" not in groomer._verdict_line({**mark, "rested_on": []})
    line = groomer._verdict_line({**mark, "rested_on": ["done-work", "thread"]})
    assert "rested on: done-work, thread" in line


def test_an_adjusted_verdict_says_a_pull_request_did_part_of_it():
    mark = {"verdict": "partly-solved", "summary": "Half of it shipped.",
            "proof": [{**DONE_PROOF, "covers": "part"}],
            "rested_on": ["done-work"], "adjusted": gva.ADJUSTED}
    assert "(a pull request did part of it)" in groomer._verdict_line(mark)


def test_a_partly_solved_rewrite_row_with_adjusted_says_so_in_what_remains():
    prop = _checked(_lane())
    target = "DRE-205"
    after = _apply(prop, {target: _doc(
        target, gva.PARTLY_SOLVED, [{**DONE_PROOF, "covers": "part"}],
        rested_on=["done-work"], adjusted=gva.ADJUSTED)})
    [row] = after["outcomes"][groomer.REWRITE]
    assert row["identifier"] == target
    assert row["verify"]["adjusted"] == gva.ADJUSTED
    cell = groomer._what_remains(row)
    assert "a pull request did part of it" in cell
    assert "rested on: done-work" in cell
    text = groomer.render_proposal(after)
    section = text.split(groomer.REWRITE_HEADING, 1)[1].split("\n## ", 1)[0]
    assert "a pull request did part of it" in section


# --------------------------------------------------------------------------
# criterion 6 — the recorded morning of 2026-10-10 replays unchanged
# --------------------------------------------------------------------------
def test_the_recorded_morning_keeps_its_one_cancel_row_and_its_id():
    fx = json.loads(FIXTURE.read_text(encoding="utf-8"))
    found = {c: dict(v) for c, v in fx["verdicts"].items()}
    for c, draft in fx["drafts"].items():
        found[c].update(draft)
    after = gva.apply(copy.deepcopy(fx["proposal"]), found)
    assert [r["identifier"] for r in after["outcomes"]["dead"]] == ["DRE-3703"]
    [row] = cancel_rows(groomer.render_proposal(after))
    assert row["identifier"] == "DRE-3703" and len(row["cells"]) == 7
    for item in fx["verdicts"]["DRE-3703"]["proof"]:
        assert f"{item['file']}:{item['line']} — " in row["reason"]
    assert after["id"] == REPLAY_ID_ON_MAIN
    assert after["id"] == groomer.proposal_id(after)
