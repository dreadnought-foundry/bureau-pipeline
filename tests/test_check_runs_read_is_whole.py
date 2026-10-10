"""The merge gate and the sweep read every check run on a commit (DRE-6532).

`GET repos/{repo}/commits/{sha}/check-runs` answers 30 runs a page, newest
first, so the runs that fall off an unpaged read are the oldest ones: the CI
jobs. On 2026-10-09 agent-bureau's `main` commit 21ca627e carried 143 check
runs; the unpaged read returned 30 and none of them was a `Console backend`
check. Three readers decided on whatever came back:

  1. the merge gate (`evaluate_and_merge.sh` → `merge_gate.py precheck`, then
     the full decision), which counted "N of total not green" over the runs
     in its file, so a red CI job off the page read as green;
  2. `reconcile.fix_approved_but_red`, which counted the failed runs on an
     approved head, so a failed job off the page counted as zero;
  3. `reconcile._review_checks_at_head`, which listed the review-named runs,
     so a review check off the page read as "no review check at this head".

Each now reads every page (`gh api --paginate --slurp …?per_page=100`). The
script-level proof — the gate's one call carries the flags, and a failed read
stops the step — is in tests/test_merge_gate_one_read.py.

Run: python3 -m pytest tests/test_check_runs_read_is_whole.py -v
"""

from __future__ import annotations

import ast
import json
import os
import subprocess  # nosec B404 — fixed argv, our own script
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/test")
os.environ.setdefault("GH_TOKEN", "test")

import merge_gate  # noqa: E402
import reconcile  # noqa: E402

SHA = "c" * 40
CI_SUITE = 1
FAILED_JOB = "Console backend"
# The CI run that owns suite 1, finished — so condition 1 waits on the check
# runs alone, never on a run still going.
CI_RUN_DONE = {"id": 11, "name": "CI", "path": ".github/workflows/ci.yml",
               "event": "pull_request", "status": "completed",
               "check_suite_id": CI_SUITE}


def _run(name: str, conclusion: str = "success") -> dict:
    return {"name": name, "status": "completed", "conclusion": conclusion,
            "check_suite": {"id": CI_SUITE}}


def runs_143() -> list:
    """agent-bureau 21ca627e's count, newest first: 142 green, then the one
    failed CI job, last of all — the oldest run, and so the last one GitHub
    pages out."""
    return ([_run(f"job {i}") for i in range(142)]
            + [_run(FAILED_JOB, "failure")])


def pages(runs: list, size: int = 100) -> list:
    """What `gh api --paginate --slurp` writes: one object per page."""
    return [{"total_count": len(runs), "check_runs": runs[i:i + size]}
            for i in range(0, len(runs), size)]


def _write(td: Path, name: str, payload) -> str:
    path = td / name
    path.write_text(json.dumps(payload))
    return str(path)


# --------------------------------------------------------------------------
# 1. The gate's reader takes every page
# --------------------------------------------------------------------------
def test_the_fixture_is_two_pages_with_the_failure_last_on_the_second():
    paged = pages(runs_143())
    assert [len(p["check_runs"]) for p in paged] == [100, 43]
    assert paged[1]["check_runs"][-1]["conclusion"] == "failure"


def test_a_red_job_on_the_second_page_waits(tmp_path):
    """The replay: 143 runs in two pages, the one red job last. Read through
    `_read_check_runs`, both the full condition 1 and the precheck wait."""
    runs = merge_gate._read_check_runs(_write(tmp_path, "cr.json", pages(runs_143())))
    assert len(runs) == 143
    decision = merge_gate.evaluate_checks(runs)
    assert decision is not None and decision.action == "wait"
    assert "1 of 143 check runs not green" in decision.reason
    early = merge_gate.precheck(runs, [CI_RUN_DONE])
    assert early is not None and early.action == "wait"
    assert "1 of 143 check runs not green" in early.reason


