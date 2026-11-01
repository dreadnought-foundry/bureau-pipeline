"""The proof dispatcher starts no first run before a card's Observe after time (DRE-8439).

On the evening of 2026-10-10 the dispatcher started three first runs at
DRE-6678, the last at 19:57 PT, for a groom proposal due at 06:00 PT on
2026-10-11. Each run opened a record of nine unobserved rows, and the re-run
budget was spent before the event existed. DRE-8436 gave a PROOF card an
`Observe after:` line and the two readers of it; this is the gate that reads
them, condition 8 of a first run.

Pinned here, through the dispatcher's real sweep with injected reads, one
card across several passes whose thread grows with every write:

  * the DRE-6678 replay: one `🔬 proof-waiting` hold naming 06:00 PT on
    2026-10-11, the passes after it held off the lane read, and the first
    pass after the time lifting it and dispatching `dispatch 1 of 2`;
  * a card with no line dispatched on its first pass, as before;
  * the absolute form, held until its own time;
  * a malformed line refused every pass, told once by `MALFORMED_COMMENT`,
    never held, and read again once it is fixed;
  * a merge time that cannot be read refused every pass, told once by
    `RELEASE_UNREAD_COMMENT`; a read that raised posts nothing;
  * the dry run, which prints `would: hold` and writes nothing.

Run: cd bureau-pipeline && python3 -m pytest tests/test_proof_dispatch_observe_after.py -v
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import linear_ops  # noqa: E402
import proof_and_demo  # noqa: E402
import proof_dispatch  # noqa: E402
import proof_record  # noqa: E402
import proof_release  # noqa: E402
import proof_run_state  # noqa: E402
from test_proof_dispatch import (  # noqa: E402
    REPO, SLUG, VIEWER, detail, fake_voices, lane_card, state,
)

CARD = "DRE-6678"
PT = ZoneInfo("America/Los_Angeles")
GROOM = ("Observe after: the first groom proposal after release — "
         "next 06:00 PT after release")
AT_FORM = "Observe after: 2026-10-18 06:30 PT"
#: The epic's two build cards (`detail()`'s siblings) and when each merged:
#: the newest, DRE-5922 at 18:40 PT, is the release time.
BUILD_MERGES = {"DRE-5921": "2026-10-10T17:10:00-07:00",
                "DRE-5922": "2026-10-10T18:40:00-07:00"}


def pt(y, mo, d, h, mi) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=PT)


def _iso(when: datetime) -> str:
    return when.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class Card:
    """One PROOF card in `Hand-work` across passes. Every write lands on its
    thread, and the lane read's window is that thread, as Linear shows it."""

    def __init__(self, monkeypatch, description, *, merged=None):
        self.description = description
        self.merged = dict(BUILD_MERGES if merged is None else merged)
        self.thread = [{"body": "🧹 Auto-promoted Backlog → Hand-work: the "
                                "proof is a person's to confirm.",
                        "createdAt": _iso(pt(2026, 10, 10, 18, 50)),
                        "user": {"id": VIEWER}}]
        self.fired: list = []
        self.reads: list = []
        self.now = None
        monkeypatch.setattr(linear_ops, "cmd_comment", self._post)
        monkeypatch.setattr(linear_ops, "cmd_proof_waiting", self._hold)
        monkeypatch.setattr(linear_ops, "cmd_proof_observed", self._observed)

    # -- the writes, each a comment on the thread ---------------------------- #

    def _post(self, ident, body, *flags):
        assert ident == CARD
        self.thread.append({"body": body, "createdAt": _iso(self.now),
                            "user": {"id": VIEWER}})

    def _hold(self, ident, observed, needs):
        self._post(ident, linear_ops.proof_waiting_line(observed, needs))

    def _observed(self, ident, text):
        self._post(ident, linear_ops.proof_observed_line(text))

    # -- the reads ------------------------------------------------------------ #

    def lane(self, lane):
        self.reads.append(("lane", lane))
        if lane != "Hand-work":
            return []
        return [lane_card(CARD, window=list(self.thread))]

    def card(self, ident):
        self.reads.append(("card", ident))
        got = detail()
        got["description"] = self.description
        return got

    def thread_of(self, ident):
        self.reads.append(("thread", ident))
        return list(self.thread), VIEWER

    def blocking(self, ident):
        return []

    def fix_card(self, ident):
        raise LookupError(ident)

    def find_pr(self, ident):
        got = self.merged.get(ident)
        if isinstance(got, Exception):
            raise got
        pr = {"number": int(ident.split("-")[1]), "state": "MERGED",
              "headRefName": f"agent/{ident}-build",
              "mergeCommit": {"oid": f"{ident[-4:]}abcdef0123456789"}}
        if got is not None:
            pr["mergedAt"] = got
        return pr

    def fire(self, card, repo, *, reason=None, event=None):
        self.fired.append((card["identifier"], reason))
        return True, ""

    # -- one pass ------------------------------------------------------------- #

    def sweep(self, when: datetime, *, live=True):
        self.now = when
        linear = _Linear(self)
        return proof_dispatch.sweep(
            REPO, SLUG, live=live, linear=linear, read=lambda path: None,
            find_pr=self.find_pr,
            run_state=lambda *a, **k: state("none"),
            release=lambda repo, merges, *, read: proof_release.Reading(
                "ready", ["ready — fixture"]),
            fire=self.fire, voices=fake_voices, now=when,
            find_record=lambda ident: None,
            read_record=lambda pr, head: proof_record.Record("x", None, "none"),
            open_heads=lambda: {}, file_fix=None)

    # -- what the thread says ------------------------------------------------- #

    def bodies(self) -> list:
        return [c["body"] for c in self.thread]

    def holds(self) -> list:
        return [b for b in self.bodies() if b.startswith(proof_record.HOLD_MARK)]

    def lifts(self) -> list:
        return [b for b in self.bodies() if b.startswith(proof_record.OBSERVED_MARK)]

    def plain(self) -> list:
        """The comments that are neither a hold, a lift, a receipt nor the
        promotion: what the dispatcher told the card in words."""
        return [b for b in self.bodies()
                if not b.startswith((proof_record.HOLD_MARK, proof_record.OBSERVED_MARK,
                                     "🧹", "🔬"))]


