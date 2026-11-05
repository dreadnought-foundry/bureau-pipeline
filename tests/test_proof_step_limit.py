"""Reconcile's proof steps end at their own limit, green (DRE-9101).

Reconcile's `call / sweep` job ran out its 10-minute limit 38 times between
2026-10-25 and 2026-11-03, each one in a proof step holding a silent wait of
205 to 348 s. `scripts/proof_step_limit.py run` bounds each proof step inside
the step, so a slow pass ends its own step green and the rest of the sweep
still finishes inside the job's limit. This file pins:

  * `run`: a child past its limit is stopped (SIGTERM, then SIGKILL after the
    grace), the two cut lines are printed and the exit is 0; a child that ends
    by itself exits with its own status; `--until` with no time left starts
    nothing and is reported cut;
  * `--alarm`: a cut pass after three cut passes raises
    `proof-dispatch-cut:<slug>` once, at WARN; anything less raises nothing; a
    pass that ends by itself resolves it; card-scoped runs are passed over; a
    look-back or notice call that fails or hangs is `alarm UNKNOWN`;
  * `reconcile.yml`: the three proof steps run through it with 80 s
    (`--alarm`), 30 s and one shared 120 s budget, carry no `timeout-minutes`
    of their own, fit the job's limit, and every step after `Sweep` still
    runs when an earlier one failed.

The step scripts themselves, run in bash against fake scripts, are tested
beside each step's existing tests: tests/test_proof_dispatch.py,
tests/test_proof_record_sweep_step.py and tests/test_proof_overdue_sweep.py.

Run: cd bureau-pipeline && python3 -m pytest tests/test_proof_step_limit.py -v
"""
from __future__ import annotations

import json
import os
import re
import shlex
import stat
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import pipeline_notice  # noqa: E402
import proof_step_limit  # noqa: E402

SCRIPT = ROOT / "scripts" / "proof_step_limit.py"
WORKFLOW = ROOT / ".github" / "workflows" / "reconcile.yml"

DISPATCH = "Dispatch proof runs"
CLOSE = "Close proof records that cannot merge"
OVERDUE = "Proof overdue"

#: The card's observed times outside the three proof steps, the longest of the
#: 83 sweeps read on 2026-11-03: job start to `Sweep`, `Sweep`, and every step
#: after `Proof overdue`.
OBSERVED_BEFORE_SWEEP = 153
OBSERVED_SWEEP = 147
OBSERVED_AFTER = 15

#: Slack for a slow runner on top of a bound the script promises.
SLACK = 3.0


def _sleeper(seconds: float = 30) -> list[str]:
    return [sys.executable, "-c", f"import time; time.sleep({seconds})"]


def _run(*args: str, timeout: float = 60, env: dict | None = None) -> tuple:
    started = time.monotonic()
    done = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True,
                          text=True, timeout=timeout, env=env)
    return done, time.monotonic() - started


def _cut_lines(name: str, limit: int) -> list[str]:
    return [
        f"::warning title=Proof step cut at its limit::{name} was cut at its {limit} s "
        f"limit (DRE-9101); the next full sweep runs the pass again",
        f'proof-step-limit: cut step="{name}" limit={limit}s',
    ]


# --------------------------------------------------------------------------- #
# run                                                                          #
# --------------------------------------------------------------------------- #


def test_a_child_past_its_limit_is_stopped_and_the_pass_ends_green():
    done, took = _run("run", "--step", "Proof overdue", "--limit", "2", "--", *_sleeper())
    assert done.returncode == 0, done.stdout + done.stderr
    assert took < 2 + proof_step_limit.KILL_GRACE + SLACK
    lines = done.stdout.splitlines()
    for line in _cut_lines("Proof overdue", 2):
        assert line in lines


def test_a_child_that_ignores_sigterm_is_killed():
    child = textwrap.dedent("""\
        import signal, sys, time
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        print("ready", flush=True)
        time.sleep(60)
        """)
    done, took = _run("run", "--step", "x", "--limit", "2", "--",
                      sys.executable, "-c", child)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "ready" in done.stdout
    # It waited out the grace, then killed: not stopped by the SIGTERM alone.
    assert took >= 2 + proof_step_limit.KILL_GRACE - 0.5
    assert took < 2 + proof_step_limit.KILL_GRACE + SLACK
    assert 'proof-step-limit: cut step="x" limit=2s' in done.stdout


