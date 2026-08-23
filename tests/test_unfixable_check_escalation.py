"""RED-first tests: a required check with no add-a-commit path (DRE-2694).

THE BUG (live, 2026-08-23): bureau-pipeline PR #176 sat blocked for over three
hours. The critic confirmed the feature work, the tests, the README update and
the scope were all sound, and raised exactly ONE blocking finding — the
required `TDD commit discipline` check was red, because the branch's commits
were in the wrong ORDER:

    3e00473c  config/repo-map.json         ops
    b3f2eb14  scripts/validate_card.py     <- first code change
    080ae84e  README.md + tests/...py      <- the test, LAST

`check_tdd_commits.py` (DRE-2022) requires a commit touching `tests/` strictly
before the first commit changing non-test code. The fix agent can ADD commits.
It cannot REORDER them: order changes only by rewriting history, which needs a
force-push it does not have and should not have. It diagnosed that correctly
and refused — on attempt TWO, after a full CI round and a full critic round —
and the PR then stopped permanently.

So this is a required check with **no fix path**, and nothing anywhere said so.
Every other red check the loop can attempt; this one it can only report.

WHAT IS UNDER TEST — three layers, all mechanical:

  1. `unfixable_checks.py` — the registry of required checks that CANNOT be
     satisfied by adding a commit, plus the decision the fix loop takes from a
     PR's failed check runs: escalate (first attempt, not second) or fix.
  2. `check_tdd_commits.py` — its red output now SAYS it has no add-a-commit
     path and names the remedy, from that one registry. A writer reading the
     red check learns what to do at CI time instead of after a critic round.
  3. The carriers — `agent-fix.yml` consults the registry BEFORE it announces
     an attempt, and the discipline reaches writers who never see a dispatch
     prompt: `standards/engineering.md` (the delegation rule) and a composite
     action so a product repo's own CI can run the SAME check from this one
     source (today it is wired in bureau-pipeline only, and portico #343
     merged green with its test committed after its code).

Run: cd bureau-pipeline && python3 -m pytest tests/test_unfixable_check_escalation.py -v
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
WORKFLOWS = ROOT / ".github" / "workflows"
TESTS_YML = WORKFLOWS / "tests.yml"
AGENT_FIX = WORKFLOWS / "agent-fix.yml"
ACTION = ROOT / ".github" / "actions" / "tdd-commit-check" / "action.yml"
ENGINEERING = ROOT / "standards" / "engineering.md"

sys.path.insert(0, str(SCRIPTS))

import assemble_context  # noqa: E402
import check_tdd_commits  # noqa: E402
import unfixable_checks  # noqa: E402


def _check_run(name, conclusion="failure"):
    return {"name": name, "status": "completed", "conclusion": conclusion}


# ---------------------------------------------------------------------------
# 1. The registry — which checks have no add-a-commit path, and why
# ---------------------------------------------------------------------------
class RegistryTest(unittest.TestCase):
    def test_the_tdd_order_check_is_registered(self):
        entry = unfixable_checks.match(unfixable_checks.TDD_CHECK_NAME)
        self.assertIsNotNone(entry)
        self.assertEqual(entry.check_name, unfixable_checks.TDD_CHECK_NAME)

    def test_the_registered_name_is_the_name_the_workflow_publishes(self):
        """Drift guard. The registry matches on the CHECK RUN name, which
        GitHub takes from the job's `name:`. Rename the job and the fix loop
        silently stops recognising the check it was built for."""
        doc = yaml.safe_load(TESTS_YML.read_text())
        jobs = [
            j for j in doc["jobs"].values()
            if any("check_tdd_commits.py" in (s.get("run") or "")
                   for s in j.get("steps", []))
        ]
        self.assertEqual(len(jobs), 1, "expected one job running the TDD check")
        self.assertEqual(jobs[0]["name"], unfixable_checks.TDD_CHECK_NAME)

    def test_a_nested_check_run_name_still_matches(self):
        """A repo that runs the check through a stub or a reusable workflow
        publishes it as "<caller> / <job>" — the fleet-wide shape."""
        entry = unfixable_checks.match(
            f"Pipeline Tests / {unfixable_checks.TDD_CHECK_NAME}"
        )
        self.assertIsNotNone(entry)

    def test_matching_ignores_case_and_surrounding_whitespace(self):
        self.assertIsNotNone(
            unfixable_checks.match("  tdd COMMIT discipline  ")
        )

    def test_an_ordinary_check_is_not_unfixable(self):
        for name in ("scripts unit tests", "QA Review", "build", ""):
            self.assertIsNone(unfixable_checks.match(name), name)

    def test_every_entry_states_why_and_what_clears_it(self):
        """A registry row whose text is empty would escalate a PR to a human
        with nothing to act on — the silent stall in a louder costume."""
        self.assertTrue(unfixable_checks.UNFIXABLE_CHECKS)
        for entry in unfixable_checks.UNFIXABLE_CHECKS.values():
            self.assertTrue(entry.what.strip(), entry.check_name)
            self.assertTrue(entry.why_unfixable.strip(), entry.check_name)
            self.assertTrue(entry.remedy.strip(), entry.check_name)


# ---------------------------------------------------------------------------
# 2. Reading a PR's check runs, and the decision
# ---------------------------------------------------------------------------
class FailedCheckNamesTest(unittest.TestCase):
    """`gh api repos/{repo}/commits/{sha}/check-runs` payload -> failed names."""

    def test_reads_the_bare_api_object(self):
        payload = {"check_runs": [
            _check_run("a", "failure"), _check_run("b", "success"),
        ]}
        self.assertEqual(unfixable_checks.failed_check_names(payload), ["a"])

    def test_reads_the_paginated_slurp_shape(self):
        payload = [
            {"check_runs": [_check_run("a", "failure")]},
            {"check_runs": [_check_run("b", "success"), _check_run("c", "timed_out")]},
        ]
        self.assertEqual(unfixable_checks.failed_check_names(payload), ["a", "c"])

    def test_reads_a_flat_list_of_check_runs(self):
        self.assertEqual(
            unfixable_checks.failed_check_names([_check_run("a", "failure")]), ["a"]
        )

    def test_success_neutral_and_skipped_are_not_failures(self):
        payload = {"check_runs": [
            _check_run("a", "success"), _check_run("b", "neutral"),
            _check_run("c", "skipped"),
        ]}
        self.assertEqual(unfixable_checks.failed_check_names(payload), [])

    def test_a_cancelled_run_is_not_a_verdict(self):
        """A cancelled run never reported anything, so it cannot be evidence
        of a structural violation — escalating a card to a human off one would
        park real work on an aborted run."""
        payload = {"check_runs": [_check_run("a", "cancelled")]}
        self.assertEqual(unfixable_checks.failed_check_names(payload), [])

    def test_an_in_progress_run_is_not_a_failure(self):
        payload = {"check_runs": [
            {"name": "a", "status": "in_progress", "conclusion": None}
        ]}
        self.assertEqual(unfixable_checks.failed_check_names(payload), [])

    def test_a_malformed_payload_raises_rather_than_reading_as_all_green(self):
        for bad in ("nope", 7, {"check_runs": "no"}):
            with self.assertRaises(ValueError):
                unfixable_checks.failed_check_names(bad)


class DecisionTest(unittest.TestCase):
    def test_an_unfixable_red_check_escalates(self):
        self.assertEqual(
            unfixable_checks.decide([unfixable_checks.TDD_CHECK_NAME]),
            unfixable_checks.ESCALATE,
        )

    def test_ordinary_red_checks_still_route_to_the_fix_agent(self):
        self.assertEqual(
            unfixable_checks.decide(["scripts unit tests", "build"]),
            unfixable_checks.FIX,
        )

    def test_nothing_red_routes_to_the_fix_agent(self):
        """Critic findings with a green board are the fix loop's normal work."""
        self.assertEqual(unfixable_checks.decide([]), unfixable_checks.FIX)

    def test_an_unfixable_check_among_fixable_ones_still_escalates(self):
        """The loop could fix the unit tests and would still land exactly
        where PR #176 landed — one red required check and no way to move it."""
        self.assertEqual(
            unfixable_checks.decide(
                ["scripts unit tests", unfixable_checks.TDD_CHECK_NAME]
            ),
            unfixable_checks.ESCALATE,
        )

    def test_unfixable_failures_dedupes_and_keeps_order(self):
        entries = unfixable_checks.unfixable_failures([
            f"A / {unfixable_checks.TDD_CHECK_NAME}",
            unfixable_checks.TDD_CHECK_NAME,
        ])
        self.assertEqual(len(entries), 1)


