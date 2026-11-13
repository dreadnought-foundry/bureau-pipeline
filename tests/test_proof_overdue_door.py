"""The Proof overdue step reads its PROOF cards through the read door (DRE-8909).

On every full pass `scripts/proof_overdue_sweep.py`, then
`scripts/proof_overdue_act.py`, listed the repo's open PROOF cards and read
each one's card, lane history, epic children and whole thread from Linear —
60 to 73 requests a pass on bureau-pipeline's own key. A proof is overdue
after seven days and told once a PT day, so a 15-minute-old answer changes
nothing either decides. These pin `proof_overdue.DoorReads`:

* in mode `on`, the PROOF cards come off ONE `scope=repo` board read of the
  lanes the sweep already reads there, plus ONE Linear query for
  `PROOF_LINEAR_LANES`; each door card's card and thread come off ONE
  `comments=all` cards read, and the lane history and the epic's children
  are read from Linear only when the decision needs them;
* only a door card is ever named to the door — a card from the Linear lanes,
  and every card once the board read is UNKNOWN, reads through
  `LinearReads`;
* a `thread-incomplete` batch is asked again card by card, and only that
  reason is;
* every door request sends `X-Bureau-Max-Age: 900` (`60` for the once-a-day
  re-check) and prints its `read-door: read decision=…` line;
* the once-a-day check reads the thread it already holds first;
* in modes `off` and `shadow` both scripts make exactly today's reads.

Every fallback here stubs the `LinearReads` method fallen back to and asserts
it was called, never the Linear request it makes (DRE-8898 may put the door
in front of that request). The door and the issuer are
`tests/bureau_read_fakes.py`, on 127.0.0.1; Linear is a stub that records
every request.

Run: cd bureau-pipeline && python3 -m pytest tests/test_proof_overdue_door.py -v
"""
from __future__ import annotations

import base64
import contextlib
import io
import json
import os
import re
import sys
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import bureau_read  # noqa: E402
import linear_ops  # noqa: E402
import pipeline_notice  # noqa: E402
import proof_overdue  # noqa: E402
import proof_overdue_act as act  # noqa: E402
import proof_overdue_sweep as sweep_mod  # noqa: E402
import proof_run_state  # noqa: E402
import reconcile  # noqa: E402
import bureau_read_fakes as fakes  # noqa: E402
from bureau_read_fakes import FakeDoor, FakeIssuer, door_env  # noqa: E402

SLUG = "bureau-pipeline"
REPOSITORY = "dreadnought-foundry/bureau-pipeline"
VIEWER = "pipeline-user"
#: 10:00 PT on 2026-11-02 (PST).
NOW = datetime(2026, 11, 2, 18, 0, tzinfo=timezone.utc)
DAY = "2026-11-02"
LANE_CONTRACT = ROOT / "config" / "lane-contract.json"
DOOR_LANES = ("In Progress", "In Review", "Hand-work", "Queued", "Backlog")

LINE = re.compile(
    r"^read-door: read decision=(?P<decision>\S+) verdict=(?P<verdict>FRESH|UNKNOWN) "
    r"age=(?P<age>\d+|-) max-age=(?P<max_age>\d+) reason=(?P<reason>\S+)$")

RECEIPT = ("🔬 proof-run: dispatched a proof run at 2026-10-01 08:00 PT — first proof run "
           "(dispatch 1 of 2)")
RECORD = """# Proof record

| Criterion | Result |
| --- | --- |
| The logs land | Met. 10,022 logs |
"""


def comment(body: str, at: str, user: str | None = VIEWER) -> dict:
    return {"body": body, "createdAt": at, "user": {"id": user} if user else None}


def proof(ident: str, lane: str = "Queued", *, comments=(), parent=None, repo=SLUG,
          title=None) -> dict:
    """One PROOF card in the door's full shape; `comments` oldest first."""
    node = fakes.card(ident, lane, labels=(f"repo:{repo}", "agent:ops"), parent=parent,
                      title=title or f"PROOF: {ident} is observed",
                      created="2026-09-01T00:00:00.000Z")
    node["comments"] = {"pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": list(reversed(list(comments)))}
    return node


def ran(ident: str, lane: str = "Queued", **kw) -> dict:
    """A PROOF card with a `🔬 proof-run` receipt of 2026-10-01: overdue."""
    return proof(ident, lane, comments=[comment(RECEIPT, "2026-10-01T15:00:00.000Z")], **kw)


