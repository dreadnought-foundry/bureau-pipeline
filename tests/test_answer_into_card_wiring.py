"""Every planning run writes the card's verified answers into its description
before the classifier, the critic or the planner reads it (DRE-6360).

DRE-6357 built the writer, `scripts/answer_into_card.py write`. This pins where
`plan.yml` runs it, and the one change that lets the same run's readers see
what it wrote:

  * a step `id: answer`, before `Classify the card — one-off, epic or wave`
    and gated on exactly that step's `if`, writes the block and hands the
    description as it now stands back as the step output `description`;
  * `Sanitize untrusted epic text` takes that output when there is one and the
    dispatch payload's copy when there is not — so the one-off critic, the
    planner's revise prompt and every epic prompt, which all read
    `steps.card.outputs.description`, read the description carrying the
    `## Decisions from the CEO` block, not the copy made before his answer
    (DRE-3879 round 4);
  * `One-off revision — what the planner did` runs the writer again right
    after the planner's rewrite, so a revision that dropped the block cannot
    leave it off the card.

The other steps of `plan.yml` belong to other cards (DRE-6455, DRE-6456), so
nothing here quotes their text or counts their `hold.py apply` lines: these
tests assert only what DRE-6360 adds.

Run: cd bureau-pipeline && python3 -m pytest tests/test_answer_into_card_wiring.py -v
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = os.path.join(os.path.dirname(__file__), "..")
SCRIPTS = os.path.join(ROOT, "scripts")
WF = os.path.join(ROOT, ".github", "workflows", "plan.yml")
UNTRUSTED_WIRING = os.path.join(ROOT, "tests", "test_untrusted_content_wiring.py")

sys.path.insert(0, SCRIPTS)

import answer_into_card  # noqa: E402
import review_rerun  # noqa: E402

WRITER = ".bureau-pipeline/scripts/answer_into_card.py"
ANSWER_OUTPUT = "steps.answer.outputs.description"
SANITIZED_DESC = "${{ steps.card.outputs.description }}"
RAW_DESCRIPTION = (
    "${{ steps.answer.outputs.description"
    " || github.event.client_payload.description }}"
)

CLASSIFY = "Classify the card — one-off, epic or wave"
SANITIZE = "Sanitize untrusted epic text"
REVISED = "One-off revision — what the planner did"

# The classify step's four clauses, as this card names them.
CLAUSES = (
    "steps.gate.outputs.bounced != 'true'",
    "steps.dedupe.outputs.skip != 'true'",
    "steps.slot.outputs.admitted == 'true'",
    "steps.ask.outputs.review != 'true'",
)


def _steps() -> list[dict]:
    with open(WF) as f:
        return yaml.safe_load(f)["jobs"]["plan"]["steps"]


def _named(steps: list[dict], name: str) -> tuple[int, dict]:
    hits = [(i, s) for i, s in enumerate(steps) if s.get("name") == name]
    assert len(hits) == 1, f"expected one step named {name!r}, found {len(hits)}"
    return hits[0]


def _by_id(steps: list[dict], sid: str) -> tuple[int, dict]:
    hits = [(i, s) for i, s in enumerate(steps) if s.get("id") == sid]
    assert len(hits) == 1, f"expected one step with id {sid!r}, found {len(hits)}"
    return hits[0]


class WriterStepTest(unittest.TestCase):
    """The `answer` step: where it sits, when it runs, what it calls."""

    def setUp(self):
        self.steps = _steps()
        self.ai, self.answer = _by_id(self.steps, "answer")
        self.ci, self.classify = _named(self.steps, CLASSIFY)

    def test_writer_runs_before_the_classifier_and_the_sanitizer(self):
        run = self.answer["run"]
        self.assertIn(f"python3 {WRITER} write", run)
        self.assertIn('"${{ github.event.client_payload.identifier }}"', run)
        self.assertIn('--github-output "$GITHUB_OUTPUT"', run)
        si, _ = _named(self.steps, SANITIZE)
        self.assertLess(self.ai, self.ci,
                        "the writer must run before the classifier reads the card")
        self.assertLess(self.ai, si,
                        "the writer's output must be set before the sanitizer reads it")

    def test_writer_if_is_the_classify_if_byte_for_byte(self):
        self.assertEqual(self.answer["if"], self.classify["if"])

    def test_classify_if_still_carries_the_four_clauses(self):
        # So the comparison above cannot pass on two strings that both drifted.
        for clause in CLAUSES:
            self.assertIn(clause, self.classify["if"])

    def test_writer_reads_linear_with_the_classify_keys(self):
        for key in ("LINEAR_API_KEY", "LINEAR_API_KEY_FALLBACK"):
            self.assertIn(key, self.answer["env"])
            self.assertEqual(self.answer["env"][key], self.classify["env"][key])

    def test_no_continue_on_error_the_cli_exit_rule_keeps_the_run_green(self):
        self.assertNotIn("continue-on-error", self.answer)
        out = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS, "answer_into_card.py"),
             "write", "--help"],
            capture_output=True, text=True, check=True,
        ).stdout
        self.assertIn("always exits 0", " ".join(out.split()))

    def test_writer_step_is_neither_a_hold_nor_a_lane_write(self):
        run = self.answer["run"]
        for call in ("hold.py apply", "plan_bound.py", "linear_ops.py state"):
            self.assertNotIn(call, run)


class SanitizerTakesTheLiveDescriptionTest(unittest.TestCase):
    """The same-run readers see the writer's write — through the sanitizer."""

    def setUp(self):
        self.steps = _steps()
        _, self.sanitize = _named(self.steps, SANITIZE)

    def test_raw_description_prefers_the_writer_output(self):
        env = self.sanitize["env"]
        self.assertEqual(env["RAW_DESCRIPTION"], RAW_DESCRIPTION)
        self.assertEqual(env["RAW_TITLE"], "${{ github.event.client_payload.title }}")
        run = " ".join(self.sanitize["run"].replace("\\\n", " ").split())
        self.assertIn(
            "sanitize_untrusted.py line RAW_TITLE title body RAW_DESCRIPTION description",
            run,
        )

    def test_readers_embed_the_sanitized_description_only(self):
        for sid in ("oocritic", "oorevise", "claude", "claude_retry",
                    "prea", "preb", "posta", "rollup", "rollup_retry"):
            _, step = _by_id(self.steps, sid)
            self.assertIn(SANITIZED_DESC, step["with"]["prompt"], sid)
        for step in self.steps:
            prompt = (step.get("with") or {}).get("prompt")
            if prompt is not None:
                self.assertNotIn(ANSWER_OUTPUT, prompt, step.get("name"))

    def test_writer_output_is_read_in_exactly_one_place(self):
        with open(WF) as f:
            lines = [ln for ln in f if ANSWER_OUTPUT in ln]
        self.assertEqual(len(lines), 1, lines)
        self.assertEqual(lines[0].strip(), f"RAW_DESCRIPTION: {RAW_DESCRIPTION}")

    def test_the_critic_prompt_text_carries_the_block(self):
        # Run the sanitize step's own command over a description the writer
        # produced: its `description` output is what the prompts embed.
        said = "Ship the narrow version first.\nThe wide one can wait."
        desc = answer_into_card.transcribe(
            "Do the thing.\n\n## Acceptance criteria\n\n- [ ] it works\n",
            [("2026-10-09T17:05:00Z", said)],
        )
        argv = shlex.split(self.sanitize["run"].replace("\\\n", " "))
        self.assertEqual(argv[:2], ["python3", ".bureau-pipeline/scripts/sanitize_untrusted.py"])
        with tempfile.NamedTemporaryFile(mode="r", delete=False) as out:
            self.addCleanup(os.unlink, out.name)
        subprocess.run(
            [sys.executable, os.path.join(SCRIPTS, "sanitize_untrusted.py"), *argv[2:]],
            env={**os.environ, "RAW_TITLE": "A card", "RAW_DESCRIPTION": desc,
                 "GITHUB_OUTPUT": out.name},
            capture_output=True, text=True, check=True,
        )
        written = open(out.name).read()
        m = re.search(r"^description<<(\S+)\n(.*?)\n\1$", written, re.S | re.M)
        self.assertIsNotNone(m, written)
        value = m.group(2)
        self.assertIn("\n## Decisions from the CEO\n", value)
        self.assertIn("> Ship the narrow version first.\n> The wide one can wait.", value)


