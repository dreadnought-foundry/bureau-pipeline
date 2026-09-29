"""The Verifier's scope gate, driven through its real signals (DRE-4389).

On 2026-09-20 agent-bureau PR #2652 changed what a One River row renders —
the liveness dot's pulse, files under `console/web/src/` — and the Verifier
skipped it: "Card DRE-4382 is single-system, non-UI — Verifier does not run."
The gate's only UI signal was a `**Design:**` line, and `standards/
card-quality.md` FORBIDS that line on a card with no new screen to build to. A
card fixing what an existing component renders rightly carries none, and is
rightly UI work. The gate asked "is there a design reference?" and read the
answer as "is this UI?".

The fix gives the gate a UI signal that does not depend on a design reference:
the card's own role labels, on a diff that touches the frontend bucket.

Every test here hands the gate a changed-file list and a card (labels +
description) and asserts in-scope or skip. None restates a glob. The last
class runs the workflow step's own shell, so the wiring is exercised rather
than grepped.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import verify_scope  # noqa: E402

# PR #2652's files, as DRE-4382's own record names them, and DRE-4382's labels
# read off Linear on 2026-09-29. It carried no **Design:** line.
PR_2652_FILES = [
    "console/web/src/components/oneriver/RiverRow.tsx",
    "console/web/src/components/oneriver/RiverRow.test.tsx",
    "console/web/src/dev/oneRiverFixture.ts",
    "console/web/src/dev/oneRiverFixture.test.ts",
]
DRE_4382_LABELS = [
    "hand-built", "agent:frontend", "repo:agent-bureau", "size:XS",
    "initiative:bureau", "ux", "needs-human",
]
DRE_4382_DESCRIPTION = (
    "A row waiting at a gate stops pulsing when its card has nothing to judge "
    "by.\n\n## Acceptance criteria\n\n- [ ] The row pulses while the gate works."
)


def card(description: str, labels: list[str]) -> dict:
    return {"description": description, "labels": labels}


class TheRegression(unittest.TestCase):
    """PR #2652 plus DRE-4382's labels: the miss this card exists for."""

    def test_pr_2652_on_dre_4382_is_in_scope(self):
        d = verify_scope.decide(PR_2652_FILES, card(DRE_4382_DESCRIPTION, DRE_4382_LABELS))
        self.assertTrue(d.in_scope, d)
        self.assertTrue(d.ui_role)

    def test_the_old_signals_alone_would_have_skipped_it(self):
        # Proves it is the NEW signal that flips the decision: neither the
        # Design line nor the bucket count says anything on this PR.
        d = verify_scope.decide(PR_2652_FILES, card(DRE_4382_DESCRIPTION, DRE_4382_LABELS))
        self.assertFalse(d.design)
        self.assertFalse(d.multi)
        self.assertEqual(d.buckets, {"frontend"})


class TheUiRoleSignal(unittest.TestCase):
    def test_agent_frontend_alone_brings_a_frontend_diff_in(self):
        d = verify_scope.decide(["web/src/App.tsx"], card("Fix spacing.", ["agent:frontend"]))
        self.assertTrue(d.in_scope)

    def test_ux_alone_brings_a_frontend_diff_in(self):
        d = verify_scope.decide(["web/src/App.tsx"], card("Fix focus ring.", ["ux"]))
        self.assertTrue(d.in_scope)

    def test_the_label_match_ignores_case(self):
        d = verify_scope.decide(["web/src/App.tsx"], card("Fix.", ["Agent:Frontend"]))
        self.assertTrue(d.in_scope)

    def test_a_ui_label_on_a_diff_with_no_frontend_file_skips(self):
        # The label says who built it, the diff says what changed. A UI-role
        # card whose diff never reaches the frontend has nothing to render.
        d = verify_scope.decide(["scripts/tidy.py"], card("Tidy.", ["agent:frontend", "ux"]))
        self.assertFalse(d.in_scope, d)

    def test_a_non_ui_role_on_a_frontend_diff_skips(self):
        d = verify_scope.decide(["web/src/App.tsx"], card("Bump.", ["agent:engineer"]))
        self.assertFalse(d.in_scope, d)


class TheExistingSignalsAreNotNarrowed(unittest.TestCase):
    def test_a_design_line_is_still_in_scope(self):
        body = "Build the board.\n\n**Design:** console/design/images/screens/desktop/board.png\n"
        d = verify_scope.decide(["web/src/Board.tsx"], card(body, ["agent:engineer"]))
        self.assertTrue(d.in_scope)
        self.assertTrue(d.design)

    def test_an_indented_design_line_is_still_read(self):
        body = "Intro\n   **design:** screens/board.png\n"
        d = verify_scope.decide(["README.md"], card(body, []))
        self.assertTrue(d.in_scope)

    def test_a_design_word_mid_sentence_is_not_a_design_line(self):
        body = "The **Design:** line is forbidden here, see the standard.\n"
        d = verify_scope.decide(["scripts/x.py"], card("Prose: " + body, []))
        self.assertFalse(d.design)

    def test_two_buckets_are_still_in_scope_with_no_card_at_all(self):
        d = verify_scope.decide(["backend/app/api/routes.py", "web/src/App.tsx"], None)
        self.assertTrue(d.in_scope)
        self.assertTrue(d.multi)

    def test_two_buckets_are_still_in_scope_on_a_non_ui_card(self):
        d = verify_scope.decide(
            ["backend/app/schema.py", "alembic/versions/0042_add.py"],
            card("Add a column.", ["agent:engineer"]),
        )
        self.assertTrue(d.in_scope)


