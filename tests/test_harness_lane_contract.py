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
        # The sandbox seat's key (DRE-3651), under the name linear_ops reads —
        # never the fleet's, which every proving run on main used to spend.
        assert "LINEAR_API_KEY: ${{ secrets.LINEAR_API_KEY_SANDBOX }}" in text
        assert "${{ secrets.LINEAR_API_KEY }}" not in text


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


SPEC = {"repo": "o/console", "module": "linear_states", "symbol": "BOARD_STATES"}


def _console_gh(source):
    gh = MagicMock()
    gh.default_branch.return_value = ("main", "s" * 40)
    gh.list_tree.return_value = ["console/backend/linear_states.py"]
    gh.get_file.return_value = source
    return gh


class TestTheConsoleListIsAnExpression:
    """DRE-7050: agent-bureau writes BOARD_STATES as names joined with `+`,
    not as one literal, and the literal-only reader returned None on every
    harness run — the rule never checked anything. agent-bureau was not
    readable from the build, so this fixture follows the expression quoted on
    the card, with QUEUE_LANES written as a `+` join of its own so the nested
    case is covered."""

    LINEAR_STATES_SRC = (
        '"""The console\'s Linear state vocabulary."""\n'
        "from __future__ import annotations\n"
        "\n"
        "import os\n"
        "\n"
        'ACTIVE_STATES = ("Todo", "In Progress", "In Review")\n'
        "\n"
        'HELD_LANES = ["Backlog"]\n'
        'QUEUE_LANES = HELD_LANES + ("Queued",) + ("Hand-work",)\n'
        "\n"
        'PLANNER_STATES: tuple[str, ...] = ("Planning", "Green Light")\n'
        "\n"
        "BOARD_STATES = (\n"
        '    ACTIVE_STATES + ("Intake",) + QUEUE_LANES + ("Triage",) + PLANNER_STATES\n'
        ")\n"
        "\n"
        "\n"
        "def fetch_filter():\n"
        '    return {"state": {"name": {"in": list(BOARD_STATES)}}}\n'
    )

    def test_the_board_list_is_resolved_through_every_name_in_order(self):
        states = scenario_mod.console_states(_console_gh(self.LINEAR_STATES_SRC), SPEC)
        assert states == [
            "Todo", "In Progress", "In Review",
            "Intake",
            "Backlog", "Queued", "Hand-work",
            "Triage",
            "Planning", "Green Light",
        ]

    def test_a_name_is_resolved_from_its_module_level_assignment_only(self):
        # A function-local assignment of the same name is not the module's
        # list; reading it would check the console against the wrong words.
        src = (
            "def helper():\n"
            '    EXTRA = ("Wrong",)\n'
            "    return EXTRA\n"
            'BOARD_STATES = ("Todo",) + EXTRA\n'
        )
        states, reason = scenario_mod.resolve_state_list(src, "BOARD_STATES")
        assert states is None
        assert "EXTRA" in reason


class TestAnUnresolvableListIsUnknown:
    """Anything the reader cannot resolve is None — never [] and never the
    resolvable part — and the reason names the piece it could not resolve."""

    def _resolve(self, src):
        return scenario_mod.resolve_state_list(src, "BOARD_STATES")

    def test_an_imported_name_is_unresolvable(self):
        src = (
            "from console.lanes import QUEUE_LANES\n"
            'BOARD_STATES = ("Todo",) + QUEUE_LANES\n'
        )
        states, reason = self._resolve(src)
        assert states is None
        assert "QUEUE_LANES" in reason and "import" in reason
        assert scenario_mod.console_states(_console_gh(src), SPEC) is None

    def test_a_function_call_is_unresolvable(self):
        src = (
            'ACTIVE_STATES = ("Todo", "In Progress")\n'
            'BOARD_STATES = ACTIVE_STATES + tuple(sorted({"Triage", "Intake"}))\n'
        )
        states, reason = self._resolve(src)
        assert states is None
        assert "tuple(sorted(" in reason
        assert scenario_mod.console_states(_console_gh(src), SPEC) is None

    def test_a_non_string_element_is_unresolvable_not_dropped(self):
        # Today's reader silently drops the 42 and returns the rest — a partial
        # list that would fail lanes for a reason that is not true.
        src = (
            'QUEUE_LANES = ("Backlog", 42, "Queued")\n'
            'BOARD_STATES = ("Todo",) + QUEUE_LANES\n'
        )
        states, reason = self._resolve(src)
        assert states is None
        assert "QUEUE_LANES" in reason and "42" in reason
        assert scenario_mod.console_states(_console_gh(src), SPEC) is None

    def test_a_non_string_element_in_the_plain_literal_is_unresolvable(self):
        src = 'BOARD_STATES = ["Todo", None, "In Progress"]\n'
        assert self._resolve(src)[0] is None
        assert scenario_mod.console_states(_console_gh(src), SPEC) is None

    def test_a_comprehension_is_unresolvable(self):
        src = 'BOARD_STATES = [s for s in ("Todo", "Done")]\n'
        states, reason = self._resolve(src)
        assert states is None
        assert "BOARD_STATES" in reason

    def test_a_name_with_no_assignment_is_unresolvable(self):
        states, reason = self._resolve('BOARD_STATES = ("Todo",) + MISSING\n')
        assert states is None
        assert "MISSING" in reason

    def test_a_cycle_between_names_is_unresolvable(self):
        src = (
            'A = ("Todo",) + B\n'
            'B = ("Done",) + A\n'
            "BOARD_STATES = A\n"
        )
        states, reason = self._resolve(src)
        assert states is None
        assert "cycle" in reason

    def test_a_name_bound_twice_is_unresolvable(self):
        # Which binding a `+` saw depends on statement order; guessing the last
        # one could hand back a list the console never builds.
        src = (
            'A = ("Todo",)\n'
            'BOARD_STATES = A + ("Done",)\n'
            'A = ("Canceled",)\n'
        )
        states, reason = self._resolve(src)
        assert states is None
        assert "A" in reason

    def test_a_missing_symbol_is_unresolvable(self):
        states, reason = self._resolve('OTHER = ("Todo",)\n')
        assert states is None
        assert "BOARD_STATES" in reason

    def test_the_harness_log_names_what_it_could_not_resolve(self, monkeypatch):
        src = (
            "from console.lanes import QUEUE_LANES\n"
            'BOARD_STATES = ("Todo",) + QUEUE_LANES\n'
        )
        lines = []
        monkeypatch.setattr(scenario_mod.contract, "console", lambda: dict(SPEC))
        monkeypatch.setattr(scenario_mod.contract, "pipeline_vocabulary", lambda: set())
        ctx = _ctx(gh_console=_console_gh(src), log=lines.append)
        ctx.state["board"] = {"Todo": 0}
        scenario_mod.SCENARIO.exercise(ctx)
        unread = [line for line in lines if "could not be read" in line]
        assert unread, lines
        assert "QUEUE_LANES" in unread[0]
        assert "UNEVALUATED" in unread[0]
        console = [
            f for f in ctx.state["report"].findings
            if f.clause_id == "console.state_lists_carry_every_lane"
        ]
        assert console and console[0].status == "unevaluated"


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
