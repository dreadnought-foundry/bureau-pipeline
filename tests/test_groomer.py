"""The groomer proposes with judgement, and the rules still constrain it
(DRE-3150).

Since DRE-4725 the model's ranked read neither fills nor orders the batch —
the rules do, Urgent, High, then newest first (DRE-4965). What this file holds:

  * the model's `now` order does **not** reorder the batch;
  * a **collision** orders it, a **blocker** holds a card back, an **epic
    stays one unit**, and **capacity still caps** — whatever the model ranked;
  * every row names a **reason**, a `not-now` names its **trigger**, a
    `likely-done` names its **evidence**, and a reason written in technical
    terms is refused at the write seam rather than put in front of the CEO;
  * `--no-judgement` is today's groomer, unchanged, so the audit card
    (DRE-3151) can run the two against one population;
  * the proposal says what the one call COST — the output budget it was sized
    with and whether the answer came back cut (DRE-3259) — and neither key may
    move `proposal_id`, nor put a number on the page when nothing was cut
    (DRE-3152 renders the cut itself; `tests/test_groomer_render.py` holds it).

Run: cd bureau-pipeline && python3 -m pytest tests/test_groomer.py -v
"""
from __future__ import annotations

import argparse
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

# The four fields DRE-3150 adds to every row, DRE-3764's fifth, and the block
# DRE-3150 adds to the proposal. Named once: the `--no-judgement` comparison
# strips exactly these and nothing else, so a sixth field added later cannot
# hide inside the "identical to today" claim. `reasons` is on the list
# deliberately and is `{}` on every rules-only row — the rules place a card by
# priority and age and have no labelled reasons to give it
# (tests/test_groomer_batch_reasons.py holds that one to `{}` by name, so
# stripping it here cannot hide a value appearing in it).
ADDED_ROW_KEYS = ("reason", "trigger", "evidence", "judged", "reasons")
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
    """The call seam, counted — and, since DRE-3259, sized: it takes the two
    keywords `run()` passes and records what the one call asked for."""

    def __init__(self, answer="", truncated=False):
        self.answer, self.calls, self.truncated = answer, 0, truncated
        self.budgets, self.clocks = [], []

    def __call__(self, model, prompt, *, max_tokens=None, timeout_seconds=None):
        self.calls += 1
        self.budgets.append(max_tokens)
        self.clocks.append(timeout_seconds)
        import planning_classify
        return planning_classify.Answer(text=self.answer, model="test-model",
                                        truncated=self.truncated)


def judged(cards, answer, *, model="test-model", truncated=False):
    """A judgement over `cards` from a canned answer, with no model reached."""
    rows = groom_judgement.census(cards, now=NOW)
    return groom_judgement.run(rows, PACK, call=Counter(answer, truncated),
                               model=model)


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
    # …and the one call is sized for the population it was asked about
    # (DRE-3259). A 250-card answer against the classifier's own 1,000-token
    # bound comes back cut after the first forty cards.
    budget = call.budgets[0]
    assert budget >= groom_judgement.TOKENS_PER_CARD * 250
    assert call.clocks[0] == groom_judgement.wall_clock_seconds(budget)
    assert proposal["judgement"]["output_budget"] == budget
    assert proposal["judgement"]["truncated"] is False


# --------------------------------------------------------------------------
# the rules fill the batch, newest first — not the model's order (DRE-4965)
# --------------------------------------------------------------------------
def test_the_models_order_does_not_fill_the_batch():
    cards = [card("DRE-1", days=1), card("DRE-2", days=2), card("DRE-3", days=3)]
    proposal = groomer.propose(
        cards, cycles=CYCLES, capacity=10, now=NOW,
        judgement=judged(cards, ranked(["DRE-2", "DRE-3", "DRE-1"])))
    assert positions(proposal) == ["DRE-1", "DRE-2", "DRE-3"]


