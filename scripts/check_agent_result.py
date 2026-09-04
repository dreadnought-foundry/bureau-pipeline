#!/usr/bin/env python3
"""Gate on the agent's execution result (DRE-1346 Fix 1, stdlib only).

The Claude execution result JSON can end {"subtype": "success",
"is_error": true} — a usage-limit or API death mid-run that the workflow
previously reported as success, hiding the dead card behind a green
conclusion until a staleness sweep noticed.

Called from agent-task.yml after the agent step:

    python3 check_agent_result.py <execution-json-path> <branch> <pr-url> \
        <blocker-file> [--escalation-file <path>]

Exit 1 (fail the job, loudly) when:
  - the execution result has is_error == true, OR
  - there is no agent branch, no PR, no blocker note, and no escalation note
    (silent death).
Exit 0 otherwise. An honest blocker note OR an honest escalation note (the
agent intentionally stopped to ask the CEO a decision — DRE-1655) is
working-as-designed; absence of the result file alone is not failure (action
versions move it) when the run left real evidence (branch, PR, or note).

Whenever the result says is_error, the gate also prints WHY, from the
execution file's own result record (DRE-2435, see execution_result.py) — a
whitelist of scalar fields, never the transcript. It prints on the
--ignore-is-error path too: that is the death the Report step REQUEUES
silently, so this log line is the only trace it leaves behind.

DRE-2312 — WHICH KIND OF DEATH. `is_error: true` is not one fact, it is two,
and this module is where every caller learns which:

  * `turn_exhaustion` — the agent hit claude-code-action's turn ceiling
    (`subtype: error_max_turns`, `terminal_reason: max_turns`, "Reached
    maximum number of turns (60)"). It RAN, it spent minutes and dollars, and
    it stopped mid-task. Nothing about the service, the model or the
    credentials failed.
  * `api_death` — everything else that ends is_error: the transport/auth
    failures and mid-run usage-limit deaths DRE-1354 was written for.

The split matters because the outage story is expensive: "the AI service was
unavailable" sends the reader to the credential chain (`make cred-doctor`
exists because that hunt is a known time sink), and the retry policies differ.
Callers ask classify_death() / is_turn_exhaustion() / is_api_death(); nobody
re-derives it from `is_error` locally (agent-task.yml did, and DRE-2695's
turn-exhausted BUILD run was reported as an API/model death with a
`model-error:` marker while the shared predicate sat one import away).

    python3 check_agent_result.py classify <execution-json-path> \
        [--work-on-runner true|false]

prints exactly one of `turn_exhaustion` / `credential_expiry` / `api_death` /
`none` — the form the workflows call, so a shell branch never needs its own
is_error test.

DRE-3043 — THE CREDENTIAL AGED OUT WITH THE WORK FINISHED. `credential_expiry`
is the third class, and the only one that can be true of a run whose record
does NOT say is_error: the agent finished cleanly, the branch was ready, and
the installation token every git credential on the runner is built from had
died ten minutes earlier (run 33822932627, card DRE-3029 — 5,001 tests green,
none of it pushed). Reported as a silent death it reads "agent died with no PR
and no blocker note", which is false in the one way that matters: the run did
the work. The evidence is either GitHub's own refusal in the record
(`push_credential_refused`) or the Push rescue step's observation that
committed work for this card is on the runner and GitHub does not have it,
passed in as `--work-on-runner`.

DRE-2931 — DID THE AGENT START AT ALL. classify_death() answers "which kind of
death", and every answer it can give presumes there WAS a run. A run that dies
before claude-code-action is reached — a Linear write refused at `Card → In
Progress`, a context assembly that blew up — has no death class, because
nothing died: no turn was taken, no model was called, nothing was spent. On
2026-09-01 DRE-2911 spent two of its three dead-run strikes on exactly that,
20 seconds and $0 each, and the third one parked the card as "a human must
split/fix this".

agent_started() is the discriminator, and it reads only facts:

  * GitHub's own outcome for the agent step — `skipped` means the platform is
    telling us the step never ran, which is the strongest evidence available;
  * the execution record's `num_turns` — 0 turns is no attempt at the work;
  * the absence of an execution record entirely, which is what a pre-agent
    failure leaves behind;
  * a pushed branch, which proves the agent ran whatever the record says (some
    action versions move the result file — see failure_reason below).

    python3 check_agent_result.py started <execution-json-path> \
        [--claude-outcome <outcome>] [--branch <ref>]

prints `yes` / `no`. The Report step branches on it BEFORE it counts anything.
"""

