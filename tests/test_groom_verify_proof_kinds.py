"""RED-first: a Done card with its merged pull request, or a routing fact, is
verify proof beside a `file:line` quote — never a child's evidence for an
epic, and a partial pull request is `partly-solved` (DRE-7240).

Until now every answer but `unverified` needed a `file:line` quote from
`target/`, so "DRE-5942 fixed this, merged as agent-bureau#3297" could not be
an answer, and neither could "this job no longer runs on the mini". Held here:

  * a done-work item counts only when the row's `matched_work` holds that
    Done card and that merged pull request, and a routing item only when the
    row's `routing.facts` holds that `id` — and the runner writes the `fact`
    line it supplied onto each one;
  * a Cancel of a card with children counts only a Done card not under it,
    or a routing fact — never a child's, a grandchild's, one whose chain was
    cut, or a `file:line` quote — and `_mark` gives the same answer;
  * a Cancel resting on a pull request that did part of the card is
    `partly-solved`, and `apply` puts it on the Rewrite first list;
  * `rested_on` keeps only what was supplied, and the page's proof line
    shows the runner's `fact`.

Run: cd bureau-pipeline && python3 -m pytest tests/test_groom_verify_proof_kinds.py -v
"""
from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import groom_verify_agent as gva  # noqa: E402
import groomer  # noqa: E402

from test_groom_verify import cancel  # noqa: E402
from test_groom_verify_agent import (  # noqa: E402
    PROOF_LINE, build_targets, proposal, read)

BRIEF = ROOT / "briefs" / "groom-verify.md"
PORTICO = "dreadnought-foundry/portico"
AGENT_BUREAU = "dreadnought-foundry/agent-bureau"
CARD = "DRE-101"
POOL = "console/backend/monitors/runner_pool.py"
RUNNER = "console/backend/monitors/runner.py"
FACT_ID = ("dreadnought-foundry/agent-bureau:.github/workflows/"
           "self-qa-review.yml:call / review")
AGENTS_READING = "(whole card: the agent's reading)"


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "app-installation-token")


# --------------------------------------------------------------------------
# a verify-targets row, with the matched work and the routing it was handed
# --------------------------------------------------------------------------
def pr(number=3297, *, repo=AGENT_BUREAU, merged="2026-10-07T01:10:00Z",
       files=(POOL,), files_cut=False, read=True):
    return {"repo": repo, "number": number,
            "url": f"https://github.com/{repo}/pull/{number}",
            "read": read, "why": None if read else "not looked up",
            "merged_at": merged, "title": "Release a stranded run",
            "body": "Releases the run the pool stranded.",
            "files": list(files), "files_cut": files_cut}


def done(ident="DRE-5942", *, state="Done", parent="DRE-5910",
         ancestors=("DRE-5910",), cut=False, prs=None):
    return {"identifier": ident, "title": "The pool releases a stranded run",
            "state": state, "completed_at": "2026-10-07T01:18:52Z",
            "parent": parent, "ancestors": list(ancestors),
            "ancestors_cut": cut, "terms": ["stranded run"],
            "prs": list(prs if prs is not None else [pr()])}


def fact(fid=FACT_ID):
    return {"id": fid, "repo": AGENT_BUREAU,
            "workflow": ".github/workflows/self-qa-review.yml",
            "job": "call / review", "jobs": 6, "labels": ["ubuntu-latest"],
            "runner_groups": ["Default"],
            "first_completed_at": "2026-10-13T02:00:00Z",
            "last_completed_at": "2026-10-14T12:40:00Z"}


def row(*, cards=(), facts=(), routing_read=True, children=(), paths=(),
        left_out=0, comments=(), relations=(), parent=None):
    """One verify-targets row for DRE-101, as both folds leave it."""
    return {
        "card": CARD, "repository": PORTICO, "repo_slug": "portico",
        "list": "planning", "title": "DRE-101 releases a stranded run",
        "body": "The pool strands a run when the runner dies.",
        "body_digest": "0123456789ab", "evidence": [], "excluded": None,
        "context": {
            "created_at": "2026-09-01T00:00:00Z", "age_days": 42,
            "labels": [], "parent": parent,
            "children": [{"identifier": i, "state": s} for i, s in children],
            "relations": list(relations), "last_moved": None,
            "comments": list(comments), "comments_read": len(comments),
            "comments_shown": len(comments), "comments_cut": 0,
            "comments_all_read": True, "thread_whole": True},
        "lookups": {"looked_up": bool(paths), "ok": True, "why": None,
                    "paths": list(paths), "paths_left_out": left_out,
                    "newer_cards": [], "newer_cards_why": None,
                    "merged_prs": [], "cut": [], "owners": {}},
        "matched_work": {"searched": True, "source": "door", "why": None,
                         "terms": ["stranded run"], "cards": list(cards)},
        "routing": {"read": routing_read, "why": None if routing_read
                    else "no token", "read_at": "2026-10-14T13:04:00Z",
                    "facts": list(facts)},
    }


