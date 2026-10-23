"""The record pull request's body, rebuilt from the record (DRE-7813).

A proof record's pull request body carries the card's criterion table, copied
in when the run opens the pull request. A resumed run amends the record and
pushes, and opens no second pull request — so before this nothing rewrote the
body, and the critic read the first run's table beside an amended record. On
agent-bureau #3589 (DRE-6663, 2026-10-23) the critic sent a PASS record back
four rounds running on "The PR body is stale and contradicts the record", and
the operator rewrote it by hand.

`scripts/proof_pr_body.py` renders the body from the record at the pushed
head, and proof-task.yml's `Refresh the record pull request's body` step
writes it with `gh pr edit --body-file` after every run, first or resumed.
These tests render from fixture records, drive `refresh` against a stub `gh`,
and read the step off the workflow file.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import proof_pr_body  # noqa: E402
import step_shell  # noqa: E402 — a moved step's shell, where it now lives

PROOF_TASK = ROOT / ".github" / "workflows" / "proof-task.yml"
BRIEF = ROOT / "briefs" / "proof.md"
STEP = "Refresh the record pull request's body"
ACTION = "anthropics/claude-code-action"

CARD = "DRE-6663"
CARD_URL = "https://linear.app/dreadnoughtfoundry/issue/DRE-6663/proof-one-groom-removal"
REPO = "acme/widgets"
BRANCH = "agent/DRE-6663-proof-record"
RECORD_PATH = "architecture/proof-records/DRE-6663.md"

FIRST_TABLE = [
    "| Criterion | Result |",
    "| --- | --- |",
    "| The removal is stored with its reason | Not observed. waiting for the operator's press: Not doing on one groom card |",
    "| The console shows the reason | Met. `GET /groom/41` returned `reason: duplicate` |",
]
AMENDED_TABLE = [
    "| Criterion | Result |",
    "| --- | --- |",
    "| The removal is stored with its reason | Met. sandbox press made, removal 9182 stored with `reason: duplicate` |",
    "| The console shows the reason | Met. `GET /groom/41` returned `reason: duplicate` |",
]


def _record(status: str, table: list) -> str:
    return "\n".join([
        "# PROOF record — DRE-6663: one groom removal made in the console is stored with its reason (epic DRE-6600)",
        "",
        status,
        "",
        "## How this was recorded",
        "",
        "Run 123, 2026-10-23 11:33 PT.",
        "",
        "```",
        "| Criterion | Result |",
        "| --- | --- |",
        "| a fenced table that is not the record's | Met. |",
        "```",
        "",
        "## Identities",
        "",
        "- github: read-only proof identity",
        "",
        "## Criteria",
        "",
        *table,
        "",
        "Closing notes after the table.",
        "",
    ])


FIRST = _record("**Status: PARTIAL.**", FIRST_TABLE)
AMENDED = _record("**Status: PASS.**", AMENDED_TABLE)


def _table_in(body: str) -> list:
    """The body's lines that are table rows, in order."""
    return [l for l in body.splitlines() if l.lstrip().startswith("|")]


