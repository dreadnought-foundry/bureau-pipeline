#!/usr/bin/env python3
"""Classify a failed pipeline run: is it a CRITIC INFRA-CRASH the medic must
NOT re-run, or a normal failure it should retry/diagnose? (stdlib only.)

DRE-1921 (the #1 quota-burn fix). When the QA critic *job* crashes on
INFRASTRUCTURE — a GitHub rate-limit (`API rate limit exceeded for installation
ID …`) or an auth/startup death — qa-review.yml deliberately posts a NEUTRAL
"could not run (infra error)" comment and then FAILS the job loudly "for medic
visibility" (qa-review.yml's final step). That FAILURE is a `workflow_run`
event the medic watches, so the medic:

  1. `retry` job → `gh run rerun --failed` re-runs the critic, which hits the
     SAME rate-limit and crashes again (deepening the limit), then
  2. `diagnose` job → spins up a diagnosis agent (more GitHub API + inference).

Each iteration burns more of the bot's GitHub quota, which extends the
rate-limit window — a self-reinforcing loop. On 2026-06-28 six PRs were stuck
in it and it burned the bot's GitHub quota twice.

The bug is that the medic treats a critic INFRA-CRASH (no verdict, reviewer was
DOWN) the same as a real failure worth retrying. It is not: re-running a
critic against an exhausted rate-limit cannot succeed and only makes the limit
worse. The medic must instead BACK OFF — neither rerun nor diagnose — and let
a later natural trigger (a new push, the rate-limit window resetting) re-review.

A REAL `REQUEST_CHANGES` verdict is unaffected: that is not a crash, the critic
ran, and it routes through agent-fix.yml (which fires only on
"VERDICT: REQUEST_CHANGES"), never through this path.

Detection — an infra-crash leaves a fingerprint in the FAILED RUN'S LOGS:
  * the critic posted the neutral marker comment whose body the qa-review job
    also echoes — "QA Critic could not run (infra error)"; and/or
  * a rate-limit / auth signature appears: "API rate limit exceeded",
    "rate limit", "HTTP 403" + "rate", "secondary rate limit", "is_error".

We classify off the run name + its failed-step log text. Only the QA-Review
workflow can be a *critic* infra-crash (other workflows' rate-limits are real
failures the medic should still retry/diagnose once).

SECOND CLASS — UPSTREAM 5xx (DRE-2488). On 2026-08-17 GitHub itself was down:
api.github.com answered `HTTP 503: No server is currently available to service
your request`, portico's scheduled Reconcile sweeps died on it, the medic
retried into the same outage, the diagnosis agent fired, and the operator got
10 red-run emails (5 reconcile + 5 medic) for something nobody in the fleet can
fix. A GitHub-wide 5xx is not a pipeline failure and must not read as one — so
it gets the same shape as the critic rate-limit class: no retry (retrying into
an outage makes it worse; scheduled workflows re-run themselves), no diagnosis
agent, one `::notice::`, and the medic's own run stays green.

Detection is anchored to the API-ERROR LINE SHAPE — a single log line that
names `api.github.com` AND carries a 502/503/504 (the `gh` CLI's
"failed to get runs: HTTP 503: … (https://api.github.com/…)" and an HTTP
client's "non-200 OK status code: 503"), or the CLI's self-identifying
"gh: <message> (HTTP 503)" line. Prose that merely mentions 503 — a diff hunk,
a card body quoted into an agent log — must NOT classify, or a genuine failure
would be silently swallowed.

THIRD CLASS — LINEAR RATE LIMIT (DRE-2923). On 2026-09-01 four consecutive
reconcile sweeps in agent-bureau died because the Linear workspace's
2500/hour quota was exhausted by read-only diagnostics minutes earlier. That
is a transient, SELF-HEALING condition needing no code change at all — but it
is not a defect in the estate either, and retrying into an exhausted quota
deepens it exactly the way DRE-1921's loop did. So it gets the same shape as
the two classes above: no retry, no diagnosis agent, one `::notice::`, and the
medic's own run stays green. The sweep itself still exits non-zero, so a board
that has stopped being reconciled never goes quiet.

Detection is anchored to the CLIENT'S ERROR LINE — a single line naming
Linear's API host AND carrying the rate-limit fingerprint (the `RATELIMITED`
code, or the named condition the client now composes). Line-anchored for the
same reason as the class above: DRE-2923's own card body quotes the
RATELIMITED payload, so an agent-task log that merely repeats it must NOT
classify, or a genuine failure would be silently swallowed.

FOURTH CLASS — THE RUNNER CANNOT RUN CLAUDE (DRE-3428). On 2026-09-08
(DRE-3416) the floating `claude-code-action@v1` tag moved and all six kinds of
Claude-running job in the fleet died about thirteen seconds in with `Claude
Code native binary not found`. This classifier called it a critic infra crash
— and only because the neutral marker happened to be in the same log, so
nothing here named the cause; the sweep's `reviewer-down` note then sent the
operator to "check the critic's auth/token", the one thing that was not wrong.

`scripts/reviewer_environment.py` owns that vocabulary — four positive
signatures, each with the plain-English meaning and the check that confirms
it — and this file asks it BEFORE `critic_infra_crash`, because the neutral
marker is in the same log and would otherwise win again.

The DRE-1921 GATE IS UNCHANGED by it: `infra_crash=true` is still printed for
a QA-review run, so the medic's three gates (retry, diagnose, backoff) behave
exactly as they did. What is new is that the run now also says WHICH
environment failure it was, and where to go and look.

FIFTH CLASS — THE RUN WENT SILENT (DRE-3991). On 2026-09-15 the red-main
repair agent emitted a successful `system/init` and then zero further stream
events, twice in a row, and was killed from outside at 8m27s and 11m39s. It
never read the failing build, never wrote a line, and the card got a dead run
whose cause nobody could name — while three wrong causes were all available.
`scripts/stream_watchdog.py` now stops such a run after five minutes of
SILENCE (never on elapsed time — a busy run is never cut off) and writes one
line into the run log. This classifier reads that line back and gives the
failure its own name, `stalled_no_stream`, ahead of the critic infra-crash
for the same reason the environment crash is ahead of it: a review that went
quiet also posts the neutral marker into the same log.

The stall keeps `infra_crash=false`, which is deliberate — it is the medic's
ONE automatic retry that a stall is entitled to. The second one in a row is
refused by `medic_retry`, not here.

SIXTH CLASS — THE MACHINE RAN OUT OF MEMORY (DRE-4847). On 2026-09-24 four
build and fix runs were SIGKILLed on 2 GB light runners and every instrument
misread them, so a person diagnosed each one by hand. The tell was in the log
this classifier already fetches: `##[error]Process completed with exit code
137.` on a step, which is a container at its memory limit and the kernel's
out-of-memory killer ending the process. `scripts/out_of_memory.py` owns that
reading — one kill line is the rule, the `Runner name:` line says which class
of machine it was, and a log carrying the watchdog's stall line is a stall and
not this.

It is checked after `environment_crash` and `stalled_no_stream` and BEFORE
`critic_infra_crash`, for the third repetition of the same lesson: a critic
killed mid-review writes no execution file, so qa-review posts its neutral
marker into the same log, and on three of portico's runs that made
`critic_infra_crash` win and the medic told the card "an infrastructure
rate-limit … deliberately NOT retrying". The kill keeps `infra_crash=false`,
which is deliberate — a first kill on a LIGHT runner is entitled to the medic's
one automatic retry. Every other case is refused by `medic_retry`, not here.

CLI:
    python3 medic_classify.py <workflow-name> <log-file>
prints `infra_crash=true|false` (the DRE-1921 gate, unchanged),
`class=environment_crash|stalled_no_stream|out_of_memory|critic_infra_crash|upstream_5xx|linear_ratelimited|normal`,
then `signature=`, `check=` and `meaning=` (empty unless the class is
`environment_crash`) and `stall_step=`, `stall_last_event=` and
`stall_silence=` (empty unless the class is `stalled_no_stream`), plus a human
line on stderr; exit 0 either way. The caller (medic.yml) appends the stdout
lines to `$GITHUB_OUTPUT`, so every value is one line.
"""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import out_of_memory  # noqa: E402
import reviewer_environment  # noqa: E402
import stream_watchdog  # noqa: E402

