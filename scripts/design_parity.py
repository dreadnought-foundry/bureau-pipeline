#!/usr/bin/env python3
"""Design-parity check — do the cards sum to the design? (DRE-2116)

The mechanical form of the planner obligation in standards/design-parity.md:
given the designed surfaces in scope, every surface must be accounted for by
either a card's `**Design:**` ref or an explicit `deferred: <surface> —
<reason>` line in the plan comment. Anything else is a silent omission — the
DeltaSolv failure mode (2026-07-13 gap audit: ~67 designed screens never
carded, the epic closed anyway).

Pure functions over strings — no I/O, no Linear/GitHub calls — so plan
reviewers, the epic-close gate, and tests can all run the same check.

Matching is by screen filename (basename), not exact path: planners write
refs repo-relative or design-dir-relative, but the surface IS the screen.
Prose mentions deliberately do NOT count — a card that talks about a screen
without a `**Design:**` ref is the built-to-text facade path.
"""

from __future__ import annotations

import os
import re

# The `**Design:** <ref>[, <ref>...]` card line (standards/design.md). One
# card may own several closely-coupled screens on one comma-separated line.
_DESIGN_LINE = re.compile(r"^\*\*Design:\*\*\s*(?P<refs>\S.*)$", re.MULTILINE)

# The explicit-deferral grammar from standards/design-parity.md:
#   deferred: <surface> — <reason>
# The dash separator requires surrounding whitespace so hyphenated surface
# names ("sign-in") survive, and the reason is mandatory — a reason-less
# deferral is a silent omission dressed up and must not count.
_DEFERRED_LINE = re.compile(
    r"^\s*(?:[-*]\s*)?deferred:\s*(?P<surface>.+?)\s+(?:—|--?)\s+(?P<reason>\S.*)$",
    re.IGNORECASE | re.MULTILINE,
)


def _slug(surface: str) -> str:
    """Normalize a surface/ref to its screen name for matching: basename,
    extension dropped (deferred lines name screens, not necessarily files)."""
    return os.path.splitext(os.path.basename(surface.strip()))[0].lower()


def design_refs(card_body: str) -> list[str]:
    """Every design ref a card's `**Design:**` line(s) name."""
    refs: list[str] = []
    for m in _DESIGN_LINE.finditer(card_body):
        refs.extend(r.strip() for r in m.group("refs").split(",") if r.strip())
    return refs


def deferred_surfaces(plan_comment: str) -> list[str]:
    """The surfaces a plan comment explicitly defers (with a reason), in
    order — the epic-close gate reads the ledger back out of these."""
    return [m.group("surface").strip() for m in _DEFERRED_LINE.finditer(plan_comment)]


def unaccounted_surfaces(
    surfaces: list[str], card_bodies: list[str], plan_comment: str
) -> list[str]:
    """The designed surfaces the plan silently omits — each a planning defect.

    A surface is accounted for ONLY by a card `**Design:**` ref or an
    explicit deferred line; returns the offenders in inventory order.
    """
    accounted = {_slug(r) for body in card_bodies for r in design_refs(body)}
    accounted.update(_slug(s) for s in deferred_surfaces(plan_comment))
    return [s for s in surfaces if _slug(s) not in accounted]
