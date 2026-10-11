#!/usr/bin/env python3
"""Record the GitHub calls a workflow job made on each pool login (DRE-6914, stdlib only).

The first pipeline half of epic DRE-6911. `github_quota_sample` keeps each
installation's running total and nothing says which run spent it, so this
module records, once per workflow job, two honest readings of each pool
login's hourly meter — one after the job's mints (`before`), one at its end
(`emit`) — writes them as one small JSON document and puts it in the
agent-log store over the run's own OIDC login. Shaped on `usage_reading.py`
(DRE-4338) for the document and `upload_agent_log.py` (DRE-4269) for the
upload, whose credential path it imports rather than copies.

WHAT THE READING IS, AND IS NOT. GitHub meters calls per INSTALLATION, not per
job. What a job can measure is two POINTS on its login's meter — `(sampled_at,
used, reset_at)` before and after — and the `rise` between them when both sit
in the same hourly window. That rise is the meter's climb while the job ran: it
includes every other caller on the same login in those minutes (a concurrent
job, a sweep, the console), so it is an upper bound on the job's own calls and
never a count of them. The field is named `rise`, the sharing is written into
each login's `note`, and the attribution — which job gets which part of a
shared minute, and what nothing recorded — is the console's attribution reader
(DRE-6915), which has every job's points and the sampler's five-minute points
for the same login. Two jobs on one login will each see a rise; they will not
each be credited with it.

WHICH WORKFLOWS ARE WIRED. The agent-log upload role trusts a job only when its
`job_workflow_ref` is one of the seven in agent-bureau's
`BUREAU_PIPELINE_AGENT_WORKFLOWS` (mirrored here as `UPLOAD_TRUSTED_REUSABLES`
in tests/test_self_stubs_grant_id_token.py), so these are the seven the wiring
cards place the two steps in: `agent-task.yml`, `agent-fix.yml`, `medic.yml`
(DRE-6916), `qa-review.yml`, `verify.yml` (DRE-6917), `plan.yml` and
`red-main-repair.yml` (DRE-6918). A reading emitted from any other workflow is
refused at upload, and says so on its line.

WHICH ARE NOT, AND WHY. The sweeps and gates that mint the same logins are not
wired by this epic, by decision. `reconcile.yml` (every 15 minutes, minting the
main bot, three pool probe tokens and a pool reader), `merge-gate.yml` and
`linear-sync.yml` (the qa-bot and the main bot) are refused by the upload role
on purpose: agent-bureau's infra/test/agent-log-stack.test.ts names them in
`SWEEP_WORKFLOWS` because those runs carry the console's read-door token, and
trusting them would let a read-door run reach AWS. `groomer.yml`,
`deliver-rescue.yml`, `release-train.yml`, `proof-task.yml`, `harness.yml`,
`hygiene.yml`, `nightly-watch.yml`, `promote-channel.yml`, `model-adoption.yml`
and `model-trial.yml` have no upload trust either. Extending the trust list is
a CDK change the operator deploys, and a route that needs no AWS from a sweep
is the next epic's to design. What they spend is not lost: the console's
attribution reader reports it as each login's `unattributed` remainder — the
meter's climb in the minutes no recorded job was on the login — beside the top
spender, so a ranking of the wired jobs is never presented as the whole hour.
A builder wiring a new workflow reads this paragraph first.

HOW THE POINTS ARE TAKEN. `GET /rate_limit` reports a different meter from the
one a call is billed against (DRE-4290), so each point comes off the
`x-ratelimit-*` headers of real counted calls, exactly as `dispatch_pool.py`
probes and agent-bureau's `monitors/github_quota_sampler.py` samples: each step
makes `PROBE_SAMPLES` (dispatch_pool's, 2) cheap calls —
`GET /installation/repositories?per_page=1` — on each token it is handed and
keeps every sample's `used`, `limit`, `reset` and `resource`, parsed by
`dispatch_pool.parse_probe` and its header helpers, never a second parser. THE
COST: 2 calls per login per step, 4 per login per job. A refused sample stops
that login's sampling, as the pool probe's does.

GitHub answers one token from two counters with different reset clocks
(DRE-4576), so `emit` pairs a before-sample with an after-sample carrying the
SAME `x-ratelimit-reset` (and resource). Within a pair the before point is the
latest before-sample on that counter and the after point the earliest, so
`rise = after.used - before.used - 1`: the after probe's own call is in
`after.used`, the before probe's calls are in `before.used`. Where both
counters pair, the busier one is the reading — `dispatch_pool.worst`'s rule.
When no pair shares a reset — the hour turned over mid-job, or the counters
never matched — `rise` is null, `window_turned` is true, and BOTH points are
still recorded with their own `reset_at`: a job that crosses the turnover is
two points in two windows, never a dropped reading.

ABSENT IS A READING; ZERO IS NOT. A job that minted no pool login writes no
document and prints `github-calls: no pool login minted in this job — nothing
recorded`. A login whose read failed is in `logins` with `before`/`after` null
and a `reason`, so the console records "unread", never 0.

THE TOKENS ARE READ FROM THE NAMED ENVIRONMENT VARIABLES, never from arguments:
the process table is readable on a shared runner. `--login` carries the app
slug, the installation id and the NAME of the variable holding its token. No
token value is written to the document, the before file, stdout or the key.

WHERE IT GOES. `$RUNNER_TEMP/github-calls-reading.json` (the `--out` path), and
one single-part `put-object` with `--if-none-match '*'` at
`agent-logs/<slug>/github-calls/<date>/<run-id>-<attempt>-<job>-<read-at-ms>.json`
— `<slug>` the config/repo-map.json slug of `$GITHUB_REPOSITORY`, `<date>` the
UTC day of `read_at` — through `upload_agent_log.oidc_put`. The agent-log role
already allows `s3:PutObject` under `agent-logs/<slug>/*`, so no grant changes.

THE WORKFLOW NAME. `workflow` is the REUSABLE's file name. Inside a called
workflow `$GITHUB_WORKFLOW_REF` names the CALLER's stub (release_train.py's
`caller_workflow_path` reads it for exactly that), so a wiring step passes its
own reusable's name as `--workflow agent-task.yml`; the basename of the path in
`$GITHUB_WORKFLOW_REF` is only the fallback when it does not.

THE TAGGED LINE, one per login, on stdout — the epic's proof reads it from the
job log, because a step summary is not readable by the proof identity:

    github-calls: workflow=<file> job=<job> login=<app> installation=<id> rise=<N|unknown> window=<after.reset_at> key=<the S3 key, or "not uploaded: <reason>">

`config/proof-reach.json` declares it under `log_lines`, held to `TAG` below.

Every path exits 0: a reading that could not be taken or uploaded is a
`github-calls: … not kept: <reason>` line, never a red job.

Usage — ONE `before` step after the job's first mint(s), listing every login
the job WORKS with, each installation once (pool probe tokens are not listed:
their whole spend is the one call `dispatch_pool.py select` makes); ONE `emit`
step, `always()` + `continue-on-error`, before `Keep the run's working log`,
listing the freshest token per installation:

    python3 github_calls_reading.py before --out <file> \\
        --login <app>:<installation_id>:<TOKEN_ENV_NAME> [--login …]
    python3 github_calls_reading.py emit --before <file> --out <file> \\
        --login … [--workflow <reusable>.yml] [--github-output "$GITHUB_OUTPUT"]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import dispatch_pool  # noqa: E402
import upload_agent_log  # noqa: E402

#: Opens every line this module prints; `config/proof-reach.json` holds the
#: `github-calls:` entry to it.
TAG = "github-calls"

#: The document's own version tag — the shape the console's sweep (DRE-6919) lands.
SCHEMA = "github-calls-reading.v1"

#: The `before` step's file, read back only by `emit` in the same job.
BEFORE_SCHEMA = "github-calls-before.v1"

#: GitHub's API, and the one cheap counted call each sample makes. Not
#: `/rate_limit`: that is a different meter (DRE-4290).
API_URL = dispatch_pool._API_URL
PROBE_PATH = "/installation/repositories?per_page=1"

#: Samples per login per step — the pool probe's own number, never restated.
PROBE_SAMPLES = dispatch_pool.PROBE_SAMPLES

#: Test hook, in the `BUREAU_FAKE_POOL_PROBES` shape: JSON map token variable
#: NAME -> one response (`{"status": 200, "x-ratelimit-used": "120", ...}`) for
#: every sample, a list of responses (sample i takes item i), or null. Each goes
#: through `parse_probe` exactly as a real response would.
FAKE_PROBES_ENV = "BUREAU_FAKE_GITHUB_CALLS_PROBES"

#: The key's directory under the repo's own `agent-logs/<slug>/`.
KEY_DIR = "github-calls"

NOTE = ("rise is the login's meter climb while this job ran, shared with every "
        "other caller on the login; the console attributes it")

NO_LOGIN = f"{TAG}: no pool login minted in this job — nothing recorded"

_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_APP_SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}")
_RESOURCE = re.compile(r"[A-Za-z_]{1,40}")
_UNSAFE_SEGMENT = re.compile(r"[^A-Za-z0-9_-]")


# --------------------------------------------------------------------------- #
# Logins                                                                       #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Login:
    app: str
    installation_id: int
    token_env: str


def parse_logins(specs) -> tuple[list[Login], list[str]]:
    """`(logins, problems)` from the `--login <app>:<id>:<TOKEN_ENV>` values.

    An empty installation id is a mint step that did not run — its outputs
    render as '' — and is no login at all. Each installation is kept once, the
    first time it is named. A malformed value is a problem, and the problem
    never quotes it: a value is not supposed to hold a token, but if a
    miswired step put one there, echoing it would print it.
    """
    logins: list[Login] = []
    problems: list[str] = []
    seen: set[int] = set()
    for spec in specs or []:
        parts = [p.strip() for p in str(spec).rsplit(":", 2)]
        if len(parts) != 3:
            problems.append("a --login is not <app>:<installation_id>:<TOKEN_ENV_NAME>")
            continue
        app, installation, token_env = parts
        if not installation:
            continue
        if not installation.isdigit() or int(installation) < 1:
            problems.append("a --login's installation id is not a positive number")
            continue
        if not _APP_SLUG.fullmatch(app):
            problems.append(f"the --login for installation {installation} names no app slug")
            continue
        if not _ENV_NAME.fullmatch(token_env):
            problems.append(f"the --login for installation {installation} names no "
                            f"token variable")
            continue
        if int(installation) in seen:
            continue
        seen.add(int(installation))
        logins.append(Login(app, int(installation), token_env))
    return logins, problems


# --------------------------------------------------------------------------- #
# Samples                                                                      #
# --------------------------------------------------------------------------- #

def _iso(at) -> str | None:
    """ISO-8601 UTC, seconds, trailing Z — from a datetime or epoch seconds."""
    if isinstance(at, bool):
        return None
    if isinstance(at, (int, float)):
        try:
            at = datetime.fromtimestamp(at, timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(at, datetime):
        return None
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sample(status: int, headers, sampled_at: str) -> dict:
    """One response's meter: `used`, `limit`, `reset`, `resource`, when it was
    read, and whether GitHub refused the call. `parse_probe` reads the reset,
    the remaining and the refusal; its own helpers read the rest, so there is
    one header parser. `used` falls back to limit − remaining where GitHub sent
    no `x-ratelimit-used`."""
    reading = dispatch_pool.parse_probe(status, headers)
    limit = dispatch_pool._int_or_none(dispatch_pool._header(headers, "x-ratelimit-limit"))
    used = dispatch_pool._int_or_none(dispatch_pool._header(headers, "x-ratelimit-used"))
    if used is None and limit is not None and reading.remaining is not None:
        used = limit - reading.remaining
    resource = dispatch_pool._header(headers, "x-ratelimit-resource")
    return {
        "used": used,
        "limit": limit,
        "reset": reading.reset,
        "resource": resource if resource and _RESOURCE.fullmatch(resource) else None,
        "sampled_at": sampled_at,
        "refused": reading.refused,
    }


def _readable(s: dict) -> bool:
    return isinstance(s.get("used"), int) and isinstance(s.get("reset"), int)


def _fetch_real(token: str):
    """`(status, headers)` for one counted call with `token`, or None when
    nothing came back. urllib raises on 4xx — the refusal's headers are read
    off the error, as `dispatch_pool._probe_real` does."""
    import urllib.error
    import urllib.request

    request = urllib.request.Request(
        f"{API_URL}{PROBE_PATH}",
        headers={"authorization": f"Bearer {token}",
                 "accept": "application/vnd.github+json",
                 "user-agent": "bureau-github-calls"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.headers
    except urllib.error.HTTPError as err:
        return err.code, err.headers or {}
    except Exception:
        return None


def _fake_table(env):
    raw = (env.get(FAKE_PROBES_ENV) or "").strip()
    if not raw:
        return None
    try:
        table = json.loads(raw)
    except ValueError:
        return {}
    return table if isinstance(table, dict) else {}


def _fake_response(table: dict, token_env: str, index: int):
    value = table.get(token_env)
    if isinstance(value, list):
        value = value[index] if index < len(value) else None
    if not isinstance(value, dict):
        return None
    status = value.get("status")
    return (status if isinstance(status, int) else 0,
            {k: v for k, v in value.items() if k != "status"})


def probe(login: Login, env, now) -> tuple[list[dict], str | None]:
    """`(samples, reason)`: the readable samples off `PROBE_SAMPLES` counted
    calls with the login's token, or none and the reason."""
    token = (env.get(login.token_env) or "").strip()
    if not token:
        return [], (f"the token variable {login.token_env} is empty, so the "
                    f"meter was not read")
    table = _fake_table(env)
    samples: list[dict] = []
    for index in range(PROBE_SAMPLES):
        response = (_fake_response(table, login.token_env, index) if table is not None
                    else _fetch_real(token))
        if response is None:
            continue
        status, headers = response
        taken = sample(status, headers, _iso(now()))
        if _readable(taken):
            samples.append(taken)
        if taken["refused"]:
            # Already refused — a second call buys nothing and deepens the
            # limit that refused the first (dispatch_pool's rule).
            break
    if not samples:
        return [], "no counted call answered with the x-ratelimit headers"
    return samples, None