class RenderTest(unittest.TestCase):
    """The body in the brief's shape, every part of it read off the record."""

    def test_first_three_lines_status_and_the_records_own_table(self):
        body = proof_pr_body.render(CARD, CARD_URL, FIRST)
        lines = body.splitlines()
        self.assertEqual([f"Proof record for {CARD}", "What's new: none", CARD_URL], lines[:3])
        self.assertIn("**Status: PARTIAL.**", lines)
        self.assertEqual(FIRST_TABLE, _table_in(body),
                         "the body's table is the record's criterion table, line for line")

    def test_a_fenced_table_is_not_the_records(self):
        body = proof_pr_body.render(CARD, CARD_URL, FIRST)
        self.assertNotIn("a fenced table that is not the record's", body)

    def test_status_comes_before_the_table(self):
        lines = proof_pr_body.render(CARD, CARD_URL, FIRST).splitlines()
        self.assertLess(lines.index("**Status: PARTIAL.**"), lines.index(FIRST_TABLE[0]))

    def test_an_amended_record_renders_its_amended_rows(self):
        first = proof_pr_body.render(CARD, CARD_URL, FIRST)
        second = proof_pr_body.render(CARD, CARD_URL, AMENDED)
        self.assertEqual(AMENDED_TABLE, _table_in(second))
        self.assertNotIn(FIRST_TABLE[2], second, "the first run's row is gone")
        self.assertIn("**Status: PASS.**", second.splitlines())
        self.assertNotIn("**Status: PARTIAL.**", second)
        self.assertNotEqual(first, second)

    def test_a_record_with_no_criterion_table_is_refused(self):
        with self.assertRaises(ValueError):
            proof_pr_body.render(CARD, CARD_URL, "# PROOF record\n\n**Status: PASS.**\n")

    def test_a_record_with_no_status_line_is_refused(self):
        with self.assertRaises(ValueError):
            proof_pr_body.render(CARD, CARD_URL, "\n".join(FIRST_TABLE) + "\n")


class FakeGh:
    """`gh`, as `proof_pr_body.refresh` calls it: a list of argv in, stdout
    out, RuntimeError for a refused call."""

    def __init__(self, *, prs=None, record=FIRST, files=None, refuse=()):
        self.prs = [{"number": 3589, "headRefOid": "abc123"}] if prs is None else prs
        self.record = record
        self.files = ([{"path": RECORD_PATH, "changeType": "ADDED"}]
                      if files is None else files)
        self.refuse = refuse
        self.calls = []
        self.bodies = []

    def __call__(self, args):
        self.calls.append(list(args))
        verb = " ".join(args[:2])
        if verb in self.refuse:
            raise RuntimeError(f"HTTP 502: {verb} refused")
        if verb == "pr list":
            return json.dumps(self.prs)
        if verb == "pr view":
            return json.dumps({"files": self.files})
        if args[0] == "api":
            content = base64.b64encode(self.record.encode()).decode()
            return json.dumps({"encoding": "base64", "content": content})
        if verb == "pr edit":
            self.bodies.append(Path(args[args.index("--body-file") + 1]).read_text())
            return ""
        raise AssertionError(f"unexpected gh call {args}")


class RefreshTest(unittest.TestCase):
    def _refresh(self, gh) -> int:
        return proof_pr_body.refresh(REPO, CARD, CARD_URL, gh=gh)

    def test_writes_the_rendered_body_to_the_open_pull_request(self):
        gh = FakeGh(record=AMENDED)
        self.assertEqual(0, self._refresh(gh))
        self.assertEqual([proof_pr_body.render(CARD, CARD_URL, AMENDED)], gh.bodies)
        edit = next(c for c in gh.calls if c[:2] == ["pr", "edit"])
        self.assertEqual(["pr", "edit", "3589", "--repo", REPO], edit[:5])

    def test_reads_the_open_pull_request_of_the_record_branch(self):
        gh = FakeGh()
        self._refresh(gh)
        listing = next(c for c in gh.calls if c[:2] == ["pr", "list"])
        self.assertIn(BRANCH, listing)
        self.assertEqual("open", listing[listing.index("--state") + 1])

    def test_reads_the_record_at_the_pushed_head(self):
        gh = FakeGh()
        self._refresh(gh)
        api = next(c for c in gh.calls if c[0] == "api")
        self.assertIn(f"contents/{RECORD_PATH}?ref=abc123", api[1])

    def test_a_second_push_rewrites_the_body_with_the_amended_rows(self):
        gh = FakeGh(record=FIRST)
        self._refresh(gh)
        gh.record = AMENDED
        gh.prs = [{"number": 3589, "headRefOid": "def456"}]
        self._refresh(gh)
        self.assertEqual(2, len(gh.bodies))
        self.assertEqual(FIRST_TABLE, _table_in(gh.bodies[0]))
        self.assertEqual(AMENDED_TABLE, _table_in(gh.bodies[1]))

    def test_no_open_pull_request_is_nothing_to_refresh(self):
        gh = FakeGh(prs=[])
        self.assertEqual(0, self._refresh(gh))
        self.assertEqual([], gh.bodies)

    def test_a_refused_edit_fails(self):
        self.assertEqual(1, self._refresh(FakeGh(refuse=("pr edit",))))

    def test_a_refused_lookup_fails(self):
        gh = FakeGh(refuse=("pr list",))
        self.assertEqual(1, self._refresh(gh))
        self.assertEqual([], gh.bodies)

    def test_a_record_that_cannot_be_found_fails(self):
        gh = FakeGh(files=[{"path": "scripts/x.py", "changeType": "ADDED"}])
        self.assertEqual(1, self._refresh(gh))
        self.assertEqual([], gh.bodies)

    def test_a_record_with_no_table_fails(self):
        gh = FakeGh(record="# PROOF record\n\n**Status: PASS.**\n")
        self.assertEqual(1, self._refresh(gh))
        self.assertEqual([], gh.bodies)


