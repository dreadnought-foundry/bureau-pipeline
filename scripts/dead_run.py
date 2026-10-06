#!/usr/bin/env python3
"""Dead-run requeue + hold-cap decision, unified across death classes (stdlib).

A card whose agent dies with NO PR is requeued at most REQUEUE_CAP times, then
HELD for a human (Backlog + needs-human label) so the pipeline stops looping
(DRE-1403). Three death classes share ONE cap, counted by the `dead-run-requeue`
comment tag:

  - silent  : ended with no PR and no blocker note (agent-task Report step)
  - hung     : timed out, never reached Report (reconcile sweep)
  - is_error : an API/model death mid-run (DRE-1354) — PREVIOUSLY this failed the
               job and the medic re-ran it on the SAME model, bypassing the cap,
               so DRE-1300 looped 18×. Now an is_error death counts toward the
               same cap AND records which model died (`model-error:`), so the
               requeue's next attempt selects the ALTERNATE model
               (see model_fallback.py).

TURN EXHAUSTION IS ITS OWN CLASS (DRE-2312), on its own tag and its own cap:

  - turn_exhaustion : the agent hit claude-code-action's turn ceiling. It RAN —
               agent-bureau run 32791846359 (DRE-2695) spent 36 minutes and
               reached "3/5 implementation green" — and the card was still told
               "agent died with API/model error (is_error) … dead run 1/3",
               with a `model-error: claude-opus-5` marker that armed the
               DRE-1354 fallback to switch models for a reason that did not
               exist. A budget ceiling is not a model fault and not an outage:
               it spends NO dead-run strike and writes NO model-error marker.
               And it is READ before it is retried (DRE-4366), off the last
               run's furthest `⏳ n/5` marker (`turn_budget.runs_progress()`):
               a death at or past implementation green is requeued ONCE at the
               same budget (the two DRE-2695 attempts diverged by ~10 minutes
               at the same milestone — the cap is a race the retry can win),
               and a second one there holds for a human saying budget, not
               size. A death BEFORE implementation green — or with no marker
               at all — is size, not budget: it goes to Planning to be cut
               smaller, with no retry, no park and no hold label.

A CREDENTIAL EXPIRY IS NOT A DEATH EITHER (DRE-3043):

  - credential_expiry : the run did the whole card and could not deliver it.
              The App installation token minted at the top of the job lives
              exactly one hour and every git credential on the runner is that
              token, so a card that takes longer than an hour cannot push its
              own branch. On 2026-09-03 run 33822932627 finished DRE-3029 with
              a green 5,001-test suite ten minutes after its credential died,
              and the card was told an agent had died with no PR — which sends
              the reader to the model, the service and the card, three places
              with nothing wrong in them. It spends the SHARED dead-run budget
              (a rebuild is a rebuild) and writes NO model-error marker,
              because no model failed. `push_rescue.py` is what makes it rare;
              this is what it is called when the re-mint did not save it.
              THE HOUR IS THE USUAL CAUSE, NOT THE ONLY ONE (DRE-3098): run
              33896126776 was refused with `error: 400` twenty-six minutes in,
              so the receipt quotes the status the push reported
              (`--push-status`) instead of asserting an expiry, and names the
              run artifact the work was written to (`--artifact`) so the
              reader knows a rebuild is a convenience, not the only way back.

A PLATFORM FAULT IS NOT A DEATH CLASS EITHER (DRE-2931). Everything above
presumes the agent RAN. A run that dies before claude-code-action is reached —
a Linear write refused at `Card → In Progress`, a context assembly that blew
up — took no turn, called no model and spent nothing, so it cannot have failed
at the card's work:

  - pre_agent : the discriminator is check_agent_result.agent_started() (no
              execution result, `num_turns: 0`, or GitHub reporting the agent
              step `skipped`). decide(pre_agent=True) returns the "infra"
              action: ONE receipt carrying NEITHER tag, no `model-error:`
              marker, no state move and no hold label — at ANY prior count.
              On 2026-09-01 DRE-2911 spent two of its three strikes on two
              such runs (~20 seconds, $0 each), was told a model had failed,
              and was parked as "a human must split/fix the card" on a count
              that was two thirds platform noise.
  - rate_limited : a Linear RATELIMITED failure, the specific pre-agent fault
              that caused DRE-2911's. Per DRE-2923's operator decision a quota
              wait is UNKNOWN, not FAILED: it is self-healing, nobody has
              anything to fix, and retrying into it deepens it. Same "infra"
              action, different sentence — "wait, it refills" and "a step
              broke" are different facts with different next actions.

A LIMIT DEATH IS A WAIT, NOT A DEATH (DRE-3171):

  - limit : the run hit a wall that was never the card's — the Claude
              account's usage limit (`is_error: true`, "You've hit your
              limit · resets 8:30pm (UTC)") or Linear's request budget
              (`LinearRateLimited: … rate limited: 2500 requests/hour
              exhausted`, or a read timeout right after `transient network
              fault, retried once`). On 2026-09-05 planner runs, the
              classifier, critic reviews, DRE-3062's build and two
              merge-syncs all died this way; the medic retried each once
              straight back into the same wall, spent an attempt, and then
              nothing came back to the card until an operator bounced it by
              hand. decide(limit=LimitDeath(...)) returns the "limit" action:
              exactly ONE marker comment (LIMIT_MARK, first line pinned —
              kind, stage, reset time, run id, account when known), NO
              state move, NO hold label, NO `model-error:` marker, and
              neither budget tag, so it spends no strike. It wins over every
              class below cancellation, including a count at the cap: the
              cap is the card's budget and this was not the card's fault.
              Bringing the card back is `limit_recovery.py`'s job — the
              reconcile sweep re-enters the stage that died once the reset
              time has passed or the account has switched.
              A TURN CAP IS NEVER THIS (DRE-3499): `limit_kind` classifies a
              whole failed log, and "hit your limit"/`rate_limit_error` are
              words an agent writes when it has merely READ the standard that
              quotes them. On 2026-09-07 the medic marked epic DRE-3257
              `kind=claude stage=plan` for agent-bureau run 34144302622,
              whose review ran 51 turns against a 48-turn ceiling and ended
              `"subtype": "success"` — and the marker arms limit_recovery to
              re-enter the plan stage when a window it never hit resets.
              `turn_cap_in_text` vetoes the Claude answer on the action's own
              turn-cap evidence; the Linear answer is untouched, because a
              turn count cannot explain away another vendor's refusal.
              THE CLAUDE WALL IS model_fallback's CAPACITY LIST (DRE-3978).
              `claude-fable-5-1` refused every planning call on 2026-09-12/13/14
              with "You've hit your monthly spend limit. Switch to another
              model to continue." (portico run 34924370626) — a sentence the
              old two-string list did not match, so no marker was written and
              nothing brought those cards back. `limit_kind` now reads the one
              list DRE-3970 built, minus `overloaded_error`: a 529 is the
              service being busy, clears by itself, and names no reset, so it
              is a capacity signature but never a limit death.
              A TOKEN OUR OWN RENEWAL REVOKED IS NEVER THIS (DRE-5856). The
              console renews the Claude chain about every eight hours and
              revokes the token before it, so a run in flight dies on a 401.
              The receipt step prints `credential_rotation.log_line()` for
              it, and `limit_kind` vetoes the Claude answer on that line: the
              medic re-runs the run on the renewed token instead.

A CANCELLED run is NOT a death class (DRE-2074): when the agent step's outcome
is `cancelled` (the job timeout, or an external/concurrency cancel), the agent
was killed while still working — it did not die. The old code read the
`always()` Report step's "no PR, no blocker" as a silent death right at the
45-minute job timeout and parked healthy long builds (DRE-2070 was killed 4×
mid-work, hold posted at the 45-minute mark while the run was in_progress on
GitHub with a ⏳ receipt 6 minutes old). decide(cancelled=True) returns the
"defer" action: ONE informational comment WITHOUT the DEAD_TAG (it must not
increment the shared cap), no state move, no hold label — regardless of the
prior count. The reconcile sweep's authoritative run-status check (DRE-2032)
owns the requeue once the run has actually CONCLUDED without a PR: dead-run
handling as today, never over a live run.

The cap is a budget, and a human can REFILL it: `linear_ops.py unpark <CARD>`
clears the hold label, posts a `dead-run-budget-reset` marker, and returns the
card to Todo. The prior dead count is then only the `dead-run-requeue` comments
AFTER that marker (linear_ops.count_comments(..., since=RESET_TAG)) — so a card
held for a reason that was never about the card (a fleet-wide model outage;
DRE-2308/2309/2310) gets a genuinely fresh set of attempts instead of re-holding
on its first death forever.

This module is the no-I/O core that decides — given the prior dead count and the
death class — whether to REQUEUE (→ Todo) or HOLD (→ Backlog + needs-human), and
what comment(s) to post. The workflow does the Linear writes; the decision is
unit-tested here so the "is_error counts toward the cap" regression is pinned.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ONE definition of what the action's turn ceiling looks like (DRE-3499). The
# limit classifier below vetoes on it, and check_agent_result is where it
# already lives — no I/O, no Linear seam, so importing it costs nothing.
import check_agent_result  # noqa: E402 — after the path insert, by design

# ONE definition of what a capacity refusal looks like (DRE-3978). DRE-3970
# built that list for the fallback decision; the limit classifier below reads
# the same one rather than keeping a second, older copy of the same sentences.
# Import-safe: model_fallback does no I/O and imports nothing of ours.
import model_fallback  # noqa: E402 — after the path insert, by design

# A run our own token renewal killed (DRE-5856): the line the receipt step
# prints into the log, and the one reader of it. Import-safe: no I/O at import.
import credential_rotation  # noqa: E402 — after the path insert, by design

DEAD_TAG = "dead-run-requeue"
HOLD_LABEL = "needs-human"
REQUEUE_CAP = 2  # requeue at most twice (attempts 1,2,3), then hold

# Turn exhaustion's own budget tag and cap (DRE-2312). The string discipline
# RESET_TAG documents below applies here too: counting is substring-based, so
# TURN_TAG must not contain DEAD_TAG (every exhaustion would spend a death) and
# DEAD_TAG must not contain TURN_TAG. "turn-exhaustion-requeue" and
# "dead-run-requeue" share only the "-requeue" suffix; neither contains the
# other, and tests/test_turn_exhaustion_not_outage.py pins both directions.
TURN_TAG = "turn-exhaustion-requeue"
TURN_REQUEUE_CAP = 1  # requeue once (attempts 1,2), then hold

# The un-park marker. A held card that a human releases (`linear_ops.py unpark`)
# gets this comment, and the death count is only the DEAD_TAG comments AFTER the
# most recent one — see linear_ops.count_comments(..., since=RESET_TAG).
#
# THE TRAP IT CLOSES: nothing used to reset the count, and the HOLD comment
# below itself contains DEAD_TAG (it is the last death's own receipt). So a
# card a human un-parked still carried its entire exhausted history and
# re-held on its very FIRST subsequent death — no fresh budget, ever. Three
# good portico cards (DRE-2308/2309/2310) landed there after a fleet-wide
# model misconfiguration killed their runs: ~6 matching comments each, for
# deaths that said nothing about the cards. Same bug class the fix loop
# already closed in DRE-2018 (fix_dead_run.consecutive_prior_deaths counts
# deaths since the last successful push).
#
# THE STRING MATTERS: counting is substring-based, so RESET_TAG must not
# contain DEAD_TAG (every reset would register as a death) and DEAD_TAG must
# not contain RESET_TAG (every death would wipe the budget it is spending).
# "dead-run-budget-reset" vs "dead-run-requeue" share only the "dead-run-"
# stem — neither is a substring of the other. tests/test_dead_run_budget_reset
# pins both directions; do not rename either string without re-checking it.
RESET_TAG = "dead-run-budget-reset"

# model_fallback writes the same prefix; kept in sync via the shared constant.
ERROR_MARKER_PREFIX = "model-error:"

# The hold's two writes (DRE-2931). The lane the card is parked in, and how
# many times each write is attempted before park() gives up having written
# NOTHING. Three because a Linear write fails for two reasons — a transient
# blip, which a second attempt clears, and an exhausted quota, which no number
# of attempts inside one run will clear; three separates them cheaply without
# pretending the second case is winnable here.
PARK_STATE = "Backlog"
PARK_ATTEMPTS = 3

# A limit death's own marker (DRE-3171). Same substring discipline as
# RESET_TAG: "limit-death" contains neither DEAD_TAG nor TURN_TAG and neither
# contains it, so the marker spends no budget and no budget receipt reads as a
# marker — tests/test_limit_death_is_its_own_class.py pins both directions.
LIMIT_TAG = "limit-death"
LIMIT_MARK = f"🪦 {LIMIT_TAG}:"

# The fingerprints, as data. The first two are the Claude account's usage
# limit as claude-code-action's result text and the API's error type carry
# it; the rest are Linear's request budget as linear_ops composes it
# (DRE-2923), names it, and — the bare extension code — as the client's error
# line quotes it. A new wall is one line here, never a branch below.
# The CLAUDE half of this tuple is no longer the whole Claude answer: since
# DRE-3978 that is `_CLAUDE_LIMIT_SIGNATURES` below, read off
# `model_fallback.CAPACITY_SIGNATURES`. These two members stay because they
# are members of that list too — the Linear loop in `limit_kind` skips them,
# and the roll-call test reads the tuple as the record of what came from where.
LIMIT_SIGNATURES = (
    "hit your limit",
    "rate_limit_error",
    "rate limited: 2500 requests/hour exhausted",
    "LinearRateLimited",
    "RATELIMITED",
)
# The read-timeout shape seen on 2026-09-05: the quota was gone and the
# socket never answered, so the ONE retry (DRE-3087) fired and then the
# second attempt timed out too. Ordered — the second string after the first —
# because a read timeout on its own is a network fault, not a limit.
LIMIT_SIGNATURE_PAIRS = (
    ("transient network fault, retried once", "The read operation timed out"),
)
# THE CLAUDE WALL IS THE CAPACITY LIST (DRE-3978). `model_fallback` already
# owns what a model says when it refuses for capacity rather than for anything
# about the request, and DRE-3970 widened it to the sentences the CLI actually
# prints. `("hit your limit", "rate_limit_error")` was a second, older copy of
# the same fact: `claude-fable-5-1` refused every planning call on
# 2026-09-12/13/14 with "You've hit your monthly spend limit. Switch to another
# model to continue." (portico run 34924370626), which contains neither, so
# `limit_kind` answered None, no marker was written and `limit_recovery.py`
# never saw the wall. ONE list, read from where it lives.
#
# Each signature is DECIDED, not inherited wholesale. A limit death is an
# ACCOUNT WALL: nothing the pipeline does clears it, it has a reset the
# recovery sweep waits for, and re-running into it wastes an attempt. That is
# every spend/usage/rate signature. `overloaded_error` is the one that is not —
# an HTTP 529 is the SERVICE being busy, it clears by itself in seconds, it
# names no reset to wait for, and marking it a limit death would park a card
# waiting for a window that was never closed. It stays a capacity signature
# (model_fallback may still fall to the next rung on it) and is not a death.
_CAPACITY_NOT_A_LIMIT_DEATH = ("overloaded_error",)
_CLAUDE_LIMIT_SIGNATURES = tuple(
    sig for sig in model_fallback.CAPACITY_SIGNATURES
    if sig not in _CAPACITY_NOT_A_LIMIT_DEATH
)
# The turn-cap VETO (DRE-3499). The Claude signatures above are ordinary
# English, and the classifier scans the WHOLE failed log — `gh run view
# --log-failed`, prose and all. On 2026-09-07 at 16:50Z the medic posted
# `🪦 limit-death: kind=claude stage=plan reset=unknown` on epic DRE-3257 for
# agent-bureau run 34144302622: nothing in that run met an account wall — the
# post-approval review (the second critic, which ran after approval until
# DRE-5268) ran 51 turns against a 48-turn ceiling and the action
# ended `"subtype": "success"`, `"num_turns": 51`. The words were in the log
# because the reviewer had READ the standard that quotes `429
# rate_limit_error` as an example. The consequence is not a wrong word:
# limit_recovery.py reads that marker and re-enters the plan stage when a
# window the run never hit "resets".
#
# So the ceiling is checked FIRST, and it is checked POSITIVELY — the same
# discipline check_agent_result._turn_cap_evidence uses, never an inference
# from the absence of something else. `_TURN_CAP_TEXT`/`_TURN_CAP_SUBTYPES`
# come from that module rather than being retyped: it already owns what the
# action's turn ceiling looks like, and a second spelling here is how two
# readers of the same payload drift apart.
_TURN_CAP_SUBTYPE = re.compile(
    r'"?subtype"?\s*:\s*"?(?:%s)"?'
    % "|".join(re.escape(s) for s in check_agent_result._TURN_CAP_SUBTYPES)
)
# The ceiling as the log states it: the action input (`maxTurns: 48`), the
# result record's own field (`"max_turns": 48`) and the CLI arg the workflow
# passes (`--max-turns 48`). "num_turns" does not contain any of these stems.
_TURN_CEILING = re.compile(r"max[_ -]?turns[\"']?\s*[:= ]\s*(\d+)", re.I)
_TURNS_SPENT = re.compile(r"\"?num_turns\"?\s*[:=]\s*(\d+)")
# The bare `RATELIMITED` code is line-anchored, exactly as medic_classify
# anchors it: DRE-2923's own card body quotes the payload and an agent log
# echoes card text, so the code counts only on a line that also names
# Linear's client or its host. A quoted payload in prose is not a wall.
_LINEAR_LINE_ANCHORS = ("api.linear.app", "LinearRateLimited", "rate limited:",
                        "linear_ops")
LIMIT_STAGES = ("classify", "plan", "build", "fix", "review", "sync")
# The workflow the medic was woken for names the stage the death has to
# re-enter. Prefix-matched, case-insensitively: the reusable is "Agent Plan
# (reusable)" and the stub is "Agent Plan", and both must read the same.
_STAGE_BY_WORKFLOW = (
    ("agent plan", "plan"),
    ("agent task", "build"),
    ("agent fix", "fix"),
    ("qa review", "review"),
    ("linear sync", "sync"),
)
# A death in the plan workflow's second-critic review re-enters `review`, not
# `plan` (DRE-5455): the epic already has its cards, and the re-review watcher
# (DRE-5640, DRE-5842) is what brings a dead review back. Anchored at the START
# of the failed step's name, never a substring, so "Re-plan after the second
# critic sent it back" and "Re-mint bot token — second critic" stay `plan`. The
# review step itself is continue-on-error; the step that fails the job on its
# behalf is "Review — the review died".
_REVIEW_STEP_PREFIXES = ("second critic", "review —")
# One Claude usage window (DRE-5455). A Claude wall whose text names no reset
# is marked with a reset this long after the run ended, so the recovery sweep
# has a clock to bring the card back on. A wall still up then costs one short
# start that dies at its first model call and is marked again.
CLAUDE_RESET_ASSUMED_HOURS = 5
_CLAUDE_RESET = re.compile(
    r"resets\s+(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>am|pm)\s*\(UTC\)", re.I
)
_LINEAR_RESET = re.compile(r"x-ratelimit-requests-reset\s*[:=]\s*(\d{10,13})", re.I)
_MARKER_LINE = re.compile(
    rf"^{re.escape(LIMIT_MARK)} kind=(\S+) stage=(\S+) reset=(\S+) run=(\S+)"
    r"(?: account=(\S+))?(?: assumed=(\S+))?\s*$"
)
_ISO_Z = "%Y-%m-%dT%H:%M:%SZ"


def turn_cap_in_text(text: str) -> bool:
    """True when a failed log carries the action's OWN turn-cap evidence.

    Four shapes, any one of which settles it:

      - `"subtype": "error_max_turns"` — the result record's JSON field;
      - `  subtype: error_max_turns` — the line print_failure_detail writes
        into the log from that same field;
      - `maximum number of turns` — the sentence the action puts in `result`
        and check_agent_result._TURN_CAP_TEXT already recognises;
      - a result record that spent at least as many turns as the log says it
        was GIVEN (`"num_turns": 51` against `--max-turns 48`). A run that was
        under its ceiling is not evidence of anything, which is why the
        comparison is against a ceiling the log states rather than a constant.

    Pure text, no I/O: the medic holds a log, not an execution file.
    """
    text = text or ""
    if _TURN_CAP_SUBTYPE.search(text):
        return True
    if check_agent_result._TURN_CAP_TEXT in text.lower():
        return True
    ceilings = [int(m) for m in _TURN_CEILING.findall(text)]
    if not ceilings:
        return False
    spent = [int(m) for m in _TURNS_SPENT.findall(text)]
    return any(turns >= ceiling for turns in spent for ceiling in ceilings)


def limit_kind(text: str) -> str | None:
    """`"claude"` or `"linear"` when `text` — a failed run's log or result —
    carries one of `_CLAUDE_LIMIT_SIGNATURES` (the Claude wall, read off
    `model_fallback.CAPACITY_SIGNATURES`) or LIMIT_SIGNATURES /
    LIMIT_SIGNATURE_PAIRS (Linear's), else None.

    THE TURN-CAP VETO COMES FIRST (DRE-3499). A run that hit the turn ceiling
    did not hit the Claude account's usage limit, whatever words are in its
    log — and the Claude signatures are words an agent writes about itself
    when it has merely read the standard that quotes them. When the veto
    fires the Claude signatures are not consulted at all.

    The Linear answer is untouched by the veto: that is a different vendor
    refusing a request, and a turn count cannot explain it away. A log
    carrying BOTH a Linear signature and a turn cap still reads `linear`.

    THE CLAUDE SIGNATURES ARE model_fallback's CAPACITY LIST (DRE-3978), minus
    the transient service faults — matched case-insensitively, because they are
    lower-cased data and the vendor prints "Switch to another model" with a
    capital S. The Linear signatures below keep their own casing: `RATELIMITED`
    and `LinearRateLimited` are codes, not prose.

    THE CREDENTIAL-ROTATION VETO (DRE-5856) is the same kind of veto, on the
    same side. A run whose Claude token was revoked under it by the console's
    renewal hit no wall: it is re-run, never marked. Planner run 37328397948
    died on `API Error: 401 OAuth access token has been revoked` at
    utilization 0.06 and was marked `kind=claude` because its log carried
    "monthly spend limit" — in a comment of plan.yml's own script, which
    GitHub echoes into every planner run's log. The evidence is the receipt
    step's printed line (`credential_rotation.in_log`), because the 401 itself
    never reaches the log. Linear is untouched, as it is by the turn cap.
    """
    text = text or ""
    if not turn_cap_in_text(text) and not credential_rotation.in_log(text) and any(
        sig in text.lower() for sig in _CLAUDE_LIMIT_SIGNATURES
    ):
        return "claude"
    for sig in LIMIT_SIGNATURES:
        if sig in _CLAUDE_LIMIT_SIGNATURES:
            continue
        if sig == "RATELIMITED":
            for line in text.splitlines():
                if sig in line and any(a in line for a in _LINEAR_LINE_ANCHORS):
                    return "linear"
            continue
        if sig in text:
            return "linear"
    for first, second in LIMIT_SIGNATURE_PAIRS:
        at = text.find(first)
        if at >= 0 and second in text[at + len(first):]:
            return "linear"
    return None


def limit_reset(text: str, kind: str, now: datetime) -> datetime | None:
    """When the wall comes down, UTC — or None when the text does not say.

    Claude's result text says `resets 8:30pm (UTC)` with no date: that is
    today at 20:30 UTC, or tomorrow when 20:30 has already passed. Linear's
    reset is the `x-ratelimit-requests-reset` epoch (milliseconds or seconds)
    when the text carries the header; the client's error line usually does
    not, and then the honest answer is unknown.
    """
    text = text or ""
    if kind == "linear":
        found = _LINEAR_RESET.search(text)
        if not found:
            return None
        raw = int(found.group(1))
        if raw > 10**11:  # milliseconds
            raw //= 1000
        return datetime.fromtimestamp(raw, UTC)
    found = _CLAUDE_RESET.search(text)
    if not found:
        return None
    hour = int(found.group("h")) % 12 + (12 if found.group("ap").lower() == "pm" else 0)
    minute = int(found.group("m") or 0)
    now = now.astimezone(UTC)
    when = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if when <= now:
        when += timedelta(days=1)
    return when


def workflow_for_stage(stage: str) -> str:
    """The workflow a marker's stage was read from — `limit_stage` run
    backwards, off the same table — as its lower-case name prefix, or "" for a
    stage the table does not know. `classify` is a step of Agent Plan.

    Stage 2 fix #23: `limit_recovery` asks whether a `needs-human` hold blocks
    re-entering a stage, and the answer is the medic gate's rule for the
    WORKFLOW (`medic_retry.park_rule_applies`), so the stage has to name one.
    """
    wanted = "plan" if stage == "classify" else stage
    for prefix, known in _STAGE_BY_WORKFLOW:
        if known == wanted:
            return prefix
    return ""


def limit_stage(workflow_name: str, failed_step: str = "") -> str | None:
    """The stage a death in `workflow_name` has to re-enter, or None when the
    workflow is not one a card's run lives in (a Reconcile sweep dying on the
    quota is a run fault with no card to bring back). In the plan workflow a
    step whose name opens with `second critic` or `review —` is the second
    critic's review, and answers `review` (DRE-5455)."""
    name = (workflow_name or "").strip().lower()
    step = (failed_step or "").strip().lower()
    for prefix, stage in _STAGE_BY_WORKFLOW:
        if name.startswith(prefix):
            if stage == "plan" and "classif" in step:
                return "classify"
            if stage == "plan" and step.startswith(_REVIEW_STEP_PREFIXES):
                return "review"
            return stage
    return None