# --------------------------------------------------------------------------- #
# The reading                                                                  #
# --------------------------------------------------------------------------- #

def _point(s: dict | None) -> dict | None:
    if s is None:
        return None
    return {"used": s["used"], "limit": s["limit"],
            "reset_at": _iso(s["reset"]), "sampled_at": s["sampled_at"]}


def pair(before: list[dict], after: list[dict]):
    """`(before_sample, after_sample)` on one counter, or None.

    A counter is a reset clock (and resource). On each counter both steps
    sampled, the before point is the latest before-sample and the after point
    the earliest after-sample; of several such counters the busier — the
    larger after.used — is the reading, as `dispatch_pool.worst` takes it.
    """
    candidates = []
    for reset in {s["reset"] for s in before} & {s["reset"] for s in after}:
        b = max((s for s in before if s["reset"] == reset), key=lambda s: s["used"])
        a = min((s for s in after if s["reset"] == reset), key=lambda s: s["used"])
        if b["resource"] and a["resource"] and b["resource"] != a["resource"]:
            continue
        candidates.append((b, a))
    if not candidates:
        return None
    return max(candidates, key=lambda p: (p[1]["used"], p[1]["reset"]))


def login_row(app: str, installation_id: int, before: list[dict], after: list[dict],
              before_reason: str | None, after_reason: str | None) -> dict:
    """One `logins` entry. Both points read: paired when they share a counter,
    else two points in two windows. Either unread: null, and a `reason`."""
    rise = None
    window_turned = False
    reasons = []
    if before and after:
        matched = pair(before, after)
        if matched:
            b, a = matched
            rise = a["used"] - b["used"] - 1
            if rise < 0:
                reasons.append("the meter read lower after the job than before "
                               "it on the same counter")
                rise = None
        else:
            b = max(before, key=lambda s: s["used"])
            a = max(after, key=lambda s: s["used"])
            window_turned = True
    else:
        b = max(before, key=lambda s: s["used"]) if before else None
        a = max(after, key=lambda s: s["used"]) if after else None
        if not before:
            reasons.append(f"before: {before_reason or 'not read'}")
        if not after:
            reasons.append(f"after: {after_reason or 'not read'}")
    resource = next((s["resource"] for s in (a, b) if s and s["resource"]), None)
    row = {
        "app": app,
        "installation_id": installation_id,
        "resource": resource,
        "before": _point(b),
        "after": _point(a),
        "rise": rise,
        "window_turned": window_turned,
        "note": NOTE,
    }
    if reasons:
        row["reason"] = "; ".join(reasons)
    return row


