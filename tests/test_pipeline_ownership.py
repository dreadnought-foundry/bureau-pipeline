"""One rule for "does the pipeline own this branch", and a sweep for what it
does not own (DRE-2426).

THE INCIDENT, 2026-08-12. Four PRs sat for up to ten hours with real
REQUEST_CHANGES verdicts and nothing coming: agent-bureau #2035, #2036, #2041
and portico #270. None was broken. All four were on hand-named branches
(`fix/…`, `feat/…`), and EVERY layer independently skips those:

    merge-gate.yml    case "$BRANCH" in agent/*|repair/*|dependabot/*)
    agent-fix.yml     case "$BRANCH" in agent/*|repair/*)
    linear-sync.yml   grep -oiE '^agent/DRE-[0-9]+-'
    reconcile.py      startswith("agent/")   x6

Eight hand-copied tests of the same question, giving FOUR different answers.
The reconcile sweep — the backstop whose whole job is finding stranded work —
had the same blind spot as the things it backstops, so nothing ever reported
them. The operator found them by eye, twice, hours apart.

The brittleness is the duplication, not any one copy. A new sweep written
tomorrow will copy `startswith("agent/")` from its neighbour and reopen the
hole silently. So this module pins three things:

  1. the rule is DEFINED ONCE and every reconcile sweep asks that one function;
  2. the shell gates are kept honest against it, since YAML cannot import;
  3. a PR nothing owns is REPORTED rather than skipped in silence.

Read the adversarial section as the specification: the sweep must not become
a second source of noise, because a warning that fires on everything is the
same failure one level up.
"""

from __future__ import annotations

import ast
import os
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/test")
os.environ.setdefault("GH_TOKEN", "test")

import reconcile  # noqa: E402

WORKFLOWS = ROOT / ".github" / "workflows"
RECONCILE_SRC = ROOT / "scripts" / "reconcile.py"


def _shell_gate_prefixes(workflow: str) -> set[str]:
    """The branch prefixes a workflow's `case "$BRANCH" in …)` accepts."""
    text = (WORKFLOWS / workflow).read_text()
    m = re.search(r'case "\$BRANCH" in ([^)]+)\)', text)
    assert m is not None, f"{workflow}: no branch case statement"
    return {p.strip().rstrip("*") for p in m.group(1).split("|")}


# ---------------------------------------------------------------- the one rule

class OwnershipRuleTest(unittest.TestCase):
    """`pipeline_owns` is the single answer to "will automation touch this?"."""

    def test_agent_branches_are_owned(self):
        for ref in ("agent/DRE-2426-x", "agent/DRE-1-a", "AGENT/DRE-9-b"):
            with self.subTest(ref=ref):
                self.assertTrue(reconcile.pipeline_owns(ref))

    def test_repair_and_dependabot_are_owned(self):
        # merge-gate merges both; agent-fix takes repair/. They are automation's
        # own branches even though they carry no card.
        self.assertTrue(reconcile.pipeline_owns("repair/" + "a" * 40))
        self.assertTrue(reconcile.pipeline_owns("dependabot/npm/left-pad-1.0.0"))

    def test_the_nightly_standards_sync_branch_is_owned(self):
        # DRE-2777. The merge gate merges `bot/standards-sync`, so the broad
        # question has to agree — otherwise the sweep posts "no owning
        # automation, nothing is coming" on a branch the gate is actively
        # merging, which is the false status this module exists to prevent.
        self.assertTrue(reconcile.pipeline_owns("bot/standards-sync"))
        # Narrow stays narrow, exactly as for dependabot/*: the nightly job
        # carries no card, so neither the card sweeps nor the fix agent claim
        # it. Widening either tuple here is the bug this split prevents.
        self.assertFalse(reconcile.card_branch("bot/standards-sync"))
        self.assertFalse(reconcile.fix_eligible("bot/standards-sync"))
        # The LITERAL branch, not a `bot/` prefix — auto-merge is not a
        # permission a future bot branch should inherit by being named one.
        self.assertFalse(reconcile.pipeline_owns("bot/something-else"))

    def test_hand_named_branches_are_NOT_owned(self):
        # The four live strandings, by name.
        for ref in (
            "fix/subscriptions-secret-drift-warning",   # agent-bureau #2035
            "feat/claude-cred-doctor",                  # agent-bureau #2036
            "fix/DRE-2405-dev-loads-ws-ingest",         # agent-bureau #2041
            "feat/DRE-2418-shared-answers-demo",        # portico #270
        ):
            with self.subTest(ref=ref):
                self.assertFalse(reconcile.pipeline_owns(ref))

    def test_a_card_id_in_the_wrong_position_does_not_confer_ownership(self):
        # `fix/DRE-2405-…` NAMES a card and is still unowned. That combination
        # is the trap: it looks managed and is not.
        self.assertFalse(reconcile.pipeline_owns("fix/DRE-2405-dev-loads-ws"))
        self.assertFalse(reconcile.pipeline_owns("my-agent/DRE-1-x"))
        self.assertFalse(reconcile.pipeline_owns("agentx/DRE-1-x"))

    def test_empty_and_missing_refs_are_not_owned(self):
        for ref in ("", None):
            with self.subTest(ref=ref):
                self.assertFalse(reconcile.pipeline_owns(ref))


