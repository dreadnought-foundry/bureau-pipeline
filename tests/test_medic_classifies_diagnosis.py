"""The medic's diagnosis names one of the five declared classes, and its one
re-run follows the class (DRE-7793, the first half of the medic's next steps).

Until this card the diagnosis agent wrote one word, `resume` or `hold`
(DRE-7526), and `resume` re-ran the failed run once. DRE-7792 made the five
classes the weekly stop report reads — `transient-infrastructure`, `bad-card`,
`bad-code`, `platform-fault`, `unreadable` — the medic's own vocabulary, each
with one declared next step (`scripts/medic_next_step.py`). This card switches
the agent to those words and decides the step:

  * the prompt's step 6 asks for one class word, each defined in plain words,
    and names neither `resume` nor `hold`;
  * `Read the diagnosis verdict` runs on every path (`always()`), reads a
    dead agent or a missing report as `unreadable`, and runs `decide` with
    `--resume-spent` (DRE-7526's attempt-2 and retry-gate condition),
    `--has-open-pr` (only an OPEN pull request) and `--planning-spent` (only
    for `bad-card`, read off the card's whole thread). Its outputs are
    `class` and `step`; for a `card` target `step` is never empty;
  * the resume step is gated on `step == 'requeue-once'`, otherwise unchanged.

Until DRE-7799 and DRE-7794 land nothing else reads `step`, so every class
but `requeue-once` is delivered exactly as before.
"""

import json
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

import medic_next_step

MEDIC = os.path.join(ROOT, ".github", "workflows", "medic.yml")
VERDICT = ".bureau-pipeline/medic-verdict.txt"
REPORT = ".bureau-pipeline/medic-report.md"
CARD = "DRE-4242"
PR_URL = "https://github.com/dreadnought-foundry/atlas/pull/77"


def _text() -> str:
    with open(MEDIC, encoding="utf-8") as f:
        return f.read()


def _jobs() -> dict:
    return yaml.safe_load(_text())["jobs"]


def _steps() -> list:
    return _jobs()["diagnose"]["steps"]


def _step(step_id: str) -> dict:
    return next(s for s in _steps() if s.get("id") == step_id)


def _prompt() -> str:
    return _step("claude")["with"]["prompt"]


def _flat(text: str) -> str:
    return " ".join(text.split())


# ── an Actions `if:` evaluated the way Actions evaluates it ─────────────────
# Copied from tests/test_medic_resume.py: `||`/`&&` return an operand,
# `==`/`!=` compare, a missing property is null.
_IDENT = re.compile(r"'[^']*'|\b[a-z_]+(?:\.[a-z_]+)+\b")


def _runs(condition: str, ctx: dict) -> bool:
    py = _IDENT.sub(
        lambda m: m.group(0) if m.group(0).startswith("'")
        else f"ctx.get({m.group(0)!r})", condition)
    py = py.replace("||", " or ").replace("&&", " and ")
    py = " ".join(py.split())  # a folded `if:` keeps its indented newlines
    return bool(eval(py, {"ctx": ctx, "always": lambda: True}))


# ── 1. the prompt ───────────────────────────────────────────────────────────
class ThePromptTest(unittest.TestCase):
    def test_step_6_asks_for_one_of_the_five_class_words(self):
        prompt = _prompt()
        self.assertIn(VERDICT, prompt)
        for cls in medic_next_step.CLASSES:
            with self.subTest(cls=cls):
                self.assertRegex(prompt, rf"- {re.escape(cls)}: \S")

    def test_each_class_is_defined_in_plain_words(self):
        flat = _flat(_prompt())
        self.assertRegex(flat, r"- transient-infrastructure: [^-]*fresh attempt")
        self.assertRegex(flat, r"- bad-card: [^-]*card as written cannot be built")
        self.assertRegex(flat, r"- bad-code: [^-]*defect on the branch")
        self.assertRegex(flat, r"- platform-fault: [^-]*pipeline's own")
        self.assertRegex(flat, r"- unreadable: [^-]*any doubt")

    def test_the_four_categories_map_to_the_class_words(self):
        flat = _flat(_prompt())
        self.assertIn("Distinguish: (a) bad code on the branch", flat)
        self.assertRegex(flat, r"transient-infrastructure:[^.]*\(c\)")
        self.assertRegex(flat, r"bad-code:[^.]*\(a\)")
        self.assertRegex(flat, r"platform-fault:[^.]*\(b\) and \(d\)")

    def test_the_prompt_names_neither_resume_nor_hold(self):
        prompt = _prompt()
        self.assertNotRegex(prompt, r"\bresume\b")
        self.assertNotRegex(prompt, r"\bhold\b")


