#!/usr/bin/env python3
"""Todo-entry card-validation gate (DRE-1405) — FIX-FIRST.

A card is clean to enter Todo only if it has BOTH:
  1. a resolvable repo — the `repo:<slug>` LABEL is the canonical source of
     truth (DRE-1699); a legacy `**Repo:** <slug>` frontmatter line is still
     ACCEPTED as a deprecated fallback so pre-label cards keep routing, but new
     cards should carry the label only; and
  2. an agent-role label — any `agent:*` label (engineer, planner, qa-reviewer,
     devops, … — checked by prefix, not an enumerated list).

When a card enters Todo malformed, the gate REPAIRS it in place rather than
bouncing (the original behavior). It:
  1. infers the agent role label (title `[EPIC]` or has children → agent:planner,
     else agent:engineer) and adds it;
  2. infers the repo deterministically — from an `initiative:<x>` label (2a) or
     the card's Linear project NAME prefix (2b), validated against the real-repo
     set VALID_SLUGS — and adds the `repo:<slug>` LABEL (and ONLY the label,
     DRE-1699 — it no longer prepends the deprecated `**Repo:**` stamp).
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
  check-children <EPIC>
                 read-only post-plan sweep (DRE-1715): runs `missing()` — the
                 SAME validation core the gate uses — over EVERY child of an
                 epic, in WHATEVER state (children are created in Backlog, which
                 the gate path deliberately leaves alone). Exit 0 iff every child
                 carries a resolvable repo + an agent:* role and a non-placeholder
                 body; exit 1 listing the offenders otherwise, so plan.yml fails
                 the plan before it completes rather than shipping a broken child
                 the operator must hand-repair.

Auth: LINEAR_API_KEY env var (shared with linear_ops.py).
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

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
_INITIATIVE_LABEL = "initiative:"
WANT_REPO = "repo: label (or legacy **Repo:** line)"
WANT_AGENT = "agent: role label"
WANT_INITIATIVE = "initiative: label"


def _has_repo(description: str, labels: list[str]) -> bool:
    # Canonical form (DRE-1699): the repo:<slug> LABEL with a non-empty slug.
    if any(
        l.lower().startswith(_REPO_LABEL) and l.split(":", 1)[1].strip()
        for l in labels
    ):
        return True
    # DEPRECATED fallback: a legacy `**Repo:** <slug>` frontmatter line, kept
    # only so pre-label cards still validate/route. Ignores fenced code blocks
    # (avoids the doc-example false positive the relay guards against).
    stripped = _FENCE_RE.sub("", description or "")
    return _REPO_RE.search(stripped) is not None


def _has_agent_label(labels: list[str]) -> bool:
    return any(l.lower().startswith(_AGENT_PREFIX) for l in labels)


def _has_initiative_label(labels: list[str]) -> bool:
    # A non-empty `initiative:<x>` label (a bare "initiative:" does not count).
    return any(
        l.lower().startswith(_INITIATIVE_LABEL) and l.split(":", 1)[1].strip()
        for l in labels
    )


def missing(description: str, labels: list[str], *, require_initiative: bool = False) -> list[str]:
    """Return the list of missing requirements (empty list == clean card).

    `require_initiative` additionally requires a non-empty `initiative:<x>` label.
    It is OPT-IN and OFF by default so the Todo-entry gate is unchanged: that gate
    runs BEFORE repo inference and routinely sees clean cards that carry a
    `repo:<slug>` label (or a legacy `**Repo:** <slug>` line) and a role label
    but no initiative — it INFERS the repo from an initiative label only when one
    is present, so it must not demand one. The
    post-plan child sweep / create seam turn it ON — a planner-created child must
    inherit `initiative:*` (DRE-1722) or the reconcile dependency-gate, which
    scopes promotion to the initiative, never promotes it and it stalls in
    Backlog. Inheritance (parent_inherited_labels) makes this hold deterministically;
    the check is the backstop that fails the plan if it ever doesn't.
    """
    labels = labels or []
    out: list[str] = []
    if not _has_repo(description, labels):
        out.append(WANT_REPO)
    if not _has_agent_label(labels):
        out.append(WANT_AGENT)
    if require_initiative and not _has_initiative_label(labels):
        out.append(WANT_INITIATIVE)
    return out


# --- Fix-first inference (DRE-1405 extension) --------------------------------
#
# When a card enters Todo malformed, the gate REPAIRS it in place rather than
# bouncing — bouncing only when the repo cannot be inferred deterministically
# (the one case a fix would be a wrong-repo guess). The inference below is the
# pure, no-I/O core; cmd_gate applies it and writes the labels/description.

# --- Routing snapshot: derive VALID_SLUGS + the prefix map, never enumerate ---
#
# The valid-slug set and the project-prefix map are DERIVED from the SAME
# canonical routing snapshot the relay reads (DRE-1624/1627/1628): the relay's
# source of truth is the SSM parameter /bureau/relay/repo-map, seeded from and
# mirrored by config/repo-map.json. The gate runs in GitHub Actions WITHOUT AWS
# creds (and bureau-pipeline is checked out as a public repo with no token to
# read agent-bureau's PRIVATE snapshot), so it cannot read SSM at runtime.
# Instead we BUNDLE the snapshot as config/repo-map.json in THIS repo (the
# "published JSON" read path) and derive both structures from it — so onboarding
# a customer is a data write to the snapshot, not a two-line code edit here, and
# the relay and the gate stay byte-aligned by reading the same shape.
#
# Lockstep is STRUCTURAL: test_repo_map_snapshot.py asserts the derived
# structures equal the bundled snapshot (and that the bundled snapshot is a
# superset of the documented aliases), so a hand-edit that drifts from the
# snapshot fails CI rather than routing one way and validating another.

_SNAPSHOT_PATH = Path(__file__).resolve().parent.parent / "config" / "repo-map.json"

# Last-known-good fallback: the snapshot bundled at the time of this commit. The
# derive reads the on-disk snapshot first (so onboarding is a data edit); this
# literal is used ONLY if that file is missing/unreadable/empty, so the gate
# never hard-fails CI on a transient read error — it degrades to the last-known
# slug set. The divergence test pins this literal to the on-disk snapshot, so it
# can't silently rot.
_FALLBACK_REPO_MAP = {
    "atlas": "EveryBite/atlas",
    "deltasolv": "dreadnought-foundry/deltasolv",
    "vericorr": "dreadnought-foundry/vericorr",
    "agent-bureau": "dreadnought-foundry/agent-bureau",
    "agent-bureau-demo": "dreadnought-foundry/agent-bureau-demo",
}


def _load_repo_map() -> dict[str, str]:
    """The slug → "owner/repo" routing snapshot, read from the bundled
    config/repo-map.json. Falls back to the last-known-good literal above when
    the file is missing/unreadable/empty/malformed, logging to stderr — so the
    gate degrades gracefully instead of hard-failing CI on a transient read."""
    try:
        parsed = json.loads(_SNAPSHOT_PATH.read_text())
        if isinstance(parsed, dict) and parsed:
            return parsed
        print(
            f"validate_card: routing snapshot {_SNAPSHOT_PATH} empty/not-a-dict; "
            "using last-known-good fallback",
            file=sys.stderr,
        )
    except Exception as exc:  # missing / unreadable / malformed JSON
        print(
            f"validate_card: could not read routing snapshot {_SNAPSHOT_PATH} "
            f"({exc}); using last-known-good fallback",
            file=sys.stderr,
        )
    return dict(_FALLBACK_REPO_MAP)


_REPO_MAP = _load_repo_map()

# The real repos the bureau pipeline serves — the SINGLE source of truth for a
# valid repo slug, DERIVED as the routing snapshot's keys (DRE-1624/1626).
# Mirrors the relay's REPO_MAP keys (cloud/relay/lambda_function.py
# `_infer_slug`: `valid_slugs = set(_repo_map())`). Inference must never resolve
# a slug that isn't here — better to bounce than build on a repo that doesn't
# exist; onboarding a repo adds it to the snapshot, no edit here.
VALID_SLUGS = set(_REPO_MAP)

# Initiative-label slug → repo slug. The mapping is identity EXCEPT the one
# documented alias: the Agent Bureau initiative carries the `initiative:bureau`
# label, whose repo is `agent-bureau` (the label slug ≠ the repo slug). Every
# other initiative's label slug equals its repo slug. We keep this as an alias
# table (not an enumeration) so an unknown/typo'd initiative resolves to its own
# slug and is then rejected by the VALID_SLUGS guard — never silently dropped.
# Byte-aligned with the relay's _INITIATIVE_ALIAS.
_INITIATIVE_ALIAS = {"bureau": "agent-bureau"}

# Non-identity project-NAME-prefix aliases (the token before the first ":").
# Projects are named "<Product>: <thing>" (e.g. "Bureau: Console", "Demo:
# Sandbox"); their prefix is the repo slug EXCEPT these product nicknames.
# Byte-aligned with the relay's _PROJECT_PREFIX_ALIAS — the non-derivable bits.
_PROJECT_PREFIX_ALIAS = {"bureau": "agent-bureau", "demo": "agent-bureau-demo"}

# Linear project NAME prefix (the token before the first ":") → repo slug.
# DERIVED, mirroring the relay's _infer_slug: identity over every routable slug,
# plus the non-derivable product nicknames in _PROJECT_PREFIX_ALIAS. A prefix we
# don't recognize (Foundry, Dev Sandbox, …) yields no repo → bounce. Keys are
# lowercased for case-insensitive matching.
_PROJECT_PREFIX_TO_SLUG = {slug: slug for slug in VALID_SLUGS}
_PROJECT_PREFIX_TO_SLUG.update(_PROJECT_PREFIX_ALIAS)

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


def _role_from_labels(labels: list[str]) -> str:
    """The agent role to run for a card, derived from its resolved labels:
    `devops` iff `agent:devops`, `frontend` iff `agent:frontend`, else
    `engineer`. The planner is dispatched by a separate workflow (plan.yml), so
    agent-task only ever picks between engineer / frontend / devops here."""
    low = [l.lower() for l in (labels or [])]
    if "agent:devops" in low:
        return "devops"
    if "agent:frontend" in low:
        return "frontend"
    return "engineer"


def _emit_role(role: str) -> None:
    # Mirror _emit: write the resolved role to the SAME GitHub step-output
    # mechanism so agent-task.yml can branch brief/model/heartbeat on it. Always
    # set (engineer on bounce/skip paths) so the output is never empty.
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"role={role}\n")
    print(f"role={role}")


def cmd_gate(identifier: str) -> None:
    # Imported lazily so the pure core (and its tests) need no LINEAR_API_KEY.
    import linear_ops

    issue = linear_ops.get_issue(identifier)
    current = (issue.get("state") or {}).get("name", "").lower()
    if current not in _GATEABLE:
        print(f"{identifier} is in {current!r}, not a Todo-entry — gate skipped")
        _emit(False)
        _emit_role("engineer")
        return

    card = _fetch_card(linear_ops, identifier)
    description, labels = card["description"], card["labels"]

    gaps = missing(description, labels)
    if not gaps:
        print(f"{identifier} is clean — proceeding to build")
        _emit(False)
        _emit_role(_role_from_labels(labels))
        return

    # --- Fix-first: decide the FULL repair before mutating anything, so a card
    # we can't fully fix is bounced clean (never left half-repaired in Backlog).
    new_labels: list[str] = []
    fixed: list[str] = []          # human-readable bits for the auto-fix comment
    sources: set[str] = set()

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
        # DRE-1699: the repo:<slug> LABEL is the source of truth. The autofix
        # adds only the label — it no longer prepends the deprecated **Repo:**
        # stamp to the description.

    # Every gap is fixable → apply the repair (label-only, DRE-1699), then let
    # the card proceed. The description is left untouched — the label carries
    # the repo signal.
    for label in new_labels:
        linear_ops.add_label(identifier, label)

    src = f" (inferred from {', '.join(sorted(sources))})" if sources else ""
    linear_ops.cmd_comment(
        identifier,
        f"🔧 Auto-fixed by the Todo gate: added {', '.join(fixed)}{src}. Building now.",
    )
    print(f"{identifier} auto-fixed ({', '.join(fixed)}) — proceeding to build")
    _emit(False)
    # Role from the FULL resolved label set (original + any inferred). Inference
    # only ever yields engineer/planner, so this is engineer unless the card
    # already carried an agent:devops label alongside the gap we just repaired.
    _emit_role(_role_from_labels(labels + new_labels))


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
    _emit_role("engineer")


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


# --- Post-plan child sweep (DRE-1715) ----------------------------------------
#
# After the planner creates children (in Backlog), confirm EVERY child is
# complete and valid before the plan is allowed to finish — reusing this gate's
# `missing()` (single source of truth, no parallel checker) plus the shared
# body-placeholder guard. The gate's own cmd_gate intentionally only acts on
# Todo/Triage cards, so it can't be the sweep; this read-only command can.


def child_problems(title: str, description: str, labels: list[str]) -> list[str]:
    """All reasons a created child is incomplete (empty == valid). Reuses
    `missing(..., require_initiative=True)` for the repo/role/initiative contract
    and linear_ops.body_problem for the path-like/empty/placeholder body — the
    SAME checks the create seam enforces, so the sweep and the create path can
    never disagree. A child MUST carry `initiative:*` (inherited from the parent
    epic) or the reconcile dependency-gate never promotes it (DRE-1722)."""
    import linear_ops  # body_problem lives with the create seam

    out = list(missing(description, labels, require_initiative=True))
    body = linear_ops.body_problem(description)
    if body is not None:
        out.append(body)
    return out


def cmd_check_children(epic_identifier: str) -> None:
    import linear_ops

    data = linear_ops.gql(
        """query($id: String!) { issue(id: $id) { children { nodes {
             identifier title description labels { nodes { name } } } } } }""",
        {"id": epic_identifier},
    )
    children = (((data or {}).get("issue") or {}).get("children") or {}).get("nodes", [])
    broken: list[str] = []
    for c in children:
        labels = [n["name"].lower() for n in (c.get("labels") or {}).get("nodes", [])]
        probs = child_problems(c.get("title") or "", c.get("description") or "", labels)
        if probs:
            broken.append(f"{c['identifier']}: " + "; ".join(probs))
    if broken:
        print(
            f"❌ {len(broken)}/{len(children)} child card(s) of {epic_identifier} "
            "are invalid:\n  " + "\n  ".join(broken),
            file=sys.stderr,
        )
        raise SystemExit(1)
    print(f"✅ all {len(children)} child card(s) of {epic_identifier} are valid")


if __name__ == "__main__":
    cmd, *args = sys.argv[1:]
    {"gate": cmd_gate, "check-children": cmd_check_children}[cmd](*args)
