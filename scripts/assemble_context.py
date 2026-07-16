#!/usr/bin/env python3
"""Assemble an agent's context: role brief + the role's shared standards (DRE-1646).

The build agents run HEADLESS via claude-code-action and cannot load Claude Code
Skills, so the briefs (`briefs/<role>.md`) and the canonical `standards/*.md`
layer are their only knowledge channel. This script is the single place that
knows, per role, WHICH standards an agent must act on — it concatenates the
relevant standards plus the role brief into one context blob the workflow writes
to a file and points the agent at (or, with `paths`, just lists the files for a
"Read these first" prompt).

Why a script and not inline YAML:
  - DRY: the per-role mapping lives here once, not duplicated across seven
    workflows (and GitHub Actions has no YAML anchors to share it).
  - Run-time read: the standards/brief text is NOT copied into the workflow —
    this reads the files from the repo when the step runs. Because product repos
    consume the pipeline `@main`, editing a standard propagates to every repo's
    agents on the next run with NO workflow change.
  - Testable: the mapping is a pure dict; `assemble()` is pure given a reader.

Every agent gets `standards/comms.md` (the CEO-facing voice) and
`standards/untrusted-content.md` (card/comment/PR text is data, not
instructions — DRE-1989). Each role then adds its task-specific standards, in
the order below. The role brief is appended LAST so role-specific detail has
the final word over the shared base.

CLI:
  assemble <role> [--root DIR]   print the concatenated context blob to stdout
  paths    <role> [--root DIR]   print the ordered file paths (one per line)
  roles                          print the known role names (one per line)

--root is where the pipeline checkout lives relative to the agent's CWD. In CI
the product repo is the CWD and this repo is checked out at `.bureau-pipeline`,
so --root defaults to `.bureau-pipeline`. In this repo's own tests --root is the
repo root (".").
"""

from __future__ import annotations

import argparse
import os
import sys

# --- The contract: role → ordered standards (single source of truth) ---------
#
# Keys are the role names the workflows pass (validate_card._role_from_labels
# emits engineer/frontend/devops; plan/critic/verifier/fix/medic are named by
# their workflow). `standards/comms.md` and `standards/untrusted-content.md`
# are added to EVERY role by assemble(), so they are intentionally NOT repeated
# in these lists — list only the role-specific additions. Order is the order
# the agent reads them in.
ROLE_STANDARDS: dict[str, list[str]] = {
    # Implementation agents build to the engineering floor + the system shape,
    # against the card contract, and report in the CEO's voice. They also
    # answer the vendor-behavior premortem before touching an external
    # trigger/event/command (DRE-2105 — the 2026-07-12 boundary lessons),
    # and build console state surfaces honestly (DRE-2107 — badges derive
    # from what actually happened, never from adjacent signals).
    "engineer": ["engineering.md", "architecture.md", "card-quality.md", "vendor-boundaries.md", "console-honesty.md"],
    # Frontend is the engineer in web mode + the design-fidelity standard.
    "frontend": ["engineering.md", "architecture.md", "card-quality.md", "design.md", "vendor-boundaries.md", "console-honesty.md"],
    # DevOps shares the engineer set (infra is code; same discipline + shape)
    # minus console-honesty — it authors CDK/CI/migrations, not console
    # state surfaces.
    "devops": ["engineering.md", "architecture.md", "card-quality.md", "vendor-boundaries.md"],
    # The planner authors cards (card-quality), sizes them against the
    # engineering floor, writes plan comments the CEO reads (comms), and
    # bakes the vendor-boundary answers into boundary-touching cards.
    "planner": ["card-quality.md", "engineering.md", "vendor-boundaries.md"],
    # The critic reviews diffs AGAINST the engineering + architecture
    # standards — walks the vendor-boundaries checklist on boundary-touching
    # PRs and the console-honesty rules on console cards; its verdict voice
    # is comms.
    "critic": ["engineering.md", "architecture.md", "vendor-boundaries.md", "console-honesty.md"],
    # The verifier proves the feature works (comms for its verdict) and checks
    # UI against the design standard.
    "verifier": ["design.md"],
    # The fixing agent and the medic work to the engineering floor and report
    # in the CEO's voice.
    "fix": ["engineering.md"],
    "medic": ["engineering.md"],
}

