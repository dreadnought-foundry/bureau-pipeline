"""RED-first: a Parts Roster run reads every roster repo's files and prints one
line per repo (DRE-8953).

`.github/workflows/parts-roster.yml` (`Parts Roster`) runs on a push to `main`
that changes the reader, the workflow or `config/repo-map.json`, and on
`workflow_dispatch` with no inputs. Its `select` job reads every key of
`config/repo-map.json` and splits each value into `owner` and `name`. Its
`read` job runs once per repo: it mints a read-only App token for that one
repository — a mint that may fail, for a repo in an organization the App is
not installed on — checks the repo out with no credential left on disk, takes
the pipeline from this run's own checkout into `.bureau-pipeline`, and runs
`parts_roster.py read` (DRE-8950).

What this file holds shut:

  * the triggers, the select job's every key of the repo map, and the leg's
    name, matrix and limits — the `parts-count.yml` shape (DRE-8844);
  * the token's one repository, `contents: read`, `continue-on-error: true`
    and the same pinned sha `parts-count.yml` uses, and
    `persist-credentials: false` on every checkout;
  * the select and read steps' own scripts, run as written: a failed mint
    prints the workflow's own `UNKNOWN … could not be minted` line and passes;
    a reader's one `parts-roster: <slug> — ` line passes whatever its exit
    code; and no such line, or more than one, fails the leg;
  * no secret but the mint's two and no `LINEAR_` name anywhere: it writes
    nothing;
  * every third-party action pinned by a commit sha, and `Parts Roster`
    watched by the Red-Main Repair caller and the medic.

Run: cd bureau-pipeline && python3 -m pytest tests/test_parts_roster_workflow.py -q
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "parts-roster.yml"
PARTS_COUNT = ROOT / ".github" / "workflows" / "parts-count.yml"
REPO_MAP = ROOT / "config" / "repo-map.json"
REPAIR_STUB = ROOT / ".github" / "workflows" / "self-red-main-repair.yml"
MEDIC_STUB = ROOT / ".github" / "workflows" / "self-medic.yml"
sys.path.insert(0, str(ROOT / "scripts"))

import check_action_pins  # noqa: E402
import check_workflow_watchers  # noqa: E402
import parts_roster  # noqa: E402

NAME = "Parts Roster"
REPO_MAP_PATH = "config/repo-map.json"
PATHS = ["scripts/parts_roster.py", ".github/workflows/parts-roster.yml", REPO_MAP_PATH]
PIPELINE_PATH = ".bureau-pipeline"
MINT_ACTION = "actions/create-github-app-token"
CHECKOUT_ACTION = "actions/checkout"
READ_COMMAND = ('python3 .bureau-pipeline/scripts/parts_roster.py read --repo "$SLUG" '
                '--repo-root . --pipeline-root .bureau-pipeline')
ALLOWED_SECRETS = {"BUREAU_APP_ID", "BUREAU_APP_PRIVATE_KEY"}


# --------------------------------------------------------------------------
# reading the files
# --------------------------------------------------------------------------
def _load(path: Path = WORKFLOW) -> dict:
    assert path.is_file(), f"missing workflow {path.name}"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _on(doc: dict) -> dict:
    # PyYAML parses a bare `on:` key as the boolean True.
    on = doc.get("on", doc.get(True))
    assert isinstance(on, dict), on
    return on


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
    return _step_running("select", REPO_MAP_PATH)


def _read_step() -> dict:
    return _step_running("read", "parts_roster.py read")


def _mint() -> dict:
    return _step_using("read", MINT_ACTION)


def _repo_checkout() -> dict:
    return _step_using("read", CHECKOUT_ACTION,
                       repository="${{ matrix.owner }}/${{ matrix.name }}")


def _index(job_id: str, step: dict) -> int:
    return _steps(job_id).index(step)


def _watch_list(stub: Path) -> list:
    return _on(_load(stub))["workflow_run"]["workflows"]


# --------------------------------------------------------------------------
# the workflow's shape
# --------------------------------------------------------------------------
def test_it_is_named_parts_roster():
    assert _load()["name"] == NAME


def test_it_runs_on_a_push_to_main_that_changes_the_reader_the_workflow_or_the_map():
    push = _on(_load())["push"]
    assert push["branches"] == ["main"]
    assert sorted(push["paths"]) == sorted(PATHS)


def test_it_runs_on_a_dispatch_with_no_inputs():
    dispatch = _on(_load())["workflow_dispatch"]
    assert not (dispatch or {}).get("inputs"), dispatch


def test_it_runs_on_nothing_else():
    assert set(_on(_load())) == {"push", "workflow_dispatch"}


def test_it_reads_contents_and_nothing_else():
    doc = _load()
    assert doc["permissions"] == {"contents": "read"}
    for job_id, job in doc["jobs"].items():
        assert "permissions" not in job, f"{job_id} widens the workflow's permissions"


def test_the_read_job_runs_once_per_selected_repo_four_at_a_time():
    job = _job("read")
    assert job["name"] == "Read ${{ matrix.repo }}"
    assert job["needs"] == "select"
    strategy = job["strategy"]
    assert strategy["matrix"]["include"] == "${{ fromJSON(needs.select.outputs.repos) }}"
    assert strategy["max-parallel"] == 4
    assert strategy["fail-fast"] is False
    assert _job("select")["outputs"]["repos"] == "${{ steps.select.outputs.repos }}"


def test_the_read_job_is_skipped_when_nothing_is_selected():
    assert "needs.select.outputs.repos != '[]'" in _job("read")["if"]


# --------------------------------------------------------------------------
# the token and the checkouts
# --------------------------------------------------------------------------
def test_the_token_is_minted_for_the_one_repository_with_contents_read():
    with_ = _mint()["with"]
    assert with_["app-id"] == "${{ secrets.BUREAU_APP_ID }}"
    assert with_["private-key"] == "${{ secrets.BUREAU_APP_PRIVATE_KEY }}"
    assert with_["owner"] == "${{ matrix.owner }}"
    assert with_["repositories"] == "${{ matrix.name }}"
    permissions = {k: v for k, v in with_.items() if k.startswith("permission-")}
    assert permissions == {"permission-contents": "read"}


def test_a_failed_mint_does_not_fail_the_leg():
    assert _mint()["continue-on-error"] is True


def test_the_mint_is_the_same_pinned_sha_parts_count_uses():
    theirs = [s["uses"] for job in _load(PARTS_COUNT)["jobs"].values()
              for s in job.get("steps", [])
              if str(s.get("uses", "")).split("@")[0] == MINT_ACTION]
    assert theirs, "parts-count.yml mints no token"
    assert _mint()["uses"] == theirs[0]


def test_every_checkout_leaves_no_credential_on_disk():
    checkouts = [(job_id, s) for job_id, job in _load()["jobs"].items()
                 for s in job.get("steps", [])
                 if str(s.get("uses", "")).split("@")[0] == CHECKOUT_ACTION]
    assert len(checkouts) == 3, [job_id for job_id, _ in checkouts]
    for job_id, step in checkouts:
        assert step["with"]["persist-credentials"] is False, (job_id, step.get("name"))


def test_the_repo_is_checked_out_on_that_token_only_when_it_was_minted():
    mint, checkout = _mint(), _repo_checkout()
    assert checkout["with"]["token"] == f"${{{{ steps.{mint['id']}.outputs.token }}}}"
    assert checkout["if"] == f"steps.{mint['id']}.outcome == 'success'"
    assert "path" not in checkout["with"], "the reader reads the repo at the workspace root"
    assert _index("read", mint) < _index("read", checkout)


def test_the_pipeline_is_this_runs_own_checkout_beside_it():
    pipeline = _step_using("read", CHECKOUT_ACTION, path=PIPELINE_PATH)
    with_ = pipeline["with"]
    assert "repository" not in with_ and "ref" not in with_, (
        "the pipeline is the run's own commit — on the push, the merge commit")
    # A checkout at the workspace root clears it, so the repo goes first.
    assert _index("read", _repo_checkout()) < _index("read", pipeline)
    assert _index("read", pipeline) < _index("read", _read_step())


def test_the_read_step_runs_the_reader_with_its_three_flags():
    assert READ_COMMAND in _read_step()["run"]


def test_the_read_step_always_runs_and_reads_the_mint_outcome():
    step = _read_step()
    assert "if" not in step, "the read step prints the line a failed mint owes"
    assert "continue-on-error" not in step, "a missing line must turn the leg red"
    assert step["env"] == {"SLUG": "${{ matrix.repo }}", "OWNER": "${{ matrix.owner }}",
                           "NAME": "${{ matrix.name }}",
                           "MINT_OUTCOME": f"${{{{ steps.{_mint()['id']}.outcome }}}}"}
    assert "${{" not in step["run"], "matrix values reach the script through env only"


# --------------------------------------------------------------------------
# secrets, Linear, the pins
# --------------------------------------------------------------------------
def test_it_names_no_secret_but_the_mints_two():
    text = WORKFLOW.read_text(encoding="utf-8")
    named = set(re.findall(r"secrets\.([A-Za-z0-9_]+)", text))
    assert named == ALLOWED_SECRETS, named
    assert "secrets[" not in text and "toJSON(secrets" not in text
    for job_id, job in _load()["jobs"].items():
        for step in job["steps"]:
            if step == _mint():
                continue
            assert "secrets." not in json.dumps(step), (job_id, step.get("name"))


def test_it_carries_no_linear_name():
    assert "LINEAR_" not in WORKFLOW.read_text(encoding="utf-8")


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
    violations, _ = check_workflow_watchers.check_dir()
    assert violations == []
    names = {wf.name for wf in check_workflow_watchers.load_workflows()}
    assert NAME in names


# --------------------------------------------------------------------------
# the select step's own script, run as written
# --------------------------------------------------------------------------
def _bash(script: str, cwd: Path, env: dict) -> subprocess.CompletedProcess:
    full = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/tmp"), **env}
    return subprocess.run(["bash", "-e", "-o", "pipefail", "-c", script], cwd=cwd,
                          env=full, capture_output=True, text=True, timeout=60)


def _outputs(path: Path) -> dict:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        out[key] = value
    return out


def _select(tmp_path: Path, repo_map: dict) -> tuple:
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "config" / "repo-map.json").write_text(json.dumps(repo_map),
                                                       encoding="utf-8")
    output = tmp_path / "github_output"
    output.write_text("", encoding="utf-8")
    step = _select_step()
    done = _bash(step["run"], tmp_path, {"GITHUB_OUTPUT": str(output)})
    rows = json.loads(_outputs(output)["repos"]) if done.returncode == 0 else None
    return done, rows


def test_the_select_step_has_the_id_the_output_reads():
    assert _select_step()["id"] == "select"


def test_the_select_step_names_every_key_of_the_live_repo_map(tmp_path):
    repo_map = json.loads(REPO_MAP.read_text(encoding="utf-8"))
    done, rows = _select(tmp_path, repo_map)
    assert done.returncode == 0, done.stderr
    assert rows == [{"repo": slug, "owner": full.split("/")[0], "name": full.split("/")[1]}
                    for slug, full in repo_map.items()]
    assert {"repo": "atlas", "owner": "EveryBite", "name": "atlas"} in rows
    assert {"repo": "deltasolv", "owner": "DeltaSolv", "name": "deltasolv"} in rows


def test_a_map_value_with_no_owner_fails_the_select(tmp_path):
    done, _ = _select(tmp_path, {"agent-bureau": "dreadnought-foundry/agent-bureau",
                                 "nowhere": "nowhere"})
    assert done.returncode != 0
    assert "nowhere" in done.stdout + done.stderr


# --------------------------------------------------------------------------
# the read step's own script, run as written
# --------------------------------------------------------------------------
SLUG, OWNER, REPO = "atlas", "EveryBite", "atlas"
MINT_FAILED_LINE = (f"parts-roster: {SLUG} — UNKNOWN — the read token could not be "
                    f"minted for {OWNER}/{REPO}")


def _stub_reader(stdout: str, code: int) -> str:
    return ("import json, sys\nfrom pathlib import Path\n"
            "Path(__file__).with_name('reader-argv.json').write_text("
            "json.dumps(sys.argv[1:]))\n"
            f"sys.stdout.write({stdout!r})\nsys.exit({code})\n")


def _workspace(tmp_path: Path, reader: str | None = None) -> Path:
    """A repo at the root and a pipeline at `.bureau-pipeline` whose reader is
    the stub given, or the real `parts_roster.py` when none is."""
    work = tmp_path / "work"
    scripts = work / PIPELINE_PATH / "scripts"
    scripts.mkdir(parents=True)
    if reader is None:
        for name in ("parts_roster.py", "design_parts.py"):
            (scripts / name).symlink_to((ROOT / "scripts" / name).resolve())
        (work / PIPELINE_PATH / "config").mkdir()
        (work / PIPELINE_PATH / "config" / "parts-count.json").write_text(
            json.dumps({"repos": []}), encoding="utf-8")
    else:
        (scripts / "parts_roster.py").write_text(reader, encoding="utf-8")
    return work


def _run_read(work: Path, outcome: str = "success") -> subprocess.CompletedProcess:
    return _bash(_read_step()["run"], work, {
        "SLUG": SLUG, "OWNER": OWNER, "NAME": REPO, "MINT_OUTCOME": outcome})


def _roster_lines(text: str) -> list:
    return [line for line in text.splitlines() if line.startswith(parts_roster.TAG)]


def test_a_failed_mint_prints_the_could_not_be_minted_line_and_passes(tmp_path):
    work = _workspace(tmp_path, _stub_reader("", 1))
    done = _run_read(work, outcome="failure")
    assert done.returncode == 0, done.stderr
    assert _roster_lines(done.stdout) == [MINT_FAILED_LINE]
    assert not (work / PIPELINE_PATH / "scripts" / "reader-argv.json").exists(), (
        "the reader has no checkout to read when the mint failed")


def test_the_reader_is_run_with_the_slug_and_both_roots(tmp_path):
    work = _workspace(tmp_path, _stub_reader(f"parts-roster: {SLUG} — no — x\n", 0))
    done = _run_read(work)
    assert done.returncode == 0, done.stderr
    argv = json.loads((work / PIPELINE_PATH / "scripts" / "reader-argv.json").read_text())
    assert argv == ["read", "--repo", SLUG, "--repo-root", ".",
                    "--pipeline-root", PIPELINE_PATH]


def test_a_readers_unknown_line_with_exit_1_passes_the_step(tmp_path):
    line = f"parts-roster: {SLUG} — UNKNOWN — the declaration cannot be read"
    work = _workspace(tmp_path, _stub_reader(line + "\n", 1))
    done = _run_read(work)
    assert done.returncode == 0, done.stdout + done.stderr
    assert _roster_lines(done.stdout) == [line]


def test_a_readers_yes_line_passes_the_step(tmp_path):
    line = (f"parts-roster: {SLUG} — yes — design/parts.json, 3 part(s); "
            "count on; stub on")
    work = _workspace(tmp_path, _stub_reader(line + "\n", 0))
    done = _run_read(work)
    assert done.returncode == 0, done.stdout + done.stderr
    assert _roster_lines(done.stdout) == [line]


def test_a_reader_that_prints_nothing_and_exits_1_fails_the_step(tmp_path):
    work = _workspace(tmp_path, _stub_reader("", 1))
    done = _run_read(work)
    assert done.returncode != 0
    assert _roster_lines(done.stdout) == []


def test_a_reader_that_prints_nothing_and_exits_0_fails_the_step(tmp_path):
    work = _workspace(tmp_path, _stub_reader("", 0))
    assert _run_read(work).returncode != 0


def test_a_reader_that_prints_two_lines_fails_the_step(tmp_path):
    line = f"parts-roster: {SLUG} — no — x"
    work = _workspace(tmp_path, _stub_reader(f"{line}\n{line}\n", 0))
    assert _run_read(work).returncode != 0


def test_a_line_for_another_repo_fails_the_step(tmp_path):
    work = _workspace(tmp_path, _stub_reader("parts-roster: portico — no — x\n", 0))
    assert _run_read(work).returncode != 0


def test_a_crashed_reader_fails_the_step(tmp_path):
    work = _workspace(tmp_path, "raise SystemExit('Traceback: boom')\n")
    assert _run_read(work).returncode != 0


def test_the_real_reader_answers_an_empty_checkout_with_one_no_line(tmp_path):
    work = _workspace(tmp_path)
    done = _run_read(work)
    assert done.returncode == 0, done.stdout + done.stderr
    lines = _roster_lines(done.stdout)
    assert len(lines) == 1 and lines[0].startswith(f"parts-roster: {SLUG} — no — "), lines
    assert lines[0].endswith("; count off; stub off"), lines