def mark(day: str = DAY, at: str = "2026-11-02T16:00:00.000Z", user=VIEWER) -> dict:
    """The daily pass's own comment for `day`."""
    d = proof_overdue.Diagnosis(
        card="DRE-1", repo=SLUG, age_days=32.1, clock_from=NOW, cls="unrepeatable",
        action="split", waits_on="x", not_before=None, by_elimination=True, evidence="")
    return comment(sweep_mod.comment(d, day), at, user)


def linear_issue(node: dict, *, entered: str = "2026-10-20T00:00:00Z") -> dict:
    """The card `LinearReads.card` would read for a door node."""
    issue = {k: v for k, v in node.items() if k not in ("comments",)}
    lane = node["state"]["name"]
    issue["history"] = {"nodes": [{"createdAt": entered, "toState": {"name": lane}}]}
    if issue.get("parent"):
        issue["parent"] = {"identifier": issue["parent"]["identifier"],
                           "children": {"nodes": []}}
    return issue


# --------------------------------------------------------------------------- #
# the stubs                                                                    #
# --------------------------------------------------------------------------- #


class Linear:
    """Linear, answering the few reads `DoorReads` may make and recording
    every request — any other read is recorded as `other`."""

    def __init__(self, *, lane_proofs=(), all_proofs=(), entered="2026-10-20T00:00:00Z"):
        self.lane_proofs, self.all_proofs = list(lane_proofs), list(all_proofs)
        self.entered = entered
        self.reads: list[tuple[str, dict]] = []

    @staticmethod
    def _nodes(ids) -> dict:
        return {"issues": {"nodes": [{"identifier": i} for i in ids],
                           "pageInfo": {"hasNextPage": False, "endCursor": None}}}

    def gql(self, query, variables=None, **_):
        variables = dict(variables or {})
        if " ".join(query.split()) == "query { viewer { id } }":
            self.reads.append(("viewer", variables))
            return {"viewer": {"id": VIEWER}}
        if query == getattr(proof_overdue, "PROOF_LANES_QUERY", None):
            self.reads.append(("lane-proofs", variables))
            return self._nodes(self.lane_proofs)
        if query == proof_overdue.PROOFS_QUERY:
            self.reads.append(("all-proofs", variables))
            return self._nodes(self.all_proofs)
        if query == getattr(proof_overdue, "LANE_ENTRY_QUERY", None):
            self.reads.append(("lane-entry", variables))
            return {"issue": {"history": {"nodes": [
                {"createdAt": self.entered, "toState": {"name": "Hand-work"}}]}}}
        if query == getattr(proof_overdue, "EPIC_CHILDREN_QUERY", None):
            self.reads.append(("epic-children", variables))
            return {"issue": {"parent": {"identifier": "DRE-1", "children": {"nodes": []}}}}
        if query == proof_overdue.CARD_QUERY:
            self.reads.append(("card-query", variables))
        elif query == act.CARD_QUERY:
            self.reads.append(("act-card-query", variables))
        else:
            self.reads.append(("other", {"query": " ".join(query.split())[:80], **variables}))
        return {}

    def kinds(self) -> list:
        return [kind for kind, _ in self.reads]


class GitHub:
    """`gh` for `proof_overdue._record`: no record branch, and an OPEN record
    pull request for each card in `open_records`."""

    def __init__(self, open_records=()):
        self.open_records = set(open_records)
        self.calls: list = []

    def __call__(self, args):
        args = list(args)
        self.calls.append(args)
        if args[:2] == ["pr", "list"]:
            head = args[args.index("--head") + 1] if "--head" in args else ""
            for ident in self.open_records:
                if head == f"agent/{ident}-proof-record":
                    return json.dumps([{
                        "number": 77, "url": "u", "state": "OPEN",
                        "createdAt": "2026-10-15T00:00:00Z", "headRefName": head,
                        "headRefOid": "4a61f7c57640d31ec526966fc41b2b45540ffd18",
                        "files": [{"path": "architecture/proofs/logs.md",
                                   "changeType": "ADDED"}]}])
            return "[]"
        if args[0] == "api" and "/git/ref/heads/" in args[1]:
            raise RuntimeError("gh: Not Found (HTTP 404)")
        if args[0] == "api" and "/contents/" in args[1]:
            return json.dumps({"encoding": "base64", "sha": "b",
                               "content": base64.b64encode(RECORD.encode()).decode()})
        raise AssertionError(f"gh {' '.join(args)} is not read here")


