"""The Operator Agent's `do` step, wired (DRE-8928, epic DRE-8738).

Part 1's `decide` job (DRE-8747) prints each `do` card and hands nothing on.
This card hands up to `max_do_per_pass` of them a pass to a `do` job, closes
each card whose receipt owes a close in a `close` job, and reads every pass
whether the credentials those steps use hold what they need in a
`credentials` job. The modules are the sibling cards': DRE-8922's
`operator_do_ops.py`, DRE-8923's `continue` and `close-only` actions,
DRE-8924's `operator_do.py` and DRE-8925's `do-claim`, `do-post` and
`do-close`. The workflow wires them and decides nothing.

Every test reads the LIVE `.github/workflows/operator-agent.yml`. The shell
that decides anything — whether a pass is a do dry run, which cards the `do`
and `close` jobs get, what a claim's exit does, whether an AWS credential is
in reach — is EXECUTED under bash, and the `if:` of each gate is EVALUATED,
so the answer is what the runner would do and not what the YAML looks like
it says. `tests/test_operator_agent_workflow.py` holds part 1's wiring.
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
import operator_do_ops  # noqa: E402
import switch_reason  # noqa: E402

WORKFLOW = ROOT / ".github" / "workflows" / "operator-agent.yml"
DO_CONFIG = ROOT / "config" / "operator-do.json"

SWITCH = "OPERATOR_AGENT_DO_LIVE"
MODE_STEP = "Decide do dry run"
DECIDE_STEP = "Decide"
ENV_STEP = "Check no AWS credential is in reach"
READ_STEP = "Read the card"
PLAN_STEP = "Plan the step"
WRITE_STEP = "Write the decision"
CHECK_STEP = "Check the plan"
CLAIM_STEP = "Claim the card"
RUN_STEP = "Run the step"
POST_STEP = "Post the receipt"
COPY_STEP = "Keep the trusted copy"
CLOSE_STEP = "Close the card"
PR_MINT_STEP = "Mint a pull request read token"
PROBE_STEP = "Read the credentials"
MINTS = {  # kind -> (step name, step id)
    "variable": ("Mint the variable token", "do_token_variable"),
    "bot-pr": ("Mint the bot-pr token", "do_token_bot_pr"),
    "script": ("Mint the script token", "do_token_script"),
}
#: The `do` job's steps, by name and in this order.
DO_STEPS = (
    ENV_STEP,
    "Check out bureau-pipeline",
    COPY_STEP,
    READ_STEP,
    "Mint the model-home read token",
    "Select the model",
    "Install Claude Code (asserted)",
    PLAN_STEP,
    WRITE_STEP,
    CHECK_STEP,
    CLAIM_STEP,
    MINTS["variable"][0],
    MINTS["bot-pr"][0],
    MINTS["script"][0],
    RUN_STEP,
    POST_STEP,
    "Remove the trusted copy and the sealed files",
)
CLOSE_STEPS = (ENV_STEP, "Check out bureau-pipeline", PR_MINT_STEP, CLOSE_STEP)
CREDENTIAL_STEPS = ("Check out bureau-pipeline", PROBE_STEP)

ACTION = "anthropics/claude-code-action"
MINT_ACTION = "actions/create-github-app-token"
TRUSTED = "${{ runner.temp }}/pipeline-trusted"
DO_DIR = "${{ runner.temp }}/operator-do"
RUN_DIR = "${{ runner.temp }}/operator-do-run"
LINEAR_KEY = "${{ secrets.LINEAR_PLANNER_KEY || secrets.LINEAR_API_KEY }}"
STEP_TOKEN = ("${{ steps.do_token_variable.outputs.token || steps.do_token_bot_pr.outputs.token"
              " || steps.do_token_script.outputs.token }}")
#: The secrets a `do` step may hold: the Linear key, the bureau App's id and
#: key, and the Claude credential.
DO_SECRETS = {"LINEAR_PLANNER_KEY", "LINEAR_API_KEY", "BUREAU_APP_ID",
              "BUREAU_APP_PRIVATE_KEY", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"}
CLAUDE_SECRETS = ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")
DRY = "needs.decide.outputs.do_dry_run"


def _doc() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _header() -> str:
    return _text().split("\nname:", 1)[0]


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


def _evaluate(expr, values: dict) -> bool:
    """A GitHub `if:` over the names in `values`, each put in as its literal;
    `cancelled()` is false and the operators are read as Python's. A name
    left over fails to evaluate, so a gate reading something this test does
    not know — `success()` included — is a failure, never a pass."""
    body = str(expr).strip()
    if body.startswith("${{") and body.endswith("}}"):
        body = body[3:-2]
    for name, value in sorted(values.items(), key=lambda kv: -len(kv[0])):
        body = body.replace(name, repr(value))
    body = body.replace("cancelled()", "False")
    body = body.replace("&&", " and ").replace("||", " or ")
    body = re.sub(r"!(?!=)", " not ", body)
    return bool(eval(body, {"__builtins__": {}}, {}))  # noqa: S307 — test-only, literals


def _fill(template, temp: Path, card: str = "DRE-1") -> str:
    value = str(template).replace("${{ runner.temp }}", str(temp))
    value = value.replace("${{ matrix.card }}", card)
    assert "${{" not in value, f"an expression this test cannot fill: {value!r}"
    return value


def _plain_env(step: dict, temp: Path, card: str = "DRE-1") -> dict:
    """The step's env with every value this test can fill, the secrets and
    other expressions left out."""
    out = {}
    for key, value in (step.get("env") or {}).items():
        try:
            out[key] = _fill(value, temp, card)
        except AssertionError:
            continue
    return out


def _decision(card: str, outcome: str, action: str, held: str = "", **extra) -> dict:
    """One `decisions.json` entry: `Decision.to_json()` with the row's action."""
    fields = dict(card=card, repo="bureau-pipeline", lane="Hand-work", outcome=outcome,
                  reason="the model read the card", step=f"the step {card} names",
                  act="", deploy=False, evidence=[], host_cannot=[], finding="",
                  question="", recommendation="", choices=None, held=held,
                  fingerprint="0123456789ab")
    fields.update(extra)
    return {**operator_decide.Decision(**fields).to_json(), "action": action}


def _row(decision: dict) -> dict:
    return {"card": decision["card"], "action": decision["action"], "decision": decision}


def _output_block() -> str:
    """The `Decide` step's run with the module's call taken out: the block
    that reads `decisions.json` and writes the outputs."""
    run = _step("decide", DECIDE_STEP)["run"]
    lines = run.splitlines()
    start = next(i for i, line in enumerate(lines) if "operator_agent.py" in line)
    end = start
    while lines[end].rstrip().endswith("\\"):
        end += 1
    return "\n".join(lines[:start] + lines[end + 1:])


def _hand_on(tmp_path: Path, decisions: list, dry_run: str, trusted: Path = ROOT) -> dict:
    (tmp_path / "decisions.json").write_text(json.dumps(decisions))
    result, outputs, _ = _bash(_output_block(),
                               {"DRY_RUN": dry_run, "TRUSTED_PIPELINE": str(trusted)},
                               tmp_path, stub=False)
    assert result.returncode == 0, result.stderr
    return outputs


