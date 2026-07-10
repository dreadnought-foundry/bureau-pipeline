"""Card-to-branch lookup must be ANCHORED (DRE-2025).

agent-task.yml resolves a card's branch twice — the "Gate on agent result"
step and the "Report result to Linear" step — with:

    BRANCH=$(git branch -r | grep -o "agent/${CARD}[^ ]*" | head -1 | ...)

The grep is an unanchored PREFIX match, so card DRE-142 matches branch
agent/DRE-1428-* (the card id is a prefix of the longer card id). head -1
then silently picks the wrong card's branch: the Report step posts a false
"PR opened" receipt and advances the card to In QA, and — because the
PR-found branch is checked before the escalation file — a genuine CEO
escalation written by the agent is swallowed. The fix requires the branch
delimiter after the card number (agent/DRE-142-...), so DRE-142 matches
only itself.

These tests extract the LIVE lookup statements out of agent-task.yml at
test time and execute them verbatim in bash (git shadowed by a fixture
function, same live-extraction discipline as test_agent_fix_identity_gate
/ test_merge_gate_decision_table). A revert to the unanchored grep turns
this file red; copied fixtures would not.
"""

import os
import re
import subprocess
import unittest

WORKFLOW = os.path.join(
    os.path.dirname(__file__), "..", ".github", "workflows", "agent-task.yml"
)

LOOKUP_RE = re.compile(r"^\s*(BRANCH=\$\(git branch -r \|.*\))\s*$", re.M)


def workflow_src() -> str:
    return open(WORKFLOW).read()


def lookup_statements() -> list:
    """The card-to-branch lookup statements, extracted from the live
    workflow. Both the Gate step and the Report step carry one."""
    return LOOKUP_RE.findall(workflow_src())


def run_lookup(statement: str, card: str, branch_r_output: str) -> str:
    """Execute one REAL lookup statement under bash exactly as the workflow
    step would (GitHub Actions runs `bash -e -o pipefail`), with `git`
    shadowed to return a canned `git branch -r` listing."""
    script = (
        'git() { printf "%s\\n" "$FAKE_BRANCH_R"; }\n'
        + statement
        + '\nprintf "%s" "$BRANCH"\n'
    )
    proc = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", script],
        env={**os.environ, "CARD": card, "FAKE_BRANCH_R": branch_r_output},
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"lookup statement failed (rc={proc.returncode}): {proc.stderr}"
        )
    return proc.stdout.strip()


def branch_listing(*branches: str) -> str:
    """A realistic `git branch -r` listing: two-space indent, HEAD pointer
    line, main, plus the given remote branches."""
    lines = ["  origin/HEAD -> origin/main", "  origin/main"]
    lines += [f"  origin/{b}" for b in branches]
    return "\n".join(lines)


class LookupSiteEnumerationTest(unittest.TestCase):
    def test_exactly_two_lookup_sites(self):
        # Gate step + Report step. If a third lookup appears it must be
        # added to the harness below; if one disappears the false-receipt
        # surface moved and this suite no longer covers it.
        self.assertEqual(len(lookup_statements()), 2)

    def test_both_sites_use_the_same_expression(self):
        # Drift guard: the two steps must never disagree about which
        # branch a card owns.
        stmts = lookup_statements()
        self.assertEqual(stmts[0], stmts[1])


class AnchoredLookupTest(unittest.TestCase):
    """Behavioral cases, executed against BOTH live lookup sites."""

    def all_sites(self, card: str, listing: str) -> list:
        stmts = lookup_statements()
        self.assertEqual(len(stmts), 2)
        return [run_lookup(s, card, listing) for s in stmts]

    def test_short_card_does_not_match_longer_card_branch(self):
        # THE DRE-2025 bug: DRE-142 is a string prefix of DRE-1428, so the
        # unanchored grep resolves DRE-142 to DRE-1428's branch.
        listing = branch_listing("agent/DRE-1428-pipeline-ref-threading")
        for got in self.all_sites("DRE-142", listing):
            self.assertEqual(
                got, "", "DRE-142 must not resolve to agent/DRE-1428-*"
            )

    def test_card_matches_its_own_branch(self):
        listing = branch_listing("agent/DRE-142-anchor-grep")
        for got in self.all_sites("DRE-142", listing):
            self.assertEqual(got, "agent/DRE-142-anchor-grep")

    def test_mixed_listing_resolves_to_own_branch_only(self):
        # The longer card's branch sorts FIRST so head -1 picks the wrong
        # branch under the unanchored grep — the false-receipt path.
        listing = branch_listing(
            "agent/DRE-1428-pipeline-ref-threading",
            "agent/DRE-142-anchor-grep",
        )
        for got in self.all_sites("DRE-142", listing):
            self.assertEqual(got, "agent/DRE-142-anchor-grep")

    def test_longer_card_still_matches_itself(self):
        listing = branch_listing(
            "agent/DRE-1428-pipeline-ref-threading",
            "agent/DRE-142-anchor-grep",
        )
        for got in self.all_sites("DRE-1428", listing):
            self.assertEqual(got, "agent/DRE-1428-pipeline-ref-threading")

    def test_no_branch_yields_empty_not_failure(self):
        # grep exits 1 on no match; under pipefail the || true guard must
        # keep the step alive and BRANCH empty (the no-PR reporting path).
        listing = branch_listing("agent/DRE-999-unrelated")
        for got in self.all_sites("DRE-142", listing):
            self.assertEqual(got, "")


if __name__ == "__main__":
    unittest.main()
