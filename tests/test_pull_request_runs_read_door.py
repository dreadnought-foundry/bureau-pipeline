"""The pull-request runs read their own card through the read door (DRE-8899).

Linear Sync, QA Review and Verify run on a `pull_request` event. The door
serves such a run exactly one read: `/cards` for the card its head branch
names (agent-bureau DRE-8807, `bureau_read.own_card`, DRE-8812). These steps
carry the door's four variables, set exactly as `reconcile.yml` sets them, and
the step's own card max-age (`BUREAU_READ_CARD_MAX_AGE`, DRE-8898), so their
`linear_ops` card reads ask the door first:

* Linear Sync's `Card → Done` and its conflict sweep: 60 seconds, because the
  card it closes has just merged;
* QA Review's `Assemble critic context`, `Post verdict` and `Card → In Review`,
  and Verify's `Post verdict` and both `Verifier run` attempts: 120 seconds,
  the review card's bound (`bureau_read.REVIEW_CARD_MAX_AGE`).

The verifier agent reads the card and its thread from the job's snapshot
(`card_snapshot.py description` / `thread`), and Linear only when the snapshot
is absent. A shadow replay's agent is handed no door, as it is handed no
Linear key (DRE-7194): the card's thread holds the verdicts it is replaying.

The scenarios run the readers themselves on a `pull_request` run for
`agent/DRE-7-x`: the door is `tests/bureau_read_fakes.py` on 127.0.0.1 and
Linear is `LinearStub`, which counts every request.
"""
from __future__ import annotations

import os
import sys
import urllib.request
from pathlib import Path
from unittest import mock

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(TESTS))
# reconcile binds these at import.
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/portico")
os.environ.setdefault("GH_TOKEN", "test")

import bureau_read  # noqa: E402
import design_answer  # noqa: E402
import fix_concurrency as fc  # noqa: E402
import linear_ops  # noqa: E402
import reconcile  # noqa: E402
from bureau_read_fakes import FakeDoor, FakeIssuer, card, door_env  # noqa: E402
from test_linear_ops_card_via_door import (  # noqa: E402
    DOOR_ENVS,
    WORLD,
    LinearStub,
    run,
)

WORKFLOWS = ROOT / ".github" / "workflows"

#: The door's four variables, exactly as reconcile.yml's Sweep sets them.
DOOR = {
    "BUREAU_READ": "${{ vars.BUREAU_READ || 'off' }}",
    "BUREAU_READ_URL": "${{ vars.BUREAU_READ_URL }}",
    "BUREAU_READ_AUDIENCE": "${{ vars.BUREAU_READ_AUDIENCE }}",
    "BUREAU_PIPELINE_REF": "${{ inputs.pipeline_ref }}",
}
#: The verifier agent's door, closed in a shadow replay the way its Linear
#: key is: a replay that can read the card's thread can read the answer.
VERIFIER_DOOR = {
    **DOOR,
    "BUREAU_READ": "${{ !inputs.shadow && (vars.BUREAU_READ || 'off') || 'off' }}",
    "BUREAU_READ_URL": "${{ !inputs.shadow && vars.BUREAU_READ_URL || '' }}",
}

LINEAR_SYNC_MAX_AGE = 60
REVIEW_MAX_AGE = 120

CONFLICTS_ONLY = "python3 .bureau-pipeline/scripts/reconcile.py --conflicts-only"

#: (workflow, job, step name, max-age, the door it carries)
WIRED = [
    ("linear-sync.yml", "card-done", "Card → Done", LINEAR_SYNC_MAX_AGE, DOOR),
    ("linear-sync.yml", "conflict-sweep", "Dispatch fix agents for newly conflicted PRs",
     LINEAR_SYNC_MAX_AGE, DOOR),
    ("qa-review.yml", "review", "Assemble critic context", REVIEW_MAX_AGE, DOOR),
    ("qa-review.yml", "review", "Post verdict or neutral status", REVIEW_MAX_AGE, DOOR),
    ("qa-review.yml", "review", "Card → In Review (a pull request opened outside the dispatch)",
     REVIEW_MAX_AGE, DOOR),
    ("verify.yml", "verify", "Post verdict or neutral status", REVIEW_MAX_AGE, DOOR),
    ("verify.yml", "verify", "Verifier run", REVIEW_MAX_AGE, VERIFIER_DOOR),
    ("verify.yml", "verify", "Verifier run (retry)", REVIEW_MAX_AGE, VERIFIER_DOOR),
]


def _steps(workflow: str, job: str) -> list[dict]:
    return yaml.safe_load((WORKFLOWS / workflow).read_text())["jobs"][job]["steps"]


def _step(workflow: str, job: str, name: str) -> dict:
    for step in _steps(workflow, job):
        if step.get("name") == name:
            return step
    raise AssertionError(f"{workflow}: no step named {name!r} in job {job}")


# ── the workflow YAML ───────────────────────────────────────────────────────


def test_the_review_bound_is_the_clients():
    assert REVIEW_MAX_AGE == bureau_read.REVIEW_CARD_MAX_AGE
    low, high = linear_ops.CARD_MAX_AGE_RANGE
    assert low <= LINEAR_SYNC_MAX_AGE <= high and low <= REVIEW_MAX_AGE <= high


