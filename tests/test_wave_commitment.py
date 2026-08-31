"""RED-first tests: progressive commitment — an epic inside an approved wave
gets its own green light when its turn comes (DRE-2846).

THE GAP: approving a wave should approve **the shape and the order, nothing
more**. Today nothing can tell *approved as part of a wave* from *approved to
build*, so there is no field to read and a wave approval is a blank cheque over
every epic under it. Wave 1.5 is the live argument: approved 2026-08-23 as a
shape, and by 2026-08-29 two of its cards had been rewritten, one split into
four, and its phase count had gone from seven to nine. A single approval
covering all of that would have been an approval of something nobody had read.

WHAT THIS PINS, one section per acceptance criterion:

  1. THE STATE — `committed-in-sequence` is recorded on the epic, machine
     readable, and distinguishable from an epic approved to build. New state,
     not a flag: the two words are different answers and the module says so.
  2. APPROVING THE WAVE GREEN-LIGHTS NOTHING — committing a wave lands its
     epics as committed-in-sequence and moves none of them to the lane where a
     green light is given, nor to an active lane.
  3. THE TURN CARRIES A FRESH ARTIFACT — an epic reaching its turn goes to the
     lane no epic leaves without a plan artifact, DERIVED from the lane
     contract, never to the decision lane with the artifact the wave was
     approved on.
  4. THE SWEEP REFUSES — `reconcile.promote_ready` does not promote an epic
     that is committed-in-sequence and has not had its own green light, and it
     says why. The refusal reads the RECORD, so it holds where the
     `agent:planner` label does not.
  5. REORDER AND DROP — both are possible without re-approving the wave, both
     are refused when they would break the dependency order the CEO approved,
     and both are written into the managed region of the wave's own
     description, which is where the CEO reads the plan.

Run: cd bureau-pipeline && python3 -m pytest tests/test_wave_commitment.py -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import lane_contract  # noqa: E402
import linear_ops  # noqa: E402
import wave_commitment as wc  # noqa: E402

WAVE = "DRE-2719"
APPROVED = "2026-08-23T09:00:00.000Z"
LATER = "2026-08-29T09:00:00.000Z"

# A wave plan the checker would accept on the one thing this module reads out
# of it: the epics it commits to, in dependency order.
PLAN = """# Wave 1.5

