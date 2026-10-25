"""A medic diagnosis that needs a person parks the card in Green Light with
the three lines, never as a bare comment (DRE-7794, the last of the medic's
next steps).

DRE-7793 gave every diagnosis a class and a step, and DRE-7799 made the medic
move the card for three of the four steps. The fourth, `ask-person`, delivered
the diagnosis and left the card where it was. This card adds, in the
`diagnose` job of medic.yml:

  * one more file the diagnosis agent writes, `.bureau-pipeline/medic-question.json`
    (`finding`, `question`, `recommendation`, `why`), read only on `ask-person`
    and only through `medic_next_step.py question`, which guards every line;
  * `Ask a person about the diagnosis` (id `ask`), before the one `receipt`
    step: the `🙋` question with the three lines `console_escalation` renders,
    posted with no act, then `linear_ops.py advance "$TARGET" "Green Light"
    "$FROM_LANES"`, then `moved=true|false`;
  * `ask-person` in the `receipt` step's gate, so the move is written on the
    card once, as `step=ask-person`, ` moved=no` when it did not land.

The steps are run here as the job runs them — in file order, each `if:`
evaluated against the outputs the steps before it wrote — with every Linear,
GitHub and card-PR call a stub that logs what it was asked, and
`medic_next_step.py` the real script. The verdict step is run too, so the
path where the diagnosis agent itself died is walked from its outcome.
"""

import copy
import itertools
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar

import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# `reconcile` reads these at import, and `green_light_rows` reaches it through
# `ready_lane_writers` to read its published `destinations()`.
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import check_act_receipts  # noqa: E402
import console_escalation  # noqa: E402
import medic_next_step  # noqa: E402
import planning_escalation  # noqa: E402
from test_medic_acts_on_diagnosis import (  # noqa: E402
    CARD,
    PR_URL,
    RUN_ID,
    RUN_URL,
    STUB_GH,
    STUB_HEAD,
    STUBS,
    Run,
    _marker,
    _runs,
)

#: `medic_next_step.py`, the real script — except `question`, which exits
#: FAKE_QUESTION_RC when it is set: the renderer itself failing.
STUB_MEDIC_NEXT_STEP = STUB_HEAD + """
if cmd == "question" and os.environ.get("FAKE_QUESTION_RC"):
    sys.exit(int(os.environ["FAKE_QUESTION_RC"]))
real = os.path.join(os.environ["REAL_SCRIPTS"], os.path.basename(__file__))
sys.exit(subprocess.call([sys.executable, real, *sys.argv[1:]]))
"""

MEDIC = os.path.join(ROOT, ".github", "workflows", "medic.yml")
ACTS = os.path.join(ROOT, "config", "pipeline-acts.json")
REPORT = ".bureau-pipeline/medic-report.md"
VERDICT = ".bureau-pipeline/medic-verdict.txt"
QUESTION_FILE = ".bureau-pipeline/medic-question.json"
STEP_NAME = "Ask a person about the diagnosis"
ASK_RECEIPT = "🙋 The medic diagnosed a failure only a person can settle"
QUESTION_CALL = ("python3 .bureau-pipeline/scripts/medic_next_step.py question "
                 "--file .bureau-pipeline/medic-question.json "
                 "--report .bureau-pipeline/medic-report.md")
REPORT_TEXT = ("The pipeline could not reach the card tracker because its access "
               "was refused. A person needs to renew that access before the card "
               "can run.\n\nTechnical: the tracker answered 401 to every call.\n")
REPORT_FINDING = ("The pipeline could not reach the card tracker because its "
                  "access was refused.")
PLAIN_QUESTION = {
    "finding": "The pipeline lost its access to the card tracker, so the build stopped.",
    "question": "Can the operator who looks after the pipeline renew its access to "
                "the card tracker?",
    "recommendation": "Renew the access and run the card again",
    "why": "the card itself is fine and only the access is missing",
}
#: The class each verdict word decides, and the flags that keep it on its step.
STEP_OF = {"transient-infrastructure": "requeue-once", "bad-card": "send-to-planning",
           "bad-code": "fix-path", "platform-fault": "ask-person",
           "unreadable": "ask-person"}
