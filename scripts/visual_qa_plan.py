#!/usr/bin/env python3
"""Decide whether — and for which screens — the visual-QA critic stage runs.

DRE-1481, layer 3b of DRE-1477 (the "did the build execute the design?" gate).

This is the SAFE decision core for qa-review.yml's visual stage. It answers one
question, deterministically and with stdlib only, so it is unit-testable away
from CI:

    "For this PR, should we render screenshots and have the critic compare them
     against the design — and if so, which screens?"

It NEVER blocks a merge itself. It only emits a plan. The blocking finding (a
material visual mismatch) is the critic's call, made AFTER it has both images in
hand. This file's whole job is to keep that stage from EVER firing — let alone
blocking — when it shouldn't, because a false block stalls the entire bureau.

The hard safety rules (mirrored from the card + the operator brief):

  * No `**Design:**` ref in the card/PR text  → SKIP. Absence is never a defect.
  * No UI files changed                        → SKIP.
  * A Design ref that names no mapped screen   → SKIP that ref (a stray path is
    not a reason to block); only mapped, affected screens are shot.
  * On ANY uncertainty (unparseable screen map, no affected screen resolved) →
    SKIP. The gate errs toward ALLOWING the merge.

Screen map source of truth: the console harness's own map
(console/web/scripts/visual-qa/screens.mjs, DRE-1480). We parse it at runtime
from the checked-out product repo so the keys/paths stay a single contract — we
never fork the map into Python. If the map can't be parsed, we SKIP (degrade),
we do not guess.

CLI:

    python3 visual_qa_plan.py \
        --card-text-file <path>      # card description (may be empty/missing)
        --pr-body-file   <path>      # PR body (also scanned for **Design:**)
        --changed-file   <path>      # newline list of changed paths (git diff)
        --screens-mjs    <path>      # console/web/scripts/visual-qa/screens.mjs
        [--ui-prefix console/web/src/]   # what counts as a UI change

Prints a JSON plan to stdout:

    {"run": false, "reason": "...", "screens": [], "designs": []}
    {"run": true,  "reason": "...",
     "screens": ["board", "agents"],
     "designs": ["console/design/images/screens/desktop/kanban-planning-board.png", ...]}

`run:false` is the safe default and is emitted on every skip/degrade path.
Exit code is always 0 — a planner crash must not wedge the gate (the caller
treats a non-JSON / missing plan as SKIP too).
"""

from __future__ import annotations

import argparse
import json
import re
import sys

# Where the console harness writes/reads from (must match screens.mjs).
SCREENS_DIR = "console/design/images/screens"
DEFAULT_UI_PREFIX = "console/web/src/"