# ── 2. the verdict step, as written ─────────────────────────────────────────
class TheVerdictStepTest(unittest.TestCase):
    def setUp(self):
        self.step = _step("verdict")
        self.run_ = self.step["run"]
        self.env = self.step.get("env") or {}

    def test_it_keeps_its_name_and_id(self):
        self.assertEqual("Read the diagnosis verdict", self.step["name"])

    def test_it_runs_on_every_path(self):
        self.assertEqual("always()", self.step["if"].strip())

    def test_it_reads_the_agent_outcome(self):
        self.assertEqual("${{ steps.claude.outcome }}", self.env["AGENT_OUTCOME"])

    def test_it_carries_the_tokens_its_two_lookups_need(self):
        self.assertEqual("${{ github.token }}", self.env["GH_TOKEN"])
        self.assertEqual("${{ github.repository }}", self.env["REPO"])
        self.assertEqual("${{ secrets.LINEAR_API_KEY }}", self.env["LINEAR_API_KEY"])

    def test_it_reads_dre_7526s_attempt_and_retry_gate(self):
        values = set(self.env.values())
        self.assertIn("${{ github.event.workflow_run.run_attempt }}", values)
        self.assertIn("${{ needs.classify.outputs.retry }}", values)
        self.assertIn("${{ steps.target.outputs.kind }}", values)
        self.assertIn("${{ steps.target.outputs.target }}", values)

    def test_it_calls_read_decide_find_and_planning_spent(self):
        flat = _flat(self.run_)
        self.assertIn("medic_next_step.py\" read --file " + VERDICT, flat)
        self.assertIn("medic_next_step.py\" decide", flat)
        for flag in ("--resume-spent", "--has-open-pr", "--planning-spent"):
            self.assertIn(flag, flat)
        self.assertIn('card_pr.py" find "$TARGET" --repo "$REPO"', flat)
        self.assertIn('linear_ops.py" dump-comments "$TARGET"', flat)
        self.assertIn("medic_next_step.py\" planning-spent --comments-file", flat)

    def test_it_outputs_class_and_step(self):
        self.assertIn('"class=$CLASS"', self.run_)
        self.assertIn('"step=$STEP"', self.run_)

    def test_no_run_text_on_a_shell_line(self):
        self.assertNotIn("${{", self.run_)

    def test_it_no_longer_reads_resume_or_hold(self):
        self.assertNotIn("medic_resume.py", self.run_)


# ── 3. the resume step follows the step ─────────────────────────────────────
def _resume_ctx(*, attempt=2, retry="true", step="requeue-once") -> dict:
    return {
        "github.event.workflow_run.run_attempt": attempt,
        "needs.classify.outputs.retry": retry,
        "steps.verdict.outputs.step": step,
    }


class TheResumeFollowsTheStepTest(unittest.TestCase):
    def _cond(self):
        return _step("resume")["if"]

    def test_the_gate_is_dre_7526s_with_the_step(self):
        self.assertEqual(
            "github.event.workflow_run.run_attempt == 2 && "
            "needs.classify.outputs.retry != 'false' && "
            "steps.verdict.outputs.step == 'requeue-once' && "
            # DRE-7799: a card waiting out a limit is the wait's.
            "steps.waiting.outputs.waiting != 'true'",
            _flat(self._cond()))

    def test_it_resumes_attempt_2_on_requeue_once(self):
        self.assertTrue(_runs(self._cond(), _resume_ctx()))

    def test_an_unread_retry_gate_still_resumes(self):
        self.assertTrue(_runs(self._cond(), _resume_ctx(retry="")))

    def test_attempt_1_and_3_never_resume(self):
        self.assertFalse(_runs(self._cond(), _resume_ctx(attempt=1)))
        self.assertFalse(_runs(self._cond(), _resume_ctx(attempt=3)))

    def test_a_retry_gate_refusal_never_resumes(self):
        self.assertFalse(_runs(self._cond(), _resume_ctx(retry="false")))

    def test_every_other_step_and_none_never_resume(self):
        for step in ("send-to-planning", "fix-path", "ask-person", "", None):
            with self.subTest(step=step):
                self.assertFalse(_runs(self._cond(), _resume_ctx(step=step)))

    def test_the_resume_step_keeps_no_always(self):
        self.assertNotIn("always()", self._cond())

    def test_no_step_reads_a_verdict_output_or_the_old_words(self):
        text = _text()
        self.assertNotIn("outputs.verdict", text)
        self.assertNotIn("medic_resume.py verdict", text)
        for step in _steps():
            self.assertNotIn("== 'resume'", str(step.get("if", "")), step.get("name"))
            self.assertNotIn("== 'hold'", str(step.get("if", "")), step.get("name"))


