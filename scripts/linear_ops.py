#!/usr/bin/env python3
"""Minimal Linear operations for the agent pipeline (stdlib only).

Subcommands:
  state <DRE-N> <state-name> [--park]  move a card to a workflow state. Refuses
                                       to un-complete a terminal (Done/Canceled)
                                       card (DRE-1877), and re-routes a BUILDING
                                       card (In Progress) aimed at Backlog to
                                       Todo so it re-dispatches instead of
                                       stranding (DRE-1885). `--park` opts a
                                       deliberate hold (blocker / dead-run cap)
                                       out of that reroute — Backlog is intended.
  advance <DRE-N> <to-state> <from-states-csv>
                                       move ONLY if current state is in the csv
                                       (guards against dragging Done cards back)
  comment <DRE-N> <body>               add a comment to a card
  actor <DRE-N> <role>                 record WHICH agent acted on this card, in
                                       the one machine-readable form
                                       (scripts/agent_marker.py). The briefs tell
                                       every build agent to post this at the end
                                       of its run; the run URL is added from the
                                       ambient Actions env when there is one.
  card-done <DRE-N> <pr-url>           linear-sync's merge→Done seam: move the
                                       card whose OWN agent branch merged to
                                       Done and comment the PR link — UNLESS
                                       the card is operator-closed (`no-code`
                                       label) or closes-on-evidence (`DEMO:`
                                       title), in which case the merge is
                                       commented but the state is left alone
                                       (the six portico false closes)
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
                                       created broken. A parent that is NOT
                                       already an epic is also REJECTED — giving
                                       a card children reclassifies it as an
                                       epic and it stops ever being promoted
                                       (DRE-2739; file a sibling instead, see
                                       scripts/mid_epic.py). Optional flags:
                                         --label <name>   (repeatable) extra label
                                         --blocked-by DRE-N,DRE-M  (also parsed
                                                          from the body line)
  oneoff <title> <description-file>    create a PARENTLESS card (Backlog) — the
                                       one-off route's producer (DRE-2754). Same
                                       body guard, validate_card gate and
                                       blockedBy handling as `subissue`, but the
                                       PLAN supplies the labels (there is no
                                       parent to inherit repo:<slug> from):
                                         --label repo:<slug> --label agent:<role>
                                         --label initiative:<x>
                                         --blocked-by DRE-N,DRE-M
  create <title> <description-file>    create a standalone card in Triage
  find-open <title>                    print the identifier of an existing
                                       non-terminal (not Done/Canceled) card
                                       with exactly this title, else nothing —
                                       red-main-repair's escalation dedup
  children <DRE-N>                     print the number of child issues
  count-comments <DRE-N> <needle> [--since <marker>]
                                       print how many comments contain <needle>.
                                       With --since, only those AFTER the most
                                       recent comment containing <marker> — how
                                       the dead-run cap honours a budget reset
  add-label <DRE-N> <label-name>       attach a label (creating it if needed),
                                       idempotent — used for the human-hold
  remove-label <DRE-N> <label-name>    detach a label, idempotent — a no-op if
                                       absent (generic; mirrors add-label)
  unpark <DRE-N> [note]                the operator's release for a card held
                                       by the dead-run cap: clear 'needs-human',
                                       post the dead-run-budget-reset marker,
                                       and return the card to Todo — in that
                                       order, so the re-dispatched run reads a
                                       FRESH death budget instead of the
                                       exhausted history that held it
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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agent_marker  # noqa: E402 — ONE definition of the "which agent acted" marker
import dead_run  # noqa: E402 — the dead-run tags/cap live in ONE module
import lane_scope  # noqa: E402 — the lane contract, incl. the pending rename
import mid_epic  # noqa: E402 — ONE source for "a card has no children" (DRE-2739)

API = "https://api.linear.app/graphql"


class LinearError(RuntimeError):
    """A Linear API/reference error (errors payload, unknown state, rejected
    sub-issue). A REAL exception — never SystemExit — so library callers can
    isolate a failure to one card instead of dying wholesale: SystemExit
    subclasses BaseException and sailed past every `except Exception` in the
    reconcile sweep, letting one typo'd blocker reference kill the entire
    sweep every run (DRE-2035). The CLI __main__ converts it to a nonzero
    exit explicitly at top level, so command-line behavior is unchanged."""


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
        raise LinearError(f"linear error: {out['errors']}")
    return out["data"]


def gql_paged(
    query: str, variables: dict | None = None, *, connection: str = "issues"
) -> list[dict]:
    """Every node of a paginated Linear connection, followed to exhaustion.

    Linear serves at most 100 nodes per page and says so ONLY in `pageInfo` — a
    query that never selects it gets the first page and no way to know another
    exists. That is silent by construction, and it is what made the reconcile
    sweep's world the first 100 rows of a 226-card Backlog: 126 cards were not
    promotion candidates, not by policy, not reported anywhere, and WHICH 126
    was decided by Linear's default ordering (DRE-2681).

    `query` MUST declare `$after: String`, pass it as the connection's `after:`
    argument, and select `pageInfo { hasNextPage endCursor }`. A query that
    can't paginate is rejected here rather than silently returning page one —
    the failure this exists to end must never be reintroduced quietly.

    `connection` names the top-level field to walk (`issues` for every caller
    today). Terminates on a missing or repeated cursor: a server that keeps
    claiming another page without advancing must not hang a ten-minute sweep.
    """
    if "$after" not in query:
        raise ValueError(
            "gql_paged: the query must declare $after and select "
            "pageInfo { hasNextPage endCursor } — without a cursor it can only "
            "ever return the first 100 nodes"
        )
    nodes: list[dict] = []
    after: str | None = None
    seen: set[str] = set()
    while True:
        conn = gql(query, {**(variables or {}), "after": after})[connection]
        nodes.extend(conn.get("nodes") or [])
        info = conn.get("pageInfo") or {}
        if not info.get("hasNextPage"):
            return nodes
        after = info.get("endCursor")
        if not after or after in seen:
            # hasNextPage with no usable cursor: return what we have rather
            # than re-requesting the same page forever.
            print(
                f"gql_paged: {connection} claims another page with cursor "
                f"{after!r} — stopping at {len(nodes)} node(s)",
                file=sys.stderr,
            )
            return nodes
        seen.add(after)


def get_issue(identifier: str) -> dict:
    data = gql(
        """query($id: String!) { issue(id: $id) {
             id identifier title team { id } state { name type }
             labels { nodes { name } }
           } }""",
        {"id": identifier},
    )
    return data["issue"]


def state_id(team_id: str, name: str) -> str:
    """The workflow-state id for `name` on a team. (Use `state_id_and_type` when
    the caller also needs the state's lifecycle type.)"""
    return state_id_and_type(team_id, name)[0]


# Transitional lane fallbacks (DRE-2722). Renaming a lane is a MANUAL act in the
# Linear workspace, and the code that names the new lane necessarily lands first
# — so between this merge and that click the live team still answers with the
# retired name only. An exact-match-only lookup turns that gap into a hard
# failure: `plan.yml`'s approve-the-plan step raises on the FIRST epic that
# finishes planning, the epic never reaches the CEO, and the one gate the plan
# flow depends on for sign-off is silently dead until someone reads a workflow
# log.
#
# So a renamed lane resolves to what the board actually HAS, in this order:
# exact name first, pre-rename name only if the new one is genuinely absent. The
# day the workspace is renamed the exact match wins and this goes quiet on its
# own — which is when the alias gets deleted, in one place, for both readers.
#
# That one place is `lane_scope.LANE_ALIASES`, which already records the same
# pending rename for the guard's lane contract (DRE-2754). It maps board name →
# contract name because it normalizes what it READS; this is the same fact in
# the direction we WRITE, so it is inverted rather than restated — one literal,
# one deletion when the board catches up.
#
# Narrow on purpose: only a lane the repo has actually renamed gets a fallback.
# A lane that was never renamed (Triage) must keep failing loud, because there a
# missing state is a real misconfiguration and a fallback would hide it.
_LANE_RENAME_FALLBACKS = {
    new.lower(): (old,) for old, new in lane_scope.LANE_ALIASES.items()
}


def state_id_and_type(team_id: str, name: str) -> tuple[str, str]:
    """`(id, type)` for the named workflow state. `type` is Linear's lifecycle
    bucket — one of: backlog, unstarted, started, completed, canceled — which is
    what tells terminal (completed/canceled) states apart from in-flight ones.

    A lane this repo renamed falls back to its pre-rename name while the live
    board still carries it (see `_LANE_RENAME_FALLBACKS`)."""
    data = gql(
        """query($teamId: ID) { workflowStates(filter: {team: {id: {eq: $teamId}}}) {
             nodes { id name type } } }""",
        {"teamId": team_id},
    )
    nodes = data["workflowStates"]["nodes"]
    for node in nodes:
        if node["name"].lower() == name.lower():
            return node["id"], node["type"]
    for legacy in _LANE_RENAME_FALLBACKS.get(name.lower(), ()):
        for node in nodes:
            if node["name"].lower() == legacy.lower():
                print(
                    f"state: {name!r} is not on the board yet — using its "
                    f"pre-rename lane {legacy!r} (DRE-2722, transitional)"
                )
                return node["id"], node["type"]
    raise LinearError(f"no state named {name!r} on team")


# Linear lifecycle buckets that mean "this card is finished, do not reopen it".
_TERMINAL_TYPES = ("completed", "canceled")

# The hold label a DELIBERATE human-hold stamps on a card before parking it in
# Backlog (DRE-1403). Its presence is the native "a human owns this now" signal
# that exempts a Backlog park from the building-card reroute below.
_HOLD_LABEL = dead_run.HOLD_LABEL  # "needs-human"


def _label_names(issue: dict) -> list[str]:
    return [
        (lbl.get("name") or "")
        for lbl in ((issue.get("labels") or {}).get("nodes") or [])
    ]


def _clobbered_terminal_state(identifier: str, target_id: str) -> dict | None:
    """Read our own write back out of Linear's issue history and return the
    TERMINAL state it overwrote, if it overwrote one.

    Linear records `fromState` on every state transition, so the entry we just
    created answers the only question a pre-write check cannot: what the card
    ACTUALLY was at the instant of the mutation. Newest entry whose `toState`
    is the state we just set is ours.
    """
    data = gql(
        """query($id: String!) { issue(id: $id) {
             history(last: 10) { nodes {
               createdAt fromState { id name type } toState { id name type }
             } } } }""",
        {"id": identifier},
    )
    nodes = (((data.get("issue") or {}).get("history") or {}).get("nodes")) or []
    for node in reversed(nodes):  # newest last in Linear's history(last: n)
        if ((node.get("toState") or {}).get("id")) != target_id:
            continue
        frm = node.get("fromState") or {}
        return frm if frm.get("type") in _TERMINAL_TYPES else None
    return None


def _set_state(identifier: str, issue_id: str, state_id_value: str) -> None:
    gql(
        """mutation($id: String!, $input: IssueUpdateInput!) {
             issueUpdate(id: $id, input: $input) { success } }""",
        {"id": issue_id, "input": {"stateId": state_id_value}},
    )


def guarded_state_write(
    identifier: str,
    issue: dict,
    target_id: str,
    target_type: str,
    state_name: str,
) -> bool:
    """Write a card's state with the tightest terminal guard Linear allows.

    Returns True if the write happened, False if it was refused.

    THIS IS NOT ATOMIC, and nothing here claims it is. Linear's GraphQL API
    offers no compare-and-set: `issueUpdate` takes only `(id, input)`, and
    `IssueUpdateInput` has no expected-version / if-match / updatedAt
    precondition field (schema introspected 2026-08-09 — stateId, title,
    labels… and nothing conditional). There is no conditional-update mutation
    either. So a check-then-act window is unavoidable; what this does is make
    it as small as the API permits and make LOSING it survivable:

      1. RE-READ the card immediately before the mutation and refuse if it has
         become terminal. The DRE-1877 guard already refused a terminal card —
         but it decided from a read taken BEFORE the caller's own work, so the
         window was seconds wide. DRE-2316 lost it by 280 ms: linear-sync set
         DRE-2316 Done on the PR #137 merge at 22:22:30.349, and the dead-run
         requeue — which had read "In Review" — wrote Todo at 22:22:30.629.
         The card then dispatched a second agent onto shipped work. After (1)
         the window is one API round trip.

      2. READ THE WRITE BACK. Linear's issue history records the `fromState`
         of the transition we just made, so a Done that landed inside the
         RESIDUAL window is visible after the fact — and we put it back.

    THE RESIDUAL, stated honestly: between the re-read in (1) and the mutation
    a concurrent terminal transition can still land. (2) is a compensating
    write, not prevention: the card really does flap Done → ours → Done, and
    anything that reacts to the intermediate state (the relay dispatches on a
    Todo transition) has already reacted by the time we repair it. What (2)
    buys is that the FINISHED state is the one that survives, instead of a Done
    card sitting silently in Todo. Closing the window completely needs a
    primitive Linear does not expose.

    The read-back is best effort: it verifies a write that already succeeded,
    so an unreadable history degrades to "narrowed window, no repair" (printed
    loudly) rather than failing the transition.
    """
    terminal_target = target_type in _TERMINAL_TYPES
    if not terminal_target:
        # (1) The pre-write re-read. Skipped for a terminal target: closing a
        # card is always allowed, so there is nothing to refuse.
        fresh = get_issue(identifier)
        fresh_state = (fresh.get("state") or {}).get("type")
        fresh_name = (fresh.get("state") or {}).get("name", fresh_state)
        if fresh_state in _TERMINAL_TYPES:
            print(
                f"{identifier} became {fresh_name!r} (terminal) between this "
                f"transition's decision and its write — refusing to move it to "
                f"{state_name!r}; a finished card is ground truth and is never "
                f"reopened by an automated transition."
            )
            return False
    _set_state(identifier, issue["id"], target_id)
    if terminal_target:
        return True
    # (2) The read-back repair.
    try:
        clobbered = _clobbered_terminal_state(identifier, target_id)
    except Exception as e:  # noqa: BLE001 — best-effort verification of a done write
        print(
            f"WARNING: {identifier} → {state_name} written, but the verification "
            f"read failed ({e}) — a terminal state that landed in the residual "
            f"race window would go unrepaired."
        )
        return True
    if clobbered:
        _set_state(identifier, issue["id"], clobbered["id"])
        print(
            f"{identifier} was {clobbered.get('name')!r} (terminal) at the instant "
            f"of the write — the move to {state_name!r} raced a finished card and "
            f"lost. Restored {clobbered.get('name')!r}. A finished card is ground "
            f"truth (DRE-1877/DRE-2316)."
        )
    return True


def cmd_state(identifier: str, state_name: str, *flags: str) -> None:
    # `--park` (passed by the DELIBERATE park callers: the agent-task blocker
    # branch + both dead-run/hung HOLD-cap paths) opts a Backlog move out of the
    # building-card reroute below. Everything else is treated as an ordinary
    # transition that must NOT be allowed to strand a building card.
    deliberate_park = "--park" in flags
    issue = get_issue(identifier)
    target_id, target_type = state_id_and_type(issue["team"]["id"], state_name)
    # Ground-truth guard (DRE-1877): a merged PR moves its card to Done, and Done
    # is the truth. Never let a LATER, non-terminal transition drag a finished
    # card (completed/canceled) back into the working lanes. This is what stranded
    # DRE-1803 in DeltaSolv: linear-sync set it Done on the PR #16 merge, then a
    # concurrent duplicate run's dead-run HOLD path did `state Backlog` ~9 min
    # later and clobbered it — so the Done card looked unfinished, its dependent
    # never promoted, and the epic stalled. The hold/requeue/blocker parks all
    # route through here, so guarding the seam fixes every one of them at once.
    # Forward/terminal moves (→ Done, → Canceled, Done→Done idempotency) are
    # always allowed; only un-completing a terminal card is refused.
    #
    # This first check is an EARLY OUT on a read that is already stale by the
    # time we act on it — DRE-2316 lost that race by 280 ms. The load-bearing
    # guard is in guarded_state_write() below, which re-reads immediately
    # before the mutation and repairs a terminal state it clobbered anyway.
    current_type = (issue.get("state") or {}).get("type")
    current_name = (issue.get("state") or {}).get("name", current_type)
    if current_type in _TERMINAL_TYPES and target_type not in _TERMINAL_TYPES:
        print(
            f"{identifier} is {current_name!r} (terminal) — refusing to move it to "
            f"{state_name!r}; a finished card is ground truth and is never reopened "
            f"by an automated transition."
        )
        return
    # Building-card guard (DRE-1885, follow-on to DRE-1877, one lifecycle state
    # earlier): a card that is actively BUILDING — current state-type `started`
    # (In Progress) — must never be silently knocked into Backlog, where nothing
    # re-promotes it and it strands. This is what stranded DRE-1822: it went Todo
    # → In Progress at 20:08, a hold/park reverted it to Backlog at 20:13 during
    # the Actions-budget-block + dead-run window, and it sat ~3h until a human
    # re-promoted it — stalling epic E4.1. Same class as DRE-1803, but on the
    # building card rather than the finished one.
    #
    # If a building card genuinely must be re-queued, send it to Todo (which
    # re-dispatches via the cascade), NEVER Backlog (inert — nothing picks it up).
    # Two exemptions keep the LEGITIMATE Backlog parks working:
    #   * `--park` — a deliberate hold caller (the blocker branch, whose Todo
    #     would redispatch agents into the same wall — DRE-1286; and the HOLD-cap
    #     after REQUEUE_CAP dead runs — DRE-1403/1572), and
    #   * the card already carries the `needs-human` hold label (a human owns it).
    # A not-started card (backlog/unstarted/triage) dropped to Backlog — an
    # explicit operator/CEO park, or a card that never started — is untouched.
    if (
        current_type == "started"
        and target_type == "backlog"
        and not deliberate_park
        and _HOLD_LABEL not in [n.lower() for n in _label_names(issue)]
    ):
        todo_id, todo_type = state_id_and_type(issue["team"]["id"], "Todo")
        # Same guarded write as the ordinary path: the reroute is a write like
        # any other, and an unguarded one would simply become the new way to
        # clobber a card that went Done mid-decision (DRE-2316).
        if not guarded_state_write(identifier, issue, todo_id, todo_type, "Todo"):
            return
        print(
            f"{identifier} is {current_name!r} (building) — re-queued to 'Todo' "
            f"instead of {state_name!r}; an actively-building card is never parked "
            f"in Backlog (inert, nothing re-promotes it), only re-dispatched via "
            f"Todo. Pass --park (or stamp 'needs-human') for a deliberate hold."
        )
        return
    if guarded_state_write(identifier, issue, target_id, target_type, state_name):
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
    sid, stype = state_id_and_type(issue["team"]["id"], to_state)
    # The from-states csv is checked against the read ABOVE, so this seam has
    # the same check-then-act race cmd_state had: agent-task's PR path runs
    # `advance <card> "In Review" "In Progress,Todo"`, and a card that goes Done
    # in between would be dragged back into review. Same guarded write (DRE-2316).
    if guarded_state_write(identifier, issue, sid, stype, to_state):
        print(f"{identifier} → {to_state}")


def set_description(identifier: str, body: str) -> None:
    """Overwrite a card's description (generic helper / `set-description` CLI).

    NOTE (DRE-1699): the Todo-entry gate's fix-first repair NO LONGER calls this
    to prepend a `**Repo:** <slug>` stamp — the `repo:<slug>` label is now the
    canonical repo signal, so the gate adds only the label and leaves the body
    alone. This remains a general-purpose description setter.
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


def cmd_actor(identifier: str, role: str) -> None:
    """Record which agent acted on this card (DRE-2727).

    A thin wrapper over `comment` on purpose: the VALUE is that the string has
    exactly one author. Six briefs each spelling out a marker is six markers
    within a month, and nothing downstream can parse the set.
    """
    cmd_comment(identifier, agent_marker.actor_line(role))


# --- Auto-Done guard: operator cards and demo cards ---------------------------
#
# Six false card-closes in portico, one mechanism (2026-07/08): the merge→Done
# path closes the card whose own agent/DRE-<n>- branch merged, but for two card
# classes the MERGE is not the WORK:
#
#   * `no-code` operator cards — their substance is live AWS work (a deploy, a
#     migration, a key in Secrets Manager). An agent merges the RUNBOOK and the
#     card closes as if the AWS work happened: DRE-2242 closed TWICE while zero
#     CloudFront key groups existed in any account; DRE-2241 closed while the
#     security exposure it existed to close was still open; DRE-2218 (Operator
#     Milestone 2) closed when its runbook merged, before any migration ran.
#   * `DEMO:`-titled cards — their acceptance criteria say the card closes only
#     when every end-state claim in docs/demos/phase-N.md is a PASS. No merge
#     event can read a verdict inside a markdown file: DRE-2253 and DRE-2252
#     closed while their reports said "NOT demonstrated" in those words.
#
# The false closes cascade: epic DRE-2169 closed via --close-epics because all
# its children (falsely) read Done. Guarding the CHILD transitions fixes the
# epic cascade with no change to the epic logic.
#
# BOTH auto-Done paths — linear-sync's `card-done` and reconcile's merged-PR
# backstop — consult this one function, so the close cannot sneak back in
# through either path without deleting the guard itself. The linear-sync.yml
# header's promise ("hand-named branches auto-Done NOTHING: the operator closes
# those cards by hand") was defeated whenever an agent branch pointed at an
# operator card; this restores it for the card classes where it matters.

NO_CODE_LABEL = "no-code"  # standards/card-quality.md: operator-work cards

# Title must START with `DEMO:` (case-insensitive, leading whitespace allowed).
# Anchored on purpose: a card that merely MENTIONS demos — in its body or
# mid-title ("Update demo docs", "Record the demo: phase 3") — is an ordinary
# code card and still auto-closes.
_DEMO_TITLE_RE = re.compile(r"^\s*demo:", re.IGNORECASE)

# Shared marker for the "merged but deliberately left open" card comment:
# card-done posts it at merge time; reconcile's backstop greps for it
# (count_comments) so the note lands exactly once however many sweeps pass
# while the operator finishes the live work.
MERGED_NOT_CLOSED_MARKER = "Merged — card deliberately left open"


def auto_done_skip_reason(title: str, labels: list[str]) -> str | None:
    """Why a merged PR must NOT auto-Done this card, or None to proceed.

    Pure (no I/O) so tests pin it directly. Takes the card's TITLE and label
    names only — never the PR title/body (prose is not provenance, DRE-2027)
    and never the card body (a body that mentions demos is not a demo card).
    """
    if NO_CODE_LABEL in [l.lower() for l in labels]:
        return (
            f"this card carries the '{NO_CODE_LABEL}' label — its deliverable "
            "is live operator work (a deploy, a migration, a secret), which a "
            "merged runbook PR does not perform"
        )
    if _DEMO_TITLE_RE.match(title or ""):
        return (
            "this card is 'DEMO:'-titled — it closes only on evidence (every "
            "end-state claim in its demo report a PASS), which a merge event "
            "cannot attest"
        )
    return None


def merged_not_closed_comment(pr_url: str, reason: str) -> str:
    """The card comment for a merge that deliberately does NOT close the card:
    the merge stays visible on the card without the state lying."""
    return (
        f"🔒 {MERGED_NOT_CLOSED_MARKER}: {pr_url}\n\n"
        f"This PR merged, but {reason}. This card is operator-closed / "
        "closes-on-evidence and was deliberately NOT auto-closed — close it "
        "by hand when the real work is done."
    )


def cmd_card_done(identifier: str, pr_url: str) -> None:
    """linear-sync's merge→Done seam, guard included (see block comment above).

    Ordinary code cards: → Done + "✅ Merged" comment, byte-identical to the
    old inline `state`/`comment` pair. `no-code` / `DEMO:` cards: comment the
    merge, leave the state alone, and say so LOUDLY in the job log.

    Break-glass cards (DRE-2737) are the third class: the fix has shipped, so
    the debt comes due — the card returns to Planning for the classification
    it skipped instead of going Done. The decision reads the `break-glass:used`
    RECEIPT, never the live marker, so an operator who tidies the marker off
    mid-flight neither strands the card nor erases what it owes.
    """
    import break_glass

    issue = get_issue(identifier)
    labels = _label_names(issue)
    reason = auto_done_skip_reason(issue.get("title") or "", labels)
    if reason is not None:
        # The operator/demo guard wins on the STATE (it is never moved here),
        # but a break-glass debt on the same card is still recorded, not
        # silently dropped — the card stays open, so the note is where the
        # operator will see it.
        if break_glass.owes_review(labels):
            cmd_comment(identifier, break_glass.review_notice(pr_url, moved=False))
        banner = "=" * 72
        print(banner)
        print(f"AUTO-DONE SKIPPED for {identifier}: {reason}.")
        print(
            "The merge was commented on the card; its state is untouched. "
            "(Six false closes in portico: DRE-2242 x2, DRE-2241, DRE-2218, "
            "DRE-2253, DRE-2252.)"
        )
        print(banner)
        cmd_comment(identifier, merged_not_closed_comment(pr_url, reason))
        return
    if break_glass.owes_review(labels):
        # The comment lands BEFORE the move: the Planning transition is what
        # queues the skipped classification, and the run it starts should find
        # the receipt already on the card (same ordering rule as cmd_unpark).
        print(
            f"BREAK-GLASS DEBT for {identifier}: the gate was bypassed on this "
            f"card, so the merge returns it to {break_glass.REVIEW_STATE} for the "
            "classification it skipped rather than closing it (DRE-2737)."
        )
        cmd_comment(identifier, break_glass.review_notice(pr_url))
        cmd_state(identifier, break_glass.REVIEW_STATE)
        return
    cmd_state(identifier, "Done")
    cmd_comment(identifier, f"✅ Merged: {pr_url}")


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
    `repo:<slug>` label (so the child routes to the same repo), the parent's
    `initiative:<x>` label(s), and a role label.

    The `initiative:*` label is load-bearing, but NOT for the reason this said
    for a year: promotion does not read it. `reconcile.py` never mentions
    `initiative` at all (DRE-2681 checked; test_initiative_claim_matches_the_code
    keeps it honest). Two real things break without it — the create seam refuses
    the child outright (`validate_card.missing(..., require_initiative=True)`,
    enforced below in `_reject_unless_creatable`), and `validate_card.infer_repo`
    loses step 2a, its first route to a repo for a card carrying no `repo:`
    label. Inheriting it deterministically is what keeps both from biting.

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
    # Inherit every `initiative:<x>` label the parent carries (non-empty slug),
    # order-preserving — the create seam refuses a child without one.
    for l in low:
        if l.startswith("initiative:") and l.split(":", 1)[1].strip() and l not in out:
            out.append(l)
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


def _card_body(description_file: str) -> str:
    """INLINE REAL CONTENTS. The arg is a FILE the planner drafted the card to;
    we read its CONTENTS. If the file is missing, the planner likely passed the
    body text (or a bare path) directly — fall back to treating the arg as the
    body so the path-guard catches a bare "/tmp/cardN.md"."""
    if os.path.isfile(description_file):
        with open(description_file) as f:
            return f.read()
    return description_file


def _reject_unless_creatable(kind: str, title: str, description: str,
                             labels: list[str], hint: str) -> None:
    """GUARD before creating: reject a broken card, do NOT create it.

    Shared by every planner create seam (`subissue`, `oneoff`) so a one-off is
    held to exactly the checks a planned child is — the path-guard on the body,
    then the EXISTING validate_card gate's pure core (single source of truth —
    no parallel checker). `hint` says how to fix it for that seam."""
    problem = body_problem(description)
    if problem is not None:
        raise LinearError(
            f"{kind} REJECTED ({title!r}): {problem}. "
            "Re-draft the card with real contents (not a path) and retry."
        )

    import validate_card

    gaps = validate_card.missing(description, labels, require_initiative=True)
    if gaps:
        raise LinearError(
            f"{kind} REJECTED ({title!r}): card fails validate_card — missing "
            + ", ".join(gaps)
            + ". "
            + hint
        )


def _create_card(team_id: str, title: str, description: str, labels: list[str],
                 blockers: list[str], *, parent_id: str | None = None) -> None:
    """Create a validated card in `Backlog` with its labels and blockedBy
    relations, and print the receipt. `parent_id=None` creates a PARENTLESS card
    — the one-off route has no epic to hang under (DRE-2754)."""
    sid = state_id(team_id, "Backlog")
    label_ids = _team_label_ids(team_id, labels)
    card_input = {
        "teamId": team_id,
        "title": title,
        "description": description,
        "stateId": sid,
        "labelIds": label_ids,
    }
    if parent_id is not None:
        card_input["parentId"] = parent_id
    data = gql(
        """mutation($input: IssueCreateInput!) {
             issueCreate(input: $input) { success issue { id identifier url } } }""",
        {"input": card_input},
    )
    issue = data["issueCreate"]["issue"]
    rel = _add_blocked_by(issue["id"], blockers) if blockers else []
    extra = f" labels={','.join(labels)}"
    extra += f" blockedBy={','.join(rel)}" if rel else ""
    print(f"created {issue['identifier']} {issue['url']}{extra}")
    return issue


def cmd_subissue(parent_identifier: str, title: str, description_file: str, *flags) -> dict:
    parent = get_issue(parent_identifier)

    # 1 — INLINE REAL CONTENTS (never the path).
    description = _card_body(description_file)

    # Extra flags: --label <name> (repeatable), --blocked-by DRE-N,DRE-M.
    extra_labels, cli_blockers = _parse_flags(flags)

    # 2 — LABELS: inherit repo:<slug> + role from the parent epic, plus any
    # explicit --label. No child is ever created label-less.
    parent_labels = _issue_label_names(parent["id"])
    child_labels = child_labels_from(parent_labels, list(extra_labels))

    # 2b — A CARD HAS NO CHILDREN (DRE-2739). Refuse BEFORE any write: giving a
    # plain card sub-issues silently reclassifies it as an epic (validate_card
    # reads children as epic-ness) and reconcile then never promotes it. The
    # refusal names that consequence and the sibling route that works.
    # The two free epic tests come first: a parent already classified as an epic
    # cannot be reclassified by one more child, so the children read is only
    # bought when the title and the labels both say no — the planner's own path
    # (every parent is agent:planner) buys nothing at all.
    if not mid_epic.is_epic(parent.get("title"), parent_labels, has_children=False):
        refusal = mid_epic.subissue_refusal(
            parent.get("title"), parent_labels, _issue_has_children(parent["id"])
        )
        if refusal is not None:
            raise LinearError(f"subissue REJECTED ({title!r}): {refusal}")

    # 3 — ORDERING → relations: union of the body's **Blocked by:** line and any
    # --blocked-by flag. Never block on the parent epic (it deadlocks the gate).
    blockers = [
        b for b in parse_blocked_by(description) + list(cli_blockers)
        if b.upper() != parent_identifier.upper()
    ]
    # de-dup, order-preserving
    blockers = list(dict.fromkeys(b.upper() for b in blockers))

    # 4 — GUARD before creating: reject a broken child, do NOT create it. The
    # child must carry a resolvable repo and an agent:* role once the inherited
    # labels are applied.
    _reject_unless_creatable(
        "subissue", title, description, child_labels,
        "The parent epic must carry a repo:<slug> label AND an "
        "initiative:<x> label so children inherit them (DRE-1699: the repo "
        "LABEL is the source of truth — no **Repo:** stamp needed).",
    )

    # 5 — CREATE, under the epic, in Backlog. The children sit there while the
    # epic is still pre-approval, and the guard leaves them alone until the epic
    # passes Planning exit (DRE-2754 — see scripts/lane_scope.py).
    # RETURNED, not just printed: the mid-epic route needs the identifier it
    # just created so it can record the growth on the epic in the same motion
    # (DRE-2739, consumed at mid_epic.py:483). The CLI path ignores the value,
    # so behaviour there is unchanged.
    return _create_card(parent["team"]["id"], title, description, child_labels,
                        blockers, parent_id=parent["id"])


def cmd_oneoff(title: str, description_file: str, *flags) -> None:
    """Create a PARENTLESS card in Backlog — the one-off route's producer.

    `cmd_subissue` requires a parent and takes the child's `repo:` label from
    `parent_inherited_labels()`. A one-off has no parent by definition, so it
    had no API and no source for the `repo:` label that DRE-2744 makes the only
    routing key. Here the plan supplies the labels directly:

        linear_ops.py oneoff "<title>" <desc-file> \\
          --label repo:<slug> --label initiative:<x> --label agent:engineer

    Same body path-guard, same validate_card gate, same **Blocked by:** →
    relation handling, same Backlog landing as a planned child (DRE-2754).
    """
    description = _card_body(description_file)
    labels, cli_blockers = _parse_flags(flags)

    # ORDERING → relations: union of the body's **Blocked by:** line and the
    # flag. No parent epic to strip out — a one-off has none.
    blockers = list(dict.fromkeys(
        b.upper() for b in parse_blocked_by(description) + list(cli_blockers)
    ))

    _reject_unless_creatable(
        "oneoff", title, description, labels,
        "A one-off inherits nothing, so the PLAN must supply every label: "
        "--label repo:<slug> --label initiative:<x> --label agent:<role>.",
    )

    teams = gql('{ teams(filter: {key: {eq: "DRE"}}) { nodes { id } } }')
    _create_card(teams["teams"]["nodes"][0]["id"], title, description, labels,
                 blockers)


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


def _issue_has_children(issue_id: str) -> bool:
    """Does this issue already have sub-issues? The third leg of the epic test
    (DRE-2739): a parent that already has children is already an epic, so one
    more child changes nothing about what it is."""
    data = gql(
        """query($id: String!) { issue(id: $id) { children { nodes { id } } } }""",
        {"id": issue_id},
    )
    issue = data.get("issue") or {}
    return bool((issue.get("children") or {}).get("nodes"))


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


def cmd_find_open(title: str) -> None:
    """Print the identifier of an existing card with exactly `title` that is
    not completed/canceled, else print nothing. red-main-repair.yml calls
    this before creating an escalation/triage card so duplicate failure
    events for the same red main never mint duplicate cards; terminal cards
    deliberately don't count — a re-broken main deserves a fresh card."""
    data = gql(
        """query($t: String!) {
             issues(filter: {
               title: {eq: $t},
               state: {type: {nin: ["completed", "canceled"]}}
             }, first: 1) { nodes { identifier } } }""",
        {"t": title},
    )
    nodes = data["issues"]["nodes"]
    if nodes:
        print(nodes[0]["identifier"])