#: What medic.yml does for each step: the move it makes, or — `fix-path` — the
#: owner it names on the card's open pull request. A step in
#: `medic_next_step.STEPS` with no entry here is a step nothing handles.
HANDLED_BY = {
    "requeue-once": 'gh run rerun "$RUN_ID" --failed',
    "send-to-planning": 'linear_ops.py advance "$TARGET" "Planning" "$FROM_LANES"',
    "ask-person": 'linear_ops.py advance "$TARGET" "Green Light" "$FROM_LANES"',
    "fix-path": "the fix loop already reaches a red agent pull request",
}


def _text() -> str:
    with open(MEDIC, encoding="utf-8") as f:
        return f.read()


def _steps() -> list:
    return yaml.safe_load(_text())["jobs"]["diagnose"]["steps"]


def _step(step_id: str) -> dict:
    return next(s for s in _steps() if s.get("id") == step_id)


def _index(step_id: str) -> int:
    return next(i for i, s in enumerate(_steps()) if s.get("id") == step_id)


def _flat(text) -> str:
    return " ".join(str(text).replace("\\\n", " ").split())


def _three_lines(body: str) -> list:
    prefixes = (console_escalation.FINDING_PREFIX, console_escalation.QUESTION_PREFIX,
                console_escalation.RECOMMENDATION_PREFIX)
    return [line for line in body.split("\n") if line.startswith(prefixes)]


def _check_cli(body: str) -> subprocess.CompletedProcess:
    """`console_escalation.py check` on a posted body, as a reader would run it."""
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "body.md"
        path.write_text(body, encoding="utf-8")
        return subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "console_escalation.py"),
             "check", str(path)], capture_output=True, text=True, check=False)


#: The steps run, in the order the job runs them.
SIMULATED = ("waiting", "verdict", "resume", "held", "deliver", "planning", "ask",
             "receipt")


def _run_job(*, verdict="platform-fault", agent="success", report=REPORT_TEXT,
             question=PLAIN_QUESTION, thread=None, find_out=f"OPEN\t{PR_URL}",
             find_rc=0, advance="land", state="In Progress", state_rc=0,
             comment_rc=0, gh_rc=0, attempt=2, thread_dump=None,
             question_rc=None) -> Run:
    ctx = {
        "secrets.LINEAR_API_KEY": "test-linear-key",
        "github.token": "test-token",
        "github.repository": "dreadnought-foundry/atlas",
        "github.event.workflow_run.id": RUN_ID,
        "github.event.workflow_run.run_attempt": attempt,
        "github.event.workflow_run.html_url": RUN_URL,
        "github.event.workflow_run.name": "Agent Task",
        "github.event.workflow_run.path": ".github/workflows/agent-task.yml",
        "github.event.workflow_run.head_branch": f"agent/{CARD}-x",
        # An Agent Task run is dispatched, never a pull-request run (DRE-7925).
        "github.event.workflow_run.event": "repository_dispatch",
        "github.event.workflow_run.head_sha": "abc123",
        "github.event.repository.default_branch": "main",
        "vars.BUREAU_READ_URL": "",
        "needs.classify.outputs.retry": "true",
        "steps.repo.outputs.slug": "atlas",
        "steps.target.outputs.kind": "card",
        "steps.target.outputs.target": CARD,
        "steps.target.outputs.title": "",
        "steps.claude.outcome": agent,
    }

    def render(text):
        def sub(m):
            key = m.group(1).strip()
            if key.startswith("steps."):
                return str(ctx.get(key) or "")
            if key not in ctx:
                raise AssertionError(f"the step reads {key!r}, which this test does not fake")
            return str(ctx[key])
        return re.sub(r"\$\{\{\s*([^}]+?)\s*\}\}", sub, text)

    ran, log = {}, []
    with tempfile.TemporaryDirectory() as raw:
        td = Path(raw)
        scripts, bin_ = td / ".bureau-pipeline" / "scripts", td / "bin"
        scripts.mkdir(parents=True)
        bin_.mkdir()
        for name, body in {**STUBS, "medic_next_step.py": STUB_MEDIC_NEXT_STEP}.items():
            (scripts / name).write_text(body, encoding="utf-8")
            os.chmod(scripts / name, 0o755)
        (bin_ / "gh").write_text(STUB_GH, encoding="utf-8")
        os.chmod(bin_ / "gh", 0o755)
        if report is not None:
            (td / REPORT).write_text(report, encoding="utf-8")
        (td / VERDICT).write_text(verdict + "\n", encoding="utf-8")
        if isinstance(question, dict):
            (td / QUESTION_FILE).write_text(json.dumps(question), encoding="utf-8")
        elif question is not None:
            (td / QUESTION_FILE).write_text(question, encoding="utf-8")
        calls, summary = td / "calls.log", td / "summary.md"
        calls.touch()
        summary.touch()
        fake = {"FAKE_THREAD": thread_dump or json.dumps(thread or []),
                "FAKE_DUMP_RC": "0", "FAKE_GH_RC": str(gh_rc), "FAKE_ADVANCE": advance,
                "FAKE_STATE": state, "FAKE_STATE_RC": str(state_rc),
                "FAKE_FIND_OUT": find_out, "FAKE_FIND_RC": str(find_rc),
                "FAKE_COMMENT_RC": str(comment_rc), "FAKE_REPAIR": "",
                "FAKE_QUESTION_RC": str(question_rc or "")}
        for sid in sorted(SIMULATED, key=_index):
            step_ = _step(sid)
            cond = step_.get("if")
            if cond is not None and not _runs(str(cond), ctx):
                ran[sid] = False
                continue
            ran[sid] = True
            output = td / f"output-{sid}"
            output.touch()
            env = {k: render(str(v)) for k, v in (step_.get("env") or {}).items()}
            proc = subprocess.run(
                ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", step_["run"]],
                cwd=td, capture_output=True, text=True, check=False,
                env={**os.environ, **env, **fake,
                     "PATH": f"{bin_}:{os.path.dirname(sys.executable)}:{os.environ['PATH']}",
                     "PYTHONIOENCODING": "utf-8",
                     "REAL_SCRIPTS": os.path.join(ROOT, "scripts"),
                     "CALLS": str(calls), "GITHUB_OUTPUT": str(output),
                     "GITHUB_STEP_SUMMARY": str(summary), "RUNNER_TEMP": str(td)})
            if proc.returncode != 0:
                raise AssertionError(f"step {sid} exited {proc.returncode}:\n"
                                     f"{proc.stdout}\n{proc.stderr}")
            log.append(proc.stdout + proc.stderr)
            for line in output.read_text(encoding="utf-8").splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    ctx[f"steps.{sid}.outputs.{k}"] = v
        parsed = [json.loads(ln) for ln in calls.read_text(encoding="utf-8").splitlines() if ln]
        return Run(parsed, ran, ctx, summary.read_text(encoding="utf-8"), "\n".join(log))


