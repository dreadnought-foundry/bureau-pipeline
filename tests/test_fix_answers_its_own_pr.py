"""Scenario: the fix agent answers THIS pull request, or it says nothing.

The live fault (DRE-3951, 2026-09-14). A fix run dispatched for portico #501
(DRE-3947) posted, two minutes after the critic's REQUEST_CHANGES: *"PR #2556
was already approved and merged before this run started. DRE-3863 is Done"* —
agent-bureau #2556's history, in another repository. The same week agent-bureau
#2553 (DRE-3891), holding an APPROVE and a verifier PASS, collected four
fix-agent comments about PR #2516's card and history.

The carrier was the handoff file. `agent-fix.yml` told the agent to write its
blocker to `/tmp/fix-blocker.txt` and read that fixed path back with
`[ -f ... ]`; the fleet's reused self-hosted minis never clear `/tmp`, so one
card's escalation was posted on the next card's pull request.

Unit-green is not live-working and this crosses two systems — a shell body in
`.github/workflows/agent-fix.yml` and a Python module invoked from it — so this
file EXECUTES the real workflow steps (the harness in
tests/test_refuted_verdict_rereview.py), with `gh` and `linear_ops.py` stubbed
and the real pipeline scripts in the checkout, over the real fault:

  1. a stale handoff from another repo's run, present when the job starts;
  2. a handoff appearing at the shared path DURING this run, whose provenance
     cannot be attributed to this repo, PR and head;
  3. a critic verdict stamped for another repo's pull request.

`tests/test_fix_handoff.py` proves the module; this proves the wiring.

Run: python3 -m pytest tests/test_fix_answers_its_own_pr.py -v
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
WORKFLOW = os.path.join(ROOT, ".github", "workflows", "agent-fix.yml")
sys.path.insert(0, SCRIPTS)

import fix_handoff  # noqa: E402

# This run: portico #501, the pull request the fix agent was dispatched for.
REPO = "dreadnought-foundry/portico"
PR = "501"
SHA = "247fcf428" + "e" * 31
CARD = "DRE-3947"
POST_SHA = "b" * 40
BLOCKER = "the placeholders the critic found are the card's own acceptance text"

# The other run: agent-bureau #2556, whose blocker was posted on #501.
OTHER_REPO = "dreadnought-foundry/agent-bureau"
OTHER_PR = "2556"
OTHER_SHA = "43363f040" + "d" * 31
OTHER_BLOCKER = (
    "PR #2556 was already approved and merged before this run started. "
    "DRE-3863 is Done."
)

VERDICT = (
    "🔍 **QA Critic** — PR #501\n\n"
    f"VERDICT: REQUEST_CHANGES cause:unmet-criteria @{SHA}\n"
)

GH_STUB = '''#!/usr/bin/env python3
"""Stand-in for `gh`: an empty comment thread, a chosen head sha, and a log of
every write it is asked to make."""
import json, os, sys

args = sys.argv[1:]
log = os.environ["GH_LOG"]

if args[:2] == ["pr", "comment"]:
    body = None
    if "--body-file" in args:
        body = open(args[args.index("--body-file") + 1], encoding="utf-8").read()
    elif "--body" in args:
        body = args[args.index("--body") + 1]
    open(log, "a").write(json.dumps({"argv": args, "body": body}) + "\\n")
elif args[:2] == ["pr", "view"]:
    print(os.environ.get("GH_POST_SHA", ""))
elif args[:2] == ["workflow", "run"]:
    open(log, "a").write(json.dumps({"argv": args, "body": None}) + "\\n")
elif args[0] == "api":
    print(json.dumps([[]] if "--slurp" in args else []))
else:
    sys.stderr.write("unexpected gh call: %r\\n" % (args,))
    sys.exit(2)
'''


def step_named(name: str) -> dict:
    for step in yaml.safe_load(open(WORKFLOW))["jobs"]["fix"]["steps"]:
        if step.get("name") == name:
            return step
    raise AssertionError(f"agent-fix.yml has no step named {name!r}")


def substitute(run: str, values: dict) -> str:
    """The `${{ }}` substitutions Actions would make. An expression with no
    value is a hole in the harness, not a pass."""
    def repl(m):
        key = m.group(1).strip()
        if key not in values:
            raise AssertionError(f"harness has no value for ${{{{ {key} }}}}")
        return values[key]

    out = re.sub(r"\$\{\{([^}]*)\}\}", repl, run)
    assert "${{" not in out
    return out


def _executable(path: str, body: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    os.chmod(path, 0o755)


def _write(path: str, text: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


class FixRun:
    """One fix run's filesystem: the checkout, the stubs, and the two
    directories that matter — RUNNER_TEMP (this job's) and /tmp (the machine's,
    shared with every other job on the mini)."""

    def __init__(self, td: str, repo: str = REPO, pr: str = PR, sha: str = SHA,
                 card: str = CARD):
        self.td, self.repo, self.pr, self.sha, self.card = td, repo, pr, sha, card
        self.base = os.path.join(td, "runner-temp")
        self.legacy = os.path.join(td, "tmp")
        self.bin = os.path.join(td, "bin")
        for path in (self.base, self.legacy, self.bin):
            os.makedirs(path, exist_ok=True)
        self.log = os.path.join(td, "gh-calls.jsonl")
        self.linear_log = os.path.join(td, "linear-calls.jsonl")
        self.output = os.path.join(td, "github-output.txt")
        open(self.output, "a").close()
        _executable(os.path.join(self.bin, "gh"), GH_STUB)
        self.pipeline = os.path.join(td, ".bureau-pipeline")
        shutil.copytree(SCRIPTS, os.path.join(self.pipeline, "scripts"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(os.path.join(ROOT, "config"),
                        os.path.join(self.pipeline, "config"))
        _executable(
            os.path.join(self.pipeline, "scripts", "linear_ops.py"),
            "#!/usr/bin/env python3\nimport json, sys\n"
            f"open({self.linear_log!r}, 'a')"
            ".write(json.dumps(sys.argv[1:]) + '\\n')\n",
        )

    # ── the steps, executed ────────────────────────────────────────────────
    def _bash(self, run: str, env: dict):
        script = os.path.join(self.td, "step.sh")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write("set -eo pipefail\n" + run)
        return subprocess.run(
            ["bash", script], cwd=self.td,
            env=dict(
                os.environ,
                PATH=self.bin + os.pathsep + os.environ["PATH"],
                RUNNER_TEMP=self.base,
                GH_LOG=self.log,
                GH_POST_SHA=POST_SHA,
                GH_TOKEN="test",
                DISPATCH_TOKEN="workflow-token",
                LINEAR_API_KEY="test-key",
                GITHUB_OUTPUT=self.output,
                **env,
            ),
            capture_output=True, text=True,
        )

    def open_handoff_step(self):
        run = substitute(step_named("Open this run's fix handoff")["run"], {
            "github.repository": self.repo,
            "steps.pr.outputs.number": self.pr,
            "steps.pr.outputs.head_sha": self.sha,
        })
        # The machine-wide /tmp the step names, redirected into the sandbox:
        # the harness must never touch the real one.
        run = run.replace("/tmp", self.legacy)
        return self._bash(run, {
            "REPO": self.repo, "PR": self.pr, "HEAD_SHA": self.sha,
        })

    def report_step(self):
        run = substitute(step_named("Report")["run"], {
            "steps.pr.outputs.number": self.pr,
            "steps.pr.outputs.attempt": "2",
            "steps.pr.outputs.mode": "fix",
            "steps.claude.outputs.execution_file": os.path.join(self.td, "exec.json"),
            "github.repository": self.repo,
            "github.server_url": "https://github.com",
            "github.run_id": "34902813699",
        })
        run = run.replace("/tmp/", self.legacy + "/")
        return self._bash(run, {
            "REPO": self.repo, "CARD": self.card, "PRE_SHA": self.sha,
        })

    # ── what the run did ───────────────────────────────────────────────────
    def posted(self) -> list:
        if not os.path.exists(self.log):
            return []
        calls = [json.loads(line) for line in open(self.log).read().splitlines()]
        return [c["body"] for c in calls if c["argv"][:2] == ["pr", "comment"]]

    def card_notes(self) -> list:
        if not os.path.exists(self.linear_log):
            return []
        return [json.loads(line)
                for line in open(self.linear_log).read().splitlines()]

    # ── the fixtures a run finds on disk ───────────────────────────────────
    def open_handoff(self):
        return fix_handoff.open_handoff(
            self.base, self.repo, self.pr, self.sha, legacy_dir=self.legacy
        )

    def stamp_verdict(self, repo=None, pr=None, sha=None, body=VERDICT):
        path = _write(os.path.join(self.td, "critic-verdict.md"), body)
        return fix_handoff.stamp_verdict(
            self.base, repo or self.repo, pr or self.pr, sha or self.sha, path,
            _dir=fix_handoff.handoff_dir(self.base, self.repo, self.pr, self.sha),
        )

    def stale_legacy_blocker(self, text: str = OTHER_BLOCKER, age: int = 3600):
        """What the previous job on this mini left in /tmp."""
        path = _write(os.path.join(self.legacy, "fix-blocker.txt"), text)
        when = time.time() - age
        os.utime(path, (when, when))
        return path

    def concurrent_legacy_blocker(self, text: str = OTHER_BLOCKER):
        """A handoff that appears at the shared path DURING this run — another
        job on the same mini, or this agent writing a path it was not told to
        write. Either way its provenance cannot be established."""
        return _write(os.path.join(self.legacy, "fix-blocker.txt"), text)


class FixRunCase(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp(prefix="fix-handoff-")
        self.addCleanup(shutil.rmtree, self.td, True)
        self.run = FixRun(self.td)


class TheJobClearsWhatTheLastJobLeft(FixRunCase):
    """The prevention half: `/tmp` is not this run's."""

    def test_the_open_step_deletes_the_previous_jobs_handoff(self):
        stale = self.run.stale_legacy_blocker()
        proc = self.run.open_handoff_step()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(os.path.exists(stale),
                         "the previous job's /tmp/fix-blocker.txt survived")

    def test_the_open_step_publishes_this_runs_paths(self):
        proc = self.run.open_handoff_step()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        fields = dict(
            line.split("=", 1)
            for line in open(self.run.output).read().splitlines() if "=" in line
        )
        for kind in ("blocker", "refutation"):
            self.assertTrue(fields[kind].startswith(self.run.base + os.sep), fields)
            self.assertIn(PR, fields[kind], "the path is keyed to this PR")
        self.assertIn(SHA[:8], fields["key"])

    def test_a_cleared_handoff_leaves_the_report_step_with_nothing_to_quote(self):
        # End to end over the live incident: #2516's file, found by #2556's
        # run. Cleared at the top of the job, so the fix attempt reports its
        # own push and quotes nobody.
        self.run.stale_legacy_blocker()
        self.assertEqual(self.run.open_handoff_step().returncode, 0)
        self.run.stamp_verdict()
        proc = self.run.report_step()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        posted = self.run.posted()
        self.assertEqual(len(posted), 1, posted)
        self.assertNotIn(OTHER_BLOCKER, posted[0])
        self.assertNotIn(OTHER_PR, posted[0])
        self.assertIn("pushed", posted[0])


