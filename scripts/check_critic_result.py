#!/usr/bin/env python3
"""Gate on the QA critic's execution result + verdict (stdlib only).

Mirror of DRE-1346 Fix 1 (check_agent_result.py) for the critic side.

The QA critic runs claude-code-action then is expected to write a verdict to
/tmp/qa-verdict.md whose first non-blank line is `VERDICT: APPROVE` or
`VERDICT: REQUEST_CHANGES`. When that step CRASHES, the Claude execution
result ends {"is_error": true} (auth/startup death — observed ~340ms / 1 turn
/ $0 inference, 2026-06-13) and/or no verdict file is written. qa-review.yml
previously fail-closed and posted a REQUEST_CHANGES verdict with NO real
findings — a false reject that churned good PRs (#1441/#1442) into the fix
loop and spawned duplicate-PR cycles (DRE-1330/1332).

A crash must NEVER yield a real verdict. This gate decides whether a GENUINE
review ran: a real verdict requires is_error != true AND a verdict file that
exists, is non-empty, and declares a VERDICT: line. Anything else means the
review did not really run — the workflow must retry once, then post a NEUTRAL
status (not REQUEST_CHANGES) and fail loudly (medic-visible).

ONE EXCEPTION, DRE-2422: `subtype: error_max_turns` is not a crash in the
sense above. The auth death this gate was built for produced nothing — 634ms,
1 turn, $0. A turn-ceiling death is the opposite: portico PR #273 ran a full
8-minute, 41-turn, $2.05 review and was cut off by the ceiling, and a review
that far along may already have written its finished verdict. Throwing that
away costs a completed review and buys a second identical failure. So a
max-turns run may keep its verdict — but it must be a COMPLETE one (a legal
VERDICT: value plus the mandated `## Summary` section), and the run must show
real work (num_turns > 1). Every other is_error is rejected exactly as before,
so the auth-death fingerprint is untouched.

On any is_error the gate also prints WHY, from the execution file's own
result record (DRE-2435, see execution_result.py) — a whitelist of scalar
fields, never the transcript.

SECOND EXCEPTION-SHAPED RULE, DRE-2466: the critic now writes its verdict
file FIRST as a stub carrying INCOMPLETE_MARKER and rewrites it as it goes,
so a review that ends early leaves something readable behind. A file still
carrying that marker is a receipt, not a verdict — it is never real, on any
path, and its partial content is printed to the run log instead.

DRE-2465: and on the NO-VERDICT path it says what it found instead. Two
things were missing there. First, "no usable verdict file" covers three
different situations — the file is absent, the file is empty, or the file has
content but no line that starts with `VERDICT:` (a critic that wrote
`**VERDICT: APPROVE**` in bold lands in the third and looks exactly like one
that wrote nothing). The workflow rm -f's the file between attempts and the
runner is then destroyed, so if the log does not say which, nobody can ever
find out. Second, a run that ended `is_error: false` got no numbers at all,
and the operator was told it had died at startup: portico PR #297 ran four
times to completion for $12.40 under that notice. Both are printed now, and
the run's own turns/cost also leave here as step outputs so the neutral PR
comment can describe the run instead of asserting a crash.

Called from qa-review.yml after each critic attempt:

    python3 check_critic_result.py <execution-json-path> <verdict-path> \
        [--github-output <path>]

DRE-2924: and a run that spent its TURNS says so. `error_max_turns` is
is_error, so a ceiling death landed on the crash path and the pull request
was told the reviewer had crashed at startup with no inference — over portico
PR #364's first attempt, which spent 62 of its 80 turns and ~$2.56. That is
its own outcome now, and the turns/cost ride on every failing outcome rather
than only on the one that ended cleanly, because those two numbers are the
whole difference between the two deaths.

DRE-3304: and a run that finished EMPTY says WHICH way it was empty. All four
verdict-file failures shared the one word `completed_no_verdict`, and
qa-review.yml's fail step printed "QA critic crashed on both attempts" for
anything that was not turn exhaustion — over run 34170941436, whose two
attempts both ended `subtype: success` (52 turns/$1.56 and 29/$0.93) having
written the DRE-2466 stub and never replaced it. The gate had already computed
and logged that. `cause`/`cause_text` are it, leaving as step outputs so the
red line can say it too, and deliberately EMPTY on a crash — where the verdict
file is never consulted, so naming a cause for it would be the same error in a
new direction.

Exit 0 when a real verdict exists (post it). Exit 1 on crash/no-verdict
(retry, then neutral + loud fail). With --github-output, appends
`outcome=ok|turn_exhaustion|crash|completed_no_verdict|unknown`, `cause=` /
`cause_text=` (only when the verdict FILE is why there is nothing) plus
`turns=` and `cost=` (numbers only, whenever the execution record carries
them) to that file. The flag is optional: verify.yml calls this gate without
it.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from execution_result import (  # noqa: E402
    load_execution as _load_execution,
    print_completion_detail,
    print_failure_detail,
    spend_scalars,
)
from verdict_cause import verdict_decision  # noqa: E402


# The only crash whose verdict may still be believed (DRE-2422). Every other
# is_error stays hard-rejected — see _crash_may_keep_its_verdict.
_MAX_TURNS_SUBTYPE = "error_max_turns"

# The two decisions a critic is allowed to reach.
_LEGAL_VERDICTS = ("APPROVE", "REQUEST_CHANGES")

#: The critic now writes its verdict file FIRST, as a stub, and rewrites it
#: as the review proceeds (DRE-2466) — so a run that ends early leaves
#: something behind instead of the nothing portico PR #297 left four times.
#: This marker is the stub's own header line and the CONTRACT with the
#: prompt: while it is present the review has NOT finished, and the final
#: rewrite removes it. Both critic prompts in qa-review.yml quote this exact
#: string (pinned by tests/test_critic_size_strategy.py) — without the rule
#: below, the stub would post as a REQUEST_CHANGES with no findings and wake
#: the fix agent, which is the false-reject class DRE-1330/1332 opened.
INCOMPLETE_MARKER = "<!-- QA-REVIEW-INCOMPLETE -->"

#: Only the stub's own header counts. A verdict REVIEWING this gate may
#: quote the marker in its findings section, and an honest review of this
#: file must not void itself by mentioning it.
_MARKER_SCAN_LINES = 5


def verdict_is_unfinished(text: str) -> bool:
    """True while the critic's own stub marker still heads the file."""
    return any(
        line.strip() == INCOMPLETE_MARKER
        for line in text.splitlines()[:_MARKER_SCAN_LINES]
    )


