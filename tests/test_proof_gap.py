"""A proof record whose unmet rows are all gaps closes its PROOF card and
files one fix card in Intake (DRE-7952).

THE GAP: a record whose unmet rows each carry one of the four cause words
(`config/proof-reach.json` `causes`, DRE-6607) or read `Not met. history:`
(DRE-7740) never closed. Those rows stayed in `proof_record.reading().unmet`,
so `shortfall` held the merge gate (condition P) and the PROOF close
(`linear_ops._record_gap`), and the dispatcher put them to the operator once
(`proof_cause_park`, DRE-6837). A re-run reads the same thing again, and the
operator's answer was only ever "reword it on a new card".

THE RULE UNDER TEST: `proof_record.gap_rows` names each such row with its
kind, `all_unmet_gaps` says every unmet row is one, and `shortfall` returns
None for that record. The PROOF close names each gap row and files ONE fix
card through `proof_gap.file_fix_card` — parentless, in Intake, labeled
`repo:<slug>` — and posts `🩹 proof-gap-card: <DRE-n>` on the PROOF card, the
receipt a second close reads instead of filing again. A row that is plainly
`Not met.`, `Not observed.` with no cause, an unlinked acceptance or a waiting
row still holds, exactly as before.

Run: cd bureau-pipeline && python3 -m pytest tests/test_proof_gap.py -v
"""
from __future__ import annotations

import base64
import io
import json
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/portico")
os.environ.setdefault("REPO_SLUG", "portico")
os.environ.setdefault("GH_TOKEN", "x")

import check_act_receipts  # noqa: E402
import linear_ops  # noqa: E402
import merge_gate  # noqa: E402
import pipeline_act  # noqa: E402
import proof_gap  # noqa: E402
import proof_record  # noqa: E402
import spoken_thread  # noqa: E402

CARD = "DRE-3447"
EPIC = "DRE-3440"
FIX = {"id": "fix-uuid", "identifier": "DRE-9001",
       "url": "https://linear.app/dreadnoughtfoundry/issue/DRE-9001/x"}
PR_URL = "https://github.com/dreadnought-foundry/portico/pull/40"
HEAD = "a" * 40
MERGED_AT = "2026-10-05T18:45:00Z"
PROOF_TITLE = "PROOF: folder access observed against the live portal"
PROOF_LABELS = ["repo:portico", "no-code", "hand-built", "agent:ops"]
QA = "agent-bureau-qa-bot"
RECORD_PATH = "architecture/proofs/folder-access.md"
BRANCH = f"agent/{CARD}-proof-record"

REACH = "The nightly reads the repo variable"
REACH_CELL = "Not observed. cause: out-of-reach — `gh variable get` answered 403"
WRONG = "The console shows the order total"
WRONG_CELL = "Not observed. cause: misdescribed — the console shows no total at all"
WINDOW = "The 09:00 PT window has a free slot"
WINDOW_CELL = "Not met. history: the 09:00–10:00 PT window of 2026-10-12 — it filled at 09:52"

#: Every judged row met but two cause rows and one history row.
GAP_RECORD = f"""# Proof: folder access

| Criterion | Result |
|---|---|
| A moderator opens the shared folder | Met — 11:31 PT, screenshot 1 |
| A guest is refused the folder | Observed. |
| {REACH} | {REACH_CELL} |
| {WRONG} | {WRONG_CELL} |
| {WINDOW} | {WINDOW_CELL} |
"""

PLAIN = "The export finishes in a minute"
#: One cause row beside a row plainly not met.
MIXED_RECORD = f"""# Proof: folder access

| Criterion | Result |
|---|---|
| A moderator opens the shared folder | Met |
| {REACH} | {REACH_CELL} |
| {PLAIN} | Not met. it took four minutes |
"""

#: The only unmet row waits on an event.
WAITING_RECORD = """# Proof: folder access

| Criterion | Result |
|---|---|
| A moderator opens the shared folder | Met |
| The release notes name the change | Not observed. waiting for the 2026-10-14 release |
"""


def _rec(text):
    return proof_record.Record(RECORD_PATH, text, None)


def _with_row(text, criterion, result):
    return text + f"| {criterion} | {result} |\n"


