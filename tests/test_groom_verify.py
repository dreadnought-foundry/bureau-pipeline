"""RED-first: a deterministic check reads every proposed card against the
evidence the groomer never looked at, before the proposal is posted (DRE-4966).

On 2026-09-26 the proposal put DRE-2897 on the Planning list. The evidence
that it was finished sat in three places the groomer did not read: a
2026-09-05 comment on the card saying it was superseded and not to be built
from, PR #431, and DRE-3230's description. The same proposal offered to cancel
DRE-3526 as superseded by DRE-4630, which was still in Intake and unapproved.

What this file holds:

  * a comment declaring the card superseded, done or not to be built moves it
    to the Cancel list with the comment quoted, and the next spare card takes
    its slot;
  * a merged pull request that is FOR the card moves it too, citing the PR —
    and only a closing line, an `agent/DRE-N-…` branch or the card's own pull
    request attachment makes it so; a mention never does (DRE-5857);
  * a card that says it supersedes this one, and is Done or approved and in
    flight, moves it too;
  * a Cancel that names a replacement stands only when the replacement is
    real — Done, a merged PR, or approved and in flight — and is otherwise
    rejected, the card kept on the Planning list with the reason;
  * a source the check could not read is said on the proposal as unread, and
    no card is marked clean on it;
  * a card the CEO excluded on ANY earlier proposal on the standing card is
    not proposed again until `groom-added` brings it back;
  * `groom-held` is Hold: the drain treats it exactly as `groom-excluded` for
    the proposal it names, and a later proposal may offer the card again but
    ranks it after every card that was never held.

Run: cd bureau-pipeline && python3 -m pytest tests/test_groom_verify.py -v
"""
from __future__ import annotations

import argparse
import copy
import importlib
import json
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import groom_context  # noqa: E402
import groom_verify  # noqa: E402
import groomer  # noqa: E402
import lane_contract  # noqa: E402

from test_groomer import CYCLES, NOW, card  # noqa: E402
from test_groomer_approval_gate import FakeOps as GateOps  # noqa: E402

PROPOSAL_CARD = "DRE-2683"
OWNERS = ["dreadnought-foundry"]
PR_431 = "https://github.com/dreadnought-foundry/portico/pull/431"

#: Fifteen cards, DRE-101 the newest: the rules put them in the order
#: DRE-101, DRE-102, … and a capacity of three makes the Planning list
#: DRE-101..103 with DRE-104 onward as the spares.
LANE = [card(f"DRE-{100 + n}", days=n) for n in range(1, 16)]


# --------------------------------------------------------------------------
# the two readers the check spends, faked
# --------------------------------------------------------------------------
def comment(body, at="2026-09-05T23:48:45.000Z"):
    return {"body": body, "createdAt": at}


def other(identifier, state, description="", title=None):
    return {"identifier": identifier, "title": title or f"{identifier} work",
            "description": description, "state": {"name": state}}


class FakeLinear:
    """Linear as the check reads it: one query per checked card (its
    comments, its relations, its siblings and the cards a search finds), and
    one per replacement a Cancel names."""

    def __init__(self, details=None, lanes=None, fail=()):
        self.details = dict(details or {})
        self.lanes = dict(lanes or {})
        self.fail = set(fail)
        self.asked: list[str] = []

    def gql(self, query, variables=None):
        ident = (variables or {}).get("id")
        self.asked.append(ident)
        if ident in self.fail:
            raise RuntimeError(f"linear answered 500 for {ident}")
        if "comments(" in query:
            d = self.details.get(ident, {})
            return {"issue": {
                "identifier": ident, "state": {"name": "Intake"},
                "comments": {"nodes": d.get("comments", [])},
                "relations": {"nodes": [
                    {"type": "related", "relatedIssue": r}
                    for r in d.get("related", [])]},
                "inverseRelations": {"nodes": []},
                "attachments": {"nodes": [
                    {"url": u} for u in d.get("attachments", [])]},
                "parent": ({"identifier": "DRE-999", "children": {
                    "nodes": d["siblings"]}} if d.get("siblings") else None),
            }, "searchIssues": {"nodes": d.get("search", [])}}
        if ident not in self.lanes:
            return {"issue": None}
        return {"issue": {"identifier": ident,
                          "state": {"name": self.lanes[ident]}}}


def pr(number, *, title="a change", body="", repo="portico"):
    return {"title": title, "body": body,
            "html_url": f"https://github.com/dreadnought-foundry/{repo}/pull/{number}",
            "repository_url": f"https://api.github.com/repos/dreadnought-foundry/{repo}",
            "pull_request": {"merged_at": "2026-09-06T04:23:00Z"}}


class FakeGh:
    """`gh api`, as `groom_context._gh_json` runs it: the installation, the
    merged-PR search and a single pull request — whose head branch is the
    one thing a search result does not carry (`heads`, by URL)."""

    def __init__(self, prs=(), *, fail_search=False, merged=None, heads=None,
                 fail_pulls=False):
        self.prs = list(prs)
        self.fail_search = fail_search
        self.merged = dict(merged or {})
        self.heads = dict(heads or {})
        self.fail_pulls = fail_pulls
        self.queries: list[str] = []
        self.pulls: list[str] = []

    def __call__(self, args):
        joined = " ".join(args)
        if "installation/repositories" in joined:
            return json.dumps({"total_count": 1, "repositories": [
                {"full_name": f"{o}/portico"} for o in OWNERS]})
        if "search/issues" in joined:
            self.queries.append(joined)
            if self.fail_search:
                raise groom_context.ContextError(
                    "gh api search/issues failed rc=1: HTTP 403 rate limited")
            return json.dumps({"total_count": len(self.prs),
                               "items": self.prs})
        found = re.search(r"repos/(\S+?)/pulls/(\d+)", joined)
        if found:
            url = f"https://github.com/{found.group(1)}/pull/{found.group(2)}"
            self.pulls.append(url)
            if self.fail_pulls:
                raise groom_context.ContextError(
                    f"gh api {joined} failed rc=1: HTTP 502")
            return json.dumps({"merged_at": self.merged.get(url),
                               "head": {"ref": self.heads.get(url, "main")}})
        raise AssertionError(f"unexpected gh call: {joined}")


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "app-installation-token")


