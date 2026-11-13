"""A pull-request run sends its own card's workflow states and dependents (DRE-9170).

Linear Sync's `card-done` job runs on a merged pull request's `pull_request`
event, and until this card the client refused every read on such a run but
one — `/cards` for the card the head branch names (DRE-8812). So every merge
fell back on its other two reads:

    read-fallback: reader="Linear Sync / card-done / linear_ops.py" read=workflow-states reason=pull-request
    read-fallback: reader="Linear Sync / card-done / merge_sweep_gate.py" read=dependents reason=pull-request

The door serves those two to a pull-request token since agent-bureau
DRE-9166. Since DRE-9254 only the first of those lines could still occur:
`merge_sweep_gate.py` no longer runs in Linear Sync, only in the relay's
card-done Reconcile pass, so no `pull_request` run asks for its own card's
dependents. The read is kept, and pinned below, for the reason
`bureau_read.py`'s docstring gives; the gate test drives the reader directly
on a `pull_request` run to prove the read still works for any reader that
asks it. What these pin, each against the failure it exists to stop:

* on a `pull_request` run whose head branch names `DRE-<n>`,
  `workflow_states()` and `dependents("DRE-<n>", …)` are sent to the door and
  served from it — and so are the two readers that asked them in Linear Sync;
* on the same run every other read — another card's dependents, the board, a
  search — is still refused before a token is minted, and the
  `read-fallback:` line still names it with reason `pull-request`;
* a `pull_request` run whose branch names no card, and every
  `pull_request_target` run, are refused every read exactly as before;
* the module's own words name the three reads the door serves such a run.

No test here reaches the console, GitHub or Linear: the door and the issuer
are `tests/bureau_read_fakes.py`, on 127.0.0.1.
"""
from __future__ import annotations

import ast
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("LINEAR_API_KEY", "test-key")

import bureau_read  # noqa: E402
import linear_ops  # noqa: E402
import merge_sweep_gate  # noqa: E402
from bureau_read_fakes import FakeDoor, FakeIssuer, card, door_env  # noqa: E402

LINE = re.compile(
    r'^read-fallback: reader="(?P<reader>[^"]+)" read=(?P<read>\S+) reason=(?P<reason>\S+)$')

OWN = "DRE-123"
OTHER = "DRE-124"
QUEUED = merge_sweep_gate.QUEUE_LANES[0]
WORLD = {
    OWN: card(OWN, "In Review"),
    OTHER: card(OTHER, QUEUED, blockers=((OWN, "In Review"),)),
    "DRE-125": card("DRE-125", QUEUED, blockers=((OTHER, QUEUED),)),
}


@pytest.fixture(autouse=True)
def _fresh_client(monkeypatch):
    for name in ("BUREAU_READ", "BUREAU_READ_URL", "BUREAU_READ_AUDIENCE",
                 "BUREAU_PIPELINE_REF", "ACTIONS_ID_TOKEN_REQUEST_URL",
                 "ACTIONS_ID_TOKEN_REQUEST_TOKEN", "GITHUB_REPOSITORY",
                 "GITHUB_EVENT_NAME", "GITHUB_HEAD_REF", "GITHUB_WORKFLOW",
                 "GITHUB_JOB", "BUREAU_READ_TOKEN_BROKER",
                 "BUREAU_READ_TOKEN_BROKER_SECRET", "BUREAU_READ_LINES_FILE"):
        monkeypatch.delenv(name, raising=False)
    bureau_read.reset_for_tests()
    linear_ops.reset_workflow_states()
    yield
    bureau_read.reset_for_tests()
    linear_ops.reset_workflow_states()


def _pull_request_run(monkeypatch, door, issuer, *, head_ref="agent/DRE-123-x",
                      event="pull_request"):
    for key, value in door_env(door_url=door.url, issuer=issuer).items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("GITHUB_EVENT_NAME", event)
    if head_ref is not None:
        monkeypatch.setenv("GITHUB_HEAD_REF", head_ref)


