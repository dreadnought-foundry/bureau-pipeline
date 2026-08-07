"""Propose-gate history is recorded truthfully (DRE-2293).

The README's "no propose-first hard stop" section used to claim the relay's
propose routing "was canceled and never deployed". False: it WAS deployed and
ran ~six weeks, dispatching every ordinary standalone card as `agent-propose`
— an event type no workflow consumed, so those cards were logged as handled
and silently never built (DRE-1980; removed by agent-bureau PR #2008). The
`fast-track` label folklore was the hand-workaround for that bug, never a
convention. These tests pin the corrected record so it can't silently revert.
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"


class TestProposeGateHistory(unittest.TestCase):
    def setUp(self):
        self.text = README.read_text()
        # The README hard-wraps prose, so match on whitespace-normalized
        # text — the false claim spans a line break in the original.
        self.flat = " ".join(self.text.split())

    def test_false_never_deployed_claim_is_gone(self):
        # The exact folklore-seeding sentence must not survive in any form.
        self.assertNotIn("canceled and never deployed", self.flat)
        self.assertNotIn("cancelled and never deployed", self.flat)
        self.assertNotIn("never deployed", self.flat)

    def test_records_the_gate_actually_ran(self):
        # The corrected record: deployed, ran ~six weeks, dispatched the
        # unconsumed agent-propose type, removed by DRE-1980 / PR #2008.
        self.assertIn("agent-propose", self.text)
        self.assertIn("six weeks", self.text)
        self.assertIn("DRE-1980", self.text)
        self.assertIn("#2008", self.text)

    def test_retires_the_fast_track_workaround(self):
        # fast-track must be named AND explicitly disclaimed as a
        # convention — it was the hand-fix for the bug, nothing more.
        self.assertIn("fast-track", self.text)
        self.assertIn("not a convention", self.text)


if __name__ == "__main__":
    unittest.main()
