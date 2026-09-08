"""RED-first tests for the lost-event backstop's mergeability guard (DRE-2767).

THE BUG (live, 2026-08-26, two PRs): `retrigger_dead_heads` re-pushes an empty
commit at an agent PR whose head has zero check-runs after 15 minutes, on the
theory that GitHub dropped the push event. Its one guard against the OTHER
cause of that signature — a conflicted PR, which emits no events at all
because GitHub cannot build its test-merge commit — was
`mergeStateStatus == "DIRTY"`.

GitHub computes mergeability LAZILY, so that field frequently answers
`UNKNOWN`, and `UNKNOWN != "DIRTY"` fell straight through the guard. bp #191
read `DIRTY` at 15:22 PT, `UNKNOWN` on a re-read at 15:37, and `UNKNOWN` again
at 15:52 while `refs/pull/191/merge` answered 404 — the unambiguous proof of
conflict. The sweep pushed an empty commit that produced no check run and
could not: a re-push at a conflicted PR fires nothing AND resets the head's
age, so the PR re-qualifies 15 minutes later. A self-sustaining loop that adds
a commit per sweep, converges never, and masks the stall it is sitting on.

FIX UNDER TEST — `retrigger_dead_heads` never acts on an indefinite
mergeability, the rule `unstick_conflicts` has held since DRE-2121:
  * `UNKNOWN` (or absent) is re-read once — reading is what forces GitHub's
    recompute — and a still-indefinite answer means NO push; the next sweep
    asks again.
  * `git/ref/pull/{n}/merge` is consulted as a direct observation rather than
    a cached opinion: 404 means GitHub could not build the merge commit, which
    is conflict however `mergeStateStatus` reads. Unreadable is not a licence.
  * The re-push is capped per head (`RETRIGGER_CAP`), so any future misfire is
    bounded instead of unbounded.
And the backstop's real purpose survives: a genuinely event-starved, genuinely
mergeable PR — its origin case, PR #25 — still gets its re-push.

Run: cd bureau-pipeline && python3 -m pytest tests/test_dead_head_unknown_mergeability.py -v
"""

import contextlib
import io
import json
import os
import sys
import unittest
from datetime import UTC, datetime, timedelta
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/test")
os.environ.setdefault("GH_TOKEN", "test")

import reconcile  # noqa: E402

HEAD = "b" * 40
TREE = "t" * 40
NEW = "c" * 40
BRANCH = "agent/DRE-2739-mid-epic-discovery"


def _iso(minutes_ago: float) -> str:
    return (
        (datetime.now(UTC) - timedelta(minutes=minutes_ago))
        .isoformat()
        .replace("+00:00", "Z")
    )


def _pr(number=191, mstate="UNKNOWN", branch=BRANCH, sha=HEAD):
    return {
        "number": number,
        "headRefName": branch,
        "headRefOid": sha,
        "mergeStateStatus": mstate,
    }


def _commit(sha=HEAD, age_min=40.0, message="feat(DRE-2739): the work",
            parent="p" * 40):
    return {
        "sha": sha,
        "message": message,
        "tree": {"sha": TREE},
        "parents": [{"sha": parent}],
        "committer": {"date": _iso(age_min)},
    }