# The exact neutral marker qa-review.yml posts + echoes when the critic crashes
# on infra (qa-review.yml "Post verdict or neutral status" step). Matching this
# is the strongest, most specific signal — it means the CRITIC itself declared
# an infra failure, not a code rejection.
CRITIC_NEUTRAL_MARKER = "QA Critic could not run (infra error)"

# Rate-limit / auth-death signatures (case-insensitive). A GitHub rate-limit is
# the documented 2026-06-28 cause; an auth/startup death (is_error, no inference)
# is the other infra-crash mode the critic gate already fail-closes on.
_INFRA_SIGNATURES = (
    re.compile(r"api rate limit exceeded", re.I),
    re.compile(r"secondary rate limit", re.I),
    re.compile(r"\brate limit\b", re.I),
    re.compile(r"x-ratelimit-remaining[\"'\s:]+0", re.I),
    re.compile(r"403[^\n]*rate", re.I),
)


# ── upstream 5xx (DRE-2488) ──────────────────────────────────────────────────
# GitHub's own host. A GitHub API error line always names it; prose quoting a
# 503 does not. Requiring it ON THE SAME LINE as the status is what keeps a
# card body or diff hunk that mentions "503" out of this class.
_GH_API_HOST = "api.github.com"

# The 5xx status shapes we accept on such a line. 502/503/504 only: those are
# GitHub-side server errors. 500 is deliberately excluded — it is also what a
# broken request of ours returns, and we must not swallow our own bugs.
_UPSTREAM_5XX_SHAPES = (
    # `gh run view`/`gh pr list` style: "failed to get runs: HTTP 503: No
    # server is currently available to service your request. (https://api…)"
    re.compile(r"\bHTTP 50[234]\b\s*:", re.I),
    # HTTP-client wrappers (octokit/go): "non-200 OK status code: 503 Service
    # Unavailable".
    re.compile(r"non-200 OK status code:\s*50[234]\b", re.I),
    # Bare status + reason phrase, e.g. "503 Service Unavailable".
    re.compile(r"\b50[234]\s+(?:service unavailable|bad gateway|gateway time)", re.I),
)

