"""No step in the fix job spends an App token minted before a model run
(DRE-4320).

THE INCIDENT (2026-09-19, dreadnought-foundry/portico run 35439204813, the
Agent Fix run for PR #613 / DRE-4314 — it failed twice, the run and its
automatic retry). `Mint bot token` minted at 04:26:45 PT. The `Fix` step read
the review and refuted one finding. At 05:28:33 PT — 61m48s after the mint —
`Report` called `gh api graphql` through `pipeline_act.py`:

    HTTP 401: Bad credentials (https://api.github.com/graphql)

GitHub gives an App installation token exactly 60 minutes. `Report` spent
`steps.app.outputs.token`, the token minted at job start, so a fix run that
passed the hour finished its work and could not post the answer. That PR came
to no harm — it had merged 31 seconds earlier — but on a PR still open a lost
refutation leaves the critic's finding standing with nobody answering it.

The model step is the only step in this job whose duration is not bounded in
seconds (the job's own ceiling is 120 minutes), so the rule pinned here is
general, not a patch on one step — it is the same rule
tests/test_plan_token_remint.py states for plan.yml and DRE-3043 applied to
agent-task.yml:

  every step that reads a `steps.<id>.outputs.token` reads it from a
  `create-github-app-token` mint that sits after the last model step before
  it — no `anthropics/claude-code-action` step lies between a token and the
  step that spends it.

It is stated over EVERY token reader after `Fix` rather than over `Report` by
name, so a consumer added to the back of this job later is caught by the same
assertion.

THE ONE SANCTIONED EXCEPTION, and it is why "spends" is the verb above: a
stale token may still be NAMED after a model step as the trailing `||`
alternative of a fresh mint that is `continue-on-error`. That is the card's
own requirement — a failed mint must leave the run exactly as it was, which
means falling back to what the run held before — and `plan.yml` does not need
it because its re-mints go red at the mint instead. The exception is pinned
rather than waved through: the expression's FIRST token is the one GitHub
hands the step whenever the mint worked, every other id in it must sit behind
a `||`, and the primary must be a `continue-on-error` mint after the model
step. A consumer that reads the job-start token on its own is caught exactly
as before.

THE RE-MINT IS THE BOOT MINT AGAIN, not the pool reader. `Report`'s comments
are worker-attributed — fix_budget.py counts attempt markers by the boot App's
login (tests/test_readers_on_the_pool.py pins that step as identity-sensitive)
— so the fresh token comes from the same App id and private key `Mint bot
token` uses, and the identity that posts the report does not change. It is
`continue-on-error` with a fallback to `steps.app.outputs.token` for
agent-task.yml's reason (DRE-3043): a mint that fails must leave the run
exactly as it was, never fail a report that is otherwise fine.

self-agent-fix.yml is a thin stub over this same reusable workflow, so the rule
holds for it by construction; the last class checks it stays that way.

Run: python3 -m pytest tests/test_agent_fix_token_remint.py -v
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / ".github" / "workflows" / "agent-fix.yml"
SELF_FIX = ROOT / ".github" / "workflows" / "self-agent-fix.yml"

MINT_ACTION = "actions/create-github-app-token"
MODEL_ACTION = "anthropics/claude-code-action"
MODEL_STEP = "Fix"
CONSUMER_STEP = "Report"
START_MINT_ID = "app"
# The pool's own mints (DRE-4282): mints, but not re-mints — the reader is
# selected once, up front, and tests/test_readers_on_the_pool.py owns it.
POOL_STEP_IDS = {"reader", "probe_2", "probe_3", "probe_4"}


def _steps() -> list[dict]:
    doc = yaml.safe_load(FIX.read_text(encoding="utf-8"))
    return doc["jobs"]["fix"]["steps"]


def _action(step: dict) -> str:
    return str(step.get("uses") or "").split("@")[0]


def _index_named(name: str) -> int:
    for i, step in enumerate(_steps()):
        if str(step.get("name") or "") == name:
            return i
    raise AssertionError(f"agent-fix.yml's fix job has no step named {name!r}")


def _index_of_id(step_id: str) -> int:
    for i, step in enumerate(_steps()):
        if step.get("id") == step_id:
            return i
    raise AssertionError(f"agent-fix.yml's fix job has no step with id {step_id!r}")


_EXPRESSION = re.compile(r"\$\{\{(.*?)\}\}", re.S)
_TOKEN_ID = re.compile(r"steps\.([A-Za-z0-9_-]+)\.outputs\.token")


def _token_reads(step: dict) -> list[tuple[str, list[str]]]:
    """Every `${{ }}` expression in this step that names an App token, with the
    step ids it names in the order the expression prefers them: the token
    GitHub hands the step first, then any `||` fallbacks.

    Read over `with:`, `env:` and `run:` alike, because a GH_TOKEN in env
    expires exactly as a github_token input does. `width` keeps the dump from
    folding a long expression across lines, which would hide it from the
    regex.
    """
    text = yaml.safe_dump(step, width=10**6)
    reads = []
    for expression in _EXPRESSION.findall(text):
        ids = _TOKEN_ID.findall(expression)
        if ids:
            reads.append((expression.strip(), ids))
    return reads


def _token_step_ids(step: dict) -> list[str]:
    """Every step id this step takes an App token from, fallbacks included."""
    return [step_id for _expression, ids in _token_reads(step) for step_id in ids]


def _label(i: int, step: dict) -> str:
    return f"step {i} {step.get('name') or step.get('uses') or step.get('id')!r}"


def _consumers() -> list[tuple[int, dict, str]]:
    """(step index, step, the id of the token it SPENDS) — the first id of each
    token expression, which is what GitHub hands the step whenever the mint it
    names produced anything."""
    return [
        (i, step, ids[0])
        for i, step in enumerate(_steps())
        for _expression, ids in _token_reads(step)
    ]


def _fallbacks() -> list[tuple[int, dict, str, list[str]]]:
    """(step index, step, the expression, its ids) for every token expression
    that names more than one mint."""
    return [
        (i, step, expression, ids)
        for i, step in enumerate(_steps())
        for expression, ids in _token_reads(step)
        if len(ids) > 1
    ]


def _conjuncts(condition: str) -> set[str]:
    """The `&&`-joined terms of a step `if:`, as a set — the order they are
    written in does not change which runs they admit."""
    assert "||" not in condition, f"not a plain conjunction: {condition!r}"
    return {term.strip() for term in condition.split("&&") if term.strip()}


class NoTokenOutlivesAModelRun(unittest.TestCase):

    def test_nothing_after_the_model_step_spends_the_start_of_job_token(self):
        # The card's acceptance criterion, in its own words. Run 35439204813
        # is this assertion failing on `Report`.
        model = _index_named(MODEL_STEP)
        stale = [
            _label(i, step) for i, step, step_id in _consumers()
            if i > model and step_id == START_MINT_ID
        ]
        self.assertEqual(stale, [], "these spend the token minted before the fix agent")

    def test_no_model_step_sits_between_a_token_and_the_step_that_spends_it(self):
        # Stated over every consumer in the job, so a token reader added after
        # the model step later is caught without naming it here.
        steps = _steps()
        offenders = []
        for i, step, step_id in _consumers():
            mint = _index_of_id(step_id)
            between = [
                _label(j, steps[j]) for j in range(mint + 1, i)
                if _action(steps[j]) == MODEL_ACTION
            ]
            if between:
                offenders.append(f"{_label(i, step)} spends `{step_id}` across {between}")
        self.assertEqual(offenders, [])

    def test_a_stale_token_is_admitted_only_as_a_fallback(self):
        # The sanctioned exception, pinned (module docstring). Anything a step
        # names besides the token it spends sits behind a `||`, and the token
        # it spends is a mint that may fail without failing the run — the only
        # reason to carry a fallback at all.
        steps = _steps()
        for i, step, expression, ids in _fallbacks():
            with self.subTest(step=_label(i, step)):
                primary, behind = ids[0], ids[1:]
                for step_id in behind:
                    self.assertIn(
                        f"|| steps.{step_id}.outputs.token",
                        " ".join(expression.split()),
                        "a stale token must be the ALTERNATIVE, never the value",
                    )
                self.assertTrue(
                    steps[_index_of_id(primary)].get("continue-on-error"),
                    "a fallback is only warranted behind a mint that may fail",
                )

    def test_every_token_comes_from_a_mint_that_runs_before_it(self):
        steps = _steps()
        for i, step, step_id in _consumers():
            mint = _index_of_id(step_id)
            self.assertEqual(_action(steps[mint]), MINT_ACTION, step_id)
            self.assertLess(mint, i, f"{_label(i, step)} reads a later step's token")

    def test_the_report_reads_a_token_minted_after_the_model_step(self):
        # The incident step, named, so a rewrite of the general rule above
        # cannot quietly stop covering it.
        report = _index_named(CONSUMER_STEP)
        ids = _token_step_ids(_steps()[report])
        fresh = _index_of_id(ids[0])
        self.assertGreater(fresh, _index_named(MODEL_STEP))
        self.assertLess(fresh, report)


class TheReportFallsBackToTheBootToken(unittest.TestCase):
    """A failed mint must leave the run exactly as it was: `Report` spends the
    fresh token and falls through to the job-start one when the mint produced
    none (an empty output is falsy, so `||` picks the boot token)."""

    def _report(self) -> dict:
        return _steps()[_index_named(CONSUMER_STEP)]

    def test_the_fallback_expression_is_pinned(self):
        ids = _token_step_ids(self._report())
        self.assertEqual(len(ids), 2, ids)
        fresh, fallback = ids
        self.assertEqual(fallback, START_MINT_ID)
        self.assertEqual(
            (self._report().get("env") or {}).get("GH_TOKEN"),
            "${{ steps.%s.outputs.token || steps.%s.outputs.token }}" % (fresh, START_MINT_ID),
        )

    def test_the_fallback_is_the_only_survivor_of_a_failed_mint(self):
        # The mint may fail without failing the job, so the fallback is the
        # thing that keeps today's behaviour on that path.
        fresh = _token_step_ids(self._report())[0]
        self.assertTrue(_steps()[_index_of_id(fresh)].get("continue-on-error"))


class TheReMintIsTheBootMintAgain(unittest.TestCase):
    """Same App id and private key as `Mint bot token`, so the identity that
    posts the report does not change (tests/test_readers_on_the_pool.py pins
    `Report` as identity-sensitive: fix_budget.py counts by the boot App's
    login). Same pinned sha as the file's other mints — a floating tag is what
    DRE-3418's pin check exists to stop."""

    def _re_mints(self) -> list[tuple[int, dict]]:
        return [
            (i, s) for i, s in enumerate(_steps())
            if _action(s) == MINT_ACTION
            and s.get("id") != START_MINT_ID
            and s.get("id") not in POOL_STEP_IDS
        ]

    def test_there_is_a_re_mint(self):
        self.assertEqual(len(self._re_mints()), 1, self._re_mints())

    def test_same_pin_and_same_inputs_as_the_boot_mint(self):
        boot = _steps()[_index_of_id(START_MINT_ID)]
        for i, step in self._re_mints():
            self.assertEqual(step.get("uses"), boot.get("uses"), _label(i, step))
            self.assertEqual(step.get("with"), boot.get("with"), _label(i, step))
            self.assertNotIn("steps.pool", yaml.safe_dump(step.get("with") or {}))

    def test_it_runs_on_exactly_the_routes_the_report_runs_on(self):
        # Including after a FAILED Fix: `always()` is what admits that run, and
        # it is the model step's failure that most needs a report posted.
        report = _conjuncts(str(self._report_if()))
        self.assertIn("always()", report)
        for i, step in self._re_mints():
            self.assertEqual(_conjuncts(str(step.get("if") or "")), report, _label(i, step))

    def test_a_failed_mint_never_fails_the_run(self):
        for i, step in self._re_mints():
            self.assertTrue(step.get("continue-on-error"), _label(i, step))

    def test_it_sits_after_the_model_step_and_before_the_report(self):
        for i, _step in self._re_mints():
            self.assertGreater(i, _index_named(MODEL_STEP))
            self.assertLess(i, _index_named(CONSUMER_STEP))

    def test_every_re_mint_is_spent(self):
        spent = {step_id for _i, _s, step_id in _consumers()}
        for i, step in self._re_mints():
            self.assertIn(step.get("id"), spent, f"{_label(i, step)} is never read")

    def _report_if(self) -> str:
        return _steps()[_index_named(CONSUMER_STEP)].get("if") or ""


