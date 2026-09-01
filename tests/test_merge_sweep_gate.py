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
3. **The invocation count**, which is the acceptance criterion. The scenario
   tests EXECUTE the merge step's own shell, lifted verbatim from
   linear-sync.yml, against a stub `reconcile.py` that logs every call — so
   the number counted is the number the workflow would really run. The gate
   the shell calls is the real module, driven by a fixture board: fixture →
   gate → shipped shell → invocation count, with nothing re-implemented.

The 15-minute cron sweep is deliberately untouched and `WorkflowWiringTest`
asserts it: this card changes the merge-triggered path only, and the cron
remains the backstop for everything the gate declines to run now.
"""

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
WORKFLOWS = ROOT / ".github" / "workflows"
LINEAR_SYNC = WORKFLOWS / "linear-sync.yml"
RECONCILE_YML = WORKFLOWS / "reconcile.yml"

sys.path.insert(0, str(SCRIPTS))

import check_wip_cap as cwc  # noqa: E402
import merge_sweep_gate as gate  # noqa: E402


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
        """The relation-direction trap. A forward `blocks` node names the card
        itself under `issue`; a gate reading that side sees a Done card,
        concludes nothing is blocked, and skips every promotion forever —
        silently. Here the ONLY non-terminal id is on the `relatedIssue` side,
        so reading the wrong one turns this red."""
        card = _card(state="Done", relations=[_rel("blocks", "DRE-3", "Backlog")])
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
# The shipped shell, lifted verbatim (pattern: test_linear_sync_done_gate.py).
# ---------------------------------------------------------------------------
MARKER_OPEN = "# >>> DRE-2930 merge-sweep gate"
MARKER_CLOSE = "# <<< DRE-2930 merge-sweep gate"


def merge_step_run() -> str:
    """The whole `run:` script of linear-sync.yml's merge step."""
    doc = yaml.safe_load(LINEAR_SYNC.read_text())
    runs = [
        step["run"]
        for job in (doc.get("jobs") or {}).values()
        for step in (job or {}).get("steps") or []
        if isinstance(step.get("run"), str) and MARKER_OPEN in step["run"]
    ]
    assert len(runs) == 1, (
        f"expected exactly one merge-sweep gate block in linear-sync.yml, "
        f"found {len(runs)}"
    )
    return runs[0]


def gate_block() -> str:
    """Just the fenced gate region — the part the scenario tests execute."""
    lines = merge_step_run().splitlines()
    start = next(i for i, ln in enumerate(lines) if MARKER_OPEN in ln)
    end = next(i for i, ln in enumerate(lines) if MARKER_CLOSE in ln)
    assert start < end, "gate markers are out of order in linear-sync.yml"
    return textwrap.dedent("\n".join(lines[start + 1 : end]))


def reconcile_invocations(run: str) -> list[str]:
    """Every logical command line in `run` that executes reconcile.py, with
    shell continuations folded (the same fold check_wip_cap.py does)."""
    folded = run.replace("\\\n", " ")
    return [
        ln.strip()
        for ln in folded.splitlines()
        if "reconcile.py" in ln and not ln.strip().startswith("#")
    ]


class WorkflowWiringTest(unittest.TestCase):
    """The workflow side of the acceptance criteria."""

    def test_the_merge_step_has_exactly_one_reconcile_invocation(self):
        """`:115` and `:123` collapse to ONE invocation site, parameterised by
        the gate — two hardcoded full-board passes become zero, one or two
        decided passes."""
        self.assertEqual(1, len(reconcile_invocations(merge_step_run())))

    def test_no_sweep_flag_is_hardcoded_in_the_merge_step(self):
        """A literal flag left behind is an ungated sweep: it would run on
        every merge again and the gate above it would be decoration."""
        line = reconcile_invocations(merge_step_run())[0]
        for flag in gate.ALL_SWEEPS:
            self.assertNotIn(flag, line, f"{flag} is still hardcoded on the merge path")

    def test_the_merge_step_runs_the_gate(self):
        self.assertIn("merge_sweep_gate.py", merge_step_run())

    def test_linear_sync_is_still_a_wip_cap_promotion_path(self):
        """DRE-2529's guard finds promotion paths by reading reconcile.py
        invocations out of the workflows. Parameterising the flag must not
        hide this path from it — a promotion path the guard cannot see is one
        that can silently re-acquire a hardcoded cap."""
        doc = yaml.safe_load(LINEAR_SYNC.read_text())
        self.assertTrue(cwc.promotion_steps(doc), "linear-sync.yml no longer detected")
        self.assertEqual([], cwc.check_workflow(doc, "linear-sync.yml"))

    def test_the_cron_sweep_is_untouched(self):
        """Scope: this card changes the merge-triggered path only. The
        15-minute sweep stays ungated — it is the backstop for everything the
        gate declines."""
        reconcile_yml = RECONCILE_YML.read_text()
        self.assertNotIn("merge_sweep_gate", reconcile_yml)
        self.assertIn("reconcile.py", reconcile_yml)

    def test_no_other_workflow_gates_its_sweep(self):
        """plan.yml's epic-activate promotion is a different event (an epic
        was approved, not a card merged) and this gate answers a question
        about a MERGED card — wiring it there would refuse every activation."""
        for path in sorted(WORKFLOWS.glob("*.yml")):
            if path.name == LINEAR_SYNC.name:
                continue
            self.assertNotIn("merge_sweep_gate", path.read_text(), path.name)