@pytest.mark.parametrize("workflow,job,name,max_age,door", WIRED,
                         ids=[f"{w}:{n}" for w, _j, n, _a, _d in WIRED])
def test_each_step_carries_the_door_and_its_card_max_age(workflow, job, name, max_age, door):
    env = _step(workflow, job, name).get("env") or {}
    for key, value in door.items():
        assert env.get(key) == value, (name, key)
    assert str(env.get(linear_ops.CARD_MAX_AGE_ENV)) == str(max_age), name


def test_the_conflict_sweep_step_is_the_one_running_both_passes():
    run_block = _step("linear-sync.yml", "conflict-sweep",
                      "Dispatch fix agents for newly conflicted PRs")["run"]
    assert run_block.count(CONFLICTS_ONLY) == 2
    for step in _steps("linear-sync.yml", "conflict-sweep"):
        if CONFLICTS_ONLY in (step.get("run") or ""):
            assert step["name"] == "Dispatch fix agents for newly conflicted PRs"


def _verify_ctx(*, shadow: bool) -> dict:
    return {"inputs": {"shadow": shadow, "pipeline_ref": "stable"},
            "vars": {"BUREAU_READ": "on", "BUREAU_READ_URL": "https://door.example",
                     "BUREAU_READ_AUDIENCE": "aud"}}


@pytest.mark.parametrize("name", ["Verifier run", "Verifier run (retry)"])
def test_the_verifier_agent_has_the_door_and_a_shadow_replay_does_not(name):
    env = _step("verify.yml", "verify", name)["env"]
    live = {k: fc.interpolate(str(env[k]), _verify_ctx(shadow=False)) for k in DOOR}
    shadow = {k: fc.interpolate(str(env[k]), _verify_ctx(shadow=True)) for k in DOOR}
    assert live["BUREAU_READ"] == "on"
    assert live["BUREAU_READ_URL"] == "https://door.example"
    assert shadow["BUREAU_READ"] == "off"
    assert shadow["BUREAU_READ_URL"] == ""
    unset = {"inputs": {"shadow": False, "pipeline_ref": "stable"}, "vars": {}}
    assert fc.interpolate(str(env["BUREAU_READ"]), unset) == "off"


# ── the verifier's prompt ───────────────────────────────────────────────────

SNAPSHOT = '"$RUNNER_TEMP/card-snapshot.json"'


@pytest.mark.parametrize("name", ["Verifier run", "Verifier run (retry)"])
def test_the_verifier_reads_the_card_from_the_snapshot(name):
    prompt = " ".join(_step("verify.yml", "verify", name)["with"]["prompt"].split())
    assert f"python3 .bureau-pipeline/scripts/card_snapshot.py description {SNAPSHOT}" in prompt
    assert f"python3 .bureau-pipeline/scripts/card_snapshot.py thread {SNAPSHOT}" in prompt
    assert "Linear only when the snapshot is absent" in prompt
    assert "read the LIVE card" not in prompt


def test_both_attempts_tell_the_verifier_the_same_thing():
    first, retry = (_step("verify.yml", "verify", n)["with"]["prompt"]
                    for n in ("Verifier run", "Verifier run (retry)"))
    assert first == retry


def test_the_snapshot_the_prompt_names_is_the_one_the_run_writes():
    take = _step("verify.yml", "verify", "Read the card once (read door, else one Linear read)")
    assert 'SNAP="${RUNNER_TEMP:-/tmp}/card-snapshot.json"' in take["run"]
    assert 'card_snapshot.py take "$CARD" "$SNAP"' in take["run"]


# ── the scenarios: a pull_request run on agent/DRE-7-x ─────────────────────


