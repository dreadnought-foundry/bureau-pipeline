#!/usr/bin/env python3
"""The `question` class asks the card's blocker once, in Green Light (DRE-6459).

A blocker is never left as a silent skip. The reporter (DRE-6444) keeps that
for every NEW note by turning a question into the build run's Green Light
escalation. What it cannot reach is the board as it stands — every
`🛑 Agent blocked` marker posted before the blocker-class epic carries no class
and is read as a question — and the mechanical cases an action module hands
back as a person's call: a wrong-repo note naming two repositories, a branch
with nothing ahead of main, a draft pull request on the card's branch. The
resolver (`blocker_resolve.resolve_blocker`, DRE-6508) calls this module for
the `question` class and for any mechanical module's `None`. It never returns
`None` itself: it always asks.

## The ask

One comment, built by `compose` the way `report_agent_result.sh`'s escalation
branch builds its own: the `🙋` first line, the agent's reason completed by
`console_escalation.complete` with the Finding, Question and Recommendation
lines (`none given — …` where the agent recommended nothing; a reason that
already declares them comes back unchanged), and a closing line naming the
agent's run — the `Run:` URL read off the open marker, or `not recorded`. It
does not call `planning_escalation.escalate`: that function stands down for a
card outside Planning's segment, and its lane write is the `question` kind's
one declared site.

## What `resolve` does, in order

  1. Walks the live thread newest → oldest to the open marker. A comment newer
     than the marker that already opens with the ask's first line is an ask an
     earlier pass posted whose move did not land: nothing is posted again.
     No marker in the window is `NotNow` — the resolver read one seconds ago.
  2. Posts the ask. The comment-cap condition (the thread is full, nothing
     posted) is `NotNow`, and the card is not moved: a card is never carried to
     Green Light with no question on it (DRE-6490).
  3. Moves the card Backlog → Green Light, `held=True`, and reads the lane back
     fresh; anywhere but Green Light is `NotNow`, the marker left open and the
     ask standing for the next pass to find.

The next pass finds it only because `🙋` is one of
`reconcile._AGENT_COMMENT_PREFIXES` (DRE-6448): the standing ask is the
pipeline's own comment, never a person's reply. On a confirmed move it returns
`("asked", "asked in Green Light: <the Question line>")`. It posts no receipt:
the resolver's, posted after the return, is what stops the sweep asking twice.

The ask is declared `not-an-act` in `config/pipeline-acts.json`'s
`unconverted` block, anchored on the poster call below, and its Green Light
write is the `agent-escalation` arrival `blocker_ask.py#resolve` in
`config/lane-contract.json`.
"""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import blocker_class  # noqa: E402
import console_escalation  # noqa: E402
import linear_ops  # noqa: E402

#: The ask's first line — a machine prefix, so the sweep reads a standing ask
#: as its own comment.
FIRST_LINE = ("🙋 The build agent stopped on this card and the sweep could not "
              "act on its note — it needs your call.")

#: What `console_escalation.complete` asks, and who it says recommended
#: nothing, when the agent's reason declares no lines.
QUESTION = "Which way should this card go?"
WHO = "the build agent"

#: The closing line, before the agent's run.
CLOSING = ("Answer here, then move this card to **Todo** to proceed (a fresh run "
           "picks up your guidance), or to **Backlog** to drop it. Asked by the "
           "sweep; the agent's run: ")
NOT_RECORDED = "not recorded"

#: How much of the Question line the note quotes.
NOTE_QUOTE = 120

#: The marker's closing `Run: <url>` (`report_agent_result.sh`).
_RUN = re.compile(r"\bRun:\s*(https?://\S+)")


def compose(reason: str, run_url: str | None) -> str:
    """The whole ask: the first line, the reason with its three lines, and the
    closing line naming the agent's run."""
    completed = console_escalation.complete(reason, question=QUESTION, who=WHO)
    return f"{FIRST_LINE}\n\n{completed}\n\n{CLOSING}{run_url or NOT_RECORDED}"


def _read_thread(card: dict) -> tuple[str | None, bool]:
    """`(run_url, asked)` off the live thread: the open marker's `Run:` URL,
    and whether a comment newer than it already opens with the ask."""
    prefix = blocker_class.load()["marker"]
    asked = False
    for node in reversed(linear_ops.window_nodes(card.get("comments"))):
        body = (node.get("body") or "").lstrip()
        if body.startswith(prefix):
            runs = _RUN.findall(body)
            return (runs[-1] if runs else None), asked
        if body.startswith(FIRST_LINE):
            asked = True
    raise blocker_class.NotNow(
        f"no open {prefix.rstrip(':')} marker in the live thread of {card['identifier']}")


def resolve(card: dict, reason: str, *, repo: str) -> tuple[str, str] | None:
    """Ask the blocker in Green Light. See the module docstring."""
    run_url, asked = _read_thread(card)
    ask = compose(reason, run_url)
    if not asked:
        if linear_ops.cmd_comment(card["identifier"], ask) == linear_ops.COMMENT_CAP_CONDITION:
            raise blocker_class.NotNow("the thread is full — the question could not be posted")
    linear_ops.cmd_advance(card["identifier"], "Green Light", "Backlog", held=True)
    lane = (linear_ops.get_issue(card["identifier"], fresh=True).get("state") or {}).get("name")
    if lane != "Green Light":
        raise blocker_class.NotNow(f"Green Light move did not land — the card is in {lane}")
    question = console_escalation.parse(ask).question
    return "asked", f"asked in Green Light: {question[:NOTE_QUOTE]}"
