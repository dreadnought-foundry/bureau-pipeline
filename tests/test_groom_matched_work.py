"""RED-first: the groom lookups find the Done cards and merged pull requests
matched to a card's PROBLEM, not only to the file it names (DRE-7236).

On 2026-10-10 the verify agent saw one repo's files and nothing else, and two
removed cards went unseen: DRE-4049's problem had been fixed in another file
under DRE-5942 (Done 2026-10-07, agent-bureau#3297), and DRE-5657's had been
delivered by DRE-5658 (Done 2026-10-07, portico#942). What this file holds:

  * `problem_terms` — the card's identifier, its paths, its backticked spans
    and its title's two-word phrases, in that order, at most `MAX_TERMS`;
  * `cards` searches the read door first and Linear when the door is off,
    refusing or UNKNOWN, and `matched_work.source` says which answered;
  * only Done cards completed after the card was created are matched, never
    the card itself, ranked and capped, each with its parent chain and the
    pull requests on its attachments;
  * a card that names no file is searched all the same, and its matched pull
    requests are read by `owner` and written by `fold`;
  * nothing here changes a byte of the existing `lookups` block.

Run: cd bureau-pipeline && python3 -m pytest tests/test_groom_matched_work.py -v
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import bureau_read  # noqa: E402
import groom_lookups as gl  # noqa: E402
from bureau_read_fakes import FakeDoor, FakeIssuer, door_env  # noqa: E402

OWNER = "dreadnought-foundry"
AB = "dreadnought-foundry/agent-bureau"
PORTICO = "dreadnought-foundry/portico"
ATLAS = "EveryBite/atlas"
REPO_MAP = {"agent-bureau": AB, "portico": PORTICO, "atlas": ATLAS}
NOW_ISO = "2026-10-13T13:00:00Z"
CREATED = "2026-09-15T22:01:23.504Z"

# ── the cards as Linear recorded them (read 2026-10-13; each last updated
#    2026-10-10T17:26:09Z, so this is the text the 2026-10-10 run read) ──────
DRE_4049_TITLE = (
    "The runner fail-back moves the workflows but not the jobs already queued "
    "— a stranded run holds the reconcile concurrency group and costs an "
    "hour of sweeps after the switch has worked")
DRE_4049_BODY = (
    "When the Mac mini pool goes dark, `runner_pool.py` deletes the per-repo "
    "`BUREAU_RUNS_ON` / `BUREAU_CI_RUNS_ON` variables so new jobs fall through "
    "to GitHub-hosted runners. That part works. What it does not do is deal "
    "with the jobs already queued against the pool, and on 2026-09-15 that "
    "turned a working fail-back into an hour of lost housekeeping.\n\n"
    "**What happened.** The minis served their last job at 13:32:44 PT and the "
    "fail-back fired at 13:43:19 PT. Two reconcile sweeps were already queued "
    "for the pool and simply sat there: `agent-bureau-demo` Reconcile #4531 "
    "queued 13:23:14, started 14:35:43 — **72.0 minutes**; "
    "`bureau-harness` Reconcile #3480 queued 13:32:45, started 14:35:42 — "
    "**62.5 minutes**. Neither started until the minis physically came back.")
DRE_5657_TITLE = ("portico: DRE-5400's proof record carries the step-4 moderator "
                  "upload into PROOF scratch")
DRE_5657_BODY = (
    "Owed by [DRE-5400](https://linear.app/dreadnoughtfoundry/issue/DRE-5400)'s "
    "proof record (`docs/evidence/DRE-5390/README.md`, [PR #869]"
    "(<https://github.com/dreadnought-foundry/portico/pull/869>)). Step 4 of "
    "the card says: as the moderator, in PROOF scratch, \"upload one small "
    "HTML file through it and record that it appears\". On 2026-10-02 the "
    "moderator got the Add-document sheet (screenshot `11`), but nothing was "
    "uploaded. \"Choose files…\" opens the operating system's file "
    "dialog, which the Chrome extension cannot drive, and the harness's "
    "permission layer refused a write into the shared local library. Step 1's "
    "`make stop` was skipped in the same run, because it also stops the Docker "
    "containers other sessions on that machine share.\n\n"
    "## What lands\n\n"
    "* On the local proof portal (`make local-portal`), signed in as "
    "`moderator@local.portico.test`, one small HTML file is uploaded into "
    "PROOF scratch through the Add-document sheet.\n\n"
    "**Files:** docs/evidence/DRE-5390/README.md, one screenshot beside it")
DRE_4417_TITLE = (
    "A build on the mini LIGHT tier was SIGKILLed twice at ~34 min with no "
    "cause recorded — the silence timer did not trip, and the death "
    "receipt says \"the run did not die\"")
#: DRE-4417's first three paragraphs, as recorded. Its fourth, the
#: "Technical detail" paragraph, names `push_rescue.py` in backticks, so the
#: whole body names a file; these three are the part that names none.
DRE_4417_PROSE = (
    "The build agent working on card DRE-2415 (a portico security fix) got "
    "killed mid-run by the infrastructure it runs on — this is not bad "
    "code and not a broken script. It ran for about 34 minutes producing no "
    "visible activity, past our 5-minute \"gone quiet\" safety limit, so the "
    "runner force-stopped it before it could finish and report back "
    "cleanly.\n\n"
    "Good news: our recovery step still worked. It saved the agent's "
    "in-progress commits and opened PR #625 for DRE-2415 automatically, so "
    "the work was not lost. The pipeline still marks this run red on purpose, "
    "even though a PR exists, so a person or agent double-checks it — "
    "the automatic retry correctly declined to re-run (it had already reached "
    "the model and done real work, so re-running would waste a second full "
    "run's cost).\n\n"
    "This happened on both the original run and the automatic retry, so it's "
    "not a one-off blip. No secret or permission was missing, and nothing in "
    "the diff looks unsafe — the failure is upstream of the code.")
DRE_4417_TECHNICAL = (
    "\n\nTechnical detail: the `claude.run` step exited with SIGKILL (code 137) "
    "at 2026-09-20T21:35:47Z, 2061s after \"Claude Code initialized\" with no "
    "execution-output.json ever written; `push_rescue.py` recovered the branch "
    "and opened https://github.com/dreadnought-foundry/portico/pull/625 for "
    "DRE-2415.")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("BUREAU_READ", "BUREAU_READ_URL", "BUREAU_READ_AUDIENCE",
                 "BUREAU_PIPELINE_REF", "ACTIONS_ID_TOKEN_REQUEST_URL",
                 "ACTIONS_ID_TOKEN_REQUEST_TOKEN", "GITHUB_REPOSITORY",
                 "GITHUB_EVENT_NAME"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GH_TOKEN", "owner-installation-token")
    bureau_read.reset_for_tests()
    yield
    bureau_read.reset_for_tests()


# --------------------------------------------------------------------------
# fakes
# --------------------------------------------------------------------------
def target(card, body="", *, title=None, repository=AB, excluded=None,
           created=CREATED, which="planning"):
    """A target row as `groom_verify_agent.targets` writes it."""
    return {"card": card, "repository": repository,
            "repo_slug": (repository or "widgets").split("/")[-1],
            "title": title if title is not None else card, "body": body,
            "evidence": [], "list": which,
            "context": {"created_at": created, "age_days": 30},
            "excluded": excluded}


def hit(ident, *, state="Done", title=None, created="2026-10-06T02:49:26.858Z"):
    """One search node: DRE-6100's four fields, Linear's `searchIssues` too."""
    return {"identifier": ident, "title": title or f"{ident} title",
            "createdAt": created, "state": {"name": state}}


def match(ident, *, state="Done", completed="2026-10-07T01:18:52.501Z",
          parents=(), urls=(), title=None):
    """One card as the match request reads it: its parent chain nested
    nearest first, its attachments' urls."""
    parent = None
    for ident_up in reversed(parents):
        parent = {"identifier": ident_up, "parent": parent}
    return {"identifier": ident, "title": title or f"{ident} title",
            "state": {"name": state}, "completedAt": completed,
            "attachments": {"nodes": [{"url": u} for u in urls]},
            "parent": parent}