# The token a near-miss verdict is a near miss OF. `_verdict_line_present`
# wants it at the start of a stripped line; a critic that bolded it, prefixed
# it, or buried it in prose wrote a review and still fails the gate.
_VERDICT_TOKEN = "VERDICT"


def _read_verdict(path: str) -> bytes | None:
    """The verdict file's raw bytes, or None when there is no file to read.

    Bytes, not text: the critic writes this file after reading a pull request
    written by anyone, and a decode error is not a reason for the gate to
    exit with a traceback instead of a verdict. Callers decode with
    errors="replace" — the file is only ever inspected, never echoed.
    """
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def _verdict_line_present(text: str) -> bool:
    for line in text.splitlines():
        if line.strip().startswith("VERDICT:"):
            return True
    return False


def _verdict_is_complete(text: str) -> bool:
    """Stricter than _verdict_line_present: did the review actually FINISH?

    Only used on the max-turns path, where "the file exists" is not enough —
    we need to tell a review that finished and then hit the ceiling from one
    that was cut off mid-thought. A finished verdict declares one of the two
    legal decisions AND carries the `## Summary` section the critic prompt
    mandates. A review still working has neither.

    The decision is read as the WORD after `VERDICT:` (verdict_cause.py), not
    as the whole rest of the line: since DRE-2489 a rejection also names its
    blocking cause (`VERDICT: REQUEST_CHANGES cause:defect`), and a
    whole-line match would read that as an illegal verdict and throw away a
    completed review — buying a second identical failure, which is exactly
    what DRE-2422 added this path to stop.
    """
    declared = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("VERDICT:"):
            if verdict_decision(stripped) in _LEGAL_VERDICTS:
                declared = True
            break
    if not declared:
        return False
    return any(
        line.strip().lower().startswith("## summary")
        for line in text.splitlines()
    )


