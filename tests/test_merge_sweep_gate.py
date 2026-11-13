"""A merge only sweeps the board when the merge changed something (DRE-2930).

Origin, measured: `linear-sync.yml` fired TWO board-wide reconcile passes on
every merge — `--promote-only` at :115 and `--close-epics` at :123 — whether
or not the merge changed anything either pass acts on. **47 cards merged on
2026-09-01, so those two lines bought roughly 94 extra full-board passes** on
top of the 4-per-hour cron and the 4-per-hour dispatch sweeps; the peak hour
ran 8 reconciles, not 4, and that day the workspace exhausted Linear's
2,500/hour quota. Nothing was wrong per merge. The cost was unbounded in merge
rate, so the pipeline was least reliable on its best days.

Option A from the card: gate on the merge having unblocked something. The
merged card's `blocks` relations are already known at that moment, and one
Linear read of that card answers both questions —

  * `--promote-only` acts on cards this merge unblocked, so it is needed only
    when the merged card actually reached a terminal state AND blocks at least
    one card that has not;
  * `--close-epics` can only be made newly true by this merge for the merged
    card's OWN parent, so it is needed only when that parent exists, is still
    open, and its children are now all terminal.

What this file pins, in three layers:

1. **The decision** — `merge_sweep_gate.sweeps()` over card shapes, including
   the two "unknown is not empty" fallbacks and the relation-DIRECTION trap
   (a forward `blocks` relation names the card itself under `issue` and the
   dependent under `relatedIssue`; reading the wrong side makes every merge
   look like it unblocked nothing, which would be silent and permanent).
2. **The fallbacks** — an unreadable board must still sweep; an exhausted
   quota must not (retrying at an exhausted limit deepens it — DRE-1921).
3. **The invocation count**, which is the acceptance criterion, counted where
   the passes now run. Until DRE-9254 that was linear-sync.yml's merge step,
   whose shell these tests executed verbatim. That step ran in the merge's
   `pull_request` run, where the read door refuses every read but the merged
   card's own, so its passes read Linear; the relay's card-done dispatch ran
   the same gate and the same passes a minute later in a Reconcile run the
   door serves (DRE-3645). DRE-9254 removed the inline passes, so the relay
   pass is the one place a merge's passes run, and `RelayPassTest` drives it:
   `reconcile.run([])` with `SWEEP_REASON=card-done`, the real gate over a
   fixture board, and a `reconcile.main` that records each pass it is asked
   for — fixture → gate → relay pass → invocation count.

The 15-minute cron sweep is deliberately untouched and `WorkflowWiringTest`
asserts it: it is the backstop for everything the gate declines to run, and
for a relay dispatch that is lost.
"""

import contextlib
import io
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
WORKFLOWS = ROOT / ".github" / "workflows"
LINEAR_SYNC = WORKFLOWS / "linear-sync.yml"
RECONCILE_YML = WORKFLOWS / "reconcile.yml"

sys.path.insert(0, str(SCRIPTS))
# `reconcile` binds these at import. The values test_sweep_dispatch_scope.py
# sets, so whichever file imports it first, the sweep's GitHub reads stay off
# the live repository.
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")
os.environ.setdefault("GH_TOKEN", "x")

import check_wip_cap as cwc  # noqa: E402
import lane_contract  # noqa: E402
import merge_sweep_gate as gate  # noqa: E402
import reconcile  # noqa: E402


# ---------------------------------------------------------------------------
# Card shapes — exactly what the gate's own query returns.
# ---------------------------------------------------------------------------
def _rel(kind, identifier, state, *, of="DRE-1"):
    """One relation node as Linear returns it on the FORWARD `relations` edge:
    `issue` is the card itself, `relatedIssue` is the other side. Verified
    against the live API on 2026-09-01 (DRE-2676 `blocks` DRE-2929 reads
    issue=DRE-2676, relatedIssue=DRE-2929)."""
    return {
        "type": kind,
        "issue": {"identifier": of},
        "relatedIssue": {"identifier": identifier, "state": {"name": state}},
    }


