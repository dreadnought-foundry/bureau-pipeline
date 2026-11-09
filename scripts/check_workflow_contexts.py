#!/usr/bin/env python3
"""No workflow names a context GitHub does not provide at its key (DRE-8913).

Origin (live, 2026-11-02). `parts-count.yml` set its `count` job's own `env:`
to

    COUNT_JSON: ${{ runner.temp }}/parts-count.json

The `runner` context does not exist when a job's `env:` is evaluated — the
runner has not been chosen yet — so GitHub refused the whole file. Every push
run on `main` died in seconds with no jobs and the workflow's path for a name,
because its `name:` was never read, and the one-time count never ran. The file
was valid YAML, so `Workflow YAML parses` and the wiring tests passed it: the
first reader that knew the rule was GitHub, after the merge.

The rule is GitHub's "Context availability" table, transcribed below as
`ALLOWED`, its URL beside it. Each workflow file is walked from its own keys —
no list of workflows is kept — and each string is read for expressions: every
`${{ … }}`, and the whole value of an `if:` that carries none (the bare form).
An expression names a context when an identifier that is a context name
stands on its own: not inside a quoted string literal, not a property after a
`.`, not a function name before a `(`. Each one is held against the row of the
longest table key that matches the string's path, so `jobs.<job_id>.env.X`
reads the `jobs.<job_id>.env` row and a step's `with.path` reads
`jobs.<job_id>.steps.with`. A key the table has no row for — a step's `uses`,
say — provides no context, so any context named there is refused.

Deterministic, PyYAML-only, no network. Run from anywhere:

    python3 scripts/check_workflow_contexts.py [workflows_dir]

Exit 0 with a one-line summary, or exit 1 with one line per finding naming the
file, the job, the key and the context. A file that does not parse is a finding
too. tests/test_check_workflow_contexts.py holds it against fixtures it writes
and against the live workflow files; `tests.yml` runs it in `unit-result`,
beside `Workflow YAML parses`.
"""

import re
import sys
from pathlib import Path
from typing import NamedTuple

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS_DIR = ROOT / ".github" / "workflows"

# Every context an expression can name. An identifier that is none of these is
# not this check's to judge.
CONTEXTS = frozenset({"github", "env", "vars", "job", "jobs", "steps", "runner",
                      "secrets", "strategy", "matrix", "needs", "inputs"})

# Transcribed from GitHub's "Context availability" table, read 2026-11-02:
TABLE_URL = "https://docs.github.com/en/actions/reference/workflows-and-actions/contexts#context-availability"  # noqa: E501
_STEP = frozenset({"github", "needs", "strategy", "matrix", "job", "runner", "env",
                   "vars", "secrets", "steps", "inputs"})
ALLOWED = {
    "run-name": {"github", "inputs", "vars"},
    "concurrency": {"github", "inputs", "vars"},
    "env": {"github", "secrets", "inputs", "vars"},
    "jobs.<job_id>.concurrency": {"github", "needs", "strategy", "matrix", "inputs", "vars"},
    "jobs.<job_id>.container": {"github", "needs", "strategy", "matrix", "vars", "inputs"},
    "jobs.<job_id>.container.credentials": {"github", "needs", "strategy", "matrix", "env",
                                            "vars", "secrets", "inputs"},
    "jobs.<job_id>.container.env.<env_id>": {"github", "needs", "strategy", "matrix", "job",
                                             "runner", "env", "vars", "secrets", "inputs"},
    "jobs.<job_id>.container.image": {"github", "needs", "strategy", "matrix", "vars",
                                      "inputs"},
    "jobs.<job_id>.continue-on-error": {"github", "needs", "strategy", "vars", "matrix",
                                        "inputs"},
    "jobs.<job_id>.defaults.run": {"github", "needs", "strategy", "matrix", "env", "vars",
                                   "inputs"},
    "jobs.<job_id>.env": {"github", "needs", "strategy", "matrix", "vars", "secrets",
                          "inputs"},
    "jobs.<job_id>.environment": {"github", "needs", "strategy", "matrix", "vars", "inputs"},
    "jobs.<job_id>.environment.url": {"github", "needs", "strategy", "matrix", "job",
                                      "runner", "env", "vars", "steps", "inputs"},
    "jobs.<job_id>.if": {"github", "needs", "vars", "inputs"},
    "jobs.<job_id>.name": {"github", "needs", "strategy", "matrix", "vars", "inputs"},
    "jobs.<job_id>.outputs.<output_id>": {"github", "needs", "strategy", "matrix", "job",
                                          "runner", "env", "vars", "secrets", "steps",
                                          "inputs"},
    "jobs.<job_id>.runs-on": {"github", "needs", "strategy", "matrix", "vars", "inputs"},
    "jobs.<job_id>.secrets.<secrets_id>": {"github", "needs", "strategy", "matrix",
                                           "secrets", "inputs", "vars"},
    "jobs.<job_id>.services": {"github", "needs", "strategy", "matrix", "vars", "inputs"},
    "jobs.<job_id>.services.<service_id>.credentials": {"github", "needs", "strategy",
                                                        "matrix", "env", "vars", "secrets",
                                                        "inputs"},
    "jobs.<job_id>.services.<service_id>.env.<env_id>": {"github", "needs", "strategy",
                                                         "matrix", "job", "runner", "env",
                                                         "vars", "secrets", "inputs"},
    "jobs.<job_id>.steps.continue-on-error": _STEP,
    "jobs.<job_id>.steps.env": _STEP,
    "jobs.<job_id>.steps.if": _STEP - {"secrets"},
    "jobs.<job_id>.steps.name": _STEP,
    "jobs.<job_id>.steps.run": _STEP,
    "jobs.<job_id>.steps.timeout-minutes": _STEP,
    "jobs.<job_id>.steps.with": _STEP,
    "jobs.<job_id>.steps.working-directory": _STEP,
    "jobs.<job_id>.strategy": {"github", "needs", "vars", "inputs"},
    "jobs.<job_id>.timeout-minutes": {"github", "needs", "strategy", "matrix", "vars",
                                      "inputs"},
    "jobs.<job_id>.with.<with_id>": {"github", "needs", "strategy", "matrix", "inputs",
                                     "vars"},
    "on.workflow_call.inputs.<inputs_id>.default": {"github", "inputs", "vars"},
    "on.workflow_call.outputs.<output_id>.value": {"github", "jobs", "vars", "inputs"},
}
ALLOWED = {key: frozenset(contexts) for key, contexts in ALLOWED.items()}
_ROWS = sorted(((key, key.split(".")) for key in ALLOWED), key=lambda r: -len(r[1]))

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")


