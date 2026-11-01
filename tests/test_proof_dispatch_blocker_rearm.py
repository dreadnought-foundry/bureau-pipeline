"""A spent proof re-arms when a fix card filed against it ships (DRE-8156).

The re-arm of DRE-7740 read one source of fix cards, the record's `fix:`
cells, and refused a fix labelled for another repo. Two proofs stood still on
that: DRE-7251, whose two fixes were filed as Linear `blocks` relations and
shipped in agent-bureau and in Portico, and DRE-7430, which moved only after
an operator hand-wrote ` fix: DRE-8032` into its record. And the lane skip of
DRE-7787 never looked at a relation added after its marked hold.

Pinned here, replayed off the two proofs' own pipeline lines, relation rows,
card states, pull request rows and times — no person's name or email:

  * DRE-7251: two blockers related after the newest `🔬 proof-run` receipt,
    one per repo, each merged and each release `ready` — the lane read does
    not skip it, and one run, `re-run after DRE-7990, DRE-7991 shipped
    (re-armed)`;
  * DRE-7430 without the hand-added marker: In Review holds and says so, Done
    and live dispatches;
  * a blocker related before the newest receipt — an epic's build card — adds
    nothing, and the budget's hold is marked only when neither source names
    a fix;
  * a blocker Canceled, unmerged, or not live, here or in another repo, a
    repo outside the roster, an untimed relation and unread relations: no
    re-arm, the line naming the card and why;
  * the set re-arms once;
  * the two Linear reads: the proof's relations read to the end, and the lane
    read's relation times in one request for every marked card.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import linear_ops  # noqa: E402
import proof_dispatch  # noqa: E402
import proof_release  # noqa: E402
import proof_run_state  # noqa: E402
from test_proof_dispatch import (  # noqa: E402
    MET_ROW,
    NOT_MET_ROW,
    PERSON,
    VIEWER,
    Board,
    Harness,
    _about,
    _lines,
    fake_voices,
    gate_note,
    lane_card,
    state,
    verdict,
)

AB = "agent-bureau"
AB_REPO = "dreadnought-foundry/agent-bureau"
PORTICO_REPO = "dreadnought-foundry/portico"
SPENT_NAMED = "re-run: budget spent and nothing new"
GATE_HELD = (proof_dispatch.GATE_EXHAUSTED_OBSERVED, proof_dispatch.GATE_EXHAUSTED_NEEDS)


def _ts(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def at(body: str, stamp: str, user: str | None = VIEWER) -> dict:
    """A comment posted at an exact UTC time."""
    return {"body": body, "createdAt": stamp, "user": {"id": user} if user else None}


def run_receipt(stamp: str, pt: str, reason: str, count: str) -> dict:
    return at(f"🔬 proof-run: dispatched a proof run at {pt} — {reason} ({count})", stamp)


def budget_hold(stamp: str, needs: str) -> dict:
    return at(linear_ops.proof_waiting_line(GATE_HELD[0], needs), stamp)


def relation(ident: str, stamp: str | None) -> dict:
    return {"type": "blocks", "createdAt": stamp, "issue": {"identifier": ident}}


def fix(ident: str, repo: str, state_name: str = "Done") -> dict:
    return {"identifier": ident, "state": {"name": state_name},
            "labels": {"nodes": [{"name": f"repo:{repo}"}, {"name": "agent:engineer"}]}}


def merged(number: int, sha: str, ident: str, stamp: str | None = None) -> dict:
    """A merged pull request row; `stamp` is its merge time where the
    record gives one."""
    row = {"number": number, "state": "MERGED", "headRefName": f"agent/{ident}-fix",
           "mergeCommit": {"oid": sha}}
    return {**row, "mergedAt": stamp} if stamp else row


def record_text(ident: str, *rows) -> str:
    table = "\n".join(f"| {c} | {r} |" for c, r in rows)
    return (f"# PROOF record — {ident}: observed live\n\n**Status: FAIL.**\n\n"
            f"| Criterion | Result |\n|---|---|\n{table}\n")


def record_pr(ident: str, number: int, head: str, *, opened: str, approved: str,
              declined: str) -> dict:
    """The record pull request the critic approved and the gate declined at
    `head` — the shape `test_proof_dispatch.record` builds, at exact times."""
    return {"number": number, "url": f"https://github.com/{AB_REPO}/pull/{number}",
            "headRefName": f"agent/{ident}-proof-record", "state": "OPEN",
            "headRefOid": head, "createdAt": opened,
            "files": [{"path": f"docs/proof-{ident.lower()}.md", "changeType": "ADDED"}],
            "comments": [{"author": {"login": "agent-bureau-bot"}, "createdAt": opened,
                          "body": "opened"},
                         {**verdict("APPROVE", 0, sha=head), "createdAt": approved},
                         {**gate_note(0, sha=head), "createdAt": declined}]}


class Relations(Board):
    """The fixture board, with the proof's `blocks` relations and their times."""

    def __init__(self, *args, relations=None, **kw):
        super().__init__(*args, **kw)
        self.relations = relations or {}

    def blocking(self, ident):
        self.reads.append(("blocking", ident))
        got = self.relations.get(ident, [])
        if isinstance(got, Exception):
            raise got
        return list(got)


class Fleet(Harness):
    """One pass in agent-bureau: pull requests and releases read per repo."""

    def __init__(self, monkeypatch, board, *, prs, releases=None, **kw):
        super().__init__(monkeypatch, board, **kw)
        self.repo_prs = prs
        self.releases = releases or {}
        self.pr_reads: list = []

    def find_pr(self, ident, repo=AB_REPO):
        self.pr_reads.append((repo, ident))
        got = self.repo_prs.get((repo, ident))
        if isinstance(got, Exception):
            raise got
        return got

    def release(self, repo, merges, *, read):
        self.release_calls.append((repo, [m.card for m in merges]))
        got = self.releases.get(repo, ("ready", [f"ready — {repo} fixture"]))
        if isinstance(got, Exception):
            raise got
        return proof_release.Reading(*got)

    def sweep(self, *, live=True, now):
        return proof_dispatch.sweep(
            AB_REPO, AB, live=live, linear=self.board, read=lambda path: None,
            find_pr=self.find_pr, run_state=self.run_state, release=self.release,
            fire=self.fire, voices=fake_voices, now=now,
            find_record=self.find_record, read_record=self.read_record,
            open_heads=self.open_heads)


