"""Every agent workflow uploads its scrubbed working log (DRE-4269, epic DRE-4267).

The epic keeps every agent run's transcript for six months in our own storage,
and the runs that matter most are the ones that DIED — turn cap, 429, timeout.
So the step that does it runs `always()`, and it can never be allowed to fail
the agent's own run: a log-keeping step that turns a healthy build red is a step
someone deletes.

WHAT THIS FILE PINS, and why each half is here:

  * THE POPULATION IS DERIVED, never remembered. `check_death_receipts.model_jobs`
    already answers "which reusable workflow jobs run a model" from the files
    themselves (DRE-4340), so this guard asks it rather than keeping a second
    list that drifts. Every job it finds must be classified — either it uploads
    its working log, or it is named here with the reason it does not. Add a
    seventh agent workflow and this test fails until someone decides which.
  * THE SHAPE of the step, on each of the six: `always()`, `continue-on-error`,
    after the last model step, no `${{ }}` in the body (the 21,000-character
    expression ceiling, DRE-3484).
  * THE BEHAVIOUR of `scripts/upload_agent_log.py`, executed for real against
    stub `aws` and a stub OIDC endpoint: no upload without a successful scrub,
    one single-part `put-object` carrying `If-None-Match`, the key under the
    store's own prefix, nothing of the log on stdout or stderr, and exit 0 on
    every gap — no OIDC token (portico), no AWS CLI (the self-hosted minis), a
    log over the cap.

ON `id-token: write`, WHICH THIS FILE DELIBERATELY DOES NOT ASSERT INSIDE THE
REUSABLE WORKFLOWS. A called job that asks for more permission than its caller
granted fails the WHOLE run at startup — release-train.yml says so in its own
header comment, and it is why that workflow declares no job-level `permissions:`
block either. portico forbids `id-token: write` outright
(`.github/scripts/assert-credential-free.sh`), so a `permissions:` block here
would not give the upload a token: it would take portico's agent runs away
entirely. The grant belongs in the CALLING STUB (DRE-4348 scaffold + agent-bureau,
DRE-4349..4353 per repo), and the reusable inherits it. What is asserted here is
the half that lives in this repo: no uploading job declares a `permissions:`
block, and the uploader ASKS the runner for a token with the `sts.amazonaws.com`
audience and records a gap when there is none.
"""

from __future__ import annotations

import gzip
import json
import os
import subprocess
import sys
import threading
import unittest
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import yaml

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
WORKFLOWS = REPO / ".github" / "workflows"
UPLOADER = SCRIPTS / "upload_agent_log.py"

sys.path.insert(0, str(SCRIPTS))
import check_death_receipts as receipts  # noqa: E402

#: The six reusable workflow jobs that run an agent for a card and so keep a
#: working log. The same six as `BUREAU_PIPELINE_AGENT_WORKFLOWS` in
#: agent-bureau's `infra/lib/agent-log-stack.ts` — the upload roles trust
#: exactly these `job_workflow_ref`s (DRE-4343), so a workflow added here and
#: not there is refused at AssumeRoleWithWebIdentity and shows up only as gaps.
UPLOADS = {
    ("agent-task.yml", "execute"),
    ("agent-fix.yml", "fix"),
    ("qa-review.yml", "review"),
    ("verify.yml", "verify"),
    ("plan.yml", "plan"),
    ("medic.yml", "diagnose"),
}

#: Jobs that run a model and deliberately keep no working log, each with the
#: reason. Kept as data so the discovery above can be exhaustive.
NOT_UPLOADING = {
    # The epic names the six agent runs (engineer, fix, critic, verifier,
    # planner, medic). These two also run the vendor action and are left out on
    # purpose — the agent-log roles do not trust them, and nobody has reviewed
    # them for holding one (agent-log-stack.ts, DRE-4343).
    ("red-main-repair.yml", "repair"),
    ("model-trial.yml", "trial"),
    # The groomer's judged read is a model call through `planning_classify`,
    # not an agent run: there is no transcript, which is why its own death
    # receipt records `unknown` with the reason (groomer.yml).
    ("groomer.yml", "groom"),
}

#: What the step's `run:` body must call. Matched on the call, not on a step
#: name — names are prose, this is the contract.
UPLOAD_CALL = "upload_agent_log.py"

RUN_ID = "987654321"
RUN_ATTEMPT = "2"
JOB = "execute"
SLUG = "atlas"
REPOSITORY = "EveryBite/atlas"


