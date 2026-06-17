#!/usr/bin/env python3
"""Todo-entry card-validation gate (DRE-1405) — FIX-FIRST.

A card is clean to enter Todo only if it has BOTH:
  1. a resolvable repo — a `**Repo:** <slug>` frontmatter line OR a `repo:<slug>`
     label; and
  2. an agent-role label — any `agent:*` label (engineer, planner, qa-reviewer,
     devops, … — checked by prefix, not an enumerated list).

When a card enters Todo malformed, the gate REPAIRS it in place rather than
bouncing (the original behavior). It:
  1. infers the agent role label (title `[EPIC]` or has children → agent:planner,
     else agent:engineer) and adds it;
  2. infers the repo deterministically — from an `initiative:<x>` label (2a) or
     the card's Linear project NAME prefix (2b), validated against the real-repo
     set VALID_SLUGS — and adds the `repo:<slug>` label + a `**Repo:** <slug>`
     line at the top of the description.
On a successful repair the card PROCEEDS (no bounce) and gets a 🔧 comment.
It is BOUNCED to Backlog ONLY when the repo cannot be inferred (no initiative
label, unknown/absent project) or the inference yields a slug that isn't a real
repo — the one case where a fix would be a wrong-repo guess. See infer_repo /
VALID_SLUGS for the mapping (mirrors the relay's REPO_MAP, single source).

The relay (cloud/relay/lambda_function.py in agent-bureau) has only a GitHub App
token and cannot write to Linear, so the repair lives here, in the workflows
that carry LINEAR_API_KEY. `missing()` / `infer_agent_label()` / `infer_repo()`
are the no-I/O core (unit-tested directly); `cmd_gate()` re-reads the card's LIVE
Linear state and only ever acts while the card is still in Todo/Triage — a card
already past Todo (In Progress/QA/…) is never touched.

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


# --- Fix-first inference (DRE-1405 extension) --------------------------------
#
# When a card enters Todo malformed, the gate REPAIRS it in place rather than
# bouncing — bouncing only when the repo cannot be inferred deterministically
# (the one case a fix would be a wrong-repo guess). The inference below is the
# pure, no-I/O core; cmd_gate applies it and writes the labels/description.

# The real repos the bureau pipeline serves — the SINGLE source of truth for a
# valid repo slug. Mirrors the relay's REPO_MAP in agent-bureau
# (cloud/relay/deploy.sh: {atlas, deltasolv, vericorr}) PLUS agent-bureau, which
# is a real repo + a real initiative that consumes this pipeline @main (the relay
# DEFAULT_REPO covers it for routing). Inference must never resolve a slug that
# isn't here — better to bounce than build on a repo that doesn't exist.
VALID_SLUGS = {"atlas", "deltasolv", "vericorr", "agent-bureau", "agent-bureau-demo"}

# Initiative-label slug → repo slug. The mapping is identity EXCEPT the one
# documented alias: the Agent Bureau initiative carries the `initiative:bureau`
# label, whose repo is `agent-bureau` (the label slug ≠ the repo slug). Every
# other initiative's label slug equals its repo slug. We keep this as an alias
# table (not an enumeration) so an unknown/typo'd initiative resolves to its own
# slug and is then rejected by the VALID_SLUGS guard — never silently dropped.
_INITIATIVE_ALIAS = {"bureau": "agent-bureau"}

# Linear project NAME prefix (the token before the first ":") → repo slug.
# Projects are named "<Product>: <thing>" (e.g. "Bureau: Console",
# "Atlas: Allergen Pivot", "DeltaSolv: Phase 6", "VeriCorr: Forms"). A prefix we
# don't recognize (Foundry, Dev Sandbox, or anything else) yields no repo →
# bounce. Keys are lowercased for case-insensitive matching.
_PROJECT_PREFIX_TO_SLUG = {
    "bureau": "agent-bureau",
    "atlas": "atlas",
    "deltasolv": "deltasolv",
    "vericorr": "vericorr",
    "demo": "agent-bureau-demo",
}

_INITIATIVE_PREFIX = "initiative:"


def infer_agent_label(title: str, has_children: bool, labels: list[str]) -> str:
    """The agent role label a card should carry if it has none.

    A card is an epic (→ agent:planner) when its title contains `[EPIC]` OR it
    has child issues; otherwise it's implementation work (→ agent:engineer).
    """
    t = (title or "").lower()
    if "[epic]" in t or has_children:
        return "agent:planner"
    return "agent:engineer"


def infer_repo(labels: list[str], project_name: str | None) -> tuple[str | None, str | None]:
    """Deterministically infer a repo slug for a card lacking one.

    Returns (slug, source) where source describes where the slug came from for
    the auto-fix comment, or (None, None) when the repo cannot be inferred.

    Precedence (rule 2 of DRE-1405):
      2a. an `initiative:<x>` label (aliased: bureau→agent-bureau, else identity)
      2b. else the card's Linear project NAME prefix.

    The returned candidate is NOT yet validated against VALID_SLUGS — the caller
    (cmd_gate) does that, so a candidate that is a real-looking slug but not a
    real repo (e.g. initiative:foundry → "foundry") is rejected as a wrong-repo
    guess rather than silently treated as "uninferable".
    """
    # 2a — initiative label wins.
    for label in labels or []:
        low = label.lower()
        if low.startswith(_INITIATIVE_PREFIX):
            raw = low[len(_INITIATIVE_PREFIX):].strip()
            if raw:
                slug = _INITIATIVE_ALIAS.get(raw, raw)
                return slug, f"initiative:{raw}"
    # 2b — project name prefix.
    if project_name:
        prefix = project_name.split(":", 1)[0].strip().lower()
        slug = _PROJECT_PREFIX_TO_SLUG.get(prefix)
        if slug:
            return slug, f"project {project_name!r}"
    return None, None


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

    card = _fetch_card(linear_ops, identifier)
    description, labels = card["description"], card["labels"]

    gaps = missing(description, labels)
    if not gaps:
        print(f"{identifier} is clean — proceeding to build")
        _emit(False)
        return

    # --- Fix-first: decide the FULL repair before mutating anything, so a card
    # we can't fully fix is bounced clean (never left half-repaired in Backlog).
    new_labels: list[str] = []
    fixed: list[str] = []          # human-readable bits for the auto-fix comment
    sources: set[str] = set()
    new_description = description

    if WANT_AGENT in gaps:
        agent_label = infer_agent_label(card["title"], card["has_children"], labels)
        new_labels.append(agent_label)
        fixed.append(agent_label)

    if WANT_REPO in gaps:
        slug, source = infer_repo(labels, card["project_name"])
        # Bounce ONLY when the repo can't be inferred deterministically, OR the
        # inference yields a slug that isn't a real repo (never a wrong-repo
        # guess). This is the one path the fix-first gate still bounces.
        if slug is None or slug not in VALID_SLUGS:
            why = (
                f"inferred repo {slug!r} is not a known repo"
                if slug is not None
                else "could not infer a repo (no initiative label or known project)"
            )
            _bounce(linear_ops, identifier, gaps, why)
            return
        new_labels.append(f"repo:{slug}")
        fixed.append(f"repo:{slug}")
        sources.add(source)
        new_description = f"**Repo:** {slug}\n\n{description}".rstrip("\n") if description else f"**Repo:** {slug}"

    # Every gap is fixable → apply the repair, then let the card proceed.
    for label in new_labels:
        linear_ops.add_label(identifier, label)
    if new_description != description:
        linear_ops.set_description(identifier, new_description)

    src = f" (inferred from {', '.join(sorted(sources))})" if sources else ""
    linear_ops.cmd_comment(
        identifier,
        f"🔧 Auto-fixed by the Todo gate: added {', '.join(fixed)}{src}. Building now.",
    )
    print(f"{identifier} auto-fixed ({', '.join(fixed)}) — proceeding to build")
    _emit(False)


def _bounce(linear_ops, identifier: str, gaps: list[str], why: str) -> None:
    body = (
        "🚧 Not ready for build — missing: "
        + ", ".join(gaps)
        + ". Returned to Backlog; fix and move to Todo again."
    )
    linear_ops.cmd_comment(identifier, body)
    linear_ops.cmd_state(identifier, "Backlog")
    print(f"{identifier} bounced to Backlog ({why}; missing: {gaps})")
    _emit(True)


def _fetch_card(linear_ops, identifier: str) -> dict:
    """Live card fields the gate needs: title, description, labels (lowercased),
    whether it has children, and its project name."""
    data = linear_ops.gql(
        """query($id: String!) { issue(id: $id) {
             title description
             labels { nodes { name } }
             children { nodes { id } }
             project { name } } }""",
        {"id": identifier},
    )
    issue = data["issue"]
    project = issue.get("project")
    return {
        "title": issue.get("title") or "",
        "description": issue.get("description") or "",
        "labels": [n["name"].lower() for n in issue["labels"]["nodes"]],
        "has_children": bool(issue.get("children", {}).get("nodes")),
        "project_name": (project or {}).get("name"),
    }


if __name__ == "__main__":
    cmd, *args = sys.argv[1:]
    {"gate": cmd_gate}[cmd](*args)
