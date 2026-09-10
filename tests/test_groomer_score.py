"""Scoring the groomer's judgement against a hand audit it never saw (DRE-3155).

The planner has an audit (DRE-3016) and the critic had one before it (DRE-2685).
The groomer's judgement — the one ranked read that says `now`, `not-now`,
`likely-done` or nothing at all — has had none: the CEO reads a batch, approves
it or does not, and whether the read was any good is learned one cycle at a
time.

This is the same instrument, pointed at the groomer, built on the same rules:

  * **The held-out answer is the CEO's own**, written on DRE-3151 BEFORE he
    reads the model's reason. The scorer never fetches it — the audit card's
    comments arrive on stdin.
  * **A contaminated dimension is never scored.** `constraints` — collisions,
    blockers, epic units and capacity — is enforced by `groomer.sequence` and
    its tests, so scoring it grades the code rather than the judgement. The
    exclusion checks ITSELF, by reading `scripts/groomer.py` for the constraint
    functions it names.
  * **Agreement and disagreement are equal results**, and both are printed,
    empty or not.
  * **A row nobody answered is UNKNOWN** and out of the rate — a blank or
    `unsure` CEO column is the absence of an answer, not an agreement.
  * **`unranked` is counted and never scored.** A refusal is the designed
    answer (`groom_judgement.WITHHELD_REASON`), not a miss.

And the one number this exists for: a **false "done"** — the judgement calling a
card likely-done that the CEO says is live work — is the expensive miss, so it
is counted on its own line rather than folded into the rate.

Run: cd bureau-pipeline && python3 -m pytest tests/test_groomer_score.py -v
"""
from __future__ import annotations

import ast
import copy
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import groomer_score  # noqa: E402


HEADER = "| Card | Rules said | Judgement said | CEO said | Agree? |"


# --------------------------------------------------------------------------
# fixtures — hand-built, never the shipped reference, so these keep meaning
# after the audit is re-run over a different batch
# --------------------------------------------------------------------------
def comment(rows, *, header=HEADER, false_done=None, unranked=None,
            preamble="Here is what I would have chosen, written before I read "
                     "the model's reasons."):
    """One Linear comment carrying the audit table, in the contract's shape."""
    lines = [preamble, "", header, "| -- | -- | -- | -- | -- |"]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    lines.append("")
    if false_done is not None:
        lines.append(f"false-done: {false_done}")
    if unranked is not None:
        lines.append(f"unranked: {unranked}")
    return "\n".join(lines) + "\n"


def row(card, rules, judgement, ceo, agree=None):
    if agree is None:
        agree = "yes" if judgement == ceo else "no"
    return (card, rules, judgement, ceo, agree)


def proposal(rows=(), *, unranked=(), calls=1, answered="claude-fable-5-1"):
    """A groomer proposal in the shape `groomer.propose` writes it: the four
    marks DRE-3150 puts on every row, `source` on the dead ones, and the
    `judgement` block."""
    outcomes = {"now": [], "not-now": [], "dead": []}
    sequence = []
    for spec in rows:
        record = {
            "identifier": spec["card"],
            "title": spec.get("title") or f"{spec['card']} · a card",
            "reason": spec.get("reason") or "because",
            "trigger": spec.get("trigger"),
            "evidence": spec.get("evidence"),
            "judged": spec.get("judged", True),
        }
        bucket = spec.get("outcome") or "now"
        if bucket == "dead":
            record["source"] = spec.get("source") or "judgement"
        outcomes[bucket].append(record)
        sequence.append({**record, "outcome": bucket})
    return {
        "id": "f673bfefa340",
        "outcomes": outcomes,
        "sequence": sequence,
        "judgement": {"enabled": True, "calls": calls, "unranked": list(unranked),
                      "model_answered": answered},
    }


def audit(rows, **kwargs):
    return groomer_score.parse_audit([comment(rows, **kwargs)])


