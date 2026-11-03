"""Decide whether the proven sha may become the channel head (DRE-2551).

Wave 1 Step 2. `release-gate.yml` already VALIDATES a tag against the harness's
stamp and is good machinery — it is not touched here. What was missing is the
thing that PUSHES: the tag move was a human act, it happened five times in ten
days and then stopped, and nothing noticed for a month while `main` drifted 174
commits past `v5`.

Under automatic promotion that rot cannot reach the `stable` ref: it advances
on every proven commit, so it is never more than one behind. Scope, stated
because it is easy to over-read: `stable` is the only ref moved here, nothing
pins it, and the `vN` cut the fleet does pin stays a human act (README
"Release channel"; docs/self-hosting.md).

This module is the decision only — no network, no git. The workflow gathers
GitHub's records and acts on the verdict, the `release_gate.py` shape.

A refusal here is ORDINARY. Most harness runs will not promote (nothing new,
held, already there), and that is a no-op, not a failure — so the caller reads
the `promote` output rather than an exit code.

WHY THE REFUSAL HAS TO SAY WHICH REFUSAL IT IS (DRE-3070)
---------------------------------------------------------
On 2026-09-03, fourteen PRs merged to `main` in two and a half hours and
`stable` did not move once — it sat 50 commits behind while every product repo
ran the afternoon's code. The harness runs on every push to main and only ONE
run at a time may touch the sandbox, so a busy evening leaves a queue: the run
proving commit N finishes and promotes, and heads that were still waiting when
a newer push arrived are cancelled before they start.

The channel surviving that is the design (`docs/self-hosting.md`, "Queue
behind, never cancel"). What was NOT survivable is that the whole case was
invisible: this workflow only ran on a GREEN harness run on main, so a
displaced run produced no promote-channel run, no receipt, and nothing for the
staleness alarm to read — and a PR-head run produced a bare `skipped`, which
reads identically to a defect. "The channel is quiet", "the channel is
starved", and "that run was never about the channel" all looked the same.

So EVERY completed harness run now reaches this decision, and the decision
names which of four things happened:

    harness-run-not-on-main         a PR-head run — nothing owed
    harness-cancelled-by-newer-push a merge train displaced it
    harness-failed                  a red trunk
    harness-passed-promoting        the channel moved

machine-readably, so a watcher can count merge trains instead of reporting
the cause as unknown (the channel-staleness alarm did, until DRE-6053 retired
it). Three more names cover the refusals that already existed and were equally
silent: `channel-held`, `no-harness-stamp`, `not-ahead-of-channel`.

A FAILED RUN IS NOT ALWAYS A RED TRUNK (DRE-9068)
-------------------------------------------------
On 2026-11-02 run 41581663622 passed every code scenario and lost two —
`gate_paths`' cleanup and `proof_task_parses`' exercise — to GitHub's `403 API
rate limit exceeded for installation ID 123249480`, the worker's shared hour.
The decision read the run's `failure` conclusion as `harness-failed`, and
`stable` held until a person promoted by hand. A run whose refusal outlasted
the client's wait now stamps `RATE_LIMIT_MARKER`, and the decision reads it
as `harness-inconclusive` / rate-limited: re-run the harness, not repair the
trunk. promote-channel.yml fetches the stamp on a failed harness run for that,
so the marker reaches the decision at all.

THE BY-HAND PATH, AND THE LINE IT MUST NOT CROSS (DRE-4111)
-----------------------------------------------------------
Everything above is reached by exactly one trigger: a `workflow_run` on the
harness. So when the harness fails for a reason that is not about the code,
`stable` freezes and the only move is to re-run it and hope. On 2026-09-16 the
channel sat five merge commits and six merged pull requests behind while four
of the harness's five scenarios died on

    GitHub API 403: "API rate limit exceeded for installation ID 123249480"

— the worker App's hourly bucket, empty. Nothing was wrong with any merged
commit, and every promote-channel run that night reported success while
promoting nothing.

`evaluate(manual=True)` is the by-hand route, and its whole design is the line
it does not cross. The harness is what PROVES a commit, and `bureau-harness` is
deliberately the one repo kept off the channel so promotion can never validate
itself — so a by-hand promote must not become "skip the proof":

  * The ORDINARY by-hand promote re-reads the candidate's own combined commit
    status and still requires a green `integration-harness` there. That status
    is run-agnostic: it is the stamp from whichever run proved the sha, which
    is exactly the case this exists for — the proof passed an hour ago and the
    channel is still behind.
  * FORCING past a red or absent stamp is a separate, louder act: its own
    input, never the default, refused without a stated reason and refused to a
    machine actor. It overrides the PROOF and nothing else — not the ancestry
    rail, not the hold, and not the trunk check.
  * The TRUNK check is the by-hand analogue of the branch question the
    automatic path asks of its triggering run. `harness.yml` also runs on
    `pull_request`, so a PR head carries a green stamp of its own; without this
    a by-hand promote could put `stable` on a commit that never merged — the
    proof present, the code unshipped.
  * The mover is recorded either way: who, when, which sha, and why by hand.

AGENT-BUREAU'S MIRROR TESTS ARE PART OF THE PROOF (DRE-6496)
------------------------------------------------------------
On 2026-10-09 the `operator-step` vocabulary reached `stable` and broke
agent-bureau's CI three times in one day: agent-bureau mirrors this repo's
vocabularies, and every mirror's drift test reads bureau-pipeline at `stable`,
so the break was found only after the promotion. `with_mirror` weighs
`scripts/mirror_check.py`'s result against a decision that would move the
channel — `mirror-tests-failed` when a test passes on `stable` and fails on the
candidate, `mirror-check-blocked` when the check could not run — and a person's
`force` overrides both, as it overrides a red harness stamp. The workflow runs
the decision twice: on the proof alone, to learn whether the check runs at all,
and then with `--mirror-result`.

Since DRE-6622 a `mirror-check-blocked` decision on a `main` or by-hand run
fails the run, after every receipt is written. From 02:26 to 08:45 PT on
2026-10-10 the check could not run at all, and every run held `stable` behind
thirteen merges while finishing green — and nothing in this repository alarms
on a quiet channel, so the red run is the signal.

THE UNIT SUITE STARTS IT TOO (DRE-6993)
---------------------------------------
`with_unit_suite` (DRE-6968) refuses a commit whose own `Pipeline Tests` run
on `main` is red, still running or unreadable, and since DRE-6993 the
workflow hands it that run on every route and starts again when the suite
ends. A run started by the suite carries the SUITE's conclusion, so
`--woken-by` names the workflow that started it: for `UNIT_SUITE` the
conclusion is not handed to `evaluate` — a red suite read as `harness-failed`
would name the wrong run — and the harness proof is the commit's own
`integration-harness` status, as on the by-hand route.

A REFUSED RUN LOOKS BACK FOR THE NEWEST PROVEN COMMIT (DRE-9069)
----------------------------------------------------------------
Each automatic run judges one commit, the one whose own run started it, and
nothing ever looked at an older proven commit again. So a proven commit A
whose promote run was canceled while pending — the `promote-channel` group
keeps one pending run, and GitHub drops it when a newer one queues (runs
41583518423 and 41560513828 on 2026-11-02) — or was refused for a reason that
clears on its own, had no run left to promote it, and `stable` stayed behind
A whenever every newer commit failed.

Now an automatic run that refuses its own commit B at the HARNESS proof —
`LOOK_BACK_OUTCOMES`, never the hold, the trunk, the ancestry rail, the unit
suite or the mirror check — walks main's first-parent chain from main's head,
newest first, at most `LOOK_BACK_LIMIT` commits, stopping at `stable`, and the
first `proven` commit becomes the candidate. It then gets every gate, read for
A, under the outcome words that already exist; the look-back only says where
to look, and A's own stamp is re-read as the proof. `look-back` is the one
command here that reads anything itself, because the first proven commit ends
the search and the reads have to stop there: the chain from `git`, each stamp
from `gh`. A by-hand dispatch never looks back, so force stays its alone.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import subprocess
import sys
from typing import NamedTuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mirror_check  # noqa: E402

#: The moving, always-proven head this repo keeps for itself. NOT a consumer
#: pin — no product repo references `@stable`; `v*` tags remain what they
#: always were, the operator-cut refs the fleet pins (docs/self-hosting.md).
CHANNEL = "stable"

#: The context harness.yml stamps on the sha it actually checked out. The same
#: string release_gate.py reads — one name for one fact.
STATUS_CONTEXT = "integration-harness"

#: Ancestry sentinel for "the channel ref does not exist yet", which is the
#: first promotion and cannot possibly move anything backwards.
NO_CHANNEL_YET = "no-channel-yet"

#: What the harness driver writes at the FRONT of the stamp's description when
#: a run died on a dead SANDBOX rather than on the commit (DRE-3076) — see
#: `harness/sandbox_health.py`, which composes the line, and harness.yml, which
#: carries it into the status.
#:
#: The distinction is the point of the marker. "harness failed" reads as *this
#: commit is bad*; on 2026-09-03 the commit was fine and the sandbox's own
#: reconcile sweep had been rate-limited by Linear, and the run that followed
#: proved the same trunk green in eleven minutes. Marker first, because GitHub
#: clamps a status description at 140 characters and a clamped receipt must
#: still be recognisable here.
BLOCKED_MARKER = "harness blocked:"

#: The blocked receipt for one particular sandbox fault (DRE-6147): GitHub left
#: a sandbox run queued with no jobs, and the harness re-ran it
#: `framework.QUEUE_REKICK_LIMIT` times without it starting. It OPENS with
#: `BLOCKED_MARKER`, so the stamp step and red-main repair's back-off read it
#: as the block it is; the words after it are what lets the receipt name
#: GitHub's queue rather than the sandbox's machinery.
QUEUE_STALL_MARKER = f"{BLOCKED_MARKER} GitHub queue stall"

#: The receipt for a run GitHub refused for a RATE LIMIT that outlasted the
#: client's wait (DRE-9068), written by `harness/__main__.py`. It opens with
#: `BLOCKED_MARKER` for the queue stall's reason — every reader that knows a
#: block (the stamp step, red-main repair's back-off, the by-hand proof read)
#: keeps knowing this one — and the words after it let `evaluate` name it
#: `harness-inconclusive` / rate-limited rather than a dead sandbox.
RATE_LIMIT_MARKER = f"{BLOCKED_MARKER} GitHub rate limit"

#: GitHub's compare API vocabulary (base=channel, head=candidate).
AHEAD = "ahead"
BEHIND = "behind"
IDENTICAL = "identical"
DIVERGED = "diverged"

#: GitHub's own conclusion for a run displaced from the concurrency group's
#: single pending slot by a newer push. Not a failure, and reported as such.
CANCELLED = "cancelled"
SUCCESS = "success"

#: The only branch whose harness runs are promotion candidates. A PR-head run
#: proved a commit that is not on the trunk.
TRUNK = "main"

#: The receipt vocabulary (DRE-3070). Stable strings: docs/self-hosting.md
#: names them, so they are constants here and nowhere else. The first three
#: are the card's three reasons; the rest are the refusals that already
#: existed and were equally unnamed. `docs/self-hosting.md`
#: carries the same table, pinned by a test.
OUTCOME_PROMOTING = "harness-passed-promoting"
OUTCOME_CANCELLED = "harness-cancelled-by-newer-push"
OUTCOME_FAILED = "harness-failed"
OUTCOME_NOT_MAIN = "harness-run-not-on-main"
OUTCOME_HELD = "channel-held"
OUTCOME_BLOCKED = "harness-blocked-by-sandbox"
OUTCOME_QUEUE_STALL = "harness-blocked-by-github-queue"
#: DRE-9068: GitHub refused the harness for a rate limit, so the run proved
#: nothing either way. The cause leads the reason, so the receipt reads
#: `harness-inconclusive: rate-limited — …`.
OUTCOME_INCONCLUSIVE = "harness-inconclusive"
INCONCLUSIVE_RATE_LIMITED = "rate-limited"
OUTCOME_UNPROVEN = "no-harness-stamp"
OUTCOME_NOT_AHEAD = "not-ahead-of-channel"

#: The by-hand vocabulary (DRE-4111). Separate names on purpose: "the harness
#: moved the channel" and "a person moved the channel" are different facts, and
#: a row of receipts that cannot tell them apart is the thing this card is
#: named after.
OUTCOME_BY_HAND = "by-hand-promoting"
OUTCOME_BY_HAND_FORCED = "by-hand-forced-promoting"
OUTCOME_FORCE_NEEDS_REASON = "by-hand-force-needs-reason"
OUTCOME_FORCE_NOT_OPERATOR = "by-hand-force-not-operator"
OUTCOME_NOT_ON_TRUNK = "by-hand-candidate-not-on-main"

#: The mirror check's two refusals (DRE-6496). Named apart for the same reason
#: the harness's are: "agent-bureau's mirror of this change is behind" and
#: "the check could not run" send a reader to different places.
OUTCOME_MIRROR_FAILED = "mirror-tests-failed"
OUTCOME_MIRROR_BLOCKED = "mirror-check-blocked"

#: The candidate's own unit suite's three refusals (DRE-6968). Apart for the
#: same reason again: a red suite is a break to repair, an unfinished one is a
#: wait, and an unreadable one proves nothing either way.
OUTCOME_UNIT_RED = "unit-suite-red"
OUTCOME_UNIT_NOT_FINISHED = "unit-suite-not-finished"
OUTCOME_UNIT_UNPROVEN = "unit-suite-unproven"

#: The workflow whose run on `main` is the candidate's own unit suite, and the
#: conclusions that mean it went red rather than proved nothing.
UNIT_SUITE = "Pipeline Tests"
UNIT_RED_CONCLUSIONS = ("failure", "timed_out")

#: The outcomes that move the channel — the only ones the mirror check reads.
PROMOTING_OUTCOMES = (OUTCOME_PROMOTING, OUTCOME_BY_HAND, OUTCOME_BY_HAND_FORCED)

#: The refusals at the harness proof — the only ones that look back for an
#: older proven commit (DRE-9069). Every other refusal says something about
#: the channel or the commit that a look-back must not talk over: the hold
#: still wins, and a commit refused by its unit suite or the mirror check is
#: offered again when that record changes.
LOOK_BACK_OUTCOMES = (OUTCOME_CANCELLED, OUTCOME_FAILED, OUTCOME_BLOCKED,
                      OUTCOME_QUEUE_STALL, OUTCOME_UNPROVEN)

#: How many commits of main's first-parent chain the look-back reads, from
#: main's head. The deepest lag seen so far was 22 commits behind on
#: 2026-11-02, a count that includes branch commits, so fewer first-parent ones.
LOOK_BACK_LIMIT = 20

#: The record the look-back leaves for the two decision steps after it.
LOOK_BACK_FILE = "look_back.json"

#: Where the walk ended.
STOP_FOUND = "found"
STOP_CHANNEL = "channel"
STOP_LIMIT = "limit"
STOP_END = "end"
#: The chain itself could not be read — the fetch or `rev-list` failed — so
#: nothing is known about the commits before the run's own; never `STOP_END`,
#: which says main's history really ended (standards/console-honesty.md).
STOP_UNREAD = "unread"

#: The bound on the mirror check's one-line summary (DRE-6683). Promote
#: Channel run 38076381376 moved `stable` and then went red: its summary named
#: all 1,238 tests already red on `stable`, 207,749 bytes, and Linux will not
#: start a process with an environment string over 131,072, so `Say what
#: happened` could not start. The line now states both counts, names at most
#: `MIRROR_EXAMPLES` tests of each list, each clamped, and points at the file
#: that holds every one — which `promote-channel.yml` keeps as the run's
#: `MIRROR_ARTIFACT`.
MIRROR_SUMMARY_MAX_BYTES = 1500
MIRROR_EXAMPLES = 3
MIRROR_EXAMPLE_CHARS = 200
MIRROR_RESULT_FILE = "mirror_result.json"
MIRROR_ARTIFACT = "mirror-result"

#: Actors a FORCED promote is refused to. Forcing past the harness is the one
#: act here that is operator-only, and "a person ticked the box" has to be
#: checked rather than assumed: every build agent in this fleet runs under an
#: App token carrying `actions: write` and can therefore dispatch a workflow
#: (agent-task.yml, plan.yml). A bot login is the one thing GitHub tells us for
#: free, so it is what we read.
MACHINE_ACTORS = ("github-actions", "github-actions[bot]")


class Decision(NamedTuple):
    """`(promote, reason, outcome)` — prose for a human, a token for a reader.

    Unpacks as the `(promote, reason)` pair it has always been; `outcome` is
    the addition DRE-3070 needs, because an alarm cannot count English.
    """

    promote: bool
    reason: str
    outcome: str


def matches(pattern: str, ref: str) -> bool:
    """Does a workflow `tags:` glob match this ref name?

    Exists so the test that release-gate.yml actually fires on the channel can
    ask the question in the same vocabulary GitHub uses. `v*` does not match
    `stable`, and a gate wired to a ref nobody pushes validates nothing.
    """
    return fnmatch.fnmatch(ref, pattern)


def _harness_stamp(combined: dict | None) -> dict | None:
    """The harness's own stamp for this sha, or None if it never said.

    A `{}` substitute (what the caller writes when the status fetch fails) and
    a genuinely absent stamp are the same answer: we do not know, so we do not
    promote.
    """
    for status in (combined or {}).get("statuses") or []:
        if status.get("context") == STATUS_CONTEXT:
            return status
    return None


def _harness_verdict(combined: dict | None) -> str | None:
    """The stamp's state alone."""
    stamp = _harness_stamp(combined)
    return stamp.get("state") if stamp else None


