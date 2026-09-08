"""The proposal says WHY, in the comment the CEO approves (DRE-3152).

DRE-3150 put the judgement into the proposal JSON and DRE-3259 put the cost of
the one call beside it. Neither of them is read by anybody until it is on the
page the CEO reads before approving a batch, and that page is what this file
holds to:

  * every row of "The batch, in order" carries the **Why** that placed it —
    the model's line on a judged row, the rule's own line on a rules-only one;
  * every deferred card names the **trigger** that brings it back, grouped so
    four cards waiting on one thing read as one line and not as four;
  * a dead recommendation says which of the two sources made it — a
    declaration a person wrote on the card, or the ranked read's judgement with
    the **evidence** it named;
  * "could not rank" is its own section and never folded into "not now" —
    refusal is not a default;
  * one **receipt line** at the top says what ranked it, over how many cards,
    against how much context — and says a **cut answer** out loud, so a short
    answer is read as a short answer rather than as a model declining to rank.

Run: cd bureau-pipeline && python3 -m pytest tests/test_groomer_render.py -v
"""
from __future__ import annotations

import dataclasses
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")

import groom_context  # noqa: E402
import groom_judgement  # noqa: E402
import groomer  # noqa: E402

from test_groom_context import StubLops, failing_gh  # noqa: E402
from test_groomer import CYCLES, GOLDEN, NOW, Counter, card, judged, ranked  # noqa: E402

# The pack the receipt line reports on: three epics in flight, two merged pull
# requests and one closed card, so a count that is really the wrong section's
# shows up as the wrong number rather than as a coincidence.
PACK = groom_context.pack(
    epics=[{"identifier": f"DRE-9{n}", "title": f"epic {n}"} for n in range(3)],
    merged_prs=[{"title": "DRE-1 does a thing", "url": "https://x/1",
                 "merged_at": "2026-09-04T09:00:00Z"},
                {"title": "DRE-2 does a thing", "url": "https://x/2",
                 "merged_at": "2026-09-03T09:00:00Z"}],
    closed_cards=[{"identifier": "DRE-3", "title": "closed",
                   "completedAt": "2026-09-02T09:00:00Z"}],
    now=NOW)


def with_pack(cards, answer, *, truncated=False, pack=PACK):
    """A judgement over `cards` against a pack with real counts in it."""
    rows = groom_judgement.census(cards, now=NOW)
    return groom_judgement.run(rows, pack, call=Counter(answer, truncated),
                               model="test-model")


def section(text: str, heading: str) -> str:
    """One `## ` section of the rendered proposal, heading excluded."""
    assert heading in text, f"the proposal has no {heading!r} section"
    body = text.split(heading, 1)[1]
    return body.split("\n## ", 1)[0]


def batch_rows(text: str) -> dict:
    """The rows of "The batch, in order", keyed by card id."""
    rows = {}
    for line in section(text, "## The batch, in order").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) > 2 and cells[1].startswith("DRE-"):
            rows[cells[1]] = cells
    return rows


# --------------------------------------------------------------------------
# a reason on every card
# --------------------------------------------------------------------------
def test_the_batch_table_carries_a_why_for_every_row():
    cards = [card(f"DRE-{n}", days=n) for n in range(1, 5)]
    answer = "\n".join([
        "DRE-1 | now | the console cannot ship without it",
        "DRE-2 | now | it unblocks the forms work",
        "DRE-3 | now | a customer asked for it twice",
        "DRE-4 | now | it finishes the epic",
    ])
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=10, now=NOW,
                               judgement=with_pack(cards, answer))
    text = groomer.render_proposal(proposal)
    assert "| Why |" in text, "the batch table has no Why column"
    rows = batch_rows(text)
    assert set(rows) == {"DRE-1", "DRE-2", "DRE-3", "DRE-4"}
    for row in proposal["outcomes"]["now"]:
        cells = rows[row["identifier"]]
        assert any(row["reason"] in cell for cell in cells), (
            f"{row['identifier']}'s reason is not on its row"
        )


