"""One writer parks a proof card on its cause rows (DRE-6612).

A record's `Not observed. cause: <word> — <what the run saw>` rows (DRE-6607)
are met by the proof run that wrote them and by the proof dispatcher. Either
way the hold, the one question and the move to Green Light are composed and
written by `scripts/proof_cause_park.py`, and the lane contract declares its
`park` as the site. These tests read the composition through the modules that
declare each half — `console_escalation` for the three lines,
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
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
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
        assert pcp.QUESTION_OPENING == "🙋 The proof run cannot observe"
        assert pcp.QUESTION == ("Accept each row as out of date, reword it on a new "
                                "card, or drop it?")

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
        assert first == "🙋 The proof run cannot observe 2 row(s) as worded, and asks:"
        assert body.split("\n")[1] == ""

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
        assert console_escalation.parse(body).recommendation == "Drop both"

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
    """`linear_ops`' three writers, faked: each call recorded in order, and
    each post added to the thread so a later `park` reads it."""

    def __init__(self, monkeypatch, voices=None, *, moved=True):
        self.calls: list = []
        self.voices = list(voices or [])
        self.moved = moved
        monkeypatch.setattr(linear_ops, "cmd_proof_waiting", self.proof_waiting)
        monkeypatch.setattr(linear_ops, "cmd_comment", self.comment)
        monkeypatch.setattr(linear_ops, "cmd_advance", self.advance)

    def proof_waiting(self, ident, observed, needs):
        self.calls.append(("hold", ident, observed, needs))
        self.voices.append(_voice(linear_ops.proof_waiting_line(observed, needs)
                                  + "\n\n<!-- act trailer -->"))

    def comment(self, ident, body, *flags):
        self.calls.append(("comment", ident, body))
        self.voices.append(_voice(body))
        return None

    def advance(self, ident, to_state, from_states, *flags, held=False):
        self.calls.append(("advance", ident, to_state, from_states))
        return self.moved


def _hold_voice(rows=ROWS) -> spoken_thread.Voice:
    return _voice(linear_ops.proof_waiting_line(*pcp.hold(rows)))


class TestPark:
    def test_an_empty_thread_is_held_then_asked_then_moved(self, monkeypatch):
        w = Writers(monkeypatch)
        said = pcp.park(CARD, ROWS, voices=[], run_url=RUN)
        assert said == "held, asked, parked"
        assert [c[0] for c in w.calls] == ["hold", "comment", "advance"]
        assert w.calls[0] == ("hold", CARD, *pcp.hold(ROWS))
        assert w.calls[1] == ("comment", CARD, pcp.question(ROWS, run_url=RUN))
        assert w.calls[2] == ("advance", CARD, "Green Light", "Hand-work,In Review")

    def test_a_thread_already_held_and_asked_only_moves(self, monkeypatch):
        thread = [_voice("⏳ earlier"), _hold_voice(), _voice(pcp.question(ROWS))]
        w = Writers(monkeypatch)
        assert pcp.park(CARD, ROWS, voices=thread) == "already asked — parked"
        assert [c[0] for c in w.calls] == ["advance"]

    def test_a_thread_held_with_no_question_is_asked_only(self, monkeypatch):
        w = Writers(monkeypatch)
        said = pcp.park(CARD, ROWS, voices=[_hold_voice()])
        assert [c[0] for c in w.calls] == ["comment", "advance"]
        assert said == "asked, parked"

    def test_a_question_before_the_hold_does_not_count(self, monkeypatch):
        thread = [_voice(pcp.question(ROWS)), _hold_voice()]
        w = Writers(monkeypatch)
        pcp.park(CARD, ROWS, voices=thread)
        assert [c[0] for c in w.calls] == ["comment", "advance"]

    def test_a_different_hold_is_not_this_hold(self, monkeypatch):
        thread = [_hold_voice(ROWS[:1]), _voice(pcp.question(ROWS[:1]))]
        w = Writers(monkeypatch)
        assert pcp.park(CARD, ROWS, voices=thread) == "held, asked, parked"
        assert [c[0] for c in w.calls] == ["hold", "comment", "advance"]

    def test_a_dry_run_prints_and_calls_nothing(self, monkeypatch, capsys):
        w = Writers(monkeypatch)
        said = pcp.park(CARD, ROWS, voices=[], live=False)
        assert w.calls == []
        out = capsys.readouterr().out
        assert out.strip() == f"would: park {CARD} — {NEEDS}"
        assert said.startswith("would: park")

    def test_no_rows_is_nothing_to_do(self, monkeypatch):
        w = Writers(monkeypatch)
        said = pcp.park(CARD, [], voices=[])
        assert said.startswith("nothing to do — ")
        assert w.calls == []

    def test_two_parks_over_one_thread_leave_one_hold_and_one_question(self, monkeypatch):
        w = Writers(monkeypatch)
        assert pcp.park(CARD, ROWS, voices=w.voices, run_url=RUN) == "held, asked, parked"
        assert pcp.park(CARD, ROWS, voices=list(w.voices), run_url=RUN) == (
            "already asked — parked")
        assert [c[0] for c in w.calls] == ["hold", "comment", "advance", "advance"]
        bodies = [v.body for v in w.voices]
        assert sum(b.startswith(proof_record.HOLD_MARK) for b in bodies) == 1
        assert sum(b.startswith(pcp.QUESTION_OPENING) for b in bodies) == 1

    def test_a_full_thread_is_never_moved_with_no_question(self, monkeypatch):
        w = Writers(monkeypatch, [_hold_voice()])
        monkeypatch.setattr(linear_ops, "cmd_comment",
                            lambda *a, **k: linear_ops.COMMENT_CAP_CONDITION)
        with pytest.raises(linear_ops.LinearError):
            pcp.park(CARD, ROWS, voices=w.voices)
        assert [c[0] for c in w.calls] == []

    def test_a_refused_move_is_said(self, monkeypatch):
        Writers(monkeypatch, moved=False)
        with pytest.raises(linear_ops.LinearError) as caught:
            pcp.park(CARD, ROWS, voices=[])
        assert "Green Light" in str(caught.value)


# --------------------------------------------------------------------------- #
# the CLI — it writes nothing                                                  #
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
        assert "park(" not in cli
        assert "linear_ops" not in cli
