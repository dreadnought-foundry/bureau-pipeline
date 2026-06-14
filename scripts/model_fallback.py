#!/usr/bin/env python3
"""Model fallback on is_error death (DRE-1354, stdlib only).

The pipeline pins ONE model per agent role (engineer→Opus 4.8, planner→Fable 5).
When that one model is overloaded or erroring (`is_error=true` mid-run), the run
dies and the requeue re-dispatches onto the SAME model — so a card can die many
times in a row against a single flaky model while the alternate model is healthy
and idle (DRE-1300 looped 18×, 2026-06-13). This module is the no-I/O core that
picks the ALTERNATE model on the retry-after-error path.

Behavior is the contract:
  - Engineer primary Opus 4.8  → fallback Fable 5.
  - Planner  primary Fable 5   → fallback Opus 4.8.
  - On a prior `is_error` death, the next attempt uses the model NOT used on that
    death (both directions — today it was Opus flaking; tomorrow it may be Fable).

The agent-task workflow records which model each attempt used via a comment
marker (`MARKER_PREFIX`), and stamps an `is_error` death with `error_marker()`.
`select_model()` reads the last death's model from those markers and returns the
alternate; with no prior error, it returns the role's primary. Pure functions —
unit-tested with fixtures, no real API.
"""

from __future__ import annotations

import re

OPUS = "claude-opus-4-8"
FABLE = "claude-fable-5"

# role -> (primary, fallback). The fallback is the model used after the primary
# dies with is_error. Both directions are real: the planner's primary is Fable,
# so a Fable outage falls the PLANNER back to Opus.
ROLE_MODELS: dict[str, tuple[str, str]] = {
    "engineer": (OPUS, FABLE),
    "planner": (FABLE, OPUS),
}

# Every known model id, so we can validate a marker's payload.
KNOWN_MODELS = {OPUS, FABLE}

# Comment markers (machine-parseable; also human-readable on the Linear card).
# The report step writes MARKER_PREFIX + the model used for THIS attempt, and on
# an is_error death it writes ERROR_MARKER_PREFIX + the model that died.
MARKER_PREFIX = "model-attempt:"
ERROR_MARKER_PREFIX = "model-error:"

_ERROR_MARKER_RE = re.compile(
    re.escape(ERROR_MARKER_PREFIX) + r"\s*([a-z0-9.-]+)", re.IGNORECASE
)


def alternate(role: str, model: str) -> str:
    """The OTHER model in the role's pair. Falls back to the role's fallback if
    `model` is unrecognised for the role (defensive — never returns the dead
    model)."""
    primary, fallback = role_pair(role)
    return fallback if model == primary else primary


def role_pair(role: str) -> tuple[str, str]:
    """(primary, fallback) for a role; engineer's pair for anything unknown so a
    novel role never crashes a dispatch."""
    return ROLE_MODELS.get(role, ROLE_MODELS["engineer"])


def primary_model(role: str) -> str:
    return role_pair(role)[0]


def last_error_model(comment_bodies: list[str | None]) -> str | None:
    """The model id from the MOST RECENT is_error death marker, or None.

    `comment_bodies` is oldest→newest (Linear's natural order); we scan from the
    end so the latest death wins. Only a KNOWN model id counts."""
    for body in reversed(comment_bodies):
        for found in _ERROR_MARKER_RE.findall(body or ""):
            model = found.lower()
            if model in KNOWN_MODELS:
                return model
    return None


def select_model(role: str, comment_bodies: list[str | None]) -> str:
    """The model the NEXT attempt for this card should use.

    No prior is_error death → the role's primary. A prior death on model X →
    the role's alternate to X, so a single-model outage doesn't kill the run."""
    died = last_error_model(comment_bodies)
    if died is None:
        return primary_model(role)
    return alternate(role, died)


def attempt_marker(model: str) -> str:
    """Hidden-ish marker recording which model an attempt used (heartbeat)."""
    return f"{MARKER_PREFIX} {model}"


def error_marker(model: str) -> str:
    """Marker recording an is_error death and the model that died."""
    return f"{ERROR_MARKER_PREFIX} {model}"


def _role_from_labels(labels: list[str]) -> str:
    return "planner" if "agent:planner" in [l.lower() for l in labels] else "engineer"


def main(argv: list[str]) -> int:
    """CLI for the workflow.

      select <role> <comments-file>   print the model the next attempt should use
      role-of <label,label,...>       print engineer|planner from labels

    `comments-file` is a JSON array of comment body strings (oldest→newest); an
    empty/absent file means no prior attempts (→ primary). Keeping I/O here makes
    the workflow a one-liner and the logic above purely testable."""
    if not argv:
        print("usage: model_fallback.py select <role> <comments-file> | role-of <labels>")
        return 2
    cmd, *rest = argv
    if cmd == "role-of":
        labels = (rest[0] if rest else "").split(",")
        print(_role_from_labels([l.strip() for l in labels if l.strip()]))
        return 0
    if cmd == "select":
        import json

        role = rest[0] if rest else "engineer"
        bodies: list[str | None] = []
        if len(rest) > 1 and rest[1]:
            try:
                with open(rest[1]) as f:
                    loaded = json.load(f)
                if isinstance(loaded, list):
                    bodies = loaded
            except (OSError, ValueError):
                bodies = []
        print(select_model(role, bodies))
        return 0
    print(f"unknown command {cmd!r}")
    return 2


if __name__ == "__main__":
    import sys

    sys.exit(main(sys.argv[1:]))
