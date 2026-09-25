"""Alarm when a repo's nightly full CI run on `main` did not run (DRE-4805).

The CEO's decision of 2026-09-24 narrowed PR CI to the suites a change can
reach and put a nightly `schedule:` run on `main` behind it that runs
everything (standards/engineering.md, "CI: narrow per change, whole every
night"). That makes the nightly the ONLY thing that runs the suites a pull
request skipped — and the only thing standing between narrowing and silent
tech debt: *"we have to have an alarm if the nightly one doesn't run so we know
that we're not creating tech debt."*

WHAT ALREADY HAS A RAIL, AND WHY THIS IS NOT IT
-----------------------------------------------
A nightly that runs and goes RED is already repaired. Red-Main Repair
(`red-main-repair.yml`) fires on any failed run whose head branch is the
default branch; it filters on the BRANCH, never on the event, so a scheduled
run that goes red is repaired exactly like a push that goes red. A red nightly
therefore does not alarm here — two alarms on one fact is how both get ignored.

What nothing watches is the run that **never happens**: a schedule GitHub
disabled after 60 days of repo inactivity, a cron typo, a workflow that errors
before any job starts, runs stuck in the queue. Nothing fails, because nothing
ran. This is the `channel_watch.py` shape (DRE-2552) pointed at a different
silence, and it raises its alarm through the same mechanism, so no new surface
is added.

WHO IS WATCHED — COMPUTED, NEVER LISTED
---------------------------------------
Every workflow, in every repo in `config/repo-map.json`, whose file **on the
default branch** triggers on BOTH `pull_request` and `schedule`. That is what
"PR CI with a nightly behind it" looks like in data, whatever it is called:
agent-bureau's `CI` (DRE-3656, live today), Portico's `CI` (DRE-4804), and
bureau-pipeline's own `Pipeline Tests` once DRE-4807 gives it a schedule —
which is why no line below mentions any of those names. A scheduled sweep like
`reconcile.yml` has no `pull_request` trigger and is not a nightly. A repo that
adds a nightly is watched from its first merge; one that removes it stops being
watched. **No roster is kept anywhere**, for the reason
`check_workflow_watchers.py` gives one repo down: a list that is remembered is
a list that drifts, silently, in the direction of watching less.

THE THRESHOLDS
--------------
  * **26 hours** since the newest `schedule` run — a nightly plus two hours of
    slack. GitHub does not promise a scheduled run at the minute (it delays
    hardest at :00, which is why every cron in this repo is off the hour), and
    a run that has not started 26 hours after the last one has missed a whole
    night. A tighter 24 would fire on ordinary scheduler drift; a looser 48
    would let a whole night's suites go unrun without a word.
  * **3 hours** queued or in progress. A full-suite nightly is the longest run
    these repos have — this repo's own unit job is budgeted 20 minutes and
    agent-bureau's whole CI is ten jobs — so three hours is not a slow run, it
    is a run that is not going to finish.

UNKNOWN IS ITS OWN READING AND NEVER PASSES
-------------------------------------------
This watcher reads repos it does not live in, across three App installations,
so "I could not look" is a routine answer and must never render as "nightly
ok" (standards/console-honesty.md rules 1-3). A repo the token cannot read, a
workflow file that will not fetch, a run listing GitHub declines: each is
reported UNKNOWN and alarms. A missing nightly takes the card title when both
are true, because it is the actionable one, but the unknown is still named in
the body — an alarm that quietly shrinks the world to what it happened to be
able to read is the failure this file is about.

KNOWN LIMIT, STATED SO IT IS NOT MISTAKEN FOR COVERAGE
------------------------------------------------------
The 2026-09-22 outside-read audit (DRE-4655..4665) found that production has no
alert-delivery environment, so CRITICAL alarms do not currently reach the CEO.
This alarm reaches exactly as far as the mechanism it rides — one deduplicated
Linear card — and no further. It does not fix delivery.

The decision here is pure: no network, no clock of its own, the
`channel_watch.py` shape. `collect()` takes the GitHub reader as an argument so
the whole gathering path is exercised in tests against a declared fleet.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import sys
from dataclasses import dataclass
from typing import Callable

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# One reader for the elapsed-time arithmetic and one for the roster, rather
# than a second copy of either: `hours_since` renders an unreadable timestamp
# as None (unknown), which is the whole of this module's honesty rule, and
# `fleet_roster` is the same `config/repo-map.json` the relay routes on and the
# fleet wake-up dispatches from.
from channel_watch import _days as elapsed, hours_since  # noqa: E402
from check_workflow_watchers import on_block  # noqa: E402
from gh_read_retry import GhReadError, read as gh_read  # noqa: E402
from release_train import fleet_owners, fleet_roster as roster  # noqa: E402

#: Hours since the newest schedule run at which a nightly is late. A nightly
#: plus two hours' slack — see THE THRESHOLDS above.
STALE_AFTER_HOURS = 26.0

#: Hours a schedule run may sit queued or in progress before it is stuck.
STUCK_AFTER_HOURS = 3.0

#: The cron cadence in nightly-watch.yml. The wiring test pins the two
#: together. Hourly, on a schedule of its own: this must not depend on the
#: repos it watches running anything.
INTERVAL_HOURS = 1.0

#: How often a STANDING alarm says it again. Deliberately not the interval
#: above: running hourly is how a missing nightly is FOUND within the hour, and
#: re-confirming hourly is how the card becomes 24 comments a day that nobody
#: reads. `channel-watch.yml` re-confirms daily and that is the rate that reads
#: as deliberate rather than as a stuck process. Only the re-confirmation waits
#: — the card itself is filed on whichever hour the alarm first fires.
RECONFIRM_AFTER_HOURS = 24.0

#: …and the hour it speaks on, in UTC. 07:00 UTC, beside Channel Watch's 07:41,
#: so the two standing alarms land in one morning rather than at two unrelated
#: times of night.
RECONFIRM_HOUR_UTC = 7

#: Per-nightly readings.
OK = "ok"
LATE = "late"
STUCK = "stuck"
NEVER = "never"
UNKNOWN = "unknown"

#: The verdict states. LATE/STUCK/NEVER all mean one thing to a reader — the
#: nightly did not run — so they share a title and a card.
MISSING = "missing"

#: Which readings are worth waking somebody for.
ALARMING = (LATE, STUCK, NEVER)

#: Exact, stable titles — `linear_ops.py find-open` matches on equality, so a
#: title that moved with the numbers would mint a fresh card every hour and the
#: alarm would become the inbox we are escaping. The OWNER is part of the
#: title, and that is not decoration: the watcher runs once per App
#: installation, three matrix jobs at the same minute, and one shared title
#: would have two of them racing to create the same card.
MISSING_TITLE = "Nightly full CI run on main did not run"
UNKNOWN_TITLE = "Nightly watcher cannot read a repo's CI"

#: Printed inside every alarm so a later reader can tell a considered threshold
#: from a guess without coming to find this file.
DERIVATION = (
    f"Thresholds: a watched workflow alarms when its newest schedule run on "
    f"the default branch is more than {STALE_AFTER_HOURS:.0f}h old (a nightly "
    f"plus two hours' slack — GitHub does not promise a scheduled run at the "
    f"minute), when its newest run has been queued or in progress for "
    f"{STUCK_AFTER_HOURS:.0f}h, or when it has never run at all. A run that "
    f"completed RED does not alarm here: Red-Main Repair already fires on any "
    f"failed run whose head branch is the default branch, schedule runs "
    f"included. Watched = every workflow whose file on the default branch "
    f"triggers on BOTH `pull_request` and `schedule`, read from the file "
    f"itself — no list of repos or workflows is kept anywhere."
)


class Unreadable(RuntimeError):
    """GitHub did not answer a read. Never a value, never a zero."""


@dataclass(frozen=True)
class Reading:
    """One watched nightly, or one repo we could not get that far into."""

    subject: str   # "owner/repo · Workflow name", or the repo when unknown
    state: str
    detail: str    # one sentence, in words a non-technical reader can act on


@dataclass(frozen=True)
class Verdict:
    """What the fleet's nightlies are doing, and whether to wake somebody."""

    state: str
    alarm: bool
    title: str
    headline: str
    detail: str