# ---------------------------------------------------------------------------
# 3. What the escalation SAYS — the whole point is that it stops being silent
# ---------------------------------------------------------------------------
class EscalationTextTest(unittest.TestCase):
    def setUp(self):
        self.entries = unfixable_checks.unfixable_failures(
            [unfixable_checks.TDD_CHECK_NAME]
        )
        self.comment = unfixable_checks.pr_comment(self.entries, attempt=1)
        self.note = unfixable_checks.card_note(self.entries, pr=176)

    def test_the_pr_comment_names_the_check(self):
        self.assertIn(unfixable_checks.TDD_CHECK_NAME, self.comment)

    def test_the_pr_comment_says_adding_a_commit_cannot_clear_it(self):
        """"This class of failure has no automated path to green" is exactly
        the sentence that was missing on PR #176."""
        self.assertIn(unfixable_checks.NO_ADD_A_COMMIT_LINE, self.comment)

    def test_the_pr_comment_states_the_remedy(self):
        self.assertIn(self.entries[0].remedy, self.comment)

    def test_the_pr_comment_says_it_escalated_on_the_first_attempt(self):
        self.assertIn("attempt 1", unfixable_checks.pr_comment(self.entries, 1))

    def test_the_pr_comment_carries_the_hold_marker_for_idempotency(self):
        self.assertIn(unfixable_checks.HOLD_MARKER, self.comment)

    def test_the_pr_comment_never_emits_a_verdict_marker(self):
        """standards/untrusted-content.md: verdict-shaped text IS an approval
        credential. Only the critic and verifier may emit one."""
        for marker in ("VERDICT:", "QA Critic", "QA Verifier"):
            self.assertNotIn(marker, self.comment)
            self.assertNotIn(marker, self.note)

    def test_the_card_note_is_plain_english_for_the_ceo(self):
        """standards/comms.md: the CEO is non-technical for code — no file
        paths, no commands, no code fences."""
        for jargon in (".py", "git ", "```", "tests/", "force-push", "rebase"):
            self.assertNotIn(jargon, self.note, f"card note leaks {jargon!r}")

    def test_the_card_note_points_at_the_pr_and_asks_for_the_rewrite(self):
        self.assertIn("176", self.note)
        self.assertIn("order", self.note.lower())