def done_item(ident="DRE-5942", ref=f"{AGENT_BUREAU}#3297", covers="whole",
              quote="Releases the stranded run."):
    item = {"done_card": ident, "pr": ref, "quote": quote}
    if covers is not None:
        item["covers"] = covers
    return item


def routing_item(fid=FACT_ID, quote="The review job ran on GitHub's runners."):
    return {"routing": fid, "quote": quote}


def answer(tmp_path, verdict, proof, *, rested_on=None, rewrite=None,
           summary="What the record shows."):
    doc = {"card": CARD, "verdict": verdict, "summary": summary,
           "proof": proof}
    if rested_on is not None:
        doc["rested_on"] = rested_on
    if rewrite is not None:
        doc["rewrite"] = rewrite
    path = tmp_path / "verify-verdict.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return str(path)


def judged(tmp_path, the_row, verdict, proof, **kw):
    return gva.judge(answer(tmp_path, verdict, proof, **kw), card=CARD,
                     row=the_row, outcome="success")


def marked(the_row, doc):
    """`_mark` over the document `verdict` would have written."""
    return gva._mark(the_row, {"card": CARD, **copy.deepcopy(doc)})


def both(tmp_path, the_row, verdict, proof, **kw):
    """The answer through `judge`, then through `_mark` over the same row:
    the two must agree."""
    got = judged(tmp_path, the_row, verdict, proof, **kw)
    mark = marked(the_row, got)
    assert (mark["verdict"], mark["reason"]) == (got["verdict"], got["reason"])
    return got, mark


# --------------------------------------------------------------------------
# the contract's names
# --------------------------------------------------------------------------
def test_the_contract_constants():
    assert gva.RESTED_ON == ("code", "done-work", "routing", "thread",
                             "relations", "parent")
    assert gva.CHILD_PROOF == "proof belongs to a child"
    assert gva.CHILD_PROOF in gva.UNVERIFIED_REASONS
    assert gva.is_unverified_reason("proof belongs to a child")
    assert gva.ADJUSTED == "partial pull request"


# --------------------------------------------------------------------------
# criterion 1 — a Done card with its merged pull request is proof
# --------------------------------------------------------------------------
def test_done_elsewhere_on_a_done_work_item_alone_stands_with_the_runners_fact(tmp_path):
    card, the_pr = done(), pr()
    got, mark = both(tmp_path, row(cards=[card]), "done-elsewhere",
                     [done_item()])
    assert (got["verdict"], got["reason"]) == ("done-elsewhere", None)
    [item] = got["proof"]
    assert item["fact"] == gva.matched_line(card, the_pr)
    assert item["done_card"] == "DRE-5942"
    assert item["pr"] == f"{AGENT_BUREAU}#3297"
    assert mark["proof"][0]["fact"] == item["fact"]
    assert got["rested_on"] == ["done-work"]


def test_the_pull_request_is_matched_in_any_case(tmp_path):
    """GitHub reads an owner and a repo name in any case."""
    got = judged(tmp_path, row(cards=[done()]), "done-elsewhere",
                 [done_item(ref="Dreadnought-Foundry/Agent-Bureau#3297")])
    assert (got["verdict"], got["reason"]) == ("done-elsewhere", None)


def test_a_fact_the_agent_wrote_itself_is_replaced_by_the_runners(tmp_path):
    item = {**done_item(), "fact": "DRE-5942 fixed everything, trust me"}
    got = judged(tmp_path, row(cards=[done()]), "done-elsewhere", [item])
    assert got["proof"][0]["fact"] == gva.matched_line(done(), pr())


