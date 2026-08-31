"""RED-first tests: every epic carries a proof card and a demo card (DRE-2746).

The convention was already written down — `standards/plan-artifact.md` gives
the artifact a "Proof and demo" section — and nothing made any planner follow
it. A convention nothing checks is a convention that drifts, so the check runs
on the PLANNER'S OUTPUT (the cards it actually created) rather than on the
brief's text.

The two things are not the same:

  * **Proof** answers *did it work* — and it is not a green test suite. It is
    the mechanism observed running against real state.
  * **Demo** answers *can the CEO see it*. A merged PR and a passing suite are
    invisible to the person who green-lit the epic.

WHAT THIS PINS, one section per acceptance criterion:

  1. A planner's output is read for a `PROOF:` card and a `DEMO:` card, and
     they must be the LAST two children.
  2. Both are blocked by every other child — read off the Linear `blocks`
     relations, never off the order the cards happen to sit in.
  3. Neither may carry a FLEET verdict. The verdicts that MAY confirm an epic
     are derived from `config/routing-verdicts.json` — the ones whose actor is
     a human — so "something other than the builder confirms it" is read from
     the file rather than restated here.
  4. An epic missing either card is refused with the reason NAMED, in a comment
     a planner can act on.

Run: cd bureau-pipeline && python3 -m pytest tests/test_proof_and_demo.py -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import linear_ops  # noqa: E402
import planning_route  # noqa: E402
import proof_and_demo  # noqa: E402
import routing_verdict  # noqa: E402

VERDICTS = ROOT / "config" / "routing-verdicts.json"

EPIC = "DRE-2746"

# --- one realistic planner output -------------------------------------------
#
# Three work cards, then the two that close the epic. Written the way a real
# planner writes them: checkbox criteria, and the proof card's criteria naming
# a live observation rather than a passing suite.

WORK_BODY = (
    "Add the gate.\n\n"
    "## Acceptance criteria\n\n"
    "- [ ] the gate refuses an epic with no proof card\n"
)

PROOF_BODY = (
    "Record what was observed, where it was read, and when.\n\n"
    "## Acceptance criteria\n\n"
    "- [ ] the gate is observed refusing a real epic in production\n"
    "- [ ] the observation is written to docs/proof/DRE-2746.md and merged\n"
)

DEMO_BODY = (
    "Show the CEO the epic's outcome.\n\n"
    "## Acceptance criteria\n\n"
    "- [ ] the CEO is walked through the bounce on a real epic\n"
)

LABELS = ("repo:bureau-pipeline", "agent:engineer", "initiative:pipeline")


def _card(identifier, title, body=WORK_BODY, labels=LABELS, blocked_by=()):
    return {
        "identifier": identifier,
        "title": title,
        "body": body,
        "labels": list(labels),
        "blocked_by": list(blocked_by),
    }


def _plan(*, work=3, proof=True, demo=True, blocked=True, order=None):
    """One epic's children, in the order the planner created them."""
    work_ids = [f"DRE-90{n:02d}" for n in range(1, work + 1)]
    children = [_card(i, f"Build piece {n}") for n, i in enumerate(work_ids, 1)]
    tail = []
    if proof:
        tail.append(_card("DRE-9091", "PROOF: the gate refused a real epic",
                          body=PROOF_BODY,
                          blocked_by=work_ids if blocked else []))
    if demo:
        tail.append(_card("DRE-9092", "DEMO: the bounce, end to end",
                          body=DEMO_BODY,
                          blocked_by=work_ids if blocked else []))
    children += tail
    if order is not None:
        by_id = {c["identifier"]: c for c in children}
        children = [by_id[i] for i in order]
    return children