def pacific(when: datetime) -> str:
    """`2026-09-05 13:30 PT` — every time a person reads is Pacific."""
    try:
        from zoneinfo import ZoneInfo

        local = when.astimezone(ZoneInfo("America/Los_Angeles"))
        return local.strftime("%Y-%m-%d %H:%M PT")
    except Exception:  # noqa: BLE001 — no tz database: say UTC, never a wrong PT
        return when.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


def limit_marker(kind: str, stage: str, reset: datetime | None, run_id: str,
                 account: str | None = None, reset_assumed: bool = False) -> str:
    """The ONE comment a limit death leaves. Line one is the machine-readable
    marker limit_recovery reads back through parse_limit_marker(); the
    paragraph is for the person reading the card, and says what brings the
    card back.

    `reset_assumed` (DRE-5455) marks a Claude reset the run never stated:
    the line ends ` assumed=yes`, after `account=` when there is one. It means
    nothing without a reset, and nothing on a Linear death."""
    assumed = bool(reset_assumed and reset and kind == "claude")
    reset_field = reset.astimezone(UTC).strftime(_ISO_Z) if reset else "unknown"
    first = f"{LIMIT_MARK} kind={kind} stage={stage} reset={reset_field} run={run_id or 'unknown'}"
    if account:
        first += f" account={account}"
    if assumed:
        first += " assumed=yes"
    wall = (
        "the Claude account's usage limit" if kind == "claude"
        else "Linear's request budget (the workspace's hourly quota)"
    )
    if assumed:
        back = (
            f"The run named no reset time, so the reset is assumed five hours "
            f"after the run ended (one usage window), at {pacific(reset)}, and "
            f"the reconcile sweep re-enters the {stage} stage then. If the wall "
            f"is still up then, the run dies at its first model call and is "
            f"marked again, and a third death on an assumed clock within a day "
            f"is handed to a person."
        )
    elif reset:
        back = (
            f"The reconcile sweep re-enters the {stage} stage on its own once "
            f"the window resets (at {pacific(reset)})."
        )
    elif kind == "linear":
        back = (
            f"The run named no reset time, so the reconcile sweep re-enters the "
            f"{stage} stage on its next pass."
        )
    else:
        back = (
            "The run named no reset time, so nothing tells the reconcile sweep "
            "when the wall comes down, and it hands the card to a person."
        )
    if account:
        back += (
            f" The marker records the account {account}: a change of that "
            f"account brings the card back sooner."
        )
    paragraph = (
        f"This run hit {wall} during the {stage} stage and stopped there. That "
        f"is a wait, not a fault in this card, the model or the service: no "
        f"strike is spent against this card's budget, no model is recorded as "
        f"having failed, and the card is neither requeued into the same wall "
        f"nor parked for a human. {back}"
    )
    return f"{first}\n\n{paragraph}"


