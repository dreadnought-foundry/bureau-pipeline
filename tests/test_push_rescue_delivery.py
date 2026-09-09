"""RED-first tests: a rescue that cannot push still DELIVERS (DRE-3262).

THE INCIDENT (2026-09-06, agent-bureau run 34045232203, card DRE-3165). The
build ran 93 minutes, finished 5/5 green with four commits on the runner, and
could not push — the job's start token had died at the hour, which DRE-3043
made survivable. The rescue step then ran with its OWN two fresh mints
(DRE-3098) and GitHub refused that push too: `RESCUE_PUSH_STATUS: 400`. So the
branch `agent/DRE-3165-console-release-train` never appeared.

What DID survive is the artifact the rescue wrote — `rescue-DRE-3165.patch`,
235 KB, 4 commits, 35 files. A person found it by hand at 11:05 PT
(`gh run download` → `git am -3` → push → PR). Nothing in the pipeline told
anyone it was there: the card's last two comments said "5/5 branch ready — the
push-rescue step delivers the branch and opens the PR" and then "Could not read
this card's PR state from GitHub (API error), so this run is NOT being recorded
as a dead agent". Without the hand step the card would have been rebuilt from
nothing — ~90 minutes and the run's cost again.

A rescued patch only a human knows about is the DRE-3043 loss with an extra
step. So the run's LAST step must do two things, and both are pinned below:

  1. SAY IT, in the grammar a reader and a machine can both use —
     `🚨 rescue-push-failed: status 400 on both mints — the work is in artifact
     rescue-DRE-n.patch on run <id>; nothing will open a PR until it is
     delivered`.
  2. HAND THE DELIVERY TO SOMETHING THAT CAN DO IT — a `deliver-rescue`
     workflow_dispatch carrying the run id, which downloads the artifact with a
     NEW job's own token, applies it on a fresh branch and opens the pull
     request. When the dispatch cannot be made the same comment says so, and
     the reconcile sweep re-checks off the artifact fact rather than the PR
     list (tests/test_check_agent_result_failed_delivery.py).

Run: cd bureau-pipeline && python3 -m pytest tests/test_push_rescue_delivery.py -v
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("GH_TOKEN", "x")

import deliver_rescue  # noqa: E402
import push_rescue  # noqa: E402

import test_platform_fault_scenario as report_harness  # noqa: E402

WORKFLOWS = ROOT / ".github" / "workflows"
AGENT_TASK = WORKFLOWS / "agent-task.yml"
DELIVER = WORKFLOWS / "deliver-rescue.yml"
DELIVER_STUB = WORKFLOWS / "self-deliver-rescue.yml"
MEDIC_STUB = WORKFLOWS / "self-medic.yml"

# The incident's own facts. The artifact name, the run id and the status are
# what the card must name, so they are spelled here rather than derived.
CARD = "DRE-3165"
RUN_ID = "34045232203"
ARTIFACT = "rescue-DRE-3165.patch"
REPO = "dreadnought-foundry/agent-bureau"

# The line the card asked for, verbatim. Pinned as a literal on purpose: it is
# the contract between the run that writes it, the human who reads it and the
# sweep that parses it, so a reword is a decision and not a typo.
EXPECTED_LINE = (
    "🚨 rescue-push-failed: status 400 on both mints — the work is in artifact "
    "rescue-DRE-3165.patch on run 34045232203; nothing will open a PR until it "
    "is delivered"
)


def _on(doc: dict) -> dict:
    on = doc.get("on", doc.get(True))
    return on if isinstance(on, dict) else {}


def _step(workflow: Path, name: str, job: str) -> dict:
    doc = yaml.safe_load(workflow.read_text())
    for step in doc["jobs"][job]["steps"]:
        if step.get("name") == name:
            return step
    raise AssertionError(f"{workflow.name} has no {name!r} step")


def report_step_source() -> str:
    src = AGENT_TASK.read_text()
    m = re.search(
        r"name:\s*Report result to Linear(.*?)(?:\n      - name:|\Z)", src, re.S
    )
    assert m, "'Report result to Linear' step not found"
    return m.group(1)


def report_code() -> str:
    """The Report step with its comment lines stripped — the comments quote the
    incident's own wording, which must not answer a source-level pin."""
    return "\n".join(
        line for line in report_step_source().splitlines()
        if not line.lstrip().startswith("#")
    )


