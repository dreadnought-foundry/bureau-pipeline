"""RED-first: the groomer's memory, as one pure module (DRE-7822).

`scripts/groom_memory.py` takes the console's stored decisions (the read
door's rows) and the per-card markers on every standing card's thread, and
answers what the newest decision about each card is and, this morning, where
each card stands. The failures this file holds shut:

  * four cards marked `groom-excluded` on DRE-4488 on 2026-09-22 (DRE-4054,
    DRE-4267, DRE-4271, DRE-4272) were offered again by every DRE-4541
    proposal from 2026-09-26, because the memory read ONE standing card — so
    every standing card is read, the current one included, and the rotation
    pull request cannot forget to add the new one;
  * the memory knew no reason and no condition — so the console's tail
    grammar is read into a decision, a hold until a card is met only when that
    card is `Done`, a hold until a date only on that date, and a person's
    marker typed by hand beside the door's rows is never dropped in silence;
  * the module reads nothing and imports nothing from the groomer, so the
    groomer can import it without a cycle.

Run: cd bureau-pipeline && python3 -m pytest tests/test_groom_memory.py -v
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import groom_memory as gm  # noqa: E402

THREADS = json.loads(
    (ROOT / "tests" / "fixtures" / "groom-memory-threads.json").read_text())

REASONS = [{"key": "waits-on-other-work", "label": "Not ready: waits on other work"},
           {"key": "already-done", "label": "Already done"}]

KEYS = {"card", "decision", "reason", "reason_label", "hold_until_card",
        "hold_until_date", "actor_name", "recorded_at", "standing_card",
        "proposal_id", "source"}


def marker(card="DRE-100", kind="excluded", tail=None, *,
           at="2026-10-20T16:00:00Z", standing="DRE-4541", proposal="a4f198411b02"):
    return {"standing_card": standing, "created_at": at, "kind": kind,
            "proposal_id": proposal, "card": card, "tail": tail}


def row(card="DRE-100", decision="not-doing", *, at="2026-10-20T16:00:00Z",
        standing="DRE-4541", proposal="a4f198411b02", reason=None,
        until_card=None, until_date=None, comment=None, actor="Sid", ident=1):
    return {"id": ident, "recorded_at": at, "standing_card": standing,
            "proposal_id": proposal, "card": card, "decision": decision,
            "reason": reason, "hold_until_card": until_card,
            "hold_until_date": until_date, "actor_name": actor, "comment": comment}


def one(markers, rows=(), *, reasons=REASONS, door_problem=None):
    got = gm.remembered(list(rows), list(markers), reasons=reasons,
                        door_problem=door_problem)
    return got


# --- the standing cards ---------------------------------------------------

def test_standing_cards_are_the_five_oldest_first():
    assert gm.STANDING_CARDS == ("DRE-2840", "DRE-3013", "DRE-3327",
                                 "DRE-4488", "DRE-4541")


def test_standing_cards_never_repeats_the_current_card():
    assert gm.standing_cards("DRE-4541") == gm.STANDING_CARDS
    assert gm.standing_cards(None) == gm.STANDING_CARDS


def test_a_rotated_standing_card_is_read_beside_the_old_ones():
    got = gm.standing_cards("DRE-9999")
    assert got == gm.STANDING_CARDS + ("DRE-9999",)
    assert "DRE-4541" in got


def test_every_groomer_standing_card_in_hand_work_age_is_declared():
    """The rotation pull request adds the new card to `config/hand-work-age.json`
    so it never alarms. This test makes the same pull request append it to
    `STANDING_CARDS`, so the memory reads it the morning after it rotates out."""
    config = json.loads((ROOT / "config" / "hand-work-age.json").read_text())
    groomer_cards = [entry["card"] for entry in config["standing"]
                     if "groomer" in entry.get("why", "").lower()]
    assert groomer_cards, "no standing entry names the groomer — the test reads nothing"
    missing = [card for card in groomer_cards if card not in gm.STANDING_CARDS]
    assert not missing, (f"{missing} stand for the groomer in config/hand-work-age.json "
                         "but are not in groom_memory.STANDING_CARDS — append them")


def test_the_vocabularies_are_the_contracts():
    assert gm.DECISIONS == ("not-doing", "cancel", "hold", "put-back")
    assert gm.SOURCES == ("record", "history", "thread", "fallback")
    assert gm.MARKER_KINDS == ("excluded", "held", "added", "rewrite-accepted")


# --- the marker tail grammar ----------------------------------------------

def test_every_decision_carries_exactly_the_contract_keys():
    got = one([marker("DRE-1"), marker("DRE-2", "held", "until DRE-7")],
              [row("DRE-3", at="2026-10-01T00:00:00Z")])
    assert set(got) == {"DRE-1", "DRE-2", "DRE-3"}
    for decision in got.values():
        assert set(decision) == KEYS


def test_move_to_cancel_is_a_cancel():
    got = one([marker(tail="move to Cancel")], door_problem="unavailable")
    assert got["DRE-100"]["decision"] == "cancel"


def test_the_console_separator_is_read_as_part_of_the_tail_grammar():
    got = one([marker(tail=" — move to Cancel")], door_problem="unavailable")
    assert got["DRE-100"]["decision"] == "cancel"


def test_a_served_label_is_a_keyed_not_doing():
    got = one([marker(tail="Not ready: waits on other work")], door_problem=None)
    decision = got["DRE-100"]
    assert decision["decision"] == "not-doing"
    assert decision["reason"] == "waits-on-other-work"
    assert decision["reason_label"] == "Not ready: waits on other work"


def test_reasons_may_be_served_as_a_mapping():
    got = one([marker(tail="Already done")],
              reasons={"already-done": "Already done"})
    assert got["DRE-100"]["reason"] == "already-done"


def test_free_text_is_a_not_doing_with_its_words_kept():
    words = "Replaced by DRE-5942 (Done 2026-10-06)"
    got = one([marker(tail=words)])
    assert got["DRE-100"]["decision"] == "not-doing"
    assert got["DRE-100"]["reason"] is None
    assert got["DRE-100"]["reason_label"] == words


def test_with_no_reasons_served_a_label_is_kept_as_words():
    got = one([marker(tail="Not ready: waits on other work")], reasons=None,
              door_problem="unavailable")
    assert got["DRE-100"]["decision"] == "not-doing"
    assert got["DRE-100"]["reason"] is None
    assert got["DRE-100"]["reason_label"] == "Not ready: waits on other work"


def test_a_bare_exclusion_is_a_not_doing_with_no_words():
    got = one([marker(tail=None)])
    assert got["DRE-100"]["decision"] == "not-doing"
    assert got["DRE-100"]["reason"] is None
    assert got["DRE-100"]["reason_label"] is None


def test_held_until_a_card_is_a_hold_on_that_card():
    got = one([marker(kind="held", tail="until DRE-6061")])["DRE-100"]
    assert got["decision"] == "hold"
    assert got["hold_until_card"] == "DRE-6061"
    assert got["hold_until_date"] is None


def test_held_until_a_date_is_a_hold_on_that_date():
    got = one([marker(kind="held", tail=" — until 2026-11-02")])["DRE-100"]
    assert got["decision"] == "hold"
    assert got["hold_until_date"] == "2026-11-02"
    assert got["hold_until_card"] is None


def test_any_other_held_tail_is_a_hold_with_no_condition():
    words = "Still real, but DRE-3546 started building in the same file"
    got = one([marker(kind="held", tail=words)])["DRE-100"]
    assert got["decision"] == "hold"
    assert got["hold_until_card"] is None and got["hold_until_date"] is None
    assert got["reason_label"] == words
    bare = one([marker(kind="held")])["DRE-100"]
    assert bare["decision"] == "hold" and bare["hold_until_card"] is None


def test_an_impossible_date_is_words_not_a_condition():
    got = one([marker(kind="held", tail="until 2026-13-45")])["DRE-100"]
    assert got["hold_until_date"] is None
    assert got["reason_label"] == "until 2026-13-45"


@pytest.mark.parametrize("kind", ["added", "rewrite-accepted"])
def test_added_and_rewrite_accepted_are_put_backs_with_their_words(kind):
    got = one([marker(kind=kind, tail="It is ready now")])["DRE-100"]
    assert got["decision"] == "put-back"
    assert got["reason_label"] == "It is ready now"
    assert got["actor_name"] is None


# --- which source counts --------------------------------------------------

def test_a_row_is_the_record_and_carries_its_label_and_actor():
    got = one([], [row(reason="already-done", actor="Sid", ident=7)])["DRE-100"]
    assert got["source"] == "record"
    assert got["reason"] == "already-done"
    assert got["reason_label"] == "Already done"
    assert got["actor_name"] == "Sid"


def test_a_row_with_no_keyed_reason_falls_back_to_its_comment():
    got = one([], [row(comment="Superseded by the console work")])["DRE-100"]
    assert got["reason_label"] == "Superseded by the console work"


def test_a_row_hold_carries_its_condition():
    got = one([], [row(decision="hold", until_card="DRE-7", until_date=None)])
    assert got["DRE-100"]["hold_until_card"] == "DRE-7"


def test_a_marker_older_than_the_oldest_row_counts_as_history():
    got = one([marker("DRE-1", at="2026-09-22T02:49:30Z")],
              [row("DRE-2", at="2026-10-15T00:00:00Z")])
    assert got["DRE-1"]["source"] == "history"
    assert got["DRE-1"]["recorded_at"] == "2026-09-22T02:49:30Z"
    assert got["DRE-2"]["source"] == "record"


def test_a_marker_a_row_mirrors_is_the_same_decision_and_is_dropped():
    """The console writes the row AND the marker. The marker after the row is
    the same decision, and the row — with its actor and keyed reason — is the
    one that is kept, even when the marker is a second later."""
    got = one([marker("DRE-1", tail="Already done", at="2026-10-20T16:00:01Z")],
              [row("DRE-1", reason="already-done", at="2026-10-20T16:00:00Z")])
    assert got["DRE-1"]["source"] == "record"
    assert got["DRE-1"]["actor_name"] == "Sid"


def test_an_unmirrored_later_marker_counts_as_thread():
    """Typed by hand on the thread after the console began storing: no row
    mirrors it, and a person's decision is never dropped in silence."""
    got = one([marker("DRE-1", kind="added", tail="Bring it back",
                      at="2026-10-21T09:00:00Z")],
              [row("DRE-1", at="2026-10-20T16:00:00Z")])
    assert got["DRE-1"]["source"] == "thread"
    assert got["DRE-1"]["decision"] == "put-back"


