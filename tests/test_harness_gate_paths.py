"""RED-first tests for the gate_paths harness scenario (DRE-2100).

gate_paths proves the merge gate's semantics against real GitHub with
three synthesized PRs plus an opportunistic look at the real Dependabot
PR:

  * NO RE-MERGE (DRE-2416): a behind-base PR is merged AT THE HEAD IT WAS
    REVIEWED AT — the gate never merges the base in, so a burst of merges
    costs no CI restarts on the open branches, and it never leaves a
    green, approved branch unmerged either;
  * HUMAN PATH (DRE-2039): a worker-authored PR on a dependabot-named
    branch is `human` — the gate posts the honest waiting-for-human state
    exactly ONCE and never touches the PR (no branch mutation, no merge);
  * VERDICT BINDING (DRE-1990): a push right after a bound APPROVE makes
    that verdict stale — the gate must NOT merge until a fresh verdict
    binds the new head;
  * REAL-PR POSTURE: when the sandbox's genuine Dependabot PR is
    observable, its gate arm (major/unprovable → human once + untouched;
    provable minor/patch + bound APPROVE → auto-merge) is asserted too;
  * GIVING UP HONESTLY (DRE-3453): the named leg's second wake is a critic
    comment on the probe, and both ways of never getting one now END the
    wait in minutes with the cause named — a verdict that is DELETED after
    it was posted (probe #1494, comment 5607416317), and a sandbox that is
    alive but doing nothing at all (probe #1407). Neither may cost the
    critic's full 4,200-second budget, and every give-up names the critic's
    published check on the head, whether a verdict exists now, and the
    critic's last run.

The LIVE scenario mocks nothing GitHub-side; this suite drives its LOGIC
against the shared FakeGitHub, with a per-leg driver standing in for the
sandbox's real critic + gate. The waiting-for-human marker the scenario
greps for is PINNED to merge-gate.yml's own literal.

These tests must FAIL before the scenario exists, and PASS after.
"""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import merge_gate  # noqa: E402
from harness import framework  # noqa: E402
from harness import sandbox_health  # noqa: E402
from harness import scenarios  # noqa: E402
from harness.scenarios import gate_paths  # noqa: E402
from test_harness_bot_pr_flow import QA, WORKER, FakeGitHub, _FakeTime  # noqa: E402

DEPENDABOT = "dependabot[bot]"
REAL_BRANCH = "dependabot/pip/requests-2.32.5"
MERGE_GATE_YML = (
    Path(__file__).resolve().parents[1] / ".github" / "workflows" / "merge-gate.yml"
)


#: A wall-clock instant for the idle-sandbox replay. The liveness probe reads
#: Actions run timestamps, which are epoch-shaped — the monotonic fake clock
#: above cannot stand in for them.
WALL_NOW = 1_757_000_000.0

def _ctx(gh, run_id="gha-1-1", namespace=framework.DEFAULT_NAMESPACE,
         faketime=None, sandbox_probe=None, wait_deadline=None,
         verdict_timeout=100, merge_timeout=100):
    # Composed the way __main__ composes it: the namespace OPENS the run id,
    # so the run's branches sit in the slice its own sweep owns (DRE-3075).
    run_id = framework.namespaced_run_id(namespace, run_id)
    faketime = faketime or _FakeTime()
    return framework.HarnessContext(
        gh=gh,
        gh_qa=gh,
        repo="dreadnought-foundry/bureau-harness",
        run_id=run_id,
        namespace=namespace,
        worker_login=WORKER,
        qa_login=QA,
        verdict_timeout=verdict_timeout,
        merge_timeout=merge_timeout,
        poll_interval=1,
        clock=faketime.clock,
        sleep=faketime.sleep,
        wall_clock=lambda: WALL_NOW,
        wait_deadline=(
            framework.WAIT_DEADLINE_SECONDS if wait_deadline is None
            else wait_deadline
        ),
        sandbox_probe=sandbox_probe,
        log=lambda *_: None,
    )


def _find(gh, fragment):
    """The NEWEST PR whose head ref contains `fragment` (driver-side
    lookup — a swept crashed leftover may share the fragment)."""
    matches = [pr for pr in gh.prs.values() if fragment in pr["head"]["ref"]]
    return matches[-1] if matches else None


