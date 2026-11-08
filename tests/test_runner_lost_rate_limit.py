"""RED-first tests for DRE-9214 — the runner-lost step waits out a GitHub rate
limit on its read of the default branch, and asks again, before it gives up.

THE INCIDENT (2026-11-04, PT): at 6:48 AM, 8:49 AM, 12:47 PM and 3:46 PM
agent-bureau's sweep printed `DEGRADED: runner-lost: dreadnought-foundry/
agent-bureau's default branch unreadable — re-running nothing this sweep`.
Each time the read had been refused with `API rate limit exceeded for
installation ID 123249480`, and minutes later that installation still had
about 4,800–5,400 of its 5,500 core calls. Short limits, not a spent hour —
and one refused answer cost every stranded-run re-run that pass.

UNDER TEST — `reconcile.rerun_runner_lost_ci`, with `gh` and the clock faked:

  * a rate-limited branch read is waited out and asked again — the first wait
    the step's own backoff (the silent read that was refused kept no headers),
    each later one what GitHub's answer asked for (`retry-after`, or
    `x-ratelimit-reset` when nothing is left) — and a read that then answers
    goes on to the re-run as if nothing had happened;
  * the waits stop at a bound that fits the sweep's 10-minute job, and the
    `DEGRADED:` line then says the read was rate-limited and what was waited,
    never "unreadable";
  * a refusal that is not a rate limit is not waited on, and keeps today's
    line;
  * no other step's reading of the default branch changes.

Run: cd bureau-pipeline && python3 -m pytest tests/test_runner_lost_rate_limit.py -v
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/atlas")
os.environ.setdefault("REPO_SLUG", "atlas")
os.environ.setdefault("GH_TOKEN", "x")

import reconcile  # noqa: E402

REPO = "dreadnought-foundry/agent-bureau"
SHA = "559c8b9" + "0" * 33
NOT_ACQUIRED = ("The job was not acquired by Runner of type hosted even after "
                "multiple attempts")

#: What the silent `gh api repos/:r --jq .default_branch` hands back on the
#: refusal of 2026-11-04: GitHub's error body, on stdout.
REFUSED_BODY = json.dumps({
    "message": "API rate limit exceeded for installation ID 123249480. If you "
               "reach out to GitHub Support for help, please include the "
               "request ID 1234:5678.",
    "documentation_url": "https://docs.github.com/rest/overview/rate-limits-for-the-rest-api",
    "status": "403",
})
REFUSED_STDERR = ("gh: API rate limit exceeded for installation ID 123249480. "
                  "(HTTP 403)")


def _answer(status_line, headers, body, rc):
    head = "\r\n".join([status_line, *(f"{k}: {v}" for k, v in headers.items())])
    return subprocess.CompletedProcess(
        args=[], returncode=rc, stdout=f"{head}\r\n\r\n{body}",
        stderr="" if rc == 0 else REFUSED_STDERR)


def refused(**headers):
    return _answer("HTTP/2.0 403 Forbidden", headers, REFUSED_BODY, 1)


def answered(branch="main"):
    return _answer("HTTP/2.0 200 OK", {"X-Ratelimit-Remaining": "4812"},
                   json.dumps({"full_name": REPO, "default_branch": branch}), 0)


class FakeGh:
    """`subprocess.run` for the step's own re-read of the repo, answered in
    turn; any other command is a test bug and fails loudly."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls: list = []

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        assert argv[:2] == ["gh", "api"] and f"repos/{REPO}" in argv, argv
        assert self.answers, f"one read more than the test expected: {argv}"
        return self.answers.pop(0)


class _Actions:
    def __init__(self, table):
        self.table = table
        self.calls: list = []

    def __call__(self, args):
        self.calls.append(args)
        path = args[1] if args[:1] == ("api",) else ""
        for prefix, payload in self.table.items():
            if path.startswith(prefix):
                return json.dumps(payload), None
        return None, "rc=1: HTTP 404"


def _lost_run(run_id=4242):
    return {
        "id": run_id, "name": "CI", "path": ".github/workflows/ci.yml",
        "workflow_id": 11, "event": "push", "head_branch": "main",
        "head_sha": SHA, "status": "completed", "conclusion": "cancelled",
        "run_attempt": 1, "created_at": "2026-11-04T14:40:00Z",
        "updated_at": "2026-11-04T14:46:00Z",
        "html_url": f"https://github.com/{REPO}/actions/runs/{run_id}",
    }