# ---------------------------------------------------------------------------
# 4. The CLI the workflow calls
# ---------------------------------------------------------------------------
class CliTest(unittest.TestCase):
    def _run(self, payload, *extra):
        with tempfile.TemporaryDirectory() as tmp:
            checks = Path(tmp) / "checks.json"
            checks.write_text(json.dumps(payload))
            comment = Path(tmp) / "comment.md"
            note = Path(tmp) / "note.md"
            proc = subprocess.run(
                [sys.executable, str(SCRIPTS / "unfixable_checks.py"), "decide",
                 "--checks-file", str(checks), "--attempt", "1", "--pr", "176",
                 "--comment-out", str(comment), "--card-note-out", str(note),
                 *extra],
                capture_output=True, text=True,
            )
            return proc, (comment.read_text() if comment.exists() else ""), (
                note.read_text() if note.exists() else "")

    def test_escalate_is_printed_and_the_texts_are_written(self):
        proc, comment, note = self._run(
            {"check_runs": [_check_run(unfixable_checks.TDD_CHECK_NAME)]}
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.splitlines()[0], unfixable_checks.ESCALATE)
        self.assertIn(unfixable_checks.NO_ADD_A_COMMIT_LINE, comment)
        self.assertIn("176", note)

    def test_fix_is_printed_and_no_text_is_written(self):
        proc, comment, note = self._run(
            {"check_runs": [_check_run("scripts unit tests")]}
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.splitlines()[0], unfixable_checks.FIX)
        self.assertEqual(comment, "")
        self.assertEqual(note, "")

    def test_a_green_tdd_check_does_not_escalate(self):
        """Selection happens on FAILED runs only — a PR whose TDD check passed
        must reach the fix agent normally."""
        proc, _, _ = self._run({"check_runs": [
            _check_run(unfixable_checks.TDD_CHECK_NAME, "success"),
            _check_run("scripts unit tests", "failure"),
        ]})
        self.assertEqual(proc.stdout.splitlines()[0], unfixable_checks.FIX)

    def test_a_malformed_payload_exits_2_and_never_prints_a_decision(self):
        """Fail loud. A check-runs read that silently answered "fix" would put
        the loop back where it started, and answering "escalate" would park
        healthy cards."""
        proc, _, _ = self._run("not a payload")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertNotIn(unfixable_checks.FIX, proc.stdout)
        self.assertNotIn(unfixable_checks.ESCALATE, proc.stdout)

    def test_a_missing_file_exits_2(self):
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "unfixable_checks.py"), "decide",
             "--checks-file", "/nonexistent/checks.json"],
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)


