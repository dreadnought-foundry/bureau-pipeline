#!/usr/bin/env python3
"""A workflow step that runs reconcile.py is a CALL SITE (DRE-3042).

`reconcile.py` takes its arguments from the environment, and a workflow step
is the only place they are passed. A step that omits one is a call with a
missing argument — and nothing said so until a live sweep did.

What it cost, once. On 2026-09-03 four sibling PRs merged into bureau-pipeline
in 48 minutes and the fifth went DIRTY on the files they all touched: the exact
case `linear-sync.yml`'s conflict sweep exists for. That step never set
`REPO_SLUG`. **Absence there is not loud** — `REPO` raises at import, but
`REPO_SLUG` DEFAULTS to `atlas`, so `fix_workflow()` computed `agent-fix.yml`,
which on this repo is a `workflow_call` reusable with no dispatch trigger;
`gh workflow run` exited non-zero and the step died having dispatched nothing.
A conflicted pull request emits no workflow events at all, so nothing else
noticed either: PR #244 sat with no critic and no fix loop until a person
dispatched `self-agent-fix.yml` by hand twelve minutes later.

Every other reconcile.py step in the tree set the variable. The defect was
never in the helper or in the sweep — it was one call site written without one
argument, and the only reader that could have caught it was a lint over the
call sites.

This checker FAILS (exit 1) when any step that runs `reconcile.py` does not
provide every variable in `reconcile.REQUIRED_ENV`. A variable counts as
provided when it is in the workflow, job or step `env:` block, assigned in the
step's shell (`VAR=…` / `export VAR=…`), or prefixed onto the invocation
itself (`REPO_SLUG=$(…) python3 …/reconcile.py`) — the three spellings the
shipped steps actually use.

The required set is READ FROM `reconcile.py`, statically, so this file cannot
drift from the helper it checks; and which steps are call sites is
`check_wip_cap`'s predicate, not a second derivation of it — two guards
disagreeing about what a reconcile.py step is would let one of them go quiet
while reporting "ok".

Deterministic, PyYAML-only (the constant is read with `ast`, so importing
reconcile.py — which requires live env — is never needed). Run from anywhere:

    python3 scripts/check_reconcile_env.py [workflows_dir]
    python3 scripts/check_reconcile_env.py list   # every call site and its env

tests/test_linear_sync_workflow.py exercises these functions against the LIVE
workflow files and against a doc with the variable stripped back out, so the
incident replays as a red build rather than as a red evening.
"""

import ast
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_wip_cap  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS_DIR = ROOT / ".github" / "workflows"
RECONCILE_PY = Path(__file__).resolve().parent / "reconcile.py"

# The call-site predicate and the YAML walk are `check_wip_cap`'s. They are
# reached through their private names on purpose: the alternative is a second
# derivation of "which steps run reconcile.py", and the failure mode of two
# derivations is that the quieter one keeps reporting ok.
steps_of = check_wip_cap._steps
env_of = check_wip_cap._env
reconcile_invocations = check_wip_cap._reconcile_invocations

#: `VAR=` / `export VAR=` opening its own line — how the sweep's other steps
#: derive a value in shell rather than declaring it in `env:`.
_ASSIGNMENT = re.compile(r"(?m)^\s*(?:export\s+)?([A-Z][A-Z0-9_]*)=")

#: `VAR=value python3 …` — the per-command prefix form (linear-sync's merge
#: step and plan.yml's epic-activate route both pass REPO/REPO_SLUG that way).
_PREFIXED = re.compile(r"([A-Z][A-Z0-9_]*)=")