class LegDriver:
    """Stands in for the sandbox's critic + merge gate, one state machine
    per leg. Modes select the behavior under test; 'happy' is what the
    real pipeline promises."""

    def __init__(self, stale="happy", skew="happy", named="happy", real=None):
        self.modes = {"stale": stale, "skew": skew, "named": named, "real": real}
        self.state = {}

    def __call__(self, gh):
        self._drive_named(gh)
        self._drive_stale(gh)
        self._drive_skew(gh)
        self._drive_real(gh)

    # -- named: gate says human, once, hands off --------------------------
    def _drive_named(self, gh):
        mode = self.modes["named"]
        pr = _find(gh, "-named")
        if pr is None or pr["state"] != "open":
            return
        n = pr["number"]
        if not self.state.get("named_wait"):
            gh.post_human_wait(n)
            if mode == "spam":
                gh.post_human_wait(n)
            self.state["named_wait"] = True
            return
        if not self.state.get("named_verdict"):
            if mode == "swept":
                # NOT the pipeline: a concurrent harness run whose driver
                # predates the namespaced sweep (DRE-3075) closes this
                # run's live probe PR and deletes its branch. No critic
                # comment can ever arrive on it now.
                gh.close_pr("x", n)
                gh.branches.pop(pr["head"]["ref"], None)
                self.state["named_verdict"] = True
                return
            if mode == "no_verdict":
                # Probe #1407's shape: the gate has held the PR, and nothing
                # else in the sandbox ever happens on it. The critic's comment
                # — the second gate wake — never arrives.
                return
            # The critic reviews dependabot/** branches too — its comment
            # is the second gate wake, and it publishes its head-bound check
            # alongside it (publish_review_check.py).
            if mode == "quote":
                # The critic reasoning out loud about the state it found:
                # its verdict PROSE quotes the gate's status line. Same
                # login as the note (one App, two steps), so only the
                # comment's shape separates the two. Posted through the
                # fake's own minter, so it carries an id like every other
                # comment — the deletion watch reads ids (DRE-3453) and a
                # fixture without one would be invisible to it.
                gh._add_comment(
                    n,
                    QA,
                    f"🔎 {merge_gate.CRITIC_MARKER} — VERDICT: "
                    f"REQUEST_CHANGES @{pr['head']['sha']}\n\n"
                    f"## Summary\nThe gate has already posted "
                    f"`{gate_paths.HUMAN_WAIT_MARKER}` here, so nothing "
                    "merges on my verdict.\n",
                )
            else:
                gh.post_verdict(n, "REQUEST_CHANGES", pr["head"]["sha"])
            gh.check_runs.setdefault(pr["head"]["sha"], []).append(
                {
                    "name": gate_paths.CRITIC_CHECK_NAME,
                    "status": "completed",
                    "conclusion": "failure",
                    "output": {"title": f"REQUEST_CHANGES @{pr['head']['sha']}"},
                }
            )
            self.state["named_verdict"] = True
            if mode == "touch":
                gh.gate_update_branch(n)
            elif mode == "merge":
                gh.merge_as(n, QA)

    # -- stale: APPROVE, then hold until a fresh bound verdict ------------
    def _drive_stale(self, gh):
        mode = self.modes["stale"]
        pr = _find(gh, "-stale")
        if pr is None or pr["merged"] or pr["state"] != "open":
            return
        n, ref = pr["number"], pr["head"]["ref"]
        if "stale_sha1" not in self.state:
            sha1 = gh.branches[ref]
            gh.post_verdict(n, "APPROVE", sha1)
            self.state["stale_sha1"] = sha1
            return
        sha1 = self.state["stale_sha1"]
        if mode == "insta_merge":
            # The gate wins the race: merge at sha1 before the harness's
            # push lands (branch deleted with it).
            gh.merge_as(n, QA)
            return
        if gh.branches.get(ref, sha1) == sha1:
            return  # waiting for the harness's stale-making push
        if mode == "merge_stale":
            # THE regression: merging on the stale APPROVE after a push.
            gh.merge_as(n, QA)
            return
        if not self.state.get("stale_verdict2"):
            gh.post_verdict(n, "APPROVE", gh.branches[ref])
            self.state["stale_verdict2"] = True
            return
        gh.merge_as(n, QA)

    # -- skew: behind base, merged untouched (DRE-2416) -------------------
    def _drive_skew(self, gh):
        mode = self.modes["skew"]
        pr = _find(gh, "-skew")
        if pr is None or pr["merged"] or pr["state"] != "open":
            return
        n, ref = pr["number"], pr["head"]["ref"]
        if mode == "remerge":
            # The pre-DRE-2416 gate: merge the base in first. Every such
            # push restarted the full CI suite on unchanged source.
            gh.gate_update_branch(n)
            self.modes["skew"] = "happy"
            return
        if mode == "no_verdict":
            return  # the review never binds this head — nothing to merge on
        if not self.state.get("skew_verdict"):
            gh.post_verdict(n, "APPROVE", gh.branches[ref])
            self.state["skew_verdict"] = True
            return
        if mode == "never_merges":
            return  # the starvation itself: green + approved, left sitting
        gh.merge_as(n, QA)

    # -- real dependabot PR: gate posture ---------------------------------
    def _drive_real(self, gh):
        if self.modes["real"] != "auto_merge":
            return
        pr = _find(gh, REAL_BRANCH)
        if pr is None or pr["merged"] or pr["state"] != "open":
            return
        # Only merge once the harness is actually watching (a bound
        # APPROVE was seeded); mirrors the gate acting on its own wake.
        gh.merge_as(pr["number"], QA)