class FakeLinear:
    """`linear_ops` as `cards` uses it, holding a key. Every `gql` is kept in
    `calls` as `(kind, query, variables)`: `paths` for the existing newer-card
    request (variables `p<i>`), `search` for the problem search (`t<i>`),
    `match` for the matched cards' read. `found[term]` answers the search,
    `issues[ident]` the match, `paths[path]` the path request."""

    def __init__(self, found=None, issues=None, *, paths=None, key="lin-key",
                 fail=None):
        self.found = dict(found or {})
        self.issues = dict(issues or {})
        self.paths = dict(paths or {})
        self.key = key
        self.fail = dict(fail or {})
        self.calls: list[tuple[str, str, dict]] = []

    def api_key(self):
        if self.key is None:
            raise KeyError("LINEAR_API_KEY")
        return self.key

    def gql(self, query, variables=None):
        variables = dict(variables or {})
        if "completedAt" in query:
            kind = "match"
        elif any(k.startswith("t") for k in variables):
            kind = "search"
        else:
            kind = "paths"
        self.calls.append((kind, query, variables))
        if kind in self.fail:
            raise RuntimeError(self.fail[kind])
        if kind == "match":
            return {alias: copy.deepcopy(self.issues.get(ident))
                    for alias, ident in variables.items()}
        source = self.found if kind == "search" else self.paths
        return {alias: {"nodes": copy.deepcopy(source.get(term, []))}
                for alias, term in variables.items()}

    def kinds(self):
        return [c[0] for c in self.calls]


class KeylessLops(FakeLinear):
    """A `linear_ops` that holds no key: `api_key` raises as the real one
    does inside GitHub Actions."""

    def __init__(self, *a, **kw):
        super().__init__(*a, key=None, **kw)


def unread_pr(repo, number):
    return {"repo": repo, "number": number,
            "url": f"https://github.com/{repo}/pull/{number}",
            "read": False, "why": "not looked up", "merged_at": None,
            "title": None, "body": None, "files": [], "files_cut": False}


def with_matched(row, prs, *, matched_card="DRE-5942"):
    """`row` as `cards` leaves it with one matched card carrying `prs`."""
    row["matched_work"] = {
        "searched": True, "source": "linear", "why": None,
        "terms": [row["card"]],
        "cards": [{"identifier": matched_card, "title": "t", "state": "Done",
                   "completed_at": "2026-10-07T01:18:52.501Z", "parent": None,
                   "ancestors": [], "ancestors_cut": False,
                   "terms": [row["card"]],
                   "prs": [unread_pr(repo, n) for repo, n in prs]}]}
    return row


def no_file(card, **kw):
    r = target(card, "No file named.", **kw)
    r["lookups"] = {"looked_up": False, "ok": True, "why": None, "paths": [],
                    "paths_left_out": 0, "newer_cards": [],
                    "newer_cards_why": None, "merged_prs": [], "cut": [],
                    "owners": {}}
    return r


def looked(card, paths=("scripts/a.py",), **kw):
    r = target(card, "", **kw)
    r["lookups"] = {"looked_up": True, "ok": None, "why": None,
                    "paths": list(paths), "paths_left_out": 0,
                    "newer_cards": [], "newer_cards_why": None,
                    "merged_prs": [], "cut": [], "owners": {}}
    return r


def gh_pull(number, *, title=None, body="", merged="2026-10-07T01:10:00Z",
            changed=None):
    out = {"number": number, "title": title or f"PR {number}", "body": body,
           "merged_at": merged,
           "html_url": f"https://github.com/x/y/pull/{number}"}
    if changed is not None:
        out["changed_files"] = changed
    return out


class PullGh:
    """A `gh api -i` stand-in for one leg. `pulls[(repo, n)]` answers
    `pulls/{n}`, `files[(repo, n)]` the file names `pulls/{n}/files` answers,
    `nexts` the `(repo, n)` whose files answer says more pages exist; a
    commits query answers no commits. `fail(endpoint)` returns an exception
    to raise, or None."""

    def __init__(self, pulls=None, files=None, *, nexts=(), fail=None):
        self.pulls = dict(pulls or {})
        self.files = dict(files or {})
        self.nexts = set(nexts)
        self.fail = fail
        self.calls: list[str] = []

    def __call__(self, args):
        endpoint = args[0]
        self.calls.append(endpoint)
        if self.fail:
            err = self.fail(endpoint)
            if err is not None:
                raise err
        url = urlsplit(endpoint)
        parts = url.path.split("/")
        repo = "/".join(parts[1:3])
        lines = ["HTTP/2.0 200 OK", "Content-Type: application/json"]
        if parts[3] == "commits":
            body = []
        elif parts[-1] == "files":
            key = (repo, int(parts[4]))
            assert parse_qs(url.query) == {"per_page": [str(gl.MAX_PR_FILES)]}
            if key in self.nexts:
                lines.append(f'Link: <https://api.github.com/{url.path}?page=2>;'
                             ' rel="next"')
            body = [{"filename": f} for f in self.files.get(key, [])]
        else:
            body = self.pulls.get((repo, int(parts[4])),
                                  {"message": "Not Found"})
        return "\r\n".join(lines) + "\r\n", json.dumps(body)

    def pr_calls(self):
        return [c for c in self.calls if "/pulls/" in c]


def leg(rows, gh, *, owner=OWNER, budget=None, repo_map=REPO_MAP):
    return gl.owner(rows, owner=owner, gh_run=gh, repo_map=repo_map,
                    now=NOW_ISO, budget=budget)


