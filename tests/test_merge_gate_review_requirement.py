"""No mapped repo may require an approving review while the merge gate wakes
on the verdict comment alone (Stage 2 #20, review G7 and addition 42).

The critic posts its verdict COMMENT (post_verdict.sh) before it files the
formal APPROVE review beside it. Since #20 the comment is the critic's only
wake: the gate no longer runs on the QA Review run finishing, and a bot's
review never wakes it (merge-gate.yml's `pull_request_review` leg admits only
a person's). So if a repo's main ever required an approving review, the
comment's wake could read BLOCKED before the formal review lands, and nothing
would wake the gate again until the sweep's nudge.

Two settings could require one:
  - `required_approving_review_count` above zero, on classic branch
    protection or on a ruleset's `pull_request` rule;
  - a ruleset's `require_extra_approval_for_unattributed_changes`. GitHub's
    documentation ("Available rules for rulesets", section "Additional
    approval for unattributed Copilot pull requests") scopes it to a pull
    request Copilot opens under its own app identity, where it "requires one
    more approval than the number you configured", and says: "This setting has
    no effect if the ruleset requires zero approvals". So it requires a
    review exactly when the count is above zero — which is already a failure
    here — and the test names the flag in that failure so nobody reads the
    count alone. Portico carries the flag (true, the default GitHub set) with
    a count of 0, and the agent bot's PR #879 merged under it on
    2026-10-02 with no review.

`required_review_reasons` is the rule, proved offline below on every run.

The LIVE check is opt-in, and skips unless `BUREAU_LIVE_CHECKS=1`: a guard
that makes network calls in every local and CI run is the very class of
problem it would be guarding against. Run it deliberately, as the operator,
with gh logged in:

    BUREAU_LIVE_CHECKS=1 python3 -m pytest \
      tests/test_merge_gate_review_requirement.py -v -rs

It reads every repo in config/repo-map.json with GET-only `gh api` calls
(classic protection and the default branch's active rules), never Linear or
AWS. A repo it cannot read is skipped and named; a repo whose plan cannot
have protection (GitHub answers 403 "Upgrade to GitHub Pro") requires
nothing.

Offline only: python3 -m pytest tests/test_merge_gate_review_requirement.py -v
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess  # nosec B404 — fixed-arg GET calls to the gh CLI
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO_MAP = ROOT / "config" / "repo-map.json"

FLAG = "require_extra_approval_for_unattributed_changes"
NO_PLAN = "Upgrade to GitHub Pro"
NOT_PROTECTED = "Branch not protected"


def required_review_reasons(protection, rules) -> list[str]:
    """Why a merge into this branch would wait for an approving review;
    empty when nothing requires one.

    `protection` is GET branches/{b}/protection (None = not protected);
    `rules` is GET rules/branches/{b}, every active rule on the branch from
    every ruleset (None = none)."""
    reasons = []
    reviews = (protection or {}).get("required_pull_request_reviews") or {}
    count = reviews.get("required_approving_review_count") or 0
    if count > 0:
        reasons.append(f"branch protection requires {count} approving review(s)")
    for rule in rules or []:
        if rule.get("type") != "pull_request":
            continue
        params = rule.get("parameters") or {}
        count = params.get("required_approving_review_count") or 0
        if count > 0:
            where = f"ruleset {rule.get('ruleset_id', '?')}"
            reasons.append(f"{where} requires {count} approving review(s)")
            if params.get(FLAG):
                reasons.append(
                    f"{where} also sets {FLAG}, one more for an unattributed "
                    "Copilot pull request"
                )
    return reasons


PORTICO_RULE = {  # portico's live pull_request rule, read 2026-10-02
    "type": "pull_request", "ruleset_id": 23194028,
    "parameters": {"required_approving_review_count": 0,
                   "require_code_owner_review": False, FLAG: True},
}


class TheRuleTest(unittest.TestCase):
    def test_nothing_protected_requires_nothing(self):
        self.assertEqual(required_review_reasons(None, None), [])

    def test_portico_today_requires_nothing(self):
        """The flag is true and the count is 0: GitHub says the flag has no
        effect at zero approvals."""
        self.assertEqual(required_review_reasons(None, [PORTICO_RULE]), [])

    def test_a_ruleset_count_requires_a_review_and_names_the_flag(self):
        rule = json.loads(json.dumps(PORTICO_RULE))
        rule["parameters"]["required_approving_review_count"] = 1
        reasons = required_review_reasons(None, [rule])
        self.assertEqual(len(reasons), 2, reasons)
        self.assertIn("requires 1 approving review", reasons[0])
        self.assertIn(FLAG, reasons[1])

    def test_a_classic_protection_count_requires_a_review(self):
        protection = {"required_pull_request_reviews": {"required_approving_review_count": 2}}
        self.assertEqual(len(required_review_reasons(protection, [])), 1)

    def test_other_rules_require_nothing(self):
        rules = [{"type": "deletion"}, {"type": "required_status_checks",
                                        "parameters": {"required_status_checks": []}}]
        self.assertEqual(required_review_reasons(None, rules), [])


def _gh_get(path: str):
    """(payload, None) on success; (None, error text) on failure. GET only.

    Several suites set a placeholder `GH_TOKEN` in this process at import
    (CI sets one too). A 401 is retried once without the token variables, so
    gh's own stored login answers where there is one; in CI there is none and
    the repo is skipped."""
    env = dict(os.environ)
    for attempt in (1, 2):
        p = subprocess.run(  # nosec B603 B607 — fixed-arg GET, no shell
            ["gh", "api", "--method", "GET", path],
            capture_output=True, text=True, timeout=30, check=False, env=env,
        )
        if p.returncode == 0:
            return json.loads(p.stdout or "null"), None
        err = (p.stdout + p.stderr).strip()
        if attempt == 1 and "HTTP 401" in err:
            env = {k: v for k, v in env.items() if k not in ("GH_TOKEN", "GITHUB_TOKEN")}
            continue
        return None, err
    return None, err


LIVE_OPT_IN = "BUREAU_LIVE_CHECKS"


@unittest.skipUnless(
    os.environ.get(LIVE_OPT_IN) == "1",
    f"live GitHub read — opt in with {LIVE_OPT_IN}=1 (see the module docstring)",
)
class NoMappedRepoRequiresAnApprovingReviewTest(unittest.TestCase):
    def test_no_mapped_repo_requires_approving_review(self):
        if shutil.which("gh") is None:
            self.skipTest("no gh CLI — the live read is skipped offline")
        repos = sorted(set(json.loads(REPO_MAP.read_text()).values()))
        self.assertTrue(repos, "config/repo-map.json maps no repo")
        read = 0
        for repo in repos:
            with self.subTest(repo=repo):
                meta, err = _gh_get(f"repos/{repo}")
                if meta is None:
                    self.skipTest(f"{repo} unreadable with this token: {err[:200]}")
                branch = meta.get("default_branch") or "main"
                protection, err = _gh_get(f"repos/{repo}/branches/{branch}/protection")
                if protection is None and NO_PLAN in err:
                    read += 1
                    continue  # the plan cannot protect a branch: nothing required
                if protection is None and NOT_PROTECTED not in err:
                    self.skipTest(f"{repo} protection unreadable: {err[:200]}")
                rules, err = _gh_get(f"repos/{repo}/rules/branches/{branch}")
                if rules is None:
                    self.skipTest(f"{repo} rules unreadable: {err[:200]}")
                read += 1
                self.assertEqual(
                    required_review_reasons(protection, rules), [],
                    f"{repo}@{branch} requires an approving review — the gate's "
                    "comment-only wake for a critic verdict (Stage 2 #20) could "
                    "read BLOCKED before the critic's formal review lands, and no "
                    "bot review re-wakes the gate",
                )
        if read == 0:
            self.skipTest("no mapped repo was readable with this token")


if __name__ == "__main__":
    unittest.main()