def _model_jobs():
    return receipts.model_jobs(WORKFLOWS)


def _uploading_jobs():
    return [mj for mj in _model_jobs() if (mj.filename, mj.job) in UPLOADS]


def _upload_steps(mj):
    return [s for s in mj.steps if UPLOAD_CALL in str(s.get("run") or "")]


class DiscoveryTest(unittest.TestCase):
    def test_every_job_that_runs_a_model_is_classified(self):
        """A seventh agent workflow must not slip in unlogged and unnoticed."""
        found = {(mj.filename, mj.job) for mj in _model_jobs()}
        self.assertEqual(
            found, UPLOADS | NOT_UPLOADING,
            "the set of jobs that run a model has changed. Every one either "
            "uploads its working log (UPLOADS, and agent-bureau's "
            "BUREAU_PIPELINE_AGENT_WORKFLOWS must trust it) or is named in "
            "NOT_UPLOADING with the reason it does not.",
        )

    def test_the_discovery_is_not_vacuous(self):
        self.assertEqual(len(_uploading_jobs()), len(UPLOADS))


class StepShapeTest(unittest.TestCase):
    def test_every_uploading_job_has_exactly_one_upload_step(self):
        for mj in _uploading_jobs():
            with self.subTest(workflow=mj.filename, job=mj.job):
                steps = _upload_steps(mj)
                self.assertEqual(
                    len(steps), 1,
                    f"{mj.filename} [{mj.job}] must carry exactly one step "
                    f"running `{UPLOAD_CALL}`; found {len(steps)}",
                )

    def test_the_step_runs_however_the_run_ended(self):
        for mj in _uploading_jobs():
            with self.subTest(workflow=mj.filename, job=mj.job):
                step = _upload_steps(mj)[0]
                self.assertIn(
                    "always()", str(step.get("if") or ""),
                    f"{mj.filename} [{mj.job}]: the upload step needs "
                    f"`always()` — the killed runs are the ones worth keeping",
                )

    def test_the_step_can_never_fail_the_agents_own_run(self):
        for mj in _uploading_jobs():
            with self.subTest(workflow=mj.filename, job=mj.job):
                step = _upload_steps(mj)[0]
                self.assertIs(
                    step.get("continue-on-error"), True,
                    f"{mj.filename} [{mj.job}]: the upload step is missing "
                    f"`continue-on-error: true` — keeping a log must never "
                    f"change the job's own conclusion",
                )

    def test_the_step_comes_after_the_last_model_step(self):
        for mj in _uploading_jobs():
            with self.subTest(workflow=mj.filename, job=mj.job):
                index = [i for i, s in enumerate(mj.steps)
                         if UPLOAD_CALL in str(s.get("run") or "")][0]
                self.assertGreater(
                    index, mj.last_model_step,
                    f"{mj.filename} [{mj.job}]: the upload runs before the "
                    f"last model step, so it would keep a transcript the run "
                    f"had not finished writing",
                )

    def test_the_step_interpolates_nothing_inside_its_run_body(self):
        """DRE-3484: a `run:` holding `${{ }}` compiles to one expression with
        a 21,000-character ceiling, and GitHub refuses the file at DISPATCH."""
        for mj in _uploading_jobs():
            with self.subTest(workflow=mj.filename, job=mj.job):
                step = _upload_steps(mj)[0]
                self.assertNotIn("${{", str(step.get("run") or ""))

    def test_the_step_is_handed_the_jobs_execution_file(self):
        for mj in _uploading_jobs():
            with self.subTest(workflow=mj.filename, job=mj.job):
                env = _upload_steps(mj)[0].get("env") or {}
                self.assertIn(
                    "CLAUDE_EXECUTION_FILE", env,
                    f"{mj.filename} [{mj.job}]: the upload step must be handed "
                    f"the same execution file its death-cause receipt reads",
                )

    def test_no_uploading_job_declares_a_permissions_block(self):
        """A called job asking for more than its caller granted fails the whole
        run at startup (release-train.yml). The stub grants `id-token: write`
        (DRE-4348/4353); the reusable inherits it and must not re-declare it."""
        for path in sorted(WORKFLOWS.glob("*.yml")):
            doc = yaml.safe_load(path.read_text())
            for name, job in (doc.get("jobs") or {}).items():
                if (path.name, str(name)) not in UPLOADS:
                    continue
                with self.subTest(workflow=path.name, job=name):
                    self.assertIsNone(
                        job.get("permissions"),
                        f"{path.name} [{name}] declares a job-level "
                        f"`permissions:` block — a called job may never ask "
                        f"for more than its caller granted, and portico grants "
                        f"no `id-token: write` at all",
                    )