def cmd_children(identifier: str) -> None:
    data = gql(
        """query($id: String!) { issue(id: $id) { children { nodes { id } } } }""",
        {"id": identifier},
    )
    print(len(data["issue"]["children"]["nodes"]))


def count_comments(identifier: str, needle: str, *, since: str | None = None) -> int:
    """How many comments on the card contain `needle`. Used by agent-task's
    dead-run requeue cap (an agent ending with no PR and no blocker note).

    `since` — count only the comments AFTER the most recent one containing this
    marker. THE ONE definition of reset-aware counting; every death count in the
    pipeline routes through here, so the cap cannot drift between the two
    reconcile sites and the in-run Report step.

    Why it exists: the dead-run cap is counted by substring-matching comments,
    and nothing reset the count — not even a human un-parking the card. The hold
    comment itself carries `dead-run-requeue`, so a released card walked back in
    with its whole exhausted history and re-held on its FIRST subsequent death
    (DRE-2308/2309/2310, held by a fleet-wide model misconfiguration that had
    nothing to do with the cards). `linear_ops.py unpark` posts
    `dead_run.RESET_TAG` and this argument makes the counter honour it.

    Substring counting means the two markers must never contain one another —
    see dead_run.RESET_TAG's note and the tests that pin both directions.

    The fetch window (`comments(last: 50)`) is unchanged: `since` changes WHICH
    of those comments count, never HOW MANY are read. With `since=None` (the
    generic uses: MERGED_NOT_CLOSED_MARKER, the bad-blocker tag) the behaviour is
    byte-identical to before.
    """
    data = gql(
        """query($id: String!) { issue(id: $id) {
             comments(last: 50) { nodes { body } } } }""",
        {"id": identifier},
    )
    bodies = [(c.get("body") or "") for c in data["issue"]["comments"]["nodes"]]
    if since:
        # Oldest→newest, so the LAST marker in the list is the most recent reset.
        for i in range(len(bodies) - 1, -1, -1):
            if since in bodies[i]:
                bodies = bodies[i + 1:]
                break
    return sum(1 for body in bodies if needle in body)