# --------------------------------------------------------------------------
# the contract constants
# --------------------------------------------------------------------------
def test_contract_constants():
    assert gl.MAX_TERMS == 12
    assert gl.MAX_MATCHED_CARDS == 8
    assert gl.MAX_MATCHED_PRS == 3
    assert gl.MAX_ANCESTRY == 4
    assert gl.MAX_PR_FILES == 30
    assert gl.PR_BODY_LIMIT == 1500
    assert isinstance(gl.STOPWORDS, tuple) and "the" in gl.STOPWORDS
    assert all(w == w.lower() for w in gl.STOPWORDS)


# --------------------------------------------------------------------------
# problem_terms
# --------------------------------------------------------------------------
def test_dre_4049s_recorded_title_yields_stranded_run():
    terms = gl.problem_terms(target("DRE-4049", DRE_4049_BODY,
                                    title=DRE_4049_TITLE))
    assert "stranded run" in terms
    assert terms == [
        "DRE-4049", "runner_pool.py",
        "BUREAU_RUNS_ON", "BUREAU_CI_RUNS_ON", "agent-bureau-demo",
        "jobs already", "already queued", "stranded run", "run holds",
        "reconcile concurrency", "concurrency group"]
    # The title alone finds it too: the phrase is the title's.
    assert "stranded run" in gl.problem_terms(
        {"card": "DRE-4049", "title": DRE_4049_TITLE, "body": ""})


def test_dre_5657s_recorded_body_yields_its_proof_record_path():
    terms = gl.problem_terms(target("DRE-5657", DRE_5657_BODY,
                                    title=DRE_5657_TITLE))
    assert "docs/evidence/DRE-5390/README.md" in terms
    assert terms[:2] == ["DRE-5657", "docs/evidence/DRE-5390/README.md"]
    # `11` is too short, `make stop` holds whitespace, the path is a path.
    assert terms[2:3] == ["moderator@local.portico.test"]
    assert terms.count("docs/evidence/DRE-5390/README.md") == 1


def test_the_terms_come_in_the_declared_order_and_stop_at_max_terms():
    row = target(
        "DRE-1",
        "Edit `a/one.py`, `b/two.md`, `c/three.ts` and `d/four.py`. "
        "Also `span_one`, `span_two`, `span_three`, `span_four`.",
        title="Fix `title_span` where stale runner leases outlive dead "
              "workers across busy mornings and cost hours daily")
    terms = gl.problem_terms(row)
    assert len(terms) == gl.MAX_TERMS == 12
    assert terms == [
        "DRE-1",
        "a/one.py", "b/two.md", "c/three.ts",           # MAX_PATHS of them
        "title_span", "span_one", "span_two",           # 3 spans, title first
        "stale runner", "runner leases", "leases outlive", "outlive dead",
        "dead workers"]                                 # cut at 12


def test_spans_are_whitespace_free_four_to_eighty_and_not_paths():
    long_span = "x" * 81
    row = {"card": "DRE-2", "title": "",
           "body": f"`abc` `has space` `{long_span}` `scripts/a.py:12` "
                   f"`{'y' * 80}` `abcd` `abcd`"}
    assert gl.problem_terms(row) == ["DRE-2", "scripts/a.py", "y" * 80, "abcd"]


def test_phrases_are_adjacent_words_of_three_letters_lowercased_in_order():
    row = {"card": "DRE-3", "body": "",
           "title": "The Stale-lease Monitor: of the RUNNER POOL, fix it "
                    "for 2 Big jobs"}
    # `Stale-lease` is not a word, `of`/`it`/`2` are too short or stop,
    # `for` is a stopword.
    assert gl.problem_terms(row) == ["DRE-3", "runner pool", "pool fix",
                                     "big jobs"]


def test_at_most_six_phrases():
    row = {"card": "DRE-4", "body": "",
           "title": "alpha bravo charlie delta echo foxtrot golf hotel india"}
    assert gl.problem_terms(row) == [
        "DRE-4", "alpha bravo", "bravo charlie", "charlie delta",
        "delta echo", "echo foxtrot", "foxtrot golf"]


def test_terms_of_an_empty_row():
    assert gl.problem_terms({}) == []
    assert gl.problem_terms({"card": "DRE-5", "title": None, "body": None}) == [
        "DRE-5"]


# --------------------------------------------------------------------------
# cards — the search, Linear first when the door is off
# --------------------------------------------------------------------------
def test_with_the_door_off_cards_asks_linear_once_per_card_and_says_linear():
    rows = [target("DRE-4049", DRE_4049_BODY, title=DRE_4049_TITLE),
            target("DRE-5657", DRE_5657_BODY, title=DRE_5657_TITLE)]
    lops = FakeLinear(
        found={"stranded run": [hit("DRE-5942")],
               "docs/evidence/DRE-5390/README.md": [hit("DRE-5658")]},
        issues={"DRE-5942": match("DRE-5942", parents=("DRE-5910",),
                                  urls=(f"https://github.com/{AB}/pull/3297",)),
                "DRE-5658": match("DRE-5658",
                                  completed="2026-10-07T17:44:06.247Z",
                                  urls=(f"https://github.com/{PORTICO}/pull/942",))})
    gl.cards(rows, lops=lops, now=NOW_ISO)
    searches = [c for c in lops.calls if c[0] == "search"]
    assert len(searches) == 2
    for (_, query, variables), row in zip(searches, rows):
        terms = gl.problem_terms(row)
        assert [variables[f"t{i}"] for i in range(len(terms))] == terms
        assert query.count("searchIssues(") == len(terms)
        assert "t0: searchIssues(term: $t0, first: 25)" in query
    assert lops.kinds().count("match") == 2
    a, b = (r["matched_work"] for r in rows)
    assert (a["searched"], a["source"], a["why"]) == (True, "linear", None)
    assert [c["identifier"] for c in a["cards"]] == ["DRE-5942"]
    assert a["cards"][0]["prs"] == [unread_pr(AB, 3297)]
    assert a["cards"][0]["terms"] == ["stranded run"]
    assert [c["identifier"] for c in b["cards"]] == ["DRE-5658"]
    assert b["source"] == "linear"
    assert b["cards"][0]["prs"] == [unread_pr(PORTICO, 942)]


def test_the_matched_card_carries_the_contract_shape():
    rows = [target("DRE-4049", "", title=DRE_4049_TITLE)]
    lops = FakeLinear(
        found={"stranded run": [hit("DRE-5942", title="stranded runs re-run")]},
        issues={"DRE-5942": match("DRE-5942", parents=("DRE-5910",),
                                  title="stranded runs re-run",
                                  urls=(f"https://github.com/{AB}/pull/3297",))})
    gl.cards(rows, lops=lops, now=NOW_ISO)
    work = rows[0]["matched_work"]
    assert set(work) == {"searched", "source", "why", "terms", "cards"}
    assert work["terms"] == gl.problem_terms(rows[0])
    assert work["cards"] == [{
        "identifier": "DRE-5942", "title": "stranded runs re-run",
        "state": "Done", "completed_at": "2026-10-07T01:18:52.501Z",
        "parent": "DRE-5910", "ancestors": ["DRE-5910"],
        "ancestors_cut": False, "terms": ["stranded run"],
        "prs": [unread_pr(AB, 3297)]}]
    json.dumps(rows)


