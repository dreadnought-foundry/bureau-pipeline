"""RED-first: a reusable Parts Check workflow runs the duplicate-parts check on
a repo's pull requests, and a red one holds the merge (DRE-8842).

`.github/workflows/parts-check.yml` is a `workflow_call` reusable a repo's thin
`Parts Check` stub calls on its pull requests. It checks out the pull
request's head with full history, checks out bureau-pipeline at
`inputs.pipeline_ref` into `.bureau-pipeline`, writes `git diff
<base sha>...<head sha>` to a file, and runs `parts_check.py check` (DRE-8841)
over it. The check's exit code is the job's, so a copy the diff adds turns
`call / Parts check` red, and the merge gate holds any pull request with a
check run that is not green (`merge_gate.evaluate_checks`).

What this file holds shut:

  * the workflow's shape — the one trigger and its one required input, the
    one job and its name, `contents: read`, no secret and no `paths:` filter;
  * every third-party action pinned by a 40-character commit sha;
  * the two checkouts, and the check step's diff, command and exit code —
    the step's own script is run as written against a throwaway repository:
    a head that adds a copy of a shared part exits 1 and prints the check's
    `COPY` line, and the same page using the shared part exits 0;
  * an event that is not a pull request prints `does not apply` and passes;
  * the merge gate waits on a head whose `call / Parts check` failed;
  * `docs/parts-check-stub.md` carries the stub in the fleet's shape and says
    where else a caller names it.

Run: cd bureau-pipeline && python3 -m pytest tests/test_parts_check_workflow.py -q
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "parts-check.yml"
DOC = ROOT / "docs" / "parts-check-stub.md"
sys.path.insert(0, str(ROOT / "scripts"))

import check_action_pins  # noqa: E402
import merge_gate  # noqa: E402

PIPELINE_REPO = "dreadnought-foundry/bureau-pipeline"
JOB_ID = "parts-check"
JOB_NAME = "Parts check"
STUB_NAME = "Parts Check"
CHECK_RUN = "call / Parts check"
ON_PR = "github.event_name == 'pull_request'"
NOT_PR = "github.event_name != 'pull_request'"
NOT_A_PR_LINE = "parts-check: does not apply — not a pull request"
BASE_SHA = "${{ github.event.pull_request.base.sha }}"
HEAD_SHA = "${{ github.event.pull_request.head.sha }}"
CHECK_COMMAND = "python3 .bureau-pipeline/scripts/parts_check.py check"
PR_TYPES = ["opened", "reopened", "synchronize", "ready_for_review"]
SHA = re.compile(r"[0-9a-f]{40}")


# --------------------------------------------------------------------------
# reading the YAML
# --------------------------------------------------------------------------
def _load() -> dict:
    assert WORKFLOW.is_file(), f"missing workflow {WORKFLOW.name}"
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _on(doc: dict) -> dict:
    # PyYAML parses a bare `on:` key as the boolean True.
    on = doc.get("on", doc.get(True))
    assert isinstance(on, dict), on
    return on


def _job() -> dict:
    return _load()["jobs"][JOB_ID]


def _steps() -> list[dict]:
    return _job()["steps"]


def _checkouts() -> list[dict]:
    return [s for s in _steps() if str(s.get("uses", "")).startswith("actions/checkout@")]


def _caller_checkout() -> dict:
    found = [s for s in _checkouts() if (s.get("with") or {}).get("repository") is None]
    assert len(found) == 1, found
    return found[0]


def _pipeline_checkout() -> dict:
    found = [s for s in _checkouts()
             if (s.get("with") or {}).get("repository") == PIPELINE_REPO]
    assert len(found) == 1, found
    return found[0]


def _check_step() -> dict:
    found = [s for s in _steps() if CHECK_COMMAND in str(s.get("run", ""))]
    assert len(found) == 1, f"one step runs {CHECK_COMMAND!r}: {found}"
    return found[0]


def _not_a_pr_step() -> dict:
    found = [s for s in _steps() if s.get("if") == NOT_PR]
    assert len(found) == 1, found
    return found[0]


def _keys(node) -> set:
    """Every mapping key anywhere in the document."""
    if isinstance(node, dict):
        out = {str(k) for k in node}
        for value in node.values():
            out |= _keys(value)
        return out
    if isinstance(node, list):
        out = set()
        for value in node:
            out |= _keys(value)
        return out
    return set()


# --------------------------------------------------------------------------
# the shape
# --------------------------------------------------------------------------
def test_it_is_called_and_nothing_else():
    assert list(_on(_load())) == ["workflow_call"]


def test_pipeline_ref_is_its_one_required_string_input():
    call = _on(_load())["workflow_call"]
    inputs = call["inputs"]
    assert list(inputs) == ["pipeline_ref"]
    spec = inputs["pipeline_ref"]
    assert spec["type"] == "string"
    assert spec["required"] is True
    assert "default" not in spec


def test_it_asks_for_no_secret():
    call = _on(_load())["workflow_call"]
    assert "secrets" not in (call or {})
    assert "secrets." not in WORKFLOW.read_text(encoding="utf-8")


def test_its_one_job_is_parts_check_named_parts_check():
    jobs = _load()["jobs"]
    assert list(jobs) == [JOB_ID]
    assert jobs[JOB_ID]["name"] == JOB_NAME


def test_the_stubs_call_job_makes_the_check_run_the_gate_reads():
    """GitHub names a called job `<caller job> / <called job name>`."""
    assert f"call / {_job()['name']}" == CHECK_RUN


def test_it_reads_contents_and_nothing_else():
    doc = _load()
    assert doc["permissions"] == {"contents": "read"}
    assert "permissions" not in _job()


def test_no_paths_filter_anywhere():
    """A filtered-out run creates no check run at all
    (standards/engineering.md, CI rule 1)."""
    keys = _keys(_load())
    assert "paths" not in keys
    assert "paths-ignore" not in keys


# --------------------------------------------------------------------------
# the pins
# --------------------------------------------------------------------------
def test_every_third_party_action_is_pinned_by_a_commit_sha():
    third_party = [r for r in check_action_pins.references(WORKFLOW)
                   if not r.local and not r.exempt]
    assert third_party, "the workflow uses actions/checkout at least"
    loose = [r.uses for r in third_party if not SHA.fullmatch(r.ref or "")]
    assert not loose, loose
    assert all(r.comment for r in third_party), "each pin names its version"


def test_check_action_pins_passes_on_the_file():
    assert check_action_pins.check_file(WORKFLOW) == []


# --------------------------------------------------------------------------
# the checkouts
# --------------------------------------------------------------------------
def test_it_checks_out_the_pull_requests_head_with_full_history():
    step = _caller_checkout()
    assert step["if"] == ON_PR
    assert step["with"]["ref"] == HEAD_SHA
    assert step["with"]["fetch-depth"] == 0


def test_it_checks_out_the_pipeline_at_pipeline_ref_with_no_token():
    step = _pipeline_checkout()
    assert step["if"] == ON_PR
    assert step["with"]["ref"] == "${{ inputs.pipeline_ref || 'main' }}"
    assert step["with"]["path"] == ".bureau-pipeline"
    assert "token" not in step["with"]


def test_the_caller_is_checked_out_before_the_pipeline():
    """The caller's checkout cleans the workspace; run after the pipeline's
    it would delete `.bureau-pipeline`."""
    steps = _steps()
    assert steps.index(_caller_checkout()) < steps.index(_pipeline_checkout())
    assert steps.index(_pipeline_checkout()) < steps.index(_check_step())


# --------------------------------------------------------------------------
# the check step
# --------------------------------------------------------------------------
def test_the_diff_comes_from_the_pull_requests_base_and_head_shas():
    step = _check_step()
    assert step["if"] == ON_PR
    assert step["env"]["BASE_SHA"] == BASE_SHA
    assert step["env"]["HEAD_SHA"] == HEAD_SHA
    assert re.search(r'git diff [^\n]*"\$BASE_SHA\.\.\.\$HEAD_SHA" > "\$DIFF"', step["run"])


def test_the_check_reads_that_diff_from_the_callers_checkout():
    run = _check_step()["run"]
    assert re.search(
        re.escape(CHECK_COMMAND) + r' --diff "\$DIFF" --repo-root "\$GITHUB_WORKSPACE"', run)


def test_nothing_swallows_the_checks_exit_code():
    step = _check_step()
    assert "continue-on-error" not in step
    assert "continue-on-error" not in _job()
    assert "|| true" not in step["run"]
    assert "set +e" not in step["run"]


def test_every_step_but_the_not_a_pr_line_runs_on_a_pull_request_only():
    _not_a_pr_step()
    for step in _steps():
        if step.get("if") == NOT_PR:
            continue
        assert step.get("if") == ON_PR, step


# --------------------------------------------------------------------------
# the steps, run as written
# --------------------------------------------------------------------------
DECLARATION = ".github/bureau/design-system.json"
MOLECULES = "web/src/components/molecules"
NOTICE = f"{MOLECULES}/Notice.tsx"
PAGE = "web/src/pages/InitiativesPage.tsx"

NOTICE_SRC = """\
export function Notice({ title }: { title: string }) {
  return <div role="status">{title}</div>;
}
"""

PAGE_COPY = """\
function Notice({ title }: { title: string }) {
  return <p className="notice">{title}</p>;
}