def test_a_marker_on_another_proposal_is_not_mirrored():
    got = one([marker("DRE-1", at="2026-10-21T09:00:00Z", proposal="06e997f2ba4c")],
              [row("DRE-1", at="2026-10-20T16:00:00Z")])
    assert got["DRE-1"]["source"] == "thread"


def test_the_newest_decision_wins_across_sources():
    rows = [row("DRE-1", decision="hold", at="2026-10-20T16:00:00Z")]
    markers = [marker("DRE-1", kind="excluded", at="2026-09-22T02:00:00Z"),
               marker("DRE-2", kind="excluded", at="2026-09-22T02:00:00Z"),
               marker("DRE-2", kind="added", at="2026-10-22T02:00:00Z")]
    got = one(markers, rows)
    assert got["DRE-1"]["decision"] == "hold"
    assert got["DRE-1"]["source"] == "record"
    assert got["DRE-2"]["decision"] == "put-back"
    assert got["DRE-2"]["source"] == "thread"


def test_a_newer_hand_marker_beats_an_older_row():
    rows = [row("DRE-1", decision="not-doing", at="2026-10-20T16:00:00Z")]
    markers = [marker("DRE-1", kind="held", at="2026-10-21T16:00:00Z")]
    assert one(markers, rows)["DRE-1"]["decision"] == "hold"


