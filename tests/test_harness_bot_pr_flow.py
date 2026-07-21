"""RED-first tests for the bot_pr_flow harness scenario (DRE-2098).

bot_pr_flow proves the happy path end to end in the sandbox repo: the
worker bot pushes an `agent/harness-…` branch and opens a PR, the real
critic posts a verdict bound to the head sha, the real merge gate merges
as the qa-bot, and cleanup leaves the sandbox default branch usable.

The LIVE scenario mocks nothing GitHub-side — that is the whole point of
the epic. This suite drives the scenario's LOGIC against an in-memory
FakeGitHub that mimics only the REST shapes the driver reads, so we can
prove offline:

  * the exercise phase creates exactly the namespaced, reviewable shape
    (agent/ prefix, NO DRE-n card ref — the Linear-side decision: every
    pipeline Linear touchpoint no-ops on a cardless branch);
  * the verify phase accepts only a qa-authored verdict BOUND to the head
    sha and a merge BY the qa-bot — and names the failure otherwise;
  * cleanup closes/deletes everything the run created, and a run started
    after a simulated mid-flight crash still passes (the acceptance
    criterion for self-cleaning runs);
  * nothing the harness posts ever contains a verdict marker (the
    untrusted-content standard's never-emit rule).

These tests must FAIL before scripts/harness/ exists, and PASS after.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import merge_gate  # noqa: E402
from harness import framework  # noqa: E402
from harness import scenarios  # noqa: E402
from harness.scenarios import bot_pr_flow  # noqa: E402

QA = "agent-bureau-qa-bot[bot]"
WORKER = "agent-bureau-bot[bot]"


class FakeGitHub:
    """In-memory stand-in exposing the client methods the driver uses,
    returning the same REST shapes the real GitHub API returns. Shared by
    test_harness_framework.py's sweep tests and the sibling scenario
    suites (dependabot_flow / gate_paths)."""

    def __init__(self, default_branch="main"):
        self._default = default_branch
        self._sha_counter = 0
        self.branches = {default_branch: self._new_sha()}
        self.files = {}  # (branch, path) -> content
        self.prs = {}  # number -> PR dict (REST shape)
        self.comments = {}  # number -> [comment dict]
        self.commits = {}  # sha -> REST commit shape (parents/author/committer)
        self.check_runs = {}  # sha -> [check-run dicts]
        self.on_create_pr = None  # hook(fake, pr) — the test's "pipeline"
        self.on_poll = None  # hook(fake) — fired on get_pr/list_comments polls

    # -- helpers for tests -------------------------------------------------
    def _new_sha(self):
        self._sha_counter += 1
        return f"{self._sha_counter:040x}"

    def _record_commit(self, sha, parents, login):
        self.commits[sha] = {
            "sha": sha,
            "parents": [{"sha": p} for p in parents],
            "author": {"login": login},
            "committer": {"login": login},
        }

    def seed_pr(self, head, state="open", login=WORKER):
        number = len(self.prs) + 1
        self.prs[number] = {
            "number": number,
            "state": state,
            "merged": False,
            "merged_by": None,
            "user": {"login": login},
            "head": {"ref": head, "sha": self.branches.get(head, self._new_sha())},
            "base": {"ref": self._default},
            "mergeable_state": "clean",
            "title": "seeded",
            "body": "",
        }
        self.comments.setdefault(number, [])
        return number

    def merge_as(self, number, login):
        """Simulate the sandbox merge gate landing the PR as `login`
        (`gh pr merge --delete-branch` semantics: the head branch goes)."""
        pr = self.prs[number]
        head = pr["head"]["ref"]
        pr["head"]["sha"] = self.branches.get(head, pr["head"]["sha"])
        pr["merged"] = True
        pr["state"] = "closed"
        pr["merged_by"] = {"login": login}
        for (branch, path), content in list(self.files.items()):
            if branch == head:
                self.files[(self._default, path)] = content
        self.branches[self._default] = self._new_sha()
        self.branches.pop(head, None)

    def gate_update_branch(self, number, login=QA):
        """Simulate merge-gate's DRE-1924 update-branch: merge the base tip
        into the PR head as `login`; returns the new head (merge commit)."""
        pr = self.prs[number]
        head_ref = pr["head"]["ref"]
        old = self.branches[head_ref]
        sha = self._new_sha()
        self._record_commit(sha, [old, self.branches[self._default]], login)
        self.branches[head_ref] = sha
        return sha

    def post_verdict(self, number, token, sha, login=QA):
        self.comments.setdefault(number, []).append(
            {
                "user": {"login": login},
                "body": (
                    f"🔎 {merge_gate.CRITIC_MARKER} — VERDICT: {token} @{sha}"
                    "\n\n## Summary\nok"
                ),
            }
        )

    def post_human_wait(self, number, login=QA):
        """The merge gate's DRE-2039 status note, shaped as merge-gate.yml
        posts it (a status, deliberately NOT verdict-shaped)."""
        self.comments.setdefault(number, []).append(
            {
                "user": {"login": login},
                "body": (
                    "⏸️ Merge gate: waiting for human merge — dependabot PR "
                    "includes a semver-major update"
                ),
            }
        )

    # -- the client surface the driver consumes ----------------------------
    def default_branch(self, repo):
        return self._default, self.branches[self._default]

    def matching_refs(self, repo, prefix):
        return [b for b in sorted(self.branches) if b.startswith(prefix)]

    def create_ref(self, repo, branch, sha):
        if branch in self.branches:
            raise RuntimeError(f"ref exists: {branch}")
        self.branches[branch] = sha

    def delete_ref(self, repo, branch):
        return self.branches.pop(branch, None) is not None

    def put_file(self, repo, branch, path, content, message):
        if branch not in self.branches:
            # Real contents-API behavior: a push to a deleted branch (e.g.
            # merged out from under the harness) fails, not silently forks.
            raise RuntimeError(f"branch not found: {branch}")
        prev = self.branches[branch]
        self.files[(branch, path)] = content
        sha = self._new_sha()
        self._record_commit(sha, [prev], WORKER)
        self.branches[branch] = sha
        return sha

    def get_file_sha(self, repo, path, ref):
        return "blob" if (ref, path) in self.files else None

    def list_dir(self, repo, path, ref):
        prefix = path.rstrip("/") + "/"
        return [
            {"path": p, "type": "file"}
            for (branch, p) in sorted(self.files)
            if branch == ref and p.startswith(prefix)
        ]

    def delete_file(self, repo, branch, path, message):
        return self.files.pop((branch, path), None) is not None

    def create_pr(self, repo, head, base, title, body):
        number = self.seed_pr(head)
        pr = self.prs[number]
        pr["title"], pr["body"] = title, body
        pr["user"] = {"login": WORKER}
        if self.on_create_pr:
            self.on_create_pr(self, pr)
        return pr

    def get_pr(self, repo, number):
        if self.on_poll:
            self.on_poll(self)
        pr = self.prs[number]
        ref = pr["head"]["ref"]
        if pr["state"] == "open" and ref in self.branches:
            pr["head"]["sha"] = self.branches[ref]  # live head, like REST
        return pr

    def list_open_prs(self, repo):
        return [p for p in self.prs.values() if p["state"] == "open"]

    def close_pr(self, repo, number):
        self.prs[number]["state"] = "closed"

    def list_comments(self, repo, number):
        if self.on_poll:
            self.on_poll(self)
        return list(self.comments.get(number, []))

    def create_comment(self, repo, number, body):
        comment = {"user": {"login": WORKER}, "body": body}
        self.comments.setdefault(number, []).append(comment)
        return comment

    def get_commit(self, repo, sha):
        return self.commits[sha]

    def list_pr_commits(self, repo, number):
        return list(self.prs[number].get("commits_payload") or [])

    def list_check_runs(self, repo, sha):
        return list(self.check_runs.get(sha, []))


def _ctx(gh, run_id="gha-1-1"):
    faketime = _FakeTime()
    return framework.HarnessContext(
        gh=gh,
        repo="dreadnought-foundry/bureau-harness",
        run_id=run_id,
        worker_login=WORKER,
        qa_login=QA,
        verdict_timeout=100,
        merge_timeout=100,
        poll_interval=1,
        clock=faketime.clock,
        sleep=faketime.sleep,
        log=lambda *_: None,
    )


class _FakeTime:
    def __init__(self):
        self.now = 0.0

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def _happy_pipeline(fake, pr):
    """The sandbox pipeline's happy path: sha-bound APPROVE, qa-bot merge."""
    fake.post_verdict(pr["number"], "APPROVE", pr["head"]["sha"])
    fake.merge_as(pr["number"], QA)