def parse_limit_marker(body: str) -> dict | None:
    """The marker's fields off a comment body, or None when it is not one.
    `reset` is a UTC datetime or None; `account` is a label or None;
    `assumed` is True only for `assumed=yes` (DRE-5455), False for every other
    marker — every marker written before that field existed included."""
    first = (body or "").split("\n", 1)[0].strip()
    found = _MARKER_LINE.match(first)
    if not found:
        return None
    kind, stage, reset_field, run_id, account, assumed = found.groups()
    reset = None
    if reset_field != "unknown":
        try:
            reset = datetime.strptime(reset_field, _ISO_Z).replace(tzinfo=UTC)
        except ValueError:
            reset = None
    return {"kind": kind, "stage": stage, "reset": reset, "run": run_id,
            "account": account or None, "assumed": assumed == "yes"}


@dataclass(frozen=True)
class LimitDeath:
    """A run that hit a wall: which wall, which stage, when it comes down,
    which GitHub run died, and — when known — which account it was on.
    `reset_assumed` says the reset is the assumed Claude window, not one the
    run stated (DRE-5455)."""

    kind: str
    stage: str
    reset: datetime | None
    run_id: str
    account: str | None = None
    reset_assumed: bool = False

    def marker(self) -> str:
        return limit_marker(self.kind, self.stage, self.reset, self.run_id, self.account,
                            reset_assumed=self.reset_assumed)