def test_the_rules_only_path_orders_the_same_population_the_same_way():
    """The same population with no judgement sequences by the rules into the
    same order — proof the assertion above is the rules' order and not an
    accident of the model's."""
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
        judgement=judged(cards, ranked(["DRE-1", "DRE-2"])))
    assert positions(proposal) == ["DRE-2", "DRE-1"], (
        "the newer card of a colliding pair goes first whatever the model "
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
    cards = [card(f"DRE-{n}", days=6 - n) for n in range(1, 6)]
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
    assert "DRE-1" in dead["DRE-2"]["reason"], (
        "a card its own description condemned names what superseded it — the "
        "declaration a person wrote is not a card the read could not rank"
    )


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
    assert set(block["pack"]) == set(groom_context.SECTIONS) | {"truncated",
                                                                "unread"}
    assert block["calls"] in (0, 1)
    # DRE-3259's two keys, beside the ones DRE-3150 shipped.
    assert set(block) >= {"output_budget", "truncated"}
    assert block["output_budget"] == groom_judgement.output_budget(1)
    assert block["truncated"] is False


def test_the_answers_cut_and_the_packs_cap_are_never_read_for_each_other():
    """`judgement.truncated` is the ANSWER being cut at the budget, a bool;
    `judgement.pack.truncated` is the list of context-pack sections that were
    capped. Two facts, two types, one name apart."""
    cards = [card("DRE-1")]
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=5, now=NOW,
                               judgement=judged(cards, ranked(["DRE-1"])))
    block = proposal["judgement"]
    assert block["truncated"] is False
    assert isinstance(block["pack"]["truncated"], list)


def test_a_cut_answer_is_reported_on_the_proposal():
    cards = [card(f"DRE-{n:02d}") for n in range(10)]
    # Four whole lines and a fifth cut mid-reason: six cards the answer never
    # reached, plus the garbled one, come back unranked.
    answer = ranked([f"DRE-{n:02d}" for n in range(4)]) + "\nDRE-04 | now | it is wa"
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=10, now=NOW,
                               judgement=judged(cards, answer, truncated=True))
    block = proposal["judgement"]
    assert block["truncated"] is True
    assert block["output_budget"] == groom_judgement.output_budget(10)
    assert len(block["unranked"]) == 6
    assert "DRE-04" in block["unranked"]
    assert block["problem"] is None, (
        "a cut is not a problem — the run ranked what it read"
    )


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
    assert block["output_budget"] == 0, "no call, no budget"
    assert block["truncated"] is False


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


def test_the_rendered_proposal_names_no_ranked_read_without_a_judgement():
    """The CEO-facing text is the audit's other half. Nothing the ranked read
    produced may render on the rules-only path — the one thing that page says
    about a judgement is that there was not one (DRE-3152 owns the wording;
    `tests/test_groomer_render.py` holds it)."""
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    proposal = groomer.propose(
        golden["cards"], cycles=golden["cycles"], capacity=golden["capacity"],
        batch_cycles=golden["batch_cycles"], now=golden["now"], judgement=None)
    text = groomer.render_proposal(proposal)
    assert "What the ranked read said" not in text
    assert "Could not rank" not in text
    assert "Ranked by the rules only (judgement off)" in text


# --------------------------------------------------------------------------
# what the budget must NOT move (DRE-3259)
# --------------------------------------------------------------------------
# Computed on the fixture and pinned here: `proposal_id` digests the batch's
# cards, positions and cycles and nothing else, so neither of the two new keys
# may retire a CEO approval of the same batch. Re-pinned by DRE-4725, and
# again by DRE-4965, which moved the batch itself to newest first — and since
# the ranked read no longer orders the batch, a read that ranks every card
# `now` proposes the rules' batch, so the two ids are one.
FIXTURE_RULES_ONLY_ID = "a85999f18b18"
FIXTURE_JUDGED_ID = "a85999f18b18"


def _fixture_proposal(judgement=None):
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    return groomer.propose(
        golden["cards"], cycles=golden["cycles"], capacity=golden["capacity"],
        batch_cycles=golden["batch_cycles"], now=golden["now"],
        judgement=judgement)