def test_a_grandchild_is_stopped_with_the_child_so_the_pipe_closes(tmp_path):
    """The step pipes the limiter through `tee`, which ends only when every
    writer of the pipe is gone: a `gh` the proof script started must be
    stopped too, or the step waits on it past its limit."""
    child = (f"import subprocess, sys, time; "
             f"subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
             f"time.sleep(60)")
    command = " ".join(shlex.quote(a) for a in (
        sys.executable, str(SCRIPT), "run", "--step", "x", "--limit", "2", "--",
        sys.executable, "-c", child))
    started = time.monotonic()
    done = subprocess.run(["bash", "-c", f"set -o pipefail; {command} 2>&1 | tee out.log"],
                          cwd=tmp_path, capture_output=True, text=True, timeout=60)
    took = time.monotonic() - started
    assert done.returncode == 0, done.stdout + done.stderr
    assert took < 2 + proof_step_limit.KILL_GRACE + SLACK
    assert 'proof-step-limit: cut step="x" limit=2s' in (tmp_path / "out.log").read_text()


@pytest.mark.parametrize("status", [0, 1, 3])
def test_a_child_that_ends_by_itself_gives_its_own_status(status):
    done, _ = _run("run", "--step", "x", "--limit", "20", "--", sys.executable, "-c",
                   f"import sys; print('said by the child'); sys.exit({status})")
    assert done.returncode == status
    assert "said by the child" in done.stdout
    assert "proof-step-limit: cut" not in done.stdout
    assert "::warning" not in done.stdout


def test_until_already_past_starts_nothing_and_reports_the_pass_cut(tmp_path):
    marker = tmp_path / "started"
    done, took = _run("run", "--step", "Proof overdue / act",
                      "--until", str(int(time.time()) - 5), "--",
                      sys.executable, "-c", f"open({str(marker)!r}, 'w').close()")
    assert done.returncode == 0, done.stdout + done.stderr
    assert not marker.exists()
    assert took < SLACK + 1
    for line in _cut_lines("Proof overdue / act", 0):
        assert line in done.stdout.splitlines()


def test_until_gives_the_command_the_seconds_left():
    done, took = _run("run", "--step", "x", "--until", str(int(time.time()) + 3), "--",
                      *_sleeper())
    assert done.returncode == 0, done.stdout + done.stderr
    assert took < 3 + proof_step_limit.KILL_GRACE + SLACK
    match = re.search(r'^proof-step-limit: cut step="x" limit=(\d+)s$', done.stdout, re.M)
    assert match and int(match.group(1)) in (2, 3)


def test_until_with_time_left_lets_a_quick_command_end_by_itself():
    done, _ = _run("run", "--step", "x", "--until", str(int(time.time()) + 30), "--",
                   sys.executable, "-c", "import sys; sys.exit(4)")
    assert done.returncode == 4


def test_limit_and_until_are_one_or_the_other():
    done, _ = _run("run", "--step", "x", "--limit", "2", "--until", "1", "--", "true")
    assert done.returncode == 2
    done, _ = _run("run", "--step", "x", "--", "true")
    assert done.returncode == 2


# --------------------------------------------------------------------------- #
# --alarm                                                                      #
# --------------------------------------------------------------------------- #

REPO = "acme/widgets"
SLUG = "widgets"
RUN_ID = 1000
READ_TOKEN = "the-read-token"

_FAKE_GH = """\
#!{python}
import json, os, sys, time
here = os.environ["FAKE_GH_DIR"]
with open(os.path.join(here, "calls.jsonl"), "a") as f:
    f.write(json.dumps({{"argv": sys.argv[1:], "token": os.environ.get("GH_TOKEN")}}) + "\\n")
mode = os.environ.get("FAKE_GH_MODE", "")
if mode == "fail":
    sys.stderr.write("HTTP 403: Resource not accessible by integration\\n")
    sys.exit(1)
if mode == "hang":
    time.sleep(60)
if sys.argv[1:3] == ["run", "list"]:
    print(open(os.path.join(here, "runs.json")).read())
elif sys.argv[1:3] == ["run", "view"]:
    print(open(os.path.join(here, "view-" + sys.argv[3] + ".json")).read())
else:
    sys.exit(2)
"""


