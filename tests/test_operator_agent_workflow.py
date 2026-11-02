"""The Operator Agent run (DRE-8747, epic DRE-8738): `.github/workflows/operator-agent.yml`.

`scripts/operator_agent.py` (DRE-8745) selects the cards waiting on a person
and turns the model's answers into decisions; `scripts/operator_agent_apply.py`
(DRE-8746) claims and applies them. This file holds the workflow that wires
them, which decides nothing itself. It is `triage-repair.yml`'s wiring, and
these tests are `tests/test_triage_repair_workflow.py`'s and
`tests/test_alarm_repair_workflow.py`'s. Every test reads the LIVE workflow
file. The pieces of shell that decide anything — whether the run is a dry
run, which cards the model is given, which decisions reach the `apply` job,
what a `claim` exit of 3 does — are EXECUTED under bash, and the `if:` of the
gates are EVALUATED, so the answer is what the runner would do and not what
the YAML looks like it says.

The `Decide` step is run with the real `operator_agent.py decide` over a
`cards.json`, card files and answer files written here, so the lines it
prints and the rows it hands on are the module's own, not a copy of them.
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

import check_workflow_watchers  # noqa: E402
import operator_decide  # noqa: E402
import switch_reason  # noqa: E402

WORKFLOWS = ROOT / ".github" / "workflows"
WORKFLOW = WORKFLOWS / "operator-agent.yml"
TRIAGE = WORKFLOWS / "triage-repair.yml"
MEDIC_STUB = WORKFLOWS / "self-medic.yml"

SWITCH = "OPERATOR_AGENT_LIVE"
ACTION = "anthropics/claude-code-action"
MODEL_STEP = "Read the cards"
DECIDE_STEP = "Decide"
SELECT_STEP = "Select cards"
COPY_STEP = "Keep the trusted copy"
WRITE_STEP = "Write the decision"
#: The `decide` job's steps, by name and in this order (the card's list).
DECIDE_STEPS = (
    "Check out bureau-pipeline",
    "Decide dry run",
    COPY_STEP,
    SELECT_STEP,
    "Mint the model-home read token",
    "Select the model",
    "Install Claude Code (asserted)",
    MODEL_STEP,
    DECIDE_STEP,
)
BEGIN = "===== BEGIN UNTRUSTED CARD TEXT ====="
END = "===== END UNTRUSTED CARD TEXT ====="
TRUSTED = "${{ runner.temp }}/pipeline-trusted"
#: The one directory the model reads cards from and writes answers to.
AGENT_DIR = "${{ runner.temp }}/operator-agent"
LINEAR_KEY = "${{ secrets.LINEAR_PLANNER_KEY || secrets.LINEAR_API_KEY }}"
#: The minutes other workflows' schedules already hold, each by name.
TAKEN = {"0", "15", "30", "45",   # the sweep's */15
         "4", "19", "34", "49",   # Triage Repair
         "23",                    # Nightly Watch
         "37"}                    # Hygiene


def _doc() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _header() -> str:
    return _text().split("\nname:", 1)[0]


def _on(doc):
    return check_workflow_watchers.on_block(doc)


def _job(name: str) -> dict:
    return _doc()["jobs"][name]


def _step(job: str, name: str) -> dict:
    found = [s for s in _job(job)["steps"] if s.get("name") == name]
    assert len(found) == 1, f"{name!r} is not exactly one step of the {job} job"
    return found[0]


def _index(job: str, name: str) -> int:
    return [s.get("name") for s in _job(job)["steps"]].index(name)


def _bash(script: str, env: dict, cwd: Path, rc: int = 0, stub: bool = True):
    """Run one step's `run:` the way the runner does (`bash -e`). With `stub`,
    the `python3` on the PATH records its argv, NUL-separated, one file per
    call, and exits `rc`; without it the real `python3` runs."""
    calls = cwd / ".calls"
    calls.mkdir(exist_ok=True)
    for old in calls.iterdir():
        old.unlink()
    path = os.environ["PATH"]
    if stub:
        stub_dir = cwd / ".stub"
        stub_dir.mkdir(exist_ok=True)
        fake = stub_dir / "python3"
        fake.write_text("#!/bin/sh\n"
                        f'n=$(ls "{calls}" | wc -l)\n'
                        f'printf "%s\\0" "$@" > "{calls}/$n"\n'
                        f"exit {rc}\n")
        fake.chmod(0o755)
        path = f"{stub_dir}:{path}"
    output = cwd / ".github_output"
    output.write_text("")
    full_env = {"PATH": path, "GITHUB_OUTPUT": str(output), "HOME": str(cwd), **env}
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
    body = str(expr).strip()
    if body.startswith("${{") and body.endswith("}}"):
        body = body[3:-2]
    for name, value in sorted(values.items(), key=lambda kv: -len(kv[0])):
        body = body.replace(name, repr(value))
    body = body.replace("cancelled()", "False")
    body = body.replace("&&", " and ").replace("||", " or ")
    body = re.sub(r"!(?!=)", " not ", body)
    return bool(eval(body, {"__builtins__": {}}, {}))  # noqa: S307 — test-only, literals


def _fill(template: str, temp: Path) -> str:
    value = str(template).replace("${{ runner.temp }}", str(temp))
    assert "${{" not in value, f"an expression this test cannot fill: {value!r}"
    return value


def _minutes(field: str) -> set[str]:
    """Every minute a cron's minute field fires on, as text."""
    found: set[int] = set()
    for part in field.split(","):
        step = 1
        if "/" in part:
            part, every = part.split("/")
            step = int(every)
        if part == "*":
            lo, hi = 0, 59
        elif "-" in part:
            lo, hi = (int(x) for x in part.split("-"))
        else:
            lo = int(part)
            hi = lo if step == 1 else 59
        found.update(range(lo, hi + 1, step))
    return {str(m) for m in found}


