"""The release train writes a Linear release for every surface that declares a
pipeline — pinned without Linear and without GitHub (DRE-3854).

What this file pins:

  1. the declaration is ONE top-level key, `linear_pipelines`, checked by the
     train's own schema check with the key named — and absent is fine;
  2. a surface that declares no pipeline is never touched and prints nothing;
  3. the release is written AFTER the train verified the tag: sync with the
     tag as the version and the released commit, then complete, then the
     note — in that order, on the workspace-key mutations, with `pipelineId`;
  4. the cards are the ones the surface's own changes name since the previous
     tag, a change another surface's narrower path owns is not counted, and a
     merge that names no card is read through the SUBJECTS it carried;
  5. a first release attaches nothing rather than the whole history;
  6. an existing note is updated, never doubled; a failed note falls back to
     the release description;
  7. nothing Linear does — no key, a refusal, an outage — raises, and the
     train's decision and receipt are the ones it would have printed anyway.

The git legs build a REAL repository in a temp directory, the way
tests/test_release_train.py does.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import release_linear  # noqa: E402
import release_train  # noqa: E402

PIPELINE = "0c1d2e3f-4a5b-4c6d-8e7f-901234567890"
PT = ZoneInfo("America/Los_Angeles")


def _git(repo, *args, env=None):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True,
                          env={**os.environ, **(env or {})}).stdout.strip()


def _surface_entry(paths, record="tag", script="infra/release-portals.sh"):
    return {
        "tag_series": ["portico-portals-v*"],
        "paths": list(paths),
        "script": script if record == "tag" else None,
        "rollback": "make rollback-portals VERSION=<tag>" if record == "tag" else None,
        "spacing_minutes": 30,
        "window": "always",
        "auto": record == "tag",
        "identity": "portico-release-role",
        "record": record,
    }


def _data(**pipelines):
    return {
        "surfaces": {
            "portals": _surface_entry(["infra/", "client/"]),
            "docs": _surface_entry(["infra/docs/"]),
        },
        "linear_pipelines": pipelines,
    }


class FakeLinear:
    """Records every call and answers the way Linear does."""

    def __init__(self, *, existing_note=False, fail=None, attach=None):
        self.calls = []
        self.existing_note = existing_note
        self.fail = fail or {}
        self.attach = attach

    def __call__(self, query, variables):
        name = next(op for op in (
            "releaseSync", "releaseComplete", "releaseNoteCreate",
            "releaseNoteUpdate", "releaseUpdateByPipeline", "release(")
            if op in query)
        self.calls.append((name, variables))
        if name in self.fail:
            raise self.fail[name]
        release = {"id": "rel-1", "version": (variables.get("input") or {}).get("version"),
                   "url": "https://linear.app/x/release/rel-1", "completedAt": "now"}
        if name == "releaseSync":
            self.synced = variables["input"]["issueReferences"]
            return {"releaseSync": {"success": True, "release": release}}
        if name == "releaseComplete":
            return {"releaseComplete": {"success": True, "release": release}}
        if name == "release(":
            ids = self.attach if self.attach is not None else [
                r["identifier"] for r in self.synced]
            return {"release": {
                "id": "rel-1", "version": "v", "url": release["url"],
                "releaseNotes": [{"id": "note-1"}] if self.existing_note else [],
                "issues": {"nodes": [{"identifier": i, "title": f"Title of {i}",
                                      "description": ""} for i in ids]}}}
        return {name: {"success": True}}

    def names(self):
        return [name for name, _ in self.calls]


# ── 1. the declaration ──────────────────────────────────────────────────────


def test_no_declaration_is_not_a_problem():
    data = _data()
    del data["linear_pipelines"]
    assert release_linear.check(data) == []
    assert release_train.check_schema(data) == []


def test_a_good_declaration_passes_the_trains_schema_check():
    assert release_train.check_schema(_data(portals=PIPELINE)) == []


@pytest.mark.parametrize("declared, needle", [
    ("not-an-object", "linear_pipelines: must be an object"),
    ({"nope": PIPELINE}, "linear_pipelines.nope: names no declared surface"),
    ({"portals": "portico-portals"}, "linear_pipelines.portals: must be the Linear"),
    ({"portals": "https://linear.app/x/pipeline/portico-portals"},
     "linear_pipelines.portals: must be the Linear"),
])
def test_a_malformed_declaration_is_refused_with_the_key_named(declared, needle):
    data = _data()
    data["linear_pipelines"] = declared
    problems = release_train.check_schema(data)
    assert any(needle in p for p in problems), problems


def test_a_channel_surface_cannot_name_a_pipeline():
    data = {"surfaces": {"pipeline-channel": _surface_entry([], record="channel")},
            "linear_pipelines": {"pipeline-channel": PIPELINE}}
    problems = release_train.check_schema(data)
    assert any("cuts no version tag" in p for p in problems), problems


def test_the_declaration_is_top_level_so_an_old_schema_never_reads_it():
    # A caller can land the key before this change reaches `stable`: the
    # per-surface check refuses unknown SURFACE fields, and this is not one.
    data = _data(portals=PIPELINE)
    assert "linear_pipelines" not in data["surfaces"]["portals"]
    problems = [p for name, entry in data["surfaces"].items()
                for p in release_train._check_surface(name, entry)]
    assert problems == []


# ── 2. undeclared surfaces are untouched ────────────────────────────────────


def test_a_surface_with_no_pipeline_is_never_touched_and_prints_nothing():
    fake, lines = FakeLinear(), []
    result = release_linear.write(
        data=_data(), surface_name="portals", repo="o/portico", repo_root=".",
        version="portico-portals-v3", sha="a" * 40, previous_tag="portico-portals-v2",
        call=fake, env={"LINEAR_API_KEY": "k"}, out=lines.append)
    assert result is None and fake.calls == [] and lines == []


def test_no_key_is_a_warning_and_no_call():
    fake, lines = FakeLinear(), []
    result = release_linear.write(
        data=_data(portals=PIPELINE), surface_name="portals", repo="o/portico",
        repo_root=".", version="portico-portals-v3", sha="a" * 40,
        previous_tag=None, call=fake, env={"LINEAR_API_KEY": ""}, out=lines.append)
    assert fake.calls == []
    assert "LINEAR_API_KEY is not set" in result["problem"]
    assert any("WARNING" in line and "unaffected" in line for line in lines)


# ── the git legs ────────────────────────────────────────────────────────────


def _repo(tmp_path):
    repo = tmp_path / "portico"
    (repo / "infra" / "docs").mkdir(parents=True)
    (repo / "client").mkdir()
    (repo / "infra" / "stack.ts").write_text("v0\n")
    _git(tmp_path, "init", "-q", "-b", "main", str(repo))
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "the start")
    # Dated in the past: the train's newest tag is by creator date, and a
    # tag cut in the same second as the release's would tie with it.
    _git(repo, "tag", "-a", "portico-portals-v0", "-m", "v0",
         env={"GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z"})
    return repo


def _merge(repo, branch, path, subject, carried="work"):
    _git(repo, "checkout", "-qb", branch)
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(target.read_text() + "x\n" if target.exists() else "x\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", carried)
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--no-ff", branch, "-m", subject)
    return _git(repo, "rev-parse", "HEAD")


def test_the_release_is_synced_completed_and_noted_with_the_surfaces_cards(tmp_path):
    repo = _repo(tmp_path)
    _merge(repo, "agent/DRE-101-stack", "infra/stack.ts",
           "Merge pull request #1 from o/agent/DRE-101-stack")
    _merge(repo, "agent/DRE-102-docs", "infra/docs/page.md",
           "Merge pull request #2 from o/agent/DRE-102-docs")   # the docs surface's
    _merge(repo, "bot/sync", "client/app.ts",
           "Merge pull request #3 from o/bot/sync",
           carried="DRE-103 the client change")                  # named by what it carried
    _merge(repo, "hand-named", "client/other.ts",
           "Merge pull request #4 from o/hand-named")            # no card at all
    _merge(repo, "agent/DRE-104-elsewhere", "README.md",
           "Merge pull request #5 from o/agent/DRE-104-elsewhere")  # outside the paths
    sha = _git(repo, "rev-parse", "HEAD")
    fake, lines = FakeLinear(), []

    result = release_linear.write(
        data=_data(portals=PIPELINE), surface_name="portals",
        repo="dreadnought-foundry/portico", repo_root=repo,
        version="portico-portals-v1", sha=sha, previous_tag="portico-portals-v0",
        call=fake, env={"LINEAR_API_KEY": "k"}, out=lines.append)

    assert result["problem"] is None, lines
    assert fake.names() == ["releaseSync", "releaseComplete", "release(",
                            "releaseNoteCreate"]
    sync = fake.calls[0][1]["input"]
    assert sync["pipelineId"] == PIPELINE
    assert sync["version"] == sync["name"] == "portico-portals-v1"
    assert sync["commitSha"] == sha
    assert [r["identifier"] for r in sync["issueReferences"]] == ["DRE-101", "DRE-103"]
    assert sync["repository"]["name"] == "portico"
    complete = fake.calls[1][1]["input"]
    assert complete == {"pipelineId": PIPELINE, "version": "portico-portals-v1",
                        "commitSha": sha}
    note = fake.calls[3][1]["input"]
    assert note["pipelineId"] == PIPELINE and note["releaseIds"] == ["rel-1"]
    assert note["content"].startswith("## portico-portals v1")
    assert "* Title of DRE-103. (DRE-103)" in note["content"]
    assert "1 change with no card:" in note["content"]
    assert result["cards"] == ["DRE-101", "DRE-103"]


def test_a_first_release_attaches_nothing_rather_than_the_whole_history(tmp_path):
    repo = _repo(tmp_path)
    _merge(repo, "agent/DRE-101-stack", "infra/stack.ts", "Merge agent/DRE-101-stack")
    fake = FakeLinear()
    result = release_linear.write(
        data=_data(portals=PIPELINE), surface_name="portals", repo="o/portico",
        repo_root=repo, version="portico-portals-v0", sha=_git(repo, "rev-parse", "HEAD"),
        previous_tag=None, call=fake, env={"LINEAR_API_KEY": "k"}, out=lambda _: None)
    assert fake.calls[0][1]["input"]["issueReferences"] == []
    assert "The first release in this series" in fake.calls[-1][1]["input"]["content"]
    assert result["problem"] is None


def test_an_existing_note_is_updated_never_doubled(tmp_path):
    repo = _repo(tmp_path)
    fake = FakeLinear(existing_note=True)
    release_linear.write(
        data=_data(portals=PIPELINE), surface_name="portals", repo="o/portico",
        repo_root=repo, version="portico-portals-v1", sha=_git(repo, "rev-parse", "HEAD"),
        previous_tag="portico-portals-v0", call=fake, env={"LINEAR_API_KEY": "k"},
        out=lambda _: None)
    assert "releaseNoteCreate" not in fake.names()
    assert fake.calls[-1][0] == "releaseNoteUpdate"
    assert fake.calls[-1][1]["id"] == "note-1"


def test_a_failed_note_falls_back_to_the_description_and_never_raises(tmp_path):
    repo = _repo(tmp_path)
    fake = FakeLinear(fail={"releaseNoteCreate": release_linear.ReleaseLinearError("boom")})
    lines = []
    result = release_linear.write(
        data=_data(portals=PIPELINE), surface_name="portals", repo="o/portico",
        repo_root=repo, version="portico-portals-v1", sha=_git(repo, "rev-parse", "HEAD"),
        previous_tag="portico-portals-v0", call=fake, env={"LINEAR_API_KEY": "k"},
        out=lines.append)
    assert fake.names()[-1] == "releaseUpdateByPipeline"
    assert "could not be written as a note" in fake.calls[-1][1]["input"]["description"]
    assert "notes" in result["problem"]


@pytest.mark.parametrize("error", [
    release_linear.ReleaseLinearError("linear error: RATELIMITED"),
    RuntimeError("anything at all"),
    KeyError("releaseSync"),
])
def test_nothing_linear_does_raises(tmp_path, error):
    repo = _repo(tmp_path)
    fake, lines = FakeLinear(fail={"releaseSync": error}), []
    result = release_linear.write(
        data=_data(portals=PIPELINE), surface_name="portals", repo="o/portico",
        repo_root=repo, version="portico-portals-v1", sha=_git(repo, "rev-parse", "HEAD"),
        previous_tag="portico-portals-v0", call=fake, env={"LINEAR_API_KEY": "k"},
        out=lines.append)
    assert result["problem"]
    assert fake.names() == ["releaseSync"]
    assert any("WARNING" in line for line in lines)


def test_a_version_linear_already_has_says_so_and_stops(tmp_path):
    repo = _repo(tmp_path)
    fake = FakeLinear(fail={"releaseSync": release_linear.ReleaseLinearError(
        "Release already exists")})
    result = release_linear.write(
        data=_data(portals=PIPELINE), surface_name="portals", repo="o/portico",
        repo_root=repo, version="portico-portals-v1", sha=_git(repo, "rev-parse", "HEAD"),
        previous_tag="portico-portals-v0", call=fake, env={"LINEAR_API_KEY": "k"},
        out=lambda _: None)
    assert "already exists" in result["problem"]
    assert fake.names() == ["releaseSync"]


# ── the train's seam ────────────────────────────────────────────────────────

SCRIPT = """#!/usr/bin/env bash
set -euo pipefail
git tag -a "portico-portals-v1" -m "release of $RELEASE_SHA" "$RELEASE_SHA"
"""


def _train_repo(tmp_path, script=SCRIPT, pipelines=None):
    repo = _repo(tmp_path)
    (repo / ".github" / "bureau").mkdir(parents=True)
    (repo / "infra" / "release-portals.sh").write_text(script)
    data = {"surfaces": {"portals": _surface_entry(["infra/", "client/"])}}
    if pipelines is not None:
        data["linear_pipelines"] = pipelines
    (repo / ".github" / "bureau" / "release.json").write_text(json.dumps(data))
    _merge(repo, "agent/DRE-201-stack", "infra/stack.ts", "Merge agent/DRE-201-stack")
    return repo


def _green():
    return release_train.read_checks(
        [{"name": "ci", "status": "completed", "conclusion": "success"}])


def _release(repo, on_released=None, out=print):
    data = release_train.load(repo / ".github" / "bureau" / "release.json")
    return release_train.release(
        release_train.surfaces(data)["portals"], repo="o/portico", repo_root=repo,
        sha=_git(repo, "rev-parse", "HEAD"),
        now=datetime(2026, 9, 14, 10, 0, tzinfo=PT), checks=_green(),
        on_released=on_released, out=out)


def test_the_hook_runs_once_after_the_verified_tag_with_the_previous_tag(tmp_path):
    repo = _train_repo(tmp_path)
    seen, lines = [], []
    decision = _release(repo, on_released=lambda prev, d: seen.append((prev, d.tag)),
                        out=lines.append)
    assert decision.act == release_train.RELEASE, decision.reason
    assert seen == [("portico-portals-v0", "portico-portals-v1")]
    assert lines[-1].startswith(f"{release_train.TAG}: released")


def test_the_hook_never_runs_when_nothing_was_released(tmp_path):
    repo = _train_repo(tmp_path, script="#!/usr/bin/env bash\necho 'deferred: later'\n")
    seen = []
    decision = _release(repo, on_released=lambda prev, d: seen.append(d))
    assert decision.act == release_train.NO_OP and seen == []


def test_a_hook_that_raises_changes_neither_the_decision_nor_the_receipt(tmp_path):
    repo = _train_repo(tmp_path)

    def explode(prev, decision):
        raise RuntimeError("Linear is down")

    lines = []
    decision = _release(repo, on_released=explode, out=lines.append)
    assert decision.act == release_train.RELEASE
    assert any("WARNING the after-release step failed" in line for line in lines)
    assert lines[-1].startswith(f"{release_train.TAG}: released")


def test_the_cli_writes_the_linear_release_for_a_declared_surface(tmp_path, monkeypatch,
                                                                  capsys):
    repo = _train_repo(tmp_path, pipelines={"portals": PIPELINE})
    fake = FakeLinear()
    monkeypatch.setattr(release_linear, "_default_call", fake)
    monkeypatch.setattr(release_train, "fetch_checks", lambda repo, sha: _green())
    monkeypatch.setenv("LINEAR_API_KEY", "k")
    monkeypatch.delenv("RELEASE_HOLD", raising=False)
    code = release_train.main([
        "--file", str(repo / ".github" / "bureau" / "release.json"),
        "--repo-root", str(repo), "--repo", "dreadnought-foundry/portico",
        "release", "--sha", _git(repo, "rev-parse", "HEAD"), "--surface", "portals",
        "--dispatched"])
    out = capsys.readouterr().out
    assert code == 0, out
    assert fake.names()[:2] == ["releaseSync", "releaseComplete"]
    assert [r["identifier"] for r in fake.calls[0][1]["input"]["issueReferences"]] \
        == ["DRE-201"]
    assert "release-train: [portals] linear-release:" in out
    assert out.strip().splitlines()[-1].startswith("release-train: released")


def test_the_cli_touches_nothing_for_a_surface_with_no_pipeline(tmp_path, monkeypatch,
                                                                capsys):
    repo = _train_repo(tmp_path)
    fake = FakeLinear()
    monkeypatch.setattr(release_linear, "_default_call", fake)
    monkeypatch.setattr(release_train, "fetch_checks", lambda repo, sha: _green())
    monkeypatch.setenv("LINEAR_API_KEY", "k")
    monkeypatch.delenv("RELEASE_HOLD", raising=False)
    code = release_train.main([
        "--file", str(repo / ".github" / "bureau" / "release.json"),
        "--repo-root", str(repo), "--repo", "dreadnought-foundry/portico",
        "release", "--sha", _git(repo, "rev-parse", "HEAD"), "--surface", "portals",
        "--dispatched"])
    assert code == 0
    assert fake.calls == []
    assert "linear-release" not in capsys.readouterr().out