def checked(cards=LANE, *, linear=None, gh=None, capacity=3, **extra):
    return groomer.verify_proposal(
        cards, dict(cycles=CYCLES, capacity=capacity, now=NOW, **extra),
        lops=linear or FakeLinear(), run=gh or FakeGh(), owners=OWNERS)


def planning(proposal):
    return [r["identifier"] for r in sorted(proposal["outcomes"]["now"],
                                            key=lambda r: r["position"])]


def cancel(proposal):
    return sorted(proposal["outcomes"]["dead"], key=lambda r: r["position"])


def record(proposal, identifier):
    rows = [r for r in proposal["verification"]["cards"]
            if r["identifier"] == identifier]
    assert rows, f"the check wrote no record for {identifier}"
    return rows[0]


# --------------------------------------------------------------------------
# comments
# --------------------------------------------------------------------------
SUPERSEDED = ("**Superseded, 2026-09-05.** The CEO decided this on another "
              "card, and that card now carries the work. Do not build from "
              "this card.")


def test_a_comment_declaring_the_card_superseded_moves_it_to_cancel_quoted():
    """The DRE-2897 morning: the comment was on the card for three weeks and
    the Planning list carried it anyway."""
    linear = FakeLinear({"DRE-102": {"comments": [comment(SUPERSEDED)]}})
    got = checked(linear=linear)

    assert [r["identifier"] for r in cancel(got)] == ["DRE-102"]
    reason = cancel(got)[0]["reason"]
    assert "Superseded, 2026-09-05." in reason
    assert "Do not build from this card." in reason
    # …and the next spare takes its slot: the Planning list is still three.
    assert planning(got) == ["DRE-101", "DRE-103", "DRE-104"]

    row = record(got, "DRE-102")
    assert row["verdict"] == "cancel"
    assert row["source"] == "comments"
    assert "Do not build from this card." in row["evidence"][0]["text"]


def test_the_quoted_reason_is_the_line_the_drain_writes_on_the_card():
    """The Cancel row round-trips through the posted comment: the reason the
    drain reads back is the quote, whole."""
    linear = FakeLinear({"DRE-102": {"comments": [comment(SUPERSEDED)]}})
    got = checked(linear=linear)
    parsed = groomer.parse_proposal_comment(groomer.proposal_comment(got))
    assert [r["identifier"] for r in parsed["cancel"]] == ["DRE-102"]
    assert "Do not build from this card." in parsed["cancel"][0]["reason"]


@pytest.mark.parametrize("body", [
    "Nothing left to build: this defect was already fixed and shipped.",
    "This card is already done — the export landed last week.",
    "Not to be built; the work moved elsewhere.",
])
def test_a_comment_saying_done_or_not_to_be_built_counts(body):
    linear = FakeLinear({"DRE-101": {"comments": [comment(body)]}})
    assert [r["identifier"] for r in cancel(checked(linear=linear))] == ["DRE-101"]


@pytest.mark.parametrize("body", [
    "The old export path was superseded, and this card builds the new one.",
    "Do not build this until DRE-5 is merged.",
    "Is this already done? Checking.",
])
def test_a_comment_that_only_mentions_the_words_is_not_evidence(body):
    linear = FakeLinear({"DRE-101": {"comments": [comment(body)]}})
    got = checked(linear=linear)
    assert cancel(got) == []
    assert record(got, "DRE-101")["verdict"] == "clean"


def test_a_comment_naming_an_unapproved_replacement_is_not_evidence():
    """The replacement rule holds for the check's own evidence too: "superseded
    by X" where X is still in Intake names nothing that replaced the card."""
    linear = FakeLinear(
        {"DRE-101": {"comments": [comment("Superseded by DRE-900.")]}},
        lanes={"DRE-900": "Intake"})
    got = checked(linear=linear)
    assert cancel(got) == []
    assert "DRE-101" in planning(got)


# --------------------------------------------------------------------------
# merged pull requests
# --------------------------------------------------------------------------
def test_a_merged_pr_whose_body_closes_the_card_moves_it_citing_the_pr():
    gh = FakeGh([pr(431, title="Clear the overwritten answer's status",
                    body="Closes DRE-103.\n\nWhat it does …")])
    got = checked(gh=gh)
    assert [r["identifier"] for r in cancel(got)] == ["DRE-103"]
    assert PR_431 in cancel(got)[0]["reason"]
    assert planning(got) == ["DRE-101", "DRE-102", "DRE-104"]
    row = record(got, "DRE-103")
    assert (row["verdict"], row["source"]) == ("cancel", "merged_prs")
    assert row["evidence"][0]["url"] == PR_431


@pytest.mark.parametrize("line", [
    "Closes DRE-101.", "Fixes DRE-101", "Resolves DRE-101", "closed DRE-101",
    "fixed: DRE-101", "**Resolves:** DRE-101", "This resolved DRE-101 today."])
def test_every_closing_keyword_makes_the_pr_for_the_card(line):
    gh = FakeGh([pr(431, body=f"Some words.\n\n{line}\n")])
    assert [r["identifier"] for r in cancel(checked(gh=gh))] == ["DRE-101"]


def test_a_merged_pr_on_the_cards_agent_branch_counts():
    gh = FakeGh([pr(431, title="the export clears the status",
                    body="Why: DRE-101 asked for it.")],
                heads={PR_431: "agent/DRE-101-export-clears-status"})
    got = checked(gh=gh)
    assert [r["identifier"] for r in cancel(got)] == ["DRE-101"]
    assert gh.pulls == [PR_431]


def test_a_merged_pr_the_card_carries_as_its_own_attachment_counts():
    linear = FakeLinear({"DRE-102": {"attachments": [PR_431]}})
    gh = FakeGh([pr(431, body="Part of the work DRE-102 describes.")])
    got = checked(linear=linear, gh=gh)
    assert [r["identifier"] for r in cancel(got)] == ["DRE-102"]
    assert record(got, "DRE-102")["evidence"][0]["url"] == PR_431


def test_a_title_that_names_the_card_is_a_mention_and_not_evidence():
    """A title names whatever the change is about; the branch, a closing
    line or the card's own attachment says what it is FOR."""
    gh = FakeGh([pr(431, title="feat(DRE-4966): stop proposing DRE-101")],
                heads={PR_431: "agent/DRE-4966-the-check"})
    assert cancel(checked(gh=gh)) == []