def _review(ident: str, thread: list, relations=None) -> dict:
    """The proof as the lane read shows it: its whole thread in the window
    and, when given, its relations with their times."""
    card = lane_card(ident, state="In Review", repo=AB, window=thread)
    if relations is not None:
        card["inverseRelations"] = {"pageInfo": {"hasNextPage": False, "endCursor": None},
                                    "nodes": list(relations)}
    return card


def _receipt_reason(h) -> str:
    [(_, body)] = h.posted
    parsed = proof_run_state.receipt(body)
    return f"{parsed.reason} ({parsed.count})"


# --------------------------------------------------------------------------- #
# DRE-7251, record #3957 at 25ef001                                            #
# --------------------------------------------------------------------------- #

D7251 = "DRE-7251"
HEAD_7251 = "25ef001" + "0" * 33
PR_7251 = 3957
#: The newest receipt: the budget's last run, re-armed by the operator's note.
REARMED_7251 = run_receipt("2026-10-25T03:51:57Z", "2026-10-24 20:51 PT",
                           "re-run after the 🔬 proof-observed of 2026-10-24 17:56 PT",
                           "re-armed")
#: Its two re-runs and the note that re-armed it, before the newest receipt.
SPENT_7251 = [
    run_receipt("2026-10-21T16:02:10Z", "2026-10-21 09:02 PT", "first proof run",
                "dispatch 1 of 2"),
    at("⏳ 5/5 PR opened", "2026-10-21T16:40:00Z"),
    run_receipt("2026-10-22T17:15:00Z", "2026-10-22 10:15 PT",
                "re-run after the gate's decline at 1d2e3f4", "re-run 1 of 2"),
    run_receipt("2026-10-23T18:20:00Z", "2026-10-23 11:20 PT",
                "re-run after the gate's decline at 5a6b7c8", "re-run 2 of 2"),
    at(linear_ops.proof_observed_line("the row reads differently at 17:50 PT"),
       "2026-10-25T00:56:00Z", PERSON),
    REARMED_7251,
]
#: The budget's hold, marked with the head the record was read at.
HOLD_7251 = budget_hold("2026-10-25T04:32:22Z",
                        f"{GATE_HELD[1]} — the re-armed run of 2026-10-24 20:51 PT was "
                        "its event's one run — the record at 25ef001 names no fix")
THREAD_7251 = SPENT_7251 + [HOLD_7251]
#: The epic's build cards, related long before, and the two fixes filed
#: against the proof on 10-25.
BUILD_7251 = [relation(f"DRE-72{40 + i}", "2026-10-19T20:00:00Z") for i in range(5)]
FIXES_7251 = [relation("DRE-7990", "2026-10-25T18:45:35Z"),
              relation("DRE-7991", "2026-10-25T18:45:37Z")]
CARDS_7251 = {"DRE-7990": fix("DRE-7990", "agent-bureau"),
              "DRE-7991": fix("DRE-7991", "portico"),
              **{r["issue"]["identifier"]: fix(r["issue"]["identifier"], "agent-bureau")
                 for r in BUILD_7251}}
PRS_7251 = {(AB_REPO, "DRE-7990"): merged(4179, "527d0d682" + "0" * 31, "DRE-7990",
                                     "2026-10-25T19:07:31Z"),
            (PORTICO_REPO, "DRE-7991"): merged(1050, "b8490f3d6" + "0" * 31, "DRE-7991",
                                         "2026-10-25T19:45:25Z")}
#: Merged 19:07:31Z and 19:45:25Z; agent-bureau Reconcile at 17:49:26Z on 10-27.
NOW_7251 = _ts("2026-10-27T17:49:26Z")
REARM_7251 = "re-run after DRE-7990, DRE-7991 shipped"


def _pass_7251(monkeypatch, *, thread=None, relations=None, lane_relations="same",
               cards=None, prs=None, releases=None, text=None, head=HEAD_7251,
               declined="2026-10-25T04:31:40Z", now=NOW_7251, live=True,
               run="finished"):
    thread = THREAD_7251 if thread is None else thread
    relations = BUILD_7251 + FIXES_7251 if relations is None else relations
    shown = relations if lane_relations == "same" else lane_relations
    board = Relations(review=[_review(D7251, thread, shown)], threads={D7251: thread},
                      fixes=CARDS_7251 if cards is None else cards,
                      relations={D7251: relations})
    pr = record_pr(D7251, PR_7251, head, opened="2026-10-21T16:35:00Z",
                   approved="2026-10-25T04:25:00Z", declined=declined)
    h = Fleet(monkeypatch, board, prs=PRS_7251 if prs is None else prs,
              releases=releases, records={D7251: pr},
              texts={PR_7251: record_text(D7251, MET_ROW, NOT_MET_ROW)
                     if text is None else text},
              states={D7251: state(run, dispatches=1,
                                   record={"number": PR_7251, "state": "open"})})
    tally = h.sweep(now=now, live=live)
    return h, tally


