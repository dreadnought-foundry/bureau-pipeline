#!/usr/bin/env python3
"""Run a read-path `gh` command, retrying a BRIEF rate-limit refusal (stdlib only).

This is the DRE-4109 seam — `reconcile.gh_read`'s retry, lifted out of the
sweep so a workflow step can use it too (DRE-4157). There is ONE copy of the
classifier, the attempt count and the backoff, and the sweep imports them from
here; the two callers cannot drift.

Why a workflow step needed it
-----------------------------
On 2026-09-17 → 18 the shared App installation (123249480) was throttled six
times while agent-fix.yml's "Resolve PR, mode, and attempt budget" step was
reading a pull request's comment thread (portico #582 first, run 35252920674).
`gh api` printed GitHub's error page on STDOUT and exited 1; the step piped
that straight into `jq`, which read the error as the comment record and died
on it (`Cannot index string with string "user"`, exit 5), and the run was red
for a reason that had nothing to do with the branch. The CEO's signed answer
on the card: notice the refusal, wait and retry the way the sweep does under
DRE-4109, and if it still cannot read, stop with a plain-English reason
instead of crashing.

What is retried, and what is not
--------------------------------
Only GitHub's rate-limit wording — `API rate limit exceeded`, `secondary rate
limit`, an HTTP 429 — because those are the refusals a wait actually fixes.
The OTHER 403, `Resource not accessible by integration`, is a permission
failure that no amount of waiting repairs, and retrying it would only deepen
the quota burn DRE-1921 was filed for; it fails at once, as does a 404 or a
network error. Three attempts, ~15s then ~45s: under a minute in the worst
case. An hourly bucket that is genuinely empty will NOT clear in that minute,
and that is the case the caller is meant to STOP on — loudly, with the reason
named, and never with a substitute that reads as data.

The CLI
-------
    gh_read_retry.py [--out FILE] gh api --paginate --slurp "repos/o/r/issues/N/comments?per_page=100"

The command is trailing argv and must start with `gh`, the way `timeout` or
`env` take theirs — so the workflow line still reads `gh api --paginate …` to
every guard that greps for one (tests/test_comment_record_pagination.py).

  - success: the answer goes to stdout, or to `--out FILE` (written only on
    success — a refused read leaves NO file for a caller to mistake for the
    record). Retry lines go to stderr, which is the run log, never the data.
  - a refusal that persists: one plain-English paragraph on stderr naming
    the command and the throttle, exit 75 — EX_TEMPFAIL, the code the sweep
    already uses for a quota exhaustion (`reconcile.RATE_LIMITED_EXIT`,
    DRE-2923). Under `bash -e` that stops the step where it stands.
  - any other failure: gh's stderr, exit 1, no wait.
  - GitHub's error body NEVER reaches stdout. That body on the data channel
    is the whole of the incident.

Test hook: `BUREAU_GH_READ_BACKOFF` — comma-separated seconds replacing the
backoff (the `claude_rate_limit_retry.py` / `dispatch_pool.py` hook shape), so
a harness can run the real step and the real retry without the real minute.
It changes the waits and nothing else.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess  # nosec B404 — fixed argv, the gh CLI
import sys
import time
from typing import Callable, Sequence

#: How many times a read is attempted when GitHub answers with a BRIEF refusal,
#: and how long it waits between attempts (DRE-4109). Three attempts, ~15s then
#: ~45s: under a minute of waiting in the worst case, which sits well inside
#: the sweep's 10-minute job timeout (and the fix job's 120), and long enough
#: that a secondary-rate-limit blip has cleared. An hourly bucket that is
#: genuinely empty will NOT clear in that minute — that read still fails, and
#: the caller still says so, which is exactly the "only tell me if it persists"
#: the CEO asked for (signed console answer, 2026-09-16).
ATTEMPTS = 3
BACKOFF_SECONDS = (15, 45)

#: The sweep's own exit code for a quota exhaustion (DRE-2923): 75 is
#: EX_TEMPFAIL, "the failure is temporary, try again later". Declared here
#: rather than imported from reconcile so a workflow step does not pay for a
#: 7,000-line import to read one integer; tests/test_gh_read_retry.py pins the
#: two equal.
RATE_LIMITED_EXIT = 75

#: GitHub's rate-limit wording, and ONLY that (DRE-4109). Deliberately narrower
#: than medic_classify's log-scanning patterns: this one decides whether to
#: spend another request, so it matches the refusals that a wait actually fixes.
#: The 429 alternative is anchored to an `http`/`status` word so a bare 429
#: inside an installation id or a PR number cannot match.
RATE_LIMIT_REFUSAL = re.compile(
    r"api rate limit exceeded"
    r"|secondary rate limit"
    r"|(?:http|status)\D{0,6}\b429\b",
    re.I,
)


def is_rate_limit_refusal(stderr: str) -> bool:
    """True iff `gh`'s stderr is GitHub declining THIS request for quota."""
    return bool(RATE_LIMIT_REFUSAL.search(stderr))


