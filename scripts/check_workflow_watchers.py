#!/usr/bin/env python3
"""Every workflow that can go red on the default branch has a watcher (DRE-2820).

Origin (live, 2026-08-29). `self-red-main-repair.yml` watched exactly one
workflow, by name:

    on:
      workflow_run:
        workflows: [Pipeline Tests]

DRE-2726 shipped the lane-contract harness as its OWN workflow (`harness.yml` /
"Integration Harness") and nothing added it to that list. The harness went red
on `main` at ~02:55 and stayed red for fourteen hours; every Red-Main Repair run
that day concluded `skipped`, including the one three minutes after the failure.
Two approved PRs inherited the breakage, one fix agent spent attempts on a check
that was never its fault, and the cause — two stale entries in
`config/lane-contract.json`, named in plain words by the harness log the whole
time — was found by accident.

Adding "Integration Harness" to the list is the symptom's fix. The DEFECT is
that the list was remembered: a repo can have any number of workflows that go
red on `main`, and the repair rail knew one of them by name, so adding a
workflow silently added an unwatched failure surface. This is the same
two-gating-layers hazard as a workflow's `paths:` filter versus the CI-suite
filter inside it — two independent gates that must be fixed separately.

So the watched set is DERIVED from the workflow files and asserted here, as a
Pipeline Tests step. Two rules, from two different failure meanings:

  RULE 1 — RED COMMIT. A workflow that validates a commit on the default
  branch (a `push` trigger that reaches it) must be watched by the Red-Main
  Repair caller. Its failure means `main` itself is broken, which is what the
  repair loop exists to forward-fix (adr-red-main-auto-repair).

  RULE 2 — CRASHED RUN. A workflow that can RUN on the default branch at all
  (schedule, repository_dispatch, workflow_dispatch, workflow_run,
  issue_comment) must be watched by SOME watcher. Its failure is usually a run
  that crashed rather than a commit that is broken, which is the medic's job —
  but "nobody is watching" must never be the answer.

The ONE exemption is declared, in data, with its reason: the medic is the
terminal watcher. A medic that watched itself would rebuild the 2026-06-28
medic-crash-loop the ADR forbids, so nothing watches it by design.

Deterministic, PyYAML-only. Run from anywhere:

    python3 scripts/check_workflow_watchers.py [workflows_dir] [default_branch]

tests/test_red_main_watch_coverage.py exercises these functions against
synthetic workflows (each violation class, including a newly added workflow
that nothing watches) and against the LIVE workflow files, so the next
workflow added without a watcher turns Pipeline Tests red instead of passing
quietly.
"""

import fnmatch
import sys
from pathlib import Path
from typing import NamedTuple

import yaml

WORKFLOWS_DIR = Path(__file__).resolve().parent.parent / ".github" / "workflows"
DEFAULT_BRANCH = "main"

# The reusable workflow each rail is built from. A caller is identified by what
# it CALLS, never by its filename — a product repo names its stub whatever it
# likes, and this repo's own stubs have been renamed before.
REPAIR_REUSABLE = "red-main-repair.yml"
MEDIC_REUSABLE = "medic.yml"

# Triggers that produce a run whose head_branch is the default branch even
# though no commit was pushed to it: scheduled sweeps, dispatches, and
# workflow_run/issue_comment runs (GitHub runs those from the default branch).
# `pull_request` is deliberately absent — its head_branch is the PR's branch.
DEFAULT_BRANCH_EVENTS = (
    "schedule",
    "workflow_dispatch",
    "repository_dispatch",
    "workflow_run",
    "issue_comment",
)

TERMINAL_WATCHER_REASON = (
    "the medic is the terminal watcher: a medic that watched itself would "
    "rebuild the 2026-06-28 medic crash-loop (retrying a run that cannot "
    "succeed deepens the failure), so nothing watches it, deliberately"
)


class Workflow(NamedTuple):
    """One workflow file, as the checker needs it."""

    filename: str
    name: str          # the `name:` GitHub matches workflow_run against
    on: dict           # the trigger table
    doc: dict


def on_block(doc):
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


def load_workflows(workflows_dir=WORKFLOWS_DIR):
    """Every *.yml in the directory, in filename order."""
    found = []
    for path in sorted(Path(workflows_dir).glob("*.yml")):
        doc = yaml.safe_load(path.read_text())
        if not isinstance(doc, dict):
            continue
        found.append(
            Workflow(
                filename=path.name,
                name=doc.get("name") or path.name,
                on=on_block(doc),
                doc=doc,
            )
        )
    return found


def is_reusable_only(on) -> bool:
    """A workflow that can only be CALLED never runs — and never goes red — on
    its own, so no watcher can or should carry it."""
    keys = set(on or {})
    return bool(keys) and keys <= {"workflow_call"}