def test_dre_7251_replayed_two_blockers_in_two_repos_re_arm_one_run(monkeypatch, capsys):
    """On `stable` at 1b5ecc9 this pass printed `budget spent and nothing new`
    and dispatched nothing. Its fixes are blockers related after the newest
    receipt, DRE-7990 merged in agent-bureau and DRE-7991 in Portico, both
    Done and both releases ready: one run."""
    h, tally = _pass_7251(monkeypatch)
    lines = _about(_lines(capsys), D7251)
    assert not any(SPENT_NAMED in line for line in lines), lines
    assert h.fired == [(D7251, AB_REPO, REARM_7251, "proof-execute")], lines
    assert tally.dispatched == 1 and h.holds == []
    assert h.posted[0][1].split("\n", 1)[0].endswith(f" — {REARM_7251} (re-armed)")
    assert _receipt_reason(h) == f"{REARM_7251} (re-armed)"
    # Each fix's pull request on its own repo, each release in its own repo.
    assert (PORTICO_REPO, "DRE-7991") in h.pr_reads
    assert (AB_REPO, "DRE-7990") in h.pr_reads
    assert sorted(h.release_calls) == [(AB_REPO, ["DRE-7990"]),
                                       (PORTICO_REPO, ["DRE-7991"])]
    # The epic's build cards were related before the newest receipt: unread.
    assert not any(r[0] == "fix_card" and r[1].startswith("DRE-724") for r in h.board.reads)
    assert any(line.startswith(f"proof-release: {D7251} — ") and PORTICO_REPO in line
               for line in lines), lines


def test_dre_7251_the_lane_skip_reads_the_card_once_the_relations_exist(monkeypatch, capsys):
    """The marked hold with nothing after it is skipped at no read only while
    no blocking relation is newer than it (DRE-7787 kept): the build cards
    alone, it is skipped; with the two fixes related after it, it is read."""
    h, tally = _pass_7251(monkeypatch, relations=BUILD_7251)
    lines = _about(_lines(capsys), D7251)
    assert any(SPENT_NAMED in line for line in lines), lines
    assert ("thread", D7251) not in h.board.reads and h.record_reads == []
    assert h.fired == [] and tally.held == 1

    h, _ = _pass_7251(monkeypatch)
    lines = _about(_lines(capsys), D7251)
    assert ("thread", D7251) in h.board.reads and h.record_reads == [D7251]
    assert not any(SPENT_NAMED in line for line in lines), lines


def test_dre_7251_relations_the_lane_read_could_not_read_leave_it_a_candidate(
        monkeypatch, capsys):
    unread = {"pageInfo": {"hasNextPage": True, "endCursor": None}, "nodes": []}
    board_card = _review(D7251, THREAD_7251)
    board_card["inverseRelations"] = unread
    board = Relations(review=[board_card], threads={D7251: THREAD_7251},
                      fixes=CARDS_7251, relations={D7251: BUILD_7251})
    pr = record_pr(D7251, PR_7251, HEAD_7251, opened="2026-10-21T16:35:00Z",
                   approved="2026-10-25T04:25:00Z", declined="2026-10-25T04:31:40Z")
    h = Fleet(monkeypatch, board, prs=PRS_7251, records={D7251: pr},
              texts={PR_7251: record_text(D7251, MET_ROW, NOT_MET_ROW)},
              states={D7251: state("finished", dispatches=1)})
    h.sweep(now=NOW_7251)
    lines = _about(_lines(capsys), D7251)
    assert ("thread", D7251) in board.reads
    assert not any(SPENT_NAMED in line for line in lines), lines


# --------------------------------------------------------------------------- #
# DRE-7430's 10-26 sequence, without the hand-added ` fix: DRE-8032`           #
# --------------------------------------------------------------------------- #

D7430 = "DRE-7430"
HEAD_7430 = "a91b46e" + "0" * 33
PR_7430 = 3888
SPENT_7430 = [
    run_receipt("2026-10-23T15:00:00Z", "2026-10-23 08:00 PT", "first proof run",
                "dispatch 1 of 2"),
    at("⏳ 5/5 PR opened", "2026-10-23T15:40:00Z"),
    run_receipt("2026-10-25T16:00:00Z", "2026-10-25 09:00 PT",
                "re-run after the gate's decline at 0b1c2d3", "re-run 1 of 2"),
    run_receipt("2026-10-26T05:10:00Z", "2026-10-25 22:10 PT",
                "re-run after the gate's decline at 4e5f6a7", "re-run 2 of 2"),
]
THREAD_7430 = SPENT_7430 + [budget_hold(
    "2026-10-26T07:21:07Z", f"{GATE_HELD[1]} — the record at a91b46e names no fix")]
RELATIONS_7430 = [relation("DRE-7425", "2026-10-20T17:00:00Z"),
                  relation("DRE-8032", "2026-10-26T20:35:39Z")]
CONSOLE = "ready — console: newest agent-bureau-console-v1.6.628 carries #4219 (3a50c99) for DRE-8032"


def _pass_7430(monkeypatch, *, now, fix_state, prs, release=("ready", [CONSOLE])):
    board = Relations(review=[_review(D7430, THREAD_7430, RELATIONS_7430)],
                      threads={D7430: THREAD_7430},
                      fixes={"DRE-8032": fix("DRE-8032", "agent-bureau", fix_state),
                             "DRE-7425": fix("DRE-7425", "agent-bureau")},
                      relations={D7430: RELATIONS_7430})
    pr = record_pr(D7430, PR_7430, HEAD_7430, opened="2026-10-23T15:35:00Z",
                   approved="2026-10-26T07:00:00Z", declined="2026-10-26T07:20:30Z")
    h = Fleet(monkeypatch, board, prs=prs, releases={AB_REPO: release},
              records={D7430: pr},
              texts={PR_7430: record_text(D7430, MET_ROW, NOT_MET_ROW)},
              states={D7430: state("finished", dispatches=1,
                                   record={"number": PR_7430, "state": "open"})})
    tally = h.sweep(now=now)
    return h, tally


