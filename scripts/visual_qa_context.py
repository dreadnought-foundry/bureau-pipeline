#!/usr/bin/env python3
"""Render the critic-facing "visual QA" context block for qa-review.yml.

DRE-1481. After the planner (visual_qa_plan.py) decides which screens to shoot
and the harness (DRE-1480) renders them, this turns the outcome into the exact
prose injected into BOTH critic prompts (primary + retry). Keeping it here —
not inline in YAML — means the wording (and its safety framing) is unit-tested,
and the duplicated prompt blocks stay in sync via one `${{ steps.* }}` ref.

It encodes the SAFETY contract in the words the critic reads:

  * SKIP  → tell the critic there is no visual stage for this PR and that this
            is NORMAL — it must NOT invent a visual finding or block on it.
  * RUN, renders ok → give the critic each (design PNG, rendered screenshot)
            pair to Read and compare; a MATERIAL mismatch is a blocking finding.
  * RUN, harness degraded → tell the critic the comparison DID NOT HAPPEN, and
            that it must SAY SO on the verdict, in one plain line, while still
            not blocking on the missing render.

DEGRADED MUST NEVER READ AS GREEN (DRE-3248). This block used to tell the
critic a failed render was "infrastructure flakiness … expected and NOT a
defect", and the critic duly wrote a verdict with nothing on it about the
visual check. One River's Phase 2 proof pushed a red banner onto five screens,
the render died before a single screenshot (the runner's default node was
below what `console/web` requires), and the design-fidelity verdict came back
a skip wearing green — the only trace was a log line nobody reads. What
changed is the SAYING, not the blocking: a degraded stage still does not block
a merge, it just stops being silent about not having run.

CLI:

    python3 visual_qa_context.py \
        --plan-file <plan.json>        # output of visual_qa_plan.py
        --render-status <ok|degraded>  # did the harness produce the renders?
        [--render-note "<short note>"] # e.g. which screens failed
        [--rendered key=path ...]      # rendered screenshot per screen key

Prints the block to stdout. Always exits 0.
"""

from __future__ import annotations

import argparse
import json
import sys

SCREENS_DIR = "console/design/images/screens"
RENDER_DIR = "console/design/images/_renders"

# SKIP wording. A skipped stage is a PR with no design comparison to make —
# that IS expected, and the critic must not invent a finding out of it.
_DO_NOT_BLOCK = (
    "This is expected and is NOT a defect. Do NOT raise any visual-comparison "
    "finding and do NOT block the merge on the absence of a screenshot — run "
    "the rest of your review normally."
)

# The one line a degraded stage puts on the verdict. It is what someone
# reading the verdict sees instead of silence, so it is plain English and it
# is the same words every time.
DEGRADED_LINE = "Visual check did not run: {note}"

# Used when the harness gave no note of its own — the line is never left
# dangling after its colon.
DEFAULT_DEGRADED_NOTE = "the screenshot harness produced no render"


def _render_path(key: str, rendered: dict[str, str]) -> str:
    return rendered.get(key, f"{RENDER_DIR}/{key}.png")


def build_context(plan: dict, render_status: str, render_note: str,
                  rendered: dict[str, str]) -> str:
    if not plan.get("run"):
        return (
            "VISUAL QA: skipped for this PR "
            f"({plan.get('reason', 'no design comparison applies')}). "
            + _DO_NOT_BLOCK
        )

    screens = plan.get("screens", [])
    designs = plan.get("designs", [])

    if render_status != "ok":
        note = (render_note or "").strip() or DEFAULT_DEGRADED_NOTE
        line = DEGRADED_LINE.format(note=note)
        return "\n".join([
            "VISUAL QA — THE COMPARISON DID NOT HAPPEN. This PR has a "
            "**Design:** ref and changed UI, so the affected screen(s) should "
            "have been rendered and compared against the design — but the "
            "screenshot harness could not produce them, so nothing was "
            "compared.",
            "",
            "Your verdict Summary MUST carry this line, verbatim, on its own "
            "line:",
            "",
            f"    {line}",
            "",
            "Then review the rest of the PR normally. You have no render, so "
            "do NOT raise a visual-comparison finding and do NOT block the "
            "merge on the missing screenshot. But do NOT report the design "
            "check as done, passed, or not applicable either — it was "
            "attempted and it failed, and the verdict has to say so.",
        ])

    lines = [
        "VISUAL QA — DESIGN-FIDELITY COMPARISON (this PR has a **Design:** ref "
        "and changed UI, so the affected screen(s) were rendered for you):",
        "",
        "For EACH pair below, Read BOTH images and compare the rendered build "
        "against the design:",
        "  - the DESIGN is the committed exported screen the card asked the team "
        "to build to;",
        "  - the RENDER is a screenshot of the actual built app at the design "
        "viewport, produced offline (seeded data, no live network).",
        "",
    ]
    for i, key in enumerate(screens):
        design = designs[i] if i < len(designs) else f"{SCREENS_DIR}/{key}.png"
        lines.append(f"  • screen `{key}`:")
        lines.append(f"      DESIGN:  {design}")
        lines.append(f"      RENDER:  {_render_path(key, rendered)}")
    lines += [
        "",
        "Compare layout, column widths and gutters, spacing/padding, alignment, "
        "and presence of every element. A MATERIAL visual mismatch (clearly "
        "wrong widths/gutters, broken or misaligned layout, missing components, "
        "wrong structure) IS a blocking finding — report it in your verdict "
        "(business language in the Summary; the precise screen + what's wrong in "
        "the technical section). Minor antialiasing / sub-pixel / font-hinting "
        "differences are NOT findings. If a RENDER file is missing or empty, "
        "treat that one screen as infra-degraded and do NOT block on it.",
    ]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan-file")
    ap.add_argument("--render-status", default="ok")
    ap.add_argument("--render-note", default="")
    ap.add_argument("--rendered", nargs="*", default=[])
    args = ap.parse_args(argv)

    plan: dict = {"run": False, "reason": "no plan"}
    if args.plan_file:
        try:
            with open(args.plan_file, encoding="utf-8") as f:
                plan = json.load(f)
        except (OSError, ValueError):
            plan = {"run": False, "reason": "plan unavailable — visual stage skipped"}

    rendered: dict[str, str] = {}
    for item in args.rendered:
        if "=" in item:
            k, v = item.split("=", 1)
            rendered[k.strip()] = v.strip()

    print(build_context(plan, args.render_status, args.render_note, rendered))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
