"""DRE-8721: the critic judges every UI pull request in a repo whose design
system exists against the shared parts, and no other pull request.

`qa-review.yml`'s `Assemble critic context` step appends two blocks to the
critic's context after the standards and the brief: the parts review
(`scripts/parts_review.py`, DRE-8719) of the pull request's diff against the
checkout's catalog, and the CEO's answer to the critic's design question
(`scripts/design_answer.py context`, DRE-8722). `briefs/critic.md` tells the
critic what to do with both.

The behavioral tests here execute the step's real `run:` block, taken from the
workflow, the way `tests/test_critic_context_root.py` does: a runner laid out
after the move, a checkout `.` copied from DRE-8719's fixture trees, and a
fake `gh` on PATH that serves (or refuses) the pull request's diff.
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
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
QA_REVIEW = ROOT / ".github" / "workflows" / "qa-review.yml"
BRIEF = ROOT / "briefs" / "critic.md"
PROOF_REACH = ROOT / "config" / "proof-reach.json"
FIXTURES = ROOT / "tests" / "fixtures" / "parts-review"
sys.path.insert(0, str(ROOT / "scripts"))

import console_escalation  # noqa: E402
import design_answer  # noqa: E402
import design_parts  # noqa: E402
import parts_review  # noqa: E402
import proof_reach  # noqa: E402
import step_shell  # noqa: E402

IN_TREE = ".bureau-pipeline"
CONTEXT = IN_TREE + "/agent-context.md"

NOTICE = "console/web/src/components/molecules/Notice.tsx"
PAGE = "console/web/src/pages/initiatives/InitiativesPage.tsx"
COPY_FINDING = (
    "FINDING copy (suggested verdict: REQUEST_CHANGES) — uses-the-shared-parts: "
    f"`Notice` in `{PAGE}` is a copy of the shared part `Notice` — use `{NOTICE}` "
    "and delete the copy.")

SHADOW_NONE = ("DESIGN ANSWER: none — a shadow replay judges the change, "
               "not the answers it got")
NO_CARD_NONE = "DESIGN ANSWER: none — this pull request's branch names no card"

#: The fake `gh`: `gh pr diff <n>` prints $FAKE_GH_DIFF, or fails when it is
#: unset — and every call is logged, so a test can say which were made.
FAKE_GH = """#!/usr/bin/env bash
echo "$*" >> "$FAKE_GH_LOG"
if [ "$1 $2" = "pr diff" ] && [ -n "$FAKE_GH_DIFF" ]; then
  cat "$FAKE_GH_DIFF"
  exit 0
