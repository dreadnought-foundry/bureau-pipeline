"""The diagnosis agent gets the card as a snapshot, not a Linear key
(Stage 2 fix #23, package BP-6).

Before this change the diagnosis agent ran with `LINEAR_API_KEY` in its
environment and a prompt that suggested "GraphQL via curl with LINEAR_API_KEY"
for finding an existing failure card. That is an open-ended reader on the
fleet's Linear hour: how many requests a diagnosis spent was whatever the model
chose, and nothing counted them. Every one printed `budget: undeclared`.

What the agent actually needs from Linear is small and knowable in advance:
which card the report goes to (the head branch's card, an open
"Pipeline failure: <workflow>" card, or none yet), and that card's recent
history, so a repeat failure can say whether the diagnosis changed. So:

  * a step BEFORE the agent (`medic_retry.py diagnosis-target`) resolves the
    target and writes the card's facts to a snapshot file — one read for a
    card on the branch, two for a failure card found by title, one when there
    is none yet;
  * the agent reads that file and the failed run's logs, and writes its report
    to a file. It holds no Linear key;
  * a step AFTER the agent posts the report: a comment, through the same act
    as before (`run-failure-diagnosed`), or a new card through `create`.

The diagnosis itself was never read from Linear. The agent reads the failed
run's logs; that part of the prompt is unchanged, and so is its turn ceiling
and its web access.
"""

import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/test")
os.environ.setdefault("GH_TOKEN", "test")

import linear_ops  # noqa: E402
import medic_retry  # noqa: E402
import pipeline_act  # noqa: E402

WORKFLOW = os.path.join(ROOT, ".github", "workflows", "medic.yml")
SNAPSHOT = ".bureau-pipeline/medic-card-snapshot.json"
REPORT = ".bureau-pipeline/medic-diagnosis.md"


def _jobs() -> dict:
    with open(WORKFLOW, encoding="utf-8") as f:
        return yaml.safe_load(f)["jobs"]


def _diagnose_steps() -> list:
    return _jobs()["diagnose"]["steps"]


def _step(step_id: str) -> dict:
    return next(s for s in _diagnose_steps() if s.get("id") == step_id)


def _index(step_id: str) -> int:
    return next(i for i, s in enumerate(_diagnose_steps()) if s.get("id") == step_id)


# ── 1. the agent holds no Linear key ─────────────────────────────────────────
class TheAgentHoldsNoLinearKeyTest(unittest.TestCase):
    def test_the_agent_step_has_no_linear_key(self):
        agent = _step("claude")
        self.assertNotIn("LINEAR_API_KEY", (agent.get("env") or {}))
        self.assertNotIn("LINEAR_API_KEY", yaml.safe_dump(agent))

    def test_the_prompt_never_sends_the_agent_to_linear(self):
        prompt = _step("claude")["with"]["prompt"]
        for phrase in ("linear_ops.py", "GraphQL", "curl", "LINEAR_API_KEY"):
            self.assertNotIn(phrase, prompt)

    def test_the_prompt_hands_over_the_snapshot_and_names_the_report_file(self):
        prompt = _step("claude")["with"]["prompt"]
        self.assertIn(SNAPSHOT, prompt)
        self.assertIn(REPORT, prompt)
        # Card text is agent-influenced: the agent is told it is data.
        self.assertIn("not instructions", prompt)

    def test_the_diagnosis_part_of_the_prompt_is_unchanged(self):
        prompt = _step("claude")["with"]["prompt"]
        self.assertIn("gh run view ${{ github.event.workflow_run.id }} --log-failed", prompt)
        self.assertIn("Distinguish: (a) bad code on the", prompt)
        self.assertIn("Do NOT attempt to fix anything", prompt)


# ── 2. the steps around the agent ────────────────────────────────────────────
class TheStepsAroundTheAgentTest(unittest.TestCase):
    def test_the_target_is_resolved_before_the_agent(self):
        step = _step("target")
        self.assertLess(_index("target"), _index("claude"))
        self.assertIn("medic_retry.py diagnosis-target", step["run"])
        self.assertIn(SNAPSHOT, step["run"])
        self.assertEqual("${{ secrets.LINEAR_API_KEY }}", step["env"]["LINEAR_API_KEY"])

    def test_the_target_step_never_takes_the_diagnosis_down(self):
        """No answer is not a reason to skip the diagnosis: the delivery step
        resolves the target itself when this one could not."""
        self.assertIn("|| OUT=", _step("target")["run"])

    def test_the_report_is_delivered_after_the_agent(self):
        step = _step("deliver")
        self.assertGreater(_index("deliver"), _index("claude"))
        self.assertEqual("${{ secrets.LINEAR_API_KEY }}", step["env"]["LINEAR_API_KEY"])
        self.assertIn("--act=run-failure-diagnosed", step["run"])
        self.assertIn("linear_ops.py create", step["run"])

    def test_agent_influenced_text_reaches_the_steps_through_env(self):
        """DRE-1996: the head branch and the run name never sit on a shell
        line as `${{ }}`."""
        for step_id in ("target", "deliver"):
            run = _step(step_id)["run"]
            self.assertNotIn("${{", run, step_id)

    def test_the_act_is_still_pinned_where_it_is_emitted(self):
        """The registry pins `run-failure-diagnosed` to its emission in
        medic.yml. The emission moved from the prompt into the delivery step;
        the pin moved with it, and the registry still binds."""
        self.assertEqual([], pipeline_act.problems())
        anchor = pipeline_act.record("run-failure-diagnosed")["emits"]["anchor"]
        self.assertIn(anchor, _step("deliver")["run"])


