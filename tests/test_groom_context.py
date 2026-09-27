"""The groomer's context pack — what is in flight, bounded and stated (DRE-3150).

The groomer sequences Intake against nothing. It has never read what the
company is already doing, so "is this worth doing now" is answered from the
card's own text and the clock. The pack is the other half of that question:
the epics in progress and what each one said it would do, the initiatives and
their current objectives, what actually merged in the last fortnight, and what
was closed or cancelled in the last month with the reason where one was given.

Two properties this file pins, and both are about the pack's edges:

  * **It is a PURE builder.** Rows in, pack out — no Linear key, no `gh`, no
    clock of its own. The readers that fetch those rows are a thin seam above
    it, so the judgement this feeds can be tested without a network and
    without a model.
  * **A pack over its cap is truncated NEWEST-FIRST and SAYS SO.** A prompt
    that silently drops the oldest half of a fortnight's merges tells the model
    less than it thinks it is telling it, and the model has no way to know. The
    truncation is recorded in the pack and written into the rendered prompt.

Run: cd bureau-pipeline && python3 -m pytest tests/test_groom_context.py -v
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import groom_context  # noqa: E402

NOW = "2026-09-05T12:00:00Z"
BASE = datetime.fromisoformat(NOW.replace("Z", "+00:00"))


def ago(days: float) -> str:
    return (BASE - timedelta(days=days)).isoformat().replace("+00:00", "Z")


def epic(identifier, *, plan="", title=None, comments=None):
    row = {"identifier": identifier, "title": title or f"[EPIC] {identifier}",
           "state": "In Progress"}
    if plan:
        row["plan"] = plan
    if comments is not None:
        row["comments"] = comments
    return row


def pr(number, *, days=1, repo="portico", card="DRE-1", title=None):
    return {"title": title or f"feat({card}): thing {number}",
            "url": f"https://github.com/dreadnought-foundry/{repo}/pull/{number}",
            "repository": {"nameWithOwner": f"dreadnought-foundry/{repo}"},
            "mergedAt": ago(days)}


def closed(identifier, *, days=1, state="Done", description=""):
    return {"identifier": identifier, "title": f"{identifier} did a thing",
            "state": {"name": state}, "description": description,
            "completedAt": ago(days)}


class StubLops:
    """Just enough of `linear_ops` for `read_pack`: the Linear sources answer,
    so a run against it has exactly one broken source — `gh`."""

    def gql_paged(self, query, variables=None):
        if "completedAt" in query:
            return [closed("DRE-9")]
        return [{"identifier": "DRE-90", "title": "[EPIC] DRE-90",
                 "description": "Ship the console.",
                 "children": {"nodes": [{"id": "child"}]}}]

    def gql(self, query, variables=None):
        return {"initiatives": {"nodes": [{"name": "Console",
                                           "description": "Ship the pilot."}]}}

    def comment_bodies(self, identifier):
        return []


def failing_gh(args):
    """What `_gh_json` does when `gh search prs` exits non-zero — the failure
    run 34183475867 actually hit."""
    raise groom_context.ContextError(
        f"gh {' '.join(args)} failed rc=1: HTTP 503")


# --------------------------------------------------------------------------
# the builder is pure
# --------------------------------------------------------------------------
def test_the_pack_builds_from_rows_alone():
    got = groom_context.pack(
        epics=[epic("DRE-200", plan="We are rebuilding the console.\n\nDetail.")],
        initiatives=[{"name": "portico", "description": "Ship the pilot.\nMore."}],
        merged_prs=[pr(1)],
        closed_cards=[closed("DRE-9")],
        now=NOW,
    )
    assert got["epics_in_progress"][0]["plan"] == "We are rebuilding the console."
    assert got["initiatives"][0]["objective"] == "Ship the pilot."
    assert got["merged_prs"][0]["card"] == "DRE-1"
    assert got["merged_prs"][0]["repo"] == "portico"
    assert got["closed_cards"][0]["identifier"] == "DRE-9"


def test_every_section_the_proposal_names_is_a_key_of_the_pack():
    """The proposal's `judgement.pack` block names these four and nothing else
    (DRE-3150's contract). A section renamed here without the summary moving
    with it is a block the sibling cards read as zero."""
    got = groom_context.pack(now=NOW)
    for name in groom_context.SECTIONS:
        assert name in got, f"the pack has no {name!r} section"
    assert set(groom_context.summary(got)) == set(groom_context.SECTIONS) | {
        "truncated", "unread"
    }


# --------------------------------------------------------------------------
# the windows
# --------------------------------------------------------------------------
def test_a_merge_older_than_the_window_is_not_in_the_pack():
    got = groom_context.pack(
        merged_prs=[pr(1, days=2), pr(2, days=groom_context.MERGED_PR_DAYS + 3)],
        now=NOW,
    )
    urls = [row["url"] for row in got["merged_prs"]]
    assert any(u.endswith("/1") for u in urls)
    assert not any(u.endswith("/2") for u in urls), (
        "the pack is the last 14 days of merges; an older one is history"
    )


def test_a_closed_card_older_than_the_window_is_not_in_the_pack():
    got = groom_context.pack(
        closed_cards=[closed("DRE-1", days=5),
                      closed("DRE-2", days=groom_context.CLOSED_CARD_DAYS + 5)],
        now=NOW,
    )
    assert [c["identifier"] for c in got["closed_cards"]] == ["DRE-1"]


def test_the_two_windows_are_the_ones_the_card_states():
    assert groom_context.MERGED_PR_DAYS == 14
    assert groom_context.CLOSED_CARD_DAYS == 30


# --------------------------------------------------------------------------
# the cap — truncated newest-first, and never silently
# --------------------------------------------------------------------------
def test_an_over_cap_section_keeps_the_newest_and_records_the_cut():
    cap = groom_context.CAPS["merged_prs"]
    rows = [pr(n, days=n * 0.01) for n in range(cap + 25)]     # 0 is the newest
    got = groom_context.pack(merged_prs=rows, now=NOW)
    assert len(got["merged_prs"]) == cap
    kept = {row["url"].rsplit("/", 1)[-1] for row in got["merged_prs"]}
    assert "0" in kept, "the newest merge was dropped"
    assert str(cap + 24) not in kept, "the oldest merge survived the cut"
    assert got["truncated"]["merged_prs"] == {"kept": cap, "of": cap + 25}


def test_a_truncated_pack_says_so_in_the_prompt():
    cap = groom_context.CAPS["closed_cards"]
    rows = [closed(f"DRE-{n}", days=n * 0.01) for n in range(cap + 4)]
    got = groom_context.pack(closed_cards=rows, now=NOW)
    rendered = groom_context.render(got)
    assert str(cap + 4) in rendered and str(cap) in rendered, (
        "the prompt must state the truncation: a model given the newest 60 of "
        "64 rows with no note reads them as all of them"
    )
    assert "newest" in rendered.lower()


def test_an_untruncated_pack_records_no_truncation():
    got = groom_context.pack(merged_prs=[pr(1)], now=NOW)
    assert got["truncated"] == {}
    assert groom_context.summary(got)["truncated"] == []


# --------------------------------------------------------------------------
# what each row carries
# --------------------------------------------------------------------------
def test_the_plan_paragraph_is_the_first_one_only():
    got = groom_context.pack(
        epics=[epic("DRE-1", plan="# Plan\n\nFirst para, two lines.\nStill it.\n\n"
                                  "Second para nobody asked for.")],
        now=NOW,
    )
    plan = got["epics_in_progress"][0]["plan"]
    assert plan == "First para, two lines. Still it."
    assert "Second para" not in plan


def test_the_plan_comment_is_read_past_the_machine_markers():
    """An epic's thread opens with receipts — heartbeats, actor markers,
    routing verdicts. The plan is the first comment that is prose."""
    got = groom_context.pack(
        epics=[epic("DRE-1", comments=["⏳ 1/5 spec read, plan formed",
                                       "🧭 routing-verdict: FLEET",
                                       "We will cut the console into four cards."])],
        now=NOW,
    )
    assert got["epics_in_progress"][0]["plan"] == (
        "We will cut the console into four cards."
    )


def test_a_closed_card_carries_its_reason_line_where_one_exists():
    got = groom_context.pack(
        closed_cards=[
            closed("DRE-1", description="Superseded by: DRE-2"),
            closed("DRE-2", description="Reason: the customer withdrew it"),
            closed("DRE-3", description="Just an ordinary body."),
        ],
        now=NOW,
    )
    by_id = {row["identifier"]: row for row in got["closed_cards"]}
    assert by_id["DRE-1"]["reason"] == "DRE-2"
    assert by_id["DRE-2"]["reason"] == "the customer withdrew it"
    assert by_id["DRE-3"]["reason"] is None, (
        "a reason nobody wrote is None, never a guess"
    )


def test_a_closed_card_says_which_terminal_state_it_reached():
    got = groom_context.pack(
        closed_cards=[closed("DRE-1", state="Done"),
                      closed("DRE-2", state="Canceled")],
        now=NOW,
    )
    assert {r["identifier"]: r["state"] for r in got["closed_cards"]} == {
        "DRE-1": "Done", "DRE-2": "Canceled",
    }


def test_the_pack_renders_without_a_section_it_could_not_read():
    """Unknown is shown as unknown (standards/console-honesty.md rule 2). A
    source that could not be read is named in the prompt rather than rendered
    as an empty list the model reads as 'nothing is in flight'."""
    got = groom_context.pack(now=NOW, unread=["merged_prs"])
    rendered = groom_context.render(got)
    assert "merged_prs" in got["unread"]
    assert "could not be read" in rendered


def test_a_section_that_could_not_be_read_has_no_count_in_the_summary():
    """`summary()` is what the proposal's receipt line reports, and a section
    nobody could read has NO count — the number it would otherwise default to
    is `0`, and "0 merged PRs" on a night the fleet merged several is
    indistinguishable from a real answer (DRE-3329).
    """
    got = groom_context.pack(now=NOW, closed_cards=[], unread=["merged_prs"])
    out = groom_context.summary(got)
    assert out["merged_prs"] is None, (
        "an unreadable section must carry no count at all"
    )
    assert out["unread"] == ["merged_prs"]
    assert out["closed_cards"] == 0, (
        "a section that WAS read and held nothing is a real zero"
    )


def test_a_failing_gh_search_leaves_the_merges_unread_and_uncounted():
    """End to end from the failure the card names: `gh search prs` exits
    non-zero, the section is named unread, and nothing downstream is handed a
    number for it."""
    got = groom_context.read_pack(StubLops(), now=NOW, run=failing_gh)
    assert got["unread"] == ["merged_prs"], "the other three sources read fine"
    assert groom_context.summary(got)["merged_prs"] is None
    assert "could not be read" in groom_context.render(got)
    assert groom_context.summary(got)["closed_cards"] == 1


def test_a_non_zero_gh_exit_is_raised_rather_than_read_as_no_merges():
    class Done:
        returncode, stdout, stderr = 1, "", "HTTP 503"

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("GH_TOKEN", "app-token")
        mp.setattr(groom_context.subprocess, "run", lambda *a, **k: Done())
        with pytest.raises(groom_context.ContextError):
            groom_context.read_merged_prs(now=NOW)


# --------------------------------------------------------------------------
# the merged-PR read (DRE-4964): the App token, the real merge date, every
# page, and an owner the token cannot read named rather than counted as zero
# --------------------------------------------------------------------------
FLEET_OWNERS = sorted({repo.split("/")[0] for repo in json.loads(
    (ROOT / "config" / "repo-map.json").read_text()).values()})
HOME = "dreadnought-foundry"


def item(number, *, merged_days, created_days=None, owner=HOME, repo="portico",
         card="DRE-1"):
    """One result of the search API, in the shape `search/issues` serves it.
    `pull_request.merged_at` is the merge date; `created_at` is when the PR
    was OPENED, and the two differ for every PR that lived longer than a day."""
    created = ago(merged_days if created_days is None else created_days)
    return {
        "number": number,
        "title": f"feat({card}): thing {number}",
        "html_url": f"https://github.com/{owner}/{repo}/pull/{number}",
        "repository_url": f"https://api.github.com/repos/{owner}/{repo}",
        "created_at": created,
        "updated_at": ago(merged_days),
        "pull_request": {"merged_at": ago(merged_days)},
    }


class FakeGitHub:
    """The two REST reads `read_merged_prs` makes, answered from fixtures.

    `installed` is the owners the App token's installation can see (the
    `/installation/repositories` read); `pages` is each owner's search results,
    page by page; `totals` is the search's own `total_count` per owner, which
    is allowed to be larger than the rows it will ever hand back."""

    def __init__(self, *, installed=(HOME,), pages=None, totals=None,
                 failing=None):
        self.installed = list(installed)
        self.pages = pages or {}
        self.totals = totals or {}
        self.failing = failing or {}
        self.calls = []

    @staticmethod
    def _field(args, name):
        for i, arg in enumerate(args):
            if arg in ("-f", "-F", "--raw-field", "--field") and \
                    args[i + 1].startswith(f"{name}="):
                return args[i + 1].split("=", 1)[1]
        return None

    def __call__(self, args):
        self.calls.append(list(args))
        joined = " ".join(args)
        if "installation/repositories" in joined:
            repos = [{"full_name": f"{owner}/repo"} for owner in self.installed]
            return json.dumps({"total_count": len(repos), "repositories": repos})
        assert "search/issues" in joined, f"an unexpected gh call: {args}"
        query = self._field(args, "q") or ""
        assert "is:pr" in query and "is:merged" in query, query
        owner = next(o for o in FLEET_OWNERS + [HOME] if f"user:{o}" in query)
        if owner in self.failing:
            raise groom_context.ContextError(self.failing[owner])
        pages = self.pages.get(owner) or [[]]
        page = int(self._field(args, "page") or 1)
        items = pages[page - 1] if page <= len(pages) else []
        total = self.totals.get(owner, sum(len(p) for p in pages))
        return json.dumps({"total_count": total, "incomplete_results": False,
                           "items": items})

    def searched(self):
        return [c for c in self.calls if "search/issues" in " ".join(c)]


@pytest.fixture
def token(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "app-token")


def test_a_pr_is_dated_by_its_merge_not_by_when_it_was_opened(token):
    """A PR opened 30 days ago and merged yesterday shipped YESTERDAY. Dated
    by `createdAt` it sorts a month back, and past the 14-day window it is not
    in the pack at all — the card it did the work of looks unstarted."""
    gh = FakeGitHub(pages={HOME: [[item(7, created_days=30, merged_days=1)]]})
    read = groom_context.read_merged_prs(now=NOW, run=gh)
    rows = read["rows"]
    assert [r["url"] for r in rows] == [f"https://github.com/{HOME}/portico/pull/7"]
    assert rows[0]["merged_at"] == ago(1), "the row carries the real merge date"

    got = groom_context.pack(merged_prs=rows, now=NOW)
    assert [r["merged_at"] for r in got["merged_prs"]] == [ago(1)], (
        "a long-lived PR merged yesterday fell out of the fortnight's merges"
    )


def test_the_newest_rows_are_the_newest_by_real_merge_date(token):
    """The 40 the model reads are the 40 newest MERGES. Here opening order is
    the reverse of merging order, so a cut on `createdAt` keeps exactly the
    wrong ones."""
    cap = groom_context.CAPS["merged_prs"]
    n = cap + 10
    items = [item(i, merged_days=i * 0.1, created_days=13 - i * 0.1)
             for i in range(n)]                     # 0 merged newest, opened last
    gh = FakeGitHub(pages={HOME: [items]})
    got = groom_context.pack(
        merged_prs=groom_context.read_merged_prs(now=NOW, run=gh)["rows"],
        now=NOW)
    kept = {int(r["url"].rsplit("/", 1)[-1]) for r in got["merged_prs"]}
    assert kept == set(range(cap))


def test_every_page_is_read_and_the_count_is_the_searchs_total(token):
    """1,337 merged in a fortnight; one page is 100 rows and the pack keeps 40.
    Both pages are read, and the number the proposal reports is the search's
    own `total_count` — not the 40 kept, and not the rows fetched."""
    first = [item(i, merged_days=0.01 * i) for i in range(100)]
    second = [item(100 + i, merged_days=1 + 0.01 * i) for i in range(20)]
    gh = FakeGitHub(pages={HOME: [first, second]}, totals={HOME: 1337})
    read = groom_context.read_merged_prs(now=NOW, run=gh, owners=[HOME])

    urls = {r["url"] for r in read["rows"]}
    assert f"https://github.com/{HOME}/portico/pull/0" in urls
    assert f"https://github.com/{HOME}/portico/pull/119" in urls, (
        "page two was never read"
    )
    assert len(read["rows"]) == 120
    assert {self_page(c) for c in gh.searched()} >= {"1", "2"}
    assert read["total_count"] == 1337

    got = groom_context.pack(merged_prs=read["rows"],
                             counts={"merged_prs": read["total_count"]}, now=NOW)
    cap = groom_context.CAPS["merged_prs"]
    assert len(got["merged_prs"]) == cap
    assert groom_context.summary(got)["merged_prs"] == 1337, (
        "the reported count must be the search's total_count, not the rows kept"
    )
    assert got["truncated"]["merged_prs"] == {"kept": cap, "of": 1337}
    assert "newest 40 of 1337" in groom_context.render(got)


def self_page(call):
    return FakeGitHub._field(call, "page")


def test_read_pack_reports_the_searchs_total_end_to_end(token):
    gh = FakeGitHub(pages={HOME: [[item(i, merged_days=1) for i in range(3)]]},
                    totals={HOME: 1337})
    got = groom_context.read_pack(StubLops(), now=NOW, run=gh)
    assert "merged_prs" not in got["unread"]
    assert groom_context.summary(got)["merged_prs"] == 1337


def test_the_search_stops_at_the_last_page():
    """No page past the one that came back short — the reader follows every
    page there IS, and does not spend the search quota on empty ones."""
    gh = FakeGitHub(pages={HOME: [[item(i, merged_days=1) for i in range(100)],
                                  [item(100, merged_days=2)]]})
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("GH_TOKEN", "app-token")
        groom_context.read_merged_prs(now=NOW, run=gh, owners=[HOME])
    assert [self_page(c) for c in gh.searched()] == ["1", "2"]


def test_an_owner_the_token_cannot_read_is_named_and_never_counted_as_zero(token):
    """The owners come from `config/repo-map.json`. The App token reads the
    ones its installation covers; every other owner is named unread — its
    private repositories would answer the search with a confident, empty 0
    (DRE-3329's rule, one level down) — while the readable owner's rows still
    arrive."""
    assert len(FLEET_OWNERS) > 1, "the fixture needs a fleet of several owners"
    gh = FakeGitHub(installed=[HOME],
                    pages={HOME: [[item(1, merged_days=1), item(2, merged_days=2)]]},
                    totals={HOME: 2})
    got = groom_context.read_pack(StubLops(), now=NOW, run=gh)

    others = [o for o in FLEET_OWNERS if o != HOME]
    assert sorted(got["unread_owners"]) == others
    assert [r["url"].rsplit("/", 1)[-1] for r in got["merged_prs"]] == ["1", "2"]
    assert "merged_prs" not in got["unread"], (
        "one owner read fine — the section is partly known, not unknown"
    )
    for owner in others:
        assert not any(f"user:{owner}" in " ".join(c) for c in gh.searched()), (
            f"{owner} was searched with a token that cannot see it"
        )

    out = groom_context.summary(got)
    assert out["merged_prs"] == 2, "the unread owners added nothing, not 0 each"
    for owner in others:
        assert f"merged_prs:{owner}" in out["unread"], (
            f"{owner} is missing from the proposal's unread list"
        )
    rendered = groom_context.render(got)
    for owner in others:
        assert owner in rendered, f"the prompt never names {owner} as unread"
    assert "unknown" in rendered.lower()


def test_an_owner_whose_search_fails_is_named_with_its_error(token):
    other = next(o for o in FLEET_OWNERS if o != HOME)
    gh = FakeGitHub(installed=FLEET_OWNERS,
                    pages={HOME: [[item(1, merged_days=1)]]},
                    failing={other: "gh api search/issues failed rc=1: HTTP 403"})
    read = groom_context.read_merged_prs(now=NOW, run=gh)
    assert "HTTP 403" in read["unread_owners"][other]
    assert [r["url"].rsplit("/", 1)[-1] for r in read["rows"]] == ["1"]


def test_no_owner_readable_is_the_whole_section_unread(token):
    """A token whose installation covers none of the fleet reads nothing, so
    the section is unknown — and the reason names every owner it could not
    read, rather than a search of each one answering 0."""
    gh = FakeGitHub(installed=["someone-else"])
    got = groom_context.read_pack(StubLops(), now=NOW, run=gh)
    assert got["unread"] == ["merged_prs"]
    assert groom_context.summary(got)["merged_prs"] is None
    assert gh.searched() == [], "an owner the token cannot see was searched"
    for owner in FLEET_OWNERS:
        assert owner in got["unread_reasons"]["merged_prs"]


def test_no_token_leaves_the_section_unread_with_the_error_named(monkeypatch):
    """The failure every run since 2026-09-15 hit: the Groom step set no
    `GH_TOKEN`, so `gh` exited rc=4. The section is unread, never zero, and
    the pack says WHY — the name of the missing variable, not a bare gap."""
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    gh = FakeGitHub(pages={HOME: [[item(1, merged_days=1)]]})
    got = groom_context.read_pack(StubLops(), now=NOW, run=gh)
    assert got["unread"] == ["merged_prs"]
    assert "GH_TOKEN" in got["unread_reasons"]["merged_prs"]
    assert groom_context.summary(got)["merged_prs"] is None
    assert "could not be read" in groom_context.render(got)
    assert gh.calls == [], "a read with no token was attempted anyway"