def test_a_rules_only_row_shows_the_rule_that_placed_it():
    cards = [card("DRE-1", days=1, priority=1), card("DRE-2", days=3)]
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=10, now=NOW)
    rows = batch_rows(groomer.render_proposal(proposal))
    assert "Urgent" in " | ".join(rows["DRE-1"])
    assert "created inside the window" in " | ".join(rows["DRE-2"]), (
        "a rules-only row shows its rule, not an empty cell"
    )


# --------------------------------------------------------------------------
# a trigger on every 'not now'
# --------------------------------------------------------------------------
def test_the_not_now_section_groups_one_trigger_shared_by_four_cards():
    cards = [card(f"DRE-{n}", days=n) for n in range(1, 7)]
    answer = "\n".join([
        ranked(["DRE-1", "DRE-2"]),
        "DRE-3 | not-now | it waits on the console | revisit when DRE-3120 finishes",
        "DRE-4 | not-now | it waits on the console | revisit when DRE-3120 finishes",
        "DRE-5 | not-now | it waits on the console | revisit when DRE-3120 finishes",
        "DRE-6 | not-now | it waits on the console | revisit when DRE-3120 finishes",
    ])
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=2, now=NOW,
                               judgement=with_pack(cards, answer))
    body = section(groomer.render_proposal(proposal),
                   "## Not now — and when to come back")
    assert "revisit when DRE-3120 finishes — 4 cards" in body, (
        "four cards waiting on one thing read as one line, with the count"
    )
    for identifier in ("DRE-3", "DRE-4", "DRE-5", "DRE-6"):
        assert identifier in body, f"{identifier} is deferred and unnamed"
    # …and EVERY trigger the rows carry is on the page with its own count, not
    # just the one the model wrote.
    counts: dict = {}
    for row in proposal["outcomes"]["not-now"]:
        counts[row["trigger"]] = counts.get(row["trigger"], 0) + 1
    assert counts, "the fixture deferred nothing"
    for trigger, count in counts.items():
        assert f"{trigger} — {count} card" in body, (
            f"the trigger {trigger!r} is not reported with its count"
        )


def test_the_window_receipt_still_reports_the_cards_no_trigger_reaches():
    """Rows the window held back keep today's one-line receipt — the section
    above is an addition, not a replacement for it."""
    proposal = json.loads(GOLDEN.read_text(encoding="utf-8"))
    built = groomer.propose(
        proposal["cards"], cycles=proposal["cycles"],
        capacity=proposal["capacity"], batch_cycles=proposal["batch_cycles"],
        now=proposal["now"], judgement=None)
    assert built["older_than_window"]["line"] in groomer.render_proposal(built)


# --------------------------------------------------------------------------
# evidence on every 'likely done', and the source it came from
# --------------------------------------------------------------------------
def test_the_dead_list_shows_the_evidence_and_separates_the_two_sources():
    cards = [card("DRE-1"), card("DRE-2", description="Superseded by: DRE-9"),
             card("DRE-3")]
    answer = "\n".join([
        "DRE-1 | likely-done | a merge already did it | pull request 274 merged it",
        ranked(["DRE-3"]),
    ])
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=5, now=NOW,
                               judgement=with_pack(cards, answer))
    body = section(groomer.render_proposal(proposal),
                   "## Recommended dead — your call, not ours")
    judged_line = next(l for l in body.splitlines() if l.startswith("- DRE-1"))
    declared_line = next(l for l in body.splitlines() if l.startswith("- DRE-2"))
    assert "likely done or obsolete — pull request 274 merged it" in judged_line
    assert "DRE-1 does a thing" in judged_line
    assert "superseded by DRE-9" in declared_line
    # …and the CEO can tell which is a declaration on the card and which is a
    # judgement, without knowing the two readers exist.
    assert body.index("Declared on the card") < body.index(declared_line)
    assert body.index("ranked read") < body.index(judged_line)
    assert body.index(declared_line) != body.index(judged_line)


