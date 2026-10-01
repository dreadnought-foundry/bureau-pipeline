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


# ==========================================================================
# DRE-5458 — the groom job's half (`cards`) and the verify leg's (`fold`)
#
# On 2026-09-29 every card was judged on nothing. The rule held here is per
# card: a mapped card's lookup is `ok` only when the repo its own files live
# in answered, whatever the other owners said; an unmapped card's when any
# repo did; a card naming no file is never asked anything.
# ==========================================================================
FOLD_MAP = {
    "bureau-pipeline": "dreadnought-foundry/bureau-pipeline",
    "agent-bureau": "dreadnought-foundry/agent-bureau",
    "atlas": "EveryBite/atlas",
    "deltasolv": "DeltaSolv/deltasolv",
}
HOME = "dreadnought-foundry/bureau-pipeline"
DF, EB, DS = "dreadnought-foundry", "EveryBite", "DeltaSolv"
NO_TOKEN_DF = ("no token for dreadnought-foundry: the App's installation "
               "could not be minted")
NO_TOKEN_EB = "no token for EveryBite: the App's installation could not be minted"


def test_the_lookup_state_constants():
    assert gl.LOOKUP_STATES == ("ok", "failed", "none", "not-run")
    assert gl.LOOKUP_FAILED == "lookup failed: "


# --------------------------------------------------------------------------
# paths_of and cards
# --------------------------------------------------------------------------
class FakeLops:
    """`linear_ops` as `cards` uses it: `gql` counted, each aliased
    `searchIssues` field answered from `found[path]`; `fail` raises on every
    request."""

    def __init__(self, found=None, *, fail=None):
        self.found = found or {}
        self.fail = fail
        self.calls: list[tuple[str, dict]] = []

    def gql(self, query, variables=None):
        self.calls.append((query, dict(variables or {})))
        if self.fail:
            raise RuntimeError(self.fail)
        return {alias: {"nodes": list(self.found.get(path, []))}
                for alias, path in (variables or {}).items()}


def issue(ident, *, created="2026-09-20T10:00:00.000Z", state="Backlog",
          title=None):
    return {"identifier": ident, "title": title or f"{ident} title",
            "createdAt": created, "state": {"name": state}}


def target(card, body, *, repository=HOME, excluded=None, created=CREATED):
    return {"card": card, "repository": repository,
            "repo_slug": (repository or "widgets").split("/")[-1],
            "title": card, "body": body, "evidence": [], "list": "planning",
            "context": {"created_at": created, "age_days": 30},
            "excluded": excluded}


NOW_ISO = "2026-10-01T13:00:00Z"


def test_paths_of_reads_the_backticked_paths_as_written_up_to_max_paths():
    body = ("Fix `scripts/a.py:12` and `docs/b.md`, then `c.ts`, "
            "`d/e.yml` and `f.json`; `scripts/a.py` again. plain.py is prose.")
    assert gl.paths_of({"body": body}) == (
        ["scripts/a.py", "docs/b.md", "c.ts"], 2)
    assert gl.paths_of({"body": "No file here."}) == ([], 0)
    assert gl.paths_of({"body": None}) == ([], 0)


def test_cards_makes_one_request_per_file_naming_row_and_none_for_the_rest():
    rows = [target("DRE-1", "Edit `scripts/a.py`."),
            target("DRE-2", "No file at all."),
            target("DRE-3", "Edit `x.py`, `y.py`, `z.py` and `w.py`."),
            target("DRE-4", "Nothing named either."),
            target("DRE-5", "Edit `docs/groomer.md`.")]
    lops = FakeLops()
    gl.cards(rows, lops=lops, now=NOW_ISO)
    assert len(lops.calls) == 3
    for (query, variables), want in zip(
            lops.calls, (["scripts/a.py"], ["x.py", "y.py", "z.py"],
                         ["docs/groomer.md"])):
        assert query.count("searchIssues(") == len(want) <= gl.MAX_PATHS
        assert [variables[f"p{i}"] for i in range(len(want))] == want
        for i in range(len(want)):
            assert f"p{i}: searchIssues(term: $p{i}, first: 25)" in query
    assert [r["lookups"]["looked_up"] for r in rows] == [
        True, False, True, False, True]


