#!/usr/bin/env python3
"""Retry a build GitHub refused BEFORE the model started (DRE-4108, stdlib only).

Why
---
On 2026-09-16 two portico Agent Task runs died the same way, minutes apart,
inside `anthropics/claude-code-action`'s allowed-bots setup:

    GET /users/agent-bureau-bot-2%5Bbot%5D
    403 API rate limit exceeded for installation ID 123249480

The model was never started. No turn was taken, nothing was billed, and the
run exited 1 on the first refusal — so each one filed a pipeline-failure card
for a shared-quota hiccup that would have cleared by itself. The CEO's signed
rule on DRE-4109 puts it in one line: a brief refusal from an outside service
is a retry, not a failure.

The failing call is inside the vendor action, which the fleet pins to a commit
and does not patch, so the retry wraps the step in OUR workflow
(`agent-task.yml`) and this module is the thing that decides whether to fire
it.

The signature, and why it has to be PROBED
------------------------------------------
The decision this module makes is deliberately narrow — it must never re-run a
build that did real work — and it is the conjunction of three facts:

  1. the agent step FAILED (GitHub's own outcome for it);
  2. the model never started (`check_agent_result.agent_started`, the one
     shared predicate — a run with a turn on it called the model and refused,
     and that run counts);
  3. GitHub is refusing this installation RIGHT NOW.

Fact 3 cannot be read out of the failed step's log: GitHub publishes a job's
log only after the job ends, so no later step in the same job can see what the
vendor action printed. So this module RE-ISSUES the call the vendor made —
`GET /users/<bot>%5Bbot%5D`, with the same installation token — and reads
GitHub's own answer. That is what makes the signature observable at all, and
it is why the receipt line can name the call the way the card asks.

It also fixes the direction the classifier fails in. Confirmation is required:
an unreadable probe, a healthy bucket, or a 403 that is not a rate limit each
stop the retry. DRE-1921 is the reason — the medic once re-ran a critic that
had crashed on a GitHub rate limit, and six PRs looped and burned the bot's
quota twice. Re-running into a limit nobody confirmed cannot succeed and
deepens it.

A benign false positive is possible and accepted: if the step died of
something else while the bucket happened to be empty, this retries once or
twice for nothing. It costs two GitHub requests and a wait — never a model
call, because a run that reached the model is excluded by fact 2.

The wait
--------
Every retry backs off first (DRE-2429: verify.yml and qa-review.yml both had
retries issued as the immediately following step, which re-issued a
deterministic failure at once and was charged twice). The wait is capped at
`MAX_BACKOFF_SECONDS` regardless of when the window resets, because a job that
sleeps out its own App installation token is a worse failure than one that
fails fast: GitHub kills that token at 60 minutes (DRE-3043), and the medic
can re-run a red run an hour later when this job cannot.

Interface
---------
    claude_rate_limit_retry.py decide --attempt N --max-attempts M \
        --claude-outcome <GitHub's outcome for the attempt> \
        --execution-file <path> [--branch <branch>] \
        --allowed-bots a,b,c [--github-output $GITHUB_OUTPUT]

      writes `retry=`, `reason=`, `delay=` and `call=` and prints the receipt
      line the operator reads. NEVER exits non-zero: a classifier that can
      fail a build is a new way to lose a run (dispatch_pool.py's rule).

    claude_rate_limit_retry.py resolve --attempt '<outcome>=<execution-file>' ... \
        [--github-output $GITHUB_OUTPUT]

      writes `outcome=`, `execution_file=` and `attempts=` — the LAST attempt
      that actually ran, which is what the job's result gate and its Linear
      report must judge the run by. A consumer left reading attempt 1 would
      report a dead agent for a build the retry finished.

Test hooks (the same shape as dispatch_pool.py's `BUREAU_FAKE_RATE_LIMITS`):
`BUREAU_FAKE_BOT_LOOKUP` — JSON `{"status": 403, "body": "..."}` — replaces the
live user lookup, and `BUREAU_FAKE_RATE_LIMIT_RESET` — seconds — replaces the
live `/rate_limit` read.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import namedtuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from check_agent_result import agent_started  # noqa: E402
from execution_result import load_execution  # noqa: E402

GITHUB_API = "https://api.github.com"

# The call the two portico runs died on, spelled exactly as the vendor action
# issues it: the `[bot]` suffix is percent-encoded in the path.
BOT_LOOKUP_PATH = "/users/{name}%5Bbot%5D"
RATE_LIMIT_PATH = "/rate_limit"

# Three attempts: the original plus two retries. Bounded, and small — this
# retry exists for a refusal that clears in a couple of minutes. One that
# outlives that is a real outage, and the right answer for an outage is a red
# run the medic can re-run later, not a job holding a runner.
MAX_ATTEMPTS = 3

# Seconds to wait before attempt n+1. Same order of magnitude as
# qa-review.yml's critic backoff (120s), which was sized for this class of
# transient.
BACKOFFS = (60, 120)

# The ceiling on any single wait, however far away GitHub says the window
# resets. See "The wait" above.
MAX_BACKOFF_SECONDS = 120

REASON_NOT_A_FAILURE = "step-did-not-fail"
REASON_MODEL_STARTED = "model-started"
REASON_EXHAUSTED = "retries-exhausted"
REASON_RATE_LIMITED = "github-rate-limited"
REASON_NOT_RATE_LIMITED = "not-rate-limited"
REASON_PROBE_UNREADABLE = "probe-unreadable"

# The fingerprints, matched against the BODY of a 403 and never on their own.
# Both are GitHub's own wording; `medic_classify.py` carries the same two for
# reading a run's logs. Status AND body, because an auth failure whose prose
# happens to mention a rate limit is an auth failure.
_RATE_LIMIT_SHAPES = (
    re.compile(r"api rate limit exceeded", re.I),
    re.compile(r"secondary rate limit", re.I),
)

ProbeResult = namedtuple("ProbeResult", "status body reset_in")
ProbeResult.__new__.__defaults__ = (None,)

Decision = namedtuple("Decision", "retry reason delay call line")


# --------------------------------------------------------------------------- #
# The signature                                                                #
# --------------------------------------------------------------------------- #

def is_rate_limit_refusal(status: int | None, body: str | None) -> bool:
    """True iff GitHub answered this call with a rate-limit refusal."""
    if status != 403:
        return False
    return any(shape.search(body or "") for shape in _RATE_LIMIT_SHAPES)


def backoff_seconds(attempt: int, reset_in: int | None = None) -> int:
    """Seconds to wait before the attempt after `attempt`.

    `reset_in` — seconds until GitHub's core window resets, when the probe
    could read it — only ever SHORTENS the wait, never lengthens it past the
    cap: there is no point sleeping past the reset, and no run may sleep out
    its credential waiting for one that is an hour away.
    """
    scheduled = BACKOFFS[min(max(attempt, 1), len(BACKOFFS)) - 1]
    if reset_in is None:
        return min(scheduled, MAX_BACKOFF_SECONDS)
    return max(1, min(scheduled, max(int(reset_in) + 5, 1), MAX_BACKOFF_SECONDS))


# --------------------------------------------------------------------------- #
# The probe                                                                    #
# --------------------------------------------------------------------------- #

def _fake_lookup() -> ProbeResult | None:
    """The test seam. Anything unparseable reads as an unreadable probe, which
    is the fail-closed answer — a broken seam must never invent a retry."""
    raw = os.environ.get("BUREAU_FAKE_BOT_LOOKUP", "")
    if not raw.strip():
        return None
    try:
        parsed = json.loads(raw)
    except ValueError:
        return ProbeResult(status=None, body="", reset_in=None)
    if not isinstance(parsed, dict):
        return ProbeResult(status=None, body="", reset_in=None)
    return ProbeResult(
        status=parsed.get("status"),
        body=parsed.get("body") or "",
        reset_in=parsed.get("reset_in"),
    )


def _get(url: str, token: str) -> tuple[int | None, str]:
    """GET `url` with an installation token. (status, body); (None, '') on a
    transport failure — an unreadable answer, never a healthy one."""
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        url,
        headers={
            "authorization": f"Bearer {token}",
            "accept": "application/vnd.github+json",
            "user-agent": "bureau-claude-rate-limit-retry",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:  # 403 lives here, and it is the point
        try:
            body = exc.read().decode("utf-8", "replace")
        except Exception:  # pragma: no cover - a body we cannot read is empty
            body = ""
        return exc.code, body
    except Exception:
        return None, ""


def _reset_in(token: str) -> int | None:
    """Seconds until the core window resets, from the quota-exempt endpoint.

    Best-effort and never load-bearing: `None` simply means the scheduled
    backoff is used unchanged.
    """
    fake = os.environ.get("BUREAU_FAKE_RATE_LIMIT_RESET", "")
    if fake.strip():
        try:
            return int(fake)
        except ValueError:
            return None
    if not token:
        return None
    import time

    status, body = _get(GITHUB_API + RATE_LIMIT_PATH, token)
    if status != 200:
        return None
    try:
        reset = json.loads(body)["resources"]["core"]["reset"]
    except (ValueError, KeyError, TypeError):
        return None
    if not isinstance(reset, (int, float)):
        return None
    return max(0, int(reset - time.time()))


def live_probe(token: str):
    """The real probe: re-issue the vendor's own lookup for one bot name."""

    def probe(name: str) -> ProbeResult:
        fake = _fake_lookup()
        if fake is not None:
            return fake
        status, body = _get(
            GITHUB_API + BOT_LOOKUP_PATH.format(name=name), token
        )
        return ProbeResult(status=status, body=body, reset_in=_reset_in(token))

    return probe


