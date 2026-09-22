"""A fix run that finishes after its pull request merged (DRE-4486).

THE RACE, and it has run four times:

  * **portico #611** ([DRE-4183](https://linear.app/dreadnoughtfoundry/issue/DRE-4183))
    merged 08:34:12Z by `agent-bureau-qa-bot`, 22 seconds after its own
    APPROVE; commit `9f1b7542` was pushed to the merged branch at 08:43:39Z,
    **nine minutes later**. That fix is on `origin`, reviewed and measured,
    and on no path to `main` — the live bug it was meant to fix is now
    [DRE-4460](https://linear.app/dreadnoughtfoundry/issue/DRE-4460): every
    photo portal's sign-in page draws two stacked veils on a phone.
  * **portico #351** ([DRE-2637](https://linear.app/dreadnoughtfoundry/issue/DRE-2637))
    merged 18:55:47Z, `72964abd` pushed at 19:00:04Z — **four minutes**.
    Harmless only by luck: later work redid the same fix.
  * **[DRE-2227](https://linear.app/dreadnoughtfoundry/issue/DRE-2227)**,
    recovered as [DRE-2989](https://linear.app/dreadnoughtfoundry/issue/DRE-2989)
    — *"stranded on its branch, and the card reads Done"*.
  * **[DRE-2591](https://linear.app/dreadnoughtfoundry/issue/DRE-2591)** —
    *"stranded a better version on a dead branch"*.

Each earlier occurrence was handled as a RECOVERY. None filed the
PREVENTION, which is why it recurred — so these tests pin the prevention,
in the three places the CEO's signed answer of 2026-09-21 put it:

  1. **The gate does not merge while a fix run is live for the pull
     request.** `merge_gate.evaluate_fix_lane` — condition F, between the
     verifier and the draft check. This is the half that closes the race;
     everything below is the belt to its braces.
  2. **A fix run that finds its pull request already merged does not push
     to that branch.** `stranded_fix.push_decision`, driven from a git
     `pre-push` hook agent-fix.yml installs into the PR checkout.
  3. **A merged pull request whose head branch carries commits dated after
     the merge is reported once, naming them.** `stranded_fix.detect`,
     swept by `reconcile.flag_stranded_fixes`.

The mutation check each rule owes: revert the rule and the test goes red.
`test_the_race_merges_today_without_condition_f` is that check written out
rather than promised — it drives the same inputs with the lane record
withheld (the pre-DRE-4486 caller) and asserts today's gate merges.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/portico")
os.environ.setdefault("REPO_SLUG", "portico")
os.environ.setdefault("GH_TOKEN", "x")

import merge_gate  # noqa: E402
import stranded_fix as sf  # noqa: E402

MODULE = ROOT / "scripts" / "stranded_fix.py"
MERGE_GATE_YML = ROOT / ".github" / "workflows" / "merge-gate.yml"
AGENT_FIX_YML = ROOT / ".github" / "workflows" / "agent-fix.yml"
RECONCILE = ROOT / "scripts" / "reconcile.py"
ADR = ROOT / "architecture" / "decisions" / "adr-stranded-fix-after-merge.md"

HEAD = "9f1b7542" + "0" * 32
QA = "agent-bureau-qa-bot[bot]"

MERGED_AT = "2026-08-11T08:34:12Z"
PUSHED_AT = "2026-08-11T08:43:39Z"


# --------------------------------------------------------------------------- #
# helpers — the payload shapes the callers actually hand these functions       #
# --------------------------------------------------------------------------- #


def approve(sha: str = HEAD) -> dict:
    return {"user": {"login": QA},
            "body": f"🔎 QA Critic — VERDICT: APPROVE @{sha}"}


def green_checks() -> list:
    return [{"name": "unit", "status": "completed", "conclusion": "success"}]


def lane_payload(**kw) -> dict:
    """The record merge-gate.yml writes with `stranded_fix.py lane`."""
    payload = {"readable": True, "runs": []}
    payload.update(kw)
    return payload


def run(run_id: int, status: str, *jobs: str) -> dict:
    return {"id": run_id, "status": status, "jobs": list(jobs)}


def commit(sha: str, date: str, message: str = "fix(DRE-4183): address review findings (attempt 1)") -> dict:
    return {"sha": sha,
            "commit": {"message": message, "committer": {"date": date}}}


def merged_pr(number: int = 611) -> dict:
    return {
        "number": number,
        "headRefName": "agent/DRE-4183-auth-gate-veil",
        "baseRefName": "main",
        "mergedAt": MERGED_AT,
        "url": f"https://github.com/dreadnought-foundry/portico/pull/{number}",
    }


# --------------------------------------------------------------------------- #
# 1. the gate: condition F                                                     #
# --------------------------------------------------------------------------- #


class GateRefusesWhileAFixRunIsLive(unittest.TestCase):
    """The diagnosis, run as a test: the merge gate asked every question about
    the head and never asked whether a fix run was still working on it."""

    def gate(self, **kw):
        base = dict(
            head_sha=HEAD,
            qa_login=QA,
            check_runs=green_checks(),
            comments=[approve()],
            pr_number=611,
        )
        base.update(kw)
        return merge_gate.decide(**base)

    def test_the_race_merges_today_without_condition_f(self):
        """THE MUTATION CHECK. Withhold the lane record — every caller that
        never passes it — and the gate merges exactly as it did on the
        morning of portico #611. If this ever stops merging, the test below
        is proving something other than condition F."""
        self.assertEqual(self.gate().action, "merge")

    def test_a_fix_run_in_flight_for_this_pr_blocks_the_merge(self):
        lane = sf.read_lane(lane_payload(
            runs=[run(33231413617, "in_progress", "fix PR #611")]))
        decision = self.gate(fix_lane=lane)
        self.assertEqual(decision.action, "wait")
        self.assertIn("33231413617", decision.reason)
        self.assertIn("#611", decision.reason)

    def test_a_queued_fix_run_for_this_pr_blocks_the_merge(self):
        """Queued counts. The DRE-2810 grouping leaves a verdict trigger
        PENDING behind a running one — it has not pushed yet, which is the
        whole hazard."""
        lane = sf.read_lane(lane_payload(
            runs=[run(1, "queued", "fix PR #611")]))
        self.assertEqual(self.gate(fix_lane=lane).action, "wait")

    def test_a_fix_run_on_another_pr_does_not_block_this_one(self):
        """No starvation. The fix lane serialises per pull request; a run on
        #612 says nothing about #611 (the DRE-2908 reading)."""
        lane = sf.read_lane(lane_payload(
            runs=[run(2, "in_progress", "fix PR #612")]))
        self.assertEqual(self.gate(fix_lane=lane).action, "merge")

    def test_an_unattributed_in_flight_run_blocks_the_merge(self):
        """GitHub lists ZERO jobs for a run cancelled or pending on its
        concurrency group, and a repo on a release tag older than DRE-2908
        names its job without the number. Unattributed is "could be any PR",
        never "not this one" — so it waits, and the reason says why."""
        lane = sf.read_lane(lane_payload(runs=[run(3, "queued")]))
        decision = self.gate(fix_lane=lane)
        self.assertEqual(decision.action, "wait")
        self.assertIn("unattributed", decision.reason.lower())

    def test_a_completed_fix_run_blocks_nothing(self):
        lane = sf.read_lane(lane_payload(
            runs=[run(4, "completed", "fix PR #611")]))
        self.assertEqual(self.gate(fix_lane=lane).action, "merge")

    def test_an_unreadable_lane_record_waits(self):
        """Fail closed, the direction every other record in this gate takes:
        a blip that read as "no fix run" is precisely the failure."""
        lane = sf.read_lane({"readable": False, "detail": "HTTP 403"})
        decision = self.gate(fix_lane=lane)
        self.assertEqual(decision.action, "wait")
        self.assertIn("could not", decision.reason.lower())

    def test_the_refusal_needs_the_pr_number_to_be_exact(self):
        """Without a PR number nothing can be attributed, so every in-flight
        run is unattributed and the gate waits — never merges."""
        lane = sf.read_lane(lane_payload(
            runs=[run(5, "in_progress", "fix PR #612")]))
        self.assertEqual(self.gate(fix_lane=lane, pr_number=None).action, "wait")

    def test_condition_f_runs_before_the_draft_note(self):
        """Condition 4's note claims "everything else is green, so marking it
        ready is all that is left". With a fix run in flight that is false,
        and the gate must not post it."""
        lane = sf.read_lane(lane_payload(
            runs=[run(6, "in_progress", "fix PR #611")]))
        self.assertEqual(self.gate(fix_lane=lane, is_draft=True).action, "wait")

    def test_a_conflicted_branch_still_reaches_the_fix_agent(self):
        """Condition 0 keeps its position: a live fix run must not stop the
        conflict route, or a DIRTY pull request with a queued fix run has
        nothing left that can move it."""
        lane = sf.read_lane(lane_payload(
            runs=[run(7, "queued", "fix PR #611")]))
        self.assertEqual(
            self.gate(fix_lane=lane, merge_state="DIRTY").action, "conflict")

    def test_a_standing_request_changes_still_holds(self):
        """`hold` is the more informative answer and it comes first — the
        fix run in flight is the one working that very verdict."""
        lane = sf.read_lane(lane_payload(
            runs=[run(8, "in_progress", "fix PR #611")]))
        comments = [{"user": {"login": QA},
                     "body": f"🔎 QA Critic — VERDICT: REQUEST_CHANGES @{HEAD}"}]
        self.assertEqual(
            self.gate(fix_lane=lane, comments=comments).action, "hold")