class DiscoveryTest(unittest.TestCase):
    def test_bot_pr_flow_is_discovered_by_convention(self):
        # Convention-based discovery (glob over scenarios/) — sibling cards
        # add dependabot_flow / gate_paths as NEW FILES, no shared registry.
        found = scenarios.discover()
        self.assertIn("bot_pr_flow", found)


class ContentSafetyTest(unittest.TestCase):
    def test_probe_artifacts_carry_the_run_id(self):
        self.assertIn("gha-9-9", bot_pr_flow.probe_path("gha-9-9"))
        self.assertIn("gha-9-9", bot_pr_flow.probe_markdown("gha-9-9"))
        self.assertIn("gha-9-9", bot_pr_flow.pr_title("gha-9-9"))
        self.assertIn("gha-9-9", bot_pr_flow.pr_body("gha-9-9"))

    def test_nothing_the_harness_posts_contains_a_verdict_marker(self):
        # Verdict-shaped text IS an approval credential (untrusted-content
        # standard) — the harness must never emit one anywhere it writes.
        for text in (
            bot_pr_flow.probe_markdown("gha-9-9"),
            bot_pr_flow.pr_title("gha-9-9"),
            bot_pr_flow.pr_body("gha-9-9"),
        ):
            for marker in ("VERDICT:", "QA Critic", "QA Verifier"):
                self.assertNotIn(marker, text)

    def test_branch_carries_no_card_reference(self):
        # The Linear-side decision: a cardless agent/ branch is reviewed by
        # should_review_pr.py yet every Linear touchpoint no-ops. A DRE-n in
        # the branch would make qa-review comment on a REAL card.
        import should_review_pr

        branch = framework.scenario_branch("gha-1-1", bot_pr_flow.SCENARIO.name)
        self.assertTrue(should_review_pr.should_review(branch))
        self.assertIsNone(should_review_pr.card_in_branch(branch))