def _secrets(value) -> set:
    return set(re.findall(r"secrets\.([A-Z_]+)", json.dumps(value)))


def _live(**extra) -> dict:
    """The names a `do` leg's gates read, on a live pass whose plan passed
    its check and whose claim exited 0."""
    values = {DRY: "false", "steps.check.outputs.ok": "true",
              "steps.check.outputs.kind": "variable", "steps.claim.outputs.claimed": "true"}
    values.update(extra)
    return values


# --------------------------------------------------------------------------- #
# the switch                                                                   #
# --------------------------------------------------------------------------- #


class TestDecideDoDryRun:
    @pytest.fixture
    def step(self):
        return _step("decide", MODE_STEP)

    def _decide(self, step, tmp_path, event, part1="false", dispatch="", live=None):
        env = {"EVENT": event, "DRY_RUN": part1, "DISPATCH_DO_DRY_RUN": dispatch}
        if live is not None:
            env[SWITCH] = live
        result, outputs, argvs = _bash(step["run"], env, tmp_path)
        assert result.returncode == 0, result.stderr
        assert argvs == []
        assert (f"operator-agent: do event={event} dry_run={outputs['do_dry_run']}"
                in result.stdout.splitlines())
        return outputs["do_dry_run"]

    def test_it_reads_part_ones_mode_the_event_the_input_and_the_variable(self, step):
        assert step["id"] == "do_mode"
        assert step["env"] == {
            "EVENT": "${{ github.event_name }}",
            "DRY_RUN": "${{ steps.mode.outputs.dry_run }}",
            "DISPATCH_DO_DRY_RUN": "${{ inputs.do_dry_run }}",
            SWITCH: "${{ vars.OPERATOR_AGENT_DO_LIVE }}",
        }
        assert _job("decide")["outputs"]["do_dry_run"] == "${{ steps.do_mode.outputs.do_dry_run }}"

    def test_it_is_decided_once_after_part_ones_mode_and_before_any_card_is_read(self):
        assert _index("decide", "Decide dry run") < _index("decide", MODE_STEP)
        assert _index("decide", MODE_STEP) < _index("decide", "Select cards")
        readers = [s["name"] for job in _doc()["jobs"].values() for s in job["steps"]
                   if "vars.OPERATOR_AGENT_DO_LIVE" in json.dumps(s)]
        assert readers == [MODE_STEP]

    def test_a_schedule_is_do_live_only_on_exactly_true(self, step, tmp_path):
        assert self._decide(step, tmp_path, "schedule", live="true") == "false"
        for near_miss in ("TRUE", "true ", "True", "false", "", "1"):
            assert self._decide(step, tmp_path, "schedule", live=near_miss) == "true", near_miss

    def test_an_unset_switch_is_a_do_dry_run(self, step, tmp_path):
        assert self._decide(step, tmp_path, "schedule") == "true"

    def test_an_unreadable_switch_is_a_do_dry_run(self, step, tmp_path):
        """A variable the run cannot read reaches the step as the empty
        string, and a value that is not exactly `true` is off."""
        assert self._decide(step, tmp_path, "schedule", live="") == "true"
        assert self._decide(step, tmp_path, "schedule", live="${{ vars.X }}") == "true"

    def test_a_part_one_dry_run_is_a_do_dry_run_whatever_the_switch_says(self, step, tmp_path):
        for part1 in ("true", "", "false "):
            assert self._decide(step, tmp_path, "schedule", part1=part1, live="true") == "true"
            assert self._decide(step, tmp_path, "workflow_dispatch", part1=part1,
                                dispatch="false") == "true"

    def test_a_dispatch_is_do_live_only_on_exactly_false(self, step, tmp_path):
        assert self._decide(step, tmp_path, "workflow_dispatch", dispatch="false") == "false"
        for other in ("true", "", "FALSE", "no"):
            assert self._decide(step, tmp_path, "workflow_dispatch", dispatch=other,
                                live="true") == "true", other

    def test_the_schedule_ignores_the_dispatch_input(self, step, tmp_path):
        assert self._decide(step, tmp_path, "schedule", dispatch="false") == "true"

    def test_any_other_event_is_a_do_dry_run(self, step, tmp_path):
        assert self._decide(step, tmp_path, "push", dispatch="false", live="true") == "true"

    def test_the_dispatch_input_defaults_to_a_do_dry_run(self):
        inputs = check_workflow_watchers.on_block(_doc())["workflow_dispatch"]["inputs"]
        assert inputs["do_dry_run"]["type"] == "boolean"
        assert inputs["do_dry_run"]["default"] is True

    def test_the_switch_has_its_row_and_its_doc_line(self):
        rows = {r["name"]: r for r in switch_reason.load()["switches"]}
        row = rows[SWITCH]
        assert row["reader"] == ".github/workflows/operator-agent.yml"
        assert row["step"] == MODE_STEP
        assert "do" in row["means"] and "receipt" in row["means"]
        docs = (ROOT / "docs" / "switches.md").read_text(encoding="utf-8")
        line = next(l for l in docs.splitlines()
                    if l.startswith(f"| `{SWITCH}` | `operator-agent.yml`, `{MODE_STEP}` |"))
        assert "claims" in line and "receipt" in line

    def test_the_check_passes_with_the_row(self):
        done = subprocess.run([sys.executable, str(ROOT / "scripts" / "switch_reason.py"),
                               "check"], cwd=ROOT, capture_output=True, text=True)
        assert done.returncode == 0, done.stdout + done.stderr

    def test_the_check_fails_with_the_row_removed(self):
        doc = switch_reason.load()
        doc["switches"] = [r for r in doc["switches"] if r["name"] != SWITCH]
        found = switch_reason.problems(doc)
        assert found and all(SWITCH in f for f in found), found

    def test_the_agent_never_names_its_switch_in_a_plan(self):
        assert SWITCH in operator_do_ops.load()["never"]