def test_a_looked_up_row_carries_the_contract_shape_before_fold():
    rows = [target("DRE-1", "Edit `scripts/a.py`.")]
    gl.cards(rows, lops=FakeLops(), now=NOW_ISO)
    assert rows[0]["lookups"] == {
        "looked_up": True, "ok": None, "why": None,
        "paths": ["scripts/a.py"], "paths_left_out": 0, "newer_cards": [],
        "newer_cards_why": None, "merged_prs": [], "cut": [], "owners": {}}


def test_cards_keeps_newer_cards_only_and_writes_one_evidence_line_each():
    found = {"scripts/a.py": [
        issue("DRE-9", state="Done", title="Moved the roster onto the portal"),
        issue("DRE-8", created="2026-08-01T00:00:00.000Z"),   # older
        issue("DRE-1")],                                      # itself
        "docs/b.md": [issue("DRE-7", state="In Progress", title="Doc it")]}
    rows = [target("DRE-1", "Edit `scripts/a.py` and `docs/b.md`.")]
    gl.cards(rows, lops=FakeLops(found), now=NOW_ISO)
    look = rows[0]["lookups"]
    assert look["newer_cards"] == [
        {"identifier": "DRE-9", "title": "Moved the roster onto the portal",
         "state": "Done", "path": "scripts/a.py"},
        {"identifier": "DRE-7", "title": "Doc it", "state": "In Progress",
         "path": "docs/b.md"}]
    assert rows[0]["evidence"] == [
        "newer card DRE-9 (Done) names scripts/a.py: "
        "Moved the roster onto the portal",
        "newer card DRE-7 (In Progress) names docs/b.md: Doc it"]


def test_a_row_naming_no_file_is_not_asked_and_carries_its_own_state():
    rows = [target("DRE-2", "No file."), target("DRE-4", "Nor here.")]
    lops = FakeLops()
    gl.cards(rows, lops=lops, now=NOW_ISO)
    assert lops.calls == []
    for r in rows:
        assert r["lookups"] == {
            "looked_up": False, "ok": True, "why": None, "paths": [],
            "paths_left_out": 0, "newer_cards": [], "newer_cards_why": None,
            "merged_prs": [], "cut": [], "owners": {}}
        assert r["evidence"] == [
            "the card names no file, so nothing was looked up"]


def test_an_unmapped_row_naming_a_file_is_looked_up_like_a_mapped_one():
    rows = [target("DRE-6", "Edit `lib/w.rb`.", repository=None)]
    lops = FakeLops()
    gl.cards(rows, lops=lops, now=NOW_ISO)
    assert len(lops.calls) == 1
    assert rows[0]["lookups"]["looked_up"] is True
    assert rows[0]["lookups"]["paths"] == ["lib/w.rb"]
    assert rows[0]["lookups"]["ok"] is None


def test_an_excluded_row_is_not_looked_up_and_carries_null():
    rows = [target("DRE-3", "Edit `scripts/a.py`.", excluded="hand-built")]
    lops = FakeLops()
    gl.cards(rows, lops=lops, now=NOW_ISO)
    assert lops.calls == []
    assert rows[0]["lookups"] is None
    assert rows[0]["evidence"] == []


def test_a_row_past_max_paths_is_looked_up_for_the_first_ones_and_says_so():
    rows = [target("DRE-3", "Edit `a.py`, `b.py`, `c.py`, `d.py`, `e.py`.")]
    lops = FakeLops()
    gl.cards(rows, lops=lops, now=NOW_ISO)
    [(query, variables)] = lops.calls
    assert sorted(variables.values()) == ["a.py", "b.py", "c.py"]
    assert query.count("searchIssues(") == gl.MAX_PATHS
    assert rows[0]["lookups"]["paths"] == ["a.py", "b.py", "c.py"]
    assert rows[0]["lookups"]["paths_left_out"] == 2
    assert "2 more path(s) were not looked up" in rows[0]["evidence"]


def test_a_refused_request_is_named_and_the_next_card_is_asked():
    rows = [target("DRE-1", "Edit `a.py`."), target("DRE-2", "Edit `b.py`.")]
    lops = FakeLops(fail="Linear said RATELIMITED")
    gl.cards(rows, lops=lops, now=NOW_ISO)
    assert len(lops.calls) == 2
    for r in rows:
        assert r["lookups"]["newer_cards"] == []
        assert r["lookups"]["newer_cards_why"] == "Linear said RATELIMITED"
        assert r["lookups"]["looked_up"] is True and r["lookups"]["ok"] is None