def _questions(r: Run) -> list:
    return [body for _, body, act in r.comments if body.startswith(ASK_RECEIPT)]


_RECEIPT_FIRST = re.compile(
    rf"^{re.escape(medic_next_step.RECEIPT_MARK)} class=(\S+) step=(\S+) "
    rf"run={RUN_ID}( moved=no)?$")


# ── 1. the step, as written ────────────────────────────────────────────────
class TheAskStepTest(unittest.TestCase):
    def setUp(self):
        self.step = _step("ask")
        self.run_ = _flat(self.step["run"])

    def test_its_name_id_and_place(self):
        self.assertEqual(STEP_NAME, self.step["name"])
        self.assertLess(_index("planning"), _index("ask"))
        self.assertEqual(_index("ask") + 1, _index("receipt"))

    def test_its_gate(self):
        self.assertEqual(
            "always() && steps.target.outputs.kind == 'card' && "
            "steps.verdict.outputs.step == 'ask-person' && "
            "steps.waiting.outputs.waiting != 'true'", _flat(self.step["if"]))
        cond = self.step["if"]
        base = {"steps.target.outputs.kind": "card",
                "steps.verdict.outputs.step": "ask-person",
                "steps.waiting.outputs.waiting": "false"}
        self.assertTrue(_runs(cond, base))
        for step in ("requeue-once", "send-to-planning", "fix-path", "", None):
            self.assertFalse(_runs(cond, {**base, "steps.verdict.outputs.step": step}))
        for kind in ("repair-card", "notice", "", None):
            self.assertFalse(_runs(cond, {**base, "steps.target.outputs.kind": kind}))
        self.assertFalse(_runs(cond, {**base, "steps.waiting.outputs.waiting": "true"}))

    def test_the_three_lines_come_from_the_question_reader_alone(self):
        self.assertIn(QUESTION_CALL, self.run_)
        self.assertNotIn("console_escalation", self.step["run"])

    def test_the_receipt_line_is_echoed_first_into_the_comment_file(self):
        self.assertIn(f'echo "{ASK_RECEIPT}, and asks:"', self.run_)
        self.assertIn('> "$RUNNER_TEMP/medic-ask-comment.md"', self.run_)
        receipt_at = self.step["run"].index(ASK_RECEIPT)
        self.assertLess(receipt_at, self.step["run"].index(QUESTION_CALL.split(" --")[0]))

    def test_the_question_posts_before_the_move_with_no_act(self):
        post = ('linear_ops.py comment "$TARGET" '
                '"$(cat "$RUNNER_TEMP/medic-ask-comment.md")"')
        self.assertIn(post, self.run_)
        line = next(ln for ln in self.step["run"].splitlines() if "linear_ops.py comment" in ln)
        self.assertNotIn("--act", line)
        self.assertLess(self.run_.index(post), self.run_.index(HANDLED_BY["ask-person"]))

    def test_the_from_lanes_come_from_the_one_command(self):
        self.assertIn("FROM_LANES=$(python3 .bureau-pipeline/scripts/medic_next_step.py "
                      "from-lanes)", self.run_)
        self.assertIn(HANDLED_BY["ask-person"], self.run_)
        for lane in ("In Review", "Queued", "In Progress"):
            self.assertNotIn(lane, self.step["run"])

    def test_its_one_output_is_moved(self):
        written = re.findall(r'"([a-z_]+)=[^"]*"\s*>>\s*"\$GITHUB_OUTPUT"', self.step["run"])
        self.assertEqual(["moved"], sorted(set(written)))

    def test_no_run_text_on_a_shell_line(self):
        self.assertNotIn("${{", self.step["run"])


