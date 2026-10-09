"""Regression pin (DRE-6467): a sweep that is red on a STANDING CARD DEFECT is
the alarm it is meant to be — the medic neither reruns it nor spends a
diagnosis agent on it.

The sweep goes red on purpose. `scripts/reconcile.py` keeps a third ledger,
`_stale_defects`, for "nothing failed, a card is wrong" — today an epic's
prose-blocker defect standing two hours (DRE-2676) — and a red run IS that
ledger's escalation. But the medic could not tell that red from a crash: the
class was `normal`, so `retry` ran a second full sweep on attempt 1 and
`diagnose` spent up to twenty minutes of Claude on attempt 2, every fifteen
minutes, for an alarm working as designed.

The class is read off the sweep's own closing line, line-anchored and on a
Reconcile run only:

  * zero write failures, zero read failures (or no read clause — the
    `--promote-only` exit) and one or more unfixed card defects is
    `standing_defect`;
  * one write or read failure beside the defects is a real failure and stays
    `normal`, so it keeps the retry and the diagnosis;
  * the same line quoted in an Agent Task log (this card's own text) is not
    the class — or a genuine failure would be silently swallowed.

Exactly the shape DRE-2488 and DRE-2923 gave their classes: no rerun, no
diagnosis, one ::notice::, and the medic's own run ends green. The Reconcile
run itself stays red.
"""

import ast
import contextlib
import io
import os
import sys
import unittest

import yaml

sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts"))
)

import medic_classify  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
WORKFLOW = os.path.join(ROOT, ".github", "workflows", "medic.yml")
RECONCILE = os.path.join(ROOT, "scripts", "reconcile.py")

# A full sweep red on two standing defects and nothing else.
STANDING_LOG = os.path.join(FIXTURES, "reconcile-standing-defect.log")
# The same defects with one failed write beside them — a real failure.
BESIDE_WRITE_LOG = os.path.join(FIXTURES, "reconcile-defect-beside-write-failure.log")
# An agent-task log that QUOTES the closing line (this card's text).
QUOTED_LOG = os.path.join(FIXTURES, "agent-task-quotes-standing-defect.log")

# The `--promote-only` exit has no read clause.
PROMOTE_ONLY_LOG = (
    "sweep\tSweep\t2026-10-09T16:50:02.1234567Z Run python3 "
    ".bureau-pipeline/scripts/reconcile.py --promote-only\n"
    "sweep\tSweep\t2026-10-09T16:50:21.2234567Z ERROR: DRE-4101: a prose "
    "blocker declaring DRE-4099 with no blockedBy relation has stood for 2.5h\n"
    "sweep\tSweep\t2026-10-09T16:50:22.3234567Z reconcile: 0 write failure(s), "
    "1 unfixed card defect(s) — see ERROR lines above\n"
    "sweep\tSweep\t2026-10-09T16:50:22.4234567Z ##[error]Process completed "
    "with exit code 1.\n"
)

RECONCILE_NAME = "Reconcile"


