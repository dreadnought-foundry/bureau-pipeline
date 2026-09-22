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
  HARNESS_READER_TOKEN  optional — a dispatch-pool App with headroom,
                        sandbox-scoped (DRE-4282: harness.yml probes every
                        configured slot with dispatch_pool.py — one real call
                        each on the sandbox, ranked on the response's
                        x-ratelimit-remaining header since DRE-4290 — and
                        mints from the one it picks). Every GET the
                        worker client would send goes out as this identity
                        instead; writes, and the clone/push credential, stay
                        the worker's. Absent: reads ride the worker on pool
                        slot 1, exactly as before, and the run says so.
  HARNESS_READER_APP_ID / HARNESS_READER_APP_PRIVATE_KEY
                        optional — the SELECTED App's credentials, so the
                        reader re-mints mid-run from its own key rather than
                        drifting back to the worker's.
  HARNESS_POOL_SLOT     the slot the reads rode on (`n` from the selector),
                        named in the reader's `github-spend:` line.
  HARNESS_POOL_HEADROOM optional — `headroom=` from the same selector run,
                        `1:4812,2:refused,3:unreadable,4:4990`: what the
                        probe read off each slot's meter. Orders the
                        reader's fallbacks roomiest-first when a refusal
                        moves it (DRE-4575). Absent: slot order.
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
  HARNESS_TESTED_SHA    the commit this run PROVES — the sha harness.yml
                        stamps `integration-harness` on. The
                        agent_task_parses rehearsal reports it beside the
                        commit the sandbox actually compiled (DRE-3486).
  HARNESS_REPO          default --repo
  HARNESS_RUN_ID        default --run-id (else a local one is generated)
  HARNESS_NAMESPACE     default --namespace: the slice of the sandbox this
                        run owns — `main` for a push to main and for a hand
                        dispatch, `pr<number>` for a pull request's proving
                        run, `local` off the CLI. Every branch and probe
                        file the run creates sits under it and the sweep
                        collects nothing else, so main's run and a PR's run
                        can share the sandbox instead of queueing (DRE-3075).
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
import re
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
    sleeper=None,
):
    """A re-mint callable for GitHub(token_supplier=...), or None when the
    App credentials are not in the env (local PAT runs keep their static
    token and the old behavior).

    `sleeper`, when given, is handed to the mint so the client it builds for
    its two JWT-authed calls waits on the CALLER's clock (DRE-4575) — a mint
    is run inside a rate-limit recovery and can be refused itself. None means
    the mint keeps its own default (`time.sleep`), which is also what keeps
    the call three positional arguments for every stand-in mint in the suite.
    """
    if not app_id or not private_key_pem:
        return None

    def supply() -> str:
        log(f"re-minting the {role} App installation token (hourly TTL)")
        if sleeper is None:
            return mint(app_id, private_key_pem, repo)
        return mint(app_id, private_key_pem, repo, sleeper=sleeper)

    return supply


_POOL_APP_ID_RE = re.compile(r"^HARNESS_POOL_APP_ID_([0-9]+)$")


def pool_headroom(raw: str | None) -> dict:
    """`{slot: remaining | None | "refused"}` from the selector's `headroom=`
    output — `1:4812,2:refused,3:unreadable,4:4990` (dispatch_pool.py,
    DRE-4575). An int is the `x-ratelimit-remaining` the probe read, None is
    a slot nothing could be read for, `"refused"` is one GitHub turned the
    probe itself away from.

    The probe read every slot's meter in this same job; without this the
    fallback order threw those readings away and walked slot numbers. A
    malformed or absent value is simply no readings — the order degrades to
    slot number, never a crash.
    """
    out: dict = {}
    for item in (raw or "").split(","):
        slot, _, reading = item.strip().partition(":")
        if not slot.strip().isdigit():
            continue
        reading = reading.strip()
        if reading == "refused":
            out[int(slot)] = "refused"
            continue
        try:
            out[int(slot)] = int(reading)
        except ValueError:
            out[int(slot)] = None
    return out