# --------------------------------------------------------------------------
# 1. THE LINE — one grammar, written and parsed by one module
# --------------------------------------------------------------------------
class TheAnnouncement(unittest.TestCase):
    def test_it_is_the_line_the_card_asked_for(self):
        self.assertEqual(
            deliver_rescue.announcement(
                CARD, artifact=ARTIFACT, run_id=RUN_ID, status="400", mints=2
            ),
            EXPECTED_LINE,
        )

    def test_it_names_the_artifact_and_the_run(self):
        line = deliver_rescue.announcement(
            CARD, artifact=ARTIFACT, run_id=RUN_ID, status="400"
        )
        self.assertIn(ARTIFACT, line)
        self.assertIn(RUN_ID, line)

    def test_an_unreadable_status_is_not_invented(self):
        """`http_status` answers "" for a refusal it cannot name (DRE-3098) —
        the receipt must say so rather than print a number nobody saw."""
        line = deliver_rescue.announcement(
            CARD, artifact=ARTIFACT, run_id=RUN_ID, status=""
        )
        self.assertIn("no HTTP status", line)
        self.assertNotIn("status  ", line)

    def test_one_mint_is_not_reported_as_both(self):
        line = deliver_rescue.announcement(
            CARD, artifact=ARTIFACT, run_id=RUN_ID, status="400", mints=1
        )
        self.assertNotIn("both mints", line)

    def test_the_parser_reads_what_the_writer_wrote(self):
        """The sweep re-checks off this line, so the grammar and its parser can
        never drift: the round trip is the test."""
        marker = deliver_rescue.parse_marker(EXPECTED_LINE)
        self.assertIsNotNone(marker)
        self.assertEqual(marker.run_id, RUN_ID)
        self.assertEqual(marker.artifact, ARTIFACT)

    def test_ordinary_prose_is_not_a_marker(self):
        self.assertIsNone(deliver_rescue.parse_marker(
            "the rescue push failed and I downloaded rescue-DRE-3165.patch by hand"
        ))

    def test_a_delivered_card_has_no_pending_delivery(self):
        pending = deliver_rescue.pending_delivery([
            EXPECTED_LINE,
            f"🚚 {deliver_rescue.DELIVERED_TAG}: run {RUN_ID}'s artifact "
            f"{ARTIFACT} is now https://github.com/x/y/pull/9",
        ])
        self.assertIsNone(pending)

    def test_an_undelivered_card_has_one(self):
        pending = deliver_rescue.pending_delivery(["⏳ 5/5 done", EXPECTED_LINE])
        self.assertIsNotNone(pending)
        self.assertEqual(pending.run_id, RUN_ID)