def _decision(card: str, outcome: str, action: str, held: str = "", **extra) -> dict:
    """One `decisions.json` entry: `Decision.to_json()` with the row's action."""
    fields = dict(card=card, repo="bureau-pipeline", lane="Hand-work", outcome=outcome,
                  reason="the model read the card", step=f"the step {card} names",
                  act="", deploy=False, evidence=[], host_cannot=[], finding="",
                  question="", recommendation="", choices=None, held=held,
                  fingerprint="0123456789ab")
    fields.update(extra)
    return {**operator_decide.Decision(**fields).to_json(), "action": action}


def _output_block() -> str:
    """The `Decide` step's run with the module's call taken out: the block
    that reads `decisions.json` and writes the `cards` output."""
    run = _step("decide", DECIDE_STEP)["run"]
    lines = run.splitlines()
    start = next(i for i, line in enumerate(lines) if "operator_agent.py" in line)
    end = start
    while lines[end].rstrip().endswith("\\"):
        end += 1
    return "\n".join(lines[:start] + lines[end + 1:])


def _cards_output(outputs: dict) -> list:
    return json.loads(outputs["cards"])


# --------------------------------------------------------------------------- #
# the clock and the fleet                                                      #
# --------------------------------------------------------------------------- #


class TestTheClock:
    def test_it_is_named_operator_agent(self):
        assert _doc()["name"] == "Operator Agent"

    def test_the_header_says_what_it_is_modeled_on(self):
        header = _header()
        assert "medic.yml" in header
        assert "Triage Repair" in header and "DRE-8647" in header
        assert "triage-repair.yml" in header

    def test_it_runs_once_an_hour_on_one_fixed_minute(self):
        crons = [e["cron"] for e in _on(_doc())["schedule"]]
        assert len(crons) == 1
        minute, *rest = crons[0].split()
        assert rest == ["*", "*", "*", "*"]
        assert re.fullmatch(r"\d{1,2}", minute) and int(minute) < 60

    def test_its_minute_is_clear_of_every_other_schedule(self):
        minute = _on(_doc())["schedule"][0]["cron"].split()[0]
        assert minute not in TAKEN
        for path in sorted(WORKFLOWS.glob("*.yml")):
            if path == WORKFLOW:
                continue
            doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            for entry in (_on(doc) or {}).get("schedule") or []:
                assert minute not in _minutes(entry["cron"].split()[0]), (path.name, entry)

    def test_the_dispatch_input_defaults_to_a_dry_run(self):
        on = _on(_doc())
        assert set(on) == {"schedule", "workflow_dispatch"}
        inputs = on["workflow_dispatch"]["inputs"]
        assert set(inputs) == {"dry_run"}
        assert inputs["dry_run"]["type"] == "boolean"
        assert inputs["dry_run"]["default"] is True

    def test_one_run_at_a_time_queued_never_canceled(self):
        assert _doc()["concurrency"] == {"group": "operator-agent",
                                         "cancel-in-progress": False}

    def test_the_workflow_token_is_read_only(self):
        doc = _doc()
        assert doc["permissions"] == {"contents": "read"}
        for name, job in doc["jobs"].items():
            assert "permissions" not in job, name

    def test_the_header_answers_the_vendor_premortem(self):
        header = _header()
        for q in ("Q1", "Q2", "Q3", "Q4", "Q5"):
            assert q in header, f"standards/vendor-boundaries.md {q} is unanswered"
        for fact in ("github-actions", "LINEAR_PLANNER_KEY", "LINEAR_API_KEY",
                     "CLAUDE_AUTH_MODE", SWITCH, "exits 3", "apply-only", "DRE-2696",
                     "one board read", "whole-thread read", "20 cards"):
            assert fact in header, f"the header does not say {fact!r}"

    def test_the_medic_watches_it(self):
        workflows = _on(yaml.safe_load(MEDIC_STUB.read_text()))["workflow_run"]["workflows"]
        assert "Operator Agent" in workflows
        assert "Operator Agent" in MEDIC_STUB.read_text().split("\nname:", 1)[0]

    def test_it_reads_the_whole_fleet_from_the_repo_map_and_copies_none_of_it(self):
        """One board read here, over every key of config/repo-map.json, read
        by `select` from the file: no product repo calls it, no job fans out
        over repos, and no fleet repo but this one is named in the file."""
        doc = _doc()
        assert "workflow_call" not in _on(doc)
        assert "strategy" not in doc["jobs"]["decide"]
        keys = json.loads((ROOT / "config" / "repo-map.json").read_text())
        keys = keys.get("repos", keys) if isinstance(keys, dict) else keys
        assert len(keys) >= 10
        text = _text()
        for key in keys:
            if key == "bureau-pipeline":
                continue
            assert not re.search(rf"(?<![\w-]){re.escape(key)}(?![\w-])", text), key
        assert "operator_agent.py select" in _step("decide", SELECT_STEP)["run"]


# --------------------------------------------------------------------------- #
# the switch: one dry-run value, computed once                                 #
# --------------------------------------------------------------------------- #