# ---------------------------------------------------------------------------
# 5. The red check tells the writer, at CI time, that it has no fix path
# ---------------------------------------------------------------------------
class TddCheckOutputTest(unittest.TestCase):
    """DRE-2694 item 0 / option 2: fail where it is cheap. The violation is
    knowable at the second commit; PR #176 learned it after a full CI round
    AND a full critic round."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "t")
        self.add_commit("README.md", "chore: seed")

    def git(self, *args):
        subprocess.run(["git", "-C", str(self.repo), *args], check=True,
                       capture_output=True, text=True)

    def add_commit(self, path, subject):
        p = self.repo / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"# {subject}\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", subject)

    def test_the_red_output_states_there_is_no_add_a_commit_path(self):
        self.git("checkout", "-q", "-b", "agent/DRE-2694-x")
        self.add_commit("scripts/widget.py", "fix(DRE-2694): impl first")
        self.add_commit("tests/test_widget.py", "test(DRE-2694): late")
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "check_tdd_commits.py"), "main", "HEAD"],
            cwd=str(self.repo), capture_output=True, text=True,
            env={**os.environ, "PR_AUTHOR": "someone"},
        )
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        # Still says what is missing (DRE-2022's wording, unchanged)...
        self.assertIn(check_tdd_commits.FAILURE_MESSAGE, proc.stdout)
        # ...and now also that no commit can clear it, plus what does.
        self.assertIn(unfixable_checks.NO_ADD_A_COMMIT_LINE, proc.stdout)
        entry = unfixable_checks.match(unfixable_checks.TDD_CHECK_NAME)
        self.assertIn(entry.remedy, proc.stdout)

    def test_a_green_run_does_not_print_the_remedy(self):
        self.git("checkout", "-q", "-b", "agent/DRE-2694-y")
        self.add_commit("tests/test_widget.py", "test(DRE-2694): RED")
        self.add_commit("scripts/widget.py", "fix(DRE-2694): green")
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "check_tdd_commits.py"), "main", "HEAD"],
            cwd=str(self.repo), capture_output=True, text=True,
            env={**os.environ, "PR_AUTHOR": "someone"},
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertNotIn(unfixable_checks.NO_ADD_A_COMMIT_LINE, proc.stdout)


# ---------------------------------------------------------------------------
# 6. agent-fix.yml — escalate on the FIRST attempt, never silently
# ---------------------------------------------------------------------------
class AgentFixWiringTest(unittest.TestCase):
    def setUp(self):
        self.doc = yaml.safe_load(AGENT_FIX.read_text())
        self.steps = self.doc["jobs"]["fix"]["steps"]
        self.names = [str(s.get("name", "")) for s in self.steps]

    def _index_of_step_running(self, fragment):
        for i, s in enumerate(self.steps):
            if fragment in (s.get("run") or ""):
                return i
        raise AssertionError(f"no step runs {fragment!r}")

    def _index_named(self, fragment):
        for i, n in enumerate(self.names):
            if fragment.lower() in n.lower():
                return i
        raise AssertionError(f"no step named ~{fragment!r}; have {self.names}")

    def test_the_gate_step_consults_the_registry(self):
        i = self._index_of_step_running("unfixable_checks.py")
        self.assertIsNotNone(self.steps[i].get("id"),
                             "the gate must carry an id so later steps can read it")

    def test_the_gate_runs_before_the_fix_agent(self):
        self.assertLess(
            self._index_of_step_running("unfixable_checks.py"),
            next(i for i, s in enumerate(self.steps)
                 if "claude-code-action" in str(s.get("uses", ""))),
        )

    def test_the_gate_runs_before_the_attempt_is_announced(self):
        """The announce step strips `needs-human` off the card — running it
        first would un-park a card this gate is about to park."""
        self.assertLess(
            self._index_of_step_running("unfixable_checks.py"),
            self._index_named("Announce fix attempt"),
        )

    def test_the_gate_does_not_wait_for_a_second_attempt(self):
        """"Escalates on the first attempt rather than the second" — the
        acceptance criterion. An `if` keyed on the attempt number would
        reproduce PR #176 exactly."""
        gate = self.steps[self._index_of_step_running("unfixable_checks.py")]
        self.assertNotIn("attempt", str(gate.get("if", "")))

    def test_the_gate_is_scoped_to_fix_mode(self):
        gate = self.steps[self._index_of_step_running("unfixable_checks.py")]
        self.assertIn("mode", str(gate.get("if", "")))

    def test_the_gate_reads_the_head_sha_through_env(self):
        gate = self.steps[self._index_of_step_running("unfixable_checks.py")]
        self.assertIn("head_sha", str(gate.get("env", {})))

    def test_the_gate_parks_the_card_for_a_human(self):
        """Visible, not silent: the CEO's "needs you" queue is the whole
        difference between this and a permanent stall."""
        run = self.steps[self._index_of_step_running("unfixable_checks.py")]["run"]
        self.assertIn("needs-human", run)
        self.assertIn("Plan Review", run)

    def test_the_gate_comments_on_the_pull_request(self):
        run = self.steps[self._index_of_step_running("unfixable_checks.py")]["run"]
        self.assertIn("gh pr comment", run)

    def test_the_gate_does_not_repeat_itself_on_the_same_head(self):
        """Idempotent per head sha, the fix-convergence-halt pattern — a
        re-fired critic must not re-post the same hold."""
        run = self.steps[self._index_of_step_running("unfixable_checks.py")]["run"]
        self.assertIn(unfixable_checks.HOLD_MARKER, run)
        self.assertIn("SHA8", run)

    def test_every_step_after_the_gate_stands_down_when_it_escalates(self):
        """The loop must not "attempt and block". Anything that costs
        inference, announces an attempt, or reports an outcome is skipped."""
        gate_index = self._index_of_step_running("unfixable_checks.py")
        gate_id = self.steps[gate_index]["id"]
        guard = f"steps.{gate_id}.outputs.escalate"
        unguarded = [
            str(s.get("name", s.get("uses", "?")))
            for s in self.steps[gate_index + 1:]
            if guard not in str(s.get("if", ""))
        ]
        self.assertEqual(unguarded, [], f"steps run past the escalation: {unguarded}")