class LaneRecordReading(unittest.TestCase):
    def test_an_absent_record_is_no_lane_at_all(self):
        """`None` is the pre-DRE-4486 caller and must gate nothing."""
        self.assertIsNone(merge_gate.evaluate_fix_lane(None, 611))

    def test_a_shapeless_payload_reads_as_unreadable(self):
        for payload in ([], "nope", {"runs": "nope"}, {"readable": True}):
            with self.subTest(payload=payload):
                self.assertFalse(sf.read_lane(payload).readable)

    def test_statuses_github_uses_for_not_finished_yet(self):
        for status in ("queued", "in_progress", "requested", "waiting", "pending"):
            with self.subTest(status=status):
                lane = sf.read_lane(lane_payload(
                    runs=[run(9, status, "fix PR #611")]))
                self.assertIn(611, lane.by_pr)

    def test_the_pr_number_comes_from_the_job_name_one_reader(self):
        """`fix_concurrency.JOB_PR_PREFIX` is the producer's own expression.
        Read through that module, never re-derived here (DRE-2908)."""
        import fix_concurrency

        lane = sf.read_lane(lane_payload(
            runs=[run(10, "queued", f"{fix_concurrency.JOB_PR_PREFIX}611")]))
        self.assertEqual(lane.by_pr[611], 10)