# --------------------------------------------------------------------------
# fold — over the layout the workflow produces
# --------------------------------------------------------------------------
def entry(*, read=True, why=None, merged=(), cut=(), unread=None):
    return {"read": read, "why": why, "merged_prs": list(merged),
            "cut": list(cut), "unread_repos": dict(unread or {})}


def odoc(owner, cards, *, read=True, why=None):
    return {"owner": owner, "read": read, "why": why, "requests": 3,
            "budget": 600, "seconds": 1.0, "cards": cards}


def merged(number, *, repo=HOME, path="scripts/a.py"):
    return {"repo": repo, "number": number, "title": f"PR {number}",
            "url": f"https://github.com/{repo}/pull/{number}",
            "merged_at": "2026-09-12T08:30:00Z", "path": path}


def looked(card, *, repository=HOME, paths=("scripts/a.py",), left_out=0):
    r = target(card, "", repository=repository)
    r["lookups"] = {"looked_up": True, "ok": None, "why": None,
                    "paths": list(paths), "paths_left_out": left_out,
                    "newer_cards": [], "newer_cards_why": None,
                    "merged_prs": [], "cut": [], "owners": {}}
    return r


def lay(tmp_path, docs, *, under=None) -> Path:
    """One directory per artifact, nothing at the top level — what
    `download-artifact` with `merge-multiple: false` leaves. `under` names
    the directory a document sits in when it is not its own owner's."""
    base = tmp_path / "lookups"
    base.mkdir(exist_ok=True)
    for doc in docs:
        name = (under or {}).get(doc["owner"], doc["owner"])
        d = base / f"groom-lookups-{name}"
        d.mkdir()
        (d / f"lookups-{name}.json").write_text(json.dumps(doc),
                                                encoding="utf-8")
    return base


def fold_cmd(tmp_path, rows, lookups_dir, repo_map=FOLD_MAP):
    targets = tmp_path / "targets.json"
    targets.write_text(json.dumps(rows), encoding="utf-8")
    map_file = tmp_path / "fold-map.json"
    map_file.write_text(json.dumps(repo_map), encoding="utf-8")
    out = tmp_path / "targets-folded.json"
    code = gl.main(["fold", "--targets", str(targets), "--lookups-dir",
                    str(lookups_dir), "--out", str(out), "--repo-map",
                    str(map_file)])
    assert code == 0
    return {r["card"]: r for r in json.loads(out.read_text(encoding="utf-8"))}


def test_fold_mapped_home_read_is_ok_whatever_the_other_owners_said(tmp_path):
    base = lay(tmp_path, [
        odoc(DF, {"DRE-1": entry(merged=[merged(77)])}),
        odoc(EB, {"DRE-1": entry(read=False, why=NO_TOKEN_EB)},
             read=False, why=NO_TOKEN_EB)])
    assert not list(base.glob("*.json"))
    got = fold_cmd(tmp_path, [looked("DRE-1")], base)["DRE-1"]
    look = got["lookups"]
    assert (look["ok"], look["why"]) == (True, None)
    assert look["merged_prs"] == [merged(77)]
    assert look["owners"] == {
        DF: {"read": True, "why": None},
        EB: {"read": False, "why": NO_TOKEN_EB},
        DS: {"read": False, "why": "no lookup record for DeltaSolv"}}
    assert ("merged pull request #77 in dreadnought-foundry/bureau-pipeline "
            "touched scripts/a.py on 2026-09-12: PR 77") in got["evidence"]
    assert f"the lookup did not cover EveryBite: {NO_TOKEN_EB}" in got["evidence"]
    assert ("the lookup did not cover DeltaSolv: no lookup record for "
            "DeltaSolv") in got["evidence"]


def _df_cut_by_its_clock():
    """The morning the dreadnought-foundry leg ran out of clock before DRE-1
    and EveryBite read every card with nothing found."""
    why = "time budget of 300 s spent"
    return [odoc(DF, {"DRE-1": entry(read=False, why=why),
                      "DRE-2": entry(read=False, why=why)}, why=why),
            odoc(EB, {"DRE-1": entry(), "DRE-2": entry()})]


