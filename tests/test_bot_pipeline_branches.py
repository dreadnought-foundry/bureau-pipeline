"""RED-first tests for the two scheduled jobs' own branches (DRE-3879).

`split-ledger.yml` and `model-drift.yml` both ended in `git push origin
HEAD:main` and both had been failing on it — `GH006: Protected branch update
failed`, every day for the ledger, every week for the drift watch. The CEO's
signed answers settle the remedy and its limits, and this module is where each
limit is held:

  * **2026-09-14 08:14 PT** — open a pull request; change no branch
    protection. So: each job commits to ONE fixed branch and opens or updates
    ONE pull request, and nothing in this repository touches a GitHub
    protection rule, ruleset or repository setting.
  * **2026-09-15 13:12 PT** — add EXACTLY two literal branch names,
    `bot/split-ledger` and `bot/model-drift`, trusted the way
    `bot/standards-sync` is. No wildcard; no other branch gains merge rights.
    CI and the critic still run, and a red check or a REQUEST_CHANGES verdict
    still blocks the merge.

Two literals, not `bot/*`, for the reason merge-gate.yml already gives about
`bot/standards-sync`: a broad prefix hands auto-merge to every future branch
somebody happens to name that way — a permission nobody asked for.

The trusted list is the same fact in three places, and DRE-2426 is what
happens when they drift: eight hand-copied answers to "does automation own
this branch", four of them different, and four pull requests stranded for ten
hours. So the branch names here are read OUT of the workflows that use them
and checked against `reconcile.PIPELINE_BRANCH_PREFIXES`, rather than typed a
fourth time.
"""

import os
import re
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
MERGE_GATE = WORKFLOWS / "merge-gate.yml"

sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/test")
os.environ.setdefault("GH_TOKEN", "test")

import bot_branch_harness as H  # noqa: E402
import merge_gate  # noqa: E402
import reconcile  # noqa: E402
import should_review_pr  # noqa: E402

#: The two jobs this card fixes, and the branch each one is required to use.
#: The names are the CEO's, quoted verbatim on the card — pinned as literals
#: here on purpose, because "exactly these two and no others" is the decision.
JOB_BRANCHES = {
    "split-ledger.yml": "bot/split-ledger",
    "model-drift.yml": "bot/model-drift",
}

HEAD = "aa11" * 10
QA_LOGIN = "agent-bureau-qa-bot[bot]"
GREEN_CI = [{"name": "unit", "status": "completed", "conclusion": "success"}]
RED_CI = [{"name": "unit", "status": "completed", "conclusion": "failure"}]


def _verdict(token: str):
    return [{"user": {"login": QA_LOGIN, "type": "Bot"},
             "body": f"🔎 QA Critic — VERDICT: {token} @{HEAD}"}]


def _doc(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text())


def _steps(name: str) -> list:
    return [s
            for job in (_doc(name).get("jobs") or {}).values()
            for s in (job or {}).get("steps") or []
            if isinstance(s, dict)]


def publish_step(name: str) -> dict:
    """The one step that publishes the job's generated files."""
    found = [s for s in _steps(name) if "bot_branch_pr.py" in (s.get("run") or "")]
    assert len(found) == 1, f"{name}: expected one publish step, found {len(found)}"
    return found[0]


def declared_branch(name: str) -> str:
    """The `--branch` the workflow hands the publisher — read, never assumed."""
    m = re.search(r"--branch\s+(\S+)", publish_step(name)["run"])
    assert m, f"{name}: the publish step names no branch"
    return m.group(1)


def declared_paths(name: str) -> list[str]:
    return re.findall(r"--path\s+(\S+)", publish_step(name)["run"])


def _uncommented(text: str) -> str:
    return "\n".join(line for line in text.splitlines()
                     if not line.lstrip().startswith("#"))


def _shell_gate_prefixes() -> set[str]:
    """merge-gate.yml's `case "$BRANCH" in …)` — the same read
    tests/test_pipeline_ownership.py makes, so the two agree by construction."""
    m = re.search(r'case "\$BRANCH" in ([^)]+)\)', MERGE_GATE.read_text())
    assert m is not None, "merge-gate.yml: no branch case statement"
    return {p.strip().rstrip("*") for p in m.group(1).split("|")}