def is_turn_exhaustion(execution: dict | None) -> bool:
    """True iff the action itself said it stopped at the turn ceiling.

    DRE-2924. Read off `subtype` alone, which is the action's own statement
    of why it stopped — not inferred from turns or elapsed time, because an
    inference is exactly what got this wrong the first time. The auth death
    carries a different subtype (`error_during_execution`, with an api_error
    terminal reason), so this cannot swallow it.
    """
    return (
        isinstance(execution, dict)
        and execution.get("subtype") == _MAX_TURNS_SUBTYPE
    )


def _crash_may_keep_its_verdict(execution: dict) -> bool:
    """True only for a turn-ceiling death that did real work first.

    THE DISTINCTION THIS GATE RESTS ON. Two things end `is_error: true` and
    they are opposites:

    * The auth/startup death (~634ms, 1 turn, $0, nothing written — seen
      2026-06-13 and again in the 2026-08-09 Fable fleet outage). The agent
      never ran. It has NO opinion, so anything sitting in the verdict file
      is stale or spurious and must never be read as a review. This is the
      case the gate was built for and it is untouched.

    * `error_max_turns` (portico PR #273, 2026-08-13 — 41 turns, 8 minutes,
      $2.05). The agent ran a full review and was cut off by the ceiling.
      It may well have written its finished verdict already.

    Only the second may keep its verdict, and only with real work behind it.
    num_turns > 1 is belt and braces: a one-turn run reviewed nothing no
    matter what subtype it claims.
    """
    if execution.get("subtype") != _MAX_TURNS_SUBTYPE:
        return False
    turns = execution.get("num_turns")
    return isinstance(turns, int) and turns > 1


def verdict_is_real(execution: dict | None, verdict_path: str) -> bool:
    """True iff a genuine review ran and left a usable verdict.

    A crashed execution (is_error=true) is authoritative — even a stale
    verdict file does not rescue it. The ONE exception is a turn-ceiling
    death (DRE-2422), which is a completed review cut short rather than an
    agent that never ran; it may keep its verdict, but only if that verdict
    is COMPLETE (see _verdict_is_complete), not merely present.

    Otherwise the verdict file must exist, be non-empty, and contain a
    `VERDICT:` line.
    """
    crashed = execution is not None and execution.get("is_error") is True
    if crashed and not _crash_may_keep_its_verdict(execution):
        return False
    raw = _read_verdict(verdict_path)
    if raw is None:
        return False
    text = raw.decode("utf-8", "replace")
    if not text.strip():
        return False
    if verdict_is_unfinished(text):
        # The review started and did not finish. That file is a receipt, not
        # a verdict: posting it would request changes nobody found (the
        # #1441/#1442 churn), and on the max-turns path it is precisely what
        # DRE-2422's keep-the-verdict exception must not rescue.
        return False
    if crashed:
        # Higher bar on the crash path only. Failing it lands exactly where
        # today's code lands — retry, then neutral + loud fail — so this can
        # only ever rescue a verdict, never manufacture one.
        return _verdict_is_complete(text)
    return _verdict_line_present(text)


