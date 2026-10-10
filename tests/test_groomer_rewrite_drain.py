"""RED-first: the drain honors the CEO's acceptance of a Rewrite first draft
(DRE-6667).

DRE-6666 puts a drafted updated body on each partly-solved card for the CEO
to accept, and nothing read his acceptance: the drain moved the Planning list,
cancelled the Cancel list and knew no third list. Now:

  * `🧺 groom-rewrite-accepted: <id> DRE-N` is a per-card decision, honored
    on the same terms as the others — a person's own, or the console's with a
    receipt that verifies, and only for the approved batch;
  * for an accepted card on the record's Rewrite first table the drain reads
    the card's own thread for the newest draft the PIPELINE wrote whose
    `body=` digest is the digest of the card's current description, posts
    `🧺 groom-rewritten: <id> from draft <draft id>` carrying the old body,
    writes the draft as the description, and moves the card to Planning —
    in that order, rationed by the free planner slots like any other card;
  * a draft-shaped comment from any other account is data, never a draft;
  * a Rewrite first card with no acceptance is held back and not written to;
  * the page tells the CEO what to type, because the console has no control
    for it.

Run: cd bureau-pipeline && python3 -m pytest tests/test_groomer_rewrite_drain.py -v
"""
from __future__ import annotations

import ast
import copy
import json
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import check_act_receipts  # noqa: E402
import console_receipt  # noqa: E402
import console_receipt_vectors as V  # noqa: E402
import groomer  # noqa: E402
import pipeline_act  # noqa: E402
import ready_lane_writers  # noqa: E402

from test_groomer_approval_gate import FakeOps as _GateOps  # noqa: E402
from test_groomer_console_receipt import OPENSSL, receipted  # noqa: E402
from test_groomer_two_lists import two_lists  # noqa: E402

PROPOSAL_CARD = "DRE-2683"
DOC = ROOT / "docs" / "groomer.md"
REGISTRY = ROOT / "config" / "pipeline-acts.json"

#: The two partly-solved cards on the Rewrite first table, in its order.
FIRST, SECOND = "DRE-901", "DRE-902"
REWRITE_CARDS = (FIRST, SECOND)
#: A pipeline draft posted under an earlier morning's proposal.
EARLIER = "0123456789ab"

REFUSED_WHY = "no draft of the pipeline's own for the card's current body"
NOT_ACCEPTED = "rewrite not accepted"


def _body(identifier: str) -> str:
    return (f"## The card\nThe old body of {identifier}.\n\n"
            f"## Acceptance criteria\n- [ ] the old thing, already on main")


def _draft(identifier: str) -> str:
    return f"## The card\nWhat remains of {identifier}, and nothing else."


def rewrite_proposal() -> dict:
    """The fifteen-and-five morning, plus two cards on Rewrite first."""
    proposal = two_lists()
    proposal["outcomes"][groomer.REWRITE] = [
        {"identifier": identifier, "title": f"Rewrite {identifier}",
         "repo": "agent-bureau", "epic": None, "band": 2, "position": n,
         "summary": "Part of it is already on main.",
         "draft": _draft(identifier),
         "body_digest": groomer.body_digest(_body(identifier)),
         "verify": {}}
        for n, identifier in enumerate(REWRITE_CARDS, 1)]
    return proposal


def _pid(proposal) -> str:
    return groomer.proposal_id(proposal)


def _record(proposal):
    return {"body": groomer.proposal_comment(proposal),
            "authored_by_pipeline": True}


def _decision(tag, proposal, *, card=None, reason=None, by_pipeline=False):
    return {"body": groomer.decision_comment(tag, _pid(proposal), card=card,
                                             reason=reason),
            "authored_by_pipeline": by_pipeline}


def _accept(proposal, card, **kw):
    return _decision(groomer.REWRITE_ACCEPT_TAG, proposal, card=card, **kw)


def _thread(proposal, *decisions):
    return [_record(proposal), _decision(groomer.APPROVAL_TAG, proposal),
            *decisions]


