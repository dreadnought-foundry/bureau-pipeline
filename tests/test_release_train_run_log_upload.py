"""The release train uploads each surface's run log as an artifact (DRE-6574).

Portico's release script leaves its run log on the runner when the lap ends
(DRE-6507), at the path its runbook documents:
`${RUNNER_TEMP}/release-artifacts/<surface>/release-runs.jsonl`. Both Portico
releases since then (runs 38010586953 and 38029537480) printed `run log: left
at …` and ended with `total_count: 0` artifacts, because this reusable workflow
had no step to pick the file up — it went with the runner. The proof of the
release train (DRE-5377) reads that file after a release, so it has to outlive
the runner.

The contract this file pins, and nothing more:

* One upload step, in the `release` job, its last, after `Run the surface`.
* `path` and `name` built from `matrix.surface`, the surface name the plan
  already puts in the matrix: `release-artifacts/portals/release-runs.jsonl`
  and `release-portals-run<run_id>-attempt<run_attempt>` for Portico.
* `if: always()` so a failed or refused lap still uploads;
  `if-no-files-found: ignore` so a surface that writes no run log — every one
  but Portico's today — uploads nothing and stays green; 90 days.
* The file holds partner domains: nothing in the workflow prints it, and the
  artifact is private to the repository like every Actions artifact.
* No job gains a `permissions:` block (the header comment says why a called
  job may not ask for one), and the steps before the upload are unchanged.

What it cannot show: an artifact actually stored. That exists only once this
workflow is the one running on `stable`, so the observation is a reading after
merge (DRE-3075), owed by its own follow-up card.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
WORKFLOW = WORKFLOWS / "release-train.yml"

UPLOAD = "actions/upload-artifact"
PATH = ("${{ runner.temp }}/release-artifacts/${{ matrix.surface }}"
        "/release-runs.jsonl")
NAME = ("release-${{ matrix.surface }}-run${{ github.run_id }}"
        "-attempt${{ github.run_attempt }}")

# The release job's steps as they stood before this card, in order. A checkout
# carries no `name:`, so it is named by what it checks out.
STEPS_BEFORE = [
    "Mint the tag-push token",
    "Say the pair did not mint",
    "checkout: the caller",
    "checkout: dreadnought-foundry/bureau-pipeline",
    "Name the tagger",
    "Assume the caller's release identity",
    "Run the surface",
]


def _doc():
    return yaml.safe_load(WORKFLOW.read_text())


def _steps(job):
    return _doc()["jobs"][job]["steps"]


def _uses(step, action):
    return str(step.get("uses", "")).startswith(action + "@")


def _uploads(job):
    return [i for i, s in enumerate(_steps(job)) if _uses(s, UPLOAD)]


def _upload_step():
    (i,) = _uploads("release")
    return _steps("release")[i]


def _label(step):
    if "name" in step:
        return step["name"]
    if _uses(step, "actions/checkout"):
        repo = (step.get("with") or {}).get("repository")
        return f"checkout: {repo or 'the caller'}"
    return str(step.get("uses"))


# --------------------------------------------------------------------------
# Where the step is: the release job, its last, after the surface ran.
# --------------------------------------------------------------------------

def test_the_release_job_uploads_exactly_once():
    assert len(_uploads("release")) == 1


def test_no_other_job_uploads():
    for name in _doc()["jobs"]:
        if name != "release":
            assert _uploads(name) == [], name


def test_the_upload_is_the_release_jobs_last_step_after_the_surface():
    steps = _steps("release")
    (upload,) = _uploads("release")
    assert upload == len(steps) - 1
    surface = next(i for i, s in enumerate(steps)
                   if s.get("name") == "Run the surface")
    assert surface == upload - 1


def test_the_steps_before_the_upload_are_unchanged():
    steps = _steps("release")
    (upload,) = _uploads("release")
    assert [_label(s) for s in steps[:upload]] == STEPS_BEFORE


# --------------------------------------------------------------------------
# What it uploads: the contract Portico's runbook already documents.
# --------------------------------------------------------------------------

def test_the_path_is_the_surfaces_run_log_under_runner_temp():
    # A `with:` value is not shell-expanded: `$RUNNER_TEMP` would be a
    # literal directory named `$RUNNER_TEMP`.
    assert _upload_step()["with"]["path"] == PATH


def test_the_name_is_per_surface_run_and_attempt():
    # Per attempt, or a re-run's upload is refused as a duplicate name.
    assert _upload_step()["with"]["name"] == NAME


def test_a_failed_or_refused_lap_still_uploads():
    assert _upload_step()["if"] == "always()"


def test_a_surface_that_writes_no_run_log_uploads_nothing_and_stays_green():
    assert _upload_step()["with"]["if-no-files-found"] == "ignore"


def test_the_run_log_is_kept_ninety_days():
    assert _upload_step()["with"]["retention-days"] == 90


def test_the_upload_never_fails_a_lap_that_released():
    # THE TRAIN IS NEVER STOPPED (DRE-3263): the tag is already cut when this
    # runs, so a storage hiccup is a marked step, never a red release.
    assert _upload_step()["continue-on-error"] is True


def test_the_upload_takes_only_the_settings_the_contract_names():
    assert set(_upload_step()["with"]) == {
        "name", "path", "if-no-files-found", "retention-days",
    }


# --------------------------------------------------------------------------
# The pin: the same sha every other workflow's upload carries.
# --------------------------------------------------------------------------

def test_the_upload_is_pinned_to_the_sha_every_other_workflow_carries():
    elsewhere = set()
    for path in WORKFLOWS.glob("*.yml"):
        if path == WORKFLOW:
            continue
        elsewhere |= set(re.findall(
            rf"uses:\s*({re.escape(UPLOAD)}@[0-9a-f]{{40}} # v[\w.]+)",
            path.read_text()))
    assert len(elsewhere) == 1, f"the repo's upload pins disagree: {elsewhere}"
    (pin,) = elsewhere
    line = next(l for l in WORKFLOW.read_text().splitlines()
                if f"uses: {UPLOAD}@" in l)
    assert line.split("uses:", 1)[1].strip() == pin
    assert _upload_step()["uses"] == pin.split(" #")[0]


def test_the_pin_check_passes_on_the_train():
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_action_pins.py"),
         str(WORKFLOW)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


# --------------------------------------------------------------------------
# What it must not do: ask for permission, or print the file.
# --------------------------------------------------------------------------

def test_no_job_declares_a_permissions_block():
    doc = _doc()
    assert "permissions" not in doc
    for name, job in doc["jobs"].items():
        assert "permissions" not in job, name


def test_nothing_in_the_workflow_prints_the_run_log():
    # The file holds partner domains. Read every `run:` body, and the raw
    # text besides, for a cat, echo or tee that names the file or its folder.
    printer = re.compile(
        r"\b(cat|echo|tee)\b[^\n]*(release-runs\.jsonl|release-artifacts)")
    runs = [str(s.get("run", ""))
            for job in _doc()["jobs"].values()
            for s in job.get("steps", [])]
    assert runs, "the walk found no run: bodies — it is reading nothing"
    for body in runs:
        assert not printer.search(body), body
    assert not printer.search(WORKFLOW.read_text())


def test_the_upload_runs_no_shell():
    assert "run" not in _upload_step()
