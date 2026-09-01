"""Scenario: the fix loop's two receipts, composed by EXECUTING the workflow.

Unit-green is not live-working, and this half of DRE-2826 crosses two systems:
a shell body assembled in `agent-fix.yml` and a Python writer invoked from it.
`tests/test_act_emission.py` proves the writer and pins the shell literal at the
source; neither of them runs the step. So this does — the real `Report` block
from `.github/workflows/agent-fix.yml`, under the runner's own shell flags, with
`gh` and `linear_ops.py` stubbed and the REAL `pipeline_act.py` and registry in
the checkout, exactly as a fix run has them.

What it catches that a source-level pin cannot: a quoting mistake in the shell
that mangles the body, a `--out` path that does not reach `--body-file`, or a
composition failure that silently posts nothing. The last one is why the
fallback exists — a lost trailer is a missing machine-readable line; a lost
comment is a disputed fix nobody is told about.

Follows the harness in tests/test_fix_dispatch_clears_stale_hold.py.

Run: python3 -m pytest tests/test_act_emission_scenario.py -v
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW = os.path.join(ROOT, ".github", "workflows", "agent-fix.yml")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import pipeline_act  # noqa: E402

CARD = "DRE-2826"
PR = "207"
REPO = "dreadnought-foundry/bureau-pipeline"
PRE_SHA = "a" * 40
POST_SHA = "b" * 40
BLOCKER = "the reviewer's finding is wrong and I will not force it"

# The paths the step itself names. Absolute in the workflow, so absolute here.
BLOCKER_FILE = "/tmp/fix-blocker.txt"
RECEIPTS = ("/tmp/act-fix-blocked.md", "/tmp/act-fix-pushed.md")


def report_step() -> dict:
    for step in yaml.safe_load(open(WORKFLOW))["jobs"]["fix"]["steps"]:
        if step.get("name") == "Report":
            return step
    raise AssertionError("the Report step is gone from agent-fix.yml")


def substitute(run: str, values: dict) -> str:
    """Apply the `${{ }}` substitutions Actions would make, and prove none
    survive — an unsubstituted expression is a hole in the harness, not a pass."""
    def repl(m):
        key = m.group(1).strip()
        if key not in values:
            raise AssertionError(f"harness has no value for ${{{{ {key} }}}}")
        return values[key]

    out = re.sub(r"\$\{\{([^}]*)\}\}", repl, run)
    assert "${{" not in out
    return out


def _checkout(td: str) -> str:
    """A `.bureau-pipeline` checkout: the real scripts and the real registry,
    with only linear_ops.py replaced. pipeline_act.py is the thing under test
    and reads config/pipeline-acts.json relative to itself, so both are real."""
    base = os.path.join(td, ".bureau-pipeline")
    shutil.copytree(os.path.join(ROOT, "scripts"), os.path.join(base, "scripts"),
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(os.path.join(ROOT, "config"), os.path.join(base, "config"))
    _executable(
        os.path.join(base, "scripts", "linear_ops.py"),
        "#!/usr/bin/env python3\nimport sys\nsys.exit(0)\n",
    )
    return base


def _executable(path: str, body: str) -> None:
    with open(path, "w") as fh:
        fh.write(body)
    os.chmod(path, 0o755)


def _gh_stub(td: str, log: str) -> str:
    """A `gh` that records every comment it is asked to post and answers
    `pr view` with a head sha the caller chooses."""
    binary = os.path.join(td, "bin")
    os.makedirs(binary, exist_ok=True)
    _executable(os.path.join(binary, "gh"), f"""#!/usr/bin/env python3
import json, sys
argv = sys.argv[1:]
if argv[:2] == ["pr", "view"]:
    print("{POST_SHA}")
    sys.exit(0)
if argv[:2] == ["pr", "comment"]:
    body = None
    if "--body-file" in argv:
        body = open(argv[argv.index("--body-file") + 1], encoding="utf-8").read()
    elif "--body" in argv:
        body = argv[argv.index("--body") + 1]
    open({log!r}, "a").write(json.dumps({{"argv": argv, "body": body}}) + "\\n")
    sys.exit(0)
if argv[:1] == ["api"]:
    print("[]")
    sys.exit(0)
