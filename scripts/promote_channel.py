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
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import sys

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


def evaluate(
    combined: dict | None,
    sha: str,
    *,
    hold: str | None = None,
    ancestry: str | None = None,
) -> tuple[bool, str]:
    """Return ``(promote, reason)``. The reason is operator-facing text.

    Order is deliberate: the hold is checked FIRST so that a deliberately
    paused channel reads as paused, never as broken. Everything after it fails
    closed.
    """
    # 1. The hold switch. Approved as a switch (D2), and the distinction is the
    #    whole lesson: a hold that is a switch is a control, a hold that is a
    #    habit is the July failure wearing a different hat. So it must be
    #    explicit and it must say who stopped it and why — DRE-2552 alarms if
    #    it persists.
    if hold and hold.strip():
        return False, (
            f"channel HELD — not promoting {sha}. Reason on record: "
            f"{hold.strip()}. Clear the hold variable to resume."
        )

    # 2. Blocked by the SANDBOX (DRE-3076) — read before the state, because it
    #    is a different fact. The harness never judged this commit: its own
    #    proving ground was down (a Linear rate limit, on 2026-09-03). Nothing
    #    is proven and nothing is disproven, so this is neither a promotion nor
    #    a defect, and the receipt must not send anyone looking at the diff.
    blocked = blocked_by_sandbox(combined)
    if blocked:
        return False, (
            f"not promoting {sha}: the harness was BLOCKED BY THE SANDBOX, "
            f"not by this commit — {blocked}. Nothing is proven either way; "
            f"the next run re-proves this trunk."
        )

    # 3. The harness must have proved THIS sha. Never promote on unverifiable
    #    data — the merge gate's compare-blip rule, and the reason a fetch
    #    failure is indistinguishable from no stamp here.
    verdict = _harness_verdict(combined)
    if verdict != "success":
        seen = verdict or "no stamp at all"
        return False, (
            f"not promoting {sha}: the {STATUS_CONTEXT} stamp reports "
            f"{seen}. Only a green harness run against this exact sha may "
            f"move {CHANNEL}."
        )

    # 4. The channel may only ever advance. Two harness runs can finish out of
    #    order; without this, the later-finishing older commit wins and the
    #    channel silently regresses — which would look exactly like a working
    #    channel while shipping older code.
    if ancestry == NO_CHANNEL_YET:
        return True, f"creating {CHANNEL} at {sha} — first proven commit."
    if ancestry == IDENTICAL:
        return False, f"{CHANNEL} is already at {sha} — nothing to do."
    if ancestry == BEHIND:
        return False, (
            f"refusing to move {CHANNEL} backwards to {sha}: it is behind the "
            f"current channel head. A late-finishing older run must not "
            f"regress the channel."
        )
    if ancestry != AHEAD:
        return False, (
            f"not promoting {sha}: could not establish that it is ahead of "
            f"{CHANNEL} (ancestry={ancestry!r}). Failing closed."
        )

    return True, f"promoting {CHANNEL} to {sha} — harness green, strictly ahead."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--statuses-file", required=True)
    parser.add_argument("--ancestry", default=None)
    parser.add_argument("--hold", default=None)
    args = parser.parse_args(argv)

    try:
        combined = json.loads(open(args.statuses_file).read() or "{}")
    except (OSError, ValueError):
        # Unreadable is the same as unverifiable.
        combined = {}

    ok, reason = evaluate(
        combined, args.sha, hold=args.hold, ancestry=args.ancestry
    )
    print(reason)

    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as fh:
            fh.write(f"promote={'true' if ok else 'false'}\n")
            # One line, always. `$GITHUB_OUTPUT` is a key=value file, so a
            # newline inside the value is a second KEY — and since DRE-3076 the
            # reason can quote a sandbox log, which is not ours to trust.
            fh.write(f"reason={' '.join(reason.split())}\n")
    # A refusal is ordinary, not a failure — the caller branches on `promote`.
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
