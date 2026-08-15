"""RED-first tests for DRE-2466 — the critic needs a strategy, not one pass.

THE BUG (portico PR #297, 2026-08-15). A pull request of 118 files and
+16,909/-628 went through the same review the pipeline gives a two-file
change: ONE exhaustive `gh pr diff` pass, under the prompt's standing
instruction to "examine the ENTIRE diff and list every blocking finding in
THIS verdict". Four executions across two run attempts, every one of them
`subtype: success`, `is_error: false`, none near its turn ceiling:

    attempt 1 critic   29 / 80 turns   $4.2052   209 s
    attempt 1 retry    42 / 120 turns  $3.3481   181 s
    attempt 2 critic   21 / 80 turns   $1.4550   123 s
    attempt 2 retry    25 / 120 turns  $3.3887   203 s

$12.40, no verdict file, four times. The PR then merged with the review
check red and no review at all, on an operator override.

WHAT IS ESTABLISHED: #297 is 4-6x larger than any PR ever successfully
reviewed in this repo (largest passing: #275 at +4,092 across 29 files;
#290 at +2,828 across 41), PR #296 passed on identical config 15 hours
earlier, and this signature is novel here. WHAT IS NOT ESTABLISHED: WHY
scale breaks it — every run ended voluntarily in 2-3.5 minutes, which is
neither context exhaustion (that ends `error_max_turns`, which this repo
has produced) nor an auth death. These tests therefore pin the STRATEGY,
not a mechanism nobody can prove from the surviving record.

WHAT THIS FILE PINS:

1. The size is MEASURED before the critic runs, from records the workflow
   already fetches, and the measurement plus the chosen path appear in the
   run log. A silent strategy switch is unauditable.
2. Above the first threshold the critic gets a FILE-LIST strategy — triage
   the changed-file list, read per file, exhaustive over what it actually
   reviewed — and a turn ceiling raised to match. Failing fast at this size
   would only convert $12 of doomed spend into $0: the PR still ships
   unreviewed, which is the state that got overridden anyway.
3. Only past a SECOND, much larger threshold does the job fail fast — and
   that message names the real size and asks for a split. It must never
   claim an authentication or credential failure: PR #297 spent a day being
   read as a token problem (DRE-2465), and the mechanism here is still
   unproven.
4. The critic writes its verdict file FIRST as a stub and rewrites it as it
   goes, so a run that ends early leaves something behind — with the stub
   marked UNFINISHED so it can never be posted as a real verdict or wake
   the fix agent with an empty finding list (the #1441/#1442 false-reject
   class).

THRESHOLDS come from this repo's own history, not round numbers:
  * 50 files / 5,000 changed lines — above the largest review that has ever
    succeeded here (+4,092 / 29 files; #290's 41 files), so every PR the
    one-pass review demonstrably handles keeps handling it unchanged.
  * 200 files / 20,000 changed lines — above #297 itself, because #297 is
    exactly the PR that most deserved a review. Also under GitHub's 300-file
    compare cap, so the fail-fast decision is never made on truncated data.
Generated files (lock files, snapshots, minified bundles) are discounted
from the reviewable size: a 25k-line lockfile bump is not a 25k-line review,
and failing those fast would block every dependency PR.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

import yaml

REPO = os.path.join(os.path.dirname(__file__), "..")
WF_DIR = os.path.join(REPO, ".github", "workflows")
SCRIPTS = os.path.join(REPO, "scripts")
sys.path.insert(0, SCRIPTS)

import check_critic_result  # noqa: E402
import pr_size_strategy as pss  # noqa: E402


# ── helpers ────────────────────────────────────────────────────────────────

def compare(files):
    """A GitHub compare record: [(path, additions, deletions), ...]."""
    return {
        "files": [
            {"filename": p, "additions": a, "deletions": d}
            for p, a, d in files
        ]
    }


def code(n_files, lines_each, prefix="src/mod"):
    return [(f"{prefix}{i}.py", lines_each, 0) for i in range(n_files)]


def strategy_for(compare_record=None, pr_json=None):
    m = pss.measure(compare_record, pr_json)
    return pss.choose(m)


def wf_steps(workflow="qa-review.yml", job="review"):
    doc = yaml.safe_load(open(os.path.join(WF_DIR, workflow)))
    return doc["jobs"][job]["steps"]


def wf_step(step_id, workflow="qa-review.yml", job="review"):
    for step in wf_steps(workflow, job):
        if step.get("id") == step_id:
            return step
    raise AssertionError(f"no step id={step_id!r} in {workflow}:{job}")


def critic_prompts():
    return [wf_step(sid)["with"]["prompt"] for sid in ("critic", "critic_retry")]


def src(workflow="qa-review.yml"):
    return open(os.path.join(WF_DIR, workflow)).read()


# ── 1. the measurement ─────────────────────────────────────────────────────

class MeasureTest(unittest.TestCase):
    def test_counts_files_and_lines_from_the_compare_record(self):
        """The Resolve PR step already writes /tmp/qa-compare.json for the
        content id (DRE-2340) — the size signal is free."""
        m = pss.measure(compare([("a.py", 10, 5), ("b.py", 1, 2)]), None)
        self.assertEqual(m["files"], 2)
        self.assertEqual(m["lines"], 18)

    def test_pr_totals_beat_a_truncated_compare_record(self):
        """GitHub's compare record caps `files[]` at 300 entries and does not
        paginate it (verdict_content.py documents the same cap). Measuring a
        400-file PR off that record would UNDER-count, and under-counting is
        the dangerous direction — it routes a huge PR down the one-pass path
        that cannot review it."""
        m = pss.measure(
            compare(code(300, 20)),
            {"changedFiles": 412, "additions": 30_000, "deletions": 1_000},
        )
        self.assertEqual(m["files"], 412)
        self.assertEqual(m["lines"], 31_000)
        self.assertTrue(m["truncated"])

    def test_generated_files_are_discounted_from_the_reviewable_size(self):
        """A lockfile bump is not a review. Two files, 24,003 lines, of which
        24,000 are `package-lock.json` — the critic reads the manifest, not
        the lock."""
        m = pss.measure(
            compare([("package.json", 2, 1), ("package-lock.json", 20_000, 4_000)]),
            None,
        )
        self.assertEqual(m["lines"], 24_003)
        self.assertEqual(m["review_lines"], 3)
        self.assertEqual(m["review_files"], 1)

    def test_no_data_at_all_measures_zero(self):
        """An API blip leaves `{}` on disk. Zero must be a legal measurement
        that degrades to today's behavior, never a crash."""
        m = pss.measure({}, {})
        self.assertEqual((m["files"], m["lines"]), (0, 0))
        self.assertEqual(pss.choose(m), "standard")

    def test_junk_values_do_not_raise(self):
        m = pss.measure(
            {"files": [{"filename": "a.py", "additions": None, "deletions": "x"},
                       "not-a-dict"]},
            {"changedFiles": "many"},
        )
        self.assertEqual(m["lines"], 0)


