"""promote-channel records what it did to `stable` (DRE-5214).

The console's Train Yard is built from two kinds of GitHub deployment, and
until this card bureau-pipeline posted neither: `promote-channel.yml` moved
`stable` 87 times in a week and the deployments list stayed empty, so the
pipeline was the one train nobody could see.

`scripts/channel_record.py` writes the two records, and nothing here needs a
token or a runner — both go through `release_decision.gh`, the one seam a test
replaces. What this file pins:

  1. every evaluation records ONE release-train decision through
     `release_decision.record` / `write`, surface `pipeline-channel`, phase
     `release`, the run's own id — for all three acts, and each passes
     `release_decision.check_record`;
  2. a `held` decision names the gating run and its failing scenarios, in the
     words the `## Did not promote` summary already uses;
  3. a tag move posts one `release` deployment plus one status, field for
     field against the contract the console's `record_releases.py` reads
     (DRE-5099's, plus `from_sha` / `to_sha`);
  4. a refused or failing post never changes the exit code, and the workflow
     is wired so it cannot change whether `stable` moves.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import channel_record  # noqa: E402
import release_decision  # noqa: E402

WORKFLOW = ROOT / ".github" / "workflows" / "promote-channel.yml"
RELEASE_JSON = ROOT / ".github" / "bureau" / "release.json"

REPO = "dreadnought-foundry/bureau-pipeline"
FROM = "1111111aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
TO = "2222222bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
GATE = "https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/777"
NOW = datetime(2026, 9, 29, 17, 4, 9, tzinfo=timezone.utc)

ENV = {
    "GITHUB_SERVER_URL": "https://github.com",
    "GITHUB_REPOSITORY": REPO,
    "GITHUB_RUN_ID": "9001",
    "GITHUB_RUN_ATTEMPT": "1",
    "GITHUB_EVENT_NAME": "workflow_run",
}


class FakeGh:
    """The `release_decision.gh` seam: records every POST, answers an id."""

    def __init__(self, fail_on: str | None = None):
        self.posts: list[tuple[str, dict]] = []
        self.fail_on = fail_on

    def __call__(self, path: str, body: dict) -> dict:
        # Round-trip through JSON, exactly as `gh api --input -` receives it,
        # so a body that is not real JSON fails here and not on GitHub.
        body = json.loads(json.dumps(body))
        self.posts.append((path, body))
        if self.fail_on and self.fail_on in path:
            raise RuntimeError("gh: Resource not accessible by integration (HTTP 403)")
        return {"id": 4242}


@pytest.fixture
def env(monkeypatch):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    return ENV


def _run(argv, monkeypatch, gh):
    monkeypatch.setattr(release_decision, "gh", gh)
    return channel_record.main(["--repo", REPO, *argv])


def _decision(monkeypatch, gh, **given):
    args = {
        "--promoted": "false",
        "--outcome": "",
        "--reason": "",
        "--ancestry": "",
        "--candidate": TO,
        "--from-sha": FROM,
        "--gating-run": GATE,
        "--scenarios": "none",
    }
    args.update({f"--{k.replace('_', '-')}": v for k, v in given.items()})
    argv = ["decision"]
    for key, value in args.items():
        argv += [key, value]
    return _run(argv, monkeypatch, gh)


# --------------------------------------------------------------------------
# 1. The contract strings are the ones the agent-bureau cards read.
# --------------------------------------------------------------------------

def test_the_contract_strings_are_spelled_exactly():
    assert channel_record.SURFACE == "pipeline-channel"
    assert channel_record.ENVIRONMENT == "pipeline-channel"
    assert channel_record.TASK == "release"
    assert channel_record.version(TO) == "stable@2222222"
    # The surface is the one this repo already declares for the channel.
    declared = json.loads(RELEASE_JSON.read_text())["surfaces"]
    assert channel_record.SURFACE in declared
    assert declared[channel_record.SURFACE]["record"] == "channel"


# --------------------------------------------------------------------------
# 2. One decision per evaluation, for each of the three acts.
# --------------------------------------------------------------------------

def _only_decision(gh: FakeGh) -> dict:
    paths = [path for path, _ in gh.posts]
    assert paths == [f"repos/{REPO}/deployments",
                     f"repos/{REPO}/deployments/4242/statuses"], paths
    deployment = gh.posts[0][1]
    assert deployment["task"] == release_decision.TASK, (
        "the decision goes through release_decision, the train's own record")
    assert deployment["environment"] == release_decision.ENVIRONMENT
    record = deployment["payload"]
    assert release_decision.check_record(record) == [], record
    status = gh.posts[1][1]
    assert release_decision.check_record(
        dict(record, state=status["state"],
             description=deployment["description"])) == []
    assert record["surface"] == "pipeline-channel"
    assert record["phase"] == "release"
    assert record["run_id"] == 9001
    assert record["repo"] == REPO
    return record


def test_a_promotion_records_a_release_decision(env, monkeypatch):
    gh = FakeGh()
    code = _decision(monkeypatch, gh, promoted="true",
                     outcome="harness-passed-promoting", ancestry="ahead",
                     reason=f"promoting stable to {TO} — harness green, strictly ahead.")
    assert code == 0
    record = _only_decision(gh)
    assert record["act"] == "release"
    assert record["code"] == "released"
    assert record["sha"] == TO
    assert record["version"] == "stable@2222222"
    assert record["deployed"] == "stable@1111111"
    # The new sha and the sha it moved from, both in full.
    assert FROM in record["reason"] and TO in record["reason"]


def test_a_decline_records_held_naming_the_gating_run_and_its_scenarios(
        env, monkeypatch):
    gh = FakeGh()
    code = _decision(monkeypatch, gh, promoted="false",
                     outcome="harness-failed",
                     reason=f"not promoting {TO}: the harness run failed "
                            f"(conclusion=failure).",
                     scenarios="card_to_pr,merge_gate")
    assert code == 0
    record = _only_decision(gh)
    assert record["act"] == "held"
    assert record["code"] == "channel-blocked"
    assert record["version"] is None
    assert record["deployed"] == "stable@1111111"
    reason = record["reason"]
    # The `## Did not promote` summary's own words.
    assert f"gating run: {GATE}" in reason
    assert "failing scenarios: card_to_pr, merge_gate" in reason
    assert "harness run failed" in reason
    assert TO in reason


def test_a_merge_train_decline_is_held_but_advancing(env, monkeypatch):
    gh = FakeGh()
    _decision(monkeypatch, gh, outcome="harness-cancelled-by-newer-push",
              reason="not promoting: cancelled by a newer push")
    record = _only_decision(gh)
    assert record["act"] == "held"
    assert record["code"] == "channel-advancing"


def test_already_at_the_candidate_records_a_no_op(env, monkeypatch):
    gh = FakeGh()
    code = _decision(monkeypatch, gh, outcome="not-ahead-of-channel",
                     ancestry="identical", from_sha=TO,
                     reason=f"stable is already at {TO} — nothing to do.")
    assert code == 0
    record = _only_decision(gh)
    assert record["act"] == "no-op"
    assert record["code"] == "channel-current"
    assert record["version"] is None
    assert record["deployed"] == "stable@2222222"


def test_a_stale_run_behind_the_channel_is_a_no_op_not_a_hold(env, monkeypatch):
    """A late-finishing older run: `stable` already carries the candidate.
    Nothing is stuck, so the Train Yard must not read it as held."""
    gh = FakeGh()
    _decision(monkeypatch, gh, outcome="not-ahead-of-channel",
              ancestry="behind",
              reason="refusing to move stable backwards")
    assert _only_decision(gh)["act"] == "no-op"


def test_an_unreadable_ancestry_is_held_not_a_no_op(env, monkeypatch):
    gh = FakeGh()
    _decision(monkeypatch, gh, outcome="not-ahead-of-channel",
              ancestry="", reason="could not establish that it is ahead")
    assert _only_decision(gh)["act"] == "held"


def test_a_failed_main_run_with_no_channel_read_still_records(env, monkeypatch):
    """A red or cancelled main run reads neither the ref nor the compare, so
    the from-sha is empty — the record says `null`, never a made-up tag."""
    gh = FakeGh()
    _decision(monkeypatch, gh, outcome="harness-failed", from_sha="",
              reason="not promoting: the harness run failed")
    record = _only_decision(gh)
    assert record["deployed"] is None
    assert record["act"] == "held"


# --------------------------------------------------------------------------
# 3. The `release` deployment, field for field.
# --------------------------------------------------------------------------

def test_the_release_deployment_body_matches_the_contract_field_for_field(env):
    body = channel_record.release_body(from_sha=FROM, to_sha=TO, env=ENV)
    assert body == {
        "ref": TO,
        "environment": "pipeline-channel",
        "task": "release",
        "description": "stable@2222222",
        "auto_merge": False,
        "required_contexts": [],
        "production_environment": True,
        "payload": {
            "run_id": "9001",
            "surface": "pipeline-channel",
            "version": "stable@2222222",
            "from": "stable@1111111",
            "from_sha": FROM,
            "to_sha": TO,
        },
    }
    # Real JSON types — a boolean and an empty list, never the strings
    # `gh api -f` would have sent.
    wire = json.dumps(body)
    assert '"auto_merge": false' in wire
    assert '"required_contexts": []' in wire
    assert '"production_environment": true' in wire
    assert '"run_id": "9001"' in wire


def test_the_release_status_body_matches_the_contract(env):
    assert channel_record.release_status_body(repo=REPO, env=ENV) == {
        "state": "success",
        "description": "live",
        "auto_inactive": False,
        "log_url": f"https://github.com/{REPO}/actions/runs/9001",
    }


def test_a_tag_move_posts_one_deployment_then_its_status(env, monkeypatch):
    gh = FakeGh()
    code = _run(["release", "--from-sha", FROM, "--to-sha", TO],
                monkeypatch, gh)
    assert code == 0
    assert [path for path, _ in gh.posts] == [
        f"repos/{REPO}/deployments",
        f"repos/{REPO}/deployments/4242/statuses",
    ]
    assert gh.posts[0][1] == channel_record.release_body(
        from_sha=FROM, to_sha=TO, env=ENV)
    assert gh.posts[1][1] == channel_record.release_status_body(repo=REPO, env=ENV)


def test_the_real_seam_sends_the_body_as_json_on_stdin(env, monkeypatch):
    """`release_decision.gh` is the path both records take: `--input -`
    with the body serialized by `json.dumps`, never `-f` fields."""
    seen = {}

    class Done:
        returncode = 0
        stdout = '{"id": 7}'
        stderr = ""

    def fake_run(cmd, input=None, **_):
        seen["cmd"], seen["input"] = cmd, input
        return Done()

    monkeypatch.setattr(release_decision.subprocess, "run", fake_run)
    clause = channel_record.write_release(repo=REPO, from_sha=FROM, to_sha=TO,
                                          env=ENV, out=lambda _line: None)
    assert "--input" in seen["cmd"] and "-f" not in seen["cmd"]
    assert json.loads(seen["input"])["state"] == "success"
    assert "recorded" in clause and "not recorded" not in clause


# --------------------------------------------------------------------------
# 4. A refused post never fails the promotion.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("fail_on", ["/deployments", "/statuses"])
def test_a_refused_release_post_exits_zero_and_says_so(
        env, monkeypatch, capsys, fail_on):
    gh = FakeGh(fail_on=fail_on)
    code = _run(["release", "--from-sha", FROM, "--to-sha", TO],
                monkeypatch, gh)
    assert code == 0
    assert "release not recorded" in capsys.readouterr().out


def test_a_refused_decision_post_exits_zero_and_says_so(env, monkeypatch, capsys):
    gh = FakeGh(fail_on="/deployments")
    code = _decision(monkeypatch, gh, promoted="true", ancestry="ahead",
                     outcome="harness-passed-promoting", reason="promoting")
    assert code == 0
    assert "decision not recorded" in capsys.readouterr().out


def test_a_crashing_seam_exits_zero(env, monkeypatch, capsys):
    def boom(path, body):
        raise ValueError("not even a RuntimeError")

    assert _run(["release", "--from-sha", FROM, "--to-sha", TO],
                monkeypatch, boom) == 0
    assert _decision(monkeypatch, boom, reason="x") == 0


# --------------------------------------------------------------------------
# 5. The workflow calls the recorder, and cannot let it touch the move.
# --------------------------------------------------------------------------

def _steps():
    return yaml.safe_load(WORKFLOW.read_text())["jobs"]["promote"]["steps"]


def _index(steps, predicate) -> int:
    found = [i for i, step in enumerate(steps) if predicate(step)]
    assert len(found) == 1, found
    return found[0]


def _runs(fragment):
    return lambda step: fragment in str(step.get("run", ""))


def test_the_workflow_records_the_decision_after_deciding_on_every_evaluation():
    steps = _steps()
    decide = _index(steps, lambda s: s.get("id") == "decide")
    decision = _index(steps, lambda s: _runs("channel_record.py")(s)
                      and " decision" in str(s.get("run", "")))
    step = steps[decision]
    assert decision > decide
    condition = str(step.get("if", ""))
    # Every run that evaluates the channel: a main harness run or a dispatch
    # — the same runs the `## Did not promote` block speaks for.
    assert "workflow_dispatch" in condition
    assert "head_branch == 'main'" in condition
    assert "promote" not in condition, "a decline is recorded too"
    env = str(step.get("env", {}))
    for wanted in ("steps.decide.outputs.promote", "steps.decide.outputs.outcome",
                   "steps.decide.outputs.reason", "steps.ancestry.outputs.status",
                   "steps.ancestry.outputs.head", "steps.channel.outputs.scenarios",
                   "workflow_run.html_url", "GH_TOKEN"):
        assert wanted in env, wanted


def test_the_workflow_records_a_release_only_after_the_tag_moved():
    steps = _steps()
    move = _index(steps, lambda s: s.get("name") == "Move the channel")
    release = _index(steps, lambda s: _runs("channel_record.py")(s)
                     and " release" in str(s.get("run", "")))
    assert release > move
    step = steps[release]
    assert step.get("if") == "steps.decide.outputs.promote == 'true'"
    env = str(step.get("env", {}))
    assert "steps.ancestry.outputs.head" in env
    assert "steps.resolve.outputs.candidate" in env


def test_the_recorder_cannot_change_the_exit_code_or_the_move():
    steps = _steps()
    move = _index(steps, lambda s: s.get("name") == "Move the channel")
    recorders = [i for i, s in enumerate(steps)
                 if "channel_record.py" in str(s.get("run", ""))]
    assert len(recorders) == 2
    for i in recorders:
        assert i > move, "the tag is moved before anything is recorded"
        assert steps[i].get("continue-on-error") is True
        assert "id" not in steps[i], "nothing downstream may read a recorder"
    # The move reads only the decision — never a record.
    assert "channel_record" not in str(steps[move])


def test_the_workflow_may_write_deployments():
    perms = yaml.safe_load(WORKFLOW.read_text())["permissions"]
    assert perms.get("deployments") == "write"
    # Least privilege everywhere else is unchanged.
    assert perms.get("contents") == "read"