def _run(driver=None, gh=None, faketime=None):
    gh = gh or FakeGitHub()
    gh.on_poll = driver or LegDriver()
    result = framework.run_scenario(
        gate_paths.SCENARIO, _ctx(gh, faketime=faketime)
    )
    return result, gh


def _seed_real_pr(gh, level, verdict, human_waits=1):
    sha = gh._new_sha()
    gh.branches[REAL_BRANCH] = sha
    gh._record_commit(sha, ["0" * 40], DEPENDABOT)
    number = gh.seed_pr(head=REAL_BRANCH, login=DEPENDABOT)
    gh.prs[number]["commits_payload"] = [
        {
            "sha": sha,
            "commit": {
                "message": f"bump\n\nupdate-type: version-update:semver-{level}"
            },
        }
    ]
    for _ in range(human_waits):
        gh.post_human_wait(number)
    if verdict:
        gh.post_verdict(number, verdict, sha)
    return number, sha


class DiscoveryTest(unittest.TestCase):
    def test_gate_paths_is_discovered_by_convention(self):
        self.assertIn("gate_paths", scenarios.discover())


class ContentSafetyTest(unittest.TestCase):
    def test_nothing_the_harness_posts_contains_a_gate_credential(self):
        # Verdict markers are approval credentials; the waiting-for-human
        # marker is the gate's idempotence key — emitting it would make
        # the gate think it already posted the state.
        texts = [gate_paths.base_advance_markdown("gha-9-9")]
        for leg in gate_paths.LEGS:
            texts += [
                gate_paths.probe_markdown("gha-9-9", leg),
                gate_paths.pr_title("gha-9-9", leg),
                gate_paths.pr_body("gha-9-9", leg),
            ]
        for text in texts:
            for marker in (
                "VERDICT:",
                "QA Critic",
                "QA Verifier",
                gate_paths.HUMAN_WAIT_MARKER,
            ):
                self.assertNotIn(marker, text)

    def test_probe_artifacts_carry_the_run_id(self):
        for leg in gate_paths.LEGS:
            self.assertIn("gha-9-9", gate_paths.probe_path("gha-9-9", leg))
            self.assertIn("gha-9-9", gate_paths.pr_title("gha-9-9", leg))
        self.assertIn("gha-9-9", gate_paths.base_advance_path("gha-9-9"))


class MarkerParityTest(unittest.TestCase):
    def test_human_wait_marker_is_merge_gates_own_literal(self):
        # The scenario greps for the exact string merge-gate.yml posts
        # (and uses as its own idempotence check) — drift here would make
        # the human-path assertions blind.
        self.assertIn(gate_paths.HUMAN_WAIT_MARKER, MERGE_GATE_YML.read_text())