def _step(name: str, seconds: int | None, conclusion: str = "success") -> dict:
    if seconds is None:
        return {"name": name, "conclusion": "skipped", "status": "completed",
                "startedAt": "2026-11-03T13:00:00Z", "completedAt": "2026-11-03T13:00:00Z"}
    return {"name": name, "conclusion": conclusion, "status": "completed",
            "startedAt": "2026-11-03T13:00:00Z",
            "completedAt": f"2026-11-03T13:{seconds // 60:02d}:{seconds % 60:02d}Z"}


class Alarm:
    """A fake `gh` on PATH serving one look-back, and the notice client
    stubbed: every raise and resolve is recorded, never sent."""

    def __init__(self, tmp_path: Path, monkeypatch):
        self.dir = tmp_path / "gh"
        self.dir.mkdir()
        fake = self.dir / "gh"
        fake.write_text(_FAKE_GH.format(python=sys.executable), encoding="utf-8")
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        self.raised: list = []
        self.resolved: list = []
        self.outcome = {"raise": "raised", "resolve": "resolved"}
        monkeypatch.setenv("PATH", f"{self.dir}:{os.environ['PATH']}")
        monkeypatch.setenv("FAKE_GH_DIR", str(self.dir))
        monkeypatch.delenv("FAKE_GH_MODE", raising=False)
        monkeypatch.setenv("REPO", REPO)
        monkeypatch.setenv("REPO_SLUG", SLUG)
        monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
        monkeypatch.setenv("GITHUB_RUN_ID", str(RUN_ID))
        monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
        monkeypatch.setenv("GITHUB_WORKFLOW_REF",
                           f"{REPO}/.github/workflows/reconcile.yml@refs/heads/main")
        monkeypatch.setenv("GH_READ_TOKEN", READ_TOKEN)
        monkeypatch.setenv("GH_TOKEN", "the-write-token")
        monkeypatch.setattr(pipeline_notice, "raise_notice", self._raise)
        monkeypatch.setattr(pipeline_notice, "resolve_notice", self._resolve)

    def _raise(self, notice, **kwargs):
        self.raised.append({"notice": notice, **kwargs})
        return self.outcome["raise"]

    def _resolve(self, notice, **kwargs):
        self.resolved.append(notice)
        return self.outcome["resolve"]

    def runs(self, previous: list[tuple[int, int | None]], *, current_at: str =
             "2026-11-03T13:06:00Z") -> None:
        """This run in progress, then each `(run id, dispatch seconds)`, newest
        first; `None` seconds is a card-scoped pass whose dispatch skipped."""
        listing = [{"databaseId": RUN_ID, "status": "in_progress", "conclusion": "",
                    "createdAt": current_at}]
        for n, (run_id, seconds) in enumerate(previous):
            listing.append({"databaseId": run_id, "status": "completed",
                            "conclusion": "success",
                            "createdAt": f"2026-11-03T12:{50 - 9 * n:02d}:00Z"})
            view = {"jobs": [{"name": "call / sweep", "steps": [
                _step("Sweep", 60), _step(DISPATCH, seconds), _step(CLOSE, 3)]}]}
            (self.dir / f"view-{run_id}.json").write_text(json.dumps(view))
        (self.dir / "runs.json").write_text(json.dumps(listing))

    def calls(self) -> list[dict]:
        path = self.dir / "calls.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line]


@pytest.fixture
def alarm(tmp_path, monkeypatch):
    return Alarm(tmp_path, monkeypatch)


#: The alarm tests cut a 2 s pass; a previous pass is cut at 2 s or more, and
#: the clean one ran 1 s.
LIMIT = 2
CUT, CLEAN = 85, 1


def _cut_dispatch() -> int:
    return proof_step_limit.main(["run", "--step", DISPATCH, "--limit", str(LIMIT),
                                  "--alarm", "--", *_sleeper()])


def test_a_fourth_cut_pass_in_a_row_raises_one_warn_notice(alarm, capfd):
    alarm.runs([(999, CUT), (998, CUT), (997, CUT)])
    assert _cut_dispatch() == 0
    out = capfd.readouterr().out
    assert len(alarm.raised) == 1
    raised = alarm.raised[0]
    assert raised["notice"] == "proof-dispatch-cut:widgets"
    assert raised["severity"] == "WARN"
    assert raised["title"] == "widgets's proof runs have not been dispatched for 4 sweeps in a row"
    for run_id in (RUN_ID, 999, 998, 997):
        assert f"https://github.com/{REPO}/actions/runs/{run_id}" in raised["body"]
    # Their PT start times: 13:06Z is 5:06 AM PST.
    assert "5:06 AM PT on 2026-11-03" in raised["body"]
    assert "4:50 AM PT on 2026-11-03" in raised["body"]
    assert "no proof run is started while this stands" in raised["body"]
    assert "proof-step-limit: dispatch cut on 4 of the last 4 full passes" in out
    assert alarm.resolved == []


