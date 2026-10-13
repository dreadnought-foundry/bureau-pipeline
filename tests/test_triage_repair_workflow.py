"""The Triage Repair run (DRE-7179, epic DRE-7174): `.github/workflows/triage-repair.yml`.

`scripts/triage_repair.py` (DRE-7178) decides which plan-critic parks in
Triage get their one repair and applies the result; this file holds the
workflow that runs it to the epic's contract. Every test reads the LIVE
workflow file. The pieces of shell that decide anything — whether the run is
a dry run, which cards reach the matrix, what a `claim` exit of 3 does — are
EXECUTED under bash with a stub `python3` on the PATH, and the `if:` of the
steps that gate the planner and the apply are EVALUATED over every case, so
the answer is what the runner would do and not what the YAML looks like it
says.

The scenario at the end runs the `apply` step's own command line, filled in
for a matrix card and the directory the agent step's prompt names, through
`triage_repair.main` on `TriageRepairBoard`, with the real
`planning_escalation.escalate` and the real `config/lane-contract.json`.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import check_workflow_watchers  # noqa: E402
import switch_reason  # noqa: E402
import test_triage_repair as base  # noqa: E402 — its fixtures, never its classes
import triage_repair  # noqa: E402
from triage_repair_fakes import TriageRepairBoard  # noqa: E402

WORKFLOWS = ROOT / ".github" / "workflows"
WORKFLOW = WORKFLOWS / "triage-repair.yml"
PLAN = WORKFLOWS / "plan.yml"
MEDIC_STUB = WORKFLOWS / "self-medic.yml"

CRON = "4,19,34,49 * * * *"
ACTION = "anthropics/claude-code-action"
AGENT_STEP = "Repair the card"
INSTALL_ACTION = "./.bureau-pipeline/.github/actions/install-claude-code"
BEGIN = "===== BEGIN UNTRUSTED CARD TEXT ====="
END = "===== END UNTRUSTED CARD TEXT ====="
#: The one directory the prompt tells the planner to write to, and `apply`
#: reads from.
OUT_DIR = "${{ runner.temp }}/triage-repair/${{ matrix.card }}"
#: The copy of bureau-pipeline every step holding the Linear key runs its
#: scripts from: outside the workspace and outside `--add-dir`, so outside
#: everything the planner's Write reaches.
TRUSTED = "${{ runner.temp }}/pipeline-trusted"
COPY_STEP = "Keep a trusted copy of bureau-pipeline"
DROP_STEP = "Remove the trusted copy"
#: The four files the planner may write (`triage_repair`'s names).
OUTPUT_FILES = ("revised-card.md", "repair-answers.md", "question.md",
                "question-choices.json")
#: Every expression carrying card text or critic findings: each may stand in
#: the agent's prompt only between the two sentinel lines.
UNTRUSTED = ("steps.card.outputs.title", "steps.card.outputs.description",
             "steps.card.outputs.findings", "steps.card.outputs.decisions")
#: The raw matrix fields that carry the same text before it is sanitized.
RAW_FIELDS = ("matrix.title", "matrix.findings", "matrix.decisions", "matrix.why")


def _doc() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _on(doc):
    return check_workflow_watchers.on_block(doc)


def _job(name: str) -> dict:
    return _doc()["jobs"][name]


def _step(job: str, name: str) -> dict:
    found = [s for s in _job(job)["steps"] if s.get("name") == name]
    assert len(found) == 1, f"{name!r} is not exactly one step of the {job} job"
    return found[0]


def _index(job: str, predicate) -> int:
    for i, step in enumerate(_job(job)["steps"]):
        if predicate(step):
            return i
    raise AssertionError(f"no step of {job} matches")


def _runs(step: dict, needle: str) -> bool:
    return needle in str(step.get("run") or "")


def _agent() -> dict:
    return _step("repair", AGENT_STEP)


def _bash(script: str, env: dict, cwd: Path, rc: int = 0):
    """Run one step's `run:` the way the runner does (`bash -e`), with a
    `python3` on the PATH that records its argv, NUL-separated, one file per
    call, and exits `rc`."""
    stub_dir = cwd / ".stub"
    stub_dir.mkdir(exist_ok=True)
    calls = cwd / ".calls"
    calls.mkdir(exist_ok=True)
    for old in calls.iterdir():
        old.unlink()
    stub = stub_dir / "python3"
    stub.write_text("#!/bin/sh\n"
                    f'n=$(ls "{calls}" | wc -l)\n'
                    f'printf "%s\\0" "$@" > "{calls}/$n"\n'
                    f"exit {rc}\n")
    stub.chmod(0o755)
    output = cwd / ".github_output"
    output.write_text("")
    full_env = {"PATH": f"{stub_dir}:{os.environ['PATH']}",
                "GITHUB_OUTPUT": str(output), **env}
    result = subprocess.run(["bash", "-e", "-c", script], cwd=cwd, env=full_env,
                            capture_output=True, text=True)
    outputs = dict(line.split("=", 1)
                   for line in output.read_text().splitlines() if "=" in line)
    argvs = [(calls / str(i)).read_bytes().decode().split("\0")[:-1]
             for i in range(len(list(calls.iterdir())))]
    return result, outputs, argvs


def _evaluate(expr: str, values: dict) -> bool:
    """A GitHub `if:` over the few names this file's gates read: each name in
    `values` is put in as its literal, `cancelled()` is false, and the
    operators are read as Python's. A name left over fails to evaluate, so a
    gate reading something this test does not know is a failure, never a pass."""
    body = expr.strip()
    if body.startswith("${{") and body.endswith("}}"):
        body = body[3:-2]
    for name, value in sorted(values.items(), key=lambda kv: -len(kv[0])):
        body = body.replace(name, repr(value))
    body = body.replace("cancelled()", "False")
    body = body.replace("&&", " and ").replace("||", " or ")
    body = re.sub(r"!(?!=)", " not ", body)
    return bool(eval(body, {"__builtins__": {}}, {}))  # noqa: S307 — test-only, literals


def _fill(template: str, row: dict, temp: Path, model: str = "") -> str:
    """`${{ runner.temp }}`, `${{ matrix.<key> }}` and the model step's
    `${{ steps.model.outputs.model }}` (empty when that step was skipped),
    filled in."""
    value = template.replace("${{ runner.temp }}", str(temp))
    value = value.replace("${{ steps.model.outputs.model }}", model)
    value = re.sub(r"\$\{\{\s*matrix\.(\w+)\s*\}\}", lambda m: str(row[m.group(1)]), value)
    assert "${{" not in value, f"an expression this test cannot fill: {value!r}"
    return value


def _step_env(step: dict, row: dict, temp: Path, model: str = "") -> dict:
    return {k: _fill(str(v), row, temp, model)
            for k, v in (step.get("env") or {}).items()
            if "secrets." not in str(v)}


# --------------------------------------------------------------------------- #
# the clock                                                                    #
# --------------------------------------------------------------------------- #


class TestTheClock:
    def test_it_is_named_triage_repair(self):
        assert _doc()["name"] == "Triage Repair"

    def test_the_cron_is_off_every_other_minute(self):
        assert [e["cron"] for e in _on(_doc())["schedule"]] == [CRON]
        minutes = set(CRON.split()[0].split(","))
        # The sweep's */15, Nightly Watch's :23 and Hygiene's :37.
        assert not minutes & {"0", "15", "30", "45", "23", "37"}

    def test_the_dispatch_input_defaults_to_a_dry_run(self):
        inputs = _on(_doc())["workflow_dispatch"]["inputs"]
        assert set(inputs) == {"dry_run"}
        assert inputs["dry_run"]["type"] == "boolean"
        assert inputs["dry_run"]["default"] is True

    def test_one_run_at_a_time_queued_never_canceled(self):
        doc = _doc()
        assert doc["concurrency"] == {"group": "triage-repair",
                                      "cancel-in-progress": False}

    def test_the_workflow_token_is_read_only(self):
        assert _doc()["permissions"] == {"contents": "read"}

    def test_the_header_answers_the_vendor_premortem(self):
        header = _text().split("\nname:", 1)[0]
        for q in ("Q1", "Q2", "Q3", "Q4", "Q5"):
            assert q in header, f"standards/vendor-boundaries.md {q} is unanswered"
        for fact in ("github-actions", "LINEAR_PLANNER_KEY", "LINEAR_API_KEY",
                     "BUREAU_APP_ID", "BUREAU_APP_PRIVATE_KEY", "CLAUDE_AUTH_MODE",
                     "TRIAGE_REPAIR_LIVE", "exits 3", "started", "DRE-7183",
                     "DRE-2696"):
            assert fact in header, f"the header does not say {fact!r}"

    def test_the_medic_watches_it(self):
        workflows = _on(yaml.safe_load(MEDIC_STUB.read_text()))["workflow_run"]["workflows"]
        assert "Triage Repair" in workflows
        assert "Triage Repair" in MEDIC_STUB.read_text().split("\nname:", 1)[0]


# --------------------------------------------------------------------------- #
# the switch: one dry-run value, computed once                                 #
# --------------------------------------------------------------------------- #


class TestDecideDryRun:
    @pytest.fixture
    def step(self):
        return _step("select", "Decide dry run")

    def _decide(self, step, tmp_path, event, dispatch="", live=None):
        env = {"EVENT": event, "DISPATCH_DRY_RUN": dispatch}
        if live is not None:
            env["TRIAGE_REPAIR_LIVE"] = live
        result, outputs, _ = _bash(step["run"], env, tmp_path)
        assert result.returncode == 0, result.stderr
        assert f"triage-repair: event={event} dry_run={outputs['dry_run']}" in result.stdout
        return outputs["dry_run"]

    def test_it_reads_the_event_the_input_and_the_variable(self, step):
        assert step["id"] == "mode"
        assert step["env"] == {
            "EVENT": "${{ github.event_name }}",
            "DISPATCH_DRY_RUN": "${{ inputs.dry_run }}",
            "TRIAGE_REPAIR_LIVE": "${{ vars.TRIAGE_REPAIR_LIVE }}",
        }
        assert _job("select")["outputs"]["dry_run"] == "${{ steps.mode.outputs.dry_run }}"

    def test_the_schedule_is_live_only_on_exactly_true(self, step, tmp_path):
        assert self._decide(step, tmp_path, "schedule") == "true"
        for near_miss in ("", "TRUE", "True", "false", "1", "true "):
            assert self._decide(step, tmp_path, "schedule", live=near_miss) == "true", near_miss
        assert self._decide(step, tmp_path, "schedule", live="true") == "false"

    def test_the_schedule_ignores_the_dispatch_input(self, step, tmp_path):
        assert self._decide(step, tmp_path, "schedule", dispatch="false") == "true"

    def test_a_dispatch_is_live_only_on_exactly_false(self, step, tmp_path):
        assert self._decide(step, tmp_path, "workflow_dispatch", "true", live="true") == "true"
        assert self._decide(step, tmp_path, "workflow_dispatch", "") == "true"
        assert self._decide(step, tmp_path, "workflow_dispatch", "FALSE") == "true"
        assert self._decide(step, tmp_path, "workflow_dispatch", "false") == "false"

    def test_any_other_event_is_a_dry_run(self, step, tmp_path):
        assert self._decide(step, tmp_path, "push", "false", live="true") == "true"

    def test_the_switch_has_its_row(self):
        rows = {r["name"]: r for r in switch_reason.load()["switches"]}
        assert rows["TRIAGE_REPAIR_LIVE"] == {
            "name": "TRIAGE_REPAIR_LIVE",
            "reader": ".github/workflows/triage-repair.yml",
            "step": "Decide dry run",
            "means": ("The scheduled Triage Repair run claims parked cards and has "
                      "the planner repair them, instead of printing what it would "
                      "repair."),
        }
        assert switch_reason.problems() == []
        docs = (ROOT / "docs" / "switches.md").read_text(encoding="utf-8")
        assert "| `TRIAGE_REPAIR_LIVE` | `triage-repair.yml`, `Decide dry run` |" in docs

    def test_the_check_fails_with_the_row_removed(self):
        doc = switch_reason.load()
        doc["switches"] = [r for r in doc["switches"] if r["name"] != "TRIAGE_REPAIR_LIVE"]
        found = switch_reason.problems(doc)
        assert found and all("TRIAGE_REPAIR_LIVE" in p for p in found), found


# --------------------------------------------------------------------------- #
# select, and the hand-off to the matrix                                       #
# --------------------------------------------------------------------------- #


ROWS = [
    {"card": "DRE-1", "title": "a", "repo": "dreadnought-foundry/agent-bureau",
     "action": "repair", "why": "w", "findings": ["f"], "decisions": [], "max_turns": 120},
    {"card": "DRE-2", "title": "b", "repo": "dreadnought-foundry/agent-bureau",
     "action": "escalate", "why": "w", "findings": ["f"], "decisions": ["f"],
     "max_turns": 120},
    {"card": "DRE-3", "title": "c", "repo": "dreadnought-foundry/portico",
     "action": "replan", "why": "w", "findings": [], "decisions": [], "max_turns": 120},
    {"card": "DRE-4", "title": "d", "repo": "dreadnought-foundry/portico",
     "action": "spent", "why": "w", "findings": [], "decisions": [], "max_turns": 120},
]


class TestSelect:
    def test_select_runs_the_module_with_its_out_file(self, tmp_path):
        steps = [s for s in _job("select")["steps"] if _runs(s, "triage_repair.py select")]
        assert len(steps) == 1
        result, _, argvs = _bash(steps[0]["run"], {}, tmp_path)
        assert result.returncode == 0, result.stderr
        assert argvs == [["scripts/triage_repair.py", "select", "--out", "cards.json"]]
        assert "LINEAR_API_KEY" in steps[0]["env"]

    def test_the_hand_off_carries_only_the_worked_actions(self, tmp_path):
        step = _step("select", "Hand the worked cards to the repair")
        (tmp_path / "cards.json").write_text(json.dumps(ROWS), encoding="utf-8")
        output = tmp_path / "out"
        output.write_text("")
        done = subprocess.run(["bash", "-e", "-c", step["run"]], cwd=tmp_path,
                              env={**os.environ, "GITHUB_OUTPUT": str(output)},
                              capture_output=True, text=True)
        assert done.returncode == 0, done.stderr
        cards = json.loads(output.read_text().split("cards=", 1)[1])
        assert [c["card"] for c in cards] == ["DRE-1", "DRE-2", "DRE-3"]
        assert cards[0] == ROWS[0]
        assert _job("select")["outputs"]["cards"] == "${{ steps.cards.outputs.cards }}"

    def test_the_hand_off_does_not_run_in_a_dry_run(self):
        gate = _step("select", "Hand the worked cards to the repair")["if"]
        assert _evaluate(gate, {"steps.mode.outputs.dry_run": "false"})
        for value in ("true", ""):
            assert not _evaluate(gate, {"steps.mode.outputs.dry_run": value})

    @pytest.mark.parametrize("dry_run,cards,runs", [
        ("false", '[{"card":"DRE-1"}]', True),
        ("true", '[{"card":"DRE-1"}]', False),
        ("", '[{"card":"DRE-1"}]', False),
        ("false", "[]", False),
        ("false", "", False),
    ])
    def test_the_repair_job_runs_only_when_the_decision_said_live(self, dry_run, cards, runs):
        job = _job("repair")
        assert job["needs"] == "select"
        assert _evaluate(job["if"], {"needs.select.outputs.dry_run": dry_run,
                                     "needs.select.outputs.cards": cards}) is runs


# --------------------------------------------------------------------------- #
# the repair job                                                               #
# --------------------------------------------------------------------------- #


class TestTheRepairJob:
    def test_one_card_at_a_time_and_the_timeout(self):
        job = _job("repair")
        assert job["timeout-minutes"] == 100
        assert job["strategy"]["max-parallel"] == 1
        assert job["strategy"]["fail-fast"] is False
        assert job["strategy"]["matrix"] == {
            "include": "${{ fromJSON(needs.select.outputs.cards) }}"}

    def test_the_steps_run_in_the_contracts_order(self):
        order = [
            _index("repair", lambda s: str(s.get("uses", "")).startswith(
                "actions/create-github-app-token") and s.get("id") == "app"),
            _index("repair", lambda s: s.get("name") == "Check out the card's repo"),
            _index("repair", lambda s: s.get("name") == "Check out bureau-pipeline"),
            _index("repair", lambda s: s.get("name") == COPY_STEP),
            _index("repair", lambda s: _runs(s, "triage_repair.py claim")),
            _index("repair", lambda s: _runs(s, "assemble_context.py assemble planner")),
            _index("repair", lambda s: _runs(s, "model_fallback.py select planner")),
            _index("repair", lambda s: s.get("uses") == INSTALL_ACTION),
            _index("repair", lambda s: s.get("name") == AGENT_STEP),
            _index("repair", lambda s: _runs(s, "triage_repair.py apply")),
            _index("repair", lambda s: s.get("name") == DROP_STEP),
        ]
        assert order == sorted(order), order

    def test_the_token_reads_the_card_repo_only(self):
        mint = next(s for s in _job("repair")["steps"] if s.get("id") == "app")
        assert mint["name"] == "Mint bot token (the card repo's installation)"
        assert mint["with"]["app-id"] == "${{ secrets.BUREAU_APP_ID }}"
        assert mint["with"]["private-key"] == "${{ secrets.BUREAU_APP_PRIVATE_KEY }}"
        assert mint["with"]["owner"] == "${{ steps.repo.outputs.owner }}"
        assert mint["with"]["repositories"] == "${{ steps.repo.outputs.name }}"
        assert mint["with"]["permission-contents"] == "read"

    def test_the_repo_is_split_for_the_mint(self, tmp_path):
        step = next(s for s in _job("repair")["steps"] if s.get("id") == "repo")
        result, outputs, _ = _bash(step["run"], {"REPOSITORY": "dreadnought-foundry/portico",
                                                 "CARD": "DRE-1"}, tmp_path)
        assert result.returncode == 0, result.stderr
        assert outputs == {"owner": "dreadnought-foundry", "name": "portico"}
        for bad in ("", "portico"):
            result, _, _ = _bash(step["run"], {"REPOSITORY": bad, "CARD": "DRE-1"}, tmp_path)
            assert result.returncode != 0, bad

    def test_the_planner_layout_card_repo_at_the_root_pipeline_beside_it(self):
        card = _step("repair", "Check out the card's repo")
        assert card["with"]["repository"] == "${{ matrix.repo }}"
        assert card["with"]["token"] == "${{ steps.app.outputs.token }}"
        assert card["with"].get("persist-credentials") is False
        assert "path" not in card["with"]
        pipeline = _step("repair", "Check out bureau-pipeline")
        assert pipeline["with"]["path"] == ".bureau-pipeline"


class TestClaim:
    @pytest.fixture
    def step(self):
        steps = [s for s in _job("repair")["steps"] if _runs(s, "triage_repair.py claim")]
        assert len(steps) == 1
        assert steps[0]["id"] == "claim"
        return steps[0]

    @staticmethod
    def _env(step, tmp_path):
        return _step_env(step, {"card": "DRE-1"}, tmp_path / "temp")

    def test_a_claim_runs_on_the_matrix_card(self, step, tmp_path):
        assert step["env"]["CARD"] == "${{ matrix.card }}"
        result, outputs, argvs = _bash(step["run"], self._env(step, tmp_path), tmp_path)
        assert result.returncode == 0, result.stderr
        trusted = _fill(TRUSTED, {}, tmp_path / "temp")
        assert argvs == [[f"{trusted}/scripts/triage_repair.py", "claim", "DRE-1"]]
        assert outputs["claimed"] == "true"

    def test_exit_3_ends_the_leg_green_with_nothing_claimed(self, step, tmp_path):
        result, outputs, _ = _bash(step["run"], self._env(step, tmp_path), tmp_path,
                                   rc=triage_repair.EXIT_SPENT)
        assert result.returncode == 0, result.stderr
        assert outputs["claimed"] == "false"

    def test_any_other_failure_is_red(self, step, tmp_path):
        result, outputs, _ = _bash(step["run"], self._env(step, tmp_path), tmp_path, rc=1)
        assert result.returncode != 0
        assert outputs.get("claimed") != "true"


class TestTheGates:
    """The agent step and `apply`, evaluated over every claim and action."""

    def _gate(self, name_or_pred, claimed, action):
        steps = _job("repair")["steps"]
        step = next(s for s in steps if (s.get("name") == name_or_pred
                                         if isinstance(name_or_pred, str)
                                         else name_or_pred(s)))
        return _evaluate(step["if"], {"steps.claim.outputs.claimed": claimed,
                                      "matrix.action": action})

    @pytest.mark.parametrize("action", ["repair", "escalate"])
    def test_the_planner_runs_for_a_claimed_repair_or_escalate(self, action):
        assert self._gate(AGENT_STEP, "true", action)

    def test_the_planner_is_skipped_for_replan(self):
        assert not self._gate(AGENT_STEP, "true", "replan")

    @pytest.mark.parametrize("action", ["repair", "escalate", "replan"])
    def test_an_unclaimed_card_runs_neither_the_planner_nor_apply(self, action):
        for claimed in ("false", ""):
            assert not self._gate(AGENT_STEP, claimed, action)
            assert not self._gate(lambda s: _runs(s, "triage_repair.py apply"),
                                  claimed, action)

    @pytest.mark.parametrize("action", ["repair", "escalate", "replan"])
    def test_apply_runs_for_every_claimed_card(self, action):
        assert self._gate(lambda s: _runs(s, "triage_repair.py apply"), "true", action)

    def test_every_planner_step_is_gated_like_the_agent(self):
        for name in ("Planner context", "Read the card for the planner",
                     "Select model", "Install Claude Code (asserted)"):
            assert not self._gate(name, "true", "replan"), name
            assert not self._gate(name, "false", "repair"), name
            assert self._gate(name, "true", "escalate"), name

    def test_a_dead_planner_still_reaches_apply(self):
        apply = next(s for s in _job("repair")["steps"] if _runs(s, "triage_repair.py apply"))
        assert "!cancelled()" in apply["if"]
        assert _agent()["continue-on-error"] is True


# --------------------------------------------------------------------------- #
# the agent step                                                               #
# --------------------------------------------------------------------------- #


class TestTheAgentStep:
    def test_it_is_the_vendor_action_at_plan_ymls_pin(self):
        plan = yaml.safe_load(PLAN.read_text())
        pins = {s["uses"] for job in plan["jobs"].values() for s in job.get("steps") or []
                if str(s.get("uses", "")).startswith(ACTION + "@")}
        assert len(pins) == 1
        assert _agent()["uses"] in pins
        assert "# v" in next(line for line in _text().splitlines()
                             if line.strip().startswith(f"uses: {ACTION}@"))

    def test_it_runs_the_asserted_install(self):
        install = _step("repair", "Install Claude Code (asserted)")
        assert install["uses"] == INSTALL_ACTION
        assert install["id"] == "install_claude"
        assert (_agent()["with"]["path_to_claude_code_executable"]
                == "${{ steps.install_claude.outputs.executable }}")

    def test_its_arguments(self):
        args = _agent()["with"]["claude_args"]
        assert "--max-turns ${{ matrix.max_turns }}" in args
        assert "--model ${{ steps.model.outputs.model }}" in args
        assert '--allowedTools "Read,Glob,Grep,Write"' in args
        assert args.count("--allowedTools") == 1
        assert _agent()["env"]["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] == "1"

    def test_it_holds_no_linear_key(self):
        agent = _agent()
        assert not [k for k in (agent.get("env") or {}) if "LINEAR" in k]
        assert "LINEAR" not in json.dumps(agent)
        # The job's env names the Linear bucket and the token's expiry for the
        # steps that hold a key; it carries no secret the agent would inherit.
        assert "secrets." not in json.dumps(_job("repair").get("env") or {})
        assert "secrets." not in json.dumps(_doc().get("env") or {})

    def test_the_linear_key_rides_only_the_steps_that_read_or_write(self):
        holders = {s.get("name") for s in _job("repair")["steps"]
                   if "LINEAR_API_KEY" in (s.get("env") or {})}
        assert holders == {"Claim the card", "Planner context",
                           "Read the card for the planner",
                           "Apply the planner's files"}
        for job in ("select", "repair"):
            for step in _job(job)["steps"]:
                key = (step.get("env") or {}).get("LINEAR_API_KEY")
                if key is not None:
                    assert key == ("${{ secrets.LINEAR_PLANNER_KEY || "
                                   "secrets.LINEAR_API_KEY }}"), step.get("name")

    def test_the_model_comes_from_the_selector_and_no_id_is_pinned(self):
        select = _step("repair", "Select model")
        assert "model_fallback.py select planner" in select["run"]
        assert not re.search(r"--model\s+claude-", _text())

    def test_the_model_home_token_reaches_the_selector(self):
        mint = next(s for s in _job("repair")["steps"] if s.get("id") == "model_home")
        assert mint["continue-on-error"] is True
        assert mint["with"]["owner"] == "dreadnought-foundry"
        assert mint["with"]["repositories"] == "bureau-pipeline"
        # No permission subset: the pinned action declares no variables
        # input, and one it does not declare is refused (DRE-7230).
        assert not [k for k in mint["with"] if k.startswith("permission-")]
        assert (_step("repair", "Select model")["env"]["BUREAU_MODEL_HOME_TOKEN"]
                == "${{ steps.model_home.outputs.token }}")

    def test_the_scheduled_actor_is_admitted(self):
        bots = _agent()["with"]["allowed_bots"].split(",")
        assert "agent-bureau-qa-bot" in bots and "github-actions" in bots


class TestTheTrustedCopy:
    """Withholding the key from the agent step is not enough on its own: the
    planner's Write reaches the workspace — the card repo and the
    `.bureau-pipeline` checkout — and its `--add-dir`, and a later step of the
    same job that held the key and ran `.bureau-pipeline/scripts/` would run
    whatever the planner wrote there. So every step holding the key runs its
    scripts from a copy taken before any model runs, outside both, and
    read-only."""

    @staticmethod
    def _holders() -> list[dict]:
        return [s for s in _job("repair")["steps"]
                if "LINEAR_API_KEY" in (s.get("env") or {})]

    @staticmethod
    def _env(step: dict, temp: Path) -> dict:
        """The step's env, `runner.temp` and the matrix filled; every other
        expression is a placeholder, since `python3` is a stub here."""
        row = {"card": "DRE-1", "action": "repair", "title": "t"}
        env = {}
        for key, value in (step.get("env") or {}).items():
            value = str(value).replace("${{ runner.temp }}", str(temp))
            value = re.sub(r"\$\{\{\s*matrix\.(\w+)\s*\}\}",
                           lambda m: str(row[m.group(1)]), value)
            env[key] = re.sub(r"\$\{\{.*?\}\}", "[]", value)
        return env

    def test_the_copy_lies_outside_everything_the_planner_can_write(self):
        add_dirs = re.findall(r"--add-dir\s+(\$\{\{ runner\.temp \}\}\S*|\S+)",
                              _agent()["with"]["claude_args"])
        assert add_dirs == ["${{ runner.temp }}/triage-repair"]
        # Not under the workspace, which is the agent's working directory.
        assert TRUSTED.startswith("${{ runner.temp }}/")
        for granted in add_dirs:
            assert not (TRUSTED + "/").startswith(granted + "/"), granted
            assert not (granted + "/").startswith(TRUSTED + "/"), granted

    def test_no_step_holding_the_key_runs_a_script_the_planner_can_write(self, tmp_path):
        holders = self._holders()
        assert {s["name"] for s in holders} == {
            "Claim the card", "Planner context", "Read the card for the planner",
            "Apply the planner's files"}
        temp = tmp_path / "temp"
        trusted = _fill(TRUSTED, {}, temp)
        for step in holders:
            assert step["env"]["TRUSTED_PIPELINE"] == TRUSTED, step["name"]
            assert ".bureau-pipeline/scripts" not in step["run"], step["name"]
            work = tmp_path / re.sub(r"\W", "_", step["name"])
            (work / ".bureau-pipeline").mkdir(parents=True)
            result, _, argvs = _bash(step["run"], self._env(step, temp), work)
            assert result.returncode == 0, (step["name"], result.stderr)
            scripts = [argv[0] for argv in argvs if argv and argv[0] != "-c"]
            assert scripts, step["name"]
            for script in scripts:
                assert script.startswith(f"{trusted}/scripts/"), (step["name"], script)

    def test_the_planner_context_is_read_from_the_copy_and_written_for_the_planner(self):
        run = _step("repair", "Planner context")["run"]
        assert 'assemble planner --root "$TRUSTED_PIPELINE"' in run
        # The planner reads its context where its prompt says: the checkout.
        assert "> .bureau-pipeline/agent-context.md" in run
        assert ".bureau-pipeline/agent-context.md" in _agent()["with"]["prompt"]

    def test_the_copy_is_taken_before_any_model_runs_and_holds_no_key(self):
        copy = _step("repair", COPY_STEP)
        assert "if" not in copy
        assert "LINEAR" not in json.dumps(copy)
        assert copy["env"]["TRUSTED_PIPELINE"] == TRUSTED
        at = _index("repair", lambda s: s.get("name") == COPY_STEP)
        assert at == _index("repair", lambda s: s.get("name") == "Check out bureau-pipeline") + 1
        for later in ("Claim the card", "Select model", AGENT_STEP):
            assert at < _index("repair", lambda s, n=later: s.get("name") == n), later

    def test_the_copy_is_whole_read_only_and_removed_at_the_end(self, tmp_path):
        temp = tmp_path / "temp"
        temp.mkdir()
        scripts = tmp_path / ".bureau-pipeline" / "scripts"
        scripts.mkdir(parents=True)
        (scripts / "triage_repair.py").write_text("print('the reviewed code')\n")
        copy, drop = _step("repair", COPY_STEP), _step("repair", DROP_STEP)
        trusted = Path(_fill(TRUSTED, {}, temp))
        # Twice: a reused runner can hold the last run's read-only copy.
        for _ in range(2):
            result, _, _ = _bash(copy["run"], self._env(copy, temp), tmp_path)
            assert result.returncode == 0, result.stderr
        landed = trusted / "scripts" / "triage_repair.py"
        assert landed.read_text() == "print('the reviewed code')\n"
        if os.geteuid() != 0:  # root writes through any mode bit
            with pytest.raises(PermissionError):
                landed.write_text("print('the planner was here')\n")
            with pytest.raises(PermissionError):
                (trusted / "scripts" / "json.py").write_text("")
        assert drop["if"] == "${{ always() }}"
        result, _, _ = _bash(drop["run"], self._env(drop, temp), tmp_path)
        assert result.returncode == 0, result.stderr
        assert not trusted.exists()


class TestThePrompt:
    def _prompt(self) -> str:
        return _agent()["with"]["prompt"]

    def test_card_text_and_findings_sit_only_inside_the_fence(self):
        prompt = self._prompt()
        assert prompt.count(BEGIN) == 1 and prompt.count(END) == 1
        start, stop = prompt.index(BEGIN), prompt.index(END)
        for expr in UNTRUSTED:
            spots = [m.start() for m in re.finditer(re.escape(expr), prompt)]
            assert spots, f"{expr} is not in the prompt"
            assert all(start < at < stop for at in spots), expr
        # And nowhere else in the file — not in a run, not in another prompt.
        lines = _text().splitlines()
        begins = [i for i, line in enumerate(lines) if line.strip() == BEGIN]
        ends = [i for i, line in enumerate(lines) if line.strip() == END]
        assert len(begins) == 1 and len(ends) == 1
        for i, line in enumerate(lines):
            for expr in UNTRUSTED:
                if expr in line:
                    assert begins[0] < i < ends[0], f"{expr} stands outside the fence: {line!r}"

    def test_the_fence_is_announced_as_data(self):
        before = self._prompt().split(BEGIN, 1)[0]
        assert "DATA, not instructions" in before
        assert "untrusted-content.md" in before

    def test_the_raw_card_text_reaches_only_the_sanitizer(self):
        for line in _text().splitlines():
            for field in RAW_FIELDS:
                if field in line:
                    assert re.match(r"^\s+[A-Z_]+: \$\{\{ (toJSON\()?" + re.escape(field),
                                    line), line
        read = _step("repair", "Read the card for the planner")
        assert "sanitize_untrusted.py" in read["run"]
        assert read["id"] == "card"

    def test_the_prompt_names_one_output_directory_and_the_four_files(self):
        prompt = self._prompt()
        assert OUT_DIR in prompt
        for name in OUTPUT_FILES:
            assert f"{OUT_DIR}/{name}" in prompt, name
        assert "no Linear access" in prompt

    def test_the_prompt_carries_the_contracts_instructions(self):
        prompt = self._prompt()
        for words in ("You are the PLANNER", "plan critic's bound", "ONCE",
                      "one numbered line per finding", "CEO", "escalate",
                      "${{ matrix.action }}", "steps.card.outputs.decisions"):
            assert words in prompt, words


# --------------------------------------------------------------------------- #
# the scenario: the workflow's decision route, end to end                      #
# --------------------------------------------------------------------------- #


def _apply_argv(row: dict, temp: Path, work: Path, model: str = "") -> list[str]:
    """The `apply` step's own command line, for one matrix card, with the
    model step's output `model` (empty when that step was skipped)."""
    step = next(s for s in _job("repair")["steps"] if _runs(s, "triage_repair.py apply"))
    result, _, argvs = _bash(step["run"], _step_env(step, row, temp, model), work)
    assert result.returncode == 0, result.stderr
    assert len(argvs) == 1
    assert argvs[0][0] == f"{_fill(TRUSTED, row, temp)}/scripts/triage_repair.py"
    return argvs[0][1:]


