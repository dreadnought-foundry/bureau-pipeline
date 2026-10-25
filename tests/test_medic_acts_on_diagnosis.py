"""The medic acts on its diagnosis, and writes the move on the card once,
beside the diagnosis (DRE-7799, the second half of the medic's next steps).

DRE-7793 made the diagnosis name one of the five classes and gated DRE-7526's
one re-run on the step the class owes. This card adds the moves that did not
exist and one receipt for all three, in the `diagnose` job of medic.yml:

  * `Is the card waiting?` (id `waiting`) reads a `card` target's thread right
    after `target`. A card whose newest `🪦 limit-death:` marker still stands
    is the wait's: the medic posts nothing on it, moves nothing and starts no
    re-run, and leaves the report in the step summary. Every comment it could
    write would supersede that marker (`limit_recovery.is_receipt`), and the
    card would never be brought back.
  * `requeue-once` now also delivers the diagnosis on the card when the
    re-run starts, through the one `Deliver the diagnosis` step, whose `if:`
    is widened rather than copied.
  * `send-to-planning` moves the card with `linear_ops.py advance "$TARGET"
    "Planning" "$FROM_LANES"`, the from-lanes read off
    `medic_next_step.py from-lanes` and nowhere else.
  * `fix-path` moves nothing; its receipt names the open pull request and the
    fix loop's two existing routes.
  * `Write the medic's receipt` (id `receipt`) posts `medic_next_step.receipt`
    once, under the `medic-next-step` act. A ` moved=no` receipt names the lane
    one `linear_ops.py state-of` read found, or `unknown`.

The steps are run here as the job runs them: in file order, each `if:`
evaluated against the outputs the steps before it wrote, with every Linear,
GitHub and card-PR call a stub that logs what it was asked.
"""

import ast
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import dead_run
import limit_recovery
import medic_next_step
import medic_resume
import pipeline_act

MEDIC = os.path.join(ROOT, ".github", "workflows", "medic.yml")
AGENT_FIX = os.path.join(ROOT, ".github", "workflows", "agent-fix.yml")
RECONCILE = os.path.join(ROOT, "scripts", "reconcile.py")
ACTS = os.path.join(ROOT, "config", "pipeline-acts.json")
REPORT = ".bureau-pipeline/medic-report.md"
CARD = "DRE-4242"
REPAIR_CARD = "DRE-5151"
RUN_ID = "24680"
RUN_URL = f"https://github.com/dreadnought-foundry/atlas/actions/runs/{RUN_ID}"
PR_URL = "https://github.com/dreadnought-foundry/atlas/pull/77"
REPORT_TEXT = ("The card asks for two lanes that contradict each other. "
               "No build can satisfy both.\n\n"
               "Technical: criteria 2 and 4 name different destinations.\n")
DIAGNOSIS_ANCHOR = ('linear_ops.py comment "$TARGET" "$(cat "$REPORT")" '
                    '--act=run-failure-diagnosed')
RECEIPT_ANCHOR = 'linear_ops.py comment "$TARGET" "$RECEIPT" --act=medic-next-step'
THREE_STEPS = ("requeue-once", "send-to-planning", "fix-path")
CLASS_OF = {"requeue-once": "transient-infrastructure",
            "send-to-planning": "bad-card",
            "fix-path": "bad-code",
            "ask-person": "platform-fault"}
#: The steps this card adds or gates, in the order the job runs them.
SIMULATED = ("waiting", "resume", "held", "deliver", "planning", "ask", "receipt")


def _text() -> str:
    with open(MEDIC, encoding="utf-8") as f:
        return f.read()


def _steps() -> list:
    return yaml.safe_load(_text())["jobs"]["diagnose"]["steps"]


def _step(step_id: str) -> dict:
    return next(s for s in _steps() if s.get("id") == step_id)


def _index(step_id: str) -> int:
    return next(i for i, s in enumerate(_steps()) if s.get("id") == step_id)


def _flat(text: str) -> str:
    return " ".join(str(text).split())


def _marker(run_id: str = "13579") -> str:
    return dead_run.limit_marker("claude", "build",
                                 datetime(2026, 10, 23, 5, tzinfo=UTC), run_id)


def _recovery_after(marker: str) -> str:
    return limit_recovery.recovery_receipt(
        dead_run.parse_limit_marker(marker), "window reset at 10:00 PT",
        "Re-dispatched the build")


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


