"""A release's What's new lines become its `whats-new.json` asset — pinned
without GitHub (DRE-5513).

What this file pins:

  1. the pull requests are the ones the surface's own first-parent changes
     name since the previous tag — a `--merge` subject or a squash `(#n)` —
     each read once, and their sentences are collected newest first;
  2. the collector never asks whether the rule is switched on: the same
     sentences are collected whatever the cutover file says or whether it
     exists, and the module's source never names either cutover symbol;
  3. a `none`, a missing line, an exempt branch and a title with internal
     wording are each skipped, named in a printed `whats-new: ` line, and
     never raise;
  4. a first release and a release with nothing to say publish nothing;
  5. the asset is created on a tag with no release and clobbered on one that
     has a release, and the URL is the one `gh` printed;
  6. nothing GitHub does — a forbidden read, a refused release — raises;
  7. `shipped` is the injected clock on the Pacific clock, to the second;
  8. `publish --dry-run` prints the document and publishes nothing.

The git legs build a REAL repository in a temp directory, the way
tests/test_release_linear.py does; `gh` is a stub that answers like it.
"""

from __future__ import annotations

import inspect
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import whats_new  # noqa: E402
import whats_new_release  # noqa: E402

REPO = "dreadnought-foundry/portico"
TAG = "portico-portals-v1"
PREVIOUS = "portico-portals-v0"
NOW = datetime(2026, 10, 1, 21, 5, 30, 123456, tzinfo=timezone.utc)
RELEASE_URL = f"https://github.com/{REPO}/releases/tag/{TAG}"

TABLES = ("What's new: improved, everyone: Searching a document now finds "
          "words inside tables.")
QUEUE = ("What's new: fixed, moderators: Approving a flagged post no longer "
         "hides the next one in the queue.")


def _git(repo, *args, env=None):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True,
                          env={**os.environ, **(env or {})}).stdout.strip()


def _data():
    def entry(paths):
        return {"tag_series": ["portico-portals-v*"], "paths": list(paths)}
    return {"surfaces": {"portals": entry(["infra/", "client/"]),
                         "docs": entry(["infra/docs/"])}}


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
    _git(repo, "tag", "-a", PREVIOUS, "-m", "v0")
    return repo


def _touch(repo, path):
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(target.read_text() + "x\n" if target.exists() else "x\n")
    _git(repo, "add", "-A")


def _merge(repo, number, branch, path):
    _git(repo, "checkout", "-qb", branch)
    _touch(repo, path)
    _git(repo, "commit", "-qm", "work")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--no-ff", branch,
         "-m", f"Merge pull request #{number} from o/{branch}")
    return _git(repo, "rev-parse", "HEAD")


def _squash(repo, subject, path):
    _touch(repo, path)
    _git(repo, "commit", "-qm", subject)
    return _git(repo, "rev-parse", "HEAD")


def _scenario(tmp_path):
    """Two merges and one squash since the previous tag — #12, #15, #17."""
    repo = _repo(tmp_path)
    _merge(repo, 12, "agent/DRE-12-tables", "client/search.ts")
    _merge(repo, 15, "agent/DRE-15-chores", "infra/stack.ts")
    sha = _squash(repo, "Keep the queue in place after an approval (#17)", "client/queue.ts")
    return repo, sha


class FakeGh:
    """Records every command and answers the way `gh` does."""

    def __init__(self, pulls, *, release_exists=False, fail=None):
        self.pulls = pulls
        self.release_exists = release_exists
        self.fail = fail or {}
        self.calls = []
        self.envs = []
        self.published = []

    def __call__(self, argv, *, env=None):
        argv = list(argv)
        self.calls.append(argv)
        self.envs.append(env)
        key = " ".join(argv[:3])
        if key in self.fail:
            return self.fail[key]
        if argv[:2] == ["gh", "api"]:
            number = int(argv[2].rsplit("/", 1)[1])
            if number not in self.pulls:
                return 1, "", "gh: Not Found (HTTP 404)"
            head, body = self.pulls[number]
            return 0, json.dumps({"head": head, "body": body}) + "\n", ""
        if key == "gh release view":
            if self.release_exists:
                return 0, RELEASE_URL + "\n", ""
            return 1, "", "release not found\n"
        if key in ("gh release create", "gh release upload"):
            asset = Path(argv[4])
            self.published.append((asset.name, json.loads(asset.read_text())))
            return 0, (RELEASE_URL + "\n") if argv[2] == "create" else "", ""
        raise AssertionError(f"unexpected command {argv}")

    def api_reads(self):
        return [argv[2] for argv in self.calls if argv[:2] == ["gh", "api"]]

    def releases(self):
        return [argv for argv in self.calls if argv[:2] == ["gh", "release"]]