# `gh api`'s own error line — "gh: <message> (HTTP 503)" — carries no URL, but
# the `gh: … (HTTP 50x)` shape is the CLI identifying itself and is not prose.
_GH_CLI_STATUS_LINE = re.compile(r"\bgh:\s[^\n]*\(HTTP 50[234]\)", re.I)


def is_upstream_5xx(log_text: str) -> bool:
    """True iff the failed run's logs show GitHub itself returning 5xx — an
    UPSTREAM OUTAGE the medic must back off from (no retry, no diagnosis), not
    a pipeline failure. Line-anchored on purpose: see the module docstring.
    """
    for line in (log_text or "").splitlines():
        if _GH_CLI_STATUS_LINE.search(line):
            return True
        if _GH_API_HOST in line and any(s.search(line) for s in _UPSTREAM_5XX_SHAPES):
            return True
    return False


# ── Linear rate limit (DRE-2923) ─────────────────────────────────────────────
# Linear's API host. The client's error line always names the endpoint it
# called (that is half of DRE-2923's fix); prose quoting a rate-limit body does
# not. Requiring it ON THE SAME LINE as the fingerprint is what keeps this
# card's own text, quoted into an agent log, out of this class.
_LINEAR_API_HOST = "api.linear.app"

# The fingerprints. `RATELIMITED` is Linear's own extension code, carried in
# the body the client now captures; the second is the named condition
# linear_ops composes in front of it (`rate limited: 2500 requests/hour
# exhausted`) — matched so the class survives a body we truncated past the code.
_LINEAR_RATELIMIT_SHAPES = (
    re.compile(r"\bRATELIMITED\b"),
    re.compile(r"rate limited:\s*[\d,]+\s+requests/\w+\s+exhausted", re.I),
    re.compile(r"rate limited:\s*workspace request quota exhausted", re.I),
)