def test_the_proposal_id_is_untouched_by_the_budget_keys():
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    judgement = judged(golden["cards"],
                       ranked([c["identifier"] for c in golden["cards"]]))
    assert _fixture_proposal()["id"] == FIXTURE_RULES_ONLY_ID
    assert _fixture_proposal(judgement)["id"] == FIXTURE_JUDGED_ID


def test_the_run_log_names_the_budget_and_what_the_cut_cost(monkeypatch, capsys):
    """`_build` prints the budget beside the problem line, and prints the cut
    and its count when there was one — the proposal is read by the CEO, and the
    number that explains a short answer belongs in the run log."""
    cards = [card(f"DRE-{n:02d}") for n in range(10)]
    answer = ranked([f"DRE-{n:02d}" for n in range(4)]) + "\nDRE-04 | now | it is wa"
    judgement = judged(cards, answer, truncated=True)   # BEFORE the patch below

    monkeypatch.setattr(groomer, "read_population", lambda lops, lane: cards)
    monkeypatch.setattr(groomer, "read_cycles", lambda lops: CYCLES)
    monkeypatch.setattr(groom_context, "read_pack", lambda lops: PACK)
    monkeypatch.setattr(groom_judgement, "run", lambda rows, pack: judgement)

    groomer._build(argparse.Namespace(
        lane="Intake", capacity=10, batch_cycles=1, judgement=True,
        window_days=groomer.WINDOW_DAYS,
        priority=",".join(groomer.REPO_PRIORITY)))
    err = capsys.readouterr().err
    assert str(groom_judgement.output_budget(10)) in err
    assert "6 card" in err, "the run log names how many cards the cut cost"


def test_an_answer_that_was_not_cut_says_nothing_about_a_budget():
    """DRE-3152 renders the budget only when the answer came back CUT. A whole
    answer costs the CEO no number: the receipt exists to explain a short read,
    and a page that prints a token count on every run trains people past it.
    (The cut page itself is held by `tests/test_groomer_render.py`.)"""
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    judgement = judged(golden["cards"],
                       ranked([c["identifier"] for c in golden["cards"]]))
    assert judgement.truncated is False
    for proposal in (_fixture_proposal(), _fixture_proposal(judgement)):
        text = groomer.render_proposal(proposal)
        assert "output_budget" not in text
        assert "token" not in text.lower()
        assert "cut short" not in text.lower()


def test_the_cli_carries_the_no_judgement_switch():
    assert "--no-judgement" in (groomer.__doc__ or ""), (
        "the switch the audit card runs the comparison with is not documented "
        "where the module documents its own CLI"
    )
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.suppress(SystemExit):
        groomer.main(["propose", "--help"])
    assert "--no-judgement" in buf.getvalue()


# --------------------------------------------------------------------------
# DRE-5306 — one count of open children, read by verify and by DRE-5309
# --------------------------------------------------------------------------
def test_closed_states_are_done_canceled_and_duplicate():
    assert groomer.CLOSED_STATES == ("Done", "Canceled", "Duplicate")


def test_open_children_counts_the_children_not_in_a_closed_state():
    node = {"children": {"nodes": [
        {"identifier": "DRE-1", "state": {"name": "In Progress"}},
        {"identifier": "DRE-2", "state": {"name": "Done"}},
        {"identifier": "DRE-3", "state": {"name": "Todo"}}]}}
    assert groomer.open_children(node) == 2


def test_open_children_of_a_card_with_no_children_key_is_zero():
    assert groomer.open_children(card("DRE-1")) == 0
    assert groomer.open_children({"children": None}) == 0


# --------------------------------------------------------------------------
# DRE-5307 — a stale Urgent or High is ranked as Medium, and the page says so
# --------------------------------------------------------------------------
STALE = {"priority": "Urgent", "set_at": "2026-07-29T12:00:00Z", "days": 38}


def _stale(identifier, *, priority=1, stale=STALE, days=40):
    marked = card(identifier, priority=priority, days=days)
    marked["priority_stale"] = dict(stale)
    return marked