class BranchShapeTest(unittest.TestCase):
    def test_legs_are_namespaced_and_sweepable(self):
        import should_review_pr

        for leg in ("skew", "stale"):
            branch = gate_paths.leg_branch("gha-1-1", leg)
            self.assertTrue(branch.startswith("agent/harness-"), branch)
            self.assertTrue(framework.is_harness_ref(branch))
            self.assertTrue(should_review_pr.should_review(branch))
            self.assertIsNone(should_review_pr.card_in_branch(branch))
        named = gate_paths.leg_branch("gha-1-1", "named")
        # dependabot-named so merge_gate condition D applies — but inside
        # the harness namespace so the sweep owns it and the genuine-PR
        # detector never mistakes it for Dependabot's.
        self.assertTrue(named.startswith("dependabot/harness-"), named)
        self.assertTrue(framework.is_harness_ref(named))


class HappyPathTest(unittest.TestCase):
    def test_full_flow_passes_and_leaves_the_sandbox_clean(self):
        result, gh = _run()
        self.assertTrue(result.ok, result.errors)

        skew, stale, named = (
            _find(gh, "-skew"), _find(gh, "-stale"), _find(gh, "-named"),
        )
        # Skew and stale merged by the qa-bot; named honestly parked, then
        # closed (not merged) by cleanup.
        for pr in (skew, stale):
            self.assertTrue(pr["merged"])
            self.assertEqual(pr["merged_by"]["login"], QA)
        self.assertFalse(named["merged"])
        self.assertEqual(named["state"], "closed")
        # Everything namespaced is gone: branches, probe files, the
        # base-advance file — and no open harness PRs remain.
        for prefix in framework.HARNESS_BRANCH_PREFIXES:
            self.assertEqual(gh.matching_refs("x", prefix), [])
        leftovers = [
            p for (branch, p) in gh.files
            if branch == "main" and p.startswith(framework.PROBE_DIR)
        ]
        self.assertEqual(leftovers, [])
        self.assertEqual(gh.list_open_prs("x"), [])

    def test_run_after_simulated_crash_sweeps_dependabot_named_leftovers(self):
        gh = FakeGitHub()
        # A previous run of THIS lane — same namespace, hence swept.
        stale_branch = "dependabot/harness-local-crashed-gate_paths-named"
        gh.branches[stale_branch] = gh._new_sha()
        gh.seed_pr(head=stale_branch)
        result, gh = _run(gh=gh)
        self.assertTrue(result.ok, result.errors)
        self.assertNotIn(stale_branch, gh.branches)


class StaleVerdictTest(unittest.TestCase):
    def test_merging_on_the_stale_approve_is_the_named_failure(self):
        result, _ = _run(LegDriver(stale="merge_stale"))
        self.assertFalse(result.ok)
        self.assertEqual(result.failed_phase, "verify")
        self.assertIn("stale", "\n".join(result.errors).lower())

    def test_gate_winning_the_race_fails_honestly_not_silently(self):
        # If the gate merges before the stale-making push lands, the
        # binding property was NOT exercised — the scenario must say so,
        # never pass on pretend coverage.
        result, _ = _run(LegDriver(stale="insta_merge"))
        self.assertFalse(result.ok)
        self.assertIn("race", "\n".join(result.errors).lower())


class NoRemergeTest(unittest.TestCase):
    """DRE-2416, both directions of the bug the skew leg now guards."""

    def test_re_merging_the_behind_pr_is_a_failure(self):
        # The pre-DRE-2416 behaviour: the gate merges the base in before
        # merging the PR. Every such push cost a full CI suite on
        # unchanged source and, on a busy base, starved the branch.
        result, _ = _run(LegDriver(skew="remerge"))
        self.assertFalse(result.ok)
        self.assertEqual(result.failed_phase, "verify")
        self.assertIn("re-merge", "\n".join(result.errors).lower())

    def test_never_merging_the_behind_pr_times_out_as_a_failure(self):
        # The starvation itself: green, approved, merely behind — and left
        # sitting. The scenario must name it rather than time out mutely.
        result, _ = _run(LegDriver(skew="never_merges"))
        self.assertFalse(result.ok)
        self.assertEqual(result.failed_phase, "verify")
        self.assertIn("starvation", "\n".join(result.errors).lower())

    def test_merging_without_a_bound_verdict_is_a_failure(self):
        # Freshness stopped being a gate; the SHA-bound APPROVE did not.
        result, _ = _run(LegDriver(skew="no_verdict"))
        self.assertFalse(result.ok)
        self.assertEqual(result.failed_phase, "verify")