# --------------------------------------------------------------------------
# the reading
# --------------------------------------------------------------------------
class TestTheReading:
    def test_gap_rows_names_each_cause_and_history_row_with_its_kind(self):
        read = proof_record.reading(GAP_RECORD)
        assert proof_record.gap_rows(read) == [
            (REACH, "out-of-reach"), (WRONG, "misdescribed"), (WINDOW, "history")]
        assert proof_record.all_unmet_gaps(read) is True

    @pytest.mark.parametrize("word", ["out-of-reach", "state-gone", "misdescribed",
                                      "operator-step-unblocked"])
    def test_each_of_the_four_cause_words_is_a_gap_kind(self, word):
        text = _with_row(GAP_RECORD, "The cache is warm", f"Not observed. cause: {word} — cold")
        assert ("The cache is warm", word) in proof_record.gap_rows(proof_record.reading(text))

    def test_the_gap_record_passes_the_shortfall(self):
        """MUTATION CHECK: drop the `all_unmet_gaps` arm from `shortfall` and
        the record is held on its three rows — red here."""
        assert proof_record.shortfall(_rec(GAP_RECORD)) is None

    @pytest.mark.parametrize("result", [
        "Not met.",
        "Not met. it took four minutes",
        "Not observed.",
        "Not observed. the runner was down",
        "Not observed. cause: impossible — the run saw a 403",
        "Not observed. cause: state-gone",
        "ACCEPTED by operator decision — overtaken",
        "Not observed. waiting for the 2026-10-14 release",
        "Not met. wording-only: seen “a 403” — differs: the code",
    ])
    def test_a_row_that_is_not_a_gap_still_holds(self, result):
        text = _with_row(GAP_RECORD, PLAIN, result)
        read = proof_record.reading(text)
        assert (PLAIN, result) in read.unmet
        assert PLAIN not in [c for c, _ in proof_record.gap_rows(read)]
        assert proof_record.all_unmet_gaps(read) is False
        why = proof_record.shortfall(_rec(text))
        assert why is not None and "4 row(s) not met" in why

    def test_one_cause_row_and_one_plain_row_holds_and_names_both(self):
        why = proof_record.shortfall(_rec(MIXED_RECORD))
        assert why is not None and "2 row(s) not met" in why
        assert "Not met. it took four minutes" in why
        assert "cause: out-of-reach" in why

    def test_a_record_whose_only_unmet_row_is_waiting_still_holds(self):
        read = proof_record.reading(WAITING_RECORD)
        assert proof_record.gap_rows(read) == []
        assert proof_record.all_unmet_gaps(read) is False
        assert "1 row(s) not met" in proof_record.shortfall(_rec(WAITING_RECORD))

    def test_a_row_the_checker_accepted_is_not_a_gap(self):
        """DRE-7949's acceptance is read first: an accepted row is accepted,
        never a gap, and the cause row beside it is still one."""
        text = _with_row(GAP_RECORD, "The banner reads Saved",
                         "Not met. wording-only: seen “Saved at 11:31” — differs: the time")
        verdict = {"body": ("🔎 QA Critic — VERDICT: APPROVE @" + HEAD + "\n\n"
                            "🔎 proof-row-accepted: “The banner reads Saved” — seen: "
                            "“Saved at 11:31” — differs: the time — reason: it reads the same."),
                   "url": "https://github.com/dreadnought-foundry/portico/pull/40#issuecomment-9"}
        read = proof_record.reading(text, verdict)
        assert ("The banner reads Saved",
                "Not met. wording-only: seen “Saved at 11:31” — differs: the time") in read.accepted
        assert [c for c, _ in proof_record.gap_rows(read)] == [REACH, WRONG, WINDOW]
        assert proof_record.shortfall(_rec(text), verdict) is None

    def test_no_judged_row_is_not_all_gaps(self):
        closing = ("# P\n\n| Criterion | Result |\n|---|---|\n"
                   "| The record is merged to main | Pending |\n")
        read = proof_record.reading(closing)
        assert proof_record.all_unmet_gaps(read) is False
        assert "no row but its closing step" in proof_record.shortfall(_rec(closing))

    def test_no_table_is_not_all_gaps(self):
        assert proof_record.all_unmet_gaps(proof_record.reading("# no table here")) is False

    def test_only_gap_rows_and_no_met_row_still_passes(self):
        """At least one judged row, and every unmet row a gap row."""
        text = f"# P\n\n| Criterion | Result |\n|---|---|\n| {REACH} | {REACH_CELL} |\n"
        assert proof_record.all_unmet_gaps(proof_record.reading(text)) is True
        assert proof_record.shortfall(_rec(text)) is None


