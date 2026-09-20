#!/usr/bin/env python3
"""Every workflow that runs a model writes a death-cause receipt (DRE-4340).

Origin (measured, 2026-09-19). The fleet's failures were counted for the first
time — 1,310 out of 62,451 runs over 22 days — and **69 of them, $97 of model
work, could not be attributed to any cause at all**, because the workflow they
died in wrote nothing down. The classifier existed and was correct; four of
the workflows that run a model simply never called it.

Wiring those four is the symptom's fix. The DEFECT is that the set was
REMEMBERED: a list of the workflows that run a model drifts the moment somebody
adds the next one, and the drift is silent — the new workflow dies of a cause
nothing records, and the next count is short by however many runs it took. This
is the same shape as `check_workflow_watchers.py` (a repair rail that knew one
workflow by name while a second went red on `main` for fourteen hours), and it
gets the same remedy: the population is DERIVED from the workflow files.

THE POPULATION. A reusable workflow (`on: workflow_call` — the fleet's unit of
work; a product repo's stub carries the trigger) with at least one step that
RUNS A MODEL, which is either of:

  * the vendor model action, `anthropics/claude-code-action` — every agent step
    in the pipeline goes through it (standards/vendor-boundaries.md Q2: a
    subscription OAuth token cannot call the raw Messages API at all); or
  * a step handed a model credential in `env:` — `ANTHROPIC_API_KEY` or
    `CLAUDE_CODE_OAUTH_TOKEN`. That is how the groomer's judged read and the
    planner's classifier call a model, through `planning_classify`, and a step
    holding the credential is a step that can spend it.

The second test is deliberately loose. It also matches the model-SELECTING
probe steps, which run no agent — and that costs nothing, because the unit
this guard fails is the JOB, and a job holding a probe is a job that runs the
agent a few steps later. Loose in the safe direction: the failure mode this
exists to prevent is a model step nobody noticed, never a receipt written by a
job that did not need one.

THE RULES, one violation each, all readable off the file:

  1. The job writes EXACTLY ONE death-cause receipt (`death_receipt.py emit`).
     Two are two spellings of the same run's cause.
  2. The receipt step comes AFTER the last model step. A receipt taken before
     the model describes a run that had not died yet.
  3. It runs on failure as well as success (`always()` in its `if`), or it is
     no receipt for exactly the runs this card exists to count.
  4. It cannot change the job's own conclusion (`continue-on-error: true`).
  5. No `${{ }}` inside its `run:` body — the 21k-character expression ceiling
     and the injection rule; every value arrives through `env:`.
  6. The step immediately after it uploads the receipt, pinned to a 40-char
     sha, on `always()` and `continue-on-error`. A receipt on a disk that is
     destroyed with the runner is not a receipt.

Deterministic, PyYAML-only. Run from anywhere:

    python3 scripts/check_death_receipts.py [workflows_dir]

tests/test_death_receipt_wiring.py exercises it against the live files AND
against a mutated copy of them — one receipt step removed at a time, for every
workflow the discovery found — because a guard that has never been seen to
fail has never been shown to work.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import NamedTuple

import yaml

WORKFLOWS_DIR = Path(__file__).resolve().parent.parent / ".github" / "workflows"

#: The vendor model action every agent step in this pipeline runs through.
MODEL_ACTION = "anthropics/claude-code-action"
#: A step handed either of these can spend the model. Both, because
#: `CLAUDE_AUTH_MODE` decides which one a repo sets and a guard that knew one
#: would be blind on every repo holding the other.
MODEL_CREDENTIALS = ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")

#: The one receipt writer. Matched on the call, not on a step name: names are
#: prose and this is the contract (scripts/death_receipt.py).
RECEIPT_CALL = "death_receipt.py emit"

#: The upload that makes the receipt outlive the runner, at a 40-char sha with
#: its version in a comment (DRE-3418, enforced separately by
#: check_action_pins.py; asserted here so a receipt cannot be uploaded by an
#: unpinned action).
PINNED_UPLOAD = re.compile(r"^actions/upload-artifact@[0-9a-f]{40}\b")


class ModelJob(NamedTuple):
    """One job of one reusable workflow that runs a model."""

    filename: str
    job: str
    steps: list
    last_model_step: int


def on_block(doc) -> dict:
    """The workflow's trigger table. YAML 1.1 (safe_load) parses the bare key
    `on` as boolean True, so accept both spellings."""
    on = doc.get("on", doc.get(True))
    if on is None:
        return {}
    if isinstance(on, str):
        return {on: None}
    if isinstance(on, list):
        return {key: None for key in on}
    return on if isinstance(on, dict) else {}


def runs_a_model(step) -> bool:
    """Does this step run a model — the vendor action, or a step holding the
    credential that can call one?"""
    if not isinstance(step, dict):
        return False
    if str(step.get("uses") or "").startswith(MODEL_ACTION):
        return True
    env = step.get("env")
    if not isinstance(env, dict):
        return False
    return any(name in env for name in MODEL_CREDENTIALS)


def writes_a_receipt(step) -> bool:
    if not isinstance(step, dict):
        return False
    return RECEIPT_CALL in str(step.get("run") or "")


def model_jobs(workflows_dir=WORKFLOWS_DIR) -> list:
    """Every (reusable workflow, job) that runs a model, in filename order."""
    found = []
    for path in sorted(Path(workflows_dir).glob("*.yml")):
        try:
            doc = yaml.safe_load(path.read_text())
        except yaml.YAMLError:  # a file that will not parse is not this
            continue            # guard's finding — the YAML step owns that
        if not isinstance(doc, dict):
            continue
        if "workflow_call" not in on_block(doc):
            continue
        for name, job in (doc.get("jobs") or {}).items():
            if not isinstance(job, dict):
                continue
            steps = job.get("steps")
            if not isinstance(steps, list):
                continue
            model = [i for i, step in enumerate(steps) if runs_a_model(step)]
            if model:
                found.append(ModelJob(path.name, str(name), steps, max(model)))
    return found


def _violations(mj: ModelJob) -> list:
    """Every rule this model job breaks, each naming the file and the job."""
    where = f"{mj.filename} [{mj.job}]"
    receipts = [i for i, step in enumerate(mj.steps) if writes_a_receipt(step)]

    if not receipts:
        return [f"{where}: runs a model and writes no death-cause receipt — "
                f"a run that dies here cannot be attributed to any cause. Add "
                f"a step running `{RECEIPT_CALL}` after the last model step."]
    if len(receipts) > 1:
        return [f"{where}: {len(receipts)} death-cause receipt steps — one run "
                f"has one cause, and a second receipt is a second spelling of "
                f"it."]

    index = receipts[0]
    step = mj.steps[index]
    found = []
    if index < mj.last_model_step:
        found.append(f"{where}: the death-cause receipt is written before the "
                     f"last model step (step {index} of {mj.last_model_step}) "
                     f"— it would describe a run that had not died yet.")
    if "always()" not in str(step.get("if") or ""):
        found.append(f"{where}: the death-cause receipt step does not carry "
                     f"`always()` — a receipt written only when the run "
                     f"survived is no receipt for the runs that died.")
    if step.get("continue-on-error") is not True:
        found.append(f"{where}: the death-cause receipt step is missing "
                     f"`continue-on-error: true` — the receipt must never "
                     f"change the job's own conclusion.")
    if "${{" in str(step.get("run") or ""):
        found.append(f"{where}: the death-cause receipt step interpolates "
                     f"`${{{{ }}}}` inside its `run:` body — every value "
                     f"arrives through `env:` (the expression ceiling and the "
                     f"injection rule).")

    if index + 1 >= len(mj.steps):
        found.append(f"{where}: nothing follows the death-cause receipt step "
                     f"to upload it — a receipt on a disk destroyed with the "
                     f"runner is not a receipt.")
        return found
    upload = mj.steps[index + 1]
    uses = str(upload.get("uses") or "") if isinstance(upload, dict) else ""
    if not PINNED_UPLOAD.match(uses):
        found.append(f"{where}: the step after the death-cause receipt is not "
                     f"`actions/upload-artifact` at a 40-char sha (found "
                     f"{uses or 'no `uses:`'}) — the receipt must outlive the "
                     f"runner, and a third-party action must be pinned.")
        return found
    if "always()" not in str(upload.get("if") or ""):
        found.append(f"{where}: the death-cause receipt's upload does not "
                     f"carry `always()` — the receipt that matters is the one "
                     f"a failed run wrote.")
    if upload.get("continue-on-error") is not True:
        found.append(f"{where}: the death-cause receipt's upload is missing "
                     f"`continue-on-error: true`.")
    return found


def check_dir(workflows_dir=WORKFLOWS_DIR):
    """(violations, stats) across every *.yml in the directory.

    `stats` guards against a silently vacuous run: it counts what was actually
    inspected, so a checker pointed at the wrong directory says so rather than
    reporting a clean bill of health for nothing.
    """
    workflows = sorted(Path(workflows_dir).glob("*.yml"))
    jobs = model_jobs(workflows_dir)
    violations = []
    for mj in jobs:
        violations.extend(_violations(mj))
    stats = {
        "workflows": len(workflows),
        "model_jobs": len(jobs),
        "receipts": sum(
            1 for mj in jobs
            if len([s for s in mj.steps if writes_a_receipt(s)]) == 1),
    }
    return violations, stats


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    workflows_dir = Path(argv[0]) if argv else WORKFLOWS_DIR
    violations, stats = check_dir(workflows_dir)
    print(f"checked {stats['workflows']} workflows "
          f"({stats['model_jobs']} job(s) run a model, "
          f"{stats['receipts']} write a death-cause receipt)")
    if stats["workflows"] == 0:
        print("ERROR: found no workflows — wrong directory, or the checker "
              "went vacuous")
        return 1
    if stats["model_jobs"] == 0:
        print("ERROR: found no job that runs a model — this pipeline runs "
              "models, so the discovery is broken rather than the fleet clean")
        return 1
    if violations:
        for v in violations:
            print(f"FAIL {v}")
        return 1
    print("ok: every reusable workflow that runs a model writes the same "
          "death-cause receipt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