def _draft_comment(identifier, *, pid=EARLIER, digest=None, text=None,
                   by_pipeline=True):
    return {"body": groomer.rewrite_draft_comment(
                pid, digest or groomer.body_digest(_body(identifier)),
                text if text is not None else _draft(identifier)),
            "authored_by_pipeline": by_pipeline,
            "created_at": "2026-10-10T14:00:00.000Z"}


class FakeOps(_GateOps):
    """The approval-gate fixture, with each card's own thread and
    description, and ONE log of every write in the order the drain made it."""

    def __init__(self, comments, *, threads=None, bodies=None, **kwargs):
        super().__init__(comments, **kwargs)
        self.threads = {k: list(v) for k, v in (threads or {}).items()}
        self.bodies = dict(bodies or {})
        self.log: list[tuple] = []
        self.thread_reads: list[tuple[str, bool]] = []

    def comment_records(self, identifier, *, whole_thread=False):
        if identifier == PROPOSAL_CARD:
            return list(self.comments)
        self.thread_reads.append((identifier, whole_thread))
        return list(self.threads.get(identifier, []))

    def gql(self, query, variables=None):
        if query == groomer.DESCRIPTION_QUERY:
            return {"issue": {"description":
                              self.bodies.get((variables or {})["id"])}}
        if "cycles(" not in query:
            self.log.append(("cycle", (variables or {}).get("id")))
        return super().gql(query, variables)

    def set_description(self, identifier, body):
        self.log.append(("description", identifier, body))
        self.bodies[identifier] = body

    def add_label(self, identifier, label_name):
        self.log.append(("label", identifier, label_name))

    def cmd_state(self, identifier, state_name, *flags):
        self.log.append(("state", identifier, state_name))
        super().cmd_state(identifier, state_name, *flags)

    def cmd_comment(self, identifier, body, *flags):
        self.log.append(("comment", identifier, body))
        super().cmd_comment(identifier, body, *flags)


def _ops(proposal, *decisions, threads=None, bodies=None, **kw):
    bodies = bodies if bodies is not None else {
        i: _body(i) for i in REWRITE_CARDS}
    return FakeOps(_thread(proposal, *decisions), threads=threads,
                   bodies=bodies, **kw)


def _writes_to(ops, identifier):
    return [e for e in ops.log
            if e[1] in (identifier, f"uuid-{identifier}")]


def _rewritten_notes(ops, identifier):
    return [e[2] for e in ops.log if e[0] == "comment" and e[1] == identifier
            and e[2].startswith(f"{groomer.MARK} {groomer.REWRITTEN_TAG}:")]


def _drained(ops):
    records = [b for t, b in ops.written if t == PROPOSAL_CARD
               and b.startswith(f"{groomer.MARK} {groomer.DRAINED_TAG}:")]
    assert len(records) == 1, f"the drain wrote {len(records)} drained records"
    return records[0]


def _row(body, identifier):
    return next((ln for ln in body.splitlines()
                 if re.match(rf"^\|[^|]*\|\s*{identifier}\s*\|", ln.strip())),
                None)


def _cells(body, identifier):
    row = _row(body, identifier)
    assert row, f"{identifier} has no row in the drain's record:\n{body}"
    return [c.strip() for c in row.strip().strip("|").split("|")]


def _summary(body):
    return next(ln for ln in body.splitlines() if ln.startswith("moved: "))


# --------------------------------------------------------------------------
# the vocabulary
# --------------------------------------------------------------------------
def test_the_two_markers_are_the_contract_and_in_every_marker_set():
    assert groomer.REWRITE_ACCEPT_TAG == "groom-rewrite-accepted"
    assert groomer.REWRITTEN_TAG == "groom-rewritten"
    assert groomer.REWRITE_ACCEPT_TAG in groomer.DECISION_TAGS
    assert groomer.REWRITE_ACCEPT_TAG in groomer.PER_CARD_TAGS
    assert groomer.REWRITE_ACCEPT_TAG not in groomer.HELD_BACK_TAGS
    for tag in (groomer.REWRITE_ACCEPT_TAG, groomer.REWRITTEN_TAG):
        assert tag in groomer.ALL_MARKERS, tag