# ===========================================================================
# 1: the two cards, and where they sit
# ===========================================================================
class TestTheTwoCards:
    def test_a_well_formed_plan_passes(self):
        assert proof_and_demo.findings(_plan()) == []

    def test_an_epic_with_no_proof_card_is_refused_and_the_reason_is_named(self):
        found = proof_and_demo.findings(_plan(proof=False))
        assert found, "an epic with no proof card must be refused"
        assert any("proof" in f.lower() for f in found)
        # The reason is named, not merely signalled.
        assert any("PROOF:" in f for f in found)

    def test_an_epic_with_no_demo_card_is_refused_and_the_reason_is_named(self):
        found = proof_and_demo.findings(_plan(demo=False))
        assert found
        assert any("demo" in f.lower() for f in found)
        assert any("DEMO:" in f for f in found)

    def test_an_epic_with_neither_names_both(self):
        found = proof_and_demo.findings(_plan(proof=False, demo=False))
        assert any("proof" in f.lower() for f in found)
        assert any("demo" in f.lower() for f in found)

    def test_they_must_be_the_last_two_children(self):
        """Position is part of the convention: a proof card that is not last
        can be started before the work it proves exists."""
        plan = _plan(order=["DRE-9001", "DRE-9091", "DRE-9092", "DRE-9002",
                            "DRE-9003"])
        found = proof_and_demo.findings(plan)
        assert any("last two" in f for f in found), found

    def test_the_pair_may_sit_in_either_order(self):
        plan = _plan(order=["DRE-9001", "DRE-9002", "DRE-9003",
                            "DRE-9092", "DRE-9091"])
        assert proof_and_demo.findings(plan) == []

    def test_the_title_convention_is_anchored_not_a_substring(self):
        """`Record the demo: phase 3` is an ordinary code card — the same
        anchoring the auto-Done guard and the routing vocabulary already use."""
        assert proof_and_demo.is_demo("DEMO: phase 3 end to end")
        assert proof_and_demo.is_demo("  demo: lower case, indented")
        assert not proof_and_demo.is_demo("Record the demo: phase 3")
        assert not proof_and_demo.is_demo("Update demo docs")
        assert proof_and_demo.is_proof("PROOF: the sweep promoted it")
        assert not proof_and_demo.is_proof("Add proof: the counter is live")

    def test_two_proof_cards_are_refused_with_both_named(self):
        plan = _plan()
        plan.insert(3, _card("DRE-9093", "PROOF: a second one", body=PROOF_BODY,
                             blocked_by=["DRE-9001", "DRE-9002", "DRE-9003"]))
        found = proof_and_demo.findings(plan)
        assert any("DRE-9091" in f and "DRE-9093" in f for f in found), found

    def test_a_childless_epic_is_refused_rather_than_passing_empty(self):
        found = proof_and_demo.findings([])
        assert any("proof" in f.lower() for f in found)
        assert any("demo" in f.lower() for f in found)


# ===========================================================================
# 2: blocked by every sibling — the RELATION, never the ordering
# ===========================================================================
class TestBlockedByEverySibling:
    def test_a_proof_card_with_no_relations_is_refused(self):
        found = proof_and_demo.findings(_plan(blocked=False))
        assert any("DRE-9091" in f and "blocked by" in f.lower() for f in found)
        assert any("DRE-9092" in f and "blocked by" in f.lower() for f in found)

    def test_a_missing_relation_names_the_sibling_it_is_missing(self):
        plan = _plan()
        proof = next(c for c in plan if c["identifier"] == "DRE-9091")
        proof["blocked_by"] = ["DRE-9001", "DRE-9002"]
        found = proof_and_demo.findings(plan)
        assert any("DRE-9003" in f for f in found), found

    def test_ordering_prose_is_not_a_relation(self):
        """`**Blocked by:** …` in the body is prose. The gate reads the formal
        relation — the source of truth the reconcile gates honour — so a card
        that only SAYS it is blocked is still refused."""
        plan = _plan(blocked=False)
        for card in plan:
            if proof_and_demo.is_proof(card["title"]) or proof_and_demo.is_demo(card["title"]):
                card["body"] += "\n**Blocked by:** DRE-9001, DRE-9002, DRE-9003\n"
        found = proof_and_demo.findings(plan)
        assert any("DRE-9091" in f and "blocked by" in f.lower() for f in found), found

    def test_the_pair_need_not_block_each_other(self):
        """`every other child` cannot mean each other in both directions —
        that is a deadlock, not an order."""
        plan = _plan()
        demo = next(c for c in plan if c["identifier"] == "DRE-9092")
        demo["blocked_by"] = demo["blocked_by"] + ["DRE-9091"]
        assert proof_and_demo.findings(plan) == []

    def test_a_mutual_block_between_the_pair_is_refused(self):
        plan = _plan()
        for card in plan:
            if card["identifier"] == "DRE-9091":
                card["blocked_by"] += ["DRE-9092"]
            if card["identifier"] == "DRE-9092":
                card["blocked_by"] += ["DRE-9091"]
        found = proof_and_demo.findings(plan)
        assert any("each other" in f for f in found), found


