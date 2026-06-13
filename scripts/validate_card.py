#!/usr/bin/env python3
"""Todo-entry card-validation gate (DRE-1405).

A card is clean to enter Todo only if it has BOTH:
  1. a resolvable repo — a `**Repo:** <slug>` frontmatter line OR a `repo:<slug>`
     label; and
  2. an agent-role label — any `agent:*` label (engineer, planner, qa-reviewer,
     devops, … — checked by prefix, not an enumerated list).

The relay (cloud/relay/lambda_function.py in agent-bureau) has only a GitHub App
token and cannot write to Linear, so the bounce lives here, in the workflows
that carry LINEAR_API_KEY. `missing()` is the no-I/O core (unit-tested directly);
`cmd_gate()` re-reads the card's LIVE Linear state and bounces it to Backlog with
a diagnostic comment ONLY when it is malformed AND still sitting in Todo/Triage —
a card already past Todo (In Progress/QA/…) is never touched.

CLI:
  gate <DRE-N>   exit 0 always; prints `bounced=true` / `bounced=false` to
                 $GITHUB_OUTPUT (and stdout) so the workflow can skip the build.

Auth: LINEAR_API_KEY env var (shared with linear_ops.py).
"""

from __future__ import annotations

import os
import re
import sys

# IMPORTANT: this regex MUST stay in lockstep with the relay's _card_repo_slug
# (cloud/relay/lambda_function.py in agent-bureau) so routing and validation
# agree on what a repo frontmatter line is. They are separate deployables (a
# Lambda and a workflow script) and cannot share a module — duplicate, don't
# drift. Same fenced-code-strip + anchored + case-insensitive form.
_REPO_RE = re.compile(
    r"^\*\*Repo:\*\*\s*([a-z0-9._/-]+)\s*$", re.MULTILINE | re.IGNORECASE
)
_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)

# What's missing, in the exact words the bounce comment shows the CEO.
_REPO_LABEL = "repo:"
_AGENT_PREFIX = "agent:"
WANT_REPO = "**Repo:** line or repo: label"
WANT_AGENT = "agent: role label"


def _has_repo(description: str, labels: list[str]) -> bool:
    # Label form: repo:<slug> with a non-empty slug.
    if any(
        l.lower().startswith(_REPO_LABEL) and l.split(":", 1)[1].strip()
        for l in labels
    ):
        return True
    # Frontmatter form, ignoring fenced code blocks (avoids the doc-example
    # false positive the relay guards against).
    stripped = _FENCE_RE.sub("", description or "")
    return _REPO_RE.search(stripped) is not None


def _has_agent_label(labels: list[str]) -> bool:
    return any(l.lower().startswith(_AGENT_PREFIX) for l in labels)


def missing(description: str, labels: list[str]) -> list[str]:
    """Return the list of missing requirements (empty list == clean card)."""
    labels = labels or []
    out: list[str] = []
    if not _has_repo(description, labels):
        out.append(WANT_REPO)
    if not _has_agent_label(labels):
        out.append(WANT_AGENT)
    return out


# --- Linear-touching CLI (thin wrapper; the logic above is pure) -------------

# Only these states are a "Todo-entry" the gate may bounce. A card the human (or
# a workflow) has already moved past Todo is left alone — the gate validates the
# Todo-entry transition only, never drags work backward.
_GATEABLE = {"todo", "triage"}


def _emit(bounced: bool) -> None:
    val = "true" if bounced else "false"
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"bounced={val}\n")
    print(f"bounced={val}")


def cmd_gate(identifier: str) -> None:
    # Imported lazily so the pure core (and its tests) need no LINEAR_API_KEY.
    import linear_ops

    issue = linear_ops.get_issue(identifier)
    current = (issue.get("state") or {}).get("name", "").lower()
    if current not in _GATEABLE:
        print(f"{identifier} is in {current!r}, not a Todo-entry — gate skipped")
        _emit(False)
        return

    description, labels = _fetch_desc_and_labels(linear_ops, identifier)
    gaps = missing(description, labels)
    if not gaps:
        print(f"{identifier} is clean — proceeding to build")
        _emit(False)
        return

    body = (
        "🚧 Not ready for build — missing: "
        + ", ".join(gaps)
        + ". Returned to Backlog; fix and move to Todo again."
    )
    linear_ops.cmd_comment(identifier, body)
    linear_ops.cmd_state(identifier, "Backlog")
    print(f"{identifier} bounced to Backlog (missing: {gaps})")
    _emit(True)


def _fetch_desc_and_labels(linear_ops, identifier: str) -> tuple[str, list[str]]:
    """The card's current description + label names (lowercased)."""
    data = linear_ops.gql(
        """query($id: String!) { issue(id: $id) {
             description labels { nodes { name } } } }""",
        {"id": identifier},
    )
    issue = data["issue"]
    labels = [n["name"].lower() for n in issue["labels"]["nodes"]]
    return issue.get("description") or "", labels


if __name__ == "__main__":
    cmd, *args = sys.argv[1:]
    {"gate": cmd_gate}[cmd](*args)