export function InitiativesPage() {
  return <Notice title="Drafts" />;
}
"""

PAGE_SHARED = """\
import { Notice } from "@/components/molecules/Notice";

export function InitiativesPage() {
  return <Notice title="Drafts" />;
}
"""


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
         "-c", "commit.gpgsign=false", *args],
        cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def _write(root: Path, path: str, text: str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _pull_request(tmp_path: Path, page: str) -> tuple[Path, str, str]:
    """A caller's checkout: a base commit with one shared part, a head commit
    adding `page`, and the pipeline beside it as the workflow places it."""
    repo = tmp_path / "caller"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _write(repo, DECLARATION,
           '{"parts": {"atoms": [], "molecules": ["%s"], "organisms": [], '
           '"templates": []}}' % MOLECULES)
    _write(repo, NOTICE, NOTICE_SRC)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "feature")
    _write(repo, PAGE, page)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "head")
    head = _git(repo, "rev-parse", "HEAD")
    (repo / ".bureau-pipeline").mkdir()
    (repo / ".bureau-pipeline" / "scripts").symlink_to(ROOT / "scripts")
    return repo, base, head


def _run(step: dict, cwd: Path, env: dict) -> subprocess.CompletedProcess:
    """A `run:` step the way the runner's default shell runs it."""
    full = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", str(cwd)), **env}
    return subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c",
                           step["run"]], cwd=cwd, env=full, capture_output=True,
                          text=True, timeout=120)


