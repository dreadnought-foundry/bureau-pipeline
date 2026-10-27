"""DRE-8170: the critic's comment goes through the redaction seam before it posts.

On 2026-10-20 the critic's approval on bureau-pipeline #985 quoted the `grep`
it had run, and that command carried a person's surname. `post_verdict.sh` now
runs the composed `/tmp/qa-comment.md` through `name_redact.py redact
--in-place`, then `name_redact.py guard --text-file`, when `NAME_GUARD_LIVE`
is `true`. A comment the guard refuses is held: the file is replaced by a
neutral status opening `🔎 QA Critic — held at the name guard`, carrying the
guard's own line and no `VERDICT:` line, so the merge gate holds, the fix
agent is not woken and the formal review is left alone.

Each case runs `bash scripts/post_verdict.sh` by path, so it reads and writes
the fixed `/tmp/qa-*.md` paths the workflow uses, under `fixed_tmp`'s lock, as
`tests/test_post_verdict.py` does. The pipeline checkout it is handed is the
real `scripts/` with three files stubbed: `name_redact.py` (told what to answer
by `STUB_GUARD`), `sync_review_state.py` and `linear_ops.py`, each logging how
it was called. No fixture holds a name: the stub's one "form" is the token
`FORM-ALPHA`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
QA_REVIEW = ROOT / ".github" / "workflows" / "qa-review.yml"
SCRIPT = ROOT / "scripts" / "post_verdict.sh"
STEP = "Post verdict or neutral status"
DELEGATION = 'bash "$PIPELINE_DIR/scripts/post_verdict.sh"'

sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fixed_tmp  # noqa: E402
import merge_gate  # noqa: E402

#: The fixed paths the script reads and writes.
TMP_FILES = ("qa-verdict.md", "qa-comment.md", "qa-linear.md", "qa-evidence-hold.md")

SHA = "a" * 40
CONTENT = "b" * 64
VERDICT_LINE = f"🔎 QA Critic — VERDICT: APPROVE @{SHA} content:{CONTENT}"
APPROVE = ("VERDICT: APPROVE\n\n## Summary\n\n"
           "I ran `grep -rn FORM-ALPHA scripts/` and the change does what the card asked.\n")
HELD = "🔎 QA Critic — held at the name guard"

#: What the stubbed guard prints, by STUB_GUARD.
FORM_LINE = "name-guard: refused — /tmp/qa-comment.md:5 carries a surname form"
ROSTER_LINE = "name-guard: refused — roster unreadable: the roster could not be fetched"

NAME_REDACT_STUB = r'''#!/usr/bin/env python3
import json, os, sys
argv = sys.argv[1:]
with open(os.environ["STUB_LOG"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps(["name_redact", *argv]) + "\n")
mode = os.environ.get("STUB_GUARD", "clean")
if argv[0] == "redact":
    if mode == "roster":
        print("name-guard: refused — roster unreadable: the roster could not be fetched", file=sys.stderr)
        sys.exit(2)
    path = argv[argv.index("--file") + 1]
    text = open(path, encoding="utf-8").read()
    if mode != "form":
        text = text.replace("FORM-ALPHA", "[a person]")
    open(path, "w", encoding="utf-8").write(text)
    sys.exit(0)
if argv[0] == "guard":
    if mode == "form":
        print("name-guard: refused — /tmp/qa-comment.md:5 carries a surname form")
        sys.exit(1)
    if mode == "roster":
        print("name-guard: refused — roster unreadable: the roster could not be fetched")
        sys.exit(2)
    print("name-guard: ok — 5 lines read, nothing to refuse")
    sys.exit(0)
sys.exit(3)
'''

LOGGING_STUB = r'''#!/usr/bin/env python3
import json, os, sys
with open(os.environ["STUB_LOG"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps([os.path.basename(sys.argv[0])[:-3], *sys.argv[1:]]) + "\n")
'''

GH_STUB = '''#!/usr/bin/env bash
if [ "$1" = "api" ]; then printf '\\n'; exit 0; fi
if [ "$1" = "pr" ] && [ "$2" = "comment" ]; then
  while [ $# -gt 0 ]; do
    if [ "$1" = "--body-file" ]; then cat "$2" > "$STUB_POSTED"; fi
    shift
  done
fi
exit 0
'''


def _step() -> dict:
    doc = yaml.safe_load(QA_REVIEW.read_text())
    found = [
        step for step in doc["jobs"]["review"]["steps"]
        if isinstance(step, dict) and step.get("name") == STEP
    ]
    if len(found) != 1:
        raise AssertionError(f"qa-review.yml has {len(found)} steps named {STEP!r}")
    return found[0]


class TheStepCarriesTheSwitchTest(unittest.TestCase):
    def test_it_carries_the_switch_and_the_owner(self):
        env = _step().get("env") or {}
        self.assertEqual(env.get("NAME_GUARD_LIVE"), "${{ vars.NAME_GUARD_LIVE }}")
        self.assertEqual(env.get("OWNER"), "${{ github.repository_owner }}")

    def test_it_is_still_the_one_line_delegation(self):
        self.assertEqual(_step()["run"].strip(), DELEGATION)


class _PostRun(unittest.TestCase):
    """Runs the script by path over a composed APPROVE, with the seam stubbed."""

    def setUp(self):
        fixed_tmp.hold(self)
        saved = {}
        for name in TMP_FILES:
            path = Path("/tmp") / name
            saved[name] = path.read_bytes() if path.exists() else None
            path.unlink(missing_ok=True)

        def restore():
            for name, data in saved.items():
                path = Path("/tmp") / name
                path.unlink(missing_ok=True)
                if data is not None:
                    path.write_bytes(data)

        self.addCleanup(restore)
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.td = Path(self._td.name)

    def run_post(self, *, live, guard="clean", card="DRE-9001"):
        td = self.td
        pipeline = td / "pipeline"
        scripts = pipeline / "scripts"
        scripts.mkdir(parents=True)
        stubs = {"name_redact.py": NAME_REDACT_STUB,
                 "sync_review_state.py": LOGGING_STUB,
                 "linear_ops.py": LOGGING_STUB}
        for path in (ROOT / "scripts").iterdir():
            if path.name not in stubs:
                (scripts / path.name).symlink_to(path)
        for name, body in stubs.items():
            (scripts / name).write_text(body)
        bin_dir = td / "bin"
        bin_dir.mkdir()
        (bin_dir / "gh").write_text(GH_STUB)
        (bin_dir / "gh").chmod(0o755)
        Path("/tmp/qa-verdict.md").write_text(APPROVE)
        self.log = td / "stub.log"
        self.posted = td / "posted.md"
        env = dict(os.environ)
        env.pop("NAME_GUARD_LIVE", None)
        env["PATH"] = f"{bin_dir}:{env['PATH']}"
        env.update({
            "PIPELINE_DIR": str(pipeline),
            "STUB_LOG": str(self.log), "STUB_POSTED": str(self.posted),
            "STUB_GUARD": guard,
            "CARD": card, "PR": "985", "REPO": "acme-org/widgets",
            "OWNER": "acme-org",
            "REAL": "true", "REVIEWED_SHA": SHA, "CONTENT_ID": CONTENT,
            "MODEL_ID": "claude-opus-5", "MODEL_WHY": "advisory ladder top",
            "GITHUB_REPOSITORY": "acme-org/widgets",
            "A1_OUTCOME": "ok",
        })
        if live is not None:
            env["NAME_GUARD_LIVE"] = live
        proc = subprocess.run(["bash", str(SCRIPT)], cwd=td, env=env,
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.proc = proc
        return self.posted.read_text()

    def calls(self, name):
        if not self.log.exists():
            return []
        rows = [json.loads(line) for line in self.log.read_text().splitlines()]
        return [row[1:] for row in rows if row[0] == name]

    def linear_comment(self):
        found = [c for c in self.calls("linear_ops") if c[0] == "comment"]
        self.assertEqual(len(found), 1, found)
        return found[0][2]


class LiveAndCleanTest(_PostRun):
    def test_it_posts_the_redacted_comment_first_line_unchanged(self):
        posted = self.run_post(live="true")
        self.assertEqual(posted.splitlines()[0], VERDICT_LINE)
        self.assertNotIn("FORM-ALPHA", posted)
        self.assertIn("grep -rn [a person] scripts/", posted)

    def test_it_runs_redact_then_guard_on_the_comment(self):
        self.run_post(live="true")
        self.assertEqual(self.calls("name_redact"), [
            ["redact", "--file", "/tmp/qa-comment.md", "--in-place", "--owner", "acme-org"],
            ["guard", "--text-file", "/tmp/qa-comment.md", "--owner", "acme-org"],
        ])

    def test_the_card_copy_is_built_after_the_seam(self):
        self.run_post(live="true")
        copy = self.linear_comment()
        self.assertEqual(copy.splitlines()[0], VERDICT_LINE)
        self.assertNotIn("FORM-ALPHA", copy)

    def test_the_formal_review_is_still_brought_into_line(self):
        self.run_post(live="true")
        self.assertEqual(self.calls("sync_review_state"), [["acme-org/widgets", "985"]])


class _Held:
    """The guard refused: a held neutral status, never the composed text."""

    GUARD = ""
    LINE = ""

    def test_it_posts_the_held_neutral_status(self):
        posted = self.run_post(live="true", guard=self.GUARD)
        self.assertEqual(posted.splitlines()[0], HELD)
        self.assertIn("QA Critic", posted)
        self.assertNotIn("VERDICT:", posted)
        self.assertIn(self.LINE, posted)
        self.assertNotIn("FORM-ALPHA", posted)

    def test_the_merge_gate_reads_it_as_the_critics_word_and_no_verdict(self):
        posted = self.run_post(live="true", guard=self.GUARD)
        self.assertTrue(merge_gate.opens_with_marker(posted, merge_gate.CRITIC_MARKER))
        self.assertIsNone(merge_gate.verdict_token(merge_gate.first_line(posted),
                                                   merge_gate.CRITIC_MARKER))

    def test_the_formal_review_is_left_alone(self):
        self.run_post(live="true", guard=self.GUARD)
        self.assertEqual(self.calls("sync_review_state"), [])

    def test_the_card_copy_is_the_held_status_too(self):
        self.run_post(live="true", guard=self.GUARD)
        copy = self.linear_comment()
        self.assertEqual(copy.splitlines()[0], HELD)
        self.assertNotIn("VERDICT:", copy)
        self.assertNotIn("FORM-ALPHA", copy)


class LiveAndAFormRemainsTest(_Held, _PostRun):
    GUARD = "form"
    LINE = FORM_LINE


class LiveAndTheRosterUnreadableTest(_Held, _PostRun):
    GUARD = "roster"
    LINE = ROSTER_LINE


class NotLiveTest(_PostRun):
    def test_unset_posts_the_composed_comment_unchanged(self):
        posted = self.run_post(live=None)
        self.assertEqual(posted.splitlines()[0], VERDICT_LINE)
        self.assertIn("I ran `grep -rn FORM-ALPHA scripts/`", posted)
        self.assertEqual(self.calls("name_redact"), [])
        self.assertEqual(self.calls("sync_review_state"), [["acme-org/widgets", "985"]])

    def test_a_value_other_than_true_is_not_live(self):
        posted = self.run_post(live="false", guard="form")
        self.assertEqual(posted.splitlines()[0], VERDICT_LINE)
        self.assertEqual(self.calls("name_redact"), [])


if __name__ == "__main__":
    unittest.main()