def test_no_candidate_means_searched_with_nothing_found_and_no_match_read():
    rows = [target("DRE-1", "", title="lonely problem here")]
    lops = FakeLinear()
    gl.cards(rows, lops=lops, now=NOW_ISO)
    assert lops.kinds() == ["search"]
    assert rows[0]["matched_work"]["searched"] is True
    assert rows[0]["matched_work"]["cards"] == []


def test_the_match_request_asks_state_completion_attachments_and_four_parents():
    rows = [target("DRE-1", "", title="stale runner")]
    lops = FakeLinear(found={"stale runner": [hit("DRE-9")]},
                      issues={"DRE-9": match("DRE-9")})
    gl.cards(rows, lops=lops, now=NOW_ISO)
    [(_, query, variables)] = [c for c in lops.calls if c[0] == "match"]
    assert variables == {"c0": "DRE-9"}
    flat = " ".join(query.split())
    assert "c0: issue(id: $c0)" in flat
    for field in ("state { name }", "completedAt", "attachments",
                  "{ url }",
                  "parent { identifier parent { identifier parent { "
                  "identifier parent { identifier } } } }"):
        assert field in flat, field
    assert flat.count("parent {") == gl.MAX_ANCESTRY


# --------------------------------------------------------------------------
# cards — the door first
# --------------------------------------------------------------------------
def _door_search(door, found):
    def answer(endpoint, query, headers):
        return 200, door.envelope(copy.deepcopy(found.get(query.get("term"), [])))
    door.routes["/search"] = answer


def _point(monkeypatch, door, issuer, **kw):
    for key, value in door_env(door_url=door.url, issuer=issuer, **kw).items():
        monkeypatch.setenv(key, value)


def test_with_the_door_answering_it_says_door_and_asks_linear_no_search(
        monkeypatch):
    row = target("DRE-4049", "", title=DRE_4049_TITLE)
    terms = gl.problem_terms(row)
    lops = FakeLinear(
        found={"stranded run": [hit("DRE-5942")]},   # never asked
        issues={"DRE-5942": match("DRE-5942",
                                  urls=(f"https://github.com/{AB}/pull/3297",))})
    with FakeIssuer() as issuer, FakeDoor() as door:
        _point(monkeypatch, door, issuer)
        _door_search(door, {"stranded run": [hit("DRE-5942")]})
        gl.cards([row], lops=lops, now=NOW_ISO)
    assert [r["query"]["term"] for r in door.requests] == terms
    assert all(r["query"]["scope"] == "fleet" and r["query"]["first"] == "25"
               for r in door.requests)
    assert "search" not in lops.kinds()
    assert lops.kinds() == ["match"]
    work = row["matched_work"]
    assert (work["searched"], work["source"]) == (True, "door")
    assert [c["identifier"] for c in work["cards"]] == ["DRE-5942"]


@pytest.mark.parametrize("refusal", [(403, None), (401, None), (404, None),
                                     (None, "stale")])
def test_a_door_refusing_or_unknown_sends_the_card_to_linear(monkeypatch,
                                                            refusal):
    status, reason = refusal
    rows = [target("DRE-4049", "", title=DRE_4049_TITLE),
            target("DRE-5657", "", title=DRE_5657_TITLE)]
    lops = FakeLinear(found={"stranded run": [hit("DRE-5942")]},
                      issues={"DRE-5942": match("DRE-5942")})
    with FakeIssuer() as issuer, FakeDoor() as door:
        _point(monkeypatch, door, issuer)
        door.routes["/search"] = ((status, {"error": {"code": "FORBIDDEN"}})
                                  if status else door.unknown(reason))
        gl.cards(rows, lops=lops, now=NOW_ISO)
        # A fleet refusal is UNKNOWN for that read: the door stays in use,
        # and the second card asks it again before falling back.
        assert len(door.requests) == 2
    assert lops.kinds().count("search") == 2
    for row in rows:
        assert row["matched_work"]["source"] == "linear"
        assert row["matched_work"]["searched"] is True
    assert [c["identifier"] for c in rows[0]["matched_work"]["cards"]] == [
        "DRE-5942"]


def test_a_door_that_fails_is_not_asked_again_and_linear_answers(monkeypatch):
    rows = [target("DRE-1", "", title="stale runner"),
            target("DRE-2", "", title="dead worker")]
    lops = FakeLinear()
    with FakeIssuer() as issuer, FakeDoor() as door:
        _point(monkeypatch, door, issuer)
        door.routes["/search"] = (503, {"error": {"code": "CLOSED"}})
        gl.cards(rows, lops=lops, now=NOW_ISO)
        assert len(door.requests) == 1
    assert lops.kinds() == ["search", "search"]
    assert [r["matched_work"]["source"] for r in rows] == ["linear", "linear"]


def test_a_door_answer_part_way_is_never_mixed_with_linear(monkeypatch):
    rows = [target("DRE-1", "", title="stale runner leases")]
    lops = FakeLinear(found={"runner leases": [hit("DRE-8")]},
                      issues={"DRE-8": match("DRE-8")})
    with FakeIssuer() as issuer, FakeDoor() as door:
        _point(monkeypatch, door, issuer)

        def answer(endpoint, query, headers):
            if query["term"] == "runner leases":
                return door.unknown("stale")
            return 200, door.envelope([hit("DRE-7")])
        door.routes["/search"] = answer
        gl.cards(rows, lops=lops, now=NOW_ISO)
    work = rows[0]["matched_work"]
    assert work["source"] == "linear"
    assert [c["identifier"] for c in work["cards"]] == ["DRE-8"]


def test_a_door_saying_linear_hold_is_a_skip_never_a_linear_search(monkeypatch):
    rows = [target("DRE-1", "", title="stale runner")]
    lops = FakeLinear()
    with FakeIssuer() as issuer, FakeDoor() as door:
        _point(monkeypatch, door, issuer)
        door.routes["/search"] = door.unknown("linear-hold")
        gl.cards(rows, lops=lops, now=NOW_ISO)
    assert lops.calls == []
    work = rows[0]["matched_work"]
    assert work["searched"] is False and work["cards"] == []
    assert "linear-hold" in work["why"]


# --------------------------------------------------------------------------
# the match: Done, completed after the card was filed, never itself, ranked
# --------------------------------------------------------------------------
def test_only_done_cards_completed_after_creation_reach_the_match():
    rows = [target("DRE-1", "", title="stale runner")]
    found = {"stale runner": [hit("DRE-1"),            # the card itself
                              hit("DRE-20", state="Canceled"),
                              hit("DRE-21"),           # Done, but too early
                              hit("DRE-22"),           # Done since, Canceled now
                              hit("DRE-23")]}          # the one
    issues = {"DRE-1": match("DRE-1"),
              "DRE-20": match("DRE-20", state="Canceled"),
              "DRE-21": match("DRE-21", completed="2026-09-10T00:00:00Z"),
              "DRE-22": match("DRE-22", state="Canceled"),
              "DRE-23": match("DRE-23")}
    lops = FakeLinear(found=found, issues=issues)
    gl.cards(rows, lops=lops, now=NOW_ISO)
    assert [c["identifier"] for c in rows[0]["matched_work"]["cards"]] == [
        "DRE-23"]
    [(_, _, variables)] = [c for c in lops.calls if c[0] == "match"]
    assert "DRE-1" not in variables.values()


