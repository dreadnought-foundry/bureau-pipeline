#!/usr/bin/env python3
"""Writing `$GITHUB_OUTPUT` so a value that wraps cannot kill the step
(DRE-4202, stdlib only).

`$GITHUB_OUTPUT` is a `key=value` file the runner reads a LINE at a time. A
value carrying a newline is not a long value there — it is a second key, and
the runner refuses the malformed line it makes: the step dies with
`Unable to process file command 'output' successfully`, naming the line it
choked on and nothing else.

Red-Main Repair died that way on portico run 35314499681 (2026-09-17 23:21 PT,
head `eeac2b14`), and it failed in the worst available place. The step that
writes the outputs is the step that has already filed the repair card and spent
Linear budget, so the attempt cost a card and quota and repaired nothing —
`main` stayed red for its bundle budget (DRE-4123). Worse, a run that dies
before the model starts wears `is_error, 1 turn, $0`, the documented
fingerprint of a dead `CLAUDE_CODE_OAUTH_TOKEN`: DRE-4201 read it as exactly
that and asked for a fleet-wide rotation of a credential chain that was
working.

Two rules, and this module is both of them.

## `render` — the value decides the form, not the author

Any value that cannot ride a plain `key=value` line goes out under a HEREDOC
delimiter, the only multi-line form the runner accepts. The delimiter is
random and re-drawn on collision, so no value can close its own block — the
shape `sanitize_untrusted.py` already uses against attacker-chosen text.

A single-line value still emits as plain `key=value`. Every consumer of these
outputs reads the FILE, not this module, so the plain form has to stay the
plain form; the safety is that the writer no longer depends on the author
having been right about which values can wrap. `branch` and `attempt` were
safe on that run by accident, not by construction.

## `only_outputs` — stdout is the machine channel or it is nothing

Four scripts in this repo are run with their STDOUT redirected into the file
(`… >> "$GITHUB_OUTPUT"`). In that shape stdout stops being a place to talk,
and not only for the script itself: `linear_ops.cmd_comment` prints
`commented on DRE-4200` there, and `repair_card.py open` calls it. That line —
not a wrapped `reason` — is the one portico's runner actually named. Human
text belongs on stderr, where those scripts' own docstrings already put it;
this context manager puts it there whoever wrote it.
"""

from __future__ import annotations

import contextlib
import sys
import uuid
from collections.abc import Iterable

#: What a plain `key=value` line cannot carry. A newline or a carriage return
#: ends the line early. `<<` is how the runner recognises a heredoc opener, so
#: a value carrying one could be read as opening a block of its own — no
#: newline required.
UNSAFE_MARKERS = ("\n", "\r", "<<")


def is_plain(value: str) -> bool:
    """Can `value` ride a plain `key=value` line?"""
    return not any(marker in value for marker in UNSAFE_MARKERS)


def _token() -> str:
    """The random half of a delimiter. Its own function so a test can make a
    collision happen instead of waiting out 2^128 draws for one."""
    return uuid.uuid4().hex


def delimiter(value: str) -> str:
    """A heredoc delimiter `value` cannot contain, re-drawn until it does not
    occur anywhere in the text (substring, not line — stricter than the runner
    needs and cheaper than reasoning about why)."""
    delim = f"EOF-{_token()}"
    while delim in value:
        delim = f"EOF-{_token()}"
    return delim


def render(pairs: Iterable[tuple[str, object]]) -> str:
    """The exact text a step appends to `$GITHUB_OUTPUT` for `pairs`.

    `None` is the empty value: a key a decision did not fill is still a key
    the workflow reads, and an absent output reads as empty anyway.
    """
    block = []
    for name, raw in pairs:
        value = "" if raw is None else str(raw)
        if is_plain(value):
            block.append(f"{name}={value}\n")
        else:
            delim = delimiter(value)
            block.append(f"{name}<<{delim}\n{value}\n{delim}\n")
    return "".join(block)


@contextlib.contextmanager
def only_outputs():
    """Run the body with stdout pointed at stderr, so nothing it prints — or
    anything it imports prints — can reach the output file.

    The caller writes its rendered block to the real stdout AFTER the block,
    which is the whole discipline in one shape: compute with the channel shut,
    then emit.
    """
    with contextlib.redirect_stdout(sys.stderr):
        yield