# --------------------------------------------------------------------------
# condition P — the merge gate reads the same shortfall
# --------------------------------------------------------------------------
def _payload(text):
    return {"applies": True, "path": RECORD_PATH, "text": text, "detail": None}


def test_the_gap_record_passes_condition_p():
    assert merge_gate.evaluate_proof_record(BRANCH, _payload(GAP_RECORD)) is None


@pytest.mark.parametrize("text", [MIXED_RECORD, WAITING_RECORD], ids=["mixed", "waiting"])
def test_a_record_with_a_row_that_is_not_a_gap_holds_condition_p(text):
    decision = merge_gate.evaluate_proof_record(BRANCH, _payload(text))
    assert decision is not None and decision.action == "hold"


# --------------------------------------------------------------------------
# the fix card
# --------------------------------------------------------------------------
GAPS = [(REACH, "out-of-reach", REACH_CELL), (WRONG, "misdescribed", WRONG_CELL),
        (WINDOW, "history", WINDOW_CELL)]


class _Board:
    """Linear, faked: the PROOF card's thread, the cards created, the
    comments posted. A comment the pipeline posts lands on the thread, so a
    second close reads the receipt the first one wrote."""

    def __init__(self, thread=()):
        self.nodes = [dict(n) for n in thread]
        self.created, self.comments, self.states = [], [], []

    def thread(self, identifier, *needs, whole=False):
        return [dict(n) for n in self.nodes], "pipeline-key-user"

    def comment(self, identifier, body, *flags):
        self.comments.append((identifier, body))
        self.nodes.append({"body": body, "user": {"id": "pipeline-key-user"},
                           "createdAt": "2026-10-05T18:50:00Z"})
        return None

    def create(self, title, description, *, repo_slug, labels=(), lane="Planning"):
        self.created.append({"title": title, "description": description,
                             "repo_slug": repo_slug, "labels": list(labels), "lane": lane})
        return dict(FIX)

    def state(self, identifier, name, **kwargs):
        self.states.append((identifier, name))


class _Verifier:
    def check_answer(self, body, *, card, created_at):
        return None


def _patched(board):
    return [
        patch.object(linear_ops, "_thread_and_viewer", board.thread),
        patch.object(linear_ops, "cmd_comment", board.comment),
        patch.object(linear_ops, "create_card", board.create),
        patch.object(linear_ops, "cmd_state", board.state),
        patch.object(linear_ops, "find_open", lambda title: None),
        patch.object(proof_gap, "epic_of", lambda identifier: EPIC),
        patch.object(spoken_thread, "_VERIFIER", _Verifier()),
    ]


def _file(board, gaps=GAPS, kind="gap"):
    ps = _patched(board)
    for p in ps:
        p.start()
    try:
        return proof_gap.file_fix_card(CARD, EPIC, PR_URL, gaps, repo_slug="portico", kind=kind)
    finally:
        for p in reversed(ps):
            p.stop()


