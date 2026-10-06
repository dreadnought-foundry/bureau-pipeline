"""Every time `stable` advances, promote-channel writes a Linear release (DRE-4872).

bureau-pipeline's one release is the `stable` channel moving, and until this
card nothing in Linear recorded it: DRE-3855 left the `pipeline-channel`
surface out on purpose, and `release_linear.check()` refused a pipeline on any
`channel` surface. The CEO reversed that on 2026-09-25. What this file pins:

  1. the repo's own `release.json` declares the `bureau-pipeline-channel`
     pipeline and the train's schema check passes on it; a tagless surface
     that is not `record: channel` is still refused;
  2. a promotion from A to B writes version `stable-<B[:7]>` with the cards
     the merges in `A..B` name — the promote step's own call, driven through
     the CLI with a fake Linear;
  3. a held channel writes nothing, and nor does a move that cannot be
     verified, because nothing moved;
  4. a Linear error is a WARNING and exit 0 — the promotion's result is the
     one it would have been;
  5. the note names the Integration Harness run that proved the promoted sha,
     with its URL and its time in PT;
  6. the workflow calls it after the move, gated on the decision, with
     `LINEAR_API_KEY` from the repo's secrets, and cannot change the move.

The git legs build a REAL repository in a temp directory, the way
tests/test_release_linear.py does.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import promote_channel  # noqa: E402
import release_linear  # noqa: E402
import release_train  # noqa: E402
from test_release_linear import FakeLinear  # noqa: E402

RELEASE_JSON = ROOT / ".github" / "bureau" / "release.json"
WORKFLOW = ROOT / ".github" / "workflows" / "promote-channel.yml"

#: The pipeline the operator session created on 2026-09-25 (the card).
CHANNEL_PIPELINE = "65541905-7345-48a4-bfc4-c83aae296f0c"
REPO = "dreadnought-foundry/bureau-pipeline"
HARNESS = "https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/4242"
#: 16:23 UTC on 2026-09-25 is 09:23 PT — the move the CEO asked about.
HARNESS_AT = "2026-09-25T16:23:05Z"


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


# ── 1. the declaration ──────────────────────────────────────────────────────


def test_the_repo_declares_the_channel_pipeline_and_the_schema_passes():
    data = json.loads(RELEASE_JSON.read_text())
    assert data["linear_pipelines"] == {"pipeline-channel": CHANNEL_PIPELINE}
    assert release_linear.check(data) == []
    assert release_train.check_schema(data) == []


def _channel_entry(record="channel"):
    return {"tag_series": ["stable"], "paths": [], "script": None,
            "rollback": None, "spacing_minutes": 0, "window": "always",
            "auto": False, "identity": "promote-channel.yml", "record": record}


def test_a_channel_surface_may_name_a_pipeline():
    data = {"surfaces": {"pipeline-channel": _channel_entry()},
            "linear_pipelines": {"pipeline-channel": CHANNEL_PIPELINE}}
    assert release_linear.check(data) == []


@pytest.mark.parametrize("record", ["deploy", "none", ""])
def test_any_other_tagless_surface_is_still_refused(record):
    data = {"surfaces": {"elsewhere": _channel_entry(record=record)},
            "linear_pipelines": {"elsewhere": CHANNEL_PIPELINE}}
    problems = release_linear.check(data)
    assert any("cuts no version tag" in p for p in problems), problems


def test_the_channel_version_is_stable_and_the_first_seven_of_the_new_sha():
    assert release_linear.channel_version("7a517bd56" + "0" * 31) == "stable-7a517bd"


# ── the git legs ────────────────────────────────────────────────────────────


def _merge(repo, branch, subject, carried="work"):
    _git(repo, "checkout", "-qb", branch)
    (repo / f"{branch.replace('/', '_')}.txt").write_text("x\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", carried)
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--no-ff", branch, "-m", subject)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def channel_repo(tmp_path):
    """`main` with the channel at A and three merges after it, B their head."""
    repo = tmp_path / "bureau-pipeline"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    (repo / ".github" / "bureau").mkdir(parents=True)
    (repo / ".github" / "bureau" / "release.json").write_text(RELEASE_JSON.read_text())
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "the start")
    _merge(repo, "agent/DRE-4800-old", "Merge pull request #1 from o/agent/DRE-4800-old")
    a = _git(repo, "rev-parse", "HEAD")
    _merge(repo, "agent/DRE-4833-approval",
           "Merge pull request #2 from o/agent/DRE-4833-approval")
    _merge(repo, "agent/DRE-4836-opus", "Merge pull request #3 from o/agent/DRE-4836-opus")
    (repo / "ledger.txt").write_text("ledger\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "chore: the ledger")                # uncarded
    b = _git(repo, "rev-parse", "HEAD")
    return repo, a, b


def _promote(repo, monkeypatch, fake, *, promoted="true", a, b, stable_now=None,
             extra=()):
    monkeypatch.setattr(release_linear, "_default_call", fake)
    monkeypatch.setenv("LINEAR_API_KEY", "k")
    return release_linear.main([
        "--file", str(repo / ".github" / "bureau" / "release.json"),
        "--repo-root", str(repo), "--repo", REPO,
        "channel", "--surface", "pipeline-channel", "--promoted", promoted,
        "--from-sha", a, "--to-sha", b,
        "--stable-now", b if stable_now is None else stable_now,
        "--harness-run", HARNESS, "--harness-at", HARNESS_AT, *extra])


# ── 2. a promotion writes its release ───────────────────────────────────────


def test_a_promotion_from_a_to_b_writes_stable_b_with_the_cards_a_to_b_names(
        channel_repo, monkeypatch, capsys):
    repo, a, b = channel_repo
    fake = FakeLinear()
    assert _promote(repo, monkeypatch, fake, a=a, b=b) == 0
    assert fake.names() == ["releaseSync", "releaseComplete", "release(",
                            "releaseNoteCreate"], capsys.readouterr().out
    sync = fake.calls[0][1]["input"]
    assert sync["pipelineId"] == CHANNEL_PIPELINE
    assert sync["version"] == sync["name"] == f"stable-{b[:7]}"
    assert sync["commitSha"] == b
    # A..B — the merge AT A is the previous release's, never this one's.
    assert [r["identifier"] for r in sync["issueReferences"]] == ["DRE-4833", "DRE-4836"]
    assert sync["repository"]["name"] == "bureau-pipeline"
    note = fake.calls[-1][1]["input"]["content"]
    assert f"stable-{b[:7]}" in note
    assert f"1 change with no card: {b[:7]}" in note


def test_the_note_names_the_harness_run_that_proved_the_sha(channel_repo, monkeypatch):
    repo, a, b = channel_repo
    fake = FakeLinear()
    _promote(repo, monkeypatch, fake, a=a, b=b)
    note = fake.calls[-1][1]["input"]["content"]
    assert "Integration Harness" in note
    assert HARNESS in note
    assert "2026-09-25 09:23 PT" in note


def test_a_dispatch_names_the_run_that_stamped_the_commit(channel_repo, monkeypatch,
                                                          tmp_path):
    # A by-hand promote has no triggering harness run: the proof is the
    # commit's own `integration-harness` stamp, read from the status file the
    # decision already read.
    repo, a, b = channel_repo
    stamped = "https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/99"
    statuses = tmp_path / "combined_status.json"
    statuses.write_text(json.dumps({"statuses": [
        {"context": "ci", "state": "success", "target_url": "https://x/ci",
         "updated_at": "2026-09-25T15:00:00Z"},
        {"context": "integration-harness", "state": "success",
         "target_url": stamped, "updated_at": "2026-09-25T16:01:00Z"}]}))
    fake = FakeLinear()
    monkeypatch.setattr(release_linear, "_default_call", fake)
    monkeypatch.setenv("LINEAR_API_KEY", "k")
    assert release_linear.main([
        "--file", str(repo / ".github" / "bureau" / "release.json"),
        "--repo-root", str(repo), "--repo", REPO,
        "channel", "--surface", "pipeline-channel", "--promoted", "true",
        "--from-sha", a, "--to-sha", b, "--stable-now", b,
        "--harness-run", "", "--harness-at", "",
        "--statuses-file", str(statuses)]) == 0
    note = fake.calls[-1][1]["input"]["content"]
    assert stamped in note and "2026-09-25 09:01 PT" in note


def test_no_proof_on_record_is_said_rather_than_invented(channel_repo, monkeypatch):
    repo, a, b = channel_repo
    fake = FakeLinear()
    monkeypatch.setattr(release_linear, "_default_call", fake)
    monkeypatch.setenv("LINEAR_API_KEY", "k")
    release_linear.main([
        "--file", str(repo / ".github" / "bureau" / "release.json"),
        "--repo-root", str(repo), "--repo", REPO,
        "channel", "--surface", "pipeline-channel", "--promoted", "true",
        "--from-sha", a, "--to-sha", b, "--stable-now", b])
    note = fake.calls[-1][1]["input"]["content"]
    assert "No Integration Harness run is on record" in note


def test_a_first_promotion_attaches_nothing(channel_repo, monkeypatch):
    repo, _, b = channel_repo
    fake = FakeLinear()
    assert _promote(repo, monkeypatch, fake, a="", b=b) == 0
    assert fake.calls[0][1]["input"]["issueReferences"] == []


# ── 3. nothing moved, nothing written ───────────────────────────────────────


def test_a_held_channel_writes_nothing(channel_repo, monkeypatch, capsys):
    repo, a, b = channel_repo
    # The decision itself, with CHANNEL_HOLD set: it does not promote.
    decision = promote_channel.evaluate(
        {"statuses": [{"context": "integration-harness", "state": "success"}]},
        b, hold="true", ancestry="ahead", conclusion="success", branch="main")
    assert decision.promote is False
    fake = FakeLinear()
    promoted = "true" if decision.promote else "false"
    assert _promote(repo, monkeypatch, fake, promoted=promoted, a=a, b=b) == 0
    assert fake.calls == []
    assert "no release written" in capsys.readouterr().out


def test_an_unverified_move_writes_nothing(channel_repo, monkeypatch, capsys):
    repo, a, b = channel_repo
    fake = FakeLinear()
    assert _promote(repo, monkeypatch, fake, a=a, b=b, stable_now=a) == 0
    assert fake.calls == []
    out = capsys.readouterr().out
    assert "WARNING" in out and "stable" in out


# ── 4. Linear never changes the promotion ───────────────────────────────────


@pytest.mark.parametrize("error", [
    release_linear.ReleaseLinearError("linear error: RATELIMITED"),
    RuntimeError("Linear is down"),
])
def test_a_linear_error_is_a_warning_and_the_exit_is_unchanged(
        channel_repo, monkeypatch, capsys, error):
    repo, a, b = channel_repo
    fake = FakeLinear(fail={"releaseSync": error})
    assert _promote(repo, monkeypatch, fake, a=a, b=b) == 0
    assert fake.names() == ["releaseSync"]
    assert "WARNING" in capsys.readouterr().out


def test_no_key_is_a_warning_and_no_call(channel_repo, monkeypatch, capsys):
    repo, a, b = channel_repo
    fake = FakeLinear()
    monkeypatch.setattr(release_linear, "_default_call", fake)
    monkeypatch.setenv("LINEAR_API_KEY", "")
    assert release_linear.main([
        "--file", str(repo / ".github" / "bureau" / "release.json"),
        "--repo-root", str(repo), "--repo", REPO,
        "channel", "--surface", "pipeline-channel", "--promoted", "true",
        "--from-sha", a, "--to-sha", b, "--stable-now", b]) == 0
    assert fake.calls == []
    assert "LINEAR_API_KEY is not set" in capsys.readouterr().out


def test_an_unreadable_release_json_still_exits_zero(tmp_path, capsys):
    assert release_linear.main([
        "--file", str(tmp_path / "missing.json"), "--repo-root", str(tmp_path),
        "--repo", REPO, "channel", "--surface", "pipeline-channel",
        "--promoted", "true", "--from-sha", "a" * 40, "--to-sha", "b" * 40,
        "--stable-now", "b" * 40]) == 0
    assert "WARNING" in capsys.readouterr().out


# ── 5. the note's proof line, pinned directly ───────────────────────────────


def test_the_note_is_unchanged_for_a_tag_release():
    # The train passes no proof, so its notes stay byte for byte as they were.
    kwargs = dict(label="portico-portals", version="portico-portals-v1",
                  cards=[], unnamed=[], first=False)
    assert release_linear.assemble_note(**kwargs) == \
        release_linear.assemble_note(**kwargs, proof=None)
    assert "Integration Harness" not in release_linear.assemble_note(**kwargs)


# ── 6. the workflow ─────────────────────────────────────────────────────────


def _steps():
    return yaml.safe_load(WORKFLOW.read_text())["jobs"]["promote"]["steps"]


def _linear_step(steps):
    found = [i for i, s in enumerate(steps) if "release_linear.py" in str(s.get("run", ""))]
    assert len(found) == 1, found
    return found[0]


def test_the_workflow_writes_the_release_after_the_move_gated_on_the_decision():
    steps = _steps()
    move = next(i for i, s in enumerate(steps) if s.get("name") == "Move the channel")
    at = _linear_step(steps)
    assert at > move, "the tag is moved before the release is written"
    step = steps[at]
    assert step.get("if") == "steps.decide.outputs.promote == 'true'"
    env = step.get("env", {})
    assert env.get("LINEAR_API_KEY") == "${{ secrets.LINEAR_API_KEY }}"
    joined = str(env)
    for wanted in ("steps.ancestry.outputs.head", "steps.resolve.outputs.candidate",
                   "steps.decide.outputs.promote", "workflow_run.html_url",
                   "workflow_run.updated_at"):
        assert wanted in joined, wanted
    run = step["run"]
    # Verified: the ref is read back and handed over, never assumed.
    assert "git/ref/tags/stable" in run and "--stable-now" in run
    # The range needs history the default shallow checkout does not have.
    assert "--unshallow" in run
    assert "--statuses-file combined_status.json" in run
    assert "--surface pipeline-channel" in run


def test_the_linear_step_cannot_change_the_exit_code_or_the_move():
    steps = _steps()
    at = _linear_step(steps)
    assert steps[at].get("continue-on-error") is True
    assert "id" not in steps[at], "nothing downstream may read the Linear step"
    move = next(s for s in steps if s.get("name") == "Move the channel")
    assert "release_linear" not in str(move)
