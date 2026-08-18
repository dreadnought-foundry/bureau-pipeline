"""RED-first tests for DRE-2508 — the gate's status note under concurrency.

The bug (caught live 2026-08-17 on bureau-pipeline #157): merge-gate.yml's
`human` arm posted its "waiting for human merge" note behind

    if ! grep -q "Merge gate: waiting for human merge" /tmp/comments.json

— a check-then-act over a comments snapshot fetched ~30 lines earlier in the
same run. Two gate evaluations of ONE PR overlapping (the CI `workflow_run`
leg and the critic's `issue_comment` leg are separate wakes) both read the
snapshot before either posted, neither saw the marker, and both posted. The
harness's `gate_paths` named leg asserts exactly one such note and went red;
a gate that reds on busy nights is a gate people re-run without reading.

Two independent mechanisms are pinned here, either of which alone would have
held on 2026-08-17:

  1. SERIALIZATION (workflow) — the two legs used DIFFERENT concurrency
     groups (`merge-gate-<head_branch>` for workflow_run, `merge-gate-<pr>`
     for issue_comment, `merge-gate-` for dispatch), so per-PR serialization
     never applied ACROSS legs. The gate now resolves the PR number in its
     own job and keys the evaluating job on that one identity, so no two
     evaluations of one PR can overlap regardless of which leg woke them.

  2. ATOMIC-IN-EFFECT POST (scripts/gate_note.py) — the note is re-read as
     late as possible, posted, and then converged: every writer that sees
     more than one note keeps the EARLIEST (lowest comment id) and deletes
     the rest. Deterministic winner, so concurrent writers agree without
     coordinating. The race is reproduced DELIBERATELY below (threads +
     a barrier that forces both runs to read before either writes), not
     observed by luck.

Neither mechanism may delay or drop a legitimate first post: on a quiet repo
the note is created after exactly one read, with no back-off.
"""

from __future__ import annotations

import threading
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "merge-gate.yml"

import sys  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts"))

import gate_note  # noqa: E402

# The live literals: the marker merge-gate.yml posts and the harness asserts
# (scripts/harness/scenarios/gate_paths.py HUMAN_WAIT_MARKER).
MARKER = "Merge gate: waiting for human merge"
QA_LOGIN = "agent-bureau-qa-bot[bot]"


# ── a shared comments store, one fake client per concurrent gate run ────────
class _Store:
    """The PR's comment list, as GitHub holds it: ids strictly increase in
    creation order, and every client sees the same list."""

    def __init__(self, seed=()):
        self._lock = threading.Lock()
        self._next_id = 100
        self.comments = []
        for login, body in seed:
            self.create(login, body)

    def snapshot(self):
        with self._lock:
            return [dict(c, user=dict(c["user"])) for c in self.comments]

    def create(self, login, body):
        with self._lock:
            c = {"id": self._next_id, "user": {"login": login}, "body": body}
            self._next_id += 1
            self.comments.append(c)
            return dict(c, user=dict(c["user"]))

    def delete(self, comment_id):
        with self._lock:
            keep = [c for c in self.comments if c["id"] != comment_id]
            if len(keep) == len(self.comments):
                # GitHub's 404 for a comment a racing peer already removed.
                raise gate_note.GateNoteError(f"404 comment {comment_id}")
            self.comments = keep


class _FakeApi:
    """One gate run's view of the store. `read_barrier`, when given, holds
    the FIRST read until every racing run has taken its snapshot — the exact
    check-then-act window, forced open instead of waited for."""

    def __init__(self, store, login=QA_LOGIN, read_barrier=None):
        self.store, self.login, self.read_barrier = store, login, read_barrier
        self.calls = []

    def list_comments(self):
        snapshot = self.store.snapshot()
        self.calls.append("list")
        if self.read_barrier is not None and self.calls.count("list") == 1:
            self.read_barrier.wait(timeout=10)
        return snapshot

    def create_comment(self, body):
        self.calls.append("create")
        return self.store.create(self.login, body)

    def delete_comment(self, comment_id):
        self.calls.append("delete")
        self.store.delete(comment_id)