def blocked_by_sandbox(combined: dict | None) -> str | None:
    """The sandbox's quoted failure when the harness never got to judge this
    commit, else None.

    Read BEFORE the state, and independently of it: the marker says the run
    proved nothing, which is true whatever colour the stamp ended up wearing.
    """
    stamp = _harness_stamp(combined) or {}
    description = (stamp.get("description") or "").strip()
    return description if description.startswith(BLOCKED_MARKER) else None


def is_machine_actor(actor: str | None) -> bool:
    """Is this login a bot rather than a person (DRE-4111)?

    Nobody named is TRUE — the caller that cannot say who asked for a forced
    promote has not established that anybody did, and this is the one gate in
    the file that exists to keep the fleet's own agents out.
    """
    name = (actor or "").strip().lower()
    if not name:
        return True
    return name.endswith("[bot]") or name in MACHINE_ACTORS


def _ancestry_gate(
    sha: str,
    ancestry: str | None,
    *,
    promoting: str,
    creating: str,
    outcome: str,
) -> Decision:
    """The channel may only ever advance.

    Two harness runs can finish out of order; without this, the
    later-finishing older commit wins and the channel silently regresses —
    which would look exactly like a working channel while shipping older code.

    Shared by both routes since DRE-4111, because this is precisely the rail a
    by-hand promote does not get to skip: `--force` is about the PROOF, and a
    promotion that moved `stable` backwards would be a rollback wearing a
    promotion's receipt.
    """
    if ancestry == NO_CHANNEL_YET:
        return Decision(True, creating, outcome)
    if ancestry == IDENTICAL:
        return Decision(
            False, f"{CHANNEL} is already at {sha} — nothing to do.",
            OUTCOME_NOT_AHEAD,
        )
    if ancestry == BEHIND:
        return Decision(False, (
            f"refusing to move {CHANNEL} backwards to {sha}: it is behind the "
            f"current channel head. A late-finishing older run must not "
            f"regress the channel."
        ), OUTCOME_NOT_AHEAD)
    if ancestry != AHEAD:
        return Decision(False, (
            f"not promoting {sha}: could not establish that it is ahead of "
            f"{CHANNEL} (ancestry={ancestry!r}). Failing closed."
        ), OUTCOME_NOT_AHEAD)
    return Decision(True, promoting, outcome)


