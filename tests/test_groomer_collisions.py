"""The set-level read a per-card critic cannot produce (DRE-2683).

The independent Forms review (DRE-2649) found unrecorded collisions nobody had
spotted — two cards touching the same file, scheduled as if they were
independent. Something reading one card at a time cannot see that: the fact
only exists BETWEEN two cards. So the groomer reads the whole set, finds the
pairs, and turns each one into an ORDER between those two cards.

The three things this file pins, each of them a way the check could quietly
stop working:

  * a shared file makes an order, and the order is reported with the file that
    caused it — a constraint nobody can see is a constraint nobody can argue
    with;
  * a path cited by a large slice of the set (the branch-rule banner naming
    `linear-sync.yml` sits on nineteen live Backlog cards) is REFERENCE, not
    ownership — treating it as a collision would serialise the whole batch on
    a boilerplate line;
  * a card that names no files at all is REPORTED as unreadable. Five of the
    eight collisions DRE-2649 found are invisible to this method for exactly
    that reason, and a coverage gap that is stated is a gap someone can close.

Run: cd bureau-pipeline && python3 -m pytest tests/test_groomer_collisions.py -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import groomer  # noqa: E402

from test_groomer_population import CYCLES, card  # noqa: E402


# --------------------------------------------------------------------------
# reading the files a card declares
# --------------------------------------------------------------------------
def test_file_references_come_out_of_the_card_text():
    text = ("Edit `client/app/src/features/viewer/rails/CommentsRail/Thread.tsx` "
            "and `infra/lambda/responses_lib.ts:112` for the anchor.")
    assert groomer.file_references(text) == {"Thread.tsx", "responses_lib.ts"}


def test_a_path_matches_however_deep_the_two_cards_cite_it():
    """One card writes `Thread.tsx`, the other
    `rails/CommentsRail/Thread.tsx`. Same file, same collision."""
    a = groomer.file_references("touches `Thread.tsx`")
    b = groomer.file_references("touches `rails/CommentsRail/Thread.tsx`")
    assert a & b == {"Thread.tsx"}


def test_prose_that_is_not_a_path_is_not_a_file():
    assert groomer.file_references("the answer is in the form.definition") == set()
    assert groomer.file_references("`someIdentifier`") == set()


# --------------------------------------------------------------------------
# the pairs
# --------------------------------------------------------------------------
def _pair_cards():
    return [
        card("DRE-1", created="2026-08-01T00:00:00.000Z",
             description="rewrites `Thread.tsx`"),
        card("DRE-2", created="2026-08-05T00:00:00.000Z",
             description="also rewrites `rails/CommentsRail/Thread.tsx`"),
        card("DRE-3", description="touches nothing anyone else does: `notify_lib.ts`"),
    ]


def test_two_cards_on_one_file_are_a_collision_and_the_file_is_named():
    report = groomer.collision_report(_pair_cards())
    assert len(report["pairs"]) == 1
    pair = report["pairs"][0]
    assert {pair["before"], pair["after"]} == {"DRE-1", "DRE-2"}
    assert pair["files"] == ["Thread.tsx"]


def test_a_collision_becomes_an_order_between_those_two_cards():
    proposal = groomer.propose(_pair_cards(), cycles=CYCLES)
    pos = {r["identifier"]: r["position"] for r in proposal["sequence"]}
    assert pos["DRE-1"] < pos["DRE-2"], (
        "the older card goes first when nothing else decides it — the point "
        "is that SOME explicit order exists, recorded with its reason"
    )
    assert any("Thread.tsx" in c["files"] for c in proposal["collisions"]["pairs"])


def test_a_formal_blocked_by_relation_beats_the_age_tiebreak():
    cards = _pair_cards()
    cards[0]["inverseRelations"] = {"nodes": [
        {"type": "blocks", "issue": {"identifier": "DRE-2", "state": {"name": "Intake"}}}
    ]}
    proposal = groomer.propose(cards, cycles=CYCLES)
    pos = {r["identifier"]: r["position"] for r in proposal["sequence"]}
    assert pos["DRE-2"] < pos["DRE-1"], (
        "DRE-2 blocks DRE-1 — a recorded relation decides the order, not the "
        "creation dates"
    )


def test_a_path_everybody_cites_is_reference_not_ownership():
    """The live shape: nineteen Backlog cards carry a branch-rule banner naming
    `.github/workflows/linear-sync.yml`. None of them edits it."""
    cards = [card(f"DRE-{n}", description="branch rule: `.github/workflows/linear-sync.yml`")
             for n in range(1, 20)]
    report = groomer.collision_report(cards)
    assert report["pairs"] == []
    assert report["boilerplate"]["linear-sync.yml"] == 19


def test_a_card_that_names_no_files_is_reported_as_unreadable():
    cards = [card("DRE-1", description="Ship the thing."),
             card("DRE-2", description="edits `foo.ts`")]
    report = groomer.collision_report(cards)
    assert report["unreadable"] == ["DRE-1"]
    text = groomer.render_proposal(groomer.propose(cards, cycles=CYCLES))
    assert "DRE-1" in text and "no file" in text.lower(), (
        "the coverage gap must be visible in the proposal the CEO reads"
    )


def test_a_cross_epic_collision_orders_the_two_epics():
    """A collision spanning two epics is the kind nobody owns (DRE-2649).
    Epics are the atom of assignment, so the constraint lands on the epics."""
    cards = [
        card("DRE-11", parent="DRE-900", created="2026-08-01T00:00:00.000Z",
             description="edits `routes/library.ts`"),
        card("DRE-12", parent="DRE-900"),
        card("DRE-21", parent="DRE-901", created="2026-08-09T00:00:00.000Z",
             description="edits `infra/lambda/routes/library.ts`"),
        card("DRE-22", parent="DRE-901"),
    ]
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=2)
    pos = {r["identifier"]: r["position"] for r in proposal["sequence"]}
    assert max(pos["DRE-11"], pos["DRE-12"]) < min(pos["DRE-21"], pos["DRE-22"])
    assert proposal["collisions"]["pairs"], "the cross-epic collision was lost"


def test_two_repos_naming_the_same_file_do_not_collide():
    """`package.json` in portico and `package.json` in deltasolv are different
    files in different git repositories and can never conflict.

    Measured live on 2026-08-29: cross-repo matches on `CLAUDE.md`,
    `repo-map.json` and `deploy.sh` pulled an agent-bureau card to position 30
    and pushed Portico work behind it — a false constraint breaking the one
    ordering rule the batch has."""
    cards = [
        card("DRE-1", repo="portico", description="edits `client/package.json`"),
        card("DRE-2", repo="agent-bureau", description="edits `console/package.json`"),
    ]
    assert groomer.collision_report(cards)["pairs"] == []


def test_portico_stays_first_when_another_repo_names_the_same_basename():
    cards = [
        card("DRE-1", repo="agent-bureau", created="2026-01-01T00:00:00.000Z",
             description="edits `CLAUDE.md`"),
        card("DRE-2", repo="portico", created="2026-08-01T00:00:00.000Z",
             description="edits `CLAUDE.md`"),
    ]
    proposal = groomer.propose(cards, cycles=CYCLES)
    pos = {r["identifier"]: r["position"] for r in proposal["sequence"]}
    assert pos["DRE-2"] < pos["DRE-1"], (
        "an older agent-bureau card must not be pulled ahead of Portico by a "
        "shared basename it cannot actually conflict with"
    )


def test_a_constraint_loop_is_broken_where_it_is_found_not_at_the_end():
    """Portico cards that constrain each other in a loop must not push every
    other repo ahead of them.

    The live failure (2026-08-29): DRE-2492, DRE-2628 and DRE-2629 constrain
    each other in a loop, nothing in the tangle was ever "ready", and the sort
    drained agent-bureau, bureau-pipeline, atlas and deltasolv first — the
    highest-priority work in the population came out at position 118 of 147.

    The loop here is the shape that produces one: two collisions ordered by
    age, closed by a blocks relation pointing the other way.
    """
    cards = [
        card("DRE-10", repo="portico", created="2026-08-01T00:00:00.000Z",
             description="edits `alpha.ts`"),
        card("DRE-11", repo="portico", created="2026-08-02T00:00:00.000Z",
             description="edits `alpha.ts` and `beta.ts`"),
        card("DRE-12", repo="portico", created="2026-08-03T00:00:00.000Z",
             description="edits `beta.ts`"),
        card("DRE-20", repo="agent-bureau", created="2026-01-01T00:00:00.000Z"),
        card("DRE-21", repo="agent-bureau", created="2026-01-02T00:00:00.000Z"),
    ]
    # alpha.ts puts 10 before 11, beta.ts puts 11 before 12, and 12 blocks 10.
    cards[0]["inverseRelations"] = {"nodes": [
        {"type": "blocks", "issue": {"identifier": "DRE-12", "state": {"name": "Intake"}}}
    ]}
    proposal = groomer.propose(cards, cycles=CYCLES)
    pos = {r["identifier"]: r["position"] for r in proposal["sequence"]}
    assert max(pos["DRE-10"], pos["DRE-11"], pos["DRE-12"]) < \
        min(pos["DRE-20"], pos["DRE-21"]), (
        "the tangled Portico cards were pushed behind another repo"
    )
    assert proposal["unhonoured_constraints"], (
        "the constraint that had to be dropped must be reported, not silently "
        "discarded — two cards that each have to go first is a planning question"
    )