# ── the stubs ───────────────────────────────────────────────────────────────
# Every script the steps call is a stub that logs its argv as one JSON line.
# `medic_next_step.py` and `medic_resume.py` then run the real script.
STUB_HEAD = """#!/usr/bin/env python3
import json, os, subprocess, sys
with open(os.environ["CALLS"], "a", encoding="utf-8") as f:
    f.write(json.dumps([os.path.basename(__file__), *sys.argv[1:]]) + "\\n")
cmd = sys.argv[1] if len(sys.argv) > 1 else ""
"""

STUB_LINEAR_OPS = STUB_HEAD + """
if cmd == "dump-comments":
    if os.environ.get("FAKE_DUMP_RC", "0") != "0":
        sys.exit(int(os.environ["FAKE_DUMP_RC"]))
    sys.stdout.write(os.environ.get("FAKE_THREAD", "[]"))
elif cmd == "comment":
    sys.exit(int(os.environ.get("FAKE_COMMENT_RC", "0")))
elif cmd == "advance":
    mode = os.environ.get("FAKE_ADVANCE", "land")
    if mode == "land":
        print(f"{sys.argv[2]} → {sys.argv[3]}")
    elif mode == "refuse":
        print(f"{sys.argv[2]} is in 'Green Light', not in {sys.argv[4]!r} — not advancing")
    else:
        sys.exit(1)
elif cmd == "state-of":
    if os.environ.get("FAKE_STATE_RC", "0") != "0":
        sys.exit(int(os.environ["FAKE_STATE_RC"]))
    print(os.environ.get("FAKE_STATE", "Green Light"))
else:
    sys.exit(2)
"""

STUB_CARD_PR = STUB_HEAD + """
out = os.environ.get("FAKE_FIND_OUT", "")
if out:
    sys.stdout.write(out + "\\n")
sys.exit(int(os.environ.get("FAKE_FIND_RC", "0")))
"""

STUB_MEDIC_RETRY = STUB_HEAD + """
if cmd == "repair-card":
    print(os.environ.get("FAKE_REPAIR", ""))
elif cmd == "failure-card-body":
    print("the report, with criteria")
elif cmd == "notice-workflow-file":
    print("agent-task")
"""

STUB_QUIET = STUB_HEAD

STUB_REAL = STUB_HEAD + """
real = os.path.join(os.environ["REAL_SCRIPTS"], os.path.basename(__file__))
sys.exit(subprocess.call([sys.executable, real, *sys.argv[1:]]))
"""

STUB_GH = STUB_HEAD.replace("os.path.basename(__file__)", '"gh"') + """
sys.exit(int(os.environ.get("FAKE_GH_RC", "0")))
"""

STUBS = {"linear_ops.py": STUB_LINEAR_OPS, "card_pr.py": STUB_CARD_PR,
         "medic_retry.py": STUB_MEDIC_RETRY, "pipeline_notice.py": STUB_QUIET,
         "medic_next_step.py": STUB_REAL, "medic_resume.py": STUB_REAL}


class Run:
    """What one simulated diagnose job did."""

    def __init__(self, calls, ran, ctx, summary, log):
        self.calls, self.ran, self.ctx = calls, ran, ctx
        self.summary, self.log = summary, log

    def called(self, script, cmd=None):
        return [c for c in self.calls
                if c[0] == script and (cmd is None or c[1:2] == [cmd])]

    @property
    def comments(self):
        """(target, body, act) for every comment the job posted."""
        out = []
        for c in self.called("linear_ops.py", "comment"):
            act = next((a.split("=", 1)[1] for a in c[4:] if a.startswith("--act=")), None)
            out.append((c[2], c[3], act))
        return out

    @property
    def receipts(self):
        return [body for _, body, act in self.comments if act == "medic-next-step"]


