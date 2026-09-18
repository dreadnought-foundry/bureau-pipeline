"""RED-first tests: the DRE-4109 read-retry seam is ONE module, usable from a
workflow step as well as from the sweep (DRE-4157).

THE INCIDENT (2026-09-17 → 18, six times on portico: PRs #582, #597, #598,
#600, #603, #605): agent-fix.yml's "Resolve PR, mode, and attempt budget" step
piped `gh api --paginate --slurp …/issues/N/comments` straight into `jq`. When
the shared App installation (123249480) was throttled, `gh` printed GitHub's
error body on STDOUT and exited 1; nothing checked the exit, jq read the error
object as the comment record and died on it (`Cannot index string with string
"user"`, exit 5), and the fix run was red for a reason that had nothing to do
with the branch.

THE CEO'S ANSWER (signed console answer on the card, 2026-09-17 15:31 PT):
"The fix loop's step that reads a pull request's comment history must notice
when GitHub refuses the call, wait and retry the way the sweep now does under
DRE-4109, and if it still cannot read, stop with a plain-English reason
instead of crashing. One pull request, with a test that feeds it a refused
answer."

FIX UNDER TEST — `scripts/gh_read_retry.py`, the seam `reconcile.gh_read`
already is, lifted into a stdlib module with a CLI so a bash step can use it:
  - `read(args)` runs `gh <args>`; a non-zero exit whose stderr carries
    GitHub's rate-limit wording is retried — three attempts, ~15s then ~45s,
    one run-log line per retry naming the command and `attempt N of 3` —
    exactly the DRE-4109 numbers, because it IS the DRE-4109 code;
  - anything that is not a rate-limit refusal raises at once, no wait;
  - the CLI, `gh_read_retry.py [--out FILE] gh api …`, hands the answer to
    stdout (or the file) ONLY on success. A refusal that persists is a
    plain-English line on stderr naming the throttle and exit 75
    (EX_TEMPFAIL, `reconcile.RATE_LIMITED_EXIT`'s convention); any other
    failure exits 1. GitHub's error body never reaches the data channel;
  - `reconcile.gh_read` consumes the module — one implementation, so the
    sweep and the fix loop cannot drift (the DRE-4109 suite in
    tests/test_gh_read_rate_limit_retry.py stays green as its proof).

Run: cd bureau-pipeline && python3 -m pytest tests/test_gh_read_retry.py -v
"""
from __future__ import annotations

import json
import os
import subprocess  # nosec B404 — fixed argv, this repo's own scripts
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import gh_read_retry  # noqa: E402
import reconcile  # noqa: E402

SCRIPT = ROOT / "scripts" / "gh_read_retry.py"

#: What `gh` put on STDERR on portico run 35252920674, verbatim from the card.
RATE_LIMITED = (
    "gh: HTTP 403: API rate limit exceeded for installation ID 123249480 "
    "(https://api.github.com/repos/dreadnought-foundry/portico/issues/582/comments?per_page=100)"
)
#: And what it put on STDOUT — GitHub's pretty-printed error body, which is
#: what jq was handed as "the comment record".
ERROR_BODY = json.dumps({
    "message": "API rate limit exceeded for installation ID 123249480.",
    "documentation_url": "https://docs.github.com/rest/overview/rate-limits-for-the-rest-api",
    "status": "403",
}, indent=2)
NOT_PERMITTED = "gh: HTTP 403: Resource not accessible by integration"
THREAD = json.dumps([[{"user": {"login": "agent-bureau-bot[bot]"}, "body": "a note"}]])


def _run_stub(answers):
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        rc, out, err = answers[min(len(calls) - 1, len(answers) - 1)]
        return SimpleNamespace(returncode=rc, stdout=out, stderr=err)

    return fake_run, calls


def _sleep_stub():
    waits: list[float] = []
    return (lambda seconds: waits.append(seconds)), waits


@pytest.fixture(autouse=True)
def _no_backoff_hook(monkeypatch):
    """The test hook must not leak between tests, nor into the sweep's own
    suite, which asserts the real numbers."""
    monkeypatch.delenv("BUREAU_GH_READ_BACKOFF", raising=False)


