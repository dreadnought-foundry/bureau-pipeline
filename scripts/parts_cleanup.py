#!/usr/bin/env python3
"""The one-time count's filer: one Intake cleanup card per copy of a shared part (DRE-8843).

`file` reads the JSON `parts_check.py count --json` prints (DRE-8841) and
files one card per copy already in the repo, through the create seam
`linear_ops.create_card`, in `Intake` (`LANE`) — the front door, from which
nothing dispatches — labeled `repo:<slug>`, the `--agent` label and the
`--initiative` label. It posts no receipt comment and registers no pipeline
act.

## The identity, and why a copy is filed once

A copy's identity is (repo, part name, copy file), and the card carries it on
its own description line, `parts-copy: <slug> | <Part> | <copy_path>`
(`identity`). Before filing, ONE read lists the team's cards, in every state,
whose description holds `parts-copy: <slug>`, and each is matched on that
whole line: a card already carrying it is counted as already filed and
nothing is written. Every state on purpose — a Done or Canceled card is the
answer to "was this copy ever filed", so a second run over the same files
files nothing new, whatever became of the first run's cards. The weekly sweep
(DRE-8713) files under the same identity later.

The line is matched whole, so `<copy_path>.bak` is another copy; and read
through the escapes a Linear round trip can add — a backslash before
punctuation, the underscore included, the line set in backticks — so a stored
card is still found.

## The owner

`--owned-by <DRE-n>` names an epic already deleting these copies (in
agent-bureau, the parts refactor's fifth wave, DRE-8536). Each card then also
carries `parts-owner: <DRE-n>` and `OWNER_SENTENCE`: its card for that area
deletes the copy, so this one is canceled as a duplicate once the copy is gone.
The count still files one card per copy, so every copy has its identity on the
board and the weekly sweep agrees with it.

## The log

One stdout line per copy, in the input's order, names the card filed or the
card already found (`--dry-run`: the card it would file), then exactly one
line on stderr opens `parts-cleanup:` (`TAG`) — the copies, filed and already
filed, or `UNKNOWN` with the problem (exit 1). A log with `0 copy(ies)` ran
and found none; a log with no line never ran.

CLI:
    parts_cleanup.py file --repo <slug> --count-json <file> --agent <agent:label>
        --initiative <initiative:label> [--owned-by <DRE-n>] [--dry-run]
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import linear_ops  # noqa: E402 — the create seam and the read

#: Opens the one stderr line, and every per-copy stdout line.
TAG = "parts-cleanup:"
#: Where every card is filed: the front door, from which nothing dispatches.
LANE = "Intake"
#: Opens the description line carrying the copy's identity.
IDENTITY_MARK = "parts-copy:"
#: Opens the description line naming the epic already deleting the copy.
OWNER_MARK = "parts-owner:"
#: What the `--agent` and `--initiative` labels open with.
AGENT_PREFIX = "agent:"
INITIATIVE_PREFIX = "initiative:"
#: What a card under `--owned-by` says of its owner, word for word.
OWNER_SENTENCE = ("This copy is in the scope of {owner}, which is deleting it; do not "
                  "build this card on its own, and cancel it as a duplicate once the copy "
                  "is gone from the default branch.")

#: The fields each copy in the count JSON must carry.
TEXT_FIELDS = ("part", "part_path", "copy_path")
LINE_FIELDS = ("part_line", "copy_line")

_OWNER = re.compile(r"^DRE-\d+$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_ESCAPE = re.compile(r"\\([^A-Za-z0-9\s])")


class Problem(Exception):
    """Why the run cannot answer: printed as the `UNKNOWN` line."""


def _clean(text) -> str:
    """Untrusted file text, written onto a card: one line, no verdict marker."""
    import merge_gate  # noqa: PLC0415 — lazy: it imports linear_ops

    return merge_gate.without_verdict_markers(_CONTROL.sub(" ", str(text))).strip()


def identity(slug: str, copy: dict) -> str:
    return f"{IDENTITY_MARK} {slug} | {copy['part']} | {copy['copy_path']}"


def title(slug: str, copy: dict) -> str:
    return f"{slug}: delete the copy of {copy['part']} in {copy['copy_path']}"


def description(slug: str, copy: dict, owner: str | None = None) -> str:
    layer = f" ({copy['layer']})" if copy.get("layer") else ""
    lines = [
        f"`{copy['part']}` is a shared part of {slug}{layer}: it ships at "
        f"`{copy['part_path']}:{copy['part_line']}`. A copy of it is declared at "
        f"`{copy['copy_path']}:{copy['copy_line']}`.",
        "",
        "The fix is to use the shared part and delete the copy. A copy is deleted, not "
        "deprecated (`standards/design-system.md`, rules 2 and 3).",
        "",
        "Filed by the one-time count of copies of shared parts "
        "(`scripts/parts_cleanup.py`, DRE-8843). The weekly sweep files under the same "
        "identity, so this copy is only ever filed once.",
        "",
        identity(slug, copy),
    ]
    if owner:
        lines += [f"{OWNER_MARK} {owner}", OWNER_SENTENCE.format(owner=owner)]
    return "\n".join(lines) + "\n"


def read_copies(path: str, slug: str) -> list:
    """The copies of the count JSON at `path`, in its order, each field cleaned."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise Problem(f"the count JSON {path} cannot be read: {exc}") from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("copies"), list):
        raise Problem(f"the count JSON {path} carries no `copies` list")
    counted = doc.get("repo")
    if counted is not None and counted != slug:
        raise Problem(f"the count JSON {path} counted {counted!r}, not {slug!r}")
    copies = []
    for i, raw in enumerate(doc["copies"]):
        if not isinstance(raw, dict):
            raise Problem(f"copy {i} in {path} is not an object")
        for name in TEXT_FIELDS:
            if not isinstance(raw.get(name), str) or not _clean(raw[name]):
                raise Problem(f"copy {i} in {path} carries no `{name}`")
        for name in LINE_FIELDS:
            value = raw.get(name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise Problem(f"copy {i} in {path} carries no line number `{name}`")
        copy = {name: _clean(raw[name]) for name in TEXT_FIELDS}
        copy.update({name: raw[name] for name in LINE_FIELDS})
        layer = raw.get("layer")
        copy["layer"] = _clean(layer) if isinstance(layer, str) else ""
        copies.append(copy)
    return copies


def _unescaped(line: str) -> str:
    return _ESCAPE.sub(r"\1", line.strip().strip("`").strip())


def filed(slug: str, mark: str = IDENTITY_MARK) -> dict:
    """Identity line → the OLDEST card carrying it, in any state, among the
    team's cards whose description holds `<mark> <slug>` — `parts-copy:` here,
    `parts-rejected:` for `parts_provisional.py`'s replacement cards (DRE-8941)."""
    nodes = linear_ops.gql_paged(
        """query($needle: String!, $after: String) {
             issues(first: 100, after: $after, filter: {
               team: {key: {eq: "DRE"}},
               description: {contains: $needle}
             }) { nodes { identifier createdAt description }
                  pageInfo { hasNextPage endCursor } } }""",
        {"needle": f"{mark} {slug}"},
    )
    found = {}
    for node in sorted(nodes, key=lambda n: n.get("createdAt") or ""):
        for line in (node.get("description") or "").splitlines():
            found.setdefault(_unescaped(line), node.get("identifier") or "")
    return found


def _create(named: str, body: str, slug: str, agent: str, initiative: str) -> dict:
    """One card in `LANE`, through the create seam. The guard is the labels'
    bound, read by `label_writers.py check` as well as at run time."""
    if agent.startswith(AGENT_PREFIX) and initiative.startswith(INITIATIVE_PREFIX):
        # create_card prints its own receipt; stdout is one line per copy.
        with redirect_stdout(io.StringIO()):
            return linear_ops.create_card(named, body, repo_slug=slug,
                                          labels=(agent, initiative), lane=LANE)
    raise Problem(f"{agent!r} or {initiative!r} is not an `{AGENT_PREFIX}` or "
                  f"`{INITIATIVE_PREFIX}` label")


def run(args) -> tuple:
    """`(stdout lines, summary)` for one `file` run; `Problem` when it cannot answer."""
    if args.owned_by is not None and not _OWNER.match(args.owned_by):
        raise Problem(f"--owned-by {args.owned_by!r} is not a card id (DRE-<n>)")
    for flag, value, prefix in (("--agent", args.agent, AGENT_PREFIX),
                                ("--initiative", args.initiative, INITIATIVE_PREFIX)):
        if not value.startswith(prefix) or not value[len(prefix):].strip():
            raise Problem(f"{flag} {value!r} is not an `{prefix}` label")
    try:
        linear_ops.validated_repo_slug(args.repo)
    except linear_ops.LinearError as exc:
        raise Problem(str(exc)) from exc
    copies = read_copies(args.count_json, args.repo)
    known = filed(args.repo) if copies else {}
    lines, made, already = [], 0, 0
    for copy in copies:
        key, named = identity(args.repo, copy), title(args.repo, copy)
        if known.get(key):
            already += 1
            lines.append(f"{TAG} already filed {known[key]} — {named}")
            print(lines[-1])
            continue
        made += 1
        if args.dry_run:
            lines.append(f"{TAG} would file — {named}")
        else:
            issue = _create(named, description(args.repo, copy, args.owned_by),
                            args.repo, args.agent, args.initiative)
            known[key] = issue["identifier"]
            lines.append(f"{TAG} filed {issue['identifier']} — {named}")
        print(lines[-1])
    summary = (f"{TAG} {args.repo} — {len(copies)} copy(ies), {made} filed, "
               f"{already} already filed")
    return lines, summary


def cmd_file(args) -> int:
    try:
        _, summary = run(args)
    except Exception as exc:  # the run always answers
        problem = str(exc) if isinstance(exc, Problem) else f"{type(exc).__name__}: {exc}"
        print(f"{TAG} {args.repo} — UNKNOWN — {_CONTROL.sub(' ', problem)}", file=sys.stderr)
        return 1
    print(summary, file=sys.stderr)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    filing = sub.add_parser("file", help="file one Intake cleanup card per copy")
    filing.add_argument("--repo", required=True)
    filing.add_argument("--count-json", required=True)
    filing.add_argument("--agent", required=True)
    filing.add_argument("--initiative", required=True)
    filing.add_argument("--owned-by", default=None)
    filing.add_argument("--dry-run", action="store_true")
    filing.set_defaults(func=cmd_file)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
