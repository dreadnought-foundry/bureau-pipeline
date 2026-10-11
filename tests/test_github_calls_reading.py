"""RED-first tests for DRE-6914 — a workflow job records the GitHub calls it made
on each pool login (epic DRE-6911).

GitHub meters calls per INSTALLATION, not per job, so `scripts/github_calls_reading.py`
records two POINTS on each login's hourly meter — one after the job's mints
(`before`), one at its end (`emit`) — and the `rise` between them when both sit
in the same hourly window. It writes one `github-calls-reading.v1` document and
puts it in the agent-log store over the run's own OIDC login.

WHAT THIS FILE PINS, one class per criterion on the card:

  * **the document** — one login, fake responses in the `BUREAU_FAKE_POOL_PROBES`
    shape (status + headers, through `dispatch_pool.parse_probe`), and the
    `rise` is after.used − before.used − 1 for the pair sharing a reset;
  * **a crossing job is never dropped** — resets that never match leave `rise`
    null, `window_turned` true, and BOTH points with their own `reset_at`;
  * **absence is not zero** — no login writes no document and says so; a read
    that failed is `before`/`after` null with a `reason`, never `rise: 0`;
  * **the upload** — the key is built from the document's own run, attempt,
    job and read time under `github-calls/`, and the put is single-part with
    `If-None-Match: *` through `upload_agent_log`'s own OIDC helper. A sentinel
    planted in a token reaches neither the document, stdout, the line nor the key;
  * **the tagged line** — fields in the contract's order, opening with `TAG`;
  * **the docstring** names the wired and the refused workflows, read against
    `UPLOAD_TRUSTED_REUSABLES` so the two lists cannot drift apart silently;
  * **the proof reach** — `config/proof-reach.json` declares the line.
"""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
SCRIPT = SCRIPTS / "github_calls_reading.py"

sys.path.insert(0, str(SCRIPTS))

import dispatch_pool  # noqa: E402
import github_calls_reading as gcr  # noqa: E402
import proof_reach  # noqa: E402
import upload_agent_log  # noqa: E402
from test_self_stubs_grant_id_token import UPLOAD_TRUSTED_REUSABLES  # noqa: E402

RUN_ID = "38004481513"
ATTEMPT = "1"
JOB = "execute"
REPOSITORY = "dreadnought-foundry/agent-bureau"
SLUG = "agent-bureau"
APP = "agent-bureau-bot"
INSTALLATION = 123249480
TOKEN_ENV = "BUREAU_TOKEN"
FAKES = "BUREAU_FAKE_GITHUB_CALLS_PROBES"

#: One hourly window's reset, and the next one's.
RESET = 1791702251
NEXT_RESET = RESET + 3600

SENTINEL = "ghs_SENTINEL-TOKEN-DO-NOT-KEEP-0123456789"

ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

LINE = re.compile(
    r"^github-calls: workflow=(?P<workflow>\S+) job=(?P<job>\S+) "
    r"login=(?P<login>\S+) installation=(?P<installation>\S+) "
    r"rise=(?P<rise>\S+) window=(?P<window>\S+) key=(?P<key>.+)$")


def _iso(seconds: int) -> str:
    return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _resp(used: int, reset: int, *, limit: int = 5000, resource: str = "core",
          status: int = 200) -> dict:
    """One fake response — status + headers, exactly what parse_probe reads."""
    return {
        "status": status,
        "x-ratelimit-limit": str(limit),
        "x-ratelimit-remaining": str(limit - used),
        "x-ratelimit-used": str(used),
        "x-ratelimit-reset": str(reset),
        "x-ratelimit-resource": resource,
    }