def _planner_dir(row: dict, temp: Path) -> Path:
    """The directory the agent step's prompt tells the planner to write to."""
    assert OUT_DIR in _agent()["with"]["prompt"]
    return Path(_fill(OUT_DIR, row, temp))


def _row(card: str, action: str) -> dict:
    return {"card": card, "title": "t", "repo": "dreadnought-foundry/agent-bureau",
            "action": action, "why": "w", "findings": [], "decisions": [],
            "max_turns": triage_repair.REPAIR_MAX_TURNS}


class TestTheDecisionRoute:
    def _end_state(self, board, card):
        assert board.lane(card) == "Green Light"
        assert "needs-human" not in board.labels(card)
        assert board.description(card) == base.DESCRIPTION
        assert board.newest(card).startswith("🩹 triage-repair: asked")

    def test_an_escalate_card_ends_in_green_light(self, tmp_path):
        board = TriageRepairBoard()
        base._parked(board, "DRE-2", base.DECISION_NOTE)
        _, rows = base._select(board, tmp_path)
        assert [r["action"] for r in rows] == ["escalate"]
        assert base._claim(board, "DRE-2") == 0
        row, temp = rows[0], tmp_path / "temp"
        out = _planner_dir(row, temp)
        base._write(out, question=base.QUESTION, choices=base.CHOICES)
        argv = _apply_argv(row, temp, tmp_path)
        assert argv == ["apply", "DRE-2", "--action", "escalate", "--dir", str(out)]
        with board.patched():
            assert triage_repair.main(argv) == 0
        self._end_state(board, "DRE-2")

    def test_a_repair_card_whose_planner_wrote_only_a_question_ends_the_same(self, tmp_path):
        board = TriageRepairBoard()
        base._parked(board, "DRE-7066", base.NOTE_7066)
        _, rows = base._select(board, tmp_path)
        assert [r["action"] for r in rows] == ["repair"]
        assert base._claim(board, "DRE-7066") == 0
        row, temp = rows[0], tmp_path / "temp"
        out = _planner_dir(row, temp)
        base._write(out, question=base.QUESTION)
        argv = _apply_argv(row, temp, tmp_path)
        with board.patched():
            assert triage_repair.main(argv) == 0
        self._end_state(board, "DRE-7066")

    def test_a_replan_card_is_applied_with_no_directory(self, tmp_path):
        argv = _apply_argv(_row("DRE-3", "replan"), tmp_path / "temp", tmp_path)
        assert argv == ["apply", "DRE-3", "--action", "replan"]
