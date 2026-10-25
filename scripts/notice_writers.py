#!/usr/bin/env python3
"""Every writer that files a Linear card, declared as notice or kept (DRE-7452).

The CEO asked for the writers that file notice cards to be repointed
ENUMERATED rather than fixed one at a time. `config/notice-writers.json` is the
enumeration: one row per writer that creates a Linear card automatically, each
with its `id`, `repo`, `file`, `title` (the title pattern it files),
`disposition` and `reason`.

* `notice` — this epic repoints it to a console alert, and the row carries its
  `notice_key` from the epic's contract (a list when one writer files two).
* `kept` — it stays a card, and `reason` opens with why: `work`, `decision` or
  `record`, optionally followed by `: <the reason in words>`.

A row may name a `site`: the Python function, or the workflow step by its
`name:`, that files its card. `report` then reads that site alone, so a file
that also holds kept writers (`reconcile.py`) can read as repointed.

## check — discovered, never listed

`check [--agent-bureau-dir <path>]` reads every `.py`, `.sh`, `.yml` and
`.yaml` file in the bureau-pipeline checkout this script sits in and, when the
flag is given, in that agent-bureau checkout too — outside `.git/`,
`.bureau-pipeline/`, `node_modules/`, any `tests/` directory and any
`test_*.py` file. A file is card-creating when a line that is not a `#`
comment holds any of three things:

1. a call of `create_card`, `_create_card`, `cmd_oneoff`, `cmd_subissue` or
   `cmd_create`, each a whole name followed by an open parenthesis — so
   `cmd_create_label` is not one;
2. in a workflow or shell file, a line invoking `linear_ops.py create`,
   `oneoff` or `subissue` — `create-label` is not one;
3. a raw GraphQL `issueCreate` mutation followed by its parenthesis, which is
   how agent-bureau's console writes cards without `linear_ops`.

It exits 1 naming every card-creating file no row declares for its repo, and
every malformed row. A writer nobody remembered fails by its own file.

What it does not see: bureau-pipeline's CI runs it without the flag, because CI
checks out one repository, so a card-filing writer added to agent-bureau later
is caught only the next time someone runs `check --agent-bureau-dir`. A row
whose file no longer exists, or no longer creates a card, is not a failure: a
repointed writer keeps its row, and `scripts/pipeline_notice.py`'s fallback is
declared before the client lands.

## report — a reading, not a gate

`report [--agent-bureau-dir <path>]` prints one line per `notice` row:

* `notice-writers: <id> repointed` — the row's file (its site, when it names
  one) calls the `pipeline_notice` client with the row's notice key, every
  literal part of it outside its `<placeholders>`, and holds no card-creating
  line any more;
* `notice-writers: <id> still-files-cards` — otherwise;
* `notice-writers: <id> unread` — an agent-bureau row with no
  `--agent-bureau-dir`, or a file or site that cannot be read.

It always exits 0, because the repoint cards land one by one.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "notice-writers.json"

PIPELINE = "bureau-pipeline"
AGENT_BUREAU = "agent-bureau"
REPOS = (PIPELINE, AGENT_BUREAU)

READ_SUFFIXES = (".py", ".sh", ".yml", ".yaml")
SHELL_SUFFIXES = (".sh", ".yml", ".yaml")
SKIP_DIRS = frozenset({".git", ".bureau-pipeline", "node_modules", "tests"})

DISPOSITIONS = ("notice", "kept")
KEPT_REASONS = ("work", "decision", "record")
#: The contract's notice-key shape, read once each `<placeholder>` is filled.
KEY_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,120}$")
PLACEHOLDER_RE = re.compile(r"<[^<>]*>")

CALL_RE = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"(create_card|_create_card|cmd_oneoff|cmd_subissue|cmd_create)\s*\(")
SHELL_RE = re.compile(
    r"linear_ops\.py[\"']?\s+(create|oneoff|subissue)(?![A-Za-z0-9_-])")
GRAPHQL_RE = re.compile(r"issueCreate\s*\(")
CLIENT_RE = re.compile(r"pipeline_notice\.py|\b(?:raise|resolve)_notice\s*\(")


def code_lines(text: str) -> list[str]:
    """The lines that are not a `#` comment."""
    return [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]


def card_forms(text: str, suffix: str) -> list[str]:
    """The card-creating forms `text` holds, by name, in order of the rules."""
    found: list[str] = []
    for line in code_lines(text):
        for m in CALL_RE.finditer(line):
            found.append(f"{m.group(1)}(")
        if suffix in SHELL_SUFFIXES:
            for m in SHELL_RE.finditer(line):
                found.append(f"linear_ops.py {m.group(1)}")
        if GRAPHQL_RE.search(line):
            found.append("issueCreate" + "(")  # split, so this line is not one
    return sorted(set(found), key=found.index)


def read_paths(root: Path) -> list[Path]:
    """Every file `check` reads under `root`, relative and sorted. A skipped
    directory is never entered, so agent-bureau's `node_modules/` costs
    nothing."""
    out: list[Path] = []
    for here, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            if not name.endswith(READ_SUFFIXES):
                continue
            if name.endswith(".py") and name.startswith("test_"):
                continue
            out.append((Path(here) / name).relative_to(root))
    return sorted(out)


def discover(root: Path) -> dict[str, list[str]]:
    """Every card-creating file under `root`: its posix path → its forms."""
    out: dict[str, list[str]] = {}
    for rel in read_paths(root):
        try:
            text = (root / rel).read_text(errors="replace")
        except OSError:
            continue
        forms = card_forms(text, rel.suffix)
        if forms:
            out[rel.as_posix()] = forms
    return out


# --- the declared list --------------------------------------------------------


def load(path: Path = CONFIG) -> list[dict]:
    data = json.loads(path.read_text())
    rows = data.get("writers") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise ValueError("no `writers` list")
    return rows


def as_list(value) -> list:
    return value if isinstance(value, list) else [value]


def row_problems(rows: list[dict]) -> list[str]:
    problems: list[str] = []
    seen: set[str] = set()
    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            problems.append(f"malformed row #{i} — not an object")
            continue
        wid = r.get("id")
        why: list[str] = []
        if not isinstance(wid, str) or not wid.strip():
            wid = f"#{i}"
            why.append("no id")
        elif wid in seen:
            problems.append(f"duplicate id {wid}")
        seen.add(wid)
        if r.get("repo") not in REPOS:
            why.append(f"repo must be one of {', '.join(REPOS)}")
        file = r.get("file")
        if not isinstance(file, str) or not file or file.startswith("/"):
            why.append("file must be a path relative to its repo")
        titles = as_list(r.get("title"))
        if not titles or not all(isinstance(t, str) and t.strip()
                                 for t in titles):
            why.append("title must be a pattern, or a list of them")
        disposition = r.get("disposition")
        reason = r.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            why.append("no reason")
        if disposition not in DISPOSITIONS:
            why.append(f"disposition must be {' or '.join(DISPOSITIONS)}")
        elif disposition == "kept":
            if isinstance(reason, str) and reason.split(":")[0].strip() \
                    not in KEPT_REASONS:
                why.append(f"a kept row's reason opens with "
                           f"{', '.join(KEPT_REASONS)}")
            if "notice_key" in r:
                why.append("a kept row carries no notice_key")
        else:
            keys = as_list(r.get("notice_key"))
            if not keys or not all(
                    isinstance(k, str)
                    and KEY_RE.match(PLACEHOLDER_RE.sub("x", k)) for k in keys):
                why.append("a notice row carries the contract's notice_key")
        site = r.get("site")
        if site is not None and (not isinstance(site, str) or not site.strip()):
            why.append("site must name a function or a step")
        if why:
            problems.append(f"malformed row {wid} — {'; '.join(why)}")
    return problems


def declared(rows: list[dict]) -> set[tuple[str, str]]:
    return {(r.get("repo"), r.get("file")) for r in rows if isinstance(r, dict)}


def trees(agent_bureau_dir: Path | None) -> dict[str, Path]:
    out = {PIPELINE: ROOT}
    if agent_bureau_dir is not None:
        out[AGENT_BUREAU] = agent_bureau_dir
    return out


def check(agent_bureau_dir: Path | None) -> int:
    try:
        rows = load()
    except (OSError, ValueError) as e:
        print(f"notice-writers: config unreadable — {CONFIG.name}: {e}")
        return 1
    problems = row_problems(rows)
    known = declared(rows)
    total = 0
    for repo, root in trees(agent_bureau_dir).items():
        for rel, forms in discover(root).items():
            total += 1
            if (repo, rel) not in known:
                problems.append(
                    f"undeclared {repo}:{rel} — holds {', '.join(forms)}; "
                    f"declare it in config/{CONFIG.name} as notice or kept")
    for p in problems:
        print(f"notice-writers: {p}")
    if problems:
        print(f"notice-writers: {len(problems)} problem(s) — every writer "
              "that files a Linear card is declared, notice or kept")
        return 1
    read = " and ".join(trees(agent_bureau_dir))
    print(f"notice-writers: {total} card-creating file(s) in {read}, "
          "every one declared")
    return 0


# --- report -------------------------------------------------------------------


def python_site(text: str, name: str) -> str | None:
    """The source of the function `name` in `text`, or None."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    lines = text.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            return "\n".join(lines[node.lineno - 1:node.end_lineno])
    return None


