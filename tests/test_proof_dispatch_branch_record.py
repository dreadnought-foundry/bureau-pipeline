"""The proof dispatcher reads a waiting record from its branch (DRE-8440).

Once a waiting proof keeps no pull request open (DRE-8437, DRE-8441), its
record lives only on `agent/DRE-<n>-proof-record`. Before this card the
re-run paths read the record off an open pull request, so a waiting proof
never re-ran when its date passed: `rerunning` refused "no open record pull
request", and a Hand-work card whose run `finished` was refused at
condition 5 for good.

Pinned here, through the dispatcher's real sweep with injected reads, one
card across passes whose thread grows with every write. Its hold is the one
`proof_record_pr.decide` posts at the end of the run, through the real
`decide` over the same fake GitHub the dispatcher reads:

  * a Hand-work card and an In Review card whose record pull request was
    closed, each with a stamped waiting row, held before the time and given
    one timed re-run after it, `RERUN_BUDGET` unspent;
  * an unstamped waiting row's re-check twelve hours after its hold;
  * a record with a row that does not wait decided as DRE-8451 re-arms it,
    never refused (`tests/test_proof_dispatch_branch_rearm.py` has the rest);
  * a branch read that raised refused, nothing posted;
  * a branch that is gone, then a head with no record, each told once;
  * the spent-with-nothing-new shortcut over the branch's head;
  * the contract's names.

Run: cd bureau-pipeline && python3 -m pytest tests/test_proof_dispatch_branch_record.py -v
"""

from __future__ import annotations

import base64
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import linear_ops  # noqa: E402
import proof_dispatch  # noqa: E402
import proof_record  # noqa: E402
import proof_record_pr  # noqa: E402
import proof_release  # noqa: E402
import proof_run_state  # noqa: E402
import reconcile  # noqa: E402
from test_proof_dispatch import (  # noqa: E402
    REPO, SLUG, VIEWER, detail, fake_voices, lane_card, state,
)

CARD = "DRE-8470"
BRANCH = f"agent/{CARD}-proof-record"
RECORD = "docs/proof-dre-8470.md"
DESCRIPTION = f"Prove the Sunday report.\n\n**Files:** {RECORD}\n"
HEAD = "b7a9c3e" + "1" * 33
NEW_HEAD = "d4e5f60" + "2" * 33
PT = ZoneInfo("America/Los_Angeles")
NOT_FOUND = "gh api {path} failed rc=1: gh: Not Found (HTTP 404)"


def pt(d, h, mi) -> datetime:
    return datetime(2026, 10, d, h, mi, tzinfo=PT)


def _iso(when: datetime) -> str:
    return when.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


#: The first run: dispatched at 09:00 PT on 2026-10-20, finished at 09:40.
DISPATCHED = pt(20, 9, 0)
FINISHED = pt(20, 9, 40)
STAMP = "2026-10-21T06:30-07:00"
BEFORE = pt(21, 5, 30)
AFTER = pt(21, 6, 45)

MET = "| The sweep posts one receipt | Met. Seen at 09:05 PT |"
STAMPED = ("| The Sunday report lists every card | Not observed. waiting for the "
           f"Sunday report [not-before={STAMP}]: the report names each card |")
UNSTAMPED = ("| The Sunday report lists every card | Not observed. waiting for the "
             "Sunday report: the report names each card |")
PLAIN = "| The receipt is posted once | Not met. two receipts were posted |"
FIXED = "| The badge renders on the card | Not met. the badge is missing — fix: DRE-9001 |"
CLOSING = "| The CEO closes this card after reading the record | Open: the CEO's step |"


def record_md(*rows: str) -> str:
    return "\n".join([f"# PROOF record — {CARD}", "", "**Status: proving**", "",
                      "| Criterion | Result |", "|---|---|", MET, *rows, CLOSING, ""])


def receipt_body(at: datetime, reason: str, count: str) -> str:
    return (f"🔬 proof-run: dispatched a proof run at "
            f"{at.strftime('%Y-%m-%d %H:%M')} PT — {reason} ({count})")


