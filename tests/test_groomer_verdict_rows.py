"""Every row the verify agent judged carries its verdict on the page, on the
Cancel list as on the Planning list (DRE-7823).

The console finds a card's verdict in one place only: its `### DRE-N` block
under `## Why each card is in the batch` (agent-bureau's `groomCardVerify`),
and DRE-6661 draws the verdict chip on the Planning list and the Cancel list
alike. `groom_verify_agent.apply` marks Cancel rows too, but until this card
the section wrote a block for Planning cards only — so on the morning of
2026-10-13 (proposal 3381b8d562a8) DRE-7060 and DRE-4054, both judged
done-elsewhere and both on the Cancel list, showed no verdict anywhere the
console reads. This file pins:

  * after every Planning block, one block per judged Cancel row, in Cancel
    list order: `- **Why:** <its Cancel reason>`, then the same
    `Verified against main:` line a Planning card gets, every proof on it;
  * a Cancel row the verify agent did not judge gets no block;
  * the section renders when the ranked read ran or any row on either list
    was judged, and there is a block to write — so an empty Planning list
    with a judged Cancel row renders that row alone;
  * the Planning blocks, and every other section of the page, are byte for
    byte what they were.

Run: cd bureau-pipeline && python3 -m pytest tests/test_groomer_verdict_rows.py -v
"""
from __future__ import annotations

import copy
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import groom_verify_agent  # noqa: E402
import groomer  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "groom-verdict-rows-3381b8d562a8.json"
#: The Cancel rows the verify agent judged that morning, in Cancel list order.
JUDGED_CANCELS = ["DRE-7060", "DRE-4054"]
#: The Cancel row the check before posting proposed, which nobody judged.
UNJUDGED_CANCEL = "DRE-3333"
VERDICT_LINE = re.compile(r"^Verified against main: \*\*([^*]+)\*\*")


def _proposal() -> dict:
    return copy.deepcopy(
        json.loads(FIXTURE.read_text(encoding="utf-8"))["proposal"])


def _section(text: str) -> str | None:
    """The section's body, heading excluded — None when it did not render."""
    if groomer.BATCH_REASONS_HEADING not in text:
        return None
    return text.split(groomer.BATCH_REASONS_HEADING, 1)[1].split("\n## ", 1)[0]


def _blocks(text: str) -> dict:
    """Every `### DRE-N` block of the section, in page order: id → lines."""
    body = _section(text)
    assert body is not None, "the section did not render"
    out = {}
    for entry in body.split("\n### ")[1:]:
        head, *lines = entry.splitlines()
        assert head not in out, f"{head} has two blocks"
        out[head] = [line for line in lines if line]
    return out


def _heads(text: str) -> list:
    return [line[4:] for line in (_section(text) or "").splitlines()
            if line.startswith("### ")]


def _row(proposal: dict, outcome: str, identifier: str) -> dict:
    [row] = [r for r in proposal["outcomes"][outcome]
             if r["identifier"] == identifier]
    return row


def _without_section(text: str) -> str:
    body = _section(text)
    return text if body is None else text.replace(
        groomer.BATCH_REASONS_HEADING + body, "")


def _unjudge(rows: list) -> None:
    for row in rows:
        row.pop("verify", None)


# --------------------------------------------------------------------------
# the fixture is the morning it says it is
# --------------------------------------------------------------------------
def test_the_fixture_holds_judged_and_unjudged_cancel_rows():
    proposal = _proposal()
    dead = sorted(proposal["outcomes"]["dead"],
                  key=lambda r: r.get("position") or 0)
    assert [r["identifier"] for r in dead if r.get("verify")] == JUDGED_CANCELS
    assert [r["identifier"] for r in dead if not r.get("verify")] == \
        [UNJUDGED_CANCEL]
    assert proposal["outcomes"]["now"], "the fixture has a Planning list"
    assert (proposal.get("judgement") or {}).get("enabled")


# --------------------------------------------------------------------------
# one block per judged card, on either list
# --------------------------------------------------------------------------
def test_every_judged_card_on_either_list_has_exactly_one_block():
    proposal = _proposal()
    text = groomer.render_proposal(proposal)
    heads = _heads(text)
    judged = [r["identifier"]
              for outcome in ("now", "dead")
              for r in proposal["outcomes"][outcome] if r.get("verify")]
    assert judged, "the fixture has no judged card"
    for identifier in judged:
        assert heads.count(identifier) == 1, (
            f"{identifier} carries a verify mark and has "
            f"{heads.count(identifier)} block(s) in the section")


