"""One writer holds a proof card on its cause rows for the operator (DRE-6612, DRE-6837).

A record's `Not observed. cause: <word> — <what the run saw>` rows (DRE-6607)
are met by the proof run that wrote them and by the proof dispatcher. Either
way the hold and the one question to the operator are composed and written by
`scripts/proof_cause_park.py`, and the card is left in the lane it was read in
— `In Review` on every path the epic builds, never moved to Green Light
(DRE-6837). These tests read the composition through the modules that declare
each half — `console_escalation` for the three lines,
`linear_ops.proof_text_refusal` for the hold, `proof_dispatch` for the press
park — and record `park`'s writes in order with the writers faked.

Run: cd bureau-pipeline && python3 -m pytest tests/test_proof_cause_park.py -v
"""
from __future__ import annotations

import os
import subprocess  # nosec B404 — fixed argv, our own script
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
# `proof_dispatch` imports `reconcile`, which reads these at import.
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")
os.environ.setdefault("GH_TOKEN", "x")

import console_escalation  # noqa: E402
import linear_ops  # noqa: E402
import proof_cause_park as pcp  # noqa: E402
import proof_reach  # noqa: E402
import proof_record  # noqa: E402
import spoken_thread  # noqa: E402

SCRIPT = ROOT / "scripts" / "proof_cause_park.py"
CARD = "DRE-7001"
RUN = "https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/42"

RECORD = """# Proof — DRE-7001

| Criterion | Result |
| --- | --- |
| The nightly reads the repo variable | Not observed. cause: out-of-reach — `gh variable get` answered 403 |
| The sweep posts one receipt | Met. Seen at 09:00 PT |
| The stale branch is deleted | **Not observed.** cause: `state-gone` — the branch was gone at 09:02 PT |
| The console shows the badge | Not observed. The run had no browser |
| Merged to main | pending |
"""

ROWS = [
    pcp.CauseRow("The nightly reads the repo variable", "out-of-reach",
                 "`gh variable get` answered 403"),
    pcp.CauseRow("The stale branch is deleted", "state-gone",
                 "the branch was gone at 09:02 PT"),
]

LINES = [
    "Some preamble the run wrote.",
    "cause: out-of-reach — “The nightly reads the repo variable” — `gh variable get` answered 403",
    "cause: state-gone — “The stale branch is deleted” — the branch was gone at 09:02 PT",
]

NEEDS = ("the operator's decision on 2 row(s) the run cannot observe as worded "
         "(cause: out-of-reach, state-gone)")


def _voice(body: str, kind: str = spoken_thread.PIPELINE) -> spoken_thread.Voice:
    return spoken_thread.Voice(kind, f"{kind} label", "2026-10-11T16:00:00Z", body)


# --------------------------------------------------------------------------- #
# the contract strings                                                         #
# --------------------------------------------------------------------------- #


class TestTheContract:
    def test_the_declared_strings(self):
        assert pcp.QUESTION_OPENING == "🙋 For the operator — the proof run cannot observe"
        assert pcp.QUESTION == ("Accept each row as out of date, reword it on a new "
                                "card, or drop it?")
        assert pcp.CAUSE_HOLD_OPENING == "the operator's decision on"
        assert pcp.result_line("DRE-6772", "In Review") == (
            "left DRE-6772 in In Review for the operator")

    def test_the_glyph_is_still_read_as_the_pipelines_by_both_readers(self):
        # DRE-6837 changes the words and keeps 🙋: the comment carries no act
        # trailer, so the glyph is the only mark that says the pipeline wrote it.
        import limit_recovery
        import reconcile

        glyph = pcp.QUESTION_OPENING[0]
        assert glyph == "🙋"
        assert glyph in limit_recovery.RECEIPT_GLYPHS
        assert glyph in reconcile._AGENT_COMMENT_PREFIXES

    def test_no_code_in_the_module_names_a_lane_to_move_to(self):
        # The module writes two comments and moves no card (DRE-6837): no
        # lane writer is called, and no string outside a docstring is
        # `Green Light`.
        import ast

        source = SCRIPT.read_text(encoding="utf-8")
        assert "cmd_advance" not in source and "cmd_state" not in source
        tree = ast.parse(source)
        docs = {id(n.body[0].value) for n in ast.walk(tree)
                if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef))
                and n.body and isinstance(n.body[0], ast.Expr)
                and isinstance(n.body[0].value, ast.Constant)}
        said = [n.value for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and id(n) not in docs]
        assert not [s for s in said if "Green Light" in s], said

    def test_it_imports_nothing_from_the_sweep(self):
        source = SCRIPT.read_text(encoding="utf-8")
        assert "import reconcile" not in source
        assert "from reconcile" not in source
        for module in ("linear_ops", "proof_record", "proof_reach", "spoken_thread",
                       "console_escalation"):
            assert f"import {module}" in source, module

    def test_kind_d_names_the_cause_park_in_the_contract_and_its_render(self):
        import lane_contract

        lane = next(l for l in lane_contract.load()["lanes"] if l["name"] == "Green Light")
        text = lane["clauses"]["entrance"]["text"]
        kind_d = text[text.index("(d)"):text.index("(e)")]
        assert "cause park" in kind_d and "DRE-6612" in kind_d
        assert "proof_cause_park.py" in kind_d
        # DRE-6837: the cause park never enters this lane, and kind (d) says so.
        assert "never enters this lane" in kind_d and "DRE-6837" in kind_d


