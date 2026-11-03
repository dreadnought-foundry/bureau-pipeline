"""RED-first: the automatic promoter looks back for the newest proven commit
(DRE-9069).

What 2026-11-02 actually showed: nothing proven was passed over that night.
`be3fc12` was promoted as soon as the last of its own proofs ended, and the
earlier lag had no proven commit ahead of `stable` at all. The gap that is
real is narrower. Each promote run judges ONE commit — the one whose harness
or unit-suite run started it — and nothing ever looks at an older proven
commit again. Two things can leave a proven commit A with no run left to
promote it:

  1. the run that would have promoted A is canceled before it starts — the
     `promote-channel` concurrency group keeps one pending run, and GitHub
     drops it when a newer one queues (runs 41583518423 and 41560513828 on
     2026-11-02); or
  2. A's run is refused for a reason that clears on its own, such as
     `mirror-check-blocked`.

If every newer commit on main then fails, is canceled or has no stamp,
`stable` stays behind A until a person promotes A by hand.

So when an automatic run refuses its own commit B at the HARNESS proof, it
walks main's first-parent chain from main's head, newest first, at most 20
commits, stopping at `stable`, and puts the first proven commit A through
every existing gate. This file pins that: what counts as proven, how far and
where the walk goes, that A gets every gate, that force cannot be reached
through it, that a passing B is untouched, and the workflow's shape.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import mirror_check  # noqa: E402
import promote_channel  # noqa: E402

WORKFLOW = ROOT / ".github" / "workflows" / "promote-channel.yml"
DOC = ROOT / "docs" / "self-hosting.md"

REPO = "dreadnought-foundry/bureau-pipeline"
HARNESS = "Integration Harness"

#: The commits of 2026-11-02, padded to full length.
STABLE = "541e867" + "0" * 33
A = "be3fc12" + "0" * 33
B = "dd9a33d" + "0" * 33
C = "cb11594" + "0" * 33


def _stamp(state, description=""):
    return {"state": state, "statuses": [{
        "context": promote_channel.STATUS_CONTEXT, "state": state,
        "description": description}]}


PASSED = _stamp("success", "harness passed")
FAILED = _stamp("failure", "harness failed")
PENDING = _stamp("pending", "harness running")
ERROR = _stamp("error", "harness errored")
SANDBOX = _stamp("failure", "harness blocked: Linear 429 on the sandbox sweep")
#: A blocked receipt is never proof, whatever state it wears.
SANDBOX_GREEN = _stamp("success", "harness blocked: the sandbox never ran")
ABSENT = {"state": "pending", "statuses": []}
#: A status read that failed.
UNREADABLE = None

UNIT_GREEN = {"run_id": 41583116977, "status": "completed", "conclusion": "success"}
UNIT_RED = {"run_id": 41583116978, "status": "completed", "conclusion": "failure"}
UNIT_RUNNING = {"run_id": 41583116979, "status": "in_progress", "conclusion": None}

MIRROR_PASSED = {"status": mirror_check.MIRROR_PASSED, "tests": 43,
                 "agent_bureau_sha": "a" * 40, "failing": [], "already_red": []}


def _flat(text) -> str:
    return " ".join(str(text or "").split())


def _sha(n: int) -> str:
    return f"{n:07x}" + "f" * 33


class Trunk:
    """Main's first-parent chain, oldest first, beginning at `stable`, and
    every record a promote run would read about it."""

    def __init__(self, *commits, channel=STABLE):
        self.order = [sha for sha, _stamp, _unit in commits]
        self.stamps = {sha: stamp for sha, stamp, _unit in commits}
        self.units = {sha: unit for sha, _stamp, unit in commits}
        self.channel = channel
        self.reads: list[str] = []
        self.chain_asked: list[tuple] = []

    # The three readers the look-back makes, patched in.
    def read_status(self, repo, sha):
        self.reads.append(sha)
        return self.stamps.get(sha, ABSENT)

    def channel_head(self, repo):
        return self.channel

    def chain(self, branch, limit):
        self.chain_asked.append((branch, limit))
        return list(reversed(self.order))[:limit]

    def ancestry(self, sha):
        if sha not in self.order or self.channel not in self.order:
            return promote_channel.DIVERGED
        at, base = self.order.index(sha), self.order.index(self.channel)
        if at == base:
            return promote_channel.IDENTICAL
        return promote_channel.AHEAD if at > base else promote_channel.BEHIND


class Run:
    """One automatic promote run, its two decision steps run as the workflow
    runs them: `look-back` first, then the decision for whichever candidate it
    settled."""

    def __init__(self, trunk: Trunk, own: str, *, conclusion="failure",
                 woken_by=HARNESS, hold="", manual=False, force=False,
                 mirror=MIRROR_PASSED, actor="github-actions[bot]"):
        self.trunk, self.own = trunk, own
        self.conclusion, self.woken_by, self.hold = conclusion, woken_by, hold
        self.manual, self.force, self.mirror, self.actor = manual, force, mirror, actor
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def close(self):
        self._tmp.cleanup()

    def _main(self, argv):
        out = self.tmp / "gh_output"
        out.write_text("")
        printed = io.StringIO()
        old = os.getcwd()
        os.chdir(self.tmp)
        os.environ["GITHUB_OUTPUT"] = str(out)
        try:
            with contextlib.redirect_stdout(printed), \
                    mock.patch.object(promote_channel, "_read_status",
                                      self.trunk.read_status), \
                    mock.patch.object(promote_channel, "_read_channel_head",
                                      self.trunk.channel_head), \
                    mock.patch.object(promote_channel, "_first_parent_chain",
                                      self.trunk.chain):
                rc = promote_channel.main(argv)
        finally:
            os.environ.pop("GITHUB_OUTPUT", None)
            os.chdir(old)
        assert rc == 0, rc
        written = dict(line.split("=", 1)
                       for line in out.read_text().splitlines() if line)
        return printed.getvalue(), written

    def _write(self, name, data):
        path = self.tmp / name
        if data is not None:
            path.write_text(json.dumps(data))
        elif path.exists():
            path.unlink()
        return str(path)

    def _reads_own_status(self):
        return (self.manual or self.woken_by == promote_channel.UNIT_SUITE
                or self.conclusion == "success")

    def _common(self):
        args = ["--hold", self.hold, "--conclusion", self.conclusion,
                "--branch", "main", "--woken-by", self.woken_by]
        if self.manual:
            args += ["--manual", "--actor", self.actor, "--reason", "by hand",
                     "--trunk", "behind"]
        if self.force:
            args += ["--force"]
        return args

    def look_back(self):
        own = self.trunk.stamps.get(self.own) if self._reads_own_status() else {}
        statuses = self._write("own_status.json", own if own is not None else {})
        return self._main(["look-back", "--sha", self.own,
                           "--statuses-file", statuses, "--repo", REPO,
                           "--default-branch", "main", *self._common()])

    def decide(self, candidate):
        """The `Decide` step for `candidate`: the proof's arguments plus the
        mirror result, over that candidate's own records."""
        looked = candidate != self.own
        fetched = looked or self._reads_own_status()
        stamp = self.trunk.stamps.get(candidate) if fetched else {}
        statuses = self._write("combined_status.json",
                               stamp if stamp is not None else {})
        unit = self._write("unit_suite.json",
                           self.trunk.units.get(candidate) or {})
        mirror = self._write("mirror_result.json", self.mirror)
        ancestry = self.trunk.ancestry(candidate) if fetched else ""
        return self._main(["--sha", candidate, "--statuses-file", statuses,
                           "--ancestry", ancestry, "--unit-suite", unit,
                           "--look-back", str(self.tmp / promote_channel.LOOK_BACK_FILE),
                           *self._common(), "--mirror-result", mirror])

    def __call__(self):
        _printed, looked = self.look_back()
        candidate = looked.get("candidate") or self.own
        printed, written = self.decide(candidate)
        return candidate, printed, written