def pool_fallbacks(
    env, selected_slot: str, repo: str, mint=None, log=print, sleeper=None
) -> list:
    """`(name, supply)` pairs for every OTHER configured pool slot — the
    reader's next identities when GitHub refuses the one it is on (DRE-4575).

    ROOMIEST FIRST, SLOT 1 LAST. The order is the probe's own readings,
    handed down from the selector in `HARNESS_POOL_HEADROOM`: the slot with
    the most requests left is tried first, a slot nothing could be read for
    after those, and a slot the probe itself was refused last of all. Walking
    slot numbers instead — the first cut of this card — put selected-slot 4's
    first move onto slot 1, which is the WORKER App: the bucket the worker
    client's own writes are already charged to, and the one the probe had
    just ranked below the slot it chose. So slot 1 stays last however well it
    reads, and ties fall back to slot order.

    A slot is configured iff `HARNESS_POOL_APP_ID_<n>` AND its
    `HARNESS_POOL_APP_PRIVATE_KEY_<n>` are both set (harness.yml passes every
    pair; slot 1 is the worker App). Each supply mints sandbox-scoped through
    the same `token_supplier` the reader's own re-mint uses, so a slot moved
    to re-mints from its own key for the rest of the run. Empty without the
    pairs — a local run keeps the pre-DRE-4575 shape, and a refusal stands.

    Why the driver and not the workflow: the mint steps run once, before any
    scenario, and a refusal arrives mid-run — run 35664409350 was refused on
    every call from ~12 s after its first dispatch, four attempts running,
    each on the slot the probe had just read as healthy.
    """
    mint = mint or app_token.mint_installation_token
    slots: dict = {}
    for name, value in env.items():
        m = _POOL_APP_ID_RE.match(name)
        if not m or not (value or "").strip():
            continue
        n = int(m.group(1))
        key = (env.get(f"HARNESS_POOL_APP_PRIVATE_KEY_{n}") or "").strip()
        if key:
            slots[n] = (value.strip(), key)
    try:
        current = int(selected_slot)
    except (TypeError, ValueError):
        current = 1
    headroom = pool_headroom(env.get("HARNESS_POOL_HEADROOM"))

    def rank(n: int):
        # (worker App last, read before unread before refused, roomiest
        # first, then slot number). A slot the selector never mentioned is
        # "not read", which is not the same as "empty".
        reading = headroom.get(n)
        if isinstance(reading, int):
            return (n == 1, 0, -reading, n)
        return (n == 1, 2 if reading == "refused" else 1, 0, n)

    after = sorted((n for n in slots if n != current), key=rank)
    return [
        (
            f"pool slot {n}",
            token_supplier(
                f"reader (pool slot {n})", *slots[n], repo,
                mint=mint, log=log, sleeper=sleeper,
            ),
        )
        for n in after
    ]


