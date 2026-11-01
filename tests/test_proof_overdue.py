"""A PROOF card open more than seven days is read into one of five waits (DRE-8625).

`scripts/proof_overdue.py` is the one reader: how long a `PROOF:` card has
been open, which of five things it waits on, and the one action that class
takes. It writes nothing. Pinned here:

  * the contract the two sibling cards read — `AGE_DAYS`, `CLASSES`,
    `ACTIONS`, `Diagnosis`, `diagnose`, `reading`, `TAG` — spelled exactly;
  * one test per class, each off a hold or a record row, asserting `cls`,
    `action`, `waits_on` and `evidence`; two classes read the earlier in
    `CLASSES`; a card under the age reads None; a card reading none of the
    five reads `unrepeatable` by elimination;
  * the read-through: the dispatcher's own time-only re-check hold over a
    record whose waiting row names an unshipped card reads `release`;
  * the press rule: on the sandbox list on a `repo:agent-bureau` PROOF is
    `press`, the same words on `repo:portico` and a press off the list are
    `unrepeatable`;
  * the age clock — the earliest of the first `🔬 proof-run` receipt and the
    open record pull request's `createdAt`, else the entry into the lane;
  * the replay over the three cards of 2026-11-01, copied off the board;
  * `replay --repo` naming a card it could not read and exiting 1;
  * `reading` handed recorders for Linear and GitHub, and no write call.
"""

from __future__ import annotations

import dataclasses
import inspect
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import linear_ops  # noqa: E402
import proof_dispatch  # noqa: E402
import proof_overdue  # noqa: E402
import proof_record  # noqa: E402
import proof_record_pr  # noqa: E402
import proof_release  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "proof_overdue"
DOC = ROOT / "docs" / "proof-overdue.md"
NOW = datetime(2026, 11, 1, 18, 0, tzinfo=timezone.utc)
OLD = (NOW - timedelta(days=12)).isoformat()


def _hold(observed: str, needs: str) -> str:
    return linear_ops.proof_waiting_line(observed, needs)


def _card(*, repo="agent-bureau", lane="Hand-work", entered=OLD, first_run=None,
          record_opened=None, states=None, blockers=None, identifier="DRE-9000"):
    return {"identifier": identifier, "repo": repo, "labels": [f"repo:{repo}", "agent:ops"],
            "lane": lane, "entered": entered, "first_run": first_run,
            "record_opened": record_opened, "states": states or {},
            "blockers": blockers or {}}


def _diagnose(holds=(), rows=(), release=None, **card):
    return proof_overdue.diagnose(_card(**card), list(holds), list(rows), now=NOW,
                                  release=release)


# --------------------------------------------------------------------------- #
# the contract                                                                 #
# --------------------------------------------------------------------------- #


def test_the_contract_is_spelled_as_the_siblings_read_it():
    assert proof_overdue.AGE_DAYS == 7
    assert proof_overdue.CLASSES == ("release", "date", "press", "deploy", "unrepeatable")
    assert proof_overdue.ACTIONS == {"release": "rerun-on-release", "date": "rerun-on-date",
                                     "press": "sandbox-press", "deploy": "rerun-on-deploy",
                                     "unrepeatable": "split"}
    assert proof_overdue.TAG == "proof-overdue:"
    fields = [f.name for f in dataclasses.fields(proof_overdue.Diagnosis)]
    assert fields == ["card", "repo", "age_days", "clock_from", "cls", "action", "waits_on",
                      "not_before", "by_elimination", "evidence"]
    assert proof_overdue.Diagnosis.__dataclass_params__.frozen
    assert list(inspect.signature(proof_overdue.diagnose).parameters) == [
        "card", "holds", "rows", "now", "release"]
    reading = inspect.signature(proof_overdue.reading).parameters
    assert list(reading) == ["card", "now", "gh", "linear"]
    assert reading["now"].kind is inspect.Parameter.KEYWORD_ONLY
    assert reading["gh"].default is None and reading["linear"].default is None


# --------------------------------------------------------------------------- #
# one class each                                                               #
# --------------------------------------------------------------------------- #


def test_a_hold_waiting_for_a_card_not_shipped_reads_release():
    line = _hold("the gate declined the record twice after re-observation",
                 proof_dispatch.FIX_SHIPPED_NEEDS.format(card="DRE-9101"))
    got = _diagnose([line], states={"DRE-9101": "In Progress"})
    assert (got.cls, got.action, got.waits_on) == ("release", "rerun-on-release", "DRE-9101")
    assert got.evidence == line
    assert got.not_before is None and got.by_elimination is False


