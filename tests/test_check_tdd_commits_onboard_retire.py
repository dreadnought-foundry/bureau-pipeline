"""RED-first tests for DRE-7866 — the TDD commit check relieves a retirement's
removal of one per-repo console fact, the mirror of DRE-7431.

Since DRE-7537 the retire step `50_retire_route.py` opens its own routing pull
request in agent-bureau on `bot/onboard`, and it REMOVES the three facts
onboarding wrote, one commit each:

  * `console/backend/sources.py` — one `PROJECT_IDENTITIES` entry, in the
    same write as the file's regenerated repo-mirror region;
  * `console/backend/github_oidc.py` — one `REPO_IDS` entry;
  * `console/backend/runner-routing.json` — one repo under `repos`.

DRE-7431 relieved only an addition, so agent-bureau #4123 (retiring
onboard-sandbox-3) failed `TDD commit discipline` on its first commit: the
`sources.py` removal is outside the mirror region, and deletion detection
never covers the first code commit. No added commit clears that (DRE-2694).
The relief here is DRE-7431's, reversed, with the same exactness:
  * exactly one entry removed from exactly that literal, in the shape the
    engine writes, and no other line of the file changed — a second entry,
    a changed existing entry or an edited function is code;
  * only on an onboarding routing PR — opened by agent-bureau-bot
    (GitHub-attested) on the literal branch `bot/onboard`.

Run: cd bureau-pipeline && python3 -m pytest tests/test_check_tdd_commits_onboard_retire.py -v
"""

from __future__ import annotations

import os
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import check_tdd_commits as tdd  # noqa: E402
from test_check_tdd_commits_onboard import (  # noqa: E402
    AFTER, END, ONBOARD,
)
from test_check_tdd_commits_onboard_facts import (  # noqa: E402
    FACTS, IDENTITY_ENTRY, OIDC, OIDC_ENTRY, RUNNER_ROUTING, SOURCES,
    VARIABLES, code_paths, fact_commit, oidc, runner_routing, sources,
    variable,
)
from test_tdd_commit_check import BOT, GitRepoMixin  # noqa: E402

HARBOR_IDS = '    "harbor": ("1", "2"),\n'
HARBOR_IDENTITY = '    "harbor": {"name": "Harbor", "client": "Harbor"},\n'

# Each fact file as it reads once `foundry` is onboarded — the state a
# retirement starts from — and as it reads once `foundry` is retired.
ONBOARDED = {
    SOURCES: sources(AFTER, entries=IDENTITY_ENTRY),
    OIDC: oidc(entries=OIDC_ENTRY),
    RUNNER_ROUTING: runner_routing(("atlas", "foundry")),
}
RETIRED = {
    SOURCES: sources(),
    OIDC: oidc(),
    RUNNER_ROUTING: runner_routing(),
}


def retirement_commits():
    """The three commits #4123 carried, in its order, one fact each —
    sources.py with its regenerated region and its identity removed in ONE
    write, as the branch's first code commit."""
    return [fact_commit(path, ONBOARDED[path], RETIRED[path])
            for path in FACTS]


class RemovalReliefTest(unittest.TestCase):
    """One removed entry, in the shape the engine writes, is relieved once
    the PR is marked as the onboarding routing PR — and only then."""

    def test_each_of_the_three_commits_is_relieved_once_marked(self):
        for c in retirement_commits():
            with self.subTest(path=c["paths"][0]):
                self.assertEqual(code_paths(c), [])
                self.assertNotIn("code", tdd.commit_categories(
                    tdd.mark_routing_paths([c])[0]))

    def test_each_of_the_three_commits_is_code_unmarked(self):
        for c in retirement_commits():
            with self.subTest(path=c["paths"][0]):
                self.assertEqual(tdd._code_paths(c), c["paths"])

    def test_the_sources_removal_is_not_a_mirror_change_alone(self):
        # Guards the pass above from going vacuous: the region rule does not
        # cover the identity line, so only the removal relief can.
        self.assertFalse(tdd.is_routing_mirror_change(
            SOURCES, ONBOARDED[SOURCES], RETIRED[SOURCES]))
        self.assertFalse(tdd.is_routing_entry_change(
            SOURCES, ONBOARDED[SOURCES], RETIRED[SOURCES]))

    def test_the_three_commits_pass_the_rule_once_marked(self):
        ok, reason = tdd.check_commits(
            tdd.mark_routing_paths(retirement_commits()))
        self.assertTrue(ok, reason)

    def test_the_three_commits_fail_the_rule_unmarked(self):
        ok, reason = tdd.check_commits(retirement_commits())
        self.assertFalse(ok)
        self.assertEqual(reason, tdd.FAILURE_MESSAGE)

    def test_each_removal_alone_as_the_first_code_commit_passes(self):
        # Deletion detection never covers the first code commit, so each
        # removal leading the branch is relieved by this rule or not at all.
        for c in retirement_commits():
            with self.subTest(path=c["paths"][0]):
                ok, reason = tdd.check_commits(tdd.mark_routing_paths([c]))
                self.assertTrue(ok, reason)

    def test_an_identity_removal_without_a_region_change_is_relieved(self):
        c = fact_commit(SOURCES, sources(entries=IDENTITY_ENTRY), sources())
        self.assertEqual(code_paths(c), [])

    def test_the_entry_need_not_be_the_last(self):
        after = oidc()
        before = after.replace('    "atlas"', OIDC_ENTRY + '    "atlas"')
        self.assertEqual(code_paths(fact_commit(OIDC, before, after)), [])

    def test_the_addition_relief_is_unchanged(self):
        # DRE-7431's direction still holds beside the new one.
        for path in FACTS:
            with self.subTest(path=path):
                self.assertEqual(code_paths(fact_commit(
                    path, RETIRED[path], ONBOARDED[path])), [])


