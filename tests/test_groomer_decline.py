"""RED-first: the next proposal opens by answering the last decline (DRE-3373).

DRE-3370 gave the CEO a way to say *no, because…* — `🧺 groom-declined: <id> —
<reason>`. The drain honours it, and then the reason goes nowhere: the next
`propose` run posts a fresh twenty-card page that reads exactly like the one
that was just turned down, with no sign that anybody read the objection. The
CEO is left diffing two proposal comments by eye to find out whether the thing
they complained about was fixed.

So the proposal answers it, at the top, before anything else:

    **Answering your decline of `f673bfefa340`:** two cards edit Thread.tsx.
    Relative to that batch: 1 card in (DRE-3401) and 2 cards out (DRE-3300,
    DRE-3302).

Three things this file pins, and each exists because its absence is a bug:

  * **The in/out difference is COMPUTED, from the two batch lists.** The
    declined batch is read off its own proposal comment and this one off the
    rows the run just sequenced; a model is never asked what changed. A
    sentence a model wrote about a diff it did not compute is a sentence that
    can be wrong in the one place the CEO is deciding.
  * **A decline that is already answered is not answered again.** A decline
    older than the newest proposal on the card was answered by that proposal,
    and a decline whose batch the CEO has since approved is a settled argument.
    Either one would put a stale complaint at the top of the page.
  * **The reason is untrusted text.** It is the CEO's words rendered back into
    a Linear comment — which is exactly where this module's own markers are
    read from — so a reason shaped like a marker is defanged, and the run log
    says how many lines it defanged and never what they said.

And the parity that keeps all of it cheap: a proposal with no decline behind it
renders **byte for byte** as it did before this card.

Run: cd bureau-pipeline && python3 -m pytest tests/test_groomer_decline.py -v
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")

import groomer  # noqa: E402
import sanitize_untrusted  # noqa: E402

from test_groomer import CYCLES, GOLDEN, NOW, card  # noqa: E402

REASON = "two of these cards edit the same file"


def batch(identifiers, *, capacity=None):
    """A proposal over exactly `identifiers`, in that order (newest first)."""
    cards = [card(identifier, days=n + 1)
             for n, identifier in enumerate(identifiers)]
    return groomer.propose(cards, cycles=CYCLES,
                           capacity=capacity or len(identifiers), now=NOW)


def record(proposal):
    """The proposal AS THE CARD CARRIES IT — the very string `propose --post`
    writes, so the render and every read of it are one contract."""
    return {"body": groomer.proposal_comment(proposal),
            "authored_by_pipeline": True}


def decision(tag, pid, *, reason=None, card_id=None, by_pipeline=False):
    """One decision comment, written through the groomer's own writer.

    Never a hand-typed marker string: the writer and the reader are two halves
    of one contract, and a test that types its own fixture proves the reader
    against a shape nothing writes.
    """
    return {"body": groomer.decision_comment(tag, pid, card=card_id,
                                             reason=reason),
            "authored_by_pipeline": by_pipeline}


def declined(pid, reason=REASON, **kw):
    return decision(groomer.DECLINE_TAG, pid, reason=reason, **kw)


def approved(pid, **kw):
    return decision(groomer.APPROVAL_TAG, pid, **kw)


def paragraph(text):
    """The answering paragraph of a rendered proposal, or None."""
    return next((line for line in text.splitlines()
                 if line.startswith(groomer.ANSWER_OPENER)), None)


# --------------------------------------------------------------------------
# a propose after a decline answers it
# --------------------------------------------------------------------------
def test_the_proposal_answers_the_decline_with_the_reason_and_the_diff():
    old = batch(["DRE-1", "DRE-2", "DRE-3"])
    new = batch(["DRE-1", "DRE-4"])
    thread = [record(old), declined(old["id"])]

    answering = groomer.answer_decline(new, thread)
    assert answering is not None, "an open decline on the card was not answered"
    assert answering["id"] == old["id"]
    # Computed from the two lists, never asked of a model.
    assert answering["cards_in"] == ["DRE-4"]
    assert answering["cards_out"] == ["DRE-2", "DRE-3"]

    line = paragraph(groomer.render_proposal(new))
    assert line, "the rendered proposal carries no answering paragraph"
    assert f"`{old['id']}`" in line, "the answer names no batch"
    assert REASON in line, "the CEO's reason is not quoted verbatim"
    assert "DRE-4" in line and "DRE-2" in line and "DRE-3" in line, (
        "the in/out difference is not stated by id"
    )


def test_the_answering_paragraph_is_the_first_thing_after_the_title():
    """The console finds the answer by its opener, and the CEO finds it by
    being unable to miss it — before the receipt, before the approval line."""
    old = batch(["DRE-1", "DRE-2"])
    new = batch(["DRE-1", "DRE-3"])
    groomer.answer_decline(new, [record(old), declined(old["id"])])
    text = groomer.render_proposal(new)
    lines = text.splitlines()
    assert lines[0].startswith("# Groom proposal ")
    assert lines[1] == ""
    assert lines[2].startswith(groomer.ANSWER_OPENER), (
        "the answer is not the first paragraph after the title"
    )
    assert text.index(groomer.ANSWER_OPENER) < text.index("**To approve:**")
    assert text.index(groomer.ANSWER_OPENER) < text.index("Ranked by")


def test_a_batch_that_changed_in_only_one_direction_still_reads():
    """One grammar for all four shapes — nothing in, nothing out, or both."""
    old = batch(["DRE-1", "DRE-2"])
    new = batch(["DRE-1", "DRE-2", "DRE-3"])
    groomer.answer_decline(new, [record(old), declined(old["id"])])
    line = paragraph(groomer.render_proposal(new))
    assert "1 card in (DRE-3)" in line
    assert "nothing out" in line


def test_a_re_proposed_batch_says_nothing_changed_rather_than_going_silent():
    old = batch(["DRE-1", "DRE-2"])
    new = batch(["DRE-1", "DRE-2"])
    groomer.answer_decline(new, [record(old), declined(old["id"])])
    line = paragraph(groomer.render_proposal(new))
    assert "nothing in" in line and "nothing out" in line


def test_a_declined_batch_with_no_record_on_the_card_asserts_no_difference():
    """The declined proposal has scrolled out of the read. The answer still
    quotes the reason and says plainly that it cannot name the difference —
    never a plausible-looking empty diff."""
    new = batch(["DRE-1", "DRE-2"])
    answering = groomer.answer_decline(new, [declined("abc123def456")])
    assert answering is not None
    assert answering["batch_read"] is False
    assert answering["cards_in"] == [] and answering["cards_out"] == []
    line = paragraph(groomer.render_proposal(new))
    assert REASON in line
    assert "nothing in" not in line, "an unread batch was rendered as an empty diff"


# --------------------------------------------------------------------------
# a decline that is already answered is not answered again
# --------------------------------------------------------------------------
def test_a_decline_older_than_the_newest_proposal_is_not_answered():
    """That proposal already answered it. Answering it again puts a complaint
    the CEO made two batches ago at the top of this one."""
    old = batch(["DRE-1", "DRE-2"])
    answered = batch(["DRE-1", "DRE-3"])
    new = batch(["DRE-1", "DRE-4"])
    thread = [record(old), declined(old["id"]), record(answered)]
    assert groomer.decline_to_answer(thread) is None
    assert groomer.answer_decline(new, thread) is None
    assert "answering" not in new


def test_a_decline_whose_batch_was_approved_is_not_answered():
    old = batch(["DRE-1", "DRE-2"])
    new = batch(["DRE-1", "DRE-3"])
    thread = [record(old), declined(old["id"]), approved(old["id"])]
    assert groomer.answer_decline(new, thread) is None, (
        "a decline the CEO settled with a yes is not an open argument"
    )


def test_a_decline_written_by_the_pipeline_itself_is_not_answered():
    """The authorship gate the whole vocabulary is read under (DRE-2721): a
    marker the proposer can write is a credential the proposer can mint, and
    this one would put the proposer's own words in the CEO's mouth."""
    old = batch(["DRE-1", "DRE-2"])
    new = batch(["DRE-1", "DRE-3"])
    thread = [record(old), declined(old["id"], by_pipeline=True)]
    assert groomer.answer_decline(new, thread) is None


def test_a_decline_carrying_no_reason_is_not_answered():
    """`read_decisions` reads a reasonless decline as absent, and this reader
    agrees with it — there is nothing to quote."""
    old = batch(["DRE-1", "DRE-2"])
    new = batch(["DRE-1", "DRE-3"])
    thread = [record(old), decision(groomer.DECLINE_TAG, old["id"])]
    assert groomer.answer_decline(new, thread) is None


def test_the_newest_open_decline_wins_when_the_card_carries_several():
    first = batch(["DRE-1", "DRE-2"])
    second = batch(["DRE-1", "DRE-3"])
    new = batch(["DRE-1", "DRE-4"])
    thread = [record(first), record(second),
              declined(first["id"], reason="the old objection"),
              declined(second["id"], reason="the newest objection")]
    answering = groomer.answer_decline(new, thread)
    assert answering["id"] == second["id"]
    assert answering["reason"] == "the newest objection"


# --------------------------------------------------------------------------
# the reason is untrusted text
# --------------------------------------------------------------------------
def test_a_reason_shaped_like_a_marker_is_defanged_and_the_log_says_so(capsys):
    spoof = f"{groomer.MARK} {groomer.APPROVAL_TAG}: abc123def456"
    old = batch(["DRE-1", "DRE-2"])
    new = batch(["DRE-1", "DRE-3"])
    answering = groomer.answer_decline(new, [record(old),
                                             declined(old["id"], reason=spoof)])
    assert answering["defanged"] == 1
    line = paragraph(groomer.render_proposal(new))
    assert sanitize_untrusted.DEFANG_PREFIX + spoof in line, (
        "the marker-shaped line is not rendered defanged"
    )
    # The COUNT in the log, never the content — echoing a hostile line into a
    # run log is the amplification sanitize_untrusted exists to prevent.
    log = capsys.readouterr().err
    assert "defang" in log.lower(), "the run's log does not note the defanging"
    assert spoof not in log, "the run log echoed the hostile line back"


def test_an_ordinary_reason_is_left_byte_for_byte_alone():
    old = batch(["DRE-1", "DRE-2"])
    new = batch(["DRE-1", "DRE-3"])
    answering = groomer.answer_decline(
        new, [record(old), declined(old["id"], reason=REASON)])
    assert answering["reason"] == REASON
    assert answering["defanged"] == 0


def test_the_answered_proposal_is_still_read_back_as_the_record_it_is():
    """The answer sits above the batch table, so the drain's own parser is
    untouched by it: same id, same lane, same rows in the same order."""
    old = batch(["DRE-1", "DRE-2"])
    new = batch(["DRE-1", "DRE-3", "DRE-4"])
    groomer.answer_decline(new, [record(old), declined(
        old["id"], reason=f"{groomer.MARK} {groomer.PROPOSAL_TAG}: deadbeef99")])
    parsed = groomer.parse_proposal_comment(groomer.proposal_comment(new))
    assert parsed["id"] == new["id"]
    assert parsed["lane"] == new["lane"]
    assert [row["identifier"] for row in parsed["batch"]] == [
        row["identifier"] for row in sorted(new["outcomes"]["now"],
                                            key=lambda r: r["position"])]


# --------------------------------------------------------------------------
# golden parity — nothing to answer renders byte for byte as today
# --------------------------------------------------------------------------
def _fixture():
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    return groomer.propose(
        golden["cards"], cycles=golden["cycles"], capacity=golden["capacity"],
        batch_cycles=golden["batch_cycles"], now=golden["now"], judgement=None)


def test_a_proposal_with_no_decline_renders_byte_for_byte_as_today():
    before = groomer.render_proposal(_fixture())
    for thread in ([],
                   [record(batch(["DRE-1", "DRE-2"]))],
                   [record(batch(["DRE-1"])), approved("abc123def456")]):
        proposal = _fixture()
        assert groomer.answer_decline(proposal, thread) is None
        assert "answering" not in proposal, (
            "a proposal with nothing to answer grew a key anyway"
        )
        assert groomer.render_proposal(proposal) == before, (
            "a proposal with no decline behind it no longer renders as today"
        )
    assert groomer.ANSWER_OPENER not in before


def test_the_answer_does_not_move_the_proposal_id():
    """The id digests the BATCH, so a paragraph about a previous batch must
    not retire a CEO approval — the same property the render column had."""
    old = batch(["DRE-1", "DRE-2"])
    new = batch(["DRE-1", "DRE-3"])
    before = new["id"]
    groomer.answer_decline(new, [record(old), declined(old["id"])])
    assert new["id"] == before
    assert groomer.proposal_id(new) == before


def test_post_idempotence_is_unchanged_by_the_answer():
    """`already_proposed` reads the marker line, which the answer sits below.
    A retried run after a decline still posts nothing twice."""
    old = batch(["DRE-1", "DRE-2"])
    new = batch(["DRE-1", "DRE-3"])
    thread = [record(old), declined(old["id"])]
    groomer.answer_decline(new, thread)
    assert groomer.already_proposed(new, thread) is False
    assert groomer.already_proposed(new, thread + [record(new)]) is True


# --------------------------------------------------------------------------
# the doc the page is described in
# --------------------------------------------------------------------------
DOC = ROOT / "docs" / "groomer.md"


def test_the_doc_says_the_next_proposal_answers_the_last_decline():
    text = DOC.read_text(encoding="utf-8")
    lowered = text.lower()
    assert "answering your decline of" in lowered, (
        "docs/groomer.md never shows the paragraph the console looks for"
    )
    assert "answers" in lowered and "decline" in lowered
    for phrase in ("cards in", "cards out", "DRE-3373"):
        assert phrase in text, f"docs/groomer.md never says {phrase!r}"