class Finding(NamedTuple):
    file: str
    job: str      # "" for a workflow-level key
    key: str      # the key below the job, e.g. `env`, `steps.with`
    context: str
    where: str    # the full path of the string, e.g. `jobs.count.env.COUNT_JSON`
    allowed: frozenset  # what the key's row provides


def _strip_strings(expression: str) -> str:
    """The expression with every quoted string literal blanked out."""
    return re.sub(r"'(?:[^']|'')*'", lambda m: " " * len(m.group(0)), expression)


def expressions(value: str, bare: bool) -> list:
    """The bodies of `value`'s `${{ … }}` expressions; with `bare`, a value
    that carries none is one expression whole (the bare form of `if:`)."""
    found = []
    start = value.find("${{")
    while start != -1:
        i, quoted = start + 3, False
        while i < len(value):
            if value[i] == "'":
                quoted = not quoted
            elif not quoted and value.startswith("}}", i):
                break
            i += 1
        found.append(value[start + 3:i])
        start = value.find("${{", i + 2)
    if not found and bare and value.strip():
        found.append(value)
    return found


def contexts_named(expression: str) -> set:
    """The contexts an expression names: identifiers that are context names,
    outside string literals, neither a property nor a function name."""
    text = _strip_strings(expression)
    named = set()
    for match in _IDENTIFIER.finditer(text):
        before = text[:match.start()].rstrip()
        after = text[match.end():].lstrip()
        if before.endswith(".") or after.startswith("("):
            continue
        name = match.group(0).lower()
        if name in CONTEXTS:
            named.add(name)
    return named


def _row(path: list):
    """The table key whose pattern matches the longest prefix of `path`."""
    for key, parts in _ROWS:
        if len(parts) <= len(path) and all(
                p.startswith("<") or p == s for p, s in zip(parts, path)):
            return key
    return None


def _strings(node, path: list):
    if isinstance(node, dict):
        for k, v in node.items():
            # PyYAML reads a bare `on:` key as the boolean True.
            yield from _strings(v, path + ["on" if k is True else str(k)])
    elif isinstance(node, list):
        for v in node:
            yield from _strings(v, path)
    elif isinstance(node, str):
        yield path, node


def _display(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def check_file(path: Path, stats: dict | None = None) -> list:
    """Every context `path` names at a key that does not provide it.
    Raises yaml.YAMLError when the file does not parse."""
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    findings = []
    if not isinstance(doc, dict):
        return findings
    for where, value in _strings(doc, []):
        bare = where[-1:] == ["if"]
        bodies = expressions(value, bare)
        if stats is not None:
            stats["expressions"] = stats.get("expressions", 0) + len(bodies)
        if not bodies:
            continue
        row = _row(where)
        allowed = ALLOWED[row] if row else frozenset()
        in_job = len(where) > 2 and where[0] == "jobs"
        job = where[1] if in_job else ""
        if row:
            key = ".".join(p for p in row.split(".")[2 if in_job else 0:]
                           if not p.startswith("<"))
        else:
            key = ".".join(where[2:] if in_job else where)
        for body in bodies:
            for context in sorted(contexts_named(body) - allowed):
                findings.append(Finding(_display(path), job, key, context,
                                        ".".join(where), allowed))
    return findings


def workflow_files(workflows_dir: Path = WORKFLOWS_DIR) -> list:
    return sorted(p for p in Path(workflows_dir).iterdir()
                  if p.is_file() and p.suffix in (".yml", ".yaml"))


def check_dir(workflows_dir: Path = WORKFLOWS_DIR) -> tuple:
    """(findings, stats) over every workflow file in `workflows_dir`."""
    stats = {"files": 0, "expressions": 0}
    findings = []
    for path in workflow_files(workflows_dir):
        stats["files"] += 1
        findings.extend(check_file(path, stats))
    return findings, stats


def main(argv: list) -> int:
    workflows_dir = Path(argv[1]) if len(argv) > 1 else WORKFLOWS_DIR
    stats = {"files": 0, "expressions": 0}
    findings, broken = [], []
    for path in workflow_files(workflows_dir):
        stats["files"] += 1
        try:
            findings.extend(check_file(path, stats))
        except yaml.YAMLError as exc:
            broken.append(f"{_display(path)}: does not parse as YAML — {exc}")
    for line in broken:
        print(f"::error::{line}")
    for f in findings:
        allowed = ", ".join(sorted(f.allowed)) or "none"
        print(f"::error file={f.file}::{f.file}: job {f.job or '(workflow)'}, key {f.key} "
              f"({f.where}): context `{f.context}` is not available there — "
              f"available: {allowed}. See {TABLE_URL}")
    refused = len(findings) + len(broken)
    print(f"workflow-contexts: {stats['files']} file(s), {stats['expressions']} "
          f"expression(s) read, {refused} refused")
    return 1 if refused else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