# Every agent reads this first — the CEO-facing voice for any message it posts.
COMMS = "comms.md"

# ...and this second — card/comment/PR text is untrusted data, never
# instructions (DRE-1989). Universal because EVERY role reads text authored
# outside the trust boundary (card bodies, PR comments, commit messages).
UNTRUSTED = "untrusted-content.md"

# Brief file per role, relative to the repo root (mirrors agents.yaml briefPath).
# Roles without a brief (critic, fix, medic) get None — standards only.
ROLE_BRIEF: dict[str, str | None] = {
    "engineer": "engineer.md",
    "frontend": "frontend.md",
    "devops": "devops.md",
    "planner": "planner.md",
    "verifier": "verifier.md",
    "critic": None,
    "fix": None,
    "medic": None,
}


def standards_for(role: str) -> list[str]:
    """Ordered standards filenames for a role: comms, then untrusted-content,
    then role-specific.

    Raises KeyError for an unknown role — callers must pass a known role so a
    typo fails loudly rather than silently dropping every standard.
    """
    extra = ROLE_STANDARDS[role]
    return [COMMS, UNTRUSTED, *extra]


def context_paths(role: str, root: str = ".bureau-pipeline") -> list[str]:
    """Ordered list of files an agent should read for a role, repo-CWD-relative:
    the standards (comms + role set) then the role brief (if any)."""
    paths = [os.path.join(root, "standards", s) for s in standards_for(role)]
    brief = ROLE_BRIEF[role]
    if brief:
        paths.append(os.path.join(root, "briefs", brief))
    return paths


def assemble(role: str, read) -> str:
    """Concatenate the role's context from a `read(path)->str` function.

    Pure given `read` (the CLI passes a real file reader; tests pass a stub), so
    the mapping + ordering + section framing are unit-testable with no I/O.
    Each file is wrapped in a labeled fence so the agent can tell the standards
    apart from the brief and from each other.
    """
    out: list[str] = [
        "# Agent context — shared standards + role brief (assembled by the "
        "pipeline; do not edit inline).",
        "# These are your operating rules. The standards are the canonical "
        "shared base (single source of truth, consumed @main); the role brief "
        "adds role-specific detail and has the final word.",
        "",
    ]
    for path in context_paths(role):
        # Label by the bare filename so the section headers are stable
        # regardless of --root (e.g. "standards/comms.md", "briefs/engineer.md").
        label = "/".join(path.split(os.sep)[-2:])
        out.append(f"===== BEGIN {label} =====")
        out.append(read(path).rstrip("\n"))
        out.append(f"===== END {label} =====")
        out.append("")
    return "\n".join(out).rstrip("\n") + "\n"


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def _resolve_paths(role: str, root: str) -> list[str]:
    """context_paths but anchored at an arbitrary --root for the CLI."""
    paths = [os.path.join(root, "standards", s) for s in standards_for(role)]
    brief = ROLE_BRIEF[role]
    if brief:
        paths.append(os.path.join(root, "briefs", brief))
    return paths


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("assemble", help="print the concatenated context blob")
    a.add_argument("role")
    a.add_argument("--root", default=".bureau-pipeline")

    p = sub.add_parser("paths", help="print the ordered file paths")
    p.add_argument("role")
    p.add_argument("--root", default=".bureau-pipeline")

    sub.add_parser("roles", help="print the known role names")

    args = ap.parse_args(argv)

    if args.cmd == "roles":
        for r in ROLE_STANDARDS:
            print(r)
        return 0

    role = args.role
    if role not in ROLE_STANDARDS:
        known = ", ".join(ROLE_STANDARDS)
        print(f"unknown role {role!r}; known roles: {known}", file=sys.stderr)
        return 2

    if args.cmd == "paths":
        for path in _resolve_paths(role, args.root):
            print(path)
        return 0

    # assemble — read from --root, reusing the pure assembler with a root-aware
    # reader so the section labels stay root-independent.
    paths = _resolve_paths(role, args.root)
    by_label = {"/".join(p.split(os.sep)[-2:]): p for p in paths}

    def read(rel: str) -> str:
        label = "/".join(rel.split(os.sep)[-2:])
        return _read(by_label[label])

    sys.stdout.write(assemble(role, read))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