def _pulls():
    return {12: ("agent/DRE-12-tables", f"{TABLES}\n\nThe card says more."),
            15: ("agent/DRE-15-chores", "What's new: none\n"),
            17: ("agent/DRE-17-queue", f"**{QUEUE.replace(': ', ':** ', 1)}\n")}


def _write(repo, sha, gh, *, previous=PREVIOUS, lines=None, **kwargs):
    return whats_new_release.write(
        data=_data(), surface_name="portals", repo=REPO, repo_root=repo,
        version=TAG, sha=sha, previous_tag=previous, run=gh,
        env={"GH_TOKEN": "t"}, now=NOW,
        out=(lines.append if lines is not None else lambda line: None), **kwargs)


# ── 1. which pull requests, and what they say ──────────────────────────────


def test_a_release_collects_its_sentences_newest_first_and_drops_the_nones(tmp_path):
    repo, sha = _scenario(tmp_path)
    gh = FakeGh(_pulls())
    result = _write(repo, sha, gh)

    assert result["problem"] is None
    assert [item["title"] for item in result["items"]] == [
        "Approving a flagged post no longer hides the next one in the queue.",
        "Searching a document now finds words inside tables."]
    assert result["items"][0] == {"kind": "fixed", "audience": "moderators",
                                  "title": result["items"][0]["title"], "body": ""}
    assert (15, "none") in result["skipped"]
    # Each pull request is read once, with the documented `gh api` call.
    assert sorted(gh.api_reads()) == [f"repos/{REPO}/pulls/{n}" for n in (12, 15, 17)]
    read = next(argv for argv in gh.calls if argv[:2] == ["gh", "api"])
    assert read[3:] == ["--jq", "{head: .head.ref, body: .body}"]
    assert all(env and env.get("GH_TOKEN") == "t" for env in gh.envs)

    [(name, document)] = gh.published
    assert name == whats_new_release.ASSET_NAME == "whats-new.json"
    assert whats_new.validate(document) == []
    assert document["product"] == "portico"
    assert document["release"] == TAG
    assert document["items"] == result["items"]


def test_assemble_is_the_document_the_standard_describes():
    entries = [whats_new.Entry("new", "admins", "A page lists every portal.", "", "/portals")]
    document = whats_new_release.assemble(
        "portico", TAG, "2026-10-01T14:05:30-07:00", entries)
    assert document == {"product": "portico", "release": TAG,
                        "shipped": "2026-10-01T14:05:30-07:00",
                        "items": [entries[0].as_item()]}
    assert whats_new.validate(document) == []


# ── 2. the collector never asks whether the rule is switched on ────────────


@pytest.mark.parametrize("cutover", ["absent", "later"])
def test_the_cutover_never_changes_what_a_release_says(tmp_path, monkeypatch, cutover):
    repo, sha = _scenario(tmp_path)
    path = tmp_path / "whats-new-cutover.json"
    if cutover == "later":
        path.write_text(json.dumps({"enforced_from": "2099-01-01T00:00:00Z",
                                    "why": "Later than every pull request."}))
    monkeypatch.setattr(whats_new, "CUTOVER_FILE", path)
    result = _write(repo, sha, FakeGh(_pulls()))
    assert [item["kind"] for item in result["items"]] == ["fixed", "improved"]
    assert result["problem"] is None


def test_the_module_never_names_the_cutover_question():
    source = Path(whats_new_release.__file__).read_text()
    assert "enforced_for" not in source
    assert "CUTOVER_FILE" not in source
    docstring = " ".join((whats_new_release.__doc__ or "").split())
    assert ("this module never asks whether the What's New rule is switched on "
            "and never reads the cutover file") in docstring


# ── 3. what is skipped, and that it is named ───────────────────────────────