def _lane(name: str) -> dict:
    import lane_contract

    return next(l for l in lane_contract.load()["lanes"] if l["name"] == name)


def _act_row(**match) -> dict:
    import json

    acts = json.loads((ROOT / "config" / "pipeline-acts.json").read_text(encoding="utf-8"))
    rows = [r for r in acts["acts"] + acts["unconverted"]
            if all(r.get(k) == v for k, v in match.items())]
    assert len(rows) == 1, (match, rows)
    return rows[0]


class TestTheContractSaysInReview:
    """DRE-6837 owns the epic's contract sentences: each written here, each
    sibling's half marked not live until that sibling lands."""

    def test_green_light_declares_no_cause_park_arrival(self):
        arrivals = _lane("Green Light")["clauses"]["entrance"]["arrivals"]
        assert not [r for r in arrivals if "proof_cause_park" in r["where"]]
        assert not [r for r in arrivals if r["writer"] == "proof_cause_park.py"]

    def test_the_kept_arrivals_name_the_proof_exceptions_not_live_yet(self):
        arrivals = {r["where"]: r for r in
                    _lane("Green Light")["clauses"]["entrance"]["arrivals"]}
        proof = arrivals["proof-task.yml#Report proof result to Linear"]["evidence"]
        assert "never for cause rows" in proof and "not live until DRE-6840" in proof
        cap = arrivals["reconcile.py#hand_review_nudge_to_person"]["evidence"]
        assert "never on a proof-record pull request" in cap
        assert "`agent/DRE-<n>-proof-record`" in cap
        assert "held In Review for the operator" in cap
        assert "not live until DRE-6841 lands" in cap

    def test_the_in_review_entrance_holds_the_cause_record_for_the_operator(self):
        text = " ".join(_lane("In Review")["clauses"]["entrance"]["text"].split())
        assert "held here for the operator" in text
        assert "scripts/proof_cause_park.py" in text
        assert "🔬 proof-waiting" in text and "never re-run to read the same thing" in text
        assert "not live until DRE-6840 lands" in text
        assert "never in Green Light" in text and "not live until DRE-6841 lands" in text

    def test_the_in_review_entrance_names_hand_work_as_the_lane_not_chosen(self):
        text = " ".join(_lane("In Review")["clauses"]["entrance"]["text"].split())
        assert "Hand-work was the alternative and is not chosen" in text
        # The three reasons, each by what it rests on.
        assert "Hand-work's own exit" in text
        assert "proof_dispatch.RERUN_LANE" in text and "two-dispatch budget" in text
        assert "the hold, not the lane" in text and "config/pipeline-acts.json" in text

    def test_the_in_review_entrance_names_the_bound_not_live_until_dre_6852(self):
        text = " ".join(_lane("In Review")["clauses"]["entrance"]["text"].split())
        assert "`hand-work-overdue`" in text and "config/hand-work-age.json" in text
        assert "not live until DRE-6852 lands" in text
        assert "DRE-6842" in text

    def test_the_act_rows_say_in_review_and_never_a_move(self):
        row = _act_row(file="scripts/proof_cause_park.py",
                       anchor="cmd_comment(identifier, body)")
        said = row["means"] + " " + row["why"]
        assert pcp.QUESTION_OPENING in row["means"]
        assert "In Review" in row["means"] and "no lane is written" in row["means"]
        assert "Green Light" not in said and "The proof run cannot observe" not in said

    def test_the_hand_work_overdue_row_names_the_held_proof_kind(self):
        row = _act_row(name="hand-work-overdue")
        assert "held In Review for the operator" in row["means"]
        assert "not live until DRE-6852" in row["means"]
        assert row["emits"] == {"file": "scripts/reconcile.py",
                                "anchor": "🚨 hand-work-overdue: {ident} has waited in Hand-work"}
        assert (row["tag"], row["kind"], row["next_actor"], row["cadence_s"]) == (
            "hand-work-overdue", "hold", "operator", None)

    @pytest.mark.parametrize("anchor,card", [
        ("cmd_comment(ident, review_question)", "DRE-6841"),
        ("_post_pr_note(number, review_blocker)", "DRE-6841"),
        ("proof-escalation-comment.md", "DRE-6840"),
    ])
    def test_the_sibling_rows_name_their_case_not_live_yet(self, anchor, card):
        row = _act_row(anchor=anchor)
        assert row["kind"] == "not-an-act"
        assert f"not live until {card}" in row["means"]
        if card == "DRE-6841":
            assert "proof-record pull request" in row["means"]
            assert "held `In Review`" in row["means"]
        else:
            assert "a press or a decision" in row["means"]

    def test_the_registry_doc_names_no_move_for_the_cause_park(self):
        doc = " ".join((ROOT / "docs" / "pipeline-acts.md").read_text(
            encoding="utf-8").split())
        at = doc.index("`proof_cause_park.park`")
        said = doc[at:at + 400]
        assert "Green Light" not in said, said
        assert "In Review" in said
        rendered = (ROOT / "docs" / "lane-contract.md").read_text(encoding="utf-8")
        assert kind_d in rendered