class HumanPathTest(unittest.TestCase):
    def test_spammed_waiting_state_is_a_failure(self):
        result, _ = _run(LegDriver(named="spam"))
        self.assertFalse(result.ok)
        self.assertIn("once", "\n".join(result.errors).lower())

    def test_touching_the_named_pr_is_a_failure(self):
        result, _ = _run(LegDriver(named="touch"))
        self.assertFalse(result.ok)
        self.assertIn("touch", "\n".join(result.errors).lower())

    def test_merging_the_named_pr_is_a_failure(self):
        result, _ = _run(LegDriver(named="merge"))
        self.assertFalse(result.ok)
        self.assertEqual(result.failed_phase, "verify")

    def test_a_swept_named_pr_fails_at_once_naming_the_closure(self):
        # Run 33899093729 (red main). At 17:36 a concurrent PR harness run
        # — running the pre-DRE-3075 driver from its own head, so its sweep
        # was still unscoped — closed main's live probe PR #929 and deleted
        # its branch. Main's named leg then waited the full 4200s verdict
        # budget for a critic comment that could never come, and reported
        # `timed out … waiting for a critic comment`: the sandbox's critic
        # blamed for a PR that no longer existed, 70 minutes of a 76-minute
        # run spent, and no way to tell the cause from the log.
        #
        # The wait must END the moment its subject is gone, and say so.
        faketime = _FakeTime()
        result, _ = _run(LegDriver(named="swept"), faketime=faketime)
        self.assertFalse(result.ok)
        self.assertEqual(result.failed_phase, "verify")
        errors = "\n".join(result.errors).lower()
        self.assertIn("closed", errors)
        self.assertIn("sweep", errors)
        # Not "slow": nothing can ever arrive, so sitting out the verdict
        # budget only buys a timeout that names the wrong culprit.
        self.assertLess(faketime.now, _ctx(FakeGitHub()).verdict_timeout)

    def test_a_verdict_quoting_the_marker_is_not_a_second_waiting_state(self):
        """The once-only count separates the gate's note from the critic's
        verdict by SHAPE, because it cannot separate them by author: both
        are posted by the qa-bot. `_human_wait_comments` already promises a
        quoted marker "never satisfies (or spams) the assertion" — counting
        `MARKER in body` does not deliver it for the one author the filter
        admits, and the leg then reds with "2 waiting-for-human comments"
        against a gate that posted exactly one.

        This is the same read as the gate's own idempotence key
        (scripts/gate_note.py), and it has to move with it: fixing the gate
        so the quoting verdict SURVIVES is what puts a second
        marker-carrying comment on the PR in the first place.
        """
        result, _ = _run(LegDriver(named="quote"))
        self.assertTrue(result.ok, result.errors)

    def test_the_quoted_verdict_is_still_read_as_the_second_gate_wake(self):
        """Anti-vacuity for the test above: the quoting comment must still
        be the verdict the leg is waiting for, or the leg would pass by
        never seeing a second wake at all."""
        gh = FakeGitHub()
        ctx = _ctx(gh)
        head = "c" * 40
        quoting = {
            "user": {"login": QA},
            "body": (
                f"🔎 {merge_gate.CRITIC_MARKER} — VERDICT: APPROVE @{head}\n\n"
                f"## Summary\n`{gate_paths.HUMAN_WAIT_MARKER}` is already up.\n"
            ),
        }
        note = {
            "user": {"login": QA},
            "body": (
                f"⏸️ {gate_paths.HUMAN_WAIT_MARKER} — dependabot PR includes "
                "a semver-major update"
            ),
        }
        comments = [note, quoting]
        self.assertEqual(
            framework.verdict_state(comments, ctx.qa_login, head)[0], "APPROVE",
            "the quoting comment stopped reading as the critic's verdict",
        )
        waits = gate_paths._human_wait_comments(comments, ctx.qa_login)
        self.assertEqual(
            [c["body"] for c in waits], [note["body"]],
            "the critic's verdict was counted as a waiting-for-human note",
        )

    def test_a_verdict_that_is_posted_and_KEPT_passes_unchanged(self):
        # Probe #1498's shape — the run that passed in nine minutes on the
        # same PR. The deletion watch (DRE-3453) must cost this path nothing.
        gh = FakeGitHub()
        result, gh = _run(LegDriver(), gh=gh)
        self.assertTrue(result.ok, result.errors)
        named = _find(gh, "-named")
        verdicts = [
            c for c in gh.comments[named["number"]]
            if "VERDICT:" in (c.get("body") or "")
        ]
        self.assertEqual(len(verdicts), 1, "the critic's verdict must survive")


