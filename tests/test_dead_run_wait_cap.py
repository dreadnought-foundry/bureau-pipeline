"""RED-first tests: a card waits out a vendor or renewal refusal at most twice
in a row — the third such death is a strike again (DRE-7801).

A wait spends no strike (DRE-7800), so without a bound a card that meets the
same refusal run after run would wait forever. The bound is positional, read
off the thread the report script already passes as `--comments-file`: the
build wait markers since the newest `dead-run-requeue` (and inside the newest
`dead-run-budget-reset`). At `WAIT_REPEAT_CAP` of them, a `--wait-kind` death
decides exactly as it would without the flag — a strike and a `model-error:`
marker — and its receipt says why in one sentence. No thread counts as zero
waits, so an unreadable thread never spends a strike on its own.

Run: cd bureau-pipeline && python3 -m pytest tests/test_dead_run_wait_cap.py -v
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")
os.environ.setdefault("GH_TOKEN", "x")

import dead_run  # noqa: E402, RUF100
import vendor_wait  # noqa: E402, RUF100

RUN = "34990000002"
NOW = "2026-10-24T18:00:00Z"
END = datetime(2026, 10, 24, 18, 0, tzinfo=UTC)
MODEL = "claude-opus-4-8"
SENTENCE = "waited twice in a row on a vendor or renewal refusal"


# --------------------------------------------------------------------------
# thread fixtures — every body as the pipeline writes it, never hand-typed
# --------------------------------------------------------------------------
def _wait(kind: str = vendor_wait.VENDOR_TRANSIENT) -> str:
    return dead_run.decide(0, limit=dead_run.wait_death(kind, RUN, END)).comments[0]


def _wall(kind: str, stage: str = "build") -> str:
    return dead_run.limit_marker(kind, stage, END + timedelta(hours=1), RUN)


def _strike() -> str:
    return dead_run.decide(0, is_error=True, error_model=MODEL).comments[0]


def _reset() -> str:
    return dead_run.reset_comment()


def _thread(tmp_path, bodies) -> str:
    path = tmp_path / "comments.json"
    path.write_text(json.dumps(bodies), encoding="utf-8")
    return str(path)


def _decide(*args: str, capsys) -> list[str]:
    rc = dead_run.main(["decide", *args])
    assert rc == 0
    return capsys.readouterr().out.splitlines()


def _wait_death(tmp_path, bodies, capsys, prior_dead: str = "0",
                kind: str = vendor_wait.VENDOR_TRANSIENT) -> list[str]:
    """A wait death as the report script will pass it: an API-error death
    with the card's thread."""
    args = [prior_dead, "--is-error", "--error-model", MODEL,
            "--wait-kind", kind, "--run-id", RUN, "--now", NOW]
    if bodies is not None:
        args += ["--comments-file", _thread(tmp_path, bodies)]
    return _decide(*args, capsys=capsys)


# --------------------------------------------------------------------------
# the contract
# --------------------------------------------------------------------------
def test_wait_repeat_cap_is_two():
    assert dead_run.WAIT_REPEAT_CAP == 2


def test_waits_in_a_row_counts_build_wait_markers():
    assert dead_run.waits_in_a_row([]) == 0
    assert dead_run.waits_in_a_row(None) == 0
    assert dead_run.waits_in_a_row([_wait()]) == 1
    assert dead_run.waits_in_a_row(
        [_wait(vendor_wait.ROTATION), "⏳ 1/5 plan", _wait()]) == 2


# --------------------------------------------------------------------------
# the third wait in a row is a strike
# --------------------------------------------------------------------------
@pytest.mark.parametrize("kind", vendor_wait.KINDS)
def test_a_third_wait_in_a_row_is_a_strike_with_a_model_error_marker(
        kind, tmp_path, capsys):
    out = _wait_death(tmp_path, [_strike(), _wait(), _wait(vendor_wait.ROTATION)],
                      capsys, kind=kind)
    assert out[0] == "requeue"
    body = "\n".join(out[2:])
    assert out[2].startswith(f"🪦 {dead_run.DEAD_TAG}:")
    assert f"{dead_run.ERROR_MARKER_PREFIX} {MODEL}" in body
    assert dead_run.LIMIT_MARK not in body
    assert SENTENCE in body