class GitHub:
    """The repository as both readers see it: `decide`'s `gh` argv seam, and
    the dispatcher's `read(path)`, `gh api` parsed — the read seam the
    branch record goes through. `heads` maps a head sha to the record there
    (None: no record file at that head)."""

    def __init__(self, text, *, pulls=()):
        self.head = HEAD
        self.heads = {HEAD: text}
        self.branch = True
        self.pulls = [dict(p) for p in pulls]
        self.fail: Exception | None = None
        self.reads: list = []

    def api(self, path: str):
        self.reads.append(path)
        if self.fail is not None:
            raise self.fail
        if "/git/ref/heads/" in path:
            assert path == f"repos/{REPO}/git/ref/heads/{BRANCH}", path
            if not self.branch:
                raise reconcile.ReconcileReadError(NOT_FOUND.format(path=path))
            return {"ref": f"refs/heads/{BRANCH}", "object": {"sha": self.head}}
        if "/contents/" in path:
            ref = path.rsplit("?ref=", 1)[1]
            text = self.heads.get(ref)
            if text is None:
                raise reconcile.ReconcileReadError(NOT_FOUND.format(path=path))
            return {"sha": f"blob{ref[:7]}", "encoding": "base64",
                    "content": base64.b64encode(text.encode()).decode()}
        raise AssertionError(f"unexpected read {path}")

    def cli(self, args: list) -> str:
        args = list(args)
        if args[0] == "api":
            return json.dumps(self.api(args[1]))
        if args[:2] == ["pr", "list"]:
            wanted = args[args.index("--state") + 1]
            return json.dumps([{"number": p["number"], "state": p["state"],
                                "mergedAt": None} for p in self.pulls
                               if wanted == "all" or p["state"] == "OPEN"])
        if args[:2] == ["pr", "view"]:
            return json.dumps({"files": [{"path": RECORD, "changeType": "ADDED"}]})
        if args[:2] == ["pr", "close"]:
            for p in self.pulls:
                if str(p["number"]) == args[2]:
                    p["state"] = "CLOSED"
            return ""
        raise AssertionError(f"unexpected gh {args}")