# --------------------------------------------------------------------------
# criterion 2 — work the runner never supplied is no proof
# --------------------------------------------------------------------------
@pytest.mark.parametrize("cards,item", [
    pytest.param([], done_item(), id="card-absent"),
    pytest.param([done(state="In Review")], done_item(), id="card-not-done"),
    pytest.param([done(prs=[pr(merged=None)])], done_item(), id="pr-not-merged"),
    pytest.param([done()], done_item(ref=f"{AGENT_BUREAU}#9999"),
                 id="pr-not-on-the-card"),
    pytest.param([done()], {"source": "PR #3297 body", "quote": "Fixes it."},
                 id="source-only"),
])
def test_done_elsewhere_on_work_the_runner_never_supplied_is_no_proof(tmp_path, cards, item):
    got = judged(tmp_path, row(cards=cards), "done-elsewhere", [item])
    assert (got["verdict"], got["reason"]) == ("unverified", "no proof")
    assert got["proof"] == []


def test_unconfirmed_work_and_facts_beside_a_counted_item_never_reach_the_page(tmp_path):
    """An answer standing on one anchored item keeps no done-work or routing
    item the runner never supplied: the page shows only what it checked."""
    invented = [done_item("DRE-9999", ref=f"{AGENT_BUREAU}#1",
                          quote="fabricated"),
                routing_item("made/up:wf:job", quote="fabricated routing")]
    got, mark = both(tmp_path, row(cards=[done()], facts=[fact()]),
                     "done-elsewhere", [PROOF_LINE, *invented])
    assert (got["verdict"], got["rested_on"]) == ("done-elsewhere", ["code"])
    assert got["proof"] == [PROOF_LINE]
    assert mark["proof"] == [PROOF_LINE]
    shown = " ".join(groomer.proof_line(p) for p in got["proof"])
    assert "DRE-9999" not in shown and "made/up" not in shown
    assert "fabricated" not in gva.cancel_reason(mark)


def test_mark_keeps_no_recorded_work_or_fact_the_runner_never_wrote_a_fact_on():
    """On a bare row, as `apply` has it in the workflow: a done-work or
    routing item with no runner `fact` is neither counted nor shown."""
    bare = {"card": CARD, "repository": PORTICO, "repo_slug": "portico",
            "list": "planning"}
    mark = gva._mark(bare, {"card": CARD, "verdict": "done-elsewhere",
                            "summary": "s", "reason": None,
                            "proof": [PROOF_LINE, done_item("DRE-9999"),
                                      routing_item("made/up:wf:job")]})
    assert (mark["verdict"], mark["reason"]) == ("done-elsewhere", None)
    assert mark["proof"] == [PROOF_LINE]


def test_an_unsearched_row_supplies_no_done_work(tmp_path):
    the_row = row(cards=[done()])
    del the_row["matched_work"]
    got = judged(tmp_path, the_row, "done-elsewhere", [done_item()])
    assert (got["verdict"], got["reason"]) == ("unverified", "no proof")


def test_a_done_work_item_missing_its_pull_request_is_unreadable(tmp_path):
    got = judged(tmp_path, row(cards=[done()]), "done-elsewhere",
                 [{"done_card": "DRE-5942", "quote": "Fixed."}])
    assert (got["verdict"], got["reason"]) == ("unverified",
                                               "unreadable answer")


# --------------------------------------------------------------------------
# criterion 3 — a routing fact is proof
# --------------------------------------------------------------------------
def test_obsolete_on_a_routing_item_alone_stands_with_the_runners_fact(tmp_path):
    got, mark = both(tmp_path, row(facts=[fact()]), "obsolete",
                     [routing_item()])
    assert (got["verdict"], got["reason"]) == ("obsolete", None)
    assert got["proof"][0]["fact"] == gva.routing_line(fact())
    assert got["rested_on"] == ["routing"]


@pytest.mark.parametrize("the_row", [
    pytest.param(row(facts=[fact("other/repo:.github/workflows/x.yml:build")]),
                 id="id-absent"),
    pytest.param(row(facts=[fact()], routing_read=False), id="not-read"),
])
def test_obsolete_on_a_routing_fact_the_runner_never_supplied_is_no_proof(tmp_path, the_row):
    got = judged(tmp_path, the_row, "obsolete", [routing_item()])
    assert (got["verdict"], got["reason"]) == ("unverified", "no proof")


# --------------------------------------------------------------------------
# criterion 4 — a card with children is never canceled on a child's evidence
# --------------------------------------------------------------------------
CHILDREN = (("DRE-201", "Done"), ("DRE-202", "Done"))


