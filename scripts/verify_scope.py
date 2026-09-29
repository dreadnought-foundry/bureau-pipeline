#!/usr/bin/env python3
"""The Verifier's scope gate: does this pull request get a behavioral Verifier?

    python3 scripts/verify_scope.py --card DRE-N --changed <file-list>

Prints `in_scope=true|false` and `is_ui=true|false` on stdout, in the shape
`$GITHUB_OUTPUT` takes, and one human line on stderr naming each signal.
`verify.yml`'s `scope` step is the caller.

In scope when ANY of three signals holds:

- **design** — the card description carries a `**Design:**` line.
- **ui_role** — the card wears a UI role label (`UI_ROLE_LABELS`) AND the diff
  touches the frontend bucket (DRE-4389).
- **multi** — the changed files span two or more system buckets.

## Why a label, and not only the Design line (DRE-4389)

`standards/card-quality.md` says `**Design:**` names an exported screen PNG to
build to, and FORBIDS it on a card with none. A card fixing what an existing
component renders — an interaction bug, a state-rendering fix, accessibility,
copy, spacing — has no new screen, so it rightly carries no Design line and is
rightly UI work. Reading the Design line as "is this UI?" skipped exactly
those: agent-bureau PR #2652 changed the One River liveness pulse, three
frontend files on a card labelled `agent:frontend` and `ux`, and went to merge
on one reviewer. The role label is the card's own authoritative say.

The label alone is not enough: it says who built the card, and the diff says
what changed. A UI-role card whose diff never reaches the frontend bucket has
nothing new to render, so it stays out.

## Degrade, never block

A card that cannot be read — no card id, Linear unreachable, an empty
description — contributes NO signal, so a single-bucket diff SKIPs. The
multi-system signal needs only the diff and is unchanged. The workflow step
treats a crash of this script as out of scope too: a skipped Verifier posts no
verdict and gates nothing, where a failed one would strand the pull request.

The bucket globs are heuristic and moved here from `verify.yml` unchanged; they
are still the first thing to tune against a real repo at kickoff.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# The system buckets, verbatim from the inline gate this replaced.
BUCKETS: dict[str, list[str]] = {
    "backend":   [r"backend/", r"/graphql/", r"/api/", r"schema\.py$", r"resolvers"],
    "frontend":  [r"web/", r"\.tsx?$", r"/components/", r"/pages/"],
    "proxy":     [r"proxy\.(ts|mjs|js)$", r"build-proxy"],
    "migration": [r"alembic", r"migrations?/", r"models?/"],
    "infra":     [r"infra/", r"\.github/", r"cdk", r"Dockerfile", r"\.ya?ml$"],
}

# Labels that say a card is UI work. `agent:frontend` is the roster's frontend
# role (agents.yaml); `ux` and `ui` are the board's UI labels. Compared
# case-insensitively.
UI_ROLE_LABELS = frozenset({"agent:frontend", "ux", "ui"})

_DESIGN_LINE = re.compile(r"^\s*\*\*Design:\*\*", re.IGNORECASE | re.MULTILINE)

_CARD_QUERY = """query($id: String!) {
  issue(id: $id) { description labels { nodes { name } } }
}"""


@dataclass(frozen=True)
class Decision:
    in_scope: bool
    design: bool
    ui_role: bool
    multi: bool
    buckets: set[str] = field(default_factory=set)

    @property
    def is_ui(self) -> bool:
        return self.design or self.ui_role


def buckets_hit(changed: list[str]) -> set[str]:
    hit: set[str] = set()
    for f in changed:
        f = f.strip()
        if not f:
            continue
        for name, pats in BUCKETS.items():
            if any(re.search(p, f) for p in pats):
                hit.add(name)
    return hit


def decide(changed: list[str], card: dict | None) -> Decision:
    """The scope decision. `card` is `read_card`'s result — None when unreadable."""
    hit = buckets_hit(changed)
    multi = len(hit) >= 2
    design = ui_role = False
    if card:
        design = bool(_DESIGN_LINE.search(card.get("description") or ""))
        labels = {(name or "").strip().lower() for name in card.get("labels") or []}
        ui_role = bool(labels & UI_ROLE_LABELS) and "frontend" in hit
    return Decision(
        in_scope=design or ui_role or multi,
        design=design,
        ui_role=ui_role,
        multi=multi,
        buckets=hit,
    )


def read_card(identifier: str, *, gql=None) -> dict | None:
    """`{"description", "labels"}` for a card, or None when it cannot be read.

    None for no id, any read failure, a missing issue, or an empty description
    — every one of them is "no card signal", never an error that blocks.
    """
    if not identifier:
        return None
    try:
        if gql is None:
            import linear_ops

            gql = linear_ops.gql
        issue = (gql(_CARD_QUERY, {"id": identifier}) or {}).get("issue")
    except Exception as exc:  # noqa: BLE001 — an unreadable card is no signal
        print(f"verify-scope: could not read {identifier} ({exc})", file=sys.stderr)
        return None
    if not issue or not (issue.get("description") or "").strip():
        return None
    return {
        "description": issue["description"],
        "labels": [
            n.get("name") or ""
            for n in ((issue.get("labels") or {}).get("nodes") or [])
        ],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--card", default="", help="card id, e.g. DRE-4389 (empty = none)")
    ap.add_argument("--changed", required=True, help="file with one changed path per line")
    args = ap.parse_args(argv)

    try:
        changed = Path(args.changed).read_text().splitlines()
    except OSError:
        changed = []
    card = read_card(args.card)
    d = decide(changed, card)
    tf = lambda b: "true" if b else "false"  # noqa: E731
    print(f"in_scope={tf(d.in_scope)}")
    print(f"is_ui={tf(d.is_ui)}")
    print(
        f"scope decision: in_scope={tf(d.in_scope)} (design={tf(d.design)} "
        f"ui_role={tf(d.ui_role)} multi={tf(d.multi)} "
        f"card={'read' if card else 'unreadable'} "
        f"buckets={','.join(sorted(d.buckets)) or 'none'})",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
