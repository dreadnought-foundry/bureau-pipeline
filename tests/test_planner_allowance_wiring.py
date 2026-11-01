"""plan.yml reads the planners' Linear allowance where it used to claim a slot (DRE-8617, epic DRE-8539).

The CEO's decision of 2026-10-31: no fixed number of planners. DRE-8616 built
`planner_allowance.py wait`, which starts a planner at once while the
planners' Linear allowance is healthy and holds it only while the allowance is
nearly used up. This card wires the plan workflow to it, and takes out the
slot claim, release and next-in-line steps it replaces.

THE WIRING UNDER TEST:

  1. one step, `Planner allowance — start or wait` (`id: allowance`),
     directly after `Duplicate-dispatch guard` and before the first pool
     probe, the classifier, the route step and every model step. It carries
     the guard's own `if:`, holds every value in `env:` (DRE-1996), runs the
     contract's command line verbatim, answers `start=true` with a
     `::warning::` when the script crashes, and carries no
     `continue-on-error`;
  2. every step that was gated on the slot's admission is gated on
     `steps.allowance.outputs.start == 'true'`, and a run whose step did not
     answer `start=true` reaches no model step, no route and no Green Light
     writer — walked through the gates, never read off step names;
  3. the slot steps, `planner_queue.py` and `PLANNER_MAX_RUNNING` are gone
     from the file;
  4. `jobs.plan.timeout-minutes` is the planner's own budget plus
     `hold_max_minutes` plus `wait_minutes`, both read from
     `config/planner-allowance.json`, so the pin moves with either;
  5. the step's shell, executed: a healthy `PLANNER_ALLOWANCE_FAKE` reading
     answers `start=true` with no request and no receipt, and a script that
     exits 1 still answers `start=true`, loudly. That proves the wiring only:
     the low and refused paths are DRE-8616's, proven through
     `linear_ops.gql` in `tests/test_planner_allowance.py`;
  6. the duplicate-dispatch guard, and its `SENT_BY_RUN` read, are
     byte-for-byte what they were at the merge base with `main`.

Run: cd bureau-pipeline && python3 -m pytest tests/test_planner_allowance_wiring.py -v
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import test_plan_critic_wiring as pcw  # noqa: E402
from workflow_walk import Walk, truth  # noqa: E402

WF = os.path.join(ROOT, ".github", "workflows", "plan.yml")
WF_REL = ".github/workflows/plan.yml"
CONFIG = os.path.join(ROOT, "config", "planner-allowance.json")
SCRIPT = os.path.join(ROOT, "scripts", "planner_allowance.py")

NAME = "Planner allowance — start or wait"
ID = "allowance"
START = "steps.allowance.outputs.start == 'true'"
GUARD_IF = "steps.gate.outputs.bounced != 'true' && steps.dedupe.outputs.skip != 'true'"
DEDUPE = "Duplicate-dispatch guard"
DEDUPE_CLAUSE = "steps.dedupe.outputs.skip != 'true'"
FIRST_PROBE = "Mint pool probe token (2)"
CLASSIFY = "Classify the card — one-off, epic or wave"
ROUTE = "Route — plan or activate"
ACTION = "anthropics/claude-code-action"
PLANNER_KEY_EXPR = "${{ secrets.LINEAR_PLANNER_KEY || secrets.LINEAR_API_KEY }}"
COMMAND = ('python3 .bureau-pipeline/scripts/planner_allowance.py wait "$CARD" '
           '--run-id "$GITHUB_RUN_ID" --repo "$GITHUB_REPOSITORY" --title "$TITLE" '
           '--github-output "$GITHUB_OUTPUT"')
ENV = {
    "LINEAR_API_KEY": PLANNER_KEY_EXPR,
    "LINEAR_API_KEY_FALLBACK": "${{ secrets.LINEAR_API_KEY }}",
    "CARD": "${{ github.event.client_payload.identifier }}",
    "TITLE": "${{ github.event.client_payload.title }}",
    "BUREAU_READ_URL": "${{ vars.BUREAU_READ_URL }}",
    "BUREAU_READ_AUDIENCE": "${{ vars.BUREAU_READ_AUDIENCE }}",
    "BUREAU_PIPELINE_REF": "${{ inputs.pipeline_ref }}",
}
# Every step whose `if:` carried the slot step's admission clause on
# 2026-11-01, by `id:`, less the two slot steps this card deletes (`release`,
# `next`): 26 then, 24 now.
WAS_ADMITTED = {
    "probe_2", "probe_3", "probe_4", "pool", "reader", "calls_before", "ask", "answer",
    "model_home", "classify", "shape", "repo", "model", "route", "card",
    "install_claude", "posta", "postreplanturns", "postreplan",
    "model_home_postreplan_cap", "postreplan_cap", "app_post_retry",
    "postreplan_retry", "postreplan_done",
}


def wf_src() -> str:
    with open(WF, encoding="utf-8") as fh:
        return fh.read()


def steps(doc: dict | None = None) -> list[dict]:
    return (doc or yaml.safe_load(wf_src()))["jobs"]["plan"]["steps"]


def index_where(pred) -> int:
    for i, s in enumerate(steps()):
        if pred(s):
            return i
    raise AssertionError("no such step in plan.yml")


def step() -> dict:
    return steps()[index_where(lambda s: s.get("id") == ID)]


def gate(s: dict) -> str:
    return str(s.get("if") or "")


def allowance_config() -> dict:
    with open(CONFIG, encoding="utf-8") as fh:
        return json.load(fh)


# --------------------------------------------------------------------------- #
# 1. The step                                                                  #
# --------------------------------------------------------------------------- #


class TheAllowanceStep(unittest.TestCase):
    def setUp(self):
        self.step = step()
        self.run_block = str(self.step.get("run") or "")
        self.env = self.step.get("env") or {}

    def test_it_is_named_and_identified_as_the_contract_says(self):
        self.assertEqual(self.step.get("name"), NAME)

    def test_it_sits_after_the_guard_and_before_the_probes_the_classifier_and_every_model(self):
        at = index_where(lambda s: s.get("id") == ID)
        self.assertEqual(at, index_where(lambda s: s.get("name") == DEDUPE) + 1,
                         "the allowance step must follow the duplicate guard directly")
        self.assertLess(at, index_where(lambda s: s.get("name") == FIRST_PROBE))
        self.assertLess(at, index_where(lambda s: s.get("name") == CLASSIFY))
        self.assertLess(at, index_where(lambda s: s.get("name") == ROUTE))
        self.assertLess(at, index_where(
            lambda s: str(s.get("uses") or "").split("@")[0] == ACTION))

    def test_it_carries_the_guards_if(self):
        self.assertEqual(gate(self.step), GUARD_IF)

    def test_it_runs_the_contract_command_line_verbatim(self):
        self.assertIn(COMMAND, " ".join(self.run_block.replace("\\\n", " ").split()))

    def test_the_cli_accepts_every_flag_the_step_passes(self):
        out = subprocess.run([sys.executable, SCRIPT, "wait", "--help"],
                             capture_output=True, text=True, check=True).stdout
        for flag in ("--run-id", "--repo", "--title", "--github-output"):
            self.assertIn(flag, out, flag)

    def test_every_value_arrives_through_env_and_none_is_interpolated(self):
        self.assertEqual(self.env, ENV)
        self.assertNotIn("${{", self.run_block)

    def test_it_exits_zero_on_every_path_and_never_continues_on_error(self):
        self.assertNotIn("continue-on-error", self.step,
                         "a failed-but-continued step would leave `start` unset "
                         "and skip the whole run silently")
        self.assertRegex(self.run_block, r"\|\|\s*\{")
        self.assertIn('echo "start=true" >> "$GITHUB_OUTPUT"', self.run_block)
        self.assertIn('echo "because=read-failed" >> "$GITHUB_OUTPUT"', self.run_block)
        self.assertIn("::warning::", self.run_block)

    def test_its_comment_block_answers_the_five_questions(self):
        src = wf_src()
        end = src.index(f"        id: {ID}\n")
        start = src.rindex("      - name: " + DEDUPE, 0, end)
        start = src.index("\n\n", start)  # past the guard's own body
        block = " ".join(src[start:end].replace("#", " ").split())
        for q in ("Q1", "Q2", "Q3", "Q4", "Q5"):
            self.assertIn(q, block, q)
        q3 = block[block.index("Q3"):block.index("Q4")]
        q5 = block[block.index("Q5"):]
        for text in (q3, q5):
            self.assertIn("RATELIMITED", text)
        for word in ("lift", "no receipt", "new process", "DRE-8616"):
            self.assertIn(word, q3 + q5, word)


# --------------------------------------------------------------------------- #
# 2. The gate chain                                                            #
# --------------------------------------------------------------------------- #


class TheChain(unittest.TestCase):
    def test_every_step_that_was_gated_on_admission_is_gated_on_start(self):
        found = {s.get("id") for s in steps() if START in gate(s)}
        self.assertEqual(found, WAS_ADMITTED)

    def test_every_step_the_duplicate_guard_gates_the_allowance_gates_too(self):
        chained = [s for s in steps() if DEDUPE_CLAUSE in gate(s) and s.get("id") != ID]
        self.assertTrue(chained)
        for s in chained:
            self.assertIn(START, gate(s), s.get("name"))


def reach(known: dict) -> dict[int, bool | None]:
    walk, verdict = Walk(known, set()), {}
    for i, s in enumerate(steps()):
        expr = s.get("if")
        t = None if expr is None else truth(walk.evaluate(str(expr)))
        verdict[i] = t
        if t is False and s.get("id"):
            walk.skipped.add(s["id"])
    return verdict


def must_not_run() -> dict[int, str]:
    out = {}
    for i, s in enumerate(steps()):
        if s.get("name") == ROUTE:
            out[i] = "the route step"
        elif str(s.get("uses") or "").split("@")[0] == ACTION:
            out[i] = "a model step"
        elif "Green Light" in str(s.get("run") or ""):
            out[i] = "a Green Light writer"
    return out


class TheWalk(unittest.TestCase):
    """`start` is `true` on every path the step has, so `false` is never an
    answer it gives — but the gate is still the only thing between a run and
    its model steps, and this proves it is wired to them."""

    def test_the_targets_are_found(self):
        self.assertEqual(set(must_not_run().values()),
                         {"the route step", "a model step", "a Green Light writer"})

    def test_a_run_without_start_reaches_no_model_no_route_and_no_green_light_writer(self):
        verdict = reach({"steps.allowance.outputs.start": ""})
        names = [s.get("name") for s in steps()]
        for i, why in must_not_run().items():
            self.assertIs(verdict[i], False, f"{why} {names[i]!r} is reachable")

    def test_a_started_run_can_reach_every_one_of_them(self):
        verdict = reach({"steps.allowance.outputs.start": "true"})
        names = [s.get("name") for s in steps()]
        for i, why in must_not_run().items():
            self.assertIsNot(verdict[i], False, f"{why} {names[i]!r} is unreachable")


# --------------------------------------------------------------------------- #
# 3. The slot is gone                                                          #
# --------------------------------------------------------------------------- #


class TheSlotIsGone(unittest.TestCase):
    def test_no_slot_reference_queue_call_or_cap_variable_is_left(self):
        src = wf_src()
        # Spelled in pieces, so `git grep` for the slot gate finds no test either.
        for word in ("planner_queue", "steps." + "slot", "PLANNER_MAX_RUNNING"):
            self.assertNotIn(word, src, word)

    def test_no_step_name_or_id_mentions_a_planner_slot(self):
        for s in steps():
            for field in ("name", "id"):
                self.assertNotRegex(str(s.get(field) or "").lower(), r"\bslot\b",
                                    f"{field}: {s.get(field)}")
            self.assertNotIn("planner slot", str(s.get("name") or "").lower())

    def test_the_end_of_run_steps_are_gone(self):
        ids = {s.get("id") for s in steps()}
        self.assertEqual(ids & {"slot", "release", "next", "next_token", "next_dispatch"},
                         set())


# --------------------------------------------------------------------------- #
# 4. The job's clock                                                           #
# --------------------------------------------------------------------------- #


class TheJobTimeout(unittest.TestCase):
    def test_it_is_the_planners_own_budget_plus_the_whole_hold(self):
        cfg = allowance_config()
        timeout = yaml.safe_load(wf_src())["jobs"]["plan"]["timeout-minutes"]
        self.assertEqual(
            timeout,
            pcw.PLANNER_OWN_MINUTES + cfg["hold_max_minutes"] + cfg["wait_minutes"])

    def test_today_that_is_180(self):
        """The arithmetic above, at today's settings — red the day either
        setting moves without the job's clock moving with it."""
        cfg = allowance_config()
        self.assertEqual((cfg["hold_max_minutes"], cfg["wait_minutes"]), (60, 5))
        self.assertEqual(yaml.safe_load(wf_src())["jobs"]["plan"]["timeout-minutes"], 180)

    def test_the_planners_own_budget_covers_the_step_budgets(self):
        self.assertGreaterEqual(pcw.PLANNER_OWN_MINUTES, pcw.planner_step_minutes())

    def test_the_comment_says_why_and_the_claim_ttl_sentence_is_gone(self):
        src = wf_src()
        at = src.index("    timeout-minutes: 180\n")
        block = src[src.rindex("    # 115, raised from 105", 0, at):at]
        self.assertIn("hold_max_minutes", block)
        self.assertIn("wait_minutes", block)
        self.assertIn("DRE-8617", block)
        self.assertNotIn("claim TTL", block)
        self.assertNotIn("planner-queue.json", block)