def workflow_site(text: str, name: str) -> str | None:
    """The lines of the step whose `name:` is `name`, or None."""
    lines = text.splitlines()
    head = re.compile(r"^(\s*)-\s+name:\s*(.*?)\s*$")
    for i, line in enumerate(lines):
        m = head.match(line)
        if not m or m.group(2).strip("\"'") != name:
            continue
        indent = len(m.group(1))
        end = i + 1
        while end < len(lines):
            nxt = lines[end]
            if nxt.strip() and len(nxt) - len(nxt.lstrip()) <= indent:
                break
            end += 1
        return "\n".join(lines[i:end])
    return None


def scope(text: str, suffix: str, site: str | None) -> str | None:
    if site is None:
        return text
    if suffix == ".py":
        return python_site(text, site)
    if suffix in (".yml", ".yaml"):
        return workflow_site(text, site)
    return None


def calls_client_with(text: str, keys: list[str]) -> bool:
    code = "\n".join(code_lines(text))
    if not CLIENT_RE.search(code):
        return False
    return all(part in code
               for key in keys
               for part in PLACEHOLDER_RE.split(key) if part)


def reading(r: dict, roots: dict[str, Path]) -> str:
    root = roots.get(r["repo"])
    if root is None:
        return "unread"
    path = root / r["file"]
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return "unread"
    text = scope(text, path.suffix, r.get("site"))
    if text is None:
        return "unread"
    if calls_client_with(text, as_list(r["notice_key"])) \
            and not card_forms(text, path.suffix):
        return "repointed"
    return "still-files-cards"