class TestADoDryRun:
    """Nothing is claimed, minted, run or posted, and nothing is closed."""

    def test_nothing_is_claimed_minted_or_posted(self):
        for dry in ("true", ""):
            values = _live(**{DRY: dry})
            values["steps.check.outputs.ok"] = "true"
            assert not _evaluate(_step("do", CLAIM_STEP)["if"], values)
            for kind, (name, _id) in MINTS.items():
                assert not _evaluate(_step("do", name)["if"],
                                     {**values, "steps.check.outputs.kind": kind}), name
            for ok in ("true", "false"):
                assert not _evaluate(_step("do", POST_STEP)["if"],
                                     {**values, "steps.check.outputs.ok": ok})

    def test_the_run_step_still_prints_what_it_would_run(self, tmp_path):
        step = _step("do", RUN_STEP)
        values = {DRY: "true", "steps.check.outputs.ok": "true",
                  "steps.check.outputs.kind": "bot-pr", "steps.claim.outputs.claimed": ""}
        assert _evaluate(step["if"], values)
        env = _plain_env(step, tmp_path)
        env["DO_DRY_RUN"] = "true"
        result, _o, argvs = _bash(step["run"], env, tmp_path)
        assert result.returncode == 0, result.stderr
        assert len(argvs) == 1 and argvs[0][-1] == "--dry-run"
        assert argvs[0][1:3] == ["run", "DRE-1"]

    def test_an_unreadable_mode_is_a_dry_run_at_the_run_step(self, tmp_path):
        step = _step("do", RUN_STEP)
        for value in ("", "TRUE", "no"):
            env = _plain_env(step, tmp_path)
            env["DO_DRY_RUN"] = value
            _r, _o, argvs = _bash(step["run"], env, tmp_path)
            assert argvs[0][-1] == "--dry-run", value

    def test_the_run_step_holds_no_credential_in_a_dry_run(self):
        env = _step("do", RUN_STEP)["env"]
        assert env["GH_TOKEN"] == STEP_TOKEN
        assert env["DO_DRY_RUN"] == "${{ needs.decide.outputs.do_dry_run }}"
        linear = env["LINEAR_API_KEY"]
        assert linear.startswith("${{ needs.decide.outputs.do_dry_run == 'false' && "
                                 "steps.check.outputs.kind == 'linear-admin' && ")
        assert linear.endswith(" || '' }}")

    def test_the_close_job_does_not_run(self):
        gate = _job("close")["if"]
        row = '[{"card":"DRE-1"}]'
        for dry in ("true", "", "TRUE"):
            assert not _evaluate(gate, {DRY: dry, "needs.decide.outputs.close_cards": row})


# --------------------------------------------------------------------------- #
# what the decide job hands on                                                 #
# --------------------------------------------------------------------------- #


class TestTheHandOn:
    def test_the_decide_job_declares_the_three_new_outputs(self):
        outputs = _job("decide")["outputs"]
        assert outputs["do_dry_run"] == "${{ steps.do_mode.outputs.do_dry_run }}"
        assert outputs["do_cards"] == "${{ steps.decide.outputs.do_cards }}"
        assert outputs["close_cards"] == "${{ steps.decide.outputs.close_cards }}"
        assert outputs["cards"] == "${{ steps.decide.outputs.cards }}"

    def test_the_decide_step_reads_the_limits_from_the_trusted_copy(self):
        step = _step("decide", DECIDE_STEP)
        assert step["env"]["TRUSTED_PIPELINE"] == TRUSTED
        assert "operator-do.json" in step["run"]

    @pytest.mark.parametrize("dry_run", ["true", "false"])
    def test_do_cards_are_written_in_both_modes(self, tmp_path, dry_run):
        first = _decision("DRE-1", "do", "decide")
        outputs = _hand_on(tmp_path, [first], dry_run)
        assert json.loads(outputs["do_cards"]) == [_row(first)]
        assert json.loads(outputs["close_cards"]) == [_row(first)]

    def test_only_a_do_not_held_decide_or_continue_row_is_a_do_card(self, tmp_path):
        rows = [_decision("DRE-1", "do", "decide"),
                _decision("DRE-2", "do", "decide", held=operator_decide.HELD_NO_QUOTE),
                _decision("DRE-3", "ceo", "decide"),
                _decision("DRE-4", "do", "continue"),
                _decision("DRE-5", "do", "spent"),
                _decision("DRE-6", "do", "close-only"),
                _decision("DRE-7", "do", "guard-returned"),
                _decision("DRE-8", "studio", "apply-only")]
        outputs = _hand_on(tmp_path, rows, "false")
        assert [r["card"] for r in json.loads(outputs["do_cards"])] == ["DRE-1", "DRE-4"]
        assert json.loads(outputs["do_cards"])[1] == _row(rows[3])

    def test_at_most_max_do_per_pass_in_the_order_decisions_lists_them(self, tmp_path):
        limit = json.loads(DO_CONFIG.read_text())["max_do_per_pass"]
        assert limit == 3
        rows = [_decision(f"DRE-{n}", "do", "decide" if n % 2 else "continue")
                for n in (9, 4, 7, 2, 5)]
        outputs = _hand_on(tmp_path, rows, "false")
        assert [r["card"] for r in json.loads(outputs["do_cards"])] == ["DRE-9", "DRE-4", "DRE-7"]

    def test_close_cards_are_the_close_only_rows_then_the_do_cards(self, tmp_path):
        rows = [_decision("DRE-1", "do", "decide"),
                _decision("DRE-2", "do", "close-only"),
                _decision("DRE-3", "do", "continue"),
                _decision("DRE-4", "do", "close-only"),
                _decision("DRE-5", "ceo", "close-only")]
        outputs = _hand_on(tmp_path, rows, "true")
        assert [r["card"] for r in json.loads(outputs["close_cards"])] == \
            ["DRE-2", "DRE-4", "DRE-5", "DRE-1", "DRE-3"]

    def test_close_cards_stop_at_max_close_per_pass(self, tmp_path):
        assert json.loads(DO_CONFIG.read_text())["max_close_per_pass"] == 10
        rows = [_decision(f"DRE-{n}", "do", "close-only") for n in range(1, 10)]
        rows += [_decision(f"DRE-{n}", "do", "decide") for n in range(20, 23)]
        outputs = _hand_on(tmp_path, rows, "false")
        cards = [r["card"] for r in json.loads(outputs["close_cards"])]
        assert cards == [f"DRE-{n}" for n in range(1, 10)] + ["DRE-20"]
        assert len(json.loads(outputs["do_cards"])) == 3

    def test_the_limits_are_read_from_the_file_never_restated(self, tmp_path):
        trusted = tmp_path / "trusted"
        (trusted / "config").mkdir(parents=True)
        # The rest of the checkout, as the trusted copy holds it: the block
        # reads its outcomes off the copy's operator_decide (DRE-9052).
        (trusted / "scripts").symlink_to(ROOT / "scripts")
        for path in (ROOT / "config").iterdir():
            if path.name != DO_CONFIG.name:
                (trusted / "config" / path.name).symlink_to(path)
        doc = json.loads(DO_CONFIG.read_text())
        doc.update(max_do_per_pass=1, max_close_per_pass=2)
        (trusted / "config" / "operator-do.json").write_text(json.dumps(doc))
        rows = [_decision("DRE-1", "do", "close-only"), _decision("DRE-2", "do", "decide"),
                _decision("DRE-3", "do", "decide"), _decision("DRE-4", "do", "close-only")]
        outputs = _hand_on(tmp_path, rows, "false", trusted=trusted)
        assert [r["card"] for r in json.loads(outputs["do_cards"])] == ["DRE-2"]
        assert [r["card"] for r in json.loads(outputs["close_cards"])] == ["DRE-1", "DRE-4"]

    def test_a_card_that_is_not_a_card_id_is_never_handed_on(self, tmp_path):
        rows = [_decision("DRE-1; touch pwned", "do", "decide"),
                _decision("dre-2", "do", "close-only"),
                _decision("DRE-3", "do", "decide")]
        outputs = _hand_on(tmp_path, rows, "false")
        assert [r["card"] for r in json.loads(outputs["do_cards"])] == ["DRE-3"]
        assert [r["card"] for r in json.loads(outputs["close_cards"])] == ["DRE-3"]

    def test_cards_is_unchanged_and_never_carries_a_do(self, tmp_path):
        ceo = _decision("DRE-1", "ceo", "decide")
        rows = [ceo, _decision("DRE-2", "do", "decide"), _decision("DRE-3", "do", "continue"),
                _decision("DRE-4", "do", "close-only")]
        live = _hand_on(tmp_path, rows, "false")
        assert json.loads(live["cards"]) == [_row(ceo)]
        assert _hand_on(tmp_path, rows, "true")["cards"] == "[]"

    def test_each_output_is_one_line(self, tmp_path):
        rows = [_decision("DRE-1", "do", "decide", step="one\ntwo")]
        _hand_on(tmp_path, rows, "false")
        written = (tmp_path / ".github_output").read_text().splitlines()
        assert [line.split("=", 1)[0] for line in written] == ["cards", "do_cards",
                                                               "close_cards"]

    def test_no_card_writes_empty_lists(self, tmp_path):
        outputs = _hand_on(tmp_path, [], "true")
        assert outputs["do_cards"] == "[]" and outputs["close_cards"] == "[]"