def test_fold_mapped_home_cut_by_its_clock_fails_though_another_answered(tmp_path):
    got = fold_cmd(tmp_path, [looked("DRE-1")],
                   lay(tmp_path, _df_cut_by_its_clock()))["DRE-1"]["lookups"]
    assert got["ok"] is False
    assert got["why"] == ("dreadnought-foundry/bureau-pipeline did not answer"
                          " — time budget of 300 s spent")


def test_fold_mapped_home_owner_with_no_token_fails_with_that_reason(tmp_path):
    base = lay(tmp_path, [
        odoc(DF, {"DRE-1": entry(read=False, why=NO_TOKEN_DF)},
             read=False, why=NO_TOKEN_DF),
        odoc(EB, {"DRE-1": entry()})])
    got = fold_cmd(tmp_path, [looked("DRE-1")], base)["DRE-1"]["lookups"]
    assert got["ok"] is False
    assert got["why"] == f"{HOME} did not answer — {NO_TOKEN_DF}"


def test_fold_mapped_home_owner_with_no_document_fails_named(tmp_path):
    base = lay(tmp_path, [odoc(EB, {"DRE-1": entry()})])
    got = fold_cmd(tmp_path, [looked("DRE-1")], base)["DRE-1"]["lookups"]
    assert got["ok"] is False
    assert got["why"] == (f"{HOME} did not answer — no lookup record for "
                          "dreadnought-foundry")


def test_fold_mapped_home_repo_refused_fails_with_its_own_reason(tmp_path):
    base = lay(tmp_path, [
        odoc(DF, {"DRE-1": entry(unread={HOME: "timed out after 20 s"})}),
        odoc(EB, {"DRE-1": entry()})])
    got = fold_cmd(tmp_path, [looked("DRE-1")], base)["DRE-1"]
    assert got["lookups"]["ok"] is False
    assert got["lookups"]["why"] == (f"{HOME} did not answer — timed out "
                                     "after 20 s")
    assert got["lookups"]["owners"][DF] == {"read": True, "why": None}
    assert (f"the lookup did not cover {HOME}: timed out after 20 s"
            in got["evidence"])


def test_fold_a_card_mapped_to_everybite_is_ok_the_same_morning(tmp_path):
    got = fold_cmd(tmp_path, [looked("DRE-1"),
                              looked("DRE-2", repository="EveryBite/atlas")],
                   lay(tmp_path, _df_cut_by_its_clock()))
    assert got["DRE-1"]["lookups"]["ok"] is False
    assert (got["DRE-2"]["lookups"]["ok"], got["DRE-2"]["lookups"]["why"]) == (
        True, None)
    assert ("the lookup did not cover dreadnought-foundry: time budget of "
            "300 s spent") in got["DRE-2"]["evidence"]


def test_fold_reads_the_owner_off_the_document_never_the_directory(tmp_path):
    base = lay(tmp_path, [odoc(DF, {"DRE-1": entry()})],
               under={DF: "DeltaSolv"})
    assert (base / "groom-lookups-DeltaSolv" / "lookups-DeltaSolv.json").exists()
    got = fold_cmd(tmp_path, [looked("DRE-1")], base)["DRE-1"]["lookups"]
    assert got["ok"] is True
    assert got["owners"][DF] == {"read": True, "why": None}
    assert got["owners"][DS] == {"read": False,
                                 "why": "no lookup record for DeltaSolv"}


def test_fold_an_unmapped_row_is_ok_when_one_owner_read_it(tmp_path):
    base = lay(tmp_path, [odoc(EB, {"DRE-6": entry()}),
                          odoc(DF, {}, read=False, why=NO_TOKEN_DF)])
    got = fold_cmd(tmp_path, [looked("DRE-6", repository=None)],
                   base)["DRE-6"]["lookups"]
    assert (got["ok"], got["why"]) == (True, None)


def test_fold_an_unmapped_row_no_owner_read_fails_naming_each_reason(tmp_path):
    why_eb = "rate limited: API rate limit exceeded"
    base = lay(tmp_path, [
        odoc(EB, {"DRE-6": entry(read=False, why=why_eb)}, why=why_eb),
        odoc(DF, {}, read=False, why=NO_TOKEN_DF)])
    got = fold_cmd(tmp_path, [looked("DRE-6", repository=None)],
                   base)["DRE-6"]["lookups"]
    assert got["ok"] is False
    assert got["why"].startswith("no repo answered — ")
    assert f"{DF}: {NO_TOKEN_DF}" in got["why"]
    assert f"{EB}: {why_eb}" in got["why"]
    assert f"{DS}: no lookup record for DeltaSolv" in got["why"]