def first_bot(allowed_bots: str) -> str:
    """The name the probe asks about.

    ONE name, not five: the quota bucket is per INSTALLATION, so the first
    lookup answers for every name in the list, and a probe that spent five
    requests to learn one fact would be making the exhaustion it is measuring
    slightly worse.
    """
    for name in (allowed_bots or "").split(","):
        name = name.strip()
        if name:
            return name
    return "agent-bureau-bot"


# --------------------------------------------------------------------------- #
# The decision                                                                 #
# --------------------------------------------------------------------------- #

def decide(
    *,
    attempt: int,
    max_attempts: int,
    claude_outcome: str,
    execution: dict | None,
    branch_exists: bool,
    probe,
    allowed_bots: str,
) -> Decision:
    """Whether to re-run the agent step, and the one line that says why.

    Read in this order, and the order is the safety property: the two cheap
    local facts settle the question before anything touches the network, so a
    run that did real work and a retry budget already spent are never
    "rate-limit questions" at all.
    """
    name = first_bot(allowed_bots)
    call = BOT_LOOKUP_PATH.format(name=name)
    where = f"attempt {attempt} of {max_attempts}"

    if (claude_outcome or "").strip().lower() != "failure":
        return Decision(
            False, REASON_NOT_A_FAILURE, 0, call,
            f"agent step retry: no retry ({where}) — the step did not fail "
            f"(outcome: {claude_outcome or 'unset'})",
        )

    if agent_started(execution, claude_outcome=claude_outcome,
                     branch_exists=branch_exists):
        return Decision(
            False, REASON_MODEL_STARTED, 0, call,
            f"agent step retry: no retry ({where}) — this run REACHED the "
            f"model, so it did real work; re-running it would duplicate a "
            f"build and repeat its spend",
        )

    if attempt >= max_attempts:
        return Decision(
            False, REASON_EXHAUSTED, 0, call,
            f"agent step retry: no retry ({where}) — the retry budget is "
            f"spent; the run fails and the medic files a card",
        )

    result = probe(name)
    if is_rate_limit_refusal(result.status, result.body):
        delay = backoff_seconds(attempt, result.reset_in)
        return Decision(
            True, REASON_RATE_LIMITED, delay, call,
            f"agent step retry: GET {call} answered 403 "
            f"\"API rate limit exceeded\" and the model never started — "
            f"retrying after {delay}s ({where})",
        )

    if result.status is None:
        return Decision(
            False, REASON_PROBE_UNREADABLE, 0, call,
            f"agent step retry: no retry ({where}) — GET {call} could not be "
            f"read, and an unconfirmed limit is not retried into (DRE-1921)",
        )

    return Decision(
        False, REASON_NOT_RATE_LIMITED, 0, call,
        f"agent step retry: no retry ({where}) — GET {call} answered "
        f"{result.status}, so GitHub is not rate-limiting this installation; "
        f"this failure is something else",
    )