def _lines(err: str) -> list[tuple[str, str]]:
    """Every `read-fallback:` line on stderr, as `(read, reason)`."""
    out = []
    for raw in err.splitlines():
        if raw.startswith(bureau_read.FALLBACK_TAG):
            match = LINE.match(raw)
            assert match, f"not the contract's shape: {raw!r}"
            out.append((match["read"], match["reason"]))
    return out


# ── the two reads the door now serves such a run ────────────────────────────


@pytest.mark.parametrize("head_ref", ["agent/DRE-123-x", "repair/DRE-123-bcb36adf6037"])
def test_a_pull_request_runs_workflow_states_are_sent_to_the_door(monkeypatch, head_ref):
    with FakeIssuer() as issuer, FakeDoor(WORLD) as door:
        _pull_request_run(monkeypatch, door, issuer, head_ref=head_ref)
        read = bureau_read.workflow_states(max_age=bureau_read.WORKFLOW_STATES_MAX_AGE)
    assert read.nodes == [{"id": "s1", "name": "Todo", "type": "unstarted"}]
    assert len(issuer.requests) == 1 and len(door.requests) == 1
    sent = door.requests[0]
    assert sent["path"] == "/api/v1/pipeline/workflow-states"
    assert sent["query"] == {}
    assert sent["headers"]["x-bureau-max-age"] == str(bureau_read.WORKFLOW_STATES_MAX_AGE)
    assert bureau_read.served() == 1 and bureau_read.enabled()


@pytest.mark.parametrize("head_ref", ["agent/DRE-123-x", "repair/DRE-123-bcb36adf6037"])
def test_a_pull_request_runs_own_dependents_are_sent_to_the_door(monkeypatch, head_ref):
    with FakeIssuer() as issuer, FakeDoor(WORLD) as door:
        _pull_request_run(monkeypatch, door, issuer, head_ref=head_ref)
        read = bureau_read.dependents(OWN, max_age=bureau_read.BOARD_MAX_AGE,
                                      lanes=[QUEUED])
    assert [n["identifier"] for n in read.nodes] == [OTHER]
    assert len(issuer.requests) == 1 and len(door.requests) == 1
    sent = door.requests[0]
    assert sent["path"] == f"/api/v1/pipeline/cards/{OWN}/dependents"
    assert sent["query"] == {"lanes": QUEUED, "comments": "50", "relations": "1"}
    assert sent["headers"]["x-bureau-relations-max-age"] == str(bureau_read.RELATIONS_MAX_AGE)
    assert bureau_read.served() == 1 and bureau_read.enabled()


def test_the_own_card_is_matched_whatever_case_the_caller_spells_it(monkeypatch):
    """`dependents` asks for the upper-cased identifier, so that is the one
    compared with the branch's card."""
    with FakeIssuer() as issuer, FakeDoor(WORLD) as door:
        _pull_request_run(monkeypatch, door, issuer)
        bureau_read.dependents(OWN.lower(), max_age=bureau_read.BOARD_MAX_AGE)
    assert [r["path"] for r in door.requests] == [f"/api/v1/pipeline/cards/{OWN}/dependents"]


def test_all_three_reads_go_to_the_door_on_one_run(monkeypatch, capsys):
    with FakeIssuer() as issuer, FakeDoor(WORLD) as door:
        _pull_request_run(monkeypatch, door, issuer)
        bureau_read.cards([OWN], max_age=60, relations=False)
        bureau_read.workflow_states(max_age=bureau_read.WORKFLOW_STATES_MAX_AGE)
        bureau_read.dependents(OWN, max_age=bureau_read.BOARD_MAX_AGE, lanes=[QUEUED])
    assert [r["path"].rsplit("/pipeline", 1)[1] for r in door.requests] == [
        "/cards", "/workflow-states", f"/cards/{OWN}/dependents"]
    assert len(issuer.requests) == 3  # a fresh token every request
    assert bureau_read.served() == 3
    assert _lines(capsys.readouterr().err) == []


