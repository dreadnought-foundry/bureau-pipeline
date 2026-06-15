#!/usr/bin/env python3
"""Minimal Linear operations for the agent pipeline (stdlib only).

Subcommands:
  state <DRE-N> <state-name>           move a card to a workflow state
  advance <DRE-N> <to-state> <from-states-csv>
                                       move ONLY if current state is in the csv
                                       (guards against dragging Done cards back)
  comment <DRE-N> <body>               add a comment to a card
  subissue <DRE-N(parent)> <title> <description-file>
                                       create a child issue (Backlog) under an epic
  create <title> <description-file>    create a standalone card in Triage
  children <DRE-N>                     print the number of child issues
  add-label <DRE-N> <label-name>       attach a label (creating it if needed),
                                       idempotent — used for the human-hold
  description <DRE-N>                   print the card's raw description to
                                       stdout (the authoritative **Design:**
                                       source the visual-QA stage reads)

Auth: LINEAR_API_KEY env var.
"""

from __future__ import annotations

import json
import os
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


def cmd_subissue(parent_identifier: str, title: str, description_file: str) -> None:
    parent = get_issue(parent_identifier)
    with open(description_file) as f:
        description = f.read()
    sid = state_id(parent["team"]["id"], "Backlog")
    data = gql(
        """mutation($input: IssueCreateInput!) {
             issueCreate(input: $input) { success issue { identifier url } } }""",
        {
            "input": {
                "teamId": parent["team"]["id"],
                "parentId": parent["id"],
                "title": title,
                "description": description,
                "stateId": sid,
            }
        },
    )
    issue = data["issueCreate"]["issue"]
    print(f"created {issue['identifier']} {issue['url']}")


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
        "description": cmd_description,
    }[cmd](*args)