# --------------------------------------------------------------------------
# the reference — the vocabulary, and an exclusion that checks itself
# --------------------------------------------------------------------------
class ReferenceTest(unittest.TestCase):
    def test_the_shipped_reference_has_no_problems(self):
        """`python3 scripts/groomer_score.py check` against the file we ship."""
        self.assertEqual(groomer_score.reference_problems(), [])

    def test_the_check_command_exits_zero_on_the_shipped_reference(self):
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "groomer_score.py"), "check"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_an_excluded_dimension_naming_a_function_the_groomer_lost_is_refused(self):
        """The load-bearing half of the exclusion. `constraints` is excluded
        BECAUSE `groomer.sequence` and the functions it calls enforce it; take
        one of those away and the dimension stops being contaminated."""
        doc = copy.deepcopy(groomer_score.load())
        block = doc["dimensions"][groomer_score.CONTAMINATED_DIMENSION]
        block["enforces"] = list(block["enforces"]) + ["capacity_that_was_deleted"]
        problems = groomer_score.reference_problems(doc)
        self.assertTrue(any("capacity_that_was_deleted" in p for p in problems),
                        problems)

    def test_every_function_the_exclusion_names_is_still_defined(self):
        block = groomer_score.load()["dimensions"][
            groomer_score.CONTAMINATED_DIMENSION]
        self.assertTrue(block["enforces"], "the exclusion names no function")
        for name in block["enforces"]:
            self.assertTrue(groomer_score.groomer_defines(name),
                            f"scripts/groomer.py no longer defines {name}")

    def test_the_contaminated_dimension_may_not_be_scored(self):
        doc = copy.deepcopy(groomer_score.load())
        doc["dimensions"][groomer_score.CONTAMINATED_DIMENSION]["scored"] = True
        problems = groomer_score.reference_problems(doc)
        self.assertTrue(
            any(groomer_score.CONTAMINATED_DIMENSION in p for p in problems),
            problems)

    def test_the_reference_carries_the_header_the_parser_demands(self):
        """One contract, written once: DRE-3151 records the table, DRE-3150
        writes the proposal, and this file is where the shape is declared."""
        self.assertEqual(
            list(groomer_score.load()["contract"]["header"]),
            list(groomer_score.HEADER))

    def test_a_reference_whose_header_drifts_from_the_parser_is_refused(self):
        doc = copy.deepcopy(groomer_score.load())
        doc["contract"]["header"] = ["Card", "Judgement said", "CEO said"]
        self.assertTrue(any("header" in p for p in
                            groomer_score.reference_problems(doc)))


# --------------------------------------------------------------------------
# the parser — the exact header, and nothing else
# --------------------------------------------------------------------------
class ParserTest(unittest.TestCase):
    def test_the_contract_header_is_parsed_into_records(self):
        parsed = audit([row("DRE-2348", "now", "not-now", "not-now"),
                        row("DRE-2371", "not-now", "now", "now")])
        self.assertEqual([r["card"] for r in parsed["rows"]],
                         ["DRE-2348", "DRE-2371"])
        self.assertEqual(parsed["rows"][0]["rules"], "now")
        self.assertEqual(parsed["rows"][0]["judgement"], "not-now")
        self.assertEqual(parsed["rows"][0]["ceo"], "not-now")
        self.assertEqual(parsed["rows"][0]["agree"], "yes")

    def test_a_mismatched_header_is_refused_naming_the_header_it_expected(self):
        bad = "| Card | Judgement said | CEO said |"
        with self.assertRaises(groomer_score.AuditError) as caught:
            groomer_score.parse_audit([comment(
                [("DRE-1", "now", "not-now")], header=bad)])
        self.assertIn(groomer_score.HEADER_LINE, str(caught.exception))

    def test_a_thread_with_no_audit_table_is_refused_naming_the_header(self):
        with self.assertRaises(groomer_score.AuditError) as caught:
            groomer_score.parse_audit(["No table here, just a note."])
        self.assertIn(groomer_score.HEADER_LINE, str(caught.exception))

    def test_a_table_without_a_ceo_column_is_not_the_audit_table(self):
        """The proposal comment carries tables of its own. Only the CEO's
        column makes a table the hand audit, so the others are passed over
        rather than refused."""
        other = ("| Card | Rules said | Judgement said | Judgement's reason |\n"
                 "| -- | -- | -- | -- |\n"
                 "| DRE-9 | now | not-now | no epic in flight |\n")
        parsed = groomer_score.parse_audit(
            [other, comment([row("DRE-2348", "now", "not-now", "not-now")])])
        self.assertEqual([r["card"] for r in parsed["rows"]], ["DRE-2348"])

    def test_the_two_count_lines_are_read_off_the_same_comment(self):
        parsed = audit([row("DRE-1", "now", "likely-done", "now")],
                       false_done=1, unranked=4)
        self.assertEqual(parsed["declared"]["false-done"], 1)
        self.assertEqual(parsed["declared"]["unranked"], 4)