from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from execution_result import (  # noqa: E402
    load_execution as _load_execution,
    print_failure_detail,
)

# The death classes classify_death() returns. Strings, not an enum: they cross
# a shell boundary (the `classify` CLI) into workflow `if` tests.
DEATH_NONE = "none"
DEATH_TURN_EXHAUSTION = "turn_exhaustion"
DEATH_API = "api_death"
# DRE-3043: the work is finished ON THE RUNNER and GitHub refused the push.
# Not a death of the agent, the model or the service — the run did the work and
# could not deliver it, because the installation token every git credential on
# the runner is built from lives one hour.
DEATH_CREDENTIAL_EXPIRY = "credential_expiry"

# claude-code-action's own names for hitting the turn ceiling. `subtype` is the
# canonical one; `terminal_reason`/`stop_reason` and the human sentence in
# `result` are carried too, because the observed payloads (portico PR #170,
# agent-bureau run 32791846359) did not all set the same field.
_TURN_CAP_SUBTYPES = ("error_max_turns",)
_TURN_CAP_REASONS = ("max_turns",)
_TURN_CAP_TEXT = "maximum number of turns"
# "Reached maximum number of turns (60)" — the cap the message can be built on.
_TURN_CAP_NUMBER = re.compile(r"maximum number of turns\s*\((\d+)\)", re.I)

# The POSITIVE signature of a genuine transport/auth failure (DRE-2365): it
# returns in well under a second, on one turn, having spent nothing. A run that
# spent money and took minutes did not fail to reach the service.
_OUTAGE_MAX_DURATION_MS = 1000

# What GitHub and git say when the credential is dead (DRE-3043). git's two
# sentences are what `git push` prints over HTTPS; `bad credentials` and
# `http 401`/`http 403` are `gh`'s. All GitHub-specific on purpose: a bare
# "401" would swallow an Anthropic auth failure, which is an api_death.
_PUSH_REFUSAL_TEXT = (
    "fatal: authentication failed",
    "remote: invalid username or password",
    "could not read username for 'https://github.com'",
    "bad credentials",
    "http 401",
    "http 403",
)


def _truthy(value: str) -> bool:
    """A shell boolean. The workflow passes a step output, which is the string
    "true"/"false" (or empty when the step was skipped)."""
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def _number(execution: dict, field: str) -> int | float | None:
    """A numeric whitelist field, or None. bools are not numbers here — none of
    these fields is ever legitimately one."""
    value = execution.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def has_service_outage_signature(execution: dict | None) -> bool:
    """True when the record carries the positive signature of a REAL outage.

    This is what makes the split safe rather than guesswork: rather than
    inferring an outage from the ABSENCE of `error_max_turns`, we can point at
    what a failure to reach the service actually looks like — sub-second
    `duration_ms`, `num_turns: 1`, `total_cost_usd: 0`. Used as a guard: a run
    that never got a turn and spent nothing cannot have exhausted a 60-turn
    budget, whatever else its record says.
    """
    if not isinstance(execution, dict):
        return False
    turns = _number(execution, "num_turns")
    cost = _number(execution, "total_cost_usd")
    duration = _number(execution, "duration_ms")
    if turns is None or cost is None or duration is None:
        return False
    return turns <= 1 and cost == 0 and duration < _OUTAGE_MAX_DURATION_MS


def _turn_cap_evidence(execution: dict) -> bool:
    """True when the record positively says the run hit the turn ceiling."""
    if str(execution.get("subtype") or "").strip() in _TURN_CAP_SUBTYPES:
        return True
    for field in ("terminal_reason", "stop_reason"):
        if str(execution.get(field) or "").strip() in _TURN_CAP_REASONS:
            return True
    for field in ("result", "errors", "error"):
        value = execution.get(field)
        if not value:
            continue
        text = value if isinstance(value, str) else json.dumps(value, default=str)
        if _TURN_CAP_TEXT in text.lower():
            return True
    return False