# ------------------------------------------------------- the duplication guard

class NoHandCopiedPrefixTest(unittest.TestCase):
    """The guard on the guard. Eight copies caused this; one is allowed."""

    def test_no_sweep_hand_rolls_the_branch_test(self):
        src = RECONCILE_SRC.read_text()
        tree = ast.parse(src)
        offenders = []
        for node in ast.walk(tree):
            # `<expr>.startswith("agent/")` anywhere outside the one definition
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "startswith"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                    and node.args[0].value.startswith("agent/")):
                offenders.append(node.lineno)
        allowed = reconcile.PIPELINE_OWNS_DEFINITION_LINES
        stray = [n for n in offenders if n not in allowed]
        self.assertEqual(
            stray, [],
            f"reconcile.py lines {stray} hand-roll the branch-ownership test. "
            "Call pipeline_owns() — eight hand-copied variants of this check "
            "is what stranded four PRs for ten hours on 2026-08-12.",
        )

    def test_the_shell_gates_agree_with_the_python_rule(self):
        # YAML cannot import, so the comparison happens here — against the
        # CONSTANTS, not against literals copied beside them. Editing either
        # side alone now fails this test, which is the whole point: a ninth
        # hand-maintained copy of the answer is the bug, even in a test.
        cases = {
            "merge-gate.yml": set(reconcile.PIPELINE_BRANCH_PREFIXES),
            "agent-fix.yml": set(reconcile.FIX_BRANCH_PREFIXES),
        }
        for name, expected in cases.items():
            with self.subTest(workflow=name):
                found = _shell_gate_prefixes(name)
                self.assertEqual(
                    found, expected,
                    f"{name}'s branch gate drifted from the Python rule. "
                    "If this is deliberate, change the constant in "
                    "reconcile.py and the gate together — never one alone.",
                )

    def test_every_prefix_the_shell_accepts_is_owned_in_python(self):
        # The direction that actually strands work: a branch the gate merges
        # but the sweeps ignore. Read from the workflow, so a prefix added to
        # the shell alone is caught rather than assumed.
        for prefix in sorted(_shell_gate_prefixes("merge-gate.yml")):
            with self.subTest(prefix=prefix):
                self.assertTrue(
                    reconcile.pipeline_owns(prefix + "whatever-1"),
                    f"{prefix} is merged by the gate but unowned by the sweeps",
                )

    def test_every_prefix_the_fix_gate_accepts_is_fix_eligible_in_python(self):
        # `fix_eligible` is the Python mirror of agent-fix.yml's own case
        # statement — the named answer a future sweep should ask instead of
        # copying `agent/`|`repair/` out of the workflow by eye.
        for prefix in sorted(_shell_gate_prefixes("agent-fix.yml")):
            with self.subTest(prefix=prefix):
                self.assertTrue(
                    reconcile.fix_eligible(prefix + "whatever-1"),
                    f"{prefix} is dispatched to the fix agent by the workflow "
                    "but fix_eligible() says otherwise",
                )
        # Narrow stays narrow: dependabot has no card to work, and Dependabot
        # recreates its own conflicted PRs — handing it to the fix agent is
        # the widening this split of predicates exists to prevent.
        self.assertFalse(reconcile.fix_eligible("dependabot/npm/left-pad-1.0.0"))
        self.assertTrue(reconcile.pipeline_owns("dependabot/npm/left-pad-1.0.0"))


