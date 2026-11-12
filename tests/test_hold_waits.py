"""RED-first: one read enumerates what every hygiene hold is waiting on (DRE-9274).

`scripts/hold_waits.py --board <file>` reads the board file
`python3 scripts/hygiene.py read --out <file>` writes and lists every open
`hyg-hand-step`, `hyg-blocker-owner` and `hand-work-overdue` hold — its card,
its lane, its receipt's first line and reason line verbatim, its standing
hours, and what it waits on — grouped by what the receipt itself says it waits
for. It writes nothing and adds no direct Linear read: its only reads are the
board file and `hygiene_blockers.read_cards` through a read-only hygiene
context.

Every board here is built in the test, in the `board_snapshot.CARD_QUERY`
shape. The two hygiene receipts are composed with `hygiene.receipt`, and each
`hand-work-overdue` alarm in both of `reconcile.py`'s first-line forms through
`pipeline_act.receipt` — and once by `reconcile._hand_work_age` itself.

Run: cd bureau-pipeline && python3 -m pytest tests/test_hold_waits.py -v
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import check_linear_reads  # noqa: E402
import hygiene  # noqa: E402
import hygiene_blockers  # noqa: E402
import hygiene_hand_step  # noqa: E402
import linear_ops  # noqa: E402
import pipeline_act  # noqa: E402
import reconcile  # noqa: E402

MODULE_PATH = ROOT / "scripts" / "hold_waits.py"
ACTS_PATH = ROOT / "config" / "pipeline-acts.json"

#: 17:00 UTC on 2026-11-12 is 09:00 in Pacific Standard Time.
NOW = datetime(2026, 11, 12, 17, 0, tzinfo=UTC)

HAND_STEP = "hyg-hand-step"
BLOCKER_OWNER = "hyg-blocker-owner"
OVERDUE = "hand-work-overdue"

#: The act each hold's tag is declared under in `config/pipeline-acts.json`.
HOLD_ACTS = ("hygiene-hand-step-class", "hygiene-blocker-owner-notice", "hand-work-overdue")


def load_script():
    if not MODULE_PATH.exists():
        return None
    spec = importlib.util.spec_from_file_location("hold_waits", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hw = load_script()


def script():
    assert hw is not None, f"{MODULE_PATH.name} does not exist yet"
    return hw


def iso(when: datetime) -> str:
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def ago(hours: float, now: datetime = NOW) -> datetime:
    return now - timedelta(hours=hours)


# --------------------------------------------------------------------------- #
# builders                                                                     #
# --------------------------------------------------------------------------- #


def comment(body: str, at: datetime) -> dict:
    return {"body": body, "createdAt": iso(at), "user": None}


def card(ident, lane, *, entered=None, comments=(), labels=(), blockers=(), title=None,
         partial=False):
    """One card in the `board_snapshot.CARD_QUERY` shape. `entered` is its move
    into `lane` on the history read; `comments` are `comment()` dicts in any
    order, laid out newest first as Linear answers the window; `blockers` are
    `(identifier, lane)` pairs that block this card."""
    history = [] if entered is None else [
        {"createdAt": iso(entered - timedelta(days=3)), "toState": {"name": "Planning"}},
        {"createdAt": iso(entered), "toState": {"name": lane}},
    ]
    nodes = sorted(comments, key=lambda c: c["createdAt"], reverse=True)
    return {
        "id": f"uuid-{ident}",
        "identifier": ident,
        "title": title or f"synthetic {ident}",
        "description": "synthetic",
        "createdAt": iso(ago(24 * 60)),
        "updatedAt": iso(NOW),
        "state": {"name": lane},
        "labels": {"nodes": [{"name": n} for n in labels]},
        "parent": None,
        "children": {"nodes": []},
        "comments": {"pageInfo": {"hasNextPage": partial, "endCursor": "c" if partial else None},
                     "nodes": nodes},
        "relations": {"pageInfo": {"hasNextPage": False}, "nodes": []},
        "inverseRelations": {"pageInfo": {"hasNextPage": False}, "nodes": [
            {"type": "blocks", "issue": {"identifier": b, "state": {"name": s}}}
            for b, s in blockers]},
        "history": {"nodes": history},
    }


def board(*cards, taken_at=NOW) -> dict:
    lanes: dict = {lane: [] for lane in hygiene.LANES + (hygiene.HAND_WORK,)}
    for c in cards:
        lanes.setdefault(c["state"]["name"], []).append(c)
    return {"taken_at": iso(taken_at).replace(".000Z", "Z"), "lanes": lanes}


def blocker_receipt(ident, lane, day, held, *, now=NOW) -> str:
    """The notice `hygiene_blockers.notice` posts, composed the way it composes
    it."""
    return hygiene.receipt(
        hygiene_blockers.ACT, f"{ident} in {lane} since {day}",
        [f"entered {lane} {day}T17:00:00Z", *(f"relation {h} blocked by {ident}" for h in held)],
        now,
        [f"no owner to tell — {ident} has waited in {lane} since {day} (20 days) and "
         f"holds up {len(held)} open cards:"])


def hand_step_receipt(cls, kind, phrase, *, now=NOW) -> str:
    """The receipt `hygiene_hand_step.classed` posts."""
    return hygiene.receipt(hygiene_hand_step.ACT, f"{cls}, kind {kind}", [phrase], now)


def overdue_in_hand_work(ident, hours, kind) -> str:
    """`reconcile._hand_work_age`'s alarm — the first first-line form."""
    return pipeline_act.receipt("hand-work-overdue", (
        f"🚨 hand-work-overdue: {ident} has waited in Hand-work {hours} hours, "
        f"past its 24 — the operator's to look at.\nKind: {kind}"))


