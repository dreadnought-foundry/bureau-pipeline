"""RED-first tests for DRE-6624 — the model config change workflow.

`.github/workflows/model-config-change.yml` is what the console's Save changes
dispatches. Given one change set it applies the changes to
`config/models.yaml`, regenerates every generated file, validates, and opens ONE
pull request as the bureau App — the way `model-adoption.yml` opens its own:

  * a change set the validator refuses fails the run with each reason, before
    any branch exists, so no branch and no pull request is ever made;
  * a change set whose pull request is already open is found by its branch and
    opens nothing — a re-run of Save changes is a no-op;
  * the branch is an `agent/` branch named for the change set, the commit
    carries only the four model-config files, and the body opens with the
    `What's new:` line the merge gate reads — the record the gates need.

Some of these execute the workflow's own `run:` blocks against a fake `gh`, the
discipline `tests/test_model_adoption_workflow.py` applies.

Run: cd bureau-pipeline && python3 -m pytest tests/test_model_config_change_workflow.py -v
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
WORKFLOW = WORKFLOWS / "model-config-change.yml"
ADOPTION = WORKFLOWS / "model-adoption.yml"
MEDIC_STUB = WORKFLOWS / "self-medic.yml"

sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import model_config_change as mcc  # noqa: E402
import model_fixture  # noqa: E402
import whats_new  # noqa: E402

NAME = "Model config change"
CONFIG_FILES = ("config/models.yaml", "agents.yaml", "scripts/model_fallback.py",
                "config/model-choices.json")


def _doc() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())


def _text() -> str:
    return WORKFLOW.read_text()


def _uncommented() -> str:
    return "\n".join(line for line in _text().splitlines()
                     if not line.lstrip().startswith("#"))


def _on(doc: dict) -> dict:
    on = doc.get("on", doc.get(True))
    return on if isinstance(on, dict) else {}


def _job() -> dict:
    jobs = _doc().get("jobs") or {}
    assert list(jobs) == ["change"], "one job: the change set is one unit of work"
    return jobs["change"]


def _steps() -> list[dict]:
    return [s for s in _job().get("steps") or [] if isinstance(s, dict)]


def _step(step_id: str) -> dict:
    for step in _steps():
        if step.get("id") == step_id:
            return step
    raise AssertionError(f"no step with id {step_id!r}")


def _index(needle: str) -> int:
    for at, step in enumerate(_steps()):
        if needle in (step.get("run") or ""):
            return at
    raise AssertionError(f"no step runs {needle!r}")


def _write_fake_gh(tmp_path: Path, url: str) -> Path:
    """A `gh` that answers `pr list --jq` with `url` (empty: none open)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    gh = bin_dir / "gh"
    gh.write_text(f"#!/usr/bin/env bash\nprintf '%s\\n' '{url}'\n")
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    return bin_dir


def _run_step(tmp_path: Path, step: dict, env_extra: dict | None = None,
              open_url: str | None = None):
    script = tmp_path / "step.sh"
    script.write_text("set -eo pipefail\n" + step["run"])
    out = tmp_path / "github_output"
    out.write_text("")
    summary = tmp_path / "step_summary"
    summary.write_text("")
    env = {k: v for k, v in os.environ.items()
           if k not in ("GITHUB_OUTPUT", "GITHUB_STEP_SUMMARY")}
    env.update({
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_OUTPUT": str(out),
        "GITHUB_STEP_SUMMARY": str(summary),
        "GITHUB_REPOSITORY": "dreadnought-foundry/bureau-pipeline",
    })
    if open_url is not None:
        env["PATH"] = f"{_write_fake_gh(tmp_path, open_url)}{os.pathsep}{env['PATH']}"
    env.update(env_extra or {})
    proc = subprocess.run(["bash", str(script)], cwd=ROOT, env=env,
                          capture_output=True, text=True)
    outputs = dict(line.split("=", 1)
                   for line in out.read_text().splitlines() if "=" in line)
    return proc, outputs, summary.read_text()


def _change_set(*changes: dict) -> str:
    return json.dumps({"requested_by": "ceo@example.com", "changes": list(changes)})