```epics
[
  {"key": "standard", "title": "The standard moves where agents read it",
   "depends_on": []},
  {"key": "route", "title": "The wave route and its checker",
   "depends_on": ["standard"]},
  {"key": "commitment", "title": "Progressive commitment",
   "depends_on": ["route"]}
]
```
"""


def ledger_of(md: str = PLAN) -> dict:
    return wc.ledger_from_plan(WAVE, md)


# ===========================================================================
# 1: the state — recorded, machine-readable, and not the same as approved
# ===========================================================================
class TestTheRecordedState:
    def test_the_two_states_are_different_words(self):
        """New state, not a flag to read: 'approved as part of a wave' and
        'approved to build' must not collapse into one answer."""
        assert wc.COMMITTED != wc.GREEN_LIT
        assert wc.COMMITTED == "committed-in-sequence"

    def test_a_card_with_no_record_has_no_wave_state(self):
        assert wc.state([]) is None
        assert wc.state(["a chatty comment about waves"]) is None

    def test_the_stamp_records_committed_in_sequence(self):
        body = wc.commitment_comment(WAVE, ledger_of()["epics"][1], position=2, total=3)
        assert wc.state([body]) == wc.COMMITTED

    def test_an_own_green_light_is_the_other_state(self):
        """Observed, never stamped: the epic entered an active lane on its own
        account, and that is what 'approved to build' means."""
        body = wc.commitment_comment(WAVE, ledger_of()["epics"][0], position=1, total=3)
        assert wc.state([body], green_lit_at=LATER) == wc.GREEN_LIT

    def test_the_marker_must_open_the_comment(self):
        """The sweep's own refusal QUOTES the state. A reader that matched the
        marker anywhere in a body would read the refusal back as the record."""
        body = wc.commitment_comment(WAVE, ledger_of()["epics"][0], position=1, total=3)
        quoted = "The sweep says:\n\n" + body
        assert wc.state([quoted]) is None

    def test_the_record_names_the_wave_it_belongs_to(self):
        body = wc.commitment_comment(WAVE, ledger_of()["epics"][0], position=1, total=3)
        assert WAVE in body
        assert wc.wave_on([body]) == WAVE

    def test_a_dropped_epic_is_its_own_state(self):
        entry = ledger_of()["epics"][2]
        body = wc.drop_comment(WAVE, entry, "the console shipped it another way")
        assert wc.state([body]) == wc.DROPPED

    def test_the_latest_stamp_wins(self):
        entry = ledger_of()["epics"][2]
        committed = wc.commitment_comment(WAVE, entry, position=3, total=3)
        dropped = wc.drop_comment(WAVE, entry, "no longer worth building")
        assert wc.state([committed, dropped]) == wc.DROPPED


# ===========================================================================
# 2: approving the wave green-lights nothing
# ===========================================================================
class TestApprovingTheWaveGreenLightsNothing:
    def test_the_ledger_records_every_epic_as_committed(self):
        ledger = ledger_of()
        assert [e["key"] for e in ledger["epics"]] == ["standard", "route", "commitment"]
        assert {e["status"] for e in ledger["epics"]} == {wc.COMMITTED}

    def test_committing_a_wave_creates_the_epics_and_stamps_them(self):
        ops = _FakeOps(description=wc.render_ledger(ledger_of()), green_lit_at=APPROVED)
        wc.commit(ops, WAVE)
        assert len(ops.created) == 3
        for issue in ops.created:
            stamps = [b for b in ops.comments_on(issue["identifier"])
                      if wc.state([b]) == wc.COMMITTED]
            assert stamps, f"{issue['identifier']} carries no commitment record"

    def test_committing_a_wave_moves_no_epic_to_the_decision_lane(self):
        """The whole point: the wave's approval is not the epic's green light,
        so nothing arrives in the CEO's queue carrying the wave-time plan."""
        ops = _FakeOps(description=wc.render_ledger(ledger_of()), green_lit_at=APPROVED)
        wc.commit(ops, WAVE)
        lanes = [lane for _, lane in ops.states + ops.advanced_to]
        assert wc.decision_lane() not in lanes
        for active in ("Todo", "In Progress"):
            assert active not in lanes

    def test_an_unapproved_wave_commits_nothing(self):
        """No green light on the wave itself, no commitment: there is no
        approved shape to commit to yet."""
        ops = _FakeOps(description=wc.render_ledger(ledger_of()), green_lit_at=None)
        with pytest.raises(wc.CommitmentRefused):
            wc.commit(ops, WAVE)
        assert ops.created == []

    def test_only_the_epic_whose_turn_it_is_moves(self):
        """`standard` depends on nothing, so its turn is now; the other two
        wait for it. Approving the wave started ONE epic, not three."""
        ops = _FakeOps(description=wc.render_ledger(ledger_of()), green_lit_at=APPROVED)
        wc.commit(ops, WAVE)
        moved = [ident for ident, _ in ops.advanced_to]
        assert len(moved) == 1
        assert ops.title_of(moved[0]).startswith("The standard moves")

    def test_a_run_that_died_mid_commit_adopts_rather_than_duplicates(self):
        """Q5 of the vendor-boundary premortem: our own crash. A run that
        created a card and died before recording it must not create it again on
        the retry — a wave whose sequence names one epic under two card numbers
        is worse than no sequence at all."""
        ops = _FakeOps(description=wc.render_ledger(ledger_of()), green_lit_at=APPROVED)
        orphan = ops.cmd_subissue(
            WAVE, "The standard moves where agents read it", "body")
        wc.commit(ops, WAVE)
        assert len(ops.created) == 3, "the orphan was created a second time"
        assert wc.entry(wc.parse_ledger(ops.description), "standard")["card"] == \
            orphan["identifier"]

    def test_the_committed_epics_carry_the_label_that_makes_them_epics(self):
        """Without it the relay dispatches a BUILD agent at a container — the
        blank cheque, one label over. The label comes from the shape
        vocabulary's own marks, never typed here."""
        import planning_shape

        ops = _FakeOps(description=wc.render_ledger(ledger_of()), green_lit_at=APPROVED)
        wc.commit(ops, WAVE)
        mark = planning_shape.marks("epic")[0]
        for issue in ops.created:
            assert mark in ops.labels[issue["identifier"]]
            assert not [
                l for l in ops.labels[issue["identifier"]]
                if l.startswith("agent:") and l != mark
            ], "a build role survived on an epic"

    def test_the_turn_comes_when_the_predecessor_is_done(self):
        ledger = ledger_of()
        assert wc.turn(ledger, settled=()) == ("standard",)
        assert wc.turn(ledger, settled=("standard",)) == ("route",)
        assert wc.turn(ledger, settled=("standard", "route")) == ("commitment",)

    def test_a_dropped_epic_never_takes_a_turn(self):
        ledger = wc.drop(ledger_of(), "commitment", "the console shipped it another way")
        assert "commitment" not in wc.turn(ledger, settled=("standard", "route"))


