"""DRE-8170: the proof run's escalation comment goes through the redaction seam.

`report_proof_result.sh` builds `proof-escalation-comment.md` from the agent's
own escalation file, which quotes criteria and what the run saw, so it can
carry a person's name. When `NAME_GUARD_LIVE` is `true` the file goes through
`name_redact.py redact --in-place` before `linear_ops.py comment` posts it.
When the seam cannot redact it — the roster is unreadable — the comment posted
is the fixed sentence and the run URL, and the park still happens: a decision
is never lost over a redaction. Not live, the file posts unchanged.

Each case runs `bash scripts/report_proof_result.sh` by path in a throwaway
working directory whose `.bureau-pipeline/scripts/` is the real `scripts/`
with three files stubbed: `linear_ops.py` (logs each call), `card_pr.py`
(answers an open record pull request) and `name_redact.py` (told what to answer
by `STUB_REDACT`). No fixture holds a name: the stub's one "form" is the token
`FORM-ALPHA`.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "report_proof_result.sh"

CARD = "DRE-9002"
RUN_URL = "https://github.com/acme-org/widgets/actions/runs/4242"
PR_URL = "https://github.com/acme-org/widgets/pull/77"
UNREDACTED = "🔬 the escalation could not be redacted (name guard: roster unreadable) — read the run"

ESCALATION = (
    "Finding: the run read the page FORM-ALPHA owns and saw the old banner.\n"
    "Question: should the criterion be reworded to the banner the page shows now?\n"
    "Recommendation: reword it.\n"
)

NAME_REDACT_STUB = r'''#!/usr/bin/env python3
import json, os, sys
argv = sys.argv[1:]
with open(os.environ["STUB_LOG"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps(["name_redact", *argv]) + "\n")
if os.environ.get("STUB_REDACT") == "roster":
    print("name-guard: refused — roster unreadable: the roster could not be fetched", file=sys.stderr)
    sys.exit(2)
path = argv[argv.index("--file") + 1]
text = open(path, encoding="utf-8").read()
open(path, "w", encoding="utf-8").write(text.replace("FORM-ALPHA", "[a person]"))
'''

LINEAR_OPS_STUB = r'''#!/usr/bin/env python3
import json, os, sys
with open(os.environ["STUB_LOG"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps(["linear_ops", *sys.argv[1:]]) + "\n")
if sys.argv[1] == "advance":
    print(f"advanced {sys.argv[2]} to {sys.argv[3]}")
'''

CARD_PR_STUB = f'''#!/usr/bin/env python3
print("OPEN\\t{PR_URL}")
'''


class _ProofRun(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.td = Path(self._td.name)

    def run_report(self, *, live, redact="clean", owner="acme-org"):
        td = self.td
        scripts = td / ".bureau-pipeline" / "scripts"
        scripts.mkdir(parents=True)
        stubs = {"name_redact.py": NAME_REDACT_STUB,
                 "linear_ops.py": LINEAR_OPS_STUB,
                 "card_pr.py": CARD_PR_STUB}
        for path in (ROOT / "scripts").iterdir():
            if path.name not in stubs:
                (scripts / path.name).symlink_to(path)
        for name, body in stubs.items():
            (scripts / name).write_text(body)
        temp = td / "runner-temp"
        temp.mkdir()
        escalation = td / "agent-escalation.txt"
        escalation.write_text(ESCALATION)
        self.log = td / "stub.log"
        self.comment_file = temp / "proof-escalation-comment.md"
        env = dict(os.environ)
        for key in ("NAME_GUARD_LIVE", "OWNER"):
            env.pop(key, None)
        env.update({
            "STUB_LOG": str(self.log), "STUB_REDACT": redact,
            "CARD": CARD, "CARD_DESCRIPTION": "## Acceptance criteria\n\n- [ ] the banner reads right\n",
            "RECORD_BRANCH": f"agent/{CARD}-proof-record",
            "ESCALATION_FILE": str(escalation),
            "BUREAU_SERVER_URL": "https://github.com",
            "BUREAU_REPOSITORY": "acme-org/widgets",
            "BUREAU_RUN_ID": "4242",
            "GITHUB_STEP_SUMMARY": str(td / "summary.md"),
            "RUNNER_TEMP": str(temp),
        })
        if owner is not None:
            env["OWNER"] = owner
        if live is not None:
            env["NAME_GUARD_LIVE"] = live
        proc = subprocess.run(["bash", str(SCRIPT)], cwd=td, env=env,
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.proc = proc

    def calls(self, name):
        if not self.log.exists():
            return []
        rows = [json.loads(line) for line in self.log.read_text().splitlines()]
        return [row[1:] for row in rows if row[0] == name]

    def escalation_comment(self):
        """The one comment that is neither of the fixed receipts."""
        found = [c[2] for c in self.calls("linear_ops")
                 if c[0] == "comment" and not c[2].startswith("🤖 ")]
        self.assertEqual(len(found), 1, self.calls("linear_ops"))
        return found[0]

    def assert_parked(self):
        ops = self.calls("linear_ops")
        self.assertEqual([c[0] for c in ops if c[0] == "proof-waiting"], ["proof-waiting"])
        self.assertIn(["advance", CARD, "Green Light", "Hand-work,In Review"], ops)

    def assert_receipts_unchanged(self):
        receipts = [c[2] for c in self.calls("linear_ops")
                    if c[0] == "comment" and c[2].startswith("🤖 ")]
        self.assertEqual(receipts, [
            f"🤖 PR opened: {PR_URL} — CI + critic review running. Run: {RUN_URL}"])


class LiveAndRedactedTest(_ProofRun):
    def test_it_posts_the_redacted_escalation_and_still_parks(self):
        self.run_report(live="true")
        posted = self.escalation_comment()
        self.assertTrue(posted.startswith("🙋 The proof run asks a decision only the CEO can make:"), posted)
        self.assertIn("the page [a person] owns", posted)
        self.assertNotIn("FORM-ALPHA", posted)
        self.assertIn(f"Run: {RUN_URL}", posted)
        self.assert_parked()
        self.assert_receipts_unchanged()

    def test_it_redacts_the_comment_file_in_place_before_it_posts(self):
        self.run_report(live="true")
        self.assertEqual(self.calls("name_redact"), [
            ["redact", "--file", str(self.comment_file), "--in-place", "--owner", "acme-org"]])
        rows = [json.loads(line) for line in self.log.read_text().splitlines()]
        redact_at = next(i for i, row in enumerate(rows) if row[0] == "name_redact")
        comment_at = next(i for i, row in enumerate(rows)
                          if row[:2] == ["linear_ops", "comment"] and row[3].startswith("🙋"))
        self.assertLess(redact_at, comment_at)

    def test_with_no_owner_it_reads_the_owner_off_the_repository(self):
        self.run_report(live="true", owner=None)
        self.assertEqual([c[-1] for c in self.calls("name_redact")], ["acme-org"])


class LiveAndTheRosterUnreadableTest(_ProofRun):
    def test_it_posts_the_fixed_sentence_and_the_run_and_still_parks(self):
        self.run_report(live="true", redact="roster")
        posted = self.escalation_comment()
        self.assertEqual(posted.splitlines()[0], UNREDACTED)
        self.assertIn(RUN_URL, posted)
        self.assertNotIn("FORM-ALPHA", posted)
        self.assertNotIn("Finding:", posted)
        self.assert_parked()
        self.assert_receipts_unchanged()


class NotLiveTest(_ProofRun):
    def test_unset_posts_the_file_unchanged(self):
        self.run_report(live=None)
        posted = self.escalation_comment()
        self.assertIn("the page FORM-ALPHA owns", posted)
        self.assertEqual(posted, self.comment_file.read_text().rstrip("\n"))
        self.assertEqual(self.calls("name_redact"), [])
        self.assert_parked()

    def test_a_value_other_than_true_is_not_live(self):
        self.run_report(live="1", redact="roster")
        self.assertIn("the page FORM-ALPHA owns", self.escalation_comment())
        self.assertEqual(self.calls("name_redact"), [])


if __name__ == "__main__":
    unittest.main()
