"""DRE-5224: agent-fix.yml's `Resolve PR, mode, and attempt budget` runs scripts/resolve_fix_pr.sh.

The step's shell moved out of the workflow into its own file, mechanically
(`scripts/step_shell.py move`, DRE-5380), and its seven `${{ }}` expressions
became two env inputs, PR_NUMBER and REPO. The suites that walk its branches
read it through `step_shell` (DRE-5222). This file pins the move itself: the
step delegates by the one line the contract allows, it keeps every input it
had plus the two the move adds, the script opens the way a `run:` block runs,
its header says what it does before what happened to it — and the file runs
as a file.

The end-to-end case runs `bash scripts/resolve_fix_pr.sh` by path, with no
rewriting, against the `gh` stub tests/test_hand_dispatch_no_work.py uses.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
AGENT_FIX = ROOT / ".github" / "workflows" / "agent-fix.yml"
SCRIPT = ROOT / "scripts" / "resolve_fix_pr.sh"
STEP = "Resolve PR, mode, and attempt budget"
DELEGATION = "bash .bureau-pipeline/scripts/resolve_fix_pr.sh"

sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import step_shell  # noqa: E402
from test_hand_dispatch_no_work import (  # noqa: E402
    ATTEMPT, GH_STUB, HUMAN, ROUND, WORKER, rest,
)

import fix_context  # noqa: E402
import linear_ops  # noqa: E402

#: The step's inputs: the five it had on `main` before the move, then the
#: two the move adds in place of its seven expressions.
ENV_KEYS = (
    "GH_TOKEN", "WRITER_TOKEN", "WORKER_LOGIN", "EVENT_NAME", "TRIGGERING_ACTOR",
    "PR_NUMBER", "REPO",
)

#: What the two new inputs carry — the expressions the block interpolated.
NEW_ENV = {
    "PR_NUMBER": "${{ github.event.issue.number || github.event.inputs.pr_number }}",
    "REPO": "${{ github.repository }}",
}

#: Every card the block's comments named on `main`; the move keeps them all.
REFERENCES = (
    "DRE-1927", "DRE-1988", "DRE-1995", "DRE-1996", "DRE-2024", "DRE-2053",
    "DRE-2316", "DRE-2409", "DRE-2548", "DRE-2813", "DRE-2817", "DRE-3451",
    "DRE-4109", "DRE-4139", "DRE-4157", "DRE-4407", "DRE-4848",
)


def _step() -> dict:
    """The step as the workflow file itself spells it — never read through."""
    doc = yaml.safe_load(AGENT_FIX.read_text())
    found = [
        step for step in doc["jobs"]["fix"]["steps"]
        if isinstance(step, dict) and step.get("name") == STEP
    ]
    if len(found) != 1:
        raise AssertionError(f"agent-fix.yml has {len(found)} steps named {STEP!r}")
    return found[0]


class TheStepDelegatesTest(unittest.TestCase):
    def test_run_is_the_delegation_line(self):
        run = _step()["run"]
        self.assertEqual(run.strip(), DELEGATION)
        self.assertEqual(step_shell.delegated_script(run), "scripts/resolve_fix_pr.sh")

    def test_it_keeps_every_input_and_adds_the_two(self):
        env = _step().get("env") or {}
        self.assertEqual([k for k in ENV_KEYS if k not in env], [])
        for key, value in NEW_ENV.items():
            self.assertEqual(env[key], value)

    def test_it_keeps_its_id_and_condition(self):
        step = _step()
        self.assertEqual(step.get("id"), "pr")
        self.assertEqual(step.get("if"), "steps.decision.outputs.start != 'skip'")


class TheScriptFileTest(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.is_file(), f"{SCRIPT.relative_to(ROOT)} is missing")
        self.text = SCRIPT.read_text()

    def test_it_opens_the_way_a_run_block_runs(self):
        self.assertEqual(self.text.splitlines()[:2], ["#!/usr/bin/env bash", "set -e"])

    def test_it_holds_no_expression(self):
        self.assertNotIn("${{", self.text)

    def test_what_it_does_comes_before_its_history(self):
        does = self.text.find("# What this script does")
        history = self.text.find("# Incident history")
        self.assertNotEqual(does, -1, "no 'What this script does' section")
        self.assertNotEqual(history, -1, "no 'Incident history' section")
        self.assertLess(does, history)
        first_code = step_shell.code_lines(self.text)[0]
        self.assertLess(history, self.text.find(first_code), "the header sits above the first code line")

    def test_the_header_names_every_input(self):
        header = self.text[:self.text.find(step_shell.code_lines(self.text)[0])]
        self.assertEqual([k for k in ENV_KEYS if k not in header], [])

    def test_every_card_reference_survives(self):
        self.assertEqual([ref for ref in REFERENCES if ref not in self.text], [])


class TheFileRunsAsAFileTest(unittest.TestCase):
    """A plain open agent pull request, one attempt in, resolved by the file itself."""

    def test_an_open_agent_pr_goes(self):
        with tempfile.TemporaryDirectory() as raw:
            td = Path(raw)
            (td / "bin").mkdir()
            stub = td / "bin" / "gh"
            stub.write_text(GH_STUB)
            stub.chmod(0o755)
            # The pipeline checkout the job takes before this step.
            (td / ".bureau-pipeline").symlink_to(ROOT)
            info = td / "pr-info.json"
            info.write_text(json.dumps({
                "state": "OPEN", "headRefName": "agent/DRE-2721-x",
                "headRefOid": "d9f2c1ab" + "0" * 32, "mergeStateStatus": "CLEAN",
            }))
            comments = td / "comments.json"
            comments.write_text(json.dumps([rest(WORKER, ATTEMPT.format(n=1))]))
            out_file = td / "step-output"
            out_file.write_text("")
            log = td / "gh-writes.jsonl"
            proc = subprocess.run(
                ["bash", str(SCRIPT)], cwd=td,
                env=dict(
                    os.environ,
                    PATH=f"{td}/bin:{os.environ['PATH']}",
                    GITHUB_OUTPUT=str(out_file), RUNNER_TEMP=str(td),
                    GH_PR_INFO=str(info), GH_COMMENTS=str(comments), GH_LOG=str(log),
                    GH_TOKEN="test", WRITER_TOKEN="test-writer",
                    WORKER_LOGIN=WORKER, EVENT_NAME="issue_comment",
                    TRIGGERING_ACTOR="agent-bureau-qa-bot[bot]",
                    PR_NUMBER="199", REPO="dreadnought-foundry/bureau-pipeline",
                ),
                capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            outputs = dict(
                line.partition("=")[::2]
                for line in out_file.read_text().splitlines() if "=" in line
            )
            posted = log.read_text() if log.exists() else ""
        self.assertEqual(outputs["go"], "true")
        self.assertEqual(outputs["number"], "199")
        self.assertEqual(outputs["card"], "DRE-2721")
        self.assertEqual(outputs["mode"], "fix")
        self.assertEqual(outputs["attempt"], "2")
        self.assertEqual(posted, "")


HEAD = "735e4c75" + "0" * 32


def _resolve(thread):
    """Run the file by path against `thread`; (step outputs, posted bodies)."""
    run = _run(thread)
    return run["outputs"], run["posted"]


def _run(thread, merge_state="CLEAN", head=HEAD):
    """Run the file by path against `thread` at `head`; the step outputs, the
    posted bodies, the log and the run-record note, as a dict."""
    with tempfile.TemporaryDirectory() as raw:
        td = Path(raw)
        (td / "bin").mkdir()
        stub = td / "bin" / "gh"
        stub.write_text(GH_STUB)
        stub.chmod(0o755)
        (td / ".bureau-pipeline").symlink_to(ROOT)
        info = td / "pr-info.json"
        info.write_text(json.dumps({
            "state": "OPEN", "headRefName": "agent/DRE-6487-x",
            "headRefOid": head, "mergeStateStatus": merge_state,
        }))
        comments = td / "comments.json"
        comments.write_text(json.dumps(thread))
        out_file = td / "step-output"
        out_file.write_text("")
        log = td / "gh-writes.jsonl"
        proc = subprocess.run(
            ["bash", str(SCRIPT)], cwd=td,
            env=dict(
                os.environ,
                PATH=f"{td}/bin:{os.environ['PATH']}",
                GITHUB_OUTPUT=str(out_file), RUNNER_TEMP=str(td),
                GH_PR_INFO=str(info), GH_COMMENTS=str(comments), GH_LOG=str(log),
                GH_TOKEN="test", WRITER_TOKEN="test-writer",
                WORKER_LOGIN=WORKER, EVENT_NAME="issue_comment",
                TRIGGERING_ACTOR="agent-bureau-qa-bot[bot]",
                PR_NUMBER="3481", REPO="dreadnought-foundry/agent-bureau",
            ),
            capture_output=True, text=True, timeout=60,
        )
        if proc.returncode != 0:
            raise AssertionError(proc.stderr)
        outputs = dict(
            line.partition("=")[::2]
            for line in out_file.read_text().splitlines() if "=" in line
        )
        posted = ([json.loads(line) for line in log.read_text().splitlines()]
                  if log.exists() else [])
        no_work = td / "fix-no-work.txt"
        return {
            "outputs": outputs, "posted": posted, "log": proc.stdout,
            "no_work": no_work.read_text() if no_work.exists() else "",
        }


def _hold(thread, whole=False):
    outputs, posted = _resolve(thread)
    holds = [body for body in posted if body.startswith("🛑 Fix budget exhausted")]
    if len(holds) != 1:
        raise AssertionError(f"expected one hold, got {posted!r}")
    return outputs, holds[0] if whole else holds[0].split("\n", 1)[0]


QA = "agent-bureau-qa-bot[bot]"
REPEAT = "repeat-finding prior-fixes-held in-scope"
CONVERGING = "new-finding prior-fixes-held in-scope"


def _verdict(n, line=None):
    body = (f"🔎 QA Critic — VERDICT: REQUEST_CHANGES cause:defect @{n:040x} "
            "content:" + "c" * 64 + "\n\n")
    if line:
        body += f"convergence: {line}\n\n"
    return body + "## Summary\nNot yet.\n\n## For the fixing agent\nfoo.py:1 — fix it."


def _thread(*lines):
    out = []
    for i, line in enumerate(lines, start=1):
        if i > 1:
            out.append(rest(WORKER, ATTEMPT.format(n=i - 1)))
        out.append(rest(QA, _verdict(i, line)))
    return out


class TheHoldNamesWhichStopItWasTest(unittest.TestCase):
    """(DRE-6533) The pull-request hold has three arms. It still opens
    `🛑 Fix budget exhausted`, which linear_ops.CONSOLE_HOLD_MARKERS and the
    act registry key on; the non-convergence and ceiling wordings are today's,
    word for word."""

    def test_silence(self):
        outputs, first = _hold(_thread(None, None, None, None))
        self.assertEqual(outputs["stopped_by"], "silence")
        self.assertIn("did not say whether", first)
        self.assertIn("new ground", first)
        self.assertNotIn("same ground", first)
        self.assertNotIn("made no progress", first)

    def test_no_part_of_the_silence_hold_says_circling(self):
        # The whole comment — the receipt and the release instructions too.
        _, body = _hold(_thread(None, None, None, None), whole=True)
        self.assertIn("📊 fix-convergence", body)
        self.assertNotIn("same ground", body)
        self.assertNotIn("made no progress", body)

    def test_the_3481_thread_is_not_held(self):
        # The incident: two silent re-reviews now buy attempt 3.
        outputs, posted = _resolve(_thread(None, None, None))
        self.assertEqual(outputs["go"], "true")
        self.assertEqual(outputs["attempt"], "3")
        self.assertEqual(outputs["stopped_by"], "none")
        self.assertEqual(posted, [])

    def test_non_convergence_keeps_todays_words(self):
        outputs, first = _hold(_thread(None, REPEAT, REPEAT))
        self.assertEqual(outputs["stopped_by"], "non-convergence")
        self.assertEqual(
            first,
            "🛑 Fix budget exhausted — 2 review rounds in a row made no "
            "progress (the stop budget is 2). A round that finds something "
            "new, leaves the earlier fixes working and stays in scope does "
            "not spend that budget. Holding for a human decision.")

    def test_the_ceiling_keeps_todays_words(self):
        outputs, first = _hold(_thread(*([None] + [CONVERGING] * 6)))
        self.assertEqual(outputs["stopped_by"], "ceiling")
        self.assertEqual(
            first,
            "🛑 Fix budget exhausted — it reached the hard ceiling of 6 fix "
            "attempts. That is the runaway backstop, not a judgement that the "
            "loop stopped making progress — it kept finding new work and "
            "never finished. Holding for a human decision.")


CONFLICT_HOLD = "🛑 Conflict-resolution budget exhausted"
FIX_HOLD = "🛑 Fix budget exhausted"
#: The pool bot that authored most of PR #1134's 58 conflict holds.
POOL = "agent-bureau-bot-4[bot]"
EARLIER = "80d8d5d5" + "0" * 32


def _conflict_spent():
    """Five conflict rounds, every one the worker bot's: the budget is spent."""
    return [rest(WORKER, ROUND.format(n=n)) for n in range(1, 6)]