def overdue_for_operator(ident, lane, hours, held_by) -> str:
    """`reconcile.alarm_held_proofs`' alarm — the second first-line form."""
    return pipeline_act.receipt("hand-work-overdue", (
        f"🚨 hand-work-overdue: {ident} has waited {lane} for the operator "
        f"{hours} hours, past its 24 — the operator's to look at.\n"
        f"Kind: {held_by}."))


def cards_node(ident, lane) -> dict:
    """One card as `hygiene_blockers.CARDS_QUERY` answers it."""
    return {"identifier": ident, "title": f"synthetic {ident}", "createdAt": iso(ago(24 * 40)),
            "state": {"name": lane}, "history": {"nodes": []}, "assignee": None,
            "creator": None, "relations": {"pageInfo": {"hasNextPage": False}, "nodes": []}}


class FakeLinear:
    """The transport under `hygiene.read_only_linear`: answers
    `hygiene_blockers.CARDS_QUERY` from `states`, and raises on any document
    whose operation is `mutation`. A card `states` does not hold is not
    answered."""

    def __init__(self, states: dict):
        self.states = states
        self.queries: list = []

    def __call__(self, query, variables=None):
        if query.lstrip().startswith("mutation"):
            raise AssertionError(f"hold_waits sent a mutation: {query[:60]}")
        self.queries.append(query)
        assert query == hygiene_blockers.CARDS_QUERY, query[:80]
        numbers = [int(n) for n in (variables or {}).get("numbers") or []]
        found = [cards_node(f"DRE-{n}", self.states[f"DRE-{n}"])
                 for n in numbers if f"DRE-{n}" in self.states]
        return {"issues": {"nodes": found, "pageInfo": {"hasNextPage": False, "endCursor": None}}}


def context(fake):
    return hygiene.make_context(hygiene.HOME_OWNER, gh=lambda argv: "[]",
                                linear=hygiene.read_only_linear(fake), now=NOW)


def read(doc, states=None):
    fake = FakeLinear(states or {})
    report = script().read(doc, context(fake))
    return report, script().render(report), fake


def rows(report, ident):
    return [h for h in report.holds if h.card == ident]


# --------------------------------------------------------------------------- #
# the guard: the tags and the alarm's opening are the writers'                 #
# --------------------------------------------------------------------------- #


def test_the_tags_are_exactly_the_three_hold_acts_in_the_registry():
    acts = {a["name"]: a for a in json.loads(ACTS_PATH.read_text(encoding="utf-8"))["acts"]}
    tags = []
    for name in HOLD_ACTS:
        assert name in acts, f"{name} is not an act in config/pipeline-acts.json"
        assert acts[name]["kind"] == "hold", f"{name} is not a kind: hold act"
        tags.append(acts[name]["tag"])
    assert sorted(script().TAGS) == sorted(tags)
    assert len(script().TAGS) == 3