# --------------------------------------------------------------------------
# the score — agreement, disagreement, and the rows that are neither
# --------------------------------------------------------------------------
class ScoreTest(unittest.TestCase):
    def test_agreement_and_disagreement_are_both_counted(self):
        result = groomer_score.score(audit([
            row("DRE-1", "now", "not-now", "not-now"),
            row("DRE-2", "not-now", "now", "now"),
            row("DRE-3", "now", "not-now", "now"),
        ]))
        self.assertEqual(result["counts"]["agree"], 2)
        self.assertEqual(result["counts"]["disagree"], 1)
        self.assertEqual(result["rate"], round(2 / 3, 4))

    def test_a_blank_or_unsure_ceo_column_is_unknown_and_out_of_the_rate(self):
        result = groomer_score.score(audit([
            row("DRE-1", "now", "not-now", "not-now"),
            row("DRE-2", "now", "not-now", "", agree=""),
            row("DRE-3", "now", "not-now", "unsure", agree=""),
        ]))
        outcomes = {r["card"]: r["outcome"] for r in result["rows"]}
        self.assertEqual(outcomes["DRE-2"], "unknown")
        self.assertEqual(outcomes["DRE-3"], "unknown")
        self.assertEqual(result["counts"]["unknown"], 2)
        self.assertEqual(result["scored"], 1)
        self.assertEqual(result["rate"], 1.0)

    def test_an_unranked_row_is_counted_and_never_scored(self):
        """A refusal is the designed answer, not a miss — so it is neither an
        agreement nor a disagreement, whatever the CEO would have chosen."""
        result = groomer_score.score(audit([
            row("DRE-1", "now", "not-now", "not-now"),
            row("DRE-2", "not-now", "unranked", "now", agree="no"),
        ]))
        outcomes = {r["card"]: r["outcome"] for r in result["rows"]}
        self.assertEqual(outcomes["DRE-2"], "unranked")
        self.assertEqual(result["unranked"]["counted"], 1)
        self.assertEqual(result["scored"], 1)
        self.assertEqual(result["counts"]["disagree"], 0)

    def test_a_false_done_is_counted_on_its_own(self):
        result = groomer_score.score(audit([
            row("DRE-1", "not-now", "likely-done", "now", agree="no"),
            row("DRE-2", "not-now", "likely-done", "likely-done"),
        ]))
        self.assertEqual(result["false_done"]["counted"], 1)
        self.assertEqual(result["false_done"]["cards"], ["DRE-1"])
        # …and it is still a disagreement: the count is the expensive half of
        # the same miss, never a second population.
        self.assertEqual(result["counts"]["disagree"], 1)

    def test_a_likely_done_the_ceo_did_not_answer_is_not_a_false_done(self):
        result = groomer_score.score(audit([
            row("DRE-1", "not-now", "likely-done", "unsure", agree=""),
        ]))
        self.assertEqual(result["false_done"]["counted"], 0)
        self.assertEqual(result["counts"]["unknown"], 1)

    def test_a_card_its_own_description_condemned_is_not_the_models_call(self):
        """`groomer._mark`: a card whose description says it was superseded is
        never re-judged — the declaration a person wrote outranks the read. So
        a dead row sourced from that line is not the judgement's false done."""
        result = groomer_score.score(
            audit([row("DRE-1", "likely-done", "likely-done", "now", agree="no")]),
            proposal=proposal([{"card": "DRE-1", "outcome": "dead",
                                "source": "superseded-line", "judged": False}]),
        )
        self.assertEqual(result["false_done"]["counted"], 0)
        self.assertEqual(result["counts"]["disagree"], 1)

    def test_the_declared_counts_are_reported_beside_the_counted_ones(self):
        result = groomer_score.score(audit(
            [row("DRE-1", "not-now", "likely-done", "now", agree="no")],
            false_done=2, unranked=0))
        self.assertEqual(result["false_done"]["declared"], 2)
        self.assertEqual(result["false_done"]["counted"], 1)
        self.assertTrue(result["notes"], "a declared count that does not match "
                                         "the table is reported")