# --------------------------------------------------------------------------
# 2. THE HAND-OFF — the dispatch, and the honesty when it cannot be made
# --------------------------------------------------------------------------
class TheHandoff(unittest.TestCase):
    def setUp(self):
        self.posted: list[str] = []
        self.calls: list[list[str]] = []

    def _run(self, code=0, err=""):
        def run(argv, **kw):
            self.calls.append(list(argv))
            return code, "", err
        return run

    def handoff(self, **kw):
        return deliver_rescue.handoff(
            CARD, repo=REPO, run_id=RUN_ID, artifact=ARTIFACT,
            status="400", mints=2, run=self._run(**kw),
            post=self.posted.append,
        )

    def test_the_card_gets_the_line_in_the_same_run(self):
        self.handoff()
        self.assertEqual(len(self.posted), 1, self.posted)
        self.assertIn(EXPECTED_LINE, self.posted[0])

    def test_the_delivery_is_dispatched_in_the_same_run(self):
        outcome = self.handoff()
        self.assertTrue(outcome.dispatched)
        dispatch = [c for c in self.calls if "workflow" in c and "run" in c]
        self.assertEqual(len(dispatch), 1, self.calls)
        argv = " ".join(dispatch[0])
        self.assertIn(deliver_rescue.delivery_workflow(REPO), argv)
        self.assertIn(f"run_id={RUN_ID}", argv)
        self.assertIn(f"card={CARD}", argv)
        self.assertIn(f"artifact={ARTIFACT}", argv)

    def test_the_dispatch_targets_the_repo_the_artifact_lives_in(self):
        self.handoff()
        argv = self.calls[0]
        self.assertIn("--repo", argv)
        self.assertEqual(argv[argv.index("--repo") + 1], REPO)

    def test_the_self_host_repo_dispatches_its_own_stub(self):
        """review_workflow()/fix_workflow()'s rule (DRE-2056): in
        bureau-pipeline the reusable filename is workflow_call-only and
        `gh workflow run` on it 422s, so the dispatch names the stub."""
        self.assertEqual(
            deliver_rescue.delivery_workflow("dreadnought-foundry/bureau-pipeline"),
            "self-deliver-rescue.yml",
        )
        self.assertEqual(deliver_rescue.delivery_workflow(REPO), "deliver-rescue.yml")

    def test_a_refused_dispatch_says_a_human_is_needed(self):
        outcome = self.handoff(code=1, err="HTTP 404: Not Found")
        self.assertFalse(outcome.dispatched)
        body = self.posted[0]
        self.assertIn(EXPECTED_LINE, body)
        self.assertIn("could not be dispatched", body.lower())
        self.assertIn(ARTIFACT, body)

    def test_the_card_is_told_even_when_the_dispatch_cannot_be_made(self):
        """The comment is the only channel that survives a repo with no stub —
        it must be posted whatever the dispatch answers."""
        self.handoff(code=1, err="HTTP 404")
        self.assertEqual(len(self.posted), 1)

    def test_it_quotes_githubs_own_refusal_when_the_rescue_captured_one(self):
        """AC 1's other half: the git stderr the incident's log held and the
        card never saw. Passed through, never re-derived."""
        stderr = "fatal: unable to access 'https://github.com/o/r/': The requested URL returned error: 400"
        outcome = deliver_rescue.handoff(
            CARD, repo=REPO, run_id=RUN_ID, artifact=ARTIFACT, status="400",
            mints=2, stderr=stderr, run=self._run(), post=self.posted.append,
        )
        self.assertTrue(outcome.dispatched)
        self.assertIn("returned error: 400", self.posted[0])

    def test_no_token_ever_reaches_the_card(self):
        """The stderr is git's, but it crosses into a comment — anything that
        looks like a credential is redacted before it is posted."""
        stderr = "remote: Invalid username or password ghs_aBcD1234567890aBcD1234567890aBcD12"
        deliver_rescue.handoff(
            CARD, repo=REPO, run_id=RUN_ID, artifact=ARTIFACT, status="400",
            mints=2, stderr=stderr, run=self._run(), post=self.posted.append,
        )
        self.assertNotIn("ghs_aBcD1234567890aBcD1234567890aBcD12", self.posted[0])


