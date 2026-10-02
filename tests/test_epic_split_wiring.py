"""The roll-up route, wired into the plan run (DRE-4718).

A card too big for one epic is a ROLL-UP: the planner splits it into child
epics under itself, each planned and green-lit on its own
(`standards/card-quality.md`, "What the planner files instead"). DRE-4698 gave
`linear_ops.py subissue` its `--epic` flag, DRE-4699 made the vocabulary stamp
`roll-up`, and DRE-4717 wrote `epic_split.py` — the check that a split is a
split, and the activation that records it and places the cards. This card
wires the three together in `plan.yml`, in the place the retired wave branch
stood, and these tests pin that rail:

  1. THE GATE — every `Roll-up route` step is gated on the roll-up shape, and
     no step anywhere is gated on the wave.
  2. THE PROMPT — the agent steps (the planner and its re-run on the next
     rung) tell the planner to create each child with `subissue … --epic`,
     to check its own work with `epic_split.py check`, and to read the
     children that already exist first; they fence the card text with the
     untrusted-content sentinels; and they name no lane.
  3. THE ORDER — the check follows the agent, and the activation follows the
     check: a split is recorded and its cards moved only once it has passed.
  4. THE ACTIVATION — `epic_split.py activate` is handed the card id and
     nothing else. The receipt, the children's lane and the parent's lane are
     all decided inside the script.
  5. THE RECEIPT — the death receipt's `MODEL_RAN` asks both roll-up agent
     steps whether a model ran, in place of the wave's.
  6. THE RETIREMENT — nothing in the file names `wave_plan.py`,
     `wave_commitment.py` or `standards/wave-plan.md`.

Run: cd bureau-pipeline && python3 -m pytest tests/test_epic_split_wiring.py -v
"""

from __future__ import annotations

import re
import shlex
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / ".github" / "workflows" / "plan.yml"
sys.path.insert(0, str(ROOT / "scripts"))

import lane_contract  # noqa: E402

ACTION = "anthropics/claude-code-action"
GATE = "steps.shape.outputs.route == 'roll-up'"
PREFIX = "Roll-up route — "
CARD_ID = "${{ github.event.client_payload.identifier }}"
AGENT_IDS = ("rollup", "rollup_retry")

BEGIN = "===== BEGIN UNTRUSTED CARD TEXT ====="
END = "===== END UNTRUSTED CARD TEXT ====="


def _src() -> str:
    return WF.read_text(encoding="utf-8")


def _steps() -> list:
    doc = yaml.safe_load(_src())
    return list(doc["jobs"]["plan"]["steps"])


def _names() -> list:
    return [s.get("name") or "" for s in _steps()]


def _by_id(step_id: str) -> dict:
    for step in _steps():
        if step.get("id") == step_id:
            return step
    raise AssertionError(f"plan.yml's plan job carries no step with id {step_id!r}")


def _by_name(name: str) -> dict:
    for step in _steps():
        if step.get("name") == name:
            return step
    raise AssertionError(f"plan.yml's plan job carries no step named {name!r}")


def _index(name: str) -> int:
    return _names().index(name)


def _rollup_steps() -> list:
    return [s for s in _steps() if (s.get("name") or "").startswith(PREFIX)]


def _joined(script: str) -> str:
    """Backslash continuations joined, so a wrapped command reads as one."""
    return re.sub(r"\s*\\\n\s*", " ", script)


# ---------------------------------------------------------------------------
# 1. The gate
# ---------------------------------------------------------------------------