class TestTheFixCard:
    def test_the_receipt_mark_is_spelled_once(self):
        assert proof_gap.RECEIPT_MARK == "🩹 proof-gap-card:"

    def test_it_files_one_parentless_card_in_intake_labeled_for_the_repo(self):
        board = _Board()
        issue = _file(board)
        assert issue["identifier"] == FIX["identifier"]
        assert len(board.created) == 1
        made = board.created[0]
        assert made["lane"] == "Intake"
        assert made["repo_slug"] == "portico"
        assert made["labels"] == []
        assert made["title"] == (f"portico: the gaps PROOF {CARD} closed with — "
                                 "3 row(s) not observable as worded")

    def test_its_body_has_one_line_per_row_with_the_kind_cell_and_fix(self):
        board = _Board()
        _file(board)
        body = board.created[0]["description"]
        assert CARD in body and EPIC in body and PR_URL in body
        lines = {kind: next(line for line in body.splitlines() if f"“{crit}”" in line)
                 for crit, kind, _ in GAPS}
        assert "out-of-reach" in lines["out-of-reach"] and "a missing reach" in lines["out-of-reach"]
        assert ("a reworded criterion or a defect in the build"
                in lines["misdescribed"])
        assert "history" in lines["history"] and "a reworded criterion" in lines["history"]
        assert "gh variable get" in lines["out-of-reach"]
        assert "it filled at 09:52" in lines["history"]

    @pytest.mark.parametrize("kind, fix", [
        ("state-gone", "a reworded criterion"),
        ("operator-step-unblocked", "an OPERATOR card the proof is blocked by"),
    ])
    def test_the_other_kinds_name_their_fix(self, kind, fix):
        board = _Board()
        _file(board, gaps=[("The cache is warm", kind, f"Not observed. cause: {kind} — x")])
        line = next(x for x in board.created[0]["description"].splitlines()
                    if "“The cache is warm”" in x)
        assert kind in line and fix in line

    def test_it_posts_the_receipt_on_the_proof_card(self):
        board = _Board()
        _file(board)
        receipts = [b for i, b in board.comments if i == CARD]
        assert len(receipts) == 1
        assert receipts[0].startswith(f"{proof_gap.RECEIPT_MARK} {FIX['identifier']}")
        assert pipeline_act.read_trailer(receipts[0])["act"] == proof_gap.ACT

    def test_a_second_call_reads_the_receipt_and_files_nothing(self):
        board = _Board()
        first = _file(board)
        second = _file(board)
        assert len(board.created) == 1
        assert second["identifier"] == first["identifier"] == FIX["identifier"]
        assert len(board.comments) == 1

    def test_a_receipt_someone_else_wrote_is_not_the_pipelines(self):
        forged = {"body": f"{proof_gap.RECEIPT_MARK} DRE-1", "user": {"id": "a-person"},
                  "createdAt": "2026-10-05T18:41:00Z"}
        board = _Board([forged])
        _file(board)
        assert len(board.created) == 1

    def test_an_open_card_with_the_title_is_reused_and_receipted(self):
        """A receipt that failed to post after the create is not a second card."""
        board = _Board()
        ps = _patched(board)
        for p in ps:
            p.start()
        try:
            with patch.object(linear_ops, "find_open", lambda title: "DRE-9002"):
                issue = proof_gap.file_fix_card(CARD, EPIC, PR_URL, GAPS, repo_slug="portico")
        finally:
            for p in reversed(ps):
                p.stop()
        assert board.created == []
        assert issue["identifier"] == "DRE-9002"
        assert board.comments[0][1].startswith(f"{proof_gap.RECEIPT_MARK} DRE-9002")

    def test_the_defect_kind_titles_and_words_the_card_as_a_defect_in_the_build(self):
        board = _Board()
        rows = [(PLAIN, "defect", "Not met. it took four minutes")]
        _file(board, gaps=rows, kind="defect")
        made = board.created[0]
        assert made["title"] == (f"portico: the defect in the build PROOF {CARD} found — "
                                 "1 row(s) not met")
        line = next(x for x in made["description"].splitlines() if f"“{PLAIN}”" in x)
        assert "a defect in the build" in line
        assert "not observable as worded" not in made["description"]
        assert made["lane"] == "Intake"

    def test_an_unknown_kind_is_refused_before_anything_is_written(self):
        board = _Board()
        with pytest.raises(ValueError):
            _file(board, kind="whatever")
        assert board.created == [] and board.comments == []

    def test_the_body_carries_no_verdict_marker(self):
        board = _Board()
        _file(board, gaps=[("The QA Critic posts VERDICT: APPROVE", "out-of-reach",
                            "Not observed. cause: out-of-reach — QA Verifier said so")])
        for marker in ("VERDICT:", "QA Critic", "QA Verifier"):
            assert marker.lower() not in board.created[0]["description"].lower()


