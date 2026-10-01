#!/usr/bin/env python3
"""The line a workflow step uses to call its script, and the readers that see through it (DRE-5380).

DRE-3488 moves five oversized `run: |` blocks out of the workflows and into
`scripts/<name>.sh`. Once a block has moved, its step's whole `run:` is one
line, and everything that used to read the block — the checkers, the tests
that read a workflow's text — has to read the script instead. This module is
the ONE place that says what that line looks like and how to read through it,
so the checkers, the tests and the five move cards share one spelling instead
of five. Nothing else restates the grammar; import it from here.

THE DELEGATION LINE. A step delegates when its whole `run:` value, stripped, is
exactly one of

    bash .bureau-pipeline/scripts/<name>.sh
    bash "$PIPELINE_DIR/scripts/<name>.sh"

with `<name>` matching `[a-z0-9_]+`. It delegates to `scripts/<name>.sh` in
this repo. `qa-review.yml` uses the `$PIPELINE_DIR` form because its pipeline
checkout moves out of the working tree (DRE-3226, DRE-4158); every other
workflow uses the `.bureau-pipeline` form.

THE SCRIPT FILE. Line 1 is `#!/usr/bin/env bash` and line 2 is `set -e`: that
is the one option a `run:` block gets when its step sets no `shell:` — GitHub
runs it as `bash -e {0}`, and `-o pipefail` comes only with an explicit
`shell: bash`. None of the five steps sets `shell:` and the move adds none, so
the script fails on exactly the lines the block fails on today. A `set` line
already inside a body is body and stays verbatim. After the two opening lines
come the header — written by the move card, never by `move` — then the body.

THE READERS (stdlib only, so a checker can import them where PyYAML is absent):

    delegated_script(run)            -> "scripts/<name>.sh" | None
    step_shell(step, root=ROOT)      -> the script's text, else step["run"]
    workflow_source(path, root=ROOT) -> the workflow, delegation lines expanded
    code_lines(text)                 -> the lines `verify` compares

THE COMMANDS (these parse YAML, so they need PyYAML):

    python3 scripts/step_shell.py move --root DIR --workflow FILE --step NAME \\
        --script NAME [--via pipeline-dir] [--env NAME=EXPR ...]
    python3 scripts/step_shell.py verify --base REF [--root DIR] \\
        --workflow FILE --step NAME [--env NAME=EXPR ...]
    python3 scripts/step_shell.py rehearse --out DIR

`move` is mechanical: a move card writes no shell by hand. It refuses a body
that still holds a `${{` once its `--env` substitutions are applied, and it
refuses to write anything unless reading the result back through
`workflow_source` gives the body it moved. `verify` is the check each move
card runs against `origin/main`. `rehearse` applies all five moves of
REHEARSAL to a throwaway copy, so a card can prove its files survive the
moves before any move has landed.

A FILE argument is a path under the tree's root; a bare name such as
`agent-task.yml` means `.github/workflows/agent-task.yml`.

Exit codes: 0 ok, 1 refused or not verified, 2 usage.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

ROOT = Path(__file__).resolve().parents[1]

SHEBANG = "#!/usr/bin/env bash"
SET_E = "set -e"
OPENING = f"{SHEBANG}\n{SET_E}\n"

_DELEGATION = re.compile(
    r'bash (?:\.bureau-pipeline/scripts/(?P<plain>[a-z0-9_]+)\.sh'
    r'|"\$PIPELINE_DIR/scripts/(?P<pipeline_dir>[a-z0-9_]+)\.sh")'
)
_LINE = {
    None: "bash .bureau-pipeline/scripts/{name}.sh",
    "pipeline-dir": 'bash "$PIPELINE_DIR/scripts/{name}.sh"',
}
_SCRIPT_NAME = re.compile(r"[a-z0-9_]+")
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_DRE = re.compile(r"DRE-\d+")
# `<<WORD`, `<<-WORD`, `<<'WORD'`, `<<"WORD"`, `<<\WORD` — never `<<<`.
_HEREDOC = re.compile(
    r"(?<!<)<<(?P<dash>-?)[ \t]*(?:'(?P<sq>[^']+)'|\"(?P<dq>[^\"]+)\"|\\?(?P<bare>[A-Za-z_][A-Za-z0-9_]*))"
)
_RUN_LINE = re.compile(r"^(?P<lead>\s*(?:-\s+)?)run:[ \t]*(?P<value>\S.*?)\s*$")
_STEP_NAME = re.compile(r"^(?P<indent>\s*)-\s+name:\s*(?P<value>.+?)\s*$")


@dataclass(frozen=True)
class Move:
    """One row of the DRE-3488 move table, as each move card writes it."""

    workflow: str
    step: str
    script: str
    via: str | None = None
    envs: tuple[tuple[str, str], ...] = ()


REHEARSAL: tuple[Move, ...] = (
    Move("agent-task.yml", "Report result to Linear", "report_agent_result"),
    Move(
        "agent-fix.yml",
        "Resolve PR, mode, and attempt budget",
        "resolve_fix_pr",
        envs=(
            ("PR_NUMBER", "github.event.issue.number || github.event.inputs.pr_number"),
            ("REPO", "github.repository"),
        ),
    ),
    Move("agent-fix.yml", "Report", "report_fix_result"),
    Move("merge-gate.yml", "Evaluate and merge", "evaluate_and_merge"),
    Move("qa-review.yml", "Post verdict or neutral status", "post_verdict", via="pipeline-dir"),
)


class StepShellError(Exception):
    """A move or a read that cannot be done as asked."""


# --- the readers ------------------------------------------------------------


def delegated_script(run: str) -> str | None:
    """`scripts/<name>.sh` when `run` is exactly a delegation line, else None."""
    match = _DELEGATION.fullmatch(run.strip())
    if match is None:
        return None
    return f"scripts/{match.group('plain') or match.group('pipeline_dir')}.sh"


def step_shell(step: dict, root: Path = ROOT) -> str:
    """The shell a step runs: its script's text when it delegates, else its `run`."""
    run = step["run"]
    script = delegated_script(run)
    if script is None:
        return run
    return (Path(root) / script).read_text()