# ── 2. strategy selection, either side of each threshold (AC 7) ────────────

class StrategySelectionTest(unittest.TestCase):
    """Either side of BOTH thresholds, on files and on lines independently."""

    def test_just_under_the_first_threshold_is_the_standard_one_pass(self):
        self.assertEqual(
            strategy_for(compare(code(10, pss.LARGE_LINES // 10))), "standard"
        )
        self.assertEqual(
            strategy_for(compare(code(pss.LARGE_FILES, 1))), "standard"
        )

    def test_just_over_the_first_threshold_switches_to_the_file_list(self):
        self.assertEqual(
            strategy_for(compare(code(10, pss.LARGE_LINES // 10 + 1))), "large"
        )
        self.assertEqual(
            strategy_for(compare(code(pss.LARGE_FILES + 1, 1))), "large"
        )

    def test_just_under_the_second_threshold_still_gets_reviewed(self):
        """The whole point of the card: a large PR is the one MOST worth
        reviewing. Failing fast here would only make the doomed spend $0."""
        self.assertEqual(
            strategy_for(compare(code(100, pss.OVERSIZED_LINES // 100))), "large"
        )
        self.assertEqual(
            strategy_for(compare(code(pss.OVERSIZED_FILES, 1))), "large"
        )

    def test_just_over_the_second_threshold_fails_fast(self):
        self.assertEqual(
            strategy_for(compare(code(100, pss.OVERSIZED_LINES // 100 + 1))),
            "oversized",
        )
        self.assertEqual(
            strategy_for(compare(code(pss.OVERSIZED_FILES + 1, 1))), "oversized"
        )

    def test_the_thresholds_sit_above_this_repos_proven_history(self):
        """Every PR the one-pass review has ACTUALLY completed must keep the
        one-pass review — the thresholds are derived from that history, so a
        later edit that drops them below it has to fail here."""
        proven = {
            "#296 (+278/-11, 4 files)": compare(
                [(f"f{i}.py", 70, 3) for i in range(4)]
            ),
            "#275 (+4,092, 29 files)": compare(
                [(f"f{i}.py", 4092 // 29, 0) for i in range(29)]
            ),
            "#290 (+2,828, 41 files)": compare(
                [(f"f{i}.py", 2828 // 41, 0) for i in range(41)]
            ),
        }
        for name, rec in proven.items():
            with self.subTest(pr=name):
                self.assertEqual(strategy_for(rec), "standard")

    def test_portico_297_gets_the_large_strategy_not_a_fast_failure(self):
        """118 files, +16,909/-628 — the PR this card is about. It must be
        REVIEWED by the file-list strategy, not refused."""
        m = pss.measure(
            None, {"changedFiles": 118, "additions": 16_909, "deletions": 628}
        )
        self.assertEqual(pss.choose(m), "large")

    def test_a_giant_lockfile_bump_is_not_oversized(self):
        """Discounting generated files is what keeps dependency PRs — whose
        diffs routinely clear 20k lines in one file — out of the fail-fast
        path they must never enter."""
        m = pss.measure(
            compare([("package.json", 3, 3), ("package-lock.json", 25_000, 9_000)]),
            None,
        )
        self.assertEqual(pss.choose(m), "standard")


# ── 3. the turn ceiling matches the work ───────────────────────────────────

class TurnBudgetTest(unittest.TestCase):
    def test_the_large_path_gets_more_turns_than_the_standard_one(self):
        std, std_retry = pss.turn_budget("standard")
        big, big_retry = pss.turn_budget("large")
        self.assertGreater(big, std)
        self.assertGreater(big_retry, std_retry)

    def test_every_retry_budget_stays_strictly_higher(self):
        """DRE-2422's durable rule, extended to the new path: a retry that
        comes back with no more of the resource it exhausted is a second
        invoice, not a recovery."""
        for strategy in pss.TURN_BUDGET:
            with self.subTest(strategy=strategy):
                first, retry = pss.turn_budget(strategy)
                self.assertGreater(retry, first)
                self.assertLessEqual(retry, 200, "a ceiling is still a ceiling")

    def test_an_unknown_strategy_falls_back_to_the_standard_budget(self):
        self.assertEqual(pss.turn_budget("nonsense"), pss.turn_budget("standard"))


# ── 4. what the critic is actually told ────────────────────────────────────

class StrategyContextTest(unittest.TestCase):
    def ctx(self, strategy, m=None, pr="297"):
        return pss.strategy_context(strategy, m or pss.measure(None, None), pr)

    def test_standard_block_keeps_the_exhaustive_single_pass(self):
        block = self.ctx("standard")
        self.assertIn("gh pr diff", block)
        self.assertIn("ENTIRE diff", block)

    def test_large_block_forbids_the_single_exhaustive_pass(self):
        block = self.ctx("large")
        self.assertRegex(block, r"(?i)do not.*single.*pass|not.*one .*pass")
        self.assertIn("--name-only", block)

    def test_large_block_orders_a_triaged_per_file_review(self):
        block = self.ctx("large").lower()
        for token in ("file list", "per file", "highest risk"):
            with self.subTest(token=token):
                self.assertIn(token, block)

    def test_large_block_scopes_the_exhaustive_requirement_to_what_it_read(self):
        """The exhaustive-findings rule (PR #7, six rounds) still applies —
        but to the files actually reviewed, and the verdict must say which
        files those were, so nobody reads an unread file as a clean one."""
        block = self.ctx("large").lower()
        self.assertIn("every blocking finding", block)
        self.assertIn("did not review", block)

    def test_both_blocks_name_the_measured_size(self):
        m = pss.measure(None, {"changedFiles": 118, "additions": 16_909,
                               "deletions": 628})
        for strategy in ("standard", "large"):
            with self.subTest(strategy=strategy):
                block = self.ctx(strategy, m)
                self.assertIn("118", block)
                self.assertIn("17,537", block)

    def test_the_block_carries_the_pr_number_it_was_built_for(self):
        self.assertIn("297", self.ctx("large", pr="297"))

    def test_a_hostile_pr_number_cannot_smuggle_text_into_the_prompt(self):
        """The PR number reaches this script from workflow context; it is
        never anything but digits, and the script must not be the place that
        assumption is first tested."""
        block = pss.strategy_context(
            "large", pss.measure(None, None), "1; rm -rf / #`whoami`"
        )
        self.assertNotIn("rm -rf", block)
        self.assertNotIn("whoami", block)


class OversizeMessageTest(unittest.TestCase):
    def msg(self):
        m = pss.measure(
            None, {"changedFiles": 480, "additions": 60_000, "deletions": 5_000}
        )
        return pss.oversize_message(m)

    def test_it_names_the_actual_size(self):
        text = self.msg()
        self.assertIn("480", text)
        self.assertIn("65,000", text)

    def test_it_asks_for_the_pull_request_to_be_split(self):
        self.assertRegex(self.msg(), r"(?i)split")

    def test_it_names_the_limit_that_was_exceeded(self):
        text = self.msg()
        self.assertIn(f"{pss.OVERSIZED_FILES:,}", text)
        self.assertIn(f"{pss.OVERSIZED_LINES:,}", text)

    def test_it_makes_no_claim_about_authentication(self):
        """DRE-2465: the critic's failure notice blamed the credential when
        the reviewer had actually run, and that cost a day of
        credential-hunting. This path knows exactly why it stopped — it must
        say that and nothing else."""
        text = self.msg().lower()
        for word in ("auth", "credential", "token", "secret", "login",
                     "infra error", "infrastructure"):
            with self.subTest(word=word):
                self.assertNotIn(word, text)

    def test_it_says_plainly_that_this_is_not_a_code_rejection(self):
        self.assertRegex(self.msg(), r"(?i)not a (code )?rejection")

    def test_it_carries_the_qa_critic_marker_but_no_approve_verdict(self):
        """merge-gate reads the latest `QA Critic` comment: without the
        marker a stale APPROVE would still stand, and with a VERDICT:
        APPROVE line this message would BE an approval."""
        text = self.msg()
        self.assertIn("QA Critic", text)
        self.assertNotIn("VERDICT: APPROVE", text)


# ── 5. the CLI contract the workflow uses ──────────────────────────────────

class CliTest(unittest.TestCase):
    def run_cli(self, compare_record=None, pr_json=None, pr="297"):
        with tempfile.TemporaryDirectory() as td:
            cmp_path = os.path.join(td, "compare.json")
            pr_path = os.path.join(td, "size.json")
            out_path = os.path.join(td, "out.txt")
            with open(cmp_path, "w") as f:
                json.dump(compare_record if compare_record is not None else {}, f)
            with open(pr_path, "w") as f:
                json.dump(pr_json if pr_json is not None else {}, f)
            env = dict(os.environ, GITHUB_OUTPUT=out_path)
            proc = subprocess.run(
                [sys.executable, os.path.join(SCRIPTS, "pr_size_strategy.py"),
                 "--compare-file", cmp_path, "--pr-json-file", pr_path,
                 "--pr", pr],
                capture_output=True, text=True, env=env,
            )
            return proc, open(out_path).read()

    def outputs(self, raw):
        """Parse $GITHUB_OUTPUT (scalars + heredoc blocks)."""
        out, lines, i = {}, raw.splitlines(), 0
        while i < len(lines):
            line = lines[i]
            if "<<" in line:
                name, delim = line.split("<<", 1)
                body = []
                i += 1
                while i < len(lines) and lines[i] != delim:
                    body.append(lines[i])
                    i += 1
                out[name] = "\n".join(body)
            elif "=" in line:
                name, value = line.split("=", 1)
                out[name] = value
            i += 1
        return out

    def test_it_publishes_every_output_the_workflow_reads(self):
        proc, raw = self.run_cli(
            pr_json={"changedFiles": 118, "additions": 16_909, "deletions": 628}
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = self.outputs(raw)
        for key in ("strategy", "files", "lines", "review_files", "review_lines",
                    "max_turns", "retry_max_turns", "strategy_context",
                    "oversize_message"):
            with self.subTest(key=key):
                self.assertIn(key, out)
                self.assertTrue(out[key].strip(), f"{key} is empty")
        self.assertEqual(out["strategy"], "large")
        self.assertEqual(out["files"], "118")
        self.assertEqual(out["lines"], "17537")
        self.assertEqual(
            (out["max_turns"], out["retry_max_turns"]),
            tuple(str(v) for v in pss.turn_budget("large")),
        )

    def test_the_chosen_path_and_the_measured_size_reach_the_run_log(self):
        """AC 5. A strategy switch nobody can see in the log is unauditable —
        the whole diagnosis of #297 came from reading run records."""
        proc, _ = self.run_cli(
            pr_json={"changedFiles": 118, "additions": 16_909, "deletions": 628}
        )
        log = proc.stdout + proc.stderr
        self.assertIn("large", log)
        self.assertIn("118", log)
        self.assertIn("17,537", log)
        self.assertRegex(log, r"(?i)(because|reason|threshold)")

    def test_standard_is_logged_too(self):
        proc, _ = self.run_cli(compare_record=compare([("a.py", 3, 1)]))
        self.assertIn("standard", proc.stdout + proc.stderr)

    def test_unreadable_inputs_degrade_to_standard_and_never_fail(self):
        """A context-builder failure must degrade the review, not wedge the
        gate (repair_context.py's rule). Standard is today's behavior."""
        with tempfile.TemporaryDirectory() as td:
            out_path = os.path.join(td, "out.txt")
            env = dict(os.environ, GITHUB_OUTPUT=out_path)
            proc = subprocess.run(
                [sys.executable, os.path.join(SCRIPTS, "pr_size_strategy.py"),
                 "--compare-file", os.path.join(td, "nope.json"),
                 "--pr-json-file", os.path.join(td, "also-nope.json"),
                 "--pr", "1"],
                capture_output=True, text=True, env=env,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            out = self.outputs(open(out_path).read())
        self.assertEqual(out["strategy"], "standard")
        self.assertEqual(out["max_turns"], str(pss.turn_budget("standard")[0]))


# ── 6. the verdict stub — readable early, never a real verdict ─────────────

class UnfinishedVerdictTest(unittest.TestCase):
    def real(self, text):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "qa-verdict.md")
            with open(path, "w") as f:
                f.write(text)
            return check_critic_result.verdict_is_real({"is_error": False}, path)

    def stub(self):
        return (
            "VERDICT: REQUEST_CHANGES\n"
            f"{check_critic_result.INCOMPLETE_MARKER}\n"
            "## Summary\nThis review has not finished.\n"
        )

    def test_the_stub_is_not_a_real_verdict(self):
        """It exists so an early finish leaves something readable — not so a
        review that never happened can post REQUEST_CHANGES with no findings
        and wake the fix agent (#1441/#1442, DRE-1330/1332)."""
        self.assertFalse(self.real(self.stub()))

    def test_a_finished_verdict_is_still_real(self):
        self.assertTrue(self.real("VERDICT: APPROVE\n## Summary\nAll good.\n"))

    def test_a_marker_quoted_in_the_findings_does_not_void_the_verdict(self):
        """THE self-referential hazard: a verdict REVIEWING this code may
        quote the marker in its findings section. Only the stub's own header
        position counts, so an honest review of the gate itself survives."""
        text = (
            "VERDICT: REQUEST_CHANGES\n"
            "## Summary\nThe change is not ready.\n"
            "## For the fixing agent\n"
            f"check_critic_result.py:1 — the marker `{check_critic_result.INCOMPLETE_MARKER}` "
            "is never removed by the prompt.\n"
        )
        self.assertTrue(self.real(text))

    def test_a_max_turns_death_over_an_unfinished_verdict_is_not_real(self):
        """DRE-2422 lets a turn-ceiling death keep a COMPLETE verdict. An
        unfinished one is exactly what that exception must not rescue."""
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "qa-verdict.md")
            with open(path, "w") as f:
                f.write(self.stub())
            self.assertFalse(check_critic_result.verdict_is_real(
                {"is_error": True, "subtype": "error_max_turns", "num_turns": 90},
                path,
            ))


# ── 7. wiring: the workflow actually carries all of it ─────────────────────

class WorkflowWiringTest(unittest.TestCase):
    def test_the_size_is_measured_before_the_critic_runs(self):
        ids = [s.get("id") for s in wf_steps()]
        self.assertIn("size", ids)
        self.assertLess(ids.index("size"), ids.index("critic"))
        self.assertIn("pr_size_strategy.py", wf_step("size")["run"])

    def test_the_size_step_reads_records_the_workflow_already_fetches(self):
        run = wf_step("size")["run"]
        self.assertIn("/tmp/qa-compare.json", run)
        resolve = wf_step("pr")["run"]
        self.assertIn("/tmp/qa-compare.json", resolve)
        # The authoritative totals, from the same `gh pr view` the step
        # already calls — GitHub's compare record truncates at 300 files.
        self.assertIn("changedFiles", resolve)

    def test_the_size_step_cannot_leave_the_critic_without_a_turn_ceiling(self):
        """claude_args interpolates these outputs. An empty one would hand
        the action `--max-turns` with no value."""
        run = wf_step("size")["run"]
        self.assertRegex(run, r"max_turns=\d+")
        self.assertRegex(run, r"strategy=standard")

    def test_both_critic_blocks_take_their_ceiling_from_the_size_step(self):
        a1 = wf_step("critic")["with"]["claude_args"]
        rt = wf_step("critic_retry")["with"]["claude_args"]
        self.assertIn("steps.size.outputs.max_turns", a1)
        self.assertIn("steps.size.outputs.retry_max_turns", rt)

    def test_both_critic_prompts_receive_the_strategy_block(self):
        for prompt in critic_prompts():
            self.assertIn("steps.size.outputs.strategy_context", prompt)

    def test_both_critic_prompts_keep_a_static_fallback_strategy(self):
        """If the size step dies entirely the block is empty — the prompt
        must still say how to review (repair_context.py's discipline)."""
        for prompt in critic_prompts():
            self.assertRegex(prompt, r"(?i)fallback")

    def test_both_critic_prompts_order_the_verdict_file_written_first(self):
        """AC 3, and the marker is the CONTRACT with the gate — the prompt
        and check_critic_result.py must name the same string or the stub
        silently becomes a postable verdict."""
        for prompt in critic_prompts():
            self.assertIn("/tmp/qa-verdict.md", prompt)
            self.assertIn(check_critic_result.INCOMPLETE_MARKER, prompt)
            self.assertRegex(prompt, r"(?i)before you (read|review)")
            self.assertRegex(prompt, r"(?i)never write .?VERDICT: APPROVE")

    def test_no_critic_inference_is_spent_on_an_oversized_pull_request(self):
        """The point of the second threshold: stop paying full price to
        fail. Every step of the review chain must be gated, or the retry
        chain runs on an empty gate output and bills the same $12."""
        for step_id in ("critic", "gate1", "critic_retry", "gate2"):
            with self.subTest(step=step_id):
                self.assertIn(
                    "steps.size.outputs.strategy != 'oversized'",
                    wf_step(step_id)["if"],
                )

    def test_the_backoff_sleep_is_skipped_on_an_oversized_pull_request(self):
        sleeps = [s for s in wf_steps() if "sleep 120" in (s.get("run") or "")]
        self.assertTrue(sleeps)
        for step in sleeps:
            self.assertIn("steps.size.outputs.strategy != 'oversized'",
                          step.get("if", ""))

    def test_the_oversized_path_fails_the_job_with_the_size_named(self):
        step = wf_step("oversize_fail")
        self.assertIn("steps.size.outputs.strategy == 'oversized'", step["if"])
        run = step["run"]
        self.assertIn("::error::", run)
        for token in ("steps.size.outputs.files", "steps.size.outputs.lines"):
            with self.subTest(token=token):
                self.assertIn(token, run)
        self.assertIn("exit 1", run)

    def test_the_crash_failure_step_does_not_double_fire_on_oversized(self):
        """`Fail if critic never really ran` reads gate outputs that never
        exist on the oversized path — unguarded it would post the crash
        message over the real reason."""
        for step in wf_steps():
            if "crashed on both attempts" in (step.get("run") or ""):
                self.assertIn("steps.size.outputs.strategy != 'oversized'",
                              step["if"])
                break
        else:
            self.fail("the crash-failure step is gone — update this test")

    def test_the_oversized_comment_is_the_scripts_message_not_a_new_one(self):
        """One wording, one place. A second copy in bash drifts from the
        tested one."""
        post = wf_step("post")
        self.assertIn("oversize_message", json.dumps(post))

    def test_the_review_check_tells_the_truth_about_an_oversized_head(self):
        """publish_review_check.py's `--real false` summary says the reviewer
        hit an infrastructure failure. On this path it did not — it read the
        size and declined."""
        self.assertIn("--too-large", src())

    def test_the_job_has_wall_clock_for_the_raised_ceiling(self):
        """A ceiling the job timeout cannot reach is not a raised ceiling.

        A timed-out job is CANCELLED, and a cancelled job skips even its
        `always()` steps — no verdict comment, no head-bound check, nothing
        to read. At the ~5-7 seconds a turn portico's completed reviews ran
        at, the large first attempt alone needs more than 25 minutes.
        """
        doc = yaml.safe_load(open(os.path.join(WF_DIR, "qa-review.yml")))
        timeout = doc["jobs"]["review"]["timeout-minutes"]
        first = pss.turn_budget("large")[0]
        self.assertGreaterEqual(timeout, first * 7 / 60)

    def test_show_full_output_stays_off(self):
        """Explicit card constraint — the transcript dump is not the
        diagnostic channel (tests/test_execution_failure_detail.py)."""
        self.assertNotIn("show_full_output", src())


class OversizedPostStepScenarioTest(unittest.TestCase):
    """EXECUTE the real post block from qa-review.yml on the oversized path.

    Grepping the YAML proves the branch exists; it does not prove the branch
    runs. This one does: the step's own `run:` body, with the expressions
    Actions would substitute, against a temp filesystem and a fake `gh` that
    records what it was asked to post. (Same discipline as
    tests/test_qa_review_model_note.py, which caught a shell-level break in
    this exact step.)
    """

    def _run_post(self, td, **env_extra):
        run = wf_step("post")["run"]
        run = re.sub(r"\$\{\{[^}]*\}\}", "", run)  # env-only step; none survive
        self.assertNotIn("${{", run)
        os.mkdir(os.path.join(td, "bin"))
        log = os.path.join(td, "calls.log")
        gh = os.path.join(td, "bin", "gh")
        with open(gh, "w") as f:
            f.write("#!/usr/bin/env bash\n"
                    f'printf "gh %s\\n" "$*" >> {log}\n'
                    "exit 0\n")
        os.chmod(gh, 0o755)
        script = os.path.join(td, "post.sh")
        with open(script, "w") as f:
            # -u on purpose: the runner does not set it, but an unbound
            # variable here is a latent break the moment anyone does.
            f.write("set -euo pipefail\n" + run.replace("/tmp/", td + "/"))
        env = dict(os.environ, PATH=f"{td}/bin:{os.environ['PATH']}",
                   CARD="", REAL="false", PR="297",
                   REVIEWED_SHA="a" * 40, CONTENT_ID="", MODEL_ID="m",
                   MODEL_WHY="why")
        env.update(env_extra)
        proc = subprocess.run(["bash", script], cwd=td, env=env,
                              capture_output=True, text=True)
        comment = os.path.join(td, "qa-comment.md")
        body = open(comment).read() if os.path.exists(comment) else ""
        return proc, body, open(log).read() if os.path.exists(log) else ""

    def test_the_oversized_branch_posts_the_size_message(self):
        m = pss.measure(
            None, {"changedFiles": 480, "additions": 60_000, "deletions": 5_000}
        )
        with tempfile.TemporaryDirectory() as td:
            proc, body, calls = self._run_post(
                td, STRATEGY="oversized", OVERSIZE_MESSAGE=pss.oversize_message(m)
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("480", body)
        self.assertRegex(body, r"(?i)split")
        self.assertNotIn("VERDICT: APPROVE", body)
        self.assertNotIn("VERDICT: REQUEST_CHANGES", body)
        self.assertNotIn("auth", body.lower())
        self.assertIn("gh pr comment", calls)

    def test_a_normal_review_is_untouched_by_the_new_branch(self):
        with tempfile.TemporaryDirectory() as td:
            with open(os.path.join(td, "qa-verdict.md"), "w") as f:
                f.write("VERDICT: APPROVE\n\nLooks good.\n")
            proc, body, _ = self._run_post(
                td, STRATEGY="standard", OVERSIZE_MESSAGE="", REAL="true"
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(body.startswith("🔎 QA Critic — VERDICT: APPROVE @"))


class PublishCheckOversizeTest(unittest.TestCase):
    def test_too_large_is_not_reported_as_a_crash(self):
        sys.path.insert(0, SCRIPTS)
        import publish_review_check as prc  # noqa: E402

        conclusion, title, summary = prc.decide(
            False, "", too_large="480 files, 65,000 changed lines"
        )
        self.assertEqual(conclusion, "failure")
        self.assertIn("480 files", summary)
        self.assertRegex(title + summary, r"(?i)too large|split")
        for word in ("auth", "credential", "infrastructure"):
            with self.subTest(word=word):
                self.assertNotIn(word, (title + summary).lower())


if __name__ == "__main__":
    unittest.main()