class Decision:
    """What to do about a dead run.

    action   — "requeue" (→ Todo), "hold" (→ Backlog + needs-human label),
               "replan" (DRE-4366: a turn-cap death before implementation
               green — → Planning, no label, no retry),
               "defer" (cancelled run: post the receipt, change NOTHING —
               the reconcile sweep requeues off the run's real conclusion),
               "infra" (DRE-2931: the run died before the agent started —
               post the receipt against the RUN, count nothing, move nothing),
               or "limit" (DRE-3171: the run hit a wall — post the marker,
               move nothing, label nothing)
    comments — comment bodies to post, in order (each one that contains DEAD_TAG
               also increments the shared cap for the NEXT death)
    """

    def __init__(self, action: str, comments: list[str]):
        self.action = action
        self.comments = comments

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, Decision)
            and self.action == other.action
            and self.comments == other.comments
        )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Decision({self.action!r}, {self.comments!r})"


#: DRE-4366. The first line of the receipt a turn-cap death BEFORE
#: implementation green writes: no retry, the card goes to Planning. It carries
#: TURN_TAG so `linear_ops.py count-comments` and the split ledger count it,
#: and `medic_retry.REPLAN_RECEIPT_MARK` is this string, so the medic never
#: re-runs a card this receipt has just sent to be split.
REPLAN_MARK = f"✂️ {TURN_TAG} → Planning:"

#: Where the agent writes its hand-back before it stops (the THIRD exit,
#: DRE-2727). agent-task.yml's hand-back branch reads the same path; a
#: turn-cap death that left one attaches it to the replan receipt, because
#: the pieces the agent already found are what the planner needs first.
HANDBACK_PATH = "/tmp/agent-handback.txt"

#: The five `⏳ n/5` phases, as the engineering standard names them. Only for
#: the words a receipt prints beside the number — the reading itself is the
#: NUMBER, never the label, because the three build briefs spell phase 3
#: three different ways (turn_budget.progress_of).
PHASE_NAMES = {
    1: "plan",
    2: "failing tests written",
    3: "implementation green",
    4: "local checks",
    5: "PR opened",
}