# --------------------------------------------------------------------------
# 3. THE DELIVERY — a new job's own token turns the artifact into a PR
# --------------------------------------------------------------------------
class TheDelivery(unittest.TestCase):
    """`deliver_rescue.deliver()` is what the follow-up run executes: it takes
    the downloaded patch and produces a branch and a pull request. Driven
    through the same injected `run` seam push_rescue.py uses."""

    def setUp(self):
        self.calls: list[list[str]] = []
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = os.path.join(self.tmp.name, ARTIFACT)
        with open(self.patch, "w", encoding="utf-8") as fh:
            fh.write("From 0000 Mon Sep 17 00:00:00 2001\nSubject: [PATCH] work\n")
        self.addCleanup(self.tmp.cleanup)

    def _run(self, failing=(), pr_json="[]"):
        def run(argv, **kw):
            self.calls.append(list(argv))
            joined = " ".join(argv)
            for needle in failing:
                if needle in joined:
                    return 1, "", f"refused: {needle}"
            if "pr" in argv and "list" in argv:
                return 0, pr_json, ""
            if "pr" in argv and "create" in argv:
                return 0, "https://github.com/o/r/pull/42\n", ""
            return 0, "", ""
        return run

    def deliver(self, **kw):
        return deliver_rescue.deliver(
            CARD, repo=REPO, run_id=RUN_ID, patch_dir=self.tmp.name,
            base="main", token="fresh-token", card_url="https://linear/DRE-3165",
            run=kw.pop("run", None) or self._run(**kw), post=lambda body: None,
        )

    def test_it_finds_the_downloaded_patch(self):
        self.assertEqual(deliver_rescue.patch_file(self.tmp.name), self.patch)

    def test_no_patch_is_not_a_crash(self):
        with tempfile.TemporaryDirectory() as empty:
            self.assertEqual(deliver_rescue.patch_file(empty), "")

    def test_the_branch_is_this_cards_own_and_says_it_was_rescued(self):
        branch = deliver_rescue.delivery_branch(CARD)
        self.assertTrue(branch.startswith(f"agent/{CARD}-"))
        self.assertIn("rescued", branch)

    def test_it_applies_the_patch_and_opens_the_pull_request(self):
        outcome = self.deliver()
        self.assertTrue(outcome.pushed, outcome.error)
        self.assertTrue(outcome.pr_opened, outcome.error)
        self.assertEqual(outcome.pr_url, "https://github.com/o/r/pull/42")
        self.assertTrue(any("am" in c for c in self.calls), self.calls)
        self.assertTrue(any("push" in c for c in self.calls), self.calls)

    def test_it_branches_off_the_base_and_never_off_the_runners_head(self):
        self.deliver()
        checkout = [c for c in self.calls if "checkout" in c or "switch" in c]
        self.assertTrue(checkout, self.calls)
        self.assertTrue(
            any("origin/main" in " ".join(c) for c in checkout),
            f"the delivery branch must start at origin/main: {checkout}",
        )

    def test_a_card_that_already_has_a_pull_request_is_left_alone(self):
        """Idempotent: the sweep may dispatch this twice, and a second PR for
        one card is worse than the bug it fixes (push_rescue.py's rule)."""
        outcome = self.deliver(pr_json=json.dumps(
            [{"url": "https://github.com/o/r/pull/7"}]))
        self.assertFalse(outcome.pr_opened)
        self.assertNotIn("create", [c[1] for c in self.calls if len(c) > 1])

    def test_a_plain_diff_is_applied_when_git_am_refuses_it(self):
        """`write_patch` falls back to `git diff` for a shallow clone, so the
        delivery must too — otherwise the fallback preserves work nothing can
        replay."""
        outcome = self.deliver(failing=("am",))
        self.assertTrue(outcome.pushed, outcome.error)
        self.assertTrue(any("apply" in c for c in self.calls), self.calls)

    def test_a_patch_that_cannot_be_applied_is_reported_not_swallowed(self):
        outcome = self.deliver(failing=("am", "apply"))
        self.assertFalse(outcome.pushed)
        self.assertTrue(outcome.error)

    def test_the_card_is_told_the_delivery_landed(self):
        posted: list[str] = []
        deliver_rescue.deliver(
            CARD, repo=REPO, run_id=RUN_ID, patch_dir=self.tmp.name,
            base="main", token="fresh-token", card_url="",
            run=self._run(), post=posted.append,
        )
        self.assertEqual(len(posted), 1, posted)
        self.assertIn(deliver_rescue.DELIVERED_TAG, posted[0])
        self.assertIn(RUN_ID, posted[0])

    def test_the_pull_request_body_says_who_opened_it_and_from_what(self):
        self.deliver()
        create = [c for c in self.calls if "create" in c][0]
        body = create[create.index("--body") + 1]
        self.assertIn(RUN_ID, body)
        self.assertIn(ARTIFACT, body)
        self.assertIn("not by the agent", body)
        # It knows a patch applied, and nothing more. The critic reads this.
        self.assertNotIn("the card is finished", body.lower())
        self.assertIn("asserts the card is complete", body)


