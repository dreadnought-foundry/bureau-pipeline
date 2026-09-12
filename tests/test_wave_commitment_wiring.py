"""Progressive commitment, wired into the rail (DRE-2846).

`scripts/wave_commitment.py` is only worth anything if the run that writes a
wave plan records the commitment, the sweep reads that record, and the standard
the CEO and the planner are held to says what the record means. These tests pin
the rail:

  1. THE RUN — plan.yml records the commitment from the same wave plan it just
     checked, and every step is gated on the wave shape.
  2. THE SWEEP READS IT — reconcile imports the module, refuses a
     committed-in-sequence card in `promote_ready`, and sends an epic whose
     turn has come to the lane that owes a plan artifact rather than onward
     into the build path.
  3. THE STANDARD — `standards/wave-plan.md` states that a wave's approval is
     the shape and the order and nothing more, names the recorded state, and
     says a reorder or a drop needs no re-approval. Anything the standard
     stops saying is a rule the pipeline enforces from memory.
  4. NO SECOND VOCABULARY — the lanes this module uses are derived from the
     lane contract and the shape vocabulary, never typed in.
"""

import os
import re
import sys
import unittest

import yaml

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
WF = os.path.join(REPO, ".github", "workflows", "plan.yml")
SCRIPTS = os.path.join(REPO, "scripts")
STANDARD = os.path.join(REPO, "standards", "wave-plan.md")
sys.path.insert(0, SCRIPTS)
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import lane_contract  # noqa: E402
import plan_run  # noqa: E402
import reconcile  # noqa: E402
import wave_commitment as wc  # noqa: E402

PLAN_PATH = "wave-plan.md"


def wf_steps() -> list:
    doc = yaml.safe_load(open(WF, encoding="utf-8").read())
    return [s for job in doc["jobs"].values() for s in job.get("steps") or []]


def step_named(fragment: str) -> dict:
    for s in wf_steps():
        if fragment.lower() in (s.get("name") or "").lower():
            return s
    raise AssertionError(
        f"no step whose name contains {fragment!r}; have: "
        + ", ".join(repr(s.get("name")) for s in wf_steps()))


def body_of(step: dict) -> str:
    return str(step.get("run") or (step.get("with") or {}).get("prompt") or "")


def sweep_turn(*dependents: str, children: tuple = ()):
    """Run the sweep's turn for real: `DRE-2900` reached Done and it blocks
    `dependents`, each a committed-in-sequence epic still in Backlog with every
    blocker met. Returns the `cmd_advance`, `cmd_comment` and `gql` mocks.

    `gql` answers by the QUERY it is asked, not from a list — the Done epic's
    forward relations, or the card itself (with `children`) — so the fake
    serves the same number of reads whether or not the turn still reads the
    card to decide a dispatch (DRE-3664 removed that read with the dispatch).
    """
    from unittest.mock import patch

    record = wc.commitment_comment(
        "DRE-2719",
        {"key": "route", "title": "The wave route", "depends_on": ["standard"],
         "status": wc.COMMITTED},
        position=2, total=3)

    def answer(query, variables=None):
        if "relations" in query:
            return {"issue": {"relations": {"nodes": [
                {"type": "blocks", "issue": {"identifier": dep}}
                for dep in dependents]}}}
        ident = (variables or {}).get("id", "DRE-2901")
        return {"issue": {"id": "u", "identifier": ident, "title": "route",
                          "description": "x",
                          "labels": {"nodes": [{"name": "agent:planner"}]},
                          "children": {"nodes": [{"identifier": kid}
                                                 for kid in children]}}}

    with patch.object(reconcile.linear_ops, "gql", side_effect=answer) as gql, \
        patch.object(reconcile.linear_ops, "comment_bodies", return_value=[record]), \
        patch.object(reconcile.mid_epic, "last_green_light", return_value=None), \
        patch.object(reconcile, "card_state", return_value="Backlog"), \
        patch.object(reconcile, "epic_blockers_unmet", return_value=False), \
        patch.object(reconcile.linear_ops, "cmd_advance") as advance, \
        patch.object(reconcile.linear_ops, "cmd_comment") as comment:
        reconcile.advance_unblocked_epics("DRE-2900")
    return advance, comment, gql


