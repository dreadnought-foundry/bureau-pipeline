"""The sweep dispatches a proof run at a PROOF card (DRE-5926).

`scripts/proof_dispatch.py` is the phase that starts a proof run when an
epic's build cards are Done and the release carrying their merges is live. It
DECIDES; the two readings it acts on are DRE-5922's (`proof_run_state`,
`proof_release`) and are stubbed here, the dispatch is `plan_run.fire` and is
recorded, and every Linear and GitHub read is injected — no network.

Pinned here, over a fixture board:

  * the seven first-run conditions, each refusing BY NAME and IN ORDER;
  * an eligible card dispatched once, `event="proof-execute"`, its receipt
    posted only after a confirmed dispatch;
  * one dispatch per pass, oldest first; the second dispatch on
    `never-started` and on `dead`; the hold after two;
  * a proof that cannot run yet does not keep the front (DRE-6464): a hold
    the lane read shows is named every pass at no read, and the rest begin
    at the pass's turn, so an eligible card behind three that cannot run is
    dispatched within two passes, under the same read bound;
  * the return after the CEO's signed answer, served before a first run;
  * the re-run after the critic's findings (DRE-5931): its trigger, its
    budget of two counted apart from the first run's, its three stops, the
    hold after two, and its place behind the first runs under one cap;
  * the dry run, which writes nothing;
  * the read bound: five candidates cost at most eight Linear requests;
  * the step in `reconcile.yml`: gated on a full pass, the stub tested before
    any Python runs.
"""

from __future__ import annotations

import dataclasses
import json
import os
import stat
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import linear_ops  # noqa: E402
import pipeline_act  # noqa: E402
import plan_run  # noqa: E402
import proof_dispatch  # noqa: E402
import proof_record  # noqa: E402
import proof_release  # noqa: E402
import proof_run_state  # noqa: E402
import spoken_thread  # noqa: E402

REPO = "dreadnought-foundry/bureau-pipeline"
SLUG = "bureau-pipeline"
EPIC = "DRE-5920"
VIEWER = "pipeline-key-user"
PERSON = "a-person"
#: A comment the fake `voices` reads as console-signed — the real reader
#: checks a signature; this suite only needs to know which comments passed.
SIGNED = "console-signed"
#: 10:30 PT. Its turn begins the proof ring at its front for any ring under
#: two hundred (`proof_dispatch._turned`), so the oldest-first and cap pins
#: read from the front — an edit to it must keep that, or they move for no
#: visible reason (DRE-6464).
NOW = datetime(2026, 10, 6, 17, 30, tzinfo=timezone.utc)
WORKFLOW = ROOT / ".github" / "workflows" / "reconcile.yml"
STEP = "Dispatch proof runs"