# --------------------------------------------------------------------------
# 4. THE WIRING — the run's LAST step is where this happens
# --------------------------------------------------------------------------
class TheReportStepWiring(unittest.TestCase):
    def test_the_delivery_branch_exists(self):
        self.assertIn("deliver_rescue.py", report_code())

    def test_it_is_reached_before_the_unreadable_pr_state_branch(self):
        """The incident's own ordering bug: `PR_STATE=UNREADABLE` answered
        first and said "NOT being recorded as a dead agent", so nothing told
        anyone the rescue had failed."""
        code = report_code()
        self.assertLess(
            code.index("deliver_rescue.py"),
            code.index('"$PR_STATE" = "UNREADABLE"'),
            "a failed delivery must be answered before the failed PR read",
        )

    def test_the_step_is_handed_the_rescues_own_outputs(self):
        step = _step(AGENT_TASK, "Report result to Linear", "execute")
        env = step["env"]
        self.assertEqual(env["RESCUE_PUSHED"], "${{ steps.rescue.outputs.pushed }}")
        self.assertEqual(
            env["RESCUE_PUSH_STATUS"], "${{ steps.rescue.outputs.push_status }}"
        )
        self.assertIn("RESCUE_ARTIFACT", env)
        self.assertEqual(env["RESCUE_ERROR"], "${{ steps.rescue.outputs.error }}")

    def test_the_rescue_exports_the_refusal_it_read(self):
        """AC 1: the git stderr lived only in the run's log, which is where the
        cause of this incident's 400 still is. It leaves the step now."""
        self.assertIn("error=", "\n".join(
            push_rescue.output_lines(push_rescue.Outcome())))

    def test_a_failed_push_says_how_many_auth_headers_git_is_sending(self):
        """AC 1's diagnostic. A 400 has three candidate causes and the DRE-3165
        log distinguished none: a rejected credential, a branch protection, or
        MORE THAN ONE `AUTHORIZATION` header — which GitHub answers as a
        malformed request. `repoint_git_credential` can only unset the LOCAL
        scope, so the count and the origins are what tell them apart."""
        logged: list[str] = []

        def run(argv, **kw):
            joined = " ".join(argv)
            if "--show-origin" in argv:
                return 0, ("file:/home/runner/.gitconfig\tAUTHORIZATION: basic aaa\n"
                           "file:.git/config\tAUTHORIZATION: basic bbb\n"), ""
            if "push" in argv:
                return 1, "", "fatal: The requested URL returned error: 400"
            if "ls-remote" in argv:
                return 0, "deadbeef\trefs/heads/agent/DRE-3165-x\n", ""
            if "for-each-ref" in argv:
                return 0, "agent/DRE-3165-x\n", ""
            if "rev-parse" in argv:
                return 0, "cafe1234\n", ""
            if "rev-list" in argv:
                return 0, "4\n", ""
            if "format-patch" in argv:
                return 0, "patch\n", ""
            return 0, "", ""

        with tempfile.TemporaryDirectory() as td:
            out = push_rescue.rescue(
                CARD, REPO, "fresh-1", retry_token="fresh-2",
                patch_path=os.path.join(td, ARTIFACT), base="main",
                run=run, log=logged.append, stop_notes=(),
            )
        self.assertFalse(out.pushed)
        self.assertEqual(out.push_status, "400")
        joined = "\n".join(logged)
        self.assertIn("2 auth header(s)", joined)
        self.assertIn("/home/runner/.gitconfig", joined)
        # Origins, never values: the values are credentials.
        self.assertNotIn("basic aaa", joined)

    def test_the_exported_refusal_is_one_line(self):
        """`$GITHUB_OUTPUT` is key=value per line: a multi-line stderr would
        corrupt every output after it."""
        out = push_rescue.Outcome()
        out.error = "fatal: could not read\nsecond line\nthird"
        lines = push_rescue.output_lines(out)
        self.assertEqual(len([l for l in lines if l.startswith("error=")]), 1)
        for line in lines:
            self.assertNotIn("\n", line)
        self.assertIn("second line", [l for l in lines if l.startswith("error=")][0])