class TestTheGate:
    def test_the_route_has_its_steps(self):
        names = {s["name"] for s in _rollup_steps()}
        for expected in (
            "Roll-up route — hand off",
            "Roll-up route — planner context",
            "Roll-up route — split into child epics",
            "Roll-up route — split into child epics — out of capacity?",
            "Roll-up route — split into child epics — on the next rung",
            "Roll-up route — split into child epics — finished?",
            "Roll-up route — check the split",
            "Roll-up route — activate the split",
        ):
            assert expected in names, f"plan.yml has no {expected!r} step"

    def test_every_roll_up_step_is_gated_on_the_roll_up_shape(self):
        steps = _rollup_steps() + [
            _by_id("app_rollup"), _by_id("app_rollup_retry")]
        for step in steps:
            gate = str(step.get("if") or "")
            assert GATE in gate, (
                f"{step.get('name')!r} is not gated on the roll-up shape: `{gate}`")
            assert "'wave'" not in gate

    def test_the_mints_are_named_for_the_roll_up(self):
        assert _by_id("app_rollup")["name"] == "Re-mint bot token — roll-up"
        assert _by_id("app_rollup_retry")["name"] == (
            "Re-mint bot token — Roll-up route — split into child epics on the next rung")

    def test_no_step_is_gated_on_the_wave(self):
        for step in _steps():
            assert "route == 'wave'" not in str(step.get("if") or ""), step.get("name")
        assert "route == 'wave'" not in _src()

    def test_no_wave_step_survives(self):
        for name in _names():
            assert not name.startswith("Wave route"), name
            assert not name.startswith("Wave plan"), name

    def test_the_hand_off_still_exits_through_planning_route(self):
        step = _by_name("Roll-up route — hand off")
        assert step["if"] == GATE
        assert "planning_route.py exit" in _joined(step["run"])
        assert CARD_ID in step["run"]


# ---------------------------------------------------------------------------
# 2. The prompt
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("step_id", AGENT_IDS)
class TestThePrompt:
    def test_it_is_a_model_step_at_eighty_turns(self, step_id):
        step = _by_id(step_id)
        assert str(step.get("uses", "")).startswith(ACTION)
        assert step.get("continue-on-error") is True
        assert "--max-turns 80" in step["with"]["claude_args"]
        assert "github-actions" in step["with"]["allowed_bots"]

    def test_it_creates_each_child_as_an_epic(self, step_id):
        prompt = _joined(_by_id(step_id)["with"]["prompt"])
        assert (
            'python3 .bureau-pipeline/scripts/linear_ops.py subissue '
            f'"{CARD_ID}" "[EPIC] <slug>: <title>" <desc-file> --epic'
        ) in prompt
        assert "--label repo:<slug>" in prompt

    def test_it_checks_its_own_work_with_the_split_check(self, step_id):
        prompt = _joined(_by_id(step_id)["with"]["prompt"])
        assert (
            f'python3 .bureau-pipeline/scripts/linear_ops.py children-detail "{CARD_ID}" '
            f'| python3 .bureau-pipeline/scripts/epic_split.py check --epic "{CARD_ID}"'
        ) in prompt

    def test_it_reads_the_children_that_exist_before_creating_any(self, step_id):
        """Q5: a crash after some children were created leaves them standing,
        and the retry must add only the missing ones."""
        prompt = _by_id(step_id)["with"]["prompt"]
        read = prompt.index("children-detail")
        create = prompt.index("linear_ops.py subissue")
        assert read < create

    def test_it_files_no_build_card_and_no_proof_card(self, step_id):
        prompt = _by_id(step_id)["with"]["prompt"]
        assert "NO build cards and NO proof card" in prompt
        assert "proof_and_demo.py" not in prompt

    def test_it_never_names_the_parent_as_a_blocker(self, step_id):
        prompt = _by_id(step_id)["with"]["prompt"]
        assert "**Blocked by:** DRE-N" in prompt
        assert "Never write the parent's id on that line" in prompt

    def test_it_escalates_through_the_epic_routes_file(self, step_id):
        prompt = _by_id(step_id)["with"]["prompt"]
        assert "${{ runner.temp }}/planner-escalation.txt" in prompt

    def test_the_card_text_is_fenced(self, step_id):
        prompt = _by_id(step_id)["with"]["prompt"]
        assert prompt.count(BEGIN) == 1 and prompt.count(END) == 1
        begin, end = prompt.index(BEGIN), prompt.index(END)
        fenced = prompt[begin:end]
        assert "${{ steps.card.outputs.description }}" in fenced
        assert "standards/untrusted-content.md" in prompt[:begin]
        assert "${{ steps.card.outputs.title }}" in prompt

    def test_it_names_no_lane(self, step_id):
        prompt = _by_id(step_id)["with"]["prompt"]
        for lane in lane_contract.lane_names(status="live"):
            assert lane not in prompt, (
                f"the {step_id} prompt names the lane {lane!r} — the lanes are "
                "epic_split.py's to write")

    def test_it_says_nothing_of_the_wave(self, step_id):
        prompt = _by_id(step_id)["with"]["prompt"]
        assert not re.search(r"\bwaves?\b", prompt, re.I)


