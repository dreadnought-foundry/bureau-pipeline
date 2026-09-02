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

DRE-2465 — THE OTHER HALF. The above only ever fires on `is_error: true`, so
a run that ended CLEANLY and still produced nothing usable got no description
at all. That is not a hypothetical: portico PR #297 ran the critic four times
to completion (117 turns, $12.40) and the pull request was told four times
that the reviewer had died at startup on a credential error. completion_detail
/ completion_scalars describe that run — turns, cost, duration, subtype —
under the same whitelist discipline, minus `result`, which on a clean run is
the agent's own prose about the diff it just read.

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

# Printed, in this order, when the final result message has is_error != true —
# a run that ENDED CLEANLY and whose caller still has nothing usable (DRE-2465).
# There is no error to quote here, so the question is not "why did it die" but
# "did it run at all, and how much did it do" — which is precisely the question
# portico PR #297 answered wrongly for a day.
#
# `result` is deliberately ABSENT. On the crash path it carries the provider's
# error string; on a clean run it is the agent's own closing message, which
# quotes the pull request it just read. Same discipline as above: every field
# here is a number or the action's own fixed subtype enum.
_COMPLETION_FIELDS = (
    "subtype",
    "num_turns",
    "total_cost_usd",
    "duration_ms",
)

# The header `print_failure_detail` writes above those fields. A CONSTANT
# because a second reader has to find the block in a log: the medic holds no
# execution file — it holds the failed run's log — and this line is where the
# run's own result record starts in it (DRE-2954). Two spellings of the same
# sentence is how that reader would silently stop finding anything.
FAILURE_HEADER = "what the agent itself reported (from the execution file):"

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


def _field_lines(execution: dict, fields: tuple[str, ...]) -> list[str]:
    """`  field: value` lines for the whitelisted fields that carry a value.

    Empty/absent fields are skipped so the log carries signal, not a column
    of `None`s.
    """
    lines = []
    for field in fields:
        value = execution.get(field)
        if value is None or value == "" or value == [] or value == {}:
            continue
        lines.append(f"  {field}: {_render(value)}")
    return lines


def failure_detail(execution: dict | None) -> list[str]:
    """`field: value` lines explaining a death, or [] when nothing died.

    Only ever reads the final result message, and only the whitelisted fields
    of it.
    """
    if not isinstance(execution, dict) or execution.get("is_error") is not True:
        return []
    return _field_lines(execution, _DIAGNOSTIC_FIELDS)


def completion_detail(execution: dict | None) -> list[str]:
    """`field: value` lines describing a run that ENDED WITHOUT AN ERROR.

    The other half of failure_detail (DRE-2465). A gate can fail on a run
    that never errored — the critic that finishes 25 turns and leaves no
    verdict file is the case this exists for — and until now the printer
    was structurally incapable of describing it, so the log showed nothing
    and the operator was told the reviewer had never started.

    [] when the run DID die (failure_detail owns that, with its own wording)
    and [] when there is no result record at all: a missing execution file is
    not evidence that anything ran, and callers key their message off exactly
    that distinction.
    """
    if not isinstance(execution, dict) or execution.get("is_error") is True:
        return []
    return _field_lines(execution, _COMPLETION_FIELDS)


def completion_scalars(execution: dict | None) -> dict:
    """The same whitelist, as NUMBERS, for a caller that must pass them on.

    qa-review.yml puts these in a PR comment, by way of a step output and a
    shell string. Strings are dropped rather than escaped: a number cannot
    carry a newline (which would write a second `$GITHUB_OUTPUT` key), a
    quote, or a command substitution, so the value is safe by construction
    instead of safe by careful quoting. bools are numbers in Python and are
    excluded on purpose — none of these fields is ever legitimately one.
    """
    if not isinstance(execution, dict) or execution.get("is_error") is True:
        return {}
    scalars = {}
    for field in _COMPLETION_FIELDS:
        value = execution.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        scalars[field] = value
    return scalars


def print_failure_detail(execution: dict | None, prefix: str) -> None:
    """Print the death's own explanation under a one-line header.

    `prefix` names the gate doing the reporting so the lines are greppable
    next to its verdict line.
    """
    lines = failure_detail(execution)
    if not lines:
        return
    print(f"{prefix}: {FAILURE_HEADER}")
    for line in lines:
        print(line)


def print_completion_detail(execution: dict | None, prefix: str) -> None:
    """Print what a run that did NOT die actually did.

    The counterpart header to print_failure_detail's. It exists to be read by
    someone holding a failing gate and a theory that the agent never started:
    turns and dollars are the evidence that settles it.
    """
    lines = completion_detail(execution)
    if not lines:
        return
    print(
        f"{prefix}: the run did NOT error — this is what it did "
        f"(from the execution file):"
    )
    for line in lines:
        print(line)
