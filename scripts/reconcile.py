#!/usr/bin/env python3
"""Reconcile sweep: Linear card states vs pipeline reality (stdlib + gh CLI).

For every atlas card in an active state, verify reality matches and nudge the
pipeline when it doesn't. Self-limiting: every nudge comments on the card,
which bumps updatedAt past the staleness threshold for the next sweep.

Checks (card must carry **Repo:** atlas, any owner-prefix form):
  Todo        stale >15m, no open PR, no fresh dispatch -> re-fire dispatch
  In Progress stale >3h: PR open -> advance In QA + trigger qa-review;
              no PR -> back to Todo (relay re-dispatches on the transition)
  In QA       stale >2h: PR merged -> Done; verdict bound to the current
              head (DRE-1990) -> merge-gate; verdict missing OR stale/
              unbound -> re-trigger qa-review; no PR -> back to Todo,
              capped by the shared dead-run cap (DRE-2034)
  In Review   stale >1h: PR merged -> Done; else re-trigger merge-gate

Stranded-card watchdog (DRE-1993): every card/epic in Planning / Todo /
In Progress whose repo has no route in the routing snapshot, or (this repo's
cards) with no run receipt after 30 minutes, gets ONE plain-English comment
naming the reason plus the needs-human label — the board must never say work
is happening while nothing runs (DRE-1978 sat in Planning 7 days unseen).

Also runs the dependency gate: Backlog children whose parent epic is ACTIVATED
(= plan approved) are auto-promoted to Todo once every blocker is Done — blockers
read from Linear's native "blocks" relations AND from "Blocked by: DRE-N" /
"serialize after DRE-N" lines in the description. A WIP cap (MAX_WIP, default 4
active cards) throttles promotion so the pipeline never floods.

An epic counts as ACTIVATED in EITHER Todo OR In Progress (DRE-1893). The CEO's
activation action is moving an approved epic to **Todo** (lifecycle Backlog →
Planning → Todo); In Progress is a downstream/system progression. Todo is purely
ADDITIVE to the pre-existing In Progress trigger, so both activate identically
and nothing that worked before changes. MAX_WIP and the blocker checks are
unchanged — only the set of parent states that count as "active" widened.

EPIC-LEVEL dependencies (DRE-1772): the gate also honours dependencies between
EPICS. Before promoting an epic's children, it checks that EPIC's own
"blocked-by" relations (read the same way as a card's); if any blocker epic is
not Done, none of that epic's children promote this sweep — regardless of the
epic's own state. And when a blocker epic reaches Done, every epic blocked-by
it whose blockers are now ALL Done is auto-advanced from Backlog to Triage
(which re-triggers the planner) — never to In Progress, so the Plan Review
human-approval gate is preserved. Both behaviors fail SAFE on unreadable
relation data (don't promote / don't advance on uncertainty).

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
import validate_card  # noqa: E402 — VALID_SLUGS, the canonical routing snapshot

REPO = os.environ["REPO"]
REPO_SLUG = os.environ.get("REPO_SLUG", "atlas")

# In Progress dropped 180→60: silent agent deaths now requeue instantly from
# the run itself; this timer is only the backstop for lost run outcomes.
STALE_MINUTES = {"Todo": 15, "In Progress": 60, "In QA": 120, "In Review": 60}
MAX_WIP = int(os.environ.get("MAX_WIP", "4"))

# Parent-epic states that count as ACTIVATED for the dependency gate (DRE-1893).
# The CEO activates an approved epic by moving it to **Todo** (lifecycle Backlog
# → Planning → Todo); In Progress is a later/system progression that historically
# was the ONLY activation trigger. Todo is ADDITIVE: an epic in either state
# promotes its unblocked Backlog children. Anything else (Backlog, Planning,
# Plan Review, Done, …) is not active and its children stay parked.
EPIC_ACTIVE_STATES = ("Todo", "In Progress")

# Human-hold (DRE-1403). A card whose agent keeps dying with no PR — whether it
# crashes (counted by agent-task) or HANGS/times out (seen only here) — is
# requeued at most REQUEUE_CAP times. After that it is parked in Backlog with
# HOLD_LABEL so neither the relay nor this sweep re-dispatch it into the same
# wall. Both paths count the shared DEAD_TAG so the cap is unified. A human
# splits/fixes the card and removes the label to retry.
HOLD_LABEL = "needs-human"
DEAD_TAG = "dead-run-requeue"
REQUEUE_CAP = int(os.environ.get("DEAD_RUN_CAP", "2"))

# Engineer-blocker guard (DRE-1585). When the engineer agent hits a genuine,
# deterministic blocker it posts this exact marker (see agent-task.yml) and
# parks the card back in Backlog ON PURPOSE — re-dispatching would walk the
# next agent straight into the same wall. The dependency gate, however, only
# looks at FORMAL blockers (blocks relations + "Blocked by:" lines); when those
# happen to be Done and the epic is active it re-promoted the card anyway.
# Real incident: DRE-1572 looped Backlog→Todo→In Progress→Backlog FIVE times,
# burning five engineer runs. So before promoting we also check for an
# *unresolved* agent-blocker (latest blocker marker newer than any human reply).
BLOCKER_MARKER = "🛑 Agent blocked"

# Comments the pipeline itself authors all start with one of these emoji
# markers (engineer/QA/reconcile receipts). A blocker is "resolved" only when a
# HUMAN (CEO/operator) weighs in afterward — i.e. a comment that is NOT one of
# our own machine markers. The gate's own "🧹 Auto-promoted" receipt is a
# machine marker, so it can never clear the blocker and re-arm the loop.
_AGENT_COMMENT_PREFIXES = ("🤖", "🛑", "🧹", "🪦", "🚨", "🏁")

# Live-run liveness gate (DRE-2032). agent-task's "Card → In Progress" step
# posts this heartbeat with the run's URL, so the card itself maps to its
# Actions run. ⏳ (phase receipts) and 🧠 (model-attempt) are the only
# PROOF-OF-LIFE prefixes — the sweep's own 🪦/🧹/🚨 receipts must never count,
# or every requeue comment would suppress the next requeue forever.
RUN_MARKER = "🧠 model-attempt:"
_RUN_ID = re.compile(r"/actions/runs/(\d+)\b")
_LIFE_PREFIXES = ("⏳", "🧠")

# Stranded-card watchdog (DRE-1993). A card/epic parked in an ACTIVE lane
# with NO evidence any matching run ever started is invisible to every other
# backstop: the board says work is happening while nothing runs. Live
# incident: DRE-1978 sat in Planning for SEVEN DAYS with zero planner runs
# (its repo label routed nowhere at the time) — the CEO found it by asking.
# Budget blocks, quota exhaustion, relay outages, and any future off-map repo
# all strand cards the same silent way. flag_stranded() below alarms on both
# classes, ONCE per card (the WATCHDOG_TAG comment is the idempotency
# marker), and adds HOLD_LABEL so the sweep stops re-dispatching into the
# same wall. Planning is a watchdog-only lane: the nudge loop / WIP cap
# below never see it (no STALE_MINUTES entry, no defined nudge).
WATCHDOG_LANES = ("Planning", "Todo", "In Progress")
WATCHDOG_MINUTES = int(os.environ.get("WATCHDOG_MINUTES", "30"))
WATCHDOG_TAG = "stranded-watchdog"

# The sweep's own Todo-redispatch receipt (posted in main() below). It bumps
# updatedAt every ~15-minute cycle, so a silently-failing dispatch loop never
# LOOKS WATCHDOG_MINUTES stale — a prior receipt with still no proof-of-life
# is the same evidence as the elapsed time: dispatch fired, nothing ran.
_TODO_REDISPATCH_NOTE = "card sat in Todo with no run — re-dispatched"


def held(card: dict) -> bool:
    """True if the card carries HOLD_LABEL — the sweep must not requeue, nudge,
    or auto-promote it until a human removes the label."""
    return any(
        lbl["name"].lower() == HOLD_LABEL
        for lbl in (card.get("labels") or {}).get("nodes", [])
    )

# "Blocked by: DRE-1204 + DRE-1205", "serialize after DRE-1226", "depends on
# DRE-N" — blockers are every DRE-N on a line that contains one of these
# phrases. Line-scoped on purpose: parent-epic links appear all over card
# bodies and must not count as blockers.
_BLOCKER_LINE = re.compile(r"(?:blocked by|serialize after|depends on)", re.IGNORECASE)
_CARD_REF = re.compile(r"DRE-\d+")


def gh(*args: str) -> str:
    # B603/B607: args are program-constructed (no user input), shell=False,
    # and "gh" resolves via PATH on the runner by design.
    # SILENT by design — safe only where an empty answer means "do nothing"
    # (the PR-level backstops) or the caller has its own fallback
    # (agent_run_alive's receipt path). The card PR lookup and every write
    # use the LOUD helpers below instead (DRE-1254, DRE-2034).
    return subprocess.run(  # nosec B603 B607
        ["gh", *args], capture_output=True, text=True, check=False
    ).stdout.strip()


class ReconcileWriteError(RuntimeError):
    """A write-path gh call failed — surface it; never pretend success."""


class ReconcileReadError(RuntimeError):
    """A read-path gh call failed — surface it; never act on the fabricated
    empty result (a 403 is NOT "this card has no PR")."""


#: Write failures collected during the sweep; non-empty -> exit 1 so the
#: Actions run goes red and medic picks it up.
_write_failures: list[str] = []

#: Read failures (unreadable PR lookups) collected the same way — the sweep
#: skips the unreadable card, sweeps the rest, and still exits 1 (DRE-2034).
_read_failures: list[str] = []


def gh_read(*args: str) -> str:
    """Run a read-path gh command LOUDLY: raise ReconcileReadError on rc!=0.

    Origin (2026-06-28, twice live / DRE-2034): the silent gh() helper
    discarded exit code and stderr, so a 403/rate-limit on the PR lookup
    parsed as "[]" — indistinguishable from "this card has no PR" — and the
    sweep requeued healthy cards off that fabricated emptiness.
    """
    p = subprocess.run(  # nosec B603 B607 — fixed-arg gh call, shell=False
        ["gh", *args], capture_output=True, text=True, check=False
    )
    if p.returncode != 0:
        raise ReconcileReadError(
            f"gh {' '.join(args)} failed rc={p.returncode}: {p.stderr.strip()[:400]}"
        )
    return p.stdout.strip()


def gh_dispatch(*args: str) -> None:
    """Run a write-path gh command LOUDLY: raise ReconcileWriteError on rc!=0.

    Origin (2026-06-12, PR #48 / DRE-1254): every `gh workflow run` in this
    sweep executed under the minted App token, which lacks Actions:write —
    GitHub answered "HTTP 403: Resource not accessible by integration" and
    the silent gh() helper discarded it. The sweep printed "dispatching fix
    agent" (and posted "re-triggered" Linear comments) while nothing ran,
    so conflicted PRs sat stuck through sweep after green sweep.

    Two-part fix: (1) failures raise instead of vanishing; (2) dispatch runs
    under GH_DISPATCH_TOKEN when set — the calling stub grants actions:write
    to the workflow's github.token, which the reusable workflow passes
    through (see reconcile.yml Sweep env).
    """
    env = None
    dispatch_token = os.environ.get("GH_DISPATCH_TOKEN")
    if dispatch_token:
        env = {**os.environ, "GH_TOKEN": dispatch_token}
    p = subprocess.run(  # nosec B603 B607 — fixed-arg gh call, shell=False
        ["gh", *args], capture_output=True, text=True, check=False, env=env
    )
    if p.returncode != 0:
        raise ReconcileWriteError(
            f"gh {' '.join(args)} failed rc={p.returncode}: {p.stderr.strip()[:400]}"
        )


def _nudge(workflow: str, pr_number: int) -> bool:
    """Dispatch a workflow for a PR; True only when it actually went through.

    Callers MUST gate their "re-triggered" Linear comments on this — a
    comment claiming a re-trigger that 403'd is how DRE-1254 looked
    "self-healing" while fully stalled.
    """
    try:
        gh_dispatch("workflow", "run", workflow, "--repo", REPO,
                    "-f", f"pr_number={pr_number}")
        return True
    except ReconcileWriteError as e:
        _write_failures.append(str(e))
        print(f"ERROR: {e}", file=sys.stderr)
        return False


def card_repo_slug(description: str) -> str | None:
    stripped = re.sub(r"```.*?```", "", description or "", flags=re.DOTALL)
    m = re.search(r"^\*\*Repo:\*\*\s*([a-z0-9._/-]+)\s*$", stripped, re.MULTILINE | re.IGNORECASE)
    return m.group(1).lower().rsplit("/", 1)[-1] if m else None


def card_repo(card: dict) -> str | None:
    """A card's repo slug, LABEL-first (DRE-1879).

    The `repo:<slug>` label is the canonical repo signal — the `**Repo:**`
    description stamp is a deprecated relic that cards created the modern way no
    longer carry (DRE-1699/DRE-1697). The event-driven promotion gate matched ONLY
    the stamp, so a label-only card (e.g. DeltaSolv's DRE-1811, `repo:deltasolv`,
    no stamp) returned None and was silently skipped — its blocker went Done on a
    merge but it never promoted, stranding the chain until the operator did it by
    hand. Read the label first; fall back to the legacy stamp for old cards.
    """
    for lbl in (card.get("labels") or {}).get("nodes", []):
        name = (lbl.get("name") or "").lower()
        if name.startswith("repo:"):
            return name[len("repo:"):].rsplit("/", 1)[-1] or None
    return card_repo_slug(card.get("description") or "")


def age_minutes(iso: str) -> float:
    then = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return (datetime.now(UTC) - then).total_seconds() / 60


# The nudge loop's lanes — the pre-DRE-1993 sweep, byte-identical. The
# watchdog passes WATCHDOG_LANES instead to also see Planning.
SWEEP_STATES = ("Todo", "In Progress", "In QA", "In Review")


def active_cards(states: tuple[str, ...] = SWEEP_STATES) -> list[dict]:
    data = linear_ops.gql(
        """query($states: [String!]!) { issues(first: 100, filter: {
             team: {key: {eq: "DRE"}},
             state: {name: {in: $states}}
           }) { nodes {
             id identifier title description updatedAt
             state { name } labels { nodes { name } }
           } } }""",
        {"states": list(states)},
    )
    return data["issues"]["nodes"]


def flag_stranded() -> set[str]:
    """DRE-1993 watchdog: flag active-lane cards with no evidence of work.

    Two strand classes, checked over WATCHDOG_LANES on every full sweep:
      (a) NO ROUTE — the card's repo slug is not in the routing snapshot
          (validate_card.VALID_SLUGS, mirroring the relay's map), so no
          dispatch can EVER start a run. Flagged within one sweep, however
          fresh the card. Every repo's sweep checks this (an off-map card
          belongs to no sweep's `mine`); the once-ever gate makes whichever
          sweep gets there first the only one that speaks.
      (b) NO RUN — a dispatchable card of THIS sweep's repo with zero run
          receipts (the DRE-2032 🧠/⏳ proof-of-life comments; agent-task
          and plan both post them at run start) after WATCHDOG_MINUTES —
          or after a prior Todo-redispatch receipt, which resets updatedAt
          every cycle and would otherwise hide the strand forever. A card
          with ANY receipt started a run once (live or dead) and is never
          flagged: started-then-died is the dead-run requeue's case.
          Epics past Planning are containers — no run ever targets them,
          so their receipt-less state is normal, not a strand.

    Flagging = one plain-English comment (🚨 + WATCHDOG_TAG) naming the
    reason, plus HOLD_LABEL — no state move, no cancel. A false positive
    (e.g. a run queued 30+ minutes behind a runner-capacity crunch, which
    posts no receipt until it starts) costs a label a human removes; the
    run itself is untouched. Fail loud beats fail silent (DRE-1979).

    Returns the identifiers flagged THIS sweep so the caller's nudge loop
    can skip them — their fetched labels predate the hold label.
    """
    flagged: set[str] = set()
    for card in active_cards(WATCHDOG_LANES):
        ident, state = card["identifier"], card["state"]["name"]
        if held(card):
            continue  # already in a human's queue — never spam
        slug = card_repo(card)
        routable = slug is not None and slug in validate_card.VALID_SLUGS
        if routable and slug != REPO_SLUG:
            continue  # that repo's own sweep runs the no-run check for its cards
        labels = [lbl["name"].lower() for lbl in card["labels"]["nodes"]]
        if routable and state != "Planning" and "agent:planner" in labels:
            continue  # an epic past Planning carries no run — normal, not stranded
        bodies = linear_ops.comment_bodies(ident)
        if any(WATCHDOG_TAG in b for b in bodies):
            continue  # flagged once already — idempotent forever
        if routable:
            if any(b.lstrip().startswith(_LIFE_PREFIXES) for b in bodies):
                continue  # a run DID start — the dead-run machinery owns it now
            redispatched = any(_TODO_REDISPATCH_NOTE in b for b in bodies)
            if not redispatched and age_minutes(card["updatedAt"]) < WATCHDOG_MINUTES:
                continue  # young — give the dispatch time to start a run
            reason = (
                f"no agent run has started after {WATCHDOG_MINUTES}+ minutes in "
                f"{state} (no run receipts on the card) — check the GitHub Actions "
                "budget, the LLM quota, and the relay. If a run is merely queued, "
                f"remove the '{HOLD_LABEL}' label; otherwise this card needs a "
                "human to unblock the pipeline."
            )
        else:
            reason = (
                f"repo '{slug or 'none — no repo label'}' isn't on the dispatch "
                "rail — no agent can ever pick this card up, so it must be "
                "hand-built (or the repo onboarded to the routing map first). "
                f"Labeled '{HOLD_LABEL}' for a human."
            )
        linear_ops.cmd_comment(ident, f"🚨 {WATCHDOG_TAG}: {reason}")
        linear_ops.add_label(ident, HOLD_LABEL)
        flagged.add(ident)
        print(f"watchdog: {ident} in {state} flagged ({'no-run' if routable else 'no-route'})")
    return flagged


def pr_for(identifier: str) -> dict | None:
    """The card's PR, looked up by HEAD BRANCH (agent/DRE-N-*), reads LOUD.

    Both reads raise ReconcileReadError on failure — acting on a 403 parsed
    as "no PR" is what falsely requeued healthy cards on 2026-06-28.

    Search by head branch first (`head:agent/DRE-N` matches branch-name
    tokens): an old card's PR must never fall off a list window and read as
    missing. The newest-100 scan survives ONLY as a fallback for search-index
    lag on a just-opened PR. Either way the \\b-anchored confirm keeps
    near-miss identifiers out (DRE-1034 vs DRE-10345), and among matches the
    highest PR number wins — the newest attempt; an older merged PR must not
    shadow a newer open one and flip the card to Done.
    """
    fields = "number,headRefName,state,comments,headRefOid"

    def newest_match(out: str) -> dict | None:
        matches = [
            pr for pr in json.loads(out or "[]")
            if re.search(rf"\b{identifier}\b", pr["headRefName"])
        ]
        return max(matches, key=lambda pr: pr["number"]) if matches else None

    found = newest_match(gh_read(
        "pr", "list", "--repo", REPO, "--state", "all", "--limit", "30",
        "--search", f"head:agent/{identifier}", "--json", fields,
    ))
    if found:
        return found
    return newest_match(gh_read(
        "pr", "list", "--repo", REPO, "--state", "all", "--limit", "100",
        "--json", fields,
    ))


def agent_run_alive(identifier: str) -> bool:
    """True if the card's agent-task run is ACTUALLY still running — in which
    case the card is NOT dead, regardless of elapsed time, and the sweep must
    leave it alone (DRE-2032).

    Origin (2026-07-10 20:07–22:22Z, DRE-2023 on agent-bureau): three builds
    each ran ~45 minutes with real progress receipts on the card; the
    In-Progress-no-PR branch read the staleness as death, requeued to Todo,
    and the fresh dispatch CANCELLED the still-running build via the per-card
    concurrency group (run 29125285930 concluded cancelled at "Gate on agent
    result"). The dead-run cap then parked the card needs-human — the watchdog
    caused all three deaths it was counting.

    Detection, authoritative first:
      1. The newest 🧠 model-attempt heartbeat carries the run's URL
         (agent-task.yml posts it at Card → In Progress). Ask GitHub for THAT
         run's status: queued/in_progress/etc. → alive; completed → dead (a
         concluded run with no PR is the real requeue case, and a fresh
         receipt must not shadow it).
      2. When no run id is readable (legacy heartbeat, comment never posted)
         or the status read fails (API blip), fall back to receipts: a ⏳/🧠
         comment younger than the In Progress staleness window is proof of
         life without a GitHub call. The sweep's own 🪦/🧹/🚨 receipts never
         count. With neither signal the card is dead, exactly as before.
    """
    nodes = linear_ops.gql(
        """query($id: String!) { issue(id: $id) {
             comments(last: 50) { nodes { body createdAt } } } }""",
        {"id": identifier},
    )["issue"]["comments"]["nodes"]
    for node in reversed(nodes):  # newest → oldest: the CURRENT attempt's run
        body = (node.get("body") or "").lstrip()
        if not body.startswith(RUN_MARKER):
            continue
        m = _RUN_ID.search(body)
        if not m:
            break  # legacy heartbeat without a run URL — receipts decide
        status = gh("api", f"repos/{REPO}/actions/runs/{m.group(1)}",
                    "--jq", ".status")
        if status == "completed":
            return False  # concluded with no PR: the real dead-run case
        if status:
            return True  # queued / in_progress / waiting / … — a live run
        break  # unreadable status — receipts decide
    for node in reversed(nodes):
        body = (node.get("body") or "").lstrip()
        created = node.get("createdAt") or ""
        if (
            body.startswith(_LIFE_PREFIXES)
            and created
            and age_minutes(created) < STALE_MINUTES["In Progress"]
        ):
            return True
    return False


QA_BOT_LOGIN = "agent-bureau-qa-bot"


def is_qa_bot_comment(comment: dict) -> bool:
    """True iff the PR comment was AUTHORED by the qa-bot App (DRE-1998).

    The verdict reads below previously trusted any comment whose BODY
    mentioned "QA Critic" — a forged comment (worker bot, human) could
    suppress the In QA re-review nudge (card stalls in In QA) or read as
    APPROVE to the approved-but-red sweep (spurious agent-fix dispatches).
    Merge was never at risk — merge-gate enforces authorship itself
    (DRE-1987) — this closes the stall/waste vector.

    Login shape: reconcile's comments come from `gh pr list --json
    comments` (GraphQL-backed), where a GitHub App's author.login carries
    NO "[bot]" suffix — "agent-bureau-qa-bot", unlike the REST user.login
    "agent-bureau-qa-bot[bot]" merge-gate reads. The suffix is stripped
    before comparing so either payload shape matches; a literal
    "agent-bureau-qa-bot[bot]" compare would match NOTHING here and wedge
    every In QA card in review churn.

    Why a literal login instead of merge-gate's app-slug derivation:
    merge-gate learns the slug from the qa-bot token it mints in order to
    merge; reconcile mints only the WORKER bot token (reconcile.yml) and
    never acts as the qa-bot, so deriving the slug would mean minting a
    qa-bot token solely to learn its own name. If the App is ever renamed
    this fails CLOSED and visibly: reconcile sees no verdict and re-nudges
    qa-review (fresh-review churn on the card), never a merge.
    """
    login = (comment.get("author") or {}).get("login") or ""
    return login.removesuffix("[bot]") == QA_BOT_LOGIN


def critic_comment_bodies(pr: dict) -> list[str]:
    """Bodies of the PR's QA Critic comments, oldest→newest — counting ONLY
    comments authored by the qa-bot App. Forged critic comments are
    invisible (not merely non-approving), so a forged trailing comment can
    never shadow or mask a genuine verdict (DRE-1998)."""
    return [
        c.get("body") or ""
        for c in pr.get("comments", [])
        if is_qa_bot_comment(c) and "QA Critic" in (c.get("body") or "")
    ]


def has_verdict(pr: dict) -> bool:
    """True iff the latest qa-bot-authored QA Critic comment is a verdict
    BOUND to the PR's CURRENT head commit — the verdict line ends
    `@<full-sha>` (DRE-1990); forged/non-qa-bot comments are invisible
    (DRE-1998).

    A stale binding (verdict for an older commit) or a legacy/neutral
    comment with no SHA is NOT a verdict: merge-gate ignores those
    fail-closed, so nudging merge-gate would spin forever. Returning False
    routes the In QA re-nudge to qa-review instead, producing a fresh,
    bound verdict — this is also the automatic one-time re-review path for
    APPROVEs posted before DRE-1990 shipped.
    """
    bodies = critic_comment_bodies(pr)
    if not bodies:
        return False
    first_line = bodies[-1].splitlines()[0] if bodies[-1] else ""
    m = re.search(r"@([0-9a-f]{40})", first_line)
    return bool(m) and m.group(1) == (pr.get("headRefOid") or "")


def redispatch(card: dict) -> bool:
    """Re-fire the card's repository_dispatch; True ONLY on confirmed success.

    Callers MUST gate their "re-dispatched" receipt on the return value — the
    old silent gh() meant a 403'd dispatch still told the CEO the card was
    restarted (the DRE-1254 false-receipt class, DRE-2034). A failure is
    recorded so the sweep run goes red for medic.

    Runs under the default App token on purpose: the dispatches API needs
    contents:write, which the App token holds — GH_DISPATCH_TOKEN (the
    stub's github.token) is contents:read and exists only for
    `gh workflow run` (actions:write), so gh_dispatch would 403 here.
    """
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
    try:
        p = subprocess.run(  # nosec B603 B607 — fixed-arg gh call, shell=False
            ["gh", "api", f"repos/{REPO}/dispatches", "--input", path],
            capture_output=True, text=True, check=False,
        )
    finally:
        os.unlink(path)
    if p.returncode != 0:
        err = (
            f"redispatch {card['identifier']}: gh api repos/{REPO}/dispatches "
            f"failed rc={p.returncode}: {p.stderr.strip()[:400]}"
        )
        _write_failures.append(err)
        print(f"ERROR: {err}", file=sys.stderr)
        return False
    return True


def backlog_children() -> list[dict]:
    data = linear_ops.gql(
        """query { issues(first: 100, filter: {
             team: {key: {eq: "DRE"}},
             state: {name: {eq: "Backlog"}}
           }) { nodes {
             id identifier title description
             parent { identifier state { name } }
             labels { nodes { name } }
             comments(last: 50) { nodes { body } }
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


def _fetch_epic_relations(epic_identifier: str) -> dict | None:
    """Read an epic's identifier, description, and `blocked-by` relations.

    Returns the same shape `blockers_of` consumes (identifier, description,
    inverseRelations) so the epic-level gate can reuse it verbatim. Returns
    None on any read failure so callers can fail SAFE (DRE-1772).
    """
    try:
        data = linear_ops.gql(
            """query($id: String!) { issue(id: $id) {
                 identifier description
                 inverseRelations(first: 20) { nodes {
                   type issue { identifier state { name } }
                 } }
               } }""",
            {"id": epic_identifier},
        )
    except Exception as e:  # noqa: BLE001 — any Linear/transport error -> fail safe
        print(f"epic-gate: could not read relations for {epic_identifier}: {e}")
        return None
    return (data or {}).get("issue")


def epic_blockers_unmet(epic_identifier: str) -> bool:
    """True if EPIC `epic_identifier` is itself blocked-by another epic/card
    that is not yet Done — in which case none of its children may promote this
    sweep (DRE-1772, epic-level gate).

    Reuses the exact card-level blocker detection (`blockers_of`: native
    `blocks` relations + "Blocked by:/serialize after/depends on" description
    lines), just applied to the epic. A blocker counts as MET only when its
    state is Done/Canceled/Duplicate. Fails SAFE: if the epic's relation data
    can't be read, returns True (treat as blocked, do not promote).
    """
    epic = _fetch_epic_relations(epic_identifier)
    if epic is None:
        return True  # ambiguous/unreadable -> fail safe (blocked)
    epic.setdefault("parent", None)
    epic.setdefault("identifier", epic_identifier)
    # Native `blocks` relations: `blockers_of` already filters these to
    # NON-terminal blockers (state not in Done/Canceled/Duplicate), reading the
    # state inline from the relation — so any relation-blocker it returns is, by
    # construction, unmet and needs no extra fetch.
    relation_blockers = {
        rel["issue"]["identifier"]
        for rel in epic["inverseRelations"]["nodes"]
        if rel["type"] == "blocks"
    }
    for blocker in blockers_of(epic):
        if blocker in relation_blockers:
            return True  # relation blocker, already known non-terminal
        # A description-line blocker ("Blocked by: DRE-N"): state unknown, fetch.
        if card_state(blocker) not in ("Done", "Canceled", "Duplicate"):
            return True
    return False


def advance_unblocked_epics(done_epic: str) -> None:
    """When epic `done_epic` reaches Done, pull the next epics in the chain into
    the pipeline (DRE-1772, auto-advance).

    For each epic that `done_epic` `blocks` (its forward `relations`): if ALL of
    that epic's own blocker epics are now Done AND it is still in Backlog, move
    it to **Triage** (which triggers the planner). NEVER to In Progress — the
    Plan Review approval gate stays human-owned. Idempotent and safe:
      * only acts on epics still in Backlog (never re-advances one already past
        it, never thrashes an operator-parked or already-running epic);
      * never revives a Canceled/Duplicate/Done dependent;
      * fails SAFE on unreadable relation data (advances nothing).
    """
    try:
        data = linear_ops.gql(
            """query($id: String!) { issue(id: $id) {
                 relations(first: 20) { nodes {
                   type issue { identifier }
                 } } } }""",
            {"id": done_epic},
        )
    except Exception as e:  # noqa: BLE001 — fail safe
        print(f"epic-advance: could not read forward relations for {done_epic}: {e}")
        return
    issue = (data or {}).get("issue")
    if not issue:
        return  # fail safe — nothing to advance
    dependents = {
        rel["issue"]["identifier"]
        for rel in (issue.get("relations") or {}).get("nodes", [])
        if rel["type"] == "blocks"
    }
    for dep in sorted(dependents):
        if card_state(dep) != "Backlog":
            continue  # idempotent: only ever advance a still-Backlog epic
        if epic_blockers_unmet(dep):
            continue  # another blocker epic isn't Done yet — hold
        linear_ops.cmd_advance(dep, "Triage", "Backlog")
        linear_ops.cmd_comment(
            dep,
            f"🧹 Auto-advanced Backlog → Triage: blocker epic {done_epic} is Done "
            "and all blocker epics are now complete. The planner will take it from "
            "here; a human still approves the plan (→ In Progress).",
        )


def has_unresolved_blocker(card: dict) -> bool:
    """True if the card's latest engineer-blocker marker has no human reply after
    it — i.e. the card was parked in Backlog on a genuine blocker and nobody has
    resolved it yet. Promoting such a card just re-dispatches the engineer into
    the identical wall (DRE-1585 / DRE-1572's five-run loop).

    Reads the card's `comments` (oldest→newest), which the dependency-gate query
    fetches inline so no extra per-card API call is needed. Detection walks them
    newest→oldest and stops at the first decisive comment — either the blocker
    marker or a HUMAN comment (any comment NOT prefixed with one of the
    pipeline's own machine markers). If that first decisive comment is the
    blocker marker, the blocker is still open; a later human comment (or a human
    moving/editing the card and commenting) flips it to resolved. A card with no
    `comments` key (e.g. a hand-built test fixture) is treated as unblocked.
    """
    nodes = (card.get("comments") or {}).get("nodes", [])
    for node in reversed(nodes):  # newest → oldest
        text = (node.get("body") or "").lstrip()
        if text.startswith(BLOCKER_MARKER):
            return True  # newest decisive comment is an open blocker
        if not text.startswith(_AGENT_COMMENT_PREFIXES):
            return False  # a human spoke after the blocker — treat as resolved
    return False  # no blocker marker on the card at all


def promote_ready(active_count: int) -> int:
    """Auto-promote Backlog children whose blockers are all Done."""
    budget = MAX_WIP - active_count
    if budget <= 0:
        print(f"promotion: WIP at cap ({active_count}/{MAX_WIP}) — none promoted")
        return 0
    promoted = 0
    # Cache the epic-level gate per parent epic: it is the same answer for every
    # child of that epic, so consult Linear once per epic per sweep (DRE-1772).
    epic_gate: dict[str, bool] = {}
    candidates = sorted(backlog_children(), key=lambda c: int(c["identifier"].split("-")[1]))
    for card in candidates:
        if promoted >= budget:
            break
        if card_repo(card) != REPO_SLUG:
            continue
        labels = [lbl["name"].lower() for lbl in card["labels"]["nodes"]]
        if "agent:planner" in labels:
            continue  # epics are promoted by humans, never by the sweep
        if HOLD_LABEL in labels:
            continue  # held for a human (DRE-1403) — never auto-promote
        parent = card.get("parent")
        if not parent or parent["state"]["name"] not in EPIC_ACTIVE_STATES:
            continue  # parent epic not approved/active (Todo or In Progress; DRE-1893)
        # Epic-level gate (DRE-1772): even an active (plan-approved) epic must
        # not start its children while the epic itself is blocked-by a
        # prerequisite epic that has not shipped. Composes with the card-level
        # gate, MAX_WIP, and the DRE-1585 agent-blocker guard below.
        epic_id = parent["identifier"]
        if epic_id not in epic_gate:
            epic_gate[epic_id] = epic_blockers_unmet(epic_id)
        if epic_gate[epic_id]:
            print(
                f"promotion: {card['identifier']}'s epic {epic_id} is blocked by "
                "an unfinished epic — skipping"
            )
            continue
        unmet = {
            b for b in blockers_of(card) if card_state(b) not in ("Done", "Canceled", "Duplicate")
        }
        if unmet:
            continue
        # Formal blockers are clear, but the engineer may have parked this card
        # on a *deterministic* blocker it flagged itself (DRE-1585). Re-promoting
        # would redispatch it straight back into the same wall — exactly the
        # five-run loop DRE-1572 hit. Skip until a human resolves it (a human
        # comment after the blocker marker, or the human clears it some other way
        # and the card leaves Backlog).
        if has_unresolved_blocker(card):
            print(f"promotion: {card['identifier']} has an unresolved agent-blocker — skipping")
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
            # This epic just shipped — pull the next epics in the dependency
            # chain into the pipeline (DRE-1772). Merge-time hook; the full
            # sweep is the backstop.
            advance_unblocked_epics(epic)


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
        gh_dispatch("workflow", "run", "agent-fix.yml", "--repo", REPO,
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
        # qa-bot-authored comments only (DRE-1998): a forged APPROVE must
        # not spawn agent-fix dispatches, and a forged trailing non-APPROVE
        # must not mask a genuine one.
        verdicts = critic_comment_bodies(pr)
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
        gh_dispatch("workflow", "run", "agent-fix.yml", "--repo", REPO,
                    "-f", f"pr_number={pr['number']}")
        return  # one dispatch per sweep; the busy-guard handles the rest


def repo_epics(active: list[dict]) -> set[str]:
    """Identifiers of THIS repo's active epics (agent:planner cards).

    Epics (agent:planner) are containers, not work: they carry no PR and sit
    In Progress for the life of their children — never nudged, never counted
    against the WIP cap. They DO close themselves when finished.
    """
    mine = [c for c in active if card_repo(c) == REPO_SLUG]
    return {
        c["identifier"]
        for c in mine
        if any(lbl["name"].lower() == "agent:planner" for lbl in c["labels"]["nodes"])
    }


def main(
    promote_only: bool = False, conflicts_only: bool = False, close_only: bool = False
) -> None:
    """Full sweep by default; promote_only runs JUST the dependency gate.

    promote_only exists because GitHub's cron is best-effort — the "*/15"
    schedule delivers sweeps 78-100 minutes apart in practice. Eligibility
    changes at two precise events, so those workflows invoke this directly:
      - plan.yml, the moment an epic activates (Todo or In Progress; the gate
        counts an epic as active in EITHER state — DRE-1893)
      - linear-sync.yml, the moment a merge flips a card to Done
    Promotion is pure Linear (the Backlog→Todo transition rides the Linear
    webhook → relay → repository_dispatch for the actual agent start), so
    the event hooks need only LINEAR_API_KEY. (Origin: DRE-1260 activated
    9s after a sweep checked and faced an ~80-minute wait, 2026-06-12.)

    close_only runs JUST the epic-close pass, for the SAME cron-drift reason:
    a merge that flips the last child to Done is the exact moment its parent
    epic becomes all-Done, yet epic-close otherwise runs only on the drifting
    full sweep — so an epic read "still working" for up to ~an hour after it
    shipped (DRE-1496 sat In Progress with 9/9 children Done). linear-sync
    invokes this on every merge. Pure Linear, like promote_only — needs only
    LINEAR_API_KEY. (Origin: DRE-1552.)

    conflicts_only runs JUST the DIRTY-PR backstop, for the same cron-drift
    reason: a merge to the default branch is the exact event that conflicts
    sibling PRs touching the same files, so linear-sync invokes this on
    every merge. Needs a dispatch-capable GH token, unlike promote_only.
    (Origin: PR #1348 / DRE-1277 sat conflicted ~1h waiting on the cron.)
    """
    if conflicts_only:
        try:
            unstick_conflicts()
        except ReconcileWriteError as e:
            sys.exit(f"reconcile --conflicts-only: {e}")
        return
    if close_only:
        epics = repo_epics(active_cards())
        close_finished_epics(epics)
        print(f"close-only: epic close evaluated ({len(epics)} active epic(s))")
        return
    nudges = 0
    flagged: set[str] = set()
    if not promote_only:
        # Backstops run independently: one failing must not silence the
        # others, but every write failure is recorded and fails the run.
        for backstop in (unstick_conflicts, retrigger_dead_heads, fix_approved_but_red):
            try:
                backstop()
            except ReconcileWriteError as e:
                _write_failures.append(str(e))
                print(f"ERROR: {backstop.__name__}: {e}", file=sys.stderr)
        # Stranded-card watchdog (DRE-1993) — BEFORE the nudge loop, so a
        # card flagged this very sweep is skipped below (its fetched labels
        # predate the hold label the watchdog just added).
        flagged = flag_stranded()
    mine = [c for c in active_cards() if card_repo(c) == REPO_SLUG]
    epics = repo_epics(mine)
    if not promote_only:
        close_finished_epics(epics)
    mine = [c for c in mine if c["identifier"] not in epics]
    promote_ready(active_count=len(mine))
    if promote_only:
        print(f"promote-only: gate evaluated (WIP base {len(mine)})")
        if _write_failures:
            sys.exit(
                f"reconcile: {len(_write_failures)} write failure(s) — see ERROR lines above"
            )
        return
    for card in mine:
        ident, state = card["identifier"], card["state"]["name"]
        if held(card) or ident in flagged:
            continue  # human-hold: untouched until a human removes the label
        if age_minutes(card["updatedAt"]) < STALE_MINUTES.get(state, 9999):
            continue

        try:
            pr = pr_for(ident)
        except ReconcileReadError as e:
            # An unreadable answer is NOT "no PR": act on nothing for this
            # card (no requeue, no receipt), sweep the rest, exit red at the
            # end so medic sees it (DRE-2034; happened live twice 2026-06-28).
            _read_failures.append(str(e))
            print(f"ERROR: pr_for {ident}: {e}", file=sys.stderr)
            continue
        merged = pr is not None and pr["state"] == "MERGED"
        is_open = pr is not None and pr["state"] == "OPEN"
        print(f"stale: {ident} in {state} (pr={pr['number'] if pr else None})")

        if merged:
            linear_ops.cmd_state(ident, "Done")
            linear_ops.cmd_comment(ident, "🧹 Reconcile: PR was already merged — moved to Done.")
        elif state == "Todo" and not is_open:
            # The receipt follows the dispatch's REAL outcome: a 🧹 success
            # receipt on a 403'd dispatch is the DRE-1254 false-receipt class.
            # The success note is _TODO_REDISPATCH_NOTE so the watchdog's
            # prior-redispatch detection (see flag_stranded) still matches it.
            if redispatch(card):
                linear_ops.cmd_comment(ident, f"🧹 Reconcile: {_TODO_REDISPATCH_NOTE}.")
            else:
                linear_ops.cmd_comment(
                    ident,
                    "🚨 Reconcile: re-dispatch FAILED — the dispatch call did not "
                    "go through, so no run was started. The sweep run is red; "
                    "medic will pick it up, and the next sweep retries.",
                )
        elif state == "In Progress":
            if is_open:
                linear_ops.cmd_advance(ident, "In QA", "In Progress")
                if _nudge("qa-review.yml", pr["number"]):
                    linear_ops.cmd_comment(
                        ident,
                        "🧹 Reconcile: PR exists but card was stuck In Progress — advanced to In QA, critic re-triggered.",
                    )
            else:
                # No PR past the staleness window: dead (silent crash), HUNG
                # (timed out — never reached agent-task's report step, so only
                # we see it) — or STILL RUNNING a legitimately long build.
                # Check liveness FIRST: a queued/in_progress run means not
                # dead regardless of elapsed time, and a requeue would kill it
                # (the Todo transition re-dispatches; the fresh run cancels
                # the live one via the per-card concurrency group — DRE-2032,
                # run 29125285930 / DRE-2023's three-loop death). Otherwise
                # requeue a couple of times; after the shared cap, HOLD
                # instead of looping forever (DRE-1403).
                if agent_run_alive(ident):
                    print(f"live: {ident} agent run still going — leaving alone")
                    continue
                dead = linear_ops.count_comments(ident, DEAD_TAG)
                if dead >= REQUEUE_CAP:
                    linear_ops.add_label(ident, HOLD_LABEL)
                    # --park: a deliberate HOLD-cap park (DRE-1403). Without it
                    # the DRE-1885 building-card guard would re-route this
                    # In Progress → Backlog move to Todo and re-loop forever.
                    linear_ops.cmd_state(ident, "Backlog", "--park")
                    linear_ops.cmd_comment(
                        ident,
                        f"🚨 held-for-human: agent keeps dying with no PR (hung or "
                        f"silent) after {dead} requeues — parked in Backlog with the "
                        f"'{HOLD_LABEL}' label so the sweep stops looping. A human must "
                        "split/fix the card and clear the label to retry.",
                    )
                else:
                    linear_ops.cmd_state(ident, "Todo")
                    linear_ops.cmd_comment(
                        ident,
                        f"🪦 {DEAD_TAG}: In Progress with no PR past the "
                        f"{STALE_MINUTES['In Progress']}-minute window — agent run "
                        f"appears dead (hung or lost). Requeued to Todo "
                        f"(dead run {dead + 1}/{REQUEUE_CAP + 1}).",
                    )
        elif state == "In QA" and is_open:
            if has_verdict(pr):
                if _nudge("merge-gate.yml", pr["number"]):
                    linear_ops.cmd_comment(
                        ident,
                        "🧹 Reconcile: verdict present but merge never happened — merge gate re-triggered.",
                    )
            else:
                if _nudge("qa-review.yml", pr["number"]):
                    linear_ops.cmd_comment(
                        ident, "🧹 Reconcile: no critic verdict after 2h — review re-triggered."
                    )
        elif state == "In QA" and not is_open:
            # Capped like the In Progress dead-run path (DRE-1403 mechanics,
            # same shared DEAD_TAG counter): uncapped, a card whose PR keeps
            # reading as gone laps In QA → Todo → In Progress → In QA forever,
            # burning an agent run per lap (DRE-2034).
            dead = linear_ops.count_comments(ident, DEAD_TAG)
            if dead >= REQUEUE_CAP:
                linear_ops.add_label(ident, HOLD_LABEL)
                # --park: deliberate HOLD-cap park, same DRE-1885 opt-out as
                # the In Progress hold.
                linear_ops.cmd_state(ident, "Backlog", "--park")
                linear_ops.cmd_comment(
                    ident,
                    f"🚨 held-for-human: In QA with no PR after {dead} requeues — "
                    f"parked in Backlog with the '{HOLD_LABEL}' label so the sweep "
                    "stops looping. A human must split/fix the card and clear the "
                    "label to retry.",
                )
            else:
                linear_ops.cmd_state(ident, "Todo")
                linear_ops.cmd_comment(
                    ident,
                    f"🪦 {DEAD_TAG}: In QA with no PR — requeued to Todo "
                    f"(dead run {dead + 1}/{REQUEUE_CAP + 1}).",
                )
        elif state == "In Review" and is_open:
            if _nudge("merge-gate.yml", pr["number"]):
                linear_ops.cmd_comment(
                    ident, "🧹 Reconcile: stuck In Review — merge gate re-triggered."
                )
        nudges += 1
    print(f"sweep complete: {nudges} nudge(s)")
    if _write_failures or _read_failures:
        # Red run -> medic's failed-workflow path picks it up. Never exit 0
        # when a write we claimed to make didn't happen (DRE-1254 lesson) or
        # when a card's PR state was unreadable (DRE-2034 lesson).
        sys.exit(
            f"reconcile: {len(_write_failures)} write / {len(_read_failures)} read "
            "failure(s) — see ERROR lines above"
        )


if __name__ == "__main__":
    main(
        promote_only="--promote-only" in sys.argv,
        conflicts_only="--conflicts-only" in sys.argv,
        close_only="--close-epics" in sys.argv,
    )