class Fallback:
    """`LinearReads.card` and `.thread` (and `act.LinearReads.card`),
    stubbed: each serves the Linear copy of a card in `world`."""

    def __init__(self, world: dict):
        self.world = world
        self.calls: list[tuple[str, str]] = []

    def card(self, reads, identifier):
        self.calls.append(("card", identifier))
        return linear_issue(self.world[identifier])

    def thread(self, reads, identifier):
        self.calls.append(("thread", identifier))
        return linear_ops.window_nodes(self.world[identifier]["comments"]), VIEWER

    def named(self) -> set:
        return {ident for _, ident in self.calls}


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    for name in ("BUREAU_READ", "BUREAU_READ_URL", "BUREAU_READ_AUDIENCE",
                 "BUREAU_PIPELINE_REF", "ACTIONS_ID_TOKEN_REQUEST_URL",
                 "ACTIONS_ID_TOKEN_REQUEST_TOKEN", "GITHUB_REPOSITORY",
                 "GITHUB_EVENT_NAME", "GITHUB_HEAD_REF", "PROOF_DISPATCH_LIVE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(bureau_read, "TOTAL_TIMEOUT", 0.5)
    bureau_read.reset_for_tests()
    linear_ops.reset_pass_cache()
    yield
    bureau_read.reset_for_tests()
    linear_ops.reset_pass_cache()


@pytest.fixture
def linear(monkeypatch):
    stub = Linear()
    monkeypatch.setattr(linear_ops, "gql", stub.gql)
    return stub


@pytest.fixture
def fallback(monkeypatch):
    stub = Fallback({})
    # Plain functions, so each binds to the reader it is called on.
    monkeypatch.setattr(proof_overdue.LinearReads, "card", lambda r, i: stub.card(r, i))
    monkeypatch.setattr(proof_overdue.LinearReads, "thread", lambda r, i: stub.thread(r, i))
    monkeypatch.setattr(act.LinearReads, "card", lambda r, i: stub.card(r, i))
    return stub


@pytest.fixture
def writes(monkeypatch):
    """The sweep's writes and its Linear comment re-read, recorded."""
    got = {"posted": [], "records": [], "raised": [], "resolved": []}

    def comment_records(card, **_):
        got["records"].append(card)
        return []

    def cmd_comment(card, body, *_):
        got["posted"].append((card, body))
        return None

    monkeypatch.setattr(linear_ops, "comment_records", comment_records)
    monkeypatch.setattr(linear_ops, "cmd_comment", cmd_comment)
    monkeypatch.setattr(pipeline_notice, "raise_notice",
                        lambda key, **_: got["raised"].append(key) or "raised")
    monkeypatch.setattr(pipeline_notice, "resolve_notice",
                        lambda key, **_: got["resolved"].append(key) or "not-open")
    return got


@contextlib.contextmanager
def door_at(monkeypatch, *cards, mode="on"):
    world = {c["identifier"]: c for c in cards}
    with FakeIssuer() as issuer, FakeDoor(world, repository=REPOSITORY) as door:
        for key, value in door_env(door_url=door.url, issuer=issuer, mode=mode,
                                   repository=REPOSITORY).items():
            monkeypatch.setenv(key, value)
        yield door


def run_sweep(*, gh=None, live=False):
    out = io.StringIO()
    with redirect_stdout(out):
        status = sweep_mod.sweep(SLUG, now=NOW, live=live, gh=gh or GitHub())
    return status, out.getvalue()


def door_lines(text: str) -> list:
    return [m.groupdict() for m in map(LINE.match, text.splitlines()) if m]


def decisions(text: str) -> list:
    return [line["decision"] for line in door_lines(text)]


def cards_asked(door) -> list:
    """The ids of each `/cards` request, in order."""
    return [r["query"].get("ids", "").split(",") for r in door.asked("/cards")]


def would_lines(text: str) -> list:
    return sorted(line for line in text.splitlines() if line.startswith("would: comment"))


# --------------------------------------------------------------------------- #
# the contract                                                                 #
# --------------------------------------------------------------------------- #


def test_the_contract_is_spelled_as_the_card_spells_it():
    assert proof_overdue.OVERDUE_MAX_AGE == 900
    assert proof_overdue.PROOF_LINEAR_LANES == ("Intake", "Planning", "Green Light", "Triage")
    assert issubclass(proof_overdue.DoorReads, proof_overdue.LinearReads)
    assert issubclass(act.DoorReads, proof_overdue.DoorReads)
    for name in ("proofs", "card", "thread", "states", "_linear_lane_proofs", "_lane_entry",
                 "_epic_children"):
        assert callable(getattr(proof_overdue.DoorReads, name)), name


def test_the_door_lanes_and_the_linear_lanes_are_every_open_lane():
    assert reconcile.DOOR_WORK_LANES + reconcile.QUEUE_LANES == DOOR_LANES
    lanes = {lane["name"] for lane in json.loads(LANE_CONTRACT.read_text())["lanes"]}
    open_lanes = lanes - {"Done", "Canceled", "Duplicate"}
    assert set(DOOR_LANES) | set(proof_overdue.PROOF_LINEAR_LANES) == open_lanes
    assert not set(DOOR_LANES) & set(proof_overdue.PROOF_LINEAR_LANES)
    assert "Todo" not in proof_overdue.PROOF_LINEAR_LANES + DOOR_LANES


# --------------------------------------------------------------------------- #
# mode on: the door answers                                                    #
# --------------------------------------------------------------------------- #


def test_three_door_cards_are_diagnosed_with_only_the_linear_reads_the_decision_needs(
        monkeypatch, linear):
    """A receipt (DRE-101), neither receipt nor record (DRE-102) and an open
    record pull request (DRE-103): the one lane entry read is DRE-102's."""
    world = (ran("DRE-101"), proof("DRE-102", "Hand-work"), proof("DRE-103", "In Review"),
             proof("DRE-104", title="Card DRE-104"),  # not a PROOF
             ran("DRE-105", repo="portico"))  # another repo's
    with door_at(monkeypatch, *world) as door:
        status, out = run_sweep(gh=GitHub(open_records=("DRE-103",)))
    assert status == 0, out
    assert sorted(linear.kinds()) == ["lane-entry", "lane-proofs", "viewer"], linear.reads
    assert [v["id"] for k, v in linear.reads if k == "lane-entry"] == ["DRE-102"]
    for ident in ("DRE-101", "DRE-102", "DRE-103"):
        assert f"would: comment {ident} — ⏳ proof-overdue: {DAY} " in out, out
    assert "DRE-104" not in out and "DRE-105" not in out
    # One board read, one batch, and the 60-second re-check per told card.
    assert decisions(out).count("proof-overdue-board") == 1
    assert decisions(out).count("proof-overdue-cards") == 1
    assert cards_asked(door)[0] == ["DRE-101", "DRE-102", "DRE-103"]


def test_the_board_read_asks_the_five_door_lanes_and_the_linear_query_the_other_four(
        monkeypatch, linear, writes):
    with door_at(monkeypatch, ran("DRE-101")) as door:
        run_sweep()
    (board,) = door.asked("/board")
    assert board["query"]["lanes"] == ",".join(DOOR_LANES)
    assert board["query"]["scope"] == "repo"
    (lanes,) = [v for k, v in linear.reads if k == "lane-proofs"]
    assert lanes["states"] == list(proof_overdue.PROOF_LINEAR_LANES)
    assert lanes["label"] == f"repo:{SLUG}"
    assert not any("Todo" in json.dumps(r["query"]) for r in door.requests)


def test_every_door_request_sends_its_max_age_and_prints_its_line(
        monkeypatch, linear, writes):
    hold = comment(linear_ops.proof_waiting_line("the logs land", "DRE-7001 shipped"),
                   "2026-10-02T00:00:00.000Z")
    world = (proof("DRE-101", comments=[comment(RECEIPT, "2026-10-01T15:00:00.000Z"), hold]),
             ran("DRE-102", "Hand-work"),
             proof("DRE-7001", "In Progress", title="the fix"))
    with door_at(monkeypatch, *world) as door:
        _, out = run_sweep()
    lines = door_lines(out)
    assert {line["decision"] for line in lines} == {
        "proof-overdue-board", "proof-overdue-cards", "proof-overdue-states",
        "proof-overdue-told"}
    assert len(lines) == len(door.requests)
    for line, request in zip(lines, door.requests):
        seconds = "60" if line["decision"] == "proof-overdue-told" else "900"
        assert line["max_age"] == seconds, line
        assert request["headers"]["x-bureau-max-age"] == seconds, (line, request)
        assert line["verdict"] == "FRESH"
    assert ["DRE-7001"] in cards_asked(door)  # the states read


def test_the_epic_children_are_read_only_where_the_release_needs_them(monkeypatch, linear):
    """Both in an epic: DRE-102 never ran and has no record, so its release
    is read; DRE-101 ran, so its children are not."""
    world = (ran("DRE-101", parent="DRE-1"), proof("DRE-102", "Hand-work", parent="DRE-1"))
    with door_at(monkeypatch, *world):
        reads = proof_overdue.DoorReads()
        assert reads.proofs(SLUG) == ["DRE-101", "DRE-102"]
        for ident in ("DRE-101", "DRE-102"):
            proof_overdue.gather(ident, now=NOW, gh=GitHub(), linear=reads)
    assert [v["id"] for k, v in linear.reads if k == "epic-children"] == ["DRE-102"]
    assert [v["id"] for k, v in linear.reads if k == "lane-entry"] == ["DRE-102"]
    assert "card-query" not in linear.kinds()


def test_a_card_and_its_thread_are_read_once_per_process(monkeypatch, linear):
    with door_at(monkeypatch, ran("DRE-101"), ran("DRE-102")) as door:
        reads = proof_overdue.DoorReads()
        reads.proofs(SLUG)
        for _ in range(2):
            for ident in ("DRE-101", "DRE-102"):
                assert reads.card(ident)["identifier"] == ident
                nodes, viewer = reads.thread(ident)
                assert viewer == VIEWER and nodes[0]["body"] == RECEIPT
    assert len(door.asked("/cards")) == 1
    assert linear.kinds().count("viewer") == 1


# --------------------------------------------------------------------------- #
# what is never sent to the door                                               #
# --------------------------------------------------------------------------- #


def test_a_green_light_card_is_never_named_to_the_door(monkeypatch, linear, fallback,
                                                        writes):
    world = {c["identifier"]: c for c in (ran("DRE-101"), ran("DRE-102", "Hand-work"),
                                          ran("DRE-200", "Green Light"))}
    fallback.world = world
    linear.lane_proofs = ["DRE-200"]
    with door_at(monkeypatch, world["DRE-101"], world["DRE-102"]) as door:
        _, out = run_sweep()
    assert decisions(out).count("proof-overdue-cards") == 1
    assert cards_asked(door)[0] == ["DRE-101", "DRE-102"]
    assert not any("DRE-200" in ids for ids in cards_asked(door))
    assert fallback.calls == [("card", "DRE-200"), ("thread", "DRE-200")]
    assert "DRE-200 · in Green Light" in out


def test_an_unknown_board_reads_every_lane_from_linear_and_never_names_a_card(
        monkeypatch, linear, fallback, writes):
    world = {c["identifier"]: c for c in (ran("DRE-101"), ran("DRE-102", "Hand-work"))}
    fallback.world = world
    linear.all_proofs = ["DRE-101", "DRE-102"]
    with door_at(monkeypatch, *world.values()) as door:
        door.routes["/board"] = door.unknown("stale")
        _, out = run_sweep()
    assert linear.kinds().count("all-proofs") == 1
    assert "lane-proofs" not in linear.kinds()
    assert door.asked("/cards") == []
    assert "proof-overdue-cards" not in decisions(out)
    assert "proof-overdue-card" not in decisions(out)
    assert fallback.named() == {"DRE-101", "DRE-102"}
    assert sorted(writes["records"]) == ["DRE-101", "DRE-102"]  # the re-check is Linear's
    assert len(would_lines(out)) == 2


# --------------------------------------------------------------------------- #
# the batch read's UNKNOWNs                                                    #
# --------------------------------------------------------------------------- #


def test_a_thread_the_door_cannot_prove_whole_costs_only_its_own_card(
        monkeypatch, linear, fallback, writes):
    world = {c["identifier"]: c for c in (ran("DRE-101"), ran("DRE-102"),
                                          ran("DRE-103", "Hand-work"),
                                          ran("DRE-104", "In Review"))}
    fallback.world = world
    with door_at(monkeypatch, *world.values()) as door:
        door.incomplete_threads = {"DRE-104"}
        _, out = run_sweep()
    lines = door_lines(out)
    batch = [line for line in lines if line["decision"] == "proof-overdue-cards"]
    assert [line["reason"] for line in batch] == ["thread-incomplete"]
    one = [line for line in lines if line["decision"] == "proof-overdue-card"]
    assert len(one) == 4
    assert [line["verdict"] for line in one].count("FRESH") == 3
    assert cards_asked(door)[1:5] == [["DRE-101"], ["DRE-102"], ["DRE-103"], ["DRE-104"]]
    assert fallback.calls == [("card", "DRE-104"), ("thread", "DRE-104")]
    assert sorted(linear.kinds()) == ["lane-proofs", "viewer"], linear.reads
    assert len(would_lines(out)) == 4


def _missing_one(door):
    """A `/cards` answer that leaves out the last id it was asked for."""
    def answer(endpoint, query, headers):
        ids = query["ids"].split(",")
        return 200, door.envelope([door._shaped(door.world[i], query) for i in ids[:-1]])
    return answer


@pytest.mark.parametrize("reason", ["stale", "not-found", "malformed"])
def test_any_other_unknown_on_the_batch_reads_every_card_through_linear_reads(
        monkeypatch, linear, fallback, writes, reason):
    world = {c["identifier"]: c for c in (ran("DRE-101"), proof("DRE-102", "Hand-work"),
                                          ran("DRE-103", "In Review"))}
    fallback.world = world
    with door_at(monkeypatch, *world.values()):
        _, served = run_sweep()
    bureau_read.reset_for_tests()
    linear.reads.clear()
    with door_at(monkeypatch, *world.values()) as door:
        door.routes["/cards"] = {"stale": door.unknown("stale"),
                                 "not-found": (404, {"error": {"code": "NOT_FOUND"}}),
                                 "malformed": _missing_one(door)}[reason]
        _, out = run_sweep()
    # A stale answer is asked once more before the fallback (DRE-9250).
    assert len(door.asked("/cards")) == (2 if reason == "stale" else 1)
    assert [line["reason"] for line in door_lines(out)
            if line["decision"] == "proof-overdue-cards"] == [reason]
    assert "proof-overdue-card" not in decisions(out)
    assert fallback.named() == {"DRE-101", "DRE-102", "DRE-103"}
    assert would_lines(out) == would_lines(served)
    assert len(would_lines(out)) == 3


# --------------------------------------------------------------------------- #
# the once-a-day check                                                         #
# --------------------------------------------------------------------------- #


def test_a_held_thread_with_todays_mark_posts_nothing_and_reads_nothing_more(
        monkeypatch, linear, writes):
    card = proof("DRE-101", comments=[comment(RECEIPT, "2026-10-01T15:00:00.000Z"), mark()])
    with door_at(monkeypatch, card) as door:
        status, out = run_sweep(live=True)
    assert status == 0, out
    assert f"DRE-101 · told on {DAY} already" in out
    assert writes["posted"] == [] and writes["records"] == []
    assert "proof-overdue-told" not in decisions(out)
    assert len(door.asked("/cards")) == 1


def test_a_mark_only_the_minute_old_reread_shows_posts_nothing(monkeypatch, linear, writes):
    card = ran("DRE-101")
    with door_at(monkeypatch, card) as door:
        def answer(endpoint, query, headers):
            node = door._shaped(card, query)
            if headers.get("X-Bureau-Max-Age") == "60":
                node["comments"]["nodes"] = [mark()] + node["comments"]["nodes"]
            return 200, door.envelope([node])
        door.routes["/cards"] = answer
        status, out = run_sweep(live=True)
    assert status == 0, out
    told = [line for line in door_lines(out) if line["decision"] == "proof-overdue-told"]
    assert [(line["verdict"], line["max_age"]) for line in told] == [("FRESH", "60")]
    assert door.asked("/cards")[-1]["query"] == {"ids": "DRE-101", "comments": "all",
                                                  "relations": "0"}
    assert f"DRE-101 · told on {DAY} already" in out
    assert writes["posted"] == [] and writes["records"] == []


def test_an_unmarked_card_is_told_after_the_reread_and_unknown_reads_linear(
        monkeypatch, linear, writes):
    with door_at(monkeypatch, ran("DRE-101"), ran("DRE-102")) as door:
        def answer(endpoint, query, headers):
            if headers.get("X-Bureau-Max-Age") == "60" and query["ids"] == "DRE-102":
                return door.unknown("stale")
            return 200, door.envelope([door._shaped(door.world[i], query)
                                       for i in query["ids"].split(",")])
        door.routes["/cards"] = answer
        status, out = run_sweep(live=True)
    assert status == 0, out
    assert sorted(c for c, _ in writes["posted"]) == ["DRE-101", "DRE-102"]
    assert writes["records"] == ["DRE-102"]  # DRE-101's minute-old read was the door's


# --------------------------------------------------------------------------- #
# the action step                                                              #
# --------------------------------------------------------------------------- #


def _act_card() -> dict:
    d = proof_overdue.Diagnosis(
        card="DRE-6008", repo=SLUG, age_days=12.4, clock_from=NOW, cls="date",
        action="rerun-on-date", waits_on="2026-11-01T00:00:00+00:00",
        not_before=None, by_elimination=False, evidence="")
    told = comment(sweep_mod.comment(d, DAY), "2026-11-02T17:00:00.000Z")
    return proof("DRE-6008", "Hand-work", comments=[told])


def run_act(**over):
    fired, posted = [], []

    def fire(issue, repo, *, reason, event):
        fired.append((issue["id"], issue["identifier"], reason))
        return True, None

    def run_state(repo, ident, nodes, viewer, *, read, now):
        return proof_run_state.State("finished", ["finished — fixture"], 1, None, [], None)

    out = io.StringIO()
    with redirect_stdout(out):
        status = act.sweep(REPOSITORY, SLUG, live=True, now=NOW, run_state=run_state,
                           fire=fire, read=lambda path: None, gh=GitHub(),
                           released=lambda *a: None, deployed=lambda *a: None,
                           unrecurring=lambda *a, **k: 1, **over)
    return status, fired, out.getvalue()


def test_the_action_step_makes_todays_decision_off_the_door(monkeypatch, linear, fallback,
                                                             writes):
    card = _act_card()
    fallback.world = {"DRE-6008": card}
    with door_at(monkeypatch, card) as door:
        status, fired, out = run_act()
    assert status == 0, out
    assert "card-query" not in linear.kinds() and "act-card-query" not in linear.kinds()
    assert fallback.calls == []
    assert sorted(set(decisions(out))) == ["proof-overdue-board", "proof-overdue-cards"]
    assert all(r["headers"]["x-bureau-max-age"] == "900" for r in door.requests)

    bureau_read.reset_for_tests()
    monkeypatch.setenv("BUREAU_READ", "off")
    linear.all_proofs = ["DRE-6008"]
    status, today, out = run_act()
    assert status == 0, out
    assert fired == today
    assert fired[0][0] == "uuid-DRE-6008"
    assert act.DATE_REASON.split("{")[0] in fired[0][2]


# --------------------------------------------------------------------------- #
# modes off and shadow                                                         #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("mode", ["off", "shadow"])
def test_off_and_shadow_make_todays_reads_and_ask_the_door_nothing(
        monkeypatch, linear, fallback, writes, mode):
    world = {c["identifier"]: c for c in (ran("DRE-101"), proof("DRE-6008", "Hand-work"))}
    world["DRE-6008"] = _act_card()
    fallback.world = world
    linear.all_proofs = list(world)
    with door_at(monkeypatch, *world.values(), mode=mode) as door:
        _, out = run_sweep()
        sweep_calls = sorted(set(fallback.calls))
        fallback.calls.clear()
        _, fired, act_out = run_act()
    assert door.requests == []
    assert door_lines(out + act_out) == []
    assert sweep_calls == [("card", "DRE-101"), ("card", "DRE-6008"),
                                        ("thread", "DRE-101"), ("thread", "DRE-6008")]
    assert sorted(set(fallback.calls)) == sweep_calls
    assert set(linear.kinds()) == {"all-proofs"}, linear.reads
    assert sorted(writes["records"]) == ["DRE-101", "DRE-6008"]
    assert len(fired) == 1
