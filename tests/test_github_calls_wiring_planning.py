"""The planning run and the red-main repair run record their GitHub calls per pool login (DRE-6918).

The fourth pipeline half of epic DRE-6911. `scripts/github_calls_reading.py`
(DRE-6914) takes two points on each pool login's hourly meter — `before`, after
the job's mints, and `emit`, at its end — and keeps them in the agent-log store.
This file pins where those two steps sit in `plan.yml` and
`red-main-repair.yml`; the script and its document are DRE-6914's, and the
engineer/fix/medic and critic/verifier runs are their own cards' (DRE-6916,
DRE-6917), each with its own test file.

WHICH JOBS ARE WIRED, AND WHY.

  * `plan.yml`'s `plan` job — the planner, the two critics and every re-plan,
    on the slot-1 bot and the pool-selected app.
  * `plan.yml`'s `publish` job — it mints the slot-1 bot and calls GitHub with
    it to check the plan portal out and push the page, but only when the repo
    names a portal (`vars.PLAN_PORTAL_REPO`); with none, the token is never
    spent, so both steps are gated on the same variable as the portal checkout.
  * `red-main-repair.yml`'s `repair` job — the slot-1 bot (`app`) and the
    pool-selected worker (`worker`).

THE STEP CONTRACT (DRE-6914's docstring). ONE `before` step after the job's
work-token mints, naming each INSTALLATION once at the first mint that reaches
it, by `app-slug` and `installation-id` output, the token as a NAMED env var.
ONE `emit` step, `always()` + `continue-on-error`, immediately before `Keep the
run's working log` (the job's last step where it keeps none), handing the
freshest token per installation. The pool probe tokens are never listed.

THE PLAN JOB'S FRESHEST TOKEN. `tests/test_plan_token_remint.py` forbids every
step after the planner to read the start-of-job token, and forbids any step to
read a token across a model step — GitHub gives an installation token sixty
minutes, and this job runs for up to 115. Every re-mint in the file is spent by
the model step after it, so none of them can be handed to `emit`; the portal
mint is pinned to its one checkout. So the plan job re-mints the READER's pool
map once more, directly before `emit` (`app_calls`) — the reader's installation
again. The plan job's `before` carries the reader's own gate (a bounced,
refused or unadmitted dispatch does no work and records nothing; and
tests/test_plan_dispatch_dedupe.py lets no step after the guard run
ungated), and the reading's mint and `emit` run only where `before` did.
The slot-1 bot is also handed `next_token` — the end-of-run mint for the
next card's owner — but only when GitHub says that mint reached the SAME
installation, so another account's installation is never recorded as a meter
this job read before.

Run: python3 -m pytest tests/test_github_calls_wiring_planning.py -v
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
sys.path.insert(0, str(ROOT / "tests"))

from test_self_stubs_grant_id_token import UPLOAD_TRUSTED_REUSABLES  # noqa: E402
from test_workflow_expression_budget import (  # noqa: E402
    BUDGET,
    RUN_BLOCK_CEILING,
    compiled_expression_length,
)

MINT = "actions/create-github-app-token"
MODEL = "anthropics/claude-code-action"
CHECKOUT = "actions/checkout"
KEEP_LOG = "Keep the run's working log"
PIPELINE_CHECKOUT = "the bureau-pipeline checkout"
BEFORE_CMD = "github_calls_reading.py before"
EMIT_CMD = "github_calls_reading.py emit"

#: `--login "$<APP>:$<INSTALLATION>:<TOKEN_ENV_NAME>"` as the steps spell it.
LOGIN = re.compile(r'--login\s+"\$([A-Z0-9_]+):\$([A-Z0-9_]+):([A-Z0-9_]+)"')
OUTPUT_REF = re.compile(r"^\$\{\{ steps\.([A-Za-z0-9_]+)\.outputs\.([a-z-]+) \}\}$")
#: The one guarded shape: hand a mint's installation only when it is a listed one.
SAME_INSTALLATION = re.compile(
    r"^\$\{\{ steps\.([A-Za-z0-9_]+)\.outputs\.installation-id == "
    r"steps\.([A-Za-z0-9_]+)\.outputs\.installation-id && "
    r"steps\.\2\.outputs\.installation-id \|\| '' \}\}$")


@dataclass(frozen=True)
class Wired:
    #: The step the `before` step sits immediately after: a mint's id, or the
    #: bureau-pipeline checkout where the job's only mint precedes it.
    follows: str
    #: The mints `before` names, in order — each installation once, at the
    #: first mint that reaches it.
    listed: tuple[str, ...]
    #: The step `emit` sits immediately before; None where it is the last step.
    emit_precedes: str | None
    #: `(token mint, listed mint it is guarded on or None)` per `emit` login,
    #: in order — the first login naming an installation wins.
    handed: tuple[tuple[str, str | None], ...]
    #: A condition both steps carry beside `always()`, or None.
    gate: str | None = None


WIRED = {
    ("plan.yml", "plan"): Wired(
        follows="reader",
        listed=("app", "reader"),
        emit_precedes=KEEP_LOG,
        handed=(("app_calls", None), ("next_token", "app"))),
    ("plan.yml", "publish"): Wired(
        follows=PIPELINE_CHECKOUT,
        listed=("app",),
        emit_precedes=None,
        handed=(("app", None),),
        gate="vars.PLAN_PORTAL_REPO != ''"),
    ("red-main-repair.yml", "repair"): Wired(
        follows="worker",
        listed=("app", "worker"),
        emit_precedes=KEEP_LOG,
        handed=(("worker", None), ("app", None))),
}

#: The plan job's own re-mint for the reading — the reader's pool map again.
PLAN_READING_MINT = "app_calls"


def _doc(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _steps(name: str, job: str) -> list[dict]:
    return _doc(name)["jobs"][job]["steps"]


def _action(step: dict) -> str:
    return str(step.get("uses") or "").split("@")[0]


def _flat(text) -> str:
    return " ".join(str(text or "").split())


def _one(steps: list[dict], command: str, where: str) -> int:
    found = [i for i, s in enumerate(steps) if command in str(s.get("run") or "")]
    if len(found) != 1:
        raise AssertionError(f"{where}: {len(found)} steps run `{command}`, want exactly one")
    return found[0]


def _by_id(steps: list[dict], step_id: str) -> int:
    found = [i for i, s in enumerate(steps) if s.get("id") == step_id]
    if len(found) != 1:
        raise AssertionError(f"{len(found)} steps with id {step_id!r}")
    return found[0]


def _installation(step: dict) -> tuple[str, ...]:
    """What decides which installation a mint reaches: the App it mints
    from, and the account and repositories it is scoped to (none — this
    repository's own account)."""
    w = step.get("with") or {}
    return tuple(_flat(w.get(k)) for k in ("app-id", "private-key", "owner", "repositories"))


def _logins(step: dict) -> list[tuple[str, str, str]]:
    """`(app value, installation value, token value)` per `--login`, read
    through the step's own env."""
    env = step.get("env") or {}
    rows = []
    for app, installation, token in LOGIN.findall(step.get("run") or ""):
        rows.append((env.get(app, ""), env.get(installation, ""), env.get(token, "")))
    return rows


def _ref(value: str, output: str) -> str:
    m = OUTPUT_REF.match(value)
    if not m or m.group(2) != output:
        raise AssertionError(f"{value!r} is not one mint's `{output}` output")
    return m.group(1)


class Placement(unittest.TestCase):

    def test_each_wired_job_has_one_before_and_one_emit_step(self):
        for (name, job) in WIRED:
            with self.subTest(workflow=name, job=job):
                steps = _steps(name, job)
                before = _one(steps, BEFORE_CMD, f"{name}:{job}")
                emit = _one(steps, EMIT_CMD, f"{name}:{job}")
                self.assertLess(before, emit)

    def test_the_before_step_follows_the_jobs_work_token_mints(self):
        for (name, job), wired in WIRED.items():
            with self.subTest(workflow=name, job=job):
                steps = _steps(name, job)
                before = _one(steps, BEFORE_CMD, f"{name}:{job}")
                prior = steps[before - 1]
                if wired.follows == PIPELINE_CHECKOUT:
                    self.assertEqual(_action(prior), CHECKOUT)
                    self.assertEqual((prior.get("with") or {}).get("repository"),
                                     "dreadnought-foundry/bureau-pipeline")
                else:
                    self.assertEqual(prior.get("id"), wired.follows)
                for mint in wired.listed:
                    self.assertLess(_by_id(steps, mint), before, mint)

    def test_the_emit_step_sits_before_the_working_log_or_ends_the_job(self):
        for (name, job), wired in WIRED.items():
            with self.subTest(workflow=name, job=job):
                steps = _steps(name, job)
                emit = _one(steps, EMIT_CMD, f"{name}:{job}")
                if wired.emit_precedes is None:
                    self.assertEqual(emit, len(steps) - 1, "the job's last step")
                else:
                    self.assertEqual(steps[emit + 1].get("name"), wired.emit_precedes)

    def test_neither_step_can_change_the_jobs_conclusion(self):
        for (name, job), wired in WIRED.items():
            steps = _steps(name, job)
            for command in (BEFORE_CMD, EMIT_CMD):
                with self.subTest(workflow=name, job=job, step=command):
                    step = steps[_one(steps, command, f"{name}:{job}")]
                    self.assertIs(step.get("continue-on-error"), True)
                    self.assertNotIn("${{", step["run"], "substitutions go in env:")
                    if wired.gate:
                        self.assertIn(wired.gate, str(step.get("if")))

    def test_the_emit_step_runs_on_every_outcome_and_reports(self):
        for (name, job) in WIRED:
            with self.subTest(workflow=name, job=job):
                steps = _steps(name, job)
                step = steps[_one(steps, EMIT_CMD, f"{name}:{job}")]
                self.assertIn("always()", str(step.get("if")))
                self.assertIn('--github-output "$GITHUB_OUTPUT"', step["run"])
                self.assertIn(f"--workflow {name}", step["run"])

    def test_the_emit_step_reads_the_file_the_before_step_wrote(self):
        for (name, job) in WIRED:
            with self.subTest(workflow=name, job=job):
                steps = _steps(name, job)
                before = steps[_one(steps, BEFORE_CMD, f"{name}:{job}")]["run"]
                emit = steps[_one(steps, EMIT_CMD, f"{name}:{job}")]["run"]
                written = re.search(r'--out\s+("[^"]+"|\S+)', before).group(1)
                read = re.search(r'--before\s+("[^"]+"|\S+)', emit).group(1)
                self.assertEqual(read, written)


class TheBeforeStepNamesEachInstallationOnce(unittest.TestCase):

    def test_it_names_the_wired_mints_by_slug_installation_and_named_token(self):
        for (name, job), wired in WIRED.items():
            with self.subTest(workflow=name, job=job):
                steps = _steps(name, job)
                step = steps[_one(steps, BEFORE_CMD, f"{name}:{job}")]
                named = []
                for app, installation, token in _logins(step):
                    mint = _ref(app, "app-slug")
                    self.assertEqual(_ref(installation, "installation-id"), mint)
                    self.assertEqual(_ref(token, "token"), mint)
                    self.assertEqual(_action(steps[_by_id(steps, mint)]), MINT)
                    named.append(mint)
                self.assertEqual(tuple(named), wired.listed)

    def test_it_names_every_distinct_installation_the_job_works_with(self):
        # Read off the file's mint steps, never a remembered count: every
        # mint before the `before` step that is not a pool probe reaches an
        # installation, and each one must be named exactly once.
        for (name, job), wired in WIRED.items():
            with self.subTest(workflow=name, job=job):
                steps = _steps(name, job)
                before = _one(steps, BEFORE_CMD, f"{name}:{job}")
                reached = {}
                for i, step in enumerate(steps[:before]):
                    if _action(step) != MINT or str(step.get("id", "")).startswith("probe_"):
                        continue
                    reached.setdefault(_installation(step), step.get("id"))
                named = [_ref(app, "app-slug") for app, _i, _t in _logins(steps[before])]
                keys = [_installation(steps[_by_id(steps, m)]) for m in named]
                self.assertEqual(len(keys), len(set(keys)), "an installation named twice")
                self.assertEqual(sorted(named), sorted(reached.values()))

    def test_every_later_mint_is_an_installation_already_named_or_another_account(self):
        # A mint after `before` on an installation `before` never read would
        # have no first point. The one kind allowed is a mint scoped to
        # another account (`owner:`), which `emit` hands only when GitHub says
        # it reached a named installation after all.
        for (name, job) in WIRED:
            with self.subTest(workflow=name, job=job):
                steps = _steps(name, job)
                before = _one(steps, BEFORE_CMD, f"{name}:{job}")
                emit = _one(steps, EMIT_CMD, f"{name}:{job}")
                named = {_installation(steps[_by_id(steps, _ref(a, "app-slug"))])
                         for a, _i, _t in _logins(steps[before])}
                for step in steps[before + 1:emit]:
                    if _action(step) != MINT:
                        continue
                    if (step.get("with") or {}).get("owner"):
                        continue
                    self.assertIn(_installation(step), named, step.get("name"))

    def test_no_pool_probe_token_is_listed_or_handed(self):
        for (name, job) in WIRED:
            steps = _steps(name, job)
            for command in (BEFORE_CMD, EMIT_CMD):
                with self.subTest(workflow=name, job=job, step=command):
                    step = steps[_one(steps, command, f"{name}:{job}")]
                    self.assertNotIn("probe_", yaml.safe_dump(step))

    def test_the_plan_jobs_count_is_two_today_and_the_test_reads_it(self):
        # The derivation above, made to fail if it silently found nothing.
        steps = _steps("plan.yml", "plan")
        before = _one(steps, BEFORE_CMD, "plan.yml:plan")
        mints = [s for s in steps[:before]
                 if _action(s) == MINT and not str(s.get("id", "")).startswith("probe_")]
        self.assertGreaterEqual(len({_installation(s) for s in mints}), 2)


class TheEmitStepHandsTheFreshestTokenPerInstallation(unittest.TestCase):

    def test_it_hands_the_pinned_mints_in_order(self):
        for (name, job), wired in WIRED.items():
            with self.subTest(workflow=name, job=job):
                steps = _steps(name, job)
                step = steps[_one(steps, EMIT_CMD, f"{name}:{job}")]
                handed = []
                for app, installation, token in _logins(step):
                    mint = _ref(token, "token")
                    self.assertEqual(_ref(app, "app-slug"), mint)
                    guard = SAME_INSTALLATION.match(installation)
                    if guard:
                        self.assertEqual(guard.group(1), mint)
                        handed.append((mint, guard.group(2)))
                    else:
                        self.assertEqual(_ref(installation, "installation-id"), mint)
                        handed.append((mint, None))
                self.assertEqual(tuple(handed), wired.handed)

    def test_every_named_installation_is_handed_a_token(self):
        for (name, job), wired in WIRED.items():
            with self.subTest(workflow=name, job=job):
                steps = _steps(name, job)
                covered = set()
                for mint, guard in wired.handed:
                    covered.add(_installation(steps[_by_id(steps, guard or mint)]))
                for mint in wired.listed:
                    self.assertIn(_installation(steps[_by_id(steps, mint)]), covered, mint)

    def test_each_handed_token_is_its_installations_last_mint_before_the_emit(self):
        for (name, job), wired in WIRED.items():
            with self.subTest(workflow=name, job=job):
                steps = _steps(name, job)
                emit = _one(steps, EMIT_CMD, f"{name}:{job}")
                for mint, _guard in wired.handed:
                    at = _by_id(steps, mint)
                    key = _installation(steps[at])
                    later = [s.get("id") for s in steps[at + 1:emit]
                             if _action(s) == MINT and _installation(s) == key]
                    self.assertEqual(later, [], f"{mint} is not the freshest")

    def test_the_plan_jobs_reading_mint_is_the_reader_again_directly_before_emit(self):
        # No token the plan job holds across a model step may be spent
        # (test_plan_token_remint.py), so the reading takes its own re-mint of
        # the reader's pool map, after the last model step.
        steps = _steps("plan.yml", "plan")
        emit = _one(steps, EMIT_CMD, "plan.yml:plan")
        at = _by_id(steps, PLAN_READING_MINT)
        reader = steps[_by_id(steps, "reader")]
        self.assertEqual(at, emit - 1)
        self.assertEqual(steps[at].get("uses"), reader.get("uses"))
        self.assertEqual(steps[at].get("with"), reader.get("with"))
        self.assertIn("always()", str(steps[at].get("if")))
        last_model = max(i for i, s in enumerate(steps) if _action(s) == MODEL)
        self.assertGreater(at, last_model)

    def test_the_plan_job_reads_only_where_the_reader_ran(self):
        steps = _steps("plan.yml", "plan")
        before = steps[_one(steps, BEFORE_CMD, "plan.yml:plan")]
        reader = steps[_by_id(steps, "reader")]
        self.assertTrue(str(reader.get("if") or "").strip())
        self.assertEqual(before.get("if"), reader.get("if"))
        took_it = "steps.calls_before.outcome == 'success'"
        self.assertEqual(before.get("id"), "calls_before")
        for step in (steps[_by_id(steps, PLAN_READING_MINT)],
                     steps[_one(steps, EMIT_CMD, "plan.yml:plan")]):
            self.assertIn(took_it, str(step.get("if")), step.get("name"))

    def test_no_plan_job_token_handed_to_emit_crossed_a_model_step(self):
        steps = _steps("plan.yml", "plan")
        emit = _one(steps, EMIT_CMD, "plan.yml:plan")
        for mint, _guard in WIRED[("plan.yml", "plan")].handed:
            at = _by_id(steps, mint)
            between = [s.get("name") for s in steps[at + 1:emit] if _action(s) == MODEL]
            self.assertEqual(between, [], mint)


class TheFilesStayInsideTheirLimits(unittest.TestCase):

    def test_both_files_are_trusted_by_the_upload_role(self):
        for name in {name for name, _job in WIRED}:
            self.assertIn(name, UPLOAD_TRUSTED_REUSABLES)

    def test_both_files_stay_under_the_expression_budget_and_run_block_ceiling(self):
        for name in {name for name, _job in WIRED}:
            for job_name, job in _doc(name)["jobs"].items():
                for step in job.get("steps") or []:
                    script = step.get("run")
                    if not isinstance(script, str):
                        continue
                    with self.subTest(workflow=name, job=job_name, step=step.get("name")):
                        self.assertLessEqual(len(script), RUN_BLOCK_CEILING)
                        if "${{" in script:
                            self.assertLessEqual(compiled_expression_length(script), BUDGET)


# --------------------------------------------------------------------------- #
# The steps' own shell, run against the real script                            #
# --------------------------------------------------------------------------- #

FAKES = "BUREAU_FAKE_GITHUB_CALLS_PROBES"
RESET = 1_760_000_000


def _meter(used: int) -> dict:
    return {"status": 200, "x-ratelimit-used": str(used), "x-ratelimit-limit": "5000",
            "x-ratelimit-remaining": str(5000 - used), "x-ratelimit-reset": str(RESET),
            "x-ratelimit-resource": "core"}


def _render(value: str, outputs: dict) -> str:
    """One env value as GitHub would render it from the mints' outputs: a
    plain output reference, or the same-installation guard."""
    value = str(value)
    guard = SAME_INSTALLATION.match(value)
    if guard:
        mint, listed = guard.groups()
        theirs = outputs.get(mint, {}).get("installation-id", "")
        ours = outputs.get(listed, {}).get("installation-id", "")
        return ours if theirs == ours and ours else ""
    m = OUTPUT_REF.match(value)
    if m:
        return outputs.get(m.group(1), {}).get(m.group(2), "")
    if "${{" in value:
        raise AssertionError(f"no renderer for {value!r}")
    return value


class TheStepsRecordAReading(unittest.TestCase):
    """The wired shell, executed: the outputs a run's mints would give, the
    meters a fake GitHub answers, and the document `emit` writes."""

    def _run_step(self, step: dict, outputs: dict, fakes: dict, tmp: Path) -> str:
        env = {"PATH": os.environ["PATH"], "RUNNER_TEMP": str(tmp),
               "GITHUB_OUTPUT": str(tmp / "github-output"),
               "GITHUB_REPOSITORY": "dreadnought-foundry/bureau-pipeline",
               "GITHUB_RUN_ID": "4242", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_JOB": "job",
               FAKES: json.dumps(fakes)}
        for key, value in (step.get("env") or {}).items():
            env[key] = _render(value, outputs)
        done = subprocess.run(["bash", "-e", "-c", step["run"]], cwd=tmp, env=env,
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def _reading(self, name, job, outputs, before_used, after_used) -> dict:
        steps = _steps(name, job)
        before = steps[_one(steps, BEFORE_CMD, f"{name}:{job}")]
        emit = steps[_one(steps, EMIT_CMD, f"{name}:{job}")]
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            (tmp / ".bureau-pipeline").symlink_to(ROOT)
            self._run_step(before, outputs, {k: _meter(v) for k, v in before_used.items()}, tmp)
            out = self._run_step(emit, outputs, {k: _meter(v) for k, v in after_used.items()}, tmp)
            self.assertIn("github-calls: ", out)
            for value in ("tok-", ):
                self.assertNotIn(value, out, "a token value reached the log")
            return json.loads((tmp / "github-calls-reading.json").read_text())

    @staticmethod
    def _mint(slug, installation, token):
        return {"app-slug": slug, "installation-id": str(installation), "token": token}

    def _token_envs(self, name, job, command):
        steps = _steps(name, job)
        return [t for _a, _i, t in LOGIN.findall(steps[_one(steps, command, "")]["run"])]

    def test_plan_on_a_pool_slot_reads_both_installations(self):
        bot = self._mint("agent-bureau-bot", 11, "tok-bot")
        pool = self._mint("agent-bureau-bot-3", 33, "tok-pool")
        outputs = {"app": bot, "reader": pool,
                   PLAN_READING_MINT: self._mint("agent-bureau-bot-3", 33, "tok-fresh"),
                   "next_token": self._mint("agent-bureau-bot", 11, "tok-next")}
        b_bot, b_pool = self._token_envs("plan.yml", "plan", BEFORE_CMD)
        a_pool, a_next = self._token_envs("plan.yml", "plan", EMIT_CMD)
        doc = self._reading("plan.yml", "plan", outputs,
                            {b_bot: 100, b_pool: 500}, {a_pool: 900, a_next: 140})
        rows = {r["installation_id"]: r for r in doc["logins"]}
        self.assertEqual(sorted(rows), [11, 33])
        self.assertEqual(rows[33]["rise"], 900 - 500 - 1)
        self.assertEqual(rows[11]["rise"], 140 - 100 - 1)
        self.assertEqual(doc["workflow"], "plan.yml")

    def test_plan_never_hands_another_accounts_installation(self):
        outputs = {"app": self._mint("agent-bureau-bot", 11, "tok-bot"),
                   "reader": self._mint("agent-bureau-bot-3", 33, "tok-pool"),
                   PLAN_READING_MINT: self._mint("agent-bureau-bot-3", 33, "tok-fresh"),
                   "next_token": self._mint("agent-bureau-bot", 77, "tok-next")}
        b_bot, b_pool = self._token_envs("plan.yml", "plan", BEFORE_CMD)
        a_pool, a_next = self._token_envs("plan.yml", "plan", EMIT_CMD)
        doc = self._reading("plan.yml", "plan", outputs,
                            {b_bot: 100, b_pool: 500}, {a_pool: 900, a_next: 140})
        rows = {r["installation_id"]: r for r in doc["logins"]}
        self.assertEqual(sorted(rows), [11, 33], "installation 77 was never read before")
        self.assertIsNone(rows[11]["after"], "slot 1 had no fresh token this run")

    def test_plan_on_one_app_reads_one_installation_on_the_fresh_token(self):
        # A single-app pool: the reader, and the reading's re-mint, fall
        # through to slot 1 — one installation, one meter, one row.
        same = self._mint("agent-bureau-bot", 11, "tok")
        outputs = {"app": same, "reader": same, PLAN_READING_MINT: same}
        b_bot, b_pool = self._token_envs("plan.yml", "plan", BEFORE_CMD)
        a_pool, a_next = self._token_envs("plan.yml", "plan", EMIT_CMD)
        doc = self._reading("plan.yml", "plan", outputs,
                            {b_bot: 100, b_pool: 100}, {a_pool: 300, a_next: 999})
        self.assertEqual([r["installation_id"] for r in doc["logins"]], [11])
        self.assertEqual(doc["logins"][0]["rise"], 300 - 100 - 1)

    def test_a_debounced_repair_run_reads_the_bot_alone(self):
        # The decide step said no: no pool selection, no worker mint, whose
        # outputs render '' and name no login.
        outputs = {"app": self._mint("agent-bureau-bot", 11, "tok-bot")}
        b_bot, b_worker = self._token_envs("red-main-repair.yml", "repair", BEFORE_CMD)
        a_worker, a_bot = self._token_envs("red-main-repair.yml", "repair", EMIT_CMD)
        doc = self._reading("red-main-repair.yml", "repair", outputs, {b_bot: 10}, {a_bot: 15})
        self.assertEqual([(r["installation_id"], r["rise"]) for r in doc["logins"]],
                         [(11, 4)])

    def test_repair_reads_the_bot_and_the_worker(self):
        outputs = {"app": self._mint("agent-bureau-bot", 11, "tok-bot"),
                   "worker": self._mint("agent-bureau-bot-2", 22, "tok-worker")}
        b_bot, b_worker = self._token_envs("red-main-repair.yml", "repair", BEFORE_CMD)
        a_worker, a_bot = self._token_envs("red-main-repair.yml", "repair", EMIT_CMD)
        doc = self._reading("red-main-repair.yml", "repair", outputs,
                            {b_bot: 50, b_worker: 70}, {a_worker: 170, a_bot: 60})
        rows = {r["installation_id"]: r["rise"] for r in doc["logins"]}
        self.assertEqual(rows, {11: 9, 22: 99})
        self.assertEqual(doc["workflow"], "red-main-repair.yml")

    def test_publish_reads_the_bot(self):
        outputs = {"app": self._mint("agent-bureau-bot", 11, "tok-bot")}
        (b_bot,) = self._token_envs("plan.yml", "publish", BEFORE_CMD)
        (a_bot,) = self._token_envs("plan.yml", "publish", EMIT_CMD)
        doc = self._reading("plan.yml", "publish", outputs, {b_bot: 1}, {a_bot: 5})
        self.assertEqual([(r["installation_id"], r["rise"]) for r in doc["logins"]],
                         [(11, 3)])


if __name__ == "__main__":
    unittest.main()
