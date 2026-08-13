#!/usr/bin/env python3
"""Read the Claude execution result file, and say WHY a run died (stdlib only).

DRE-2435. Both result gates (check_agent_result.py, check_critic_result.py)
loaded this file with their own private copy of the same loader and reported a
death as a fixed string: "execution result has is_error=true". The run log
therefore showed only the shape of the corpse — `num_turns: 1,
total_cost_usd: 0` — which is the SAME shape for an expired token, an
overloaded API, a refusal and a bad model id. People guessed "credentials" and
were wrong repeatedly; it cost days.

The explanation was never missing. `anthropics/claude-code-action@v1` redacts
its own stdout (it rebuilds the result object from a small field whitelist
before echoing it), but it separately writes the RAW message array to
`$RUNNER_TEMP/claude-execution-output.json` — and the gates already read that
file and already held the full unredacted result dict in memory. This module
prints the handful of fields that answer the question.

WHAT IT WILL NOT DO. The action ships a `show_full_output` switch that dumps
the ENTIRE transcript: every assistant turn, every tool result — file
contents, command output, and whatever a hostile PR contrived to get echoed
back — into a log that is public on a public repo. That switch is not set
anywhere in this repo and a test in tests/test_execution_failure_detail.py
holds it that way. What we print instead is a whitelist of scalar fields
(_DIAGNOSTIC_FIELDS) taken from the FINAL RESULT MESSAGE ONLY, each capped at
_VALUE_CAP characters. Nothing else on the result record is printed either —
notably `env`, which carries the whole environment.

One loader, one printer, imported by both gates: a second copy is how the two
gates quietly stop agreeing about what a death looks like.
"""

from __future__ import annotations

import json

# Printed, in this order, when the final result message has is_error=true.
# Everything the action records about WHY it stopped; nothing about WHAT the
# agent read, ran or said. Adding a field here means auditing whether it can
# ever carry transcript or environment content — most of them can.
_DIAGNOSTIC_FIELDS = (
    "subtype",
    "api_error_status",
    "stop_reason",
    "terminal_reason",
    "errors",
    "result",
)

# Per-value ceiling. A provider death can hand back a page of HTML or a
# multi-kilobyte JSON body; the first few hundred characters always carry the
# error class, and the rest just buries the rest of the log.
_VALUE_CAP = 500


def load_execution(path: str) -> dict | None:
    """The final result record from an execution-output file, or None.

    The action writes either a single result object or the whole message list
    ending with the result record; absence/corruption is not an error here —
    action versions move the file around, and the callers each decide what a
    missing result means.
    """
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if isinstance(data, list):
        for entry in reversed(data):
            if isinstance(entry, dict) and "is_error" in entry:
                return entry
        return None
    return data if isinstance(data, dict) else None


def _render(value: object) -> str:
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, default=str)
        except (TypeError, ValueError):
            text = str(value)
    if len(text) > _VALUE_CAP:
        return f"{text[:_VALUE_CAP]}... [truncated, {len(text)} chars total]"
    return text


def failure_detail(execution: dict | None) -> list[str]:
    """`field: value` lines explaining a death, or [] when nothing died.

    Only ever reads the final result message, and only the whitelisted fields
    of it. Empty/absent fields are skipped so the log carries signal, not a
    column of `None`s.
    """
    if not isinstance(execution, dict) or execution.get("is_error") is not True:
        return []
    lines = []
    for field in _DIAGNOSTIC_FIELDS:
        value = execution.get(field)
        if value is None or value == "" or value == [] or value == {}:
            continue
        lines.append(f"  {field}: {_render(value)}")
    return lines


def print_failure_detail(execution: dict | None, prefix: str) -> None:
    """Print the death's own explanation under a one-line header.

    `prefix` names the gate doing the reporting so the lines are greppable
    next to its verdict line.
    """
    lines = failure_detail(execution)
    if not lines:
        return
    print(f"{prefix}: what the agent itself reported (from the execution file):")
    for line in lines:
        print(line)
