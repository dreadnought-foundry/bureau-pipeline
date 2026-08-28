"""The harness asserts the lane contract (DRE-2726).

There is already a harness that proves a pipeline commit before the channel
advances, and it never looked at lane movement. This scenario adds that: it
reads the LIVE Linear board, reads the console's state lists where it can
reach them, scans the checkout's own lane vocabulary, and runs the contract's
conformance rules over all three.

It is a READ-ONLY scenario. The harness's standing promise is zero Linear
writes (`scripts/harness/README.md` — "the harness cannot spam real cards
because it never addresses one"), and a conformance check must not be the
thing that breaks it. That promise is pinned below with a Linear client that
raises on any mutation.

It is also in the DEFAULT sweep, unlike the agent scenarios: it costs two API
reads, not a build-agent run, and the whole point is that every trunk commit
is checked.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import lane_contract  # noqa: E402
from harness import framework  # noqa: E402
from harness.scenarios import discover  # noqa: E402
from harness.scenarios import lane_contract as scenario_mod  # noqa: E402


def _ctx(**kw):
    ctx = framework.HarnessContext(
        gh=MagicMock(), repo="o/sandbox", run_id="unit-test", qa_login="qa[bot]"
    )
    for k, v in kw.items():
        setattr(ctx, k, v)
    return ctx


class TestItIsWiredIn:
    def test_the_scenario_is_discovered_by_name(self):
        assert "lane_contract" in discover()

    def test_it_is_in_the_default_sweep(self):
        available = discover()
        from harness.__main__ import select_names

        assert "lane_contract" in select_names(available, [])

    def test_it_does_not_spend_a_build_agent_run(self):
        assert discover()["lane_contract"].requires_agent is False

    def test_the_workflow_gives_it_a_linear_key(self):
        text = (ROOT / ".github" / "workflows" / "harness.yml").read_text(
            encoding="utf-8"
        )
        assert "LINEAR_API_KEY: ${{ secrets.LINEAR_API_KEY }}" in text


class TestItReadsAndNeverWrites:
    def test_the_board_read_uses_a_query_not_a_mutation(self):
        calls = []

        def gql(query, variables=None):
            calls.append(query)
            return {
                "workflowStates": {
                    "nodes": [
                        {"name": "Todo", "issues": {"nodes": [{"identifier": "A-1"}]}},
                    ]
                }
            }

        board = scenario_mod.board_states(gql)
        assert board == {"Todo": 1}
        assert calls and all(q.strip().startswith("query") for q in calls)

    def test_a_mutation_attempt_would_be_caught(self):
        """Non-vacuous guard: the fixture raises on anything that is not a
        query, so a future edit that writes to Linear fails this test."""

        def gql(query, variables=None):
            if not query.strip().startswith("query"):
                raise AssertionError("the harness must never write to Linear")
            return {"workflowStates": {"nodes": []}}

        assert scenario_mod.board_states(gql) == {}


class TestTheConsoleRead:
    CONSOLE_SRC = (
        "# the console's fetch allowlist\n"
        'BOARD_STATES = ["Todo", "In Progress", "Proposed"]\n'
    )

    def test_it_extracts_the_declared_symbol_from_the_declared_module(self):
        gh = MagicMock()
        gh.default_branch.return_value = ("main", "s" * 40)
        gh.list_tree.return_value = [
            "console/backend/app/other.py",
            "console/backend/app/linear_states.py",
        ]
        gh.get_file.return_value = self.CONSOLE_SRC
        states = scenario_mod.console_states(
            gh, {"repo": "o/console", "module": "linear_states", "symbol": "BOARD_STATES"}
        )
        assert states == ["Todo", "In Progress", "Proposed"]

    def test_an_unreachable_console_reads_as_unknown_not_as_agreement(self):
        # console-honesty rule 2: unknown is unknown. Returning [] here would
        # read as "the console lists nothing", which fails every lane for the
        # wrong reason; returning the contract's lanes would be a fake green.
        gh = MagicMock()
        gh.default_branch.side_effect = RuntimeError("404")
        assert scenario_mod.console_states(
            gh, {"repo": "o/console", "module": "linear_states", "symbol": "BOARD_STATES"}
        ) is None

    def test_no_console_client_reads_as_unknown(self):
        assert scenario_mod.console_states(None, {"repo": "o/c"}) is None


class TestTheVerdict:
    def _run(self, ctx, report):
        scenario = scenario_mod.SCENARIO
        ctx.state["report"] = report
        scenario.verify(ctx)

    def test_a_clean_report_passes(self):
        report = lane_contract.Report(
            [lane_contract.Finding("board.every_lane_exists", "pass", "ok")]
        )
        self._run(_ctx(), report)  # no exception

    def test_a_failing_rule_fails_the_scenario_and_names_it(self):
        report = lane_contract.Report(
            [
                lane_contract.Finding(
                    "board.every_state_is_named",
                    "fail",
                    "Linear has 'In Design Review'; the contract does not name it",
                )
            ]
        )
        with pytest.raises(framework.ScenarioFailure) as exc:
            self._run(_ctx(), report)
        assert "In Design Review" in str(exc.value)

    def test_a_report_that_asserted_nothing_is_a_failure(self):
        """A green run that checked nothing is the failure mode this whole
        card exists to prevent."""
        report = lane_contract.Report(
            [lane_contract.Finding("x.y", "skipped", "phase 5")]
        )
        with pytest.raises(framework.ScenarioFailure):
            self._run(_ctx(), report)


class TestTheScenarioTouchesNoSandboxState:
    def test_cleanup_creates_and_deletes_nothing(self):
        gh = MagicMock()
        ctx = _ctx(gh=gh)
        scenario_mod.SCENARIO.cleanup(ctx)
        for forbidden in (
            "create_ref", "put_file", "create_pr", "delete_file", "delete_ref",
            "create_comment", "create_issue",
        ):
            assert not getattr(gh, forbidden).called, f"{forbidden} was called"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