class _RunCase(unittest.TestCase):
    def run_once(self, trunk, own, **kw):
        run = Run(trunk, own, **kw)
        self.addCleanup(run.close)
        return run()


# --------------------------------------------------------------------------- #
# The card's scenarios                                                         #
# --------------------------------------------------------------------------- #

class LooksBackTest(_RunCase):
    def test_a_failed_head_promotes_the_proven_commit_before_it(self):
        for conclusion, word in (
                ("failure", promote_channel.OUTCOME_FAILED),
                ("cancelled", promote_channel.OUTCOME_CANCELLED)):
            with self.subTest(conclusion=conclusion):
                trunk = Trunk((STABLE, PASSED, UNIT_GREEN),
                              (A, PASSED, UNIT_GREEN),
                              (B, FAILED, UNIT_GREEN))
                candidate, printed, written = self.run_once(
                    trunk, B, conclusion=conclusion)
                self.assertEqual(candidate, A)
                self.assertEqual(written["promote"], "true")
                self.assertEqual(written["outcome"],
                                 promote_channel.OUTCOME_PROMOTING)
                line = printed.strip()
                self.assertTrue(line.startswith(
                    f"{promote_channel.OUTCOME_PROMOTING}: "), line)
                self.assertIn(A, line)
                self.assertIn(B, line)
                self.assertIn(word, line)
                self.assertIn("2 commits read", line)
                self.assertEqual(written["reason"], " ".join(
                    line.split(": ", 1)[1].split()))

    def test_every_harness_refusal_looks_back(self):
        """The five refusals at the harness proof, each one B's own word."""
        cases = (
            (dict(conclusion="failure"), FAILED, promote_channel.OUTCOME_FAILED),
            (dict(conclusion="cancelled"), ABSENT,
             promote_channel.OUTCOME_CANCELLED),
            (dict(conclusion="success"), SANDBOX,
             promote_channel.OUTCOME_BLOCKED),
            (dict(conclusion="success"),
             _stamp("failure", f"{promote_channel.QUEUE_STALL_MARKER} — 3 rekicks"),
             promote_channel.OUTCOME_QUEUE_STALL),
            (dict(conclusion="success", woken_by=promote_channel.UNIT_SUITE),
             PENDING, promote_channel.OUTCOME_UNPROVEN),
        )
        for kw, own_stamp, word in cases:
            with self.subTest(word=word):
                trunk = Trunk((STABLE, PASSED, UNIT_GREEN),
                              (A, PASSED, UNIT_GREEN),
                              (B, own_stamp, UNIT_GREEN))
                candidate, printed, written = self.run_once(trunk, B, **kw)
                self.assertEqual(candidate, A)
                self.assertEqual(written["outcome"],
                                 promote_channel.OUTCOME_PROMOTING)
                self.assertIn(word, printed)

    def test_a_newer_commit_still_running_does_not_end_the_search(self):
        trunk = Trunk((STABLE, PASSED, UNIT_GREEN),
                      (A, PASSED, UNIT_GREEN),
                      (C, PENDING, UNIT_RUNNING),
                      (B, FAILED, UNIT_GREEN))
        candidate, printed, written = self.run_once(trunk, B)
        self.assertEqual(candidate, A)
        self.assertEqual(written["promote"], "true")
        self.assertEqual(written["outcome"], promote_channel.OUTCOME_PROMOTING)
        self.assertIn("3 commits read", printed)

    def test_the_first_proven_commit_ends_the_search(self):
        older = _sha(1)
        trunk = Trunk((STABLE, PASSED, UNIT_GREEN),
                      (older, PASSED, UNIT_GREEN),
                      (A, PASSED, UNIT_GREEN),
                      (B, FAILED, UNIT_GREEN))
        candidate, _printed, _written = self.run_once(trunk, B)
        self.assertEqual(candidate, A)
        self.assertNotIn(older, trunk.reads)

    def test_the_line_names_the_proving_run_never_b(self):
        """The release note is the workflow's (`HARNESS_RUN` empty on a
        look-back, below); the decision line never calls B's run A's proof."""
        trunk = Trunk((STABLE, PASSED, UNIT_GREEN), (A, PASSED, UNIT_GREEN),
                      (B, FAILED, UNIT_GREEN))
        _candidate, printed, _written = self.run_once(trunk, B)
        self.assertNotIn("harness run failed", printed)