def _marker(last_progress: int) -> str:
    """A `⏳ n/5` marker with its phase name, the way a receipt prints it."""
    name = PHASE_NAMES.get(last_progress, "")
    marker = f"`⏳ {last_progress}/5`"
    return f"{marker} ({name})" if name else marker


def decide(
    prior_dead: int,
    *,
    is_error: bool = False,
    error_model: str | None = None,
    turn_exhaustion: bool = False,
    turn_facts: str = "",
    last_progress: int | None = None,
    cancelled: bool = False,
    pre_agent: bool = False,
    failed_step: str = "",
    rate_limited: bool = False,
    credential_expiry: bool = False,
    push_status: str = "",
    artifact: str = "",
    run_url: str = "",
    limit: LimitDeath | None = None,
    cap: int = REQUEUE_CAP,
    turn_cap: int = TURN_REQUEUE_CAP,
) -> Decision:
    """Decide requeue-vs-hold for a death given the prior requeue count on the
    card — `dead-run-requeue` comments normally, `turn-exhaustion-requeue`
    comments when `turn_exhaustion` (each class reads and spends its OWN
    budget, so a card that survived two API deaths still gets its retry when it
    later runs out of turns).

    `is_error`/`error_model`: this death was an API/model error on `error_model`
    — record a `model-error:` marker so the requeue switches models, and (the
    DRE-1354 contract) count it toward the SAME cap as silent/hung deaths.

    `turn_exhaustion`/`turn_facts` (DRE-2312): the agent ran out of turns.
    Wins over `is_error` (a turn-exhausted run also ends `is_error: true`, and
    reading only that bit is the bug this closes). `turn_facts` is the clause
    check_agent_result.turn_exhaustion_facts() builds — the cap it hit and what
    it spent — so the message names real numbers instead of a shrug.

    `last_progress` (DRE-4366): the furthest `⏳ n/5` marker the run that just
    died posted — the last element of `turn_budget.runs_progress()` over the
    card's thread — or None when it posted none. The death is READ before
    anything is retried, off this and `prior_dead` and nothing else:

      1. at or past implementation green (3) with the retry unspent →
         "requeue", once, at the same budget. The work was finished and the
         run was not: budget, not size.
      2. at or past implementation green with the retry spent → "hold", the
         DRE-3097 budget-not-size park. Never called a split — DRE-3088 was
         split to XS and died a third time at the same marker.
      3. before implementation green, or no marker at all → "replan": no
         retry, the card goes to Planning to be cut smaller. Size, not
         budget: another run at the same ceiling buys more of the same. There
         is one ceiling since DRE-4361 (400 for every run), so there is no
         rung to raise to and no fact about one.

    `cancelled` (DRE-2074): the agent step was cancelled — killed by the job
    timeout or an external cancel while still working, NOT a death. Wins over
    every other input, including a prior count at the cap: the answer is
    always "defer" with a no-DEAD_TAG receipt, and the reconcile sweep
    requeues (with the existing cap) only after the run has actually
    concluded without a PR. When the step ALSO classified the run as turn
    exhaustion, the receipt's first line carries TURN_TAG (DRE-4366): a kill
    at the turn cap is a turn-cap death, and the untagged receipt is how such
    deaths went missing from the count and the split ledger.

    `pre_agent`/`failed_step`/`rate_limited` (DRE-2931): the run never reached
    the agent, so it consumed no attempt at the card's work. Wins over every
    death class below it (a run that never started cannot have exhausted turns
    or killed a model, whatever else the caller passes) and is unaffected by
    the prior count, because the count is a budget for the CARD and this fault
    was not the card's. `failed_step` names the step GitHub reported as failed;
    `rate_limited` says the failure was Linear answering RATELIMITED, which is
    a wait rather than a fault at all.

    `credential_expiry` (DRE-3043): the work was finished ON THE RUNNER and
    GitHub refused the push, because the App installation token the job started
    with had aged out at 60 minutes. Ranked BELOW cancellation, the pre-agent
    fault and turn exhaustion — each of those is a fuller account of the same
    run — and above the silent/is_error classes, which would call it a death.
    It spends the shared dead-run budget (a rebuild is a rebuild) and records
    no `model-error:` marker, because no model failed.

    `push_status`/`artifact` (DRE-3098): what GitHub answered the rescue push
    with, and the run artifact the work was written to. The expiry is not the
    only way this happens — run 33896126776 was refused with a 400 twenty-six
    minutes in — so the receipt names the status rather than asserting a
    cause, and names the artifact so the reader knows the work still exists.

    `limit` (DRE-3171): the run hit the Claude account's usage limit or
    Linear's request budget — a wall that was never the card's. Ranked BELOW
    cancellation (a killed run is the fuller account) and ABOVE everything
    else, including the pre-agent fault: DRE-3062's `Card → In Progress` died
    on RATELIMITED and is exactly this. Blind to `prior_dead` for the same
    reason the pre-agent fault is. The answer is "limit": ONE marker comment
    carrying neither budget tag and no `model-error:` marker, no state move,
    no hold label — the reconcile sweep brings the card back
    (limit_recovery.py) once the reset time has passed or the account has
    switched.
    """
    run_suffix = f" Run: {run_url}" if run_url else ""
    if cancelled:
        # The receipt must not carry DEAD_TAG (it would increment the shared
        # cap) and must not start with a ⏳/🧠 proof-of-life prefix (it would
        # suppress the reconcile sweep's eventual requeue). A turn-cap death
        # that was then killed opens with TURN_TAG instead (DRE-4366), so the
        # death is counted where every other turn-cap death is.
        opener = (
            f"🪦 {TURN_TAG} (deferred): the agent ran out of steps — it hit "
            f"{turn_facts or 'the turn cap'} — and the "
            if turn_exhaustion
            else "🤖 "
        )
        return Decision(
            "defer",
            [
                f"{opener}run cancelled mid-build (GitHub job timeout or an "
                "external cancel) — the agent was killed while still working, "
                "so this does NOT count as a dead run (DRE-2074). If the run "
                "concluded without a PR, the reconcile sweep requeues it from "
                "GitHub's own conclusion — never over a live run."
                f"{run_suffix}"
            ],
        )
    if limit is not None:
        # A wall, not a death (DRE-3171). One marker, nothing moved, nothing
        # counted; limit_recovery.py re-enters the stage when the wall comes
        # down. Above the pre-agent fault on purpose — DRE-3062's pre-agent
        # RATELIMITED death is a limit death, and the marker is what lets
        # the sweep bring it back instead of leaving it for the stale clock.
        return Decision("limit", [limit.marker()])
    if pre_agent:
        # A platform fault against the RUN (DRE-2931). Checked before every
        # death class because a run that never started cannot have died of
        # one, and deliberately BLIND to `prior_dead`: the cap is the card's
        # budget and this is not the card's fault, so there is no count at
        # which it becomes one.
        where = (
            f'at "{failed_step}"' if failed_step
            else "before the agent step could run"
        )
        if rate_limited:
            return Decision(
                "infra",
                [
                    f"⏳ infrastructure wait — Linear's request quota was "
                    f"exhausted: this run failed {where}, so it could not even "
                    f"record that it had started. The agent never ran, no "
                    f"model was called and nothing was spent. A quota "
                    f"exhaustion is a WAIT, not a fault in this card and not a "
                    f"fault in any model: it refills on its own, it spends no "
                    f"strike against this card's budget, and the card is "
                    f"neither parked nor labelled for a human. The reconcile "
                    f"sweep picks the card up once the quota is back."
                    f"{run_suffix}"
                ],
            )
        return Decision(
            "infra",
            [
                f"🧯 infrastructure fault before the agent started: this run "
                f"failed {where}, so the agent never ran — no turn was taken, "
                f"no model was called and nothing was spent. That is a fault "
                f"of the RUN, not a failed attempt at this card: it spends no "
                f"strike against this card's budget, and no model is recorded "
                f"as having failed, because none was reached. The card is left "
                f"exactly where it was; the reconcile sweep picks it up from "
                f"the run's own conclusion.{run_suffix}"
            ],
        )
    if turn_exhaustion:
        # A failed ATTEMPT, reported as one: no dead-run strike, no
        # model-error marker (DRE-2312). And READ before it is retried
        # (DRE-4366, whose count was seven in ten of 110 turn-cap deaths
        # already past implementation green): the first death used to be
        # retried blind from nothing, and the second parked for a human
        # whatever the reading said.
        import turn_budget  # local: only this branch reads the milestone

        facts = turn_facts or "the turn cap"
        if last_progress is None or last_progress < turn_budget.IMPLEMENTATION_GREEN:
            stopped = (
                f"at {_marker(last_progress)}" if last_progress
                else "before posting any progress marker"
            )
            # SIZE, NOT BUDGET. The run stopped before the work was finished,
            # and another run at the same ceiling buys more of the same — the
            # DRE-2838 shape. No retry at any count: the card goes to the lane
            # that owes a decomposition, with nothing parked and no label.
            return Decision(
                "replan",
                [
                    f"{REPLAN_MARK} the agent ran out of steps — it hit {facts} "
                    f"— and the run stopped {stopped}, before implementation "
                    f"green. That is size, not budget: the run never got the "
                    f"work finished, and another run at the same budget buys "
                    f"more of the same. Not retried — the card is back in "
                    f"Planning to be cut into smaller pieces, with nothing "
                    f"parked and no hold label.{run_suffix}"
                ],
            )
        if prior_dead >= turn_cap:
            # DRE-3097's reading, now the only one a park can make: every
            # death that reaches here got past implementation green. The
            # prefix is load-bearing: medic_retry.HELD_RECEIPT_MARK and
            # split_ledger.TURN_HOLD_MARK both match on it, and a park the
            # medic does not recognise is a turn-cap death it retries.
            return Decision(
                "hold",
                [
                    f"🚨 held-for-human ({TURN_TAG} cap reached): the agent ran "
                    f"out of steps {prior_dead + 1} times in a row — the last "
                    f"run hit {facts}, having reached {_marker(last_progress)}. "
                    f"That is budget, "
                    f"not size: the work finishes but the run does not, and the "
                    f"one retry at the same budget did not land it either. "
                    f"Parked in Backlog with the '{HOLD_LABEL}' label for a "
                    f"person to read what the runs left behind and decide how "
                    f"the card gets finished.{run_suffix}"
                ],
            )
        return Decision(
            "requeue",
            [
                f"🪦 {TURN_TAG}: the agent ran out of steps — it hit {facts}, "
                f"having reached {_marker(last_progress)}. That is budget, not "
                f"size: the work "
                f"was finished and the run was not. Requeued to Todo for one "
                f"more attempt at the same budget (turn exhaustion "
                f"{prior_dead + 1}/{turn_cap + 1}). If that run also dies past "
                f"implementation green, the card parks in Backlog with the "
                f"'{HOLD_LABEL}' label; if it dies short of it, the card goes "
                f"to Planning.{run_suffix}"
            ],
        )
    if credential_expiry:
        # DRE-3043. The word this branch exists to remove is "died": the run
        # did the whole card and could not deliver it, because the App
        # installation token every git credential on the runner is built from
        # lives one hour. Reported as a death it sends the reader to the model,
        # the service and the card — three places with nothing wrong in them.
        #
        # It spends the SHARED dead-run budget: a rebuild is a rebuild whatever
        # the cause, and the cap is what stops a card looping. It records no
        # `model-error:` marker, because no model failed.
        # DRE-3098: SAY WHAT GITHUB SAID. This used to assert the cause — "the
        # credential expired… installation tokens live one hour" — and run
        # 33896126776 was refused with a 400 twenty-six minutes in, so the
        # sentence was wrong about the only run whose reader needed it. The
        # status is quoted when the push reported one; the hour is offered as
        # the usual cause, not as the finding.
        refused = (
            f" — GitHub answered HTTP {push_status} to the push" if push_status
            else ""
        )
        note = (
            f"the run's GitHub credential was refused before the branch could "
            f"reach GitHub{refused} — the agent finished the work and could "
            f"not deliver it. Every git credential on the runner is one App "
            f"installation token, which lives an hour and can also be rejected "
            f"outright. This is a CREDENTIAL failure: not a fault in the "
            f"model, the service or the card"
        )
        # WHERE THE WORK IS. The rescue writes the branch out and the run
        # uploads it, so a rebuild is a convenience rather than the only path
        # back to the work — and the person reading the card is the one who
        # needs to know that.
        kept = (
            f" The work itself is not lost: this run's `{artifact}` artifact "
            f"holds the branch's commits." if artifact else ""
        )
        if prior_dead >= cap:
            return Decision(
                "hold",
                [
                    f"🚨 held-for-human ({DEAD_TAG} cap reached): {note}, and "
                    f"it has now happened {prior_dead + 1} times. Parked in "
                    f"Backlog with the '{HOLD_LABEL}' label — the re-mint at "
                    f"the push is not recovering this card, so a human needs "
                    f"to look at the App credentials before it is retried."
                    f"{kept}{run_suffix}"
                ],
            )
        return Decision(
            "requeue",
            [
                f"🪦 {DEAD_TAG}: {note}. Requeued to Todo for a fresh attempt "
                f"(attempt {prior_dead + 1}/{cap + 1}).{kept}{run_suffix}"
            ],
        )
    cause = (
        "API/model error (is_error)"
        if is_error
        else "no PR and no blocker note"
    )
    error_marker_line = ""
    if is_error and error_model:
        # Standalone marker so model_fallback.select_model picks the alternate
        # on the next attempt; on its OWN line so it survives any later edit.
        error_marker_line = f"\n{ERROR_MARKER_PREFIX} {error_model}"

    if prior_dead >= cap:
        names = ""
        if is_error and error_model:
            names = f" (last model tried: {error_model})"
        return Decision(
            "hold",
            [
                f"🚨 held-for-human ({DEAD_TAG} cap reached): agent died with "
                f"{cause} for the {prior_dead + 1}th time{names} — parked in "
                f"Backlog with the '{HOLD_LABEL}' label so the relay and the "
                f"reconcile sweep stop looping. A human must split/fix the card "
                f"and clear the label to retry.{run_suffix}{error_marker_line}"
            ],
        )
    return Decision(
        "requeue",
        [
            f"🪦 {DEAD_TAG}: agent died with {cause} — requeued to Todo for a "
            f"fresh attempt (dead run {prior_dead + 1}/{cap + 1})."
            f"{run_suffix}{error_marker_line}"
        ],
    )