class Card:
    """One PROOF card across passes. Every write lands on its thread, and the
    lane read's window is that thread."""

    def __init__(self, monkeypatch, lane: str, text, *, hold=True, pulled=None):
        self.lane = lane
        self.github = GitHub(text, pulls=[pulled] if pulled else ())
        self.run = "finished"
        self.fired: list = []
        self.reads: list = []
        self.now = DISPATCHED
        self.thread: list = []
        if lane == "Hand-work":
            self._post(CARD, "🧹 Auto-promoted Backlog → Hand-work: the proof is a "
                             "person's to confirm.", at=pt(20, 8, 50))
        self._post(CARD, receipt_body(DISPATCHED, "first proof run", "dispatch 1 of 2"))
        monkeypatch.setattr(linear_ops, "cmd_comment", self._post)
        monkeypatch.setattr(linear_ops, "cmd_proof_waiting", self._hold)
        monkeypatch.setattr(linear_ops, "cmd_proof_observed", self._observed)
        # The run's end: its heartbeat, then the hold `decide` posts for the
        # record at the branch head — closing the record's pull request first
        # when one is open.
        self.now = FINISHED
        self._post(CARD, "⏳ 5/5 record written")
        word = proof_record_pr.decide(
            REPO, CARD, f"https://linear.app/x/issue/{CARD}", "PROOF: the Sunday report",
            gh=self.github.cli, comment=self._hold, now=FINISHED, hold=hold,
            voices=lambda ident: fake_voices(self.thread, VIEWER, card=ident),
            description=DESCRIPTION)
        self.decided = word
        self.github.reads = []

    # -- the writes ------------------------------------------------------------ #

    def _post(self, ident, body, *flags, at=None):
        assert ident == CARD
        self.thread.append({"body": body, "createdAt": _iso(at or self.now),
                            "user": {"id": VIEWER}})

    def _hold(self, ident, observed, needs):
        self._post(ident, linear_ops.proof_waiting_line(observed, needs))

    def _observed(self, ident, text):
        self._post(ident, linear_ops.proof_observed_line(text))

    # -- the reads ------------------------------------------------------------- #

    def lane_read(self, lane):
        self.reads.append(("lane", lane))
        if lane != self.lane:
            return []
        card = lane_card(CARD, state=self.lane, window=list(self.thread))
        card["description"] = DESCRIPTION
        return [card]

    def card(self, ident):
        self.reads.append(("card", ident))
        return detail()

    def thread_of(self, ident):
        self.reads.append(("thread", ident))
        return list(self.thread), VIEWER

    def find_record(self, ident):
        self.reads.append(("record", ident))
        if not self.github.pulls:
            return None
        pr = self.github.pulls[-1]
        return {"number": pr["number"], "url": f"https://github.com/{REPO}/pull/{pr['number']}",
                "headRefName": BRANCH, "baseRefName": "main", "state": pr["state"],
                "headRefOid": self.github.head, "createdAt": _iso(DISPATCHED + timedelta(minutes=30)),
                "comments": [], "files": [{"path": RECORD, "changeType": "ADDED"}]}

    def fire(self, card, repo, *, reason=None, event=None):
        self.fired.append((card["identifier"], reason))
        return True, ""

    # -- one pass -------------------------------------------------------------- #

    def sweep(self, when: datetime, *, live=True, heads=None):
        self.now = when
        return proof_dispatch.sweep(
            REPO, SLUG, live=live, linear=_Linear(self), read=self.github.api,
            find_pr=lambda ident: None,
            run_state=lambda *a, **k: state(self.run, dispatches=1),
            release=lambda repo, merges, *, read: proof_release.Reading(
                "ready", ["ready — fixture"]),
            fire=self.fire, voices=fake_voices, now=when,
            find_record=self.find_record,
            read_record=lambda pr, head: proof_record.Record(RECORD, None, "no pull request"),
            open_heads=lambda: dict(heads or {}), file_fix=None)

    # -- what the thread says -------------------------------------------------- #

    def bodies(self) -> list:
        return [c["body"] for c in self.thread]

    def holds(self) -> list:
        return [b for b in self.bodies() if b.startswith(proof_record.HOLD_MARK)]

    def lifts(self) -> list:
        return [b for b in self.bodies() if b.startswith(proof_record.OBSERVED_MARK)]

    def receipts(self) -> list:
        return [r for b in self.bodies() if (r := proof_run_state.receipt(b))]

    def plain(self) -> list:
        return [b for b in self.bodies()
                if not b.startswith((proof_record.HOLD_MARK, proof_record.OBSERVED_MARK,
                                     "🧹", "🔬", "⏳"))]


class _Linear:
    def __init__(self, card):
        self.of = card

    def lane(self, lane):
        return self.of.lane_read(lane)

    def card(self, ident):
        return self.of.card(ident)

    def thread(self, ident):
        return self.of.thread_of(ident)

    def blocking(self, ident):
        return []

    def fix_card(self, ident):
        raise LookupError(ident)


def _lines(capsys) -> list:
    return [line for line in capsys.readouterr().out.splitlines() if CARD in line]


def _first(body: str) -> str:
    return body.split("\n", 1)[0]


def _closed_pull() -> dict:
    """The record's pull request, open until the run's `decide` closes it."""
    return {"number": 950, "state": "OPEN"}


def _card(monkeypatch, lane: str, text, **kw) -> Card:
    if lane == "In Review":
        kw.setdefault("pulled", _closed_pull())
    return Card(monkeypatch, lane, text, **kw)


LANES = ["Hand-work", "In Review"]


# --------------------------------------------------------------------------- #
# the contract                                                                 #
# --------------------------------------------------------------------------- #


