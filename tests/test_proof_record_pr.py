"""One reader decides whether a proof record may have a pull request (DRE-8437).

`scripts/proof_record_pr.py` reads the record on `agent/<card>-proof-record`
at its head, judges it as the merge gate would (`can_merge`), and opens,
reopens or keeps its pull request when it can merge — or closes the open one,
keeping the branch, and posts the card's one `🔬 proof-waiting` hold when it
cannot. `sweep` closes every open record pull request whose record cannot
merge. These tests drive both through fake `gh`, `comment` and `voices`
seams, and compare the all-waiting hold with what the proof dispatcher writes
today for the same rows.

Run: cd bureau-pipeline && python3 -m pytest tests/test_proof_record_pr.py -v
"""
from __future__ import annotations

import base64
import json
import os
import subprocess  # nosec B404 — fixed argv, our own script
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
# `proof_dispatch` imports `reconcile`, which reads these at import.
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")
os.environ.setdefault("GH_TOKEN", "x")

import linear_ops  # noqa: E402
import proof_cause_park  # noqa: E402
import proof_dispatch  # noqa: E402
import proof_reach  # noqa: E402
import proof_record  # noqa: E402
import proof_record_pr as prp  # noqa: E402
import spoken_thread  # noqa: E402

SCRIPT = ROOT / "scripts" / "proof_record_pr.py"
REPO = "dreadnought-foundry/agent-bureau"
CARD = "DRE-7001"
CARD_URL = "https://linear.app/dreadnoughtfoundry/issue/DRE-7001/proof"
TITLE = "PROOF: the Sunday report lists every card"
BRANCH = f"agent/{CARD}-proof-record"
PATH = "docs/sunday-report-proof.md"
DESCRIPTION = f"Prove it.\n\n**Files:** {PATH}\n"
HEAD = "c0ffee" * 6 + "abcd"
NOW = datetime(2026, 10, 15, 9, 0, tzinfo=timezone(timedelta(hours=-7)))

STAMPED = "2026-10-18T06:30-07:00"
WAITING_ROW = ("| The Sunday report lists every card | Not observed. waiting for the "
               f"Sunday report [not-before={STAMPED}]: the report names each card |")
MET_ROW = "| The sweep posts one receipt | Met. Seen at 09:00 PT |"
CLOSING_ROW = "| Merged to main | pending |"


def record(*rows: str) -> str:
    return "\n".join(["# Proof — DRE-7001", "", "**Status: proving**", "",
                      "| Criterion | Result |", "| --- | --- |", *rows, CLOSING_ROW, ""])


WAITING = record(WAITING_ROW, MET_ROW)
MET = record(MET_ROW, "| The console shows the badge | Met. Seen at 09:05 PT |")
GAP = record(MET_ROW, "| The nightly reads the repo variable | Not observed. cause: "
                      "out-of-reach — `gh variable get` answered 403 |")
WORDING = record(MET_ROW, "| The badge is green | Not met. wording-only: the badge is "
                          "teal, which the criterion calls green |")
MIXED = record(
    WAITING_ROW,
    "| The badge renders on the card | Not met. the badge is missing — fix: DRE-9001 |",
    "| The receipt is posted once | Not met. two receipts were posted |",
    MET_ROW,
)
ALL_WAITING = record(
    WAITING_ROW,
    "| The operator's switch is on | Not observed. waiting for the operator's press: "
    "the switch reads on |",
)


# --------------------------------------------------------------------------- #
# the seams                                                                    #
# --------------------------------------------------------------------------- #

def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


