"""Every train run records ONE decision message per surface (DRE-4771).

THE GAP THIS CLOSES. The train has always decided something about every
declared surface on every run, and until now that decision was one line in a
job's log. `scripts/release_decision.py` (DRE-4768) turned the decision into a
message GitHub delivers — a deployment plus its status, the decision as the
payload — and nothing called it. This file is the two call sites.

WHERE THE MESSAGE IS WRITTEN, ONE PER SURFACE PER RUN:

  * `_cmd_plan` writes for every surface whose decision is NOT `release` —
    held, every no-op, every refusal. Those are final for the run.
  * a surface the plan sends to a `Release <surface>` job is the surface job's
    to record: `_cmd_release` writes for whatever `release()` concludes, and
    `phase` says which job wrote it.

AND WHAT IT MAY NEVER DO. It may not change a decision, fail a run, or touch
the receipt line — the line agent-bureau's console parses
(`release_train_health.py`). The outcome is its own line,
`release-train: [<surface>] <clause>`, printed BEFORE the receipt so the
receipt stays the run's last line for that surface. A refused write is one
clause and never a raise: `standards/release-train.md`'s stub grants
`deployments: write`, and a stub that has not been updated is a
`decision not recorded: caller stub lacks deployments: write` clause on every
run.

Run: python3 -m pytest tests/test_release_train_decision.py -v
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import release_decision  # noqa: E402
import release_train  # noqa: E402

WORKFLOW = ROOT / ".github" / "workflows" / "release-train.yml"
STANDARD = ROOT / "standards" / "release-train.md"
DOC = ROOT / "docs" / "release-train.md"
REPO = "dreadnought-foundry/demo"

#: The id the fake `gh` answers the deployment POST with.
DEPLOYMENT_ID = 4771


# --------------------------------------------------------------------------
# The fixtures: a real git repository, and a fake `gh` on PATH.
# --------------------------------------------------------------------------

def _git(repo, *args, **kw):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True, **kw).stdout.strip()


def _head(repo):
    return _git(repo, "rev-parse", "HEAD")


def pt(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=release_train.PT)


def green():
    return release_train.read_checks(
        [{"name": "ci", "status": "completed", "conclusion": "success"}])


SCRIPT = """#!/usr/bin/env bash
set -euo pipefail
echo "releasing $2 at $RELEASE_SHA"
git tag -a "demo/v1" -m "demo release of $RELEASE_SHA" "$RELEASE_SHA"
"""

DEFERRING_SCRIPT = """#!/usr/bin/env bash
set -euo pipefail
echo "deferred: the console's first release is supervised by a person"
"""

FAILING_SCRIPT = """#!/usr/bin/env bash
echo "the migration failed" >&2
exit 3
"""


def _one_surface_repo(tmp_path, script=SCRIPT):
    """The single-surface caller the release-phase legs run over."""
    repo = tmp_path / "caller"
    (repo / "infra").mkdir(parents=True)
    (repo / "demo").mkdir()
    (repo / ".github" / "bureau").mkdir(parents=True)
    (repo / "demo" / "app.txt").write_text("v1\n")
    (repo / "infra" / "release-demo.sh").write_text(script)
    (repo / "infra" / "release-demo.sh").chmod(0o755)
    (repo / ".github" / "bureau" / "release.json").write_text(json.dumps({
        "surfaces": {"demo": {
            "tag_series": ["demo/v*"],
            "paths": ["demo/"],
            "script": "infra/release-demo.sh",
            "rollback": "make rollback-demo VERSION=<tag>",
            "spacing_minutes": 30,
            "window": "always",
            "auto": True,
            "identity": "demo-release",
            "record": "tag",
        }}}, indent=2) + "\n")
    _init(repo)
    return repo


def _three_surface_repo(tmp_path):
    """Three surfaces, and three different answers in one plan: `alpha` is
    behind and its walk chooses a green commit, `bravo` reads current, and
    `charlie` is `auto: false` — the no-op an unattended run never releases."""
    repo = tmp_path / "caller"
    (repo / "infra").mkdir(parents=True)
    for name in ("alpha", "bravo", "charlie"):
        (repo / name).mkdir()
        (repo / name / "app.txt").write_text("v1\n")
    (repo / ".github" / "bureau").mkdir(parents=True)
    for name in ("alpha", "bravo", "charlie"):
        (repo / "infra" / f"release-{name}.sh").write_text(SCRIPT)
        (repo / "infra" / f"release-{name}.sh").chmod(0o755)

    def declared(name, **over):
        data = {
            "tag_series": [f"{name}/v*"],
            "paths": [f"{name}/"],
            "script": f"infra/release-{name}.sh",
            "rollback": f"make rollback-{name} VERSION=<tag>",
            "spacing_minutes": 0,
            "window": "always",
            "auto": True,
            "identity": f"{name}-release",
            "record": "tag",
        }
        data.update(over)
        return data

    (repo / ".github" / "bureau" / "release.json").write_text(json.dumps({
        "surfaces": {
            "alpha": declared("alpha"),
            "bravo": declared("bravo"),
            "charlie": declared("charlie", auto=False),
        }}, indent=2) + "\n")
    _init(repo)
    # `bravo` and `charlie` stand at a tag; only `alpha` has changed since.
    for name in ("bravo", "charlie"):
        _git(repo, "tag", "-a", f"{name}/v0", "-m", "released")
    (repo / "alpha" / "app.txt").write_text("v2\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "a change alpha is behind on")
    return repo


def _init(repo):
    _git(repo.parent, "init", "-q", "-b", "main", str(repo))
    _git(repo, "config", "user.email", "train@example.com")
    _git(repo, "config", "user.name", "Release Train")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "the caller, as it stands")


#: A `gh` that answers the two POSTs the way GitHub does, and logs each one as
#: `<path>\t<body>`. The train reaches the deployments API through
#: `release_decision.gh`, which is `gh api … --method POST --input -` — so the
#: fake is on PATH rather than monkeypatched, and the subprocess seam is
#: exercised for real (the `tests/test_release_train.py` pattern).
_GH_OK = """#!/usr/bin/env bash
body=$(cat)
printf '%s\\t%s\\n' "$2" "$body" >> {log}
case "$2" in
  *statuses) echo '{{}}' ;;
  *) echo '{{"id": {ident}}}' ;;