def test_a_linear_link_in_the_body_is_a_mention_and_not_evidence():
    gh = FakeGh([pr(431, body="Found by https://linear.app/dreadnoughtfoundry/"
                              "issue/DRE-101/the-proof\n\nCloses DRE-4966.")],
                heads={PR_431: "agent/DRE-4966-the-check"})
    assert cancel(checked(gh=gh)) == []


@pytest.mark.parametrize("line", [
    "The sibling card DRE-101 covers who gets asked at all.",
    "Card: DRE-101", "Implements the half DRE-101 left open.",
    "Follow-up: DRE-101", "Blocked on DRE-101."])
def test_a_bare_mention_never_makes_the_pr_for_the_card(line):
    gh = FakeGh([pr(431, body=f"Closes DRE-4966.\n\n{line}\n")],
                heads={PR_431: "agent/DRE-4966-the-check"})
    assert cancel(checked(gh=gh)) == []


def test_a_branch_for_a_longer_identifier_is_not_this_card():
    gh = FakeGh([pr(431, body="Why: DRE-101.")],
                heads={PR_431: "agent/DRE-1012-something-else"})
    assert cancel(checked(gh=gh)) == []


def test_a_pr_naming_a_longer_identifier_is_not_this_card():
    gh = FakeGh([pr(431, body="Closes DRE-1012.")])
    assert cancel(checked(gh=gh)) == []


def test_a_pr_that_only_mentions_the_card_in_passing_is_not_evidence():
    """A PR body lists the cards it read, blocked on or was motivated by —
    DRE-4966's own PR names four it does not deliver. Only a PR that is FOR
    the card counts: a closing line names it, its branch is the card's, or
    the card carries it as its own attachment."""
    gh = FakeGh([pr(500, title="feat(DRE-4966): the check",
                    body="Why: the proposal offered DRE-102 on 2026-09-26.")])
    got = checked(gh=gh)
    assert cancel(got) == []


def test_a_pr_whose_branch_could_not_be_read_leaves_the_card_unread():
    """The branch is one of the three ways in, so a pull request whose
    branch could not be read is a source not read — never a clean card."""
    gh = FakeGh([pr(431, body="Why: DRE-101.")], fail_pulls=True)
    got = checked(gh=gh)
    assert cancel(got) == []
    row = record(got, "DRE-101")
    assert (row["verdict"], row["unread"]) == ("unread", ["merged_prs"])


def test_a_pr_already_proven_for_the_card_is_not_read_again_for_its_branch():
    gh = FakeGh([pr(431, body="Closes DRE-101.")])
    groom_verify.merged_mentions(["DRE-101"], run=gh, owners=OWNERS)
    assert gh.pulls == []


# --------------------------------------------------------------------------
# DRE-5857: bureau-pipeline #415 closes DRE-4058 and only names DRE-4059
# --------------------------------------------------------------------------
PR_415 = json.loads((ROOT / "tests" / "fixtures"
                     / "groom_verify_pr415_mention.json").read_text())


def _item_415():
    p = PR_415["pr"]
    return {"title": p["title"], "body": p["body"], "html_url": p["html_url"],
            "repository_url": p["repository_url"],
            "pull_request": {"merged_at": p["merged_at"]}}


def test_415_is_for_the_card_it_closes_and_not_the_sibling_it_names():
    item = _item_415()
    assert "sibling card DRE-4059 covers" in item["body"]
    branch = PR_415["pr"]["head_ref"]
    assert groom_verify._pr_is_for("DRE-4058", item)
    assert groom_verify._pr_is_for("DRE-4058", item, branch=branch)
    assert not groom_verify._pr_is_for("DRE-4059", item)
    assert not groom_verify._pr_is_for(
        "DRE-4059", item, branch=branch,
        attached=set(PR_415["attachments"]["DRE-4059"]))


def test_415_without_its_closing_line_is_still_for_DRE_4058_by_branch_and_attachment():
    item = dict(_item_415(), body="The sibling card DRE-4059 covers it.")
    assert groom_verify._pr_is_for("DRE-4058", item,
                                   branch=PR_415["pr"]["head_ref"])
    assert groom_verify._pr_is_for(
        "DRE-4058", item, attached=set(PR_415["attachments"]["DRE-4058"]))
    assert not groom_verify._pr_is_for("DRE-4058", item)


def test_the_10_05_morning_replayed_keeps_DRE_4059_off_the_cancel_list():
    """Proposal d50db9746997: the same thirty cards, the same pull request,
    the same attachments. DRE-4059 stays on the Planning list, read clean."""
    order = PR_415["planning"] + PR_415["spares"]
    cards = [card(i, repo="bureau-pipeline", days=n + 1)
             for n, i in enumerate(order)]
    p = PR_415["pr"]
    linear = FakeLinear({i: {"attachments": urls}
                         for i, urls in PR_415["attachments"].items()})
    gh = FakeGh([_item_415()], heads={p["html_url"]: p["head_ref"]})
    got = checked(cards, linear=linear, gh=gh,
                  capacity=len(PR_415["planning"]))
    assert "DRE-4059" not in [r["identifier"] for r in cancel(got)]
    assert cancel(got) == []
    assert planning(got) == PR_415["planning"]
    row = record(got, "DRE-4059")
    assert (row["verdict"], row["evidence"]) == ("clean", [])
    assert got["verification"]["moved_to_cancel"] == []


def test_the_search_is_quoted_scoped_to_merged_prs_and_per_owner():
    gh = FakeGh()
    checked(gh=gh)
    assert gh.queries, "no merged-PR search was made"
    assert all("is:pr" in q and "is:merged" in q and "user:dreadnought-foundry"
               in q for q in gh.queries)
    assert any('"DRE-101"' in q for q in gh.queries)


# --------------------------------------------------------------------------
# other cards
# --------------------------------------------------------------------------
def test_a_done_card_that_says_it_supersedes_this_one_moves_it():
    """DRE-3230's description: "Supersedes the parentless DRE-2897"."""
    linear = FakeLinear({"DRE-102": {"search": [other(
        "DRE-3230", "Done",
        "Supersedes the parentless DRE-102, whose probe is the reproduction.")]}})
    got = checked(linear=linear)
    assert [r["identifier"] for r in cancel(got)] == ["DRE-102"]
    assert "DRE-3230" in cancel(got)[0]["reason"]
    assert record(got, "DRE-102")["source"] == "other_cards"