def _run_job(*, step, kind="card", cls=None, thread=None, dump_rc=0,
             gh_rc=0, advance="land", state="Green Light", state_rc=0,
             find_out=f"OPEN\t{PR_URL}", find_rc=0, comment_rc=0,
             report=REPORT_TEXT, repair="", attempt=2) -> Run:
    target = {"card": CARD, "repair-card": REPAIR_CARD}.get(kind, "")
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
        "github.event.workflow_run.head_sha": "abc123",
        "github.event.repository.default_branch": "main",
        "vars.BUREAU_READ_URL": "",
        "needs.classify.outputs.retry": "true",
        "steps.repo.outputs.slug": "atlas",
        "steps.target.outputs.kind": kind,
        "steps.target.outputs.target": target,
        "steps.target.outputs.title": "",
        "steps.verdict.outputs.class": cls or CLASS_OF.get(step, "bad-code"),
        "steps.verdict.outputs.step": step if kind == "card" else "",
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
        for name, body in STUBS.items():
            (scripts / name).write_text(body, encoding="utf-8")
            os.chmod(scripts / name, 0o755)
        (bin_ / "gh").write_text(STUB_GH, encoding="utf-8")
        os.chmod(bin_ / "gh", 0o755)
        if report is not None:
            (td / REPORT).write_text(report, encoding="utf-8")
        calls, summary = td / "calls.log", td / "summary.md"
        calls.touch()
        summary.touch()
        fake = {"FAKE_THREAD": json.dumps(thread or []), "FAKE_DUMP_RC": str(dump_rc),
                "FAKE_GH_RC": str(gh_rc), "FAKE_ADVANCE": advance,
                "FAKE_STATE": state, "FAKE_STATE_RC": str(state_rc),
                "FAKE_FIND_OUT": find_out, "FAKE_FIND_RC": str(find_rc),
                "FAKE_COMMENT_RC": str(comment_rc), "FAKE_REPAIR": repair}
        for sid in SIMULATED:
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
            log.append(proc.stdout)
            for line in output.read_text(encoding="utf-8").splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    ctx[f"steps.{sid}.outputs.{k}"] = v
        parsed = [json.loads(ln) for ln in calls.read_text(encoding="utf-8").splitlines() if ln]
        return Run(parsed, ran, ctx, summary.read_text(encoding="utf-8"), "\n".join(log))


_RECEIPT_FIRST = re.compile(
    rf"^{re.escape(medic_next_step.RECEIPT_MARK)} class=(\S+) step=(\S+) "
    rf"run={RUN_ID}( moved=no)?$")


# ── 1. the waiting step ─────────────────────────────────────────────────────
class TheWaitingStepTest(unittest.TestCase):
    def test_it_runs_right_after_target_only_for_a_card(self):
        step = _step("waiting")
        self.assertEqual("Is the card waiting?", step["name"])
        self.assertEqual(_index("target") + 1, _index("waiting"))
        self.assertEqual("steps.target.outputs.kind == 'card'", _flat(step["if"]))

    def test_it_reads_the_thread_through_the_waiting_reader(self):
        run_ = _flat(_step("waiting")["run"])
        self.assertIn('linear_ops.py" dump-comments "$TARGET"', run_)
        self.assertIn('medic_next_step.py" waiting --comments-file', run_)
        self.assertNotIn("${{", _step("waiting")["run"])

    def test_a_standing_marker_reads_true(self):
        r = _run_job(step="fix-path", thread=["hello", _marker()])
        self.assertEqual("true", r.ctx["steps.waiting.outputs.waiting"])

    def test_a_recovery_after_the_marker_reads_false(self):
        marker = _marker()
        r = _run_job(step="fix-path", thread=[marker, _recovery_after(marker)])
        self.assertEqual("false", r.ctx["steps.waiting.outputs.waiting"])

    def test_a_read_that_fails_reads_false(self):
        r = _run_job(step="fix-path", thread=[_marker()], dump_rc=1)
        self.assertEqual("false", r.ctx["steps.waiting.outputs.waiting"])
        self.assertEqual(2, len(r.comments))

    def test_it_does_not_run_for_another_kind(self):
        for kind in ("repair-card", "notice"):
            with self.subTest(kind=kind):
                r = _run_job(step="", kind=kind, thread=[_marker()], repair=REPAIR_CARD)
                self.assertFalse(r.ran["waiting"])
                self.assertFalse(r.called("linear_ops.py", "dump-comments"))


