#!/usr/bin/env python3
"""Reconcile sweep: Linear card states vs pipeline reality (stdlib + gh CLI).

For every atlas card in an active state, verify reality matches and nudge the
pipeline when it doesn't. Self-limiting: every nudge comments on the card,
which bumps updatedAt past the staleness threshold for the next sweep.

Checks (card must carry **Repo:** atlas, any owner-prefix form):
  Todo        stale >15m, no open PR, no fresh dispatch -> re-fire dispatch
  In Progress stale >3h: PR open -> advance In QA + trigger qa-review;
              no PR -> back to Todo (relay re-dispatches on the transition)
  In QA       stale >2h: PR merged -> Done; verdict present -> merge-gate;
              no verdict -> re-trigger qa-review; no PR -> back to Todo
  In Review   stale >1h: PR merged -> Done; else re-trigger merge-gate

Also runs the dependency gate: Backlog children whose parent epic is
In Progress (= plan approved) are auto-promoted to Todo once every blocker is
Done — blockers read from Linear's native "blocks" relations AND from
"Blocked by: DRE-N" / "serialize after DRE-N" lines in the description.
A WIP cap (MAX_WIP, default 4 active cards) throttles promotion so the
pipeline never floods.

Env: LINEAR_API_KEY, GH_TOKEN, REPO (owner/name).
"""

from __future__ import annotations

import json
import os
import re
import subprocess  # nosec B404 — fixed-arg calls to the gh CLI only
import sys
import tempfile
from datetime import UTC, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import linear_ops  # noqa: E402

REPO = os.environ["REPO"]
REPO_SLUG = os.environ.get("REPO_SLUG", "atlas")

STALE_MINUTES = {"Todo": 15, "In Progress": 180, "In QA": 120, "In Review": 60}
MAX_WIP = int(os.environ.get("MAX_WIP", "4"))

# "Blocked by: DRE-1204 + DRE-1205", "serialize after DRE-1226", "depends on
# DRE-N" — blockers are every DRE-N on a line that contains one of these
# phrases. Line-scoped on purpose: parent-epic links appear all over card
# bodies and must not count as blockers.
_BLOCKER_LINE = re.compile(r"(?:blocked by|serialize after|depends on)", re.IGNORECASE)
_CARD_REF = re.compile(r"DRE-\d+")


def gh(*args: str) -> str:
    # B603/B607: args are program-constructed (no user input), shell=False,
    # and "gh" resolves via PATH on the runner by design.
    return subprocess.run(  # nosec B603 B607
        ["gh", *args], capture_output=True, text=True, check=False
    ).stdout.strip()


def card_repo_slug(description: str) -> str | None:
    stripped = re.sub(r"```.*?```", "", description or "", flags=re.DOTALL)
    m = re.search(r"^\*\*Repo:\*\*\s*([a-z0-9._/-]+)\s*$", stripped, re.MULTILINE | re.IGNORECASE)
    return m.group(1).lower().rsplit("/", 1)[-1] if m else None


def age_minutes(iso: str) -> float:
    then = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return (datetime.now(UTC) - then).total_seconds() / 60


def active_cards() -> list[dict]:
    data = linear_ops.gql(
        """query { issues(first: 100, filter: {
             team: {key: {eq: "DRE"}},
             state: {name: {in: ["Todo", "In Progress", "In QA", "In Review"]}}
           }) { nodes {
             id identifier title description updatedAt
             state { name } labels { nodes { name } }
           } } }"""
    )
    return data["issues"]["nodes"]


def pr_for(identifier: str) -> dict | None:
    out = gh(
        "pr",
        "list",
        "--repo",
        REPO,
        "--state",
        "all",
        "--limit",
        "100",
        "--json",
        "number,headRefName,state,comments",
    )
    for pr in json.loads(out or "[]"):
        if re.search(rf"\b{identifier}\b", pr["headRefName"]):
            return pr
    return None


def has_verdict(pr: dict) -> bool:
    return any("QA Critic" in (c.get("body") or "") for c in pr.get("comments", []))


