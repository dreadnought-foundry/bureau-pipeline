"""The What's New rule is switched on (DRE-5576).

`config/whats-new-cutover.json` names the instant from which the merge gate
holds a pull request opened without a `What's new:` line. Until this card the
file was absent and every consumer read the rule as off. These tests read the
repository's REAL file — no seam — because the file's existence and shape are
exactly what this card delivers.

The harness half: bureau-harness runs the critic and the merge gate at `main`,
and its default sweep opens `agent/harness-…` pull requests, which owe a line
(`whats_new.required_for`). A harness pull request opened after the cutover
without one is sent back by the critic and held by the gate, the sweep goes
red, and `stable` stops advancing for the whole fleet. So every harness body
the default sweep opens says `What's new: none` — the probe is a markdown
record nobody using a product sees.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import whats_new  # noqa: E402

CUTOVER = ROOT / "config" / "whats-new-cutover.json"


def _data() -> dict:
    return json.loads(CUTOVER.read_text())


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


class TestTheFile:
    def test_the_file_exists_where_the_module_reads_it(self):
        assert whats_new.CUTOVER_FILE == CUTOVER
        assert CUTOVER.is_file(), "config/whats-new-cutover.json is absent: the rule is off"

    def test_it_carries_exactly_the_two_keys(self):
        assert set(_data()) == {"enforced_from", "why"}

    def test_the_instant_is_an_aware_utc_instant(self):
        raw = _data()["enforced_from"]
        assert isinstance(raw, str)
        moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        assert moment.utcoffset() == timedelta(0)

    def test_the_why_is_one_non_empty_sentence(self):
        why = _data()["why"]
        assert isinstance(why, str) and why.strip()
        assert why.strip().endswith(".")


class TestTheSwitchReadsIt:
    def test_enforced_from_returns_the_files_instant(self):
        raw = _data()["enforced_from"]
        expected = datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
        assert whats_new.enforced_from() == expected

    def test_a_minute_before_is_spared_and_a_minute_after_is_held(self):
        cutover = whats_new.enforced_from()
        assert cutover is not None
        assert whats_new.enforced_for(_iso(cutover - timedelta(minutes=1))) is False
        assert whats_new.enforced_for(_iso(cutover + timedelta(minutes=1))) is True

    def test_the_rule_is_on(self):
        assert whats_new.enforced_for(None) is True

    def test_the_instant_is_never_in_the_future(self):
        # A future instant would spare pull requests opened after the rule
        # was taught (DRE-5510); the instant is the moment the file was written.
        assert whats_new.enforced_from() <= datetime.now(timezone.utc)

    def test_the_example_file_still_validates(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "whats_new.py"), "validate",
             str(ROOT / "docs" / "whats-new.example.json")],
            capture_output=True, text=True)
        assert result.returncode == 0, result.stderr


class TestTheHarnessCarriesTheLine:
    """The default sweep's pull requests owe a line and say `none`."""

    def _bodies(self):
        from harness import framework
        from harness.scenarios import bot_pr_flow, gate_paths

        run = "gha-9-9"
        yield (framework.scenario_branch(run, bot_pr_flow.SCENARIO.name),
               bot_pr_flow.pr_body(run))
        for leg in gate_paths.LEGS:
            yield gate_paths.leg_branch(run, leg), gate_paths.pr_body(run, leg)

    def test_every_harness_body_says_none(self):
        for branch, body in self._bodies():
            assert whats_new.parse_line(body) is None, branch

    def test_the_line_opens_the_body(self):
        for branch, body in self._bodies():
            assert body.splitlines()[0] == "What's new: none", branch

    def test_the_agent_heads_owe_the_line(self):
        # Why the line is needed at all: the skew, stale and bot_pr_flow heads
        # are `agent/harness-…`, which the gate and the critic hold to it.
        owed = [branch for branch, _ in self._bodies() if whats_new.required_for(branch)]
        assert len(owed) == 3, owed