# --------------------------------------------------------------------------- #
# the derivation — who is watched                                              #
# --------------------------------------------------------------------------- #


def is_nightly(text: str) -> bool | None:
    """Does this workflow file declare PR CI with a nightly behind it?

    True/False is the answer; **None is "I could not read it"** and stays
    unknown all the way to the card. A file that will not parse might be the
    nightly that stopped, and a watcher that quietly skipped it would report
    the repo as fine.
    """
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    if not isinstance(doc, dict):
        return None
    on = on_block(doc)
    return "pull_request" in on and "schedule" in on


# --------------------------------------------------------------------------- #
# the reading — what one nightly's newest schedule run means                   #
# --------------------------------------------------------------------------- #


def read_run(status: str | None, conclusion: str | None,
             age_hours: float | None) -> str:
    """The state of one watched nightly. Pure — no clock, no network.

    `conclusion` is taken and deliberately not read: whether the nightly PASSED
    is Red-Main Repair's question, and answering it here would file a second
    card for a failure that already has a rail. What this decides is whether it
    RAN.
    """
    if not status:
        return NEVER
    if age_hours is None:
        # A run GitHub will not date is a run we cannot judge.
        return UNKNOWN
    if status != "completed":
        return STUCK if age_hours >= STUCK_AFTER_HOURS else OK
    return LATE if age_hours > STALE_AFTER_HOURS else OK


