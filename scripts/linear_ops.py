#!/usr/bin/env python3
"""Minimal Linear operations for the agent pipeline (stdlib only).

Subcommands:
  state <DRE-N> <state-name>           move a card to a workflow state
  advance <DRE-N> <to-state> <from-states-csv>
                                       move ONLY if current state is in the csv
                                       (guards against dragging Done cards back)
  comment <DRE-N> <body>               add a comment to a card
  subissue <DRE-N(parent)> <title> <description-file>
                                       create a child issue (Backlog) under an epic.
                                       Inlines the file's CONTENTS (never a path),
                                       inherits repo:<slug>+role labels from the
                                       parent epic, encodes any **Blocked by:**
                                       prose into real Linear blockedBy relations,
                                       and validates the child through the SAME
                                       validate_card gate before creating it — a
                                       placeholder/empty body or a child missing
                                       repo/role is REJECTED (exit 3), never
                                       created broken. Optional flags:
                                         --label <name>   (repeatable) extra label
                                         --blocked-by DRE-N,DRE-M  (also parsed
                                                          from the body line)
  create <title> <description-file>    create a standalone card in Triage
  children <DRE-N>                     print the number of child issues
  add-label <DRE-N> <label-name>       attach a label (creating it if needed),
                                       idempotent — used for the human-hold
  remove-label <DRE-N> <label-name>    detach a label, idempotent — a no-op if
                                       absent (generic; mirrors add-label)
  description <DRE-N>                   print the card's raw description to
                                       stdout (the authoritative **Design:**
                                       source the visual-QA stage reads)

Auth: LINEAR_API_KEY env var.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request

API = "https://api.linear.app/graphql"


def gql(query: str, variables: dict | None = None) -> dict:
    req = urllib.request.Request(
        API,
        data=json.dumps({"query": query, "variables": variables or {}}).encode(),
        headers={
            "Authorization": os.environ["LINEAR_API_KEY"],
            "Content-Type": "application/json",
        },
    )
    # B310: URL is the constant https://api.linear.app endpoint, no user input.
    with urllib.request.urlopen(req, timeout=15) as resp:  # nosec B310
        out = json.loads(resp.read())
    if out.get("errors"):
        raise SystemExit(f"linear error: {out['errors']}")
    return out["data"]


def get_issue(identifier: str) -> dict:
    data = gql(
        """query($id: String!) { issue(id: $id) {
             id identifier title team { id } state { name }
           } }""",
        {"id": identifier},
    )
    return data["issue"]


def state_id(team_id: str, name: str) -> str:
    data = gql(
        """query($teamId: ID) { workflowStates(filter: {team: {id: {eq: $teamId}}}) {
             nodes { id name } } }""",
        {"teamId": team_id},
    )
    for node in data["workflowStates"]["nodes"]:
        if node["name"].lower() == name.lower():
            return node["id"]
    raise SystemExit(f"no state named {name!r} on team")


def cmd_state(identifier: str, state_name: str) -> None:
    issue = get_issue(identifier)
    sid = state_id(issue["team"]["id"], state_name)
    gql(
        """mutation($id: String!, $input: IssueUpdateInput!) {
             issueUpdate(id: $id, input: $input) { success } }""",
        {"id": issue["id"], "input": {"stateId": sid}},
    )
    print(f"{identifier} → {state_name}")


def cmd_advance(identifier: str, to_state: str, from_states_csv: str) -> None:
    issue = get_issue(identifier)
    current = issue["state"]["name"].lower()
    allowed = [s.strip().lower() for s in from_states_csv.split(",")]
    if current not in allowed:
        print(
            f"{identifier} is in {issue['state']['name']!r}, not in {from_states_csv!r} — not advancing"
        )
        return
    sid = state_id(issue["team"]["id"], to_state)
    gql(
        """mutation($id: String!, $input: IssueUpdateInput!) {
             issueUpdate(id: $id, input: $input) { success } }""",
        {"id": issue["id"], "input": {"stateId": sid}},
    )
    print(f"{identifier} → {to_state}")


def set_description(identifier: str, body: str) -> None:
    """Overwrite a card's description.

    Used by the Todo-entry gate's fix-first repair (DRE-1405) to prepend the
    `**Repo:** <slug>` frontmatter line when the repo was inferred but absent.
    """
    issue = get_issue(identifier)
    gql(
        """mutation($id: String!, $input: IssueUpdateInput!) {
             issueUpdate(id: $id, input: $input) { success } }""",
        {"id": issue["id"], "input": {"description": body}},
    )
    print(f"{identifier} description updated")


def cmd_comment(identifier: str, body: str) -> None:
    issue = get_issue(identifier)
    gql(
        """mutation($input: CommentCreateInput!) {
             commentCreate(input: $input) { success } }""",
        {"input": {"issueId": issue["id"], "body": body}},
    )
    print(f"commented on {identifier}")


# --- Sub-issue body / dependency guards (DRE-1715) ---------------------------
#
# The planner is an LLM agent: left to itself it has, in practice, (a) passed a
# scratch-file PATH (e.g. "/tmp/card2.md") as the card description instead of the
# file's CONTENTS, (b) created label-less children, and (c) left build ordering
# as English prose instead of real Linear blockedBy relations. cmd_subissue now
# closes all three at the create seam so the operator never hand-repairs a child.
# The functions below are the pure, no-I/O core (unit-tested directly); the gql
# calls live in cmd_subissue.

# A body that is JUST a filesystem path (the classic "/tmp/card2.md" mistake) —
# a single line, no whitespace inside, that looks like a path. Anchored so a real
# card body that merely MENTIONS a path in prose is not flagged.
_PATHLIKE_RE = re.compile(r"^[~./]?[\w./-]+\.(md|txt|json|markdown)$")

# Real markdown: a body must contain at least one of these to count as a genuine
# card and not a stub — a heading, a list item, or a **bold** frontmatter line.
_REAL_MARKDOWN_RE = re.compile(r"(^|\n)\s*(#{1,6}\s|[-*]\s|\*\*\w)")


def body_problem(body: str) -> str | None:
    """Why a sub-issue body is unusable, or None when it's a real card.

    Rejects (the planner's three failure modes for the BODY):
      * empty / whitespace-only;
      * a literal filesystem PATH written where the contents belong
        (single-line, path-shaped, e.g. "/tmp/card2.md" or "card2.md") — the
        create step must read the file and pass its CONTENTS, not its name;
      * a body with no real markdown structure at all (no heading, no list item,
        no **bold** frontmatter) — a placeholder stub, not a card.
    """
    text = (body or "").strip()
    if not text:
        return "empty body"
    one_line = "\n" not in text
    if one_line and (_PATHLIKE_RE.match(text) or text.startswith(("/", "./", "~/"))):
        return f"body looks like a file PATH, not card contents: {text!r}"
    if not _REAL_MARKDOWN_RE.search(text):
        return "body has no real markdown (no heading, list item, or **bold** line)"
    return None


# "**Blocked by:** DRE-1, DRE-2" / "Blocked by: DRE-3" anywhere in the body.
_BLOCKED_BY_RE = re.compile(
    r"^\s*\**\s*blocked\s*by\s*:?\**\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE
)
_DRE_RE = re.compile(r"\bDRE-\d+\b", re.IGNORECASE)


def parse_blocked_by(body: str) -> list[str]:
    """Card ids on a `**Blocked by:** DRE-N, DRE-M` body line → uppercased,
    de-duplicated, order-preserving. Empty when there is no such line. This is
    how prose ordering becomes real blockedBy relations (rule 3)."""
    found: list[str] = []
    for m in _BLOCKED_BY_RE.finditer(body or ""):
        for dre in _DRE_RE.findall(m.group(1)):
            up = dre.upper()
            if up not in found:
                found.append(up)
    return found


def parent_inherited_labels(parent_labels: list[str]) -> list[str]:
    """The labels a child must inherit from its parent epic (rule 2): the
    `repo:<slug>` label (so the child routes to the same repo) plus a role label.

    The role is `agent:engineer` by default, or `agent:devops` when the parent is
    an infra/pipeline epic (its slug is the shared pipeline repo, or it carries
    agent:devops itself). A child is NEVER label-less. The parent's own
    agent:planner is intentionally NOT inherited — children are work, not epics.
    """
    low = [l.lower() for l in (parent_labels or [])]
    out: list[str] = []
    repo = next((l for l in low if l.startswith("repo:") and l.split(":", 1)[1].strip()), None)
    if repo:
        out.append(repo)
    # devops iff the parent is a pipeline/infra epic.
    pipeline_repo = repo in ("repo:bureau-pipeline",)
    if "agent:devops" in low or pipeline_repo:
        out.append("agent:devops")
    else:
        out.append("agent:engineer")
    return out


def _team_label_ids(team_id: str, names: list[str]) -> list[str]:
    """Resolve label NAMES to ids on a team, creating any that don't exist.
    Idempotent on the team-label side (reuses an existing label of the same
    name)."""
    ids: list[str] = []
    seen: set[str] = set()
    for name in names:
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        lid = _team_label_id(team_id, name)
        if lid is None:
            created = gql(
                """mutation($input: IssueLabelCreateInput!) {
                     issueLabelCreate(input: $input) { issueLabel { id } } }""",
                {"input": {"name": name, "teamId": team_id}},
            )
            lid = created["issueLabelCreate"]["issueLabel"]["id"]
        ids.append(lid)
    return ids


def _add_blocked_by(issue_id: str, blocker_identifiers: list[str]) -> list[str]:
    """Create real Linear `blocks` relations so each blocker BLOCKS the new
    child (rule 3). Returns the ids that resolved; silently skips an unknown id
    rather than failing the whole create."""
    resolved: list[str] = []
    for ident in blocker_identifiers:
        blocker = get_issue(ident)
        if not blocker:
            print(f"  ! blocked-by {ident} not found — skipping relation", file=sys.stderr)
            continue
        gql(
            """mutation($input: IssueRelationCreateInput!) {
                 issueRelationCreate(input: $input) { success } }""",
            # blocker BLOCKS the child → the child is blockedBy the blocker.
            {"input": {"issueId": blocker["id"], "relatedIssueId": issue_id, "type": "blocks"}},
        )
        resolved.append(ident)
    return resolved


def cmd_subissue(parent_identifier: str, title: str, description_file: str, *flags) -> None:
    parent = get_issue(parent_identifier)

    # 1 — INLINE REAL CONTENTS. The arg is a FILE the planner drafted the card
    # to; we read its CONTENTS. If the file is missing, the planner likely passed
    # the body text (or a bare path) directly — fall back to treating the arg as
    # the body so the path-guard below catches a bare "/tmp/cardN.md".
    if os.path.isfile(description_file):
        with open(description_file) as f:
            description = f.read()
    else:
        description = description_file

    # Extra flags: --label <name> (repeatable), --blocked-by DRE-N,DRE-M.
    extra_labels, cli_blockers = _parse_flags(flags)

    # 2 — LABELS: inherit repo:<slug> + role from the parent epic, plus any
    # explicit --label. No child is ever created label-less.
    parent_labels = _issue_label_names(parent["id"])
    child_labels = parent_inherited_labels(parent_labels) + list(extra_labels)

    # 3 — ORDERING → relations: union of the body's **Blocked by:** line and any
    # --blocked-by flag. Never block on the parent epic (it deadlocks the gate).
    blockers = [
        b for b in parse_blocked_by(description) + list(cli_blockers)
        if b.upper() != parent_identifier.upper()
    ]
    # de-dup, order-preserving
    blockers = list(dict.fromkeys(b.upper() for b in blockers))

    # --- GUARD before creating: reject a broken child, do NOT create it. ---
    problem = body_problem(description)
    if problem is not None:
        raise SystemExit(
            f"subissue REJECTED ({title!r}): {problem}. "
            "Re-draft the card with real contents (not a path) and retry."
        )

    # 4 — VALIDATE through the EXISTING validate_card gate's pure core (single
    # source of truth — no parallel checker). The child must carry a resolvable
    # repo and an agent:* role once the inherited labels are applied.
    import validate_card

    gaps = validate_card.missing(description, child_labels)
    if gaps:
        raise SystemExit(
            f"subissue REJECTED ({title!r}): child fails validate_card — missing "
            + ", ".join(gaps)
            + ". The parent epic must carry a repo:<slug> label (and the body a "
            "**Repo:** line) so children inherit it."
        )

    sid = state_id(parent["team"]["id"], "Backlog")
    label_ids = _team_label_ids(parent["team"]["id"], child_labels)
    data = gql(
        """mutation($input: IssueCreateInput!) {
             issueCreate(input: $input) { success issue { id identifier url } } }""",
        {
            "input": {
                "teamId": parent["team"]["id"],
                "parentId": parent["id"],
                "title": title,
                "description": description,
                "stateId": sid,
                "labelIds": label_ids,
            }
        },
    )
    issue = data["issueCreate"]["issue"]
    rel = _add_blocked_by(issue["id"], blockers) if blockers else []
    extra = f" labels={','.join(child_labels)}"
    extra += f" blockedBy={','.join(rel)}" if rel else ""
    print(f"created {issue['identifier']} {issue['url']}{extra}")


def _parse_flags(flags) -> tuple[list[str], list[str]]:
    """Parse --label <name> (repeatable) and --blocked-by DRE-N,DRE-M from the
    trailing CLI args. Pure; returns (labels, blocker_ids)."""
    labels: list[str] = []
    blockers: list[str] = []
    it = iter(flags)
    for tok in it:
        if tok == "--label":
            labels.append(next(it))
        elif tok == "--blocked-by":
            blockers.extend(d.upper() for d in _DRE_RE.findall(next(it)))
    return labels, blockers


def _issue_label_names(issue_id: str) -> list[str]:
    """The label names currently on an issue (by node id)."""
    data = gql(
        """query($id: String!) { issue(id: $id) { labels { nodes { name } } } }""",
        {"id": issue_id},
    )
    issue = data.get("issue") or {}
    return [n["name"] for n in (issue.get("labels") or {}).get("nodes", [])]


def cmd_create(title: str, description_file: str) -> None:
    teams = gql('{ teams(filter: {key: {eq: "DRE"}}) { nodes { id } } }')
    team_id = teams["teams"]["nodes"][0]["id"]
    with open(description_file) as f:
        description = f.read()
    sid = state_id(team_id, "Triage")
    data = gql(
        """mutation($input: IssueCreateInput!) {
             issueCreate(input: $input) { success issue { identifier url } } }""",
        {
            "input": {
                "teamId": team_id,
                "title": title,
                "description": description,
                "stateId": sid,
            }
        },
    )
    issue = data["issueCreate"]["issue"]
    print(f"created {issue['identifier']} {issue['url']}")


def cmd_children(identifier: str) -> None:
    data = gql(
        """query($id: String!) { issue(id: $id) { children { nodes { id } } } }""",
        {"id": identifier},
    )
    print(len(data["issue"]["children"]["nodes"]))


def count_comments(identifier: str, needle: str) -> int:
    """How many comments on the card contain `needle`. Used by agent-task's
    dead-run requeue cap (an agent ending with no PR and no blocker note)."""
    data = gql(
        """query($id: String!) { issue(id: $id) {
             comments(last: 50) { nodes { body } } } }""",
        {"id": identifier},
    )
    return sum(
        1
        for c in data["issue"]["comments"]["nodes"]
        if needle in (c.get("body") or "")
    )


def cmd_count_comments(identifier: str, needle: str) -> None:
    print(count_comments(identifier, needle))


def comment_bodies(identifier: str) -> list[str]:
    """All comment bodies on the card, oldest→newest. Used by the model-fallback
    selector (DRE-1354) to read which model each prior attempt used / died on."""
    data = gql(
        """query($id: String!) { issue(id: $id) {
             comments(last: 50) { nodes { body } } } }""",
        {"id": identifier},
    )
    return [c.get("body") or "" for c in data["issue"]["comments"]["nodes"]]


def cmd_dump_comments(identifier: str) -> None:
    """Print the card's comment bodies as a JSON array (oldest→newest) so the
    workflow can feed them to model_fallback.py without a second API client."""
    print(json.dumps(comment_bodies(identifier)))


def _team_label_id(team_id: str, name: str) -> str | None:
    """ID of the team label named `name` (case-insensitive), or None."""
    data = gql(
        """query($teamId: String!) { team(id: $teamId) {
             labels(first: 250) { nodes { id name } } } }""",
        {"teamId": team_id},
    )
    for node in data["team"]["labels"]["nodes"]:
        if node["name"].lower() == name.lower():
            return node["id"]
    return None


def add_label(identifier: str, label_name: str) -> None:
    """Attach `label_name` to a card, creating the team label if it doesn't
    exist yet. Idempotent: a no-op if the card already carries it.

    Used by the dead/hung-run hold (DRE-1403): stamping 'needs-human' lets the
    reconcile sweep and the promotion gate recognise a card a human must look
    at and leave it untouched until the label is removed.
    """
    data = gql(
        """query($id: String!) { issue(id: $id) {
             id team { id } labels { nodes { id name } } } }""",
        {"id": identifier},
    )
    issue = data["issue"]
    existing = issue["labels"]["nodes"]
    if any(lbl["name"].lower() == label_name.lower() for lbl in existing):
        print(f"{identifier} already has label {label_name!r}")
        return
    team_id = issue["team"]["id"]
    label_id = _team_label_id(team_id, label_name)
    if label_id is None:
        created = gql(
            """mutation($input: IssueLabelCreateInput!) {
                 issueLabelCreate(input: $input) { issueLabel { id } } }""",
            {"input": {"name": label_name, "teamId": team_id}},
        )
        label_id = created["issueLabelCreate"]["issueLabel"]["id"]
    label_ids = [lbl["id"] for lbl in existing] + [label_id]
    gql(
        """mutation($id: String!, $input: IssueUpdateInput!) {
             issueUpdate(id: $id, input: $input) { success } }""",
        {"id": issue["id"], "input": {"labelIds": label_ids}},
    )
    print(f"{identifier} + label {label_name!r}")


def remove_label(identifier: str, label_name: str) -> None:
    """Detach `label_name` from a card. Idempotent: a no-op if the card does
    not carry it (and never an error if the team label doesn't exist).

    The generic inverse of add_label — kept available for any label the pipeline
    needs to clear. (It once cleared the `proposed` propose-gate marker; that
    hard-stop machinery was retired with the escalate-by-exception model,
    DRE-1655/1662, but the helper remains useful and stays.)
    """
    data = gql(
        """query($id: String!) { issue(id: $id) {
             id labels { nodes { id name } } } }""",
        {"id": identifier},
    )
    issue = data["issue"]
    existing = issue["labels"]["nodes"]
    if not any(lbl["name"].lower() == label_name.lower() for lbl in existing):
        print(f"{identifier} has no label {label_name!r} — nothing to remove")
        return
    label_ids = [
        lbl["id"] for lbl in existing if lbl["name"].lower() != label_name.lower()
    ]
    gql(
        """mutation($id: String!, $input: IssueUpdateInput!) {
             issueUpdate(id: $id, input: $input) { success } }""",
        {"id": issue["id"], "input": {"labelIds": label_ids}},
    )
    print(f"{identifier} − label {label_name!r}")


def cmd_description(identifier: str) -> None:
    """Print a card's raw description (markdown) to stdout.

    Used by the visual-QA stage (DRE-1481): the **Design:** ref lives in the
    card description, which is the authoritative source (the PR body is
    agent-authored and not guaranteed to quote it verbatim). Prints nothing
    (not an error) for a description-less card so callers can treat empty as
    "no design ref".
    """
    data = gql(
        """query($id: String!) { issue(id: $id) { description } }""",
        {"id": identifier},
    )
    issue = data.get("issue") or {}
    sys.stdout.write(issue.get("description") or "")


if __name__ == "__main__":
    cmd, *args = sys.argv[1:]
    {
        "state": cmd_state,
        "advance": cmd_advance,
        "comment": cmd_comment,
        "set-description": set_description,
        "subissue": cmd_subissue,
        "create": cmd_create,
        "children": cmd_children,
        "count-comments": cmd_count_comments,
        "dump-comments": cmd_dump_comments,
        "add-label": add_label,
        "remove-label": remove_label,
        "description": cmd_description,
    }[cmd](*args)
