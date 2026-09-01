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
               it spends NO dead-run strike, writes NO model-error marker, is
               requeued ONCE (the two DRE-2695 attempts diverged by ~10 minutes
               at the same milestone — the cap is a race the retry can win),
               and the SECOND one holds saying the card needs splitting.

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
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

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


class Decision:
    """What to do about a dead run.

    action   — "requeue" (→ Todo), "hold" (→ Backlog + needs-human label),
               "defer" (cancelled run: post the receipt, change NOTHING —
               the reconcile sweep requeues off the run's real conclusion), or
               "infra" (DRE-2931: the run died before the agent started —
               post the receipt against the RUN, count nothing, move nothing)
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


def decide(
    prior_dead: int,
    *,
    is_error: bool = False,
    error_model: str | None = None,
    turn_exhaustion: bool = False,
    turn_facts: str = "",
    cancelled: bool = False,
    pre_agent: bool = False,
    failed_step: str = "",
    rate_limited: bool = False,
    run_url: str = "",
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

    `cancelled` (DRE-2074): the agent step was cancelled — killed by the job
    timeout or an external cancel while still working, NOT a death. Wins over
    every other input, including a prior count at the cap: the answer is
    always "defer" with a no-DEAD_TAG receipt, and the reconcile sweep
    requeues (with the existing cap) only after the run has actually
    concluded without a PR.

    `pre_agent`/`failed_step`/`rate_limited` (DRE-2931): the run never reached
    the agent, so it consumed no attempt at the card's work. Wins over every
    death class below it (a run that never started cannot have exhausted turns
    or killed a model, whatever else the caller passes) and is unaffected by
    the prior count, because the count is a budget for the CARD and this fault
    was not the card's. `failed_step` names the step GitHub reported as failed;
    `rate_limited` says the failure was Linear answering RATELIMITED, which is
    a wait rather than a fault at all.
    """
    run_suffix = f" Run: {run_url}" if run_url else ""
    if cancelled:
        # The receipt must not carry DEAD_TAG (it would increment the shared
        # cap) and must not start with a ⏳/🧠 proof-of-life prefix (it would
        # suppress the reconcile sweep's eventual requeue).
        return Decision(
            "defer",
            [
                "🤖 run cancelled mid-build (GitHub job timeout or an external "
                "cancel) — the agent was killed while still working, so this "
                "does NOT count as a dead run (DRE-2074). If the run concluded "
                "without a PR, the reconcile sweep requeues it from GitHub's "
                f"own conclusion — never over a live run.{run_suffix}"
            ],
        )
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
        # model-error marker, one retry, then an escalation that names the
        # actual remedy (DRE-2312).
        facts = turn_facts or "the turn cap"
        if prior_dead >= turn_cap:
            return Decision(
                "hold",
                [
                    f"🚨 held-for-human ({TURN_TAG} cap reached): the agent ran "
                    f"out of steps {prior_dead + 1} times in a row — the last "
                    f"run hit {facts} and stopped mid-task. Both were full runs "
                    f"doing real work, so the evidence says this card does not "
                    f"fit inside one run: parked in Backlog with the "
                    f"'{HOLD_LABEL}' label until a human splits it into smaller "
                    f"pieces (or raises the turn budget).{run_suffix}"
                ],
            )
        return Decision(
            "requeue",
            [
                f"🪦 {TURN_TAG}: the agent ran out of steps — it hit {facts} "
                f"and stopped before opening a PR. That was a full run doing "
                f"real work, not a credentials or connection problem. "
                f"Requeued to Todo for one more attempt (turn exhaustion "
                f"{prior_dead + 1}/{turn_cap + 1}); how far an agent gets "
                f"varies run to run. If the next run hits the cap too, the card "
                f"needs splitting into smaller pieces or a larger turn "
                f"budget.{run_suffix}"
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


def park_unlanded_comment(run_url: str = "") -> str:
    """The receipt posted INSTEAD of the hold receipt when the park did not land.

    It still carries DEAD_TAG, because the death it reports was real and
    un-counting it would hand the card a budget it has already spent. What it
    does not do is claim a park that did not happen — that claim is what left
    DRE-2911 sitting In Progress for seven and a half hours with nothing
    retrying it.
    """
    run_suffix = f" Run: {run_url}" if run_url else ""
    return (
        f"🚨 {DEAD_TAG} cap reached — but the park did NOT land: Linear refused "
        f"the write, so this card is NOT in Backlog and does NOT carry the "
        f"'{HOLD_LABEL}' label. Neither was written rather than half of each, "
        f"so nothing here is claiming a hold that does not exist. This death is "
        f"still counted, and the reconcile sweep re-attempts the park on its "
        f"next pass.{run_suffix}"
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
             [--turn-exhaustion [--execution-file PATH]] [--run-url U]
             [--pre-agent [--failed-step NAME] [--rate-limited]]
      park <CARD>
      park-unlanded [--run-url U]

    With --turn-exhaustion, <prior_dead> is the card's `turn-exhaustion-requeue`
    count (its own budget) and --execution-file names the run's result JSON —
    read here, so decide() stays the no-I/O core, for the cap and spend the
    message quotes.

    `decide` prints (to stdout) the action on the first line, then a blank
    line, then the comment body. The workflow reads line 1 for the branch and
    posts the body. `park` performs the hold's two Linear writes atomically
    (DRE-2931) and exits nonzero when it wrote NEITHER; `park-unlanded` prints
    the receipt to post in that case.
    """
    usage = ("usage: dead_run.py decide <prior_dead> [--is-error] "
             "[--error-model M] [--cancelled] [--turn-exhaustion] "
             "[--execution-file PATH] [--pre-agent] [--failed-step NAME] "
             "[--rate-limited] [--run-url U] | park <CARD> | "
             "park-unlanded [--run-url U]")
    if not argv:
        print(usage)
        return 2
    cmd, *rest = argv
    if cmd == "park":
        return _cmd_park(rest[0] if rest else "")
    if cmd == "park-unlanded":
        print(park_unlanded_comment(_flag_value(rest, "--run-url")))
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
    error_model = _flag_value(rest, "--error-model") or None
    run_url = _flag_value(rest, "--run-url")
    exec_path = _flag_value(rest, "--execution-file")
    failed_step = _flag_value(rest, "--failed-step")
    turn_facts = ""
    if turn_exhaustion and exec_path:
        import check_agent_result  # local: only this branch needs the loader

        turn_facts = check_agent_result.turn_exhaustion_facts(
            check_agent_result._load_execution(exec_path)
        )
    d = decide(
        prior_dead,
        is_error=is_error,
        error_model=error_model,
        turn_exhaustion=turn_exhaustion,
        turn_facts=turn_facts,
        cancelled=cancelled,
        pre_agent=pre_agent,
        failed_step=failed_step,
        rate_limited=rate_limited,
        run_url=run_url,
    )
    print(d.action)
    print()
    print(d.comments[0])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