def _resolve_if() -> str:
    return _doc("merge-gate.yml")["jobs"]["resolve"]["if"]


# --------------------------------------------------------------------------- #
# each job commits to its own fixed branch                                     #
# --------------------------------------------------------------------------- #


class EachJobHasOneFixedBranchTest(unittest.TestCase):
    def test_each_workflow_names_the_branch_the_card_names(self):
        for workflow, branch in JOB_BRANCHES.items():
            with self.subTest(workflow=workflow):
                self.assertEqual(declared_branch(workflow), branch)

    def test_the_two_branches_are_distinct(self):
        self.assertEqual(len(set(JOB_BRANCHES.values())), 2)

    def test_neither_workflow_pushes_to_main(self):
        for workflow in JOB_BRANCHES:
            with self.subTest(workflow=workflow):
                text = _uncommented((WORKFLOWS / workflow).read_text())
                self.assertNotIn("HEAD:main", text)
                self.assertNotRegex(
                    text, r"git\s+push[^\n]*\bmain\b",
                    "a push to main is back — branch protection refuses it")

    def test_each_job_still_stages_only_what_it_generates(self):
        """The guarantee that made these jobs safe to give `contents: write`:
        the generated paths, by name. It moved into the publisher (which
        proves the staged set and refuses anything else); the workflow's job
        is still to name exactly those paths and no others."""
        self.assertEqual(declared_paths("model-drift.yml"), ["models.json"])
        self.assertEqual(sorted(declared_paths("split-ledger.yml")),
                         ["config/split-ledger.json", "docs/split-ledger.md"])
        for workflow in JOB_BRANCHES:
            with self.subTest(workflow=workflow):
                runs = "\n".join(s.get("run", "") for s in _steps(workflow))
                for sweep in ("git add .", "git add -A", "git add --all"):
                    self.assertNotIn(sweep, runs)


# --------------------------------------------------------------------------- #
# the trusted list — exactly two literals, in every place that holds one       #
# --------------------------------------------------------------------------- #


class TheTrustedListTest(unittest.TestCase):
    """`bot/standards-sync` is trusted in three places at once: the merge
    gate's event filter, the merge gate's branch gate, and reconcile's
    `pipeline_owns`. Each new branch joins all three or it is stranded in
    exactly the way DRE-2426 describes."""

    def test_the_merge_gate_branch_gate_admits_both(self):
        prefixes = _shell_gate_prefixes()
        for branch in JOB_BRANCHES.values():
            with self.subTest(branch=branch):
                self.assertIn(branch, prefixes)

    def test_the_merge_gate_event_filter_admits_both(self):
        """The `resolve` job's `if:` decides whether the gate wakes at all on
        the CI leg. A branch in the case statement but not here is a PR the
        gate never evaluates until reconcile's ~15-minute nudge finds it."""
        condition = _resolve_if()
        for branch in JOB_BRANCHES.values():
            with self.subTest(branch=branch):
                self.assertIn(f"head_branch == '{branch}'", condition)

    def test_the_sweeps_own_both(self):
        for branch in JOB_BRANCHES.values():
            with self.subTest(branch=branch):
                self.assertTrue(reconcile.pipeline_owns(branch))

    def test_the_narrow_questions_stay_narrow(self):
        """Same split `bot/standards-sync` and `dependabot/` sit on: these
        branches carry no card, so no card sweep claims them and the fix agent
        is never pointed at one."""
        for branch in JOB_BRANCHES.values():
            with self.subTest(branch=branch):
                self.assertFalse(reconcile.card_branch(branch))
                self.assertFalse(reconcile.fix_eligible(branch))

    def test_no_wildcard_was_added(self):
        """The decision in one assertion: two literal names, not `bot/*`. A
        prefix would hand auto-merge to every future branch named that way."""
        self.assertFalse(reconcile.pipeline_owns("bot/something-else"))
        self.assertFalse(reconcile.pipeline_owns("bot/split-ledger-2"))
        self.assertFalse(reconcile.pipeline_owns("bot/split-ledger/evil"))
        self.assertFalse(reconcile.pipeline_owns("evil/bot/split-ledger"))
        self.assertNotIn("bot/", _shell_gate_prefixes() - set(JOB_BRANCHES.values())
                         - {"bot/standards-sync"})
        self.assertNotIn("bot/*", _uncommented(MERGE_GATE.read_text()))

    def test_exactly_these_branches_gained_merge_rights(self):
        """Nothing else joined the trusted list on this card. The set is read
        from the gate itself and compared against what it held before, plus
        the two the CEO named."""
        before = {"agent/", "repair/", "dependabot/", "bot/standards-sync"}
        self.assertEqual(_shell_gate_prefixes(),
                         before | set(JOB_BRANCHES.values()))
        self.assertEqual(set(reconcile.PIPELINE_BRANCH_PREFIXES),
                         before | set(JOB_BRANCHES.values()))


