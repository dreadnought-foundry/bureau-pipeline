#!/usr/bin/env python3
"""The blocker resolver: one blocker's class, its action module, one receipt (DRE-6508).

The sweep reads an open `🛑 Agent blocked:` marker through
`blocker_class.open_blocker` (DRE-6438) and, from DRE-6448, hands the card and
the `Blocker` it read to `resolve_blocker`. This module carries no action of
its own. It reads the class's `action` off `config/blocker-classes.json` and:

  * for `nothing-to-change`, `wrong-repo` and `branch-without-pr`, imports
    that module by name and calls its `resolve(card, reason, repo=repo)`;
  * for `question`, and for a mechanical module's `None` (a person's call),
    calls `blocker_ask.resolve`, the latter with one line naming the class
    after the marker's reason.

A pair `(action, note)` back is posted as the act's receipt, composed in the
one `linear_ops.cmd_comment` call below and AFTER the module's own write, so a
write Linear refused never gets a receipt; its tag in a comment newer than the
marker is what resolves the marker for every later sweep.

It prints exactly two lines of its own, each opening
`promotion: <card> agent-blocker class=<class>`, and both leave the card in
Backlog with its marker open: `— not resolved this pass: <why>` when a module
raises `blocker_class.NotNow`, and `— action module <name> is not on this
checkout — skipping` while a sibling card's module has not landed. Every other
exception, a `ModuleNotFoundError` naming any other module included,
propagates: the sweep's call site isolates one card's failure from the rest.

`blocker_ask` is imported statically and called in `resolve_blocker`'s own
body, never through `importlib` or a helper: `scripts/lane_callers.py` names
the innermost enclosing function as the caller of a lane write.
"""

from __future__ import annotations

import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import blocker_class  # noqa: E402
import linear_ops  # noqa: E402
import pipeline_act  # noqa: E402

try:
    import blocker_ask  # noqa: E402
except ModuleNotFoundError as e:
    if e.name != "blocker_ask":
        raise
    blocker_ask = None

#: The class whose module is the ask itself.
QUESTION = "question"


def resolve_blocker(card: dict, blocker: blocker_class.Blocker, *,
                    repo: str) -> tuple[str, str] | None:
    """Resolve one open blocker through its class's module and post the
    receipt; `None` when nothing was resolved this pass. See the module
    docstring."""
    identifier = card["identifier"]
    cls = blocker.cls
    module_name = blocker_class.load()["classes"][cls]["action"]
    head = f"promotion: {identifier} agent-blocker class={cls}"
    reason = blocker.reason
    pair = None
    try:
        if cls != QUESTION:
            try:
                module = importlib.import_module(module_name)
            except ModuleNotFoundError as e:
                if e.name != module_name:
                    raise
                print(f"{head} — action module {module_name} is not on this checkout — skipping")
                return None
            pair = module.resolve(card, reason, repo=repo)
            if pair is None:
                reason = f"{reason}\n(class={cls}: the sweep could not act on this mechanically)"
        if pair is None:
            if blocker_ask is None:
                print(f"{head} — action module blocker_ask is not on this checkout — skipping")
                return None
            pair = blocker_ask.resolve(card, reason, repo=repo)
    except blocker_class.NotNow as why:
        print(f"{head} — not resolved this pass: {why}")
        return None
    action, note = pair
    linear_ops.cmd_comment(identifier, pipeline_act.receipt(
        "agent-blocker-resolved",
        f"🧹 agent-blocker-resolved: class={cls} action={action} — {note}"))
    return pair