# `**Design:** <path>[, <path> ...]` — one or more screen PNG paths on the line.
# Reuses the DRE-1478 convention (briefs/planner.md). Case-insensitive on the
# label; tolerant of surrounding markdown (links, backticks, trailing text).
_DESIGN_LINE = re.compile(r"^\s*\*\*Design:\*\*\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE)
# A design artifact path: anything ending in .png under the screens dir. We pull
# these out of the Design line tolerantly (commas, backticks, markdown links).
_PNG_PATH = re.compile(r"(console/design/images/screens/[A-Za-z0-9._/-]+?\.png)")


def parse_design_refs(*texts: str) -> list[str]:
    """Every design PNG path referenced on a `**Design:**` line, de-duped.

    Only paths under the screens dir count — a `**Design:**` line that points
    somewhere else (a Figma URL, a prose note) yields no screenshot targets and
    therefore no block. Order preserved; duplicates dropped.
    """
    out: list[str] = []
    seen: set[str] = set()
    for text in texts:
        if not text:
            continue
        for line_match in _DESIGN_LINE.finditer(text):
            for path in _PNG_PATH.findall(line_match.group(1)):
                norm = path.strip()
                if norm and norm not in seen:
                    seen.add(norm)
                    out.append(norm)
    return out


def parse_screen_map(mjs_source: str) -> list[dict]:
    """Parse the SCREENS array out of screens.mjs into dicts.

    We do not run JS; we extract each object's `key`, `design`, and `sources`
    with regexes scoped to the SCREENS literal. Returns [] if the array or its
    shape can't be found — the caller treats [] as "can't plan → SKIP", never
    as "nothing affected → run on nothing".
    """
    start = mjs_source.find("export const SCREENS")
    if start == -1:
        return []
    bracket = mjs_source.find("[", start)
    if bracket == -1:
        return []
    # Walk to the matching close bracket so we only parse the SCREENS literal.
    depth = 0
    end = -1
    for i in range(bracket, len(mjs_source)):
        c = mjs_source[i]
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end == -1:
        return []
    body = mjs_source[bracket : end + 1]

    screens: list[dict] = []
    # Split into top-level objects by `{ ... }` at depth 1. Simple brace walk.
    obj_depth = 0
    obj_start = -1
    for i, c in enumerate(body):
        if c == "{":
            if obj_depth == 0:
                obj_start = i
            obj_depth += 1
        elif c == "}":
            obj_depth -= 1
            if obj_depth == 0 and obj_start != -1:
                chunk = body[obj_start : i + 1]
                screen = _parse_screen_object(chunk)
                if screen:
                    screens.append(screen)
                obj_start = -1
    return screens


def _parse_screen_object(chunk: str) -> dict | None:
    key_m = re.search(r"key:\s*'([^']+)'", chunk)
    design_m = re.search(r"design:\s*'([^']+)'", chunk)
    if not key_m or not design_m:
        return None
    sources: list[str] = []
    sources_m = re.search(r"sources:\s*\[([^\]]*)\]", chunk)
    if sources_m:
        sources = re.findall(r"'([^']+)'", sources_m.group(1))
    return {"key": key_m.group(1), "design": design_m.group(1), "sources": sources}


def plan(
    card_text: str,
    pr_body: str,
    changed_files: list[str],
    screen_map: list[dict],
    ui_prefix: str = DEFAULT_UI_PREFIX,
) -> dict:
    """The decision. Returns a plan dict; run:false on every skip/degrade path."""
    design_refs = parse_design_refs(card_text, pr_body)
    if not design_refs:
        return _skip("no **Design:** ref on the card/PR — visual stage skipped (not a defect)")

    ui_changed = [f for f in changed_files if f.startswith(ui_prefix)]
    if not ui_changed:
        return _skip("no UI files changed — visual stage skipped")

    if not screen_map:
        # We have a Design ref + UI change but cannot read the screen map. We do
        # NOT guess which screen — degrade to SKIP so we never block on a stale
        # or unparseable map.
        return _skip("could not parse the screen map — visual stage skipped (degrade, no block)")

    # Affected screens: a mapped screen is affected iff its design PNG is one of
    # the card's Design refs, OR one of its page sources changed in this PR. The
    # design-ref match is the primary signal (the card says "build THIS screen");
    # the sources match catches a UI edit to a screen the card also names.
    design_set = set(design_refs)
    affected: list[dict] = []
    for s in screen_map:
        design_path = f"{SCREENS_DIR}/{s['design']}"
        # A mapped screen is affected iff the card's **Design:** ref names its
        # exact design PNG. We deliberately require the design-ref match rather
        # than a mere source-file touch: the comparison only makes sense against
        # a design baseline the card explicitly vouches for, and requiring the
        # ref keeps the gate from firing on incidental UI edits the card never
        # claimed to design. (The PR already had to touch UI to get here.)
        if design_path in design_set:
            affected.append(s)

    if not affected:
        return _skip(
            "Design ref(s) name no mapped+affected screen — visual stage skipped "
            "(no design baseline to compare against; not a block)"
        )

    return {
        "run": True,
        "reason": f"comparing {len(affected)} screen(s) against the design",
        "screens": [s["key"] for s in affected],
        "designs": [f"{SCREENS_DIR}/{s['design']}" for s in affected],
    }


def _skip(reason: str) -> dict:
    return {"run": False, "reason": reason, "screens": [], "designs": []}


def _read(path: str | None) -> str:
    if not path:
        return ""
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--card-text-file")
    ap.add_argument("--pr-body-file")
    ap.add_argument("--changed-file")
    ap.add_argument("--screens-mjs")
    ap.add_argument("--ui-prefix", default=DEFAULT_UI_PREFIX)
    args = ap.parse_args(argv)

    card_text = _read(args.card_text_file)
    pr_body = _read(args.pr_body_file)
    changed_raw = _read(args.changed_file)
    changed_files = [l.strip() for l in changed_raw.splitlines() if l.strip()]
    screen_map = parse_screen_map(_read(args.screens_mjs))

    # Any unexpected failure → SKIP (safe). We never let a planner exception
    # bubble into a non-zero exit that the workflow might read as "block".
    try:
        result = plan(card_text, pr_body, changed_files, screen_map, args.ui_prefix)
    except Exception as exc:  # pragma: no cover - defensive
        result = _skip(f"planner error ({exc}) — visual stage skipped (degrade, no block)")

    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
