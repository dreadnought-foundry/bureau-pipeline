"""The scheduled groomer's gate — 06:15 PT, and a card to post to (DRE-4688).

`self-groomer.yml`'s schedule job calls `groomer.yml`, and a job with `uses:`
has no steps — so the two questions a scheduled groom has to answer before it
spends a model call cannot be answered inside it. They are answered here, by
one script a separate gate job runs:

  * IS IT THE MORNING? GitHub's `schedule:` takes UTC only, so the sibling
    carries two cron lines (`15 13 * * *` and `15 14 * * *`). On any given day
    exactly one of them is 06:15 on the `America/Los_Angeles` wall clock and
    the other is an hour off — which one flips at each DST change, so the
    reading is done with `zoneinfo` and never with a restated offset. Both
    tests below run over a January date AND a July date for that reason: a
    gate that reads the offset out of one season is right for half the year.
  * IS THERE A CARD TO POST TO? The standing proposal card, named by the
    `GROOM_PROPOSAL_CARD` repository variable. A proposal posted to a closed
    card is a proposal nobody reads, so the gate refuses LOUDLY — exit 1, a
    red run the medic sees — when the variable is empty or names a card in a
    terminal state.

The refusal is LOUD ONLY INSIDE THE 06:xx HOUR, and that asymmetry is the
point of the last group of tests: the off-hour cron of the pair fires every
single day and must stay a quiet no-op, exit 0, whatever the variable says.
A pair of crons where one of them is red every morning is a pair nobody
reads.

Linear is never reached: every test hands the gate a fake that answers
`get_issue` from a script and RAISES on any other attribute, so a write to
Linear — or a second read nobody intended — fails the test rather than
passing silently.

Run: cd bureau-pipeline && python3 -m pytest tests/test_groom_schedule_gate.py -v
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import groom_schedule_gate as gate  # noqa: E402

UTC = timezone.utc

#: The two cron lines of the pair, as the sibling workflow card will write
#: them: `15 13 * * *` and `15 14 * * *`.
CRONS = ("13:15", "14:15")

#: One standard-time date and one daylight-time date. Both are read on the PT
#: clock, so which cron lands on 06:15 differs between them — that difference
#: IS the test.
JANUARY = "2026-01-15"
JULY = "2026-07-15"


def utc_at(date: str, clock: str) -> datetime:
    """`date` at `clock` UTC, as the runner's clock would read it."""
    return datetime.fromisoformat(f"{date}T{clock}:00+00:00").replace(tzinfo=UTC)


class FakeLinear:
    """A `linear_ops` stand-in that answers ONE question and fails loudly on
    anything else — a write, a second read, a lane move. The gate is specified
    to make exactly one Linear read and no writes, and a fake that quietly
    tolerated more would let that specification rot."""

    def __init__(self, issues: dict | None = None, error: Exception | None = None):
        self._issues = issues or {}
        self._error = error
        self.reads: list[str] = []

    def get_issue(self, identifier: str) -> dict:
        self.reads.append(identifier)
        if self._error is not None:
            raise self._error
        return self._issues[identifier]

    def __getattr__(self, name):                       # pragma: no cover - guard
        raise AssertionError(
            f"the gate reached linear_ops.{name} — it may read one card and "
            "write nothing"
        )


def card(state_name: str, state_type: str) -> dict:
    """A card as `linear_ops.get_issue` returns it, trimmed to the fields the
    gate reads."""
    return {
        "identifier": "DRE-4541",
        "title": "the standing groom proposal card",
        "state": {"name": state_name, "type": state_type},
    }


OPEN_CARD = {"DRE-4541": card("Todo", "unstarted")}
CANCELED_CARD = {"DRE-4541": card("Canceled", "canceled")}
DONE_CARD = {"DRE-4541": card("Done", "completed")}


def run(tmp_path, *, card_arg: str, now: datetime, lops, monkeypatch, capsys):
    """Drive the CLI the way the gate job will, and return
    `(exit code, {output key: value}, stdout)`."""
    out = tmp_path / "github-output"
    monkeypatch.setattr(gate, "linear_ops", lops)
    code = gate.main(
        [
            "--card", card_arg,
            "--now", now.isoformat().replace("+00:00", "Z"),
            "--github-output", str(out),
        ]
    )
    text = out.read_text(encoding="utf-8") if out.exists() else ""
    lines = [line for line in text.splitlines() if line]
    written = dict(line.split("=", 1) for line in lines)
    assert len(lines) == 2, f"expected exactly two output lines, got {lines!r}"
    return code, written, capsys.readouterr().out


# ---------------------------------------------------------------------------
# The clock — both sides of a DST change, pinned
# ---------------------------------------------------------------------------