class FakeGH:
    """`gh` for one repository. `pulls` are the record pull requests, each
    `{number, state, mergedAt, headRefName, headRefOid}`; `records` maps a
    head sha to the record's text there (None: the read fails)."""

    def __init__(self, records: dict, pulls=(), *, branches=(BRANCH,), reopen_fails=False):
        self.records = records
        self.pulls = [dict(p) for p in pulls]
        self.branches = set(branches)
        self.reopen_fails = reopen_fails
        self.calls: list = []

    def writes(self) -> list:
        return [c for c in self.calls if c[0] == "pr" and c[1] in
                ("create", "reopen", "close", "edit", "merge")]

    def __call__(self, args: list) -> str:
        args = list(args)
        self.calls.append(args)
        if args[0] == "api" and "/git/ref/heads/" in args[1]:
            branch = args[1].split("/git/ref/heads/", 1)[1]
            if branch not in self.branches:
                raise RuntimeError("HTTP 404: Not Found")
            heads = [p["headRefOid"] for p in self.pulls if p["headRefName"] == branch]
            return json.dumps({"object": {"sha": heads[-1] if heads else HEAD}})
        if args[0] == "api" and "/contents/" in args[1]:
            ref = args[1].rsplit("?ref=", 1)[1]
            text = self.records.get(ref)
            if text is None:
                raise RuntimeError("HTTP 404: Not Found")
            return json.dumps({"sha": f"blob-{ref[:6]}", "encoding": "base64",
                               "content": _b64(text)})
        if args[:2] == ["pr", "list"]:
            state = args[args.index("--state") + 1]
            pulls = self.pulls
            if "--head" in args:
                pulls = [p for p in pulls if p["headRefName"] == args[args.index("--head") + 1]]
            if state == "open":
                pulls = [p for p in pulls if p["state"] == "OPEN"]
            return json.dumps(sorted(pulls, key=lambda p: -p["number"]))
        if args[:2] == ["pr", "view"]:
            return json.dumps({"files": [{"path": PATH, "changeType": "ADDED"}]})
        if args[:2] == ["pr", "create"]:
            return f"https://github.com/{REPO}/pull/99\n"
        if args[:2] == ["pr", "reopen"]:
            if self.reopen_fails:
                raise RuntimeError("GraphQL: Could not reopen the pull request")
            return ""
        if args[:2] == ["pr", "close"]:
            return ""
        raise AssertionError(f"unexpected gh call: {args}")


def pull(number: int, state: str, *, branch: str = BRANCH, head: str = HEAD,
         merged: bool = False) -> dict:
    return {"number": number, "state": state, "headRefName": branch, "headRefOid": head,
            "mergedAt": "2026-10-14T10:00:00Z" if merged else None}


class Thread:
    """The card threads: `comment` posts a hold on one, `voices` reads it."""

    def __init__(self):
        self.posted: dict = {}

    def comment(self, card: str, observed: str, needs: str) -> None:
        self.posted.setdefault(card, []).append(linear_ops.proof_waiting_line(observed, needs))

    def voices(self, card: str) -> list:
        return [spoken_thread.Voice(spoken_thread.PIPELINE, "the pipeline",
                                    "2026-10-15T16:00:00Z", line)
                for line in self.posted.get(card, [])]

    def holds(self, card: str = CARD) -> list:
        return self.posted.get(card, [])


def decide(gh: FakeGH, thread: Thread, **kw) -> str:
    return prp.decide(REPO, CARD, CARD_URL, TITLE, gh=gh, comment=thread.comment,
                      voices=thread.voices, now=NOW, description=DESCRIPTION, **kw)


def _needs(line: str) -> str:
    return line.rpartition("— needs")[2].strip()


# --------------------------------------------------------------------------- #
# decide                                                                       #
# --------------------------------------------------------------------------- #

def test_a_waiting_record_with_no_pull_request_is_held(capsys):
    gh, thread = FakeGH({HEAD: WAITING}), Thread()
    assert decide(gh, thread) == "held"
    assert gh.writes() == []
    assert len(thread.holds()) == 1
    assert _needs(thread.holds()[0]) == f"not-before={STAMPED}"
    assert f"{prp.TAG} held {CARD} — " in capsys.readouterr().out


def test_a_waiting_record_with_an_open_pull_request_is_closed_and_held_once():
    gh, thread = FakeGH({HEAD: WAITING}, [pull(41, "OPEN")]), Thread()
    assert decide(gh, thread) == "closed-and-held"
    closes = [c for c in gh.writes() if c[1] == "close"]
    assert len(closes) == 1 and gh.writes() == closes
    close = closes[0]
    assert "41" in close and "--delete-branch" not in close
    body = close[close.index("--comment") + 1]
    assert body == prp.CLOSE_COMMENT.format(branch=BRANCH, n=1, card_url=CARD_URL)
    assert body.startswith("Closed while the proof waits — the record on "
                           f"{BRANCH} cannot merge yet: 1 row(s) wait.")
    assert len(thread.holds()) == 1

    gh.pulls[0]["state"] = "CLOSED"
    assert decide(gh, thread) == "held"
    assert len(thread.holds()) == 1, "the same hold is never posted twice"