def test_read_decisions_returns_each_acceptance_under_rewritten():
    proposal = rewrite_proposal()
    decisions = groomer.read_decisions(_thread(
        proposal, _accept(proposal, FIRST)))
    assert decisions["rewritten"] == {
        FIRST: {"tag": groomer.REWRITE_ACCEPT_TAG, "reason": None}}
    assert FIRST not in decisions["excluded"]
    assert FIRST not in decisions["added"]


def test_the_newest_marker_about_a_card_wins_between_acceptance_and_the_rest():
    proposal = rewrite_proposal()
    later_excluded = groomer.read_decisions(_thread(
        proposal, _accept(proposal, FIRST),
        _decision(groomer.EXCLUDE_TAG, proposal, card=FIRST)))
    assert FIRST not in later_excluded["rewritten"]
    assert FIRST in later_excluded["excluded"]
    later_accepted = groomer.read_decisions(_thread(
        proposal, _decision(groomer.HOLD_TAG, proposal, card=FIRST),
        _accept(proposal, FIRST)))
    assert FIRST in later_accepted["rewritten"]
    assert FIRST not in later_accepted["excluded"]


def test_the_marker_is_anchored_at_the_start_of_the_comment():
    pid = "abc123def456"
    assert groomer.decision_match(groomer.REWRITE_ACCEPT_TAG,
                                  f"🧺 groom-rewrite-accepted: {pid} DRE-9")
    assert groomer.decision_match(groomer.REWRITE_ACCEPT_TAG,
                                  f"groom-rewrite-accepted: {pid} DRE-9")
    assert not groomer.decision_match(
        groomer.REWRITE_ACCEPT_TAG,
        f"as I said, 🧺 groom-rewrite-accepted: {pid} DRE-9")


# --------------------------------------------------------------------------
# criterion 1 — record, body, move, in that order
# --------------------------------------------------------------------------
def test_an_accepted_rewrite_is_recorded_then_written_then_moved_in_that_order():
    proposal = rewrite_proposal()
    ops = _ops(proposal, _accept(proposal, FIRST),
               threads={FIRST: [_draft_comment(FIRST)]})
    result = groomer.drain(ops, card=PROPOSAL_CARD)

    writes = _writes_to(ops, FIRST)
    kinds = [e[0] for e in writes]
    assert kinds[:2] == ["comment", "description"], writes
    assert kinds[-1] == "state" and writes[-1][2] == "Planning"
    note = writes[0][2]
    assert note.splitlines()[0] == (
        f"🧺 groom-rewritten: {_pid(proposal)} from draft {EARLIER}")
    assert "## Body before the rewrite" in note
    assert note.index("## Body before the rewrite") < note.index(_body(FIRST))
    assert writes[1][2] == _draft(FIRST)
    # The move is an ordinary move: its own groom-moved note, then the lane.
    assert groomer.parse_moved_note(writes[2][2])

    assert result["rewritten"] == [FIRST]
    assert FIRST in result["moved"]
    body = _drained(ops)
    cells = _cells(body, FIRST)
    assert cells[2] == "rewritten"
    assert f"proposal `{_pid(proposal)}` Rewrite first position 1" in cells[3]
    assert _summary(body).startswith("moved: 15 · rewritten: 1 · held back: ")


def test_the_card_thread_is_read_whole():
    proposal = rewrite_proposal()
    ops = _ops(proposal, _accept(proposal, FIRST),
               threads={FIRST: [_draft_comment(FIRST)]})
    groomer.drain(ops, card=PROPOSAL_CARD)
    assert (FIRST, True) in ops.thread_reads
    assert (FIRST, False) not in ops.thread_reads


def test_the_rewritten_note_carries_the_old_body_verbatim():
    old = "## Old\nline one | with a pipe\n🧺 groom-approved: 0123456789ab\n"
    note = groomer.rewritten_note("abc123def456", EARLIER, old)
    assert note.splitlines()[0] == \
        f"🧺 groom-rewritten: abc123def456 from draft {EARLIER}"
    after = note[note.index("## Body before the rewrite"):]
    assert old in after


