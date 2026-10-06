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

DRE-5923 adds the paragraph that follows once DRE-5920's siblings land: the
pipeline starts the proof run itself, on its own workflow and with scripted
read-only identities, and the run opens the record as a pull request on
`agent/DRE-<n>-proof-record`. The card closes on the approved merge of that
record. A step only the CEO's own login can make is written
`needs the CEO's press: <the press>`, so the run can name it back.
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

    def test_the_pipeline_starts_the_proof_run_itself(self):
        self.assertSays("The pipeline starts the proof run itself (DRE-5920)")
        self.assertSays("the sweep dispatches a proof run at the PROOF card")

    def test_the_trigger_is_read_off_the_release_record(self):
        self.assertSays("the release carrying those merges is live")
        self.assertSays("read off the release record")
        self.assertSays("never assumed")

    def test_the_run_is_its_own_workflow_with_read_only_identities(self):
        self.assertSays("`proof-task.yml`")
        self.assertSays("role `proof`")
        self.assertSays("`briefs/proof.md`")
        self.assertSays("scripted read-only identities")
        self.assertSays("never a person's account")

    def test_the_record_is_a_pull_request_on_the_record_branch(self):
        self.assertSays("opens the record as a pull request")
        self.assertSays("`agent/DRE-<n>-proof-record`")

    def test_the_card_closes_on_the_approved_merge_of_the_record(self):
        self.assertSays("The card closes on the approved merge of that record")
        self.assertSays("`card-done` under DRE-5919's PROOF rule")
        self.assertSays("`hyg-proof-closed`")
        self.assertSays("nothing waits on him")

    def test_a_ceo_only_press_is_named_and_parked_once(self):
        self.assertSays("needs the CEO's press:")
        self.assertSays("parks the card once in Green Light")
        self.assertSays("nobody chases it")

    def test_the_proof_still_routes_workbench_or_operator(self):
        self.assertSays("The card still routes `WORKBENCH` or `OPERATOR`")
        self.assertSays("the five conditions do not change")


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

    def test_it_says_the_pipeline_dispatches_the_proof_run(self):
        self.assertSays("The pipeline dispatches the proof run itself")

    def test_the_record_path_is_where_the_run_writes(self):
        self.assertSays("where the proof run writes")
        self.assertSays("`agent/DRE-<n>-proof-record`")

    def test_live_facts_name_the_identity_they_are_read_as(self):
        self.assertSays("as the proof-reader identity")

    def test_a_ceo_only_step_is_its_own_criterion(self):
        self.assertSays("its own criterion opening `needs the CEO's press:`")
        self.assertSays("a run with read access can observe")

    def test_the_card_closes_on_the_approved_merge_of_the_record(self):
        self.assertSays("closes on the approved merge of the record")
        self.assertSays("the closing line the check reads stays verbatim")


if __name__ == "__main__":
    unittest.main()