def _fixture(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def _closing_lines_reconcile_prints(write: int, read: int, defects: int) -> list[str]:
    """Every `sys.exit` line in `reconcile.main` that names the unfixed card
    defects, rendered with the given counts — read off the REAL source, so the
    sweep and the medic cannot drift apart without this file going red."""
    with open(RECONCILE, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    main = next(
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main"
    )
    values = {
        "len(_write_failures)": str(write),
        "len(_read_failures)": str(read),
        "len(_stale_defects)": str(defects),
    }
    lines = []
    for node in ast.walk(main):
        if not (
            isinstance(node, ast.Call)
            and ast.unparse(node.func) == "sys.exit"
            and node.args
            and isinstance(node.args[0], ast.JoinedStr)
        ):
            continue
        parts = []
        for piece in node.args[0].values:
            if isinstance(piece, ast.Constant):
                parts.append(piece.value)
            else:
                parts.append(values[ast.unparse(piece.value)])
        line = "".join(parts)
        if "unfixed card defect" in line:
            lines.append(line)
    return lines


# ── the classifier: the standing_defect class ───────────────────────────────
class StandingDefectClassifierTest(unittest.TestCase):
    def test_a_sweep_red_only_on_standing_defects_is_the_class(self):
        log = _fixture(STANDING_LOG)
        # The fixture really is that run: two ledger lines, then the close.
        self.assertEqual(log.count("ERROR: "), 2)
        self.assertIn(
            "reconcile: 0 write / 0 read failure(s), 2 unfixed card defect(s) "
            "— see ERROR lines above",
            log,
        )
        self.assertTrue(medic_classify.is_standing_defect(RECONCILE_NAME, log))
        self.assertEqual(
            medic_classify.classify(RECONCILE_NAME, log), "standing_defect"
        )

    def test_the_promote_only_close_has_no_read_clause_and_is_the_class(self):
        self.assertNotIn(" read ", PROMOTE_ONLY_LOG.splitlines()[2])
        self.assertTrue(
            medic_classify.is_standing_defect(RECONCILE_NAME, PROMOTE_ONLY_LOG)
        )
        self.assertEqual(
            medic_classify.classify(RECONCILE_NAME, PROMOTE_ONLY_LOG),
            "standing_defect",
        )

    def test_the_reusable_and_a_stub_name_both_count_as_reconcile(self):
        log = _fixture(STANDING_LOG)
        for name in ("Reconcile (reusable)", "Reconcile", "reconcile sweep"):
            self.assertEqual(medic_classify.classify(name, log), "standing_defect")

    def test_both_lines_reconcile_really_prints_classify(self):
        """Both exit sites, rendered from reconcile.py itself: zero failures
        and defects standing is the class; one failure beside them is not."""
        standing = _closing_lines_reconcile_prints(0, 0, 2)
        self.assertEqual(len(standing), 2, standing)
        for line in standing:
            self.assertTrue(
                medic_classify.is_standing_defect(RECONCILE_NAME, line + "\n"), line
            )
        for write, read in ((1, 0), (0, 1)):
            for line in _closing_lines_reconcile_prints(write, read, 2):
                if f"{read} read" not in line and read:
                    continue  # the promote-only line reads no failures at all
                self.assertFalse(
                    medic_classify.is_standing_defect(RECONCILE_NAME, line + "\n"),
                    line,
                )

    # ── the negatives ───────────────────────────────────────────────────────
    def test_a_write_failure_beside_the_defects_stays_normal(self):
        log = _fixture(BESIDE_WRITE_LOG)
        self.assertIn(
            "1 write / 0 read failure(s), 2 unfixed card defect(s)", log
        )
        self.assertFalse(medic_classify.is_standing_defect(RECONCILE_NAME, log))
        self.assertEqual(medic_classify.classify(RECONCILE_NAME, log), "normal")

    def test_a_read_failure_beside_the_defects_stays_normal(self):
        line = (
            "reconcile: 0 write / 3 read failure(s), 2 unfixed card defect(s) "
            "— see ERROR lines above\n"
        )
        self.assertEqual(medic_classify.classify(RECONCILE_NAME, line), "normal")

    def test_zero_defects_is_not_the_class(self):
        line = (
            "reconcile: 0 write / 0 read failure(s), 0 unfixed card defect(s) "
            "— see ERROR lines above\n"
        )
        self.assertEqual(medic_classify.classify(RECONCILE_NAME, line), "normal")

    def test_an_agent_task_log_quoting_the_line_is_not_the_class(self):
        log = _fixture(QUOTED_LOG)
        # The fixture really does carry the closing line, verbatim.
        self.assertIn(
            "reconcile: 0 write / 0 read failure(s), 2 unfixed card defect(s) "
            "— see ERROR lines above",
            log,
        )
        self.assertFalse(
            medic_classify.is_standing_defect("Agent Task (reusable)", log)
        )
        self.assertEqual(
            medic_classify.classify("Agent Task (reusable)", log), "normal"
        )

    def test_the_phrase_must_sit_on_one_line(self):
        split = (
            "reconcile: 0 write / 0 read failure(s),\n"
            "2 unfixed card defect(s) — see ERROR lines above\n"
        )
        self.assertFalse(medic_classify.is_standing_defect(RECONCILE_NAME, split))

    def test_a_crash_ahead_of_it_keeps_its_own_class(self):
        """Every class ahead of this one is a crash, and a sweep that crashed
        never prints the closing line — but if both were in one log, the
        crash is the cause the next reader needs."""
        log = _fixture(STANDING_LOG) + _fixture(
            os.path.join(FIXTURES, "reconcile-linear-ratelimited-2026-09-01.log")
        )
        self.assertEqual(
            medic_classify.classify("Reconcile (reusable)", log), "linear_ratelimited"
        )

    def test_cli_prints_the_class_and_leaves_the_dre_1921_gate_false(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            rc = medic_classify.main(["Reconcile (reusable)", STANDING_LOG])
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("infra_crash=false", out)
        self.assertIn("class=standing_defect", out)


# ── medic.yml wiring: no retry, no diagnosis, one notice, ends green ────────
def _medic():
    with open(WORKFLOW) as f:
        return yaml.safe_load(f)


class MedicStandingDefectWiringTest(unittest.TestCase):
    def setUp(self):
        self.jobs = _medic()["jobs"]

    def test_retry_is_gated_off_a_standing_defect(self):
        self.assertIn(
            "needs.classify.outputs.class != 'standing_defect'",
            self.jobs["retry"]["if"],
        )

    def test_diagnose_is_gated_off_a_standing_defect(self):
        self.assertIn(
            "needs.classify.outputs.class != 'standing_defect'",
            self.jobs["diagnose"]["if"],
        )

    def test_no_job_reruns_or_diagnoses_without_excluding_it(self):
        for name, job in self.jobs.items():
            body = yaml.safe_dump(job)
            if "gh run rerun" in body or "claude-code-action" in body:
                self.assertIn(
                    "class != 'standing_defect'",
                    job.get("if", ""),
                    f"{name} reruns or diagnoses without excluding a standing defect",
                )

    def test_standing_defect_job_notices_and_ends_green(self):
        job = self.jobs["standing_defect"]
        self.assertIn("needs.classify.outputs.class == 'standing_defect'", job["if"])
        self.assertEqual(job.get("needs"), "classify")
        # No attempt gate: a notice is a per-run annotation.
        self.assertNotIn("run_attempt", job["if"])
        steps = job["steps"]
        self.assertEqual(len(steps), 1)
        run = steps[0]["run"]
        for phrase in ("::notice::", "not retrying", "not diagnosing", "a person must act"):
            self.assertIn(phrase, run)
        body = yaml.safe_dump(job)
        self.assertNotIn("gh run rerun", body)
        self.assertNotIn("claude-code-action", body)
        self.assertNotIn("anthropics/", body)
        self.assertNotIn("exit 1", body)


if __name__ == "__main__":
    unittest.main()