def test_the_look_back_reads_as_the_read_token_over_at_most_twenty_runs(alarm):
    alarm.runs([(999, CUT), (998, CUT), (997, CUT)])
    _cut_dispatch()
    calls = alarm.calls()
    assert calls, "the look-back read nothing"
    assert {c["token"] for c in calls} == {READ_TOKEN}
    listing = calls[0]["argv"]
    assert listing[:2] == ["run", "list"]
    assert listing[listing.index("--limit") + 1] == "20"
    assert listing[listing.index("--workflow") + 1] == "reconcile.yml"
    assert listing[listing.index("--repo") + 1] == REPO
    views = [c["argv"] for c in calls[1:]]
    assert [v[:3] for v in views] == [["run", "view", "999"], ["run", "view", "998"],
                                      ["run", "view", "997"]]
    assert all("jobs" in v for v in views)


def test_a_cut_pass_after_two_cut_and_one_clean_raises_nothing(alarm, capfd):
    alarm.runs([(999, CUT), (998, CLEAN), (997, CUT)])
    assert _cut_dispatch() == 0
    assert alarm.raised == []
    assert alarm.resolved == []
    assert "proof-step-limit: dispatch cut on 3 of the last 4 full passes" in capfd.readouterr().out


def test_a_pass_cut_exactly_at_its_limit_counts_as_cut(alarm):
    alarm.runs([(999, LIMIT), (998, LIMIT), (997, LIMIT)])
    _cut_dispatch()
    assert len(alarm.raised) == 1


def test_fewer_than_three_previous_passes_raise_nothing(alarm, capfd):
    alarm.runs([(999, CUT), (998, CUT)])
    assert _cut_dispatch() == 0
    assert alarm.raised == []
    assert "proof-step-limit: dispatch cut on 3 of the last 3 full passes" in capfd.readouterr().out


def test_card_scoped_runs_are_passed_over(alarm):
    alarm.runs([(999, None), (998, CUT), (997, None), (996, CUT), (995, CUT), (994, CLEAN)])
    _cut_dispatch()
    assert len(alarm.raised) == 1
    body = alarm.raised[0]["body"]
    for run_id in (RUN_ID, 998, 996, 995):
        assert f"/actions/runs/{run_id}" in body
    for run_id in (999, 997, 994):
        assert f"/actions/runs/{run_id}" not in body
    # It stopped reading once it had three passes that ran.
    assert ["run", "view", "994"] not in [c["argv"][:3] for c in alarm.calls()]


def test_a_pass_that_ends_by_itself_resolves_the_notice_and_reads_nothing(alarm):
    alarm.runs([(999, CUT), (998, CUT), (997, CUT)])
    status = proof_step_limit.main(["run", "--step", DISPATCH, "--limit", "20", "--alarm",
                                    "--", sys.executable, "-c", "pass"])
    assert status == 0
    assert alarm.resolved == ["proof-dispatch-cut:widgets"]
    assert alarm.raised == []
    assert alarm.calls() == []


def test_without_alarm_nothing_is_read_or_raised(alarm):
    alarm.runs([(999, CUT), (998, CUT), (997, CUT)])
    status = proof_step_limit.main(["run", "--step", DISPATCH, "--limit", str(LIMIT), "--",
                                    *_sleeper()])
    assert status == 0
    assert alarm.raised == [] and alarm.resolved == [] and alarm.calls() == []


def test_a_gh_that_fails_is_alarm_unknown_and_the_pass_stays_green(alarm, monkeypatch, capfd):
    alarm.runs([(999, CUT), (998, CUT), (997, CUT)])
    monkeypatch.setenv("FAKE_GH_MODE", "fail")
    assert _cut_dispatch() == 0
    out = capfd.readouterr().out
    assert re.search(r"^proof-step-limit: alarm UNKNOWN \(.+\)$", out, re.M), out
    assert alarm.raised == []