class NothingProvenTest(_RunCase):
    UNPROVEN = (("pending", PENDING), ("failure", FAILED), ("error", ERROR),
                ("sandbox-blocked", SANDBOX), ("sandbox-blocked green", SANDBOX_GREEN),
                ("absent", ABSENT), ("unreadable", UNREADABLE))

    def test_no_unproven_stamp_is_ever_promoted(self):
        for name, stamp in self.UNPROVEN:
            with self.subTest(stamp=name):
                trunk = Trunk((STABLE, PASSED, UNIT_GREEN),
                              (A, stamp, UNIT_GREEN),
                              (B, FAILED, UNIT_GREEN))
                candidate, printed, written = self.run_once(trunk, B)
                self.assertEqual(candidate, B)
                self.assertEqual(written["promote"], "false")
                self.assertEqual(written["outcome"],
                                 promote_channel.OUTCOME_FAILED)
                self.assertIn(A, trunk.reads, "A was read and passed over")
                self.assertIn("no proven commit", printed)

    def test_the_pending_case_names_b_and_the_count(self):
        trunk = Trunk((STABLE, PASSED, UNIT_GREEN), (A, PENDING, UNIT_GREEN),
                      (B, FAILED, UNIT_GREEN))
        candidate, printed, written = self.run_once(trunk, B)
        self.assertEqual(candidate, B)
        self.assertEqual(written["promote"], "false")
        self.assertEqual(written["outcome"], promote_channel.OUTCOME_FAILED)
        line = printed.strip()
        self.assertTrue(line.startswith(f"{promote_channel.OUTCOME_FAILED}: "))
        self.assertIn(B, line)
        self.assertIn("no proven commit was found", line)
        self.assertIn("2 commits read", line)
        self.assertIn("stopped at stable", line)

    def test_proven_is_a_green_unblocked_stamp_and_nothing_else(self):
        self.assertTrue(promote_channel.proven(PASSED))
        for name, stamp in self.UNPROVEN:
            with self.subTest(stamp=name):
                self.assertFalse(promote_channel.proven(stamp))
        other = {"statuses": [{"context": "ci", "state": "success"}]}
        self.assertFalse(promote_channel.proven(other))

    def test_the_runs_own_commit_is_never_its_own_candidate(self):
        """A canceled run never promotes on a stamp an earlier run left
        behind — the rule `evaluate` reads the conclusion for — so B is
        passed over even when its stamp is green."""
        trunk = Trunk((STABLE, PASSED, UNIT_GREEN), (B, PASSED, UNIT_GREEN))
        candidate, _printed, written = self.run_once(
            trunk, B, conclusion="cancelled")
        self.assertEqual(candidate, B)
        self.assertEqual(written["promote"], "false")
        self.assertEqual(written["outcome"], promote_channel.OUTCOME_CANCELLED)
        self.assertNotIn(B, trunk.reads)


