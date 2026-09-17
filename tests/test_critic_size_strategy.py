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
  * 10 files / 1,500 changed lines — above every review the critic has been
    OBSERVED to finish, and below the smallest one it demonstrably could
    not. LOWERED FROM 50 / 5,000 by DRE-2924: that pair was read off an
    older sample (#275 at +4,092 across 29 files, #290 at 41 files) and it
    routed portico PR #364 (18 files / 2,059 lines) to the one-pass review,
    which then failed to produce a verdict twice. The measurement is in
    tests/test_critic_turn_wall.py and beside the constant itself.
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


# ── removal fixtures (DRE-3995) ────────────────────────────────────────────

def removed(n_files, lines_each, prefix="archive/f"):
    """Per-file records for whole-file removals, as the files API reports
    them: no additions, every line a deletion, `status: removed`."""
    return [
        {"filename": f"{prefix}{i}.py", "additions": 0,
         "deletions": lines_each, "status": "removed"}
        for i in range(n_files)
    ]


def touched(path, additions=0, deletions=0, status="modified"):
    return {"filename": path, "additions": additions, "deletions": deletions,
            "status": status}


#: agent-bureau PR #2571 (DRE-3979) as GitHub reports it: 3,080 changed
#: files, of which 3,075 are whole-file removals of the retired v1 platform
#: under `archive/` carrying 2,094,946 deleted lines, and 5 modified files
#: carrying the 87 added lines a reviewer must actually read (a guard test
#: and four doc pointers). Refused four times since 2026-09-14 21:26 PT as
#: "too large to review (3,080 files, 2,095,033 changed lines)" — a pull
#: request that cannot merge without a verdict, and whose real review is
#: five files long.
PR_2571_FILES = 3_080
PR_2571_REMOVED = 3_075
PR_2571_DELETED = 2_094_946
PR_2571_ADDED = 87
PR_2571_MODIFIED = PR_2571_FILES - PR_2571_REMOVED


def pr_2571_files():
    """The per-file records for #2571, summing to exactly its totals."""
    each, extra = divmod(PR_2571_DELETED, PR_2571_REMOVED)
    entries = removed(PR_2571_REMOVED, each)
    entries[0]["deletions"] += extra
    add_each, add_extra = divmod(PR_2571_ADDED, PR_2571_MODIFIED)
    entries += [touched(f"docs/pointer{i}.md", additions=add_each)
                for i in range(PR_2571_MODIFIED - 1)]
    entries.append(touched("tests/test_archive_stays_gone.py",
                           additions=add_each + add_extra))
    return entries


def pr_2571_totals():
    return {"changedFiles": PR_2571_FILES, "additions": PR_2571_ADDED,
            "deletions": PR_2571_DELETED}


def parse_outputs(raw):
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


#: The per-turn cost the wall-clock budget is sized against — the upper end
#: of the 5-7 s/turn portico's COMPLETED reviews ran at. Budgeting on the
#: fast end would size the job for the runs that were never the problem.
SECONDS_PER_TURN = 7

#: Minutes the two-attempt turn arithmetic does not model: minting the
#: qa-bot token, two checkouts, should_review_pr + the compare-record fetch
#: (its own retry/backoff loop), context assembly, model selection, the
#: verdict-posting retry loop (4 attempts, 10+20+30 s of sleeps) — and, on a
#: PR carrying a `**Design:**` ref, "Render affected screens" pulling an
#: `npm ci` plus a chromium download before it renders anything.
JOB_OVERHEAD_MINUTES = 15


def retry_backoff_seconds(workflow="qa-review.yml", job="review"):
    """The mandatory sleep between critic attempt 1 and the retry, read from
    the step itself — the wall-clock budget has to move when it does."""
    for step in wf_steps(workflow, job):
        if "Back off before the critic retry" in (step.get("name") or ""):
            found = re.search(r"\bsleep\s+(\d+)", step.get("run") or "")
            if not found:
                raise AssertionError("the retry backoff no longer sleeps — "
                                     "update this helper")
            return int(found.group(1))
    raise AssertionError("the retry backoff step is gone — update this helper")


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
        later edit that drops them below it has to fail here.

        DRE-2924 REPLACED THE SAMPLE. #275 (29 files) and #290 (41 files)
        used to be listed here as proof that 50 / 5,000 was safe. They were
        real successes and they are no longer the evidence that governs:
        measured against the SAME critic on the night of 2026-08-31, every
        review that finished was <= 6 files / ~1,030 lines and the one at 18
        files / 2,059 lines could not finish, twice. So the proven set is now
        the observed one, and the larger historical PRs get the file-list
        strategy — which reviews them, with more turns, rather than refusing
        them. tests/test_critic_turn_wall.py holds the full measurement.
        """
        proven = {
            "#296 (+278/-11, 4 files)": compare(
                [(f"f{i}.py", 70, 3) for i in range(4)]
            ),
            "#361 (507 lines, 4 files)": compare(
                [(f"f{i}.py", 507 // 4, 0) for i in range(4)]
            ),
            "#362 (975 lines, 5 files)": compare(
                [(f"f{i}.py", 975 // 5, 0) for i in range(5)]
            ),
            "#363 (1,026 lines, 6 files)": compare(
                [(f"f{i}.py", 1026 // 6, 0) for i in range(6)]
            ),
        }
        for name, rec in proven.items():
            with self.subTest(pr=name):
                self.assertEqual(strategy_for(rec), "standard")

    def test_portico_364_is_no_longer_routed_one_pass(self):
        """The PR DRE-2924 is about: 18 files / 2,059 lines, two runs, no
        verdict either time. It must reach the multi-pass strategy."""
        m = pss.measure(
            None, {"changedFiles": 18, "additions": 2_059, "deletions": 0}
        )
        self.assertEqual(pss.choose(m), "large")

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

    def test_the_per_file_command_it_hands_the_critic_actually_works(self):
        """The one instruction in the block that can fail SILENTLY.

        A command that prints nothing looks like a clean file, and the
        critic would spend the turns this strategy exists to save finding
        that out. So run it — on a real multi-file diff, for a path
        containing the regex metacharacters a real path contains.
        """
        diff = (
            "diff --git a/src/app.config.ts b/src/app.config.ts\n"
            "--- a/src/app.config.ts\n+++ b/src/app.config.ts\n"
            "@@ -1 +1 @@\n-old\n+new\n"
            "diff --git a/src/other.ts b/src/other.ts\n"
            "--- a/src/other.ts\n+++ b/src/other.ts\n"
            "@@ -1 +1 @@\n-untouched\n+by the filter\n"
        )
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "qa-full.diff")
            with open(path, "w") as f:
                f.write(diff)
            command = pss.PER_FILE_HUNKS.replace(
                "<path>", "src/app.config.ts"
            ).replace("/tmp/qa-full.diff", path)
            out = subprocess.run(["bash", "-c", command],
                                 capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("+new", out.stdout)
        self.assertNotIn("by the filter", out.stdout)
        self.assertTrue(out.stdout.startswith("diff --git a/src/app.config.ts"))

    def test_the_large_block_hands_over_that_exact_command(self):
        self.assertIn(pss.PER_FILE_HUNKS, self.ctx("large"))

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
        return parse_outputs(raw)

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
        to read. That is the exact #297 outcome this card exists to remove,
        so it must not be reintroduced one size class up.

        The wall clock has to hold the WHOLE cycle the job can run, not the
        first attempt: attempt 1 + the mandatory retry backoff + the retry,
        at the ~7 s/turn upper end portico's completed reviews ran at, plus
        the fixed overhead the turn arithmetic does not model. Asserting
        against the first attempt alone was trivially satisfied (45 ≥ 17.5)
        and hid a 42.8-minute worst case inside a 45-minute budget.

        Both inputs are READ from the workflow and the budget table, so
        raising `TURN_BUDGET["large"]` or the backoff sleep re-opens this
        test rather than silently re-opening the gap.
        """
        doc = yaml.safe_load(open(os.path.join(WF_DIR, "qa-review.yml")))
        timeout = doc["jobs"]["review"]["timeout-minutes"]
        first, retry = pss.turn_budget("large")
        backoff = retry_backoff_seconds()
        needed = (
            (first + retry) * SECONDS_PER_TURN + backoff
        ) / 60 + JOB_OVERHEAD_MINUTES
        self.assertGreaterEqual(
            timeout,
            needed,
            f"timeout-minutes: {timeout} cannot hold the large strategy's "
            f"full cycle ({first}+{retry} turns at {SECONDS_PER_TURN}s + "
            f"{backoff}s backoff + {JOB_OVERHEAD_MINUTES} min overhead = "
            f"{needed:.1f} min) — a review that runs long is cancelled and "
            f"posts nothing",
        )

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
                   # DRE-3226: the checkout outside the workspace the post
                   # step runs its scripts out of. This repo is one.
                   PIPELINE_DIR=os.path.abspath(REPO),
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


# ── 8. whole-file removals are not review work (DRE-3995) ──────────────────

class RemovedFilesTest(unittest.TestCase):
    """THE BUG: a pull request is sized by `additions + deletions` over every
    changed file, so one that only REMOVES files is refused by its own size
    even when the part a reviewer must read is tiny. agent-bureau #2571 was
    refused four times for 3,080 files / 2,095,033 changed lines, of which 87
    lines are the review.

    The review question for a removed file is not what its deleted lines
    said — it is whether anything still live imports, runs or links into that
    path, and that is answered from the LIST of removed paths.
    """

    def m2571(self):
        return pss.measure(None, pr_2571_totals(), files=pr_2571_files())

    def test_pr_2571_is_not_refused_for_a_size_it_does_not_have(self):
        """AC 1 — the headline. Never `oversized`."""
        self.assertIn(pss.choose(self.m2571()), ("standard", "large"))

    def test_the_reviewable_part_is_the_five_files_a_reviewer_reads(self):
        m = self.m2571()
        self.assertEqual(m["removed_files"], PR_2571_REMOVED)
        self.assertEqual(m["removed_lines"], PR_2571_DELETED)
        self.assertEqual(m["review_files"], PR_2571_MODIFIED)
        self.assertEqual(m["review_lines"], PR_2571_ADDED)

    def test_the_totals_still_describe_the_whole_pull_request(self):
        """The discount changes what is REVIEWABLE, never what is reported:
        the `[qa-size]` log and the size phrase still name the real diff."""
        m = self.m2571()
        self.assertEqual(m["files"], PR_2571_FILES)
        self.assertEqual(m["lines"], PR_2571_DELETED + PR_2571_ADDED)

    def test_a_removal_diff_never_gets_the_one_pass_block(self):
        """The `standard` block orders `gh pr diff` read WHOLE, and the two
        million removed lines are in that diff whether or not they are review
        work. Deciding on the reviewable size must not hand the critic the
        one instruction its size makes impossible."""
        self.assertEqual(pss.choose(self.m2571()), "large")

    def test_removals_do_not_hide_additions(self):
        """AC 2. 3,000 removed files and 25,000 added lines is still a
        25,000-line review."""
        entries = removed(3_000, 500) + [touched("src/app.py", additions=25_000)]
        m = pss.measure(
            None,
            {"changedFiles": 3_001, "additions": 25_000, "deletions": 1_500_000},
            files=entries,
        )
        self.assertEqual(pss.choose(m), "oversized")

    def test_only_whole_file_removals_are_discounted(self):
        """AC 3 — deleted lines in a MODIFIED file are ordinary review work:
        the reviewer has to read what replaced them."""
        entries = [touched("src/rewritten.py", additions=12, deletions=20_001)]
        m = pss.measure(
            None, {"changedFiles": 1, "additions": 12, "deletions": 20_001},
            files=entries,
        )
        self.assertEqual(m["removed_files"], 0)
        self.assertEqual(m["review_lines"], 20_013)
        self.assertEqual(pss.choose(m), "oversized")

    def test_a_renamed_file_is_not_a_removal(self):
        """GitHub reports a rename as its own status. Only `removed` is a
        whole-file removal; everything else is read."""
        entries = [touched("src/new.py", additions=2_000, deletions=2_000,
                           status="renamed")]
        m = pss.measure(
            None, {"changedFiles": 1, "additions": 2_000, "deletions": 2_000},
            files=entries,
        )
        self.assertEqual(m["removed_files"], 0)
        self.assertEqual(m["review_lines"], 4_000)

    def test_a_removed_generated_file_is_discounted_exactly_once(self):
        """Both discounts subtract from the same total — counting a removed
        lock file twice would drive the reviewable size negative."""
        entries = [{"filename": "dist/bundle.js", "additions": 0,
                    "deletions": 900, "status": "removed"}]
        m = pss.measure(
            None, {"changedFiles": 1, "additions": 0, "deletions": 900},
            files=entries,
        )
        self.assertEqual(m["removed_files"] + m["generated_files"], 1)
        self.assertEqual(m["review_lines"], 0)
        self.assertEqual(m["review_files"], 0)

    def test_a_handful_of_removed_files_keeps_the_one_pass_review(self):
        """The floor above is about the size of the raw diff, not about the
        word `removed`: four small files still fit one pass."""
        entries = [touched("src/a.py", additions=4)] + removed(3, 12, "old/f")
        m = pss.measure(
            None, {"changedFiles": 4, "additions": 4, "deletions": 36},
            files=entries,
        )
        self.assertEqual(pss.choose(m), "standard")

    def test_the_compare_record_carries_the_same_statuses(self):
        """Under 300 files the compare record the workflow already fetches
        answers the question — a files-API blip must not lose the discount."""
        rec = {"files": removed(6, 400) + [touched("src/a.py", additions=10)]}
        m = pss.measure(rec, {"changedFiles": 7, "additions": 10,
                              "deletions": 2_400})
        self.assertEqual(m["removed_files"], 6)
        self.assertEqual(m["review_lines"], 10)

    def test_a_record_with_no_statuses_sizes_exactly_as_before(self):
        """Every existing caller hands `measure()` records without a
        `status` field. Nothing about them may move."""
        m = pss.measure(compare([("a.py", 10, 5), ("b.py", 1, 2)]), None)
        self.assertEqual((m["removed_files"], m["removed_lines"]), (0, 0))
        self.assertEqual((m["review_files"], m["review_lines"]), (2, 18))

    def test_the_tail_past_the_api_cap_does_not_refuse_the_pull_request(self):
        """GitHub's files API stops at 3,000 records and #2571 changes 3,080
        files, so 80 files' statuses are UNKNOWABLE. Counting their ~51,000
        deleted lines as review work would refuse the pull request on lines
        nobody has to read, which is the whole defect this card is about — so
        the tail's DELETIONS are split the way the deletions we could see
        were split."""
        seen = pr_2571_files()[:pss.FILES_API_MAX]
        m = pss.measure(None, pr_2571_totals(), files=seen)
        self.assertNotEqual(pss.choose(m), "oversized")
        self.assertLessEqual(m["review_lines"], PR_2571_ADDED)

    def test_the_unseen_tail_never_reports_lines_to_read_in_zero_files(self):
        """87 reviewable lines cannot live in 0 reviewable files, and the
        truncated #2571 record produced exactly that: every SEEN file was a
        removal, so the count ratio attributed all 80 unseen files to
        removals — including the five carrying the 87 added lines. A run
        record that contradicts the numbers printed beside it is the failure
        this module exists to stop (DRE-2465), so the attribution stops one
        file short whenever the tail carries lines a removal cannot own."""
        seen = pr_2571_files()[:pss.FILES_API_MAX]
        m = pss.measure(None, pr_2571_totals(), files=seen)
        self.assertEqual(m["review_lines"], PR_2571_ADDED)
        self.assertGreater(m["review_files"], 0)
        self.assertLess(m["unseen_removed_files"], m["unseen_files"])
        self.assertNotIn("0 files / 87 lines reviewable",
                         pss.summary_line(m, pss.choose(m)))

    def test_a_tail_that_really_is_all_removals_is_still_attributed_whole(self):
        """The guard above must not over-correct: where the tail adds nothing
        and every one of its deleted lines is attributed to a removal, there
        are no lines needing a file to live in and the whole tail counts."""
        m = pss.measure(
            None,
            {"changedFiles": 3_050, "additions": 0, "deletions": 1_830_000},
            files=removed(3_000, 600),
        )
        self.assertEqual(m["unseen_removed_files"], m["unseen_files"])
        self.assertEqual((m["review_files"], m["review_lines"]), (0, 0))

    def test_additions_in_the_unseen_tail_are_never_attributed_to_a_removal(self):
        """The guard on that attribution: a removed file has no additions, so
        added lines are never discounted, seen or unseen. 3,000 removals in
        front of 25,000 added lines is still oversized."""
        m = pss.measure(
            None,
            {"changedFiles": 3_050, "additions": 25_000, "deletions": 1_830_000},
            files=removed(3_000, 600),
        )
        self.assertEqual(pss.choose(m), "oversized")

    def test_a_tail_of_ordinary_edits_is_still_counted_in_full(self):
        """Attribution follows what was SEEN. A truncated record showing
        ordinary modifications attributes nothing — today's behavior, and the
        direction that errs toward reviewing."""
        m = pss.measure(
            compare(code(300, 20)),
            {"changedFiles": 412, "additions": 30_000, "deletions": 1_000},
        )
        self.assertEqual(m["review_lines"], 31_000)
        self.assertEqual(pss.choose(m), "oversized")


class RemovalSummaryTest(unittest.TestCase):
    """AC 4 — the `[qa-size]` log says what was counted as a removal."""

    def test_the_size_line_names_the_removed_files_and_lines(self):
        m = pss.measure(None, pr_2571_totals(), files=pr_2571_files())
        line = pss.summary_line(m, pss.choose(m))
        self.assertIn("[qa-size]", line)
        self.assertIn(f"{PR_2571_REMOVED:,}", line)
        self.assertIn(f"{PR_2571_DELETED:,}", line)
        self.assertRegex(line, r"(?i)removed")

    def test_the_line_names_the_reason_that_actually_decided_it(self):
        """#2571 reaches `large` with a reviewable size of 5 files / 87 lines
        — UNDER the one-pass threshold. Saying it is "past the one-pass
        threshold (10 files / 1,500 lines)" would be a run record that
        contradicts itself, and this module exists because a run record was
        misread for a day (DRE-2465)."""
        m = pss.measure(None, pr_2571_totals(), files=pr_2571_files())
        line = pss.summary_line(m, "large")
        self.assertIn("within the one-pass threshold", line)
        self.assertNotIn("is past the one-pass threshold", line)
        self.assertRegex(line, r"(?i)removed lines are in the diff")

    def test_a_genuinely_large_pull_request_still_names_the_threshold(self):
        m = pss.measure(None, {"changedFiles": 118, "additions": 16_909,
                               "deletions": 628})
        self.assertIn("is past the one-pass threshold",
                      pss.summary_line(m, "large"))

    def test_a_pull_request_with_no_removals_says_nothing_about_them(self):
        line = pss.summary_line(pss.measure(compare([("a.py", 3, 1)]), None),
                                "standard")
        self.assertNotRegex(line, r"(?i)removed")

    def test_the_refusal_message_says_removals_do_not_count(self):
        """A pull request that IS oversized on its own additions must not be
        told to split off the part that was never counted."""
        m = pss.measure(
            None, {"changedFiles": 480, "additions": 60_000, "deletions": 5_000}
        )
        self.assertRegex(pss.oversize_message(m), r"(?i)removed")


class RemovalContextTest(unittest.TestCase):
    """AC 5 — what the critic is told about a pull request with removals."""

    def block(self, m=None, pr="2571"):
        m = m if m is not None else pss.measure(
            None, pr_2571_totals(), files=pr_2571_files())
        return pss.strategy_context(pss.choose(m), m, pr)

    def test_it_asks_for_the_live_reference_check(self):
        block = self.block().lower()
        self.assertIn("removed path", block)
        for token in ("import", "execute", "link"):
            with self.subTest(token=token):
                self.assertIn(token, block)
        self.assertRegex(block, r"(?i)blocking finding")

    def test_a_long_removal_list_collapses_to_top_level_directories(self):
        """3,075 paths pasted into a prompt is the context dump this module
        exists to prevent — the critic gets the directories and the counts."""
        block = self.block()
        self.assertIn("archive/", block)
        self.assertNotIn("archive/f2000.py", block)
        self.assertLess(len(block.splitlines()), 120)
        self.assertIn(f"{PR_2571_REMOVED:,}", block)

    def test_a_short_removal_list_is_given_in_full(self):
        entries = removed(3, 20, "old/gone") + [touched("src/a.py", additions=4)]
        m = pss.measure(None, {"changedFiles": 4, "additions": 4,
                               "deletions": 60}, files=entries)
        block = pss.strategy_context(pss.choose(m), m, "42")
        for i in range(3):
            with self.subTest(path=i):
                self.assertIn(f"old/gone{i}.py", block)

    def test_a_pull_request_with_no_removals_gets_no_removal_block(self):
        for strategy in ("standard", "large"):
            with self.subTest(strategy=strategy):
                block = pss.strategy_context(
                    strategy, pss.measure(compare([("a.py", 3, 1)]), None), "1")
                self.assertNotIn("removed path", block.lower())


class PaginatedFilesApiTest(unittest.TestCase):
    """AC 4 — statuses are read PAST the compare record's 300-file cap.

    The compare record the workflow already fetches truncates at 300 entries
    and does not paginate (verdict_content.py documents the same cap), so on
    a 3,080-file pull request it can see 300 of the 3,075 removals. The
    per-file statuses therefore come from the paginated PR files API, and
    these tests drive that path with `gh` stubbed.
    """

    STUB = '''#!/usr/bin/env python3
import json, re, sys

REMOVED, DELETED, ADDED, MODIFIED = {removed}, {deleted}, {added}, {modified}
with open({log!r}, "a") as fh:
    fh.write(" ".join(sys.argv[1:]) + "\\n")
each, extra = divmod(DELETED, REMOVED)
entries = [{{"filename": "archive/f%d.py" % i, "additions": 0,
            "deletions": each + (extra if i == 0 else 0), "status": "removed"}}
           for i in range(REMOVED)]
entries += [{{"filename": "docs/pointer%d.md" % i, "additions": ADDED // MODIFIED,
             "deletions": 0, "status": "modified"}} for i in range(MODIFIED)]
url = sys.argv[-1]
if "/pulls/" not in url or "/files" not in url:
    sys.exit(1)
page = int(re.search(r"[?&]page=(\\d+)", url).group(1))
per = int(re.search(r"[?&]per_page=(\\d+)", url).group(1))
start = (page - 1) * per
print(json.dumps(entries[start:start + per]))
'''

    def stub_gh(self, td):
        os.mkdir(os.path.join(td, "bin"))
        log = os.path.join(td, "gh-calls.log")
        path = os.path.join(td, "bin", "gh")
        with open(path, "w") as fh:
            fh.write(self.STUB.format(
                removed=PR_2571_REMOVED, deleted=PR_2571_DELETED,
                added=PR_2571_ADDED, modified=PR_2571_MODIFIED, log=log))
        os.chmod(path, 0o755)
        return log

    def with_stub(self, td, fn):
        before = os.environ["PATH"]
        os.environ["PATH"] = os.path.join(td, "bin") + os.pathsep + before
        try:
            return fn()
        finally:
            os.environ["PATH"] = before

    def test_it_reads_statuses_past_the_three_hundred_file_cap(self):
        with tempfile.TemporaryDirectory() as td:
            log_path = self.stub_gh(td)
            files = self.with_stub(
                td, lambda: pss.fetch_pr_files(
                    "dreadnought-foundry/agent-bureau", "2571"))
            calls = open(log_path).read().splitlines()
        self.assertGreater(len(files), 300)
        self.assertEqual(len(files), min(PR_2571_FILES, pss.FILES_API_MAX))
        self.assertGreater(len(calls), 1, "it never paginated")
        self.assertGreaterEqual(
            sum(1 for f in files if f.get("status") == "removed"), 3_000)

    def test_it_stops_at_the_apis_own_three_thousand_file_ceiling(self):
        """GitHub returns at most 3,000 records however many pages are asked
        for. A loop with no ceiling would page forever on a bigger diff."""
        with tempfile.TemporaryDirectory() as td:
            log_path = self.stub_gh(td)
            self.with_stub(td, lambda: pss.fetch_pr_files("o/r", "2571"))
            calls = open(log_path).read().splitlines()
        self.assertLessEqual(len(calls), pss.FILES_API_MAX // 100 + 1)

    def test_a_failing_gh_call_is_not_a_failure(self):
        """Sizing never wedges the gate: no statuses simply means no
        discount, which is today's behavior."""
        with tempfile.TemporaryDirectory() as td:
            os.mkdir(os.path.join(td, "bin"))
            path = os.path.join(td, "bin", "gh")
            with open(path, "w") as fh:
                fh.write("#!/bin/sh\nexit 3\n")
            os.chmod(path, 0o755)
            self.assertEqual(
                self.with_stub(td, lambda: pss.fetch_pr_files("o/r", "1")), [])

    def test_a_hostile_pr_number_never_reaches_the_api_path(self):
        with tempfile.TemporaryDirectory() as td:
            log_path = self.stub_gh(td)
            self.with_stub(
                td, lambda: pss.fetch_pr_files("o/r", "1; rm -rf / #"))
            calls = open(log_path).read()
        self.assertNotIn("rm -rf", calls)

    def test_the_cli_sizes_the_whole_3080_file_pull_request(self):
        """End to end, the way the workflow runs it: a compare record
        truncated at 300 files, the authoritative totals, and the files API
        for the statuses. #2571 must come out reviewable."""
        truncated = {"files": pr_2571_files()[:300]}
        with tempfile.TemporaryDirectory() as td:
            self.stub_gh(td)
            cmp_path = os.path.join(td, "compare.json")
            pr_path = os.path.join(td, "size.json")
            out_path = os.path.join(td, "out.txt")
            with open(cmp_path, "w") as fh:
                json.dump(truncated, fh)
            with open(pr_path, "w") as fh:
                json.dump(pr_2571_totals(), fh)
            env = dict(os.environ, GITHUB_OUTPUT=out_path,
                       PATH=os.path.join(td, "bin") + os.pathsep
                       + os.environ["PATH"])
            proc = subprocess.run(
                [sys.executable, os.path.join(SCRIPTS, "pr_size_strategy.py"),
                 "--compare-file", cmp_path, "--pr-json-file", pr_path,
                 "--repo", "dreadnought-foundry/agent-bureau", "--pr", "2571"],
                capture_output=True, text=True, env=env,
            )
            out = parse_outputs(open(out_path).read())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotEqual(out["strategy"], "oversized")
        self.assertGreater(int(out["removed_files"]), 300)
        self.assertGreater(int(out["removed_lines"]), 2_000_000)
        # The published outputs have to agree with each other: the stub stops
        # at the API's 3,000-record ceiling, so this is the tail path, and
        # lines to read with no file to read them in is not a state.
        self.assertGreater(int(out["review_lines"]), 0)
        self.assertGreater(int(out["review_files"]), 0)
        self.assertIn("archive/", out["strategy_context"])
        self.assertRegex(proc.stdout, r"(?i)removed")


class RemovalWiringTest(unittest.TestCase):
    def test_the_size_step_can_reach_the_files_api(self):
        step = wf_step("size")
        self.assertIn("--repo", step["run"])
        self.assertIn("github.repository", step["run"])
        self.assertIn("GH_TOKEN", json.dumps(step["env"]))

    def test_the_static_fallback_publishes_the_removal_counts_too(self):
        """Every other output the script writes has a `||` fallback; a
        missing one reads as an empty string in `${{ }}`."""
        run = wf_step("size")["run"]
        self.assertRegex(run, r"removed_files=\d+")
        self.assertRegex(run, r"removed_lines=\d+")


if __name__ == "__main__":
    unittest.main()