VALID = {"op": "move_agent", "agent": "proof", "group": "advisory"}
REFUSED = {"op": "move_agent", "agent": "engineer", "group": "judgement"}


# --------------------------------------------------------------------------- #
# 1. Trigger, input, concurrency                                               #
# --------------------------------------------------------------------------- #

def test_the_workflow_is_named_model_config_change():
    assert _doc().get("name") == NAME


def test_it_is_dispatched_only_with_one_required_change_set_input():
    on = _on(_doc())
    assert list(on) == ["workflow_dispatch"], "the console dispatches it; nothing else"
    inputs = on["workflow_dispatch"].get("inputs") or {}
    assert list(inputs) == ["change_set"]
    assert inputs["change_set"].get("required") is True
    assert inputs["change_set"].get("type") == "string"


def test_one_run_at_a_time_and_a_queued_one_waits():
    concurrency = _doc().get("concurrency")
    assert concurrency == {"group": "model-config-change", "cancel-in-progress": False}


def test_its_own_token_only_reads():
    assert _doc().get("permissions") == {"contents": "read", "pull-requests": "read"}


def test_the_job_is_capped_at_30_minutes():
    assert _job().get("timeout-minutes") == 30


# --------------------------------------------------------------------------- #
# 2. The change set arrives through env, never inside a run block (DRE-3484)   #
# --------------------------------------------------------------------------- #

def test_no_expression_inside_a_run_block():
    for step in _steps():
        assert "${{" not in (step.get("run") or ""), step.get("name")


def test_the_change_set_arrives_through_env():
    read = _step("read")
    assert read["env"]["CHANGE_SET"] == "${{ inputs.change_set }}"
    assert '"$CHANGE_SET"' in read["run"]
    assert _uncommented().count("inputs.change_set") == 1, (
        "the input is read once, into env, and travels as a file after that")


def test_the_read_step_names_the_branch_from_the_module(tmp_path):
    change_set = _change_set(VALID)
    proc, out, _ = _run_step(tmp_path, _step("read"), {"CHANGE_SET": change_set})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert out["branch"] == mcc.branch_name(json.loads(change_set))
    assert json.loads((tmp_path / "change-set.json").read_text()) == json.loads(change_set)


def test_a_change_set_that_is_not_json_fails_the_read(tmp_path):
    proc, out, _ = _run_step(tmp_path, _step("read"), {"CHANGE_SET": "{nope"})
    assert proc.returncode != 0
    assert "branch" not in out


# --------------------------------------------------------------------------- #
# 3. A refused change set: the run fails with each reason, no branch, no PR    #
# --------------------------------------------------------------------------- #

def test_the_validation_runs_before_any_branch_push_or_pull_request():
    validate = _index("model_config_change.py apply --changes")
    assert "--check" in _steps()[validate]["run"]
    for later in ("git checkout -B", "git push", "gh pr create"):
        assert validate < _index(later), later


def test_no_step_runs_after_a_failure():
    """A red validation must stop the run: nothing after it may opt back in."""
    for step in _steps():
        cond = str(step.get("if") or "")
        for opt_in in ("always()", "failure()", "!cancelled()", "cancelled()"):
            assert opt_in not in cond, step.get("name")


def test_a_refused_change_set_fails_validation_with_each_plain_reason(tmp_path):
    before = (ROOT / "config/models.yaml").read_text()
    (tmp_path / "change-set.json").write_text(_change_set(VALID, REFUSED))
    proc, _, summary = _run_step(tmp_path, _step("validate"))
    assert proc.returncode != 0
    assert "change 2" in proc.stdout and "agents.engineer" in proc.stdout
    assert "change 1" not in proc.stdout
    assert "agents.engineer" in summary, "the reason reaches the run's summary page"
    assert (ROOT / "config/models.yaml").read_text() == before


def test_a_valid_change_set_passes_validation_and_writes_nothing(tmp_path):
    before = (ROOT / "config/models.yaml").read_text()
    (tmp_path / "change-set.json").write_text(_change_set(VALID))
    proc, _, _ = _run_step(tmp_path, _step("validate"))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (ROOT / "config/models.yaml").read_text() == before


# --------------------------------------------------------------------------- #
# 4. One pull request: an open one is found and nothing is opened twice        #
# --------------------------------------------------------------------------- #

