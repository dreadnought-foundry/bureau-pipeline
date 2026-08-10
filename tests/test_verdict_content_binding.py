"""Verdict↔CONTENT binding (DRE-2340) — a branch update must not destroy the
APPROVE it just earned.

THE LIVELOCK (portico PR #205, verified 2026-08-09). Nine commits, six of
them `Merge branch 'main' into agent/…` authored by the gate itself; nine
critic verdicts, seven APPROVE. Every APPROVE landed, the gate acted ~20s
later, `update-branch` moved the head, the SHA binding (DRE-1990) stopped
binding, and the critic ran again. Six complete, fully paid-for reviews
thrown away — because portico took 30 commits to `main` in that 47-minute
window and a review plus CI takes 4-6 minutes. The branch is stale again
before the review it triggered can finish.

Neither half is wrong: the gate must update a stale branch (DRE-1924 —
semantic conflicts merge cleanly and still turn main red), and the verdict
must bind what the critic actually read (DRE-1990). Together, on a busy
repo, they livelock.

THE FIX: bind the verdict to the CONTENT the critic reviewed, not to the
commit SHA that happened to carry it. `GET compare/{base}...{head}` is the
THREE-DOT comparison, so its `files[]` is the diff from
merge_base(base, head) to head — exactly the PR's own contribution, exactly
what the critic reads and exactly what lands on main. A gate-initiated
merge of `main` into the branch does not change that set, so the verdict
survives it. Anything that touches the PR's own diff — one added line, an
evil merge, a reverted base change, a base commit touching a file the
branch also touches — changes a blob SHA in that set, changes the id, and
kills the verdict.

What this suite proves, in the order the card asks for it:

  1. content_id() returns None — never a partial hash — for every
     incomplete record, including the 300-file cap GitHub does not
     paginate (fail closed: the one way unreviewed code could ride in).
  2. The two load-bearing properties, EMPIRICALLY, against a real temp git
     repo — the REST docs do not state the three-dot behaviour explicitly,
     so it is not taken on faith.
  3. The gate honours a carried verdict only under all four conditions,
     and today's behaviour is untouched everywhere else.
  4. The verdict DIES for every content-changing case and SURVIVES a base
     merge that touches nothing the branch touches (and an empty commit).
  5. One implementation: merge_gate, should_review_pr and reconcile all
     read the binding through scripts/verdict_content.py.
  6. The PR #205 interleaving, replayed, costs ONE critic run.
"""

from __future__ import annotations

import inspect
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
MERGE_GATE_YML = WORKFLOWS / "merge-gate.yml"
QA_REVIEW_YML = WORKFLOWS / "qa-review.yml"
VERIFY_YML = WORKFLOWS / "verify.yml"
SCRIPT = ROOT / "scripts" / "merge_gate.py"
SHOULD_REVIEW = ROOT / "scripts" / "should_review_pr.py"

sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("REPO", "test/test")
os.environ.setdefault("REPO_SLUG", "test")

import merge_gate  # noqa: E402
import reconcile  # noqa: E402
import should_review_pr  # noqa: E402
import verdict_content  # noqa: E402

HEAD = "aa11" * 10  # the PR's current head
REVIEWED = "bb22" * 10  # the commit the critic actually read
FOREIGN = "cc33" * 10  # a commit that is NOT in this PR's history

QA_LOGIN = "agent-bureau-qa-bot[bot]"
WORKER_LOGIN = "agent-bureau-bot[bot]"
GH_QA_LOGIN = "agent-bureau-qa-bot"  # GraphQL shape (reconcile's payload)

CID = "d" * 64  # the content id both sides compute
OTHER_CID = "e" * 64  # a different content id

GREEN_CI = [{"name": "unit", "status": "completed", "conclusion": "success",
             "check_suite": {"id": 1}}]

#: The PR's own commit record (GET pulls/{pr}/commits) — condition 4 proves
#: the reviewed commit is still in this PR's history.
PR_COMMITS = [{"sha": REVIEWED}, {"sha": HEAD}]


def critic_line(verdict, sha=None, cid=None):
    line = f"🔎 QA Critic — VERDICT: {verdict}"
    if sha:
        line += f" @{sha}"
    if cid:
        line += f" content:{cid}"
    return line


def verifier_line(verdict, sha=None, cid=None):
    line = f"🧪 QA Verifier — VERDICT: {verdict}"
    if sha:
        line += f" @{sha}"
    if cid:
        line += f" content:{cid}"
    return line


def comment(login, body):
    return {"user": {"login": login, "type": "Bot"}, "body": body}


def compare(files, status="ahead", merge_base="f" * 40):
    """A compare payload of the shape GET compare/{base}...{head} returns."""
    return {
        "status": status,
        "merge_base_commit": {"sha": merge_base},
        "files": files,
    }


def f(name, sha, status="modified", previous=None):
    entry = {"filename": name, "sha": sha, "status": status}
    if previous:
        entry["previous_filename"] = previous
    return entry