def by_hand(
    combined: dict | None,
    sha: str,
    *,
    hold: str | None = None,
    ancestry: str | None = None,
    force: bool = False,
    reason: str | None = None,
    actor: str | None = None,
    trunk: str | None = None,
    channel_head: str | None = None,
) -> Decision:
    """The `workflow_dispatch` route (DRE-4111) — a person moving the channel.

    `trunk` is GitHub's compare status for `base=<default branch>,
    head=<candidate>`: `behind` or `identical` mean the candidate is reachable
    from the trunk, anything else means it is not (and an unreadable compare
    fails closed, like every other unverifiable read here).

    Order, and why: the hold first, because a held channel is a standing
    instruction and neither by-hand route may talk over it; then the shape of
    the force request itself, because a malformed one must be refused before it
    is weighed against anything; then the trunk, then the proof, then the
    ancestry rail that both routes share.
    """
    stated = (reason or "").strip()

    # 1. The hold outranks a by-hand promote exactly as it outranks the
    #    automatic one. It is an operator saying "do not advance the channel",
    #    and `--force` is about the harness, not about that.
    if hold and hold.strip():
        return Decision(False, (
            f"channel HELD — not promoting {sha} by hand. Reason on record: "
            f"{hold.strip()}. A by-hand promote does not talk over the hold; "
            f"clear the hold variable and dispatch again."
        ), OUTCOME_HELD)

    # 2. Forcing is the louder act, so it is the one with conditions. A reason
    #    is the record the run is kept for — an unexplained force is the July
    #    ceremony back again, this time with a button.
    if force and not stated:
        return Decision(False, (
            f"refusing to force {CHANNEL} to {sha}: forcing past the harness "
            f"requires a stated reason, and none was given. Dispatch again "
            f"with the reason that makes this safe."
        ), OUTCOME_FORCE_NEEDS_REASON)
    if force and is_machine_actor(actor):
        return Decision(False, (
            f"refusing to force {CHANNEL} to {sha}: forcing past the harness "
            f"is operator-only and this run was started by "
            f"{actor or 'nobody we can name'}. An ordinary by-hand promote of "
            f"a proven commit is still open."
        ), OUTCOME_FORCE_NOT_OPERATOR)

    # 3. On the trunk at all? The automatic path asks this of its triggering
    #    run's branch; a dispatch has no run to ask, so it asks the commit.
    #    harness.yml also runs on `pull_request`, so a PR head carries a green
    #    stamp of its own — without this, a by-hand promote could put the
    #    channel on a commit that never merged.
    if trunk not in (BEHIND, IDENTICAL):
        return Decision(False, (
            f"not promoting {sha} by hand: could not establish that it is on "
            f"the trunk (compare={trunk!r}). The channel carries merged code; "
            f"a commit with a green harness stamp is not necessarily one that "
            f"shipped. Failing closed."
        ), OUTCOME_NOT_ON_TRUNK)

    # 4. The proof. This is the safety line: the candidate's own combined
    #    status, from whichever run stamped it — not the newest run, which is
    #    the entire reason this route exists.
    blocked = blocked_by_sandbox(combined)
    verdict = _harness_verdict(combined)
    proven = verdict == SUCCESS and not blocked
    if not proven:
        seen = blocked or verdict or "no stamp at all"
        if not force:
            return Decision(False, (
                f"refusing to promote {sha} by hand: its {STATUS_CONTEXT} "
                f"status reports {seen}. The by-hand path promotes a commit "
                f"the harness has ALREADY proved — from any run, not only the "
                f"newest — and this one is not proved. The last proven sha is "
                f"{channel_head or 'unknown'}, where {CHANNEL} stands now. "
                f"Re-run the harness on {sha}, or dispatch again with force "
                f"and a reason."
            ), OUTCOME_UNPROVEN)
        return _ancestry_gate(sha, ancestry, outcome=OUTCOME_BY_HAND_FORCED, promoting=(
            f"FORCED: moving {CHANNEL} to {sha} past an {STATUS_CONTEXT} "
            f"status of {seen}. Mover: {actor}. Reason: {stated}. The "
            f"harness did not prove this commit and nothing here pretends "
            f"otherwise."
        ), creating=(
            f"FORCED: creating {CHANNEL} at {sha} past an {STATUS_CONTEXT} "
            f"status of {seen}. Mover: {actor}. Reason: {stated}."
        ))

    # 5. Proven, on the trunk, no hold. An ordinary by-hand promote — and if
    #    force was ticked, say plainly that it was not needed rather than file
    #    a routine move under the loud receipt.
    unneeded = " (force was asked for and not needed)" if force else ""
    return _ancestry_gate(sha, ancestry, outcome=OUTCOME_BY_HAND, promoting=(
        f"promoting {CHANNEL} to {sha} BY HAND{unneeded} — {STATUS_CONTEXT} "
        f"green on that commit, strictly ahead. Mover: {actor}. Reason: "
        f"{stated or 'none given'}."
    ), creating=(
        f"creating {CHANNEL} at {sha} BY HAND{unneeded} — first proven "
        f"commit. Mover: {actor}. Reason: {stated or 'none given'}."
    ))


