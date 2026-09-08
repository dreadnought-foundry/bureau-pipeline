"""The ranked read accounts for every card, and keeps what it read (DRE-3331).

Run 34185093277 (2026-09-07, the first proposal a model answered) said
`Ranked by claude-fable-5-1 … in 1 call over 260 cards` and put 204 of them
under `could not rank — needs a person`. Nothing in that sentence was false and
nothing in it was the finding: the model had answered for all 260, the CLI had
continued the answer across four API requests, and the seam read the last one.

Three things this file pins so that shape can never again read as a clean run:

  * **the budget covers thinking.** `CLAUDE_CODE_MAX_OUTPUT_TOKENS` bounds the
    whole response, thinking included — the reproduction spent 36,402 of its
    46,640 output tokens thinking, over 263 cards, and the first of its three
    requests wrote no text at all. A budget sized for eighty tokens of text a
    card is a budget the model has spent before it writes a line.
  * **`unranked` is three facts, not one.** The model may SAY `unranked` (the
    brief allows it), the answer may never REACH the card, or the line may be
    GARBLED. The card's reason reads the same in all three — DRE-3150's contract
    — and the run's accounting says which, so 56 ranked of 260 is a number on
    the receipt and not a page the CEO has to count.
  * **the answer is kept.** The run that found this kept nothing, and the raw
    answer had to be bought again to see it.

Run: cd bureau-pipeline && python3 -m pytest tests/test_groom_judgement_accounting.py -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import groom_context  # noqa: E402
import groom_judgement  # noqa: E402
import planning_classify  # noqa: E402

NOW = "2026-09-05T12:00:00Z"
PACK = groom_context.pack(now=NOW)


def card(identifier, *, days=1, description="a body\nwith two lines"):
    return {"identifier": identifier, "title": f"{identifier} does a thing",
            "description": description, "priority": 0,
            "createdAt": "2026-09-04T12:00:00Z",
            "labels": {"nodes": [{"name": "repo:portico"}]}}


def line(identifier, outcome, reason, pointer=None):
    parts = [identifier, outcome, reason] + ([pointer] if pointer else [])
    return " | ".join(parts)


class Call:
    def __init__(self, answer="", truncated=False, continuations=0):
        self.answer, self.truncated = answer, truncated
        self.continuations = continuations
        self.budgets = []

    def __call__(self, model, prompt, *, max_tokens=None, timeout_seconds=None):
        self.budgets.append(max_tokens)
        return planning_classify.Answer(text=self.answer, model="test-model",
                                        truncated=self.truncated,
                                        continuations=self.continuations)


# --------------------------------------------------------------------------- #
# the budget covers thinking                                                   #
# --------------------------------------------------------------------------- #

def test_the_budget_carries_a_thinking_allowance_per_card():
    """Measured on 2026-09-07: 36,402 thinking tokens over 263 cards, about
    140 a card, beside about 40 tokens of text a line. The text number stays
    at eighty (the brief tells the model that is the measure); the thinking
    number is new and is the larger half."""
    assert groom_judgement.TOKENS_PER_CARD == 80
    assert groom_judgement.THINKING_PER_CARD == 140
    assert groom_judgement.output_budget(263) == \
        500 + (80 + 140) * 263


def test_the_ceiling_is_where_one_request_still_fits_the_lane():
    """64,000 is under the smallest per-model maximum the installed CLI reports
    for any rung of the planner ladder (`modelUsage.maxOutputTokens` for
    claude-fable-5-1 on 2.1.263 reads 64000), and it holds 288 cards — the
    263-card lane this was measured on, with room."""
    assert groom_judgement.OUTPUT_CEILING == 64000
    assert groom_judgement.CEILING_CARDS == (64000 - 500) // (80 + 140)
    assert groom_judgement.CEILING_CARDS >= 263


def test_the_floor_still_holds_for_a_small_lane():
    assert groom_judgement.output_budget(1) == groom_judgement.OUTPUT_FLOOR


# --------------------------------------------------------------------------- #
# the accounting                                                               #
# --------------------------------------------------------------------------- #

def _rows(n):
    return groom_judgement.census([card(f"DRE-{i:03d}") for i in range(n)], now=NOW)


def test_the_three_unranked_facts_are_counted_apart():
    rows = _rows(6)
    answer = "\n".join([
        line("DRE-000", "now", "wanted"),
        line("DRE-001", "not-now", "later", "the trigger"),
        line("DRE-002", "unranked", "cannot tell"),        # the model SAID so
        line("DRE-003", "not-now", "later"),               # no trigger: garbled
        "DRE-004 | maybe | a word the vocabulary lacks",   # garbled
        # DRE-005 never mentioned: omitted
    ])
    verdicts, accounting = groom_judgement.parse_accounted(answer, rows)
    assert accounting == {"ranked": 2, "declined": 1, "garbled": 2,
                          "omitted": 1}
    for identifier in ("DRE-002", "DRE-003", "DRE-004", "DRE-005"):
        assert verdicts[identifier].outcome == "unranked"
        assert verdicts[identifier].reason == groom_judgement.UNRANKED_REASON, (
            "the card's reason is DRE-3150's sentence whatever the cause — the "
            "accounting says which, the page does not"
        )


def test_parse_still_returns_the_verdicts_alone():
    rows = _rows(2)
    verdicts = groom_judgement.parse(line("DRE-000", "now", "wanted"), rows)
    assert set(verdicts) == {"DRE-000", "DRE-001"}


def test_the_run_carries_the_accounting_and_the_ranked_count():
    rows = _rows(5)
    call = Call(answer="\n".join([
        line("DRE-000", "now", "wanted"),
        line("DRE-001", "likely-done", "it merged", "DRE-9"),
        line("DRE-002", "unranked", "cannot tell"),
    ]))
    result = groom_judgement.run(rows, PACK, call=call, model="m")
    assert result.ranked == 2
    assert result.accounting == {"ranked": 2, "declined": 1, "garbled": 0,
                                 "omitted": 2, "ceiling": 0}


def test_cards_the_ceiling_dropped_are_their_own_count():
    n = groom_judgement.CEILING_CARDS + 3
    rows = _rows(n)
    kept, _ = groom_judgement.within_ceiling(rows)
    call = Call(answer="\n".join(line(r["identifier"], "now", "wanted") for r in kept))
    result = groom_judgement.run(rows, PACK, call=call, model="m")
    assert result.accounting["ceiling"] == 3
    assert result.accounting["omitted"] == 0
    assert result.ranked == groom_judgement.CEILING_CARDS


def test_a_run_that_made_no_call_accounts_for_nothing_ranked():
    rows = _rows(3)
    result = groom_judgement.run(rows, PACK, call=Call(answer=""), model="m")
    assert result.ranked == 0
    assert result.accounting["omitted"] == 3
    assert result.problem, "an answer that ranked nothing is still said out loud"


def test_the_run_log_says_how_many_were_ranked_and_why_the_rest_were_not(capsys):
    rows = _rows(4)
    call = Call(answer="\n".join([
        line("DRE-000", "now", "wanted"),
        line("DRE-001", "unranked", "cannot tell"),
    ]))
    groom_judgement.run(rows, PACK, call=call, model="m")
    err = capsys.readouterr().err
    assert "ranked 1 of 4" in err
    assert "1 declined" in err and "2 never reached" in err


# --------------------------------------------------------------------------- #
# the answer is kept, and a continued one is said                              #
# --------------------------------------------------------------------------- #

def test_the_run_keeps_the_raw_answer_it_read():
    rows = _rows(2)
    answer = line("DRE-000", "now", "wanted") + "\nDRE-001 | now | also"
    result = groom_judgement.run(rows, PACK, call=Call(answer=answer), model="m")
    assert result.answer == answer


def test_a_continued_answer_is_reported_not_swallowed(capsys):
    rows = _rows(3)
    call = Call(answer="\n".join(line(r["identifier"], "now", "w") for r in rows),
                continuations=2)
    result = groom_judgement.run(rows, PACK, call=call, model="m")
    assert result.continuations == 2
    assert result.ranked == 3, "a joined answer ranks every card it named"
    assert "3 pieces" in capsys.readouterr().err


def test_a_run_with_no_answer_keeps_none():
    rows = _rows(1)

    def dead(model, prompt, *, max_tokens=None, timeout_seconds=None):
        raise RuntimeError("no transport")

    result = groom_judgement.run(rows, PACK, call=dead, model="m")
    assert result.answer is None
    assert result.ranked == 0
