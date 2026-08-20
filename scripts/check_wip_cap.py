#!/usr/bin/env python3
"""One WIP cap across every promotion path (DRE-2529, continues DRE-2527).

Three workflows here promote Backlog children through the dependency gate by
running `reconcile.py`: the `reconcile.yml` cron sweep, `plan.yml`'s
epic-activate route, and `linear-sync.yml`'s merge handler. Only the first
took a `max_wip` workflow_call input; the other two — both `--promote-only`,
both there purely to promote work NOW rather than up to ~80 minutes later
when the drifting `*/15` cron next lands — hardcoded `MAX_WIP=8` inline.

The cost was not "two caps". On a repo whose stub passes `12`, between 8 and
11 cards in flight the two fast paths refused to promote and the card waited
for the slow sweep, which promoted it anyway at 12. The anti-stall
optimisation switched itself off exactly inside the WIP band it was built
for, and nothing reported it.

This checker FAILS (exit 1) when:
  * any workflow assigns `MAX_WIP` a literal — in a workflow/job/step `env:`
    block or inline on a `run:` line — instead of threading the caller's
    input verbatim as `${{ inputs.max_wip }}`;
  * any step that runs `reconcile.py` in a PROMOTING mode (the full sweep or
    `--promote-only`; `--close-epics` and `--conflicts-only` promote nothing)
    has no `MAX_WIP` in scope, so it silently falls through to the script's
    own fallback instead of taking the caller's value;
  * a reusable workflow containing such a step fails to declare the `max_wip`
    input as `type: string` with the ONE canonical default;
  * that canonical default diverges from `reconcile.DEFAULT_MAX_WIP` — the
    value the script uses when `MAX_WIP` is unset or empty. A stub that
    passes nothing must inherit a default that is correct on its own.

Deterministic, PyYAML-only (the script default is read with `ast`, so
importing reconcile.py — which requires live env — is never needed). Run
from anywhere:

    python3 scripts/check_wip_cap.py [workflows_dir]

tests/test_wip_cap_single_source.py exercises these functions against both
synthetic workflows (each violation class) and the LIVE workflow files, so a
diff that re-hardcodes a cap or adds an uncapped promotion path turns
Pipeline Tests red.
"""

import ast
import re
import sys
from pathlib import Path

import yaml

PIPELINE_WORKFLOW_PREFIX = "dreadnought-foundry/bureau-pipeline/.github/workflows/"
INPUT_NAME = "max_wip"
ENV_NAME = "MAX_WIP"
REQUIRED_ENV_EXPR = "${{ inputs.max_wip }}"
ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS_DIR = ROOT / ".github" / "workflows"
RECONCILE_PY = Path(__file__).resolve().parent / "reconcile.py"

# reconcile.py invocations that promote nothing — they need no cap.
NON_PROMOTING_FLAGS = ("--close-epics", "--conflicts-only")

_INLINE_ASSIGN = re.compile(rf"(?:^|\s){ENV_NAME}=(\S+)")
_INPUT_EXPR = re.compile(r"^\$\{\{\s*inputs\.max_wip\s*\}\}$")


