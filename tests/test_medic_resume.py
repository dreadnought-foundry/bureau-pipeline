"""The medic carries on after a diagnosis that says a fresh attempt would
clear the fault (DRE-7526, from weekly report DRE-7512, suggested fix fix-3).

When a pipeline workflow failed twice — the original run and the medic's one
automatic retry — the medic ran a diagnosis agent and posted its report on the
card as `run-failure-diagnosed`. That act is a hold whose next actor is the
operator, so every diagnosis stopped the work until a person read it, even
when the diagnosis itself said the cause was a passing outside fault. That
hold was 201 stops in one week, the biggest single pile of waiting time.

So the diagnosis agent now also writes a one-word verdict:

  * `resume` — the diagnosis's category (c), an outside service failure a
    fresh attempt can clear. On the failed run's SECOND attempt, with the
    medic's own retry gate not having said `false`, the medic re-runs the
    failed jobs of the same run once more (`gh run rerun <id> --failed`) and
    posts nothing: the report goes into the medic run's own step summary,
    naming attempt 3;
  * `hold` — categories (a), (b) and (d), or any doubt — and also a missing,
    empty or unreadable file, or any other text. The diagnosis is delivered
    exactly as before.

Attempt 2 becomes attempt 3 and never further: the medic of attempt 3
delivers its diagnosis whatever the verdict says, so the medic cannot loop.
The `diagnose` job's own `if:` is untouched, so every class and rule it skips
today is still skipped.
"""

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import medic_resume  # noqa: E402

MEDIC = os.path.join(ROOT, ".github", "workflows", "medic.yml")
VERDICT = ".bureau-pipeline/medic-verdict.txt"
REPORT = ".bureau-pipeline/medic-report.md"
RUN_ID = "38412906115"
RUN_URL = f"https://github.com/dreadnought-foundry/atlas/actions/runs/{RUN_ID}"


def _jobs() -> dict:
    with open(MEDIC, encoding="utf-8") as f:
        return yaml.safe_load(f)["jobs"]


def _steps() -> list:
    return _jobs()["diagnose"]["steps"]


def _step(step_id: str) -> dict:
    return next(s for s in _steps() if s.get("id") == step_id)


def _index(step_id: str) -> int:
    return next(i for i, s in enumerate(_steps()) if s.get("id") == step_id)


def _prompt() -> str:
    return _step("claude")["with"]["prompt"]


# ── an Actions `if:` evaluated the way Actions evaluates it ─────────────────
# Copied from tests/test_medic_superseded_main_run.py, plus `always()`:
# `||`/`&&` return an operand, `==`/`!=` compare, a missing property is null.
_IDENT = re.compile(r"'[^']*'|\b[a-z_]+(?:\.[a-z_]+)+\b")


def _runs(condition: str, ctx: dict) -> bool:
    py = _IDENT.sub(
        lambda m: m.group(0) if m.group(0).startswith("'")
        else f"ctx.get({m.group(0)!r})", condition)
    py = py.replace("||", " or ").replace("&&", " and ")
    py = " ".join(py.split())  # a folded `if:` keeps its indented newlines
    return bool(eval(py, {"ctx": ctx, "always": lambda: True}))  # noqa: S307


def _resume_ctx(*, attempt=2, retry="true", verdict="resume") -> dict:
    return {
        "github.event.workflow_run.run_attempt": attempt,
        "needs.classify.outputs.retry": retry,
        "steps.verdict.outputs.verdict": verdict,
    }