def test_an_open_pull_request_for_the_change_set_is_found(tmp_path):
    url = "https://github.com/dreadnought-foundry/bureau-pipeline/pull/999"
    proc, out, summary = _run_step(tmp_path, _step("open"),
                                   {"BRANCH": "agent/model-config-abc", "GH_TOKEN": "x"},
                                   open_url=url)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert out["existing"] == url
    assert url in summary


def test_no_open_pull_request_leaves_the_rail_free(tmp_path):
    proc, out, _ = _run_step(tmp_path, _step("open"),
                             {"BRANCH": "agent/model-config-abc", "GH_TOKEN": "x"},
                             open_url="")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert out["existing"] == ""


def test_the_open_check_reads_the_branch_the_read_step_named():
    step = _step("open")
    assert step["env"]["BRANCH"] == "${{ steps.read.outputs.branch }}"
    assert 'gh pr list --repo "$GITHUB_REPOSITORY" --head "$BRANCH" --state open' in step["run"]
    assert _index("--head \"$BRANCH\" --state open") < _index("model_config_change.py apply")


def test_every_step_after_the_open_check_is_skipped_when_one_is_open():
    after = _steps()[[s.get("id") for s in _steps()].index("open") + 1:]
    assert after
    for step in after:
        assert "steps.open.outputs.existing == ''" in str(step.get("if") or ""), step.get("name")


def test_the_pr_step_checks_again_before_creating():
    run = _steps()[_index("gh pr create")]["run"]
    assert run.index("gh pr list") < run.index("gh pr create")


# --------------------------------------------------------------------------- #
# 5. The edit, the generated files, the validation                             #
# --------------------------------------------------------------------------- #

def test_the_work_runs_in_order():
    order = [
        "model_config_change.py apply --changes",           # --check, validate
        "git checkout -B",
        'model_config_change.py apply --changes "$RUNNER_TEMP/change-set.json"\n',
        "python3 scripts/sync_model_config.py\n",
        "python3 scripts/sync_model_config.py --check",
        "git push",
        "gh pr create",
    ]
    runs = "\n".join((s.get("run") or "") + "\n" for s in _steps())
    positions = [runs.index(needle) for needle in order]
    assert positions == sorted(positions)


def test_the_branch_is_cut_from_origin_main_or_reused_after_a_crash():
    step = _step("branch")
    run = step["run"]
    assert step["env"]["BRANCH"] == "${{ steps.read.outputs.branch }}"
    assert 'git checkout -B "$BRANCH" origin/main' in run
    assert 'git ls-remote --exit-code --heads origin "$BRANCH"' in run
    assert "reuse=true" in run
    for step in _steps():
        run = step.get("run") or ""
        writes = (("model_config_change.py apply" in run and "--check" not in run)
                  or "sync_model_config.py" in run or "git push" in run)
        if writes:
            assert "steps.branch.outputs.reuse != 'true'" in step["if"], step["name"]


def test_the_model_tests_run_and_their_result_reaches_the_pull_request():
    step = _step("tests")
    assert "python3 -m pytest tests/test_model_policy.py tests/test_model_config.py" in step["run"]
    assert "tests/test_model_choices.py" in step["run"]
    assert "result=" in step["run"]
    pr = _steps()[_index("gh pr create")]
    assert pr["env"]["TESTS"] == "${{ steps.tests.outputs.result }}"


#: What the workflow says of the model tests (DRE-6865): no test pins a choice
#: the console can change, so a red result is a defect, never the price of a
#: change. Compared with backticks, comment marks and line breaks taken out.
NO_PIN = (
    "No model test pins a choice the console can change: a rule is tested against "
    "the frozen fixture at tests/fixtures/model-config-frozen/ "
    "(model_fixture.FIXTURE_DIR), and "
    "tests/test_console_change_keeps_the_suite_green.py is written to prove every "
    "operation leaves every model-reading test green (its per-case runs skip in "
    "the unit parts and run in the console change keeps the suite green job of "
    "Pipeline Tests) — so a red result on a change pull "
    "request is a defect in the change or in a test, never the expected cost of "
    "a change the console allows."
)
#: What it said before: that a legitimate change could turn the tests red, and
#: that the critic and the fix agent owned that repair.
OLD_PHRASES = ("legitimate change", "the fix agent own")


