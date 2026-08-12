"""The critic's model attribution: scenario + adversarial (2026-08-12).

The advisory ladder moved Fable -> Sonnet 5, and the qa-review post step grew a
model line on the LINEAR copy of the verdict. Two things about that change are
load-bearing and neither is provable by grepping the YAML:

  * the PR copy (`/tmp/qa-comment.md`) must stay BYTE-IDENTICAL — merge-gate
    parses it, and a stray character in the verdict line is a merge that does
    not happen;
  * the Linear call must still no-op for a cardless PR.

So these tests EXECUTE the real run block out of qa-review.yml, with the
GitHub expressions substituted the way Actions would, against a temp filesystem
and a fake `gh`/`linear_ops.py` that record what they were asked to do.

Why this file exists at all: the previous round of this change shipped a test
that pinned the shell SPELLING of the card guard. Rewriting the guard as an
`if` block broke it while the contract held perfectly — a false red that
teaches people to edit tests. Behaviour is asserted here; shape is not.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
QA_REVIEW = ROOT / ".github" / "workflows" / "qa-review.yml"

VERDICT_BODY = "VERDICT: APPROVE\n\nLooks good. No findings.\n"
MODEL = "claude-sonnet-5"
WHY = "advisory ladder top; nothing skipped"


def _post_step_run() -> str:
    doc = yaml.safe_load(QA_REVIEW.read_text())
    for step in doc["jobs"]["review"]["steps"]:
        if step.get("id") == "post":
            return step["run"]
    raise AssertionError("qa-review.yml has no step with id 'post'")


def _resolve_expressions(run: str, *, model: str = MODEL, why: str = WHY) -> str:
    """Substitute the ${{ ... }} expressions Actions would resolve.

    Deliberately narrow: only the four this step uses. Anything else left in
    the block would be a silent no-op under bash, so the test asserts none
    survive before it runs.
    """
    mapping = {
        "github.repository": "dreadnought-foundry/portico",
        "github.run_id": "31350171859",
    }
    for expr, value in mapping.items():
        run = re.sub(r"\$\{\{\s*" + re.escape(expr) + r"\s*\}\}", value, run)
    return run


class _Harness:
    """A temp dir with a fake `gh` and a fake `linear_ops.py`, both of which
    append their argv to a log instead of talking to anything."""

    def __init__(self, td: Path):
        self.td = td
        self.log = td / "calls.log"
        bin_dir = td / "bin"
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        gh.write_text(
            "#!/usr/bin/env bash\n"
            f'printf "gh %s\\n" "$*" >> {self.log}\n'
            "exit 0\n"
        )
        gh.chmod(0o755)
        # Each Linear call lands in its OWN file. The verdict body is
        # multi-line, so appending it to a shared log and splitting on newlines
        # silently truncated it at the first line — the harness's own bug,
        # caught because a test asserted on content rather than on a call count.
        self.linear_dir = td / "linear-calls"
        self.linear_dir.mkdir()
        ops = td / ".bureau-pipeline" / "scripts"
        ops.mkdir(parents=True)
        (ops / "linear_ops.py").write_text(
            "import sys, json, pathlib\n"
            f"d = pathlib.Path({str(self.linear_dir)!r})\n"
            "n = len(list(d.iterdir()))\n"
            "(d / f'{n:03d}.json').write_text(json.dumps(sys.argv[1:]))\n"
        )
        (ops / "sync_review_state.py").write_text("pass\n")

    def calls(self) -> list[str]:
        return self.log.read_text().splitlines() if self.log.exists() else []

    def linear_bodies(self) -> list[str]:
        import json

        out = []
        for f in sorted(self.linear_dir.iterdir()):
            argv = json.loads(f.read_text())
            if len(argv) >= 3 and argv[0] == "comment":
                out.append(argv[2])
        return out


def _run_post(td: Path, *, card: str, real: str = "true",
              model: str = MODEL, why: str = WHY) -> subprocess.CompletedProcess:
    harness = _Harness(td)
    run = _resolve_expressions(_post_step_run(), model=model, why=why)
    assert "${{" not in run, "an unresolved GitHub expression reached the shell"
    (td / "tmp").mkdir(exist_ok=True)
    (td / "qa-verdict.md").write_text(VERDICT_BODY)
    script = td / "post.sh"
    # The block reads /tmp/qa-verdict.md and writes /tmp/qa-comment.md. Point
    # both at the temp dir so a test never touches the real /tmp.
    run = run.replace("/tmp/", str(td) + "/")
    script.write_text("set -euo pipefail\n" + run)
    env = dict(os.environ)
    env["PATH"] = f"{td / 'bin'}:{env['PATH']}"
    # MODEL_ID / MODEL_WHY arrive as ENV, exactly as the step declares them —
    # that indirection is the thing under test, so the harness must not
    # shortcut it by pasting the values into the script.
    env.update({
        "CARD": card, "REAL": real, "PR": "132",
        "REVIEWED_SHA": "a" * 40, "CONTENT_ID": "c" * 64,
        "MODEL_ID": model, "MODEL_WHY": why,
    })
    return subprocess.run(
        ["bash", str(script)], cwd=td, env=env,
        capture_output=True, text=True,
    ), harness


class PrCopyIsUntouchedTest(unittest.TestCase):
    """merge-gate parses the PR comment. The model line must not reach it."""

    def test_the_pr_comment_is_byte_identical_with_and_without_a_card(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            proc_a, _ = _run_post(Path(a), card="DRE-2199")
            proc_b, _ = _run_post(Path(b), card="")
            self.assertEqual(proc_a.returncode, 0, proc_a.stderr)
            self.assertEqual(proc_b.returncode, 0, proc_b.stderr)
            self.assertEqual(
                (Path(a) / "qa-comment.md").read_bytes(),
                (Path(b) / "qa-comment.md").read_bytes(),
                "the card path changed the PR comment — merge-gate parses this",
            )

    def test_the_model_line_never_reaches_the_pr_comment(self):
        with tempfile.TemporaryDirectory() as td:
            proc, _ = _run_post(Path(td), card="DRE-2199")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            pr_copy = (Path(td) / "qa-comment.md").read_text()
            self.assertNotIn("🧠", pr_copy)
            self.assertNotIn(MODEL, pr_copy)

    def test_the_verdict_line_still_leads_the_pr_comment(self):
        # merge-gate reads the FIRST line for `VERDICT: <X> @<sha>`.
        with tempfile.TemporaryDirectory() as td:
            proc, _ = _run_post(Path(td), card="DRE-2199")
            first = (Path(td) / "qa-comment.md").read_text().splitlines()[0]
            self.assertTrue(first.startswith("🔎 QA Critic — VERDICT: APPROVE @"))
            self.assertIn("c" * 64, first, "the content id was dropped")


class LinearCopyCarriesTheModelTest(unittest.TestCase):
    def test_the_linear_copy_carries_the_model_and_the_reason(self):
        with tempfile.TemporaryDirectory() as td:
            proc, harness = _run_post(Path(td), card="DRE-2199")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            bodies = harness.linear_bodies()
            self.assertEqual(len(bodies), 1, f"expected one Linear comment: {bodies}")
            self.assertIn(MODEL, bodies[0])
            self.assertIn(WHY, bodies[0])
            self.assertIn("VERDICT: APPROVE", bodies[0],
                          "the Linear copy lost the verdict itself")

    def test_a_cardless_pr_posts_nothing_to_linear(self):
        # THE guard. A dependabot PR has no card; this must no-op, not crash.
        with tempfile.TemporaryDirectory() as td:
            proc, harness = _run_post(Path(td), card="")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(harness.linear_bodies(), [],
                             "a cardless PR reached Linear")

    def test_a_crashed_critic_still_posts_the_neutral_status(self):
        with tempfile.TemporaryDirectory() as td:
            proc, harness = _run_post(Path(td), card="DRE-2199", real="false")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            body = (Path(td) / "qa-comment.md").read_text()
            self.assertIn("could not run", body)
            self.assertNotIn("VERDICT: APPROVE", body,
                             "a crashed critic must never look like an approval")


class AdversarialNoteTest(unittest.TestCase):
    """The model note is interpolated into a double-quoted shell string. The
    selection note is machine-generated and single-line, so this is safe today
    — these tests are what makes that a CHECKED property rather than a belief."""

    def test_a_note_containing_a_double_quote_does_not_break_the_step(self):
        hostile = 'skipped claude-fable-5 ("excluded"); nothing else'
        with tempfile.TemporaryDirectory() as td:
            proc, harness = _run_post(Path(td), card="DRE-2199", why=hostile)
            self.assertEqual(
                proc.returncode, 0,
                f"a quote in the selection note broke the post step:\n{proc.stderr}",
            )
            self.assertTrue(harness.linear_bodies(), "the comment was lost")

    def test_a_note_attempting_command_substitution_is_not_executed(self):
        # If this ever executes, the file appears and the assertion fires.
        with tempfile.TemporaryDirectory() as td:
            marker = Path(td) / "PWNED"
            hostile = f"$(touch {marker}) `touch {marker}`"
            proc, _ = _run_post(Path(td), card="DRE-2199", why=hostile)
            self.assertFalse(
                marker.exists(),
                "the selection note was executed as shell — it is DATA",
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_the_selection_note_really_is_one_line(self):
        # The property the interpolation leans on, asserted against the real
        # generator rather than assumed.
        sys.path.insert(0, str(ROOT / "scripts"))
        import model_fallback as mf

        mf.clear_availability_cache()
        for role in ("critic", "engineer", "verifier"):
            for probe in (lambda m: True, lambda m: False,
                          lambda m: m != MODEL):
                mf.clear_availability_cache()
                note = mf.selection_note(
                    mf.select_with_reasons(role, probe=probe)
                )
                with self.subTest(role=role, note=note[:40]):
                    self.assertEqual(len(note.splitlines()), 1)
                    self.assertNotIn('"', note,
                                     "a quote in the note reaches a shell string")


if __name__ == "__main__":
    unittest.main()