def test_the_contract_names_are_the_modules():
    assert proof_dispatch.BRANCH_SOURCE == "branch"
    assert proof_dispatch.PULL_SOURCE == "pull"
    assert proof_dispatch.RecordSource._fields == (
        "kind", "number", "branch", "head", "text", "at")
    assert proof_dispatch.BRANCH_GONE_COMMENT == (
        "Proof not re-run — no record can be read on {branch}: {what}.")
    # DRE-8451 deleted the refusal of a record whose rows do not all wait.
    assert not hasattr(proof_dispatch, "BRANCH_NOT_YET")
    assert proof_dispatch.BRANCH_RERUN_REASON == "re-run of the record on its branch at {sha7}"


def test_the_waiting_rows_readers_are_proof_record_prs_under_the_old_names():
    """The dispatcher's private copies are gone; the old names stay bound."""
    assert proof_dispatch.WAITING_RECHECK_HOURS is proof_record_pr.WAITING_RECHECK_HOURS
    assert proof_dispatch.OPERATOR_PRESS is proof_record_pr.OPERATOR_PRESS
    assert proof_dispatch._waiting_rows is proof_record_pr.waiting_rows
    assert proof_dispatch._waiting_observed is proof_record_pr.waiting_observed
    assert proof_dispatch._not_before is proof_record_pr.not_before
    assert proof_dispatch._waiting_criterion is proof_record_pr.waiting_criterion
    source = (ROOT / "scripts" / "proof_dispatch.py").read_text(encoding="utf-8")
    for name in ("_waiting_rows", "_waiting_observed", "_not_before", "_waiting_criterion"):
        assert f"def {name}(" not in source, name


def test_the_hold_decide_posts_for_every_row_waiting_is_the_dispatchers_own(monkeypatch):
    card = _card(monkeypatch, "Hand-work", record_md(STAMPED))
    assert card.decided == proof_record_pr.HELD
    blocking = proof_record_pr.can_merge(record_md(STAMPED)).blocking
    assert card.holds() == [linear_ops.proof_waiting_line(
        *proof_record_pr.hold_line(blocking, FINISHED))]
    assert card.holds()[0].endswith(f"— needs not-before={STAMP}")


# --------------------------------------------------------------------------- #
# a stamped waiting row: held before its time, one timed re-run after          #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("lane", LANES)
def test_a_stamped_waiting_row_on_the_branch_is_held_then_given_one_timed_re_run(
        monkeypatch, capsys, lane):
    card = _card(monkeypatch, lane, record_md(STAMPED))
    (held,) = card.holds()
    if lane == "In Review":
        assert card.decided == proof_record_pr.CLOSED_AND_HELD
        assert card.github.pulls[0]["state"] == "CLOSED"
    count = len(card.thread)

    # Before the time: held, nothing written, the line says when it lifts.
    tally = card.sweep(BEFORE)
    lines = _lines(capsys)
    assert card.fired == [] and len(card.thread) == count
    assert tally.held == 1, lines
    assert any(f"held by {held} — lifts at 2026-10-21 06:30 PT" in line
               for line in lines), lines

    # After it: one lift, one timed re-run at the branch head.
    tally = card.sweep(AFTER)
    lines = _lines(capsys)
    reason = f"timed re-run after not-before 2026-10-21 06:30 PT at {HEAD[:7]}"
    assert card.fired == [(CARD, reason)], lines
    (lift,) = card.lifts()
    assert f"not-before={STAMP} has passed" in lift
    assert proof_dispatch.LIFTED_BY in lift
    newest = card.receipts()[-1]
    assert (newest.reason, newest.count) == (reason, "timed re-run 1 of 2")
    # RERUN_BUDGET is not spent: no receipt counts `re-run`.
    assert not any(r.count.startswith("re-run") for r in card.receipts())
    assert tally.dispatched == 1
    assert card.holds() == [held] and card.plain() == []