def test_a_card_in_intake_that_says_it_supersedes_this_one_is_not_evidence():
    linear = FakeLinear({"DRE-102": {"search": [other(
        "DRE-4630", "Intake", "Supersedes DRE-102.")]}})
    assert cancel(checked(linear=linear)) == []


def test_a_related_done_card_that_merely_names_it_is_not_evidence():
    """An investigation card is Done and lists the follow-ups it filed. That
    names them; it does not cover them."""
    linear = FakeLinear({"DRE-102": {"related": [other(
        "DRE-2895", "Done", "Three findings, filed as DRE-102 and DRE-103.")]}})
    assert cancel(checked(linear=linear)) == []


def test_a_done_sibling_that_covers_it_moves_it():
    linear = FakeLinear({"DRE-102": {"siblings": [
        other("DRE-102", "Intake"),
        other("DRE-3230", "Done", "This card covers DRE-102 as well.")]}})
    assert [r["identifier"] for r in cancel(checked(linear=linear))] == ["DRE-102"]


# --------------------------------------------------------------------------
# the reason names the other card and quotes the sentence (DRE-5305)
# --------------------------------------------------------------------------
#: "Supersedes DRE-102." inside a longer paragraph, with a sentence on either
#: side of it — the reason quotes the one sentence, never the paragraph.
PARAGRAPH = ("The export moved to the new reader last week. Supersedes "
             "DRE-102. The old path stays until the console stops calling it.")


def _other_cards_item(proposal, identifier="DRE-102"):
    items = [e for e in record(proposal, identifier)["evidence"]
             if e["source"] == "other_cards"]
    assert items, f"no other_cards evidence for {identifier}"
    return items[0]


def test_a_done_sibling_superseding_it_is_named_and_its_sentence_quoted():
    """The 2026-09-29 morning: the proposal asked to cancel DRE-4633 on a
    match nobody could check from the page, because the line said only that
    the other card "says it supersedes or covers this card"."""
    linear = FakeLinear({"DRE-102": {"siblings": [
        other("DRE-102", "Intake"), other("DRE-3230", "Done", PARAGRAPH)]}})
    got = checked(linear=linear)
    item = _other_cards_item(got)
    assert item == {
        "source": "other_cards", "card": "DRE-3230",
        "quote": "Supersedes DRE-102.",
        "text": 'DRE-3230 (Done) says: "Supersedes DRE-102."'}
    assert cancel(got)[0]["reason"] == item["text"]


@pytest.mark.parametrize("where", ["search", "related", "siblings"])
def test_every_route_to_another_card_carries_the_same_shape(where):
    """`searchIssues`, a `related` relation and a sibling under the same
    parent are three ways to the same evidence, and the page and the drain
    rely on one shape for all of them."""
    linear = FakeLinear({"DRE-102": {where: [
        other("DRE-3230", "In Progress", PARAGRAPH)]}})
    item = _other_cards_item(checked(linear=linear))
    assert item == {
        "source": "other_cards", "card": "DRE-3230",
        "quote": "Supersedes DRE-102.",
        "text": 'DRE-3230 (approved and in In Progress) says: '
                '"Supersedes DRE-102."'}


def test_the_sentence_runs_to_a_newline_and_drops_the_line_lead():
    """A description line is a sentence too: the quote stops at the line's
    end, and the list marker in front of it is not part of what it says."""
    linear = FakeLinear({"DRE-102": {"search": [other(
        "DRE-3230", "Done",
        "## Scope\n- Absorbs DRE-102 and its probe\n- Ships the reader")]}})
    item = _other_cards_item(checked(linear=linear))
    assert item["quote"] == "Absorbs DRE-102 and its probe"


def test_a_long_sentence_is_cut_to_the_quote_length():
    sentence = "Supersedes DRE-102 " + "and the rest of the reader " * 20
    linear = FakeLinear({"DRE-102": {"search": [other(
        "DRE-3230", "Done", sentence)]}})
    item = _other_cards_item(checked(linear=linear))
    assert len(item["quote"]) == groom_verify.QUOTE_CHARS
    assert item["quote"].startswith("Supersedes DRE-102 and the rest")
    assert item["quote"].endswith("…")


@pytest.mark.parametrize("sentence", [
    "Supersedes DRE-102 by moving the read into scripts/groom_verify.py.",
    "Supersedes DRE-102 — `render_proposal()` now draws the table.",
])
def test_a_quoted_sentence_carrying_code_is_replaced_like_a_comment(sentence):
    """The same guard a comment quote passes through: the CEO reads a sentence
    saying where the words are, and the record keeps them."""
    linear = FakeLinear({"DRE-102": {"search": [other(
        "DRE-3230", "Done", sentence)]}})
    got = checked(linear=linear)
    item = _other_cards_item(got)
    assert item["quote"] == sentence
    assert item["text"] == ("DRE-3230 (Done) says it supersedes or covers "
                            "this card; its words are in the proposal record")
    assert groom_verify.planning_escalation.refusal(item["text"]) is None
    assert sentence not in groomer.render_proposal(got)


def test_the_cancel_table_reason_names_the_card_and_quotes_the_sentence():
    linear = FakeLinear({"DRE-102": {"search": [other(
        "DRE-3230", "Done", PARAGRAPH)]}})
    page = groomer.render_proposal(checked(linear=linear))
    rows = [line for line in page.splitlines()
            if line.startswith("|") and "| DRE-102 |" in line]
    assert rows, "the Cancel table has no row for DRE-102"
    reason = rows[0].rstrip(" |").rsplit(" | ", 1)[-1]
    assert reason == 'DRE-3230 (Done) says: "Supersedes DRE-102."'


# --------------------------------------------------------------------------
# a Cancel needs a real replacement
# --------------------------------------------------------------------------
def _superseded(identifier, target, days):
    return card(identifier, days=days, description=f"Superseded by: {target}")


def _lane_with_cancel(target):
    return [LANE[0], _superseded("DRE-102", target, 2), *LANE[2:]]


