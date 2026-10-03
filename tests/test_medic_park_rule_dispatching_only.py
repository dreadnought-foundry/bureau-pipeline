"""The park rule is for runs that START WORK, not for bookkeeping runs
(Stage 2 fix #23, package BP-6).

Origin: DRE-5620 and DRE-5622, 2026-10-02. Both PRs had merged. Each card's
`card-done` job in Linear Sync died on the fleet's exhausted Linear quota
(runs 37051027773 at 11:58 PT and 37056899351 at 12:52 PT), and the cards
stayed In Review. The medic run for each (37051307731, 37057166857) did three
things, in this order:

  1. `classify` read the log as `linear_ratelimited`, and the limit step posted
     `🪦 limit-death: kind=linear stage=sync … run=37051027773` on the card. That
     marker is how the reconcile sweep brings a limit death back
     (`limit_recovery.py` re-runs the original `sync` run once the quota has
     refilled).
  2. The retry gate read the card, found a leftover `needs-human` label, and
     answered `retry=false rule=card-parked`.
  3. So `retry_declined` posted `🩺 medic-retry-declined` on the card, AFTER
     the marker.

The retry itself was never going to happen on those two runs: `class` and
`limit` gate the `retry` job off on their own. The damage was step 3. The 🩺
receipt is a pipeline act (trailer, and an opening glyph in
`limit_recovery.RECEIPT_GLYPHS`), and `limit_recovery.waiting()` reads any later
pipeline receipt as closing the limit-death marker. The medic's own refusal
took the card off the one path that would have brought it back.

The refusal was also wrong on its own terms. The park rule (DRE-2954) exists
so the medic does not start NEW AGENT WORK on a card a person owns: a build, a
fix, a plan. A Linear Sync rerun starts no agent: it finishes bookkeeping
for work that already merged, and whoever owns the card wants that
bookkeeping done. So the gate now takes the failed workflow's name and applies
the park rule to every workflow EXCEPT the named exempt ones. An empty or
unrecognized name keeps the park rule: that is the DRE-2937 incident's
protection, and an unknown run must not lose it.

Merge Gate is exempt too, and it is NOT "bookkeeping" in the sense of "does
not merge" (Stage 2 review item 44, M12): re-running it starts no agent work;
the gate re-evaluates and merges only on critic APPROVE and green CI. A
`needs-human` label is mostly applied mechanically when a robot loop gives up
and often lingers stale, and the merge gate's real safety is the critic and
CI, not the label. The CEO decided on 2026-10-02 (about 17:37 PT) that
"needs a human" does NOT block merging.
"""

import contextlib
import io
import os
import sys
import unittest
from unittest import mock

import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/test")
os.environ.setdefault("GH_TOKEN", "test")

import dead_run  # noqa: E402
import limit_recovery  # noqa: E402
import medic_classify  # noqa: E402
import medic_retry  # noqa: E402

FIXTURES = os.path.join(ROOT, "tests", "fixtures")
WORKFLOW = os.path.join(ROOT, ".github", "workflows", "medic.yml")

# The failed card-done run's own log, trimmed to the lines that classify it.
# Nothing in it is reconstructed: these are lines 33, 56 and 406-408 of
# `gh run view 37051027773 --log-failed`, byte for byte.
DRE5620_LOG = os.path.join(
    FIXTURES, "linear-sync-card-done-ratelimited-dre5620-2026-10-02.log"
)
DRE5620_RUN = "37051027773"
RUN_STARTED_AT = "2026-10-02T18:58:28Z"

# The card as the gate read it at 12:01 PT: In Review, with a leftover hold
# label (the medic's own detail line: "the 'needs-human' label is on it").
DRE5620_FACTS = {
    "state": "In Review",
    "labels": ["repo:agent-bureau", dead_run.HOLD_LABEL],
    "comments": [],
}

# The names `github.event.workflow_run.name` carries: the calling STUB's
# `name:`, which is what medic.yml's stub watches by.
BOOKKEEPING = ("Linear Sync", "Merge Gate")
DISPATCHING = ("Agent Task", "Agent Fix", "Agent Plan")