@pytest.mark.parametrize("lane", LANES)
def test_a_run_in_flight_still_holds_a_branch_record(monkeypatch, capsys, lane):
    card = _card(monkeypatch, lane, record_md(STAMPED))
    card.run = "running"
    count = len(card.thread)
    tally = card.sweep(AFTER)
    lines = _lines(capsys)
    assert card.fired == [], lines
    assert tally.running == 1, lines
    # The time hold is lifted first, as the existing rules lift it; nothing
    # else is written.
    assert len(card.thread) - count == 1 and len(card.lifts()) == 1


def test_the_branch_budgets_are_counted_since_the_first_proof_run_receipt(
        monkeypatch, capsys):
    """No pull request opened: the timed budget is every timed re-run since
    the card's first receipt, so a third is never sent."""
    card = _card(monkeypatch, "In Review", record_md(STAMPED))
    hold_line = card.holds()[0]
    card.thread = [c for c in card.thread if not c["body"].startswith(proof_record.HOLD_MARK)]
    for n, hour in ((1, 11), (2, 13)):
        card.now = pt(20, hour, 0)
        card._post(CARD, receipt_body(card.now, "timed re-run after not-before "
                                                f"2026-10-20 {hour - 1}:00 PT at {HEAD[:7]}",
                                      f"timed re-run {n} of 2"))
        card.now = pt(20, hour, 30)
        card._post(CARD, "⏳ 5/5 record amended")
    card.now = pt(20, 14, 0)
    card._post(CARD, hold_line)
    card.sweep(AFTER)
    lines = _lines(capsys)
    assert card.fired == [], lines
    assert card.holds()[-1] == linear_ops.proof_waiting_line(
        proof_dispatch.TIMED_EXHAUSTED_OBSERVED, proof_dispatch.TIMED_EXHAUSTED_NEEDS)
    assert any(f"2 timed re-runs on {BRANCH}" in line for line in lines), lines


# --------------------------------------------------------------------------- #
# an unstamped waiting row: the re-check twelve hours after its hold            #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("lane", LANES)
def test_an_unstamped_waiting_row_on_the_branch_is_re_checked_twelve_hours_after(
        monkeypatch, capsys, lane):
    card = _card(monkeypatch, lane, record_md(UNSTAMPED))
    (held,) = card.holds()
    recheck = FINISHED + timedelta(hours=proof_dispatch.WAITING_RECHECK_HOURS)
    assert proof_record.time_only(held) == recheck

    card.sweep(recheck - timedelta(minutes=15))
    assert card.fired == [] and card.lifts() == []

    tally = card.sweep(recheck + timedelta(minutes=15))
    lines = _lines(capsys)
    reason = f"re-check of the waiting rows at {HEAD[:7]}"
    assert card.fired == [(CARD, reason)], lines
    assert len(card.lifts()) == 1
    newest = card.receipts()[-1]
    assert (newest.reason, newest.count) == (reason, "re-check")
    assert tally.dispatched == 1


# --------------------------------------------------------------------------- #
# a row that does not wait: decided, never refused (DRE-8451)                  #
# --------------------------------------------------------------------------- #

NOT_YET = {
    "plain": record_md(PLAIN),
    "fix": record_md(FIXED),
    "beside-a-waiting-row": record_md(STAMPED, PLAIN),
}


@pytest.mark.parametrize("lane", LANES)
@pytest.mark.parametrize("case", sorted(NOT_YET))
def test_a_branch_record_with_a_row_that_does_not_wait_is_decided_not_refused(
        monkeypatch, capsys, lane, case):
    # The record's own hold, as the run's `decide` posts it, stands in both
    # lanes; DRE-8451 reads past it once the record is read.
    card = _card(monkeypatch, lane, NOT_YET[case])
    assert proof_record_pr.is_record_hold(card.holds()[-1])
    count = len(card.thread)
    card.sweep(AFTER)
    lines = _lines(capsys)
    assert not any("DRE-8451's" in line for line in lines), lines
    if case == "plain":
        # Plain `Not met.` rows: the re-run of the record on its branch.
        reason = f"re-run of the record on its branch at {HEAD[:7]}"
        assert card.fired == [(CARD, reason)], lines
        newest = card.receipts()[-1]
        assert (newest.reason, newest.count) == (reason, "re-run 1 of 2")
    elif case == "fix":
        # A fix named: not dispatched until it ships, nothing written.
        assert card.fired == [] and len(card.thread) == count, card.bodies()[count:]
        assert any("DRE-9001" in line for line in lines), lines
    else:
        # A waiting row beside it: held on the waiting row's time.
        assert card.fired == [], lines
        (held,) = card.bodies()[count:]
        assert proof_record.time_only(held) is not None


