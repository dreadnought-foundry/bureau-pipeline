#!/usr/bin/env python3
"""The one death-cause receipt every model-running run writes (DRE-4340).

On 2026-09-19 the fleet's failures were counted for the first time: 1,310 out
of 62,451 runs over 22 days. **69 of them — $97 of model work — could not be
attributed to any cause at all**, because the workflow they died in wrote no
receipt saying why. The count exists; it cannot be repeated tomorrow without
guessing. The same blind spot is why "a subscription ran out" was believed to
be the main failure for weeks when it is in fact the sixth cause (49 runs,
$20) and the 150-turn cap is the first ($1,559).

Nothing here classifies anything new. Every answer is READ from the module
that already owns it:

  * `check_agent_result.classify_death` — `turn_exhaustion` / `api_death` /
    `credential_expiry` / `none`, the death classes the workflows already
    branch on (DRE-2312, DRE-3043).
  * `check_agent_result.agent_started` — did this run consume an attempt at
    all (DRE-2931). A run that died before the model started wasted no model
    work, and filing it as a model death inflates every wasted-work figure
    downstream.
  * `death_cause.cause` / `.reset` / `.quote` — WHICH WALL an api_death hit
    (`throttled` / `capped` / `revoked`), the wall's stated reset, and the
    provider's own error body (DRE-4129).
  * `execution_result.spend_scalars` — what the run spent, as numbers.
  * `usage_reading.run_identity` / `.delivery_id` / `.card_from_ref` — the
    run's identity and the delivery key, byte-for-byte the shape its sibling
    reading already writes (DRE-4338), so The Record reads one grammar.

WHAT IS NEW HERE IS THE NAME FOR TWO THINGS NOTHING ELSE NAMES, and both are
about honesty rather than classification:

  * `setup` — the run died with no turn taken and nothing spent. The death
    classes all presume there WAS a run; this one says there was not.
  * `unknown` — the run died and nothing can say why. It is written WITH the
    reason it could not be classified, because the 69 unattributed failures
    are the whole point: a blank and a guess are the two ways to lose them
    again. `cancelled` is the third name, and it is not a death at all
    (DRE-2074: the job timeout and an external cancel both land there, and
    counting either as a death once killed three live builds).

IT CANNOT MASK A FAILURE. `emit` returns 0 whatever happens to it, and the
workflow step carries `if: always()` + `continue-on-error: true`, so the
receipt never changes the job's own conclusion in either direction — it does
not rescue a red run and it cannot redden a green one.

WHAT IT PUBLISHES, AND THAT THIS IS ON PURPOSE. The artifact is world-readable
for its 90 days wherever the repo is public — and this repo is, and dispatches
these workflows against itself (DRE-1929 self-hosting). What lands there is
the death class, the wall, the provider's error body and the run's own spend.
The quote reaches it ONLY through `death_cause`, whose single way into the
record is `execution_result._DIAGNOSTIC_FIELDS` — the audited whitelist — so
the transcript, `env` and the bare `error` field are structurally out of
reach. `execution_result.py` already prints the same class of fact
(`total_cost_usd`, `num_turns`) to the same public logs on every run.

Usage (from a workflow step, after the last model step):

    python3 death_receipt.py emit --execution-file <path> --out <path> \
        --job-status "$JOB_STATUS" [--step-outcome <outcome>] \
        [--model-ran true] [--branch <ref>] [--work-on-runner true] \
        [--card DRE-N] \
        [--github-output "$GITHUB_OUTPUT"] [--step-summary "$GITHUB_STEP_SUMMARY"]

`scripts/check_death_receipts.py` discovers every reusable workflow that runs
a model and fails the build when one of them does not call this.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import check_agent_result  # noqa: E402 — the death classes and agent_started
import death_cause  # noqa: E402 — which wall, its reset, its quote
import execution_result  # noqa: E402 — the loader and the spend whitelist
import usage_reading  # noqa: E402 — the run identity its sibling already writes

#: The body's own version tag. NOT the Record envelope's `record.v1` — that is
#: the wrapper's schema; this names the document inside `body_raw`.
SCHEMA = "run-death-receipt.v1"

# THE CAUSE VOCABULARY, AND ONLY ONE SPELLING OF IT. The four death classes
# are `check_agent_result`'s own constants, bound here rather than retyped: a
# second spelling is how two readers of the same corpse stop agreeing.
CAUSE_NONE = check_agent_result.DEATH_NONE
CAUSE_TURN_EXHAUSTION = check_agent_result.DEATH_TURN_EXHAUSTION
CAUSE_API = check_agent_result.DEATH_API
CAUSE_CREDENTIAL_EXPIRY = check_agent_result.DEATH_CREDENTIAL_EXPIRY

#: The run died before the model started (DRE-2931). No turn was taken and
#: nothing was spent, so it is not a model death and must not be counted as
#: wasted model work.
CAUSE_SETUP = "setup"
#: Stopped from outside — the job timeout, an external cancel. NOT a death
#: (DRE-2074), and `died` is False.
CAUSE_CANCELLED = "cancelled"
#: The run died and nothing can say why. Always written WITH the reason it
#: could not be classified; never blank, never a guess.
CAUSE_UNKNOWN = "unknown"

#: Every name this module can write. A reader that meets one outside this set
#: is reading a second emitter.
CAUSES = (
    CAUSE_NONE,
    CAUSE_TURN_EXHAUSTION,
    CAUSE_API,
    CAUSE_CREDENTIAL_EXPIRY,
    CAUSE_SETUP,
    CAUSE_CANCELLED,
    CAUSE_UNKNOWN,
)

#: WHERE the run died. `setup` is before the model, `model` is at or after it.
#: The split is what keeps the wasted-work figures honest.
PHASE_SETUP = "setup"
PHASE_MODEL = "model"

#: The `died: false` answers. Everything else in CAUSES is a death.
_NOT_A_DEATH = (CAUSE_NONE, CAUSE_CANCELLED)

#: GitHub's own outcomes for a step that REACHED the model. The mirror image of
#: the `skipped` that `check_agent_result.agent_started` already reads as "the
#: step never ran", and the same class of evidence: the platform saying what
#: happened to the step, not the transcript saying what happened in it. It is
#: read here and nowhere else — the death CLASSIFICATION is unchanged — because
#: two of the model steps in this fleet write no execution record at all (the
#: groomer's judged read and the planner's classifier go through
#: `planning_classify`, not the vendor action), and without it every one of
#: their deaths would be filed as a setup death that wasted nothing.
_STEP_REACHED_THE_MODEL = ("success", "failure")

# The reasons, one per answer. Sentences rather than codes: they are read by a
# human in a job summary and by a query over the artifacts, and the point of
# the card is that a cause with no reason is how 69 failures became unreadable.
_REASONS = {
    CAUSE_NONE: "the run did not die",
    CAUSE_CANCELLED: (
        "the run was cancelled from outside — a job timeout or an external "
        "cancel, which is not a death (DRE-2074)"
    ),
    CAUSE_SETUP: (
        "the run died before the model started: no turn was taken and nothing "
        "was spent, so this is a setup death and not wasted model work "
        "(check_agent_result.agent_started)"
    ),
    CAUSE_TURN_EXHAUSTION: (
        "the agent reached its turn ceiling — a full run of real work that ran "
        "out of steps, not a fault of the model, the service or the setup"
    ),
    CAUSE_CREDENTIAL_EXPIRY: (
        "the work was finished on the runner and GitHub refused the push — the "
        "installation token every git credential is built from lives one hour "
        "(DRE-3043)"
    ),
    CAUSE_API: "the execution record reports an API/model death (is_error)",
}

# The two `unknown` sentences. They differ on purpose: a reader must be able to
# tell "nothing was written down" from "the record says the model was fine"
# without opening the run.
_UNKNOWN_NO_RECORD = (
    "the run died and no execution record was written, so nothing says why — "
    "recorded as unknown rather than guessed"
)
_UNKNOWN_RECORD_IS_CLEAN = (
    "the run died after the model finished cleanly (the execution record "
    "reports no error), so the failure is outside the model step — recorded as "
    "unknown rather than guessed"
)


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _lower(value: object) -> str:
    return _text(value).lower()


def classify(
    execution: dict | None,
    *,
    job_status: str = "",
    step_outcome: str = "",
    branch_exists: bool = False,
    work_on_runner: bool = False,
    model_ran: bool = False,
) -> tuple[str, str, bool]:
    """`(cause, reason, model_started)` for one run.

    ORDER, and why it is this one:

      1. THE RECORD FIRST. A death class the execution record itself evidences
         is the strongest answer available and outranks any status: a run
         whose record says `error_max_turns` exhausted its turns whatever
         GitHub later reported for the job.
      2. CANCELLED NEXT. Nothing died, so no death class applies.
      3. THEN "did the job fail at all". A green job that wrote a clean record
         is `none`.
      4. A FAILED JOB WITH NO MODEL RUN IS A SETUP DEATH (DRE-2931), and a
         failed job with a model run nothing can explain is `unknown` — with
         the reason, which is different for a missing record and a clean one.

    Anything that is not a result RECORD is no record: `load_execution`
    returns a dict or None, but a file holding a bare list or a string parses
    to neither, and a receipt that raised there would be a receipt not
    written for exactly the malformed runs this card exists to count.
    """
    if not isinstance(execution, dict):
        execution = None
    started = check_agent_result.agent_started(
        execution, claude_outcome=step_outcome, branch_exists=branch_exists)
    if not started and _lower(step_outcome) != "skipped":
        # `skipped` is GitHub saying the step never ran and nothing overrides
        # it — the order agent_started already sets, kept here.
        started = _lower(step_outcome) in _STEP_REACHED_THE_MODEL or model_ran
    klass = check_agent_result.classify_death(
        execution, work_on_runner=work_on_runner)
    if klass != CAUSE_NONE:
        return klass, _REASONS[klass], started
    if _lower(job_status) == "cancelled" or _lower(step_outcome) == "cancelled":
        return CAUSE_CANCELLED, _REASONS[CAUSE_CANCELLED], started
    if _lower(job_status) != "failure":
        return CAUSE_NONE, _REASONS[CAUSE_NONE], started
    if not started:
        return CAUSE_SETUP, _REASONS[CAUSE_SETUP], started
    reason = (_UNKNOWN_RECORD_IS_CLEAN if isinstance(execution, dict) and execution
              else _UNKNOWN_NO_RECORD)
    return CAUSE_UNKNOWN, reason, started


def receipt(
    execution: dict | None,
    *,
    run: dict,
    repo: str | None,
    read_at: datetime,
    job_status: str = "",
    step_outcome: str = "",
    branch_exists: bool = False,
    work_on_runner: bool = False,
    model_ran: bool = False,
    card: str | None = None,
) -> dict:
    """The document the artifact holds — the Record's `body_raw` for one run.

    `read_at` must be timezone-aware; it is written as ISO-8601 UTC and as
    epoch milliseconds, and the latter is a fact-key part exactly as it is in
    the sibling usage reading, so a run's death and its usage reading key the
    same way.
    """
    if read_at.tzinfo is None or read_at.utcoffset() is None:
        raise ValueError("read_at must be timezone-aware")
    if not isinstance(execution, dict):  # see classify(): no record is no record
        execution = None
    read_at = read_at.astimezone(UTC)
    epoch_ms = int(read_at.timestamp() * 1000)
    cause, reason, started = classify(
        execution,
        job_status=job_status,
        step_outcome=step_outcome,
        branch_exists=branch_exists,
        work_on_runner=work_on_runner,
        model_ran=model_ran,
    )
    died = cause not in _NOT_A_DEATH
    # WHICH WALL, when one was hit. `death_cause` answers positively or not at
    # all, and its turn-cap veto comes first, so a run that ran out of steps
    # never reports a wall however its closing message reads.
    wall = death_cause.cause(execution)
    wall_reset = death_cause.reset(execution, read_at) if wall else None
    return {
        "schema": SCHEMA,
        "delivery_id": usage_reading.delivery_id(run, epoch_ms),
        "repo": repo,
        "read_at": read_at.strftime("%Y-%m-%dT%H:%M:%S.")
                   + f"{read_at.microsecond // 1000:03d}Z",
        "read_at_epoch_ms": epoch_ms,
        "run": run,
        "card": card if card else usage_reading.card_from_ref(run.get("ref")),
        "died": died,
        "phase": PHASE_SETUP if cause == CAUSE_SETUP else PHASE_MODEL,
        "cause": cause,
        "cause_reason": reason,
        "model_started": started,
        "job_status": _text(job_status) or None,
        "step_outcome": _text(step_outcome) or None,
        # The finer grain under an api_death: throttled clears in minutes,
        # capped clears at a stated reset hours away, revoked never clears.
        "wall": {
            "cause": wall,
            "resets_at": wall_reset.strftime("%Y-%m-%dT%H:%M:%SZ") if wall_reset else None,
        },
        # The provider's own error body, capped at execution_result._VALUE_CAP
        # and reachable only through the audited whitelist.
        "quote": death_cause.quote(execution),
        # What the run actually spent. Empty on a setup death, because a run
        # that took no turn spent nothing — that emptiness is the property.
        "spend": execution_result.spend_scalars(execution),
        "record": {
            "role": "body_raw",
            "fact_key_parts": ["run.id", "run.attempt", "read_at_epoch_ms"],
            "declared_by": ("the Record card names the source and the event "
                            "(config/record-contract.json); this emitter "
                            "declares neither"),
        },
    }


def summary_line(doc: dict) -> str:
    """One line for the job summary and the log."""
    parts = [f"Run death: {doc.get('cause')}"]
    wall = (doc.get("wall") or {}).get("cause")
    if wall:
        parts.append(f"wall {wall}")
    spend = doc.get("spend") or {}
    if "num_turns" in spend:
        parts.append(f"{spend['num_turns']} turns")
    if "total_cost_usd" in spend:
        parts.append(f"${spend['total_cost_usd']}")
    return " · ".join(parts) + f" — {doc.get('cause_reason')}"


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #

def _append(path: str | None, line: str) -> None:
    if not path:
        return
    with open(path, "a") as f:
        f.write(line + "\n")


def _truthy(value: str) -> bool:
    return _lower(value) in ("true", "yes", "1")


def emit(args: argparse.Namespace) -> int:
    execution = execution_result.load_execution(args.execution_file)
    doc = receipt(
        execution,
        run=usage_reading.run_identity(),
        repo=usage_reading._text(os.environ.get("GITHUB_REPOSITORY")),
        read_at=datetime.now(UTC),
        job_status=args.job_status,
        step_outcome=args.step_outcome,
        branch_exists=bool(_text(args.branch)),
        work_on_runner=_truthy(args.work_on_runner),
        model_ran=_truthy(args.model_ran),
        card=args.card or None,
    )
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(doc, f, indent=1)
        f.write("\n")
    line = summary_line(doc)
    print(line)
    print(f"death receipt written: {args.out} (delivery {doc['delivery_id']})")
    _append(args.step_summary, line)
    _append(args.github_output, f"path={args.out}")
    _append(args.github_output, f"cause={doc['cause']}")
    _append(args.github_output, f"died={'true' if doc['died'] else 'false'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="command", required=True)
    e = sub.add_parser("emit", help="write the run's death-cause receipt")
    e.add_argument("--execution-file", required=True)
    e.add_argument("--out", required=True)
    # `${{ job.status }}` at the receipt step: success / failure / cancelled.
    e.add_argument("--job-status", default="")
    # The model step's own outcome. `skipped` is GitHub saying the step never
    # ran, which is the strongest evidence of a setup death there is.
    e.add_argument("--step-outcome", default="")
    # A pushed branch proves the agent ran whatever the record says.
    e.add_argument("--branch", default="")
    # What the Push rescue step OBSERVED — committed work GitHub does not have.
    e.add_argument("--work-on-runner", default="")
    # `true` when the workflow can say a model step EXECUTED but has no single
    # step outcome to hand over — a job with several mutually exclusive routes
    # (plan.yml), where only one route's model steps ever run. Never overrides
    # a `--step-outcome skipped`.
    e.add_argument("--model-ran", default="")
    e.add_argument("--card", default="")
    e.add_argument("--github-output", default="")
    e.add_argument("--step-summary", default="")
    args = parser.parse_args(argv)
    try:
        return emit(args)
    except Exception as exc:  # never a red build: the receipt is not the work
        print(f"death receipt: skipped ({type(exc).__name__}: {exc})")
        # ...and say so where a human looks, not only in the log. The step runs
        # `continue-on-error: true`, so a systematically broken emitter would
        # otherwise render as SILENCE in the job summary — indistinguishable
        # from a run that never died (standards/console-honesty.md rule 2).
        try:
            _append(args.step_summary,
                    f"Run death: receipt could not be written ({type(exc).__name__})")
        except OSError:  # the summary file itself is what broke — the log has it
            pass
        return 0


if __name__ == "__main__":
    sys.exit(main())