# --------------------------------------------------------------------------- #
# the gates that still gate                                                    #
# --------------------------------------------------------------------------- #


class TheChecksStillGateTest(unittest.TestCase):
    """Trusted means "the gate will consider this branch", never "the gate
    waves it through". The CEO's answer says so in terms, and this is it."""

    def _decide(self, checks, comments, branch):
        return merge_gate.decide(
            head_sha=HEAD, qa_login=QA_LOGIN, check_runs=checks,
            comments=comments, head_branch=branch, pr_author="agent-bureau-bot",
        )

    def test_the_critic_reviews_these_pull_requests(self):
        for branch in JOB_BRANCHES.values():
            with self.subTest(branch=branch):
                self.assertTrue(should_review_pr.should_review(branch))

    def test_a_red_check_blocks_the_merge(self):
        for branch in JOB_BRANCHES.values():
            with self.subTest(branch=branch):
                decision = self._decide(RED_CI, _verdict("APPROVE"), branch)
                self.assertNotEqual(decision.decision, "merge", decision.reason)

    def test_a_request_changes_verdict_blocks_the_merge(self):
        for branch in JOB_BRANCHES.values():
            with self.subTest(branch=branch):
                decision = self._decide(GREEN_CI, _verdict("REQUEST_CHANGES"),
                                        branch)
                self.assertNotEqual(decision.decision, "merge", decision.reason)

    def test_no_verdict_at_all_blocks_the_merge(self):
        for branch in JOB_BRANCHES.values():
            with self.subTest(branch=branch):
                decision = self._decide(GREEN_CI, [], branch)
                self.assertNotEqual(decision.decision, "merge", decision.reason)

    def test_green_ci_and_an_approve_merges(self):
        """The control: without this the three tests above would pass on a
        gate that merges nothing at all."""
        for branch in JOB_BRANCHES.values():
            with self.subTest(branch=branch):
                decision = self._decide(GREEN_CI, _verdict("APPROVE"), branch)
                self.assertEqual(decision.decision, "merge", decision.reason)

    def test_both_repository_checks_still_run_on_these_pull_requests(self):
        """Nothing filters these branches out of CI or the critic: both
        workflows fire on `pull_request` with no branch condition at all."""
        for name in ("tests.yml", "pr-review.yml"):
            with self.subTest(workflow=name):
                doc = _doc(name)
                on = doc.get("on", doc.get(True))
                self.assertIn("pull_request", on)
                trigger = on["pull_request"] or {}
                self.assertNotIn("branches", trigger)
                self.assertNotIn("branches-ignore", trigger)


# --------------------------------------------------------------------------- #
# no GitHub setting is touched                                                 #
# --------------------------------------------------------------------------- #