def evaluate(
    combined: dict | None,
    sha: str,
    *,
    hold: str | None = None,
    ancestry: str | None = None,
    conclusion: str | None = None,
    branch: str | None = None,
    manual: bool = False,
    force: bool = False,
    reason: str | None = None,
    actor: str | None = None,
    trunk: str | None = None,
    channel_head: str | None = None,
) -> Decision:
    """Return ``(promote, reason, outcome)``. The reason is operator-facing.

    Order is deliberate. The BRANCH comes first: a hold is a statement about
    the channel, and a PR-head run never approaches the channel — reporting it
    as held would be true of the channel and useless about the run. After that
    the hold, so a deliberately paused channel reads as paused rather than as
    broken, and everything below it fails closed.

    `conclusion` is the triggering harness run's own conclusion, when the
    caller knows it. It is read BEFORE the commit status because the two answer
    different questions: the status says whether this sha was ever proved (by
    any run), the conclusion says what THIS run did. A cancelled run must
    never promote on a stamp some earlier run left behind.

    `branch` and `conclusion` are both optional and both mean "nobody said"
    when absent — the stamp and the ancestry stay the authorities, which is
    what every caller before DRE-3070 relied on.

    `manual` switches to the by-hand route (DRE-4111) and is the ONLY way to
    reach it: `force`, `reason`, `actor` and `trunk` are dispatch-only facts
    and are ignored here, so a `workflow_run` carrying a stray `force` cannot
    promote an unproven commit.
    """
    if manual:
        return by_hand(
            combined, sha, hold=hold, ancestry=ancestry, force=force,
            reason=reason, actor=actor, trunk=trunk, channel_head=channel_head,
        )

    # 1. Was this run ever about the trunk? The PR trigger runs the same
    #    harness against a PR head, which proves a commit that is not on main.
    #    Skipping it was always right; saying nothing about it was not — on
    #    2026-09-03 four of these produced four bare `skipped` runs and it took
    #    reading all of them to learn nothing was wrong.
    if branch is not None and branch != TRUNK:
        return Decision(False, (
            f"not promoting {sha}: its harness run was on {branch!r}, not "
            f"{TRUNK}. A PR-head run proves a commit that is not on the trunk "
            f"— nothing is wrong and nothing is owed."
        ), OUTCOME_NOT_MAIN)

    # 2. The hold switch. Approved as a switch (D2), and the distinction is the
    #    whole lesson: a hold that is a switch is a control, a hold that is a
    #    habit is the July failure wearing a different hat. So it must be
    #    explicit and it must say who stopped it and why — a held channel stops
    #    advancing, and the console's channel-health monitor alarms on that
    #    (DRE-6050).
    if hold and hold.strip():
        return Decision(False, (
            f"channel HELD — not promoting {sha}. Reason on record: "
            f"{hold.strip()}. Clear the hold variable to resume."
        ), OUTCOME_HELD)

    # 3. Blocked by the SANDBOX (DRE-3076) — read before the triggering run's
    #    own conclusion, because it is a different fact and the more specific
    #    one: the harness never judged this commit, its own proving ground was
    #    down (a Linear rate limit, on 2026-09-03). That is true whether or not
    #    the run that hit it also reports a bare `failure` conclusion, and
    #    "blocked by the sandbox" must win that race — a plain "harness
    #    failed" sends someone looking at a diff nobody judged. Nothing is
    #    proven and nothing is disproven, so this is neither a promotion nor a
    #    defect.
    blocked = blocked_by_sandbox(combined)
    if blocked and blocked.startswith(RATE_LIMIT_MARKER):
        # GitHub's meter, not the commit (DRE-9068). On 2026-11-02 run
        # 41581663622 passed every code scenario and lost two to the worker
        # installation's empty hour, and this read as a red trunk.
        return Decision(False, (
            f"{INCONCLUSIVE_RATE_LIMITED} — not promoting {sha}: GitHub "
            f"refused the harness for a rate limit that outlasted its wait, "
            f"so it never finished judging this commit — {blocked}. Not a "
            f"red trunk: re-run the harness on {sha}, or let the next push "
            f"to {TRUNK} re-prove the trunk."
        ), OUTCOME_INCONCLUSIVE)
    if blocked and blocked.startswith(QUEUE_STALL_MARKER):
        # The narrower block (DRE-6147): GitHub's own queue, not the sandbox's
        # machinery. On 2026-10-07 this read as a red trunk and the channel
        # sat behind for an hour on a stall no code caused.
        return Decision(False, (
            f"not promoting {sha}: the harness was blocked by a GitHub QUEUE "
            f"STALL in the sandbox, not by this commit — {blocked}. Nothing "
            f"is proven either way; the next run re-proves this trunk."
        ), OUTCOME_QUEUE_STALL)
    if blocked:
        return Decision(False, (
            f"not promoting {sha}: the harness was BLOCKED BY THE SANDBOX, "
            f"not by this commit — {blocked}. Nothing is proven either way; "
            f"the next run re-proves this trunk."
        ), OUTCOME_BLOCKED)

    # 4. What the triggering run did (DRE-3070). Cancelled is the merge-train
    #    arm and it is NOT a failure: GitHub keeps one pending run per
    #    concurrency group, so a head still waiting when the next merge lands
    #    is dropped before it starts. The channel advances to the head that DID
    #    finish and then to the newest — but only if the skip says so, because
    #    a silent skip is indistinguishable from an abandoned channel.
    if conclusion is not None and conclusion != SUCCESS:
        if conclusion == CANCELLED:
            return Decision(False, (
                f"not promoting {sha}: its harness run was cancelled by a "
                f"newer push to main — a merge train. The run is queued "
                f"behind, not lost: {CHANNEL} advances to whichever head the "
                f"harness does finish proving."
            ), OUTCOME_CANCELLED)
        return Decision(False, (
            f"not promoting {sha}: the harness run failed (conclusion="
            f"{conclusion}). This is a red trunk, not a busy one."
        ), OUTCOME_FAILED)

    # 5. The harness must have proved THIS sha. Never promote on unverifiable
    #    data — the merge gate's compare-blip rule, and the reason a fetch
    #    failure is indistinguishable from no stamp here.
    verdict = _harness_verdict(combined)
    if verdict != SUCCESS:
        seen = verdict or "no stamp at all"
        return Decision(False, (
            f"not promoting {sha}: the {STATUS_CONTEXT} stamp reports "
            f"{seen}. Only a green harness run against this exact sha may "
            f"move {CHANNEL}."
        ), OUTCOME_UNPROVEN)

    # 6. The channel may only ever advance — the rail both routes share, in
    #    `_ancestry_gate` since DRE-4111.
    return _ancestry_gate(
        sha, ancestry, outcome=OUTCOME_PROMOTING,
        promoting=f"promoting {CHANNEL} to {sha} — harness green, strictly ahead.",
        creating=f"creating {CHANNEL} at {sha} — first proven commit.",
    )