def _unread(identifier, why, *, priority=1, days=40):
    kept = card(identifier, priority=priority, days=days)
    kept["priority_unread"] = why
    return kept


def test_priority_reads_a_stale_card_as_unprioritised():
    assert groomer._priority(card("DRE-1", priority=1)) == groomer.URGENT
    assert groomer._priority(_stale("DRE-1")) == 0
    assert groomer._priority(_stale("DRE-2", priority=2)) == 0


def test_propose_writes_one_stale_priorities_row_per_stale_card():
    cards = [_stale("DRE-2702"),
             _stale("DRE-2564", priority=2, stale={
                 "priority": "High", "set_at": "2026-08-01T12:00:00Z",
                 "days": 35}),
             card("DRE-5400", priority=3, days=0)]
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=3, now=NOW)
    assert proposal["stale_priorities"] == [
        {"identifier": "DRE-2702", "priority": "Urgent",
         "set_at": "2026-07-29T12:00:00Z", "days": 38},
        {"identifier": "DRE-2564", "priority": "High",
         "set_at": "2026-08-01T12:00:00Z", "days": 35},
    ]
    # Ranked as Medium: the card created today goes ahead of both.
    assert positions(proposal)[0] == "DRE-5400"


def test_propose_names_the_cards_kept_unread_by_reason():
    why = "read budget of 40 spent"
    cards = [_unread("DRE-901", why), _unread("DRE-900", why),
             _unread("DRE-3530", "Linear said no"), card("DRE-5400")]
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=4, now=NOW)
    assert proposal["priorities_unread"] == {
        "Linear said no": ["DRE-3530"], why: ["DRE-900", "DRE-901"]}
    assert "stale_priorities" not in proposal


def test_a_proposal_with_nothing_stale_or_unread_carries_neither_key():
    proposal = groomer.propose([card("DRE-1", priority=1, days=40)],
                               cycles=CYCLES, capacity=3, now=NOW)
    assert "stale_priorities" not in proposal
    assert "priorities_unread" not in proposal
    assert "## Priorities re-ranked as Medium" not in groomer.render_proposal(
        proposal)


def test_the_page_says_which_priorities_were_re_ranked_and_which_were_not_read():
    cards = [_stale("DRE-2702"),
             _unread("DRE-901", "read budget of 40 spent"),
             _unread("DRE-900", "read budget of 40 spent"),
             _unread("DRE-3530", "Linear said no"),
             _unread("DRE-3681", groomer.groom_priority.VIEWER_UNREAD),
             card("DRE-5400", priority=3, days=0)]
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=6, now=NOW)
    text = groomer.render_proposal(proposal)
    assert text.count("## Priorities re-ranked as Medium") == 1
    section = text.split("## Priorities re-ranked as Medium\n", 1)[1]
    section = section.split("\n## ", 1)[0]
    lines = [line for line in section.splitlines() if line.strip()]
    assert ("- DRE-2702 — Urgent set on 2026-07-29, 38 days ago, not "
            "re-confirmed — ranked as Medium") in lines
    assert ("- Kept their priority, not read — read budget of 40 spent: "
            "DRE-900, DRE-901") in lines
    assert "- Kept their priority, not read — Linear said no: DRE-3530" in lines
    assert ("- Kept their priority, not read — the pipeline's own Linear "
            "identity could not be read: DRE-3681") in lines
    # After the order paragraph, and the batch table still reads back whole:
    # the drain parses it out of its own section.
    assert text.index("## The batch, in order") < text.index(
        "## Priorities re-ranked as Medium")
    parsed = groomer.parse_proposal_comment(groomer.proposal_comment(proposal))
    assert [row["identifier"] for row in parsed["batch"]] == positions(proposal)