def _fix_spent():
    """Two review rounds that re-found the same ground: the fix budget is spent."""
    return _thread(None, REPEAT, REPEAT)


#: Each hold kind: the thread that spends its budget, and the merge state that
#: puts the run in its mode.
KINDS = {
    CONFLICT_HOLD: (_conflict_spent, "DIRTY"),
    FIX_HOLD: (_fix_spent, "CLEAN"),
}


def _posted_hold(prefix, head=HEAD):
    """The hold the file itself posts for `prefix` at `head` — never re-typed
    here, so the standing hold the next run reads is the one GitHub would hold."""
    spent, merge_state = KINDS[prefix]
    run = _run(spent(), merge_state=merge_state, head=head)
    holds = [body for body in run["posted"] if body.startswith(prefix)]
    if len(holds) != 1:
        raise AssertionError(f"expected one {prefix!r} hold, got {run['posted']!r}")
    return holds[0]


class AHeldPullRequestGetsOneHoldNoticeTest(unittest.TestCase):
    """(DRE-7506) PR #1134 got 58 identical conflict holds in ten hours, one
    per fix run. A hold already standing at this head, with no newer operator
    decision, is not posted again — whichever pool bot posted it."""

    def again(self, prefix, standing, extra=()):
        spent, merge_state = KINDS[prefix]
        return _run(spent() + [standing] + list(extra), merge_state=merge_state)

    def assert_suppressed(self, run):
        self.assertEqual(run["posted"], [])
        self.assertEqual(run["outputs"]["go"], "false")
        self.assertEqual(run["outputs"]["no_work"], "true")
        # The job's "Notify hold on Linear" steps post once per held=true.
        self.assertNotIn("held", run["outputs"])
        self.assertIn("already standing", run["log"])
        self.assertIn("already standing", run["no_work"])

    def assert_one_hold(self, run, prefix):
        self.assertEqual(len(run["posted"]), 1, run["posted"])
        self.assertTrue(run["posted"][0].startswith(prefix), run["posted"][0])
        self.assertEqual(run["outputs"]["held"], "true")
        self.assertNotIn("already standing", run["log"])

    def test_a_second_run_under_a_standing_hold_posts_nothing(self):
        for prefix in KINDS:
            with self.subTest(prefix=prefix):
                standing = rest(WORKER, _posted_hold(prefix))
                self.assert_suppressed(self.again(prefix, standing))

    def test_a_hold_posted_by_another_pool_bot_still_stands(self):
        # The PR #1134 case: the hold goes out on GH_TOKEN, the pool reader,
        # so its author is not WORKER_LOGIN. The DRE-4848 failure, again.
        self.assertNotEqual(POOL, WORKER)
        for prefix in KINDS:
            with self.subTest(prefix=prefix):
                standing = rest(POOL, _posted_hold(prefix))
                self.assertEqual(standing["user"]["type"], "Bot")
                self.assert_suppressed(self.again(prefix, standing))

    def test_a_new_commit_releases_the_hold(self):
        # The release on PR #1134 was a hand-pushed merge commit, which posts
        # no push marker: the hold's own commit is what is compared.
        for prefix in KINDS:
            with self.subTest(prefix=prefix):
                standing = rest(POOL, _posted_hold(prefix, head=EARLIER))
                self.assert_one_hold(self.again(prefix, standing), prefix)

    def test_a_newer_operator_decision_releases_the_hold(self):
        marker = {CONFLICT_HOLD: ROUND.format(n=6), FIX_HOLD: ATTEMPT.format(n=3)}
        for prefix in KINDS:
            with self.subTest(prefix=prefix):
                standing = rest(WORKER, _posted_hold(prefix))
                decision = rest(HUMAN, "**Operator decision** — try once more.")
                # The decision already bought its attempt, so the budget
                # answers hold again.
                extra = [decision, rest(WORKER, marker[prefix])]
                spent, _ = KINDS[prefix]
                thread = spent() + [standing] + extra
                self.assertIs(fix_context.operator_decision(thread, WORKER), decision)
                self.assert_one_hold(self.again(prefix, standing, extra), prefix)

    def test_a_person_quoting_the_hold_is_not_a_standing_hold(self):
        quoted = rest(HUMAN, _posted_hold(CONFLICT_HOLD))
        self.assertEqual(quoted["user"]["type"], "User")
        self.assert_one_hold(self.again(CONFLICT_HOLD, quoted), CONFLICT_HOLD)

    def test_one_kind_of_hold_does_not_silence_the_other(self):
        # PR #1134 carried both on the same afternoon.
        conflict = rest(POOL, _posted_hold(CONFLICT_HOLD))
        self.assert_one_hold(self.again(FIX_HOLD, conflict), FIX_HOLD)
        fix = rest(POOL, _posted_hold(FIX_HOLD))
        self.assert_one_hold(self.again(CONFLICT_HOLD, fix), CONFLICT_HOLD)

    def test_a_hold_from_before_this_change_reads_as_released(self):
        # It carries no commit: a pull request already held when this ships
        # gets one more notice and then stops.
        old = rest(POOL, CONFLICT_HOLD_BEFORE)
        self.assert_one_hold(self.again(CONFLICT_HOLD, old), CONFLICT_HOLD)

    def test_the_hold_carries_its_commit_after_the_pinned_sentences(self):
        for prefix in KINDS:
            with self.subTest(prefix=prefix):
                body = _posted_hold(prefix)
                rest_of_it, last = body.rsplit("\n\n", 1)
                self.assertEqual(last, f"Held at commit {HEAD}.")
                self.assertTrue(rest_of_it.endswith(fix_context.HAND_DISPATCH_NOTICE))
                self.assertTrue(any(
                    marker in body.lower() for marker in linear_ops.CONSOLE_HOLD_MARKERS))


#: A conflict hold as PR #1134 carried it before DRE-7506: no commit line.
CONFLICT_HOLD_BEFORE = (
    f"{CONFLICT_HOLD} (6 rounds) — main keeps moving under this branch; "
    "holding for a human decision.\n\nTo release this PR, comment here with a "
    "first line that starts with the words Operator decision."
)


class TheIncidentIsInTheHeaderTest(unittest.TestCase):
    def test_the_header_carries_a_dated_dre_7506_note(self):
        text = SCRIPT.read_text()
        header = text[:text.find(step_shell.code_lines(text)[0])]
        history = header[header.find("# Incident history"):]
        self.assertRegex(history, r"# 2026-10-\d\d · DRE-7506\.")
        note = history[history.find("DRE-7506."):]
        self.assertIn("#1134", note)


if __name__ == "__main__":
    unittest.main()