@pytest.fixture
def pr_run(monkeypatch):
    """A `pull_request` run for `agent/DRE-7-x` with Linear stubbed: the
    environment the wired steps run in, minus the door."""
    for name in DOOR_ENVS:
        monkeypatch.delenv(name, raising=False)
    for name in ("LINEAR_API_KEY_FALLBACK", "AGENT_TASK_SNAPSHOT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(linear_ops.time, "sleep", lambda _s: None)
    monkeypatch.setenv("LINEAR_API_KEY", "test-key")
    monkeypatch.delenv(linear_ops.FALLBACK_ENV, raising=False)
    monkeypatch.delenv(linear_ops.HOME_ENV, raising=False)
    monkeypatch.setenv(linear_ops.IDENTITY_ENV, "fleet")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GITHUB_HEAD_REF", "agent/DRE-7-x")
    bureau_read.reset_for_tests()
    linear_ops.reset_pass_cache()
    stub = LinearStub()
    monkeypatch.setattr(urllib.request, "urlopen", stub)
    yield stub
    linear_ops.reset_pass_cache()
    bureau_read.reset_for_tests()


def _open_door(monkeypatch, door: FakeDoor, issuer: FakeIssuer, max_age: int) -> None:
    for key, value in door_env(door_url=door.url, issuer=issuer).items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv(linear_ops.CARD_MAX_AGE_ENV, str(max_age))


def _card_reads(door: FakeDoor) -> list[str]:
    return [r["query"]["ids"] for r in door.asked("/cards")]


def test_the_critics_design_answer_reads_its_card_through_the_door(pr_run, monkeypatch):
    with FakeIssuer() as issuer, FakeDoor(WORLD) as door:
        _open_door(monkeypatch, door, issuer, REVIEW_MAX_AGE)
        context, said = design_answer.read_context("DRE-7")
        assert _card_reads(door) == ["DRE-7"]
    assert "UNKNOWN" not in said, said
    assert "read 5 comment(s)" in said
    # No Linear query reads the card: the one request is this process's own
    # `viewer`, which authorship is judged against and the door never answers
    # (DRE-8898, "The viewer is never the door's").
    assert pr_run.thread_reads() == []
    assert pr_run.queries() == ["query { viewer { id } }"]
    assert context


def test_without_the_card_max_age_the_same_read_goes_to_linear(pr_run, monkeypatch):
    """The step variable is what moves the read: a step that names none reads
    Linear exactly as before, door or no door."""
    with FakeIssuer() as issuer, FakeDoor(WORLD) as door:
        _open_door(monkeypatch, door, issuer, REVIEW_MAX_AGE)
        monkeypatch.delenv(linear_ops.CARD_MAX_AGE_ENV)
        design_answer.read_context("DRE-7")
        assert _card_reads(door) == []
    assert pr_run.thread_reads(), "the thread came from Linear"


def test_the_comment_command_reads_its_card_through_the_door(tmp_path):
    over = {"GITHUB_EVENT_NAME": "pull_request", "GITHUB_HEAD_REF": "agent/DRE-7-x",
            linear_ops.CARD_MAX_AGE_ENV: str(REVIEW_MAX_AGE)}
    with FakeIssuer() as issuer, FakeDoor(WORLD) as door:
        env = door_env(door_url=door.url, issuer=issuer)
        env.update(over)
        proc, sent = run(tmp_path, env, "cli", "comment", "DRE-7", "the verdict")
        assert _card_reads(door) == ["DRE-7"]
    assert proc.returncode == 0, proc.stderr
    assert "commented on DRE-7" in proc.stdout
    # The write and nothing else: no Linear query read the card.
    assert len(sent) == 1 and "commentCreate(" in sent[0], sent
    assert not [ln for ln in proc.stderr.splitlines()
                if ln.startswith(bureau_read.FALLBACK_TAG)]


def _conflict_gh(*args):
    """GitHub for the conflict sweep: an idle fix lane and one DIRTY pull
    request, another card's (DRE-8)."""
    if args[:2] == ("run", "list"):
        return "[]"
    if args[:2] == ("pr", "list"):
        return ('[{"number": 5, "headRefName": "agent/DRE-8-y", "headRefOid": "'
                + "a" * 40 + '", "mergeStateStatus": "DIRTY", "comments": []}]')
    return ""


def _conflict_sweep(monkeypatch) -> list:
    monkeypatch.setenv("REPO", "dreadnought-foundry/portico")
    monkeypatch.setenv("REPO_SLUG", "portico")
    monkeypatch.setenv("GITHUB_WORKFLOW", "Linear Sync")
    monkeypatch.setenv("GITHUB_JOB", "conflict-sweep")
    dispatched = []
    with mock.patch.object(reconcile, "gh", side_effect=_conflict_gh), \
            mock.patch.object(reconcile, "gh_dispatch",
                              side_effect=lambda *a: dispatched.append(a)), \
            mock.patch("time.sleep"):
        reconcile.main(conflicts_only=True)
    return dispatched


def test_the_conflict_sweep_names_its_linear_read_and_reads_linear_as_today(
        pr_run, monkeypatch, capsys):
    baseline_dispatch = _conflict_sweep(monkeypatch)
    baseline = pr_run.queries()
    assert baseline, "the sweep reads the conflicted card's park state from Linear"
    capsys.readouterr()
    pr_run.sent.clear()
    bureau_read.reset_for_tests()
    linear_ops.reset_pass_cache()

    with FakeIssuer() as issuer, FakeDoor({**WORLD, "DRE-8": card("DRE-8", "In Review")}) as door:
        _open_door(monkeypatch, door, issuer, LINEAR_SYNC_MAX_AGE)
        dispatched = _conflict_sweep(monkeypatch)
        # The door serves this run its own card only; another card is never
        # asked for, so no token is spent on a refusal.
        assert door.requests == []
    err = capsys.readouterr().err
    fallbacks = [ln for ln in err.splitlines() if ln.startswith(bureau_read.FALLBACK_TAG)]
    assert fallbacks, err
    assert fallbacks[0] == (f'{bureau_read.FALLBACK_TAG} reader="Linear Sync / conflict-sweep / '
                            f'{os.path.basename(sys.argv[0])}" read=cards reason=pull-request')
    assert all(ln.endswith("reason=pull-request") for ln in fallbacks)
    # …and reads Linear exactly as it did with the door off.
    assert pr_run.queries() == baseline
    assert dispatched == baseline_dispatch and dispatched
