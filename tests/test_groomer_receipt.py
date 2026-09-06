"""The groomer's receipt line, read off `proposal.json` (DRE-3153).

The workflow posts one `🧠 model-attempt:` comment after a judged `propose`,
and the line is composed HERE rather than in YAML — the same rule
`plan.yml`'s classifier heartbeat follows, and for the same reason: a receipt
assembled in a shell string is a second answer to "which model answered" that
nothing tests.

What the reader owes, and what it must refuse to invent:

  * a judged run says which model answered and how many calls it took;
  * `judgement.output_budget` and `judgement.truncated` are DRE-3259's keys, so
    a `proposal.json` written before that card carries NEITHER — an absent key
    is "not reported", the clause is omitted, and no number is guessed;
  * a run that made no call — `--no-judgement`, or a judgement that never
    reached a model — gets no comment at all, because there is no model attempt
    to report.

Run: cd bureau-pipeline && python3 -m pytest tests/test_groomer_receipt.py -v
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import groomer_receipt  # noqa: E402

#: Exactly the keys DRE-3150 shipped in the `judgement` block — no
#: `output_budget`, no `truncated`. This is what a proposal written before
#: DRE-3259 looks like, and the reader has to cope with it forever.
SHIPPED = {
    "enabled": True,
    "calls": 1,
    "model_asked": "claude-opus-5",
    "model_answered": "claude-opus-5",
    "receipt": "claude-opus-5 (asked) / claude-opus-5 (answered)",
    "pack": {},
    "unranked": [],
    "withheld": [],
    "problem": None,
}


def _proposal(**judgement) -> dict:
    block = dict(SHIPPED)
    block.update(judgement)
    return {"lane": "Intake", "id": "abc123", "judgement": block}


class ShippedKeysOnlyTest(unittest.TestCase):
    """A proposal from before DRE-3259 — the line ends after the call count."""

    def test_the_line_names_the_model_that_answered_and_the_call_count(self):
        line = groomer_receipt.receipt_line(_proposal())
        self.assertEqual(
            line,
            "🧠 model-attempt: claude-opus-5 (asked) / claude-opus-5 "
            "(answered) — groomer judgement ranked the census in 1 call",
        )

    def test_it_opens_with_the_marker_the_ladders_readers_find(self):
        self.assertTrue(
            groomer_receipt.receipt_line(_proposal()).startswith(
                "🧠 model-attempt:"),
            "the same marker plan.yml posts for the classifier",
        )

    def test_it_invents_no_budget_and_no_cut(self):
        line = groomer_receipt.receipt_line(_proposal())
        self.assertNotIn("output budget", line)
        self.assertNotIn("cut short", line)

    def test_a_degraded_receipt_travels_verbatim(self):
        """The receipt string is `planning_classify.model_receipt`'s, read out
        of the proposal — never recomposed here, or DEGRADED could be dropped
        by the second writer."""
        line = groomer_receipt.receipt_line(_proposal(
            receipt="DEGRADED claude-opus-5 (asked) / claude-fable-5-1 (answered)"))
        self.assertIn(
            "🧠 model-attempt: DEGRADED claude-opus-5 (asked) / "
            "claude-fable-5-1 (answered) —", line)


class BudgetAndCutTest(unittest.TestCase):
    """DRE-3259's two keys, read here when present."""

    def test_a_reported_budget_is_named(self):
        line = groomer_receipt.receipt_line(_proposal(output_budget=18580))
        self.assertIn("in 1 call, output budget 18580 tokens", line)
        self.assertNotIn("cut short", line)

    def test_a_cut_answer_says_so_and_counts_what_it_lost(self):
        line = groomer_receipt.receipt_line(_proposal(
            truncated=True, unranked=["DRE-1", "DRE-2", "DRE-3"]))
        self.assertTrue(
            line.endswith(" — answer cut short; 3 cards unranked for it"), line)

    def test_both_keys_read_in_the_contracted_order(self):
        line = groomer_receipt.receipt_line(_proposal(
            output_budget=32000, truncated=True, unranked=["DRE-1"]))
        self.assertEqual(
            line,
            "🧠 model-attempt: claude-opus-5 (asked) / claude-opus-5 "
            "(answered) — groomer judgement ranked the census in 1 call, "
            "output budget 32000 tokens — answer cut short; 1 card unranked "
            "for it",
        )

    def test_a_false_truncated_is_not_a_cut(self):
        line = groomer_receipt.receipt_line(_proposal(
            truncated=False, unranked=["DRE-1"]))
        self.assertNotIn("cut short", line)

    def test_a_budget_of_zero_is_not_reported(self):
        """0 is what `_annotate` writes when no call was made — a number, but
        not a budget anything was asked with. Omitted rather than printed as
        `output budget 0 tokens`, which would be a false fact rather than a
        missing one."""
        self.assertNotIn("output budget",
                         groomer_receipt.receipt_line(_proposal(output_budget=0)))