# ===========================================================================
# 3: the turn carries the epic's OWN plan artifact, written at that moment
# ===========================================================================
class TestTheTurnCarriesAFreshArtifact:
    def test_the_turn_lane_is_the_lane_that_owes_a_plan_artifact(self):
        """DERIVED from config/lane-contract.json — the live lane whose EXIT an
        epic cannot make without a plan artifact. A lane name typed here would
        drift from the clause that makes the artifact compulsory."""
        lane = lane_contract.lane(wc.turn_lane())
        assert "plan artifact" in lane["clauses"]["exit"]["text"]

    def test_the_turn_lane_is_not_the_decision_lane(self):
        """Arriving straight in the CEO's queue would carry the artifact the
        wave was approved on, which is the one thing this card forbids."""
        assert wc.turn_lane() != wc.decision_lane()

    def test_the_decision_lane_is_the_epic_shapes_own_destination(self):
        import planning_shape

        assert wc.decision_lane() == planning_shape.destination("epic")

    def test_the_arrival_says_the_artifact_is_written_now(self):
        arrival = wc.turn_arrival(
            "DRE-2900",
            [wc.commitment_comment(WAVE, ledger_of()["epics"][1], position=2, total=3)],
        )
        assert arrival is not None
        assert arrival.lane == wc.turn_lane()
        assert "plan artifact" in arrival.note.lower()

    def test_a_card_outside_a_wave_has_no_turn_arrival(self):
        assert wc.turn_arrival("DRE-2900", []) is None

    def test_an_epic_already_green_lit_is_not_sent_round_again(self):
        body = wc.commitment_comment(WAVE, ledger_of()["epics"][0], position=1, total=3)
        assert wc.turn_arrival("DRE-2900", [body], green_lit_at=LATER) is None


# ===========================================================================
# 4: the sweep does not promote a committed-in-sequence epic
# ===========================================================================
class TestTheSweepRefuses:
    def test_a_committed_epic_is_refused(self):
        body = wc.commitment_comment(WAVE, ledger_of()["epics"][1], position=2, total=3)
        refusal = wc.promotion_refusal("DRE-2901", [body])
        assert refusal is not None
        assert wc.COMMITTED in refusal
        assert wc.refusal_tag(refusal) == wc.NOT_GREEN_LIT_TAG

    def test_its_own_green_light_clears_the_refusal(self):
        body = wc.commitment_comment(WAVE, ledger_of()["epics"][1], position=2, total=3)
        assert wc.promotion_refusal("DRE-2901", [body], green_lit_at=LATER) is None

    def test_a_card_with_no_commitment_is_untouched(self):
        assert wc.promotion_refusal("DRE-2901", ["ordinary chatter"]) is None

    def test_the_sweep_refuses_the_epic_even_without_the_planner_label(self):
        """The record is the state. `agent:planner` is a LABEL — a human can
        remove it in Linear, and the wave's approval must still not become an
        approval to build. Revert the guard and this card promotes."""
        import reconcile

        board = _PromotionBoard(comments=[
            wc.commitment_comment(WAVE, ledger_of()["epics"][1], position=2, total=3)
        ])
        assert board.promote() == 0
        assert board.visited("DRE-2901") == ["Backlog"]
        assert board.surfaced_once(wc.NOT_GREEN_LIT_TAG)
        assert reconcile.wave_commitment is wc

    def test_the_sweep_promotes_it_once_it_has_its_own_green_light(self):
        board = _PromotionBoard(
            comments=[wc.commitment_comment(
                WAVE, ledger_of()["epics"][1], position=2, total=3)],
            green_lit_at=LATER,
        )
        assert board.promote() == 1

    def test_an_ordinary_card_still_promotes(self):
        assert _PromotionBoard(comments=[]).promote() == 1