def count_of(comment_bodies, tag: str) -> int:
    """How many of `comment_bodies` spend `tag`'s budget.

    The same substring test linear_ops.count_comments applies to the live
    thread, in a form a test can drive without Linear. It exists so the
    DRE-2911 trio fixture counts the way the pipeline counts — a fixture that
    asserted its own arithmetic would prove nothing about the budget.
    """
    return sum(1 for body in comment_bodies if tag in (body or ""))


def park(
    identifier: str,
    *,
    has_label,
    add_label,
    remove_label,
    write_state,
    label: str = HOLD_LABEL,
    attempts: int = PARK_ATTEMPTS,
    log=print,
) -> bool:
    """Park a card for a human — BOTH writes, or neither. True iff both landed.

    DRE-2911's park half-applied: the `needs-human` label write landed, the
    state write to Backlog did not, and the card sat In Progress for seven and
    a half hours while its own comment said it was in Backlog. Nothing retried,
    because from the pipeline's side the park was done — the label is what
    every sweep reads to leave a held card alone.

    The order and the undo are the whole mechanism:

      1. read whether the card ALREADY carries the label. An unreadable answer
         aborts having written nothing, and the answer is what decides step 4:
         a label this call did not write is not this call's to remove;
      2. write the LABEL, retried. If it never lands, stop — the state is
         untouched, so nothing is half-applied;
      3. write the hold STATE, retried. Both landed: the park is real;
      4. if the state never lands, take the label back off (only if step 2 put
         it there). That is the DRE-2911 failure, undone: the card is left
         exactly as it was found, and the sweep can re-attempt the whole park
         because nothing is telling it the card is already held.

    The undo is a LABEL write, never a lane write: restoring a state would mean
    this module could put a card in a lane a caller computed, which is the one
    thing `ready_lane_writers.py` (DRE-2859) exists to refuse. Step 4 removes
    only the label it wrote seconds earlier, in the same call — it is not, and
    must never become, a path that un-parks a card somebody else parked.

    The writes are injected rather than imported so the caller owns the Linear
    seam and the decision stays testable — the same shape decide() has.
    """

    def _attempt(what: str, action) -> bool:
        for n in range(1, attempts + 1):
            try:
                action()
                return True
            except Exception as exc:  # any Linear failure: blip or quota
                log(f"park {identifier}: {what} attempt {n}/{attempts} failed: {exc}")
        return False

    try:
        already_labelled = has_label(label)
    except Exception as exc:
        log(f"park {identifier}: could not read the card's labels ({exc}) — "
            f"wrote nothing, so the park is not half-applied.")
        return False
    if not already_labelled and not _attempt(
        f"label {label}", lambda: add_label(label)
    ):
        log(f"park {identifier}: the '{label}' label never landed — the state "
            f"was NOT written either, so nothing is half-applied and the sweep "
            f"can re-attempt the whole park.")
        return False
    if _attempt(f"state → {PARK_STATE}", lambda: write_state()):
        return True
    log(f"park {identifier}: the state write to {PARK_STATE} never landed. "
        f"Undoing this call's own label write so the card is not left claiming "
        f"a hold it is not in — the DRE-2911 shape, refused.")
    if not already_labelled:
        _attempt(f"undo label {label}", lambda: remove_label(label))
    return False