sys.exit(0)
""")
    return binary


def run_report(td: str, *, mode: str, blocked: bool):
    """Execute the real Report block. Returns (proc, posted comments)."""
    _checkout(td)
    log = os.path.join(td, "comments.jsonl")
    binary = _gh_stub(td, log)

    for path in (BLOCKER_FILE, *RECEIPTS):
        if os.path.exists(path):
            os.remove(path)
    if blocked:
        with open(BLOCKER_FILE, "w", encoding="utf-8") as fh:
            fh.write(BLOCKER + "\n")

    run = substitute(report_step()["run"], {
        "steps.pr.outputs.number": PR,
        "steps.pr.outputs.attempt": "2",
        "steps.pr.outputs.mode": mode,
        "steps.claude.outputs.execution_file": os.path.join(td, "exec.json"),
        "github.repository": REPO,
        "github.server_url": "https://github.com",
        "github.run_id": "1234",
    })
    script = os.path.join(td, "report.sh")
    with open(script, "w") as fh:
        fh.write("set -eo pipefail\n" + run)
    proc = subprocess.run(
        ["bash", script],
        cwd=td,
        env=dict(
            os.environ,
            PATH=binary + os.pathsep + os.environ["PATH"],
            CARD=CARD, PRE_SHA=PRE_SHA,
            GH_TOKEN="test", LINEAR_API_KEY="test-key",
        ),
        capture_output=True, text=True,
    )
    posted = [
        json.loads(line)
        for line in (open(log).read().splitlines() if os.path.exists(log) else [])
    ]
    return proc, posted


def _answer_format() -> str:
    return subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "fix_context.py"),
         "--answer-format"],
        capture_output=True, text=True, check=True,
    ).stdout


class FixLoopReceiptsCarryTheirTrailer(unittest.TestCase):
    def tearDown(self):
        for path in (BLOCKER_FILE, *RECEIPTS):
            if os.path.exists(path):
                os.remove(path)

    def test_the_blocker_notice_is_the_live_body_plus_its_trailer(self):
        with tempfile.TemporaryDirectory() as td:
            proc, posted = run_report(td, mode="fix", blocked=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(posted), 1, posted)
        expected_body = (
            f"🛑 Fix attempt 2 blocked: {BLOCKER}\n\n{_answer_format().rstrip()}"
        )
        self.assertEqual(
            posted[0]["body"],
            f"{expected_body}\n\n{pipeline_act.trailer('fix-attempt-disputed')}",
        )

    def test_the_push_marker_is_the_live_body_plus_its_trailer(self):
        with tempfile.TemporaryDirectory() as td:
            proc, posted = run_report(td, mode="fix", blocked=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(posted), 1, posted)
        self.assertEqual(
            posted[0]["body"],
            "🔧 Fix attempt 2 pushed — CI and critic review re-running."
            f"\n\n{pipeline_act.trailer('fix-attempt-landed')}",
        )

    def test_the_conflict_wording_is_the_same_act(self):
        """Two wordings, one act. A second trailer here would make the conflict
        rounds count as a different obligation from the fix attempts."""
        with tempfile.TemporaryDirectory() as td:
            proc, posted = run_report(td, mode="conflict", blocked=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            posted[0]["body"],
            "🔀 Conflict resolution round 2 pushed — CI and critic review "
            f"re-running.\n\n{pipeline_act.trailer('fix-attempt-landed')}",
        )

    def test_the_push_marker_the_workflow_reads_back_still_opens_the_body(self):
        """agent-fix.yml routes fix-vs-conflict on `contains("pushed — CI and
        critic review re-running")` and fix_budget counts on "🔧 Fix attempt".
        The trailer goes last precisely so neither read moves."""
        with tempfile.TemporaryDirectory() as td:
            _, posted = run_report(td, mode="fix", blocked=False)
        body = posted[0]["body"]
        self.assertTrue(body.startswith("🔧 Fix attempt"))
        self.assertIn("pushed — CI and critic review re-running", body)

    def test_a_broken_writer_still_posts_the_comment(self):
        """The fail-soft rule, exercised. If composition dies the escalation
        still reaches the PR — a lost trailer is a missing machine-readable
        line, a lost comment is a disputed fix nobody is told about."""
        with tempfile.TemporaryDirectory() as td:
            base = _checkout(td)
            _executable(os.path.join(base, "scripts", "pipeline_act.py"),
                        "#!/usr/bin/env python3\nimport sys\nsys.exit(3)\n")
            log = os.path.join(td, "comments.jsonl")
            binary = _gh_stub(td, log)
            with open(BLOCKER_FILE, "w", encoding="utf-8") as fh:
                fh.write(BLOCKER + "\n")
            run = substitute(report_step()["run"], {
                "steps.pr.outputs.number": PR,
                "steps.pr.outputs.attempt": "2",
                "steps.pr.outputs.mode": "fix",
                "steps.claude.outputs.execution_file": os.path.join(td, "exec.json"),
                "github.repository": REPO,
                "github.server_url": "https://github.com",
                "github.run_id": "1234",
            })
            script = os.path.join(td, "report.sh")
            with open(script, "w") as fh:
                fh.write("set -eo pipefail\n" + run)
            proc = subprocess.run(
                ["bash", script], cwd=td,
                env=dict(os.environ, PATH=binary + os.pathsep + os.environ["PATH"],
                         CARD=CARD, PRE_SHA=PRE_SHA, GH_TOKEN="test",
                         LINEAR_API_KEY="test-key"),
                capture_output=True, text=True,
            )
            posted = [json.loads(line) for line in
                      (open(log).read().splitlines() if os.path.exists(log) else [])]
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(posted), 1, posted)
        self.assertTrue(posted[0]["body"].startswith("🛑 Fix attempt 2 blocked:"))
        self.assertIsNone(
            pipeline_act.read_trailer(posted[0]["body"]),
            "the fallback posts the raw body — it must not invent a trailer",
        )


if __name__ == "__main__":
    unittest.main()
