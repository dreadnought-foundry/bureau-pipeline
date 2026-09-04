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
  HARNESS_WORKER_APP_ID / HARNESS_WORKER_APP_PRIVATE_KEY
  HARNESS_CONSOLE_TOKEN optional — a token scoped to the CONSOLE's repository,
                        for the lane contract's console-parity clause. Absent:
                        that clause reports UNEVALUATED rather than passing.
  LINEAR_API_KEY        required by the lane_contract scenario — one read-only
                        GraphQL query for the board's workflow states.
  HARNESS_QA_APP_ID / HARNESS_QA_APP_PRIVATE_KEY
                        optional — App credentials so the driver can
                        RE-MINT its installation tokens mid-run: the
                        workflow's mint steps run once, tokens live one
                        hour, and a full run can outlast it (run
                        29795108949 401ed its late scenarios). Absent:
                        the initial tokens are static and a long run
                        will 401 past the hour.
  HARNESS_REPO          default --repo
  HARNESS_RUN_ID        default --run-id (else a local one is generated)
  HARNESS_VERDICT_TIMEOUT / HARNESS_MERGE_TIMEOUT / HARNESS_POLL_INTERVAL
                        seconds, optional overrides
  HARNESS_WAIT_DEADLINE_MINUTES
                        minutes, optional — how often a wait stops to ask
                        whether the SANDBOX is still alive (DRE-3076). The
                        deadline is not a shorter budget: a healthy-but-slow
                        sandbox keeps its full one. `0` switches the check off.