# ── 2. the park ────────────────────────────────────────────────────────────
class TheParkTest(unittest.TestCase):
    def test_question_then_move_then_receipt(self):
        r = _run_job()
        self.assertEqual("ask-person", r.ctx["steps.verdict.outputs.step"])
        order = [c[1] for c in r.called("linear_ops.py") if c[1] in ("comment", "advance")]
        # The diagnosis (as before), the question, the move, the receipt.
        self.assertEqual(["comment", "comment", "advance", "comment"], order)
        acts = [act for _, _, act in r.comments]
        self.assertEqual(["run-failure-diagnosed", None, "medic-next-step"], acts)
        (question,) = _questions(r)
        self.assertEqual(r.comments[1][1], question)
        self.assertEqual(f"{ASK_RECEIPT}, and asks:", question.split("\n", 1)[0])
        self.assertEqual([], console_escalation.problems(question))
        esc = console_escalation.parse(question)
        self.assertEqual(PLAIN_QUESTION["finding"], esc.finding)
        self.assertEqual(PLAIN_QUESTION["question"], esc.question)
        self.assertEqual(PLAIN_QUESTION["recommendation"], esc.recommendation)
        self.assertIn(RUN_URL, question)

    def test_the_move_is_to_green_light_from_the_medic_s_lanes(self):
        r = _run_job()
        lanes = ",".join(medic_next_step.from_lanes())
        self.assertEqual([["linear_ops.py", "advance", CARD, "Green Light", lanes]],
                         r.called("linear_ops.py", "advance"))
        self.assertEqual(1, len(r.called("medic_next_step.py", "from-lanes")))
        self.assertEqual("true", r.ctx["steps.ask.outputs.moved"])

    def test_one_plain_receipt_names_ask_person(self):
        r = _run_job()
        (receipt,) = r.receipts
        self.assertEqual(("platform-fault", "ask-person", None),
                         _RECEIPT_FIRST.match(receipt.split("\n", 1)[0]).groups())
        self.assertIn("moved the card to Green Light", receipt)
        self.assertEqual([], r.called("linear_ops.py", "state-of"))

    def test_a_refused_move_carries_moved_no_and_the_lane_the_receipt_read(self):
        for advance in ("refuse", "fail"):
            with self.subTest(advance=advance):
                r = _run_job(advance=advance, state="In Review")
                self.assertEqual("false", r.ctx["steps.ask.outputs.moved"])
                (receipt,) = r.receipts
                self.assertTrue(receipt.split("\n", 1)[0].endswith(" moved=no"))
                self.assertIn("left in In Review", receipt)
                self.assertEqual([["linear_ops.py", "state-of", CARD]],
                                 r.called("linear_ops.py", "state-of"))
                self.assertEqual(1, len(_questions(r)))

    def test_a_question_that_was_not_posted_moves_nothing(self):
        r = _run_job(comment_rc=1)
        self.assertEqual([], r.called("linear_ops.py", "advance"))
        self.assertEqual("false", r.ctx["steps.ask.outputs.moved"])
        self.assertRegex(r.log, r"::warning[^\n]*question")

    def test_a_question_that_could_not_be_rendered_posts_nothing_and_moves_nothing(self):
        # A header with no three lines is a Green Light row with no question.
        r = _run_job(question_rc=1)
        self.assertEqual(1, len(r.called("medic_next_step.py", "question")))
        self.assertEqual([], _questions(r))
        self.assertEqual([], r.called("linear_ops.py", "advance"))
        self.assertEqual("false", r.ctx["steps.ask.outputs.moved"])
        self.assertRegex(r.log, r"::warning[^\n]*question")
        (receipt,) = r.receipts
        self.assertTrue(receipt.split("\n", 1)[0].endswith(" moved=no"))
        self.assertEqual(["run-failure-diagnosed", "medic-next-step"],
                         [act for _, _, act in r.comments])

    def test_a_waiting_card_gets_none_of_the_three(self):
        r = _run_job(thread=["a person speaks", _marker()])
        self.assertEqual("true", r.ctx["steps.waiting.outputs.waiting"])
        self.assertFalse(r.ran["ask"])
        self.assertFalse(r.ran["receipt"])
        self.assertEqual([], r.comments)
        self.assertEqual([], r.called("linear_ops.py", "advance"))
        self.assertEqual([], r.called("linear_ops.py", "state-of"))