# --------------------------------------------------------------------------- #
# reading the rows                                                             #
# --------------------------------------------------------------------------- #


class TestRows:
    def test_rows_of_returns_the_cause_rows_and_not_the_plain_one(self):
        assert pcp.rows_of(RECORD) == ROWS

    def test_rows_of_a_record_with_no_cause_row_is_empty(self):
        assert pcp.rows_of(RECORD.replace("cause:", "because")) == []
        assert pcp.rows_of("no table here") == []

    def test_rows_from_lines_returns_the_same_two(self):
        assert pcp.rows_from_lines(LINES) == ROWS

    def test_a_fifth_word_is_refused_naming_the_four(self):
        bad = LINES + ["cause: impossible — “The badge is green” — a 404"]
        with pytest.raises(ValueError) as caught:
            pcp.rows_from_lines(bad)
        said = str(caught.value)
        assert "impossible" in said
        for word in proof_reach.causes():
            assert word in said

    def test_the_cause_line_reads_back(self):
        lines = [pcp.cause_line(r) for r in ROWS]
        assert lines == LINES[1:]
        assert pcp.rows_from_lines(lines) == ROWS

    def test_a_long_criterion_is_cut_to_seventy_on_the_line(self):
        row = pcp.CauseRow("x" * 100, "misdescribed", "the page has no such tab")
        line = pcp.cause_line(row)
        assert f"“{proof_record._cut('x' * 100, 70)}”" in line
        assert pcp.rows_from_lines([line])[0].criterion == proof_record._cut("x" * 100, 70)


# --------------------------------------------------------------------------- #
# the hold                                                                     #
# --------------------------------------------------------------------------- #


class TestHold:
    def test_the_hold_names_the_criteria_and_the_operators_decision(self):
        observed, needs = pcp.hold(ROWS)
        assert observed == ("The nightly reads the repo variable; "
                            "The stale branch is deleted")
        assert needs == NEEDS

    def test_both_readers_compose_the_same_hold_on_a_long_criterion(self):
        # The dispatcher reads the record; the proof run reads its escalation
        # file. One hold line between them is what keeps the park to one.
        long = "The nightly reads the repo variable " + "and then much more " * 6
        record = RECORD.replace("The nightly reads the repo variable", long.strip())
        from_record = pcp.rows_of(record)
        from_file = pcp.rows_from_lines(pcp.question(from_record).splitlines())
        assert from_record[0].criterion != from_file[0].criterion
        assert pcp.hold(from_record) == pcp.hold(from_file)
        assert pcp.question(from_record) == pcp.question(from_file)

    def test_the_cause_words_are_distinct_and_in_row_order(self):
        rows = [ROWS[1], ROWS[0], ROWS[1]._replace(criterion="Another gone thing")]
        _, needs = pcp.hold(rows)
        assert needs.endswith("on 3 row(s) the run cannot observe as worded "
                              "(cause: state-gone, out-of-reach)")

    def test_each_criterion_is_cut_as_the_record_reader_cuts(self):
        row = ROWS[0]._replace(criterion="word " * 40)
        observed, _ = pcp.hold([row])
        assert observed == proof_record._cut("word " * 40, 70)

    def test_neither_half_names_the_ceos_press_and_both_are_postable(self):
        rows = ROWS + [ROWS[0]._replace(
            criterion="After the CEO's press the badge shows")]
        for half, what in zip(pcp.hold(rows), ("observed", "needs")):
            assert proof_record.CEO_PRESS not in half
            assert linear_ops.proof_text_refusal(half, what) is None
        _, needs = pcp.hold(ROWS)
        assert linear_ops.proof_text_refusal(needs, "needs") is None

    @pytest.mark.parametrize("marker", linear_ops.CONSOLE_HOLD_MARKERS)
    @pytest.mark.parametrize("case", [str.lower, str.upper, str.title])
    def test_neither_half_borrows_a_fix_budget_marker(self, marker, case):
        # Read off the console's own list, so a marker added there later
        # fails here rather than in a live park.
        said = case(marker)
        rows = [ROWS[0]._replace(criterion=f"The {said} comment shows",
                                 seen=f"the thread read {said}")]
        for half, what in zip(pcp.hold(rows), ("observed", "needs")):
            assert marker not in half.lower()
            assert linear_ops.proof_text_refusal(half, what) is None

    def test_a_press_park_is_never_read_off_the_cause_hold(self):
        import proof_dispatch

        observed, needs = pcp.hold(ROWS)
        hold_line = linear_ops.proof_waiting_line(observed, needs)
        answer = _voice("Accept both rows.", spoken_thread.CEO_VIA_CONSOLE)
        assert proof_dispatch._answered_after_park([_voice(hold_line), answer]) is None
        # The same thread with a press hold is read as a press park — the
        # test above fails for the right reason if the needs ever named it.
        press = linear_ops.proof_waiting_line(observed, f"{proof_record.CEO_PRESS}: x")
        assert proof_dispatch._answered_after_park([_voice(press), answer]) is not None


# --------------------------------------------------------------------------- #
# the question                                                                 #
# --------------------------------------------------------------------------- #