class GateCliThreadsTheRecord(unittest.TestCase):
    def _files(self, tmp: Path, lane: dict) -> list:
        (tmp / "checks.json").write_text(json.dumps({"check_runs": green_checks()}))
        (tmp / "comments.json").write_text(json.dumps([approve()]))
        (tmp / "runs.json").write_text(json.dumps({"workflow_runs": []}))
        (tmp / "compare.json").write_text(json.dumps({}))
        (tmp / "lane.json").write_text(json.dumps(lane))
        return [
            "--head-sha", HEAD, "--qa-login", QA,
            "--check-runs-file", str(tmp / "checks.json"),
            "--comments-file", str(tmp / "comments.json"),
            "--workflow-runs-file", str(tmp / "runs.json"),
            "--compare-file", str(tmp / "compare.json"),
            "--fix-lane-file", str(tmp / "lane.json"),
            "--pr-number", "611",
        ]

    def test_cli_waits_on_a_live_fix_run(self):
        import contextlib
        import io
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            argv = self._files(Path(d), lane_payload(
                runs=[run(11, "in_progress", "fix PR #611")]))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(merge_gate.main(argv), 0)
            self.assertIn("decision=wait", out.getvalue())

    def test_cli_merges_with_an_idle_lane(self):
        import contextlib
        import io
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            argv = self._files(Path(d), lane_payload())
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(merge_gate.main(argv), 0)
            self.assertIn("decision=merge", out.getvalue())

    def test_an_unreadable_lane_file_is_not_an_idle_lane(self):
        """A file the workflow never wrote must not read as "nothing is
        running" — that is the fail-open direction this card closes."""
        import contextlib
        import io
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            argv = self._files(Path(d), lane_payload())
            argv[argv.index("--fix-lane-file") + 1] = str(Path(d) / "absent.json")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(merge_gate.main(argv), 0)
            self.assertIn("decision=wait", out.getvalue())


