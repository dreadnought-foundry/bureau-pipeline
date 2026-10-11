"""RED-first tests for DRE-6916 — the engineer, fix and medic runs record their
GitHub calls per pool login (epic DRE-6911).

`scripts/github_calls_reading.py` (DRE-6914) takes two POINTS on each pool
login's hourly meter — `before`, after the job's mints, and `emit`, at its end —
and the console attributes the climb between them (DRE-6915). This file pins
where the two steps sit in the three agent workflows this card wires:
`agent-task.yml`'s `execute`, `agent-fix.yml`'s `fix` and `medic.yml`'s
`diagnose`. The critic and verifier workflows (DRE-6917) and the planner and
repair ones (DRE-6918) are pinned in their own files.

WHAT THIS FILE PINS, one class per criterion on the card:

  * **the before step** — one per job, after the named mint(s), with nothing
    but checkouts between, naming each mint's `app-slug` and `installation-id`
    outputs and handing its token as a NAMED env var, never an argument;
  * **the emit step** — one per job, `always()` + `continue-on-error`,
    immediately before `Keep the run's working log`, handing the freshest
    token for each installation (`WIRED` below says which), and writing its
    key to `$GITHUB_OUTPUT`;
  * **the medic** — its one login is the main bot, `Mint bot token` on
    `BUREAU_APP_ID`, and nothing else; no step in the three lists a pool
    probe token;
  * **the trust and the ceilings** — each file is one the agent-log upload
    role trusts, neither step references a secret, every `${{ }}` is in
    `env:`, and the files still parse.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_workflow_expression_budget as budget  # noqa: E402
from test_self_stubs_grant_id_token import UPLOAD_TRUSTED_REUSABLES  # noqa: E402

SCRIPT = ".bureau-pipeline/scripts/github_calls_reading.py"
WORKING_LOG = "Keep the run's working log"
CHECKOUT = "actions/checkout@"

#: `--login "$<SLUG_VAR>:$<INSTALLATION_VAR>:<TOKEN_VAR>"` — the slug and the
#: installation are expanded by the shell from env:, the token is only NAMED.
LOGIN = re.compile(r'--login\s+"\$(\w+):\$(\w+):(\w+)"')

# Each wired file: its job, the mint steps (by id) the `before` step must list,
# the token the `emit` step hands for each of those installations, and the gate
# the emit step must carry beside `always()`.
#
# The token is the freshest one minted for THAT installation. agent-task.yml's
# DRE-3043 re-mint (`pushtoken`) is the worker's App again, so the worker gets
# `pushtoken || worker`. agent-fix.yml's DRE-6350 re-mint (`pushtoken`) is the
# BOOT App's pair, so there it falls back to the bot's token, never the
# worker's: a bot token handed under the worker's installation id would read
# the wrong meter. Both re-mints sit AFTER `Keep the run's working log`, so at
# the emit step neither has run and each `||` resolves to the job's own mint —
# the reference is the card's rule, ready for a re-mint that moves ahead.
#
# The worker is listed FIRST: when the pool selects slot 1 the worker and the
# bot are one installation, the script keeps the first one named, and the
# worker's token is the later mint.
WIRED = {
    "agent-task.yml": (
        "execute",
        ("worker", "app"),
        {"worker": "steps.pushtoken.outputs.token || steps.worker.outputs.token",
         "app": "steps.app.outputs.token"},
        (),
    ),
    "agent-fix.yml": (
        "fix",
        ("worker", "app"),
        {"worker": "steps.worker.outputs.token",
         "app": "steps.pushtoken.outputs.token || steps.app.outputs.token"},
        # The loop must not act past the unfixable gate at all
        # (tests/test_unfixable_check_escalation.py).
        ("steps.unfixable.outputs.escalate != 'true'",),
    ),
    "medic.yml": (
        "diagnose",
        ("app",),
        {"app": "steps.app.outputs.token"},
        (),
    ),
}


def _steps(name: str) -> list[dict]:
    doc = yaml.safe_load((WORKFLOWS / name).read_text())
    return doc["jobs"][WIRED[name][0]]["steps"]


def _index(steps: list[dict], name: str, phase: str) -> int:
    found = [i for i, s in enumerate(steps)
             if f"github_calls_reading.py {phase}" in (s.get("run") or "")]
    if len(found) != 1:
        raise AssertionError(f"{name}: {len(found)} `{phase}` steps, expected exactly one")
    return found[0]


def _step_id(steps: list[dict], step_id: str) -> int:
    found = [i for i, s in enumerate(steps) if s.get("id") == step_id]
    if len(found) != 1:
        raise AssertionError(f"no single step with id {step_id!r}")
    return found[0]


def _expr(expression: str) -> str:
    return "${{ " + expression + " }}"


def _logins(step: dict) -> list[tuple[str, str, str]]:
    """`[(slug env value, installation env value, token env value)]`, in the
    order the run block names them."""
    env = step.get("env") or {}
    out = []
    for slug_var, installation_var, token_var in LOGIN.findall(step["run"]):
        out.append((env.get(slug_var), env.get(installation_var), env.get(token_var)))
    return out


class BeforeStep(unittest.TestCase):
    def test_one_before_step_after_the_named_mints_with_only_checkouts_between(self):
        for name, (_job, mints, _tokens, _gate) in WIRED.items():
            with self.subTest(workflow=name):
                steps = _steps(name)
                i = _index(steps, name, "before")
                last_mint = max(_step_id(steps, m) for m in mints)
                self.assertGreater(i, last_mint)
                between = steps[last_mint + 1:i]
                self.assertTrue(
                    all(str(s.get("uses", "")).startswith(CHECKOUT) for s in between),
                    f"{name}: only checkouts may sit between the last mint and "
                    f"the before step, found {[s.get('name') for s in between]}")

    def test_the_before_step_lists_each_mint_once_from_its_own_outputs(self):
        for name, (_job, mints, _tokens, _gate) in WIRED.items():
            with self.subTest(workflow=name):
                steps = _steps(name)
                step = steps[_index(steps, name, "before")]
                expected = [(_expr(f"steps.{m}.outputs.app-slug"),
                             _expr(f"steps.{m}.outputs.installation-id"),
                             _expr(f"steps.{m}.outputs.token")) for m in mints]
                self.assertEqual(_logins(step), expected)

    def test_the_before_step_writes_the_file_the_emit_step_reads(self):
        for name in WIRED:
            with self.subTest(workflow=name):
                steps = _steps(name)
                before = steps[_index(steps, name, "before")]["run"]
                emit = steps[_index(steps, name, "emit")]["run"]
                out = re.search(r'--out\s+("[^"]+")', before)
                self.assertIsNotNone(out, f"{name}: the before step names its --out")
                self.assertIn(f"--before {out.group(1)}", emit)

    def test_the_before_step_never_changes_the_jobs_conclusion(self):
        for name in WIRED:
            with self.subTest(workflow=name):
                steps = _steps(name)
                self.assertIs(steps[_index(steps, name, "before")].get("continue-on-error"),
                              True)


class EmitStep(unittest.TestCase):
    def test_the_emit_step_sits_immediately_before_the_working_log(self):
        for name in WIRED:
            with self.subTest(workflow=name):
                steps = _steps(name)
                i = _index(steps, name, "emit")
                self.assertLess(i + 1, len(steps))
                self.assertEqual(steps[i + 1].get("name"), WORKING_LOG)
                self.assertGreater(i, _index(steps, name, "before"))

    def test_the_emit_step_runs_on_every_outcome_and_never_fails_the_job(self):
        for name, (_job, _mints, _tokens, gate) in WIRED.items():
            with self.subTest(workflow=name):
                steps = _steps(name)
                step = steps[_index(steps, name, "emit")]
                condition = str(step.get("if"))
                self.assertTrue(condition.startswith("always()"), condition)
                for conjunct in gate:
                    self.assertIn(conjunct, condition)
                self.assertIs(step.get("continue-on-error"), True)

    def test_the_emit_step_hands_the_freshest_token_per_installation(self):
        for name, (_job, mints, tokens, _gate) in WIRED.items():
            with self.subTest(workflow=name):
                steps = _steps(name)
                step = steps[_index(steps, name, "emit")]
                expected = [(_expr(f"steps.{m}.outputs.app-slug"),
                             _expr(f"steps.{m}.outputs.installation-id"),
                             _expr(tokens[m])) for m in mints]
                self.assertEqual(_logins(step), expected)

    def test_the_agent_task_worker_gets_the_re_mint_when_it_ran(self):
        # The card's own rule, pinned on the one file where the re-mint is the
        # worker's App: `pushtoken` mints the selected pool slot again.
        steps = _steps("agent-task.yml")
        remint = steps[_step_id(steps, "pushtoken")]
        worker = steps[_step_id(steps, "worker")]
        self.assertEqual(remint["with"]["app-id"], worker["with"]["app-id"])
        step = steps[_index(steps, "agent-task.yml", "emit")]
        self.assertIn(_expr("steps.pushtoken.outputs.token || steps.worker.outputs.token"),
                      (step.get("env") or {}).values())

    def test_the_emit_step_names_its_reusable_and_writes_its_outputs(self):
        for name in WIRED:
            with self.subTest(workflow=name):
                steps = _steps(name)
                run = steps[_index(steps, name, "emit")]["run"]
                self.assertRegex(run, rf"--workflow\s+{re.escape(name)}(\s|$)")
                self.assertIn('--github-output "$GITHUB_OUTPUT"', run)


class Medic(unittest.TestCase):
    def test_the_medic_lists_the_main_bot_and_nothing_else(self):
        steps = _steps("medic.yml")
        app = steps[_step_id(steps, "app")]
        self.assertEqual(app.get("name"), "Mint bot token")
        # agent-bureau-bot — pool slot 1, the login that reached 4,999 on
        # 2026-10-10 — is the App behind `BUREAU_APP_ID`.
        self.assertEqual(app["with"]["app-id"], _expr("secrets.BUREAU_APP_ID"))
        for phase in ("before", "emit"):
            step = steps[_index(steps, "medic.yml", phase)]
            logins = _logins(step)
            self.assertEqual(len(logins), 1, phase)
            self.assertEqual(logins[0][:2], (_expr("steps.app.outputs.app-slug"),
                                             _expr("steps.app.outputs.installation-id")))

    def test_no_step_lists_a_pool_probe_token(self):
        for name in WIRED:
            for phase in ("before", "emit"):
                with self.subTest(workflow=name, phase=phase):
                    steps = _steps(name)
                    step = steps[_index(steps, name, phase)]
                    self.assertNotIn("probe_", str(step.get("env")))
                    self.assertNotIn("probe_", step["run"])


class TrustAndCeilings(unittest.TestCase):
    def test_every_wired_file_is_one_the_upload_role_trusts(self):
        for name in WIRED:
            self.assertIn(name, UPLOAD_TRUSTED_REUSABLES)

    def test_neither_step_references_a_secret(self):
        # So neither needs a new `SCRUB_` line (tests/test_agent_log_upload.py).
        for name in WIRED:
            for phase in ("before", "emit"):
                with self.subTest(workflow=name, phase=phase):
                    steps = _steps(name)
                    step = steps[_index(steps, name, phase)]
                    self.assertNotIn("secrets.", str(step))

    def test_every_substitution_is_in_env_never_in_the_script(self):
        # The expression ceiling (tests/test_workflow_expression_budget.py)
        # never reaches a run block that interpolates nothing.
        for name in WIRED:
            for phase in ("before", "emit"):
                with self.subTest(workflow=name, phase=phase):
                    steps = _steps(name)
                    step = steps[_index(steps, name, phase)]
                    self.assertNotIn("${{", step["run"])
                    self.assertIn(SCRIPT, step["run"])

    def test_the_tokens_are_named_never_passed(self):
        for name in WIRED:
            for phase in ("before", "emit"):
                with self.subTest(workflow=name, phase=phase):
                    steps = _steps(name)
                    step = steps[_index(steps, name, phase)]
                    env = step.get("env") or {}
                    for _slug, _installation, token_var in LOGIN.findall(step["run"]):
                        self.assertIn(token_var, env)
                        self.assertNotIn(f"${token_var}", step["run"])
                        self.assertNotIn("${" + token_var, step["run"])

    def test_every_block_in_the_wired_files_stays_under_the_ceilings(self):
        # Measured by the budget test's own functions, never restated here.
        for path, job, step, script in budget.run_blocks():
            if path not in WIRED:
                continue
            with self.subTest(workflow=path, job=job, step=step):
                self.assertLessEqual(len(script), budget.RUN_BLOCK_CEILING)
                if "${{" in script:
                    self.assertLessEqual(budget.compiled_expression_length(script),
                                         budget.BUDGET)

    def test_the_wired_files_still_parse(self):
        for name in WIRED:
            with self.subTest(workflow=name):
                self.assertIsInstance(yaml.safe_load((WORKFLOWS / name).read_text()), dict)


if __name__ == "__main__":
    unittest.main()