class TheRunRecordsTheCommitmentTest(unittest.TestCase):
    def test_the_run_records_the_commitment_from_the_plan_it_checked(self):
        step = step_named("record the commitment")
        self.assertIn("wave_commitment.py record", body_of(step))
        self.assertIn(PLAN_PATH, body_of(step),
                      "the commitment is recorded from the plan this run "
                      "wrote, never from a file nothing produced")

    def test_it_is_gated_on_the_wave_shape(self):
        self.assertIn("route == 'wave'",
                      str(step_named("record the commitment").get("if") or ""),
                      "recording a wave's commitment on a one-off would "
                      "invent a wave nobody planned")

    def test_it_runs_after_the_check_not_before(self):
        names = [(s.get("name") or "") for s in wf_steps()]
        check = next(i for i, n in enumerate(names) if "wave plan — check" in n.lower())
        record = next(i for i, n in enumerate(names) if "record the commitment" in n.lower())
        self.assertGreater(record, check,
                           "a commitment recorded from an unchecked plan "
                           "commits the wave to epics the gate would refuse")

    def test_the_turn_lands_the_epic_where_the_run_parks_it_for_the_ceo(self):
        """The other half of the turn, closed mechanically: the lane a turn
        sends an epic to is the one plan.yml runs the planner in, and that run
        ends by parking the epic in the decision lane on its fresh artifact.
        Without this the claim 'it arrives in Green Light on its own' would be
        prose."""
        self.assertIn(wc.decision_lane(), body_of(step_named("Epic → Green Light")))
        route = body_of(step_named("Route — plan or activate"))
        self.assertIn(wc.turn_lane(), route,
                      "the planner run's own plan route puts the epic in the "
                      "lane a turn sends it to")

    def test_the_announcement_still_says_nothing_is_approved(self):
        body = body_of(step_named("wave plan — announce"))
        self.assertIn("own", body.lower())
        self.assertTrue(
            re.search(r"nothing is approved|not(hing)? green.?lit", body, re.I),
            "the run must not let a wave plan read as an approval of its epics",
        )


