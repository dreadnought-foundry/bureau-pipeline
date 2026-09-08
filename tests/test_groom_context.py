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
        mp.setattr(groom_context.subprocess, "run", lambda *a, **k: Done())
        with pytest.raises(groom_context.ContextError):
            groom_context.read_merged_prs(now=NOW)
