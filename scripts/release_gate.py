#!/usr/bin/env python3
"""The vN release gate's DECISION (DRE-2103): harness proves the tag.

The promotion contract is "agents author, human promotes, harness proves"
(ADR adr-bureau-pipeline-self-host + DRE-2103): the operator cuts a `vN`
tag only after a green integration-harness run against that exact sha.
release-gate.yml fires on every `v*` tag push, peels the tag to its
commit, fetches the combined commit status (GET
/repos/{repo}/commits/{sha}/status), and acts on this module's verdict —
green only when the harness's own stamp reports success on the tagged
sha; everything else is loudly RED.

Why a commit-status stamp, never the workflow-run listing (vendor
premortem): a workflow_dispatch run's recorded head_sha is the tip of the
ref the workflow FILE was dispatched on — while the `pipeline_ref` input
governs what was actually checked out and tested. The run record can
therefore claim main's tip while testing v1's code. harness.yml instead
stamps `git rev-parse HEAD` of its own checkout with the
`integration-harness` status context (success on green, failure on red;
GitHub keeps the LATEST status per context), and that stamp is the only
honest sha-binding this gate reads.

Fail-closed directions:
  * no stamp on the tagged sha        → RED (the pre-tag run never ran,
    or ran via the PR gate on a head sha that is not this merge commit)
  * failure / error / pending stamp   → RED (red proved a problem;
    pending proved nothing)
  * `{}` blip substitute for the payload → RED (never promote on
    unverifiable data)

The RED output always carries the exact remediation command, so a red
tag is self-explaining:

    gh workflow run harness.yml --repo <repo> -f pipeline_ref=<sha>

Contract with release-gate.yml:
  argv: --sha (the peeled tag commit, full 40-hex), --statuses-file (the
    raw REST payload of GET commits/{sha}/status; `{}` on a fetch blip),
    --context (default integration-harness).
  exit 0 = a green stamp binds the sha; exit 1 = red, do not promote;
  exit 2 = malformed input (fails the job loudly — never fail open).

Unit-tested in tests/test_release_gate.py; harness.yml's stamp step is
pinned to this context string in tests/test_harness_wiring.py.
"""

from __future__ import annotations

import argparse
import json
import re
import sys

# The shared contract string: harness.yml stamps it, this gate reads it.
STATUS_CONTEXT = "integration-harness"

_HEAD_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _remediation(sha: str) -> str:
    return (
        "run the harness against the candidate first:\n"
        "    gh workflow run harness.yml "
        "--repo dreadnought-foundry/bureau-pipeline "
        f"-f pipeline_ref={sha}\n"
        "then re-push the tag once the run is green (a green run stamps "
        f"a success '{STATUS_CONTEXT}' status on {sha})"
    )


def evaluate(combined, sha: str, context: str = STATUS_CONTEXT):
    """(ok, reason) for the combined-status payload of the tagged sha.
    GitHub's combined record lists the LATEST status per context, so one
    lookup suffices — no ordering logic here."""
    statuses = combined.get("statuses") if isinstance(combined, dict) else None
    stamp = next(
        (s for s in statuses or [] if s.get("context") == context), None
    )
    if stamp is None:
        return False, (
            f"no '{context}' status on {sha} — the harness never proved "
            f"this sha; {_remediation(sha)}"
        )
    state = stamp.get("state")
    if state == "success":
        return True, f"green '{context}' stamp on {sha} — the harness proved this sha"
    return False, (
        f"'{context}' status on {sha} is {state!r}, not success — "
        f"{_remediation(sha)}"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sha", required=True,
                        help="the peeled tag commit (full 40-hex)")
    parser.add_argument("--statuses-file", required=True,
                        help="raw REST payload of GET commits/{sha}/status "
                             "({} on a fetch blip — fail-closed)")
    parser.add_argument("--context", default=STATUS_CONTEXT,
                        help="the harness's status context")
    return parser


def _die(msg: str) -> int:
    print(f"release_gate: {msg}", file=sys.stderr)
    return 2


def main(argv=None) -> int:
    try:
        args = build_parser().parse_args(argv)
    except SystemExit:
        return 2

    if not _HEAD_SHA_RE.match(args.sha or ""):
        return _die(f"--sha must be a full 40-hex SHA, got {args.sha!r}")

    try:
        with open(args.statuses_file) as f:
            combined = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        return _die(f"cannot read combined status: {e}")
    if not isinstance(combined, dict):
        return _die("combined-status payload is not an object")

    ok, reason = evaluate(combined, args.sha, args.context)
    print(("PASS: " if ok else "FAIL: ") + reason)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