def _clamp(text: str, limit: int) -> str:
    """`text` cut to `limit` characters, marked where it was cut."""
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _examples(entries: list[dict]) -> str:
    """At most `MIRROR_EXAMPLES` of a list, each clamped, then how many more."""
    shown = "; ".join(_clamp(mirror_check.describe([e]), MIRROR_EXAMPLE_CHARS)
                      for e in entries[:MIRROR_EXAMPLES])
    more = len(entries) - MIRROR_EXAMPLES
    return f"{shown} and {more} more" if more > 0 else shown


def mirror_summary(mirror: dict | None) -> str:
    """The mirror check's result in one line — for the receipt, the forced
    warning and the `mirror` output.

    Bounded at `MIRROR_SUMMARY_MAX_BYTES` (DRE-6683): both counts exactly, a
    handful of examples, and where the full lists are."""
    if not mirror:
        return f"{OUTCOME_MIRROR_BLOCKED}: the check did not run"
    status = mirror.get("status")
    tests = mirror.get("tests", 0)
    sha = (mirror.get("agent_bureau_sha") or "unknown")[:7]
    failing = mirror.get("failing") or []
    red = mirror.get("already_red") or []
    if status == mirror_check.MIRROR_PASSED:
        head = (f"passed — {tests} agent-bureau tests at {sha} green; "
                f"0 failing, {len(red)} already red on stable")
        examples = f": {_examples(red)}" if red else ""
    elif status == mirror_check.MIRROR_FAILED:
        head = (f"{OUTCOME_MIRROR_FAILED} — {len(failing)} failing, "
                f"{len(red)} already red on stable, agent-bureau at {sha}")
        examples = f"; failing: {_examples(failing)}"
        if red:
            examples += f"; already red: {_examples(red)}"
    else:
        why = _clamp(mirror.get("why") or "the check gave no answer",
                     MIRROR_EXAMPLE_CHARS * 2)
        return f"{OUTCOME_MIRROR_BLOCKED} — {why}"
    where = (f" — full lists in {MIRROR_RESULT_FILE}, this run's "
             f"`{MIRROR_ARTIFACT}` artifact" if failing or red else "")
    # Each example is clamped already; this is the backstop for a result no
    # one has imagined yet. It cuts the examples only — the counts lead and
    # the pointer ends, so neither is ever lost.
    room = MIRROR_SUMMARY_MAX_BYTES - len(f"{head}{where}".encode("utf-8"))
    raw = examples.encode("utf-8")
    if len(raw) > room:
        examples = raw[:max(room - 3, 0)].decode("utf-8", "ignore") + "…"
    return f"{head}{examples}{where}"