def test_the_cron_that_is_0615_pt_differs_by_season():
    """13:15 UTC is the morning in July; 14:15 UTC is the morning in January.
    Exactly one cron of the pair is inside the window on each date."""
    assert gate.in_morning_window(utc_at(JULY, "13:15")) is True
    assert gate.in_morning_window(utc_at(JULY, "14:15")) is False

    assert gate.in_morning_window(utc_at(JANUARY, "14:15")) is True
    assert gate.in_morning_window(utc_at(JANUARY, "13:15")) is False


def test_exactly_one_cron_of_the_pair_is_the_morning_on_any_date():
    for date in (JANUARY, JULY):
        inside = [c for c in CRONS if gate.in_morning_window(utc_at(date, c))]
        assert len(inside) == 1, f"{date}: {inside!r} of {CRONS!r} read as 06:xx PT"


def test_the_window_is_the_whole_0600_to_0659_hour():
    # July, PDT: 13:00 UTC is 06:00 PT and 13:59 UTC is 06:59 PT.
    assert gate.in_morning_window(utc_at(JULY, "13:00")) is True
    assert gate.in_morning_window(utc_at(JULY, "13:59")) is True
    assert gate.in_morning_window(utc_at(JULY, "12:59")) is False   # 05:59 PT
    assert gate.in_morning_window(utc_at(JULY, "14:00")) is False   # 07:00 PT


def test_a_naive_timestamp_is_read_as_utc():
    """The CLI's `--now` is documented UTC, and a run that loses the offset
    must not silently be read on the runner's own clock."""
    assert gate.in_morning_window(datetime(2026, 7, 15, 13, 15)) is True
    assert gate.in_morning_window(datetime(2026, 1, 15, 13, 15)) is False


# ---------------------------------------------------------------------------
# The card — read once, judged on its state TYPE
# ---------------------------------------------------------------------------

def test_an_open_card_is_open():
    lops = FakeLinear(OPEN_CARD)
    assert gate.card_is_open(lops, "DRE-4541") == (True, "")
    assert lops.reads == ["DRE-4541"]


@pytest.mark.parametrize("issues,state", [(CANCELED_CARD, "Canceled"), (DONE_CARD, "Done")])
def test_a_terminal_card_is_not_open_and_the_reason_names_the_state(issues, state):
    ok, why = gate.card_is_open(FakeLinear(issues), "DRE-4541")
    assert ok is False
    assert "DRE-4541" in why and state in why


def test_a_card_that_cannot_be_read_is_not_open():
    ok, why = gate.card_is_open(FakeLinear(error=RuntimeError("no such issue")), "DRE-4541")
    assert ok is False
    assert "DRE-4541" in why
    assert "no such issue" in why


def test_an_empty_identifier_is_not_open_and_never_reaches_linear():
    lops = FakeLinear()
    ok, why = gate.card_is_open(lops, "")
    assert ok is False
    assert gate.CARD_VARIABLE in why
    assert lops.reads == []


# ---------------------------------------------------------------------------
# The CLI — go, quiet skip, loud refusal
# ---------------------------------------------------------------------------

def test_inside_the_window_with_an_open_card_it_goes(tmp_path, monkeypatch, capsys):
    code, written, stdout = run(
        tmp_path, card_arg="DRE-4541", now=utc_at(JULY, "13:15"),
        lops=FakeLinear(OPEN_CARD), monkeypatch=monkeypatch, capsys=capsys,
    )
    assert code == 0
    assert written["go"] == "true"
    assert "06:15 PT" in written["why"]
    assert "DRE-4541" in written["why"]
    assert written["why"] in stdout


def test_outside_the_window_it_is_a_quiet_no_op(tmp_path, monkeypatch, capsys):
    """The off-hour cron of the pair fires every day. It exits 0, says so, and
    never reads Linear — the card is not its question."""
    lops = FakeLinear(OPEN_CARD)
    code, written, _ = run(
        tmp_path, card_arg="DRE-4541", now=utc_at(JULY, "14:15"),
        lops=lops, monkeypatch=monkeypatch, capsys=capsys,
    )
    assert code == 0
    assert written["go"] == "false"
    assert "07:15 PT" in written["why"]
    assert lops.reads == []


@pytest.mark.parametrize("card_arg", ["", "DRE-4541"])
def test_outside_the_window_an_absent_card_is_not_a_failure(
    tmp_path, monkeypatch, capsys, card_arg
):
    code, written, _ = run(
        tmp_path, card_arg=card_arg, now=utc_at(JANUARY, "13:15"),
        lops=FakeLinear(CANCELED_CARD), monkeypatch=monkeypatch, capsys=capsys,
    )
    assert code == 0
    assert written["go"] == "false"