def test_the_first_30_runs_alone_read_green():
    """Today's reading, on record: the unpaged read is the first 30 runs, and
    nothing in them is red. That is the cut this card closes."""
    first_page = runs_143()[:30]
    assert merge_gate.evaluate_checks(first_page) is None


def test_every_shape_reads_the_same_runs(tmp_path):
    runs = runs_143()
    shapes = {
        "bare object": {"total_count": len(runs), "check_runs": runs},
        "flat list": runs,
        "array of pages": pages(runs),
    }
    for shape, payload in shapes.items():
        got = merge_gate._read_check_runs(_write(tmp_path, "cr.json", payload))
        assert got == runs, shape


def test_one_empty_page_is_no_runs(tmp_path):
    """A head with no check runs yet slurps to one page holding none."""
    path = _write(tmp_path, "cr.json", [{"total_count": 0, "check_runs": []}])
    assert merge_gate._read_check_runs(path) == []


@pytest.mark.parametrize("payload", [
    pytest.param([_run("unit"), "not a run"], id="flat-list-item-not-an-object"),
    pytest.param([{"total_count": 1, "check_runs": [_run("unit")]}, 7],
                 id="page-list-item-not-an-object"),
    pytest.param([{"total_count": 1, "check_runs": {"name": "unit"}}],
                 id="page-check-runs-not-a-list"),
    pytest.param([{"total_count": 1, "check_runs": None}],
                 id="page-check-runs-null"),
    pytest.param({"total_count": 0}, id="bare-object-with-no-list"),
    pytest.param({"check_runs": "x"}, id="bare-object-list-not-a-list"),
    pytest.param("check runs", id="a-string"),
    pytest.param(None, id="null"),
])
def test_an_unreadable_record_exits_2(tmp_path, payload):
    path = _write(tmp_path, "cr.json", payload)
    with pytest.raises(SystemExit) as exc:
        merge_gate._read_check_runs(path)
    assert exc.value.code == 2


# --------------------------------------------------------------------------
# 2. The refused merge's explanation reads the paged file
# --------------------------------------------------------------------------
OWNERS = {"rules": [{"type": "required_status_checks", "parameters": {
    "required_status_checks": [{"context": FAILED_JOB}, {"context": "job 141"},
                               {"context": "job 0"}]}}]}


def _explain(td: Path, check_runs_payload) -> subprocess.CompletedProcess:
    return subprocess.run(  # nosec B603 B607 — fixed argv, our own script
        [sys.executable, str(ROOT / "scripts" / "code_owner_hold.py"), "explain",
         "--owners-file", _write(td, "owners.json", OWNERS),
         "--check-runs-file", _write(td, "cr.json", check_runs_payload),
         "--author", "agent-bureau-bot[bot]"],
        capture_output=True, text=True, check=False)


def test_explain_prints_the_same_lines_for_pages_and_one_object(tmp_path):
    runs = runs_143()
    flat = _explain(tmp_path, {"total_count": len(runs), "check_runs": runs})
    paged = _explain(tmp_path, pages(runs))
    assert flat.returncode == 0 and paged.returncode == 0, (flat.stderr, paged.stderr)
    assert paged.stdout == flat.stdout
    # Non-vacuous: the lines name runs on both pages, green and red.
    assert f"required check {FAILED_JOB!r}: NOT satisfied" in paged.stdout
    assert "required check 'job 141': satisfied" in paged.stdout
    assert "required check 'job 0': satisfied" in paged.stdout


# --------------------------------------------------------------------------
# 3. The two sweep readers take every page
# --------------------------------------------------------------------------
REVIEW_CHECK = "call / review"


def head_runs() -> list:
    """An approved head, newest first: review checks up front, 120 green CI
    jobs, then the one failed CI job and a review check from the first
    dispatch on the second page."""
    return ([_run("qa-review / review", "success")]
            + [_run(f"job {i}") for i in range(120)]
            + [_run(FAILED_JOB, "failure"),
               {"name": REVIEW_CHECK, "status": "completed",
                "conclusion": "cancelled", "check_suite": {"id": 2}}])