def note_body(run):
    return f"⏸️ {MARKER} — cannot prove it is minor/patch-only (run {run})"


def surviving_notes(store):
    return gate_note.matching_notes(store.snapshot(), MARKER, QA_LOGIN)


def race(store, runs):
    """Run `runs` gate evaluations concurrently, all reading before any
    writes. Returns each run's post_once result, in run order."""
    barrier = threading.Barrier(runs)
    apis = [_FakeApi(store, read_barrier=barrier) for _ in range(runs)]
    results = [None] * runs
    errors = []

    def work(i):
        try:
            results[i] = gate_note.post_once(
                apis[i], MARKER, note_body(i), QA_LOGIN, log=lambda *a: None
            )
        except BaseException as e:  # noqa: BLE001 — surfaced by the assert below
            errors.append(e)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(runs)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not any(t.is_alive() for t in threads), "a racing gate run hung"
    assert not errors, f"a racing gate run raised: {errors}"
    return results, apis


class PostOnceRaceTest(unittest.TestCase):
    """The deliberate reproduction: concurrent evaluations of ONE PR."""

    def test_two_concurrent_runs_leave_exactly_one_note(self):
        store = _Store()
        race(store, 2)
        notes = surviving_notes(store)
        self.assertEqual(
            len(notes), 1,
            f"two overlapping gate runs left {len(notes)} waiting-for-human "
            "notes — the harness's gate_paths named leg reds on exactly this",
        )

    def test_the_survivor_is_the_earliest_note(self):
        """Both writers must pick the SAME winner without coordinating, or
        they delete each other's and the PR is left with none."""
        store = _Store()
        race(store, 2)
        notes = surviving_notes(store)
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]["id"], 100, "the first note posted must survive")

    def test_five_concurrent_runs_leave_exactly_one_note(self):
        """A burst — 12 gate runs in 25 minutes is what 2026-08-17 looked
        like. Convergence must not depend on there being exactly two."""
        store = _Store()
        race(store, 5)
        self.assertEqual(len(surviving_notes(store)), 1)

    def test_a_standing_note_suppresses_every_racing_run(self):
        store = _Store(seed=[(QA_LOGIN, note_body("earlier"))])
        results, apis = race(store, 3)
        notes = surviving_notes(store)
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]["id"], 100, "the standing note was replaced")
        self.assertEqual([r["action"] for r in results], ["standing"] * 3)
        for api in apis:
            self.assertNotIn("create", api.calls, "re-evaluation spammed the PR")


class QuietRepoTest(unittest.TestCase):
    """Criterion: the existing post-once behaviour on a quiet repo is
    unchanged, and nothing delays or drops a legitimate FIRST post."""

    def test_the_first_evaluation_posts_the_note(self):
        store = _Store()
        api = _FakeApi(store)
        result = gate_note.post_once(api, MARKER, note_body(1), QA_LOGIN)
        self.assertEqual(result["action"], "posted")
        self.assertEqual(len(surviving_notes(store)), 1)
        self.assertEqual(surviving_notes(store)[0]["body"], note_body(1))

    def test_the_first_post_is_not_delayed(self):
        """One read, then the write — no back-off, no lock acquisition, no
        second confirming read BEFORE the post."""
        store = _Store()
        api = _FakeApi(store)
        gate_note.post_once(api, MARKER, note_body(1), QA_LOGIN)
        self.assertEqual(api.calls[:2], ["list", "create"])
        self.assertNotIn("delete", api.calls, "the only note was deleted")

    def test_a_later_evaluation_does_not_post_again(self):
        store = _Store()
        first = _FakeApi(store)
        gate_note.post_once(first, MARKER, note_body(1), QA_LOGIN)
        second = _FakeApi(store)
        result = gate_note.post_once(second, MARKER, note_body(2), QA_LOGIN)
        self.assertEqual(result["action"], "standing")
        self.assertNotIn("create", second.calls)
        self.assertEqual(len(surviving_notes(store)), 1)

    def test_a_body_without_the_marker_is_refused(self):
        """The marker IS the idempotence key: a body that does not carry it
        would re-post on every single wake (the DRE-2340 carry-note trap)."""
        store = _Store()
        with self.assertRaises(ValueError):
            gate_note.post_once(_FakeApi(store), MARKER, "no key here", QA_LOGIN)
        self.assertEqual(store.snapshot(), [])