def test_a_card_never_completed_or_unreadable_is_not_matched():
    rows = [target("DRE-1", "", title="stale runner")]
    lops = FakeLinear(found={"stale runner": [hit("DRE-30"), hit("DRE-31")]},
                      issues={"DRE-30": match("DRE-30", completed=None)})
    gl.cards(rows, lops=lops, now=NOW_ISO)
    assert rows[0]["matched_work"]["cards"] == []
    assert rows[0]["matched_work"]["searched"] is True


def test_ranked_by_terms_matched_then_newest_completion_at_most_eight():
    title = "stale runner leases outlive"   # three phrases
    found = {"stale runner": [hit(f"DRE-{n}") for n in range(100, 110)],
             "runner leases": [hit("DRE-105"), hit("DRE-107")],
             "leases outlive": [hit("DRE-107")]}
    issues = {f"DRE-{n}": match(f"DRE-{n}",
                                completed=f"2026-10-{n - 99:02d}T00:00:00Z")
              for n in range(100, 110)}
    rows = [target("DRE-1", "", title=title)]
    gl.cards(rows, lops=FakeLinear(found=found, issues=issues), now=NOW_ISO)
    got = rows[0]["matched_work"]["cards"]
    assert len(got) == gl.MAX_MATCHED_CARDS == 8
    assert [c["identifier"] for c in got] == [
        "DRE-107", "DRE-105",                  # three terms, then two
        "DRE-109", "DRE-108", "DRE-106", "DRE-104", "DRE-103", "DRE-102"]
    assert got[0]["terms"] == ["stale runner", "runner leases",
                               "leases outlive"]


# --------------------------------------------------------------------------
# the parent chain
# --------------------------------------------------------------------------
def test_a_card_two_levels_under_another_names_both_nearest_first():
    rows = [target("DRE-1", "", title="stale runner")]
    lops = FakeLinear(found={"stale runner": [hit("DRE-40")]},
                      issues={"DRE-40": match("DRE-40",
                                              parents=("DRE-41", "DRE-42"))})
    gl.cards(rows, lops=lops, now=NOW_ISO)
    [card] = rows[0]["matched_work"]["cards"]
    assert card["parent"] == "DRE-41"
    assert card["ancestors"] == ["DRE-41", "DRE-42"]
    assert card["ancestors_cut"] is False


def test_a_chain_that_reaches_the_fourth_level_is_cut():
    rows = [target("DRE-1", "", title="stale runner")]
    lops = FakeLinear(found={"stale runner": [hit("DRE-50"), hit("DRE-60")]},
                      issues={"DRE-50": match("DRE-50", parents=(
                          "DRE-51", "DRE-52", "DRE-53", "DRE-54")),
                              "DRE-60": match("DRE-60", parents=(
                                  "DRE-61", "DRE-62", "DRE-63", "DRE-64",
                                  "DRE-65")),
                              })
    gl.cards(rows, lops=lops, now=NOW_ISO)
    by = {c["identifier"]: c for c in rows[0]["matched_work"]["cards"]}
    assert by["DRE-50"]["ancestors"] == ["DRE-51", "DRE-52", "DRE-53", "DRE-54"]
    assert by["DRE-50"]["ancestors_cut"] is True
    # Nothing above the fourth level is ever written, whatever came back.
    assert by["DRE-60"]["ancestors"] == ["DRE-61", "DRE-62", "DRE-63", "DRE-64"]
    assert by["DRE-60"]["ancestors_cut"] is True


def test_a_card_with_no_parent():
    rows = [target("DRE-1", "", title="stale runner")]
    lops = FakeLinear(found={"stale runner": [hit("DRE-70")]},
                      issues={"DRE-70": match("DRE-70")})
    gl.cards(rows, lops=lops, now=NOW_ISO)
    [card] = rows[0]["matched_work"]["cards"]
    assert (card["parent"], card["ancestors"], card["ancestors_cut"]) == (
        None, [], False)


# --------------------------------------------------------------------------
# the pull requests on a matched card's attachments
# --------------------------------------------------------------------------
def test_only_github_pull_request_links_at_most_three_each_once():
    urls = ("https://linear.app/dreadnoughtfoundry/issue/DRE-5",
            f"https://github.com/{AB}/pull/3297",
            f"https://github.com/{AB}/pull/3297/files",
            f"https://github.com/{AB}/issues/12",
            f"https://github.com/{PORTICO}/pull/942#discussion_r1",
            f"https://github.com/{ATLAS}/pull/7",
            f"https://github.com/{AB}/pull/3300")
    rows = [target("DRE-1", "", title="stale runner")]
    lops = FakeLinear(found={"stale runner": [hit("DRE-80")]},
                      issues={"DRE-80": match("DRE-80", urls=urls)})
    gl.cards(rows, lops=lops, now=NOW_ISO)
    [card] = rows[0]["matched_work"]["cards"]
    assert card["prs"] == [unread_pr(AB, 3297), unread_pr(PORTICO, 942),
                           unread_pr(ATLAS, 7)]
    assert len(card["prs"]) == gl.MAX_MATCHED_PRS


# --------------------------------------------------------------------------
# what is never searched, and what a failed search leaves
# --------------------------------------------------------------------------
MAIN_NO_FILE = {"looked_up": False, "ok": True, "why": None, "paths": [],
                "paths_left_out": 0, "newer_cards": [], "newer_cards_why": None,
                "merged_prs": [], "cut": [], "owners": {}}


def test_an_excluded_row_is_not_searched_and_keeps_null_lookups():
    rows = [target("DRE-1", "Edit `scripts/a.py`.", title="stale runner",
                   excluded="hand-built")]
    lops = FakeLinear()
    gl.cards(rows, lops=lops, now=NOW_ISO)
    assert lops.calls == []
    assert rows[0]["lookups"] is None
    assert rows[0]["evidence"] == []
    work = rows[0]["matched_work"]
    assert work["searched"] is False and work["source"] is None
    assert work["why"] == gl.EXCLUDED_NOT_SEARCHED
    assert work["cards"] == []


def test_no_linear_key_is_not_searched_and_asks_nothing(monkeypatch):
    rows = [target("DRE-1", "", title="stale runner")]
    lops = KeylessLops(found={"stale runner": [hit("DRE-9")]})
    with FakeIssuer() as issuer, FakeDoor() as door:
        _point(monkeypatch, door, issuer)
        _door_search(door, {"stale runner": [hit("DRE-9")]})
        gl.cards(rows, lops=lops, now=NOW_ISO)
        assert door.requests == []
    assert lops.calls == []
    work = rows[0]["matched_work"]
    assert (work["searched"], work["source"], work["why"]) == (
        False, None, gl.NO_LINEAR_KEY)
    assert rows[0]["lookups"] == MAIN_NO_FILE