# ---------------------------------------------------------------------------
# 7. The detection gap — one source, callable by any product repo's CI
# ---------------------------------------------------------------------------
class SharedTddActionTest(unittest.TestCase):
    """The fleet sweep of 2026-08-23: portico #343 was GREEN and mergeable
    with its test committed after its code, and deltasolv runs no such check
    either. `check_tdd_commits.py` is wired in bureau-pipeline's tests.yml and
    nowhere else, so "green" across the fleet does not mean the order was
    right. A composite action gives every repo's own CI the SAME check from
    this one file — no per-repo copy to drift."""

    def setUp(self):
        self.assertTrue(ACTION.is_file(), f"{ACTION} is missing")
        self.doc = yaml.safe_load(ACTION.read_text())
        self.steps = (self.doc.get("runs") or {}).get("steps") or []

    def test_is_a_composite_action(self):
        self.assertEqual(self.doc["runs"]["using"], "composite")

    def test_declares_the_inputs_a_caller_must_pass(self):
        for name in ("base-ref", "head-sha", "pr-author"):
            self.assertIn(name, self.doc.get("inputs", {}))

    def test_it_runs_the_one_checker_and_carries_no_copy_of_it(self):
        runs = "\n".join(s.get("run") or "" for s in self.steps)
        self.assertIn("check_tdd_commits.py", runs)

    def test_the_script_path_it_uses_actually_resolves(self):
        """The action reaches the checker through its own download of this
        repo (`$GITHUB_ACTION_PATH/../../..` is the repo root). Compute that
        path for real — a typo here is a check that never runs."""
        runs = "\n".join(s.get("run") or "" for s in self.steps)
        marker = "$GITHUB_ACTION_PATH/"
        self.assertIn(marker, runs)
        rel = runs.split(marker, 1)[1].split('"', 1)[0].split()[0]
        self.assertTrue(
            (ACTION.parent / rel).resolve().is_file(),
            f"{rel!r} does not resolve to a file from {ACTION.parent}",
        )

    def test_caller_input_reaches_the_check_through_env_not_interpolation(self):
        """Same rule tests.yml follows: a crafted branch name must never
        become shell input."""
        step = next(s for s in self.steps if "check_tdd_commits.py" in (s.get("run") or ""))
        env = str(step.get("env", {}))
        self.assertIn("inputs.base-ref", env)
        self.assertIn("inputs.head-sha", env)
        self.assertIn("inputs.pr-author", env)
        self.assertNotIn("inputs.base-ref", step["run"])
        self.assertNotIn("inputs.head-sha", step["run"])

    def test_every_run_step_declares_a_shell(self):
        for step in self.steps:
            if "run" in step:
                self.assertIn("shell", step, step.get("name"))

    def test_it_never_checks_out_the_pipeline_repo(self):
        """A composite action cannot thread `pipeline_ref`, so a checkout in
        here would escape the release channel entirely (test_shared_node_
        action.py enforces the same rule across every action)."""
        for step in self.steps:
            self.assertFalse(str(step.get("uses", "")).startswith("actions/checkout"))