class TestDecideDryRun:
    @pytest.fixture
    def step(self):
        return _step("decide", "Decide dry run")

    def _decide(self, step, tmp_path, event, dispatch="", live=None):
        env = {"EVENT": event, "DISPATCH_DRY_RUN": dispatch}
        if live is not None:
            env[SWITCH] = live
        result, outputs, _ = _bash(step["run"], env, tmp_path)
        assert result.returncode == 0, result.stderr
        assert f"operator-agent: event={event} dry_run={outputs['dry_run']}" in result.stdout
        return outputs["dry_run"]

    def test_it_reads_the_event_the_input_and_the_variable(self, step):
        assert step["id"] == "mode"
        assert step["env"] == {
            "EVENT": "${{ github.event_name }}",
            "DISPATCH_DRY_RUN": "${{ inputs.dry_run }}",
            SWITCH: "${{ vars.OPERATOR_AGENT_LIVE }}",
        }
        assert _job("decide")["outputs"]["dry_run"] == "${{ steps.mode.outputs.dry_run }}"

    def test_it_is_decided_before_anything_is_selected(self):
        assert _index("decide", "Decide dry run") < _index("decide", SELECT_STEP)

    def test_the_schedule_is_live_only_on_exactly_true(self, step, tmp_path):
        assert self._decide(step, tmp_path, "schedule", live="true") == "false"
        for near_miss in ("TRUE", "true ", "True", "false", "", "1"):
            assert self._decide(step, tmp_path, "schedule", live=near_miss) == "true", near_miss

    def test_an_unset_or_unreadable_switch_is_a_dry_run(self, step, tmp_path):
        assert self._decide(step, tmp_path, "schedule") == "true"

    def test_a_dispatch_is_live_only_on_exactly_false(self, step, tmp_path):
        assert self._decide(step, tmp_path, "workflow_dispatch", "false") == "false"
        assert self._decide(step, tmp_path, "workflow_dispatch", "true", live="true") == "true"
        assert self._decide(step, tmp_path, "workflow_dispatch", "") == "true"
        assert self._decide(step, tmp_path, "workflow_dispatch", "FALSE") == "true"

    def test_the_schedule_ignores_the_dispatch_input(self, step, tmp_path):
        assert self._decide(step, tmp_path, "schedule", dispatch="false") == "true"

    def test_any_other_event_is_a_dry_run(self, step, tmp_path):
        assert self._decide(step, tmp_path, "push", "false", live="true") == "true"

    def test_the_switch_has_its_row_and_its_doc_line(self):
        rows = {r["name"]: r for r in switch_reason.load()["switches"]}
        assert rows[SWITCH] == {
            "name": SWITCH,
            "reader": ".github/workflows/operator-agent.yml",
            "step": "Decide dry run",
            "means": ("The scheduled Operator Agent run posts a receipt on each card it "
                      "routes studio, ceo or done-already and makes that outcome's write, "
                      "instead of printing what it would decide."),
        }
        docs = (ROOT / "docs" / "switches.md").read_text(encoding="utf-8")
        line = next(l for l in docs.splitlines()
                    if l.startswith(f"| `{SWITCH}` | `operator-agent.yml`, `Decide dry run` |"))
        assert ("The scheduled Operator Agent run posts a receipt on each card it routes "
                "studio, ceo or done-already and makes that outcome's write.") in line
        assert re.search(r"`OPERATOR_AGENT_LIVE_OFF_UNTIL`[^\n]*DRE-8747[^\n]*DRE-8830"
                         r"[^\n]*DRE-8748", docs)

    def test_the_check_passes_with_the_row(self):
        done = subprocess.run([sys.executable, str(ROOT / "scripts" / "switch_reason.py"),
                               "check"], cwd=ROOT, capture_output=True, text=True)
        assert done.returncode == 0, done.stdout + done.stderr

    def test_the_check_fails_with_the_row_removed(self):
        doc = switch_reason.load()
        doc["switches"] = [r for r in doc["switches"] if r["name"] != SWITCH]
        found = switch_reason.problems(doc)
        assert found and all(SWITCH in f for f in found), found
        assert any("operator-agent.yml" in f and "Decide dry run" in f for f in found)


# --------------------------------------------------------------------------- #
# the decide job: dry or live, it writes nothing                               #
# --------------------------------------------------------------------------- #


_WRITE_CALLS = (
    re.compile(r"operator_agent_apply"),
    re.compile(r"\b(claim|apply)\b\s+\"?\$"),
    re.compile(r"linear_ops\.py\"?\s+(comment|state|actor|label|subissue|create|move)\b"),
    re.compile(r"\bcmd_(comment|state)\b"),
    re.compile(r"\bgh\s+(pr|issue|api|workflow|release|label|variable|secret|run)\b"),
    re.compile(r"\bgit\s+(add|commit|push|tag)\b"),
)


