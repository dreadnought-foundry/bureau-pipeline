"""`Report result to Linear` runs as a file: scripts/report_agent_result.sh (DRE-5223).

DRE-3488 moves the step's 26,000-character `run:` block into a script, and
the step keeps one line that calls it. The suites DRE-5221 pointed at
`step_shell` prove the behavior survives the move; this file proves the move
itself. The step delegates and keeps every value it hands the script, the
script is shaped the way `step_shell` says a moved script is, its header
reads current behavior first and incident history second, and the file
runs end to end by its own path. That last part matters because no other
test calls the file directly.

The harness copies the one in tests/test_turn_budget_scenario.py:
`linear_ops.py` records rather than writes, `card_pr.py` answers what the
test picks, `git` answers nothing, and every other script in the checkout is
the real one.

Run: python3 -m pytest tests/test_report_agent_result.py -v
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW = os.path.join(ROOT, ".github", "workflows", "agent-task.yml")
SCRIPT = os.path.join(ROOT, "scripts", "report_agent_result.sh")
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import step_shell  # noqa: E402

STEP = "Report result to Linear"
DELEGATION = "bash .bureau-pipeline/scripts/report_agent_result.sh"

#: The step's `env:` on `main` at 9fe61d5, as the card lists it.
ENV_KEYS = (
    "LINEAR_API_KEY", "GH_TOKEN", "CARD", "RESUME_BRANCH", "RESUME_SHA",
    "RESUME_SNAPSHOT", "MODEL_USED", "CLAUDE_OUTCOME", "DEDUPE_OUTCOME",
    "MODEL_OUTCOME", "CTX_OUTCOME", "SANITIZE_OUTCOME", "INPROGRESS_OUTCOME",
    "PRE_AGENT_LOG", "RESCUE_PUSH_STATUS", "RESCUE_PATCH", "RESCUE_ARTIFACT",
    "RESCUE_PUSHED", "RESCUE_ERROR", "GH_DISPATCH_TOKEN", "BUREAU_SERVER_URL",
    "BUREAU_REPOSITORY", "BUREAU_RUN_ID", "CLAUDE_EXECUTION_FILE",
    "RESCUE_LOCAL_WORK",
)

#: The 27 card references the block carried on `main`; `verify` requires
#: every one of them in the script.
REFERENCES = (
    "DRE-1286", "DRE-1300", "DRE-1343", "DRE-1354", "DRE-1403", "DRE-1655",
    "DRE-1885", "DRE-2032", "DRE-2034", "DRE-2070", "DRE-2074", "DRE-2312",
    "DRE-2316", "DRE-2695", "DRE-2727", "DRE-2776", "DRE-2911", "DRE-2923",
    "DRE-2931", "DRE-3043", "DRE-3097", "DRE-3098", "DRE-3165", "DRE-3262",
    "DRE-4366", "DRE-4368", "DRE-4370",
)

CARD = "DRE-5223"
REPO = "dreadnought-foundry/bureau-pipeline"
RUN_ID = "34000000001"
PR_URL = f"https://github.com/{REPO}/pull/700"


def step() -> dict:
    for entry in yaml.safe_load(open(WORKFLOW))["jobs"]["execute"]["steps"]:
        if entry.get("name") == STEP:
            return entry
    raise AssertionError(f"{STEP!r} is gone from agent-task.yml")


def script_text() -> str:
    with open(SCRIPT, encoding="utf-8") as fh:
        return fh.read()


class TheStepDelegates(unittest.TestCase):

    def test_the_run_is_the_delegation_line(self):
        """Put the inline block back and this goes red."""
        self.assertEqual(DELEGATION, step()["run"].strip())
        self.assertEqual("scripts/report_agent_result.sh",
                         step_shell.delegated_script(step()["run"]))

    def test_the_env_still_carries_every_value_the_script_reads(self):
        missing = [k for k in ENV_KEYS if k not in (step().get("env") or {})]
        self.assertEqual([], missing)

    def test_the_step_keeps_its_condition(self):
        self.assertIn("steps.dedupe.outputs.skip != 'true'", step()["if"])

    def test_the_step_sets_no_shell(self):
        """The script's `set -e` matches the runner's `bash -e {0}` only
        while the step sets no `shell:` of its own."""
        self.assertNotIn("shell", step())


class TheScriptFile(unittest.TestCase):

    def test_it_opens_with_the_two_required_lines(self):
        lines = script_text().splitlines()
        self.assertEqual(["#!/usr/bin/env bash", "set -e"], lines[:2])

    def test_it_carries_no_actions_expression(self):
        """Actions never substitutes into a file, so an expression here
        would reach bash as literal text."""
        self.assertNotIn("${{", script_text())

    def test_the_header_reads_present_tense_first_and_history_second(self):
        lines = script_text().splitlines()
        self.assertIn("# What this script does", lines)
        self.assertIn("# Incident history", lines)
        self.assertLess(lines.index("# What this script does"),
                        lines.index("# Incident history"))

    def test_the_header_sits_between_line_two_and_the_first_code_line(self):
        lines = script_text().splitlines()
        first_code = next(i for i, line in enumerate(lines[2:], start=2)
                          if line.strip() and not line.lstrip().startswith("#"))
        header = "\n".join(lines[2:first_code])
        self.assertIn("What this script does", header)
        self.assertIn("Incident history", header)

    def test_the_header_names_every_env_input(self):
        lines = script_text().splitlines()
        start = lines.index("# What this script does")
        end = lines.index("# Incident history")
        section = "\n".join(lines[start:end])
        missing = [k for k in ENV_KEYS if k not in section]
        self.assertEqual([], missing)

    def test_every_card_reference_is_kept(self):
        text = script_text()
        missing = [ref for ref in REFERENCES if ref not in text]
        self.assertEqual([], missing)


# --------------------------------------------------------------------------- #
# the file runs as a file                                                      #
# --------------------------------------------------------------------------- #

LINEAR_STUB = '''#!/usr/bin/env python3
import json, os, sys
with open(os.environ["LINEAR_STUB_LOG"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"op": sys.argv[1], "args": sys.argv[2:]}) + "\\n")
if sys.argv[1] == "count-comments":
    print("0")
if sys.argv[1] == "dump-comments":
    print("[]")
'''

CARD_PR_STUB = '''#!/usr/bin/env python3
import os
print("OPEN\\t" + os.environ["CARD_PR_STUB_URL"])
'''

FINISHED = {"type": "result", "subtype": "success", "is_error": False,
            "num_turns": 120, "total_cost_usd": 9.40, "result": "PR opened"}


def _executable(path: str, body: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    os.chmod(path, 0o755)


def _checkout(td: str) -> None:
    base = os.path.join(td, ".bureau-pipeline")
    shutil.copytree(os.path.join(ROOT, "scripts"), os.path.join(base, "scripts"),
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(os.path.join(ROOT, "config"), os.path.join(base, "config"))
    _executable(os.path.join(base, "scripts", "linear_ops.py"), LINEAR_STUB)
    _executable(os.path.join(base, "scripts", "card_pr.py"), CARD_PR_STUB)


def run_pr_opened(tc, command: list[str], *, card_pr: str = CARD_PR_STUB):
    """Run `command` from a runner-shaped directory; return `(proc, journal)`."""
    td = tempfile.mkdtemp()
    tc.addCleanup(shutil.rmtree, td, ignore_errors=True)
    _checkout(td)
    _executable(os.path.join(td, ".bureau-pipeline", "scripts", "card_pr.py"), card_pr)
    binary = os.path.join(td, "bin")
    os.makedirs(binary)
    _executable(os.path.join(binary, "git"), "#!/bin/sh\nexit 0\n")
    exec_file = os.path.join(td, "claude-execution-output.json")
    with open(exec_file, "w", encoding="utf-8") as fh:
        json.dump(FINISHED, fh)
    log = os.path.join(td, "linear.jsonl")
    env = {k: v for k, v in os.environ.items() if k not in ENV_KEYS}
    env.update(
        PATH=binary + os.pathsep + os.environ["PATH"],
        RUNNER_TEMP=td,
        LINEAR_API_KEY="test-key",
        GH_TOKEN="test",
        CARD=CARD,
        RESUME_SNAPSHOT=os.path.join(td, "resume-branches.json"),
        MODEL_USED="claude-opus-5",
        CLAUDE_OUTCOME="success",
        DEDUPE_OUTCOME="success",
        MODEL_OUTCOME="success",
        CTX_OUTCOME="success",
        SANITIZE_OUTCOME="success",
        INPROGRESS_OUTCOME="success",
        PRE_AGENT_LOG=os.path.join(td, "preagent.log"),
        RESCUE_ARTIFACT=f"rescue-{CARD}.patch",
        BUREAU_SERVER_URL="https://github.com",
        BUREAU_REPOSITORY=REPO,
        BUREAU_RUN_ID=RUN_ID,
        CLAUDE_EXECUTION_FILE=exec_file,
        RESCUE_LOCAL_WORK="false",
        LINEAR_STUB_LOG=log,
        CARD_PR_STUB_URL=PR_URL,
    )
    proc = subprocess.run(command, cwd=td, env=env,
                          capture_output=True, text=True)
    journal = []
    if os.path.exists(log):
        journal = [json.loads(line) for line in
                   open(log, encoding="utf-8").read().splitlines()]
    return proc, journal


class PrOpenedRunsAsAFile(unittest.TestCase):
    """The "PR opened" path, driven through the file in this repo."""

    def _assert_pr_opened(self, proc, journal):
        self.assertEqual(0, proc.returncode, proc.stderr + proc.stdout)
        posted = [e["args"][1] for e in journal if e["op"] == "comment"]
        self.assertEqual(
            [f"🤖 PR opened: {PR_URL} — CI + critic review running. "
             f"Run: https://github.com/{REPO}/actions/runs/{RUN_ID}"],
            posted)
        moved = [e["args"] for e in journal if e["op"] in ("advance", "state")]
        self.assertEqual([[CARD, "In Review", "In Progress,Todo"]], moved)

    def test_the_script_runs_by_its_path(self):
        self._assert_pr_opened(*run_pr_opened(self, ["bash", SCRIPT]))

    def test_the_step_line_runs_the_same_script(self):
        """The step's own `run:` executed from the runner's working
        directory, where the pipeline checkout sits at .bureau-pipeline."""
        self._assert_pr_opened(*run_pr_opened(self, ["bash", "-c", step()["run"]]))


# --------------------------------------------------------------------------- #
# a blocker note is classified before it is parked (DRE-6444)                  #
# --------------------------------------------------------------------------- #

#: tests/test_turn_budget_scenario.py's CARD_PR_EXIT_STUB answering exit 0: no
#: pull request, so the chain walks past the PR branches to the agent's notes.
CARD_PR_NO_PR_STUB = '''#!/usr/bin/env python3
print("\\t")
'''

BLOCKER = "/tmp/agent-blocker.txt"
ESCALATION = "/tmp/agent-escalation.txt"
HANDBACK = "/tmp/agent-handback.txt"
EXIT_FILES = (HANDBACK, ESCALATION, BLOCKER)

FIXTURES = os.path.join(ROOT, "tests", "fixtures", "blocker-reasons.json")
PARKED = " — parked in Backlog"
ATTESTED = (
    "- [x] the cap is 15 — config/epic-cap.json reads 15 on main\n"
    "- [x] the proof names the count — architecture/proofs/epic-cap.md says 9 of 15\n"
    "- [x] no pull request is opened — the change would be empty\n"
)


def _clear_exit_files():
    for path in EXIT_FILES:
        if os.path.exists(path):
            os.remove(path)


def _quoted(comment: str) -> str:
    """What the marker quotes: after the `·`, up to the LAST parked clause."""
    rest = comment.split(" · ", 1)[1]
    return rest[:rest.rindex(PARKED)]


class ABlockerIsClassifiedBeforeItParks(unittest.TestCase):
    """The blocker branch, driven through the file with no PR to report."""

    def _run(self, exit_files: dict):
        _clear_exit_files()
        self.addCleanup(_clear_exit_files)
        for path, text in exit_files.items():
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
        proc, journal = run_pr_opened(self, ["bash", SCRIPT], card_pr=CARD_PR_NO_PR_STUB)
        self.assertEqual(0, proc.returncode, proc.stderr + proc.stdout)
        posted = [e["args"][1] for e in journal if e["op"] == "comment"]
        moved = [e["args"] for e in journal if e["op"] in ("advance", "state")]
        return posted, moved

    def _assert_parked(self, cls: str, note: str):
        posted, moved = self._run({BLOCKER: note})
        self.assertEqual(1, len(posted), posted)
        self.assertTrue(
            posted[0].startswith(f"🛑 Agent blocked: class={cls} · "), posted[0])
        self.assertNotIn("blocker-class:", posted[0])
        self.assertIn(PARKED + " until the sweep acts on it (", posted[0])
        self.assertTrue(posted[0].endswith(
            f"Run: https://github.com/{REPO}/actions/runs/{RUN_ID}"), posted[0])
        self.assertEqual([[CARD, "Backlog", "--park"]], moved)
        return posted[0]

    def test_a_wrong_repo_note_keeps_its_repo_line_first(self):
        posted = self._assert_parked(
            "wrong-repo",
            "blocker-class: wrong-repo\nrepo: bureau-pipeline\n"
            "The card's files live in bureau-pipeline, not here.\n")
        self.assertTrue(_quoted(posted).startswith("repo: bureau-pipeline\n"), posted)
        self.assertIn("The card's files live in bureau-pipeline, not here.",
                      _quoted(posted))

    def test_a_branch_without_pr_note_parks_with_its_class(self):
        posted = self._assert_parked(
            "branch-without-pr",
            "blocker-class: branch-without-pr\n"
            "The work is pushed on agent/DRE-5223-x and no PR was opened.\n")
        self.assertEqual(
            "The work is pushed on agent/DRE-5223-x and no PR was opened.",
            _quoted(posted))

    def test_a_nothing_to_change_note_quotes_its_attestations_verbatim(self):
        posted = self._assert_parked(
            "nothing-to-change", "blocker-class: nothing-to-change\n" + ATTESTED)
        self.assertEqual(ATTESTED.rstrip("\n"), _quoted(posted))

    def test_the_class_names_what_the_sweep_does_with_it(self):
        """Each mechanical class gets its own clause, never one shared text."""
        clauses = set()
        for cls, note in (
                ("wrong-repo", "blocker-class: wrong-repo\nrepo: bureau-pipeline\n"),
                ("branch-without-pr", "blocker-class: branch-without-pr\nx\n"),
                ("nothing-to-change", "blocker-class: nothing-to-change\n" + ATTESTED)):
            posted = self._assert_parked(cls, note)
            clauses.add(posted.rsplit(PARKED, 1)[1])
        self.assertEqual(3, len(clauses), clauses)

    def test_a_legacy_note_reaches_the_poster_through_its_wording(self):
        """DRE-5195's free prose, stamped by nothing, names one class."""
        with open(FIXTURES, encoding="utf-8") as fh:
            row = next(r for r in json.load(fh) if r["card"] == "DRE-5195")
        posted = self._assert_parked("nothing-to-change", row["reason"])
        self.assertEqual(row["reason"], _quoted(posted))

    def _assert_asked(self, posted, moved):
        self.assertEqual(1, len(posted), posted)
        self.assertTrue(posted[0].startswith("🙋"), posted[0])
        for line in ("🔎 Finding:", "❓ Question:", "💡 Recommendation:"):
            self.assertIn(line, posted[0])
        self.assertNotIn("🛑 Agent blocked", posted[0])
        self.assertNotIn("blocker-class:", posted[0])
        self.assertIn([CARD, "Green Light", "In Progress,Todo"], moved)
        self.assertNotIn("Backlog", [m[1] for m in moved])

    def test_an_unclassed_note_is_asked_in_green_light(self):
        posted, moved = self._run({BLOCKER: "the API does not exist"})
        self._assert_asked(posted, moved)
        self.assertIn("🔎 Finding: the API does not exist", posted[0])
        self.assertIn("💡 Recommendation: none given — ", posted[0])

    def test_a_note_stamped_question_is_asked_without_its_stamp(self):
        posted, moved = self._run(
            {BLOCKER: "blocker-class: question\nShould a canceled run count?\n"})
        self._assert_asked(posted, moved)
        self.assertIn("Should a canceled run count?", posted[0])

    def test_an_escalation_the_agent_wrote_is_never_overwritten(self):
        posted, moved = self._run({
            ESCALATION: "Which of A or B should ship first?",
            BLOCKER: "the API does not exist",
        })
        self._assert_asked(posted, moved)
        self.assertIn("Which of A or B should ship first?", posted[0])
        self.assertNotIn("the API does not exist", posted[0])
        with open(ESCALATION, encoding="utf-8") as fh:
            self.assertEqual("Which of A or B should ship first?", fh.read())

    def test_a_question_with_nothing_to_ask_parks_for_the_sweep(self):
        """A stamp and no words: the hand-off leaves no escalation, so the
        blocker branch parks the question and the sweep asks it."""
        posted, moved = self._run({BLOCKER: "blocker-class: question\n"})
        self.assertEqual(1, len(posted), posted)
        self.assertTrue(
            posted[0].startswith("🛑 Agent blocked: class=question · "), posted[0])
        self.assertIn(PARKED + " until the sweep asks it in Green Light. Run: ",
                      posted[0])
        self.assertEqual([[CARD, "Backlog", "--park"]], moved)

    def test_the_header_describes_the_classify_first_branch(self):
        lines = script_text().splitlines()
        section = "\n".join(lines[lines.index("# What this script does"):
                                  lines.index("# Incident history")])
        self.assertIn("blocker_class.py classify", section)
        self.assertIn("class=<class>", section)


if __name__ == "__main__":
    unittest.main()
