"""Wiring pin: DRE-2120 — every hardcoded qa-bot LOGIN literal is rostered.

Two trigger gates hardcode the login `agent-bureau-qa-bot[bot]`: the merge
gate's verdict-landing leg (merge-gate.yml `issue_comment` job-if) and the
fix agent's dispatch gate (agent-fix.yml job-if). The gates' own decision
logic derives the trusted login from the minted App token's app-slug
precisely so an App rename follows automatically — but a job-if runs before
any step can mint a token, so these two sites CANNOT derive it and stay
hardcoded. If the qa App is ever renamed and one site is missed, both
comment-triggered legs go dark SILENTLY: verdicts stop landing merges
(recovered ~15 min later by reconcile's gate nudge) and REQUEST_CHANGES
verdicts stop dispatching the fix agent (covered by NO sweep — PRs strand).
This is the DRE-2020 failure shape (hardcoded identity at a gate), found
on paper by the DRE-2110 vendor-boundary backfill audit (checklist Q1).

This suite reads the LIVE workflow YAML at test time (same live-extraction
pattern as test_worker_pool_allowed_bots.py — no copied fixtures) and pins:

  1. The literal appears ONLY in the rostered files, and every occurrence
     is classified (job-if author equality, jq author filter, or comment)
     with EXACT per-file counts. A new hardcoded site — new file, new
     line, or a kind this suite has never seen — fails the roster.
  2. Both job-ifs still carry the exact author-equality expression (the
     DRE-1987 / DRE-1988 identity gates must not silently loosen).
  3. Both job-ifs carry a RENAME PROCEDURE comment naming this test, so
     whoever renames the App is pointed at the full roster instead of
     patching the one site they happened to notice.

Renaming the qa App = update EVERY rostered site AND this file's
QA_BOT_LOGIN/ROSTER together, in one commit.
"""
import os
import re
import unittest
from glob import glob

WORKFLOWS_DIR = os.path.join(
    os.path.dirname(__file__), "..", ".github", "workflows"
)

# The single shared constant every hardcoded site must match. GitHub
# reserves the "[bot]" suffix, so no user account can impersonate it;
# empirically the critic posts as this login since bureau-pipeline #51.
QA_BOT_LOGIN = "agent-bureau-qa-bot[bot]"

# The two trigger gates: a job-if author-equality check on the comment
# event. Single-quoted (GitHub expression syntax) — exact form pinned.
JOB_IF_EQUALITY = (
    "github.event.comment.user.login == 'agent-bureau-qa-bot[bot]'"
)

# In-step jq author filters (agent-fix.yml reads verdict/budget state back
# from PR comments and must trust only qa-bot-authored bodies, DRE-1988).
JQ_AUTHOR_FILTER_RE = re.compile(
    r'select\(\.(?:value\.)?user\.login == "agent-bureau-qa-bot\[bot\]"\)'
)

# The exact roster: every workflow file allowed to contain the literal,
# with per-kind occurrence counts. Anything off-roster is a regression.
ROSTER = {
    "merge-gate.yml": {"job-if": 1, "jq-author-filter": 0, "comment": 1},
    "agent-fix.yml": {"job-if": 1, "jq-author-filter": 3, "comment": 0},
}


def workflow_files():
    files = sorted(
        glob(os.path.join(WORKFLOWS_DIR, "*.yml"))
        + glob(os.path.join(WORKFLOWS_DIR, "*.yaml"))
    )
    assert files, f"no workflow files found under {WORKFLOWS_DIR}"
    return files


def classify(line):
    """One kind per matched line — None means a site this suite has never
    seen, which the roster test reports as a new hardcoded site."""
    if line.lstrip().startswith("#"):
        return "comment"
    if JOB_IF_EQUALITY in line:
        return "job-if"
    if JQ_AUTHOR_FILTER_RE.search(line):
        return "jq-author-filter"
    return None


def occurrences(path):
    """(lineno, line, count) for every line carrying the login literal."""
    out = []
    for lineno, line in enumerate(open(path).read().splitlines(), start=1):
        count = line.count(QA_BOT_LOGIN)
        if count:
            out.append((lineno, line, count))
    return out