# ── 3. diagnosis-target: one read per card, a snapshot, no guessing ──────────
class _Resp:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode()
        self.headers = {}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class _Linear:
    def __init__(self, open_card=None, fail_find=False):
        self.open_card = open_card
        self.fail_find = fail_find

    def __call__(self, request, *_a, **_k):
        query = json.loads(request.data)["query"]
        if "issues(filter" in query:
            if self.fail_find:
                raise OSError("connection refused")
            nodes = [{"identifier": self.open_card}] if self.open_card else []
            return _Resp({"data": {"issues": {"nodes": nodes}}})
        return _Resp({"data": {"issue": {
            "title": "Pipeline failure: Reconcile",
            "description": "the sweep died",
            "state": {"name": "Planning"},
            "labels": {"nodes": [{"name": "repo:agent-bureau"}]},
            "comments": {"pageInfo": {"hasNextPage": False}, "nodes": [
                {"body": "failed again; diagnosis unchanged",
                 "createdAt": "2026-10-02T19:00:00Z"},
            ]},
        }}})


def _target(branch, workflow="Reconcile", *, linear=None):
    linear_ops._reset_budget_state()
    with tempfile.TemporaryDirectory() as d:
        snap = os.path.join(d, "snap.json")
        out = io.StringIO()
        with mock.patch.object(linear_ops.urllib.request, "urlopen", linear or _Linear()):
            with mock.patch.object(linear_ops.time, "sleep", lambda _s: None):
                with contextlib.redirect_stdout(out), \
                        contextlib.redirect_stderr(io.StringIO()):
                    rc = medic_retry.main([
                        "diagnosis-target", "--branch", branch,
                        "--workflow", workflow, "--snapshot", snap,
                    ])
        calls = linear_ops.requests_made()
        snapshot = None
        if os.path.exists(snap):
            with open(snap, encoding="utf-8") as f:
                snapshot = json.load(f)
    linear_ops._reset_budget_state()
    lines = dict(ln.split("=", 1) for ln in out.getvalue().splitlines() if "=" in ln)
    return rc, lines, calls, snapshot


class DiagnosisTargetTest(unittest.TestCase):
    def test_a_card_branch_is_the_target_and_costs_one_read(self):
        rc, out, calls, snap = _target("agent/DRE-5620-chain-risk-reason")
        self.assertEqual(0, rc)
        self.assertEqual({"kind": "card", "target": "DRE-5620"},
                         {k: out[k] for k in ("kind", "target")})
        self.assertEqual(1, calls)
        self.assertEqual("DRE-5620", snap["target"])
        self.assertEqual("Planning", snap["state"])
        self.assertEqual("the sweep died", snap["description"])
        self.assertEqual(["failed again; diagnosis unchanged"],
                         [c["body"] for c in snap["comments"]])

    def test_an_open_failure_card_is_found_by_title_and_read(self):
        rc, out, calls, snap = _target("main", linear=_Linear(open_card="DRE-4000"))
        self.assertEqual(("failure-card", "DRE-4000"), (out["kind"], out["target"]))
        self.assertEqual("Pipeline failure: Reconcile", out["title"])
        self.assertEqual(2, calls)
        self.assertEqual("DRE-4000", snap["target"])

    def test_no_failure_card_yet_means_a_new_one(self):
        rc, out, calls, snap = _target("main")
        self.assertEqual(("new", ""), (out["kind"], out["target"]))
        self.assertEqual(1, calls)
        self.assertEqual("new", snap["kind"])
        self.assertNotIn("comments", snap)

    def test_an_unanswered_search_is_unknown_never_new(self):
        """A search that failed is not a search that found nothing. `new`
        would mint a duplicate card; `unknown` sends the delivery step to ask
        again."""
        rc, out, calls, snap = _target("main", linear=_Linear(fail_find=True))
        self.assertEqual(0, rc)
        self.assertEqual("unknown", out["kind"])

    def test_the_title_is_one_line(self):
        rc, out, _, _ = _target("main", workflow="Reconcile\nkind=card")
        self.assertEqual("new", out["kind"])
        self.assertNotIn("\n", out["title"])