# --------------------------------------------------------------------------- #
# 2. the fix run: it does not push onto a merged branch                        #
# --------------------------------------------------------------------------- #


class PushDecision(unittest.TestCase):
    def test_a_merged_pull_request_refuses_the_push(self):
        action, reason = sf.push_decision({"state": "MERGED", "mergedAt": MERGED_AT})
        self.assertEqual(action, sf.REFUSE)
        self.assertIn("merged", reason.lower())

    def test_an_open_pull_request_pushes(self):
        action, _ = sf.push_decision({"state": "OPEN", "mergedAt": None})
        self.assertEqual(action, sf.PUSH)

    def test_a_closed_unmerged_pull_request_pushes(self):
        """Narrow on purpose: a closed pull request can be reopened, and the
        CEO's answer names the merged case. Widening it would refuse pushes
        nothing is wrong with."""
        action, _ = sf.push_decision({"state": "CLOSED", "mergedAt": None})
        self.assertEqual(action, sf.PUSH)

    def test_an_unreadable_record_allows_the_push_and_says_so(self):
        """FAIL OPEN, deliberately and uniquely in this card: this runs in a
        git hook on every push of every fix run, and a hook that refused on
        an API blip would break the loop it is protecting. The gate above is
        the rule; this is the belt, and the detector below is the net."""
        action, reason = sf.push_decision(None)
        self.assertEqual(action, sf.UNPROVEN)
        self.assertIn("could not", reason.lower())

    def test_the_hook_exits_non_zero_only_on_refuse(self):
        self.assertEqual(sf.hook_status(sf.REFUSE), 1)
        self.assertEqual(sf.hook_status(sf.PUSH), 0)
        self.assertEqual(sf.hook_status(sf.UNPROVEN), 0)


class AgentFixInstallsTheGuard(unittest.TestCase):
    def setUp(self):
        self.doc = yaml.safe_load(AGENT_FIX_YML.read_text())
        self.steps = self.doc["jobs"]["fix"]["steps"]

    def _step(self, name: str) -> dict:
        for step in self.steps:
            if step.get("name") == name:
                return step
        self.fail(f"agent-fix.yml has no step named {name!r}")

    def test_the_pre_push_hook_is_installed(self):
        step = self._step("Refuse a push onto a merged pull request")
        self.assertIn("pre-push", step["run"])
        self.assertIn("stranded_fix.py", step["run"])

    def test_it_is_installed_before_the_agent_runs(self):
        names = [s.get("name") for s in self.steps]
        self.assertLess(
            names.index("Refuse a push onto a merged pull request"),
            names.index("Fix"),
            "the hook must exist before the agent can push through it",
        )

    def test_it_is_installed_after_the_pr_checkout(self):
        names = [s.get("name") for s in self.steps]
        self.assertGreater(
            names.index("Refuse a push onto a merged pull request"),
            names.index("Checkout PR branch"),
            "a hook written before the checkout is overwritten by it",
        )

    def test_the_report_step_routes_a_stranded_fix_onward(self):
        report = self._step("Report")
        self.assertIn("stranded_fix.py", report["run"])
        self.assertIn("route", report["run"])


# --------------------------------------------------------------------------- #
# 3. the detector: a merged branch that carries commits dated after the merge  #
# --------------------------------------------------------------------------- #