# --------------------------------------------------------------------------- #
# a read that raised: refused as unread, nothing posted                        #
# --------------------------------------------------------------------------- #

RAISED = {
    "the-ref": lambda gh: setattr(gh, "fail", reconcile.ReconcileReadError(
        "gh api failed rc=1: HTTP 502: Bad Gateway")),
    "rate-limited": lambda gh: setattr(gh, "fail", reconcile.ReconcileRateLimited(
        "gh api failed rc=1: API rate limit exceeded")),
}


@pytest.mark.parametrize("lane", LANES)
@pytest.mark.parametrize("case", sorted(RAISED))
def test_a_branch_read_that_raised_is_refused_and_posts_nothing(
        monkeypatch, capsys, lane, case):
    card = _card(monkeypatch, lane, record_md(STAMPED))
    # Past the time, so nothing but the read stands between it and a re-run.
    card.thread = [c for c in card.thread if not c["body"].startswith(proof_record.HOLD_MARK)]
    RAISED[case](card.github)
    count = len(card.thread)
    tally = card.sweep(AFTER)
    lines = _lines(capsys)
    assert card.fired == [] and len(card.thread) == count, card.bodies()[count:]
    assert tally.dispatched == 0
    assert card.github.reads, "the branch was read"
    assert any(BRANCH in line and "could not be read" in line for line in lines), lines


# --------------------------------------------------------------------------- #
# a branch that is gone, or a head with no record: told once per state         #
# --------------------------------------------------------------------------- #

REST = ("The card's 🔬 proof-waiting hold still says what the proof waited on. "
        "Nothing re-runs it until the branch carries its record again; the "
        "sweep's held-proof alarm reports the hold to the operator once it has "
        "stood past its due time.")


def test_a_gone_branch_and_then_a_head_with_no_record_are_each_told_once(
        monkeypatch, capsys):
    card = _card(monkeypatch, "In Review", record_md(STAMPED))
    assert card.holds() and card.github.pulls[0]["state"] == "CLOSED"
    card.github.branch = False
    gone = proof_dispatch.BRANCH_GONE_COMMENT.format(branch=BRANCH, what="the branch is gone")

    for n in range(3):
        card.sweep(BEFORE + timedelta(minutes=15 * n))
        assert card.fired == []
        assert card.plain() == [f"{gone}\n\n{REST}"], (n, card.plain())
    lines = _lines(capsys)
    assert any(gone in line for line in lines), lines

    # The branch comes back at a head with no record file.
    card.github.branch = True
    card.github.head = NEW_HEAD
    card.github.heads = {NEW_HEAD: None}
    empty = proof_dispatch.BRANCH_GONE_COMMENT.format(
        branch=BRANCH, what=f"its head {NEW_HEAD[:7]} carries no record")
    card.sweep(AFTER)
    assert card.fired == []
    assert [_first(b) for b in card.plain()] == [gone, empty]
    assert card.plain()[-1] == f"{empty}\n\n{REST}"
    card.sweep(AFTER + timedelta(minutes=15))
    assert card.fired == [] and len(card.plain()) == 2


def test_a_gone_branch_in_the_dry_run_says_would_comment_and_writes_nothing(
        monkeypatch, capsys):
    card = _card(monkeypatch, "In Review", record_md(STAMPED))
    card.github.branch = False
    count = len(card.thread)
    card.sweep(BEFORE, live=False)
    out = capsys.readouterr().out
    assert len(card.thread) == count and card.fired == []
    assert f"would: comment {CARD} — " + proof_dispatch.BRANCH_GONE_COMMENT.format(
        branch=BRANCH, what="the branch is gone") in out