def _positive_int(value) -> int | None:
    text = str(value or "").strip()
    return int(text) if text.isdigit() and int(text) >= 1 else None


def workflow_from_ref(ref) -> str | None:
    """The basename of the path in `owner/repo/.github/workflows/x.yml@ref`."""
    path = str(ref or "").split("@", 1)[0]
    name = path.rsplit("/", 1)[-1]
    return name if name.endswith((".yml", ".yaml")) else None


def document(rows: list[dict], *, repo, workflow, job, run: dict, read_at: datetime) -> dict:
    epoch_ms = int(read_at.timestamp() * 1000)
    segment = _UNSAFE_SEGMENT.sub("-", job or "job")
    return {
        "schema": SCHEMA,
        "delivery_id": f"{run['id'] or 'unknown'}-{run['attempt'] or 'unknown'}-"
                       f"{segment}-{epoch_ms}",
        "repo": repo,
        "workflow": workflow,
        "job": job,
        "run": run,
        "read_at": _iso(read_at),
        "read_at_epoch_ms": epoch_ms,
        "logins": rows,
    }


def object_key(doc: dict, slug: str) -> str:
    """`agent-logs/<slug>/github-calls/<date>/<run-id>-<attempt>-<job>-<read-at-ms>.json`,
    from the document's own fields — `<date>` the UTC day of its `read_at`."""
    job = _UNSAFE_SEGMENT.sub("-", doc["job"] or "job")
    return (f"{upload_agent_log.KEY_PREFIX}{slug}/{KEY_DIR}/{doc['read_at'][:10]}/"
            f"{doc['run']['id']}-{doc['run']['attempt']}-{job}-"
            f"{doc['read_at_epoch_ms']}.json")