def push_credential_refused(execution: dict | None) -> bool:
    """True when the record positively says GitHub refused a credentialed
    write (DRE-3043).

    The strings are GitHub's and git's own, never a generic "401": an
    Anthropic 401 is an api_death and reads nothing like these. Same shape as
    _turn_cap_evidence — a POSITIVE signature, so the class is never inferred
    from the absence of another one.
    """
    if not isinstance(execution, dict):
        return False
    for field in ("result", "errors", "error"):
        value = execution.get(field)
        if not value:
            continue
        text = value if isinstance(value, str) else json.dumps(value, default=str)
        lowered = text.lower()
        if any(marker in lowered for marker in _PUSH_REFUSAL_TEXT):
            return True
    return False


def classify_death(execution: dict | None, *, work_on_runner: bool = False) -> str:
    """Which kind of death this execution result records (DRE-2312).

    DEATH_TURN_EXHAUSTION — it ran out of turns: a failed ATTEMPT, retried at
    most once and then escalated with the real reason.
    DEATH_CREDENTIAL_EXPIRY — the work is on the runner and GitHub refused the
    push (DRE-3043).
    DEATH_API — an API/model death: the outage path, unchanged (bounded
    retries, model fallback, outage wording).
    DEATH_NONE — it did not die (or there is no result record to say so).

    `work_on_runner` is what the Push rescue step OBSERVED — committed work for
    this card that GitHub does not have — never something read out of the
    transcript. It is a keyword with a False default so every existing caller
    (fix_dead_run, medic_retry, dead_run) keeps the answer it had.

    ORDER MATTERS, and it is the order of the evidence's strength:

      * an OUTAGE signature first — a run that returned in 400ms on one turn
        having spent nothing did not push anything and did not exhaust a
        budget, whatever else its record says (DRE-2365);
      * TURN EXHAUSTION next, because an agent that commits its RED tests and
        then runs out of steps ALSO leaves work on the runner. The budget
        ceiling is the honest story there, and it is the one with a remedy;
      * the CREDENTIAL last of the three. GitHub's own refusal in the record
        beats an api_death, because a push GitHub refused is not the service
        being unreachable — but it is only read on a record that already says
        is_error, so no existing caller's answer moves. `work_on_runner` is
        the route that does not need the record to say anything at all, and it
        is the one the incident needed: the DRE-3029 agent finished CLEANLY and
        reported in prose that it could not push, so its record carries no
        error string to match on.
    """
    if is_error_death(execution):
        if has_service_outage_signature(execution):
            # Nothing that returned in 400ms on one turn exhausted 60 turns.
            return DEATH_API
        if _turn_cap_evidence(execution):
            return DEATH_TURN_EXHAUSTION
        if push_credential_refused(execution):
            return DEATH_CREDENTIAL_EXPIRY
    if work_on_runner:
        return DEATH_CREDENTIAL_EXPIRY
    if is_error_death(execution):
        return DEATH_API
    return DEATH_NONE


def agent_started(
    execution: dict | None,
    *,
    claude_outcome: str = "",
    branch_exists: bool = False,
) -> bool:
    """True when this run actually consumed an attempt at the card's work.

    False means the run died BEFORE the agent — a platform fault against the
    RUN, never a strike against the card (DRE-2931). Read in this order, most
    authoritative first:

      1. `claude_outcome == "skipped"` — GitHub itself saying the agent step
         never ran, because a step before it failed. Nothing else can override
         that, so it is checked first.
      2. `branch_exists` — the agent pushed. It ran, whatever the record says.
         This guard is why the absence of a result file cannot silently stop a
         genuine silent death from counting.
      3. `num_turns` — 0 turns is no attempt; 1 or more is one. A record with
         no turn count at all (the DRE-1346 legacy shape) still proves the
         action produced a result, so it counts as started.
      4. No record at all — the shape a pre-agent failure leaves behind.

    Note the boundary against DRE-2365's outage signature: a transport/auth
    death returns in 400ms on ONE turn having spent nothing. One turn means the
    model WAS called and refused, so that run started and still counts.
    """
    if (claude_outcome or "").strip().lower() == "skipped":
        return False
    if branch_exists:
        return True
    if not isinstance(execution, dict):
        return False
    turns = _number(execution, "num_turns")
    if turns is None:
        return True
    return turns >= 1