# ── 4. the verdict step, run ────────────────────────────────────────────────
# Each pipeline script the step calls is a stub that logs its call. The
# `medic_next_step.py` stub runs the real one, except where a test makes
# `decide` exit non-zero or `planning-spent` print a fixed line.
STUB_LOG = """
import os, sys
with open(os.environ["CALLS"], "a") as f:
    f.write(os.path.basename(__file__) + " " + " ".join(sys.argv[1:]) + "\\n")
"""

STUB_CARD_PR = "#!/usr/bin/env python3" + STUB_LOG + """
out = os.environ.get("FAKE_FIND_OUT", "")
if out:
    sys.stdout.write(out + "\\n")
sys.exit(int(os.environ.get("FAKE_FIND_RC", "0")))
"""

STUB_LINEAR_OPS = "#!/usr/bin/env python3" + STUB_LOG + """
if os.environ.get("FAKE_DUMP_RC", "0") != "0":
    sys.exit(int(os.environ["FAKE_DUMP_RC"]))
sys.stdout.write(os.environ.get("FAKE_DUMP_OUT", "[]"))
"""

STUB_NEXT_STEP = "#!/usr/bin/env python3" + STUB_LOG + """
import subprocess
cmd = sys.argv[1] if len(sys.argv) > 1 else ""
if cmd == "decide" and os.environ.get("FAKE_DECIDE_RC"):
    sys.exit(int(os.environ["FAKE_DECIDE_RC"]))
if cmd == "planning-spent" and os.environ.get("FAKE_PLANNING_SPENT"):
    print(os.environ["FAKE_PLANNING_SPENT"])
    sys.exit(0)
sys.exit(subprocess.call([sys.executable, os.environ["REAL_NEXT_STEP"], *sys.argv[1:]]))
"""


