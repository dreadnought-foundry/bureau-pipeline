#!/usr/bin/env python3
"""Keep one agent run's working log: scrub it, then put it in our own store.

DRE-4269, epic DRE-4267 (stdlib only, like the scrub it calls). Every agent run
writes a transcript; the runs worth keeping are the ones that DIED — the turn
cap, a 429, a timeout — so the step that calls this runs `always()`.

    printf '%s' "$SECRETS_JSON" | python3 upload_agent_log.py --log-file "$EXEC_FILE"

THE ONE RULE ABOVE ALL OTHERS: this never fails the agent's own run. Every
path returns 0. A log-keeping step that turns a healthy build red is a step
somebody deletes, and then the epic has nothing. What it does instead is record
a GAP — one line saying which log was not kept and why — so "we have no log for
that run" is a fact we can read rather than a silence.

WHAT HAPPENS, in order, cheapest refusal first:

  1. the log file exists (a bounced card, a skipped job, or an agent step that
     never started has none — that is an ordinary quiet line, not a warning);
  2. `$GITHUB_REPOSITORY` is a repo the platform serves — `config/repo-map.json`,
     the same routing snapshot `validate_card.VALID_SLUGS` derives from — because
     the slug is both the upload role's name and the key prefix it may write;
  3. the runner will mint an OIDC token. NO TOKEN IS AN ORDINARY GAP: portico
     forbids `id-token: write` outright (its own
     `.github/scripts/assert-credential-free.sh`), and whether that changes is
     the CEO's decision, not this step's. A repo whose stub has not been given
     the grant yet (DRE-4348..4353) reads the same way;
  4. the AWS CLI is on PATH. The self-hosted Mac minis may not have it — also a
     gap, never a failure;
  5. **THE SCRUB RUNS, AND NOTHING IS UPLOADED UNLESS IT EXITS 0.** Several of
     the repos these runs happen in are public and a transcript is a record of
     everything a tool printed; `scrub_agent_log.py` is the one piece where a
     mistake leaks a credential, and its contract is that silence is never
     success. A refusal here is a WARNING gap: it should have worked;
  6. the scrubbed file is gzipped and its size checked against
     `MAX_UPLOAD_BYTES`. The store's write-once deny (`s3:if-none-match` Null)
     has no exemption for multipart or CopyObject, so anything the client would
     split is simply refused by the bucket. `aws s3api put-object` is
     single-part by construction, and this cap is the second half of that
     promise: a bigger object is recorded as a gap, never attempted;
  7. one `put-object` with `--if-none-match '*'`, under
     `agent-logs/<slug>/<date>/<run-id>-<attempt>-<job>.json.gz`.

THE LOG ITSELF IS NEVER PRINTED — not to stdout, not to stderr, not to an
Actions artifact. Only the scrub's own summary line (counts per secret NAME and
per shape, which carry no part of a value) and the key that was written.

HOW THE CREDENTIAL IS OBTAINED, and why it is not on a command line. The runner
mints the OIDC token; it is written to a 0600 file under `$RUNNER_TEMP` and the
AWS CLI is pointed at it with `AWS_WEB_IDENTITY_TOKEN_FILE`, so the CLI performs
`AssumeRoleWithWebIdentity` itself. A token passed as an argument would reach
the process table, which any process on a shared self-hosted runner can read —
the same reasoning that keeps the scrub's secrets on stdin. Any ambient AWS
credentials are removed from the CLI's environment, so a runner with leftover
keys cannot preempt the repo's own role.

Exit 0, always.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent

#: The routing snapshot every other reader uses (`validate_card.VALID_SLUGS` is
#: derived from it). The slug is the upload role's name AND its key prefix.
REPO_MAP_PATH = HERE.parent / "config" / "repo-map.json"

#: The scrub, beside this script. No upload without a successful run of it.
SCRUB = HERE / "scrub_agent_log.py"

#: Everything the store holds lives under this prefix (AGENT_LOG_PREFIX in
#: agent-bureau's infra/lib/agent-log-stack.ts).
KEY_PREFIX = "agent-logs/"

#: The agent-log bucket and the account its upload roles live in (DRE-4245,
#: AgentLogStack). Read from the deployed stack's outputs, overridable by env
#: for a test or a second account.
BUCKET = "agentlogstack-agentlogbucketffd20d5f-3yaboku3ceyv"
ACCOUNT = "442004016891"
REGION = "us-west-2"

#: STS, and only STS. The roles pin `:aud` to this, so a token minted for some
#: other service cannot be replayed.
AUDIENCE = "sts.amazonaws.com"

#: The size cap on the GZIPPED object, stated because the bucket's write-once
#: deny refuses anything but a single-part PutObject. 64 MiB is also the scrub's
#: own per-file input cap (`scrub_agent_log.DEFAULT_MAX_BYTES`), so one number
#: bounds both ends, and it is far below S3's 5 GiB single-PUT limit.
MAX_UPLOAD_BYTES = 64 * 1024 * 1024

#: Ambient credentials are removed before the CLI runs: the repo's own role, or
#: nothing.
AMBIENT_AWS = (
    "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
    "AWS_SECURITY_TOKEN", "AWS_PROFILE", "AWS_DEFAULT_PROFILE",
    "AWS_ROLE_ARN", "AWS_WEB_IDENTITY_TOKEN_FILE", "AWS_ROLE_SESSION_NAME",
)


def role_arn(slug: str, account: str = ACCOUNT) -> str:
    """The repo's own write-only upload role (DRE-4245)."""
    return f"arn:aws:iam::{account}:role/bureau-agent-log-upload-{slug}"