class HowFarTest(_RunCase):
    def test_the_search_reads_at_most_twenty_and_says_so(self):
        between = [_sha(n) for n in range(1, 21)]
        # Oldest first: stable, then the only proven commit, then 19 unproven
        # commits and B at the head — the proven one is 21st from the head.
        trunk = Trunk((STABLE, PASSED, UNIT_GREEN),
                      (_sha(99), PASSED, UNIT_GREEN),
                      *[(sha, FAILED, UNIT_GREEN) for sha in between[:19]],
                      (B, FAILED, UNIT_GREEN))
        self.assertEqual(list(reversed(trunk.order)).index(_sha(99)), 20)
        candidate, printed, written = self.run_once(trunk, B)
        self.assertEqual(candidate, B)
        self.assertEqual(written["promote"], "false")
        self.assertNotIn(_sha(99), trunk.reads)
        self.assertLessEqual(len(trunk.reads), promote_channel.LOOK_BACK_LIMIT)
        self.assertEqual(promote_channel.LOOK_BACK_LIMIT, 20)
        self.assertIn("20 commits read", printed)
        self.assertIn("the search stopped at 20", printed)
        self.assertEqual(trunk.chain_asked, [("main", 20)])

    def test_the_search_stops_at_stable_and_never_considers_it(self):
        old = _sha(7)
        trunk = Trunk((old, PASSED, UNIT_GREEN),
                      (STABLE, PASSED, UNIT_GREEN),
                      (C, PENDING, UNIT_GREEN),
                      (B, FAILED, UNIT_GREEN))
        candidate, printed, _written = self.run_once(trunk, B)
        self.assertEqual(candidate, B)
        self.assertNotIn(STABLE, trunk.reads)
        self.assertNotIn(old, trunk.reads)
        self.assertIn("2 commits read", printed)
        self.assertIn("stopped at stable", printed)

    def test_the_walk_alone(self):
        chain = [B, C, A, STABLE]
        stamps = {C: PENDING, A: PASSED}
        look = promote_channel.look_back(
            chain, stamps.get, own=B,
            own_outcome=promote_channel.OUTCOME_FAILED, channel_head=STABLE)
        self.assertEqual(look.candidate, A)
        self.assertEqual(look.read, 3)
        self.assertEqual(look.stopped, promote_channel.STOP_FOUND)
        empty = promote_channel.look_back(
            [], stamps.get, own=B,
            own_outcome=promote_channel.OUTCOME_FAILED, channel_head=STABLE)
        self.assertEqual(empty.candidate, "")
        self.assertEqual(empty.read, 0)


