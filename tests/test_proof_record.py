"""The proof record's readers live in one leaf module (DRE-6141).

On 2026-10-07 three proof records whose rows read `Not observed.` merged on
green CI and the critic's APPROVE, and DRE-5919's close-on-merge marked their
cards Done. The merge gate and the PROOF close both now open the record. They
read it through `scripts/proof_record.py`, which holds:

  * the proof-record branch rule, moved from `proof_dispatch`;
  * the criterion-table reader, moved from `hygiene_done`;
  * the hold-discharge reader, moved from `proof_dispatch` as `open_holds`;
  * the record finder, new: the ONE `.md` file the pull request ADDS under
    `docs/` or `architecture/` — the roots `hygiene_done` reads records
    from — read at a given sha.

Moved, never copied: `hygiene_done.reading is proof_record.reading`. And it is
a leaf: `reconcile` reads `os.environ["REPO"]` at import, and neither the
merge gate's step nor linear-sync's `Card → Done` step sets it, so nothing here
may import `reconcile` or `hygiene`.

Run: cd bureau-pipeline && python3 -m pytest tests/test_proof_record.py -v
"""

from __future__ import annotations

import base64
import json
import os
import subprocess  # nosec B404 — fixed argv, our own script
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import hygiene_done  # noqa: E402
import linear_ops  # noqa: E402
import proof_dispatch  # noqa: E402
import proof_reach  # noqa: E402
import proof_record  # noqa: E402
import spoken_thread  # noqa: E402

REPO = "dreadnought-foundry/agent-bureau"
SHA = "c" * 40
RECORD_PATH = "architecture/proofs/planners-at-once.md"

MET = """# Proof: planners at once

| # | Criterion | Result |
|---|---|---|
| 1 | Two planners run at once | Met — runs 1 and 2 overlapped 09:01–09:04 PT |
| 2 | Each plan lands on its own epic | Observed. |
| 3 | The record is merged to main | Pending the merge |
"""

NOT_OBSERVED = """# Proof: briefing preferences

| Criterion | Result |
|---|---|
| The briefing honours the preference | Met |
| The CEO's press changes the briefing | Not observed. |
| The console shows the new order | Not met. |
"""

NO_TABLE = "# Proof\n\nWe watched it and it worked.\n"

CLOSING_ONLY = """# Proof

| Criterion | Result |
|---|---|
| The record is merged to main | Pending |
"""


def added(path: str, change: str = "ADDED") -> dict:
    """One entry of `gh pr view --json files`, as gh renders it."""
    return {"path": path, "additions": 12, "deletions": 0, "changeType": change}


# --------------------------------------------------------------------------
# One reader, moved — never copied
# --------------------------------------------------------------------------
class TestOneReader:
    def test_hygiene_done_reads_the_table_through_proof_record(self):
        for name in ("reading", "criterion_rows", "is_closing_row", "row_met",
                     "Reading", "_row"):
            assert getattr(hygiene_done, name) is getattr(proof_record, name), name
        for name in ("CLOSING_ROW_WORDS", "MET_WORDS", "HEDGES"):
            assert getattr(hygiene_done, name) is getattr(proof_record, name), name
        # Where a record may live is one answer too: the gate and the hygiene
        # lane must never disagree about whether a pull request carries one.
        assert hygiene_done.RECORD_ROOTS is proof_record.RECORD_DIRS

    def test_proof_dispatch_reads_holds_and_branches_through_proof_record(self):
        assert proof_dispatch._open_holds is proof_record.open_holds
        assert proof_dispatch.proof_record_branch is proof_record.proof_record_branch
        for name in ("CEO_PRESS", "HOLD_MARK", "OBSERVED_MARK"):
            assert getattr(proof_dispatch, name) is getattr(proof_record, name), name

    def test_no_second_definition_survives_in_either_module(self):
        """A `def reading` or `def _open_holds` written back into either file
        would be a second judgment that can drift from this one."""
        for module, names in (
            ("hygiene_done.py", ("criterion_rows", "is_closing_row", "row_met",
                                 "reading", "_row", "_cells")),
            ("proof_dispatch.py", ("_open_holds", "proof_record_branch")),
        ):
            text = (SCRIPTS / module).read_text(encoding="utf-8")
            for name in names:
                assert f"\ndef {name}(" not in text, (module, name)


# --------------------------------------------------------------------------
# The branch rule
# --------------------------------------------------------------------------
@pytest.mark.parametrize("ref, expected", [
    ("agent/DRE-5798-proof-record", True),
    ("agent/DRE-5798-proof-record-2", False),
    ("agent/DRE-5798-planner-fix", False),
    ("repair/DRE-5798-proof-record", False),
    ("", False),
    (None, False),
])
def test_the_proof_record_branch(ref, expected):
    assert proof_record.proof_record_branch(ref) is expected