class _Sweep:
    """One `retrigger_dead_heads` run against a fake GitHub.

    `views` are the successive `pr view` answers per PR number (the last one
    repeats — GitHub recomputes on read); `merge_ref` is what
    `git/ref/pull/{n}/merge` does per PR number: True = the ref exists (a
    test-merge commit was built), False = 404 (it could not be), None = the
    read failed for some other reason.
    """

    def __init__(self, prs, commits=None, views=None, merge_ref=None,
                 check_total="0"):
        self.prs = prs
        self.commits = commits if commits is not None else {HEAD: _commit()}
        self.views = views or {}
        self.merge_ref = merge_ref if merge_ref is not None else {}
        self.check_total = check_total
        self.reads = {}
        self.calls = []
        self.pushed = []      # bodies of the created commits
        self.patched = []     # (ref, sha) pairs written to the branch
        self.stdout = ""

    def gh(self, *args):
        self.calls.append(args)
        if args[:2] == ("pr", "list"):
            return json.dumps(self.prs)
        if args[:2] == ("pr", "view"):
            n = int(args[2])
            seq = self.views.get(n) or ["UNKNOWN"]
            i = self.reads.get(n, 0)
            self.reads[n] = i + 1
            return json.dumps(_pr(n, seq[min(i, len(seq) - 1)]))
        if args[0] == "api" and args[1] == "-X":
            if args[2] == "POST":
                self.pushed.append(
                    dict(a.split("=", 1) for a in args if "=" in a)
                )
                return NEW
            if args[2] == "PATCH":
                self.patched.append((args[3], args[-1].split("=", 1)[-1]))
                return "{}"
            return ""
        if args[0] == "api" and args[1].endswith("/check-runs"):
            return self.check_total
        if args[0] == "api" and "/git/commits/" in args[1]:
            return json.dumps(self.commits.get(args[1].rsplit("/", 1)[1], {}))
        return ""

    def gh_read(self, *args):
        self.calls.append(args)
        if args[0] == "api" and "/git/ref/pull/" in args[1]:
            n = int(args[1].split("/git/ref/pull/")[1].split("/")[0])
            state = self.merge_ref.get(n, True)
            if state is True:
                return json.dumps({"ref": f"refs/pull/{n}/merge"})
            if state is False:
                raise reconcile.ReconcileReadError(
                    f"gh {' '.join(args)} failed rc=1: gh: Not Found (HTTP 404)"
                )
            raise reconcile.ReconcileReadError(
                f"gh {' '.join(args)} failed rc=1: gh: HTTP 502 (bad gateway)"
            )
        return ""

    def run(self):
        out = io.StringIO()
        with mock.patch.object(reconcile, "gh", side_effect=self.gh), \
                mock.patch.object(reconcile, "gh_read", side_effect=self.gh_read), \
                contextlib.redirect_stdout(out):
            reconcile.retrigger_dead_heads()
        self.stdout = out.getvalue()
        return self


class IndefiniteMergeabilityTest(unittest.TestCase):
    """Never act on an unknown. The live #191/#2143 shape and its variants."""

    def test_unknown_on_a_conflicted_pr_pushes_nothing(self):
        # THE case: mergeStateStatus UNKNOWN on both reads, and the merge ref
        # 404s — the PR is conflicted and a re-push can only add a commit.
        s = _Sweep([_pr(191, "UNKNOWN")], views={191: ["UNKNOWN"]},
                   merge_ref={191: False}).run()
        self.assertEqual(s.pushed, [], "an empty commit was pushed at a conflicted PR")
        self.assertEqual(s.patched, [])

    def test_unknown_that_resolves_dirty_pushes_nothing(self):
        # The re-read is what forces GitHub's recompute; DIRTY on the second
        # look belongs to unstick_conflicts, not to this backstop.
        s = _Sweep([_pr(191, "UNKNOWN")], views={191: ["DIRTY"]},
                   merge_ref={191: True}).run()
        self.assertEqual(s.pushed, [])
        self.assertEqual(s.patched, [])

    def test_still_unknown_after_the_reread_pushes_nothing(self):
        # Even with a merge ref present, an indefinite mergeability is not a
        # negative: the sweep says so and leaves the PR for the next pass.
        s = _Sweep([_pr(191, "UNKNOWN")], views={191: ["UNKNOWN"]},
                   merge_ref={191: True}).run()
        self.assertEqual(s.pushed, [])
        self.assertIn("UNKNOWN", s.stdout)
        self.assertIn("#191", s.stdout)

    def test_absent_mergeability_field_is_indefinite_too(self):
        pr = _pr(191)
        del pr["mergeStateStatus"]
        s = _Sweep([pr], views={191: ["UNKNOWN"]}, merge_ref={191: True}).run()
        self.assertEqual(s.pushed, [])

    def test_merge_ref_404_blocks_a_pr_reading_clean(self):
        # The direct observation outranks the cached opinion: GitHub could not
        # build the test-merge commit, whatever mergeStateStatus says.
        s = _Sweep([_pr(191, "CLEAN")], merge_ref={191: False}).run()
        self.assertEqual(s.pushed, [])
        self.assertEqual(s.patched, [])

    def test_unreadable_merge_ref_is_not_a_licence_to_push(self):
        # DRE-2034: unreadable is never "nothing wrong here".
        s = _Sweep([_pr(191, "CLEAN")], merge_ref={191: None}).run()
        self.assertEqual(s.pushed, [])

    def test_dirty_on_the_listing_costs_no_reads_and_pushes_nothing(self):
        # The pre-existing guard, kept: a definite DIRTY is skipped before any
        # per-PR read is paid.
        s = _Sweep([_pr(191, "DIRTY")]).run()
        self.assertEqual(s.pushed, [])
        self.assertEqual(
            [a for a in s.calls if a[:2] != ("pr", "list")], [],
            "a definitely-conflicted PR must cost no API reads",
        )


