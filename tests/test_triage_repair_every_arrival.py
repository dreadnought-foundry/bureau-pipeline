"""RED-first tests: Triage Repair takes every card that arrives in Triage (DRE-8647).

Until this card the repair took one kind of card: in Triage, wearing
`needs-human`, its newest stamp `plan-critic-bound`. On 2026-11-01 none of the
five cards in Triage was that kind, and every other arrival waited for a
person. `config/triage-repair.json` now maps each Triage writer that
`config/triage-reasons.json` declares to one repair — `rewrite`, `replan`,
`relabel` or `none` — read off the marker the writer leaves on the card, and
every card the run selects ends with a receipt.

Every scenario runs over `TriageRepairBoard` (tests/triage_repair_fakes.py),
and the workflow's own `apply` command line and gates are read from
`.github/workflows/triage-repair.yml`, as `tests/test_triage_repair_workflow.py`
reads them.

Run: cd bureau-pipeline && python3 -m pytest tests/test_triage_repair_every_arrival.py -v
"""
from __future__ import annotations

import collections
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")

import dedupe_dispatch  # noqa: E402
import hold  # noqa: E402
import held_verdict  # noqa: E402
import hygiene  # noqa: E402
import medic_next_step  # noqa: E402
import pipeline_act  # noqa: E402
import pipeline_notice  # noqa: E402
import plan_bound  # noqa: E402
import prose_blockers  # noqa: E402
import test_triage_repair_workflow as wf  # noqa: E402 — its workflow readers
import triage_repair  # noqa: E402
from triage_repair_fakes import TriageRepairBoard  # noqa: E402

TABLE = ROOT / "config" / "triage-repair.json"
REASONS = ROOT / "config" / "triage-reasons.json"
HOLDS = ROOT / "config" / "holds.json"
REPO_MAP = ROOT / "config" / "repo-map.json"

#: Each table repair, and the action `select` answers for it on a card with
#: no finding that names the CEO's choice.
ACTION_OF = {"rewrite": "repair", "replan": "replan", "relabel": "relabel", "none": "none",
             "wait": "wait"}
#: The repair whose card select leaves alone: it waits on something the
#: sweep lifts, so nothing is claimed and no receipt is posted (DRE-8849).
WAITING = "wait"
#: The repairs whose apply lifts the card's hold.
LIFTING = ("rewrite", "replan", "relabel")

SHA = "0123456789abcdef0123456789abcdef01234567"
FINDING = "The card never names the file the report is written to, so the build has to guess it."
DESCRIPTION = "The card as it stood.\n\n## Acceptance criteria\n\n- [ ] one\n"
REVISED = "The card, rewritten.\n\n## Acceptance criteria\n\n- [ ] two\n"
#: DRE-8516's shape: created straight into Triage, no hold, no park note.
DESCRIPTION_8516 = ("Console: the release page names the train that shipped each card.\n\n"
                    "## Acceptance criteria\n\n- [ ] the train is named\n")
#: The prose blocker DRE-2676 refuses: a declaring line naming a card the
#: board does not hold.
NOWHERE = "DRE-99999"
PROSE_DESCRIPTION = ("Fix the release note.\n\n**Blocked by:** " + NOWHERE + "\n\n"
                     "## Acceptance criteria\n\n- [ ] one\n")

#: The fake board's clock starts 2026-10-12 18:00 UTC and ticks a minute a
#: write; the run reads this as now, so every card added is days old unless
#: a test says otherwise.
NOW = datetime(2026, 11, 1, 18, 0, tzinfo=UTC)

#: One critic verdict the evidence gate held (DRE-9230), as
#: `held_verdict.Hold` reads it off the pull request.
HELD = held_verdict.Hold("", SHA, "42", "1", "- command output", FINDING)


