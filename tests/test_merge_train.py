"""RED-first tests for the merge train that starved the channel (DRE-3070).

On 2026-09-03, fourteen PRs merged to `main` between 18:30 and 20:53 PT and
`stable` did not move once — it sat at `0469a887`, 50 commits behind, while
every product repo ran the afternoon's code. The harness runs on every push to
main and `promote-channel.yml` promotes only what a harness run proved, so a
harness run that never finishes is a channel that never advances.

**The rule this file pins is QUEUE BEHIND, NEVER CANCEL.** The run proving
commit N must be allowed to finish and promote; the newest head queues behind
it. Intermediate heads may be dropped — GitHub keeps at most ONE pending run
per concurrency group — and that is the intended trade: `stable` advances to N
and then to the latest, instead of never.

Three things are asserted here, and each is a way the channel goes quiet:

  1. **The queue-behind rule holds in the shipped config.** The simulation
     below reads `cancel-in-progress` out of `harness.yml` rather than assuming
     it, so flipping that switch turns these tests red instead of turning the
     channel off.
  2. **The counterfactual starves.** The same two pushes under
     `cancel-in-progress: true` complete nothing and promote nothing inside a
     harness duration. Without this the test above would pass against any
     config that happens to be green.
  3. **A skipped promotion still says why.** A displaced run leaves a receipt
     naming the merge train, so a stale channel is diagnosable from the record
     rather than from someone reading Actions at midnight.

`simulate()` is a MODEL of GitHub's per-group semantics, not our code — it
exists because the real behaviour cannot be run in CI. It was written against
the observed record of the incident evening (workflow `harness.yml`, runs
33824344380 → 33834607810): every run that concluded `cancelled` has
`run_started_at == created_at`, i.e. it was cancelled while PENDING, one to two
seconds after the next run in the group was created. Nothing in progress was
ever killed. That is exactly `cancel-in-progress: false` plus a single pending
slot, and it is why the fix for this card was never a switch flip.
"""

import os
import sys
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import promote_channel  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / ".github" / "workflows" / "harness.yml"

#: A harness run's wall clock, rounded from the incident evening's completed
#: runs (9m40s → 15m47s). Only the ORDER of events depends on it; the numbers
#: below are chosen so no assertion sits on a boundary.
HARNESS_SECONDS = 600.0

COMPLETED = "completed"
CANCELLED = "cancelled"
PENDING = "pending"

SHA = "c" * 40


def _harness_concurrency() -> dict:
    doc = yaml.safe_load(HARNESS.read_text())
    conc = doc.get("concurrency")
    assert isinstance(conc, dict), "harness.yml must hold a concurrency group"
    return conc


def simulate(arrivals, *, cancel_in_progress, duration=HARNESS_SECONDS, until=None):
    """GitHub's per-concurrency-group behaviour, as a pure function.

    `arrivals` is a list of `(seconds, label)`. Returns `{label: outcome}` with
    one of `completed` / `cancelled` / `pending` per run, observed at `until`
    (default: the last arrival, so nothing is credited that has not happened).

    The three rules, in GitHub's order:
      * a run arriving to an idle group starts immediately;
      * a run arriving while one is in progress either KILLS it
        (`cancel-in-progress: true`) or becomes the group's pending run;
      * there is only ever ONE pending run — a new arrival cancels whatever
        was waiting.
    """
    outcome: dict[str, str] = {}
    running: str | None = None
    finish: float | None = None
    waiting: str | None = None

    def advance(now: float) -> None:
        nonlocal running, finish, waiting
        while running is not None and finish is not None and finish <= now:
            outcome[running] = COMPLETED
            started = finish
            running, finish = None, None
            if waiting is not None:
                running, finish, waiting = waiting, started + duration, None

    for at, label in sorted(arrivals):
        advance(at)
        if running is None:
            running, finish = label, at + duration
        elif cancel_in_progress:
            outcome[running] = CANCELLED
            running, finish = label, at + duration
        else:
            if waiting is not None:
                outcome[waiting] = CANCELLED
            waiting = label

    advance(until if until is not None else max(at for at, _ in arrivals))
    for label in (running, waiting):
        if label is not None:
            outcome.setdefault(label, PENDING)
    return outcome


def _promotions(outcomes, conclusions=None):
    """The channel moves this set of runs would produce, promoter's own rules."""
    green = {"statuses": [{"context": promote_channel.STATUS_CONTEXT,
                           "state": "success"}]}
    moved = []
    for label, state in outcomes.items():
        if state != COMPLETED:
            continue
        decision = promote_channel.evaluate(
            green, SHA, ancestry=promote_channel.AHEAD, conclusion="success"
        )
        if decision.promote:
            moved.append(label)
    return moved


class QueueBehindTest(unittest.TestCase):
    """1. The card's headline rule, read out of the shipped workflow."""

    def test_the_harness_queues_behind_the_proving_run(self):
        self.assertIs(
            _harness_concurrency().get("cancel-in-progress"), False,
            "the run proving commit N must finish; a newer push queues behind "
            "it. cancel-in-progress here freezes the channel on a busy night.",
        )

    def test_two_pushes_thirty_seconds_apart_complete_one_run_and_promote_once(self):
        """The card's acceptance test, and the shape of the incident evening."""
        outcomes = simulate(
            [(0.0, "N"), (30.0, "N+1")],
            cancel_in_progress=_harness_concurrency()["cancel-in-progress"],
            until=HARNESS_SECONDS,
        )
        self.assertEqual(outcomes["N"], COMPLETED)
        self.assertNotIn(CANCELLED, outcomes.values(),
                         "two pushes 30s apart must not produce a cancellation")
        self.assertEqual(_promotions(outcomes), ["N"],
                         "exactly one promotion inside one harness duration")

    def test_the_second_push_is_proved_next_not_dropped(self):
        outcomes = simulate(
            [(0.0, "N"), (30.0, "N+1")],
            cancel_in_progress=_harness_concurrency()["cancel-in-progress"],
            until=2 * HARNESS_SECONDS,
        )
        self.assertEqual(outcomes["N+1"], COMPLETED)
        self.assertEqual(sorted(_promotions(outcomes)), ["N", "N+1"])


