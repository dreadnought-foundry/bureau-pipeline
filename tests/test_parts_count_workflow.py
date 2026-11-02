"""RED-first: a Parts Count workflow runs the one-time count in a listed repo,
and its merge runs it in agent-bureau (DRE-8844).

`.github/workflows/parts-count.yml` (`Parts Count`) runs on a push to `main`
that changes `config/parts-count.json`, and on `workflow_dispatch` with a
`repo` input. Its `select` job reads the file and names the repos to count —
every listed repo on a push, the one asked for on a dispatch. Its `count` job
runs once per repo: it mints a read-only App token for that one repository,
checks the repo out with no credential left on disk, checks this run's own
commit out at `.bureau-pipeline`, runs `parts_check.py count --repo <slug>
--json` (DRE-8841) and hands its output to `parts_cleanup.py file` (DRE-8843)
with the entry's labels, and `--owned-by` when the entry carries `owned_by`.

What this file holds shut:

  * `config/parts-count.json` — agent-bureau listed with the contract's labels
    and owner, every `repo` a key of `config/repo-map.json`, every entry
    carrying `repo`, `agent` and `initiative`, an `owned_by` a `DRE-<n>` id;
  * the triggers, the token's one repository and `contents: read`, the
    checkouts' `persist-credentials: false`, and the count before the filer;
  * the select, count and filing steps' own scripts, run as written: the push
    selects every listed repo and a dispatch only the one it names, the count
    is the real `parts_check.py`, and the filer is handed `--owned-by` exactly
    when the entry carries one;
  * the Linear key on the filing step's env only, `LINEAR_IDENTITY` declared,
    every third-party action pinned by a commit sha;
  * `Parts Count` watched by the Red-Main Repair caller and the medic.

Run: cd bureau-pipeline && python3 -m pytest tests/test_parts_count_workflow.py -q
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
WORKFLOW = ROOT / ".github" / "workflows" / "parts-count.yml"
CONFIG = ROOT / "config" / "parts-count.json"
REPO_MAP = ROOT / "config" / "repo-map.json"
REPAIR_STUB = ROOT / ".github" / "workflows" / "self-red-main-repair.yml"
MEDIC_STUB = ROOT / ".github" / "workflows" / "self-medic.yml"
sys.path.insert(0, str(ROOT / "scripts"))

import check_action_pins  # noqa: E402
import check_workflow_watchers  # noqa: E402
import parts_check  # noqa: E402
import parts_cleanup  # noqa: E402

NAME = "Parts Count"
CONFIG_PATH = "config/parts-count.json"
PIPELINE_PATH = ".bureau-pipeline"
MINT_ACTION = "actions/create-github-app-token"
CHECKOUT_ACTION = "actions/checkout"
COUNT_COMMAND = 'python3 .bureau-pipeline/scripts/parts_check.py count --repo "$SLUG" --json'
FILE_COMMAND = ('python3 .bureau-pipeline/scripts/parts_cleanup.py file --repo "$SLUG" '
                '--count-json "$COUNT_JSON" --agent "$AGENT" --initiative "$INITIATIVE"')
AGENT_BUREAU = {"repo": "agent-bureau", "agent": "agent:frontend",
                "initiative": "initiative:bureau", "owned_by": "DRE-8536"}
CARD_ID = re.compile(r"^DRE-[0-9]+$")
ENTRY_KEYS = {"repo", "agent", "initiative", "owned_by"}


# --------------------------------------------------------------------------
# reading the files
# --------------------------------------------------------------------------
def _load() -> dict:
    assert WORKFLOW.is_file(), f"missing workflow {WORKFLOW.name}"
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _on(doc: dict) -> dict:
    # PyYAML parses a bare `on:` key as the boolean True.
    on = doc.get("on", doc.get(True))
    assert isinstance(on, dict), on
    return on


def _config() -> dict:
    assert CONFIG.is_file(), f"missing {CONFIG_PATH}"
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def _repo_map() -> dict:
    return json.loads(REPO_MAP.read_text(encoding="utf-8"))


def _job(job_id: str) -> dict:
    jobs = _load()["jobs"]
    assert job_id in jobs, f"no `{job_id}` job: {sorted(jobs)}"
    return jobs[job_id]


def _steps(job_id: str) -> list:
    return _job(job_id)["steps"]


def _step_using(job_id: str, action: str, **with_) -> dict:
    found = [s for s in _steps(job_id)
             if str(s.get("uses", "")).split("@")[0] == action
             and all((s.get("with") or {}).get(k) == v for k, v in with_.items())]
    assert len(found) == 1, f"{job_id}: {len(found)} `{action}` step(s) matching {with_}"
    return found[0]


def _step_running(job_id: str, needle: str) -> dict:
    found = [s for s in _steps(job_id) if needle in str(s.get("run", ""))]
    assert len(found) == 1, f"{job_id}: {len(found)} step(s) running {needle!r}"
    return found[0]


def _select_step() -> dict:
    return _step_running("select", CONFIG_PATH)


def _count_step() -> dict:
    return _step_running("count", "parts_check.py count")


def _file_step() -> dict:
    return _step_running("count", "parts_cleanup.py file")


def _index(job_id: str, step: dict) -> int:
    return _steps(job_id).index(step)


def _watch_list(stub: Path) -> list:
    return _on(yaml.safe_load(stub.read_text(encoding="utf-8")))["workflow_run"]["workflows"]


# --------------------------------------------------------------------------
# config/parts-count.json
# --------------------------------------------------------------------------
def test_the_config_lists_agent_bureau_with_the_contracts_labels_and_owner():
    entries = {e["repo"]: e for e in _config()["repos"]}
    assert entries.get("agent-bureau") == AGENT_BUREAU


def test_the_config_carries_a_repos_list_and_nothing_else():
    config = _config()
    assert set(config) == {"repos"}
    assert isinstance(config["repos"], list) and config["repos"]


def test_every_entry_names_a_repo_the_repo_map_carries():
    keys = set(_repo_map())
    for entry in _config()["repos"]:
        assert entry.get("repo") in keys, (
            f"{entry.get('repo')!r} is not a key of config/repo-map.json")


def test_every_entry_carries_repo_agent_and_initiative_and_no_other_key():
    for entry in _config()["repos"]:
        for key in ("repo", "agent", "initiative"):
            assert isinstance(entry.get(key), str) and entry[key], (entry, key)
        assert set(entry) <= ENTRY_KEYS, f"unknown key(s): {set(entry) - ENTRY_KEYS}"
        assert entry["agent"].startswith(parts_cleanup.AGENT_PREFIX), entry
        assert entry["initiative"].startswith(parts_cleanup.INITIATIVE_PREFIX), entry


def test_an_owned_by_where_present_is_a_card_id():
    for entry in _config()["repos"]:
        if "owned_by" in entry:
            assert isinstance(entry["owned_by"], str) and CARD_ID.match(entry["owned_by"]), entry


def test_a_repo_is_listed_once():
    repos = [e["repo"] for e in _config()["repos"]]
    assert len(repos) == len(set(repos)), repos


# --------------------------------------------------------------------------
# the workflow's shape
# --------------------------------------------------------------------------
def test_it_is_named_parts_count():
    assert _load()["name"] == NAME


def test_it_runs_on_a_push_to_main_that_changes_the_config():
    push = _on(_load())["push"]
    assert push["branches"] == ["main"]
    assert CONFIG_PATH in push["paths"]


def test_it_runs_on_a_dispatch_with_a_required_repo_input():
    dispatch = _on(_load())["workflow_dispatch"]
    repo = dispatch["inputs"]["repo"]
    assert repo["type"] == "string"
    assert repo["required"] is True


def test_it_runs_on_nothing_else():
    assert set(_on(_load())) == {"push", "workflow_dispatch"}


def test_it_reads_contents_and_nothing_else():
    doc = _load()
    assert doc["permissions"] == {"contents": "read"}
    for job_id, job in doc["jobs"].items():
        assert "permissions" not in job, f"{job_id} widens the workflow's permissions"


def test_two_runs_never_file_at_once():
    concurrency = _load()["concurrency"]
    assert concurrency["group"] == "parts-count"
    assert concurrency["cancel-in-progress"] is False


def test_the_count_job_runs_once_per_selected_repo_one_at_a_time():
    job = _job("count")
    assert job["needs"] == "select"
    strategy = job["strategy"]
    assert strategy["matrix"]["include"] == "${{ fromJSON(needs.select.outputs.repos) }}"
    assert strategy["max-parallel"] == 1
    assert strategy["fail-fast"] is False
    assert _job("select")["outputs"]["repos"] == "${{ steps.select.outputs.repos }}"


def test_the_count_job_is_skipped_when_nothing_is_selected():
    assert "needs.select.outputs.repos != '[]'" in _job("count")["if"]


# --------------------------------------------------------------------------
# the token and the checkouts
# --------------------------------------------------------------------------
def test_the_token_is_minted_for_the_one_repository_with_contents_read():
    mint = _step_using("count", MINT_ACTION)
    with_ = mint["with"]
    assert with_["app-id"] == "${{ secrets.BUREAU_APP_ID }}"
    assert with_["private-key"] == "${{ secrets.BUREAU_APP_PRIVATE_KEY }}"
    assert with_["owner"] == "${{ matrix.owner }}"
    assert with_["repositories"] == "${{ matrix.name }}"
    permissions = {k: v for k, v in with_.items() if k.startswith("permission-")}
    assert permissions == {"permission-contents": "read"}


def test_the_repo_is_checked_out_on_that_token_with_no_credential_left():
    mint = _step_using("count", MINT_ACTION)
    checkout = _step_using("count", CHECKOUT_ACTION,
                           repository="${{ matrix.owner }}/${{ matrix.name }}")
    assert checkout["with"]["token"] == f"${{{{ steps.{mint['id']}.outputs.token }}}}"
    assert checkout["with"]["persist-credentials"] is False
    assert "path" not in checkout["with"], "the count reads the repo at the workspace root"
    assert _index("count", mint) < _index("count", checkout)


def test_the_pipeline_is_this_runs_own_checkout_beside_it():
    pipeline = _step_using("count", CHECKOUT_ACTION, path=PIPELINE_PATH)
    with_ = pipeline["with"]
    assert "repository" not in with_ and "ref" not in with_, (
        "the pipeline is the run's own commit — on the push, the merge commit")
    assert with_["persist-credentials"] is False
    # A checkout at the workspace root clears it, so the repo goes first.
    repo = _step_using("count", CHECKOUT_ACTION,
                       repository="${{ matrix.owner }}/${{ matrix.name }}")
    assert _index("count", repo) < _index("count", pipeline)


def test_the_count_runs_before_the_filer():
    pipeline = _step_using("count", CHECKOUT_ACTION, path=PIPELINE_PATH)
    assert _index("count", pipeline) < _index("count", _count_step()) < _index(
        "count", _file_step())


def test_nothing_runs_the_filer_past_a_failed_count():
    assert "continue-on-error" not in _count_step()
    assert "if" not in _file_step()
    assert "|| true" not in _count_step()["run"]


def test_the_steps_run_the_contracts_commands():
    assert COUNT_COMMAND in _count_step()["run"]
    assert FILE_COMMAND in _file_step()["run"]


def test_the_steps_read_the_entry_off_the_matrix():
    assert _count_step()["env"] == {"SLUG": "${{ matrix.repo }}"}
    env = dict(_file_step()["env"])
    env.pop("LINEAR_API_KEY")
    assert env == {"SLUG": "${{ matrix.repo }}", "AGENT": "${{ matrix.agent }}",
                   "INITIATIVE": "${{ matrix.initiative }}",
                   "OWNED_BY": "${{ matrix.owned_by }}"}
    assert _job("count")["env"]["COUNT_JSON"] == "${{ runner.temp }}/parts-count.json"


# --------------------------------------------------------------------------
# Linear, the pins
# --------------------------------------------------------------------------
def test_the_linear_key_is_on_the_filing_step_only():
    doc = _load()
    filing = _file_step()
    assert filing["env"]["LINEAR_API_KEY"] == "${{ secrets.LINEAR_API_KEY }}"
    assert "LINEAR_API_KEY" not in (doc.get("env") or {})
    for job_id, job in doc["jobs"].items():
        assert "LINEAR_API_KEY" not in (job.get("env") or {}), job_id
        for step in job["steps"]:
            if step == filing:
                continue
            assert "secrets.LINEAR" not in json.dumps(step), (
                f"{job_id}: {step.get('name')} holds a Linear key")
    assert WORKFLOW.read_text(encoding="utf-8").count("secrets.LINEAR") == 1


def test_it_declares_the_fleet_linear_identity():
    assert _job("count")["env"]["LINEAR_IDENTITY"] == "fleet"


def test_every_third_party_action_is_pinned_by_a_commit_sha():
    assert check_action_pins.check_file(WORKFLOW) == []
    third_party = [r for r in check_action_pins.references(WORKFLOW)
                   if not str(r).startswith("./")]
    assert third_party, "the checker read no `uses:` at all"


# --------------------------------------------------------------------------
# the watchers
# --------------------------------------------------------------------------
def test_the_red_main_repair_caller_watches_it():
    assert NAME in _watch_list(REPAIR_STUB)


def test_the_medic_watches_it():
    assert NAME in _watch_list(MEDIC_STUB)


def test_the_watcher_check_passes_with_it():
    violations, stats = check_workflow_watchers.check_dir()
    assert violations == []
    names = {wf.name for wf in check_workflow_watchers.load_workflows()}
    assert NAME in names


# --------------------------------------------------------------------------
# the steps' own scripts, run as written
# --------------------------------------------------------------------------
def _bash(script: str, cwd: Path, env: dict) -> subprocess.CompletedProcess:
    full = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/tmp"), **env}
    return subprocess.run(["bash", "-e", "-c", script], cwd=cwd, env=full,
                          capture_output=True, text=True, timeout=60)


def _outputs(path: Path) -> dict:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        out[key] = value
    return out


def _select(tmp_path: Path, event: str, requested: str = "",
            repos: list | None = None) -> tuple:
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "config" / "repo-map.json").write_text(
        REPO_MAP.read_text(encoding="utf-8"), encoding="utf-8")
    listed = repos if repos is not None else [
        AGENT_BUREAU,
        {"repo": "portico", "agent": "agent:frontend", "initiative": "initiative:portico"},
    ]
    (tmp_path / "config" / "parts-count.json").write_text(
        json.dumps({"repos": listed}), encoding="utf-8")
    output = tmp_path / "github_output"
    output.write_text("", encoding="utf-8")
    step = _select_step()
    assert step["env"] == {"EVENT_NAME": "${{ github.event_name }}",
                           "REQUESTED": "${{ inputs.repo }}"}
    done = _bash(step["run"], tmp_path, {"EVENT_NAME": event, "REQUESTED": requested,
                                         "GITHUB_OUTPUT": str(output)})
    rows = json.loads(_outputs(output)["repos"]) if done.returncode == 0 else None
    return done, rows


def test_the_select_step_has_the_id_the_output_reads():
    assert _select_step()["id"] == "select"


def test_a_push_selects_every_listed_repo_with_its_owner_and_name(tmp_path):
    done, rows = _select(tmp_path, "push")
    assert done.returncode == 0, done.stderr
    assert rows == [
        {"repo": "agent-bureau", "owner": "dreadnought-foundry", "name": "agent-bureau",
         "agent": "agent:frontend", "initiative": "initiative:bureau",
         "owned_by": "DRE-8536"},
        {"repo": "portico", "owner": "dreadnought-foundry", "name": "portico",
         "agent": "agent:frontend", "initiative": "initiative:portico", "owned_by": ""},
    ]


def test_a_dispatch_selects_only_the_repo_it_names(tmp_path):
    done, rows = _select(tmp_path, "workflow_dispatch", "portico")
    assert done.returncode == 0, done.stderr
    assert [r["repo"] for r in rows] == ["portico"]


def test_a_dispatch_for_a_repo_the_file_does_not_list_fails(tmp_path):
    done, _ = _select(tmp_path, "workflow_dispatch", "atlas")
    assert done.returncode != 0
    assert "atlas" in done.stdout + done.stderr


def test_an_entry_whose_repo_the_map_does_not_carry_fails(tmp_path):
    done, _ = _select(tmp_path, "push", repos=[
        {"repo": "nowhere", "agent": "agent:frontend", "initiative": "initiative:bureau"}])
    assert done.returncode != 0
    assert "nowhere" in done.stdout + done.stderr


def test_the_live_config_selects_agent_bureau_on_the_push(tmp_path):
    (tmp_path / "config").mkdir()
    for path in (CONFIG, REPO_MAP):
        (tmp_path / "config" / path.name).write_text(path.read_text(encoding="utf-8"),
                                                     encoding="utf-8")
    output = tmp_path / "github_output"
    output.write_text("", encoding="utf-8")
    done = _bash(_select_step()["run"], tmp_path,
                 {"EVENT_NAME": "push", "REQUESTED": "", "GITHUB_OUTPUT": str(output)})
    assert done.returncode == 0, done.stderr
    rows = json.loads(_outputs(output)["repos"])
    assert {"repo": "agent-bureau", "owner": "dreadnought-foundry",
            "name": "agent-bureau", "agent": "agent:frontend",
            "initiative": "initiative:bureau", "owned_by": "DRE-8536"} in rows


FAKE_FILER = '''\
import json, sys
from pathlib import Path
argv = sys.argv[1:]
counted = json.loads(Path(argv[argv.index("--count-json") + 1]).read_text())
Path(__file__).with_name("filer-argv.json").write_text(json.dumps(
    {"argv": argv, "counted": counted}))
print("parts-cleanup: agent-bureau — 0 copy(ies), 0 filed, 0 already filed",
      file=sys.stderr)
'''


def _workspace(tmp_path: Path) -> Path:
    """A counted repo at the root and a pipeline at `.bureau-pipeline` whose
    count is the real `parts_check.py` and whose filer records its argv."""
    work = tmp_path / "work"
    scripts = work / PIPELINE_PATH / "scripts"
    scripts.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(work)], check=True)
    (scripts / "parts_check.py").symlink_to(Path(parts_check.__file__).resolve())
    (scripts / "parts_cleanup.py").write_text(FAKE_FILER, encoding="utf-8")
    return work


def _count_and_file(tmp_path: Path, owned_by: str) -> tuple:
    work = _workspace(tmp_path)
    count_json = tmp_path / "parts-count.json"
    env = {"COUNT_JSON": str(count_json), "SLUG": "agent-bureau"}
    counted = _bash(_count_step()["run"], work, env)
    assert counted.returncode == 0, counted.stderr
    filed = _bash(_file_step()["run"], work, {
        **env, "AGENT": "agent:frontend", "INITIATIVE": "initiative:bureau",
        "OWNED_BY": owned_by, "LINEAR_API_KEY": "unused"})
    assert filed.returncode == 0, filed.stderr
    record = json.loads((work / PIPELINE_PATH / "scripts" / "filer-argv.json").read_text())
    return counted, filed, record


def test_an_entry_with_an_owner_hands_the_filer_owned_by(tmp_path):
    _, _, record = _count_and_file(tmp_path, "DRE-8536")
    assert record["argv"] == [
        "file", "--repo", "agent-bureau", "--count-json", str(tmp_path / "parts-count.json"),
        "--agent", "agent:frontend", "--initiative", "initiative:bureau",
        "--owned-by", "DRE-8536"]


def test_an_entry_with_no_owner_hands_the_filer_no_owned_by(tmp_path):
    _, _, record = _count_and_file(tmp_path, "")
    assert "--owned-by" not in record["argv"]
    assert record["argv"] == [
        "file", "--repo", "agent-bureau", "--count-json", str(tmp_path / "parts-count.json"),
        "--agent", "agent:frontend", "--initiative", "initiative:bureau"]


def test_the_filer_reads_the_counts_own_json(tmp_path):
    _, _, record = _count_and_file(tmp_path, "DRE-8536")
    assert record["counted"]["repo"] == "agent-bureau"
    assert record["counted"]["copies"] == []


def test_both_tagged_lines_reach_the_log_unchanged(tmp_path):
    counted, filed, _ = _count_and_file(tmp_path, "DRE-8536")
    assert f"{parts_check.COUNT_TAG} agent-bureau — " in counted.stderr
    assert f"{parts_cleanup.TAG} agent-bureau — " in filed.stderr


def test_a_count_that_cannot_answer_fails_its_step(tmp_path):
    work = _workspace(tmp_path)
    broken = work / PIPELINE_PATH / "scripts" / "parts_check.py"
    broken.unlink()
    broken.write_text("import sys\nprint('parts-count: x — UNKNOWN — boom', file=sys.stderr)\n"
                      "sys.exit(1)\n", encoding="utf-8")
    done = _bash(_count_step()["run"], work,
                 {"COUNT_JSON": str(tmp_path / "parts-count.json"), "SLUG": "agent-bureau"})
    assert done.returncode == 1
