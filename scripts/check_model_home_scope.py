#!/usr/bin/env python3
"""Every step that reads the model home holds the token that reads it (DRE-6940).

The selector reads the one home for model choices (DRE-6939: the variable
`BUREAU_MODEL_CHOICES` on dreadnought-foundry/bureau-pipeline) with the token
in `BUREAU_MODEL_HOME_TOKEN`. Every reusable workflow here mints its boot token
for the CALLING repository only, so a run in another organization holds
nothing that can read that variable: each job that holds a home-reading step
mints a second, narrow token — the bureau App's installation on
dreadnought-foundry, repository `bureau-pipeline`, `variables: read` — and
hands it to exactly those steps. A step that misses it selects on the defaults
in the code whatever the home says, and says so only in its own note; this
check is what keeps a new selecting step from being added without it.

WHICH STEPS READ THE HOME. A script under `scripts/` (or a subdirectory)
declares the CLI commands whose process reads the home as a module-level
tuple, `READS_MODEL_HOME`, or `("*",)` for every invocation. The declarations
are read by parsing the source — nothing is imported. A workflow step needs
the token when its `run:`

  * invokes `<name>.py <command>` with `<command>` in that script's tuple, or
    invokes `<name>.py` at all when the tuple is `("*",)`;
  * imports a `("*",)` script inline — `import <name>` or `from <name>
    import …` in a `run:` that starts Python with `-c` or from a heredoc;
  * runs `python3 -m <package>` where `scripts/<package>/` holds a module
    declaring `("*",)` (`harness`, whose agent scenarios run the selector as
    a child process that inherits the step's environment).

For each such step it FAILS (exit 1), naming the workflow, the job and the
step, when:

  * `BUREAU_MODEL_HOME_TOKEN` is not in the step's scope (step, job or
    workflow `env:`), or its value is anything but
    `${{ steps.model_home.outputs.token }}`;
  * the job has no mint step (`id: model_home`) before it;
  * the mint step is not `actions/create-github-app-token`, lacks
    `continue-on-error: true` (a refused permission must cost the defaults,
    never the run), or does not ask for owner `dreadnought-foundry`,
    repository `bureau-pipeline` and `permission-variables: read`.

THE DECLARATIONS ARE DERIVED, never listed. Walking the source of `scripts/`
by AST, the check finds every script with a function that reaches the home in
its own process — it calls (or hands on as a callable) `model_home.read` or
one of the five choice functions of `model_fallback` (`kind_for`,
`ladder_for`, `fallback_for`, `select_with_reasons`, `select`), directly or
through a function of another script that does (`groom_judgement` reaches
through `planning_classify._pick_model`, and `groomer` through
`groom_judgement`). It also finds every script with a function that builds a
command list running a declaring script as a child process: a list or tuple
literal holding a string that ends `<name>.py` — alone, or as the last
argument of an `os.path.join` call — followed by a string literal in that
script's tuple, or by anything at all when the tuple is `("*",)`. It FAILS
naming any such script that declares no `READS_MODEL_HOME`. A command list
running a command outside the tuple (`model_fallback.py effort`) needs nothing.

What it still cannot see:

  * a command name built at run time (`model_fallback.py "$CMD"`, or a list
    whose command element is a variable) — it names none today;
  * reach through a child process is not carried on to the in-process
    callers of the function that starts it: `harness/agent_run.py` declares,
    and the modules of `harness` that call it run inside the `python3 -m
    harness` step that declaration already covers;
  * a call through an attribute of an object (`self.x()`, `obj.select()`) or
    a module reached by a dotted chain (`a.b.select()`).

Deterministic, PyYAML-only. Run from anywhere:

    python3 scripts/check_model_home_scope.py [repo_root]

tests/test_model_home_scope.py runs it against the live tree and against
synthetic workflows and scripts for each failure.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent

DECLARATION = "READS_MODEL_HOME"
EVERY = "*"
TOKEN_ENV = "BUREAU_MODEL_HOME_TOKEN"
MINT_ID = "model_home"
REQUIRED_TOKEN_EXPR = "${{ steps.model_home.outputs.token }}"
MINT_ACTION = "actions/create-github-app-token"
MINT_WITH = {
    "owner": "dreadnought-foundry",
    "repositories": "bureau-pipeline",
    "permission-variables": "read",
}

# The functions whose first call in a process reads the home (DRE-6939).
CHOICE_FUNCTIONS = ("kind_for", "ladder_for", "fallback_for",
                    "select_with_reasons", "select")
# {module: the functions of it that read the home}, by module name.
SEEDS = {"model_fallback": CHOICE_FUNCTIONS, "model_home": ("read",)}

MODULE_SCOPE = "<module>"

_TOKEN_EXPR = re.compile(r"^\$\{\{\s*steps\.model_home\.outputs\.token\s*\}\}$")
_PYTHON = r"\bpython3?\b"
_MODULE_RUN = re.compile(_PYTHON + r"[^|;&\n]*?\s-m\s+([A-Za-z_][\w.]*)")
_INLINE_PYTHON = re.compile(_PYTHON + r"(?:\s+-\S+)*?\s+(?:-c\b|-\s*<<)")


# --------------------------------------------------------------------------- #
# The scripts: declarations, and the call graph that derives them              #
# --------------------------------------------------------------------------- #

def _sources(scripts_dir):
    """{relative posix path under scripts/: source} for every script."""
    scripts_dir = Path(scripts_dir)
    found = {}
    for path in sorted(scripts_dir.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        found[path.relative_to(scripts_dir).as_posix()] = path.read_text()
    return found


def declared(tree):
    """The module's `READS_MODEL_HOME`, as a tuple of strings; None when it
    declares none; the string `"malformed"` when it is not a literal tuple
    of strings."""
    for node in tree.body:
        targets = []
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        if any(isinstance(t, ast.Name) and t.id == DECLARATION for t in targets):
            try:
                found = ast.literal_eval(value)
            except ValueError:
                return "malformed"
            if (isinstance(found, tuple) and found
                    and all(isinstance(c, str) and c for c in found)):
                return found
            return "malformed"
    return None


def _stem(rel):
    return rel.rsplit("/", 1)[-1][: -len(".py")]


class _Tree:
    """The parsed scripts and how a name in one of them resolves."""

    def __init__(self, sources):
        self.trees = {}
        self.broken = []
        for rel, text in sources.items():
            try:
                self.trees[rel] = ast.parse(text)
            except SyntaxError as exc:
                self.broken.append(f"scripts/{rel}: does not parse ({exc.msg})")
        self.declarations = {rel: declared(t) for rel, t in self.trees.items()}
        # A bare module name resolves to the top-level script first — every
        # script puts its own directory on sys.path — then to any script of
        # that name in a subdirectory.
        self.by_stem = {}
        for rel in sorted(self.trees, key=lambda r: (r.count("/"), r)):
            self.by_stem.setdefault(_stem(rel), rel)

    def exact(self, dotted, here=""):
        """The relative path of the module a dotted name spells out — beside
        the importing script, or from scripts/ — or None."""
        path = "/".join(dotted.split("."))
        candidates = [path + ".py", path + "/__init__.py"]
        if here:
            candidates = [f"{here}/{c}" for c in candidates] + candidates
        return next((c for c in candidates if c in self.trees), None)

    def module(self, dotted, here=""):
        """The relative path a dotted module name names, or `<stem>.py` for
        a module outside the tree (so the seeds still match by name)."""
        stem = dotted.split(".")[-1]
        return self.exact(dotted, here) or self.by_stem.get(stem, stem + ".py")

    def declaration_for(self, stem):
        """The declared tuple of the script a `<stem>.py` path names."""
        rel = self.by_stem.get(stem)
        found = self.declarations.get(rel) if rel else None
        return found if isinstance(found, tuple) else None


def _aliases(tree, rel, scripts):
    """{local name: ("module", rel) or ("func", rel, name)} for every import
    in the module, wherever it sits."""
    here = rel.rsplit("/", 1)[0] if "/" in rel else ""
    found = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    found[alias.asname] = ("module", scripts.module(alias.name, here))
                else:
                    head = alias.name.split(".")[0]
                    found[head] = ("module", scripts.module(head, here))
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                anchor = here.split("/") if here else []
                anchor = anchor[: len(anchor) - (node.level - 1)] if node.level > 1 else anchor
                base = ".".join([p for p in anchor if p] + ([base] if base else []))
            for alias in node.names:
                local = alias.asname or alias.name
                sub = scripts.exact(f"{base}.{alias.name}" if base else alias.name, here)
                if sub:
                    found[local] = ("module", sub)
                elif base:
                    found[local] = ("func", scripts.module(base, here), alias.name)
    return found


def _scopes(tree):
    """{function name: [nodes]} — each top-level function and class (a class
    is one scope holding its methods), and `<module>` for the rest."""
    scopes = {MODULE_SCOPE: []}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            scopes[node.name] = [node]
        else:
            scopes[MODULE_SCOPE].append(node)
    return scopes


def _walk(nodes):
    for node in nodes:
        yield from ast.walk(node)


def _references(nodes, rel, local, aliases):
    """Every function of a script these nodes call or hand on, as (rel, name)."""
    found = set()
    for node in _walk(nodes):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id in local:
                found.add((rel, node.id))
            elif aliases.get(node.id, ("",))[0] == "func":
                _kind, target, name = aliases[node.id]
                found.add((target, name))
        elif (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
              and aliases.get(node.value.id, ("",))[0] == "module"):
            found.add((aliases[node.value.id][1], node.attr))
    return found


def _script_name(node):
    """The `<name>` of a `<name>.py` string — a literal, or the last argument
    of `os.path.join(...)` — or None."""
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "join" and node.args):
        node = node.args[-1]
    if isinstance(node, ast.Constant) and isinstance(node.value, str) \
            and node.value.endswith(".py"):
        return node.value.rsplit("/", 1)[-1][: -len(".py")]
    return None


def child_process_reads(nodes, scripts):
    """The declaring scripts these nodes build a command list to run in a way
    their declaration says reads the home, as [`<name>.py <command>`]."""
    found = []
    for node in _walk(nodes):
        if not isinstance(node, (ast.List, ast.Tuple)):
            continue
        for i, element in enumerate(node.elts):
            name = _script_name(element)
            commands = scripts.declaration_for(name) if name else None
            if not commands:
                continue
            following = node.elts[i + 1] if i + 1 < len(node.elts) else None
            if EVERY in commands:
                found.append(f"{name}.py")
            elif (isinstance(following, ast.Constant)
                  and following.value in commands):
                found.append(f"{name}.py {following.value}")
    return found


def readers(scripts_dir):
    """{rel: reason} for every script that reaches the home — in its own
    process through the choice functions, or by running a declaring script
    as a child process."""
    scripts = _Tree(_sources(scripts_dir))
    graph = {}
    reasons = {}
    for rel, tree in scripts.trees.items():
        scopes = _scopes(tree)
        aliases = _aliases(tree, rel, scripts)
        local = set(scopes) - {MODULE_SCOPE}
        for name, nodes in scopes.items():
            graph[(rel, name)] = _references(nodes, rel, local, aliases)
            for command in child_process_reads(nodes, scripts):
                reasons.setdefault(rel, f"{_stem(rel)}.{name} runs {command} as a child process")
    seeds = {(scripts.module(stem), name)
             for stem, names in SEEDS.items() for name in names}
    reach = set(seeds)
    via = {}
    # In rounds, so each function's chain to the home is a shortest one.
    while True:
        found = {function: min(called & reach)
                 for function, called in graph.items()
                 if function not in reach and called & reach}
        if not found:
            break
        via.update(found)
        reach.update(found)
    # A named function says more than the module's own top level does.
    for rel, name in sorted(reach - seeds, key=lambda f: (f[0], f[1] == MODULE_SCOPE, f[1])):
        chain, step = [f"{_stem(rel)}.{name}"], via[(rel, name)]
        while True:
            chain.append(f"{_stem(step[0])}.{step[1]}")
            if step in seeds:
                break
            step = via[step]
        reasons.setdefault(rel, f"{' → '.join(chain)}")
    return reasons, scripts


def check_declarations(scripts_dir):
    """Every script the walk finds reaching the home declares
    `READS_MODEL_HOME`; every declaration is a literal tuple of strings."""
    found, scripts = readers(scripts_dir)
    violations = list(scripts.broken)
    for rel, reason in sorted(found.items()):
        if scripts.declarations.get(rel) is None:
            violations.append(
                f"scripts/{rel}: reads the model home ({reason}) but declares "
                f"no {DECLARATION} — declare the CLI commands whose process "
                f"reads it, or (\"*\",), so the workflow steps that run it are "
                f"held to carry {TOKEN_ENV}"
            )
    for rel, value in sorted(scripts.declarations.items()):
        if value == "malformed":
            violations.append(
                f"scripts/{rel}: {DECLARATION} must be a literal tuple of "
                f"command names, or (\"*\",)"
            )
    return violations, found, scripts


# --------------------------------------------------------------------------- #
# The workflows                                                                #
# --------------------------------------------------------------------------- #

def _steps(doc):
    for job_id, job in (doc.get("jobs") or {}).items():
        for i, step in enumerate((job or {}).get("steps") or []):
            if isinstance(step, dict):
                yield job_id, i, step


def _env(obj):
    env = (obj or {}).get("env") if isinstance(obj, dict) else None
    return env if isinstance(env, dict) else {}


def _logical_lines(run):
    """The command lines of a `run:`, continuations folded, comments out."""
    return [line for line in run.replace("\\\n", " ").splitlines()
            if not line.strip().startswith("#")]


def star_packages(scripts):
    """The packages under scripts/ holding a module that declares `("*",)`."""
    return {rel.split("/", 1)[0] for rel, value in scripts.declarations.items()
            if "/" in rel and isinstance(value, tuple) and EVERY in value}


def step_reads(run, scripts, packages):
    """Why a `run:` reads the home, as [reason]; empty when it does not."""
    reasons = []
    declared_stems = {_stem(rel): value for rel, value in scripts.declarations.items()
                      if isinstance(value, tuple)}
    for line in _logical_lines(run):
        if not re.search(_PYTHON, line):
            continue
        for stem, commands in declared_stems.items():
            pattern = re.compile(
                _PYTHON + r"[^|;&]*?(?:^|[\s/\"'])" + re.escape(stem)
                + r"\.py[\"']?(?:\s+[\"']?([^\s\"']+))?"
            )
            for match in pattern.finditer(line):
                command = match.group(1)
                if EVERY in commands:
                    reasons.append(f"runs {stem}.py")
                elif command in commands:
                    reasons.append(f"runs {stem}.py {command}")
        for match in _MODULE_RUN.finditer(line):
            package = match.group(1).split(".")[0]
            if package in packages:
                reasons.append(f"runs python3 -m {package}")
    if _INLINE_PYTHON.search(run):
        for stem, commands in declared_stems.items():
            if EVERY not in commands:
                continue
            pattern = re.compile(
                r"(?:\bimport\s+(?:[\w.]+\s*,\s*)*" + re.escape(stem) + r"\b"
                r"|\bfrom\s+" + re.escape(stem) + r"\s+import\b)"
            )
            if pattern.search(run):
                reasons.append(f"imports {stem} inline")
    return sorted(set(reasons))


def token_in_scope(doc, job_id, step):
    """The value `BUREAU_MODEL_HOME_TOKEN` has in a step, or None."""
    for scope in (step, (doc.get("jobs") or {}).get(job_id), doc):
        if TOKEN_ENV in _env(scope):
            return _env(scope)[TOKEN_ENV]
    return None


def is_token_expression(value):
    return bool(_TOKEN_EXPR.match(str(value).strip()))


def _mint_errors(mint):
    """What is wrong with a mint step, as [reason]."""
    errors = []
    uses = mint.get("uses")
    if not (isinstance(uses, str) and uses.split("@", 1)[0] == MINT_ACTION):
        errors.append(f"is not {MINT_ACTION} (uses {uses!r})")
    if mint.get("continue-on-error") is not True:
        errors.append(
            "lacks `continue-on-error: true` — a refused permission must "
            "leave the token empty and the run on the defaults in the code, "
            "never fail the job"
        )
    with_ = mint.get("with") if isinstance(mint.get("with"), dict) else {}
    for key, want in MINT_WITH.items():
        if str(with_.get(key, "")).strip() != want:
            errors.append(f"has {key}: {with_.get(key)!r}, not {want!r}")
    return errors


def reading_steps(doc, scripts, packages):
    """[(job_id, index, step, reasons)] for every step that reads the home."""
    found = []
    for job_id, i, step in _steps(doc):
        run = step.get("run")
        if isinstance(run, str):
            reasons = step_reads(run, scripts, packages)
            if reasons:
                found.append((job_id, i, step, reasons))
    return found


def check_workflow(doc, name, scripts, packages):
    """All DRE-6940 violations in one parsed workflow. Empty list == clean."""
    violations = []
    jobs = doc.get("jobs") or {}
    for job_id, i, step, reasons in reading_steps(doc, scripts, packages):
        where = f"{name}: job {job_id}: step {step.get('name') or i!r}"
        why = "; ".join(reasons)
        value = token_in_scope(doc, job_id, step)
        if value is None:
            violations.append(
                f"{where} ({why}) has no {TOKEN_ENV} in scope — set it to "
                f"{REQUIRED_TOKEN_EXPR!r}, or it selects on the defaults in the "
                f"code whatever the home says"
            )
        elif not is_token_expression(value):
            violations.append(
                f"{where} ({why}): {TOKEN_ENV} is {value!r} — it must be "
                f"exactly {REQUIRED_TOKEN_EXPR!r}"
            )
        earlier = ((jobs.get(job_id) or {}).get("steps") or [])[:i]
        mints = [s for s in earlier if isinstance(s, dict) and s.get("id") == MINT_ID]
        if not mints:
            violations.append(
                f"{where} ({why}): job {job_id} has no mint step "
                f"(id: {MINT_ID}) before it — the token it names is empty"
            )
            continue
        for error in _mint_errors(mints[-1]):
            violations.append(f"{name}: job {job_id}: mint step {error}")
    return sorted(set(violations), key=violations.index)


def check_tree(root=ROOT):
    """(violations, stats) for the scripts and workflows under `root`."""
    root = Path(root)
    violations, found, scripts = check_declarations(root / "scripts")
    packages = star_packages(scripts)
    stats = {"workflows": 0, "steps": 0, "readers": len(found),
             "declared": sum(isinstance(v, tuple) for v in scripts.declarations.values())}
    for path in sorted((root / ".github" / "workflows").glob("*.yml")):
        doc = yaml.safe_load(path.read_text())
        if not isinstance(doc, dict):
            continue
        stats["workflows"] += 1
        stats["steps"] += len(reading_steps(doc, scripts, packages))
        violations.extend(check_workflow(doc, path.name, scripts, packages))
    return violations, stats


def main(argv):
    root = Path(argv[1]) if len(argv) > 1 else ROOT
    violations, stats = check_tree(root)
    print(
        f"checked {stats['workflows']} workflows ({stats['steps']} steps read "
        f"the model home) and {stats['declared']} declaring scripts "
        f"({stats['readers']} found reading it)"
    )
    if stats["declared"] == 0 or stats["steps"] == 0:
        print("ERROR: found no declaring script or no reading step — "
              "wrong directory or the checker went vacuous")
        return 1
    if violations:
        for v in violations:
            print(f"FAIL {v}")
        return 1
    print(f"ok: every step that reads the model home holds {TOKEN_ENV} from "
          f"its job's mint, and every script that reads it declares "
          f"{DECLARATION}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