def report(agent_bureau_dir: Path | None) -> int:
    try:
        rows = load()
    except (OSError, ValueError) as e:
        print(f"notice-writers: config unreadable — {CONFIG.name}: {e}",
              file=sys.stderr)
        return 0
    roots = trees(agent_bureau_dir)
    for r in rows:
        if not isinstance(r, dict) or r.get("disposition") != "notice":
            continue
        try:
            outcome = reading(r, roots)
        except (KeyError, TypeError):
            outcome = "unread"
        print(f"notice-writers: {r.get('id')} {outcome}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, help_ in (
        ("check", "exit 1 naming any card-creating file no row declares"),
        ("report", "read each notice row: repointed, still-files-cards, unread"),
    ):
        p = sub.add_parser(name, help=help_)
        p.add_argument("--agent-bureau-dir", type=Path, default=None,
                       help="an agent-bureau checkout to read as well")
    args = ap.parse_args(argv)
    ab = args.agent_bureau_dir
    if ab is not None:
        ab = ab.resolve()
        if not ab.is_dir():
            print(f"notice-writers: --agent-bureau-dir {ab} is not a directory",
                  file=sys.stderr)
            # A check that cannot read the tree it was asked to is no pass;
            # the report reads its agent-bureau rows as unread instead.
            if args.cmd == "check":
                return 2
            ab = None
    return check(ab) if args.cmd == "check" else report(ab)


if __name__ == "__main__":
    sys.exit(main())
