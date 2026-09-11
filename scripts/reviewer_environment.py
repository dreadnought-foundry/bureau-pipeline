#!/usr/bin/env python3
""""The runner cannot run Claude" — the vocabulary, the class, and the one
hold receipt (DRE-3428). Stdlib only.

On 2026-09-08 (DRE-3416) the floating `claude-code-action@v1` tag moved and
all six kinds of Claude-running job in the fleet — critic, verifier, engineer,
planner, medic, fix agent — died about thirteen seconds in with `Claude Code
native binary not found`. Nothing in the pipeline had a name for that.
`medic_classify` called it a critic infra crash, and only because the neutral
marker happened to be in the same log; the sweep's `reviewer-down` note then
told the operator to "check the critic's auth/token", which was the one thing
that was not wrong.

This module is the answer two siblings read — the medic, which leaves the
evidence note after the first crash, and the reconcile sweep, which posts the
hold after the second — so they cite one answer instead of inventing two
(standards/card-quality.md: a card whose pieces read a shared rule splits that
rule out FIRST).

It owns three things and nothing else. It does not wire the medic, it does not
wire the sweep, and it moves no card: `state` on its act row is `unchanged`,
exactly as `reviewer-down`'s is — the hold lives in the receipt.

## 1. The signature table

Four POSITIVE signatures. Each carries a slug, a line-anchored pattern, a
one-line plain-English meaning and the check that confirms it — the check
matters because the whole cost of DRE-3416 was an operator sent to the wrong
one.

LINE-ANCHORED IS THE DRE-2488/2923 DISCIPLINE, and here the adversary is this
card's own body: it quotes every one of these phrases, and an agent-task log
that merely repeats a card body must not classify or a genuine failure is
silently swallowed. Two guards, both cheap:

  * the line is read after its Actions log prefix is stripped, and a line that
    opens with a diff or quotation marker (`+`, `-`, `>`, `|`) is not a report
    of anything — it is a quotation of one;
  * a phrase immediately preceded by a backtick is being QUOTED. That is how
    every card body, comment and brief in this pipeline writes a machine
    string, and it is how DRE-3428's own description writes all four of these.

`authentication-error` additionally requires `API Error` or `"type"` on the
SAME line, which is the shape DRE-2488 gave a 5xx: the token alone is a word,
and prose carries words.

## 2. `detect()`

The first matching signature in table order, or None. The credential shape is
NOT a regex — it is the run's own execution record (`is_error`, one turn or
none, $0, sub-second) — and it is read through the two shipped readers,
`medic_retry.execution_from_log()` and
`check_agent_result.has_service_outage_signature()`, never re-derived here.
A second opinion about what a refused credential looks like is exactly how
`alerts._advanced_recently` and `project_rollups.is_stale` came to disagree
about one field.

WHAT THAT COSTS TODAY, said out loud: `execution_result._DIAGNOSTIC_FIELDS` —
the whitelist the gate prints under `FAILURE_HEADER` — carries `subtype`,
`api_error_status`, `stop_reason`, `terminal_reason`, `errors` and `result`,
and none of the three NUMBERS the outage signature reads. So a real log
carries the credential record only when those numbers are in the block, and
until they are, a refused credential is caught by `authentication-error` off
the `result`/`errors` line instead. Widening that whitelist is a change to the
gate, which this card does not own.

## 3. The two bodies

The **evidence note** the medic leaves on the card after the FIRST crash. It
is a report about the commit, not something the pipeline did on its own — it
creates no obligation and hands the work to nobody — so it is declared
`not-an-act` in the registry's `unconverted` block rather than given a row.

Its first words are `CRITIC_UNAVAILABLE_MARKER`, byte-identical to the note
`.github/workflows/medic.yml`'s `backoff` job posts today, and that is a
CONTRACT with epic DRE-3420: its fleet-wide outage detector counts every
Linear comment STARTING with that phrase as one could-not-run outcome from
that repository, and it is how a second repository's crash is seen at all. A
note that dropped the phrase would blind the fleet alarm for exactly the crash
class this epic names — so the phrase comes first and the environment detail
after it, and a test reads medic.yml and asserts the phrase is still there.

Both READS of that note live here too — `evidence_for_head` for "is there one
for this head" and `signature_from_evidence` for "what does it say the cause
is" (DRE-3431). The sweep needs the second to compose a hold at all, and a
module that wrote a format somebody else parsed would have handed out two
answers about one sentence.

The **hold receipt**, composed through `pipeline_act.receipt()` and posted by
`post_hold()` to the pull request (the sha-bound counter the sweep reads) and
mirrored to the card (what the console and the fleet alarm read). It carries
the crash phrase NOWHERE: a hold is mirrored to the card, and the detector
must count crashes, never holds.

Neither body may carry `linear_ops.CONSOLE_HOLD_MARKERS` — the console reads
either sentence as a FIX-BUDGET hold and renders the wrong kind of stuck card
— and the note may carry no live idempotency key at all, because `tag in body`
is how every one of those is counted.

`is_release_act()` delegates to `review_rerun.is_rerun_act`. The re-run act's
own line is written once, in `review_rerun.RERUN_REVIEW_ACT` (DRE-3286), and
is interpolated here rather than restated — the console and the relay mirror
that string too, so a second copy is a second thing to reword.

CLI:

    reviewer_environment.py detect <log-file>
    reviewer_environment.py note --card C --sha S --signature SLUG --run-url U
    reviewer_environment.py hold --card C --sha S --signature SLUG --count N \\
        [--subject S] [--repo OWNER/NAME --pr N]
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess  # nosec B404 — fixed-arg `gh` calls, shell=False
import sys
from typing import NamedTuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import check_agent_result  # noqa: E402
import medic_retry  # noqa: E402
import pipeline_act  # noqa: E402
import review_rerun  # noqa: E402


# --------------------------------------------------------------------------- #
# the signature table                                                          #
# --------------------------------------------------------------------------- #


class Signature(NamedTuple):
    """One way this runner can fail to run Claude.

    `pattern` is None for exactly one of them: a refused credential leaves no
    phrase in the log, it leaves a RECORD — see `_credential_refused`.
    """

    slug: str
    pattern: re.Pattern | None
    meaning: str
    check: str


# The two checks. An operator holding a hold receipt needs the ONE command
# that settles which half of the boundary broke, and DRE-3416's whole cost was
# being sent to the other one.
_ACTION_PIN_CHECK = (
    "grep -n 'claude-code-action@' .github/workflows/*.yml in bureau-pipeline, "
    "then compare the pinned SHA with anthropics/claude-code-action#1817 "
    "(DRE-3416 pinned v1.0.217; DRE-3417 unpins)"
)
_CREDENTIAL_CHECK = "make cred-doctor in agent-bureau"

# A phrase immediately preceded by a backtick is being QUOTED — that is how
# every card body, brief and comment in this pipeline writes a machine string,
# DRE-3428's own description included. A double or single quote is added for
# the prose phrase, where a quoted mention is prose; `executable_not_found`
# and `authentication_error` are left free of it because their real log line
# is JSON and puts them in double quotes.
_NOT_QUOTED = r"(?<![`\"'])"
_NOT_BACKTICKED = r"(?<!`)"

SIGNATURES: tuple[Signature, ...] = (
    Signature(
        "native-binary-missing",
        re.compile(_NOT_QUOTED + r"Claude Code native binary not found"),
        "the vendor action installed Claude Code but left no launcher on this "
        "runner, so nothing here can start Claude",
        _ACTION_PIN_CHECK,
    ),
    Signature(
        "executable-not-found",
        re.compile(_NOT_BACKTICKED + r"executable_not_found"),
        "the action could not find a Claude executable to start",
        _ACTION_PIN_CHECK,
    ),
    Signature(
        "credential-refused",
        None,
        "Claude started and was refused before its first turn: the credential "
        "this run holds is not accepted",
        _CREDENTIAL_CHECK,
    ),
    Signature(
        # Both on ONE line, the DRE-2488 shape: `authentication_error` alone is
        # a word, and prose carries words. Written as two lookaheads rather
        # than a pair of searches so the whole rule is the row's own pattern —
        # the table is the contract, and a rule kept somewhere else is a second
        # place to read.
        "authentication-error",
        re.compile(
            r"^(?=.*" + _NOT_BACKTICKED + r"authentication_error)"
            r'(?=.*(?:API Error|"type"))'
        ),
        "the API rejected the credential outright",
        _CREDENTIAL_CHECK,
    ),
)


def by_slug(slug: str) -> Signature:
    """The signature that slug names. Raises rather than guessing: a hold
    receipt naming the wrong cause sends an operator to the wrong check, which
    is the failure this module exists to end."""
    for signature in SIGNATURES:
        if signature.slug == slug:
            return signature
    raise KeyError(
        f"{slug!r} is not an environment signature — the table declares "
        + ", ".join(s.slug for s in SIGNATURES)
    )


# A GitHub Actions log line is `job\tstep\t<ISO timestamp> <content>`. The same
# shape `medic_retry._LOG_PREFIX` reads, and for the same reason: without it
# the first character of the line is the job name rather than the message, and
# a plain log with no prefix passes through untouched.
_LOG_PREFIX = re.compile(r"^.*?\d{4}-\d{2}-\d{2}T[\d:.]+Z\s?")

# A line that OPENS with one of these is a quotation of something, not a report
# of it: a diff hunk in an agent log, a quoted comment, a shell trace.
_QUOTATION_LINE = re.compile(r"^\s*[+\->|]")


def _message_lines(log_text: str | None) -> list[str]:
    """Every log line that is the run SAYING something, prefix stripped."""
    out = []
    for raw in (log_text or "").splitlines():
        line = _LOG_PREFIX.sub("", raw, count=1)
        if _QUOTATION_LINE.match(line):
            continue
        out.append(line)
    return out


def execution_record(log_text: str | None) -> dict | None:
    """The failed run's execution record out of its log, numbers as numbers.

    `medic_retry.execution_from_log()` reads the block the agent-result gate
    prints under `execution_result.FAILURE_HEADER`, and every value it returns
    is the STRING the log carried. `check_agent_result._number` deliberately
    refuses a string — a field that should be a number and is not is a field
    nobody should guess at — so the three numeric fields are parsed here,
    where the parse is visible, and everything else is passed through.

    None when the log carries no such block, which is what a pre-agent
    failure, an infra flake and an unreadable log all leave behind.
    """
    record = medic_retry.execution_from_log(log_text)
    if record is None:
        return None
    out = dict(record)
    for field in ("num_turns", "total_cost_usd", "duration_ms"):
        value = out.get(field)
        if not isinstance(value, str):
            continue
        try:
            out[field] = float(value) if "." in value else int(value)
        except ValueError:
            pass  # left as the string it was; the signature then does not fire
    return out


def _credential_refused(log_text: str | None) -> bool:
    """Did Claude start and get refused before its first turn?

    Both halves are the shipped readers': the record is `medic_retry`'s and the
    shape is `check_agent_result`'s. `is_error` is checked explicitly because
    the contract names it — `execution_from_log` sets it by construction, and
    a future reader should not have to know that to see the rule.
    """
    record = execution_record(log_text)
    if not record or record.get("is_error") is not True:
        return False
    return check_agent_result.has_service_outage_signature(record)


def detect(log_text: str | None) -> Signature | None:
    """The first environment signature this log carries, or None.

    Table order is the precedence, so two signatures in one log always give
    the same answer. A log with none of them is not this class and must fall
    straight through to the classes that were here before (DRE-1921's critic
    infra-crash, DRE-2488's upstream 5xx, DRE-2923's Linear rate limit).
    """
    lines = _message_lines(log_text)
    for signature in SIGNATURES:
        if signature.pattern is None:
            if _credential_refused(log_text):
                return signature
            continue
        if any(signature.pattern.search(line) for line in lines):
            return signature
    return None


# --------------------------------------------------------------------------- #
# the evidence note (a report, not an act)                                     #
# --------------------------------------------------------------------------- #

#: The first words of the note `.github/workflows/medic.yml`'s `backoff` job
#: posts on a card today, byte-identical. A contract with epic DRE-3420: its
#: fleet-wide detector (`reviewer_down.witness_from_comments`, DRE-3433) counts
#: every Linear comment STARTING with this phrase as one could-not-run outcome
#: from that repository. Reword it on either side and the alarm goes blind for
#: exactly the crash class this epic names.
CRITIC_UNAVAILABLE_MARKER = "\U0001f50c The code reviewer was temporarily unavailable"

#: What a READER of the card looks for. Deliberately not a `*_TAG` constant:
#: the note is not an act, the registry declares no row for it, and
#: `pipeline_act.problems()` reads every `*_TAG = "…"` in an emitting file as a
#: tag that must have one.
EVIDENCE_MARKER = "reviewer-environment-crash"


def evidence_note(signature: Signature, sha: str, run_url: str = "") -> str:
    """What the medic leaves on the card after ONE crash.

    The phrase first, the environment detail after it — that order is the
    contract above, not a style choice. The full head sha, because the sweep
    that reads these back is asking about one commit.
    """
    run = f" The failed run: {run_url}" if (run_url or "").strip() else ""
    return (
        f"{CRITIC_UNAVAILABLE_MARKER} — {EVIDENCE_MARKER} @{sha}: "
        f"{signature.slug} — {signature.meaning}. The sweep retries this "
        f"review once; a second identical crash holds it. "
        f"Check: {signature.check}."
        "\n\n"
        "What failed is the runner's environment, not the work: the run never "
        f"reached a verdict and nothing in this pull request was rejected.{run}"
    )


def evidence_for_head(bodies, sha: str) -> list:
    """The evidence notes on a card that are bound to THIS head sha.

    The sha binding is the whole point: a crash on an older commit is history,
    and a hold is counted off two crashes on one head. A hold receipt carries
    no `EVIDENCE_MARKER`, so it can never be counted as one of them.
    """
    marker = f"{EVIDENCE_MARKER} @{sha}:"
    return [b for b in (bodies or []) if marker in (b or "")]


#: The slug out of a note this module wrote, anchored on the same marker + sha
#: pair `evidence_for_head` matches, so the two reads can never disagree about
#: which note they are looking at. `evidence_note` writes
#: `<marker> @<sha>: <slug> — <meaning>`; the em-dash is what ends the slug.
_EVIDENCE_SLUG = re.compile(
    re.escape(EVIDENCE_MARKER) + r" @[0-9a-fA-F]+: ([a-z0-9-]+) — "
)


def signature_from_evidence(bodies, sha: str) -> Signature | None:
    """The cause the NEWEST evidence note for this head names, or None.

    The reconcile sweep (DRE-3431) holds a crashed review by composing
    `hold_receipt`, which takes a `Signature`, and all the sweep has is the
    note. So the note is parsed HERE, in the module that writes it, and never
    at the reader: one place knows the note's shape, which is the whole
    argument for this module existing.

    Newest wins, because a head can be crashed twice by two different halves
    of the boundary and the current cause is the one just observed.

    None when no note covers this head, or when the newest one that parses
    names a slug this table does not carry — a hand-edited note, or a note
    left by a newer release of the pipeline. A hold receipt naming the wrong
    cause sends an operator to the wrong check, which is precisely what
    DRE-3416 cost, so an unreadable cause is never guessed at: the caller
    falls back to the report that names none.
    """
    for body in reversed(evidence_for_head(bodies, sha)):
        found = _EVIDENCE_SLUG.search(body or "")
        if not found:
            continue
        try:
            return by_slug(found.group(1))
        except KeyError:
            continue
    return None


# --------------------------------------------------------------------------- #
# the hold receipt                                                             #
# --------------------------------------------------------------------------- #

#: The act's name, as the trailer's `📎 pipeline-act:` field shows it.
HOLD_ACT = "reviewer-environment-hold"

#: The act's idempotency key, in the body. ADOPTED by the registry off this
#: line — the string lives in the code that emits it, never invented by the
#: JSON — and read back by the console, so it may not be reworded without the
#: console moving first (docs/pipeline-acts.md: console-first).
HOLD_TAG = "runner-environment-hold"

#: Every failed post, so a sweep can report what it could not say. A failed
#: post is RECORDED, never raised: the hold is a statement about work that has
#: already stopped, and an exception here would take the caller down over a
#: comment (the `reconcile._post_pr_note` shape).
POST_FAILURES: list = []


def _how_many(count) -> str:
    """`twice` for the second crash on a head, `again after release` for a
    later one — the two things an operator needs told apart, because the
    second means the release rule fired and the runner is still broken."""
    try:
        number = int(count)
    except (TypeError, ValueError):
        number = 2
    return "twice" if number <= 2 else "again after release"


def hold_receipt(signature: Signature, sha: str, count,
                 subject: str = "reviewer") -> str:
    """The hold's body — the detail, without the trailer.

    `post_hold` composes it through `pipeline_act.receipt()`, which appends
    the trailer and nothing else. `subject` is `reviewer` for the critic and
    the workflow's own name for anything else, because all six kinds of
    Claude-running job die of this and a hold that always said "reviewer"
    would misname five of them.
    """
    return (
        f"\U0001f6d1 {HOLD_TAG} @{sha}: {subject} cannot run on this runner — "
        f"{signature.slug}: {signature.meaning} — {_how_many(count)} on "
        f"{sha[:8]}; holding, nothing re-dispatched. Check: {signature.check}."
        "\n\n"
        "What failed is the runner's environment, not the work: no verdict was "
        "written, nothing in this pull request was rejected, and nothing has "
        "been re-dispatched."
        "\n\n"
        "**How this is released:** the first critic verdict posted in this "
        "repository after this hold, or a comment on the held pull request "
        f"whose whole body is `{review_rerun.RERUN_REVIEW_ACT}`, re-dispatches "
        "the held review once. Until one of those happens, nothing else is "
        "coming."
    )


def is_release_act(body: str | None) -> bool:
    """Is this comment the act that releases the hold?

    `review_rerun.is_rerun_act`'s answer, not a second one: that module owns
    the string and its whole-body rule, and the console and the relay mirror
    it. A copy here would be a second thing to reword.
    """
    return review_rerun.is_rerun_act(body)


def _record_failure(what: str) -> None:
    POST_FAILURES.append(what)
    print(f"ERROR: reviewer-environment: {what}", file=sys.stderr)


def post_hold(*, repo: str, pr_number, card: str, body: str) -> None:
    """Post the hold: the pull request first, then the card.

    THE ORDER IS THE CONTRACT. The PR comment is the sha-bound counter the
    reconcile sweep reads to know this hold already stands; the card mirror is
    what the console and the fleet alarm read. A failure on either is recorded
    and the other still happens — a hold nobody can see is a stall nobody can
    act on, and half of it is better than none.

    The body is composed HERE, through the one writer, so both copies carry
    byte-identical bytes and the same trailer.
    """
    # The act named as a LITERAL, the way every other composed site in this
    # repo names it: `check_act_receipts._receipt_act` reads the first
    # argument off the AST and can only read a constant, so a call that passed
    # `HOLD_ACT` would be reported as `<computed>` and this act would count as
    # declared-but-never-composed. `test_both_carry_the_composed_receipt`
    # holds the two spellings together.
    composed = pipeline_act.receipt("reviewer-environment-hold", body)
    if repo and pr_number:
        try:
            posted = subprocess.run(  # nosec B603 B607 — fixed-arg gh, no shell
                ["gh", "pr", "comment", str(pr_number), "--repo", repo,
                 "--body", composed],
                capture_output=True, text=True, check=False,
            )
            if posted.returncode != 0:
                _record_failure(
                    f"the hold on {repo}#{pr_number} failed "
                    f"rc={posted.returncode}: {posted.stderr.strip()[:400]}"
                )
        except OSError as exc:
            _record_failure(f"the hold on {repo}#{pr_number} failed: {exc}")
    if not card:
        return
    try:
        _linear_ops().cmd_comment(card, composed)
    except Exception as exc:  # noqa: BLE001 — a comment never takes a sweep down
        _record_failure(f"the hold mirror on {card} failed: {exc}")


def _linear_ops():
    """The Linear client, imported at the moment of use.

    `medic_classify` imports this module on the medic's critical path, where
    there is no Linear credential and nothing to say to Linear. Keeping the
    client out of that import graph is the same argument
    `review_rerun._cmd_dispatch` makes: the seams that must never fail carry
    no client at all.
    """
    import linear_ops  # noqa: PLC0415 — see the docstring

    return linear_ops


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def report_lines(signature: Signature | None) -> list:
    """The three `key=value` lines a caller reads off a classification.

    One line per key and never a newline inside a value: `medic.yml` appends
    this stdout straight to `$GITHUB_OUTPUT`, which is one key per line, and a
    value carrying a newline writes a second key nobody declared.
    """
    return [
        f"signature={signature.slug if signature else ''}",
        f"check={signature.check if signature else ''}",
        f"meaning={signature.meaning if signature else ''}",
    ]


def _cmd_detect(args) -> int:
    """Name the signature in a log. ALWAYS 0: this is a classifier, and a
    classifier that fails its caller has turned one broken run into two."""
    signature = detect(_read(args.log))
    for line in report_lines(signature):
        print(line)
    if signature is None:
        print("no environment signature in this log", file=sys.stderr)
    return 0


def _cmd_note(args) -> int:
    signature = by_slug(args.signature)
    _linear_ops().cmd_comment(
        args.card, evidence_note(signature, args.sha, args.run_url)
    )
    print(f"reviewer-environment: evidence note on {args.card} "
          f"({signature.slug} @{args.sha[:8]})")
    return 0


def _cmd_hold(args) -> int:
    signature = by_slug(args.signature)
    post_hold(
        repo=args.repo,
        pr_number=args.pr,
        card=args.card,
        body=hold_receipt(signature, args.sha, args.count, args.subject),
    )
    print(f"reviewer-environment: hold posted for {args.card} "
          f"({signature.slug} @{args.sha[:8]})")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    d = sub.add_parser("detect", help="name the environment signature in a log")
    d.add_argument("log")
    d.set_defaults(fn=_cmd_detect)

    n = sub.add_parser("note", help="the medic's evidence note, on the card")
    n.add_argument("--card", required=True)
    n.add_argument("--sha", required=True)
    n.add_argument("--signature", required=True)
    n.add_argument("--run-url", default="")
    n.set_defaults(fn=_cmd_note)

    h = sub.add_parser("hold", help="the hold receipt, on the PR and the card")
    h.add_argument("--card", required=True)
    h.add_argument("--sha", required=True)
    h.add_argument("--signature", required=True)
    h.add_argument("--count", default=2)
    h.add_argument("--subject", default="reviewer")
    h.add_argument("--repo", default="")
    h.add_argument("--pr", default=None)
    h.set_defaults(fn=_cmd_hold)

    args = parser.parse_args(argv)
    try:
        return args.fn(args)
    except KeyError as exc:
        print(f"reviewer-environment: {exc.args[0]}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