def test_a_cancel_superseded_by_an_unapproved_card_is_rejected_and_kept():
    """The DRE-3526 morning: the replacement was still in Intake."""
    linear = FakeLinear(lanes={"DRE-900": "Intake"})
    got = checked(_lane_with_cancel("DRE-900"), linear=linear)
    assert cancel(got) == []
    assert "DRE-102" in planning(got)
    row = next(r for r in got["outcomes"]["now"] if r["identifier"] == "DRE-102")
    assert "DRE-900" in row["reason"] and "Intake" in row["reason"]
    assert record(got, "DRE-102")["verdict"] == "cancel-rejected"


def test_a_cancel_superseded_by_a_done_card_stands():
    linear = FakeLinear(lanes={"DRE-900": "Done"})
    got = checked(_lane_with_cancel("DRE-900"), linear=linear)
    assert [r["identifier"] for r in cancel(got)] == ["DRE-102"]
    assert record(got, "DRE-102")["verdict"] == "cancel-stands"


@pytest.mark.parametrize("lane", ["Backlog", "Todo", "In Progress", "In Review"])
def test_a_cancel_superseded_by_an_approved_card_in_flight_stands(lane):
    linear = FakeLinear(lanes={"DRE-900": lane})
    got = checked(_lane_with_cancel("DRE-900"), linear=linear)
    assert [r["identifier"] for r in cancel(got)] == ["DRE-102"]


# --------------------------------------------------------------------------
# the in-flight lanes are read off the lane contract (DRE-5348)
# --------------------------------------------------------------------------
def _contract_with(**statuses):
    """The committed contract with the named lanes' `status` replaced."""
    doc = copy.deepcopy(lane_contract.load())
    for entry in doc["lanes"]:
        if entry["name"] in statuses:
            entry["status"] = statuses[entry["name"]]
    return doc


@pytest.fixture
def reload_against(monkeypatch, tmp_path):
    """Re-import groom_verify with the lane contract at another path, and put
    the committed one back afterwards."""
    def _reload(doc):
        path = tmp_path / "lane-contract.json"
        path.write_text(json.dumps(doc), encoding="utf-8")
        monkeypatch.setattr(lane_contract, "CONTRACT_PATH", str(path))
        return importlib.reload(groom_verify)
    yield _reload
    monkeypatch.undo()
    importlib.reload(groom_verify)


def test_in_flight_on_the_committed_contract_is_the_five_lanes_in_flow_order():
    # Hand-work went live with DRE-5320, and joined with nothing here edited.
    assert lane_contract.lane("Hand-work")
    assert groom_verify.IN_FLIGHT == (
        "Backlog", "Todo", "Hand-work", "In Progress", "In Review")


def test_a_live_hand_work_lane_joins_in_flight_in_flow_order(reload_against):
    fresh = reload_against(_contract_with(**{"Hand-work": "live"}))
    assert fresh.IN_FLIGHT == (
        "Backlog", "Todo", "Hand-work", "In Progress", "In Review")
    assert fresh.lane_says("DRE-1", "Hand-work") == (
        True, "DRE-1 is approved and in Hand-work")


def test_while_hand_work_is_arriving_a_card_there_is_not_approved(reload_against):
    doc = _contract_with(**{"Hand-work": "arriving"})
    entry = next(e for e in doc["lanes"] if e["name"] == "Hand-work")
    entry.update(arriving_by="DRE-5240", reason="arriving fixture",
                 board_action="create the state")
    fresh = reload_against(doc)
    assert fresh.IN_FLIGHT == ("Backlog", "Todo", "In Progress", "In Review")
    assert fresh.lane_says("DRE-1", "Hand-work") == (
        False, "DRE-1 is in Hand-work and has not been approved")


def test_a_contract_whose_only_work_lane_is_done_has_nothing_in_flight():
    doc = copy.deepcopy(lane_contract.load())
    doc["lanes"] = [entry for entry in doc["lanes"]
                    if entry["segment"] != "work" or entry["name"] == "Done"]
    assert groom_verify.in_flight(doc) == ()


def test_a_live_hand_work_card_replacing_a_cancel_lets_it_stand(reload_against):
    reload_against(_contract_with(**{"Hand-work": "live"}))
    linear = FakeLinear(lanes={"DRE-900": "Hand-work"})
    got = checked(_lane_with_cancel("DRE-900"), linear=linear)
    assert [r["identifier"] for r in cancel(got)] == ["DRE-102"]
    assert record(got, "DRE-102")["verdict"] == "cancel-stands"


def test_a_cancel_superseded_by_a_merged_pr_stands_and_an_open_one_does_not():
    stands = checked(_lane_with_cancel(PR_431),
                     gh=FakeGh(merged={PR_431: "2026-09-06T04:23:00Z"}))
    assert [r["identifier"] for r in cancel(stands)] == ["DRE-102"]
    rejected = checked(_lane_with_cancel(PR_431), gh=FakeGh())
    assert cancel(rejected) == []
    assert "DRE-102" in planning(rejected)


def test_a_replacement_the_check_could_not_read_does_not_stand():
    """Cancel is the direction that is hard to undo, so a replacement nobody
    could read keeps the card on the Planning list — and the reason says it was
    not read, never that it was checked."""
    linear = FakeLinear(fail={"DRE-900"})
    got = checked(_lane_with_cancel("DRE-900"), linear=linear)
    assert cancel(got) == []
    row = next(r for r in got["outcomes"]["now"] if r["identifier"] == "DRE-102")
    assert "could not be read" in row["reason"]


# --------------------------------------------------------------------------
# a source that was not read is said, never passed off as clean
# --------------------------------------------------------------------------
def test_a_failed_merged_pr_search_is_said_unread_and_marks_nothing_clean():
    got = checked(gh=FakeGh(fail_search=True))
    unread = got["verification"]["unread"]
    assert "merged_prs" in unread
    checked_ids = [r["identifier"] for r in got["verification"]["cards"]
                   if r["verdict"] not in ("cancel-stands", "cancel-rejected",
                                           "cancel-kept")]
    assert sorted(unread["merged_prs"]["cards"]) == sorted(checked_ids)
    for row in got["verification"]["cards"]:
        assert row["verdict"] != "clean", row
        assert "merged_prs" in row["unread"]
    page = groomer.render_proposal(got)
    assert "merged pull requests" in page
    assert "could not be read" in page
    for identifier in planning(got):
        assert identifier in page.split("## What the check read")[1]


def test_no_token_is_unread_too(monkeypatch):
    monkeypatch.delenv("GH_TOKEN", raising=False)
    got = checked()
    assert "merged_prs" in got["verification"]["unread"]
    assert all(r["verdict"] != "clean" for r in got["verification"]["cards"])