class DeletedVerdictSandbox(FakeGitHub):
    """The sandbox of probe #1494 (DRE-3453): the critic's verdict on the
    named leg is posted, read ONCE, and then deleted out from under the wait.

    The deletion is keyed on the read rather than on a poll count so the
    replay stays exact if the scenario's polling changes: the named PR's
    comments are read by nothing but the named leg's own two waits.
    """

    def __init__(self):
        super().__init__()
        self.deleted_id = None
        self._reads = 0

    def list_comments(self, repo, number):
        comments = super().list_comments(repo, number)
        ref = ((self.prs.get(number) or {}).get("head") or {}).get("ref", "")
        if "-named" not in ref:
            return comments
        verdict = next(
            (c for c in comments if "VERDICT:" in (c.get("body") or "")), None
        )
        if verdict is None:
            return comments
        self._reads += 1
        if self._reads >= 2:  # observed once, gone by the next poll
            self.delete_comment(number, verdict["id"])
            self.deleted_id = verdict["id"]
            comments = [c for c in comments if c["id"] != verdict["id"]]
        return comments


def _idle_sandbox_probe(asked):
    """A liveness probe over a sandbox that is ALIVE (nothing has failed) and
    IDLE (its newest run predates this wait). Shaped as sandbox_health.probe's
    callable, which is what wires the two facts together in production."""

    def ask(description, elapsed):
        asked.append(elapsed)
        return sandbox_health.ProbeReport(
            quote=None, newest_run_at=WALL_NOW - 900.0
        )

    return ask


class DeletedVerdictTest(unittest.TestCase):
    """DRE-3453 — a verdict that is posted and then deleted is a SENTENCE.

    2026-09-09, PR #329 attempts 1 and 2 (runs 34385950560): the critic posted
    `REQUEST_CHANGES @d3155ae8` on probe #1494 at 12:20:30 PT as comment
    5607416317, and by 13:38 that comment answered 404. `poll_critic` reads
    `verdict_state`, which is a question about the comments that exist NOW, so
    a deleted verdict is indistinguishable from one that has not been written
    — and the leg waited out its whole 4,200-second budget, twice, reporting a
    timeout against a critic that had done its job.
    """

    def setUp(self):
        self.faketime = _FakeTime()
        self.gh = DeletedVerdictSandbox()
        self.gh.workflow_runs = [
            {
                "id": 34385950560,
                "name": "QA Review (reusable)",
                "path": ".github/workflows/qa-review.yml",
                "status": "completed",
                "conclusion": "success",
                "updated_at": "2026-09-09T19:22:00Z",
            }
        ]
        self.result, _ = _run(
            LegDriver(named="happy"), gh=self.gh, faketime=self.faketime
        )
        self.errors = "\n".join(self.result.errors)

    def test_the_deletion_is_named_with_the_comment_id(self):
        self.assertFalse(self.result.ok)
        self.assertEqual(self.result.failed_phase, "verify")
        self.assertIsNotNone(self.gh.deleted_id, "the replay never deleted")
        self.assertIn("deleted after it was posted", self.errors)
        self.assertIn(str(self.gh.deleted_id), self.errors)

    def test_the_last_known_verdict_line_is_reported(self):
        self.assertIn("REQUEST_CHANGES", self.errors)

    def test_it_fails_within_a_poll_interval_not_at_the_verdict_budget(self):
        # The whole card: a deletion costs one poll, never seventy minutes.
        self.assertLess(self.faketime.now, _ctx(FakeGitHub()).verdict_timeout)

    def test_the_give_up_message_names_what_the_scenario_can_see(self):
        # The critic's published check on h1, whether a verdict comment for h1
        # exists now, and the critic's last run — the three facts that turn
        # this run's last line into the diagnosis.
        self.assertIn(gate_paths.CRITIC_CHECK_NAME, self.errors)
        self.assertIn("verdict comment for", self.errors)
        self.assertIn("qa-review", self.errors)