def test_no_hold_is_posted_when_hold_is_false():
    gh, thread = FakeGH({HEAD: WAITING}, [pull(41, "OPEN")]), Thread()
    assert decide(gh, thread, hold=False) == "closed-and-held"
    assert thread.holds() == []


def test_a_met_record_reopens_a_closed_unmerged_pull_request():
    gh, thread = FakeGH({HEAD: MET}, [pull(41, "CLOSED")]), Thread()
    assert decide(gh, thread) == "reopened"
    assert gh.writes() == [["pr", "reopen", "41", "--repo", REPO]]
    assert thread.holds() == []


def test_a_refused_reopen_opens_a_new_pull_request():
    gh, thread = FakeGH({HEAD: MET}, [pull(41, "CLOSED")], reopen_fails=True), Thread()
    assert decide(gh, thread) == "opened"
    assert [c[1] for c in gh.writes()] == ["reopen", "create"]


def test_a_met_record_after_a_merged_pull_request_opens_a_new_one():
    gh, thread = FakeGH({HEAD: MET}, [pull(41, "MERGED", merged=True)]), Thread()
    assert decide(gh, thread) == "opened"
    assert [c[1] for c in gh.writes()] == ["create"]


@pytest.mark.parametrize("text", [MET, GAP, WORDING], ids=["met", "gap", "wording-only"])
def test_a_record_that_can_merge_with_no_pull_request_opens_one(text):
    gh, thread = FakeGH({HEAD: text}), Thread()
    assert decide(gh, thread) == "opened"
    (create,) = gh.writes()
    assert create[:2] == ["pr", "create"]
    assert create[create.index("--head") + 1] == BRANCH
    assert create[create.index("--title") + 1] == f"PROOF record: {TITLE}"
    assert CARD_URL in create[create.index("--body") + 1]
    assert thread.holds() == []


def test_a_record_that_can_merge_keeps_its_open_pull_request():
    gh, thread = FakeGH({HEAD: MET}, [pull(41, "OPEN")]), Thread()
    assert decide(gh, thread) == "kept-open"
    assert gh.writes() == []


def test_no_branch_is_no_record_and_writes_nothing():
    gh, thread = FakeGH({HEAD: WAITING}, branches=()), Thread()
    assert decide(gh, thread) == "no-record"
    assert gh.writes() == [] and thread.holds() == []


def test_a_record_that_cannot_be_read_is_no_record():
    gh, thread = FakeGH({}, [pull(41, "OPEN")]), Thread()
    assert decide(gh, thread) == "no-record"
    assert gh.writes() == [] and thread.holds() == []


# --------------------------------------------------------------------------- #
# can_merge                                                                    #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("text", [MET, GAP, WORDING], ids=["met", "gap", "wording-only"])
def test_can_merge_lets_met_gap_and_wording_only_rows_through(text):
    verdict = prp.can_merge(text)
    assert verdict.ok is True and verdict.blocking == []


def test_can_merge_lists_every_blocking_row():
    verdict = prp.can_merge(MIXED)
    assert verdict.ok is False
    assert [c for c, _ in verdict.blocking] == [
        "The Sunday report lists every card", "The badge renders on the card",
        "The receipt is posted once"]


@pytest.mark.parametrize("text", [None, "# Proof\n\nno table here\n", record()],
                         ids=["no text", "no table", "no judged row"])
def test_can_merge_refuses_a_record_it_cannot_judge(text):
    verdict = prp.can_merge(text)
    assert verdict.ok is False and verdict.blocking == [] and verdict.why


# --------------------------------------------------------------------------- #
# the hold                                                                     #
# --------------------------------------------------------------------------- #