def test_dre_7430_replayed_holds_while_the_fix_is_in_review_and_re_arms_once_done(
        monkeypatch, capsys):
    h, tally = _pass_7430(monkeypatch, now=_ts("2026-10-26T21:06:16Z"),
                          fix_state="In Review", prs={})
    lines = _about(_lines(capsys), D7430)
    assert h.fired == [] and h.posted == [] and tally.held == 1
    assert any("the fix DRE-8032 is In Review, not Done" in line for line in lines), lines
    assert not any(SPENT_NAMED in line for line in lines), lines
    # The epic's build card, related before the newest receipt, is not read.
    assert ("fix_card", "DRE-7425") not in h.board.reads

    h, tally = _pass_7430(monkeypatch, now=_ts("2026-10-26T22:32:23Z"), fix_state="Done",
                          prs={(AB_REPO, "DRE-8032"): merged(4219, "3a50c99" + "0" * 33,
                                                             "DRE-8032")})
    lines = _about(_lines(capsys), D7430)
    assert h.fired == [(D7430, AB_REPO, "re-run after DRE-8032 shipped", "proof-execute")]
    assert _receipt_reason(h) == "re-run after DRE-8032 shipped (re-armed)"
    assert h.release_calls == [(AB_REPO, ["DRE-8032"])]
    assert f"proof-release: {D7430} — {CONSOLE}" in lines


# --------------------------------------------------------------------------- #
# what a blocker adds, and what it does not                                    #
# --------------------------------------------------------------------------- #

#: The budget spent and no hold posted yet, so each pass's hold is seen.
UNHELD_7251 = SPENT_7251


def test_a_blocker_related_before_the_newest_receipt_re_arms_nothing(monkeypatch, capsys):
    """An epic's build card — DRE-7420 here — blocks every proof and was Done
    long before. Related before the newest receipt, it adds nothing, so
    neither source names a fix and the budget's hold is marked."""
    build = [relation("DRE-7420", "2026-10-18T21:00:00Z")]
    h, tally = _pass_7251(monkeypatch, thread=UNHELD_7251, relations=build,
                          cards={"DRE-7420": fix("DRE-7420", "agent-bureau")})
    lines = _about(_lines(capsys), D7251)
    assert h.fired == [] and tally.held == 1
    assert ("fix_card", "DRE-7420") not in h.board.reads and h.release_calls == []
    assert h.holds == [(D7251, GATE_HELD[0],
                        f"{GATE_HELD[1]} — the re-armed run of 2026-10-24 20:51 PT was "
                        "its event's one run — the record at 25ef001 names no fix")]
    assert any("no unmet row of the record names a fix" in line
               and "blocks" in line for line in lines), lines


def test_a_blocker_fix_named_keeps_the_hold_unmarked(monkeypatch, capsys):
    """`SPENT_AT` is written only when neither source names a fix: a blocker
    filed after the receipt and not yet Done leaves the hold unmarked."""
    cards = {**CARDS_7251, "DRE-7990": fix("DRE-7990", "agent-bureau", "In Progress")}
    h, _ = _pass_7251(monkeypatch, thread=UNHELD_7251, cards=cards)
    [(_, observed, needs)] = h.holds
    assert observed == GATE_HELD[0] and "names no fix" not in needs


@pytest.mark.parametrize("repo", ["agent-bureau", "portico"])
@pytest.mark.parametrize("case, why", [
    ("canceled", "is Canceled, not Done"),
    ("unmerged", "is not merged"),
    ("pr-unread", "could not be read"),
    ("release-waiting", "is waiting"),
    ("release-unread", "could not be read"),
])
def test_a_blocker_fix_not_shipped_re_arms_nothing_and_the_line_names_it(
        monkeypatch, capsys, repo, case, why):
    full = f"dreadnought-foundry/{repo}"
    ident = "DRE-7990"
    cards = {ident: fix(ident, repo, "Canceled" if case == "canceled" else "Done")}
    pr = {"unmerged": {"number": 4179, "state": "OPEN", "headRefName": f"agent/{ident}-fix"},
          "pr-unread": RuntimeError("gh: HTTP 502")}.get(
        case, merged(4179, "527d0d682" + "0" * 31, ident))
    release = {"release-waiting": ("waiting", ["waiting — the train has not cut it"]),
               "release-unread": RuntimeError("gh: HTTP 403 on release.json")}.get(case)
    h, tally = _pass_7251(monkeypatch, relations=[FIXES_7251[0]], cards=cards,
                          prs={(full, ident): pr},
                          releases={full: release} if release else None)
    lines = _about(_lines(capsys), D7251)
    assert h.fired == [] and h.posted == [] and tally.held == 1
    assert any(ident in line and why in line for line in lines), lines
    if repo == "portico" and case not in ("canceled",):
        assert (full, ident) in h.pr_reads
    if case in ("canceled", "unmerged", "pr-unread"):
        assert h.release_calls == []


def test_a_blocker_labelled_for_a_repo_outside_the_roster_stays_refused(monkeypatch, capsys):
    cards = {"DRE-7990": fix("DRE-7990", "somewhere-else")}
    h, _ = _pass_7251(monkeypatch, relations=[FIXES_7251[0]], cards=cards)
    lines = _about(_lines(capsys), D7251)
    assert h.fired == [] and h.pr_reads == [] and h.release_calls == []
    assert any("DRE-7990" in line and "somewhere-else" in line
               and "proof-observed is the route" in line for line in lines), lines


def test_an_untimed_relation_and_unread_relations_add_nothing_and_say_so(monkeypatch, capsys):
    untimed = [relation("DRE-7990", None)]
    h, _ = _pass_7251(monkeypatch, relations=untimed, lane_relations=FIXES_7251)
    lines = _about(_lines(capsys), D7251)
    assert h.fired == [] and ("fix_card", "DRE-7990") not in h.board.reads
    assert any("DRE-7990" in line and "no time" in line for line in lines), lines

    h, _ = _pass_7251(monkeypatch, relations=RuntimeError("Linear: 503"),
                      lane_relations=FIXES_7251)
    lines = _about(_lines(capsys), D7251)
    assert h.fired == []
    assert any("relations could not be read" in line and "503" in line
               for line in lines), lines