class TestQuestion:
    def test_it_opens_with_the_question_opening(self):
        body = pcp.question(ROWS, run_url=RUN)
        first = body.split("\n", 1)[0]
        assert first.startswith(pcp.QUESTION_OPENING)
        assert first == ("🙋 For the operator — the proof run cannot observe 2 row(s) "
                         "as worded, and asks:")
        assert body.split("\n")[1] == ""

    def test_it_says_what_the_operator_does_next_and_never_asks_the_ceo(self):
        body = pcp.question(ROWS, run_url=RUN)
        said = " ".join(body.split())
        # A cause row's three moves: a new head of the record, a new card, or
        # dropping the record — and the new head is what moves the card.
        assert "ACCEPTED by operator decision" in said and "DRE-6244" in said
        assert "a new card" in said
        assert "drop the record" in said
        assert "new head of the record pull request" in said
        assert "the critic and the merge gate read" in said
        # `🔬 proof-observed` is named in exactly one sentence, the one about
        # rows waiting on an event: for cause rows alone it does nothing.
        assert pcp.MIXED_SHAPE == (
            "If the record also carries rows waiting on an event, record "
            "🔬 proof-observed once the event has happened, so the dispatcher "
            "re-runs the proof over every row.")
        assert pcp.MIXED_SHAPE in body.split("\n")
        assert said.count("proof-observed") == 1
        for line in pcp.NEXT_STEP:
            assert "proof-observed" not in line
            assert line in body.split("\n")
        assert "the CEO" not in " ".join(pcp.NEXT_STEP + (pcp.MIXED_SHAPE, pcp.QUESTION_OPENING))
        # The next step sits after the three lines and before the cause lines,
        # so neither reader of the body reads it as one of its own.
        lines = body.split("\n")
        at_rec = next(i for i, x in enumerate(lines)
                      if x.startswith(console_escalation.RECOMMENDATION_PREFIX))
        at_cause = next(i for i, x in enumerate(lines) if x.startswith("cause:"))
        assert at_rec < lines.index(pcp.MIXED_SHAPE) < at_cause

    def test_it_carries_the_three_lines_with_nothing_wrong(self):
        body = pcp.question(ROWS, recommendation="Reword both rows on a new card — "
                                                  "the mechanism is real, the words are old",
                            run_url=RUN)
        assert console_escalation.problems(body) == []
        esc = console_escalation.parse(body)
        assert esc.question == pcp.QUESTION
        assert esc.recommendation == "Reword both rows on a new card"
        assert esc.why == "the mechanism is real, the words are old"
        for row in ROWS:
            assert proof_record._cut(row.criterion, 70) in esc.finding
            assert row.seen in esc.finding
        assert "2 row(s)" in esc.finding
        assert "out-of-reach" in esc.finding and "state-gone" in esc.finding

    def test_the_three_lines_are_what_console_escalation_renders(self):
        body = pcp.question(ROWS)
        esc = console_escalation.parse(body)
        assert console_escalation.render(esc) in body

    def test_with_no_recommendation_the_line_is_the_none_given_form(self):
        body = pcp.question(ROWS)
        assert console_escalation.problems(body) == []
        esc = console_escalation.parse(body)
        assert esc.recommendation is None
        line = next(x for x in body.split("\n")
                    if x.startswith(console_escalation.RECOMMENDATION_PREFIX))
        assert line.startswith(f"{console_escalation.RECOMMENDATION_PREFIX} "
                               f"{console_escalation.NONE_GIVEN}{console_escalation.SEPARATOR}")

    def test_a_recommendation_with_no_why_still_has_one(self):
        body = pcp.question(ROWS, recommendation="Drop both")
        assert console_escalation.problems(body) == []
        esc = console_escalation.parse(body)
        assert esc.recommendation == "Drop both"
        assert esc.why == pcp.RECOMMENDED_BY

    def test_one_cause_line_per_row_then_the_run(self):
        body = pcp.question(ROWS, run_url=RUN)
        lines = body.split("\n")
        assert [x for x in lines if x.startswith("cause:")] == LINES[1:]
        assert lines[-1] == f"Run: {RUN}"
        assert pcp.rows_from_lines(lines) == ROWS

    def test_no_run_line_without_a_run(self):
        assert "Run:" not in pcp.question(ROWS)


# --------------------------------------------------------------------------- #
# the park                                                                     #
# --------------------------------------------------------------------------- #


class Writers:
    """`linear_ops`' two writers faked, and its lane writers recorded: each
    call in order, and each post added to the thread so a later `park` reads
    it."""

    def __init__(self, monkeypatch, voices=None):
        self.calls: list = []
        self.voices = list(voices or [])
        monkeypatch.setattr(linear_ops, "cmd_proof_waiting", self.proof_waiting)
        monkeypatch.setattr(linear_ops, "cmd_comment", self.comment)
        for name in ("cmd_advance", "cmd_state"):
            monkeypatch.setattr(linear_ops, name,
                                lambda *a, _n=name, **k: self.calls.append((_n, *a)))

    def proof_waiting(self, ident, observed, needs):
        self.calls.append(("hold", ident, observed, needs))
        self.voices.append(_voice(linear_ops.proof_waiting_line(observed, needs)
                                  + "\n\n<!-- act trailer -->"))

    def comment(self, ident, body, *flags):
        self.calls.append(("comment", ident, body))
        self.voices.append(_voice(body))
        return None

    def lane_writes(self) -> list:
        return [c for c in self.calls if c[0] in ("cmd_advance", "cmd_state")]