def _run_verdict(*, verdict="bad-code", report="The tests fail on the branch.",
                 outcome="success", kind="card", target=CARD, attempt="2",
                 retry="true", find_out="", find_rc=0, dump_out="[]",
                 dump_rc=0, planning_spent=None, decide_rc=None):
    step = _step("verdict")
    values = {
        "steps.claude.outcome": outcome,
        "steps.target.outputs.kind": kind,
        "steps.target.outputs.target": target,
        "github.event.workflow_run.run_attempt": attempt,
        "needs.classify.outputs.retry": retry,
        "github.token": "test-token",
        "github.repository": "dreadnought-foundry/atlas",
        "secrets.LINEAR_API_KEY": "test-linear-key",
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
        scripts = td / ".bureau-pipeline" / "scripts"
        scripts.mkdir(parents=True)
        for name, body in (("card_pr.py", STUB_CARD_PR),
                           ("linear_ops.py", STUB_LINEAR_OPS),
                           ("medic_next_step.py", STUB_NEXT_STEP)):
            (scripts / name).write_text(body)
            os.chmod(scripts / name, 0o755)
        if verdict is not None:
            (td / VERDICT).write_text(verdict)
        if report is not None:
            (td / REPORT).write_text(report)
        calls, output = td / "calls.log", td / "output"
        for p in (calls, output):
            p.touch()
        fake = {"FAKE_FIND_OUT": find_out, "FAKE_FIND_RC": str(find_rc),
                "FAKE_DUMP_OUT": dump_out, "FAKE_DUMP_RC": str(dump_rc)}
        if planning_spent is not None:
            fake["FAKE_PLANNING_SPENT"] = planning_spent
        if decide_rc is not None:
            fake["FAKE_DECIDE_RC"] = str(decide_rc)
        proc = subprocess.run(
            ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", step["run"]],
            cwd=td, capture_output=True, text=True, check=False,
            env={**os.environ, **env, **fake,
                 "PATH": f"{os.path.dirname(sys.executable)}:{os.environ['PATH']}",
                 "REAL_NEXT_STEP": os.path.join(ROOT, "scripts", "medic_next_step.py"),
                 "CALLS": str(calls), "GITHUB_OUTPUT": str(output),
                 "RUNNER_TEMP": str(td)})
        outputs = dict(ln.split("=", 1) for ln in output.read_text().splitlines() if "=" in ln)
        return proc, outputs, [ln for ln in calls.read_text().splitlines() if ln]


def _decide_call(calls):
    return next(c for c in calls if c.startswith("medic_next_step.py decide"))


class TheOpenPullRequestTest(unittest.TestCase):
    def test_an_open_pull_request_decides_fix_path(self):
        proc, outputs, calls = _run_verdict(find_out=f"OPEN\t{PR_URL}")
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertIn("--has-open-pr", _decide_call(calls))
        self.assertEqual("bad-code", outputs["class"])
        self.assertEqual("fix-path", outputs["step"])
        self.assertIn(f"card_pr.py find {CARD} --repo dreadnought-foundry/atlas", calls)

    def test_merged_empty_and_unreadable_pass_no_flag(self):
        for find_out, find_rc in ((f"MERGED\t{PR_URL}", 0), ("", 0), ("", 3)):
            with self.subTest(find_out=find_out, find_rc=find_rc):
                proc, outputs, calls = _run_verdict(find_out=find_out, find_rc=find_rc)
                self.assertEqual(0, proc.returncode, proc.stderr)
                self.assertNotIn("--has-open-pr", _decide_call(calls))
                self.assertEqual("bad-code", outputs["class"])
                self.assertEqual("ask-person", outputs["step"])

    def test_an_unreadable_lookup_warns_with_its_exit_code(self):
        proc, _, _ = _run_verdict(find_rc=3)
        self.assertRegex(proc.stdout, r"::warning[^\n]*\b3\b")

    def test_a_readable_lookup_does_not_warn(self):
        proc, _, _ = _run_verdict(find_out=f"MERGED\t{PR_URL}")
        self.assertNotIn("::warning", proc.stdout)

    def test_the_state_is_the_first_field_exactly(self):
        proc, _, calls = _run_verdict(find_out=f"OPENED\t{PR_URL}")
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertNotIn("--has-open-pr", _decide_call(calls))


class ThePlanningBoundTest(unittest.TestCase):
    def test_a_first_bad_card_is_sent_to_planning(self):
        proc, outputs, calls = _run_verdict(verdict="bad-card",
                                            planning_spent="planning_spent=false")
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertNotIn("--planning-spent", _decide_call(calls))
        self.assertEqual("bad-card", outputs["class"])
        self.assertEqual("send-to-planning", outputs["step"])
        self.assertIn(f"linear_ops.py dump-comments {CARD}", calls)

    def test_spent_unknown_and_an_unread_thread_ask_a_person(self):
        for spent, dump_rc in (("planning_spent=true", 0),
                               ("planning_spent=unknown", 0),
                               (None, 1)):
            with self.subTest(spent=spent, dump_rc=dump_rc):
                proc, outputs, calls = _run_verdict(
                    verdict="bad-card", planning_spent=spent, dump_rc=dump_rc)
                self.assertEqual(0, proc.returncode, proc.stderr)
                self.assertIn("--planning-spent", _decide_call(calls))
                self.assertEqual("bad-card", outputs["class"])
                self.assertEqual("ask-person", outputs["step"])

    def test_the_thread_is_read_through_the_real_receipt_reader(self):
        receipt = (f"{medic_next_step.RECEIPT_MARK} class=bad-card "
                   f"step=send-to-planning run=123\n\nSent back.")
        _, outputs, _ = _run_verdict(verdict="bad-card",
                                     dump_out=json.dumps(["a comment", receipt]))
        self.assertEqual("ask-person", outputs["step"])
        _, outputs, _ = _run_verdict(verdict="bad-card",
                                     dump_out=json.dumps(["a comment"]))
        self.assertEqual("send-to-planning", outputs["step"])

    def test_no_other_class_reads_the_thread(self):
        for verdict in ("bad-code", "transient-infrastructure",
                        "platform-fault", "unreadable"):
            with self.subTest(verdict=verdict):
                _, _, calls = _run_verdict(verdict=verdict)
                self.assertFalse(any(c.startswith("linear_ops.py") for c in calls), calls)
                self.assertFalse(any("planning-spent" in c for c in calls), calls)


class TheResumeSpentTest(unittest.TestCase):
    def test_attempt_2_with_the_gate_open_requeues_once(self):
        for retry in ("true", ""):
            with self.subTest(retry=retry):
                _, outputs, calls = _run_verdict(verdict="transient-infrastructure",
                                                 retry=retry)
                self.assertNotIn("--resume-spent", _decide_call(calls))
                self.assertEqual("requeue-once", outputs["step"])

    def test_attempt_3_or_a_gate_refusal_is_spent(self):
        for attempt, retry in (("3", "true"), ("2", "false")):
            with self.subTest(attempt=attempt, retry=retry):
                _, outputs, calls = _run_verdict(verdict="transient-infrastructure",
                                                 attempt=attempt, retry=retry)
                self.assertIn("--resume-spent", _decide_call(calls))
                self.assertEqual("ask-person", outputs["step"])


class TheDeadAgentTest(unittest.TestCase):
    def _assert_unreadable(self, proc, outputs):
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual("unreadable", outputs["class"])
        self.assertEqual("ask-person", outputs["step"])

    def test_a_failed_agent_is_unreadable_whatever_its_verdict(self):
        proc, outputs, calls = _run_verdict(verdict="bad-card", outcome="failure")
        self._assert_unreadable(proc, outputs)
        self.assertFalse(any(c.startswith("medic_next_step.py read") for c in calls))
        self.assertFalse(any(c.startswith("linear_ops.py") for c in calls))

    def test_a_cancelled_or_skipped_agent_is_unreadable(self):
        for outcome in ("cancelled", "skipped", ""):
            with self.subTest(outcome=outcome):
                proc, outputs, _ = _run_verdict(verdict="bad-code", outcome=outcome)
                self._assert_unreadable(proc, outputs)

    def test_no_report_is_unreadable(self):
        proc, outputs, calls = _run_verdict(verdict="bad-card", report=None)
        self._assert_unreadable(proc, outputs)
        self.assertFalse(any(c.startswith("medic_next_step.py read") for c in calls))

    def test_an_empty_report_is_unreadable(self):
        proc, outputs, _ = _run_verdict(verdict="bad-card", report="")
        self._assert_unreadable(proc, outputs)

    def test_a_decide_that_fails_asks_a_person(self):
        proc, outputs, _ = _run_verdict(verdict="bad-code", decide_rc=1)
        self._assert_unreadable(proc, outputs)

    def test_a_missing_verdict_file_reads_unreadable(self):
        proc, outputs, calls = _run_verdict(verdict=None)
        self._assert_unreadable(proc, outputs)
        self.assertTrue(any(c.startswith("medic_next_step.py read") for c in calls))

    def test_a_good_bad_card_diagnosis_is_sent_to_planning(self):
        proc, outputs, _ = _run_verdict(verdict="bad-card", outcome="success",
                                        planning_spent="planning_spent=false")
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual("bad-card", outputs["class"])
        self.assertEqual("send-to-planning", outputs["step"])

    def test_the_old_words_read_unreadable(self):
        for word in ("resume", "hold"):
            with self.subTest(word=word):
                proc, outputs, _ = _run_verdict(verdict=word)
                self._assert_unreadable(proc, outputs)


class TheOtherKindsTest(unittest.TestCase):
    def test_a_repair_card_or_notice_has_an_empty_step(self):
        for kind in ("repair-card", "notice", "unknown", ""):
            with self.subTest(kind=kind):
                proc, outputs, calls = _run_verdict(verdict="bad-code", kind=kind)
                self.assertEqual(0, proc.returncode, proc.stderr)
                self.assertEqual("bad-code", outputs["class"])
                self.assertEqual("", outputs["step"])
                self.assertFalse(any(c.startswith("card_pr.py") for c in calls))

    def test_a_dead_agent_on_a_notice_still_leaves_an_empty_step(self):
        proc, outputs, _ = _run_verdict(outcome="failure", kind="notice")
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual("unreadable", outputs["class"])
        self.assertEqual("", outputs["step"])


if __name__ == "__main__":
    unittest.main()