@pytest.mark.parametrize("cards,proof", [
    pytest.param([done("DRE-201", parent=CARD, ancestors=(CARD, "DRE-50"))],
                 [done_item("DRE-201")], id="a-child"),
    pytest.param([done("DRE-301", parent="DRE-201",
                       ancestors=("DRE-201", CARD, "DRE-50"))],
                 [done_item("DRE-301")], id="a-grandchild"),
    pytest.param([done("DRE-401", parent="DRE-400",
                       ancestors=("DRE-400", "DRE-399", "DRE-398", "DRE-397"),
                       cut=True)],
                 [done_item("DRE-401")], id="a-chain-cut"),
    pytest.param([], [PROOF_LINE], id="file-line-alone"),
])
def test_a_cancel_of_a_card_with_children_on_a_childs_evidence_is_child_proof(tmp_path, cards, proof):
    got, mark = both(tmp_path, row(cards=cards, children=CHILDREN),
                     "done-elsewhere", proof)
    assert (got["verdict"], got["reason"]) == ("unverified",
                                               "proof belongs to a child")
    assert mark["reason"] == gva.CHILD_PROOF


def test_a_cancel_of_a_card_with_children_stands_on_a_done_card_not_under_it(tmp_path):
    proof = [PROOF_LINE, done_item()]
    got, mark = both(tmp_path, row(cards=[done()], children=CHILDREN),
                     "done-elsewhere", proof)
    assert (got["verdict"], got["reason"]) == ("done-elsewhere", None)
    # The `file:line` item is kept in the record, and not counted.
    assert got["proof"][0] == PROOF_LINE
    assert got["rested_on"] == ["done-work"]
    assert mark["verdict"] == "done-elsewhere"
    assert mark["proof"][0] == PROOF_LINE
    assert mark["rested_on"] == ["done-work"]


def test_a_cancel_of_a_card_with_children_stands_on_a_routing_fact(tmp_path):
    got, mark = both(tmp_path, row(facts=[fact()], children=CHILDREN),
                     "obsolete", [PROOF_LINE, routing_item()])
    assert (got["verdict"], got["reason"]) == ("obsolete", None)
    assert got["proof"][0] == PROOF_LINE
    assert got["rested_on"] == ["routing"]
    assert mark["rested_on"] == ["routing"]


def test_a_child_in_any_state_makes_the_rule_apply(tmp_path):
    got = judged(tmp_path, row(children=(("DRE-201", "Canceled"),)),
                 "obsolete", [PROOF_LINE])
    assert got["reason"] == gva.CHILD_PROOF


def test_code_still_counts_toward_still_needed_for_a_card_with_children(tmp_path):
    got, _ = both(tmp_path, row(children=CHILDREN), "still-needed",
                  [PROOF_LINE])
    assert (got["verdict"], got["reason"]) == ("still-needed", None)
    assert got["rested_on"] == ["code"]


def test_mark_over_a_document_carrying_an_epics_cancel_on_a_child_refuses_it():
    """A verdict file `judge` would never write: `_mark` re-applies the rule
    over the same row."""
    child = done("DRE-201", parent=CARD, ancestors=(CARD,))
    the_row = row(cards=[child], children=CHILDREN)
    mark = marked(the_row, {
        "verdict": "done-elsewhere", "summary": "s", "reason": None,
        "proof": [{**done_item("DRE-201"),
                   "fact": gva.matched_line(child, pr()),
                   "covers_check": "agent"}]})
    assert (mark["verdict"], mark["reason"]) == ("unverified",
                                                 "proof belongs to a child")


def test_mark_over_a_cancel_with_no_anchored_item_is_no_proof():
    """On a row with no matched work, as `apply` has it in the workflow: a
    done-work item the runner never wrote a `fact` on is not proof."""
    bare = {"card": CARD, "repository": PORTICO, "repo_slug": "portico",
            "list": "planning"}
    mark = gva._mark(bare, {"card": CARD, "verdict": "done-elsewhere",
                            "summary": "s", "reason": None,
                            "proof": [done_item()]})
    assert (mark["verdict"], mark["reason"]) == ("unverified", "no proof")