def test_the_alarm_opening_is_a_prefix_of_the_writers_anchor():
    acts = {a["tag"]: a for a in json.loads(ACTS_PATH.read_text(encoding="utf-8"))["acts"]}
    anchor = acts[OVERDUE]["emits"]["anchor"]
    assert script().OVERDUE_MARK == "🚨 hand-work-overdue:"
    assert anchor.startswith(script().OVERDUE_MARK)


# --------------------------------------------------------------------------- #
# one open hold of each kind, in its group                                     #
# --------------------------------------------------------------------------- #


def every_kind_board():
    blocker = blocker_receipt("DRE-101", "Intake", "2026-10-23", ["DRE-201", "DRE-202"])
    step = hand_step_receipt("machine", "deploy", "make deploy-console")
    first_form = overdue_in_hand_work(
        "DRE-301", 30, "the operator's card, operator-step — it entered Hand-work "
        "Nov 10 23:00 PT, and no run is dispatched for it.")
    second_form = overdue_for_operator(
        "DRE-302", "In Review", 26, "a PROOF: card held for the operator — 🔬 proof-waiting")
    return board(
        card("DRE-101", "Intake", entered=ago(24 * 20),
             comments=[comment(blocker, ago(24 * 10))]),
        card("DRE-401", "Hand-work", entered=ago(100), labels=["operator-step",
                                                                 "hand-step:machine"],
             comments=[comment(step, ago(40))]),
        card("DRE-301", "Hand-work", entered=ago(30), labels=["operator-step"],
             comments=[comment(first_form, ago(6))]),
        card("DRE-302", "In Review", entered=ago(80), title="PROOF: a proof",
             comments=[comment(second_form, ago(2))]),
    )


def test_one_open_hold_of_each_kind_lands_in_its_group():
    report, text, _ = read(every_kind_board(), {"DRE-201": "In Progress", "DRE-202": "Done"})
    by_card = {h.card: h for h in report.holds}
    assert set(by_card) == {"DRE-101", "DRE-401", "DRE-301", "DRE-302"}

    assert by_card["DRE-101"].kind == BLOCKER_OWNER
    assert by_card["DRE-101"].group == "Intake, holds up open work"
    assert by_card["DRE-101"].lane == "Intake"
    assert by_card["DRE-101"].hours == pytest.approx(240.0)
    assert "DRE-201 (In Progress)" in by_card["DRE-101"].waits_on
    assert "DRE-202 (Done)" in by_card["DRE-101"].waits_on

    assert by_card["DRE-401"].kind == HAND_STEP
    assert by_card["DRE-401"].group == "machine, kind deploy"
    assert by_card["DRE-401"].hours == pytest.approx(40.0)
    assert by_card["DRE-401"].waits_on == "waits on the step itself"

    assert by_card["DRE-301"].kind == OVERDUE
    assert by_card["DRE-301"].group == "the operator's card, operator-step"
    assert by_card["DRE-301"].lane == "Hand-work"
    assert by_card["DRE-301"].hours == pytest.approx(6.0)

    assert by_card["DRE-302"].kind == OVERDUE
    assert by_card["DRE-302"].group == "a PROOF: card held for the operator"
    assert by_card["DRE-302"].lane == "In Review"

    # The receipt's own first line and reason line, verbatim, on its row.
    for h in by_card.values():
        body = next(c["body"] for lane in every_kind_board()["lanes"].values()
                    for c2 in lane if c2["identifier"] == h.card
                    for c in c2["comments"]["nodes"])
        first, second = body.splitlines()[:2]
        assert h.first_line == first
        assert h.reason == second
        assert first in text and second in text


def test_the_output_opens_with_the_count_line_and_groups_largest_hours_first():
    _, text, _ = read(every_kind_board(), {"DRE-201": "In Progress", "DRE-202": "Done"})
    lines = text.splitlines()
    assert lines[0].startswith("hold-waits: taken_at=2026-11-12 09:00 PT lanes=")
    for part in ("open=4", "hyg-hand-step=1", "hyg-blocker-owner=1", "hand-work-overdue=2",
                 "unreadable=0", "cut-short=0"):
        assert f" {part}" in lines[0], part
    headers = [ln for ln in lines if ln.startswith(("hyg-", "hand-work-overdue"))]
    assert headers[0].startswith("hyg-blocker-owner — Intake, holds up open work: 1 hold")
    assert headers[1].startswith("hyg-hand-step — machine, kind deploy: 1 hold")
    assert "240.0 h total" in headers[0] and "oldest 240.0 h" in headers[0]
    assert "open 1" in headers[0]
    assert "step itself 1" in headers[1]


