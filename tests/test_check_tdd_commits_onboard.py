"""RED-first tests for DRE-7209 — the TDD commit check passes a routing PR.

The onboarding process opens a routing pull request on the literal branch
`bot/onboard` in this repo and in agent-bureau. What it changes is
REGENERATED, never authored — but only in part of each file: the repo map and
the channel and workspace files under `config/` (ops already), and, in the
five slug mirrors agent-bureau's `scripts/sync_repo_mirrors.py` lists in
`SLUG_MIRRORS`, the lines between its `BEGIN generated repo mirror` and
`END generated repo mirror` markers. That region is what `make sync-mirrors
CHECK=1` proves, and there is no RED test to write for a slug added to it —
while the check's finding is the ORDER of commits, which no added commit
clears (DRE-2694).

So a routing path's change is not code on that one branch, opened by that one
bot, when it is confined to that region — and only then:
  * the opener is the GitHub-ATTESTED login (PR_AUTHOR, else the event
    payload), exactly the signal the standards-sync exemption is keyed on —
    a branch name and a git author line are forgeable, the opener is not;
  * the branch is exactly `bot/onboard`, never a prefix;
  * a routing path edited by one line outside its mirror region, or on a
    marker line, is code — the proof does not cover it (the QA review of
    PR #1087);
  * any other path on that branch is held to the normal rule, so a commit
    that touches a routing path AND anything else is a code commit.

This repo's `scripts/validate_card.py` is NOT in the set: its mirror region is
already proved and exempt by the generated-region rule (DRE-3896), so a
routing PR here passes without the onboarding relief.

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

# The routing set, as the card names it — the two repos' paths in one list,
# less this repo's `scripts/validate_card.py`, which the generated-region rule
# (DRE-3896) already covers.
HERE = ("config/repo-map.json",)
VALIDATE_CARD = "scripts/validate_card.py"
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
# relief actually moves.
CODE_ROUTING = tuple(p for p in AGENT_BUREAU if tdd.classify_path(p) == "code")

BEGIN = "--- BEGIN generated repo mirror (from config/repo-map.json) ---"
END = "--- END generated repo mirror ---"


def mirror(path, slugs, logic="route", begin=BEGIN, end=END):
    """A slug mirror the way agent-bureau's generator leaves one: authored
    code around a marked region holding the slug list, in the file's own
    comment syntax. `logic` names the authored function, so changing it is
    an edit outside the region."""
    if path.endswith(".ts"):
        return (f"// {begin}\nexport const SLUGS = {slugs!r};\n// {end}\n"
                f"export function {logic}(s: string) "
                f"{{ return SLUGS.includes(s); }}\n")
    if path.endswith(".py"):
        return (f"# {begin}\nSLUGS = {slugs!r}\n# {end}\n\n\n"
                f"def {logic}(s):\n    return s in SLUGS\n")
    return f'{{"slugs": {list(slugs)!r}}}\n'.replace("'", '"')


BEFORE = ["atlas"]
AFTER = ["atlas", "foundry"]


def commit(paths, subject="chore: route a repo", sources=None):
    return {"sha": "e" * 40, "subject": subject, "paths": list(paths),
            "sources": sources or {}}


def routing_commit(paths=CODE_ROUTING, **after_kwargs):
    """A commit adding one slug to each path's mirror region; `after_kwargs`
    reshape the after side (a logic edit, a moved marker)."""
    return commit(paths, sources={
        p: (mirror(p, BEFORE), mirror(p, AFTER, **after_kwargs))
        for p in paths
    })


class RoutingSetTest(unittest.TestCase):

    def test_the_routing_set_is_exactly_the_cards(self):
        self.assertEqual(tdd.ONBOARD_ROUTING_PATHS,
                         frozenset(HERE + AGENT_BUREAU))

    def test_validate_card_is_left_to_the_generated_region_rule(self):
        # Its region-only change is docs already (DRE-3896); listing it here
        # would add nothing legitimate and only widen the relief.
        self.assertNotIn(VALIDATE_CARD, tdd.ONBOARD_ROUTING_PATHS)
        self.assertTrue(tdd.has_generated_region_proof(VALIDATE_CARD))

    def test_the_branch_and_opener_are_the_literal_ones(self):
        self.assertEqual(tdd.ONBOARD_BRANCH, ONBOARD)
        self.assertEqual(tdd.ONBOARD_BOT, "agent-bureau-bot")

    def test_most_of_the_set_is_code_without_the_relief(self):
        # Guards the end-to-end tests below from going vacuous: if these
        # paths were docs or ops already, a pass would prove nothing.
        self.assertEqual(len(CODE_ROUTING), 5)


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

    def test_a_region_only_routing_commit_is_not_code_once_marked(self):
        c = tdd.mark_routing_paths([routing_commit()])[0]
        self.assertNotIn("code", tdd.commit_categories(c))

    def test_an_unmarked_routing_commit_is_still_code(self):
        self.assertIn("code", tdd.commit_categories(routing_commit()))

    def test_a_routing_path_without_its_sources_is_code(self):
        # Path-only is the strict answer: no versions, nothing proved.
        c = tdd.mark_routing_paths([commit(CODE_ROUTING)])[0]
        self.assertEqual(tdd._code_paths(c), list(CODE_ROUTING))

    def test_a_logic_edit_outside_the_mirror_region_is_code(self):
        # The QA review of PR #1087: the regeneration check proves the region,
        # never the authored code around it, so a logic edit there is code.
        for path in CODE_ROUTING:
            with self.subTest(path=path):
                c = tdd.mark_routing_paths(
                    [routing_commit((path,), logic="route_anywhere")])[0]
                self.assertEqual(tdd._code_paths(c), [path])

    def test_a_logic_edit_on_a_routing_path_fails_the_rule(self):
        for path in CODE_ROUTING:
            with self.subTest(path=path):
                ok, reason = tdd.check_commits(tdd.mark_routing_paths(
                    [routing_commit((path,), logic="route_anywhere")]))
                self.assertFalse(ok)
                self.assertEqual(reason, tdd.FAILURE_MESSAGE)

    def test_an_edit_to_a_marker_line_is_code(self):
        c = tdd.mark_routing_paths([routing_commit(
            end="--- END generated repo mirror (moved) ---")])[0]
        self.assertEqual(tdd._code_paths(c), list(CODE_ROUTING))

    def test_a_region_not_marked_as_the_repo_mirror_is_code(self):
        # Only the repo-mirror region is what `make sync-mirrors CHECK=1`
        # proves; another generator's markers are not that proof.
        paths = CODE_ROUTING
        c = tdd.mark_routing_paths([commit(paths, sources={
            p: (mirror(p, BEFORE, begin="BEGIN generated other",
                       end="END generated other"),
                mirror(p, AFTER, begin="BEGIN generated other",
                       end="END generated other"))
            for p in paths
        })])[0]
        self.assertEqual(tdd._code_paths(c), list(paths))

    def test_a_new_or_deleted_routing_file_is_code(self):
        for path in CODE_ROUTING:
            with self.subTest(path=path):
                for before, after in ((None, mirror(path, AFTER)),
                                      (mirror(path, BEFORE), None)):
                    c = tdd.mark_routing_paths([commit(
                        (path,), sources={path: (before, after)})])[0]
                    self.assertEqual(tdd._code_paths(c), [path])

    def test_python_that_will_not_parse_is_code(self):
        path = "console/backend/sources.py"
        c = tdd.mark_routing_paths([commit((path,), sources={
            path: (mirror(path, BEFORE), mirror(path, AFTER) + "def (:\n"),
        })])[0]
        self.assertEqual(tdd._code_paths(c), [path])

    def test_another_path_beside_the_routing_set_is_code(self):
        c = routing_commit()
        c["paths"].append("scripts/widget.py")
        c = tdd.mark_routing_paths([c])[0]
        self.assertIn("code", tdd.commit_categories(c))
        self.assertEqual(tdd._code_paths(c), ["scripts/widget.py"])

    def test_the_rule_passes_routing_only_and_fails_a_rider(self):
        ok, _ = tdd.check_commits(tdd.mark_routing_paths([routing_commit()]))
        self.assertTrue(ok)
        ok, reason = tdd.check_commits(tdd.mark_routing_paths(
            [routing_commit(), commit(["scripts/widget.py"])]))
        self.assertFalse(ok)
        self.assertEqual(reason, tdd.FAILURE_MESSAGE)


class OnboardCliTest(GitRepoMixin, unittest.TestCase):
    """End-to-end over a real temp repo, the way CI runs the script: the
    mirrors exist on main, and the branch adds one slug to each region."""

    def _seed(self, paths):
        self.git("checkout", "-q", "main")
        for rel in paths:
            self.write(rel, mirror(rel, BEFORE))
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "chore: seed the mirrors")
        self.git("checkout", "-q", "-B", self.branch)

    def _routing_commit(self, paths, extra=(), **after_kwargs):
        for rel in paths:
            self.write(rel, mirror(rel, AFTER, **after_kwargs))
        for rel in extra:
            self.write(rel, "def widget():\n    return 1\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "chore: route foundry",
                 "--author", f"{BOT} <{BOT}@users.noreply.github.com>")

    def setUp(self):
        super().setUp()
        self.branch = ONBOARD
        self._seed(AGENT_BUREAU)

    def test_agent_bureau_routing_pr_by_the_bot_exits_0(self):
        self._routing_commit(AGENT_BUREAU)
        p = self.run_check(head_ref=ONBOARD, author=BOT)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn(ONBOARD, p.stdout)

    def test_this_repos_routing_pr_passes_on_the_region_rule(self):
        # validate_card.py's mirror region is DRE-3896's; the repo map is ops.
        self.git("checkout", "-q", "main")
        self.write(VALIDATE_CARD, mirror(VALIDATE_CARD, BEFORE))
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "chore: seed validate_card")
        self.git("checkout", "-q", "-B", ONBOARD)
        self._routing_commit(HERE + (VALIDATE_CARD,))
        p = self.run_check(head_ref=ONBOARD, author=BOT)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_the_opener_can_arrive_from_the_event_payload(self):
        self._routing_commit(AGENT_BUREAU)
        p = self.run_check(github_head_ref=ONBOARD, event_author=BOT)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_a_logic_edit_on_a_routing_path_fails(self):
        for path in CODE_ROUTING:
            with self.subTest(path=path):
                self.git("checkout", "-q", "-B", ONBOARD, "main")
                self._routing_commit((path,), logic="route_anywhere")
                p = self.run_check(head_ref=ONBOARD, author=BOT)
                self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
                self.assertIn(tdd.FAILURE_MESSAGE, p.stdout)

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
        self.git("checkout", "-q", "-B", "bot/onboard-x", "main")
        self._routing_commit(AGENT_BUREAU)
        p = self.run_check(head_ref="bot/onboard-x", author=BOT)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)


if __name__ == "__main__":
    unittest.main()