class TheDeliveryWorkflow(unittest.TestCase):
    def test_the_reusable_workflow_exists_and_is_dispatchable(self):
        doc = yaml.safe_load(DELIVER.read_text())
        self.assertIn("workflow_call", _on(doc))
        stub = yaml.safe_load(DELIVER_STUB.read_text())
        self.assertIn("workflow_dispatch", _on(stub))

    def test_it_takes_the_run_the_artifact_and_the_card(self):
        for doc, trigger in (
            (yaml.safe_load(DELIVER.read_text()), "workflow_call"),
            (yaml.safe_load(DELIVER_STUB.read_text()), "workflow_dispatch"),
        ):
            inputs = (_on(doc)[trigger] or {}).get("inputs") or {}
            for name in ("run_id", "card", "artifact"):
                self.assertIn(name, inputs, f"{trigger} must take {name}")

    def test_it_downloads_the_artifact_with_its_own_freshly_minted_token(self):
        src = DELIVER.read_text()
        self.assertIn("gh run download", src)
        self.assertIn("create-github-app-token", src)
        self.assertIn("deliver_rescue.py", src)

    def test_the_download_never_spends_the_dead_runs_credential(self):
        """The whole point of the follow-up: a NEW job's own token. Nothing in
        it may read a credential minted by the run that failed."""
        self.assertNotIn("steps.worker.outputs.token", DELIVER.read_text())

    def test_the_stub_calls_the_reusable_at_the_qualified_main_ref(self):
        job = next(iter(yaml.safe_load(DELIVER_STUB.read_text())["jobs"].values()))
        self.assertEqual(
            job.get("uses"),
            "dreadnought-foundry/bureau-pipeline/.github/workflows/"
            "deliver-rescue.yml@main",
        )
        self.assertEqual(job.get("secrets"), "inherit")

    def test_the_medic_watches_it(self):
        """DRE-2036: a runnable workflow nobody watches fails silently."""
        name = yaml.safe_load(DELIVER_STUB.read_text())["name"]
        watched = (_on(yaml.safe_load(MEDIC_STUB.read_text()))
                   ["workflow_run"]["workflows"])
        self.assertIn(name, watched)

    def test_the_pipeline_ref_is_threaded(self):
        """DRE-2026: a stub pinned to @vN must not run scripts from @main."""
        self.assertEqual(
            subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "check_pipeline_ref.py")],
                capture_output=True, text=True, cwd=ROOT,
            ).returncode, 0,
        )


# --------------------------------------------------------------------------
# 5. THE SCENARIO — the real Report step, executed
# --------------------------------------------------------------------------
# The run's own execution record: 93 minutes, green, no error. The delivery is
# the only thing that failed, which is what makes the incident's "NOT being
# recorded as a dead agent" comment so nearly right.
FINISHED_BUT_UNPUSHED = {
    "type": "result", "subtype": "success", "is_error": False,
    "num_turns": 96, "total_cost_usd": 21.7, "duration_ms": 5_580_000,
    "result": "5/5 green, four commits, and the push was refused.",
}

GH_STUB = '''#!/usr/bin/env python3
import json, os, sys
with open(os.environ["GH_STUB_LOG"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\\n")
sys.exit(int(os.environ.get("GH_STUB_EXIT", "0")))
'''


def run_report(td, **kw):
    """Drive the REAL 'Report result to Linear' block for a run whose rescue
    could not push. Extends the DRE-2931 harness rather than copying it."""
    # The same `bin` the harness's own git stub lives in, so it is already the
    # first thing on the step's PATH.
    binary = report_harness._git_stub(td)
    gh_log = os.path.join(td, "gh.jsonl")
    report_harness._executable(os.path.join(binary, "gh"), GH_STUB)
    # The artifact the step names must EXIST — `write_patch` can fail, and a
    # run that could not write one has nothing to hand to a delivery job.
    patch = os.path.join(td, "rescue.patch")
    if kw.get("patch", True):
        with open(patch, "w", encoding="utf-8") as fh:
            fh.write("From 0000\nSubject: [PATCH] the work\n")
    proc, journal = report_harness.run_report(
        td,
        # The DRE-3165 run's own shape: it finished cleanly after 93 minutes,
        # so `is_error` is false and it plainly STARTED (DRE-2931) — the failure
        # is entirely in the delivery.
        execution=FINISHED_BUT_UNPUSHED,
        claude_outcome="success",
        local_work="true",
        env_extra=dict(
            RESCUE_PUSH_STATUS=kw.get("status", "400"),
            RESCUE_PUSHED="false",
            RESCUE_ARTIFACT=ARTIFACT,
            RESCUE_PATCH=patch,
            RESCUE_ERROR=kw.get("stderr", ""),
            GH_STUB_LOG=gh_log,
            GH_STUB_EXIT=str(kw.get("gh_exit", 0)),
        ),
        card_pr_exit=kw.get("card_pr_exit", 3),
    )
    dispatches = [
        json.loads(line) for line in
        (open(gh_log, encoding="utf-8").read().splitlines()
         if os.path.exists(gh_log) else [])
    ]
    return proc, journal, dispatches