def _without_opening(text: str) -> str:
    """A script's text minus line 1 when it is the shebang and line 2 when it is `set -e`."""
    lines = text.splitlines(keepends=True)
    if lines and lines[0].startswith("#!"):
        lines = lines[1:]
        if lines and lines[0].rstrip("\n") == SET_E:
            lines = lines[1:]
    return "".join(lines)


def _expand(text: str, read: Callable[[str], str]) -> str:
    """`text` with each delegation line replaced by a `run: |` block of its
    script's body, indented to the step. `read` maps `scripts/<name>.sh` to
    that file's text."""
    out: list[str] = []
    for line in text.splitlines(keepends=True):
        match = _RUN_LINE.match(line)
        script = delegated_script(match.group("value")) if match else None
        if script is None:
            out.append(line)
            continue
        lead = match.group("lead")
        indent = " " * (len(lead) + 2)
        out.append(f"{lead}run: |\n")
        for body_line in _without_opening(read(script)).splitlines():
            out.append(f"{indent}{body_line}\n" if body_line else "\n")
    return "".join(out)


def workflow_source(path, root: Path = ROOT) -> str:
    """The workflow's text with every delegation line read through to its script.

    Each delegation line becomes a `run: |` block holding its script's text,
    minus the two opening lines and indented to the step; `yaml.safe_load` of
    the result gives each moved step the `run` it had before it moved. A
    workflow with no delegating step comes back unchanged. `path` is absolute
    or relative to `root`."""
    root = Path(root)
    path = Path(path)
    if not path.is_absolute():
        path = root / path
    return _expand(path.read_text(), lambda script: (root / script).read_text())


def code_lines(text: str) -> list[str]:
    """The lines of a script or block that `verify` compares.

    Dropped: line 1 when it is the shebang, line 2 when it is exactly `set -e`,
    blank lines and full-line `#` comments. A heredoc body is kept whole —
    its blank and `#` lines are data, not comments."""
    lines = text.splitlines()
    start = 0
    if lines and lines[0].startswith("#!"):
        start = 1
        if len(lines) > 1 and lines[1] == SET_E:
            start = 2
    kept: list[str] = []
    pending: list[tuple[str, bool]] = []  # heredoc terminators still open
    for line in lines[start:]:
        if pending:
            kept.append(line)
            word, dash = pending[0]
            if (line.lstrip("\t") if dash else line) == word:
                pending.pop(0)
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        kept.append(line)
        for match in _HEREDOC.finditer(line):
            word = match.group("sq") or match.group("dq") or match.group("bare")
            pending.append((word, bool(match.group("dash"))))
    return kept