def _sentence(subject: str, state: str, age_hours: float | None,
              status: str | None, conclusion: str | None) -> str:
    if state == NEVER:
        return (f"{subject}: no scheduled run has EVER happened on the default "
                f"branch. The workflow declares a nightly and GitHub has never "
                f"started one — a cron that does not parse, a schedule GitHub "
                f"disabled after 60 days of repo inactivity, or a workflow that "
                f"errors before any job starts all look exactly like this.")
    when = elapsed(age_hours) if age_hours is not None else "an unknown time"
    if state == LATE:
        return (f"{subject}: the newest scheduled run is {when} old. Every "
                f"suite a pull request skipped in that time has not been run "
                f"by anything.")
    if state == STUCK:
        return (f"{subject}: the newest scheduled run has been {status} for "
                f"{when}. It started and is not finishing, so the suites it "
                f"was going to run have not run.")
    return (f"{subject}: nightly ok — the newest scheduled run is {when} old "
            f"({status}"
            + (f", {conclusion}" if conclusion else "")
            + ").")


# --------------------------------------------------------------------------- #
# the gathering — GitHub's records, through an injected reader                 #
# --------------------------------------------------------------------------- #


def _decode(blob: dict) -> str | None:
    """A contents-API answer as text, or None when it is not one.

    GitHub answers a file over ~1 MB with `encoding: none` and an empty body.
    That is a file we did not read, not an empty workflow.
    """
    if not isinstance(blob, dict):
        return None
    if (blob.get("encoding") or "") != "base64":
        return None
    try:
        return base64.b64decode(blob.get("content") or "").decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None


def collect(api: Callable[[str], object], *, roster: dict,
            now: str | None = None) -> list:
    """Every watched nightly's reading, for the repos in `roster`.

    `api(path)` returns GitHub's parsed JSON answer and raises `Unreadable`
    when it will not answer — which is the ONE thing this function is careful
    about. Every refusal becomes an UNKNOWN reading at the narrowest subject it
    can name (the repo, or the one workflow), and nothing is ever skipped
    quietly.
    """
    readings: list = []
    for _slug, repo in sorted(roster.items(), key=lambda item: item[1]):
        try:
            branch = str((api(f"repos/{repo}") or {}).get("default_branch") or "")
        except Unreadable as refusal:
            readings.append(Reading(repo, UNKNOWN, _unreadable_repo(repo, refusal)))
            continue
        if not branch:
            readings.append(Reading(repo, UNKNOWN, _unreadable_repo(
                repo, "GitHub named no default branch")))
            continue
        try:
            listing = api(f"repos/{repo}/actions/workflows?per_page=100") or {}
        except Unreadable as refusal:
            readings.append(Reading(repo, UNKNOWN, _unreadable_repo(repo, refusal)))
            continue
        workflows = listing.get("workflows") or []
        # GitHub pages this list. A repo with more workflows than one page
        # would have its nightly fall off the end, and the watcher would report
        # on the ones it happened to see — watching less, without saying so.
        # It says so: a partial read is a read we did not finish.
        total = listing.get("total_count")
        if isinstance(total, int) and total > len(workflows):
            readings.append(Reading(repo, UNKNOWN, _unreadable_repo(
                repo, f"it has {total} workflows and only {len(workflows)} came "
                      f"back in one page, so the list was read in part")))
            continue
        for workflow in workflows:
            readings.extend(
                _read_workflow(api, repo, branch, workflow, now)
            )
    return readings