def test_on_an_exact_tie_the_row_wins():
    at = "2026-10-20T16:00:00Z"
    rows = [row("DRE-1", decision="hold", at=at),
            row("DRE-2", decision="hold", at="2026-10-01T00:00:00Z")]
    markers = [marker("DRE-1", kind="excluded", at=at)]
    got = one(markers, rows)
    assert got["DRE-1"]["decision"] == "hold"
    assert got["DRE-1"]["source"] == "record"


def test_timestamps_compare_as_instants_not_strings():
    """09:00 Pacific is 16:00Z, after 15:30Z — although it sorts first as text."""
    rows = [row("DRE-1", decision="hold", at="2026-10-20T09:00:00-07:00")]
    markers = [marker("DRE-1", kind="excluded", at="2026-10-20T15:30:00.500Z")]
    assert one(markers, rows)["DRE-1"]["decision"] == "hold"
    later = [marker("DRE-1", kind="excluded", at="2026-10-20T16:00:00.500Z")]
    assert one(later, rows)["DRE-1"]["decision"] == "not-doing"


def test_when_the_door_could_not_be_read_every_marker_is_fallback():
    markers = [marker("DRE-1", at="2026-09-22T02:00:00Z"),
               marker("DRE-2", kind="held", at="2026-10-22T02:00:00Z")]
    got = one(markers, [], reasons=None, door_problem="unavailable")
    assert {d["source"] for d in got.values()} == {"fallback"}
    assert set(got) == {"DRE-1", "DRE-2"}