# ---------------------------------------------------------------------------
# 8. Item 0 — the rule reaches writers who never see a dispatch prompt
# ---------------------------------------------------------------------------
class DelegationRuleTest(unittest.TestCase):
    """The root cause, as the card's SECOND CORRECTION narrows it: on
    2026-08-23 the delegating session handed hand-built CODE work to a
    coordinating sub-agent for the first time, working from a task brief that
    never mentioned the rule. A sub-agent inherits the repo, the tools and the
    credentials — none of the delegating session's context. Discipline applied
    by habit has to be written down at the moment of handoff, because habit
    does not travel."""

    def setUp(self):
        self.text = ENGINEERING.read_text()

    def test_the_standard_carries_a_delegation_rule(self):
        self.assertIn(unfixable_checks.DELEGATION_RULE_HEADING, self.text)

    def test_the_rule_says_the_brief_must_carry_the_gates(self):
        lowered = self.text.lower()
        self.assertIn("brief", lowered)
        self.assertIn("gate", lowered)
        section = self.text.split(unfixable_checks.DELEGATION_RULE_HEADING, 1)[1]
        section = section[:1500].lower()
        for phrase in ("sub-agent", "commit", "habit"):
            self.assertIn(phrase, section, f"the delegation rule never says {phrase!r}")

    def test_the_rule_names_the_tdd_order_gate_it_was_written_for(self):
        section = self.text.split(unfixable_checks.DELEGATION_RULE_HEADING, 1)[1]
        self.assertIn("DRE-2694", section[:1500])

    def test_the_standard_reaches_the_writers_and_the_fix_loop(self):
        """assemble_context is the only knowledge channel headless agents
        have — a standard no role loads is a document nobody reads."""
        for role in ("engineer", "frontend", "devops", "planner", "critic", "fix"):
            self.assertIn(
                "engineering.md", assemble_context.standards_for(role), role
            )


# ---------------------------------------------------------------------------
# 6b. The gate step, EXECUTED — not grepped
# ---------------------------------------------------------------------------
# The YAML saying the right thing proves the wiring, not the behaviour: this
# step spans three systems (the checks API, the registry, Linear), and
# unit-green is not live-working. So run its real `run:` block against stubbed
# `gh` and `linear_ops.py`, the way tests/test_fix_dispatch_clears_stale_hold.py
# executes the Announce step.
GH_STUB = r"""#!/usr/bin/env python3
import json, os, sys
argv = sys.argv[1:]
json.dump(argv, open(os.environ["GH_LOG"], "a")); open(os.environ["GH_LOG"], "a").write("\n")
if argv[:1] == ["api"]:
    # The URL is not argv[1] — `--paginate --slurp` come first.
    if any("/check-runs" in a for a in argv):
        sys.stdout.write(open(os.environ["GH_CHECKS"]).read())
    else:
        sys.stdout.write(open(os.environ["GH_COMMENTS"]).read())
    sys.exit(0)
if argv[:2] == ["pr", "comment"]:
    body = argv[argv.index("--body-file") + 1]
    open(os.environ["GH_POSTED"], "a").write(open(body).read())
    sys.exit(0)
sys.exit(0)
"""


