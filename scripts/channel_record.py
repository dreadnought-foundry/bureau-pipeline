#!/usr/bin/env python3
"""promote-channel's two records of what it did to `stable` (DRE-5214).

The console's Train Yard is built from two kinds of GitHub deployment: the
release train's DECISION (`scripts/release_decision.py`, task
`release-train-decision`) and a `release` deployment each time a surface
actually moves. bureau-pipeline releases by moving its `stable` tag, and until
this card it posted neither — `promote-channel.yml` moved the tag 87 times in
a week while the deployments list stayed empty, so the pipeline was the one
train in the fleet nobody could see stuck.

This module writes both, and changes nothing about WHEN `stable` moves:

  * `decision` — every run that evaluates the channel records ONE train
    decision, through `release_decision.record` / `write`, under the surface
    `.github/bureau/release.json` already declares (`pipeline-channel`), phase
    `release`. The act is `release` (the tag moved), `held` (the candidate was
    not promoted — the reason names the gating run and its failing scenarios,
    the words the `## Did not promote` summary already writes) or `no-op`
    (`stable` already carries the candidate).
  * `release` — each time the tag moves, one `release` deployment plus one
    status, the contract the console's `record_releases.py` reads (Portico's,
    DRE-5099), plus `from_sha` / `to_sha`: a channel's version is not a tag
    the console can compare, so it gets the two shas instead.

BEST-EFFORT, THE TRAIN'S RULE. Both posts go through `release_decision.gh`, and
neither ever fails the run: a refused post is one printed line and exit 0. The
workflow runs both steps AFTER the tag move with `continue-on-error`, so not
even a crash here can change whether `stable` moved.

The strings `pipeline-channel` (surface and environment) and
`stable@<short sha>` (version) are the contract with the agent-bureau cards —
spelled once, here.

CLI:
    channel_record.py --repo R decision --promoted true|false --outcome O \\
        --reason TEXT --ancestry A --candidate SHA --from-sha SHA \\
        --gating-run URL --scenarios a,b
    channel_record.py --repo R release --from-sha SHA --to-sha SHA
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from typing import NamedTuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import agent_marker  # noqa: E402
import release_decision  # noqa: E402

#: Opens every line this module prints.
TAG = "channel-record"

CHANNEL = "stable"
SURFACE = "pipeline-channel"        # the release.json surface, and the train decision's
ENVIRONMENT = "pipeline-channel"    # the `release` deployment's environment
TASK = "release"                    # what the console's record_releases.py filters on
PHASE = "release"
SHORT = 7

#: GitHub's compare answers (base=stable, head=candidate) that mean `stable`
#: already carries the candidate — nothing is stuck, so the record is a no-op.
#: `promote_channel` refuses both as `not-ahead-of-channel`; anything else it
#: refuses (an unreadable compare, a diverged candidate) is a hold.
CARRIED = ("identical", "behind")

#: The `promote_channel` outcome for a merge train: a newer push displaced
#: the run, and the channel advances to the head the harness does finish.
MERGE_TRAIN = "harness-cancelled-by-newer-push"


class ChannelDecision(NamedTuple):
    """The attributes `release_decision.record` reads off a train Decision."""

    act: str
    code: str
    reason: str
    tag: str | None = None
    re_arm_at: None = None


def version(sha: str | None) -> str | None:
    """`stable@<short sha>`, or `None` when there is no sha to name."""
    sha = (sha or "").strip()
    return f"{CHANNEL}@{sha[:SHORT]}" if sha else None


def _scenarios(raw: str | None) -> str:
    names = [name.strip() for name in (raw or "").split(",") if name.strip()]
    names = [name for name in names if name != "none"]
    return ", ".join(names) or "none"


def decide(*, promoted: bool, outcome: str, reason: str, ancestry: str,
           candidate: str, from_sha: str, gating_run: str,
           scenarios: str) -> ChannelDecision:
    """What this run did to `stable`, as a train decision."""
    reason = " ".join((reason or "").split())
    if promoted:
        return ChannelDecision(
            "release", "released",
            f"moved {CHANNEL} from {from_sha or 'nowhere — first promotion'} "
            f"to {candidate}: {reason}",
            tag=version(candidate))
    if ancestry in CARRIED:
        return ChannelDecision("no-op", "channel-current", reason)
    code = "channel-advancing" if outcome == MERGE_TRAIN else "channel-blocked"
    run = gating_run or "none — this was a by-hand dispatch"
    return ChannelDecision(
        "held", code,
        f"did not promote {candidate or 'an unresolved candidate'} "
        f"({outcome or 'no outcome'}): {reason} — gating run: {run}; "
        f"failing scenarios: {_scenarios(scenarios)}")


def write_decision(decision: ChannelDecision, *, repo: str, candidate: str,
                   from_sha: str, env, now=None, out=print) -> str:
    """Record the decision through the train's own writer. Never raises."""
    try:
        record = release_decision.record(
            decision, repo=repo, surface=SURFACE, phase=PHASE,
            sha=candidate or "", head=candidate or "",
            deployed=version(from_sha), now=now or datetime.now(timezone.utc),
            env=env)
        return release_decision.write(record, repo=repo, out=out)
    except Exception as error:  # noqa: BLE001 — a record never fails a run
        return f"decision not recorded: {error}"