# ------------------------------------------------------------ the new sweep

def _pr(number=1, branch="fix/x", updated="2026-08-12T00:00:00Z",
        draft=False, comments=()):
    return {
        "number": number,
        "headRefName": branch,
        "isDraft": draft,
        "updatedAt": updated,
        "comments": list(comments),
    }


class UnownedSweepTest(unittest.TestCase):
    """`flag_unowned_prs` reports open PRs no layer will ever touch."""

    def setUp(self):
        self.posted = []
        self.p_list = mock.patch.object(reconcile, "open_prs")
        self.p_comment = mock.patch.object(
            reconcile, "comment_on_pr",
            side_effect=lambda n, body: self.posted.append((n, body)))
        self.list_prs = self.p_list.start()
        self.p_comment.start()
        self.addCleanup(self.p_list.stop)
        self.addCleanup(self.p_comment.stop)

    def test_a_stale_unowned_pr_is_reported(self):
        self.list_prs.return_value = [
            _pr(2035, "fix/subscriptions-secret-drift-warning",
                updated="2026-08-12T00:00:00Z")]
        reconcile.flag_unowned_prs(now="2026-08-12T14:00:00Z")
        self.assertEqual(len(self.posted), 1, f"not reported: {self.posted}")
        body = self.posted[0][1]
        self.assertIn("agent/", body, "the fix must name the correct shape")

    def test_an_owned_pr_is_never_reported(self):
        # Found by mutation: asserting only `agent/` let a version through
        # that used the NARROW predicate here and cheerfully reported every
        # repair/ and dependabot/ PR as unowned. The merge gate merges all
        # three, so all three must stay silent — the sweep asks the BROAD
        # question on purpose.
        for ref in ("agent/DRE-2426-x",
                    "repair/" + "a" * 40,
                    "dependabot/npm_and_yarn/left-pad-1.0.0"):
            with self.subTest(ref=ref):
                self.posted.clear()
                self.list_prs.return_value = [
                    _pr(1, ref, updated="2026-08-01T00:00:00Z")]
                reconcile.flag_unowned_prs(now="2026-08-12T14:00:00Z")
                self.assertEqual(
                    self.posted, [],
                    f"{ref} is merged by the gate — reporting it as unowned "
                    "would put a false warning on every dependency PR",
                )

    def test_a_fresh_unowned_pr_is_left_alone(self):
        # Someone opening a PR right now is mid-work, not stranded.
        self.list_prs.return_value = [
            _pr(9, "fix/just-opened", updated="2026-08-12T13:55:00Z")]
        reconcile.flag_unowned_prs(now="2026-08-12T14:00:00Z")
        self.assertEqual(self.posted, [])

    def test_a_draft_is_left_alone(self):
        self.list_prs.return_value = [
            _pr(9, "fix/wip", updated="2026-08-01T00:00:00Z", draft=True)]
        reconcile.flag_unowned_prs(now="2026-08-12T14:00:00Z")
        self.assertEqual(self.posted, [])

    def test_it_speaks_once_per_pr(self):
        # THE noise test. A sweep runs every 15 minutes; a warning repeated
        # every 15 minutes is the permanently-red check all over again.
        already = _pr(2035, "fix/x", updated="2026-08-12T00:00:00Z",
                      comments=[{"body": reconcile.UNOWNED_MARKER + " blah"}])
        self.list_prs.return_value = [already]
        reconcile.flag_unowned_prs(now="2026-08-12T14:00:00Z")
        self.assertEqual(self.posted, [], "the sweep repeated itself")

    def test_the_report_names_what_is_lost(self):
        self.list_prs.return_value = [
            _pr(2035, "fix/x", updated="2026-08-12T00:00:00Z")]
        reconcile.flag_unowned_prs(now="2026-08-12T14:00:00Z")
        body = self.posted[0][1].lower()
        for phrase in ("merge", "fix", "done"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, body,
                              "the notice must name all three lost gates, not "
                              "just the one the operator happened to notice")