def spend_lines(clients) -> list:
    """One `github-spend:` line per DISTINCT identity the run used (DRE-4132).

    `billed` is what the run cost that identity's hourly GitHub allowance;
    `free` is how many reads GitHub answered `304 Not Modified` and did not
    charge. Printed on every run, zero included, for the reason the sweep's
    `sweep-spend:` lines are: on 2026-09-17 the worker installation ran dry
    every hour and nobody could say what a harness run cost, because nothing
    had ever counted. The qa client falls back to the worker's when no qa
    token is minted — the same object, so it is reported once. The reader
    (DRE-4282) is its own identity and its own line, labelled with the pool
    slot it was minted from; absent, there is no line, and the worker's
    `billed` is the reads.

    A client that MOVED identity mid-run (DRE-4575) gets one line per slot,
    each with what that slot's own hour paid — `reader (pool slot 3) 1
    billed` then `reader (pool slot 4) 5 billed`. Merging them into one
    number would put two installations' billing behind one label, which is
    precisely the question this ledger exists to answer the next time a slot
    runs dry. A client that never moved reads exactly as it did before.
    """
    lines, seen = [], set()
    for role, client in clients:
        if client is None or id(client) in seen:
            continue
        seen.add(id(client))
        # Telemetry must never be what fails a run: a stand-in client with no
        # ledger is skipped, not crashed on.
        history = getattr(client, "spend_history", None)
        if callable(history):
            entries = history()
        else:
            ledger = getattr(client, "spend", None)
            if not callable(ledger):
                continue
            entries = [(getattr(client, "identity", None), ledger())]
        for identity, spend in entries:
            # The reader's identity is a pool slot and the role is `reader`,
            # so the label names both; every other client IS its role and
            # would only read as `worker (worker)`.
            label = role if not identity or identity == role else f"{role} ({identity})"
            lines.append(
                f"github-spend: {label} {spend['billed']} billed, "
                f"{spend['free']} free (304 Not Modified)"
            )
    return lines


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
    parser.add_argument(
        "--namespace",
        default=(
            os.environ.get("HARNESS_NAMESPACE") or framework.DEFAULT_NAMESPACE
        ),
        help="the sandbox slice this run owns (main / pr<number> / local)",
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

    # The namespace OPENS the run id, so every branch and probe file the
    # run creates is already inside the slice its sweep owns — nothing has
    # to remember to prefix anything (DRE-3075).
    namespace = framework.validate_namespace(args.namespace)
    run_id = framework.namespaced_run_id(namespace, args.run_id)
    worker_supplier = token_supplier(
        "worker",
        os.environ.get("HARNESS_WORKER_APP_ID", ""),
        os.environ.get("HARNESS_WORKER_APP_PRIVATE_KEY", ""),
        args.repo,
    )
    # The pool reader (DRE-4282): the worker client's GETs go out as the
    # dispatch-pool App harness.yml selected, on that App's own hour; the
    # worker's writes, and the credential the agent scenarios clone and push
    # with, are untouched. Its re-mint comes from the SELECTED App's key.
    reader_token = os.environ.get("HARNESS_READER_TOKEN")
    pool_slot = (os.environ.get("HARNESS_POOL_SLOT") or "").strip() or "1"
    # …and when GitHub refuses that slot mid-run, the reader moves to the
    # next configured one (DRE-4575) — every slot's pair comes from
    # harness.yml, in HARNESS_POOL_APP_ID_<n> / _PRIVATE_KEY_<n>.
    gh_reader = (
        GitHub(
            reader_token,
            identity=f"pool slot {pool_slot}",
            token_supplier=token_supplier(
                "reader",
                os.environ.get("HARNESS_READER_APP_ID", ""),
                os.environ.get("HARNESS_READER_APP_PRIVATE_KEY", ""),
                args.repo,
            ),
            fallback_suppliers=pool_fallbacks(os.environ, pool_slot, args.repo),
        )
        if reader_token
        else None
    )
    # The worker has no fallback identity — WHICH identity acts is the thing
    # under test — so a refusal there is waited out (github_api.RateLimited).
    gh = GitHub(token, identity="worker", token_supplier=worker_supplier, reader=gh_reader)
    if not worker_supplier:
        print(
            "note: HARNESS_WORKER_APP_ID/_PRIVATE_KEY unset — no token "
            "re-mint; a run longer than an hour will 401"
        )
    if not gh_reader:
        print(
            "note: HARNESS_READER_TOKEN unset — reads ride the worker identity "
            "(pool slot 1), the pre-DRE-4282 shape"
        )
    qa_token = os.environ.get("HARNESS_QA_TOKEN")
    gh_qa = (
        GitHub(
            qa_token,
            # Named, like every other client: the spend line labels a client
            # by its identity, and `qa` is what it has always read as.
            identity="qa",
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
        # The fallback is the worker CLIENT, and since DRE-4282 that client
        # delegates its GETs to the reader — so with a reader present these
        # reads go out as the pool App, not as the worker. harness.yml always
        # mints the qa token, so this is the local/degraded path only; name
        # the identity anyway, because github_api.GitHub's rule is that WHICH
        # identity acts is explicit and never inferred from the client.
        rider = "the reader (pool slot)" if gh_reader else "the worker token"
        print(f"note: HARNESS_QA_TOKEN unset — check-runs reads use {rider}")
    # Third client, for the lane contract's console-parity clause (DRE-2726).
    # The console lives in another repository and needs its own installation
    # token; harness.yml mints it best-effort, because a console the harness
    # cannot reach must not turn every boundary PR red. Absent, the clause
    # reports UNEVALUATED — which the contract escalates to a hard failure from
    # the phase it names, so this is a schedule, not a shrug.
    console_token = os.environ.get("HARNESS_CONSOLE_TOKEN")
    gh_console = GitHub(console_token, identity="console") if console_token else None
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
            f"with its cause quoted, and "
            f"{framework.IDLE_PROBE_LIMIT} consecutive checks finding the "
            f"sandbox has started nothing end the wait as "
            f"'the sandbox will not do this'"
        )
    else:
        print(
            "note: HARNESS_WAIT_DEADLINE_MINUTES=0 — no sandbox-liveness "
            "check; a stuck wait runs its full budget"
        )
    print(
        f"harness run {run_id} on {args.repo} [namespace {namespace}]: "
        f"scenarios {names}"
    )

    results = []
    blocked = None
    for name in names:
        ctx = framework.HarnessContext(
            gh=gh,
            gh_qa=gh_qa,
            gh_console=gh_console,
            repo=args.repo,
            run_id=run_id,
            namespace=namespace,
            worker_login=os.environ.get("HARNESS_WORKER_LOGIN", ""),
            qa_login=qa_login,
            # The agent scenarios clone the sandbox as the worker bot; every
            # other scenario ignores this.
            worker_token=token,
            # The commit this run proves (DRE-3486). harness.yml resolves it
            # from the ACTUAL checkout and stamps it; the agent_task_parses
            # rehearsal reports it beside the commit the sandbox compiled.
            tested_sha=os.environ.get("HARNESS_TESTED_SHA", "").strip(),
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
    # The reader's line names the slot its reads rode on (DRE-4282) — and one
    # line PER slot, with that slot's own billing, when a refusal moved it
    # mid-run (DRE-4575). With no reader the worker's line IS the reads, and
    # the note above said so.
    for line in spend_lines(
        [
            ("worker", gh),
            ("reader", gh_reader),
            ("qa", gh_qa),
            ("console", gh_console),
        ]
    ):
        print(line)
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