def _at(minutes_ago: float) -> str:
    return (NOW - timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z")


def comment(body: str, minutes_ago: float, user: str | None = VIEWER) -> dict:
    return {"body": body, "createdAt": _at(minutes_ago),
            "user": {"id": user} if user else None}


def promoted(minutes_ago: float) -> dict:
    return comment("🧹 Auto-promoted Backlog → Hand-work: the proof is a "
                   "person's to confirm.", minutes_ago)


def hold(observed: str, needs: str, minutes_ago: float, user=VIEWER) -> dict:
    return comment(linear_ops.proof_waiting_line(observed, needs), minutes_ago, user)


def ceo_press_hold(minutes_ago: float) -> dict:
    return hold("the CEO signs in to the console",
                "the CEO's press: sign in once", minutes_ago)


def answer(minutes_ago: float) -> dict:
    return comment("Answer from Sid, 2026-10-06 09:30 PT:\nDone, signed in.",
                   minutes_ago, SIGNED)


def receipt(minutes_ago: float, reason="first proof run",
            count="dispatch 1 of 2", at="2026-10-06 08:00 PT") -> dict:
    return comment(f"🔬 proof-run: dispatched a proof run at {at} — {reason} "
                   f"({count})", minutes_ago)


def lane_card(ident: str, *, title: str | None = None, repo: str = SLUG,
              state: str = "Hand-work", labels=(), window=None,
              entered: float = 600) -> dict:
    nodes = list(window if window is not None else [promoted(entered)])
    return {
        "id": f"uuid-{ident}", "identifier": ident,
        "title": title or f"PROOF: {ident} observed live",
        "description": "The CEO reads this record and closes this card.",
        "updatedAt": _at(1),
        "state": {"name": state},
        "labels": {"nodes": [{"name": f"repo:{repo}"}, {"name": "agent:ops"},
                             *({"name": name} for name in labels)]},
        "children": {"nodes": []},
        "comments": {"pageInfo": {"hasNextPage": False},
                     "nodes": list(reversed(nodes))},
    }


def detail(*, epic_state="In Progress", blockers=None, siblings=None,
           partial=False, parent=True) -> dict:
    blockers = {"DRE-5921": "Done", "DRE-5922": "Done"} if blockers is None else blockers
    siblings = ([("DRE-5921", "Done", SLUG), ("DRE-5922", "Done", SLUG)]
                if siblings is None else siblings)
    out = {
        "inverseRelations": {
            "pageInfo": {"hasNextPage": partial, "endCursor": None},
            "nodes": [{"type": "blocks",
                       "issue": {"identifier": i, "state": {"name": s}}}
                      for i, s in blockers.items()],
        },
        "parent": None,
    }
    if parent:
        out["parent"] = {
            "identifier": EPIC, "state": {"name": epic_state},
            "children": {"nodes": [
                {"identifier": i, "title": f"bureau-pipeline: build {i}",
                 "state": {"name": s},
                 "labels": {"nodes": [{"name": f"repo:{r}"}]}}
                for i, s, r in siblings]},
        }
    return out


class Board:
    """The injected Linear reads, counted."""

    def __init__(self, hand=(), green=(), details=None, threads=None, review=()):
        self.lanes = {"Hand-work": list(hand), "Green Light": list(green),
                      "In Review": list(review)}
        self.details = details or {}
        self.threads = threads or {}
        self.reads: list = []

    def lane(self, state):
        self.reads.append(("lane", state))
        return list(self.lanes.get(state, []))

    def card(self, ident):
        self.reads.append(("card", ident))
        return self.details.get(ident) or detail()

    def thread(self, ident):
        self.reads.append(("thread", ident))
        return list(self.threads.get(ident, [])), VIEWER


def state(name, *, dispatches=0, receipts=(), run_id=None, lines=None,
          record=None):
    return proof_run_state.State(
        name, list(lines or [f"{name} — fixture"]), dispatches, run_id,
        list(receipts), record)


def fake_voices(nodes, viewer, *, card):
    out = []
    for node in nodes:
        who = (node.get("user") or {}).get("id")
        kind = (spoken_thread.CEO_VIA_CONSOLE if who == SIGNED
                else spoken_thread.PIPELINE if who == viewer
                else spoken_thread.PERSON)
        out.append(spoken_thread.Voice(kind, f"{kind} label",
                                       node.get("createdAt"), node.get("body")))
    return out


class Harness:
    """One pass: the board, the stubbed readings, and the recorded writes."""

    def __init__(self, monkeypatch, board, *, states=None, release="ready",
                 release_lines=None, fire_ok=True, prs=None, records=None,
                 texts=None):
        self.board = board
        self.states = states or {}
        self.release_state = release
        self.release_lines = release_lines or [f"{release} — fixture"]
        self.release_calls: list = []
        self.fired: list = []
        self.fire_ok = fire_ok
        self.prs = prs
        self.records = records or {}
        self.record_reads: list = []
        self.texts = texts or {}
        self.text_reads: list = []
        self.posted: list = []
        self.holds: list = []
        self.order: list = []
        monkeypatch.setattr(linear_ops, "cmd_comment", self._comment)
        monkeypatch.setattr(linear_ops, "cmd_proof_waiting", self._hold)

    def _comment(self, ident, body, *flags):
        self.order.append(("comment", ident))
        self.posted.append((ident, body))

    def _hold(self, ident, observed, needs):
        self.order.append(("hold", ident))
        self.holds.append((ident, observed, needs))

    def run_state(self, repo, ident, comments, viewer, *, read, now):
        return self.states.get(ident) or state("none")

    def release(self, repo, merges, *, read):
        self.release_calls.append((repo, list(merges)))
        return proof_release.Reading(self.release_state, list(self.release_lines))

    def find_pr(self, ident):
        if self.prs is not None:
            got = self.prs.get(ident)
            if isinstance(got, Exception):
                raise got
            return got
        return {"number": int(ident.split("-")[1]), "state": "MERGED",
                "headRefName": f"agent/{ident}-build",
                "mergeCommit": {"oid": f"{ident[-4:]}abcdef0123456789"}}

    def find_record(self, ident):
        self.record_reads.append(ident)
        got = self.records.get(ident)
        if isinstance(got, Exception):
            raise got
        return got

    def read_record(self, pr, head):
        """The record at `head`, as `proof_record.fetch` answers: never
        raises, a record not read is one with no text and the reason."""
        self.text_reads.append((pr.get("number"), head))
        got = self.texts.get(pr.get("number"))
        if got is None or isinstance(got, Exception):
            why = f"{RECORD_PATH} could not be read at {head}: {got or 'no fixture'}"
            return proof_record.Record(RECORD_PATH, None, why)
        return proof_record.Record(RECORD_PATH, got, None)

    def fire(self, card, repo, *, reason=None, event=None):
        self.order.append(("fire", card["identifier"]))
        self.fired.append((card["identifier"], repo, reason, event))
        return (True, "") if self.fire_ok else (False, "gh api refused: HTTP 403")

    def sweep(self, *, live=True, now=NOW):
        return proof_dispatch.sweep(
            REPO, SLUG, live=live, linear=self.board, read=lambda path: None,
            find_pr=self.find_pr, run_state=self.run_state,
            release=self.release, fire=self.fire, voices=fake_voices, now=now,
            find_record=self.find_record, read_record=self.read_record)


def _lines(capsys) -> list:
    return capsys.readouterr().out.splitlines()


def _about(lines, ident) -> list:
    return [line for line in lines if ident in line]


# --------------------------------------------------------------------------- #
# the seven first-run conditions, by name and in order                         #
# --------------------------------------------------------------------------- #


def test_an_eligible_card_is_dispatched_once_with_the_proof_event(monkeypatch, capsys):
    board = Board(hand=[lane_card("DRE-5930")])
    h = Harness(monkeypatch, board)
    tally = h.sweep()
    assert h.fired == [("DRE-5930", REPO, "first proof run", "proof-execute")]
    assert plan_run.PROOF_EVENT == "proof-execute"
    assert tally.dispatched == 1
    assert len(h.posted) == 1
    ident, body = h.posted[0]
    assert ident == "DRE-5930"
    first = body.split("\n", 1)[0]
    assert first.startswith("🔬 proof-run: dispatched a proof run at ")
    assert first.endswith(" — first proof run (dispatch 1 of 2)")
    # The receipt is the line DRE-5922's reader parses, and it is composed.
    parsed = proof_run_state.receipt(body)
    assert parsed is not None and parsed.reason == "first proof run"
    assert parsed.count == "dispatch 1 of 2"
    assert pipeline_act.read_trailer(body)["act"] == "proof-run-dispatched"
    assert h.order == [("fire", "DRE-5930"), ("comment", "DRE-5930")]


def test_a_proof_wearing_only_agent_ops_and_no_code_is_a_first_run_candidate(monkeypatch, capsys):
    """DRE-6227: the stamp puts `no-code` alone on a proof card, and nothing
    automatic applies `hand-built` or a proof's `operator-step` any more. A
    Hand-work proof carrying neither is still the proof run's to start."""
    card = lane_card("DRE-5930", labels=("no-code",))
    names = {node["name"] for node in card["labels"]["nodes"]}
    assert names == {f"repo:{SLUG}", "agent:ops", "no-code"}
    board = Board(hand=[card])
    h = Harness(monkeypatch, board)
    tally = h.sweep()
    assert h.fired == [("DRE-5930", REPO, "first proof run", "proof-execute")]
    assert tally.dispatched == 1


def test_the_receipt_is_posted_only_after_a_confirmed_dispatch(monkeypatch, capsys):
    board = Board(hand=[lane_card("DRE-5930")])
    h = Harness(monkeypatch, board, fire_ok=False)
    tally = h.sweep()
    assert h.fired and h.posted == []
    assert tally.failures
    assert any("HTTP 403" in line for line in _lines(capsys))


def test_condition_1_a_proof_in_another_repo_is_refused_by_name(monkeypatch, capsys):
    board = Board(hand=[lane_card("DRE-5930", repo="portico"),
                        lane_card("DRE-5931", title="bureau-pipeline: a build")])
    h = Harness(monkeypatch, board)
    h.sweep()
    lines = _lines(capsys)
    assert h.fired == []
    assert any("condition 1" in line and "portico" in line
               for line in _about(lines, "DRE-5930"))
    # A card that is not a proof is no candidate at all, and costs nothing.
    assert ("card", "DRE-5931") not in board.reads
    assert ("card", "DRE-5930") not in board.reads


def test_condition_2_the_epic_must_be_in_progress(monkeypatch, capsys):
    board = Board(hand=[lane_card("DRE-5930")],
                  details={"DRE-5930": detail(epic_state="Green Light",
                                              blockers={"DRE-5921": "In Progress"})})
    h = Harness(monkeypatch, board)
    h.sweep()
    lines = _about(_lines(capsys), "DRE-5930")
    assert h.fired == []
    # The FIRST failing condition is the one named, and the pass moves on.
    assert any("condition 2" in line and "Green Light" in line for line in lines)
    assert not any("condition 3" in line for line in lines)
    assert ("thread", "DRE-5930") not in board.reads


def test_condition_2_a_proof_with_no_parent_epic(monkeypatch, capsys):
    board = Board(hand=[lane_card("DRE-5930")],
                  details={"DRE-5930": detail(parent=False)})
    h = Harness(monkeypatch, board)
    h.sweep()
    assert h.fired == []
    assert any("condition 2" in line for line in _about(_lines(capsys), "DRE-5930"))


def test_condition_3_an_open_blocker_refuses(monkeypatch, capsys):
    board = Board(hand=[lane_card("DRE-5930")],
                  details={"DRE-5930": detail(blockers={"DRE-5921": "Done",
                                                        "DRE-5922": "In Review",
                                                        "DRE-5923": "Canceled"})})
    h = Harness(monkeypatch, board)
    h.sweep()
    lines = _about(_lines(capsys), "DRE-5930")
    assert h.fired == []
    refusal = [line for line in lines if "condition 3" in line]
    assert refusal and "DRE-5922" in refusal[0] and "DRE-5923" not in refusal[0]


def test_condition_3_relations_not_read_to_the_end_refuse(monkeypatch, capsys):
    board = Board(hand=[lane_card("DRE-5930")],
                  details={"DRE-5930": detail(partial=True)})
    h = Harness(monkeypatch, board)
    h.sweep()
    assert h.fired == []
    assert any("condition 3" in line for line in _about(_lines(capsys), "DRE-5930"))


# A proof is blocked by every other card in its epic, so a big epic's proof
# fills the first relation page (DRE-6416: DRE-6274 held 21, all terminal, and
# was refused on every pass). `LinearReads.card` reads such a page to the end.

def _relation(ident: str, state_name: str) -> dict:
    return {"type": "blocks", "issue": {"identifier": ident, "state": {"name": state_name}}}


class PagedLinear:
    """`linear_ops.gql` for the real `LinearReads.card`: the card's read
    returns a full first page of Done blockers, and the top-up read returns
    `rest` — or raises `fail`."""

    def __init__(self, rest=(), fail=None):
        self.rest, self.fail = list(rest), fail
        self.topups: list = []

    def __call__(self, query, variables=None):
        if query == proof_dispatch.CARD_QUERY:
            first = [_relation(f"DRE-60{i:02d}", "Done")
                     for i in range(proof_dispatch.reconcile.INVERSE_PAGE)]
            issue = detail()  # the query asks for no identifier
            issue["inverseRelations"] = {
                "pageInfo": {"hasNextPage": True, "endCursor": "cursor-20"},
                "nodes": first}
            return {"issue": issue}
        self.topups.append(dict(variables or {}))
        if self.fail:
            raise self.fail
        return {"issue": {"inverseRelations": {
            "pageInfo": {"hasNextPage": False, "endCursor": "cursor-end"},
            "nodes": self.rest}}}


class LiveCardBoard(Board):
    """The fixture board, with each card's relations read by the production
    `LinearReads.card` over the stubbed `linear_ops.gql`."""

    def card(self, ident):
        self.reads.append(("card", ident))
        return proof_dispatch.LinearReads().card(ident)


def _paged(monkeypatch, gql) -> Harness:
    monkeypatch.setattr(linear_ops, "gql", gql)
    monkeypatch.setattr(proof_dispatch.reconcile, "_inverse_topup_refused", [])
    return Harness(monkeypatch, LiveCardBoard(hand=[lane_card("DRE-5930")]))


def test_condition_3_a_full_first_page_is_read_to_the_end_and_passes(monkeypatch, capsys):
    gql = PagedLinear(rest=[_relation("DRE-6020", "Done")])
    h = _paged(monkeypatch, gql)
    h.sweep()
    assert gql.topups == [{"id": "DRE-5930", "after": "cursor-20"}]
    assert not any("condition 3" in line for line in _about(_lines(capsys), "DRE-5930"))
    assert h.fired == [("DRE-5930", REPO, "first proof run", "proof-execute")]


def test_condition_3_an_open_blocker_on_the_second_page_is_named(monkeypatch, capsys):
    gql = PagedLinear(rest=[_relation("DRE-6020", "In Review")])
    h = _paged(monkeypatch, gql)
    h.sweep()
    assert h.fired == []
    refusal = [line for line in _about(_lines(capsys), "DRE-5930") if "condition 3" in line]
    assert refusal and "not every blocker is terminal" in refusal[0]
    assert "DRE-6020 is In Review" in refusal[0]
    assert "could not be read to the end" not in refusal[0]


def test_condition_3_a_failed_top_up_read_stays_unknown_never_eligible(monkeypatch, capsys):
    gql = PagedLinear(fail=RuntimeError("HTTP 502"))
    h = _paged(monkeypatch, gql)
    h.sweep()
    assert gql.topups, "the rest of the full page was never asked for"
    assert h.fired == []
    refusal = [line for line in _about(_lines(capsys), "DRE-5930") if "condition 3" in line]
    assert refusal and "could not be read to the end" in refusal[0]


def test_condition_4_needs_human_holds(monkeypatch, capsys):
    board = Board(hand=[lane_card("DRE-5930", labels=("needs-human",))])
    h = Harness(monkeypatch, board)
    tally = h.sweep()
    assert h.fired == []
    assert tally.held == 1
    assert any("condition 4" in line and "needs-human" in line
               for line in _about(_lines(capsys), "DRE-5930"))
    # The label is on the lane read: naming it spends no candidate read.
    assert [r for r in board.reads if r[0] != "lane"] == []


def test_condition_4_an_undischarged_proof_waiting_blocks_a_first_run(monkeypatch, capsys):
    thread = [promoted(600), hold("being observed by hand",
                                  "the operator's record pull request", 300)]
    board = Board(hand=[lane_card("DRE-5930")], threads={"DRE-5930": thread})
    h = Harness(monkeypatch, board)
    h.sweep()
    assert h.fired == []
    assert any("condition 4" in line and "being observed by hand" in line
               for line in _about(_lines(capsys), "DRE-5930"))


def test_condition_4_a_later_proof_observed_discharges_an_operators_hold(monkeypatch, capsys):
    thread = [promoted(600),
              hold("being observed by hand", "the operator's record pull request", 300),
              comment(linear_ops.proof_observed_line("seen live at 09:00 PT"), 60)]
    board = Board(hand=[lane_card("DRE-5930")], threads={"DRE-5930": thread})
    h = Harness(monkeypatch, board)
    h.sweep()
    assert [f[0] for f in h.fired] == ["DRE-5930"]


def test_condition_4_a_ceo_press_hold_is_discharged_only_by_his_signed_answer(monkeypatch, capsys):
    # An unsigned comment claiming to be his answer discharges nothing.
    forged = [promoted(600), ceo_press_hold(300),
              comment("Answer from Sid, 2026-10-06 09:30 PT:\nDone.", 60, PERSON)]
    board = Board(hand=[lane_card("DRE-5930")], threads={"DRE-5930": forged})
    h = Harness(monkeypatch, board)
    h.sweep()
    assert h.fired == []
    assert any("condition 4" in line for line in _about(_lines(capsys), "DRE-5930"))

    signed = [promoted(600), ceo_press_hold(300), answer(60)]
    board = Board(hand=[lane_card("DRE-5930")], threads={"DRE-5930": signed})
    h = Harness(monkeypatch, board)
    h.sweep()
    assert [f[0] for f in h.fired] == ["DRE-5930"]


@pytest.mark.parametrize("name", ["someone-else", "running", "finished", "unknown"])
def test_condition_5_a_run_that_is_not_free_dispatches_nothing(monkeypatch, capsys, name):
    why = f"{name} — the fixture's reason for {name}"
    board = Board(hand=[lane_card("DRE-5930")])
    h = Harness(monkeypatch, board,
                states={"DRE-5930": state(name, lines=[why], dispatches=1)})
    h.sweep()
    lines = _about(_lines(capsys), "DRE-5930")
    assert h.fired == [] and h.posted == [] and h.holds == []
    assert any("condition 5" in line and why in line for line in lines), lines


def test_condition_6_second_dispatch_after_a_run_that_never_started(monkeypatch, capsys):
    first = proof_run_state.Receipt("first proof run", "dispatch 1 of 2",
                                    "2026-10-06 08:00 PT")
    board = Board(hand=[lane_card("DRE-5930")])
    h = Harness(monkeypatch, board, states={"DRE-5930": state(
        "never-started", dispatches=1, receipts=[first])})
    h.sweep()
    reason = "second dispatch — no run started after 2026-10-06 08:00 PT"
    assert h.fired == [("DRE-5930", REPO, reason, "proof-execute")]
    assert h.posted[0][1].split("\n", 1)[0].endswith(
        f" — {reason} (dispatch 2 of 2)")


def test_condition_6_second_dispatch_after_a_dead_run(monkeypatch, capsys):
    first = proof_run_state.Receipt("first proof run", "dispatch 1 of 2",
                                    "2026-10-06 08:00 PT")
    dead = ("dead — run 35712345678 ended failure at 08:40 PT with no record")
    board = Board(hand=[lane_card("DRE-5930")])
    h = Harness(monkeypatch, board, states={"DRE-5930": state(
        "dead", dispatches=1, receipts=[first], run_id="35712345678",
        lines=[dead])})
    h.sweep()
    reason = ("second dispatch — run 35712345678 ended failure at 08:40 PT "
              "with no record")
    assert h.fired == [("DRE-5930", REPO, reason, "proof-execute")]
    assert h.posted[0][1].split("\n", 1)[0].endswith(
        f" — {reason} (dispatch 2 of 2)")


@pytest.mark.parametrize("name", ["never-started", "dead"])
def test_condition_6_after_two_dispatches_one_hold_and_never_again(monkeypatch, capsys, name):
    board = Board(hand=[lane_card("DRE-5930")])
    h = Harness(monkeypatch, board, states={"DRE-5930": state(name, dispatches=2)})
    h.sweep()
    assert h.fired == [] and h.posted == []
    assert h.holds == [("DRE-5930",
                        "the proof run did not finish after two dispatches",
                        "an operator reading the two 🔬 proof-run receipts and "
                        "the Actions runs they name")]
    assert any("condition 6" in line for line in _about(_lines(capsys), "DRE-5930"))

    # The hold already stands — discharged by hand, even — and is not posted
    # twice; the card is still never dispatched again.
    thread = [promoted(600),
              hold("the proof run did not finish after two dispatches",
                   "an operator reading the two 🔬 proof-run receipts and the "
                   "Actions runs they name", 120),
              comment(linear_ops.proof_observed_line("read the runs"), 60)]
    board = Board(hand=[lane_card("DRE-5930")], threads={"DRE-5930": thread})
    h = Harness(monkeypatch, board, states={"DRE-5930": state(name, dispatches=2)})
    h.sweep()
    assert h.fired == [] and h.holds == [] and h.posted == []


def test_condition_6_rerun_receipts_never_spend_the_first_run_budget(monkeypatch, capsys):
    """One first-run receipt and two critic re-runs: one first-run dispatch is
    still left. Read through DRE-5922's real reader, not a stub."""
    run = "35799999999"
    thread = [
        promoted(900),
        receipt(800),
        comment("⏳ 5/5 PR opened", 700),
        receipt(500, reason="re-run on the critic's findings", count="re-run 1 of 2"),
        receipt(300, reason="re-run on the critic's findings", count="re-run 2 of 2"),
        comment("🧠 model-attempt: claude-opus-5-5 — proof agent starting. Run: "
                f"https://github.com/{REPO}/actions/runs/{run}", 280),
    ]
    reads = {
        f"repos/{REPO}/pulls?head=dreadnought-foundry:agent/DRE-5930-"
        "proof-record&state=all&per_page=100": [],
        f"repos/{REPO}/actions/runs/{run}": {
            "status": "completed", "conclusion": "failure",
            "updated_at": _at(200)},
    }
    board = Board(hand=[lane_card("DRE-5930")], threads={"DRE-5930": thread})
    h = Harness(monkeypatch, board)
    proof_dispatch.sweep(
        REPO, SLUG, live=True, linear=board, read=lambda path: reads[path],
        find_pr=h.find_pr, run_state=proof_run_state.reading,
        release=h.release, fire=h.fire, voices=fake_voices, now=NOW)
    assert len(h.fired) == 1
    assert h.fired[0][2].startswith(f"second dispatch — run {run} ended failure at ")
    assert h.posted[0][1].split("\n", 1)[0].endswith("(dispatch 2 of 2)")


@pytest.mark.parametrize("reading", ["waiting", "unknown"])
def test_condition_7_a_release_not_ready_never_dispatches(monkeypatch, capsys, reading):
    line = f"{reading} — whole repository: stable does not carry #5921"
    board = Board(hand=[lane_card("DRE-5930")])
    h = Harness(monkeypatch, board, release=reading, release_lines=[line])
    tally = h.sweep()
    lines = _about(_lines(capsys), "DRE-5930")
    assert h.fired == [] and h.posted == []
    assert any("condition 7" in l and line in l for l in lines), lines
    if reading == "waiting":
        assert tally.waiting == 1


@pytest.mark.parametrize("live", [True, False])
def test_condition_7_a_ready_release_prints_its_lines_for_the_card_it_dispatches(
        monkeypatch, capsys, live):
    """DRE-6516: `ready` leaves the gate's reading in the log, one
    `proof-release:` line per line of it, naming the card it dispatches — the
    line a proof record quotes. Printed before the card's eligible line."""
    lines = ["ready — whole repository: stable carries #5921 (abcdef0) for DRE-5921",
             "ready — console: untouched — #5922 (1234567) for DRE-5922 changed "
             "nothing under console/ that it does not ignore"]
    board = Board(hand=[lane_card("DRE-5930")])
    h = Harness(monkeypatch, board, release="ready", release_lines=lines)
    tally = h.sweep(live=live)
    out = _lines(capsys)
    released = [l for l in out if l.startswith(f"{proof_release.TAG}:")]
    assert released == [f"proof-release: DRE-5930 — {line}" for line in lines]
    assert tally.dispatched == 1
    eligible = next(i for i, l in enumerate(out) if "DRE-5930 — eligible:" in l)
    assert out.index(released[-1]) < eligible


@pytest.mark.parametrize("reading", ["waiting", "unknown"])
def test_condition_7_a_release_not_ready_prints_no_ready_line(monkeypatch, capsys, reading):
    """A refused card's reading stays on its condition 7 line: the
    `proof-release:` line is the dispatched card's alone."""
    board = Board(hand=[lane_card("DRE-5930")])
    h = Harness(monkeypatch, board, release=reading)
    h.sweep()
    assert not any(l.startswith("proof-release:") for l in _lines(capsys))


def test_condition_7_reads_the_siblings_merges_and_skips_other_repos(monkeypatch, capsys):
    siblings = [("DRE-5921", "Done", SLUG), ("DRE-5922", "Done", "agent-bureau"),
                ("DRE-5923", "Canceled", SLUG), ("DRE-5930", "Hand-work", SLUG)]
    board = Board(hand=[lane_card("DRE-5930")],
                  details={"DRE-5930": detail(siblings=siblings)})
    h = Harness(monkeypatch, board, prs={"DRE-5921": {
        "number": 801, "state": "MERGED", "headRefName": "agent/DRE-5921-x",
        "mergeCommit": {"oid": "a" * 40}}, "DRE-5923": None})
    h.sweep()
    lines = _about(_lines(capsys), "DRE-5922")
    assert [f[0] for f in h.fired] == ["DRE-5930"]
    assert h.release_calls == [(REPO, [proof_release.Merge("DRE-5921", 801, "a" * 40, [])])]
    assert any("unchecked" in line and "agent-bureau" in line for line in lines)


def test_condition_7_an_unreadable_pull_request_is_never_ready(monkeypatch, capsys):
    board = Board(hand=[lane_card("DRE-5930")])
    h = Harness(monkeypatch, board, prs={"DRE-5921": RuntimeError("HTTP 502"),
                                         "DRE-5922": None})
    h.sweep()
    assert h.fired == []
    assert any("condition 7" in line and "HTTP 502" in line
               for line in _about(_lines(capsys), "DRE-5930"))


def test_the_conditions_are_read_in_order(monkeypatch, capsys):
    """A card failing 4, 5, 6 and 7 at once names 4: the first that fails."""
    board = Board(hand=[lane_card("DRE-5930", labels=("needs-human",))])
    h = Harness(monkeypatch, board, release="waiting",
                states={"DRE-5930": state("dead", dispatches=2)})
    h.sweep()
    lines = [l for l in _about(_lines(capsys), "DRE-5930") if "condition" in l]
    assert len(lines) == 1 and "condition 4" in lines[0]
    assert h.holds == []


# --------------------------------------------------------------------------- #
# one dispatch per pass, oldest first                                          #
# --------------------------------------------------------------------------- #


def test_at_most_one_dispatch_per_pass_oldest_first(monkeypatch, capsys):
    board = Board(hand=[lane_card("DRE-5941", entered=60),
                        lane_card("DRE-5942", entered=3000),
                        lane_card("DRE-5943", entered=900)])
    h = Harness(monkeypatch, board)
    tally = h.sweep()
    assert [f[0] for f in h.fired] == ["DRE-5942"]
    assert tally.dispatched == 1
    lines = _lines(capsys)
    assert any("deferred" in l for l in _about(lines, "DRE-5941"))
    assert any("deferred" in l for l in _about(lines, "DRE-5943"))


def test_the_candidate_cap_defers_the_rest(monkeypatch, capsys):
    cards = [lane_card(f"DRE-59{50 + i}", entered=1000 - i) for i in range(5)]
    board = Board(hand=cards)
    h = Harness(monkeypatch, board,
                states={c["identifier"]: state("running") for c in cards})
    tally = h.sweep()
    lines = _lines(capsys)
    read = [ident for kind, ident in board.reads if kind == "thread"]
    assert read == ["DRE-5950", "DRE-5951", "DRE-5952"]
    assert proof_dispatch.PROOF_CANDIDATES_PER_PASS == 3
    for ident in ("DRE-5953", "DRE-5954"):
        assert any("deferred — candidate cap, read on a later pass" in l
                   for l in _about(lines, ident))
    assert tally.deferred == 2 and tally.running == 3
    summary = [l for l in lines if l.startswith("proof-dispatch: eligible")]
    assert summary and "deferred 2" in summary[0] and "running 3" in summary[0]


# --------------------------------------------------------------------------- #
# a proof that cannot run yet does not keep the front (DRE-6464)              #
# --------------------------------------------------------------------------- #

#: One pass every quarter hour — the stubs' `*/15` cron.
PASS = timedelta(minutes=15)


def _shown_held(ident: str, entered: float) -> dict:
    """A first-run candidate whose lane window carries an undischarged hold."""
    return lane_card(ident, window=[
        promoted(entered),
        hold(f"{ident} waits for Sunday's edition", "the edition to land",
             entered - 10)])


def _three_blocked(eligible: str = "DRE-6042") -> Board:
    """The 2026-10-09 front of the queue: relations not read to the end, an
    open blocker, and a hold the lane window does not show — each costing a
    read — entered before an eligible fourth."""
    unseen = [promoted(2000), hold("held beyond the window", "the operator",
                                   1990)]
    return Board(
        hand=[lane_card("DRE-5407", entered=3000),
              lane_card("DRE-5409", entered=2500),
              lane_card("DRE-5830", entered=2000),
              lane_card(eligible, entered=100)],
        details={"DRE-5407": detail(partial=True),
                 "DRE-5409": detail(blockers={"DRE-6125": "In Progress"})},
        threads={"DRE-5830": unseen})


def _threads(cards) -> dict:
    """Each card's thread, the same comments its lane window shows."""
    return {c["identifier"]: linear_ops.window_nodes(c["comments"]) for c in cards}


def _three_held(eligible: str = "DRE-6042") -> Board:
    held = [_shown_held("DRE-5407", 3000), _shown_held("DRE-5409", 2500),
            _shown_held("DRE-5830", 2000)]
    return Board(hand=[*held, lane_card(eligible, entered=100)],
                 threads=_threads(held))


def _candidates_read(board: Board, since: int = 0) -> set:
    return {ident for kind, ident in board.reads[since:] if kind != "lane"}


@pytest.mark.parametrize("front", [_three_blocked, _three_held],
                         ids=["blocked", "held"])
@pytest.mark.parametrize("turn", range(4))
def test_three_that_cannot_run_ahead_of_an_eligible_one_dispatch_it_within_two_passes(
        monkeypatch, capsys, front, turn):
    """Whatever turn the first pass lands on, the fourth card — the one that
    can run — is dispatched by the second pass, and no pass reads more than
    `PROOF_CANDIDATES_PER_PASS` candidates."""
    board = front()
    h = Harness(monkeypatch, board)
    for n in range(2):
        before = len(board.reads)
        h.sweep(now=NOW + (turn + n) * PASS)
        assert len(_candidates_read(board, before)) <= proof_dispatch.PROOF_CANDIDATES_PER_PASS
        if h.fired:
            break
    assert [f[0] for f in h.fired] == ["DRE-6042"], _lines(capsys)


def _behind(blocked: int, eligible: str = "DRE-6042") -> Board:
    """`blocked` first runs with an open blocker, each costing a read, all
    entered before an eligible card at the back of the ring."""
    cards = [lane_card(f"DRE-57{10 + i}", entered=3000 - i) for i in range(blocked)]
    return Board(hand=[*cards, lane_card(eligible, entered=100)],
                 details={c["identifier"]: detail(blockers={"DRE-6125": "In Progress"})
                          for c in cards})


@pytest.mark.parametrize("ring", [6, 7, 12])
@pytest.mark.parametrize("every", [1, 2, 3, 4])
@pytest.mark.parametrize("turn", range(4))
def test_the_ring_comes_round_on_any_steady_cadence(
        monkeypatch, capsys, ring, every, turn):
    """Passes every turn, every second turn — a `*/15` cron that only lands
    at :07 and :37 — every third or every fourth: the eligible card at the
    back of a ring of 6, 7 or 12 is dispatched within twelve passes. A fixed
    stride of three read the same windows forever on a ring of 6 or 12 when
    the passes fell every second turn."""
    board = _behind(ring - 1)
    h = Harness(monkeypatch, board)
    for n in range(12):
        before = len(board.reads)
        h.sweep(now=NOW + (turn + n * every) * PASS)
        assert len(_candidates_read(board, before)) <= proof_dispatch.PROOF_CANDIDATES_PER_PASS
        if h.fired:
            break
    assert [f[0] for f in h.fired] == ["DRE-6042"], _lines(capsys)


def test_a_held_candidate_is_named_on_its_line_every_pass_at_no_read(monkeypatch, capsys):
    """Four held cards and two that cannot be dispatched: every pass names
    every hold — past the cap and after the one dispatch alike — and none of
    them is read."""
    held = [_shown_held(f"DRE-58{30 + i}", 4000 - 100 * i) for i in range(4)]
    board = Board(hand=[*held, lane_card("DRE-5941", entered=50),
                        lane_card("DRE-5942", entered=40)],
                  threads=_threads(held))
    h = Harness(monkeypatch, board,
                states={"DRE-5941": state("running"), "DRE-5942": state("running")})
    for n in range(4):
        before = len(board.reads)
        tally = h.sweep(now=NOW + n * PASS)
        lines = _lines(capsys)
        for card in held:
            ident = card["identifier"]
            named = [l for l in _about(lines, ident)
                     if "condition 4 (hold): held by 🔬 proof-waiting:" in l
                     and f"{ident} waits for Sunday's edition" in l]
            assert named, (n, ident, lines)
            assert not any("deferred" in l for l in _about(lines, ident))
        assert tally.held == 4
        assert not _candidates_read(board, before) & {c["identifier"] for c in held}


def test_a_hold_the_window_shows_discharged_is_left_to_the_thread_read(monkeypatch, capsys):
    """A later `🔬 proof-observed` in the window may discharge the hold: the
    lane read names nothing, the thread decides, and the card is dispatched."""
    nodes = [promoted(600),
             hold("being observed by hand", "the operator's record pull request", 300),
             comment(linear_ops.proof_observed_line("seen live at 09:00 PT"), 60)]
    board = Board(hand=[lane_card("DRE-5930", window=nodes)],
                  threads={"DRE-5930": nodes})
    h = Harness(monkeypatch, board)
    h.sweep()
    assert [f[0] for f in h.fired] == ["DRE-5930"]


def test_a_ceo_press_hold_the_window_shows_answered_is_left_to_the_thread_read(
        monkeypatch, capsys):
    nodes = [promoted(600), ceo_press_hold(300), answer(60)]
    board = Board(hand=[lane_card("DRE-5930", window=nodes)],
                  threads={"DRE-5930": nodes})
    h = Harness(monkeypatch, board)
    h.sweep()
    assert [f[0] for f in h.fired] == ["DRE-5930"]


@pytest.mark.parametrize("turn", range(6))
def test_the_read_bound_is_unchanged_whatever_the_turn(monkeypatch, capsys, turn):
    """The real readers over a counting transport, a board with held and
    blocked candidates and more: still at most 2 + 2 × 3 requests a pass."""
    import reconcile

    monkeypatch.delenv("BUREAU_READ", raising=False)
    cards = ([lane_card(f"DRE-59{60 + i}", entered=1000 - i) for i in range(5)]
             + [_shown_held(f"DRE-58{30 + i}", 2000 - i) for i in range(3)])
    calls: list = []

    def gql(query, variables=None):
        calls.append(query)
        variables = variables or {}
        if "issues(" in query:
            states = set(variables.get("states") or ())
            nodes = [c for c in cards if c["state"]["name"] in states]
            return {"issues": {"nodes": nodes,
                               "pageInfo": {"hasNextPage": False, "endCursor": None}}}
        if "viewer { id }" in query and "comments" in query:
            return {"viewer": {"id": VIEWER}, "issue": {"comments": {
                "nodes": [promoted(600)],
                "pageInfo": {"hasNextPage": False, "endCursor": None}}}}
        if "inverseRelations" in query:
            return {"issue": detail(blockers={"DRE-6125": "In Progress"})}
        raise AssertionError(f"unexpected query {query[:80]}")

    monkeypatch.setattr(linear_ops, "gql", gql)
    reconcile.reset_sweep_cards()
    h = Harness(monkeypatch, Board())
    tally = proof_dispatch.sweep(
        REPO, SLUG, live=True, read=lambda path: None, find_pr=h.find_pr,
        run_state=h.run_state, release=h.release, fire=h.fire,
        voices=fake_voices, now=NOW + turn * PASS)
    assert len(calls) <= 2 + 2 * proof_dispatch.PROOF_CANDIDATES_PER_PASS, calls
    assert tally.refused == proof_dispatch.PROOF_CANDIDATES_PER_PASS
    assert tally.held == 3 and tally.deferred == 2


# --------------------------------------------------------------------------- #
# the return after the CEO's answer                                            #
# --------------------------------------------------------------------------- #


def _parked(*, answers=(60,), receipts=(), extra=()):
    nodes = [receipt(900), comment("⏳ 5/5 PR opened", 800), ceo_press_hold(400)]
    nodes += [answer(m) for m in answers]
    nodes += [receipt(m, reason="re-run after the CEO's answer at 09:00 PT",
                      count="after the CEO's answer") for m in receipts]
    nodes += list(extra)
    return sorted(nodes, key=lambda c: c["createdAt"])


def _green(ident, nodes):
    return lane_card(ident, state="Green Light", window=nodes)


def test_the_return_dispatches_on_his_signed_answer_with_an_open_record(monkeypatch, capsys):
    nodes = _parked()
    board = Board(green=[_green("DRE-5930", nodes)], threads={"DRE-5930": nodes})
    record = {"number": 900, "state": "open", "url": "u",
              "head": "agent/DRE-5930-proof-record"}
    h = Harness(monkeypatch, board, release="waiting", states={
        "DRE-5930": state("finished", dispatches=1, record=record)})
    h.sweep()
    assert len(h.fired) == 1
    ident, repo, reason, event = h.fired[0]
    assert ident == "DRE-5930" and event == "proof-execute"
    assert reason.startswith("re-run after the CEO's answer at ")
    assert reason.endswith(" PT")
    first = h.posted[0][1].split("\n", 1)[0]
    assert first.endswith(f" — {reason} (after the CEO's answer)")
    # the release and the first-run budget are not this branch's
    assert h.release_calls == []


@pytest.mark.parametrize("name", ["running", "unknown"])
def test_a_run_in_flight_holds_the_return(monkeypatch, capsys, name):
    nodes = _parked()
    board = Board(green=[_green("DRE-5930", nodes)], threads={"DRE-5930": nodes})
    h = Harness(monkeypatch, board, states={"DRE-5930": state(name)})
    h.sweep()
    assert h.fired == []
    assert any(name in line for line in _about(_lines(capsys), "DRE-5930"))


def test_a_receipt_newer_than_his_answer_stops_a_second_return(monkeypatch, capsys):
    nodes = _parked(answers=(300,), receipts=(200,))
    board = Board(green=[_green("DRE-5930", nodes)], threads={"DRE-5930": nodes})
    h = Harness(monkeypatch, board, states={"DRE-5930": state("finished")})
    h.sweep()
    assert h.fired == []


def test_a_second_signed_answer_admits_one_more_return(monkeypatch, capsys):
    nodes = _parked(answers=(300, 100), receipts=(200,))
    board = Board(green=[_green("DRE-5930", nodes)], threads={"DRE-5930": nodes})
    h = Harness(monkeypatch, board, states={"DRE-5930": state("finished")})
    h.sweep()
    assert [f[0] for f in h.fired] == ["DRE-5930"]


def test_an_unsigned_claim_never_returns_a_card(monkeypatch, capsys):
    nodes = _parked(answers=(), extra=[comment(
        "Answer from Sid, 2026-10-06 09:30 PT:\nGo ahead.", 60, PERSON)])
    board = Board(green=[_green("DRE-5930", nodes)], threads={"DRE-5930": nodes})
    h = Harness(monkeypatch, board, states={"DRE-5930": state("finished")})
    h.sweep()
    assert h.fired == []


def test_a_hold_that_does_not_name_the_ceos_press_is_no_return(monkeypatch, capsys):
    nodes = sorted([receipt(900), hold("a thing", "the operator's look", 400),
                    answer(60)], key=lambda c: c["createdAt"])
    board = Board(green=[_green("DRE-5930", nodes)], threads={"DRE-5930": nodes})
    h = Harness(monkeypatch, board, states={"DRE-5930": state("finished")})
    h.sweep()
    assert h.fired == []


def test_the_return_reads_the_epic_and_the_blockers(monkeypatch, capsys):
    nodes = _parked()
    board = Board(green=[_green("DRE-5930", nodes)], threads={"DRE-5930": nodes},
                  details={"DRE-5930": detail(blockers={"DRE-5921": "In Review"})})
    h = Harness(monkeypatch, board, states={"DRE-5930": state("finished")})
    h.sweep()
    assert h.fired == []
    assert any("condition 3" in line for line in _about(_lines(capsys), "DRE-5930"))


def test_the_return_is_served_before_a_first_run(monkeypatch, capsys):
    nodes = _parked()
    board = Board(hand=[lane_card("DRE-5931", entered=5000)],
                  green=[_green("DRE-5930", nodes)], threads={"DRE-5930": nodes})
    h = Harness(monkeypatch, board, states={"DRE-5930": state("finished")})
    h.sweep()
    assert [f[0] for f in h.fired] == ["DRE-5930"]
    assert any("deferred" in line for line in _about(_lines(capsys), "DRE-5931"))


def test_a_green_light_proof_with_no_answer_costs_no_read(monkeypatch, capsys):
    nodes = sorted([receipt(900), ceo_press_hold(400)], key=lambda c: c["createdAt"])
    board = Board(green=[_green("DRE-5930", nodes),
                         lane_card("DRE-5999", title="[EPIC] a plan", state="Green Light")])
    h = Harness(monkeypatch, board)
    h.sweep()
    assert [r for r in board.reads if r[0] != "lane"] == []


# --------------------------------------------------------------------------- #
# the dry run                                                                  #
# --------------------------------------------------------------------------- #


def test_the_dry_run_prints_would_lines_and_writes_nothing(monkeypatch, capsys):
    board = Board(hand=[lane_card("DRE-5930"), lane_card("DRE-5931", entered=10)])
    h = Harness(monkeypatch, board, states={"DRE-5931": state("dead", dispatches=2)})
    h.sweep(live=False)
    lines = _lines(capsys)
    assert h.fired == [] and h.posted == [] and h.holds == []
    assert "would: dispatch DRE-5930 — first proof run" in lines


@pytest.mark.parametrize("value", [None, "", "false", "TRUE", "yes", "1"])
def test_only_the_exact_word_true_is_live(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("PROOF_DISPATCH_LIVE", raising=False)
    else:
        monkeypatch.setenv("PROOF_DISPATCH_LIVE", value)
    assert proof_dispatch.is_live() is False
    monkeypatch.setenv("PROOF_DISPATCH_LIVE", "true")
    assert proof_dispatch.is_live() is True


def test_main_runs_the_dry_run_when_the_variable_is_unset(monkeypatch, capsys):
    monkeypatch.delenv("PROOF_DISPATCH_LIVE", raising=False)
    monkeypatch.setenv("REPO", REPO)
    monkeypatch.setenv("REPO_SLUG", SLUG)
    seen = {}

    def fake_sweep(repo, slug, *, live, **_):
        seen.update(repo=repo, slug=slug, live=live)
        return proof_dispatch.Tally()

    monkeypatch.setattr(proof_dispatch, "sweep", fake_sweep)
    assert proof_dispatch.main([]) == 0
    assert seen == {"repo": REPO, "slug": SLUG, "live": False}
    assert any(l.startswith("linear-budget:") for l in _lines(capsys))


# The dry run names why it is dry (DRE-6439): the switch's own line from
# `switch_reason.off_line`, composed from the environment alone.

SWITCH_LINE = "proof-dispatch: PROOF_DISPATCH_LIVE"


def _main_over(monkeypatch, *, switch=None, until=None):
    """`main()` over the harness's board, the switch and its companion as given."""
    for name, value in (("PROOF_DISPATCH_LIVE", switch),
                        ("PROOF_DISPATCH_LIVE_OFF_UNTIL", until)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    monkeypatch.setenv("REPO", REPO)
    monkeypatch.setenv("REPO_SLUG", SLUG)
    board = Board(hand=[lane_card("DRE-5930")])
    h = Harness(monkeypatch, board)
    real = proof_dispatch.sweep

    def over_the_board(repo, slug, *, live):
        return real(repo, slug, live=live, linear=board, read=lambda path: None,
                    find_pr=h.find_pr, run_state=h.run_state, release=h.release,
                    fire=h.fire, voices=fake_voices, now=NOW,
                    find_record=h.find_record)

    monkeypatch.setattr(proof_dispatch, "sweep", over_the_board)
    assert proof_dispatch.main([]) == 0
    return h, board


def test_the_dry_run_names_the_switch_and_the_cards_it_waits_on(monkeypatch, capsys):
    # The same pass run straight, for its read log: `main()` adds no read.
    straight = Board(hand=[lane_card("DRE-5930")])
    Harness(monkeypatch, straight).sweep(live=False)
    capsys.readouterr()
    h, board = _main_over(monkeypatch, until="DRE-6141, DRE-6142, DRE-6143")
    lines = _lines(capsys)
    line = ("proof-dispatch: PROOF_DISPATCH_LIVE is off — until DRE-6141, "
            "DRE-6142, DRE-6143 land")
    assert lines.count(line) == 1
    assert [l for l in lines if l.startswith(SWITCH_LINE)] == [line]
    would = [i for i, l in enumerate(lines) if l.startswith("would:")]
    assert would and lines.index(line) < would[0]
    tally = next(l for l in lines if l.startswith("proof-dispatch: eligible"))
    assert tally.endswith("(dry run)")
    assert h.fired == [] and h.posted == [] and h.holds == []
    assert board.reads == straight.reads


def test_the_dry_run_with_no_companion_says_no_reason_given(monkeypatch, capsys):
    _main_over(monkeypatch)
    lines = _lines(capsys)
    assert [l for l in lines if l.startswith(SWITCH_LINE)] == [
        "proof-dispatch: PROOF_DISPATCH_LIVE is off — no reason given"]


def test_a_live_pass_prints_no_switch_line(monkeypatch, capsys):
    h, _ = _main_over(monkeypatch, switch="true", until="DRE-6141")
    lines = _lines(capsys)
    assert [l for l in lines if l.startswith(SWITCH_LINE)] == []
    assert h.fired and next(
        l for l in lines if l.startswith("proof-dispatch: eligible")).endswith("(live)")


# --------------------------------------------------------------------------- #
# the read bound                                                               #
# --------------------------------------------------------------------------- #


def test_five_candidates_cost_at_most_eight_linear_reads(monkeypatch, capsys):
    """The real readers — `reconcile.active_cards`, the card read, the thread
    read — over a faked transport that counts every request. `In Review`
    (DRE-5931) costs nothing more: the sweep's one board read serves it with
    `Hand-work`, and `Green Light` is the other read."""
    import reconcile

    monkeypatch.delenv("BUREAU_READ", raising=False)
    cards = [lane_card(f"DRE-59{60 + i}", entered=1000 - i) for i in range(5)]
    green = [lane_card("DRE-5999", title="[EPIC] a plan", state="Green Light")]
    calls: list = []

    def gql(query, variables=None):
        calls.append(query)
        variables = variables or {}
        if "issues(" in query:
            states = set(variables.get("states") or ())
            nodes = [c for c in cards + green if c["state"]["name"] in states]
            return {"issues": {"nodes": nodes,
                               "pageInfo": {"hasNextPage": False, "endCursor": None}}}
        if "viewer { id }" in query and "comments" in query:
            return {"viewer": {"id": VIEWER}, "issue": {"comments": {
                "nodes": [promoted(600)],
                "pageInfo": {"hasNextPage": False, "endCursor": None}}}}
        if "inverseRelations" in query:
            return {"issue": detail()}
        raise AssertionError(f"unexpected query {query[:80]}")

    monkeypatch.setattr(linear_ops, "gql", gql)
    reconcile.reset_sweep_cards()
    h = Harness(monkeypatch, Board(),
                states={c["identifier"]: state("running") for c in cards})
    tally = proof_dispatch.sweep(
        REPO, SLUG, live=True, read=lambda path: None, find_pr=h.find_pr,
        run_state=h.run_state, release=h.release, fire=h.fire,
        voices=fake_voices, now=NOW)
    lines = _lines(capsys)
    assert len(calls) <= 8, calls
    assert len(calls) == 2 + 2 * proof_dispatch.PROOF_CANDIDATES_PER_PASS
    deferred = [l for l in lines if "deferred — candidate cap, read on a later pass" in l]
    assert len(deferred) == 2 and tally.deferred == 2


# --------------------------------------------------------------------------- #
# the contract the siblings read                                              #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("ref,expected", [
    ("agent/DRE-5930-proof-record", True),
    ("agent/DRE-1-proof-record", True),
    ("agent/DRE-5930-proof-record-2", False),
    ("agent/DRE-5930-build", False),
    ("feature/agent/DRE-5930-proof-record", False),
    ("", False),
    (None, False),
])
def test_proof_record_branch(ref, expected):
    assert proof_dispatch.proof_record_branch(ref) is expected


def test_the_act_row_ships_with_the_module():
    row = pipeline_act.record("proof-run-dispatched")
    assert row["tag"] == "proof-run"
    assert (row["kind"], row["state"]) == ("recovery", "dispatched")
    assert row["next_actor"] == "proof-task.yml"
    assert row["subscriber"] == "reconcile.yml"
    assert row["cadence_s"] == 7200
    assert row["emits"]["file"] == "scripts/proof_dispatch.py"
    source = (ROOT / "scripts" / "proof_dispatch.py").read_text(encoding="utf-8")
    assert source.count(row["emits"]["anchor"]) == 1
    assert pipeline_act.problems() == []


def test_the_doc_carries_the_row():
    text = (ROOT / "docs" / "pipeline-acts.md").read_text(encoding="utf-8")
    for expected in ("proof-run-dispatched", "`proof-run`", "PROOF_DISPATCH_LIVE",
                     "scripts/proof_dispatch.py"):
        assert expected in text


# --------------------------------------------------------------------------- #
# the step in reconcile.yml                                                    #
# --------------------------------------------------------------------------- #


def _steps() -> list:
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return doc["jobs"]["sweep"]["steps"]


def _step() -> dict:
    found = [s for s in _steps() if s.get("name") == STEP]
    assert len(found) == 1, f"{STEP!r} is not one step of reconcile.yml"
    return found[0]


def test_the_step_follows_the_sweep_and_runs_only_on_a_full_pass():
    names = [s.get("name") for s in _steps()]
    assert names.index(STEP) == names.index("Sweep") + 1
    assert _step()["if"] == "inputs.sweep_reason == ''"


def test_the_step_carries_the_sweeps_env_and_the_switch():
    sweep = next(s for s in _steps() if s.get("name") == "Sweep")
    env = _step()["env"]
    for name in ("LINEAR_API_KEY", "GH_TOKEN", "GH_READ_TOKEN", "REPO",
                 "BUREAU_READ", "BUREAU_READ_URL", "BUREAU_READ_AUDIENCE",
                 "BUREAU_PIPELINE_REF"):
        assert env[name] == sweep["env"][name], name
    assert env["PROOF_DISPATCH_LIVE"] == "${{ vars.PROOF_DISPATCH_LIVE }}"
    assert env["PROOF_DISPATCH_LIVE_OFF_UNTIL"] == "${{ vars.PROOF_DISPATCH_LIVE_OFF_UNTIL }}"
    assert "REPO_SLUG" in _step()["run"]


def test_the_summary_grep_lifts_the_switch_the_tally_and_the_would_lines(tmp_path):
    grep = next(line.strip() for line in _step()["run"].splitlines()
                if line.strip().startswith("SUMMARY="))
    log = [
        "proof-dispatch: PROOF_DISPATCH_LIVE is off — until DRE-6141 land",
        "proof-dispatch: DRE-5930 — eligible: first proof run (1 of 2)",
        "would: dispatch DRE-5930 — first proof run",
        "proof-dispatch: eligible 1, dispatched 0, deferred 0 (dry run)",
        "linear-budget: 4 request(s)",
    ]
    (tmp_path / "proof-dispatch.log").write_text("\n".join(log) + "\n",
                                                 encoding="utf-8")
    done = subprocess.run(["bash", "-e", "-c", f"{grep}\nprintf '%s\\n' \"$SUMMARY\""],
                          cwd=tmp_path, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert done.stdout.splitlines() == [log[0], log[2], log[3]]


def test_the_summary_grep_lifts_the_release_reading_of_the_dispatched_card(tmp_path):
    """DRE-6516: the `proof-release:` lines `_released` prints on `ready` reach
    the step summary, in log order beside the would line they explain."""
    grep = next(line.strip() for line in _step()["run"].splitlines()
                if line.strip().startswith("SUMMARY="))
    log = [
        "proof-dispatch: DRE-5921 — unchecked — its repo: label names portico",
        "proof-release: DRE-5930 — ready — whole repository: stable carries "
        "#5921 (abcdef0) for DRE-5921",
        "proof-dispatch: DRE-5930 — eligible: first proof run (dispatch 1 of 2)",
        "would: dispatch DRE-5930 — first proof run",
        "proof-dispatch: eligible 1, dispatched 1, deferred 0 (dry run)",
        "linear-budget: 4 request(s)",
    ]
    (tmp_path / "proof-dispatch.log").write_text("\n".join(log) + "\n",
                                                 encoding="utf-8")
    done = subprocess.run(["bash", "-e", "-c", f"{grep}\nprintf '%s\\n' \"$SUMMARY\""],
                          cwd=tmp_path, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert done.stdout.splitlines() == [log[1], log[3], log[4]]


def test_the_stub_is_tested_before_any_python():
    run = _step()["run"]
    python = run.index("python3")
    for stub in proof_dispatch.STUBS:
        assert stub in run[:python]
    assert proof_dispatch.NO_STUB in run[:python]


def _run_step(tmp_path, stubs=()):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    marker = tmp_path / "python-ran"
    fake = bin_dir / "python3"
    fake.write_text(f"#!/bin/sh\ntouch {marker}\necho 'linear-budget: fixture'\n"
                    "echo 'proof-dispatch: eligible 0, dispatched 0'\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    work = tmp_path / "work"
    (work / ".github" / "workflows").mkdir(parents=True)
    for stub in stubs:
        (work / stub).write_text("name: stub\n")
    summary = tmp_path / "summary.md"
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}",
           "GITHUB_REPOSITORY": "dreadnought-foundry/portico",
           "GITHUB_STEP_SUMMARY": str(summary)}
    done = subprocess.run(["bash", "-e", "-c", _step()["run"]], cwd=work, env=env,
                          capture_output=True, text=True)
    return done, marker.exists(), summary


def test_no_stub_prints_the_line_and_runs_no_python(tmp_path):
    done, ran, _ = _run_step(tmp_path)
    assert done.returncode == 0, done.stderr
    assert not ran
    assert done.stdout.strip() == proof_dispatch.NO_STUB
    assert proof_dispatch.NO_STUB.startswith(
        "proof-dispatch: no proof-run stub in this repository")


@pytest.mark.parametrize("stub", proof_dispatch.STUBS)
def test_a_stub_runs_the_phase_and_lifts_its_spend(tmp_path, stub):
    done, ran, summary = _run_step(tmp_path, stubs=(stub,))
    assert done.returncode == 0, done.stderr
    assert ran
    text = summary.read_text(encoding="utf-8")
    assert "linear-budget: fixture" in text
    assert "proof-dispatch: eligible 0" in text


def test_the_stub_names_are_the_fix_workflow_family():
    assert proof_dispatch.STUBS == (".github/workflows/self-proof-task.yml",
                                    ".github/workflows/proof-task.yml")


# --------------------------------------------------------------------------- #
# an answer the console's key could not check                                 #
# --------------------------------------------------------------------------- #


def _unchecked_voices(nodes, viewer, *, card):
    """`fake_voices`, with the CEO's comments read as UNCHECKED: the key was
    unreadable, so the signature check never ran (DRE-4153)."""
    return [dataclasses.replace(v, kind=spoken_thread.UNCHECKED, body=None)
            if v.kind == spoken_thread.CEO_VIA_CONSOLE else v
            for v in fake_voices(nodes, viewer, card=card)]


def test_an_unchecked_console_answer_neither_returns_nor_discharges_and_says_why(
        monkeypatch, capsys):
    nodes = _parked()
    board = Board(green=[_green("DRE-5930", nodes)], threads={"DRE-5930": nodes},
                  hand=[lane_card("DRE-5931")])
    board.threads["DRE-5931"] = [promoted(600), ceo_press_hold(300), answer(60)]
    h = Harness(monkeypatch, board, states={"DRE-5930": state("finished")})
    proof_dispatch.sweep(
        REPO, SLUG, live=True, linear=board, read=lambda path: None,
        find_pr=h.find_pr, run_state=h.run_state, release=h.release,
        fire=h.fire, voices=_unchecked_voices, now=NOW)
    lines = _lines(capsys)
    assert h.fired == [] and h.posted == []
    assert any("COULD NOT BE CHECKED" in l for l in _about(lines, "DRE-5930"))
    assert any("condition 4" in l and "COULD NOT BE CHECKED" in l
               for l in _about(lines, "DRE-5931"))


# --------------------------------------------------------------------------- #
# the re-run after the critic's findings (DRE-5931)                            #
# --------------------------------------------------------------------------- #

HEAD = "c0ffee1" + "0" * 33
SHA7 = HEAD[:7]
OLD_HEAD = "abc1234" + "f" * 33
QA_BOT = "agent-bureau-qa-bot"
RECORD_PATH = "docs/proof-dre-5930.md"
RERUN = f"re-run after the critic's findings at {SHA7}"
RERUN_HELD = ("the record was sent back twice after re-observation",
              "an operator reading the critic's findings and the two re-run "
              "receipts")


def verdict(token: str, minutes_ago: float, *, sha: str = HEAD,
            login: str = QA_BOT) -> dict:
    return {"author": {"login": login}, "createdAt": _at(minutes_ago),
            "body": f"🔎 QA Critic — VERDICT: {token} @{sha}\n\nThe findings."}


def record(*verdicts, ident="DRE-5930", opened: float = 2400, head: str = HEAD,
           branch: str | None = None, pr_state: str = "OPEN") -> dict:
    return {"number": 900, "url": f"https://github.com/{REPO}/pull/900",
            "headRefName": branch or f"agent/{ident}-proof-record",
            "state": pr_state, "headRefOid": head, "createdAt": _at(opened),
            "files": [{"path": RECORD_PATH, "changeType": "ADDED"}],
            "comments": [{"author": {"login": "agent-bureau-bot"},
                          "createdAt": _at(opened), "body": "opened"},
                         *verdicts]}


def rerun_receipt(minutes_ago: float, n: int) -> dict:
    return receipt(minutes_ago, reason=f"re-run after the critic's findings "
                                       f"at {OLD_HEAD[:7]}", count=f"re-run {n} of 2")


def _sent_back(*extra) -> list:
    """A first run that opened the record, then whatever `extra` adds."""
    nodes = [receipt(3000), comment("⏳ 5/5 PR opened", 2500), *extra]
    return sorted(nodes, key=lambda c: c["createdAt"])


def _review(ident="DRE-5930", **kw) -> dict:
    return lane_card(ident, state="In Review", **kw)


def _rerun_pass(monkeypatch, *, thread, pr, run="finished", live=True,
                extra_hand=()):
    board = Board(review=[_review()], hand=list(extra_hand),
                  threads={"DRE-5930": thread})
    h = Harness(monkeypatch, board, records={"DRE-5930": pr},
                states={"DRE-5930": state(run, dispatches=1,
                                          record={"number": 900, "state": "open"})})
    tally = h.sweep(live=live)
    return h, tally


def test_rerun_request_changes_at_head_with_no_newer_receipt_dispatches_once(
        monkeypatch, capsys):
    h, tally = _rerun_pass(monkeypatch, thread=_sent_back(),
                           pr=record(verdict("REQUEST_CHANGES", 100)))
    assert h.fired == [("DRE-5930", REPO, RERUN, "proof-execute")]
    assert tally.dispatched == 1 and h.holds == []
    ident, body = h.posted[0]
    first = body.split("\n", 1)[0]
    assert first.startswith("🔬 proof-run: dispatched a proof run at ")
    assert first.endswith(f" — {RERUN} (re-run 1 of 2)")
    parsed = proof_run_state.receipt(body)
    assert parsed.reason == RERUN and parsed.count == "re-run 1 of 2"
    assert pipeline_act.read_trailer(body)["act"] == "proof-run-dispatched"
    # The release and the epic are not this branch's to read.
    assert h.release_calls == []


def test_rerun_a_newer_receipt_whose_run_died_dispatches_the_second(monkeypatch, capsys):
    thread = _sent_back(rerun_receipt(50, 1))
    h, _ = _rerun_pass(monkeypatch, thread=thread, run="dead",
                       pr=record(verdict("REQUEST_CHANGES", 100)))
    assert h.fired == [("DRE-5930", REPO, RERUN, "proof-execute")]
    assert h.posted[0][1].split("\n", 1)[0].endswith(f" — {RERUN} (re-run 2 of 2)")


def test_rerun_dead_is_read_through_the_real_reader_with_the_record_open(
        monkeypatch, capsys):
    run = "35700000001"
    thread = _sent_back(
        rerun_receipt(50, 1),
        comment("🧠 model-attempt: claude-opus-5-5 — proof agent starting. Run: "
                f"https://github.com/{REPO}/actions/runs/{run}", 45))
    reads = {
        f"repos/{REPO}/pulls?head=dreadnought-foundry:agent/DRE-5930-"
        "proof-record&state=all&per_page=100": [
            {"number": 900, "state": "open", "html_url": "u",
             "head": {"ref": "agent/DRE-5930-proof-record"}}],
        f"repos/{REPO}/actions/runs/{run}": {
            "status": "completed", "conclusion": "failure",
            "updated_at": _at(30)},
    }
    board = Board(review=[_review()], threads={"DRE-5930": thread})
    h = Harness(monkeypatch, board,
                records={"DRE-5930": record(verdict("REQUEST_CHANGES", 100))})
    proof_dispatch.sweep(
        REPO, SLUG, live=True, linear=board, read=lambda path: reads[path],
        find_pr=h.find_pr, run_state=proof_run_state.reading,
        release=h.release, fire=h.fire, voices=fake_voices, now=NOW,
        find_record=h.find_record)
    assert [f[2] for f in h.fired] == [RERUN]
    assert h.posted[0][1].split("\n", 1)[0].endswith("(re-run 2 of 2)")


def test_rerun_a_newer_receipt_that_never_started_dispatches_the_second(
        monkeypatch, capsys):
    """A re-run dispatched but never begun is owed again, and spent one."""
    thread = _sent_back(rerun_receipt(50, 1))
    h, _ = _rerun_pass(monkeypatch, thread=thread, run="never-started",
                       pr=record(verdict("REQUEST_CHANGES", 100)))
    assert h.fired == [("DRE-5930", REPO, RERUN, "proof-execute")]
    assert h.holds == []
    assert h.posted[0][1].split("\n", 1)[0].endswith(f" — {RERUN} (re-run 2 of 2)")


def test_rerun_two_that_never_started_reach_the_hold(monkeypatch, capsys):
    thread = _sent_back(rerun_receipt(80, 1), rerun_receipt(50, 2))
    h, tally = _rerun_pass(monkeypatch, thread=thread, run="never-started",
                           pr=record(verdict("REQUEST_CHANGES", 100)))
    assert h.fired == [] and h.posted == []
    assert h.holds == [("DRE-5930", *RERUN_HELD)]
    assert tally.held == 1


def test_rerun_never_started_is_read_through_the_real_reader(monkeypatch, capsys):
    """Past the queued window with no `🧠 model-attempt`: the real reader says
    never-started, and the re-run is sent again."""
    thread = _sent_back(rerun_receipt(50, 1))
    reads = {
        f"repos/{REPO}/pulls?head=dreadnought-foundry:agent/DRE-5930-"
        "proof-record&state=all&per_page=100": [
            {"number": 900, "state": "open", "html_url": "u",
             "head": {"ref": "agent/DRE-5930-proof-record"}}],
    }
    board = Board(review=[_review()], threads={"DRE-5930": thread})
    h = Harness(monkeypatch, board,
                records={"DRE-5930": record(verdict("REQUEST_CHANGES", 100))})
    proof_dispatch.sweep(
        REPO, SLUG, live=True, linear=board, read=lambda path: reads[path],
        find_pr=h.find_pr, run_state=proof_run_state.reading,
        release=h.release, fire=h.fire, voices=fake_voices, now=NOW,
        find_record=h.find_record)
    assert [f[2] for f in h.fired] == [RERUN]
    assert h.posted[0][1].split("\n", 1)[0].endswith("(re-run 2 of 2)")


RERUN_UNANSWERED = ("the re-run finished and the critic's findings still stand "
                    "at the record's head",
                    "an operator reading the critic's findings and the re-run's "
                    "thread")


def test_rerun_finished_with_the_verdict_still_at_the_head_is_held_once(
        monkeypatch, capsys):
    """The re-run said 5/5 yet the critic's REQUEST_CHANGES is still at the
    head: nothing it amended reached the critic. Held, never sent again."""
    done = (rerun_receipt(50, 1), comment("⏳ 5/5 record amended", 20))
    h, tally = _rerun_pass(monkeypatch, thread=_sent_back(*done),
                           pr=record(verdict("REQUEST_CHANGES", 100)))
    lines = _about(_lines(capsys), "DRE-5930")
    assert h.fired == [] and h.posted == []
    assert h.holds == [("DRE-5930", *RERUN_UNANSWERED)]
    assert tally.held == 1
    assert any("reads finished" in line and SHA7 in line for line in lines), lines

    # The next pass reads the hold it posted and names it; nothing is posted.
    h, tally = _rerun_pass(monkeypatch, thread=_sent_back(*done,
                                                          hold(*RERUN_UNANSWERED, 10)),
                           pr=record(verdict("REQUEST_CHANGES", 100)))
    lines = _about(_lines(capsys), "DRE-5930")
    assert h.fired == [] and h.holds == [] and h.posted == []
    assert any("held by 🔬 proof-waiting: the re-run finished" in line
               for line in lines), lines


def test_rerun_finished_hold_in_the_dry_run_writes_nothing(monkeypatch, capsys):
    done = (rerun_receipt(50, 1), comment("⏳ 5/5 record amended", 20))
    h, _ = _rerun_pass(monkeypatch, thread=_sent_back(*done), live=False,
                       pr=record(verdict("REQUEST_CHANGES", 100)))
    assert h.fired == [] and h.posted == [] and h.holds == []
    assert f"would: hold DRE-5930 — {RERUN_UNANSWERED[0]}" in _lines(capsys)


def _nothing_cases():
    rc = verdict("REQUEST_CHANGES", 100)
    return {
        "approve-at-head": (_sent_back(), record(verdict("APPROVE", 100)),
                            "finished", "APPROVE"),
        "older-verdict-running": (_sent_back(rerun_receipt(50, 1)), record(rc),
                                  "running", "running"),
        "proof-waiting": (_sent_back(hold("being observed by hand",
                                          "the operator's record pull request", 60)),
                          record(rc), "finished",
                          "held by 🔬 proof-waiting: being observed by hand"),
        "unknown": (_sent_back(), record(rc), "unknown", "unknown"),
        "two-reruns-spent": (_sent_back(rerun_receipt(1500, 1),
                                        comment("⏳ 5/5 record amended", 1400),
                                        rerun_receipt(800, 2),
                                        comment("⏳ 5/5 record amended", 700)),
                             record(rc), "finished", "never a third"),
        "forged-verdict": (_sent_back(),
                           record(verdict("REQUEST_CHANGES", 100, login="mallory")),
                           "finished", "none"),
        "verdict-on-an-earlier-head": (_sent_back(),
                                       record(verdict("REQUEST_CHANGES", 100,
                                                      sha=OLD_HEAD)),
                                       "finished", f"earlier head {OLD_HEAD[:7]}"),
        "not-a-record-branch": (_sent_back(),
                                record(rc, branch="agent/DRE-5930-build"),
                                "finished", "proof-record"),
        "record-merged": (_sent_back(), record(rc, pr_state="MERGED"),
                          "finished", "no open record pull request"),
        "no-pull-request": (_sent_back(), None, "finished",
                            "no open record pull request"),
        "unreadable-pull-request": (_sent_back(), RuntimeError("HTTP 502"),
                                    "finished", "HTTP 502"),
    }


@pytest.mark.parametrize("case", list(_nothing_cases()))
def test_rerun_dispatches_nothing_and_says_why(monkeypatch, capsys, case):
    thread, pr, run, why = _nothing_cases()[case]
    h, _ = _rerun_pass(monkeypatch, thread=thread, pr=pr, run=run)
    lines = _about(_lines(capsys), "DRE-5930")
    assert h.fired == [] and h.posted == []
    assert any("re-run" in line and why in line for line in lines), lines


def test_rerun_budget_is_counted_from_rerun_receipts_only(monkeypatch, capsys):
    # Two first-run receipts and no re-run: the first re-run is still owed.
    thread = sorted([receipt(3000), receipt(2800, reason="second dispatch — run 1 "
                                                          "ended failure at 08:00 PT "
                                                          "with no record",
                                            count="dispatch 2 of 2"),
                     comment("⏳ 5/5 PR opened", 2500)],
                    key=lambda c: c["createdAt"])
    h, _ = _rerun_pass(monkeypatch, thread=thread,
                       pr=record(verdict("REQUEST_CHANGES", 100)))
    assert h.posted[0][1].split("\n", 1)[0].endswith(f" — {RERUN} (re-run 1 of 2)")

    # One first-run receipt and two re-runs: held, never `3 of 2`.
    capsys.readouterr()
    thread = _sent_back(rerun_receipt(1500, 1), comment("⏳ 5/5 amended", 1400),
                        rerun_receipt(800, 2), comment("⏳ 5/5 amended", 700))
    h, tally = _rerun_pass(monkeypatch, thread=thread,
                           pr=record(verdict("REQUEST_CHANGES", 100)))
    out = capsys.readouterr().out
    assert h.fired == [] and h.posted == []
    assert h.holds == [("DRE-5930", *RERUN_HELD)]
    assert "3 of 2" not in out and tally.held == 1


def test_rerun_budget_is_this_pull_requests(monkeypatch, capsys):
    """Re-runs spent before this record pull request opened are not its own."""
    thread = _sent_back(rerun_receipt(2300, 1), rerun_receipt(2200, 2),
                        comment("⏳ 5/5 a new record", 2100))
    h, _ = _rerun_pass(monkeypatch, thread=thread,
                       pr=record(verdict("REQUEST_CHANGES", 100), opened=2000))
    assert h.posted[0][1].split("\n", 1)[0].endswith(f" — {RERUN} (re-run 1 of 2)")


def test_rerun_the_hold_after_two_and_the_next_pass_skips_by_name(monkeypatch, capsys):
    spent = (rerun_receipt(1500, 1), comment("⏳ 5/5 amended", 1400),
             rerun_receipt(800, 2), comment("⏳ 5/5 amended", 700))
    h, _ = _rerun_pass(monkeypatch, thread=_sent_back(*spent),
                       pr=record(verdict("REQUEST_CHANGES", 100)))
    assert h.holds == [("DRE-5930", "the record was sent back twice after "
                                    "re-observation",
                        "an operator reading the critic's findings and the two "
                        "re-run receipts")]
    capsys.readouterr()

    # The next pass reads the hold it posted and names it; nothing is posted.
    held = _sent_back(*spent, hold(*RERUN_HELD, 50))
    h, tally = _rerun_pass(monkeypatch, thread=held,
                           pr=record(verdict("REQUEST_CHANGES", 100)))
    lines = _about(_lines(capsys), "DRE-5930")
    assert h.fired == [] and h.holds == [] and h.posted == []
    assert any("held by 🔬 proof-waiting: the record was sent back twice after "
               "re-observation" in line for line in lines), lines
    assert tally.held == 1


def test_rerun_shares_the_one_dispatch_cap_behind_a_first_run(monkeypatch, capsys):
    h, tally = _rerun_pass(monkeypatch, thread=_sent_back(),
                           pr=record(verdict("REQUEST_CHANGES", 100)),
                           extra_hand=[lane_card("DRE-5941")])
    assert [(f[0], f[2]) for f in h.fired] == [("DRE-5941", "first proof run")]
    assert tally.dispatched == 1
    assert any("deferred — one dispatch per pass" in line
               for line in _about(_lines(capsys), "DRE-5930"))
    # A deferred candidate costs nothing.
    assert h.record_reads == []


def test_rerun_dry_run_prints_would_and_writes_nothing(monkeypatch, capsys):
    h, _ = _rerun_pass(monkeypatch, thread=_sent_back(), live=False,
                       pr=record(verdict("REQUEST_CHANGES", 100)))
    lines = _lines(capsys)
    assert h.fired == [] and h.posted == [] and h.holds == []
    assert f"would: dispatch DRE-5930 — {RERUN}" in lines

    spent = _sent_back(rerun_receipt(1500, 1), comment("⏳ 5/5 amended", 1400),
                       rerun_receipt(800, 2), comment("⏳ 5/5 amended", 700))
    h, _ = _rerun_pass(monkeypatch, thread=spent, live=False,
                       pr=record(verdict("REQUEST_CHANGES", 100)))
    lines = _lines(capsys)
    assert h.fired == [] and h.posted == [] and h.holds == []
    assert f"would: hold DRE-5930 — {RERUN_HELD[0]}" in lines


def test_an_in_review_card_that_is_not_ours_costs_nothing(monkeypatch, capsys):
    board = Board(review=[_review("DRE-5932", repo="portico"),
                          _review("DRE-5933", title="bureau-pipeline: a build")])
    h = Harness(monkeypatch, board)
    h.sweep()
    assert h.fired == [] and h.record_reads == []
    assert [r for r in board.reads if r[0] != "lane"] == []


@pytest.mark.parametrize("turn", range(4))
def test_rerun_is_not_starved_behind_three_first_runs_that_cannot_run(
        monkeypatch, capsys, turn):
    """Three blocked first runs no longer keep the re-run behind them out of
    every pass: the critic's findings are answered within two passes."""
    board = _three_blocked()
    board.lanes["Hand-work"] = board.lanes["Hand-work"][:3]
    board.lanes["In Review"] = [_review()]
    board.threads["DRE-5930"] = _sent_back()
    h = Harness(monkeypatch, board,
                records={"DRE-5930": record(verdict("REQUEST_CHANGES", 100))},
                states={"DRE-5930": state("finished", dispatches=1,
                                          record={"number": 900, "state": "open"})})
    for n in range(2):
        h.sweep(now=NOW + (turn + n) * PASS)
        if h.fired:
            break
    assert h.fired == [("DRE-5930", REPO, RERUN, "proof-execute")], _lines(capsys)


# --------------------------------------------------------------------------- #
# the re-run reads a record the sweep parked on its review cap (DRE-6406)      #
# --------------------------------------------------------------------------- #


def review_cap_stamp(minutes_ago: float, head: str = HEAD) -> dict:
    return comment(f"🔒 hold: reason=review-cap-spent at={head} lifts=new-head "
                   "by=reconcile.py", minutes_ago)


def _capped(*extra, head: str = HEAD) -> list:
    """A record sent back, then the sweep's review-cap park on `head`."""
    return _sent_back(review_cap_stamp(300, head),
                      comment(f"🚨 review-nudge-cap PR #900 @{head}: a question", 299),
                      *extra)


def _capped_card(nodes, ident="DRE-5930", labels=("needs-human",)) -> dict:
    return lane_card(ident, state="Green Light", labels=labels, window=nodes)


class _LaneWrites:
    """Every lane, label and lift write the pass could make — it makes none."""

    def __init__(self, monkeypatch):
        self.log: list = []
        for name in ("cmd_advance", "cmd_state", "add_label", "remove_label"):
            monkeypatch.setattr(linear_ops, name,
                                lambda *a, _n=name, **k: self.log.append((_n, a)))


def _capped_pass(monkeypatch, *, thread, pr, run="finished", live=True,
                 labels=("needs-human",), window=None, green=()):
    board = Board(green=[_capped_card(thread if window is None else window,
                                      labels=labels), *green],
                  threads={"DRE-5930": thread})
    writes = _LaneWrites(monkeypatch)
    h = Harness(monkeypatch, board, records={"DRE-5930": pr},
                states={"DRE-5930": state(run, dispatches=1,
                                          record={"number": 900, "state": "open"})})
    tally = h.sweep(live=live)
    return h, tally, board, writes


def test_a_review_cap_park_on_a_sent_back_record_is_re_run_in_place(monkeypatch, capsys):
    h, tally, _, writes = _capped_pass(
        monkeypatch, thread=_capped(), pr=record(verdict("REQUEST_CHANGES", 400)))
    assert h.fired == [("DRE-5930", REPO, RERUN, "proof-execute")]
    assert tally.dispatched == 1 and h.holds == []
    assert len(h.posted) == 1
    first = h.posted[0][1].split("\n", 1)[0]
    assert first.startswith("🔬 proof-run: dispatched a proof run at ")
    assert first.endswith(f" — {RERUN} (re-run 1 of 2)")
    # Nothing moved, nothing labelled, nothing lifted: the new head does that.
    assert writes.log == []
    assert not any(body.startswith("🔓") for _, body in h.posted)


def test_a_review_cap_park_with_two_re_runs_spent_gets_the_one_hold(monkeypatch, capsys):
    spent = (rerun_receipt(1500, 1), comment("⏳ 5/5 amended", 1400),
             rerun_receipt(800, 2), comment("⏳ 5/5 amended", 700))
    h, tally, _, writes = _capped_pass(
        monkeypatch, thread=_capped(*spent), pr=record(verdict("REQUEST_CHANGES", 400)))
    assert h.fired == [] and h.posted == []
    assert h.holds == [("DRE-5930", *RERUN_HELD)]
    assert tally.held == 1 and writes.log == []

    # The hold is said once: the next pass reads it and names it.
    capsys.readouterr()
    thread = _capped(*spent, hold(*RERUN_HELD, 100))
    h, _, _, _ = _capped_pass(monkeypatch, thread=thread,
                              pr=record(verdict("REQUEST_CHANGES", 400)))
    assert h.fired == [] and h.holds == [] and h.posted == []
    assert any("held by 🔬 proof-waiting: the record was sent back twice" in line
               for line in _about(_lines(capsys), "DRE-5930"))


def test_a_park_on_a_head_the_record_has_left_is_the_holds_lanes(monkeypatch, capsys):
    # The record moved on and the critic sent the new head back too; the
    # stamp is on the old one. The holds lane lifts it and returns the card.
    h, tally, _, writes = _capped_pass(
        monkeypatch, thread=_capped(head=OLD_HEAD),
        pr=record(verdict("REQUEST_CHANGES", 400)))
    lines = _about(_lines(capsys), "DRE-5930")
    assert h.fired == [] and h.posted == [] and h.holds == []
    assert writes.log == []
    assert any("re-run" in line and "holds lane" in line and OLD_HEAD[:7] in line
               and SHA7 in line for line in lines), lines


def test_a_label_over_a_spent_stamp_is_a_persons_and_costs_no_read(monkeypatch, capsys):
    import hold as hold_module

    lifted = comment(hold_module.lift_line("review-cap-spent", "new-head",
                                           "hygiene_holds.py"), 200)
    h, _, board, _ = _capped_pass(monkeypatch, thread=_capped(lifted),
                                  pr=record(verdict("REQUEST_CHANGES", 400)))
    assert h.fired == [] and h.record_reads == []
    assert [r for r in board.reads if r[0] != "lane"] == []


def test_a_label_with_no_stamp_at_all_is_not_a_candidate(monkeypatch, capsys):
    h, _, board, _ = _capped_pass(monkeypatch, thread=_sent_back(),
                                  pr=record(verdict("REQUEST_CHANGES", 400)))
    assert h.fired == [] and h.record_reads == []
    assert [r for r in board.reads if r[0] != "lane"] == []


def test_a_stamp_without_the_label_is_not_a_candidate(monkeypatch, capsys):
    h, _, board, _ = _capped_pass(monkeypatch, thread=_capped(), labels=(),
                                  pr=record(verdict("REQUEST_CHANGES", 400)))
    assert h.fired == [] and h.record_reads == []
    assert [r for r in board.reads if r[0] != "lane"] == []


def test_a_partial_window_is_decided_on_the_whole_thread(monkeypatch, capsys):
    # The lane read's window is the newest few comments and carries no stamp;
    # the whole thread does, so the card is read and re-run.
    thread = _capped(comment("chatter", 50))
    window = [comment("chatter", 50)]
    board = Board(green=[_capped_card(window)], threads={"DRE-5930": thread})
    board.lanes["Green Light"][0]["comments"]["pageInfo"]["hasNextPage"] = True
    h = Harness(monkeypatch, board,
                records={"DRE-5930": record(verdict("REQUEST_CHANGES", 400))},
                states={"DRE-5930": state("finished", dispatches=1)})
    h.sweep()
    assert [f[2] for f in h.fired] == [RERUN]

    # And a whole thread whose hold is not the sweep's park is refused.
    capsys.readouterr()
    board = Board(green=[_capped_card(window)],
                  threads={"DRE-5930": _sent_back(comment("chatter", 50))})
    board.lanes["Green Light"][0]["comments"]["pageInfo"]["hasNextPage"] = True
    h = Harness(monkeypatch, board,
                records={"DRE-5930": record(verdict("REQUEST_CHANGES", 400))},
                states={"DRE-5930": state("finished", dispatches=1)})
    h.sweep()
    assert h.fired == []
    assert any("review-cap" in line
               for line in _about(_lines(capsys), "DRE-5930"))


def test_a_park_on_the_ceos_press_is_the_return_branchs_only(monkeypatch, capsys):
    # Both shapes at once: his answered press AND the sweep's stamp. The
    # return takes it, and the re-run never reads it.
    nodes = sorted(_parked() + [review_cap_stamp(500)], key=lambda c: c["createdAt"])
    board = Board(green=[_capped_card(nodes)], threads={"DRE-5930": nodes})
    h = Harness(monkeypatch, board,
                records={"DRE-5930": record(verdict("REQUEST_CHANGES", 400))},
                states={"DRE-5930": state("finished")})
    h.sweep()
    lines = _about(_lines(capsys), "DRE-5930")
    assert [f[2].startswith("re-run after the CEO's answer") for f in h.fired] == [True]
    assert h.record_reads == []
    assert not any("deferred" in line or "re-run:" in line for line in lines), lines


def test_a_review_cap_park_in_the_dry_run_prints_would_and_writes_nothing(
        monkeypatch, capsys):
    h, _, _, writes = _capped_pass(monkeypatch, thread=_capped(), live=False,
                                   pr=record(verdict("REQUEST_CHANGES", 400)))
    lines = _lines(capsys)
    assert h.fired == [] and h.posted == [] and h.holds == [] and writes.log == []
    assert f"would: dispatch DRE-5930 — {RERUN}" in lines


def test_a_review_cap_park_costs_no_linear_read_beyond_the_return_branchs(
        monkeypatch, capsys):
    """The real readers over a counting transport: the board read and the
    Green Light read the return branch already makes, then the candidate's
    one thread read — three requests, and the Green Light list is read once."""
    import reconcile

    monkeypatch.delenv("BUREAU_READ", raising=False)
    thread = _capped()
    green = [_capped_card(thread),
             lane_card("DRE-5999", title="[EPIC] a plan", state="Green Light")]
    calls: list = []

    def gql(query, variables=None):
        calls.append(query)
        variables = variables or {}
        if "issues(" in query:
            states = set(variables.get("states") or ())
            nodes = [c for c in green if c["state"]["name"] in states]
            return {"issues": {"nodes": nodes,
                               "pageInfo": {"hasNextPage": False, "endCursor": None}}}
        if "viewer { id }" in query and "comments" in query:
            return {"viewer": {"id": VIEWER}, "issue": {"comments": {
                "nodes": list(reversed(thread)),
                "pageInfo": {"hasNextPage": False, "endCursor": None}}}}
        raise AssertionError(f"unexpected query {query[:80]}")

    monkeypatch.setattr(linear_ops, "gql", gql)
    reconcile.reset_sweep_cards()
    h = Harness(monkeypatch, Board(),
                records={"DRE-5930": record(verdict("REQUEST_CHANGES", 400))},
                states={"DRE-5930": state("finished", dispatches=1)})
    proof_dispatch.sweep(
        REPO, SLUG, live=False, read=lambda path: None, find_pr=h.find_pr,
        run_state=h.run_state, release=h.release, fire=h.fire,
        voices=fake_voices, now=NOW, find_record=h.find_record)
    assert f"would: dispatch DRE-5930 — {RERUN}" in _lines(capsys)
    assert len(calls) == 3, calls
    assert len([q for q in calls if "issues(" in q]) == 2


# --------------------------------------------------------------------------- #
# the re-run after the merge gate's decline of an approved record (DRE-6488)   #
# --------------------------------------------------------------------------- #

GATE_RERUN = f"re-run after the gate's decline at {SHA7}"
GATE_HELD = ("the gate declined the record twice after re-observation",
             "an operator reading the gate's declined note and the two re-run "
             "receipts")
GATE_UNANSWERED = ("the re-run finished and the gate's decline still stands at "
                   "the record's head",
                   "an operator reading the gate's declined note and the "
                   "re-run's thread")
NOT_MET_ROW = ("The release tag moves to the merged commit",
               "Not met. the tag still names v41")
WAITING_ROW = ("The release tag moves to the merged commit",
               "Not observed. waiting for the 2026-10-14 release: the tag moves")
BROWSER_ROW = ("The console renders the card as waiting for proof",
               "Not observed. needs a browser on a local run of the released commit")
MET_ROW = ("The sweep reads the gate's note", "Met. the 12:07 PT pass read it")


def gate_note(minutes_ago: float, *, sha: str = HEAD, reason: str | None = None,
              login: str = QA_BOT) -> dict:
    reason = reason or (f"proof record not proven: {RECORD_PATH} has 1 row(s) "
                        "not met: “The release tag moves to the merged commit” "
                        "reads “Not met. the tag still names v41” (DRE-6141)")
    return {"author": {"login": login}, "createdAt": _at(minutes_ago),
            "body": f"⏸️ Merge gate: declined @{sha} — {reason}\n\nNot merged. "
                    "The gate looks again on the next CI completion, review or "
                    "sweep, and merges once nothing holds it."}


def record_md(*rows) -> str:
    table = "\n".join(f"| {c} | {r} |" for c, r in rows)
    return ("# PROOF record — DRE-5930: observed live (epic DRE-5920)\n\n"
            "**Status: FAIL.**\n\n| Criterion | Result |\n|---|---|\n"
            f"{table}\n| The CEO closes this card after reading the record | "
            "Open: the CEO's step |\n")


def gate_rerun_receipt(minutes_ago: float, n: int) -> dict:
    return receipt(minutes_ago, reason=f"re-run after the gate's decline at "
                                       f"{OLD_HEAD[:7]}", count=f"re-run {n} of 2")


def _declined(*, approve: dict | None = None, note: dict | None = None, **kw) -> dict:
    """A record pull request the critic approved and the gate declined, both
    at the head unless told otherwise."""
    return record(approve or verdict("APPROVE", 100), note or gate_note(90), **kw)


def _gate_pass(monkeypatch, *, thread, pr, text=None, run="finished", live=True):
    board = Board(review=[_review()], threads={"DRE-5930": thread})
    writes = _LaneWrites(monkeypatch)
    h = Harness(monkeypatch, board, records={"DRE-5930": pr},
                texts={900: record_md(MET_ROW, NOT_MET_ROW) if text is None else text},
                states={"DRE-5930": state(run, dispatches=1,
                                          record={"number": 900, "state": "open"})})
    tally = h.sweep(live=live)
    return h, tally, writes


def test_gate_an_approved_record_the_gate_declined_at_its_head_is_re_run_once(
        monkeypatch, capsys):
    h, tally, writes = _gate_pass(monkeypatch, thread=_sent_back(), pr=_declined())
    assert h.fired == [("DRE-5930", REPO, GATE_RERUN, "proof-execute")]
    assert tally.dispatched == 1 and h.holds == []
    assert len(h.posted) == 1
    body = h.posted[0][1]
    first = body.split("\n", 1)[0]
    assert first.startswith("🔬 proof-run: dispatched a proof run at ")
    assert first.endswith(f" — {GATE_RERUN} (re-run 1 of 2)")
    parsed = proof_run_state.receipt(body)
    assert parsed.reason == GATE_RERUN and parsed.count == "re-run 1 of 2"
    assert pipeline_act.read_trailer(body)["act"] == "proof-run-dispatched"
    # The record was read at the head, and nothing moved.
    assert h.text_reads == [(900, HEAD)]
    assert writes.log == [] and h.release_calls == []


def test_gate_the_reason_constant_sits_beside_the_critics():
    assert proof_dispatch.GATE_RERUN_REASON.format(sha7=SHA7) == GATE_RERUN
    assert proof_dispatch.RERUN_COUNT == "re-run {n} of 2"
    assert "files" in proof_dispatch.RECORD_FIELDS.split(",")


def _gate_nothing_cases():
    return {
        "note-on-an-earlier-head": (
            _declined(note=gate_note(90, sha=OLD_HEAD)), f"{OLD_HEAD[:7]}"),
        "note-with-another-reason": (
            _declined(note=gate_note(90, reason="What's new: the line is missing")),
            "not a proof-record decline"),
        "note-by-another-login": (
            _declined(note=gate_note(90, login="mallory")), "no decline note"),
        "no-note-at-all": (record(verdict("APPROVE", 100)), "no decline note"),
        "approve-from-an-earlier-head": (
            _declined(approve=verdict("APPROVE", 100, sha=OLD_HEAD)),
            f"APPROVE from earlier head {OLD_HEAD[:7]}"),
        "record-unreadable-at-the-head": (
            _declined(), "could not be read"),
    }


@pytest.mark.parametrize("case", list(_gate_nothing_cases()))
def test_gate_dispatches_nothing_and_says_why(monkeypatch, capsys, case):
    pr, why = _gate_nothing_cases()[case]
    text = RuntimeError("HTTP 404") if case == "record-unreadable-at-the-head" else None
    h, _, _ = _gate_pass(monkeypatch, thread=_sent_back(), pr=pr, text=text)
    lines = _about(_lines(capsys), "DRE-5930")
    assert h.fired == [] and h.posted == [] and h.holds == []
    assert any("re-run" in line and why in line for line in lines), lines


def test_gate_a_request_changes_at_the_head_is_the_critics_re_run_not_the_gates(
        monkeypatch, capsys):
    """Both stand at the head: the critic's trigger keeps precedence, and the
    record is not read for it."""
    h, _, _ = _gate_pass(monkeypatch, thread=_sent_back(),
                         pr=_declined(approve=verdict("REQUEST_CHANGES", 100)))
    assert [f[2] for f in h.fired] == [RERUN]
    assert h.text_reads == []


def test_gate_a_review_cap_park_on_the_head_is_re_run_the_same_way(monkeypatch, capsys):
    board =Board(green=[_capped_card(_capped())], threads={"DRE-5930": _capped()})
    writes = _LaneWrites(monkeypatch)
    h = Harness(monkeypatch, board, records={"DRE-5930": _declined()},
                texts={900: record_md(NOT_MET_ROW)},
                states={"DRE-5930": state("finished", dispatches=1)})
    h.sweep()
    assert h.fired == [("DRE-5930", REPO, GATE_RERUN, "proof-execute")]
    assert h.posted[0][1].split("\n", 1)[0].endswith(f" — {GATE_RERUN} (re-run 1 of 2)")
    assert writes.log == []


def test_gate_a_review_cap_park_on_an_older_head_is_the_holds_lanes(monkeypatch, capsys):
    board = Board(green=[_capped_card(_capped(head=OLD_HEAD))],
                  threads={"DRE-5930": _capped(head=OLD_HEAD)})
    writes = _LaneWrites(monkeypatch)
    h = Harness(monkeypatch, board, records={"DRE-5930": _declined()},
                texts={900: record_md(NOT_MET_ROW)},
                states={"DRE-5930": state("finished", dispatches=1)})
    h.sweep()
    lines = _about(_lines(capsys), "DRE-5930")
    assert h.fired == [] and h.posted == [] and h.holds == [] and writes.log == []
    assert any("holds lane" in line and OLD_HEAD[:7] in line for line in lines), lines


@pytest.mark.parametrize("first, second", [
    (rerun_receipt, gate_rerun_receipt), (gate_rerun_receipt, rerun_receipt)])
def test_gate_after_the_shared_budget_one_hold_and_never_a_loop(
        monkeypatch, capsys, first, second):
    """One critic re-run and one gate re-run, in either order, spend the one
    budget of two: the gate's next decline holds once and never a third."""
    spent = (first(1500, 1), comment("⏳ 5/5 amended", 1400),
             second(800, 2), comment("⏳ 5/5 amended", 700))
    h, tally, _ = _gate_pass(monkeypatch, thread=_sent_back(*spent), pr=_declined())
    out = capsys.readouterr().out
    assert h.fired == [] and h.posted == []
    assert h.holds == [("DRE-5930", *GATE_HELD)]
    assert tally.held == 1 and "3 of 2" not in out

    # The next pass names the hold it posted and posts nothing.
    h, tally, _ = _gate_pass(monkeypatch, pr=_declined(),
                             thread=_sent_back(*spent, hold(*GATE_HELD, 50)))
    lines = _about(_lines(capsys), "DRE-5930")
    assert h.fired == [] and h.holds == [] and h.posted == []
    assert any("held by 🔬 proof-waiting: the gate declined the record twice after "
               "re-observation" in line for line in lines), lines
    assert tally.held == 1


def test_gate_a_critic_re_run_spends_one_of_the_gates_two(monkeypatch, capsys):
    thread = _sent_back(rerun_receipt(1500, 1), comment("⏳ 5/5 amended", 1400))
    h, _, _ = _gate_pass(monkeypatch, thread=thread, pr=_declined())
    assert h.posted[0][1].split("\n", 1)[0].endswith(f" — {GATE_RERUN} (re-run 2 of 2)")


def test_gate_a_re_run_that_finished_with_the_decline_at_the_head_holds_once(
        monkeypatch, capsys):
    done = (gate_rerun_receipt(50, 1), comment("⏳ 5/5 record amended", 20))
    h, tally, _ = _gate_pass(monkeypatch, thread=_sent_back(*done), pr=_declined())
    assert h.fired == [] and h.posted == []
    assert h.holds == [("DRE-5930", *GATE_UNANSWERED)]
    assert tally.held == 1

    capsys.readouterr()
    h, _, _ = _gate_pass(monkeypatch, pr=_declined(),
                         thread=_sent_back(*done, hold(*GATE_UNANSWERED, 10)))
    lines = _about(_lines(capsys), "DRE-5930")
    assert h.fired == [] and h.holds == [] and h.posted == []
    assert any("held by 🔬 proof-waiting: the re-run finished and the gate's decline"
               in line for line in lines), lines


@pytest.mark.parametrize("run", ["dead", "never-started"])
def test_gate_a_re_run_that_died_or_never_started_is_sent_again_and_counted(
        monkeypatch, capsys, run):
    h, _, _ = _gate_pass(monkeypatch, thread=_sent_back(gate_rerun_receipt(50, 1)),
                         pr=_declined(), run=run)
    assert [f[2] for f in h.fired] == [GATE_RERUN] and h.holds == []
    assert h.posted[0][1].split("\n", 1)[0].endswith(f" — {GATE_RERUN} (re-run 2 of 2)")


def test_gate_a_newer_re_run_still_running_is_not_sent_again(monkeypatch, capsys):
    h, _, _ = _gate_pass(monkeypatch, thread=_sent_back(gate_rerun_receipt(50, 1)),
                         pr=_declined(), run="running")
    assert h.fired == [] and h.posted == [] and h.holds == []


WAITING = (proof_record._cut(WAITING_ROW[0], 70),
           "the 2026-10-14 release: the tag moves")


def test_gate_a_row_waiting_on_an_event_holds_instead_of_a_re_run(monkeypatch, capsys):
    h, tally, writes = _gate_pass(monkeypatch, thread=_sent_back(), pr=_declined(),
                                  text=record_md(MET_ROW, WAITING_ROW))
    assert h.fired == [] and h.posted == []
    assert h.holds == [("DRE-5930", *WAITING)]
    assert tally.held == 1 and writes.log == []
    line = linear_ops.proof_waiting_line(*WAITING)
    assert line == ("🔬 proof-waiting: The release tag moves to the merged commit "
                    "— needs the 2026-10-14 release: the tag moves")
    for half in WAITING:
        assert linear_ops.proof_text_refusal(half, "half") is None

    # The next pass names the hold and posts nothing.
    capsys.readouterr()
    held = _sent_back(hold(*WAITING, 60))
    h, tally, _ = _gate_pass(monkeypatch, thread=held, pr=_declined(),
                             text=record_md(MET_ROW, WAITING_ROW))
    lines = _about(_lines(capsys), "DRE-5930")
    assert h.fired == [] and h.posted == [] and h.holds == []
    assert any(f"held by {line}" in l for l in lines), lines

    # The operator observes the event: the record is re-run to re-observe it.
    observed = _sent_back(hold(*WAITING, 60), comment(linear_ops.proof_observed_line(
        "the 2026-10-14 release was cut at 10:02 PT"), 30, PERSON))
    h, _, _ = _gate_pass(monkeypatch, thread=observed, pr=_declined(),
                         text=record_md(MET_ROW, WAITING_ROW))
    assert [f[2] for f in h.fired] == [GATE_RERUN] and h.holds == []
    assert h.posted[0][1].split("\n", 1)[0].endswith(f" — {GATE_RERUN} (re-run 1 of 2)")


def test_gate_the_hold_names_every_waiting_row_in_table_order_and_wins(
        monkeypatch, capsys):
    long = ("The nightly full run on main reports green to the fleet watcher "
            "within the hour of its cron")
    rows = (NOT_MET_ROW, (long, "Not observed. Waiting For the 02:07 PT nightly"),
            BROWSER_ROW, WAITING_ROW)
    h, _, _ = _gate_pass(monkeypatch, thread=_sent_back(), pr=_declined(),
                         text=record_md(*rows))
    assert h.fired == []
    cut = proof_record._cut(long, 70)
    assert len(cut) == 70 and cut.endswith("…")
    assert h.holds == [("DRE-5930", f"{cut}; {WAITING[0]}",
                        f"the 02:07 PT nightly; {WAITING[1]}")]


def test_gate_a_row_a_re_run_may_re_observe_is_re_run_not_held(monkeypatch, capsys):
    h, _, _ = _gate_pass(monkeypatch, thread=_sent_back(), pr=_declined(),
                         text=record_md(MET_ROW, BROWSER_ROW))
    assert [f[2] for f in h.fired] == [GATE_RERUN] and h.holds == []


def test_gate_the_dry_run_prints_would_and_writes_nothing(monkeypatch, capsys):
    h, _, writes = _gate_pass(monkeypatch, thread=_sent_back(), pr=_declined(),
                              text=record_md(WAITING_ROW), live=False)
    lines = _lines(capsys)
    assert h.fired == [] and h.posted == [] and h.holds == [] and writes.log == []
    assert f"would: hold DRE-5930 — {WAITING[0]}" in lines

    h, _, writes = _gate_pass(monkeypatch, thread=_sent_back(), pr=_declined(),
                              live=False)
    lines = _lines(capsys)
    assert h.fired == [] and h.posted == [] and h.holds == [] and writes.log == []
    assert f"would: dispatch DRE-5930 — {GATE_RERUN}" in lines


def test_gate_the_record_is_read_at_the_head_off_the_pull_requests_files(
        monkeypatch, capsys):
    """The production read: `proof_record.fetch` over the pull request's
    `files`, at its head, through the sweep's GitHub read seam."""
    import base64

    import reconcile

    asked: list = []
    text = record_md(WAITING_ROW)

    def gh_read(*args):
        asked.append(args)
        return json.dumps({"encoding": "base64",
                           "content": base64.b64encode(text.encode()).decode()})

    monkeypatch.setattr(reconcile, "gh_read", gh_read)
    board = Board(review=[_review()], threads={"DRE-5930": _sent_back()})
    h = Harness(monkeypatch, board, records={"DRE-5930": _declined()},
                states={"DRE-5930": state("finished", dispatches=1)})
    proof_dispatch.sweep(
        REPO, SLUG, live=True, linear=board, read=lambda path: None,
        find_pr=h.find_pr, run_state=h.run_state, release=h.release,
        fire=h.fire, voices=fake_voices, now=NOW, find_record=h.find_record)
    assert asked == [("api", f"repos/{REPO}/contents/{RECORD_PATH}?ref={HEAD}")]
    assert h.holds == [("DRE-5930", *WAITING)] and h.fired == []


# --------------------------------------------------------------------------- #
# DRE-6571: a record the critic approved that is red on a CHECK, not on a row  #
# --------------------------------------------------------------------------- #
RED_CHECK = "scripts unit tests (part 2)"


def rollup(**conclusions) -> list:
    """The head's checks as `gh pr view --json statusCheckRollup` returns
    them: CheckRun nodes, conclusions in capitals."""
    return [{"__typename": "CheckRun", "name": name, "status": "COMPLETED",
             "conclusion": conclusion.upper()}
            for name, conclusion in {"scripts unit tests (part 1)": "success",
                                     "QA critic review": "success",
                                     **conclusions}.items()]


def _red(pr: dict, **conclusions) -> dict:
    return dict(pr, statusCheckRollup=rollup(**conclusions))


def test_red_the_record_pull_request_is_read_with_its_checks():
    assert "statusCheckRollup" in proof_dispatch.RECORD_FIELDS.split(",")


def test_red_an_approved_record_red_on_a_check_names_the_check_and_the_fix_agent(
        monkeypatch, capsys):
    pr = _red(record(verdict("APPROVE", 100)), **{RED_CHECK: "failure"})
    h, tally, writes = _gate_pass(monkeypatch, thread=_sent_back(), pr=pr)
    lines = _about(_lines(capsys), "DRE-5930")
    assert h.fired == [] and h.posted == [] and h.holds == [] and writes.log == []
    said = [line for line in lines if "re-run" in line]
    assert len(said) == 1, lines
    assert RED_CHECK in said[0] and "the fix agent has it" in said[0], said
    assert "nothing to answer" not in said[0]
    assert tally.someone_else == 1
    # The record itself is never read for it: a check is not a row.
    assert h.text_reads == []


def test_red_a_review_check_or_a_cancelled_one_is_not_red(monkeypatch, capsys):
    for conclusions in ({RED_CHECK: "cancelled"}, {"call / review": "failure"}):
        pr = _red(record(verdict("APPROVE", 100)), **conclusions)
        _gate_pass(monkeypatch, thread=_sent_back(), pr=pr)
        lines = _about(_lines(capsys), "DRE-5930")
        assert any("no decline note" in line and "nothing to answer" in line
                   for line in lines), lines
        assert not any("fix agent" in line for line in lines), lines


def test_red_a_not_proven_decline_at_the_head_is_re_run_as_today(monkeypatch, capsys):
    """DRE-6488's path holds whatever the checks say: the decline at the head
    is a row only the proof run can re-observe."""
    h, tally, _ = _gate_pass(monkeypatch, thread=_sent_back(),
                             pr=_red(_declined(), **{RED_CHECK: "failure"}))
    assert h.fired == [("DRE-5930", REPO, GATE_RERUN, "proof-execute")]
    assert tally.dispatched == 1


def test_red_a_decline_on_an_earlier_head_with_a_red_check_is_the_fix_agents(
        monkeypatch, capsys):
    pr = _red(_declined(note=gate_note(90, sha=OLD_HEAD)), **{RED_CHECK: "failure"})
    h, _, _ = _gate_pass(monkeypatch, thread=_sent_back(), pr=pr)
    lines = _about(_lines(capsys), "DRE-5930")
    assert h.fired == []
    assert any(RED_CHECK in line and "the fix agent has it" in line for line in lines), lines