def _card(
    identifier="DRE-1",
    state="Done",
    relations=(),
    relations_truncated=False,
    parent=None,
):
    return {
        "identifier": identifier,
        "state": {"name": state},
        "relations": {
            "pageInfo": {"hasNextPage": relations_truncated},
            "nodes": list(relations),
        },
        "parent": parent,
    }


def _parent(identifier="DRE-900", state="In Progress", children=(), truncated=False):
    return {
        "identifier": identifier,
        "state": {"name": state},
        "children": {
            "pageInfo": {"hasNextPage": truncated},
            "nodes": [{"identifier": f"{identifier}-c{i}", "state": {"name": s}}
                      for i, s in enumerate(children)],
        },
    }


class GateDecisionTest(unittest.TestCase):
    """The decision itself: which sweeps THIS merge can have changed."""

    def test_a_merge_that_unblocks_nothing_runs_no_sweep(self):
        """THE card: a merged card blocking nothing, under no epic, made no
        card promotable and finished no epic — so it buys zero board passes."""
        self.assertEqual([], gate.sweeps(_card()))

    def test_a_card_blocking_only_terminal_cards_unblocks_nothing(self):
        card = _card(relations=[
            _rel("blocks", "DRE-2", "Done"),
            _rel("blocks", "DRE-3", "Canceled"),
            _rel("blocks", "DRE-4", "Duplicate"),
        ])
        self.assertEqual([], gate.sweeps(card))

    def test_a_card_that_blocks_a_live_card_promotes(self):
        """The behaviour that must not regress: a merge that DOES unblock a
        card still runs the promotion sweep."""
        card = _card(relations=[
            _rel("blocks", "DRE-2", "Done"),
            _rel("blocks", "DRE-3", "Backlog"),
        ])
        self.assertEqual([gate.PROMOTE], gate.sweeps(card))

    def test_a_card_the_done_guard_refused_unblocks_nothing(self):
        """`card-done` refuses to close a `no-code`/`DEMO:` card (the six
        portico false closes), so the card is still open — nothing downstream
        of it was unblocked and the sweep would find nothing."""
        card = _card(state="In Review", relations=[_rel("blocks", "DRE-3", "Backlog")])
        self.assertEqual([], gate.sweeps(card))

    def test_a_canceled_card_still_unblocks_its_dependents(self):
        """Terminal is Done/Canceled/Duplicate — the same set the dependency
        gate clears blockers on, not `Done` alone."""
        card = _card(state="Canceled", relations=[_rel("blocks", "DRE-3", "Todo")])
        self.assertEqual([gate.PROMOTE], gate.sweeps(card))

    def test_the_card_is_never_its_own_dependent(self):
        """The relation-direction trap, pinned on the read itself. A forward
        `blocks` node names the card under `issue` and the dependent under
        `relatedIssue`; `reconcile.advance_unblocked_epics` reads the `issue`
        side, which is the card asking the question. A gate that did the same
        would decide off its own state and never promote anybody — silently,
        and permanently. So assert WHICH card comes back, not just that some
        sweep did."""
        card = _card(
            identifier="DRE-1", relations=[_rel("blocks", "DRE-3", "Backlog", of="DRE-1")]
        )
        self.assertEqual(
            ["DRE-3"], [dep["identifier"] for dep in gate._dependents(card)]
        )
        self.assertEqual([gate.PROMOTE], gate.sweeps(card))

    def test_a_related_relation_is_not_a_blocker(self):
        """`related` is the sibling-card link every one of these cards carries;
        it blocks nothing and must not buy a sweep."""
        card = _card(relations=[_rel("related", "DRE-3", "Backlog")])
        self.assertEqual([], gate.sweeps(card))

    def test_a_finished_parent_epic_runs_the_epic_close(self):
        card = _card(parent=_parent(children=("Done", "Done", "Canceled")))
        self.assertEqual([gate.CLOSE_EPICS], gate.sweeps(card))

    def test_an_epic_with_an_open_sibling_closes_nothing(self):
        card = _card(parent=_parent(children=("Done", "In Review")))
        self.assertEqual([], gate.sweeps(card))

    def test_an_epic_with_no_done_child_is_not_finished(self):
        """Mirrors `reconcile.close_finished_epics`: all-terminal AND at least
        one Done. An epic whose every child was cancelled did not ship."""
        card = _card(state="Canceled", parent=_parent(children=("Canceled", "Canceled")))
        self.assertEqual([], gate.sweeps(card))

    def test_an_epic_with_no_children_is_not_finished(self):
        card = _card(parent=_parent(children=()))
        self.assertEqual([], gate.sweeps(card))

    def test_an_already_closed_parent_needs_no_sweep(self):
        """The epic closed on an earlier merge; re-sweeping the board to
        rediscover that is exactly the waste this card removes."""
        card = _card(parent=_parent(state="Done", children=("Done", "Done")))
        self.assertEqual([], gate.sweeps(card))

    def test_a_parentless_card_closes_no_epic(self):
        self.assertEqual([], gate.sweeps(_card(parent=None)))

    def test_both_conditions_run_both_sweeps_once_each(self):
        card = _card(
            relations=[_rel("blocks", "DRE-3", "Backlog")],
            parent=_parent(children=("Done", "Done")),
        )
        self.assertEqual([gate.PROMOTE, gate.CLOSE_EPICS], gate.sweeps(card))

    def test_a_full_relations_page_is_unknown_not_empty(self):
        """"We did not look" and "there is nothing there" are different
        answers (the `check_prose_blockers` rule). A card whose relations FILL
        the page may block something on page two, so it sweeps."""
        card = _card(relations=[], relations_truncated=True)
        self.assertEqual([gate.PROMOTE], gate.sweeps(card))

    def test_a_full_children_page_is_unknown_not_empty(self):
        card = _card(parent=_parent(children=("Done",), truncated=True))
        self.assertEqual([gate.CLOSE_EPICS], gate.sweeps(card))