class FirstParentTest(unittest.TestCase):
    """A real repository: a merged branch's own commit is never a candidate."""

    def _git(self, *args, cwd):
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x"}
        return subprocess.run(["git", *args], cwd=cwd, env=env, check=True,
                              capture_output=True, text=True).stdout.strip()

    def test_only_the_first_parent_chain_is_walked(self):
        with tempfile.TemporaryDirectory() as tmp:
            git = lambda *a: self._git(*a, cwd=tmp)  # noqa: E731
            git("init", "-q", "-b", "main")
            git("commit", "-q", "--allow-empty", "-m", "stable")
            stable = git("rev-parse", "HEAD")
            git("checkout", "-q", "-b", "feature")
            git("commit", "-q", "--allow-empty", "-m", "branch work")
            branch = git("rev-parse", "HEAD")
            git("checkout", "-q", "main")
            git("commit", "-q", "--allow-empty", "-m", "another merge")
            other = git("rev-parse", "HEAD")
            git("merge", "-q", "--no-ff", "-m", "merge feature", "feature")
            merge = git("rev-parse", "HEAD")
            git("commit", "-q", "--allow-empty", "-m", "head")
            head = git("rev-parse", "HEAD")
            # `git log`, without --first-parent, lists the branch's commit.
            self.assertIn(branch, git("log", "--format=%H").split())

            chain = promote_channel.first_parent_chain("HEAD", 20, cwd=tmp)
            self.assertEqual(chain, [head, merge, other, stable])
            self.assertNotIn(branch, chain)
            self.assertEqual(promote_channel.first_parent_chain("HEAD", 2, cwd=tmp),
                             [head, merge])

            stamps = {branch: PASSED, merge: PENDING, other: FAILED}
            seen = []
            look = promote_channel.look_back(
                chain, lambda sha: seen.append(sha) or stamps.get(sha), own=head,
                own_outcome=promote_channel.OUTCOME_FAILED, channel_head=stable)
            self.assertEqual(look.candidate, "")
            self.assertNotIn(branch, seen)

    def test_an_unreadable_chain_is_an_empty_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                promote_channel.first_parent_chain("HEAD", 20, cwd=tmp), [])


# --------------------------------------------------------------------------- #
# A look-back candidate gets every gate                                        #
# --------------------------------------------------------------------------- #