class TheSweepReadsTheRecordTest(unittest.TestCase):
    def test_reconcile_imports_the_module(self):
        self.assertIs(reconcile.wave_commitment, wc)

    def test_promote_ready_consults_the_commitment(self):
        src = open(os.path.join(SCRIPTS, "reconcile.py"), encoding="utf-8").read()
        promote = src.split("def promote_ready", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("wave_commitment.promotion_refusal", promote)

    def test_the_turn_goes_to_the_artifact_lane_not_onward(self):
        src = open(os.path.join(SCRIPTS, "reconcile.py"), encoding="utf-8").read()
        advance = src.split("def advance_unblocked_epics", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("wave_commitment.turn_arrival", advance)

    def test_a_committed_epic_reaching_its_turn_goes_to_the_artifact_lane(self):
        """The sweep, run for real. Its predecessor is Done and its turn has
        come — and it goes to the lane that owes a plan artifact, never onward
        into the build path and never to Triage, which is the broken-card lane
        and not a turn. The move is the whole of what the sweep does about the
        planner run: it fires nothing itself (DRE-3664)."""
        from unittest.mock import patch

        with patch.object(plan_run, "fire") as fire:
            advance, comment, _ = sweep_turn("DRE-2901")
        advance.assert_called_once_with("DRE-2901", wc.turn_lane(), "Backlog")
        self.assertIn("plan artifact", comment.mock_calls[0].args[1].lower())
        fire.assert_not_called()

    def test_the_turn_relies_on_the_lane_entry_and_starts_nothing_itself(self):
        """INVERTED by DRE-3664. This test used to pin that the turn asked for
        the planner run explicitly — on the belief that nothing dispatched off
        the lane that owes a plan artifact — and said "could NOT be started"
        when the ask failed. The relay DOES dispatch `agent-plan` on every
        entry into that lane (DRE-1913, label or no label since DRE-3030), so
        the sweep's ask was the second of two dispatches for one lane move, and
        the second one found out on a hosted runner that had already billed a
        minute. Now the note says what starts the run and what asks if nothing
        has; it never claims a run the sweep did not start (the DRE-1254
        false-receipt class)."""
        from unittest.mock import patch

        with patch.object(plan_run, "fire") as fire:
            _, comment, _ = sweep_turn("DRE-2901")
        fire.assert_not_called()
        said = comment.mock_calls[0].args[1]
        self.assertNotIn("has been started", said)
        self.assertNotIn("could NOT be started", said)
        self.assertIn(f"`{wc.turn_lane()}`", said)
        self.assertIn("stalled-planning alarm", said)

    def test_an_epic_that_already_has_a_plan_is_not_re_dispatched(self):
        """`plan.yml` routes an epic with children to ACTIVATE — which
        green-lights it and promotes its children — but ONLY off an In
        Progress entry (DRE-3100); a Planning entry always plans. The sweep
        used to read the card's children to refuse its own ask for such an
        epic. It makes no ask now (DRE-3664), so it reads nothing for one: the
        one Linear read the turn makes is the Done epic's forward relations."""
        from unittest.mock import patch

        with patch.object(plan_run, "fire") as fire:
            _, _, gql = sweep_turn("DRE-2901", children=("DRE-2950",))
        fire.assert_not_called()
        self.assertEqual(
            gql.call_count, 1,
            "the turn read the card again — the only reason it ever did was "
            "to decide a dispatch it no longer makes")

    def test_the_sweep_never_sends_a_committed_epic_to_an_active_lane(self):
        """Its turn is a turn to be PLANNED, not a turn to be built: an
        active lane is the green light, and the wave's approval was not it."""
        arrival = wc.turn_arrival(
            "DRE-2900",
            [wc.commitment_comment("DRE-2719",
                                   {"key": "route", "title": "The wave route",
                                    "depends_on": ["standard"],
                                    "status": wc.COMMITTED},
                                   position=2, total=3)],
        )
        self.assertIsNotNone(arrival)
        self.assertNotIn(arrival.lane, reconcile.EPIC_ACTIVE_STATES)


class OneAskForThePlannerRunTest(unittest.TestCase):
    """Two paths send an epic to the lane that owes a plan artifact — the
    sweep, when the predecessor reaches Done, and the wave commitment itself,
    for the epic that has no predecessor to wait for. The lane entry itself is
    a planner dispatch (the relay's, DRE-1913 / DRE-3030), and NEITHER path
    asks for a second one: the wave's own turn stopped in DRE-3659, the
    sweep's in DRE-3664. `plan_run.note` — the one place the ask used to be
    written, kept there so that it would go from one place — is gone with its
    last caller; what `plan_run` still holds is the payload and the dispatch
    the Todo re-dispatch and the review re-run genuinely make."""

    def source_of(self, module: str, func: str) -> str:
        src = open(os.path.join(SCRIPTS, module), encoding="utf-8").read()
        return src.split(f"def {func}", 1)[1].split("\ndef ", 1)[0]

    def test_there_is_no_place_that_asks(self):
        """INVERTED by DRE-3664: this pinned `callable(plan_run.note)`."""
        self.assertFalse(hasattr(plan_run, "note"),
                         "plan_run.note is back — an ask nothing should make")

    def test_the_sweeps_turn_does_not_ask_at_all(self):
        """INVERTED by DRE-3664: this pinned `plan_run.note` inside
        `reconcile._plan_run_note`. The turn is a lane move and a note; a
        `plan_run` reference in it is the second dispatcher coming back."""
        src = open(os.path.join(SCRIPTS, "reconcile.py"), encoding="utf-8").read()
        self.assertNotIn("def _plan_run_note", src)
        self.assertNotIn("plan_run",
                         self.source_of("reconcile.py", "advance_unblocked_epics"))

    def test_the_waves_own_turn_does_not_ask_at_all(self):
        """The lane move is the dispatch. A second ask here is the DRE-3659
        duplicate — a hosted runner started to find out it had nothing to do."""
        self.assertNotIn("plan_run",
                         self.source_of("wave_commitment.py", "advance"))


class OneDispatchPerPlanningEntryTest(unittest.TestCase):
    """One planner dispatch per Planning entry, counted across BOTH dispatchers
    (DRE-3659).

    The relay dispatches `agent-plan` for every card entering Planning — it has
    since DRE-1913 (2026-06-29), label or no label since DRE-3030 — and the
    wave's commit dispatched the same event for the same entry. When the
    DRE-3530 wave was approved on 2026-09-11 at 18:50 PT the commit started
    three epics and SIX Agent Plan runs fired within eight seconds. The three
    the commit sent won by two to six seconds; the three the relay sent queued,
    each started a hosted runner twenty-odd minutes later, and skipped as
    duplicates. `dedupe_dispatch.py plan-gate` decided that correctly, inside
    a job that had already billed a runner. It stays as the backstop; these
    tests pin that it stops being the common path.

    The relay lives in agent-bureau, so it is modelled here at the seam it
    listens on: the state webhook for a card entering the lane that owes a
    plan artifact. Every `cmd_advance` INTO that lane is one relay dispatch,
    and every `plan_run.fire` is one of the commit's own. The sum is the
    number, and the number is the count of Planning entries.
    """

    TWO_READY = """# Wave

```epics
[
  {"key": "quiet", "title": "The relay goes quiet", "depends_on": []},
  {"key": "fence", "title": "The fence", "depends_on": []},
  {"key": "sweep", "title": "The sweep", "depends_on": ["quiet", "fence"]}
]
```
"""

    def dispatches_for(self, plan_md: str):
        """Commit an approved wave planned as `plan_md`; return every
        `agent-plan` dispatch either dispatcher made, as (who, epic)."""
        from unittest.mock import patch
        from test_wave_commitment import APPROVED, WAVE, _FakeOps

        ops = _FakeOps(
            description=wc.render_ledger(wc.ledger_from_plan(WAVE, plan_md)),
            green_lit_at=APPROVED)
        fired: list = []
        moved = ops.cmd_advance

        def relay_listens(identifier, to_state, from_states):
            moved(identifier, to_state, from_states)
            if to_state == wc.turn_lane():
                fired.append(("relay", identifier))

        def commit_asks(card, repo, **kwargs):
            fired.append(("commit", card["identifier"]))
            return True, ""

        ops.cmd_advance = relay_listens
        with patch.object(plan_run, "fire", commit_asks):
            wc.commit(ops, WAVE)
        return fired, ops

    def test_one_planning_entry_is_one_dispatch_across_relay_and_commit(self):
        from test_wave_commitment import PLAN

        fired, _ = self.dispatches_for(PLAN)
        self.assertEqual(
            len(fired), 1,
            f"one epic entered {wc.turn_lane()!r} and {len(fired)} planner "
            f"dispatches were made: {fired} — the duplicate is decided before "
            "the dispatch, not inside a job that already started a runner")

    def test_two_ready_epics_are_two_dispatches_not_four(self):
        from collections import Counter

        fired, _ = self.dispatches_for(self.TWO_READY)
        per_epic = Counter(epic for _, epic in fired)
        self.assertEqual(len(fired), 2, f"dispatches: {fired}")
        self.assertEqual(set(per_epic.values()), {1},
                         f"an epic was dispatched more than once: {fired}")

    def test_the_arrival_note_claims_no_run_the_commit_did_not_start(self):
        """The commit no longer starts the run, so it must not say it did —
        a receipt nobody can check is the DRE-1254 false-receipt class."""
        from test_wave_commitment import PLAN

        _, ops = self.dispatches_for(PLAN)
        moved = [ident for ident, lane in ops.advanced_to if lane == wc.turn_lane()]
        said = "\n".join(ops.comments_on(moved[0]))
        self.assertNotIn("has been started", said)
        self.assertIn(wc.turn_lane(), said)


class OneDispatchPerTurnTest(unittest.TestCase):
    """One planner dispatch per Planning entry, counted across BOTH
    dispatchers, for the SWEEP's turn (DRE-3664) — the twin DRE-3659 named
    and left to its own card.

    Every epic after a wave's first reaches its turn here: its predecessor
    reached Done, `reconcile.advance_unblocked_epics` moves it Backlog → the
    lane that owes a plan artifact, and the relay dispatches `agent-plan` on
    that entry (DRE-1913 / DRE-3030). The sweep then ALSO asked, through
    `plan_run.fire`, so every such epic got two Agent Plan runs and the second
    learned it was the duplicate inside a job that had already started a
    hosted runner. `dedupe_dispatch.py plan-gate` decided that correctly and
    stays as the backstop; these tests pin that it stops being the common
    path.

    As in `OneDispatchPerPlanningEntryTest`, the relay is modelled at the seam
    it listens on — every `cmd_advance` INTO the lane is one relay dispatch —
    and every `plan_run.fire` is one of the sweep's own. The sum is the
    number, and the number is the count of Planning entries.
    """

    def dispatches_for(self, *dependents: str):
        """The sweep's turn for `dependents`; every `agent-plan` dispatch
        either dispatcher made, as (who, epic)."""
        from unittest.mock import patch

        fired: list = []

        def relay_listens(identifier, to_state, from_states):
            if to_state == wc.turn_lane():
                fired.append(("relay", identifier))

        def sweep_asks(card, repo, **kwargs):
            fired.append(("sweep", card["identifier"]))
            return True, ""

        with patch.object(plan_run, "fire", sweep_asks):
            advance, comment, _ = sweep_turn(*dependents)
        for call in advance.mock_calls:
            relay_listens(*call.args)
        return fired, comment

    def test_one_turn_is_one_dispatch_across_relay_and_sweep(self):
        fired, _ = self.dispatches_for("DRE-2901")
        self.assertEqual(
            len(fired), 1,
            f"one epic entered {wc.turn_lane()!r} and {len(fired)} planner "
            f"dispatches were made: {fired} — the duplicate is decided before "
            "the dispatch, not inside a job that already started a runner")

    def test_two_epics_reaching_their_turn_are_two_dispatches_not_four(self):
        from collections import Counter

        fired, _ = self.dispatches_for("DRE-2901", "DRE-2902")
        per_epic = Counter(epic for _, epic in fired)
        self.assertEqual(len(fired), 2, f"dispatches: {fired}")
        self.assertEqual(set(per_epic.values()), {1},
                         f"an epic was dispatched more than once: {fired}")

    def test_the_arrival_note_claims_no_run_the_sweep_did_not_start(self):
        """The sweep no longer starts the run, so it must not say it did — a
        receipt nobody can check is the DRE-1254 false-receipt class. It says
        the same sentence the wave's own turn says, from the same function:
        one wording for one fact."""
        _, comment = self.dispatches_for("DRE-2901")
        said = comment.mock_calls[0].args[1]
        self.assertNotIn("has been started", said)
        self.assertTrue(said.endswith(wc.lane_starts_the_run()),
                        "the sweep's note does not end on the sentence the "
                        "wave's own turn ends on")


class NoSecondVocabularyTest(unittest.TestCase):
    def test_the_lanes_are_derived_not_typed(self):
        src = open(os.path.join(SCRIPTS, "wave_commitment.py"), encoding="utf-8").read()
        body = src.split('"""', 2)[2]  # past the module docstring
        for lane in ("Green Light", "Planning"):
            for literal in (f'"{lane}"', f"'{lane}'"):
                self.assertNotIn(
                    literal, body,
                    f"{lane} is written into wave_commitment.py — the lane "
                    "comes from config/lane-contract.json and the shape "
                    "vocabulary, or it drifts from them",
                )

    def test_the_turn_lane_is_a_live_lane(self):
        self.assertIn(wc.turn_lane(), lane_contract.lane_names(status="live"))
        self.assertIn(wc.decision_lane(), lane_contract.lane_names(status="live"))

    def test_the_sweep_may_write_the_lane_it_sends_the_epic_to(self):
        """A destination its writer may not touch is the DRE-2824 dead end."""
        self.assertIn("reconcile.py", lane_contract.lane_writers(wc.turn_lane()))


class TheStandardSaysItTest(unittest.TestCase):
    def setUp(self):
        self.text = open(STANDARD, encoding="utf-8").read()

    def test_it_names_the_recorded_state(self):
        self.assertIn(wc.COMMITTED, self.text)

    def test_it_says_the_approval_is_the_shape_and_the_order(self):
        self.assertTrue(
            re.search(r"shape and .{0,10}order", self.text, re.I),
            "the standard must say what approving a wave actually approves",
        )

    def test_it_says_a_reorder_or_a_drop_needs_no_re_approval(self):
        self.assertTrue(
            re.search(r"(reorder|drop)", self.text, re.I),
            "the standard must say the sequence can change inside an "
            "approved wave",
        )
        self.assertIn("wave_commitment.py", self.text)

    def test_it_adds_no_new_numbered_requirement(self):
        """`wave_plan.py` parses `### N.` out of this file as the sections a
        plan must carry. A sixth-and-a-half requirement added here would
        silently fail every wave plan already written."""
        import wave_plan

        self.assertEqual([r.number for r in wave_plan.requirements()],
                         [1, 2, 3, 4, 5, 6])

    def test_every_relative_link_still_resolves(self):
        for target in re.findall(r"\]\(\s*([^)\s#]+)", self.text):
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            path = os.path.normpath(
                os.path.join(os.path.dirname(STANDARD), target.split("#", 1)[0]))
            self.assertTrue(os.path.exists(path), f"dead link: {target}")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(not unittest.main(exit=False).result.wasSuccessful())
