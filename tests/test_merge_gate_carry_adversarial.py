"""Adversarial pass over the merge gate's decision channel (2026-08-10).

DRE-2340 gave the gate a second way to authorise a merge: a verdict can now
be CARRIED across a head change when the PR's own diff is byte-identical.
That is a new path to `decision=merge`, so it deserves to be attacked rather
than only demonstrated.

Two attacker positions are assumed, both realistic:

  * a PR author, who controls the branch, its diff, its title and body, and
    can post comments;
  * anyone who can post a comment on the PR (the repos are org-visible and
    every bot in the fleet comments).

Neither may reach a merge they have not earned. The properties below are the
ones that, if broken, hand an unreviewed diff a merge — plus the shell-level
fail-closed property that the same night's outage showed nobody was testing.

Companion to test_merge_gate_optional_fields.py, which covers the execution
of the step; this file covers what the step is allowed to conclude.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import merge_gate  # noqa: E402
import verdict_content  # noqa: E402
from test_merge_gate_optional_fields import extraction_lines  # noqa: E402

QA_LOGIN = "agent-bureau-qa-bot[bot]"
HEAD = "a" * 40
OTHER_HEAD = "b" * 40
CONTENT = "c" * 64
REVIEW_SUITES = frozenset()

GREEN_CI = [
    {"name": "ci", "status": "completed", "conclusion": "success",
     "check_suite": {"id": 1}},
]


def comment(body, login=QA_LOGIN):
    return {"user": {"login": login}, "body": body}


def approve(sha=HEAD, content=None, login=QA_LOGIN):
    line = f"🔎 QA Critic — VERDICT: APPROVE @{sha}"
    if content:
        line += f" content:{content}"
    return comment(line, login)


def decide(**kw):
    """decide() with everything else already satisfying a merge, so the only
    thing under test is the carry.

    `pr_commits` matters: carries_content ALSO requires the reviewed commit
    to still be in the PR's own commit record. Omit it and the carry path is
    unreachable — every assertion below would then pass for the wrong reason
    (caught by mutation: disabling the id comparison left the suite green).
    """
    kw.setdefault("head_sha", HEAD)
    kw.setdefault("qa_login", QA_LOGIN)
    kw.setdefault("check_runs", list(GREEN_CI))
    kw.setdefault("review_suites", REVIEW_SUITES)
    kw.setdefault("compare_status", "identical")
    kw.setdefault("pr_commits", [{"sha": OTHER_HEAD}, {"sha": HEAD}])
    return merge_gate.decide(**kw)


class CarryPathReachableTest(unittest.TestCase):
    """The positive control for every negative below.

    Without this, a change that quietly makes the carry unreachable would
    turn the whole adversarial suite into a set of vacuous passes."""

    def test_a_legitimately_carried_verdict_merges(self):
        d = decide(comments=[approve(OTHER_HEAD, CONTENT)],
                   head_content_id=CONTENT)
        self.assertEqual(
            d.action, "merge",
            "the carry path is unreachable — every negative test below is "
            f"passing vacuously. reason: {d.reason}",
        )
        self.assertIn(OTHER_HEAD, " ".join(d.carried or []),
                      "the carried record does not name the reviewed commit")
        self.assertEqual(d.content_id, CONTENT)

    def test_the_reviewed_commit_must_still_be_in_the_pr(self):
        """A verdict for a commit that has been force-pushed away is not
        carried, even with a matching id."""
        d = decide(comments=[approve(OTHER_HEAD, CONTENT)],
                   head_content_id=CONTENT,
                   pr_commits=[{"sha": HEAD}])
        self.assertNotEqual(d.action, "merge",
                            "carried a verdict for a commit not in this PR")

    def test_an_empty_commit_record_never_carries(self):
        """`[]` is the workflow's blip substitute — unverifiable, so no
        carry, per the fail-closed rule the rest of the gate follows."""
        d = decide(comments=[approve(OTHER_HEAD, CONTENT)],
                   head_content_id=CONTENT, pr_commits=[])
        self.assertNotEqual(d.action, "merge",
                            "carried on an unverifiable commit record")


class ForgedVerdictTest(unittest.TestCase):
    """A carried verdict is still a verdict: it must satisfy every check an
    ordinary verdict does. Authorship is the load-bearing one — the content
    id is public (it is printed on the PR), so if authorship were not
    enforced, quoting someone else's id would be a merge."""

    def test_a_non_qa_author_cannot_carry_a_verdict(self):
        """The attack: read the content id off the gate's own carry note,
        then post the same APPROVE line yourself."""
        for impostor in ("agent-bureau-bot[bot]", "smeed652",
                         "agent-bureau-qa-bot", "AGENT-BUREAU-QA-BOT[bot]"):
            with self.subTest(login=impostor):
                d = decide(
                    comments=[approve(OTHER_HEAD, CONTENT, login=impostor)],
                    head_content_id=CONTENT,
                )
                self.assertNotEqual(
                    d.action, "merge",
                    f"{impostor} carried a verdict across a head change",
                )

    def test_a_quoted_carry_note_is_not_a_verdict(self):
        """A PR author can quote the gate's own note verbatim in a comment.
        It carries a real content id and a real SHA — it must still not
        authorise anything."""
        note = (
            "♻️ Merge gate: carried verdict content:%s — the standing review "
            "verdict was carried across a branch update. Reviewed at %s; "
            "current head %s." % (CONTENT, OTHER_HEAD, HEAD)
        )
        d = decide(comments=[comment(note, login="smeed652")],
                   head_content_id=CONTENT)
        self.assertNotEqual(d.action, "merge",
                            "a quoted carry note authorised a merge")

    def test_the_gates_own_carry_note_is_not_verdict_shaped(self):
        """The workflow claims the note 'carries NO verdict-shaped text'.
        Hold it to that: parse the real note text with the real verdict
        parser. If this ever fails, the gate can approve its own PRs."""
        run_block = (ROOT / ".github" / "workflows" / "merge-gate.yml").read_text()
        self.assertIn("♻️ Merge gate:", run_block,
                      "carry note text not found — has the arm been renamed?")
        note_lines = [
            ln for ln in run_block.splitlines() if "♻️ Merge gate:" in ln
        ]
        self.assertTrue(note_lines)
        for ln in note_lines:
            with self.subTest(line=ln.strip()[:60]):
                self.assertNotIn(
                    "VERDICT:", ln,
                    "the gate's own note contains a verdict marker — it would "
                    "re-wake the issue_comment leg and could read as approval",
                )