# ===========================================================================
# 5: reorder and drop, without re-approving the wave
# ===========================================================================
class TestReorderAndDrop:
    def test_reordering_moves_the_epic_and_what_it_waits_for(self):
        ledger = wc.reorder(ledger_of(), "commitment", after="standard",
                            because="the console needs it before the route")
        keys = [e["key"] for e in ledger["epics"]]
        assert keys == ["standard", "commitment", "route"]
        assert wc.entry(ledger, "commitment")["depends_on"] == ["standard"]

    def test_reordering_to_the_front_clears_what_it_waits_for(self):
        ledger = wc.reorder(ledger_of(), "route", first=True, because="it is ready now")
        assert [e["key"] for e in ledger["epics"]][0] == "route"
        assert wc.entry(ledger, "route")["depends_on"] == []

    def test_a_reorder_that_breaks_the_order_is_refused(self):
        """`standard` cannot be moved after `route`, which depends on it — the
        wave's dependency order is what the CEO approved."""
        with pytest.raises(wc.CommitmentRefused):
            wc.reorder(ledger_of(), "standard", after="route", because="why not")

    def test_reordering_records_the_change_with_its_reason(self):
        ledger = wc.reorder(ledger_of(), "commitment", after="standard",
                            because="the console needs it before the route")
        assert len(ledger["changes"]) == 1
        assert "the console needs it" in ledger["changes"][0]["because"]

    def test_a_change_without_a_reason_is_refused(self):
        with pytest.raises(wc.CommitmentRefused):
            wc.reorder(ledger_of(), "route", first=True, because="  ")
        with pytest.raises(wc.CommitmentRefused):
            wc.drop(ledger_of(), "commitment", because="")

    def test_dropping_marks_the_epic_and_records_why(self):
        ledger = wc.drop(ledger_of(), "commitment", "the console shipped it another way")
        assert wc.entry(ledger, "commitment")["status"] == wc.DROPPED
        assert "shipped it another way" in ledger["changes"][0]["because"]

    def test_dropping_something_another_epic_waits_for_is_refused(self):
        with pytest.raises(wc.CommitmentRefused):
            wc.drop(ledger_of(), "standard", "we changed our minds")

    def test_an_unknown_key_is_refused_by_name(self):
        with pytest.raises(wc.CommitmentRefused) as e:
            wc.drop(ledger_of(), "nonesuch", "typo")
        assert "nonesuch" in str(e.value)

    def test_the_change_is_written_where_the_ceo_reads_the_plan(self):
        """The managed region of the WAVE's own description — the same place
        mid_epic.py records an epic's growth, and the place a CEO opens."""
        ops = _FakeOps(description="Wave 1.5.\n\n" + wc.render_ledger(ledger_of()),
                       green_lit_at=APPROVED)
        wc.apply_change(ops, WAVE, "drop", "commitment",
                        because="the console shipped it another way")
        assert ops.descriptions, "the wave's description was never rewritten"
        _, body = ops.descriptions[-1]
        assert "Wave 1.5." in body, "the CEO-readable plan above the region was lost"
        assert "the console shipped it another way" in body
        assert wc.entry(wc.parse_ledger(body), "commitment")["status"] == wc.DROPPED

    def test_a_change_does_not_re_approve_the_wave(self):
        """Reordering or dropping is possible WITHOUT re-approving the whole
        wave: the wave's own lane is never touched."""
        ops = _FakeOps(description=wc.render_ledger(ledger_of()), green_lit_at=APPROVED)
        wc.apply_change(ops, WAVE, "reorder", "route", first=True,
                        because="it is ready now")
        assert [s for s in ops.states if s[0] == WAVE] == []

    def test_the_change_is_announced_on_the_wave(self):
        ops = _FakeOps(description=wc.render_ledger(ledger_of()), green_lit_at=APPROVED)
        wc.apply_change(ops, WAVE, "drop", "commitment", because="shipped another way")
        assert any("shipped another way" in b for b in ops.comments_on(WAVE))