def backoff_seconds() -> tuple[float, ...]:
    """The waits between attempts — the module's numbers, or the test hook's."""
    raw = os.environ.get("BUREAU_GH_READ_BACKOFF", "").strip()
    if not raw:
        return BACKOFF_SECONDS
    return tuple(float(part) for part in raw.split(","))


class GhReadError(RuntimeError):
    """A read-path gh call failed — after retrying, if it was a throttle.

    `rate_limited` says which: True is a refusal that persisted past every
    attempt (or past the caller's budget), False is anything else, which was
    not retried at all.
    """

    def __init__(self, message: str, *, returncode: int, stderr: str, rate_limited: bool):
        super().__init__(message)
        self.returncode = returncode
        self.stderr = stderr
        self.rate_limited = rate_limited


def read(
    args: Sequence[str],
    *,
    may_retry: Callable[[], bool] | None = None,
    log: Callable[[str], None] = print,
) -> str:
    """Run `gh <args>` and return its stdout (stripped), retrying a brief
    rate-limit refusal; raise GhReadError otherwise.

    `may_retry` is the caller's own bound on top of the per-read one — the
    sweep spends a per-process budget through it (DRE-4109's second bound,
    because `pr_for` runs once per card and an empty bucket refuses all of
    them). It is asked before each wait and answers False to stop; it may
    print its own reason. A workflow step makes one read per process and
    needs no such bound.

    `log` receives ONE line per retry, naming the command and `attempt N of
    3` — the whole of the visibility the CEO asked for in place of a card.
    """
    cmd = "gh " + " ".join(args)
    waits = backoff_seconds()
    for attempt in range(1, ATTEMPTS + 1):
        p = subprocess.run(  # nosec B603 B607 — fixed-arg gh call, shell=False
            ["gh", *args], capture_output=True, text=True, check=False
        )
        if p.returncode == 0:
            return p.stdout.strip()
        stderr = p.stderr.strip()
        throttled = is_rate_limit_refusal(stderr)
        retryable = throttled and attempt < ATTEMPTS
        if retryable and may_retry is not None and not may_retry():
            retryable = False
        if not retryable:
            raise GhReadError(
                f"{cmd} failed rc={p.returncode}: {stderr[:400]}",
                returncode=p.returncode, stderr=stderr, rate_limited=throttled,
            )
        wait = waits[min(attempt - 1, len(waits) - 1)]
        log(
            f"gh-read: GitHub refused `{cmd}` for rate limit on "
            f"attempt {attempt} of {ATTEMPTS} — retrying in {wait:g}s: "
            f"{stderr[:200]}"
        )
        time.sleep(wait)
    # Unreachable: the final attempt either returns or raises above. Kept so
    # the function has no implicit `return None` for a caller to act on.
    raise GhReadError(f"{cmd} failed: retries exhausted", returncode=1, stderr="",
                      rate_limited=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gh_read_retry.py",
        description="Run a read-path gh command, retrying a brief rate-limit refusal.",
    )
    parser.add_argument("--out", help="write the answer here instead of stdout (only on success)")
    parser.add_argument("command", nargs=argparse.REMAINDER,
                        help="the gh command, starting with `gh`")
    args = parser.parse_args(argv)
    command = list(args.command)
    if command[:1] == ["--"]:
        command = command[1:]
    if not command or command[0] != "gh":
        print(
            "gh_read_retry.py: the command must start with `gh` — this retries "
            f"the gh CLI's read calls and nothing else (got: {' '.join(command) or 'nothing'})",
            file=sys.stderr,
        )
        return 2
    gh_args = command[1:]
    cmd = "gh " + " ".join(gh_args)

    def to_log(line: str) -> None:
        print(line, file=sys.stderr, flush=True)

    try:
        answer = read(gh_args, log=to_log)
    except GhReadError as e:
        if e.rate_limited:
            waited = sum(backoff_seconds()[: ATTEMPTS - 1])
            print(
                f"gh-read: GitHub is still refusing `{cmd}` for rate limit after "
                f"{ATTEMPTS} attempts ({waited:g}s of waiting) — the App "
                "installation's hourly quota is empty, not blipping. This is a "
                "throttle on an outside service, not a defect on the branch: "
                "nothing was read, so this step stops here rather than deciding "
                f"on data it does not have. Last answer: {e.stderr[:200]}",
                file=sys.stderr,
            )
            return RATE_LIMITED_EXIT
        print(
            f"gh-read: `{cmd}` failed rc={e.returncode} — not a rate limit, so it "
            f"was not retried: {e.stderr[:400]}",
            file=sys.stderr,
        )
        return 1
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(answer + "\n")
    else:
        sys.stdout.write(answer)
    return 0


if __name__ == "__main__":
    sys.exit(main())