# --------------------------------------------------------------------------- #
# 5. The step's shell, executed                                                #
# --------------------------------------------------------------------------- #


class TheShellRuns(unittest.TestCase):
    def _run(self, pipeline: str, fake: str | None) -> tuple[subprocess.CompletedProcess, str]:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            (tmp / ".bureau-pipeline").symlink_to(pipeline)
            out = tmp / "github-output"
            env = {"PATH": os.environ["PATH"], "CARD": "DRE-1", "TITLE": "A card `$(id)`",
                   "GITHUB_RUN_ID": "4242", "GITHUB_REPOSITORY": "dreadnought-foundry/x",
                   "GITHUB_OUTPUT": str(out)}
            if fake is not None:
                env["PLANNER_ALLOWANCE_FAKE"] = fake
            done = subprocess.run(["bash", "-e", "-c", step()["run"]], cwd=tmp, env=env,
                                  capture_output=True, text=True, timeout=120)
            return done, out.read_text(encoding="utf-8") if out.exists() else ""

    def test_a_healthy_reading_starts_at_once_with_no_receipt(self):
        done, out = self._run(ROOT, json.dumps([{"remaining": 4900, "limit": 5000}]))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("start=true\n", out)
        self.assertIn("because=allowance-ok\n", out)
        line = next(x for x in done.stdout.splitlines() if x.startswith("planner-allowance:"))
        self.assertIn("receipt none", line)
        self.assertIn("requests 0", line)
        self.assertNotIn("::warning::", done.stdout)

    def test_a_script_that_exits_1_still_starts_and_says_so(self):
        with tempfile.TemporaryDirectory() as raw:
            fake = Path(raw)
            (fake / "scripts").mkdir()
            (fake / "scripts" / "planner_allowance.py").write_text(
                "raise SystemExit(1)\n", encoding="utf-8")
            done, out = self._run(str(fake), None)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(out, "start=true\nbecause=read-failed\n")
        self.assertIn("::warning::", done.stdout)


