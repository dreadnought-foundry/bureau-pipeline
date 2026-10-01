"""The two proof routes, as the documents must carry them (DRE-5391).

The CEO's decision of 2026-09-30 (Option C): a proof no longer waits on the CEO
signing in. Live facts are observed against the real deployment through a
scripted, limited proof-reader identity; on-screen steps are observed in a
browser on a local run of the same released commit; an on-screen step counts
as proven only when BOTH halves are seen — the screen locally and the request
it makes succeeding live. The operator runs a proof as soon as its build cards
finish.

The pre-approval critic and the planner read the epic shape out of exactly two
documents — `standards/card-quality.md` and `briefs/planner.md` — so a rule
stated anywhere else is a rule neither of them is handed. Each assertion below
goes red if the sentence it names is removed from the section it belongs in.
The five conditions the gate enforces are not restated here; their own tests
(`test_proof_and_demo*.py`) pin them, and one assertion below pins only that
this change left the numbered list at five.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STANDARD = ROOT / "standards" / "card-quality.md"
BRIEF = ROOT / "briefs" / "planner.md"

PROOF_HEADING = r"^## Every epic ends with a proof card"


def _loose(needle: str) -> re.Pattern:
    """`needle` with every run of whitespace made elastic — the documents
    hard-wrap at 80 columns, and a phrase falling across a line break is not a
    document that stopped saying it."""
    return re.compile(r"\s+".join(re.escape(w) for w in needle.split()), re.I)


def _proof_section(path: Path) -> str:
    """The `Every epic ends with a proof card` section of `path`, heading to
    the next `## ` heading."""
    text = path.read_text(encoding="utf-8")
    found = re.search(PROOF_HEADING + r".*?(?=^## )", text, re.S | re.M)
    assert found, f"{path.name} must still carry the proof-card section"
    return found.group(0)


class StandardTest(unittest.TestCase):
    """`standards/card-quality.md` — the contract the critic reads."""

    def setUp(self):
        self.section = _proof_section(STANDARD)

    def assertSays(self, phrase: str):
        self.assertRegex(self.section, _loose(phrase),
                         f"the standard's proof section must say {phrase!r}")

    def test_it_names_the_decision(self):
        self.assertIn("2026-09-30", self.section)

    def test_proofs_never_wait_on_the_ceos_sign_in(self):
        self.assertSays("Proofs never wait on the CEO's sign-in")
        self.assertSays("needed only for a real business decision")

    def test_the_operator_runs_a_proof_when_its_build_cards_finish(self):
        self.assertSays("the operator runs a proof as soon as its build cards finish")

    def test_live_facts_route_through_the_proof_reader_identity(self):
        self.assertSays("Live facts")
        self.assertSays("proof-reader identity")

    def test_on_screen_steps_route_through_a_local_run_of_the_released_commit(self):
        self.assertSays("On-screen steps")
        self.assertSays("on a local run of the same released commit")

    def test_an_on_screen_step_needs_both_halves(self):
        self.assertSays("counts as proven only when both halves are seen")
        self.assertSays("its screen behavior on the local run")
        self.assertSays("the request it makes succeeding live")

    def test_the_routing_and_the_five_conditions_are_unchanged(self):
        self.assertSays("never `FLEET`")
        numbered = re.findall(r"^(\d+)\. ", self.section, re.M)
        self.assertEqual(numbered, ["1", "2", "3", "4", "5"],
                         "the proof section carries exactly the five conditions")


class PlannerBriefTest(unittest.TestCase):
    """`briefs/planner.md` — how the planner writes the proof card."""

    def setUp(self):
        self.section = _proof_section(BRIEF)

    def assertSays(self, phrase: str):
        self.assertRegex(self.section, _loose(phrase),
                         f"the planner brief's proof section must say {phrase!r}")

    def test_it_says_proofs_never_wait_on_the_ceos_sign_in(self):
        self.assertSays("Proofs never wait on the CEO's sign-in")

    def test_an_on_screen_step_is_written_as_two_observations(self):
        self.assertSays("Write every on-screen step as two observations")
        self.assertSays("Local screen:")
        self.assertSays("Live request:")

    def test_live_facts_are_written_against_the_proof_reader_identity(self):
        self.assertSays("proof-reader identity")


if __name__ == "__main__":
    unittest.main()