def test_a_lops_with_no_key_reader_is_read_as_holding_no_key():
    class Bare:
        def __init__(self):
            self.calls = []

        def gql(self, query, variables=None):
            self.calls.append(query)
            return {}
    rows = [target("DRE-1", "", title="stale runner")]
    lops = Bare()
    gl.cards(rows, lops=lops, now=NOW_ISO)
    assert lops.calls == []
    assert rows[0]["matched_work"]["why"] == gl.NO_LINEAR_KEY


def test_a_row_with_no_creation_date_is_not_searched():
    rows = [target("DRE-1", "", title="stale runner", created=None)]
    lops = FakeLinear()
    gl.cards(rows, lops=lops, now=NOW_ISO)
    assert lops.calls == []
    assert rows[0]["matched_work"]["searched"] is False
    assert rows[0]["matched_work"]["why"] == gl.NO_CREATED_AT


@pytest.mark.parametrize("kind", ["search", "match"])
def test_a_search_that_raises_leaves_every_lookup_field_as_main_writes_it(kind):
    body = "Edit `scripts/a.py` and `docs/b.md`."
    newer = {"scripts/a.py": [{"identifier": "DRE-9", "title": "t",
                               "createdAt": "2026-09-20T10:00:00.000Z",
                               "state": {"name": "Done"}}]}
    rows = [target("DRE-1", body, title="stale runner"),
            target("DRE-2", "No file.", title="stale runner")]
    lops = FakeLinear(found={"stale runner": [hit("DRE-9")]}, paths=newer,
                      fail={kind: "Linear said RATELIMITED"})
    gl.cards(rows, lops=lops, now=NOW_ISO)
    main_rows = [target("DRE-1", body, title="stale runner"),
                 target("DRE-2", "No file.", title="stale runner")]
    gl.cards(main_rows, lops=KeylessLops(paths=newer), now=NOW_ISO)
    for got, want in zip(rows, main_rows):
        assert json.dumps(got["lookups"]) == json.dumps(want["lookups"])
        assert got["evidence"] == want["evidence"]
        work = got["matched_work"]
        assert work["searched"] is False
        assert work["why"] == "Linear said RATELIMITED"
        assert work["cards"] == []
    assert rows[1]["lookups"] == MAIN_NO_FILE
    assert rows[0]["lookups"]["newer_cards"][0]["identifier"] == "DRE-9"


def test_a_search_that_raises_anything_never_fails_the_card(monkeypatch):
    def broken(row):
        raise TypeError("a term no one expected")
    monkeypatch.setattr(gl, "problem_terms", broken)
    rows = [target("DRE-1", "Edit `scripts/a.py`.", title="stale runner")]
    lops = FakeLinear()
    gl.cards(rows, lops=lops, now=NOW_ISO)
    assert rows[0]["lookups"]["looked_up"] is True
    assert rows[0]["lookups"]["ok"] is None
    assert rows[0]["matched_work"]["searched"] is False
    assert rows[0]["matched_work"]["why"] == "a term no one expected"
    assert lops.kinds() == ["paths"]


def test_the_lookups_block_is_byte_identical_with_and_without_the_search():
    newer = {"scripts/a.py": [{"identifier": "DRE-9", "title": "t",
                               "createdAt": "2026-09-20T10:00:00.000Z",
                               "state": {"name": "In Progress"}}]}
    bodies = ["Edit `scripts/a.py`.", "No file.",
              "`a.py` `b.py` `c.py` `d.py`"]
    searched = [target(f"DRE-{i}", b, title="stale runner")
                for i, b in enumerate(bodies)]
    gl.cards(searched, lops=FakeLinear(
        found={"stale runner": [hit("DRE-90")]},
        issues={"DRE-90": match("DRE-90")}, paths=newer), now=NOW_ISO)
    plain = [target(f"DRE-{i}", b, title="stale runner")
             for i, b in enumerate(bodies)]
    gl.cards(plain, lops=KeylessLops(paths=newer), now=NOW_ISO)
    for got, want in zip(searched, plain):
        assert json.dumps(got["lookups"]) == json.dumps(want["lookups"])
        assert got["evidence"] == want["evidence"]
        assert got["matched_work"]["searched"] is True


def test_a_row_written_before_this_card_reads_as_not_searched():
    assert gl.matched_work_of({"card": "DRE-1"}) == {
        "searched": False, "why": "not searched", "cards": []}
    assert gl.matched_work_of({"card": "DRE-1", "matched_work": None}) == {
        "searched": False, "why": "not searched", "cards": []}
    work = {"searched": True, "source": "door", "why": None, "terms": [],
            "cards": []}
    assert gl.matched_work_of({"matched_work": work}) is work


# --------------------------------------------------------------------------
# DRE-4417 — a card that names no file, through cards, owner and fold
# --------------------------------------------------------------------------
def test_dre_4417s_whole_recorded_body_names_one_file_and_its_prose_none():
    assert gl.paths_of({"body": DRE_4417_PROSE + DRE_4417_TECHNICAL}) == (
        ["push_rescue.py"], 0)
    assert gl.paths_of({"body": DRE_4417_PROSE}) == ([], 0)