# ── 3. the question file, missing or malformed ─────────────────────────────
class TheFallbackTest(unittest.TestCase):
    MALFORMED = (None, "{not json", "[]", json.dumps({"finding": "only this"}),
                 json.dumps({k: "  " for k in medic_next_step.QUESTION_FIELDS}))

    def test_a_missing_or_malformed_file_still_parks_with_the_fallback_lines(self):
        for question in self.MALFORMED:
            with self.subTest(question=question):
                r = _run_job(question=question)
                (body,) = _questions(r)
                esc = console_escalation.parse(body)
                if question and "only this" in question:
                    self.assertEqual("only this", esc.finding)
                else:
                    self.assertEqual(REPORT_FINDING, esc.finding)
                self.assertEqual(medic_next_step.FALLBACK_QUESTION, esc.question)
                self.assertIsNone(esc.recommendation)
                self.assertEqual(
                    f"{console_escalation.RECOMMENDATION_PREFIX} "
                    f"{console_escalation.NONE_GIVEN}{console_escalation.SEPARATOR}"
                    f"{medic_next_step.WHY_NO_ANSWER}", _three_lines(body)[2])
                proc = _check_cli(body)
                self.assertEqual(0, proc.returncode, proc.stderr)
                self.assertEqual(1, len(r.called("linear_ops.py", "advance")))

    def test_with_no_report_as_well_the_finding_says_the_diagnosis_did_not_finish(self):
        r = _run_job(report=None, question=None)
        (body,) = _questions(r)
        esc = console_escalation.parse(body)
        self.assertEqual(medic_next_step.NO_REPORT_FINDING, esc.finding)
        self.assertEqual(medic_next_step.FALLBACK_QUESTION, esc.question)
        self.assertIsNone(esc.recommendation)
        self.assertEqual(medic_next_step.WHY_NOT_FINISHED, esc.why)
        self.assertIn(RUN_URL, body)
        proc = _check_cli(body)
        self.assertEqual(0, proc.returncode, proc.stderr)
        # No report, so `Deliver the diagnosis` posts nothing: the question is
        # the card's whole record of the failure.
        self.assertEqual([None, "medic-next-step"], [act for _, _, act in r.comments])