# --------------------------------------------------------------------------
# The record finder
# --------------------------------------------------------------------------
class TestTheRecordFinder:
    def test_the_one_added_markdown_file_under_proofs_is_the_record(self):
        files = [added("scripts/x.py", "MODIFIED"), added(RECORD_PATH),
                 added("architecture/proofs/shot.png")]
        assert proof_record.find_record(files) == (RECORD_PATH, None)

    def test_an_audit_is_a_record_too(self):
        path = "architecture/audits/repo-map-resolution.md"
        assert proof_record.find_record([added(path)]) == (path, None)

    def test_a_record_under_docs_is_a_record_too(self):
        """Most of bureau-pipeline's proof records live under `docs/`: #772,
        on `agent/DRE-5843-proof-record`, added only
        `docs/claude-limit-recovery-proof-2026-10.md`. A finder that missed it
        would hold that record forever, with nothing to move it."""
        path = "docs/claude-limit-recovery-proof-2026-10.md"
        assert proof_record.find_record([added(path)]) == (path, None)

    def test_no_match_is_no_record(self):
        path, why = proof_record.find_record([added("README.md"),
                                              added("scripts/x.py", "MODIFIED")])
        assert path is None
        assert "docs/" in why and "architecture/" in why

    def test_two_matches_are_no_record_and_both_are_named(self):
        other = "docs/second-proof.md"
        path, why = proof_record.find_record([added(RECORD_PATH), added(other)])
        assert path is None
        assert RECORD_PATH in why and other in why

    def test_only_a_modified_record_is_no_record_and_is_named(self):
        path, why = proof_record.find_record([added(RECORD_PATH, "MODIFIED")])
        assert path is None
        assert RECORD_PATH in why

    def test_a_record_outside_the_roots_or_not_markdown_is_not_one(self):
        for entry in (added("proofs/planners.md"), added("architecture/proofs/a.txt"),
                      added("docsx/a.md"), added("scripts/docs/a.md")):
            assert proof_record.find_record([entry])[0] is None, entry

    def test_an_unreadable_file_list_is_no_record(self):
        for files in (None, "files", [None, 3]):
            path, why = proof_record.find_record(files)
            assert path is None and why, files


class _Gh:
    """A `gh` seam: answers the contents API for one path at one ref."""

    def __init__(self, text: str | None = MET, fail: bool = False):
        self.text, self.fail, self.calls = text, fail, []

    def __call__(self, args):
        self.calls.append(list(args))
        if self.fail:
            raise RuntimeError("HTTP 502: Bad Gateway")
        content = base64.b64encode((self.text or "").encode()).decode()
        return json.dumps({"encoding": "base64", "content": content})


class TestFetch:
    def test_the_record_is_read_at_the_given_sha(self):
        gh = _Gh(MET)
        record = proof_record.fetch(REPO, [added(RECORD_PATH)], SHA, gh=gh)
        assert record == proof_record.Record(RECORD_PATH, MET, None)
        assert gh.calls == [["api", f"repos/{REPO}/contents/{RECORD_PATH}?ref={SHA}"]]

    def test_a_failed_read_is_no_text_and_says_why(self):
        record = proof_record.fetch(REPO, [added(RECORD_PATH)], SHA, gh=_Gh(fail=True))
        assert record.text is None and record.path == RECORD_PATH
        assert "502" in record.detail

    def test_no_record_makes_no_read(self):
        gh = _Gh()
        record = proof_record.fetch(REPO, [], SHA, gh=gh)
        assert record.text is None and record.path is None and record.detail
        assert gh.calls == []


# --------------------------------------------------------------------------
# The one judgment both halves make
# --------------------------------------------------------------------------
class TestShortfall:
    def rec(self, text):
        return proof_record.Record(RECORD_PATH, text, None)

    def test_every_judged_row_met_is_no_shortfall(self):
        assert proof_record.shortfall(self.rec(MET)) is None

    def test_a_row_not_observed_or_not_met_is_named(self):
        why = proof_record.shortfall(self.rec(NOT_OBSERVED))
        assert RECORD_PATH in why
        assert "The CEO's press changes the briefing" in why
        assert "Not observed." in why
        assert "The console shows the new order" in why
        assert "honours the preference" not in why

    def test_no_criterion_table(self):
        assert "no criterion table" in proof_record.shortfall(self.rec(NO_TABLE))

    def test_no_judged_row(self):
        assert "no row but its closing step" in proof_record.shortfall(self.rec(CLOSING_ONLY))

    def test_no_record(self):
        why = proof_record.shortfall(proof_record.Record(None, None, "it adds two"))
        assert why and "it adds two" in why


# --------------------------------------------------------------------------
# A criterion the operator accepted as overtaken (DRE-6244)
# --------------------------------------------------------------------------
LINEAR_DECISION = "https://linear.app/dreadnoughtfoundry/issue/DRE-1/x#comment-abc"
GITHUB_DECISION = "https://github.com/dreadnought-foundry/agent-bureau/pull/3339#issuecomment-4242"
OVERTAKEN = "The proof-reader is refused on the Subscriptions page"