class NeverAnArtifactTest(unittest.TestCase):
    def test_no_workflow_uploads_the_working_log_as_an_actions_artifact(self):
        """Several consumer repos are public. The log goes to our own store or
        nowhere — never to an artifact anyone with repo read can download."""
        for path in sorted(WORKFLOWS.glob("*.yml")):
            doc = yaml.safe_load(path.read_text())
            for name, job in (doc.get("jobs") or {}).items():
                if not isinstance(job, dict):
                    continue
                for step in job.get("steps") or []:
                    if not isinstance(step, dict):
                        continue
                    if not str(step.get("uses") or "").startswith(
                            "actions/upload-artifact"):
                        continue
                    with_ = step.get("with") or {}
                    self.assertNotIn(
                        "agent-log", str(with_.get("path") or ""),
                        f"{path.name} [{name}]: an artifact upload names the "
                        f"agent-log directory — the working log must never "
                        f"leave the runner as an Actions artifact",
                    )


class _TokenHandler(BaseHTTPRequestHandler):
    """The runner's OIDC endpoint, as `ACTIONS_ID_TOKEN_REQUEST_URL` serves it."""

    token = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJyZXBvIn0.c2lnbmF0dXJl"
    seen: list = []

    def do_GET(self):  # noqa: N802 (BaseHTTPRequestHandler's spelling)
        query = parse_qs(urlparse(self.path).query)
        _TokenHandler.seen.append(
            {"audience": (query.get("audience") or [""])[0],
             "authorization": self.headers.get("Authorization", "")})
        body = json.dumps({"value": self.token, "count": 1}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep the test output clean
        pass


class UploaderBehaviourTest(unittest.TestCase):
    """The script, EXECUTED — against a stub `aws` and a real HTTP endpoint.

    Nothing here greps the source: "no upload without a successful scrub" means
    the stub `aws` really recorded no invocation.
    """

    SECRET = "ghp_" + "A" * 36

    def setUp(self):
        import tempfile

        self.tmp = Path(tempfile.mkdtemp(prefix="dre4269-"))
        self.addCleanup(__import__("shutil").rmtree, self.tmp, True)

        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.aws_calls = self.tmp / "aws-calls.txt"
        self._write_aws(exit_code=0)

        self.log = self.tmp / "claude-execution-output.json"
        self.log.write_text(json.dumps(
            [{"type": "system", "token": self.SECRET, "text": "PLANTED-LOG-BODY"}]))

        _TokenHandler.seen = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _TokenHandler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)
        self.token_url = f"http://127.0.0.1:{self.server.server_port}/token?api-version=2.0"

    def _write_aws(self, exit_code=0, stderr=""):
        script = self.bin / "aws"
        script.write_text(
            "#!/bin/sh\n"
            f'printf "%s\\n" "$*" >> "{self.aws_calls}"\n'
            + (f'echo "{stderr}" >&2\n' if stderr else "")
            + f"exit {exit_code}\n"
        )
        script.chmod(0o755)

    def _env(self, **overrides):
        env = {
            "PATH": f"{self.bin}:{os.environ.get('PATH', '')}",
            "HOME": str(self.tmp),
            "RUNNER_TEMP": str(self.tmp / "runner-temp"),
            "GITHUB_REPOSITORY": REPOSITORY,
            "GITHUB_RUN_ID": RUN_ID,
            "GITHUB_RUN_ATTEMPT": RUN_ATTEMPT,
            "GITHUB_JOB": JOB,
            "GITHUB_WORKFLOW": "Agent Task",
            "ACTIONS_ID_TOKEN_REQUEST_URL": self.token_url,
            "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "request-token",
        }
        env.update({k: v for k, v in overrides.items() if v is not None})
        for key, value in overrides.items():
            if value is None:
                env.pop(key, None)
        Path(env["RUNNER_TEMP"]).mkdir(parents=True, exist_ok=True)
        return env

    def _run(self, log=None, secrets=None, env=None):
        return subprocess.run(
            [sys.executable, str(UPLOADER), "--log-file",
             str(self.log if log is None else log)],
            input=json.dumps({"BUREAU_TOKEN": self.SECRET})
            if secrets is None else secrets,
            capture_output=True, text=True, env=env or self._env(),
        )

    def _calls(self):
        if not self.aws_calls.exists():
            return []
        return [line for line in self.aws_calls.read_text().splitlines() if line]

    # ---- the gate the whole card rests on -------------------------------

    def test_a_refused_scrub_uploads_nothing(self):
        """`scrub_agent_log.py` refuses a file it cannot read as UTF-8 and
        writes nothing. Nothing may be uploaded on that path."""
        self.log.write_bytes(b"\xff\xfe not utf-8 \x00")
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._calls(), [],
                         "the AWS CLI was invoked after a refused scrub")
        self.assertIn("gap", (result.stdout + result.stderr).lower())

    def test_a_scrubbed_log_is_uploaded_once_with_if_none_match(self):
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self._calls()
        self.assertEqual(len(calls), 1, f"expected one AWS call, got {calls}")
        self.assertIn("s3api put-object", calls[0])
        self.assertIn("--if-none-match", calls[0])
        self.assertNotIn("create-multipart-upload", calls[0])

    def test_the_key_sits_under_the_stores_own_prefix(self):
        self._run()
        call = self._calls()[0]
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        expected = (f"agent-logs/{SLUG}/{today}/"
                    f"{RUN_ID}-{RUN_ATTEMPT}-{JOB}.json.gz")
        self.assertIn(expected, call)
        self.assertIn(f"agent-logs/{SLUG}/", call,
                      "a role scoped to agent-logs/<slug>/ must be able to "
                      "write this key")

    def test_the_uploaded_body_is_gzip_and_carries_no_secret(self):
        self._run()
        call = self._calls()[0]
        body = [tok for tok in call.split() if tok.endswith(".json.gz")]
        body = [Path(tok) for tok in body if Path(tok).exists()]
        self.assertTrue(body, f"no readable --body path in: {call}")
        text = gzip.decompress(body[0].read_bytes()).decode()
        self.assertIn("PLANTED-LOG-BODY", text)
        self.assertNotIn(self.SECRET, text)

    def test_the_log_never_reaches_stdout_or_stderr(self):
        result = self._run()
        self.assertNotIn("PLANTED-LOG-BODY", result.stdout)
        self.assertNotIn("PLANTED-LOG-BODY", result.stderr)
        self.assertNotIn(self.SECRET, result.stdout)
        self.assertNotIn(self.SECRET, result.stderr)

    def test_the_token_is_asked_for_with_the_sts_audience(self):
        self._run()
        self.assertTrue(_TokenHandler.seen, "no OIDC token was requested")
        self.assertEqual(_TokenHandler.seen[0]["audience"], "sts.amazonaws.com")

    # ---- every gap is recorded, and none of them fails the run ----------

    def test_no_oidc_token_is_a_recorded_gap(self):
        """portico forbids `id-token: write`. That is a gap, not an error."""
        result = self._run(env=self._env(ACTIONS_ID_TOKEN_REQUEST_URL=None))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._calls(), [])

    def test_no_aws_cli_is_a_recorded_gap(self):
        """The self-hosted Mac minis may not have it."""
        empty = self.tmp / "empty-bin"
        empty.mkdir()
        result = self._run(env=self._env(PATH=str(empty)))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._calls(), [])

    def test_a_log_over_the_cap_is_a_recorded_gap(self):
        """One single-part put_object or nothing: the store's write-once deny
        has no exemption for multipart."""
        result = self._run(env=self._env(BUREAU_AGENT_LOG_MAX_BYTES="16"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._calls(), [])

    def test_a_missing_log_file_is_a_recorded_gap(self):
        result = self._run(log=self.tmp / "nothing-here.json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._calls(), [])

    def test_a_repo_the_map_does_not_know_is_a_recorded_gap(self):
        result = self._run(env=self._env(GITHUB_REPOSITORY="someone/else"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._calls(), [])

    def test_a_refused_put_is_a_recorded_gap(self):
        self._write_aws(exit_code=1, stderr="PreconditionFailed")
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self._calls()), 1)

    def test_empty_secrets_on_stdin_uploads_nothing(self):
        """The scrub refuses empty stdin rather than reading it as "no
        secrets" — an unset `$SECRETS_JSON` looks exactly like that."""
        result = self._run(secrets="")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._calls(), [])


if __name__ == "__main__":
    unittest.main()