class AuthorScopeTest(unittest.TestCase):
    """The idempotence key is the gate's OWN note, not the string anywhere
    on the PR — the same qa-authored filter the harness applies."""

    def test_a_quoted_marker_from_someone_else_does_not_suppress_the_post(self):
        store = _Store(seed=[("some-human", f"why did `{MARKER}` fire here?")])
        api = _FakeApi(store)
        result = gate_note.post_once(api, MARKER, note_body(1), QA_LOGIN)
        self.assertEqual(
            result["action"], "posted",
            "a human quoting the marker silenced the gate's honest state",
        )
        self.assertEqual(len(surviving_notes(store)), 1)

    def test_a_foreign_note_is_never_deleted(self):
        quoted = f"why did `{MARKER}` fire here?"
        store = _Store(seed=[("some-human", quoted)])
        race(store, 2)
        bodies = [c["body"] for c in store.snapshot()]
        self.assertIn(quoted, bodies, "the gate deleted a human's comment")

    def test_the_bot_suffix_is_tolerated(self):
        """The workflow passes `<app-slug>[bot]`; the REST record may carry
        either spelling (the same tolerance harness.same_bot applies)."""
        store = _Store(seed=[("agent-bureau-qa-bot", note_body("earlier"))])
        api = _FakeApi(store)
        result = gate_note.post_once(api, MARKER, note_body(1), QA_LOGIN)
        self.assertEqual(result["action"], "standing")


class ReadBlipTest(unittest.TestCase):
    """A comments-API blip must not become a DUPLICATE note: unreadable
    means "defer to the next wake", never "post blind"."""

    class _BlindApi(_FakeApi):
        def list_comments(self):
            raise gate_note.GateNoteError("503 from the comments API")

    def test_an_unreadable_comments_record_defers_the_note(self):
        store = _Store()
        api = self._BlindApi(store)
        result = gate_note.post_once(api, MARKER, note_body(1), QA_LOGIN,
                                     log=lambda *a: None)
        self.assertEqual(result["action"], "deferred")
        self.assertEqual(store.snapshot(), [], "posted over an unreadable record")

    def test_a_failed_dedupe_delete_never_reds_the_run(self):
        """The peer may delete the loser first — a 404 there is the happy
        path, not a gate failure."""
        store = _Store()
        results, _ = race(store, 2)
        self.assertEqual(len(surviving_notes(store)), 1)
        self.assertEqual(
            {r["action"] for r in results}, {"posted", "deduped"},
            f"unexpected outcomes {results}",
        )


# ── workflow wiring ─────────────────────────────────────────────────────────
def _doc():
    return yaml.safe_load(WORKFLOW.read_text())


def _evaluate_block():
    steps = _doc()["jobs"]["evaluate"]["steps"]
    runs = [s["run"] for s in steps if s.get("name") == "Evaluate and merge"]
    assert len(runs) == 1, "expected exactly one 'Evaluate and merge' step"
    return runs[0]


class WorkflowWiringTest(unittest.TestCase):
    def setUp(self):
        self.block = _evaluate_block()

    def test_the_human_arm_delegates_to_the_atomic_poster(self):
        human = self.block.find('"$DECISION" = "human"')
        poster = self.block.find("gate_note.py")
        self.assertGreater(human, -1, "no human-decision arm")
        self.assertGreater(poster, human, "the human arm no longer posts")

    def test_the_check_then_act_grep_is_gone(self):
        self.assertNotIn(
            f'grep -q "{MARKER}"', self.block,
            "the human note is still guarded by a grep over the snapshot "
            "fetched earlier in the run — that is the DRE-2508 race",
        )

    def test_the_posted_marker_is_the_literal_the_harness_asserts(self):
        self.assertIn(MARKER, self.block)

    def test_the_note_is_scoped_to_the_gates_own_login(self):
        self.assertIn("--author", self.block)
        self.assertIn('"$QA_LOGIN"', self.block)

    def test_the_workflow_passes_every_flag_the_script_requires(self):
        """Drift guard in both directions, the merge_gate.py pattern."""
        parser = gate_note.build_parser()
        required = [
            a.option_strings[0]
            for a in parser._actions
            if a.option_strings and a.required
        ]
        self.assertTrue(required, "gate_note.py has no required options")
        for flag in required:
            self.assertIn(flag, self.block, f"workflow never passes {flag}")

    def test_the_note_carries_no_verdict_shaped_text(self):
        """It is a status note, not an approval credential — and it must not
        re-wake the gate's own issue_comment leg."""
        start = self.block.find('"$DECISION" = "human"')
        end = self.block.find('[ "$DECISION" = "merge" ] || exit 0')
        self.assertGreater(end, start, "the human arm no longer precedes the merge guard")
        arm = self.block[start:end]
        for forbidden in ("QA Critic", "QA Verifier", "VERDICT:"):
            self.assertNotIn(forbidden, arm)