# ── 1. content_id(): fail closed on anything not provably complete ───────
class ContentIdTest(unittest.TestCase):
    """The id is a sha256 over the sorted (filename, previous_filename,
    status, sha) tuples of the compare record's files[] — or None. There is
    no third outcome: a partial hash would let unreviewed code ride in."""

    def test_stable_over_a_complete_record(self):
        payload = compare([f("a.py", "1" * 40), f("b.py", "2" * 40)])
        first = verdict_content.content_id(payload)
        self.assertIsNotNone(first)
        self.assertRegex(first, r"^[0-9a-f]{64}$")
        # Same record, files listed in the other order — GitHub does not
        # promise an ordering, so the id must not depend on one.
        shuffled = compare([f("b.py", "2" * 40), f("a.py", "1" * 40)])
        self.assertEqual(verdict_content.content_id(shuffled), first)

    def test_a_changed_blob_changes_the_id(self):
        base = verdict_content.content_id(compare([f("a.py", "1" * 40)]))
        edited = verdict_content.content_id(compare([f("a.py", "9" * 40)]))
        self.assertNotEqual(base, edited)

    def test_an_added_file_changes_the_id(self):
        base = verdict_content.content_id(compare([f("a.py", "1" * 40)]))
        wider = verdict_content.content_id(
            compare([f("a.py", "1" * 40), f("c.py", "3" * 40, "added")])
        )
        self.assertNotEqual(base, wider)

    def test_a_status_flip_changes_the_id(self):
        """Same path, same blob, different status (added vs modified) is a
        different contribution — e.g. a file main deleted that the branch
        re-adds."""
        self.assertNotEqual(
            verdict_content.content_id(compare([f("a.py", "1" * 40, "added")])),
            verdict_content.content_id(compare([f("a.py", "1" * 40, "modified")])),
        )

    def test_a_rename_changes_the_id(self):
        self.assertNotEqual(
            verdict_content.content_id(
                compare([f("new.py", "1" * 40, "renamed", previous="old.py")])
            ),
            verdict_content.content_id(compare([f("new.py", "1" * 40, "added")])),
        )

    def test_missing_files_key_is_none(self):
        payload = compare([])
        del payload["files"]
        self.assertIsNone(verdict_content.content_id(payload))

    def test_files_not_a_list_is_none(self):
        self.assertIsNone(verdict_content.content_id(compare({"a.py": "x"})))

    def test_three_hundred_files_is_none_the_uncounted_cap(self):
        """GitHub shows "up to 300 changed files for the ENTIRE comparison"
        and does NOT paginate files[]. At the cap the record may be
        truncated, so anything past file 300 could change without changing
        the id — the one way unreviewed code could ride in. Fail closed."""
        at_cap = [f(f"f{i}.py", f"{i:040x}") for i in range(300)]
        self.assertIsNone(verdict_content.content_id(compare(at_cap)))
        self.assertIsNone(verdict_content.content_id(compare(at_cap + at_cap[:1])))
        # 299 is provably complete and still hashes.
        self.assertIsNotNone(verdict_content.content_id(compare(at_cap[:299])))

    def test_entry_missing_filename_or_sha_is_none(self):
        self.assertIsNone(verdict_content.content_id(
            compare([{"sha": "1" * 40, "status": "modified"}])))
        self.assertIsNone(verdict_content.content_id(
            compare([{"filename": "a.py", "status": "modified"}])))
        self.assertIsNone(verdict_content.content_id(
            compare([f("a.py", "1" * 40), {"filename": "b.py"}])))

    def test_missing_merge_base_is_none(self):
        payload = compare([f("a.py", "1" * 40)])
        del payload["merge_base_commit"]
        self.assertIsNone(verdict_content.content_id(payload))
        payload = compare([f("a.py", "1" * 40)], merge_base="")
        self.assertIsNone(verdict_content.content_id(payload))

    def test_unknown_status_is_none(self):
        for status in (None, "", "garbage"):
            self.assertIsNone(
                verdict_content.content_id(
                    compare([f("a.py", "1" * 40)], status=status)),
                f"status={status!r} must not yield an id",
            )
        for status in ("ahead", "behind", "diverged", "identical"):
            self.assertIsNotNone(
                verdict_content.content_id(
                    compare([f("a.py", "1" * 40)], status=status)),
                f"status={status!r} is a real compare status",
            )

    def test_the_blip_substitute_is_none(self):
        """merge-gate.yml substitutes `{}` when the compare API blips. That
        must keep yielding BOTH an unknown branch-currency status and no
        content id — fail closed on both axes."""
        self.assertIsNone(verdict_content.content_id({}))
        self.assertIsNone(verdict_content.content_id(None))
        self.assertIsNone(verdict_content.content_id([]))

    def test_verdict_content_id_reads_the_producers_field(self):
        line = critic_line("APPROVE", REVIEWED, CID)
        self.assertEqual(verdict_content.verdict_content_id(line), CID)
        self.assertIsNone(
            verdict_content.verdict_content_id(critic_line("APPROVE", REVIEWED))
        )
        # Never a partial read: a truncated or over-long field is no field.
        self.assertIsNone(
            verdict_content.verdict_content_id(f"… @{REVIEWED} content:{'d' * 63}")
        )
        self.assertIsNone(
            verdict_content.verdict_content_id(f"… @{REVIEWED} content:{'d' * 65}")
        )

    def test_content_field_does_not_disturb_the_sha_parse(self):
        """Trap 2: `_SHA_RE` is a search for @<40-hex> ANYWHERE on the line
        and `_verdict_re` is an anchored match. Appending the content field
        must leave both reading exactly what they read today, and must never
        introduce a second @<40-hex>."""
        line = critic_line("APPROVE", REVIEWED, CID)
        self.assertEqual(merge_gate.verdict_sha(line), REVIEWED)
        self.assertEqual(
            merge_gate.verdict_token(line, merge_gate.CRITIC_MARKER), "APPROVE"
        )
        self.assertEqual(len(re.findall(r"@[0-9a-f]{40}", line)), 1)