def test_the_mixed_hold_names_what_each_row_waits_on():
    observed, needs = prp.hold_line(prp.can_merge(MIXED).blocking, NOW)
    assert observed.startswith(f"{prp.RECORD_HOLD_OPENING} ")
    for criterion in ("The Sunday report lists every card", "The badge renders on the card",
                      "The receipt is posted once"):
        assert criterion in observed
    parts = needs.split("; ")
    assert parts == ["the Sunday report (not before 2026-10-18 06:30 PT)",
                     "DRE-9001 shipped", "a re-run"]


def _dispatcher_hold(waiting: list, now: datetime) -> tuple:
    """What `proof_dispatch._Pass._hold_waiting` posts for these rows now."""
    posted = []

    class _Self:
        pass

    me = _Self()
    me.now = now
    me._hold = lambda ident, observed, needs: posted.append((observed, needs))
    with pytest.raises(proof_dispatch._Refused):
        proof_dispatch._Pass._hold_waiting(me, CARD, waiting, HEAD)
    (hold,) = posted
    return hold


UNSTAMPED = record("| The operator's switch is on | Not observed. waiting for the operator's "
                   "press: the switch reads on |")


@pytest.mark.parametrize("text", [ALL_WAITING, WAITING, UNSTAMPED],
                         ids=["mixed stamps", "stamped", "unstamped"])
def test_the_all_waiting_hold_is_the_dispatchers(text):
    blocking = prp.can_merge(text).blocking
    waiting = proof_dispatch._waiting_rows(blocking)
    assert len(waiting) == len(blocking)
    assert prp.hold_line(blocking, NOW) == _dispatcher_hold(waiting, NOW)
    assert not prp.hold_line(blocking, NOW)[0].startswith(prp.RECORD_HOLD_OPENING)


def test_a_ceo_press_row_keeps_the_prose_needs_half():
    text = record("| The CEO approves | Not observed. waiting for the CEO's press: the "
                  "approval |")
    blocking = prp.can_merge(text).blocking
    assert prp.hold_line(blocking, NOW) == _dispatcher_hold(
        proof_dispatch._waiting_rows(blocking), NOW)
    assert prp.hold_line(blocking, NOW)[1] == "the CEO's press"


def test_the_waiting_helpers_are_the_dispatchers():
    unmet = proof_record.reading(ALL_WAITING).unmet
    assert prp.waiting_rows(unmet) == proof_dispatch._waiting_rows(unmet)
    waiting = prp.waiting_rows(unmet)
    assert prp.waiting_observed(waiting) == proof_dispatch._waiting_observed(waiting)
    when = datetime(2026, 10, 18, 6, 30, 15, tzinfo=timezone.utc)
    assert prp.not_before(when) == proof_dispatch._not_before(when)
    assert prp.not_before(NOW) == proof_dispatch._not_before(NOW)
    for criterion, event in (("the CEO's press switch", "the operator's press"),
                             ("the CEO's press switch", "the report")):
        assert prp.waiting_criterion(criterion, event) == \
            proof_dispatch._waiting_criterion(criterion, event)
    assert prp.WAITING_RECHECK_HOURS == proof_dispatch.WAITING_RECHECK_HOURS == 12
    assert prp.OPERATOR_PRESS == proof_dispatch.OPERATOR_PRESS


def test_is_record_hold_reads_only_the_record_hold(monkeypatch):
    mixed = linear_ops.proof_waiting_line(*prp.hold_line(prp.can_merge(MIXED).blocking, NOW))
    assert prp.is_record_hold(mixed) is True

    waiting = linear_ops.proof_waiting_line(
        *prp.hold_line(prp.can_merge(ALL_WAITING).blocking, NOW))
    assert prp.is_record_hold(waiting) is False

    posted = []
    monkeypatch.setattr(linear_ops, "cmd_comment", lambda ident, body, *f: posted.append(body))
    linear_ops.cmd_proof_waiting_until(CARD, "the Sunday report", STAMPED)
    time_only = posted[0].split("\n", 1)[0]
    assert proof_record.time_only(time_only) is not None
    assert prp.is_record_hold(time_only) is False

    cause = linear_ops.proof_waiting_line(*proof_cause_park.hold(
        [proof_cause_park.CauseRow("The nightly reads the variable", "out-of-reach",
                                   "403")]))
    assert proof_cause_park.is_cause_hold(cause)
    assert prp.is_record_hold(cause) is False

    for opening in proof_dispatch.BUDGET_HOLDS:
        assert prp.is_record_hold(opening + proof_dispatch.RERUN_EXHAUSTED_NEEDS) is False