class EveryGateTest(_RunCase):
    def test_the_candidates_own_unit_suite_is_weighed(self):
        for unit, word in ((UNIT_RED, promote_channel.OUTCOME_UNIT_RED),
                           (UNIT_RUNNING, promote_channel.OUTCOME_UNIT_NOT_FINISHED),
                           (None, promote_channel.OUTCOME_UNIT_UNPROVEN)):
            with self.subTest(word=word):
                trunk = Trunk((STABLE, PASSED, UNIT_GREEN), (A, PASSED, unit),
                              (B, FAILED, UNIT_GREEN))
                candidate, printed, written = self.run_once(trunk, B)
                self.assertEqual(candidate, A)
                self.assertEqual(written["promote"], "false")
                self.assertEqual(written["outcome"], word)
                self.assertIn(A, printed)
                self.assertIn(B, printed)

    def test_a_held_channel_answers_held_without_looking_back(self):
        trunk = Trunk((STABLE, PASSED, UNIT_GREEN), (A, PASSED, UNIT_GREEN),
                      (B, FAILED, UNIT_GREEN))
        candidate, printed, written = self.run_once(
            trunk, B, hold="who=Ada paused for the cut")
        self.assertEqual(candidate, B)
        self.assertEqual(written["outcome"], promote_channel.OUTCOME_HELD)
        self.assertEqual(trunk.reads, [])
        self.assertEqual(trunk.chain_asked, [])
        self.assertNotIn("ooked back", printed)

    def test_the_mirror_check_is_weighed(self):
        trunk = Trunk((STABLE, PASSED, UNIT_GREEN), (A, PASSED, UNIT_GREEN),
                      (B, FAILED, UNIT_GREEN))
        candidate, _printed, written = self.run_once(trunk, B, mirror=None)
        self.assertEqual(candidate, A)
        self.assertEqual(written["outcome"], promote_channel.OUTCOME_MIRROR_BLOCKED)

    def test_the_ancestry_rail_is_weighed(self):
        """`stable` off the chain (a by-hand promote of a branch commit): the
        walk reaches A, and the compare refuses it."""
        trunk = Trunk((A, PASSED, UNIT_GREEN), (B, FAILED, UNIT_GREEN),
                      channel=_sha(42))
        candidate, _printed, written = self.run_once(trunk, B)
        self.assertEqual(candidate, A)
        self.assertEqual(written["outcome"], promote_channel.OUTCOME_NOT_AHEAD)

    def test_refusals_outside_the_harness_proof_never_look_back(self):
        for decision in (
                promote_channel.Decision(False, "x", promote_channel.OUTCOME_HELD),
                promote_channel.Decision(False, "x", promote_channel.OUTCOME_NOT_MAIN),
                promote_channel.Decision(False, "x", promote_channel.OUTCOME_NOT_AHEAD),
                promote_channel.Decision(False, "x", promote_channel.OUTCOME_UNIT_RED),
                promote_channel.Decision(False, "x", promote_channel.OUTCOME_UNIT_NOT_FINISHED),
                promote_channel.Decision(False, "x", promote_channel.OUTCOME_UNIT_UNPROVEN),
                promote_channel.Decision(False, "x", promote_channel.OUTCOME_MIRROR_FAILED),
                promote_channel.Decision(False, "x", promote_channel.OUTCOME_MIRROR_BLOCKED),
                promote_channel.Decision(True, "x", promote_channel.OUTCOME_PROMOTING)):
            with self.subTest(outcome=decision.outcome):
                self.assertFalse(promote_channel.looks_back(decision, manual=False))
        for outcome in promote_channel.LOOK_BACK_OUTCOMES:
            decision = promote_channel.Decision(False, "x", outcome)
            self.assertTrue(promote_channel.looks_back(decision, manual=False))
            self.assertFalse(promote_channel.looks_back(decision, manual=True))
        self.assertEqual(set(promote_channel.LOOK_BACK_OUTCOMES), {
            "harness-cancelled-by-newer-push", "harness-failed",
            "harness-blocked-by-sandbox", "harness-blocked-by-github-queue",
            "no-harness-stamp"})


# --------------------------------------------------------------------------- #
# Force                                                                        #
# --------------------------------------------------------------------------- #

