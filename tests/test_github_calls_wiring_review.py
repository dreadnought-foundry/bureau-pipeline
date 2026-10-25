"""RED-first tests for DRE-6917 — the critic's and the verifier's runs record
their GitHub calls per pool login (epic DRE-6911).

`scripts/github_calls_reading.py` (DRE-6914) takes two points on each pool
login's hourly meter — `before`, after the job's mints, and `emit`, at its end
— and keeps them in the agent-log store. This card places its two steps in the
two review-side jobs the upload role trusts: `qa-review.yml`'s `review` and
`verify.yml`'s `verify`. The engineer, fix and medic runs (DRE-6916) and the
planner and repair runs (DRE-6918) are their siblings' files, not this one's.

WHAT THIS FILE PINS, one class per criterion on the card:

  * **the two steps** — each job carries exactly ONE `before` step after the
    mints `WIRED` names and before the model runs, and ONE `emit` step,
    `always()` + `continue-on-error`, immediately before `Keep the run's
    working log`. Each `--login` is built from one mint's `app-slug` and
    `installation-id` outputs, the token as a NAMED env variable, and every
    `${{ }}` sits in `env:`;
  * **the right buckets** — the critic's `before` names the qa App AND the
    pool-selected reader, so its check-run writes and its reads are each
    attributed to the meter they spend; no step names a `probe_*` token;
  * **the freshest token per installation** — `parse_logins` keeps the first
    `--login` per installation, so `emit` names the pool mint first: when the
    pool fell back to slot 1 the two mints are ONE installation, and the later
    mint's token is the one handed to the after point. A later mint on a
    DIFFERENT App (`verify.yml`'s qa-bot verdict mint, `qa-review.yml`'s
    `console` and `checks_app` main-bot mints) is never handed in its place —
    its token would read another installation's meter under this one's id;
  * **the wiring runs** — the two steps' own shell, given fake mint outputs and
    fake meters, writes one `github-calls-reading.v1` document with a rise per
    login and exits 0;
  * **the boundaries** — both files are upload-trusted, and both stay under
    the expression ceiling.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"

sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import github_calls_reading as gcr  # noqa: E402
from test_self_stubs_grant_id_token import UPLOAD_TRUSTED_REUSABLES  # noqa: E402
from test_workflow_expression_budget import (  # noqa: E402
    BUDGET,
    RUN_BLOCK_CEILING,
    compiled_expression_length,
)

SCRIPT = "github_calls_reading.py"
MINT = "actions/create-github-app-token@"
MODEL = "anthropics/claude-code-action@"
WORKING_LOG = "Keep the run's working log"

# Each wired reusable: its job, and the mint step ids its `before` lists, in
# the order the job mints them — the workflow's own slot-1 App first, the
# pool-selected mint second. `emit` lists the same mints, pool first.
WIRED = {
    "qa-review.yml": ("review", ("app", "reader")),
    "verify.yml": ("verify", ("app", "worker")),
}

# The App each slot-1 mint is: the critic's is the qa App, the check-run
# writer's bucket (DRE-4282); the verifier's is the main bot (DRE-2429).
SLOT_ONE_APP = {
    "qa-review.yml": "secrets.BUREAU_QA_APP_ID",
    "verify.yml": "secrets.BUREAU_APP_ID",
}

OUTPUT = re.compile(r"^\$\{\{ steps\.([A-Za-z0-9_-]+)\.outputs\.([A-Za-z0-9_-]+) \}\}$")


def _steps(name: str) -> list[dict]:
    job = WIRED[name][0]
    return yaml.safe_load((WORKFLOWS / name).read_text())["jobs"][job]["steps"]


def _index(steps: list[dict], command: str) -> list[int]:
    return [i for i, s in enumerate(steps)
            if f"{SCRIPT} {command}" in (s.get("run") or "")]


def _args(run: str) -> list[str]:
    """The script's own arguments, the shell's line continuations joined."""
    line = " ".join(run.replace("\\\n", " ").split())
    return shlex.split(line[line.index(SCRIPT):])


def _flag(run: str, flag: str) -> list[str]:
    args = _args(run)
    return [args[i + 1] for i, a in enumerate(args[:-1]) if a == flag]


def _mint_of(env: dict, value: str, output: str) -> str:
    """The step id whose `output` the env variable `value` names, or ''."""
    found = OUTPUT.match(str(env.get(value, "")))
    return found.group(1) if found and found.group(2) == output else ""


def _login_mints(step: dict) -> list[str]:
    """The mint step id behind each `--login`, in order. Every part of a login
    must come from the SAME mint — slug, installation id and token — or the
    login names ''."""
    env = step.get("env") or {}
    mints = []
    for spec in _flag(step["run"], "--login"):
        app, installation, token_env = spec.rsplit(":", 2)
        ids = {
            _mint_of(env, app.lstrip("$").strip("{}"), "app-slug"),
            _mint_of(env, installation.lstrip("$").strip("{}"), "installation-id"),
            _mint_of(env, token_env, "token"),
        }
        mints.append(ids.pop() if len(ids) == 1 else "")
    return mints


class TheTwoSteps(unittest.TestCase):
    def _one(self, name, command):
        steps = _steps(name)
        found = _index(steps, command)
        self.assertEqual(len(found), 1, f"{name}: exactly one {command} step")
        return steps, found[0]

    def test_one_before_step_after_the_mints_and_before_the_model(self):
        for name, (_, mints) in WIRED.items():
            with self.subTest(workflow=name):
                steps, i = self._one(name, "before")
                ids = [s.get("id") for s in steps]
                for mint in mints:
                    self.assertTrue(str(steps[ids.index(mint)].get("uses", "")).startswith(MINT))
                    self.assertGreater(i, ids.index(mint), f"{name}: after {mint}")
                first_model = min(j for j, s in enumerate(steps)
                                  if str(s.get("uses", "")).startswith(MODEL))
                self.assertLess(i, first_model, f"{name}: before the model runs")

    def test_the_before_step_lists_each_mint_once_from_its_own_outputs(self):
        for name, (_, mints) in WIRED.items():
            with self.subTest(workflow=name):
                steps, i = self._one(name, "before")
                self.assertEqual(_login_mints(steps[i]), list(mints))

    def test_one_emit_step_immediately_before_the_working_log(self):
        for name in WIRED:
            with self.subTest(workflow=name):
                steps, i = self._one(name, "emit")
                self.assertEqual(steps[i + 1].get("name"), WORKING_LOG)
                self.assertEqual(sum(1 for s in steps if s.get("name") == WORKING_LOG), 1)

    def test_the_emit_step_runs_on_every_outcome_and_never_fails_the_job(self):
        for name in WIRED:
            with self.subTest(workflow=name):
                steps, i = self._one(name, "emit")
                self.assertIn("always()", str(steps[i].get("if")))
                self.assertIs(steps[i].get("continue-on-error"), True)
                self.assertEqual(_flag(steps[i]["run"], "--github-output"),
                                 ["$GITHUB_OUTPUT"])
                self.assertEqual(_flag(steps[i]["run"], "--workflow"), [name])

    def test_the_before_step_never_fails_the_job_either(self):
        for name in WIRED:
            with self.subTest(workflow=name):
                steps, i = self._one(name, "before")
                self.assertIs(steps[i].get("continue-on-error"), True)

    def test_emit_reads_the_file_before_wrote(self):
        for name in WIRED:
            with self.subTest(workflow=name):
                steps, b = self._one(name, "before")
                _, e = self._one(name, "emit")
                out = _flag(steps[b]["run"], "--out")
                self.assertEqual(len(out), 1)
                self.assertEqual(_flag(steps[e]["run"], "--before"), out)
                self.assertNotEqual(_flag(steps[e]["run"], "--out"), out)

    def test_every_substitution_is_in_env_never_in_the_script(self):
        for name in WIRED:
            for command in ("before", "emit"):
                with self.subTest(workflow=name, step=command):
                    steps, i = self._one(name, command)
                    self.assertNotIn("${{", steps[i]["run"])
                    for value in (steps[i].get("env") or {}).values():
                        self.assertNotIn("secrets.", str(value),
                                         "no new secret reference, so no new SCRUB_ line")


class TheRightBuckets(unittest.TestCase):
    def test_the_critic_names_the_qa_app_and_the_read_pool(self):
        steps = _steps("qa-review.yml")
        before = steps[_index(steps, "before")[0]]
        mints = _login_mints(before)
        self.assertIn("app", mints)
        self.assertIn("reader", mints)
        by_id = {s.get("id"): s for s in steps}
        self.assertEqual(by_id["app"]["with"]["app-id"], "${{ secrets.BUREAU_QA_APP_ID }}")
        self.assertIn("steps.pool.outputs.n", by_id["reader"]["with"]["app-id"])

    def test_each_slot_one_mint_is_the_app_the_card_names(self):
        for name, app in SLOT_ONE_APP.items():
            with self.subTest(workflow=name):
                by_id = {s.get("id"): s for s in _steps(name)}
                self.assertEqual(by_id["app"]["with"]["app-id"], "${{ " + app + " }}")
                self.assertIn("steps.pool.outputs.n",
                              by_id[WIRED[name][1][1]]["with"]["app-id"])

    def test_no_step_lists_a_pool_probe_token(self):
        for name in WIRED:
            steps = _steps(name)
            for i in _index(steps, "before") + _index(steps, "emit"):
                with self.subTest(workflow=name, step=steps[i].get("name")):
                    self.assertNotIn("probe_", yaml.safe_dump(steps[i]))
                    self.assertFalse(any(m.startswith("probe_") for m in _login_mints(steps[i])))


class TheFreshestTokenPerInstallation(unittest.TestCase):
    def test_emit_lists_the_same_mints_the_pool_one_first(self):
        # parse_logins keeps the first --login per installation; the pool mint
        # ran after the slot-1 mint, so where the pool fell back to slot 1 its
        # token is the fresher of the two and must be the one kept.
        for name, (_, (slot_one, pool)) in WIRED.items():
            with self.subTest(workflow=name):
                steps = _steps(name)
                emit = steps[_index(steps, "emit")[0]]
                self.assertEqual(_login_mints(emit), [pool, slot_one])

    def test_a_later_mint_on_another_app_is_never_handed_in_its_place(self):
        # The later mints in these jobs are on a DIFFERENT App, so they are a
        # different installation's meter; a token from one under the other's
        # installation id would record the wrong meter.
        others = {"verify.yml": ("qa",), "qa-review.yml": ("console", "checks_app")}
        for name, ids in others.items():
            steps = _steps(name)
            for i in _index(steps, "before") + _index(steps, "emit"):
                text = yaml.safe_dump(steps[i])
                for other in ids:
                    with self.subTest(workflow=name, mint=other):
                        self.assertNotIn(f"steps.{other}.outputs", text)


def _render(step: dict, outputs: dict[str, dict[str, str]]) -> dict[str, str]:
    """The step's env with each `${{ steps.X.outputs.Y }}` given a fake value."""
    env = {}
    for key, value in (step.get("env") or {}).items():
        found = OUTPUT.match(str(value))
        assert found, f"{key}: only mint outputs are expected here, got {value!r}"
        env[key] = outputs.get(found.group(1), {}).get(found.group(2), "")
    return env


