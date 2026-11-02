"""The shared-parts rule, written into the design-system standard (DRE-8718).

The CEO approved epic DRE-8708 on 2026-11-01: every screen an agent builds
uses the product's shared parts, and a second copy of a part never gets in.
Its rules 1 to 11 are settled on the epic; this card writes them where the
agents and the critic read them, `standards/design-system.md`.

The sibling cards ship strings the critic writes and the gate, the sweep and
the fix loop read: the finding wordings, the `🪴 part-provisional:` line, the
`🎨 design-question:` block and the three `Parts check:` lines. The standard
must quote them exactly, so these tests pin the strings rather than the
paraphrase around them. A standard that words a line differently from the
script that parses it teaches the critic a line nothing reads.
"""

import os
import re
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
STANDARD = os.path.join(REPO, "standards", "design-system.md")
README = os.path.join(REPO, "README.md")

SECTION_HEADING = "## UI code in a repo whose design system exists"
LOCK_HEADING = "## The lock rule"
CHECKLIST_HEADING = '## The design critic\'s checklist — what gates "done"'


def body() -> str:
    with open(STANDARD, encoding="utf-8") as f:
        return f.read()


def _slice(text: str, heading: str) -> str:
    """The text of one `## ` section, from its heading to the next one."""
    start = text.index(heading)
    nxt = text.find("\n## ", start + len(heading))
    return text[start:] if nxt == -1 else text[start:nxt]


def section() -> str:
    return _slice(body(), SECTION_HEADING)


def flat(text: str) -> str:
    """Collapse wrapped lines, so a quoted string may wrap in the source."""
    return re.sub(r"\s+", " ", text)


class TheSectionExistsTest(unittest.TestCase):
    def test_the_section_is_in_the_standard(self):
        self.assertIn(SECTION_HEADING, body())

    def test_it_cites_the_epic_the_rules_were_settled_on(self):
        self.assertIn("DRE-8708", section())


class ScopeRuleTest(unittest.TestCase):
    """Rule 1: "exists" is read from files, exactly as the reader defines it,
    and only the pull request's diff is judged."""

    def test_exists_is_read_from_the_two_files(self):
        text = flat(section())
        self.assertIn("`design/LOCK.json`", text)
        self.assertIn("`.github/bureau/design-system.json`", text)
        self.assertRegex(
            text, r"at least one folder that is present",
            "the declaration counts only when it names a folder the checkout "
            "has — the reader's definition, word for word",
        )

    def test_the_lock_only_fallback_folders_are_named(self):
        text = section()
        for folder in ("design/atoms", "design/molecules",
                       "design/organisms", "design/templates"):
            self.assertIn(folder, text)

    def test_it_links_the_declaration_contract(self):
        self.assertIn("docs/design-system-declaration.md", section())

    def test_only_the_diff_is_judged(self):
        text = flat(section())
        self.assertRegex(text, r"[Oo]nly (?:a|the) pull request's diff is judged")
        self.assertRegex(
            text, r"cleanup cards?",
            "copies already in a repo go on cleanup cards, never on every pull "
            "request",
        )


class BlockingFindingsTest(unittest.TestCase):
    """Rules 2 and 3: blocking REQUEST_CHANGES findings worded as the parts
    review prints them."""

    COPY = ("uses-the-shared-parts: `<Name>` in `<file>` is a copy of the "
            "shared part `<Part>` — use `<ships at>` and delete the copy.")
    LOOK_ALIKE = ("uses-the-shared-parts: `<Name>` in `<file>` looks like the "
                  "shared part `<Part>` (shared props: <a, b, c>) — use "
                  "`<ships at>` and delete the copy.")

    def test_both_rules_are_named_and_blocking(self):
        text = flat(section())
        self.assertIn("Uses the shared parts", text)
        self.assertIn("No copies", text)
        self.assertIn("REQUEST_CHANGES", text)
        self.assertIn("blocking", text)

    def test_the_copy_finding_is_quoted_exactly(self):
        self.assertIn(self.COPY, flat(section()))

    def test_the_look_alike_finding_is_quoted_exactly(self):
        self.assertIn(self.LOOK_ALIKE, flat(section()))

    def test_a_copy_is_deleted_not_deprecated(self):
        self.assertIn("deleted, not deprecated", flat(section()))


class NewPartOrderTest(unittest.TestCase):
    """Rule 4's a/b/c order, the provisional record and the one question."""

    PROVISIONAL = ("🪴 part-provisional: `<Name>` at `<path>` — reasoning: "
                   "<one sentence>")
    NEW_PART = ("new-part: `<Name>` at `<path>` — no shared part is near it; "
                "approve it as provisional with its `🪴 part-provisional:` "
                "line unless rule 4(c) applies.")
    QUESTION_BLOCK = (
        "🎨 design-question: `<Name>` at `<path>`\n"
        "🔎 Finding: <one line>\n"
        "❓ Question: <one line>\n"
        "💡 Recommendation: <one line>\n"
    )

    def test_the_three_steps_are_stated_in_order(self):
        text = section()
        a, b, c = (text.find(f"**({x})") for x in "abc")
        self.assertTrue(
            -1 < a < b < c,
            "the a/b/c order for a new part must be stated, in that order",
        )

    def test_a_new_part_near_a_shared_part_goes_back_under_4a(self):
        text = flat(section())
        self.assertIn("4(a)", text)
        self.assertRegex(text, r"Jaccard similarity of 0\.6")
        self.assertRegex(text, r"at least 2 names")

    def test_the_new_part_line_is_quoted_exactly(self):
        self.assertIn(self.NEW_PART, flat(section()))

    def test_provisional_status_is_stated(self):
        self.assertIn("**provisional**", section())

    def test_the_provisional_line_is_quoted_exactly(self):
        self.assertIn(self.PROVISIONAL, section())

    def test_the_provisional_line_is_the_one_record(self):
        self.assertRegex(
            flat(section()), r"the one record of (?:a|the) critic's approval")

    def test_the_question_block_is_quoted_exactly(self):
        self.assertIn(self.QUESTION_BLOCK, section())

    def test_it_is_the_one_green_light_question(self):
        text = flat(section())
        self.assertIn("Green Light", text)
        self.assertRegex(text, r"Finding, Question, Recommendation")