def test_a_judged_dead_row_whose_evidence_was_withheld_says_so():
    cards = [card("DRE-1"), card("DRE-2")]
    answer = "\n".join([
        "DRE-1 | likely-done | a merge already did it | see scripts/groomer.py",
        ranked(["DRE-2"]),
    ])
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=5, now=NOW,
                               judgement=with_pack(cards, answer))
    body = section(groomer.render_proposal(proposal),
                   "## Recommended dead — your call, not ours")
    line = next(l for l in body.splitlines() if l.startswith("- DRE-1"))
    assert "run log" in line, (
        "evidence that could not be shown is reported as absent, never as an "
        "empty recommendation"
    )


# --------------------------------------------------------------------------
# 'could not rank' said out loud
# --------------------------------------------------------------------------
def test_the_unranked_are_their_own_section_with_their_titles():
    cards = [card("DRE-1", days=1, title="wire the alert engine"),
             card("DRE-2", days=2, title="rename the console tab"),
             card("DRE-3", days=3, title="ship the groomer"),
             card("DRE-4", days=4, title="drain the backlog")]
    answer = "\n".join([
        ranked(["DRE-3"]),
        "DRE-4 | not-now | the console work has to land first | when the console lands",
    ])
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=5, now=NOW,
                               judgement=with_pack(cards, answer))
    text = groomer.render_proposal(proposal)
    body = section(text, "## Could not rank — needs a person")
    assert proposal["judgement"]["unranked"] == ["DRE-1", "DRE-2"]
    for identifier, title in (("DRE-1", "wire the alert engine"),
                              ("DRE-2", "rename the console tab")):
        assert identifier in body and title in body
    later = section(text, "## Not now — and when to come back")
    assert "DRE-1" not in later and "DRE-2" not in later, (
        "a card nobody could rank is not a card deliberately deferred — "
        "refusal is not a default"
    )
    assert "DRE-4" in later, "the deliberately deferred card is still deferred"


def test_no_unranked_section_when_every_card_was_ranked():
    cards = [card("DRE-1"), card("DRE-2")]
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=5, now=NOW,
                               judgement=with_pack(cards,
                                                   ranked(["DRE-1", "DRE-2"])))
    assert "Could not rank" not in groomer.render_proposal(proposal)


# --------------------------------------------------------------------------
# the receipt line at the top
# --------------------------------------------------------------------------
def test_the_receipt_line_names_the_read_the_call_and_the_context():
    cards = [card(f"DRE-{n}", days=n) for n in range(1, 4)]
    proposal = groomer.propose(
        cards, cycles=CYCLES, capacity=5, now=NOW,
        judgement=with_pack(cards, ranked(["DRE-1", "DRE-2", "DRE-3"])))
    text = groomer.render_proposal(proposal)
    assert ("Ranked by test-model (asked) / test-model (answered) in 1 call "
            "over 3 cards, against 3 epics in flight, 2 merged PRs and "
            "1 closed card") in text
    # …and it is the FIRST thing under the title, before the approval line.
    assert text.index("Ranked by") < text.index("**To approve:**")


def test_the_receipt_line_says_the_rules_alone_when_the_judgement_is_off():
    cards = [card("DRE-1"), card("DRE-2")]
    text = groomer.render_proposal(
        groomer.propose(cards, cycles=CYCLES, capacity=5, now=NOW))
    assert "Ranked by the rules only (judgement off)" in text
    assert "What the ranked read said" not in text
    assert "Could not rank" not in text


def test_the_receipt_line_counts_the_reasons_the_guard_withheld():
    cards = [card("DRE-1"), card("DRE-2"), card("DRE-3")]
    answer = "\n".join([
        "DRE-1 | now | it patches scripts/groomer.py",
        "DRE-2 | now | run python3 scripts/groomer.py propose first",
        "DRE-3 | now | the console work needs it",
    ])
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=5, now=NOW,
                               judgement=with_pack(cards, answer))
    text = groomer.render_proposal(proposal)
    assert proposal["judgement"]["withheld"] == ["DRE-1", "DRE-2"]
    receipt = next(l for l in text.splitlines() if l.startswith("Ranked by"))
    assert "2 reasons" in receipt, (
        "the count of withheld reasons is part of the receipt, not a footnote"
    )