def test_a_card_hold_whose_card_is_done_is_not_a_release():
    line = _hold("observation 8", "DRE-9101's record to merge")
    assert _diagnose([line], states={"DRE-9101": "In Progress"}).cls == "release"
    done = _diagnose([line], states={"DRE-9101": "Done"})
    assert done.cls == "unrepeatable" and done.by_elimination is True


def test_an_open_blocker_reads_release_and_a_closed_one_reads_nothing():
    got = _diagnose(blockers={"DRE-9105": "Hand-work", "DRE-9106": "Done"})
    assert (got.cls, got.action, got.waits_on) == ("release", "rerun-on-release", "DRE-9105")
    assert got.evidence == "blocked by DRE-9105 (Hand-work)"
    closed = _diagnose(blockers={"DRE-9106": "Done", "DRE-9107": "Canceled"})
    assert closed.by_elimination is True


def test_the_saved_cards_blockers_are_its_open_blocks_relations():
    saved = json.loads((FIXTURES / "DRE-5357.json").read_text())
    saved["card"]["inverseRelations"] = {"nodes": [
        {"type": "blocks", "issue": {"identifier": "DRE-9105", "state": {"name": "Queued"}}},
        {"type": "related", "issue": {"identifier": "DRE-9108", "state": {"name": "Queued"}}},
        {"type": "blocks", "issue": {"identifier": "DRE-4271", "state": {"name": "Done"}}}]}
    assert proof_overdue.facts(saved)["blockers"] == {"DRE-9105": "Queued"}
    got = proof_overdue.from_saved(saved)
    assert (got.cls, got.waits_on) == ("release", "DRE-9105")


def test_a_release_not_yet_out_reads_release_with_its_tag():
    release = proof_release.Reading("waiting", [
        "waiting — console (console-v*): newest console-v1.4.2 does not carry DRE-9102 (#77)"])
    got = _diagnose(release=release)
    assert (got.cls, got.action) == ("release", "rerun-on-release")
    assert got.waits_on == "console-v1.4.2"
    assert got.evidence == release.lines[0]


def test_a_stamped_waiting_row_reads_date():
    cell = ("Not observed. waiting for the seven-day window to close "
            "[not-before=2026-11-02T02:25:00+00:00]: every card that entered Green Light")
    got = _diagnose(rows=[("Live fact: every card that entered Green Light", cell)])
    assert (got.cls, got.action) == ("date", "rerun-on-date")
    assert got.waits_on == "2026-11-02T02:25:00+00:00"
    assert got.not_before == datetime(2026, 11, 2, 2, 25, tzinfo=timezone.utc)
    assert got.evidence == cell


def test_several_dated_rows_wait_for_the_latest():
    late = "Not observed. waiting for the report [not-before=2026-11-09T13:30:00+00:00]: it"
    early = "Not observed. waiting for the window [not-before=2026-11-02T02:25:00+00:00]: it"
    got = _diagnose(rows=[("Observation 1", early), ("Observation 2", late)])
    assert (got.cls, got.waits_on, got.evidence) == ("date", "2026-11-09T13:30:00+00:00", late)


def test_a_time_only_hold_with_no_rows_behind_it_reads_date():
    line = _hold("its card says it can first be observed after Nov 3 06:00 PT (Observe after: "
                 "2026-11-03 06:00 PT)", "not-before=2026-11-03T06:00-08:00")
    got = _diagnose([line])
    assert (got.cls, got.waits_on) == ("date", "2026-11-03T14:00:00+00:00")
    assert got.not_before == datetime(2026, 11, 3, 14, 0, tzinfo=timezone.utc)
    assert got.evidence == line


def test_a_sandbox_press_on_an_agent_bureau_proof_reads_press():
    criterion = "Sandbox press: make a task on agent-bureau-demo as the console proof-reader"
    cell = "Not observed. waiting for the sandbox press: the task it makes"
    got = _diagnose(rows=[(criterion, cell)])
    assert (got.cls, got.action, got.waits_on) == ("press", "sandbox-press", "createTask")
    assert got.evidence == cell