def test_relations_not_read_leave_the_budget_hold_unmarked(monkeypatch, capsys):
    """Unread is not "names none": with the relations read failing, the two
    fixes filed after the receipt are still there, so the hold the pass
    posts carries no `SPENT_AT` the lane skip would trust."""
    h, tally = _pass_7251(monkeypatch, thread=UNHELD_7251,
                          relations=RuntimeError("Linear: 503"), lane_relations=FIXES_7251)
    assert h.fired == [] and tally.held == 1
    [(_, observed, needs)] = h.holds
    assert observed == GATE_HELD[0] and "names no fix" not in needs


def test_a_receipt_with_no_time_leaves_the_budget_hold_unmarked(monkeypatch, capsys):
    """A receipt with no time gives no cut to read the relations at, so they
    are unread, and the hold is not marked as naming no fix."""
    untimed = [*SPENT_7251[:-1], {**REARMED_7251, "createdAt": None}]
    h, tally = _pass_7251(monkeypatch, thread=untimed, run="dead")
    lines = _about(_lines(capsys), D7251)
    assert h.fired == [] and tally.held == 1, lines
    assert any("receipt has no time" in line for line in lines), lines
    [(_, observed, needs)] = h.holds
    assert observed == GATE_HELD[0] and "names no fix" not in needs


def test_the_record_and_the_blockers_are_one_set_under_the_cap(monkeypatch, capsys):
    """The record names DRE-7990 and DRE-7992 by `fix:`; the blockers add
    DRE-7990 again and DRE-7991 and DRE-7993: four distinct cards, more than
    `REARM_FIX_CARDS`, so none is read."""
    row = ("The Desk loads in under two seconds",
           "Not met. 9 s at 02:31 PT. fix: DRE-7990, DRE-7992")
    relations = FIXES_7251 + [relation("DRE-7993", "2026-10-25T18:46:00Z")]
    h, _ = _pass_7251(monkeypatch, relations=relations,
                      text=record_text(D7251, MET_ROW, row))
    lines = _about(_lines(capsys), D7251)
    assert h.fired == [] and not any(r[0] == "fix_card" for r in h.board.reads)
    assert any("more than three" in line and all(
        c in line for c in ("DRE-7990", "DRE-7991", "DRE-7992", "DRE-7993"))
        for line in lines), lines


def test_once_re_armed_the_next_pass_dispatches_nothing_for_the_same_set(
        monkeypatch, capsys):
    """The re-armed run amended the record and the gate declined the new
    head. Its receipt is now the newest, so the two relations are older than
    it and add nothing — and a record naming one of them by `fix:` meets the
    rule that a set re-arms once."""
    rearmed = run_receipt("2026-10-27T17:50:02Z", "2026-10-27 10:50 PT", REARM_7251,
                          "re-armed")
    thread = THREAD_7251 + [rearmed, at("⏳ 5/5 amended", "2026-10-27T18:30:00Z")]
    head = "9c8b7a6" + "0" * 33
    later = _ts("2026-10-27T19:04:26Z")
    h, tally = _pass_7251(monkeypatch, thread=thread, head=head,
                          declined="2026-10-27T18:40:00Z", now=later)
    assert h.fired == [] and tally.held == 1
    assert not any(r[0] == "fix_card" for r in h.board.reads)
    capsys.readouterr()

    row = ("The Desk loads in under two seconds", "Not met. 4 s. fix: DRE-7990")
    h, _ = _pass_7251(monkeypatch, thread=thread, head=head,
                      declined="2026-10-27T18:40:00Z", now=later,
                      text=record_text(D7251, MET_ROW, row))
    lines = _about(_lines(capsys), D7251)
    assert h.fired == []
    assert any("a set re-arms once" in line for line in lines), lines


def test_the_dry_run_would_dispatch_and_writes_nothing(monkeypatch, capsys):
    h, _ = _pass_7251(monkeypatch, live=False)
    assert h.fired == [] and h.posted == [] and h.holds == []
    assert f"would: dispatch {D7251} — {REARM_7251}" in _lines(capsys)


# --------------------------------------------------------------------------- #
# the two Linear reads                                                         #
# --------------------------------------------------------------------------- #


def test_the_proofs_relations_are_read_to_the_end_with_their_times(monkeypatch):
    pages = [{"pageInfo": {"hasNextPage": True, "endCursor": "c1"},
              "nodes": BUILD_7251},
             {"pageInfo": {"hasNextPage": False, "endCursor": "c2"},
              "nodes": FIXES_7251}]
    calls: list = []

    def gql(query, variables=None):
        calls.append(dict(variables or {}))
        assert "createdAt" in query and "inverseRelations" in query
        return {"issue": {"inverseRelations": pages[len(calls) - 1]}}

    monkeypatch.setattr(linear_ops, "gql", gql)
    got = proof_dispatch.LinearReads().blocking(D7251)
    assert got == BUILD_7251 + FIXES_7251
    assert [c.get("after") for c in calls] == [None, "c1"]


def test_relations_past_the_bound_are_unread(monkeypatch):
    def gql(query, variables=None):
        return {"issue": {"inverseRelations": {
            "pageInfo": {"hasNextPage": True, "endCursor": "more"}, "nodes": BUILD_7251}}}

    monkeypatch.setattr(linear_ops, "gql", gql)
    with pytest.raises(LookupError, match="not read to the end"):
        proof_dispatch.LinearReads().blocking(D7251)


def _lane_over(monkeypatch, cards, *, fail=None):
    import reconcile

    calls: list = []

    def gql(query, variables=None):
        calls.append((query, dict(variables or {})))
        if fail:
            raise fail
        numbers = set((variables or {}).get("numbers") or ())
        return {"issues": {"nodes": [
            {"identifier": c["identifier"],
             "inverseRelations": {"pageInfo": {"hasNextPage": False, "endCursor": None},
                                  "nodes": RELATIONS_7430}}
            for c in cards if int(c["identifier"].split("-")[1]) in numbers]}}

    monkeypatch.setattr(reconcile, "active_cards", lambda states: [
        c for c in cards if c["state"]["name"] in states])
    monkeypatch.setattr(linear_ops, "gql", gql)
    return calls