def _unreadable_repo(repo: str, why) -> str:
    return (f"{repo}: UNKNOWN — the watcher could not read the repository, so "
            f"whether it has a nightly, and whether that nightly ran, are both "
            f"unanswered. This is not a healthy repo and is not being reported "
            f"as one; most often it means the bureau App is not installed there "
            f"with Actions read. GitHub said: {why}")


def _read_workflow(api, repo: str, branch: str, workflow: dict,
                   now: str | None) -> list:
    """The readings one workflow file produces: none, or exactly one."""
    path = str(workflow.get("path") or "")
    if not path.startswith(".github/workflows/"):
        # A run can name a workflow that is not a file in the tree (a deleted
        # one still has runs). There is nothing to read the triggers from.
        return []
    subject = f"{repo} · {workflow.get('name') or path}"
    try:
        blob = api(f"repos/{repo}/contents/{path}?ref={branch}")
    except Unreadable as refusal:
        return [Reading(subject, UNKNOWN, _unreadable_file(subject, path, refusal))]
    text = _decode(blob)
    nightly = is_nightly(text) if text is not None else None
    if nightly is None:
        return [Reading(subject, UNKNOWN, _unreadable_file(
            subject, path, "its contents did not come back as readable YAML"))]
    if not nightly:
        return []
    filename = path.rsplit("/", 1)[-1]
    try:
        runs = api(
            f"repos/{repo}/actions/workflows/{filename}/runs"
            f"?branch={branch}&event=schedule&per_page=1"
        ) or {}
    except Unreadable as refusal:
        return [Reading(subject, UNKNOWN, (
            f"{subject}: UNKNOWN — this workflow declares a nightly, and "
            f"GitHub would not say whether it has run. That is not the same as "
            f"a nightly that ran. GitHub said: {refusal}"))]
    record = (runs.get("workflow_runs") or [None])[0] or {}
    status = record.get("status")
    conclusion = record.get("conclusion")
    age = hours_since(record.get("created_at"), now=now)
    state = read_run(status, conclusion, age)
    return [Reading(subject, state,
                    _sentence(subject, state, age, status, conclusion))]


def _unreadable_file(subject: str, path: str, why) -> str:
    return (f"{subject}: UNKNOWN — `{path}` could not be fetched, so whether it "
            f"is a nightly, and whether that nightly ran, are both unanswered. "
            f"Never reported as ok. GitHub said: {why}")


# --------------------------------------------------------------------------- #
# the verdict                                                                  #
# --------------------------------------------------------------------------- #


def should_reconfirm(now: str | None = None) -> bool:
    """Is this the hourly tick that re-confirms a standing alarm?

    One tick in `RECONFIRM_AFTER_HOURS` — read off the clock rather than from
    the card's own comments, because a watcher that has to read a thread to
    decide whether to write to it is a second thing that can fail, and the
    thing it protects is one comment a day.
    """
    moment = dt.datetime.now(dt.timezone.utc)
    if now:
        try:
            moment = dt.datetime.fromisoformat(now.replace("Z", "+00:00"))
        except ValueError:
            return True  # an unreadable clock must not silence a real alarm
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=dt.timezone.utc)
    ticks = int(RECONFIRM_AFTER_HOURS / INTERVAL_HOURS)
    return moment.hour % ticks == RECONFIRM_HOUR_UTC % ticks


def title_for(base: str, owner: str) -> str:
    return f"{base} — {owner}" if owner else base