class _Linear:
    """`LinearReads`, served by the card."""

    def __init__(self, card):
        self.of = card

    def lane(self, lane):
        return self.of.lane(lane)

    def card(self, ident):
        return self.of.card(ident)

    def thread(self, ident):
        return self.of.thread_of(ident)

    def blocking(self, ident):
        return self.of.blocking(ident)

    def fix_card(self, ident):
        return self.of.fix_card(ident)


def _lines(capsys) -> list:
    return [line for line in capsys.readouterr().out.splitlines() if CARD in line]


def _first(body: str) -> str:
    return body.split("\n", 1)[0]


# --------------------------------------------------------------------------- #
# the contract                                                                 #
# --------------------------------------------------------------------------- #


def test_the_pull_request_read_carries_its_merge_time():
    assert "mergedAt" in proof_dispatch.PR_FIELDS.split(",")


def test_the_card_read_carries_the_description():
    """Condition 8 reads the line through the request the candidate already
    makes, adding no Linear request."""
    assert "description" in proof_dispatch.CARD_QUERY


def test_the_two_comments_are_module_constants():
    assert proof_dispatch.MALFORMED_COMMENT == (
        "Proof not started — its Observe after line does not parse: “{line}”.")
    assert proof_dispatch.RELEASE_UNREAD_COMMENT == (
        "Proof not started — the time its Observe after line names cannot be "
        "worked out, because the merge time of {cards} cannot be read.")


def test_the_docstring_names_condition_8():
    doc = proof_dispatch.__doc__
    first_runs = doc[doc.index("## A first run"):doc.index("One exception to the order")]
    assert "  8. " in first_runs
    assert "Observe after" in first_runs


def test_the_card_standard_says_the_dispatcher_waits_on_the_line():
    """`standards/card-quality.md` told a planner the line was not yet waited
    on until this gate shipped; with the gate in, it says the dispatcher waits."""
    text = (ROOT / "standards" / "card-quality.md").read_text(encoding="utf-8")
    start = text.index("A PROOF card can say when it can first be observed")
    paragraph = text[start:text.index("**Proofs never wait on the CEO's sign-in", start)]
    flat = " ".join(paragraph.split())
    assert "not yet waited on" not in flat
    assert "until that gate ships" not in flat
    assert "condition 8" in flat and "DRE-8439" in flat
    assert "proof-waiting" in flat
    assert "dispatch 1 of 2" in flat


# --------------------------------------------------------------------------- #
# the DRE-6678 replay                                                          #
# --------------------------------------------------------------------------- #