# ── 2. a waiting card is the wait's ─────────────────────────────────────────
class TheWaitingCardTest(unittest.TestCase):
    def test_nothing_is_posted_moved_or_rerun(self):
        for step in (*THREE_STEPS, "ask-person"):
            with self.subTest(step=step):
                r = _run_job(step=step, thread=["a person speaks", _marker()])
                self.assertEqual([], r.comments)
                self.assertEqual([], r.called("linear_ops.py", "advance"))
                self.assertEqual([], r.called("linear_ops.py", "state-of"))
                self.assertEqual([], r.called("gh"))
                self.assertEqual([], r.called("pipeline_notice.py"))
                self.assertFalse(r.ran["resume"])
                self.assertFalse(r.ran["deliver"])
                self.assertFalse(r.ran["planning"])
                self.assertFalse(r.ran["ask"])
                self.assertFalse(r.ran["receipt"])

    def test_the_report_goes_to_the_step_summary_under_a_notice(self):
        r = _run_job(step="send-to-planning", thread=[_marker()])
        self.assertTrue(r.ran["held"])
        self.assertIn("No build can satisfy both.", r.summary)
        self.assertIn(CARD, r.summary)
        self.assertNotIn("Resumed:", r.summary)
        self.assertRegex(r.log, rf"::notice[^\n]*{CARD}")
        self.assertEqual(1, len(r.called("medic_resume.py", "summary")))

    def test_a_recovery_after_the_marker_acts_as_normal(self):
        marker = _marker()
        r = _run_job(step="send-to-planning", thread=[marker, _recovery_after(marker)])
        self.assertFalse(r.ran["held"])
        self.assertEqual(["run-failure-diagnosed", "medic-next-step"],
                         [act for _, _, act in r.comments])
        self.assertEqual(1, len(r.called("linear_ops.py", "advance")))

    def test_the_medics_would_be_comments_would_end_the_wait(self):
        # Why the gate exists: what the medic posts on a card that is not
        # waiting would supersede a standing marker, and limit recovery would
        # never bring the card back.
        marker = _marker()
        self.assertIsNotNone(limit_recovery.waiting([marker]))
        for step in THREE_STEPS:
            with self.subTest(step=step):
                r = _run_job(step=step)
                posted = [pipeline_act.receipt(act, body) for _, body, act in r.comments]
                self.assertEqual(2, len(posted))
                self.assertIsNone(limit_recovery.waiting([marker, *posted]))
                for one in posted:
                    self.assertIsNone(limit_recovery.waiting([marker, one]))

    def test_the_gate_reads_the_waiting_output(self):
        for sid in ("resume", "deliver", "planning", "ask", "receipt"):
            with self.subTest(step=sid):
                self.assertIn("steps.waiting.outputs.waiting != 'true'",
                              _flat(_step(sid)["if"]))
        self.assertIn("steps.waiting.outputs.waiting == 'true'", _flat(_step("held")["if"]))


# ── 3. requeue-once ─────────────────────────────────────────────────────────
class TheRequeueOnceTest(unittest.TestCase):
    def test_a_started_rerun_delivers_then_writes_a_plain_receipt(self):
        r = _run_job(step="requeue-once")
        self.assertEqual([["gh", "run", "rerun", RUN_ID, "--failed",
                           "--repo", "dreadnought-foundry/atlas"]], r.called("gh"))
        self.assertEqual("true", r.ctx["steps.resume.outputs.resumed"])
        (t1, diagnosis, a1), (t2, receipt, a2) = r.comments
        self.assertEqual((CARD, REPORT_TEXT.rstrip("\n"), "run-failure-diagnosed"),
                         (t1, diagnosis, a1))
        self.assertEqual((CARD, "medic-next-step"), (t2, a2))
        first = receipt.split("\n", 1)[0]
        self.assertEqual(("transient-infrastructure", "requeue-once", None),
                         _RECEIPT_FIRST.match(first).groups())
        self.assertIn("Resumed:", r.summary)
        self.assertEqual([], r.called("linear_ops.py", "advance"))
        self.assertEqual([], r.called("linear_ops.py", "state-of"))

    def test_a_rerun_that_would_not_start_carries_moved_no(self):
        r = _run_job(step="requeue-once", gh_rc=1, state="In Progress")
        self.assertEqual("false", r.ctx["steps.resume.outputs.resumed"])
        self.assertEqual(["run-failure-diagnosed", "medic-next-step"],
                         [act for _, _, act in r.comments])
        receipt = r.receipts[0]
        self.assertTrue(receipt.split("\n", 1)[0].endswith(" moved=no"))
        self.assertIn("left in In Progress", receipt)
        self.assertEqual([["linear_ops.py", "state-of", CARD]],
                         r.called("linear_ops.py", "state-of"))
        self.assertEqual([], r.called("linear_ops.py", "advance"))

    def test_the_resume_is_gated_on_the_wait(self):
        self.assertEqual(
            "github.event.workflow_run.run_attempt == 2 && "
            "needs.classify.outputs.retry != 'false' && "
            "steps.verdict.outputs.step == 'requeue-once' && "
            "steps.waiting.outputs.waiting != 'true'",
            _flat(_step("resume")["if"]))