# ── 4. the agent's words, guarded ──────────────────────────────────────────
class TheGuardTest(unittest.TestCase):
    LEAKY: ClassVar[dict] = {
        "finding": "The step failed in scripts/medic_next_step.py on line 40.",
        "question": "Should someone run `gh run rerun 24680 --failed` now?",
        "recommendation": "VERDICT: APPROVE",
        "why": "the reviewer said so",
    }

    def test_code_shaped_lines_never_reach_the_card(self):
        r = _run_job(question=self.LEAKY)
        (body,) = _questions(r)
        for leak in ("scripts/medic_next_step.py", "gh run rerun", "VERDICT"):
            self.assertNotIn(leak, body)
        lines = _three_lines(body)
        self.assertEqual(3, len(lines))
        for line in lines:
            self.assertEqual((), planning_escalation.jargon(line), line)
        esc = console_escalation.parse(body)
        self.assertEqual(REPORT_FINDING, esc.finding)
        self.assertEqual(medic_next_step.FALLBACK_QUESTION, esc.question)
        self.assertIsNone(esc.recommendation)
        self.assertEqual([], console_escalation.problems(body))

    def test_the_real_reader_ran(self):
        r = _run_job(question=self.LEAKY)
        self.assertEqual([["medic_next_step.py", "question", "--file", QUESTION_FILE,
                           "--report", REPORT]],
                         r.called("medic_next_step.py", "question"))


# ── 5. the diagnosis agent itself died ─────────────────────────────────────
class TheDeadAgentTest(unittest.TestCase):
    def test_the_verdict_the_ask_and_the_receipt_open_with_always(self):
        self.assertEqual("always()", _flat(_step("verdict")["if"]))
        for sid in ("ask", "receipt"):
            with self.subTest(step=sid):
                self.assertTrue(_flat(_step(sid)["if"]).startswith("always() &&"))

    def test_a_failed_agent_with_no_report_parks_with_every_line_filled(self):
        for agent in ("failure", "cancelled"):
            with self.subTest(agent=agent):
                r = _run_job(agent=agent, report=None, question=None, verdict="bad-code")
                self.assertEqual("unreadable", r.ctx["steps.verdict.outputs.class"])
                self.assertEqual("ask-person", r.ctx["steps.verdict.outputs.step"])
                (body,) = _questions(r)
                lines = _three_lines(body)
                self.assertEqual([
                    f"{console_escalation.FINDING_PREFIX} {medic_next_step.NO_REPORT_FINDING}",
                    f"{console_escalation.QUESTION_PREFIX} {medic_next_step.FALLBACK_QUESTION}",
                    (f"{console_escalation.RECOMMENDATION_PREFIX} "
                     f"{console_escalation.NONE_GIVEN}{console_escalation.SEPARATOR}"
                     f"{medic_next_step.WHY_NOT_FINISHED}"),
                ], lines)
                proc = _check_cli(body)
                self.assertEqual(0, proc.returncode, proc.stderr)
                self.assertEqual(1, len(r.called("linear_ops.py", "advance")))
                (receipt,) = r.receipts
                self.assertEqual(("unreadable", "ask-person", None),
                                 _RECEIPT_FIRST.match(receipt.split("\n", 1)[0]).groups())
                self.assertIn(f"Finding: {medic_next_step.NO_REPORT_FINDING}", receipt)