class TestTheMatrices:
    def test_the_do_job_runs_whenever_do_cards_is_not_empty(self):
        job = _job("do")
        assert job["needs"] == "decide"
        row = '[{"card":"DRE-1"}]'
        for dry in ("true", "false"):
            assert _evaluate(job["if"], {"needs.decide.outputs.do_cards": row, DRY: dry})
        for empty in ("[]", ""):
            assert not _evaluate(job["if"], {"needs.decide.outputs.do_cards": empty,
                                             DRY: "false"})

    @pytest.mark.parametrize("job,output", [("do", "do_cards"), ("close", "close_cards")])
    def test_one_leg_at_a_time_and_one_red_leg_stops_no_other(self, job, output):
        strategy = _job(job)["strategy"]
        assert strategy["max-parallel"] == 1
        assert strategy["fail-fast"] is False
        assert strategy["matrix"] == {"include": f"${{{{ fromJSON(needs.decide.outputs.{output}) }}}}"}

    def test_the_jobs_spend_the_planners_linear_bucket(self):
        for name in ("do", "close", "credentials"):
            env = _job(name)["env"]
            assert env["LINEAR_IDENTITY"] == "fleet", name
            assert env["LINEAR_KEY_HOME"] == "planner-oauth", name


# --------------------------------------------------------------------------- #
# the do job                                                                   #
# --------------------------------------------------------------------------- #


class TestTheDoJob:
    def test_its_steps_in_order(self):
        assert [s.get("name") for s in _job("do")["steps"]] == list(DO_STEPS)

    def test_its_token_reads_contents_and_nothing_more(self):
        assert _job("do")["permissions"] == {"contents": "read"}

    def test_each_leg_starts_from_a_fresh_checkout_beside_the_workspace(self):
        step = _step("do", "Check out bureau-pipeline")
        assert step["uses"].startswith("actions/checkout@")
        assert step["with"] == {"path": ".bureau-pipeline", "persist-credentials": False}

    def test_one_action_each(self):
        for step in _job("do")["steps"]:
            assert ("uses" in step) != ("run" in step), step.get("name")

    def test_read_holds_the_linear_key_and_seals_the_card_file(self, tmp_path):
        step = _step("do", READ_STEP)
        assert step["id"] == "read"
        assert step["env"]["LINEAR_API_KEY"] == LINEAR_KEY
        result, _o, argvs = _bash(step["run"], _plain_env(step, tmp_path), tmp_path)
        assert result.returncode == 0, result.stderr
        assert argvs == [[".bureau-pipeline/scripts/operator_do.py", "read", "DRE-1",
                          "--out", f"{tmp_path}/operator-do/cards"]]
        assert re.search(r'chmod -R a-w "\$DO_DIR/cards"', step["run"])

    def test_the_decision_is_written_after_the_model_through_env(self, tmp_path):
        step = _step("do", WRITE_STEP)
        assert _index("do", WRITE_STEP) > _index("do", PLAN_STEP)
        assert step["env"]["DECISION"] == "${{ toJSON(matrix.decision) }}"
        assert "${{" not in step["run"]
        decision = _decision("DRE-1", "do", "decide", step="$(touch pwned)`id`")
        env = _plain_env(step, tmp_path)
        env["DECISION"] = json.dumps(decision)
        result, _o, _a = _bash(step["run"], env, tmp_path, stub=False)
        assert result.returncode == 0, result.stderr
        written = json.loads((tmp_path / "operator-do-run" / "decisions.json").read_text())
        assert written == [decision]
        assert not (tmp_path / "pwned").exists()

    def test_the_leg_files_lie_outside_the_models_directory(self):
        assert not RUN_DIR.startswith(DO_DIR + "/")
        for name in (WRITE_STEP, CHECK_STEP, CLAIM_STEP, RUN_STEP, POST_STEP):
            env = _step("do", name)["env"]
            assert env.get("RUN_DIR") == RUN_DIR, name

    def test_a_reused_runners_files_are_cleared_before_the_leg(self):
        run = _step("do", COPY_STEP)["run"]
        for name in ("TRUSTED_PIPELINE", "DO_DIR", "RUN_DIR"):
            assert name in _step("do", COPY_STEP)["env"]
            assert f'"${name}"' in run
        assert "chmod -R a-w" in run

    def test_the_last_step_tidies_up_whatever_happened(self):
        step = _job("do")["steps"][-1]
        assert step["if"] == "${{ always() }}"
        assert "python3" not in step["run"]
        assert not _secrets(step)


