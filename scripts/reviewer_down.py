#!/usr/bin/env python3
"""Is the reviewer down FLEET-WIDE, and what should the ONE card say (DRE-3433).

Origin (live, 2026-09-08 15:19–16:27 PT, DRE-3416). The floating
`anthropics/claude-code-action@v1` tag moved and every critic run in the fleet
died in about thirteen seconds with `ReferenceError: Claude Code native binary
not found at /home/runner/.local/bin/claude`. Per-run detection worked
perfectly — each pull request got the neutral could-not-run receipt, which is
`medic_classify.CRITIC_NEUTRAL_MARKER` — and NOTHING aggregated it. Seven runs
across two repositories failed the same way inside half an hour, and no single
surface anywhere said "the reviewer is down".

This module is the decision behind that one card and nothing else: pure
functions over the outcomes a sweep can already see, no I/O of any kind — no
network, no shell, no Linear — and a CLI for humans: the shape
`scripts/stale_merge_ref.py` (DRE-3138) carries. The reconcile wiring is a
sibling card.

## The threshold is DATA

`window_s`, `consecutive` and `repos` live on the act row in
`config/pipeline-acts.json`, read through `threshold_from_registry()`. An
operator who wants the alarm to fire sooner edits a number in a file they can
read, not a literal buried in a comparison. It is also the only registry read
here, and `pipeline_act.load` caches it.

## The witness reads BOTH shapes of medic note

The sweep runs once per repository, so it reads THIS repository's pull requests
directly. A second repository's crashes are seen only through the medic's note
on the Linear card — which every sweep already reads — and after DRE-3430 that
note has two shapes:

  * `MEDIC_BACKOFF_MARKER`, the generic infra-crash note the `backoff` job
    posts. DRE-3430 narrows it to a NON-environment infra crash (a rate limit)
    and keeps the literal exactly once, so a fleet-wide 429 is still witnessed
    through it;
  * `ENVIRONMENT_EVIDENCE_MARKER`, which opens the evidence note the same job
    posts for `class == 'environment_crash'` — the native-binary-not-found
    class that IS the 2026-09-08 incident. It is IMPORTED from
    `reviewer_environment` (DRE-3428 owns that string), never restated, and the
    test that pins it builds a real note with `evidence_note()` rather than
    reading the workflow YAML: the writer is the contract, the YAML is one
    caller of it.

A detector that read only the generic literal would have been blind to exactly
the outage this epic exists to catch.

NOT a witness, and both absences are load-bearing. The hold receipt
(`reviewer_environment.HOLD_TAG`) records a SECOND crash whose evidence note is
already counted, and for a non-review workflow it is not about the reviewer at
all. The sweep's own `reviewer-down` note follows receipts the local read has
already seen, so counting it would count one outage twice.

## The decision, in order

`decide()` answers with exactly one of `close`, `append`, `file`, `nothing`.
Close comes first because a reviewer that is back makes every other answer
wrong; append before file because a second card for one outage is the noise
this module exists to remove; and `file` only once the threshold is met.

CLI:

    python3 scripts/reviewer_down.py replay <fixture.json> [--now <ISO>]

prints the Decision as JSON. The proof card runs it against the recorded
incident in `tests/fixtures/reviewer-down-2026-09-08.json`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import medic_classify  # noqa: E402
import merge_gate  # noqa: E402
import pipeline_act  # noqa: E402
import reviewer_environment  # noqa: E402

#: The act's name, as the trailer's `📎 pipeline-act:` field shows it.
ACT = "reviewer-outage-fleet-wide"

#: The act's idempotency key, in the body. Read off THIS line by the registry
#: (`pipeline_act._TAG_CONSTANT`), so it may not be computed.
OUTAGE_TAG = "fleet-reviewer-outage"

#: The card's title opens with this, so the sweep can find its own open card.
TITLE_PREFIX = "Reviewer down since "

#: Every run the card has counted is one line, and the line is the record: the
#: sweep re-reads them next time instead of re-deriving what it already knew.
LEDGER_PREFIX = "run repo="

# --------------------------------------------------------------------------- #
# the witness markers                                                          #
# --------------------------------------------------------------------------- #

#: The GENERIC infra-crash note `.github/workflows/medic.yml`'s `backoff` job
#: posts. Written as a literal and pinned by a test that reads medic.yml,
#: deliberately: this constant's contract is with the WORKFLOW, which keeps the
#: phrase exactly once before and after DRE-3430, and borrowing
#: `reviewer_environment`'s copy of the same phrase would bind it to the
#: environment note instead — the one note DRE-3430 posts INSTEAD of this one.
MEDIC_BACKOFF_MARKER = "\U0001f50c The code reviewer was temporarily unavailable"

#: What opens the first line of the EVIDENCE note DRE-3430's `backoff` posts for
#: `class == 'environment_crash'`. Imported, never restated: DRE-3428 owns the
#: string, and a second copy is a second thing to reword.
ENVIRONMENT_EVIDENCE_MARKER = reviewer_environment.EVIDENCE_MARKER + " @"

#: One could-not-run outcome per comment whose FIRST LINE carries either.
WITNESS_MARKERS = (MEDIC_BACKOFF_MARKER, ENVIRONMENT_EVIDENCE_MARKER)

# --------------------------------------------------------------------------- #
# the vocabulary                                                               #
# --------------------------------------------------------------------------- #

COULD_NOT_RUN = "could-not-run"
VERDICT = "verdict"

FILE = "file"
APPEND = "append"
CLOSE = "close"
NOTHING = "nothing"

ACTIONS = (FILE, APPEND, CLOSE, NOTHING)

#: The three usual suspects, IN DIAGNOSIS ORDER, each with the one command that
#: settles it. The order is the whole value: DRE-3416 cost what it cost because
#: the operator was sent to the credential first, which was the one thing that
#: was not wrong.
SUSPECTS = (
    (
        "the vendor action's release moved under us (a floating tag, or a "
        "release pulled)",
        "gh api repos/anthropics/claude-code-action/git/ref/tags/v1",
    ),
    (
        "the fleet's Claude credential is refused",
        "make cred-doctor --account <account>",
    ),
    (
        "Anthropic is having an incident",
        "https://status.anthropic.com",
    ),
)

# A GitHub Actions log line is `job\tstep\t<ISO timestamp> <content>`. Borrowed
# rather than re-derived: `reviewer_environment` already reads this shape (and
# `medic_retry` before it), and two readers of one prefix are two answers about
# one line waiting to disagree.
_LOG_PREFIX = reviewer_environment._LOG_PREFIX

# What makes a line the FAILURE in a `run view --log-failed` tail, in the order
# a reader would want them: the class DRE-3416 was, then the phrase, then the
# two generic shapes.
_ERROR_PHRASES = ("ReferenceError", "native binary not found", "Error:", "error:")

_ACTION_REF = re.compile(r"(anthropics/claude-code-action@\S+)([ \t]*#[^\n]*)?")

_LEDGER_LINE = re.compile(
    re.escape(LEDGER_PREFIX)
    + r"(?P<repo>\S+) pr=(?P<pr>\S+) at=(?P<at>\S+) src=(?P<src>\S+)"
)


# --------------------------------------------------------------------------- #
# the shapes                                                                   #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Outcome:
    """One thing the reviewer did, or failed to do, on one pull request.

    `src` is the IDEMPOTENCY KEY — a comment url, or `linear:<card>:<createdAt>`
    for a witness. Everything this module de-duplicates, it de-duplicates on
    `src`, because the same crash is seen twice the moment two sweeps overlap.
    """

    repo: str
    at: str
    kind: str
    src: str
    pr: int | None = None
    detail: str = ""


@dataclass(frozen=True)
class Threshold:
    """When a scatter of crashes becomes an outage. Data, never a literal."""

    window_s: int
    consecutive: int
    repos: int


@dataclass(frozen=True)
class OpenCard:
    """The outage card already open, if there is one.

    `text` is the description plus every comment body, because a ledger line
    may have been appended as either.
    """

    identifier: str
    filed_at: str
    text: str


@dataclass(frozen=True)
class FirstRun:
    """The first crashed run, as the card body reports it."""

    repo: str = ""
    pr: int | None = None
    at: str = ""
    run_url: str = ""
    log_line: str = ""
    action_ref: str = ""


@dataclass(frozen=True)
class Decision:
    """One action, and everything the caller needs to take it.

    `title` and `body` are what the card should say; `lines` are the ledger
    lines to write; `resolve_note` is set on a close and nowhere else.
    """

    action: str
    title: str = ""
    body: str = ""
    lines: list = field(default_factory=list)
    runs: int = 0
    repos: int = 0
    first_at: str = ""
    resolve_note: str = ""


# --------------------------------------------------------------------------- #
# time                                                                         #
# --------------------------------------------------------------------------- #


def _dt(value) -> datetime | None:
    """An ISO-8601 instant, or None when it cannot be read.

    None rather than a raise: an unreadable timestamp on one comment must not
    take down a sweep reading fifty of them, and an outcome we cannot place in
    time is one we decline to count.
    """
    text = (value or "").strip()
    if not text:
        return None
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def pt(iso: str) -> str:
    """`15:19 PT` — every time a person reads here is Pacific.

    The `dead_run.pacific()` discipline, in this card's own format: on a runner
    with no tz database say UTC out loud rather than print a wrong PT.
    """
    when = _dt(iso)
    if when is None:
        return "an unknown time"
    try:
        from zoneinfo import ZoneInfo  # noqa: PLC0415 — see the docstring

        return when.astimezone(ZoneInfo("America/Los_Angeles")).strftime("%H:%M PT")
    except Exception:  # noqa: BLE001 — no tz database: UTC, never a wrong PT
        return when.astimezone(timezone.utc).strftime("%H:%M UTC")


def _sort_key(outcome: Outcome):
    when = _dt(outcome.at)
    return (when is None, when or datetime.min.replace(tzinfo=timezone.utc),
            outcome.src)


# --------------------------------------------------------------------------- #
# reading the outcomes                                                         #
# --------------------------------------------------------------------------- #


def outcomes_from_pr(pr: dict, repo: str) -> list:
    """Every outcome on one pull request, off the `--json number,comments` shape.

    THE FIRST LINE, never the body. `merge_gate.opens_with_marker` makes the
    same argument about the same comments: prose that quotes a marker satisfies
    a substring search, and a card body quoting the neutral receipt would
    otherwise count as a crashed run.

    The pull-request side is untouched by epic DRE-3421 — the neutral receipt
    is posted by the review workflow, which none of its cards edit.
    """
    number = pr.get("number") if isinstance(pr, dict) else None
    out = []
    for comment in (pr or {}).get("comments") or []:
        line = merge_gate.first_line(comment.get("body") or "")
        at = comment.get("createdAt") or ""
        src = comment.get("url") or ""
        if medic_classify.CRITIC_NEUTRAL_MARKER in line:
            out.append(Outcome(repo, at, COULD_NOT_RUN, src, number, line))
        elif merge_gate.verdict_token(line, merge_gate.CRITIC_MARKER):
            out.append(Outcome(repo, at, VERDICT, src, number, line))
    return out


def witness_from_comments(repo: str, identifier: str, comments: list) -> list:
    """A second repository's crashes, seen through the card every sweep reads.

    One outcome per comment whose FIRST LINE carries either witness marker —
    per COMMENT, not per marker, because the environment note opens with both
    and one crashed run is one outcome.
    """
    out = []
    for comment in comments or []:
        line = merge_gate.first_line(comment.get("body") or "")
        if not any(marker in line for marker in WITNESS_MARKERS):
            continue
        at = comment.get("createdAt") or ""
        out.append(Outcome(repo, at, COULD_NOT_RUN,
                           f"linear:{identifier}:{at}", None, line))
    return out


# --------------------------------------------------------------------------- #
# the ledger                                                                   #
# --------------------------------------------------------------------------- #


def ledger_line(outcome: Outcome) -> str:
    """One counted run, as the card records it."""
    number = f"#{outcome.pr}" if outcome.pr is not None else "-"
    return (f"{LEDGER_PREFIX}{outcome.repo} pr={number} at={outcome.at} "
            f"src={outcome.src}")


def ledger_from_text(text: str) -> list:
    """Every ledger line anywhere in a description or a comment.

    Anywhere on purpose: a line may have been written into the description at
    filing and appended as a comment later, and both are the same record.
    """
    out = []
    for match in _LEDGER_LINE.finditer(text or ""):
        raw = match.group("pr")
        number = int(raw[1:]) if raw.startswith("#") and raw[1:].isdigit() else None
        out.append(Outcome(match.group("repo"), match.group("at"), COULD_NOT_RUN,
                           match.group("src"), number, match.group(0)))
    return out


def threshold_from_registry(doc: dict | None = None) -> Threshold:
    """The three numbers, off the act row. The only registry read in this file."""
    declared = (pipeline_act.record(ACT, doc) or {}).get("threshold") or {}
    try:
        return Threshold(
            window_s=int(declared["window_s"]),
            consecutive=int(declared["consecutive"]),
            repos=int(declared["repos"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise pipeline_act.ActError(
            f"the {ACT} row declares no readable threshold ({exc}) — the "
            "numbers are data on the row so an operator can change them, and "
            "defaulting them here would put a number nobody chose behind an "
            "alarm that files a card"
        ) from exc


# --------------------------------------------------------------------------- #
# the decision                                                                 #
# --------------------------------------------------------------------------- #


def _window(outcomes, now, window_s: int) -> list:
    """The outcomes still inside the window, de-duplicated on `src`, oldest
    first. An outcome exactly on the edge is IN: the window is `at >= now -
    window_s`, and an alarm that dropped its own first run would mis-title
    every card it filed."""
    edge = _dt(now)
    cutoff = edge - timedelta(seconds=window_s) if edge is not None else None
    kept, seen = [], set()
    for outcome in outcomes:
        when = _dt(outcome.at)
        if cutoff is not None and (when is None or when < cutoff):
            continue
        if outcome.src in seen:
            continue
        seen.add(outcome.src)
        kept.append(outcome)
    kept.sort(key=_sort_key)
    return kept


def _dedupe(outcomes) -> list:
    seen, kept = set(), []
    for outcome in outcomes:
        if outcome.src in seen:
            continue
        seen.add(outcome.src)
        kept.append(outcome)
    kept.sort(key=_sort_key)
    return kept


def _counts(outcomes) -> tuple:
    """(runs, repos, first_at) over a de-duplicated set of crashed runs."""
    unique = _dedupe(outcomes)
    if not unique:
        return 0, 0, ""
    return len(unique), len({o.repo for o in unique}), unique[0].at


def _first_verdict_after(local, filed_at: str) -> Outcome | None:
    """The earliest LOCAL verdict posted after the card was filed.

    Local only: a verdict is proof the reviewer ran HERE, and a witness outcome
    is a note about somewhere else. Earliest, because the card should record
    the moment the reviewer came back, not the most recent time it was seen up.
    """
    filed = _dt(filed_at)
    if filed is None:
        return None
    candidates = [o for o in local
                  if o.kind == VERDICT and (_dt(o.at) or filed) > filed]
    return min(candidates, key=_sort_key) if candidates else None


def _threshold_met(window, threshold: Threshold) -> bool:
    """Either rule fires on its own, and they catch different outages.

    The SPREAD rule (two repositories) catches a fleet-wide break the moment it
    is visibly fleet-wide, which is the 2026-09-08 shape: one crash each in two
    places at once is not a flake. The RUN rule (three in a row since the last
    verdict) catches a break confined to one repository, where the only
    evidence is that nothing has succeeded since.
    """
    crashes = [o for o in window if o.kind == COULD_NOT_RUN]
    if len({o.repo for o in crashes}) >= threshold.repos:
        return True
    last_verdict = None
    for outcome in window:
        if outcome.kind == VERDICT:
            last_verdict = outcome
    if last_verdict is None:
        trailing = crashes
    else:
        edge = _dt(last_verdict.at)
        trailing = [o for o in crashes
                    if edge is None or (_dt(o.at) or edge) > edge]
    return len(trailing) >= threshold.consecutive


def decide(local, witness, open_card, now, threshold, first_run=None) -> Decision:
    """Given the outcomes a sweep can see, what should the ONE card say?

    Pure: every argument is already-read data, and nothing here reaches the
    network, Linear or a shell.
    """
    text = open_card.text if open_card else ""
    window = _window(list(local) + list(witness) + ledger_from_text(text),
                     now, threshold.window_s)
    crashes = [o for o in window if o.kind == COULD_NOT_RUN]
    new = [o for o in crashes if o.src not in text]
    new_lines = [ledger_line(o) for o in new]

    if open_card is not None:
        back = _first_verdict_after(local, open_card.filed_at)
        if back is not None:
            # First, and before anything else: a reviewer that is back makes
            # every other answer on this card wrong.
            return Decision(
                CLOSE,
                first_at=back.at,
                resolve_note=(
                    f"reviewer back at {pt(back.at)} — first successful "
                    f"verdict after this card was filed ({back.src})"
                ),
            )
        if new_lines:
            runs, repos, first_at = _counts(ledger_from_text(text) + new)
            return Decision(APPEND, title=card_title(first_at, runs, repos),
                            lines=new_lines, runs=runs, repos=repos,
                            first_at=first_at)
        return Decision(NOTHING)

    if _threshold_met(window, threshold):
        runs, repos, first_at = _counts(crashes)
        return Decision(FILE, title=card_title(first_at, runs, repos),
                        body=card_body(first_run, new_lines), lines=new_lines,
                        runs=runs, repos=repos, first_at=first_at)
    return Decision(NOTHING)


# --------------------------------------------------------------------------- #
# what the card says                                                           #
# --------------------------------------------------------------------------- #


def card_title(first_at: str, runs, repos) -> str:
    """`Reviewer down since 15:19 PT — 7 runs, 2 repos`.

    Always plural and always integers: the title is rewritten on every append,
    and a title that grammar-agreed with its counts would change shape as well
    as numbers — which is one more thing for a reader matching cards to get
    wrong.
    """
    return f"{TITLE_PREFIX}{pt(first_at)} — {int(runs)} runs, {int(repos)} repos"


def card_body(first_run, lines) -> str:
    """What happened, the evidence, the three checks, and the ledger."""
    run = first_run or FirstRun()
    where = f"{run.repo}#{run.pr}" if run.repo and run.pr else (run.repo or "the fleet")
    error = (run.log_line or "").strip() or "log tail unreadable"
    ref = (run.action_ref or "").strip() or "unknown — no reference read"
    when = f" at {pt(run.at)}" if run.at else ""

    body = [
        f"The code reviewer stopped being able to run{when}, and it is not one "
        f"pull request's problem: the runs below crashed the same way across "
        f"every repository listed. Each of them got its own could-not-run "
        f"receipt and nothing was rejected — no verdict was written, so no "
        f"work here has been judged.",
        "",
        f"The first crashed run was on {where}"
        + (f" ({run.run_url})" if run.run_url else "")
        + ", and this is the line its log ended on:",
        "",
        "```",
        error,
        "```",
        "",
        f"The action reference those runs used: `{ref}`",
        "",
        "**The three usual suspects, in the order worth checking them.** The "
        "order is the point: the 2026-09-08 outage (DRE-3416) cost what it "
        "cost because the first person to look was sent to the credential, "
        "which was the one thing that was not wrong.",
        "",
    ]
    for index, (suspect, command) in enumerate(SUSPECTS, start=1):
        body.append(f"{index}. {suspect} —")
        body.append(f"   `{command}`")
    body.extend([
        "",
        "**The runs this card has counted.** One line per run, and the line is "
        "the record — the sweep re-reads them rather than re-deriving what it "
        "already knew:",
        "",
    ])
    body.extend(f"- {line}" for line in (lines or ["(none recorded yet)"]))
    body.extend([
        "",
        "The sweep closes this card by itself on the first successful verdict "
        "posted after it was filed. Nothing else needs to happen here.",
    ])
    return "\n".join(body)


def outage_receipt(decision) -> str:
    """The detail text of the filing receipt.

    The wiring wraps it in `pipeline_act.receipt(ACT, …)`, which appends the
    trailer and nothing else — so the tag opens the body here, where it is the
    idempotency key a later sweep reads back with `tag in body`.

    Deliberately not blocker-shaped and carrying no verdict marker: a bot
    comment opening with 🛑 is read as a prior fix-loop blocker by
    `fix_context.py`, and verdict-shaped text is an approval credential only
    the critic may write (`standards/untrusted-content.md`).
    """
    first = pt(decision.first_at) if decision.first_at else "an unknown time"
    return (
        f"\U0001f6a8 {OUTAGE_TAG} @{decision.first_at}: the code reviewer is "
        f"down across the fleet — {decision.runs} runs in {decision.repos} "
        f"repos could not run since {first}, and none of them reached a "
        f"verdict. {decision.title}."
        "\n\n"
        "What this is NOT: nothing in any of those pull requests was rejected "
        "and no work has been judged. The pipeline has stopped re-dispatching "
        "reviews into a reviewer that cannot start, and handed this to a "
        "person."
        "\n\n"
        "The card names the first failed run, the exact error line, the action "
        "reference those runs used and the three checks that tell the causes "
        "apart. The sweep closes it on the first successful verdict posted "
        "after it was filed."
    )


# --------------------------------------------------------------------------- #
# the two log readers                                                          #
# --------------------------------------------------------------------------- #


def error_line(log_text: str) -> str:
    """The line a failed run ended on, out of a `--log-failed` tail.

    The Actions prefix is stripped first, so the card shows the message rather
    than the job name. A tail with no recognisable error phrase falls back to
    its last non-empty line — a wrong-looking line a person can read beats a
    confident blank — and an unreadable tail says so.
    """
    lines = []
    for raw in (log_text or "").splitlines():
        stripped = _LOG_PREFIX.sub("", raw, count=1).strip()
        if stripped:
            lines.append(stripped)
    for line in lines:
        if any(phrase in line for phrase in _ERROR_PHRASES):
            return line
    return lines[-1] if lines else "log tail unreadable"


def action_ref(workflow_text: str) -> str:
    """The first `anthropics/claude-code-action@<ref>`, with its trailing
    comment when there is one — the comment is usually the human-readable
    version a pin stands for, and an operator comparing releases wants it."""
    match = _ACTION_REF.search(workflow_text or "")
    if not match:
        return ""
    return (match.group(1) + (match.group(2) or "")).rstrip()


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #


def _outcome(raw: dict) -> Outcome:
    return Outcome(
        repo=raw.get("repo") or "",
        at=raw.get("at") or "",
        kind=raw.get("kind") or COULD_NOT_RUN,
        src=raw.get("src") or "",
        pr=raw.get("pr"),
        detail=raw.get("detail") or "",
    )


def replay(doc: dict, now: str | None = None) -> Decision:
    """The decision a recorded incident produces. The proof card's entry point."""
    declared = doc.get("threshold")
    threshold = (Threshold(**declared) if isinstance(declared, dict)
                 else threshold_from_registry())
    card = doc.get("open_card")
    run = doc.get("first_run")
    return decide(
        [_outcome(o) for o in doc.get("local") or []],
        [_outcome(o) for o in doc.get("witness") or []],
        OpenCard(**card) if isinstance(card, dict) else None,
        now or doc.get("now") or "",
        threshold,
        FirstRun(**run) if isinstance(run, dict) else None,
    )


def _cmd_replay(args) -> int:
    with open(args.fixture, encoding="utf-8") as handle:
        doc = json.load(handle)
    print(json.dumps(asdict(replay(doc, args.now)), indent=2, ensure_ascii=False))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    r = sub.add_parser("replay", help="decide against a recorded incident")
    r.add_argument("fixture")
    r.add_argument("--now", default=None,
                   help="the instant to decide AT (default: the fixture's own)")
    r.set_defaults(fn=_cmd_replay)

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