def with_mirror(
    decision: Decision,
    mirror: dict | None,
    *,
    sha: str,
    force: bool = False,
    actor: str | None = None,
    reason: str | None = None,
) -> Decision:
    """Weigh agent-bureau's mirror tests against a decision (DRE-6496).

    On 2026-10-09 the `operator-step` vocabulary reached `stable` and broke
    agent-bureau's CI three times in one day, because each of its mirrors is
    proven against bureau-pipeline at `stable` — after the promotion, by every
    agent-bureau pull request at once. So the check is part of the proof:

      * Only a decision that would MOVE the channel is weighed. Every refusal
        before it stands as it was, and the workflow never ran the check for
        one, so an absent result there is the ordinary case.
      * `mirror` is `scripts/mirror_check.py`'s result. A green one promotes
        under the outcome the proof gave, unchanged. A failed one refuses as
        `mirror-tests-failed`; one that could not run refuses as
        `mirror-check-blocked`, which proves nothing either way. ABSENT means
        the check did not run, which is legal only on a forced promote — on
        any other promoting route it is a blocked check.
      * `force` is the by-hand operator's force, and the caller passes it only
        on that route (`manual and force`). It overrides the mirror check
        exactly as it overrides a red harness stamp, under
        `by-hand-forced-promoting`, naming the result it overrode. It reaches
        nothing else: the hold, the trunk check, the ancestry rail and the
        bot refusal all answered before a decision got here.
    """
    if not decision.promote or decision.outcome not in PROMOTING_OUTCOMES:
        return decision
    summary = mirror_summary(mirror)
    status = (mirror or {}).get("status")
    if status == mirror_check.MIRROR_PASSED:
        return Decision(True, f"{decision.reason} Mirror check: {summary}.",
                        decision.outcome)
    if force:
        return Decision(True, (
            f"{decision.reason} FORCED past the agent-bureau mirror check: "
            f"{summary}. Mover: {actor}. Reason: {(reason or '').strip()}."
        ), OUTCOME_BY_HAND_FORCED)
    if status == mirror_check.MIRROR_FAILED:
        return Decision(False, (
            f"not promoting {sha}: agent-bureau's mirror tests pass on "
            f"{CHANNEL} and fail on this candidate — {summary}. agent-bureau's "
            f"mirror follows first; the next run on {TRUNK} (or an ordinary "
            f"by-hand promote) re-evaluates, and a person's force is the way "
            f"past it."
        ), OUTCOME_MIRROR_FAILED)
    return Decision(False, (
        f"not promoting {sha}: the agent-bureau mirror check could not run — "
        f"{summary}. Nothing is proven either way; this run fails so the hold "
        f"is seen, and a person's force is the way past it."
    ), OUTCOME_MIRROR_BLOCKED)


def with_unit_suite(
    decision: Decision,
    unit: dict | None,
    *,
    sha: str,
    force: bool = False,
    actor: str | None = None,
    reason: str | None = None,
) -> Decision:
    """Weigh the candidate's own `Pipeline Tests` run on `main` (DRE-6968).

    On 2026-10-11 `stable` moved three times onto commits whose own unit suite
    was red on `main`, or still running and about to be: the harness and the
    mirror tests were green, and nothing asked about the suite. Two pull
    requests that each passed alone failed together, which is exactly the
    case where "it merged, so its suite was green" stops being true.

      * `unit` is that run as `{"run_id", "status", "conclusion"}`. Only a
        decision that would MOVE the channel is weighed, as in `with_mirror`.
      * `completed` and `success` promotes, unchanged. Not `completed` refuses
        as `unit-suite-not-finished` — `stable` waits for it. `failure` or
        `timed_out` refuses as `unit-suite-red`. Anything else — no record,
        not an object, a cancelled run — refuses as `unit-suite-unproven`.
      * `force` is the by-hand operator's force, passed only on that route,
        and it overrides each refusal exactly as it overrides the mirror check.
    """
    if not decision.promote or decision.outcome not in PROMOTING_OUTCOMES:
        return decision
    record = unit if isinstance(unit, dict) else {}
    status = record.get("status")
    conclusion = record.get("conclusion")
    run = f"{UNIT_SUITE} run {record.get('run_id') or '(no id)'}"
    if status == "completed" and conclusion == SUCCESS:
        return decision
    if status and status != "completed":
        outcome, refusal = OUTCOME_UNIT_NOT_FINISHED, (
            f"not promoting {sha}: its own unit suite on {TRUNK}, {run}, has "
            f"not finished (status={status}). {CHANNEL} waits for it."
        )
        seen = f"{run} still {status}"
    elif status == "completed" and conclusion in UNIT_RED_CONCLUSIONS:
        outcome, refusal = OUTCOME_UNIT_RED, (
            f"not promoting {sha}: the commit's own unit suite failed on "
            f"{TRUNK} — {run} ended {conclusion}. A green harness proves a "
            f"handful of scenarios; the suite is red, so this commit is not "
            f"proved."
        )
        seen = f"{run} ended {conclusion}"
    else:
        outcome, refusal = OUTCOME_UNIT_UNPROVEN, (
            f"not promoting {sha}: its own unit suite on {TRUNK} proves "
            f"nothing either way — {run} reads status={status!r}, "
            f"conclusion={conclusion!r}. Failing closed."
        )
        seen = f"{run} reads status={status!r}, conclusion={conclusion!r}"
    if force:
        return Decision(True, (
            f"{decision.reason} FORCED past the commit's own unit suite "
            f"({outcome}: {seen}). Mover: {actor}. Reason: "
            f"{(reason or '').strip()}."
        ), OUTCOME_BY_HAND_FORCED)
    return Decision(False, refusal, outcome)


def proven(combined: dict | None) -> bool:
    """Did the harness prove this commit (DRE-9069)? A green
    `integration-harness` stamp whose description does not lead with
    `BLOCKED_MARKER`, and nothing else: pending, failure, error, a blocked
    stamp, no stamp and a read that failed are all passed over."""
    if not isinstance(combined, dict):
        return False
    return _harness_verdict(combined) == SUCCESS and not blocked_by_sandbox(combined)


def proving_run(combined: dict | None) -> str:
    """The harness run behind a proven stamp — its `target_url` — or `""`.
    What a look-back promotion names as its proof, never the starting run,
    which was about the refused commit."""
    if not proven(combined):
        return ""
    return str((_harness_stamp(combined) or {}).get("target_url") or "")


def looks_back(decision: Decision, *, manual: bool) -> bool:
    """Does this decision about the run's own commit look back (DRE-9069)?
    Only an automatic refusal at the harness proof does."""
    return (not manual and not decision.promote
            and decision.outcome in LOOK_BACK_OUTCOMES)


class LookBack(NamedTuple):
    """What the walk found: the run's own commit and its refusal word, the
    candidate (`""` when nothing proven was found), how many commits of the
    chain it read, and where it stopped."""

    own: str
    own_outcome: str
    candidate: str
    read: int
    stopped: str


