"""RED-first tests for DRE-5573 — the model adoption workflow's pull requests
carry `What's new: none`.

The adoption workflow opens its pull requests on `agent/` branches —
`agent/<card>-adopt-<candidate>`, `agent/model-adoption-<candidate>` and
`agent/claude-code-pin-<version>` — which the What's New gate does not exempt
(`standards/whats-new.md`, DRE-5508). No brief reaches the two body renderers,
so they write the line themselves: `model_adoption_actions.pr_body` and
`claude_code_pin.pr_body` open with `What's new: none` and a blank line.

THE TRAPS THESE TESTS PIN.

  * **The parser is the oracle.** Each rendered body goes through DRE-5508's
    `whats_new.parse_line`, which must return `None` (the `none` form) — not
    raise — so the renderers and the parser cannot drift apart.
  * **The line is FIRST**, above everything else the body carries, and the old
    body follows unchanged after one blank line.
  * **Through the CLI too** — the workflow renders by `render pr-body`, so the
    line is checked on what the scripts actually print.

Run: cd bureau-pipeline && python3 -m pytest tests/test_model_adoption_whats_new.py -q
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import claude_code_pin as ccp  # noqa: E402
import model_adoption as ma  # noqa: E402
import model_adoption_actions as maa  # noqa: E402
import model_catalog as mc  # noqa: E402
from whats_new import parse_line  # noqa: E402

LINE = "What's new: none"
SNAPSHOT_PATH = ROOT / "models.json"

SONNET6 = "claude-sonnet-6"
TRIAL = {
    "model": SONNET6,
    "outcome": "passed",
    "run_url": "https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/456",
    "summary": "trial card DRE-1 built green in 41 turns; tests 12/12",
}

PIN = {
    "model": "claude-sonnet-5-5", "answer": "found", "why": "",
    "from": {"claude_code": "2.1.282", "action": "v1.0.234",
             "sha": "9171db3e57d6a3140a37ddc2ba92788584e0ead6"},
    "to": {"claude_code": "2.1.284", "action": "v1.0.236",
           "sha": "8ce9314fa9a404564fa7e954cd84f25bcba2b829"},
    "limits": {"max_input_tokens": 1_000_000, "max_tokens": 128_000},
    "listed": {"context_window": 1_000_000, "max_output_tokens": 128_000},
}
PIN_TRIAL = {
    "model": "claude-sonnet-5-5", "outcome": "degraded",
    "run_url": "https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36479502126",
    "summary": "claude-sonnet-5-5: degraded — ran below full strength",
}


@pytest.fixture(scope="module")
def adopt_sonnet6():
    """An `adopt` Decision from the real classifier, as in
    tests/test_model_adoption_actions.py — never a hand-written record."""
    snap = json.loads(SNAPSHOT_PATH.read_text())
    snap["models"] = [{"id": SONNET6, "display_name": "Claude Sonnet 6",
                       "created_at": "2026-11-01T00:00:00Z",
                       "in_catalog": True}] + list(snap["models"])
    prices = ma.load_prices()
    prices[SONNET6] = {"input": 2.0, "output": 10.0}
    found = ma.classify_catalog(mc.snapshot_catalog(snap), ma.load_config(), prices)
    (d,) = [json.loads(json.dumps(d)) for d in found if d["candidate"] == SONNET6]
    assert d["rule"] == ma.RULE_ADOPT, d
    return d


def _write(tmp_path, name, data) -> str:
    path = tmp_path / name
    path.write_text(json.dumps(data))
    return str(path)


def _cli(*argv) -> str:
    done = subprocess.run([sys.executable, *argv], cwd=ROOT,
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return done.stdout


def _assert_opens_with_the_line(body: str) -> str:
    """The body's first line is the `none` line, a blank line follows, and
    the parser reads it as `none`. Returns what follows the blank line."""
    lines = body.split("\n")
    assert lines[0] == LINE, lines[:3]
    assert lines[1] == "", lines[:3]
    assert lines[2].strip(), "one blank line, then the body as it was"
    assert parse_line(body) is None
    return "\n".join(lines[2:])


# --------------------------------------------------------------------------- #
# model_adoption_actions.pr_body                                               #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("trial", [None, TRIAL], ids=["no-trial", "trial"])
def test_the_adoption_body_opens_with_whats_new_none(adopt_sonnet6, trial):
    _assert_opens_with_the_line(maa.pr_body(adopt_sonnet6, trial))


@pytest.mark.parametrize("trial", [None, TRIAL], ids=["no-trial", "trial"])
def test_the_adoption_body_follows_the_line_unchanged(adopt_sonnet6, trial):
    rest = _assert_opens_with_the_line(maa.pr_body(adopt_sonnet6, trial))
    assert rest.startswith(maa._summary(adopt_sonnet6))
    assert LINE not in rest, "exactly one line"


def test_the_adoption_cli_prints_the_line_first(tmp_path, adopt_sonnet6):
    out = _cli("scripts/model_adoption_actions.py", "render", "pr-body",
               "--decision", _write(tmp_path, "d.json", adopt_sonnet6))
    _assert_opens_with_the_line(out)


def test_the_adoption_cli_prints_the_line_first_with_a_trial(tmp_path, adopt_sonnet6):
    out = _cli("scripts/model_adoption_actions.py", "render", "pr-body",
               "--decision", _write(tmp_path, "d.json", adopt_sonnet6),
               "--trial", _write(tmp_path, "t.json", TRIAL))
    _assert_opens_with_the_line(out)


def test_the_record_card_body_gains_no_line(adopt_sonnet6):
    # The Linear record card is a card, not a pull request.
    assert "What's new" not in maa.record_body(adopt_sonnet6)


# --------------------------------------------------------------------------- #
# claude_code_pin.pr_body                                                      #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("trial", [None, PIN_TRIAL], ids=["no-trial", "trial"])
def test_the_pin_body_opens_with_whats_new_none(trial):
    rest = _assert_opens_with_the_line(ccp.pr_body(PIN, trial))
    assert rest.startswith(f"`{PIN['model']}` needs a newer Claude Code")
    assert LINE not in rest, "exactly one line"


def test_the_pin_cli_prints_the_line_first(tmp_path):
    out = _cli("scripts/claude_code_pin.py", "render", "pr-body",
               "--pin", _write(tmp_path, "pin.json", PIN))
    _assert_opens_with_the_line(out)


def test_the_pin_cli_prints_the_line_first_with_a_trial(tmp_path):
    out = _cli("scripts/claude_code_pin.py", "render", "pr-body",
               "--pin", _write(tmp_path, "pin.json", PIN),
               "--trial", _write(tmp_path, "t.json", PIN_TRIAL))
    _assert_opens_with_the_line(out)