def park_unlanded_comment(run_url: str = "", tag: str = DEAD_TAG) -> str:
    """The receipt posted INSTEAD of the hold receipt when the park did not land.

    It still carries a budget tag, because the attempt it reports was real and
    un-counting it would hand the card a budget it has already spent. What it
    does not do is claim a park that did not happen — that claim is what left
    DRE-2911 sitting In Progress for seven and a half hours with nothing
    retrying it.

    `tag` is WHICH budget, and it is not decoration: decide() reaches "hold"
    from two independent caps (DEAD_TAG and TURN_TAG), this body REPLACES
    whichever one decide() wrote, and the two tags are deliberately
    non-overlapping substrings. A tag hardcoded here would silently drop the
    strike the card actually spent and bill the other budget for an attempt
    that never happened — the same accounting corruption the atomic park
    exists to remove, one corner over. The caller passes the cap that was in
    play; DEAD_TAG is the default because it is the cap three of the four
    death classes share.
    """
    run_suffix = f" Run: {run_url}" if run_url else ""
    return (
        f"🚨 {tag} cap reached — but the park did NOT land: Linear refused "
        f"the write, so this card is NOT in Backlog and does NOT carry the "
        f"'{HOLD_LABEL}' label. Neither was written rather than half of each, "
        f"so nothing here is claiming a hold that does not exist. This run is "
        f"still counted against the '{tag}' budget, and the reconcile sweep "
        f"re-attempts the park on its next pass.{run_suffix}"
    )


def last_run_markers(comment_bodies) -> list[str]:
    """The `⏳ n/5` markers the LAST run on the card posted, oldest first.

    Grouped exactly the way `turn_budget.runs_progress` groups them — a run
    begins at its `🧠 model-attempt` heartbeat, and anything before the first
    one belongs to no run — so the markers a replan receipt lists are the
    markers its `last_progress` was read from. The Report step dumps the thread
    after the run, so the last block is the run that just died.
    """
    import turn_budget  # local: the grouping rule has one owner

    runs: list[list[str]] = []
    for body in comment_bodies or []:
        text = (body or "").strip()
        if text.startswith(turn_budget._RUN_MARKER):
            runs.append([])
            continue
        if runs and turn_budget.progress_of(text) is not None:
            runs[-1].append(text.splitlines()[0])
    return runs[-1] if runs else []


def replan_evidence(markers, handback: str = "") -> str:
    """What a replan receipt carries below its first line (DRE-4366): the run's
    own progress markers, and the agent's hand-back when it wrote one before
    it died. The planner reads both before it cuts the card."""
    listed = "\n".join(f"- {m}" for m in markers) if markers else (
        "- none: the run posted no progress marker before it died")
    out = f"\n\nThis run's progress markers:\n{listed}"
    note = (handback or "").strip()
    if note:
        out += f"\n\nThe agent's hand-back, written before the run died:\n{note}"
    return out