class TheWiringRuns(unittest.TestCase):
    """The steps' own shell, run against fake mints and fake meters."""

    RESET = 1791702251

    def _run(self, name, outputs, before_meters, after_meters):
        steps = _steps(name)
        before = steps[_index(steps, "before")[0]]
        emit = steps[_index(steps, "emit")[0]]
        with tempfile.TemporaryDirectory() as tmp:
            os.symlink(ROOT, os.path.join(tmp, ".bureau-pipeline"))
            base = {k: v for k, v in os.environ.items()
                    if not k.startswith(("GITHUB_", "ACTIONS_", "RUNNER_"))}
            base.update({
                "PIPELINE_DIR": str(ROOT),
                "RUNNER_TEMP": tmp,
                "GITHUB_OUTPUT": os.path.join(tmp, "github-output"),
                "GITHUB_REPOSITORY": "dreadnought-foundry/agent-bureau",
                "GITHUB_RUN_ID": "38004481513",
                "GITHUB_RUN_ATTEMPT": "1",
                "GITHUB_JOB": WIRED[name][0],
            })
            procs = []
            for step, meters in ((before, before_meters), (emit, after_meters)):
                env = dict(base, **_render(step, outputs))
                env[gcr.FAKE_PROBES_ENV] = json.dumps(meters(env))
                procs.append(subprocess.run(["bash", "-e", "-c", step["run"]], cwd=tmp,
                                            env=env, capture_output=True, text=True))
            out = _flag(emit["run"], "--out")[0].replace("${RUNNER_TEMP:-/tmp}", tmp)
            doc = json.loads(Path(out).read_text()) if Path(out).exists() else None
            gh_out = Path(base["GITHUB_OUTPUT"]).read_text()
        return procs, doc, gh_out

    def _meter(self, used):
        return {"status": 200, "x-ratelimit-used": str(used), "x-ratelimit-limit": "5000",
                "x-ratelimit-remaining": str(5000 - used),
                "x-ratelimit-reset": str(self.RESET), "x-ratelimit-resource": "core"}

    def _by_token(self, used_for):
        """A fake table keyed by the token variable each env value names."""
        def table(env):
            return {var: self._meter(used_for(env[var])) for var in env
                    if env[var].startswith("ghs_fake_")}
        return table

    def test_two_logins_each_get_a_rise(self):
        for name, (_, (slot_one, pool)) in WIRED.items():
            with self.subTest(workflow=name):
                outputs = {
                    slot_one: {"app-slug": "agent-bureau-qa-bot", "installation-id": "101",
                               "token": "ghs_fake_one"},
                    pool: {"app-slug": "agent-bureau-pool-2", "installation-id": "202",
                           "token": "ghs_fake_pool"},
                }
                before = self._by_token(lambda t: {"ghs_fake_one": 100, "ghs_fake_pool": 10}[t])
                after = self._by_token(lambda t: {"ghs_fake_one": 140, "ghs_fake_pool": 90}[t])
                procs, doc, gh_out = self._run(name, outputs, before, after)
                for proc in procs:
                    self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertIsNotNone(doc, procs[1].stdout)
                self.assertEqual(doc["schema"], gcr.SCHEMA)
                self.assertEqual(doc["workflow"], name)
                rows = {r["installation_id"]: r for r in doc["logins"]}
                self.assertEqual(set(rows), {101, 202})
                self.assertEqual(rows[101]["app"], "agent-bureau-qa-bot")
                self.assertEqual(rows[101]["rise"], 39)
                self.assertEqual(rows[202]["rise"], 79)
                self.assertIn("uploaded=false", gh_out)
                self.assertNotIn("ghs_fake_", procs[0].stdout + procs[1].stdout)
                self.assertNotIn("ghs_fake_", json.dumps(doc))

    def test_a_pool_that_fell_back_to_slot_one_is_one_login_read_on_the_fresher_token(self):
        for name, (_, (slot_one, pool)) in WIRED.items():
            with self.subTest(workflow=name):
                outputs = {
                    slot_one: {"app-slug": "agent-bureau-bot", "installation-id": "101",
                               "token": "ghs_fake_first"},
                    pool: {"app-slug": "agent-bureau-bot", "installation-id": "101",
                           "token": "ghs_fake_fresh"},
                }
                before = self._by_token(lambda t: 100)
                # The first token has expired by the job's end; only the fresh
                # one still answers.
                after = self._by_token(lambda t: 150 if t == "ghs_fake_fresh" else 0)

                def after_table(env, _after=after):
                    table = _after(env)
                    for var, value in env.items():
                        if value == "ghs_fake_first":
                            table[var] = {"status": 401}
                    return table

                procs, doc, _ = self._run(name, outputs, before, after_table)
                for proc in procs:
                    self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(len(doc["logins"]), 1)
                self.assertEqual(doc["logins"][0]["after"]["used"], 150)
                self.assertEqual(doc["logins"][0]["rise"], 49)

    def test_a_job_whose_pool_mint_never_ran_still_records_its_slot_one_login(self):
        # An out-of-scope verify, a no-review critic run: the pool mint's
        # outputs render as '' and that login is simply not there.
        for name, (_, (slot_one, _pool)) in WIRED.items():
            with self.subTest(workflow=name):
                outputs = {slot_one: {"app-slug": "agent-bureau-bot", "installation-id": "101",
                                      "token": "ghs_fake_one"}}
                procs, doc, _ = self._run(name, outputs, self._by_token(lambda t: 5),
                                          self._by_token(lambda t: 9))
                for proc in procs:
                    self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual([r["installation_id"] for r in doc["logins"]], [101])
                self.assertEqual(doc["logins"][0]["rise"], 3)


class TheBoundaries(unittest.TestCase):
    def test_both_files_are_trusted_by_the_upload_role(self):
        for name in WIRED:
            self.assertIn(name, UPLOAD_TRUSTED_REUSABLES)

    def test_the_new_steps_and_both_files_stay_under_the_expression_ceiling(self):
        for name in WIRED:
            doc = yaml.safe_load((WORKFLOWS / name).read_text())
            for job in doc["jobs"].values():
                for step in job.get("steps") or []:
                    script = step.get("run")
                    if not isinstance(script, str):
                        continue
                    with self.subTest(workflow=name, step=step.get("name")):
                        self.assertLessEqual(len(script), RUN_BLOCK_CEILING)
                        if "${{" in script:
                            self.assertLessEqual(compiled_expression_length(script), BUDGET)


if __name__ == "__main__":
    unittest.main()