# --------------------------------------------------------------------------
# the trigger split — an event a person will notice, or a date nobody watches
# --------------------------------------------------------------------------
class TriggerTest(unittest.TestCase):
    def test_a_cycle_number_and_a_bare_date_are_dates_nobody_watches(self):
        for text in ("when cycle 14 opens", "2026-09-14",
                     "seven clean console days after 2026-09-07"):
            self.assertEqual(groomer_score.trigger_kind(text), "date", text)

    def test_a_thing_that_happens_somewhere_we_watch_is_an_event(self):
        for text in ("when DRE-3179 lands", "operator pushes the built branch",
                     "when the auto-switch epic closes"):
            self.assertEqual(groomer_score.trigger_kind(text), "event", text)

    def test_a_missing_trigger_is_never_counted_as_an_event(self):
        self.assertEqual(groomer_score.trigger_kind(None), "unknown")
        self.assertEqual(groomer_score.trigger_kind("  "), "unknown")

    def test_the_split_is_taken_over_the_not_now_rows_of_the_proposal(self):
        result = groomer_score.score(
            audit([row("DRE-1", "now", "not-now", "not-now"),
                   row("DRE-2", "now", "not-now", "not-now"),
                   row("DRE-3", "not-now", "now", "now")]),
            proposal=proposal([
                {"card": "DRE-1", "outcome": "not-now",
                 "trigger": "when DRE-3179 lands"},
                {"card": "DRE-2", "outcome": "not-now",
                 "trigger": "when cycle 14 opens"},
                {"card": "DRE-3", "outcome": "now"},
            ]))
        self.assertEqual(result["triggers"]["event"], 1)
        self.assertEqual(result["triggers"]["date"], 1)
        self.assertEqual(result["triggers"]["unknown"], 0)

    def test_a_not_now_row_with_no_proposal_row_is_unknown_not_an_event(self):
        result = groomer_score.score(
            audit([row("DRE-1", "now", "not-now", "not-now")]),
            proposal=proposal([]))
        self.assertEqual(result["triggers"]["unknown"], 1)
        self.assertEqual(result["triggers"]["event"], 0)


# --------------------------------------------------------------------------
# the report — both halves, empty or not, and every card exactly once
# --------------------------------------------------------------------------
class ReportTest(unittest.TestCase):
    def fixture(self):
        """A thread with agreements, disagreements, an unknown, an unranked
        refusal and a false done — the whole vocabulary in one table."""
        rows = [
            row("DRE-2348", "now", "not-now", "not-now"),
            row("DRE-2371", "not-now", "now", "now"),
            row("DRE-2382", "now", "not-now", "now", agree="no"),
            row("DRE-2598", "not-now", "likely-done", "now", agree="no"),
            row("DRE-3020", "not-now", "unranked", "not-now", agree=""),
            row("DRE-3085", "not-now", "not-now", "unsure", agree=""),
        ]
        prop = proposal([
            {"card": "DRE-2348", "outcome": "not-now",
             "trigger": "when the groomer epic closes"},
            {"card": "DRE-2371", "outcome": "now"},
            {"card": "DRE-2382", "outcome": "not-now",
             "trigger": "when cycle 13 opens"},
            {"card": "DRE-2598", "outcome": "dead", "source": "judgement",
             "evidence": "DRE-3181 Done"},
            {"card": "DRE-3020", "outcome": "not-now",
             "trigger": "when somebody raises its priority"},
            {"card": "DRE-3085", "outcome": "not-now",
             "trigger": "2026-09-14"},
        ], unranked=["DRE-3020"])
        return groomer_score.score(audit(rows, false_done=1, unranked=1),
                                   proposal=prop)

    def test_both_halves_are_printed_even_when_one_is_empty(self):
        result = groomer_score.score(
            audit([row("DRE-1", "now", "not-now", "not-now")]))
        report = groomer_score.render_report(result)
        self.assertEqual(result["counts"]["disagree"], 0)
        for heading in ("## Agreement", "## Disagreement"):
            self.assertIn(heading, report)
        self.assertIn("*(none)*", report)

    def test_the_fixture_thread_produces_both_halves(self):
        report = groomer_score.render_report(self.fixture())
        agreement, _, rest = report.partition("## Disagreement")
        self.assertIn("| DRE-2348 |", agreement)
        self.assertIn("| DRE-2371 |", agreement)
        self.assertIn("| DRE-2382 |", rest)
        self.assertNotIn("| DRE-2382 |", agreement)

    def test_every_audited_card_appears_exactly_once(self):
        result = self.fixture()
        report = groomer_score.render_report(result)
        for record in result["rows"]:
            self.assertEqual(report.count(record["card"]), 1,
                             f"{record['card']} is not printed exactly once")

    def test_the_report_prints_the_rate_the_false_done_and_the_split(self):
        report = groomer_score.render_report(self.fixture())
        self.assertIn("false-done: 1", report)
        self.assertIn("unranked: 1", report)
        self.assertIn("%", report)                      # the agreement rate
        self.assertRegex(report, r"trigger[^\n]*event")
        self.assertIn("## Excluded as contaminated", report)
        self.assertIn(groomer_score.CONTAMINATED_DIMENSION, report)

    def test_the_false_done_count_has_a_line_of_its_own(self):
        report = groomer_score.render_report(self.fixture())
        lines = [ln for ln in report.splitlines() if "false-done:" in ln]
        self.assertEqual(len(lines), 1, report)
        self.assertTrue(lines[0].lstrip("*- ").startswith("false-done:"),
                        lines[0])

    def test_an_unknown_row_is_named_under_its_own_heading(self):
        report = groomer_score.render_report(self.fixture())
        _, _, rest = report.partition("## Unknown")
        self.assertIn("DRE-3085", rest)