def test_a_card_naming_no_file_is_searched_read_and_folded(tmp_path):
    row = target("DRE-4417", DRE_4417_PROSE, title=DRE_4417_TITLE,
                 repository=AB, created="2026-09-20T22:10:03.580Z")
    terms = gl.problem_terms(row)
    assert "silence timer" in terms
    assert gl.paths_of(row) == ([], 0)
    lops = FakeLinear(
        found={"silence timer": [hit("DRE-4500")],
               "death receipt": [hit("DRE-4500")]},
        issues={"DRE-4500": match(
            "DRE-4500", completed="2026-09-28T20:00:00Z",
            urls=(f"https://github.com/{AB}/pull/2900",
                  f"https://github.com/{ATLAS}/pull/12"))})
    rows = [row]
    gl.cards(rows, lops=lops, now=NOW_ISO)

    # cards: searched, and the lookups block is what main writes.
    assert lops.kinds() == ["search", "match"]
    assert row["lookups"] == MAIN_NO_FILE
    main_row = target("DRE-4417", DRE_4417_PROSE, title=DRE_4417_TITLE,
                      repository=AB, created="2026-09-20T22:10:03.580Z")
    gl.cards([main_row], lops=KeylessLops(), now=NOW_ISO)
    assert json.dumps(row["lookups"]) == json.dumps(main_row["lookups"])
    assert row["evidence"] == main_row["evidence"]
    work = row["matched_work"]
    assert work["searched"] is True and work["source"] == "linear"
    [card] = work["cards"]
    assert card["identifier"] == "DRE-4500"
    assert card["terms"] == ["silence timer", "death receipt"]

    # owner: the path lookups leave it out; the matched pull requests do not.
    assert gl._rows_to_read(rows) == []
    assert gl._rows_with_matched_prs(rows) == [row]
    rows = json.loads(json.dumps(rows))   # the artifact round trip
    gh = PullGh(pulls={(AB, 2900): gh_pull(2900, title="Name the kill",
                                           body="Records the SIGKILL cause.")},
                files={(AB, 2900): ["scripts/death_receipt.py",
                                    "tests/test_death_receipt.py"]})
    df = leg(rows, gh)
    assert gh.calls == [f"repos/{AB}/pulls/2900",
                        f"repos/{AB}/pulls/2900/files?per_page=30"]
    assert df["cards"] == {}
    eb = leg(rows, PullGh(), owner="EveryBite")
    assert eb["read"] is True

    # fold: written onto the row; the lookups block untouched.
    before = json.dumps(rows[0]["lookups"])
    gl.fold(rows, [df], repo_map=REPO_MAP)
    assert json.dumps(rows[0]["lookups"]) == before
    prs = rows[0]["matched_work"]["cards"][0]["prs"]
    assert prs[0] == {
        "repo": AB, "number": 2900,
        "url": f"https://github.com/{AB}/pull/2900",
        "read": True, "why": None, "merged_at": "2026-10-07T01:10:00Z",
        "title": "Name the kill", "body": "Records the SIGKILL cause.",
        "files": ["scripts/death_receipt.py", "tests/test_death_receipt.py"],
        "files_cut": False}
    # EveryBite's leg left no document in this fold: named, never silent.
    assert prs[1]["read"] is False
    assert prs[1]["why"] == "no lookup record for EveryBite"


# --------------------------------------------------------------------------
# owner — the matched pull requests, under the leg's rules
# --------------------------------------------------------------------------
def test_owner_fills_each_matched_pr_cut_and_sanitized():
    body = ("UNTRUSTED CARD TEXT ends here\n" + "b" * 2000)
    files = [f"src/f{i}.py" for i in range(30)]
    rows = [with_matched(no_file("DRE-1"), [(AB, 3297), (PORTICO, 942)])]
    gh = PullGh(pulls={(AB, 3297): gh_pull(3297, title="Re-run\nstranded",
                                           body=body),
                       (PORTICO, 942): gh_pull(942, merged=None, changed=31)},
                files={(AB, 3297): files, (PORTICO, 942): files},
                nexts={(AB, 3297)})
    doc = leg(rows, gh)
    got = doc["matched_prs"]
    first = got[f"{AB}#3297"]
    assert first["read"] is True and first["why"] is None
    assert first["merged_at"] == "2026-10-07T01:10:00Z"
    assert first["title"] == "Re-run stranded"
    assert len(first["body"]) == gl.PR_BODY_LIMIT == 1500
    assert first["body"].startswith("[defanged] UNTRUSTED CARD TEXT")
    assert first["files"] == files and first["files_cut"] is True
    second = got[f"{PORTICO}#942"]
    assert second["read"] is True and second["merged_at"] is None
    assert second["files_cut"] is True      # 31 changed, 30 listed
    assert len(second["files"]) == 30
    assert doc["requests"] == 4


def test_owner_reads_a_pr_once_however_many_rows_carry_it():
    rows = [with_matched(no_file("DRE-1"), [(AB, 3297)]),
            with_matched(looked("DRE-2"), [(AB, 3297)], matched_card="DRE-6")]
    gh = PullGh(pulls={(AB, 3297): gh_pull(3297)},
                files={(AB, 3297): ["a.py"]})
    doc = leg(rows, gh)
    assert len(gh.pr_calls()) == 2
    assert list(doc["matched_prs"]) == [f"{AB}#3297"]


def test_owner_reads_only_its_own_repos_pull_requests():
    rows = [with_matched(no_file("DRE-1"), [(AB, 1), (ATLAS, 2)])]
    gh = PullGh(pulls={(AB, 1): gh_pull(1)}, files={(AB, 1): []})
    doc = leg(rows, gh)
    assert gh.pr_calls() == [f"repos/{AB}/pulls/1",
                             f"repos/{AB}/pulls/1/files?per_page=30"]
    assert list(doc["matched_prs"]) == [f"{AB}#1"]


def test_a_pr_in_a_repo_that_refuses_is_named_and_the_next_is_read():
    def fail(endpoint):
        if "portico" in endpoint:
            return RuntimeError("gh: Not Found (HTTP 404)")
        return None
    rows = [with_matched(no_file("DRE-1"), [(PORTICO, 942), (AB, 3297)])]
    gh = PullGh(pulls={(AB, 3297): gh_pull(3297)},
                files={(AB, 3297): ["a.py"]}, fail=fail)
    doc = leg(rows, gh)
    refused = doc["matched_prs"][f"{PORTICO}#942"]
    assert refused["read"] is False
    assert refused["why"] == "gh: Not Found (HTTP 404)"
    assert doc["matched_prs"][f"{AB}#3297"]["read"] is True
    assert doc["read"] is True and doc["why"] is None


def test_a_pr_answer_that_is_not_an_object_is_a_refusal():
    rows = [with_matched(no_file("DRE-1"), [(AB, 5)])]
    doc = leg(rows, PullGh())      # answers {"message": "Not Found"}… no number
    entry = doc["matched_prs"][f"{AB}#5"]
    assert entry["read"] is False
    assert entry["why"]


def test_a_leg_out_of_budget_names_the_cut_on_every_pr_it_did_not_read():
    rows = [looked("DRE-1", paths=("a.py",)),
            with_matched(no_file("DRE-2"), [(AB, 1), (AB, 2)])]
    gh = PullGh(pulls={(AB, 1): gh_pull(1), (AB, 2): gh_pull(2)},
                files={(AB, 1): [], (AB, 2): []})
    # 2 path requests (two repos), then pull 1 (2 requests); pull 2 is cut.
    doc = leg(rows, gh, budget=4, repo_map={"agent-bureau": AB,
                                             "portico": PORTICO})
    assert doc["cards"]["DRE-1"]["read"] is True
    assert doc["matched_prs"][f"{AB}#1"]["read"] is True
    cut = doc["matched_prs"][f"{AB}#2"]
    assert cut == {"read": False, "why": "request budget of 4 spent",
                   "merged_at": None, "title": None, "body": None,
                   "files": [], "files_cut": False}
    assert doc["why"] == "request budget of 4 spent"
    assert doc["requests"] == 4


def test_a_leg_stopped_by_the_path_lookups_names_every_pr_unread():
    rows = [looked("DRE-1", paths=("a.py",)),
            with_matched(no_file("DRE-2"), [(AB, 1)])]
    doc = leg(rows, PullGh(), budget=0)
    assert doc["cards"]["DRE-1"]["why"] == "request budget of 0 spent"
    assert doc["matched_prs"][f"{AB}#1"]["why"] == "request budget of 0 spent"