class UnownedSweepAdversarialTest(UnownedSweepTest):
    """Ways this sweep could become the next thing nobody reads."""

    def test_a_later_sweep_sees_its_own_notice_and_stays_quiet(self):
        # The sweep runs every ~15 minutes forever. Idempotence comes from
        # re-reading the PR's comments, so the second listing must carry the
        # notice the first one posted — model that rather than replaying a
        # stale snapshot, or the test proves nothing about the real loop.
        pr = _pr(2035, "fix/x", updated="2026-08-12T00:00:00Z")
        self.list_prs.return_value = [pr]

        def record(number, body):
            self.posted.append((number, body))
            pr["comments"].append({"body": body})   # what the next listing sees

        with mock.patch.object(reconcile, "comment_on_pr", side_effect=record):
            reconcile.flag_unowned_prs(now="2026-08-12T14:00:00Z")
            reconcile.flag_unowned_prs(now="2026-08-12T14:30:00Z")
            reconcile.flag_unowned_prs(now="2026-08-12T18:00:00Z")
        self.assertEqual(len(self.posted), 1,
                         "a later sweep re-reported an already-flagged PR")

    def test_the_rename_it_suggests_is_not_the_doubled_card_id(self):
        # agent-bureau #2041 verbatim. The ref already NAMES DRE-2405, just
        # not where the gates read it — so recombining `agent/{card}-{tail}`
        # blindly suggests `agent/DRE-2405-DRE-2405-dev-loads-ws-ingest`. That
        # is the one case this notice was built for, and the one case where a
        # human following it literally lands on a still-unowned name.
        self.list_prs.return_value = [
            _pr(2041, "fix/DRE-2405-dev-loads-ws-ingest",
                updated="2026-08-12T00:00:00Z")]
        reconcile.flag_unowned_prs(now="2026-08-12T14:00:00Z")
        body = self.posted[0][1]
        self.assertIn("`agent/DRE-2405-dev-loads-ws-ingest`", body)
        self.assertNotIn("DRE-2405-DRE-2405", body)

    def test_every_rename_it_suggests_is_itself_owned(self):
        # The invariant behind the case above: a notice that hands back a name
        # the pipeline still skips is worse than no notice at all.
        for ref in (
            "fix/DRE-2405-dev-loads-ws-ingest",   # card id in the tail
            "fix/dev-loads-DRE-2405-ws-ingest",   # card id mid-slug
            "fix/dre-2405-lowercase",             # card id in lower case
            "fix/DRE-2405",                       # card id and nothing else
            "DRE-2405-no-prefix-at-all",          # no `/` to split on
            "fix/subscriptions-secret-drift",     # no card id at all
            "hotfix",                             # neither
        ):
            with self.subTest(ref=ref):
                want = reconcile._suggested_rename(ref, reconcile.branch_card(ref))
                self.assertTrue(
                    reconcile.pipeline_owns(want),
                    f"{ref} -> {want}, which the pipeline still skips",
                )
                self.assertNotRegex(
                    want, r"(?i)(DRE-\d+)[-_]\1",
                    f"{ref} -> {want}: the card id is doubled",
                )

    def test_a_renamed_branch_stops_being_reported(self):
        # The operator does the right thing; the sweep must go quiet.
        self.list_prs.return_value = [
            _pr(2035, "agent/DRE-2405-dev-loads-ws-ingest",
                updated="2026-08-12T00:00:00Z")]
        reconcile.flag_unowned_prs(now="2026-08-12T14:00:00Z")
        self.assertEqual(self.posted, [])

    def test_a_write_failure_does_not_abort_the_rest(self):
        # One unreachable PR must not silence every other finding — the same
        # rule the sibling backstops follow.
        def boom(number, body):
            if number == 1:
                raise RuntimeError("gh exploded")
            self.posted.append((number, body))

        with mock.patch.object(reconcile, "comment_on_pr", side_effect=boom):
            self.list_prs.return_value = [
                _pr(1, "fix/a", updated="2026-08-12T00:00:00Z"),
                _pr(2, "fix/b", updated="2026-08-12T00:00:00Z"),
            ]
            reconcile.flag_unowned_prs(now="2026-08-12T14:00:00Z")
        self.assertEqual([n for n, _ in self.posted], [2],
                         "one failure suppressed the other PR's notice")

    def test_an_unreadable_pr_list_is_not_treated_as_no_strandings(self):
        # Fail closed: a listing blip must not read as "all clear".
        self.list_prs.side_effect = RuntimeError("api down")
        with self.assertRaises(RuntimeError):
            reconcile.flag_unowned_prs(now="2026-08-12T14:00:00Z")


if __name__ == "__main__":
    unittest.main()
