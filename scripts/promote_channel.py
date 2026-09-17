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

machine-readably, so `channel_watch.py` can count merge trains instead of
reporting the cause as unknown. Three more names cover the refusals that
already existed and were equally silent: `channel-held`, `no-harness-stamp`,
`not-ahead-of-channel`.

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
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import sys
from typing import NamedTuple

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

#: The receipt vocabulary (DRE-3070). Stable strings: the staleness alarm and
#: docs/self-hosting.md both name them, so they are constants here and nowhere
#: else. The first three are the card's three reasons; the rest are the
#: refusals that already existed and were equally unnamed. `docs/self-hosting.md`
#: carries the same table, pinned by a test.
OUTCOME_PROMOTING = "harness-passed-promoting"
OUTCOME_CANCELLED = "harness-cancelled-by-newer-push"
OUTCOME_FAILED = "harness-failed"
OUTCOME_NOT_MAIN = "harness-run-not-on-main"
OUTCOME_HELD = "channel-held"
OUTCOME_BLOCKED = "harness-blocked-by-sandbox"
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
    #    explicit and it must say who stopped it and why — DRE-2552 alarms if
    #    it persists.
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


def main(argv: list[str] | None = None) -> int:
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
    args = parser.parse_args(argv)

    try:
        combined = json.loads(open(args.statuses_file).read() or "{}")
    except (OSError, ValueError):
        # Unreadable is the same as unverifiable.
        combined = {}

    decision = evaluate(
        combined,
        args.sha,
        hold=args.hold,
        ancestry=args.ancestry,
        conclusion=(args.conclusion or None),
        branch=(args.branch or None),
        manual=args.manual,
        force=args.force,
        reason=(args.reason or None),
        actor=(args.actor or None),
        trunk=(args.trunk or None),
        channel_head=(args.channel_head or None),
    )
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
    # A refusal is ordinary, not a failure — the caller branches on `promote`.
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