def test_judged_cancel_blocks_follow_every_planning_block_in_cancel_order():
    proposal = _proposal()
    text = groomer.render_proposal(proposal)
    planning = [r["identifier"] for r in
                sorted(proposal["outcomes"]["now"], key=lambda r: r["position"])]
    assert _heads(text) == planning + JUDGED_CANCELS, (
        "the Planning blocks first, in batch order, then one block per judged "
        "Cancel row, in Cancel list order")


def test_a_judged_cancel_block_carries_its_reason_and_its_verdict_line():
    proposal = _proposal()
    text = groomer.render_proposal(proposal)
    blocks = _blocks(text)
    for identifier in JUDGED_CANCELS:
        row = _row(proposal, "dead", identifier)
        why, verdict, *rest = blocks[identifier]
        assert rest == [], f"{identifier}'s block has lines beyond its two"
        assert why == f"- **Why:** {groomer._line(row['reason'])}"
        assert verdict == groomer._verdict_line(row["verify"])
        match = VERDICT_LINE.match(verdict)
        assert match and match.group(1) in groom_verify_agent.VERDICTS
        assert match.group(1) == row["verify"]["verdict"]
        assert f"**{row['verify']['verdict']}** — " in verdict
        proofs = row["verify"].get("proof") or []
        assert proofs, f"{identifier} was judged with no proof in the record"
        for item in proofs:
            assert groomer.proof_line(item) in verdict, (
                f"{identifier}'s verdict line drops a proof item")


def test_a_cancel_row_the_verify_agent_did_not_judge_gets_no_block():
    text = groomer.render_proposal(_proposal())
    assert UNJUDGED_CANCEL not in _heads(text)
    assert f"### {UNJUDGED_CANCEL}" not in text


# --------------------------------------------------------------------------
# when the section renders
# --------------------------------------------------------------------------
def test_an_empty_planning_list_with_one_judged_cancel_renders_that_block_alone():
    proposal = _proposal()
    proposal["outcomes"]["now"] = []
    proposal["outcomes"]["dead"] = [_row(proposal, "dead", "DRE-7060"),
                                    _row(proposal, "dead", UNJUDGED_CANCEL)]
    text = groomer.render_proposal(proposal)
    assert _heads(text) == ["DRE-7060"]


def test_a_judged_cancel_renders_the_section_with_the_ranked_read_off():
    proposal = _proposal()
    proposal["judgement"] = {"enabled": False}
    proposal["outcomes"]["now"] = []
    text = groomer.render_proposal(proposal)
    assert _heads(text) == JUDGED_CANCELS


def test_ranked_read_off_and_nothing_judged_renders_no_section():
    proposal = _proposal()
    proposal["judgement"] = {"enabled": False}
    _unjudge(proposal["outcomes"]["now"])
    _unjudge(proposal["outcomes"]["dead"])
    text = groomer.render_proposal(proposal)
    assert groomer.BATCH_REASONS_HEADING not in text


def test_an_empty_planning_list_and_no_judged_cancel_renders_no_section():
    proposal = _proposal()
    proposal["outcomes"]["now"] = []
    _unjudge(proposal["outcomes"]["dead"])
    assert proposal["judgement"]["enabled"]
    text = groomer.render_proposal(proposal)
    assert groomer.BATCH_REASONS_HEADING not in text


def test_ranked_read_on_and_a_planning_list_renders_as_today_judged_or_not():
    proposal = _proposal()
    _unjudge(proposal["outcomes"]["now"])
    _unjudge(proposal["outcomes"]["dead"])
    text = groomer.render_proposal(proposal)
    planning = [r["identifier"] for r in
                sorted(proposal["outcomes"]["now"], key=lambda r: r["position"])]
    assert _heads(text) == planning
    assert not any(VERDICT_LINE.match(line)
                   for line in _section(text).splitlines())


# --------------------------------------------------------------------------
# nothing else moves
# --------------------------------------------------------------------------
def test_the_planning_blocks_and_every_other_section_are_unchanged():
    """Rendered with the Cancel rows' marks and without them, the page
    differs only by the judged Cancel blocks appended to the section."""
    judged = _proposal()
    bare = _proposal()
    _unjudge(bare["outcomes"]["dead"])
    with_cancels = groomer.render_proposal(judged)
    before = groomer.render_proposal(bare)
    assert _without_section(with_cancels) == _without_section(before), (
        "a section other than the reasons moved")
    first = f"\n### {JUDGED_CANCELS[0]}\n"
    assert first in _section(with_cancels), "no judged Cancel block was added"
    head = _section(with_cancels).split(first, 1)[0]
    assert head == _section(before), (
        "the Planning blocks are not byte for byte what they were")