def test_the_lane_read_carries_relation_times_in_one_request_for_every_marked_card(
        monkeypatch):
    marked = [_review(D7251, THREAD_7251), _review(D7430, THREAD_7430)]
    plain = [_review("DRE-7767", SPENT_7430[:2])]
    calls = _lane_over(monkeypatch, marked + plain)
    got = proof_dispatch.LinearReads().lane("In Review")
    assert len(calls) == 1
    query, variables = calls[0]
    assert "createdAt" in query and sorted(variables["numbers"]) == [7251, 7430]
    by_id = {c["identifier"]: c for c in got}
    assert by_id[D7430]["inverseRelations"]["nodes"] == RELATIONS_7430
    assert "inverseRelations" not in by_id["DRE-7767"]

    calls = _lane_over(monkeypatch, plain)
    proof_dispatch.LinearReads().lane("In Review")
    assert calls == []


def test_a_lane_relation_read_that_fails_leaves_every_marked_card_unread(
        monkeypatch, capsys):
    marked = [_review(D7251, THREAD_7251)]
    _lane_over(monkeypatch, marked, fail=RuntimeError("Linear: 503"))
    [got] = proof_dispatch.LinearReads().lane("In Review")
    assert got["inverseRelations"]["pageInfo"]["hasNextPage"] is True
    out = capsys.readouterr().out
    assert D7251 in out and "503" in out


# --------------------------------------------------------------------------- #
# DRE-8250: an epic and a duplicate in the fix set — DRE-7430 on 10-28         #
# --------------------------------------------------------------------------- #

#: The newest receipt: the run DRE-8032's shipping re-armed on 10-26.
REARMED_7430 = run_receipt("2026-10-26T21:55:23Z", "2026-10-26 14:51 PT",
                           "re-run after DRE-8032 shipped", "re-armed")
#: That run amended the record; the critic approved the new head and the gate
#: declined it, and the budget's hold came back.
HEAD_1028 = "c4d5e6f" + "0" * 33
THREAD_1028 = SPENT_7430 + [
    REARMED_7430,
    at("⏳ 5/5 amended", "2026-10-26T22:40:00Z"),
    budget_hold("2026-10-26T23:46:00Z",
                f"{GATE_HELD[1]} — the re-armed run of 2026-10-26 14:51 PT was its "
                "event's one run"),
]
SPEED_ROW = ("The Desk loads in under two seconds",
             "Not met. 9 s at 02:31 PT. fix: DRE-8158")
#: Related before the newest receipt (adding nothing), then the two after it.
RELATIONS_1028 = RELATIONS_7430 + [relation("DRE-8158", "2026-10-27T18:35:45Z"),
                                   relation("DRE-8215", "2026-10-28T01:03:44Z")]
BUILD_8158 = ("DRE-8159", "DRE-8160", "DRE-8161")
PROOF_8158 = "DRE-8162"


def child(ident: str, state_name: str = "Done", *, title: str | None = None,
          labels=("repo:agent-bureau", "agent:engineer")) -> dict:
    """An epic's child as the fix card's read carries it — the shape
    `epic_cap.buildable()` reads."""
    return {"identifier": ident, "title": title or f"Desk speed: step {ident}",
            "state": {"name": state_name},
            "labels": {"nodes": [{"name": n} for n in labels],
                       "pageInfo": {"hasNextPage": False}},
            "children": {"nodes": []}}


def epic(ident: str, children: list, state_name: str = "Done") -> dict:
    return {"identifier": ident, "title": "Desk speed: the Desk under two seconds",
            "state": {"name": state_name},
            "labels": {"nodes": [{"name": "repo:agent-bureau"}, {"name": "agent:planner"}]},
            "children": {"nodes": children, "pageInfo": {"hasNextPage": False}},
            "relations": {"nodes": []}}


def duplicate(ident: str, of: str | None) -> dict:
    rels = [{"type": "duplicate", "relatedIssue": {"identifier": of}}] if of else []
    return {**fix(ident, "agent-bureau", "Duplicate"), "children": {"nodes": []},
            "relations": {"nodes": [{"type": "related",
                                     "relatedIssue": {"identifier": "DRE-7425"}}, *rels]}}


def epic_8158(**states) -> dict:
    """DRE-8158 on 10-28: its three build cards Done, its PROOF In Review."""
    kids = [child(c, states.get(c, "Done")) for c in BUILD_8158]
    kids.append(child(PROOF_8158, states.get(PROOF_8158, "In Review"),
                      title="PROOF: the Desk loads in under two seconds",
                      labels=("repo:agent-bureau", "agent:proof")))
    return epic("DRE-8158", kids)


CARDS_1028 = {"DRE-8158": epic_8158(), "DRE-8215": fix("DRE-8215", "agent-bureau"),
              "DRE-8032": fix("DRE-8032", "agent-bureau"),
              "DRE-7425": fix("DRE-7425", "agent-bureau")}
PRS_1028 = {**{(AB_REPO, c): merged(4200 + i, f"{c[-4:]}aaa" + "0" * 33, c)
               for i, c in enumerate(BUILD_8158)},
            (AB_REPO, "DRE-8215"): merged(4248, "7e95e69b0" + "0" * 31, "DRE-8215",
                                          "2026-10-28T15:02:11Z")}
CONSOLE_1028 = ("ready — console: newest agent-bureau-console-v1.6.641 carries #4248 "
                "(7e95e69) for DRE-8215")
#: agent-bureau Reconcile at 17:06:00Z on 10-28, an hour after the release.
NOW_1028 = _ts("2026-10-28T17:06:00Z")
REARM_1028 = "re-run after DRE-8158, DRE-8215 shipped"


