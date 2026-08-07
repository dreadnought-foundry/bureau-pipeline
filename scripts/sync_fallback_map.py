#!/usr/bin/env python3
"""Regenerate validate_card's fallback literal from the snapshot (stdlib only).

``config/repo-map.json`` is the canonical routing snapshot; ``validate_card.py``
carries a last-known-good copy (``_FALLBACK_REPO_MAP``) so the gate degrades
gracefully when the snapshot is unreadable. The two must byte-match
(test_repo_map_snapshot.py pins them) — but the literal used to be a hand edit,
so agent-bureau's onboarding tool, which opens a PR here updating ONLY the
JSON, could never produce a green PR: every onboarding PR was born red.

This script makes the literal a generated region, delimited by
``BEGIN generated repo mirror`` / ``END generated repo mirror`` marker comments
— the exact phrases agent-bureau's scripts/sync_repo_mirrors.py splices on, so
its onboarding tool can splice the same region cross-repo.

Usage:

    python3 scripts/sync_fallback_map.py          # rewrite the region in place
    python3 scripts/sync_fallback_map.py --check  # CI: exit 1 if stale, no write

Exit 0 → in sync (or written). Exit 1 → --check found the region stale.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "config" / "repo-map.json"
TARGET = ROOT / "scripts" / "validate_card.py"

_BEGIN = "BEGIN generated repo mirror"
_END = "END generated repo mirror"

REPAIR_COMMAND = "python3 scripts/sync_fallback_map.py"


def load_repo_map() -> dict[str, str]:
    """The canonical slug -> owner/repo map, insertion order preserved."""
    data = json.loads(SNAPSHOT.read_text())
    if not isinstance(data, dict) or not data:
        raise SystemExit(f"{SNAPSHOT} is empty or not an object")
    return {str(k): str(v) for k, v in data.items()}


def render_literal(repo_map: dict[str, str]) -> str:
    """The ``_FALLBACK_REPO_MAP = {`` ... ``}`` literal, same key order as the
    snapshot and the file's 4-space one-entry-per-line style — regenerating an
    in-sync region is a byte-for-byte no-op."""
    lines = ["_FALLBACK_REPO_MAP = {"]
    for slug, repo in repo_map.items():
        lines.append(f'    "{slug}": "{repo}",')
    lines.append("}")
    return "\n".join(lines)


def splice(text: str, body: str) -> str:
    """Replace the lines strictly between the BEGIN and END marker lines with
    ``body``, leaving the marker lines intact. Raises if the markers are
    missing — the region MUST be declared."""
    lines = text.splitlines(keepends=True)
    begin = end = None
    for i, line in enumerate(lines):
        if _BEGIN in line:
            begin = i
        elif _END in line:
            end = i
            break
    if begin is None or end is None or end <= begin:
        raise SystemExit(
            f"{TARGET}: missing '{_BEGIN}' / '{_END}' markers (cannot regenerate)"
        )
    return "".join(lines[: begin + 1]) + body + "\n" + "".join(lines[end:])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--check",
        action="store_true",
        help="verify the fallback literal matches the snapshot; exit 1 if "
        "stale (do not write).",
    )
    args = ap.parse_args(argv)

    original = TARGET.read_text()
    updated = splice(original, render_literal(load_repo_map()))
    if updated == original:
        print(f"ok:    {TARGET.relative_to(ROOT)}")
        return 0
    if args.check:
        print(
            f"STALE: {TARGET.relative_to(ROOT)} — _FALLBACK_REPO_MAP does not "
            f"match config/repo-map.json; run `{REPAIR_COMMAND}` and commit "
            "the result.",
            file=sys.stderr,
        )
        return 1
    TARGET.write_text(updated)
    print(f"wrote: {TARGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