def cmd_count_comments(identifier: str, needle: str, *flags: str) -> None:
    """`count-comments <DRE-N> <needle> [--since <marker>]` — see count_comments."""
    since = None
    if "--since" in flags:
        i = flags.index("--since")
        if i + 1 < len(flags):
            since = flags[i + 1]
    print(count_comments(identifier, needle, since=since))


def comment_bodies(identifier: str) -> list[str]:
    """All comment bodies on the card, oldest→newest. Used by the model-fallback
    selector (DRE-1354) to read which model each prior attempt used / died on."""
    data = gql(
        """query($id: String!) { issue(id: $id) {
             comments(last: 50) { nodes { body } } } }""",
        {"id": identifier},
    )
    return [c.get("body") or "" for c in data["issue"]["comments"]["nodes"]]


def comment_records(identifier: str) -> list[dict]:
    """Every comment on the card WITH who wrote it, oldest→newest.

    `[{"body": str, "authored_by_pipeline": bool}]`. The one authorship fact
    anything downstream needs: this pipeline's writes all go through a single
    `LINEAR_API_KEY` that resolves to one Linear user (README — "the relay,
    reconcile, the planner and every agent share one LINEAR_API_KEY and resolve
    to the operator's own Linear user"), so "the pipeline wrote this" is
    exactly "the key's own `viewer` wrote this". A comment from anyone else on
    the card — a teammate, a guest with comment access — resolves to a
    different user, and an integration's comment has no `user` at all, which is
    the same rule README already states for break-glass labels: "a marker
    applied by a bot actor (an integration we do not own) is not honored."

    Why it exists (DRE-2721 review): `plan_critic.py` counts a plan's review
    rounds out of markers in this thread, and `comment_bodies` selected bodies
    and nothing else — so a marker nobody could attribute was a credential
    anyone could mint. Two stray comments carrying the marker line overrode a
    real critic rejection and promoted an epic's children to build; one
    carrying the cycle boundary refunded a budget that had been spent. Neither
    needs a hostile actor: `standards/plan-critic.md`'s worked example is a
    literal boundary line.

    An unknown viewer vouches for nobody. That is the safe direction: the round
    history reads as absent rather than as whatever a stranger wrote.
    """
    data = gql(
        """query($id: String!) { viewer { id } issue(id: $id) {
             comments(last: 50) { nodes { body user { id } } } } }""",
        {"id": identifier},
    )
    me = (data.get("viewer") or {}).get("id")
    rows = []
    for c in data["issue"]["comments"]["nodes"]:
        author = (c.get("user") or {}).get("id")
        rows.append({
            "body": c.get("body") or "",
            "authored_by_pipeline": bool(me) and author == me,
        })
    return rows


