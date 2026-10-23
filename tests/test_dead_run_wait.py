"""RED-first tests: a build death the reader calls a wait leaves one
limit-death marker instead of a strike, and limit recovery brings the card
back (DRE-7800).

`vendor_wait.kind` (DRE-7795) names two deaths that are nobody's fault —
`rotation`, our own renewal replaced the run's token, and `vendor-transient`,
the model service refused for a moment. `dead_run.py decide --wait-kind
<kind>` turns either into a `LimitDeath` passed through `decide`'s existing
`limit` parameter, so the death takes the limit branch: ONE `🪦 limit-death:`
marker, no budget tag, no `model-error:` marker. Nothing passes the flag yet
(DRE-7802 wires the report script), so no live death changes here.

Run: cd bureau-pipeline && python3 -m pytest tests/test_dead_run_wait.py -v
"""

from __future__ import annotations

import inspect
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")
os.environ.setdefault("GH_TOKEN", "x")

import dead_run  # noqa: E402
import lane_contract  # noqa: E402
import limit_recovery  # noqa: E402
import vendor_wait  # noqa: E402

RUN = "34990000001"
NOW = "2026-10-23T18:00:00Z"
END = datetime(2026, 10, 23, 18, 0, tzinfo=UTC)

#: `decide`'s parameters on `main` before this card, in order. The wait is a
#: way to FILL `limit`, never a parameter of its own.
DECIDE_PARAMS_ON_MAIN = [
    "prior_dead", "is_error", "error_model", "turn_exhaustion", "turn_facts",
    "last_progress", "cancelled", "pre_agent", "failed_step", "rate_limited",
    "credential_expiry", "push_status", "artifact", "run_url", "limit",
    "split_tried", "deaths", "footprint", "cap", "turn_cap",
]

#: The Claude account's usage limit as the result text carries it — a
#: `--limit-log` wall with a stated reset (20:30 UTC).
CLAUDE_LIMIT = (
    '{"type": "result", "subtype": "success", "is_error": true, '
    '"result": "You\'ve hit your limit \\u00b7 resets 8:30pm (UTC)"}'
)


def _decide(*args: str, capsys) -> tuple[list[str], str]:
    rc = dead_run.main(["decide", *args])
    captured = capsys.readouterr()
    assert rc == 0
    return captured.out.splitlines(), captured.err


# --------------------------------------------------------------------------
# the contract
# --------------------------------------------------------------------------
def test_vendor_wait_minutes_is_five():
    assert dead_run.VENDOR_WAIT_MINUTES == 5


def test_decide_signature_is_unchanged():
    assert list(inspect.signature(dead_run.decide).parameters) == DECIDE_PARAMS_ON_MAIN


# --------------------------------------------------------------------------
# --wait-kind: one marker, no strike
# --------------------------------------------------------------------------
def test_vendor_transient_is_a_limit_marker_five_minutes_after_the_run(capsys):
    out, _ = _decide("0", "--is-error", "--error-model", "claude-opus-5-5",
                     "--wait-kind", "vendor-transient", "--run-id", RUN,
                     "--now", NOW, capsys=capsys)
    assert out[0] == "limit"
    assert out[2] == (
        f"🪦 limit-death: kind=vendor-transient stage=build "
        f"reset=2026-10-23T18:05:00Z run={RUN}"
    )
    body = "\n".join(out[2:])
    assert dead_run.DEAD_TAG not in body
    assert dead_run.TURN_TAG not in body
    assert dead_run.ERROR_MARKER_PREFIX not in body


def test_rotation_is_a_limit_marker_at_the_run_end(capsys):
    out, _ = _decide("0", "--is-error", "--error-model", "claude-opus-5-5",
                     "--wait-kind", "rotation", "--run-id", RUN,
                     "--now", NOW, capsys=capsys)
    assert out[0] == "limit"
    assert out[2] == (
        f"🪦 limit-death: kind=rotation stage=build "
        f"reset=2026-10-23T18:00:00Z run={RUN}"
    )
    body = "\n".join(out[2:])
    assert dead_run.DEAD_TAG not in body
    assert dead_run.ERROR_MARKER_PREFIX not in body