def test_fold_leaves_a_no_file_row_as_cards_wrote_it(tmp_path):
    rows = [target("DRE-2", "No file.")]
    gl.cards(rows, lops=FakeLops(), now=NOW_ISO)
    before = json.loads(json.dumps(rows[0]))
    got = fold_cmd(tmp_path, rows, lay(tmp_path, _df_cut_by_its_clock()))
    assert got["DRE-2"] == before
    assert got["DRE-2"]["lookups"]["ok"] is True
    assert got["DRE-2"]["lookups"]["owners"] == {}
    assert not any("did not cover" in e for e in got["DRE-2"]["evidence"])


@pytest.mark.parametrize("layout", ["missing", "empty"])
def test_fold_over_no_documents_still_writes_every_owner_unread(tmp_path,
                                                                 layout):
    base = tmp_path / "lookups"
    if layout == "empty":
        (base / "groom-lookups-dreadnought-foundry").mkdir(parents=True)
        (base / "groom-lookups-dreadnought-foundry" / "notes.txt").write_text(
            "x", encoding="utf-8")
    excluded = target("DRE-3", "", excluded="hand-built")
    excluded["lookups"] = None
    got = fold_cmd(tmp_path, [looked("DRE-1"), excluded], base)
    look = got["DRE-1"]["lookups"]
    assert look["ok"] is False
    assert look["owners"] == {o: {"read": False,
                                  "why": f"no lookup record for {o}"}
                              for o in (DF, EB, DS)}
    assert got["DRE-3"]["lookups"] is None


def test_fold_skips_a_file_that_is_not_json_and_says_so(tmp_path, capsys):
    base = lay(tmp_path, [odoc(DF, {"DRE-1": entry()})])
    bad = base / "groom-lookups-EveryBite"
    bad.mkdir()
    (bad / "lookups-EveryBite.json").write_text("{not json", encoding="utf-8")
    got = fold_cmd(tmp_path, [looked("DRE-1")], base)["DRE-1"]["lookups"]
    assert got["ok"] is True
    assert got["owners"][EB] == {"read": False,
                                 "why": "no lookup record for EveryBite"}
    err = capsys.readouterr().err
    assert "lookups-EveryBite.json" in err


def test_fold_copies_a_cut_and_names_it_with_max_commits(tmp_path):
    base = lay(tmp_path, [odoc(DF, {"DRE-1": entry(
        cut=[{"repo": HOME, "path": "scripts/a.py"}])})])
    got = fold_cmd(tmp_path, [looked("DRE-1")], base)["DRE-1"]
    assert got["lookups"]["cut"] == [{"repo": HOME, "path": "scripts/a.py"}]
    n = gl.MAX_COMMITS
    assert (f"more than {n} commits touched scripts/a.py in {HOME} since the "
            f"card was filed; only the newest {n} were followed to pull "
            f"requests, so an older merged pull request may be missing"
            ) in got["evidence"]
    assert got["lookups"]["ok"] is True


def test_fold_with_no_cut_writes_no_cut_line(tmp_path):
    base = lay(tmp_path, [odoc(DF, {"DRE-1": entry()})])
    got = fold_cmd(tmp_path, [looked("DRE-1")], base)["DRE-1"]
    assert got["lookups"]["cut"] == []
    assert not any(e.startswith("more than ") for e in got["evidence"])


def test_fold_names_paths_left_out_once(tmp_path):
    rows = [target("DRE-1", "`a.py` `b.py` `c.py` `d.py`")]
    gl.cards(rows, lops=FakeLops(), now=NOW_ISO)
    got = fold_cmd(tmp_path, rows, lay(tmp_path, [odoc(DF, {"DRE-1": entry()})]))
    assert got["DRE-1"]["evidence"].count(
        "1 more path(s) were not looked up") == 1


def test_fold_function_skips_a_row_with_null_lookups():
    r = target("DRE-3", "", excluded="hand-built")
    r["lookups"] = None
    assert gl.fold([r], [], repo_map=FOLD_MAP)[0]["lookups"] is None