def test_a_card_linear_would_not_answer_for_is_unread_on_both_linear_sources():
    got = checked(linear=FakeLinear(fail={"DRE-101"}))
    row = record(got, "DRE-101")
    assert row["verdict"] == "unread"
    assert set(row["unread"]) >= {"comments", "other_cards"}


def test_evidence_from_a_source_that_was_read_still_counts():
    """The search failed; the comment was still read, and still says so."""
    linear = FakeLinear({"DRE-102": {"comments": [comment(SUPERSEDED)]}})
    got = checked(linear=linear, gh=FakeGh(fail_search=True))
    assert [r["identifier"] for r in cancel(got)] == ["DRE-102"]


def test_a_check_that_fails_outright_posts_everything_unread(monkeypatch):
    """A bug in the check must not cost the CEO the morning's proposal: it
    posts as it would have unchecked, with every card unread on every source
    and nothing marked clean."""
    def boom(*a, **k):
        raise KeyError("identifier")

    monkeypatch.setattr(groom_verify, "check", boom)
    got = checked()
    plain = groomer.propose(LANE, cycles=CYCLES, capacity=3, now=NOW)
    assert planning(got) == planning(plain) and got["id"] == plain["id"]
    assert all(r["verdict"] == "unread" for r in got["verification"]["cards"])
    assert set(got["verification"]["unread"]) == set(groom_verify.SOURCES)
    assert "could not be read" in groomer.render_proposal(got)


def test_a_clean_read_says_clean():
    got = checked()
    assert all(r["verdict"] == "clean" for r in got["verification"]["cards"])
    assert got["verification"]["unread"] == {}


# --------------------------------------------------------------------------
# the window, and what it does not reach
# --------------------------------------------------------------------------
def test_the_check_reads_the_morning_set_and_ten_spares_in_proposal_order():
    linear = FakeLinear()
    checked(linear=linear)
    assert linear.asked == [f"DRE-{n}" for n in range(101, 114)]


def test_a_spare_with_evidence_that_no_slot_reaches_is_not_cancelled_early():
    """A card outside the morning's set waits its turn (DRE-4727) — the check
    names what it found and cancels nothing it did not need to reach."""
    linear = FakeLinear({"DRE-110": {"comments": [comment(SUPERSEDED)]}})
    got = checked(linear=linear)
    assert cancel(got) == []
    assert record(got, "DRE-110")["verdict"] == "cancel"
    assert record(got, "DRE-110")["acted"] is False


def test_a_planning_card_the_check_never_reached_is_named_not_checked():
    """Every spare fails: the Planning list is filled from past the window,
    and those cards are said to be unchecked rather than clean."""
    bad = {f"DRE-{n}": {"comments": [comment(SUPERSEDED)]}
           for n in range(101, 114)}
    got = checked(linear=FakeLinear(bad))
    assert got["verification"]["not_checked"] == planning(got)
    assert planning(got) == ["DRE-114", "DRE-115"]


def test_the_id_covers_the_checked_lists():
    plain = groomer.propose(LANE, cycles=CYCLES, capacity=3, now=NOW)
    linear = FakeLinear({"DRE-102": {"comments": [comment(SUPERSEDED)]}})
    got = checked(linear=linear)
    assert got["id"] != plain["id"]
    assert got["id"] == groomer.proposal_id(got)
    assert got["id"] in groomer.proposal_comment(got).splitlines()[0]


def test_the_check_makes_no_model_call(monkeypatch):
    import groom_judgement

    def refuse(*a, **k):
        raise AssertionError("the check called the model")

    monkeypatch.setattr(groom_judgement, "run", refuse)
    checked(linear=FakeLinear({"DRE-102": {"comments": [comment(SUPERSEDED)]}}))


# --------------------------------------------------------------------------
# the hook — `_build` checks before anything is posted
# --------------------------------------------------------------------------
def _args(**extra):
    args = dict(lane="Intake", capacity=3, batch_cycles=1, judgement=False,
                window_days=groomer.WINDOW_DAYS, keep_answer=None,
                post=None, hold_repo=[], verify=False,
                priority=",".join(groomer.REPO_PRIORITY))
    args.update(extra)
    return argparse.Namespace(**args)


def _patch_build(monkeypatch, cards, thread=None):
    ops = GateOps(list(thread or []))
    monkeypatch.setattr(groomer, "_now", lambda: NOW)
    monkeypatch.setattr(groomer, "read_population", lambda lops, l: list(cards))
    monkeypatch.setattr(groomer, "read_cycles", lambda lops: CYCLES)
    monkeypatch.setattr(groomer.linear_ops, "comment_records",
                        ops.comment_records)
    return ops


def test_build_runs_the_check_when_asked(monkeypatch):
    _patch_build(monkeypatch, LANE)
    linear = FakeLinear({"DRE-102": {"comments": [comment(SUPERSEDED)]}})
    visible = sorted(set(groom_context.fleet_owners()) | set(OWNERS))

    def gh(args):
        if "installation/repositories" in " ".join(args):
            return json.dumps({"total_count": len(visible), "repositories": [
                {"full_name": f"{o}/x"} for o in visible]})
        return FakeGh()(args)

    monkeypatch.setattr(groomer.linear_ops, "gql", linear.gql)
    monkeypatch.setattr(groom_context, "_gh_json", gh)
    built = groomer._build(_args(verify=True))
    assert [r["identifier"] for r in cancel(built)] == ["DRE-102"]
    assert "verification" in built
    assert built["id"] == groomer.proposal_id(built)


def test_build_without_the_check_writes_no_record(monkeypatch):
    _patch_build(monkeypatch, LANE)
    assert "verification" not in groomer._build(_args())


def test_the_cli_runs_the_check_by_default_and_documents_the_off_switch(capsys):
    with pytest.raises(SystemExit):
        groomer.main(["propose", "--help"])
    assert "--no-verify" in capsys.readouterr().out
    parser = argparse.ArgumentParser()
    groomer._shaping(parser)
    assert parser.parse_args([]).verify is True
    assert parser.parse_args(["--no-verify"]).verify is False


# --------------------------------------------------------------------------
# past exclusions — Don't do
# --------------------------------------------------------------------------
def _posted(proposal):
    return {"body": groomer.proposal_comment(proposal),
            "authored_by_pipeline": True}