def required_env(path=RECONCILE_PY):
    """`reconcile.REQUIRED_ENV`, read statically. Importing reconcile.py would
    demand REPO/LINEAR_API_KEY in the environment; the guard must run
    anywhere, so parse the module instead of executing it."""
    tree = ast.parse(Path(path).read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "REQUIRED_ENV":
                    return tuple(ast.literal_eval(node.value))
    return None


def provided_env(doc, job_id, step):
    """Every variable name this step puts in reconcile.py's environment.

    Names only, never values: the question is whether the argument was passed
    at all. Order does not matter either — a step that sets the variable after
    the sweep is a different defect, and one no reader has ever made.
    """
    names = set(env_of(doc)) | set(env_of((doc.get("jobs") or {}).get(job_id)))
    names |= set(env_of(step))
    run = step.get("run")
    if isinstance(run, str):
        names |= set(_ASSIGNMENT.findall(run))
        for line in reconcile_invocations(run):
            names |= set(_PREFIXED.findall(line))
    return names


def call_sites(doc):
    """`[(job_id, step_index, step)]` for every step that runs reconcile.py.

    Every mode, unlike `check_wip_cap.promotion_steps`: a cap is only owed by
    a promoting sweep, but REPO/REPO_SLUG are owed by all four of them — and
    `--conflicts-only`, the one that promotes nothing, is the one that broke.
    """
    found = []
    for job_id, index, step in steps_of(doc):
        run = step.get("run")
        if isinstance(run, str) and reconcile_invocations(run):
            found.append((job_id, index, step))
    return found


def check_workflow(doc, name="<doc>", required=None):
    """Every missing argument in one workflow, as readable violations."""
    required = required if required is not None else required_env()
    violations = []
    for job_id, index, step in call_sites(doc):
        provided = provided_env(doc, job_id, step)
        missing = [variable for variable in required if variable not in provided]
        if missing:
            where = step.get("name") or f"jobs.{job_id}.steps[{index}]"
            violations.append(
                f"{name}: step {where!r} runs reconcile.py without "
                f"{', '.join(missing)} — reconcile.REQUIRED_ENV names "
                f"{', '.join(required)}, and a variable this step does not "
                f"pass is an argument the sweep does not get (REPO_SLUG does "
                f"not raise when absent; it defaults, and dispatches the "
                f"wrong workflow)"
            )
    return violations


def check_dir(workflows_dir=WORKFLOWS_DIR, reconcile_py=RECONCILE_PY):
    """(violations, stats) across every *.yml. stats guards against a silently
    vacuous run: it counts what was actually inspected."""
    required = required_env(reconcile_py)
    stats = {"workflows": 0, "callers": 0, "call_sites": 0, "required": required}
    if not required:
        return ([
            f"{Path(reconcile_py).name}: no REQUIRED_ENV constant — the "
            f"sweep's argument list has no single source for this guard to "
            f"check the call sites against"
        ], stats)
    violations = []
    for path in sorted(Path(workflows_dir).glob("*.yml")):
        doc = yaml.safe_load(path.read_text())
        if not isinstance(doc, dict):
            continue
        stats["workflows"] += 1
        sites = call_sites(doc)
        if sites:
            stats["callers"] += 1
            stats["call_sites"] += len(sites)
        violations.extend(check_workflow(doc, path.name, required))
    return violations, stats


def listing(workflows_dir=WORKFLOWS_DIR):
    """Every call site and what it passes — the `list` subcommand's rows."""
    required = required_env() or ()
    rows = []
    for path in sorted(Path(workflows_dir).glob("*.yml")):
        doc = yaml.safe_load(path.read_text())
        if not isinstance(doc, dict):
            continue
        for job_id, index, step in call_sites(doc):
            provided = provided_env(doc, job_id, step)
            rows.append((
                path.name,
                step.get("name") or f"jobs.{job_id}.steps[{index}]",
                [v for v in required if v in provided],
                [v for v in required if v not in provided],
            ))
    return rows


def main(argv):
    if len(argv) > 1 and argv[1] == "list":
        for name, step, passes, missing in listing():
            mark = "ok  " if not missing else "MISS"
            print(f"{mark} {name}: {step} — passes {', '.join(passes) or 'nothing'}"
                  + (f"; missing {', '.join(missing)}" if missing else ""))
        return 0
    workflows_dir = Path(argv[1]) if len(argv) > 1 else WORKFLOWS_DIR
    violations, stats = check_dir(workflows_dir)
    print(
        f"checked {stats['workflows']} workflows "
        f"({stats['callers']} run reconcile.py, "
        f"{stats['call_sites']} call site(s), "
        f"required env = {', '.join(stats['required'] or ['<none>'])})"
    )
    if stats["call_sites"] == 0:
        print("ERROR: found no reconcile.py call sites — "
              "wrong directory or the checker went vacuous")
        return 1
    if violations:
        for violation in violations:
            print(f"FAIL {violation}")
        return 1
    print("ok: every reconcile.py call site passes every required variable")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
