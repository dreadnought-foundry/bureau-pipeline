"""The design-system standard, and the rail that delivers it (DRE-3938).

The CEO decided on 2026-09-14 that every repo's design work follows one
`design/` standard, starts from the master template, locks tokens and atoms on
his agreement, and is gated by a design critic. The Designer role in
agent-bureau already defines "done" as that checklist passing — but nothing in
the pipeline said it WHERE THE AGENTS AND CRITICS READ, and a standard an agent
never receives is a standard nobody is held to.

These tests pin the three halves, the same way test_console_honesty_wiring.py
pins its standard (DRE-2107):

  1. The STANDARD — standards/design-system.md exists and states the folder
     layout, the lock rule, the multi-brand rule and the master-template rule,
     and links the agent-bureau ADR that is the decision record.
  2. The CHECKLIST — every check the card names is present, and each is framed
     as a question a reviewer answers FROM THE FILES ALONE.
  3. The DELIBERATE ABSENCE — the "published Claude Design project matches the
     locked files" item is NOT a check, and the standard records WHY. Without
     that record the next reader finds the ADR listing it, assumes it was
     forgotten, and puts back a gate no reviewer can actually answer.
  4. The RAIL — assemble_context.py injects it for the roles that read it
     (frontend builds design work, the critic gates it), and standards/README.md
     says so in both its index and its per-role table.
"""

import os
import re
import sys
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
STANDARD = os.path.join(REPO, "standards", "design-system.md")
README = os.path.join(REPO, "standards", "README.md")
sys.path.insert(0, os.path.join(REPO, "scripts"))

# The two headings that delimit the checklist. Sliced rather than searched
# whole-file on purpose: the absence assertions below have to be able to tell
# "the item is not a check" from "the item is discussed as deliberately absent",
# and only the slice can.
CHECKLIST_HEADING = '## The design critic\'s checklist — what gates "done"'
ABSENCE_HEADING = "## Not on the checklist, deliberately"


def body() -> str:
    with open(STANDARD, encoding="utf-8") as f:
        return f.read()


def checklist() -> str:
    text = body()
    start = text.index(CHECKLIST_HEADING)
    end = text.index(ABSENCE_HEADING)
    assert end > start, "the absence section must follow the checklist"
    return text[start:end]


def readme() -> str:
    with open(README, encoding="utf-8") as f:
        return f.read()


class StandardOnTheRailTest(unittest.TestCase):
    """The standard must exist and reach the roles that build and gate design
    work through assemble_context.py — the DRE-1646 single-source rail."""

    def test_standard_file_exists(self):
        self.assertTrue(
            os.path.isfile(STANDARD),
            "standards/design-system.md must exist",
        )

    def test_the_design_roles_receive_the_standard(self):
        import assemble_context as ac

        for role in ("frontend", "critic"):
            self.assertIn(
                "design-system.md",
                ac.standards_for(role),
                f"{role} must receive the design-system standard — a frontend "
                "build agent produces design work and the critic gates it done",
            )

    def test_the_lean_roles_do_not_carry_it(self):
        # Keeping the other roles' context lean is deliberate, exactly as it is
        # for console-honesty.md: devops authors CDK/CI, the fixer and medic work
        # inside an already-reviewed diff, and the planner's design obligation is
        # design-parity.md (cards summing to the design), not the folder shape.
        import assemble_context as ac

        for role in ("devops", "fix", "medic", "database-architect"):
            self.assertNotIn(
                "design-system.md",
                ac.standards_for(role),
                f"{role} must not carry the design-system standard",
            )

    def test_readme_index_lists_the_standard(self):
        self.assertIn(
            "design-system.md", readme(),
            "standards/README.md must list design-system.md — the index is "
            "where a reader learns the standard exists",
        )

    def test_readme_per_role_table_names_it_for_both_roles(self):
        index = readme()
        for role in ("frontend", "critic"):
            row = next((l for l in index.splitlines()
                        if re.match(rf"\|\s*{role}\s*\|", l)), "")
            self.assertTrue(row, f"standards/README.md has no {role!r} role row")
            self.assertIn(
                "design-system", row,
                "standards/README.md's per-role table is where a reader learns "
                "which standards a role gets — it must match assemble_context.py",
            )