# --- shared by the commands -------------------------------------------------


def workflow_path(workflow: str) -> str:
    """A FILE argument as a path under the root: a bare name is a workflow."""
    return workflow if "/" in workflow else f".github/workflows/{workflow}"


def parse_envs(pairs: Iterable[str]) -> tuple[tuple[str, str], ...]:
    """`NAME=EXPR` arguments as (NAME, EXPR) pairs."""
    envs = []
    for pair in pairs:
        name, sep, expr = pair.partition("=")
        name, expr = name.strip(), expr.strip()
        if not sep or not _ENV_NAME.fullmatch(name) or not expr:
            raise StepShellError(f"--env wants NAME=EXPR, got {pair!r}")
        envs.append((name, expr))
    return tuple(envs)


def substitute(body: str, envs: Iterable[tuple[str, str]]) -> str:
    """`body` with every `${{ EXPR }}` of each --env replaced by `${NAME}`."""
    for name, expr in envs:
        pattern = re.compile(r"\$\{\{\s*" + re.escape(expr) + r"\s*\}\}")
        body = pattern.sub(lambda _m, name=name: "${" + name + "}", body)
    return body


def _find_step(text: str, step_name: str, where: str) -> dict:
    import yaml

    data = yaml.safe_load(text) or {}
    found = [
        step
        for job in (data.get("jobs") or {}).values()
        for step in (job or {}).get("steps") or []
        if isinstance(step, dict) and step.get("name") == step_name
    ]
    if len(found) != 1:
        raise StepShellError(f"{where}: {len(found)} steps named {step_name!r}, want exactly 1")
    return found[0]


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _block_end(lines: list[str], start: int, indent: int, stop: int) -> int:
    """The index after the last non-blank line below `start`, before `stop`,
    indented deeper than `indent` — the extent of a key's nested block."""
    end = start + 1
    for i in range(start + 1, stop):
        if not lines[i].strip():
            continue
        if _indent(lines[i]) <= indent:
            break
        end = i + 1
    return end


# --- move -------------------------------------------------------------------


def move(
    root: Path,
    workflow: str,
    step_name: str,
    script: str,
    via: str | None = None,
    envs: Iterable[tuple[str, str]] = (),
) -> str:
    """Move one step's `run: |` block to `scripts/<script>.sh` under `root`,
    leaving the delegation line in its place. Returns "moved", or "skipped"
    when the step already delegates. Writes nothing unless the result reads
    back, through `workflow_source`, to the body it moved."""
    root = Path(root)
    envs = tuple(envs)
    if not _SCRIPT_NAME.fullmatch(script):
        raise StepShellError(f"--script {script!r} must match [a-z0-9_]+")
    if via not in _LINE:
        raise StepShellError(f"--via {via!r} is not one of: pipeline-dir")
    rel = workflow_path(workflow)
    path = root / rel
    text = path.read_text()
    step = _find_step(text, step_name, rel)
    run = step.get("run")
    if not isinstance(run, str):
        raise StepShellError(f"{rel}: step {step_name!r} has no run block")
    if delegated_script(run) is not None:
        return "skipped"

    body = substitute(run, envs)
    if "${{" in body:
        left = sorted(set(re.findall(r"\$\{\{.*?\}\}", body))) or ["${{"]
        raise StepShellError(
            f"{rel}: step {step_name!r} still holds an expression after the --env "
            f"substitutions; add an --env for each: {', '.join(left)}"
        )
    if not body.endswith("\n"):
        body += "\n"
    script_rel = f"scripts/{script}.sh"
    script_text = OPENING + body
    target = root / script_rel
    if target.exists():
        raise StepShellError(f"{script_rel} already exists")

    new_text = _rewrite(text, step_name, _LINE[via].format(name=script), envs, rel)

    # The proof the move was mechanical, taken before anything is written.
    import yaml

    read_back = _find_step(
        _expand(new_text, lambda s: script_text if s == script_rel else (root / s).read_text()),
        step_name, rel,
    )
    if read_back.get("run") != body:
        raise StepShellError(f"{rel}: step {step_name!r} does not read back to its body after the move")
    if yaml.safe_load(new_text) is None:
        raise StepShellError(f"{rel}: the moved workflow does not parse")
    moved_env = _find_step(new_text, step_name, rel).get("env") or {}
    for name, expr in envs:
        if moved_env.get(name) != "${{ " + expr + " }}":
            raise StepShellError(f"{rel}: env {name} did not land on step {step_name!r}")

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(script_text)
    target.chmod(0o755)
    path.write_text(new_text)
    return "moved"