# ===========================================================================
# 3: neither may be fleet-buildable, and the rule comes from the file
# ===========================================================================
class TestNeitherIsFleetBuildable:
    def test_the_confirming_verdicts_are_the_ones_a_human_acts_on(self):
        assert set(proof_and_demo.confirming_verdicts()) == {"WORKBENCH", "OPERATOR"}
        for name in proof_and_demo.confirming_verdicts():
            assert routing_verdict.actor(name) in planning_route.HUMAN_ACTORS

    def test_fleet_is_never_a_confirming_verdict(self):
        assert "FLEET" not in proof_and_demo.confirming_verdicts()
        assert proof_and_demo.vocabulary_problems() == []

    def test_a_fleet_buildable_proof_card_is_rejected(self):
        """The criteria are what route a card, so a proof card whose exit
        condition is a rendered screen is a card the fleet can close by
        merging its own code."""
        plan = _plan()
        proof = next(c for c in plan if c["identifier"] == "DRE-9091")
        proof["body"] = (
            "Prove it.\n\n## Acceptance criteria\n\n"
            "- [ ] the proof page renders with the design tokens\n"
        )
        assert routing_verdict.route(
            proof["title"], proof["body"], proof["labels"]).verdict == "FLEET"
        found = proof_and_demo.findings(plan)
        assert any("DRE-9091" in f and "FLEET" in f for f in found), found

    def test_a_proof_card_that_routes_to_nobody_is_rejected(self):
        """Criteria naming neither a live observation nor an operator's hand
        fall through to a judgement call — which is not OPERATOR or WORKBENCH,
        so it is not a proof anyone is accountable for."""
        plan = _plan()
        proof = next(c for c in plan if c["identifier"] == "DRE-9091")
        proof["body"] = (
            "Prove it.\n\n## Acceptance criteria\n\n"
            "- [ ] the mechanism is proven\n"
        )
        found = proof_and_demo.findings(plan)
        assert any("DRE-9091" in f for f in found), found

    def test_an_operator_proof_card_is_accepted(self):
        plan = _plan()
        proof = next(c for c in plan if c["identifier"] == "DRE-9091")
        proof["labels"] = list(LABELS) + ["no-code"]
        proof["body"] = "Run it.\n\n## Acceptance criteria\n\n- [ ] it ran\n"
        assert proof_and_demo.findings(plan) == []

    def test_the_rule_is_read_from_the_vocabulary_not_restated_here(self):
        """Hand WORKBENCH to an agent in a private copy of the file and a
        WORKBENCH demo card stops being a demo anybody confirms."""
        doc = json.loads(VERDICTS.read_text(encoding="utf-8"))
        entry = next(v for v in doc["verdicts"] if v["name"] == "WORKBENCH")
        entry["actor"] = "agent-task.yml"
        assert "WORKBENCH" not in proof_and_demo.confirming_verdicts(doc)
        found = proof_and_demo.findings(_plan(), doc)
        assert any("DRE-9092" in f for f in found), found

    def test_a_vocabulary_with_no_human_confirmer_is_refused(self):
        doc = json.loads(VERDICTS.read_text(encoding="utf-8"))
        for entry in doc["verdicts"]:
            entry["actor"] = "agent-task.yml"
        assert proof_and_demo.vocabulary_problems(doc)