class TheDecisionRecordTest(unittest.TestCase):
    """The standard is not its own authority — it cites the ADR the CEO's
    decision was recorded in, so a reader can check the source."""

    def test_it_links_the_agent_bureau_adr(self):
        text = body()
        self.assertIn("architecture/decisions/adr-design-system-and-new-roles.md", text)
        self.assertIn(
            "https://github.com/dreadnought-foundry/agent-bureau/blob/main/"
            "architecture/decisions/adr-design-system-and-new-roles.md",
            text,
            "the ADR lives in another repo, so the citation must be a link a "
            "reader can follow rather than a path only this repo resolves",
        )

    def test_it_dates_the_decision(self):
        self.assertIn("2026-09-14", body(), "the standard must date the decision")


class FolderLayoutTest(unittest.TestCase):
    """One `design/` folder at the repo root, the same shape everywhere. Each
    entry is asserted by name: a layout missing one is a layout two repos will
    fill in differently."""

    REQUIRED = (
        "README.md", "DESIGN.md", "tokens/", "atoms/", "molecules/",
        "organisms/", "templates/", "screens/web", "screens/mobile",
        "brand/", "decks/", "collateral/", "emails/", "handoff/", "LOCK.json",
    )

    def test_every_entry_is_named(self):
        text = body()
        missing = [e for e in self.REQUIRED if e not in text]
        self.assertFalse(
            missing, f"standards/design-system.md never names {missing}")

    def test_the_folder_is_at_the_repo_root(self):
        self.assertTrue(
            re.search(r"`design/`[^.\n]{0,80}repo\s+ROOT|repo\s+root", body(), re.I),
            "the standard must say the folder sits at the repo root",
        )

    def test_atomic_composition_is_stated_as_an_order(self):
        text = body()
        self.assertIn("Atomic composition", text)
        self.assertTrue(
            re.search(r"molecule composes atoms", text, re.I),
            "the standard must say which layer composes which, not just the "
            "word 'atomic'",
        )


class LockRuleTest(unittest.TestCase):
    """Tokens and atoms change only on the CEO's agreement, and the agreement
    is recorded in LOCK.json — the record is what makes the lock checkable."""

    def test_the_lock_rule_names_what_is_locked_and_who_agrees(self):
        text = body()
        self.assertIn("## The lock rule", text)
        self.assertTrue(
            re.search(r"only on the CEO's agreement", text),
            "the standard must say tokens and atoms move only on the CEO's "
            "agreement",
        )
        self.assertTrue(
            re.search(r"recorded[^.\n]{0,40}`LOCK\.json`", text),
            "an agreement nothing records is an agreement nobody can check",
        )

    def test_an_unrecorded_token_or_atom_is_not_locked(self):
        self.assertTrue(
            re.search(r"not in it is not locked", body()),
            "LOCK.json must be stated as the record, so absence from it is a "
            "definite answer rather than a maybe",
        )


class MultiBrandRuleTest(unittest.TestCase):
    def test_it_is_shared_components_plus_per_product_config(self):
        text = body()
        self.assertIn("## The multi-brand rule", text)
        self.assertIn("shared components", text)
        self.assertTrue(
            re.search(r"per-product token/brand and feature\s+config", text),
            "the rule is shared components PLUS a per-product token/brand and "
            "feature config",
        )
        self.assertTrue(
            re.search(r"never a forked component set|not a fork", text, re.I),
            "the standard must say what the rule forbids, not only what it "
            "permits",
        )


