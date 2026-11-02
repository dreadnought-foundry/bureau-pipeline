"""No automatic re-run goes back onto the runner service a job just failed on
(DRE-8817).

The CEO's rule of 2026-11-01: when a job fails or hangs, a retry runs on the
next runner service in the failover order, never the one that just failed it.
`scripts/runner_service.py` is the one rule the three retry paths ask —
`canceled_check` through the sweep, `hung_job` and the medic's `decide`:

  * `column_of` — which of the three `runs-on:` chains a run's workflow file
    reads (`ci`, `short`, `long`), or None;
  * `service_of` — which service a job ran on, off its Jobs API record;
  * `services_named` — which services a column's effective value names;
  * `bad_end` — a hang, an out-of-memory kill or a lost runner;
  * `gate` — held or allowed, and why.

Run: python3 -m pytest tests/test_runner_service.py -v
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"

import sys  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts"))

import runner_service  # noqa: E402

CI = "${{ fromJSON(vars.BUREAU_CI_RUNS_ON || vars.BUREAU_RUNS_ON || '[\"ubuntu-latest\"]') }}"
SHORT = "${{ fromJSON(vars.BUREAU_SHORT_RUNS_ON || vars.BUREAU_RUNS_ON || '[\"ubuntu-latest\"]') }}"
LONG = "${{ fromJSON(vars.BUREAU_RUNS_ON || '[\"ubuntu-latest\"]') }}"

KILL_LOG = ("2026-11-01T02:10:00.0000000Z Run the suite\n"
            "2026-11-01T02:11:00.0000000Z ##[error]Process completed with exit code 137.\n")
LIMIT_NOTE = {"message": "The job has exceeded the maximum execution time of 40m0s"}
LOST_NOTE = {"message": "The self-hosted runner: bureau-studio-x lost communication "
                        "with the server. Verify the machine is running and has a "
                        "healthy network connection."}


def workflow(*runs_on: str, extra: str = "") -> str:
    jobs = "".join(
        f"  job{n}:\n    runs-on: {value}\n    steps:\n      - run: echo {n}\n"
        for n, value in enumerate(runs_on))
    return f"name: CI\non: [push]\njobs:\n{jobs}{extra}"


# --- the column a run's jobs read --------------------------------------------

@pytest.mark.parametrize("line, column", [(CI, "ci"), (SHORT, "short"), (LONG, "long")])
def test_each_chain_reads_as_its_column(line, column):
    assert runner_service.column_of(workflow(line)) == column
    assert runner_service.column_of(workflow(line, line)) == column


def test_whitespace_and_a_trailing_comment_are_not_part_of_the_line():
    spaced = "${{  fromJSON( vars.BUREAU_CI_RUNS_ON||vars.BUREAU_RUNS_ON || '[\"ubuntu-latest\"]' ) }}"
    assert runner_service.column_of(workflow(spaced + "   # the build lane")) == "ci"


@pytest.mark.parametrize("text", [
    workflow("ubuntu-latest"),
    workflow("[self-hosted, bureau-light]"),
    workflow("${{ matrix.os }}"),
    workflow(CI, SHORT),
    workflow(CI, "ubuntu-latest"),
    workflow(CI, extra="  call:\n    uses: ./.github/workflows/groomer.yml\n"),
    workflow(extra="  call:\n    uses: dreadnought-foundry/bureau-pipeline/.github/"
                   "workflows/agent-task.yml@main\n"),
    "name: CI\non: [push]\njobs: {}\n",
    "",
], ids=["literal", "literal-list", "matrix", "two-columns", "column-and-literal",
        "reusable-call", "stub", "no-runs-on", "empty"])
def test_anything_else_reads_no_column(text):
    assert runner_service.column_of(text) is None


def test_a_step_using_an_action_is_not_a_reusable_call():
    text = workflow(CI) + "      - uses: actions/checkout@v7\n"
    assert runner_service.column_of(text) == "ci"


def test_no_column_says_why():
    column, why = runner_service.read_column(workflow(CI, SHORT))
    assert column is None and "two" in why
    column, why = runner_service.read_column(
        workflow(extra="  call:\n    uses: ./.github/workflows/groomer.yml\n"))
    assert column is None and "reusable" in why
    column, why = runner_service.read_column("jobs: {}\n")
    assert column is None and "no runs-on" in why


@pytest.mark.parametrize("name, column", [
    ("tests.yml", "ci"),
    ("smoke-setup-node-cached.yml", "ci"),
    ("smoke-setup-python-cached.yml", "ci"),
    ("hygiene.yml", "short"),
    ("release-gate.yml", "short"),
    ("promote-channel.yml", "short"),
    ("nightly-watch.yml", "short"),
    ("channel-watch.yml", "short"),
    ("harness.yml", "long"),
    ("self-agent-task.yml", None),
    ("triage-repair.yml", None),
    ("groom-replay-gate.yml", None),
    ("self-linear-sync.yml", None),
])
def test_this_repos_own_workflow_files(name, column):
    text = (WORKFLOWS / name).read_text(encoding="utf-8")
    assert runner_service.column_of(text) == column


# --- the service a job ran on -------------------------------------------------

@pytest.mark.parametrize("job, service", [
    ({"runner_name": "runs-on-1", "runner_group_name": "RunsOn"}, "runs-on"),
    ({"runner_name": "x", "runner_group_name": "RunsOn"}, "runs-on"),
    ({"runner_name": "runs-on-41379620566-abc"}, "runs-on"),
    ({"runner_name": "i-0abc", "labels": ["runs-on"]}, "runs-on"),
    ({"runner_name": "i-0abc", "labels": ["runs-on=41379620566/runner=4cpu-linux-x64"]},
     "runs-on"),
    # The form the fleet's jobs carry (docs/red-main-proof-2026-10.md).
    ({"runner_name": "", "labels": ["runs-on/runner=2cpu-linux-arm64/spot=false"]},
     "runs-on"),
    ({"runner_name": "bureau-studio-sDcV8SdrTXe9n"}, "mac-studio"),
    ({"runner_name": "bureau-studio-light-1"}, "mac-studio"),
    ({"runner_name": "bureau-mini-light-3"}, "mac-mini"),
    ({"runner_name": "bureau-mini-PQk8rgNMXNXGn"}, "mac-mini"),
    ({"runner_name": "GitHub Actions 12"}, "github-hosted"),
], ids=["runs-on-group", "runs-on-group-any-name", "runs-on-name", "runs-on-label",
        "runs-on-label-value", "runs-on-label-path", "studio", "studio-light",
        "mini-light", "mini-heavy", "hosted"])
def test_each_job_record_row_names_its_service(job, service):
    assert runner_service.service_of(job) == service


@pytest.mark.parametrize("job", [
    {"runner_name": "my-laptop"},
    {"runner_name": ""},
    {"runner_name": None},
    {},
    {"runner_name": "studio-bureau-1"},
], ids=["other", "empty", "none", "no-field", "not-a-prefix"])
def test_a_job_record_that_matches_no_row_is_unknown(job):
    assert runner_service.service_of(job) == "unknown"


# --- the services a column value names ----------------------------------------

@pytest.mark.parametrize("value, services", [
    ('["runs-on"]', {"runs-on"}),
    ('["runs-on=${{ github.run_id }}/runner=4cpu-linux-x64"]', {"runs-on"}),
    # The forms the fleet's columns read back (tests/test_plan_proof_reach.py).
    ('["runs-on/runner=4cpu-linux-arm64"]', {"runs-on"}),
    ('["runs-on/runner=2cpu-linux-x64/spot=false"]', {"runs-on"}),
    ('["self-hosted", "mac-studio"]', {"mac-studio"}),
    ('["self-hosted", "bureau-light", "machine-studio-1"]', {"mac-studio"}),
    ('["self-hosted", "mac-mini"]', {"mac-mini"}),
    ('["self-hosted", "bureau-heavy", "machine-mini-1"]', {"mac-mini"}),
    ('["self-hosted", "bureau-light"]', {"mac-mini", "mac-studio"}),
    ('["ubuntu-latest"]', {"github-hosted"}),
    ('["ubuntu-24.04", "macos-15"]', {"github-hosted"}),
    ('["windows-latest"]', {"github-hosted"}),
], ids=["runs-on", "runs-on-value", "runs-on-path", "runs-on-path-spot", "studio",
        "machine-studio", "mini",
        "machine-mini", "either-mac", "ubuntu", "ubuntu-macos", "windows"])
def test_each_column_value_row_names_its_services(value, services):
    assert runner_service.services_named(value) == frozenset(services)


@pytest.mark.parametrize("value", [
    '["bureau-light"]',
    '["ubuntu-latest", "bureau-light"]',
    '"ubuntu-latest"',
    "[]",
    '[1, 2]',
    "not json",
    "",
    None,
], ids=["label-only", "hosted-and-other", "string", "empty-list", "not-strings",
        "not-json", "empty", "none"])
def test_a_column_value_that_matches_no_row_is_unknown(value):
    assert runner_service.services_named(value) == frozenset({"unknown"})


# --- the bad ends -------------------------------------------------------------

def test_a_job_canceled_at_its_own_limit_is_a_hang():
    job = {"conclusion": "cancelled"}
    assert runner_service.bad_end(job, [LIMIT_NOTE]) == runner_service.HANG


def test_a_job_canceled_without_a_limit_is_no_bad_end():
    job = {"conclusion": "cancelled"}
    assert runner_service.bad_end(job, [{"message": "The operation was canceled."}]) is None


def test_a_job_whose_log_carries_the_kill_line_is_an_out_of_memory_kill():
    job = {"conclusion": "failure"}
    assert runner_service.bad_end(job, [], KILL_LOG) == runner_service.OUT_OF_MEMORY


def test_a_job_that_lost_its_runner_is_a_lost_runner():
    job = {"conclusion": "failure"}
    assert runner_service.bad_end(job, [LOST_NOTE]) == runner_service.LOST_RUNNER


def test_an_ordinary_failure_is_no_bad_end():
    job = {"conclusion": "failure"}
    notes = [{"message": "Process completed with exit code 1."}]
    assert runner_service.bad_end(job, notes, "FAILED tests/x.py::test_y\n") is None


# --- the decision -------------------------------------------------------------

STUDIO = {"name": "Console backend shard 3", "conclusion": "cancelled",
          "runner_name": "bureau-studio-sDcV8SdrTXe9n"}
COLUMNS = {"ci": '["self-hosted", "mac-studio"]', "short": '["runs-on"]',
           "long": '["ubuntu-latest"]'}


def test_no_column_is_allowed_and_says_so():
    answer = runner_service.gate([(STUDIO, runner_service.HANG)], None, COLUMNS)
    assert answer.allow is True
    assert "no single" in answer.why


def test_a_hang_on_the_service_the_column_still_names_is_held():
    allow, service, why = runner_service.gate([(STUDIO, runner_service.HANG)], "ci", COLUMNS)
    assert allow is False and service == "mac-studio"
    for part in ("Console backend shard 3", "bureau-studio-sDcV8SdrTXe9n", "mac-studio",
                 "`ci`", COLUMNS["ci"]):
        assert part in why, part


def test_a_hang_on_a_service_the_column_no_longer_names_is_allowed():
    columns = dict(COLUMNS, ci='["runs-on=1/runner=4cpu-linux-x64"]')
    answer = runner_service.gate([(STUDIO, runner_service.HANG)], "ci", columns)
    assert answer.allow is True and answer.service == "mac-studio"


def test_a_hang_on_mac_studio_is_allowed_once_the_column_reads_the_fleets_runs_on_form():
    columns = dict(COLUMNS, ci='["runs-on/runner=4cpu-linux-arm64"]')
    answer = runner_service.gate([(STUDIO, runner_service.HANG)], "ci", columns)
    assert answer.allow is True and answer.service == "mac-studio"
    assert "names runs-on" in answer.why


def test_the_runs_column_is_compared_never_ci():
    mini = {"name": "hygiene", "conclusion": "cancelled", "runner_name": "bureau-mini-light-1"}
    moved = {"ci": '["self-hosted", "mac-mini"]', "short": '["runs-on"]', "long": "[]"}
    assert runner_service.gate([(mini, runner_service.HANG)], "short", moved).allow is True
    reverse = {"ci": '["runs-on"]', "short": '["self-hosted", "mac-mini"]', "long": "[]"}
    assert runner_service.gate([(mini, runner_service.HANG)], "short", reverse).allow is False


def test_an_unknown_service_holds():
    job = {"name": "x", "conclusion": "cancelled", "runner_name": "my-laptop"}
    answer = runner_service.gate([(job, runner_service.HANG)], "ci", dict(COLUMNS, ci='["runs-on"]'))
    assert answer.allow is False and answer.service == "unknown"


def test_an_unknown_column_value_holds():
    for value in ("", "not json", '["bureau-light"]'):
        answer = runner_service.gate([(STUDIO, runner_service.HANG)], "ci",
                                     dict(COLUMNS, ci=value))
        assert answer.allow is False, value


def test_a_missing_column_value_holds():
    answer = runner_service.gate([(STUDIO, runner_service.HANG)], "ci", {})
    assert answer.allow is False


def test_a_failure_with_no_bad_end_is_allowed_wherever_it_ran():
    answer = runner_service.gate([(STUDIO, None)], "ci", COLUMNS)
    assert answer.allow is True


def test_either_mac_pool_holds_a_job_on_either_mac():
    columns = dict(COLUMNS, ci='["self-hosted", "bureau-light"]')
    mini = dict(STUDIO, runner_name="bureau-mini-light-1")
    for job in (STUDIO, mini):
        assert runner_service.gate([(job, runner_service.HANG)], "ci", columns).allow is False


def test_the_columns_are_read_from_the_three_env_vars():
    env = {"CI_RUNS_ON": "a", "SHORT_RUNS_ON": "b", "LONG_RUNS_ON": "c", "OTHER": "d"}
    assert runner_service.columns_from_env(env) == {"ci": "a", "short": "b", "long": "c"}
    assert runner_service.columns_from_env({}) == {"ci": "", "short": "", "long": ""}


def test_an_unread_answer_is_neither_allowed_nor_held():
    answer = runner_service.unread("the jobs could not be read")
    assert answer.allow is None and "could not be read" in answer.why


def test_the_rule_makes_no_network_call():
    tree = ast.parse((ROOT / "scripts" / "runner_service.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert not imported & {"subprocess", "urllib", "http", "socket", "requests",
                           "reconcile", "linear_ops"}, imported


def test_the_docstring_names_both_open_gaps():
    doc = ast.get_docstring(ast.parse(
        (ROOT / "scripts" / "runner_service.py").read_text(encoding="utf-8"))) or ""
    assert "minutes" in doc and "head_sha" in doc


def test_the_columns_are_the_three_pinned_chains_byte_for_byte():
    sys.path.insert(0, str(ROOT / "tests"))
    import test_runs_on_switchable as pinned  # noqa: PLC0415

    assert runner_service.CHAINS == {"ci": pinned.BUILD_SWITCHABLE,
                                     "short": pinned.SHORT_SWITCHABLE,
                                     "long": pinned.SWITCHABLE}
    assert runner_service.CHAINS == {"ci": CI, "short": SHORT, "long": LONG}
    json.dumps(runner_service.CHAINS)  # plain strings


# --- the replay: agent-bureau #4499, 2026-11-01 -------------------------------
#
# Attempt 1 of CI run 41379620566 hung on RunsOn; the canceled-check re-run sent
# it to studio-1, where it hung again, because the order had put agent-bureau's
# CI column back on mac-studio at 7:47 PM PT. Driven through the sweep
# (`reconcile.fix_approved_but_red` → `canceled_check.decide`, the reads faked
# as `_tend_canceled_runs` makes them) and through `hung_job`'s decision.

import os  # noqa: E402
from datetime import UTC, datetime, timedelta  # noqa: E402
from unittest import mock  # noqa: E402

os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/test")
os.environ.setdefault("GH_TOKEN", "test")

import canceled_check  # noqa: E402
import hung_job  # noqa: E402
import pipeline_act  # noqa: E402
import reconcile  # noqa: E402

REPLAY = ROOT / "tests" / "fixtures" / "runner-service-replay-4499.json"
WORKER = "agent-bureau-bot"


def replay() -> dict:
    return json.loads(REPLAY.read_text(encoding="utf-8"))


def test_the_replays_readme_names_the_source_and_its_placeholders():
    readme = replay()["_readme"]
    for part in ("#4499", "41379620566", "125088332790", "PLACEHOLDERS", "head_sha",
                 "runner_name", "columns", "workflow_text"):
        assert part in readme, part
    assert runner_service.column_of(replay()["workflow_text"]) == "ci"


def _as_attempt_one(fx: dict, attempt: str) -> dict:
    """The hang of attempt `attempt`, presented as an attempt-1 record — the
    shape a pass reads before any re-run."""
    return fx["attempts"][attempt]


class ReplaySweep:
    """One pass of `fix_approved_but_red` over #4499's head."""

    def __init__(self, attempt: str, ci: str, notes=()):
        self.fx = fx = replay()
        self.hang = _as_attempt_one(fx, attempt)
        job = self.hang["jobs"][0]
        run_id = fx["run"]["id"]
        self.check_runs = [
            {"id": job["id"], "name": job["name"], "status": "completed",
             "conclusion": "cancelled", "completed_at": job["completed_at"],
             "details_url": f"https://github.com/{fx['repo']}/actions/runs/{run_id}"
                            f"/job/{job['id']}"},
        ]
        comments = [{"author": {"login": "agent-bureau-qa-bot"},
                     "body": f"QA Critic\nVERDICT: APPROVE @{fx['head_sha']}"}]
        comments += [{"author": {"login": WORKER}, "body": body, "createdAt": at}
                     for body, at in notes]
        self.pr = {"number": fx["pr"], "headRefName": fx["head_branch"],
                   "headRefOid": fx["head_sha"], "mergeStateStatus": "BLOCKED",
                   "comments": comments}
        self.columns = {"CI_RUNS_ON": ci, "SHORT_RUNS_ON": "[]", "LONG_RUNS_ON": "[]"}
        self.reruns: list = []
        self.posted: list = []
        self.fixes: list = []

    def gh(self, *args):
        joined = " ".join(args)
        run_id = self.fx["run"]["id"]
        if args[:2] == ("pr", "list"):
            return json.dumps([self.pr])
        if "/contents/" in joined:
            assert f"contents/{self.fx['workflow_path']}?ref={self.fx['head_sha']}" in joined
            return self.fx["workflow_text"]
        if f"actions/runs/{run_id}/attempts/1/jobs" in joined:
            return json.dumps({"total_count": 1, "jobs": self.hang["jobs"]})
        if "/annotations" in joined:
            for job, notes in self.hang["annotations"].items():
                if f"check-runs/{job}/annotations" in joined:
                    return json.dumps(notes)
            return "[]"
        if "/logs" in joined:
            return self.hang["log"]
        if "/check-runs" in joined:
            return json.dumps([{"total_count": 1, "check_runs": self.check_runs}])
        if f"actions/runs/{run_id}" in joined:
            return json.dumps(dict(self.fx["run"], head_sha=self.fx["head_sha"]))
        return ""

    def dispatch(self, *args):
        (self.reruns if args[:2] == ("run", "rerun") else self.fixes).append(args)

    def run(self) -> "ReplaySweep":
        with mock.patch.dict(os.environ, {**self.columns, "GH_DISPATCH_TOKEN": ""}), \
                mock.patch.object(reconcile, "gh", side_effect=self.gh), \
                mock.patch.object(reconcile, "gh_dispatch", side_effect=self.dispatch), \
                mock.patch.object(reconcile, "_post_pr_note",
                                  side_effect=lambda n, b: self.posted.append(b) or True), \
                mock.patch.object(reconcile, "_actions_runs_busy", return_value=False), \
                mock.patch.object(reconcile, "fix_dispatch_blocked", return_value=False), \
                mock.patch.object(reconcile, "fix_agent_absent_hold", return_value=False), \
                mock.patch.object(reconcile, "_write_failures", []):
            reconcile.fix_approved_but_red()
        return self