def object_key(slug: str, day: str, run_id: str, attempt: str, job: str) -> str:
    """`agent-logs/<slug>/<date>/<run-id>-<attempt>-<job>.json.gz`.

    One object per job per run attempt — which is what makes `If-None-Match`
    write-once a property rather than a nuisance: a re-run increments
    `GITHUB_RUN_ATTEMPT`, so it writes a new key rather than colliding.
    """
    return f"{KEY_PREFIX}{slug}/{day}/{run_id}-{attempt}-{job}.json.gz"


def repo_slug(repository: str, repo_map_path: Path = REPO_MAP_PATH):
    """The map's slug for `owner/name`, or None. Case-insensitive: GitHub's
    `$GITHUB_REPOSITORY` carries the owner's display case (`EveryBite/atlas`)."""
    try:
        repo_map = json.loads(Path(repo_map_path).read_text())
    except Exception:
        return None
    if not isinstance(repo_map, dict):
        return None
    wanted = repository.strip().lower()
    for slug, full in repo_map.items():
        if isinstance(full, str) and full.lower() == wanted:
            return slug
    return None


class Gap(Exception):
    """This run's log was not kept, and the message says why.

    `loud` separates the two kinds. An environment that cannot upload — no
    token, no CLI, no log because the job bounced — is expected and reported
    quietly; a scrub refusal, an oversize log or a refused put SHOULD have
    worked and gets a `::warning::`.
    """

    def __init__(self, reason: str, loud: bool = False):
        super().__init__(reason)
        self.loud = loud


