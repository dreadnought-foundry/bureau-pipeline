"""A wording-only proof row is accepted by the record's critic (DRE-7949).

THE GAP: a proof-record row whose mechanism was seen working, and where only
the card's wording, a quote, a time, a count or the way it was observed
differs, could only be accepted as `ACCEPTED by operator decision —` with a
linked comment — and nothing checked who wrote that comment. The run that
took the reading could accept its own row.

THE RULE UNDER TEST: the proof run writes such a row as a CANDIDATE —
`Not met. wording-only: seen “<quote>” — differs: <what>` — and on its own it
is unmet. It counts as accepted only when the critic verdict bound to the head
(qa-bot authored, so the proof run's App can never write it) carries a
`🔎 proof-row-accepted:` line whose criterion is the row's, cut to 70 as
`proof_record._cut` renders it, and whose seen-quote appears in the record.
Never an acceptance: the line in any other comment, the line written into the
record, a row that opens `ACCEPTED by checker`, and a line naming a row whose
result opens `Not observed.`. Both readers — the merge gate's condition P and
the PROOF close — pass the bound verdict's body and URL into the reading, and
the close names each checker-accepted row and links the verdict comment.

Run: cd bureau-pipeline && python3 -m pytest tests/test_proof_checker_acceptance.py -v
"""

from __future__ import annotations

import io
import json
import os
import subprocess  # nosec B404 — fixed argv, our own script
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("GH_TOKEN", "x")

import linear_ops  # noqa: E402
import proof_record  # noqa: E402
import spoken_thread  # noqa: E402

GATE = ROOT / "scripts" / "merge_gate.py"

CARD = "DRE-5798"
HEAD = "ab12" * 10
QA_REST = "agent-bureau-qa-bot[bot]"
QA_GRAPHQL = "agent-bureau-qa-bot"
BUREAU_REST = "agent-bureau-bot[bot]"
BUREAU_GRAPHQL = "agent-bureau-bot"
PROOF_BRANCH = f"agent/{CARD}-proof-record"
PR_URL = "https://github.com/dreadnought-foundry/agent-bureau/pull/3318"
VERDICT_URL = f"{PR_URL}#issuecomment-9001"
OTHER_URL = f"{PR_URL}#issuecomment-9002"
RECORD_PATH = "architecture/proofs/board-done.md"
GREEN = [{"name": "unit", "status": "completed", "conclusion": "success"}]
MERGED_AT = "2026-10-25T18:45:00Z"

CRITERION = "The board shows the card in Done"
SEEN = "Status: Done (moved 11:02 PT)"
#: The contract's two openings, spelled out so a reader that renamed them
#: fails the contract test rather than every fixture.
WORDING_OPENING = "Not met. wording-only:"
ACCEPTANCE_MARK = "🔎 proof-row-accepted:"
WORDING = (f"{WORDING_OPENING} seen “{SEEN}” — differs: the card "
           "says the board, the record read the API")
OTHER = "A moderator opens the shared folder"


def table(*rows, closing=True) -> str:
    lines = ["| Criterion | Result |", "|---|---|"]
    lines += [f"| {c} | {r} |" for c, r in rows]
    if closing:
        lines.append("| The record is merged to main | Pending |")
    return "# Proof: the board\n\n" + "\n".join(lines) + "\n"


#: The record this card is about: one row met, one wording-only candidate.
RECORD = table((OTHER, "Met — 11:31 PT, screenshot 1"), (CRITERION, WORDING))


def acceptance(criterion=CRITERION, seen=SEEN) -> str:
    return (f"{ACCEPTANCE_MARK} “{proof_record._cut(criterion, 70)}” — "
            f"seen: “{seen}” — differs: the board versus the API — reason: The card "
            "moved to Done, and only the place it was read differs.")


def verdict_body(*lines) -> str:
    return "\n".join([f"🔎 QA Critic — VERDICT: APPROVE @{HEAD}", "",
                      "The record holds.", "", *lines])


def verdict(*lines, url=VERDICT_URL) -> dict:
    return {"body": verdict_body(*lines), "url": url}


# --------------------------------------------------------------------------- #
# the reading                                                                  #
# --------------------------------------------------------------------------- #


def test_the_contract_constants():
    assert proof_record.WORDING_OPENING == WORDING_OPENING
    assert proof_record.ACCEPTANCE_MARK == ACCEPTANCE_MARK


def test_a_candidate_row_on_its_own_is_unmet():
    read = proof_record.reading(RECORD)
    assert (CRITERION, WORDING) in read.unmet
    assert read.accepted == []
    read = proof_record.reading(RECORD, None)
    assert (CRITERION, WORDING) in read.unmet