class QueueNameTest(unittest.TestCase):
    """The queue the promotion moves cards out of is `lane_contract`'s, read
    under every name it carries (DRE-6748) — never a lane literal here."""

    def test_the_gate_reads_the_queue_off_the_contract(self):
        self.assertEqual(lane_contract.QUEUE_LANES, gate.QUEUE_LANES)

    def test_no_string_in_the_gate_names_the_retired_queue(self):
        path = str(SCRIPTS / "merge_sweep_gate.py")
        self.assertEqual(set(), lane_contract._scan_python(path, ["Backlog"]))


class ReadFailureTest(unittest.TestCase):
    """What the gate does when it cannot answer. Two different unknowns with
    two opposite right answers."""

    def _decide(self, gql):
        original = gate.linear_ops.gql
        gate.linear_ops.gql = gql
        try:
            return gate.decide("DRE-1")
        finally:
            gate.linear_ops.gql = original

    def test_an_unreadable_card_falls_back_to_both_sweeps(self):
        """Fail OPEN: a read we could not make is not a merge that changed
        nothing, and losing a promotion is worse than one extra pass."""
        def boom(*_a, **_k):
            raise gate.linear_ops.LinearError("schema exploded")

        self.assertEqual(list(gate.ALL_SWEEPS), self._decide(boom))

    def test_an_unknown_card_falls_back_to_both_sweeps(self):
        self.assertEqual(
            list(gate.ALL_SWEEPS), self._decide(lambda *_a, **_k: {"issue": None})
        )

    def test_a_rate_limited_read_runs_no_sweep(self):
        """The one unknown that must NOT fall open. The quota is already gone
        (the 2026-09-01 exhaustion is why this card exists) and two board-wide
        passes would deepen it — the DRE-1921 lesson: classify before
        retrying. The cron picks it up once the quota refills."""
        def limited(*_a, **_k):
            raise gate.linear_ops.LinearRateLimited("RATELIMITED")

        self.assertEqual([], self._decide(limited))

    def test_the_gate_never_writes_to_linear(self):
        """A gate that mutates the board is a second writer nobody registered.
        It reads one card and prints; every write stays in reconcile.py."""
        source = (SCRIPTS / "merge_sweep_gate.py").read_text()
        for writer in ("cmd_state", "cmd_comment", "cmd_advance", "cmd_label"):
            self.assertNotIn(writer, source, f"the gate must not call {writer}")