def _marker(tag, proposal, identifier=None, *, by_pipeline=False):
    return {"body": groomer.decision_comment(tag, proposal["id"],
                                             card=identifier),
            "authored_by_pipeline": by_pipeline}


def _everywhere(proposal):
    return [r["identifier"] for r in proposal["sequence"]]


def test_a_card_excluded_on_an_earlier_proposal_is_not_proposed_again(monkeypatch):
    _patch_build(monkeypatch, LANE)
    first = groomer._build(_args(post=PROPOSAL_CARD))
    assert "DRE-102" in planning(first)

    thread = [_posted(first),
              _marker(groomer.EXCLUDE_TAG, first, "DRE-102")]
    _patch_build(monkeypatch, LANE, thread)
    second = groomer._build(_args(post=PROPOSAL_CARD))
    assert "DRE-102" not in _everywhere(second)
    assert planning(second) == ["DRE-101", "DRE-103", "DRE-104"]
    assert second["excluded_before"] == ["DRE-102"]
    assert "DRE-102" in groomer.render_proposal(second)

    # …until the CEO brings it back.
    thread += [_posted(second),
               _marker(groomer.ADD_TAG, second, "DRE-102")]
    _patch_build(monkeypatch, LANE, thread)
    third = groomer._build(_args(post=PROPOSAL_CARD))
    assert "DRE-102" in _everywhere(third)
    assert planning(third) == ["DRE-101", "DRE-102", "DRE-103"]


def test_an_exclusion_the_pipeline_wrote_excludes_nothing(monkeypatch):
    _patch_build(monkeypatch, LANE)
    first = groomer._build(_args(post=PROPOSAL_CARD))
    thread = [_posted(first), _marker(groomer.EXCLUDE_TAG, first, "DRE-102",
                                      by_pipeline=True)]
    _patch_build(monkeypatch, LANE, thread)
    assert "DRE-102" in planning(groomer._build(_args(post=PROPOSAL_CARD)))


def test_the_standing_reading_takes_the_newest_marker_per_card():
    p = {"id": "abc123abc123"}
    records = [_marker(groomer.EXCLUDE_TAG, p, "DRE-1"),
               _marker(groomer.HOLD_TAG, p, "DRE-2"),
               _marker(groomer.ADD_TAG, p, "DRE-1"),
               _marker(groomer.EXCLUDE_TAG, p, "DRE-3"),
               _marker(groomer.HOLD_TAG, p, "DRE-3")]
    # DRE-1 was excluded and then added back; DRE-3 was excluded and then
    # held, which is the CEO's newer word.
    assert groomer.standing_decisions(records) == {
        "excluded": [], "held": ["DRE-2", "DRE-3"]}


def test_the_standing_reading_spans_every_proposal_on_the_card():
    records = [_marker(groomer.EXCLUDE_TAG, {"id": "aaaaaaaaaaaa"}, "DRE-1"),
               _marker(groomer.EXCLUDE_TAG, {"id": "bbbbbbbbbbbb"}, "DRE-2")]
    assert groomer.standing_decisions(records)["excluded"] == ["DRE-1", "DRE-2"]


# --------------------------------------------------------------------------
# Hold — `groom-held`
# --------------------------------------------------------------------------
def test_the_hold_marker_is_the_console_contract_character_for_character():
    assert groomer.HOLD_TAG == "groom-held"
    assert groomer.HOLD_TAG in groomer.DECISION_TAGS
    assert groomer.decision_match(
        groomer.HOLD_TAG, "🧺 groom-held: 0123456789ab DRE-7")


def test_a_held_card_is_left_in_intake_and_ranked_last_next_time(monkeypatch):
    _patch_build(monkeypatch, LANE)
    first = groomer._build(_args(post=PROPOSAL_CARD))
    assert planning(first) == ["DRE-101", "DRE-102", "DRE-103"]

    thread = [_posted(first),
              {"body": groomer.approval_comment(first["id"]),
               "authored_by_pipeline": False},
              _marker(groomer.HOLD_TAG, first, "DRE-101")]
    ops = GateOps(thread)
    drained = groomer.drain(ops, card=PROPOSAL_CARD)
    assert "DRE-101" not in [cid for cid, _ in ops.state_writes]
    assert drained["held_back"] == ["DRE-101"]

    # The next morning: the two that moved have left Intake; the held card
    # has not, and may be offered — behind every card that was never held.
    rest = [c for c in LANE if c["identifier"] not in ("DRE-102", "DRE-103")]
    thread.append({"body": groomer.drained_record(drained),
                   "authored_by_pipeline": True})
    _patch_build(monkeypatch, rest, thread)
    second = groomer._build(_args(post=PROPOSAL_CARD, capacity=20))
    order = _everywhere(second)
    assert order[-1] == "DRE-101"
    assert "DRE-101" in planning(second)
    assert planning(second)[-1] == "DRE-101"
    assert second["held_before"] == ["DRE-101"]
    row = next(r for r in second["outcomes"]["now"]
               if r["identifier"] == "DRE-101")
    assert "held" in row["reason"]


def test_a_held_card_ranks_behind_an_older_card_that_was_never_held():
    """Newest first would put DRE-101 at the top; the hold puts it behind the
    oldest card in the pile — and when capacity runs out first, it waits."""
    got = groomer.propose(LANE, cycles=CYCLES, capacity=3, now=NOW,
                          held_cards=["DRE-101"])
    assert "DRE-101" not in planning(got)
    assert _everywhere(got)[-1] == "DRE-101"


def test_a_held_urgent_card_still_ranks_behind_every_unheld_card():
    lane = [card("DRE-101", days=1, priority=groomer.URGENT), *LANE[1:]]
    got = groomer.propose(lane, cycles=CYCLES, capacity=20, now=NOW,
                          held_cards=["DRE-101"])
    assert _everywhere(got)[-1] == "DRE-101"


def test_no_hold_and_no_exclusion_changes_nothing():
    plain = groomer.propose(LANE, cycles=CYCLES, capacity=3, now=NOW)
    same = groomer.propose(LANE, cycles=CYCLES, capacity=3, now=NOW,
                           excluded=(), held_cards=())
    assert plain == same
    assert "excluded_before" not in plain and "held_before" not in plain