# --------------------------------------------------------------------------- #
# 6. The duplicate guard is untouched                                          #
# --------------------------------------------------------------------------- #


def _git(*args: str) -> str | None:
    try:
        done = subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                              text=True, check=False)
    except OSError:
        return None
    return done.stdout if done.returncode == 0 else None


def _guard_text(src: str) -> str:
    """The guard's comment block and step, from its banner to the next step's
    first comment line."""
    start = src.index("      # --- The planner's duplicate-dispatch guard (DRE-3409)")
    body = src.index("      - name: " + DEDUPE, start)
    return src[start:src.index("\n\n", body)]


class TheDuplicateGuardIsUnchanged(unittest.TestCase):
    def test_the_guard_and_its_sent_by_run_read_match_main(self):
        base = (_git("merge-base", "HEAD", "origin/main") or "").strip()
        before = _git("show", f"{base}:{WF_REL}") if base else None
        if before is None:
            if os.environ.get("BUREAU_REQUIRE_GIT_HISTORY"):
                self.fail("no git history for plan.yml, and this job requires it")
            self.skipTest("no git history for plan.yml (a shallow checkout)")
        self.assertEqual(_guard_text(wf_src()), _guard_text(before))
        old = next(s for s in steps(yaml.safe_load(before)) if s.get("name") == DEDUPE)
        new = next(s for s in steps() if s.get("name") == DEDUPE)
        self.assertEqual(new, old)
        self.assertEqual(new["env"]["SENT_BY_RUN"],
                         "${{ github.event.client_payload.sent_by_run }}")


if __name__ == "__main__":
    unittest.main()