# --------------------------------------------------------------------------
# The module: DRE-4109's numbers, DRE-4109's classifier
# --------------------------------------------------------------------------
def test_a_brief_refusal_is_retried_and_the_answer_returned():
    fake_run, calls = _run_stub([
        (1, ERROR_BODY, RATE_LIMITED),
        (0, THREAD, ""),
    ])
    sleep, waits = _sleep_stub()
    log: list[str] = []
    with mock.patch.object(gh_read_retry.subprocess, "run", side_effect=fake_run), \
            mock.patch.object(gh_read_retry.time, "sleep", side_effect=sleep):
        out = gh_read_retry.read(["api", "repos/o/r/issues/1/comments"], log=log.append)

    assert out == THREAD
    assert len(calls) == 2 and calls[0][:2] == ["gh", "api"]
    assert waits == [15], "the first backoff is DRE-4109's ~15s"
    assert len(log) == 1 and "attempt 1 of 3" in log[0]
    assert "gh api repos/o/r/issues/1/comments" in log[0], "the line names the command"


def test_a_persisting_refusal_raises_after_three_attempts():
    fake_run, calls = _run_stub([(1, ERROR_BODY, RATE_LIMITED)])
    sleep, waits = _sleep_stub()
    with mock.patch.object(gh_read_retry.subprocess, "run", side_effect=fake_run), \
            mock.patch.object(gh_read_retry.time, "sleep", side_effect=sleep):
        with pytest.raises(gh_read_retry.GhReadError) as exc_info:
            gh_read_retry.read(["api", "x"], log=lambda _line: None)

    assert len(calls) == 3 and waits == [15, 45]
    assert exc_info.value.rate_limited is True
    assert "rate limit exceeded" in str(exc_info.value)


def test_a_permission_403_is_not_retried():
    fake_run, calls = _run_stub([(1, "", NOT_PERMITTED)])
    sleep, waits = _sleep_stub()
    with mock.patch.object(gh_read_retry.subprocess, "run", side_effect=fake_run), \
            mock.patch.object(gh_read_retry.time, "sleep", side_effect=sleep):
        with pytest.raises(gh_read_retry.GhReadError) as exc_info:
            gh_read_retry.read(["api", "x"], log=lambda _line: None)

    assert len(calls) == 1 and waits == []
    assert exc_info.value.rate_limited is False


def test_the_backoff_test_hook_shortens_the_waits(monkeypatch):
    """`BUREAU_GH_READ_BACKOFF` (the claude_rate_limit_retry.py hook shape)
    exists so the bash harness can run the real step without a minute of
    sleeping. It changes the waits and nothing else."""
    monkeypatch.setenv("BUREAU_GH_READ_BACKOFF", "0,0")
    fake_run, calls = _run_stub([(1, ERROR_BODY, RATE_LIMITED)])
    sleep, waits = _sleep_stub()
    with mock.patch.object(gh_read_retry.subprocess, "run", side_effect=fake_run), \
            mock.patch.object(gh_read_retry.time, "sleep", side_effect=sleep):
        with pytest.raises(gh_read_retry.GhReadError):
            gh_read_retry.read(["api", "x"], log=lambda _line: None)
    assert len(calls) == 3 and waits == [0, 0]


# --------------------------------------------------------------------------
# The sweep consumes the same seam
# --------------------------------------------------------------------------
def test_reconcile_gh_read_is_the_same_seam():
    assert reconcile.GH_READ_ATTEMPTS == gh_read_retry.ATTEMPTS
    assert reconcile.GH_READ_BACKOFF_SECONDS == gh_read_retry.BACKOFF_SECONDS
    assert reconcile._is_rate_limit_refusal is gh_read_retry.is_rate_limit_refusal
    with mock.patch.object(gh_read_retry, "read", return_value="the listing") as read:
        assert reconcile.gh_read("api", "repos/o/r/branches") == "the listing"
    assert read.call_count == 1, "reconcile.gh_read reads through the module"


