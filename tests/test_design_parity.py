"""Design-parity standard — cards must sum to the design (DRE-2116).

The DeltaSolv lesson (2026-07-13 gap audit): every card was built and verified
AS WRITTEN, yet the shipped product missed the design badly — ~67 designed
screens never carded, Done cards with unmet acceptance criteria, load-bearing
facades (no web login, fake transcription). The failure was upstream of the
build loop: nothing checked that the CARDS SUM TO THE DESIGN. These tests pin
the fix:

  1. The STANDARD — standards/design-parity.md exists, rides the same
     assemble_context.py rail as its siblings (planner authors plans under it;
     the critic and verifier review under it), and names the load-bearing
     conventions: the `deferred: <surface> — <reason>` line, the `**Design:**`
     ref, the no-fake-states lens, and the epic-close deferred ledger.
  2. The BRIEF — briefs/planner.md points the planner at the obligation, so
     plans are authored to it, not just reviewed against it.
  3. The CHECK — scripts/design_parity.py is the mechanical form of the
     parity check the standard specifies: given the designed surfaces, the
     plan's card bodies, and the plan comment, every surface must be accounted
     for by a card's `**Design:**` ref or an explicit deferred line. The
     DeltaSolv scenario (designed login screen, no card ever filed, no
     deferred line) must come back as unaccounted — caught at plan review
     time, not at the gap audit.
"""

import os
import sys
import unittest

REPO = os.path.join(os.path.dirname(__file__), "..")
SCRIPTS = os.path.join(REPO, "scripts")
STANDARD = os.path.join(REPO, "standards", "design-parity.md")
PLANNER_BRIEF = os.path.join(REPO, "briefs", "planner.md")
sys.path.insert(0, SCRIPTS)

import assemble_context as ac  # noqa: E402


def _check():
    import design_parity  # deferred: RED until DRE-2116 lands the script

    return design_parity


# The DeltaSolv shape: a design inventory with a login screen, an epic whose
# cards cover every OTHER surface, and a plan comment with no deferred line.
SURFACES = [
    "console/design/images/screens/desktop/login.png",
    "console/design/images/screens/desktop/board.png",
    "console/design/images/screens/desktop/settings.png",
]
CARD_BOARD = (
    "**Design:** console/design/images/screens/desktop/board.png\n\n"
    "Build the board view.\n\n## Acceptance criteria\n- [ ] board renders"
)
CARD_SETTINGS = (
    "**Design:** console/design/images/screens/desktop/settings.png\n\n"
    "Build the settings screen.\n\n## Acceptance criteria\n- [ ] settings save"
)
PLAN_COMMENT_SILENT = "We will build the board and settings screens first."
PLAN_COMMENT_DEFERRED = (
    "We will build the board and settings screens first.\n"
    "deferred: login — auth vendor decision pending, tracked for phase 2"
)


class StandardOnTheRailTest(unittest.TestCase):
    """The standard must exist and reach the plan-authoring and plan/PR-review
    roles via assemble_context.py — the DRE-1646 single-source rail."""

    def test_standard_file_exists(self):
        self.assertTrue(
            os.path.isfile(STANDARD), "standards/design-parity.md must exist"
        )

    def test_planner_critic_verifier_receive_the_standard(self):
        # Injection scope per DRE-2116: the planner authors plans under the
        # obligation; the critic (qa-review) and verifier (verify) review
        # Design-bearing PRs under the lens.
        for role in ("planner", "critic", "verifier"):
            self.assertIn(
                "design-parity.md",
                ac.standards_for(role),
                f"{role} must receive the design-parity standard",
            )

    def test_standard_names_the_load_bearing_conventions(self):
        # The exact strings agents act on. Drift here means the planner writes
        # deferred lines the check cannot parse, or the critic never learns
        # what a blocking parity finding is.
        body = open(STANDARD).read()
        for needle, why in [
            ("deferred: <surface> — <reason>", "the explicit-deferral grammar"),
            ("**Design:**", "the card design-ref convention"),
            ("ledger", "the epic-close deferred ledger"),
            ("scripts/design_parity.py", "the mechanical form of the check"),
        ]:
            self.assertIn(needle, body, f"standard must name {why}: {needle!r}")

    def test_standard_carries_the_no_fake_states_lens(self):
        # DeltaSolv shipped spinners that never resolved and "AI-suggested"
        # text that was hardcoded — the verifier lens must name fake states
        # explicitly or the finding stays unblockable.
        body = open(STANDARD).read().lower()
        for word in ("spinner", "hardcoded"):
            self.assertIn(
                word, body, f"standard must name fake states ({word!r})"
            )

    def test_planner_brief_references_the_obligation(self):
        self.assertIn(
            "design-parity.md",
            open(PLANNER_BRIEF).read(),
            "briefs/planner.md must point the planner at the parity obligation",
        )