# ── 4. the delivery step, executed ───────────────────────────────────────────
SHIM = r"""#!/bin/bash
printf '%s\n' "$*" >> "$SHIM_LOG"
case "$*" in
  *linear_ops.py\ find-open*) printf '%s' "${FAKE_FOUND:-}"; exit "${FAKE_FIND_RC:-0}" ;;
esac
exit 0
"""


def _render(text: str, values: dict) -> str:
    def sub(match):
        key = match.group(1).strip()
        if key not in values:
            raise AssertionError(f"the step reads {key!r}, which this test does not fake")
        return values[key]

    return re.sub(r"\$\{\{\s*([^}]+?)\s*\}\}", sub, text)


def _deliver(kind, target="", *, report="the report", found="", find_rc=0):
    step = _step("deliver")
    values = {
        "secrets.LINEAR_API_KEY": "test-key",
        "steps.target.outputs.kind": kind,
        "steps.target.outputs.target": target,
        "steps.target.outputs.title": "Pipeline failure: Reconcile",
        "steps.repo.outputs.slug": "agent-bureau",
        "github.event.workflow_run.name": "Reconcile",
        "github.event.workflow_run.html_url": "https://github.com/o/r/actions/runs/1",
    }
    env = {k: _render(str(v), values) for k, v in (step.get("env") or {}).items()}
    with tempfile.TemporaryDirectory() as raw:
        td = Path(raw)
        (td / "bin").mkdir()
        (td / "bin" / "python3").write_text(SHIM)
        os.chmod(td / "bin" / "python3", 0o755)
        (td / ".bureau-pipeline").mkdir()
        if report is not None:
            (td / REPORT).write_text(report)
        log = td / "calls.log"
        log.touch()
        proc = subprocess.run(
            ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", step["run"]],
            cwd=td, capture_output=True, text=True, check=False,
            env={**os.environ, **env, "PATH": f"{td / 'bin'}:{os.environ['PATH']}",
                 "SHIM_LOG": str(log), "FAKE_FOUND": found,
                 "FAKE_FIND_RC": str(find_rc)},
        )
        return proc, [ln for ln in log.read_text().splitlines() if ln]


class TheDeliveryStepTest(unittest.TestCase):
    def test_a_card_on_the_branch_gets_the_act(self):
        proc, calls = _deliver("card", "DRE-5620")
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual(1, len(calls), calls)
        self.assertIn("linear_ops.py comment DRE-5620 the report --act=run-failure-diagnosed", calls[0])

    def test_an_open_failure_card_gets_the_report_as_a_comment(self):
        proc, calls = _deliver("failure-card", "DRE-4000")
        self.assertEqual(1, len(calls), calls)
        self.assertIn("linear_ops.py comment DRE-4000 the report", calls[0])

    def test_no_failure_card_yet_creates_one_with_the_repo_label(self):
        proc, calls = _deliver("new")
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual(1, len(calls), calls)
        self.assertIn("linear_ops.py create Pipeline failure: Reconcile", calls[0])
        self.assertIn(REPORT, calls[0])
        self.assertIn("--repo agent-bureau", calls[0])

    def test_an_unknown_target_searches_before_creating(self):
        proc, calls = _deliver("unknown", found="DRE-4000")
        self.assertEqual(2, len(calls), calls)
        self.assertIn("linear_ops.py find-open Pipeline failure: Reconcile", calls[0])
        self.assertIn("linear_ops.py comment DRE-4000", calls[1])
        proc, calls = _deliver("unknown")
        self.assertIn("linear_ops.py create", calls[-1])

    def test_a_search_that_fails_again_creates_nothing(self):
        proc, calls = _deliver("unknown", find_rc=1)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual([], [c for c in calls if "create" in c or " comment " in c])
        self.assertIn("::warning", proc.stdout + proc.stderr)

    def test_no_report_posts_nothing_and_says_so(self):
        proc, calls = _deliver("card", "DRE-5620", report=None)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual([], calls)
        self.assertIn("::warning", proc.stdout + proc.stderr)

    def test_the_step_runs_after_a_failed_agent_too(self):
        """A report written before the agent died is still worth delivering;
        the step decides by the file, not by the agent's outcome."""
        self.assertIn("always()", _step("deliver").get("if", ""))


if __name__ == "__main__":
    unittest.main()