class FailedDeliveryScenario(unittest.TestCase):
    """The DRE-3165 run itself: work on the runner, both mints refused with
    400, and GitHub unable to answer whether a PR exists."""

    def report(self, **kw):
        with tempfile.TemporaryDirectory() as td:
            return run_report(td, **kw)

    def test_the_step_succeeds(self):
        proc, _, _ = self.report()
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_the_card_carries_the_rescue_push_failed_line(self):
        _, journal, _ = self.report()
        bodies = report_harness.comments(journal)
        self.assertTrue(
            any(deliver_rescue.FAILED_TAG in b for b in bodies), bodies
        )
        line = [b for b in bodies if deliver_rescue.FAILED_TAG in b][0]
        self.assertIn(ARTIFACT, line)

    def test_the_line_names_the_artifact_and_this_run(self):
        _, journal, _ = self.report()
        line = [b for b in report_harness.comments(journal)
                if deliver_rescue.FAILED_TAG in b][0]
        self.assertIn(ARTIFACT, line)
        # The harness runs as run 33468806067 (test_platform_fault_scenario).
        self.assertIn("33468806067", line)

    def test_the_delivery_is_dispatched_in_the_same_run(self):
        _, _, dispatches = self.report()
        runs = [d for d in dispatches if d[:2] == ["workflow", "run"]]
        self.assertEqual(len(runs), 1, dispatches)
        self.assertIn("run_id=33468806067", " ".join(runs[0]))

    def test_the_dead_pr_read_no_longer_has_the_last_word(self):
        """The exact comment the card carried while the work sat in an
        artifact nobody was told about."""
        _, journal, _ = self.report()
        for body in report_harness.comments(journal):
            self.assertNotIn("NOT being recorded as a dead agent", body)

    def test_the_card_is_not_requeued_while_the_delivery_is_in_flight(self):
        """A requeue rebuilds from nothing — 90 minutes and the run's cost
        again — for work that is sitting in the artifact."""
        _, journal, _ = self.report()
        self.assertEqual(report_harness.ops(journal, "state"), [])

    def test_no_artifact_means_no_claim_and_the_dre_3043_remedy_stands(self):
        """`write_patch` can fail, and then push_rescue says plainly that the
        runner's disk is the only copy. Naming an artifact that does not exist
        would send a reader — and a delivery job — after nothing, so this branch
        stands aside and the credential-expiry requeue takes the card."""
        _, journal, dispatches = self.report(patch=False)
        bodies = report_harness.comments(journal)
        for body in bodies:
            self.assertNotIn(deliver_rescue.FAILED_TAG, body)
        self.assertEqual(dispatches, [])
        self.assertIn("credential", bodies[0].lower())
        self.assertEqual(
            [e["args"][1] for e in report_harness.ops(journal, "state")], ["Todo"]
        )

    def test_a_run_that_pushed_its_own_work_says_nothing_of_the_kind(self):
        """The control: this whole branch must be invisible to every ordinary
        run, which is all of them."""
        with tempfile.TemporaryDirectory() as td:
            _, journal = report_harness.run_report(
                td, execution=FINISHED_BUT_UNPUSHED, claude_outcome="success")
        for body in report_harness.comments(journal):
            self.assertNotIn(deliver_rescue.FAILED_TAG, body)


if __name__ == "__main__":
    unittest.main()