def test_a_missing_line_an_exempt_branch_and_internal_wording_are_skipped_by_name(tmp_path):
    repo, sha = _scenario(tmp_path)
    gh = FakeGh(_pulls())
    gh.pulls.update({12: ("agent/DRE-12-tables", "Nothing to see here.\n"),
                     15: ("dependabot/x", f"{TABLES}\n"),
                     17: ("agent/DRE-17-queue",
                          "What's new: fixed, moderators: The queue fix from DRE-5484 is in.\n")})
    lines = []
    result = _write(repo, sha, gh, lines=lines)

    reasons = dict(result["skipped"])
    assert reasons[12] == "no line"
    assert reasons[15] == "exempt"
    assert reasons[17].startswith("wording: ")
    assert "DRE-5484" in reasons[17]
    assert result["items"] == []
    for number in (12, 15, 17):
        assert any(line.startswith("whats-new: ") and f"#{number}" in line
                   and reasons[number] in line for line in lines), lines


def test_a_line_that_does_not_parse_is_skipped_and_named(tmp_path):
    repo, sha = _scenario(tmp_path)
    gh = FakeGh(_pulls())
    gh.pulls[12] = ("agent/DRE-12-tables", "What's new: shiny, everyone: Tables.\n")
    result = _write(repo, sha, gh)
    reasons = dict(result["skipped"])
    assert reasons[12].startswith("does not parse: ")
    assert "shiny" in reasons[12]
    assert len(result["items"]) == 1


def test_a_change_naming_no_pull_request_is_skipped_by_its_sha(tmp_path):
    repo, _ = _scenario(tmp_path)
    sha = _squash(repo, "A hand commit straight to main", "client/hand.ts")
    gh = FakeGh(_pulls())
    result = _write(repo, sha, gh)
    assert (sha[:7], "uncarded") in result["skipped"]
    assert len(gh.api_reads()) == 3


def test_a_change_outside_the_surface_contributes_nothing(tmp_path):
    repo, _ = _scenario(tmp_path)
    _merge(repo, 20, "agent/DRE-20-readme", "README.md")              # outside every path
    sha = _merge(repo, 21, "agent/DRE-21-docs", "infra/docs/page.md")  # the docs surface's
    gh = FakeGh(_pulls())
    gh.pulls.update({20: ("agent/DRE-20-readme", f"{TABLES}\n"),
                     21: ("agent/DRE-21-docs", f"{QUEUE}\n")})
    result = _write(repo, sha, gh)
    assert not any(read.endswith(("/20", "/21")) for read in gh.api_reads())
    assert len(result["items"]) == 2
    assert {number for number, _ in result["skipped"]} == {15}


# ── 4. nothing to publish ──────────────────────────────────────────────────


def test_a_first_release_collects_nothing_and_says_so(tmp_path):
    repo, sha = _scenario(tmp_path)
    gh = FakeGh(_pulls())
    lines = []
    result = _write(repo, sha, gh, previous=None, lines=lines)
    assert result["items"] == [] and result["url"] is None and result["problem"] is None
    assert gh.calls == []
    assert any(line.startswith("whats-new: ") and "first release" in line for line in lines)


def test_a_release_with_nothing_to_say_publishes_nothing(tmp_path):
    repo, sha = _scenario(tmp_path)
    gh = FakeGh(_pulls())
    gh.pulls.update({12: ("agent/DRE-12-tables", "What's new: none\n"),
                     17: ("agent/DRE-17-queue", "What's new: none\n")})
    lines = []
    result = _write(repo, sha, gh, lines=lines)
    assert result["items"] == [] and result["url"] is None and result["problem"] is None
    assert gh.releases() == []
    assert f"whats-new: nothing to say for {TAG} — nothing published" in lines


# ── 5. publishing ──────────────────────────────────────────────────────────


def test_a_tag_with_no_release_gets_one_created_with_the_asset(tmp_path):
    repo, sha = _scenario(tmp_path)
    gh = FakeGh(_pulls(), release_exists=False)
    result = _write(repo, sha, gh)
    view, create = gh.releases()
    assert view[:4] == ["gh", "release", "view", TAG]
    assert view[view.index("--repo") + 1] == REPO
    assert create[:4] == ["gh", "release", "create", TAG]
    assert Path(create[4]).name == "whats-new.json"
    assert "--verify-tag" in create
    assert create[create.index("--title") + 1] == TAG
    assert create[create.index("--notes") + 1] == f"What's new in {TAG}"
    assert create[create.index("--repo") + 1] == REPO
    assert result["url"] == RELEASE_URL