def test_a_gh_that_hangs_is_cut_at_the_alarm_timeout(alarm, monkeypatch, capfd):
    alarm.runs([(999, CUT), (998, CUT), (997, CUT)])
    monkeypatch.setenv("FAKE_GH_MODE", "hang")
    monkeypatch.setattr(proof_step_limit, "ALARM_TIMEOUT", 1.0)
    started = time.monotonic()
    assert _cut_dispatch() == 0
    took = time.monotonic() - started
    assert took < LIMIT + 1.0 + SLACK
    out = capfd.readouterr().out
    assert re.search(r"^proof-step-limit: alarm UNKNOWN \(.+\)$", out, re.M), out
    assert alarm.raised == []


def test_the_alarm_timeout_is_fifteen_seconds():
    assert proof_step_limit.ALARM_TIMEOUT == 15


def test_a_notice_call_that_hangs_is_alarm_unknown(alarm, monkeypatch, capfd):
    alarm.runs([(999, CUT), (998, CUT), (997, CUT)])
    monkeypatch.setattr(proof_step_limit, "ALARM_TIMEOUT", 1.0)
    monkeypatch.setattr(pipeline_notice, "raise_notice",
                        lambda *a, **k: time.sleep(10) or "raised")
    started = time.monotonic()
    assert _cut_dispatch() == 0
    assert time.monotonic() - started < LIMIT + 1.0 + SLACK
    assert re.search(r"^proof-step-limit: alarm UNKNOWN \(.+\)$", capfd.readouterr().out, re.M)


@pytest.mark.parametrize("which,outcome", [("raise", "lost"), ("resolve", "resolve-refused")])
def test_a_notice_the_door_did_not_take_is_alarm_unknown(alarm, capfd, which, outcome):
    alarm.runs([(999, CUT), (998, CUT), (997, CUT)])
    alarm.outcome[which] = outcome
    if which == "raise":
        assert _cut_dispatch() == 0
    else:
        assert proof_step_limit.main(["run", "--step", DISPATCH, "--limit", "20", "--alarm",
                                      "--", sys.executable, "-c", "pass"]) == 0
    assert f"proof-step-limit: alarm UNKNOWN (notice {outcome})" in capfd.readouterr().out


def test_a_notice_call_that_raises_is_alarm_unknown(alarm, monkeypatch, capfd):
    alarm.runs([(999, CUT), (998, CUT), (997, CUT)])

    def broken(*a, **k):
        raise RuntimeError("the door fell over")

    monkeypatch.setattr(pipeline_notice, "raise_notice", broken)
    assert _cut_dispatch() == 0
    assert "proof-step-limit: alarm UNKNOWN (" in capfd.readouterr().out


def test_a_failed_pass_that_ends_by_itself_keeps_its_status_through_the_alarm(alarm):
    status = proof_step_limit.main(["run", "--step", DISPATCH, "--limit", "20", "--alarm",
                                    "--", sys.executable, "-c", "import sys; sys.exit(1)"])
    assert status == 1


def test_the_notice_key_and_its_console_kind():
    assert proof_step_limit.notice_key("agent-bureau") == "proof-dispatch-cut:agent-bureau"
    assert pipeline_notice.KEY_PATTERN.match(proof_step_limit.notice_key("agent-bureau"))


# --------------------------------------------------------------------------- #
# reconcile.yml                                                                #
# --------------------------------------------------------------------------- #


def _job() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["sweep"]


def _step_named(name: str) -> dict:
    found = [s for s in _job()["steps"] if s.get("name") == name]
    assert len(found) == 1, name
    return found[0]


def _limited(run: str) -> list[list[str]]:
    """Each `proof_step_limit.py run …` command in a step's script, as argv,
    up to its redirection."""
    commands = []
    for line in run.splitlines():
        if "proof_step_limit.py" not in line:
            continue
        commands.append(shlex.split(line.split("2>&1")[0]))
    return commands


def _flag(argv: list[str], name: str) -> str | None:
    head = argv[:argv.index("--")]
    return head[head.index(name) + 1] if name in head else None


def _child(argv: list[str]) -> list[str]:
    return argv[argv.index("--") + 1:]


def test_dispatch_proof_runs_runs_its_script_at_80_s_with_the_alarm():
    commands = _limited(_step_named(DISPATCH)["run"])
    assert len(commands) == 1
    argv = commands[0]
    assert argv[:3] == ["python3", ".bureau-pipeline/scripts/proof_step_limit.py", "run"]
    assert _flag(argv, "--step") == DISPATCH
    assert _flag(argv, "--limit") == "80"
    assert "--alarm" in argv[:argv.index("--")]
    assert _child(argv) == ["python3", ".bureau-pipeline/scripts/proof_dispatch.py"]