def cmd_dump_comments(identifier: str, *flags: str) -> None:
    """`dump-comments <DRE-N> [--with-authors]`.

    Bare: the card's comment bodies as a JSON array (oldest→newest) so the
    workflow can feed them to model_fallback.py without a second API client.

    `--with-authors`: the same thread as `comment_records` rows instead, for
    callers that read a machine record out of it and must know who wrote it.
    """
    if "--with-authors" in flags:
        print(json.dumps(comment_records(identifier)))
        return
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


def agent_label_refusal(label_name: str) -> str | None:
    """Why the pipeline must never write this label itself, or None.

    Exactly one label qualifies: `break-glass` (DRE-2737). It is an OPERATOR
    action — an agent that could bypass the intake gate would eventually
    bypass it for a reason that seemed good at the time, which is the entire
    failure class the intake wave addresses. Actor identity cannot enforce
    that (the whole fleet shares one LINEAR_API_KEY and resolves to the
    operator's own user), so the enforcement lives at the WRITE SEAM instead:
    the label-writing paths below refuse it. The pipeline's own
    `break-glass:used` receipt is a different label and stays writable — the
    gate has to be able to record a bypass.
    """
    import break_glass  # local: keeps the label constants in one module

    if (label_name or "").strip().lower() == break_glass.MARKER:
        return (
            f"'{break_glass.MARKER}' is an operator action and no agent may "
            "apply it — the marker must be applied by hand, in Linear, by a "
            "person (DRE-2737)"
        )
    return None