# ── 2. the two properties, proved against a real git repo ────────────────
class GitCompareSemanticsTest(unittest.TestCase):
    """The whole design rests on two claims about
    `compare/{base}...{head}` that the REST docs do not state explicitly.
    Prove them empirically against real git, the way
    test_merge_gate_branch_currency.ReproductionTest does — never on faith.

      (a) THREE-DOT: files[] is the diff from merge_base(base, head) to
          head — the PR's own contribution, not base's movement.
      (b) A FUNCTION OF head ALONE: for a fixed head H, merge_base(X, H) is
          the same commit for every X descending from the last base commit
          H already contains. So the id computed at review time and the id
          computed at decision time agree by construction — no clock, no
          race, no shared state.
    """

    _STATUS = {"A": "added", "D": "removed", "M": "modified",
               "R": "renamed", "C": "copied", "T": "changed"}

    def git(self, *args):
        proc = subprocess.run(
            ["git", "-C", str(self.repo), *args], capture_output=True, text=True
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout.strip()

    def write(self, name, text):
        (self.repo / name).write_text(text)
        self.git("add", name)

    @staticmethod
    def app(value, extra=""):
        """app.py, padded so a top edit and a bottom append don't collide."""
        filler = "".join(f"# padding {i}\n" for i in range(12))
        return f"def widget():\n    return {value}\n\n{filler}{extra}"

    def commit(self, message):
        self.git("commit", "-qm", message)
        return self.git("rev-parse", "HEAD")

    def compare_payload(self, base, head):
        """GitHub's compare/{base}...{head} record, reconstructed from the
        same git plumbing GitHub serves it from: the THREE-DOT diff
        (merge-base → head), each entry carrying the blob sha."""
        merge_base = self.git("merge-base", base, head)
        raw = self.git("diff", "--raw", "--abbrev=40", "-M", merge_base, head)
        files = []
        for line in raw.splitlines():
            if not line.startswith(":"):
                continue
            meta, *paths = line.split("\t")
            _mode_src, _mode_dst, sha_src, sha_dst, status = meta[1:].split()
            letter = status[0]
            entry = {
                "filename": paths[-1],
                "status": self._STATUS.get(letter, "changed"),
                "sha": sha_src if letter == "D" else sha_dst,
            }
            if letter in ("R", "C"):
                entry["previous_filename"] = paths[0]
            files.append(entry)
        ahead = int(self.git("rev-list", "--count", f"{base}..{head}"))
        behind = int(self.git("rev-list", "--count", f"{head}..{base}"))
        status = ("diverged" if ahead and behind
                  else "behind" if behind else "ahead" if ahead else "identical")
        return {"status": status,
                "merge_base_commit": {"sha": merge_base},
                "files": files}

    def cid(self, base, head):
        return verdict_content.content_id(self.compare_payload(base, head))

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        self.git("init", "-q")
        self.git("config", "user.email", "t@t")
        self.git("config", "user.name", "t")
        self.git("config", "commit.gpgsign", "false")
        self.git("checkout", "-q", "-b", "main")
        # A base main with two files the branch will and will not touch.
        # app.py is padded so an edit at the top and an append at the
        # bottom merge cleanly — this suite is about content ids, not about
        # git's conflict heuristics (the evil-merge case below forces a
        # real conflict deliberately).
        self.write("app.py", self.app(1))
        self.write("unrelated.py", "OTHER = 1\n")
        self.commit("base")
        # The PR: its own contribution is a change to app.py.
        self.git("checkout", "-q", "-b", "pr")
        self.write("app.py", self.app(2))
        self.commit("the PR's own change")
        self.reviewed = self.git("rev-parse", "HEAD")
        self.reviewed_cid = self.cid("main", "pr")
        self.assertIsNotNone(self.reviewed_cid)

    def tearDown(self):
        self._tmp.cleanup()

    def advance_main(self, name, text, message):
        """A commit lands on main from other work (30 of them in 47 minutes
        on portico, one every 95 seconds)."""
        self.git("checkout", "-q", "main")
        self.write(name, text)
        sha = self.commit(message)
        self.git("checkout", "-q", "pr")
        return sha

    # (a) three-dot
    def test_files_are_the_prs_own_contribution_not_bases_movement(self):
        self.advance_main("unrelated.py", "OTHER = 2\n", "other work")
        payload = self.compare_payload("main", "pr")
        self.assertEqual(payload["status"], "diverged")
        self.assertEqual(
            [e["filename"] for e in payload["files"]], ["app.py"],
            "three-dot: main's own movement must NOT appear in files[]",
        )

    # (b) a function of head alone
    def test_id_is_a_function_of_head_alone(self):
        """The id computed at review time (base = main then) and at
        decision time (base = main after 3 more merges) must agree."""
        at_review = self.cid("main", "pr")
        base_at_review = self.git("rev-parse", "main")
        for i in range(3):
            self.advance_main("unrelated.py", f"OTHER = {i + 2}\n", f"other {i}")
        self.assertEqual(self.cid("main", "pr"), at_review)
        self.assertEqual(self.cid(base_at_review, "pr"), at_review)

    # ── the verdict SURVIVES ─────────────────────────────────────────────
    def test_survives_a_base_merge_touching_only_untouched_files(self):
        """THE case this card exists for: the gate merges main into the
        branch. The head moves, the PR's own contribution does not."""
        self.advance_main("unrelated.py", "OTHER = 2\n", "other work")
        self.git("merge", "-q", "--no-edit", "main")
        self.assertNotEqual(self.git("rev-parse", "HEAD"), self.reviewed)
        self.assertEqual(self.cid("main", "pr"), self.reviewed_cid)

    def test_survives_an_empty_commit(self):
        """reconcile.retrigger_dead_heads pushes an empty commit to re-arm a
        dead head. The merged result is byte-identical — which is exactly
        what this binding claims — so the verdict must survive it."""
        self.git("commit", "-q", "--allow-empty", "-m", "re-arm the head")
        self.assertNotEqual(self.git("rev-parse", "HEAD"), self.reviewed)
        self.assertEqual(self.cid("main", "pr"), self.reviewed_cid)

    def test_survives_repeated_base_merges_the_portico_205_shape(self):
        """Six gate update-branch merges in 47 minutes; the id never moves."""
        for i in range(6):
            self.advance_main("unrelated.py", f"OTHER = {i + 2}\n", f"main {i}")
            self.git("merge", "-q", "--no-edit", "main")
            self.assertEqual(self.cid("main", "pr"), self.reviewed_cid,
                             f"id moved on base merge {i}")

    # ── the verdict DIES ─────────────────────────────────────────────────
    def test_dies_on_an_added_line(self):
        self.write("app.py", self.app(2, extra="EXTRA = True\n"))
        self.commit("one more line")
        self.assertNotEqual(self.cid("main", "pr"), self.reviewed_cid)

    def test_dies_on_a_base_merge_touching_a_file_the_branch_touches(self):
        """If main touched a file the branch also touches, the merged blob
        differs — the verdict correctly dies and a fresh review is required.
        The two edits merge CLEANLY (no conflict); the id still moves."""
        self.advance_main("app.py", self.app(1, extra="HELPER = 1\n"),
                          "main touches app.py too")
        self.git("merge", "-q", "--no-edit", "main")
        self.assertNotEqual(self.cid("main", "pr"), self.reviewed_cid)

    def test_dies_on_an_evil_merge(self):
        """A conflict resolved by INSERTING NEW CODE in the merge commit —
        code no critic ever read. You cannot add code without changing
        content."""
        self.git("checkout", "-q", "main")
        (self.repo / "app.py").write_text(self.app(3))
        self.git("add", "app.py")
        self.commit("main edits the same line")
        self.git("checkout", "-q", "pr")
        proc = subprocess.run(
            ["git", "-C", str(self.repo), "merge", "--no-edit", "main"],
            capture_output=True, text=True,
        )
        self.assertNotEqual(proc.returncode, 0, "expected a real conflict")
        (self.repo / "app.py").write_text(
            self.app(2, extra="BACKDOOR = True\n")
        )
        self.git("add", "app.py")
        self.git("commit", "-qm", "resolve the conflict (evil merge)")
        self.assertNotEqual(self.cid("main", "pr"), self.reviewed_cid)

    def test_dies_on_a_reverted_base_change(self):
        """The branch merges main and then reverts one of main's changes:
        the revert IS part of the PR's contribution now, so the three-dot
        set grows a file and the id moves."""
        self.advance_main("unrelated.py", "OTHER = 2\n", "other work")
        self.git("merge", "-q", "--no-edit", "main")
        self.assertEqual(self.cid("main", "pr"), self.reviewed_cid)
        self.write("unrelated.py", "OTHER = 1\n")
        self.commit("revert main's change on the branch")
        self.assertNotEqual(self.cid("main", "pr"), self.reviewed_cid)


# ── 3. the gate honours a carried verdict, under all four conditions ─────
class CriticCarryTest(unittest.TestCase):
    """evaluate_critic. The sha == head path is UNCHANGED, byte for byte —
    it is the common path. A verdict for a different sha binds ONLY when
    all four conditions hold.

      verdict                    | head id | in PR commits | expected
      ---------------------------+---------+---------------+----------
      APPROVE @head              |    —    |       —       | proceed
      APPROVE @old + content:X   |    X    |      yes      | proceed  ← new
      APPROVE @old + content:X   |    X    |      NO       | wait
      APPROVE @old + content:X   |    Y    |      yes      | wait
      APPROVE @old + content:X   |  None   |      yes      | wait
      APPROVE @old (no content)  |    X    |      yes      | wait     ← pinned
      REQUEST_CHANGES @old + X   |    X    |      yes      | hold (never APPROVE)
    """

    def gate(self, line, head=HEAD, cid=None, commits=PR_COMMITS):
        return merge_gate.evaluate_critic(
            line, head, cid,
            frozenset(c["sha"] for c in commits),
        )

    def test_matching_sha_is_unchanged(self):
        self.assertIsNone(self.gate(critic_line("APPROVE", HEAD), cid=CID))
        self.assertIsNone(self.gate(critic_line("APPROVE", HEAD, CID), cid=CID))
        # …including with no content machinery in play at all.
        self.assertIsNone(self.gate(critic_line("APPROVE", HEAD), cid=None))

    def test_carries_when_all_four_conditions_hold(self):
        self.assertIsNone(self.gate(critic_line("APPROVE", REVIEWED, CID), cid=CID))

    def test_no_content_field_is_still_no_verdict(self):
        """PINNED: today's behaviour for every in-flight PR on the fleet —
        a verdict whose sha != head and which carries no content id is NO
        verdict. `@main` is production; this must be a strict superset."""
        got = self.gate(critic_line("APPROVE", REVIEWED), cid=CID)
        self.assertIsNotNone(got)
        self.assertEqual(got.action, "wait")
        self.assertIn("stale", got.reason)

    def test_gate_computed_no_id_does_not_carry(self):
        """A truncated/blipped compare record at the head → None → the gate
        falls back to SHA binding. Fail closed against truncation."""
        got = self.gate(critic_line("APPROVE", REVIEWED, CID), cid=None)
        self.assertIsNotNone(got)
        self.assertEqual(got.action, "wait")

    def test_mismatched_ids_do_not_carry(self):
        got = self.gate(critic_line("APPROVE", REVIEWED, CID), cid=OTHER_CID)
        self.assertIsNotNone(got)
        self.assertEqual(got.action, "wait")

    def test_reviewed_sha_must_be_in_the_prs_own_commit_record(self):
        """Condition 4: the reviewed commit must still be in THIS PR's
        history — proof it was not rewritten away, and that the verdict is
        not being replayed from somewhere else."""
        got = self.gate(critic_line("APPROVE", FOREIGN, CID), cid=CID)
        self.assertIsNotNone(got)
        self.assertEqual(got.action, "wait")

    def test_unavailable_commit_record_does_not_carry(self):
        """`[]` is the workflow's fail-closed substitute for a listing
        blip — never carry a verdict on unverifiable data."""
        got = self.gate(critic_line("APPROVE", REVIEWED, CID), cid=CID, commits=[])
        self.assertIsNotNone(got)
        self.assertEqual(got.action, "wait")

    def test_request_changes_never_becomes_an_approve(self):
        for cid, expected in ((CID, "hold"), (OTHER_CID, "wait"), (None, "wait")):
            got = self.gate(
                critic_line("REQUEST_CHANGES", REVIEWED, CID), cid=cid
            )
            self.assertIsNotNone(got)
            self.assertEqual(got.action, expected, f"head id {cid!r}")
            self.assertNotEqual(got.action, "merge")

    def test_neutral_status_carries_nothing(self):
        got = self.gate(
            "🔎 QA Critic could not run (infra error) — re-review needed.",
            cid=CID,
        )
        self.assertIsNotNone(got)


class VerifierCarryTest(unittest.TestCase):
    """Trap 1: fixing only the critic turns the livelock into a VERIFIER
    DEADLOCK on any repo in verifier scope — a present-but-stale verifier
    verdict HOLDs (the DRE-1990/DRE-1991 asymmetry), and a hold is not
    lifted by a fresh gate wake. The verifier gets the same carry rule and
    keeps holding on every non-matching case."""

    def gate(self, line, head=HEAD, cid=None, commits=PR_COMMITS):
        return merge_gate.evaluate_verifier(
            line, head, cid, frozenset(c["sha"] for c in commits)
        )

    def test_pass_carries_when_all_conditions_hold(self):
        got, _ = self.gate(verifier_line("PASS", REVIEWED, CID), cid=CID)
        self.assertIsNone(got)

    def test_skip_carries_and_stays_advisory(self):
        got, note = self.gate(verifier_line("SKIP", REVIEWED, CID), cid=CID)
        self.assertIsNone(got)
        self.assertIn("SKIP", note)

    def test_fail_carries_as_a_hold_never_a_pass(self):
        got, _ = self.gate(verifier_line("FAIL", REVIEWED, CID), cid=CID)
        self.assertIsNotNone(got)
        self.assertEqual(got.action, "hold")

    def test_every_non_matching_case_still_holds(self):
        cases = {
            "no content field": (verifier_line("PASS", REVIEWED), CID, PR_COMMITS),
            "gate computed no id": (verifier_line("PASS", REVIEWED, CID), None,
                                    PR_COMMITS),
            "ids differ": (verifier_line("PASS", REVIEWED, CID), OTHER_CID,
                           PR_COMMITS),
            "sha not in the PR": (verifier_line("PASS", FOREIGN, CID), CID,
                                  PR_COMMITS),
            "no commit record": (verifier_line("PASS", REVIEWED, CID), CID, []),
        }
        for label, (line, cid, commits) in cases.items():
            got, _ = self.gate(line, cid=cid, commits=commits)
            self.assertIsNotNone(got, label)
            self.assertEqual(got.action, "hold", label)

    def test_absent_verdict_is_still_not_a_gate(self):
        got, note = self.gate("", cid=CID)
        self.assertIsNone(got)
        self.assertIn("not a gate", note)


class DecideCarryTest(unittest.TestCase):
    """decide() threads the head content id and records the carry."""

    def decide(self, comments, cid=CID, compare_status="ahead",
               commits=PR_COMMITS, checks=None):
        return merge_gate.decide(
            head_sha=HEAD,
            qa_login=QA_LOGIN,
            check_runs=GREEN_CI if checks is None else checks,
            comments=comments,
            compare_status=compare_status,
            pr_commits=commits,
            head_content_id=cid,
        )

    def test_carried_approve_merges_and_names_all_three(self):
        d = self.decide([comment(QA_LOGIN, critic_line("APPROVE", REVIEWED, CID))])
        self.assertEqual(d.action, "merge", d.reason)
        self.assertIn(REVIEWED, d.reason)
        self.assertIn(HEAD, d.reason)
        self.assertIn(CID, d.reason)

    def test_the_carry_is_reported_for_the_pr_record(self):
        """Today `decision=update` posts NOTHING — which is why PR #205's
        six merge commits look inexplicable. A carry has to explain itself."""
        d = self.decide([comment(QA_LOGIN, critic_line("APPROVE", REVIEWED, CID))])
        self.assertTrue(d.carried, "the decision must record the carry")
        self.assertTrue(any(REVIEWED in c for c in d.carried))
        self.assertEqual(d.content_id, CID)

    def test_no_carry_recorded_on_the_ordinary_path(self):
        d = self.decide([comment(QA_LOGIN, critic_line("APPROVE", HEAD, CID))])
        self.assertEqual(d.action, "merge")
        self.assertEqual(d.carried, [])

    def test_forged_comment_with_a_valid_content_id_stays_invisible(self):
        """DRE-1987 authorship is untouched: the worker bot's perfect
        carry-shaped verdict is not a verdict at all."""
        d = self.decide([comment(WORKER_LOGIN, critic_line("APPROVE", REVIEWED, CID))])
        self.assertEqual(d.action, "wait")
        self.assertIn("no critic verdict yet", d.reason)

    def test_forged_carry_cannot_refresh_a_real_stale_verdict(self):
        d = self.decide([
            comment(QA_LOGIN, critic_line("APPROVE", REVIEWED)),
            comment(WORKER_LOGIN, critic_line("APPROVE", REVIEWED, CID)),
        ])
        self.assertEqual(d.action, "wait")

    def test_condition_zero_is_untouched_a_stale_head_still_never_merges(self):
        """Trap 5: this card changes what INVALIDATES a verdict, never what
        PERMITS a merge. A carried verdict on a stale branch is an `update`,
        and an unverifiable compare is still `wait`."""
        approve = [comment(QA_LOGIN, critic_line("APPROVE", REVIEWED, CID))]
        self.assertEqual(self.decide(approve, compare_status="behind").action,
                         "update")
        self.assertEqual(self.decide(approve, compare_status=None).action, "wait")

    def test_red_ci_still_beats_a_carried_approve(self):
        red = [{"name": "unit", "status": "completed", "conclusion": "failure",
                "check_suite": {"id": 1}}]
        d = self.decide(
            [comment(QA_LOGIN, critic_line("APPROVE", REVIEWED, CID))], checks=red
        )
        self.assertEqual(d.action, "wait")
        self.assertIn("not green", d.reason)

    def test_carried_critic_with_a_stale_verifier_still_holds(self):
        d = self.decide([
            comment(QA_LOGIN, critic_line("APPROVE", REVIEWED, CID)),
            comment(QA_LOGIN, verifier_line("PASS", REVIEWED)),
        ])
        self.assertEqual(d.action, "hold")


class NoContentIdParityTest(unittest.TestCase):
    """With no `content:` id present ANYWHERE, behaviour is byte-identical
    to today — the strict-superset requirement (`@main` is production and
    every in-flight PR on the fleet has verdicts with no content field)."""

    def decide(self, comments, **kw):
        return merge_gate.decide(
            head_sha=HEAD, qa_login=QA_LOGIN, check_runs=GREEN_CI,
            comments=comments, compare_status="ahead", **kw
        )

    def test_every_shape_matches_the_pre_card_call(self):
        shapes = [
            [],
            [comment(QA_LOGIN, critic_line("APPROVE", HEAD))],
            [comment(QA_LOGIN, critic_line("APPROVE", REVIEWED))],
            [comment(QA_LOGIN, critic_line("REQUEST_CHANGES", HEAD))],
            [comment(QA_LOGIN, critic_line("APPROVE", HEAD)),
             comment(QA_LOGIN, verifier_line("PASS", REVIEWED))],
            [comment(WORKER_LOGIN, critic_line("APPROVE", HEAD))],
        ]
        for comments in shapes:
            legacy = self.decide(comments)
            # …and with the new inputs supplied but no content ids anywhere.
            fresh = self.decide(comments, pr_commits=PR_COMMITS,
                                head_content_id=CID)
            self.assertEqual(legacy.action, fresh.action, comments)
            self.assertEqual(legacy.reason, fresh.reason, comments)
            self.assertEqual(fresh.carried, [])


# ── the CLI + the workflow wiring ────────────────────────────────────────
class GateCliTest(unittest.TestCase):
    """merge_gate.py computes the head's content id from the compare payload
    it ALREADY reads (--compare-file) — no new API call on the decision
    path, and no chance of the workflow handing it an id for a different
    head."""

    def run_cli(self, compare_payload, comments, commits=PR_COMMITS):
        with tempfile.TemporaryDirectory() as td:
            paths = {}
            for name, data in (
                ("check-runs", {"check_runs": GREEN_CI}),
                ("comments", comments),
                ("workflow-runs", {"workflow_runs": []}),
                ("pr-commits", commits),
            ):
                p = Path(td) / f"{name}.json"
                p.write_text(json.dumps(data))
                paths[name] = str(p)
            cp = Path(td) / "compare.json"
            cp.write_text(compare_payload)
            return subprocess.run(
                [sys.executable, str(SCRIPT),
                 "--head-sha", HEAD, "--qa-login", QA_LOGIN,
                 "--check-runs-file", paths["check-runs"],
                 "--comments-file", paths["comments"],
                 "--workflow-runs-file", paths["workflow-runs"],
                 "--compare-file", str(cp),
                 "--pr-commits-file", paths["pr-commits"]],
                capture_output=True, text=True,
            )

    def fields(self, proc):
        return dict(
            ln.split("=", 1) for ln in proc.stdout.splitlines() if "=" in ln
        )

    def test_carried_verdict_merges_through_the_cli(self):
        payload = compare([f("a.py", "1" * 40)])
        cid = verdict_content.content_id(payload)
        proc = self.run_cli(
            json.dumps(payload),
            [comment(QA_LOGIN, critic_line("APPROVE", REVIEWED, cid))],
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        fields = self.fields(proc)
        self.assertEqual(fields.get("decision"), "merge", proc.stdout)
        self.assertEqual(fields.get("carried_content_id"), cid)
        self.assertIn(REVIEWED, fields.get("carried", ""))

    def test_truncated_payload_falls_back_to_sha_binding(self):
        """A 300-entry files[] yields None, so the carry cannot happen and
        the gate waits for a fresh review — fail closed against truncation."""
        at_cap = compare([f(f"f{i}.py", f"{i:040x}") for i in range(300)])
        # The producer's id, computed when the record was still complete.
        proc = self.run_cli(
            json.dumps(at_cap),
            [comment(QA_LOGIN, critic_line("APPROVE", REVIEWED, CID))],
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.fields(proc).get("decision"), "wait")

    def test_blip_substitute_still_waits_and_carries_nothing(self):
        proc = self.run_cli(
            "{}", [comment(QA_LOGIN, critic_line("APPROVE", REVIEWED, CID))]
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        fields = self.fields(proc)
        self.assertEqual(fields.get("decision"), "wait")
        self.assertNotIn("carried", fields)

    def test_widened_payload_still_decides_currency_off_status(self):
        payload = compare([f("a.py", "1" * 40)], status="behind")
        cid = verdict_content.content_id(payload)
        proc = self.run_cli(
            json.dumps(payload),
            [comment(QA_LOGIN, critic_line("APPROVE", REVIEWED, cid))],
        )
        self.assertEqual(self.fields(proc).get("decision"), "update")


class MergeGateWiringTest(unittest.TestCase):
    """merge-gate.yml: the compare fetch is no longer trimmed, and a carried
    verdict leaves a record on the PR."""

    def setUp(self):
        doc = yaml.safe_load(MERGE_GATE_YML.read_text())
        steps = doc["jobs"]["evaluate"]["steps"]
        runs = [s["run"] for s in steps if s.get("name") == "Evaluate and merge"]
        assert len(runs) == 1
        self.run_block = runs[0]

    def test_compare_fetch_is_no_longer_trimmed(self):
        """Trap 9: the fetch was `--jq '{status: .status}'`, which throws
        files[] away. It must pass the whole record — while keeping the
        fail-closed `{}` substitute."""
        line = next(ln for ln in self.run_block.splitlines()
                    if "compare/$BASE...$SHA" in ln)
        self.assertNotIn("--jq", line, f"compare fetch still trimmed: {line}")
        self.assertIn("echo '{}' >", self.run_block)

    def test_the_carry_is_posted_to_the_pr(self):
        self.assertIn("carried=", self.run_block)
        self.assertIn("carried_content_id=", self.run_block)
        # Posted through the issue-comments API rather than `gh pr comment`:
        # this arm runs before the update/human/merge arms, and the
        # dependabot suite pins the first `gh pr comment` to the human arm.
        self.assertIn("-F body=@/tmp/carry-note.md", self.run_block)
        self.assertIn("/issues/$PR/comments", self.run_block)

    def test_the_carry_comment_is_idempotent(self):
        """The gate wakes many times per head; the record must be posted
        once, the way the `human` arm already does it."""
        self.assertIn("grep -q", self.run_block)
        # …and the needle must NOT be the bare content id. The verdict
        # comment being carried carries that id itself, so a bare
        # `content:$CARRIED_ID` grep matches on the very first wake and the
        # record is never posted at all.
        self.assertNotIn('grep -q "content:$CARRIED_ID"', self.run_block)
        needle = re.search(r'CARRY_MARK="([^"]+)"', self.run_block)
        self.assertIsNotNone(needle, "no dedicated idempotence marker")
        self.assertIn('grep -q "$CARRY_MARK"', self.run_block)
        # The marker must appear in the body it guards, or the note posts
        # again on every wake.
        body_start = self.run_block.find("CARRY_MARK=")
        self.assertIn("$CARRY_MARK", self.run_block[body_start:body_start + 400])

    def test_the_carry_comment_carries_no_verdict_marker(self):
        """It must not read as a verdict, and must not re-wake the gate's
        own issue_comment leg (which fires on a qa-bot comment containing
        the critic marker) — that would be a comment loop."""
        block = self.run_block
        start = block.find("carried_content_id=")
        self.assertGreater(start, -1)
        tail = block[start:]
        for forbidden in ("QA Critic", "QA Verifier", "VERDICT:"):
            self.assertNotIn(forbidden, tail)


class ProducerLineTest(unittest.TestCase):
    """Producer↔consumer, live-extracted: the ACTUAL line composed by
    qa-review.yml / verify.yml must be accepted by the gate for a CARRIED
    head — and, with no content id in the environment, must stay exactly
    what it is today."""

    QA_FILES = ("/tmp/qa-verdict.md", "/tmp/qa-comment.md")
    VERIFY_FILES = ("/tmp/verify-verdict.md", "/tmp/verify-comment.md")

    def tearDown(self):
        for path in self.QA_FILES + self.VERIFY_FILES:
            Path(path).unlink(missing_ok=True)

    @staticmethod
    def extract(path, prefix):
        lines = [ln.strip() for ln in path.read_text().splitlines()
                 if ln.strip().startswith(prefix)]
        assert len(lines) == 1, f"expected one {prefix!r} line in {path.name}"
        return lines[0]

    def compose(self, workflow, prefix, files, first_line, cid=None):
        line = self.extract(workflow, prefix)
        verdict_file, comment_file = files
        Path(verdict_file).write_text(f"{first_line}\n\n## Summary\nEvidence.\n")
        script = "set -euo pipefail\n" + f"REVIEWED_SHA={shlex.quote(REVIEWED)}\n"
        if cid:
            script += f"CONTENT_ID={shlex.quote(cid)}\n"
        proc = subprocess.run(["bash", "-c", script + line + "\n"],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return Path(comment_file).read_text().splitlines()[0]

    def test_critic_line_with_a_content_id_carries(self):
        posted = self.compose(QA_REVIEW_YML, '{ echo "🔎 QA Critic — ',
                              self.QA_FILES, "VERDICT: APPROVE", cid=CID)
        self.assertIn(f"@{REVIEWED}", posted)
        self.assertIn(f"content:{CID}", posted)
        self.assertIsNone(merge_gate.evaluate_critic(
            posted, HEAD, CID, frozenset({REVIEWED})))
        # …and still binds its own head the ordinary way.
        self.assertIsNone(merge_gate.evaluate_critic(posted, REVIEWED))

    def test_critic_line_without_a_content_id_is_todays_line(self):
        posted = self.compose(QA_REVIEW_YML, '{ echo "🔎 QA Critic — ',
                              self.QA_FILES, "VERDICT: APPROVE")
        self.assertEqual(posted, f"🔎 QA Critic — VERDICT: APPROVE @{REVIEWED}")
        self.assertIsNotNone(merge_gate.evaluate_critic(
            posted, HEAD, CID, frozenset({REVIEWED})))

    def test_verifier_line_with_a_content_id_carries(self):
        posted = self.compose(VERIFY_YML, '{ echo "🧪 QA Verifier — ',
                              self.VERIFY_FILES, "VERDICT: PASS", cid=CID)
        self.assertIn(f"content:{CID}", posted)
        got, _ = merge_gate.evaluate_verifier(
            posted, HEAD, CID, frozenset({REVIEWED}))
        self.assertIsNone(got)

    def test_verifier_line_without_a_content_id_is_todays_line(self):
        posted = self.compose(VERIFY_YML, '{ echo "🧪 QA Verifier — ',
                              self.VERIFY_FILES, "VERDICT: PASS")
        self.assertEqual(posted, f"🧪 QA Verifier — VERDICT: PASS @{REVIEWED}")


class ProducerWiringTest(unittest.TestCase):
    """Both producers compute the id for the SAME sha they capture, from
    GitHub's own compare record, through the one module."""

    def block(self, workflow, step_name):
        doc = yaml.safe_load(workflow.read_text())
        job = next(iter(doc["jobs"].values()))
        step = next(s for s in job["steps"] if s.get("name") == step_name)
        return step["run"]

    def test_qa_review_computes_the_id_for_the_reviewed_sha(self):
        block = self.block(QA_REVIEW_YML, "Resolve PR")
        self.assertIn("verdict_content.py", block)
        self.assertIn("compare/$BASE...$SHA", block)
        self.assertIn("content=", block)

    def test_verify_computes_the_id_for_the_verified_sha(self):
        block = self.block(VERIFY_YML, "Resolve PR")
        self.assertIn("verdict_content.py", block)
        self.assertIn("compare/$BASE...$SHA", block)

    def test_both_producers_thread_the_id_into_the_verdict_step(self):
        for workflow, step in ((QA_REVIEW_YML, "Post verdict or neutral status"),
                               (VERIFY_YML, "Post verdict or neutral status")):
            doc = yaml.safe_load(workflow.read_text())
            job = next(iter(doc["jobs"].values()))
            s = next(x for x in job["steps"] if x.get("name") == step)
            self.assertIn("CONTENT_ID", s.get("env", {}),
                          f"{workflow.name} does not thread the content id")


# ── 5. stop paying for the re-review, through ONE implementation ─────────
class ShouldReviewSkipTest(unittest.TestCase):
    """should_review() returns False when the latest qa-bot critic verdict
    is an APPROVE whose content id equals the current head's."""

    def call(self, comments, cid=CID, branch="agent/DRE-2340-x"):
        return should_review_pr.should_review(
            branch, comments=comments, qa_login=QA_LOGIN, head_content_id=cid
        )

    def test_skips_a_carried_approve(self):
        self.assertFalse(
            self.call([comment(QA_LOGIN, critic_line("APPROVE", REVIEWED, CID))])
        )

    def test_reviews_when_the_content_moved(self):
        self.assertTrue(
            self.call([comment(QA_LOGIN, critic_line("APPROVE", REVIEWED, CID))],
                      cid=OTHER_CID)
        )

    def test_reviews_when_the_gate_computed_no_id(self):
        self.assertTrue(
            self.call([comment(QA_LOGIN, critic_line("APPROVE", REVIEWED, CID))],
                      cid=None)
        )

    def test_reviews_a_request_changes_whatever_its_content_id(self):
        self.assertTrue(
            self.call([comment(QA_LOGIN,
                               critic_line("REQUEST_CHANGES", REVIEWED, CID))])
        )

    def test_reviews_a_forged_carry(self):
        self.assertTrue(
            self.call([comment(WORKER_LOGIN, critic_line("APPROVE", REVIEWED, CID))])
        )

    def test_reviews_an_unbound_verdict(self):
        self.assertTrue(
            self.call([comment(QA_LOGIN, "🔎 QA Critic — VERDICT: APPROVE")])
        )

    def test_every_pr_is_still_reviewed_with_no_content_inputs(self):
        """DRE-2250 stands: absent a proven carry, every PR is reviewed."""
        self.assertTrue(should_review_pr.should_review("chore/bump-deps"))
        self.assertTrue(should_review_pr.should_review("agent/DRE-1-x"))
        self.assertTrue(self.call([], cid=CID))

    def test_carried_sha_is_reported_for_the_republished_check(self):
        sha = should_review_pr.carried_approve(
            [comment(QA_LOGIN, critic_line("APPROVE", REVIEWED, CID))],
            QA_LOGIN, CID,
        )
        self.assertEqual(sha, REVIEWED)


class OneImplementationTest(unittest.TestCase):
    """Trap 4: merge_gate, reconcile.has_verdict and should_review_pr must
    read the binding through ONE module. DRE-1998 already had to fix
    reconcile's independent copy once."""

    def test_should_review_pr_imports_the_gates_parsing(self):
        src = inspect.getsource(should_review_pr)
        self.assertIn("import merge_gate", src)
        self.assertNotIn(
            'CRITIC_MARKER = "', src,
            "should_review_pr re-declares the critic marker instead of "
            "importing the gate's",
        )

    def test_no_module_reimplements_the_content_field_regex(self):
        for module in (merge_gate, should_review_pr, reconcile):
            src = inspect.getsource(module)
            self.assertNotIn(
                "content:(", src,
                f"{module.__name__} re-implements the content-field parse — "
                "it must import verdict_content",
            )

    def test_reconcile_reads_the_binding_through_the_module(self):
        src = inspect.getsource(reconcile.has_verdict)
        self.assertNotIn("[0-9a-f]{40}", src,
                         "reconcile still has its own binding regex")

    def test_the_algorithm_lives_in_exactly_one_place(self):
        hits = [m for m in (merge_gate, should_review_pr, reconcile)
                if "hashlib" in inspect.getsource(m)]
        self.assertEqual(hits, [], "content_id must be computed in one module")


class ReconcileBindingTest(unittest.TestCase):
    """reconcile.has_verdict honours the same carry, so the In QA sweep
    stops re-nudging a review for a PR whose verdict still binds."""

    def pr(self, head, bodies, base="main"):
        return {
            "headRefOid": head,
            "baseRefName": base,
            "comments": [{"author": {"login": GH_QA_LOGIN}, "body": b}
                         for b in bodies],
        }

    def test_carried_verdict_counts(self):
        pr = self.pr(HEAD, [critic_line("APPROVE", REVIEWED, CID)])
        self.assertTrue(reconcile.has_verdict(pr, CID))

    def test_stale_verdict_without_a_content_id_still_does_not_count(self):
        pr = self.pr(HEAD, [critic_line("APPROVE", REVIEWED)])
        self.assertFalse(reconcile.has_verdict(pr, CID))

    def test_mismatched_content_id_does_not_count(self):
        pr = self.pr(HEAD, [critic_line("APPROVE", REVIEWED, CID)])
        self.assertFalse(reconcile.has_verdict(pr, OTHER_CID))
        self.assertFalse(reconcile.has_verdict(pr))

    def test_head_bound_verdict_needs_no_content_id(self):
        pr = self.pr(HEAD, [critic_line("APPROVE", HEAD)])
        self.assertTrue(reconcile.has_verdict(pr))

    def test_forged_carry_is_invisible_to_reconcile_too(self):
        pr = {
            "headRefOid": HEAD,
            "baseRefName": "main",
            "comments": [{"author": {"login": "agent-bureau-bot"},
                          "body": critic_line("APPROVE", REVIEWED, CID)}],
        }
        self.assertFalse(reconcile.has_verdict(pr, CID))

    def test_the_sweep_requests_the_base_ref_it_needs(self):
        """Without baseRefName in the listing there is no compare to
        compute, and the carry silently never fires."""
        self.assertIn("baseRefName", inspect.getsource(reconcile.pr_for))


# ── 6. the PR #205 replay ────────────────────────────────────────────────
class Portico205ScenarioTest(unittest.TestCase):
    """The interleaving that cost eight CI runs, eight QA Review runs and
    eight Specimen runs on one PR, replayed.

    Timeline (2026-08-09, portico #205): APPROVE @0f3e24a6 → gate
    update-branch 20s later → APPROVE @ca076bd1 → update 21s later → …
    seven APPROVEs, six of them destroyed by the gate's own merge commits,
    because main took 30 commits in the 47-minute window.

    With content binding the review runs ONCE: every gate update merges
    only files the PR does not touch, so the id never moves.
    """

    HEADS = ["%040x" % (0x205 + i) for i in range(7)]

    def _simulate(self, content_ids):
        """Walk the heads. At each one, ask should_review_pr whether the
        critic must run; run it if so (posting a bound verdict), then ask
        the gate what it decides. Returns (review count, last decision)."""
        comments = []
        reviews = 0
        decision = None
        commits = []
        for head, cid in zip(self.HEADS, content_ids):
            commits.append({"sha": head})
            if should_review_pr.should_review(
                "agent/DRE-2329-orphan-user-audit",
                comments=comments, qa_login=QA_LOGIN, head_content_id=cid,
            ):
                reviews += 1
                comments.append(
                    comment(QA_LOGIN, critic_line("APPROVE", head, cid))
                )
            decision = merge_gate.decide(
                head_sha=head, qa_login=QA_LOGIN, check_runs=GREEN_CI,
                comments=comments, compare_status="ahead",
                pr_commits=commits, head_content_id=cid,
            )
        return reviews, decision

    def test_one_critic_run_not_seven(self):
        reviews, decision = self._simulate([CID] * 7)
        self.assertEqual(reviews, 1,
                         f"expected ONE critic run across the interleaving, "
                         f"got {reviews}")
        self.assertEqual(decision.action, "merge", decision.reason)
        self.assertTrue(decision.carried)

    def test_a_real_code_change_mid_stream_still_earns_a_review(self):
        """The 23:47 fix commit (fffb1002) changed the PR's own diff — that
        one MUST be re-reviewed. Two reviews, not one, and not seven."""
        ids = [CID, CID, CID, OTHER_CID, OTHER_CID, OTHER_CID, OTHER_CID]
        reviews, decision = self._simulate(ids)
        self.assertEqual(reviews, 2)
        self.assertEqual(decision.action, "merge")

    def test_the_old_sha_only_binding_reviews_every_head(self):
        """The control: with no content id anywhere (today's fleet), every
        head costs a review — the livelock, pinned so the delta is visible."""
        reviews, decision = self._simulate([None] * 7)
        self.assertEqual(reviews, 7)
        self.assertEqual(decision.action, "merge")


class SkipRepublishesTheHeadBoundCheckTest(unittest.TestCase):
    """On a skip the head must show an honest review status instead of
    nothing — the existing `QA critic review` check, re-published against
    the new head, naming the carried sha. That check is created with the
    DISPATCH App token (qa-bot holds checks:read only)."""

    def setUp(self):
        self.doc = yaml.safe_load(QA_REVIEW_YML.read_text())
        self.steps = self.doc["jobs"]["review"]["steps"]

    def step(self, predicate):
        return [s for s in self.steps if predicate(s)]

    def test_a_skip_republishes_the_review_check(self):
        publishers = self.step(
            lambda s: "publish_review_check.py" in (s.get("run") or "")
        )
        self.assertTrue(publishers)
        conditions = " ".join(s.get("if", "") for s in publishers)
        self.assertIn("carried", conditions,
                      "no publish step is reachable on the carried-skip path")

    def test_the_skip_path_mints_the_dispatch_app_token(self):
        mints = self.step(
            lambda s: "create-github-app-token" in (s.get("uses") or "")
            and "BUREAU_APP_ID" in json.dumps(s.get("with", {}))
        )
        self.assertTrue(mints)
        self.assertIn("carried", " ".join(s.get("if", "") for s in mints))

    def test_the_skip_posts_nothing_on_the_pr(self):
        """A skip must be a SILENT no-op so the existing verdict stays the
        latest comment: every `gh pr comment` stays behind review == 'true'."""
        for s in self.steps:
            if "gh pr comment" in (s.get("run") or ""):
                self.assertIn("review == 'true'", s.get("if", ""),
                              f"step {s.get('name')!r} could comment on a skip")

    def test_the_republished_check_names_the_carried_sha(self):
        publishers = self.step(
            lambda s: "publish_review_check.py" in (s.get("run") or "")
        )
        self.assertTrue(any("carried-from" in s["run"] for s in publishers))


class PublishReviewCheckCarryTest(unittest.TestCase):
    """publish_review_check.py can say a verdict was carried, without
    inventing an outcome nobody produced."""

    def test_carried_from_appears_in_the_check_output(self):
        import publish_review_check

        conclusion, title, summary = publish_review_check.decide(
            True, "VERDICT: APPROVE\n"
        )
        self.assertEqual(conclusion, "success")
        detail = publish_review_check.detail_body(
            sha=HEAD, summary=summary, run_url="", event="pull_request",
            carried_from=REVIEWED,
        )
        self.assertIn(REVIEWED, detail)


if __name__ == "__main__":
    unittest.main()