def accepted_record(result: str) -> str:
    return f"""# Proof: subscriptions

| Criterion | Result |
|---|---|
| The identity heading shows the tenant | Met — screenshot 1 |
| The plan tab lists the plan | Observed. |
| {OVERTAKEN} | {result} |
| The record is merged to main | Pending |
"""


class TestAnAcceptedRow:
    def rec(self, text):
        return proof_record.Record(RECORD_PATH, text, None)

    @pytest.mark.parametrize("link", [
        LINEAR_DECISION,
        GITHUB_DECISION,
        "linear.app/dreadnoughtfoundry/issue/DRE-5883/proof-reader-reads#comment-9f3e2a1b",
        "github.com/dreadnought-foundry/agent-bureau/pull/3339#issuecomment-4242",
        f"[the decision]({LINEAR_DECISION})",
        f"[the decision]({GITHUB_DECISION})",
    ])
    def test_an_accepted_row_with_a_linked_decision_passes(self, link):
        result = f"ACCEPTED by operator decision — DRE-5883 made it a platform reader ({link})"
        text = accepted_record(result)
        assert proof_record.shortfall(self.rec(text)) is None
        read = proof_record.reading(text)
        assert read.accepted == [(OVERTAKEN, result)]
        assert (OVERTAKEN, result) not in read.met
        assert read.unmet == []

    def test_emphasis_and_case_are_read_the_way_met_words_are(self):
        for opening in ("**ACCEPTED by operator decision**", "Accepted by operator decision",
                        "_accepted by operator decision_"):
            result = f"{opening} — overtaken ({LINEAR_DECISION})"
            assert proof_record.reading(accepted_record(result)).accepted == [(OVERTAKEN, result)]

    def test_the_summary_names_the_accepted_row(self):
        result = f"ACCEPTED by operator decision — overtaken ({LINEAR_DECISION})"
        read = proof_record.reading(accepted_record(result))
        assert proof_record.summary(read) == (
            f"2 rows met, 1 accepted by operator decision: “{OVERTAKEN}”")

    def test_the_summary_with_nothing_accepted_reads_as_today(self):
        assert proof_record.summary(proof_record.reading(MET)) == "2 rows met"

    def test_the_summary_names_every_accepted_row_cut_like_a_row(self):
        long = "A criterion " + "that runs on and on " * 6
        text = (f"| Criterion | Result |\n|---|---|\n| it ran | Met |\n"
                f"| first overtaken | ACCEPTED by operator decision ({LINEAR_DECISION}) |\n"
                f"| {long} | ACCEPTED by operator decision ({GITHUB_DECISION}) |\n")
        said = proof_record.summary(proof_record.reading(text))
        cut = " ".join(long.split())[:69].rstrip() + "…"
        assert said == (f"1 rows met, 2 accepted by operator decision: "
                        f"“first overtaken”; “{cut}”")

    def test_a_table_of_only_accepted_rows_passes(self):
        text = (f"| Criterion | Result |\n|---|---|\n"
                f"| {OVERTAKEN} | ACCEPTED by operator decision ({LINEAR_DECISION}) |\n"
                f"| The record is merged to main | Pending |\n")
        assert proof_record.shortfall(self.rec(text)) is None

    def test_the_closing_step_alone_is_still_refused(self):
        assert "no row but its closing step" in proof_record.shortfall(self.rec(CLOSING_ONLY))

    def test_an_accepted_row_with_no_link_is_held_and_says_why(self):
        result = "ACCEPTED by operator decision — DRE-5883"
        why = proof_record.shortfall(self.rec(accepted_record(result)))
        assert why == (f"{RECORD_PATH} has 1 row(s) not met: “{OVERTAKEN}” reads "
                       f"“{result}” — accepted without a linked decision")
        read = proof_record.reading(accepted_record(result))
        assert read.accepted == [] and read.unmet == [(OVERTAKEN, result)]

    @pytest.mark.parametrize("link", [
        "https://linear.app/dreadnoughtfoundry/issue/DRE-5883/proof-reader-reads",
        "https://linear.app/dreadnoughtfoundry/project/x#comment-abc",
        "https://github.com/dreadnought-foundry/agent-bureau/pull/3339",
        "https://github.com/dreadnought-foundry/agent-bureau/issues/3339#issuecomment-4242",
        "https://evillinear.app/dreadnoughtfoundry/issue/DRE-1/x#comment-abc",
        "https://example.com/decision",
    ])
    def test_a_link_of_any_other_shape_is_no_decision(self, link):
        result = f"ACCEPTED by operator decision ({link})"
        why = proof_record.shortfall(self.rec(accepted_record(result)))
        assert why is not None and "accepted without a linked decision" in why

    def test_the_link_must_be_in_the_same_cell(self):
        text = (f"| Criterion | Result | Notes |\n|---|---|---|\n"
                f"| {OVERTAKEN} | ACCEPTED by operator decision | {LINEAR_DECISION} |\n")
        why = proof_record.shortfall(self.rec(text))
        assert why is not None and "accepted without a linked decision" in why

    def test_a_link_alone_accepts_nothing(self):
        result = f"Not met — but see {LINEAR_DECISION}"
        why = proof_record.shortfall(self.rec(accepted_record(result)))
        assert why is not None and "accepted without a linked decision" not in why

    @pytest.mark.parametrize("result", [
        "NOT MET as written",
        "Not observed.",
        "ACCEPTED by operator decision",
        "**ACCEPTED by operator decision** — overtaken",
        # every hedge tests/test_hygiene_done.py's parametrized hold cases list
        "Met, but only for the first card", "Pass with caveats", "Yes and no",
        "**Met** — but not on agent-bureau", "Observed only once",
    ])
    def test_what_held_before_still_holds(self, result):
        why = proof_record.shortfall(self.rec(accepted_record(result)))
        assert why is not None and OVERTAKEN in why

    def test_no_word_was_added_to_the_met_words(self):
        assert proof_record.MET_WORDS == ("met", "holds", "observed", "proven", "pass", "yes")