def verdict_file_report(verdict_path: str) -> list[str]:
    """What the gate FOUND where the verdict should have been.

    Booleans and a byte count — never a byte of the file itself. The critic
    writes it having just read a pull request authored by anyone, so its
    content is untrusted and stays out of a log that is public on a public
    repo (same discipline as execution_result.py's field whitelist).

    Three situations hide behind one message today, and /tmp/qa-verdict.md is
    deleted between attempts, so the log is the only place the difference can
    ever be recorded: absent, empty, or present with content that simply
    never starts a line with `VERDICT:`.
    """
    raw = _read_verdict(verdict_path)
    if raw is None:
        return ["  existed: no"]
    text = raw.decode("utf-8", "replace")
    lines = [
        "  existed: yes",
        f"  bytes: {len(raw)}",
        f"  non-blank: {'yes' if text.strip() else 'no'}",
    ]
    if not _verdict_line_present(text):
        near = _VERDICT_TOKEN in text
        lines.append(
            f"  near-miss (a {_VERDICT_TOKEN!r} token is present but no line "
            f"starts with '{_VERDICT_TOKEN}:'): {'yes' if near else 'no'}"
        )
    return lines


def print_verdict_file_report(verdict_path: str, prefix: str) -> None:
    print(f"{prefix}: what was at {verdict_path}:")
    for line in verdict_file_report(verdict_path):
        print(line)


def outcome(execution: dict | None, real: bool) -> str:
    """One word for what happened, for the workflow to pick its message from.

    * `ok` — a genuine verdict; nothing to explain.
    * `turn_exhaustion` — the run hit its TURN CEILING and left no complete
      verdict (DRE-2924, portico PR #364: 62 of 80 turns, ~$2.56, nothing
      readable). It is `is_error: true` and it used to land on `crash`, so
      the pull request was told the reviewer had never started. A retry hits
      the same wall; the remedy is a smaller change, not a credential.
    * `crash` — every other is_error. The auth/startup death the neutral
      notice was written for, and still the only thing that notice may
      describe.
    * `completed_no_verdict` — the run ENDED CLEANLY and left nothing usable
      (portico PR #297). Blaming a credential for this is what cost a day.
    * `unknown` — no execution record at all. We cannot prove it ran, so we
      do not claim it did; the workflow keeps the crash wording here.
    """
    if real:
        return "ok"
    if execution is None:
        return "unknown"
    if execution.get("is_error") is True:
        if is_turn_exhaustion(execution):
            return "turn_exhaustion"
        return "crash"
    return "completed_no_verdict"


#: WHICH of the four ways the verdict file was unusable, and the sentence the
#: workflow prints for each (DRE-3304). One table, so the enum and the English
#: cannot drift into disagreeing about the same run.
#:
#: The gate has always COMPUTED all four — verdict_file_report and
#: _print_unfinished_verdict print them to the log — and then thrown them away:
#: `outcome()` returns the single word `completed_no_verdict` for all of them.
#: Run 34170941436 was the unfinished stub, and its red line said "crashed".
#:
#: Every value here is a fixed string written in this file. NONE of it comes
#: from the verdict file, which is written by an agent that has just read a
#: pull request authored by anyone — same discipline as verdict_file_report,
#: which reports booleans and a byte count and never content.
_NO_VERDICT_CAUSES = {
    "unfinished_stub": (
        "the reviewer wrote its in-progress stub and never replaced it, so the "
        "file still declares the review unfinished — it stopped part-way "
        "through rather than reaching a decision"
    ),
    "verdict_absent": "no verdict file was written at all",
    "verdict_empty": "the verdict file was written and left empty",
    "no_verdict_line": (
        "the verdict file has content but no line begins with 'VERDICT:', so "
        "the gate could not read a decision out of it"
    ),
    "verdict_incomplete": (
        "the verdict file declares a decision but is missing the mandated "
        "'## Summary' section, so the review had not finished"
    ),
}

#: What the workflow says when no cause was recorded. Deliberately an
#: admission: the failure this card exists for is a message that asserted more
#: than the run supported, and the fix for that is not a different assertion.
#: qa-review.yml carries this string too, as the fallback in its fail step,
#: and tests/test_critic_no_verdict_cause.py holds the two byte-identical.
UNKNOWN_CAUSE_TEXT = (
    "the gate could not tell which way the verdict file was unusable, so this "
    "line will not guess — the job log is where the answer still is"
)


