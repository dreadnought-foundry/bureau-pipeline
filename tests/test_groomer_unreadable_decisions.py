"""RED-first: a groom decision the engine cannot read is reported, never
dropped (DRE-6642).

A per-card decision — `groom-excluded`, `groom-held`, `groom-added` — was read
only when its first line ended with the card id, or with the card id, an em
dash and a reason. `🧺 groom-excluded: <id> DRE-1234 already done` matched
neither, so the engine ignored it without a word: the card was offered again
the next morning, and whoever wrote the decision was never told it did not
count.

Two halves. A marker that names a card is honored for that card, whatever
follows the id — the text after it is the reason, with or without the dash.
And a marker that names no card at all is listed on the next proposal, and in
the drain's record, under `## Decisions I could not read`, its first line
quoted.

Run: cd bureau-pipeline && python3 -m pytest tests/test_groomer_unreadable_decisions.py -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import groomer  # noqa: E402

from test_groomer import CYCLES, NOW  # noqa: E402
from test_groom_verify import LANE, _args, _patch_build, planning  # noqa: E402
from test_groom_verify import PROPOSAL_CARD as STANDING_CARD  # noqa: E402
from test_groomer_decisions import (  # noqa: E402
    PROPOSAL_CARD, FakeOps, _batch_ids, _lanes, _proposal, _record, _row,
    _sole_record, _thread)

HEADING = "## Decisions I could not read"
PID = "abc123abc123"


def _said(line, *, by_pipeline=False, rest=""):
    """A decision typed by hand — the point of this card is the shapes the
    groomer's own writer never produces, so these are written out."""
    return {"body": line + rest, "authored_by_pipeline": by_pipeline}


def _excluded(pid, tail, **kw):
    return _said(f"{groomer.MARK} {groomer.EXCLUDE_TAG}: {pid}{tail}", **kw)


def _section(body):
    """The lines under HEADING, up to the next heading or the end."""
    lines = body.splitlines()
    assert HEADING in lines, f"no `{HEADING}` section:\n{body}"
    out = []
    for line in lines[lines.index(HEADING) + 1:]:
        if line.startswith("## "):
            break
        out.append(line)
    return "\n".join(out)


# --------------------------------------------------------------------------
# the four shapes, read by the standing reader
# --------------------------------------------------------------------------
@pytest.mark.parametrize("tail,rest", [
    (" DRE-7 — already done", ""),                      # the dash form
    (" DRE-7", "\n\nalready done, see the thread"),     # reason on line two
    (" DRE-7 already done", ""),                        # no dash, same line
    (" DRE-7 - already done", ""),                      # a hyphen for a dash
    (" DRE-7: already done", ""),                       # a colon for a dash
], ids=["dash", "second-line", "no-dash", "hyphen", "colon"])
def test_a_marker_that_names_a_card_is_honoured_for_it(tail, rest):
    records = [_excluded(PID, tail, rest=rest)]
    got = groomer.standing_decisions(records)
    assert got["excluded"] == ["DRE-7"]
    assert got["unreadable"] == []


@pytest.mark.parametrize("tag", ["groom-excluded", "groom-held",
                                 "groom-added"])
def test_every_per_card_marker_reads_a_reason_without_the_dash(tag):
    records = [_said(f"{groomer.MARK} groom-excluded: {PID} DRE-7"),
               _said(f"{groomer.MARK} {tag}: {PID} DRE-7 because I said so")]
    got = groomer.standing_decisions(records)
    assert ("DRE-7" in got["excluded"]) == (tag == "groom-excluded")
    assert ("DRE-7" in got["held"]) == (tag == "groom-held")


def test_a_marker_that_names_no_card_is_listed_with_its_first_line():
    line = f"{groomer.MARK} {groomer.EXCLUDE_TAG}: {PID} already done"
    records = [_said(line, rest="\n\nAna and I talked this through.")]
    got = groomer.standing_decisions(records)
    assert got["excluded"] == [] and got["held"] == []
    assert got["unreadable"] == [line]


def test_an_unreadable_marker_the_pipeline_wrote_is_not_a_decision_to_list():
    records = [_excluded(PID, " already done", by_pipeline=True)]
    assert groomer.standing_decisions(records)["unreadable"] == []


def test_an_unreadable_marker_is_listed_on_the_next_proposal_only():
    """Listed once — on the proposal posted after it was written. A thread
    carries every decision ever made, and one already reported on an earlier
    page is not reported again on every morning's."""
    proposal = _proposal()
    old = f"{groomer.MARK} {groomer.HOLD_TAG}: {PID} next week"
    new = f"{groomer.MARK} {groomer.EXCLUDE_TAG}: {PID} not this one"
    records = [_said(old), _record(proposal), _said(new)]
    assert groomer.standing_decisions(records)["unreadable"] == [new]