def _pass_1028(monkeypatch, *, cards=None, prs=None, text=None, thread=None,
               relations=None, head=HEAD_1028, declined="2026-10-26T23:45:10Z",
               now=NOW_1028):
    thread = THREAD_1028 if thread is None else thread
    relations = RELATIONS_1028 if relations is None else relations
    board = Relations(review=[_review(D7430, thread, relations)],
                      threads={D7430: thread},
                      fixes=CARDS_1028 if cards is None else cards,
                      relations={D7430: relations})
    pr = record_pr(D7430, PR_7430, head, opened="2026-10-23T15:35:00Z",
                   approved="2026-10-26T23:40:00Z", declined=declined)
    h = Fleet(monkeypatch, board, prs=PRS_1028 if prs is None else prs,
              releases={AB_REPO: ("ready", [CONSOLE_1028])}, records={D7430: pr},
              texts={PR_7430: record_text(D7430, MET_ROW, SPEED_ROW)
                     if text is None else text},
              states={D7430: state("finished", dispatches=1,
                                   record={"number": PR_7430, "state": "open"})})
    tally = h.sweep(now=now)
    return h, tally


def test_dre_7430_replayed_on_10_28_an_epic_and_its_fix_re_arm_one_run(monkeypatch, capsys):
    """On `main` this pass printed `the fix DRE-8158's pull request is not
    merged` and dispatched nothing: DRE-8158 is an epic and has no pull
    request of its own. Built out, its three build cards merged and live, and
    DRE-8215 merged and live: one run, naming both."""
    h, tally = _pass_1028(monkeypatch)
    lines = _about(_lines(capsys), D7430)
    assert h.fired == [(D7430, AB_REPO, REARM_1028, "proof-execute")], lines
    assert tally.dispatched == 1
    assert _receipt_reason(h) == f"{REARM_1028} (re-armed)"
    # The epic's build cards and the second fix, each read for its pull
    # request; the epic itself never is, and both release on one reading.
    assert sorted(h.pr_reads) == sorted((AB_REPO, c) for c in (*BUILD_8158, "DRE-8215"))
    assert h.release_calls == [(AB_REPO, [*BUILD_8158, "DRE-8215"])]
    # One Linear read per fix card: the children ride the epic's own read.
    assert [r for r in h.board.reads if r[0] == "fix_card"] == [
        ("fix_card", "DRE-8158"), ("fix_card", "DRE-8215")]
    assert any("DRE-8158 is an epic" in line and all(c in line for c in BUILD_8158)
               for line in lines), lines


def test_an_epic_whose_only_open_child_is_its_proof_is_not_held_by_it(monkeypatch, capsys):
    """DRE-8162, the epic's own PROOF, is In Review: follow-on work, as the
    sweep reads it (DRE-7774). It holds nothing and is never read for a pull
    request — and the line names it as what carries on."""
    h, _ = _pass_1028(monkeypatch)
    lines = _about(_lines(capsys), D7430)
    assert h.fired and (AB_REPO, PROOF_8158) not in h.pr_reads
    assert not any("not built out" in line for line in lines), lines
    assert any("DRE-8158 is an epic" in line and PROOF_8158 in line for line in lines), lines


@pytest.mark.parametrize("held", BUILD_8158)
def test_an_epic_with_a_build_card_not_merged_re_arms_nothing_and_names_both(
        monkeypatch, capsys, held):
    prs = {**PRS_1028, (AB_REPO, held): {"number": 4210, "state": "OPEN",
                                          "headRefName": f"agent/{held}-fix"}}
    h, tally = _pass_1028(monkeypatch, prs=prs)
    lines = _about(_lines(capsys), D7430)
    assert h.fired == [] and h.posted == [] and tally.held == 1
    assert h.release_calls == []
    assert any(f"the fix DRE-8158 is an epic whose build card {held}'s pull request "
               "is not merged" in line for line in lines), lines


def test_an_epic_with_a_build_card_open_is_not_built_out(monkeypatch, capsys):
    cards = {**CARDS_1028, "DRE-8158": epic_8158(**{"DRE-8160": "In Progress"})}
    h, tally = _pass_1028(monkeypatch, cards=cards)
    lines = _about(_lines(capsys), D7430)
    assert h.fired == [] and tally.held == 1
    assert not any(ident in BUILD_8158 for _, ident in h.pr_reads)
    assert any("the fix DRE-8158 is an epic not built out" in line and "DRE-8160" in line
               for line in lines), lines


def test_an_epic_whose_children_were_not_read_to_the_end_is_not_built_out(
        monkeypatch, capsys):
    truncated = epic_8158()
    truncated["children"]["pageInfo"] = {"hasNextPage": True}
    h, _ = _pass_1028(monkeypatch, cards={**CARDS_1028, "DRE-8158": truncated})
    lines = _about(_lines(capsys), D7430)
    assert h.fired == []
    assert any("DRE-8158 is an epic not built out" in line for line in lines), lines


def test_an_epic_s_canceled_build_card_is_nothing_to_ship(monkeypatch, capsys):
    """A build card Canceled is closed with nothing merged, as the sweep
    reads it: it holds nothing, and no pull request is read for it."""
    cards = {**CARDS_1028, "DRE-8158": epic_8158(**{"DRE-8161": "Canceled"})}
    prs = {k: v for k, v in PRS_1028.items() if k != (AB_REPO, "DRE-8161")}
    h, _ = _pass_1028(monkeypatch, cards=cards, prs=prs)
    assert h.fired == [(D7430, AB_REPO, REARM_1028, "proof-execute")], _lines(capsys)
    assert (AB_REPO, "DRE-8161") not in h.pr_reads
    assert h.release_calls == [(AB_REPO, ["DRE-8159", "DRE-8160", "DRE-8215"])]


