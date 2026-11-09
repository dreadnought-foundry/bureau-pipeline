"""The Medic's card read asks the read door first (DRE-8900).

`medic_retry.card_facts` is the retry gate's read and the diagnosis
snapshot's: the card's lane, labels and newest fifty comments, and with
`detail` its title and description. It asks `linear_ops.card_via_door` first,
under the job's `BUREAU_READ_CARD_SEEN_AFTER` — the failed run's end — and
builds the same facts from the door's node; on `None` it makes today's one
Linear read.

The scenario tests run the gate through `medic_retry.py decide`, the command
medic.yml's `classify` job runs, with that job's `env:` rendered out of the
YAML (`tests/test_agent_runs_read_door.py`). The failed run ended at T and
posted its park receipt at T minus 5 seconds: a door older than T must not
answer for it.
"""

import contextlib
import io
import json
import sys
import urllib.request
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS.parent / "scripts"))
sys.path.insert(0, str(TESTS))

import dead_run  # noqa: E402
import linear_ops  # noqa: E402
import medic_retry  # noqa: E402
from test_agent_runs_read_door import (  # noqa: E402
    CARD, LinearCard, at, comment, door_at, fallback_lines, new_card, rendered_job_env)

#: The failed run's start and end.
RUN_STARTED = "2026-11-03T17:40:00Z"
RUN_END = "2026-11-03T18:00:00Z"
#: A log the gate retries on its own: no execution record, no stall, no kill.
FLAKE_LOG = TESTS / "fixtures" / "agent-task-infra-flake.log"