def test_the_section_prints_for_unread_cards_alone():
    proposal = groomer.propose([_unread("DRE-3530", "Linear said no")],
                               cycles=CYCLES, capacity=3, now=NOW)
    text = groomer.render_proposal(proposal)
    assert "## Priorities re-ranked as Medium" in text
    assert "ranked as Medium\n" not in text.split(
        "## Priorities re-ranked as Medium", 1)[1].split("\n## ", 1)[0]


def _build_args(**extra):
    args = dict(lane="Intake", capacity=3, batch_cycles=1, judgement=False,
                window_days=groomer.WINDOW_DAYS, keep_answer=None,
                post=None, card=None, hold_repo=[], verify=False,
                priority=",".join(groomer.REPO_PRIORITY))
    args.update(extra)
    return argparse.Namespace(**args)


def _patch_build(monkeypatch, cards):
    import groom_priority
    seen = []

    def annotate(on_offer, *, lops, now, verifier=None):
        seen.append(([c["identifier"] for c in on_offer], lops, now))
        for c in on_offer:
            if c["identifier"] == "DRE-2702":
                c["priority_stale"] = dict(STALE)
        return {"stale": ["DRE-2702"], "unread": {}}

    monkeypatch.setattr(groomer, "_now", lambda: NOW)
    monkeypatch.setattr(groomer, "read_population", lambda lops, l: list(cards))
    monkeypatch.setattr(groomer, "read_cycles", lambda lops: CYCLES)
    monkeypatch.setattr(groom_priority, "annotate", annotate)
    return seen


def test_build_annotates_the_on_offer_cards_before_proposing(monkeypatch):
    cards = [card("DRE-2702", priority=1, days=40),
             card("DRE-5400", priority=3, days=0)]
    seen = _patch_build(monkeypatch, cards)
    proposal = groomer._build(_build_args())
    assert seen == [(["DRE-2702", "DRE-5400"], groomer.linear_ops, NOW)]
    assert [r["identifier"] for r in proposal["stale_priorities"]] == [
        "DRE-2702"]
    assert positions(proposal)[0] == "DRE-5400"


def test_build_the_verified_proposal_sees_the_annotation_too(monkeypatch):
    import groom_verify
    cards = [card("DRE-2702", priority=1, days=40),
             card("DRE-5400", priority=3, days=0)]
    _patch_build(monkeypatch, cards)

    def broken(*a, **k):
        raise RuntimeError("the check is down")

    monkeypatch.setattr(groom_verify, "check", broken)
    proposal = groomer._build(_build_args(verify=True))
    assert "verification" in proposal
    assert [r["identifier"] for r in proposal["stale_priorities"]] == [
        "DRE-2702"]
    assert positions(proposal)[0] == "DRE-5400"


# --------------------------------------------------------------------------
# no proposal cancels an epic with an open child (DRE-5309)
# --------------------------------------------------------------------------
# DRE-4633's shape on 2026-09-29: a live parent epic in Intake whose children
# were still being built, put on the Cancel list by a match on another card's
# text. Its children are not in Intake, so they are not in the population —
# only the epic's own `children` field says it has them.
EPIC = "DRE-4633"


def epic(identifier=EPIC, children=(("DRE-4634", "Done"),
                                    ("DRE-4635", "In Progress")), **kw):
    """`card`, with the `children` field `POPULATION_QUERY` reads."""
    c = card(identifier, **kw)
    c["children"] = {"nodes": [{"identifier": i, "state": {"name": s}}
                               for i, s in children]}
    return c


def _epic_lane(**kw):
    """The epic newest, so it is in the morning's set, and three cards behind
    it — capacity 3 puts the epic and DRE-1, DRE-2 in the set."""
    return [epic(days=0, **kw)] + [card(f"DRE-{n}", days=n)
                                   for n in range(1, 4)]


def _refused(proposal):
    return {r["identifier"]: r for r in proposal["cancels_refused"]}