def _prose(text: str) -> str:
    lines = [re.sub(r"^\s*#\s?", "", line) for line in text.splitlines()]
    return " ".join(" ".join(lines).replace("`", "").split())


def _header() -> str:
    lines = _text().splitlines()
    return "\n".join(lines[:next(n for n, line in enumerate(lines)
                                 if line.startswith("name:"))])


def _comment_above(step_name: str) -> str:
    lines = _text().splitlines()
    at = next(n for n, line in enumerate(lines)
              if line.strip() == f"- name: {step_name}")
    above = []
    for line in reversed(lines[:at]):
        if not line.lstrip().startswith("#"):
            break
        above.insert(0, line)
    return "\n".join(above)


def _pr_body(tmp_path: Path) -> str:
    """The body the pull request step writes, run as written."""
    (tmp_path / "change-set.json").write_text(_change_set(VALID))
    shutil.copy(ROOT / "config" / "models.yaml", tmp_path / "models-before.yaml")
    proc, _, _ = _run_step(tmp_path, _steps()[_index("gh pr create")],
                           {"BRANCH": "agent/model-config-x", "TESTS": "failed",
                            "GH_TOKEN": "fake-token"}, open_url="")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return (tmp_path / "pr-body.md").read_text()


def test_the_no_pin_sentence_names_the_fixture_and_the_harness_that_exist():
    rel = model_fixture.FIXTURE_DIR.relative_to(ROOT).as_posix() + "/"
    assert rel in NO_PIN
    assert (ROOT / "tests" / "test_console_change_keeps_the_suite_green.py").is_file()


def test_the_no_pin_sentence_says_where_the_per_case_runs_run():
    """The per-case runs skip in the unit parts and run in DRE-6873's job, so
    the sentence says where they run — and names the job the harness's own
    skip reason names."""
    import test_console_change_keeps_the_suite_green as harness
    job = re.fullmatch(r'runs in the "(.+)" job', harness.SKIP_REASON).group(1)
    assert f"run in the {job} job of Pipeline Tests" in NO_PIN
    assert " proves " not in NO_PIN


def test_the_header_says_no_model_test_pins_a_console_choice():
    header = _prose(_header())
    assert NO_PIN in header
    for phrase in OLD_PHRASES:
        assert phrase not in header, phrase


def test_the_model_test_step_comment_says_so_too():
    comment = _prose(_comment_above("Run the model tests (informational)"))
    assert "INFORMATIONAL" in comment
    assert NO_PIN in comment
    for phrase in OLD_PHRASES:
        assert phrase not in comment, phrase


def test_the_pull_request_body_says_so_under_model_tests(tmp_path):
    body = _pr_body(tmp_path)
    section = body[body.index("## Model tests"):]
    assert "on this branch: **failed**." in section
    assert NO_PIN in _prose(section)
    for phrase in OLD_PHRASES:
        assert phrase not in body, phrase


def test_the_old_wording_is_gone_from_the_workflow():
    text = _prose(_text())
    left = [phrase for phrase in OLD_PHRASES if phrase in text]
    assert left == [], f"{WORKFLOW.name} still says {left}"


def test_the_commit_touches_only_the_model_config():
    run = _steps()[_index("git push")]["run"]
    assert "git add -- " + " ".join(CONFIG_FILES) in run
    assert "git add ." not in run and "git add -A" not in run
    assert "|".join(CONFIG_FILES) + ") ;;" in run
    assert 'user.name="agent-bureau-bot[bot]"' in run


def test_nothing_pushes_to_main():
    text = _uncommented()
    assert "HEAD:main" not in text
    assert not re.search(r"git\s+push[^\n]*\bmain\b", text)
    assert 'git push origin "HEAD:refs/heads/$BRANCH"' in _steps()[_index("git push")]["run"]


# --------------------------------------------------------------------------- #
# 6. Reaches the merge gate the way model-adoption.yml's pull requests do      #
# --------------------------------------------------------------------------- #