class Detector(unittest.TestCase):
    def compare(self, *commits, ahead=None) -> dict:
        return {"ahead_by": len(commits) if ahead is None else ahead,
                "commits": list(commits)}

    def test_portico_611_is_detected(self):
        """The live case, with its real timestamps."""
        found = sf.detect(merged_pr(611),
                          self.compare(commit("9f1b7542" + "0" * 32, PUSHED_AT)))
        self.assertIsNotNone(found)
        self.assertEqual(found.pr, 611)
        self.assertEqual([c["sha"] for c in found.commits],
                         ["9f1b7542" + "0" * 32])

    def test_portico_351_is_detected(self):
        found = sf.detect(
            {"number": 351, "headRefName": "agent/DRE-2637-submit-lock",
             "baseRefName": "main", "mergedAt": "2026-07-04T18:55:47Z",
             "url": "https://github.com/dreadnought-foundry/portico/pull/351"},
            self.compare(commit("72964abd" + "0" * 32, "2026-07-04T19:00:04Z")),
        )
        self.assertIsNotNone(found)
        self.assertEqual(found.pr, 351)

    def test_an_unmerged_pull_request_is_not_this_class(self):
        pr = merged_pr()
        pr["mergedAt"] = None
        self.assertIsNone(sf.detect(pr, self.compare(commit("a" * 40, PUSHED_AT))))

    def test_a_branch_level_with_its_base_is_nothing(self):
        self.assertIsNone(sf.detect(merged_pr(), self.compare(ahead=0)))

    def test_commits_that_predate_the_merge_are_not_orphans(self):
        """The ordinary un-deleted branch of a SQUASHED merge is ahead of its
        base forever and nothing is wrong with it. Only a commit dated after
        the merge can have been pushed after it."""
        self.assertIsNone(sf.detect(
            merged_pr(),
            self.compare(commit("a" * 40, "2026-08-11T08:00:00Z")),
        ))

    def test_only_the_commits_after_the_merge_are_named(self):
        found = sf.detect(merged_pr(), self.compare(
            commit("a" * 40, "2026-08-11T08:00:00Z"),
            commit("9f1b7542" + "0" * 32, PUSHED_AT),
        ))
        self.assertEqual([c["sha"] for c in found.commits],
                         ["9f1b7542" + "0" * 32])

    def test_an_unreadable_compare_says_nothing(self):
        for payload in (None, {}, "nope", {"ahead_by": "many"}):
            with self.subTest(payload=payload):
                self.assertIsNone(sf.detect(merged_pr(), payload))

    def test_a_commit_with_no_date_is_never_an_orphan(self):
        """Unreadable is not evidence. A branch alarmed on a missing field
        would alarm on every one of them."""
        self.assertIsNone(sf.detect(
            merged_pr(),
            {"ahead_by": 1, "commits": [{"sha": "a" * 40, "commit": {}}]},
        ))

    def test_orphaned_is_exact_about_the_boundary(self):
        """A commit at the merge instant is IN the merge, not after it."""
        self.assertEqual(
            sf.orphaned([commit("a" * 40, MERGED_AT)], MERGED_AT), [])


