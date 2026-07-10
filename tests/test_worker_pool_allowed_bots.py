"""Regression pin: DRE-2020 — the dispatch pool must pass every allowed_bots gate.

The 4-app dispatch pool (agent-bureau-bot-2/-3/-4, worker-identical GitHub
Apps added for API quota) authors PRs in product repos, but every
claude-code-action allowlist that admitted the worker bot hardcoded only
`agent-bureau-bot`. The QA critic and verifier therefore crashed on every
pool-authored PR:

    Workflow initiated by non-human actor: agent-bureau-bot-3 (type: Bot).
    Add bot to allowed_bots list

(observed live: dreadnought-foundry/agent-bureau PR #1901, QA Review run
29112893529).

This suite reads the LIVE workflow YAML at test time (same live-extraction
pattern as test_agent_fix_identity_gate.py — no copied fixtures) and pins:

  1. Wherever `agent-bureau-bot` appears in an allowed_bots value, ALL pool
     members (-2/-3/-4) appear too. Written generically over every workflow
     file, so a future workflow that allowlists the worker bot is caught the
     moment it lands without the pool.
  2. agent-fix.yml's allowlist stays EXACTLY `agent-bureau-qa-bot,github-actions`
     — that gate is a deliberate security lock (DRE-1988, see
     test_agent_fix_identity_gate.py) and must not be widened for the pool.
  3. No allowlist outside medic.yml is the `*` wildcard.
"""
import glob
import os
import re
import unittest

WORKFLOWS_DIR = os.path.join(
    os.path.dirname(__file__), "..", ".github", "workflows"
)

WORKER_BOT = "agent-bureau-bot"
POOL_BOTS = ["agent-bureau-bot-2", "agent-bureau-bot-3", "agent-bureau-bot-4"]

ALLOWED_BOTS_RE = re.compile(r"^\s*allowed_bots:\s*(.+?)\s*$", re.M)


def workflow_files():
    files = sorted(
        glob.glob(os.path.join(WORKFLOWS_DIR, "*.yml"))
        + glob.glob(os.path.join(WORKFLOWS_DIR, "*.yaml"))
    )
    assert files, f"no workflow files found under {WORKFLOWS_DIR}"
    return files


def allowed_bots_values(path):
    """All allowed_bots values in a live workflow file, as token lists.

    Values may be bare (`a,b`) or quoted (`"a,b"`); tokens are compared
    exactly (agent-bureau-bot-2 and agent-bureau-qa-bot are NOT the worker
    bot), so this survives whitespace/quoting churn but catches omissions.
    """
    values = []
    for raw in ALLOWED_BOTS_RE.findall(open(path).read()):
        value = raw.strip().strip("\"'")
        values.append([token.strip() for token in value.split(",")])
    return values


def all_sites():
    """(filename, token-list) for every allowed_bots site in the live repo."""
    sites = []
    for path in workflow_files():
        for tokens in allowed_bots_values(path):
            sites.append((os.path.basename(path), tokens))
    return sites


class PoolCoversEveryWorkerAllowlistTest(unittest.TestCase):
    """Wherever the worker bot is allowed, the whole pool must be allowed."""

    def test_worker_allowlists_exist(self):
        # Sanity: the invariant below is vacuous if extraction finds nothing.
        worker_sites = [s for s in all_sites() if WORKER_BOT in s[1]]
        self.assertGreaterEqual(
            len(worker_sites), 6,
            "expected worker-bot allowlists in qa-review.yml (x2), "
            f"verify.yml (x2), agent-task.yml, plan.yml; found {worker_sites}",
        )

    def test_every_worker_allowlist_includes_the_full_pool(self):
        failures = []
        for filename, tokens in all_sites():
            if WORKER_BOT not in tokens:
                continue
            missing = [bot for bot in POOL_BOTS if bot not in tokens]
            if missing:
                failures.append(f"{filename}: allowed_bots={tokens} missing {missing}")
        self.assertEqual(
            failures, [],
            "every allowed_bots that admits agent-bureau-bot must admit the "
            "whole dispatch pool (bot-2/3/4), or pool-authored PRs crash the "
            "critic/verifier:\n" + "\n".join(failures),
        )

    def test_expected_files_each_carry_a_pooled_worker_allowlist(self):
        # The six known sites, pinned per-file so a file-level regression is
        # named directly in the failure.
        expected = {
            "qa-review.yml": 2,
            "verify.yml": 2,
            "agent-task.yml": 1,
            "plan.yml": 1,
        }
        for filename, count in expected.items():
            sites = [
                tokens for name, tokens in all_sites()
                if name == filename and WORKER_BOT in tokens
            ]
            self.assertEqual(
                len(sites), count,
                f"{filename}: expected {count} worker allowed_bots site(s), found {sites}",
            )
            for tokens in sites:
                for bot in POOL_BOTS:
                    self.assertIn(
                        bot, tokens,
                        f"{filename}: allowed_bots={tokens} is missing {bot}",
                    )


class AgentFixGateUnchangedTest(unittest.TestCase):
    """agent-fix's allowlist is a security gate (DRE-1988) — the pool never
    triggers agent-fix (the qa-bot verdict comment does), so it must stay
    exactly as-is."""

    def test_agent_fix_allowlist_is_exactly_qa_bot_and_github_actions(self):
        path = os.path.join(WORKFLOWS_DIR, "agent-fix.yml")
        values = allowed_bots_values(path)
        self.assertEqual(
            values, [["agent-bureau-qa-bot", "github-actions"]],
            "agent-fix.yml allowed_bots must remain exactly "
            "'agent-bureau-qa-bot,github-actions' (identity gate, DRE-1988)",
        )


class NoNewWildcardTest(unittest.TestCase):
    """Only medic.yml may run for any bot actor."""

    def test_no_wildcard_allowlist_outside_medic(self):
        offenders = [
            (filename, tokens) for filename, tokens in all_sites()
            if filename != "medic.yml" and "*" in tokens
        ]
        self.assertEqual(
            offenders, [],
            f"wildcard allowed_bots is only permitted in medic.yml: {offenders}",
        )


if __name__ == "__main__":
    unittest.main()