# --------------------------------------------------------------------------
# criterion 6 — a card with no children is judged as on main
# --------------------------------------------------------------------------
def test_a_card_with_no_children_still_stands_on_a_file_line_alone(tmp_path):
    got, mark = both(tmp_path, row(), "obsolete", [PROOF_LINE])
    assert (got["verdict"], got["reason"]) == ("obsolete", None)
    assert got["proof"] == [PROOF_LINE]
    assert mark["proof"] == [PROOF_LINE]


# --------------------------------------------------------------------------
# criterion 7 — a pull request that did part of the card is partly-solved
# --------------------------------------------------------------------------
@pytest.mark.parametrize("covers", ["part", None])
def test_a_cancel_on_a_partial_pull_request_is_partly_solved(tmp_path, covers):
    got, mark = both(tmp_path, row(cards=[done()]), "done-elsewhere",
                     [done_item(covers=covers)],
                     rewrite="## What remains\n\nThe second runner.",
                     summary="DRE-5942 released one run of two.")
    assert (got["verdict"], got["reason"]) == ("partly-solved", None)
    assert got["adjusted"] == "partial pull request"
    assert got["summary"] == "DRE-5942 released one run of two."
    assert got["rewrite"] == "## What remains\n\nThe second runner."
    assert got["body_digest"] == "0123456789ab"
    assert mark["adjusted"] == "partial pull request"
    assert mark["rewrite"] == got["rewrite"]


def test_a_partial_pull_request_turns_a_cancel_partly_solved_beside_other_proof(tmp_path):
    """The agent's own word that the pull request did part of the card is
    not outweighed by a routing fact beside it: the card goes to Rewrite
    first, never to the Cancel list."""
    got, mark = both(tmp_path, row(cards=[done()], facts=[fact()]),
                     "obsolete", [done_item(covers="part"), routing_item()])
    assert (got["verdict"], got["adjusted"]) == ("partly-solved",
                                                 "partial pull request")
    assert got["rested_on"] == ["done-work", "routing"]
    assert mark["verdict"] == "partly-solved"


def test_apply_puts_a_partial_pull_request_on_rewrite_first_never_on_cancel(tmp_path):
    pfile, targets, _ = build_targets(tmp_path, proposal())
    rows = read(targets)
    [the_row] = [r for r in rows if r["card"] == CARD]
    the_row["matched_work"] = row(cards=[done()])["matched_work"]
    doc = {"card": CARD, **gva.judge(
        answer(tmp_path, "obsolete", [done_item(covers="part")],
               rewrite="The card, rewritten."),
        card=CARD, row=the_row, outcome="success")}
    assert doc["verdict"] == "partly-solved"
    after = gva.apply(read(pfile), {CARD: doc}, rows)
    assert CARD not in [r["identifier"] for r in cancel(after)]
    assert CARD not in [r["identifier"] for r in after["outcomes"]["now"]]
    [rewrite] = [r for r in after["outcomes"]["rewrite"]
                 if r["identifier"] == CARD]
    assert rewrite["verify"]["verdict"] == "partly-solved"
    assert rewrite["verify"]["adjusted"] == "partial pull request"
    assert rewrite["draft"] == "The card, rewritten."


# --------------------------------------------------------------------------
# criterion 8 — the files the card names decide `whole` when they can
# --------------------------------------------------------------------------
def test_a_whole_pull_request_holding_one_of_two_named_files_is_partly_solved(tmp_path):
    the_row = row(cards=[done(prs=[pr(files=(POOL, "README.md"))])],
                  paths=(POOL, RUNNER))
    got, mark = both(tmp_path, the_row, "done-elsewhere", [done_item()])
    assert (got["verdict"], got["adjusted"]) == ("partly-solved",
                                                 "partial pull request")
    assert got["proof"][0]["covers_check"] == "paths"
    assert mark["verdict"] == "partly-solved"


def test_a_whole_pull_request_holding_both_named_files_stands_on_the_paths(tmp_path):
    the_row = row(cards=[done(prs=[pr(files=(RUNNER, POOL))])],
                  paths=(POOL, RUNNER))
    got, _ = both(tmp_path, the_row, "done-elsewhere", [done_item()])
    assert (got["verdict"], got["reason"]) == ("done-elsewhere", None)
    assert "adjusted" not in got
    assert got["proof"][0]["covers_check"] == "paths"
    assert not groomer.proof_line(got["proof"][0]).endswith(AGENTS_READING)