# ── 4. the one delivery ─────────────────────────────────────────────────────
class TheDeliveryTest(unittest.TestCase):
    def _cond(self):
        return _step("deliver")["if"]

    def test_a_resumed_card_is_delivered(self):
        self.assertTrue(_runs(self._cond(), {
            "steps.resume.outputs.resumed": "true", "steps.target.outputs.kind": "card"}))

    def test_a_resumed_non_card_still_skips_delivery(self):
        for kind in ("repair-card", "notice", "", None):
            with self.subTest(kind=kind):
                self.assertFalse(_runs(self._cond(), {
                    "steps.resume.outputs.resumed": "true",
                    "steps.target.outputs.kind": kind}))

    def test_a_waiting_card_is_never_delivered(self):
        for resumed in ("true", "false", None):
            with self.subTest(resumed=resumed):
                self.assertFalse(_runs(self._cond(), {
                    "steps.resume.outputs.resumed": resumed,
                    "steps.target.outputs.kind": "card",
                    "steps.waiting.outputs.waiting": "true"}))

    def test_it_still_runs_after_a_failed_agent(self):
        self.assertTrue(_flat(self._cond()).startswith("always() &&"))

    def test_each_anchor_is_in_the_file_exactly_once(self):
        text = _text()
        self.assertEqual(1, text.count(DIAGNOSIS_ANCHOR))
        self.assertEqual(1, text.count(RECEIPT_ANCHOR))
        self.assertIn(DIAGNOSIS_ANCHOR, _step("deliver")["run"])
        self.assertIn(RECEIPT_ANCHOR, _step("receipt")["run"])


# ── 5. send-to-planning ─────────────────────────────────────────────────────
class TheSendToPlanningTest(unittest.TestCase):
    def test_a_landed_move_writes_a_plain_receipt_naming_the_finding(self):
        r = _run_job(step="send-to-planning")
        lanes = ",".join(medic_next_step.from_lanes())
        self.assertEqual([["linear_ops.py", "advance", CARD, "Planning", lanes]],
                         r.called("linear_ops.py", "advance"))
        self.assertEqual("yes", r.ctx["steps.planning.outputs.moved"])
        self.assertEqual(["run-failure-diagnosed", "medic-next-step"],
                         [act for _, _, act in r.comments])
        receipt = r.receipts[0]
        self.assertEqual(("bad-card", "send-to-planning", None),
                         _RECEIPT_FIRST.match(receipt.split("\n", 1)[0]).groups())
        self.assertIn("Finding: The card asks for two lanes that contradict each other.",
                      receipt)
        self.assertEqual([], r.called("linear_ops.py", "state-of"))
        self.assertEqual([], r.called("gh"))

    def test_the_diagnosis_is_delivered_before_the_move(self):
        r = _run_job(step="send-to-planning")
        order = [c[1] for c in r.called("linear_ops.py") if c[1] in ("comment", "advance")]
        self.assertEqual(["comment", "advance", "comment"], order)

    def test_a_refused_move_carries_moved_no_and_names_the_lane(self):
        for advance in ("refuse", "fail"):
            with self.subTest(advance=advance):
                r = _run_job(step="send-to-planning", advance=advance, state="Green Light")
                self.assertEqual("no", r.ctx["steps.planning.outputs.moved"])
                receipt = r.receipts[0]
                self.assertTrue(receipt.split("\n", 1)[0].endswith(" moved=no"))
                self.assertIn("left in Green Light", receipt)
                self.assertEqual([["linear_ops.py", "state-of", CARD]],
                                 r.called("linear_ops.py", "state-of"))

    def test_the_from_lanes_come_from_the_one_command(self):
        run_ = _step("planning")["run"]
        self.assertIn('FROM_LANES=$(python3 .bureau-pipeline/scripts/medic_next_step.py from-lanes)',
                      run_)
        self.assertIn('linear_ops.py advance "$TARGET" "Planning" "$FROM_LANES"', run_)
        self.assertEqual(1, _text().count('advance "$TARGET" "Planning"'))
        # The Green Light move (DRE-7794) calls the same command, and no other.
        assigned = re.findall(r"FROM_LANES=(.*)", _text())
        self.assertEqual(2, len(assigned))
        for value in assigned:
            self.assertEqual("$(python3 .bureau-pipeline/scripts/medic_next_step.py "
                             "from-lanes) || true", value.strip())
        r = _run_job(step="send-to-planning")
        self.assertEqual(1, len(r.called("medic_next_step.py", "from-lanes")))

    def test_the_from_lanes_are_not_an_output(self):
        run_ = _step("planning")["run"]
        written = re.findall(r'"([a-z_]+)=[^"]*"\s*>>\s*"\$GITHUB_OUTPUT"', run_)
        self.assertEqual(["moved"], sorted(set(written)))
        self.assertNotIn("FROM_LANES", _flat(_step("receipt")))
        for lane in ("In Review", "Queued", "In Progress"):
            self.assertNotIn(lane, run_)

    def test_the_move_runs_only_on_send_to_planning_for_a_card(self):
        cond = _step("planning")["if"]
        base = {"steps.target.outputs.kind": "card",
                "steps.verdict.outputs.step": "send-to-planning"}
        self.assertTrue(_runs(cond, base))
        for step in ("requeue-once", "fix-path", "ask-person", ""):
            self.assertFalse(_runs(cond, {**base, "steps.verdict.outputs.step": step}))
        self.assertFalse(_runs(cond, {**base, "steps.target.outputs.kind": "repair-card"}))