def _decide(workflow, *, facts=None, log=DRE5620_LOG,
            branch="agent/DRE-5620-chain-risk-reason") -> str:
    argv = [
        "decide",
        "--branch", branch,
        "--log", log,
        "--run-started-at", RUN_STARTED_AT,
        "--run-id", DRE5620_RUN,
        "--run-attempt", "1",
    ]
    if workflow is not None:
        argv += ["--workflow", workflow]
    buf = io.StringIO()
    with mock.patch.object(
        medic_retry, "card_facts", return_value=facts or DRE5620_FACTS
    ):
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            rc = medic_retry.main(argv)
    assert rc == 0
    return buf.getvalue()


def _outputs(text: str) -> dict:
    return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)


class TheIncidentShapeTest(unittest.TestCase):
    """The exact DRE-5620/5622 shape, through the CLI medic.yml calls."""

    def test_the_fixture_is_the_linear_quota_death_the_medic_saw(self):
        with open(DRE5620_LOG, encoding="utf-8") as f:
            log = f.read()
        self.assertTrue(medic_classify.is_linear_rate_limited(log))
        self.assertIn("linear-budget: 0 → 0", log)
        self.assertIn("budget: fleet", log)

    def test_a_linear_sync_run_on_a_held_card_is_not_refused(self):
        out = _outputs(_decide("Linear Sync"))
        self.assertEqual("true", out["retry"])
        self.assertEqual(medic_retry.RULE_NONE, out["rule"])

    def test_the_card_is_still_named_for_the_limit_marker(self):
        """The limit step writes `🪦 limit-death` to `steps.r.outputs.card`.
        Exempting the run from the park rule must not stop naming its card,
        or the marker that brings the card back is never written."""
        self.assertEqual("DRE-5620", _outputs(_decide("Linear Sync"))["card"])

    def test_dre5622_reads_the_same(self):
        out = _outputs(
            _decide("Linear Sync", branch="agent/DRE-5622-own-instance-writer")
        )
        self.assertEqual("true", out["retry"])
        self.assertEqual("DRE-5622", out["card"])

    def test_the_refusal_receipt_is_what_closed_the_limit_marker(self):
        """The reason the gate's answer matters on a run it could not retry
        anyway: a 🩺 refusal after the 🪦 marker takes the card off the sweep's
        limit-recovery path. With the gate answering `retry=true`, medic.yml's
        `retry_declined` job (gated on `retry == 'false'`) posts nothing, and
        the marker stays open."""
        marker = dead_run.limit_marker("linear", "sync", None, DRE5620_RUN)
        self.assertIsNotNone(limit_recovery.waiting([marker]))
        refusal = medic_retry.declined_comment(
            medic_retry.decide(
                parked_because=f"the '{dead_run.HOLD_LABEL}' label is on it"
            ),
            run_url=f"https://github.com/dreadnought-foundry/agent-bureau/actions/runs/{DRE5620_RUN}",
        )
        self.assertIsNone(limit_recovery.waiting([marker, refusal]))