# ── 6. no path leaves only a comment ───────────────────────────────────────
class NoPathLeavesOnlyACommentTest(unittest.TestCase):
    def _handlers(self, step: str) -> list:
        gate = f"steps.verdict.outputs.step == '{step}'"
        return [s for s in _steps() if gate in _flat(s.get("if") or "")]

    def test_every_step_decide_reaches_moves_the_card_or_names_its_owner(self):
        reached = set()
        for cls in medic_next_step.CLASSES:
            for resume_spent, has_open_pr, planning_spent in itertools.product(
                    (False, True), repeat=3):
                with self.subTest(cls=cls, resume_spent=resume_spent,
                                  has_open_pr=has_open_pr, planning_spent=planning_spent):
                    step = medic_next_step.decide(
                        cls, kind="card", resume_spent=resume_spent,
                        has_open_pr=has_open_pr, planning_spent=planning_spent).step
                    reached.add(step)
                    self.assertIn(step, HANDLED_BY,
                                  f"{step} is a step no medic.yml step handles")
                    handlers = self._handlers(step)
                    self.assertTrue(any(HANDLED_BY[step] in _flat(h.get("run") or "")
                                        for h in handlers),
                                    f"no medic.yml step gated on {step} does "
                                    f"{HANDLED_BY[step]!r}")
                    self.assertIn("receipt", [h.get("id") for h in handlers], step)
        self.assertEqual(set(medic_next_step.STEPS), reached)

    def test_every_declared_step_has_a_handler(self):
        for step in medic_next_step.STEPS:
            with self.subTest(step=step):
                self.assertIn(step, HANDLED_BY)

    def test_each_step_run_end_to_end_moves_the_card_or_names_the_pull_request(self):
        for verdict, step in STEP_OF.items():
            with self.subTest(verdict=verdict):
                r = _run_job(verdict=verdict)
                self.assertEqual(step, r.ctx["steps.verdict.outputs.step"])
                moved = r.called("linear_ops.py", "advance") or r.called("gh", "run")
                if step == "fix-path":
                    self.assertFalse(moved)
                    self.assertIn(PR_URL, r.receipts[0])
                else:
                    self.assertTrue(moved, step)
                self.assertEqual(1, len(r.receipts))

    def test_the_one_path_that_leaves_the_card_is_the_wait(self):
        for verdict in STEP_OF:
            with self.subTest(verdict=verdict):
                r = _run_job(verdict=verdict, thread=[_marker()])
                self.assertEqual([], r.comments)
                self.assertEqual([], r.called("linear_ops.py", "advance"))
                self.assertEqual([], r.called("gh", "run"))
                self.assertTrue(r.ran["held"])


# ── 7. the receipt step reads the park ─────────────────────────────────────
class TheReceiptReadsTheParkTest(unittest.TestCase):
    def test_the_gate_carries_ask_person(self):
        cond = _step("receipt")["if"]
        self.assertIn("steps.verdict.outputs.step == 'ask-person'", _flat(cond))
        self.assertTrue(_runs(cond, {"steps.target.outputs.kind": "card",
                                     "steps.verdict.outputs.step": "ask-person"}))
        self.assertFalse(_runs(cond, {"steps.target.outputs.kind": "card",
                                      "steps.verdict.outputs.step": "ask-person",
                                      "steps.waiting.outputs.waiting": "true"}))

    def test_it_reads_the_ask_step_s_moved(self):
        env = _step("receipt")["env"]
        self.assertIn("${{ steps.ask.outputs.moved }}", env.values())

    def test_the_act_is_written_once(self):
        self.assertEqual(1, _text().count("--act=medic-next-step"))


# ── 8. the question is declared not an act ─────────────────────────────────
class TheActRegistryTest(unittest.TestCase):
    ENTRY: ClassVar[dict] = {"file": ".github/workflows/medic.yml", "step": STEP_NAME,
             "anchor": "medic-ask-comment.md", "kind": "not-an-act"}

    def setUp(self):
        with open(ACTS, encoding="utf-8") as f:
            self.doc = json.load(f)
        found = [d for d in self.doc["unconverted"]
                 if d.get("anchor") == self.ENTRY["anchor"]]
        self.assertEqual(1, len(found), found)
        self.entry = found[0]

    def test_the_entry(self):
        self.assertEqual(self.ENTRY, {k: self.entry[k] for k in self.ENTRY})
        self.assertIn("🙋", self.entry["means"])
        self.assertIn("console_escalation", self.entry["means"])
        self.assertIn("Green Light", self.entry["means"])
        why = self.entry["why"]
        for needle in ("A QUESTION, not an act", "medic-next-step", "step=ask-person",
                       "green_light_rows.py"):
            self.assertIn(needle, why)

    def test_it_sits_beside_the_proof_run_s_question(self):
        anchors = [d.get("anchor") for d in self.doc["unconverted"]]
        self.assertEqual(anchors.index("proof-escalation-comment.md") + 1,
                         anchors.index(self.ENTRY["anchor"]))

    def test_it_matches_exactly_the_ask_step_s_comment(self):
        hits = [s for s in check_act_receipts.sites()
                if check_act_receipts._matches(self.entry, s)]
        self.assertEqual(1, len(hits), hits)
        self.assertEqual(".github/workflows/medic.yml", hits[0].path)
        self.assertEqual(STEP_NAME, hits[0].step)
        self.assertIsNone(hits[0].composed_as)

    def test_without_the_entry_the_medic_site_is_named(self):
        doc = copy.deepcopy(self.doc)
        doc["unconverted"] = [d for d in doc["unconverted"]
                              if d.get("anchor") != self.ENTRY["anchor"]]
        found = check_act_receipts.problems(doc)
        named = [p for p in found if p.startswith(".github/workflows/medic.yml:")
                 and "not composed through pipeline_act.receipt()" in p]
        self.assertEqual(1, len(named), found)

    def test_the_registry_checks_pass(self):
        for script in (["pipeline_act.py", "check"], ["check_act_receipts.py"]):
            with self.subTest(script=script):
                proc = subprocess.run(
                    [sys.executable, os.path.join(ROOT, "scripts", script[0]), *script[1:]],
                    cwd=ROOT, capture_output=True, text=True, check=False, timeout=300)
                self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)


