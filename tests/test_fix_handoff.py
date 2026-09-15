"""The fix loop's context is keyed to ONE repo, PR and head (DRE-3951).

Origin (2026-09-14, two sightings in two repos):

  * portico #501 (DRE-3947). The critic returned REQUEST_CHANGES over PR-body
    placeholders at 15:11 PT. Two minutes later the fix run posted "PR #2556
    was already approved and merged before this run started. DRE-3863 is
    Done" — agent-bureau #2556's history, in another repository.
  * agent-bureau #2553 (DRE-3891), which held an APPROVE and a verifier PASS,
    collected four fix-agent comments about DRE-3738 and DRE-3746 — PR #2516's
    facts, from two days earlier.

The carrier was the handoff: agent-fix.yml told the fixing agent to write its
blocker to the FIXED path `/tmp/fix-blocker.txt`, and the Report step read that
path back with `[ -f ... ]`. On the reused self-hosted minis nothing clears
`/tmp` between jobs, so one card's escalation was posted on the next card's PR
— #2556's run found #2516's file dated 09-13 06:56, and #2567's run then posted
#2556's.

`scripts/fix_handoff.py` is the answer: one keyed directory per (repo, PR, head
sha), opened and stamped at the top of the job, and a read that REFUSES rather
than guess when what it finds cannot be attributed to this run. These are its
unit tests; `tests/test_fix_answers_its_own_pr.py` executes the real workflow
steps over the same fault.

Run: python3 -m pytest tests/test_fix_handoff.py -v
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)

import fix_handoff  # noqa: E402

REPO = "dreadnought-foundry/portico"
OTHER_REPO = "dreadnought-foundry/agent-bureau"
PR = "501"
OTHER_PR = "2556"
SHA = "247fcf428" + "c" * 31
OTHER_SHA = "43363f040" + "d" * 31
CARD = "DRE-3947"
BLOCKER = "the PR body placeholders are the card's own text"
STALE = "PR #2556 was already approved and merged before this run started."


def _sandbox(td: str):
    """A base (the job's RUNNER_TEMP) and a legacy dir (the machine's /tmp)."""
    base = os.path.join(td, "runner-temp")
    legacy = os.path.join(td, "tmp")
    os.makedirs(base)
    os.makedirs(legacy)
    return base, legacy


def _write(path: str, text: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def _age(path: str, seconds: int = 60) -> None:
    """Backdate a file — the litter a previous job left behind."""
    when = time.time() - seconds
    os.utime(path, (when, when))


class TheKeyNamesRepoPrAndHead(unittest.TestCase):
    def test_the_key_carries_all_three(self):
        key = fix_handoff.key(REPO, PR, SHA)
        self.assertIn(REPO, key)
        self.assertIn(PR, key)
        self.assertIn(SHA[:8], key)

    def test_two_prs_in_one_repo_never_share_a_key(self):
        self.assertNotEqual(
            fix_handoff.key(REPO, PR, SHA), fix_handoff.key(REPO, OTHER_PR, SHA)
        )

    def test_the_same_pr_number_in_two_repos_never_shares_a_key(self):
        # portico #501 and agent-bureau #501 are different pull requests; the
        # live sighting crossed repositories, so the repo is part of the key.
        self.assertNotEqual(
            fix_handoff.key(REPO, PR, SHA), fix_handoff.key(OTHER_REPO, PR, SHA)
        )

    def test_a_new_head_is_a_new_key(self):
        self.assertNotEqual(
            fix_handoff.key(REPO, PR, SHA), fix_handoff.key(REPO, PR, OTHER_SHA)
        )

    def test_the_directory_is_named_from_the_key_and_is_path_safe(self):
        with tempfile.TemporaryDirectory() as td:
            base, _ = _sandbox(td)
            path = fix_handoff.handoff_dir(base, REPO, PR, SHA)
        self.assertTrue(path.startswith(base + os.sep))
        # The slash in the repo slug must not become a directory level.
        self.assertEqual(len(path[len(base) + 1:].split(os.sep)), 1)


class OpeningAHandoffClearsWhatCameBefore(unittest.TestCase):
    def test_open_creates_the_directory_and_stamps_it(self):
        with tempfile.TemporaryDirectory() as td:
            base, legacy = _sandbox(td)
            paths = fix_handoff.open_handoff(base, REPO, PR, SHA, legacy_dir=legacy)
            self.assertTrue(os.path.isdir(paths["dir"]))
            stamp = fix_handoff.read_stamp(paths["dir"])
        self.assertEqual(stamp["key"], fix_handoff.key(REPO, PR, SHA))
        self.assertEqual(stamp["repo"], REPO)
        self.assertEqual(stamp["pr"], PR)
        self.assertEqual(stamp["head"], SHA)
        self.assertEqual(stamp["legacy"], legacy)

    def test_open_deletes_the_legacy_fixed_paths(self):
        # THE live carrier: `/tmp/fix-blocker.txt` from a previous job.
        with tempfile.TemporaryDirectory() as td:
            base, legacy = _sandbox(td)
            stale = _write(os.path.join(legacy, "fix-blocker.txt"), STALE)
            stale_ref = _write(os.path.join(legacy, "fix-refutation.txt"), STALE)
            fix_handoff.open_handoff(base, REPO, PR, SHA, legacy_dir=legacy)
            self.assertFalse(os.path.exists(stale))
            self.assertFalse(os.path.exists(stale_ref))

    def test_open_deletes_another_runs_keyed_handoff(self):
        with tempfile.TemporaryDirectory() as td:
            base, legacy = _sandbox(td)
            foreign = fix_handoff.handoff_dir(base, OTHER_REPO, OTHER_PR, OTHER_SHA)
            _write(os.path.join(foreign, "fix-blocker.txt"), STALE)
            fix_handoff.open_handoff(base, REPO, PR, SHA, legacy_dir=legacy)
            self.assertFalse(os.path.exists(foreign))

    def test_open_wipes_a_previous_attempt_at_the_same_key(self):
        # A re-dispatch on the same head must not inherit its own last
        # attempt's blocker — that is the same fault with a shorter reach.
        with tempfile.TemporaryDirectory() as td:
            base, legacy = _sandbox(td)
            first = fix_handoff.open_handoff(base, REPO, PR, SHA, legacy_dir=legacy)
            _write(first["blocker"], "last attempt's blocker")
            fix_handoff.open_handoff(base, REPO, PR, SHA, legacy_dir=legacy)
            self.assertFalse(os.path.exists(first["blocker"]))

    def test_open_reports_what_it_cleared(self):
        with tempfile.TemporaryDirectory() as td:
            base, legacy = _sandbox(td)
            _write(os.path.join(legacy, "fix-blocker.txt"), STALE)
            paths = fix_handoff.open_handoff(base, REPO, PR, SHA, legacy_dir=legacy)
        self.assertEqual(len(paths["cleared"]), 1, paths["cleared"])
        self.assertIn("fix-blocker.txt", paths["cleared"][0])


class ReadingAHandoffProvesItIsThisRuns(unittest.TestCase):
    def test_this_runs_own_blocker_is_read(self):
        with tempfile.TemporaryDirectory() as td:
            base, legacy = _sandbox(td)
            paths = fix_handoff.open_handoff(base, REPO, PR, SHA, legacy_dir=legacy)
            _write(paths["blocker"], BLOCKER)
            status, text, reason = fix_handoff.read_handoff(
                base, REPO, PR, SHA, "blocker"
            )
        self.assertEqual(status, fix_handoff.PRESENT)
        self.assertEqual(text, BLOCKER)
        self.assertEqual(reason, "")

    def test_no_handoff_at_all_is_absent_not_a_refusal(self):
        with tempfile.TemporaryDirectory() as td:
            base, legacy = _sandbox(td)
            fix_handoff.open_handoff(base, REPO, PR, SHA, legacy_dir=legacy)
            status, text, _ = fix_handoff.read_handoff(base, REPO, PR, SHA, "blocker")
        self.assertEqual(status, fix_handoff.ABSENT)
        self.assertEqual(text, "")

    def test_an_unopened_run_with_nothing_on_disk_is_absent(self):
        # No stamp and no candidate: there is nothing to misattribute, and a
        # run that never reached the open step must still be able to report a
        # push. Absence is not a refusal.
        with tempfile.TemporaryDirectory() as td:
            base, _ = _sandbox(td)
            status, text, _ = fix_handoff.read_handoff(base, REPO, PR, SHA, "blocker")
        self.assertEqual(status, fix_handoff.ABSENT)
        self.assertEqual(text, "")

    def test_an_unopened_run_refuses_a_file_it_cannot_attribute(self):
        # Nothing stamped this run, so nothing found on disk can be proved to
        # be its own. Refuse rather than read whatever is lying around.
        with tempfile.TemporaryDirectory() as td:
            base, legacy = _sandbox(td)
            os.makedirs(fix_handoff.handoff_dir(base, REPO, PR, SHA))
            _write(
                os.path.join(fix_handoff.handoff_dir(base, REPO, PR, SHA),
                             "fix-blocker.txt"),
                STALE,
            )
            status, text, reason = fix_handoff.read_handoff(
                base, REPO, PR, SHA, "blocker"
            )
            self.assertFalse(os.path.exists(os.path.join(legacy, "x")))
        self.assertEqual(status, fix_handoff.MISMATCH)
        self.assertEqual(text, "")
        self.assertIn("no handoff", reason.lower())

    def test_a_stamp_for_another_pr_refuses(self):
        with tempfile.TemporaryDirectory() as td:
            base, legacy = _sandbox(td)
            paths = fix_handoff.open_handoff(base, REPO, PR, SHA, legacy_dir=legacy)
            _write(paths["blocker"], BLOCKER)
            # The directory is this run's, the stamp inside it is not.
            _write(
                os.path.join(paths["dir"], fix_handoff.STAMP),
                f"repo={OTHER_REPO}\npr={OTHER_PR}\nhead={OTHER_SHA}\n"
                f"key={fix_handoff.key(OTHER_REPO, OTHER_PR, OTHER_SHA)}\n"
                f"legacy={legacy}\n",
            )
            status, text, reason = fix_handoff.read_handoff(
                base, REPO, PR, SHA, "blocker"
            )
        self.assertEqual(status, fix_handoff.MISMATCH)
        self.assertEqual(text, "", "another run's blocker must never be returned")
        self.assertIn(OTHER_PR, reason)

    def test_a_handoff_written_during_this_run_at_the_legacy_path_refuses(self):
        # The provenance cannot be established: `/tmp` is shared machine-wide
        # on the minis, so a file that appeared there after this run opened its
        # own handoff may belong to any job on the box. Refuse, never guess.
        with tempfile.TemporaryDirectory() as td:
            base, legacy = _sandbox(td)
            fix_handoff.open_handoff(base, REPO, PR, SHA, legacy_dir=legacy)
            stray = _write(os.path.join(legacy, "fix-blocker.txt"), STALE)
            status, text, reason = fix_handoff.read_handoff(
                base, REPO, PR, SHA, "blocker"
            )
        self.assertEqual(status, fix_handoff.MISMATCH)
        self.assertEqual(text, "")
        self.assertIn(stray, reason)
        self.assertNotIn(STALE, reason, "the foreign body is never echoed")

    def test_litter_older_than_this_run_is_not_a_refusal(self):
        # A file the open step could not remove (a permission blip, a path it
        # does not know) but which predates this run is somebody else's
        # rubbish, not a handoff of ours: it is ignored, never read, and it
        # does not stop a healthy fix from reporting.
        with tempfile.TemporaryDirectory() as td:
            base, legacy = _sandbox(td)
            fix_handoff.open_handoff(base, REPO, PR, SHA, legacy_dir=legacy)
            old = _write(os.path.join(legacy, "fix-blocker.txt"), STALE)
            _age(old, 3600)
            status, text, _ = fix_handoff.read_handoff(base, REPO, PR, SHA, "blocker")
        self.assertEqual(status, fix_handoff.ABSENT)
        self.assertEqual(text, "")

    def test_another_runs_keyed_handoff_written_during_this_run_refuses(self):
        with tempfile.TemporaryDirectory() as td:
            base, legacy = _sandbox(td)
            fix_handoff.open_handoff(base, REPO, PR, SHA, legacy_dir=legacy)
            foreign = fix_handoff.handoff_dir(base, OTHER_REPO, OTHER_PR, OTHER_SHA)
            _write(os.path.join(foreign, "fix-blocker.txt"), STALE)
            status, text, reason = fix_handoff.read_handoff(
                base, REPO, PR, SHA, "blocker"
            )
        self.assertEqual(status, fix_handoff.MISMATCH)
        self.assertEqual(text, "")
        self.assertIn(OTHER_REPO, reason)

    def test_the_two_kinds_are_independent(self):
        with tempfile.TemporaryDirectory() as td:
            base, legacy = _sandbox(td)
            paths = fix_handoff.open_handoff(base, REPO, PR, SHA, legacy_dir=legacy)
            _write(paths["refutation"], "the evidence")
            blocker = fix_handoff.read_handoff(base, REPO, PR, SHA, "blocker")
            refutation = fix_handoff.read_handoff(base, REPO, PR, SHA, "refutation")
        self.assertEqual(blocker[0], fix_handoff.ABSENT)
        self.assertEqual(refutation[0], fix_handoff.PRESENT)
        self.assertEqual(refutation[1], "the evidence")


class TheVerdictIsStampedWithWhatItIsFor(unittest.TestCase):
    VERDICT = (
        "🔍 **QA Critic** — PR #501\n\n"
        f"VERDICT: REQUEST_CHANGES cause:unmet-criteria @{SHA}\n"
    )

    def _stamped(self, td):
        base, legacy = _sandbox(td)
        fix_handoff.open_handoff(base, REPO, PR, SHA, legacy_dir=legacy)
        verdict = _write(os.path.join(td, "critic-verdict.md"), self.VERDICT)
        return base, legacy, verdict

    def test_the_stamp_records_the_verdict_sha_it_read(self):
        with tempfile.TemporaryDirectory() as td:
            base, _, verdict = self._stamped(td)
            fix_handoff.stamp_verdict(base, REPO, PR, SHA, verdict)
            ok, reason = fix_handoff.check_context(base, REPO, PR, SHA)
            self.assertTrue(ok, reason)
            self.assertEqual(fix_handoff.verdict_sha(base, REPO, PR, SHA), SHA)

    def test_an_empty_verdict_is_stamped_as_none_and_still_passes(self):
        # Conflict mode and hand dispatches have no critic verdict at all —
        # that is not a mismatch, it is an absence, and it is named as one.
        with tempfile.TemporaryDirectory() as td:
            base, _, _ = self._stamped(td)
            empty = _write(os.path.join(td, "empty.md"), "")
            fix_handoff.stamp_verdict(base, REPO, PR, SHA, empty)
            ok, reason = fix_handoff.check_context(base, REPO, PR, SHA)
        self.assertTrue(ok, reason)
        self.assertIsNone(fix_handoff.verdict_sha(base, REPO, PR, SHA))

    def test_a_verdict_stamped_for_another_repo_refuses(self):
        # The cross-repo sighting, at the verdict: portico #501's run holding
        # agent-bureau #2556's verdict.
        with tempfile.TemporaryDirectory() as td:
            base, _, verdict = self._stamped(td)
            fix_handoff.stamp_verdict(base, OTHER_REPO, OTHER_PR, OTHER_SHA, verdict,
                                      _dir=fix_handoff.handoff_dir(base, REPO, PR, SHA))
            ok, reason = fix_handoff.check_context(base, REPO, PR, SHA)
        self.assertFalse(ok)
        self.assertIn(OTHER_REPO, reason)
        self.assertIn(OTHER_PR, reason)

    def test_an_unstamped_verdict_is_reported_but_does_not_refuse(self):
        # A run whose verdict fetch never happened (it died earlier in the job)
        # still owes the pull request its report — and the report says the
        # verdict is unstamped rather than naming one it cannot prove.
        with tempfile.TemporaryDirectory() as td:
            base, _, _ = self._stamped(td)
            ok, reason = fix_handoff.check_context(base, REPO, PR, SHA)
            line = fix_handoff.attribution(base, REPO, PR, SHA, CARD)
        self.assertTrue(ok, reason)
        self.assertIn("unstamped", reason.lower())
        self.assertIn("unstamped", line.lower())


class EveryCommentNamesWhatItAnswers(unittest.TestCase):
    def _attribution(self, td, card=CARD, verdict=True):
        base, legacy = _sandbox(td)
        fix_handoff.open_handoff(base, REPO, PR, SHA, legacy_dir=legacy)
        body = (
            f"🔍 **QA Critic** — VERDICT: REQUEST_CHANGES @{SHA}\n"
            if verdict else ""
        )
        fix_handoff.stamp_verdict(
            base, REPO, PR, SHA, _write(os.path.join(td, "v.md"), body)
        )
        return fix_handoff.attribution(base, REPO, PR, SHA, card)

    def test_it_names_the_repo_the_pr_the_card_and_the_verdict_sha(self):
        with tempfile.TemporaryDirectory() as td:
            line = self._attribution(td)
        self.assertIn(f"{REPO}#{PR}", line)
        self.assertIn(CARD, line)
        self.assertIn(SHA[:8], line)
        self.assertEqual(len(line.splitlines()), 1, "one line, like every trailer")

    def test_it_says_none_rather_than_inventing_a_verdict(self):
        with tempfile.TemporaryDirectory() as td:
            line = self._attribution(td, verdict=False)
        self.assertIn("none", line)
        self.assertIn(f"{REPO}#{PR}", line)

    def test_a_cardless_branch_says_so(self):
        with tempfile.TemporaryDirectory() as td:
            line = self._attribution(td, card="")
        self.assertIn("none", line)
        self.assertIn(f"{REPO}#{PR}", line)

    def test_the_line_is_greppable_and_not_a_verdict_marker(self):
        # standards/untrusted-content.md: nothing this pipeline writes may
        # carry a verdict-shaped string outside the critic's own comment.
        with tempfile.TemporaryDirectory() as td:
            line = self._attribution(td)
        self.assertIn(fix_handoff.ATTRIBUTION_TAG, line)
        for forbidden in ("VERDICT:", "QA Critic", "QA Verifier"):
            self.assertNotIn(forbidden, line)


class TheCliIsTheWorkflowsSeam(unittest.TestCase):
    def _run(self, *args, **kwargs):
        return subprocess.run(
            [sys.executable, os.path.join(SCRIPTS, "fix_handoff.py"), *args],
            capture_output=True, text=True, **kwargs,
        )

    def test_open_prints_github_output_lines(self):
        with tempfile.TemporaryDirectory() as td:
            base, legacy = _sandbox(td)
            proc = self._run("open", "--base", base, "--repo", REPO, "--pr", PR,
                             "--sha", SHA, "--legacy-dir", legacy)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        fields = dict(
            line.split("=", 1) for line in proc.stdout.splitlines() if "=" in line
        )
        self.assertTrue(fields["blocker"].endswith("fix-blocker.txt"))
        self.assertTrue(fields["refutation"].endswith("fix-refutation.txt"))
        self.assertEqual(fields["key"], fix_handoff.key(REPO, PR, SHA))
        self.assertTrue(os.path.isdir(fields["dir"]))

    def test_read_exits_zero_and_prints_the_body(self):
        with tempfile.TemporaryDirectory() as td:
            base, legacy = _sandbox(td)
            paths = fix_handoff.open_handoff(base, REPO, PR, SHA, legacy_dir=legacy)
            _write(paths["blocker"], BLOCKER)
            proc = self._run("read", "--kind", "blocker", "--base", base,
                             "--repo", REPO, "--pr", PR, "--sha", SHA)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.rstrip("\n"), BLOCKER)

    def test_read_exits_3_when_there_is_nothing_to_report(self):
        with tempfile.TemporaryDirectory() as td:
            base, legacy = _sandbox(td)
            fix_handoff.open_handoff(base, REPO, PR, SHA, legacy_dir=legacy)
            proc = self._run("read", "--kind", "blocker", "--base", base,
                             "--repo", REPO, "--pr", PR, "--sha", SHA)
        self.assertEqual(proc.returncode, fix_handoff.EXIT_ABSENT)
        self.assertEqual(proc.stdout, "")

    def test_read_exits_4_and_prints_nothing_on_a_foreign_handoff(self):
        with tempfile.TemporaryDirectory() as td:
            base, legacy = _sandbox(td)
            fix_handoff.open_handoff(base, REPO, PR, SHA, legacy_dir=legacy)
            _write(os.path.join(legacy, "fix-blocker.txt"), STALE)
            proc = self._run("read", "--kind", "blocker", "--base", base,
                             "--repo", REPO, "--pr", PR, "--sha", SHA)
        self.assertEqual(proc.returncode, fix_handoff.EXIT_MISMATCH)
        self.assertEqual(proc.stdout, "", "stdout is the body — it must be empty")
        self.assertNotIn(STALE, proc.stderr)

    def test_check_exits_4_when_the_verdict_is_another_prs(self):
        with tempfile.TemporaryDirectory() as td:
            base, legacy = _sandbox(td)
            fix_handoff.open_handoff(base, REPO, PR, SHA, legacy_dir=legacy)
            fix_handoff.stamp_verdict(
                base, OTHER_REPO, OTHER_PR, OTHER_SHA,
                _write(os.path.join(td, "v.md"), "VERDICT: REQUEST_CHANGES"),
                _dir=fix_handoff.handoff_dir(base, REPO, PR, SHA),
            )
            proc = self._run("check", "--base", base, "--repo", REPO,
                             "--pr", PR, "--sha", SHA)
        self.assertEqual(proc.returncode, fix_handoff.EXIT_MISMATCH)
        self.assertIn(OTHER_PR, proc.stderr)

    def test_attribution_prints_one_line(self):
        with tempfile.TemporaryDirectory() as td:
            base, legacy = _sandbox(td)
            fix_handoff.open_handoff(base, REPO, PR, SHA, legacy_dir=legacy)
            fix_handoff.stamp_verdict(
                base, REPO, PR, SHA,
                _write(os.path.join(td, "v.md"),
                       f"🔍 **QA Critic** VERDICT: REQUEST_CHANGES @{SHA}"),
            )
            proc = self._run("attribution", "--base", base, "--repo", REPO,
                             "--pr", PR, "--sha", SHA, "--card", CARD)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(proc.stdout.strip().splitlines()), 1)
        self.assertIn(f"{REPO}#{PR}", proc.stdout)
        self.assertIn(CARD, proc.stdout)

    def test_attribution_never_dies_on_a_missing_stamp(self):
        # It is appended to comments the loop MUST post; a missing stamp
        # degrades to naming less, never to losing the comment.
        with tempfile.TemporaryDirectory() as td:
            base, _ = _sandbox(td)
            proc = self._run("attribution", "--base", base, "--repo", REPO,
                             "--pr", PR, "--sha", SHA, "--card", CARD)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(f"{REPO}#{PR}", proc.stdout)


if __name__ == "__main__":
    unittest.main()