class ForceTest(_RunCase):
    def test_a_stray_force_on_the_automatic_route_reaches_nothing(self):
        for name, stamp in NothingProvenTest.UNPROVEN:
            with self.subTest(stamp=name):
                trunk = Trunk((STABLE, PASSED, UNIT_GREEN), (A, stamp, UNIT_RED),
                              (B, FAILED, UNIT_GREEN))
                _candidate, _printed, written = self.run_once(
                    trunk, B, force=True, mirror=None)
                self.assertEqual(written["promote"], "false")
                self.assertNotEqual(written["outcome"],
                                    promote_channel.OUTCOME_BY_HAND_FORCED)

    def test_a_forged_record_naming_an_unproven_commit_promotes_nothing(self):
        """The decision re-reads the candidate's own stamp: a look-back record
        is where to look, never proof."""
        run = Run(Trunk((STABLE, PASSED, UNIT_GREEN), (A, FAILED, UNIT_GREEN),
                        (B, FAILED, UNIT_GREEN)), B, force=True)
        self.addCleanup(run.close)
        (run.tmp / promote_channel.LOOK_BACK_FILE).write_text(json.dumps({
            "own": B, "own_outcome": promote_channel.OUTCOME_FAILED,
            "candidate": A, "read": 2, "stopped": promote_channel.STOP_FOUND}))
        _printed, written = run.decide(A)
        self.assertEqual(written["promote"], "false")
        self.assertEqual(written["outcome"], promote_channel.OUTCOME_UNPROVEN)

    def test_a_dispatch_never_looks_back(self):
        for own in (B, A):  # a blank sha resolves to main's head; or a named one
            with self.subTest(sha=own):
                trunk = Trunk((STABLE, PASSED, UNIT_GREEN), (A, FAILED, UNIT_GREEN),
                              (B, FAILED, UNIT_GREEN))
                run = Run(trunk, own, conclusion="", woken_by="", manual=True,
                          actor="sid")
                self.addCleanup(run.close)
                printed, looked = run.look_back()
                self.assertEqual(looked.get("candidate", ""), "")
                self.assertEqual(trunk.reads, [])
                self.assertEqual(trunk.chain_asked, [])
                self.assertFalse((run.tmp / promote_channel.LOOK_BACK_FILE).exists())

    def test_a_dispatch_ignores_a_look_back_record(self):
        run = Run(Trunk((STABLE, PASSED, UNIT_GREEN), (B, PASSED, UNIT_GREEN)),
                  B, conclusion="", woken_by="", manual=True, actor="sid")
        self.addCleanup(run.close)
        (run.tmp / promote_channel.LOOK_BACK_FILE).write_text(json.dumps({
            "own": B, "own_outcome": promote_channel.OUTCOME_FAILED,
            "candidate": "", "read": 2, "stopped": promote_channel.STOP_CHANNEL}))
        printed, written = run.decide(B)
        self.assertEqual(written["outcome"], promote_channel.OUTCOME_BY_HAND)
        self.assertNotIn("ooked back", printed)

    def test_a_forced_dispatch_by_a_bot_is_still_refused(self):
        trunk = Trunk((STABLE, PASSED, UNIT_GREEN), (B, FAILED, UNIT_GREEN))
        run = Run(trunk, B, conclusion="", woken_by="", manual=True, force=True,
                  actor="agent-bureau-bot[bot]")
        self.addCleanup(run.close)
        run.look_back()
        _printed, written = run.decide(B)
        self.assertEqual(written["promote"], "false")
        self.assertEqual(written["outcome"],
                         promote_channel.OUTCOME_FORCE_NOT_OPERATOR)


# --------------------------------------------------------------------------- #
# A passing B is untouched                                                     #
# --------------------------------------------------------------------------- #

class UnchangedTest(_RunCase):
    def test_a_passing_commit_makes_no_look_back_read(self):
        for woken_by in (HARNESS, promote_channel.UNIT_SUITE):
            with self.subTest(woken_by=woken_by):
                trunk = Trunk((STABLE, PASSED, UNIT_GREEN), (A, PASSED, UNIT_GREEN),
                              (B, PASSED, UNIT_GREEN))
                candidate, printed, written = self.run_once(
                    trunk, B, conclusion="success", woken_by=woken_by)
                self.assertEqual(candidate, B)
                self.assertEqual(trunk.reads, [])
                self.assertEqual(trunk.chain_asked, [])
                reason = f"promoting stable to {B} — harness green, strictly ahead."
                self.assertTrue(printed.startswith(
                    f"{promote_channel.OUTCOME_PROMOTING}: {reason} Mirror check:"),
                    printed)
                self.assertNotIn("ooked back", printed)

    def test_no_record_reads_exactly_as_before(self):
        run = Run(Trunk((STABLE, PASSED, UNIT_GREEN), (B, PASSED, UNIT_GREEN)),
                  B, conclusion="success", mirror=None)
        self.addCleanup(run.close)
        statuses = run._write("combined_status.json", PASSED)
        unit = run._write("unit_suite.json", UNIT_GREEN)
        printed, written = run._main([
            "--sha", B, "--statuses-file", statuses, "--ancestry", "ahead",
            "--unit-suite", unit, "--conclusion", "success", "--branch", "main",
            "--look-back", str(run.tmp / "absent.json")])
        reason = f"promoting stable to {B} — harness green, strictly ahead."
        self.assertEqual(printed, f"harness-passed-promoting: {reason}\n")
        self.assertEqual(written, {"promote": "true", "reason": reason,
                                   "outcome": "harness-passed-promoting"})


# --------------------------------------------------------------------------- #
# The workflow                                                                 #
# --------------------------------------------------------------------------- #