@pytest.fixture
def sweep(monkeypatch):
    """The step wired to fakes: the cached branch answer is the refused body,
    every sleep is recorded instead of slept, and the CI listing holds one
    run GitHub never gave a machine."""
    state = {"sleeps": [], "dispatched": []}
    monkeypatch.setattr(reconcile, "REPO", REPO)
    monkeypatch.setattr(reconcile, "_default_branch", REFUSED_BODY)
    monkeypatch.setattr(reconcile, "_degraded", [])
    monkeypatch.setattr(reconcile.time, "sleep", state["sleeps"].append)
    actions = _Actions({
        f"repos/{REPO}/actions/runs?": {"workflow_runs": [_lost_run()]},
        f"repos/{REPO}/actions/runs/4242/attempts/1/jobs": {"jobs": [
            {"id": 77, "name": "Tests", "status": "completed", "conclusion": "cancelled"}]},
    })
    monkeypatch.setattr(reconcile, "_actions_read", actions)
    monkeypatch.setattr(reconcile, "gh_dispatch",
                        lambda *a: state["dispatched"].append(a))
    monkeypatch.setattr(reconcile, "_check_run_annotations",
                        lambda job_id: [{"message": NOT_ACQUIRED}])
    reconcile._write_failures.clear()
    state["actions"] = actions

    def run(*answers):
        fake = FakeGh(*answers)
        monkeypatch.setattr(reconcile.subprocess, "run", fake)
        reconcile.rerun_runner_lost_ci()
        state["gh"] = fake
        return state

    yield run
    reconcile._write_failures.clear()


def _degraded_lines(out):
    return [line for line in out.splitlines() if line.startswith("DEGRADED:")]


# --------------------------------------------------------------------------- #
# waited out, then answered                                                    #
# --------------------------------------------------------------------------- #

def test_a_rate_limited_read_is_waited_out_and_the_lost_run_is_still_re_run(sweep, capsys):
    """The 2026-11-04 shape: one refused answer, a short wait, and the read
    answers. The pass re-runs the stranded run exactly as it would have."""
    state = sweep(answered("main"))

    assert state["sleeps"] == [reconcile.RUNNER_LOST_RATE_LIMIT_BACKOFF]
    assert state["dispatched"] == [("run", "rerun", "4242", "--failed", "--repo", REPO)]
    listing = state["actions"].calls[0][1]
    assert "branch=main" in listing
    out = capsys.readouterr().out
    assert _degraded_lines(out) == []
    assert reconcile._degraded == []
    assert reconcile._write_failures == []


def test_the_wait_is_said_in_the_run_log(sweep, capsys):
    """Every wait is one line, so a pass that paused is visible in its log."""
    sweep(answered("main"))
    out = capsys.readouterr().out
    waits = [line for line in out.splitlines() if "rate limit" in line.lower()]
    assert len(waits) == 1
    assert REPO in waits[0]
    assert f"{reconcile.RUNNER_LOST_RATE_LIMIT_BACKOFF}" in waits[0]


def test_the_branch_the_re_read_found_is_the_branch_the_listing_asks_for(sweep):
    """Not the refused body still cached by `default_branch()`: the listing's
    URL carries the branch the step's own read answered with."""
    state = sweep(answered("trunk"))
    listing = state["actions"].calls[0][1]
    assert "branch=trunk" in listing
    assert "rate+limit" not in listing and "rate%20limit" not in listing


def test_a_second_wait_is_what_github_asked_for_in_retry_after(sweep):
    state = sweep(refused(**{"Retry-After": "7"}), answered("main"))
    assert state["sleeps"] == [reconcile.RUNNER_LOST_RATE_LIMIT_BACKOFF, 7]
    assert len(state["dispatched"]) == 1


def test_a_second_wait_runs_to_the_reset_when_nothing_is_left(sweep, monkeypatch):
    monkeypatch.setattr(reconcile.time, "time", lambda: 1_000_000.0)
    state = sweep(
        refused(**{"X-Ratelimit-Remaining": "0", "X-Ratelimit-Reset": "1000012"}),
        answered("main"))
    assert state["sleeps"] == [reconcile.RUNNER_LOST_RATE_LIMIT_BACKOFF, 12]
    assert len(state["dispatched"]) == 1