# --------------------------------------------------------------------------
# the PROOF close
# --------------------------------------------------------------------------
def _verdict():
    return {"author": {"login": QA}, "url": f"{PR_URL}#issuecomment-1",
            "body": f"🔎 QA Critic — VERDICT: APPROVE @{HEAD}\n\nThe record holds.",
            "createdAt": "2026-10-05T18:39:00Z"}


APPROVED = {"url": PR_URL, "headRefName": BRANCH, "headRefOid": HEAD,
            "mergedAt": MERGED_AT, "state": "MERGED", "comments": [_verdict()],
            "files": [{"path": RECORD_PATH, "changeType": "ADDED"}]}


def _contents(text):
    def gh(args):
        return json.dumps({"encoding": "base64",
                           "content": base64.b64encode(text.encode()).decode()})
    return gh


def _issue():
    return {"id": "card-uuid", "identifier": CARD, "title": PROOF_TITLE,
            "team": {"id": "team-1"}, "state": {"name": "In Review", "type": "started"},
            "labels": {"nodes": [{"name": n} for n in PROOF_LABELS]}}


def _card_done(board, text):
    out = io.StringIO()
    ps = _patched(board) + [
        patch.object(linear_ops, "get_issue", return_value=_issue()),
        patch.object(linear_ops, "read_merged_pr", return_value=APPROVED),
        patch.object(proof_record, "_gh", _contents(text)),
    ]
    for p in ps:
        p.start()
    try:
        with redirect_stdout(out):
            linear_ops.cmd_card_done(CARD, PR_URL)
    finally:
        for p in reversed(ps):
            p.stop()
    return out.getvalue()


class TestThePROOFClose:
    def test_the_gap_record_closes_its_card_and_files_one_fix_card(self):
        board = _Board()
        _card_done(board, GAP_RECORD)
        assert board.states == [(CARD, "Done")]
        assert len(board.created) == 1
        made = board.created[0]
        assert made["lane"] == "Intake" and made["repo_slug"] == "portico"
        for crit, kind, _ in GAPS:
            assert f"“{crit}”" in made["description"]
        close = [b for i, b in board.comments if b.startswith(f"✅ Merged: {PR_URL}")]
        assert len(close) == 1

    def test_the_close_comment_names_each_gap_row_its_kind_and_what_it_is(self):
        board = _Board()
        _card_done(board, GAP_RECORD)
        close = next(b for _, b in board.comments if b.startswith("✅ Merged:"))
        assert FIX["identifier"] in close
        for crit, kind, _ in GAPS:
            line = next(x for x in close.splitlines() if f"“{crit}”" in x)
            assert kind in line
        reach = next(x for x in close.splitlines() if f"“{REACH}”" in x)
        assert "the criterion names a reading the proof identity cannot make" in reach
        window = next(x for x in close.splitlines() if f"“{WINDOW}”" in x)
        assert "closed" in window or "finished" in window
        for marker in ("VERDICT:", "QA Critic", "QA Verifier"):
            assert marker not in close

    def test_running_the_close_twice_files_one_fix_card(self):
        board = _Board()
        _card_done(board, GAP_RECORD)
        _card_done(board, GAP_RECORD)
        assert len(board.created) == 1
        receipts = [b for _, b in board.comments if b.startswith(proof_gap.RECEIPT_MARK)]
        assert len(receipts) == 1
        closes = [b for _, b in board.comments if b.startswith("✅ Merged:")]
        assert len(closes) == 2 and all(FIX["identifier"] in b for b in closes)

    def test_the_reconcile_backstop_ruling_files_through_the_same_receipt(self):
        """Both auto-Done paths ask `merge_close_ruling`; after card-done
        filed, the backstop's ask reads the receipt and files nothing."""
        board = _Board()
        _card_done(board, GAP_RECORD)
        ps = _patched(board) + [
            patch.object(linear_ops, "read_merged_pr", return_value=APPROVED),
            patch.object(proof_record, "_gh", _contents(GAP_RECORD)),
        ]
        for p in ps:
            p.start()
        try:
            skip, note = linear_ops.merge_close_ruling(CARD, PROOF_TITLE, PROOF_LABELS, PR_URL)
        finally:
            for p in reversed(ps):
                p.stop()
        assert skip is None and FIX["identifier"] in note
        assert len(board.created) == 1

    @pytest.mark.parametrize("text", [MIXED_RECORD, WAITING_RECORD], ids=["mixed", "waiting"])
    def test_a_record_with_a_row_that_is_not_a_gap_stays_open_and_files_nothing(self, text):
        board = _Board()
        out = _card_done(board, text)
        assert board.states == []
        assert board.created == []
        assert not [b for _, b in board.comments if b.startswith(proof_gap.RECEIPT_MARK)]
        assert "AUTO-DONE SKIPPED" in out

    def test_an_all_met_record_files_nothing(self):
        met = ("# P\n\n| Criterion | Result |\n|---|---|\n"
               "| A moderator opens the shared folder | Met |\n")
        board = _Board()
        _card_done(board, met)
        assert board.states == [(CARD, "Done")]
        assert board.created == []
        close = next(b for _, b in board.comments if b.startswith("✅ Merged:"))
        assert proof_gap.RECEIPT_MARK not in close

    def test_a_failed_filing_leaves_the_card_open_and_says_why(self):
        """The fix card is filed before the close, so a create Linear refused
        leaves the card open for the backstop to ask again — a refusal with
        its reason, never an exception: the reconcile backstop asks the same
        ruling inside its sweep, and one card's failed write must not end the
        pass."""
        board = _Board()

        def refuse(*a, **k):
            raise linear_ops.LinearError("HTTP 503 from Linear")

        board.create = refuse
        out = _card_done(board, GAP_RECORD)
        assert board.states == []
        assert "AUTO-DONE SKIPPED" in out
        left = [b for _, b in board.comments if linear_ops.MERGED_NOT_CLOSED_MARKER in b]
        assert len(left) == 1 and "fix card could not be filed" in left[0]
        assert "HTTP 503 from Linear" in left[0]

    def test_the_backstop_ruling_refuses_rather_than_raises_on_a_failed_filing(self):
        board = _Board()

        def refuse(*a, **k):
            raise linear_ops.LinearError("HTTP 503 from Linear")

        board.create = refuse
        ps = _patched(board) + [
            patch.object(linear_ops, "read_merged_pr", return_value=APPROVED),
            patch.object(proof_record, "_gh", _contents(GAP_RECORD)),
        ]
        for p in ps:
            p.start()
        try:
            skip, note = linear_ops.merge_close_ruling(CARD, PROOF_TITLE, PROOF_LABELS, PR_URL)
        finally:
            for p in reversed(ps):
                p.stop()
        assert note is None and "fix card could not be filed" in skip