class MasterTemplateRuleTest(unittest.TestCase):
    def test_new_work_starts_from_the_master_template(self):
        text = body()
        self.assertIn("master template", text)
        self.assertTrue(
            re.search(r"[Nn]ew apps and new designs start from the master template", text),
            "the standard must say new apps and designs start from the master "
            "template",
        )


class CriticChecklistTest(unittest.TestCase):
    """The checklist that gates "done". Every check the card names, and every
    one of them answerable yes/no from the files alone — a check that needs a
    live tool or a person's eye is not a gate."""

    def test_the_checklist_section_exists(self):
        self.assertIn(CHECKLIST_HEADING, body())

    def test_it_is_answerable_from_the_files_alone(self):
        self.assertIn(
            "from the files alone", checklist(),
            "the checklist must say what kind of question each item is",
        )

    def test_structure_and_readme_followed(self):
        self.assertIn("Structure and README followed", checklist())

    def test_every_value_is_a_token(self):
        self.assertIn("Every value is a token", checklist())

    def test_no_atom_outside_the_lock(self):
        self.assertTrue(
            re.search(r"[Nn]o atom that is not in `LOCK\.json`", checklist()),
            "the checklist must refuse an atom the lock does not carry",
        )

    def test_molecules_and_organisms_compose_from_locked_atoms(self):
        self.assertIn("composed from locked atoms", checklist())

    def test_completeness_covers_states_and_all_three_widths(self):
        text = checklist()
        self.assertIn("states", text)
        for width in ("phone", "tablet", "desktop"):
            self.assertIn(width, text, f"the completeness check omits {width}")

    def test_it_renders_and_passes_contrast_and_accessibility(self):
        text = checklist()
        self.assertIn("contrast and accessibility", text)
        self.assertIn("WCAG AA", text)
        self.assertTrue(
            re.search(r"unresolved token|missing asset", text),
            "'it renders' has to be a file question: the check names what "
            "renders wrong in a file, not what looks wrong on a screen",
        )

    def test_divergence_from_the_master_template_is_rejected(self):
        text = checklist()
        self.assertIn("master template", text)
        self.assertTrue(
            re.search(r"rejected", text),
            "diverging work is rejected, not noted",
        )

    def test_a_failed_item_blocks(self):
        self.assertIn(
            "blocking finding", checklist(),
            "a checklist whose failure is not blocking does not gate anything",
        )


class TheDeliberateAbsenceTest(unittest.TestCase):
    """The one item that is NOT a check, and the record that keeps it out.

    The CEO dropped "the published Claude Design project matches the locked
    files" from the file-based checklist on 2026-09-16: it cannot be answered
    from the files, and the Designer verifies it by hand after each lock. The
    ADR still describes the published project, so without a written record of
    the removal the next reader assumes the item was forgotten and reinstates a
    gate no reviewer can answer — which is how a deliberate removal turns into
    an accident.
    """

    def test_it_is_not_one_of_the_checks(self):
        text = checklist()
        self.assertNotIn(
            "Claude Design", text,
            "the published-Claude-Design check must not be a checklist item — "
            "it is not answerable from the files",
        )
        self.assertNotIn(
            "published", text.lower(),
            "no checklist item may turn on what is published outside the repo",
        )

    def test_the_absence_is_recorded_with_its_reason(self):
        text = body()
        self.assertIn(ABSENCE_HEADING, text)
        absence = text[text.index(ABSENCE_HEADING):]
        self.assertIn("Claude Design", absence, "the record must name the item")
        self.assertTrue(
            re.search(r"not an omission|a decision, not an omission", absence, re.I),
            "the record must say the absence is deliberate",
        )
        self.assertTrue(
            re.search(r"cannot be answered from the files", absence),
            "the record must say WHY it is absent",
        )
        self.assertTrue(
            re.search(r"Designer verifies it by hand", absence),
            "the record must say who does check it, and how",
        )
        self.assertTrue(
            re.search(r"[Dd]o\s+not\s+reinstate", absence),
            "the record must tell the next reader not to put it back",
        )


if __name__ == "__main__":
    unittest.main()
