"""Agent Fix's hold read asks the read door first (DRE-8900).

`hold._read_card` reads a card's labels and its newest fifty comments, for
`hold.py reason` and for a lift that reads the live stamp. In Agent Fix's
`fix` job — whose `env:` is rendered out of agent-fix.yml here, and whose
floor is the job's own start (`tests/test_agent_runs_read_door.py`) — it asks
`linear_ops.card_via_door` first and builds the same answer from the door's
node. On `None` it makes today's one Linear read.
"""

import sys
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS.parent / "scripts"))
sys.path.insert(0, str(TESTS))

import hold  # noqa: E402
import linear_ops  # noqa: E402
from test_agent_runs_read_door import (  # noqa: E402
    CARD, LinearCard, at, comment, door_at, fallback_lines, iso, new_card, rendered_job_env)

#: The fix job's start, as its first step notes it.
JOB_START = iso(datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=1))


@pytest.fixture
def linear(monkeypatch, tmp_path):
    for name in ("BUREAU_READ", "BUREAU_READ_URL", "BUREAU_READ_AUDIENCE",
                 "BUREAU_PIPELINE_REF", "BUREAU_READ_CARD_MAX_AGE",
                 "BUREAU_READ_CARD_SEEN_AFTER", "ACTIONS_ID_TOKEN_REQUEST_URL",
                 "ACTIONS_ID_TOKEN_REQUEST_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(linear_ops.time, "sleep", lambda _s: None)
    monkeypatch.setenv("LINEAR_IDENTITY", "fleet")
    linear_ops.reset_pass_cache()
    stamp = hold.stamp_line("unfixable-check", "0123abcd" * 5, "agent-fix.yml")
    card_file = new_card(
        tmp_path / "card.json",
        [comment(f"receipt {i}", at(JOB_START, -7200 + i)) for i in range(55)]
        + [comment(stamp, at(JOB_START, -60))],
        labels=[hold.HOLD_LABEL, "repo:portico"])
    stub = LinearCard(card_file)
    monkeypatch.setattr(urllib.request, "urlopen", stub)
    yield stub
    linear_ops.reset_pass_cache()


def fix_job_env(monkeypatch, door, issuer) -> None:
    for key, value in rendered_job_env("agent-fix.yml", "fix", door=door,
                                       issuer=issuer).items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv(linear_ops.CARD_SEEN_AFTER_ENV, JOB_START)


def test_a_fresh_door_answers_the_same_labels_and_comments(linear, monkeypatch, capsys):
    from_linear = hold._read_card(CARD)
    assert len(linear.sent) == 1, "the baseline is today's one Linear read"
    linear.sent.clear()
    with door_at(linear.path, at(JOB_START, 30)) as (door, issuer):
        fix_job_env(monkeypatch, door, issuer)
        from_door = hold._read_card(CARD)
        (asked,) = door.asked("/cards")
    assert linear.sent == []
    assert from_door == from_linear
    labels, bodies = from_door
    assert hold.HOLD_LABEL in labels
    assert len(bodies) == linear_ops.COMMENT_WINDOW
    assert hold.reason_of(labels, bodies) == "unfixable-check"
    assert asked["query"]["comments"] == str(linear_ops.COMMENT_WINDOW)
    assert fallback_lines(capsys.readouterr().err) == []


def test_with_the_door_off_it_is_todays_read(linear, monkeypatch):
    with door_at(linear.path, at(JOB_START, 30)) as (door, issuer):
        fix_job_env(monkeypatch, door, issuer)
        monkeypatch.setenv("BUREAU_READ", "off")
        labels, _bodies = hold._read_card(CARD)
        assert door.requests == []
    assert hold.HOLD_LABEL in labels
    assert len(linear.sent) == 1
    assert "labels { nodes { name } }" in linear.sent[0]


def test_a_door_older_than_the_jobs_start_reads_linear(linear, monkeypatch, capsys):
    with door_at(linear.path, at(JOB_START, -30)) as (door, issuer):
        fix_job_env(monkeypatch, door, issuer)
        _labels, bodies = hold._read_card(CARD)
        assert door.asked("/cards")
    assert len(linear.sent) == 1
    assert hold.read_stamp(bodies)
    (line,) = fallback_lines(capsys.readouterr().err)
    assert "reason=older-than-needed" in line