def test_a_tag_with_a_release_has_its_asset_clobbered(tmp_path):
    repo, sha = _scenario(tmp_path)
    gh = FakeGh(_pulls(), release_exists=True)
    result = _write(repo, sha, gh)
    view, upload = gh.releases()
    assert upload[:4] == ["gh", "release", "upload", TAG]
    assert Path(upload[4]).name == "whats-new.json"
    assert "--clobber" in upload
    assert upload[upload.index("--repo") + 1] == REPO
    assert not any(argv[2] == "create" for argv in gh.releases())
    assert result["url"] == RELEASE_URL
    assert result["problem"] is None


# ── 6. nothing GitHub does raises ──────────────────────────────────────────


def test_a_forbidden_read_names_the_missing_permission_and_publishes_nothing(tmp_path):
    repo, sha = _scenario(tmp_path)
    forbidden = (1, "", "gh: Resource not accessible by integration (HTTP 403)\n")
    gh = FakeGh(_pulls(), fail={f"gh api repos/{REPO}/pulls/17": forbidden})
    lines = []
    result = _write(repo, sha, gh, lines=lines)
    assert "caller stub lacks pull-requests: read" in result["problem"]
    assert result["items"] == [] and result["url"] is None
    assert gh.releases() == []
    assert any("caller stub lacks pull-requests: read" in line for line in lines)


def test_a_refused_release_returns_the_stderr_as_the_problem(tmp_path):
    repo, sha = _scenario(tmp_path)
    refused = (1, "", "HTTP 422: Validation Failed (tag_name was not found)\n")
    gh = FakeGh(_pulls(), fail={"gh release create": refused})
    result = _write(repo, sha, gh)
    assert "tag_name was not found" in result["problem"]
    assert result["url"] is None


def test_a_release_view_that_fails_otherwise_is_a_problem_not_a_create(tmp_path):
    repo, sha = _scenario(tmp_path)
    gh = FakeGh(_pulls(), fail={"gh release view": (1, "", "HTTP 502: Bad Gateway\n")})
    result = _write(repo, sha, gh)
    assert "Bad Gateway" in result["problem"]
    assert [argv[2] for argv in gh.releases()] == ["view"]


def test_an_unreadable_range_is_a_problem_not_an_exception(tmp_path):
    repo, sha = _scenario(tmp_path)
    result = _write(repo, sha, FakeGh(_pulls()), previous="no-such-tag")
    assert result["problem"]
    assert result["items"] == [] and result["url"] is None


# ── 7. the clock ───────────────────────────────────────────────────────────


def test_shipped_is_the_injected_clock_on_the_pacific_clock_to_the_second(tmp_path):
    repo, sha = _scenario(tmp_path)
    gh = FakeGh(_pulls())
    _write(repo, sha, gh)
    [(_, document)] = gh.published
    assert document["shipped"] == "2026-10-01T14:05:30-07:00"


# ── 8. the command line ────────────────────────────────────────────────────


def _cli(repo, sha, *extra):
    path = repo / ".github" / "bureau" / "release.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_data()))
    return whats_new_release.main([
        "publish", "--repo", REPO, "--surface", "portals", "--tag", TAG,
        "--previous-tag", PREVIOUS, "--sha", sha, "--file", str(path),
        "--repo-root", str(repo), *extra])


def test_publish_dry_run_prints_the_document_and_publishes_nothing(tmp_path, monkeypatch,
                                                                   capsys):
    repo, sha = _scenario(tmp_path)
    gh = FakeGh(_pulls())
    monkeypatch.setattr(whats_new_release, "_run", gh)
    assert _cli(repo, sha, "--dry-run") == 0
    out = capsys.readouterr().out
    match = re.search(r"^\{$.*?^\}$", out, re.M | re.S)
    assert match, out
    document = json.loads(match.group(0))
    assert whats_new.validate(document) == []
    assert len(document["items"]) == 2
    assert gh.releases() == []


def test_publish_without_dry_run_runs_the_same_write(tmp_path, monkeypatch, capsys):
    repo, sha = _scenario(tmp_path)
    gh = FakeGh(_pulls())
    monkeypatch.setattr(whats_new_release, "_run", gh)
    assert _cli(repo, sha) == 0
    assert [argv[2] for argv in gh.releases()] == ["view", "create"]
    [(_, document)] = gh.published
    assert len(document["items"]) == 2
    assert RELEASE_URL in capsys.readouterr().out