def test_inside_the_window_an_empty_card_is_a_loud_refusal(tmp_path, monkeypatch, capsys):
    lops = FakeLinear()
    code, written, stdout = run(
        tmp_path, card_arg="", now=utc_at(JANUARY, "14:15"),
        lops=lops, monkeypatch=monkeypatch, capsys=capsys,
    )
    assert code == 1
    assert written["go"] == "false"
    assert "06:15 PT" in written["why"]
    assert gate.CARD_VARIABLE in written["why"]
    assert written["why"] in stdout
    assert lops.reads == []


@pytest.mark.parametrize("issues,state", [(CANCELED_CARD, "Canceled"), (DONE_CARD, "Done")])
def test_inside_the_window_a_closed_card_is_a_loud_refusal(
    tmp_path, monkeypatch, capsys, issues, state
):
    code, written, _ = run(
        tmp_path, card_arg="DRE-4541", now=utc_at(JANUARY, "14:15"),
        lops=FakeLinear(issues), monkeypatch=monkeypatch, capsys=capsys,
    )
    assert code == 1
    assert written["go"] == "false"
    assert "06:15 PT" in written["why"]
    assert "DRE-4541" in written["why"]
    assert state in written["why"]


def test_inside_the_window_an_unreadable_card_is_a_loud_refusal(tmp_path, monkeypatch, capsys):
    code, written, _ = run(
        tmp_path, card_arg="DRE-4541", now=utc_at(JULY, "13:15"),
        lops=FakeLinear(error=RuntimeError("Linear said 404")),
        monkeypatch=monkeypatch, capsys=capsys,
    )
    assert code == 1
    assert written["go"] == "false"
    assert "DRE-4541" in written["why"]
    assert "Linear said 404" in written["why"]


# ---------------------------------------------------------------------------
# The contract the sibling workflow card keys on
# ---------------------------------------------------------------------------

def test_the_why_is_one_line_whatever_the_card_title_carries(tmp_path, monkeypatch, capsys):
    """`$GITHUB_OUTPUT` is read a LINE at a time and the sibling reads `why`
    off it, so a state name carrying a newline must not become a second key
    (DRE-4202)."""
    hostile = {"DRE-4541": {
        "identifier": "DRE-4541",
        "title": "x",
        "state": {"name": "Cancel\ned\ngo=true", "type": "canceled"},
    }}
    code, written, _ = run(
        tmp_path, card_arg="DRE-4541", now=utc_at(JULY, "13:15"),
        lops=FakeLinear(hostile), monkeypatch=monkeypatch, capsys=capsys,
    )
    assert code == 1
    assert written["go"] == "false"
    assert "\n" not in written["why"]


def test_github_output_defaults_to_the_environment(tmp_path, monkeypatch, capsys):
    out = tmp_path / "from-env"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setattr(gate, "linear_ops", FakeLinear(OPEN_CARD))
    assert gate.main(["--card", "DRE-4541", "--now", utc_at(JULY, "13:15").isoformat()]) == 0
    written = dict(
        line.split("=", 1) for line in out.read_text(encoding="utf-8").splitlines() if line
    )
    assert written["go"] == "true"


def test_no_output_file_is_still_a_decision(monkeypatch, capsys):
    """Run from a terminal with no `$GITHUB_OUTPUT`, the gate still decides and
    still prints its sentence — it does not crash for want of a file."""
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.setattr(gate, "linear_ops", FakeLinear(CANCELED_CARD))
    assert gate.main(["--card", "DRE-4541", "--now", utc_at(JULY, "13:15").isoformat()]) == 1
    assert "DRE-4541" in capsys.readouterr().out


def test_now_defaults_to_the_real_clock(monkeypatch, capsys):
    """No `--now` is the production shape: the gate reads the wall clock."""
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.setattr(gate, "linear_ops", FakeLinear(OPEN_CARD))
    seen: list[datetime] = []

    def spy(when):
        seen.append(when)
        return False

    monkeypatch.setattr(gate, "in_morning_window", spy)
    assert gate.main(["--card", "DRE-4541"]) == 0
    assert len(seen) == 1
    assert seen[0].tzinfo is not None
    assert abs((seen[0] - datetime.now(UTC)).total_seconds()) < 60


def test_the_gate_makes_no_model_call_and_writes_nothing():
    """What a gate must not do, read off its own source: no model call, no
    Linear write, no shelling out. The fake above already proves the Linear
    seam is read-only on every path the CLI takes; this is the half of the
    claim a stub cannot make, because a model call or a `subprocess` would
    never go through it."""
    source = (ROOT / "scripts" / "groom_schedule_gate.py").read_text(encoding="utf-8")
    body = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )
    for forbidden in (
        "anthropic", "ANTHROPIC", "claude", "CLAUDE",
        "linear_ops.comment", "linear_ops.move", "gql(", "subprocess",
    ):
        assert forbidden not in body, f"the gate names {forbidden!r}"
