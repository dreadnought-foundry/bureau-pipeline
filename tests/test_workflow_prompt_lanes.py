"""Inline agent prompts are agent instructions too (DRE-2727).

`briefs/` and `standards/` are the obvious surface. They miss a whole class:
prompts written in prose inside `.github/workflows/`. Nothing that checks a
brief reaches them, and nothing that checks a lane name reaches a prompt.

The live example this card was opened on: medic.yml told its agent to search
Linear for an existing failure card in Triage, Todo or In Progress before
creating a new one. Point the create seam at a lane that is not on that list
and a repeatedly-failing workflow mints a fresh card on every failure instead
of commenting on the one that already exists.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import check_workflow_prompts as cwp  # noqa: E402

WORKFLOWS = ROOT / ".github" / "workflows"

# Named, not counted: the point of the criterion is that the list is written
# down so the next person does not rediscover it. A prompt added to a workflow
# that is not here fails this test and has to be classified deliberately.
EXPECTED_PROMPT_FILES = {
    "agent-fix.yml": 1,
    # Three since DRE-4108: the agent step plus two retries for a GitHub
    # rate-limit refusal that arrives before the model starts. The three
    # prompts are byte-identical — tests/test_presubmit_gate_prompt.py
    # asserts that — so this is one prompt shipped three times, and each
    # copy is checked for live lane names here like any other.
    "agent-task.yml": 3,
    # DRE-4972: the groom verify agent, one per proposed card.
    "groomer.yml": 1,
    "medic.yml": 1,
    # DRE-3897: the model trial's prompt. It names no lane and no card — it is
    # a fixed, checkable task on a candidate model — but it is an inline agent
    # prompt like any other and belongs in the enumeration.
    "model-trial.yml": 1,
    # 6 → 7 with DRE-3041: the pre-approval critic reads the one-off exit.
    # 8 → 12 with DRE-3970: the four planner steps' re-runs on the next rung.
    # 12 → 13 with DRE-5376: the planner's revision of a sent-back one-off.
    "plan.yml": 13,
    "qa-review.yml": 2,
    "red-main-repair.yml": 1,
    "verify.yml": 2,
}

# Same convention as tests/test_lane_contract.py: spelled out here because the
# contract deliberately no longer spells them anywhere.
RETIRED_LANES = ("In Design Review", "In QA")


class TestTheEnumeration:
    def test_every_inline_agent_prompt_is_found(self):
        found: dict[str, int] = {}
        for prompt in cwp.prompts(WORKFLOWS):
            found[prompt.workflow] = found.get(prompt.workflow, 0) + 1
        assert found == EXPECTED_PROMPT_FILES

    def test_each_prompt_carries_its_body_and_a_line_number(self):
        for prompt in cwp.prompts(WORKFLOWS):
            assert prompt.line > 0
            assert prompt.body.strip(), f"{prompt.workflow}:{prompt.line} is empty"


class TestNoPromptNamesALaneThatDoesNotExist:
    def test_the_repository_is_clean(self):
        findings = cwp.findings(WORKFLOWS)
        assert not findings, "\n".join(str(f) for f in findings)

    def test_no_prompt_names_a_retired_lane_either(self):
        # The deleted-outright half. It is passed IN rather than held in
        # scripts/, because nothing under scripts/ or .github/workflows/ may
        # spell a retired lane name — tests/test_lane_contract_conformance.py
        # reads Python as an AST and would flag the constant itself.
        found = cwp.findings(WORKFLOWS, extra=RETIRED_LANES)
        assert not found, "\n".join(str(f) for f in found)

    def test_a_retired_lane_in_a_prompt_fails_red(self):
        # The whole point of the check: this class drifts again the next time a
        # lane is renamed unless something goes red.
        for name in RETIRED_LANES:
            body = f"Move the card to {name} when the review starts."
            found = cwp.lanes_that_do_not_exist(body, extra=RETIRED_LANES)
            assert name in found, f"a prompt naming {name!r} passed the check"

    def test_a_live_lane_in_a_prompt_is_fine(self):
        assert cwp.lanes_that_do_not_exist("Park it in Green Light for the CEO.") == []
        assert cwp.lanes_that_do_not_exist("Park it in Green Light.", extra=RETIRED_LANES) == []

    def test_the_denylist_covers_every_alias_the_contract_declares(self):
        # A rename is done by adding an alias entry. Feeding the denylist from
        # there is what makes this check survive the NEXT rename with no edit to
        # the script — the standalone CI step catches it on its own.
        with open(ROOT / "config" / "lane-contract.json", encoding="utf-8") as fh:
            aliases = [e["from"] for e in json.load(fh)["aliases"]["entries"]]
        denied = cwp.not_lanes()
        missing = [a for a in aliases if a not in denied]
        assert not missing, f"aliases absent from the denylist: {missing}"

    def test_an_alias_readopted_as_a_live_lane_is_not_a_finding(self):
        # The board has the name again; a prompt may use it.
        live = cwp.live_lanes()[0]
        assert live not in cwp.not_lanes(extra=[live])

    def test_a_live_lane_is_never_on_the_denylist(self):
        assert not (set(cwp.live_lanes()) & set(cwp.not_lanes()))


class TestMedicDuplicateSuppression:
    """A repeated failure must comment on the card that exists, not mint a
    second one. The search has to look wherever a failure card can be.

    Since Stage 2 fix #23 the search is not the agent's to run. It used to be
    prose in the diagnosis prompt, which named the lanes to search, and these
    tests bound that list to the lane `cmd_create` writes. The agent no longer
    holds a Linear key: `medic_retry.py diagnosis-target` runs
    `linear_ops.find_open` before the agent, and the delivery step creates a
    card only when that search answered "none". `find_open` asks by title and
    by the state's TYPE (not completed, not canceled), never by a lane name,
    so it covers every lane a failure card can sit in, including whichever one
    `cmd_create` writes, by construction."""

    def _medic_prompt(self) -> str:
        return next(p.body for p in cwp.prompts(WORKFLOWS) if p.workflow == "medic.yml")

    def _medic_text(self) -> str:
        return (WORKFLOWS / "medic.yml").read_text(encoding="utf-8")

    def test_the_search_asks_by_title_and_openness_never_by_lane(self):
        import inspect

        import linear_ops

        source = inspect.getsource(linear_ops.find_open)
        assert "title: {eq: $t}" in source
        assert 'state: {type: {nin: ["completed", "canceled"]}}' in source
        named = [lane for lane in cwp.live_lanes() if f'"{lane}"' in source]
        assert not named, f"find_open filters by lane names {named}"

    def test_the_create_seams_lane_is_open_so_the_search_sees_it(self):
        # Read as a VALUE (DRE-3533): the lane `cmd_create` writes must be one
        # the search's state-type filter keeps, i.e. not a terminal lane.
        import linear_ops

        assert linear_ops.CREATE_LANE in cwp.live_lanes()
        assert linear_ops.CREATE_LANE not in ("Done", "Canceled", "Duplicate")

    def test_the_search_runs_before_any_card_is_created(self):
        import medic_retry

        assert "find_open(" in Path(medic_retry.__file__).read_text(encoding="utf-8")
        text = self._medic_text()
        assert "medic_retry.py diagnosis-target" in text
        assert text.index("medic_retry.py diagnosis-target") < text.index(
            "linear_ops.py create"
        )

    def test_the_prompt_no_longer_sends_the_agent_searching(self):
        assert "find-open" not in self._medic_prompt()
        assert "linear_ops.py" not in self._medic_prompt()

    def test_the_lanes_the_prompt_names_are_all_live(self):
        assert cwp.lanes_that_do_not_exist(self._medic_prompt()) == []


class TestPlanPromptMatchesTheNewModel:
    def _planner_prompt(self) -> str:
        # Selected by what the prompt SAYS, not by where it sits. It used to be
        # "the first prompt in plan.yml", which held until DRE-3041 put the
        # pre-approval critic's prompt on the one-off route ahead of it — and
        # the symptom was these assertions quietly checking a critic.
        return next(p.body for p in cwp.prompts(WORKFLOWS)
                    if p.workflow == "plan.yml"
                    and "standards for the planner" in p.body)

    def test_the_epic_activates_at_in_progress(self):
        import re

        body = self._planner_prompt()
        assert not re.search(r"move this epic to `?\*{0,2}Todo", body, re.I), (
            "the planner still tells the CEO to start an epic from Todo"
        )
        assert re.search(r"In Progress", body)

    def test_the_deprecated_repo_stamp_is_not_demanded(self):
        # DRE-1699 made the `repo:<slug>` LABEL canonical and `subissue`
        # inherits it. The prompt demanding the body stamp contradicts both
        # briefs/planner.md and standards/card-quality.md.
        assert "**Repo:**" not in self._planner_prompt()