# ── 9. the lane contract declares the writers ──────────────────────────────
class TheLaneContractTest(unittest.TestCase):
    def setUp(self):
        import lane_contract

        self.lane_contract = lane_contract
        self.green = lane_contract.lane("Green Light")["clauses"]
        self.planning = lane_contract.lane("Planning")["clauses"]

    def test_planning_names_the_medic_s_bad_card_move(self):
        text = self.planning["writers"]["text"]
        self.assertIn("the medic sending back a card it diagnosed as `bad-card`", text)
        self.assertIn("DRE-7799", text)
        self.assertIn("linear_ops.py", self.planning["writers"]["who"])

    def test_green_light_declares_the_medic_as_a_writer(self):
        self.assertIn("medic.yml", self.green["writers"]["who"])
        self.assertIn("the medic asking a person about a diagnosis only a person can "
                      "settle (DRE-7794)", self.green["writers"]["text"])
        glossary = self.lane_contract.load()["writers"]["medic.yml"]
        self.assertEqual(".github/workflows/medic.yml", glossary["path"])

    def test_clause_d_names_the_medic_s_question(self):
        text = self.green["entrance"]["text"]
        self.assertIn("The medic's diagnosis that only a person can settle is the same "
                      "kind (DRE-7794)", text)

    def test_the_arrival(self):
        records = [r for r in self.green["entrance"]["arrivals"]
                   if r["where"] == f"medic.yml#{STEP_NAME}"]
        self.assertEqual([{
            "kind": "agent-escalation", "writer": "medic.yml",
            "where": f"medic.yml#{STEP_NAME}",
            "evidence": "the 🙋 medic question carrying the diagnosis's three lines, "
                        "posted before the move",
            "card": "DRE-7794"}], records)

    def test_the_doc_matches_its_render(self):
        rendered = self.lane_contract.render_markdown()
        with open(os.path.join(ROOT, "docs", "lane-contract.md"), encoding="utf-8") as f:
            self.assertEqual(rendered, f.read())

    def test_the_lane_checks_pass(self):
        # Offline, as their own suites read them: the board rules of
        # `lane_contract.py check` and Linear's team default in
        # `ready_lane_writers.py check` need a live board, and this proves the
        # contract and the source. Both CLIs pass with a Linear key.
        import green_light_rows
        import ready_lane_writers

        report = self.lane_contract.check(
            vocabulary=self.lane_contract.pipeline_vocabulary())
        self.assertEqual([], [f.clause_id for f in report.failures()
                              if not f.clause_id.startswith("board.")])
        self.assertEqual([], green_light_rows.problems())
        self.assertEqual([], ready_lane_writers.writer_problems())


# ── 10. the agent is asked for the question ────────────────────────────────
class ThePromptTest(unittest.TestCase):
    def setUp(self):
        step = next(s for s in _steps() if s.get("id") == "claude")
        self.prompt = _flat(step["with"]["prompt"])

    def test_it_names_the_file_and_its_four_fields(self):
        self.assertIn(".bureau-pipeline/medic-question.json", self.prompt)
        for field in medic_next_step.QUESTION_FIELDS:
            self.assertIn(f"`{field}`", self.prompt)

    def test_it_asks_for_plain_words_and_who_settles_it(self):
        for needle in ("no code", "no file paths", "no commands", "platform-fault",
                       "the operator who fixes the pipeline",
                       "whoever decides the card's next step"):
            self.assertIn(needle, self.prompt)


if __name__ == "__main__":
    unittest.main()