class FakeGh:
    """`gh` as reconcile calls it: every page for a `--paginate --slurp`
    read, GitHub's first page alone for any other read of the endpoint."""

    def __init__(self, runs, fail_checks=False, prs=()):
        self.paged = pages(runs)
        self.fail_checks = fail_checks
        self.prs = list(prs)
        self.calls: list = []

    def __call__(self, *args):
        self.calls.append(args)
        if args[:2] == ("pr", "list"):
            return json.dumps(self.prs)
        if args[0] == "api" and any("/check-runs" in a for a in args):
            if self.fail_checks:
                return ""  # gh() is silent: a failed read is an empty answer
            if "--paginate" in args and "--slurp" in args:
                return json.dumps(self.paged)
            return json.dumps(self.paged[0])
        if args[0] == "api" and any("/git/commits/" in a for a in args):
            return json.dumps({"committer": {"date": "2026-01-01T00:00:00Z"}})
        return ""

    def check_run_reads(self) -> list:
        return [c for c in self.calls
                if c[0] == "api" and any("/check-runs" in a for a in c)]


def approved_pr() -> dict:
    return {"number": 120, "headRefName": "agent/DRE-6532-whole-read",
            "headRefOid": SHA, "mergeStateStatus": "BLOCKED",
            "comments": [{"author": {"login": "agent-bureau-qa-bot"},
                          "body": f"QA Critic\nVERDICT: APPROVE @{SHA}"}]}


def _approved_but_red(fake: FakeGh) -> list:
    dispatched: list = []
    with mock.patch.object(reconcile, "gh", side_effect=fake), \
            mock.patch.object(reconcile, "gh_dispatch",
                              side_effect=lambda *a: dispatched.append(a)), \
            mock.patch.object(reconcile, "_actions_runs_busy", return_value=False), \
            mock.patch.object(reconcile, "fix_dispatch_blocked", return_value=False), \
            mock.patch.object(reconcile, "fix_agent_absent_hold", return_value=False):
        reconcile.fix_approved_but_red()
    return dispatched


def _assert_whole_reads(fake: FakeGh) -> None:
    reads = fake.check_run_reads()
    assert reads, fake.calls
    for call in reads:
        assert "--paginate" in call and "--slurp" in call, call
        assert any(a.endswith(f"commits/{SHA}/check-runs?per_page=100")
                   for a in call), call


def test_the_fixture_puts_both_runs_on_the_second_page():
    first, second = pages(head_runs())
    names = [r["name"] for r in first["check_runs"]]
    assert FAILED_JOB not in names and REVIEW_CHECK not in names
    assert {FAILED_JOB, REVIEW_CHECK} <= {r["name"] for r in second["check_runs"]}


def test_approved_but_red_on_the_second_page_dispatches_the_fix():
    fake = FakeGh(head_runs(), prs=[approved_pr()])
    dispatched = _approved_but_red(fake)
    assert len(dispatched) == 1, fake.calls
    assert "pr_number=120" in dispatched[0]
    _assert_whole_reads(fake)


def test_approved_and_green_on_every_page_dispatches_nothing():
    """Non-vacuous twin: the same head with the failed job made green. The
    review check's `cancelled` is a review's, never counted."""
    runs = [r if r["name"] != FAILED_JOB else _run(FAILED_JOB) for r in head_runs()]
    fake = FakeGh(runs, prs=[approved_pr()])
    assert _approved_but_red(fake) == []
    _assert_whole_reads(fake)


def test_approved_but_red_skips_a_pr_whose_read_fails():
    fake = FakeGh(head_runs(), fail_checks=True, prs=[approved_pr()])
    assert _approved_but_red(fake) == []
    assert fake.check_run_reads(), fake.calls


def test_a_review_check_on_the_second_page_is_found():
    fake = FakeGh(head_runs())
    with mock.patch.object(reconcile, "gh", side_effect=fake):
        rows = reconcile._review_checks_at_head(SHA)
    assert rows is not None
    assert ("completed", "cancelled", REVIEW_CHECK) in rows
    assert ("completed", "success", "qa-review / review") in rows
    # Review-named only: no CI job rides along.
    assert all(name.endswith("review") for _status, _conclusion, name in rows)
    _assert_whole_reads(fake)