class TestTheDecideJob:
    def test_its_steps_are_the_cards_nine_in_order(self):
        names = [s.get("name") for s in _job("decide")["steps"]]
        assert names[:len(DECIDE_STEPS)] == list(DECIDE_STEPS), names
        # Anything after them only tidies up: it holds no key and runs no script.
        for step in _job("decide")["steps"][len(DECIDE_STEPS):]:
            assert "LINEAR_API_KEY" not in (step.get("env") or {})
            assert "python3" not in str(step.get("run") or "")

    def test_one_action_each(self):
        for step in _job("decide")["steps"]:
            assert ("uses" in step) != ("run" in step), step.get("name")

    def test_no_step_holds_a_write_to_linear_or_github(self):
        for step in _job("decide")["steps"]:
            body = json.dumps(step)
            for pattern in _WRITE_CALLS:
                assert not pattern.search(body), (step.get("name"), pattern.pattern)
            env = step.get("env") or {}
            assert "GH_TOKEN" not in env and "GITHUB_TOKEN" not in env, step.get("name")
            for key, value in (step.get("with") or {}).items():
                if key.startswith("permission-"):
                    assert value == "read", (step.get("name"), key, value)

    def test_every_token_it_holds_reads_only(self):
        """The workflow token is `contents: read`; the one App mint is the
        model-home read token, for bureau-pipeline alone, and reaches only the
        model selector."""
        mints = [s for s in _job("decide")["steps"]
                 if "create-github-app-token" in str(s.get("uses") or "")]
        assert [m["name"] for m in mints] == ["Mint the model-home read token"]
        mint = mints[0]
        assert mint["id"] == "model_home"
        assert mint["continue-on-error"] is True
        assert mint["with"]["owner"] == "dreadnought-foundry"
        assert mint["with"]["repositories"] == "bureau-pipeline"
        readers = [s["name"] for s in _job("decide")["steps"]
                   if "steps.model_home.outputs.token" in json.dumps(s)]
        assert readers == ["Select the model"]
        assert _step("decide", MODEL_STEP)["with"]["github_token"] == "${{ github.token }}"

    def test_it_declares_the_two_outputs(self):
        assert _job("decide")["outputs"] == {
            "dry_run": "${{ steps.mode.outputs.dry_run }}",
            "cards": "${{ steps.decide.outputs.cards }}",
        }

    def test_it_spends_the_planners_linear_bucket(self):
        env = _job("decide")["env"]
        assert env["LINEAR_IDENTITY"] == "fleet"
        assert env["LINEAR_KEY_HOME"] == "planner-oauth"
        assert env["LINEAR_PLANNER_KEY_EXPIRES_AT"] == "${{ vars.LINEAR_PLANNER_KEY_EXPIRES_AT }}"

    def test_only_select_holds_the_linear_key(self):
        holders = [s["name"] for s in _job("decide")["steps"]
                   if "LINEAR_API_KEY" in (s.get("env") or {})]
        assert holders == [SELECT_STEP]
        env = _step("decide", SELECT_STEP)["env"]
        assert env["LINEAR_API_KEY"] == LINEAR_KEY
        assert env["LINEAR_API_KEY_FALLBACK"] == "${{ secrets.LINEAR_API_KEY }}"


class TestSelectCards:
    @pytest.fixture
    def step(self):
        return _step("decide", SELECT_STEP)

    def _env(self, step, tmp_path):
        return {k: _fill(v, tmp_path) for k, v in step["env"].items()
                if "secrets." not in str(v)}

    def test_it_runs_select_with_the_card_files_outside_the_checkout(self, step, tmp_path):
        result, _outputs, argvs = _bash(step["run"], self._env(step, tmp_path), tmp_path)
        selects = [a for a in argvs if any(x.endswith("operator_agent.py") for x in a)]
        assert len(selects) == 1, argvs
        argv = selects[0]
        assert argv[1] == "select"
        assert argv[argv.index("--cards-dir") + 1] == f"{tmp_path}/operator-agent/cards"
        out = argv[argv.index("--out") + 1]
        assert out.endswith("/cards.json")
        # Neither in the workspace nor in the directory the model may write.
        assert out.startswith(str(tmp_path) + "/")
        assert not out.startswith(f"{tmp_path}/operator-agent/")

    def _select(self, step, tmp_path, rows):
        """The step with a `select` that writes `rows` and their card files."""
        fake = tmp_path / "fake_select.py"
        fake.write_text(
            "import json, os, sys\n"
            "argv = sys.argv[1:]\n"
            "out = argv[argv.index('--out') + 1]\n"
            "cards = argv[argv.index('--cards-dir') + 1]\n"
            f"rows = json.loads({json.dumps(json.dumps(rows))})\n"
            "for row in rows:\n"
            "    open(os.path.join(cards, row['card'] + '.md'), 'w').write('card file')\n"
            "json.dump(rows, open(out, 'w'))\n")
        run = step["run"].replace("python3 .bureau-pipeline/scripts/operator_agent.py",
                                  f"python3 {fake}")
        assert run != step["run"]
        return _bash(run, self._env(step, tmp_path), tmp_path, stub=False)

    def test_it_names_the_cards_for_the_model_and_seals_the_card_files(self, step, tmp_path):
        rows = [{"card": "DRE-1", "for_model": True, "action": "decide"},
                {"card": "DRE-2", "for_model": False, "action": "unread"},
                {"card": "DRE-3", "for_model": True, "action": "decide"},
                {"card": "DRE-4", "for_model": False, "action": "apply-only"}]
        result, outputs, _ = self._select(step, tmp_path, rows)
        assert result.returncode == 0, result.stderr
        assert outputs["count"] == "2"
        assert outputs["for_model"] == "DRE-1 DRE-3"
        cards = tmp_path / "operator-agent" / "cards"
        assert (tmp_path / "operator-agent" / "answers").is_dir()
        # The files `decide` checks every quote against cannot be rewritten.
        assert not os.access(cards, os.W_OK)
        assert not os.access(cards / "DRE-1.md", os.W_OK)
        assert os.access(tmp_path / "operator-agent" / "answers", os.W_OK)

    def test_a_name_that_is_not_a_card_id_never_reaches_the_prompt(self, step, tmp_path):
        rows = [{"card": "DRE-1", "for_model": True, "action": "decide"},
                {"card": "DRE-2; touch pwned", "for_model": True, "action": "decide"}]
        result, outputs, _ = self._select(step, tmp_path, rows)
        assert outputs["for_model"] == "DRE-1"
        assert outputs["count"] == "1"

    def test_a_reused_runners_sealed_files_are_cleared_first(self, step, tmp_path):
        rows = [{"card": "DRE-1", "for_model": True, "action": "decide"}]
        assert self._select(step, tmp_path, rows)[0].returncode == 0
        stale = tmp_path / "operator-agent" / "answers" / "DRE-9.json"
        stale.write_text("{}")
        result, outputs, _ = self._select(step, tmp_path, rows)
        assert result.returncode == 0, result.stderr
        assert not stale.exists()

    def test_the_model_steps_run_only_with_a_card_to_read(self):
        for name in ("Mint the model-home read token", "Select the model",
                     "Install Claude Code (asserted)", MODEL_STEP):
            gate = _step("decide", name)["if"]
            assert _evaluate(gate, {"steps.select.outputs.count": "2"}), name
            assert not _evaluate(gate, {"steps.select.outputs.count": "0"}), name