# --------------------------------------------------------------------------
# The CLI, as a workflow step calls it
# --------------------------------------------------------------------------
GH_STUB = '''#!/usr/bin/env python3
"""Stand-in for `gh`: refuses the first GH_REFUSE_FIRST calls the way GitHub
did on 2026-09-17 (error body on stdout, gh's line on stderr, exit 1), then
answers GH_ANSWER."""
import os, sys
count_file = os.environ["GH_CALLS"]
n = int(open(count_file).read() or 0) if os.path.exists(count_file) else 0
open(count_file, "w").write(str(n + 1))
if n < int(os.environ.get("GH_REFUSE_FIRST", "0")):
    sys.stdout.write(os.environ["GH_ERROR_BODY"] + "\\n")
    sys.stderr.write(os.environ["GH_ERROR_LINE"] + "\\n")
    sys.exit(1)
sys.stdout.write(os.environ["GH_ANSWER"])
'''


def _cli(tmp_path: Path, refuse_first: int, *cli_args: str,
         error_line: str = RATE_LIMITED) -> tuple[subprocess.CompletedProcess, int]:
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    stub = bindir / "gh"
    stub.write_text(GH_STUB)
    stub.chmod(0o755)
    calls = tmp_path / "calls"
    env = dict(
        os.environ,
        PATH=f"{bindir}:{os.environ['PATH']}",
        GH_CALLS=str(calls),
        GH_REFUSE_FIRST=str(refuse_first),
        GH_ERROR_BODY=ERROR_BODY,
        GH_ERROR_LINE=error_line,
        GH_ANSWER=THREAD,
        BUREAU_GH_READ_BACKOFF="0,0",
    )
    proc = subprocess.run(  # nosec B603 — fixed argv, our own script
        [sys.executable, str(SCRIPT), *cli_args,
         "gh", "api", "--paginate", "--slurp", "repos/o/r/issues/1/comments?per_page=100"],
        capture_output=True, text=True, env=env, cwd=tmp_path,
    )
    return proc, int(calls.read_text() or 0)


def test_cli_prints_the_answer_on_stdout_after_a_blip(tmp_path):
    proc, calls = _cli(tmp_path, 1)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == THREAD, "stdout is the data channel — the answer, nothing else"
    assert calls == 2
    assert "attempt 1 of 3" in proc.stderr, "the retry line goes to the run log, not the data"


def test_cli_writes_the_out_file_only_on_success(tmp_path):
    out = tmp_path / "thread.json"
    proc, _ = _cli(tmp_path, 1, "--out", str(out))
    assert proc.returncode == 0, proc.stderr
    assert json.loads(out.read_text()) == json.loads(THREAD)
    assert proc.stdout == "", "with --out nothing goes to stdout"

    out2 = tmp_path / "never.json"
    proc, _ = _cli(tmp_path, 99, "--out", str(out2))
    assert proc.returncode != 0
    assert not out2.exists(), "a refused read leaves NO file for a caller to mistake for data"


def test_cli_stops_with_a_plain_english_reason_when_the_refusal_persists(tmp_path):
    """The card's second half: "if it still cannot read, stop with a
    plain-English reason instead of crashing." EX_TEMPFAIL, the code the sweep
    already uses for a quota exhaustion (DRE-2923)."""
    proc, calls = _cli(tmp_path, 99)
    assert proc.returncode == reconcile.RATE_LIMITED_EXIT == 75
    assert calls == 3, "three attempts in all, then it is a stop"
    assert proc.stdout == "", "GitHub's error body must NEVER reach the data channel"
    assert "rate limit" in proc.stderr
    assert "gh api --paginate --slurp repos/o/r/issues/1/comments?per_page=100" in proc.stderr, (
        "the reason names the call that was refused"
    )
    assert "not" in proc.stderr and "branch" in proc.stderr, (
        "and says in plain English that this is the outside service, not the branch"
    )
    assert "Traceback" not in proc.stderr


def test_cli_exits_1_at_once_on_a_failure_that_is_not_a_throttle(tmp_path):
    proc, calls = _cli(tmp_path, 99, error_line=NOT_PERMITTED)
    assert proc.returncode == 1
    assert calls == 1, "a permission failure is not retried"
    assert proc.stdout == ""
    assert "Resource not accessible" in proc.stderr


def test_cli_refuses_a_command_that_is_not_gh(tmp_path):
    proc = subprocess.run(  # nosec B603 — fixed argv, our own script
        [sys.executable, str(SCRIPT), "curl", "https://api.github.com"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 2
    assert "gh" in proc.stderr