# --------------------------------------------------------------------------- #
# what it waits on                                                             #
# --------------------------------------------------------------------------- #


def test_a_blocker_whose_held_cards_all_closed_holds_up_nothing_open():
    receipt = blocker_receipt("DRE-102", "Hand-work", "2026-10-30", ["DRE-203", "DRE-204"])
    doc = board(card("DRE-102", "Hand-work", entered=ago(24 * 13),
                     comments=[comment(receipt, ago(24 * 11))]))
    report, text, _ = read(doc, {"DRE-203": "Done", "DRE-204": "Canceled"})
    (hold,) = rows(report, "DRE-102")
    assert hold.group == "Hand-work, holds up nothing open"
    assert hold.waits == "closed"
    assert "closed 1" in text


def test_a_held_card_the_read_does_not_answer_reads_state_unread_never_closed():
    receipt = blocker_receipt("DRE-103", "Intake", "2026-10-30", ["DRE-205", "DRE-206"])
    doc = board(card("DRE-103", "Intake", entered=ago(24 * 13),
                     comments=[comment(receipt, ago(24 * 11))]))
    report, _, fake = read(doc, {"DRE-206": "Done"})
    (hold,) = rows(report, "DRE-103")
    assert hold.group == "Intake, state unread"
    assert "DRE-205 (state unread)" in hold.waits_on
    assert hold.waits == "unread"
    assert fake.queries == [hygiene_blockers.CARDS_QUERY]


def test_an_open_held_card_wins_over_an_unanswered_one():
    receipt = blocker_receipt("DRE-104", "Intake", "2026-10-30", ["DRE-207", "DRE-208"])
    doc = board(card("DRE-104", "Intake", entered=ago(24 * 13),
                     comments=[comment(receipt, ago(24 * 11))]))
    report, _, _ = read(doc, {"DRE-207": "Queued"})
    (hold,) = rows(report, "DRE-104")
    assert hold.group == "Intake, holds up open work"
    assert hold.waits == "open"


def test_a_failed_read_leaves_every_held_card_unread():
    receipt = blocker_receipt("DRE-105", "Intake", "2026-10-30", ["DRE-209"])
    doc = board(card("DRE-105", "Intake", entered=ago(24 * 13),
                     comments=[comment(receipt, ago(24 * 11))]))

    def broken(query, variables=None):
        raise linear_ops.LinearError("boom")

    report = script().read(doc, context(broken))
    (hold,) = rows(report, "DRE-105")
    assert hold.group == "Intake, state unread"


def test_a_step_held_behind_an_open_or_a_closed_blocker_says_which():
    step = hand_step_receipt("person", "person", operator_step_kind_no_match())
    alarm = overdue_in_hand_work("DRE-403", 30, "the operator's card, WORKBENCH — it "
                                 "entered Hand-work Nov 10 23:00 PT, and no run is dispatched for it.")
    doc = board(
        card("DRE-402", "Hand-work", entered=ago(50), labels=["hand-step:person"],
             comments=[comment(step, ago(20))], blockers=[("DRE-501", "In Progress")]),
        card("DRE-403", "Hand-work", entered=ago(30),
             comments=[comment(alarm, ago(5))], blockers=[("DRE-502", "Done"),
                                                          ("DRE-503", "Canceled")]),
    )
    report, _, _ = read(doc)
    (step_hold,) = rows(report, "DRE-402")
    assert step_hold.group == "person, kind person"
    assert step_hold.waits_on == "waits on open blocker DRE-501 (In Progress)"
    assert step_hold.waits == "open"
    (alarm_hold,) = rows(report, "DRE-403")
    assert "blocker DRE-502 closed" in alarm_hold.waits_on
    assert "blocker DRE-503 closed" in alarm_hold.waits_on
    assert alarm_hold.waits == "closed"


def operator_step_kind_no_match():
    import operator_step_kind  # noqa: PLC0415
    return operator_step_kind.NO_MATCH