# ── 6. the bound ────────────────────────────────────────────────────────────
class TheBoundTest(unittest.TestCase):
    def test_a_landed_receipt_spends_planning(self):
        receipt = _run_job(step="send-to-planning").receipts[0]
        self.assertTrue(medic_next_step.planning_spent(["a comment", receipt]))
        composed = pipeline_act.receipt("medic-next-step", receipt)
        self.assertTrue(medic_next_step.planning_spent([composed]))

    def test_a_refused_receipt_does_not(self):
        receipt = _run_job(step="send-to-planning", advance="refuse").receipts[0]
        self.assertFalse(medic_next_step.planning_spent(["a comment", receipt]))

    def test_the_other_steps_receipts_do_not(self):
        for step in ("requeue-once", "fix-path"):
            with self.subTest(step=step):
                receipt = _run_job(step=step).receipts[0]
                self.assertFalse(medic_next_step.planning_spent([receipt]))


# ── 7. the lane a moved=no receipt names ────────────────────────────────────
class TheLaneReadTest(unittest.TestCase):
    def _receipt_call(self, r):
        (call,) = r.called("medic_next_step.py", "receipt")
        return call

    def test_a_failed_or_empty_read_gives_unknown_and_still_posts(self):
        for path in ({"step": "requeue-once", "gh_rc": 1},
                     {"step": "send-to-planning", "advance": "refuse"}):
            for state, state_rc in (("Green Light", 1), ("", 0)):
                with self.subTest(path=path, state=state, state_rc=state_rc):
                    r = _run_job(**path, state=state, state_rc=state_rc)
                    call = self._receipt_call(r)
                    self.assertIn("--moved-no", call)
                    self.assertEqual("unknown", call[call.index("--lane") + 1])
                    self.assertEqual(1, len(r.receipts))
                    self.assertIn("left in unknown", r.receipts[0])
                    self.assertEqual(1, len(r.called("linear_ops.py", "state-of")))

    def test_a_plain_receipt_makes_no_read(self):
        for step in THREE_STEPS:
            with self.subTest(step=step):
                r = _run_job(step=step)
                self.assertEqual([], r.called("linear_ops.py", "state-of"))
                self.assertNotIn("--moved-no", self._receipt_call(r))
                self.assertNotIn("--lane", self._receipt_call(r))


