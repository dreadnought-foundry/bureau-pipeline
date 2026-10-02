"""The train publishes `whats-new.json` after every verified tag, and the Linear
release note carries the same entries under `## What's new` (DRE-5516).

What this file pins:

  1. the `release` command's after-release hook calls the collector
     (`whats_new_release.write`) with exactly DRE-5513's keywords, BEFORE the
     Linear writer, and hands the Linear writer the collected items as
     `whats_new`;
  2. a collector that could not publish says so where a person looks: one
     `::warning` annotation and a `## What's new not published` block on the
     run's summary page — and says nothing at all when it published;
  3. a collector that raises is caught by `release()`'s existing guard: the
     decision is still `released`, the receipt is still the last line, and one
     `WARNING` line names the failure;
  4. the Linear note renders a `## What's new` section above the card bullets,
     and is byte for byte what it was when there is nothing to say;
  5. a surface that declares no pipeline gets no Linear call, items or not.

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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import release_linear  # noqa: E402
import release_train  # noqa: E402
import whats_new_release  # noqa: E402

PIPELINE = "0c1d2e3f-4a5b-4c6d-8e7f-901234567890"

ITEMS = [
    {"kind": "improved", "audience": "everyone",
     "title": "Searching a document now finds words inside tables.",
     "body": "", "open": "/documents"},
    {"kind": "fixed", "audience": "moderators",
     "title": "Approving a flagged post no longer hides the next one in the queue.",
     "body": "It stays where it was."},
]

#: The keyword set DRE-5513's `write` declares and the hook passes.
COLLECTOR_KEYWORDS = {"data", "surface_name", "repo", "repo_root", "version",
                      "sha", "previous_tag", "out"}

SCRIPT = """#!/usr/bin/env bash
set -euo pipefail
git tag -a "portico-portals-v1" -m "release of $RELEASE_SHA" "$RELEASE_SHA"
"""


def _git(repo, *args, env=None):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True,
                          env={**os.environ, **(env or {})}).stdout.strip()


def _surface_entry(paths):
    return {
        "tag_series": ["portico-portals-v*"],
        "paths": list(paths),
        "script": "infra/release-portals.sh",
        "rollback": "make rollback-portals VERSION=<tag>",
        "spacing_minutes": 30,
        "window": "always",
        "auto": True,
        "identity": "portico-release-role",
        "record": "tag",
    }


def _train_repo(tmp_path, pipelines=None):
    repo = tmp_path / "portico"
    (repo / "infra").mkdir(parents=True)
    (repo / "infra" / "stack.ts").write_text("v0\n")
    _git(tmp_path, "init", "-q", "-b", "main", str(repo))
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "the start")
    _git(repo, "tag", "-a", "portico-portals-v0", "-m", "v0",
         env={"GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z"})
    (repo / ".github" / "bureau").mkdir(parents=True)
    (repo / "infra" / "release-portals.sh").write_text(SCRIPT)
    data = {"surfaces": {"portals": _surface_entry(["infra/"])}}
    if pipelines is not None:
        data["linear_pipelines"] = pipelines
    (repo / ".github" / "bureau" / "release.json").write_text(json.dumps(data))
    (repo / "infra" / "stack.ts").write_text("v1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "the change (#7)")
    return repo


def _green():
    return release_train.read_checks(
        [{"name": "ci", "status": "completed", "conclusion": "success"}])


@pytest.fixture
def train(tmp_path, monkeypatch):
    """A releasable repo, the CLI wired the way the workflow runs it, a step
    summary pointed at a temp file, and both writers recorded in one list."""
    repo = _train_repo(tmp_path)
    summary = tmp_path / "step-summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.delenv("RELEASE_HOLD", raising=False)
    monkeypatch.delenv("GITHUB_SHA", raising=False)
    monkeypatch.setattr(release_train, "fetch_checks", lambda repo, sha: _green())
    # The decision message is DRE-4771's, not this card's: kept off the network.
    monkeypatch.setattr(release_train, "_record_decision", lambda *a, **k: "")
    calls = []

    def linear_write(**kwargs):
        calls.append(("linear", kwargs))
        return None

    monkeypatch.setattr(release_linear, "write", linear_write)

    def stub_collector(result=None, raises=None):
        def collector(**kwargs):
            calls.append(("collector", kwargs))
            if raises is not None:
                raise raises
            return result
        monkeypatch.setattr(whats_new_release, "write", collector)

    def run():
        code = release_train.main([
            "--file", str(repo / ".github" / "bureau" / "release.json"),
            "--repo-root", str(repo), "--repo", "dreadnought-foundry/portico",
            "release", "--sha", _git(repo, "rev-parse", "HEAD"),
            "--surface", "portals", "--dispatched"])
        return code

    def summary_text():
        return summary.read_text() if summary.exists() else ""

    return {"repo": repo, "calls": calls, "stub": stub_collector, "run": run,
            "summary": summary_text}


def _result(items=(), problem=None):
    return {"items": list(items), "skipped": [], "url": None, "problem": problem}


# ── 1. the hook calls the collector first and feeds Linear its items ────────


def test_the_hook_collects_before_linear_and_passes_the_items(train, capsys):
    train["stub"](_result(ITEMS))
    code = train["run"]()
    out = capsys.readouterr().out
    assert code == 0, out
    assert [name for name, _ in train["calls"]] == ["collector", "linear"]
    assert train["calls"][1][1]["whats_new"] == ITEMS
    assert out.strip().splitlines()[-1].startswith("release-train: released")


def test_the_collector_gets_exactly_dre_5513s_keywords(train, capsys):
    train["stub"](_result(ITEMS))
    sha = _git(train["repo"], "rev-parse", "HEAD")
    assert train["run"]() == 0
    collected = train["calls"][0][1]
    assert set(collected) == COLLECTOR_KEYWORDS
    assert collected["surface_name"] == "portals"
    assert collected["repo"] == "dreadnought-foundry/portico"
    assert collected["version"] == "portico-portals-v1"
    assert collected["previous_tag"] == "portico-portals-v0"
    assert collected["sha"] == sha
    assert collected["repo_root"] == str(train["repo"])
    assert "portals" in collected["data"]["surfaces"]
    # The Linear writer is called with the same release, as it was before.
    linear = train["calls"][1][1]
    for key in COLLECTOR_KEYWORDS - {"out"}:
        assert linear[key] == collected[key], key


def test_the_collector_prints_inside_the_trains_surface_prefix(train, capsys):
    train["stub"](_result())
    assert train["run"]() == 0
    capsys.readouterr()
    dict(train["calls"])["collector"]["out"]("whats-new: hello")
    assert capsys.readouterr().out == "release-train: [portals] whats-new: hello\n"


# ── 2. a collector that could not publish says so on the run ────────────────


def test_a_problem_is_an_annotation_and_a_summary_block(train, capsys):
    problem = whats_new_release.FORBIDDEN
    train["stub"](_result(problem=problem))
    assert train["run"]() == 0
    out = capsys.readouterr().out
    annotations = [line for line in out.splitlines()
                   if line.startswith("::warning title=What's new not published::")]
    assert len(annotations) == 1, out
    assert problem in annotations[0]
    summary = train["summary"]()
    assert "## What's new not published" in summary
    assert problem in summary
    assert "`pull-requests: read`" in summary
    # Linear is still written, with nothing to say.
    assert [name for name, _ in train["calls"]] == ["collector", "linear"]
    assert train["calls"][1][1]["whats_new"] == []
    assert out.strip().splitlines()[-1].startswith("release-train: released")


def test_the_annotation_comes_before_the_linear_release(train, capsys, monkeypatch):
    train["stub"](_result(problem="caller stub lacks pull-requests: read"))
    printed_before_linear = []
    monkeypatch.setattr(release_linear, "write", lambda **kwargs: printed_before_linear
                        .append(capsys.readouterr().out))
    assert train["run"]() == 0
    assert len(printed_before_linear) == 1
    assert "::warning title=What's new not published::" in printed_before_linear[0]


def test_no_problem_writes_neither_annotation_nor_block(train, capsys):
    train["stub"](_result(ITEMS))
    assert train["run"]() == 0
    out = capsys.readouterr().out
    assert "What's new not published" not in out
    assert "What's new not published" not in train["summary"]()


# ── 3. a collector that raises is the existing guard's one warning ──────────


def test_a_collector_that_raises_never_changes_the_release(train, capsys):
    train["stub"](raises=RuntimeError("the collector fell over"))
    code = train["run"]()
    out = capsys.readouterr().out
    assert code == 0, out
    warnings = [line for line in out.splitlines()
                if "WARNING the after-release step failed" in line]
    assert len(warnings) == 1, out
    assert "the collector fell over" in warnings[0]
    last = out.strip().splitlines()[-1]
    assert last.startswith("release-train: released"), last


# ── 4. the note's What's new section ────────────────────────────────────────

CARDS = [{"identifier": "DRE-7", "title": "The train names its cards", "description": ""}]


def test_the_note_renders_whats_new_above_the_card_bullets():
    note = release_linear.assemble_note(
        label="portals", version="portals-v1.0.1", cards=CARDS, unnamed=[],
        first=False, whats_new=ITEMS)
    lines = note.splitlines()
    # The headline is DRE-3854's, unchanged (tests/test_release_linear.py).
    assert lines[0] == "## portals v1.0.1"
    heading = lines.index("## What's new")
    improved = lines.index(
        "* Improved — Searching a document now finds words inside tables.")
    fixed = lines.index("* Fixed — Approving a flagged post no longer hides the "
                        "next one in the queue. It stays where it was.")
    card = lines.index("* The train names its cards. (DRE-7)")
    assert 0 < heading < improved < fixed < card


def test_an_empty_whats_new_leaves_the_note_byte_for_byte():
    for cards, unnamed, first in ((CARDS, [], False), ([], None, False),
                                  (CARDS, ["abc1234"], False), ([], [], True)):
        before = release_linear.assemble_note(
            label="portals", version="portals-v1.0.1", cards=cards,
            unnamed=unnamed, first=first)
        for empty in ((), []):
            assert release_linear.assemble_note(
                label="portals", version="portals-v1.0.1", cards=cards,
                unnamed=unnamed, first=first, whats_new=empty) == before
        assert "What's new" not in before


class _FakeLinear:
    def __init__(self):
        self.calls = []

    def __call__(self, query, variables):
        name = next(op for op in ("releaseSync", "releaseComplete",
                                  "releaseNoteCreate", "release(") if op in query)
        self.calls.append((name, variables))
        release = {"id": "rel-1", "url": "https://linear.app/x/release/rel-1"}
        if name in ("releaseSync", "releaseComplete"):
            return {name: {"success": True, "release": release}}
        if name == "release(":
            return {"release": {**release, "releaseNotes": [],
                                "issues": {"nodes": CARDS}}}
        return {name: {"success": True}}


def test_write_hands_the_items_to_the_note(tmp_path):
    repo = _train_repo(tmp_path, pipelines={"portals": PIPELINE})
    data = json.loads((repo / ".github" / "bureau" / "release.json").read_text())
    fake = _FakeLinear()
    result = release_linear.write(
        data=data, surface_name="portals", repo="o/portico", repo_root=repo,
        version="portico-portals-v1", sha=_git(repo, "rev-parse", "HEAD"),
        previous_tag="portico-portals-v0", call=fake, env={"LINEAR_API_KEY": "k"},
        out=lambda _: None, whats_new=ITEMS)
    assert result["problem"] is None
    note = next(v["input"]["content"] for name, v in fake.calls
                if name == "releaseNoteCreate")
    assert "## What's new" in note
    assert "* Improved — Searching a document now finds words inside tables." in note


# ── 5. no pipeline, no Linear — the items are dropped by design ─────────────


def test_a_surface_with_no_pipeline_gets_no_linear_call(tmp_path):
    repo = _train_repo(tmp_path)
    data = json.loads((repo / ".github" / "bureau" / "release.json").read_text())
    fake = _FakeLinear()
    result = release_linear.write(
        data=data, surface_name="portals", repo="o/portico", repo_root=repo,
        version="portico-portals-v1", sha=_git(repo, "rev-parse", "HEAD"),
        previous_tag="portico-portals-v0", call=fake, env={"LINEAR_API_KEY": "k"},
        out=lambda _: None, whats_new=ITEMS)
    assert result is None
    assert fake.calls == []