def upload(doc: dict, path: Path, env) -> str:
    """Put the document in the store and return its key; every refusal is a
    `upload_agent_log.Gap` naming why."""
    Gap = upload_agent_log.Gap
    repository = env.get("GITHUB_REPOSITORY") or ""
    slug = upload_agent_log.repo_slug(repository)
    if not slug:
        raise Gap(f"{repository or 'this repo'} is not in config/repo-map.json, "
                  f"so it has no upload role")
    if not doc["run"]["id"] or not doc["run"]["attempt"]:
        raise Gap("no GITHUB_RUN_ID / GITHUB_RUN_ATTEMPT — not a GitHub Actions run")
    token_url, token_request, aws = upload_agent_log.oidc_credential(env)
    key = object_key(doc, slug)
    upload_agent_log.oidc_put(
        body=path, key=key, slug=slug,
        session=f"github-calls-{doc['job']}-{doc['run']['id']}",
        runner_temp=Path(env.get("RUNNER_TEMP") or "/tmp"),
        token_url=token_url, token_request=token_request, aws=aws,
        content_type="application/json", stem="github-calls")
    return key


def log_line(doc: dict, row: dict, key: str | None, reason: str | None) -> str:
    rise = row["rise"] if isinstance(row["rise"], int) else "unknown"
    window = (row["after"] or {}).get("reset_at") or "unknown"
    where = key if key else f"not uploaded: {reason}"
    return (f"{TAG}: workflow={doc['workflow'] or 'unknown'} job={doc['job'] or 'unknown'} "
            f"login={row['app']} installation={row['installation_id']} "
            f"rise={rise} window={window} key={where}")


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _one_line(text: str) -> str:
    return " ".join(str(text).split())