# ---------------------------------------------------------------------------
# Where the gate runs on a merge (DRE-9254).
# ---------------------------------------------------------------------------
def _commands(run: str) -> list[str]:
    """Every logical command line in `run`, with shell continuations folded
    (the same fold check_wip_cap.py does) and comment lines dropped."""
    folded = run.replace("\\\n", " ")
    return [
        ln.strip()
        for ln in folded.splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]


def reconcile_invocations(run: str) -> list[str]:
    """Every command line in `run` that executes reconcile.py."""
    return [ln for ln in _commands(run) if "reconcile.py" in ln]


class WorkflowWiringTest(unittest.TestCase):
    """Linear Sync runs neither the gate nor a pass (DRE-9254). A merge's
    `pull_request` run is refused every read but the merged card's own, so
    the passes there read Linear. The relay's card-done dispatch runs the
    same gate and the same passes in a Reconcile run the door serves."""

    def test_the_card_done_job_runs_no_gate_and_no_pass(self):
        steps = yaml.safe_load(LINEAR_SYNC.read_text())["jobs"]["card-done"]["steps"]
        for step in steps:
            run = step.get("run") or ""
            name = step.get("name") or step.get("uses")
            self.assertEqual([], reconcile_invocations(run), name)
            self.assertEqual(
                [], [ln for ln in _commands(run) if "merge_sweep_gate" in ln], name
            )

    def test_linear_sync_is_no_longer_a_wip_cap_promotion_path(self):
        """DRE-2529's guard finds promotion paths by reading reconcile.py
        invocations out of the workflows. With the passes gone, the merge
        handler promotes nothing; the relay pass promotes at the cap the
        Reconcile job already takes."""
        doc = yaml.safe_load(LINEAR_SYNC.read_text())
        self.assertEqual([], cwc.promotion_steps(doc))
        self.assertEqual([], cwc.check_workflow(doc, "linear-sync.yml"))

    def test_the_cron_sweep_is_untouched(self):
        """The 15-minute sweep stays ungated — it is the backstop for
        everything the gate declines, and for a relay dispatch that is lost."""
        reconcile_yml = RECONCILE_YML.read_text()
        self.assertNotIn("merge_sweep_gate", reconcile_yml)
        self.assertIn("reconcile.py", reconcile_yml)

    def test_no_workflow_runs_the_gate(self):
        """The gate runs in-process, inside the relay pass
        (`reconcile.sweep_scope`). A workflow that ran it again would be a
        second pass per merge, on whatever reads that run is served."""
        for path in sorted(WORKFLOWS.glob("*.yml")):
            commands = _commands(path.read_text())
            self.assertEqual(
                [], [ln for ln in commands if "merge_sweep_gate" in ln], path.name
            )


# ---------------------------------------------------------------------------
# The relay pass — the one place a merge's passes run (DRE-3645, DRE-9254).
# ---------------------------------------------------------------------------
def _flag(kwargs: dict) -> str:
    """The pass `reconcile.main` was asked for, as the gate's flag."""
    if kwargs.get("promote_only"):
        return gate.PROMOTE
    if kwargs.get("close_only"):
        return gate.CLOSE_EPICS
    return "full"