# ===========================================================================
# 4: the bounce — the reason, named, where a planner reads it
# ===========================================================================
class TestTheBounce:
    def test_the_comment_names_every_finding(self):
        found = proof_and_demo.findings(_plan(proof=False))
        body = proof_and_demo.bounce_comment(EPIC, found)
        assert EPIC in body
        for finding in found:
            assert finding in body

    def test_the_comment_mints_no_verdict_marker(self):
        """standards/untrusted-content.md: the merge gate reads verdicts out of
        comments, so nothing here may look like one."""
        body = proof_and_demo.bounce_comment(
            EPIC, proof_and_demo.findings(_plan(proof=False, demo=False)))
        for forbidden in ("VERDICT:", "QA Critic", "QA Verifier"):
            assert forbidden not in body

    def test_the_comment_says_what_to_do(self):
        body = proof_and_demo.bounce_comment(EPIC, proof_and_demo.findings(_plan(proof=False)))
        assert "Planning" in body

    def test_no_comment_is_written_when_there_is_nothing_to_say(self):
        with pytest.raises(ValueError):
            proof_and_demo.bounce_comment(EPIC, [])


# ===========================================================================
# The CLI seam — cards on stdin, exactly like `plan_critic.py mechanical`
# ===========================================================================
class TestTheCli:
    def _run(self, children, *args, tmp_path=None):
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "proof_and_demo.py"), "check",
             "--epic", EPIC, *args],
            input=json.dumps(children), capture_output=True, text=True,
        )

    def test_a_clean_plan_exits_zero(self):
        r = self._run(_plan())
        assert r.returncode == 0, r.stdout + r.stderr
        assert "DRE-9091" in r.stdout and "DRE-9092" in r.stdout

    def test_a_plan_missing_a_card_exits_one_and_prints_the_reason(self):
        r = self._run(_plan(demo=False))
        assert r.returncode == 1
        assert "demo" in (r.stdout + r.stderr).lower()

    def test_the_comment_file_is_written_only_when_there_is_a_finding(self, tmp_path):
        clean = tmp_path / "clean.md"
        r = self._run(_plan(), "--comment-file", str(clean))
        assert r.returncode == 0
        assert not clean.exists(), "a passing check must not leave a bounce note"

        dirty = tmp_path / "dirty.md"
        r = self._run(_plan(proof=False), "--comment-file", str(dirty))
        assert r.returncode == 1
        assert EPIC in dirty.read_text(encoding="utf-8")


# ===========================================================================
# Drift guards — one definition of "this is a demo card", pipeline-wide
# ===========================================================================
class TestOneDefinitionOfADemoCard:
    TITLES = (
        "DEMO: phase 3 — folder access end to end",
        "  demo: indented and lower case",
        "Record the demo: phase 3",
        "Update demo docs",
        "Phase 3 demo runner",
        "PROOF: the sweep promoted it",
    )

    def test_it_agrees_with_the_auto_done_guard(self):
        """`linear_ops.auto_done_skip_reason` already refuses to auto-close a
        DEMO:-titled card. Same cards, same answer, or one of them has drifted.
        """
        for title in self.TITLES:
            assert proof_and_demo.is_demo(title) == (
                linear_ops.auto_done_skip_reason(title, []) is not None
            ), title

    def test_it_agrees_with_the_routing_vocabulary(self):
        """And a card this module calls a demo is exactly one the routing
        vocabulary sends to WORKBENCH, by title, before any judgement."""
        for title in self.TITLES:
            expected = "WORKBENCH" if proof_and_demo.is_demo(title) else None
            assert routing_verdict.title_verdict(title) == expected, title