# --------------------------------------------------------------------------- #
# The resolved result                                                          #
# --------------------------------------------------------------------------- #

def resolve_attempts(attempts) -> tuple[str, str, int]:
    """(outcome, execution_file, attempts_that_ran) for the whole agent step.

    The LAST attempt that actually ran is the run's result — `skipped` is
    GitHub saying an attempt never happened, so it is passed over rather than
    reported. The execution file falls back to the most recent attempt that
    left one, because a retry that died before the model writes no record and
    must not erase the record of the attempt that did.
    """
    outcome = "skipped"
    exec_file = ""
    ran = 0
    for attempt_outcome, attempt_file in attempts:
        attempt_outcome = (attempt_outcome or "").strip()
        attempt_file = (attempt_file or "").strip()
        if attempt_file:
            exec_file = attempt_file
        if not attempt_outcome or attempt_outcome == "skipped":
            continue
        ran += 1
        outcome = attempt_outcome
    return outcome, exec_file, ran


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #

def _emit(pairs: dict, github_output: str | None) -> None:
    if not github_output:
        return
    try:
        with open(github_output, "a") as fh:
            for key, value in pairs.items():
                fh.write(f"{key}={value}\n")
    except OSError as exc:  # pragma: no cover - a runner with no output file
        print(f"could not write step outputs: {exc}", file=sys.stderr)