def _assert_refused_onto_planning(proposal, source):
    refusal = "DRE-4633 is an epic with 1 open child"
    assert EPIC not in [r["identifier"] for r in proposal["outcomes"]["dead"]], (
        "an epic with an open child is on the Cancel list")
    assert EPIC in positions(proposal), "the refused card lost its slot"
    row = next(r for r in proposal["outcomes"]["now"]
               if r["identifier"] == EPIC)
    assert row["reason"] == refusal
    assert row["open_children"] == 1
    assert _refused(proposal)[EPIC] == {"identifier": EPIC,
                                        "refusal": refusal, "source": source}
    seq = {r["identifier"]: r for r in proposal["sequence"]}
    assert seq[EPIC]["outcome"] == "now" and seq[EPIC]["reason"] == refusal


def test_cancel_refusal_reads_the_open_children_count():
    assert groomer.cancel_refusal({"identifier": "DRE-9", "open_children": 0}) is None
    assert groomer.cancel_refusal({"identifier": "DRE-9"}) is None
    assert (groomer.cancel_refusal({"identifier": "DRE-9", "open_children": 1})
            == "DRE-9 is an epic with 1 open child")
    assert (groomer.cancel_refusal({"identifier": "DRE-9", "open_children": 2})
            == "DRE-9 is an epic with 2 open children")


def test_sequence_writes_the_open_children_count_on_every_row():
    rows = groomer.sequence(_epic_lane(description="Superseded by: DRE-4700"))
    by_id = {r["identifier"]: r for r in rows}
    assert by_id[EPIC]["open_children"] == 1
    assert all(by_id[f"DRE-{n}"]["open_children"] == 0 for n in range(1, 4))


def test_a_superseded_line_on_an_epic_with_an_open_child_is_refused():
    """The description's `Superseded by: DRE-M`, DRE-M Done — and the epic
    still has a child In Progress."""
    proposal = groomer.propose(
        _epic_lane(description="Superseded by: DRE-4700"),
        cycles=CYCLES, capacity=3, now=NOW)
    _assert_refused_onto_planning(proposal, groomer.DEAD_FROM_LINE)
    for name in ("now", "not-now", "dead"):
        for row in proposal["outcomes"][name]:
            assert "open_children" in row, f"a {name} row has no count"
    assert all("open_children" in r for r in proposal["sequence"])


def test_a_likely_done_from_the_ranked_read_on_an_epic_with_an_open_child_is_refused():
    cards = _epic_lane()
    answer = "\n".join([
        f"{EPIC} | likely-done | a merge already did it | pull request 274",
        ranked(["DRE-1", "DRE-2", "DRE-3"])])
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=3, now=NOW,
                               judgement=judged(cards, answer))
    _assert_refused_onto_planning(proposal, groomer.DEAD_FROM_JUDGEMENT)


def test_the_checks_evidence_on_an_epic_with_an_open_child_is_refused():
    """A sibling saying it covers the epic, Done — the pre-post check's
    evidence, applied inside `propose(verified=…)`."""
    from test_groom_verify import FakeGh, FakeLinear, OWNERS, other
    linear = FakeLinear({EPIC: {"siblings": [
        other(EPIC, "Intake"),
        other("DRE-4700", "Done", f"This card supersedes {EPIC}.")]}})
    proposal = groomer.verify_proposal(
        _epic_lane(), dict(cycles=CYCLES, capacity=3, now=NOW),
        lops=linear, run=FakeGh(), owners=OWNERS)
    _assert_refused_onto_planning(proposal, groomer.DEAD_FROM_CHECK)
    text = groomer.render_proposal(proposal)
    assert f"Moved to Cancel: {EPIC}" not in text


def test_an_epic_whose_children_are_all_closed_is_cancelable_as_before():
    cards = _epic_lane(children=(("DRE-4634", "Done"),
                                 ("DRE-4635", "Canceled"),
                                 ("DRE-4636", "Duplicate")),
                       description="Superseded by: DRE-4700")
    assert groomer.open_children(cards[0]) == 0
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=3, now=NOW)
    dead = [r for r in proposal["outcomes"]["dead"] if r["identifier"] == EPIC]
    assert dead and dead[0]["source"] == groomer.DEAD_FROM_LINE
    assert dead[0]["open_children"] == 0
    assert proposal["cancels_refused"] == []
    assert "## Cancels refused" not in groomer.render_proposal(proposal)