def test_the_bound_verdicts_acceptance_accepts_the_candidate():
    read = proof_record.reading(RECORD, verdict(acceptance()))
    assert read.unmet == []
    assert (CRITERION, WORDING) in read.accepted
    assert (CRITERION, VERDICT_URL) in read.checked
    assert proof_record.shortfall(
        proof_record.Record(RECORD_PATH, RECORD, None), verdict(acceptance())) is None


def test_a_long_criterion_is_matched_cut_to_seventy():
    long = ("The board shows the card in Done within five minutes of the merge, "
            "on every one of the three boards the fleet keeps")
    assert len(long) > 70
    text = table((long, WORDING))
    read = proof_record.reading(text, verdict(acceptance(long)))
    assert read.unmet == [] and (long, WORDING) in read.accepted
    # The whole criterion, uncut, is not how `_cut` renders it.
    whole = verdict(acceptance().replace(CRITERION, long))
    assert proof_record.reading(text, whole).unmet == [(long, WORDING)]


def test_a_line_that_does_not_open_its_line_is_not_read():
    quoted = verdict("> " + acceptance())
    assert proof_record.reading(RECORD, quoted).unmet == [(CRITERION, WORDING)]
    prose = verdict("I would write " + acceptance())
    assert proof_record.reading(RECORD, prose).unmet == [(CRITERION, WORDING)]


def test_an_acceptance_whose_seen_quote_is_not_in_the_record_accepts_nothing():
    read = proof_record.reading(RECORD, verdict(acceptance(seen="Status: Archived")))
    assert read.unmet == [(CRITERION, WORDING)]
    assert read.checked == []
    why = proof_record.shortfall(proof_record.Record(RECORD_PATH, RECORD, None),
                                 verdict(acceptance(seen="Status: Archived")))
    assert why is not None and CRITERION in why


def test_an_acceptance_naming_no_row_accepts_nothing():
    read = proof_record.reading(
        RECORD, verdict(acceptance(criterion="A row the record does not have")))
    assert read.unmet == [(CRITERION, WORDING)]
    assert read.checked == []


def test_an_acceptance_naming_a_row_not_observed_leaves_it_unmet():
    unseen = "Not observed. The board was not reachable from the runner."
    text = table((CRITERION, unseen))
    read = proof_record.reading(text, verdict(acceptance(seen="Not observed.")))
    assert read.unmet == [(CRITERION, unseen)]
    assert read.accepted == [] and read.checked == []
    why = proof_record.shortfall(proof_record.Record(RECORD_PATH, text, None),
                                 verdict(acceptance(seen="Not observed.")))
    assert why is not None and CRITERION in why
    assert proof_record.NOT_SEEN in why


def test_an_acceptance_written_into_the_record_is_the_reading_runs_own():
    text = RECORD + "\n" + acceptance() + "\n"
    read = proof_record.reading(text, verdict())
    assert read.unmet == [(CRITERION, WORDING)]
    why = proof_record.shortfall(proof_record.Record(RECORD_PATH, text, None), verdict())
    assert why is not None and proof_record.OWN_ACCEPTANCE in why
    # Even with no verdict at all.
    assert proof_record.OWN_ACCEPTANCE in proof_record.shortfall(
        proof_record.Record(RECORD_PATH, text, None))


def test_an_acceptance_in_another_comment_is_the_reading_runs_own():
    read = proof_record.reading(RECORD, verdict(), others=[verdict_body(acceptance())])
    assert read.unmet == [(CRITERION, WORDING)]
    why = proof_record.shortfall(proof_record.Record(RECORD_PATH, RECORD, None),
                                 verdict(), others=[acceptance()])
    assert why is not None and proof_record.OWN_ACCEPTANCE in why


def test_a_row_marked_accepted_by_checker_is_the_reading_runs_own():
    marked = f"ACCEPTED by checker — seen “{SEEN}”, only the wording differs"
    text = table((CRITERION, marked))
    read = proof_record.reading(text, verdict(acceptance()))
    assert read.unmet == [(CRITERION, marked)]
    assert read.accepted == []
    why = proof_record.shortfall(proof_record.Record(RECORD_PATH, text, None),
                                 verdict(acceptance()))
    assert why is not None and proof_record.OWN_ACCEPTANCE in why


def test_a_verdict_with_no_comment_link_accepts_nothing():
    for url in (None, "", "https://example.com/x", f"{PR_URL}#discussion_r1"):
        read = proof_record.reading(RECORD, verdict(acceptance(), url=url))
        assert read.unmet == [(CRITERION, WORDING)], url