def test_the_dre_6678_replay_holds_once_and_dispatches_after_06_00(monkeypatch, capsys):
    card = Card(monkeypatch, f"What to observe.\n\n{GROOM}\n")

    tally = card.sweep(pt(2026, 10, 10, 19, 22))
    lines = _lines(capsys)
    assert card.fired == []
    assert tally.held == 1 and tally.dispatched == 0
    holds = card.holds()
    assert len(holds) == 1
    line = _first(holds[0])
    assert line == linear_ops.proof_waiting_line(
        f"its card says it can first be observed after 2026-10-11 06:00 PT ({GROOM})",
        "not-before=2026-10-11T06:00-07:00")
    # The hold is the time-only kind, so the self-lift reads it.
    assert proof_record.time_only(line) == pt(2026, 10, 11, 6, 0)
    refusal = [x for x in lines if "condition 8" in x]
    assert refusal and GROOM in refusal[0]
    assert refusal[0].endswith("— lifts at 2026-10-11 06:00 PT")

    for when in (pt(2026, 10, 10, 19, 55), pt(2026, 10, 10, 21, 30)):
        card.reads.clear()
        tally = card.sweep(when)
        assert card.fired == []
        assert tally.held == 1
        # Held off the lane read: no candidate read is spent on it.
        assert ("thread", CARD) not in card.reads
        assert ("card", CARD) not in card.reads
    capsys.readouterr()
    assert len(card.holds()) == 1
    assert card.plain() == []

    tally = card.sweep(pt(2026, 10, 11, 6, 7))
    assert len(card.lifts()) == 1
    assert card.fired == [(CARD, "first proof run")]
    assert tally.dispatched == 1
    receipts = [proof_run_state.receipt(b) for b in card.bodies()]
    receipts = [r for r in receipts if r is not None]
    assert len(receipts) == 1
    assert receipts[0].reason == "first proof run"
    assert receipts[0].count == "dispatch 1 of 2"
    assert len(card.holds()) == 1


def test_the_replay_not_live_prints_would_hold_and_writes_nothing(monkeypatch, capsys):
    card = Card(monkeypatch, GROOM)
    before = list(card.thread)
    tally = card.sweep(pt(2026, 10, 10, 19, 22), live=False)
    out = capsys.readouterr().out
    assert f"would: hold {CARD}" in out
    assert card.thread == before
    assert card.fired == []
    assert tally.dispatched == 0


# --------------------------------------------------------------------------- #
# no line, and the absolute form                                               #
# --------------------------------------------------------------------------- #


def test_a_card_with_no_line_is_dispatched_on_its_first_pass(monkeypatch, capsys):
    card = Card(monkeypatch, "What to observe, and no time at all.")
    tally = card.sweep(pt(2026, 10, 10, 19, 22))
    assert card.fired == [(CARD, "first proof run")]
    assert tally.dispatched == 1
    assert card.holds() == []


def test_a_card_with_no_line_needs_no_merge_time(monkeypatch, capsys):
    """Today's cards carry no line, and nothing reads a merge time for them."""
    card = Card(monkeypatch, "No line.", merged={"DRE-5921": None, "DRE-5922": None})
    tally = card.sweep(pt(2026, 10, 10, 19, 22))
    assert card.fired == [(CARD, "first proof run")]
    assert tally.dispatched == 1


def test_the_absolute_form_holds_until_its_own_time(monkeypatch, capsys):
    card = Card(monkeypatch, AT_FORM)
    card.sweep(pt(2026, 10, 12, 9, 0))
    assert card.fired == []
    holds = card.holds()
    assert len(holds) == 1
    assert "2026-10-18 06:30 PT" in holds[0]
    assert proof_record.time_only(_first(holds[0])) == pt(2026, 10, 18, 6, 30)

    card.sweep(pt(2026, 10, 18, 6, 29))
    assert card.fired == []
    assert len(card.holds()) == 1

    tally = card.sweep(pt(2026, 10, 18, 6, 30))
    assert card.fired == [(CARD, "first proof run")]
    assert tally.dispatched == 1
    assert len(card.lifts()) == 1


# --------------------------------------------------------------------------- #
# a malformed line                                                             #
# --------------------------------------------------------------------------- #


def _malformed(line: str) -> str:
    return proof_dispatch.MALFORMED_COMMENT.format(line=line)


def test_a_malformed_line_is_refused_every_pass_and_told_once(monkeypatch, capsys):
    card = Card(monkeypatch, "Observe after: soon")
    for hour in (19, 20, 21):
        tally = card.sweep(pt(2026, 10, 10, hour, 30))
        lines = _lines(capsys)
        assert card.fired == []
        assert tally.refused == 1 and tally.held == 0
        refusal = [x for x in lines if "condition 8" in x]
        assert refusal and "Observe after: soon" in refusal[0]
    told = card.plain()
    assert len(told) == 1
    assert _first(told[0]) == _malformed("Observe after: soon")
    # The five forms follow it.
    for form in proof_and_demo.OBSERVE_AFTER_FORMS:
        assert form in told[0]
    assert card.holds() == []