def test_an_epic_not_done_nor_in_progress_re_arms_nothing(monkeypatch, capsys):
    cards = {**CARDS_1028, "DRE-8158": {**epic_8158(), "state": {"name": "Canceled"}}}
    h, _ = _pass_1028(monkeypatch, cards=cards)
    lines = _about(_lines(capsys), D7430)
    assert h.fired == [] and h.pr_reads == []
    assert any("DRE-8158 is an epic" in line and "Canceled" in line for line in lines), lines


def test_an_epic_in_the_set_re_arms_once(monkeypatch, capsys):
    """The run DRE-8158 and DRE-8215 re-armed amended the record and the gate
    declined the new head. Both relations are now older than the newest
    receipt, and the record still names DRE-8158: a set re-arms once."""
    rearmed = run_receipt("2026-10-28T17:06:30Z", "2026-10-28 10:06 PT", REARM_1028,
                          "re-armed")
    thread = THREAD_1028 + [rearmed, at("⏳ 5/5 amended", "2026-10-28T17:50:00Z")]
    h, tally = _pass_1028(monkeypatch, thread=thread, head="d9e8f7a" + "0" * 33,
                          declined="2026-10-28T18:00:00Z", now=_ts("2026-10-28T18:21:00Z"))
    lines = _about(_lines(capsys), D7430)
    assert h.fired == [] and tally.held == 1
    assert not any(r[0] == "fix_card" for r in h.board.reads)
    assert any("DRE-8158 already re-armed" in line and "a set re-arms once" in line
               for line in lines), lines


DUP_ROW = ("The Desk loads in under two seconds",
           "Not met. 9 s at 02:31 PT. fix: DRE-8214")


def test_a_duplicate_is_read_as_its_canonical_card_and_re_arms(monkeypatch, capsys):
    cards = {**CARDS_1028, "DRE-8214": duplicate("DRE-8214", "DRE-8215")}
    h, _ = _pass_1028(monkeypatch, cards=cards, relations=RELATIONS_7430,
                      text=record_text(D7430, MET_ROW, DUP_ROW))
    lines = _about(_lines(capsys), D7430)
    assert h.fired == [(D7430, AB_REPO, "re-run after DRE-8214 shipped",
                        "proof-execute")], lines
    assert h.pr_reads == [(AB_REPO, "DRE-8215")]
    assert h.release_calls == [(AB_REPO, ["DRE-8215"])]
    assert [r for r in h.board.reads if r[0] == "fix_card"] == [
        ("fix_card", "DRE-8214"), ("fix_card", "DRE-8215")]
    assert any("DRE-8214" in line and "duplicate" in line and "DRE-8215" in line
               for line in lines), lines


def test_a_duplicate_whose_canonical_card_is_not_merged_names_both(monkeypatch, capsys):
    cards = {**CARDS_1028, "DRE-8214": duplicate("DRE-8214", "DRE-8215")}
    prs = {(AB_REPO, "DRE-8215"): {"number": 4248, "state": "OPEN",
                                   "headRefName": "agent/DRE-8215-fix"}}
    h, tally = _pass_1028(monkeypatch, cards=cards, prs=prs, relations=RELATIONS_7430,
                          text=record_text(D7430, MET_ROW, DUP_ROW))
    lines = _about(_lines(capsys), D7430)
    assert h.fired == [] and tally.held == 1 and h.release_calls == []
    assert any("the fix DRE-8214 is a duplicate whose canonical card DRE-8215's pull "
               "request is not merged" in line for line in lines), lines


@pytest.mark.parametrize("cards, why", [
    ({"DRE-8214": duplicate("DRE-8214", None)}, "no duplicate relation"),
    ({"DRE-8214": duplicate("DRE-8214", "DRE-8215"),
      "DRE-8215": duplicate("DRE-8215", "DRE-8032")}, "read one hop"),
    ({"DRE-8214": duplicate("DRE-8214", "DRE-8299")}, "could not be read"),
])
def test_a_duplicate_with_no_readable_canonical_card_refuses_and_names_it(
        monkeypatch, capsys, cards, why):
    h, tally = _pass_1028(monkeypatch, cards={**CARDS_1028, **cards},
                          relations=RELATIONS_7430,
                          text=record_text(D7430, MET_ROW, DUP_ROW))
    lines = _about(_lines(capsys), D7430)
    assert h.fired == [] and tally.held == 1 and h.pr_reads == []
    assert any("the fix DRE-8214" in line and why in line for line in lines), lines
    assert ("fix_card", "DRE-8032") not in h.board.reads


def test_the_fix_card_read_carries_children_and_duplicate_relations_in_one_request(
        monkeypatch):
    calls: list = []

    def gql(query, variables=None):
        calls.append(query)
        return {"issue": epic_8158()}

    monkeypatch.setattr(linear_ops, "gql", gql)
    got = proof_dispatch.LinearReads().fix_card("DRE-8158")
    assert got["identifier"] == "DRE-8158" and len(calls) == 1
    query = " ".join(calls[0].split())
    assert "children(" in query and "hasNextPage" in query and "labels" in query
    assert "relations(" in query and "relatedIssue { identifier }" in query
    assert "title" in query


def test_the_docstring_says_an_epic_and_a_duplicate_are_read_as_the_sweep_reads_them():
    doc = " ".join(proof_dispatch.__doc__.split())
    section = doc.split("## The re-arm, once the budget is spent (DRE-7740)")[1].split("## ")[0]
    assert "DRE-8250" in section and "built out" in section
    assert "`epic_cap.buildable()`" in section and "Duplicate" in section
    assert "canonical" in section


def test_the_docstring_says_a_blocker_is_a_named_fix_read_in_its_own_repo():
    doc = " ".join(proof_dispatch.__doc__.split())
    section = doc.split("## The re-arm, once the budget is spent (DRE-7740)")[1].split("## ")[0]
    assert "`blocks` relation" in section and "DRE-8156" in section
    assert "newest `🔬 proof-run` receipt" in section
    assert "config/repo-map.json" in section
    assert "its own repo" in section
    assert "neither source names a fix" in doc
