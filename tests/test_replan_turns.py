"""A re-plan gets the turns a real revision takes (DRE-5288).

THE INCIDENT (epic DRE-5268, 2026-09-30). The first critic sent a ten-card plan
back and the planner was given its one revision round. The revision FINISHED —
`is_error: false`, `claude-fable-5-1`, about $12 and 21 minutes — and the
action failed the step anyway:

    Claude reported a successful result after 86 turns,
    exceeding the configured maximum of 60

`planner_capacity.py finish --required` then read "the planner step did not
succeed" and the `finished?` step exited 1. The retry hit the same wall,
because a turn ceiling is DETERMINISTIC, and the epic sat in Planning.

The step carried a literal `--max-turns 60`, and so did the other three re-plan
steps: its re-run on the next rung, and the pair that answers the SECOND
critic. A literal ceiling on work whose size is the size of the plan converts
successes into failures, which is the fault DRE-3241 fixed for the
post-approval review (`post_review_turns`) and DRE-4381 fixed for the one-off
read (`one_off_turns`). This is the same shape, one agent over.

What this file pins:

  * `replan_turns` sizes the budget from the child count — a base plus an
    allowance per card, floored at 60 and capped — and a ten-card epic gets at
    least 100, clear of the 86 turns DRE-5268's revision actually took;
  * an unreadable count is unknown, not zero, and gets the most room rather
    than the floor (standards/console-honesty.md rule 2);
  * the CLI never exits non-zero and always writes a number, because the
    workflow interpolates it into `--max-turns`;
  * every re-plan step in `plan.yml` reads its ceiling from the step that
    sizes it — discovered by name, so a fifth re-plan step that arrives with a
    literal fails here without anybody remembering to add it.

Run: cd bureau-pipeline && python3 -m pytest tests/test_replan_turns.py -v
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github" / "workflows" / "plan.yml"
SCRIPT = REPO / "scripts" / "plan_critic.py"
sys.path.insert(0, str(REPO / "scripts"))

import plan_critic as pc  # noqa: E402

ACTION = "anthropics/claude-code-action"

#: What DRE-5268's revision took, and the ceiling it was failed at — both read
#: off run 36657291632's own result record.
DRE_5268_CHILDREN = 10
DRE_5268_TURNS = 86
DRE_5268_CEILING = 60

#: Every re-plan agent step and the sizing step whose output it reads. The two
#: on the plan route answer the first critic; the two on the activate route
#: answer the second. Each `_retry` is the same step re-run on the next rung
#: (DRE-3970) and takes the same ceiling as the step it re-runs.
SIZED_BY = {
    "replan": "replanturns",
    "replan_retry": "replanturns",
    "postreplan": "postreplanturns",
    "postreplan_retry": "postreplanturns",
}

_TURNS_RE = re.compile(r"--max-turns\b[ \t]*([^\n]*)")
_LITERAL_RE = re.compile(r"--max-turns\s+\d")


def _steps() -> list[dict]:
    doc = yaml.safe_load(WORKFLOW.read_text())
    return [s for job in doc["jobs"].values() for s in job.get("steps") or []]


def _by_id(step_id: str) -> dict:
    for step in _steps():
        if step.get("id") == step_id:
            return step
    raise AssertionError(f"no step id={step_id!r} in plan.yml")


def _index(step_id: str) -> int:
    for i, step in enumerate(_steps()):
        if step.get("id") == step_id:
            return i
    raise AssertionError(f"no step id={step_id!r} in plan.yml")


def _replan_agent_steps() -> dict[str, dict]:
    """Every model step whose name says it is a re-plan, keyed by id."""
    return {
        s.get("id"): s for s in _steps()
        if str(s.get("uses") or "").split("@")[0] == ACTION
        and str(s.get("name") or "").startswith("Re-plan")
    }


def _cli(*args: str) -> tuple[int, str, str]:
    import tempfile
    with tempfile.NamedTemporaryFile("r", suffix=".out") as out:
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "replan-turns", *args,
             "--github-output", out.name],
            capture_output=True, text=True, check=False)
        return proc.returncode, out.read(), proc.stdout


# --------------------------------------------------------------------------- #
# The arithmetic                                                              #
# --------------------------------------------------------------------------- #


class TestTheBudget:
    def test_a_ten_card_revision_gets_at_least_one_hundred_turns(self):
        """The card's criterion, fed the incident's own size."""
        turns = pc.replan_turns(DRE_5268_CHILDREN)
        assert turns >= 100, (
            f"a ten-card re-plan is sized to {turns} turns; DRE-5268's took "
            f"{DRE_5268_TURNS} and was failed at {DRE_5268_CEILING}")
        assert turns == 110

    def test_it_clears_the_revision_that_died_with_headroom(self):
        """A ceiling on top of the one observed run fails the next plan that is
        one card bigger — the raise buys margin, not exactly one run."""
        assert pc.replan_turns(DRE_5268_CHILDREN) >= DRE_5268_TURNS * 1.25

    def test_it_is_a_base_plus_an_allowance_per_card(self):
        assert pc.replan_turns(8) == (pc.REPLAN_TURNS_BASE
                                      + 8 * pc.REPLAN_TURNS_PER_CARD)
        assert pc.replan_turns(9) - pc.replan_turns(8) == pc.REPLAN_TURNS_PER_CARD

    @pytest.mark.parametrize("children", [0, 1, 2, 3, 5, 10, 15, 40])
    def test_nothing_gets_less_than_the_sixty_it_had(self, children):
        assert pc.REPLAN_TURNS_FLOOR >= 60
        assert pc.replan_turns(children) >= pc.REPLAN_TURNS_FLOOR

    def test_a_tiny_plan_gets_the_floor(self):
        assert pc.replan_turns(0) == pc.REPLAN_TURNS_FLOOR
        assert pc.replan_turns(1) == pc.REPLAN_TURNS_FLOOR

    def test_it_is_still_a_ceiling(self):
        """Unbounded is not the fix: the cap is what stops a looping revision
        draining the account, and a revision of a plan that exists is never
        more work than writing it from nothing — the planner's own 140."""
        assert pc.replan_turns(40) == pc.REPLAN_TURNS_CAP
        assert pc.replan_turns(10_000) == pc.REPLAN_TURNS_CAP
        planner = _by_id("claude")["with"]["claude_args"]
        assert pc.REPLAN_TURNS_CAP <= int(
            re.search(r"--max-turns\s+(\d+)", planner).group(1))

    @pytest.mark.parametrize("unknown", [None, "", "  ", "ten", "-1", -3, "1.5"])
    def test_an_unknown_count_is_unknown_not_zero(self, unknown):
        """A Linear read that failed is not an empty plan. It gets the most
        room there is, never the floor a ten-card revision died at."""
        assert pc.replan_turns(unknown) == pc.REPLAN_TURNS_DEFAULT
        assert pc.REPLAN_TURNS_DEFAULT == pc.REPLAN_TURNS_CAP

    def test_the_count_arrives_as_the_text_linear_ops_printed(self):
        assert pc.replan_turns("10") == pc.replan_turns(10)
        assert pc.replan_turns(" 10\n") == pc.replan_turns(10)