def _bare_refusal(status_line="HTTP/2.0 403 Forbidden", **headers):
    """A refusal whose words say nothing of a rate limit — only its headers."""
    done = _answer(status_line, headers, '{"message": "Forbidden"}', 1)
    done.stderr = f"gh: Forbidden ({status_line.split()[0]} {status_line.split()[1]})"
    return done


def test_a_refusal_carrying_retry_after_is_a_rate_limit_whatever_it_says(sweep):
    state = sweep(_bare_refusal(**{"Retry-After": "5"}), answered("main"))
    assert state["sleeps"] == [reconcile.RUNNER_LOST_RATE_LIMIT_BACKOFF, 5]
    assert len(state["dispatched"]) == 1


def test_a_refusal_with_no_calls_left_is_a_rate_limit_whatever_it_says(sweep, monkeypatch):
    monkeypatch.setattr(reconcile.time, "time", lambda: 1_000_000.0)
    state = sweep(_bare_refusal(**{"X-Ratelimit-Remaining": "0",
                                   "X-Ratelimit-Reset": "1000009"}),
                  answered("main"))
    assert state["sleeps"] == [reconcile.RUNNER_LOST_RATE_LIMIT_BACKOFF, 9]
    assert len(state["dispatched"]) == 1


def test_a_reset_header_alone_is_not_a_rate_limit(sweep, capsys):
    """Every GitHub answer carries `x-ratelimit-reset`, a permission refusal
    included: with calls left and no `retry-after`, it is not waited on."""
    state = sweep(_bare_refusal(**{"X-Ratelimit-Remaining": "4100",
                                   "X-Ratelimit-Reset": "1000009"}))
    assert state["sleeps"] == [reconcile.RUNNER_LOST_RATE_LIMIT_BACKOFF]
    line, = _degraded_lines(capsys.readouterr().out)
    assert "unreadable" in line


def test_the_re_read_has_a_timeout_so_a_hung_call_cannot_spend_the_job(sweep):
    state = sweep(answered("main"))
    (_argv, kwargs), = state["gh"].calls
    assert kwargs.get("timeout") == reconcile.RUNNER_LOST_READ_TIMEOUT


# --------------------------------------------------------------------------- #
# still refused at the bound                                                   #
# --------------------------------------------------------------------------- #

def test_still_refused_after_every_wait_says_rate_limited_and_the_waits(sweep, capsys):
    """The give-up wording: rate-limited, with the waits it tried — never
    "unreadable", which is the word that hid four short limits in one day."""
    state = sweep(*[refused() for _ in range(reconcile.RUNNER_LOST_RATE_LIMIT_WAITS)])

    assert state["dispatched"] == []
    assert state["actions"].calls == []
    assert sum(state["sleeps"]) <= reconcile.RUNNER_LOST_RATE_LIMIT_WAIT_SECONDS
    assert len(state["sleeps"]) == reconcile.RUNNER_LOST_RATE_LIMIT_WAITS
    line, = _degraded_lines(capsys.readouterr().out)
    assert line.startswith(f"DEGRADED: runner-lost: {REPO}'s default branch rate-limited")
    assert "unreadable" not in line
    for seconds in state["sleeps"]:
        assert f"{seconds:g} s" in line
    assert f"{sum(state['sleeps']):g} s" in line
    assert "re-running nothing this sweep" in line
    assert "API rate limit exceeded" in line
    assert reconcile._write_failures == []
    assert len(reconcile._degraded) == 1


def test_a_wait_past_what_is_left_is_not_waited_and_the_line_says_so(sweep, capsys, monkeypatch):
    """An hour's bucket that is truly spent asks for its reset — far past the
    step's share of the job. The step does not wait it, and says what GitHub
    asked for."""
    monkeypatch.setattr(reconcile.time, "time", lambda: 1_000_000.0)
    state = sweep(refused(**{"X-Ratelimit-Remaining": "0",
                             "X-Ratelimit-Reset": "1001800"}))

    assert state["sleeps"] == [reconcile.RUNNER_LOST_RATE_LIMIT_BACKOFF]
    assert state["dispatched"] == []
    line, = _degraded_lines(capsys.readouterr().out)
    assert "rate-limited" in line and "unreadable" not in line
    assert "1800 s" in line
    assert f"{reconcile.RUNNER_LOST_RATE_LIMIT_BACKOFF:g} s" in line