def child_labels_from(parent_labels: list[str], extra_labels: list[str]) -> list[str]:
    """The full label set for a planner-created child: what it inherits from
    its parent epic plus any explicit --label, minus anything no agent may
    apply. One operator action must not open the gate for a whole epic's worth
    of cards nobody looked at, so the marker is dropped here (loudly) rather
    than inherited or passed through."""
    out: list[str] = []
    for label in parent_inherited_labels(parent_labels) + list(extra_labels or []):
        refusal = agent_label_refusal(label)
        if refusal is not None:
            print(f"  ! dropping label {label!r} from the child: {refusal}", file=sys.stderr)
            continue
        out.append(label)
    return out


def add_label(identifier: str, label_name: str) -> None:
    """Attach `label_name` to a card, creating the team label if it doesn't
    exist yet. Idempotent: a no-op if the card already carries it.

    Used by the dead/hung-run hold (DRE-1403): stamping 'needs-human' lets the
    reconcile sweep and the promotion gate recognise a card a human must look
    at and leave it untouched until the label is removed.

    Refuses the one label no agent may apply (agent_label_refusal) BEFORE any
    API call — this function is the fleet's single label-writing path, which
    is what makes the refusal an enforcement rather than a convention.
    """
    refusal = agent_label_refusal(label_name)
    if refusal is not None:
        raise LinearError(f"refusing to label {identifier}: {refusal}")
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