# --------------------------------------------------------------------------
# a cut answer, read before anything moves
# --------------------------------------------------------------------------
def test_a_cut_answer_is_named_in_the_receipt_and_lands_in_could_not_rank():
    cards = [card(f"DRE-{n:02d}") for n in range(10)]
    answer = (ranked([f"DRE-{n:02d}" for n in range(4)])
              + "\nDRE-04 | now | it is wa")
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=10, now=NOW,
                               judgement=with_pack(cards, answer, truncated=True))
    block = proposal["judgement"]
    assert block["truncated"] is True and len(block["unranked"]) == 6
    text = groomer.render_proposal(proposal)
    receipt = next(l for l in text.splitlines() if l.startswith("Ranked by"))
    assert (f"the answer was cut short at {block['output_budget']} tokens; "
            f"6 cards could not be ranked for that reason") in receipt
    body = section(text, "## Could not rank — needs a person")
    for identifier in block["unranked"]:
        assert identifier in body, f"{identifier} was cut and is not reported"


# --------------------------------------------------------------------------
# a context signal nobody could read (DRE-3329)
# --------------------------------------------------------------------------
def unread_pack(*, run=None):
    """The pack of a run whose `gh search prs` failed — the shape run
    34183475867 produced, built through the real reader so the failure travels
    the whole way to the page."""
    return groom_context.read_pack(StubLops(), now=NOW, run=run or failing_gh)


def test_a_context_signal_that_could_not_be_read_renders_as_unknown():
    """`0 merged PRs` on a night the fleet merged several is the exact failure
    console-honesty rule 2 exists to prevent: a plausible default is
    indistinguishable from a real answer. The count could not be read, so the
    page says UNKNOWN and no number at all (DRE-3329)."""
    cards = [card(f"DRE-{n}", days=n) for n in range(1, 4)]
    proposal = groomer.propose(
        cards, cycles=CYCLES, capacity=5, now=NOW,
        judgement=with_pack(cards, ranked(["DRE-1", "DRE-2", "DRE-3"]),
                            pack=unread_pack()))
    text = groomer.render_proposal(proposal)
    receipt = next(l for l in text.splitlines() if l.startswith("Ranked by"))
    assert "UNKNOWN merged PRs" in receipt
    assert "0 merged PR" not in text, "the unreadable count was published as 0"
    # Every other count on this line is real and non-zero, so a `0` anywhere in
    # it can only be the unreadable section defaulting.
    assert "0" not in receipt, f"a failing gh search put a 0 on the page: {receipt}"
    assert proposal["judgement"]["pack"]["merged_prs"] is None
    assert proposal["judgement"]["pack"]["unread"] == ["merged_prs"]


def test_a_run_that_read_no_pack_at_all_reports_unknown_not_zeros():
    """The same lie one step further out: a run holding no pack knows nothing
    about what is in flight, and zeros would say it knew."""
    cards = [card("DRE-1")]
    judgement = dataclasses.replace(with_pack(cards, ranked(["DRE-1"])), pack={})
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=5, now=NOW,
                               judgement=judgement)
    block = proposal["judgement"]
    assert all(block["pack"][name] is None for name in groom_context.SECTIONS)
    assert block["pack"]["unread"] == sorted(groom_context.SECTIONS)
    receipt = next(l for l in groomer.render_proposal(proposal).splitlines()
                   if l.startswith("Ranked by"))
    assert "UNKNOWN epics" in receipt and "0 epic" not in receipt


def test_the_ranking_prompt_is_told_the_difference_between_zero_and_unread():
    """The pack feeds the ranking as well as the page, so the model must not
    read a source nobody could reach as a fortnight with nothing in it."""
    rows = groom_judgement.census([card("DRE-1")], now=NOW)
    broken = groom_judgement.prompt_for(rows, unread_pack())
    read = groom_judgement.prompt_for(rows, groom_context.pack(now=NOW))
    assert "could not be read this run (merged_prs)" in broken
    assert "treat it as unknown rather than as empty" in broken
    assert "could not be read" not in read, (
        "a section that was read and held nothing says Nothing, not unknown"
    )
    assert "Nothing." in read