class ParityCheckTest(unittest.TestCase):
    """scripts/design_parity.py — the check the standard specifies. A surface
    is accounted for ONLY by a card's `**Design:**` ref or an explicit
    deferred line with a reason; anything else is a planning defect."""

    def test_deltasolv_scenario_is_caught(self):
        # Designed login screen, no card ever filed, no deferred line: the
        # exact failure the 2026-07-13 gap audit found AFTER the epic closed.
        # Under the standard it surfaces at plan review time instead.
        missing = _check().unaccounted_surfaces(
            SURFACES, [CARD_BOARD, CARD_SETTINGS], PLAN_COMMENT_SILENT
        )
        self.assertEqual(
            missing,
            ["console/design/images/screens/desktop/login.png"],
            "the never-carded login screen must be flagged as unaccounted",
        )

    def test_explicit_deferral_accounts_for_a_surface(self):
        # The standard's escape hatch: a `deferred: <surface> — <reason>` line
        # in the plan comment is a decision, not an omission.
        missing = _check().unaccounted_surfaces(
            SURFACES, [CARD_BOARD, CARD_SETTINGS], PLAN_COMMENT_DEFERRED
        )
        self.assertEqual(missing, [], "an explicitly deferred surface is accounted for")

    def test_deferral_without_a_reason_does_not_count(self):
        # "deferred: login" with no reason is still a silent omission dressed
        # up — the grammar requires the reason so the CEO can judge it.
        missing = _check().unaccounted_surfaces(
            SURFACES, [CARD_BOARD, CARD_SETTINGS], "deferred: login —"
        )
        self.assertIn(
            "console/design/images/screens/desktop/login.png",
            missing,
            "a reason-less deferred line must not account for the surface",
        )

    def test_prose_mention_without_design_ref_does_not_count(self):
        # A card that talks ABOUT the login screen but carries no **Design:**
        # ref is exactly the facade path (built to text, not to the design) —
        # only the ref accounts for the surface.
        prose_card = (
            "Add a login form somewhere.\nMentions login.png in passing.\n\n"
            "## Acceptance criteria\n- [ ] users can log in"
        )
        missing = _check().unaccounted_surfaces(
            SURFACES,
            [CARD_BOARD, CARD_SETTINGS, prose_card],
            PLAN_COMMENT_SILENT,
        )
        self.assertIn(
            "console/design/images/screens/desktop/login.png",
            missing,
            "a prose mention without a **Design:** ref must not account for it",
        )

    def test_design_ref_matches_by_basename(self):
        # Planners sometimes write the ref with a different root (repo-relative
        # vs design-dir-relative); the surface is the SCREEN, so match on the
        # screen filename, not the exact path string.
        card = "**Design:** design/images/screens/desktop/login.png\n\nBuild login."
        missing = _check().unaccounted_surfaces(
            SURFACES, [card, CARD_BOARD, CARD_SETTINGS], PLAN_COMMENT_SILENT
        )
        self.assertEqual(missing, [], "a basename match on the ref accounts for it")

    def test_multiple_refs_on_one_design_line(self):
        # One card may legitimately own two closely-coupled screens.
        card = (
            "**Design:** console/design/images/screens/desktop/login.png, "
            "console/design/images/screens/desktop/board.png\n\nBuild both."
        )
        missing = _check().unaccounted_surfaces(
            SURFACES, [card, CARD_SETTINGS], PLAN_COMMENT_SILENT
        )
        self.assertEqual(missing, [], "every ref on the **Design:** line counts")

    def test_deferred_surfaces_parser_for_the_epic_close_ledger(self):
        # The epic-close gate needs the deferred list back OUT of the plan
        # comment to write the ledger; hyphenated surface names must survive
        # (the dash separator requires surrounding spaces).
        deferred = _check().deferred_surfaces(
            "deferred: sign-in — SSO vendor undecided\n"
            "- deferred: audit-log.png — phase 2 scope\n"
            "not a deferred line\n"
        )
        self.assertEqual(deferred, ["sign-in", "audit-log.png"])


if __name__ == "__main__":
    unittest.main()