Exit 0 iff every selected scenario passed; 1 if one failed; 2 on a bad
invocation; `framework.BLOCKED_EXIT` (3) when the SANDBOX blocked the run —
nothing proven about the commit either way, and the next run re-proves.
"""

from __future__ import annotations

import argparse
import os
import sys

from harness import app_token, framework, sandbox_health
from harness.github_api import GitHub
from harness.scenarios import discover


def token_supplier(
    role: str,
    app_id: str,
    private_key_pem: str,
    repo: str,
    mint=app_token.mint_installation_token,
    log=print,
):
    """A re-mint callable for GitHub(token_supplier=...), or None when the
    App credentials are not in the env (local PAT runs keep their static
    token and the old behavior)."""
    if not app_id or not private_key_pem:
        return None

    def supply() -> str:
        log(f"re-minting the {role} App installation token (hourly TTL)")
        return mint(app_id, private_key_pem, repo)

    return supply


def wait_deadline_seconds(raw: str | None) -> float:
    """The per-wait sandbox-liveness deadline, in seconds, from the workflow's
    minutes-shaped input. Empty (a push/pull_request run, where the `inputs`
    context does not resolve) means the driver's own default; a value that is
    not a number means the same, loudly, rather than crashing the run."""
    text = (raw or "").strip()
    if not text:
        return framework.WAIT_DEADLINE_SECONDS
    try:
        return max(0.0, float(text) * 60.0)
    except ValueError:
        print(
            f"note: HARNESS_WAIT_DEADLINE_MINUTES={text!r} is not a number — "
            f"using the default "
            f"{framework.WAIT_DEADLINE_SECONDS / 60:.0f} minutes"
        )
        return framework.WAIT_DEADLINE_SECONDS


def write_blocked_receipt(cause: str, log=print) -> str:
    """Publish the block so the workflow's stamp step can carry it.

    `blocked_reason` becomes the `integration-harness` status description, and
    `promote_channel.evaluate` reads its marker to say *blocked by sandbox*
    instead of *harness failed*. The line is sanitised and clamped by
    `sandbox_health.receipt_line` — it is sandbox log text going into a
    `key=value` file.
    """
    line = sandbox_health.receipt_line(cause)
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as fh:
            fh.write("blocked=true\n")
            fh.write(f"blocked_reason={line}\n")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as fh:
            fh.write(f"### Harness blocked by the sandbox\n\n{cause}\n")
    log(f"::error::{line}")
    return line


def select_names(available: dict, wanted: list) -> list:
    """Which scenarios this invocation runs.

    Named scenarios run, whatever they cost. An EMPTY selection means "the
    default sweep", which deliberately excludes the agent scenarios
    (DRE-2490): each spends a real build-agent run, and harness.yml runs on
    every boundary PR with its check run holding the merge gate — five agent
    runs per PR would hold every merge in this repo for hours. They are opt-in
    by name through the same `scenarios` dispatch input.
    """
    if wanted:
        return list(wanted)
    return sorted(
        name
        for name, scenario in available.items()
        if not getattr(scenario, "requires_agent", False)
    )


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
    names = select_names(available, wanted)
    # Never a silent cap: say which scenarios the default sweep left out and
    # how to run them.
    skipped = sorted(set(available) - set(names))
    if skipped:
        print(
            f"note: opt-in agent scenarios not run: {skipped} — select them "
            "by name (--scenarios / the workflow's `scenarios` input); each "
            "spends a real build-agent run"
        )

    run_id = framework.validate_run_id(args.run_id)
    worker_supplier = token_supplier(
        "worker",
        os.environ.get("HARNESS_WORKER_APP_ID", ""),
        os.environ.get("HARNESS_WORKER_APP_PRIVATE_KEY", ""),
        args.repo,
    )
    gh = GitHub(token, token_supplier=worker_supplier)
    if not worker_supplier:
        print(
            "note: HARNESS_WORKER_APP_ID/_PRIVATE_KEY unset — no token "
            "re-mint; a run longer than an hour will 401"
        )
    qa_token = os.environ.get("HARNESS_QA_TOKEN")
    gh_qa = (
        GitHub(
            qa_token,
            token_supplier=token_supplier(
                "qa",
                os.environ.get("HARNESS_QA_APP_ID", ""),
                os.environ.get("HARNESS_QA_APP_PRIVATE_KEY", ""),
                args.repo,
            ),
        )
        if qa_token
        else gh
    )
    if not qa_token:
        print("note: HARNESS_QA_TOKEN unset — check-runs reads use the worker token")
    # Third client, for the lane contract's console-parity clause (DRE-2726).
    # The console lives in another repository and needs its own installation
    # token; harness.yml mints it best-effort, because a console the harness
    # cannot reach must not turn every boundary PR red. Absent, the clause
    # reports UNEVALUATED — which the contract escalates to a hard failure from
    # the phase it names, so this is a schedule, not a shrug.
    console_token = os.environ.get("HARNESS_CONSOLE_TOKEN")
    gh_console = GitHub(console_token) if console_token else None
    if not gh_console:
        print(
            "note: HARNESS_CONSOLE_TOKEN unset — the console's state lists "
            "cannot be read; the lane-contract clause reports UNEVALUATED"
        )
    # Is the sandbox alive? (DRE-3076) Asked only when a wait passes its
    # deadline. The qa client leads because it is the identity proven to read
    # the sandbox's run records; the worker client is the fallback, and a
    # listing neither can read leaves the sandbox UNKNOWN, never dead.
    deadline = wait_deadline_seconds(os.environ.get("HARNESS_WAIT_DEADLINE_MINUTES"))
    sandbox_probe = (
        sandbox_health.probe((gh_qa, gh), args.repo) if deadline > 0 else None
    )
    if deadline > 0:
        print(
            f"note: each wait checks the sandbox's own sweep/gate/linear-sync "
            f"runs every {deadline / 60:.0f} min; a failed one ends the run "
            f"with its cause quoted"
        )
    else:
        print(
            "note: HARNESS_WAIT_DEADLINE_MINUTES=0 — no sandbox-liveness "
            "check; a stuck wait runs its full budget"
        )
    print(f"harness run {run_id} on {args.repo}: scenarios {names}")

    results = []
    blocked = None
    for name in names:
        ctx = framework.HarnessContext(
            gh=gh,
            gh_qa=gh_qa,
            gh_console=gh_console,
            repo=args.repo,
            run_id=run_id,
            worker_login=os.environ.get("HARNESS_WORKER_LOGIN", ""),
            qa_login=qa_login,
            # The agent scenarios clone the sandbox as the worker bot; every
            # other scenario ignores this.
            worker_token=token,
            # Defaults live in framework.py, never a second literal here —
            # the env vars are the operator's override, not a second copy of
            # the budget (the one the critic's cap is pinned against).
            verdict_timeout=float(
                os.environ.get(
                    "HARNESS_VERDICT_TIMEOUT", framework.VERDICT_TIMEOUT_SECONDS
                )
            ),
            merge_timeout=float(
                os.environ.get(
                    "HARNESS_MERGE_TIMEOUT", framework.MERGE_TIMEOUT_SECONDS
                )
            ),
            poll_interval=float(
                os.environ.get(
                    "HARNESS_POLL_INTERVAL", framework.POLL_INTERVAL_SECONDS
                )
            ),
            wait_deadline=deadline,
            sandbox_probe=sandbox_probe,
        )
        result = framework.run_scenario(available[name], ctx)
        results.append(result)
        if result.blocked:
            # Stop. Every remaining scenario waits on the same dead sandbox,
            # and re-proving that one deadline at a time is how one blocked
            # run became three hours (2026-09-03).
            blocked = result.blocked
            remaining = names[names.index(name) + 1:]
            if remaining:
                print(
                    f"harness BLOCKED by the sandbox — not starting "
                    f"{remaining}; they would wait on the same failure"
                )
            break

    failed = [r for r in results if not r.ok]
    print("\n== harness summary ==")
    for r in results:
        if r.ok:
            status = "PASS"
        elif r.blocked:
            status = f"BLOCKED at {r.failed_phase}"
        else:
            status = f"FAIL at {r.failed_phase}"
        print(f"  {r.scenario}: {status}")
        for err in r.errors:
            print(f"    - {err}")
    if blocked:
        # Not a verdict on the commit: the sandbox never let us reach one.
        print(
            "\nharness BLOCKED BY SANDBOX — this commit is NOT proven and NOT "
            "disproven; the next run re-proves it."
        )
        write_blocked_receipt(blocked)
        return framework.BLOCKED_EXIT
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