def _push_reaches(push, default_branch: str) -> bool:
    """Does this `push:` trigger fire for a commit landing on the default
    branch? A bare `push:` reaches every branch; a tags-only push carries the
    TAG as head_branch and reaches no branch at all."""
    if push is None:
        return True
    if not isinstance(push, dict):
        return False
    branches = push.get("branches")
    ignore = push.get("branches-ignore")
    if branches is None and ignore is None:
        # No branch filter: tags-only means branches never fire; otherwise
        # every branch does.
        return not (push.get("tags") or push.get("tags-ignore"))
    if branches is not None:
        return any(
            fnmatch.fnmatch(default_branch, str(pattern))
            for pattern in (branches if isinstance(branches, list) else [branches])
        )
    patterns = ignore if isinstance(ignore, list) else [ignore]
    return not any(fnmatch.fnmatch(default_branch, str(p)) for p in patterns)


def validates_default_branch(on, default_branch: str = DEFAULT_BRANCH) -> bool:
    """RULE 1's population: this workflow runs against a commit ON the default
    branch, so its failure means the branch itself is red."""
    on = on or {}
    return "push" in on and _push_reaches(on.get("push"), default_branch)


def runs_on_default_branch(on, default_branch: str = DEFAULT_BRANCH) -> bool:
    """RULE 2's population: this workflow can produce a run whose head_branch
    is the default branch, by any trigger."""
    on = on or {}
    if is_reusable_only(on):
        return False
    if validates_default_branch(on, default_branch):
        return True
    return any(event in on for event in DEFAULT_BRANCH_EVENTS)


def watched(on) -> list:
    """The workflow NAMES this workflow watches via `workflow_run`."""
    run = (on or {}).get("workflow_run") or {}
    names = run.get("workflows") or []
    return [str(n) for n in names]


def calls(doc, reusable_filename: str) -> bool:
    """Does any job of this workflow call that reusable workflow?"""
    for job in (doc.get("jobs") or {}).values():
        if not isinstance(job, dict):
            continue
        uses = str(job.get("uses") or "")
        if f"/{reusable_filename}@" in uses:
            return True
    return False


def repair_callers(workflows_dir=WORKFLOWS_DIR) -> list:
    """The workflows that put this repo on the Red-Main Repair rail."""
    return [wf for wf in load_workflows(workflows_dir)
            if calls(wf.doc, REPAIR_REUSABLE)]


def terminal_watchers(workflows_dir=WORKFLOWS_DIR) -> list:
    """The declared exemption from RULE 2, by name — see
    TERMINAL_WATCHER_REASON."""
    return [wf.name for wf in load_workflows(workflows_dir)
            if calls(wf.doc, MEDIC_REUSABLE)]


def check_dir(workflows_dir=WORKFLOWS_DIR, default_branch: str = DEFAULT_BRANCH):
    """(violations, stats) across every *.yml in the directory. stats guards
    against a silently vacuous run: it counts what was actually inspected."""
    workflows = load_workflows(workflows_dir)
    repair = [wf for wf in workflows if calls(wf.doc, REPAIR_REUSABLE)]
    exempt = {wf.name for wf in workflows if calls(wf.doc, MEDIC_REUSABLE)}

    repair_watches = {name for wf in repair for name in watched(wf.on)}
    all_watches = {name for wf in workflows for name in watched(wf.on)}

    violations = []
    stats = {
        "workflows": len(workflows),
        "default_branch_validators": 0,
        "main_capable": 0,
        "watchers": sum(1 for wf in workflows if watched(wf.on)),
        "exempt": len(exempt),
    }

    for wf in workflows:
        if validates_default_branch(wf.on, default_branch):
            stats["default_branch_validators"] += 1
            if repair and wf.name not in repair_watches:
                violations.append(
                    f"{wf.filename}: {wf.name!r} runs on a push to "
                    f"{default_branch} and can go red there, but no Red-Main "
                    f"Repair caller watches it "
                    f"({', '.join(sorted(w.filename for w in repair))}: add "
                    f"it to `on.workflow_run.workflows`)"
                )
        if not runs_on_default_branch(wf.on, default_branch):
            continue
        stats["main_capable"] += 1
        if wf.name in exempt or wf.name in all_watches:
            continue
        violations.append(
            f"{wf.filename}: {wf.name!r} can run on {default_branch} and no "
            f"workflow watches it — a failure would be invisible to the "
            f"repair loop and to the medic. Add it to a `workflow_run` "
            f"watcher (the repair rail for a red commit, the medic for a "
            f"crashed run)."
        )
    return violations, stats


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    workflows_dir = Path(argv[0]) if argv else WORKFLOWS_DIR
    default_branch = argv[1] if len(argv) > 1 else DEFAULT_BRANCH
    violations, stats = check_dir(workflows_dir, default_branch)
    print(
        f"checked {stats['workflows']} workflows "
        f"({stats['default_branch_validators']} validate {default_branch}, "
        f"{stats['main_capable']} can run on it, "
        f"{stats['watchers']} watch something, "
        f"{stats['exempt']} terminal watcher(s) exempt)"
    )
    if stats["workflows"] == 0:
        print("ERROR: found no workflows — wrong directory, or the checker "
              "went vacuous")
        return 1
    if violations:
        for v in violations:
            print(f"FAIL {v}")
        print(f"exempt by design: {TERMINAL_WATCHER_REASON}")
        return 1
    print(f"ok: every workflow that can go red on {default_branch} is watched")
    return 0


if __name__ == "__main__":
    sys.exit(main())