# --------------------------------------------------------------------------
# the seam — nothing here reads Linear or GitHub
# --------------------------------------------------------------------------
class SeamTest(unittest.TestCase):
    FORBIDDEN = ("linear_ops", "subprocess", "urllib", "http", "requests",
                 "socket", "card_pr")

    def test_the_module_imports_no_client_at_all(self):
        """Not "the scoring functions do not call Linear" — the module cannot.
        The audit card's comments arrive on stdin and nothing else is read."""
        tree = ast.parse((ROOT / "scripts" / "groomer_score.py").read_text())
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported += [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
        for name in imported:
            self.assertNotIn(name.split(".")[0], self.FORBIDDEN,
                             f"{name} is imported by the scorer")

    def test_the_scoring_functions_open_no_file(self):
        """`score`, `parse_audit` and `render_report` are pure over records —
        the file reads live in `load` and in the CLI."""
        tree = ast.parse((ROOT / "scripts" / "groomer_score.py").read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            if node.name not in ("score", "parse_audit", "render_report",
                                 "parse_table", "trigger_kind"):
                continue
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call) and \
                        isinstance(inner.func, ast.Name):
                    self.assertNotEqual(inner.func.id, "open",
                                        f"{node.name} opens a file")


# --------------------------------------------------------------------------
# the CLI — stdin in, a report out
# --------------------------------------------------------------------------
class CliTest(unittest.TestCase):
    def test_score_reads_the_thread_on_stdin_and_prints_both_halves(self):
        payload = json.dumps([comment([
            row("DRE-1", "now", "not-now", "not-now"),
            row("DRE-2", "now", "not-now", "now", agree="no"),
        ], false_done=0, unranked=0)])
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "groomer_score.py"),
             "score", "--card", "DRE-3151"],
            input=payload, capture_output=True, text=True, check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("## Agreement", proc.stdout)
        self.assertIn("## Disagreement", proc.stdout)
        self.assertIn("DRE-3151", proc.stdout)
        self.assertIn("false-done: 0", proc.stdout)

    def test_a_refused_header_exits_nonzero_and_names_the_expected_header(self):
        payload = json.dumps([comment(
            [("DRE-1", "now", "not-now")],
            header="| Card | Judgement said | CEO said |")])
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "groomer_score.py"),
             "score", "--card", "DRE-3151"],
            input=payload, capture_output=True, text=True, check=False,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn(groomer_score.HEADER_LINE, proc.stderr)


if __name__ == "__main__":
    unittest.main()