class SerializationTest(unittest.TestCase):
    """Mechanism 1: two evaluations of ONE PR cannot overlap, whichever leg
    woke them. The old group keyed on `github.event.*`, which names a
    DIFFERENT thing per leg — the branch on workflow_run, the number on
    issue_comment, nothing at all on workflow_dispatch."""

    def setUp(self):
        self.doc = _doc()

    def test_the_pr_is_resolved_in_its_own_job(self):
        jobs = self.doc["jobs"]
        self.assertIn("resolve", jobs, "no PR-resolution job")
        outputs = jobs["resolve"].get("outputs") or {}
        self.assertIn("pr", outputs, "the resolve job publishes no PR number")

    def test_every_event_leg_resolves_through_that_one_job(self):
        run = "\n".join(
            s.get("run", "") for s in self.doc["jobs"]["resolve"]["steps"]
        )
        for leg in ("workflow_run", "workflow_dispatch"):
            self.assertIn(leg, run, f"the {leg} leg is not resolved")
        self.assertIn("gh pr list", run, "the workflow_run leg needs a lookup")
        self.assertIn("--state", run, "gh pr list must name its state (DRE-2316)")

    def test_evaluation_is_serialized_on_the_resolved_pr(self):
        group = self.doc["jobs"]["evaluate"]["concurrency"]["group"]
        self.assertIn(
            "needs.resolve.outputs.pr", group,
            "the evaluating job is not keyed on the resolved PR",
        )

    def test_the_group_cannot_differ_between_event_legs(self):
        group = self.doc["jobs"]["evaluate"]["concurrency"]["group"]
        self.assertNotIn(
            "github.event", group,
            "the group still reads github.event — a workflow_run wake and an "
            "issue_comment wake for the SAME PR then land in different "
            "groups and run concurrently (DRE-2508)",
        )

    def test_a_queued_evaluation_is_not_cancelled(self):
        # Cancelling would drop the wake that was going to merge; the gate
        # would then wait for reconcile's ~15-minute nudge.
        self.assertIs(
            self.doc["jobs"]["evaluate"]["concurrency"]["cancel-in-progress"],
            False,
        )

    def test_evaluate_is_unreachable_without_a_resolved_pr(self):
        job = self.doc["jobs"]["evaluate"]
        self.assertIn("resolve", job.get("needs") or [])
        cond = " ".join((job.get("if") or "").split())
        self.assertIn("needs.resolve.outputs.pr != ''", cond)
        self.assertIn(
            "needs.resolve.result == 'success'", cond,
            "a FAILED resolve must not let the gate evaluate on a guessed PR",
        )

    def test_the_event_leg_filter_still_gates_the_gate(self):
        """The #57 filter (only a qa-bot-authored verdict comment wakes the
        gate) moved to the entry job with the resolution — it must still be
        there, and no leg may reach evaluate around it."""
        cond = " ".join((self.doc["jobs"]["resolve"]["if"] or "").split())
        self.assertIn(
            "github.event.comment.user.login == 'agent-bureau-qa-bot[bot]'", cond
        )
        self.assertIn("github.event_name == 'workflow_dispatch'", cond)


if __name__ == "__main__":
    unittest.main()