def _iso(when: datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _table() -> dict:
    return json.loads(TABLE.read_text(encoding="utf-8"))


def _rows() -> list[dict]:
    return _table()["rows"]


def _declared_units() -> list[str]:
    doc = json.loads(REASONS.read_text(encoding="utf-8"))
    return [r["unit"] for r in doc.get("reasons") or []] + [
        r["unit"] for r in doc.get("unreasoned") or []]


def _file_of(unit: str) -> str:
    """`plan.yml#…` → `.github/workflows/plan.yml`, `reconcile.py#…` →
    `scripts/reconcile.py`: where config/holds.json names the unit's file."""
    name = unit.split("#", 1)[0]
    return f".github/workflows/{name}" if name.endswith(".yml") else f"scripts/{name}"


def _stamp(unit: str, reason: str) -> str:
    at = {"no-route": "repo:retired-thing"}.get(reason)
    if hold.CONTRACT_LIFTS[reason] == "new-head" or reason == "inherited-red-main":
        at = SHA
    return hold.stamp_line(reason, at, _file_of(unit))


@pytest.fixture(autouse=True)
def _clock(monkeypatch):
    monkeypatch.setattr(triage_repair, "_now", lambda: NOW)
    # The archived read the hygiene rules make: no repo is archived.
    monkeypatch.setattr(hygiene, "GH_RUNNER", lambda argv: json.dumps({"archived": False}))


def _arrived(board, card: str, when: datetime, *, came_from: str | None = None) -> None:
    """Set the card's lane history: `came_from` first when given, then its
    entry into Triage at `when`."""
    history = [] if came_from is None else [(came_from, _iso(when - timedelta(days=3)))]
    board.cards[card].history = [*history, ("Triage", _iso(when))]


def _select(board, tmp_path, limit: int = 3) -> list[dict]:
    out = tmp_path / "selected.json"
    with board.patched():
        assert triage_repair.main(["select", "--out", str(out), "--limit", str(limit)]) == 0
    return json.loads(out.read_text(encoding="utf-8"))


def _claim(board, card: str) -> int:
    with board.patched():
        return triage_repair.main(["claim", card])


def _apply_as_the_workflow_does(board, row: dict, temp: Path, work: Path) -> int:
    """The `apply` step's own command line for this matrix row, run."""
    argv = wf._apply_argv(row, temp, work)
    with board.patched():
        return triage_repair.main(argv)


def _receipts(board, card: str) -> list[str]:
    return [b for b in board.bodies(card) if b.startswith(triage_repair.RECEIPT_KEY)]


def _outcomes(board, card: str) -> list[str]:
    return [triage_repair.outcome_of(b) for b in _receipts(board, card)]


def _planner_dir(row: dict, temp: Path) -> Path:
    return wf._planner_dir(row, temp)


# --------------------------------------------------------------------------- #
# the table                                                                    #
# --------------------------------------------------------------------------- #


class TestTheTable:
    def test_every_declared_unit_has_exactly_one_row(self):
        units = [r["unit"] for r in _rows()]
        assert sorted(units) == sorted(set(units)), "a unit has two rows"
        assert triage_repair.unhandled_units() == []
        assert set(units) == set(_declared_units())

    def test_the_twenty_split_twelve_one_two_four_one(self):
        assert len(_rows()) == 20
        assert collections.Counter(r["repair"] for r in _rows()) == {
            "rewrite": 12, "replan": 1, "relabel": 2, "none": 4, "wait": 1}

    def test_a_unit_declared_tomorrow_with_no_row_is_named(self):
        reasons = json.loads(REASONS.read_text(encoding="utf-8"))
        reasons["reasons"].append({"unit": "tomorrow.py#park", "reason_at": "tomorrow.py#park",
                                   "anchor": "x", "note": "y"})
        assert triage_repair.unhandled_units(reasons, _table()) == ["tomorrow.py#park"]
        reasons["unreasoned"] = [{"unit": "later.py#move", "why": "z"}]
        assert triage_repair.unhandled_units(reasons, _table()) == [
            "tomorrow.py#park", "later.py#move"]

    def test_a_row_the_reasons_do_not_declare_is_named_too(self):
        table = _table()
        table["rows"].append({"unit": "gone.py#park", "repair": "rewrite",
                              "marker": {"tag": "gone"}})
        reasons = json.loads(REASONS.read_text(encoding="utf-8"))
        assert triage_repair.unhandled_units(reasons, table) == ["gone.py#park"]

    def test_every_row_names_one_repair_and_one_marker(self):
        for row in _rows():
            assert row["repair"] in triage_repair.REPAIR_KINDS, row["unit"]
            assert len(row["marker"]) == 1, row["unit"]
            (kind, value), = row["marker"].items()
            assert kind in ("hold", "tag", "description") and value.strip(), row["unit"]
            assert bool(row.get("why")) is (row["repair"] in ("none", WAITING)), row["unit"]

    def test_rows_sharing_a_marker_share_a_repair(self):
        repairs: dict = {}
        for row in _rows():
            repairs.setdefault(json.dumps(row["marker"], sort_keys=True), set()).add(row["repair"])
        assert all(len(found) == 1 for found in repairs.values()), repairs

    def test_a_hold_marker_is_the_reason_that_writers_holds_row_carries(self):
        holds = json.loads(HOLDS.read_text(encoding="utf-8"))
        for row in _rows():
            reason = row["marker"].get("hold")
            if reason is None:
                continue
            file, scope = _file_of(row["unit"]), row["unit"].split("#", 1)[1]
            sites = [s for s in holds["sites"] if s["file"] == file and s["scope"] == scope]
            assert len(sites) == 1, row["unit"]
            assert reason in [e["reason"] for e in sites[0]["reasons"]], row["unit"]

    def test_a_tag_marker_is_the_tag_its_writer_opens_with(self):
        tags = {r["unit"]: r["marker"].get("tag") for r in _rows()}
        for unit in ("planning_escalation.py#park_unread", "planning_escalation.py#escalate",
                     "reconcile.py#escalate_out_of_planning",
                     "medic.yml#Ask a person about the diagnosis"):
            assert tags[unit] == dedupe_dispatch.STALL_PARK_TAG
        # The medic's screened-out question opens with the stall exit's tag
        # too (DRE-8592), so its row shares that marker and that repair.
        assert medic_next_step.housekeeping_note(
            "DRE-1", "a rule", None, report_path=None, run_url="https://run").startswith(
            f"🧹 {dedupe_dispatch.STALL_PARK_TAG}:")
        assert tags["reconcile.py#_route_to_defect_lane"] == prose_blockers.CARD_TAG
        assert prose_blockers.card_refusal("DRE-1", {NOWHERE}).startswith(
            f"🚨 {prose_blockers.CARD_TAG}:")
        # A critic verdict held twice on one head (DRE-9230): its Triage line
        # opens with its tag, so the row finds the card and no planner
        # rewrites a card whose pull request is still open.
        assert tags["reconcile.py#_triage_held_twice"] == held_verdict.TWICE_TAG
        assert held_verdict.triage_line(1107, SHA, [HELD, HELD]).startswith(
            f"🚦 {held_verdict.TWICE_TAG}:")

    def test_the_description_marker_is_in_what_the_notice_writer_files(self):
        marker = next(r["marker"]["description"] for r in _rows()
                      if r["unit"] == "pipeline_notice.py#_file_card_quietly")
        missed = pipeline_notice.DoorMissed("unreachable", "")
        assert marker in pipeline_notice._description("A notice.", missed)

    def test_holds_declares_the_triage_repair_lift_for_every_reason_it_lifts(self):
        reasons = json.loads(HOLDS.read_text(encoding="utf-8"))["reasons"]
        lifted = {r["marker"]["hold"] for r in _rows()
                  if r["repair"] in LIFTING and "hold" in r["marker"]}
        assert lifted == {"plan-critic-bound", "epic-rereview-twice", "no-route"}
        for reason, entry in reasons.items():
            declared = bool((entry.get("triage_repair_lift") or "").strip())
            assert declared is (reason in lifted), reason

    def test_holds_md_names_every_lift_the_repair_makes(self):
        text = (ROOT / "docs" / "holds.md").read_text(encoding="utf-8")
        for reason in ("plan-critic-bound", "epic-rereview-twice", "no-route"):
            assert (f"reason={reason} because=triage-repair by=triage_repair.py" in text), reason


# --------------------------------------------------------------------------- #
# one fixture card per row                                                     #
# --------------------------------------------------------------------------- #


def _fixture(board, n: int, row: dict) -> str:
    """A card carrying the marker `row`'s writer leaves, and nothing that
    names a choice for the CEO."""
    card = f"DRE-{8700 + n}"
    (kind, value), = row["marker"].items()
    children = ["DRE-1", "DRE-2"] if row["repair"] == "replan" else []
    description = DESCRIPTION
    comments: list = []
    if kind == "hold":
        comments = [f"🛑 Parked in Triage.\n\nWhat the critic found:\n\n1. {FINDING}\n",
                    _stamp(row["unit"], value)]
    elif kind == "tag":
        if value == prose_blockers.CARD_TAG:
            description = PROSE_DESCRIPTION
            comments = [prose_blockers.card_refusal(card, {NOWHERE})]
        else:
            comments = [f"🧹 {value}: {card} — the planner stalled twice."]
    else:
        description = f"A notice.\n\n{value}unreachable), so this notice was filed as a card._\n"
    held = ["needs-human"] if kind == "hold" else []
    board.add(card, labels=[*held, "repo:agent-bureau", "agent:engineer"],
              description=description, comments=comments, children=children)
    return card


def _board_of_every_row():
    board = TriageRepairBoard()
    cards = {_fixture(board, n, row): row for n, row in enumerate(_rows())}
    return board, cards


class TestEveryRow:
    def test_select_returns_every_row_with_the_action_it_names(self, tmp_path, capsys):
        board, cards = _board_of_every_row()
        rows = _select(board, tmp_path, limit=50)
        assert sorted(r["card"] for r in rows) == sorted(
            c for c, row in cards.items() if row["repair"] != WAITING)
        printed = capsys.readouterr().out
        for card, row in cards.items():
            assert f"triage-repair: {card} → {ACTION_OF[row['repair']]} — " in printed, card
        for row in rows:
            assert row["action"] == ACTION_OF[cards[row["card"]]["repair"]], (
                row["card"], cards[row["card"]]["unit"])
        assert board.events == []

    def test_a_card_matching_no_row_is_a_rewrite(self, tmp_path):
        board = TriageRepairBoard()
        board.add("DRE-1", labels=["repo:agent-bureau"],
                  comments=["⏳ a comment no writer's marker opens"])
        assert triage_repair.arrival_row(board._board_node(board.cards["DRE-1"]),
                                         [{"body": "⏳ x", "authored_by_pipeline": True}]) is None
        assert [r["action"] for r in _select(board, tmp_path)] == ["repair"]

    def test_the_live_stamp_wins_over_an_older_note(self, tmp_path):
        board = TriageRepairBoard()
        board.add("DRE-1", comments=[
            f"🧹 {dedupe_dispatch.STALL_PARK_TAG}: DRE-1 — an earlier stall.",
            hold.stamp_line("unfixable-check", SHA, ".github/workflows/agent-fix.yml")])
        assert [r["action"] for r in _select(board, tmp_path)] == ["none"]

    def test_the_selection_rows_keep_their_shape(self, tmp_path):
        board, _cards = _board_of_every_row()
        for row in _select(board, tmp_path, limit=50):
            assert set(row) == {"card", "title", "repo", "action", "why", "findings",
                                "decisions", "max_turns"}
            assert "/" in row["repo"], row

    def test_at_most_three_a_run_oldest_arrival_first(self, tmp_path):
        board, cards = _board_of_every_row()
        order = sorted(c for c, row in cards.items() if row["repair"] != WAITING)
        for i, card in enumerate(order):
            _arrived(board, card, NOW - timedelta(days=2, minutes=i))
        rows = _select(board, tmp_path)
        assert [r["card"] for r in rows] == order[::-1][:3]


class TestEverySelectedCardEndsWithAReceipt:
    """One fixture per row, plus the bound-rewrite epic and a card with no
    findings list: each ends the run with `started` and one finished outcome."""

    FINISHED = set(triage_repair.OUTCOMES) - {triage_repair.STARTED}

    def _planner(self, row: dict, temp: Path) -> None:
        out = _planner_dir(row, temp)
        out.mkdir(parents=True, exist_ok=True)
        if row["action"] == "relabel":
            (out / "repo.txt").write_text("portico\n", encoding="utf-8")
        elif row["action"] == "repair":
            (out / "revised-card.md").write_text(REVISED, encoding="utf-8")
            (out / "repair-answers.md").write_text("1. Named the file.\n", encoding="utf-8")

    def test_no_selected_card_is_left_without_a_receipt(self, tmp_path):
        board, _cards = _board_of_every_row()
        epic = "DRE-8800"
        board.add(epic, children=["DRE-8801"], comments=[
            pipeline_act.receipt("plan-bound-rewrite", plan_bound.receipt_body([FINDING])),
            "🛑 Parked in Triage with needs-human for an operator.",
            hold.stamp_line("plan-critic-bound", None, ".github/workflows/plan.yml")])
        bare = "DRE-8802"
        board.add(bare, comments=["🛑 Parked — the second critic produced no result twice.",
                                  hold.stamp_line("plan-critic-bound", None,
                                                  ".github/workflows/plan.yml")])
        rows = _select(board, tmp_path, limit=50)
        assert {epic, bare} <= {r["card"] for r in rows}
        temp = tmp_path / "temp"
        for row in rows:
            assert _claim(board, row["card"]) == 0, row["card"]
            self._planner(row, temp)
            _apply_as_the_workflow_does(board, row, temp, tmp_path)
            outcomes = _outcomes(board, row["card"])
            assert outcomes[0] == triage_repair.STARTED, row["card"]
            assert len(outcomes) == 2 and outcomes[1] in self.FINISHED, (row["card"], outcomes)
        assert {r["card"]: r["action"] for r in rows}[epic] == "none"
        assert {r["card"]: r["action"] for r in rows}[bare] == "repair"
        assert _select(board, tmp_path, limit=50) == []


# --------------------------------------------------------------------------- #
# the scenarios                                                                #
# --------------------------------------------------------------------------- #


class TestTheProseBlocker:
    def _card(self, board) -> str:
        board.add("DRE-8701", labels=["repo:agent-bureau", "agent:engineer"],
                  description=PROSE_DESCRIPTION,
                  comments=[prose_blockers.card_refusal("DRE-8701", {NOWHERE})])
        return "DRE-8701"

    def test_after_two_hours_it_is_claimed_then_unrepairable_in_one_run(self, tmp_path):
        board = TriageRepairBoard()
        card = self._card(board)
        _arrived(board, card, NOW - timedelta(hours=2, minutes=5), came_from="Queued")
        rows = _select(board, tmp_path)
        assert [(r["card"], r["action"]) for r in rows] == [(card, "none")]
        before = len(board.events)
        assert _claim(board, card) == 0
        assert _apply_as_the_workflow_does(board, rows[0], tmp_path / "temp", tmp_path) == 0
        receipts = _receipts(board, card)
        assert [r.splitlines()[0] for r in receipts] == [
            receipts[0].splitlines()[0],
            "🩹 triage-repair: unrepairable — the description claims a dependency on "
            f"{NOWHERE} that the board does not hold; only its author can reword the line"]
        assert receipts[0].startswith("🩹 triage-repair: started")
        assert board.description(card) == PROSE_DESCRIPTION
        assert board.lane(card) == "Triage"
        assert [k for k, _ in board.writes()[before:]] == ["comment", "comment"]
        assert not [b for b in board.bodies(card) if b.startswith(hold.LIFT_PREFIX)]

    def test_in_its_first_two_hours_hygiene_owns_it(self, tmp_path, capsys):
        board = TriageRepairBoard()
        card = self._card(board)
        _arrived(board, card, NOW - timedelta(minutes=30), came_from="Queued")
        assert _select(board, tmp_path) == []
        assert f"triage-repair: {card} → wait — " in capsys.readouterr().out
        assert board.events == []


class TestTheCardCreatedStraightIntoTriage:
    def _card(self, board) -> str:
        board.add("DRE-8516", labels=["repo:agent-bureau", "agent:engineer"],
                  description=DESCRIPTION_8516)
        return "DRE-8516"

    def test_it_is_a_rewrite_whose_finding_is_its_description(self, tmp_path):
        board = TriageRepairBoard()
        card = self._card(board)
        rows = _select(board, tmp_path)
        assert [(r["card"], r["action"]) for r in rows] == [(card, "repair")]
        assert rows[0]["findings"] == [" ".join(DESCRIPTION_8516.split())]

    def test_it_is_rewritten_and_moved_with_no_hold_to_lift(self, tmp_path):
        board = TriageRepairBoard()
        card = self._card(board)
        rows = _select(board, tmp_path)
        assert _claim(board, card) == 0
        temp = tmp_path / "temp"
        out = _planner_dir(rows[0], temp)
        out.mkdir(parents=True)
        (out / "revised-card.md").write_text(REVISED, encoding="utf-8")
        (out / "repair-answers.md").write_text("1. Named the train.\n", encoding="utf-8")
        assert _apply_as_the_workflow_does(board, rows[0], temp, tmp_path) == 0
        assert board.description(card) == REVISED
        assert board.lane(card) == "Planning"
        assert _outcomes(board, card) == ["started", "rewritten"]
        assert not [b for b in board.bodies(card) if b.startswith(hold.LIFT_PREFIX)]


class TestTheVerdictHeldTwice:
    """DRE-9230: the review sweep moves a card to Triage when the evidence
    gate holds the critic's verdict twice on one head. Only a person can
    answer the held finding, so the repair claims it and says so."""

    def _card(self, board) -> str:
        board.add("DRE-9229", labels=["repo:agent-bureau", "agent:engineer"],
                  description=DESCRIPTION, comments=[held_verdict.triage_line(1107, SHA, [HELD, HELD])])
        return "DRE-9229"

    def test_it_is_unrepairable_and_never_rewritten(self, tmp_path):
        board = TriageRepairBoard()
        card = self._card(board)
        rows = _select(board, tmp_path)
        assert [(r["card"], r["action"]) for r in rows] == [(card, "none")]
        assert _claim(board, card) == 0
        assert _apply_as_the_workflow_does(board, rows[0], tmp_path / "temp", tmp_path) == 0
        assert _outcomes(board, card) == ["started", "unrepairable"]
        assert board.description(card) == DESCRIPTION
        assert board.lane(card) == "Triage"


class TestTheRelabel:
    def _card(self, board) -> str:
        board.add("DRE-8710", labels=["needs-human", "repo:retired-thing", "agent:engineer"],
                  comments=[
                      "🚫 no-repo: DRE-8710 was not routed.",
                      hold.stamp_line("no-route", "repo:retired-thing",
                                      "scripts/planning_route.py")])
        return "DRE-8710"

    def _run(self, board, tmp_path, slug: str) -> tuple[list, int]:
        card = self._card(board)
        rows = _select(board, tmp_path)
        assert [(r["card"], r["action"]) for r in rows] == [(card, "relabel")]
        assert rows[0]["repo"] == "dreadnought-foundry/bureau-pipeline"
        assert _claim(board, card) == 0
        temp = tmp_path / "temp"
        out = _planner_dir(rows[0], temp)
        out.mkdir(parents=True)
        (out / "repo.txt").write_text(f"{slug}\n", encoding="utf-8")
        before = len(board.events)
        rc = _apply_as_the_workflow_does(board, rows[0], temp, tmp_path)
        return board.events[before:], rc

    def test_a_key_of_the_map_is_labelled_lifted_and_moved_in_that_order(self, tmp_path):
        assert "portico" in json.loads(REPO_MAP.read_text(encoding="utf-8"))
        board = TriageRepairBoard()
        events, rc = self._run(board, tmp_path, "portico")
        assert rc == 0
        card = "DRE-8710"
        added = [i for i, e in enumerate(events) if e[:3] == ("add-label", card, "repo:portico")]
        lift = "🔓 hold lifted: reason=no-route because=triage-repair by=triage_repair.py"
        lifted = [i for i, e in enumerate(events) if e[0] == "comment" and e[2] == lift]
        moved = [i for i, e in enumerate(events) if e == ("state", card, "Planning")]
        assert len(added) == len(lifted) == len(moved) == 1, events
        assert added[0] < lifted[0] < moved[0]
        assert [n for n in board.labels(card) if n.startswith("repo:")] == ["repo:portico"]
        assert "needs-human" not in board.labels(card)
        assert board.lane(card) == "Planning"
        assert _outcomes(board, card) == ["started", "rewritten"]

    def test_a_slug_the_map_does_not_hold_is_unfinished_with_nothing_written(self, tmp_path):
        board = TriageRepairBoard()
        events, rc = self._run(board, tmp_path, "nowhere-repo")
        assert rc != 0
        assert [k for k, _c, _d in events] == ["comment"]
        assert _outcomes(board, "DRE-8710") == ["started", "unfinished"]
        assert "nowhere-repo" in _receipts(board, "DRE-8710")[-1]
        assert board.labels("DRE-8710") == ["needs-human", "repo:retired-thing", "agent:engineer"]
        assert board.lane("DRE-8710") == "Triage"


class TestTheUnfixableCheck:
    def test_it_is_claimed_then_unrepairable_in_one_run(self, tmp_path):
        board = TriageRepairBoard()
        card = "DRE-8720"
        board.add(card, comments=[
            "🛑 The fix loop cannot clear these checks: `lint`.",
            hold.stamp_line("unfixable-check", SHA, ".github/workflows/agent-fix.yml")])
        rows = _select(board, tmp_path)
        assert [(r["card"], r["action"]) for r in rows] == [(card, "none")]
        before = len(board.events)
        assert _claim(board, card) == 0
        assert _apply_as_the_workflow_does(board, rows[0], tmp_path / "temp", tmp_path) == 0
        assert [k for k, _ in board.writes()[before:]] == ["comment", "comment"]
        assert _outcomes(board, card) == ["started", "unrepairable"]
        assert _receipts(board, card)[-1].startswith("🩹 triage-repair: unrepairable — ")
        assert "needs-human" in board.labels(card)
        assert board.lane(card) == "Triage"
        assert board.description(card) == "The card as it stood.\n"


class TestTheBoundRewriteEpic:
    def test_it_is_claimed_then_unrepairable_naming_the_spent_rewrite(self, tmp_path):
        board = TriageRepairBoard()
        card = "DRE-8730"
        board.add(card, children=["DRE-8731"], comments=[
            pipeline_act.receipt("plan-bound-rewrite", plan_bound.receipt_body([FINDING])),
            "🛑 Parked in Triage with needs-human for an operator.",
            hold.stamp_line("plan-critic-bound", None, ".github/workflows/plan.yml")])
        rows = _select(board, tmp_path)
        assert [(r["card"], r["action"]) for r in rows] == [(card, "none")]
        before = len(board.events)
        assert _claim(board, card) == 0
        assert _apply_as_the_workflow_does(board, rows[0], tmp_path / "temp", tmp_path) == 0
        assert [k for k, _ in board.writes()[before:]] == ["comment", "comment"]
        newest = _receipts(board, card)[-1].splitlines()[0]
        assert newest.startswith("🩹 triage-repair: unrepairable — the planner has had its "
                                 "one bound rewrite on this epic")
        assert board.lane(card) == "Triage"


class TestTheHygieneRules:
    def _retired(self, board) -> str:
        """A card hygiene's `retired` rule settles: its repo: label names no
        key of config/repo-map.json."""
        board.add("DRE-8740", labels=["repo:gone-repo", "agent:engineer"])
        return "DRE-8740"

    def test_not_selected_in_its_first_two_hours(self, tmp_path, capsys):
        board = TriageRepairBoard()
        card = self._retired(board)
        _arrived(board, card, NOW - timedelta(hours=1, minutes=59))
        assert _select(board, tmp_path) == []
        out = capsys.readouterr().out
        assert f"triage-repair: {card} → wait — " in out and "retired" in out

    def test_selected_after_two_hours_if_still_there(self, tmp_path):
        board = TriageRepairBoard()
        card = self._retired(board)
        _arrived(board, card, NOW - timedelta(hours=2, minutes=1))
        assert [(r["card"], r["action"]) for r in _select(board, tmp_path)] == [(card, "repair")]

    def test_a_card_no_rule_settles_is_taken_at_once(self, tmp_path):
        board = TriageRepairBoard()
        board.add("DRE-8741", labels=["repo:agent-bureau"])
        _arrived(board, "DRE-8741", NOW - timedelta(minutes=5))
        assert [r["card"] for r in _select(board, tmp_path)] == ["DRE-8741"]

    def test_the_rules_are_hygienes_own(self):
        import hygiene_triage
        assert triage_repair.HYGIENE_RULES == ("moot", "retired", "proof_in_review",
                                               "prose_blocker", "loop")
        for name in triage_repair.HYGIENE_RULES:
            assert callable(getattr(hygiene_triage, name)), name


class TestOncePerCard:
    @pytest.mark.parametrize("outcome", list(triage_repair.OUTCOMES))
    def test_any_receipt_from_an_earlier_stay_is_never_selected_again(
            self, tmp_path, capsys, outcome):
        board, cards = _board_of_every_row()
        for card in cards:
            board.say(card, f"🩹 triage-repair: {outcome} — an earlier stay in Triage.")
        assert _select(board, tmp_path, limit=50) == []
        out = capsys.readouterr().out
        assert all(f"triage-repair: {card} → spent — " in out for card in cards)


# --------------------------------------------------------------------------- #
# the contract and the wiring                                                  #
# --------------------------------------------------------------------------- #


class TestTheContract:
    def test_the_outcomes_add_unrepairable(self):
        assert triage_repair.OUTCOMES == ("started", "rewritten", "asked", "replanned",
                                          "split", "unfinished", "unrepairable")
        assert triage_repair.RECEIPT_KEY == "🩹 triage-repair:"

    def test_the_receipt_opens_with_its_why(self):
        body = triage_repair.unrepairable_body("the card is held for a red check")
        assert body.splitlines()[0] == ("🩹 triage-repair: unrepairable — "
                                        "the card is held for a red check")
        assert triage_repair.outcome_of(body) == "unrepairable"


class TestTheWorkflowWiring:
    def _gate(self, step_name: str, action: str) -> bool:
        step = wf._step("repair", step_name)
        return wf._evaluate(step["if"], {"steps.claim.outputs.claimed": "true",
                                         "matrix.action": action})

    def test_the_planner_runs_for_a_relabel_and_never_for_none(self):
        for name in (wf.AGENT_STEP, "Planner context", "Read the card for the planner",
                     "Select model", "Install Claude Code (asserted)"):
            assert self._gate(name, "relabel"), name
            assert not self._gate(name, "none"), name

    def test_apply_runs_for_both_and_none_reads_no_directory(self, tmp_path):
        apply = next(s for s in wf._job("repair")["steps"]
                     if wf._runs(s, "triage_repair.py apply"))
        for action in ("relabel", "none"):
            assert wf._evaluate(apply["if"], {"steps.claim.outputs.claimed": "true",
                                              "matrix.action": action})
        row = {"card": "DRE-1", "title": "t", "repo": "dreadnought-foundry/agent-bureau",
               "action": "none", "why": "w", "findings": [], "decisions": [],
               "max_turns": 120}
        assert wf._apply_argv(row, tmp_path / "temp", tmp_path) == [
            "apply", "DRE-1", "--action", "none"]
        relabel = {**row, "action": "relabel"}
        assert "--dir" in wf._apply_argv(relabel, tmp_path / "temp", tmp_path)

    def test_the_hand_off_carries_relabel_and_none(self, tmp_path):
        import subprocess
        step = wf._step("select", "Hand the worked cards to the repair")
        rows = [{"card": f"DRE-{i}", "action": a} for i, a in enumerate(
            ("repair", "escalate", "replan", "relabel", "none", "spent", "wait"))]
        (tmp_path / "cards.json").write_text(json.dumps(rows), encoding="utf-8")
        output = tmp_path / "out"
        output.write_text("")
        done = subprocess.run(["bash", "-e", "-c", step["run"]], cwd=tmp_path,
                              env={**os.environ, "GITHUB_OUTPUT": str(output)},
                              capture_output=True, text=True)
        assert done.returncode == 0, done.stderr
        cards = json.loads(output.read_text().split("cards=", 1)[1])
        assert [c["action"] for c in cards] == ["repair", "escalate", "replan", "relabel", "none"]