class LiteralRosterTest(unittest.TestCase):
    """Every hardcoded qa-bot login literal in the live workflows is
    enumerated and matches the roster exactly."""

    def test_roster_sites_exist(self):
        # Sanity: the pins below are vacuous if extraction finds nothing.
        for filename in ROSTER:
            path = os.path.join(WORKFLOWS_DIR, filename)
            self.assertTrue(
                occurrences(path),
                f"{filename}: no {QA_BOT_LOGIN} literal found — the "
                "extraction regressed or the gate was rewritten; update "
                "the roster deliberately, not by deletion",
            )

    def test_literal_appears_only_in_rostered_files(self):
        found = {
            os.path.basename(path)
            for path in workflow_files()
            if occurrences(path)
        }
        self.assertEqual(
            found, set(ROSTER),
            f"files carrying the hardcoded {QA_BOT_LOGIN} literal drifted "
            "from the roster — a rename must update every site plus this "
            f"test together (DRE-2120): {sorted(found)}",
        )

    def test_every_occurrence_is_classified_and_counts_match(self):
        unclassified = []
        for path in workflow_files():
            filename = os.path.basename(path)
            counts = {"job-if": 0, "jq-author-filter": 0, "comment": 0}
            for lineno, line, count in occurrences(path):
                kind = classify(line)
                if kind is None:
                    unclassified.append(f"{filename}:{lineno}: {line.strip()}")
                else:
                    counts[kind] += count
            expected = ROSTER.get(
                filename, {"job-if": 0, "jq-author-filter": 0, "comment": 0}
            )
            self.assertEqual(
                counts, expected,
                f"{filename}: hardcoded {QA_BOT_LOGIN} sites drifted from "
                f"the roster (expected {expected}) — a rename or a new "
                "hardcoded gate must update every site plus this test "
                "together (DRE-2120)",
            )
        self.assertEqual(
            unclassified, [],
            "hardcoded qa-bot login in a shape this roster has never seen "
            "— classify and roster it deliberately (DRE-2120):\n"
            + "\n".join(unclassified),
        )


class TriggerGateEqualityTest(unittest.TestCase):
    """The two job-if identity gates keep the exact author-equality check
    (DRE-1987 merge-gate verdict leg, DRE-1988 fix dispatch gate)."""

    def job_if(self, filename, job):
        import yaml

        doc = yaml.safe_load(open(os.path.join(WORKFLOWS_DIR, filename)))
        return doc["jobs"][job]["if"]

    def test_merge_gate_issue_comment_leg_requires_qa_login(self):
        self.assertIn(
            JOB_IF_EQUALITY, self.job_if("merge-gate.yml", "evaluate"),
            "merge-gate.yml evaluate job-if lost the qa-bot author check — "
            "anyone could wake the gate by typing 'QA Critic'",
        )

    def test_agent_fix_dispatch_gate_requires_qa_login(self):
        self.assertIn(
            JOB_IF_EQUALITY, self.job_if("agent-fix.yml", "fix"),
            "agent-fix.yml fix job-if lost the qa-bot author check — "
            "anyone could spawn a code-writing agent by commenting "
            "'VERDICT: REQUEST_CHANGES'",
        )


class RenameProcedureDocumentedTest(unittest.TestCase):
    """Both hardcoded job-ifs carry a comment naming the rename procedure:
    update all rostered sites + this test together. The comment is what
    turns a silent partial rename into a deliberate, greppable change."""

    def comment_block_above_job_if(self, filename):
        lines = open(os.path.join(WORKFLOWS_DIR, filename)).read().splitlines()
        literal_idx = next(
            i for i, ln in enumerate(lines)
            if JOB_IF_EQUALITY in ln and not ln.lstrip().startswith("#")
        )
        if_idx = next(
            i for i in range(literal_idx, -1, -1)
            if lines[i].lstrip().startswith("if:")
        )
        block = []
        i = if_idx - 1
        while i >= 0 and lines[i].lstrip().startswith("#"):
            block.append(lines[i])
            i -= 1
        return "\n".join(reversed(block))

    def assert_rename_procedure(self, filename):
        block = self.comment_block_above_job_if(filename)
        for needle in ("RENAME PROCEDURE", "test_qa_login_literal_roster.py"):
            self.assertIn(
                needle, block,
                f"{filename}: the comment above the hardcoded job-if must "
                f"name the rename procedure (missing {needle!r}) — update "
                "all rostered sites + the roster test together (DRE-2120)",
            )

    def test_merge_gate_job_if_names_the_rename_procedure(self):
        self.assert_rename_procedure("merge-gate.yml")

    def test_agent_fix_job_if_names_the_rename_procedure(self):
        self.assert_rename_procedure("agent-fix.yml")


if __name__ == "__main__":
    unittest.main()