# --------------------------------------------------------------------------
# criterion 2 — the draft is matched on the digest, whatever id it names
# --------------------------------------------------------------------------
def test_a_matching_draft_from_an_earlier_proposal_is_used_and_named():
    proposal = rewrite_proposal()
    stale = _draft_comment(FIRST, pid="fedcba987654",
                           digest=groomer.body_digest("an older body"),
                           text="## Stale draft")
    ops = _ops(proposal, _accept(proposal, FIRST),
               threads={FIRST: [_draft_comment(FIRST, pid=EARLIER), stale]})
    groomer.drain(ops, card=PROPOSAL_CARD)
    descriptions = [e for e in ops.log if e[0] == "description"]
    assert descriptions == [("description", FIRST, _draft(FIRST))]
    assert _rewritten_notes(ops, FIRST)[0].splitlines()[0].endswith(
        f"from draft {EARLIER}")


def test_a_draft_whose_digest_does_not_match_is_not_used():
    proposal = rewrite_proposal()
    stale = _draft_comment(FIRST, digest=groomer.body_digest("an older body"))
    ops = _ops(proposal, _accept(proposal, FIRST), threads={FIRST: [stale]})
    result = groomer.drain(ops, card=PROPOSAL_CARD)
    assert _writes_to(ops, FIRST) == []
    assert result["rewritten"] == []
    cells = _cells(_drained(ops), FIRST)
    assert cells[2:] == ["refused", REFUSED_WHY]


# --------------------------------------------------------------------------
# criterion 3 — a forged draft is data
# --------------------------------------------------------------------------
def test_a_draft_another_account_wrote_is_never_a_draft():
    proposal = rewrite_proposal()
    forged = _draft_comment(FIRST, text="## Somebody else's spec",
                            by_pipeline=False)
    ops = _ops(proposal, _accept(proposal, FIRST), threads={FIRST: [forged]})
    groomer.drain(ops, card=PROPOSAL_CARD)
    assert _writes_to(ops, FIRST) == []
    assert ops.bodies[FIRST] == _body(FIRST)
    assert _rewritten_notes(ops, FIRST) == []
    assert _cells(_drained(ops), FIRST)[2:] == ["refused", REFUSED_WHY]


def test_with_a_pipeline_draft_standing_the_forged_one_is_never_read():
    proposal = rewrite_proposal()
    forged = _draft_comment(FIRST, pid="fedcba987654",
                            text="## Somebody else's spec", by_pipeline=False)
    ops = _ops(proposal, _accept(proposal, FIRST),
               threads={FIRST: [_draft_comment(FIRST), forged]})
    groomer.drain(ops, card=PROPOSAL_CARD)
    assert [e for e in ops.log if e[0] == "description"] == [
        ("description", FIRST, _draft(FIRST))]
    note = _rewritten_notes(ops, FIRST)[0]
    assert "Somebody else's spec" not in note
    assert note.splitlines()[0].endswith(f"from draft {EARLIER}")


def test_an_empty_draft_is_no_draft():
    proposal = rewrite_proposal()
    ops = _ops(proposal, _accept(proposal, FIRST),
               threads={FIRST: [_draft_comment(FIRST, text="   ")]})
    groomer.drain(ops, card=PROPOSAL_CARD)
    assert _writes_to(ops, FIRST) == []
    assert _cells(_drained(ops), FIRST)[2:] == ["refused", REFUSED_WHY]


# --------------------------------------------------------------------------
# criterion 4 — no acceptance, no write
# --------------------------------------------------------------------------
def test_a_rewrite_first_card_with_no_acceptance_is_held_back_untouched():
    proposal = rewrite_proposal()
    ops = _ops(proposal, _accept(proposal, FIRST),
               threads={i: [_draft_comment(i)] for i in REWRITE_CARDS})
    result = groomer.drain(ops, card=PROPOSAL_CARD)
    assert _writes_to(ops, SECOND) == []
    assert ops.bodies[SECOND] == _body(SECOND)
    assert SECOND in result["held_back"]
    assert _cells(_drained(ops), SECOND)[2:] == ["held back", NOT_ACCEPTED]