def _hold_voice(rows=ROWS) -> spoken_thread.Voice:
    return _voice(linear_ops.proof_waiting_line(*pcp.hold(rows)))


REVIEW = "In Review"
LEFT = f"left {CARD} in {REVIEW} for the operator"


class TestPark:
    def test_an_empty_thread_is_held_then_asked_and_left_in_its_lane(self, monkeypatch):
        w = Writers(monkeypatch)
        said = pcp.park(CARD, ROWS, voices=[], lane=REVIEW, run_url=RUN)
        assert said == f"held, asked, {LEFT}"
        assert [c[0] for c in w.calls] == ["hold", "comment"]
        assert w.calls[0] == ("hold", CARD, *pcp.hold(ROWS))
        assert w.calls[1] == ("comment", CARD, pcp.question(ROWS, run_url=RUN))
        assert w.lane_writes() == []

    def test_the_result_names_the_lane_it_was_handed_and_no_other(self, monkeypatch):
        # Two paths can reach the writer with the card somewhere other than
        # In Review; the line says what was read, never a lane assumed.
        for lane in ("In Review", "Green Light", "Hand-work"):
            w = Writers(monkeypatch)
            said = pcp.park(CARD, ROWS, voices=[], lane=lane)
            assert said.endswith(f"left {CARD} in {lane} for the operator"), said
            assert w.lane_writes() == []

    def test_no_lane_is_never_assumed(self, monkeypatch):
        w = Writers(monkeypatch)
        with pytest.raises(ValueError):
            pcp.park(CARD, ROWS, voices=[], lane="")
        assert w.calls == []

    def test_a_thread_already_held_and_asked_posts_nothing(self, monkeypatch):
        thread = [_voice("⏳ earlier"), _hold_voice(), _voice(pcp.question(ROWS))]
        w = Writers(monkeypatch)
        assert pcp.park(CARD, ROWS, voices=thread, lane=REVIEW) == f"already asked, {LEFT}"
        assert w.calls == []

    def test_a_thread_held_with_no_question_is_asked_only(self, monkeypatch):
        w = Writers(monkeypatch)
        said = pcp.park(CARD, ROWS, voices=[_hold_voice()], lane=REVIEW)
        assert [c[0] for c in w.calls] == ["comment"]
        assert said == f"asked, {LEFT}"

    def test_a_question_before_the_hold_does_not_count(self, monkeypatch):
        thread = [_voice(pcp.question(ROWS)), _hold_voice()]
        w = Writers(monkeypatch)
        pcp.park(CARD, ROWS, voices=thread, lane=REVIEW)
        assert [c[0] for c in w.calls] == ["comment"]

    def test_a_different_hold_is_not_this_hold(self, monkeypatch):
        thread = [_hold_voice(ROWS[:1]), _voice(pcp.question(ROWS[:1]))]
        w = Writers(monkeypatch)
        assert pcp.park(CARD, ROWS, voices=thread, lane=REVIEW) == f"held, asked, {LEFT}"
        assert [c[0] for c in w.calls] == ["hold", "comment"]

    def test_only_the_newest_cause_hold_is_this_parks(self, monkeypatch):
        # The record went from these rows to one, and back: the newest cause
        # hold names one row, so the rows asked first are asked again.
        thread = [_hold_voice(), _voice(pcp.question(ROWS)),
                  _hold_voice(ROWS[:1]), _voice(pcp.question(ROWS[:1]))]
        w = Writers(monkeypatch)
        assert pcp.park(CARD, ROWS, voices=thread, lane=REVIEW) == f"held, asked, {LEFT}"
        assert [c[0] for c in w.calls] == ["hold", "comment"]

    def test_a_question_the_writer_asked_before_dre_6837_counts(self, monkeypatch):
        # The opening it wrote before the words changed is the same question.
        legacy = pcp.question(ROWS).replace(pcp.QUESTION_OPENING,
                                            "🙋 The proof run cannot observe", 1)
        w = Writers(monkeypatch)
        said = pcp.park(CARD, ROWS, voices=[_hold_voice(), _voice(legacy)], lane=REVIEW)
        assert said == f"already asked, {LEFT}" and w.calls == []

    def test_a_dry_run_prints_the_hold_and_the_question_and_calls_nothing(
            self, monkeypatch, capsys):
        w = Writers(monkeypatch)
        said = pcp.park(CARD, ROWS, voices=[], lane=REVIEW, live=False)
        assert w.calls == []
        out = capsys.readouterr().out
        line = linear_ops.proof_waiting_line(*pcp.hold(ROWS))
        assert out.startswith(f"would: hold {CARD} — {line}\n")
        assert f"would: ask {CARD} — {pcp.question(ROWS)}\n" in out
        assert said == f"would: leave {CARD} in {REVIEW} for the operator"

    def test_a_dry_run_over_an_asked_thread_says_it_posts_nothing(self, monkeypatch, capsys):
        w = Writers(monkeypatch)
        thread = [_hold_voice(), _voice(pcp.question(ROWS))]
        said = pcp.park(CARD, ROWS, voices=thread, lane=REVIEW, live=False)
        assert w.calls == [] and capsys.readouterr().out == ""
        assert said == f"would: leave {CARD} in {REVIEW} for the operator"

    def test_no_rows_is_nothing_to_do(self, monkeypatch):
        w = Writers(monkeypatch)
        said = pcp.park(CARD, [], voices=[], lane=REVIEW)
        assert said.startswith("nothing to do — ")
        assert w.calls == []

    def test_two_parks_over_one_thread_leave_one_hold_and_one_question(self, monkeypatch):
        w = Writers(monkeypatch)
        assert pcp.park(CARD, ROWS, voices=w.voices, lane=REVIEW,
                        run_url=RUN) == f"held, asked, {LEFT}"
        assert pcp.park(CARD, ROWS, voices=list(w.voices), lane=REVIEW,
                        run_url=RUN) == f"already asked, {LEFT}"
        assert [c[0] for c in w.calls] == ["hold", "comment"]
        bodies = [v.body for v in w.voices]
        assert sum(b.startswith(proof_record.HOLD_MARK) for b in bodies) == 1
        assert sum(b.startswith(pcp.QUESTION_OPENING) for b in bodies) == 1

    def test_a_full_thread_is_refused_and_said(self, monkeypatch):
        w = Writers(monkeypatch, [_hold_voice()])
        monkeypatch.setattr(linear_ops, "cmd_comment",
                            lambda *a, **k: linear_ops.COMMENT_CAP_CONDITION)
        with pytest.raises(linear_ops.LinearError) as caught:
            pcp.park(CARD, ROWS, voices=w.voices, lane=REVIEW)
        assert "full" in str(caught.value)
        assert w.calls == []

    def test_no_string_it_writes_says_only_the_ceo_can_make(self, monkeypatch, capsys):
        rows = [ROWS[0]._replace(criterion="After the CEO's press the badge shows",
                                 seen="the CEO's press was never made")]
        w = Writers(monkeypatch)
        said = [pcp.park(CARD, rows, voices=[], lane=REVIEW, run_url=RUN),
                pcp.park(CARD, rows, voices=[], lane=REVIEW, live=False)]
        written = [x for c in w.calls for x in c[2:]] + said + [capsys.readouterr().out]
        written += list(pcp.hold(rows)) + [pcp.question(rows)]
        for text in written:
            assert "only the CEO can make" not in text, text
        observed, needs = pcp.hold(rows)
        assert observed == "After the press named on the card the badge shows"
        assert proof_record.CEO_PRESS not in observed + needs