# --------------------------------------------------------------------------
# A row waiting on a later event (DRE-6488)
# --------------------------------------------------------------------------
class TestARowWaitingOnAnEvent:
    @pytest.mark.parametrize("result, event", [
        ("Not observed. waiting for the 2026-10-14 release: the tag moves",
         "the 2026-10-14 release: the tag moves"),
        ("NOT OBSERVED. Waiting For the nightly run at 02:07 PT",
         "the nightly run at 02:07 PT"),
        ("**Not observed.** *waiting for* the 2026-10-20 date: the reminder fires",
         "the 2026-10-20 date: the reminder fires"),
        ("Not observed.  waiting for\tthe scheduled sweep", "the scheduled sweep"),
    ])
    def test_the_event_is_named_after_waiting_for(self, result, event):
        assert proof_record.row_waiting(result) == event

    @pytest.mark.parametrize("result", [
        "Not observed. needs a browser on a local run of the released commit",
        "Not observed. aws: none — waiting for a role is not an event",
        "Not observed. the Linear request cap of 40 was reached",
        "Not met. waiting for the release did not help",
        "Not met.",
        "Met. waiting for nothing",
        "Met.",
        "ACCEPTED by operator decision — overtaken "
        "(https://linear.app/dreadnoughtfoundry/issue/DRE-6000/x#comment-abc)",
        "Not observed. waiting for",
        "waiting for the 2026-10-14 release",
        "",
    ])
    def test_nothing_else_is_a_waiting_row(self, result):
        assert proof_record.row_waiting(result) is None

    def test_the_brief_carries_the_shape_beside_its_not_observed_paragraph(self):
        brief = (ROOT / "briefs" / "proof.md").read_text(encoding="utf-8")
        record = brief.split("## The record", 1)[1].split("\n## ", 1)[0]
        assert "Not observed. waiting for <the event>" in record
        at = record.index("A row you could not observe says `Not observed.`")
        assert record.index("Not observed. waiting for <the event>") > at


# --------------------------------------------------------------------------
# A row the run cannot observe as worded carries a cause (DRE-6607)
# --------------------------------------------------------------------------
CAUSES = list(proof_reach.causes())
CAUSED = """# Proof: the reach

| Criterion | Result |
|---|---|
| The briefing honours the preference | Met |
| The nightly reads the repo variable | Not observed. cause: out-of-reach — `gh variable get` answered 403 |
| The stale branch is deleted | Not observed. cause: state-gone — the branch was gone at 09:02 PT |
"""


def with_row(text: str, criterion: str, result: str) -> str:
    return text + f"| {criterion} | {result} |\n"