def _cmd_decide(args) -> int:
    execution = load_execution(args.execution_file) if args.execution_file else None
    # `GH_TOKEN` is the house name for a token in a step's env — the
    # GITHUB_ prefix is reserved and cannot be assigned there.
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
    decision = decide(
        attempt=args.attempt,
        max_attempts=args.max_attempts,
        claude_outcome=args.claude_outcome,
        execution=execution,
        branch_exists=bool((args.branch or "").strip()),
        probe=live_probe(token),
        allowed_bots=args.allowed_bots,
    )
    print(decision.line)
    _emit(
        {
            "retry": "true" if decision.retry else "false",
            "reason": decision.reason,
            "delay": decision.delay,
            "call": decision.call,
        },
        args.github_output,
    )
    return 0


def _cmd_resolve(args) -> int:
    parsed = []
    for raw in args.attempt:
        outcome, _, exec_file = raw.partition("=")
        parsed.append((outcome, exec_file))
    outcome, exec_file, ran = resolve_attempts(parsed)
    print(
        f"agent step result: {outcome} after {ran} attempt(s)"
        + (f", record at {exec_file}" if exec_file else ", no execution record")
    )
    _emit(
        {"outcome": outcome, "execution_file": exec_file, "attempts": ran},
        args.github_output,
    )
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    d = sub.add_parser("decide", help="retry this agent step, or not")
    d.add_argument("--attempt", type=int, required=True)
    d.add_argument("--max-attempts", type=int, default=MAX_ATTEMPTS)
    d.add_argument("--claude-outcome", default="")
    d.add_argument("--execution-file", default="")
    d.add_argument("--branch", default="")
    d.add_argument("--allowed-bots", default="")
    d.add_argument("--github-output", default="")
    d.set_defaults(func=_cmd_decide)

    r = sub.add_parser("resolve", help="the agent step's effective result")
    r.add_argument("--attempt", action="append", default=[],
                   help="<outcome>=<execution-file>, in attempt order")
    r.add_argument("--github-output", default="")
    r.set_defaults(func=_cmd_resolve)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:  # never fail a build on the classifier's account
        print(f"agent step retry: classifier error, no retry ({exc})")
        _emit({"retry": "false", "reason": "classifier-error", "delay": 0},
              getattr(args, "github_output", ""))
        return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