# ===========================================================================
# The ledger round-trips, and it does not eat the plan around it
# ===========================================================================
class TestTheLedger:
    def test_it_round_trips(self):
        ledger = wc.drop(ledger_of(), "commitment", "shipped another way")
        parsed = wc.parse_ledger(wc.render_ledger(ledger))
        assert parsed["epics"] == ledger["epics"]
        assert parsed["changes"] == ledger["changes"]

    def test_a_description_with_no_region_parses_to_nothing(self):
        assert wc.parse_ledger("just the plan, no region") is None

    def test_merging_replaces_the_region_and_keeps_the_prose(self):
        first = wc.merge_ledger("Wave 1.5 is about X.", wc.render_ledger(ledger_of()))
        second = wc.merge_ledger(
            first, wc.render_ledger(wc.drop(ledger_of(), "commitment", "shipped")))
        assert second.count(wc.REGION_BEGIN) == 1
        assert "Wave 1.5 is about X." in second

    def test_the_order_rule_is_the_wave_plans_own(self):
        """One rule, one implementation: the ledger is judged by the same
        function that judges the ```epics block in the plan itself."""
        import wave_plan

        bad = ledger_of()
        bad["epics"] = list(reversed(bad["epics"]))
        assert wc.order_defects(bad["epics"])
        assert any("dependency order" in d for d in wc.order_defects(bad["epics"]))
        assert wave_plan.epic_defects  # the rule this reuses

    def test_recording_does_not_replace_a_sequence_already_committed(self):
        """A later re-plan must not silently swap the epics the CEO approved."""
        ops = _FakeOps(green_lit_at=APPROVED)
        wc.record(ops, WAVE, PLAN)
        wc.commit(ops, WAVE)
        other = PLAN.replace('"key": "route"', '"key": "somethingelse"').replace(
            '"depends_on": ["route"]', '"depends_on": ["somethingelse"]')
        wc.record(ops, WAVE, other)
        keys = [e["key"] for e in wc.parse_ledger(ops.description)["epics"]]
        assert keys == ["standard", "route", "commitment"]


# ===========================================================================
# Test doubles
# ===========================================================================
class _FakeOps:
    """A stand-in for the `linear_ops` MODULE — the way every Linear-touching
    seam in this codebase takes its I/O (mid_epic, break_glass,
    validate_card._bounce). Only the verbs wave_commitment uses."""

    def __init__(self, description="", green_lit_at=APPROVED, state="Todo"):
        self.description = description
        self.green_lit_at = green_lit_at
        self.state = state
        self.created: list[dict] = []
        self.comments: list[tuple[str, str]] = []
        self.states: list[tuple[str, str]] = []
        self.advanced_to: list[tuple[str, str]] = []
        self.descriptions: list[tuple[str, str]] = []
        self.labels: dict[str, list[str]] = {}
        self.removed: list[tuple[str, str]] = []
        self._next = 2900
        self.LinearError = linear_ops.LinearError

    # --- the verbs --------------------------------------------------------
    def parent_inherited_labels(self, labels):
        return linear_ops.parent_inherited_labels(labels)

    def gql(self, query, variables=None):
        history = ([{"createdAt": self.green_lit_at, "toState": {"name": "Todo"}}]
                   if self.green_lit_at else [])
        return {"issue": {
            "id": f"uuid-{WAVE}", "identifier": WAVE, "title": "Wave 1.5",
            "description": self.description,
            "state": {"name": self.state},
            "labels": {"nodes": [{"name": "repo:bureau-pipeline"},
                                 {"name": "initiative:intake"},
                                 {"name": "agent:planner"}]},
            "children": {"nodes": [
                {"identifier": c["identifier"], "title": c["title"],
                 "state": {"name": c["state"]}} for c in self.created]},
            "history": {"nodes": history},
        }}

    def cmd_subissue(self, parent, title, body, *flags):
        ident = f"DRE-{self._next}"
        self._next += 1
        issue = {"id": f"uuid-{ident}", "identifier": ident, "title": title,
                 "state": "Backlog", "url": f"https://linear.app/x/{ident}"}
        self.created.append(issue)
        # The REAL label rule, not a stand-in: `linear_ops.child_labels_from`
        # inherits a BUILD role from the parent (agent:engineer / agent:devops)
        # and explicitly does NOT inherit agent:planner — "children are work,
        # not epics". An epic created under a wave has to survive that.
        self.labels[ident] = linear_ops.child_labels_from(
            [n["name"] for n in self.gql("")["issue"]["labels"]["nodes"]],
            [flags[i + 1] for i, f in enumerate(flags) if f == "--label"],
        )
        return issue

    def cmd_comment(self, identifier, body):
        self.comments.append((identifier, body))

    def cmd_state(self, identifier, state, *flags):
        self.states.append((identifier, state))

    def cmd_advance(self, identifier, to_state, from_states):
        self.advanced_to.append((identifier, to_state))
        for c in self.created:
            if c["identifier"] == identifier:
                c["state"] = to_state

    def set_description(self, identifier, body):
        self.descriptions.append((identifier, body))
        self.description = body

    def add_label(self, identifier, name):
        self.labels.setdefault(identifier, []).append(name)

    def remove_label(self, identifier, name):
        self.removed.append((identifier, name))
        self.labels.setdefault(identifier, [])
        if name in self.labels[identifier]:
            self.labels[identifier].remove(name)

    def count_comments(self, identifier, needle, **kw):
        return sum(1 for i, b in self.comments if i == identifier and needle in b)

    def comment_bodies(self, identifier):
        return self.comments_on(identifier)

    # --- assertion helpers -----------------------------------------------
    def comments_on(self, identifier):
        return [b for i, b in self.comments if i == identifier]

    def title_of(self, identifier):
        return next(c["title"] for c in self.created if c["identifier"] == identifier)