def test_the_legacy_operator_decision_still_reads_beside_the_checker():
    legacy = ("ACCEPTED by operator decision — guests were retired "
              "(https://github.com/dreadnought-foundry/portico/pull/39#issuecomment-123)")
    text = table((OTHER, legacy), (CRITERION, WORDING))
    read = proof_record.reading(text, verdict(acceptance()))
    assert read.unmet == []
    assert (OTHER, legacy) in read.accepted and (CRITERION, WORDING) in read.accepted
    said = proof_record.summary(read)
    assert said == (f"0 rows met, 1 accepted by operator decision: “{OTHER}”; "
                    f"“{CRITERION}” accepted by the checker ({VERDICT_URL})")
    # With no verdict passed, the legacy row reads exactly as it did.
    assert proof_record.reading(text).accepted == [(OTHER, legacy)]


def test_the_summary_names_a_checker_accepted_row_and_its_verdict():
    read = proof_record.reading(RECORD, verdict(acceptance()))
    assert proof_record.summary(read) == (
        f"1 rows met, “{CRITERION}” accepted by the checker ({VERDICT_URL})")


# --------------------------------------------------------------------------- #
# the merge gate's condition P                                                 #
# --------------------------------------------------------------------------- #


def rest(login, body, url):
    """An issue comment as `gh api repos/…/issues/<n>/comments` returns it."""
    return {"user": {"login": login}, "body": body, "html_url": url}


def gate(text, comments):
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        (td / "cr.json").write_text(json.dumps({"check_runs": GREEN}))
        (td / "cm.json").write_text(json.dumps(comments))
        (td / "wr.json").write_text(json.dumps({"workflow_runs": []}))
        (td / "cp.json").write_text(json.dumps({"status": "ahead"}))
        (td / "pr.json").write_text(json.dumps(
            {"applies": True, "path": RECORD_PATH, "text": text, "detail": None}))
        argv = [sys.executable, str(GATE), "--head-sha", HEAD, "--qa-login", QA_REST,
                "--check-runs-file", str(td / "cr.json"),
                "--comments-file", str(td / "cm.json"),
                "--workflow-runs-file", str(td / "wr.json"),
                "--compare-file", str(td / "cp.json"),
                "--head-branch", PROOF_BRANCH,
                "--proof-record-file", str(td / "pr.json")]
        proc = subprocess.run(argv, capture_output=True, text=True, check=False)  # nosec B603
    assert proc.returncode == 0, proc.stderr
    return dict(ln.split("=", 1) for ln in proc.stdout.splitlines() if "=" in ln)


def test_the_gate_merges_a_candidate_the_bound_critic_accepted():
    fields = gate(RECORD, [rest(QA_REST, verdict_body(acceptance()), VERDICT_URL)])
    assert fields["decision"] == "merge", fields


def test_the_gate_holds_the_candidate_with_no_acceptance():
    fields = gate(RECORD, [rest(QA_REST, verdict_body(), VERDICT_URL)])
    assert fields["decision"] == "hold"
    assert CRITERION in fields["reason"]


def test_the_gate_holds_an_acceptance_the_proof_runs_app_posted():
    fields = gate(RECORD, [rest(QA_REST, verdict_body(), VERDICT_URL),
                           rest(BUREAU_REST, acceptance(), OTHER_URL)])
    assert fields["decision"] == "hold"
    assert proof_record.OWN_ACCEPTANCE in fields["reason"]


def test_the_gate_holds_an_acceptance_written_into_the_record():
    fields = gate(RECORD + "\n" + acceptance() + "\n",
                  [rest(QA_REST, verdict_body(), VERDICT_URL)])
    assert fields["decision"] == "hold"
    assert proof_record.OWN_ACCEPTANCE in fields["reason"]


def test_the_gate_holds_a_row_marked_accepted_by_checker():
    text = table((CRITERION, f"ACCEPTED by checker — seen “{SEEN}”"))
    fields = gate(text, [rest(QA_REST, verdict_body(acceptance()), VERDICT_URL)])
    assert fields["decision"] == "hold"
    assert proof_record.OWN_ACCEPTANCE in fields["reason"]


def test_the_gate_reads_only_the_latest_critic_verdict():
    """An acceptance in an earlier critic verdict is not in the verdict the
    gate binds to the head."""
    fields = gate(RECORD, [rest(QA_REST, verdict_body(acceptance()), OTHER_URL),
                           rest(QA_REST, verdict_body(), VERDICT_URL)])
    assert fields["decision"] == "hold"