def test_the_two_waits_are_different_markers_with_different_resets(capsys):
    rotation, _ = _decide("0", "--wait-kind", "rotation", "--run-id", RUN,
                          "--now", NOW, capsys=capsys)
    transient, _ = _decide("0", "--wait-kind", "vendor-transient", "--run-id", RUN,
                           "--now", NOW, capsys=capsys)
    a = dead_run.parse_limit_marker("\n".join(rotation[2:]))
    b = dead_run.parse_limit_marker("\n".join(transient[2:]))
    assert (a["kind"], b["kind"]) == ("rotation", "vendor-transient")
    assert a["reset"] == END
    assert b["reset"] == END + timedelta(minutes=5)
    assert rotation[2:] != transient[2:]


def test_the_wait_through_decide_wins_over_is_error():
    """The same decision without the CLI: the wait IS a `LimitDeath`, passed
    through the parameter `decide` already has."""
    wait = dead_run.wait_death("vendor-transient", RUN, END)
    assert wait == dead_run.LimitDeath(
        kind="vendor-transient", stage="build",
        reset=END + timedelta(minutes=5), run_id=RUN,
    )
    d = dead_run.decide(2, is_error=True, error_model="claude-opus-5-5", limit=wait)
    assert d.action == "limit"
    assert len(d.comments) == 1
    assert dead_run.DEAD_TAG not in d.comments[0]
    assert dead_run.ERROR_MARKER_PREFIX not in d.comments[0]


def test_wait_death_refuses_a_kind_outside_the_reader():
    assert dead_run.wait_death("claude", RUN, END) is None
    assert dead_run.wait_death("", RUN, END) is None
    for kind in vendor_wait.KINDS:
        assert dead_run.wait_death(kind, RUN, END).kind == kind


@pytest.mark.parametrize("word", ["overloaded", "claude", "linear", "none"])
def test_an_unknown_kind_decides_as_without_the_flag(word, capsys):
    base = ["0", "--is-error", "--error-model", "claude-opus-5-5",
            "--run-id", RUN, "--now", NOW]
    without, _ = _decide(*base, capsys=capsys)
    with_flag, err = _decide(*base, "--wait-kind", word, capsys=capsys)
    assert with_flag == without
    assert with_flag[0] != "limit"
    # refused out loud
    assert "--wait-kind" in err and word in err


def test_cancelled_still_wins_over_the_wait(capsys):
    out, _ = _decide("0", "--cancelled", "--wait-kind", "vendor-transient",
                     "--run-id", RUN, "--now", NOW, capsys=capsys)
    assert out[0] == "defer"
    assert dead_run.LIMIT_MARK not in "\n".join(out)


def test_limit_log_wins_over_the_wait_and_says_so(tmp_path, capsys):
    log = tmp_path / "result.json"
    log.write_text(CLAUDE_LIMIT, encoding="utf-8")
    out, err = _decide("0", "--is-error", "--limit-log", str(log),
                       "--workflow", "Agent Task (reusable)",
                       "--wait-kind", "vendor-transient", "--run-id", RUN,
                       "--now", NOW, capsys=capsys)
    assert out[0] == "limit"
    assert out[2] == (
        f"🪦 limit-death: kind=claude stage=build reset=2026-10-23T20:30:00Z run={RUN}"
    )
    lines = [line for line in err.splitlines() if line.strip()]
    assert len(lines) == 1
    assert "--limit-log" in lines[0] and "--wait-kind" in lines[0]
    assert "claude" in lines[0] and "vendor-transient" in lines[0]