def look_back(
    chain: list[str],
    read_status,
    *,
    own: str,
    own_outcome: str,
    channel_head: str | None,
    limit: int = LOOK_BACK_LIMIT,
) -> LookBack:
    """Walk `chain` — main's first-parent chain, newest first — for the newest
    proven commit (DRE-9069).

    It stops at `channel_head`, which it does not consider, and after `limit`
    commits; the first proven commit ends it. The run's own commit counts as
    read and is passed over without a status read: its own run was refused,
    and a canceled run never promotes on a stamp an earlier run left behind.
    A newer commit still running is passed over too — promoting an older
    proven one is safe, the channel only moves forward, and the newer one
    still promotes when its own run ends.
    """
    if not chain:
        return LookBack(own, own_outcome, "", 0, STOP_UNREAD)
    read = 0
    for sha in chain:
        if channel_head and sha == channel_head:
            return LookBack(own, own_outcome, "", read, STOP_CHANNEL)
        if read >= limit:
            return LookBack(own, own_outcome, "", read, STOP_LIMIT)
        read += 1
        if sha == own:
            continue
        try:
            combined = read_status(sha)
        except Exception:  # noqa: BLE001 — an unread stamp proves nothing
            combined = None
        if proven(combined):
            return LookBack(own, own_outcome, sha, read, STOP_FOUND)
    return LookBack(own, own_outcome, "", read,
                    STOP_LIMIT if read >= limit else STOP_END)


def look_back_note(look: LookBack) -> str:
    """The sentence a look-back adds to the decision line: B and its refusal
    word, A or that nothing was found, and how many commits were read."""
    count = f"{look.read} commit{'' if look.read == 1 else 's'} read"
    head = f"Looked back from {look.own}, refused as {look.own_outcome}"
    if look.stopped == STOP_UNREAD:
        return (f"{head}: {TRUNK}'s first-parent chain could not be read, so "
                f"whether an older commit is proven is unknown.")
    if look.candidate:
        return (f"{head}: {look.candidate} is the newest proven commit on "
                f"{TRUNK}'s first-parent chain — {count}.")
    where = {
        STOP_CHANNEL: f"the search stopped at {CHANNEL}",
        STOP_LIMIT: f"the search stopped at {LOOK_BACK_LIMIT}, its limit",
    }.get(look.stopped, f"{TRUNK}'s history ended")
    return (f"{head}: no proven commit was found on {TRUNK}'s first-parent "
            f"chain — {count}, {where}.")


def with_look_back(decision: Decision, look: LookBack | None) -> Decision:
    """The decision, its line carrying the look-back's sentence."""
    if look is None:
        return decision
    return Decision(decision.promote,
                    f"{decision.reason} {look_back_note(look)}",
                    decision.outcome)


def _read_look_back(path: str | None, *, sha: str) -> LookBack | None:
    """The look-back record, or None. A record is only about this decision
    when it names this sha as its candidate, or — nothing found — as the
    run's own commit; anything else is ignored, never trusted."""
    if not path:
        return None
    try:
        data = json.loads(open(path).read())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        look = LookBack(str(data["own"]), str(data["own_outcome"]),
                        str(data.get("candidate") or ""), int(data["read"]),
                        str(data["stopped"]))
    except (KeyError, TypeError, ValueError):
        return None
    if look.own_outcome not in LOOK_BACK_OUTCOMES:
        return None
    if look.candidate != sha and not (not look.candidate and look.own == sha):
        return None
    return look