class _TokenHandler(BaseHTTPRequestHandler):
    """The runner's OIDC endpoint, as `ACTIONS_ID_TOKEN_REQUEST_URL` serves it."""

    token = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJyZXBvIn0.c2lnbmF0dXJl"

    def do_GET(self):  # noqa: N802
        body = json.dumps({"value": self.token}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class _Harness(unittest.TestCase):
    """The script EXECUTED, against a stub `aws` and a stub OIDC endpoint —
    the same two stand-ins tests/test_agent_log_upload.py runs the log upload
    against, so "through the existing OIDC helper" is a recorded call."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dre6914-"))
        self.addCleanup(__import__("shutil").rmtree, self.tmp, True)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.aws_calls = self.tmp / "aws-calls.txt"
        self.aws_env = self.tmp / "aws-env.txt"
        self._write_aws(0)
        self.before = self.tmp / "before.json"
        self.out = self.tmp / "github-calls-reading.json"
        self.github_output = self.tmp / "github-output.txt"

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _TokenHandler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)
        self.token_url = f"http://127.0.0.1:{self.server.server_port}/token"

    def _write_aws(self, exit_code: int, stderr: str = ""):
        script = self.bin / "aws"
        script.write_text(
            "#!/bin/sh\n"
            f'printf "%s\\n" "$*" >> "{self.aws_calls}"\n'
            f'env | grep -E "^AWS_" | sort >> "{self.aws_env}"\n'
            + (f'echo "{stderr}" >&2\n' if stderr else "")
            + f"exit {exit_code}\n")
        script.chmod(0o755)

    def _calls(self) -> list[str]:
        if not self.aws_calls.exists():
            return []
        return [c for c in self.aws_calls.read_text().splitlines() if c]

    def _env(self, fakes: dict | None, **extra) -> dict:
        env = {
            "PATH": f"{self.bin}:{os.environ.get('PATH', '')}",
            "HOME": str(self.tmp),
            "RUNNER_TEMP": str(self.tmp / "runner-temp"),
            "GITHUB_REPOSITORY": REPOSITORY,
            "GITHUB_RUN_ID": RUN_ID,
            "GITHUB_RUN_ATTEMPT": ATTEMPT,
            "GITHUB_JOB": JOB,
            "GITHUB_WORKFLOW_REF": (f"{REPOSITORY}/.github/workflows/"
                                    "self-agent-task.yml@refs/heads/main"),
            "ACTIONS_ID_TOKEN_REQUEST_URL": self.token_url,
            "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "request-token",
            TOKEN_ENV: "ghs_" + "x" * 36,
        }
        if fakes is not None:
            env[FAKES] = json.dumps(fakes)
        for key, value in extra.items():
            if value is None:
                env.pop(key, None)
            else:
                env[key] = value
        Path(env["RUNNER_TEMP"]).mkdir(parents=True, exist_ok=True)
        return env

    def _run(self, args: list[str], env: dict) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(SCRIPT), *args],
                              capture_output=True, text=True, env=env)

    def _login(self, app=APP, installation=INSTALLATION, token_env=TOKEN_ENV):
        return ["--login", f"{app}:{installation}:{token_env}"]

    def _before(self, fakes, logins=None, **extra):
        logins = self._login() if logins is None else logins
        result = self._run(["before", "--out", str(self.before), *logins],
                           self._env(fakes, **extra))
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def _emit(self, fakes, logins=None, workflow="agent-task.yml", **extra):
        logins = self._login() if logins is None else logins
        args = ["emit", "--before", str(self.before), "--out", str(self.out),
                *logins, "--github-output", str(self.github_output)]
        if workflow:
            args += ["--workflow", workflow]
        result = self._run(args, self._env(fakes, **extra))
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def _doc(self) -> dict:
        return json.loads(self.out.read_text())

    def _lines(self, result) -> list[str]:
        return [line for line in result.stdout.splitlines()
                if line.startswith("github-calls: workflow=")]

    def _one_job(self, before, after, **extra):
        self._before({TOKEN_ENV: before}, **extra)
        return self._emit({TOKEN_ENV: after}, **extra)


class OneLoginDocumentTest(_Harness):
    """Criterion 1: one login, two samples each step, one shared reset."""

    def test_one_document_carries_the_run_the_login_both_points_and_the_rise(self):
        result = self._one_job([_resp(119, RESET), _resp(120, RESET)],
                               [_resp(1360, RESET), _resp(1361, RESET)])
        doc = self._doc()
        self.assertEqual(doc["schema"], "github-calls-reading.v1")
        self.assertEqual(doc["workflow"], "agent-task.yml")
        self.assertEqual(doc["job"], JOB)
        self.assertEqual(doc["repo"], REPOSITORY)
        self.assertEqual(doc["run"], {"id": int(RUN_ID), "attempt": int(ATTEMPT)})
        self.assertEqual(len(doc["logins"]), 1, doc["logins"])
        login = doc["logins"][0]
        self.assertEqual(login["app"], APP)
        self.assertEqual(login["installation_id"], INSTALLATION)
        self.assertEqual(login["resource"], "core")
        for name, used in (("before", 120), ("after", 1360)):
            point = login[name]
            self.assertEqual(point["used"], used, name)
            self.assertEqual(point["limit"], 5000, name)
            self.assertEqual(point["reset_at"], _iso(RESET), name)
            self.assertRegex(point["sampled_at"], ISO, name)
        self.assertEqual(login["rise"], 1360 - 120 - 1)
        self.assertIs(login["window_turned"], False)
        self.assertIn("shared", login["note"])
        self.assertEqual(len(self._lines(result)), 1, result.stdout)

    def test_the_document_carries_exactly_the_contract_keys(self):
        self._one_job([_resp(119, RESET), _resp(120, RESET)],
                      [_resp(1360, RESET), _resp(1361, RESET)])
        doc = self._doc()
        self.assertEqual(
            list(doc),
            ["schema", "delivery_id", "repo", "workflow", "job", "run",
             "read_at", "read_at_epoch_ms", "logins"])
        login = doc["logins"][0]
        self.assertEqual(
            list(login),
            ["app", "installation_id", "resource", "before", "after", "rise",
             "window_turned", "note"])
        self.assertEqual(list(login["before"]),
                         ["used", "limit", "reset_at", "sampled_at"])
        self.assertRegex(doc["read_at"], ISO)
        self.assertEqual(
            doc["delivery_id"],
            f"{RUN_ID}-{ATTEMPT}-{JOB}-{doc['read_at_epoch_ms']}")

    def test_the_pair_is_the_one_sharing_a_reset_never_across_counters(self):
        """DRE-4576: one token is answered from two counters with different
        reset clocks. A rise across them would be a difference of two meters."""
        self._one_job([_resp(60, RESET), _resp(3385, RESET - 1440)],
                      [_resp(3400, RESET - 1440), _resp(75, RESET)])
        login = self._doc()["logins"][0]
        self.assertEqual(login["before"]["reset_at"], login["after"]["reset_at"])
        # Both counters pair; the busier one is the reading, as dispatch_pool's
        # `worst` takes the busier counter — never 3400 - 60 or 75 - 3385.
        self.assertEqual(login["rise"], 3400 - 3385 - 1)
        self.assertEqual(login["before"]["reset_at"], _iso(RESET - 1440))
        self.assertIs(login["window_turned"], False)

    def test_each_step_takes_the_probe_samples_dispatch_pool_takes(self):
        """The cost the docstring states: PROBE_SAMPLES calls per login per step."""
        self.assertEqual(dispatch_pool.PROBE_SAMPLES, 2)
        self.assertIn("2 calls per login per step", gcr.__doc__)
        self.assertIn("4 per login per job", gcr.__doc__)
        self.assertIs(gcr.PROBE_SAMPLES, dispatch_pool.PROBE_SAMPLES)

    def test_the_workflow_falls_back_to_the_workflow_refs_file_name(self):
        """No `--workflow`: the basename of the path in `$GITHUB_WORKFLOW_REF`."""
        self._before({TOKEN_ENV: _resp(120, RESET)})
        self._emit({TOKEN_ENV: _resp(130, RESET)}, workflow="",
                   GITHUB_WORKFLOW_REF=("o/r/.github/workflows/agent-fix.yml"
                                        "@refs/heads/main"))
        self.assertEqual(self._doc()["workflow"], "agent-fix.yml")

    def test_each_installation_is_recorded_once(self):
        logins = self._login() + self._login(token_env="BUREAU_TOKEN_REMINT")
        self._before({TOKEN_ENV: _resp(120, RESET)}, logins=self._login())
        self._emit({TOKEN_ENV: _resp(130, RESET),
                    "BUREAU_TOKEN_REMINT": _resp(130, RESET)},
                   logins=logins, BUREAU_TOKEN_REMINT="ghs_" + "y" * 36)
        self.assertEqual(len(self._doc()["logins"]), 1)

    def test_a_remint_is_the_same_installation_and_needs_no_second_before(self):
        """DRE-3043: the emit step hands the freshest token per installation;
        it is matched to the before point by installation, not by token."""
        self._before({TOKEN_ENV: [_resp(119, RESET), _resp(120, RESET)]})
        self._emit({"BUREAU_TOKEN_REMINT": [_resp(500, RESET), _resp(501, RESET)]},
                   logins=self._login(token_env="BUREAU_TOKEN_REMINT"),
                   BUREAU_TOKEN_REMINT="ghs_" + "y" * 36)
        self.assertEqual(self._doc()["logins"][0]["rise"], 500 - 120 - 1)


class CrossingWindowTest(_Harness):
    """Criterion 2: a job that crosses the hour is two points in two windows."""

    def test_resets_that_never_match_keep_both_points_and_no_rise(self):
        result = self._one_job([_resp(4900, RESET), _resp(4901, RESET)],
                               [_resp(12, NEXT_RESET), _resp(13, NEXT_RESET)])
        login = self._doc()["logins"][0]
        self.assertIsNone(login["rise"])
        self.assertIs(login["window_turned"], True)
        self.assertEqual(login["before"]["used"], 4901)
        self.assertEqual(login["before"]["reset_at"], _iso(RESET))
        self.assertEqual(login["after"]["reset_at"], _iso(NEXT_RESET))
        self.assertRegex(login["before"]["sampled_at"], ISO)
        self.assertRegex(login["after"]["sampled_at"], ISO)
        line = LINE.match(self._lines(result)[0])
        self.assertEqual(line["rise"], "unknown")
        self.assertEqual(line["window"], _iso(NEXT_RESET))


class AbsenceIsNotZeroTest(_Harness):
    """Criterion 3: nothing minted writes nothing; an unread login is unread."""

    def test_no_login_writes_no_document_and_says_so(self):
        for args in (["before", "--out", str(self.before)],
                     ["emit", "--before", str(self.before), "--out", str(self.out)]):
            with self.subTest(command=args[0]):
                result = self._run(args, self._env({}))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("github-calls: no pool login minted in this job — "
                              "nothing recorded", result.stdout)
        self.assertFalse(self.out.exists())
        self.assertFalse(self.before.exists())
        self.assertEqual(self._calls(), [])

    def test_a_mint_that_did_not_run_is_no_login(self):
        """A skipped mint step renders its outputs as '' — `::BUREAU_TOKEN`."""
        result = self._run(["emit", "--before", str(self.before), "--out",
                            str(self.out), "--login", f"::{TOKEN_ENV}"],
                           self._env({}, **{TOKEN_ENV: ""}))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no pool login minted", result.stdout)
        self.assertFalse(self.out.exists())

    def test_a_failed_read_is_null_with_a_reason_never_zero(self):
        self._one_job(None, None)
        login = self._doc()["logins"][0]
        self.assertIsNone(login["before"])
        self.assertIsNone(login["after"])
        self.assertTrue(login["reason"])
        self.assertIsNone(login["rise"])
        self.assertNotEqual(login["rise"], 0)

    def test_an_empty_token_variable_is_an_unread_login(self):
        self._before({}, **{TOKEN_ENV: ""})
        self._emit({}, **{TOKEN_ENV: ""})
        login = self._doc()["logins"][0]
        self.assertIsNone(login["before"])
        self.assertIsNone(login["after"])
        self.assertIn(TOKEN_ENV, login["reason"])

    def test_an_unread_after_keeps_the_before_point(self):
        self._before({TOKEN_ENV: [_resp(119, RESET), _resp(120, RESET)]})
        result = self._emit({TOKEN_ENV: None})
        login = self._doc()["logins"][0]
        self.assertEqual(login["before"]["used"], 120)
        self.assertIsNone(login["after"])
        self.assertIsNone(login["rise"])
        self.assertTrue(login["reason"])
        self.assertEqual(LINE.match(self._lines(result)[0])["rise"], "unknown")

    def test_no_before_file_is_a_missing_point_not_a_red_job(self):
        result = self._emit({TOKEN_ENV: _resp(130, RESET)})
        login = self._doc()["logins"][0]
        self.assertIsNone(login["before"])
        self.assertEqual(login["after"]["used"], 130)
        self.assertIsNone(login["rise"])
        self.assertTrue(login["reason"])
        self.assertEqual(len(self._lines(result)), 1)

    def test_bad_arguments_still_exit_zero(self):
        result = self._run(["emit", "--nonsense"], self._env({}))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("github-calls:", result.stdout)


class UploadTest(_Harness):
    """Criterion 4: the key, the put, and the token kept out of all of it."""

    def test_the_key_is_built_from_the_documents_own_fields(self):
        result = self._one_job([_resp(119, RESET), _resp(120, RESET)],
                               [_resp(1360, RESET), _resp(1361, RESET)])
        doc = self._doc()
        day = doc["read_at"][:10]
        expected = (f"agent-logs/{SLUG}/github-calls/{day}/"
                    f"{doc['run']['id']}-{doc['run']['attempt']}-{doc['job']}-"
                    f"{doc['read_at_epoch_ms']}.json")
        self.assertEqual(gcr.object_key(doc, SLUG), expected)
        self.assertEqual(LINE.match(self._lines(result)[0])["key"], expected)
        self.assertIn(f"key={expected}", self.github_output.read_text())

    def test_the_put_is_one_single_part_write_once_through_the_oidc_login(self):
        self._one_job([_resp(119, RESET), _resp(120, RESET)],
                      [_resp(1360, RESET), _resp(1361, RESET)])
        calls = self._calls()
        self.assertEqual(len(calls), 1, calls)
        self.assertIn("s3api put-object", calls[0])
        self.assertIn("--if-none-match *", calls[0])
        self.assertNotIn("multipart", calls[0])
        self.assertIn("--content-type application/json", calls[0])
        self.assertIn(f"--key {gcr.object_key(self._doc(), SLUG)}", calls[0])
        env = self.aws_env.read_text()
        self.assertIn("AWS_WEB_IDENTITY_TOKEN_FILE=", env)
        self.assertIn(f"role/bureau-agent-log-upload-{SLUG}", env)
        self.assertIn("AWS_CONFIG_FILE=/dev/null", env)
        self.assertNotIn(_TokenHandler.token, calls[0])
        runner_temp = Path(self._env({})["RUNNER_TEMP"])
        self.assertEqual(list(runner_temp.glob("*.jwt")), [])
        self.assertFalse((runner_temp / "github-calls-home").exists())

    def test_the_put_goes_through_upload_agent_logs_helper(self):
        """Not a copy of the credential path: the one upload_agent_log owns."""
        tmp = self.tmp
        env = self._env({TOKEN_ENV: _resp(120, RESET)})
        before = tmp / "b.json"
        out = tmp / "o.json"
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(upload_agent_log, "oidc_put") as put, \
                redirect_stdout(io.StringIO()):
            gcr.main(["before", "--out", str(before), *self._login()])
            gcr.main(["emit", "--before", str(before), "--out", str(out),
                      *self._login(), "--workflow", "agent-task.yml"])
        self.assertEqual(put.call_count, 1)
        _, kwargs = put.call_args
        self.assertEqual(kwargs["key"], gcr.object_key(json.loads(out.read_text()), SLUG))
        self.assertEqual(kwargs["content_type"], "application/json")
        self.assertEqual(Path(kwargs["body"]), out)

    def test_a_refused_put_is_a_line_and_exit_zero(self):
        self._write_aws(1, "An error occurred (AccessDenied)")
        result = self._one_job(_resp(120, RESET), _resp(130, RESET))
        line = LINE.match(self._lines(result)[0])
        self.assertTrue(line["key"].startswith("not uploaded: "), line["key"])
        self.assertTrue(self.out.exists())

    def test_no_oidc_token_is_a_line_and_exit_zero(self):
        result = self._one_job(_resp(120, RESET), _resp(130, RESET),
                               ACTIONS_ID_TOKEN_REQUEST_URL=None)
        line = LINE.match(self._lines(result)[0])
        self.assertTrue(line["key"].startswith("not uploaded: "), line["key"])
        self.assertEqual(self._calls(), [])

    def test_a_token_sentinel_reaches_nothing_on_the_cli_path(self):
        result = self._one_job([_resp(119, RESET), _resp(120, RESET)],
                               [_resp(1360, RESET), _resp(1361, RESET)],
                               **{TOKEN_ENV: SENTINEL})
        self.assertNotIn(SENTINEL, self.out.read_text())
        self.assertNotIn(SENTINEL, self.before.read_text())
        self.assertNotIn(SENTINEL, result.stdout + result.stderr)
        self.assertNotIn(SENTINEL, "\n".join(self._calls()))
        self.assertNotIn(SENTINEL, self.aws_env.read_text())
        self.assertNotIn(SENTINEL, self.github_output.read_text())


class _MeterHandler(BaseHTTPRequestHandler):
    """GitHub's API, answering `GET /installation/repositories` with a meter."""

    seen: list = []
    used = 120

    def do_GET(self):  # noqa: N802
        _MeterHandler.seen.append((self.path, self.headers.get("Authorization", "")))
        _MeterHandler.used += 1
        body = b'{"total_count": 1, "repositories": []}'
        self.send_response(200)
        for name, value in _resp(_MeterHandler.used, RESET).items():
            if name != "status":
                self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class RealProbeTest(_Harness):
    """The real read, against a local stand-in for api.github.com: the token
    is SENT (so the sentinel check below is not vacuous) and kept nowhere."""

    def setUp(self):
        super().setUp()
        _MeterHandler.seen = []
        _MeterHandler.used = 120
        self.api = ThreadingHTTPServer(("127.0.0.1", 0), _MeterHandler)
        threading.Thread(target=self.api.serve_forever, daemon=True).start()
        self.addCleanup(self.api.shutdown)

    def test_a_token_sentinel_is_sent_and_reaches_neither_document_line_nor_key(self):
        env = self._env(None, **{TOKEN_ENV: SENTINEL})
        uploaded = {}

        def fake_put(**kwargs):
            uploaded.update(kwargs)
            uploaded["text"] = Path(kwargs["body"]).read_text()

        stdout = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(gcr, "API_URL", f"http://127.0.0.1:{self.api.server_port}"), \
                mock.patch.object(upload_agent_log, "oidc_put", side_effect=fake_put), \
                redirect_stdout(stdout):
            self.assertEqual(gcr.main(["before", "--out", str(self.before), *self._login()]), 0)
            self.assertEqual(gcr.main(["emit", "--before", str(self.before), "--out",
                                       str(self.out), *self._login(),
                                       "--workflow", "agent-task.yml"]), 0)

        self.assertEqual(len(_MeterHandler.seen), 2 * dispatch_pool.PROBE_SAMPLES)
        for path, auth in _MeterHandler.seen:
            self.assertEqual(path, "/installation/repositories?per_page=1")
            self.assertEqual(auth, f"Bearer {SENTINEL}")
        doc = self._doc()
        self.assertEqual(doc["logins"][0]["before"]["used"], 122)
        self.assertEqual(doc["logins"][0]["after"]["used"], 123)
        self.assertEqual(doc["logins"][0]["rise"], 0)
        for where, text in (("document", self.out.read_text()),
                            ("before file", self.before.read_text()),
                            ("stdout", stdout.getvalue()),
                            ("key", uploaded["key"]),
                            ("uploaded body", uploaded["text"])):
            with self.subTest(where=where):
                self.assertNotIn(SENTINEL, text)
        self.assertEqual(len([l for l in stdout.getvalue().splitlines()
                              if l.startswith("github-calls: workflow=")]), 1)


class TaggedLineTest(_Harness):
    """Criterion 5: the line the epic's proof reads from the job log."""

    def test_the_line_carries_the_fields_in_order_and_opens_with_tag(self):
        self.assertEqual(gcr.TAG, "github-calls")
        result = self._one_job([_resp(119, RESET), _resp(120, RESET)],
                               [_resp(1360, RESET), _resp(1361, RESET)])
        lines = self._lines(result)
        self.assertEqual(len(lines), 1, result.stdout)
        self.assertTrue(lines[0].startswith(f"{gcr.TAG}: "))
        match = LINE.match(lines[0])
        self.assertIsNotNone(match, lines[0])
        self.assertEqual(
            match.groupdict(),
            {"workflow": "agent-task.yml", "job": JOB, "login": APP,
             "installation": str(INSTALLATION), "rise": "1239",
             "window": _iso(RESET), "key": gcr.object_key(self._doc(), SLUG)})

    def test_one_line_per_login(self):
        logins = self._login() + self._login(app="agent-bureau-pool-2",
                                             installation=222,
                                             token_env="BUREAU_POOL_TOKEN_2")
        extra = {"BUREAU_POOL_TOKEN_2": "ghs_" + "z" * 36}
        self._before({TOKEN_ENV: _resp(120, RESET),
                      "BUREAU_POOL_TOKEN_2": _resp(10, RESET)}, logins=logins, **extra)
        result = self._emit({TOKEN_ENV: _resp(130, RESET),
                             "BUREAU_POOL_TOKEN_2": _resp(20, RESET)},
                            logins=logins, **extra)
        lines = [LINE.match(l) for l in self._lines(result)]
        self.assertEqual([m["login"] for m in lines], [APP, "agent-bureau-pool-2"])
        self.assertEqual(len(self._doc()["logins"]), 2)


def _section(text: str, start: str, end: str) -> str:
    head = text.index(start)
    return text[head:text.index(end, head)]


class DocstringListsTest(unittest.TestCase):
    """Criterion 6: the wired and the refused, read against the test's own list."""

    WIRED = "WHICH WORKFLOWS ARE WIRED"
    NOT_WIRED = "WHICH ARE NOT, AND WHY"
    AFTER = "HOW THE POINTS ARE TAKEN"
    REFUSED = ("reconcile.yml", "merge-gate.yml", "linear-sync.yml")

    def test_every_upload_trusted_reusable_is_named_as_wired(self):
        wired = _section(gcr.__doc__, self.WIRED, self.NOT_WIRED)
        self.assertEqual(len(UPLOAD_TRUSTED_REUSABLES), 7)
        for name in UPLOAD_TRUSTED_REUSABLES:
            with self.subTest(workflow=name):
                self.assertIn(name, wired)
        for name in self.REFUSED:
            self.assertNotIn(name, wired)

    def test_the_sweeps_are_named_as_refused_by_the_upload_role(self):
        refused = _section(gcr.__doc__, self.NOT_WIRED, self.AFTER)
        flat = " ".join(refused.split())
        self.assertIn("refused by the upload role", flat)
        for name in self.REFUSED:
            with self.subTest(workflow=name):
                self.assertIn(name, refused)
        for name in UPLOAD_TRUSTED_REUSABLES:
            self.assertNotIn(name, refused)


class ProofReachTest(unittest.TestCase):
    """Criterion 7: the line is declared where the proof's reach is declared."""

    ENTRY = {"tag": "github-calls:",
             "printed_by": "scripts/github_calls_reading.py",
             "constant": "TAG",
             "when": ("every job a wiring card places the emit step in, one line "
                      "per login, since DRE-6914")}

    def test_the_log_lines_entry_is_declared_exactly(self):
        doc = json.loads((ROOT / "config" / "proof-reach.json").read_text())
        entries = [e for e in doc["log_lines"] if e["tag"] == "github-calls:"]
        self.assertEqual(entries, [self.ENTRY])

    def test_the_printer_spells_the_tag_in_its_constant(self):
        self.assertEqual(proof_reach._log_line_problems(ROOT, self.ENTRY), [])
        self.assertEqual(proof_reach.check(ROOT), [])


if __name__ == "__main__":
    unittest.main()