class CancelInProgressStarvesTest(unittest.TestCase):
    """2. The counterfactual, so the test above is about the config and not
    about any config being green."""

    def test_cancelling_the_proving_run_completes_nothing(self):
        outcomes = simulate(
            [(0.0, "N"), (30.0, "N+1")],
            cancel_in_progress=True,
            until=HARNESS_SECONDS,
        )
        self.assertEqual(outcomes["N"], CANCELLED)
        self.assertEqual(_promotions(outcomes), [],
                         "a cancelled proving run promotes nothing")

    def test_a_merge_train_under_cancellation_never_advances_the_channel(self):
        """Fourteen merges inside one harness duration: the incident."""
        arrivals = [(i * 60.0, f"merge-{i}") for i in range(14)]
        outcomes = simulate(arrivals, cancel_in_progress=True,
                            until=13 * 60.0)
        self.assertEqual(_promotions(outcomes), [])
        self.assertEqual(
            sum(1 for s in outcomes.values() if s == CANCELLED), 13,
            "every merge but the last kills the run proving the one before it",
        )


class MergeTrainStillAdvancesTest(unittest.TestCase):
    """3. Three merges inside one harness duration still move `stable` once,
    and the head that was skipped says so rather than going quiet."""

    def test_three_merges_inside_one_harness_duration_promote_at_least_once(self):
        outcomes = simulate(
            [(0.0, "N"), (120.0, "N+1"), (240.0, "N+2")],
            cancel_in_progress=_harness_concurrency()["cancel-in-progress"],
            until=HARNESS_SECONDS,
        )
        self.assertGreaterEqual(
            len(_promotions(outcomes)), 1,
            "the channel must advance at least once during a merge train",
        )
        self.assertEqual(outcomes["N+2"], PENDING,
                         "the newest head queues behind, it is not dropped")

    def test_the_skipped_intermediate_head_leaves_a_named_receipt(self):
        outcomes = simulate(
            [(0.0, "N"), (120.0, "N+1"), (240.0, "N+2")],
            cancel_in_progress=_harness_concurrency()["cancel-in-progress"],
            until=HARNESS_SECONDS,
        )
        self.assertEqual(outcomes["N+1"], CANCELLED)
        decision = promote_channel.evaluate(
            {}, SHA, ancestry=promote_channel.AHEAD, conclusion=CANCELLED
        )
        self.assertFalse(decision.promote)
        self.assertEqual(decision.outcome, promote_channel.OUTCOME_CANCELLED)


class TheSandboxIsStillSerialisedTest(unittest.TestCase):
    """The constraint the fix must not break, and the gap it leaves.

    One sandbox repo (`bureau-harness`) serves every harness run, and each run
    SWEEPS every leftover branch matching the harness namespace — any run id.
    Two concurrent runs would delete each other's branches mid-scenario, so the
    group is deliberately a single constant shared by the push and pull_request
    triggers alike (scripts/harness/README.md).

    The cost of that sharing is written down rather than papered over: a PR
    harness run and a push-to-main harness run compete for the one pending
    slot, so main's proving run can be displaced by a PR's. That is what the
    incident record shows (run 33832750432, main@46ca2476, cancelled by the
    DRE-3059 PR run) and it is why the receipt below has to name the cause —
    fixing it means giving the sandbox a real lock, not a second group.
    """

    def test_the_group_is_one_constant_shared_by_every_trigger(self):
        group = _harness_concurrency().get("group")
        self.assertIsInstance(group, str)
        self.assertNotIn("${{", group,
                         "a per-ref group would let two runs share the sandbox")

    def test_a_pull_request_run_can_displace_mains_pending_run(self):
        outcomes = simulate(
            [(0.0, "pr"), (120.0, "main-head"), (240.0, "pr-again")],
            cancel_in_progress=_harness_concurrency()["cancel-in-progress"],
            until=HARNESS_SECONDS,
        )
        self.assertEqual(
            outcomes["main-head"], CANCELLED,
            "known residual gap: the trunk's proving run shares the pending "
            "slot with PR runs. The receipt must therefore name the cause.",
        )


class TheRuleIsWrittenDownTest(unittest.TestCase):
    """A change that contradicts a document updates it in the same PR. The
    release-channel doc described a channel that advances on every proven
    commit and said nothing about what happens when the proving runs pile
    up — which is the only condition under which it does not."""

    def setUp(self):
        self.doc = (ROOT / "docs" / "self-hosting.md").read_text()

    def test_the_doc_names_the_queue_behind_rule(self):
        self.assertIn("queue behind", self.doc.lower())

    def test_the_doc_says_why_cancel_in_progress_is_wrong_here(self):
        self.assertIn("cancel-in-progress", self.doc)
        for token in ("merge train", "pending"):
            self.assertIn(token, self.doc.lower(),
                          f"the doc does not explain {token}")

    def test_the_doc_names_the_three_receipt_reasons(self):
        for outcome in (promote_channel.OUTCOME_CANCELLED,
                        promote_channel.OUTCOME_FAILED,
                        promote_channel.OUTCOME_PROMOTING):
            self.assertIn(outcome, self.doc,
                          "a receipt vocabulary nobody wrote down is one "
                          "nobody can read at 2am")


if __name__ == "__main__":
    unittest.main()