def is_linear_rate_limited(log_text: str) -> bool:
    """True iff the failed run's logs show LINEAR answering a rate limit — a
    self-healing quota exhaustion the medic must back off from (no retry, no
    diagnosis), not a defect in the estate. Line-anchored on purpose: see the
    module docstring.
    """
    for line in (log_text or "").splitlines():
        if _LINEAR_API_HOST in line and any(
            s.search(line) for s in _LINEAR_RATELIMIT_SHAPES
        ):
            return True
    return False


def classify(workflow_name: str, log_text: str) -> str:
    """The failed run's class: `environment_crash` (DRE-3428 — this runner
    cannot run Claude at all), `stalled_no_stream` (DRE-3991 — the run went
    quiet and the watchdog stopped it), `out_of_memory` (DRE-4847 — the machine
    hit its memory limit and the kernel killed the job), `critic_infra_crash`
    (DRE-1921 — back off, the reviewer was down), `upstream_5xx` (DRE-2488 —
    GitHub is down, back off), `linear_ratelimited` (DRE-2923 — the workspace
    quota is exhausted, back off), or `normal` (retry once, then diagnose).

    The environment crash is checked FIRST and that ordering is the whole
    point of DRE-3428: the crashed review posts the neutral marker into the
    same log, so `critic_infra_crash` won on 2026-09-08 and the cause was
    never named. The DRE-1921 gate below is unaffected — a QA-review run
    still prints `infra_crash=true`.

    THE STALL IS CHECKED NEXT, ahead of the critic infra-crash, for exactly
    that reason a second time: a review that went quiet posts the neutral
    marker into the same log too, and the cause the next reader needs is the
    silence, not the reviewer's rate limit.

    AND THE KILL IS CHECKED NEXT, a third time for the same reason: a critic
    killed mid-review writes no execution file, so qa-review always falls into
    the neutral branch and the marker is in that log too. It sits after the
    stall because the watchdog's own line says which of the two happened
    (`out_of_memory.from_log` reads the stall and stands down).
    """
    if reviewer_environment.detect(log_text) is not None:
        return "environment_crash"
    if stream_watchdog.stall_from_log(log_text) is not None:
        return "stalled_no_stream"
    if out_of_memory.from_log(log_text) is not None:
        return "out_of_memory"
    if is_critic_infra_crash(workflow_name, log_text):
        return "critic_infra_crash"
    if is_upstream_5xx(log_text):
        return "upstream_5xx"
    if is_linear_rate_limited(log_text):
        return "linear_ratelimited"
    return "normal"


def _is_qa_review(workflow_name: str) -> bool:
    """Only the QA-Review workflow can be a *critic* infra-crash. Match the
    reusable workflow's name ("QA Review (reusable)") and any stub that embeds
    "QA Review", case-insensitively, so a renamed consuming stub still matches.
    """
    return "qa review" in (workflow_name or "").lower()