# The two records of 2026-10-10 the epic names (DRE-6837), replayed from its
# description: never fetched.
DRE_6694 = """# PROOF record — DRE-6694

| Criterion | Result |
| --- | --- |
| The console's weekly page reads the fleet's spend | Not observed. cause: out-of-reach — the console's admin read answered 403 |
| The weekly report names every repo | Not observed. waiting for the weekly report of 2026-10-18 |
| The weekly report counts the week's merges | Not observed. waiting for the weekly report of 2026-10-18 |
| The weekly report counts the week's escalations | Not observed. waiting for the weekly report of 2026-10-18 |
| The weekly report names the slowest card | Not observed. waiting for the weekly report of 2026-10-18 |
| The weekly report is filed in Green Light | Not observed. waiting for the weekly report of 2026-10-18 |
| The CEO closes this card after reading the record | Open: the CEO's step |
"""


class TestTheReplayOfDre6694:
    """The writer's own path — what `park --record` drives — over DRE-6694's
    record: one `out-of-reach` row beside five rows waiting on a dated report.
    The writer reads the cause rows and nothing else of the record."""

    def test_it_is_held_and_asked_for_the_operator_and_left_where_it_was(self, monkeypatch):
        rows = pcp.rows_of(DRE_6694)
        assert [r.cause for r in rows] == ["out-of-reach"]
        w = Writers(monkeypatch)
        said = pcp.park("DRE-6694", rows, voices=[], lane="In Review")
        assert said == "held, asked, left DRE-6694 in In Review for the operator"
        assert said.endswith(pcp.result_line("DRE-6694", "In Review"))
        hold, ask = w.calls
        assert hold[0] == "hold" and hold[3].startswith(pcp.CAUSE_HOLD_OPENING)
        assert hold[3] == ("the operator's decision on 1 row(s) the run cannot observe "
                           "as worded (cause: out-of-reach)")
        assert ask[0] == "comment" and ask[2].startswith(pcp.QUESTION_OPENING)
        assert w.lane_writes() == []

    def test_handed_green_light_it_says_green_light(self, monkeypatch):
        w = Writers(monkeypatch)
        said = pcp.park("DRE-6694", pcp.rows_of(DRE_6694), voices=[], lane="Green Light")
        assert said.endswith("left DRE-6694 in Green Light for the operator")
        assert w.lane_writes() == []


# --------------------------------------------------------------------------- #
# the CLI — two comments at most, and never a move                             #
# --------------------------------------------------------------------------- #


def _cli(*args, env=None):
    clean = {k: v for k, v in os.environ.items() if k != "LINEAR_API_KEY"}
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True,  # nosec B603
                          text=True, check=False, env=clean, timeout=60)