def is_error_death(execution: dict | None) -> bool:
    """True when the execution result records a mid-run DEATH ({"is_error":
    true}) — either class. The single source of truth for is_error detection.

    It says the run died, NOT why: the docstring here used to assert that
    is_error MEANS an API/model death, and every reader downstream inherited
    that (DRE-2312). Ask classify_death() before telling anyone a story about
    the AI service.
    """
    return execution is not None and execution.get("is_error") is True


def is_turn_exhaustion(execution: dict | None) -> bool:
    """True when the run died by hitting the turn cap."""
    return classify_death(execution) == DEATH_TURN_EXHAUSTION


def is_api_death(execution: dict | None) -> bool:
    """True when the run died of an API/model failure — the outage path."""
    return classify_death(execution) == DEATH_API


def turn_exhaustion_facts(execution: dict | None) -> str:
    """A human clause naming the cap and what the run actually spent, e.g.
    "the 60-turn cap after 60 turns and $4.72".

    Every turn-exhaustion message is built on this: the operator needs the
    number the run hit and the budget it burned to judge whether the card needs
    splitting. Degrades to "the turn cap" when the record carries no numbers —
    it never prints a None.
    """
    execution = execution if isinstance(execution, dict) else {}
    cap = None
    for field in ("result", "errors", "error"):
        value = execution.get(field)
        if not value:
            continue
        text = value if isinstance(value, str) else json.dumps(value, default=str)
        m = _TURN_CAP_NUMBER.search(text)
        if m:
            cap = m.group(1)
            break
    if cap is None:
        turns = _number(execution, "num_turns")
        cap = str(int(turns)) if turns else None
    head = f"the {cap}-turn cap" if cap else "the turn cap"
    spent = []
    turns = _number(execution, "num_turns")
    if turns:
        spent.append(f"{int(turns)} turns")
    cost = _number(execution, "total_cost_usd")
    if cost:
        spent.append(f"${cost:.2f}")
    return f"{head} after {' and '.join(spent)}" if spent else head


def failure_reason(
    execution: dict | None,
    *,
    branch_exists: bool,
    pr_exists: bool = False,
    blocker_note: bool = False,
    escalation_note: bool = False,
    ignore_is_error: bool = False,
    claude_outcome: str = "",
) -> str | None:
    """Why this run should fail, or None if it is acceptable.

    `ignore_is_error` (DRE-1354): an is_error death is now handled by the Report
    step's model-fallback requeue (it switches model + counts toward the hold
    cap), so the agent-task gate no longer hard-fails on it — a hard fail would
    trigger the medic to re-run the job on the SAME model, bypassing the cap
    (the DRE-1300 18×-loop bug). The gate still fails on a no-evidence silent
    death so a truly lost run stays loud.

    `claude_outcome` (DRE-2074): the agent step's Actions outcome. "cancelled"
    means the agent was KILLED (job timeout / external cancel) while still
    working — no evidence is expected, so it is not a silent death and must
    not fail the gate (a red gate summons the medic to re-run a healthy-but-
    slow card). "skipped" (DRE-2931) is the same shape from the other end: a
    step BEFORE the agent failed, so the agent never ran and no branch, PR or
    note was ever possible. Failing the gate there sends the medic to re-run a
    job whose only fault was the platform's — and on 2026-09-01 that platform
    fault was an exhausted Linear quota, which a rerun can only deepen (the
    DRE-1921 loop). Both waive ONLY the silent-death reason: an is_error record
    is affirmative evidence of a model death and still fails without the ignore
    flag. The reconcile sweep owns the requeue off the run's real conclusion.
    """
    if not ignore_is_error and is_error_death(execution):
        return "execution result has is_error=true"
    if claude_outcome in ("cancelled", "skipped"):
        return None
    if (
        not branch_exists
        and not pr_exists
        and not blocker_note
        and not escalation_note
    ):
        return "no agent branch, no PR, no blocker note, and no escalation note"
    return None