def evaluate(readings: list, *, owner: str = "") -> Verdict:
    """What to say, and whether to say it. Pure.

    Order, and every step of it is load-bearing:
      1. a nightly that did not run is the actionable finding and takes the
         card, however many repos we also could not read;
      2. what we could not read is UNKNOWN, never a reassuring silence;
      3. otherwise every nightly we can see has run, and nobody is told
         anything. Silence is the ordinary outcome.
    """
    missing = [r for r in readings if r.state in ALARMING]
    unknown = [r for r in readings if r.state == UNKNOWN]
    fine = [r for r in readings if r.state == OK]

    def _detail(head: str, *blocks: str) -> str:
        parts = [head]
        for block, lines in (
            ("Did not run:", missing),
            ("Could not be read:", unknown),
            ("Ran:", fine),
        ):
            if lines:
                parts.append(block + "\n" + "\n".join(f"  - {r.detail}" for r in lines))
        parts.extend(b for b in blocks if b)
        parts.append(DERIVATION)
        return "\n\n".join(parts)

    if missing:
        named = ", ".join(r.subject for r in missing[:3])
        if len(missing) > 3:
            named += f", and {len(missing) - 3} more"
        head = (
            f"{len(missing)} nightly full CI run(s) on main did not run: {named}."
        )
        return Verdict(
            state=MISSING,
            alarm=True,
            title=title_for(MISSING_TITLE, owner),
            headline=head,
            detail=_detail(
                head,
                "The nightly is the only thing that runs the suites a narrowed "
                "pull request skipped. While it is not running, those suites "
                "are not being run by anything, and the debt is invisible — "
                "which is the whole reason this alarm exists.",
            ),
        )

    if unknown:
        head = (
            f"Unknown: the watcher could not read {len(unknown)} of "
            f"{len(readings)} subject(s), so it cannot say whether their "
            f"nightly ran."
        )
        return Verdict(
            state=UNKNOWN,
            alarm=True,
            title=title_for(UNKNOWN_TITLE, owner),
            headline=head,
            detail=_detail(
                head,
                "This is not the same as a healthy nightly and is not being "
                "reported as one. The usual cause is an App installation that "
                "cannot read the repository's Actions — the Nightly Watch run "
                "log carries GitHub's own answer for each one.",
            ),
        )

    head = (
        f"Every nightly this watcher can see has run: {len(fine)} watched "
        f"workflow(s) across the roster."
        if fine else
        "No workflow in the roster declares a nightly, so there is nothing to "
        "watch."
    )
    return Verdict(state=OK, alarm=False, title="", headline=head,
                   detail=_detail(head))


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #


def _gh_api(path: str):
    """One GitHub read, retried through the shared read seam.

    Every refusal — a throttle that outlasted its retries, a 404, a token that
    cannot see the repository — arrives here as `Unreadable`, which `collect`
    renders as UNKNOWN. Nothing is ever substituted for an answer.
    """
    try:
        answer = gh_read(["api", path], log=lambda line: print(line, file=sys.stderr))
    except GhReadError as refusal:
        raise Unreadable(str(refusal)) from refusal
    try:
        return json.loads(answer or "{}")
    except json.JSONDecodeError as broken:
        raise Unreadable(f"the answer to {path} was not JSON") from broken


def _cmd_owners(args) -> int:
    """The matrix: every owner in the roster, so the token is minted once per
    App installation (fleet-wake.yml's shape — one installation, one token)."""
    owners = fleet_owners(roster(args.map or None))
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"owners={json.dumps(owners)}\n")
    print(f"nightly-watch: {len(owners)} owner(s) in the roster: "
          f"{', '.join(owners) or 'none'}")
    return 0


def _cmd_watch(args) -> int:
    fleet = roster(args.map or None, args.owner or None)
    verdict = evaluate(
        collect(_gh_api, roster=fleet, now=args.now or None),
        owner=args.owner or "",
    )

    print(verdict.detail)

    if args.title_file and verdict.title:
        with open(args.title_file, "w", encoding="utf-8") as fh:
            fh.write(verdict.title)
    if args.body_file:
        with open(args.body_file, "w", encoding="utf-8") as fh:
            fh.write(verdict.detail + "\n")

    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"alarm={'true' if verdict.alarm else 'false'}\n")
            fh.write(f"state={verdict.state}\n")
            fh.write(f"headline={verdict.headline}\n")
            fh.write(
                f"reconfirm={'true' if should_reconfirm(args.now or None) else 'false'}\n"
            )
    # A fleet whose nightlies all ran is the ordinary outcome, not a failure:
    # the caller branches on `alarm`, never on an exit code (channel_watch's
    # rule, and promote_channel's before it).
    return 0


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    owners = sub.add_parser(
        "owners", help="the owners in the roster, as the watcher's matrix")
    owners.add_argument("--map", default="", help="the roster; the fleet map by default")

    watch = sub.add_parser("watch", help="read one owner's nightlies and decide")
    watch.add_argument("--owner", default="",
                       help="read only this owner's repos — an App "
                            "installation token is scoped to one installation")
    watch.add_argument("--map", default="", help="the roster; the fleet map by default")
    watch.add_argument("--now", default=None)
    watch.add_argument("--title-file", default=None)
    watch.add_argument("--body-file", default=None)

    args = parser.parse_args(argv)
    return {"owners": _cmd_owners, "watch": _cmd_watch}[args.command](args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