class TheFixAgentsOwnTokenIsUnchanged(unittest.TestCase):
    """The card's fifth criterion. The agent PUSHES with this credential and
    the push is agent-bureau-bot's, like the PR it lands on; the model step is
    also where the hour is spent, so re-minting for it would buy nothing."""

    def test_the_model_step_still_takes_the_boot_token(self):
        model = _steps()[_index_named(MODEL_STEP)]
        self.assertEqual(
            (model.get("with") or {}).get("github_token"),
            "${{ steps.app.outputs.token }}",
        )


class SelfAgentFixRidesTheSameJob(unittest.TestCase):
    """self-agent-fix.yml needs no re-mint of its own because it has no steps:
    it calls agent-fix.yml. If it ever grows a job of its own, this goes red
    and the rule above has to be applied there too."""

    def test_self_agent_fix_is_a_thin_caller_of_agent_fix_yml(self):
        doc = yaml.safe_load(SELF_FIX.read_text(encoding="utf-8"))
        jobs = doc["jobs"]
        self.assertEqual(len(jobs), 1, jobs)
        (job,) = jobs.values()
        self.assertIn(".github/workflows/agent-fix.yml@", str(job.get("uses")))
        self.assertNotIn("steps", job)


if __name__ == "__main__":
    unittest.main()