class TheCeosAnswerTest(unittest.TestCase):
    """His signed console answer settles 4(c) for that part on that pull
    request, and the question is asked once per part."""

    def test_his_signed_answer_settles_the_question(self):
        text = flat(section())
        self.assertRegex(text, r"signed")
        self.assertRegex(text, r"settles rule 4\(c\) for that part on that pull request")

    def test_the_critic_cites_his_answer_or_names_his_part(self):
        text = flat(section())
        self.assertRegex(text, r"`🪴 part-provisional:` line, citing his answer")
        self.assertRegex(text, r"names the part he chose")

    def test_the_question_is_asked_once_per_part(self):
        text = flat(section())
        self.assertIn("asked once per part on a pull request", text)
        self.assertRegex(text, r"parked without a second ask")

    def test_the_record_strings_are_named(self):
        text = flat(section())
        self.assertIn("🎨 design-question-answered:", text)
        self.assertIn("ceo-via-console", text)


class PartsCheckLineTest(unittest.TestCase):
    LINES = (
        "Parts check: applied — <n> finding(s)",
        "Parts check: applied by hand — the review read UNKNOWN (<reason>), "
        "<n> finding(s)",
        "Parts check: not applied — <reason>",
    )

    def test_the_three_lines_are_quoted_exactly(self):
        text = flat(section())
        for line in self.LINES:
            self.assertIn(line, text)

    def test_no_line_where_the_review_does_not_apply(self):
        text = flat(section())
        self.assertIn("does not apply", text)
        self.assertRegex(text, r"carries no (?:such|`Parts check:`) line")


class CatalogRolesTest(unittest.TestCase):
    """Rule 7: the three roles whose context carries the parts catalog."""

    def test_the_three_roles_are_named(self):
        text = flat(section())
        self.assertRegex(text, r"`engineer`, `frontend` and the fix agent")

    def test_it_says_why_the_fix_agent_is_one(self):
        self.assertRegex(
            flat(section()), r"fix for one finding can add a copy")

    def test_the_catalog_command_is_named(self):
        self.assertIn("design_parts.py catalog", section())


class RulesSevenToElevenTest(unittest.TestCase):
    def test_each_rule_is_stated(self):
        text = flat(section())
        for marker in (
            r"[Bb]uilders get the parts list up front",
            r"[Cc]ount before enforcing",
            r"[Rr]enamed copies are caught two ways",
            r"weekly list is visual",
            r"[Pp]rovisional becomes permanent after two weeks",
        ):
            self.assertRegex(text, marker)

    def test_the_rollout_order(self):
        self.assertRegex(
            flat(section()),
            r"agent-bureau's console, then Portico, then the other repos with "
            r"a design system",
        )


class LockRuleAmendmentTest(unittest.TestCase):
    """Rule 6: the amendment is deliberate and dated, and the 2026-09-24
    design-approval rule stays word for word."""

    FIVE_POINTS = (
        "1. **Approving a design IS the agreement.**",
        "2. **The builder records it, in the same PR.**",
        "3. **The critic accepts a recorded design approval.**",
        "4. **A card cannot forbid what an approved design needs.**",
        "5. **What stays gated:** a token or atom change **no approved design shows**.",
    )

    def lock(self) -> str:
        return _slice(body(), LOCK_HEADING)

    def test_the_amendment_is_dated_and_deliberate(self):
        text = flat(self.lock())
        self.assertIn("2026-11-01", text)
        self.assertRegex(text, r"deliberate change")

    def test_a_recorded_critic_approval_admits_a_provisional_part(self):
        text = flat(self.lock())
        self.assertIn("🪴 part-provisional:", text)
        self.assertRegex(text, r"admits a provisional part")
        self.assertRegex(text, r"overrule")

    def test_the_2026_09_24_rule_and_its_five_points_stay(self):
        text = self.lock()
        self.assertIn("2026-09-24", text)
        self.assertIn("DRE-4833", text)
        for point in self.FIVE_POINTS:
            self.assertIn(point, text)


class ChecklistScopeTest(unittest.TestCase):
    def test_the_checklist_says_ui_code_is_judged_by_the_new_section(self):
        text = flat(_slice(body(), CHECKLIST_HEADING))
        self.assertIn("`design/`", text)
        self.assertIn("UI code in a repo whose design system exists", text)


class ReadmeOnboardingTest(unittest.TestCase):
    def test_the_onboarding_step_names_the_declaration(self):
        with open(README, encoding="utf-8") as f:
            text = f.read()
        onboarding = _slice(text, "## Onboarding a new product repo")
        self.assertIn(".github/bureau/design-system.json", onboarding)
        self.assertIn("docs/design-system-declaration.md", onboarding)


class NoPersonNamedTest(unittest.TestCase):
    """bureau-pipeline is public: the CEO is named by role only."""

    def test_no_email_address_in_the_section(self):
        self.assertNotRegex(section(), r"[\w.+-]+@[\w-]+\.[\w.]+")


if __name__ == "__main__":
    unittest.main()
