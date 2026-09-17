"""A 0-card proposal is not a decision, and a dry run is not a proposal (DRE-3712).

On **2026-09-04 10:21 PT** the groomer posted `🧺 groom-proposal: 2b10ecfb36f6`
on DRE-2840: 0 of 0 cards, over an Intake that was empty, as a dry-run
demonstration of the new ordering. Nothing about it was a decision, and the
console had no way to know that — it reads the marker, so it showed the CEO a
proposal waiting on him. He found it in Green Light on 2026-09-12 "waiting
196.9 h" and asked what was going on. Eight days of a decision queue holding a
demo.

Three things are pinned here, and the third is the one that made the row
unreadable as well as pointless:

  * **An empty batch writes no marker.** `post_proposal` is the one writer of
    `🧺 groom-proposal:`, so the refusal lives there and covers every caller —
    the CLI, the workflow, and a retry.
  * **A dry run writes nothing at all.** `propose --dry-run` renders the
    proposal it WOULD have posted to the run log and posts none of it, so a
    demonstration can be run against a live card without putting a decision in
    front of anyone.
  * **The title line is well-formed for every input.** With no cycle the
    proposal used to render `— cycle ` and stop, and the console's copy of the
    pattern walked past the blank line onto the next one: the Green Light row
    read *"0 cards for cycle 0 cards of 0 in Intake…"*. No cycle, no clause.

The console keeps the other copy of that pattern
(`console/backend/groom_proposal.py` in agent-bureau) and this repo cannot
import it, so `TITLE` below is the shape it mirrors, written out here. The two
change in lockstep — the mirror is its own card in that repo, blocked by this
one.

Run: cd bureau-pipeline && python3 -m pytest tests/test_groomer_empty_batch.py -v
"""
from __future__ import annotations

import contextlib
import io
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")

import groomer  # noqa: E402

from test_groomer_population import CYCLES, card  # noqa: E402
from test_groomer_retry import FakeOps  # noqa: E402

PROPOSAL_CARD = "DRE-2840"

#: The id the 2026-09-04 comment carried. It is a digest of the batch's own
#: contents, so an empty lane still produces exactly this one — which makes it
#: the incident's own fixture rather than a number invented for a test.
INCIDENT_ID = "2b10ecfb36f6"

#: The console's reader, in the shape it mirrors: the title line read ON ITS
#: OWN, with the cycle clause optional. The real one lives in agent-bureau.
TITLE = re.compile(r"^# Groom proposal `(?P<id>[0-9a-f]{6,})`"
                   r"(?: — cycle (?P<cycle>.+))?$")

#: …and the same reader as it behaved on 2026-09-04, when the clause was
#: always written: `\s*` crosses the blank line, so an empty cycle captures
#: whatever the next line says. The fix is that there is nothing left after
#: `cycle` for it to walk on to.
GREEDY = re.compile(r"# Groom proposal `[0-9a-f]{6,}` — cycle\s*(.+)")


def empty() -> dict:
    """The 2026-09-04 proposal: an empty lane, and nothing to propose."""
    return groomer.propose([], cycles=CYCLES, capacity=3, batch_cycles=1)


def batch() -> dict:
    """An ordinary proposal — a six-card lane, three of them in cycle 12."""
    return groomer.propose([card(f"DRE-{n:03d}") for n in range(6)],
                           cycles=CYCLES, capacity=3, batch_cycles=1)


class EmptyLane(FakeOps):
    """`FakeOps` over a lane with nothing in it."""

    def gql_paged(self, query, variables=None, *, connection="issues"):
        return []


# --------------------------------------------------------------------------
# an empty batch writes no marker
# --------------------------------------------------------------------------
def test_an_empty_batch_posts_no_proposal_marker():
    proposal = empty()
    assert proposal["batch"]["cards"] == 0
    ops = FakeOps()
    assert groomer.post_proposal(ops, PROPOSAL_CARD, proposal) is False
    assert ops.posted == [], "a 0-card proposal was put in front of the CEO"


def test_the_2026_09_04_proposal_is_the_one_refused():
    """The incident's own id, recomputed from an empty lane."""
    assert empty()["id"] == INCIDENT_ID