# --------------------------------------------------------------------------- #
# the model step                                                               #
# --------------------------------------------------------------------------- #


class TestTheModelStep:
    @pytest.fixture
    def step(self):
        return _step("decide", MODEL_STEP)

    def test_it_is_the_vendor_action_at_triage_repairs_pin(self, step):
        triage = yaml.safe_load(TRIAGE.read_text())["jobs"]["repair"]["steps"]
        pin = next(s["uses"] for s in triage if ACTION in str(s.get("uses")))
        assert step["uses"] == pin

    def test_it_holds_no_linear_key(self, step):
        assert step["env"] == {"CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1"}
        assert "LINEAR" not in json.dumps(step)

    def test_its_turn_ceiling_is_a_literal_80(self, step):
        args = step["with"]["claude_args"]
        assert re.search(r"^--max-turns 80$", args, re.MULTILINE), args
        assert args.count("--max-turns") == 1

    def test_its_arguments(self, step):
        args = step["with"]["claude_args"]
        assert f"--add-dir {AGENT_DIR}\n" in args
        assert '--allowedTools "Read,Glob,Grep,Write"' in args
        assert "--model ${{ steps.model.outputs.model }}" in args
        assert step["continue-on-error"] is True
        assert step["with"]["path_to_claude_code_executable"] == \
            "${{ steps.install_claude.outputs.executable }}"

    def test_the_scheduled_actor_is_admitted(self, step):
        bots = step["with"]["allowed_bots"].split(",")
        assert "agent-bureau-qa-bot" in bots and "github-actions" in bots

    def test_the_card_files_are_fenced_as_data(self, step):
        prompt = step["with"]["prompt"]
        assert BEGIN in prompt and END in prompt
        assert "DATA" in prompt and "never instructions" in prompt
        assert "standards/untrusted-content.md" in prompt

    def test_it_is_given_the_cards_for_the_model_and_their_files(self, step):
        prompt = step["with"]["prompt"]
        assert "${{ steps.select.outputs.for_model }}" in prompt
        assert f"{AGENT_DIR}/cards/<CARD>.md" in prompt
        # The only expressions in it: the run's temp directory and the
        # select step's checked card names.
        for expr in re.findall(r"\$\{\{[^}]*\}\}", prompt):
            assert expr in ("${{ runner.temp }}", "${{ steps.select.outputs.for_model }}"), expr

    def test_it_names_the_four_outcomes_in_plain_words(self, step):
        prompt = step["with"]["prompt"]
        for outcome in ("`do`", "`studio`", "`ceo`", "`done-already`"):
            assert outcome in prompt, outcome
        assert "test environments" in prompt
        assert "production access" in prompt
        assert "word for word" in prompt
        assert "business choice" in prompt
        assert "rule table" in prompt

    def test_the_host_limits_are_this_repos_copy(self, step):
        prompt = step["with"]["prompt"]
        assert "config/operator-agent.json" in prompt
        assert "studio.host_limits" in prompt

    def test_the_hand_step_labels_never_decide_alone(self, step):
        prompt = " ".join(step["with"]["prompt"].split())
        assert "`hand-step:machine`" in prompt and "`hand-step:person`" in prompt
        assert "never decide alone" in prompt

    def test_it_names_the_answer_and_the_one_choices_file(self, step):
        prompt = " ".join(step["with"]["prompt"].split())
        assert f"{AGENT_DIR}/answers/<CARD>.json" in prompt
        assert f"{AGENT_DIR}/answers/<CARD>.choices.json" in prompt
        for key in operator_decide.ANSWER_KEYS:
            assert f"`{key}`" in prompt, key
        for key in ("question", "context", "choices", "recommended", "why",
                    "id", "label", "effect", "outcome", "preview"):
            assert f"`{key}`" in prompt, key
        assert "never in the answer file" in prompt
        assert "No choices file is written for any other outcome" in prompt

    def test_a_missing_answer_is_unread_never_do(self, step):
        prompt = " ".join(step["with"]["prompt"].split())
        assert "every card" in prompt.lower()
        assert "`unread`" in prompt and "never `do`" in prompt

    def test_it_names_no_person_email_or_sign_in(self, step):
        prompt = step["with"]["prompt"]
        assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", prompt)
        for word in ("Sid", "sign in", "sign-in", "signin", "log in", "login", "password"):
            assert not re.search(rf"\b{re.escape(word)}\b", prompt, re.IGNORECASE), word