# --------------------------------------------------------------------------- #
# The `release` deployment                                                     #
# --------------------------------------------------------------------------- #


def release_body(*, from_sha: str, to_sha: str, env) -> dict:
    """`POST /repos/{repo}/deployments` — DRE-5099's contract, plus the shas.

    `auto_merge: false` and `required_contexts: []` or GitHub answers 409;
    both leave here as real JSON types because the body is posted on stdin.
    """
    return {
        "ref": to_sha,
        "environment": ENVIRONMENT,
        "task": TASK,
        "description": version(to_sha),
        "auto_merge": False,
        "required_contexts": [],
        "production_environment": True,
        "payload": {
            "run_id": str(env.get("GITHUB_RUN_ID") or ""),
            "surface": SURFACE,
            "version": version(to_sha),
            "from": version(from_sha),
            "from_sha": from_sha or None,
            "to_sha": to_sha,
        },
    }


def release_status_body(*, repo: str, env) -> dict:
    """`POST /repos/{repo}/deployments/{id}/statuses`."""
    env = dict(env, GITHUB_REPOSITORY=repo)
    return {
        "state": "success",
        "description": "live",
        "auto_inactive": False,
        "log_url": agent_marker.run_url(env) or "",
    }


def _refused(why, out) -> str:
    clause = f"release not recorded: {release_decision._why(why)}"
    out(f"{TAG}: {clause}")
    return clause


def write_release(*, repo: str, from_sha: str, to_sha: str, env,
                  out=print) -> str:
    """Post the deployment, then its status. Never raises, never retries."""
    try:
        post = release_decision.gh
        created = post(f"repos/{repo}/deployments",
                       release_body(from_sha=from_sha, to_sha=to_sha, env=env))
        identifier = created.get("id") if isinstance(created, dict) else None
        if not identifier:
            return _refused(f"GitHub's answer named no deployment ({created!r})", out)
        try:
            post(f"repos/{repo}/deployments/{identifier}/statuses",
                 release_status_body(repo=repo, env=env))
        except Exception as error:  # noqa: BLE001
            return _refused(f"deployment {identifier} was created and its "
                            f"status was not: {error}", out)
        clause = f"release recorded: deployment {identifier} ({version(to_sha)})"
        out(f"{TAG}: {clause}")
        return clause
    except Exception as error:  # noqa: BLE001 — a record never fails a run
        return _refused(error, out)


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", required=True)
    sub = parser.add_subparsers(dest="command", required=True)

    decision = sub.add_parser("decision", help="record this run's decision")
    decision.add_argument("--promoted", default="false")
    decision.add_argument("--outcome", default="")
    decision.add_argument("--reason", default="")
    decision.add_argument("--ancestry", default="")
    decision.add_argument("--candidate", default="")
    decision.add_argument("--from-sha", default="")
    decision.add_argument("--gating-run", default="")
    decision.add_argument("--scenarios", default="")

    release = sub.add_parser("release", help="record the tag move")
    release.add_argument("--from-sha", default="")
    release.add_argument("--to-sha", required=True)

    args = parser.parse_args(argv)
    try:
        if args.command == "decision":
            decided = decide(
                promoted=args.promoted == "true", outcome=args.outcome,
                reason=args.reason, ancestry=args.ancestry,
                candidate=args.candidate, from_sha=args.from_sha,
                gating_run=args.gating_run, scenarios=args.scenarios)
            clause = write_decision(
                decided, repo=args.repo, candidate=args.candidate,
                from_sha=args.from_sha, env=os.environ,
                out=lambda line: print(f"{TAG}: {line}"))
            print(f"{TAG}: [{SURFACE}] {decided.act} {decided.code} — {clause}")
        else:
            write_release(repo=args.repo, from_sha=args.from_sha,
                          to_sha=args.to_sha, env=os.environ)
    except Exception as error:  # noqa: BLE001 — never fails the promotion
        print(f"{TAG}: not recorded: {error}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
