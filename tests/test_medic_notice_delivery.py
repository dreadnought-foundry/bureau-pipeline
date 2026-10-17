"""The medic raises a console alert for a failed run, not a card (DRE-7453).

The medic was the pipeline's biggest notice writer: 46 "Pipeline failure:
<workflow>" cards in the 30 days to 2026-10-16, 29 of them ended Canceled or
Duplicate. Its "Deliver the diagnosis" step now hands a diagnosis with no card
to go to to `pipeline_notice.py raise`, under the key
`medic:<repo>:<workflow file>`, and a repeat failure is a raise of the same
key, which the console answers `repeat`: one alert, its text refreshed.

What stays as it was: a diagnosis for a card on the head branch, or for the
red-main repair card open on the default branch, is a comment on that card.

These tests read the step out of `.github/workflows/medic.yml` and execute it
with every script it calls stubbed (except the two that compute what it
sends, which run for real), and drive `medic_retry.diagnosis_target` with a
fake board.
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
import notice_writers  # noqa: E402

WORKFLOW = os.path.join(ROOT, ".github", "workflows", "medic.yml")
REPORT = ".bureau-pipeline/medic-report.md"
KEY_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,120}$")
RUN_URL = "https://github.com/dreadnought-foundry/agent-bureau/actions/runs/38200000001"
HEAD_SHA = "bcb36adf60370d0e8052c5a3f81021900f93165b"
REPAIR_TITLE = "Red main repaired — Pipeline Tests failed at bcb36adf6037"


def _workflow() -> dict:
    with open(WORKFLOW, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _step(step_id: str) -> dict:
    steps = _workflow()["jobs"]["diagnose"]["steps"]
    return next(s for s in steps if s.get("id") == step_id)


# ── the step, executed ───────────────────────────────────────────────────────
#: Every call is one line of its arguments joined by \x1f, so an argument with
#: spaces in it ("Open the failed run") reads back whole.
SHIM = r"""#!/bin/bash
{ for a in "$@"; do printf '%s\x1f' "$a"; done; printf '\n'; } >> "$SHIM_LOG"
case "$*" in
  *linear_ops.py\ find-open*) exit 0 ;;
  *medic_retry.py\ repair-card*) printf '%s' "${FAKE_REPAIR:-}"; exit "${FAKE_REPAIR_RC:-0}" ;;
  # What the step sends is computed by the real script.
  *medic_retry.py\ failure-card-body*|*medic_retry.py\ notice-workflow-file*)
    shift; exec "$REAL_PYTHON" "$SCRIPTS/medic_retry.py" "$@" ;;
  # Keep the body the client was handed.
  *pipeline_notice.py\ raise*)
    while [ $# -gt 0 ]; do
      if [ "$1" = "--body-file" ]; then cp "$2" "$RAISED"; fi
      shift
    done
    exit "${FAKE_RAISE_RC:-0}" ;;
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


def _deliver(kind, target="", *, report="the diagnosis, in plain English",
             workflow="Reconcile", path=".github/workflows/agent-task.yml",
             slug="agent-bureau", repair="", repair_rc=0, raise_rc=0,
             title=None):
    step = _step("deliver")
    values = {
        "secrets.LINEAR_API_KEY": "test-key",
        "vars.BUREAU_READ_URL": "https://console.example.test",
        "steps.target.outputs.kind": kind,
        "steps.target.outputs.target": target,
        "steps.target.outputs.title": (medic_retry.failure_card_title(workflow)
                                       if title is None else title),
        "steps.repo.outputs.slug": slug,
        "github.event.workflow_run.name": workflow,
        "github.event.workflow_run.path": path,
        "github.event.workflow_run.html_url": RUN_URL,
        "github.event.workflow_run.head_branch": "main",
        "github.event.workflow_run.head_sha": HEAD_SHA,
        "github.event.repository.default_branch": "main",
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
        raised = td / "raised.md"
        proc = subprocess.run(
            ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", step["run"]],
            cwd=td, capture_output=True, text=True, check=False,
            env={**os.environ, **env, "PATH": f"{td / 'bin'}:{os.environ['PATH']}",
                 "SHIM_LOG": str(log), "FAKE_REPAIR": repair,
                 "FAKE_REPAIR_RC": str(repair_rc), "FAKE_RAISE_RC": str(raise_rc),
                 "REAL_PYTHON": sys.executable,
                 "SCRIPTS": os.path.join(ROOT, "scripts"), "RAISED": str(raised)},
        )
        calls = [ln.split("\x1f")[:-1] for ln in log.read_text().splitlines() if ln]
        body = raised.read_text(encoding="utf-8") if raised.exists() else None
        return proc, calls, body


def _script(call) -> str:
    return " ".join(call[:2])


def _raises(calls):
    return [c for c in calls if c[:2] == [".bureau-pipeline/scripts/pipeline_notice.py", "raise"]]


def _flag(call, name):
    values = [call[i + 1] for i in range(len(call) - 1) if call[i] == name]
    return values


class NoCardRaisesTheNoticeTest(unittest.TestCase):
    def test_the_diagnosis_is_raised_under_the_medic_key(self):
        proc, calls, body = _deliver("notice")
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        raises = _raises(calls)
        self.assertEqual(1, len(raises), calls)
        call = raises[0]
        self.assertEqual(["medic:agent-bureau:agent-task.yml"], _flag(call, "--notice"))
        self.assertEqual(["WARN"], _flag(call, "--severity"))
        self.assertEqual([RUN_URL], _flag(call, "--action-url"))
        self.assertEqual(["Open the failed run"], _flag(call, "--action-label"))
        self.assertTrue(body.startswith("the diagnosis, in plain English"), body)

    def test_the_fallback_is_today_s_card_exactly(self):
        proc, calls, _ = _deliver("notice", workflow="Pipeline Tests")
        call = _raises(calls)[0]
        self.assertEqual([medic_retry.failure_card_title("Pipeline Tests")],
                         _flag(call, "--fallback-title"))
        self.assertEqual(["Planning"], _flag(call, "--fallback-lane"))
        self.assertEqual(["repo:agent-bureau"], _flag(call, "--fallback-label"))

    def test_no_card_is_created_and_no_failure_card_searched(self):
        for kind in ("notice", "unknown", ""):
            proc, calls, _ = _deliver(kind)
            self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
            self.assertEqual(1, len(_raises(calls)), (kind, calls))
            scripts = [_script(c) for c in calls]
            self.assertNotIn(".bureau-pipeline/scripts/linear_ops.py create", scripts)
            self.assertNotIn(".bureau-pipeline/scripts/linear_ops.py find-open", scripts)
            self.assertNotIn(".bureau-pipeline/scripts/linear_ops.py comment", scripts)

    def test_the_repair_card_is_asked_for_once_more_before_raising(self):
        proc, calls, _ = _deliver("notice")
        asked = [i for i, c in enumerate(calls)
                 if _script(c) == ".bureau-pipeline/scripts/medic_retry.py repair-card"]
        self.assertEqual(1, len(asked), calls)
        self.assertLess(asked[0], calls.index(_raises(calls)[0]))

    def test_a_raise_that_fails_warns_and_never_reds_the_job(self):
        proc, calls, _ = _deliver("notice", raise_rc=1)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn("::warning", proc.stdout)

    def test_a_target_step_with_no_title_still_hands_today_s_title(self):
        proc, calls, _ = _deliver("", workflow="Reconcile", title="")
        self.assertEqual(["Pipeline failure: Reconcile"],
                         _flag(_raises(calls)[0], "--fallback-title"))


class TheRepairCardStaysACommentTest(unittest.TestCase):
    def test_an_open_repair_card_is_commented_and_no_notice_raised(self):
        proc, calls, _ = _deliver("repair-card", "DRE-6511")
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual([], _raises(calls))
        comments = [c for c in calls
                    if _script(c) == ".bureau-pipeline/scripts/linear_ops.py comment"]
        self.assertEqual(1, len(comments), calls)
        self.assertEqual("DRE-6511", comments[0][2])
        self.assertIn("--act=run-failure-diagnosed", comments[0])

    def test_a_repair_card_found_on_the_second_ask_is_commented(self):
        proc, calls, _ = _deliver("notice", repair="DRE-6511")
        self.assertEqual([], _raises(calls))
        comments = [c for c in calls
                    if _script(c) == ".bureau-pipeline/scripts/linear_ops.py comment"]
        self.assertEqual("DRE-6511", comments[0][2])

    def test_a_branch_card_is_commented_and_no_notice_raised(self):
        proc, calls, _ = _deliver("card", "DRE-5620")
        self.assertEqual([], _raises(calls))
        self.assertEqual([], [c for c in calls if "repair-card" in c])

    def test_a_repair_lookup_that_fails_delivers_nothing(self):
        """Unchanged: a card filed on an unanswered search is the duplicate
        the search exists to prevent, and the fallback is a card."""
        proc, calls, _ = _deliver("notice", repair_rc=1)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual([], _raises(calls))
        self.assertIn("::warning title=Diagnosis not delivered::", proc.stdout)
        self.assertIn("the diagnosis, in plain English", proc.stdout)


# ── the key ──────────────────────────────────────────────────────────────────
class TheNoticeKeyTest(unittest.TestCase):
    def test_the_workflow_part_is_the_file_s_base_name(self):
        self.assertEqual("medic:atlas:agent-task.yml",
                         medic_retry.notice_key("atlas", ".github/workflows/agent-task.yml"))

    def test_spaces_and_parentheses_are_replaced(self):
        key = medic_retry.notice_key("atlas", ".github/workflows/my (odd) flow.yml")
        self.assertEqual("medic:atlas:my__odd__flow.yml", key)
        self.assertRegex(key, KEY_RE)

    def test_a_long_name_is_cut_to_fit_the_key(self):
        key = medic_retry.notice_key("atlas", ".github/workflows/" + "x" * 300 + ".yml")
        self.assertRegex(key, KEY_RE)
        self.assertTrue(key.startswith("medic:atlas:x"), key)

    def test_no_path_still_makes_a_valid_key(self):
        self.assertRegex(medic_retry.notice_key("atlas", ""), KEY_RE)

    def test_the_step_sends_the_key_the_helper_makes(self):
        for path in (".github/workflows/agent-task.yml",
                     ".github/workflows/my (odd) flow.yml",
                     ".github/workflows/" + "y" * 200 + ".yaml"):
            proc, calls, _ = _deliver("notice", path=path, slug="atlas")
            self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
            sent = _flag(_raises(calls)[0], "--notice")
            self.assertEqual([medic_retry.notice_key("atlas", path)], sent, path)
            self.assertRegex(sent[0], KEY_RE)


# ── the step's environment and the job's grant ───────────────────────────────
class TheStepCanReachTheDoorTest(unittest.TestCase):
    def test_the_step_env_carries_the_url_and_the_workflow_path(self):
        env = _step("deliver")["env"]
        self.assertEqual("${{ vars.BUREAU_READ_URL }}", env["BUREAU_READ_URL"])
        self.assertEqual("${{ github.event.workflow_run.path }}", env["WORKFLOW_PATH"])

    def test_the_job_is_not_denied_id_token_write(self):
        """A called job holds what its stub grants unless a `permissions:`
        block narrows it — and one that narrows must grant it again."""
        wf = _workflow()
        for scope in (wf, wf["jobs"]["diagnose"]):
            perms = scope.get("permissions")
            if perms is None:
                continue
            self.assertIsInstance(perms, dict, perms)
            self.assertEqual("write", perms.get("id-token"), perms)

    def test_the_step_never_puts_run_text_on_a_shell_line(self):
        self.assertNotIn("${{", _step("deliver")["run"])

    def test_the_step_no_longer_files_or_searches_failure_cards(self):
        run = _step("deliver")["run"]
        self.assertNotIn("linear_ops.py create", run)
        self.assertNotIn("find-open", run)
        self.assertIn("pipeline_notice.py raise", run)


# ── diagnosis_target, against a fake board ───────────────────────────────────
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


class _Board:
    """Answers a title search from `open_cards` (exact title → identifier),
    and any card read with a small card."""

    def __init__(self, open_cards=None):
        self.open_cards = dict(open_cards or {})
        self.searched: list[str] = []

    def __call__(self, request, *_a, **_k):
        payload = json.loads(request.data)
        if "issues(filter" in payload["query"]:
            title = (payload.get("variables") or {}).get("t", "")
            self.searched.append(title)
            found = self.open_cards.get(title)
            return _Resp({"data": {"issues": {"nodes": [{"identifier": found}] if found else []}}})
        return _Resp({"data": {"issue": {
            "title": REPAIR_TITLE, "description": "red main",
            "state": {"name": "Triage"}, "labels": {"nodes": []},
            "comments": {"pageInfo": {"hasNextPage": False}, "nodes": []},
        }}})


def _target(branch, board, workflow="Pipeline Tests"):
    linear_ops._reset_budget_state()
    with tempfile.TemporaryDirectory() as d:
        snap = os.path.join(d, "snap.json")
        with mock.patch.object(linear_ops.urllib.request, "urlopen", board), \
                mock.patch.object(linear_ops.time, "sleep", lambda _s: None), \
                contextlib.redirect_stderr(io.StringIO()):
            found = medic_retry.diagnosis_target(
                branch, workflow, snap, head_sha=HEAD_SHA, default_branch="main")
    linear_ops._reset_budget_state()
    return found


class DiagnosisTargetTest(unittest.TestCase):
    def test_off_the_default_branch_with_no_card_it_is_a_notice_and_reads_nothing(self):
        board = _Board({"Pipeline failure: Pipeline Tests": "DRE-4000"})
        found = _target("release-candidate", board)
        self.assertEqual(("notice", ""), (found["kind"], found["target"]))
        self.assertEqual([], board.searched)

    def test_an_open_failure_card_is_no_longer_the_target(self):
        board = _Board({"Pipeline failure: Pipeline Tests": "DRE-4000"})
        found = _target("main", board)
        self.assertEqual(("notice", ""), (found["kind"], found["target"]))
        self.assertEqual([REPAIR_TITLE], board.searched)

    def test_the_open_repair_card_on_the_default_branch_is_the_target(self):
        board = _Board({REPAIR_TITLE: "DRE-6511"})
        found = _target("main", board)
        self.assertEqual(("repair-card", "DRE-6511"), (found["kind"], found["target"]))

    def test_the_fallback_title_is_still_carried(self):
        found = _target("main", _Board())
        self.assertEqual("Pipeline failure: Pipeline Tests", found["failure_title"])


class ThePromptTest(unittest.TestCase):
    def _prompt(self) -> str:
        return _step("claude")["with"]["prompt"]

    def test_the_repeat_instruction_applies_to_the_repair_card_only(self):
        prompt = self._prompt()
        self.assertNotIn('"failure-card"', prompt)
        bullet = prompt.split('- "repair-card"', 1)[1].split("\n               - ", 1)[0]
        self.assertIn("Failed again at", bullet)

    def test_the_notice_kind_is_described(self):
        self.assertIn('"notice"', self._prompt())


class TheNoticeWritersReportTest(unittest.TestCase):
    def test_the_medic_reads_as_repointed(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            notice_writers.main(["report"])
        self.assertIn("notice-writers: medic repointed", out.getvalue().splitlines())


if __name__ == "__main__":
    unittest.main()