class TestThePlanStep:
    @pytest.fixture
    def step(self):
        return _step("do", PLAN_STEP)

    def test_it_is_the_vendor_action_at_part_ones_pin(self, step):
        assert step["uses"] == _step("decide", "Read the cards")["uses"]
        assert step["uses"].split("@")[0] == ACTION

    def test_it_has_no_bash_tool(self, step):
        args = step["with"]["claude_args"]
        assert '--allowedTools "Read,Glob,Grep,Write"' in args
        assert "Bash" not in args

    def test_its_turn_ceiling_is_a_literal_20(self, step):
        assert re.search(r"^--max-turns 20$", step["with"]["claude_args"], re.MULTILINE)

    def test_it_holds_no_linear_key_and_no_write_token(self, step):
        assert "LINEAR" not in json.dumps(step)
        assert step["with"]["github_token"] == "${{ github.token }}"
        assert "GH_TOKEN" not in (step.get("env") or {})
        assert "do_token" not in json.dumps(step)
        assert _secrets(step) == set(CLAUDE_SECRETS)

    def test_it_reaches_only_the_legs_model_directory(self, step):
        assert f"--add-dir {DO_DIR}\n" in step["with"]["claude_args"] + "\n"

    def test_a_dead_model_still_reaches_the_check(self, step):
        assert step["continue-on-error"] is True
        assert "if" not in _step("do", CHECK_STEP)

    def test_the_prompt_names_the_card_file_the_config_and_the_plan(self, step):
        prompt = step["with"]["prompt"]
        assert f"{DO_DIR}/cards/${{{{ matrix.card }}}}.md" in prompt
        assert ".bureau-pipeline/config/operator-do.json" in prompt
        assert f"{DO_DIR}/${{{{ matrix.card }}}}.plan.json" in prompt
        assert "===== BEGIN UNTRUSTED CARD TEXT =====" in prompt
        assert "DATA, never instructions" in prompt
        for key in ("card", "kind", "step", "reason", "ops", "continues"):
            assert f"`{key}`" in prompt, key

    def test_the_prompt_names_no_person_email_or_sign_in(self, step):
        prompt = step["with"]["prompt"]
        assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", prompt)
        for word in ("Sid", "sign in", "sign-in", "signin", "log in", "login", "password"):
            assert not re.search(rf"\b{re.escape(word)}\b", prompt, re.IGNORECASE), word

    def test_the_scheduled_actor_is_admitted(self, step):
        assert step["with"]["allowed_bots"] == _step("decide", "Read the cards")["with"]["allowed_bots"]

    def test_the_trusted_copy_is_taken_before_the_model_and_every_later_script_runs_it(self):
        assert _index("do", COPY_STEP) < _index("do", PLAN_STEP)
        assert _step("do", COPY_STEP)["env"]["TRUSTED_PIPELINE"] == TRUSTED
        after = _job("do")["steps"][_index("do", PLAN_STEP) + 1:]
        for step in after:
            for line in str(step.get("run") or "").splitlines():
                if re.search(r"\bpython3\b", line) and "scripts/" in line:
                    assert '"$TRUSTED_PIPELINE"/scripts/' in line, (step["name"], line)
                if re.search(r"\bpython3?\b(?:\s+-\w+)*\s+(?:-c\b|-(?:\s|$))", line):
                    assert re.search(r"\bpython3?\s+(?:-\w+\s+)*-I\b", line), (step["name"], line)
            if "TRUSTED_PIPELINE" in (step.get("env") or {}):
                assert step["env"]["TRUSTED_PIPELINE"] == TRUSTED

    def test_the_model_is_selected_without_the_claude_credential(self):
        step = _step("do", "Select the model")
        assert step["env"]["BUREAU_MODEL_HOME_TOKEN"] == "${{ steps.model_home.outputs.token }}"
        assert not _secrets(step)
        mint = _step("do", "Mint the model-home read token")
        assert mint["id"] == "model_home"
        assert mint["continue-on-error"] is True
        assert mint["with"]["repositories"] == "bureau-pipeline"


class TestTheCheck:
    @pytest.fixture
    def step(self):
        return _step("do", CHECK_STEP)

    def _trusted(self, tmp_path: Path, checked: dict, rc: int) -> Path:
        """A trusted copy whose `operator_do.py` writes `checked` to the
        `--out` file and exits `rc`, recording its argv."""
        trusted = tmp_path / "trusted"
        (trusted / "scripts").mkdir(parents=True, exist_ok=True)
        (trusted / "scripts" / "operator_do.py").write_text(
            "import json, sys\n"
            "argv = sys.argv[1:]\n"
            f"open({str(tmp_path / 'argv.json')!r}, 'w').write(json.dumps(argv))\n"
            "out = argv[argv.index('--out') + 1]\n"
            f"open(out, 'w').write(json.dumps({checked!r}))\n"
            f"sys.exit({rc})\n")
        return trusted

    def _check(self, step, tmp_path, checked: dict, rc: int):
        env = _plain_env(step, tmp_path)
        env["TRUSTED_PIPELINE"] = str(self._trusted(tmp_path, checked, rc))
        (tmp_path / "operator-do-run").mkdir(exist_ok=True)
        return _bash(step["run"], env, tmp_path, stub=False)

    def test_it_runs_the_contracts_command(self, step, tmp_path):
        assert step["id"] == "check"
        checked = {"ok": True, "kind": "bot-pr", "repo": "dreadnought-foundry/bureau-pipeline",
                   "command_lines": []}
        result, _o, _a = self._check(step, tmp_path, checked, 0)
        assert result.returncode == 0, result.stderr
        argv = json.loads((tmp_path / "argv.json").read_text())
        assert argv[:2] == ["check", "DRE-1"]
        assert argv[argv.index("--plan") + 1] == f"{tmp_path}/operator-do/DRE-1.plan.json"
        assert argv[argv.index("--card") + 1] == f"{tmp_path}/operator-do/cards/DRE-1.md"
        assert argv[argv.index("--out") + 1] == f"{tmp_path}/operator-do-run/DRE-1.check.json"
        assert argv[argv.index("--transcript") + 1] == f"{tmp_path}/operator-do-run/transcript.json"

    @pytest.mark.parametrize("kind", sorted(operator_do_ops.load()["kinds"]))
    def test_ok_kind_and_repo_become_outputs(self, step, tmp_path, kind):
        repo = None if kind == "linear-admin" else "dreadnought-foundry/bureau-pipeline"
        checked = {"ok": True, "kind": kind, "repo": repo, "command_lines": ["x"]}
        result, outputs, _a = self._check(step, tmp_path, checked, 0)
        assert result.returncode == 0, result.stderr
        assert outputs["ok"] == "true"
        assert outputs["kind"] == kind
        assert outputs["repo"] == (repo or "")
        assert outputs["repo_name"] == ("bureau-pipeline" if repo else "")

    def test_a_refusal_is_ok_false_and_the_leg_goes_on(self, step, tmp_path):
        checked = {"ok": False, "kind": "variable", "repo": None, "command_lines": []}
        result, outputs, _a = self._check(step, tmp_path, checked, 1)
        assert result.returncode == 0, result.stderr
        assert outputs["ok"] == "false"

    def test_any_other_exit_fails_the_leg(self, step, tmp_path):
        checked = {"ok": False, "kind": "", "repo": None, "command_lines": []}
        for rc in (2, 3):
            result, _o, _a = self._check(step, tmp_path, checked, rc)
            assert result.returncode == rc

    def test_a_repo_outside_the_org_or_off_the_name_rule_mints_for_nothing(self, step, tmp_path):
        for repo in ("someone-else/bureau-pipeline", "dreadnought-foundry/a b",
                     "dreadnought-foundry/x\nrepo_name=evil", "dreadnought-foundry/a/b"):
            checked = {"ok": True, "kind": "script", "repo": repo, "command_lines": []}
            result, outputs, _a = self._check(step, tmp_path, checked, 0)
            assert result.returncode == 0, result.stderr
            assert outputs["repo"] == "" and outputs["repo_name"] == "", repo


