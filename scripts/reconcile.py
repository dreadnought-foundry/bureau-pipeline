#!/usr/bin/env python3
"""Reconcile sweep: Linear card states vs pipeline reality (stdlib + gh CLI).

For every atlas card in an active state, verify reality matches and nudge the
pipeline when it doesn't. Self-limiting: every nudge comments on the card,
which bumps updatedAt past the staleness threshold for the next sweep.

Checks (card must carry **Repo:** atlas, any owner-prefix form):
  Todo        stale >15m, no open PR, no fresh dispatch -> re-fire dispatch
  In Progress stale >1h: PR open -> advance to the review lane + trigger
              qa-review; no PR -> back to Todo (relay re-dispatches on the
              transition)
  In Review   stale >2h: PR merged -> Done; verdict bound to the current
              head (DRE-1990) -> merge-gate; verdict missing OR stale/
              unbound -> re-trigger qa-review; no PR -> back to Todo,
              capped by the shared dead-run cap (DRE-2034)

The review lane is ONE lane since DRE-2726 — the two that preceded it both
meant "a pull request is open and being checked", and the split made the
sweep's job depend on which of them a card happened to sit in rather than on
the evidence. It keeps the longer of the two stall windows.

No-checks watchdog (DRE-2261): every open, non-draft agent/* PR whose head
commit has ZERO check runs after NO_CHECKS_MINUTES gets ONE plain-English
comment on its linked card — regardless of the card's park state, because a
human-parked card suppresses every repair without raising a hand (DRE-2180
sat invisible five hours; portico PR #21 thirteen days). Alert-only: never
dispatches, so the DRE-2024 fix-loop cannot come back.

Unlanded-work watchdog (DRE-2682): every gate here keys off a PULL REQUEST, so
a pushed branch that never became one is outside the system entirely — DRE-2655
sat that way nineteen hours with the work finished, found by eye. Any
`agent/DRE-*` branch carrying commits with no PR ever opened (open, closed or
merged) is reported on its card after UNLANDED_MINUTES, naming the route out;
and a hand-built card in a working lane with no branch and no PR is reported
after HAND_IDLE_MINUTES, which is the alarm that replaces what `hand-built`
suppresses (DRE-2524). Alert-only, and nothing here reads git authorship — it
misleads in both directions. The full census of ways work escapes the one path
is docs/escape-census.md.

Crashed-review recovery (DRE-2282): an open agent PR whose review CRASHED
(FAILURE review check, no verdict at the head — the critic fails its job on
purpose and the medic correctly skips it, DRE-1921) gets ONE re-dispatch of
the review stub per head sha, receipted like the dependabot retries
(DRE-2071); a head that caps out, or one whose only verdict binds an older
sha with nothing running, is reported once on its card in plain English.
Never merges, never starts a build agent.

Answered-blocker restart (DRE-2409): a held agent PR whose latest fix-loop 🛑
blocker now carries a human operator decision after it gets ONE re-dispatch of
the fix agent, receipted on the PR, and its card released from the human queue
— the escalate-by-exception exit door used to need a hand `workflow_dispatch`
on top of the answer. A human comment that mentions an operator decision but
does not parse as one is reported on the PR instead of held in silence.

Stranded-card watchdog (DRE-1993): every card/epic in Todo / In Progress whose
repo has no route in the routing snapshot, or (this repo's cards) with no run
receipt, gets — after 30 minutes either way — ONE plain-English comment naming
the reason plus the needs-human label, so the board never says work is
happening while nothing runs. Planning has its OWN rule (DRE-2736): a card
there owes a classification, not a repo label or a run receipt, and is flagged
only after PLANNING_MINUTES with nothing happening to it (DRE-1978 sat in
Planning 7 days unseen).

Also runs the dependency gate: Backlog children whose parent epic is ACTIVATED
(= plan approved) are auto-promoted to Todo once every blocker is Done — blockers
read from Linear's native "blocks" relations AND from "Blocked by: DRE-N" /
"serialize after DRE-N" lines in the description. A WIP cap (MAX_WIP, default
DEFAULT_MAX_WIP active cards) throttles promotion so the pipeline never floods.
The calling workflow passes the repo's own cap through its `max_wip` input;
every promotion path in the pipeline uses that one value (DRE-2529).

Mid-epic discovery (DRE-2739): a Backlog child created AFTER its epic's most
recent green light is a card the approved plan never described, and promoting it
dispatches an agent within fifteen minutes whether or not anyone has read it. So
it promotes only once it carries a verdict (`mid-epic-verdict`), which the
mid-epic route records for it — the green light is waived, Layer 1 is not. An
epic whose green light Linear cannot report abstains rather than refusing. Every
full sweep also refreshes each active epic's growth record on the epic itself:
green-lit at N cards, running M, plus any card that joined without the plan
moving with it (scripts/mid_epic.py owns the whole mechanism).

An epic counts as ACTIVATED in EITHER Todo OR In Progress (DRE-1893). The CEO's
activation action is moving an approved epic to **In Progress** — that is what
standards/card-quality.md and the planner brief tell them, and what the plan
comment asks for (DRE-2727). Todo remains accepted and activates identically, so
an epic started the old way still flows; the set of parent states that count as
"active" is unchanged, and so are MAX_WIP and the blocker checks.

EPIC-LEVEL dependencies (DRE-1772): the gate also honours dependencies between
EPICS. Before promoting an epic's children, it checks that EPIC's own
"blocked-by" relations (read the same way as a card's); if any blocker epic is
not Done, none of that epic's children promote this sweep — regardless of the
epic's own state. And when a blocker epic reaches Done, every epic blocked-by
it whose blockers are now ALL Done is auto-advanced from Backlog to Triage
(which re-triggers the planner) — never to In Progress, so the Green Light
human-approval gate is preserved. Both behaviors fail SAFE on unreadable
relation data (don't promote / don't advance on uncertainty).

Env: LINEAR_API_KEY, GH_TOKEN, REPO (owner/name).
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess  # nosec B404 — fixed-arg calls to the gh CLI only
import sys
import tempfile
import time
from datetime import UTC, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import break_glass  # noqa: E402 — ONE source for the sanctioned gate bypass (DRE-2737)
import card_pr  # noqa: E402 — ONE source for "does this card have a PR?" (DRE-2316)
import dead_run  # noqa: E402 — ONE source for the dead-run tags and cap
import fix_concurrency  # noqa: E402 — ONE source for the fix loop's grouping (DRE-2810)
import fix_context  # noqa: E402 — ONE parser for what an operator decision is
import fix_dead_run  # noqa: E402
# DRE-2726: ONE source for the lanes, their order and their stall windows —
# config/lane-contract.json, the file the harness asserts the live board against
# and docs/lane-contract.md is rendered from.
import lane_contract  # noqa: E402
import linear_ops  # noqa: E402
# DRE-2340: ONE implementation of the verdict binding — the sweep must read
# a verdict exactly the way the gate does (DRE-1998 had to fix an
# independent copy once already).
import merge_gate  # noqa: E402
# DRE-2739: ONE source for the mid-epic discovery route — what counts as a card
# added after the green light, and the verdict it must carry before it promotes.
import mid_epic  # noqa: E402
# DRE-2291: ONE source for the head-bound review check's name — the sweep
# must read the same record qa-review.yml writes.
from publish_review_check import CHECK_NAME as HEAD_REVIEW_CHECK_NAME  # noqa: E402
# DRE-2724: ONE source for the routing vocabulary — where a verdict sends a
# card, who picks it up there, and which of the five may be dispatched at all.
import routing_verdict  # noqa: E402
# DRE-2682: ONE source for "this card is finished" — the terminal states the
# structural sweep already names, read here rather than spelled a fifth time.
import structural_repair  # noqa: E402
import validate_card  # noqa: E402 — VALID_SLUGS, the canonical routing snapshot
import verdict_content  # noqa: E402 — the content-binding algorithm

REPO = os.environ["REPO"]
REPO_SLUG = os.environ.get("REPO_SLUG", "atlas")

# The stall windows come from the lane contract (DRE-2726), not from a second
# copy here: a lane's stall budget is part of what the lane IS, and the file the
# harness asserts the board against is where it belongs. In Progress dropped
# 180→60 because silent agent deaths now requeue instantly from the run itself;
# this timer is only the backstop for lost run outcomes. The review lane keeps
# 120 — the longer of the two windows the fold merged.
STALE_MINUTES = lane_contract.stale_minutes()

# The ONE review lane (DRE-2726). Read THROUGH the contract rather than written
# down here: lane_contract.lane() raises UnknownLane if the board ever stops
# carrying it, so a rename fails at import instead of leaving a dead literal
# that writes into a state Linear no longer has.
REVIEW_LANE = lane_contract.lane("In Review")["name"]
# The ONE work-in-progress cap (DRE-2529). This constant is the single source
# of truth: scripts/check_wip_cap.py reads it and fails the build unless every
# reusable workflow declares its `max_wip` input with exactly this default, so
# a stub that passes nothing inherits a value that is correct on its own.
# It was 4 while the workflows all said 8 — a fifth cap hiding behind an unset
# env var, which is the same defect this card removes from the workflows.
DEFAULT_MAX_WIP = 8


def resolve_max_wip(raw):
    """The cap from the environment, falling back to the one default.

    Empty is NOT zero. `MAX_WIP` now comes from a workflow_call input, and on
    any event where the `inputs` context is empty the interpolation yields the
    empty string — `int("")` would crash the promotion step outright, turning
    a cap question into a red run. Unset, empty, and unparseable all mean
    "the one default".
    """
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return DEFAULT_MAX_WIP


MAX_WIP = resolve_max_wip(os.environ.get("MAX_WIP"))

# Parent-epic states that count as ACTIVATED for the dependency gate (DRE-1893).
# The CEO activates an approved epic by moving it to **In Progress** (DRE-2727 —
# the verb the standard, the planner brief and the plan comment all name). Todo
# was added by DRE-1893 and is still accepted: an epic in either state promotes
# its unblocked Backlog children, so an epic started the old way still flows.
# Anything else (Backlog, Planning, Green Light, Done, …) is not active and its
# children stay parked.
EPIC_ACTIVE_STATES = ("Todo", "In Progress")

# Human-hold (DRE-1403). A card whose agent keeps dying with no PR — whether it
# crashes (counted by agent-task) or HANGS/times out (seen only here) — is
# requeued at most REQUEUE_CAP times. After that it is parked in Backlog with
# HOLD_LABEL so neither the relay nor this sweep re-dispatch it into the same
# wall. Both paths count the shared DEAD_TAG so the cap is unified. A human
# splits/fixes the card and releases it with `linear_ops.py unpark <CARD>`,
# which posts RESET_TAG — every death count below is taken SINCE that marker, so
# a released card gets a genuinely fresh budget instead of re-holding on its
# first death (DRE-2308/2309/2310, held by a fleet-wide model misconfiguration).
# Aliases, not copies: the strings live once, in dead_run.
HOLD_LABEL = dead_run.HOLD_LABEL
DEAD_TAG = dead_run.DEAD_TAG
RESET_TAG = dead_run.RESET_TAG
REQUEUE_CAP = int(os.environ.get("DEAD_RUN_CAP", "2"))

# Per-card isolation for the dependency gate (DRE-2035). One bad "Blocked by:"
# reference used to kill the WHOLE sweep: card_state() on a nonexistent id made
# linear_ops raise SystemExit, which sails past every `except Exception`
# (BaseException subclass) — promotion, nudges, and the dead-run machinery all
# died every run until a human found the offending card. linear_ops now raises
# LinearError instead, and the gate SKIPS just the unevaluable card: a loud
# ERROR line, ONE explanatory comment on the card (keyed on this tag, like
# DEAD_TAG), a sweep-level skip count — and the rest of the sweep proceeds.
BAD_REF_TAG = "unresolvable-blocker-reference"
_card_skips: list[str] = []

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
# same wall.
#
# A LANE'S STRAND RULE MATCHES WHAT THAT LANE'S OCCUPANTS OWE (DRE-2736).
# Planning used to be swept here, and the no-route class fired on the first
# sweep that saw a card — fine while almost nothing sat in Planning, fatal
# under DRE-2719, where every card enters through Planning and a card in
# Planning has NO repo: label yet, because assigning one is what Planning
# does. Every card on the front path would have collected the hold label
# within fifteen minutes, and promote_ready() skips a held card permanently:
# a front door that manufactures unpromotable cards. (With DRE-2725's
# move-to-Triage upgrade it is worse — Triage's exit re-classifies into
# Planning, so the flag becomes a loop.) So the lanes below own the two
# classes a Todo / In Progress card really does owe, and Planning gets its
# own rule in flag_stalled_planning(). The nudge loop / WIP cap still never
# see Planning (it is not in SWEEP_STATES, and there is no defined nudge) — the
# contract's stall window for it feeds flag_stalled_planning() alone.
WATCHDOG_LANES = ("Todo", "In Progress")
WATCHDOG_MINUTES = int(os.environ.get("WATCHDOG_MINUTES", "30"))
WATCHDOG_TAG = "stranded-watchdog"

# Planning's own lane and threshold (DRE-2736). Longer than WATCHDOG_MINUTES
# on purpose: what a Planning card owes is a CLASSIFICATION, and producing one
# is a planner run — plan.yml's job alone is capped at 45 minutes (DRE-2721
# widened it for the pre-approval critic's two rounds and the one re-plan
# between them), so sharing the 30-minute threshold would alarm on runs that
# are simply still going.
# Every receipt the planner posts bumps updatedAt, so this clock only runs on
# a card nothing is happening to.
# The default is the contract's own stall window for the lane (DRE-2726), so
# the number a reader finds in docs/lane-contract.md is the number that runs.
PLANNING_LANE = ("Planning",)
PLANNING_MINUTES = int(
    os.environ.get("PLANNING_MINUTES", STALE_MINUTES["Planning"])
)

# Hand-built work is not stranded work (DRE-2524). On 2026-08-17 five portico
# cards (DRE-2499/2500/2501/2505/2507) each collected a 🚨 notice plus the hold
# label and ALL FIVE were false alarms: the work had been built by hand before
# the card existed, and the run the watchdog reported as "never started" opened
# a PR forty minutes later. Both strand classes are wrong on that work — no
# dispatched run is coming BY DESIGN, and routing is irrelevant when nothing is
# being routed. The NO-ROUTE notice even prescribes "so it must be hand-built",
# so on a card labelled hand-built it tells us to do what we did.
#
# This label suppresses two things: flag_stranded's alarm, and — in main()'s
# nudge loop — the sweep's OWN dispatch on a card with no PR yet. Silencing the
# alarm alone leaves the engine that raised it running: "Todo, no PR" past 15
# minutes is the normal state of hand-built work, and the loop answers it with
# a real repository_dispatch, i.e. a second agent run competing with the human
# on the same card; "In Progress, no PR" past 3 hours requeues to Todo (feeding
# that same dispatch next sweep) and, past REQUEUE_CAP, parks the card to
# Backlog with the hold label — the sweep overriding the human's own placement.
#
# It is deliberately not a second HOLD_LABEL: hold means "a human owes this
# card an action" and stands down repairs; hand-built means "no agent was ever
# coming". Everything that keys on a PULL REQUEST stays label-blind — the
# PR-level backstops below (flag_no_checks_prs, flag_unowned_prs,
# unstick_conflicts, retrigger_dead_heads, …) and the nudge loop's own
# PR-carrying branches (merged → Done, open PR → In Review + critic). So a
# hand-built card whose PR wedges is still caught, and once the human opens a
# PR the sweep shepherds it exactly as before — see
# test_hand_built_not_stranded.py, which asserts both halves structurally.
#
# WHAT SUPPRESSION OWES (DRE-2682). Silencing an alarm without replacing it is
# how DRE-2655's finished work sat invisible for nineteen hours: the label that
# says "no agent is coming" also switched off the only thing that would have
# said the work had stopped. So this label now has a counterpart alarm that
# measures what hand-built work actually owes — _flag_hand_built_idle, plus the
# branch half beside it — and the suppression above is legitimate only for as
# long as that counterpart exists.
HAND_BUILT_LABEL = "hand-built"

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


def hand_built(card: dict) -> bool:
    """True if the card carries HAND_BUILT_LABEL (DRE-2524).

    The work is done by a human or a local agent rather than a dispatched
    pipeline agent, so "no run receipt", "no dispatch route" and "no PR yet"
    are all the normal state, not evidence of a stall. Read by exactly two
    callers, both answering "should the pipeline start or restart an agent on
    this card": flag_stranded (the alarm) and main()'s nudge loop on a card
    with no PR (the dispatch that alarm was reporting on). Never read by a
    PR-keyed repair path, or this would silently become a second, wider hold.
    """
    return any(
        lbl["name"].lower() == HAND_BUILT_LABEL
        for lbl in (card.get("labels") or {}).get("nodes", [])
    )

# "**Blocked by:** DRE-1204 + DRE-1205", "Serialize after: DRE-1226", "Depends
# on DRE-N" — blockers are every DRE-N on a line that DECLARES a dependency.
# Line-scoped on purpose: parent-epic links appear all over card bodies and
# must not count as blockers.
#
# ANCHORED on purpose (DRE-2670). A bare substring match read a blocker out of
# any sentence that merely MENTIONED one, so epic DRE-2492 — zero formal
# `blockedBy` relations, a well-written plan — was jammed by its own prose:
# "B3 is formally blocked by it" and "neither depends on the other" each named
# one of the epic's own CHILDREN, the epic-level gate then held those children,
# and the children were what would have unblocked it. Five cards, five days,
# ~480 consecutive GREEN sweeps. A sentence whose literal meaning is "there are
# no dependencies here" was parsed as declaring one.
#
# So the phrase must OPEN the line (after list/quote/heading/emphasis markup)
# and be followed by a colon or the ids themselves. Same anchored idea as
# `linear_ops._BLOCKED_BY_RE`, which turns these lines into real relations —
# that one is deliberately narrower (bold-or-bare "Blocked by:" only), and
# narrower is safe here: this gate reading MORE declarations than the creation
# path writes relations for can only hold a card, never release one early.
#
# The prefix accepts ORDERED list markers too (`1.` / `2)`). card-quality.md
# promises the declaration may sit "inside a list item" without naming a style,
# and numbered acceptance-criteria lists are common on these cards — dropping
# them failed UNSAFE (the opposite of DRE-2492): a card with a real, undone
# dependency would have read as free to promote. The marker only widens what
# may PRECEDE the phrase, so a numbered line that merely mentions a dependency
# mid-sentence still declares nothing.
_BLOCKER_LINE = re.compile(
    r"^[\s>*_`~+#-]*"                             # -, *, >, #, **bold**, `code`
    r"(?:\d+[.)][\s>*_`~+#-]*)?"                  # 1. / 2) ordered list item
    r"(?:blocked by|serialize after|depends on)"
    r"[\s*_`]*"                                   # closing emphasis markers
    r"(?::|(?=\s*DRE-\d+))",                      # a colon, or the ids directly
    re.IGNORECASE,
)
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


def gh_actions_read(*args: str) -> str | None:
    """Read the Actions API under a token that can actually read it.

    Origin (2026-08-17): DRE-1254 found `gh workflow run` executing under the
    minted App token, which lacks Actions:write — "HTTP 403: Resource not
    accessible by integration", discarded by the silent gh() helper. It fixed
    the dispatch and left every Actions READ behind. The App token 403s there
    too (reproduced live against DeltaSolv/deltasolv; the workflow-scoped AND
    repo-wide runs endpoints both refuse), and the silent helper turned that
    into fabricated data at six call sites:

      * ``json.loads(gh(...) or "[]")`` -> [] -> "no run is busy" -> the
        backoff that exists because of the 2026-06-28 quota burn never
        engaged (FAIL-OPEN);
      * ``json.loads("")`` -> ValueError -> "a review is in flight" forever;
      * a status read of "" -> never "completed" -> the run looks alive forever.

    So: swap in GH_DISPATCH_TOKEN exactly as gh_dispatch does (the calling
    stub grants actions:write to the workflow's github.token), and return
    **None** — never "" and never "[]" — when the read fails, so no caller can
    mistake unreadable for a real empty answer (the DRE-2034 discipline). The
    failure is recorded, so the sweep exits 1 and medic sees it.

    Callers choose their own safe answer for None; every one of them must fail
    CLOSED (assume busy / assume in flight / assume alive), because acting on
    an unreadable listing is what burst-dispatches and cancels live runs.

    Structure mirrors gh_dispatch: swap the token only when there IS one, and
    that is also the only path that can tell "unreadable" from "empty". With a
    token we see rc and stderr, so a 403 is recorded and answered None. With no
    GH_DISPATCH_TOKEN the call goes through the ordinary gh() seam, which
    discards rc — so an empty answer stays ambiguous and is passed through
    unchanged rather than guessed at.

    That split is deliberate and it lands the guard where the bug lives: the
    calling stub always passes github.token, so every pipeline run takes the
    loud path. The quiet path is local runs and unit tests, which stub gh() and
    legitimately return "" for calls they do not model.
    """
    out, detail = _actions_read(args)
    if detail is not None:
        _note_actions_read_failure(args, detail)
    return out


def _actions_read(args: tuple[str, ...]) -> tuple[str | None, str | None]:
    """One Actions read, WITHOUT deciding what its failure means.

    Returns (output, failure detail). `detail` is None on success and on the
    quiet no-token path; when it is a string the read failed loudly and
    `output` is None. Split out of gh_actions_read (DRE-2525) for the one
    caller that must inspect a failure before it is recorded — see
    _actions_runs_busy, where a 404 can mean "this repo has no such stub"
    rather than "unreadable". Everyone else keeps the old contract: the
    failure is recorded the moment it happens.
    """
    dispatch_token = os.environ.get("GH_DISPATCH_TOKEN")
    if not dispatch_token:
        return gh(*args), None
    p = subprocess.run(  # nosec B603 B607 — fixed-arg gh call, shell=False
        ["gh", *args], capture_output=True, text=True, check=False,
        env={**os.environ, "GH_TOKEN": dispatch_token},
    )
    if p.returncode != 0:
        return None, f"rc={p.returncode}: {p.stderr.strip()[:400]}"
    return p.stdout.strip(), None


def _note_actions_read_failure(args: tuple[str, ...], detail: str) -> None:
    """Record an unreadable Actions read so the sweep exits 1 (DRE-2034)."""
    err = (
        f"gh {' '.join(args)} (Actions read) failed — {detail}. Treating as "
        "UNREADABLE; callers fail closed, and the sweep goes red rather than "
        "act on fabricated data."
    )
    _read_failures.append(err)
    print(f"ERROR: {err}", file=sys.stderr)


#: Where a consumer repo's pipeline stubs live. The contents API answers for
#: the DEFAULT BRANCH when no ref is given — the same branch `gh run list
#: --workflow` resolves a workflow name against, which is the question here.
_WORKFLOWS_DIR = ".github/workflows"


def workflow_on_default_branch(workflow: str) -> bool | None:
    """Is `workflow` a file in .github/workflows on REPO's default branch?

    True / False / **None when it cannot be proved either way** (DRE-2525).

    Drawn positively, off the CONTENTS API, rather than by reading gh's error
    text: `gh run list --workflow X` 404s both when the workflow file is not on
    the default branch AND when the token may not read that ref, and the two
    must not be conflated. Asking a different API a different question settles
    it — the file is either listed or it is not.

    Absence is proved only by a listing that was read, parsed, and does not
    contain the file. An unreadable, empty, unparseable or non-list answer
    proves NOTHING and returns None, so the caller carries on to the Actions
    read and fails closed exactly as it did before: git cannot store an empty
    directory, so `[]` from a real repo is a failure wearing a success's
    clothes, not evidence.

    Called ONLY when an Actions read has already failed, so a healthy sweep
    costs no extra API call and the probe runs at most once per failing
    workflow per sweep.

    Silent gh() by design — this helper HAS its own fallback (None), and it
    reads the contents API, not the Actions API the AST guard in
    test_reconcile_actions_reads_403.py polices.
    """
    raw = gh("api", f"repos/{REPO}/contents/{_WORKFLOWS_DIR}")
    try:
        entries = json.loads(raw) if raw else None
    except ValueError:
        entries = None
    if not isinstance(entries, list) or not entries:
        return None  # unreadable/empty — never provable absence
    return workflow in {
        e.get("name") for e in entries if isinstance(e, dict)
    }


def _actions_runs_busy(workflow: str) -> bool:
    """True when a run of `workflow` is queued/in_progress — or unreadable.

    The single busy-guard used by every dispatch site. Unreadable answers
    BUSY on purpose: deferring one sweep costs 15 minutes, while dispatching
    off a fabricated empty list burst-drains the App and LLM quotas.

    A workflow that is ABSENT from the target repo is a third answer, not a
    failure (DRE-2525): a consumer may legitimately lack an optional stub, and
    no run of a file that does not exist can be in flight. bureau-harness has
    no agent-fix stub, so this guard asked for its runs, got a 404, recorded it
    as UNREADABLE and took the sweep red — 61 consecutive failed runs over
    18h37m on 2026-08-18, ~61 emails a day, in the repo that produced 3,074
    runs in seven days, 23% of all fleet workflow runs, while GitHub spend sat
    at 90% of budget.

    So a FAILED read is adjudicated before it is recorded: if the workflow file
    is provably not on the default branch, there was nothing to read and the
    sweep stays green. Everything else — a revoked permission, a missing scope,
    an unreadable ref, an absence the contents API could not confirm — is still
    UNREADABLE: recorded, answered BUSY, sweep red. Collapsing the two would
    hide a real permission failure, which is the same mistake pointing the
    other way.

    Deliberately NOT extended to the dispatch sites: if such a repo ever does
    produce a wedged PR, `gh workflow run` on the missing stub still fails
    LOUDLY. A repo with no fix agent and a stuck pull request is a real problem
    and must not be swallowed by this quieting.
    """
    args = ("run", "list", "--repo", REPO, "--workflow", workflow,
            "--limit", "10", "--json", "status")
    out, detail = _actions_read(args)
    if detail is not None:
        if workflow_on_default_branch(workflow) is False:
            print(
                f"busy-guard: {workflow} is not on {REPO}'s default branch — "
                "this repo has no such stub, so no run of it can be in "
                "flight. Nothing to check, and nothing to report."
            )
            return False
        _note_actions_read_failure(args, detail)
        return True
    if out is None:
        return True
    try:
        busy = json.loads(out or "[]")
    except ValueError:
        _read_failures.append(
            f"unparseable run listing for {workflow}: {out[:200]!r}")
        return True
    return any(r.get("status") in ("queued", "in_progress") for r in busy)


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


def review_workflow() -> str:
    """The DISPATCHABLE critic stub's filename for this sweep's repo.

    Product repos name their qa-review stub qa-review.yml. bureau-pipeline
    itself can't: that filename IS the reusable definition (workflow_call
    only — `gh workflow run qa-review.yml` 422s there), so its stub is
    pr-review.yml (test_self_host_stubs.py). Every review dispatch in this
    sweep must resolve through here or the self-host repo's re-reviews and
    dependabot reviews (DRE-2047) silently never fire.
    """
    return "pr-review.yml" if REPO_SLUG == "bureau-pipeline" else "qa-review.yml"


def fix_workflow() -> str:
    """The DISPATCHABLE fix stub's filename for this sweep's repo.

    Same resolution as review_workflow(), same reason: product repos name
    their agent-fix stub agent-fix.yml, but in bureau-pipeline that filename
    IS the reusable definition (workflow_call only — `gh workflow run
    agent-fix.yml` 422'd every sweep, run 29198533233 / DRE-2056), so its
    stub is self-agent-fix.yml. Both the dispatches AND the busy-guard
    `run list` calls must resolve through here — runs only ever exist under
    the stub's filename, so a guard watching the reusable reads permanently
    idle.
    """
    return "self-agent-fix.yml" if REPO_SLUG == "bureau-pipeline" else "agent-fix.yml"


def gate_workflow() -> str:
    """The DISPATCHABLE merge-gate stub's filename for this sweep's repo.

    Third member of the review_workflow()/fix_workflow() family (DRE-2056):
    the In Review nudges dispatch merge-gate.yml, which in
    bureau-pipeline is the workflow_call-only reusable — the stub is
    self-merge-gate.yml.
    """
    return "self-merge-gate.yml" if REPO_SLUG == "bureau-pipeline" else "merge-gate.yml"


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


def age_minutes(iso: str, now: str | None = None) -> float:
    """Minutes since `iso`. `now` (an ISO string) makes the age testable
    without freezing the clock — every existing caller omits it."""
    then = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    at = (datetime.fromisoformat(now.replace("Z", "+00:00"))
          if now else datetime.now(UTC))
    return (at - then).total_seconds() / 60


# The nudge loop's lanes: the work-segment lanes that carry a stall window,
# derived from the contract rather than listed here (DRE-2726). A lane with a
# declared window is a lane the sweep owes a nudge; Done carries no window
# because there is nothing to nudge a finished card toward, and Planning is a
# planning-segment lane with its own rule in flag_stalled_planning(). The
# watchdog passes WATCHDOG_LANES instead to also see Planning.
SWEEP_STATES = tuple(
    name
    for name in lane_contract.flow_lanes()
    if name in STALE_MINUTES and lane_contract.lane(name)["segment"] == "work"
)


def drain_retiring_lanes() -> None:
    """Move every card out of a lane the contract is retiring (DRE-2726).

    A retiring lane is one the pipeline no longer writes to. The board still
    has it — archiving a Linear state is a workspace change in another repo —
    so cards the PREVIOUS pipeline put there would sit forever with nothing
    coming for them. The contract names each retiring lane's replacement and
    this drains into it, every sweep, until the board catches up.

    Order matters and is the reason this exists at all: the code stops writing
    the lane first, the sweep drains it second, the board archives the state
    third, and the contract entry is deleted fourth. Archiving before the drain
    would fail every in-flight write instead.

    NOTHING IS RETIRING TODAY, and that is deliberate rather than neglect
    (DRE-2818): the operator confirmed DRE-2726's drain was finished, Linear
    archived both states, and the two entries were deleted — a retiring entry
    whose state Linear already dropped is itself drift, and
    `board.retired_entry_is_deleted` fails on it. This function stays because
    it is step two of a protocol the contract still defines: `retiring` remains
    a validated lane status with a mandatory board step, and the Phase-3 rule
    `board.retiring_lane_is_empty` names this drain as its prerequisite. With
    an empty retiring set it returns before it reads anything from Linear, and
    tests/test_lane_fold_in_review.py pins both that inertness and — against an
    injected retiring lane, since the real file no longer has one — the
    behaviour it will have on the next retirement.

    Idempotent by construction — `cmd_advance` moves a card only while it is
    still in the lane named, so a second sweep finds nothing and a race between
    two repos' sweeps costs one no-op.
    """
    draining = {
        lane["name"]: lane
        for lane in lane_contract.lanes(status="retiring")
        if lane.get("replaced_by")
    }
    if not draining:
        return
    try:
        stranded = active_cards(tuple(draining))
    except Exception as e:  # a read failure here must not kill the sweep
        raise ReconcileWriteError(f"drain: could not list retiring lanes: {e}") from e
    for card in stranded:
        ident = card["identifier"]
        was = card["state"]["name"]
        lane = draining.get(was)
        if lane is None:
            continue  # not a retiring lane: the state filter is the query's job
        to = lane["replaced_by"]
        try:
            linear_ops.cmd_advance(ident, to, was)
            linear_ops.cmd_comment(
                ident,
                f"🧹 Reconcile: the '{was}' lane is being retired "
                f"({lane['retired_by']}) and nothing writes to it any more — "
                f"moved to '{to}', which is where the work now continues.",
            )
        except Exception as e:
            raise ReconcileWriteError(f"{ident} drain {was}->{to}: {e}") from e
    if stranded:
        print(f"drain: moved {len(stranded)} card(s) out of retiring lane(s)")


def active_cards(states: tuple[str, ...] = SWEEP_STATES) -> list[dict]:
    """EVERY card in `states`, not the first page of them (DRE-2681).

    This list is the sweep's whole world: the nudge loop, the WIP count that
    budgets promotion, and the stranded-card watchdog all read it. A single
    unpaginated page silently truncated every one of them at 100 rows.
    """
    return linear_ops.gql_paged(
        """query($states: [String!]!, $after: String) {
           issues(first: 100, after: $after, filter: {
             team: {key: {eq: "DRE"}},
             state: {name: {in: $states}}
           }) { nodes {
             id identifier title description updatedAt
             state { name } labels { nodes { name } }
           } pageInfo { hasNextPage endCursor } } }""",
        {"states": list(states)},
    )


# Live-snapshot re-check for NO-ROUTE claims (DRE-2260). Fleet repos pin the
# reusable workflows to a release tag, so THIS checkout's routing snapshot
# (validate_card.VALID_SLUGS — the bundled repo-map.json, or its fallback
# literal) can predate a repo's onboarding: atlas's and deltasolv's v4 sweeps
# called live `portico` cards "not on the dispatch rail" and parked nine of
# them needs-human, which made fix_dispatch_blocked() skip them and silently
# disabled unstick_conflicts for the life of each card (DRE-2180 sat five
# hours on a conflicted PR mid-build). A pinned sweep cannot tell "not on the
# rail" from "onboarded after my pin", so before any NO-ROUTE claim about a
# labeled card, flag_stranded re-checks the slug against the CANONICAL
# snapshot: config/repo-map.json at bureau-pipeline@main, the published
# mirror of the relay's SSM map (public repo, raw read needs no scopes).
# ONE literal for one fact (DRE-2681): the Todo gate confirms an unknown
# `repo:` label against the same snapshot for the same reason, so the endpoint
# is defined once, in validate_card, and read here.
_CANONICAL_SNAPSHOT_ENDPOINT = validate_card.CANONICAL_SNAPSHOT_ENDPOINT


def live_rail_slugs() -> frozenset[str] | None:
    """The slug set of the canonical routing snapshot at bureau-pipeline@main,
    or None when it can't be read/parsed. The silent gh() is fine here — None
    is this caller's own fallback (defer the claim, per flag_stranded), and it
    must never collapse to an empty set, which would read as "flag everything".
    """
    raw = gh(
        "api", "-H", "Accept: application/vnd.github.raw+json",
        _CANONICAL_SNAPSHOT_ENDPOINT,
    )
    try:
        parsed = json.loads(raw) if raw else None
    except ValueError:
        parsed = None
    if not isinstance(parsed, dict) or not parsed:
        return None
    return frozenset(str(slug).lower() for slug in parsed)


def flag_stranded() -> set[str]:
    """DRE-1993 watchdog: flag active-lane cards with no evidence of work.

    Two strand classes, checked over WATCHDOG_LANES on every full sweep,
    plus Planning's own rule (flag_stalled_planning, called at the end —
    Planning is not one of these lanes since DRE-2736):
      (a) NO ROUTE — the card's repo slug is not in the routing snapshot
          (validate_card.VALID_SLUGS, mirroring the relay's map), so no
          dispatch can EVER start a run. Flagged after WATCHDOG_MINUTES,
          the same grace class (b) always had: a card is genuinely
          unroutable for a real interval between creation and the Todo
          gate's repair pass (validate_card infers a missing repo label and
          repairs it), and flagging on the first sweep RACES that repair —
          for a permanent consequence, since promote_ready() skips a held
          card forever (DRE-2736). Every repo's sweep checks this (an
          off-map card belongs to no sweep's `mine`); the once-ever gate
          makes whichever sweep gets there first the only one that speaks.
          Because fleet sweeps run PINNED checkouts whose snapshot can
          predate an onboarding (DRE-2260), a labeled slug is claimed
          unroutable only after the canonical @main snapshot confirms it:
          slug present
          there → stale pin, leave the card to a current sweep; snapshot
          unreadable → defer the claim to a later sweep. Only a label-less
          card — unroutable under ANY snapshot — needs no confirmation.
      (b) NO RUN — a dispatchable card of THIS sweep's repo with zero run
          receipts (the DRE-2032 🧠/⏳ proof-of-life comments; agent-task
          and plan both post them at run start) after WATCHDOG_MINUTES —
          or after a prior Todo-redispatch receipt, which resets updatedAt
          every cycle and would otherwise hide the strand forever.
          Epics in these lanes are containers — no run ever targets them,
          so their receipt-less state is normal, not a strand.

    Neither class applies to HAND-BUILT work (DRE-2524): a card labelled
    HAND_BUILT_LABEL is skipped outright, because no dispatched run is coming
    by design and routing is irrelevant when nothing is being routed. That
    label suppresses this watchdog and nothing else — the PR-level backstops
    further down key on the pull request, not on card labels.

    A card with ANY receipt started a run once (live or dead) and is never
    flagged by EITHER class: started-then-died is the dead-run requeue's
    case, and a stale-pinned sweep computes routable=False for a repo it
    has never heard of — parking a mid-build card would disable
    unstick_conflicts for its whole life (DRE-2260/DRE-2180, flagged 16s
    after a live ⏳ receipt).

    Flagging = one plain-English comment (🚨 + WATCHDOG_TAG) naming the
    reason, plus HOLD_LABEL — no state move, no cancel. A false positive
    (e.g. a run queued 30+ minutes behind a runner-capacity crunch, which
    posts no receipt until it starts) costs a label a human removes; the
    run itself is untouched. Fail loud beats fail silent (DRE-1979).

    Returns the identifiers flagged THIS sweep so the caller's nudge loop
    can skip them — their fetched labels predate the hold label.
    """
    flagged: set[str] = set()
    live: frozenset[str] | None = None
    live_fetched = False  # one canonical-snapshot read per sweep, and only if needed
    for card in active_cards(WATCHDOG_LANES):
        ident, state = card["identifier"], card["state"]["name"]
        if state == "Planning":
            continue  # Planning has its own rule (DRE-2736) — never these two
        if held(card):
            continue  # already in a human's queue — never spam
        if hand_built(card):
            # DRE-2524: neither class applies to work built by hand — no
            # dispatched run is coming and nothing is being routed.
            print(
                f"watchdog: {ident} is labeled '{HAND_BUILT_LABEL}' — no "
                "dispatched run is expected, so a missing run receipt and an "
                "off-rail repo are both normal here, not a strand"
            )
            continue
        slug = card_repo(card)
        routable = slug is not None and slug in validate_card.VALID_SLUGS
        if routable and slug != REPO_SLUG:
            continue  # that repo's own sweep runs the no-run check for its cards
        labels = [lbl["name"].lower() for lbl in card["labels"]["nodes"]]
        if routable and "agent:planner" in labels:
            continue  # an epic in these lanes carries no run — normal, not stranded
        bodies = linear_ops.comment_bodies(ident)
        if routing_verdict.is_parked(bodies):
            # DRE-2724: a PARKED card is well-formed and sitting still ON
            # PURPOSE. Reporting the intended state as a defect costs the card
            # a hold label a human has to remove, and promote_ready() skips a
            # held card forever — so the alarm would make the deliberate
            # decision permanent by accident.
            print(
                f"watchdog: {ident} is routed PARKED — deliberately not built, "
                "so time spent sitting still is not a strand"
            )
            continue
        if any(WATCHDOG_TAG in b for b in bodies):
            continue  # flagged once already — idempotent forever
        if any(b.lstrip().startswith(_LIFE_PREFIXES) for b in bodies):
            continue  # a run DID start (either class) — the dead-run machinery owns it now
        # ONE age gate, both classes (DRE-2736). It used to sit inside the
        # routable branch, so NO ROUTE fired on the first sweep that saw a
        # card and raced the Todo gate's repair pass. A prior redispatch
        # receipt substitutes for the elapsed time on either class: it resets
        # updatedAt every cycle, so "dispatch fired, nothing ran" is already
        # the evidence the clock was there to gather.
        redispatched = any(_TODO_REDISPATCH_NOTE in b for b in bodies)
        if not redispatched and age_minutes(card["updatedAt"]) < WATCHDOG_MINUTES:
            continue  # young — give the dispatch (and the gate's repair) time
        if routable:
            # Says what was OBSERVED and nothing more (DRE-2524). The old text
            # offered three suspects — the Actions budget, the LLM quota, the
            # relay — with no evidence for any of them, so the reader had to
            # check all three. One cause or "I don't know"; this is the latter.
            reason = (
                f"no agent run has started. Observed: this card has sat in "
                f"{state} for {WATCHDOG_MINUTES}+ minutes with no run receipt "
                "on it — every agent posts one the moment it starts, so as far "
                "as this sweep can see, nothing has begun. Why it has not "
                "started is not known from here. If a run is merely queued, "
                f"remove the '{HOLD_LABEL}' label and it will carry on; "
                "otherwise this card needs a human to look."
            )
        else:
            live_confirmed = ""
            if slug is not None:
                if not live_fetched:
                    live, live_fetched = live_rail_slugs(), True
                if live is None:
                    print(
                        f"watchdog: {ident} repo '{slug}' unknown to this "
                        "snapshot and the canonical snapshot is unreadable — "
                        "deferring the no-route claim to a later sweep"
                    )
                    continue  # can't tell dead route from stale pin — don't guess
                if slug in live:
                    print(
                        f"watchdog: {ident} repo '{slug}' onboarded after this "
                        "checkout's snapshot — left to a current sweep"
                    )
                    continue  # a stale pin, not a dead route (DRE-2260)
                live_confirmed = " and from the canonical snapshot at bureau-pipeline@main"
            snapshot = ", ".join(sorted(validate_card.VALID_SLUGS))
            reason = (
                f"repo '{slug or 'none — no repo label'}' isn't on the dispatch "
                "rail — no agent can ever pick this card up, so it must be "
                "hand-built (or the repo onboarded to the routing map first). "
                f"Absent from this sweep's routing snapshot [{snapshot}]"
                f"{live_confirmed}. Labeled '{HOLD_LABEL}' for a human."
            )
        linear_ops.cmd_comment(ident, f"🚨 {WATCHDOG_TAG}: {reason}")
        linear_ops.add_label(ident, HOLD_LABEL)
        flagged.add(ident)
        print(f"watchdog: {ident} in {state} flagged ({'no-run' if routable else 'no-route'})")
    return flagged | flag_stalled_planning()


def flag_stalled_planning() -> set[str]:
    """DRE-2736 watchdog: flag cards stalled in Planning, on Planning's terms.

    Planning's occupants owe a CLASSIFICATION — the decision about what the
    card is and where it goes — and nothing else. Not a `repo:` label
    (assigning one is what Planning does, so the whole front path arrives
    without one), and not a run receipt (a planner-created child inherits
    `repo:` and a role label but never `agent:planner`, so the no-run class's
    epic exemption never covered it). Judging Planning by those two rules is
    what made the watchdog flag every card on the new front path.

    So the only question asked here is the lane's own: has anything happened
    to this card in PLANNING_MINUTES? Every planner receipt, comment and state
    move bumps updatedAt, so the clock runs only on a card that is genuinely
    sitting still — the DRE-1978 shape, which sat in Planning for SEVEN DAYS
    with no planner run and which this rule must still catch. Removing
    Planning from the sweep instead of re-keying it would have given those
    seven days back.

    Same manners as flag_stranded: held and hand-built cards are skipped, the
    WATCHDOG_TAG comment is the once-ever idempotency marker, and flagging is
    one plain-English comment plus HOLD_LABEL — no state move, no cancel.
    Returns the identifiers flagged this sweep.
    """
    flagged: set[str] = set()
    for card in active_cards(PLANNING_LANE):
        ident, state = card["identifier"], card["state"]["name"]
        if state != "Planning":
            continue  # this rule speaks for one lane only
        if held(card):
            continue  # already in a human's queue — never spam
        if hand_built(card):
            print(
                f"watchdog: {ident} is labeled '{HAND_BUILT_LABEL}' — the "
                "pipeline is not planning this card, so time spent in "
                "Planning is not a strand"
            )
            continue
        bodies = linear_ops.comment_bodies(ident)
        if routing_verdict.is_parked(bodies):
            # DRE-2724, same rule as flag_stranded: PARKED is a decision, not a
            # stall. A parked card in Planning owes nobody a classification —
            # it has one.
            print(
                f"watchdog: {ident} is routed PARKED — deliberately not built, "
                "so time spent in Planning is not a strand"
            )
            continue
        if any(WATCHDOG_TAG in b for b in bodies):
            continue  # flagged once already — idempotent forever
        if age_minutes(card["updatedAt"]) < PLANNING_MINUTES:
            continue  # planning is young — let it produce its classification
        reason = (
            "planning has produced nothing. Observed: this card has sat in "
            f"Planning for {PLANNING_MINUTES}+ minutes with nothing posted or "
            "changed on it — a card in Planning owes a decision about what it "
            "is and where it goes, and no decision has been recorded. Why is "
            "not known from here. If planning is genuinely still going, remove "
            f"the '{HOLD_LABEL}' label and it will carry on; otherwise this "
            "card needs a human to look."
        )
        linear_ops.cmd_comment(ident, f"🚨 {WATCHDOG_TAG}: {reason}")
        linear_ops.add_label(ident, HOLD_LABEL)
        flagged.add(ident)
        print(f"watchdog: {ident} in Planning flagged (no-classification)")
    return flagged


# Human-park dispatch gate (DRE-2024). The PR backstops below dispatch
# agent-fix from PR state alone (DIRTY / approved-but-red / dead-fix-run),
# blind to the card — so a card the fix loop had already escalated to
# needs-human / Triage kept getting the identical doomed run every
# sweep (DeltaSolv PR #120 / DRE-2009: five max-turns deaths in one evening,
# runs 29115842272×2, 29122046329, 29125603420, 29128546908). Human-parked
# means the loop is over until a human acts: look the card up by the DRE-N
# in the head ref and skip the dispatch. Same family as the medic↔critic
# loop-break (bureau-pipeline #50).
#
# There are TWO human queues, and this gate asks the one question they both
# answer yes to: does a person owe this card an action before automation may
# act again? **Triage** holds a card that went WRONG — unroutable, held,
# bounced by the readiness guard (DRE-2723). **Green Light** holds a card that
# is waiting on a JUDGEMENT: an epic's plan awaiting approval, and — since
# DRE-2776 — an agent's escalate-by-exception question. Different reasons, same
# consequence for the loop: it is over until the human acts.
#
# Recognising only one of them is silent, and the failure is a bill: every
# doomed fix run this gate exists to stop dispatches again, every sweep,
# forever (DeltaSolv PR #120 / DRE-2009). So both lanes are declared here, once,
# and `card_parked_for_human` tests membership rather than equality.
PARKED_STATE = "Triage"
ESCALATED_STATE = "Green Light"
PARKED_STATES = (PARKED_STATE, ESCALATED_STATE)
_BRANCH_CARD = re.compile(r"DRE-\d+", re.IGNORECASE)


# --- branch ownership: ONE definition, three named questions (DRE-2426) ------
#
# "Does automation touch this branch?" used to be hand-written eight times
# across reconcile.py, merge-gate.yml, agent-fix.yml and linear-sync.yml — and
# the eight copies gave FOUR different answers. On 2026-08-12 four PRs
# (agent-bureau #2035/#2036/#2041, portico #270) sat up to ten hours holding
# real REQUEST_CHANGES verdicts because every layer skipped hand-named
# branches independently, INCLUDING this sweep, which exists to catch exactly
# that. The operator found them by eye, twice.
#
# Three questions, deliberately NOT collapsed into one predicate: merging them
# would silently widen each caller. `unstick_conflicts` must never hand a
# dependabot PR to the fix agent (no card to work, and Dependabot recreates
# its own conflicted PRs), so the narrow questions stay narrow. What changed
# is that each one is now NAMED and defined here, instead of spelled out at
# the call site where the next sweep copies it.
#
# `bot/standards-sync` is the nightly dreadnought-standards regeneration
# (DRE-2777). The merge gate merges it, so the broad question must say so —
# otherwise the sweep tells a PR "nothing is coming" on a branch the gate is
# actively merging. It stays OUT of the two narrow tuples for the same reason
# `dependabot/` does: no card to work, and no fix agent to hand it to. It is
# the LITERAL branch, not a `bot/` prefix — auto-merge is not a permission any
# future `bot/…` branch should inherit by name alone.
CARD_BRANCH_PREFIXES = ("agent/",)
FIX_BRANCH_PREFIXES = ("agent/", "repair/")
PIPELINE_BRANCH_PREFIXES = (
    "agent/", "repair/", "dependabot/", "bot/standards-sync",
)


def _has_prefix(head_ref: str | None, prefixes: tuple[str, ...]) -> bool:
    return (head_ref or "").lower().startswith(prefixes)


def card_branch(head_ref: str | None) -> bool:
    """A card's OWN branch (`agent/…`) — the narrowest question, and the one
    every sweep in this file used to ask inline."""
    return _has_prefix(head_ref, CARD_BRANCH_PREFIXES)


def fix_eligible(head_ref: str | None) -> bool:
    """A branch `agent-fix.yml` will act on: `agent/` or `repair/`."""
    return _has_prefix(head_ref, FIX_BRANCH_PREFIXES)


def pipeline_owns(head_ref: str | None) -> bool:
    """Will ANY automation touch this branch — merge gate, fix loop, sync?

    The broad question, asked by :func:`flag_unowned_prs`. False means the PR
    is invisible to the entire pipeline: no merge gate, no fix agent, no
    automatic Done, and — before DRE-2426 — no sweep to notice either.
    """
    return _has_prefix(head_ref, PIPELINE_BRANCH_PREFIXES)


#: Lines where the branch test may legitimately be spelled out, for the AST
#: guard in tests/test_pipeline_ownership.py. Empty on purpose: `_has_prefix`
#: uses a tuple, not a literal, so nothing needs an exemption. A new entry
#: here should be argued for, not added to make a test pass.
PIPELINE_OWNS_DEFINITION_LINES: frozenset[int] = frozenset()


def branch_card(head_ref: str) -> str | None:
    """The DRE-N a head ref carries (upper-cased), or None (repair/* etc.)."""
    m = _BRANCH_CARD.search(head_ref or "")
    return m.group(0).upper() if m else None


def card_parked_for_human(identifier: str) -> bool:
    """True if the card sits in either human queue (:data:`PARKED_STATES`) OR
    carries HOLD_LABEL — a person owes it an action either way, so no fix agent
    may be dispatched for its PR. Fails SAFE on an unreadable card: treat as
    parked (skip this sweep; the next one retries) rather than dispatch into a
    possibly-parked card."""
    try:
        issue = linear_ops.gql(
            """query($id: String!) { issue(id: $id) {
                 state { name } labels { nodes { name } } } }""",
            {"id": identifier},
        )["issue"] or {}
    except Exception as e:  # noqa: BLE001 — any Linear/transport error -> fail safe
        print(f"park-gate: could not read {identifier}: {e} — skipping dispatch")
        return True
    if ((issue.get("state") or {}).get("name")) in PARKED_STATES:
        return True
    return any(
        (lbl.get("name") or "").lower() == HOLD_LABEL
        for lbl in (issue.get("labels") or {}).get("nodes", [])
    )


def fix_dispatch_blocked(pr: dict) -> bool:
    """True when the PR's card is human-parked and the caller must NOT
    dispatch agent-fix for it. A ref with no card (repair/*, experiments)
    has no park state to consult and never blocks."""
    card = branch_card(pr.get("headRefName") or "")
    if card and card_parked_for_human(card):
        print(
            f"park-gate: PR #{pr['number']} card {card} is human-parked "
            f"({' / '.join(PARKED_STATES)} / {HOLD_LABEL}) — not dispatching agent-fix"
        )
        return True
    return False


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

    Attribution and the newest-wins rule come from card_pr, the ONE predicate
    the run's own Report step now shares (DRE-2316) — this sweep and that step
    disagreeing about whether a card has a PR is the bug that requeued
    DRE-2316 onto a second agent.
    """
    # baseRefName: the content binding (DRE-2340) compares base...head —
    # without it verdict_bound has nothing to compare and the carry never
    # fires, so the In Review sweep would re-review every carried head.
    fields = "number,url,headRefName,state,comments,headRefOid,baseRefName"

    def newest_match(out: str) -> dict | None:
        return card_pr.newest([
            pr for pr in json.loads(out or "[]")
            if card_pr.matches_card(pr.get("headRefName"), identifier)
        ])

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
        # None = unreadable (the App token 403s here): the run is NOT provably
        # concluded, so it stays "alive" and no dead-run retry fires off a
        # fabricated answer. Recorded by gh_actions_read, so the sweep is red.
        status = gh_actions_read("api", f"repos/{REPO}/actions/runs/{m.group(1)}",
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
    suppress the In Review re-review nudge (card stalls in In Review) or read as
    APPROVE to the approved-but-red sweep (spurious agent-fix dispatches).
    Merge was never at risk — merge-gate enforces authorship itself
    (DRE-1987) — this closes the stall/waste vector.

    Login shape: reconcile's comments come from `gh pr list --json
    comments` (GraphQL-backed), where a GitHub App's author.login carries
    NO "[bot]" suffix — "agent-bureau-qa-bot", unlike the REST user.login
    "agent-bureau-qa-bot[bot]" merge-gate reads. The suffix is stripped
    before comparing so either payload shape matches; a literal
    "agent-bureau-qa-bot[bot]" compare would match NOTHING here and wedge
    every In Review card in review churn.

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


def has_verdict(
    pr: dict,
    head_content_id: str | None = None,
    pr_commit_shas=frozenset(),
) -> bool:
    """True iff the latest qa-bot-authored QA Critic comment is a verdict
    that BINDS the PR's current head — by SHA (`@<full-sha>`, DRE-1990) or,
    when the head has moved but the PR's own contribution has not, by
    CONTENT (DRE-2340). Forged/non-qa-bot comments are invisible
    (DRE-1998).

    An unbound verdict (legacy/neutral comment with no SHA) or one bound to
    an older commit whose content no longer matches is NOT a verdict:
    merge-gate ignores those fail-closed, so nudging merge-gate would spin
    forever. Returning False routes the In Review re-nudge to qa-review
    instead, producing a fresh, bound verdict — this is also the automatic
    one-time re-review path for APPROVEs posted before DRE-1990 shipped.

    `head_content_id` is the id of the PR's own contribution at its current
    head (head_content_id_for), and `pr_commit_shas` the sha set of the PR's
    own commit record (pr_commit_shas_for) — together the four conditions
    merge_gate.carries_content applies, applied BY that function. Omitted =
    SHA binding only, which is exactly the pre-DRE-2340 answer.

    Both records, not just the content id (the review finding on this PR):
    a sweep that carried on content alone disagreed with the gate on any
    content-preserving head rewrite — the gate refused the carry for want
    of condition 4, so it waited for a fresh review, while this sweep read
    the PR as healthy and nudged the GATE instead of qa-review. Nothing
    ordered the review, every 15 minutes, forever. Applying the same four
    conditions routes that state back to qa-review, and one fresh review
    unsticks it with no human in the loop.

    The binding itself is read through merge_gate / verdict_content — trap
    4: there is ONE implementation, and DRE-1998 already had to fix an
    independent copy of a verdict read once.
    """
    bodies = critic_comment_bodies(pr)
    if not bodies:
        return False
    line = merge_gate.first_line(bodies[-1])
    sha = merge_gate.verdict_sha(line)
    if not sha:
        return False
    if sha == (pr.get("headRefOid") or ""):
        return True
    return merge_gate.carries_content(
        line, sha, head_content_id, pr_commit_shas
    )


def head_content_id_for(pr: dict) -> str | None:
    """The content id of the PR's own contribution at its current head, or
    None when it cannot be proved (DRE-2340).

    Costs one compare read, so callers ask for it ONLY when the cheap SHA
    binding has already failed — the common case (a verdict bound to the
    head) spends nothing. `baseRefName` must be in the PR listing's --json
    fields or there is nothing to compare against and the carry silently
    never fires; verdict_bound() is the paired reader.
    """
    base = pr.get("baseRefName")
    head = pr.get("headRefOid")
    if not base or not head:
        return None
    raw = gh("api", f"repos/{REPO}/compare/{base}...{head}")
    if not raw:
        return None  # unreadable — never act on fabricated data (DRE-2034)
    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    return verdict_content.content_id(payload)


def pr_commit_shas_for(pr: dict) -> frozenset:
    """The sha set of the PR's own commit record — the carry's condition 4,
    read exactly as merge-gate.yml reads it (DRE-2340).

    Costs one read, and like head_content_id_for it is paid ONLY on the
    stale-sha path that already pays a compare read; the common case (a
    verdict bound to the head) spends nothing. An unreadable record yields
    the empty set, which carries_content refuses — fail closed, and the
    sweep then routes the nudge to qa-review (a fresh review), never to a
    gate that would wait for one nobody ordered.
    """
    number = pr.get("number")
    if not number:
        return frozenset()
    raw = gh("api", f"repos/{REPO}/pulls/{number}/commits?per_page=100")
    if not raw:
        return frozenset()  # unreadable — never act on fabricated data
    try:
        payload = json.loads(raw)
    except ValueError:
        return frozenset()
    return merge_gate.commit_shas(payload)


def verdict_bound(pr: dict) -> bool:
    """has_verdict, resolving the content binding from GitHub's own compare
    and commit records when the SHA binding is stale (DRE-2340).

    Without this the sweep would fight the gate: after a base merge into
    the branch (the fix agent reconciling a conflict; before DRE-2416, the
    gate's own `update-branch`) the standing verdict still binds the PR's
    content, the gate is about to merge on it, and reconcile would spend a
    re-review (In Review nudge) or report a false "a fresh review is needed" on
    the card (crashed-review recovery) roughly every 15 minutes.

    Both records are resolved because the gate applies both; a sweep that
    honoured a carry the gate refuses reports a stalled PR as healthy (see
    has_verdict). The content id is resolved first and short-circuits: no
    id means no carry under any commit record, so the second read is never
    paid on the common truncated/blipped compare."""
    if has_verdict(pr):
        return True
    head_content_id = head_content_id_for(pr)
    if not head_content_id:
        return False
    return has_verdict(pr, head_content_id, pr_commit_shas_for(pr))


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
    """EVERY Backlog card, not the first page of them (DRE-2681).

    promote_ready() picks its candidates from this list, so an unpaginated page
    made "every Backlog card" mean "the first 100 Linear happened to return" —
    126 of the 226 cards on the 2026-08-26 census were not promotion
    candidates, and nothing said so.
    """
    return linear_ops.gql_paged(
        """query($after: String) {
           issues(first: 100, after: $after, filter: {
             team: {key: {eq: "DRE"}},
             state: {name: {eq: "Backlog"}}
           }) { nodes {
             id identifier title description createdAt
             parent { identifier state { name } }
             labels { nodes { name } }
             comments(last: 50) { nodes { body } }
             inverseRelations(first: 20) { nodes {
               type issue { identifier state { name } }
             } }
           } pageInfo { hasNextPage endCursor } } }"""
    )


def card_state(identifier: str) -> str:
    data = linear_ops.gql(
        "query($id: String!) { issue(id: $id) { state { name } } }", {"id": identifier}
    )
    return data["issue"]["state"]["name"]


def blockers_of(card: dict) -> set[str]:
    """Every live blocker of `card`: its non-terminal formal `blocks` relations,
    plus every DRE-N on a description line that DECLARES a dependency
    (`_BLOCKER_LINE` — a mention is not a declaration; DRE-2670)."""
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

    Logs WHICH KIND of blocker holds the epic (DRE-2670): a formal relation and
    a description line that no relation corroborates are different facts with
    different fixes, and "this epic is frozen by its own documentation" is the
    one nobody ever noticed — the sweep stayed green while it printed.
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
    for blocker in sorted(blockers_of(epic)):  # sorted: a deterministic log line
        if blocker in relation_blockers:
            print(
                f"epic-gate: {epic_identifier} is held by a formal blockedBy "
                f"relation on {blocker}, which is not Done"
            )
            return True  # relation blocker, already known non-terminal
        # A description-line blocker ("Blocked by: DRE-N"): state unknown, fetch.
        try:
            state = card_state(blocker)
        except linear_ops.LinearError as e:
            # A reference that doesn't resolve (typo'd id) must not kill the
            # sweep (DRE-2035) — same fail-safe as unreadable relation data:
            # treat the epic as blocked, loudly, and keep sweeping.
            print(
                f"epic-gate: blocker reference {blocker} on {epic_identifier} "
                f"doesn't resolve ({e}) — treating epic as blocked"
            )
            return True
        if state not in ("Done", "Canceled", "Duplicate"):
            # PROSE ONLY: a description line declares this blocker and no formal
            # relation corroborates it. The gate still honors it (prose has been
            # load-bearing since DRE-1233), but it says so — an epic frozen by
            # its own documentation should be readable as exactly that, not as
            # an ordinary dependency (DRE-2670).
            print(
                f"epic-gate: {epic_identifier} is held by PROSE ONLY — a description "
                f"line declares {blocker} ({state}) a blocker and no formal blockedBy "
                "relation corroborates it; set the relation or reword the line"
            )
            return True
    return False


def advance_unblocked_epics(done_epic: str) -> None:
    """When epic `done_epic` reaches Done, pull the next epics in the chain into
    the pipeline (DRE-1772, auto-advance).

    For each epic that `done_epic` `blocks` (its forward `relations`): if ALL of
    that epic's own blocker epics are now Done AND it is still in Backlog, move
    it to **Triage** (which triggers the planner). NEVER to In Progress — the
    Green Light approval gate stays human-owned. Idempotent and safe:
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


def skip_bad_reference(identifier: str, err: Exception) -> None:
    """Record a card the gate could not evaluate (a blocker reference that
    doesn't resolve, or any other per-card Linear failure): a loud ERROR line,
    the sweep-level skip counter, and ONE plain-English comment on the card
    across repeated sweeps (keyed on BAD_REF_TAG, the DEAD_TAG pattern).
    Reporting must never kill the sweep, so the comment path is itself guarded
    (DRE-2035)."""
    _card_skips.append(f"{identifier}: {err}")
    print(
        f"ERROR: {identifier} skipped — blocker reference doesn't resolve: {err}",
        file=sys.stderr,
    )
    try:
        if linear_ops.count_comments(identifier, BAD_REF_TAG) == 0:
            linear_ops.cmd_comment(
                identifier,
                f"🚨 {BAD_REF_TAG}: a blocker reference on this card doesn't "
                "resolve in Linear (likely a typo'd card id on a blocker "
                "line) — fix the reference. The reconcile sweep skips this "
                "card until then; the rest of the fleet is unaffected.",
            )
    except Exception as e:  # noqa: BLE001 — reporting never blocks the sweep
        print(f"ERROR: could not post skip comment on {identifier}: {e}", file=sys.stderr)


def promote_ready(active_count: int) -> int:
    """Auto-promote Backlog cards whose blockers are all Done.

    Two gates, because there are two ways a card can have been approved
    (DRE-2735). A CHILD is gated on its parent epic's state: a human moved that
    epic, and that decision covers everything under it (DRE-1893). A PARENTLESS
    card — the one-off, which goes straight to Backlog and which nothing
    escalates by design — has no approval to inherit, so its VERDICT is the
    approval, written at Planning exit. Refusing it for want of a parent left
    the most common thing anyone files sitting in Backlog forever.
    """
    budget = MAX_WIP - active_count
    if budget <= 0:
        print(f"promotion: WIP at cap ({active_count}/{MAX_WIP}) — none promoted")
        return 0
    promoted = 0
    parentless = 0  # of `promoted` — the new path, reported rather than inferred
    # Cache the epic-level gate per parent epic: it is the same answer for every
    # child of that epic, so consult Linear once per epic per sweep (DRE-1772).
    epic_gate: dict[str, bool] = {}
    # Same shape, same reason, for the epic's green light (DRE-2739) — one
    # history read per epic per sweep, shared by all its children.
    green_light: dict[str, str | None] = {}
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
        if parent and parent["state"]["name"] not in EPIC_ACTIVE_STATES:
            # DRE-1893 unchanged: a child must not build while its epic is
            # unapproved, whatever verdict the child itself carries. Said out
            # loud so the sweep log distinguishes this from the parentless
            # refusal below — different facts, different next actions.
            print(
                f"promotion: {card['identifier']}'s epic {parent['identifier']} is "
                f"not active ({parent['state']['name']}) — skipping"
            )
            continue
        # Per-card isolation (DRE-2035): everything from here on reads Linear
        # per THIS card — a blocker reference that doesn't resolve raises
        # LinearError, which must skip this one card (loudly, with a one-time
        # comment), never kill the gate for the rest of the fleet.
        # The read-guard covers ONLY the gate-evaluation reads: a LinearError
        # here means a blocker reference doesn't resolve, so this one card is
        # skipped as unevaluable (DRE-2035). The mutations that follow are
        # DELIBERATELY outside it — a write failure there is a write failure,
        # not a bad reference, and must not stamp the card with a false
        # "reference doesn't resolve" diagnostic (critic PR #89).
        # Two refusals can hold a card here, each with its own idempotency tag
        # so one does not silence the other's notice (DRE-2739, DRE-2724).
        refusal: str | None = None
        refusal_tag = mid_epic.NO_VERDICT_TAG
        try:
            # Epic-level gate (DRE-1772): even an active (plan-approved) epic
            # must not start its children while the epic itself is blocked-by a
            # prerequisite epic that has not shipped. Composes with the
            # card-level gate, MAX_WIP, and the DRE-1585 agent-blocker guard
            # below. A parentless card has no epic to gate on — asking Linear
            # about one would be a query about a card that does not exist.
            epic_id = parent["identifier"] if parent else None
            if epic_id is not None:
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
            # Mid-epic discovery (DRE-2739): a card added to an epic AFTER it was
            # green-lit dispatches an agent on this very sweep — within fifteen
            # minutes — whether or not anyone has read it. Right for a card the
            # plan anticipated, wrong for one nobody has seen. So it carries a
            # verdict before it joins: Layer 1 is not waived, only the green
            # light is. An epic whose green light Linear cannot report abstains
            # (mid_epic.promotion_refusal) — never refuses the whole roster.
            # There is no green light to read for a card with no epic.
            bodies = [
                n.get("body") or ""
                for n in (card.get("comments") or {}).get("nodes", [])
            ]
            if epic_id is not None:
                if epic_id not in green_light:
                    green_light[epic_id] = mid_epic.last_green_light(linear_ops, epic_id)
                refusal = mid_epic.promotion_refusal(
                    card["identifier"],
                    card.get("createdAt"),
                    green_light[epic_id],
                    bodies,
                )
            # Routing verdict (DRE-2724): the verdict answers WHO builds this
            # card, and only FLEET means "an unattended agent, in one pull
            # request". WORKBENCH needs an interactive flow, OPERATOR is not
            # code at all, PARKED is deliberately not built, NEEDS WORK is not
            # buildable as written — dispatching any of them sends the fleet at
            # a card it cannot close. A CHILD with NO verdict promotes exactly as
            # before: Backlog's "it carries a verdict" clause is enforced from
            # Phase 5, and refusing the whole verdictless board today would
            # freeze it rather than route it.
            #
            # A PARENTLESS card is the one case where no verdict is itself the
            # refusal (DRE-2735): a child inherits its epic's approval and this
            # one has none to inherit, so the verdict IS the approval.
            if refusal is None:
                refusal = (
                    routing_verdict.promotion_refusal(card["identifier"], bodies)
                    if epic_id is not None
                    else routing_verdict.parentless_promotion_refusal(
                        card["identifier"], bodies
                    )
                )
                if refusal is not None:
                    refusal_tag = routing_verdict.refusal_tag(refusal)
        except linear_ops.LinearError as e:
            skip_bad_reference(card["identifier"], e)
            continue
        if refusal is not None:
            print(
                f"promotion: {card['identifier']} is not being promoted — "
                f"{refusal.splitlines()[0]}"
            )
            # Surfaced ONCE, the DEAD_TAG shape: the card is invisible otherwise,
            # and an invisible refusal is the silent-accretion problem wearing a
            # different hat. A WRITE, so deliberately outside the read-guard —
            # and guarded itself, because reporting never blocks the sweep.
            try:
                if linear_ops.count_comments(card["identifier"], refusal_tag) == 0:
                    linear_ops.cmd_comment(card["identifier"], refusal)
            except linear_ops.LinearError as e:
                print(
                    f"ERROR: could not surface the promotion refusal on "
                    f"{card['identifier']}: {e}",
                    file=sys.stderr,
                )
            continue
        # Gate passed — now mutate. A LinearError here is a WRITE failure, not a
        # bad reference: record it on the existing _write_failures path (fails
        # the run red for medic) instead of the bad-reference diagnostic.
        try:
            linear_ops.cmd_advance(card["identifier"], "Todo", "Backlog")
            # The receipt names what actually approved this card. "parent epic
            # active" on a card with no parent would be a confident wrong
            # answer about the one thing the reader is asking.
            linear_ops.cmd_comment(
                card["identifier"],
                "🧹 Auto-promoted Backlog → Todo: parent epic active and all blockers Done."
                if parent
                else "🧹 Auto-promoted Backlog → Todo: no parent epic, a FLEET "
                     "verdict, and all blockers Done.",
            )
        except linear_ops.LinearError as e:
            _write_failures.append(f"{card['identifier']} advance/comment: {e}")
            print(
                f"ERROR: failed to advance/comment on {card['identifier']}: {e}",
                file=sys.stderr,
            )
            continue
        promoted += 1
        if not parent:
            parentless += 1
    # The parentless count is printed on EVERY sweep, zero included: the new
    # path is meant to be visible rather than inferred from a total, and "no
    # line" and "none promoted" must not render the same (DRE-2735).
    print(
        f"promotion: {promoted} card(s) promoted, {parentless} parentless "
        f"one-off(s) (WIP {active_count}+{promoted}/{MAX_WIP})"
    )
    if _card_skips:
        # A red pattern the run log can't miss (DRE-2035) — pairs with the
        # per-card ERROR lines above; the sweep itself stays alive and green.
        print(
            f"promotion: {len(_card_skips)} card(s) SKIPPED on unresolvable "
            "blocker references — see ERROR lines above",
            file=sys.stderr,
        )
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


# UNKNOWN-mergeable polling bounds (DRE-2121). GitHub computes a PR's
# mergeable state LAZILY, so the merge-triggered sweep must wait out the
# recompute — but bounded, so linear-sync stays fast. 3 × 20s covers the
# recompute comfortably; anything slower belongs to the cron backstop.
CONFLICT_POLL_TRIES = 3
CONFLICT_POLL_SECONDS = 20


def _dispatch_conflict_fix(pr: dict) -> None:
    """Dispatch the fix agent for one DIRTY agent PR (human-park gated)."""
    if fix_dispatch_blocked(pr):
        return  # human-parked card (DRE-2024) — the loop is over
    print(f"conflict: PR #{pr['number']} ({pr['headRefName']}) DIRTY — dispatching fix agent")
    gh_dispatch("workflow", "run", fix_workflow(), "--repo", REPO,
                "-f", f"pr_number={pr['number']}")


def unstick_conflicts() -> None:
    """A conflicted (DIRTY) PR emits no workflow events at all — GitHub
    cannot build its test-merge commit, so pull_request workflows silently
    never run, and the merge gate's DIRTY path (which fires on those very
    events) never gets a chance. This sweep is the backstop: dispatch the
    fix agent for any open agent PR sitting in conflict. (Origin: PR #25 /
    DRE-1218 sat 35 minutes with pushes firing nothing.)

    UNKNOWN is "not yet computed", never "not dirty" (DRE-2121). GitHub
    computes mergeable lazily, so the merge-triggered sweep (linear-sync
    fires this seconds after a merge — the exact event that conflicts
    siblings) races the recompute: bp#109 merged 00:56:57, the sweep listed
    at 00:57:10, #110 read UNKNOWN, and the sweep honestly found nothing
    DIRTY while the conflicted PR sat 17 minutes for a hand-nudge. Reading
    a PR triggers the recompute, so agent PRs reading UNKNOWN are re-read
    (bounded: CONFLICT_POLL_TRIES × CONFLICT_POLL_SECONDS) until each
    resolves, then acted on — at most one dispatch per PR per invocation.
    Still UNKNOWN at the cap: log loudly and leave it to the cron backstop.
    """
    # Unreadable answers BUSY (gh_actions_read): the App token 403s on this
    # API, and the old `or "[]"` turned that into "nothing running" — the
    # backoff failed OPEN at every one of these sites.
    if _actions_runs_busy(fix_workflow()):
        print("conflict sweep: fix agent busy — retry next sweep")
        return
    prs = json.loads(gh(
        "pr", "list", "--repo", REPO, "--state", "open", "--limit", "30",
        "--json", "number,headRefName,mergeStateStatus",
    ) or "[]")
    pending = []  # agent PRs whose mergeable GitHub hasn't computed yet
    for pr in prs:
        if not card_branch(pr["headRefName"]):
            continue
        status = pr.get("mergeStateStatus")
        if status == "UNKNOWN":
            pending.append(pr)
        elif status == "DIRTY":
            _dispatch_conflict_fix(pr)
    for attempt in range(1, CONFLICT_POLL_TRIES + 1):
        if not pending:
            return
        print(
            f"conflict sweep: {len(pending)} PR(s) mergeable UNKNOWN — GitHub "
            f"is still computing; re-read {attempt}/{CONFLICT_POLL_TRIES} "
            f"in {CONFLICT_POLL_SECONDS}s"
        )
        time.sleep(CONFLICT_POLL_SECONDS)
        still_unknown = []
        for pr in pending:
            fresh = json.loads(gh(
                "pr", "view", str(pr["number"]), "--repo", REPO,
                "--json", "number,headRefName,mergeStateStatus",
            ) or "{}")
            status = fresh.get("mergeStateStatus")
            if status == "DIRTY":
                _dispatch_conflict_fix(fresh)
            elif status in (None, "UNKNOWN"):
                # Unreadable counts as unresolved — keep polling, never guess.
                still_unknown.append(fresh or pr)
        pending = still_unknown
    if pending:
        nums = ", ".join(f"#{p['number']}" for p in pending)
        print(
            f"ERROR: conflict sweep: mergeable still UNKNOWN after "
            f"{CONFLICT_POLL_TRIES}×{CONFLICT_POLL_SECONDS}s for {nums} — "
            "leaving to the cron backstop",
            file=sys.stderr,
        )


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
        if not card_branch(pr["headRefName"]) or pr.get("mergeStateStatus") == "DIRTY":
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


# No-checks watchdog (DRE-2261). A PR the pipeline cannot see: 30 minutes,
# the same reasoning as WATCHDOG_MINUTES — long enough for GitHub's check
# spin-up lag and the 15-minute lost-event re-push (retrigger_dead_heads) to
# get their chance first, short enough that a human hears within a sweep or
# two instead of the five hours DRE-2180 sat / thirteen days portico PR #21
# sat. False positive cost is one comment on the card, nothing else.
NO_CHECKS_MINUTES = int(os.environ.get("NO_CHECKS_MINUTES", "30"))
NO_CHECKS_TAG = "no-checks-watchdog"

# --- unowned-branch watchdog (DRE-2426) --------------------------------------
# A PR on a hand-named branch gets NO merge gate, NO fix agent and NO automatic
# Done — and, until this sweep, no notice either, because every backstop in
# this file skipped it exactly the same way. Four PRs sat up to ten hours like
# that on 2026-08-12 holding real REQUEST_CHANGES verdicts; the operator found
# them by eye. Noticing is not fixing: this reports and does nothing else.
#
# Two hours, not thirty minutes: a hand-named branch is a legitimate choice
# (cardless hotfixes live there), so the sweep waits until the PR has plainly
# stopped moving rather than greeting every push. The cost of a false positive
# is one comment; the cost of firing too eagerly is a warning nobody reads,
# which is the failure this whole card is about.
UNOWNED_MINUTES = int(os.environ.get("UNOWNED_MINUTES", "120"))
UNOWNED_MARKER = "unowned-branch-watchdog"

# --- unlanded-work watchdog (DRE-2682) ---------------------------------------
# Every gate in this pipeline keys off a PULL REQUEST — CI, the critic, the
# merge gate and linear-sync all read one — so a pushed branch that never
# became a PR is outside the system entirely. On 2026-08-22 that is exactly
# where DRE-2655's finished work sat: three files, tests green, pushed to
# `agent/DRE-2655-drift-count-out-of-the-pill`, for NINETEEN HOURS, while the
# card read "In Progress" — true, and carrying no information. An operator
# found it by eye. The branch was named perfectly and that bought nothing:
# every gate matches the head ref OF A PULL REQUEST, so the naming convention
# only starts paying once one exists.
#
# The unowned-branch watchdog above covers the MIRROR case (a PR on a branch
# the pipeline does not own). This covers the branch that never became a PR —
# and the hand-built card with nothing to point at at all, which is the alarm
# that REPLACES what HAND_BUILT_LABEL suppresses. DRE-2524 correctly silenced
# flag_stranded for hand-built work and left nothing measuring the HAND thing
# (no branch, or a branch with no PR) in place of the FLEET thing (in a
# working lane with no dispatched run, which for hand-built work is normal).
#
# Alert-only, like both backstops above: one comment per branch (or per card
# for the no-branch half), no state move, no hold label — the hold label would
# stand down repairs and block promotion forever, and noticing is not parking.
#
# AUTHORSHIP IS NEVER THE SIGNAL. DRE-2655's commits were authored
# `agent-bureau-bot[bot]` and were written by hand; DRE-2694's hand-built work
# wore the operator's identity. Git authorship misleads in BOTH directions, so
# nothing here reads it: the facts are the branch, the pull request, and the
# card's own label.
#
# One hour, not thirty minutes: pushing a branch and opening its PR are two
# deliberate acts a person does minutes apart, and an agent run that pushes
# opens the PR in the same run — so an hour of a branch with no PR is already
# a stall, while anything shorter greets a normal push.
UNLANDED_MINUTES = int(os.environ.get("UNLANDED_MINUTES", "60"))
# Longer again for the no-branch half: what a hand-built card owes is a
# person's own work, and a person who picked a card up an hour ago is not
# late. The clock only runs on a card nothing is happening to — every comment
# on it bumps updatedAt.
HAND_IDLE_MINUTES = int(os.environ.get("HAND_IDLE_MINUTES", "180"))
UNLANDED_TAG = "unlanded-work-watchdog"

#: REPO's default branch, read at most once per sweep (see default_branch()).
_default_branch: str | None = None


def open_prs(limit: int = 50) -> list[dict]:
    """Open PRs with the fields the watchdogs need.

    Uses the SILENT ``gh`` with an empty-list fallback, the same shape every
    sibling PR-level backstop uses and the case ``gh``'s own docstring blesses:
    an unreadable listing means this sweep reports nothing, not that it invents
    a finding. The sweep only ever ADDS a notice, so a blip costs one delayed
    warning — and the next sweep is fifteen minutes away.
    """
    return json.loads(gh(
        "pr", "list", "--repo", REPO, "--state", "open", "--limit", str(limit),
        "--json", "number,headRefName,isDraft,updatedAt,comments",
    ) or "[]")


def comment_on_pr(number: int, body: str) -> None:
    """One comment on a PR, through the write path that records failures."""
    gh("pr", "comment", str(number), "--repo", REPO, "--body", body)


def _suggested_rename(ref: str, card: str | None) -> str:
    """The `agent/…` name that would put this PR back on the rail.

    The card id is taken OUT of the ref's tail before it is put back in front:
    `fix/DRE-2405-dev-loads-ws-ingest` (agent-bureau #2041, one of the four
    strandings) must suggest `agent/DRE-2405-dev-loads-ws-ingest`, not the
    doubled `agent/DRE-2405-DRE-2405-dev-loads-ws-ingest` a blind recombination
    produces. A ref that NAMES a card in the wrong position is precisely the
    case this notice exists for, so a suggestion that is itself still unowned
    would fail the one reader it was written for.
    """
    slug = ref.split("/", 1)[-1] if "/" in ref else ref
    if not card:
        return "agent/DRE-<n>-<slug>"
    # Only THIS card's id, anywhere in the tail and in any case — a second,
    # different DRE-N in the slug is someone's deliberate cross-reference.
    slug = re.sub(re.escape(card), "", slug, flags=re.IGNORECASE)
    slug = re.sub(r"[-_]{2,}", "-", slug).strip("-_")
    return f"agent/{card}-{slug}" if slug else f"agent/{card}-<slug>"


def _unowned_notice(pr: dict) -> str:
    ref = pr["headRefName"]
    card = branch_card(ref)
    want = _suggested_rename(ref, card)
    names = (
        f"This branch names **{card}**, but not in the position the pipeline reads."
        if card else
        "This branch carries no card id."
    )
    return (
        f"⚠️ `{UNOWNED_MARKER}` — nothing is coming for this PR.\n\n"
        f"`{ref}` is not an `agent/` branch, so the pipeline skips it at every "
        f"layer:\n\n"
        f"* **no merge gate** — it will never be merged automatically\n"
        f"* **no fix agent** — a REQUEST_CHANGES verdict here is nobody's work\n"
        f"* **no automatic Done** — the card stays open after you merge\n\n"
        f"{names} All three gates read the head ref, anchored: "
        f"`^agent/DRE-<n>-<slug>`.\n\n"
        f"Either rename to `{want}` and the pipeline takes over, or expect to "
        f"review, merge and close this by hand. Both are fine — this notice "
        f"exists so the choice is deliberate rather than discovered hours "
        f"later.\n\n"
        f"_Posted once per PR. Renaming the branch silences it._"
    )


def flag_unowned_prs(now: str | None = None) -> None:
    """DRE-2426 watchdog: report open PRs no automation will ever touch.

    The gap this closes is not that hand-named branches exist — they are a
    legitimate choice — but that choosing one was INVISIBLE. Every other
    backstop in this file asks ``card_branch()`` and moves on, so the PR that
    most needs a human is the one nothing mentions.
    """
    prs = open_prs()  # deliberately un-caught: fail closed, never "all clear"
    for pr in prs:
        try:
            _flag_one_unowned_pr(pr, now=now)
        except Exception as e:  # noqa: BLE001 — isolate one PR, sweep the rest
            _write_failures.append(
                f"unowned watchdog on PR #{pr.get('number')}: {e}"
            )
            print(
                f"ERROR: unowned watchdog on PR #{pr.get('number')}: {e}",
                file=sys.stderr,
            )


def _flag_one_unowned_pr(pr: dict, now: str | None = None) -> None:
    """Evaluate ONE open PR and report it if the pipeline owns nothing here."""
    if pipeline_owns(pr.get("headRefName")):
        return  # automation has it — every other backstop applies
    if pr.get("isDraft"):
        return  # a draft is work in progress, not a stranding
    updated = pr.get("updatedAt")
    if not updated or age_minutes(updated, now=now) < UNOWNED_MINUTES:
        return  # still moving; someone may be mid-push
    bodies = [c.get("body") or "" for c in (pr.get("comments") or [])]
    if any(UNOWNED_MARKER in b for b in bodies):
        return  # said once, and once is the point
    comment_on_pr(pr["number"], _unowned_notice(pr))
    print(
        f"unowned: PR #{pr['number']} ({pr['headRefName']}) has no owning "
        f"automation and has not moved in {UNOWNED_MINUTES}m — reported"
    )


def flag_no_checks_prs() -> None:
    """DRE-2261 watchdog: report an open agent PR the pipeline cannot see.

    A conflicted PR emits no workflow events at all — GitHub cannot build
    its test-merge commit, so CI, the critic, and the merge gate silently
    never run: no red check, no verdict, no failed run. unstick_conflicts
    is the repair, but it (correctly) stands down on a human-parked card
    (fix_dispatch_blocked, DRE-2024 — that gate STAYS), and nothing else
    ever looked: parking suppressed the repair without raising a hand.
    DRE-2180 sat that way five hours; portico PR #21 thirteen days.

    Noticing is not dispatching. This backstop only ALERTS — one comment on
    the linked card per PR (the NO_CHECKS_TAG + "PR #N:" marker is the
    idempotency key, the flag_stranded pattern), naming the PR, branch,
    mergeStateStatus, and how long it has been silent, plus the card's park
    state — reported parked or not, because parked IS the invisible case.
    Deliberately NO hold label: on an unparked card that label would stand
    down unstick_conflicts and CREATE the very invisibility this alarms on.

    Checks merely queued/in progress mean the pipeline CAN see the PR — only
    a head commit with zero check runs at all counts. Unreadable check-run /
    commit reads skip the PR (DRE-2034: a 403 is not "zero checks"). The
    non-DIRTY zero-checks class normally never reaches the threshold —
    retrigger_dead_heads re-pushes at 15 minutes, which resets the head
    commit's clock here — so this fires exactly when every repair path has
    silently failed or been stood down.

    A watchdog must never take the sweep down with it: any unexpected
    per-PR (or listing) failure is recorded on the fail-loudly rail — red
    run, medic — and the sweep continues (the DRE-2035 isolation
    discipline, applied at the backstop level).
    """
    try:
        prs = json.loads(gh(
            "pr", "list", "--repo", REPO, "--state", "open", "--limit", "30",
            "--json", "number,headRefName,headRefOid,mergeStateStatus,isDraft",
        ) or "[]")
    except Exception as e:  # noqa: BLE001 — record loudly, never kill the sweep
        _write_failures.append(f"no-checks watchdog: PR listing failed: {e}")
        print(f"ERROR: no-checks watchdog: PR listing failed: {e}", file=sys.stderr)
        return
    for pr in prs:
        try:
            _flag_one_silent_pr(pr)
        except Exception as e:  # noqa: BLE001 — isolate the one PR, sweep the rest
            _write_failures.append(
                f"no-checks watchdog on PR #{pr.get('number')}: {e}"
            )
            print(
                f"ERROR: no-checks watchdog on PR #{pr.get('number')}: {e}",
                file=sys.stderr,
            )


def _flag_one_silent_pr(pr: dict) -> None:
    """Evaluate ONE open PR for flag_no_checks_prs and report if silent."""
    if not card_branch(pr["headRefName"]) or pr.get("isDraft"):
        return
    sha = pr.get("headRefOid") or ""
    if not sha:
        return
    statuses = gh("api", f"repos/{REPO}/commits/{sha}/check-runs",
                  "--jq", "[.check_runs[].status]")
    if not statuses:
        return  # unreadable — never alarm on a fabricated empty (DRE-2034)
    try:
        if json.loads(statuses):
            return  # check runs exist (completed or in flight) — visible
    except ValueError:
        return
    commit = json.loads(gh("api", f"repos/{REPO}/git/commits/{sha}") or "{}")
    when = (commit.get("committer") or {}).get("date")
    if not when or age_minutes(when) < NO_CHECKS_MINUTES:
        return  # fresh head — spin-up and the re-push repair go first
    card = branch_card(pr["headRefName"])
    if not card:
        print(
            f"WARNING: no-checks: PR #{pr['number']} "
            f"({pr['headRefName']}) carries no card id — nowhere to report"
        )
        return
    # ":"-terminated so #21's marker never substring-matches #210's.
    marker = f"{NO_CHECKS_TAG} PR #{pr['number']}:"
    if any(marker in b for b in linear_ops.comment_bodies(card)):
        return  # reported once already — idempotent forever
    park_note = (
        "The card is human-parked (waiting on a person in "
        f"{' or '.join(PARKED_STATES)}, or stamped {HOLD_LABEL}), so every "
        "automatic repair is standing down on purpose (DRE-2024) — the PR "
        "stays frozen until a human acts on the card."
        if card_parked_for_human(card) else
        "The card is NOT human-parked — the repair backstops should be "
        "acting; if this alert recurs, check the fix-agent rail."
    )
    silent = age_minutes(when)
    linear_ops.cmd_comment(card, (
        f"🚨 {marker} open PR #{pr['number']} "
        f"(branch {pr['headRefName']}, mergeStateStatus "
        f"{pr.get('mergeStateStatus')}) has had ZERO completed check "
        f"runs on its head commit for {silent:.0f} minutes — the "
        "pipeline cannot see it: no CI, no critic verdict, no merge "
        f"gate will ever fire on this commit. {park_note}"
    ))
    print(
        f"no-checks: PR #{pr['number']} ({pr['headRefName']}) silent "
        f"{silent:.0f}m — reported on {card}"
    )


def default_branch() -> str:
    """REPO's default branch, read at most once per sweep.

    Silent gh() with a `main` fallback: this helper HAS its own answer, and a
    wrong base only makes the compare below unreadable, which the caller
    already treats as "say nothing this sweep".
    """
    global _default_branch
    if _default_branch is None:
        _default_branch = gh("api", f"repos/{REPO}", "--jq", ".default_branch") or "main"
    return _default_branch


def card_branches() -> list[dict] | None:
    """Every `agent/DRE-*` branch head in REPO — `{name, sha}` each, or None
    when the listing is unreadable.

    Emitted as JSON LINES (a per-object `--jq`) rather than one array: gh
    `--paginate` concatenates one array PER PAGE, so a whole-array jq hands
    back a stream no json.loads can read — and the sweep would then see zero
    branches on exactly the busy repos this watchdog is for.

    Read LOUDLY (gh_read), and None — never [] — on failure. Those JSON lines
    are exactly why: an array listing betrays a failed read by parsing to
    nothing, but a per-object stream makes a 403 and a genuinely branchless
    repo byte-identical empty stdout. The silent gh() helper would hand both
    back as [], and _flag_hand_built_idle would then tell a person "no branch,
    no pull request" about a branch it simply could not see. Same discipline,
    same reason, as pr_head_refs below (DRE-2034).
    """
    try:
        out = gh_read(
            "api", f"repos/{REPO}/branches", "--paginate",
            "--jq", ".[] | {name: .name, sha: .commit.sha}",
        )
    except Exception as e:  # noqa: BLE001 — record loudly, never kill the sweep
        _write_failures.append(f"unlanded watchdog: branch listing failed: {e}")
        print(
            f"ERROR: unlanded watchdog: branch listing failed: {e}",
            file=sys.stderr,
        )
        return None
    found: list[dict] = []
    for line in (out or "").splitlines():
        try:
            branch = json.loads(line)
        except ValueError:
            continue  # a partial page or an error body — not a branch
        if not isinstance(branch, dict):
            continue
        name = branch.get("name") or ""
        if card_branch(name) and branch_card(name):
            found.append(branch)
    return found


def pr_head_refs() -> set[str] | None:
    """Head refs of every PR in ANY state, or None when the listing is unreadable.

    `--state all`, not `open`, is the load-bearing part: a squash-merged
    branch stays "ahead" of the default branch forever, so an open-only
    listing would report every merged branch that was not deleted. None
    (never an empty set) on an unreadable answer — a blip must not read as
    "nothing here has a pull request" (DRE-2034).
    """
    try:
        raw = gh(
            "pr", "list", "--repo", REPO, "--state", "all",
            "--limit", "100", "--json", "headRefName",
        )
    except Exception as e:  # noqa: BLE001 — record loudly, never kill the sweep
        _write_failures.append(f"unlanded watchdog: PR listing failed: {e}")
        print(f"ERROR: unlanded watchdog: PR listing failed: {e}", file=sys.stderr)
        return None
    try:
        prs = json.loads(raw) if raw else None
    except ValueError:
        prs = None
    if not isinstance(prs, list):
        return None
    return {pr.get("headRefName") or "" for pr in prs if isinstance(pr, dict)}


def flag_unlanded_work() -> None:
    """DRE-2682 watchdog: report work that never became a pull request.

    Two halves over one branch listing and one PR listing:

      (A) an `agent/DRE-*` branch carrying commits that are not on the default
          branch, with NO pull request ever opened for it — open, closed or
          merged — is reported on its card after UNLANDED_MINUTES. This is the
          hole DRE-2655 fell through: nineteen hours of finished work on a
          correctly-named branch that no gate could see.
      (C) a HAND-BUILT card in a working lane with nothing to point at — no
          card branch, no PR — is reported after HAND_IDLE_MINUTES
          (_flag_hand_built_idle). That is the alarm which replaces what the
          label suppresses; without it, routing work to a hand-built lane
          makes DRE-2655's failure mode more common, not less.

    Both halves only ever ADD a notice, so every unreadable answer means "say
    nothing this sweep" rather than "nothing has a PR": the PR listing, the
    BRANCH listing, the per-branch confirm, the compare and the card read each
    fail closed. A per-branch failure is recorded on the fail-loudly rail and
    the rest of the sweep continues (the DRE-2035 isolation discipline).
    """
    pr_refs = pr_head_refs()
    if pr_refs is None:
        print("unlanded: PR listing unreadable — reporting nothing this sweep")
        return  # fail closed: a blip is not "nothing has a pull request"
    branches = card_branches()
    if branches is None:
        print("unlanded: branch listing unreadable — reporting nothing this sweep")
        return  # fail closed: a blip is not "this card has no branch"
    for branch in branches:
        try:
            _flag_one_unlanded_branch(branch, pr_refs)
        except Exception as e:  # noqa: BLE001 — isolate one branch, sweep the rest
            _write_failures.append(
                f"unlanded watchdog on branch {branch.get('name')}: {e}"
            )
            print(
                f"ERROR: unlanded watchdog on branch {branch.get('name')}: {e}",
                file=sys.stderr,
            )
    _flag_hand_built_idle(branches, pr_refs)


def _flag_one_unlanded_branch(branch: dict, pr_refs: set[str]) -> None:
    """Evaluate ONE card branch and report it if no PR was ever opened."""
    name, sha = branch["name"], branch.get("sha") or ""
    if name in pr_refs or not sha:
        return  # a PR exists (any state) — every other backstop applies
    pulls = gh("api", f"repos/{REPO}/commits/{sha}/pulls", "--jq", "[.[].number]")
    if not pulls:
        return  # unreadable — a 403 is not "no PR was ever opened" (DRE-2034)
    try:
        if json.loads(pulls):
            return  # a PR older than the listing window — confirmed, not guessed
    except ValueError:
        return
    base = default_branch()
    raw = gh(
        "api", f"repos/{REPO}/compare/{base}...{name}",
        "--jq", "{ahead: .ahead_by, last: ([.commits[].commit.committer.date] | last)}",
    )
    try:
        compared = json.loads(raw) if raw else None
    except ValueError:
        compared = None
    if not isinstance(compared, dict):
        return  # unreadable — never alarm on a fabricated empty
    ahead, last = compared.get("ahead") or 0, compared.get("last")
    if ahead < 1 or not last:
        return  # nothing of its own on this branch — an old or empty ref
    idle = age_minutes(last)
    if idle < UNLANDED_MINUTES:
        return  # still moving; the PR may be seconds away
    card = branch_card(name)
    try:
        state = card_state(card)
    except Exception as e:  # noqa: BLE001 — any Linear/transport error -> defer
        print(f"unlanded: could not read {card}: {e} — deferring to a later sweep")
        return
    if state in structural_repair.TERMINAL_STATES:
        return  # a finished card's leftover branch is route F/I, not this alarm
    # Keyed on the BRANCH, not the card: a card whose first attempt was
    # reported must still speak when a second branch strands the same way.
    marker = f"{UNLANDED_TAG} branch {name}:"
    if any(marker in b for b in linear_ops.comment_bodies(card)):
        return  # said once, and once is the point
    linear_ops.cmd_comment(card, (
        f"🚨 {marker} the branch `{name}` carries {ahead} commit(s) that are "
        f"not on `{base}`, and no pull request has ever been opened for it — "
        f"so nothing in the pipeline can see this work. CI, the critic, the "
        f"merge gate and the Linear sync all key off a pull request; a branch "
        f"on its own passes through no gate at all and closes no card. Last "
        f"commit {idle:.0f} minutes ago.\n\n"
        f"Where it goes from here: open a pull request from `{name}` into "
        f"`{base}`. Hand-built work lands exactly the way dispatched work "
        f"does — the branch, then the pull request, then the same checks and "
        f"the same critic verdict. What changes is who writes the code, never "
        f"how it lands.\n\n"
        f"_Posted once per branch. Opening the pull request ends it._"
    ))
    print(
        f"unlanded: branch {name} has {ahead} commit(s), no PR, idle "
        f"{idle:.0f}m — reported on {card}"
    )


def _flag_hand_built_idle(branches: list[dict], pr_refs: set[str]) -> None:
    """The alarm that replaces what HAND_BUILT_LABEL suppresses (DRE-2682).

    A hand-built card in Todo / In Progress with NO card branch and NO pull
    request has nothing to point at: the board says work is happening and
    there is no artifact anywhere that agrees. flag_stranded cannot say so —
    it is silenced on this label by design and correctly, because the thing it
    measures (no dispatched run receipt) is normal here. So this measures what
    hand-built work actually owes, and says what is missing rather than
    calling the card "stalled".

    A card with a branch is the other half's business (it names the specific
    gap), a card with a PR belongs to the PR-level backstops, and a card
    without the label belongs to flag_stranded — one alarm per card, always
    the one that knows what is wrong.
    """
    with_branch = {branch_card(b["name"]) for b in branches}
    with_pr = {branch_card(ref) for ref in pr_refs if branch_card(ref)}
    for card in active_cards(WATCHDOG_LANES):
        ident = card["identifier"]
        try:
            if not hand_built(card):
                continue  # a dispatched card with no run is flag_stranded's case
            if held(card):
                continue  # already in a human's queue — never spam
            if card_repo(card) != REPO_SLUG:
                continue  # that repo's own sweep sees its own branches
            if ident in with_branch or ident in with_pr:
                continue  # there IS something to point at
            if age_minutes(card["updatedAt"]) < HAND_IDLE_MINUTES:
                continue  # a person picked this up recently — not a stall
            marker = f"{UNLANDED_TAG} no branch:"
            if any(marker in b for b in linear_ops.comment_bodies(ident)):
                continue  # reported once already — idempotent forever
            state, idle = card["state"]["name"], age_minutes(card["updatedAt"])
            linear_ops.cmd_comment(ident, (
                f"🚨 {marker} this card has sat in {state} for {idle:.0f} "
                f"minutes with nothing to point at: no branch, no pull "
                f"request. It is labelled '{HAND_BUILT_LABEL}', so no "
                f"dispatched run is coming by design — which also means the "
                f"stranded-card watchdog stays silent on it (DRE-2524). This "
                f"notice is what stands in its place.\n\n"
                f"What is missing, in order: a branch named "
                f"`agent/{ident}-<slug>`, then a pull request from it. "
                f"Hand-built work meets the same checks and the same critic "
                f"verdict as dispatched work — the only difference is who "
                f"writes the code. If the work is deliberately paused, say so "
                f"on the card, so the pause is a decision rather than a "
                f"silence.\n\n"
                f"_Posted once per card._"
            ))
            print(
                f"unlanded: {ident} is hand-built, in {state} {idle:.0f}m with "
                f"no branch and no PR — reported"
            )
        except Exception as e:  # noqa: BLE001 — isolate one card, sweep the rest
            _write_failures.append(f"unlanded watchdog on {ident}: {e}")
            print(f"ERROR: unlanded watchdog on {ident}: {e}", file=sys.stderr)


def fix_approved_but_red() -> None:
    """Dead-zone repair: a PR with critic APPROVE but a failed CI check has
    no automatic fixer — agent-fix's trigger is a REQUEST_CHANGES comment,
    and the gate (correctly) won't merge red. Dispatch the fix agent for any
    open agent PR in that state whose head is >20 min old (gives medic's
    auto-retry time to clear transient flakes first). Origin: PR #46 sat
    approved-but-red with nothing coming. Skips when a fix run is already
    queued/in_progress (same busy-guard as the conflict sweep)."""
    # Unreadable answers BUSY (gh_actions_read): the App token 403s on this
    # API, and the old `or "[]"` turned that into "nothing running" — the
    # backoff failed OPEN at every one of these sites.
    if _actions_runs_busy(fix_workflow()):
        return
    prs = json.loads(gh(
        "pr", "list", "--repo", REPO, "--state", "open", "--limit", "30",
        "--json", "number,headRefName,headRefOid,mergeStateStatus,comments",
    ) or "[]")
    for pr in prs:
        if not card_branch(pr["headRefName"]) or pr.get("mergeStateStatus") == "DIRTY":
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
        if fix_dispatch_blocked(pr):
            continue  # human-parked card (DRE-2024) — the loop is over
        print(f"approved-but-red: PR #{pr['number']} has APPROVE + {failed.strip()} failed check(s) — dispatching fix agent")
        gh_dispatch("workflow", "run", fix_workflow(), "--repo", REPO,
                    "-f", f"pr_number={pr['number']}")
        return  # one dispatch per sweep; the busy-guard handles the rest


WORKER_BOT_LOGIN = "agent-bureau-bot"


def is_worker_bot_comment(comment: dict) -> bool:
    """True iff the PR comment was authored by the WORKER bot App — the
    identity agent-fix's Report step posts with. Same login-shape tolerance
    as is_qa_bot_comment: `gh pr list --json comments` is GraphQL-backed and
    carries no "[bot]" suffix; the suffix is stripped so either shape
    matches. Same literal-login rationale too (reconcile never re-mints a
    token just to learn its own slug; a rename fails CLOSED — no dispatch)."""
    login = (comment.get("author") or {}).get("login") or ""
    return login.removesuffix("[bot]") == WORKER_BOT_LOGIN


def retry_dead_fix_runs() -> None:
    """DRE-2018: re-dispatch the fix agent for a PR whose last fix run died
    of a model/API error (is_error — an outage, not an agent failure), or
    (DRE-2312) ran out of turns before it could push.

    A dying fix run pushes nothing, and the qa-bot's REQUEST_CHANGES comment
    that triggered it is consumed — nothing event-driven ever re-fires
    agent-fix for that PR (the medic does not watch Agent Fix; merge-gate
    dispatches it only for merge conflicts), so the PR would stall in In Review
    forever. agent-fix's Report step posts a retry marker instead of parking;
    this sweep is the promised retry.

    Dispatch iff the NEWEST worker-bot comment on the PR carries one of
    fix_dead_run.RETRY_MARKERS: any later fix outcome (pushed / blocked /
    held) posts a newer worker-bot comment and switches the sweep off. The
    caps live in agent-fix's Report step — the death after
    fix_dead_run.RETRY_CAP, and the second turn exhaustion (DRE-2312), each
    post a hold WITHOUT any marker — so the sweep stays dumb. Non-worker authors are invisible —
    a planted marker must not spawn fix runs (DRE-1995/1998 discipline).
    Skips DIRTY PRs (unstick_conflicts owns those) and backs off while a fix
    run is queued/in_progress; one dispatch per sweep, like
    fix_approved_but_red."""
    # Unreadable answers BUSY (gh_actions_read): the App token 403s on this
    # API, and the old `or "[]"` turned that into "nothing running" — the
    # backoff failed OPEN at every one of these sites.
    if _actions_runs_busy(fix_workflow()):
        return
    prs = json.loads(gh(
        "pr", "list", "--repo", REPO, "--state", "open", "--limit", "30",
        "--json", "number,headRefName,mergeStateStatus,comments",
    ) or "[]")
    for pr in prs:
        if not card_branch(pr["headRefName"]) or pr.get("mergeStateStatus") == "DIRTY":
            continue
        worker = [
            c.get("body") or ""
            for c in pr.get("comments", [])
            if is_worker_bot_comment(c)
        ]
        if not worker or not any(t in worker[-1] for t in fix_dead_run.RETRY_MARKERS):
            continue
        if fix_dispatch_blocked(pr):
            continue  # human-parked card (DRE-2024) — the loop is over
        why = (
            "ran out of turns"
            if fix_dead_run.TURN_CAP_TAG in worker[-1]
            else "died of a model/API error"
        )
        print(
            f"dead fix run: PR #{pr['number']} last fix run {why} — "
            f"re-dispatching fix agent"
        )
        gh_dispatch("workflow", "run", fix_workflow(), "--repo", REPO,
                    "-f", f"pr_number={pr['number']}")
        return  # one dispatch per sweep; the busy-guard handles the rest


# Answered-blocker restart (DRE-2409). The escalate-by-exception exit door
# only opened halfway: a recognised operator decision reached the NEXT fix
# dispatch, but nothing ever fired one. The REQUEST_CHANGES comment that
# triggers agent-fix is consumed by the run that held, merge-gate dispatches
# only for conflicts, and the card is parked — so both live incidents
# (portico #132 / DRE-2199, agent-bureau #2034 / DRE-2399) needed a hand
# `workflow_dispatch` on top of a correctly-phrased answer. This sweep is the
# missing half: the answer itself restarts the loop.
DECISION_RESTART_TAG = "fix-restart-on-operator-decision"
# The worker bot in REST shape. fix_context reads GitHub's REST payload
# (user.login carries the "[bot]" suffix and user.type says Bot vs User),
# unlike the GraphQL-backed `gh pr list --json comments` the sibling sweeps
# read — which is exactly why this sweep re-fetches the thread over REST
# rather than guessing humanity from a login string.
WORKER_REST_LOGIN = f"{WORKER_BOT_LOGIN}[bot]"


def _post_pr_note(pr_number: int, body: str) -> bool:
    """Post a PR comment as the worker bot (the sweep's default GH_TOKEN).
    A failed post is recorded, never raised — same shape as the dependabot
    receipt: the caller's dispatch already happened."""
    p = subprocess.run(  # nosec B603 B607 — fixed-arg gh call, shell=False
        ["gh", "pr", "comment", str(pr_number), "--repo", REPO, "--body", body],
        capture_output=True, text=True, check=False,
    )
    if p.returncode != 0:
        err = (
            f"PR note on #{pr_number} failed rc={p.returncode}: "
            f"{p.stderr.strip()[:400]}"
        )
        _write_failures.append(err)
        print(f"ERROR: {err}", file=sys.stderr)
        return False
    return True


def _thread_worth_fetching(pr: dict) -> bool:
    """Cheap pre-filter before the per-PR REST fetch: only a PR that already
    shows a fix-loop blocker or ANY mention of the decision phrase can have
    an answer (or a near miss) to find.

    Reads the GraphQL-backed comments the PR list already carries. Fails
    OPEN — a payload with no `comments` key at all is fetched rather than
    skipped, because "we could not see" is not "there is nothing there"."""
    if "comments" not in pr:
        return True
    for c in pr["comments"] or []:
        body = c.get("body") or ""
        if is_worker_bot_comment(c) and body.lstrip().startswith(
            fix_context.BLOCKER_PREFIX
        ):
            return True
        if fix_context.mentions_decision(body):
            return True
    return False


def _pr_thread(pr_number: int) -> list:
    """The PR's comments in REST shape — the payload fix_context parses."""
    raw = gh("api", "--paginate", "--slurp",
             f"repos/{REPO}/issues/{pr_number}/comments?per_page=100")
    try:
        return fix_context.flatten_pages(json.loads(raw or "[]"))
    except (ValueError, json.JSONDecodeError) as e:
        print(f"restart sweep: unreadable thread on PR #{pr_number}: {e}")
        return []


def _release_card(pr: dict, note: str) -> None:
    """Take the PR's card out of the human queue: the operator HAS acted, so
    leaving needs-human + Triage on it would keep every other repair
    sweep standing down (DRE-2024) and keep the card in the CEO's queue
    claiming it still needs them."""
    card = branch_card(pr.get("headRefName") or "")
    if not card:
        return
    # The dispatch and its receipt already happened; a Linear outage here must
    # be recorded (the run goes red, medic sees it) and must NOT abort the
    # remaining backstops in this sweep.
    try:
        linear_ops.remove_label(card, HOLD_LABEL)
        linear_ops.cmd_advance(card, REVIEW_LANE, PARKED_STATE)
        linear_ops.cmd_comment(card, note)
    except Exception as e:  # noqa: BLE001 — any Linear/transport error
        err = f"releasing {card} after an operator decision failed: {e}"
        _write_failures.append(err)
        print(f"ERROR: {err}", file=sys.stderr)


def _report_decision_near_miss(pr: dict, thread: list) -> None:
    """Fail LOUDLY on a near miss (DRE-2409). A human comment that mentions
    an operator decision but does not parse as one is the exact case that
    burned both incidents — and silence is indistinguishable from "the
    operator has not answered yet". One notice per new near miss: the sweep's
    own notice is a worker-bot comment, so a newer near miss re-arms it."""
    near = fix_context.near_misses(thread, WORKER_REST_LOGIN)
    if not near:
        return
    near_ids = {id(c) for c in near}
    newest = max(i for i, c in enumerate(thread) if id(c) in near_ids)
    told = max(
        (
            i
            for i, c in enumerate(thread)
            if (c.get("user") or {}).get("login") == WORKER_REST_LOGIN
            and fix_context.NEAR_MISS_TAG in (c.get("body") or "")
        ),
        default=-1,
    )
    if newest < told:
        return
    print(
        f"near miss: PR #{pr['number']} has {len(near)} comment(s) that "
        "mention an operator decision but do not parse — saying so on the PR"
    )
    _post_pr_note(pr["number"], fix_context.near_miss_notice(near))


def restart_answered_blockers() -> None:
    """DRE-2409: re-dispatch the fix agent for a held PR whose latest fix-loop
    blocker now carries an operator decision after it.

    The decision is read by fix_context — the SAME predicate the fix agent's
    own thread render uses, so "the sweep saw an answer" and "the fixer sees
    an answer" can never disagree. Everything that grants the answer its
    authority is unchanged: a non-bot human author, newer than the latest
    worker-bot 🛑 blocker.

    This sweep deliberately does NOT consult fix_dispatch_blocked. The card is
    human-parked precisely BECAUSE the loop escalated, and DRE-2024's gate
    exists to stop identical doomed re-runs — an operator decision is new
    input and the human act that gate is waiting for. Runaway is bounded by
    the receipt instead: the restart posts a worker-bot comment, and the sweep
    only fires when NO worker-bot comment is newer than the decision, so each
    answer buys exactly one dispatch (a further answer re-arms it).

    The one exception is DRE-2813's no-work notice. That arming rule was
    satisfied in the wrong direction by a hand `workflow_dispatch` on a
    budget-exhausted PR: the run fixed nothing but posted a fresh 🛑 hold, and
    the hold outranked a standing answer, so this sweep stood down forever
    (PR #199, 2026-08-29). The fix job now posts a tagged "I did nothing"
    notice instead of that hold, and fix_context.decision_consumed skips it —
    the loop saying it did not move must not read as the loop moving.

    DIRTY PRs are released but not dispatched — unstick_conflicts owns
    conflicted PRs, and un-parking the card is what lets it act on the next
    sweep. Otherwise the house pattern: back off while a fix run is in flight,
    one dispatch per sweep."""
    # Unreadable answers BUSY (gh_actions_read): the App token 403s on this
    # API, and the old `or "[]"` turned that into "nothing running" — the
    # backoff failed OPEN at every one of these sites.
    if _actions_runs_busy(fix_workflow()):
        return
    prs = json.loads(gh(
        "pr", "list", "--repo", REPO, "--state", "open", "--limit", "30",
        "--json", "number,headRefName,mergeStateStatus,comments",
    ) or "[]")
    for pr in prs:
        if not card_branch(pr["headRefName"]):
            continue
        if not _thread_worth_fetching(pr):
            continue
        thread = _pr_thread(pr["number"])
        decision = fix_context.operator_decision(thread, WORKER_REST_LOGIN)
        if decision is None:
            _report_decision_near_miss(pr, thread)
            continue
        # Consumed? Any worker-bot comment newer than the decision means the
        # loop already moved on it — this sweep's receipt, a fix attempt, a
        # push marker. Only an UNANSWERED-side-newest decision restarts. The
        # predicate lives in fix_context (identity-located, DRE-2813's
        # no-work notice exempted) so this sweep and the fix job's own budget
        # gate read one rule: a dispatch that did nothing must not be able to
        # tell this sweep the loop has moved.
        if fix_context.decision_consumed(thread, decision, WORKER_REST_LOGIN):
            continue
        if pr.get("mergeStateStatus") == "DIRTY":
            print(
                f"answered blocker: PR #{pr['number']} is DIRTY — releasing "
                "the card and leaving the dispatch to the conflict sweep"
            )
            _post_pr_note(pr["number"], (
                f"🔓 {DECISION_RESTART_TAG}: your decision was picked up. This "
                "PR is conflicted with the default branch, so the conflict "
                "sweep resolves it first and the fix loop follows (DRE-2409)."
            ))
            _release_card(pr, (
                f"🔓 Your answer on PR #{pr['number']} was picked up — this "
                "card is out of your queue. The PR needs a merge conflict "
                "resolved first; the pipeline does that on its own."
            ))
            continue
        print(
            f"answered blocker: PR #{pr['number']} has an operator decision "
            "newer than its latest blocker — restarting the fix loop"
        )
        gh_dispatch("workflow", "run", fix_workflow(), "--repo", REPO,
                    "-f", f"pr_number={pr['number']}")
        _post_pr_note(pr["number"], (
            f"🔓 {DECISION_RESTART_TAG}: an operator decision landed after the "
            "last blocker, so the reconcile sweep re-dispatched the fix agent "
            "(DRE-2409) — no hand dispatch needed. One restart per answer; a "
            "further decision comment re-arms it."
        ))
        _release_card(pr, (
            f"🔓 Your answer on PR #{pr['number']} was picked up — the fix "
            "agent is running again and this card is out of your queue. "
            "Nothing more needed from you."
        ))
        return  # one dispatch per sweep; the busy-guard handles the rest


# Dependabot review routing (DRE-2047). A workflow run triggered by
# dependabot[bot]'s pull_request events receives GitHub's separate Dependabot
# secrets store — EMPTY for us — plus a read-only token, so the critic stub's
# `secrets: inherit` passes nothing and the reusable dies at required-secret
# validation with ZERO steps (bp PRs #93–#96, run 29168433294). The stub now
# SKIPS that doomed run; THIS backstop is the real review path. Same branch/
# author discipline as merge_gate.py's condition D.
DEPENDABOT_BRANCH_PREFIX = "dependabot/"
DEPENDABOT_DISPATCH_TAG = "dependabot-review-dispatch"
# Per-sweep dispatch pacing (DRE-2049). Dependabot arrives in batches —
# agent-bureau's first sweep opened 27 PRs at once — and every dispatch is a
# full critic run, so an unpaced backstop would burst-drain the LLM
# subscription in a single reconcile pass. At most this many dispatches per
# sweep, oldest PR first; the tail waits for the next sweep (~15 min cron).
DEPENDABOT_DISPATCH_CAP = 3
# Outcome-aware retry bound (DRE-2071). A dispatched review that CRASHES
# (infra failure — the run concludes with no verdict ever posted) must not
# freeze the head behind its receipt forever (bit twice live 2026-07-12:
# 27 agent-bureau reviews crashed pre-DRE-2052, then 6 atlas/deltasolv
# reviews on the v3 actor/secrets gaps — both needed an operator). But a
# persistently crashing reviewer must never be retried unboundedly either
# (bp#50, the medic-loop lesson): at most this many dispatched reviews per
# head sha, counted by the worker-bot receipts; hitting the cap surfaces on
# the fail-loudly rail instead of looping. A rebase changes the sha and
# re-arms a fresh budget.
DEPENDABOT_RECEIPT_CAP = 2


def is_dependabot_pr(pr: dict) -> bool:
    """True iff the PR is on a dependabot-named branch AND authored by
    dependabot[bot]. gh's PR listings surface a Bot author's login variously
    as "dependabot" (GraphQL), "dependabot[bot]" (REST shape) or
    "app/dependabot" (gh's bot marker) — normalize all three. A human's
    branch merely NAMED dependabot/... is not dependabot's: its
    pull_request events run with normal secrets, so the event-driven review
    already works and a sweep dispatch would double-review it."""
    if not (pr.get("headRefName") or "").startswith(DEPENDABOT_BRANCH_PREFIX):
        return False
    login = (pr.get("author") or {}).get("login") or ""
    return login.removeprefix("app/").removesuffix("[bot]") == "dependabot"


def _worker_receipt_count(pr: dict, tag: str) -> int:
    """How many WORKER-BOT receipts carrying `tag` cover the PR's current
    head — the per-sha dispatch count the receipt caps bound (DRE-2071,
    generalised for DRE-2282). Forged receipts by other authors are
    invisible (DRE-1998 discipline): a forger can neither suppress a review
    nor exhaust the retry budget — the worst achieved is one extra review.
    Receipts bound to a superseded sha don't count either: a new head
    re-arms a fresh budget."""
    sha = pr.get("headRefOid") or ""
    if not sha:
        return 0
    return sum(
        1
        for c in pr.get("comments", [])
        if is_worker_bot_comment(c)
        and tag in (c.get("body") or "")
        and sha in (c.get("body") or "")
    )


def dependabot_receipt_count(pr: dict) -> int:
    """The DEPENDABOT_RECEIPT_CAP's per-sha count (DRE-2071) — see
    _worker_receipt_count for the counting discipline."""
    return _worker_receipt_count(pr, DEPENDABOT_DISPATCH_TAG)


def _review_dispatch_in_flight() -> bool:
    """True iff a workflow_dispatch run of this repo's review stub is still
    queued/in progress — or the listing is unreadable.

    This is how a receipt-bearing head resolves its dispatched run's outcome
    at sweep time (DRE-2071): no in-flight run + no bound verdict means the
    dispatched review CONCLUDED without one — a green review always posts
    its verdict, so that is the failure/cancelled crash case. Fails CLOSED
    on an unreadable listing (DRE-2034 read discipline): re-dispatching
    while the prior run is live would CANCEL it via the stub's per-PR
    concurrency group (cancel-in-progress) — the DRE-2032
    watchdog-kills-its-patient class. Repo-wide on purpose: the In Review
    re-review nudges share the same stub, and deferring a retry one sweep
    is always cheaper than cancelling a live review.

    TOKEN + LOUDNESS (2026-08-17): this is DRE-1254 on the read path. That
    card found every `gh workflow run` here executing under the minted App
    token, which lacks Actions:write; GitHub answered "HTTP 403: Resource
    not accessible by integration" and the silent gh() helper discarded it.
    This read hits the SAME Actions API and had neither half of the fix.
    Reproduced live against DeltaSolv/deltasolv with a real bot token — the
    workflow-scoped AND repo-wide runs endpoints both 403, so it is the
    token, not the query shape. The empty stdout then parsed as ValueError
    and became "in flight" on EVERY sweep, wedging dependabot PRs #211,
    #212 and #213 for a day behind a green sweep and a reassuring log line.

    So: run under GH_DISPATCH_TOKEN like gh_dispatch does, and RECORD an
    unreadable listing in _read_failures (the sweep then exits 1 and medic
    sees it). The fail-closed answer itself is unchanged and deliberate —
    visibility is the fix, not recklessness."""
    out = gh_actions_read("run", "list", "--repo", REPO,
                          "--workflow", review_workflow(),
                          "--event", "workflow_dispatch", "--limit", "50",
                          "--json", "status")
    # None (loud path: rc!=0, already recorded) and "" (quiet path: gh()
    # discarded the rc, so an empty answer is all we get) are both UNREADABLE.
    # Either way the answer is "in flight": a re-dispatch on that fabricated
    # emptiness would cancel a live review (DRE-2034 read discipline, pinned by
    # test_unreadable_run_listing_reads_as_in_flight).
    if not out:
        return True
    try:
        runs = json.loads(out)
    except ValueError:
        _read_failures.append(
            f"unparseable review run listing: {out[:200]!r}")
        return True  # unreadable — never risk cancelling a live review
    return any(r.get("status") != "completed" for r in runs)


def _post_dependabot_receipt(pr: dict) -> None:
    """Post the once-per-sha dispatch receipt on the PR (as the worker bot —
    the sweep's default GH_TOKEN). A failed post is recorded, not raised:
    the dispatch DID happen; the next sweep merely re-dispatches one extra
    review, and the red run tells medic why."""
    body = (
        f"🔁 {DEPENDABOT_DISPATCH_TAG} @{pr['headRefOid']}: dependabot-triggered "
        "pull_request runs get GitHub's empty Dependabot secrets store, so the "
        "reconcile sweep dispatched the critic via workflow_dispatch instead "
        f"(DRE-2047). At most {DEPENDABOT_RECEIPT_CAP} dispatches per head sha "
        "— a crashed review run earns one retry (DRE-2071); a rebase re-arms "
        "a fresh budget."
    )
    p = subprocess.run(  # nosec B603 B607 — fixed-arg gh call, shell=False
        ["gh", "pr", "comment", str(pr["number"]), "--repo", REPO, "--body", body],
        capture_output=True, text=True, check=False,
    )
    if p.returncode != 0:
        err = (
            f"dependabot receipt on PR #{pr['number']} failed "
            f"rc={p.returncode}: {p.stderr.strip()[:400]}"
        )
        _write_failures.append(err)
        print(f"ERROR: {err}", file=sys.stderr)


def review_dependabot_prs() -> None:
    """DRE-2047: dispatch the critic for dependabot PRs — their own
    pull_request events can never produce a review (empty Dependabot secrets
    store; the stub skips those doomed runs), and with no Linear card the
    In Review nudges never see them either. workflow_dispatch runs with full
    secrets against the PR ref — deliberately NOT pull_request_target, which
    would attach secrets to a checkout of untrusted head code.

    Every open dependabot-authored PR whose CURRENT head has no sha-bound
    verdict gets a dispatch bounded per head sha by the worker-bot receipts,
    OUTCOME-AWARE since DRE-2071: while the dispatched run is still in
    flight the receipt blocks (re-dispatching would cancel the live run via
    the stub's concurrency group), a run that concluded with no verdict
    (crashed) earns ONE retry, and at DEPENDABOT_RECEIPT_CAP receipts the
    head stops and surfaces on the fail-loudly rail instead of looping
    (bp#50); a rebase changes the sha and re-arms. Dispatches are PACED
    (DRE-2049): at most DEPENDABOT_DISPATCH_CAP per sweep, oldest PR first,
    so a batch sweep can't burst-drain the critic's LLM quota — settled PRs
    (verdict, or receipt with its run unresolved) consume no slot, and the
    deferred tail is reported, never silently dropped. DIRTY PRs are
    skipped: dependabot rebases its own conflicts, which re-arms the new
    head."""
    prs = json.loads(gh(
        "pr", "list", "--repo", REPO, "--state", "open", "--limit", "30",
        "--json", "number,headRefName,headRefOid,baseRefName,author,"
        "mergeStateStatus,comments",
    ) or "[]")
    eligible = []
    in_flight = None  # lazy — fetched once, only when a receipt needs its outcome
    for pr in prs:
        if not is_dependabot_pr(pr):
            continue
        if pr.get("mergeStateStatus") == "DIRTY":
            print(f"dependabot: PR #{pr['number']} is DIRTY — dependabot's own "
                  "rebase will re-arm the new head")
            continue
        if not pr.get("headRefOid"):
            continue  # no sha to bind a receipt to — retry next sweep
        if verdict_bound(pr):
            continue
        receipts = dependabot_receipt_count(pr)
        if receipts == 0:
            eligible.append(pr)
            continue
        # Receipt(s) but no bound verdict: the dispatched review has not
        # produced one. Resolve its outcome before deciding (DRE-2071).
        if in_flight is None:
            in_flight = _review_dispatch_in_flight()
        if in_flight:
            print(
                f"dependabot: PR #{pr['number']} head {pr['headRefOid'][:8]} "
                "has a dispatch receipt and a workflow_dispatch review run "
                "is still in flight — waiting, never double-dispatching"
            )
            continue
        if receipts >= DEPENDABOT_RECEIPT_CAP:
            err = (
                f"dependabot: PR #{pr['number']} head {pr['headRefOid'][:8]} — "
                f"{receipts} dispatched reviews concluded with no verdict "
                f"(crashed) and the retry cap ({DEPENDABOT_RECEIPT_CAP}) is "
                f"reached; NOT re-dispatching (bp#50). A human must fix the "
                f"reviewer, then rebase the PR (a new head re-arms) or "
                f"dispatch {review_workflow()} by hand."
            )
            _write_failures.append(err)
            print(f"ERROR: {err}", file=sys.stderr)
            continue
        print(
            f"dependabot: PR #{pr['number']} head {pr['headRefOid'][:8]} — "
            f"dispatched review concluded with no verdict (crashed run); "
            f"retry {receipts + 1}/{DEPENDABOT_RECEIPT_CAP}"
        )
        eligible.append(pr)
    eligible.sort(key=lambda p: p["number"])  # oldest first — drain in arrival order
    for pr in eligible[:DEPENDABOT_DISPATCH_CAP]:
        print(
            f"dependabot: PR #{pr['number']} head {pr['headRefOid'][:8]} has no "
            f"bound verdict — its pull_request review runs are doomed (empty "
            f"Dependabot secrets store), dispatching {review_workflow()}"
        )
        if _nudge(review_workflow(), pr["number"]):
            _post_dependabot_receipt(pr)
    deferred = len(eligible) - DEPENDABOT_DISPATCH_CAP
    if deferred > 0:
        print(
            f"dependabot: {deferred} eligible PR(s) deferred past the "
            f"per-sweep dispatch cap ({DEPENDABOT_DISPATCH_CAP}) — the next "
            f"sweep picks them up oldest-first (DRE-2049)"
        )


# Crashed-review recovery for agent PRs (DRE-2282). A PR whose critic
# CRASHED — as opposed to returning a verdict — has green CI, a FAILURE
# review check, and no verdict: nothing anywhere goes red for a human, and
# two CORRECT behaviours combine into a permanent stall. The critic retries
# twice internally then deliberately fails its job "for medic visibility";
# the medic then skips a crashed critic ON PURPOSE (the DRE-1921 fix for
# the medic↔critic infra-crash loop that burned the App quota on
# 2026-06-28 — that skip STAYS). Live incident 2026-08-07: portico's
# critic token stopped authenticating and EIGHT PRs (#128, #141, #144,
# #145, #149, #150, #151, #152) sat green-CI/no-verdict until a human
# hand-dispatched qa-review.yml for each. recover_crashed_reviews() below
# automates exactly that hand remedy, bounded per head sha the same way
# DEPENDABOT_RECEIPT_CAP bounds dependabot retries (DRE-2071): the crashed
# review earns ONE re-dispatch, a new commit re-arms, and a head that caps
# out is REPORTED in the flag_no_checks_prs shape instead of looped on.
CRASHED_REVIEW_DISPATCH_TAG = "crashed-review-redispatch"
#: Automatic re-dispatches per head sha. ONE on purpose: the original
#: review already ran and crashed once (often twice, counting the critic's
#: internal retries), so a second sweep dispatch failing too is an outage,
#: not a flake — escalate, never loop (the DRE-1921 quota lesson).
CRASHED_REVIEW_RETRY_CAP = 1
#: Per-sweep dispatch pacing (the DRE-2049 lesson): the 2026-08-07 outage
#: crashed eight reviews at once, and every re-dispatch is a full critic
#: run — an unpaced sweep would burst-drain the LLM quota. Oldest PR
#: first; the tail waits for the next ~15-min sweep.
CRASHED_REVIEW_SWEEP_CAP = 3
REVIEWER_DOWN_TAG = "reviewer-down"
STALE_VERDICT_TAG = "stale-verdict-watchdog"
#: Review-check conclusions that mean "concluded without ever posting a
#: verdict" — the same set fix_approved_but_red treats as failed. A green
#: review always posts its verdict before its job completes (DRE-1994), so
#: any of these with no bound verdict is the crash case.
_REVIEW_CRASH_CONCLUSIONS = ("failure", "timed_out", "cancelled")


def _review_checks_at_head(sha: str) -> list[tuple[str, ...]] | None:
    """[(status, conclusion, name)] of the review-named check runs at `sha`,
    or None when the read fails (DRE-2034: a 403 parsed as emptiness is not
    "no crashed review" — act on nothing, never on fabricated data).

    Name-based (endswith "review"), like fix_approved_but_red's exclusion
    on this same surface. DRE-1994's forged-name caveat is accepted here
    because the blast radius is tiny and safe: a PR-authored check named
    "…review" can at worst earn one bounded review dispatch and a comment —
    never a merge (the gate verifies origin itself) and never a build agent.

    The name rides along since DRE-2291 so callers can prefer the
    head-bound record over the run's own event-attributed check.
    """
    out = gh("api", f"repos/{REPO}/commits/{sha}/check-runs", "--jq",
             '[.check_runs[] | select(.name | endswith("review")) '
             '| [.status, (.conclusion // ""), .name]]')
    if not out:
        return None
    try:
        rows = json.loads(out)
        return [tuple(str(f) for f in row) for row in rows]
    except (ValueError, TypeError):
        return None


def _authoritative_review_checks(
    checks: list[tuple[str, ...]],
) -> list[tuple[str, ...]]:
    """The checks that actually speak for this head (DRE-2291).

    qa-review's own `call / review` is created against the RUN's head, so a
    workflow_dispatch re-review never touches it: the superseded
    pull_request run's CANCELLED check sits on the commit forever, saying
    the review died long after a dispatched one succeeded. The head-bound
    check qa-review now publishes is written against the reviewed sha on
    every trigger, so where it exists it is the review's outcome and the
    run-attributed checks are stale noise.

    Falls back to everything when no head-bound check is present, so every
    head reviewed before DRE-2291 shipped behaves exactly as before.
    """
    bound = [c for c in checks if len(c) > 2 and c[2] == HEAD_REVIEW_CHECK_NAME]
    return bound or list(checks)


def _post_rereview_receipt(pr: dict) -> None:
    """Post the per-sha re-dispatch receipt on the PR (as the worker bot —
    the sweep's default GH_TOKEN). A failed post is recorded, not raised:
    the dispatch DID happen; the next sweep merely re-dispatches one extra
    review, and the red run tells medic why."""
    body = (
        f"🔁 {CRASHED_REVIEW_DISPATCH_TAG} @{pr['headRefOid']}: the review "
        "run for this head crashed (an infra failure — it never produced a "
        "verdict, so this is NOT a code rejection), and the reconcile sweep "
        f"re-dispatched {review_workflow()} (DRE-2282). At most "
        f"{CRASHED_REVIEW_RETRY_CAP} automatic re-dispatch per head sha — a "
        "new commit re-arms a fresh budget; past the cap the stall is "
        "reported on the Linear card instead of retried."
    )
    p = subprocess.run(  # nosec B603 B607 — fixed-arg gh call, shell=False
        ["gh", "pr", "comment", str(pr["number"]), "--repo", REPO, "--body", body],
        capture_output=True, text=True, check=False,
    )
    if p.returncode != 0:
        err = (
            f"re-review receipt on PR #{pr['number']} failed "
            f"rc={p.returncode}: {p.stderr.strip()[:400]}"
        )
        _write_failures.append(err)
        print(f"ERROR: {err}", file=sys.stderr)


def _report_reviewer_down(pr: dict, receipts: int) -> None:
    """Cap spent = a real outage, not a flake: ONE plain-English report on
    the linked card per head sha (the flag_no_checks_prs shape — the
    TAG + "PR #N @sha:" marker is the idempotency key). Report-only: no
    hold label, no state move, and above all no dispatch — the budget is
    spent and looping past it is the DRE-1921 quota burn."""
    card = branch_card(pr["headRefName"])
    print(
        f"ERROR: crashed-review: PR #{pr['number']} head "
        f"{pr['headRefOid'][:8]} — the review crashed and {receipts} "
        f"re-dispatch(es) crashed too (cap {CRASHED_REVIEW_RETRY_CAP}); "
        "the reviewer is down and a human must fix it",
        file=sys.stderr,
    )
    if not card:
        print(
            f"WARNING: crashed-review: PR #{pr['number']} "
            f"({pr['headRefName']}) carries no card id — nowhere to report"
        )
        return
    marker = f"{REVIEWER_DOWN_TAG} PR #{pr['number']} @{pr['headRefOid']}:"
    if any(marker in b for b in linear_ops.comment_bodies(card)):
        return  # reported once for this head already — idempotent forever
    linear_ops.cmd_comment(card, (
        f"🚨 {marker} the adversarial reviewer is DOWN for open PR "
        f"#{pr['number']} (branch {pr['headRefName']}). Its review run "
        f"crashed on this commit and the sweep's {receipts} automatic "
        "re-dispatch(es) crashed too — an infrastructure outage, NOT a "
        "code rejection of the PR. CI is green but no verdict can post, "
        "so the merge gate will never fire on this commit. A human must "
        "fix the reviewer (check the critic's auth/token and its run "
        "logs), then push a new commit to the PR or dispatch "
        f"{review_workflow()} by hand — either re-arms the review."
    ))
    print(
        f"crashed-review: PR #{pr['number']} reviewer-down reported on {card}"
    )


def _report_stale_verdict(pr: dict) -> None:
    """A PR whose newest verdict binds an OLDER sha than its head, with no
    review running or dispatched, is invisible the same way a crashed one
    is: the merge gate rightly ignores the stale verdict (DRE-1990), no
    check is red, and nothing re-reviews on its own (#128 carried an
    APPROVE two commits behind its head through the 2026-08-07 outage).
    ONE report per head, same marker discipline as _report_reviewer_down."""
    card = branch_card(pr["headRefName"])
    if not card:
        print(
            f"WARNING: stale-verdict: PR #{pr['number']} "
            f"({pr['headRefName']}) carries no card id — nowhere to report"
        )
        return
    sha = pr["headRefOid"]
    marker = f"{STALE_VERDICT_TAG} PR #{pr['number']} @{sha}:"
    if any(marker in b for b in linear_ops.comment_bodies(card)):
        return  # reported once for this head already — idempotent forever
    bodies = critic_comment_bodies(pr)
    reviewed = merge_gate.verdict_sha(
        merge_gate.first_line(bodies[-1]) if bodies else ""
    )
    old = f"an older commit ({reviewed[:8]})" if reviewed else "an older commit"
    linear_ops.cmd_comment(card, (
        f"🚨 {marker} open PR #{pr['number']} (branch {pr['headRefName']}) "
        f"has its newest review verdict bound to {old}, not to the current "
        f"head ({sha[:8]}), and no review is running or queued. The merge "
        "gate rightly ignores stale verdicts, so nothing will re-review or "
        "merge this PR on its own — a fresh review of the current commit "
        "is needed. This is NOT a code rejection."
    ))
    print(f"stale-verdict: PR #{pr['number']} reported on {card}")


def recover_crashed_reviews() -> None:
    """DRE-2282 backstop: un-park agent PRs whose critic CRASHED, bounded.

    For every open, non-draft, non-DIRTY agent/* PR with no verdict bound
    to its current head (has_verdict, DRE-1990):

      * review check still queued/in_progress at the head, or a
        workflow_dispatch review run in flight anywhere → LEAVE ALONE.
        The review stub is cancel-in-progress per PR, so dispatching over
        a live review would cancel it and manufacture the very crash being
        recovered (the DRE-2032 watchdog-kills-its-patient class);
        _review_dispatch_in_flight is deliberately repo-wide and
        fail-closed, exactly as on the dependabot path.
      * crashed review check (completed failure/timed_out/cancelled) →
        ONE re-dispatch of the review stub per head sha, receipted by a
        worker-bot PR comment (CRASHED_REVIEW_RETRY_CAP, the
        DEPENDABOT_RECEIPT_CAP shape — DRE-2071). A new commit changes the
        sha and re-arms the budget. The cap is per HEAD, never per sweep:
        the count lives in the sha-bound receipts, so repeated sweeps can
        never accumulate dispatches the way the DRE-1921 loop did.
      * cap spent → the outage is REPORTED, once per head, on the linked
        card in plain English (_report_reviewer_down) — never looped on.
      * no crashed check but the newest verdict binds an older sha →
        reported once per head (_report_stale_verdict).

    Dispatches are paced per sweep (CRASHED_REVIEW_SWEEP_CAP, oldest PR
    first — the DRE-2049 burst lesson) and the deferred tail is logged,
    never silently dropped. DIRTY PRs belong to unstick_conflicts; a PR
    with unreadable check runs is skipped (DRE-2034). This path never
    merges, never fires repository_dispatch, and never dispatches a fix or
    build agent — the only workflow it may start is the review stub, i.e.
    the same hand remedy the 2026-08-07 outage needed eight times over.
    The medic's deliberate skip of crashed critics (DRE-1921) is untouched.

    A backstop must never take the sweep down with it: per-PR failures are
    recorded on the fail-loudly rail and the sweep continues (the DRE-2035
    isolation discipline)."""
    try:
        prs = json.loads(gh(
            "pr", "list", "--repo", REPO, "--state", "open", "--limit", "30",
            "--json", "number,headRefName,headRefOid,baseRefName,"
            "mergeStateStatus,isDraft,comments",
        ) or "[]")
    except Exception as e:  # noqa: BLE001 — record loudly, never kill the sweep
        _write_failures.append(f"crashed-review recovery: PR listing failed: {e}")
        print(f"ERROR: crashed-review recovery: PR listing failed: {e}", file=sys.stderr)
        return
    in_flight = None  # lazy — one workflow_dispatch listing per sweep, only if needed
    eligible = []  # crashed heads with retry budget, dispatched paced below
    for pr in prs:
        try:
            if not card_branch(pr.get("headRefName")) or pr.get("isDraft"):
                continue
            if pr.get("mergeStateStatus") == "DIRTY":
                continue  # unstick_conflicts owns conflicted PRs; the fix re-arms
            sha = pr.get("headRefOid") or ""
            if not sha:
                continue
            if verdict_bound(pr):
                continue  # a verdict still binding this head settles the PR
            checks = _review_checks_at_head(sha)
            if checks is None:
                continue  # unreadable — never act on fabricated data (DRE-2034)
            # DRE-2291: where the head carries the review's own bound check,
            # that IS the outcome — a run-attributed check the dispatch
            # cancelled must not keep speaking for this commit.
            checks = _authoritative_review_checks(checks)
            if any(status != "completed" for status, *_ in checks):
                print(
                    f"crashed-review: PR #{pr['number']} head {sha[:8]} has a "
                    "review still running — leaving alone (dispatching would "
                    "cancel it)"
                )
                continue
            if any(c in _REVIEW_CRASH_CONCLUSIONS for _, c, *_rest in checks):
                if in_flight is None:
                    in_flight = _review_dispatch_in_flight()
                if in_flight:
                    print(
                        f"crashed-review: PR #{pr['number']} head {sha[:8]} — "
                        "a workflow_dispatch review run is still in flight; "
                        "waiting, never double-dispatching"
                    )
                    continue
                receipts = _worker_receipt_count(pr, CRASHED_REVIEW_DISPATCH_TAG)
                if receipts >= CRASHED_REVIEW_RETRY_CAP:
                    _report_reviewer_down(pr, receipts)
                    continue
                eligible.append(pr)
                continue
            # No crashed review at this head and nothing in flight at it:
            # a verdict bound to a SUPERSEDED sha is the remaining
            # invisible stall (#128's shape). Report-only.
            if critic_comment_bodies(pr):
                if in_flight is None:
                    in_flight = _review_dispatch_in_flight()
                if in_flight:
                    continue  # a live dispatch may be posting the fresh verdict
                _report_stale_verdict(pr)
        except Exception as e:  # noqa: BLE001 — isolate the one PR, sweep the rest
            _write_failures.append(
                f"crashed-review recovery on PR #{pr.get('number')}: {e}"
            )
            print(
                f"ERROR: crashed-review recovery on PR #{pr.get('number')}: {e}",
                file=sys.stderr,
            )
    eligible.sort(key=lambda p: p["number"])  # oldest first — drain in arrival order
    for pr in eligible[:CRASHED_REVIEW_SWEEP_CAP]:
        print(
            f"crashed-review: PR #{pr['number']} head {pr['headRefOid'][:8]} — "
            f"review crashed with no verdict; re-dispatching {review_workflow()} "
            f"(1/{CRASHED_REVIEW_RETRY_CAP} for this head)"
        )
        if _nudge(review_workflow(), pr["number"]):
            _post_rereview_receipt(pr)
    deferred = len(eligible) - CRASHED_REVIEW_SWEEP_CAP
    if deferred > 0:
        print(
            f"crashed-review: {deferred} eligible PR(s) deferred past the "
            f"per-sweep dispatch cap ({CRASHED_REVIEW_SWEEP_CAP}) — the next "
            "sweep picks them up oldest-first (DRE-2049)"
        )


# Dependabot slot-capacity monitor (DRE-2119, found by the DRE-2110
# vendor-boundary audit, checklist Q3/Q5). Vendor behavior at the bound:
# once open-pull-requests-limit PRs are open for an ecosystem, Dependabot
# silently stops opening NEW version-update PRs — including the weekly
# grouped minor/patch (security-relevant) PR. Majors are deliberately
# excluded from the groups, arrive as singles, and the merge gate parks
# them as `human` (condition D) — so unattended majors eat the slots until
# the patch stream starves, with no signal anywhere. No-silent-killers:
# WARN (console-only) at ~80% of the CONFIGURED limit, CRITICAL on the
# fail-loudly rail AT the limit, naming the parked PRs.
DEPENDABOT_CONFIG = ".github/dependabot.yml"  # cwd = the target repo checkout
#: Dependabot's documented default when open-pull-requests-limit is omitted.
DEPENDABOT_DEFAULT_PR_LIMIT = 5
DEPENDABOT_WARN_FRACTION = 0.8

# dependabot.yml names ecosystems by manifest keyword; branch names carry the
# vendor's INTERNAL package-manager token (dependabot/<token>/...). Most pairs
# differ only by "-" vs "_"; these diverge entirely.
_DEPENDABOT_BRANCH_TOKENS = {
    "npm": "npm_and_yarn",
    "gomod": "go_modules",
    "gitsubmodule": "submodules",
    "mix": "hex",
}


def dependabot_branch_token(ecosystem: str) -> str:
    """The branch-segment spelling of a dependabot.yml ecosystem name
    (github-actions -> github_actions, npm -> npm_and_yarn, ...)."""
    return _DEPENDABOT_BRANCH_TOKENS.get(ecosystem, ecosystem.replace("-", "_"))


def parse_dependabot_limits(text: str) -> dict:
    """{ecosystem: {"limit": int|None, "groups": [names]}} from a
    dependabot.yml body. Stdlib on purpose: this sweep runs on the runner's
    bare python3 — reconcile.yml has no pip-install step, so PyYAML is not
    guaranteed. The shape understood here is the one the config guard pins
    (tests/test_dependabot_config.py), and parser drift against the LIVE
    file turns tests/test_dependabot_limit_alerts.py red."""
    result: dict[str, dict] = {}
    entry = None
    item_indent = None    # indent of the `- ` update-entry dashes
    groups_indent = None  # indent of the current entry's `groups:` key
    name_indent = None    # indent where that block's group NAMES live
    in_updates = False
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if indent == 0:
            in_updates = stripped == "updates:"
            entry = item_indent = groups_indent = name_indent = None
            continue
        if not in_updates:
            continue
        if stripped.startswith("- ") and (item_indent is None or indent == item_indent):
            item_indent = indent
            entry = {"limit": None, "groups": []}
            groups_indent = name_indent = None
            indent += 2
            stripped = stripped[2:].strip()
            if not stripped:
                continue
        if entry is None:
            continue
        if groups_indent is not None and indent <= groups_indent:
            groups_indent = name_indent = None
        key, sep, val = stripped.partition(":")
        if not sep:
            continue  # e.g. a nested list item — not a key
        key = key.strip()
        val = val.split(" #", 1)[0].strip().strip("\"'")
        if groups_indent is not None:
            # The first keyed child of `groups:` sets the name level; only
            # keys AT that level are group names (deeper = patterns/...).
            if not val and (name_indent is None or indent == name_indent):
                name_indent = indent
                entry["groups"].append(key)
            continue
        if key == "package-ecosystem" and val:
            result[val] = entry
        elif key == "open-pull-requests-limit":
            try:
                entry["limit"] = int(val)
            except ValueError:
                pass
        elif key == "groups" and not val:
            groups_indent = indent
    return result


def _is_grouped_dependabot_branch(pr: dict, groups: list) -> bool:
    """True when the PR is one of the ecosystem's grouped updates — its
    branch's final segment is `<group-name>-<hash>`. Everything else is a
    single-dependency PR: the human-merge lane condition D parks."""
    last = (pr.get("headRefName") or "").rsplit("/", 1)[-1]
    return any(last.startswith(f"{g}-") for g in groups)


def check_dependabot_capacity() -> None:
    """DRE-2119: WARN/CRITICAL before open-pull-requests-limit starves the
    minor/patch group.

    Read-only monitor, per configured ecosystem: count(open dependabot-
    authored PRs on dependabot/<token>/...) against the CONFIGURED limit
    (vendor default 5 when the key is omitted; 0 = version updates
    disabled, skipped). At ceil(80%): a WARNING sweep line, console-only.
    AT the limit: a CRITICAL on the fail-loudly rail (_write_failures ->
    red run -> medic -> Linear) naming the parked human-lane PRs — the
    singles the merge gate never auto-merges — so the operator decision
    (merge, or a config `ignore` rule) is forced BEFORE Dependabot silently
    stops opening new version-update PRs.

    Fail-safe per the DRE-2034 read discipline: no config file = the repo
    doesn't run Dependabot, silence; an EXISTING config that parses to
    nothing, or an unreadable PR listing, is a recorded read failure (red
    run), never a quiet zero. Security-update PRs share the branch
    namespace and count too — they occupy slots the same way. A sustained
    at-limit condition re-fires each sweep by design: the pressure IS the
    operator forcing function (same shape as the DEPENDABOT_RECEIPT_CAP
    alert)."""
    try:
        with open(DEPENDABOT_CONFIG, encoding="utf-8") as fh:
            config_text = fh.read()
    except FileNotFoundError:
        return  # no dependabot config -> no limit to starve
    config = parse_dependabot_limits(config_text)
    if not config:
        err = (
            f"{DEPENDABOT_CONFIG} exists but no update entries parsed — the "
            "slot-capacity check is blind (parser drift or malformed "
            "config); fix one of them (DRE-2119)"
        )
        _read_failures.append(err)
        print(f"ERROR: {err}", file=sys.stderr)
        return
    try:
        out = gh_read(
            "pr", "list", "--repo", REPO, "--state", "open", "--limit", "100",
            "--json", "number,title,headRefName,author",
        )
        prs = json.loads(out or "[]")
    except (ReconcileReadError, ValueError) as e:
        # An unreadable listing is NOT "0 open PRs — all quiet" (DRE-2034).
        err = f"check_dependabot_capacity: PR listing unreadable: {e}"
        _read_failures.append(err)
        print(f"ERROR: {err}", file=sys.stderr)
        return
    by_token = {dependabot_branch_token(eco): eco for eco in config}
    counted: dict[str, list[dict]] = {eco: [] for eco in config}
    for pr in prs:
        if not is_dependabot_pr(pr):
            continue
        parts = (pr.get("headRefName") or "").split("/")
        eco = by_token.get(parts[1]) if len(parts) >= 3 else None
        if eco is None:
            # A genuine dependabot PR the mapping can't place still occupies
            # a slot somewhere — invisible would mean undercounting, so say so.
            print(
                f"WARNING: dependabot PR #{pr['number']} "
                f"({pr.get('headRefName')}) matches no configured ecosystem "
                "— the slot count may be low (token mapping or config drift)"
            )
            continue
        counted[eco].append(pr)
    for eco, cfg in config.items():
        limit = cfg["limit"] if cfg["limit"] is not None else DEPENDABOT_DEFAULT_PR_LIMIT
        if limit <= 0:
            continue  # 0 disables version updates for the ecosystem
        open_prs = sorted(counted[eco], key=lambda p: p["number"])
        n = len(open_prs)
        if n >= limit:
            parked = [
                p for p in open_prs
                if not _is_grouped_dependabot_branch(p, cfg["groups"])
            ]
            listing = ", ".join(
                f"#{p['number']} ({(p.get('title') or '').strip()[:80]})"
                for p in parked
            ) or "none identified — every slot is a grouped PR"
            err = (
                f"CRITICAL: dependabot {eco} is at its open-PR limit "
                f"({n}/{limit}) — Dependabot now silently opens NO new "
                f"version-update PRs for {eco}, the grouped minor/patch "
                f"(security-relevant) stream included. Parked human-lane "
                f"PR(s) holding slots: {listing}. Operator decision needed "
                "per the majors playbook: merge each, or add a config "
                "`ignore` rule in dependabot.yml (`@dependabot ignore` "
                "does not work on grouped PRs — DRE-2062 — and a per-major "
                "ignore walks down to the next major — DRE-2064)."
            )
            _write_failures.append(err)
            print(err, file=sys.stderr)
        elif n >= max(1, math.ceil(limit * DEPENDABOT_WARN_FRACTION)):
            print(
                f"WARNING: dependabot {eco}: {n}/{limit} open-PR slots used "
                f"— at {limit} Dependabot silently stops opening new "
                "version-update PRs (grouped minor/patch included); merge "
                "or config-ignore the parked PRs before the limit bites "
                "(DRE-2119)"
            )


def report_break_glass() -> None:
    """Print the break-glass count on every full sweep (DRE-2737).

    The number has to be readable without anyone applying a filter or
    remembering to look, so it rides the sweep that already runs. A read that
    fails prints UNKNOWN and returns — a KPI is never worth failing a sweep
    for, and a break-glass count that renders 0 because Linear was down would
    say "the front door is fine", the one conclusion it must never invent.
    """
    try:
        c = break_glass.counts(linear_ops)
        print(break_glass.count_line(c["recorded"], c["owing"]))
        if c["owing_cards"]:
            print("break-glass still owing: " + ", ".join(c["owing_cards"]))
    except Exception as exc:  # noqa: BLE001 — a KPI read never fails the sweep
        print(break_glass.count_line(None, None, error=str(exc)))

#: How far back the eviction report looks. Wider than the sweep's own 15
#: minutes on purpose (GitHub's cron delivers sweeps 78-100 minutes apart in
#: practice), so a dropped verdict is named at least once rather than falling
#: between two ticks. A run named twice costs a log line; one never named is
#: the DRE-2810 stall.
FIX_EVICTION_WINDOW_MIN = 180


def report_fix_concurrency(workflows: str = _WORKFLOWS_DIR) -> None:
    """Audit THIS repo's agent-fix stub on every full sweep (DRE-2810).

    The concurrency group is written in each consumer repo's stub, not in the
    reusable workflow, so "every stub in the fleet carries the grouping" cannot
    be answered from one repository. It can be answered in every repository, on
    the sweep that already runs there: the stub is in the checkout this sweep
    is standing in.

    A stub that still groups on the PR alone is a WARN, not a failure. The
    condition is latent — it costs nothing until a critic returns a verdict
    while a fix run is finishing — and taking a fleet of sweeps red over a
    stub only that repo's own PR can change would be an alarm people learn to
    ignore (standards: WARN at the threshold, CRITICAL when it breaks).
    """
    try:
        problems = fix_concurrency.audit_workflows_dir(workflows)
    except Exception as exc:  # noqa: BLE001 — a report never fails the sweep
        print(f"fix-concurrency: UNKNOWN — could not audit {workflows} ({exc})")
        return
    if not problems:
        print(
            "fix-concurrency: this repo carries no agent-fix stub — nothing "
            "to check."
        )
        return
    for name, found in sorted(problems.items()):
        if not found:
            print(
                f"fix-concurrency: {name} groups Agent Fix runs by PR and "
                "commenter — a bot notice cannot evict a pending "
                "REQUEST_CHANGES trigger."
            )
            continue
        for line in found:
            print(f"fix-concurrency: WARN — {line}")


def _fix_run_job_count(run_id) -> int | None:
    """How many jobs GitHub listed for a run. None when unreadable — never 0,
    which is the very fact the caller is looking for."""
    args = ("api", f"repos/{REPO}/actions/runs/{run_id}/jobs", "--jq", ".total_count")
    out, detail = _actions_read(args)
    if detail is not None:
        _note_actions_read_failure(args, detail)
        return None
    try:
        return int((out or "").strip())
    except ValueError:
        return None


def report_evicted_fix_runs(now: str | None = None) -> None:
    """Name every Agent Fix run cancelled before it started a job (DRE-2810).

    A `cancelled` Agent Fix run has two meanings and GitHub records them
    identically: a duplicate dispatch nobody was waiting on, and a
    REQUEST_CHANGES verdict evicted from the concurrency queue by a run that
    went on to skip. The second one stalls a PR indefinitely with every run
    reporting success, and on PR #199 it read as the first.

    The actor tells them apart — on an `issue_comment` run it is the commenter
    — so a run triggered by the qa-bot and cancelled before any job started is
    a verdict that never reached the fix agent, and it is reported as one.

    This is a report, not a repair: the grouping fix is what stops the
    eviction, and re-dispatching off a log line would race the sweeps that
    already own PR recovery.
    """
    workflow = fix_workflow()
    args = ("api", f"repos/{REPO}/actions/workflows/{workflow}/runs?per_page=30",
            "--jq", fix_concurrency.RUNS_JQ)
    out, detail = _actions_read(args)
    if detail is not None:
        # Absence is a third answer, never a failure (DRE-2525): adjudicate it
        # BEFORE recording, or a repo with no fix stub takes the sweep red.
        if workflow_on_default_branch(workflow) is False:
            return
        _note_actions_read_failure(args, detail)
        print(
            "evicted-fix-run: UNKNOWN — the Agent Fix run listing was "
            "unreadable, so this sweep cannot say whether a verdict trigger "
            "was evicted."
        )
        return
    try:
        runs = json.loads(out) if out else None
    except ValueError:
        runs = None
    if not isinstance(runs, list):
        print(
            "evicted-fix-run: UNKNOWN — the Agent Fix run listing did not "
            "parse, so this sweep cannot say whether a verdict trigger was "
            "evicted."
        )
        return

    lost = duplicates = 0
    for run in runs:
        if not fix_concurrency.is_cancelled(run):
            continue
        try:
            if age_minutes(run.get("created_at") or "", now) > FIX_EVICTION_WINDOW_MIN:
                continue
        except ValueError:
            continue
        jobs = _fix_run_job_count(run.get("id"))
        if jobs is None:
            print(
                f"evicted-fix-run: UNKNOWN — could not read the job list for "
                f"run {run.get('id')}, so whether it ever started is unknown."
            )
            continue
        if not fix_concurrency.never_started(jobs):
            continue  # cancelled mid-work: a timeout or a human, not an eviction
        if fix_concurrency.trigger_kind(run) != fix_concurrency.TRIGGER_VERDICT:
            duplicates += 1
            continue
        lost += 1
        print(fix_concurrency.eviction_report(
            run, fix_concurrency.evictor_of(run, runs)))
    print(
        f"evicted-fix-run: {lost} verdict trigger(s) and {duplicates} no-op "
        f"trigger(s) were cancelled before starting in the last "
        f"{FIX_EVICTION_WINDOW_MIN} minutes."
    )


def report_epic_growth(epics: set[str]) -> None:
    """Refresh each active epic's growth artifact on every full sweep (DRE-2739).

    The epic shows what it was green-lit at and what it is now — approved at
    nine cards, running at fourteen. Nobody polices the number; it just has to
    be visible, because silent accretion turns an approved scope into an
    unapproved one with no single decision being wrong. Riding the sweep that
    already runs is what makes it visible without anyone remembering to look.

    A read that fails prints and moves on: a KPI is never worth failing a sweep
    for, and one epic's unreadable history must not cost the others theirs.
    """
    for epic in sorted(epics):
        try:
            report = mid_epic.refresh_epic_growth(linear_ops, epic)
        except Exception as exc:  # noqa: BLE001 — a KPI read never fails the sweep
            print(f"epic-growth: {epic} unknown — Linear did not answer ({exc})")
            continue
        if report["unrecorded"]:
            print(
                f"epic-growth: {epic} grew without its plan changing — "
                + ", ".join(report["unrecorded"])
            )


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
        for backstop in (
            drain_retiring_lanes,
            unstick_conflicts,
            retrigger_dead_heads,
            flag_no_checks_prs,
            flag_unowned_prs,
            flag_unlanded_work,
            fix_approved_but_red,
            retry_dead_fix_runs,
            restart_answered_blockers,
            review_dependabot_prs,
            recover_crashed_reviews,
            check_dependabot_capacity,
        ):
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
        # card_pr.has_work_pr is the ONE "this card produced a PR" predicate
        # (DRE-2316): OPEN or MERGED. Every no-PR branch below is reachable
        # only when it is False, so a merged PR can never read as a dead run
        # here — the mistake the run's own Report step made ten seconds after
        # PR #137 merged.
        has_pr = card_pr.has_work_pr(pr)
        merged = has_pr and card_pr.pr_state(pr) == card_pr.MERGED
        is_open = has_pr and card_pr.pr_state(pr) == card_pr.OPEN
        print(f"stale: {ident} in {state} (pr={pr['number'] if pr else None})")

        if hand_built(card) and not has_pr:
            # DRE-2524, second half: the label suppresses the sweep's own
            # dispatch, not just the watchdog's alarm about it. Every no-PR
            # branch below starts or restarts an agent — Todo redispatches, In
            # Progress requeues to Todo (which redispatches next sweep) and
            # then parks to Backlog with HOLD_LABEL. On hand-built work that is
            # a competing run on a card the label says no run is coming for.
            # Scoped to `not has_pr` on purpose: once there IS a pull request
            # the branches below are ordinary PR shepherding (merged → Done,
            # open → In Review) and stay label-blind like every PR-level backstop.
            print(
                f"hand-built: {ident} in {state} with no PR — no dispatched "
                "run is coming by design, leaving alone"
            )
            continue

        if merged:
            # Same guard as linear-sync's card-done (the six portico false
            # closes — DRE-2242 ×2, DRE-2241, DRE-2218, DRE-2253, DRE-2252):
            # for a `no-code` operator card or a `DEMO:`-titled card the merge
            # is not the work, and this backstop must not re-close one sweep
            # later what linear-sync deliberately left open. The marker
            # comment posts at most once (card-done normally already did);
            # the card then sits here, correctly open, until the operator
            # closes it by hand.
            skip = linear_ops.auto_done_skip_reason(
                card.get("title") or "",
                [l["name"] for l in (card.get("labels") or {}).get("nodes", [])],
            )
            if skip is not None:
                print(
                    f"AUTO-DONE SKIPPED for {ident}: {skip} — the operator "
                    "closes this card by hand (see linear_ops.auto_done_skip_reason)."
                )
                if not linear_ops.count_comments(
                    ident, linear_ops.MERGED_NOT_CLOSED_MARKER
                ):
                    linear_ops.cmd_comment(
                        ident,
                        linear_ops.merged_not_closed_comment(
                            f"https://github.com/{REPO}/pull/{pr['number']}", skip
                        ),
                    )
                continue
            # Break-glass debt (DRE-2737): the same call linear-sync's
            # card-done makes, for the same reason the no-code guard is
            # mirrored here — linear-sync can be down, and this backstop must
            # not close a card that owes the classification it skipped. Read
            # off the `break-glass:used` receipt, so a marker removed
            # mid-flight neither strands the card nor cancels the debt.
            if break_glass.owes_review(
                [l["name"] for l in (card.get("labels") or {}).get("nodes", [])]
            ):
                if not linear_ops.count_comments(ident, break_glass.REVIEW_TAG):
                    linear_ops.cmd_comment(
                        ident,
                        break_glass.review_notice(
                            f"https://github.com/{REPO}/pull/{pr['number']}"
                        ),
                    )
                linear_ops.cmd_state(ident, break_glass.REVIEW_STATE)
                continue
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
                linear_ops.cmd_advance(ident, REVIEW_LANE, "In Progress")
                if _nudge(review_workflow(), pr["number"]):
                    linear_ops.cmd_comment(
                        ident,
                        "🧹 Reconcile: PR exists but card was stuck In Progress — "
                        f"advanced to {REVIEW_LANE}, critic re-triggered.",
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
                # since=RESET_TAG: only deaths after the last un-park count.
                dead = linear_ops.count_comments(ident, DEAD_TAG, since=RESET_TAG)
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
        elif state == REVIEW_LANE and is_open:
            # ONE review lane since DRE-2726, and the branch it takes is decided
            # by the EVIDENCE rather than by which of two lanes the card sat in.
            # The two lanes both meant "a pull request is open and being
            # checked"; what actually differs is whether a verdict is bound to
            # the head yet.
            if verdict_bound(pr):
                if _nudge(gate_workflow(), pr["number"]):
                    linear_ops.cmd_comment(
                        ident,
                        "🧹 Reconcile: verdict present but merge never happened — merge gate re-triggered.",
                    )
            else:
                # review_workflow(), not a hardcoded qa-review.yml: in the
                # self-host repo that filename is the reusable and the
                # dispatch would 422 silently into _write_failures (DRE-2047).
                if _nudge(review_workflow(), pr["number"]):
                    linear_ops.cmd_comment(
                        ident,
                        "🧹 Reconcile: no critic verdict after "
                        f"{STALE_MINUTES[REVIEW_LANE] // 60}h — review re-triggered.",
                    )
        elif state == REVIEW_LANE and not is_open:
            # Capped like the In Progress dead-run path (DRE-1403 mechanics,
            # same shared DEAD_TAG counter): uncapped, a card whose PR keeps
            # reading as gone laps the review lane → Todo → In Progress → the
            # review lane forever, burning an agent run per lap (DRE-2034).
            # since=RESET_TAG: only deaths after the last un-park count.
            dead = linear_ops.count_comments(ident, DEAD_TAG, since=RESET_TAG)
            if dead >= REQUEUE_CAP:
                linear_ops.add_label(ident, HOLD_LABEL)
                # --park: deliberate HOLD-cap park, same DRE-1885 opt-out as
                # the In Progress hold.
                linear_ops.cmd_state(ident, "Backlog", "--park")
                linear_ops.cmd_comment(
                    ident,
                    f"🚨 held-for-human: {REVIEW_LANE} with no PR after {dead} "
                    f"requeues — parked in Backlog with the '{HOLD_LABEL}' label "
                    "so the sweep stops looping. A human must split/fix the card "
                    "and clear the label to retry.",
                )
            else:
                linear_ops.cmd_state(ident, "Todo")
                linear_ops.cmd_comment(
                    ident,
                    f"🪦 {DEAD_TAG}: {REVIEW_LANE} with no PR — requeued to Todo "
                    f"(dead run {dead + 1}/{REQUEUE_CAP + 1}).",
                )
        nudges += 1
    # The break-glass KPI, beside the sweep's own numbers (DRE-2737): a rising
    # count is a finding about the front door, not about the people using it.
    report_break_glass()
    # The epic-growth KPI (DRE-2739), beside the sweep's own numbers: green-lit
    # at N, running M, and any card that joined without the plan moving with it.
    report_epic_growth(epics)
    # The fix loop's own grouping (DRE-2810), audited where the stub lives —
    # the only way "every stub in the fleet carries it" is checked rather than
    # remembered — and any REQUEST_CHANGES trigger GitHub cancelled before it
    # could start, which otherwise reads as a harmless duplicate dispatch.
    report_fix_concurrency()
    report_evicted_fix_runs()
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
