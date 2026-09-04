"""Alarm when the release channel goes quiet (DRE-2552, Wave 1 Step 3).

Nothing in the estate raised this sentence:

    The release channel has not moved in 29 days while the engine moved 174
    commits.

That is the only reason July went unnoticed for a month. The gate did not fail
and the tag did not break — **the mechanism stopped being invoked, and no
signal existed for "stopped"**. Every other backstop here watches for something
going wrong; this one watches for something ceasing to happen.

It is the second half of DRE-2551. `promote-channel.yml` moves `stable` on
every harness-proven commit and refuses when `CHANNEL_HOLD` is set — and a hold
that nobody is told about is indistinguishable from an abandoned channel. So
this module reports both quiet channels, and it reports them differently:

  * stale because nothing promoted it  → something is broken;
  * stale because it is HELD           → say so, say by whom, and keep saying
    it.

This module is the decision only — no network, no git, the `promote_channel.py`
shape. The workflow gathers GitHub's records and acts on the verdict. The
ordering of the checks below is deliberately the same as its sibling's: the
hold is read FIRST so a deliberately paused channel reads as paused, never as
broken.

WHERE THE THRESHOLD COMES FROM (the card asked for a decision, not a default)
----------------------------------------------------------------------------
Measured on this repo, 2026-08-20, over `git log origin/main` from the first
commit (2026-06-11) to that date — 522 commits across 70 days:

    commits/day             7.4 mean (6.3 over the trailing 30d, 11.8 over 14d)
    gap between commits     p50 0.06h · p90 3.3h · p95 15.2h · p99 62.4h
    longest quiet stretch   257.9h = 10.7 days
    commits in a 72h window median 32 · 10th percentile 8

Reproduce with:

    git log --format=%cI origin/main   # then diff consecutive timestamps

From that:

  * **72 hours** — 99.0% of all observed gaps between commits are shorter than
    this, so ordinary quiet does not reach it. A rounder 24h would sit at the
    96.9% mark and fire on weekends; a week would let a third of a July go by.
  * **8 commits** — the 10th-percentile count of commits in a 72h window.
    Below that the trunk was unusually quiet and one slow harness run explains
    the lag; at or above it, promotion has stopped rather than lagged.
  * **14 days, one commit** — the backstop, for the shape the pair above would
    miss: a near-idle trunk whose single commit never promotes. Longer than any
    quiet stretch main has ever had (10.7 days), so it cannot be ordinary.
  * **24 hours** for a hold — a hold is true by construction, so this is not a
    false-positive risk, only a noise one. One day covers 96.9% of commit gaps:
    a switch flipped and cleared inside a working day stays private; one that
    outlives a day has, at this cadence, blocked ~7 commits and is a habit
    forming.

Both stale thresholds must be met together, and either way the channel must
have something to promote — `commits_ahead == 0` is silent by construction. A
noisy alarm gets muted, and a muted alarm on the thing protecting the fleet is
how we get back to July.

NAMING THE MERGE TRAIN (DRE-3070)
---------------------------------
The alarm above fires on *not moving*, which is what a merge train produces —
but it fired saying the cause was somewhere else: *"the Promote Channel run log
says which"*. On 2026-09-03 the run log said nothing, because a harness run
displaced from the concurrency group's single pending slot triggered no
promote-channel run at all, so there was no receipt to read.

Both halves of that are fixed together. `promote_channel.py` now names its own
refusal, and this module reads one more record — how many harness runs on main
concluded `cancelled` since the channel head — and reports **MERGE TRAIN**
rather than unknown when there are enough of them to mean it. One cancelled run
is the queue-behind rule working as designed; `MERGE_TRAIN_CANCELLATIONS` is
where it stops being that.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from promote_channel import CHANNEL  # noqa: E402  one name for one ref

#: Hours before an unmoved channel with real drift behind it is an alarm.
STALE_AFTER_HOURS = 72

#: …and how much drift. Both conditions, or ordinary quiet cries wolf.
STALE_AFTER_COMMITS = 8

#: The backstop for a near-idle trunk: longer than any quiet stretch observed.
QUIET_BACKSTOP_HOURS = 14 * 24.0

#: A hold this old has stopped being a switch and started being a habit.
HOLD_ALARM_AFTER_HOURS = 24.0

#: The cron cadence in channel-watch.yml. The wiring test pins the two together.
INTERVAL_HOURS = 24.0

#: A gap larger than two scheduled ticks means the watcher itself skipped.
MISSED_TICK_HOURS = 2 * INTERVAL_HOURS

#: Cancelled harness runs on `main`, since the channel head, at or above which
#: a stale channel is diagnosed as a MERGE TRAIN rather than left as unknown
#: (DRE-3070). Not a threshold to tune — the smallest number that means
#: anything. ONE cancellation is the queue-behind rule WORKING: GitHub keeps a
#: single pending run per concurrency group, so one intermediate head being
#: dropped is the designed trade (`docs/self-hosting.md`). TWO is the first
#: count that cannot be that, and by then the channel is being outrun rather
#: than lagging. Measured against the incident: 2026-09-03 18:30–20:53 PT
#: produced 13 cancelled runs on main against 2 completed.
MERGE_TRAIN_CANCELLATIONS = 2

MOVING = "moving"
STALE = "stale"
HELD = "held"
UNKNOWN = "unknown"
WATCHER_GAP = "watcher-gap"

#: Exact, stable titles — `linear_ops.py find-open` matches on equality, so a
#: title that moved with the numbers would mint a fresh card every day and the
#: alarm would become the inbox we are escaping. No backticks or `$`: the
#: workflow carries these through a shell.
STALE_TITLE = "Release channel stale — the stable tag has stopped advancing"
HELD_TITLE = "Release channel held — promotion is paused and staying paused"
UNKNOWN_TITLE = "Release channel watcher cannot read the channel"
WATCHER_TITLE = "Release channel watcher missed its own scheduled runs"

#: Printed inside every alarm so a later reader can tell a considered threshold
#: from a guess without going to find this file.
DERIVATION = (
    f"Threshold: the alarm fires when main is {STALE_AFTER_COMMITS}+ commits "
    f"ahead of the channel AND the channel head is older than "
    f"{STALE_AFTER_HOURS}h — or when anything at all has been unpromoted for "
    f"{int(QUIET_BACKSTOP_HOURS / 24)} days. Derived from this repo's measured "
    f"cadence (522 commits over 70 days to 2026-08-20): 99.0% of gaps between "
    f"commits are under {STALE_AFTER_HOURS}h, a typical 72h window carries 32 "
    f"commits and a tenth-percentile one carries {STALE_AFTER_COMMITS}, and "
    f"the longest quiet stretch main has ever had is 10.7 days."
)

#: `who=Ada` / `by=Ada Lovelace` in the hold text. Reading a repository
#: variable's own updated_at needs an admin-scoped token the workflow does not
#: have, so the operator writes who and when into the reason itself.
_WHO = re.compile(r"\b(?:who|by)=([^\s]+(?:\s+[A-Z][^\s=]*)*)")
_SINCE = re.compile(r"\b(?:since|at)=([^\s]+)")


@dataclass(frozen=True)
class Verdict:
    """What the channel is doing, in words a non-technical reader can act on."""

    state: str
    alarm: bool
    title: str
    headline: str
    detail: str


def holder(hold: str | None) -> str | None:
    """Who set the hold, or None — never a guess."""
    m = _WHO.search(hold or "")
    return m.group(1).strip() if m else None


def hold_started(hold: str | None) -> str | None:
    """When the hold started, per the reason text, or None."""
    m = _SINCE.search(hold or "")
    return m.group(1).strip() if m else None


def hours_since(when: str | None, *, now: str | None = None) -> float | None:
    """Elapsed hours since an ISO timestamp, or None if it cannot be read.

    Unreadable and absent are the same answer — we do not know — and the
    caller renders that as unknown rather than as a number that looks fine.
    """
    if not when:
        return None
    try:
        then = dt.datetime.fromisoformat(when.replace("Z", "+00:00"))
        end = (
            dt.datetime.fromisoformat(now.replace("Z", "+00:00"))
            if now
            else dt.datetime.now(dt.timezone.utc)
        )
    except ValueError:
        return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=dt.timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=dt.timezone.utc)
    return (end - then).total_seconds() / 3600.0


def cron_interval_hours(cron: str) -> float | None:
    """The interval of a daily/hourly 5-field cron, or None if it is neither.

    Only rich enough to keep the schedule and MISSED_TICK_HOURS honest with
    each other; it is not a cron engine.
    """
    fields = (cron or "").split()
    if len(fields) != 5:
        return None
    minute, hour, dom, month, dow = fields
    if dom != "*" or month != "*" or dow != "*":
        return None
    if hour == "*":
        return 1.0
    if hour.isdigit() and minute.isdigit():
        return 24.0
    return None


def _days(hours: float) -> str:
    """'29 days' / '30 hours' — the unit a human would have used."""
    if hours >= 48:
        value, unit = hours / 24, "day"
    else:
        value, unit = hours, "hour"
    return f"{value:.0f} {unit}" + ("" if f"{value:.0f}" == "1" else "s")


def _drift_line(commits_ahead: int | None, channel_age_hours: float | None) -> str:
    if commits_ahead is None or channel_age_hours is None:
        return (
            "Drift: could not read it — "
            f"{'the commits ahead' if commits_ahead is None else 'the channel head date'} "
            "did not come back from GitHub."
        )
    return (
        f"Drift: {commits_ahead} commits on main are not on the channel, and "
        f"the channel head is {_days(channel_age_hours)} old."
    )


def _hold_lines(hold: str, hold_age_hours: float | None, hold_since: str | None) -> list[str]:
    who = holder(hold)
    since = hold_since or hold_started(hold)
    lines = [
        f"Who: {who}." if who
        else "Who: the hold does not say who set it. Whoever did should rewrite "
             "the CHANNEL_HOLD variable as `who=<name> since=<date> <reason>`.",
    ]
    if since:
        lines.append(f"Since: {since}.")
    elif hold_age_hours is not None:
        lines.append(f"Since: {_days(hold_age_hours)} ago.")
    else:
        lines.append(
            "Since: the hold does not say when it started, and GitHub will not "
            "tell us — so we cannot say how long the fleet has been frozen."
        )
    lines.append(f"Reason on record: {hold.strip()}")
    return lines


def _is_merge_train(cancelled_harness_runs: int | None) -> bool:
    """Enough cancelled proving runs to name the cause. Never on a blip.

    `None` is "we could not read the run records" and stays unknown — the
    console-honesty rule that governs the rest of this module. A guess dressed
    as a diagnosis is worse than the unknown it replaces.
    """
    return (
        cancelled_harness_runs is not None
        and cancelled_harness_runs >= MERGE_TRAIN_CANCELLATIONS
    )


def _merge_train_headline(cancelled_harness_runs: int | None) -> str:
    if not _is_merge_train(cancelled_harness_runs):
        return ""
    return (
        f" A merge train is starving it: {cancelled_harness_runs} harness runs "
        f"on main were cancelled before they could prove anything."
    )


def _cause_line(cancelled_harness_runs: int | None) -> str:
    """Why the channel stopped — named when the records say, unknown when not.

    DRE-3070: this used to be one sentence pointing at a run log that, in the
    merge-train case, held nothing to find. Every promote-channel run now
    leaves a receipt naming its own reason (`promote_channel.OUTCOME_*`), and
    the harness's cancelled runs on main are countable, so the commonest cause
    of a quiet channel is reported instead of deferred.
    """
    if _is_merge_train(cancelled_harness_runs):
        return (
            f"Cause: a MERGE TRAIN. {cancelled_harness_runs} harness runs on "
            f"main concluded `cancelled` since the channel head — each one "
            f"displaced from the harness's single pending slot by the merge "
            f"that followed it, so it never started and never proved its "
            f"commit. One skipped head is the queue-behind rule working; this "
            f"many means merges are arriving faster than the harness can "
            f"prove them. The channel is not broken and nothing needs "
            f"reverting — it advances again as soon as the trunk goes quiet "
            f"enough for one run to finish. If that is not acceptable, the "
            f"lever is the harness's duration or the merge rate, never "
            f"cancelling the run in progress (docs/self-hosting.md, 'Queue "
            f"behind, never cancel')."
        )
    return (
        "That means promotion has stopped happening, or every run since "
        "has refused. The Promote Channel run log says which — every run "
        "leaves a receipt naming one of `harness-passed-promoting`, "
        "`harness-cancelled-by-newer-push`, `harness-failed`, "
        "`channel-held`, `no-harness-stamp` or `not-ahead-of-channel` — and "
        "the distinction is the whole point of this alarm: the July failure "
        "was a mechanism that stopped being invoked, not one that failed."
    )


def evaluate(
    *,
    commits_ahead: int | None,
    channel_age_hours: float | None,
    hold: str | None = None,
    hold_age_hours: float | None = None,
    hold_since: str | None = None,
    watcher_gap_hours: float | None = None,
    cancelled_harness_runs: int | None = None,
) -> Verdict:
    """Decide what the channel is doing. Pure — no clock, no network.

    Order, and every step of it is load-bearing:
      1. a HELD channel is held, never broken (promote_channel's order);
      2. what we could not read is unknown, never a reassuring number;
      3. stale is two measured thresholds, plus a backstop for an idle trunk;
      4. the watcher's own missed ticks, when the channel itself is fine;
      5. otherwise the channel is moving and nobody is told anything.
    """
    gap = (
        f"The watcher itself missed scheduled runs — {_days(watcher_gap_hours)} "
        f"since its last one, against a {INTERVAL_HOURS:.0f}h schedule."
        if watcher_gap_hours is not None and watcher_gap_hours > MISSED_TICK_HOURS
        else ""
    )

    def _detail(*blocks: str) -> str:
        return "\n\n".join([b for b in blocks if b] + ([gap] if gap else []) + [DERIVATION])

    # 1. The hold. D2 approved it as a switch, not a habit — so a hold under a
    #    day is the switch working and stays private, and a hold past a day is
    #    reported for as long as it lasts.
    if hold and hold.strip():
        overdue = hold_age_hours is None or hold_age_hours >= HOLD_ALARM_AFTER_HOURS
        head = (
            "The release channel is HELD — paused on purpose, and still paused."
            if overdue
            else "The release channel is held, and was held recently."
        )
        return Verdict(
            state=HELD,
            alarm=overdue,
            title=HELD_TITLE,
            headline=head,
            detail=_detail(
                head,
                "\n".join(_hold_lines(hold, hold_age_hours, hold_since)),
                _drift_line(commits_ahead, channel_age_hours),
                "Nothing is promoted while this holds. Clear the CHANNEL_HOLD "
                "repository variable to resume, or leave it and this will keep "
                "saying so.",
            ),
        )

    # 2. Unknown as unknown (standards/console-honesty.md rule 2). A watcher
    #    that cannot read the channel must never report a moving one.
    if commits_ahead is None or channel_age_hours is None:
        head = (
            f"Unknown: the watcher could not read whether {CHANNEL} is still "
            f"advancing."
        )
        return Verdict(
            state=UNKNOWN,
            alarm=True,
            title=UNKNOWN_TITLE,
            headline=head,
            detail=_detail(
                head,
                _drift_line(commits_ahead, channel_age_hours),
                "This is not the same as a healthy channel and is not being "
                "reported as one. Either GitHub failed to answer, or the "
                f"{CHANNEL} ref no longer exists — the Channel Watch run log "
                "says which.",
            ),
        )

    # 3. Stale: both measured thresholds, or the idle-trunk backstop.
    stale = commits_ahead >= STALE_AFTER_COMMITS and channel_age_hours >= STALE_AFTER_HOURS
    stranded = commits_ahead >= 1 and channel_age_hours >= QUIET_BACKSTOP_HOURS
    if stale or stranded:
        head = (
            f"The release channel has not moved in {_days(channel_age_hours)} "
            f"while the engine moved {commits_ahead} commits."
        )
        head += _merge_train_headline(cancelled_harness_runs)
        return Verdict(
            state=STALE,
            alarm=True,
            # The TITLE is the dedup key and stays constant whatever the cause
            # turns out to be — a diagnosis in the title mints a second card
            # the first busy night.
            title=STALE_TITLE,
            headline=head,
            detail=_detail(
                head,
                f"The {CHANNEL} tag advances by itself on every harness-proven "
                "commit on main, so it should never be more than one green run "
                "behind. It is not a hold: the CHANNEL_HOLD switch is unset.",
                _cause_line(cancelled_harness_runs),
            ),
        )

    # 4. The watcher's own gap, when the channel it watches is fine.
    if gap:
        head = "The channel is moving, but its watcher is not running on time."
        return Verdict(
            state=WATCHER_GAP,
            alarm=True,
            title=WATCHER_TITLE,
            headline=head,
            detail=_detail(head, _drift_line(commits_ahead, channel_age_hours)),
        )

    # 5. Nothing to say. Silence is the ordinary outcome.
    head = (
        f"The {CHANNEL} channel is moving: {commits_ahead} commits ahead, head "
        f"{_days(channel_age_hours)} old."
    )
    return Verdict(
        state=MOVING, alarm=False, title="", headline=head,
        detail=_detail(head),
    )


def _int_or_none(raw: str | None) -> int | None:
    try:
        return int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _timestamp_or_none(raw: str | None) -> str | None:
    """The value if it reads as a timestamp, else None — a failed read is absent.

    The repository-variables API answers 403 to `github.token` (channel-watch.yml
    says so itself), and `gh api` hands the error BODY back on stdout, so what
    arrives as `--hold-updated` in production is
    `{"message":"Resource not accessible by integration","status":"403"}`.
    Passing that through made the age unreadable, unreadable is unknown, and
    unknown is overdue — so every hold alarmed on its first tick and the blob
    was rendered to the operator where a date belongs (DRE-2603). An answer
    that is not a timestamp is a failed read, never a value.
    """
    if not raw or not raw.strip():
        return None
    return raw.strip() if hours_since(raw.strip()) is not None else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Channel staleness alarm (DRE-2552)")
    parser.add_argument("--commits-ahead", default=None)
    parser.add_argument("--channel-committed", default=None,
                        help="ISO date of the current channel head's commit")
    parser.add_argument("--hold", default=None)
    parser.add_argument("--hold-updated", default=None,
                        help="ISO date the CHANNEL_HOLD variable last changed, "
                             "when the token is allowed to read it")
    parser.add_argument("--last-run", default=None,
                        help="ISO date this watcher last completed a run")
    parser.add_argument("--cancelled-harness-runs", default=None,
                        help="how many harness runs on main concluded "
                             "`cancelled` since the channel head — the "
                             "merge-train signal (DRE-3070). Absent or "
                             "unreadable stays unknown, never zero.")
    parser.add_argument("--now", default=None)
    parser.add_argument("--title-file", default=None)
    parser.add_argument("--body-file", default=None)
    args = parser.parse_args(argv)

    # Two sources, in order of authority, and the same fallback `_hold_lines`
    # already uses for display: GitHub's own answer when it gives one, then the
    # `since=` the operator wrote. Neither leaves the age unknown, and unknown
    # still alarms — failing loud is right, it just must not be the only path.
    hold_since = _timestamp_or_none(args.hold_updated) or hold_started(args.hold)
    verdict = evaluate(
        commits_ahead=_int_or_none(args.commits_ahead),
        channel_age_hours=hours_since(args.channel_committed, now=args.now),
        hold=args.hold,
        hold_age_hours=hours_since(hold_since, now=args.now),
        hold_since=hold_since,
        watcher_gap_hours=hours_since(args.last_run, now=args.now),
        cancelled_harness_runs=_int_or_none(args.cancelled_harness_runs),
    )

    print(verdict.detail)

    if args.title_file and verdict.title:
        with open(args.title_file, "w") as fh:
            fh.write(verdict.title)
    if args.body_file:
        with open(args.body_file, "w") as fh:
            fh.write(verdict.detail + "\n")

    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as fh:
            fh.write(f"alarm={'true' if verdict.alarm else 'false'}\n")
            fh.write(f"state={verdict.state}\n")
            fh.write(f"headline={verdict.headline}\n")
    # A moving channel is the ordinary outcome, not a failure: the caller
    # branches on `alarm` rather than on an exit code, the promote_channel rule.
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