# --------------------------------------------------------------------------- #
# trust                                                                        #
# --------------------------------------------------------------------------- #


class TestTrust:
    def test_the_copy_is_taken_before_any_model_runs_and_holds_no_key(self):
        step = _step("decide", COPY_STEP)
        assert _index("decide", COPY_STEP) < _index("decide", MODEL_STEP)
        assert step["env"] == {"TRUSTED_PIPELINE": TRUSTED}
        assert "chmod -R a-w" in step["run"]

    def test_the_copy_lies_outside_everything_the_model_can_write(self):
        assert not TRUSTED.startswith(AGENT_DIR)
        assert "${{ github.workspace }}" not in TRUSTED

    def test_decide_runs_the_trusted_copy_with_no_linear_key(self):
        step = _step("decide", DECIDE_STEP)
        assert step["env"]["TRUSTED_PIPELINE"] == TRUSTED
        assert '"$TRUSTED_PIPELINE"/scripts/operator_agent.py decide' in step["run"]
        assert "LINEAR" not in json.dumps(step)
        assert ".bureau-pipeline/scripts" not in step["run"]

    def test_a_key_holding_step_after_the_model_runs_the_trusted_copy_or_a_fresh_checkout(self):
        doc = _doc()
        steps = doc["jobs"]["decide"]["steps"]
        after = steps[_index("decide", MODEL_STEP) + 1:]
        for step in after:
            if "LINEAR_API_KEY" in (step.get("env") or {}):
                assert '"$TRUSTED_PIPELINE"/scripts/' in step["run"], step["name"]
        apply_steps = doc["jobs"]["apply"]["steps"]
        assert "actions/checkout@" in apply_steps[0]["uses"]
        assert "path" not in (apply_steps[0].get("with") or {})
        for step in apply_steps:
            if "LINEAR_API_KEY" in (step.get("env") or {}):
                assert "python3 scripts/operator_agent_apply.py" in step["run"], step["name"]

    def test_no_step_names_agent_bureau_or_fetches_the_host_document(self):
        text = _text()
        assert not re.search(r"dreadnought-foundry/agent-bureau(?![\w-])", text)
        assert "StudioDeskStack" not in text
        for job in _doc()["jobs"].values():
            for step in job["steps"]:
                if "create-github-app-token" in str(step.get("uses") or ""):
                    assert step["with"]["repositories"] == "bureau-pipeline"
                    assert step["with"]["owner"] == "dreadnought-foundry"

    def test_no_step_uploads_or_commits_the_card_or_answer_files(self):
        for job in _doc()["jobs"].values():
            for step in job["steps"]:
                assert "upload-artifact" not in str(step.get("uses") or ""), step.get("name")
                assert not re.search(r"\bgit\s", str(step.get("run") or "")), step.get("name")

    def test_no_step_sets_the_read_door(self):
        doc = _doc()
        scopes = [doc.get("env") or {}]
        for job in doc["jobs"].values():
            scopes.append(job.get("env") or {})
            scopes += [s.get("env") or {} for s in job["steps"]]
        for env in scopes:
            for name in ("BUREAU_READ", "BUREAU_READ_URL", "BUREAU_READ_AUDIENCE"):
                assert name not in env
        assert not re.search(r"BUREAU_READ(_URL|_AUDIENCE)?\b", _text())


# --------------------------------------------------------------------------- #
# Decide: the lines, and what is handed on                                     #
# --------------------------------------------------------------------------- #