def test_hand_step_hours_run_from_the_later_of_the_receipt_and_the_entry():
    step = hand_step_receipt("machine", "script", "run scripts/x.py")
    doc = board(card("DRE-404", "Hand-work", entered=ago(10), labels=["hand-step:machine"],
                     comments=[comment(step, ago(300))]))
    report, _, _ = read(doc)
    (hold,) = rows(report, "DRE-404")
    assert hold.hours == pytest.approx(10.0)


# --------------------------------------------------------------------------- #
# a hold that is not open is not listed                                        #
# --------------------------------------------------------------------------- #


def test_a_hold_whose_card_left_the_lane_or_predates_the_entry_is_not_listed():
    blocker = blocker_receipt("DRE-106", "Intake", "2026-10-23", ["DRE-210"])
    stale_alarm = overdue_in_hand_work("DRE-304", 30, "the operator's card, operator-step "
                                       "— it entered Hand-work earlier.")
    step = hand_step_receipt("machine", "deploy", "make deploy")
    stale_proof = overdue_for_operator("DRE-305", "In Review", 30,
                                       "a PROOF: card held for the operator — x")
    doc = board(
        # left the lane its cause names
        card("DRE-106", "Queued", entered=ago(2), comments=[comment(blocker, ago(24 * 5))]),
        # the alarm predates the card's newest entry into Hand-work
        card("DRE-304", "Hand-work", entered=ago(3), comments=[comment(stale_alarm, ago(20))]),
        # classed, but no longer wears its class label
        card("DRE-405", "Hand-work", entered=ago(50), comments=[comment(step, ago(20))]),
        # classed, but left Hand-work
        card("DRE-406", "In Progress", entered=ago(5), labels=["hand-step:machine"],
             comments=[comment(step, ago(20))]),
        # the second form names In Review, and the card is now in Queued
        card("DRE-305", "Queued", entered=ago(1), title="PROOF: x",
             comments=[comment(stale_proof, ago(10))]),
    )
    report, text, _ = read(doc, {"DRE-210": "In Progress"})
    assert report.holds == []
    assert " open=0" in text.splitlines()[0]


def test_the_newest_receipt_of_a_stay_is_the_hold():
    older = overdue_in_hand_work("DRE-306", 30, "the operator's card, older — x.")
    newer = overdue_in_hand_work("DRE-306", 54, "the operator's card, newer — y.")
    doc = board(card("DRE-306", "Hand-work", entered=ago(60),
                     comments=[comment(older, ago(30)), comment(newer, ago(6))]))
    report, _, _ = read(doc)
    (hold,) = rows(report, "DRE-306")
    assert hold.group == "the operator's card, newer"
    assert hold.hours == pytest.approx(6.0)


# --------------------------------------------------------------------------- #
# a receipt that does not fit is still shown                                   #
# --------------------------------------------------------------------------- #


def test_an_unreadable_receipt_is_shown_with_its_first_line_quoted():
    bad_cause = hygiene.receipt(hygiene_hand_step.ACT, "a class nobody wrote", ["x"], NOW)
    no_kind = pipeline_act.receipt("hand-work-overdue", (
        "🚨 hand-work-overdue: DRE-307 has waited in Hand-work 30 hours, past its 24 — "
        "the operator's to look at."))
    empty_kind = pipeline_act.receipt("hand-work-overdue", (
        "🚨 hand-work-overdue: DRE-308 has waited in Hand-work 30 hours, past its 24 — "
        "the operator's to look at.\nKind:    — something."))
    unparsed = "🧹 hygiene: hyg-blocker-owner — written by hand with no clock\nevidence: x"
    doc = board(
        card("DRE-407", "Hand-work", entered=ago(50), labels=["hand-step:machine"],
             comments=[comment(bad_cause, ago(20))]),
        card("DRE-307", "Hand-work", entered=ago(40), comments=[comment(no_kind, ago(10))]),
        card("DRE-308", "Hand-work", entered=ago(40), comments=[comment(empty_kind, ago(9))]),
        card("DRE-107", "Intake", entered=ago(24 * 9), comments=[comment(unparsed, ago(24))]),
    )
    report, text, _ = read(doc)
    assert {h.card for h in report.holds} == {"DRE-407", "DRE-307", "DRE-308", "DRE-107"}
    for h in report.holds:
        assert h.group == "unreadable receipt", h
        assert f'"{h.first_line}"' in text
    assert {h.kind for h in report.holds} == {HAND_STEP, OVERDUE, BLOCKER_OWNER}
    assert " unreadable=4" in text.splitlines()[0]
    assert " open=4" in text.splitlines()[0]