class TestTheClaim:
    @pytest.fixture
    def step(self):
        return _step("do", CLAIM_STEP)

    def test_it_runs_on_a_live_leg_whose_plan_passed(self, step):
        assert _evaluate(step["if"], _live())
        assert not _evaluate(step["if"], _live(**{"steps.check.outputs.ok": "false"}))
        assert not _evaluate(step["if"], _live(**{"steps.check.outputs.ok": ""}))
        assert not _evaluate(step["if"], _live(**{DRY: "true"}))

    def test_it_holds_the_linear_key_and_runs_do_claim(self, step, tmp_path):
        assert step["env"]["LINEAR_API_KEY"] == LINEAR_KEY
        result, outputs, argvs = _bash(step["run"], _plain_env(step, tmp_path), tmp_path)
        assert result.returncode == 0, result.stderr
        run = f"{tmp_path}/operator-do-run"
        assert argvs == [[f"{tmp_path}/pipeline-trusted/scripts/operator_agent_apply.py",
                          "do-claim", "DRE-1", "--decisions", f"{run}/decisions.json",
                          "--check", f"{run}/DRE-1.check.json"]]
        assert outputs.get("claimed") == "true"

    def test_an_exit_of_3_ends_the_leg_green_with_nothing_run(self, step, tmp_path):
        result, outputs, _a = _bash(step["run"], _plain_env(step, tmp_path), tmp_path, rc=3)
        assert result.returncode == 0, result.stderr
        assert "claimed" not in outputs
        assert "DRE-1" in result.stdout
        after = _live(**{"steps.claim.outputs.claimed": ""})
        for name in [n for n, _i in MINTS.values()] + [RUN_STEP, POST_STEP]:
            for kind in MINTS:
                assert not _evaluate(_step("do", name)["if"],
                                     {**after, "steps.check.outputs.kind": kind}), name

    def test_any_other_exit_fails_the_leg_with_nothing_run(self, step, tmp_path):
        for rc in (1, 2, 4):
            result, outputs, _a = _bash(step["run"], _plain_env(step, tmp_path), tmp_path,
                                        rc=rc)
            assert result.returncode == rc
            assert "claimed" not in outputs


class TestTheMints:
    @pytest.mark.parametrize("kind", sorted(MINTS))
    def test_each_runs_only_for_its_kind_after_a_claim_that_exited_0(self, kind):
        name, ident = MINTS[kind]
        step = _step("do", name)
        assert step["id"] == ident
        assert step["uses"].split("@")[0] == MINT_ACTION
        for other in list(MINTS) + ["linear-admin", ""]:
            assert _evaluate(step["if"], _live(**{"steps.check.outputs.kind": other})) is \
                (other == kind), other
        assert not _evaluate(step["if"], _live(**{"steps.check.outputs.kind": kind,
                                                  "steps.claim.outputs.claimed": ""}))

    @pytest.mark.parametrize("kind", sorted(MINTS))
    def test_each_names_one_repository_from_the_check_file(self, kind):
        step = _step("do", MINTS[kind][0])
        assert step["with"]["owner"] == "dreadnought-foundry"
        assert step["with"]["repositories"] == "${{ steps.check.outputs.repo_name }}"
        assert step["with"]["app-id"] == "${{ secrets.BUREAU_APP_ID }}"
        assert step["with"]["private-key"] == "${{ secrets.BUREAU_APP_PRIVATE_KEY }}"

    def test_the_variable_mint_asks_for_no_permission_subset(self):
        keys = set(_step("do", MINTS["variable"][0])["with"])
        assert keys == {"app-id", "private-key", "owner", "repositories"}
        assert not [k for k in keys if k.startswith("permission-")]

    def test_the_bot_pr_mint_asks_for_contents_and_pull_requests_write(self):
        given = _step("do", MINTS["bot-pr"][0])["with"]
        assert {k: v for k, v in given.items() if k.startswith("permission-")} == {
            "permission-contents": "write", "permission-pull-requests": "write"}

    def test_the_script_mint_asks_for_contents_and_pull_requests_read(self):
        given = _step("do", MINTS["script"][0])["with"]
        assert {k: v for k, v in given.items() if k.startswith("permission-")} == {
            "permission-contents": "read", "permission-pull-requests": "read"}

    def test_no_step_asks_for_variables_or_actions(self):
        for job in _doc()["jobs"].values():
            for step in job["steps"]:
                for key in (step.get("with") or {}):
                    assert key not in ("permission-variables", "permission-actions"), \
                        (step.get("name"), key)

    def test_the_app_token_inputs_suite_passes_with_no_new_exception(self):
        sys.path.insert(0, str(ROOT / "tests"))
        import test_app_token_inputs as inputs
        assert set(inputs.EXCEPTIONS) == {
            ("proof-task.yml", "Mint the read-only proof identity", "permission-variables")}
        done = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                               str(ROOT / "tests" / "test_app_token_inputs.py")],
                              cwd=ROOT, capture_output=True, text=True)
        assert done.returncode == 0, done.stdout[-3000:]


class TestTheRun:
    @pytest.fixture
    def step(self):
        return _step("do", RUN_STEP)

    def test_it_runs_only_after_a_claim_that_exited_0_on_a_live_leg(self, step):
        assert _evaluate(step["if"], _live())
        assert not _evaluate(step["if"], _live(**{"steps.claim.outputs.claimed": ""}))
        assert not _evaluate(step["if"], _live(**{"steps.check.outputs.ok": "false"}))
        assert not _evaluate(step["if"], _live(**{DRY: "true", "steps.check.outputs.ok": "false"}))

    def test_a_live_leg_runs_the_plan_with_no_dry_run_flag(self, step, tmp_path):
        env = _plain_env(step, tmp_path)
        env["DO_DRY_RUN"] = "false"
        result, _o, argvs = _bash(step["run"], env, tmp_path)
        assert result.returncode == 0, result.stderr
        assert argvs == [[f"{tmp_path}/pipeline-trusted/scripts/operator_do.py", "run", "DRE-1",
                          "--plan", f"{tmp_path}/operator-do/DRE-1.plan.json",
                          "--out", f"{tmp_path}/operator-do-run/transcript.json"]]

    def test_it_holds_the_one_credential_the_kind_names(self, step):
        env = step["env"]
        assert env["GH_TOKEN"] == STEP_TOKEN
        assert env["LINEAR_API_KEY"] == (
            "${{ needs.decide.outputs.do_dry_run == 'false' && "
            "steps.check.outputs.kind == 'linear-admin' && "
            "(secrets.LINEAR_PLANNER_KEY || secrets.LINEAR_API_KEY) || '' }}")
        assert _secrets(step) == {"LINEAR_PLANNER_KEY", "LINEAR_API_KEY"}
        for kind in operator_do_ops.load()["kinds"]:
            assert operator_do_ops.credential(kind) in ("GH_TOKEN", "LINEAR_API_KEY")