class RemovalExactnessTest(unittest.TestCase):
    """Any other line in a fact file stays code: the relief is one removed
    entry, never the file."""

    def assert_code(self, path, before, after):
        c = fact_commit(path, before, after)
        self.assertEqual(code_paths(c), [path])
        ok, reason = tdd.check_commits(tdd.mark_routing_paths([c]))
        self.assertFalse(ok)
        self.assertEqual(reason, tdd.FAILURE_MESSAGE)

    # github_oidc.py

    def test_two_repo_ids_entries_removed_is_code(self):
        self.assert_code(OIDC, oidc(entries=OIDC_ENTRY + HARBOR_IDS), oidc())

    def test_a_changed_existing_repo_ids_entry_is_code(self):
        self.assert_code(OIDC, ONBOARDED[OIDC],
                         oidc(ids=('"111"', '"999"')))

    def test_an_edited_function_beside_the_removal_is_code(self):
        self.assert_code(OIDC, ONBOARDED[OIDC],
                         oidc(logic="repo_ids_anywhere"))

    def test_a_comment_line_beside_the_removal_is_code(self):
        after = oidc().replace("\n\ndef ", "\n# routes every repo\n\ndef ")
        self.assert_code(OIDC, ONBOARDED[OIDC], after)

    def test_a_removed_line_outside_the_literal_is_code(self):
        after = oidc().replace('"""GitHub OIDC claims for the console."""\n',
                               "")
        self.assert_code(OIDC, ONBOARDED[OIDC], after)

    def test_a_removed_repo_ids_entry_of_another_shape_is_code(self):
        for entry in (
            '    "foundry": lookup("foundry"),\n',
            '    "foundry": (226758887, 1412115043),\n',
            '    **OTHER_IDS,\n',
        ):
            with self.subTest(entry=entry):
                self.assert_code(OIDC, oidc(entries=entry), oidc())

    def test_the_whole_literal_removed_is_code(self):
        before = oidc() + "\nOTHER_IDS = {\n" + OIDC_ENTRY + "}\n"
        self.assert_code(OIDC, before, oidc())

    def test_an_entry_removed_from_another_literal_is_code(self):
        before = oidc() + "\nOTHER_IDS = {\n" + OIDC_ENTRY + "}\n"
        after = oidc() + "\nOTHER_IDS = {\n}\n"
        self.assert_code(OIDC, before, after)

    # sources.py

    def test_two_project_identities_removed_is_code(self):
        self.assert_code(SOURCES, sources(
            AFTER, entries=IDENTITY_ENTRY + HARBOR_IDENTITY), sources())

    def test_a_changed_existing_project_identity_is_code(self):
        self.assert_code(SOURCES, ONBOARDED[SOURCES],
                         sources(client="Someone Else"))

    def test_an_edited_function_in_sources_is_code(self):
        self.assert_code(SOURCES, ONBOARDED[SOURCES],
                         sources(logic="project_identity_any"))

    def test_an_edited_mirror_marker_with_the_removal_is_code(self):
        self.assert_code(SOURCES, ONBOARDED[SOURCES],
                         sources().replace(END, END + " (moved)"))

    def test_a_removed_project_identity_of_another_shape_is_code(self):
        entry = '    "foundry": {"name": "Foundry", "client": tenant()},\n'
        self.assert_code(SOURCES, sources(AFTER, entries=entry), sources())

    # runner-routing.json

    def test_two_runner_routing_repos_removed_is_code(self):
        self.assert_code(RUNNER_ROUTING,
                         runner_routing(("atlas", "foundry", "harbor")),
                         runner_routing())

    def test_a_changed_existing_runner_routing_repo_is_code(self):
        changed = {v: variable(value="self-hosted") for v in VARIABLES}
        self.assert_code(RUNNER_ROUTING, ONBOARDED[RUNNER_ROUTING],
                         runner_routing(atlas=changed))

    def test_another_top_level_change_with_the_removal_is_code(self):
        self.assert_code(RUNNER_ROUTING, ONBOARDED[RUNNER_ROUTING],
                         runner_routing(readme="Routing, rewritten."))

    def test_a_renamed_repo_is_code(self):
        self.assert_code(RUNNER_ROUTING, ONBOARDED[RUNNER_ROUTING],
                         runner_routing(("atlas", "harbor")))

    def test_a_removed_repo_that_is_not_an_object_is_code(self):
        self.assert_code(RUNNER_ROUTING,
                         runner_routing(foundry="self-hosted"),
                         runner_routing())

    def test_a_deleted_fact_file_is_code(self):
        for path in FACTS:
            with self.subTest(path=path):
                self.assert_code(path, ONBOARDED[path], None)

    def test_a_rider_beside_the_removal_is_code(self):
        c = retirement_commits()[0]
        c["paths"].append("scripts/widget.py")
        self.assertEqual(code_paths(c), ["scripts/widget.py"])