def _read_handback(path: str) -> str:
    """The hand-back note, or "" when there is none or it cannot be read."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def turn_noted_comment(exit_name: str = "", facts: str = "", run_url: str = "") -> str:
    """The turn-cap death's own record when the run ALSO took another exit.

    DRE-4366. The Report step classifies every run, and a turn-cap death that
    left an escalation or a blocker note, or whose PR state GitHub would not
    report, or whose work went to a rescue delivery, takes that branch and
    never reaches `decide`. That is correct for the card — the exit owns it —
    and it is how such deaths went uncounted: no receipt carried TURN_TAG, so
    the count and the split ledger never saw them. This receipt moves nothing
    and says so; it exists so the death is on the card.
    """
    run_suffix = f" Run: {run_url}" if run_url else ""
    exit_clause = f" the {exit_name} posted above" if exit_name else " the exit posted above"
    return (
        f"🪦 {TURN_TAG} (noted): this run also ran out of steps — it hit "
        f"{facts or 'the turn cap'} — before it ended. The card follows"
        f"{exit_clause} and nothing here moves it; this line is the death's "
        f"own record, so the turn-cap count and the split ledger see it."
        f"{run_suffix}"
    )


def reset_comment(note: str = "") -> str:
    """The un-park receipt: it starts this card's death budget over.

    MUST NOT contain DEAD_TAG — it is posted on the way back INTO the working
    lanes, and a self-counting reset would hand the card a budget of two
    instead of three (and, with the cap at 1, none at all).
    """
    tail = f" Operator note: {note}" if note else ""
    return (
        f"♻️ {RESET_TAG}: un-parked by a human — the '{HOLD_LABEL}' label is "
        f"cleared and the card is back in Todo with a FULL set of attempts "
        f"({REQUEUE_CAP + 1}). Agent deaths recorded above this line no longer "
        f"count toward the hold cap; only deaths after it do.{tail}"
    )


def _flag_value(rest: list[str], flag: str) -> str:
    """`--flag VALUE`'s value, or "" when the flag is absent or trailing.

    One reader for every flag, so a value carrying spaces (`--failed-step
    "Card → In Progress"`) is taken whole from its own argv element rather
    than re-split by a per-flag branch.
    """
    if flag in rest:
        i = rest.index(flag)
        if i + 1 < len(rest):
            return rest[i + 1]
    return ""


def _cmd_park(identifier: str) -> int:
    """`park <CARD>` — the hold's two writes, atomically, against Linear.

    Exit 0 iff BOTH landed. Exit 1 means the park did not happen and NOTHING
    was left half-applied: the caller posts park_unlanded_comment() instead of
    a hold receipt that would claim a Backlog the card is not in.
    """
    if not identifier:
        print("usage: dead_run.py park <CARD>")
        return 2
    import linear_ops  # local: only this command needs the Linear seam

    def has_label(name: str) -> bool:
        return any(
            existing.lower() == name.lower()
            for existing in linear_ops._label_names(
                linear_ops.get_issue(identifier)
            )
        )

    def write_state() -> None:
        # --park: a deliberate HOLD-cap park (DRE-1403). Without it the
        # DRE-1885 building-card guard re-routes this In Progress → Backlog
        # move back to Todo and the loop returns. The destination is the
        # module constant, readable statically, so this stays the ONE lane
        # this module can write (ready_lane_writers.py, DRE-2859).
        linear_ops.cmd_state(identifier, PARK_STATE, "--park")

    ok = park(
        identifier,
        has_label=has_label,
        add_label=lambda name: linear_ops.add_label(identifier, name),
        remove_label=lambda name: linear_ops.remove_label(identifier, name),
        write_state=write_state,
    )
    return 0 if ok else 1


def main(argv: list[str]) -> int:
    """CLI for the workflow:

      decide <prior_dead> [--is-error] [--error-model M] [--cancelled]
             [--turn-exhaustion [--execution-file PATH] [--comments-file PATH]]
             [--run-url U]
             [--pre-agent [--failed-step NAME] [--rate-limited]]
             [--credential-expiry [--push-status N] [--artifact NAME]]
      park <CARD>
      park-unlanded [--run-url U] [--turn-exhaustion]
      turn-noted [--exit NAME] [--execution-file PATH] [--run-url U]

    With --turn-exhaustion, <prior_dead> is the card's `turn-exhaustion-requeue`
    count (its own budget) and --execution-file names the run's result JSON —
    read here, so decide() stays the no-I/O core, for the cap and spend the
    message quotes. --comments-file names the card's thread (the JSON array
    `linear_ops.py dump-comments` prints), read the same way and for the same
    reason: `last_progress` is the last run's furthest `⏳ n/5` marker in it
    (DRE-4366). An unreadable thread leaves it None — no evidence the work was
    finished is not a reason to spend another run, so that death is read as
    size and nothing is retried.

    A "replan" body is completed here, not in decide(): the run's own markers
    and, when the agent wrote one before it died, the hand-back at
    HANDBACK_PATH are listed under the first line. They are receipt text, not
    facts the decision branches on.

    `decide` prints (to stdout) the action on the first line, then a blank
    line, then the comment body. The workflow reads line 1 for the branch and
    posts the body. `park` performs the hold's two Linear writes atomically
    (DRE-2931) and exits nonzero when it wrote NEITHER; `park-unlanded` prints
    the receipt to post in that case, tagged with the budget the hold came
    from (`--turn-exhaustion` for the turn cap, the dead-run cap otherwise).
    `turn-noted` prints the tagged record of a turn-cap death that took one of
    the other exits (DRE-4366).
    """
    usage = ("usage: dead_run.py decide <prior_dead> [--is-error] "
             "[--error-model M] [--cancelled] [--turn-exhaustion] "
             "[--execution-file PATH] [--comments-file PATH] [--pre-agent] "
             "[--failed-step NAME] [--rate-limited] [--credential-expiry] "
             "[--push-status N] [--artifact NAME] [--run-url U] "
             "[--limit-log PATH --workflow NAME --run-id ID [--account L] "
             "[--now ISO]] | "
             "park <CARD> | park-unlanded [--run-url U] [--turn-exhaustion] | "
             "turn-noted [--exit NAME] [--execution-file PATH] [--run-url U]")
    if not argv:
        print(usage)
        return 2
    cmd, *rest = argv
    if cmd == "park":
        return _cmd_park(rest[0] if rest else "")
    if cmd == "park-unlanded":
        # --turn-exhaustion mirrors `decide`'s own flag: the caller names the
        # cap that produced the hold, and the tag string stays in this module
        # rather than being retyped into the shell. The workflow passes the
        # slot QUOTED — one argument, empty when the hold came from the
        # dead-run cap — so an empty element here means "no flag", not a
        # malformed call.
        print(park_unlanded_comment(
            _flag_value(rest, "--run-url"),
            TURN_TAG if "--turn-exhaustion" in rest else DEAD_TAG,
        ))
        return 0
    if cmd == "turn-noted":
        facts = ""
        exec_path = _flag_value(rest, "--execution-file")
        if exec_path:
            import check_agent_result  # local: only this branch needs the loader

            facts = check_agent_result.turn_exhaustion_facts(
                check_agent_result._load_execution(exec_path)
            )
        print(turn_noted_comment(
            _flag_value(rest, "--exit"), facts, _flag_value(rest, "--run-url")))
        return 0
    if cmd != "decide":
        print(f"unknown command {cmd!r}")
        return 2
    prior_dead = int(rest[0]) if rest and rest[0].lstrip("-").isdigit() else 0
    is_error = "--is-error" in rest
    cancelled = "--cancelled" in rest
    turn_exhaustion = "--turn-exhaustion" in rest
    pre_agent = "--pre-agent" in rest
    rate_limited = "--rate-limited" in rest
    credential_expiry = "--credential-expiry" in rest
    push_status = _flag_value(rest, "--push-status")
    artifact = _flag_value(rest, "--artifact")
    error_model = _flag_value(rest, "--error-model") or None
    run_url = _flag_value(rest, "--run-url")
    exec_path = _flag_value(rest, "--execution-file")
    failed_step = _flag_value(rest, "--failed-step")
    comments_path = _flag_value(rest, "--comments-file")
    # DRE-3171: was this a wall rather than a death? Read off the failed run's
    # own text (`--limit-log`: the result JSON, or `gh run view --log-failed`
    # output), classified here so the workflow never re-derives a signature.
    # Fail-soft: an unreadable log, an unknown workflow or no signature all
    # leave `limit` None and the decision exactly as it has always been.
    limit = None
    limit_log = _flag_value(rest, "--limit-log")
    if limit_log:
        try:
            with open(limit_log, encoding="utf-8", errors="replace") as fh:
                limit_text = fh.read()
        except OSError:
            limit_text = ""
        kind = limit_kind(limit_text)
        stage = limit_stage(_flag_value(rest, "--workflow"), failed_step)
        if kind and stage:
            now_raw = _flag_value(rest, "--now")
            try:
                now = datetime.fromisoformat(now_raw) if now_raw else datetime.now(UTC)
            except ValueError:
                now = datetime.now(UTC)
            if now.tzinfo is None:
                now = now.replace(tzinfo=UTC)
            # DRE-5455: a Claude wall that names no reset gets one usage
            # window from the run's own completion time, marked as assumed.
            # A stated reset is never assumed over, and Linear is unchanged.
            reset = limit_reset(limit_text, kind, now)
            reset_assumed = kind == "claude" and reset is None
            if reset_assumed:
                reset = now.astimezone(UTC) + timedelta(hours=CLAUDE_RESET_ASSUMED_HOURS)
            limit = LimitDeath(
                kind=kind,
                stage=stage,
                reset=reset,
                run_id=_flag_value(rest, "--run-id") or "unknown",
                account=_flag_value(rest, "--account") or None,
                reset_assumed=reset_assumed,
            )
    turn_facts = ""
    if turn_exhaustion and exec_path:
        import check_agent_result  # local: only this branch needs the loader

        turn_facts = check_agent_result.turn_exhaustion_facts(
            check_agent_result._load_execution(exec_path)
        )
    # DRE-4366: how far the run that just died got, off the card's own phase
    # receipts. Every step of this is fail-soft — an unreadable or absent
    # thread leaves `last_progress` None, which reads as "no marker at all":
    # a claim that the work was finished needs evidence, and without it no
    # further run is spent.
    last_progress = None
    markers: list[str] = []
    if turn_exhaustion and comments_path:
        try:
            import json as _json

            import turn_budget  # local: only this branch needs it

            with open(comments_path) as fh:
                bodies = _json.load(fh)
            bodies = bodies if isinstance(bodies, list) else []
            runs = turn_budget.runs_progress(bodies)
            last_progress = runs[-1] if runs else None
            markers = last_run_markers(bodies)
        except Exception as exc:
            print(f"dead_run: could not read the run's progress from "
                  f"{comments_path} ({exc}) — reading it as no marker at all",
                  file=sys.stderr)
    d = decide(
        prior_dead,
        is_error=is_error,
        error_model=error_model,
        turn_exhaustion=turn_exhaustion,
        turn_facts=turn_facts,
        last_progress=last_progress,
        cancelled=cancelled,
        pre_agent=pre_agent,
        failed_step=failed_step,
        rate_limited=rate_limited,
        credential_expiry=credential_expiry,
        push_status=push_status,
        artifact=artifact,
        run_url=run_url,
        limit=limit,
    )
    body = d.comments[0]
    if d.action == "replan":
        body += replan_evidence(markers, _read_handback(HANDBACK_PATH))
    print(d.action)
    print()
    print(body)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
