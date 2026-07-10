#!/usr/bin/env python3
"""Release-channel ref threading check (DRE-2026).

The reusable workflows here re-checkout dreadnought-foundry/bureau-pipeline
internally (scripts, briefs, standards). Before DRE-2026 those checkouts
carried NO ref, so they always floated to the default branch: a product-repo
stub pinned to `...@v1` pinned only the top-level workflow YAML while every
script/brief/standard it executed still came from `main`. Canary-testing a
tag therefore tested a chimera.

The fix threads a `pipeline_ref` workflow_call input (type string, default
`main`) through every reusable workflow into every internal checkout as

    ref: ${{ inputs.pipeline_ref || 'main' }}

The `|| 'main'` fallback keeps behavior byte-identical to the old ref-less
checkout on any event where the `inputs` context is empty (workflow_dispatch,
repository_dispatch, schedule), and the `main` default keeps a stub that
omits the input on today's rolling channel.

This checker FAILS (exit 1) when:
  * any `actions/checkout` of dreadnought-foundry/bureau-pipeline lacks a
    `ref:`, or carries any ref other than the canonical threading expression;
  * any reusable workflow (one with a `workflow_call` trigger) fails to
    declare the `pipeline_ref` input as `type: string` / `default: main`.

Deterministic, PyYAML-only. Run from anywhere:

    python3 scripts/check_pipeline_ref.py [workflows_dir]

tests/test_pipeline_ref_threading.py exercises these functions against both
synthetic workflows (each violation class) and the LIVE workflow files, so a
diff that drops a ref or an input turns Pipeline Tests red.
"""

import sys
from pathlib import Path

import yaml

PIPELINE_REPO = "dreadnought-foundry/bureau-pipeline"
INPUT_NAME = "pipeline_ref"
REQUIRED_REF_EXPR = "${{ inputs.pipeline_ref || 'main' }}"
WORKFLOWS_DIR = Path(__file__).resolve().parent.parent / ".github" / "workflows"


def _on_block(doc):
    """The workflow's trigger table. YAML 1.1 (safe_load) parses the bare
    key `on` as boolean True, so accept both spellings."""
    on = doc.get("on", doc.get(True))
    return on if isinstance(on, dict) else {}


def _steps(doc):
    """Yield (job_id, step_index, step) for every step in the workflow."""
    jobs = doc.get("jobs") or {}
    for job_id, job in jobs.items():
        for i, step in enumerate((job or {}).get("steps") or []):
            if isinstance(step, dict):
                yield job_id, i, step


def internal_checkouts(doc):
    """Every actions/checkout step that checks out bureau-pipeline itself
    (i.e. carries `with.repository: dreadnought-foundry/bureau-pipeline`).
    Bare checkouts (the caller's repo) and other repositories don't count."""
    found = []
    for job_id, i, step in _steps(doc):
        uses = step.get("uses") or ""
        if not uses.startswith("actions/checkout"):
            continue
        with_ = step.get("with") or {}
        if with_.get("repository") == PIPELINE_REPO:
            found.append((job_id, i, step))
    return found


def is_reusable(doc):
    return "workflow_call" in _on_block(doc)


def check_workflow(doc, name):
    """All DRE-2026 violations in one parsed workflow. Empty list == clean."""
    violations = []

    if is_reusable(doc):
        call = _on_block(doc).get("workflow_call") or {}
        spec = (call.get("inputs") or {}).get(INPUT_NAME)
        if spec is None:
            violations.append(
                f"{name}: workflow_call lacks the '{INPUT_NAME}' input "
                f"(a pinned stub cannot pin this workflow's internal checkouts)"
            )
        else:
            spec = spec or {}
            if spec.get("type") != "string":
                violations.append(
                    f"{name}: '{INPUT_NAME}' input must be type: string "
                    f"(got {spec.get('type')!r})"
                )
            if spec.get("default") != "main":
                violations.append(
                    f"{name}: '{INPUT_NAME}' input must default to 'main' "
                    f"(got {spec.get('default')!r}) — a stub that omits it "
                    f"must stay on today's rolling channel"
                )

    for job_id, i, step in internal_checkouts(doc):
        ref = (step.get("with") or {}).get("ref")
        where = f"{name}: jobs.{job_id}.steps[{i}]"
        if ref is None:
            violations.append(
                f"{where}: internal {PIPELINE_REPO} checkout has no ref: "
                f"— it floats to the default branch and escapes any pin"
            )
        elif ref != REQUIRED_REF_EXPR:
            violations.append(
                f"{where}: internal checkout ref is {ref!r}; it must thread "
                f"the input verbatim: {REQUIRED_REF_EXPR!r}"
            )

    return violations


def check_dir(workflows_dir=WORKFLOWS_DIR):
    """(violations, stats) across every *.yml in the directory. stats guards
    against a silently vacuous run: it counts what was actually inspected."""
    violations = []
    stats = {"workflows": 0, "reusable": 0, "internal_checkouts": 0}
    for path in sorted(Path(workflows_dir).glob("*.yml")):
        doc = yaml.safe_load(path.read_text())
        if not isinstance(doc, dict):
            continue
        stats["workflows"] += 1
        if is_reusable(doc):
            stats["reusable"] += 1
        stats["internal_checkouts"] += len(internal_checkouts(doc))
        violations.extend(check_workflow(doc, path.name))
    return violations, stats


def main(argv):
    workflows_dir = Path(argv[1]) if len(argv) > 1 else WORKFLOWS_DIR
    violations, stats = check_dir(workflows_dir)
    print(
        f"checked {stats['workflows']} workflows "
        f"({stats['reusable']} reusable, "
        f"{stats['internal_checkouts']} internal {PIPELINE_REPO} checkouts)"
    )
    if stats["internal_checkouts"] == 0:
        print(f"ERROR: found no internal {PIPELINE_REPO} checkouts — "
              f"wrong directory or the checker went vacuous")
        return 1
    if violations:
        for v in violations:
            print(f"FAIL {v}")
        return 1
    print("ok: every internal checkout threads pipeline_ref; "
          "every reusable workflow declares it")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