class TheReportStepRefusesAForeignContext(FixRunCase):
    """The assertion half: what cannot be attributed to this repo, PR and head
    is never posted."""

    def test_a_handoff_of_unknown_provenance_posts_nothing(self):
        self.run.open_handoff()
        self.run.stamp_verdict()
        self.run.concurrent_legacy_blocker()
        proc = self.run.report_step()
        self.assertEqual(self.run.posted(), [],
                         "another run's blocker reached this pull request")
        self.assertNotEqual(proc.returncode, 0,
                            "a refusal is loud — it cannot conclude success")
        self.assertIn("::error::", proc.stdout + proc.stderr)
        self.assertNotIn(OTHER_BLOCKER, proc.stdout + proc.stderr,
                         "the foreign body is never echoed (DRE-1996)")

    def test_a_refusal_touches_neither_the_card_nor_the_review(self):
        self.run.open_handoff()
        self.run.stamp_verdict()
        self.run.concurrent_legacy_blocker()
        self.run.report_step()
        self.assertEqual(self.run.card_notes(), [],
                         "a refusing run must not park another card's work")

    def test_a_verdict_stamped_for_another_repo_posts_nothing(self):
        self.run.open_handoff()
        self.run.stamp_verdict(repo=OTHER_REPO, pr=OTHER_PR, sha=OTHER_SHA)
        proc = self.run.report_step()
        self.assertEqual(self.run.posted(), [])
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn(OTHER_PR, proc.stdout + proc.stderr,
                      "the refusal names the context it refused")


