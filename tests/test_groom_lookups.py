"""RED-first: the lookup leg reads one owner's repos on that owner's token for
merged pull requests touching each card's files, under a request budget and a
clock, and writes a per-owner document (DRE-5308).

The failures this file holds shut:

  * on 2026-09-29 the merged-PR search failed for every card and every card
    was judged on nothing — so a repo that refuses is NAMED on the card, a
    leg with no token says so, and a leg that stops on a budget, the clock or
    a rate limit marks every card it did not finish `read: false` with why;
  * a commits list cut at `MAX_COMMITS` is not "nothing merged" — the cut is
    recorded where GitHub says more pages exist, and never paged;
  * `--budget` arrives as the empty string on every ordinary morning, so it is
    text read through `parse_budget`, never an int;
  * one hung `gh` call must not carry the leg into the job kill, so every
    request runs under `REQUEST_TIMEOUT`.

Run: cd bureau-pipeline && python3 -m pytest tests/test_groom_lookups.py -v
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import groom_lookups as gl  # noqa: E402

OWNER = "dreadnought-foundry"
CREATED = "2026-09-01T16:00:00Z"
ALERTS = "console/backend/alerts.py"
REPO_MAP = {
    "agent-bureau": "dreadnought-foundry/agent-bureau",
    "portico": "dreadnought-foundry/portico",
    "atlas": "EveryBite/atlas",
}
ONE_REPO = {"agent-bureau": "dreadnought-foundry/agent-bureau"}
RATE_LIMIT = "gh: API rate limit exceeded for installation ID 42 (HTTP 403)"


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "owner-installation-token")


def row(card, *, paths=(ALERTS,), which="planning", created=CREATED,
        repository="dreadnought-foundry/agent-bureau", looked_up=True,
        lookups="yes"):
    """A target row as the groom job uploads it: `targets` (today), the
    board context (DRE-5306) and the `lookups` block (DRE-5458)."""
    return {"card": card, "repository": repository,
            "repo_slug": repository.split("/")[-1], "title": card, "body": "",
            "evidence": [], "list": which,
            "context": {"created_at": created, "age_days": 30},
            "excluded": None,
            "lookups": (None if lookups is None
                        else {"looked_up": looked_up, "paths": list(paths)})}


def pr(number, *, merged=True):
    return {"number": number, "title": f"PR {number}",
            "html_url": f"https://github.com/x/y/pull/{number}",
            "merged_at": "2026-09-10T12:00:00Z" if merged else None}


class FakeGh:
    """A `gh api -i` stand-in that records every request.

    `commits[(repo, path)]` is the shas the commits query answers;
    `pulls[(repo, sha)]` the pull requests a commit belongs to; `nexts` the
    `(repo, path)` pairs whose commits response says more pages exist;
    `remaining` stamps `x-ratelimit-remaining`, falling by one per request as
    GitHub's does; `fail(n, endpoint)` returns an exception to raise on the
    n-th request; `clock` is advanced by `tick` seconds per request;
    `raw(endpoint)` returns a body string to answer in place of the list, or
    None for the ordinary answer."""

    def __init__(self, commits=None, pulls=None, *, nexts=(), remaining=None,
                 fail=None, clock=None, tick=0, every_path=None, raw=None):
        self.commits = commits or {}
        self.pulls = pulls or {}
        self.nexts = set(nexts)
        self.remaining = remaining
        self.fail = fail
        self.clock = clock
        self.tick = tick
        self.every_path = every_path
        self.raw = raw
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
        lines = ["HTTP/2.0 200 OK", "Content-Type: application/json"]
        if self.remaining is not None:
            lines.append(f"X-Ratelimit-Remaining: {self.remaining - n + 1}")
        if parts[-1] == "pulls":
            body = self.pulls.get((repo, parts[4]), [])
        else:
            path = parse_qs(url.query)["path"][0]
            if (repo, path) in self.nexts:
                lines.append(f'Link: <https://api.github.com/{url.path}?page=2>;'
                             f' rel="next", <https://api.github.com/x>; rel="last"')
            shas = (self.every_path(repo, path) if self.every_path
                    else self.commits.get((repo, path), []))
            body = [{"sha": s} for s in shas]
        answer = self.raw(endpoint) if self.raw else None
        return ("\r\n".join(lines) + "\r\n",
                answer if answer is not None else json.dumps(body))

    def commit_requests(self):
        return [c for c in self.calls if not c[0].endswith("/pulls")]

    def queries(self):
        out = []
        for call in self.commit_requests():
            url = urlsplit(call[0])
            out.append((url.path, {k: v[0] for k, v in parse_qs(url.query).items()}))
        return out


class Clock:
    def __init__(self):
        self.t = 0.0
        self.reads: list[float] = []

    def __call__(self):
        self.reads.append(self.t)
        return self.t


def busy(repo, path):
    """Five fresh shas for any path: every card needs far more than a budget."""
    return [f"{repo}-{path}-{i}" for i in range(gl.MAX_COMMITS)]


def run(rows, gh, *, repo_map=ONE_REPO, budget=None, clock=None, owner=OWNER):
    kwargs = {"clock": clock} if clock is not None else {}
    return gl.owner(rows, owner=owner, gh_run=gh, repo_map=repo_map,
                    now="2026-10-01T13:00:00Z", budget=budget, **kwargs)


# --------------------------------------------------------------------------
# the contract constants
# --------------------------------------------------------------------------
def test_contract_constants():
    assert gl.MAX_PATHS == 3
    assert gl.MAX_COMMITS == 5
    assert gl.MAX_REQUESTS == 600
    assert gl.BUDGET_SHARE == 0.25
    assert gl.MAX_SECONDS == 300
    assert gl.REQUEST_TIMEOUT == 20


# --------------------------------------------------------------------------
# AC1 — each repo asked since the card's creation, merged PRs kept
# --------------------------------------------------------------------------
def test_asks_each_repo_since_creation_and_keeps_merged_prs():
    gh = FakeGh(
        commits={("dreadnought-foundry/agent-bureau", ALERTS): ["aaa", "bbb"],
                 ("dreadnought-foundry/portico", ALERTS): []},
        pulls={("dreadnought-foundry/agent-bureau", "aaa"): [pr(41), pr(40, merged=False)],
               ("dreadnought-foundry/agent-bureau", "bbb"): [pr(41)]})
    doc = run([row("DRE-1")], gh, repo_map=REPO_MAP)

    queries = gh.queries()
    assert [q[0] for q in queries] == [
        "repos/dreadnought-foundry/agent-bureau/commits",
        "repos/dreadnought-foundry/portico/commits"]
    for _, params in queries:
        assert params["path"] == ALERTS
        assert params["since"] == CREATED
        assert params["per_page"] == str(gl.MAX_COMMITS)
        assert params["until"] == "2026-10-01T13:00:00Z"
    pulls = [c[0] for c in gh.calls if c[0].endswith("/pulls")]
    assert pulls == ["repos/dreadnought-foundry/agent-bureau/commits/aaa/pulls",
                     "repos/dreadnought-foundry/agent-bureau/commits/bbb/pulls"]
    assert not any("EveryBite" in c[0] for c in gh.calls)

    card = doc["cards"]["DRE-1"]
    assert card["read"] is True and card["why"] is None
    assert card["merged_prs"] == [{
        "repo": "dreadnought-foundry/agent-bureau", "number": 41,
        "title": "PR 41", "url": "https://github.com/x/y/pull/41",
        "merged_at": "2026-09-10T12:00:00Z", "path": ALERTS}]
    assert card["cut"] == [] and card["unread_repos"] == {}


# --------------------------------------------------------------------------
# AC2 — the cut at MAX_COMMITS is named, never paged; a sha followed once
# --------------------------------------------------------------------------
def test_next_link_records_the_cut_and_never_pages():
    repo = "dreadnought-foundry/agent-bureau"
    gh = FakeGh(commits={(repo, ALERTS): busy(repo, ALERTS)},
                nexts={(repo, ALERTS)})
    card = run([row("DRE-1")], gh)["cards"]["DRE-1"]
    assert card["read"] is True
    assert card["cut"] == [{"repo": repo, "path": ALERTS}]
    assert len(gh.commit_requests()) == 1
    assert not any("page" in params for _, params in gh.queries())
    assert len(gh.calls) == 1 + gl.MAX_COMMITS


def test_no_next_link_records_no_cut():
    repo = "dreadnought-foundry/agent-bureau"
    gh = FakeGh(commits={(repo, ALERTS): busy(repo, ALERTS)})
    card = run([row("DRE-1")], gh)["cards"]["DRE-1"]
    assert card["read"] is True and card["cut"] == []


def test_a_commit_under_two_paths_is_followed_once():
    repo = "dreadnought-foundry/agent-bureau"
    other = "console/backend/rules.py"
    gh = FakeGh(commits={(repo, ALERTS): ["shared"], (repo, other): ["shared"]},
                pulls={(repo, "shared"): [pr(7)]})
    card = run([row("DRE-1", paths=(ALERTS, other))], gh)["cards"]["DRE-1"]
    pulls = [c[0] for c in gh.calls if c[0].endswith("/pulls")]
    assert pulls == [f"repos/{repo}/commits/shared/pulls"]
    assert [p["number"] for p in card["merged_prs"]] == [7]
    assert card["merged_prs"][0]["path"] == ALERTS


# --------------------------------------------------------------------------
# AC3 — the request budget, the rate limit, the header's share
# --------------------------------------------------------------------------
def test_budget_five_stops_at_five_and_keeps_the_finished_card():
    repo = "dreadnought-foundry/agent-bureau"
    gh = FakeGh(commits={(repo, "a.py"): ["s1", "s2"],
                         (repo, "b.py"): ["s3", "s4", "s5"],
                         (repo, "c.py"): ["s6", "s7", "s8"]},
                pulls={(repo, "s1"): [pr(1)]})
    rows = [row("DRE-1", paths=("a.py",)), row("DRE-2", paths=("b.py",)),
            row("DRE-3", paths=("c.py",))]
    doc = run(rows, gh, budget=5)
    assert len(gh.calls) == 5
    assert doc["requests"] == 5 and doc["budget"] == 5
    first = doc["cards"]["DRE-1"]
    assert first["read"] is True and [p["number"] for p in first["merged_prs"]] == [1]
    for card in ("DRE-2", "DRE-3"):
        assert doc["cards"][card]["read"] is False
        assert doc["cards"][card]["why"] == "request budget of 5 spent"


def test_budget_zero_makes_no_request():
    gh = FakeGh(every_path=busy)
    doc = run([row("DRE-1"), row("DRE-2", which="spare")], gh, budget=0)
    assert gh.calls == []
    assert doc["requests"] == 0 and doc["budget"] == 0
    for card in doc["cards"].values():
        assert card["read"] is False
        assert card["why"] == "request budget of 0 spent"


def test_a_rate_limit_stops_the_leg():
    def fail(n, endpoint):
        return RuntimeError(RATE_LIMIT) if n == 3 else None

    gh = FakeGh(every_path=lambda repo, path: [], fail=fail)
    rows = [row("DRE-1"), row("DRE-2"), row("DRE-3"), row("DRE-4")]
    doc = run(rows, gh)
    assert len(gh.calls) == 3
    assert doc["cards"]["DRE-1"]["read"] is True
    assert doc["cards"]["DRE-2"]["read"] is True
    for card in ("DRE-3", "DRE-4"):
        assert doc["cards"][card]["read"] is False
        assert doc["cards"][card]["why"].startswith("rate limited: ")
        assert "API rate limit exceeded" in doc["cards"][card]["why"]
    assert doc["cards"]["DRE-3"]["unread_repos"] == {}


def test_the_first_responses_remaining_caps_the_leg_at_a_quarter():
    gh = FakeGh(every_path=busy, remaining=400)
    rows = [row(f"DRE-{i}", paths=("a.py", "b.py", "c.py")) for i in range(10)]
    doc = run(rows, gh)
    assert len(gh.calls) == 100
    assert doc["budget"] == 100 and doc["requests"] == 100
    unfinished = [c for c in doc["cards"].values() if not c["read"]]
    assert unfinished
    assert all(c["why"] == "request budget of 100 spent" for c in unfinished)


# --------------------------------------------------------------------------
# AC4 — --budget is text; the empty string is the ordinary morning
# --------------------------------------------------------------------------
def _files(tmp_path, rows, repo_map=ONE_REPO):
    targets = tmp_path / "targets.json"
    targets.write_text(json.dumps(rows), encoding="utf-8")
    repo_map_file = tmp_path / "repo-map.json"
    repo_map_file.write_text(json.dumps(repo_map), encoding="utf-8")
    return targets, repo_map_file


def _main(tmp_path, gh, rows, *extra, name="lookups.json"):
    targets, repo_map_file = _files(tmp_path, rows)
    out = tmp_path / name
    code = gl.main(["owner", "--owner", OWNER, "--targets", str(targets),
                    "--out", str(out), "--repo-map", str(repo_map_file), *extra],
                   gh_run=gh)
    return code, out


def test_main_empty_budget_is_the_ordinary_morning(tmp_path, capsys):
    rows = [row(f"DRE-{i}", paths=("a.py", "b.py", "c.py")) for i in range(10)]
    lines, docs = [], []
    for extra, name in ((["--budget", ""], "empty.json"), ([], "absent.json")):
        gh = FakeGh(every_path=busy, remaining=400)
        code, out = _main(tmp_path, gh, rows, *extra, name=name)
        assert code == 0
        assert len(gh.calls) == 100
        docs.append(json.loads(out.read_text(encoding="utf-8")))
        lines.append(capsys.readouterr().out.strip().splitlines()[-1])
    assert lines[0] == lines[1]
    # 18 requests a card: five cards finish inside 100, the sixth does not.
    assert lines[0].startswith(f"groom-lookups: {OWNER} — 5 card(s) read, "
                               "100 request(s) of a budget of 100, ")
    assert lines[0].endswith(f" s of {gl.MAX_SECONDS} s")
    for doc in docs:
        assert doc["budget"] == 100 and doc["requests"] == 100
    assert docs[0]["cards"] == docs[1]["cards"]


def test_parse_budget():
    assert gl.parse_budget(None) is None
    assert gl.parse_budget("") is None
    assert gl.parse_budget("  ") is None
    assert gl.parse_budget("0") == 0
    assert gl.parse_budget("7") == 7
    for bad in ("abc", "-1", "1.5"):
        with pytest.raises(ValueError):
            gl.parse_budget(bad)


def test_main_refuses_a_budget_it_cannot_read(tmp_path, capsys):
    gh = FakeGh(every_path=busy)
    code, out = _main(tmp_path, gh, [row("DRE-1")], "--budget", "abc")
    assert code == 2
    err = capsys.readouterr().err
    assert ('groom-lookups: --budget must be empty or a whole number, '
            'got "abc"') in err
    assert gh.calls == []
    assert not out.exists()


# --------------------------------------------------------------------------
# AC5 — one hung request is one repo refusing, and _gh carries the timeout
# --------------------------------------------------------------------------
def test_a_timed_out_repo_is_named_and_the_next_repo_asked():
    clock = Clock()

    def fail(n, endpoint):
        if n == 1:
            clock.t += gl.REQUEST_TIMEOUT
            return subprocess.TimeoutExpired(["gh", "api"], gl.REQUEST_TIMEOUT)
        return None

    gh = FakeGh(commits={("dreadnought-foundry/portico", ALERTS): ["p1"]},
                pulls={("dreadnought-foundry/portico", "p1"): [pr(9)]},
                fail=fail)
    doc = run([row("DRE-1")], gh, repo_map=REPO_MAP, clock=clock)
    card = doc["cards"]["DRE-1"]
    assert card["unread_repos"] == {
        "dreadnought-foundry/agent-bureau":
            f"timed out after {gl.REQUEST_TIMEOUT} s"}
    assert card["read"] is True
    assert [p["repo"] for p in card["merged_prs"]] == ["dreadnought-foundry/portico"]
    assert gh.commit_requests()[1][0].startswith("repos/dreadnought-foundry/portico/")
    assert doc["seconds"] == gl.REQUEST_TIMEOUT
    # The clock is read before the request after the timeout, as before any.
    assert len(clock.reads) >= 1 + len(gh.calls)


def test_the_real_gh_runner_passes_the_timeout(monkeypatch):
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen.update(kwargs)
        return subprocess.CompletedProcess(
            argv, 0, stdout='HTTP/2.0 200 OK\r\nX-Ratelimit-Remaining: 9\r\n\r\n[]',
            stderr="")

    monkeypatch.setattr(gl.subprocess, "run", fake_run)
    headers, body = gl._gh(["repos/o/r/commits?path=a"])
    assert seen["timeout"] == gl.REQUEST_TIMEOUT
    assert seen.get("shell", False) is False
    assert seen["argv"] == ["gh", "api", "-i", "repos/o/r/commits?path=a"]
    assert "X-Ratelimit-Remaining: 9" in headers
    assert body == "[]"


# --------------------------------------------------------------------------
# AC6 — the clock
# --------------------------------------------------------------------------
def test_the_clock_stops_the_leg_and_the_document_is_still_written(tmp_path, capsys):
    clock = Clock()
    step = gl.MAX_SECONDS / 3
    # DRE-1 needs one request (no commits); DRE-2 and DRE-3 need far more.
    gh = FakeGh(every_path=lambda repo, path: [] if path == "a.py" else busy(repo, path),
                clock=clock, tick=step)
    rows = [row("DRE-1", paths=("a.py",)), row("DRE-2"), row("DRE-3")]
    targets, repo_map_file = _files(tmp_path, rows)
    out = tmp_path / "lookups.json"
    code = gl.main(["owner", "--owner", OWNER, "--targets", str(targets),
                    "--out", str(out), "--repo-map", str(repo_map_file)],
                   gh_run=gh, clock=clock)
    assert code == 0
    assert len(gh.calls) == 3
    # Once at the start, then before each request: 0, 100, 200 before the
    # three, and the full budget before the fourth, which is never made.
    assert clock.reads[:5] == [0, 0, step, 2 * step, 3 * step]
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["cards"]["DRE-1"]["read"] is True
    for card in ("DRE-2", "DRE-3"):
        assert doc["cards"][card]["read"] is False
        assert doc["cards"][card]["why"] == f"time budget of {gl.MAX_SECONDS} s spent"
    assert doc["seconds"] == gl.MAX_SECONDS
    line = capsys.readouterr().out.strip().splitlines()[-1]
    assert line.startswith(f"groom-lookups: {OWNER} — 1 card(s) read, 3 request(s) ")
    assert line.endswith(f"{doc['seconds']:.1f} s of {gl.MAX_SECONDS} s")


# --------------------------------------------------------------------------
# AC7 — a refusing repo, and no token
# --------------------------------------------------------------------------
def test_a_refusing_repo_is_named_and_the_next_repo_asked():
    def fail(n, endpoint):
        if "agent-bureau" in endpoint:
            return RuntimeError("gh: Not Found (HTTP 404)")
        return None

    gh = FakeGh(commits={("dreadnought-foundry/portico", ALERTS): []}, fail=fail)
    card = run([row("DRE-1")], gh, repo_map=REPO_MAP)["cards"]["DRE-1"]
    assert card["unread_repos"] == {
        "dreadnought-foundry/agent-bureau": "gh: Not Found (HTTP 404)"}
    assert any("portico" in c[0] for c in gh.calls)
    assert card["read"] is True


def test_no_token_is_not_read_and_still_writes(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "")
    gh = FakeGh(every_path=busy)
    code, out = _main(tmp_path, gh, [row("DRE-1")])
    assert code == 0
    assert gh.calls == []
    why = f"no token for {OWNER}: the App's installation could not be minted"
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["read"] is False and doc["why"] == why
    assert doc["requests"] == 0
    assert doc["cards"]["DRE-1"]["read"] is False
    assert capsys.readouterr().out.strip().splitlines()[-1] == (
        f"groom-lookups: {OWNER} — not read: {why}")


# --------------------------------------------------------------------------
# AC8 — order, the rows read, the document's shape
# --------------------------------------------------------------------------
def test_planning_before_spares_and_only_looked_up_rows():
    gh = FakeGh(every_path=lambda repo, path: [])
    rows = [row("SPARE-1", paths=("s1.py",), which="spare"),
            row("PLAN-1", paths=("p1.py",)),
            row("NULL-1", paths=("n1.py",), lookups=None),
            row("OFF-1", paths=("o1.py",), looked_up=False),
            row("SPARE-2", paths=("s2.py",), which="spare"),
            row("PLAN-2", paths=("p2.py",))]
    doc = run(rows, gh)
    asked = [q[1]["path"] for q in gh.queries()]
    assert asked == ["p1.py", "p2.py", "s1.py", "s2.py"]
    assert set(doc["cards"]) == {"PLAN-1", "PLAN-2", "SPARE-1", "SPARE-2"}


def test_the_document_is_the_contract_shape():
    repo = "dreadnought-foundry/agent-bureau"

    def fail(n, endpoint):
        if "portico" in endpoint and "r.py" in endpoint:
            return RuntimeError("gh: Server Error (HTTP 502)")
        return None

    gh = FakeGh(commits={(repo, ALERTS): ["aaa"]},
                pulls={(repo, "aaa"): [pr(41)]}, fail=fail)
    rows = [row("READ-1"), row("REFUSED-1", paths=("r.py",)),
            row("LATE-1", paths=("late.py",), which="spare")]
    # READ-1: 3 requests (two repos, one follow); REFUSED-1: 2 (portico
    # refuses); LATE-1 meets the budget before its first.
    doc = run(rows, gh, repo_map=REPO_MAP, budget=5)

    assert set(doc) == {"owner", "read", "why", "requests", "budget",
                        "seconds", "cards"}
    assert doc["owner"] == OWNER
    assert doc["read"] is True
    assert doc["why"] == "request budget of 5 spent"
    assert doc["requests"] == 5 and doc["budget"] == 5
    assert isinstance(doc["seconds"], float)
    for card in doc["cards"].values():
        assert set(card) == {"read", "why", "merged_prs", "cut", "unread_repos"}
    read = doc["cards"]["READ-1"]
    assert read["read"] is True and read["why"] is None
    assert read["merged_prs"] == [{"repo": repo, "number": 41, "title": "PR 41",
                                   "url": "https://github.com/x/y/pull/41",
                                   "merged_at": "2026-09-10T12:00:00Z",
                                   "path": ALERTS}]
    assert read["cut"] == [] and read["unread_repos"] == {}
    refused = doc["cards"]["REFUSED-1"]
    assert refused["read"] is True and refused["why"] is None
    assert refused["unread_repos"] == {
        "dreadnought-foundry/portico": "gh: Server Error (HTTP 502)"}
    late = doc["cards"]["LATE-1"]
    assert late == {"read": False, "why": "request budget of 5 spent",
                    "merged_prs": [], "cut": [], "unread_repos": {}}
    json.dumps(doc)


# --------------------------------------------------------------------------
# a 200 that is not a list is a repo refusing, never "nothing merged"
# --------------------------------------------------------------------------
@pytest.mark.parametrize("answer, on, said", [
    ('{"message": "Not Found"}', "commits", "commits for {path} answered Not Found, not a list"),
    ("not json", "commits", "commits for {path} answered unreadable JSON: "),
    ('{"message": "Moved"}', "pulls", "pulls for aaa answered Moved, not a list"),
])
def test_an_answer_that_is_not_a_list_names_the_repo(answer, on, said):
    repo = "dreadnought-foundry/agent-bureau"

    def raw(endpoint):
        if "agent-bureau" not in endpoint:
            return None
        return answer if endpoint.endswith("/pulls") == (on == "pulls") else None

    gh = FakeGh(commits={(repo, ALERTS): ["aaa"],
                         ("dreadnought-foundry/portico", ALERTS): ["p1"]},
                pulls={(repo, "aaa"): [pr(41)],
                       ("dreadnought-foundry/portico", "p1"): [pr(9)]},
                raw=raw)
    card = run([row("DRE-1")], gh, repo_map=REPO_MAP)["cards"]["DRE-1"]
    assert set(card["unread_repos"]) == {repo}
    assert card["unread_repos"][repo].startswith(
        f"{repo} " + said.format(path=ALERTS))
    assert card["read"] is True
    assert [p["repo"] for p in card["merged_prs"]] == ["dreadnought-foundry/portico"]


# --------------------------------------------------------------------------
# a row with no creation date is not asked; an owner with no repo is not read
# --------------------------------------------------------------------------
def test_a_row_with_no_creation_date_is_not_asked_and_the_next_is():
    gh = FakeGh(every_path=lambda repo, path: [])
    bare = row("BARE-1", paths=("bare.py",))
    del bare["context"]
    rows = [row("NULL-1", paths=("null.py",), created=None), bare,
            row("DRE-1", paths=("a.py",))]
    doc = run(rows, gh)
    assert [q[1]["path"] for q in gh.queries()] == ["a.py"]
    assert len(gh.calls) == 1
    for card in ("NULL-1", "BARE-1"):
        assert doc["cards"][card]["read"] is False
        assert doc["cards"][card]["why"] == gl.NO_CREATED_AT
    assert doc["cards"]["DRE-1"]["read"] is True
    assert doc["read"] is True and doc["why"] is None


def test_an_owner_with_no_repo_in_the_map_is_not_read():
    gh = FakeGh(every_path=busy)
    doc = run([row("DRE-1"), row("DRE-2", which="spare")], gh,
              repo_map=REPO_MAP, owner="nobody-owns-this")
    assert gh.calls == []
    why = "no repo of nobody-owns-this in the repo map"
    assert doc["read"] is False and doc["why"] == why
    assert doc["requests"] == 0
    for card in doc["cards"].values():
        assert card["read"] is False and card["why"] == why


# --------------------------------------------------------------------------
# main: `until` is the moment the leg ran; unreadable inputs still write
# --------------------------------------------------------------------------
def test_main_bounds_every_commits_query_by_now(tmp_path, monkeypatch):
    monkeypatch.setattr(gl, "_now", lambda: "2026-10-01T13:14:15Z")
    gh = FakeGh(every_path=lambda repo, path: [])
    code, _ = _main(tmp_path, gh, [row("DRE-1", paths=("a.py", "b.py"))])
    assert code == 0
    assert [params["until"] for _, params in gh.queries()] == [
        "2026-10-01T13:14:15Z"] * 2


def test_now_is_utc_to_the_second():
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", gl._now())


@pytest.mark.parametrize("broken", ["missing", "invalid", "targets-type",
                                    "map-type"])
def test_main_unreadable_inputs_still_write_a_not_read_document(
        tmp_path, capsys, broken):
    targets, repo_map_file = _files(tmp_path, [row("DRE-1")])
    if broken == "missing":
        targets.unlink()
    elif broken == "invalid":
        repo_map_file.write_text("{not json", encoding="utf-8")
    elif broken == "targets-type":
        targets.write_text(json.dumps({"rows": []}), encoding="utf-8")
    else:
        repo_map_file.write_text(json.dumps([]), encoding="utf-8")
    out = tmp_path / "lookups.json"
    gh = FakeGh(every_path=busy)
    code = gl.main(["owner", "--owner", OWNER, "--targets", str(targets),
                    "--out", str(out), "--repo-map", str(repo_map_file)],
                   gh_run=gh)
    assert code == 0
    assert gh.calls == []
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["read"] is False and doc["cards"] == {}
    assert doc["why"].startswith("the leg's inputs could not be read: ")
    assert capsys.readouterr().out.strip().splitlines()[-1] == (
        f"groom-lookups: {OWNER} — not read: {doc['why']}")