def script_default(path=RECONCILE_PY):
    """`reconcile.DEFAULT_MAX_WIP`, read statically. Importing reconcile.py
    would demand REPO/LINEAR_API_KEY in the environment; the guard must run
    anywhere, so parse the module instead of executing it."""
    tree = ast.parse(Path(path).read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "DEFAULT_MAX_WIP":
                    return ast.literal_eval(node.value)
    return None


def canonical_default(path=RECONCILE_PY):
    """The ONE cap. Single-sourced from the script so the workflows' declared
    defaults and the script's own fallback can never drift apart."""
    return script_default(path)


def is_input_expression(value):
    """True only for the caller's input threaded verbatim. Any other value —
    including a quoted `"8"` — is a literal cap."""
    return bool(_INPUT_EXPR.match(str(value).strip().strip('"').strip("'")))


def _on_block(doc):
    """The workflow's trigger table. YAML 1.1 (safe_load) parses the bare key
    `on` as boolean True, so accept both spellings."""
    on = doc.get("on", doc.get(True))
    return on if isinstance(on, dict) else {}


def is_reusable(doc):
    return "workflow_call" in _on_block(doc)


def max_wip_input(doc):
    """The declared `max_wip` workflow_call input spec, or None."""
    call = _on_block(doc).get("workflow_call") or {}
    spec = (call.get("inputs") or {}).get(INPUT_NAME)
    return spec if isinstance(spec, dict) else (None if spec is None else {})


def _steps(doc):
    """Yield (job_id, step_index, step) for every step in the workflow."""
    jobs = doc.get("jobs") or {}
    for job_id, job in jobs.items():
        for i, step in enumerate((job or {}).get("steps") or []):
            if isinstance(step, dict):
                yield job_id, i, step


def _env(obj):
    env = (obj or {}).get("env")
    return env if isinstance(env, dict) else {}


def promotion_steps(doc):
    """Every step that runs reconcile.py in a mode that promotes cards.

    Returns [(job_id, step_index, step)]. A step whose ONLY reconcile.py
    invocations carry a non-promoting flag is excluded — demanding a cap
    there would be noise the next author learns to ignore.
    """
    found = []
    for job_id, i, step in _steps(doc):
        run = step.get("run")
        if not isinstance(run, str):
            continue
        promotes = False
        for line in _reconcile_invocations(run):
            if not any(flag in line for flag in NON_PROMOTING_FLAGS):
                promotes = True
        if promotes:
            found.append((job_id, i, step))
    return found


def _reconcile_invocations(run):
    """The logical command lines that execute reconcile.py, with shell line
    continuations folded in (the inline `MAX_WIP=8 \\` prefix lives on its
    own physical line)."""
    folded = run.replace("\\\n", " ")
    return [
        line
        for line in folded.splitlines()
        if "reconcile.py" in line and not line.strip().startswith("#")
    ]


def max_wip_assignments(doc, name="<doc>"):
    """Every place this workflow sets MAX_WIP, as [(where, value)] — workflow
    env, job env, step env, and inline `MAX_WIP=<v>` on a run line."""
    found = []
    if ENV_NAME in _env(doc):
        found.append((f"{name}: env.{ENV_NAME}", _env(doc)[ENV_NAME]))
    for job_id, job in (doc.get("jobs") or {}).items():
        if ENV_NAME in _env(job):
            found.append((f"{name}: jobs.{job_id}.env.{ENV_NAME}", _env(job)[ENV_NAME]))
    for job_id, i, step in _steps(doc):
        where = f"{name}: jobs.{job_id}.steps[{i}]"
        if ENV_NAME in _env(step):
            found.append((f"{where}.env.{ENV_NAME}", _env(step)[ENV_NAME]))
        run = step.get("run")
        if isinstance(run, str):
            for match in _INLINE_ASSIGN.finditer(run.replace("\\\n", " ")):
                found.append((f"{where}.run", match.group(1)))
    return found


def cap_in_scope(doc, job_id, step):
    """The MAX_WIP value a promotion step would actually export, or None if
    nothing sets it. Inline assignment on the run line wins over step env,
    which wins over job env, which wins over workflow env — shell order."""
    run = step.get("run")
    if isinstance(run, str):
        matches = _INLINE_ASSIGN.findall(run.replace("\\\n", " "))
        if matches:
            return matches[-1]
    for scope in (step, (doc.get("jobs") or {}).get(job_id), doc):
        if ENV_NAME in _env(scope):
            return _env(scope)[ENV_NAME]
    return None


def effective_cap(doc, caller_inputs, default=None):
    """What this workflow's promotion path exports, given the inputs a calling
    stub passes. None when the workflow promotes nothing.

    This is the resolver the acceptance test uses: it answers "for ONE repo
    passing ONE value, what cap does each path actually promote at?" — the
    question the hardcoded literals answered differently.
    """
    steps = promotion_steps(doc)
    if not steps:
        return None
    job_id, _i, step = steps[0]
    value = cap_in_scope(doc, job_id, step)
    if value is None:
        return None
    if not is_input_expression(value):
        return str(value).strip().strip('"').strip("'")
    if INPUT_NAME in caller_inputs:
        return str(caller_inputs[INPUT_NAME])
    spec = max_wip_input(doc) or {}
    declared = spec.get("default")
    return str(declared if declared is not None else default)


def effective_cap_by_workflow(docs, caller_inputs, default=None):
    """{workflow name: cap it would promote at} for every promoting workflow."""
    if default is None:
        default = canonical_default()
    caps = {}
    for name, doc in docs.items():
        if not isinstance(doc, dict):
            continue
        cap = effective_cap(doc, caller_inputs, default)
        if cap is not None:
            caps[name] = cap
    return caps


def check_workflow(doc, name, default=None):
    """All DRE-2529 violations in one parsed workflow. Empty list == clean."""
    if default is None:
        default = canonical_default()
    violations = []

    for where, value in max_wip_assignments(doc, name):
        if not is_input_expression(value):
            violations.append(
                f"{where}: literal WIP cap {value!r} — it must thread the "
                f"caller's input verbatim: {REQUIRED_ENV_EXPR!r}, or a repo "
                f"that overrides the cap silently promotes at this value here"
            )

    steps = promotion_steps(doc)
    for job_id, i, step in steps:
        if cap_in_scope(doc, job_id, step) is None:
            violations.append(
                f"{name}: jobs.{job_id}.steps[{i}]: promotes with no {ENV_NAME} "
                f"in scope — it falls through to the script default instead of "
                f"the caller's cap"
            )

    if steps and is_reusable(doc):
        spec = max_wip_input(doc)
        if spec is None:
            violations.append(
                f"{name}: promotes cards but workflow_call declares no "
                f"'{INPUT_NAME}' input — a consumer stub has no way to pass "
                f"its repo's cap (the DRE-2527 no-op: green and inert)"
            )
        else:
            if spec.get("type") != "string":
                violations.append(
                    f"{name}: '{INPUT_NAME}' input must be type: string "
                    f"(got {spec.get('type')!r})"
                )
            if spec.get("default") != str(default):
                violations.append(
                    f"{name}: '{INPUT_NAME}' input must default to "
                    f"{str(default)!r} (got {spec.get('default')!r}) — the one "
                    f"cap, single-sourced from reconcile.DEFAULT_MAX_WIP"
                )

    return violations


def _called_reusable(job):
    """The bureau-pipeline reusable a stub job calls, as a bare filename."""
    uses = (job or {}).get("uses")
    if not isinstance(uses, str) or PIPELINE_WORKFLOW_PREFIX not in uses:
        return None
    return uses.split(PIPELINE_WORKFLOW_PREFIX, 1)[1].split("@", 1)[0]


def stub_caps(docs, promoters):
    """{stub name: cap it passes, or None} for every trigger stub in `docs`
    that calls one of the PROMOTING reusables named in `promoters`.

    This repo self-hosts (DRE-1929), so its own stubs are one of the fleet's
    consumers — and a repo that passes the cap on one promotion path but not
    another has simply moved the two-caps defect down a level.
    """
    caps = {}
    for name, doc in docs.items():
        if not isinstance(doc, dict):
            continue
        for job in (doc.get("jobs") or {}).values():
            if _called_reusable(job) in promoters:
                value = ((job or {}).get("with") or {}).get(INPUT_NAME)
                caps[name] = value
    return caps


def check_stubs(docs, promoters):
    """All-or-none, and one value: violations in the caller stubs."""
    caps = stub_caps(docs, promoters)
    if not caps:
        return []
    passed = {n: v for n, v in caps.items() if v is not None}
    if not passed:
        return []  # every path inherits the one default — consistent.
    violations = []
    missing = sorted(n for n, v in caps.items() if v is None)
    if missing:
        violations.append(
            f"stubs: {sorted(passed)} pass '{INPUT_NAME}' but {missing} do not "
            f"— the repo would promote at two different caps depending on which "
            f"path fires (the DRE-2529 defect, one level down)"
        )
    for name, value in sorted(passed.items()):
        if not isinstance(value, str):
            violations.append(
                f"{name}: '{INPUT_NAME}' must be a quoted string (got "
                f"{value!r}) — an unquoted number is a YAML int and GitHub "
                f"rejects it against a type: string input"
            )
    values = {str(v) for v in passed.values()}
    if len(values) > 1:
        violations.append(
            f"stubs disagree on the repo's cap: "
            f"{ {n: v for n, v in sorted(passed.items())} }"
        )
    return violations


def check_dir(workflows_dir=WORKFLOWS_DIR, reconcile_py=RECONCILE_PY):
    """(violations, stats) across every *.yml in the directory. stats guards
    against a silently vacuous run: it counts what was actually inspected."""
    violations = []
    default = canonical_default(reconcile_py)
    stats = {
        "workflows": 0,
        "promoters": 0,
        "promotion_steps": 0,
        "stubs": 0,
        "default": default,
    }
    if default is None:
        violations.append(
            f"{reconcile_py.name}: no DEFAULT_MAX_WIP constant — the one cap "
            f"has no single source for the workflow defaults to match"
        )
        return violations, stats
    docs, promoters = {}, set()
    for path in sorted(Path(workflows_dir).glob("*.yml")):
        doc = yaml.safe_load(path.read_text())
        if not isinstance(doc, dict):
            continue
        docs[path.name] = doc
        stats["workflows"] += 1
        steps = promotion_steps(doc)
        if steps:
            promoters.add(path.name)
            stats["promoters"] += 1
            stats["promotion_steps"] += len(steps)
        violations.extend(check_workflow(doc, path.name, default))
    stats["stubs"] = len(stub_caps(docs, promoters))
    violations.extend(check_stubs(docs, promoters))
    return violations, stats


def main(argv):
    workflows_dir = Path(argv[1]) if len(argv) > 1 else WORKFLOWS_DIR
    violations, stats = check_dir(workflows_dir)
    print(
        f"checked {stats['workflows']} workflows "
        f"({stats['promoters']} promote cards, "
        f"{stats['promotion_steps']} promotion steps, "
        f"{stats['stubs']} promotion stubs, "
        f"one cap = {stats['default']})"
    )
    if stats["promotion_steps"] == 0:
        print("ERROR: found no reconcile.py promotion steps — "
              "wrong directory or the checker went vacuous")
        return 1
    if violations:
        for v in violations:
            print(f"FAIL {v}")
        return 1
    print("ok: every promotion path takes the caller's WIP cap; "
          "no literal cap remains")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
