"""CLI driver: run harness scenarios against the sandbox repo.

    HARNESS_WORKER_TOKEN=... HARNESS_QA_LOGIN=agent-bureau-qa-bot[bot] \
        PYTHONPATH=scripts python3 -m harness \
        --repo dreadnought-foundry/bureau-harness --scenarios bot_pr_flow

Env (harness.yml sets all of these):
  HARNESS_WORKER_TOKEN  required — worker-bot App token, sandbox-scoped
  HARNESS_QA_LOGIN      required — expected merger login (qa App slug)
  HARNESS_QA_TOKEN      optional — qa-bot App token, sandbox-scoped; the
                        proven reader for check-runs (merge-gate.yml's own
                        path — dependabot_flow's self-skip evidence).
                        Absent: those reads fall back to the worker token
                        and a permission refusal surfaces loudly.
  HARNESS_WORKER_LOGIN  informational — the authoring identity
  HARNESS_REPO          default --repo
  HARNESS_RUN_ID        default --run-id (else a local one is generated)
  HARNESS_VERDICT_TIMEOUT / HARNESS_MERGE_TIMEOUT / HARNESS_POLL_INTERVAL
                        seconds, optional overrides

Exit 0 iff every selected scenario passed.
"""

from __future__ import annotations

import argparse
import os
import sys

from harness import framework
from harness.github_api import GitHub
from harness.scenarios import discover


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="harness", description=__doc__)
    parser.add_argument(
        "--repo",
        default=os.environ.get("HARNESS_REPO", "dreadnought-foundry/bureau-harness"),
    )
    parser.add_argument(
        "--scenarios",
        default="",
        help="comma-separated scenario names; empty = all discovered",
    )
    parser.add_argument(
        "--run-id",
        default=os.environ.get("HARNESS_RUN_ID") or framework.new_run_id(),
    )
    args = parser.parse_args(argv)

    token = os.environ.get("HARNESS_WORKER_TOKEN")
    qa_login = os.environ.get("HARNESS_QA_LOGIN")
    if not token or not qa_login:
        print(
            "FATAL: HARNESS_WORKER_TOKEN and HARNESS_QA_LOGIN are required "
            "(harness.yml mints/derives both)",
            file=sys.stderr,
        )
        return 2

    available = discover()
    wanted = [s.strip() for s in args.scenarios.split(",") if s.strip()]
    unknown = sorted(set(wanted) - set(available))
    if unknown:
        print(
            f"FATAL: unknown scenario(s) {unknown}; available: "
            f"{sorted(available)}",
            file=sys.stderr,
        )
        return 2
    names = wanted or sorted(available)

    run_id = framework.validate_run_id(args.run_id)
    gh = GitHub(token)
    qa_token = os.environ.get("HARNESS_QA_TOKEN")
    gh_qa = GitHub(qa_token) if qa_token else gh
    if not qa_token:
        print("note: HARNESS_QA_TOKEN unset — check-runs reads use the worker token")
    print(f"harness run {run_id} on {args.repo}: scenarios {names}")

    results = []
    for name in names:
        ctx = framework.HarnessContext(
            gh=gh,
            gh_qa=gh_qa,
            repo=args.repo,
            run_id=run_id,
            worker_login=os.environ.get("HARNESS_WORKER_LOGIN", ""),
            qa_login=qa_login,
            verdict_timeout=float(os.environ.get("HARNESS_VERDICT_TIMEOUT", 1500)),
            merge_timeout=float(os.environ.get("HARNESS_MERGE_TIMEOUT", 1200)),
            poll_interval=float(os.environ.get("HARNESS_POLL_INTERVAL", 30)),
        )
        results.append(framework.run_scenario(available[name], ctx))

    failed = [r for r in results if not r.ok]
    print("\n== harness summary ==")
    for r in results:
        status = "PASS" if r.ok else f"FAIL at {r.failed_phase}"
        print(f"  {r.scenario}: {status}")
        for err in r.errors:
            print(f"    - {err}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