class TestDecide:
    @pytest.fixture
    def step(self):
        return _step("decide", DECIDE_STEP)

    def test_it_runs_after_a_failed_or_spent_model_step(self, step):
        assert "!cancelled()" in step["if"]
        assert step["id"] == "decide"

    def test_it_runs_the_contracts_command(self, step, tmp_path):
        env = {k: _fill(v, tmp_path) for k, v in step["env"].items() if k != "DRY_RUN"}
        env["DRY_RUN"] = "true"
        (tmp_path / "decisions.json").write_text("[]")
        _result, _outputs, argvs = _bash(step["run"], env, tmp_path)
        argv = next(a for a in argvs if any(x.endswith("operator_agent.py") for x in a))
        assert argv[0] == f"{tmp_path}/pipeline-trusted/scripts/operator_agent.py"
        assert argv[1] == "decide"
        assert argv[argv.index("--cards-dir") + 1] == f"{tmp_path}/operator-agent/cards"
        assert argv[argv.index("--answers") + 1] == f"{tmp_path}/operator-agent/answers"
        assert argv[argv.index("--out") + 1] == "decisions.json"
        assert argv[argv.index("--cards") + 1].endswith("/cards.json")

    def _hand_on(self, tmp_path, decisions, dry_run):
        (tmp_path / "decisions.json").write_text(json.dumps(decisions))
        result, outputs, _ = _bash(_output_block(), {"DRY_RUN": dry_run}, tmp_path,
                                   stub=False)
        assert result.returncode == 0, result.stderr
        return outputs

    def test_a_live_run_hands_on_the_written_decisions_and_the_apply_only_cards(self, tmp_path):
        ceo = _decision("DRE-1", "ceo", "decide", act="business-choice")
        done = _decision("DRE-2", "done-already", "apply-only", evidence=["it shipped"])
        spent = _decision("DRE-3", "studio", "spent")
        held = _decision("DRE-4", "studio", "decide", held=operator_decide.HELD_NO_QUOTE)
        outputs = self._hand_on(tmp_path, [ceo, done, spent, held], "false")
        cards = _cards_output(outputs)
        assert cards == [{"card": "DRE-1", "action": "decide", "decision": ceo},
                         {"card": "DRE-2", "action": "apply-only", "decision": done}]

    def test_it_never_hands_on_a_do_a_spent_or_a_guard_returned_card(self, tmp_path):
        rows = [_decision("DRE-5", "do", "decide"),
                _decision("DRE-6", "ceo", "spent"),
                _decision("DRE-7", "done-already", "guard-returned")]
        assert _cards_output(self._hand_on(tmp_path, rows, "false")) == []

    def test_a_dry_run_hands_on_nothing(self, tmp_path):
        rows = [_decision("DRE-1", "ceo", "decide"),
                _decision("DRE-2", "done-already", "apply-only")]
        outputs = self._hand_on(tmp_path, rows, "true")
        assert outputs["cards"] == "[]"

    def test_the_output_is_one_line(self, tmp_path):
        rows = [_decision("DRE-1", "ceo", "decide", question="Which one?\nReally?")]
        self._hand_on(tmp_path, rows, "false")
        written = (tmp_path / ".github_output").read_text().splitlines()
        assert len(written) == 1 and written[0].startswith("cards=")

    # -- the real `decide`, over a model step that died two answers in ----- #

    def _row(self, card: str, title: str, description: str, action: str = "decide") -> dict:
        row = {"card": card, "repo": "bureau-pipeline", "lane": "Hand-work",
               "title": title, "labels": ["repo:bureau-pipeline"],
               "description": description, "rule": None,
               "for_model": action == "decide", "action": action, "receipt": None}
        row["fingerprint"] = operator_decide.fingerprint(row)
        return row

    def _run_decide(self, step, tmp_path, rows, answers: dict, dry_run="false"):
        assert step["env"]["DRY_RUN"] == "${{ steps.mode.outputs.dry_run }}"
        env = {k: _fill(v, tmp_path) for k, v in step["env"].items() if k != "DRY_RUN"}
        env["TRUSTED_PIPELINE"] = str(ROOT)
        env["DRY_RUN"] = dry_run
        assert '--cards "$SELECTED/cards.json"' in step["run"]
        cards_json = Path(env["SELECTED"]) / "cards.json"
        cards_json.parent.mkdir(parents=True, exist_ok=True)
        cards_json.write_text(json.dumps(rows))
        cards_dir = tmp_path / "operator-agent" / "cards"
        answers_dir = tmp_path / "operator-agent" / "answers"
        cards_dir.mkdir(parents=True)
        answers_dir.mkdir(parents=True)
        for row in rows:
            (cards_dir / f"{row['card']}.md").write_text(
                f"{BEGIN}\nCard: {row['card']}\nTitle: {row['title']}\n\n"
                f"Description:\n{row['description']}\n{END}\n")
        for card, answer in answers.items():
            (answers_dir / f"{card}.json").write_text(json.dumps(answer))
        result, outputs, _ = _bash(step["run"], env, tmp_path, stub=False)
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout, outputs

    def test_a_failed_model_step_with_two_of_three_answers(self, step, tmp_path):
        rows = [self._row("DRE-101", "Rotate the staging banner",
                          "Swap the banner text on staging. The banner reads Welcome back."),
                self._row("DRE-102", "Tidy the staging banner",
                          "Change the banner. Status: the banner reads Hello again."),
                self._row("DRE-103", "Check the staging banner", "Look at the banner.")]
        answers = {
            "DRE-101": {"card": "DRE-101", "outcome": "do",
                        "step": "swap the banner text on staging", "reason": "a test step"},
            "DRE-102": {"card": "DRE-102", "outcome": "done-already",
                        "step": "nothing is left to do", "reason": "it already reads so",
                        "evidence": ["the banner reads Hello again."]},
        }
        log, outputs = self._run_decide(step, tmp_path, rows, answers)
        assert "operator-agent: DRE-101 → do" in log
        assert "operator-agent: DRE-102 → done-already" in log
        assert ("operator-agent: DRE-103 unread — the model wrote no answer for it"
                in log.splitlines())
        cards = _cards_output(outputs)
        assert [c["card"] for c in cards] == ["DRE-102"]
        assert "DRE-103" not in outputs["cards"]
        written = json.loads((tmp_path / "decisions.json").read_text())
        assert cards[0]["decision"] == next(d for d in written if d["card"] == "DRE-102")

    def test_a_card_left_unread_by_the_limit_is_never_handed_on(self, step, tmp_path):
        rows = [self._row("DRE-201", "Look at the banner", "Look at it.", action="unread")]
        log, outputs = self._run_decide(step, tmp_path, rows, {})
        assert ("operator-agent: DRE-201 unread — not read by the model on this pass"
                in log.splitlines())
        assert _cards_output(outputs) == []


# --------------------------------------------------------------------------- #
# the apply job                                                                #
# --------------------------------------------------------------------------- #