# ── Linear Sync's card-done readers (two until DRE-9254), end to end ────────


def test_linear_syncs_lane_lookup_reads_the_door_on_a_merged_pull_request(monkeypatch,
                                                                         capsys):
    """`linear_ops.py card-done`'s lane lookup: the door's states, no fallback."""
    with FakeIssuer() as issuer, FakeDoor(WORLD) as door:
        _pull_request_run(monkeypatch, door, issuer)
        monkeypatch.setenv("GITHUB_WORKFLOW", "Linear Sync")
        monkeypatch.setenv("GITHUB_JOB", "card-done")
        states = linear_ops._door_state_list()
    assert states == [{"id": "s1", "name": "Todo", "type": "unstarted"}]
    assert [r["path"] for r in door.requests] == ["/api/v1/pipeline/workflow-states"]
    assert _lines(capsys.readouterr().err) == []


def test_linear_syncs_merge_sweep_gate_reads_the_door_on_a_merged_pull_request(monkeypatch,
                                                                              capsys):
    """`merge_sweep_gate.py`'s rebuild of the merged card: its own card and its
    dependents, both from the door, and no Linear fallback named. Linear Sync
    no longer runs the gate (DRE-9254); this drives it on a `pull_request` run
    to keep the kept read proved end to end."""
    with FakeIssuer() as issuer, FakeDoor(WORLD) as door:
        _pull_request_run(monkeypatch, door, issuer)
        monkeypatch.setenv("GITHUB_WORKFLOW", "Linear Sync")
        monkeypatch.setenv("GITHUB_JOB", "card-done")
        rebuilt = merge_sweep_gate.door_card(OWN)
    assert rebuilt is not None and rebuilt["identifier"] == OWN
    assert [n["relatedIssue"]["identifier"] for n in rebuilt["relations"]["nodes"]] == [OTHER]
    assert [r["path"] for r in door.requests] == [
        "/api/v1/pipeline/cards", f"/api/v1/pipeline/cards/{OWN}/dependents"]
    assert _lines(capsys.readouterr().err) == []


# ── every other read on that run is still refused ───────────────────────────

_STILL_REFUSED = {
    "another card's dependents": (
        "dependents", lambda: bureau_read.dependents(OTHER, max_age=120, lanes=[QUEUED])),
    "board": ("board", lambda: bureau_read.board([QUEUED], max_age=120)),
    "fleet board": ("board", lambda: bureau_read.board([QUEUED], scope="fleet", max_age=120)),
    "search": ("search", lambda: bureau_read.search(OWN)),
    "repo search": ("search", lambda: bureau_read.search(OWN, scope="repo")),
    "another card": ("cards", lambda: bureau_read.cards([OTHER], max_age=60)),
}


@pytest.mark.parametrize("read, call", list(_STILL_REFUSED.values()), ids=list(_STILL_REFUSED))
def test_every_other_read_is_refused_before_a_token_and_named(monkeypatch, capsys, read,
                                                              call):
    with FakeIssuer() as issuer, FakeDoor(WORLD) as door:
        _pull_request_run(monkeypatch, door, issuer)
        with pytest.raises(bureau_read.ReadUnknown) as caught:
            call()
        assert caught.value.reason == "event-refused" and caught.value.unavailable
        assert door.requests == [] and issuer.requests == []
    assert _lines(capsys.readouterr().err) == [(read, "pull-request")]
    # A refusal by the event is not the door failing: it stays in use.
    assert bureau_read.enabled()