def test_the_pr_is_made_with_the_app_token():
    app = _steps()[0]
    assert app.get("id") == "app", "the App token is minted first"
    adoption = yaml.safe_load(ADOPTION.read_text())["jobs"]["adopt"]["steps"][0]
    assert app["uses"] == adoption["uses"], "the same pinned action as the adoption"
    assert app["with"] == adoption["with"]
    checkout = next(s for s in _steps() if str(s.get("uses", "")).startswith("actions/checkout@"))
    assert checkout["with"]["token"] == "${{ steps.app.outputs.token }}"
    assert _steps()[_index("gh pr create")]["env"]["GH_TOKEN"] == "${{ steps.app.outputs.token }}"


def test_the_branch_is_an_agent_branch_that_owes_the_whats_new_line():
    branch = mcc.branch_name({"requested_by": "x", "changes": [VALID]})
    assert branch.startswith("agent/")
    assert whats_new.required_for(branch)


def test_the_pr_body_opens_with_a_whats_new_line_the_gate_accepts():
    body = mcc.pr_body({"requested_by": "x", "changes": [VALID]},
                       yaml.safe_load((ROOT / "config/models.yaml").read_text()))
    assert body.splitlines()[0] == "What's new: none"


def test_the_pr_title_and_body_come_from_render():
    run = _steps()[_index("gh pr create")]["run"]
    assert ('--title "$(python3 scripts/model_config_change.py render pr-title '
            '--changes "$RUNNER_TEMP/change-set.json")"') in run
    assert ('python3 scripts/model_config_change.py render pr-body '
            '--changes "$RUNNER_TEMP/change-set.json"') in run
    assert '--body-file "$RUNNER_TEMP/pr-body.md"' in run
    assert "--base main" in run


def test_the_medic_watches_the_workflow():
    watched = _on(yaml.safe_load(MEDIC_STUB.read_text()))["workflow_run"]["workflows"]
    assert NAME in watched


def test_python_tooling_is_installed_through_the_shared_cached_action():
    assert any(s.get("uses") == "./.github/actions/setup-python-cached" and s.get("name")
               for s in _steps())


# --------------------------------------------------------------------------- #
# 7. No new act and no receipt (DRE-6582)                                      #
# --------------------------------------------------------------------------- #

def test_the_workflow_posts_no_comment_and_declares_no_act():
    text = _uncommented()
    for writer in ("gh pr comment", "gh issue comment", "pipeline_act", "linear_ops",
                   "LINEAR_API_KEY"):
        assert writer not in text, writer
    acts = json.loads((ROOT / "config" / "pipeline-acts.json").read_text())
    assert "model-config-change" not in json.dumps(acts)


# --------------------------------------------------------------------------- #
# 8. The whole run, end to end, against a throwaway remote                     #
# --------------------------------------------------------------------------- #
#
# The steps above are read one at a time. This runs the job's steps IN ORDER,
# the way the runner does: each `if:` is evaluated against the outputs the
# earlier steps wrote, every `${{ }}` in an `env:` is resolved, the first step
# that exits non-zero ends the run, and a `uses:` step (the App token, the
# checkout, the Python install) is the one thing stood in for — the tree is
# already checked out and the token is a fake. `git push` reaches a real bare
# repository, and `gh` is a fake that records what it was asked to create and
# then answers that the pull request is open, as GitHub would. The model tests
# step is informational and is stood in for too, so the scenario stays fast.

_EXPR = re.compile(r"\$\{\{\s*(.+?)\s*\}\}")
_CLAUSE = re.compile(r"^steps\.([\w-]+)\.outputs\.([\w-]+)\s*(==|!=)\s*'([^']*)'$")

FAKE_GH = """#!/usr/bin/env bash
echo "$*" >> "$FAKE_GH_LOG"
if [ "$1 $2" = "pr list" ]; then
  cat "$FAKE_GH_OPEN" 2>/dev/null || true
  exit 0
fi
if [ "$1 $2" = "pr create" ]; then
  while [ $# -gt 0 ]; do
    if [ "$1" = "--head" ]; then echo "https://github.com/o/r/pull/1" > "$FAKE_GH_OPEN"; fi
    if [ "$1" = "--body-file" ]; then cp "$2" "$FAKE_GH_BODY"; fi
    shift
  done
  exit 0
fi
exit 1
"""