def is_critic_infra_crash(workflow_name: str, log_text: str) -> bool:
    """True iff this failed run is a QA-critic infra-crash the medic must NOT
    rerun/diagnose. Requires (a) it is the QA-Review workflow AND (b) the logs
    carry the neutral critic marker OR a rate-limit/auth signature.
    """
    if not _is_qa_review(workflow_name):
        return False
    text = log_text or ""
    if CRITIC_NEUTRAL_MARKER in text:
        return True
    return any(sig.search(text) for sig in _INFRA_SIGNATURES)


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def main(argv: list[str]) -> int:
    workflow_name, log_file = (argv + ["", ""])[:2]
    log_text = _read(log_file)
    kind = classify(workflow_name, log_text)
    environment = (
        reviewer_environment.detect(log_text)
        if kind == "environment_crash" else None
    )
    # The DRE-1921 gate, unchanged in meaning: a QA-review run that crashed on
    # infrastructure. An environment crash of the review IS one — the medic
    # must not rerun it and must not spend a diagnosis agent on it — so it
    # keeps the gate, and the three jobs in medic.yml are untouched by this
    # card. A non-review run says `false`, exactly as it did before.
    crash = kind == "critic_infra_crash" or (
        kind == "environment_crash" and _is_qa_review(workflow_name)
    )
    print(f"infra_crash={'true' if crash else 'false'}")
    print(f"class={kind}")
    for line in reviewer_environment.report_lines(environment):
        print(line)
    # The stall's own three facts — the step that went quiet, when it last
    # spoke, and for how long — on every class, empty on all but one. They are
    # what `stall_record` writes onto the card, and a classifier that named
    # the class without them would leave the medic re-reading the log.
    stall = (
        stream_watchdog.stall_from_log(log_text)
        if kind == "stalled_no_stream" else None
    )
    for line in stream_watchdog.report_lines(stall):
        print(line)
    if kind == "stalled_no_stream" and stall is not None:
        print(
            f"medic classify: STALL — {stall.step} emitted no stream event "
            f"for {stall.silence_seconds}s after system/init (last event "
            f"{stall.last_event}), so the watchdog stopped it. Not a code "
            f"failure, not a credential failure, not a usage limit. The "
            f"medic retries a stall once; a second one in a row gets a "
            f"notice instead.",
            file=sys.stderr,
        )
    elif kind == "out_of_memory":
        # The plain-English line the 2026-09-24 operator did not have: WHICH
        # machine, and what size it is — because that is what decides whether
        # running it again can help.
        kill = out_of_memory.from_log(log_text)
        print(
            f"medic classify: OUT OF MEMORY — {out_of_memory.describe(kill)}. "
            f"Not a code failure, not a credential failure, and not the "
            f"watchdog (it stops a silent run with exit "
            f"{stream_watchdog.STALL_EXIT} and writes a line saying so). A "
            f"first kill on a light runner keeps the medic's one retry; a "
            f"heavier or GitHub-hosted machine gets none, because the same "
            f"work on the same size of machine dies the same way.",
            file=sys.stderr,
        )
    elif environment is not None:
        print(
            "medic classify: ENVIRONMENT CRASH — this runner cannot run "
            f"Claude ({environment.slug}): {environment.meaning}. Not the "
            "critic's credential and not a code rejection. Check: "
            f"{environment.check}.",
            file=sys.stderr,
        )
    elif crash:
        print(
            "medic classify: QA critic INFRA-CRASH (rate-limit/auth) — backing "
            "off, NOT rerunning (would deepen the limit and loop).",
            file=sys.stderr,
        )
    elif kind == "upstream_5xx":
        print(
            "medic classify: UPSTREAM OUTAGE — GitHub's API returned 5xx. "
            "Backing off: no retry (it would hit the same outage), no "
            "diagnosis (nobody in the fleet can fix GitHub).",
            file=sys.stderr,
        )
    elif kind == "linear_ratelimited":
        print(
            "medic classify: LINEAR RATE LIMIT — the workspace request quota is "
            "exhausted. Backing off: no retry (it would deepen the limit), no "
            "diagnosis (there is no defect to find). The quota refills on its "
            "own and the next scheduled sweep reconciles the board.",
            file=sys.stderr,
        )
    else:
        print(
            "medic classify: not a critic infra-crash — normal medic handling.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