@pytest.mark.parametrize("on_list", ["planning", "cancel"])
def test_the_drain_treats_held_exactly_as_excluded(on_list):
    lane = [LANE[0], _superseded("DRE-102", "DRE-900", 2), *LANE[2:]]
    proposal = groomer.propose(lane, cycles=CYCLES, capacity=3, now=NOW)
    target = "DRE-101" if on_list == "planning" else "DRE-102"
    assert target in [r["identifier"] for r in
                      proposal["outcomes"]["now" if on_list == "planning"
                                           else "dead"]]

    def run(tag):
        ops = GateOps([_posted(proposal),
                       {"body": groomer.approval_comment(proposal["id"]),
                        "authored_by_pipeline": False},
                       _marker(tag, proposal, target)])
        result = groomer.drain(ops, card=PROPOSAL_CARD)
        return ops, result

    held_ops, held = run(groomer.HOLD_TAG)
    excl_ops, excl = run(groomer.EXCLUDE_TAG)
    assert target not in [cid for cid, _ in held_ops.state_writes]
    assert held_ops.state_writes == excl_ops.state_writes
    assert held["held_back"] == excl["held_back"] == [target]
    held_row = next(r for r in held["rows"] if r["identifier"] == target)
    excl_row = next(r for r in excl["rows"] if r["identifier"] == target)
    assert held_row["outcome"] == excl_row["outcome"] == "held back"
    assert groomer.HOLD_TAG in held_row["why"]
    # No comment lands on the held card either — nothing is written to it.
    assert not [c for c, _ in held_ops.written if c == target]


def test_a_hold_naming_another_batch_holds_nothing_back_at_drain():
    proposal = groomer.propose(LANE, cycles=CYCLES, capacity=3, now=NOW)
    ops = GateOps([_posted(proposal),
                   {"body": groomer.approval_comment(proposal["id"]),
                    "authored_by_pipeline": False},
                   _marker(groomer.HOLD_TAG, {"id": "fedcba987654"}, "DRE-101")])
    result = groomer.drain(ops, card=PROPOSAL_CARD)
    assert "DRE-101" in result["moved"]


# --------------------------------------------------------------------------
# DRE-5317 — which owners the merged-PR search actually ran against
# --------------------------------------------------------------------------
THREE_OWNERS = ["DeltaSolv", "EveryBite", "dreadnought-foundry"]


class OwnersGh(FakeGh):
    """`gh api` whose installation sees only `visible`, and whose search of
    each owner in `failing` raises."""

    def __init__(self, visible, *, failing=(), installation_fails=False):
        super().__init__()
        self.visible = list(visible)
        self.failing = set(failing)
        self.installation_fails = installation_fails

    def __call__(self, args):
        joined = " ".join(args)
        if "installation/repositories" in joined:
            if self.installation_fails:
                raise groom_context.ContextError(
                    "gh api installation/repositories failed rc=1: HTTP 401")
            return json.dumps({"total_count": len(self.visible), "repositories": [
                {"full_name": f"{o}/repo"} for o in self.visible]})
        if "search/issues" in joined:
            self.queries.append(joined)
            if any(f"user:{o}" in joined for o in self.failing):
                raise groom_context.ContextError(
                    "gh api search/issues failed rc=1: HTTP 403 rate limited")
            return json.dumps({"total_count": 0, "items": []})
        raise AssertionError(f"unexpected gh call: {joined}")


IDS = [f"DRE-{100 + n}" for n in range(1, 8)]


def test_merged_mentions_names_the_owners_whose_search_answered():
    gh = OwnersGh(["dreadnought-foundry", "EveryBite"])
    found, gaps, searched = groom_verify.merged_mentions(
        IDS, run=gh, owners=THREE_OWNERS)
    assert searched == ["EveryBite", "dreadnought-foundry"]
    assert "DeltaSolv" not in searched
    # the blind owner is still the per-card gap it always was
    assert all("DeltaSolv" in gaps[i] for i in IDS)


def test_merged_mentions_with_no_token_searched_no_owner(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "")
    _, gaps, searched = groom_verify.merged_mentions(
        IDS, run=OwnersGh(THREE_OWNERS), owners=THREE_OWNERS)
    assert searched == []
    assert set(gaps) == set(IDS)


def test_merged_mentions_whose_installation_read_raises_searched_no_owner():
    """2026-09-29: the installation read failed and the morning posted."""
    gh = OwnersGh(THREE_OWNERS, installation_fails=True)
    _, gaps, searched = groom_verify.merged_mentions(
        IDS, run=gh, owners=THREE_OWNERS)
    assert searched == []
    assert gh.queries == []
    assert all("installation could not be read" in gaps[i] for i in IDS)


def test_merged_mentions_whose_every_search_raises_searched_no_owner():
    gh = OwnersGh(THREE_OWNERS, failing=THREE_OWNERS)
    _, gaps, searched = groom_verify.merged_mentions(
        IDS, run=gh, owners=THREE_OWNERS)
    assert gh.queries, "the searches were never attempted"
    assert searched == []
    assert set(gaps) == set(IDS)


def test_check_carries_the_searched_owners_and_settle_writes_them():
    got = checked()
    assert got["verification"]["merged_prs_searched"] == OWNERS
    first = groomer.propose(LANE, cycles=CYCLES, capacity=3, now=NOW)
    result = groom_verify.check(first, lops=FakeLinear(), run=FakeGh(),
                                owners=OWNERS)
    assert result["merged_prs_searched"] == OWNERS
    blind = groom_verify.check(
        first, lops=FakeLinear(),
        run=OwnersGh(OWNERS, installation_fails=True), owners=OWNERS)
    assert blind["merged_prs_searched"] == []
    assert groom_verify.settle(first, blind)["verification"][
        "merged_prs_searched"] == []


def test_a_check_that_failed_outright_searched_nowhere_and_says_none(monkeypatch):
    """A check that did not run is not a token that could see nothing."""
    first = groomer.propose(LANE, cycles=CYCLES, capacity=3, now=NOW)
    result = groom_verify.unread_result(first, "the check failed this run")
    assert result["merged_prs_searched"] is None

    def boom(*a, **k):
        raise KeyError("identifier")

    monkeypatch.setattr(groom_verify, "check", boom)
    got = checked()
    assert "merged_prs_searched" in got["verification"]
    assert got["verification"]["merged_prs_searched"] is None