class GateScenarioTest(unittest.TestCase):
    HEAD = "3e00473c" + "0" * 32

    def _run(self, check_runs, thread=(), card_held=0, linear_exit=0):
        """`linear_exit` makes every linear_ops call fail — count-comments then
        answers nothing at all, the shape a real API blip has."""
        """Execute the gate's real run block. Returns (proc, outputs, posted,
        linear_calls)."""
        step = None
        for s in yaml.safe_load(AGENT_FIX.read_text())["jobs"]["fix"]["steps"]:
            if "unfixable_checks.py" in (s.get("run") or ""):
                step = s
        self.assertIsNotNone(step, "no gate step in agent-fix.yml")
        run = step["run"].replace("${{ github.repository }}", "acme/widget")
        self.assertNotIn("${{", run, "harness left an unsubstituted expression")

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        td = Path(tmp.name)
        (td / "bin").mkdir()
        (td / ".bureau-pipeline" / "scripts").mkdir(parents=True)
        (td / ".bureau-pipeline" / "scripts" / "unfixable_checks.py").write_text(
            (SCRIPTS / "unfixable_checks.py").read_text()
        )
        linear_log = td / "linear.jsonl"
        (td / ".bureau-pipeline" / "scripts" / "linear_ops.py").write_text(
            "#!/usr/bin/env python3\nimport json, os, sys\n"
            f"open({str(linear_log)!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n"
            "code = int(os.environ.get('STUB_LINEAR_EXIT', '0'))\n"
            "if sys.argv[1:2] == ['count-comments'] and not code:\n"
            "    print(os.environ.get('STUB_CARD_HELD', '0'))\n"
            "sys.exit(code)\n"
        )
        gh = td / "bin" / "gh"
        gh.write_text(GH_STUB)
        gh.chmod(0o755)
        (td / "checks.json").write_text(json.dumps(check_runs))
        # `--paginate --slurp` on the comments API yields one array per page,
        # which the step's jq folds with `add`.
        (td / "comments.json").write_text(json.dumps([list(thread)]))
        out = td / "step-output"
        out.write_text("")
        script = td / "gate.sh"
        script.write_text("set -eo pipefail\n" + run)

        proc = subprocess.run(
            ["bash", str(script)], cwd=str(td), capture_output=True, text=True,
            env={**os.environ,
                 "PATH": f"{td / 'bin'}:{os.environ['PATH']}",
                 "GITHUB_OUTPUT": str(out),
                 "GH_LOG": str(td / "gh.jsonl"),
                 "GH_CHECKS": str(td / "checks.json"),
                 "GH_COMMENTS": str(td / "comments.json"),
                 "GH_POSTED": str(td / "posted.md"),
                 "GH_TOKEN": "test", "LINEAR_API_KEY": "test-key",
                 "HEAD_SHA": self.HEAD, "CARD": "DRE-2672",
                 "PR": "176", "ATTEMPT": "1",
                 "STUB_CARD_HELD": str(card_held),
                 "STUB_LINEAR_EXIT": str(linear_exit)},
        )
        outputs = dict(
            line.split("=", 1) for line in out.read_text().splitlines() if "=" in line
        )
        posted = (td / "posted.md").read_text() if (td / "posted.md").exists() else ""
        calls = [json.loads(x) for x in linear_log.read_text().splitlines()] \
            if linear_log.exists() else []
        return proc, outputs, posted, calls

    def _red_tdd(self):
        # The real `gh api --paginate --slurp` shape: one object per page.
        return [{"total_count": 2, "check_runs": [
            _check_run("scripts unit tests", "success"),
            _check_run(unfixable_checks.TDD_CHECK_NAME, "failure"),
        ]}]

    # -- the PR #176 shape ------------------------------------------------
    def test_a_red_order_check_escalates_holds_and_parks(self):
        proc, outputs, posted, calls = self._run(self._red_tdd())
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(outputs.get("escalate"), "true")
        self.assertIn(unfixable_checks.NO_ADD_A_COMMIT_LINE, posted)
        self.assertIn(unfixable_checks.HOLD_MARKER, posted)
        self.assertIn(self.HEAD[:8], posted)
        verbs = [c[0] for c in calls]
        self.assertIn("comment", verbs)
        self.assertIn("add-label", verbs)
        self.assertIn(["add-label", "DRE-2672", "needs-human"], calls)
        self.assertTrue(any(c[0] in ("advance", "state") for c in calls))

    def test_the_card_note_is_the_plain_english_one(self):
        _, _, _, calls = self._run(self._red_tdd())
        note = next(c for c in calls if c[0] == "comment")[2]
        self.assertIn("176", note)
        self.assertNotIn("force-push", note)

    # -- the normal path is untouched -------------------------------------
    def test_an_ordinary_red_check_does_not_escalate(self):
        proc, outputs, posted, calls = self._run(
            [{"check_runs": [_check_run("scripts unit tests", "failure")]}]
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertNotIn("escalate", outputs)
        self.assertEqual(posted, "")
        self.assertEqual(calls, [])

    def test_an_all_green_board_does_not_escalate(self):
        _, outputs, posted, calls = self._run([{"check_runs": []}])
        self.assertNotIn("escalate", outputs)
        self.assertEqual(posted, "")
        self.assertEqual(calls, [])

    # -- adversarial ------------------------------------------------------
    def _hold_key(self, sha8=None):
        return f"{unfixable_checks.HOLD_MARKER} @{sha8 or self.HEAD[:8]}"

    def _prior_pr_hold(self, login="agent-bureau-bot[bot]", sha8=None):
        return {"user": {"login": login},
                "body": f"🛑 held\n\n{self._hold_key(sha8)}"}

    def test_a_second_run_on_the_same_head_does_not_repeat_the_notices(self):
        """The critic can re-fire on the same commit. The hold still stands —
        escalate must stay true — but neither side collects a second copy."""
        proc, outputs, posted, calls = self._run(
            self._red_tdd(), thread=[self._prior_pr_hold()], card_held=1
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(outputs.get("escalate"), "true")
        self.assertEqual(posted, "", "the hold was posted twice on one head")
        self.assertEqual([c for c in calls if c[0] == "comment"], [],
                         "the card was noted twice on one head")

    def test_the_park_is_re_applied_even_when_both_notices_already_landed(self):
        """add-label and the park are no-ops when already applied, so they sit
        behind no receipt at all — that is what makes a retry free, and it is
        how the Report step's park_for_human() has always worked."""
        _, _, _, calls = self._run(
            self._red_tdd(), thread=[self._prior_pr_hold()], card_held=1
        )
        self.assertIn(["add-label", "DRE-2672", "needs-human"], calls)
        self.assertTrue(any(c[0] in ("advance", "state") for c in calls))

    # THE DEFECT the critic caught: one shared receipt let a Linear blip
    # disappear the human-facing notification for good. The PR comment landed,
    # a `|| true` Linear call failed silently, and every later run saw the PR
    # marker and skipped the card entirely — a hold visible on the PR and
    # nothing at all in the CEO's queue.
    def test_a_landed_pr_hold_does_not_suppress_a_card_note_that_never_landed(self):
        proc, outputs, posted, calls = self._run(
            self._red_tdd(), thread=[self._prior_pr_hold()], card_held=0
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(outputs.get("escalate"), "true")
        self.assertEqual(posted, "", "the PR side must still not repeat itself")
        note = [c for c in calls if c[0] == "comment"]
        self.assertEqual(len(note), 1, "the card note was never retried")
        self.assertIn(["add-label", "DRE-2672", "needs-human"], calls)

    def test_a_landed_card_note_does_not_suppress_a_pr_hold_that_never_landed(self):
        """The mirror image — each side reads the side it guards."""
        _, outputs, posted, calls = self._run(
            self._red_tdd(), thread=[], card_held=1
        )
        self.assertEqual(outputs.get("escalate"), "true")
        self.assertIn(unfixable_checks.HOLD_MARKER, posted)
        self.assertEqual([c for c in calls if c[0] == "comment"], [])

    def test_an_unreadable_card_receipt_retries_rather_than_going_quiet(self):
        """A duplicate note is noise; a missing one is the stall this card is
        about. So a count-comments that errors counts as ABSENT."""
        _, _, _, calls = self._run(self._red_tdd(), card_held=1, linear_exit=1)
        self.assertEqual(len([c for c in calls if c[0] == "comment"]), 1)

    def test_a_hold_from_an_earlier_head_does_not_suppress_this_one(self):
        """Sha-bound, like the convergence halt: a new commit is a new fact."""
        _, outputs, posted, _ = self._run(
            self._red_tdd(), thread=[self._prior_pr_hold(sha8="deadbeef")]
        )
        self.assertEqual(outputs.get("escalate"), "true")
        self.assertIn(unfixable_checks.HOLD_MARKER, posted)

    def test_a_planted_hold_from_another_author_does_not_suppress_it(self):
        """DRE-1995: anyone can comment on a PR. Only the worker bot's own
        marker counts, or a stranger could silence the escalation."""
        _, outputs, posted, _ = self._run(
            self._red_tdd(), thread=[self._prior_pr_hold(login="someone")]
        )
        self.assertEqual(outputs.get("escalate"), "true")
        self.assertIn(unfixable_checks.HOLD_MARKER, posted)

    def test_an_unreadable_check_payload_falls_through_to_the_normal_path(self):
        """Fail toward today's behaviour. Guessing "escalate" on a payload we
        could not read would park healthy cards; the normal fix path already
        survives a check it cannot fix, it just costs a round."""
        proc, outputs, posted, calls = self._run("not a payload")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertNotIn("escalate", outputs)
        self.assertEqual(posted, "")
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