class TestThePost:
    @pytest.fixture
    def step(self):
        return _step("do", POST_STEP)

    def test_it_runs_after_a_claim_that_exited_0_whatever_the_run_did(self, step):
        assert "!cancelled()" in step["if"]
        assert _evaluate(step["if"], _live())

    def test_it_runs_after_a_refusal_with_no_claim(self, step):
        assert _evaluate(step["if"], _live(**{"steps.check.outputs.ok": "false",
                                              "steps.claim.outputs.claimed": ""}))

    def test_it_never_runs_after_a_claim_that_did_not_exit_0(self, step):
        assert not _evaluate(step["if"], _live(**{"steps.claim.outputs.claimed": ""}))
        assert not _evaluate(step["if"], _live(**{"steps.check.outputs.ok": "",
                                                  "steps.claim.outputs.claimed": ""}))

    def test_it_posts_do_post_with_the_legs_files(self, step, tmp_path):
        assert step["env"]["LINEAR_API_KEY"] == LINEAR_KEY
        result, _o, argvs = _bash(step["run"], _plain_env(step, tmp_path), tmp_path)
        assert result.returncode == 0, result.stderr
        run = f"{tmp_path}/operator-do-run"
        assert argvs == [[f"{tmp_path}/pipeline-trusted/scripts/operator_agent_apply.py",
                          "do-post", "DRE-1", "--decisions", f"{run}/decisions.json",
                          "--transcript", f"{run}/transcript.json",
                          "--plan", f"{tmp_path}/operator-do/DRE-1.plan.json"]]

    def test_a_refused_post_fails_the_leg(self, step, tmp_path):
        for rc in (1, 3, 4):
            result, _o, _a = _bash(step["run"], _plain_env(step, tmp_path), tmp_path, rc=rc)
            assert result.returncode == rc

    def test_the_check_writes_its_transcript_where_the_post_reads_it(self):
        check = _step("do", CHECK_STEP)["run"]
        assert '--transcript "$TRANSCRIPT"' in check
        for name in (CHECK_STEP, RUN_STEP, POST_STEP):
            assert _step("do", name)["env"]["TRANSCRIPT"] == f"{RUN_DIR}/transcript.json"


# --------------------------------------------------------------------------- #
# no AWS credential, and only the declared secrets                             #
# --------------------------------------------------------------------------- #


class TestNoAwsCredential:
    @pytest.mark.parametrize("job", ["do", "close"])
    def test_it_is_the_first_step(self, job):
        first = _job(job)["steps"][0]
        assert first["name"] == ENV_STEP
        assert first["run"] == _step("do", ENV_STEP)["run"]
        assert "env" not in first and "uses" not in first

    def test_an_aws_profile_in_reach_fails_the_leg(self, tmp_path):
        run = _step("do", ENV_STEP)["run"]
        result, _o, argvs = _bash(run, {"AWS_PROFILE": "default"}, tmp_path)
        assert result.returncode != 0
        assert ("operator-agent: do env — AWS credential in reach, refusing"
                in result.stdout.splitlines())
        assert "no AWS credential in reach" not in result.stdout
        assert "default" not in result.stdout + result.stderr
        assert argvs == []

    def test_no_aws_profile_passes(self, tmp_path):
        run = _step("do", ENV_STEP)["run"]
        result, _o, _a = _bash(run, {}, tmp_path)
        assert result.returncode == 0, result.stderr
        assert result.stdout.splitlines() == ["operator-agent: do env — no AWS credential in reach"]

    def test_any_aws_variable_or_an_aws_directory_fails_the_leg(self, tmp_path):
        run = _step("do", ENV_STEP)["run"]
        for name in ("AWS_ACCESS_KEY_ID", "AWS_ROLE_ARN", "AWS_WEB_IDENTITY_TOKEN_FILE", "AWS_"):
            result, _o, _a = _bash(run, {name: "x"}, tmp_path)
            assert result.returncode != 0, name
        (tmp_path / ".aws").mkdir()
        result, _o, _a = _bash(run, {}, tmp_path)
        assert result.returncode != 0
        assert "AWS credential in reach, refusing" in result.stdout


class TestWhatTheDoJobNeverHolds:
    def test_no_step_carries_an_aws_profile_variable_or_action(self):
        job = _job("do")
        assert "id-token" not in job["permissions"]
        for scope in [job.get("env") or {}] + [s.get("env") or {} for s in job["steps"]]:
            assert not [k for k in scope if k.startswith("AWS_")], scope
        body = json.dumps(job)
        assert "aws-actions/configure-aws-credentials" not in body
        assert "id-token" not in body
        assert not re.search(r"\bAWS_[A-Z_]+\s*:", _text().split("\n  do:\n", 1)[1]
                             .split("\n  close:\n", 1)[0])

    def test_no_job_configures_an_aws_credential(self):
        assert "aws-actions/configure-aws-credentials" not in _text()
        assert "role-to-assume" not in _text()

    def test_no_secret_outside_the_declared_four(self):
        assert _secrets(_job("do")) <= DO_SECRETS
        assert _secrets(_job("do")) == DO_SECRETS

    def test_the_claude_credential_is_on_the_model_step_alone(self):
        holders = [s["name"] for s in _job("do")["steps"]
                   if any(name in json.dumps(s) for name in CLAUDE_SECRETS)]
        assert holders == [PLAN_STEP]

    def test_the_minted_step_token_is_on_the_run_step_alone(self):
        holders = [s["name"] for s in _job("do")["steps"]
                   if "steps.do_token_" in json.dumps(s)]
        assert holders == [RUN_STEP]
        for job, body in _doc()["jobs"].items():
            if job != "do":
                assert "do_token_" not in json.dumps(body), job

    def test_the_linear_key_is_held_only_by_the_steps_that_read_or_write_linear(self):
        holders = [s["name"] for s in _job("do")["steps"]
                   if "LINEAR_API_KEY" in (s.get("env") or {})]
        assert holders == [READ_STEP, CLAIM_STEP, RUN_STEP, POST_STEP]


# --------------------------------------------------------------------------- #
# the close job                                                                #
# --------------------------------------------------------------------------- #