@pytest.mark.parametrize("the_row", [
    pytest.param(row(cards=[done(prs=[pr(files=("other/file.py",))])],
                     paths=(POOL, RUNNER)), id="neither-named-file"),
    pytest.param(row(cards=[done()]), id="a-card-naming-no-file"),
    pytest.param(row(cards=[done(prs=[pr(files=(POOL,), files_cut=True)])],
                     paths=(POOL, RUNNER)), id="files-cut"),
    pytest.param(row(cards=[done()], paths=(POOL, RUNNER, "a.py"),
                     left_out=2), id="paths-left-out"),
])
def test_a_whole_pull_request_the_files_cannot_decide_rests_on_the_agent(tmp_path, the_row):
    got, _ = both(tmp_path, the_row, "done-elsewhere", [done_item()])
    assert (got["verdict"], got["reason"]) == ("done-elsewhere", None)
    assert got["proof"][0]["covers_check"] == "agent"
    assert groomer.proof_line(got["proof"][0]).endswith(AGENTS_READING)


# --------------------------------------------------------------------------
# criterion 9 — rested_on keeps only supplied context
# --------------------------------------------------------------------------
def test_rested_on_drops_context_that_was_not_supplied(tmp_path):
    got, mark = both(tmp_path, row(), "still-needed", [PROOF_LINE],
                     rested_on=["thread", "routing"])
    assert got["rested_on"] == ["code"]
    assert mark["rested_on"] == ["code"]
    assert gva._verify_field(mark)["rested_on"] == ["code"]


def test_rested_on_keeps_supplied_context_and_drops_unknown_words(tmp_path):
    the_row = row(comments=[{"at": "2026-10-01", "by": "person",
                             "body": "Still breaks."}],
                  relations=[{"type": "related", "direction": "out",
                              "identifier": "DRE-9", "title": "t",
                              "state": "Done"}],
                  parent={"identifier": "DRE-50", "state": "In Progress"})
    got = judged(tmp_path, the_row, "still-needed", [PROOF_LINE],
                 rested_on=["parent", "vibes", "thread", "relations", 7])
    assert got["rested_on"] == ["code", "thread", "relations", "parent"]


# --------------------------------------------------------------------------
# criterion 10 — the page's proof line
# --------------------------------------------------------------------------
def test_proof_line_renders_done_work_and_routing_as_fact_and_quote():
    work = {**done_item(), "fact": "DRE-5942 → x#1, merged 2026-10-07 — t",
            "covers_check": "paths"}
    assert groomer.proof_line(work) == (
        "DRE-5942 → x#1, merged 2026-10-07 — t — Releases the stranded run.")
    agent = {**work, "covers_check": "agent"}
    assert groomer.proof_line(agent) == (
        "DRE-5942 → x#1, merged 2026-10-07 — t — Releases the stranded run. "
        "(whole card: the agent's reading)")
    route = {**routing_item(), "fact": "a:b:c: 6 job(s)"}
    assert groomer.proof_line(route) == (
        "a:b:c: 6 job(s) — The review job ran on GitHub's runners.")


def test_proof_line_keeps_the_file_line_and_source_forms():
    assert groomer.proof_line(PROOF_LINE) == (
        "src/legacy_migration_lib.ts:886 — export function migrateRoster(portal) {")
    assert groomer.proof_line({"source": "PR #431 body",
                               "quote": "Adds the reader."}) == (
        "PR #431 body — Adds the reader.")


# --------------------------------------------------------------------------
# the brief and the shape say all of it
# --------------------------------------------------------------------------
def test_the_shape_shows_every_proof_kind_and_rested_on():
    for field in ('"done_card"', '"pr"', '"covers"', '"routing"',
                  '"rested_on"', '"file"', '"source"'):
        assert field in gva.SHAPE, field


def test_the_brief_names_the_kinds_the_guards_and_the_two_judgements():
    flat = " ".join(BRIEF.read_text(encoding="utf-8").split())
    for field in ('"done_card"', '"covers"', '"routing"', '"rested_on"'):
        assert field in flat, field
    assert ("for a card with children, a Cancel needs a Done card that is "
            "not under it, or a routing fact") in flat
    assert "`file:line` quotes are not counted toward it" in flat
    assert "`proof belongs to a child`" in flat
    assert "`partial pull request`" in flat
    assert "with the routing fact as its proof" in flat
    assert "DRE-5656" in flat
    assert "already reads PASS" in flat