def main(argv: list[str]) -> int:
    # `classify <execution-json-path>` (DRE-2312): print the death class and
    # exit 0. This is the form the workflows call — one shared predicate, no
    # inline `is_error` test in a shell step. Handled before the positional
    # parsing below; "classify" is not a plausible execution-file path.
    #
    # `--work-on-runner <bool>` (DRE-3043) is the Push rescue step's own
    # observation — committed work for this card that GitHub does not have —
    # passed in rather than guessed at here.
    if argv and argv[0] == "classify":
        rest = argv[1:]
        work_on_runner = False
        if "--work-on-runner" in rest:
            i = rest.index("--work-on-runner")
            work_on_runner = _truthy(rest[i + 1] if i + 1 < len(rest) else "")
            del rest[i : i + 2]
        print(classify_death(
            _load_execution(rest[0] if rest else ""),
            work_on_runner=work_on_runner,
        ))
        return 0
    # `started <execution-json-path> [--claude-outcome X] [--branch REF]`
    # (DRE-2931): did this run consume an attempt at the work? Same shape and
    # same reason as `classify` — the Report step must not re-derive it from a
    # shell test, which is how DRE-2695's turn exhaustion got the wrong story.
    if argv and argv[0] == "started":
        rest = argv[1:]
        outcome, branch = "", ""
        for flag in ("--claude-outcome", "--branch"):
            if flag in rest:
                i = rest.index(flag)
                value = rest[i + 1] if i + 1 < len(rest) else ""
                if flag == "--claude-outcome":
                    outcome = value
                else:
                    branch = value
                del rest[i : i + 2]
        print("yes" if agent_started(
            _load_execution(rest[0] if rest else ""),
            claude_outcome=outcome,
            branch_exists=bool(branch.strip()),
        ) else "no")
        return 0
    # Optional trailing --ignore-is-error flag (DRE-1354): the Report step owns
    # the is_error→model-fallback requeue, so the gate should not hard-fail on it
    # (a hard fail re-runs the job on the same model via the medic).
    ignore_is_error = "--ignore-is-error" in argv
    argv = [a for a in argv if a != "--ignore-is-error"]
    # Optional --escalation-file <path> (DRE-1655): the agent intentionally
    # stopped to ask the CEO a decision. Like a blocker note, it is an honest,
    # designed outcome — not a silent death — so its presence keeps the gate green.
    escalation_file = ""
    if "--escalation-file" in argv:
        i = argv.index("--escalation-file")
        escalation_file = (argv[i + 1] if i + 1 < len(argv) else "")
        del argv[i : i + 2]
    # Optional --claude-outcome <outcome> (DRE-2074): the agent step's Actions
    # outcome. "cancelled" = the run was killed externally mid-build, not a
    # silent death — the gate stays green and reconcile owns the follow-up.
    claude_outcome = ""
    if "--claude-outcome" in argv:
        i = argv.index("--claude-outcome")
        claude_outcome = (argv[i + 1] if i + 1 < len(argv) else "")
        del argv[i : i + 2]
    exec_path, branch, pr_url, blocker_file = (argv + ["", "", "", ""])[:4]

    def _has_note(path: str) -> bool:
        return bool(path) and os.path.isfile(path) and os.path.getsize(path) > 0

    execution = _load_execution(exec_path)
    # DRE-2435: say why the run died before saying what we do about it. This
    # runs whatever the gate decides — an is_error the Report step requeues
    # (--ignore-is-error) still leaves the log as its only evidence, and
    # "1 turn, $0" alone reads identically for an expired token, an
    # overloaded API, a refusal and a bad model id.
    print_failure_detail(execution, "agent result gate")
    reason = failure_reason(
        execution,
        branch_exists=bool(branch.strip()),
        pr_exists=bool(pr_url.strip()) and pr_url.strip() != "null",
        blocker_note=bool(blocker_file) and os.path.isfile(blocker_file),
        escalation_note=_has_note(escalation_file),
        ignore_is_error=ignore_is_error,
        claude_outcome=claude_outcome,
    )
    if reason:
        print(f"agent result gate: FAIL — {reason}")
        return 1
    print("agent result gate: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