esac
"""

_GH_REFUSES = """#!/usr/bin/env bash
cat > /dev/null
printf '%s\\t{{}}\\n' "$2" >> {log}
echo "{stderr}" >&2
exit 1
"""


def _fake_gh(tmp_path, monkeypatch, *, refuses=""):
    """A `gh` on PATH, and the log of every body it was POSTed."""
    log = tmp_path / "gh-posts.tsv"
    binary = tmp_path / "bin"
    binary.mkdir(exist_ok=True)
    body = (_GH_REFUSES.format(log=log, stderr=refuses) if refuses
            else _GH_OK.format(log=log, ident=DEPLOYMENT_ID))
    (binary / "gh").write_text(body)
    (binary / "gh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{binary}:{os.environ['PATH']}")
    return log


def _no_gh_on_path(tmp_path, monkeypatch):
    """A PATH with `git` and `bash` on it and no `gh` at all — the stub that
    was never updated is one failure mode; a runner without the CLI is the
    other, and neither may raise."""
    binary = tmp_path / "only-git"
    binary.mkdir(exist_ok=True)
    for tool in ("git", "bash", "python3", "env", "sh"):
        found = shutil.which(tool)
        if found:
            (binary / tool).symlink_to(found)
    monkeypatch.setenv("PATH", str(binary))
    assert shutil.which("gh") is None


def _run_env(monkeypatch, *, event="workflow_run", sha=""):
    monkeypatch.delenv(release_train.ENV_HOLD, raising=False)
    monkeypatch.setenv("GITHUB_RUN_ID", "34999000111")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    monkeypatch.setenv("GITHUB_EVENT_NAME", event)
    if sha:
        monkeypatch.setenv("GITHUB_SHA", sha)
    else:
        monkeypatch.delenv("GITHUB_SHA", raising=False)


def _posts(log):
    if not Path(log).exists():
        return []
    out = []
    for line in Path(log).read_text().splitlines():
        path, _, body = line.partition("\t")
        out.append((path, json.loads(body)))
    return out


def _messages(log):
    """Every decision message posted, as (deployment body, status body)."""
    posts = _posts(log)
    assert len(posts) % 2 == 0, posts
    pairs = []
    for (dep_path, dep), (state_path, state) in zip(posts[0::2], posts[1::2]):
        assert dep_path == f"repos/{REPO}/deployments", dep_path
        assert state_path == (f"repos/{REPO}/deployments/{DEPLOYMENT_ID}"
                              f"/statuses"), state_path
        pairs.append((dep, state))
    return pairs


def _records(log):
    return [dep["payload"] for dep, _ in _messages(log)]


def _receipt(out, act, name):
    return next(line for line in out.splitlines()
                if line.startswith(f"{release_train.TAG}: {act} {REPO} {name}"))


#: The ONE outcome line per surface. The full refusal text goes to the log
#: behind it, under `release_decision`'s own tag, so nothing is lost and the
#: clause stays one line.
_CLAUSE = re.compile(
    rf"^{release_train.TAG}: \[(?P<surface>[^\]]+)\] "
    rf"(?P<clause>decision (?:not )?recorded: .*)$")


def _clauses(out):
    return [m.group("surface", "clause")
            for m in (_CLAUSE.match(line) for line in out.splitlines()) if m]


def _plan(repo, monkeypatch, *, now=None, checks=None, extra=()):
    monkeypatch.setattr(release_train, "_now", lambda: now or pt(2026, 9, 24, 10))
    monkeypatch.setattr(release_train, "fetch_checks",
                        lambda _repo, _sha: checks or green())
    monkeypatch.setenv("GITHUB_OUTPUT", str(repo.parent / "github_output"))
    return release_train.main([
        "--repo", REPO, "--repo-root", str(repo),
        "--file", str(repo / ".github" / "bureau" / "release.json"),
        "plan", "--head", _head(repo), *extra,
    ])


def _release_cli(repo, monkeypatch, *, sha=None, checks=None):
    monkeypatch.setattr(release_train, "fetch_checks",
                        lambda _repo, _sha: checks or green())
    return release_train.main([
        "--repo", REPO, "--repo-root", str(repo),
        "--file", str(repo / ".github" / "bureau" / "release.json"),
        "release", "--sha", sha or _head(repo), "--surface", "demo",
    ])


def _matrix(repo):
    line = next(l for l in (repo.parent / "github_output").read_text().splitlines()
                if l.startswith("matrix="))
    return json.loads(line.partition("=")[2])


# --------------------------------------------------------------------------
# 1. The plan writes for every surface it does NOT release.
# --------------------------------------------------------------------------

def test_the_plan_records_the_surfaces_it_does_not_release_and_no_others(
        tmp_path, monkeypatch, capsys):
    """Three surfaces, three answers: two messages, both `phase: "plan"`, and
    none for the surface the plan hands to a `Release <surface>` job."""
    repo = _three_surface_repo(tmp_path)
    log = _fake_gh(tmp_path, monkeypatch)
    _run_env(monkeypatch)
    head = _head(repo)

    assert _plan(repo, monkeypatch) == 0
    out = capsys.readouterr().out

    # The matrix is what it has always been: the one releasing surface.
    assert _matrix(repo) == [{"surface": "alpha", "sha": head}]

    records = _records(log)
    assert [r["surface"] for r in records] == ["bravo", "charlie"]
    assert [r["code"] for r in records] == ["current", "auto-false"]
    for record in records:
        assert release_decision.check_record(record) == [], record
        assert record["phase"] == "plan"
        assert record["act"] == "no-op"
        assert record["repo"] == REPO
        # The plan's walk head, on both fields: no commit was chosen.
        assert record["sha"] == head and record["head"] == head
        assert record["version"] is None
        assert record["run_id"] == 34999000111
        assert record["run_attempt"] == 2
        assert record["event"] == "workflow_run"
        assert record["run_url"].endswith("/actions/runs/34999000111")
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ",
                            record["decided_at"])
        # The reason is the sentence the log line carries, verbatim.
        assert _receipt(out, "no-op", record["surface"]).endswith(record["reason"])

    # `bravo` and `charlie` both stand at a tag; `alpha`, which is not
    # recorded here, has none.
    assert [r["deployed"] for r in records] == ["bravo/v0", "charlie/v0"]
    assert "alpha" not in [r["surface"] for r in records]


def test_the_two_messages_are_the_contract_bodies(tmp_path, monkeypatch):
    repo = _three_surface_repo(tmp_path)
    log = _fake_gh(tmp_path, monkeypatch)
    _run_env(monkeypatch)
    assert _plan(repo, monkeypatch) == 0

    deployment, status = _messages(log)[0]
    record = deployment["payload"]
    assert deployment == {
        "ref": record["sha"],
        "environment": release_decision.ENVIRONMENT,
        "task": release_decision.TASK,
        "auto_merge": False,
        "required_contexts": [],
        "description": release_decision.description(record),
        "payload": record,
    }
    assert status == {
        "state": "inactive",
        "environment": release_decision.ENVIRONMENT,
        "description": release_decision.description(record),
        "log_url": record["run_url"],
        "auto_inactive": False,
    }
    assert deployment["description"].startswith(
        f"{release_decision.SCHEMA} no-op current bravo")


def test_the_brake_records_every_surface_as_held_and_releases_none(
        tmp_path, monkeypatch, capsys):
    """The brake is fleet-wide, so a braked run holds EVERY surface — the one
    state in which a `held` message is written, and it is written for all
    three."""
    repo = _three_surface_repo(tmp_path)
    log = _fake_gh(tmp_path, monkeypatch)
    _run_env(monkeypatch)
    monkeypatch.setenv(release_train.ENV_HOLD, "2026-09-24")

    assert _plan(repo, monkeypatch) == 0
    out = capsys.readouterr().out
    assert _matrix(repo) == []

    records = _records(log)
    assert [r["surface"] for r in records] == ["alpha", "bravo", "charlie"]
    for record in records:
        assert release_decision.check_record(record) == [], record
        assert record["act"] == "held" and record["code"] == "held"
        assert record["phase"] == "plan"
        assert record["hand_act"].startswith("gh variable delete RELEASE_HOLD")
        assert _receipt(out, "held", record["surface"]).endswith(record["reason"])
    assert [status["state"] for _, status in _messages(log)] == ["inactive"] * 3


def test_a_re_armed_no_op_records_the_reason_the_receipt_prints(
        tmp_path, monkeypatch, capsys):
    """DRE-3559's clause is part of the sentence the log carries, so it is part
    of the sentence the record carries — the write happens after the re-arm
    has been settled."""
    repo = _one_surface_repo(tmp_path)
    _git(repo, "tag", "-a", "demo/v0", "-m", "released",
         env={**os.environ, "GIT_COMMITTER_DATE": "2026-09-24T10:06:00-07:00"})
    (repo / "demo" / "app.txt").write_text("v2\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "a change the surface is behind on")
    log = _fake_gh(tmp_path, monkeypatch)
    _run_env(monkeypatch)
    monkeypatch.setattr(release_train, "fetch_armed_runs", lambda *a: [])
    monkeypatch.setattr(release_train, "dispatch_re_arm", lambda *a: None)
    (repo / ".github" / "workflows").mkdir(parents=True)
    (repo / ".github" / "workflows" / "release-train.yml").write_text(
        "on:\n  workflow_dispatch:\n    inputs:\n      not_before:\n"
        "        type: string\n")

    assert _plan(repo, monkeypatch, now=pt(2026, 9, 24, 10, 23), extra=[
        "--re-arm", "--workflow", ".github/workflows/release-train.yml",
        "--default-branch", "main"]) == 0

    record = _records(log)[0]
    assert record["code"] == "spacing"
    assert "— re-armed for 10:36 PT" in record["reason"]
    assert record["re_arm_at"] == "2026-09-24T17:36:00Z"
    assert _receipt(capsys.readouterr().out, "no-op", "demo").endswith(
        record["reason"])


# --------------------------------------------------------------------------
# 2. The surface job writes for whatever its own decision turns out to be.
# --------------------------------------------------------------------------

def test_a_release_records_one_message_naming_the_tag_it_cut(tmp_path,
                                                             monkeypatch):
    repo = _one_surface_repo(tmp_path)
    log = _fake_gh(tmp_path, monkeypatch)
    sha = _head(repo)
    _run_env(monkeypatch, sha=sha)

    assert _release_cli(repo, monkeypatch, sha=sha) == 0
    assert len(_messages(log)) == 1, _records(log)
    deployment, status = _messages(log)[0]
    record = deployment["payload"]
    assert release_decision.check_record(record) == [], record
    assert record["phase"] == "release"
    assert record["act"] == "release" and record["code"] == "released"
    assert record["version"] == "demo/v1"
    assert record["sha"] == sha and record["head"] == sha
    assert record["deployed"] is None       # the surface's first release
    assert record["hand_act"] is None
    assert status["state"] == "success"


def test_a_deferral_records_a_no_op_whose_hand_act_points_at_the_line(
        tmp_path, monkeypatch):
    repo = _one_surface_repo(tmp_path, script=DEFERRING_SCRIPT)
    log = _fake_gh(tmp_path, monkeypatch)
    _run_env(monkeypatch, sha=_head(repo))

    assert _release_cli(repo, monkeypatch) == 0
    deployment, status = _messages(log)[0]
    record = deployment["payload"]
    assert release_decision.check_record(record) == [], record
    assert record["phase"] == "release"
    assert record["act"] == "no-op" and record["code"] == "deferred"
    assert record["version"] is None
    assert record["hand_act"].startswith(release_decision.DEFERRAL_PREFIX)
    assert "demo" in record["hand_act"]
    assert record["reason"] == (
        "deferred: the console's first release is supervised by a person")
    assert status["state"] == "inactive"


def test_a_failed_script_records_a_refusal_that_hands_back_the_rollback(
        tmp_path, monkeypatch):
    repo = _one_surface_repo(tmp_path, script=FAILING_SCRIPT)
    log = _fake_gh(tmp_path, monkeypatch)
    _run_env(monkeypatch, sha=_head(repo))

    assert _release_cli(repo, monkeypatch) == 1
    deployment, status = _messages(log)[0]
    record = deployment["payload"]
    assert release_decision.check_record(record) == [], record
    assert record["act"] == "refuse" and record["code"] == "script-failed"
    assert record["hand_act"] == "make rollback-demo VERSION=<tag>"
    assert status["state"] == "failure"


def test_a_job_that_queued_behind_a_sibling_records_the_current_no_op(
        tmp_path, monkeypatch):
    """The DRE-3263 collapse: the lane's second job reads current. It decided
    something, so it records it — with the tag its sibling cut as `deployed`
    and nothing as `version`."""
    repo = _one_surface_repo(tmp_path)
    data = release_train.load(repo / ".github" / "bureau" / "release.json")
    first = release_train.release(
        release_train.surfaces(data)["demo"], repo=REPO, repo_root=repo,
        sha=_head(repo), now=pt(2026, 9, 24, 10), checks=green())
    assert first.act == release_train.RELEASE

    log = _fake_gh(tmp_path, monkeypatch)
    _run_env(monkeypatch, sha=_head(repo))
    assert _release_cli(repo, monkeypatch) == 0

    deployment, status = _messages(log)[0]
    record = deployment["payload"]
    assert release_decision.check_record(record) == [], record
    assert record["phase"] == "release"
    assert record["act"] == "no-op" and record["code"] == "current"
    assert record["deployed"] == "demo/v1"
    assert record["version"] is None
    assert status["state"] == "inactive"


def test_an_unreadable_checks_api_is_still_one_recorded_refusal(tmp_path,
                                                               monkeypatch):
    """FAIL CLOSED is a decision like any other, and it is made outside
    `release()` — the branch that would otherwise record nothing."""
    repo = _one_surface_repo(tmp_path)
    log = _fake_gh(tmp_path, monkeypatch)
    _run_env(monkeypatch, sha=_head(repo))

    def cannot_answer(_repo, _sha):
        raise RuntimeError("gh could not read the checks")
    monkeypatch.setattr(release_train, "fetch_checks", cannot_answer)
    assert release_train.main([
        "--repo", REPO, "--repo-root", str(repo),
        "--file", str(repo / ".github" / "bureau" / "release.json"),
        "release", "--sha", _head(repo), "--surface", "demo"]) == 1

    record = _records(log)[0]
    assert record["code"] == "unreadable" and record["phase"] == "release"
    assert _messages(log)[0][1]["state"] == "failure"


# --------------------------------------------------------------------------
# 3. The receipt line is untouched, and the outcome is its own line.
# --------------------------------------------------------------------------

def test_the_outcome_is_its_own_line_before_the_receipt(tmp_path, monkeypatch,
                                                        capsys):
    repo = _one_surface_repo(tmp_path)
    log = _fake_gh(tmp_path, monkeypatch)
    _run_env(monkeypatch, sha=_head(repo))
    assert _release_cli(repo, monkeypatch) == 0

    lines = capsys.readouterr().out.splitlines()
    clause = f"{release_train.TAG}: [demo] decision recorded: deployment {DEPLOYMENT_ID}"
    recorded = next(i for i, line in enumerate(lines) if line.startswith(clause))
    receipt = next(i for i, line in enumerate(lines)
                   if line.startswith(f"{release_train.TAG}: released {REPO} demo"))
    assert recorded < receipt, lines
    # Never appended to the receipt — that is the line the console parses.
    assert "decision recorded" not in lines[receipt]
    assert lines[receipt] == lines[-1]
    assert _messages(log)


def test_the_receipt_line_is_byte_for_byte_what_it_was_without_the_message(
        tmp_path, monkeypatch, capsys):
    """The same plan, with the message refused and with it written: the
    receipt lines are identical."""
    repo = _three_surface_repo(tmp_path)
    _run_env(monkeypatch)

    _no_gh_on_path(tmp_path, monkeypatch)
    assert _plan(repo, monkeypatch) == 0
    without = [l for l in capsys.readouterr().out.splitlines()
               if l.startswith(f"{release_train.TAG}: ") and "] " not in l]

    _fake_gh(tmp_path, monkeypatch)
    assert _plan(repo, monkeypatch) == 0
    with_message = [l for l in capsys.readouterr().out.splitlines()
                    if l.startswith(f"{release_train.TAG}: ") and "] " not in l]
    assert without == with_message


# --------------------------------------------------------------------------
# 4. A refused write is one clause. No path raises.
# --------------------------------------------------------------------------

def test_a_stub_without_deployments_write_leaves_the_plan_exactly_as_it_was(
        tmp_path, monkeypatch, capsys):
    repo = _three_surface_repo(tmp_path)
    head = _head(repo)
    _fake_gh(tmp_path, monkeypatch, refuses=(
        "HTTP 403: Resource not accessible by integration "
        "(https://api.github.com/repos/dreadnought-foundry/demo/deployments)"))
    _run_env(monkeypatch)

    assert _plan(repo, monkeypatch) == 0
    assert _matrix(repo) == [{"surface": "alpha", "sha": head}]
    out = capsys.readouterr().out
    assert _clauses(out) == [
        ("bravo", "decision not recorded: caller stub lacks deployments: "
                  "write (standards/release-train.md)"),
        ("charlie", "decision not recorded: caller stub lacks deployments: "
                    "write (standards/release-train.md)"),
    ], out


def test_a_gh_that_is_absent_from_path_is_one_clause_and_never_a_raise(
        tmp_path, monkeypatch, capsys):
    repo = _three_surface_repo(tmp_path)
    head = _head(repo)
    _no_gh_on_path(tmp_path, monkeypatch)
    _run_env(monkeypatch)

    assert _plan(repo, monkeypatch) == 0
    assert _matrix(repo) == [{"surface": "alpha", "sha": head}]
    clauses = _clauses(capsys.readouterr().out)
    assert [surface for surface, _ in clauses] == ["bravo", "charlie"]
    for _, clause in clauses:
        assert clause.startswith("decision not recorded:")
        assert "gh" in clause


def test_a_refused_write_never_changes_a_release(tmp_path, monkeypatch,
                                                 capsys):
    repo = _one_surface_repo(tmp_path)
    _fake_gh(tmp_path, monkeypatch, refuses="HTTP 500: upstream is having a day")
    _run_env(monkeypatch, sha=_head(repo))

    assert _release_cli(repo, monkeypatch) == 0
    assert _git(repo, "tag", "-l").splitlines() == ["demo/v1"]
    out = capsys.readouterr().out
    assert "decision not recorded:" in out
    assert f"{release_train.TAG}: released {REPO} demo" in out


# --------------------------------------------------------------------------
# 5. The documents: the stub's grant, the premortem, the render.
# --------------------------------------------------------------------------

def _reference_stub():
    for block in re.findall(r"```yaml\n(.*?)```", STANDARD.read_text(), re.S):
        if "release-train.yml@stable" in block:
            return yaml.safe_load(block)
    raise AssertionError("standards/release-train.md carries no stub block")


def test_the_published_stub_grants_deployments_write_and_says_what_for():
    assert _reference_stub()["permissions"]["deployments"] == "write"
    body = STANDARD.read_text()
    assert ("**`deployments: write` is what the train records its decision "
            "with**") in body, (
        "standards/release-train.md never says what the grant is for")
    assert "decision not recorded: caller stub lacks deployments: write" in body


def test_the_workflow_header_answers_the_premortem_for_the_message():
    header = "\n".join(
        line for line in WORKFLOW.read_text().splitlines()
        if line.startswith("#") or not line.strip())
    for needle in ("github-actions[bot]", "deployments: write",
                   "auto_inactive", "required_contexts", "run_attempt",
                   "DRE-4771"):
        assert needle in header, needle
    # Q5 no longer claims the tag is the only record the train writes.
    assert "the only record the train writes" not in header
    assert "the tag remains the only record a release" in header


def test_the_render_points_at_the_decisions_own_document():
    rendered = release_train.render_markdown()
    assert "docs/release-decision.md" in rendered
    assert DOC.read_text() == rendered, (
        "docs/release-train.md is stale — regenerate it with "
        "`python3 scripts/release_train.py render`")


def test_the_render_names_the_two_runs_that_are_deliberately_not_recorded():
    """Two runs decide nothing per surface, and the document says so rather
    than leaving a reader to notice the absence."""
    section = release_train.render_markdown().partition(
        "## The decision, as a message GitHub delivers")[2]
    assert section, "the render carries no decision-message section"
    assert "docs/release-decision.md" in section
    assert "promote-channel.yml" in section
    assert "did not conclude `success`" in section