# --------------------------------------------------------------------------
# the next proposal says so
# --------------------------------------------------------------------------
def test_the_next_proposal_lists_the_decision_it_could_not_read(monkeypatch):
    _patch_build(monkeypatch, LANE)
    first = groomer._build(_args(post=STANDING_CARD))
    line = (f"{groomer.MARK} {groomer.EXCLUDE_TAG}: {first['id']} the second "
            f"card is already done")
    thread = [{"body": groomer.proposal_comment(first),
               "authored_by_pipeline": True}, _said(line)]
    _patch_build(monkeypatch, LANE, thread)
    second = groomer._build(_args(post=STANDING_CARD))
    assert second["unreadable_decisions"] == [line]
    section = _section(groomer.render_proposal(second))
    assert line in section, "the decision's first line is not quoted"


def test_a_same_line_reason_without_the_dash_keeps_the_card_off_the_offer(
        monkeypatch):
    _patch_build(monkeypatch, LANE)
    first = groomer._build(_args(post=STANDING_CARD))
    assert "DRE-102" in planning(first)
    thread = [{"body": groomer.proposal_comment(first),
               "authored_by_pipeline": True},
              _excluded(first["id"], " DRE-102 already done")]
    _patch_build(monkeypatch, LANE, thread)
    second = groomer._build(_args(post=STANDING_CARD))
    assert "DRE-102" not in planning(second)
    assert second["excluded_before"] == ["DRE-102"]
    assert "unreadable_decisions" not in second


def test_a_proposal_with_nothing_unread_has_no_such_section():
    plain = groomer.propose(LANE[:5], cycles=CYCLES, capacity=3, now=NOW)
    same = groomer.propose(LANE[:5], cycles=CYCLES, capacity=3, now=NOW,
                           unreadable_decisions=())
    assert plain == same
    assert HEADING not in groomer.render_proposal(plain)


def test_a_quoted_line_cannot_break_out_of_its_quote():
    line = f"{groomer.MARK} {groomer.EXCLUDE_TAG}: {PID} see `the doc` | here"
    proposal = groomer.propose(LANE[:5], cycles=CYCLES, capacity=3, now=NOW,
                               unreadable_decisions=[line])
    section = _section(groomer.render_proposal(proposal))
    quoted = [ln for ln in section.splitlines() if ln.startswith("- ")]
    assert len(quoted) == 1
    assert quoted[0].count("`") == 2, quoted[0]
    assert "see 'the doc' | here" in quoted[0]


# --------------------------------------------------------------------------
# the drain honours the shapes and records what it could not read
# --------------------------------------------------------------------------
def test_the_drain_holds_back_a_card_excluded_without_the_dash():
    proposal = _proposal()
    batch = _batch_ids(proposal)
    ops = FakeOps(comments=_thread(
        proposal, _excluded(groomer.proposal_id(proposal),
                            f" {batch[0]} design is not settled")),
        lanes=_lanes(proposal))
    result = groomer.drain(ops, card=PROPOSAL_CARD)
    assert batch[0] not in result["moved"]
    assert result["held_back"] == [batch[0]]
    row = _row(_sole_record(ops), batch[0])
    assert "design is not settled" in row, "the reason was not kept"


def test_the_drain_keeps_no_reason_off_a_second_line():
    proposal = _proposal()
    batch = _batch_ids(proposal)
    ops = FakeOps(comments=_thread(
        proposal, _excluded(groomer.proposal_id(proposal), f" {batch[0]}",
                            rest="\n\ndesign is not settled")),
        lanes=_lanes(proposal))
    result = groomer.drain(ops, card=PROPOSAL_CARD)
    assert result["held_back"] == [batch[0]]
    assert HEADING not in _sole_record(ops)


def test_the_drains_record_lists_the_decision_it_could_not_read():
    proposal = _proposal()
    pid = groomer.proposal_id(proposal)
    line = f"{groomer.MARK} {groomer.EXCLUDE_TAG}: {pid} the first one is done"
    ops = FakeOps(comments=_thread(proposal, _said(line)),
                  lanes=_lanes(proposal))
    result = groomer.drain(ops, card=PROPOSAL_CARD)
    assert result["moved"] == _batch_ids(proposal)
    assert result["unreadable_decisions"] == [line]
    body = _sole_record(ops)
    assert line in _section(body)
    # The table and its summary line are the console's contract, and read
    # exactly as before: the section comes after the last row.
    table = [n for n, ln in enumerate(body.splitlines()) if ln.startswith("|")]
    assert table[-1] < body.splitlines().index(HEADING)


def test_the_drains_record_lists_only_decisions_about_its_own_batch():
    proposal = _proposal()
    other = f"{groomer.MARK} {groomer.EXCLUDE_TAG}: {PID} an older batch"
    ops = FakeOps(comments=_thread(proposal, _said(other)),
                  lanes=_lanes(proposal))
    result = groomer.drain(ops, card=PROPOSAL_CARD)
    assert result["unreadable_decisions"] == []
    assert HEADING not in _sole_record(ops)