# ── 1. the verdict file ─────────────────────────────────────────────────────
class TheVerdictTest(unittest.TestCase):
    def _read(self, content):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "medic-verdict.txt")
            if content is not None:
                mode = "wb" if isinstance(content, bytes) else "w"
                with open(path, mode) as f:
                    f.write(content)
            return medic_resume.verdict(path)

    def test_resume_reads_as_resume(self):
        self.assertEqual("resume", self._read("resume"))

    def test_resume_is_read_trimmed(self):
        self.assertEqual("resume", self._read("  resume\n"))

    def test_a_missing_file_reads_as_hold(self):
        self.assertEqual("hold", self._read(None))

    def test_an_empty_file_reads_as_hold(self):
        self.assertEqual("hold", self._read(""))
        self.assertEqual("hold", self._read("\n  \n"))

    def test_hold_reads_as_hold(self):
        self.assertEqual("hold", self._read("hold"))

    def test_any_other_text_reads_as_hold(self):
        for text in ("Resume", "RESUME", "resume.", "resume please",
                     "resume\nhold", "yes", "(c) resume", "`resume`"):
            with self.subTest(text=text):
                self.assertEqual("hold", self._read(text))

    def test_an_unreadable_file_reads_as_hold(self):
        self.assertEqual("hold", self._read(b"\xff\xfe resume \xff"))
        with tempfile.TemporaryDirectory() as td:
            # A directory where the file should be cannot be read as text.
            self.assertEqual("hold", medic_resume.verdict(td))

    def test_the_cli_prints_the_verdict(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "medic-verdict.txt")
            for content, want in (("resume\n", "resume"), ("hold", "hold"), (None, "hold")):
                if content is None:
                    os.unlink(path)
                else:
                    Path(path).write_text(content, encoding="utf-8")
                with self.subTest(content=content):
                    out = subprocess.run(
                        [sys.executable, os.path.join(ROOT, "scripts", "medic_resume.py"),
                         "verdict", "--file", path],
                        capture_output=True, text=True, check=True).stdout
                    self.assertEqual(want, out.strip())


# ── 2. the summary note ─────────────────────────────────────────────────────
class TheSummaryTest(unittest.TestCase):
    def test_a_resumed_attempt_2_names_attempt_3(self):
        note = medic_resume.summary("the report", failed_attempt=2,
                                    run_url=RUN_URL, workflow="Agent Task")
        first = note.splitlines()[0]
        self.assertIn("attempt 3", first)
        self.assertIn(RUN_URL, first)
        self.assertIn("Agent Task", first)

    def test_the_attempt_is_the_failed_attempt_plus_one(self):
        self.assertEqual(3, medic_resume.resumed_attempt(2))

    def test_the_note_carries_the_report(self):
        note = medic_resume.summary("Docker Hub timed out pulling the image.",
                                    failed_attempt=2, run_url=RUN_URL,
                                    workflow="Agent Task")
        self.assertIn("Docker Hub timed out pulling the image.", note)

    def test_a_missing_report_is_said_rather_than_left_blank(self):
        note = medic_resume.summary("", failed_attempt=2, run_url=RUN_URL,
                                    workflow="Agent Task")
        self.assertIn("attempt 3", note)
        self.assertIn("no report", note)


# ── 3. the prompt ───────────────────────────────────────────────────────────
class ThePromptTest(unittest.TestCase):
    def test_the_prompt_asks_for_the_verdict_file(self):
        prompt = _prompt()
        self.assertIn(VERDICT, prompt)
        self.assertIn("resume", prompt)
        self.assertIn("hold", prompt)

    def test_resume_is_category_c_and_hold_is_the_rest(self):
        prompt = " ".join(_prompt().split())
        self.assertRegex(prompt, r"resume[^.]*\(c\)")
        self.assertRegex(prompt, r"hold[^.]*\(a\), \(b\) and \(d\)")
        self.assertIn("any doubt", prompt)

    def test_the_prompt_no_longer_rules_out_a_transient_fault(self):
        prompt = _prompt()
        self.assertNotIn("NOT a transient flake", prompt)
        flat = " ".join(prompt.split())
        self.assertIn("original attempt", flat)
        self.assertIn("automatic retry", flat)
        self.assertIn("a fresh attempt", flat)

    def test_the_diagnosis_part_of_the_prompt_is_unchanged(self):
        prompt = _prompt()
        self.assertIn("Distinguish: (a) bad code on the", prompt)
        self.assertIn("Do NOT attempt to fix anything", prompt)
        self.assertIn(REPORT, prompt)


