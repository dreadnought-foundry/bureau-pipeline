"""RED-first tests for DRE-7209 — the TDD commit check passes a routing PR.

The onboarding process opens a routing pull request on the literal branch
`bot/onboard` in this repo and in agent-bureau. Every path it changes is
REGENERATED, never authored: here `config/repo-map.json` and the fallback
region of `scripts/validate_card.py`; in agent-bureau the repo map, the
channel and workspace files, and the five slug mirrors its
`scripts/sync_repo_mirrors.py` lists in `SLUG_MIRRORS`. Their proof is the
regeneration check each repo's CI already runs (`scripts/sync_fallback_map.py
--check` here, `make sync-mirrors CHECK=1` there), and there is no RED test to
write for a slug added to a mirror — while the check's finding is the ORDER of
commits, which no added commit clears (DRE-2694).

So the routing set is not code on that one branch, opened by that one bot —
and only there:
  * the opener is the GitHub-ATTESTED login (PR_AUTHOR, else the event
    payload), exactly the signal the standards-sync exemption is keyed on —
    a branch name and a git author line are forgeable, the opener is not;
  * the branch is exactly `bot/onboard`, never a prefix;
  * any other path on that branch is held to the normal rule, so a commit
    that touches a routing path AND anything else is a code commit.

Run: cd bureau-pipeline && python3 -m pytest tests/test_check_tdd_commits_onboard.py -v
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import check_tdd_commits as tdd  # noqa: E402
from test_tdd_commit_check import BOT, GitRepoMixin  # noqa: E402

ONBOARD = "bot/onboard"

# The routing set, as the card names it — the two repos' paths in one list.
HERE = ("config/repo-map.json", "scripts/validate_card.py")
AGENT_BUREAU = (
    "config/repo-map.json",
    "config/pipeline-channel.json",
    "config/linear-workspace.json",
    "console/backend/sources.py",
    "cloud/relay-gh/normalize.py",
    "console/web/src/components/wizard/new-agent/repos.ts",
    "console/web/src/components/flyout/cardMeta.ts",
    "demo/demo_library.py",
)
# The routing paths that classify as code without this card — the ones the
# exemption actually moves.
CODE_ROUTING = tuple(p for p in HERE + AGENT_BUREAU
                     if tdd.classify_path(p) == "code")


def commit(paths, subject="chore: route a repo"):
    return {"sha": "e" * 40, "subject": subject, "paths": list(paths)}


class RoutingSetTest(unittest.TestCase):

    def test_the_routing_set_is_exactly_the_cards(self):
        self.assertEqual(tdd.ONBOARD_ROUTING_PATHS,
                         frozenset(HERE + AGENT_BUREAU))

    def test_the_branch_and_opener_are_the_literal_ones(self):
        self.assertEqual(tdd.ONBOARD_BRANCH, ONBOARD)
        self.assertEqual(tdd.ONBOARD_BOT, "agent-bureau-bot")

    def test_most_of_the_set_is_code_without_the_exemption(self):
        # Guards the end-to-end tests below from going vacuous: if these
        # paths were docs or ops already, a pass would prove nothing.
        self.assertGreaterEqual(len(CODE_ROUTING), 6)


class IsOnboardRoutingPrTest(unittest.TestCase):

    def test_the_bot_on_the_branch_is_a_routing_pr(self):
        for opener in ("agent-bureau-bot", BOT, "app/agent-bureau-bot"):
            with self.subTest(opener=opener):
                self.assertTrue(tdd.is_onboard_routing_pr(ONBOARD, opener))

    def test_any_other_opener_is_not(self):
        for opener in ("mallory", "agent-bureau-qa-bot[bot]",
                       "agent-bureau-bot-3", "not-agent-bureau-bot", "", None):
            with self.subTest(opener=opener):
                self.assertFalse(tdd.is_onboard_routing_pr(ONBOARD, opener))

    def test_any_other_branch_is_not(self):
        for ref in ("bot/onboard-x", "bot/onboardx", "bot/other",
                    "bot/onboard/evil", "agent/DRE-7209-x", "", None):
            with self.subTest(ref=ref):
                self.assertFalse(tdd.is_onboard_routing_pr(ref, BOT))


class RoutingClassificationTest(unittest.TestCase):

    def test_a_routing_only_commit_is_not_code_once_marked(self):
        c = tdd.mark_routing_paths([commit(HERE + AGENT_BUREAU)])[0]
        self.assertNotIn("code", tdd.commit_categories(c))

    def test_an_unmarked_routing_commit_is_still_code(self):
        self.assertIn("code", tdd.commit_categories(commit(CODE_ROUTING)))

    def test_another_path_beside_the_routing_set_is_code(self):
        c = tdd.mark_routing_paths(
            [commit(CODE_ROUTING + ("scripts/widget.py",))])[0]
        self.assertIn("code", tdd.commit_categories(c))
        self.assertEqual(tdd._code_paths(c), ["scripts/widget.py"])

    def test_the_rule_passes_routing_only_and_fails_a_rider(self):
        ok, _ = tdd.check_commits(
            tdd.mark_routing_paths([commit(CODE_ROUTING)]))
        self.assertTrue(ok)
        ok, reason = tdd.check_commits(tdd.mark_routing_paths(
            [commit(CODE_ROUTING), commit(["scripts/widget.py"])]))
        self.assertFalse(ok)
        self.assertEqual(reason, tdd.FAILURE_MESSAGE)


class OnboardCliTest(GitRepoMixin, unittest.TestCase):
    """End-to-end over a real temp repo, the way CI runs the script."""

    def _routing_commit(self, paths, extra=()):
        for rel in tuple(paths) + tuple(extra):
            self.write(rel, f"# regenerated: {rel}\nSLUGS = ['foundry']\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "chore: route foundry",
                 "--author", f"{BOT} <{BOT}@users.noreply.github.com>")

    def setUp(self):
        super().setUp()
        self.git("checkout", "-q", "-b", ONBOARD)

    def test_agent_bureau_routing_pr_by_the_bot_exits_0(self):
        self._routing_commit(AGENT_BUREAU)
        p = self.run_check(head_ref=ONBOARD, author=BOT)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn(ONBOARD, p.stdout)

    def test_this_repos_routing_pr_by_the_bot_exits_0(self):
        self._routing_commit(HERE)
        p = self.run_check(head_ref=ONBOARD, author=BOT)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_the_opener_can_arrive_from_the_event_payload(self):
        self._routing_commit(AGENT_BUREAU)
        p = self.run_check(github_head_ref=ONBOARD, event_author=BOT)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_a_commit_that_also_touches_another_code_path_fails(self):
        self._routing_commit(AGENT_BUREAU, extra=("scripts/widget.py",))
        p = self.run_check(head_ref=ONBOARD, author=BOT)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn(tdd.FAILURE_MESSAGE, p.stdout)

    def test_a_later_commit_on_another_code_path_fails(self):
        self._routing_commit(AGENT_BUREAU)
        self.add_commit("scripts/widget.py", "feat: smuggled in", author=BOT)
        p = self.run_check(head_ref=ONBOARD, author=BOT)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)

    def test_any_other_opener_fails(self):
        self._routing_commit(AGENT_BUREAU)
        for opener in ("mallory", "agent-bureau-qa-bot[bot]"):
            with self.subTest(opener=opener):
                p = self.run_check(head_ref=ONBOARD, author=opener)
                self.assertEqual(p.returncode, 1, p.stdout + p.stderr)

    def test_no_attested_opener_fails(self):
        self._routing_commit(AGENT_BUREAU)
        p = self.run_check(head_ref=ONBOARD)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)

    def test_a_near_miss_branch_fails(self):
        self.git("checkout", "-q", "-b", "bot/onboard-x")
        self._routing_commit(AGENT_BUREAU)
        p = self.run_check(head_ref="bot/onboard-x", author=BOT)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)


if __name__ == "__main__":
    unittest.main()