class TestARowWithACause:
    def test_the_cause_words_are_proof_reachs_four(self):
        assert CAUSES == ["out-of-reach", "state-gone", "misdescribed",
                          "operator-step-unblocked"]
        assert proof_record.CAUSE_OPENING == "Not observed. cause:"

    @pytest.mark.parametrize("word", CAUSES)
    @pytest.mark.parametrize("shape", [
        "Not observed. cause: {w} — the run saw a 403",
        "**Not observed.** cause: `{w}` — the run saw a 403",
        "**Not observed. cause: {w}** — the run saw a 403",
        "Not observed. cause: `{w}` — the run saw a 403",
    ])
    def test_each_of_the_four_words_is_read(self, word, shape):
        assert proof_record.row_cause(shape.format(w=word)) == word

    @pytest.mark.parametrize("result", [
        "Not observed. waiting for the 2026-10-14 release",
        "Not observed. needs the CEO's press: the Approve button on the plan",
        "Not met.",
        "Not observed.",
        "Not observed. cause: impossible — the run saw a 403",
        "Not observed. cause: state-gone",
        "Not observed. cause: state-gone —",
        "Not observed. cause: — the run saw a 403",
        "Not observed. the run saw a 403, cause: state-gone — gone",
        "Met. cause: state-gone — gone",
        "cause: state-gone — gone",
        "",
    ])
    def test_nothing_else_is_a_cause(self, result):
        assert proof_record.row_cause(result) is None

    def test_a_cause_row_is_neither_waiting_nor_accepted_nor_met(self):
        result = "Not observed. cause: state-gone — the branch was gone"
        assert proof_record.row_waiting(result) is None
        assert proof_record.row_accepted(result) is False
        assert proof_record.row_met(result) is False

    def test_an_unknown_word_is_named_with_the_four(self):
        said = proof_record.unmet_row("The branch is deleted",
                                      "Not observed. cause: impossible — the run saw a 403")
        assert "“impossible” is not one of the four" in said
        assert ", ".join(CAUSES) in said
        assert said.startswith("“The branch is deleted” reads “Not observed. cause: impossible")

    def test_a_known_word_or_no_cause_adds_nothing(self):
        for result in ("Not observed. cause: state-gone — gone", "Not observed.", "Not met."):
            assert proof_record.unmet_row("c", result) == proof_record._row("c", result)

    def test_the_caused_rows_are_read_and_still_unmet(self):
        read = proof_record.reading(CAUSED)
        assert len(read.unmet) == 2
        assert read.caused == [("The nightly reads the repo variable", "out-of-reach"),
                               ("The stale branch is deleted", "state-gone")]
        assert proof_record.all_unmet_caused(read) is True

    def test_shortfall_still_holds_the_caused_record_and_names_the_cause(self):
        why = proof_record.shortfall(proof_record.Record(RECORD_PATH, CAUSED, None))
        assert why is not None and "2 row(s) not met" in why
        assert "reads “Not observed. cause: state-gone —" in why
        assert "reads “Not observed. cause: out-of-reach —" in why

    def test_the_summary_is_unchanged(self):
        assert proof_record.summary(proof_record.reading(CAUSED)) == "1 rows met"

    @pytest.mark.parametrize("result", [
        "Not observed.",
        "Not met.",
        "Not observed. waiting for the 2026-10-14 release",
        "Not observed. cause: impossible — the run saw a 403",
        "ACCEPTED by operator decision — overtaken",
    ])
    def test_one_unmet_row_without_a_cause_makes_it_false(self, result):
        read = proof_record.reading(with_row(CAUSED, "The console shows the order", result))
        assert len(read.unmet) == 3
        assert proof_record.all_unmet_caused(read) is False

    def test_every_row_met_is_false(self):
        read = proof_record.reading(MET)
        assert read.unmet == [] and read.caused == []
        assert proof_record.all_unmet_caused(read) is False

    def test_no_table_is_false(self):
        read = proof_record.reading(NO_TABLE)
        assert read.caused == [] and proof_record.all_unmet_caused(read) is False

    def test_an_accepted_row_beside_caused_ones_leaves_it_true(self):
        accepted = ("ACCEPTED by operator decision — overtaken "
                    "(https://linear.app/dreadnoughtfoundry/issue/DRE-6000/x#comment-abc)")
        read = proof_record.reading(with_row(CAUSED, "The console shows the order", accepted))
        assert len(read.accepted) == 1
        assert proof_record.all_unmet_caused(read) is True


# --------------------------------------------------------------------------
# The hold-discharge reader
# --------------------------------------------------------------------------
def voice(kind, body):
    return spoken_thread.Voice(kind, f"{kind} label", "2026-10-07T14:45:00Z", body)


HOLD = f"{proof_record.HOLD_MARK} the planner overlap — needs a second epic approved"
CEO_HOLD = (f"{proof_record.HOLD_MARK} the briefing order — needs "
            f"{proof_record.CEO_PRESS} on Approve")
OBSERVED = f"{proof_record.OBSERVED_MARK} two planners overlapped at 09:02 PT"