# ── 4. the steps, and where they sit ────────────────────────────────────────
class TheStepsTest(unittest.TestCase):
    def test_the_verdict_and_resume_steps_sit_between_agent_and_delivery(self):
        self.assertLess(_index("claude"), _index("verdict"))
        self.assertLess(_index("verdict"), _index("resume"))
        self.assertLess(_index("resume"), _index("deliver"))

    def test_the_verdict_step_reads_the_file_through_the_script(self):
        run = _step("verdict")["run"]
        self.assertIn("medic_resume.py verdict", run)
        self.assertIn(VERDICT, run)
        self.assertIn("verdict=", run)

    def test_the_resume_step_reruns_the_failed_jobs_of_the_same_run(self):
        step = _step("resume")
        self.assertIn('gh run rerun "$RUN_ID" --failed', step["run"])
        self.assertEqual("${{ github.event.workflow_run.id }}", step["env"]["RUN_ID"])
        self.assertIn("resumed=true", step["run"])

    def test_no_step_puts_run_text_on_a_shell_line(self):
        for step_id in ("verdict", "resume"):
            self.assertNotIn("${{", _step(step_id)["run"], step_id)

    def test_the_resume_step_writes_nothing_to_linear_or_the_console(self):
        step = _step("resume")
        self.assertNotIn("linear_ops.py", step["run"])
        self.assertNotIn("pipeline_notice.py", step["run"])
        self.assertNotIn("LINEAR_API_KEY", step.get("env") or {})


# ── 5. the conditions, replayed ─────────────────────────────────────────────
class TheResumeConditionTest(unittest.TestCase):
    def _cond(self):
        return _step("resume")["if"]

    def test_it_resumes_attempt_2_on_a_resume_verdict(self):
        self.assertTrue(_runs(self._cond(), _resume_ctx()))

    def test_an_unread_retry_gate_still_resumes(self):
        # `retry != 'false'`, exactly as the `retry` job reads it.
        self.assertTrue(_runs(self._cond(), _resume_ctx(retry="")))

    def test_attempt_1_never_resumes(self):
        self.assertFalse(_runs(self._cond(), _resume_ctx(attempt=1)))

    def test_attempt_3_never_resumes(self):
        self.assertFalse(_runs(self._cond(), _resume_ctx(attempt=3)))

    def test_a_retry_gate_refusal_never_resumes(self):
        self.assertFalse(_runs(self._cond(), _resume_ctx(retry="false")))

    def test_a_hold_verdict_never_resumes(self):
        self.assertFalse(_runs(self._cond(), _resume_ctx(verdict="hold")))

    def test_no_verdict_never_resumes(self):
        self.assertFalse(_runs(self._cond(), _resume_ctx(verdict="")))
        self.assertFalse(_runs(self._cond(), _resume_ctx(verdict=None)))


class TheDeliverConditionTest(unittest.TestCase):
    def _cond(self):
        return _step("deliver")["if"]

    def test_delivery_is_skipped_after_a_resume(self):
        self.assertFalse(_runs(self._cond(), {"steps.resume.outputs.resumed": "true"}))

    def test_delivery_runs_after_a_hold_verdict(self):
        # The resume step was skipped, so it set no output at all.
        self.assertTrue(_runs(self._cond(), {}))
        self.assertTrue(_runs(self._cond(), {"steps.resume.outputs.resumed": ""}))

    def test_delivery_runs_when_the_rerun_could_not_be_started(self):
        self.assertTrue(_runs(self._cond(), {"steps.resume.outputs.resumed": "false"}))

    def test_delivery_still_runs_after_a_failed_agent(self):
        self.assertIn("always()", self._cond())


class TheDiagnoseGateIsUnchangedTest(unittest.TestCase):
    def test_the_job_gate_is_byte_for_byte_what_it_was(self):
        self.assertEqual(
            "(github.event.workflow_run.conclusion == 'failure' || "
            "github.event.workflow_run.conclusion == 'timed_out') && "
            "github.event.workflow_run.run_attempt >= 2 && "
            "needs.classify.outputs.infra_crash != 'true' && "
            "needs.classify.outputs.class != 'environment_crash' && "
            "needs.classify.outputs.class != 'out_of_memory' && "
            "needs.classify.outputs.class != 'upstream_5xx' && "
            "needs.classify.outputs.class != 'linear_ratelimited' && "
            "needs.classify.outputs.class != 'standing_defect' && "
            "needs.classify.outputs.rule != 'refused-by-design' && "
            "needs.classify.outputs.rule != 'superseded-on-default' && "
            "needs.classify.outputs.limit != 'true'",
            " ".join(_jobs()["diagnose"]["if"].split()))