def _run_check(tmp_path: Path, page: str) -> subprocess.CompletedProcess:
    repo, base, head = _pull_request(tmp_path, page)
    temp = tmp_path / "runner-temp"
    temp.mkdir()
    return _run(_check_step(), repo, {"BASE_SHA": base, "HEAD_SHA": head,
                                      "RUNNER_TEMP": str(temp),
                                      "GITHUB_WORKSPACE": str(repo)})


def test_a_head_that_adds_a_copy_fails_the_step_with_the_checks_lines(tmp_path):
    result = _run_check(tmp_path, PAGE_COPY)
    log = result.stdout + result.stderr
    assert result.returncode == 1, log
    assert (f"parts-check: COPY Notice — shared part declared at {NOTICE}:1; "
            f"copy declared at {PAGE}:1") in log
    assert "parts-check: fail — 1 copy(ies) added" in log


def test_a_head_using_the_shared_part_passes_the_step(tmp_path):
    result = _run_check(tmp_path, PAGE_SHARED)
    log = result.stdout + result.stderr
    assert result.returncode == 0, log
    assert "parts-check: pass — 1 UI file(s) checked" in log


def test_an_event_that_is_not_a_pull_request_passes_and_says_why(tmp_path):
    result = _run(_not_a_pr_step(), tmp_path, {})
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == NOT_A_PR_LINE


# --------------------------------------------------------------------------
# the merge gate holds a red one
# --------------------------------------------------------------------------
def _check_runs(parts_conclusion: str) -> list[dict]:
    green = [{"name": name, "status": "completed", "conclusion": "success",
              "check_suite": {"id": suite}}
             for suite, name in enumerate(["scripts unit tests", "TDD commit discipline",
                                           "call / Merge Gate"], start=1)]
    return green + [{"name": CHECK_RUN, "status": "completed",
                     "conclusion": parts_conclusion, "check_suite": {"id": 9}}]


def test_the_merge_gate_waits_on_a_failed_parts_check():
    decision = merge_gate.evaluate_checks(_check_runs("failure"))
    assert decision is not None
    assert decision.action == "wait"
    assert decision.reason == "1 of 4 check runs not green — wait"


def test_the_same_head_with_a_green_parts_check_proceeds():
    assert merge_gate.evaluate_checks(_check_runs("success")) is None


# --------------------------------------------------------------------------
# the stub's page
# --------------------------------------------------------------------------
def _doc() -> str:
    assert DOC.is_file(), f"missing {DOC.relative_to(ROOT)}"
    return DOC.read_text(encoding="utf-8")


def _stub() -> dict:
    blocks = re.findall(r"```yaml\n(.*?)```", _doc(), re.S)
    stubs = [yaml.safe_load(b) for b in blocks]
    stubs = [s for s in stubs if isinstance(s, dict) and s.get("name") == STUB_NAME]
    assert len(stubs) == 1, "one yaml block carries the Parts Check stub"
    return stubs[0]


def test_the_stub_is_named_parts_check_and_runs_on_the_four_pr_types():
    stub = _stub()
    assert list(_on(stub)) == ["pull_request"]
    assert _on(stub)["pull_request"]["types"] == PR_TYPES


def test_the_stub_rides_stable_with_contents_read_and_no_secrets():
    stub = _stub()
    assert stub["permissions"] == {"contents": "read"}
    assert list(stub["jobs"]) == ["call"]
    call = stub["jobs"]["call"]
    assert call["uses"] == f"{PIPELINE_REPO}/.github/workflows/parts-check.yml@stable"
    assert call["with"] == {"pipeline_ref": "stable"}
    assert "secrets" not in call
    assert "paths" not in _keys(stub)


def test_the_doc_names_the_canary_repos_main_pair_in_one_line():
    lines = [line for line in _doc().splitlines()
             if "`@main`" in line and "`pipeline_ref: main`" in line]
    assert len(lines) == 1, lines
    assert "agent-bureau" in lines[0] and "bureau-pipeline" in lines[0]
    assert "docs/self-hosting.md" in lines[0]


def test_the_doc_says_a_caller_names_it_in_the_merge_gate_and_medic_lists():
    text = " ".join(_doc().split())
    assert "`workflow_run`" in text
    for stub in ("Merge Gate", "Pipeline Medic"):
        assert stub in text
    assert f"`{STUB_NAME}`" in text
    assert "DRE-2028" in text
    assert "never re-evaluates" in text