class NothingToReportTest(unittest.TestCase):
    """No call, no receipt. Skipped entirely — never posted empty."""

    def test_no_judgement_posts_nothing(self):
        self.assertIsNone(groomer_receipt.receipt_line(
            _proposal(enabled=False, calls=0)))

    def test_enabled_but_never_called_posts_nothing(self):
        """`enabled` with `calls: 0` is a judgement that could not pick a model
        or could not compose its prompt — no model was ever asked, so a
        model-attempt receipt would name an attempt that did not happen."""
        self.assertIsNone(groomer_receipt.receipt_line(_proposal(calls=0)))

    def test_a_proposal_with_no_judgement_block_posts_nothing(self):
        self.assertIsNone(groomer_receipt.receipt_line({"lane": "Intake"}))


class CallCountTest(unittest.TestCase):
    def test_more_than_one_call_pluralises(self):
        self.assertIn("in 2 calls",
                      groomer_receipt.receipt_line(_proposal(calls=2)))


class CliTest(unittest.TestCase):
    """The workflow's side: read a file, write the line, say nothing when
    there is nothing to say."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()

    def _write(self, proposal) -> str:
        path = os.path.join(self.tmp, "proposal.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(proposal, fh)
        return path

    def test_it_writes_the_line_to_the_out_file(self):
        out = os.path.join(self.tmp, "receipt.txt")
        rc = groomer_receipt.main(["--proposal", self._write(_proposal()),
                                   "--out", out])
        self.assertEqual(rc, 0)
        with open(out, encoding="utf-8") as fh:
            self.assertIn("🧠 model-attempt:", fh.read())

    def test_the_out_file_is_empty_when_there_is_nothing_to_post(self):
        """Empty, not absent: the workflow tests it with `[ -s ]`, and a step
        that has to distinguish 'no receipt' from 'the reader crashed' is a
        step that will get it wrong."""
        out = os.path.join(self.tmp, "receipt.txt")
        rc = groomer_receipt.main([
            "--proposal", self._write(_proposal(enabled=False, calls=0)),
            "--out", out])
        self.assertEqual(rc, 0)
        with open(out, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "")

    def test_a_missing_proposal_is_not_a_crash(self):
        """`propose` writes `proposal.json` before anything is posted (Q5), so
        an absent file means the run died before it proposed — there is
        nothing to report and nothing to fail over."""
        out = os.path.join(self.tmp, "receipt.txt")
        rc = groomer_receipt.main([
            "--proposal", os.path.join(self.tmp, "nope.json"), "--out", out])
        self.assertEqual(rc, 0)
        with open(out, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "")

    def test_unreadable_json_is_not_a_crash_either(self):
        path = os.path.join(self.tmp, "proposal.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        out = os.path.join(self.tmp, "receipt.txt")
        self.assertEqual(groomer_receipt.main(
            ["--proposal", path, "--out", out]), 0)
        with open(out, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "")


if __name__ == "__main__":                      # pragma: no cover
    unittest.main()