def test_the_first_attempt_runs_on_the_selected_model():
    args = _by_id("rollup")["with"]["claude_args"]
    assert "--model ${{ steps.model.outputs.model }}" in args
    assert "${{ steps.model.outputs.fallback_arg }}" in args
    assert "${{ steps.app_rollup.outputs.token }}" == _by_id("rollup")["with"]["github_token"]


def test_the_retry_runs_on_the_next_rung():
    step = _by_id("rollup_retry")
    assert "--model ${{ steps.rollup_cap.outputs.model }}" in step["with"]["claude_args"]
    assert step["with"]["github_token"] == "${{ steps.app_rollup_retry.outputs.token }}"


def test_both_attempts_carry_the_same_prompt_and_tools():
    first, second = _by_id("rollup")["with"], _by_id("rollup_retry")["with"]
    assert first["prompt"] == second["prompt"]
    assert first["allowed_bots"] == second["allowed_bots"]
    tools = re.search(r'--allowedTools "([^"]+)"', first["claude_args"]).group(1)
    assert f'--allowedTools "{tools}"' in second["claude_args"]


# ---------------------------------------------------------------------------
# 3. The order
# ---------------------------------------------------------------------------

class TestTheOrder:
    def test_the_check_follows_the_agent_and_the_activation_follows_the_check(self):
        agent = _index("Roll-up route — split into child epics")
        done = _index("Roll-up route — split into child epics — finished?")
        check = _index("Roll-up route — check the split")
        activate = _index("Roll-up route — activate the split")
        assert agent < done < check < activate

    def test_the_hand_off_precedes_the_planner(self):
        assert _index("Roll-up route — hand off") < _index(
            "Roll-up route — split into child epics")

    def test_the_check_reads_the_children_and_bounces_on_a_finding(self):
        step = _by_name("Roll-up route — check the split")
        run = _joined(step["run"])
        assert "linear_ops.py children-detail" in run
        assert re.search(
            r'epic_split\.py check --epic "\$EPIC" --comment-file "\$BOUNCE_FILE"',
            run)
        # The bounce is posted on the parent, then the run fails.
        assert 'linear_ops.py comment "$EPIC" "$(cat "$BOUNCE_FILE")"' in run
        assert "exit 1" in run
        # It moves nothing: the card stays where the hand-off left it.
        assert "linear_ops.py state" not in run

    def test_a_planner_that_escalated_parks_instead_of_bouncing(self):
        step = _by_name("Roll-up route — check the split")
        run = _joined(step["run"])
        assert "planner-escalation.txt" in run
        assert "planning_escalation.py escalate" in run
        assert run.index("planning_escalation.py escalate") < run.index(
            "epic_split.py check")
        activate = _by_name("Roll-up route — activate the split")
        assert "steps.rollup_check.outputs.escalated != 'true'" in activate["if"]
        assert step.get("id") == "rollup_check"


# ---------------------------------------------------------------------------
# 4. The activation
# ---------------------------------------------------------------------------

def test_the_activation_passes_the_card_id_and_nothing_else():
    run = _joined(_by_name("Roll-up route — activate the split")["run"])
    lines = [ln.strip() for ln in run.splitlines() if "epic_split.py" in ln]
    assert len(lines) == 1, lines
    argv = shlex.split(lines[0])
    assert argv[:3] == ["python3", ".bureau-pipeline/scripts/epic_split.py", "activate"]
    assert argv[3:] == [CARD_ID]


def test_the_activation_hands_linear_its_key():
    step = _by_name("Roll-up route — activate the split")
    # The planner's own bucket first, the fleet key otherwise (DRE-5589).
    assert step["env"]["LINEAR_API_KEY"] == (
        "${{ secrets.LINEAR_PLANNER_KEY || secrets.LINEAR_API_KEY }}"
    )


# ---------------------------------------------------------------------------
# 5. The death receipt
# ---------------------------------------------------------------------------

def test_model_ran_asks_both_roll_up_agent_steps():
    model_ran = _by_id("death")["env"]["MODEL_RAN"]
    for step_id in AGENT_IDS:
        assert f"steps.{step_id}.outcome != 'skipped'" in model_ran
    assert "steps.wave" not in model_ran


# ---------------------------------------------------------------------------
# 6. The retirement
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "retired", ("wave_plan.py", "wave_commitment.py", "standards/wave-plan.md", "🌊"))
def test_nothing_in_the_file_names_a_retired_wave_module(retired):
    assert retired not in _src()