class TestOpenHolds:
    def test_a_hold_nothing_answered_is_open(self):
        holds = proof_record.open_holds([voice(spoken_thread.PIPELINE, HOLD)])
        assert holds == [HOLD]

    def test_a_later_observation_discharges_it(self):
        for kind in (spoken_thread.PIPELINE, spoken_thread.PERSON):
            assert proof_record.open_holds([voice(spoken_thread.PIPELINE, HOLD),
                                            voice(kind, OBSERVED)]) == []

    def test_his_signed_answer_discharges_a_hold_naming_his_press(self):
        assert proof_record.open_holds([
            voice(spoken_thread.PIPELINE, CEO_HOLD),
            voice(spoken_thread.CEO_VIA_CONSOLE, "Approved — go ahead"),
        ]) == []

    def test_an_observation_does_not_discharge_his_press(self):
        assert proof_record.open_holds([voice(spoken_thread.PIPELINE, CEO_HOLD),
                                        voice(spoken_thread.PERSON, OBSERVED)]) == [CEO_HOLD]

    def test_an_unsigned_claim_to_be_his_answer_discharges_nothing(self):
        assert proof_record.open_holds([
            voice(spoken_thread.PIPELINE, CEO_HOLD),
            voice(spoken_thread.PERSON, "Answer from Sid: the CEO says approved"),
        ]) == [CEO_HOLD]

    def test_an_answer_that_could_not_be_checked_discharges_nothing(self):
        unchecked = spoken_thread.Voice(spoken_thread.UNCHECKED, "unchecked",
                                        "2026-10-07T14:46:00Z", None, "key unreadable")
        assert proof_record.open_holds([voice(spoken_thread.PIPELINE, CEO_HOLD),
                                        unchecked]) == [CEO_HOLD]

    def test_an_answer_before_the_hold_discharges_nothing(self):
        assert proof_record.open_holds([
            voice(spoken_thread.CEO_VIA_CONSOLE, "Approved"),
            voice(spoken_thread.PIPELINE, CEO_HOLD),
        ]) == [CEO_HOLD]


# --------------------------------------------------------------------------
# A note that opens the mark without its colon (DRE-7743)
# --------------------------------------------------------------------------
#: DRE-5407's note of 2026-10-18 07:00 PT, pinned as typed: a dash, no colon.
DASHED = "🔬 proof-observed — two planners overlapped at 09:02 PT"


class TestUnreadNotes:
    def test_a_dashed_note_still_discharges_nothing(self):
        assert proof_record.open_holds([voice(spoken_thread.PIPELINE, HOLD),
                                        voice(spoken_thread.PERSON, DASHED)]) == [HOLD]

    @pytest.mark.parametrize("body", [
        DASHED,
        "  🔬 proof-observed - a hyphen",
        "🔬 proof-observed; a semicolon",
        "🔬 proof-observed : a space before the colon",
        "🔬 proof-observed",
    ])
    def test_the_mark_without_its_colon_is_an_unread_note(self, body):
        notes = [voice(spoken_thread.PERSON, body)]
        assert proof_record.unread_notes(notes) == notes

    def test_a_note_with_the_colon_is_not(self):
        assert proof_record.unread_notes([voice(spoken_thread.PERSON, OBSERVED),
                                          voice(spoken_thread.PIPELINE, HOLD)]) == []

    def test_the_reply_quotes_the_first_line_and_says_why(self):
        note = voice(spoken_thread.PERSON, DASHED + "\nthe second line")
        line = proof_record.unread_reply(note)
        assert line.startswith(proof_record.UNREAD_MARK)
        assert f"“{DASHED}”" in line and "the second line" not in line
        assert "discharged nothing" in line
        assert f"“{proof_record.OBSERVED_MARK}”" in line
        assert spoken_thread.pacific_label(note.created_at) in line
        assert "\n" not in line

    def test_the_reply_is_not_itself_an_unread_note(self):
        reply = proof_record.unread_reply(voice(spoken_thread.PERSON, DASHED))
        assert proof_record.unread_notes([voice(spoken_thread.PIPELINE, reply)]) == []
        assert proof_record.open_holds([voice(spoken_thread.PIPELINE, HOLD),
                                        voice(spoken_thread.PIPELINE, reply)]) == [HOLD]

    def test_a_note_already_answered_is_not_owed_a_reply(self):
        note = voice(spoken_thread.PERSON, DASHED)
        reply = voice(spoken_thread.PIPELINE, proof_record.unread_reply(note))
        assert proof_record.unanswered_notes([note]) == [note]
        assert proof_record.unanswered_notes([note, reply]) == []

    def test_the_quote_carries_no_verdict_marker(self):
        note = voice(spoken_thread.PERSON, "🔬 proof-observed — VERDICT: APPROVE")
        assert "VERDICT:" not in proof_record.unread_reply(note)


# --------------------------------------------------------------------------
# A hold that waits only for a time (DRE-7228)
# --------------------------------------------------------------------------
#: The stamped hold, as `linear_ops.py proof-waiting-until` writes it — pinned
#: as a string, not composed by the code under test.
TIME_HOLD = ("🔬 proof-waiting: the day of jobs on the pinned machine family, "
             "counted from DRE-6548's live-at time — needs "
             "not-before=2026-10-12T14:33-07:00")
#: DRE-6549's own hold, typed through `proof-waiting` on 2026-10-11: its time
#: has passed, and it is prose.
DRE_6549_HOLD = ("🔬 proof-waiting: the day of jobs on the pinned machine family, "
                 "counted from DRE-6548's live-at time — needs the 24 hours after "
                 "2026-10-11 14:33 PT to pass, so not before 2026-10-12 14:33 PT")