def _gh_json(path: str) -> dict | None:
    """One `gh api` read, or None on any failure."""
    try:
        done = subprocess.run(["gh", "api", path], capture_output=True,
                              text=True, timeout=60, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    try:
        data = json.loads(done.stdout)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _read_status(repo: str, sha: str) -> dict | None:
    """A commit's combined status — the stamp the look-back weighs."""
    return _gh_json(f"repos/{repo}/commits/{sha}/status")


def _read_channel_head(repo: str) -> str:
    """Where `stable` stands, or `""` when it cannot be read."""
    data = _gh_json(f"repos/{repo}/git/ref/tags/{CHANNEL}") or {}
    return str((data.get("object") or {}).get("sha") or "")


def first_parent_chain(rev: str, limit: int, *, cwd: str | None = None) -> list[str]:
    """`rev` and its first parents, newest first, at most `limit` — so a
    commit reachable only through a merged branch's second parent is never
    on it. `[]` when git cannot answer."""
    try:
        done = subprocess.run(
            ["git", "rev-list", "--first-parent", f"--max-count={limit}", rev],
            cwd=cwd, capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.SubprocessError):
        return []
    if done.returncode != 0:
        return []
    return [line.strip() for line in done.stdout.splitlines() if line.strip()]


def _first_parent_chain(branch: str, limit: int) -> list[str]:
    """Main's first-parent chain as `origin` has it now. The checkout is
    shallow, so `limit` commits are fetched first — a depth of `limit` holds
    the first-parent chain that long. A fetch that fails leaves no
    `FETCH_HEAD`, and the chain is empty."""
    try:
        subprocess.run(["git", "fetch", "--quiet", "--no-tags",
                        f"--depth={limit}", "origin", branch],
                       capture_output=True, text=True, timeout=120, check=False)
    except (OSError, subprocess.SubprocessError):
        return []
    return first_parent_chain("FETCH_HEAD", limit)


def _read_unit_suite(path: str) -> dict | None:
    """The unit-suite record file, or None — a file that is absent or is not
    JSON is the unproven case, and `with_unit_suite` says so."""
    try:
        return json.loads(open(path).read())
    except (OSError, ValueError):
        return None


def _read_mirror(path: str | None) -> dict | None:
    """The mirror check's result file, or None — absent, unreadable and not a
    result are all "the check did not run"."""
    if not path:
        return None
    try:
        data = json.loads(open(path).read())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _parser(*, look_back_command: bool = False) -> argparse.ArgumentParser:
    """The decision's arguments. `look-back` takes the same ones, so it judges
    the run's own commit on exactly the records the proof is handed."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--statuses-file", required=True)
    parser.add_argument("--ancestry", default=None)
    parser.add_argument("--hold", default=None)
    parser.add_argument(
        "--conclusion", default=None,
        help="the triggering harness run's own conclusion (success / "
             "cancelled / failure / …). Absent means 'nobody said', and the "
             "commit status stays the only authority.",
    )
    parser.add_argument(
        "--branch", default=None,
        help="the branch the triggering harness run was on. Anything but "
             f"{TRUNK!r} is a PR-head run and no candidate; absent means "
             "'nobody said'.",
    )
    # The by-hand route (DRE-4111). None of these has any effect without
    # --manual, so a workflow_run can never reach the forced arm.
    parser.add_argument(
        "--manual", action="store_true",
        help="this is a workflow_dispatch — a person promoting by hand. The "
             "candidate's own commit status is the proof, from any run.",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="promote past a failed or absent harness status. Operator-only, "
             "requires --reason, and overrides the PROOF only — never the "
             "hold, the trunk check or the ancestry rail.",
    )
    parser.add_argument(
        "--reason", default=None,
        help="why the channel is being moved by hand. Required with --force.",
    )
    parser.add_argument(
        "--actor", default=None,
        help="the login that started the run — the mover, recorded on it.",
    )
    parser.add_argument(
        "--trunk", default=None,
        help="GitHub's compare status for base=<default branch>, "
             f"head=<candidate>: {BEHIND!r} or {IDENTICAL!r} mean the "
             "candidate is reachable from the trunk. Anything else fails "
             "closed.",
    )
    parser.add_argument(
        "--channel-head", default=None,
        help=f"the sha {CHANNEL} points at now — the last proven sha, which a "
             "refusal names so the operator does not have to go and look.",
    )
    parser.add_argument(
        "--woken-by", default=None,
        help=f"the name of the workflow whose run started this one (DRE-6993). "
             f"{UNIT_SUITE!r} means --conclusion is the unit suite's, so it is "
             "not read as the harness's: the harness proof is then the "
             "commit's own status, as on the by-hand route. Absent, empty or "
             "the harness's name, --conclusion is the harness run's.",
    )
    if look_back_command:
        parser.add_argument(
            "--repo", default=os.environ.get("GITHUB_REPOSITORY", ""),
            help="owner/name, whose statuses and channel the look-back reads.",
        )
        parser.add_argument(
            "--default-branch", default=TRUNK,
            help="the branch whose first-parent chain is walked.",
        )
        parser.add_argument(
            "--out", default=LOOK_BACK_FILE,
            help="where the look-back record is written, when it looked back.",
        )
        return parser
    parser.add_argument(
        "--look-back", default=None,
        help=f"the record `look-back` wrote ({LOOK_BACK_FILE}, DRE-9069). "
             "Ignored on a by-hand run and when it is not about --sha; read, "
             "the harness proof is the candidate's own status and the line "
             "says where the walk went.",
    )
    parser.add_argument(
        "--mirror-result", default=None,
        help="scripts/mirror_check.py's result file (DRE-6496). Given, the "
             "decision weighs agent-bureau's mirror tests, and a file that is "
             "absent means the check did not run. Not given, the decision is "
             "the proof alone — the workflow's first pass, which decides "
             "whether the check runs at all.",
    )
    parser.add_argument(
        "--unit-suite", default=None,
        help=f"a file holding the candidate's own {UNIT_SUITE!r} run on "
             f"{TRUNK} as {{run_id, status, conclusion}} (DRE-6968). Given, "
             "a promotion waits for that suite to finish green, and a file "
             "that is absent or not JSON proves nothing. Not given, the unit "
             "suite is not weighed.",
    )
    return parser


def _conclusion(args: argparse.Namespace) -> str | None:
    """The harness run's conclusion, when the harness started this run.

    A unit-suite start (DRE-6993): the conclusion it carries says nothing
    about the harness, and a red suite reported as `harness-failed` would
    send a reader to the wrong run. `with_unit_suite` weighs the suite."""
    if (args.woken_by or "").strip() == UNIT_SUITE:
        return None
    return args.conclusion or None


def _read_combined(path: str) -> dict:
    try:
        return json.loads(open(path).read() or "{}")
    except (OSError, ValueError):
        # Unreadable is the same as unverifiable.
        return {}


def _write_output(**values: str) -> None:
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as fh:
            for key, value in values.items():
                fh.write(f"{key}={value}\n")


def look_back_main(argv: list[str]) -> int:
    """`promote_channel.py look-back` (DRE-9069): judge the run's own commit
    as the proof would, and on a refusal at the harness proof walk main's
    first-parent chain for the newest proven commit.

    Writes `candidate=<sha>` (empty when there is none, or no look-back),
    `proving_run=<url>` — the harness run behind that candidate's stamp, which
    the decision record and the summary name in place of the starting run —
    and, when it looked back, the record the decision reads. Exit 0 always."""
    args = _parser(look_back_command=True).parse_args(argv)
    candidate = proof = ""
    if args.manual:
        print("no look-back: a by-hand dispatch never looks back.")
    else:
        own = evaluate(
            _read_combined(args.statuses_file), args.sha, hold=args.hold,
            conclusion=_conclusion(args), branch=(args.branch or None),
        )
        if not looks_back(own, manual=False):
            print(f"no look-back: {own.outcome} is not a refusal at the "
                  f"harness proof.")
        else:
            chain = _first_parent_chain(args.default_branch, LOOK_BACK_LIMIT)
            stamps: dict[str, dict | None] = {}

            def read(sha: str) -> dict | None:
                stamps[sha] = _read_status(args.repo, sha)
                return stamps[sha]

            look = look_back(
                chain, read,
                own=args.sha, own_outcome=own.outcome,
                channel_head=_read_channel_head(args.repo),
            )
            with open(args.out, "w") as fh:
                json.dump(look._asdict(), fh)
            candidate = look.candidate
            proof = proving_run(stamps.get(candidate)) if candidate else ""
            print(f"look-back: {look_back_note(look)}")
    _write_output(candidate=candidate, proving_run=proof)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["look-back"]:
        return look_back_main(argv[1:])
    args = _parser().parse_args(argv)
    conclusion = _conclusion(args)
    combined = _read_combined(args.statuses_file)
    # A look-back candidate (DRE-9069) is judged as a unit-suite start is: the
    # starting run's conclusion was about the run's own commit, never this
    # one, so the proof is this commit's own stamp. Never on a by-hand run.
    look = None if args.manual else _read_look_back(args.look_back, sha=args.sha)
    if look is not None and look.candidate:
        conclusion = None

    decision = evaluate(
        combined,
        args.sha,
        hold=args.hold,
        ancestry=args.ancestry,
        conclusion=conclusion,
        branch=(args.branch or None),
        manual=args.manual,
        force=args.force,
        reason=(args.reason or None),
        actor=(args.actor or None),
        trunk=(args.trunk or None),
        channel_head=(args.channel_head or None),
    )
    # The candidate's own unit suite (DRE-6968), weighed before the mirror
    # check: a red suite is answered first, and the mirror is never weighed
    # for a commit the suite already refused.
    if args.unit_suite is not None:
        decision = with_unit_suite(
            decision, _read_unit_suite(args.unit_suite), sha=args.sha,
            force=args.manual and args.force,
            actor=(args.actor or None), reason=(args.reason or None),
        )
    # The mirror check is weighed only where the proof would move the channel
    # — the same runs the workflow ran it for. Anywhere else there is nothing
    # to say about it, so the `mirror` output is left unwritten rather than
    # read as a check that was blocked.
    weighed = (args.mirror_result is not None and decision.promote
               and decision.outcome in PROMOTING_OUTCOMES)
    mirror = None
    if weighed:
        mirror = _read_mirror(args.mirror_result)
        decision = with_mirror(
            decision, mirror, sha=args.sha,
            force=args.manual and args.force,
            actor=(args.actor or None), reason=(args.reason or None),
        )
    # Last, so whichever gate answered, the line still names both commits.
    decision = with_look_back(decision, look)
    # The receipt: the token first so it can be grepped out of a run log, the
    # prose after it so a human never has to.
    print(f"{decision.outcome}: {decision.reason}")

    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as fh:
            fh.write(f"promote={'true' if decision.promote else 'false'}\n")
            # One line, always. `$GITHUB_OUTPUT` is a key=value file, so a
            # newline inside the value is a second KEY — and since DRE-3076 the
            # reason can quote a sandbox log, which is not ours to trust.
            fh.write(f"reason={' '.join(decision.reason.split())}\n")
            fh.write(f"outcome={decision.outcome}\n")
            if weighed:
                fh.write(f"mirror={' '.join(mirror_summary(mirror).split())}\n")
    # A refusal is ordinary, not a failure — the caller branches on `promote`.
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