def no_verdict_cause(execution: dict | None, verdict_path: str) -> str:
    """WHICH way the verdict file was unusable, or "" when that is not the ask.

    Empty on three paths, each for the same reason — the file was not why we
    have nothing, so naming a file cause would invent one:

    * a real verdict (nothing to explain);
    * a crash that may not keep its verdict, where `verdict_is_real` returns
      before reading the file at all;
    * anything this function cannot classify.

    Returns a bare `[a-z_]` token from _NO_VERDICT_CAUSES. $GITHUB_OUTPUT is
    line-oriented, so a value that could carry a newline could write a step
    output of its own — `real=true` among them. A token from a fixed table
    cannot.
    """
    crashed = execution is not None and execution.get("is_error") is True
    if crashed and not _crash_may_keep_its_verdict(execution):
        return ""
    raw = _read_verdict(verdict_path)
    if raw is None:
        return "verdict_absent"
    text = raw.decode("utf-8", "replace")
    if not text.strip():
        return "verdict_empty"
    if verdict_is_unfinished(text):
        return "unfinished_stub"
    if not _verdict_line_present(text):
        return "no_verdict_line"
    if crashed and not _verdict_is_complete(text):
        # The max-turns keep-the-verdict path (DRE-2422), refused: a decision
        # is declared but the review had not finished writing it.
        return "verdict_incomplete"
    return ""


def cause_text(cause: str) -> str:
    """The one-line English for a cause token, from the same table."""
    return _NO_VERDICT_CAUSES.get(cause, UNKNOWN_CAUSE_TEXT)


def write_step_outputs(
    path: str, execution: dict | None, real: bool, verdict_path: str = ""
) -> None:
    """Append `outcome`/`cause`/`cause_text`/`turns`/`cost` to $GITHUB_OUTPUT.

    Numbers only, straight from spend_scalars' whitelist: $GITHUB_OUTPUT is
    line-oriented, so a value carrying a newline would write a step output of
    its own — `real=true` among them. A float cannot.

    DRE-2924: the numbers ride on EVERY failing outcome, not only on the one
    that ended cleanly. A turn-ceiling death that publishes nothing is
    indistinguishable from an agent that never started, and the numbers are
    the only thing that tells them apart — the auth death's own 1-turn/$0
    shape included.

    DRE-3304: and `cause`/`cause_text` carry WHICH way the verdict file was
    unusable, so the job's red line can name it instead of the workflow
    inferring "crash" from the absence of any other word. Both come from
    _NO_VERDICT_CAUSES — a fixed table in this file, never the verdict file's
    own bytes — and the newline strip below is belt and braces over that: a
    constant cannot carry one, and a table someone later edits carelessly
    still cannot write a second step output.
    """
    lines = [f"outcome={outcome(execution, real)}"]
    cause = no_verdict_cause(execution, verdict_path) if not real else ""
    if cause:
        lines.append(f"cause={cause}")
        lines.append(f"cause_text={cause_text(cause).replace(chr(10), ' ')}")
    scalars = spend_scalars(execution)
    turns = scalars.get("num_turns")
    cost = scalars.get("total_cost_usd")
    if turns is not None:
        lines.append(f"turns={int(turns)}")
    if cost is not None:
        # Two decimals: this ends up in a sentence a human reads, not in an
        # invoice.
        lines.append(f"cost={float(cost):.2f}")
    try:
        with open(path, "a") as f:
            f.write("".join(f"{line}\n" for line in lines))
    except OSError as exc:
        # A gate that cannot write its outputs still has a verdict to report.
        print(f"critic result gate: could not write step outputs: {exc}")