# --------------------------------------------------------------------------
# the act
# --------------------------------------------------------------------------
def test_the_receipt_is_a_declared_act_and_the_registry_check_passes():
    entry = pipeline_act.record(proof_gap.ACT)
    assert entry["tag"] == "proof-gap-card"
    assert entry["tag"] in proof_gap.RECEIPT_MARK
    assert entry["emits"]["file"] == "scripts/proof_gap.py"
    assert entry["adopted"] is True
    assert pipeline_act.problems() == []


def test_every_comment_proof_gap_posts_is_composed():
    found = [p for p in check_act_receipts.problems() if "proof_gap.py" in str(p)]
    assert found == []
    sites = [s for s in check_act_receipts.sites() if s.path.endswith("proof_gap.py")]
    assert sites, "proof_gap.py posts its receipt through a site the guard can see"


def test_the_receipt_is_read_off_who_wrote_each_comment_not_the_voice_kinds():
    """`linear_ops.comment_records` is the one authorship fact a receipt key
    reads (`authored_by_pipeline`); the voice kinds are `spoken_thread`'s, and
    every reader of them must also handle an unchecked console answer."""
    text = (ROOT / "scripts" / "proof_gap.py").read_text(encoding="utf-8")
    assert "comment_records" in text
    assert "spoken_thread" not in text
    records = [{"body": f"{proof_gap.RECEIPT_MARK} DRE-7 — filed", "authored_by_pipeline": True}]
    assert proof_gap.filed(records)["identifier"] == "DRE-7"
    assert proof_gap.filed([{**records[0], "authored_by_pipeline": False}]) is None