fi
echo "gh: HTTP 406: the diff exceeded the maximum number of lines (20000)" >&2
exit 1
"""

#: A stub `design_answer.py` that answers `context` as an answered part would.
ANSWERED_STUB = '''#!/usr/bin/env python3
import sys
print("DESIGN ANSWER: answered — `StatusBanner` at "
      "`console/web/src/components/molecules/StatusBanner.tsx`, the CEO, "
      "via the console, at 2026-11-01 09:14 PT")
print("> Keep it — it is the banner we want on every status page.")
print("design-answer: " + (sys.argv[2] if len(sys.argv) > 2 else "") +
      ": stubbed", file=sys.stderr)
'''

#: Appended to a copy of the real `spoken_thread.py`: the one Linear read the
#: real `design_answer.py` makes fails loudly, and leaves a mark, if called.
READ_FORBIDDEN = '''

def read(card):  # DRE-8721 test stub
    import os
    with open(os.environ["LINEAR_READ_MARK"], "a") as fh:
        fh.write(card + "\\n")
    raise AssertionError("the Linear read was called for " + repr(card))
'''


def _review_steps() -> list[dict]:
    doc = yaml.safe_load(step_shell.workflow_source(QA_REVIEW))
    return doc["jobs"]["review"]["steps"]


def _ctx() -> dict:
    return next(s for s in _review_steps() if s.get("id") == "ctx")


def _lay_out(td: Path, tree: str, *, replace: dict | None = None,
             patch_read: bool = False) -> tuple[Path, Path]:
    """(workspace, pipeline_dir): the pipeline at $RUNNER_TEMP/bureau-pipeline
    with each script linked one by one, so a test can swap one; the working
    tree a copy of a DRE-8719 fixture tree."""
    workspace = td / "work" / "repo" / "repo"
    pipeline_dir = td / "work" / "_temp" / "bureau-pipeline"
    (pipeline_dir / "scripts").mkdir(parents=True)
    for content in ("standards", "briefs", "config"):
        (pipeline_dir / content).symlink_to(ROOT / content)
    for script in (ROOT / "scripts").iterdir():
        if script.is_file():
            (pipeline_dir / "scripts" / script.name).symlink_to(script)
    for name, text in (replace or {}).items():
        target = pipeline_dir / "scripts" / name
        target.unlink()
        target.write_text(text)
    if patch_read:
        target = pipeline_dir / "scripts" / "spoken_thread.py"
        target.unlink()
        target.write_text((ROOT / "scripts" / "spoken_thread.py").read_text()
                          + READ_FORBIDDEN)
    shutil.copytree(FIXTURES / tree, workspace, symlinks=True)
    stub = workspace / IN_TREE / ".github" / "actions" / "install-claude-code"
    stub.mkdir(parents=True)
    (stub / "action.yml").write_text("name: install\n")
    return workspace, pipeline_dir


def run_ctx(td: Path, tree: str, *, diff: str | None, card: str = "",
            shadow: bool = False, diff_file: str = "", replace=None,
            patch_read: bool = False):
    """Execute the step's `run:` block as written. Returns (proc, workspace,
    pipeline_dir, gh calls, the Linear read mark)."""
    workspace, pipeline_dir = _lay_out(td, tree, replace=replace,
                                       patch_read=patch_read)
    bin_dir = td / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(FAKE_GH)
    gh.chmod(0o755)
    gh_log, mark = td / "gh.log", td / "linear-read.mark"
    shell = step_shell.step_shell(_ctx())
    script = td / "ctx.sh"
    # `bash -e` is what Actions gives a `run:` step with no `shell:`.
    script.write_text("set -e\n" + shell)
    env = dict(os.environ)
    env.update({
        "PATH": f"{bin_dir}{os.pathsep}{env.get('PATH', '')}",
        "PIPELINE_DIR": str(pipeline_dir),
        "GITHUB_WORKSPACE": str(workspace),
        "RUNNER_TEMP": str(pipeline_dir.parent),
        "FAKE_GH_LOG": str(gh_log),
        "FAKE_GH_DIFF": str(FIXTURES / diff) if diff else "",
        "LINEAR_READ_MARK": str(mark),
        "GH_TOKEN": "reader-token",
        "PR": "4242",
        "CARD": card,
        "DIFF_FILE": diff_file,
        "SHADOW": "true" if shadow else "false",
        "LINEAR_API_KEY": "lin_api_test",
        "LINEAR_API_KEY_FALLBACK": "lin_api_test",
    })
    proc = subprocess.run(["bash", str(script)], cwd=workspace, env=env,
                          capture_output=True, text=True, timeout=120)
    calls = gh_log.read_text().splitlines() if gh_log.exists() else []
    return proc, workspace, pipeline_dir, calls, mark


def _block(text: str, opener: str) -> list[str]:
    """The lines from the first line opening with `opener` to the next blank."""
    lines = text.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith(opener))
    out = []
    for ln in lines[start:]:
        if not ln.strip():
            break
        out.append(ln)
    return out


class TheStepRunsTheReviewTest(unittest.TestCase):

    def _ok(self, proc):
        self.assertEqual(proc.returncode, 0,
                         f"the step failed:\n{proc.stdout}\n{proc.stderr}")

    def _contexts(self, workspace: Path, pipeline_dir: Path) -> str:
        in_tree = (workspace / CONTEXT).read_text()
        durable = (pipeline_dir / "agent-context.md").read_text()
        self.assertEqual(durable, in_tree,
                         "the retry's durable copy differs from the context")
        return in_tree

    def test_a_copy_of_a_shared_part_is_named_in_the_context_and_the_log(self):
        with tempfile.TemporaryDirectory() as raw:
            proc, ws, pd, calls, _ = run_ctx(Path(raw), "tree-notice-copy",
                                             diff="notice-copy.diff")
            self._ok(proc)
            text = self._contexts(ws, pd)
            # The standards and brief still come first.
            self.assertIn("===== BEGIN briefs/critic.md =====", text)
            self.assertIn(
                "PARTS REVIEW: applies — 1 UI file(s) changed in a repo whose "
                "design system exists", text)
            self.assertIn(f"- Notice (molecules) — ships at {NOTICE}", text)
            self.assertIn(COPY_FINDING, text)
            self.assertIn("pr diff 4242", calls)
            log = proc.stdout + proc.stderr
            tagged = [ln for ln in log.splitlines()
                      if ln.startswith(parts_review.TAG)]
            self.assertEqual(tagged, [
                "parts-review: applies — 1 UI file(s) changed in a repo whose "
                "design system exists, 1 finding(s)"])
            self.assertIn(
                "PARTS REVIEW: applies — 1 UI file(s) changed in a repo whose "
                "design system exists", proc.stdout.splitlines())

    def test_the_default_branch_checkout_gives_the_same_review(self):
        # A workflow_dispatch re-review (the hand-back, the sweep, a
        # refutation) holds the default branch, not the pull request's head.
        blocks = {}
        for tree in ("tree-notice-copy", "tree-base"):
            with tempfile.TemporaryDirectory() as raw:
                proc, ws, pd, _, _ = run_ctx(Path(raw), tree,
                                             diff="notice-copy.diff")
                self._ok(proc)
                blocks[tree] = _block(self._contexts(ws, pd), "PARTS REVIEW:")
        self.assertIn(COPY_FINDING, blocks["tree-base"])
        self.assertEqual(blocks["tree-base"], blocks["tree-notice-copy"])

    def test_no_ui_file_and_no_design_system_do_not_apply(self):
        for tree, diff, why in (
                ("tree-base", "no-ui.diff", "the diff adds or changes no UI file"),
                ("tree-no-design-system", "notice-copy.diff",
                 "the repo's design system does not exist")):
            with tempfile.TemporaryDirectory() as raw:
                proc, ws, pd, _, _ = run_ctx(Path(raw), tree, diff=diff)
                self._ok(proc)
                text = self._contexts(ws, pd)
                self.assertRegex(
                    text, rf"(?m)^PARTS REVIEW: does not apply — {re.escape(why)}")
                self.assertNotIn("FINDING ", text)
                self.assertIn("PARTS REVIEW: does not apply", proc.stdout)

    def test_a_diff_that_cannot_be_read_is_unknown_and_the_step_succeeds(self):
        with tempfile.TemporaryDirectory() as raw:
            proc, ws, pd, calls, _ = run_ctx(Path(raw), "tree-notice-copy",
                                             diff=None)
            self._ok(proc)
            self.assertIn("pr diff 4242", calls)
            text = self._contexts(ws, pd)
            block = _block(text, "PARTS REVIEW:")
            self.assertRegex(block[0], r"^PARTS REVIEW: UNKNOWN — .+ is empty$")
            self.assertEqual(block[1:], [
                f"{design_parts.CATALOG} 1 part(s) from .github/bureau/design-system.json",
                f"- Notice (molecules) — ships at {NOTICE}"])
            self.assertIn("parts-review: UNKNOWN — ", proc.stderr)

    def test_a_shadow_replay_reads_its_own_diff_file_and_no_answers(self):
        with tempfile.TemporaryDirectory() as raw:
            proc, ws, pd, calls, mark = run_ctx(
                Path(raw), "tree-notice-copy", diff=None, card="DRE-8721",
                shadow=True, diff_file=str(FIXTURES / "notice-copy.diff"),
                patch_read=True)
            self._ok(proc)
            self.assertFalse([c for c in calls if c.startswith("pr diff")],
                             "a shadow replay asked gh for today's head")
            text = self._contexts(ws, pd)
            self.assertIn(COPY_FINDING, text)
            self.assertIn(SHADOW_NONE, text.splitlines())
            self.assertFalse(mark.exists(), "a shadow replay read the card's answers")
            self.assertIn(SHADOW_NONE, proc.stdout.splitlines())

    def test_the_ceos_answer_reaches_the_context_and_the_log(self):
        with tempfile.TemporaryDirectory() as raw:
            proc, ws, pd, _, _ = run_ctx(
                Path(raw), "tree-notice-copy", diff="notice-copy.diff",
                card="DRE-8721", replace={"design_answer.py": ANSWERED_STUB})
            self._ok(proc)
            text = self._contexts(ws, pd)
            answered = ("DESIGN ANSWER: answered — `StatusBanner` at "
                        "`console/web/src/components/molecules/StatusBanner.tsx`, "
                        "the CEO, via the console, at 2026-11-01 09:14 PT")
            self.assertIn(answered + "\n> Keep it — it is the banner we want "
                          "on every status page.", text)
            self.assertIn(answered, proc.stdout.splitlines())
            self.assertIn("design-answer: DRE-8721: stubbed", proc.stderr)

    def test_a_branch_that_names_no_card_reads_nothing(self):
        # The real design_answer.py, its one Linear read made to fail.
        with tempfile.TemporaryDirectory() as raw:
            proc, ws, pd, _, mark = run_ctx(
                Path(raw), "tree-notice-copy", diff="notice-copy.diff",
                card="", patch_read=True)
            self._ok(proc)
            self.assertFalse(mark.exists(), "the Linear read was called")
            text = self._contexts(ws, pd)
            self.assertIn(NO_CARD_NONE, text.splitlines())
            self.assertIn(NO_CARD_NONE, proc.stdout.splitlines())

    def test_the_stub_really_forbids_the_read(self):
        # The guard above is only as good as the stub: with a card named the
        # real script reaches the read, and the mark is left.
        with tempfile.TemporaryDirectory() as raw:
            proc, ws, _, _, mark = run_ctx(
                Path(raw), "tree-notice-copy", diff="notice-copy.diff",
                card="DRE-8721", patch_read=True)
            self._ok(proc)
            self.assertTrue(mark.exists())
            self.assertIn("DESIGN ANSWER: UNKNOWN — ",
                          (ws / CONTEXT).read_text())


class TheStepsEnvTest(unittest.TestCase):

    def test_the_step_carries_what_it_reads(self):
        env = _ctx().get("env") or {}
        cardsnap = next(s for s in _review_steps() if s.get("id") == "cardsnap")
        self.assertEqual(env.get("GH_TOKEN"), "${{ steps.reader.outputs.token }}")
        self.assertEqual(env.get("PR"), "${{ steps.pr.outputs.number }}")
        self.assertEqual(env.get("CARD"), "${{ steps.pr.outputs.card }}")
        self.assertEqual(env.get("DIFF_FILE"), "${{ steps.pr.outputs.diff_file }}")
        self.assertEqual(env.get("SHADOW"), "${{ inputs.shadow }}")
        for key in ("LINEAR_API_KEY", "LINEAR_API_KEY_FALLBACK"):
            self.assertEqual(env.get(key), cardsnap["env"][key], key)

    def test_neither_critic_agent_step_holds_a_linear_key(self):
        agents = [s for s in _review_steps()
                  if str(s.get("uses") or "").startswith("anthropics/claude-code-action")]
        self.assertEqual(sorted(s["name"] for s in agents),
                         ["Critic review", "Critic review (retry)"])
        for step in agents:
            text = yaml.safe_dump(step, width=10**6)
            self.assertNotIn("LINEAR_API_KEY", text, step["name"])

    def test_the_step_runs_the_review_and_the_answer_and_echoes_both(self):
        shell = step_shell.step_shell(_ctx())
        self.assertIn('gh pr diff "$PR"', shell)
        self.assertIn('scripts/parts_review.py review --diff', shell)
        self.assertIn('--repo-root .', shell)
        self.assertIn('scripts/design_answer.py context "$CARD"', shell)
        self.assertIn("grep -E '^PARTS REVIEW:'", shell)
        self.assertIn("grep -E '^DESIGN ANSWER:'", shell)
        self.assertIn(SHADOW_NONE, shell)
        # Both blocks land before the durable copy the retry restores from.
        self.assertLess(shell.index("parts_review.py"),
                        shell.index('cp .bureau-pipeline/agent-context.md'))
        self.assertLess(shell.index("design_answer.py"),
                        shell.index('cp .bureau-pipeline/agent-context.md'))


class TheBriefTest(unittest.TestCase):
    """`briefs/critic.md` § UI code in a repo whose design system exists."""

    @classmethod
    def setUpClass(cls):
        text = BRIEF.read_text(encoding="utf-8")
        head = "## UI code in a repo whose design system exists"
        assert head in text.splitlines(), "the section is missing"
        rest = text.split(head, 1)[1]
        cls.section = rest.split("\n## ", 1)[0]
        cls.flat = " ".join(cls.section.split())
        # A fenced block nested in a list item is indented; its lines are
        # read as the critic writes them.
        cls.lines = [ln.strip() for ln in cls.section.splitlines()]

    def test_it_points_at_the_standard(self):
        self.assertIn('`standards/design-system.md` § "UI code in a repo whose '
                      'design system exists"', self.flat)
        self.assertIn("`PARTS REVIEW:`", self.flat)

    def test_copies_look_alikes_and_near_new_parts_block(self):
        for kind in ("`copy`", "`look-alike`", "`new-part`"):
            self.assertIn(kind, self.flat)
        self.assertIn("blocking REQUEST_CHANGES", self.flat)
        self.assertIn("naming that part and its path", self.flat)
        self.assertIn("worded exactly as the review gives it", self.flat)

    def test_the_a_b_c_order(self):
        positions = [self.flat.index(f"**({x})") for x in "abc"]
        self.assertEqual(positions, sorted(positions))

    def test_the_provisional_line_with_no_green_light_question(self):
        line = (f"{parts_review.PROVISIONAL_MARK} `<Name>` at `<path>` — "
                "reasoning: <one sentence>")
        self.assertIn(line, self.lines)
        self.assertIn("no Green Light question", self.flat)
        # The line the brief shows is the line the gate reads.
        self.assertEqual(parts_review.provisional_records(line),
                         [("<Name>", "<path>", "<one sentence>")])

    def test_the_design_question_block_at_most_one_per_verdict(self):
        block = [f"{design_answer.QUESTION_MARK} `<Name>` at `<path>`",
                 f"{console_escalation.FINDING_PREFIX} <one line>",
                 f"{console_escalation.QUESTION_PREFIX} <one line>",
                 f"{console_escalation.RECOMMENDATION_PREFIX} <one line>"]
        self.assertIn("\n".join(block), "\n".join(self.lines))
        self.assertIn("at most one", self.flat)

    def test_the_ceos_answer_settles_it_and_asked_repeats_it(self):
        self.assertIn("`DESIGN ANSWER: answered`", self.flat)
        self.assertIn("`DESIGN ANSWER: asked`", self.flat)
        self.assertIn("Never raise the block for that part again on this "
                      "pull request", self.flat)
        self.assertIn("repeat the same block word for word", self.flat)

    def test_the_three_parts_check_lines_and_unknown(self):
        for line in ("Parts check: applied — <n> finding(s)",
                     "Parts check: applied by hand — the review read UNKNOWN "
                     "(<reason>), <n> finding(s)",
                     "Parts check: not applied — <reason>"):
            self.assertIn(line, self.lines)
        self.assertIn("**When the line says `UNKNOWN`", self.flat)
        self.assertIn("`gh pr diff` refuses for a very large pull request",
                      self.flat)

    def test_does_not_apply_means_nothing_applies(self):
        self.assertIn("When the line says `does not apply`, raise no parts "
                      "finding and write no `Parts check:` line", self.flat)

    def test_the_section_reaches_the_critic(self):
        out = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "assemble_context.py"),
             "assemble", "critic", "--root", str(ROOT)],
            capture_output=True, text=True, check=True).stdout
        self.assertIn("## UI code in a repo whose design system exists", out)


class TheProofReachRowsTest(unittest.TestCase):

    def test_both_tags_are_declared_with_their_printers(self):
        rows = {e["tag"]: e for e in json.loads(PROOF_REACH.read_text())["log_lines"]}
        for tag, printer, module in (
                ("design-parts:", "scripts/design_parts.py", design_parts),
                ("parts-review:", "scripts/parts_review.py", parts_review)):
            self.assertIn(tag, rows)
            self.assertEqual(rows[tag]["printed_by"], printer)
            self.assertEqual(rows[tag]["constant"], "TAG")
            self.assertEqual(module.TAG, tag)

    def test_the_declaration_still_checks_clean(self):
        self.assertEqual(proof_reach.check(ROOT), [])


if __name__ == "__main__":
    unittest.main()