class NoRepositorySettingIsChangedTest(unittest.TestCase):
    """The CEO's first answer, held mechanically. `main` keeps requiring a
    pull request and both required checks; the trusted-list edit above is our
    own pipeline code, not a GitHub rule."""

    #: Ways a workflow or script could reach a protection rule, a ruleset or a
    #: repository setting. Matched against API paths and commands, never prose
    #: — the files here discuss branch protection constantly and must stay
    #: free to.
    FORBIDDEN = (
        r"branches/[^\s\"']+/protection",
        r"repos/[^\s\"']+/rulesets",
        r"gh\s+repo\s+edit",
        r"gh\s+ruleset",
    )

    def test_nothing_in_the_pipeline_writes_a_github_rule(self):
        offenders = []
        for path in sorted(WORKFLOWS.glob("*.yml")) + \
                sorted((ROOT / "scripts").rglob("*.py")):
            text = path.read_text()
            for pattern in self.FORBIDDEN:
                if re.search(pattern, text):
                    offenders.append(f"{path.relative_to(ROOT)}: {pattern}")
        self.assertEqual(offenders, [],
                         "a branch-protection, ruleset or repository-setting "
                         "write appeared — the CEO's answer was to open a pull "
                         "request instead")


# --------------------------------------------------------------------------- #
# both publish steps, RUN                                                      #
# --------------------------------------------------------------------------- #


class BothPublishStepsRunTest(unittest.TestCase):
    """Each workflow's own publish step, executed in a throwaway repository
    with a real `origin` and a fake `gh`. Reading the YAML says what it
    intends; this says what it does."""

    def _repo(self, workflow: str):
        repo = H.BotBranchRepo(self.addCleanup)
        for path in declared_paths(workflow):
            repo.write(path, '{"generated_at": "2026-09-15T00:00:00Z"}\n')
        repo.write("agents.yaml", "# a file these jobs may never touch\n")
        repo.seed()
        return repo

    def _regenerate(self, repo, workflow, stamp):
        for path in declared_paths(workflow):
            repo.write(path, '{"generated_at": "%s"}\n' % stamp)

    def test_a_regeneration_lands_on_the_bot_branch_behind_one_pull_request(self):
        for workflow, branch in JOB_BRANCHES.items():
            with self.subTest(workflow=workflow):
                repo = self._repo(workflow)
                before = repo.origin_ref("main")
                self._regenerate(repo, workflow, "2026-09-16T00:00:00Z")
                result = repo.run(publish_step(workflow)["run"])
                self.assertEqual(result.returncode, 0,
                                 result.stdout + result.stderr)
                self.assertEqual(repo.origin_ref("main"), before,
                                 "main moved — the job still pushes to it")
                self.assertTrue(repo.origin_ref(branch))
                self.assertEqual(repo.origin_files(branch),
                                 sorted(declared_paths(workflow)))
                creates = repo.pr_creates()
                self.assertEqual(len(creates), 1, creates)
                self.assertEqual(repo.flag(creates[0], "--head"), branch)
                self.assertIn(branch, repo.flag(creates[0], "--body"),
                              "the pull request body does not name its branch")

    def test_a_run_with_nothing_changed_opens_no_pull_request_and_is_green(self):
        for workflow, branch in JOB_BRANCHES.items():
            with self.subTest(workflow=workflow):
                repo = self._repo(workflow)
                before = repo.origin_ref("main")
                result = repo.run(publish_step(workflow)["run"])
                self.assertEqual(result.returncode, 0,
                                 result.stdout + result.stderr)
                self.assertEqual(repo.pr_creates(), [])
                self.assertEqual(repo.origin_ref(branch), "")
                self.assertEqual(repo.origin_ref("main"), before)

    def test_a_week_of_runs_opens_exactly_one_pull_request(self):
        for workflow, branch in JOB_BRANCHES.items():
            with self.subTest(workflow=workflow):
                repo = self._repo(workflow)
                for day in range(16, 23):
                    self._regenerate(repo, workflow, f"2026-09-{day}T00:00:00Z")
                    result = repo.run(publish_step(workflow)["run"])
                    self.assertEqual(result.returncode, 0,
                                     result.stdout + result.stderr)
                self.assertEqual(len(repo.pr_creates()), 1,
                                 "one pull request per run — seven a week, "
                                 "365 a year, each one reviewed by the critic")


if __name__ == "__main__":
    unittest.main()