def test_a_refused_read_leaves_the_three_served_reads_to_the_door(monkeypatch, capsys):
    with FakeIssuer() as issuer, FakeDoor(WORLD) as door:
        _pull_request_run(monkeypatch, door, issuer)
        for _, call in _STILL_REFUSED.values():
            with pytest.raises(bureau_read.ReadUnknown):
                call()
        bureau_read.workflow_states(max_age=bureau_read.WORKFLOW_STATES_MAX_AGE)
        bureau_read.dependents(OWN, max_age=bureau_read.BOARD_MAX_AGE)
        bureau_read.cards([OWN], max_age=60)
    assert [r["path"].rsplit("/pipeline", 1)[1] for r in door.requests] == [
        "/workflow-states", f"/cards/{OWN}/dependents", "/cards"]
    assert len(issuer.requests) == 3
    assert [reason for _, reason in _lines(capsys.readouterr().err)] == (
        ["pull-request"] * len(_STILL_REFUSED))


# ── a run that names no card, and pull_request_target: as before ────────────

_THE_TWO = {
    "workflow states": ("workflow-states", lambda: bureau_read.workflow_states(
        max_age=bureau_read.WORKFLOW_STATES_MAX_AGE)),
    "own dependents": ("dependents", lambda: bureau_read.dependents(OWN, max_age=120)),
}


@pytest.mark.parametrize("head_ref", [None, "", "main", "feature/DRE-123-x", "agent/DRE-123",
                                      "agent/dre-123-x", "bot/standards-sync"])
@pytest.mark.parametrize("read, call", list(_THE_TWO.values()), ids=list(_THE_TWO))
def test_a_pull_request_run_whose_branch_names_no_card_is_refused_both(monkeypatch, capsys,
                                                                       head_ref, read, call):
    with FakeIssuer() as issuer, FakeDoor(WORLD) as door:
        _pull_request_run(monkeypatch, door, issuer, head_ref=head_ref)
        with pytest.raises(bureau_read.ReadUnknown) as caught:
            call()
        assert caught.value.reason == "event-refused"
        assert door.requests == [] and issuer.requests == []
    assert bureau_read.disabled_reason() == "event-refused"
    assert _lines(capsys.readouterr().err) == [(read, "pull-request")]


@pytest.mark.parametrize("head_ref", ["agent/DRE-123-x", "repair/DRE-123-abc", None])
@pytest.mark.parametrize("read, call", list(_THE_TWO.values()), ids=list(_THE_TWO))
def test_a_pull_request_target_run_is_refused_both(monkeypatch, head_ref, read, call):
    with FakeIssuer() as issuer, FakeDoor(WORLD) as door:
        _pull_request_run(monkeypatch, door, issuer, head_ref=head_ref,
                          event="pull_request_target")
        with pytest.raises(bureau_read.ReadUnknown) as caught:
            call()
        assert caught.value.reason == "event-refused"
        assert door.requests == [] and issuer.requests == []
    assert bureau_read.disabled_reason() == "event-refused"


# ── the module says which three reads ───────────────────────────────────────


def _source() -> str:
    return (ROOT / "scripts" / "bureau_read.py").read_text()


def test_the_docstrings_pull_request_paragraph_names_the_three_reads():
    doc = ast.get_docstring(ast.parse(_source()))
    start = doc.index("* A `pull_request` or `pull_request_target` run")
    paragraph = doc[start:doc.index("\n\n", start)]
    for read in ("`/cards`", "`/workflow-states`", "`/cards/<id>/dependents`", "DRE-9170"):
        assert read in paragraph, read


def test_the_comment_above_event_env_names_the_three_reads():
    lines = _source().splitlines()
    at = next(i for i, ln in enumerate(lines) if ln.startswith("EVENT_ENV = "))
    start = at
    while lines[start - 1].startswith("#:"):
        start -= 1
    comment = " ".join(ln[2:].strip() for ln in lines[start:at])
    for read in ("`/cards`", "`/workflow-states`", "`/cards/<id>/dependents`", "DRE-9170"):
        assert read in comment, read