class ReplayGitHub:
    """`hung_job.GitHub` for #4499's records, presented as a `Pipeline Tests`
    push run on main — the only run `hung_job` watches. The usual time comes
    off ten green runs of the same job, twelve minutes each."""

    def __init__(self, attempt: str):
        fx = replay()
        self.hang = _as_attempt_one(fx, attempt)
        job = self.hang["jobs"][0]
        self.record = dict(fx["run"], name=hung_job.WORKFLOW, event=hung_job.EVENT,
                           head_branch=hung_job.BRANCH, created_at=job["started_at"],
                           updated_at=job["completed_at"])
        self.reruns: list = []

    def watched(self):
        return [self.record]

    def jobs(self, run_id, attempt=None):
        if attempt is None:  # a green run of the history
            return [{"name": self.hang["jobs"][0]["name"], "conclusion": "success",
                     "started_at": "2026-11-01T10:00:00Z",
                     "completed_at": "2026-11-01T10:12:00Z"}]
        return self.hang["jobs"]

    def history(self, before):
        for n in range(10):
            yield n, self.jobs(n)

    def annotations(self, job_id):
        return self.hang["annotations"][str(job_id)]

    def log(self, job_id):
        return self.hang["log"]

    def rerun(self, run_id):
        self.reruns.append(run_id)


