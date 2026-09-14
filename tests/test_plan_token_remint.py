"""No step in the plan job spends an App token minted before a model run
(DRE-3940).

THE INCIDENT (2026-09-14, dreadnought-foundry/portico run 34869632296, epic
DRE-3777). `Mint bot token` ran at 16:56:27Z. `Plan epic` ran from 16:56:54Z to
18:11:56Z — 75 minutes. At 18:12:03Z `First critic — round 1 (before the CEO
reads it)` failed in under a second:

    GET /users/agent-bureau-bot-4%5Bbot%5D - 401
    Action failed with error: Bad credentials

GitHub gives an App installation token exactly 60 minutes. Every consumer after
the planner read `steps.app.outputs.token`, the token minted at job start, so a
planner that ran past the hour took the whole back half of the job with it: the
plan was written and never reviewed, and the failure read as a credential
problem rather than a clock.

The model steps are the only steps in this job whose duration is not bounded in
seconds. At the incident's own rate (~32 s/turn) a 140-turn planner, a 60-turn
critic round or a 180-turn post-approval review can each pass the hour alone.
So the rule pinned here is general, not a patch on one step:

  every step that reads a `steps.<id>.outputs.token` reads it from a
  `create-github-app-token` mint that sits after the last model step before
  it — no `anthropics/claude-code-action` step lies between a token and the
  step that spends it.

That is agent-task.yml's precedent (DRE-3043's `Mint fresh push token`, sharpened
by DRE-3098): mint as its own step immediately before the consumer, because a
`run:` step cannot mint and a token held across a long step is a lost run.

self-plan.yml is a thin stub over this same reusable workflow, so the rule
holds for it by construction; the last class checks it stays that way.

Run: python3 -m pytest tests/test_plan_token_remint.py -v
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / ".github" / "workflows" / "plan.yml"
SELF_PLAN = ROOT / ".github" / "workflows" / "self-plan.yml"

MINT_ACTION = "actions/create-github-app-token"
MODEL_ACTION = "anthropics/claude-code-action"
PLANNER = "Plan epic"
START_MINT_ID = "app"


def _steps() -> list[dict]:
    doc = yaml.safe_load(PLAN.read_text(encoding="utf-8"))
    return doc["jobs"]["plan"]["steps"]


def _action(step: dict) -> str:
    return str(step.get("uses") or "").split("@")[0]


def _index_named(name: str) -> int:
    for i, step in enumerate(_steps()):
        if str(step.get("name") or "") == name:
            return i
    raise AssertionError(f"plan.yml's plan job has no step named {name!r}")


def _index_of_id(step_id: str) -> int:
    for i, step in enumerate(_steps()):
        if step.get("id") == step_id:
            return i
    raise AssertionError(f"plan.yml's plan job has no step with id {step_id!r}")


def _token_step_ids(step: dict) -> list[str]:
    """Every step id this step takes an App token from — `with:`, `env:` and
    `run:` alike, because a GH_TOKEN in env expires exactly as a github_token
    input does."""
    text = yaml.safe_dump(step)
    return re.findall(r"steps\.([A-Za-z0-9_-]+)\.outputs\.token", text)


def _label(i: int, step: dict) -> str:
    return f"step {i} {step.get('name') or step.get('uses') or step.get('id')!r}"


def _consumers() -> list[tuple[int, dict, str]]:
    return [
        (i, step, step_id)
        for i, step in enumerate(_steps())
        for step_id in _token_step_ids(step)
    ]


class NoTokenOutlivesAModelRun(unittest.TestCase):

    def test_nothing_after_the_planner_reads_the_start_of_job_token(self):
        # The card's acceptance criterion, in its own words. Run 34869632296
        # is this assertion failing on `prea`.
        planner = _index_named(PLANNER)
        stale = [
            _label(i, step) for i, step, step_id in _consumers()
            if i > planner and step_id == START_MINT_ID
        ]
        self.assertEqual(stale, [], "these read the token minted before the planner")

    def test_no_model_step_sits_between_a_token_and_its_consumer(self):
        steps = _steps()
        offenders = []
        for i, step, step_id in _consumers():
            mint = _index_of_id(step_id)
            between = [
                _label(j, steps[j]) for j in range(mint + 1, i)
                if _action(steps[j]) == MODEL_ACTION
            ]
            if between:
                offenders.append(f"{_label(i, step)} reads `{step_id}` across {between}")
        self.assertEqual(offenders, [])

    def test_every_token_comes_from_a_mint_that_runs_before_it(self):
        steps = _steps()
        for i, step, step_id in _consumers():
            mint = _index_of_id(step_id)
            self.assertEqual(_action(steps[mint]), MINT_ACTION, step_id)
            self.assertLess(mint, i, f"{_label(i, step)} reads a later step's token")

    def test_the_first_critic_reads_a_token_minted_after_the_planner(self):
        # The incident step, named, so a rewrite of the general rule above
        # cannot quietly stop covering it.
        prea = _index_of_id("prea")
        ids = _token_step_ids(_steps()[prea])
        self.assertEqual(len(ids), 1, ids)
        mint = _index_of_id(ids[0])
        self.assertGreater(mint, _index_named(PLANNER))
        self.assertLess(mint, prea)


class EveryReMintIsTheStartMintAgain(unittest.TestCase):
    """Same App, same pinned action, same inputs — a re-mint that drifted to a
    different identity would change who the critics act as."""

    def _re_mints(self) -> list[tuple[int, dict]]:
        return [
            (i, s) for i, s in enumerate(_steps())
            if _action(s) == MINT_ACTION and s.get("id") != START_MINT_ID
        ]

    def test_there_are_re_mints(self):
        self.assertTrue(self._re_mints())

    def test_same_pin_and_same_inputs_as_the_start_mint(self):
        start = _steps()[_index_of_id(START_MINT_ID)]
        for i, step in self._re_mints():
            self.assertEqual(step.get("uses"), start.get("uses"), _label(i, step))
            self.assertEqual(step.get("with"), start.get("with"), _label(i, step))

    def test_each_is_gated_and_fails_loudly(self):
        # Gated: an unconditioned mint runs on every route. Not
        # continue-on-error (deliberately unlike agent-task.yml's rescue
        # mints): a mint that cannot happen should go red at the mint, not as
        # a `Bad credentials` one step later.
        for i, step in self._re_mints():
            self.assertTrue(str(step.get("if") or "").strip(), _label(i, step))
            self.assertFalse(step.get("continue-on-error"), _label(i, step))

    def test_every_re_mint_is_spent(self):
        spent = {step_id for _i, _s, step_id in _consumers()}
        for i, step in self._re_mints():
            self.assertIn(step.get("id"), spent, f"{_label(i, step)} is never read")


class SelfPlanRidesTheSameJob(unittest.TestCase):
    """self-plan.yml needs no re-mint of its own because it has no steps: it
    calls plan.yml. If it ever grows a job of its own, this goes red and the
    rule above has to be applied there too."""

    def test_self_plan_is_a_thin_caller_of_plan_yml(self):
        doc = yaml.safe_load(SELF_PLAN.read_text(encoding="utf-8"))
        jobs = doc["jobs"]
        self.assertEqual(len(jobs), 1, jobs)
        (job,) = jobs.values()
        self.assertIn(".github/workflows/plan.yml@", str(job.get("uses")))
        self.assertNotIn("steps", job)


if __name__ == "__main__":
    unittest.main()