class ContentBindingTest(unittest.TestCase):
    """The carry is only sound if the id genuinely pins the PR's own diff."""

    def test_a_mismatched_content_id_does_not_carry(self):
        d = decide(comments=[approve(OTHER_HEAD, "d" * 64)],
                   head_content_id=CONTENT)
        self.assertNotEqual(d.action, "merge",
                            "a verdict for a DIFFERENT diff was carried")

    def test_no_content_id_on_the_head_never_carries(self):
        """Unprovable compare record → no content id → the pre-DRE-2340
        rule stands: SHA binding only, so a stale-SHA verdict is not a
        merge."""
        d = decide(comments=[approve(OTHER_HEAD, CONTENT)],
                   head_content_id=None)
        self.assertNotEqual(d.action, "merge",
                            "carried a verdict with no content binding")

    def test_a_verdict_without_a_content_id_does_not_carry(self):
        """Every verdict in flight the day this shipped has no id. They must
        keep meaning exactly what they meant: this SHA and nothing else."""
        d = decide(comments=[approve(OTHER_HEAD, None)],
                   head_content_id=CONTENT)
        self.assertNotEqual(d.action, "merge",
                            "a pre-DRE-2340 verdict was carried")

    def test_the_content_id_form_is_constrained(self):
        """The id reaches the shell and is interpolated into a grep pattern.
        A 64-hex digest cannot carry a regex metacharacter or a shell
        metacharacter — pin that so a looser format is never introduced
        without revisiting the shell."""
        self.assertEqual(verdict_content._CONTENT_RE.pattern,
                         r"\bcontent:([0-9a-f]{64})\s*$")
        for hostile in ("../../etc/passwd", ".*", "$(id)", "`id`",
                        "a" * 63, "a" * 65, "A" * 64, "g" * 64):
            with self.subTest(id=hostile[:20]):
                self.assertIsNone(
                    verdict_content.verdict_content_id(
                        f"VERDICT: APPROVE @{HEAD} content:{hostile}"
                    ),
                    f"{hostile!r} parsed as a content id",
                )

    def _compare(self, files):
        """A provably-complete three-dot compare record."""
        return {
            "status": "ahead",
            "merge_base_commit": {"sha": "f" * 40},
            "files": files,
        }

    def test_a_changed_diff_changes_the_id(self):
        """The property the whole feature rests on: if two different diffs
        can share an id, a verdict carries onto code nobody reviewed."""
        base = self._compare([
            {"filename": "a.py", "status": "modified", "sha": "1" * 40},
        ])
        changed = self._compare([
            {"filename": "a.py", "status": "modified", "sha": "2" * 40},
        ])
        renamed = self._compare([
            {"filename": "b.py", "previous_filename": "a.py",
             "status": "renamed", "sha": "1" * 40},
        ])
        flipped = self._compare([
            {"filename": "a.py", "status": "added", "sha": "1" * 40},
        ])
        extra = self._compare([
            {"filename": "a.py", "status": "modified", "sha": "1" * 40},
            {"filename": "z.py", "status": "added", "sha": "3" * 40},
        ])
        ids = [verdict_content.content_id(c)
               for c in (base, changed, renamed, flipped, extra)]
        self.assertNotIn(None, ids, "a complete record failed to produce an id")
        self.assertEqual(len(set(ids)), len(ids),
                         "distinct diffs collided onto one content id")

    def test_file_order_does_not_change_the_id(self):
        """GitHub's listing order is not stable; the id must be."""
        rows = [
            {"filename": "a.py", "status": "modified", "sha": "1" * 40},
            {"filename": "z.py", "status": "added", "sha": "3" * 40},
        ]
        self.assertEqual(
            verdict_content.content_id(self._compare(rows)),
            verdict_content.content_id(self._compare(list(reversed(rows)))),
            "the id depends on GitHub's file ordering",
        )

    def test_an_unprovable_record_yields_no_id(self):
        """Fail closed: anything short of a complete three-dot record must
        produce None, which falls back to SHA binding."""
        complete = [{"filename": "a.py", "status": "modified", "sha": "1" * 40}]
        cases = {
            "blip substitute": {},
            "no merge base": {"status": "ahead", "files": complete},
            "unreadable status": {"status": "???",
                                  "merge_base_commit": {"sha": "f" * 40},
                                  "files": complete},
            "files missing": {"status": "ahead",
                              "merge_base_commit": {"sha": "f" * 40}},
            "entry without sha": self._compare([{"filename": "a.py"}]),
            "entry without filename": self._compare([{"sha": "1" * 40}]),
            "over the file cap": self._compare([
                {"filename": f"f{i}.py", "status": "added", "sha": f"{i:040d}"}
                for i in range(verdict_content.MAX_COMPARE_FILES)
            ]),
        }
        for name, payload in cases.items():
            with self.subTest(case=name):
                self.assertIsNone(
                    verdict_content.content_id(payload),
                    f"{name}: an unprovable record produced a content id",
                )