def _rewrite(text: str, step_name: str, line: str, envs: tuple, rel: str) -> str:
    """The workflow's text with the step's run block replaced by `line` and
    each --env added to the step's `env:`."""
    lines = text.splitlines(keepends=True)
    starts = [
        i for i, l in enumerate(lines)
        if (m := _STEP_NAME.match(l)) and _unquote(m.group("value")) == step_name
    ]
    if len(starts) != 1:
        raise StepShellError(f"{rel}: {len(starts)} '- name: {step_name}' lines, want exactly 1")
    first = starts[0]
    dash = _indent(lines[first])
    key = dash + 2
    stop = len(lines)
    for i in range(first + 1, len(lines)):
        if lines[i].strip() and _indent(lines[i]) <= dash:
            stop = i
            break

    def key_line(name: str) -> int | None:
        hits = [
            i for i in range(first + 1, stop)
            if _indent(lines[i]) == key and re.match(rf"\s*{name}:", lines[i])
        ]
        return hits[0] if hits else None

    run_at = key_line("run")
    if run_at is None or not re.match(r"\s*run:\s*\|[-+]?\s*$", lines[run_at]):
        raise StepShellError(f"{rel}: step {step_name!r} has no `run: |` block to move")
    run_end = _block_end(lines, run_at, key, stop)
    lines[run_at:run_end] = [" " * key + f"run: {line}\n"]
    stop -= run_end - run_at - 1

    if envs:
        env_at = key_line("env")
        if env_at is None:
            lines[run_at:run_at] = [" " * key + "env:\n"] + [
                " " * (key + 2) + f"{name}: ${{{{ {expr} }}}}\n" for name, expr in envs
            ]
        else:
            if not re.match(r"\s*env:\s*$", lines[env_at]):
                raise StepShellError(f"{rel}: step {step_name!r} has an `env:` this cannot extend")
            env_end = _block_end(lines, env_at, key, stop)
            children = [l for l in lines[env_at + 1:env_end] if l.strip()]
            child = _indent(children[0]) if children else key + 2
            existing = {
                m.group(1)
                for l in lines[env_at + 1:env_end]
                if _indent(l) == child and (m := re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*):", l))
            }
            taken = [name for name, _ in envs if name in existing]
            if taken:
                raise StepShellError(f"{rel}: step {step_name!r} already sets env {', '.join(taken)}")
            lines[env_end:env_end] = [
                " " * child + f"{name}: ${{{{ {expr} }}}}\n" for name, expr in envs
            ]
    return "".join(lines)


# --- verify -----------------------------------------------------------------


def _git_show(repo: Path, ref: str, rel: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), "show", f"{ref}:{rel}"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise StepShellError(f"cannot read {rel} at {ref}: {result.stderr.strip()}")
    return result.stdout