def test_a_limit_log_with_no_wall_leaves_the_wait_to_fill_limit(tmp_path, capsys):
    log = tmp_path / "result.json"
    log.write_text('{"type": "result", "is_error": true, "result": "boom"}',
                   encoding="utf-8")
    out, err = _decide("0", "--is-error", "--limit-log", str(log),
                       "--workflow", "Agent Task (reusable)",
                       "--wait-kind", "rotation", "--run-id", RUN,
                       "--now", NOW, capsys=capsys)
    assert out[0] == "limit"
    assert out[2].startswith("🪦 limit-death: kind=rotation stage=build ")
    assert err.strip() == ""


# --------------------------------------------------------------------------
# the marker: plain words, round trip
# --------------------------------------------------------------------------
def test_the_paragraph_names_each_wall_and_keeps_the_clock():
    rotation = dead_run.limit_marker("rotation", "build", END, RUN)
    transient = dead_run.limit_marker("vendor-transient", "build",
                                      END + timedelta(minutes=5), RUN)
    assert "the access token our own renewal replaced" in rotation
    assert "the model service refusing for a moment (a 529 or 5xx)" in transient
    for body in (rotation, transient):
        assert "Linear's request budget" not in body
        assert f"within {dead_run._clock_hours()} hours" in body
        assert "no strike is spent" in body


@pytest.mark.parametrize("kind", ["vendor-transient", "rotation"])
def test_the_new_kinds_round_trip(kind):
    reset = END + timedelta(minutes=5)
    parsed = dead_run.parse_limit_marker(dead_run.limit_marker(kind, "build", reset, RUN))
    assert parsed == {"kind": kind, "stage": "build", "reset": reset, "run": RUN,
                      "account": None, "assumed": False}


# --------------------------------------------------------------------------
# limit recovery brings it back — scripts/limit_recovery.py unedited
# --------------------------------------------------------------------------
class _Seams:
    def __init__(self):
        self.comments: list[tuple[str, str]] = []
        self.moves: list[tuple[str, str]] = []
        self.lops = MagicMock()
        self.lops.cmd_comment.side_effect = lambda i, b: self.comments.append((i, b))

    def recover(self, cards, now):
        return limit_recovery.recover(
            self.lops, now, None, 8,
            rerun=lambda run_id: True,
            move=lambda ident, lane: self.moves.append((ident, lane)),
            dispatch=lambda c: True,
            cards=cards,
        )


def _card(ident: str, body: str) -> dict:
    return {
        "id": f"id-{ident}",
        "identifier": ident,
        "title": ident,
        "description": "work",
        "updatedAt": "2026-10-23T18:00:00Z",
        "state": {"name": lane_contract.QUEUE_LANE},
        "labels": {"nodes": [{"name": "repo:agent-bureau"}]},
        "comments": {"nodes": [{"body": body,
                                "createdAt": "2026-10-23T18:00:00Z"}]},
    }


def _wait_marker(kind: str) -> str:
    """The marker as the CLI's wait writes it, never hand-typed."""
    return dead_run.decide(0, limit=dead_run.wait_death(kind, RUN, END)).comments[0]


def test_recovery_leaves_the_vendor_wait_alone_before_its_reset():
    s = _Seams()
    s.recover([_card("DRE-1", _wait_marker("vendor-transient"))],
              END + timedelta(minutes=2))
    assert s.moves == []
    assert s.comments == []


@pytest.mark.parametrize("kind", ["rotation", "vendor-transient"])
def test_recovery_brings_each_wait_back_to_the_start_lane_after_its_reset(kind):
    s = _Seams()
    s.recover([_card("DRE-1", _wait_marker(kind))], END + timedelta(minutes=6))
    assert s.moves == [("DRE-1", lane_contract.START_LANE)]
    assert len(s.comments) == 1
    assert s.comments[0][1].startswith(limit_recovery.RECOVERY_MARK)


def test_rotation_comes_back_on_the_first_pass():
    """Its reset is the run's end: the renewed token is already in place."""
    s = _Seams()
    s.recover([_card("DRE-1", _wait_marker("rotation"))], END)
    assert s.moves == [("DRE-1", lane_contract.START_LANE)]
