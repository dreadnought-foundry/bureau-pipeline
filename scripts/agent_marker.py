#!/usr/bin/env python3
"""The observability marker: WHICH agent acted on a card (DRE-2727, stdlib only).

The card already records which MODEL an attempt ran on — agent-task.yml posts
`🧠 model-attempt: <model> — <role> agent starting` before the agent gets a
turn. That is the workflow's claim about what it was about to dispatch. It is
not the agent's own record that it ran, and on the roles that are not dispatched
by agent-task.yml (the verifier, an operator-launched database-architect) there
is no such line at all.

So the agent writes one line of its own, in a machine-readable form, at the end
of its run:

    🤖 agent-actor: <role> · run <url>

ONE definition, here, because the readers and the writers are in different
places: the briefs tell the agent to post it, `linear_ops.py actor` posts it,
and anything reading a card's history back — a console panel, a sweep, a human
asking "who touched this" — parses it. A marker restated in six briefs is a
marker that means six slightly different things within a month.

`🤖` is deliberate and load-bearing: reconcile.py's _AGENT_COMMENT_PREFIXES
already treats it as a machine comment, so this line can never be mistaken for
the human reply that clears an agent blocker. It is NOT in _LIFE_PREFIXES, so it
is not proof of life either — it says an agent acted, not that one is alive.
"""

from __future__ import annotations

import os
import re
import sys

#: The one machine-readable form. Anything writing it goes through actor_line().
ACTOR_MARKER = "🤖 agent-actor:"

#: A role is a lowercase slug — the same shape as an `agent:<role>` label and a
#: `briefs/<role>.md` filename. Validated rather than enumerated: the roster
#: lives in agents.yaml, which needs PyYAML, and this module runs on every
#: product repo's agent job where there is no pip install.
_ROLE = re.compile(r"^[a-z][a-z0-9-]*$")

_LINE = re.compile(rf"^\s*{re.escape(ACTOR_MARKER)}\s*([a-z][a-z0-9-]*)\b")


def run_url(env: dict | None = None) -> str | None:
    """This Actions run's URL from the ambient environment, or None outside CI."""
    env = os.environ if env is None else env
    server = env.get("GITHUB_SERVER_URL") or "https://github.com"
    repo = env.get("GITHUB_REPOSITORY")
    run_id = env.get("GITHUB_RUN_ID")
    if not repo or not run_id:
        return None
    return f"{server}/{repo}/actions/runs/{run_id}"


def actor_line(role: str, url: str | None = None, env: dict | None = None) -> str:
    """The comment body recording that `role` acted on this card.

    The run URL is appended when one is knowable, so the marker maps the card to
    the run the same way the model-attempt heartbeat does. Outside CI (an
    operator-launched session) there is no run, and the marker still stands on
    its own — omitting the field is honest, inventing one is not.
    """
    role = (role or "").strip().lower()
    if not _ROLE.match(role):
        raise ValueError(
            f"role {role!r} is not a role slug (lowercase, e.g. 'engineer', "
            "'database-architect')"
        )
    url = run_url(env) if url is None else url
    return f"{ACTOR_MARKER} {role}" + (f" · run {url}" if url else "")


def actor_role(comment_bodies: list) -> str | None:
    """The role recorded by the NEWEST actor marker on a card, or None.

    Input is oldest→newest, matching linear_ops.comment_bodies.
    """
    for body in reversed(comment_bodies or []):
        match = _LINE.match(body or "")
        if match:
            return match.group(1)
    return None


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 2 or argv[0] != "line":
        print(f"usage: {sys.argv[0]} line <role>", file=sys.stderr)
        return 2
    print(actor_line(argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