class RelayPassTest(unittest.TestCase):
    """The relay fires `repository_dispatch: reconcile` with `{reason:
    "card-done", identifier: "DRE-…"}` when a card reaches Done, and the
    reusable hands both to the pass as `SWEEP_REASON` / `SWEEP_CARD`.
    `reconcile.run([])` then asks this gate and runs one scoped pass per flag
    it names, each with `MERGED_CARD` set. `reconcile.main` is stubbed to
    record what it was asked for, so what is counted is what the pass would
    really run."""

    def _relay_pass(self, *, decide=None, board=None, card="DRE-1"):
        calls = []

        def record(**kwargs):
            calls.append((_flag(kwargs), os.environ.get("MERGED_CARD")))

        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.dict(
                os.environ, {"SWEEP_REASON": "card-done", "SWEEP_CARD": card}
            ))
            for name in ("MERGED_CARD", "BUREAU_READ"):
                os.environ.pop(name, None)
            stack.enter_context(mock.patch.object(reconcile, "main", side_effect=record))
            if decide is not None:
                stack.enter_context(
                    mock.patch.object(reconcile.merge_sweep_gate, "decide", side_effect=decide)
                )
            if board is not None:
                stack.enter_context(mock.patch.object(
                    gate.linear_ops, "gql", side_effect=lambda *_a, **_k: {"issue": board}
                ))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
            reconcile.run([])
            self.assertNotIn("MERGED_CARD", os.environ)
        return calls

    def test_the_relay_pass_asks_this_gate_for_its_card(self):
        asked = []
        self._relay_pass(decide=lambda card: asked.append(card) or [], card="dre-1")
        self.assertEqual(["DRE-1"], asked)

    def test_no_flags_runs_no_pass(self):
        self.assertEqual([], self._relay_pass(decide=lambda _card: []))

    def test_each_flag_runs_one_pass_with_the_merged_card_set(self):
        calls = self._relay_pass(decide=lambda _card: list(gate.ALL_SWEEPS))
        self.assertEqual(
            [(gate.PROMOTE, "DRE-1"), (gate.CLOSE_EPICS, "DRE-1")], calls
        )

    def test_the_merged_card_is_cleared_after_the_pass(self):
        """Left behind, it would scope the next pass in the same process to a
        card nobody named. `_relay_pass` asserts it on every case; this one
        runs a pass so the assertion is not vacuous."""
        self.assertTrue(self._relay_pass(decide=lambda _card: [gate.PROMOTE]))

    # -- fixture board → real gate → relay pass ----------------------------
    def test_a_merge_that_unblocks_nothing_costs_zero_board_passes(self):
        self.assertEqual([], self._relay_pass(board=_card()))

    def test_a_merge_that_unblocks_a_card_still_promotes_it(self):
        calls = self._relay_pass(
            board=_card(relations=[_rel("blocks", "DRE-3", "Backlog")])
        )
        self.assertEqual([(gate.PROMOTE, "DRE-1")], calls)

    def test_a_merge_that_finishes_an_epic_still_closes_it(self):
        calls = self._relay_pass(board=_card(parent=_parent(children=("Done", "Done"))))
        self.assertEqual([(gate.CLOSE_EPICS, "DRE-1")], calls)

    def test_a_merge_that_does_both_costs_two_passes_and_no_more(self):
        calls = self._relay_pass(
            board=_card(
                relations=[_rel("blocks", "DRE-3", "Backlog")],
                parent=_parent(children=("Done", "Done")),
            )
        )
        self.assertEqual([(gate.PROMOTE, "DRE-1"), (gate.CLOSE_EPICS, "DRE-1")], calls)

    def test_a_busy_day_of_merges_that_change_nothing_costs_nothing(self):
        """The DRE-2930 arithmetic: 47 merges that unblock nothing bought 94
        board passes before the gate, and buy 0 through the relay pass."""
        total = sum(len(self._relay_pass(board=_card())) for _ in range(47))
        self.assertEqual(0, total)


if __name__ == "__main__":
    unittest.main()
