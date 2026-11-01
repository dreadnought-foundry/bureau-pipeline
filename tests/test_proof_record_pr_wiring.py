"""A proof run pushes its record; a workflow step decides the pull request (DRE-8441).

On 2026-10-29 the CEO decided that a proof's record pull request opens only
when its record can merge (DRE-8437 holds that decision in
`scripts/proof_record_pr.py`). Until this card the proof agent opened the pull
request itself, from step 3 of its prompt, whatever its rows said, and the
push rescue opened one for any record branch that had none. This file pins the
wiring that turns the rule on for new runs:

  * all three agent attempts' prompts tell the agent to push the record branch
    and never to run `gh pr create`, `gh pr reopen` or `gh pr close`, and no
    prompt still tells it to open the pull request;
  * the push rescue on a proof run pushes and opens nothing (`--push-only`):
    the rescue's own `gh pr create` would otherwise open every record the agent
    left for the decision step, and the step would close it again;
  * `briefs/proof.md`, which the agent reads beside its prompt, and the
    standard's DRE-5920 paragraph no longer say the run opens the record's pull
    request, and both say it pushes the record and the pull request opens only
    when the record can merge.

The `record_pr` step itself — its gate, its order, and its script run against
a fake `proof_record_pr.py` — is pinned in `tests/test_proof_task_workflow.py`.

Run: cd bureau-pipeline && python3 -m pytest tests/test_proof_record_pr_wiring.py -v
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import push_rescue  # noqa: E402
import step_shell  # noqa: E402

PROOF_TASK = ROOT / ".github" / "workflows" / "proof-task.yml"
BRIEF = ROOT / "briefs" / "proof.md"
STANDARD = ROOT / "standards" / "card-quality.md"
ACTION = "anthropics/claude-code-action"

DECIDE_STEP = "Open the record pull request only when it can merge"

#: The phrase both documents carry once this card lands.
NEW_PHRASE = "the pull request opens only when the record can merge"

#: What the documents said before, one list per document.
OLD_BRIEF = (
    "pushing the record branch and opening the pull request",
    "Open the pull request only if none is open",
    "push the record and open its pull request",
    "open its pull request, and let the gate hold it",
    "leave the push and the pull request to the workflow's push-rescue step",
    "⏳ 5/5 PR opened",
)
OLD_STANDARD = ("opens the record as a pull request",)


def _flat(text: str) -> str:
    return " ".join(text.split())


def _steps() -> list[dict]:
    doc = yaml.safe_load(PROOF_TASK.read_text(encoding="utf-8"))
    return next(iter(doc["jobs"].values()))["steps"]


def _prompts() -> list[str]:
    return [s["with"]["prompt"] for s in _steps()
            if str(s.get("uses") or "").split("@")[0] == ACTION]


def _proof_section(text: str) -> str:
    found = re.search(r"^## Every epic ends with a proof card.*?(?=^## )", text, re.S | re.M)
    assert found, "card-quality.md must still carry the proof-card section"
    return found.group(0)


class AgentPromptTest(unittest.TestCase):
    """Step 3 in all three attempts: push, and never touch a pull request."""

    def setUp(self):
        self.prompts = _prompts()
        self.assertEqual(3, len(self.prompts), "an attempt and two retries")

    def test_every_attempt_forbids_each_pull_request_command(self):
        for i, prompt in enumerate(self.prompts):
            flat = _flat(prompt)
            for command in ("gh pr create", "gh pr reopen", "gh pr close"):
                with self.subTest(attempt=i, command=command):
                    self.assertIn(f"`{command}`", flat)
            with self.subTest(attempt=i):
                self.assertRegex(flat, r"never (?:run )?`gh pr create`, `gh pr reopen` or `gh pr close`")

    def test_no_attempt_still_tells_the_agent_to_open_the_pull_request(self):
        for i, prompt in enumerate(self.prompts):
            flat = _flat(prompt)
            with self.subTest(attempt=i):
                self.assertNotRegex(flat, r"(?i)\bopen the pull request with\b")
                self.assertNotIn("only if none is open for the branch", flat)
                self.assertNotIn("PR opened", flat)
                self.assertNotIn("AFTER the pull request is open", flat)
                self.assertNotIn("push the record branch and open the pull request", flat)

    def test_every_attempt_pushes_and_names_the_step_that_decides(self):
        for i, prompt in enumerate(self.prompts):
            flat = _flat(prompt)
            with self.subTest(attempt=i):
                self.assertIn("Push the branch with GH_TOKEN", flat)
                self.assertIn(f"`{DECIDE_STEP}`", flat)
                self.assertIn("`⏳ 5/5 record pushed`", flat)

    def test_the_escalation_comes_after_the_push(self):
        for i, prompt in enumerate(self.prompts):
            with self.subTest(attempt=i):
                self.assertIn("AFTER the record is pushed", _flat(prompt))


class PushRescuePushesOnlyTest(unittest.TestCase):
    """The rescue delivers the record branch and leaves the pull request to
    the decision step."""

    def test_the_proof_run_passes_push_only(self):
        step = next(s for s in _steps() if s.get("name") == "Push rescue")
        flat = re.sub(r"\\\n\s*", " ", step_shell.step_shell(step))
        self.assertIn("push_rescue.py rescue", flat)
        self.assertIn("--push-only", flat)

    def _rescue(self, *, push_only: bool, pushed_already: bool):
        calls = []
        local = "a" * 40

        def run(argv, cwd=None, env=None):
            calls.append(list(argv))
            if "for-each-ref" in argv:
                return 0, "agent/DRE-9-proof-record\n", ""
            if "rev-parse" in argv:
                return 0, local + "\n", ""
            if "rev-list" in argv:
                return 0, "2\n", ""
            if "ls-remote" in argv:
                return 0, (f"{local}\trefs/heads/agent/DRE-9-proof-record\n"
                           if pushed_already else ""), ""
            if argv[:3] == ["gh", "pr", "list"]:
                return 0, "[]", ""
            if argv[:3] == ["gh", "pr", "create"]:
                return 0, "https://github.com/o/r/pull/5\n", ""
            return 0, "", ""

        out = push_rescue.rescue("DRE-9", "o/r", "ghs_fresh", run=run,
                                 log=lambda _m: None, stop_notes=(),
                                 push_only=push_only)
        return out, calls

    def test_push_only_never_opens_a_pull_request(self):
        for pushed_already in (True, False):
            with self.subTest(pushed_already=pushed_already):
                out, calls = self._rescue(push_only=True, pushed_already=pushed_already)
                self.assertFalse([c for c in calls if c[:2] == ["gh", "pr"]], calls)
                self.assertFalse(out.pr_opened)
                self.assertEqual("", out.pr_url)
                self.assertEqual(not pushed_already, out.pushed)

    def test_without_it_the_build_rescue_still_opens_one(self):
        out, calls = self._rescue(push_only=False, pushed_already=True)
        self.assertTrue([c for c in calls if c[:3] == ["gh", "pr", "create"]], calls)
        self.assertTrue(out.pr_opened)

    def test_the_cli_takes_the_flag(self):
        self.assertIn("--push-only", Path(push_rescue.__file__).read_text(encoding="utf-8"))


class BriefTest(unittest.TestCase):
    """briefs/proof.md, which the agent reads beside its prompt."""

    def setUp(self):
        self.flat = _flat(BRIEF.read_text(encoding="utf-8"))

    def test_it_no_longer_says_the_run_opens_the_pull_request(self):
        for phrase in OLD_BRIEF:
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase, self.flat)

    def test_it_says_the_run_pushes_and_the_pull_request_opens_only_when_it_can_merge(self):
        self.assertIn(NEW_PHRASE, self.flat)
        self.assertIn("`GH_TOKEN`.** The worker App token, used for exactly one thing: "
                      "pushing the record branch", self.flat)
        self.assertIn("⏳ 5/5 record pushed", self.flat)

    def test_it_says_what_happens_while_the_proof_waits(self):
        self.assertIn("While the proof waits, the record stays on its branch and the "
                      "card's hold says what it waits on", self.flat)
        self.assertIn("A run never opens, closes or reopens a pull request itself", self.flat)


class StandardTest(unittest.TestCase):
    """standards/card-quality.md's DRE-5920 paragraph says what the brief says."""

    def setUp(self):
        self.section = _flat(_proof_section(STANDARD.read_text(encoding="utf-8")))

    def test_it_no_longer_says_the_run_opens_the_record_as_a_pull_request(self):
        for phrase in OLD_STANDARD:
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase, self.section)

    def test_it_says_the_run_pushes_the_record_and_when_the_pull_request_opens(self):
        self.assertIn("pushes the record to `agent/DRE-<n>-proof-record`", self.section)
        self.assertIn(NEW_PHRASE, self.section)


if __name__ == "__main__":
    unittest.main()