def test_an_unreadable_read_is_none_at_head():
    fake = FakeGh(head_runs(), fail_checks=True)
    with mock.patch.object(reconcile, "gh", side_effect=fake):
        assert reconcile._review_checks_at_head(SHA) is None


@pytest.mark.parametrize("answer", [
    "not json",
    json.dumps([{"total_count": 1, "check_runs": "x"}]),
    json.dumps([7]),
])
def test_an_unparseable_paged_read_keeps_todays_answers(answer):
    def gh(*args):
        if args[:2] == ("pr", "list"):
            return json.dumps([approved_pr()])
        if args[0] == "api" and any("/check-runs" in a for a in args):
            return answer
        return ""
    with mock.patch.object(reconcile, "gh", side_effect=gh):
        assert reconcile._review_checks_at_head(SHA) is None
    dispatched: list = []
    with mock.patch.object(reconcile, "gh", side_effect=gh), \
            mock.patch.object(reconcile, "gh_dispatch",
                              side_effect=lambda *a: dispatched.append(a)), \
            mock.patch.object(reconcile, "_actions_runs_busy", return_value=False), \
            mock.patch.object(reconcile, "fix_dispatch_blocked", return_value=False), \
            mock.patch.object(reconcile, "fix_agent_absent_hold", return_value=False):
        reconcile.fix_approved_but_red()
    assert dispatched == []


# --------------------------------------------------------------------------
# 4. No unpaged read is left in the three readers
# --------------------------------------------------------------------------
CHECK_RUNS_READ = "/check-runs"


def _function_source(name: str) -> str:
    text = (ROOT / "scripts" / "reconcile.py").read_text()
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(text, node)
    raise AssertionError(f"reconcile.{name} not found")


def _gh_calls(source: str) -> list:
    """Every call in `source` to a `gh*` helper, as its source text."""
    calls = []
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id.startswith("gh")):
            calls.append(ast.get_source_segment(source, node))
    return calls


def _helper_name() -> str:
    """The one reconcile helper that reads a commit's check runs whole."""
    text = (ROOT / "scripts" / "reconcile.py").read_text()
    found = [n.name for n in ast.walk(ast.parse(text))
             if isinstance(n, ast.FunctionDef)
             and any(CHECK_RUNS_READ in c and "--paginate" in c
                     for c in _gh_calls(ast.get_source_segment(text, n)))
             and n.name not in ("refresh_stale_merge_refs",)]
    assert len(found) == 1, found
    return found[0]


@pytest.mark.parametrize("name", ["fix_approved_but_red", "_review_checks_at_head"])
def test_the_sweep_readers_read_through_the_whole_read(name):
    source = _function_source(name)
    unpaged = [c for c in _gh_calls(source)
               if CHECK_RUNS_READ in c and "--paginate" not in c]
    assert unpaged == [], unpaged
    assert f"{_helper_name()}(" in source


def test_the_helper_reads_every_page_of_100():
    source = _function_source(_helper_name())
    reads = [c for c in _gh_calls(source) if CHECK_RUNS_READ in c]
    assert len(reads) == 1, reads
    for flag in ("--paginate", "--slurp", "per_page=100"):
        assert flag in reads[0], (flag, reads[0])


def test_the_gate_script_has_no_unpaged_check_runs_read():
    lines = [ln for ln in (ROOT / "scripts" / "evaluate_and_merge.sh").read_text().splitlines()
             if not ln.lstrip().startswith("#")
             and "commits/" in ln and "check-runs" in ln and "gh api" in ln]
    assert len(lines) == 1, lines
    for flag in ("--paginate", "--slurp", "per_page=100"):
        assert flag in lines[0], (flag, lines[0])
    assert "||" not in lines[0], "a failed read must stop the step, as it does today"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