class RetireFactsCliTest(GitRepoMixin, unittest.TestCase):
    """End-to-end over a real temp repo, replaying #4123's shape: the three
    files hold an onboarded repo on main, and the branch removes one fact per
    commit, `sources.py` first."""

    def setUp(self):
        super().setUp()
        for path in FACTS:
            self.write(path, ONBOARDED[path])
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "chore: seed the onboarded facts")

    def _commit(self, rel, content):
        self.write(rel, content)
        self.git("add", "-A")
        self.git("commit", "-q", "-m",
                 f"chore(routing): retire foundry — {rel}",
                 "--author", f"{BOT} <{BOT}@users.noreply.github.com>")

    def _replay(self, branch=ONBOARD, paths=FACTS, **swap):
        """The removal commits, in #4123's order. `swap` replaces one file's
        after side, keyed by its basename without the extension."""
        self.git("checkout", "-q", "-B", branch, "main")
        for path in paths:
            key = Path(path).stem.replace("-", "_")
            self._commit(path, swap.get(key, RETIRED[path]))

    def test_the_three_commits_on_bot_onboard_by_the_bot_exit_0(self):
        self._replay()
        p = self.run_check(head_ref=ONBOARD, author=BOT)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        for path in FACTS:
            with self.subTest(path=path):
                self.assertRegex(
                    p.stdout, rf"(?m)\[docs\] .*— {re.escape(path)}$")
        self.assertNotIn("[code]", p.stdout)

    def test_the_opener_can_arrive_from_the_event_payload(self):
        self._replay()
        p = self.run_check(github_head_ref=ONBOARD, event_author=BOT)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_each_removal_leading_the_branch_exits_0(self):
        for path in FACTS:
            with self.subTest(path=path):
                self._replay(paths=(path,))
                p = self.run_check(head_ref=ONBOARD, author=BOT)
                self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_any_other_line_in_a_fact_file_fails(self):
        # Each variant leads the branch, where deletion detection never
        # applies, so only this relief could pass it.
        for path, after in (
            (OIDC, oidc(ids=('"111"', '"999"'))),
            (RUNNER_ROUTING, runner_routing(readme="Routing, rewritten.")),
            (SOURCES, sources(logic="project_identity_any")),
        ):
            with self.subTest(path=path):
                key = Path(path).stem.replace("-", "_")
                self._replay(paths=(path,), **{key: after})
                p = self.run_check(head_ref=ONBOARD, author=BOT)
                self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
                self.assertIn(tdd.FAILURE_MESSAGE, p.stdout)

    def test_two_entries_removed_fails(self):
        self.write(OIDC, oidc(entries=OIDC_ENTRY + HARBOR_IDS))
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "chore: onboard harbor too")
        self._replay(paths=(OIDC,))
        p = self.run_check(head_ref=ONBOARD, author=BOT)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn(tdd.FAILURE_MESSAGE, p.stdout)

    def test_any_other_branch_fails(self):
        for branch in ("bot/onboard-x", "agent/DRE-7866-x"):
            with self.subTest(branch=branch):
                self._replay(branch=branch)
                p = self.run_check(head_ref=branch, author=BOT)
                self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
                self.assertIn("[code]", p.stdout)

    def test_any_other_opener_fails(self):
        self._replay()
        for opener in ("mallory", "agent-bureau-qa-bot[bot]"):
            with self.subTest(opener=opener):
                p = self.run_check(head_ref=ONBOARD, author=opener)
                self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
                self.assertIn("[code]", p.stdout)

    def test_no_attested_opener_fails(self):
        self._replay()
        p = self.run_check(head_ref=ONBOARD)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)


if __name__ == "__main__":
    unittest.main()