def test_a_batch_with_cards_still_posts():
    """The guard is about the empty batch and nothing else — a groomer that
    stopped proposing would pass the test above and be useless."""
    ops = FakeOps()
    assert groomer.post_proposal(ops, PROPOSAL_CARD, batch()) is True
    assert len(ops.posted) == 1


def test_the_cli_writes_nothing_when_the_lane_is_empty():
    """`propose --post` over an empty Intake is the run that happened."""
    ops = EmptyLane()
    real = groomer.linear_ops
    groomer.linear_ops = ops
    try:
        assert groomer.main(["propose", "--lane", "Intake", "--capacity", "3",
                             "--no-judgement", "--post", PROPOSAL_CARD]) == 0
    finally:
        groomer.linear_ops = real
    assert ops.posted == [], "the CLI posted a 0-card proposal"


# --------------------------------------------------------------------------
# a dry run writes nothing at all
# --------------------------------------------------------------------------
def test_a_dry_run_posts_no_marker():
    ops = FakeOps()
    real = groomer.linear_ops
    groomer.linear_ops = ops
    try:
        assert groomer.main(["propose", "--lane", "Intake", "--capacity", "3",
                             "--no-judgement", "--post", PROPOSAL_CARD,
                             "--dry-run"]) == 0
    finally:
        groomer.linear_ops = real
    assert ops.posted == [], "a dry run posted a proposal to the card"


def test_the_same_run_without_the_flag_does_post():
    """Non-vacuous: the six-card lane the dry run read is one that proposes."""
    ops = FakeOps()
    real = groomer.linear_ops
    groomer.linear_ops = ops
    try:
        assert groomer.main(["propose", "--lane", "Intake", "--capacity", "3",
                             "--no-judgement", "--post", PROPOSAL_CARD]) == 0
    finally:
        groomer.linear_ops = real
    assert len(ops.posted) == 1


def test_a_dry_run_prints_the_proposal_it_would_have_posted():
    """The run log is where a demonstration belongs — the whole page, so the
    dry run is still a demonstration of the ordering."""
    ops = FakeOps()
    real = groomer.linear_ops
    groomer.linear_ops = ops
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            groomer.main(["propose", "--lane", "Intake", "--capacity", "3",
                          "--no-judgement", "--post", PROPOSAL_CARD,
                          "--dry-run"])
    finally:
        groomer.linear_ops = real
    text = out.getvalue()
    assert "# Groom proposal" in text
    assert "## The batch, in order" in text
    assert "dry run" in text.lower(), \
        "the log does not say the proposal was not posted"


# --------------------------------------------------------------------------
# the title line, well-formed for every input
# --------------------------------------------------------------------------
def test_a_named_cycle_reads_as_it_always_did():
    text = groomer.render_proposal(batch())
    match = TITLE.match(text.splitlines()[0])
    assert match, f"the console's reader cannot parse {text.splitlines()[0]!r}"
    assert match.group("cycle") == "12"


def test_no_cycle_omits_the_clause_rather_than_leaving_it_empty():
    line = groomer.render_proposal(empty()).splitlines()[0]
    match = TITLE.match(line)
    assert match, f"the console's reader cannot parse {line!r}"
    assert match.group("id") == INCIDENT_ID
    assert match.group("cycle") is None
    assert not line.rstrip().endswith("cycle"), \
        "a dangling `— cycle` is the bug, with or without the space"


def test_the_readers_cycle_never_runs_on_into_the_next_line():
    """The Green Light title read "0 cards for cycle 0 cards of 0 in Intake…"
    because the clause was written empty and the reader crossed the blank line
    to fill it."""
    assert GREEDY.search(groomer.render_proposal(empty())) is None
    match = GREEDY.search(groomer.render_proposal(batch()))
    assert match and match.group(1) == "12"


def test_the_lead_line_names_no_empty_cycle_either():
    """The second copy of the same clause, one paragraph down — it is what the
    runaway title actually quoted."""
    text = groomer.render_proposal(empty())
    assert "0 cards of 0 in Intake are proposed, in the order below." in text
    assert "for cycle ," not in text


def test_the_lead_line_still_names_a_real_cycle():
    assert ("3 cards of 6 in Intake are proposed for cycle 12, in the order "
            "below.") in groomer.render_proposal(batch())