# ── 6. the steps, run ───────────────────────────────────────────────────────
FAKE_GH = """#!/usr/bin/env bash
echo "gh $*" >> "$CALLS"
exit "${FAKE_GH_RC:-0}"
"""


def _run_step(step_id, *, verdict=None, report="the report", gh_rc=0,
              attempt="2"):
    step = _step(step_id)
    values = {
        "github.token": "test-token",
        "github.event.workflow_run.id": RUN_ID,
        "github.event.workflow_run.run_attempt": attempt,
        "github.event.workflow_run.html_url": RUN_URL,
        "github.event.workflow_run.name": "Agent Task",
        "github.repository": "dreadnought-foundry/atlas",
    }

    def render(text):
        def sub(m):
            key = m.group(1).strip()
            if key not in values:
                raise AssertionError(f"the step reads {key!r}, which this test does not fake")
            return values[key]
        return re.sub(r"\$\{\{\s*([^}]+?)\s*\}\}", sub, text)

    env = {k: render(str(v)) for k, v in (step.get("env") or {}).items()}
    with tempfile.TemporaryDirectory() as raw:
        td = Path(raw)
        (td / "bin").mkdir()
        (td / "bin" / "gh").write_text(FAKE_GH)
        os.chmod(td / "bin" / "gh", 0o755)
        (td / ".bureau-pipeline").mkdir()
        os.symlink(os.path.join(ROOT, "scripts"), td / ".bureau-pipeline" / "scripts")
        if verdict is not None:
            (td / VERDICT).write_text(verdict)
        if report is not None:
            (td / REPORT).write_text(report)
        calls, output, summary = td / "calls.log", td / "output", td / "summary.md"
        for p in (calls, output, summary):
            p.touch()
        proc = subprocess.run(
            ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", step["run"]],
            cwd=td, capture_output=True, text=True, check=False,
            env={**os.environ, **env,
                 "PATH": f"{td / 'bin'}:{os.path.dirname(sys.executable)}:{os.environ['PATH']}",
                 "CALLS": str(calls), "FAKE_GH_RC": str(gh_rc),
                 "GITHUB_OUTPUT": str(output), "GITHUB_STEP_SUMMARY": str(summary)})
        outputs = dict(ln.split("=", 1) for ln in output.read_text().splitlines() if "=" in ln)
        return (proc, outputs, summary.read_text(),
                [ln for ln in calls.read_text().splitlines() if ln])


class TheStepsRunTest(unittest.TestCase):
    def test_the_verdict_step_outputs_resume(self):
        proc, outputs, _, _ = _run_step("verdict", verdict="resume\n")
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual("resume", outputs["verdict"])

    def test_the_verdict_step_outputs_hold_with_no_file(self):
        proc, outputs, _, _ = _run_step("verdict", verdict=None)
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual("hold", outputs["verdict"])

    def test_a_resume_reruns_the_run_and_writes_the_summary(self):
        proc, outputs, summary, calls = _run_step(
            "resume", report="GitHub's package registry answered 502.")
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual(
            [f"gh run rerun {RUN_ID} --failed --repo dreadnought-foundry/atlas"], calls)
        self.assertEqual("true", outputs["resumed"])
        self.assertIn("attempt 3", summary)
        self.assertIn("GitHub's package registry answered 502.", summary)

    def test_a_rerun_that_could_not_start_falls_back_to_delivery(self):
        proc, outputs, summary, calls = _run_step("resume", gh_rc=1)
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual(1, len(calls))
        self.assertEqual("false", outputs["resumed"])
        self.assertIn("::warning", proc.stdout + proc.stderr)
        self.assertEqual("", summary)


if __name__ == "__main__":
    unittest.main()