def test_a_retry_after_of_zero_cannot_loop(sweep):
    """The count of waits is bounded as well as their sum."""
    state = sweep(*[refused(**{"Retry-After": "0"})
                    for _ in range(reconcile.RUNNER_LOST_RATE_LIMIT_WAITS)])
    assert len(state["gh"].calls) == reconcile.RUNNER_LOST_RATE_LIMIT_WAITS
    assert state["dispatched"] == []


def test_the_bound_fits_the_sweeps_ten_minute_job():
    """The step's share, in the worst case: every wait spent, and every re-read
    running to its timeout. Two minutes of the ten — the per-read retry of
    `gh_read` keeps its own three (GH_READ_RETRY_BUDGET), untouched."""
    worst = (reconcile.RUNNER_LOST_RATE_LIMIT_WAIT_SECONDS
             + reconcile.RUNNER_LOST_RATE_LIMIT_WAITS * reconcile.RUNNER_LOST_READ_TIMEOUT)
    assert worst <= 120
    text = (ROOT / ".github" / "workflows" / "reconcile.yml").read_text(encoding="utf-8")
    assert "timeout-minutes: 10" in text
    # The backoff alone, doubled each time, never outruns the bound.
    backoffs = [reconcile.RUNNER_LOST_RATE_LIMIT_BACKOFF * 2 ** n
                for n in range(reconcile.RUNNER_LOST_RATE_LIMIT_WAITS)]
    assert sum(backoffs) <= reconcile.RUNNER_LOST_RATE_LIMIT_WAIT_SECONDS


# --------------------------------------------------------------------------- #
# what does not change                                                         #
# --------------------------------------------------------------------------- #

def test_a_refusal_that_is_not_a_rate_limit_is_not_waited_on(sweep, capsys, monkeypatch):
    monkeypatch.setattr(reconcile, "_default_branch",
                        '{\n  "message": "Bad credentials",\n  "status": "401"\n}')
    state = sweep()
    assert state["sleeps"] == []
    assert state["gh"].calls == []
    line, = _degraded_lines(capsys.readouterr().out)
    assert "unreadable" in line and "not a branch name" in line


def test_a_re_read_refused_for_another_reason_stops_waiting(sweep, capsys):
    """A permission refusal after the wait is no rate limit: no second wait,
    and today's "unreadable" line names what GitHub said."""
    denied = subprocess.CompletedProcess(
        args=[], returncode=1,
        stdout='HTTP/2.0 403 Forbidden\r\n\r\n{"message": "Resource not accessible by integration"}',
        stderr="gh: Resource not accessible by integration (HTTP 403)")
    state = sweep(denied)
    assert state["sleeps"] == [reconcile.RUNNER_LOST_RATE_LIMIT_BACKOFF]
    assert state["dispatched"] == []
    line, = _degraded_lines(capsys.readouterr().out)
    assert "unreadable" in line and "Resource not accessible" in line


def test_a_branch_already_read_is_not_read_again(sweep):
    """The ordinary pass: the cached branch is a name, so no re-read and no
    wait — the step costs exactly the calls it did before."""
    reconcile._default_branch = "main"
    state = sweep()
    assert state["gh"].calls == [] and state["sleeps"] == []
    assert len(state["dispatched"]) == 1


def test_no_other_step_reads_the_branch_differently(sweep):
    """The step's re-read is its own: `default_branch()`, which the other
    steps read, still answers what it answered before (DRE-9214's third
    criterion — no other step's behavior changes)."""
    sweep(answered("main"))
    assert reconcile._default_branch == REFUSED_BODY
    assert reconcile.default_branch() == REFUSED_BODY


def test_the_other_degraded_lines_keep_their_wording():
    reconcile._degraded.clear()
    try:
        reconcile._degrade("unlanded", "the branch listing", "HTTP 403")
        assert reconcile._degraded == [
            "unlanded: the branch listing unreadable — reporting nothing this sweep: HTTP 403"]
    finally:
        reconcile._degraded.clear()
