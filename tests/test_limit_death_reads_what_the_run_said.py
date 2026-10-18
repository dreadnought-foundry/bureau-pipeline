"""RED-first: a failed planning run is recorded as a Claude usage limit only
when the run itself says it hit one (DRE-6985).

`dead_run.limit_kind` searched the WHOLE failed log for the capacity phrases,
and a planner's failed log is mostly not the run speaking. Three kinds of line
are something else:

  * GitHub's echo of each step's script, which opens with the color code
    `[36;1m`. plan.yml's `Select model` step carries the comment "so Fable
    refusing on a monthly spend limit was", so EVERY planner log matched.
  * Step inputs, printed between `##[group]` and `##[endgroup]`.
  * The card's own text, printed between its untrusted-card-text fences — a
    card about usage limits quotes the phrases.

The 14 newest plan-stage markers on agent-bureau and bureau-pipeline cards,
read on 2026-10-11: none was a Claude usage limit. Six were Linear's request
limit, read as `kind=claude` because the Claude test runs first. The waits
those markers bought add up to 100 hours.

The other half: a real usage limit in a planner run prints nothing the
classifier could read except the echoed comment. What the run does print is
its death receipt — `Run death: api_death · wall capped · …` — and that line
now decides it.

What this file pins:

  1. **The two incident logs.** Run 37654166914's echo and its `Run death:
     unknown` receipt read None; run 36967942387's echo, Linear wall and
     receipt read `linear`, and `decide` marks it `kind=linear stage=plan`.
  2. **Echoes, groups and card text are not the run speaking.** Every Claude
     signature on an echoed line, in both spellings of the color code, reads
     None; inside a group or the card fence it reads None, and the same line
     after the close reads `claude`.
  3. **The receipt is the run speaking.** `death_receipt.summary_line` with
     wall `capped` or `throttled` reads `claude`; `revoked` and no wall read
     None; the same text echoed or quoted mid-sentence reads None.
  4. **A real planner usage limit still parks**, with the assumed five-hour
     reset when nothing it printed names one — and a quoted reset sets no
     clock.

Run: cd bureau-pipeline && python3 -m pytest tests/test_limit_death_reads_what_the_run_said.py -v
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import dead_run  # noqa: E402
import death_cause  # noqa: E402
import death_receipt  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"
#: Run 37654166914 (DRE-6139): GitHub's echo of the `Select model` comment and
#: the run's receipt. No model step failed; the marker parked it five hours.
ECHO_ONLY = FIXTURES / "plan-echo-only-37654166914.log"
#: Run 36967942387 (DRE-3624): the same echo, Linear refusing at its request
#: limit, and the receipt. Recorded `kind=claude`; it was Linear's wall.
LINEAR_WALL = FIXTURES / "plan-linear-wall-36967942387.log"
#: A real usage-limit death: 429, a stated reset — the receipt says `capped`.
USAGE_LIMIT = FIXTURES / "usage-limit-death.json"

GH_PREFIX = "call / bureau-card: DRE-0000\tUNKNOWN STEP\t2026-10-05T14:56:58.4319587Z "
#: The echoed plan.yml comment exactly as every planner log carries it.
ECHOED_COMMENT = (
    f"{GH_PREFIX}^[[36;1m# for every model — so Fable refusing on a monthly "
    "spend limit was^[[0m\n"
)
#: The two spellings of GitHub's echo color code: the raw escape byte, and the
#: two characters `^[` that `gh run view --log-failed` hands the medic.
COLOR_CODES = ("\x1b[36;1m", "^[[36;1m")
FENCE_OPEN = "===== BEGIN " + "UNTRUSTED CARD TEXT ====="
FENCE_CLOSE = "===== END " + "UNTRUSTED CARD TEXT ====="


def _printed(*lines: str) -> str:
    return "".join(f"{GH_PREFIX}{line}\n" for line in lines)


def _decide(tmp_path, capsys, text: str, run_id: str, now: str) -> list[str]:
    log = tmp_path / "medic-log.txt"
    log.write_text(text, encoding="utf-8")
    assert dead_run.main([
        "decide", "0", "--limit-log", str(log),
        "--workflow", "Agent Plan", "--run-id", run_id, "--now", now,
    ]) == 0
    return capsys.readouterr().out.splitlines()


# --------------------------------------------------------------------------
# 1. the two incident logs
# --------------------------------------------------------------------------

@pytest.mark.parametrize("path", [ECHO_ONLY, LINEAR_WALL])
def test_the_fixtures_carry_no_person_and_no_sign_in(path):
    text = path.read_text(encoding="utf-8")
    assert "@" not in text
    assert "session_id" not in text.lower()
    assert "token" not in text.lower()
    assert "Run death:" in text
    assert "monthly spend limit" in text


def test_the_echo_only_run_is_not_a_limit():
    assert dead_run.limit_kind(ECHO_ONLY.read_text(encoding="utf-8")) is None


def test_decide_writes_no_marker_for_the_echo_only_run(tmp_path, capsys):
    out = _decide(tmp_path, capsys, ECHO_ONLY.read_text(encoding="utf-8"),
                  "37654166914", "2026-10-07T16:57:22Z")
    assert out[0] != "limit"
    assert not any(dead_run.LIMIT_TAG in line for line in out)


def test_the_linear_wall_run_is_a_linear_limit():
    assert dead_run.limit_kind(LINEAR_WALL.read_text(encoding="utf-8")) == "linear"


def test_decide_marks_the_linear_wall_as_linear(tmp_path, capsys):
    out = _decide(tmp_path, capsys, LINEAR_WALL.read_text(encoding="utf-8"),
                  "36967942387", "2026-10-02T05:13:40Z")
    assert out[0] == "limit"
    assert any(dead_run.LIMIT_MARK in line and "kind=linear stage=plan" in line
               for line in out)


# --------------------------------------------------------------------------
# 2. echoes, groups and card text are not the run speaking
# --------------------------------------------------------------------------

def test_there_are_eight_claude_signatures():
    assert len(dead_run._CLAUDE_LIMIT_SIGNATURES) == 8


@pytest.mark.parametrize("sig", dead_run._CLAUDE_LIMIT_SIGNATURES)
def test_a_printed_signature_reads_claude(sig):
    """The control for the echo test below: the same words, printed."""
    assert dead_run.limit_kind(_printed(f"the run said: {sig}")) == "claude"


@pytest.mark.parametrize("color", COLOR_CODES)
@pytest.mark.parametrize("sig", dead_run._CLAUDE_LIMIT_SIGNATURES)
def test_an_echoed_signature_reads_none(sig, color):
    reset = color.replace("36;1m", "0m")
    assert dead_run.limit_kind(_printed(f"{color}# the run said: {sig}{reset}")) is None


@pytest.mark.parametrize("color", COLOR_CODES)
def test_an_echoed_signature_reads_none_with_no_log_prefix(color):
    assert dead_run.limit_kind(f"{color}echo 'usage limit reached'\n") is None


def test_a_signature_inside_a_group_reads_none():
    text = _printed("##[group]Run anthropics/claude-code-action",
                    "with:", "  prompt: the card says usage limit reached",
                    "##[endgroup]")
    assert dead_run.limit_kind(text) is None


def test_the_same_line_after_the_group_reads_claude():
    text = _printed("##[group]Run anthropics/claude-code-action",
                    "with:", "##[endgroup]",
                    "  prompt: the card says usage limit reached")
    assert dead_run.limit_kind(text) == "claude"


def test_a_signature_inside_the_card_fence_reads_none():
    text = _printed(FENCE_OPEN, "A card about the usage limit reached marker.",
                    FENCE_CLOSE)
    assert dead_run.limit_kind(text) is None


def test_the_same_line_after_the_card_fence_reads_claude():
    text = _printed(FENCE_OPEN, "A card about markers.", FENCE_CLOSE,
                    "A card about the usage limit reached marker.")
    assert dead_run.limit_kind(text) == "claude"


def test_the_linear_test_still_reads_inside_a_group():
    """The Linear half reads the text it read before this card."""
    text = _printed("##[group]Run planner slot",
                    "LinearRateLimited: rate limited: 2500 requests/hour exhausted",
                    "##[endgroup]")
    assert dead_run.limit_kind(text) == "linear"


def test_a_linear_wall_is_not_hidden_behind_an_echoed_signature():
    text = ECHOED_COMMENT + _printed(
        "LinearRateLimited: rate limited: 2500 requests/hour exhausted")
    assert dead_run.limit_kind(text) == "linear"


# --------------------------------------------------------------------------
# 3. the receipt is the run speaking
# --------------------------------------------------------------------------

def _summary(wall: str | None) -> str:
    return death_receipt.summary_line({
        "cause": death_receipt.CAUSE_API,
        "wall": {"cause": wall, "resets_at": None},
        "spend": {"num_turns": 1, "total_cost_usd": 0},
        "cause_reason": "the execution record reports an API/model death (is_error)",
    })


def test_the_walls_are_death_causes_own_names():
    assert (death_cause.CAPPED, death_cause.THROTTLED, death_cause.REVOKED) == (
        "capped", "throttled", "revoked")


@pytest.mark.parametrize("wall", [death_cause.CAPPED, death_cause.THROTTLED])
def test_a_receipt_with_a_usage_wall_reads_claude(wall):
    line = _summary(wall)
    assert f"wall {wall}" in line
    assert dead_run.limit_kind(_printed(line)) == "claude"
    assert dead_run.limit_kind(line) == "claude"


@pytest.mark.parametrize("wall", [death_cause.REVOKED, None])
def test_a_receipt_with_no_usage_wall_reads_none(wall):
    assert dead_run.limit_kind(_printed(_summary(wall))) is None


@pytest.mark.parametrize("color", COLOR_CODES)
def test_an_echoed_receipt_reads_none(color):
    line = _summary(death_cause.CAPPED)
    assert dead_run.limit_kind(_printed(f"{color}echo \"{line}\"")) is None
    assert dead_run.limit_kind(_printed(f"{color}{line}")) is None


def test_a_receipt_quoted_mid_sentence_reads_none():
    line = _summary(death_cause.CAPPED)
    assert dead_run.limit_kind(_printed(f"the last run printed {line}")) is None
    assert dead_run.limit_kind(_printed(f"see `{line}`")) is None


def test_a_receipt_inside_the_card_fence_reads_none():
    text = _printed(FENCE_OPEN, _summary(death_cause.CAPPED), FENCE_CLOSE)
    assert dead_run.limit_kind(text) is None


# --------------------------------------------------------------------------
# 4. a real planner usage limit still parks
# --------------------------------------------------------------------------

def _receipt_stdout(tmp_path, execution: Path, capsys) -> str:
    """What the receipt step prints into the job log for this record."""
    exec_file = tmp_path / "claude-execution-output.json"
    exec_file.write_text(execution.read_text(encoding="utf-8"), encoding="utf-8")
    assert death_receipt.main([
        "emit", "--execution-file", str(exec_file),
        "--out", str(tmp_path / "death-receipt.json"),
        "--job-status", "failure", "--model-ran", "true",
    ]) == 0
    return capsys.readouterr().out


def _as_failed_log(stdout: str) -> str:
    """Printed lines as `gh run view --log-failed` hands them to the medic."""
    return "".join(f"{GH_PREFIX}{line}\n" for line in stdout.splitlines())


@pytest.mark.parametrize("echo", ["", ECHOED_COMMENT], ids=["bare", "with-echo"])
def test_a_real_planner_usage_limit_reads_claude(tmp_path, capsys, echo):
    text = echo + _as_failed_log(_receipt_stdout(tmp_path, USAGE_LIMIT, capsys))
    assert dead_run.limit_kind(text) == "claude"


@pytest.mark.parametrize("echo", ["", ECHOED_COMMENT], ids=["bare", "with-echo"])
def test_decide_parks_a_real_planner_usage_limit(tmp_path, capsys, echo):
    text = echo + _as_failed_log(_receipt_stdout(tmp_path, USAGE_LIMIT, capsys))
    out = _decide(tmp_path, capsys, text, "1", "2026-10-05T15:00:00Z")
    assert out[0] == "limit"
    (marker,) = [line for line in out if line.startswith(dead_run.LIMIT_MARK)]
    assert "kind=claude stage=plan reset=2026-10-05T20:00:00Z" in marker
    assert marker.endswith("assumed=yes")


def test_a_quoted_reset_sets_no_clock(tmp_path, capsys):
    """An echoed or fenced `resets 8:30pm (UTC)` is not the run's reset."""
    text = (_as_failed_log(_receipt_stdout(tmp_path, USAGE_LIMIT, capsys))
            + f"{GH_PREFIX}^[[36;1m# You've hit your limit · resets 8:30pm (UTC)^[[0m\n"
            + _printed(FENCE_OPEN, "It resets 9:30pm (UTC).", FENCE_CLOSE))
    assert dead_run.limit_kind(text) == "claude"
    now = datetime(2026, 10, 5, 15, 0, tzinfo=UTC)
    assert dead_run.limit_reset(text, "claude", now) is None


def test_a_printed_reset_still_sets_the_clock(tmp_path, capsys):
    text = (_as_failed_log(_receipt_stdout(tmp_path, USAGE_LIMIT, capsys))
            + _printed("  result: You've hit your limit · resets 8:30pm (UTC)"))
    now = datetime(2026, 10, 5, 15, 0, tzinfo=UTC)
    assert dead_run.limit_reset(text, "claude", now) == datetime(
        2026, 10, 5, 20, 30, tzinfo=UTC)
