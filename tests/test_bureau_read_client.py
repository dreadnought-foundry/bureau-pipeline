"""The read door's client, driven through real HTTP against a fake door (Stage 2 BP-2).

`scripts/bureau_read.py` is the one way the pipeline reads the board from the
console instead of Linear. What these pin, each against the failure it exists
to stop:

* the WHOLE answer or `ReadUnknown` — never part of one (a missing field, a
  null lane, an unaffirmed lane, a missing card are all UNKNOWN);
* after ONE failure the door is not asked again this run (design §3);
* `linear-hold` — and a 429 while this process's own key is held — is a SKIP,
  never a fallback onto the held bucket (item 34);
* the mode is read once (item 35); the per-reader max-age is mandatory
  (item 39); connect 1 s / total 8 s and the door's time on the exit line
  (item 40); the token is minted once and again near expiry; it is never sent
  in clear text off this machine; a pipeline_ref the token does not name is
  refused (item 38);
* the shadow comparison's classes (item 36).

No test here reaches the console, GitHub or Linear: the door and the issuer
are `tests/bureau_read_fakes.py`, on 127.0.0.1.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import bureau_read  # noqa: E402
from bureau_read_fakes import FakeDoor, FakeIssuer, card, door_env  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_client(monkeypatch):
    for name in ("BUREAU_READ", "BUREAU_READ_URL", "BUREAU_READ_AUDIENCE",
                 "BUREAU_PIPELINE_REF", "ACTIONS_ID_TOKEN_REQUEST_URL",
                 "ACTIONS_ID_TOKEN_REQUEST_TOKEN", "GITHUB_REPOSITORY"):
        monkeypatch.delenv(name, raising=False)
    bureau_read.reset_for_tests()
    yield
    bureau_read.reset_for_tests()


def _point(monkeypatch, door, issuer, **kw):
    for key, value in door_env(door_url=door.url, issuer=issuer, **kw).items():
        monkeypatch.setenv(key, value)


WORLD = {
    "DRE-1": card("DRE-1", "Todo"),
    "DRE-2": card("DRE-2", "In Progress", labels=("repo:portico",)),
    "DRE-3": card("DRE-3", "Backlog", blockers=(("DRE-1", "Todo"),)),
}


# ── mode ────────────────────────────────────────────────────────────────────


def test_mode_is_read_once_and_printed(monkeypatch, capsys):
    monkeypatch.setenv("BUREAU_READ", "shadow")
    assert bureau_read.mode() == "shadow"
    monkeypatch.setenv("BUREAU_READ", "on")  # flipped mid-run (R11)
    assert bureau_read.mode() == "shadow"
    err = capsys.readouterr().err
    assert err.count("read-door: mode shadow") == 1


def test_the_default_mode_is_off_and_a_typo_is_off(monkeypatch, capsys):
    assert bureau_read.mode() == "off"
    bureau_read.reset_for_tests()
    monkeypatch.setenv("BUREAU_READ", "yes")
    assert bureau_read.mode() == "off"
    assert "not one of off/shadow/on" in capsys.readouterr().err


def test_off_never_sends_a_request(monkeypatch):
    with FakeIssuer() as issuer, FakeDoor(WORLD) as door:
        _point(monkeypatch, door, issuer, mode="off")
        with pytest.raises(bureau_read.ReadUnknown):
            bureau_read.board(["Todo"], max_age=120)
        assert door.requests == [] and issuer.requests == []


# ── the happy path and what it sends ────────────────────────────────────────


def test_board_sends_the_contract_and_returns_whole_nodes(monkeypatch):
    with FakeIssuer() as issuer, FakeDoor(WORLD) as door:
        _point(monkeypatch, door, issuer)
        read = bureau_read.board(["Todo", "In Progress"], max_age=bureau_read.BOARD_MAX_AGE)
    assert sorted(n["identifier"] for n in read.nodes) == ["DRE-1", "DRE-2"]
    sent = door.requests[0]
    assert sent["path"] == "/api/v1/pipeline/board"
    assert sent["query"] == {"lanes": "Todo,In Progress", "scope": "repo", "comments": "50"}
    assert sent["headers"]["authorization"].startswith("Bearer ")
    assert sent["headers"]["x-bureau-max-age"] == "120"
    assert "x-bureau-relations-max-age" not in sent["headers"]
    assert issuer.requests[0]["audience"] == bureau_read.DEFAULT_AUDIENCE
    assert read.as_of == door.as_of and read.viewer_id == "fleet-user"


def test_relations_ask_for_their_own_max_age(monkeypatch):
    with FakeIssuer() as issuer, FakeDoor(WORLD) as door:
        _point(monkeypatch, door, issuer)
        read = bureau_read.board(["Backlog"], max_age=120, relations=True)
    sent = door.requests[0]
    assert sent["query"]["relations"] == "1"
    assert sent["headers"]["x-bureau-relations-max-age"] == str(bureau_read.RELATIONS_MAX_AGE)
    assert read.nodes[0]["inverseRelations"]["nodes"][0]["issue"]["identifier"] == "DRE-1"


def test_the_audience_is_the_configured_one(monkeypatch):
    with FakeIssuer() as issuer, FakeDoor(WORLD) as door:
        _point(monkeypatch, door, issuer, audience="https://door.example/aud")
        bureau_read.board(["Todo"], max_age=120)
    assert issuer.requests[0]["audience"] == "https://door.example/aud"


def test_the_per_reader_constants_are_the_written_ones():
    assert bureau_read.BOARD_MAX_AGE == 120
    assert bureau_read.DISPATCH_CARDS_MAX_AGE == 60
    assert bureau_read.RELATIONS_MAX_AGE == 1200
    assert bureau_read.CONNECT_TIMEOUT == 1.0
    assert bureau_read.TOTAL_TIMEOUT == 8.0


@pytest.mark.parametrize("bad", [None, 0, -5, True, "120"])
def test_a_read_without_a_max_age_is_refused_before_it_is_sent(monkeypatch, bad):
    with FakeIssuer() as issuer, FakeDoor(WORLD) as door:
        _point(monkeypatch, door, issuer)
        with pytest.raises(ValueError):
            bureau_read.board(["Todo"], max_age=bad)
        assert door.requests == []


# ── the token ───────────────────────────────────────────────────────────────


def test_the_token_is_minted_once_per_run(monkeypatch):
    with FakeIssuer() as issuer, FakeDoor(WORLD) as door:
        _point(monkeypatch, door, issuer)
        bureau_read.board(["Todo"], max_age=120)
        bureau_read.board(["In Progress"], max_age=120)
        bureau_read.cards(["DRE-1"], max_age=120)
    assert len(issuer.requests) == 1
    assert len(door.requests) == 3


def test_a_token_near_expiry_is_minted_again(monkeypatch):
    with FakeIssuer(exp=int(time.time()) + 30) as issuer, FakeDoor(WORLD) as door:
        _point(monkeypatch, door, issuer)
        bureau_read.board(["Todo"], max_age=120)
        bureau_read.board(["Todo"], max_age=120)
    assert len(issuer.requests) == 2


def test_no_token_env_falls_back_and_the_door_is_never_asked(monkeypatch):
    with FakeDoor(WORLD) as door:
        _point(monkeypatch, door, None)
        with pytest.raises(bureau_read.ReadUnknown) as caught:
            bureau_read.board(["Todo"], max_age=120)
        assert caught.value.reason == "no-token" and caught.value.unavailable
        assert not caught.value.skip
        assert door.requests == []
        assert not bureau_read.enabled()


def test_an_issuer_refusal_is_a_fallback(monkeypatch):
    with FakeIssuer() as issuer, FakeDoor(WORLD) as door:
        issuer.status = 403
        _point(monkeypatch, door, issuer)
        with pytest.raises(bureau_read.ReadUnknown) as caught:
            bureau_read.board(["Todo"], max_age=120)
    assert caught.value.reason == "no-token"


def test_a_token_for_another_ref_than_the_scripts_is_refused(monkeypatch):
    ref = "dreadnought-foundry/bureau-pipeline/.github/workflows/reconcile.yml@refs/tags/stable"
    with FakeIssuer(job_workflow_ref=ref) as issuer, FakeDoor(WORLD) as door:
        _point(monkeypatch, door, issuer, pipeline_ref="some-branch")
        with pytest.raises(bureau_read.ReadUnknown) as caught:
            bureau_read.board(["Todo"], max_age=120)
        assert caught.value.reason == "pipeline-ref-mismatch"
        assert door.requests == []


@pytest.mark.parametrize("called,pinned,ok", [
    ("refs/tags/stable", "stable", True),
    ("refs/heads/main", "main", True),
    ("refs/tags/stable", "refs/tags/stable", True),
    ("a" * 40, "a" * 40, True),
    ("refs/tags/stable", "main", False),
    ("refs/heads/main", "stable", False),
    ("refs/pull/7/merge", "main", False),
])
def test_pipeline_ref_normalization_table(called, pinned, ok):
    claims = {"job_workflow_ref": f"o/r/.github/workflows/reconcile.yml@{called}"}
    assert (bureau_read.pipeline_ref_problem(claims, pinned) is None) is ok


def test_check_ref_cli_refuses_a_mismatch_and_passes_when_off(monkeypatch, capsys):
    ref = "dreadnought-foundry/bureau-pipeline/.github/workflows/reconcile.yml@refs/tags/stable"
    with FakeIssuer(job_workflow_ref=ref) as issuer, FakeDoor(WORLD) as door:
        _point(monkeypatch, door, issuer, mode="shadow")
        assert bureau_read.main(["x", "check-ref", "stable"]) == 0
        assert bureau_read.main(["x", "check-ref", "feature-x"]) == 1
        assert "refusing to run" in capsys.readouterr().out
        bureau_read.reset_for_tests()
        monkeypatch.setenv("BUREAU_READ", "off")
        assert bureau_read.main(["x", "check-ref", "feature-x"]) == 0
        assert not issuer.requests[2:]  # off mints nothing


def test_check_ref_passes_when_there_is_no_token(monkeypatch, capsys):
    monkeypatch.setenv("BUREAU_READ", "on")
    assert bureau_read.main(["x", "check-ref", "stable"]) == 0
    assert "skipped" in capsys.readouterr().out


def test_the_token_is_never_sent_in_clear_text_off_this_machine(monkeypatch):
    with FakeIssuer() as issuer:
        _point(monkeypatch, type("D", (), {"url": "http://door.example.com"})(), issuer)
        with pytest.raises(bureau_read.ReadUnknown) as caught:
            bureau_read.board(["Todo"], max_age=120)
    assert "https" in caught.value.detail
    assert issuer.requests == []  # refused before a token was even minted


# ── statuses: one failure and the door is done for the run ──────────────────


@pytest.mark.parametrize("status,reason", [
    (401, "refused"), (403, "refused"), (503, "closed"), (500, "unavailable"),
    (429, "throttled"),
])
def test_a_failed_status_stops_the_door_for_the_rest_of_the_run(monkeypatch, status, reason):
    with FakeIssuer() as issuer, FakeDoor(WORLD) as door:
        door.routes["/board"] = (status, {"error": {"code": "X"}})
        _point(monkeypatch, door, issuer)
        with pytest.raises(bureau_read.ReadUnknown) as first:
            bureau_read.board(["Todo"], max_age=120)
        assert first.value.reason == reason and first.value.unavailable
        with pytest.raises(bureau_read.ReadUnknown):
            bureau_read.cards(["DRE-1"], max_age=120)
        assert len(door.requests) == 1  # never asked again
    assert "unavailable 1" in bureau_read.exit_line()


def test_a_404_on_cards_is_unknown_for_that_read_only(monkeypatch):
    with FakeIssuer() as issuer, FakeDoor(WORLD) as door:
        _point(monkeypatch, door, issuer)
        with pytest.raises(bureau_read.ReadUnknown) as caught:
            bureau_read.cards(["DRE-1", "DRE-999"], max_age=120)
        assert caught.value.reason == "not-found" and not caught.value.unavailable
        assert bureau_read.enabled()
        assert bureau_read.cards(["DRE-1"], max_age=120).nodes[0]["identifier"] == "DRE-1"


def test_a_door_that_does_not_answer_in_time_is_a_timeout(monkeypatch):
    monkeypatch.setattr(bureau_read, "TOTAL_TIMEOUT", 0.4)
    with FakeIssuer() as issuer, FakeDoor(WORLD) as door:
        door.routes["/board"] = (200, door.envelope([], lanes=["Todo"]), 1.0)
        _point(monkeypatch, door, issuer)
        started = time.monotonic()
        with pytest.raises(bureau_read.ReadUnknown) as caught:
            bureau_read.board(["Todo"], max_age=120)
        assert time.monotonic() - started < 0.95
    assert caught.value.reason == "timeout"
    assert not bureau_read.enabled()


def test_a_door_nobody_answers_is_unavailable(monkeypatch):
    with FakeIssuer() as issuer:
        _point(monkeypatch, type("D", (), {"url": "http://127.0.0.1:9"})(), issuer)
        with pytest.raises(bureau_read.ReadUnknown) as caught:
            bureau_read.board(["Todo"], max_age=120)
    assert caught.value.unavailable and not caught.value.skip


def test_the_exit_line_counts_and_times_the_door(monkeypatch):
    with FakeIssuer() as issuer, FakeDoor(WORLD) as door:
        door.routes["/cards"] = door.unknown("stale")
        _point(monkeypatch, door, issuer)
        bureau_read.board(["Todo"], max_age=120)
        with pytest.raises(bureau_read.ReadUnknown):
            bureau_read.cards(["DRE-1"], max_age=120)
    line = bureau_read.exit_line()
    assert line.startswith("read-door: served 1 unknown 1 unavailable 0 (mode on) ")
    assert line.endswith("ms")


# ── UNKNOWN: fall back, except on a hold ────────────────────────────────────


def test_unknown_linear_hold_is_a_skip_not_a_fallback(monkeypatch):
    with FakeIssuer() as issuer, FakeDoor(WORLD) as door:
        door.routes["/board"] = door.unknown("linear-hold")
        _point(monkeypatch, door, issuer)
        with pytest.raises(bureau_read.ReadUnknown) as caught:
            bureau_read.board(["Todo"], max_age=120)
    assert caught.value.reason == "linear-hold" and caught.value.skip
    assert bureau_read.enabled()  # the door works; it is Linear that is held


def test_unknown_stale_is_a_fallback(monkeypatch):
    with FakeIssuer() as issuer, FakeDoor(WORLD) as door:
        door.routes["/board"] = door.unknown("stale")
        _point(monkeypatch, door, issuer)
        with pytest.raises(bureau_read.ReadUnknown) as caught:
            bureau_read.board(["Todo"], max_age=120)
    assert caught.value.reason == "stale" and not caught.value.skip


def test_a_429_while_this_processs_key_is_held_is_a_skip(monkeypatch):
    import linear_ops
    with FakeIssuer() as issuer, FakeDoor(WORLD) as door:
        door.routes["/board"] = (429, {"error": {"code": "THROTTLED"}})
        _point(monkeypatch, door, issuer)
        monkeypatch.setitem(linear_ops._budget, "refused_after", 3)
        with pytest.raises(bureau_read.ReadUnknown) as caught:
            bureau_read.board(["Todo"], max_age=120)
    assert caught.value.status == 429 and caught.value.skip


def test_a_429_with_the_key_free_is_a_fallback(monkeypatch):
    import linear_ops
    with FakeIssuer() as issuer, FakeDoor(WORLD) as door:
        door.routes["/board"] = (429, {"error": {"code": "THROTTLED"}})
        _point(monkeypatch, door, issuer)
        monkeypatch.setitem(linear_ops._budget, "refused_after", None)
        with pytest.raises(bureau_read.ReadUnknown) as caught:
            bureau_read.board(["Todo"], max_age=120)
    assert caught.value.status == 429 and not caught.value.skip


# ── never part of an answer ─────────────────────────────────────────────────


def test_fresh_but_older_than_the_reader_allows_is_unknown(monkeypatch):
    with FakeIssuer() as issuer, FakeDoor(WORLD, age=300) as door:
        _point(monkeypatch, door, issuer)
        with pytest.raises(bureau_read.ReadUnknown) as caught:
            bureau_read.board(["Todo"], max_age=120)
    assert caught.value.reason == "stale"


@pytest.mark.parametrize("mutilate", [
    lambda n: n.pop("labels"),
    lambda n: n.pop("updatedAt"),
    lambda n: n.__setitem__("state", None),
    lambda n: n["comments"].pop("pageInfo"),
    lambda n: n["comments"]["nodes"].append({"body": "x"}),
])
def test_a_node_missing_a_field_makes_the_whole_answer_unknown(monkeypatch, mutilate):
    broken = card("DRE-9", "Todo", comments=("hello",))
    mutilate(broken)
    with FakeIssuer() as issuer, FakeDoor(WORLD) as door:
        door.routes["/board"] = (200, door.envelope([WORLD["DRE-1"], broken], lanes=["Todo"]))
        _point(monkeypatch, door, issuer)
        with pytest.raises(bureau_read.ReadUnknown) as caught:
            bureau_read.board(["Todo"], max_age=120)
    assert caught.value.reason == "malformed"


def test_a_blocker_with_a_null_lane_is_unknown_not_promotable(monkeypatch):
    bad = card("DRE-8", "Backlog")
    bad["inverseRelations"]["nodes"] = [
        {"type": "blocks", "issue": {"identifier": "DRE-7", "state": None}}]
    with FakeIssuer() as issuer, FakeDoor({"DRE-8": bad}) as door:
        _point(monkeypatch, door, issuer)
        with pytest.raises(bureau_read.ReadUnknown):
            bureau_read.board(["Backlog"], max_age=120, relations=True)


def test_relations_asked_for_and_not_dated_is_unknown(monkeypatch):
    with FakeIssuer() as issuer, FakeDoor(WORLD, relations_as_of=None) as door:
        _point(monkeypatch, door, issuer)
        with pytest.raises(bureau_read.ReadUnknown):
            bureau_read.board(["Backlog"], max_age=120, relations=True)


def test_a_lane_the_door_does_not_affirm_is_never_an_empty_lane(monkeypatch):
    with FakeIssuer() as issuer, FakeDoor(WORLD) as door:
        door.routes["/board"] = (200, door.envelope([], lanes=["Todo"]))
        _point(monkeypatch, door, issuer)
        with pytest.raises(bureau_read.ReadUnknown) as caught:
            bureau_read.board(["Todo", "In Review"], max_age=120)
    assert caught.value.reason == "malformed"


def test_a_door_that_does_not_hold_a_lane_says_so(monkeypatch):
    with FakeIssuer() as issuer, FakeDoor(WORLD, held_lanes={"Todo"}) as door:
        _point(monkeypatch, door, issuer)
        with pytest.raises(bureau_read.ReadUnknown) as caught:
            bureau_read.board(["Todo", "In QA"], max_age=120)
    assert caught.value.reason == "lane-not-held" and not caught.value.skip


def test_a_card_in_a_lane_not_asked_for_is_unknown(monkeypatch):
    with FakeIssuer() as issuer, FakeDoor(WORLD) as door:
        door.routes["/board"] = (200, door.envelope([WORLD["DRE-2"]], lanes=["Todo"]))
        _point(monkeypatch, door, issuer)
        with pytest.raises(bureau_read.ReadUnknown):
            bureau_read.board(["Todo"], max_age=120)


def test_cards_must_answer_every_card_asked_for(monkeypatch):
    with FakeIssuer() as issuer, FakeDoor(WORLD) as door:
        door.routes["/cards"] = (200, door.envelope([WORLD["DRE-1"]]))
        _point(monkeypatch, door, issuer)
        with pytest.raises(bureau_read.ReadUnknown):
            bureau_read.cards(["DRE-1", "DRE-2"], max_age=120)


def test_an_answer_for_another_repository_is_refused(monkeypatch):
    with FakeIssuer() as issuer, FakeDoor(WORLD, repository="EveryBite/atlas") as door:
        _point(monkeypatch, door, issuer)
        with pytest.raises(bureau_read.ReadUnknown):
            bureau_read.board(["Todo"], max_age=120)


def test_dependents_answers_the_card_in_the_merge_gate_shape(monkeypatch):
    with FakeIssuer() as issuer, FakeDoor(WORLD) as door:
        _point(monkeypatch, door, issuer)
        read = bureau_read.dependents("DRE-1", max_age=120)
    node = read.nodes[0]
    assert node["identifier"] == "DRE-1"
    assert node["relations"]["nodes"][0]["relatedIssue"]["identifier"] == "DRE-3"
    assert door.requests[0]["path"] == "/api/v1/pipeline/cards/DRE-1/dependents"


def test_workflow_states(monkeypatch):
    with FakeIssuer() as issuer, FakeDoor(WORLD) as door:
        _point(monkeypatch, door, issuer)
        read = bureau_read.workflow_states(max_age=3600)
    assert read.nodes == [{"id": "s1", "name": "Todo", "type": "unstarted"}]


# ── the shadow comparison ───────────────────────────────────────────────────


AS_OF = "2026-10-02T20:00:00.000Z"


def test_a_lane_move_after_the_door_snapshot_is_door_older():
    door = [card("DRE-1", "Todo", updated="2026-10-02T19:00:00.000Z")]
    linear = [card("DRE-1", "In Progress", updated="2026-10-02T20:01:00.000Z")]
    diffs = bureau_read.compare(door, linear, door_as_of=AS_OF)
    assert [(d.klass, d.field) for d in diffs] == [("door-older", "lane")]
    assert diffs[0].explained


def test_the_same_stamp_with_a_different_value_is_the_failure():
    door = [card("DRE-1", "Todo", labels=("a",))]
    linear = [card("DRE-1", "Todo", labels=("a", "needs-human"))]
    diffs = bureau_read.compare(door, linear, door_as_of=AS_OF)
    assert [(d.klass, d.field) for d in diffs] == [("same-stamp-different-value", "labels")]
    assert not diffs[0].explained


def test_label_order_is_not_a_difference():
    door = [card("DRE-1", "Todo", labels=("b", "a"))]
    linear = [card("DRE-1", "Todo", labels=("a", "b"))]
    assert bureau_read.compare(door, linear, door_as_of=AS_OF) == []


def test_a_door_newer_than_linear_is_unexplained():
    door = [card("DRE-1", "Todo", updated="2026-10-02T20:05:00.000Z")]
    linear = [card("DRE-1", "In Review", updated="2026-10-02T19:00:00.000Z")]
    diffs = bureau_read.compare(door, linear, door_as_of=AS_OF)
    assert diffs[0].klass == "door-newer" and not diffs[0].explained


def test_a_card_only_linear_holds():
    newer = [card("DRE-5", "Todo", updated="2026-10-02T20:00:30.000Z")]
    older = [card("DRE-5", "Todo", updated="2026-10-02T10:00:00.000Z")]
    assert bureau_read.compare([], newer, door_as_of=AS_OF)[0].klass == "door-older"
    assert bureau_read.compare([], older, door_as_of=AS_OF)[0].klass == \
        "same-stamp-different-value"


def test_relation_differences_are_explained_and_said_so():
    door = [card("DRE-3", "Backlog")]
    linear = [card("DRE-3", "Backlog", blockers=(("DRE-1", "Todo"),))]
    diffs = bureau_read.compare(door, linear, door_as_of=AS_OF)
    assert [(d.klass, d.field) for d in diffs] == [("door-older", "relations")]


def test_report_diffs_counts_only_the_unexplained(capsys):
    diffs = [bureau_read.Diff("door-older", "DRE-1", "lane", "Todo", "In Progress"),
             bureau_read.Diff("same-stamp-different-value", "DRE-2", "labels", [], ["x"])]
    assert bureau_read.report_diffs("board", diffs, 2) == 1
    out = capsys.readouterr().out
    assert "read-door-diff: door-older board DRE-1 lane" in out
    assert "read-door-diff: board compared 2 card(s): 1 explained (door-older), 1 unexplained" in out