def _steps() -> list:
    jobs = yaml.safe_load(PROOF_TASK.read_text())["jobs"]
    return next(iter(jobs.values()))["steps"]


def _index(name: str) -> int:
    return next(i for i, s in enumerate(_steps()) if s.get("name") == name)


class WorkflowStepTest(unittest.TestCase):
    """Read off proof-task.yml: the step runs after every record push, on a
    first run and a resumed one alike, and its failure turns the run red."""

    def setUp(self):
        self.steps = _steps()
        self.at = _index(STEP)
        self.step = self.steps[self.at]

    def test_runs_after_every_agent_attempt_and_the_push_rescue(self):
        agents = [i for i, s in enumerate(self.steps)
                  if str(s.get("uses") or "").split("@")[0] == ACTION]
        self.assertEqual(3, len(agents))
        self.assertGreater(self.at, max(agents))
        self.assertGreater(self.at, _index("Push rescue"),
                           "a branch the rescue delivered is a push too")

    def test_runs_whatever_the_run_was(self):
        # always(): after a failed attempt too. Nothing in the condition reads
        # whether the branch was new, so a first run and a resumed one alike.
        self.assertEqual("always() && !inputs.shadow", self.step.get("if"))

    def test_its_failure_turns_the_run_red(self):
        self.assertNotIn("continue-on-error", self.step)
        script = step_shell.step_shell(self.step)
        self.assertIn("proof_pr_body.py refresh", script)
        self.assertNotIn("|| true", script)
        self.assertNotIn("${{", script, "the step reads env, never interpolates")

    def test_the_step_exits_red_when_the_refresh_fails(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        scripts = tmp / ".bureau-pipeline" / "scripts"
        scripts.mkdir(parents=True)
        (scripts / "proof_pr_body.py").write_text(
            "import sys\nprint('::error::stub refused', file=sys.stderr)\nsys.exit(1)\n")
        env = {"PATH": os.environ["PATH"], **{k: "x" for k in self.step["env"]}}
        done = subprocess.run(["bash", "-e", "-c", step_shell.step_shell(self.step)],
                              cwd=tmp, env=env, capture_output=True, text=True, timeout=60)
        self.assertNotEqual(0, done.returncode)

    def test_writes_with_a_fresh_token_and_names_the_card(self):
        env = self.step.get("env") or {}
        self.assertIn("steps.pushtoken.outputs.token", env.get("GH_TOKEN", ""))
        self.assertEqual("${{ github.event.client_payload.identifier }}", env.get("CARD"))
        self.assertEqual("${{ github.event.client_payload.url }}", env.get("CARD_URL"))
        self.assertEqual("${{ github.repository }}", env.get("REPO"))


class BriefTest(unittest.TestCase):
    def test_the_brief_says_the_body_is_rebuilt_on_every_push(self):
        text = " ".join(BRIEF.read_text().split())
        self.assertIn("rebuilds the pull request's body from the record after every push", text)
        self.assertIn("never edit the body yourself", text)


if __name__ == "__main__":
    unittest.main()