class ShellFailClosedTest(unittest.TestCase):
    """The decision file is the ONLY channel from the script to the shell.
    Whatever arrives on it — truncated, empty, hostile — the shell must never
    reach the merge arm on its own."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.decision = Path(self.tmp.name) / "gate-decision"
        self.addCleanup(self.tmp.cleanup)

    def _extract(self, payload: str):
        """Run every extraction line, then report what DECISION became."""
        self.decision.write_text(payload)
        lines = ["set -euo pipefail"]
        for _, _, line in extraction_lines():
            lines.append(line.replace("/tmp/gate-decision", str(self.decision)))
        lines.append('printf "DECISION=[%s]" "${DECISION:-}"')
        return subprocess.run(["bash", "-c", "\n".join(lines)],
                              capture_output=True, text=True)

    def test_a_reason_line_mentioning_carried_is_not_the_carried_field(self):
        """The reason text is free prose derived from GitHub data. It must
        not be able to impersonate a field — the extractions are anchored."""
        proc = self._extract(
            "decision=wait\n"
            "reason=the branch carried=deadbeef looks odd\n"
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        script = ["set -euo pipefail"]
        for _, _, line in extraction_lines():
            script.append(line.replace("/tmp/gate-decision", str(self.decision)))
        script.append('printf "[%s]" "${CARRIED:-}"')
        got = subprocess.run(["bash", "-c", "\n".join(script)],
                             capture_output=True, text=True)
        self.assertEqual(got.stdout, "[]",
                         "a reason line was read as the carried field")

    def test_an_empty_decision_file_never_yields_merge(self):
        proc = self._extract("")
        self.assertNotIn("DECISION=[merge]", proc.stdout,
                         "an empty decision file produced a merge")

    def test_a_truncated_decision_file_never_yields_merge(self):
        """The script was killed mid-write, or the pipe broke."""
        for payload in ("decis", "decision=", "note=x\n", "\n\n\n"):
            with self.subTest(payload=payload[:12]):
                proc = self._extract(payload)
                self.assertNotIn("DECISION=[merge]", proc.stdout,
                                 f"{payload!r} produced a merge")

    def test_only_the_first_decision_line_is_honoured(self):
        """If anything downstream ever appends to this file, the first line
        — the script's own — must win."""
        proc = self._extract("decision=wait\nreason=x\ndecision=merge\n")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("DECISION=[wait]", proc.stdout,
                      "a later decision line overrode the script's own")


if __name__ == "__main__":
    unittest.main()