def _watch(attempt: str, ci: str, hours_after: float) -> tuple:
    github = ReplayGitHub(attempt)
    finished = datetime.fromisoformat(github.hang["jobs"][0]["completed_at"]
                                      .replace("Z", "+00:00"))
    lines: list = []
    with mock.patch.dict(os.environ, {"CI_RUNS_ON": ci}):
        hung_job.watch(github, finished + timedelta(hours=hours_after), True, lines.append)
    return github.reruns, lines


def _held_note(body: str, hours_ago: float) -> tuple:
    at = datetime.now(UTC) - timedelta(hours=hours_ago)
    return body, at.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_replay_attempt_1_hung_on_runs_on_while_ci_named_mac_studio_is_re_run():
    fx = replay()
    sweep = ReplaySweep("1", fx["columns"]["mac_studio"]).run()
    assert len(sweep.reruns) == 1 and sweep.fixes == []
    assert (pipeline_act.read_trailer(sweep.posted[0]) or {}).get("act") == \
        canceled_check.RERUN_ACT
    reruns, _lines = _watch("1", fx["columns"]["mac_studio"], 0.5)
    assert reruns == [fx["run"]["id"]]


def test_replay_the_same_hang_on_studio_1_while_ci_names_mac_studio_is_held():
    fx = replay()
    sweep = ReplaySweep("2", fx["columns"]["mac_studio"]).run()
    assert sweep.reruns == [] and sweep.fixes == [] and len(sweep.posted) == 1
    body = sweep.posted[0]
    assert body.startswith(f"🛑 canceled-check-again @{fx['head_sha']} "
                           f"run {fx['run']['id']} held:")
    for part in ("mac-studio", "bureau-studio-sDcV8SdrTXe9n", "`ci`",
                 fx["columns"]["mac_studio"], "Console backend shard 3"):
        assert part in body, part
    reruns, lines = _watch("2", fx["columns"]["mac_studio"], 0.5)
    assert reruns == []
    assert any(x.startswith("hung-job: held ") and "bureau-studio-sDcV8SdrTXe9n" in x
               for x in lines)


def test_replay_the_same_hang_on_studio_1_after_ci_moved_to_runs_on_is_re_run():
    fx = replay()
    held = ReplaySweep("2", fx["columns"]["mac_studio"]).run().posted[0]
    sweep = ReplaySweep("2", fx["columns"]["runs_on"], notes=[_held_note(held, 1)]).run()
    assert len(sweep.reruns) == 1 and len(sweep.posted) == 1
    assert (pipeline_act.read_trailer(sweep.posted[0]) or {}).get("act") == \
        canceled_check.RERUN_ACT
    reruns, _lines = _watch("2", fx["columns"]["runs_on"], 1)
    assert reruns == [fx["run"]["id"]]


def test_replay_seven_hours_after_the_held_receipt_nothing_is_re_run_or_posted():
    fx = replay()
    held = ReplaySweep("2", fx["columns"]["mac_studio"]).run().posted[0]
    sweep = ReplaySweep("2", fx["columns"]["mac_studio"], notes=[_held_note(held, 7)]).run()
    assert sweep.reruns == [] and sweep.posted == [] and sweep.fixes == []
    reruns, _lines = _watch("2", fx["columns"]["mac_studio"], 7)
    assert reruns == []
