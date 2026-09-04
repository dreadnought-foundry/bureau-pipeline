#!/usr/bin/env python3
"""A loud floor under the one-hour credential (DRE-3043, stdlib only).

`push_rescue.py` makes an expired credential survivable. This makes it VISIBLE
while the run can still do something about it.

The GitHub App installation token `agent-task.yml` mints at the top of the job
lives exactly 60 minutes, and every git credential on the runner is that token.
Until DRE-3043 nothing on the card said so — the ceiling was silent right up to
the push, which is how run 33822932627 spent 70 minutes and ~$20 on work that
never left the disk it was written on.

So at 50 minutes, with nothing pushed, the card gets one line: *credential
expires in 10 minutes — push what is green*. `briefs/engineer.md` tells the
agent what to do with it — commit and push a WIP branch, then carry on — so an
expiry costs a rebase instead of a rebuild.

TWO PROPERTIES IT MUST HAVE, and both are pinned:

  * IT NEVER BLOCKS THE BUILD. The workflow arms it in the background and the
    agent runs on. Every failure inside it — Linear refusing the write, git
    refusing the remote read, a clock that makes no sense — is swallowed:
    progress reporting must never take a run down (`briefs/engineer.md`).
  * "I COULD NOT TELL" WARNS. An unreadable remote is treated as *not pushed*.
    A missed warning costs the whole run; a spurious one costs a line on a card.

    python3 credential_clock.py watch --card DRE-3043 --minted-at <epoch> \\
        [--run-url <url>]
"""

from __future__ import annotations

import argparse
import os
import subprocess  # nosec B404 — git ls-remote; an argv list, never a shell
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# What GitHub gives an App installation token, and how much of it we insist on
# leaving for the agent to react in. 50 minutes is also where the harness's own
# REST client re-mints (harness/github_api.TOKEN_REFRESH_SECONDS) — the same
# hour, read from the same fact.
TOKEN_LIFETIME_SECONDS = 60 * 60
WARN_MARGIN_SECONDS = 10 * 60
WARN_AT_SECONDS = TOKEN_LIFETIME_SECONDS - WARN_MARGIN_SECONDS


def _run(argv, *, cwd=None, env=None):
    done = subprocess.run(  # nosec B603 — argv list, no shell
        argv, cwd=cwd, env=env, capture_output=True, text=True
    )
    return done.returncode, (done.stdout or ""), (done.stderr or "")


def seconds_until_warning(minted_at: float, now: float) -> float:
    """How long to wait before the floor speaks. Never negative — a clock that
    is already past the mark warns immediately rather than sleeping backwards.
    """
    return max(0.0, float(minted_at) + WARN_AT_SECONDS - float(now))


def branch_is_pushed(card: str, *, run=_run, workdir: str = ".") -> bool:
    """Has ANY of this card's work reached GitHub yet?

    The question the warning turns on, asked of the remote rather than of the
    local branch: a branch that exists locally and nowhere else is exactly the
    state this exists to shout about. An unreadable answer is False — see the
    module docstring.
    """
    code, out, _ = run([
        "git", "-C", workdir, "ls-remote", "--heads", "origin",
        f"refs/heads/agent/{card}-*",
    ])
    return code == 0 and bool(out.strip())


def warning_body(card: str, run_url: str = "") -> str:
    """The one line the card gets. Written to the agent, because the agent is
    the only party that can still act on it."""
    minutes = WARN_MARGIN_SECONDS // 60
    suffix = f" Run: {run_url}" if run_url else ""
    return (
        f"⏳ credential expires in {minutes} minutes — push what is green. This "
        f"run has been going for {WARN_AT_SECONDS // 60} minutes and nothing "
        f"has reached GitHub yet. Every git credential on this runner comes "
        f"from one GitHub App token and GitHub kills it at 60 minutes, so work "
        f"that is still only on the runner after that is work that has to be "
        f"built again. Commit and push a WIP branch now and carry on: an expiry "
        f"then costs a rebase, not a rebuild.{suffix}"
    )


def post_to_linear(card: str, body: str) -> None:
    """Put the warning on the card.

    The Linear seam, imported lazily so the pure functions above stay
    importable on a runner with no key — and module-level rather than a
    closure, so `check_act_receipts.py` sees exactly one receipt site here
    (a nested def is walked twice and would look like two).
    """
    import linear_ops  # noqa: PLC0415 — only this path needs the Linear seam

    linear_ops.cmd_comment(card, body)


def watch(
    card: str,
    *,
    minted_at: float,
    run_url: str = "",
    branch_pushed=None,
    post=None,
    sleep=time.sleep,
    now=time.time,
) -> str:
    """Wait out the margin, then say so if nothing has been pushed.

    Returns the body it posted (or ""), so a caller can log it. Raises
    nothing: this runs beside a live agent and must never be the reason a
    build stops.
    """
    try:
        sleep(seconds_until_warning(minted_at, now()))
        pushed = branch_pushed() if branch_pushed else branch_is_pushed(card)
        if pushed:
            return ""
        body = warning_body(card, run_url)
        if post:
            post(body)
        else:
            post_to_linear(card, body)
        return body
    except Exception as exc:  # a heartbeat must never block the build
        print(f"credential clock: gave up quietly — {exc}", file=sys.stderr)
        return ""


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    w = sub.add_parser("watch", help="warn on the card at the 50-minute mark")
    w.add_argument("--card", required=True)
    w.add_argument("--minted-at", type=float, required=True)
    w.add_argument("--run-url", default="")
    args = parser.parse_args(argv)

    body = watch(args.card, minted_at=args.minted_at, run_url=args.run_url)
    print("credential clock: warned" if body else "credential clock: nothing to warn about")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