def test_the_gate_holds_an_acceptance_of_a_row_not_observed():
    text = table((CRITERION, "Not observed. The board was not reachable."))
    fields = gate(text, [rest(QA_REST, verdict_body(acceptance(seen="Not observed.")),
                              VERDICT_URL)])
    assert fields["decision"] == "hold"
    assert proof_record.NOT_SEEN in fields["reason"]


# --------------------------------------------------------------------------- #
# the PROOF close                                                              #
# --------------------------------------------------------------------------- #

PROOF_TITLE = "PROOF: the board shows a merged card in Done"
PROOF_LABELS = ["repo:agent-bureau", "no-code", "agent:ops"]
ADDED_RECORD = [{"path": RECORD_PATH, "changeType": "ADDED"}]


def graphql(login, body, url):
    """A comment as `gh pr view --json comments` renders it."""
    return {"author": {"login": login}, "body": body, "url": url,
            "createdAt": "2026-10-25T18:39:00Z"}


def merged(*comments) -> dict:
    return {"url": PR_URL, "headRefName": PROOF_BRANCH, "headRefOid": HEAD,
            "mergedAt": MERGED_AT, "state": "MERGED", "comments": list(comments),
            "files": ADDED_RECORD}


ACCEPTED_PR = merged(graphql(QA_GRAPHQL, verdict_body(acceptance()), VERDICT_URL))


def test_the_close_names_the_checker_accepted_row_and_links_the_verdict():
    note = linear_ops.proof_close_note(
        CARD, PROOF_TITLE, PR_URL, ACCEPTED_PR,
        proof_record.Record(RECORD_PATH, RECORD, None), [])
    assert note is not None
    assert f"“{CRITERION}” accepted by the checker ({VERDICT_URL})" in note
    assert "VERDICT:" not in note and "QA Critic" not in note


def test_the_gap_is_none_with_the_bound_acceptance():
    assert linear_ops.proof_evidence_gap(
        CARD, ACCEPTED_PR, proof_record.Record(RECORD_PATH, RECORD, None), []) is None


def test_the_gap_refuses_an_acceptance_the_proof_runs_app_posted():
    pr = merged(graphql(QA_GRAPHQL, verdict_body(), VERDICT_URL),
                graphql(BUREAU_GRAPHQL, acceptance(), OTHER_URL))
    gap = linear_ops.proof_evidence_gap(
        CARD, pr, proof_record.Record(RECORD_PATH, RECORD, None), [])
    assert gap is not None and proof_record.OWN_ACCEPTANCE in gap
    assert linear_ops.proof_close_note(
        CARD, PROOF_TITLE, PR_URL, pr,
        proof_record.Record(RECORD_PATH, RECORD, None), []) is None


class _Contents:
    """`proof_record._gh` for the contents API: the record's text."""

    def __init__(self, text):
        self.text = text

    def __call__(self, args):
        import base64
        return json.dumps({"encoding": "base64",
                           "content": base64.b64encode(self.text.encode()).decode()})


class _Verifier:
    def check_answer(self, body, *, card, created_at):
        return None


def _card_done(pr, text=RECORD):
    issue = {"id": "card-uuid", "identifier": CARD, "title": PROOF_TITLE,
             "team": {"id": "team-1"}, "state": {"name": "In Review", "type": "started"},
             "labels": {"nodes": [{"name": n} for n in PROOF_LABELS]}}
    buf = io.StringIO()
    with patch.object(linear_ops, "get_issue", return_value=issue), \
            patch.object(linear_ops, "cmd_state") as state, \
            patch.object(linear_ops, "cmd_comment") as comment, \
            patch.object(linear_ops, "read_merged_pr", return_value=pr), \
            patch.object(proof_record, "_gh", _Contents(text)), \
            patch.object(linear_ops, "_thread_and_viewer",
                         MagicMock(return_value=([], "pipeline-key-user"))), \
            patch.object(spoken_thread, "_VERIFIER", _Verifier()), \
            redirect_stdout(buf):
        linear_ops.cmd_card_done(CARD, PR_URL)
    return state, comment


def test_card_done_closes_the_card_on_the_checkers_acceptance():
    state, comment = _card_done(ACCEPTED_PR)
    state.assert_called_once_with(CARD, "Done")
    body = comment.call_args.args[1]
    assert f"“{CRITERION}” accepted by the checker ({VERDICT_URL})" in body


def test_card_done_leaves_the_card_open_on_its_own_acceptance():
    pr = merged(graphql(QA_GRAPHQL, verdict_body(), VERDICT_URL),
                graphql(BUREAU_GRAPHQL, acceptance(), OTHER_URL))
    state, comment = _card_done(pr)
    state.assert_not_called()
    assert proof_record.OWN_ACCEPTANCE in comment.call_args.args[1]