def test_close_proof_records_runs_its_script_at_30_s():
    commands = _limited(_step_named(CLOSE)["run"])
    assert len(commands) == 1
    argv = commands[0]
    assert _flag(argv, "--step") == CLOSE
    assert _flag(argv, "--limit") == "30"
    assert "--alarm" not in argv[:argv.index("--")]
    assert _child(argv) == ["python3", ".bureau-pipeline/scripts/proof_record_pr.py",
                            "sweep", "--repo", "$REPO"]


def test_proof_overdue_shares_one_120_s_budget_between_its_two_commands():
    run = _step_named(OVERDUE)["run"]
    budgets = re.findall(r"^\s*UNTIL=\$\(\( \$\(date \+%s\) \+ (\d+) \)\)\s*$", run, re.M)
    assert budgets == ["120"]
    commands = _limited(run)
    assert [_child(c)[:2] for c in commands] == [
        ["python3", ".bureau-pipeline/scripts/proof_overdue_sweep.py"],
        ["python3", ".bureau-pipeline/scripts/proof_overdue_act.py"]]
    for argv in commands:
        assert _flag(argv, "--until") == "$UNTIL"
        assert _flag(argv, "--limit") is None
    # The budget is set once, before the first command spends any of it.
    assert run.index("UNTIL=") < run.index("proof_step_limit.py")


def test_every_proof_command_in_the_three_steps_runs_through_the_limit():
    for name in (DISPATCH, CLOSE, OVERDUE):
        run = _step_named(name)["run"]
        bare = [line for line in run.splitlines()
                if re.search(r"python3 \S*scripts/proof_\w+\.py", line)
                and "proof_step_limit.py" not in line]
        assert bare == [], (name, bare)


def test_the_three_steps_carry_no_timeout_of_their_own():
    for name in (DISPATCH, CLOSE, OVERDUE):
        assert "timeout-minutes" not in _step_named(name), name


def _limit_seconds() -> int:
    total = 0
    for name in (DISPATCH, CLOSE):
        total += int(_flag(_limited(_step_named(name)["run"])[0], "--limit"))
    total += int(re.search(r"UNTIL=\$\(\( \$\(date \+%s\) \+ (\d+) \)\)",
                           _step_named(OVERDUE)["run"]).group(1))
    return total


def test_the_limits_and_the_observed_rest_fit_inside_the_jobs_limit():
    limits = _limit_seconds()
    assert limits == 230
    grace = 3 * proof_step_limit.KILL_GRACE
    assert grace == 15
    alarm = proof_step_limit.ALARM_TIMEOUT
    observed = OBSERVED_BEFORE_SWEEP + OBSERVED_SWEEP + OBSERVED_AFTER
    assert observed == 315
    assert limits + grace + alarm + observed <= _job()["timeout-minutes"] * 60
    assert _job()["timeout-minutes"] == 10


def test_every_step_after_the_sweep_runs_when_an_earlier_one_failed():
    steps = _job()["steps"]
    names = [s.get("name") for s in steps]
    after = steps[names.index("Sweep") + 1:]
    assert after, "nothing runs after the Sweep step"
    for step in after:
        condition = step.get("if") or ""
        assert "!cancelled()" in condition or "always()" in condition, step.get("name")


def _comment_above(name: str) -> str:
    """The comment block right above `- name: <name>` in the raw workflow."""
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    at = next(i for i, line in enumerate(lines) if line.strip() == f"- name: {name}")
    block = []
    for line in reversed(lines[:at]):
        if not line.strip().startswith("#"):
            break
        block.append(line.strip().lstrip("#").strip())
    return " ".join(reversed(block))


@pytest.mark.parametrize("name,limit", [(DISPATCH, "80 s"), (CLOSE, "30 s"),
                                        (OVERDUE, "120 s")])
def test_the_comment_above_each_step_gives_its_limit_and_names_the_card(name, limit):
    comment = _comment_above(name)
    assert "DRE-9101" in comment, comment
    assert limit in comment, comment


def test_the_summaries_lift_the_limit_lines():
    for name in (DISPATCH, CLOSE, OVERDUE):
        grep = next(line for line in _step_named(name)["run"].splitlines()
                    if line.strip().startswith("SUMMARY="))
        assert "proof-step-limit:" in grep, name