def test_the_capped_wait_decides_exactly_as_without_the_flag(tmp_path, capsys):
    """Same action and same receipt, with the one sentence added."""
    bodies = [_wait(), _wait()]
    capped = _wait_death(tmp_path, bodies, capsys)
    without = _decide("0", "--is-error", "--error-model", MODEL, "--run-id", RUN,
                      "--now", NOW, "--comments-file", _thread(tmp_path, bodies),
                      capsys=capsys)
    assert capped[0] == without[0] == "requeue"
    added = [line for line in capped if SENTENCE in line]
    assert len(added) == 1
    assert "\n".join(capped).replace(f"\n\n{added[0]}", "") == "\n".join(without)


def test_a_capped_wait_at_the_dead_run_cap_holds(tmp_path, capsys):
    out = _wait_death(tmp_path, [_strike(), _strike(), _wait(), _wait()],
                      capsys, prior_dead="2")
    assert out[0] == "hold"
    body = "\n".join(out[2:])
    assert f"{dead_run.ERROR_MARKER_PREFIX} {MODEL}" in body
    assert SENTENCE in body


# --------------------------------------------------------------------------
# what does not count toward the two — one test each
# --------------------------------------------------------------------------
def test_waits_before_the_newest_strike_do_not_count(tmp_path, capsys):
    bodies = [_wait(), _wait(), _strike(), _wait()]
    assert dead_run.waits_in_a_row(bodies) == 1
    assert _wait_death(tmp_path, bodies, capsys)[0] == "limit"


def test_waits_before_the_newest_budget_reset_do_not_count(tmp_path, capsys):
    bodies = [_wait(), _wait(), _reset(), _wait()]
    assert dead_run.waits_in_a_row(bodies) == 1
    assert _wait_death(tmp_path, bodies, capsys)[0] == "limit"


@pytest.mark.parametrize("kind", ["claude", "linear"])
def test_a_claude_or_linear_limit_marker_does_not_count(kind, tmp_path, capsys):
    bodies = [_wait(), _wall(kind)]
    assert dead_run.waits_in_a_row(bodies) == 1
    assert _wait_death(tmp_path, bodies, capsys)[0] == "limit"


def test_a_wait_marker_from_another_stage_does_not_count(tmp_path, capsys):
    bodies = [_wait(), _wall(vendor_wait.VENDOR_TRANSIENT, stage="fix")]
    assert dead_run.parse_limit_marker(bodies[1])["stage"] == "fix"
    assert dead_run.waits_in_a_row(bodies) == 1
    assert _wait_death(tmp_path, bodies, capsys)[0] == "limit"


# --------------------------------------------------------------------------
# a first and a second wait are still waits; no thread is zero waits
# --------------------------------------------------------------------------
@pytest.mark.parametrize("bodies", [[], [_strike()], [_strike(), _wait()]])
def test_a_first_and_a_second_wait_in_a_row_still_wait(bodies, tmp_path, capsys):
    out = _wait_death(tmp_path, bodies, capsys)
    assert out[0] == "limit"
    assert out[2].startswith(dead_run.LIMIT_MARK)


def test_a_missing_comments_file_is_zero_waits(capsys):
    """Fail-soft, the direction of the rest of `decide`: an unreadable thread
    never spends a strike on its own."""
    out = _decide("0", "--is-error", "--error-model", MODEL,
                  "--wait-kind", "rotation", "--run-id", RUN, "--now", NOW,
                  "--comments-file", "/nonexistent/comments.json", capsys=capsys)
    assert out[0] == "limit"


def test_no_comments_file_at_all_is_zero_waits(tmp_path, capsys):
    assert _wait_death(tmp_path, None, capsys)[0] == "limit"


def test_an_unparseable_comments_file_is_zero_waits(tmp_path, capsys):
    path = tmp_path / "comments.json"
    path.write_text("not json", encoding="utf-8")
    out = _decide("0", "--is-error", "--wait-kind", "vendor-transient",
                  "--run-id", RUN, "--now", NOW, "--comments-file", str(path),
                  capsys=capsys)
    assert out[0] == "limit"


# --------------------------------------------------------------------------
# the other readers of the same file are unchanged
# --------------------------------------------------------------------------
def test_split_tried_and_death_lines_read_the_wait_thread_unchanged():
    strike = _strike()
    bodies = [strike, _wait(), _wait(vendor_wait.ROTATION)]
    assert dead_run.split_tried(bodies) is False
    assert dead_run.death_lines(bodies) == [strike.splitlines()[0]]