class TestTheApplyJob:
    def test_it_runs_only_on_a_live_run_with_a_card(self):
        gate = _job("apply")["if"]
        assert "needs.decide.outputs.dry_run == 'false'" in gate
        assert "needs.decide.outputs.cards != '[]'" in gate
        row = '[{"card":"DRE-1"}]'
        cases = [("false", row, True), ("true", row, False), ("false", "[]", False),
                 ("true", "[]", False), ("false", "", False)]
        for dry_run, cards, runs in cases:
            values = {"needs.decide.outputs.dry_run": dry_run,
                      "needs.decide.outputs.cards": cards}
            assert _evaluate(gate, values) is runs, (dry_run, cards)
        assert _job("apply")["needs"] == "decide"

    def test_one_card_at_a_time_and_one_red_leg_stops_no_other(self):
        strategy = _job("apply")["strategy"]
        assert strategy["max-parallel"] == 1
        assert strategy["fail-fast"] is False
        assert strategy["matrix"] == {"include": "${{ fromJSON(needs.decide.outputs.cards) }}"}

    def test_it_spends_the_planners_linear_bucket(self):
        env = _job("apply")["env"]
        assert env["LINEAR_IDENTITY"] == "fleet"
        assert env["LINEAR_KEY_HOME"] == "planner-oauth"
        assert env["LINEAR_PLANNER_KEY_EXPIRES_AT"] == "${{ vars.LINEAR_PLANNER_KEY_EXPIRES_AT }}"

    def test_its_steps(self):
        names = [s.get("name") for s in _job("apply")["steps"]]
        assert names == ["Check out bureau-pipeline", WRITE_STEP, "Claim the card",
                         "Apply the decision"]

    def test_the_decision_travels_through_env_never_the_shell_line(self):
        step = _step("apply", WRITE_STEP)
        assert step["env"]["DECISION"] == "${{ toJSON(matrix.decision) }}"
        assert "${{" not in step["run"]
        assert "LINEAR" not in json.dumps(step)

    def test_the_written_decision_reads_back(self, tmp_path):
        step = _step("apply", WRITE_STEP)
        decision = _decision("DRE-1", "ceo", "decide", act="business-choice",
                             question="Which plan?\n$(touch pwned)`id`")
        env = {k: _fill(v, tmp_path) for k, v in step["env"].items() if k != "DECISION"}
        env["DECISION"] = json.dumps(decision)
        env["RUNNER_TEMP"] = str(tmp_path)
        result, _outputs, _ = _bash(step["run"], env, tmp_path, stub=False)
        assert result.returncode == 0, result.stderr
        path = tmp_path / "operator-agent" / "decisions.json"
        written = json.loads(path.read_text())
        assert isinstance(written, list) and len(written) == 1
        assert written[0] == decision
        assert operator_decide.Decision.from_json(written[0]) == \
            operator_decide.Decision.from_json(decision)
        assert not (tmp_path / "pwned").exists()

    def _apply_env(self, step, tmp_path, card="DRE-1"):
        env = {k: v for k, v in step["env"].items() if "secrets." not in str(v)}
        env = {k: (card if v == "${{ matrix.card }}" else v) for k, v in env.items()}
        return {k: _fill(v, tmp_path) for k, v in env.items()}

    def test_claim_runs_for_a_decide_row_only(self):
        gate = _step("apply", "Claim the card")["if"]
        assert _evaluate(gate, {"matrix.action": "decide"})
        assert not _evaluate(gate, {"matrix.action": "apply-only"})

    def test_claim_reads_the_written_decision(self, tmp_path):
        step = _step("apply", "Claim the card")
        assert step["env"]["LINEAR_API_KEY"] == LINEAR_KEY
        result, _o, argvs = _bash(step["run"], self._apply_env(step, tmp_path), tmp_path)
        assert result.returncode == 0, result.stderr
        assert argvs == [["scripts/operator_agent_apply.py", "claim", "DRE-1", "--decisions",
                          f"{tmp_path}/operator-agent/decisions.json"]]

    def test_a_claim_exit_of_3_still_reaches_apply(self, tmp_path):
        step = _step("apply", "Claim the card")
        result, _o, _a = _bash(step["run"], self._apply_env(step, tmp_path), tmp_path, rc=3)
        assert result.returncode == 0, result.stderr
        assert "DRE-1" in result.stdout

    def test_any_other_claim_failure_is_red(self, tmp_path):
        step = _step("apply", "Claim the card")
        for rc in (1, 2, 4):
            result, _o, _a = _bash(step["run"], self._apply_env(step, tmp_path), tmp_path,
                                   rc=rc)
            assert result.returncode == rc

    def test_apply_runs_under_not_cancelled_on_the_same_file(self, tmp_path):
        step = _step("apply", "Apply the decision")
        assert "!cancelled()" in step["if"]
        assert step["env"]["LINEAR_API_KEY"] == LINEAR_KEY
        result, _o, argvs = _bash(step["run"], self._apply_env(step, tmp_path), tmp_path)
        assert result.returncode == 0
        assert argvs == [["scripts/operator_agent_apply.py", "apply", "DRE-1", "--decisions",
                          f"{tmp_path}/operator-agent/decisions.json"]]

    def test_a_refused_write_fails_its_own_leg(self, tmp_path):
        step = _step("apply", "Apply the decision")
        result, _o, _a = _bash(step["run"], self._apply_env(step, tmp_path), tmp_path, rc=4)
        assert result.returncode == 4
