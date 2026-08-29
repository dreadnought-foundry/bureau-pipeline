"""A retried `propose --post` writes nothing (DRE-2683, vendor boundary Q3).

`standards/vendor-boundaries.md` question 3 asks what a retry does, and the
answer written down for the groomer is "a retried `propose` writes nothing".
That answer has to hold for the way the tool is actually invoked: `docs/groomer.md`
tells the operator to run `propose --post DRE-2683`, and the reusable workflow
passes `--post` whenever the `card` input is set. `propose --post` posts the
proposal to the card, and Linear's `commentCreate` has no idempotency key — so a
re-dispatch after a crash or a transient failure (exactly what `self-medic.yml`
retries) would post a second copy of the same proposal, and the card thread would
collect one duplicate per retry.

The post is therefore made idempotent by the only fact available to it: the
proposal id is a digest of the batch's own contents, so "this proposal is already
on the card" is decidable by reading the thread. A population that moved produces
a different id and does post — a retry writing nothing must not become a groomer
that cannot say anything new.

The marker anchoring is the approval gate's, for the approval gate's reason: a
comment that mentions the marker mid-sentence is prose about a proposal, not a
proposal.

Run: cd bureau-pipeline && python3 -m pytest tests/test_groomer_retry.py -v
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

PROPOSAL_CARD = "DRE-2683"


class FakeOps:
    """A card thread that remembers what was posted to it — the whole point is
    what the SECOND call does, so the fake has to carry the first call's write."""

    def __init__(self, comments=None):
        self.comments = list(comments or [])
        self.posted: list[tuple[str, str]] = []

    def comment_records(self, identifier):
        return list(self.comments)

    def cmd_comment(self, identifier, body):
        self.posted.append((identifier, body))
        self.comments.append({"body": body, "authored_by_pipeline": True})

    # the reads `propose` makes when it is driven through the CLI
    def gql_paged(self, query, variables=None, *, connection="issues"):
        return [card(f"DRE-{n:03d}") for n in range(6)]

    def gql(self, query, variables=None):
        return {"cycles": {"nodes": [dict(c, completedAt=None) for c in CYCLES]}}

    def __getattr__(self, name):
        """Everything else the groomer reads off `linear_ops` is a pure parser
        with no network of its own (`parse_blocked_by`) — faking those would be
        faking the code under test."""
        import linear_ops
        return getattr(linear_ops, name)


def _proposal():
    cards = [card(f"DRE-{n:03d}") for n in range(6)]
    return groomer.propose(cards, cycles=CYCLES, capacity=3, batch_cycles=1)


# --------------------------------------------------------------------------
# the retry
# --------------------------------------------------------------------------
def test_the_first_post_writes_the_proposal():
    proposal = _proposal()
    ops = FakeOps()
    assert groomer.post_proposal(ops, PROPOSAL_CARD, proposal) is True
    assert len(ops.posted) == 1
    assert ops.posted[0][0] == PROPOSAL_CARD
    assert ops.posted[0][1].startswith(f"{groomer.MARK} {groomer.PROPOSAL_TAG}: "
                                       f"{proposal['id']}")


def test_a_retried_post_writes_nothing():
    """The claim in the PR body and in `self-medic.yml`'s header, asserted."""
    proposal = _proposal()
    ops = FakeOps()
    groomer.post_proposal(ops, PROPOSAL_CARD, proposal)
    assert groomer.post_proposal(ops, PROPOSAL_CARD, proposal) is False
    assert len(ops.posted) == 1, "a retry posted a duplicate proposal"


def test_a_proposal_for_a_moved_population_still_posts():
    """Idempotence is per batch, not per card. A groomer that goes silent once
    any proposal is on the card cannot report that the population changed."""
    proposal = _proposal()
    stale = {"body": f"{groomer.MARK} {groomer.PROPOSAL_TAG}: {'0' * 12}\n\nolder batch",
             "authored_by_pipeline": True}
    ops = FakeOps(comments=[stale])
    assert groomer.post_proposal(ops, PROPOSAL_CARD, proposal) is True
    assert len(ops.posted) == 1


def test_a_mention_of_the_marker_in_prose_does_not_suppress_the_post():
    proposal = _proposal()
    ops = FakeOps(comments=[
        {"body": f"did we ever get {groomer.MARK} {groomer.PROPOSAL_TAG}: "
                 f"{proposal['id']} in front of Ana?",
         "authored_by_pipeline": False},
    ])
    assert groomer.post_proposal(ops, PROPOSAL_CARD, proposal) is True
    assert len(ops.posted) == 1, "prose about a proposal silenced the proposal"


# --------------------------------------------------------------------------
# the path the operator and the workflow actually take
# --------------------------------------------------------------------------
def test_the_cli_posts_through_the_idempotent_path():
    """`propose --post` is the documented command and the one the reusable
    workflow issues, so the guard is worth nothing unless the CLI routes
    through it."""
    ops = FakeOps()
    real = groomer.linear_ops
    groomer.linear_ops = ops
    try:
        assert groomer.main(["propose", "--lane", "Intake", "--capacity", "3",
                             "--post", PROPOSAL_CARD]) == 0
        assert groomer.main(["propose", "--lane", "Intake", "--capacity", "3",
                             "--post", PROPOSAL_CARD]) == 0
    finally:
        groomer.linear_ops = real
    assert len(ops.posted) == 1, "a re-dispatched propose posted a duplicate"