def test_a_row_waiting_on_a_deploy_reads_deploy():
    cell = ("Not observed. waiting for the operator's next `make deploy-console` beside a "
            "sleeping train run: it would show the deployment record")
    got = _diagnose(rows=[("Observation 4 quotes the deployment record", cell)])
    assert (got.cls, got.action) == ("deploy", "rerun-on-deploy")
    assert got.waits_on == "make deploy-console"
    assert got.evidence == cell


def test_a_history_row_reads_unrepeatable():
    criterion = ("One case of each of the four rules firing inside the window, each either met "
                 "on the live board or by the dry run on the released commit")
    cell = "Not met. history: the window closed 2026-10-17 18:59 PT — one rule never fired"
    got = _diagnose(rows=[(criterion, cell)])
    assert (got.cls, got.action) == ("unrepeatable", "split")
    assert got.waits_on == proof_record._cut(criterion, 70)
    assert len(got.waits_on) <= 70
    assert got.evidence == cell and got.by_elimination is False


def test_a_state_gone_row_and_a_closed_window_hold_read_unrepeatable():
    cell = "Not observed. cause: state-gone — the shelf row was deleted on 2026-10-01"
    assert _diagnose(rows=[("The shelf row is shown", cell)]).cls == "unrepeatable"
    line = _hold("Live fact: none of the listed arrivals…",
                 "the operator's decision on 1 row(s) that read a closed window or a "
                 "finished run: reword each on a new card, or drop the record")
    got = _diagnose([line])
    assert (got.cls, got.by_elimination, got.evidence) == ("unrepeatable", False, line)


def test_two_classes_read_the_earlier_in_classes():
    dated = ("Observation 2", "Not observed. waiting for the Sunday report "
             "[not-before=2026-11-08T13:30:00+00:00]: the report card")
    deployed = ("Observation 4", "Not observed. waiting for the next `make deploy-console`: "
                "the deployment record")
    assert _diagnose(rows=[deployed, dated]).cls == "date"
    shipped = ("Observation 6", "Not observed. waiting for DRE-9103 shipped: the upload")
    got = _diagnose(rows=[dated, deployed, shipped], states={"DRE-9103": "In Review"})
    assert (got.cls, got.waits_on) == ("release", "DRE-9103")


def test_a_card_under_the_age_is_not_overdue():
    young = (NOW - timedelta(days=6, hours=23)).isoformat()
    line = _hold("x", proof_dispatch.FIX_SHIPPED_NEEDS.format(card="DRE-9101"))
    assert _diagnose([line], entered=young) is None


def test_an_overdue_card_reading_none_of_the_five_reads_unrepeatable_by_elimination():
    line = _hold(proof_dispatch.EXHAUSTED_OBSERVED, proof_dispatch.EXHAUSTED_NEEDS)
    got = _diagnose([line], rows=[("The button saves the draft",
                                   "Not met. the save returned 500")])
    assert (got.cls, got.action, got.by_elimination) == ("unrepeatable", "split", True)
    assert got.waits_on == "The button saves the draft"
    nothing = _diagnose()
    assert (nothing.cls, nothing.by_elimination) == ("unrepeatable", True)


# --------------------------------------------------------------------------- #
# the read-through                                                             #
# --------------------------------------------------------------------------- #


def test_the_dispatchers_recheck_hold_is_read_through_to_the_row_it_rechecks():
    row = ("Observation 6: foundry's logs land",
           "Not observed. waiting for DRE-9104 shipped: foundry's first uploaded log")
    observed, needs = proof_record_pr.hold_line([row], NOW - timedelta(days=1))
    line = _hold(observed, needs)
    assert proof_record.time_only(line) is not None  # the re-check timer itself
    assert proof_dispatch.RECHECK_REASON.startswith("re-check of the waiting rows")
    got = _diagnose([line], rows=[row], states={"DRE-9104": "Todo"})
    assert (got.cls, got.waits_on) == ("release", "DRE-9104")
    assert got.evidence == row[1]
    # with the card shipped, the row's prose event reads none of the five
    assert _diagnose([line], rows=[row], states={"DRE-9104": "Done"}).by_elimination


# --------------------------------------------------------------------------- #
# the press rule                                                               #
# --------------------------------------------------------------------------- #


def test_the_same_press_on_another_repos_proof_reads_unrepeatable():
    row = ("Sandbox press: make a task on agent-bureau-demo as the console proof-reader",
           "Not observed. waiting for the sandbox press: the task it makes")
    assert _diagnose(rows=[row]).cls == "press"
    got = _diagnose(rows=[row], repo="portico")
    assert (got.cls, got.by_elimination) == ("unrepeatable", False)
    assert got.evidence == row[1]