# --------------------------------------------------------------------------- #
# sweep                                                                        #
# --------------------------------------------------------------------------- #

def _three():
    met, waiting, unread = "a" * 40, "b" * 40, "c" * 40
    pulls = [pull(11, "OPEN", branch="agent/DRE-7011-proof-record", head=met),
             pull(12, "OPEN", branch="agent/DRE-7012-proof-record", head=waiting),
             pull(13, "OPEN", branch="agent/DRE-7013-proof-record", head=unread),
             pull(14, "OPEN", branch="agent/DRE-7014-some-feature", head="d" * 40)]
    gh = FakeGH({met: MET, waiting: WAITING}, pulls,
                branches=[p["headRefName"] for p in pulls])
    return gh, Thread()


def test_sweep_closes_only_the_record_that_cannot_merge(capsys):
    gh, thread = _three()
    assert prp.sweep(REPO, gh=gh, comment=thread.comment, voices=thread.voices,
                     live=True, now=NOW) == 0
    (close,) = gh.writes()
    assert close[:3] == ["pr", "close", "12"] and "--delete-branch" not in close
    body = close[close.index("--comment") + 1]
    assert body == prp.CLOSE_COMMENT.format(
        branch="agent/DRE-7012-proof-record", n=1,
        card_url="https://linear.app/dreadnoughtfoundry/issue/DRE-7012")
    assert list(thread.posted) == ["DRE-7012"] and len(thread.holds("DRE-7012")) == 1
    out = capsys.readouterr().out
    assert (f"{prp.SWEEP_TAG} read 3 open record pull request(s); closed 1: #12; "
            "kept 1; unread 1") in out


def test_sweep_not_live_writes_nothing(capsys):
    gh, thread = _three()
    assert prp.sweep(REPO, gh=gh, comment=thread.comment, voices=thread.voices,
                     live=False, now=NOW) == 0
    assert gh.writes() == [] and thread.posted == {}
    assert "would: close #12" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# the declaration and the CLI                                                  #
# --------------------------------------------------------------------------- #

def test_both_tags_are_declared_log_lines():
    lines = {e["tag"]: e for e in proof_reach.load()["log_lines"]}
    for constant, tag in (("TAG", "proof-record-pr:"), ("SWEEP_TAG", "proof-record-sweep:")):
        assert getattr(prp, constant) == tag
        assert lines[tag]["printed_by"] == "scripts/proof_record_pr.py"
        assert lines[tag]["constant"] == constant


def test_the_module_is_a_leaf():
    source = SCRIPT.read_text(encoding="utf-8")
    for name in ("proof_dispatch", "reconcile"):
        assert f"import {name}" not in source and f"from {name}" not in source


def test_sweep_cli_is_live_only_when_the_switch_is_exactly_true():
    assert prp.is_live({"PROOF_DISPATCH_LIVE": "true"}) is True
    for value in ("", "True", "1", "yes"):
        assert prp.is_live({"PROOF_DISPATCH_LIVE": value}) is False
    assert prp.is_live({}) is False


def test_decide_cli_writes_the_decision_to_github_output(tmp_path, monkeypatch):
    out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    gh, thread = FakeGH({HEAD: WAITING}), Thread()
    monkeypatch.setattr(proof_record, "_gh", gh)
    monkeypatch.setattr(linear_ops, "cmd_proof_waiting", thread.comment)
    monkeypatch.setattr(linear_ops, "read_card_voices", thread.voices)
    monkeypatch.setenv("CARD_DESCRIPTION", DESCRIPTION)
    assert prp.main(["decide", "--repo", REPO, "--card", CARD, "--card-url", CARD_URL,
                     "--title", TITLE, "--no-hold"]) == 0
    assert out.read_text(encoding="utf-8") == "decision=held\n"
    assert thread.holds() == []


def test_the_cli_runs():
    done = subprocess.run([sys.executable, str(SCRIPT), "--help"],  # nosec B603
                          capture_output=True, text=True, check=False, timeout=60)
    assert done.returncode == 0 and "decide" in done.stdout and "sweep" in done.stdout