class TheCardTheReportFiles(unittest.TestCase):
    def found(self):
        return sf.detect(merged_pr(611), {
            "ahead_by": 1,
            "commits": [commit("9f1b7542" + "0" * 32, PUSHED_AT)],
        })

    def test_the_title_is_stable_per_branch(self):
        title = sf.card_title("portico", "agent/DRE-4183-auth-gate-veil")
        self.assertEqual(title,
                         sf.card_title("portico", "agent/DRE-4183-auth-gate-veil"))
        self.assertIn("agent/DRE-4183-auth-gate-veil", title)

    def test_the_title_is_prefixed_with_the_repo_slug(self):
        """standards/card-quality.md: a `<slug>: …` title and the `repo:`
        label are one fact written twice, and the planner seams refuse the
        disagreement."""
        self.assertTrue(sf.card_title("portico", "b").startswith("portico: "))

    def test_two_branches_get_two_cards(self):
        self.assertNotEqual(sf.card_title("portico", "agent/DRE-1-a"),
                            sf.card_title("portico", "agent/DRE-2-b"))

    def test_the_body_names_the_orphaned_commits(self):
        body = sf.card_body(repo="dreadnought-foundry/portico",
                            stranded=self.found(), card="DRE-4183", pushed=True)
        self.assertIn("9f1b7542", body)
        self.assertIn("agent/DRE-4183-auth-gate-veil", body)
        self.assertIn("#611", body)
        self.assertIn(MERGED_AT, body)

    def test_the_body_carries_acceptance_criteria(self):
        """`linear_ops.py oneoff` runs the card through validate_card.py."""
        body = sf.card_body(repo="dreadnought-foundry/portico",
                            stranded=self.found(), card="DRE-4183", pushed=True)
        self.assertIn("## Acceptance criteria", body)
        self.assertIn("- [ ]", body)

    def test_the_body_says_whether_the_work_reached_origin(self):
        on_origin = sf.card_body(repo="dreadnought-foundry/portico",
                                 stranded=self.found(), card="DRE-4183",
                                 pushed=True)
        refused = sf.card_body(repo="dreadnought-foundry/portico",
                               stranded=self.found(), card="DRE-4183",
                               pushed=False)
        self.assertNotEqual(on_origin, refused)
        self.assertIn("origin", on_origin)

    def test_the_body_carries_no_verdict_marker(self):
        """standards/untrusted-content.md — verdict-shaped text is an
        approval credential and only the critic writes one."""
        body = sf.card_body(repo="dreadnought-foundry/portico",
                            stranded=self.found(), card="DRE-4183", pushed=True)
        for marker in ("VERDICT:", "QA Critic", "QA Verifier"):
            self.assertNotIn(marker, body)

    def test_the_slug_comes_from_the_repo_map(self):
        self.assertEqual(sf.slug_for_repo("dreadnought-foundry/portico"), "portico")
        self.assertEqual(
            sf.slug_for_repo("dreadnought-foundry/bureau-pipeline"), "bureau-pipeline")
        self.assertIsNone(sf.slug_for_repo("someone/else"))


class TheSweepReportsItOnce(unittest.TestCase):
    """AC3 run rather than asserted: the sweep files ONE card for a stranded
    branch, and the second sweep — fifteen minutes later, same finding —
    files nothing."""

    BRANCH = "agent/DRE-4183-auth-gate-veil"

    def sweep(self, *, existing=None, parked=False, branch_exists=True,
              commit_date=PUSHED_AT):
        import reconcile

        pr = {"number": 611, "headRefName": self.BRANCH, "baseRefName": "main",
              "mergedAt": MERGED_AT,
              "url": "https://github.com/dreadnought-foundry/portico/pull/611"}
        compare = json.dumps({
            "ahead_by": 1,
            "commits": [commit("9f1b7542" + "0" * 32, commit_date)],
        })
        filed = []
        parked_comment = (
            ["🧭 routing-verdict: PARKED — deliberately not built"]
            if parked else []
        )
        with mock.patch.multiple(
            reconcile,
            REPO="dreadnought-foundry/portico",
            merged_prs=mock.MagicMock(return_value=[pr]),
            card_branches=mock.MagicMock(return_value=(
                [{"name": self.BRANCH, "sha": "a" * 40}] if branch_exists else [])),
            gh=mock.MagicMock(return_value=compare),
        ), mock.patch.object(
            reconcile.linear_ops, "find_open", return_value=existing,
        ), mock.patch.object(
            reconcile.linear_ops, "comment_bodies", return_value=parked_comment,
        ), mock.patch.object(
            reconcile.linear_ops, "cmd_oneoff",
            side_effect=lambda title, path, *a: filed.append(
                (title, Path(path).read_text(), a)),
        ):
            reconcile.flag_stranded_fixes()
        return filed

    def test_a_stranded_fix_files_one_card_naming_the_commits(self):
        filed = self.sweep()
        self.assertEqual(len(filed), 1)
        title, body, flags = filed[0]
        self.assertIn(self.BRANCH, title)
        self.assertIn("9f1b7542", body)
        self.assertIn("repo:portico", flags)
        self.assertIn("agent:engineer", flags)

    def test_the_next_sweep_files_nothing(self):
        """`once` is the criterion. The idempotency key is the card title,
        which is keyed on the branch — so a SECOND branch that strands the
        same way still speaks."""
        self.assertEqual(self.sweep(existing="DRE-9999"), [])

    def test_a_deleted_branch_is_the_healthy_outcome(self):
        self.assertEqual(self.sweep(branch_exists=False), [])

    def test_a_branch_whose_commits_predate_the_merge_is_not_reported(self):
        self.assertEqual(self.sweep(commit_date="2026-08-11T08:00:00Z"), [])

    def test_a_parked_card_is_never_reported(self):
        self.assertEqual(self.sweep(parked=True), [])