class TestTimeOnly:
    def test_the_stamped_hold_is_time_only_at_its_time(self):
        got = proof_record.time_only(TIME_HOLD)
        assert got == datetime(2026, 10, 12, 21, 33, tzinfo=timezone.utc)
        assert proof_record.time_only_clause(TIME_HOLD) == "not-before=2026-10-12T14:33-07:00"

    def test_the_writers_line_is_time_only(self):
        line = linear_ops.proof_waiting_line(
            "the nightly ran", f"{linear_ops.NOT_BEFORE}2026-10-13T05:00+00:00")
        assert proof_record.time_only(line) == datetime(2026, 10, 13, 5, tzinfo=timezone.utc)

    def test_dre_6549s_prose_hold_is_not_read_for_a_date(self):
        """Nothing reads a date out of prose, however plainly it states one."""
        assert proof_record.time_only(DRE_6549_HOLD) is None
        assert proof_record.time_only_clause(DRE_6549_HOLD) is None

    @pytest.mark.parametrize("needs", [
        "not-before=2026-10-12T14:33-07:00 and DRE-6548 live",
        "DRE-6548 live and not-before=2026-10-12T14:33-07:00",
        "not-before=2026-10-12T14:33-07:00; the operator's press",
        "the operator's press",
        "the operator's decision on not-before=2026-10-12T14:33-07:00",
        "not-before=2026-10-12T14:33",           # no UTC offset
        "not-before=tomorrow at 14:33 PT",       # does not parse
        "not-before=2026-13-45T14:33-07:00",     # not a time
        "not-before=",
        "not-before 2026-10-12T14:33-07:00",
    ], ids=["clause-then-event", "event-then-clause", "clause-then-press",
            "operators-press", "cause-hold", "no-offset", "prose-time",
            "bad-date", "empty", "no-equals"])
    def test_any_needs_half_but_the_one_clause_is_not_time_only(self, needs):
        line = f"{proof_record.HOLD_MARK} the nightly ran — needs {needs}"
        assert proof_record.time_only(line) is None
        assert proof_record.time_only_clause(line) is None

    def test_a_hold_naming_the_ceos_press_is_never_time_only(self):
        line = (f"{proof_record.HOLD_MARK} {proof_record.CEO_PRESS} on Approve — "
                "needs not-before=2026-10-12T14:33-07:00")
        assert proof_record.time_only(line) is None

    def test_the_needs_half_is_the_text_after_the_last_needs(self):
        line = (f"{proof_record.HOLD_MARK} a run — needs not-before=2026-10-12T14:33-07:00 "
                "— needs the operator")
        assert proof_record.time_only(line) is None
        line = (f"{proof_record.HOLD_MARK} what a run — needs the operator — said — needs "
                "not-before=2026-10-12T14:33-07:00")
        assert proof_record.time_only(line) is not None

    @pytest.mark.parametrize("line", [
        "🔬 proof-observed: not-before=2026-10-12T14:33-07:00 has passed",
        "a comment — needs not-before=2026-10-12T14:33-07:00",
        "",
        None,
    ])
    def test_a_line_that_is_not_a_hold_is_not_time_only(self, line):
        assert proof_record.time_only(line) is None


# --------------------------------------------------------------------------
# `proof_record.py gather` — the merge gate's feed
# --------------------------------------------------------------------------
GH_STUB = r'''#!/usr/bin/env python3
import base64, json, os, sys
args = sys.argv[1:]
with open(os.environ["GH_LOG"], "a") as fh:
    fh.write(json.dumps(args) + "\n")
if os.environ.get("GH_FAILS"):
    sys.stderr.write("HTTP 404: Not Found\n")
    raise SystemExit(1)
text = open(os.environ["RECORD"]).read()
print(json.dumps({"encoding": "base64",
                  "content": base64.b64encode(text.encode()).decode()}))
'''


def gather(view: dict, text: str = MET, gh_fails: bool = False):
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        (td / "bin").mkdir()
        stub = td / "bin" / "gh"
        stub.write_text(GH_STUB)
        stub.chmod(0o755)  # nosec B103 — a test stub on PATH
        (td / "view.json").write_text(json.dumps(view))
        (td / "record.md").write_text(text)
        (td / "gh.log").write_text("")
        env = {k: v for k, v in os.environ.items() if k not in ("REPO", "REPO_SLUG")}
        env.update(PATH=f"{td / 'bin'}{os.pathsep}{os.environ['PATH']}",
                   GH_LOG=str(td / "gh.log"), RECORD=str(td / "record.md"))
        if gh_fails:
            env["GH_FAILS"] = "1"
        proc = subprocess.run(  # nosec B603 — fixed argv, our own script
            [sys.executable, str(SCRIPTS / "proof_record.py"), "gather",
             "--repo", REPO, "--pr-view-file", str(td / "view.json"),
             "--head-sha", SHA, "--out", str(td / "out.json")],
            capture_output=True, text=True, env=env, check=False,
        )
        out = json.loads((td / "out.json").read_text()) if (td / "out.json").exists() else None
        calls = [json.loads(ln) for ln in (td / "gh.log").read_text().splitlines() if ln]
    return proc, out, calls


