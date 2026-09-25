#!/usr/bin/env python3
"""A planner step refused for capacity re-runs on the next rung in the same job (DRE-3970).

CEO decision 2026-09-14: "When it's out of Fable 5, it should just default back
to Opus 5." The classifier and the groomer make their call again on the next
rung themselves (`planning_classify.call_with_capacity_fallback`). The planner
steps in plan.yml are claude-code-action steps, so the same guarantee is built
from steps, and this module is their two decisions:

    python3 planner_capacity.py decide <execution-file> --model <m> \\
        [--role planner] [--github-output <path>]

writes `retry=true|false`, `model=<next rung>`, `effort_arg=<--effort level>`
(empty when that rung declares none, DRE-4836) and `because=<signature>`. It
says `true` only for a model refused for CAPACITY — read by the one detector,
`model_fallback.capacity_refusal`, which a run that did real work (turn cap,
more than one turn, any spend) never satisfies — and only when the ladder has a
rung below.

    python3 planner_capacity.py finish --first-outcome <o> --second-outcome <o> \\
        --first-execution-file <f> --second-execution-file <f> --asked <m> \\
        --second-model <m> --because <sig> [--required] [--github-output <path>]

picks the attempt that counts (the re-run when there was one) and writes
`outcome`, `execution_file`, `model` (the model that attempt was run on — what
a death marker names) and `receipt`. The receipt is non-empty when the planner
did NOT get the model it asked for: a re-run on the next rung, or the CLI's own
fallback firing silently inside the step. It names the model that ANSWERED,
read from the record's `modelUsage`, never the request
(`standards/console-honesty.md` rule 2). With `--required` it exits 1 when the
attempt that counts did not succeed, which is how a step that used to fail the
job still does.

No network, no probe (DRE-3652): both read files the steps already wrote.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import model_fallback  # noqa: E402
from execution_result import load_execution  # noqa: E402

ROLE = "planner"
_DONE = ("success", "failure")


def _text(record: dict) -> str:
    parts = []
    for field in ("result", "errors", "error"):
        value = record.get(field)
        if value:
            parts.append(value if isinstance(value, str) else json.dumps(value, default=str))
    return "\n".join(parts)


def decide(record, model: str, role: str = ROLE) -> dict:
    """`retry`/`model`/`effort_arg`/`because` for one failed planner attempt.

    `effort_arg` is the `--effort` argument for the rung being retried ON, or
    empty when that rung declares no level (DRE-4836). The retry is a fresh
    claude-code-action step with its own `claude_args`, so it needs the level
    of the model IT runs, not of the one that just refused.
    """
    no = {"retry": "false", "model": "", "effort_arg": "", "because": ""}
    if not isinstance(record, dict) or record.get("is_error") is not True:
        return no
    because = model_fallback.capacity_refusal(_text(record), record=record)
    below = model_fallback.fallback_for(role, model) if because else None
    if not below:
        return no
    level = model_fallback.effort_for(below)
    return {
        "retry": "true",
        "model": below,
        "effort_arg": f"--effort {level}" if level else "",
        "because": because,
    }


def answered(record, asked: str | None) -> str | None:
    """The model that answered this attempt, through the one seam that reads
    `modelUsage` (`planning_classify.answered_model`, DRE-3083).

    One narrowing first: a ledger entry that wrote NOTHING did not answer. A
    refused rung can be billed with zero output beside the rung that did the
    work, and the seam prefers the asked model whenever it is present — which
    is exactly the silent fall this receipt exists to name."""
    if not isinstance(record, dict):
        return None
    usage = record.get("modelUsage")
    if isinstance(usage, dict):
        def wrote(entry) -> bool:
            try:
                return int((entry or {}).get("outputTokens") or 0) > 0
            except (TypeError, ValueError, AttributeError):
                return False
        record = dict(record, modelUsage={k: v for k, v in usage.items() if wrote(v)})
    from planning_classify import answered_model
    return answered_model(record, asked)


def receipt(asked: str, answered_by: str | None, because: str | None) -> str:
    """`DEGRADED <asked> (asked) / <answered> (answered) — …`, the classifier's
    grammar. Composed here, not in YAML, so it is tested."""
    asked = " ".join((asked or "").split()) or "unknown"
    by = " ".join((answered_by or "").split()) or "unknown"
    line = f"DEGRADED {asked} (asked) / {by} (answered)"
    if because:
        line += f" — {asked} out of capacity ({' '.join(str(because).split())})"
    return line


def finish(*, first_outcome: str, second_outcome: str, first_exec: str,
           second_exec: str, asked: str, second_model: str, because: str) -> dict:
    retried = second_outcome in _DONE
    if retried:
        outcome, exec_file, model = second_outcome, second_exec, second_model or asked
    else:
        outcome, exec_file, model = first_outcome, first_exec, asked
    record = load_execution(exec_file) if exec_file else None
    by = answered(record, model)
    line = ""
    if retried:
        line = receipt(asked, by or (model if outcome == "success" else None), because)
    elif outcome == "success" and by and by != asked:
        line = receipt(asked, by, None)
    return {"outcome": outcome, "execution_file": exec_file or "", "model": model,
            "answered": by or "", "receipt": line}


def _write(path: str | None, pairs: dict) -> None:
    for key, value in pairs.items():
        print(f"{key}={value}")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            for key, value in pairs.items():
                fh.write(f"{key}={' '.join(str(value).split())}\n")
    except OSError as exc:
        print(f"planner_capacity: could not write step outputs: {exc}", file=sys.stderr)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    d = sub.add_parser("decide")
    d.add_argument("execution_file")
    d.add_argument("--model", required=True)
    d.add_argument("--role", default=ROLE)
    d.add_argument("--github-output", default=None)

    f = sub.add_parser("finish")
    f.add_argument("--first-outcome", default="")
    f.add_argument("--second-outcome", default="")
    f.add_argument("--first-execution-file", default="")
    f.add_argument("--second-execution-file", default="")
    f.add_argument("--asked", default="")
    f.add_argument("--second-model", default="")
    f.add_argument("--because", default="")
    f.add_argument("--required", action="store_true")
    f.add_argument("--github-output", default=None)

    args = parser.parse_args(argv)
    if args.command == "decide":
        got = decide(load_execution(args.execution_file), args.model, args.role)
        _write(args.github_output, got)
        if got["retry"] == "true":
            print(f"::warning::{args.model} is out of capacity ({got['because']}) — "
                  f"re-running this planner step on {got['model']}")
        return 0

    got = finish(first_outcome=args.first_outcome, second_outcome=args.second_outcome,
                 first_exec=args.first_execution_file,
                 second_exec=args.second_execution_file, asked=args.asked,
                 second_model=args.second_model, because=args.because)
    _write(args.github_output, got)
    if args.required and got["outcome"] != "success":
        print(f"::error::the planner step did not succeed "
              f"(outcome {got['outcome'] or 'unknown'} on {got['model']})")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