class ReconcileSweepsForIt(unittest.TestCase):
    def test_the_sweep_is_registered_as_a_backstop(self):
        """Defined is not enough — an unregistered sweep never runs, which is
        the silence this whole card is about."""
        source = RECONCILE.read_text()
        self.assertIn("def flag_stranded_fixes(", source)
        # The backstop tuple is the one `flag_unlanded_work` is registered in.
        tail = source.split("            flag_unlanded_work,\n", 1)[1]
        registered = tail.split("            fix_approved_but_red,", 1)[0]
        self.assertIn("            flag_stranded_fixes,\n", registered)

    def test_the_sweep_files_the_card_through_the_one_composer(self):
        source = RECONCILE.read_text()
        self.assertIn("stranded_fix", source)


# --------------------------------------------------------------------------- #
# the record: this is a CLASS, not an incident                                 #
# --------------------------------------------------------------------------- #


class TheFourOccurrencesAreNamed(unittest.TestCase):
    """AC5. A reader who meets this code in six months has to be able to see
    that it was written against four events, not one — or the next recovery
    is filed as another incident and the prevention is written again."""

    OCCURRENCES = ("DRE-4183", "DRE-2637", "DRE-2227", "DRE-2591")

    def test_the_module_names_all_four(self):
        source = MODULE.read_text()
        for card in self.OCCURRENCES:
            with self.subTest(card=card):
                self.assertIn(card, source)

    def test_the_adr_names_all_four(self):
        source = ADR.read_text()
        for card in self.OCCURRENCES:
            with self.subTest(card=card):
                self.assertIn(card, source)

    def test_the_adr_records_the_diagnosis(self):
        """The card's first criterion: WHICH event let the pull request merge
        while its fix run was live — written down, and where it could not be
        established, said so."""
        source = ADR.read_text().lower()
        self.assertIn("diagnosis", source)
        self.assertIn("portico", source)

    def test_the_gate_condition_cites_the_card(self):
        self.assertIn("DRE-4486", (ROOT / "scripts" / "merge_gate.py").read_text())


if __name__ == "__main__":
    unittest.main()


# --------------------------------------------------------------------------- #
# DRE-4583: a fix stub absent BY DESIGN is an idle lane, never an unreadable   #
# one. Live, 2026-09-21/22: bureau-harness carries no agent-fix.yml, so the    #
# gate's listing 404s, `gather_lane` recorded `readable: false`, and every     #
# approved probe PR waited forever (gate runs 35693033395 / 35693426673:       #
# `decision=wait reason=the Agent Fix run listing could not be read (listing   #
# agent-fix.yml runs failed: HTTP 404: workflow agent-fix.yml not found on the #
# default branch …)`). The Integration Harness waits on exactly that merge, so #
# three runs on main hung 57–80 minutes each and `stable` stopped moving.      #
#                                                                              #
# The distinction is the DRE-2525 one reconcile._fix_runs_in_flight draws:     #
# absence is PROVED off the contents API — the file is listed or it is not —   #
# never inferred from gh's error text, and anything unprovable stays           #
# unreadable, fail-closed.                                                     #
# --------------------------------------------------------------------------- #

SANDBOX = "dreadnought-foundry/bureau-harness"
NOT_FOUND = (
    "HTTP 404: workflow agent-fix.yml not found on the default branch "
    f"(https://api.github.com/repos/{SANDBOX}/actions/workflows/agent-fix.yml)"
)
FORBIDDEN = "HTTP 403: Resource not accessible by integration"
WITH_STUB = ["agent-task.yml", "agent-fix.yml", "qa-review.yml", "merge-gate.yml"]
WITHOUT_STUB = ["agent-task.yml", "qa-review.yml", "merge-gate.yml", "reconcile.yml"]