class TestTheCommand:
    def test_it_writes_the_sized_ceiling_as_a_step_output(self):
        rc, out, stdout = _cli("--children", "10")
        assert rc == 0
        assert "max_turns=110\n" in out
        assert "110" in stdout

    @pytest.mark.parametrize("children", ["", "unreadable"])
    def test_an_unreadable_count_still_writes_a_number_and_exits_zero(self, children):
        rc, out, _ = _cli("--children", children)
        assert rc == 0
        assert f"max_turns={pc.REPLAN_TURNS_DEFAULT}\n" in out

    def test_no_count_at_all_is_the_default(self):
        rc, out, _ = _cli()
        assert rc == 0
        assert f"max_turns={pc.REPLAN_TURNS_DEFAULT}\n" in out


# --------------------------------------------------------------------------- #
# The rail                                                                    #
# --------------------------------------------------------------------------- #


class TestNoReplanStepCarriesALiteral:
    def test_the_guard_finds_every_re_plan_step(self):
        """A guard that finds nothing passes forever. The four known re-plan
        steps are all there, and no fifth one has arrived unsized."""
        assert set(_replan_agent_steps()) == set(SIZED_BY), (
            f"plan.yml's re-plan model steps are {sorted(_replan_agent_steps())}; "
            f"this test knows {sorted(SIZED_BY)}. A new re-plan step reads its "
            f"ceiling from a sizing step like the others — add it here")

    @pytest.mark.parametrize("step_id", sorted(SIZED_BY))
    def test_no_re_plan_step_carries_a_literal_turn_cap(self, step_id):
        """The criterion, stated as the test that keeps it true."""
        args = str(_replan_agent_steps()[step_id]["with"]["claude_args"])
        found = _TURNS_RE.findall(args)
        assert len(found) == 1, f"plan.yml:{step_id} --max-turns: {found!r}"
        assert not _LITERAL_RE.search(args), (
            f"plan.yml:{step_id} declares a literal `--max-turns`. DRE-5268's "
            f"ten-card revision finished at {DRE_5268_TURNS} turns and was "
            f"failed at a literal {DRE_5268_CEILING}; the ceiling comes from "
            f"`steps.{SIZED_BY[step_id]}.outputs.max_turns`")

    @pytest.mark.parametrize("step_id", sorted(SIZED_BY))
    def test_each_reads_the_ceiling_its_route_sized(self, step_id):
        args = str(_replan_agent_steps()[step_id]["with"]["claude_args"])
        want = f"--max-turns ${{{{ steps.{SIZED_BY[step_id]}.outputs.max_turns }}}}"
        assert want in args, f"plan.yml:{step_id}: {args!r}"


class TestTheSizingSteps:
    @pytest.mark.parametrize("sizer,agent", [("replanturns", "replan"),
                                             ("postreplanturns", "postreplan")])
    def test_it_runs_exactly_when_the_re_plan_does_and_before_it(self, sizer, agent):
        """An output nobody wrote interpolates as a bare `--max-turns`, which
        is a run that never starts. Same gate, earlier position."""
        assert _by_id(sizer).get("if") == _by_id(agent).get("if")
        assert _index(sizer) < _index(agent)

    @pytest.mark.parametrize("sizer", ["replanturns", "postreplanturns"])
    def test_it_sizes_from_the_live_child_count(self, sizer):
        run = str(_by_id(sizer).get("run"))
        assert "linear_ops.py children" in run
        assert "plan_critic.py replan-turns" in run
        assert '--children "$KIDS"' in run
        assert '--github-output "$GITHUB_OUTPUT"' in run

    @pytest.mark.parametrize("sizer", ["replanturns", "postreplanturns"])
    def test_a_failed_sizing_falls_back_to_the_modules_default(self, sizer):
        """The belt to the script's braces, as on `ooturns` and `postturns`:
        the fallback number is the module's own unknown-count default."""
        run = str(_by_id(sizer).get("run"))
        assert f"max_turns={pc.REPLAN_TURNS_DEFAULT}" in run
        assert "::warning::" in run

    @pytest.mark.parametrize("sizer", ["replanturns", "postreplanturns"])
    def test_a_failed_linear_read_is_an_unknown_count(self, sizer):
        run = str(_by_id(sizer).get("run"))
        assert '|| KIDS=""' in run


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