def cmd_unpark(identifier: str, note: str = "") -> None:
    """Release a held card back into the pipeline WITH a fresh death budget.

    Three writes, in this order, and the order is the point:

      1. remove the `needs-human` hold label (idempotent),
      2. post the `dead-run-budget-reset` marker,
      3. move the card to Todo.

    The marker MUST land before the Todo move: the Todo transition is what
    re-dispatches the card, and the run it starts reads the death count. Post
    it after, and the fresh run races the reset and can still read the old,
    exhausted history.

    Step 3 is a plain transition, never `--park` — `--park` exists only to opt a
    deliberate Backlog HOLD out of the DRE-1885 building-card reroute, which is
    the exact opposite of what this does.

    Idempotent enough to re-run: a card without the label just skips step 1, and
    a second marker simply becomes the new reset point.
    """
    remove_label(identifier, _HOLD_LABEL)
    cmd_comment(identifier, dead_run.reset_comment(note))
    cmd_state(identifier, "Todo")
    print(f"{identifier} un-parked — death budget reset, back in Todo")


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


def cmd_child_descriptions(identifier: str) -> None:
    """Print every child card's description, one after another.

    The plan run reads these to decide whether an epic is UI work (DRE-2720):
    a child carrying a `**Design:**` ref is EVIDENCE the epic builds screens,
    where the plan's own prose is only a claim. Same degrade-quietly contract
    as `description` — a childless epic or a body-less child prints nothing,
    so the caller reads "no design refs" rather than the step erroring out.
    """
    data = gql(
        """query($id: String!) {
             issue(id: $id) { children { nodes { description } } }
           }""",
        {"id": identifier},
    )
    issue = data.get("issue") or {}
    nodes = ((issue.get("children") or {}).get("nodes")) or []
    for node in nodes:
        body = (node or {}).get("description") or ""
        if body:
            sys.stdout.write(body.rstrip("\n") + "\n")