def test_a_whitespace_cause_is_unreadable_never_a_reason():
    # `hygiene.receipt` refuses an empty cause, so this one is written by hand.
    body = "🧹 hygiene: hyg-hand-step —    · 09:00 PT\nevidence: x"
    doc = board(card("DRE-408", "Hand-work", entered=ago(50), labels=["hand-step:person"],
                     comments=[comment(body, ago(20))]))
    report, _, _ = read(doc)
    (hold,) = rows(report, "DRE-408")
    assert hold.group == "unreadable receipt"


# --------------------------------------------------------------------------- #
# a window Linear cut short                                                    #
# --------------------------------------------------------------------------- #


def test_a_cut_short_window_with_no_receipt_is_listed_not_read():
    doc = board(
        card("DRE-409", "Hand-work", entered=ago(50), partial=True,
             comments=[comment("just talk", ago(1))]),
        card("DRE-108", "Intake", entered=ago(50), partial=True),
        card("DRE-309", "In Review", entered=ago(50), partial=True, title="PROOF: cut"),
        # not a waiting lane and not a proof: not this read's
        card("DRE-601", "In Progress", entered=ago(50), partial=True),
        # cut short, but a receipt shows: read as usual
        card("DRE-410", "Hand-work", entered=ago(50), partial=True,
             labels=["hand-step:machine"],
             comments=[comment(hand_step_receipt("machine", "deploy", "x"), ago(3))]),
    )
    report, text, _ = read(doc)
    assert sorted(c.card for c in report.cut_short) == ["DRE-108", "DRE-309", "DRE-409"]
    assert "window cut short — not read" in text
    assert " cut-short=3" in text.splitlines()[0]
    assert {h.card for h in report.holds} == {"DRE-410"}


# --------------------------------------------------------------------------- #
# the writer's own alarm reads back                                            #
# --------------------------------------------------------------------------- #


def test_the_alarm_reconcile_itself_writes_is_read(monkeypatch):
    now = datetime.now(UTC)
    posted: list = []
    monkeypatch.setattr(reconcile.linear_ops, "cmd_comment",
                        lambda ident, body: posted.append((ident, body)))
    promoted = comment("🧹 Auto-promoted Queued → Hand-work: routed OPERATOR", ago(30, now))
    subject = card("DRE-310", "Hand-work", entered=ago(30, now), labels=["operator-step"],
                   comments=[promoted])
    reconcile._hand_work_age(subject, "routed OPERATOR")
    assert posted and posted[0][0] == "DRE-310"
    subject["comments"]["nodes"].insert(0, comment(posted[0][1], now - timedelta(minutes=5)))
    report, _, _ = read(board(subject, taken_at=now))
    (hold,) = rows(report, "DRE-310")
    assert hold.kind == OVERDUE
    assert hold.lane == "Hand-work"
    assert hold.group == "the operator's card, routed OPERATOR"


# --------------------------------------------------------------------------- #
# it reads, and nothing else                                                   #
# --------------------------------------------------------------------------- #


def test_the_command_reads_only_through_a_read_only_context(monkeypatch, tmp_path, capsys):
    fake = FakeLinear({"DRE-201": "In Progress", "DRE-202": "Done"})
    monkeypatch.setattr(linear_ops, "gql", fake)

    def refused(*args, **kwargs):
        raise AssertionError("hold_waits wrote to Linear")

    for name in ("cmd_comment", "cmd_state", "add_label", "remove_label", "_add_blocked_by"):
        monkeypatch.setattr(linear_ops, name, refused)
    path = tmp_path / "board.json"
    path.write_text(json.dumps(every_kind_board()), encoding="utf-8")
    assert script().main(["--board", str(path)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("hold-waits: ")
    assert fake.queries == [hygiene_blockers.CARDS_QUERY]


def test_the_script_adds_no_direct_linear_read_site():
    sites = check_linear_reads.scan(ROOT)
    assert MODULE_PATH.exists()
    assert not [name for name in sites if name.startswith("hold_waits.")]