# --------------------------------------------------------------------------
# criterion 5 — who may accept
# --------------------------------------------------------------------------
def test_an_acceptance_the_pipeline_wrote_without_a_receipt_is_ignored():
    proposal = rewrite_proposal()
    ops = _ops(proposal, _accept(proposal, FIRST, by_pipeline=True),
               threads={FIRST: [_draft_comment(FIRST)]})
    result = groomer.drain(ops, card=PROPOSAL_CARD)
    assert _writes_to(ops, FIRST) == []
    assert result["rewritten"] == []
    cells = _cells(_drained(ops), FIRST)
    assert cells[2] == "held back"
    assert cells[3].startswith(NOT_ACCEPTED)
    assert "written by the pipeline's own Linear identity" in cells[3]
    assert any(m["tag"] == groomer.REWRITE_ACCEPT_TAG
               and m["identifier"] == FIRST for m in result["refused"])


@pytest.fixture
def console_key(monkeypatch):
    if OPENSSL is None:
        if os.environ.get("CI"):
            pytest.fail("no Ed25519-capable openssl on a CI runner")
        pytest.skip("no Ed25519-capable openssl on this machine")
    monkeypatch.setenv("OPENSSL_BIN", OPENSSL)
    monkeypatch.setattr(console_receipt, "_OPENSSL", None, raising=False)
    monkeypatch.setattr(groomer, "_VERIFIER", console_receipt.Verifier(
        key_loader=lambda: console_receipt.PublicKey.from_b64(
            V.PUBLIC_KEY_B64)))


def test_an_acceptance_the_console_signed_is_honored(console_key):
    proposal = rewrite_proposal()
    marker = groomer.decision_comment(groomer.REWRITE_ACCEPT_TAG,
                                      _pid(proposal), card=FIRST)
    signed = receipted(marker, card=PROPOSAL_CARD, proposal=_pid(proposal))
    ops = _ops(proposal, signed, threads={FIRST: [_draft_comment(FIRST)]})
    result = groomer.drain(ops, card=PROPOSAL_CARD)
    assert result["rewritten"] == [FIRST]
    assert ("description", FIRST, _draft(FIRST)) in ops.log


# --------------------------------------------------------------------------
# criterion 6 — a stray acceptance, and no draft
# --------------------------------------------------------------------------
def test_an_acceptance_naming_a_card_on_no_list_is_ignored_and_named():
    proposal = rewrite_proposal()
    stray = "DRE-9999"
    ops = _ops(proposal, _accept(proposal, stray))
    result = groomer.drain(ops, card=PROPOSAL_CARD)
    assert _writes_to(ops, stray) == []
    cells = _cells(_drained(ops), stray)
    assert cells[2] == "refused"
    assert "not on the Rewrite first table" in cells[3]
    assert any(m["identifier"] == stray for m in result["refused"])


def test_an_acceptance_naming_a_planning_card_is_ignored_on_its_row():
    proposal = rewrite_proposal()
    planning = sorted(proposal["outcomes"]["now"],
                      key=lambda r: r["position"])[0]["identifier"]
    ops = _ops(proposal, _accept(proposal, planning))
    groomer.drain(ops, card=PROPOSAL_CARD)
    cells = _cells(_drained(ops), planning)
    assert cells[2] == "moved"
    assert "not on the Rewrite first table" in cells[3]
    assert [e for e in ops.log if e[0] == "description"] == []


def test_an_accepted_card_with_no_draft_at_all_is_refused_untouched():
    proposal = rewrite_proposal()
    ops = _ops(proposal, _accept(proposal, FIRST))
    result = groomer.drain(ops, card=PROPOSAL_CARD)
    assert _writes_to(ops, FIRST) == []
    assert _cells(_drained(ops), FIRST)[2:] == ["refused", REFUSED_WHY]
    assert any(m["identifier"] == FIRST for m in result["refused"])


def test_an_accepted_card_that_left_intake_is_already_gone_and_untouched():
    proposal = rewrite_proposal()
    ops = _ops(proposal, _accept(proposal, FIRST),
               threads={FIRST: [_draft_comment(FIRST)]},
               lanes={FIRST: "Done"})
    groomer.drain(ops, card=PROPOSAL_CARD)
    assert _writes_to(ops, FIRST) == []
    assert _cells(_drained(ops), FIRST)[2:] == ["already gone", "Done"]