def test_a_fixed_line_is_read_and_held_with_no_comment(monkeypatch, capsys):
    card = Card(monkeypatch, "Observe after: soon")
    card.sweep(pt(2026, 10, 10, 19, 30))
    assert len(card.plain()) == 1
    card.description = AT_FORM
    tally = card.sweep(pt(2026, 10, 10, 20, 30))
    assert len(card.plain()) == 1
    assert tally.held == 1
    holds = card.holds()
    assert len(holds) == 1 and "2026-10-18 06:30 PT" in holds[0]


def test_a_line_edited_to_another_malformed_text_is_told_once_more(monkeypatch, capsys):
    card = Card(monkeypatch, "Observe after: soon")
    card.sweep(pt(2026, 10, 10, 19, 30))
    card.description = "Observe after: later"
    card.sweep(pt(2026, 10, 10, 20, 30))
    card.sweep(pt(2026, 10, 10, 21, 30))
    told = [_first(b) for b in card.plain()]
    assert told == [_malformed("Observe after: soon"), _malformed("Observe after: later")]
    assert card.fired == [] and card.holds() == []


def test_a_malformed_line_not_live_prints_would_comment(monkeypatch, capsys):
    card = Card(monkeypatch, "Observe after: soon")
    before = list(card.thread)
    card.sweep(pt(2026, 10, 10, 19, 30), live=False)
    assert f"would: comment {CARD}" in capsys.readouterr().out
    assert card.thread == before


# --------------------------------------------------------------------------- #
# a merge time that cannot be read                                             #
# --------------------------------------------------------------------------- #


def _unread(cards: str) -> str:
    return proof_dispatch.RELEASE_UNREAD_COMMENT.format(cards=cards)


def test_a_merge_with_no_merged_at_is_refused_and_told_once(monkeypatch, capsys):
    card = Card(monkeypatch, GROOM, merged={**BUILD_MERGES, "DRE-5922": None})
    for minute in (22, 37, 52):
        tally = card.sweep(pt(2026, 10, 10, 19, minute))
        lines = _lines(capsys)
        assert card.fired == []
        assert tally.refused == 1 and tally.held == 0
        refusal = [x for x in lines if "condition 8" in x]
        assert refusal and "DRE-5922" in refusal[0] and "DRE-5921" not in refusal[0]
    told = card.plain()
    assert [_first(b) for b in told] == [_unread("DRE-5922")]
    assert card.holds() == []

    card.merged = dict(BUILD_MERGES)
    tally = card.sweep(pt(2026, 10, 10, 20, 7))
    assert len(card.plain()) == 1
    assert tally.held == 1
    holds = card.holds()
    assert len(holds) == 1 and "2026-10-11 06:00 PT" in holds[0]


def test_a_merged_at_that_does_not_parse_is_unread_too(monkeypatch, capsys):
    card = Card(monkeypatch, GROOM, merged={"DRE-5921": "yesterday",
                                            "DRE-5922": None})
    tally = card.sweep(pt(2026, 10, 10, 19, 22))
    assert card.fired == [] and tally.refused == 1
    assert [_first(b) for b in card.plain()] == [_unread("DRE-5921, DRE-5922")]


def test_a_new_set_of_unread_cards_is_told_again(monkeypatch, capsys):
    card = Card(monkeypatch, GROOM, merged={**BUILD_MERGES, "DRE-5922": None})
    card.sweep(pt(2026, 10, 10, 19, 22))
    card.merged = {"DRE-5921": None, "DRE-5922": None}
    card.sweep(pt(2026, 10, 10, 19, 37))
    card.sweep(pt(2026, 10, 10, 19, 52))
    assert [_first(b) for b in card.plain()] == [
        _unread("DRE-5922"), _unread("DRE-5921, DRE-5922")]


def test_an_unread_merge_time_not_live_prints_would_comment(monkeypatch, capsys):
    card = Card(monkeypatch, GROOM, merged={**BUILD_MERGES, "DRE-5922": None})
    before = list(card.thread)
    card.sweep(pt(2026, 10, 10, 19, 22), live=False)
    assert f"would: comment {CARD}" in capsys.readouterr().out
    assert card.thread == before


def test_a_merge_read_that_raises_is_refused_and_posts_nothing(monkeypatch, capsys):
    card = Card(monkeypatch, GROOM,
                merged={**BUILD_MERGES, "DRE-5922": RuntimeError("HTTP 502")})
    before = list(card.thread)
    tally = card.sweep(pt(2026, 10, 10, 19, 22))
    assert card.fired == []
    assert tally.refused == 1
    assert card.thread == before
    assert any("DRE-5922" in x and "HTTP 502" in x for x in _lines(capsys))