def test_a_refused_card_outside_the_batch_stays_not_now_and_is_not_listed():
    """A writer's Cancel is acted on only inside the morning's set: outside
    it the card was never going on the Cancel list, so nothing was refused."""
    cards = [card(f"DRE-{n}", days=n) for n in range(1, 4)] + [
        epic(days=9, description="Superseded by: DRE-4700")]
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=3, now=NOW)
    later = {r["identifier"] for r in proposal["outcomes"]["not-now"]}
    assert EPIC in later
    assert proposal["cancels_refused"] == []


def test_the_page_lists_each_refused_cancel_after_the_cancel_table():
    cards = _epic_lane(description="Superseded by: DRE-4700") + [
        card("DRE-77", days=0, description="Superseded by: DRE-4701")]
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=3, now=NOW)
    text = groomer.render_proposal(proposal)
    assert "## Cancels refused" in text
    assert text.index(groomer.CANCEL_HEADING) < text.index("## Cancels refused")
    section = text.split("## Cancels refused", 1)[1].split("\n## ", 1)[0]
    lines = [ln for ln in section.splitlines() if ln.startswith("- ")]
    assert len(lines) == 1
    assert "DRE-4633 is an epic with 1 open child" in lines[0]
    assert "Superseded by:" in lines[0]
    assert groomer.DEAD_FROM_LINE in lines[0]


def test_the_refusal_moves_no_proposal_id_beyond_the_lists():
    """`proposal_id` digests the two lists and nothing else: the new keys do
    not move it, only the lists the refusal changed."""
    proposal = groomer.propose(
        _epic_lane(description="Superseded by: DRE-4700"),
        cycles=CYCLES, capacity=3, now=NOW)
    assert proposal["cancels_refused"], "nothing was refused"
    stripped = {**proposal, "cancels_refused": [], "sequence": []}
    assert groomer.proposal_id(stripped) == proposal["id"]


# --------------------------------------------------------------------------
# the population read carries the children, at a page Linear will answer
# --------------------------------------------------------------------------
class PagedLinear:
    """Linear's paging, honouring the page size the QUERY asks for."""

    def __init__(self, nodes):
        self.nodes, self.pages = nodes, []

    def gql(self, query, variables=None):
        import re
        size = int(re.search(r"issues\(first: (\d+)", query).group(1))
        after = (variables or {}).get("after")
        start = int(after) if after else 0
        page = self.nodes[start:start + size]
        self.pages.append(len(page))
        end = start + len(page)
        return {"issues": {"nodes": page, "pageInfo": {
            "hasNextPage": end < len(self.nodes), "endCursor": str(end)}}}

    def gql_paged(self, query, variables=None, *, connection="issues"):
        import linear_ops
        real = linear_ops.gql
        linear_ops.gql = self.gql
        try:
            return linear_ops.gql_paged(query, variables, connection=connection)
        finally:
            linear_ops.gql = real


def test_the_population_query_pages_at_fifty_and_carries_the_children():
    assert groomer.POPULATION_PAGE == 50
    assert "issues(first: 50, after: $after" in groomer.POPULATION_QUERY
    assert ("children(first: 50) { nodes { identifier state { name } } }"
            in groomer.POPULATION_QUERY)


def test_a_120_card_lane_is_read_in_three_pages_and_every_row_counts_children():
    nodes = [epic(f"DRE-{n:04d}", children=(("DRE-9", "In Progress"),)
                  if n % 2 else ()) for n in range(120)]
    fake = PagedLinear(nodes)
    got = groomer.read_population(fake, lane="Intake")
    assert fake.pages == [50, 50, 20]
    assert len(got) == 120
    rows = groomer.sequence(got)
    assert len(rows) == 120
    assert all(r["open_children"] == (1 if int(r["identifier"][4:]) % 2 else 0)
               for r in rows)