class EveryCommentNamesWhatItAnswers(FixRunCase):
    """AC3: repo#PR, card and verdict sha on every comment the fix run posts to
    the pull request — so a repeat of this fault is visible at a glance."""

    def _assert_attributed(self, body: str):
        self.assertIn(f"{REPO}#{PR}", body)
        self.assertIn(CARD, body)
        self.assertIn(SHA[:8], body)

    def test_this_prs_own_blocker_is_posted_and_attributed(self):
        paths = self.run.open_handoff()
        _write(paths["blocker"], BLOCKER)
        self.run.stamp_verdict()
        proc = self.run.report_step()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        posted = self.run.posted()
        self.assertEqual(len(posted), 1, posted)
        self.assertIn(BLOCKER, posted[0])
        self._assert_attributed(posted[0])

    def test_the_push_marker_is_attributed_too(self):
        self.run.open_handoff()
        self.run.stamp_verdict()
        proc = self.run.report_step()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        posted = self.run.posted()
        self.assertEqual(len(posted), 1, posted)
        self._assert_attributed(posted[0])

    def test_the_refutation_is_attributed_too(self):
        paths = self.run.open_handoff()
        _write(paths["refutation"], "the check passes — here is the run id")
        self.run.stamp_verdict()
        proc = self.run.report_step()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        posted = self.run.posted()
        self.assertEqual(len(posted), 1, posted)
        self.assertIn("refuted the finding", posted[0])
        self._assert_attributed(posted[0])

    def test_no_comment_in_the_report_step_is_posted_unattributed(self):
        """Read at the source, so a comment added later cannot skip it: the
        step composes at least as many attributed bodies as it posts."""
        run = step_named("Report")["run"]
        self.assertGreaterEqual(
            run.count("$ANSWERS"), run.count("gh pr comment"),
            "a comment in the Report step names no repo, PR or verdict",
        )


if __name__ == "__main__":
    unittest.main()