def _resolve(value: str, inputs: dict, outputs: dict) -> str:
    def one(match):
        expr = match.group(1)
        if expr.startswith("inputs."):
            return inputs.get(expr[len("inputs."):], "")
        parts = expr.split(".")
        if parts[0] == "steps" and len(parts) == 4:
            return outputs.get(parts[1], {}).get(parts[3], "")
        if expr in ("github.token", "steps.app.outputs.token"):
            return "fake-token"
        raise AssertionError(f"the harness cannot resolve ${{{{ {expr} }}}}")
    return _EXPR.sub(one, str(value))


def _passes(condition, outputs: dict) -> bool:
    if not condition:
        return True
    for clause in str(condition).split("&&"):
        found = _CLAUSE.match(clause.strip())
        assert found, f"the harness cannot evaluate `{clause.strip()}`"
        step, name, op, literal = found.groups()
        value = outputs.get(step, {}).get(name, "")
        if (value == literal) != (op == "=="):
            return False
    return True


class _Run:
    def __init__(self, tmp_path: Path):
        self.tmp = tmp_path
        self.remote = tmp_path / "remote.git"
        self.work = tmp_path / "work"
        self.bin = tmp_path / "bin"
        self.state = tmp_path / "gh"
        for path in (self.work, self.bin, self.state):
            path.mkdir()
        ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
        shutil.copytree(ROOT / "scripts", self.work / "scripts", ignore=ignore)
        shutil.copytree(ROOT / "config", self.work / "config", ignore=ignore)
        shutil.copy(ROOT / "agents.yaml", self.work / "agents.yaml")
        gh = self.bin / "gh"
        gh.write_text(FAKE_GH)
        gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
        self.git("init", "-q", "-b", "main")
        self.git("add", "-A")
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "base")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.remote)],
                       check=True)
        self.git("remote", "add", "origin", str(self.remote))
        self.git("push", "-q", "origin", "main")
        self.base = self.git("rev-parse", "HEAD").strip()

    def git(self, *args: str, cwd: Path | None = None) -> str:
        return subprocess.run(["git", *args], cwd=cwd or self.work, check=True,
                              capture_output=True, text=True).stdout

    def remote_branches(self) -> list[str]:
        out = subprocess.run(["git", "for-each-ref", "--format=%(refname:short)",
                              "refs/heads"], cwd=self.remote, check=True,
                             capture_output=True, text=True).stdout
        return sorted(out.split())

    def gh_calls(self) -> list[str]:
        log = self.state / "log"
        return log.read_text().splitlines() if log.exists() else []

    def dispatch(self, change_set: str, skip=("tests",)):
        """One run of the job. Returns (ran step names, the failed step or None,
        the combined output)."""
        # A fresh runner: the checkout is main as the remote has it.
        self.git("checkout", "-q", "-f", "main")
        self.git("reset", "-q", "--hard", "origin/main")
        temp = self.tmp / f"runner-{len(list(self.tmp.glob('runner-*')))}"
        temp.mkdir()
        outputs: dict[str, dict] = {}
        ran, log = [], []
        for n, step in enumerate(_steps()):
            sid = step.get("id") or f"step-{n}"
            if not _passes(step.get("if"), outputs):
                continue
            if "uses" in step or sid in skip:
                outputs[sid] = {"result": "skipped"} if sid in skip else {}
                continue
            out_file = temp / f"output-{n}"
            out_file.write_text("")
            env = {k: v for k, v in os.environ.items()
                   if k not in ("GITHUB_OUTPUT", "GITHUB_STEP_SUMMARY")}
            env.update({
                "RUNNER_TEMP": str(temp),
                "GITHUB_OUTPUT": str(out_file),
                "GITHUB_STEP_SUMMARY": str(temp / "summary"),
                "GITHUB_REPOSITORY": "dreadnought-foundry/bureau-pipeline",
                "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
                "FAKE_GH_LOG": str(self.state / "log"),
                "FAKE_GH_OPEN": str(self.state / "open"),
                "FAKE_GH_BODY": str(self.state / "body.md"),
            })
            for key, value in (step.get("env") or {}).items():
                env[key] = _resolve(value, {"change_set": change_set}, outputs)
            proc = subprocess.run(["bash", "-e", "-c", step["run"]], cwd=self.work,
                                  env=env, capture_output=True, text=True)
            ran.append(step.get("name"))
            log.append(proc.stdout + proc.stderr)
            outputs[sid] = dict(line.split("=", 1)
                                for line in out_file.read_text().splitlines()
                                if "=" in line)
            if proc.returncode != 0:
                return ran, step.get("name"), "\n".join(log)
        return ran, None, "\n".join(log)