class RevisionKeepsTheBlockTest(unittest.TestCase):
    """The planner's rewrite cannot leave the block off the card."""

    def setUp(self):
        self.steps = _steps()
        _, self.revised = _named(self.steps, REVISED)

    def test_writer_runs_again_after_set_description_in_the_revised_branch(self):
        run = self.revised["run"]
        branch = run.index('if [ "$OUTCOME" = "revised" ]; then')
        setdesc = run.index('linear_ops.py set-description "$CARD"')
        writer = run.index(f'python3 {WRITER} write "$CARD"')
        comment = run.index('linear_ops.py comment "$CARD"', setdesc)
        end = run.index("\nfi", branch)
        self.assertLess(branch, setdesc)
        self.assertLess(setdesc, writer)
        self.assertLess(writer, comment, "the block is back before the comment")
        self.assertLess(writer, end, "the call sits inside the revised branch")
        line = next(ln for ln in run.splitlines() if "answer_into_card" in ln)
        self.assertNotIn("--github-output", line)

    def test_writer_is_called_from_exactly_two_steps(self):
        callers = [s.get("name") for s in self.steps
                   if "answer_into_card" in (s.get("run") or "")]
        _, answer = _by_id(self.steps, "answer")
        self.assertEqual(callers, [answer["name"], REVISED])