class TestCli:
    def test_hold_prints_observed_tab_needs(self, tmp_path):
        f = tmp_path / "escalation.txt"
        f.write_text("\n".join(LINES) + "\n", encoding="utf-8")
        done = _cli("hold", "--escalation-file", str(f))
        assert done.returncode == 0, done.stderr
        assert done.stdout.rstrip("\n") == "\t".join(pcp.hold(ROWS))

    def test_question_prints_the_body(self, tmp_path):
        f = tmp_path / "escalation.txt"
        f.write_text("\n".join(LINES) + "\n", encoding="utf-8")
        done = _cli("question", "--escalation-file", str(f), "--run-url", RUN,
                    "--recommendation", "Drop both — nobody reads them")
        assert done.returncode == 0, done.stderr
        assert done.stdout.rstrip("\n") == pcp.question(
            ROWS, recommendation="Drop both — nobody reads them", run_url=RUN)

    def test_rows_prints_the_cause_lines_of_a_record(self, tmp_path):
        f = tmp_path / "record.md"
        f.write_text(RECORD, encoding="utf-8")
        done = _cli("rows", "--record", str(f))
        assert done.returncode == 0, done.stderr
        assert done.stdout.splitlines() == LINES[1:]

    @pytest.mark.parametrize("cmd,flag", [("hold", "--escalation-file"),
                                          ("question", "--escalation-file"),
                                          ("rows", "--record")])
    def test_a_file_with_no_cause_line_exits_two_saying_so(self, tmp_path, cmd, flag):
        f = tmp_path / "plain.txt"
        f.write_text("Nothing here names a cause.\n", encoding="utf-8")
        done = _cli(cmd, flag, str(f))
        assert done.returncode == 2
        assert "cause:" in done.stderr

    def test_a_fifth_word_is_refused(self, tmp_path):
        f = tmp_path / "escalation.txt"
        f.write_text("cause: impossible — “A” — b\n", encoding="utf-8")
        done = _cli("hold", "--escalation-file", str(f))
        assert done.returncode != 0
        for word in proof_reach.causes():
            assert word in done.stderr

    def test_no_subcommand_moves_a_card(self):
        source = SCRIPT.read_text(encoding="utf-8")
        cli = source[source.index("# the CLI "):]
        assert "cmd_advance" not in cli and "cmd_state" not in cli
        # `park` is the one writer the CLI reaches, and only from `park`.
        assert cli.count("park(") == 1


#: The escalation file the proof run writes for cause rows: the three lines,
#: then one `cause:` line per row.
ESCALATION = "\n".join([
    f"{console_escalation.FINDING_PREFIX} two rows cannot be observed as worded",
    f"{console_escalation.QUESTION_PREFIX} {pcp.QUESTION}",
    f"{console_escalation.RECOMMENDATION_PREFIX} Drop both — nobody reads them",
    "",
    *LINES[1:],
]) + "\n"


class _NoLinear:
    """Every Linear read and write `park` could reach, recorded; the lane read
    answers `lane`."""

    def __init__(self, monkeypatch, lane="In Review", thread=()):
        self.calls: list = []
        self.thread = list(thread)
        self.lane = lane

        def gql(*a, **k):
            self.calls.append(("gql", a))
            raise AssertionError("no Linear request is made in this test")

        monkeypatch.setattr(linear_ops, "gql", gql)
        monkeypatch.setattr(linear_ops, "get_issue", self.get_issue)
        monkeypatch.setattr(linear_ops, "read_card_voices", self.voices)
        monkeypatch.setattr(linear_ops, "cmd_proof_waiting",
                            lambda *a: self.calls.append(("hold", *a)))
        monkeypatch.setattr(linear_ops, "cmd_comment",
                            lambda *a: self.calls.append(("comment", *a)))
        for name in ("cmd_advance", "cmd_state"):
            monkeypatch.setattr(linear_ops, name,
                                lambda *a, _n=name, **k: self.calls.append((_n, *a)))

    def get_issue(self, ident, *, fresh=False):
        self.calls.append(("card", ident))
        return {"identifier": ident, "state": {"name": self.lane}}

    def voices(self, ident):
        self.calls.append(("thread", ident))
        return list(self.thread)


def _file(tmp_path, text=ESCALATION, name="escalation.txt"):
    f = tmp_path / name
    f.write_text(text, encoding="utf-8")
    return str(f)