class TheRuleIsForDispatchingWorkflowsTest(unittest.TestCase):
    def test_a_held_cards_merge_gate_death_is_retried(self):
        """Stage 2 review item 44 (M12). Re-running the gate starts no agent
        work; the gate re-evaluates and merges only on critic APPROVE and
        green CI, so a leftover hold label does not refuse the retry. The
        CEO's decision of 2026-10-02: the hold does not block merging."""
        for name in ("Merge Gate", "merge gate", "Merge Gate (reusable)"):
            with self.subTest(workflow=name):
                out = _outputs(_decide(name))
                self.assertEqual("true", out["retry"])
                self.assertEqual(medic_retry.RULE_NONE, out["rule"])
                self.assertEqual("DRE-5620", out["card"])
                self.assertFalse(medic_retry.park_rule_applies(name))

    def test_the_merge_gate_exemption_is_not_called_non_merging(self):
        """The wording the review asked for: the gate is exempt because a
        rerun starts no agent work and merges only on APPROVE and green CI,
        never because it "does not merge"."""
        with open(medic_retry.__file__, encoding="utf-8") as f:
            source = f.read()
        self.assertIn("critic APPROVE and green CI", source)

    def test_a_dispatching_workflow_still_honors_the_park(self):
        """DRE-2937's protection, unchanged: a build, fix or plan rerun on a
        held card is new agent work a person has said stop to."""
        for name in DISPATCHING:
            with self.subTest(workflow=name):
                out = _outputs(_decide(name))
                self.assertEqual("false", out["retry"])
                self.assertEqual(medic_retry.RULE_PARKED, out["rule"])

    def test_no_workflow_name_keeps_the_park(self):
        """Fail closed. An old stub, a missing input, an empty event field:
        none of them is evidence the run is bookkeeping."""
        out = _outputs(_decide(None))
        self.assertEqual("false", out["retry"])
        self.assertEqual(medic_retry.RULE_PARKED, out["rule"])
        out = _outputs(_decide(""))
        self.assertEqual(medic_retry.RULE_PARKED, out["rule"])

    def test_an_unrecognized_workflow_keeps_the_park(self):
        for name in ("QA Review", "Verify", "Something New"):
            with self.subTest(workflow=name):
                self.assertEqual(
                    medic_retry.RULE_PARKED, _outputs(_decide(name))["rule"]
                )

    def test_the_name_is_matched_the_way_dead_run_matches_it(self):
        """Prefix, case-insensitive: the stub is "Linear Sync" and the
        reusable is "Linear Sync (reusable)", and both must read the same
        (`dead_run._STAGE_BY_WORKFLOW`'s rule)."""
        for name in ("Linear Sync", "linear sync", "Linear Sync (reusable)",
                     "  Merge Gate  ", "MERGE GATE (reusable)"):
            with self.subTest(workflow=name):
                self.assertFalse(medic_retry.park_rule_applies(name))
        for name in ("", None, "Agent Task", "Agent Plan (reusable)",
                     "Sync Linear", "Gate Merge", "QA Review"):
            with self.subTest(workflow=name):
                self.assertTrue(medic_retry.park_rule_applies(name))

    def test_the_bookkeeping_set_is_declared_as_data(self):
        self.assertEqual(
            {"linear sync", "merge gate"},
            set(medic_retry.BOOKKEEPING_WORKFLOWS),
        )

    def test_only_the_park_rule_moves(self):
        """A bookkeeping run keeps every other rule. A turn-cap death read off
        its log still declines, whatever the workflow."""
        out = _outputs(_decide(
            "Linear Sync",
            log=os.path.join(FIXTURES, "agent-task-turn-exhaustion-2026-09-01.log"),
        ))
        self.assertEqual("false", out["retry"])
        self.assertEqual(medic_retry.RULE_TURN_EXHAUSTION, out["rule"])


def _medic() -> dict:
    with open(WORKFLOW, encoding="utf-8") as f:
        return yaml.safe_load(f)


class MedicPassesTheWorkflowNameTest(unittest.TestCase):
    def setUp(self):
        self.step = next(
            s for s in _medic()["jobs"]["classify"]["steps"] if s.get("id") == "r"
        )

    def test_the_gate_step_hands_over_the_failed_workflows_name(self):
        env = self.step.get("env") or {}
        self.assertEqual(
            "${{ github.event.workflow_run.name }}", env.get("WORKFLOW_NAME")
        )
        self.assertIn('--workflow "$WORKFLOW_NAME"', self.step["run"])

    def test_the_name_never_reaches_the_shell_line_inline(self):
        """DRE-1996: the run name is agent-influenced text. env, never `${{ }}`
        in the script."""
        self.assertNotIn("workflow_run.name", self.step["run"])


if __name__ == "__main__":
    unittest.main()
