"""RED-first: one table places every hygiene-hold group in one bucket (DRE-9290).

`config/hold-placements.json` carries one row per group a hygiene hold can be
read into by `scripts/hold_waits.py` — `kind`, `group`, `bucket` (`pipeline`,
`owner` or `operator`), `who` and `why`. `scripts/hold_placement.py` is its one
reader: `groups()` enumerates every group the three writers can produce, read
off their own vocabularies, and `check` fails on a group nobody placed and on
a row no writer produces. The `hand-work-overdue` `Kind:` leads are composed
in `scripts/hold_deadline.py`, so the alarm `reconcile.py` posts and the group
`groups()` enumerates come from one place — pinned here by driving both alarm
sites and comparing their lead byte for byte.

Run: cd bureau-pipeline && python3 -m pytest tests/test_hold_placement.py -v
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/portico")
os.environ.setdefault("REPO_SLUG", "portico")
os.environ.setdefault("GH_TOKEN", "x")

import hold_deadline  # noqa: E402
import hygiene_blockers  # noqa: E402
import reconcile  # noqa: E402
import validate_card  # noqa: E402

import test_hand_work_age as hwa  # noqa: E402

MODULE_PATH = ROOT / "scripts" / "hold_placement.py"
TABLE_PATH = ROOT / "config" / "hold-placements.json"
STEP_KINDS_PATH = ROOT / "config" / "operator-step-kinds.json"
ACTS_PATH = ROOT / "config" / "pipeline-acts.json"

HAND_STEP = "hyg-hand-step"
BLOCKER_OWNER = "hyg-blocker-owner"
OVERDUE = "hand-work-overdue"
TAGS = (HAND_STEP, BLOCKER_OWNER, OVERDUE)

#: The card's `## The placements`, every row's `bucket` word for word.
PLACEMENTS = {
    (HAND_STEP, "machine, kind variable"): "pipeline",
    (HAND_STEP, "machine, kind bot-pr"): "pipeline",
    (HAND_STEP, "machine, kind linear-admin"): "pipeline",
    (HAND_STEP, "machine, kind script"): "operator",
    (HAND_STEP, "machine, kind deploy"): "operator",
    (HAND_STEP, "person, kind person"): "operator",
    (BLOCKER_OWNER, "Intake, holds up open work"): "owner",
    (BLOCKER_OWNER, "Hand-work, holds up open work"): "operator",
    (BLOCKER_OWNER, "<lane>, holds up nothing open"): "pipeline",
    (BLOCKER_OWNER, "<lane>, state unread"): "operator",
    (OVERDUE, "the operator's card, labeled '<mark>'"): "operator",
    (OVERDUE, "the operator's card, routed <VERDICT> to <lane>"): "operator",
    (OVERDUE, "a PROOF: card whose proof run is not in flight"): "pipeline",
    (OVERDUE, "a hold naming the CEO's press on a card in <lane>, not Green Light"): "operator",
    (OVERDUE, "a PROOF: card held for the operator"): "operator",
    (HAND_STEP, "unreadable receipt"): "operator",
    (BLOCKER_OWNER, "unreadable receipt"): "operator",
    (OVERDUE, "unreadable receipt"): "operator",
    (None, "window cut short — not read"): "operator",
}


def load_script():
    if not MODULE_PATH.exists():
        return None
    spec = importlib.util.spec_from_file_location("hold_placement", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["hold_placement"] = module
    spec.loader.exec_module(module)
    return module


hp = load_script()


def script():
    assert hp is not None, f"{MODULE_PATH.name} does not exist yet"
    return hp


def table() -> dict:
    return json.loads(TABLE_PATH.read_text(encoding="utf-8"))


def write(tmp_path: Path, name: str, doc: dict) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def placed_groups(enumerated) -> set:
    """Every enumerated group some row of the shipped table places."""
    return {(k, g.casefold()) for k, g in enumerated if script().placement(k, g) is not None}


# --------------------------------------------------------------------------- #
# the contract                                                                 #
# --------------------------------------------------------------------------- #


def test_the_end_line_and_its_words_are_the_contracts():
    assert script().END_LINE == "wait ended:"
    assert script().END_WORDS == {
        "hyg-hand-step": "the hand-step class",
        "hyg-blocker-owner": "the blocker notice",
        "hand-work-overdue": "the Hand-work age alarm",
    }


def test_no_end_words_value_carries_a_registry_tag():
    """A registry tag in a body is read as that act's receipt, so the end line
    names a hold by its words, never by its tag."""
    acts = json.loads(ACTS_PATH.read_text(encoding="utf-8"))["acts"]
    tags = {a["tag"] for a in acts if isinstance(a.get("tag"), str) and a["tag"]}
    assert set(TAGS) <= tags
    for kind, words in script().END_WORDS.items():
        found = [t for t in tags if t.casefold() in words.casefold()]
        assert not found, f"{kind}'s end words {words!r} carry the tag(s) {found}"


def test_the_bucket_words():
    assert script().BUCKETS == ("pipeline", "owner", "operator")


# --------------------------------------------------------------------------- #
# the table                                                                    #
# --------------------------------------------------------------------------- #


def test_every_row_is_the_placements_row_with_its_bucket():
    rows = table()["rows"]
    got = {(r["kind"], r["group"]): r["bucket"] for r in rows}
    assert len(got) == len(rows), "a row is written twice"
    assert got == PLACEMENTS


def test_every_row_says_who_and_one_line_of_why():
    for row in table()["rows"]:
        assert set(row) == {"kind", "group", "bucket", "who", "why"}, row
        assert row["who"].strip(), row
        why = row["why"]
        assert why.strip(), row
        assert "·" not in why and "\n" not in why and "\r" not in why, row
        assert why.rstrip().endswith("."), row
        assert "·" not in row["who"] and "\n" not in row["who"], row


def test_the_readme_says_what_each_bucket_means():
    readme = table()["_readme"]
    for words in ("`pipeline`", "`owner`", "`operator`",
                  "the pipeline does the step and the hold ends with a receipt",
                  "the hold goes to its real owner automatically",
                  "it stays a hold and says why"):
        assert words in readme, words


# --------------------------------------------------------------------------- #
# groups(): read off the writers' own vocabularies                             #
# --------------------------------------------------------------------------- #


def test_groups_reads_each_writers_vocabulary():
    import lane_contract  # noqa: PLC0415
    import operator_step_kind  # noqa: PLC0415
    import routing_verdict  # noqa: PLC0415

    got = set(script().groups())
    step_kinds = [r["kind"] for r in json.loads(STEP_KINDS_PATH.read_text())["kinds"]]
    for kind in step_kinds:
        assert (HAND_STEP, f"machine, kind {kind}") in got
    assert (HAND_STEP, "person, kind person") in got
    assert operator_step_kind.classify("", "")[:2] == ("person", "person")
    for lane in hygiene_blockers.WAITING:
        for split in ("holds up open work", "holds up nothing open", "state unread"):
            assert (BLOCKER_OWNER, f"{lane}, {split}") in got
    for mark in routing_verdict.hand_marks():
        assert (OVERDUE, f"the operator's card, labeled '{mark}'") in got
    persons = [v for v in routing_verdict.verdicts() if routing_verdict.is_person_verdict(v)]
    assert persons
    for verdict in persons:
        lane = routing_verdict.destination(verdict)
        assert (OVERDUE, f"the operator's card, routed {verdict} to {lane}") in got
    assert (OVERDUE, "a PROOF: card whose proof run is not in flight") in got
    assert (OVERDUE, "a PROOF: card held for the operator") in got
    live = [ln for ln in lane_contract.lane_names("live") if ln != "Green Light"]
    for lane in live:
        assert (OVERDUE, f"a hold naming the CEO's press on a card in {lane}, "
                         "not Green Light") in got
    for kind in TAGS:
        assert (kind, "unreadable receipt") in got
    assert (None, "window cut short — not read") in got


def test_the_set_enumerated_equals_the_set_placed_both_ways():
    enumerated = script().groups()
    assert len(enumerated) == len(set(enumerated)), "a group is enumerated twice"
    assert placed_groups(enumerated) == {(k, g.casefold()) for k, g in enumerated}
    # And the other way: every row places at least one enumerated group.
    for row in table()["rows"]:
        hits = [g for k, g in enumerated
                if k == row["kind"] and script().placement(k, g) == row]
        assert hits, f"the row {row['kind']} — {row['group']} places no enumerated group"


def test_check_exits_0_on_the_shipped_files(capsys):
    assert script().check() == 0
    out = capsys.readouterr().out
    for kind, group in script().groups():
        row = script().placement(kind, group)
        assert f"{group}: bucket {row['bucket']}" in out


def test_the_command_runs_from_a_checkout():
    got = subprocess.run([sys.executable, str(MODULE_PATH), "check"], cwd=ROOT,
                         capture_output=True, text=True, timeout=120, check=False)
    assert got.returncode == 0, got.stdout + got.stderr
    assert "unplaced" not in got.stdout


# --------------------------------------------------------------------------- #
# the guard bites                                                              #
# --------------------------------------------------------------------------- #


def test_a_new_step_kind_with_no_row_fails_check(tmp_path, capsys):
    kinds = json.loads(STEP_KINDS_PATH.read_text(encoding="utf-8"))
    kinds["kinds"].append({"kind": "dns-record", "why": "x", "phrases": ["dns record"]})
    copy = write(tmp_path, "operator-step-kinds.json", kinds)
    assert script().check(step_kinds=copy) == 1
    out = capsys.readouterr().out
    assert "hyg-hand-step — machine, kind dns-record" in out
    assert "no row" in out


def test_a_new_waiting_lane_with_no_row_fails_check(monkeypatch, capsys):
    monkeypatch.setattr(hygiene_blockers, "WAITING", hygiene_blockers.WAITING + ("Queued",))
    assert script().check() == 1
    out = capsys.readouterr().out
    assert "hyg-blocker-owner — Queued, holds up open work" in out


def test_a_new_kind_lead_constant_with_no_row_fails_check(monkeypatch, capsys):
    monkeypatch.setattr(hold_deadline, "KIND_LEAD_SOMETHING_NEW",
                        "a card waiting on something nobody placed", raising=False)
    assert ("hand-work-overdue", "a card waiting on something nobody placed") in \
        script().groups()
    assert script().check() == 1
    out = capsys.readouterr().out
    assert "hand-work-overdue — a card waiting on something nobody placed" in out


def test_a_row_no_writer_produces_fails_check(tmp_path, capsys):
    doc = table()
    doc["rows"].append({"kind": OVERDUE, "group": "a card nobody writes", "bucket": "operator",
                        "who": "the operator", "why": "Nobody writes it."})
    assert script().check(table=write(tmp_path, "t.json", doc)) == 1
    out = capsys.readouterr().out
    assert "hand-work-overdue — a card nobody writes" in out


@pytest.mark.parametrize("word", ["person", "Pipeline", "", "operator "])
def test_a_bucket_outside_the_three_words_fails_check(tmp_path, capsys, word):
    doc = table()
    doc["rows"][0]["bucket"] = word
    assert script().check(table=write(tmp_path, "t.json", doc)) == 1
    assert "bucket" in capsys.readouterr().out


def test_a_group_placed_twice_fails_check(tmp_path, capsys):
    doc = table()
    doc["rows"].append({**doc["rows"][0], "bucket": "operator"})
    assert script().check(table=write(tmp_path, "t.json", doc)) == 1
    out = capsys.readouterr().out
    assert "placed twice" in out
    assert doc["rows"][0]["group"] in out


def test_a_template_that_overlaps_another_places_a_group_twice(tmp_path, capsys):
    doc = table()
    doc["rows"].append({"kind": BLOCKER_OWNER, "group": "<lane>, holds up open work",
                        "bucket": "owner", "who": "x", "why": "X."})
    assert script().check(table=write(tmp_path, "t.json", doc)) == 1
    assert "Intake, holds up open work" in capsys.readouterr().out


@pytest.mark.parametrize("why", ["Two parts · joined.", "Two lines.\nJoined."])
def test_a_why_a_receipt_would_refuse_fails_check(tmp_path, capsys, why):
    doc = table()
    doc["rows"][0]["why"] = why
    assert script().check(table=write(tmp_path, "t.json", doc)) == 1
    assert "why" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# placement(): groups compare case-blind                                       #
# --------------------------------------------------------------------------- #


def test_a_card_labeled_in_another_case_reads_the_marks_row():
    card = {"identifier": "DRE-7001", "state": {"name": "Hand-work"}, "title": "a step",
            "labels": {"nodes": [{"name": "Operator-Step"}]}, "comments": {"nodes": []}}
    reason = reconcile.hand_built_reason(card)
    assert reason == "labeled 'Operator-Step'"
    group = hold_deadline.kind_lead_operators_card(reason)
    row = script().placement(OVERDUE, group)
    assert row is not None
    assert row["group"] == "the operator's card, labeled '<mark>'"
    assert row["bucket"] == "operator"


def test_a_group_nobody_placed_has_no_placement():
    assert script().placement(OVERDUE, "the operator's card, operator-step") is None
    assert script().placement(HAND_STEP, "machine, kind dns-record") is None
    assert script().placement(OVERDUE, "machine, kind deploy") is None
    assert script().placement(None, "window cut short — not read")["bucket"] == "operator"


# --------------------------------------------------------------------------- #
# the leads: the group enumerated is the lead the alarm writes                 #
# --------------------------------------------------------------------------- #


@pytest.fixture
def hermetic(monkeypatch):
    """The sweep owns portico and portico is on the rail — test_hand_work_age's
    own fixture, for its builders."""
    monkeypatch.setattr(reconcile, "REPO_SLUG", "portico")
    monkeypatch.setattr(reconcile, "REPO", "dreadnought-foundry/portico")
    monkeypatch.setattr(validate_card, "VALID_SLUGS", {"portico", "atlas"})
    monkeypatch.setattr(reconcile, "live_rail_slugs",
                        lambda: frozenset({"portico", "atlas"}), raising=False)
    reconcile._write_failures.clear()
    reconcile._stale_defects.clear()
    yield
    reconcile._write_failures.clear()
    reconcile._stale_defects.clear()


def lead(body: str) -> str:
    """The `Kind:` text before its first `—`, a trailing period dropped."""
    second = body.split("\n")[1]
    assert second.startswith("Kind: "), second
    return second[len("Kind:"):].split("—", 1)[0].strip().rstrip(".").strip()


def posted(got) -> str:
    got.comment.assert_called_once()
    return got.comment.call_args.args[1]


def assert_enumerated(text: str) -> None:
    enumerated = [g for k, g in script().groups() if k == OVERDUE]
    assert text in enumerated, f"{text!r} is not a group groups() enumerates"


def test_the_operators_card_lead_is_enumerated_byte_for_byte(hermetic, capsys):
    text = lead(posted(hwa._sweep([hwa._card()], capsys)))
    assert text == "the operator's card, labeled 'operator-step'"
    assert_enumerated(text)


def test_the_routed_operators_card_lead_is_enumerated_byte_for_byte(hermetic, capsys):
    text = lead(posted(hwa._sweep([hwa._workbench_card()], capsys)))
    assert text == "the operator's card, routed WORKBENCH to Hand-work"
    assert_enumerated(text)


def test_the_proof_not_in_flight_lead_is_enumerated_byte_for_byte(hermetic, capsys):
    text = lead(posted(hwa._sweep([hwa._proof_card()], capsys)))
    assert text == hold_deadline.KIND_LEAD_PROOF_NOT_IN_FLIGHT
    assert text == "a PROOF: card whose proof run is not in flight"
    assert_enumerated(text)


def test_the_ceo_press_lead_is_enumerated_byte_for_byte(hermetic, capsys):
    press = hwa._hold(hwa.CEO_NEEDS, 25.5)
    text = lead(posted(hwa._held_sweep([hwa._review_card(nodes=[press])], capsys)))
    assert text == "a hold naming the CEO's press on a card in In Review, not Green Light"
    assert text == hold_deadline.kind_lead_ceo_press("In Review")
    assert_enumerated(text)


def test_the_proof_held_lead_is_enumerated_byte_for_byte(hermetic, capsys):
    cause = hwa._hold(hwa.CAUSE_NEEDS, 25.5)
    text = lead(posted(hwa._held_sweep([hwa._review_card(nodes=[cause])], capsys)))
    assert text == hold_deadline.KIND_LEAD_PROOF_HELD
    assert text == "a PROOF: card held for the operator"
    assert_enumerated(text)


def test_the_hand_built_reason_is_composed_where_the_lead_is(monkeypatch):
    """`hand_built_reason` writes its two person reasons through the composing
    functions `groups()` enumerates with, never an f-string of its own."""
    calls: list = []
    real = hold_deadline.labeled_reason
    monkeypatch.setattr(hold_deadline, "labeled_reason",
                        lambda mark: calls.append(mark) or real(mark))
    card = {"identifier": "DRE-7001", "state": {"name": "Hand-work"}, "title": "a step",
            "labels": {"nodes": [{"name": "operator-step"}]}, "comments": {"nodes": []}}
    assert reconcile.hand_built_reason(card) == "labeled 'operator-step'"
    assert calls == ["operator-step"]