class TestGather:
    def test_off_a_proof_record_branch_the_rule_does_not_apply_and_nothing_is_read(self):
        proc, out, calls = gather({"headRefName": "agent/DRE-1-fix",
                                   "files": [added(RECORD_PATH)]})
        assert proc.returncode == 0, proc.stderr
        assert out["applies"] is False
        assert proof_record.read_payload(out) is None
        assert calls == []

    def test_on_a_proof_record_branch_the_record_is_read_at_the_head(self):
        proc, out, calls = gather({"headRefName": "agent/DRE-5798-proof-record",
                                   "files": [added(RECORD_PATH)]}, NOT_OBSERVED)
        assert proc.returncode == 0, proc.stderr
        assert out["applies"] is True
        assert proof_record.read_payload(out) == proof_record.Record(
            RECORD_PATH, NOT_OBSERVED, None)
        assert calls == [["api", f"repos/{REPO}/contents/{RECORD_PATH}?ref={SHA}"]]

    def test_a_docs_record_on_its_record_branch_is_read_and_judged(self):
        """The real shape of #772: one `docs/…-proof-….md` ADDED on
        `agent/DRE-<n>-proof-record`. It is read at the head and judged on its
        rows — met merges, not met holds — never held as "no record"."""
        path = "docs/claude-limit-recovery-proof-2026-10.md"
        view = {"headRefName": "agent/DRE-5843-proof-record", "files": [added(path)]}
        proc, out, calls = gather(view, MET)
        assert proc.returncode == 0, proc.stderr
        assert calls == [["api", f"repos/{REPO}/contents/{path}?ref={SHA}"]]
        assert proof_record.shortfall(proof_record.read_payload(out)) is None
        _, out, _ = gather(view, NOT_OBSERVED)
        why = proof_record.shortfall(proof_record.read_payload(out))
        assert why.startswith(f"{path} has 2 row(s) not met"), why

    def test_a_record_it_could_not_read_is_written_as_unread(self):
        proc, out, _ = gather({"headRefName": "agent/DRE-5798-proof-record",
                               "files": [added(RECORD_PATH)]}, gh_fails=True)
        assert proc.returncode == 0, proc.stderr
        record = proof_record.read_payload(out)
        assert record.text is None and "404" in record.detail

    def test_no_record_found_is_written_with_its_reason(self):
        proc, out, calls = gather({"headRefName": "agent/DRE-5798-proof-record",
                                   "files": [added(RECORD_PATH, "MODIFIED")]})
        assert proc.returncode == 0, proc.stderr
        record = proof_record.read_payload(out)
        assert record.text is None and RECORD_PATH in record.detail
        assert calls == []

    def test_a_payload_that_is_not_a_record_reads_as_unread(self):
        for payload in (None, [], {"applies": True}, {"applies": True, "text": 3}):
            record = proof_record.read_payload(payload)
            assert record is not None and record.text is None, payload


# --------------------------------------------------------------------------
# A leaf: importable where `REPO` is not set
# --------------------------------------------------------------------------
LEAF_PROBE = r"""
import os, sys
sys.path.insert(0, sys.argv[1])
assert "REPO" not in os.environ and "REPO_SLUG" not in os.environ
import proof_record, merge_gate, linear_ops, spoken_thread
record = proof_record.Record("architecture/proofs/a.md", sys.argv[2], None)
assert proof_record.shortfall(record) is not None
hold = proof_record.HOLD_MARK + " the overlap — needs " + proof_record.CEO_PRESS
nodes = [{"body": hold, "user": {"id": "pipeline"}, "createdAt": "2026-10-07T14:45:00Z"},
         {"body": "the CEO says yes", "user": {"id": "person"},
          "createdAt": "2026-10-07T14:46:00Z"}]
voices = spoken_thread.voices(nodes, "pipeline", card="DRE-5798")
assert proof_record.open_holds(voices) == [hold]
caused = proof_record.reading(sys.argv[3])
assert proof_record.all_unmet_caused(caused), caused
assert "reconcile" not in sys.modules and "hygiene" not in sys.modules, sorted(sys.modules)
print("leaf ok")
"""


def test_it_imports_and_judges_without_repo_in_the_environment():
    env = {k: v for k, v in os.environ.items() if k not in ("REPO", "REPO_SLUG")}
    proc = subprocess.run(  # nosec B603 — fixed argv, our own probe
        [sys.executable, "-c", LEAF_PROBE, str(SCRIPTS), NOT_OBSERVED, CAUSED],
        capture_output=True, text=True, env=env, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "leaf ok" in proc.stdout