# ---------------------------------------------------------------------------
# Sweep invocation counting — the acceptance criterion, run end to end.
# ---------------------------------------------------------------------------
#: A stand-in reconcile.py that records its argv instead of sweeping Linear.
COUNTING_RECONCILE = (
    "import os, sys\n"
    "with open(os.environ['SWEEP_LOG'], 'a') as fh:\n"
    "    fh.write(' '.join(sys.argv[1:]) + '\\n')\n"
)

#: A stand-in gate that emits whatever the test asks for — used only to pin
#: the SHELL's behaviour (one reconcile call per emitted flag, none for none).
ECHO_GATE = (
    "import os\n"
    "print(os.environ.get('GATE_FLAGS', ''))\n"
)

#: The REAL gate, driven by a fixture board instead of the live API. This is
#: what makes the counts below end-to-end rather than a mock of themselves.
REAL_GATE = (
    "import json, os, sys\n"
    f"sys.path.insert(0, {str(SCRIPTS)!r})\n"
    "import linear_ops, merge_sweep_gate\n"
    "payload = json.load(open(os.environ['BOARD_FIXTURE']))\n"
    "merge_sweep_gate.linear_ops.gql = lambda *a, **k: payload\n"
    "sys.exit(merge_sweep_gate.main(['merge_sweep_gate.py', 'DRE-1']))\n"
)


class SweepInvocationCountTest(unittest.TestCase):
    """Counts the board-wide passes ONE merge actually costs, by running the
    workflow's own shell against a counting stub."""

    def _run(self, gate_script, tmp, **env):
        scripts = tmp / ".bureau-pipeline" / "scripts"
        scripts.mkdir(parents=True, exist_ok=True)
        (scripts / "reconcile.py").write_text(COUNTING_RECONCILE)
        (scripts / "merge_sweep_gate.py").write_text(gate_script)
        log = tmp / "sweeps.log"
        script = 'CARD="DRE-1"\n' + gate_block()
        proc = subprocess.run(  # nosec B603 — fixed argv, test-local script
            ["bash", "-e", "-c", script],
            cwd=tmp,
            capture_output=True,
            text=True,
            env={
                "PATH": os.environ["PATH"],
                "GITHUB_REPOSITORY": "dreadnought-foundry/bureau-pipeline",
                "MAX_WIP": "8",
                "SWEEP_LOG": str(log),
                **env,
            },
        )
        self.assertEqual(0, proc.returncode, proc.stderr)
        return [ln for ln in log.read_text().splitlines() if ln] if log.exists() else []

    def _with_board(self, tmp, card):
        fixture = tmp / "board.json"
        fixture.write_text(json.dumps({"issue": card}))
        return self._run(REAL_GATE, tmp, BOARD_FIXTURE=str(fixture))

    # -- the shell contract -------------------------------------------------
    def test_no_flags_invokes_no_sweep(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual([], self._run(ECHO_GATE, Path(d), GATE_FLAGS=""))

    def test_each_emitted_flag_invokes_the_sweep_exactly_once(self):
        with tempfile.TemporaryDirectory() as d:
            calls = self._run(
                ECHO_GATE,
                Path(d),
                GATE_FLAGS=f"{gate.PROMOTE}\n{gate.CLOSE_EPICS}",
            )
        self.assertEqual([gate.PROMOTE, gate.CLOSE_EPICS], calls)

    # -- fixture board → real gate → shipped shell -------------------------
    def test_a_merge_that_unblocks_nothing_costs_zero_board_passes(self):
        """The 94: today this same merge runs two full-board passes."""
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual([], self._with_board(Path(d), _card()))

    def test_a_merge_that_unblocks_a_card_still_promotes_it(self):
        """The behaviour that must not regress, asserted through the shell the
        workflow actually runs."""
        with tempfile.TemporaryDirectory() as d:
            calls = self._with_board(
                Path(d), _card(relations=[_rel("blocks", "DRE-3", "Backlog")])
            )
        self.assertEqual([gate.PROMOTE], calls)

    def test_a_merge_that_finishes_an_epic_still_closes_it(self):
        with tempfile.TemporaryDirectory() as d:
            calls = self._with_board(
                Path(d), _card(parent=_parent(children=("Done", "Done")))
            )
        self.assertEqual([gate.CLOSE_EPICS], calls)

    def test_a_merge_that_does_both_costs_two_passes_and_no_more(self):
        with tempfile.TemporaryDirectory() as d:
            calls = self._with_board(
                Path(d),
                _card(
                    relations=[_rel("blocks", "DRE-3", "Backlog")],
                    parent=_parent(children=("Done", "Done")),
                ),
            )
        self.assertEqual([gate.PROMOTE, gate.CLOSE_EPICS], calls)

    def test_a_busy_day_of_merges_that_change_nothing_costs_nothing(self):
        """The card's arithmetic, as a test: 47 merges that unblock nothing
        bought 94 board passes and now buy 0. The scaling IS the defect —
        a good day was the worst day for the quota."""
        total = 0
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            for _ in range(47):
                (tmp / "sweeps.log").unlink(missing_ok=True)
                total += len(self._with_board(tmp, _card()))
        self.assertEqual(0, total)


if __name__ == "__main__":
    unittest.main()