# --------------------------------------------------------------------------
# criterion 7 — rationed with the rest, queued with its new body
# --------------------------------------------------------------------------
def test_an_accepted_card_past_the_free_slots_is_queued_with_its_new_body(monkeypatch):
    proposal = rewrite_proposal()
    batch = len(proposal["outcomes"]["now"])
    monkeypatch.setattr(groomer, "free_planner_slots",
                        lambda lops: (batch, batch + 1))
    ops = _ops(proposal, _accept(proposal, FIRST),
               threads={FIRST: [_draft_comment(FIRST)]})
    result = groomer.drain(ops, card=PROPOSAL_CARD)
    writes = _writes_to(ops, FIRST)
    assert [e[0] for e in writes] == ["comment", "description", "label"]
    assert writes[1][2] == _draft(FIRST)
    assert writes[2][2] == groomer.QUEUED_LABEL
    assert result["queued"] == [FIRST]
    assert result["rewritten"] == [FIRST]
    assert FIRST not in result["moved"]
    queued = [b for t, b in ops.written if t == PROPOSAL_CARD
              and b.startswith(f"{groomer.MARK} {groomer.QUEUED_TAG}:")]
    assert len(queued) == 1
    listed = groomer.parse_queued_record(queued[0])["cards"]
    assert [c["identifier"] for c in listed] == [FIRST]
    cells = _cells(_drained(ops), FIRST)
    assert cells[2] == "held back"
    assert cells[3].startswith("groom-queued: place 1 of 1")


# --------------------------------------------------------------------------
# criterion 8 — the move is the existing lane write
# --------------------------------------------------------------------------
def test_the_plan_carries_rewritten_rows_inside_moving_after_the_rest():
    proposal = rewrite_proposal()
    records = _thread(proposal, _accept(proposal, FIRST),
                      _decision(groomer.ADD_TAG, proposal, card="DRE-950"))
    decisions = groomer.read_decisions(records)
    record = groomer.proposal_record(_pid(proposal), records)
    drafts = {FIRST: {"draft": _draft(FIRST), "draft_pid": EARLIER,
                      "previous": _body(FIRST)}}
    plan = groomer._drain_plan(record, decisions, lane="Intake",
                               drafts=drafts)
    outcomes = [r["outcome"] for r in plan["moving"]]
    assert outcomes[-1] == "rewritten"
    assert outcomes[-2] == "added"
    assert set(outcomes[:-2]) == {"moved"}
    assert plan["moving"][-1]["identifier"] == FIRST
    assert plan["moving"][-1]["draft"] == _draft(FIRST)


def test_no_new_lane_write_site_in_the_groomer():
    tree = ast.parse((ROOT / "scripts" / "groomer.py").read_text("utf-8"))
    sites = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and getattr(node.func, "attr", None) == "cmd_state"]
    assert len(sites) == 2
    # What `ready_lane_writers.py check` reads off the tree: the groomer's
    # writes are the drain's two, each attributed to its own destination.
    found = [w for w in ready_lane_writers.writes()
             if w.where.startswith("scripts/groomer.py:")]
    assert sorted((w.lane, w.expression) for w in found) == [
        ("Canceled", "CANCEL_TO"), ("Planning", "to")]


# --------------------------------------------------------------------------
# criterion 9 — one call site, declared beside the draft's row
# --------------------------------------------------------------------------
def _calls_of(name: str) -> list[tuple[str, int, str]]:
    found = []
    for path in sorted((ROOT / "scripts").glob("*.py")):
        source = path.read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if (isinstance(node, ast.Call)
                    and getattr(node.func, "id",
                                getattr(node.func, "attr", None)) == name):
                found.append((path.name, node.lineno,
                              ast.get_source_segment(source, node)))
    return found