class HappyPathTest(unittest.TestCase):
    def test_full_flow_passes_and_leaves_the_sandbox_clean(self):
        gh = FakeGitHub()
        gh.on_create_pr = _happy_pipeline
        result = framework.run_scenario(bot_pr_flow.SCENARIO, _ctx(gh))

        self.assertTrue(result.ok, result.errors)
        # Exactly one PR, authored on the namespaced branch, now merged.
        (pr,) = gh.prs.values()
        self.assertTrue(framework.is_harness_ref(pr["head"]["ref"]))
        self.assertTrue(pr["merged"])
        # Cleanup: harness branch gone, probe file gone from the default
        # branch, no open harness PRs left behind.
        self.assertEqual(gh.matching_refs("x", framework.HARNESS_BRANCH_PREFIX), [])
        self.assertEqual(gh.list_dir("x", bot_pr_flow.PROBE_DIR, "main"), [])
        self.assertEqual(gh.list_open_prs("x"), [])

    def test_run_after_simulated_crash_still_passes(self):
        # Acceptance criterion: a previous run died mid-flight leaving a
        # branch, an open PR, and a merged probe file. The next run must
        # sweep them and pass.
        gh = FakeGitHub()
        stale_branch = "agent/harness-crashed-run-bot_pr_flow"
        gh.branches[stale_branch] = gh._new_sha()
        gh.seed_pr(head=stale_branch)
        gh.files[("main", bot_pr_flow.probe_path("crashed-run"))] = "stale"

        gh.on_create_pr = _happy_pipeline
        result = framework.run_scenario(bot_pr_flow.SCENARIO, _ctx(gh, "gha-2-1"))

        self.assertTrue(result.ok, result.errors)
        self.assertNotIn(stale_branch, gh.branches)
        self.assertNotIn(("main", bot_pr_flow.probe_path("crashed-run")), gh.files)
        self.assertEqual(gh.list_open_prs("x"), [])


class FailureModeTest(unittest.TestCase):
    def test_request_changes_fails_fast_with_the_verdict_named(self):
        gh = FakeGitHub()
        gh.on_create_pr = lambda fake, pr: fake.post_verdict(
            pr["number"], "REQUEST_CHANGES", pr["head"]["sha"]
        )
        result = framework.run_scenario(bot_pr_flow.SCENARIO, _ctx(gh))
        self.assertFalse(result.ok)
        self.assertEqual(result.failed_phase, "verify")
        self.assertIn("REQUEST_CHANGES", "\n".join(result.errors))

    def test_stale_bound_verdict_never_satisfies_verify(self):
        gh = FakeGitHub()
        gh.on_create_pr = lambda fake, pr: fake.post_verdict(
            pr["number"], "APPROVE", "f" * 40  # bound to a sha that is not the head
        )
        result = framework.run_scenario(bot_pr_flow.SCENARIO, _ctx(gh))
        self.assertFalse(result.ok)
        self.assertEqual(result.failed_phase, "verify")

    def test_merge_by_the_wrong_identity_is_a_failure(self):
        # Author ≠ merger is the two-robot safety property — a merge by the
        # worker bot means the gate's identity separation broke.
        def wrong_merger(fake, pr):
            fake.post_verdict(pr["number"], "APPROVE", pr["head"]["sha"])
            fake.merge_as(pr["number"], WORKER)

        gh = FakeGitHub()
        gh.on_create_pr = wrong_merger
        result = framework.run_scenario(bot_pr_flow.SCENARIO, _ctx(gh))
        self.assertFalse(result.ok)
        self.assertEqual(result.failed_phase, "verify")
        self.assertIn("agent-bureau-bot", "\n".join(result.errors))

    def test_even_a_failed_run_cleans_up_its_branch(self):
        gh = FakeGitHub()  # no hook: no verdict ever appears → verify times out
        result = framework.run_scenario(bot_pr_flow.SCENARIO, _ctx(gh))
        self.assertFalse(result.ok)
        self.assertEqual(gh.matching_refs("x", framework.HARNESS_BRANCH_PREFIX), [])
        self.assertEqual(gh.list_open_prs("x"), [])


if __name__ == "__main__":
    unittest.main()