class IdleSandboxTest(unittest.TestCase):
    """DRE-3453 — a wait gives up on an IDLE sandbox after two probes.

    Probe #1407's shape and the 2026-09-07/08 hangs: the gate has posted its
    hold, the sandbox's machinery is healthy (so `sandbox_health` reports no
    failure and the pre-existing fail-fast stays silent), and nothing further
    is ever going to happen on this PR. The old behaviour was to sit out the
    critic's own 65-minute job budget and then blame the critic.
    """

    def setUp(self):
        self.faketime = _FakeTime()
        self.asked = []
        gh = FakeGitHub()
        gh.workflow_runs = [
            {
                "id": 34290739524,
                "name": "QA Review (reusable)",
                "path": ".github/workflows/qa-review.yml",
                "status": "completed",
                "conclusion": "success",
                "updated_at": "2026-09-08T17:10:00Z",
            }
        ]
        gh.on_poll = LegDriver(named="no_verdict")
        self.result = framework.run_scenario(
            gate_paths.SCENARIO,
            _ctx(
                gh,
                faketime=self.faketime,
                sandbox_probe=_idle_sandbox_probe(self.asked),
                verdict_timeout=framework.VERDICT_TIMEOUT_SECONDS,
                merge_timeout=framework.MERGE_TIMEOUT_SECONDS,
            ),
        )
        self.errors = "\n".join(self.result.errors)

    def test_it_gives_up_after_two_idle_probes_not_at_seventy_minutes(self):
        self.assertFalse(self.result.ok)
        self.assertEqual(self.result.failed_phase, "verify")
        self.assertEqual(
            len(self.asked), 2,
            f"two consecutive idle probes end the wait, got {self.asked}",
        )
        self.assertAlmostEqual(
            self.asked[-1], 2 * framework.WAIT_DEADLINE_SECONDS, delta=60.0
        )
        self.assertLess(self.faketime.now, framework.VERDICT_TIMEOUT_SECONDS)

    def test_it_says_the_sandbox_will_not_do_the_thing(self):
        self.assertIn("waiting for something the sandbox will not do", self.errors)
        self.assertIn("critic comment", self.errors)

    def test_a_healthy_idle_sandbox_is_not_reported_as_BLOCKED(self):
        # Nothing in the sandbox FAILED, so this is a statement about the
        # commit's PR, not a reason to stop the run and re-prove later.
        self.assertIsNone(self.result.blocked)

    def test_the_give_up_message_names_what_the_scenario_can_see(self):
        # No critic check was ever published on h1 here — the message must say
        # so by name rather than omitting the fact.
        self.assertIn(gate_paths.CRITIC_CHECK_NAME, self.errors)
        self.assertIn("verdict comment for", self.errors)
        self.assertIn("qa-review", self.errors)


class RealPrPostureTest(unittest.TestCase):
    def test_major_arm_untouched_with_one_waiting_state_passes(self):
        gh = FakeGitHub()
        _seed_real_pr(gh, "major", "REQUEST_CHANGES")
        result, gh = _run(gh=gh)
        self.assertTrue(result.ok, result.errors)
        real = _find(gh, REAL_BRANCH)
        self.assertEqual(real["state"], "open")  # never closed by cleanup

    def test_major_arm_with_spammed_waiting_state_fails(self):
        gh = FakeGitHub()
        _seed_real_pr(gh, "major", "REQUEST_CHANGES", human_waits=2)
        result, _ = _run(gh=gh)
        self.assertFalse(result.ok)

    def test_minor_arm_auto_merges_on_bound_approve(self):
        gh = FakeGitHub()
        _seed_real_pr(gh, "patch", "APPROVE", human_waits=0)
        result, gh = _run(LegDriver(real="auto_merge"), gh=gh)
        self.assertTrue(result.ok, result.errors)
        real = _find(gh, REAL_BRANCH)
        self.assertTrue(real["merged"])
        self.assertEqual(real["merged_by"]["login"], QA)

    def test_no_real_pr_degrades_to_a_note_not_a_failure(self):
        # dependabot_flow owns enforcing the real PR's existence; this
        # posture check is opportunistic and must not double-fail.
        result, _ = _run()
        self.assertTrue(result.ok, result.errors)


if __name__ == "__main__":
    unittest.main()