def _sandbox_gh(listing=(None, NOT_FOUND), contents=None, calls=None):
    """A `stranded_fix._gh` that answers the run listing from `listing` and
    the contents read from `contents` — `(stdout, None)` or `(None, detail)`
    — and records every argv so a test can say what was asked."""
    if calls is None:
        calls = []

    def fake(args):
        calls.append(list(args))
        joined = " ".join(args)
        if args[:2] == ["run", "list"]:
            return listing
        if "/contents/" in joined:
            if contents is None:
                return json.dumps([{"name": n, "type": "file"} for n in WITHOUT_STUB]), None
            return contents
        raise AssertionError(f"unexpected gh call: {args}")

    return fake, calls


class AnAbsentStubIsAnIdleLane(unittest.TestCase):

    def _gather(self, **kw):
        fake, calls = _sandbox_gh(**kw)
        with mock.patch.object(sf, "_gh", side_effect=fake):
            return sf.gather_lane(SANDBOX, "agent-fix.yml"), calls

    def test_a_stub_provably_absent_from_the_default_branch_is_an_idle_lane(self):
        record, _ = self._gather()
        self.assertTrue(record["readable"], record)
        self.assertEqual(record["runs"], [])
        self.assertIn("agent-fix.yml", record.get("detail", ""))
        lane = sf.read_lane(record)
        self.assertTrue(lane.readable)
        self.assertIsNone(sf.lane_refusal(lane, 2382))
        decision = merge_gate.decide(
            head_sha=HEAD, qa_login=QA, check_runs=green_checks(),
            comments=[approve()], fix_lane=lane, pr_number=2382,
        )
        self.assertEqual(decision.action, "merge")

    def test_absence_is_proved_off_the_contents_api_never_the_error_text(self):
        # The SAME 404 wording, but the file IS on the default branch: that
        # is a read the token could not make, not a repo with no stub.
        record, _ = self._gather(
            contents=(json.dumps([{"name": n} for n in WITH_STUB]), None))
        self.assertFalse(record["readable"], record)
        self.assertIsNotNone(sf.lane_refusal(sf.read_lane(record), 2382))

    def test_a_permission_refusal_with_the_stub_present_still_waits(self):
        record, _ = self._gather(
            listing=(None, FORBIDDEN),
            contents=(json.dumps([{"name": n} for n in WITH_STUB]), None))
        self.assertFalse(record["readable"])

    def test_an_unprovable_absence_stays_unreadable(self):
        for contents in ((None, FORBIDDEN), ("", None), ("[]", None),
                         ("not json", None), ('{"message": "Not Found"}', None)):
            with self.subTest(contents=contents):
                record, _ = self._gather(contents=contents)
                self.assertFalse(record["readable"], record)
                self.assertIn("agent-fix.yml", record["detail"])

    def test_the_probe_reads_the_workflows_directory_once_and_only_on_failure(self):
        # A healthy listing never pays for the probe: one call, as today.
        _, calls = self._gather(listing=("[]", None))
        self.assertEqual([c[:2] for c in calls], [["run", "list"]])
        # A failed one pays exactly one contents read, on the default branch
        # (no ?ref=), and never touches the Actions API to prove absence.
        _, calls = self._gather()
        probes = [c for c in calls if any("/contents/" in a for a in c)]
        self.assertEqual(len(probes), 1, calls)
        endpoint = " ".join(probes[0])
        self.assertIn(f"repos/{SANDBOX}/contents/.github/workflows", endpoint)
        self.assertNotIn("?ref=", endpoint)
        self.assertNotIn("/actions/", endpoint)

    def test_every_fleet_repo_with_a_stub_is_untouched(self):
        # The self-host repo's stub is self-agent-fix.yml (fix_workflow); a
        # listing that succeeds there is read exactly as before this card.
        fake, calls = _sandbox_gh(listing=(json.dumps([]), None))
        with mock.patch.object(sf, "_gh", side_effect=fake):
            record = sf.gather_lane(sf.SELF_HOST_REPO, sf.fix_workflow(sf.SELF_HOST_REPO))
        self.assertEqual(record, {"readable": True, "runs": []})
        self.assertEqual(len(calls), 1)