# ── 8. fix-path ─────────────────────────────────────────────────────────────
class TheFixPathTest(unittest.TestCase):
    def test_it_delivers_moves_nothing_and_names_the_routes(self):
        r = _run_job(step="fix-path")
        self.assertEqual(["run-failure-diagnosed", "medic-next-step"],
                         [act for _, _, act in r.comments])
        receipt = r.receipts[0]
        self.assertEqual(("bad-code", "fix-path", None),
                         _RECEIPT_FIRST.match(receipt.split("\n", 1)[0]).groups())
        self.assertIn(PR_URL, receipt)
        self.assertIn("agent-fix.yml", receipt)
        self.assertIn("asks for changes", receipt)
        self.assertIn("verifier fails", receipt)
        self.assertIn("reconcile.fix_approved_but_red", receipt)
        self.assertEqual([], r.called("linear_ops.py", "advance"))
        self.assertEqual([], r.called("linear_ops.py", "state-of"))
        self.assertEqual([], r.called("gh"))

    def test_the_named_routes_still_exist(self):
        tree = ast.parse(Path(RECONCILE).read_text(encoding="utf-8"))
        self.assertIn("fix_approved_but_red",
                      {n.name for n in tree.body if isinstance(n, ast.FunctionDef)})
        with open(AGENT_FIX, encoding="utf-8") as f:
            gate = _flat(yaml.safe_load(f)["jobs"]["fix"]["if"])
        self.assertIn("github.event.comment.user.login == 'agent-bureau-qa-bot[bot]'", gate)
        self.assertIn("contains(github.event.comment.body, 'VERDICT: REQUEST_CHANGES')", gate)
        self.assertIn("contains(github.event.comment.body, 'VERDICT: FAIL')", gate)


# ── 9. the other targets, and ask-person ────────────────────────────────────
class TheOtherTargetsTest(unittest.TestCase):
    def test_a_repair_card_is_delivered_as_today_with_no_receipt(self):
        r = _run_job(step="", kind="repair-card")
        self.assertEqual([(REPAIR_CARD, REPORT_TEXT.rstrip("\n"), "run-failure-diagnosed")],
                         r.comments)
        self.assertFalse(r.ran["receipt"])
        self.assertFalse(r.ran["planning"])

    def test_a_notice_is_raised_as_today_with_no_comment(self):
        r = _run_job(step="", kind="notice")
        self.assertEqual([], r.comments)
        self.assertEqual(1, len(r.called("pipeline_notice.py", "raise")))
        self.assertFalse(r.ran["receipt"])

    def test_ask_person_is_delivered_then_asked_with_one_receipt(self):
        # DRE-7794: the diagnosis as before, then the question in Green Light
        # (tests/test_medic_asks_a_person.py), then the one receipt.
        r = _run_job(step="ask-person")
        self.assertEqual((CARD, REPORT_TEXT.rstrip("\n"), "run-failure-diagnosed"),
                         r.comments[0])
        self.assertEqual([None, "medic-next-step"], [act for _, _, act in r.comments[1:]])
        self.assertTrue(r.ran["ask"])
        self.assertTrue(r.ran["receipt"])


# ── 10. the receipt step, as written ────────────────────────────────────────
class TheReceiptStepTest(unittest.TestCase):
    def setUp(self):
        self.step = _step("receipt")

    def test_its_name_and_place(self):
        self.assertEqual("Write the medic's receipt", self.step["name"])
        self.assertLess(_index("resume"), _index("held"))
        self.assertLess(_index("held"), _index("deliver"))
        self.assertLess(_index("deliver"), _index("planning"))
        self.assertLess(_index("planning"), _index("ask"))
        self.assertLess(_index("ask"), _index("receipt"))
        self.assertLess(_index("receipt"), _index("death"))

    def test_the_move_and_the_receipt_open_with_always(self):
        for sid in ("planning", "receipt"):
            with self.subTest(step=sid):
                self.assertTrue(_flat(_step(sid)["if"]).startswith("always() &&"))

    def test_the_gate_lists_the_four_steps_and_no_other(self):
        # DRE-7794 added `ask-person`, with its park, to the one receipt step.
        cond = _flat(self.step["if"])
        for step in (*THREE_STEPS, "ask-person"):
            self.assertIn(f"steps.verdict.outputs.step == '{step}'", cond)
        self.assertIn("steps.target.outputs.kind == 'card'", cond)
        base = {"steps.target.outputs.kind": "card"}
        for step in (*THREE_STEPS, "ask-person"):
            self.assertTrue(_runs(cond, {**base, "steps.verdict.outputs.step": step}))
        for step in ("", None):
            self.assertFalse(_runs(cond, {**base, "steps.verdict.outputs.step": step}))
        self.assertFalse(_runs(cond, {"steps.target.outputs.kind": "notice",
                                      "steps.verdict.outputs.step": "fix-path"}))

    def test_the_finding_comes_from_the_finding_reader(self):
        run_ = _flat(self.step["run"])
        self.assertIn('FINDING=$(python3 "$S/medic_next_step.py" finding --report "$REPORT")',
                      run_)
        self.assertIn('--finding "$FINDING"', run_)
        self.assertIn("REPORT=.bureau-pipeline/medic-report.md", run_)
        r = _run_job(step="fix-path")
        (call,) = r.called("medic_next_step.py", "finding")
        self.assertEqual(["medic_next_step.py", "finding", "--report", REPORT], call)
        (receipt_call,) = r.called("medic_next_step.py", "receipt")
        self.assertEqual("The card asks for two lanes that contradict each other.",
                         receipt_call[receipt_call.index("--finding") + 1])
        self.assertIn("Finding: The card asks for two lanes that contradict each other.",
                      r.receipts[0])

    def test_no_run_text_on_a_shell_line(self):
        for sid in ("waiting", "held", "planning", "receipt"):
            self.assertNotIn("${{", _step(sid)["run"], sid)

    def test_a_write_that_fails_warns_and_never_fails_the_job(self):
        r = _run_job(step="fix-path", comment_rc=1)
        self.assertRegex(r.log, r"::warning[^\n]*receipt")

    def test_each_path_posts_exactly_one_receipt(self):
        for step in THREE_STEPS:
            with self.subTest(step=step):
                self.assertEqual(1, len(_run_job(step=step).receipts))