def test_the_note_is_posted_from_exactly_one_call_site_in_the_drain():
    calls = _calls_of("rewritten_note")
    assert len(calls) == 1, calls
    assert calls[0][0] == "groomer.py"
    posts = [c for c in _calls_of("cmd_comment") if "rewritten_note(" in c[2]]
    assert len(posts) == 1
    assert posts[0][2].startswith(
        'lops.cmd_comment(row["identifier"], rewritten_note(')
    source = (ROOT / "scripts" / "groomer.py").read_text(encoding="utf-8")
    drain = source[source.index("def _drain("):source.index("def _drain_plan(")]
    assert 'lops.cmd_comment(row["identifier"], rewritten_note(' in drain
    assert drain.index("rewritten_note(") < drain.index("set_description(")


def test_the_registry_declares_the_note_directly_after_the_drafts_row():
    rows = json.loads(REGISTRY.read_text(encoding="utf-8"))["unconverted"]
    at = [n for n, r in enumerate(rows) if r["anchor"] == "rewritten_note("]
    assert len(at) == 1
    row = rows[at[0]]
    assert row["file"] == "scripts/groomer.py"
    assert row["kind"] == "not-an-act"
    assert "body" in row["means"] and "rewrite" in row["means"]
    assert "CEO" in row["why"] and "bookkeeping" in row["why"]
    assert rows[at[0] - 1]["anchor"] == "rewrite_draft_comment("


def test_the_receipt_guard_passes_and_fails_naming_the_site_without_the_row():
    assert check_act_receipts.problems() == []
    doc = copy.deepcopy(pipeline_act.load())
    doc["unconverted"] = [r for r in doc["unconverted"]
                          if r.get("anchor") != "rewritten_note("]
    found = check_act_receipts.problems(doc)
    assert found, "removing the row left the note's call site undeclared"
    [(_, line, _)] = [c for c in _calls_of("cmd_comment")
                      if "rewritten_note(" in c[2]]
    assert any(f"scripts/groomer.py:{line} " in str(p) for p in found), found


# --------------------------------------------------------------------------
# criterion 10 — the page says what to type
# --------------------------------------------------------------------------
def _say_more(text):
    return next(ln for ln in text.splitlines()
                if ln.startswith("**To say more than yes:**"))


def test_the_say_more_line_carries_the_acceptance_clause():
    proposal = rewrite_proposal()
    line = _say_more(groomer.render_proposal(proposal))
    assert (f"`🧺 groom-rewrite-accepted: {proposal['id']} DRE-N` accepts the "
            f"draft posted on card DRE-N and sends it to Planning with the new "
            f"body") in line


def test_the_rewrite_first_table_carries_the_accept_line_under_it():
    proposal = rewrite_proposal()
    text = groomer.render_proposal(proposal)
    section = groomer._section(text, groomer.REWRITE_HEADING)
    accept = [ln for ln in section if ln.startswith("To accept a draft:")]
    assert accept == [
        f"To accept a draft: comment `🧺 groom-rewrite-accepted: "
        f"{proposal['id']} DRE-N` on this card, with the card's number in "
        f"place of DRE-N; the draft is the newest `🧺 groom-rewrite-draft` "
        f"comment on that card."]
    last_row = max(n for n, ln in enumerate(section) if ln.startswith("| "))
    assert section.index(accept[0]) > last_row


def test_a_page_with_no_rewrite_first_table_has_the_clause_and_not_the_line():
    proposal = two_lists()
    text = groomer.render_proposal(proposal)
    assert "groom-rewrite-accepted" in _say_more(text)
    assert "To accept a draft:" not in text


# --------------------------------------------------------------------------
# criterion 11 — the doc
# --------------------------------------------------------------------------
def test_the_doc_states_the_marker_the_gate_and_the_three_writes():
    flat = " ".join(DOC.read_text(encoding="utf-8").split())
    assert "`🧺 groom-rewrite-accepted: <id> DRE-N`" in flat
    assert "`🧺 groom-rewritten: <id> from draft <draft id>`" in flat
    assert "types it by hand on the standing card" in flat
    assert "the console offers no control for it" in flat
    assert "`authored_by_pipeline`" in flat
    assert "equals the digest of the card's current description" in flat
    assert REFUSED_WHY in flat and NOT_ACCEPTED in flat
    assert ("posts the `groom-rewritten` record, writes the draft as the "
            "description, then moves the card") in flat