class _PromotionBoard:
    """`reconcile.promote_ready` over ONE Backlog card with every other gate
    (WIP, epic blockers, card blockers, verdict) open — so the only thing that
    can hold it is the wave-commitment rule. The card carries NO
    `agent:planner` label on purpose: that label is what stops the sweep
    today, and this rule has to hold on the RECORD when it does not."""

    def __init__(self, comments=(), green_lit_at=None):
        self.green_lit_at = green_lit_at
        self.card = {
            "id": "uuid-2901",
            "identifier": "DRE-2901",
            "title": "The wave route and its checker",
            "description": "## What\n- the wave route",
            "createdAt": APPROVED,
            "parent": None,
            "labels": {"nodes": [{"name": "repo:bureau-pipeline"},
                                 {"name": "agent:devops"}]},
            "comments": {"nodes": [{"body": b} for b in comments]},
            "inverseRelations": {"nodes": []},
        }
        self.advanced: list[tuple[str, str, str]] = []
        self.posted: list[tuple[str, str]] = []
        self.lanes = {"DRE-2901": ["Backlog"]}

    def promote(self) -> int:
        from unittest.mock import patch

        import reconcile
        import routing_verdict

        # A parentless card owes a FLEET verdict (DRE-2735) — supplied so the
        # only gate under test is this one.
        self.card["comments"]["nodes"].insert(
            0, {"body": routing_verdict.verdict_comment("FLEET", "one PR")})

        def advance(ident, to_state, from_states):
            self.advanced.append((ident, to_state, from_states))
            self.lanes.setdefault(ident, []).append(to_state)

        with patch.object(reconcile, "REPO_SLUG", "bureau-pipeline"), patch.object(
            reconcile, "backlog_children", return_value=[self.card]
        ), patch.object(
            reconcile, "epic_blockers_unmet", return_value=False
        ), patch.object(
            reconcile.mid_epic, "last_green_light", return_value=self.green_lit_at
        ), patch.object(
            reconcile.linear_ops, "cmd_advance", side_effect=advance
        ), patch.object(
            reconcile.linear_ops, "cmd_comment",
            side_effect=lambda i, b: self.posted.append((i, b)),
        ), patch.object(
            reconcile.linear_ops, "count_comments",
            side_effect=lambda i, needle, **kw: sum(
                1 for pi, pb in self.posted if pi == i and needle in pb
            ),
        ):
            return reconcile.promote_ready(active_count=0)

    def visited(self, identifier):
        return self.lanes.get(identifier, [])

    def surfaced_once(self, tag):
        return len([b for i, b in self.posted if tag in b]) == 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