def test_a_section_that_was_read_and_held_nothing_is_still_a_real_zero():
    """The other half of rule 2: "nothing merged" and "we could not ask" get
    visibly different renderings, so UNKNOWN never swallows a true zero."""
    cards = [card("DRE-1")]
    empty = groom_context.pack(now=NOW)
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=5, now=NOW,
                               judgement=with_pack(cards, ranked(["DRE-1"]),
                                                   pack=empty))
    receipt = next(l for l in groomer.render_proposal(proposal).splitlines()
                   if l.startswith("Ranked by"))
    assert "0 merged PRs" in receipt
    assert "UNKNOWN" not in receipt


def test_a_proposal_written_before_the_budget_keys_invents_no_number():
    """A proposal file from before DRE-3259 carries neither key. The receipt
    line then says nothing about a budget or a cut."""
    cards = [card("DRE-1"), card("DRE-2")]
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=5, now=NOW,
                               judgement=with_pack(cards,
                                                   ranked(["DRE-1", "DRE-2"])))
    proposal["judgement"].pop("output_budget")
    proposal["judgement"].pop("truncated")
    text = groomer.render_proposal(proposal)
    assert "cut short" not in text
    assert "token" not in text.lower()
    assert "Ranked by test-model" in text, "the rest of the receipt still reads"


# --------------------------------------------------------------------------
# what none of this may move
# --------------------------------------------------------------------------
# Computed on the fixture before this card touched anything: `proposal_id`
# digests the batch's cards, positions and cycles, so a new column in the
# comment does not retire a CEO approval.
FIXTURE_RULES_ONLY_ID = "45946184638e"
FIXTURE_JUDGED_ID = "85f53ade431b"


def _fixture(judgement=None):
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    return groomer.propose(
        golden["cards"], cycles=golden["cycles"], capacity=golden["capacity"],
        batch_cycles=golden["batch_cycles"], now=golden["now"],
        judgement=judgement)


def test_the_proposal_id_is_unchanged_by_the_rendering():
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    judgement = judged(golden["cards"],
                       ranked([c["identifier"] for c in golden["cards"]]))
    assert _fixture()["id"] == FIXTURE_RULES_ONLY_ID
    assert _fixture(judgement)["id"] == FIXTURE_JUDGED_ID


def test_every_section_the_fixture_renders_today_still_renders():
    """An existing proposal is still readable: the rules-only page keeps every
    section it had, and gains the ones that read fields the rules also write."""
    text = groomer.render_proposal(_fixture())
    for heading in ("## The population", "## The batch, in order",
                    "## Collisions, and what the order does about them",
                    "## What waits, and roughly how long",
                    "## Recommended dead — your call, not ours",
                    "## On cycles"):
        assert heading in text, f"{heading} stopped rendering"
    assert "## Not now — and when to come back" in text
    assert groomer.CYCLE_IS_NOT_SPRINT_PLANNING in text


# --------------------------------------------------------------------------
# the docs the page is described in
# --------------------------------------------------------------------------
DOC = ROOT / "docs" / "groomer.md"


def test_the_doc_carries_the_judged_vocabulary_and_names_the_audit_card():
    text = DOC.read_text(encoding="utf-8")
    for phrase in ("could not rank", "trigger", "evidence", "DRE-3151"):
        assert phrase in text, f"docs/groomer.md never says {phrase!r}"
    lowered = text.lower()
    assert "three outcomes" not in lowered, (
        "the groomer's vocabulary is four answers, not three"
    )
    assert "never reads a model" not in lowered
    assert "not bodies" in lowered or "not their bodies" in lowered, (
        "the judgement's own limit — the census is titles, ids, labels, ages "
        "and first lines — is not stated in What it cannot see"
    )