def test_a_leg_with_no_token_names_every_pr_unread(monkeypatch):
    monkeypatch.delenv("GH_TOKEN")
    rows = [with_matched(no_file("DRE-1"), [(AB, 1)])]
    gh = PullGh()
    doc = leg(rows, gh)
    assert gh.calls == []
    assert doc["read"] is False
    assert doc["matched_prs"][f"{AB}#1"]["read"] is False
    assert doc["matched_prs"][f"{AB}#1"]["why"].startswith("no token for ")


def test_the_path_lookups_are_unchanged_by_the_matched_prs():
    rows = [looked("DRE-1", paths=("a.py", "b.py")),
            with_matched(looked("DRE-2", paths=("c.py",)), [(AB, 9)])]
    gh = PullGh(pulls={(AB, 9): gh_pull(9)}, files={(AB, 9): []})
    doc = leg(rows, gh)
    plain_rows = json.loads(json.dumps(rows))
    for r in plain_rows:
        r.pop("matched_work", None)
    plain_gh = PullGh()
    plain = leg(plain_rows, plain_gh)
    assert doc["cards"] == plain["cards"]
    assert gh.calls[:len(plain_gh.calls)] == plain_gh.calls
    assert "matched_prs" not in plain
    assert set(plain) == {"owner", "read", "why", "requests", "budget",
                          "seconds", "cards"}


def test_a_row_with_no_matched_work_is_read_as_before():
    rows = [looked("DRE-1"), no_file("DRE-2")]
    gh = PullGh()
    doc = leg(rows, gh)
    assert gl._rows_with_matched_prs(rows) == []
    assert "matched_prs" not in doc
    assert list(doc["cards"]) == ["DRE-1"]


# --------------------------------------------------------------------------
# fold — onto every row carrying the pull request
# --------------------------------------------------------------------------
def test_fold_writes_a_pr_onto_every_row_that_carries_it():
    rows = [with_matched(no_file("DRE-1"), [(AB, 3297)]),
            with_matched(looked("DRE-2"), [(AB, 3297)], matched_card="DRE-6")]
    gh = PullGh(pulls={(AB, 3297): gh_pull(3297, body="Why.")},
                files={(AB, 3297): ["a.py"]})
    doc = leg(rows, gh)
    gl.fold(rows, [doc], repo_map=REPO_MAP)
    for r in rows:
        [pr] = r["matched_work"]["cards"][0]["prs"]
        assert pr["read"] is True and pr["body"] == "Why."
        assert pr["files"] == ["a.py"]
    # The no-file row's lookups block is as cards wrote it.
    assert rows[0]["lookups"] == MAIN_NO_FILE
    # The looked-up row was folded as always.
    assert rows[1]["lookups"]["ok"] is True


def test_fold_keeps_the_one_sanitize_pass_owner_made():
    # owner fenced and cut the title and body; fold writes them as read,
    # so a sentinel line carries exactly one `[defanged] ` after fold.
    begin = "===== BEGIN UNTRUSTED CARD TEXT ====="
    body = f"intro\n{begin}\nx\n" + "b" * 2000
    rows = [with_matched(no_file("DRE-1"), [(AB, 3297)])]
    gh = PullGh(pulls={(AB, 3297): gh_pull(3297, title=begin, body=body)},
                files={(AB, 3297): ["a.py"]})
    doc = leg(rows, gh)
    read = doc["matched_prs"][f"{AB}#3297"]
    gl.fold(rows, [doc], repo_map=REPO_MAP)
    [pr] = rows[0]["matched_work"]["cards"][0]["prs"]
    assert pr["body"] == read["body"]
    assert pr["body"].count("[defanged] ") == 1
    assert len(pr["body"]) == gl.PR_BODY_LIMIT
    assert pr["title"] == read["title"] == f"[defanged] {begin}"


def test_fold_names_a_pr_whose_repo_is_not_in_the_map():
    other = "dreadnought-foundry/retired-repo"
    rows = [with_matched(no_file("DRE-1"), [(other, 4)])]
    doc = leg(rows, PullGh())
    gl.fold(rows, [doc], repo_map=REPO_MAP)
    [pr] = rows[0]["matched_work"]["cards"][0]["prs"]
    assert pr["read"] is False
    assert pr["why"] == f"{other} is not in the repo map"


def test_fold_names_the_owner_that_did_not_read():
    rows = [with_matched(no_file("DRE-1"), [(AB, 4)])]
    why = "no token for dreadnought-foundry: the App's installation could not be minted"
    doc = {"owner": OWNER, "read": False, "why": why, "requests": 0,
           "budget": None, "seconds": 0.0, "cards": {}}
    gl.fold(rows, [doc], repo_map=REPO_MAP)
    [pr] = rows[0]["matched_work"]["cards"][0]["prs"]
    assert (pr["read"], pr["why"]) == (False, why)


def test_fold_leaves_rows_with_no_matched_work_byte_identical():
    rows = [no_file("DRE-1"), looked("DRE-2")]
    rows[1]["lookups"]["owners"] = {}
    excluded = target("DRE-3", "", excluded="hand-built")
    excluded["lookups"] = None
    rows.append(excluded)
    before = json.loads(json.dumps(rows))
    docs = [{"owner": OWNER, "read": True, "why": None, "requests": 1,
             "budget": 600, "seconds": 1.0,
             "cards": {"DRE-2": {"read": True, "why": None, "merged_prs": [],
                                 "cut": [], "unread_repos": {}}}}]
    gl.fold(rows, docs, repo_map=REPO_MAP)
    plain = json.loads(json.dumps(before))
    gl.fold(plain, docs, repo_map=REPO_MAP)
    assert rows == plain
    assert rows[0] == before[0] and rows[2] == before[2]
    assert "matched_work" not in rows[0]


def test_fold_main_writes_the_matched_prs_through_the_files(tmp_path):
    rows = [with_matched(no_file("DRE-1"), [(AB, 7)])]
    doc = leg(rows, PullGh(pulls={(AB, 7): gh_pull(7)}, files={(AB, 7): []}))
    lookups = tmp_path / "lookups" / "groom-lookups-df"
    lookups.mkdir(parents=True)
    (lookups / "lookups-df.json").write_text(json.dumps(doc), encoding="utf-8")
    targets = tmp_path / "targets.json"
    targets.write_text(json.dumps(rows), encoding="utf-8")
    map_file = tmp_path / "map.json"
    map_file.write_text(json.dumps(REPO_MAP), encoding="utf-8")
    out = tmp_path / "out.json"
    assert gl.main(["fold", "--targets", str(targets), "--lookups-dir",
                    str(tmp_path / "lookups"), "--out", str(out),
                    "--repo-map", str(map_file)]) == 0
    [got] = json.loads(out.read_text(encoding="utf-8"))
    assert got["matched_work"]["cards"][0]["prs"][0]["read"] is True
