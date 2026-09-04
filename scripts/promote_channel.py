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
OUTCOME_UNPROVEN = "no-harness-stamp"
OUTCOME_NOT_AHEAD = "not-ahead-of-channel"


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


def _harness_verdict(combined: dict | None) -> str | None:
    """The harness's own state for this sha, or None if it never said.

    A `{}` substitute (what the caller writes when the status fetch fails) and
    a genuinely absent stamp are the same answer: we do not know, so we do not
    promote.
    """
    for status in (combined or {}).get("statuses") or []:
        if status.get("context") == STATUS_CONTEXT:
            return status.get("state")
    return None


def evaluate(
    combined: dict | None,
    sha: str,
    *,
    hold: str | None = None,
    ancestry: str | None = None,
    conclusion: str | None = None,
    branch: str | None = None,
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
    """
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

    # 3. What the triggering run did (DRE-3070). Cancelled is the merge-train
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

    # 4. The harness must have proved THIS sha. Never promote on unverifiable
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

    # 5. The channel may only ever advance. Two harness runs can finish out of
    #    order; without this, the later-finishing older commit wins and the
    #    channel silently regresses — which would look exactly like a working
    #    channel while shipping older code.
    if ancestry == NO_CHANNEL_YET:
        return Decision(
            True, f"creating {CHANNEL} at {sha} — first proven commit.",
            OUTCOME_PROMOTING,
        )
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

    return Decision(
        True,
        f"promoting {CHANNEL} to {sha} — harness green, strictly ahead.",
        OUTCOME_PROMOTING,
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
    )
    # The receipt: the token first so it can be grepped out of a run log, the
    # prose after it so a human never has to.
    print(f"{decision.outcome}: {decision.reason}")

    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as fh:
            fh.write(f"promote={'true' if decision.promote else 'false'}\n")
            fh.write(f"reason={decision.reason}\n")
            fh.write(f"outcome={decision.outcome}\n")
    # A refusal is ordinary, not a failure — the caller branches on `promote`.
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
