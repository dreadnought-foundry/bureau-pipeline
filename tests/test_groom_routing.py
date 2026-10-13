"""RED-first: the routing leg reads where each repo's jobs actually ran,
off GitHub's own job records, and the verify leg writes it onto every targets
row as `routing` (DRE-7237).

The failures this file holds shut:

  * DRE-4417 (a build SIGKILLed on the Mac mini's light tier) and DRE-3701 (a
    stale verdict file in a self-hosted runner's `/tmp`) were judged as live
    although those jobs run on RunsOn now — the verify agent could not see
    where jobs run, so each owner's leg reads it off the jobs GitHub ran;
  * a window of the newest runs across every workflow is filled by the sweep
    and Linear Sync, so the runs are kept PER WORKFLOW, the newest run of
    every workflow asked before any workflow's second run;
  * a cut is never silent: a page limit, a repo's facts past 40, the request
    budget, the clock and a refusing repo are each named in `why`;
  * a row with no routing, or `read: false`, has no facts — `fold` writes
    `read: false` with every owner named rather than an empty list that reads
    as "ran nowhere";
  * nothing here reads a repository variable: the facts are what the jobs
    ran on, never what a setting says they should.

Run: cd bureau-pipeline && python3 -m pytest tests/test_groom_routing.py -v
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import groom_routing as gr  # noqa: E402

OWNER = "dreadnought-foundry"
AB = "dreadnought-foundry/agent-bureau"
PORTICO = "dreadnought-foundry/portico"
ONE_REPO = {"agent-bureau": AB}
TWO_REPOS = {"agent-bureau": AB, "portico": PORTICO}
REPO_MAP = {"agent-bureau": AB, "portico": PORTICO, "atlas": "EveryBite/atlas"}
REVIEW = ".github/workflows/self-qa-review.yml"
SWEEP = ".github/workflows/sweep.yml"
RATE_LIMIT = "gh: API rate limit exceeded for installation ID 42 (HTTP 403)"
NOW = "2026-10-14T13:04:00Z"


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "owner-installation-token")


def at(minute: int) -> str:
    """A completion time `minute` minutes after 2026-10-13T00:00Z."""
    return f"2026-10-13T{minute // 60:02d}:{minute % 60:02d}:00Z"


def run_(run_id: int, path: str) -> dict:
    return {"id": run_id, "path": path, "name": path.rsplit("/", 1)[-1],
            "status": "completed", "conclusion": "success"}


def job(name, *, labels=("ubuntu-latest",), group="GitHub Actions",
        completed=at(0), runner="GitHub Actions 7", conclusion="success"):
    return {"name": name, "labels": list(labels), "runner_group_name": group,
            "runner_name": runner, "completed_at": completed,
            "status": "completed", "conclusion": conclusion}


class FakeGh:
    """A `gh api -i` stand-in that records every request.

    `pages[repo]` is the list of run pages the runs query answers, newest run
    first; a page that is not the last carries a `Link: rel="next"` header
    pointing at the next one, as GitHub's does. `jobs[run_id]` is the jobs one
    run answers. `fail(n, endpoint)` returns an exception to raise on the n-th
    request; `clock` is advanced by `tick` seconds per request; `jobs_next` is
    the run ids whose jobs answer says more pages exist."""

    def __init__(self, pages=None, jobs=None, *, fail=None, clock=None,
                 tick=0, jobs_next=()):
        self.pages = pages or {}
        self.jobs = jobs or {}
        self.fail = fail
        self.clock = clock
        self.tick = tick
        self.jobs_next = set(jobs_next)
        self.calls: list[list[str]] = []

    def __call__(self, args):
        self.calls.append(list(args))
        n = len(self.calls)
        if self.clock is not None:
            self.clock.t += self.tick
        endpoint = args[0]
        if self.fail:
            err = self.fail(n, endpoint)
            if err is not None:
                raise err
        url = urlsplit(endpoint)
        parts = url.path.split("/")
        repo = "/".join(parts[1:3])
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        lines = ["HTTP/2.0 200 OK", "Content-Type: application/json"]
        if parts[-1] == "jobs":
            run_id = int(parts[-2])
            if run_id in self.jobs_next:
                lines.append(f'link: <https://api.github.com/{url.path}?page=2>;'
                             f' rel="next"')
            body = {"total_count": len(self.jobs.get(run_id, [])),
                    "jobs": self.jobs.get(run_id, [])}
        else:
            pages = self.pages.get(repo, [[]])
            page = int(query.get("page", "1"))
            if page < len(pages):
                lines.append(
                    f'Link: <https://api.github.com/repositories/9/actions/runs'
                    f'?status=completed&per_page=100&page={page + 1}>; rel="next",'
                    f' <https://api.github.com/x?page={len(pages)}>; rel="last"')
            body = {"total_count": sum(len(p) for p in pages),
                    "workflow_runs": pages[page - 1]}
        return "\r\n".join(lines) + "\r\n", json.dumps(body)

    def endpoints(self):
        return [c[0] for c in self.calls]

    def job_requests(self):
        return [int(urlsplit(e).path.split("/")[-2])
                for e in self.endpoints() if urlsplit(e).path.endswith("/jobs")]

    def run_requests(self):
        return [e for e in self.endpoints() if urlsplit(e).path.endswith("/runs")]


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def leg(gh, *, repo_map=ONE_REPO, clock=None, owner=OWNER):
    kwargs = {"clock": clock} if clock is not None else {}
    return gr.owner(owner=owner, gh_run=gh, repo_map=repo_map, now=NOW,
                    **kwargs)


def fact_of(doc, fid):
    found = [f for f in doc["facts"] if f["id"] == fid]
    assert len(found) == 1, (fid, [f["id"] for f in doc["facts"]])
    return found[0]


# --------------------------------------------------------------------------
# the contract constants
# --------------------------------------------------------------------------
def test_contract_constants():
    assert gr.ROUTING_RUN_PAGES == 3
    assert gr.ROUTING_RUNS_PER_WORKFLOW == 2
    assert gr.ROUTING_FACTS == 40
    assert gr.MAX_REQUESTS == 200
    assert gr.MAX_SECONDS == 120
    assert gr.REQUEST_TIMEOUT == 20


# --------------------------------------------------------------------------
# AC1 — one fact per (workflow path, job name), with the exact id
# --------------------------------------------------------------------------
def test_one_fact_per_workflow_and_job_with_counts_labels_groups_and_times():
    gh = FakeGh(
        pages={AB: [[run_(2, REVIEW), run_(1, REVIEW)]]},
        jobs={2: [job("call / review", labels=["runs-on", "runner=4cpu"],
                      group="RunsOn", completed=at(40), runner="runs-on-a"),
                  job("call / lint", completed=at(35))],
              1: [job("call / review", labels=["self-hosted", "mac-mini"],
                      group="Default", completed=at(10), runner="mini-1")]})
    doc = leg(gh)

    assert doc["read"] is True and doc["why"] is None
    assert doc["owner"] == OWNER and doc["read_at"] == NOW
    review = fact_of(doc, f"{AB}:{REVIEW}:call / review")
    assert review == {
        "id": f"{AB}:{REVIEW}:call / review", "repo": AB,
        "workflow": REVIEW, "job": "call / review", "jobs": 2,
        "labels": ["mac-mini", "runner=4cpu", "runs-on", "self-hosted"],
        "runner_groups": ["Default", "RunsOn"],
        "first_completed_at": at(10), "last_completed_at": at(40)}
    lint = fact_of(doc, f"{AB}:{REVIEW}:call / lint")
    assert lint["jobs"] == 1 and lint["labels"] == ["ubuntu-latest"]
    assert lint["runner_groups"] == ["GitHub Actions"]
    assert lint["first_completed_at"] == lint["last_completed_at"] == at(35)
    assert len(doc["facts"]) == 2
    # The newest completed fact first.
    assert [f["job"] for f in doc["facts"]] == ["call / review", "call / lint"]


def test_runs_query_asks_completed_runs_a_hundred_a_page():
    gh = FakeGh(pages={AB: [[run_(1, REVIEW)]]}, jobs={1: [job("j")]})
    leg(gh)
    first = urlsplit(gh.run_requests()[0])
    assert first.path == f"repos/{AB}/actions/runs"
    assert parse_qs(first.query) == {"status": ["completed"],
                                     "per_page": ["100"]}
    jobs = urlsplit([e for e in gh.endpoints() if "/jobs" in e][0])
    assert jobs.path == f"repos/{AB}/actions/runs/1/jobs"
    assert parse_qs(jobs.query) == {"per_page": ["100"]}


def test_a_job_that_never_got_a_runner_is_not_a_place_it_ran():
    gh = FakeGh(pages={AB: [[run_(1, REVIEW)]]},
                jobs={1: [job("build", runner=None, group=None,
                              conclusion="skipped", labels=["mac-mini"]),
                          job("test", completed=at(5))]})
    doc = leg(gh)
    assert [f["job"] for f in doc["facts"]] == ["test"]


def test_the_id_is_owner_repo_workflow_path_and_job_name_exactly():
    gh = FakeGh(pages={AB: [[run_(1, ".github/workflows/a b.yml")]]},
                jobs={1: [job("call / build (arm64)")]})
    doc = leg(gh)
    assert [f["id"] for f in doc["facts"]] == [
        "dreadnought-foundry/agent-bureau:.github/workflows/a b.yml:"
        "call / build (arm64)"]


# --------------------------------------------------------------------------
# AC2 — per workflow, newest run of every workflow first
# --------------------------------------------------------------------------
def test_a_busy_workflow_does_not_crowd_out_a_quiet_one():
    # 90 sweep runs, newest first, then 10 review runs: a window of the
    # newest runs across workflows would never reach the review.
    page = ([run_(1000 - i, SWEEP) for i in range(90)]
            + [run_(500 - i, REVIEW) for i in range(10)])
    jobs = {r["id"]: [job("sweep" if r["path"] == SWEEP else "review",
                          completed=at(10))] for r in page}
    gh = FakeGh(pages={AB: [page]}, jobs=jobs)
    doc = leg(gh)

    assert gh.job_requests() == [1000, 500, 999, 499]
    assert fact_of(doc, f"{AB}:{REVIEW}:review")["jobs"] == 2
    assert fact_of(doc, f"{AB}:{SWEEP}:sweep")["jobs"] == 2


def test_the_budget_cuts_second_runs_before_any_newest_run(monkeypatch):
    monkeypatch.setattr(gr, "MAX_REQUESTS", 3)
    page = [run_(10, SWEEP), run_(9, SWEEP), run_(8, REVIEW), run_(7, REVIEW)]
    gh = FakeGh(pages={AB: [page]},
                jobs={10: [job("sweep")], 9: [job("sweep")],
                      8: [job("review")], 7: [job("review")]})
    doc = leg(gh)
    # One runs page, then the newest run of each workflow — no second run.
    assert gh.job_requests() == [10, 8]
    assert {f["workflow"] for f in doc["facts"]} == {SWEEP, REVIEW}
    assert doc["read"] is True
    assert "request budget of 3 spent" in doc["why"]


def test_newest_runs_of_every_repo_before_any_second_run(monkeypatch):
    gh = FakeGh(pages={AB: [[run_(2, SWEEP), run_(1, SWEEP)]],
                       PORTICO: [[run_(20, REVIEW), run_(19, REVIEW)]]},
                jobs={2: [job("a")], 1: [job("a")],
                      20: [job("b")], 19: [job("b")]})
    leg(gh, repo_map=TWO_REPOS)
    assert gh.job_requests() == [2, 20, 1, 19]


# --------------------------------------------------------------------------
# AC3 — at most three pages, the page limit named
# --------------------------------------------------------------------------
def page_of(start: int, n: int = 100) -> list[dict]:
    return [run_(start - i, SWEEP) for i in range(n)]


def test_follows_next_for_at_most_three_pages_and_names_the_limit():
    pages = [page_of(4000), page_of(3000), page_of(2000), page_of(1000)]
    gh = FakeGh(pages={AB: pages}, jobs={})
    doc = leg(gh)
    runs = gh.run_requests()
    assert len(runs) == gr.ROUTING_RUN_PAGES == 3
    # The second and third requests are the `next` links GitHub gave.
    assert [parse_qs(urlsplit(e).query).get("page") for e in runs] == [
        None, ["2"], ["3"]]
    assert urlsplit(runs[1]).path == "repositories/9/actions/runs"
    assert doc["why"] and "3 pages" in doc["why"] and AB in doc["why"]


def test_three_pages_and_no_fourth_is_not_a_cut():
    pages = [page_of(4000), page_of(3000), page_of(2000, 5)]
    gh = FakeGh(pages={AB: pages}, jobs={})
    doc = leg(gh)
    assert len(gh.run_requests()) == 3
    assert doc["why"] is None


def test_one_page_with_no_next_asks_one_page():
    gh = FakeGh(pages={AB: [[run_(1, SWEEP)]]}, jobs={1: [job("a")]})
    leg(gh)
    assert len(gh.run_requests()) == 1


# --------------------------------------------------------------------------
# AC4 — the 40-fact cut, refusals, rate limit, budget, clock
# --------------------------------------------------------------------------
def test_more_than_forty_facts_keeps_the_forty_most_recent_and_names_the_cut():
    jobs = [job(f"job {i:02d}", completed=at(i)) for i in range(45)]
    gh = FakeGh(pages={AB: [[run_(1, REVIEW)]]}, jobs={1: jobs})
    doc = leg(gh)
    assert len(doc["facts"]) == gr.ROUTING_FACTS == 40
    kept = {f["job"] for f in doc["facts"]}
    assert kept == {f"job {i:02d}" for i in range(5, 45)}
    assert doc["read"] is True
    assert AB in doc["why"] and "5 fact(s)" in doc["why"]


def test_the_forty_fact_cap_is_per_repo():
    a = [job(f"a{i}", completed=at(i)) for i in range(30)]
    b = [job(f"b{i}", completed=at(i)) for i in range(30)]
    gh = FakeGh(pages={AB: [[run_(1, REVIEW)]], PORTICO: [[run_(2, REVIEW)]]},
                jobs={1: a, 2: b})
    doc = leg(gh, repo_map=TWO_REPOS)
    assert len(doc["facts"]) == 60
    assert doc["why"] is None


def test_a_refusing_repo_is_named_and_the_next_repo_is_asked():
    def fail(n, endpoint):
        if endpoint.startswith(f"repos/{AB}/"):
            return gr.GhError("gh: Resource not accessible by integration (HTTP 403)")
        return None
    gh = FakeGh(pages={PORTICO: [[run_(2, REVIEW)]]}, jobs={2: [job("b")]},
                fail=fail)
    doc = leg(gh, repo_map=TWO_REPOS)
    assert any(e.startswith(f"repos/{PORTICO}/") for e in gh.endpoints())
    assert doc["read"] is True
    assert [f["repo"] for f in doc["facts"]] == [PORTICO]
    assert AB in doc["why"] and "not accessible" in doc["why"]
    assert doc["repos"][AB]["read"] is False
    assert doc["repos"][PORTICO] == {"read": True, "why": None}


def test_a_timed_out_request_is_a_refusal():
    def fail(n, endpoint):
        if endpoint.startswith(f"repos/{AB}/"):
            return subprocess.TimeoutExpired(cmd="gh", timeout=20)
        return None
    gh = FakeGh(pages={PORTICO: [[run_(2, REVIEW)]]}, jobs={2: [job("b")]},
                fail=fail)
    doc = leg(gh, repo_map=TWO_REPOS)
    assert "timed out after 20 s" in doc["why"]
    assert [f["repo"] for f in doc["facts"]] == [PORTICO]


def test_every_repo_refusing_is_not_read():
    gh = FakeGh(fail=lambda n, e: gr.GhError("gh: Not Found (HTTP 404)"))
    doc = leg(gh, repo_map=TWO_REPOS)
    assert doc["read"] is False
    assert AB in doc["why"] and PORTICO in doc["why"]
    assert doc["facts"] == []


def test_a_rate_limit_stops_the_leg_and_says_so():
    gh = FakeGh(pages={AB: [[run_(1, REVIEW)]], PORTICO: [[run_(2, REVIEW)]]},
                jobs={1: [job("a")], 2: [job("b")]},
                fail=lambda n, e: gr.GhError(RATE_LIMIT) if n == 2 else None)
    doc = leg(gh, repo_map=TWO_REPOS)
    assert len(gh.calls) == 2
    assert "rate limited" in doc["why"]


def test_the_clock_stops_the_leg_and_says_so():
    clock = Clock()
    page = [run_(i, f".github/workflows/w{i}.yml") for i in range(50, 0, -1)]
    gh = FakeGh(pages={AB: [page]}, jobs={i: [job("x")] for i in range(1, 51)},
                clock=clock, tick=10)
    doc = leg(gh, clock=clock)
    assert len(gh.calls) == gr.MAX_SECONDS // 10
    assert doc["read"] is True
    assert f"time budget of {gr.MAX_SECONDS} s spent" in doc["why"]
    assert doc["facts"]


def test_every_request_runs_under_the_request_timeout(monkeypatch):
    seen = {}

    def fake_run(argv, **kwargs):
        seen.update(kwargs, argv=argv)
        return subprocess.CompletedProcess(
            argv, 0, stdout='HTTP/2.0 200 OK\r\n\r\n{"workflow_runs": []}',
            stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)
    gr._gh([f"repos/{AB}/actions/runs?status=completed&per_page=100"])
    assert seen["timeout"] == gr.REQUEST_TIMEOUT
    assert seen["shell"] is False
    assert seen["argv"][:3] == ["gh", "api", "-i"]


def test_a_jobs_list_past_one_page_is_named():
    gh = FakeGh(pages={AB: [[run_(7, REVIEW)]]}, jobs={7: [job("a")]},
                jobs_next={7})
    doc = leg(gh)
    assert doc["why"] and "run 7" in doc["why"]


@pytest.mark.parametrize("stop", ["budget", "clock", "rate", "refused", "none"])
def test_the_document_is_written_on_every_path(tmp_path, monkeypatch, stop):
    repo_map = tmp_path / "repo-map.json"
    repo_map.write_text(json.dumps(ONE_REPO))
    out = tmp_path / f"routing-{OWNER}.json"
    clock = Clock()
    fail = None
    if stop == "budget":
        monkeypatch.setattr(gr, "MAX_REQUESTS", 0)
    elif stop == "rate":
        fail = lambda n, e: gr.GhError(RATE_LIMIT)  # noqa: E731
    elif stop == "refused":
        fail = lambda n, e: gr.GhError("gh: boom")  # noqa: E731
    gh = FakeGh(pages={AB: [[run_(1, REVIEW)]]}, jobs={1: [job("a")]},
                fail=fail, clock=clock,
                tick=gr.MAX_SECONDS if stop == "clock" else 0)
    code = gr.main(["owner", "--owner", OWNER, "--out", str(out),
                    "--repo-map", str(repo_map)], gh_run=gh, clock=clock)
    assert code == 0
    doc = json.loads(out.read_text())
    assert doc["owner"] == OWNER
    assert set(doc) >= {"read", "why", "read_at", "facts"}
    if stop != "none":
        assert doc["why"]


def test_an_unreadable_repo_map_still_writes_the_document(tmp_path):
    out = tmp_path / "routing.json"
    code = gr.main(["owner", "--owner", OWNER, "--out", str(out),
                    "--repo-map", str(tmp_path / "missing.json")],
                   gh_run=FakeGh())
    assert code == 0
    doc = json.loads(out.read_text())
    assert doc["read"] is False and doc["why"] and doc["facts"] == []


def test_an_owner_with_no_repo_in_the_map_is_not_read():
    gh = FakeGh()
    doc = leg(gh, owner="Nobody")
    assert gh.calls == []
    assert doc["read"] is False and "Nobody" in doc["why"]


# --------------------------------------------------------------------------
# AC5 — no token, no request
# --------------------------------------------------------------------------
def test_no_token_makes_no_request_and_is_not_read(monkeypatch):
    monkeypatch.delenv("GH_TOKEN", raising=False)
    gh = FakeGh(pages={AB: [[run_(1, REVIEW)]]}, jobs={1: [job("a")]})
    doc = leg(gh)
    assert gh.calls == []
    assert doc["read"] is False
    assert doc["why"] == gr.NO_TOKEN.format(owner=OWNER)
    assert doc["facts"] == []


def test_no_token_still_writes_the_document(tmp_path, monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "  ")
    repo_map = tmp_path / "repo-map.json"
    repo_map.write_text(json.dumps(ONE_REPO))
    out = tmp_path / "routing.json"
    gh = FakeGh()
    assert gr.main(["owner", "--owner", OWNER, "--out", str(out),
                    "--repo-map", str(repo_map)], gh_run=gh) == 0
    assert gh.calls == []
    doc = json.loads(out.read_text())
    assert doc["read"] is False and doc["why"] == gr.NO_TOKEN.format(owner=OWNER)


# --------------------------------------------------------------------------
# AC6 — fold
# --------------------------------------------------------------------------
def a_fact(repo, job_name, last=at(30)):
    return {"id": f"{repo}:{REVIEW}:{job_name}", "repo": repo,
            "workflow": REVIEW, "job": job_name, "jobs": 1,
            "labels": ["runs-on"], "runner_groups": ["RunsOn"],
            "first_completed_at": last, "last_completed_at": last}


def targets():
    return [{"card": "DRE-1", "title": "one", "evidence": ["x"],
             "lookups": {"looked_up": True, "ok": True}},
            {"card": "DRE-2", "title": "two", "evidence": [], "list": "spare"}]


def write_doc(directory: Path, owner: str, doc: dict) -> None:
    sub = directory / f"groom-routing-{owner}"
    sub.mkdir(parents=True, exist_ok=True)
    (sub / f"routing-{owner}.json").write_text(json.dumps(doc))


def fold_cli(tmp_path, rows, *, repo_map=REPO_MAP):
    t = tmp_path / "targets.json"
    t.write_text(json.dumps(rows))
    m = tmp_path / "repo-map.json"
    m.write_text(json.dumps(repo_map))
    out = tmp_path / "out.json"
    code = gr.main(["fold", "--targets", str(t), "--dir", str(tmp_path / "docs"),
                    "--out", str(out), "--repo-map", str(m)])
    assert code == 0
    return json.loads(out.read_text())


def test_fold_writes_the_identical_block_on_every_row(tmp_path):
    docs = tmp_path / "docs"
    write_doc(docs, OWNER, {"owner": OWNER, "read": True, "why": None,
                            "read_at": NOW, "facts": [a_fact(AB, "review")]})
    write_doc(docs, "EveryBite", {
        "owner": "EveryBite", "read": True, "why": None, "read_at": NOW,
        "facts": [a_fact("EveryBite/atlas", "build")]})
    rows = fold_cli(tmp_path, targets())

    first = rows[0]["routing"]
    assert all(r["routing"] == first for r in rows)
    assert first["read"] is True and first["why"] is None
    assert first["read_at"] == NOW
    assert sorted(f["id"] for f in first["facts"]) == sorted([
        f"EveryBite/atlas:{REVIEW}:build", f"{AB}:{REVIEW}:review"])


def test_fold_changes_no_other_field(tmp_path):
    before = targets()
    rows = fold_cli(tmp_path, targets())
    for old, new in zip(before, rows):
        new = dict(new)
        new.pop("routing")
        assert new == old


def test_fold_with_no_document_is_not_read_and_names_every_owner(tmp_path):
    rows = fold_cli(tmp_path, targets())
    for r in rows:
        routing = r["routing"]
        assert routing["read"] is False and routing["facts"] == []
        assert OWNER in routing["why"] and "EveryBite" in routing["why"]


def test_fold_with_only_unread_documents_is_not_read(tmp_path):
    docs = tmp_path / "docs"
    for owner in (OWNER, "EveryBite"):
        write_doc(docs, owner, {"owner": owner, "read": False,
                                "why": gr.NO_TOKEN.format(owner=owner),
                                "read_at": NOW, "facts": []})
    rows = fold_cli(tmp_path, targets())
    routing = rows[0]["routing"]
    assert routing["read"] is False and routing["facts"] == []
    assert gr.NO_TOKEN.format(owner=OWNER) in routing["why"]
    assert gr.NO_TOKEN.format(owner="EveryBite") in routing["why"]


def test_fold_is_read_when_one_owner_answered_and_names_the_others(tmp_path):
    docs = tmp_path / "docs"
    write_doc(docs, OWNER, {"owner": OWNER, "read": True,
                            "why": "request budget of 200 spent",
                            "read_at": NOW, "facts": [a_fact(AB, "review")]})
    rows = fold_cli(tmp_path, targets())
    routing = rows[1]["routing"]
    assert routing["read"] is True
    assert "EveryBite" in routing["why"]
    assert "request budget of 200 spent" in routing["why"]
    assert [f["id"] for f in routing["facts"]] == [f"{AB}:{REVIEW}:review"]


def test_fold_reads_documents_at_any_depth_and_skips_unreadable(tmp_path):
    docs = tmp_path / "docs" / "a" / "b"
    docs.mkdir(parents=True)
    (docs / f"routing-{OWNER}.json").write_text(json.dumps(
        {"owner": OWNER, "read": True, "why": None, "read_at": NOW,
         "facts": [a_fact(AB, "review")]}))
    (docs / "routing-broken.json").write_text("{not json")
    rows = fold_cli(tmp_path, targets(), repo_map=ONE_REPO)
    assert rows[0]["routing"]["read"] is True
    assert rows[0]["routing"]["why"] is None


def test_fold_function_over_rows():
    rows = targets()
    gr.fold(rows, [], repo_map=ONE_REPO)
    assert rows[0]["routing"] == rows[1]["routing"]
    assert rows[0]["routing"]["read"] is False


# --------------------------------------------------------------------------
# AC7 — no repository variable is read
# --------------------------------------------------------------------------
def test_the_module_reads_no_repository_variable():
    source = (ROOT / "scripts" / "groom_routing.py").read_text(encoding="utf-8")
    assert "actions/variables" not in source
    assert "variable get" not in source