# --- the real threads -----------------------------------------------------

def _thread_markers():
    return THREADS["markers"]["DRE-4541"] + THREADS["markers"]["DRE-4488"]


@pytest.mark.parametrize("door_problem", [None, "unavailable"])
def test_the_four_cards_excluded_on_dre_4488_stay_excluded(door_problem):
    remembered = gm.remembered([], _thread_markers(), reasons=None,
                               door_problem=door_problem)
    morning = gm.standing(remembered, today="2026-10-23", states={})
    excluded = {d["card"] for d in morning["excluded"]}
    for card in ("DRE-4267", "DRE-4271", "DRE-4272"):
        assert card in excluded, card
        assert remembered[card]["standing_card"] == "DRE-4488"
        assert remembered[card]["proposal_id"] == "50e396c78523"
    # DRE-4054's Hold on DRE-4541 of 2026-10-10 is newer than its exclusion
    # on DRE-4488 of 2026-09-22, and has no condition: ranked last.
    assert "DRE-4054" not in excluded
    assert [d["card"] for d in morning["held_last"]].count("DRE-4054") == 1
    assert remembered["DRE-4054"]["standing_card"] == "DRE-4541"


def test_dre_4054_is_excluded_when_only_dre_4488_is_read_up_to_its_hold():
    markers = [m for m in _thread_markers() if m["created_at"] < "2026-10-10"]
    remembered = gm.remembered([], markers, reasons=None, door_problem="unavailable")
    morning = gm.standing(remembered, today="2026-10-09", states={})
    assert "DRE-4054" in {d["card"] for d in morning["excluded"]}


def test_dre_2348_and_dre_2373_read_as_removals():
    remembered = gm.remembered([], THREADS["markers"]["DRE-4541"], reasons=None,
                               door_problem="unavailable")
    morning = gm.standing(remembered, today="2026-10-23", states={})
    excluded = {d["card"] for d in morning["excluded"]}
    for card in ("DRE-2348", "DRE-2373"):
        assert card in excluded
        assert remembered[card]["proposal_id"] == "b9eecae64787"


def test_the_real_cancel_markers_go_on_the_cancel_list():
    remembered = gm.remembered([], THREADS["markers"]["DRE-4541"], reasons=None,
                               door_problem="unavailable")
    morning = gm.standing(remembered, today="2026-10-23", states={})
    assert {"DRE-3589", "DRE-7000"} <= {d["card"] for d in morning["cancel"]}