def cmd_children_json(identifier: str) -> None:
    """Every child card as `{"identifier", "body"}` records, as a JSON array.

    `child-descriptions` concatenates the bodies, which is right for the
    "is this UI work?" question and useless for anything that has to say WHICH
    card is wrong. The plan critics (DRE-2721) report per card — "DRE-9001
    carries no acceptance criteria" — so they need the identity beside the
    body, and `plan_critic.py mechanical` reads exactly this shape on stdin.
    """
    data = gql(
        """query($id: String!) {
             issue(id: $id) { children { nodes { identifier description } } }
           }""",
        {"id": identifier},
    )
    issue = data.get("issue") or {}
    nodes = ((issue.get("children") or {}).get("nodes")) or []
    print(json.dumps([
        {"identifier": (n or {}).get("identifier"), "body": (n or {}).get("description") or ""}
        for n in nodes
    ]))


def cmd_epics_in_flight() -> None:
    """Every epic in flight, as `{"identifier", "title", "state"}` records.

    The post-approval critic's cross-epic sight (DRE-2721 D3). An epic is a
    card WITH CHILDREN — Linear-native parent/child, never a label
    (standards/card-quality.md) — so the child count is fetched and filtered
    here rather than guessed from a title convention.

    The states are `plan_critic.IN_FLIGHT_EPIC_STATES`, imported rather than
    restated: the critic's charter tells the CEO exactly which lanes it looked
    in, and a second copy of that tuple here would let the sentence and the
    query drift apart.
    """
    import plan_critic

    nodes = gql_paged(
        """query($states: [String!]!, $after: String) {
             issues(first: 100, after: $after, filter: {
               team: {key: {eq: "DRE"}},
               state: {name: {in: $states}}
             }) { nodes {
               identifier title state { name } children { nodes { id } }
             } pageInfo { hasNextPage endCursor } } }""",
        {"states": list(plan_critic.IN_FLIGHT_EPIC_STATES)},
    )
    print(json.dumps([
        {
            "identifier": n.get("identifier"),
            "title": n.get("title"),
            "state": (n.get("state") or {}).get("name"),
        }
        for n in nodes
        if ((n.get("children") or {}).get("nodes"))
    ]))


if __name__ == "__main__":
    cmd, *args = sys.argv[1:]
    try:
        {
            "state": cmd_state,
            "advance": cmd_advance,
            "comment": cmd_comment,
            "actor": cmd_actor,
            "card-done": cmd_card_done,
            "set-description": set_description,
            "subissue": cmd_subissue,
            "oneoff": cmd_oneoff,
            "create": cmd_create,
            "find-open": cmd_find_open,
            "children": cmd_children,
            "count-comments": cmd_count_comments,
            "unpark": cmd_unpark,
            "dump-comments": cmd_dump_comments,
            "add-label": add_label,
            "remove-label": remove_label,
            "description": cmd_description,
            "child-descriptions": cmd_child_descriptions,
            "children-json": cmd_children_json,
            "epics-in-flight": cmd_epics_in_flight,
        }[cmd](*args)
    except LinearError as e:
        # The ONLY process abort: explicit, at top level, CLI-only. Library
        # importers (reconcile above all) get a catchable exception instead.
        sys.exit(str(e))