def fetch_id_token(url: str, request_token: str) -> str:
    """The runner's OIDC token for the STS audience.

    The error BODY is read, never just the status (DRE-3221's rule, pinned by
    tests/test_linear_error_body.py): a bare `403` from this endpoint is
    unattributable, and the body is where the runner says which permission is
    missing. The body is GitHub's own message and carries no credential — the
    request token travels in the Authorization header, never in the URL.
    """
    joined = "&" if "?" in url else "?"
    request = urllib.request.Request(
        f"{url}{joined}audience={AUDIENCE}",
        headers={"Authorization": f"Bearer {request_token}",
                 "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode())
    except urllib.error.HTTPError as refused:
        body = refused.read().decode("utf-8", "replace").strip()[:200]
        raise Gap(f"the runner's OIDC endpoint answered {refused.code}: "
                  f"{body or 'no body'}", loud=True) from refused
    value = payload.get("value")
    if not isinstance(value, str) or not value:
        raise Gap("the runner's OIDC endpoint returned no token value", loud=True)
    return value


def run_scrub(log: Path, out_dir: Path, secrets: str, scrub: Path) -> Path:
    """The scrubbed copy of `log`, or a Gap. Nothing is uploaded without this."""
    if not Path(scrub).is_file():
        raise Gap(f"no scrub at {scrub} — this ref predates DRE-4268", loud=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [sys.executable, str(scrub), "--out-dir", str(out_dir), str(log)],
        input=secrets, capture_output=True, text=True)
    if result.returncode != 0:
        # The scrub's refusals name a file and a reason and never quote
        # content, so this is safe to repeat — and it is the only way anyone
        # learns why a log was dropped.
        raise Gap(
            "the scrub refused, so nothing was uploaded: "
            f"{result.stderr.strip() or f'exit {result.returncode}'}", loud=True)
    if result.stdout.strip():
        print(f"scrub: {result.stdout.strip()}")
    scrubbed = out_dir / Path(log).name
    if not scrubbed.is_file():
        raise Gap("the scrub exited 0 but wrote no file", loud=True)
    return scrubbed


def put_object(aws: str, body: Path, bucket: str, key: str, role: str,
               token_file: Path, region: str, session: str) -> None:
    """ONE single-part put, write-once. The CLI does the web-identity assume
    itself from the token file, so no credential is ever an argument."""
    env = {k: v for k, v in os.environ.items() if k not in AMBIENT_AWS}
    env.update({
        "AWS_ROLE_ARN": role,
        "AWS_WEB_IDENTITY_TOKEN_FILE": str(token_file),
        "AWS_ROLE_SESSION_NAME": session[:64],
        "AWS_REGION": region,
        "AWS_DEFAULT_REGION": region,
    })
    result = subprocess.run(
        [aws, "s3api", "put-object",
         "--bucket", bucket,
         "--key", key,
         "--body", str(body),
         "--if-none-match", "*",
         "--content-type", "application/gzip",
         "--output", "json"],
        capture_output=True, text=True, env=env)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise Gap(
            f"the store refused the put of {key}: "
            f"{detail[-1] if detail else f'exit {result.returncode}'}",
            # A key that already exists is the write-once rule working, not a
            # fault: a replayed job attempt has nothing new to record.
            loud="PreconditionFailed" not in (result.stderr or ""))
    print(f"agent log kept: s3://{bucket}/{key} ({body.stat().st_size} bytes gzipped)")


def _summary(line: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Scrub one agent working log and put it in the agent-log "
                    "store. Secret values are read from stdin as a JSON object, "
                    "exactly as scrub_agent_log.py takes them.")
    parser.add_argument(
        "--log-file", default="",
        help="The agent's execution record. Empty or missing is a recorded gap.")
    return parser


def upload(args, secrets: str) -> None:
    """Every refusal raises Gap; the caller turns that into a recorded line."""
    runner_temp = Path(os.environ.get("RUNNER_TEMP") or "/tmp")

    log = Path(args.log_file or (runner_temp / "claude-execution-output.json"))
    if not log.is_file():
        raise Gap(f"no agent log at {log} — no model step wrote one")

    repository = os.environ.get("GITHUB_REPOSITORY", "")
    slug = repo_slug(repository)
    if not slug:
        raise Gap(f"{repository or 'this repo'} is not in config/repo-map.json, "
                  f"so it has no upload role")

    token_url = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL", "")
    token_request = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "")
    if not token_url or not token_request:
        raise Gap("this job was given no OIDC token, so it cannot assume the "
                  "upload role. The CALLING STUB grants `id-token: write` "
                  "(DRE-4348); portico forbids it by policy and is expected "
                  "here")

    aws = shutil.which("aws")
    if not aws:
        raise Gap("no AWS CLI on this runner (the self-hosted minis may not "
                  "have one) — install it to keep this repo's agent logs")

    staging = runner_temp / "agent-log"
    scrubbed = run_scrub(log, staging, secrets, SCRUB)

    run_id = os.environ.get("GITHUB_RUN_ID", "0")
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "1")
    job = os.environ.get("GITHUB_JOB", "job")
    key = object_key(slug, datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                     run_id, attempt, job)

    body = staging / f"{run_id}-{attempt}-{job}.json.gz"
    with open(scrubbed, "rb") as raw, gzip.open(body, "wb") as packed:
        shutil.copyfileobj(raw, packed)

    cap = int(os.environ.get("BUREAU_AGENT_LOG_MAX_BYTES") or MAX_UPLOAD_BYTES)
    size = body.stat().st_size
    if size > cap:
        raise Gap(
            f"the gzipped log is {size} bytes, over the {cap}-byte cap. The "
            f"store refuses anything but a single-part put, so a log this big "
            f"is not uploaded", loud=True)

    token_file = runner_temp / "agent-log-oidc.jwt"
    try:
        # Created 0600 by the open itself, never written and then chmod'ed: on
        # a shared self-hosted runner the gap between the two is long enough to
        # read, and an existing file would keep its own mode through a truncate.
        token = fetch_id_token(token_url, token_request)
        token_file.unlink(missing_ok=True)
        with os.fdopen(os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                               0o600), "w") as handle:
            handle.write(token)
        put_object(
            aws=aws, body=body, bucket=os.environ.get("BUREAU_AGENT_LOG_BUCKET") or BUCKET,
            key=key,
            role=role_arn(slug, os.environ.get("BUREAU_AGENT_LOG_ACCOUNT") or ACCOUNT),
            token_file=token_file,
            region=os.environ.get("BUREAU_AGENT_LOG_REGION") or REGION,
            session=f"agent-log-{job}-{run_id}")
    finally:
        try:
            token_file.unlink()
        except OSError:
            pass


def main(argv=None, stdin=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        secrets = (sys.stdin if stdin is None else stdin).read()
    except Exception:  # a step that piped nothing at all
        secrets = ""
    try:
        upload(args, secrets)
    except Gap as gap:
        line = f"agent log NOT kept (gap): {gap}"
        if gap.loud:
            print(f"::warning title=Agent working log not kept::{gap}")
        print(line)
        _summary(line)
    except Exception as unexpected:  # noqa: BLE001 — see the module docstring
        # Never the run's problem. The class and message are printed; no part
        # of the log has been read into anything that reaches here.
        line = (f"agent log NOT kept (gap): the uploader itself failed "
                f"({type(unexpected).__name__}: {unexpected})")
        print(f"::warning title=Agent working log not kept::{line}")
        print(line)
        _summary(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