def test_a_hand_work_card_with_no_record_branch_is_refused_at_condition_5_as_before(
        monkeypatch, capsys):
    card = _card(monkeypatch, "Hand-work", record_md(STAMPED))
    card.thread = [c for c in card.thread if not c["body"].startswith(proof_record.HOLD_MARK)]
    card.github.branch = False
    count = len(card.thread)
    card.sweep(AFTER)
    lines = _lines(capsys)
    assert card.fired == [] and len(card.thread) == count
    assert any("condition 5" in line for line in lines), lines


# --------------------------------------------------------------------------- #
# the record source                                                            #
# --------------------------------------------------------------------------- #


def _pass(card: Card, when: datetime):
    return proof_dispatch._Pass(
        REPO, SLUG, live=True, linear=_Linear(card), read=card.github.api,
        find_pr=lambda ident: None, run_state=lambda *a, **k: state("finished"),
        release=None, fire=card.fire, voices=fake_voices, now=when,
        find_record=card.find_record, read_record=lambda pr, head: None)


def test_record_source_reads_the_branch_at_its_head_when_no_pull_request_is_open(
        monkeypatch):
    card = _card(monkeypatch, "In Review", record_md(STAMPED))
    lane = card.lane_read("In Review")[0]
    got = _pass(card, AFTER)._record_source(lane)
    assert got.kind == proof_dispatch.BRANCH_SOURCE
    assert (got.number, got.branch, got.head) == (None, BRANCH, HEAD)
    assert got.text == record_md(STAMPED)
    assert got.at == DISPATCHED


def test_record_source_reads_the_open_pull_request_as_today(monkeypatch):
    card = _card(monkeypatch, "In Review", record_md(PLAIN), hold=False)
    card.github.pulls[0]["state"] = "OPEN"
    lane = card.lane_read("In Review")[0]
    got = _pass(card, AFTER)._record_source(lane)
    assert (got.kind, got.number, got.branch, got.head) == (
        proof_dispatch.PULL_SOURCE, 950, BRANCH, HEAD)
    assert card.github.reads == []


def test_record_source_raises_unread_when_the_branch_read_raised(monkeypatch):
    card = _card(monkeypatch, "In Review", record_md(STAMPED))
    card.github.fail = reconcile.ReconcileReadError("HTTP 502")
    lane = card.lane_read("In Review")[0]
    with pytest.raises(proof_dispatch._Refused):
        _pass(card, AFTER)._record_source(lane)


# --------------------------------------------------------------------------- #
# DRE-7787 over the branch's head                                              #
# --------------------------------------------------------------------------- #


def _spent(card: Card, at: datetime) -> str:
    line = linear_ops.proof_waiting_line(
        proof_dispatch.GATE_EXHAUSTED_OBSERVED,
        proof_dispatch.GATE_EXHAUSTED_NEEDS
        + proof_dispatch.SPENT_AT.format(sha7=HEAD[:7]))
    card.now = at
    card._post(CARD, line)
    return line


def test_a_spent_branch_record_at_its_marked_head_costs_no_candidate_read(
        monkeypatch, capsys):
    card = _card(monkeypatch, "In Review", record_md(STAMPED))
    _spent(card, pt(20, 11, 0))
    card.reads = []
    tally = card.sweep(AFTER)
    lines = _lines(capsys)
    assert tally.held == 1 and card.fired == []
    assert ("thread", CARD) not in card.reads and ("record", CARD) not in card.reads
    assert card.github.reads == [f"repos/{REPO}/git/ref/heads/{BRANCH}"]
    assert any(proof_dispatch.SPENT_NAMED in line and HEAD[:7] in line
               for line in lines), lines

    # A new head on the branch: a candidate again.
    card.github.head = NEW_HEAD
    card.github.heads[NEW_HEAD] = record_md(STAMPED)
    card.reads = []
    card.sweep(AFTER + timedelta(minutes=15))
    assert ("record", CARD) in card.reads