@pytest.fixture
def linear(monkeypatch, tmp_path):
    """Linear is the card in `card.json`; nothing in the environment points at
    a door until a test sets the job's env."""
    for name in ("BUREAU_READ", "BUREAU_READ_URL", "BUREAU_READ_AUDIENCE",
                 "BUREAU_PIPELINE_REF", "BUREAU_READ_CARD_MAX_AGE",
                 "BUREAU_READ_CARD_SEEN_AFTER", "ACTIONS_ID_TOKEN_REQUEST_URL",
                 "ACTIONS_ID_TOKEN_REQUEST_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(linear_ops.time, "sleep", lambda _s: None)
    monkeypatch.setenv("LINEAR_IDENTITY", "fleet")
    linear_ops.reset_pass_cache()
    card_file = new_card(tmp_path / "card.json")
    stub = LinearCard(card_file)
    monkeypatch.setattr(urllib.request, "urlopen", stub)
    yield stub
    linear_ops.reset_pass_cache()


def job_env(monkeypatch, job_id: str, door, issuer) -> None:
    for key, value in rendered_job_env("medic.yml", job_id, door=door, issuer=issuer,
                                       run_end=RUN_END).items():
        monkeypatch.setenv(key, value)


def a_busy_card(linear) -> None:
    """Sixty comments, so the fifty-comment window is a real cut, on a card
    in Review with two labels."""
    new_card(linear.path, [comment(f"receipt {i}", at(RUN_END, -3600 + i)) for i in range(60)],
             state="In Review", labels=["repo:portico", "size:S"],
             title="The card", description="What the card does")


# ── the door answers ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("detail", [False, True])
def test_a_fresh_door_answers_the_same_facts_with_no_linear_request(detail, linear,
                                                                    monkeypatch, capsys):
    a_busy_card(linear)
    from_linear = medic_retry.card_facts(CARD, detail=detail)
    assert len(linear.sent) == 1, "the baseline is today's one Linear read"
    linear.sent.clear()
    with door_at(linear.path, at(RUN_END, 2)) as (door, issuer):
        job_env(monkeypatch, "classify", door, issuer)
        from_door = medic_retry.card_facts(CARD, detail=detail)
        (asked,) = door.asked("/cards")
    assert linear.sent == []
    assert from_door == from_linear
    assert len(from_door["comments"]) == linear_ops.COMMENT_WINDOW
    assert asked["query"]["comments"] == str(linear_ops.COMMENT_WINDOW)
    assert fallback_lines(capsys.readouterr().err) == []


def test_an_unknown_door_makes_todays_one_linear_read(linear, monkeypatch, capsys):
    a_busy_card(linear)
    with door_at(linear.path, at(RUN_END, 2)) as (door, issuer):
        door.routes["/cards"] = door.unknown("stale")
        job_env(monkeypatch, "classify", door, issuer)
        facts = medic_retry.card_facts(CARD)
    assert len(linear.sent) == 1
    assert "state { name } labels { nodes { name } }" in linear.sent[0]
    assert facts["state"] == "In Review"
    (line,) = fallback_lines(capsys.readouterr().err)
    assert "reason=stale" in line


def test_with_the_door_off_it_is_todays_read(linear, monkeypatch):
    a_busy_card(linear)
    with door_at(linear.path, at(RUN_END, 2)) as (door, issuer):
        job_env(monkeypatch, "classify", door, issuer)
        monkeypatch.setenv("BUREAU_READ", "off")
        medic_retry.card_facts(CARD)
        assert door.requests == []
    assert len(linear.sent) == 1


# ── the retry gate sees a park posted just before the run ended ─────────────


def _parked(linear) -> None:
    """The failed run held the card at T minus 5 seconds: the queue, and the
    hold's receipt."""
    receipt = f"🛑 {medic_retry.HELD_RECEIPT_MARK}dead-run cap) — this card needs a person"
    new_card(linear.path, [comment("⏳ 1/5 plan", at(RUN_STARTED, 60)),
                           comment(receipt, at(RUN_END, -5))], state="Queued")


def _decide(snapshot: Path) -> str:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = medic_retry.main([
            "decide", "--branch", f"agent/{CARD}-the-card", "--log", str(FLAKE_LOG),
            "--run-started-at", RUN_STARTED, "--run-id", "1", "--run-attempt", "1",
            "--workflow", "Agent Task", "--snapshot", str(snapshot)])
    assert rc == 0
    return out.getvalue()


def test_the_gate_retries_the_log_alone():
    """The premise: on an unparked card this log is retried."""
    decision = medic_retry.decide(execution=medic_retry.execution_from_log(FLAKE_LOG.read_text()))
    assert decision.retry


def test_a_door_older_than_the_runs_end_reads_linear_and_refuses(linear, monkeypatch,
                                                                 capsys, tmp_path):
    _parked(linear)
    with door_at(linear.path, at(RUN_END, -10)) as (door, issuer):
        job_env(monkeypatch, "classify", door, issuer)
        out = _decide(tmp_path / "medic-card.json")
        assert door.asked("/cards"), "the gate never asked the door"
    assert "retry=false" in out
    assert f"rule={medic_retry.RULE_PARKED}" in out
    assert len(linear.thread_reads()) == 1
    (line,) = fallback_lines(capsys.readouterr().err)
    assert "reason=older-than-needed" in line


def test_a_door_at_the_runs_end_refuses_with_no_linear_request(linear, monkeypatch,
                                                               capsys, tmp_path):
    _parked(linear)
    snapshot = tmp_path / "medic-card.json"
    with door_at(linear.path, at(RUN_END, 2)) as (door, issuer):
        job_env(monkeypatch, "classify", door, issuer)
        out = _decide(snapshot)
    assert "retry=false" in out
    assert f"rule={medic_retry.RULE_PARKED}" in out
    assert linear.sent == []
    assert fallback_lines(capsys.readouterr().err) == []
    written = json.loads(snapshot.read_text())
    assert written["card"] == CARD and written["state"] == "Queued"
    assert medic_retry.HELD_RECEIPT_MARK in written["comments"][-1]["body"]


# ── diagnose: the turn-cap witness ──────────────────────────────────────────


def _turn_capped(linear) -> None:
    witness = f"🔁 {dead_run.TURN_TAG}: run 1 reached its turn ceiling"
    new_card(linear.path, [comment("⏳ 1/5 plan", at(RUN_STARTED, 60)),
                           comment(witness, at(RUN_END, -5))])


@pytest.mark.parametrize("offset,from_linear", [(-10, True), (0, False), (2, False)])
def test_the_diagnosis_snapshot_sees_the_turn_cap_witness(offset, from_linear, linear,
                                                          monkeypatch, capsys):
    _turn_capped(linear)
    with door_at(linear.path, at(RUN_END, offset)) as (door, issuer):
        job_env(monkeypatch, "diagnose", door, issuer)
        facts = medic_retry.card_facts(CARD, detail=True)
        assert door.asked("/cards"), "the snapshot never asked the door"
    assert facts["title"] == "a card" and facts["description"] == "what it does"
    assert dead_run.TURN_TAG in medic_retry.turn_receipt(facts["comments"],
                                                         run_started_at=RUN_STARTED)
    lines = fallback_lines(capsys.readouterr().err)
    if from_linear:
        assert len(linear.sent) == 1
        (line,) = lines
        assert "reason=older-than-needed" in line
    else:
        assert linear.sent == []
        assert lines == []