# ── 11. the act ─────────────────────────────────────────────────────────────
class TheActTest(unittest.TestCase):
    def setUp(self):
        with open(ACTS, encoding="utf-8") as f:
            self.doc = json.load(f)
        self.by_name = {a["name"]: a for a in self.doc["acts"]}

    def test_the_act_is_registered(self):
        act = self.by_name["medic-next-step"]
        self.assertEqual("medic-next-step", act["tag"])
        self.assertEqual("recovery", act["kind"])
        self.assertIn(act["state"], self.doc["states"])
        self.assertEqual({"file": ".github/workflows/medic.yml", "anchor": RECEIPT_ANCHOR},
                         {k: act["emits"][k] for k in ("file", "anchor")})

    def test_the_diagnosis_act_is_unchanged(self):
        act = self.by_name["run-failure-diagnosed"]
        self.assertEqual("medic-diagnosis", act["tag"])
        self.assertEqual("hold", act["kind"])
        self.assertEqual(DIAGNOSIS_ANCHOR, act["emits"]["anchor"])

    def test_the_registry_checks_pass(self):
        for script in (["pipeline_act.py", "check"], ["check_act_receipts.py"]):
            with self.subTest(script=script):
                proc = subprocess.run(
                    [sys.executable, os.path.join(ROOT, "scripts", script[0]), *script[1:]],
                    cwd=ROOT, capture_output=True, text=True, check=False, timeout=300)
                self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)


# ── 12. the notes the scripts write ─────────────────────────────────────────
class TheNotesTest(unittest.TestCase):
    def test_a_resume_no_longer_says_nothing_was_posted(self):
        note = medic_resume.summary("x", failed_attempt=2, run_url=RUN_URL,
                                    workflow="Agent Task")
        self.assertNotIn("nothing was posted", note)
        self.assertIn("attempt 3", note)

    def test_a_waiting_note_names_the_card_and_says_nothing_moved(self):
        note = medic_resume.summary("The report.", failed_attempt=2, run_url=RUN_URL,
                                    workflow="Agent Task", waiting=CARD)
        self.assertIn(CARD, note)
        self.assertIn("posted nothing", note)
        self.assertIn("started no re-run", note)
        self.assertIn("The report.", note)
        self.assertNotIn("Resumed:", note)
        self.assertNotIn("attempt 3", note)

    def test_the_receipt_takes_the_finding_it_is_given(self):
        with tempfile.TemporaryDirectory() as td:
            report = Path(td) / "r.md"
            report.write_text("From the report. More.", encoding="utf-8")
            base = [sys.executable, os.path.join(ROOT, "scripts", "medic_next_step.py"),
                    "receipt", "--class", "bad-code", "--step", "fix-path",
                    "--run-url", RUN_URL, "--report", str(report)]
            given = subprocess.run([*base, "--finding", "Given here."],
                                   capture_output=True, text=True, check=True).stdout
            empty = subprocess.run([*base, "--finding", ""],
                                   capture_output=True, text=True, check=True).stdout
        self.assertIn("Finding: Given here.", given)
        self.assertIn("Finding: From the report.", empty)


if __name__ == "__main__":
    unittest.main()