def verify(
    base: str,
    workflow: str,
    step_name: str,
    envs: Iterable[tuple[str, str]] = (),
    root: Path = ROOT,
    repo: Path = ROOT,
) -> list[str]:
    """What is wrong with the move of one step in the tree at `root`, compared
    with the commit `base` of `repo`'s history. Empty when the move holds:

      1. the step's `run:` is the delegation line;
      2. its `env:` holds every key the step had at `base`, plus the --env names;
      3. the script's `code_lines` equal the base block's, after the same
         --env substitution (and the script opens with the two opening lines);
      4. every `DRE-<n>` in the base block appears in the script.

    A step that already delegated at `base` is compared through its script
    there, so a move that has landed verifies against itself."""
    root, repo = Path(root), Path(repo)
    envs = tuple(envs)
    rel = workflow_path(workflow)
    base_step = _find_step(_git_show(repo, base, rel), step_name, f"{rel}@{base}")
    base_run = base_step.get("run") or ""
    base_script = delegated_script(base_run)
    base_block = _git_show(repo, base, base_script) if base_script else base_run

    step = _find_step((root / rel).read_text(), step_name, rel)
    problems: list[str] = []
    run = step.get("run") or ""
    script = delegated_script(run)
    if script is None:
        problems.append(f"{rel}: step {step_name!r} run is not a delegation line: {run.strip()[:80]!r}")
    env = step.get("env") or {}
    for name in [*(base_step.get("env") or {}), *(n for n, _ in envs)]:
        if name not in env:
            problems.append(f"{rel}: step {step_name!r} env dropped {name}")
    if script is None:
        return problems
    path = root / script
    if not path.is_file():
        return problems + [f"{script}: missing"]
    text = path.read_text()
    if not text.startswith(OPENING):
        problems.append(f"{script}: lines 1-2 are not {SHEBANG!r} and {SET_E!r}")
    want = code_lines(substitute(base_block, envs))
    got = code_lines(text)
    if got != want:
        for i, (a, b) in enumerate(zip(want, got)):
            if a != b:
                problems.append(f"{script}: code line {i + 1} differs: {a!r} at {base} → {b!r}")
                break
        else:
            problems.append(f"{script}: {len(got)} code lines, {len(want)} at {base}")
    for ref in sorted(set(_DRE.findall(base_block))):
        if not re.search(re.escape(ref) + r"(?!\d)", text):
            problems.append(f"{script}: dropped the {ref} reference")
    return problems


# --- rehearse ---------------------------------------------------------------


def rehearse(out: Path, root: Path = ROOT, moves: Iterable[Move] = REHEARSAL) -> list[str]:
    """Copy the working tree at `root` to `out` without `.git`, and apply each
    move there. A move whose step already delegates is skipped."""
    out, root = Path(out).resolve(), Path(root).resolve()
    if out == root or root in out.parents:
        raise StepShellError(f"--out {out} is inside the tree it copies")
    if out.exists() and any(out.iterdir()):
        raise StepShellError(f"--out {out} is not empty; rehearse into a fresh directory")
    shutil.copytree(root, out, ignore=shutil.ignore_patterns(".git"), dirs_exist_ok=True, symlinks=True)
    report = []
    for m in moves:
        outcome = move(out, m.workflow, m.step, m.script, via=m.via, envs=m.envs)
        report.append(f"{outcome}: {m.workflow} / {m.step} -> scripts/{m.script}.sh")
    return report


# --- the CLI ----------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="step_shell.py", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_move = sub.add_parser("move", help="move one step's run block to scripts/<name>.sh")
    p_move.add_argument("--root", required=True, type=Path)
    p_move.add_argument("--workflow", required=True)
    p_move.add_argument("--step", required=True)
    p_move.add_argument("--script", required=True)
    p_move.add_argument("--via", choices=["pipeline-dir"])
    p_move.add_argument("--env", action="append", default=[], metavar="NAME=EXPR")

    p_verify = sub.add_parser("verify", help="check one move against a commit of this checkout")
    p_verify.add_argument("--base", required=True)
    p_verify.add_argument("--root", type=Path, default=ROOT)
    p_verify.add_argument("--workflow", required=True)
    p_verify.add_argument("--step", required=True)
    p_verify.add_argument("--env", action="append", default=[], metavar="NAME=EXPR")

    p_rehearse = sub.add_parser("rehearse", help="apply the five moves to a copy of this tree")
    p_rehearse.add_argument("--out", required=True, type=Path)

    args = parser.parse_args(argv)
    try:
        if args.command == "move":
            outcome = move(args.root, args.workflow, args.step, args.script,
                           via=args.via, envs=parse_envs(args.env))
            print(f"{outcome}: {workflow_path(args.workflow)} / {args.step}")
            return 0
        if args.command == "verify":
            problems = verify(args.base, args.workflow, args.step,
                              envs=parse_envs(args.env), root=args.root)
            for problem in problems:
                print(f"::error::{problem}")
            if problems:
                return 1
            print(f"verified: {workflow_path(args.workflow)} / {args.step} against {args.base}")
            return 0
        for line in rehearse(args.out):
            print(line)
        return 0
    except (StepShellError, OSError) as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