def test_a_refused_change_set_ends_the_run_with_no_branch_and_no_pull_request(tmp_path):
    run = _Run(tmp_path)
    ran, failed, log = run.dispatch(_change_set(VALID, REFUSED))
    assert failed == "Validate the change set", log
    assert "change 2 of 2 (move_agent engineer judgement): agents.engineer" in log
    assert run.remote_branches() == ["main"], "no branch was pushed"
    assert not any(call.startswith("pr create") for call in run.gh_calls())
    assert run.git("rev-parse", "origin/main").strip() == run.base


def _another_builders_choice() -> str:
    """A Builders model the console offers that is not today's default, read
    off the committed `config/model-choices.json` — the scenario runs the
    workflow against today's files, so it can name no literal model."""
    choices = json.loads((ROOT / "config" / "model-choices.json").read_text())
    group = next(g for g in choices["groups"] if g["key"] == "workhorse")
    return next(c["model"] for c in group["choices"]
                if c["allowed"] and c["model"] != group["default_model"])


def test_a_valid_change_set_opens_one_pull_request_and_a_rerun_opens_none(tmp_path):
    run = _Run(tmp_path)
    model = _another_builders_choice()
    change_set = _change_set(
        VALID, {"op": "set_default_model", "group": "workhorse", "model": model})
    branch = mcc.branch_name(json.loads(change_set))

    ran, failed, log = run.dispatch(change_set)
    assert failed is None, log
    assert run.remote_branches() == sorted(["main", branch])
    changed = run.git("diff", "--name-only", f"origin/main...origin/{branch}").split()
    assert sorted(changed) == sorted(CONFIG_FILES), "the edit and every generated file"
    run.git("checkout", "-q", f"origin/{branch}")
    check = subprocess.run([sys.executable, "scripts/sync_model_config.py", "--check"],
                           cwd=run.work, capture_output=True, text=True)
    assert check.returncode == 0, check.stdout + check.stderr
    models = yaml.safe_load((run.work / "config/models.yaml").read_text())
    assert models["agents"][VALID["agent"]] == VALID["group"]
    assert models["ladders"]["workhorse"][0]["model"] == model
    creates = [call for call in run.gh_calls() if call.startswith("pr create")]
    assert len(creates) == 1
    assert f"--head {branch}" in creates[0] and "--base main" in creates[0]
    body = (run.state / "body.md").read_text()
    assert body.splitlines()[0] == "What's new: none"
    assert "ceo@example.com" in body
    assert sum(line.startswith("- ") for line in body.splitlines()) == 2

    ran, failed, log = run.dispatch(change_set)
    assert failed is None, log
    assert "already open as https://github.com/o/r/pull/1" in log
    assert ran == ["Read the change set and name its branch", "Is its pull request already open"]
    assert len([c for c in run.gh_calls() if c.startswith("pr create")]) == 1


def test_a_run_that_crashed_after_its_push_is_finished_on_the_same_branch(tmp_path):
    run = _Run(tmp_path)
    change_set = _change_set(VALID)
    branch = mcc.branch_name(json.loads(change_set))
    run.dispatch(change_set)
    pushed = run.git("rev-parse", f"origin/{branch}").strip()
    # The pull request never opened: GitHub has no record of it.
    (run.state / "open").unlink()
    (run.state / "log").unlink()

    ran, failed, log = run.dispatch(change_set)
    assert failed is None, log
    assert "reusing it as it stands" in log
    assert run.git("rev-parse", f"origin/{branch}").strip() == pushed, "nothing re-cut"
    assert len([c for c in run.gh_calls() if c.startswith("pr create")]) == 1