class BackstopStillWorksTest(unittest.TestCase):
    """The origin case, PR #25 / DRE-1218: an event GitHub genuinely dropped
    on a genuinely mergeable PR still gets its re-push. The fix must not turn
    the backstop off."""

    def test_event_starved_mergeable_pr_still_gets_its_repush(self):
        s = _Sweep([_pr(25, "CLEAN", branch="agent/DRE-1218-pr-25")],
                   merge_ref={25: True}).run()
        self.assertEqual(len(s.pushed), 1)
        self.assertEqual(s.pushed[0]["tree"], TREE)
        self.assertEqual(s.pushed[0]["parents[]"], HEAD)
        self.assertEqual(
            s.patched, [(f"repos/{reconcile.REPO}/git/refs/heads/agent/DRE-1218-pr-25", NEW)]
        )

    def test_unknown_resolving_mergeable_gets_its_repush(self):
        # Lazy computation must delay the backstop, never disable it.
        s = _Sweep([_pr(25, "UNKNOWN", branch="agent/DRE-1218-pr-25")],
                   views={25: ["CLEAN"]}, merge_ref={25: True}).run()
        self.assertEqual(len(s.pushed), 1)

    def test_a_head_with_check_runs_is_left_alone(self):
        s = _Sweep([_pr(25, "CLEAN")], merge_ref={25: True},
                   check_total="3").run()
        self.assertEqual(s.pushed, [])

    def test_a_fresh_head_is_left_alone(self):
        s = _Sweep([_pr(25, "CLEAN")], commits={HEAD: _commit(age_min=2)},
                   merge_ref={25: True}).run()
        self.assertEqual(s.pushed, [])

    def test_a_non_card_branch_is_never_touched(self):
        s = _Sweep([_pr(42, "CLEAN", branch="dependabot/pip/foo-2.0")],
                   merge_ref={42: True}).run()
        self.assertEqual(s.pushed, [])


def _stack(depth: int) -> dict:
    """A head sitting on `depth` consecutive re-push commits — what a misfiring
    sweep builds, one per pass."""
    commits, parent = {}, "p" * 40
    shas = [HEAD] + [f"{i}" * 40 for i in range(1, depth)]
    for i, sha in enumerate(shas[:depth]):
        commits[sha] = _commit(
            sha=sha,
            message=reconcile.RETRIGGER_MESSAGE,
            parent=shas[i + 1] if i + 1 < depth else parent,
        )
    if depth == 0:
        commits[HEAD] = _commit()
    return commits


class RepushCapTest(unittest.TestCase):
    """Bound the misfire. Each re-push resets the head's age, so a wrong
    diagnosis re-qualifies the PR every sweep forever; the cap makes any
    future one cost N commits rather than an unbounded stream."""

    def test_at_the_cap_nothing_is_pushed(self):
        s = _Sweep([_pr(25, "CLEAN")], commits=_stack(reconcile.RETRIGGER_CAP),
                   merge_ref={25: True}).run()
        self.assertEqual(s.pushed, [])
        self.assertIn("cap", s.stdout.lower())

    def test_below_the_cap_the_repush_still_happens(self):
        self.assertGreater(reconcile.RETRIGGER_CAP, 1)
        s = _Sweep([_pr(25, "CLEAN")],
                   commits=_stack(reconcile.RETRIGGER_CAP - 1),
                   merge_ref={25: True}).run()
        self.assertEqual(len(s.pushed), 1)


if __name__ == "__main__":
    unittest.main()