def test_a_press_off_the_list_reads_unrepeatable():
    row = ("A project's Builders run on the model its exception names",
           "Not observed. waiting for the operator's press: setting the project's Builders "
           "model on agent-bureau-demo (DRE-6638)")
    got = _diagnose(rows=[row])
    assert (got.cls, got.by_elimination) == ("unrepeatable", False)
    assert got.waits_on == proof_record._cut(row[0], 70)


# --------------------------------------------------------------------------- #
# the age clock                                                                #
# --------------------------------------------------------------------------- #


def test_the_clock_is_the_earliest_receipt_or_open_record_else_the_lane_entry():
    run = NOW - timedelta(days=9)
    opened = NOW - timedelta(days=10, hours=12)
    got = _diagnose(first_run=run.isoformat(), record_opened=opened.isoformat(),
                    entered=(NOW - timedelta(days=2)).isoformat())
    assert got.clock_from == opened
    assert got.age_days == pytest.approx(10.5)
    got = _diagnose(first_run=run.isoformat(), entered=(NOW - timedelta(days=2)).isoformat())
    assert got.clock_from == run
    assert _diagnose(entered=(NOW - timedelta(days=2)).isoformat()) is None


def test_the_saved_card_is_clocked_off_its_thread_and_its_open_record():
    saved = json.loads((FIXTURES / "DRE-6673.json").read_text())
    facts = proof_overdue.facts(saved)
    assert facts["first_run"] == datetime(2026, 10, 11, 2, 45, 52, 840000, tzinfo=timezone.utc)
    assert facts["record_opened"] is None  # #985 was closed on 2026-11-01
    assert facts["lane"] == "In Review"
    saved["record"]["pr"]["state"] = "OPEN"
    assert proof_overdue.facts(saved)["record_opened"] == datetime(
        2026, 10, 11, 2, 53, 15, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# the replay of 2026-11-01                                                     #
# --------------------------------------------------------------------------- #

LINE = re.compile(r"^proof-overdue: (DRE-\d+) · (\d+\.\d) days · class=(\w+) · "
                  r"action=([\w-]+) · waits-on=(.+)$")
PLAN = {"DRE-6673": ("date", "2026-11-02T02:25:00+00:00"),
        "DRE-6008": ("deploy", "make deploy-console"),
        # The plan read `release`, Observation 4 waiting on DRE-8467. The record
        # on its branch at 4a61f7c now waits on DRE-8154 shipped and
        # AgentLogStack deployed, and DRE-8154 (like DRE-8467 and DRE-5407) is
        # Done — so the copied thread reads `deploy`.
        "DRE-5357": ("deploy", "AgentLogStack deployed")}


@pytest.mark.parametrize("card", sorted(PLAN))
def test_the_replay_of_each_card_prints_one_line(card, capsys):
    assert proof_overdue.main(["replay", "--fixture", str(FIXTURES / f"{card}.json")]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    tagged = [line for line in lines if line.startswith(proof_overdue.TAG)]
    assert len(tagged) == 1, lines
    found = LINE.match(tagged[0])
    assert found, tagged[0]
    ident, _, cls, action, waits_on = found.groups()
    assert ident == card
    assert cls in proof_overdue.CLASSES and action == proof_overdue.ACTIONS[cls]
    assert (cls, waits_on) == PLAN[card]


def test_the_fixtures_are_the_cards_as_they_stood():
    for card in PLAN:
        saved = json.loads((FIXTURES / f"{card}.json").read_text())
        assert saved["card"]["identifier"] == card
        assert saved["saved_at"].startswith("2026-11-01")
        assert saved["card"]["title"].startswith("PROOF:")
        assert saved["thread"] and saved["record"]["text"]
        assert any(n["name"].startswith("repo:") for n in saved["card"]["labels"]["nodes"])


def test_the_replayed_diagnoses_read_their_evidence_off_the_card():
    got = {card: proof_overdue.from_saved(json.loads((FIXTURES / f"{card}.json").read_text()))
           for card in PLAN}
    assert got["DRE-6673"].not_before == datetime(2026, 11, 2, 2, 25, tzinfo=timezone.utc)
    assert got["DRE-6673"].evidence.startswith("Not observed. waiting for the seven-day window")
    assert got["DRE-6008"].evidence.startswith("Not observed. waiting for a production "
                                               "`make deploy-console`")
    assert "AgentLogStack deployed" in got["DRE-5357"].evidence
    assert got["DRE-6008"].repo == "agent-bureau" and got["DRE-6673"].repo == "bureau-pipeline"
    assert all(d.age_days > proof_overdue.AGE_DAYS for d in got.values())


# --------------------------------------------------------------------------- #
# the live read                                                                #
# --------------------------------------------------------------------------- #

VIEWER = "pipeline-user"
CREATED = "2026-10-10T18:00:00Z"


def _issue(identifier: str, lane: str = "Hand-work", repo: str = "agent-bureau") -> dict:
    return {"identifier": identifier, "title": f"PROOF: {identifier} is observed",
            "createdAt": CREATED, "description": "", "state": {"name": lane},
            "labels": {"nodes": [{"name": f"repo:{repo}"}, {"name": "agent:ops"}]},
            "history": {"nodes": [{"createdAt": "2026-10-12T00:00:00Z",
                                   "toState": {"name": lane}}]},
            "parent": {"identifier": "DRE-1", "children": {"nodes": []}}}


class RecordingLinear:
    """Every Linear read `reading` and the replay make, recorded; anything
    else it reaches for is a write, and fails the test."""

    READS = ("proofs", "card", "thread", "states")

    def __init__(self, threads: dict, *, failing=()):
        self.threads, self.failing, self.calls = threads, set(failing), []

    def proofs(self, slug):
        self.calls.append(("proofs", slug))
        return list(self.threads)

    def card(self, identifier):
        self.calls.append(("card", identifier))
        if identifier in self.failing:
            raise LookupError(f"Linear answered no card for {identifier}")
        return _issue(identifier)

    def thread(self, identifier):
        self.calls.append(("thread", identifier))
        return self.threads[identifier], VIEWER

    def states(self, identifiers):
        self.calls.append(("states", tuple(identifiers)))
        return {i: "In Progress" for i in identifiers}

    def __getattr__(self, name):
        raise AssertionError(f"reading reached for Linear's {name}, which is not a read")


class RecordingGitHub:
    """`gh` for `reading`, recorded: a record branch whose closed pull request
    MODIFIES its record under `architecture/` (DRE-5357's #3876 shape)."""

    def __init__(self, record: str):
        self.record, self.calls = record, []

    def __call__(self, args):
        args = list(args)
        self.calls.append(args)
        if args[:2] == ["pr", "list"]:
            return json.dumps([{"number": 3876, "state": "CLOSED", "createdAt": CREATED,
                                "headRefName": "agent/DRE-9000-proof-record",
                                "headRefOid": "4a61f7c", "url": "u",
                                "files": [{"path": "architecture/proofs/logs.md",
                                           "changeType": "MODIFIED"}]}]
                              if "all" in args and "--head" in args else [])
        if args[0] == "api" and "/git/ref/heads/" in args[1]:
            return json.dumps({"object": {"sha": "4a61f7c57640d31ec526966fc41b2b45540ffd18"}})
        if args[0] == "api" and "/contents/" in args[1]:
            import base64
            return json.dumps({"encoding": "base64", "sha": "b",
                               "content": base64.b64encode(self.record.encode()).decode()})
        raise AssertionError(f"gh {' '.join(args)} is not read here")


RECORD = """# Proof record

| Criterion | Result |
| --- | --- |
| Logs land for every kind of run | Met. 10,022 logs |
| Observation 6: foundry's logs land | Not observed. waiting for DRE-9104 shipped: the upload |
"""


def _thread(*bodies) -> list:
    return [{"body": b, "createdAt": f"2026-10-{12 + i:02d}T00:00:00Z", "user": {"id": VIEWER}}
            for i, b in enumerate(bodies)]


def _no_writes(linear: RecordingLinear, gh: RecordingGitHub) -> None:
    assert {name for name, *_ in linear.calls} <= set(RecordingLinear.READS)
    for args in gh.calls:
        assert args[0] in ("pr", "api"), args
        if args[0] == "pr":
            assert args[1] in ("list", "view"), args
        assert not {"-X", "--method", "-f", "-F", "--field", "--raw-field", "--input"} & set(
            args), args


def test_reading_reads_the_record_on_its_branch_and_writes_nothing(monkeypatch):
    def refuse(*_a, **_k):
        raise AssertionError("reading went to Linear around its reads")

    monkeypatch.setattr(linear_ops, "gql", refuse)
    hold = _hold("Observation 6: foundry's logs land", "not-before=2026-10-20T08:00+00:00")
    linear = RecordingLinear({"DRE-9000": _thread(hold)})
    gh = RecordingGitHub(RECORD)
    got = proof_overdue.reading("DRE-9000", now=NOW, gh=gh, linear=linear)
    assert (got.cls, got.waits_on, got.repo) == ("release", "DRE-9104", "agent-bureau")
    assert got.clock_from == datetime(2026, 10, 12, tzinfo=timezone.utc)  # the lane entry
    _no_writes(linear, gh)
    assert any("/contents/architecture/proofs/logs.md" in " ".join(a) for a in gh.calls)


class ReleaseGitHub(RecordingGitHub):
    """A sibling build card merged, its release record named — and the record
    branch there or gone (`branch`)."""

    def __init__(self, record: str, *, branch: bool):
        super().__init__(record)
        self.branch = branch

    def __call__(self, args):
        args = list(args)
        if args[:2] == ["pr", "list"] and "head:agent/DRE-9201" in args:
            self.calls.append(args)
            return json.dumps([{"number": 41, "url": "u", "headRefName": "agent/DRE-9201-x",
                                "state": "MERGED", "mergeCommit": {"oid": "abc1234"},
                                "mergedAt": CREATED}])
        if args[0] == "api" and args[1].endswith("/contents/.github/bureau/release.json"):
            self.calls.append(args)
            raise RuntimeError("gh: Not Found (HTTP 404)")
        if args == ["api", "repos/dreadnought-foundry/agent-bureau"]:
            self.calls.append(args)
            return json.dumps({"full_name": "dreadnought-foundry/agent-bureau"})
        if not self.branch and args[0] == "api" and "/git/ref/heads/" in args[1]:
            self.calls.append(args)
            raise RuntimeError("gh: Not Found (HTTP 404)")
        return super().__call__(args)


class SiblingLinear(RecordingLinear):
    def card(self, identifier):
        issue = super().card(identifier)
        issue["parent"]["children"]["nodes"] = [
            {"identifier": "DRE-9201", "title": "agent-bureau: the build", "state": {"name": "Done"},
             "labels": {"nodes": [{"name": "repo:agent-bureau"}]}}]
        return issue


@pytest.mark.parametrize("branch", [True, False])
def test_the_release_is_read_only_for_a_card_never_run(branch):
    linear = SiblingLinear({"DRE-9000": _thread()})
    gh = ReleaseGitHub(RECORD, branch=branch)
    saved = proof_overdue.gather("DRE-9000", now=NOW, gh=gh, linear=linear)
    read = any("release.json" in " ".join(a) for a in gh.calls)
    if branch:  # a record on its branch: the card has run, receipt or not
        assert saved["record"]["text"] and not read and saved["release"] is None
    else:
        assert saved["record"]["text"] is None and read
        assert saved["release"]["state"] == "ready"
    _no_writes(linear, gh)


def test_a_card_under_the_age_reads_no_release():
    linear = SiblingLinear({"DRE-9000": _thread()})
    gh = ReleaseGitHub(RECORD, branch=False)
    young = datetime(2026, 10, 15, tzinfo=timezone.utc)  # three days after the lane entry
    saved = proof_overdue.gather("DRE-9000", now=young, gh=gh, linear=linear)
    assert saved["release"] is None
    assert not any("release.json" in " ".join(a) for a in gh.calls)
    assert proof_overdue.from_saved(saved) is None


def test_a_record_that_modifies_its_file_is_still_found_for_diagnosis():
    files = [{"path": "architecture/proofs/agent-working-logs.md", "changeType": "MODIFIED"}]
    assert proof_record.find_record(files)[0] is None  # the gate's refusal is unchanged
    assert proof_overdue.record_path(files) == "architecture/proofs/agent-working-logs.md"
    two = files + [{"path": "docs/other.md", "changeType": "MODIFIED"}]
    assert proof_overdue.record_path(two) is None
    added = files + [{"path": "docs/new.md", "changeType": "ADDED"}]
    assert proof_overdue.record_path(added) == "docs/new.md"


def test_the_replay_names_a_card_it_could_not_read_and_exits_1(capsys):
    hold = _hold("x", proof_dispatch.FIX_SHIPPED_NEEDS.format(card="DRE-9101"))
    threads = {f"DRE-{n}": _thread(hold) for n in (9001, 9002, 9003)}
    linear = RecordingLinear(threads, failing={"DRE-9002"})
    gh = RecordingGitHub(RECORD)
    code = proof_overdue.main(["replay", "--repo", "agent-bureau"], linear=linear, gh=gh, now=NOW)
    out = capsys.readouterr().out.strip().splitlines()
    assert code == 1
    assert any(line.startswith("proof-overdue: DRE-9002") and "could not be read" in line
               and "Linear answered no card" in line for line in out), out
    for card in ("DRE-9001", "DRE-9003"):
        assert any(LINE.match(line) and LINE.match(line).group(1) == card for line in out), out
    assert all(line.startswith(proof_overdue.TAG) for line in out)
    _no_writes(linear, gh)


class PagedLinear(RecordingLinear):
    """`RecordingLinear` whose repo read is the module's own, over Linear
    serving the open PROOF cards one page at a time."""

    def proofs(self, slug):
        self.calls.append(("proofs", slug))
        return proof_overdue.LinearReads().proofs(slug)


def test_the_replay_reads_every_page_of_a_repos_proof_cards(monkeypatch, capsys):
    pages = {None: (["DRE-9001"], "c1"), "c1": (["DRE-9002"], None)}

    def gql(query, variables=None):
        assert query == proof_overdue.PROOFS_QUERY
        assert variables["label"] == "repo:agent-bureau"
        ids, cursor = pages[variables.get("after")]
        return {"issues": {"nodes": [{"identifier": i} for i in ids],
                           "pageInfo": {"hasNextPage": cursor is not None, "endCursor": cursor}}}

    monkeypatch.setattr(linear_ops, "gql", gql)
    hold = _hold("x", proof_dispatch.FIX_SHIPPED_NEEDS.format(card="DRE-9101"))
    linear = PagedLinear({f"DRE-{n}": _thread(hold) for n in (9001, 9002)})
    gh = RecordingGitHub(RECORD)
    code = proof_overdue.main(["replay", "--repo", "agent-bureau"], linear=linear, gh=gh, now=NOW)
    out = capsys.readouterr().out.strip().splitlines()
    assert code == 0
    for card in ("DRE-9001", "DRE-9002"):  # the second page's card is read too
        assert any(LINE.match(line) and LINE.match(line).group(1) == card for line in out), out
    assert "$after" in proof_overdue.PROOFS_QUERY and "pageInfo" in proof_overdue.PROOFS_QUERY


def test_the_states_of_more_cards_than_one_request_answers_are_all_read(monkeypatch):
    asked = []

    def gql(query, variables=None):
        assert query == proof_dispatch.CARD_STATES_QUERY
        numbers = variables["numbers"]
        assert len(numbers) <= proof_dispatch.LANE_RELATIONS_CARDS
        asked.append(numbers)
        return {"issues": {"nodes": [{"identifier": f"DRE-{n}", "state": {"name": "Done"}}
                                     for n in numbers]}}

    monkeypatch.setattr(linear_ops, "gql", gql)
    named = [f"DRE-{n}" for n in range(9100, 9161)]  # 61, past one request's 50
    got = proof_overdue.LinearReads().states(named)
    assert got == {i: "Done" for i in named}
    assert len(asked) == 2


def test_the_replay_says_none_overdue(capsys):
    linear = RecordingLinear({})
    assert proof_overdue.main(["replay", "--repo", "portico"], linear=linear,
                              gh=RecordingGitHub(RECORD), now=NOW) == 0
    assert capsys.readouterr().out.strip() == "proof-overdue: none overdue in portico"


# --------------------------------------------------------------------------- #
# the one docs page                                                            #
# --------------------------------------------------------------------------- #


def test_the_docs_page_states_the_rules_and_names_its_one_reader():
    text = DOC.read_text().casefold()
    for cls in proof_overdue.CLASSES:
        assert f"`{cls}`" in text, cls
    for action in proof_overdue.ACTIONS.values():
        assert f"`{action}`" in text, action
    for words in ("precedence", "seven days", "read-through", "elimination",
                  "scripts/proof_overdue.py", "proof-run", "createdat", "lane"):
        assert words in text, words