def _write_json(path: str, doc: dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(doc, handle, indent=1)
        handle.write("\n")


def _logins_or_say_why(specs) -> list[Login]:
    logins, problems = parse_logins(specs)
    for problem in problems:
        print(f"{TAG}: login not kept: {problem}")
    if not logins:
        print(NO_LOGIN if not problems
              else f"{TAG}: reading not kept: no --login was well formed")
    return logins


def cmd_before(args, env, now) -> int:
    logins = _logins_or_say_why(args.login)
    if not logins:
        return 0
    rows = []
    for login in logins:
        samples, reason = probe(login, env, now)
        rows.append({"app": login.app, "installation_id": login.installation_id,
                     "samples": samples, "reason": reason})
    _write_json(args.out, {"schema": BEFORE_SCHEMA, "logins": rows})
    unread = sum(1 for row in rows if not row["samples"])
    print(f"{TAG}: before points taken on {len(rows)} login(s), {unread} unread: {args.out}")
    return 0


def _load_before(path: str) -> tuple[dict[int, dict], str | None]:
    """`({installation_id: row}, reason)` from the before step's file."""
    try:
        with open(path, encoding="utf-8") as handle:
            doc = json.load(handle)
    except OSError:
        return {}, "no before file — the before step did not run or wrote nothing"
    except ValueError:
        return {}, "the before file is not JSON"
    if not isinstance(doc, dict) or doc.get("schema") != BEFORE_SCHEMA:
        return {}, f"the before file is not {BEFORE_SCHEMA}"
    rows = {}
    for row in doc.get("logins") or []:
        if isinstance(row, dict) and isinstance(row.get("installation_id"), int):
            samples = [s for s in row.get("samples") or []
                       if isinstance(s, dict) and _readable(s)]
            rows.setdefault(row["installation_id"], {**row, "samples": samples})
    return rows, None


def cmd_emit(args, env, now) -> int:
    logins = _logins_or_say_why(args.login)
    if not logins:
        return 0
    before_rows, before_missing = _load_before(args.before)
    rows = []
    for login in logins:
        before = before_rows.pop(login.installation_id, None)
        after, after_reason = probe(login, env, now)
        rows.append(login_row(
            login.app, login.installation_id,
            before["samples"] if before else [], after,
            before.get("reason") if before else
            (before_missing or "the before step read no point for this installation"),
            after_reason))
    # A login the before step read and this step was handed no token for is
    # still a point — recorded with its after unread, never dropped.
    for installation_id, before in before_rows.items():
        rows.append(login_row(
            str(before.get("app") or "unknown"), installation_id,
            before["samples"], [], before.get("reason"),
            "the emit step was handed no token for this installation"))

    run = {"id": _positive_int(env.get("GITHUB_RUN_ID")),
           "attempt": _positive_int(env.get("GITHUB_RUN_ATTEMPT"))}
    doc = document(
        rows, repo=env.get("GITHUB_REPOSITORY") or None,
        workflow=args.workflow or workflow_from_ref(env.get("GITHUB_WORKFLOW_REF")),
        job=env.get("GITHUB_JOB") or None, run=run, read_at=now())
    _write_json(args.out, doc)

    key, reason = None, None
    try:
        key = upload(doc, Path(args.out), env)
    except upload_agent_log.Gap as gap:
        reason = _one_line(gap)
    except Exception as unexpected:  # noqa: BLE001 — never a red job
        reason = f"the upload itself failed ({type(unexpected).__name__})"
    for row in rows:
        print(log_line(doc, row, key, reason))
    if args.github_output:
        with open(args.github_output, "a", encoding="utf-8") as handle:
            handle.write(f"path={args.out}\n")
            handle.write(f"uploaded={'true' if key else 'false'}\n")
            if key:
                handle.write(f"key={key}\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="command", required=True)
    login_help = ("<app>:<installation_id>:<TOKEN_ENV_NAME> — the token is read "
                  "from the NAMED variable, never from an argument")
    b = sub.add_parser("before", help="take the before point on each login")
    b.add_argument("--out", required=True)
    b.add_argument("--login", action="append", default=[], help=login_help)
    b.set_defaults(func=cmd_before)
    e = sub.add_parser("emit", help="take the after point, write and upload the reading")
    e.add_argument("--before", required=True)
    e.add_argument("--out", required=True)
    e.add_argument("--login", action="append", default=[], help=login_help)
    e.add_argument("--workflow", default="",
                   help="the reusable's file name; else the basename of $GITHUB_WORKFLOW_REF")
    e.add_argument("--github-output", default="")
    e.set_defaults(func=cmd_emit)
    return parser


def main(argv=None, env=None, now=None) -> int:
    env = os.environ if env is None else env
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as stop:
        if stop.code not in (0, None):
            print(f"{TAG}: reading not kept: the step's arguments did not parse")
        return 0
    try:
        return args.func(args, env, now or _now)
    except Exception as unexpected:  # noqa: BLE001 — never a red job
        # The class only: the message could quote a path or a header value.
        print(f"{TAG}: reading not kept: {type(unexpected).__name__}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