class TestTheParkSubcommand:
    def test_a_dry_run_with_the_lane_prints_the_hold_the_question_and_the_result_last(
            self, tmp_path):
        done = _cli("park", "DRE-0000", "--escalation-file", _file(tmp_path),
                    "--lane", "In Review", "--dry-run", "--run-url", RUN)
        assert done.returncode == 0, done.stderr
        out = done.stdout.rstrip("\n").split("\n")
        line = linear_ops.proof_waiting_line(*pcp.hold(ROWS))
        body = pcp.question(ROWS, recommendation="Drop both — nobody reads them",
                            run_url=RUN)
        assert out[0] == f"would: hold DRE-0000 — {line}"
        assert "\n".join(out[1:-1]) == f"would: ask DRE-0000 — {body}"
        assert out[-1] == "would: leave DRE-0000 in In Review for the operator"

    def test_a_dry_run_with_the_lane_makes_no_linear_call(self, tmp_path, monkeypatch,
                                                         capsys):
        linear = _NoLinear(monkeypatch)
        code = pcp.main(["park", "DRE-0000", "--escalation-file", _file(tmp_path),
                         "--lane", "In Review", "--dry-run"])
        assert code == 0 and linear.calls == []
        out = capsys.readouterr().out.rstrip("\n").split("\n")
        assert out[-1] == "would: leave DRE-0000 in In Review for the operator"

    def test_without_the_lane_it_reads_the_lane_off_the_card_and_prints_it(
            self, tmp_path, monkeypatch, capsys):
        linear = _NoLinear(monkeypatch, lane="Green Light")
        code = pcp.main(["park", "DRE-0000", "--escalation-file", _file(tmp_path),
                         "--dry-run"])
        assert code == 0
        assert linear.calls == [("card", "DRE-0000")]
        out = capsys.readouterr().out.rstrip("\n").split("\n")
        assert out[-1] == "would: leave DRE-0000 in Green Light for the operator"

    def test_live_it_reads_the_thread_holds_asks_and_moves_nothing(
            self, tmp_path, monkeypatch, capsys):
        linear = _NoLinear(monkeypatch)
        code = pcp.main(["park", "DRE-0000", "--escalation-file", _file(tmp_path),
                         "--run-url", RUN])
        assert code == 0
        body = pcp.question(ROWS, recommendation="Drop both — nobody reads them",
                            run_url=RUN)
        assert linear.calls == [("card", "DRE-0000"), ("thread", "DRE-0000"),
                                ("hold", "DRE-0000", *pcp.hold(ROWS)),
                                ("comment", "DRE-0000", body)]
        out = capsys.readouterr().out.rstrip("\n").split("\n")
        assert out[-1] == "left DRE-0000 in In Review for the operator"
        assert "held, asked" in out[-2]

    def test_live_over_an_asked_thread_posts_nothing_and_exits_zero(
            self, tmp_path, monkeypatch, capsys):
        thread = [_hold_voice(), _voice(pcp.question(ROWS))]
        linear = _NoLinear(monkeypatch, thread=thread)
        code = pcp.main(["park", "DRE-0000", "--escalation-file", _file(tmp_path),
                         "--lane", "In Review"])
        assert code == 0
        assert linear.calls == [("thread", "DRE-0000")]
        out = capsys.readouterr().out.rstrip("\n").split("\n")
        assert out[-1] == "left DRE-0000 in In Review for the operator"
        assert "already asked" in out[-2]

    def test_from_the_record_it_parks_the_records_cause_rows(self, tmp_path, monkeypatch,
                                                           capsys):
        linear = _NoLinear(monkeypatch)
        code = pcp.main(["park", "DRE-0000", "--record",
                         _file(tmp_path, RECORD, "record.md"), "--lane", "In Review"])
        assert code == 0
        assert linear.calls[1] == ("hold", "DRE-0000", *pcp.hold(ROWS))
        assert linear.calls[2] == ("comment", "DRE-0000", pcp.question(ROWS))
        assert [c[0] for c in linear.calls] == ["thread", "hold", "comment"]

    @pytest.mark.parametrize("flag", ["--escalation-file", "--record"])
    def test_a_file_with_no_cause_row_exits_two_saying_so(self, tmp_path, flag):
        done = _cli("park", "DRE-0000", flag,
                    _file(tmp_path, "Nothing here names a cause.\n"),
                    "--lane", "In Review", "--dry-run")
        assert done.returncode == 2
        assert "cause:" in done.stderr and done.stdout == ""

    def test_a_fifth_word_exits_one_saying_so(self, tmp_path):
        done = _cli("park", "DRE-0000", "--escalation-file",
                    _file(tmp_path, "cause: impossible — “A” — b\n"),
                    "--lane", "In Review", "--dry-run")
        assert done.returncode == 1
        assert "impossible" in done.stderr
        for word in proof_reach.causes():
            assert word in done.stderr

    def test_a_refused_write_exits_one(self, tmp_path, monkeypatch, capsys):
        linear = _NoLinear(monkeypatch)
        monkeypatch.setattr(linear_ops, "cmd_comment",
                            lambda *a, **k: linear_ops.COMMENT_CAP_CONDITION)
        code = pcp.main(["park", "DRE-0000", "--escalation-file", _file(tmp_path),
                         "--lane", "In Review"])
        assert code == 1
        assert "full" in capsys.readouterr().err
        assert [c[0] for c in linear.calls] == ["thread", "hold"]

    def test_an_unread_thread_exits_one_and_writes_nothing(self, tmp_path, monkeypatch,
                                                         capsys):
        linear = _NoLinear(monkeypatch)
        monkeypatch.setattr(linear_ops, "read_card_voices", lambda ident: None)
        code = pcp.main(["park", "DRE-0000", "--escalation-file", _file(tmp_path),
                         "--lane", "In Review"])
        assert code == 1 and linear.calls == []
        assert "thread" in capsys.readouterr().err

    def test_the_file_and_the_record_are_one_or_the_other(self, tmp_path):
        done = _cli("park", "DRE-0000", "--escalation-file", _file(tmp_path),
                    "--record", _file(tmp_path, RECORD, "record.md"), "--dry-run")
        assert done.returncode == 2 and "not allowed with" in done.stderr
