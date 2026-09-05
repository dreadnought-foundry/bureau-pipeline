"""The groomer proposes with judgement, and the rules still constrain it
(DRE-3150).

The model's ranked read fills the batch; it does not get to break the batch.
That distinction is the whole of this file:

  * the model's `now` set fills the batch **in the model's order**;
  * then a **collision** re-orders it, a **blocker** holds a card back, an
    **epic stays one unit**, and **capacity still caps** — the rules constrain
    the read, they never re-rank it;
  * every row names a **reason**, a `not-now` names its **trigger**, a
    `likely-done` names its **evidence**, and a reason written in technical
    terms is refused at the write seam rather than put in front of the CEO;
  * `--no-judgement` is today's groomer, unchanged, so the audit card
    (DRE-3151) can run the two against one population.

Run: cd bureau-pipeline && python3 -m pytest tests/test_groomer.py -v
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")

import groom_context  # noqa: E402
import groom_judgement  # noqa: E402
import groomer  # noqa: E402

NOW = "2026-09-05T12:00:00Z"
BASE = datetime.fromisoformat(NOW.replace("Z", "+00:00"))
PACK = groom_context.pack(now=NOW)
GOLDEN = ROOT / "tests" / "fixtures" / "groom_rules_only_proposal.json"

# The four fields DRE-3150 adds to every row, and the block it adds to the
# proposal. Named once: the `--no-judgement` comparison strips exactly these
# and nothing else, so a fifth field added later cannot hide inside the
# "identical to today" claim.
ADDED_ROW_KEYS = ("reason", "trigger", "evidence", "judged")
ADDED_DEAD_KEYS = ADDED_ROW_KEYS + ("source",)


def ago(days: float) -> str:
    return (BASE - timedelta(days=days)).isoformat().replace("+00:00", "Z")


def card(identifier, *, repo="portico", parent=None, days=1, description="",
         title=None, priority=0):
    return {
        "identifier": identifier,
        "title": title or f"{identifier} does a thing",
        "description": description,
        "createdAt": ago(days),
        "priority": priority,
        "state": {"name": "Intake"},
        "labels": {"nodes": [{"name": f"repo:{repo}"}, {"name": "agent:engineer"}]},
        "parent": {"identifier": parent, "title": f"[EPIC] {parent}"} if parent else None,
        "project": None,
        "cycle": None,
        "inverseRelations": {"nodes": []},
    }


CYCLES = [
    {"number": 12, "id": "cyc-12", "startsAt": "2026-09-07T07:00:00.000Z",
     "endsAt": "2026-09-21T07:00:00.000Z"},
    {"number": 13, "id": "cyc-13", "startsAt": "2026-09-21T07:00:00.000Z",
     "endsAt": "2026-10-05T07:00:00.000Z"},
]


class Counter:
    def __init__(self, answer=""):
        self.answer, self.calls = answer, 0

    def __call__(self, model, prompt):
        self.calls += 1
        import planning_classify
        return planning_classify.Answer(text=self.answer, model="test-model")


def judged(cards, answer, *, model="test-model"):
    """A judgement over `cards` from a canned answer, with no model reached."""
    rows = groom_judgement.census(cards, now=NOW)
    return groom_judgement.run(rows, PACK, call=Counter(answer), model=model)


def ranked(order, outcome="now", reason="the model wanted it", pointer=None):
    return "\n".join(
        " | ".join([cid, outcome, reason] + ([pointer] if pointer else []))
        for cid in order
    )


def positions(proposal):
    return [row["identifier"] for row in
            sorted(proposal["outcomes"]["now"], key=lambda r: r["position"])]


# --------------------------------------------------------------------------
# one call per run
# --------------------------------------------------------------------------
def test_one_model_call_per_propose_run_over_a_250_card_population():
    cards = [card(f"DRE-{n:03d}") for n in range(250)]
    rows = groom_judgement.census(cards, now=NOW)
    call = Counter(ranked([r["identifier"] for r in rows]))
    judgement = groom_judgement.run(rows, PACK, call=call, model="test-model")
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=10, now=NOW,
                               judgement=judgement)
    assert call.calls == 1
    assert proposal["judgement"]["calls"] == 1
    assert proposal["judgement"]["enabled"] is True
    assert proposal["population"] == 250


# --------------------------------------------------------------------------
# the model fills the batch, in the model's order
# --------------------------------------------------------------------------
def test_the_models_order_fills_the_batch():
    cards = [card("DRE-1", days=1), card("DRE-2", days=2), card("DRE-3", days=3)]
    proposal = groomer.propose(
        cards, cycles=CYCLES, capacity=10, now=NOW,
        judgement=judged(cards, ranked(["DRE-3", "DRE-1", "DRE-2"])))
    assert positions(proposal) == ["DRE-3", "DRE-1", "DRE-2"]


def test_the_rules_only_path_does_not_use_the_models_order():
    """The same population with no judgement sequences by the rules — proof
    the assertion above is testing the model's order and not the fixture's."""
    cards = [card("DRE-1", days=1), card("DRE-2", days=2), card("DRE-3", days=3)]
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=10, now=NOW)
    assert positions(proposal) == ["DRE-1", "DRE-2", "DRE-3"]


# --------------------------------------------------------------------------
# the rules constrain the read
# --------------------------------------------------------------------------
def test_a_collision_overrides_the_models_order():
    cards = [card("DRE-1", days=9, description="edits `alpha.ts`"),
             card("DRE-2", days=1, description="also edits `alpha.ts`")]
    proposal = groomer.propose(
        cards, cycles=CYCLES, capacity=10, now=NOW,
        judgement=judged(cards, ranked(["DRE-2", "DRE-1"])))
    assert positions(proposal) == ["DRE-1", "DRE-2"], (
        "the older card of a colliding pair goes first whatever the model "
        "ranked — a merge conflict is not a preference"
    )
    assert proposal["collisions"]["pairs"], "the fixture stopped colliding"


def test_a_blocker_holds_whatever_the_model_ranked():
    cards = [card("DRE-1", days=1, description="Blocked by: DRE-2"),
             card("DRE-2", days=2)]
    proposal = groomer.propose(
        cards, cycles=CYCLES, capacity=10, now=NOW,
        judgement=judged(cards, ranked(["DRE-1", "DRE-2"])))
    assert positions(proposal) == ["DRE-2", "DRE-1"]


def test_an_epic_stays_one_unit_when_the_model_splits_it():
    cards = [card("DRE-1", days=1, parent="DRE-900"),
             card("DRE-2", days=2, parent="DRE-900"),
             card("DRE-3", days=3)]
    proposal = groomer.propose(
        cards, cycles=CYCLES, capacity=10, now=NOW,
        judgement=judged(cards, ranked(["DRE-1", "DRE-3", "DRE-2"])))
    order = positions(proposal)
    assert abs(order.index("DRE-1") - order.index("DRE-2")) == 1, (
        "an epic's children are one deliverable; the model may not spread "
        "them across a batch"
    )


def test_capacity_still_caps_the_batch():
    cards = [card(f"DRE-{n:02d}", days=n % 5 + 1) for n in range(20)]
    proposal = groomer.propose(
        cards, cycles=CYCLES, capacity=6, batch_cycles=1, now=NOW,
        judgement=judged(cards, ranked([f"DRE-{n:02d}" for n in range(20)])))
    assert len(proposal["outcomes"]["now"]) == 6, (
        "the model ranked twenty cards `now`; capacity is still the cap"
    )
    assert len(proposal["outcomes"]["not-now"]) == 14


# --------------------------------------------------------------------------
# a reason per card
# --------------------------------------------------------------------------
def test_every_row_of_every_outcome_names_a_reason():
    cards = [card(f"DRE-{n}", days=n) for n in range(1, 9)]
    cards.append(card("DRE-90", description="Superseded by: DRE-1"))
    answer = "\n".join([
        ranked(["DRE-1", "DRE-2"]),
        "DRE-3 | not-now | the console work has to land first | when DRE-1 merges",
        "DRE-4 | likely-done | a merge already did it | https://github.com/x/y/pull/9",
        ranked(["DRE-5", "DRE-6", "DRE-7", "DRE-8"]),
    ])
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=3, now=NOW,
                               judgement=judged(cards, answer))
    for bucket in ("now", "not-now", "dead"):
        for row in proposal["outcomes"][bucket]:
            assert row["reason"], f"{bucket} row {row['identifier']} names no reason"
    for row in proposal["sequence"]:
        assert row["reason"], f"sequence row {row['identifier']} names no reason"


def test_a_not_now_row_names_a_trigger():
    cards = [card(f"DRE-{n}", days=n) for n in range(1, 6)]
    answer = "\n".join([
        ranked(["DRE-1"]),
        "DRE-2 | not-now | nothing reads it yet | when the console lands",
        ranked(["DRE-3", "DRE-4", "DRE-5"]),
    ])
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=2, now=NOW,
                               judgement=judged(cards, answer))
    later = {row["identifier"]: row for row in proposal["outcomes"]["not-now"]}
    assert later["DRE-2"]["trigger"] == "when the console lands"
    for identifier, row in later.items():
        assert row["trigger"], f"{identifier} is 'not now' and names no trigger"


def test_a_likely_done_row_lands_dead_with_the_models_evidence():
    cards = [card("DRE-1"), card("DRE-2", description="Superseded by: DRE-1")]
    answer = "\n".join([
        ranked(["DRE-1"]),
    ])
    answer = ("DRE-1 | likely-done | the merge already did it | "
              "https://github.com/x/y/pull/9")
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=5, now=NOW,
                               judgement=judged(cards, answer))
    dead = {row["identifier"]: row for row in proposal["outcomes"]["dead"]}
    assert dead["DRE-1"]["source"] == "judgement"
    assert dead["DRE-1"]["evidence"] == "https://github.com/x/y/pull/9"
    assert dead["DRE-1"]["superseded_by"] is None
    assert dead["DRE-1"]["judged"] is True
    # the regex's own recommendation is untouched and still says where it came from
    assert dead["DRE-2"]["source"] == "superseded-line"
    assert dead["DRE-2"]["superseded_by"] == "DRE-1"
    assert dead["DRE-2"]["judged"] is False


def test_an_unranked_card_carries_the_exact_sentence_and_is_listed():
    cards = [card("DRE-1"), card("DRE-2")]
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=5, now=NOW,
                               judgement=judged(cards, ranked(["DRE-1"])))
    row = next(r for r in proposal["sequence"] if r["identifier"] == "DRE-2")
    assert row["reason"] == "could not rank — needs a person"
    assert row["judged"] is False
    assert proposal["judgement"]["unranked"] == ["DRE-2"]


# --------------------------------------------------------------------------
# the write seam refuses a reason written in code
# --------------------------------------------------------------------------
def test_a_reason_carrying_a_path_a_diff_or_a_command_is_refused():
    cards = [card("DRE-1"), card("DRE-2"), card("DRE-3")]
    answer = "\n".join([
        "DRE-1 | now | it patches scripts/groomer.py",
        "DRE-2 | now | run python3 scripts/groomer.py propose first",
        "DRE-3 | now | the console work needs it",
    ])
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=5, now=NOW,
                               judgement=judged(cards, answer))
    rows = {r["identifier"]: r for r in proposal["outcomes"]["now"]}
    withheld = "reason withheld — written in technical terms; see the run log"
    assert rows["DRE-1"]["reason"] == withheld
    assert rows["DRE-2"]["reason"] == withheld
    assert rows["DRE-3"]["reason"] == "the console work needs it"
    assert proposal["judgement"]["withheld"] == ["DRE-1", "DRE-2"]


def test_no_reason_in_the_proposal_survives_the_plain_english_guard():
    import planning_escalation
    cards = [card("DRE-1"), card("DRE-2")]
    answer = "\n".join([
        "DRE-1 | now | see the diff --git a/x b/x",
        "DRE-2 | not-now | it waits on `handler()` | when the api lands",
    ])
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=5, now=NOW,
                               judgement=judged(cards, answer))
    for row in proposal["sequence"]:
        assert planning_escalation.refusal(row["reason"]) is None, (
            f"{row['identifier']}'s reason would not be shown to the CEO"
        )


# --------------------------------------------------------------------------
# the judgement block the sibling cards read
# --------------------------------------------------------------------------
def test_the_judgement_block_carries_every_field_the_contract_names():
    cards = [card("DRE-1")]
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=5, now=NOW,
                               judgement=judged(cards, ranked(["DRE-1"])))
    block = proposal["judgement"]
    assert set(block) >= {"enabled", "calls", "model_asked", "model_answered",
                          "receipt", "pack", "unranked", "withheld"}
    assert block["model_asked"] == "test-model"
    assert block["model_answered"] == "test-model"
    assert block["receipt"] == "test-model (asked) / test-model (answered)"
    assert set(block["pack"]) == set(groom_context.SECTIONS) | {"truncated"}
    assert block["calls"] in (0, 1)


def test_a_run_that_could_not_rank_says_so_in_the_proposal():
    cards = [card("DRE-1"), card("DRE-2")]
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=5, now=NOW,
                               judgement=judged(cards, "I'd rather not."))
    assert proposal["judgement"]["unranked"] == ["DRE-1", "DRE-2"]
    assert proposal["judgement"]["problem"]
    assert proposal["judgement"]["problem"] in groomer.render_proposal(proposal)


def test_a_reason_changing_does_not_retire_an_approval():
    """`proposal_id` digests the batch's cards, positions and cycles and
    nothing else — a re-run whose reasons read differently must not invalidate
    a CEO approval of the same batch (DRE-3150's contract)."""
    cards = [card("DRE-1"), card("DRE-2")]
    first = groomer.propose(cards, cycles=CYCLES, capacity=5, now=NOW,
                            judgement=judged(cards, ranked(["DRE-1", "DRE-2"],
                                                           reason="one reading")))
    second = groomer.propose(cards, cycles=CYCLES, capacity=5, now=NOW,
                             judgement=judged(cards, ranked(["DRE-1", "DRE-2"],
                                                            reason="quite another")))
    assert first["id"] == second["id"]
    assert (first["outcomes"]["now"][0]["reason"]
            != second["outcomes"]["now"][0]["reason"])


# --------------------------------------------------------------------------
# --no-judgement is today's groomer
# --------------------------------------------------------------------------
def _rules_only_view(proposal: dict) -> dict:
    """The proposal with exactly DRE-3150's additions removed."""
    stripped = {k: v for k, v in proposal.items() if k != "judgement"}
    def drop(rows, keys):
        return [{k: v for k, v in row.items() if k not in keys} for row in rows]
    stripped["sequence"] = drop(proposal["sequence"], ADDED_DEAD_KEYS)
    stripped["outcomes"] = {
        "now": drop(proposal["outcomes"]["now"], ADDED_ROW_KEYS),
        "not-now": drop(proposal["outcomes"]["not-now"], ADDED_ROW_KEYS),
        "dead": drop(proposal["outcomes"]["dead"], ADDED_DEAD_KEYS),
    }
    return stripped


def test_no_judgement_is_byte_for_byte_todays_proposal():
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    proposal = groomer.propose(
        golden["cards"], cycles=golden["cycles"], capacity=golden["capacity"],
        batch_cycles=golden["batch_cycles"], now=golden["now"], judgement=None)
    assert _rules_only_view(proposal) == golden["proposal"], (
        "the rules-only path changed; the audit card cannot compare the two "
        "readings on one population if one of them moved"
    )


def test_no_judgement_makes_no_call_and_says_it_did_not():
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    proposal = groomer.propose(
        golden["cards"], cycles=golden["cycles"], capacity=golden["capacity"],
        batch_cycles=golden["batch_cycles"], now=golden["now"], judgement=None)
    block = proposal["judgement"]
    assert block["enabled"] is False
    assert block["calls"] == 0
    assert block["model_asked"] is None and block["model_answered"] is None
    assert block["unranked"] == [] and block["withheld"] == []


def test_the_rules_only_rows_are_marked_unjudged_and_still_name_a_reason():
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    proposal = groomer.propose(
        golden["cards"], cycles=golden["cycles"], capacity=golden["capacity"],
        batch_cycles=golden["batch_cycles"], now=golden["now"], judgement=None)
    for row in proposal["sequence"]:
        assert row["judged"] is False
        assert row["reason"], f"{row['identifier']} names no reason"
    for row in proposal["outcomes"]["not-now"]:
        assert row["trigger"], f"{row['identifier']} names no trigger"


def test_the_rendered_proposal_is_unchanged_without_a_judgement():
    """The CEO-facing text is the audit's other half. A judgement section that
    rendered on the rules-only path would make the two readings differ on the
    page as well as in the JSON."""
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    proposal = groomer.propose(
        golden["cards"], cycles=golden["cycles"], capacity=golden["capacity"],
        batch_cycles=golden["batch_cycles"], now=golden["now"], judgement=None)
    assert "judgement" not in groomer.render_proposal(proposal).lower()


def test_the_cli_carries_the_no_judgement_switch():
    parser_help = groomer.main.__doc__ or ""
    assert "--no-judgement" in (groomer.__doc__ or "") or parser_help
    import argparse
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.suppress(SystemExit):
        groomer.main(["propose", "--help"])
    assert "--no-judgement" in buf.getvalue()