# --- this morning's answer ------------------------------------------------

def _morning(decisions, *, today="2026-10-23", states=None):
    remembered = one(decisions, door_problem="unavailable")
    return gm.standing(remembered, today=today, states=states or {})


def _lists_holding(morning, card):
    found = []
    for name, entries in morning.items():
        for entry in entries:
            decision = entry["decision"] if name in ("held", "back") else entry
            if decision["card"] == card:
                found.append(name)
    return found


def test_standing_answers_with_exactly_the_six_lists():
    assert set(_morning([])) == {"excluded", "cancel", "held", "held_last",
                                 "back", "put_back"}


def test_a_hold_on_a_done_card_is_met_and_back_on_offer():
    morning = _morning([marker(kind="held", tail="until DRE-7")],
                       states={"DRE-7": "Done"})
    assert _lists_holding(morning, "DRE-100") == ["back"]
    assert "DRE-7" in morning["back"][0]["why"]


@pytest.mark.parametrize("state, says", [
    ("In Review", "In Review"),
    ("Canceled", "can no longer be met"),
    ("Duplicate", "can no longer be met"),
    (None, "could not be read"),
])
def test_a_hold_on_a_card_not_done_stays_held_and_says_why(state, says):
    morning = _morning([marker(kind="held", tail="until DRE-7")],
                       states={"DRE-7": state})
    assert _lists_holding(morning, "DRE-100") == ["held"]
    why = morning["held"][0]["why"]
    assert "DRE-7" in why and says in why


def test_a_hold_on_a_card_nobody_looked_up_is_unread():
    morning = _morning([marker(kind="held", tail="until DRE-7")], states={})
    assert _lists_holding(morning, "DRE-100") == ["held"]
    assert "could not be read" in morning["held"][0]["why"]


def test_a_hold_until_a_date_is_met_on_that_date_and_not_before():
    held = [marker(kind="held", tail="until 2026-11-02")]
    before = _morning(held, today="2026-11-01")
    assert _lists_holding(before, "DRE-100") == ["held"]
    assert "2026-11-02" in before["held"][0]["why"]
    on = _morning(held, today="2026-11-02")
    assert _lists_holding(on, "DRE-100") == ["back"]
    after = _morning(held, today="2026-12-01")
    assert _lists_holding(after, "DRE-100") == ["back"]


def test_a_hold_with_no_condition_is_ranked_last():
    morning = _morning([marker(kind="held", tail="ask me later")])
    assert _lists_holding(morning, "DRE-100") == ["held_last"]


def test_a_cancel_is_on_the_cancel_list():
    morning = _morning([marker(tail="move to Cancel")])
    assert _lists_holding(morning, "DRE-100") == ["cancel"]


def test_a_not_doing_is_off_the_offer():
    morning = _morning([marker(tail="Already done")])
    assert _lists_holding(morning, "DRE-100") == ["excluded"]


def test_a_put_back_is_in_put_back_and_nowhere_else():
    morning = _morning([marker(at="2026-10-01T00:00:00Z"),
                        marker(kind="added", tail="ready now",
                               at="2026-10-02T00:00:00Z")])
    assert _lists_holding(morning, "DRE-100") == ["put_back"]
    assert morning["put_back"][0]["reason_label"] == "ready now"


def test_every_list_is_sorted_by_card_number():
    markers = [marker(card) for card in ("DRE-11", "DRE-9", "DRE-100")]
    morning = _morning(markers)
    assert [d["card"] for d in morning["excluded"]] == ["DRE-9", "DRE-11", "DRE-100"]


# --- it reads nothing and imports nothing from the groomer ----------------

def test_the_module_imports_none_of_the_groomer_the_door_or_linear():
    tree = ast.parse((ROOT / "scripts" / "groom_memory.py").read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported, "the import walk found nothing — it reads nothing"
    assert not imported & {"groomer", "bureau_read", "linear_ops"}, imported