class NonUiSingleBucketStillSkips(unittest.TestCase):
    def test_a_pytest_only_change_skips(self):
        d = verify_scope.decide(
            ["tests/test_reconcile.py", "tests/test_merge_gate.py"],
            card("Cover the gate.", ["agent:engineer", "repo:bureau-pipeline"]),
        )
        self.assertFalse(d.in_scope, d)

    def test_an_infra_only_change_skips(self):
        d = verify_scope.decide(
            [".github/workflows/verify.yml", "infra/app.py"],
            card("Tune CI.", ["agent:devops"]),
        )
        self.assertFalse(d.in_scope, d)


class AnUnreadableCardStillSkips(unittest.TestCase):
    """No card id, Linear unreachable, empty description: SKIP, never block."""

    def test_no_card_on_a_frontend_only_diff_skips(self):
        self.assertFalse(verify_scope.decide(PR_2652_FILES, None).in_scope)

    def test_no_card_id_reads_nothing(self):
        calls = []
        self.assertIsNone(verify_scope.read_card("", gql=lambda *a: calls.append(a)))
        self.assertEqual(calls, [])

    def test_linear_unreachable_reads_as_no_card(self):
        def unreachable(*_a):
            raise OSError("Linear is down")
        self.assertIsNone(verify_scope.read_card("DRE-4382", gql=unreachable))

    def test_an_empty_description_reads_as_no_card_even_with_ui_labels(self):
        def empty(*_a):
            return {"issue": {"description": "", "labels": {"nodes": [{"name": "ux"}]}}}
        got = verify_scope.read_card("DRE-4382", gql=empty)
        self.assertIsNone(got)
        self.assertFalse(verify_scope.decide(PR_2652_FILES, got).in_scope)

    def test_a_missing_issue_reads_as_no_card(self):
        self.assertIsNone(verify_scope.read_card("DRE-1", gql=lambda *a: {"issue": None}))

    def test_a_readable_card_returns_its_labels_and_description(self):
        def ok(*_a):
            return {"issue": {"description": DRE_4382_DESCRIPTION,
                              "labels": {"nodes": [{"name": n} for n in DRE_4382_LABELS]}}}
        got = verify_scope.read_card("DRE-4382", gql=ok)
        self.assertEqual(got["labels"], DRE_4382_LABELS)
        self.assertTrue(verify_scope.decide(PR_2652_FILES, got).in_scope)


def _scope_step() -> dict:
    wf = yaml.safe_load((ROOT / ".github/workflows/verify.yml").read_text())
    steps = wf["jobs"]["verify"]["steps"]
    return next(s for s in steps if s.get("id") == "scope")


class TheWorkflowStepRunsTheRealGate(unittest.TestCase):
    """Execute the `scope` step's own shell against a stubbed `gh`.

    The pipeline checkout (`.bureau-pipeline`) points at this repo, `gh pr diff`
    prints a fixed file list, and `$GITHUB_OUTPUT` is read back — so what is
    asserted is what the later steps' `steps.scope.outputs.in_scope` would be.
    """

    def _run(self, changed: list[str], *, card_id: str = "", scripts: Path | None = None) -> dict:
        step = _scope_step()
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            pipeline = work / ".bureau-pipeline"
            if scripts is None:
                pipeline.symlink_to(ROOT)
            else:
                pipeline.mkdir()
                (pipeline / "scripts").symlink_to(scripts)
            bin_dir = work / "bin"
            bin_dir.mkdir()
            listing = work / "changed.txt"
            listing.write_text("".join(f + "\n" for f in changed))
            gh = bin_dir / "gh"
            gh.write_text(f"#!/bin/sh\ncat '{listing}'\n")
            gh.chmod(0o755)
            out = work / "github_output"
            out.write_text("")
            env = {
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "HOME": os.environ.get("HOME", tmp),
                "GITHUB_OUTPUT": str(out),
                # Inside Actions a missing key is an error, never a borrowed
                # operator key — so a card id here means "Linear unreachable".
                "GITHUB_ACTIONS": "true",
                "PR": "1",
                "CARD": card_id,
            }
            subprocess.run(["bash", "-c", step["run"]], cwd=work, env=env,
                           check=False, capture_output=True, text=True, timeout=60)
            pairs = [l.split("=", 1) for l in out.read_text().splitlines() if "=" in l]
            return dict(pairs)

    def test_the_step_keeps_its_degrade_never_block_contract(self):
        self.assertTrue(_scope_step().get("continue-on-error"))

    def test_the_step_decides_through_the_script(self):
        self.assertIn("verify_scope.py", _scope_step()["run"])

    def test_a_multi_system_diff_with_no_card_is_in_scope(self):
        got = self._run(["backend/app/api/x.py", "web/src/App.tsx"])
        self.assertEqual(got.get("in_scope"), "true")

    def test_a_frontend_only_diff_with_no_card_id_skips(self):
        got = self._run(PR_2652_FILES)
        self.assertEqual(got.get("in_scope"), "false")

    def test_linear_unreachable_on_a_frontend_only_diff_skips(self):
        got = self._run(PR_2652_FILES, card_id="DRE-4382")
        self.assertEqual(got.get("in_scope"), "false")

    def test_a_gate_that_crashes_skips_rather_than_blocks(self):
        with tempfile.TemporaryDirectory() as broken:
            (Path(broken) / "verify_scope.py").write_text("raise SystemExit(3)\n")
            got = self._run(["backend/app/api/x.py", "web/src/App.tsx"], scripts=Path(broken))
        self.assertEqual(got.get("in_scope"), "false")


if __name__ == "__main__":
    unittest.main()