class WorkflowShapeTest(unittest.TestCase):
    def setUp(self):
        self.steps = yaml.safe_load(WORKFLOW.read_text())["jobs"]["promote"]["steps"]
        self.names = [s.get("name") for s in self.steps]

    def _index(self, pred, what):
        for i, step in enumerate(self.steps):
            if pred(step):
                return i
        self.fail(f"no step {what}")

    def _look_back(self):
        return self._index(lambda s: s.get("id") == "lookback", "lookback")

    def test_the_look_back_settles_the_candidate_before_any_record_is_read(self):
        at = self._look_back()
        self.assertIn("promote_channel look-back", self.steps[at]["run"])
        later = (
            lambda s: s.get("id") == "resolve",
            lambda s: s.get("name") == "Fetch the candidate's combined commit status",
            lambda s: s.get("id") == "ancestry",
            lambda s: "actions/workflows/tests.yml/runs" in str(s.get("run", "")),
            lambda s: s.get("id") == "proof",
            lambda s: "mirror_check.py run" in str(s.get("run", "")),
        )
        for pred in later:
            self.assertLess(at, self._index(pred, "reading a record"))

    def test_the_candidate_is_the_look_back_one_when_there_is_one(self):
        resolve = self.steps[self._index(lambda s: s.get("id") == "resolve", "resolve")]
        self.assertIn("steps.lookback.outputs.candidate", str(resolve.get("env")))
        self.assertIn("steps.lookback.outputs.candidate != ''",
                      _flat(resolve.get("if")))

    def test_the_record_steps_run_for_a_look_back_candidate(self):
        for name in ("Which commit is this run about?", "Mint bot token",
                     "Fetch the candidate's combined commit status",
                     "Compare the candidate against the current channel head",
                     "Read the candidate's own unit suite"):
            with self.subTest(step=name):
                step = self.steps[self.names.index(name)]
                self.assertIn("steps.lookback.outputs.candidate != ''",
                              _flat(step.get("if")))

    def test_the_look_back_runs_on_main_and_never_on_a_dispatch(self):
        cond = _flat(self.steps[self._look_back()].get("if"))
        self.assertIn("github.event_name == 'workflow_run'", cond)
        self.assertIn("head_branch == 'main'", cond)
        self.assertNotIn("workflow_dispatch", cond)
        run = self.steps[self._look_back()]["run"]
        self.assertNotIn("--manual", run)
        self.assertNotIn("--force", run)

    def test_the_look_back_judges_b_on_the_records_the_proof_reads(self):
        step = self.steps[self._look_back()]
        env = _flat(step.get("env"))
        for wanted in ("vars.CHANNEL_HOLD", "workflow_run.conclusion",
                       "workflow_run.name", "workflow_run.head_branch",
                       "workflow_run.head_sha"):
            self.assertIn(wanted, env)
        for flag in ("--hold", "--conclusion", "--branch", "--woken-by",
                     "--statuses-file"):
            self.assertIn(flag, step["run"])

    def test_both_decisions_read_the_look_back_record(self):
        proof = self.steps[self._index(lambda s: s.get("id") == "proof", "proof")]
        run = proof["run"]
        saved = run[run.index("ARGS=(--sha"):run.index("decide-args")]
        self.assertIn("--look-back", saved)
        self.assertIn(promote_channel.LOOK_BACK_FILE, saved)

    def test_a_look_back_promotion_hands_the_release_no_starting_run(self):
        linear = self.steps[self.names.index("Write the Linear release")]["env"]
        for key in ("HARNESS_RUN", "HARNESS_AT"):
            value = _flat(linear[key])
            self.assertIn("steps.lookback.outputs.candidate == ''", value, key)
            self.assertTrue(value.endswith("|| '' }}"), value)
        # The release reads the proving run off the candidate's own status.
        self.assertIn("--statuses-file combined_status.json",
                      self.steps[self.names.index("Write the Linear release")]["run"])


class DocsTest(unittest.TestCase):
    def test_queue_behind_says_a_canceled_pending_run_is_covered(self):
        doc = DOC.read_text()
        start = doc.index("## Queue behind, never cancel")
        # The paragraphs directly under the heading, before its first `###`.
        section = _flat(doc[start:doc.index("\n### ", start)])
        self.assertIn("DRE-9069", section)
        self.assertIn("look-back", section)
        self.assertIn("canceled while pending", section)


if __name__ == "__main__":
    unittest.main()