def redispatch(card: dict) -> None:
    labels = [lbl["name"].lower() for lbl in card["labels"]["nodes"]]
    event = "agent-plan" if "agent:planner" in labels else "agent-execute"
    payload = {
        "card_id": card["id"],
        "identifier": card["identifier"],
        "title": card["title"],
        "description": card["description"] or "",
        "labels": labels,
        "url": f"https://linear.app/dreadnoughtfoundry/issue/{card['identifier']}",
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump({"event_type": event, "client_payload": payload}, f)
        path = f.name
    gh("api", f"repos/{REPO}/dispatches", "--input", path)
    os.unlink(path)


def backlog_children() -> list[dict]:
    data = linear_ops.gql(
        """query { issues(first: 100, filter: {
             team: {key: {eq: "DRE"}},
             state: {name: {eq: "Backlog"}}
           }) { nodes {
             id identifier title description
             parent { identifier state { name } }
             labels { nodes { name } }
             inverseRelations(first: 20) { nodes {
               type issue { identifier state { name } }
             } }
           } } }"""
    )
    return data["issues"]["nodes"]


def card_state(identifier: str) -> str:
    data = linear_ops.gql(
        "query($id: String!) { issue(id: $id) { state { name } } }", {"id": identifier}
    )
    return data["issue"]["state"]["name"]


def blockers_of(card: dict) -> set[str]:
    found: set[str] = set()
    for rel in card["inverseRelations"]["nodes"]:
        if rel["type"] == "blocks" and rel["issue"]["state"]["name"] not in (
            "Done",
            "Canceled",
            "Duplicate",
        ):
            found.add(rel["issue"]["identifier"])
    # A card's own id and its PARENT EPIC's id are never blockers: an epic
    # only closes when its children finish, so an epic ref on a blocker line
    # deadlocks the card forever (bit DRE-1207, DRE-1216, and DRE-1233 —
    # "Serialize after: all other DRE-1200 work"). The planner brief bans
    # epic ids on blocker lines; this makes the gate immune regardless.
    parent_id = (card.get("parent") or {}).get("identifier")
    for line in (card["description"] or "").splitlines():
        if _BLOCKER_LINE.search(line):
            for ref in _CARD_REF.findall(line):
                if ref not in (card["identifier"], parent_id):
                    found.add(ref)
    return found


def promote_ready(active_count: int) -> int:
    """Auto-promote Backlog children whose blockers are all Done."""
    budget = MAX_WIP - active_count
    if budget <= 0:
        print(f"promotion: WIP at cap ({active_count}/{MAX_WIP}) — none promoted")
        return 0
    promoted = 0
    candidates = sorted(backlog_children(), key=lambda c: int(c["identifier"].split("-")[1]))
    for card in candidates:
        if promoted >= budget:
            break
        if card_repo_slug(card["description"] or "") != REPO_SLUG:
            continue
        labels = [lbl["name"].lower() for lbl in card["labels"]["nodes"]]
        if "agent:planner" in labels:
            continue  # epics are promoted by humans, never by the sweep
        parent = card.get("parent")
        if not parent or parent["state"]["name"] != "In Progress":
            continue  # parent epic not approved/active
        unmet = {
            b for b in blockers_of(card) if card_state(b) not in ("Done", "Canceled", "Duplicate")
        }
        if unmet:
            continue
        linear_ops.cmd_advance(card["identifier"], "Todo", "Backlog")
        linear_ops.cmd_comment(
            card["identifier"],
            "🧹 Auto-promoted Backlog → Todo: parent epic active and all blockers Done.",
        )
        promoted += 1
    print(f"promotion: {promoted} card(s) promoted (WIP {active_count}+{promoted}/{MAX_WIP})")
    return promoted


def close_finished_epics(epic_identifiers: set[str]) -> None:
    """An In Progress epic whose children are all terminal closes itself."""
    for epic in sorted(epic_identifiers):
        kids = linear_ops.gql(
            "query($id: String!) { issue(id: $id) { children { nodes { state { name } } } } }",
            {"id": epic},
        )["issue"]["children"]["nodes"]
        states = [k["state"]["name"] for k in kids]
        if (
            states
            and all(s in ("Done", "Canceled", "Duplicate") for s in states)
            and "Done" in states
        ):
            linear_ops.cmd_state(epic, "Done")
            linear_ops.cmd_comment(
                epic,
                f"🏁 Epic complete: all {len(states)} children are closed "
                f"({states.count('Done')} done). Closed automatically by the reconcile sweep.",
            )


def unstick_conflicts() -> None:
    """A conflicted (DIRTY) PR emits no workflow events at all — GitHub
    cannot build its test-merge commit, so pull_request workflows silently
    never run, and the merge gate's DIRTY path (which fires on those very
    events) never gets a chance. This sweep is the backstop: dispatch the
    fix agent for any open agent PR sitting in conflict. (Origin: PR #25 /
    DRE-1218 sat 35 minutes with pushes firing nothing.)"""
    busy = json.loads(gh(
        "run", "list", "--repo", REPO, "--workflow", "agent-fix.yml",
        "--limit", "10", "--json", "status",
    ) or "[]")
    if any(r["status"] in ("queued", "in_progress") for r in busy):
        print("conflict sweep: fix agent busy — retry next sweep")
        return
    prs = json.loads(gh(
        "pr", "list", "--repo", REPO, "--state", "open", "--limit", "30",
        "--json", "number,headRefName,mergeStateStatus",
    ) or "[]")
    for pr in prs:
        if not pr["headRefName"].startswith("agent/"):
            continue
        if pr.get("mergeStateStatus") != "DIRTY":
            continue
        print(f"conflict: PR #{pr['number']} ({pr['headRefName']}) DIRTY — dispatching fix agent")
        gh("workflow", "run", "agent-fix.yml", "--repo", REPO,
           "-f", f"pr_number={pr['number']}")


def retrigger_dead_heads() -> None:
    """Lost-event backstop: an open agent PR whose head commit is >15 min
    old with ZERO check-runs means GitHub dropped the push event (or
    swallowed it while the PR was conflicted) — CI and review will never
    run on that commit, so no downstream trigger can ever fire. Re-push
    the same tree as an empty commit via the git data API: a real push
    event that restarts the whole chain. Signature-based, so it acts
    within one 15-min sweep instead of waiting out a staleness timer.
    (Origin: PR #25 — two pushes fired nothing while it was conflicted.)"""
    prs = json.loads(gh(
        "pr", "list", "--repo", REPO, "--state", "open", "--limit", "30",
        "--json", "number,headRefName,mergeStateStatus,headRefOid",
    ) or "[]")
    for pr in prs:
        if not pr["headRefName"].startswith("agent/") or pr.get("mergeStateStatus") == "DIRTY":
            continue
        sha = pr["headRefOid"]
        total = gh("api", f"repos/{REPO}/commits/{sha}/check-runs", "--jq", ".total_count")
        if total.strip() not in ("", "0"):
            continue
        commit = json.loads(gh("api", f"repos/{REPO}/git/commits/{sha}") or "{}")
        when = (commit.get("committer") or {}).get("date")
        if not when or age_minutes(when) < 15:
            continue  # fresh push — give GitHub a minute to spin up checks
        print(
            f"dead head: PR #{pr['number']} {sha[:8]} has no check-runs after "
            f"{age_minutes(when):.0f}m — re-pushing as empty commit"
        )
        new = gh(
            "api", "-X", "POST", f"repos/{REPO}/git/commits",
            "-f", "message=chore: retrigger CI + review (push event was lost)",
            "-f", f"tree={commit['tree']['sha']}",
            "-f", f"parents[]={sha}",
            "--jq", ".sha",
        )
        if new:
            gh("api", "-X", "PATCH", f"repos/{REPO}/git/refs/heads/{pr['headRefName']}",
               "-f", f"sha={new}")


def fix_approved_but_red() -> None:
    """Dead-zone repair: a PR with critic APPROVE but a failed CI check has
    no automatic fixer — agent-fix's trigger is a REQUEST_CHANGES comment,
    and the gate (correctly) won't merge red. Dispatch the fix agent for any
    open agent PR in that state whose head is >20 min old (gives medic's
    auto-retry time to clear transient flakes first). Origin: PR #46 sat
    approved-but-red with nothing coming. Skips when a fix run is already
    queued/in_progress (same busy-guard as the conflict sweep)."""
    busy = json.loads(gh(
        "run", "list", "--repo", REPO, "--workflow", "agent-fix.yml",
        "--limit", "10", "--json", "status",
    ) or "[]")
    if any(r["status"] in ("queued", "in_progress") for r in busy):
        return
    prs = json.loads(gh(
        "pr", "list", "--repo", REPO, "--state", "open", "--limit", "30",
        "--json", "number,headRefName,headRefOid,mergeStateStatus,comments",
    ) or "[]")
    for pr in prs:
        if not pr["headRefName"].startswith("agent/") or pr.get("mergeStateStatus") == "DIRTY":
            continue
        verdicts = [c["body"] for c in pr.get("comments", []) if "QA Critic" in (c.get("body") or "")]
        if not verdicts or "VERDICT: APPROVE" not in verdicts[-1]:
            continue
        sha = pr["headRefOid"]
        failed = gh("api", f"repos/{REPO}/commits/{sha}/check-runs", "--jq",
                    '[.check_runs[] | select(.name | endswith("review") | not)'
                    ' | select(.conclusion // "" | IN("failure","timed_out","cancelled"))] | length')
        if failed.strip() in ("", "0"):
            continue
        commit = json.loads(gh("api", f"repos/{REPO}/git/commits/{sha}") or "{}")
        when = (commit.get("committer") or {}).get("date")
        if not when or age_minutes(when) < 20:
            continue
        print(f"approved-but-red: PR #{pr['number']} has APPROVE + {failed.strip()} failed check(s) — dispatching fix agent")
        gh("workflow", "run", "agent-fix.yml", "--repo", REPO,
           "-f", f"pr_number={pr['number']}")
        return  # one dispatch per sweep; the busy-guard handles the rest


def main() -> None:
    nudges = 0
    unstick_conflicts()
    retrigger_dead_heads()
    fix_approved_but_red()
    mine = [c for c in active_cards() if card_repo_slug(c["description"] or "") == REPO_SLUG]
    # Epics (agent:planner) are containers, not work: they carry no PR and sit
    # In Progress for the life of their children — never nudge them, and don't
    # count them against the WIP cap. They DO close themselves when finished.
    epics = {
        c["identifier"]
        for c in mine
        if any(lbl["name"].lower() == "agent:planner" for lbl in c["labels"]["nodes"])
    }
    close_finished_epics(epics)
    mine = [c for c in mine if c["identifier"] not in epics]
    promote_ready(active_count=len(mine))
    for card in mine:
        ident, state = card["identifier"], card["state"]["name"]
        if age_minutes(card["updatedAt"]) < STALE_MINUTES.get(state, 9999):
            continue

        pr = pr_for(ident)
        merged = pr is not None and pr["state"] == "MERGED"
        is_open = pr is not None and pr["state"] == "OPEN"
        print(f"stale: {ident} in {state} (pr={pr['number'] if pr else None})")

        if merged:
            linear_ops.cmd_state(ident, "Done")
            linear_ops.cmd_comment(ident, "🧹 Reconcile: PR was already merged — moved to Done.")
        elif state == "Todo" and not is_open:
            redispatch(card)
            linear_ops.cmd_comment(
                ident, "🧹 Reconcile: card sat in Todo with no run — re-dispatched."
            )
        elif state == "In Progress":
            if is_open:
                linear_ops.cmd_advance(ident, "In QA", "In Progress")
                gh(
                    "workflow",
                    "run",
                    "qa-review.yml",
                    "--repo",
                    REPO,
                    "-f",
                    f"pr_number={pr['number']}",
                )
                linear_ops.cmd_comment(
                    ident,
                    "🧹 Reconcile: PR exists but card was stuck In Progress — advanced to In QA, critic re-triggered.",
                )
            else:
                linear_ops.cmd_state(ident, "Todo")
                linear_ops.cmd_comment(
                    ident,
                    "🧹 Reconcile: agent run appears dead (no PR after 3h) — requeued to Todo.",
                )
        elif state == "In QA" and is_open:
            if has_verdict(pr):
                gh(
                    "workflow",
                    "run",
                    "merge-gate.yml",
                    "--repo",
                    REPO,
                    "-f",
                    f"pr_number={pr['number']}",
                )
                linear_ops.cmd_comment(
                    ident,
                    "🧹 Reconcile: verdict present but merge never happened — merge gate re-triggered.",
                )
            else:
                gh(
                    "workflow",
                    "run",
                    "qa-review.yml",
                    "--repo",
                    REPO,
                    "-f",
                    f"pr_number={pr['number']}",
                )
                linear_ops.cmd_comment(
                    ident, "🧹 Reconcile: no critic verdict after 2h — review re-triggered."
                )
        elif state == "In QA" and not is_open:
            linear_ops.cmd_state(ident, "Todo")
            linear_ops.cmd_comment(ident, "🧹 Reconcile: In QA with no PR — requeued to Todo.")
        elif state == "In Review" and is_open:
            gh(
                "workflow",
                "run",
                "merge-gate.yml",
                "--repo",
                REPO,
                "-f",
                f"pr_number={pr['number']}",
            )
            linear_ops.cmd_comment(
                ident, "🧹 Reconcile: stuck In Review — merge gate re-triggered."
            )
        nudges += 1
    print(f"sweep complete: {nudges} nudge(s)")


if __name__ == "__main__":
    main()
