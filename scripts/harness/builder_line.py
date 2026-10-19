"""The builder's model line on the harness's probe pull requests (DRE-7619).

A real build posts `🧠 model-attempt: <model-id> — <role> agent starting`, and
the critic reads the build's model off it (`model_fallback.build_models`) to
review on a model the build did not run on. No agent builds a harness probe,
so its description carries no such line and the sandbox critic reviews it as
built on an unknown model. With the switch `on`, `bot_pr_flow` and
`gate_paths` end each probe description with `line()`: the model a real build
runs on, read off the `workhorse` ladder, never typed.

The switch is resolved once per run, by the driver (`resolve`): the dispatch
input `builder_line` first, then the bureau-pipeline repository variable
`HARNESS_BUILDER_LINE`, then `off`. The variable is the switch itself, and
setting it to `off` is the one-step rollback — it takes effect on the next
harness run that starts, with no pull request. With the switch `off` every
probe description is byte for byte what it was before this module existed.

`dependabot_flow` is out of reach: Dependabot writes that pull request's
description, so its review stays on the unknown-builder walk.
"""

from __future__ import annotations

import model_fallback

#: The environment names harness.yml sets on the `Run harness scenarios` step.
INPUT_ENV = "HARNESS_BUILDER_LINE_INPUT"
VARIABLE_ENV = "HARNESS_BUILDER_LINE_VAR"

#: Where the driver puts the resolved state in each scenario's `ctx.state`.
STATE_KEY = "builder_line"

ON, OFF = "on", "off"
STATES = (ON, OFF)

#: The role word the line names: no agent role builds a probe, and the
#: reviewer's reader counts any role but `planner` and `fixer` as a builder.
ROLE = "harness"


def resolve(input_value, variable_value, log=print) -> tuple[str, str]:
    """`(state, source)`: a non-empty dispatch input wins, then the variable,
    then `off` from `default`. A value other than `on` or `off` counts as
    `off`, from the source that gave it, with one `::warning::`."""
    for source, raw in (("input", input_value), ("variable", variable_value)):
        value = (raw or "").strip()
        if not value:
            continue
        if value in STATES:
            return value, source
        log(
            f"::warning::harness builder line: the {source} says {value!r}, "
            f"which is neither 'on' nor 'off' — the line stays off"
        )
        return OFF, source
    return OFF, "default"


def status_line(state: str, source: str) -> str:
    """The one line the driver logs per run, before any scenario starts."""
    return f"harness-builder-line: {state} (from {source})"


def model() -> str:
    """The `workhorse` ladder's first rung, as `model_fallback` reads it."""
    model_fallback.use_home()
    ladder = model_fallback.LADDERS[model_fallback.KINDS[model_fallback.WORKHORSE_KIND]]
    return ladder[0]


def line() -> str:
    """The contract line, shared with the sandbox reviewer's reader."""
    return (
        f"🧠 {model_fallback.MARKER_PREFIX} {model()} — {ROLE} agent starting "
        "(a fixture: no agent built this probe; it names the model a real "
        "build runs on)"
    )


def state_of(ctx) -> str:
    """The state the driver resolved for this run; `off` when none was."""
    return ctx.state.get(STATE_KEY) or OFF


def append(body: str, state: str) -> str:
    """`body`, ending with `line()` on a line of its own when `state` is
    `on`; untouched otherwise."""
    if state != ON:
        return body
    return f"{body}\n{line()}\n"