def main(argv: list[str]) -> int:
    args, output_path = [], None
    rest = list(argv)
    while rest:
        arg = rest.pop(0)
        if arg == "--github-output":
            output_path = rest.pop(0) if rest else None
        else:
            args.append(arg)
    exec_path, verdict_path = (args + ["", ""])[:2]
    execution = _load_execution(exec_path)
    crashed = execution is not None and execution.get("is_error") is True
    real = verdict_is_real(execution, verdict_path)
    if output_path:
        write_step_outputs(output_path, execution, real, verdict_path)
    if real:
        if crashed:
            # Say so out loud: the run is red in the Actions UI but its
            # verdict counted. Anyone reading the log needs to see why.
            print(
                "critic result gate: ok — the review hit the turn ceiling "
                "(subtype=error_max_turns, num_turns="
                f"{execution.get('num_turns')}) but had already written a "
                "complete verdict, so it stands (DRE-2422). Raise "
                "--max-turns if this recurs."
            )
            print_failure_detail(execution, "critic result gate")
        else:
            print("critic result gate: ok — real verdict")
        return 0
    if crashed and is_turn_exhaustion(execution):
        # DRE-2924. Turn exhaustion is is_error, so it used to print — and
        # post — the auth-death wording. It is the opposite of an agent that
        # never started, and the next reader must not be sent to rotate a
        # credential that is fine.
        print(
            "critic result gate: FAIL — the review ran out of TURNS "
            f"(subtype={_MAX_TURNS_SUBTYPE}, num_turns="
            f"{execution.get('num_turns')}) and left no complete verdict. "
            "This is turn exhaustion, NOT a startup/auth failure: the "
            "reviewer authenticated, did real work and was billed for it, so "
            "no credential needs rotating on the strength of this. A retry "
            "hits the same wall: same ceiling, same work. For the CRITIC that "
            "means the pull request is too large for the strategy it was "
            "routed to (pr_size_strategy.py) — verify.yml shares this gate "
            "and owns its own remedy."
        )
        print_failure_detail(execution, "critic result gate")
    elif crashed:
        print(
            "critic result gate: FAIL — execution result has is_error=true "
            f"(subtype={execution.get('subtype')!r})"
        )
        # DRE-2435: and WHY, in the run's own words. Without this the log
        # shows only 1 turn / $0 — which reads identically for an expired
        # token, an overloaded API, a refusal and a bad model id. That
        # ambiguity sent people credential-hunting for days.
        print_failure_detail(execution, "critic result gate")
    else:
        print("critic result gate: FAIL — no usable verdict file")
        # DRE-2465. WHICH no-verdict: absent, empty, or content that never
        # starts a line with VERDICT: — the file is deleted before the retry,
        # so nothing else will ever record the difference.
        print_verdict_file_report(verdict_path, "critic result gate")
        _print_unfinished_verdict(verdict_path)
        if execution is not None:
            print(
                "critic result gate: the reviewer authenticated and did real "
                "work — this is NOT an auth/startup failure and rotating a "
                "credential will not fix it (DRE-2465)"
            )
            print_completion_detail(execution, "critic result gate")
        else:
            print(
                "critic result gate: no execution record was found either, "
                "so whether the reviewer ran at all is unknown"
            )
    return 1


#: How much of an unfinished verdict reaches the log. Enough to read the
#: findings the review did reach; not a transcript dump (show_full_output
#: stays off — tests/test_execution_failure_detail.py).
_UNFINISHED_LOG_CHARS = 4_000


def _print_unfinished_verdict(verdict_path: str) -> None:
    """Show what an unfinished review DID find (DRE-2466).

    The verdict cannot be posted — it declares itself unfinished — but the
    partial findings are the only surviving evidence of how far the review
    got, and portico PR #297 was diagnosed entirely from run records. This
    is the critic's own output, which the passing path posts verbatim as a
    PR comment; logging it exposes nothing new.
    """
    raw = _read_verdict(verdict_path)
    if raw is None:
        return
    text = raw.decode("utf-8", "replace")
    if not verdict_is_unfinished(text):
        return
    print(
        "critic result gate: the review left an UNFINISHED verdict "
        f"({len(text)} chars) — it wrote the stub and never replaced it. "
        "Partial content follows; it is NOT posted as a verdict."
    )
    print(text[:_UNFINISHED_LOG_CHARS])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