class GateTurnsOnTheReviewAskTest(unittest.TestCase):

    def test_one_off_revise_is_not_a_review_ask(self):
        """The writer shares the classifier's gate, which skips a review ask.
        A revised one-off's re-read is dispatched `one-off-revise` and must
        not match, so the writer runs on it; a plan review (`re-review`)
        matches, so the writer — like the classifier — is skipped there."""
        self.assertFalse(review_rerun.is_review_ask("one-off-revise"))
        self.assertTrue(review_rerun.is_review_ask("re-review"))


class UntrustedFieldPinTest(unittest.TestCase):

    def test_writer_output_is_a_covered_untrusted_field(self):
        spec = importlib.util.spec_from_file_location("untrusted_wiring", UNTRUSTED_WIRING)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertIn(ANSWER_OUTPUT, mod.InterpolationPinTest.FIELDS)


class LaneCheckersStillPassTest(unittest.TestCase):

    def test_ready_lane_writers_and_green_light_rows_check(self):
        # Offline, the way tests/test_no_unplanned_ready_lane_writer.py runs
        # it: the team default comes from a workspace declaration, not Linear.
        with tempfile.TemporaryDirectory() as tmp:
            workspace = os.path.join(tmp, "linear-workspace.json")
            with open(workspace, "w") as f:
                json.dump({"team": {"key": "DRE", "defaultIssueState": "Planning"}}, f)
            env = {k: v for k, v in os.environ.items() if k != "LINEAR_API_KEY"}
            env["LINEAR_WORKSPACE_CONFIG"] = workspace
            for script in ("ready_lane_writers.py", "green_light_rows.py"):
                proc = subprocess.run(
                    [sys.executable, os.path.join(SCRIPTS, script), "check"],
                    cwd=ROOT, env=env, capture_output=True, text=True, timeout=300,
                )
                self.assertEqual(proc.returncode, 0,
                                 f"{script}: {proc.stdout}{proc.stderr}")


if __name__ == "__main__":
    unittest.main()