class TestTheCloseJob:
    def test_its_steps_in_order(self):
        assert [s.get("name") for s in _job("close")["steps"]] == list(CLOSE_STEPS)

    def test_it_runs_on_a_do_live_pass_with_a_card_to_close_even_with_do_skipped(self):
        job = _job("close")
        assert job["needs"] == ["decide", "do"]
        assert "!cancelled()" in job["if"]
        row = '[{"card":"DRE-1"}]'
        values = {DRY: "false", "needs.decide.outputs.close_cards": row}
        assert _evaluate(job["if"], values)
        for empty in ("[]", ""):
            assert not _evaluate(job["if"], {**values, "needs.decide.outputs.close_cards": empty})
        assert "needs.do" not in job["if"]
        assert "success()" not in job["if"]

    def test_its_token_reads_contents_and_mints_oidc(self):
        assert _job("close")["permissions"] == {"contents": "read", "id-token": "write"}

    def test_it_runs_on_the_short_runner_with_a_10_minute_ceiling(self):
        job = _job("close")
        assert job["timeout-minutes"] == 10
        assert "BUREAU_SHORT_RUNS_ON" in job["runs-on"]

    def test_the_pull_request_read_mint(self):
        step = _step("close", PR_MINT_STEP)
        assert step["uses"].split("@")[0] == MINT_ACTION
        assert step["id"] == "pr_read"
        assert step["with"] == {
            "app-id": "${{ secrets.BUREAU_APP_ID }}",
            "private-key": "${{ secrets.BUREAU_APP_PRIVATE_KEY }}",
            "owner": "dreadnought-foundry",
            "permission-pull-requests": "read",
        }

    def test_the_close_step_holds_what_do_close_reads(self):
        env = _step("close", CLOSE_STEP)["env"]
        assert env["CARD"] == "${{ matrix.card }}"
        assert env["LINEAR_API_KEY"] == LINEAR_KEY
        assert env["GH_TOKEN"] == "${{ steps.pr_read.outputs.token }}"
        assert env["BUREAU_READ"] == "${{ vars.BUREAU_READ || 'off' }}"
        assert env["BUREAU_READ_URL"] == "${{ vars.BUREAU_READ_URL }}"
        assert env["BUREAU_READ_AUDIENCE"] == "${{ vars.BUREAU_READ_AUDIENCE }}"
        assert env["BUREAU_NOTICE_AUDIENCE"] == "${{ vars.BUREAU_NOTICE_AUDIENCE }}"

    def test_it_calls_only_do_close(self, tmp_path):
        step = _step("close", CLOSE_STEP)
        result, _o, argvs = _bash(step["run"], _plain_env(step, tmp_path), tmp_path)
        assert result.returncode == 0, result.stderr
        assert argvs == [["scripts/operator_agent_apply.py", "do-close", "DRE-1"]]
        result, _o, _a = _bash(step["run"], _plain_env(step, tmp_path), tmp_path, rc=4)
        assert result.returncode == 4

    def test_it_runs_no_step_of_a_card(self):
        body = json.dumps(_job("close"))
        assert ACTION not in body
        assert "operator_do.py" not in body
        assert "do_token_" not in body
        for step in _job("close")["steps"]:
            for key, value in (step.get("with") or {}).items():
                if key.startswith("permission-"):
                    assert value == "read", (step["name"], key)
            for line in str(step.get("run") or "").splitlines():
                if "python3" in line:
                    assert "operator_agent_apply.py do-close" in line, line
        assert "aws-actions" not in body
        assert not [k for s in _job("close")["steps"] for k in (s.get("env") or {})
                    if k.startswith("AWS_")]

    def test_its_secrets_are_the_linear_key_and_the_apps(self):
        assert _secrets(_job("close")) == {"LINEAR_PLANNER_KEY", "LINEAR_API_KEY",
                                           "BUREAU_APP_ID", "BUREAU_APP_PRIVATE_KEY"}


# --------------------------------------------------------------------------- #
# the credentials job                                                          #
# --------------------------------------------------------------------------- #


class TestTheCredentialsJob:
    def test_it_runs_every_pass_in_both_modes(self):
        job = _job("credentials")
        assert "if" not in job
        assert "needs" not in job

    def test_its_steps(self):
        assert [s.get("name") for s in _job("credentials")["steps"]] == list(CREDENTIAL_STEPS)

    def test_it_runs_on_the_short_runner_with_a_5_minute_ceiling(self):
        job = _job("credentials")
        assert job["timeout-minutes"] == 5
        assert "BUREAU_SHORT_RUNS_ON" in job["runs-on"]

    def test_it_holds_only_the_apps_id_and_key_and_the_linear_key(self, tmp_path):
        step = _step("credentials", PROBE_STEP)
        assert step["env"] == {
            "BUREAU_APP_ID": "${{ secrets.BUREAU_APP_ID }}",
            "BUREAU_APP_PRIVATE_KEY": "${{ secrets.BUREAU_APP_PRIVATE_KEY }}",
            "LINEAR_API_KEY": LINEAR_KEY,
        }
        assert _secrets(_job("credentials")) == {"BUREAU_APP_ID", "BUREAU_APP_PRIVATE_KEY",
                                                 "LINEAR_PLANNER_KEY", "LINEAR_API_KEY"}
        result, _o, argvs = _bash(step["run"], {}, tmp_path)
        assert result.returncode == 0, result.stderr
        assert argvs == [["scripts/operator_do.py", "probe"]]

    def test_it_runs_no_step_of_a_card(self):
        body = json.dumps(_job("credentials"))
        for word in (ACTION, "create-github-app-token", "operator_agent_apply", "matrix",
                     "id-token"):
            assert word not in body, word


# --------------------------------------------------------------------------- #
# the record: header, leave-alone, proof reach                                 #
# --------------------------------------------------------------------------- #


class TestTheRecord:
    def test_the_header_bounds_the_new_reads_and_the_rerun(self):
        header = _header()
        assert "DRE-8928" in header
        assert "OPERATOR_AGENT_DO_LIVE" in header
        q3 = header.split("Q3", 1)[1].split("Q4", 1)[0]
        assert "do-claim" in q3 and "exits 3" in q3
        q4 = header.split("Q4", 1)[1].split("Q5", 1)[0]
        for fact in ("3 card-and-thread reads", "two per project-status op",
                     "one in `credentials`", "10 card-and-thread reads", "read door"):
            assert fact in q4, fact

    def test_the_agent_leaves_its_switch_alone(self):
        rows = json.loads((ROOT / "config" / "operator-agent.json").read_text())["leave_alone"]
        assert [r["card"] for r in rows] == ["DRE-8748", "DRE-8830", "DRE-8927", "DRE-9053"]
        assert "OPERATOR_AGENT_DO_LIVE" in rows[2]["why"]

    def test_the_proof_can_read_what_the_new_jobs_print(self):
        doc = json.loads((ROOT / "config" / "proof-reach.json").read_text())
        rows = doc["log_lines"]
        row = next(r for r in rows if r["tag"] == "operator-agent:")
        assert rows[-1] is row
        when = row["when"]
        assert when.startswith("every Operator Agent run (DRE-8744)")
        for fact in ("`do` job", "`close` job", "`credentials` job", "would run:", "ran:",
                     "do refused", "closed", "close waits", "nothing owed", "write refused",
                     "credential lines", "DRE-8928"):
            assert fact in when, fact

    def test_nothing_added_names_a_person_an_email_or_a_sign_in(self):
        added = [_header(), json.dumps(_job("do")), json.dumps(_job("close")),
                 json.dumps(_job("credentials"))]
        rows = json.loads((ROOT / "config" / "switches.json").read_text())["switches"]
        added.append(next(r["means"] for r in rows if r["name"] == SWITCH))
        for text in added:
            assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text)
            for word in ("Sid", "sign in", "sign-in", "signin", "log in", "login"):
                assert not re.search(rf"\b{re.escape(word)}\b", text, re.IGNORECASE), word
