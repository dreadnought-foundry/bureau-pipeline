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
  5. Neither card wears a BUILD role (DRE-3039). The roles a build run is
     dispatched for are read off `agents.yaml`; the role the pair may wear is
     read off the routing vocabulary. Neither list is written down here.
  6. The check WRITES the verdict it computed onto each of the two cards, as
     the same `🧭 routing-verdict` comment every other verdict uses — so
     `routing_verdict.promotion_refusal` reads it and the sweep refuses to
     promote the pair. It used to compute the answer, print it and stamp
     nothing.

Run: cd bureau-pipeline && python3 -m pytest tests/test_proof_and_demo.py -v
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

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

# The pair's own labels (DRE-3039). A work card wears a build role because a
# build agent builds it; the two cards that CONFIRM the epic wear `agent:ops`,
# the role label the routing vocabulary reads as "a person handles this". The
# proof card that started this carried `agent:engineer` and would have been
# promoted into an engineer's hands to write the proof of its own siblings.
PAIR_LABELS = ("repo:bureau-pipeline", "agent:ops", "initiative:pipeline")

# No role label at all — precedence 1 answers nothing, so the title and the
# acceptance criteria decide. Used by the rule-3 tests, which are about what the
# CRITERIA route a card to.
NO_ROLE_LABELS = ("repo:bureau-pipeline", "initiative:pipeline")


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
                          body=PROOF_BODY, labels=PAIR_LABELS,
                          blocked_by=work_ids if blocked else []))
    if demo:
        tail.append(_card("DRE-9092", "DEMO: the bounce, end to end",
                          body=DEMO_BODY, labels=PAIR_LABELS,
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
        proof["labels"] = list(NO_ROLE_LABELS)
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
        proof["labels"] = list(NO_ROLE_LABELS)
        proof["body"] = (
            "Prove it.\n\n## Acceptance criteria\n\n"
            "- [ ] the mechanism is proven\n"
        )
        found = proof_and_demo.findings(plan)
        assert any("DRE-9091" in f for f in found), found

    def test_an_operator_proof_card_is_accepted(self):
        plan = _plan()
        proof = next(c for c in plan if c["identifier"] == "DRE-9091")
        proof["labels"] = list(PAIR_LABELS) + ["no-code"]
        proof["body"] = "Run it.\n\n## Acceptance criteria\n\n- [ ] it ran\n"
        assert proof_and_demo.findings(plan) == []

    def test_the_rule_is_read_from_the_vocabulary_not_restated_here(self):
        """Hand OPERATOR to an agent in a private copy of the file and the pair
        stops being a pair anybody other than the builder confirms."""
        doc = json.loads(VERDICTS.read_text(encoding="utf-8"))
        entry = next(v for v in doc["verdicts"] if v["name"] == "OPERATOR")
        entry["actor"] = "agent-task.yml"
        assert "OPERATOR" not in proof_and_demo.confirming_verdicts(doc)
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
        # `--no-stamp` on every subprocess call: the stamp writes to Linear and
        # these run against a fake key. The write seam has its own tests below.
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "proof_and_demo.py"), "check",
             "--epic", EPIC, "--no-stamp", *args],
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
# 5: the role label — neither card wears a build role (DRE-3039)
# ===========================================================================
#
# DRE-3031 carried `agent:engineer` and a `Files:` line naming the document it
# was to write. Nothing between plan time and build time reads a proof card as
# anything other than work: the relay reads only `agent:planner`, and a child
# with no verdict promoted exactly as it always had. So when its siblings
# reached Done the sweep would have handed an engineer agent the job of writing
# the proof of its own siblings' work.
BUILD_ROLE_LABELS = (
    "agent:engineer", "agent:frontend", "agent:devops", "agent:database-architect",
)


class TestTheRoleLabel:
    def test_the_build_roles_are_read_off_the_roster_not_listed_here(self):
        """`agents.yaml` already says which agents a build run dispatches — the
        entries that run on agent-task.yml. Reading it is what keeps a fifth
        build role from arriving without this rule noticing."""
        assert set(proof_and_demo.build_roles()) == {
            "engineer", "frontend", "devops", "database-architect",
        }

    def test_the_role_a_pair_may_wear_is_read_from_the_vocabulary(self):
        """And the positive answer is derived too: the `agent:*` labels the
        routing vocabulary maps to a verdict a human acts on."""
        assert proof_and_demo.confirming_role_labels() == ("agent:ops",)
        for label in proof_and_demo.confirming_role_labels():
            assert routing_verdict.label_map()[label] in \
                proof_and_demo.confirming_verdicts()

    @pytest.mark.parametrize("label", BUILD_ROLE_LABELS)
    def test_a_proof_card_wearing_a_build_role_is_refused(self, label):
        plan = _plan()
        proof = next(c for c in plan if c["identifier"] == "DRE-9091")
        proof["labels"] = list(NO_ROLE_LABELS) + [label]
        found = proof_and_demo.findings(plan)
        assert any("DRE-9091" in f and label in f for f in found), found

    @pytest.mark.parametrize("label", BUILD_ROLE_LABELS)
    def test_a_demo_card_wearing_a_build_role_is_refused(self, label):
        plan = _plan()
        demo = next(c for c in plan if c["identifier"] == "DRE-9092")
        demo["labels"] = list(NO_ROLE_LABELS) + [label]
        found = proof_and_demo.findings(plan)
        assert any("DRE-9092" in f and label in f for f in found), found

    def test_the_finding_names_the_label_the_pair_should_carry(self):
        plan = _plan()
        proof = next(c for c in plan if c["identifier"] == "DRE-9091")
        proof["labels"] = list(LABELS)
        found = [f for f in proof_and_demo.findings(plan) if "DRE-9091" in f]
        assert found
        assert any("agent:ops" in f for f in found), found

    def test_the_label_match_is_exact_not_a_prefix(self):
        """`agent:engineering-manager` is not `agent:engineer`, and reading it
        as one is the same mistake class as a substring blocker match."""
        plan = _plan()
        proof = next(c for c in plan if c["identifier"] == "DRE-9091")
        proof["labels"] = list(PAIR_LABELS) + ["agent:engineering-manager"]
        assert proof_and_demo.findings(plan) == []

    def test_a_work_card_may_wear_a_build_role(self):
        """The rule is about the two cards that CONFIRM the epic. Every other
        child is work, and work is what a build agent is for."""
        plan = _plan()
        assert all("agent:engineer" in c["labels"]
                   for c in plan if c["identifier"].startswith("DRE-900"))
        assert proof_and_demo.findings(plan) == []

    def test_a_vocabulary_with_no_role_label_for_the_pair_is_refused(self):
        """If no `agent:*` label routes to a human, the rule has no positive
        answer to name and the check must say so rather than refuse every pair
        with nowhere to send it."""
        doc = json.loads(VERDICTS.read_text(encoding="utf-8"))
        doc["labels"]["map"] = {"no-code": "OPERATOR"}
        assert proof_and_demo.confirming_role_labels(doc) == ()
        assert proof_and_demo.vocabulary_problems(doc)


# ===========================================================================
# 6: the stamp — the check WRITES the verdict it computed (DRE-3039)
# ===========================================================================
#
# `check` printed "proof DRE-3031, demo DRE-3032, both last and blocked by
# every sibling" and stamped nothing, so `routing_verdict.promotion_refusal`
# read no verdict and returned None. One writer, the one that already knows the
# answer.
class TestTheStamp:
    def test_the_pair_is_stamped_with_the_verdict_the_check_computed(self):
        stamps = proof_and_demo.stamps(_plan())
        assert [(i, v) for i, v, _ in stamps] == [
            ("DRE-9091", "OPERATOR"), ("DRE-9092", "OPERATOR"),
        ]

    def test_the_stamped_verdict_is_the_card_s_own_routing_decision(self):
        """Not a constant: strip the role label and the criteria decide."""
        plan = _plan()
        for card in plan[-2:]:
            card["labels"] = list(NO_ROLE_LABELS)
        assert [v for _, v, _ in proof_and_demo.stamps(plan)] == [
            "WORKBENCH", "WORKBENCH",
        ]

    def test_a_stamped_verdict_is_never_fleet(self):
        for _, verdict, _ in proof_and_demo.stamps(_plan()):
            assert verdict in proof_and_demo.confirming_verdicts()

    def test_every_stamp_says_why(self):
        """`verdict_comment` refuses a verdict with no reason — a routing
        decision nobody can argue with."""
        for _, verdict, why in proof_and_demo.stamps(_plan()):
            assert why.strip()
            routing_verdict.verdict_comment(verdict, why)

    def test_nothing_is_stamped_when_the_pair_is_malformed(self):
        """An epic going back to Planning is not an epic whose cards get a
        routing decision written on them."""
        assert proof_and_demo.stamps(_plan(blocked=False)) == ()
        assert proof_and_demo.stamps(_plan(proof=False)) == ()
        assert proof_and_demo.stamps([]) == ()

    def test_the_stamp_is_the_comment_every_other_verdict_uses(self):
        """`promotion_refusal` reads it, so it has to BE that comment — not a
        second grammar that only this writer knows."""
        for _, verdict, why in proof_and_demo.stamps(_plan()):
            body = routing_verdict.verdict_comment(verdict, why)
            assert routing_verdict.verdict_on([body]) == verdict


class TestTheStampIsWritten:
    """The write seam: the same comment and the same marks
    `routing_verdict.py stamp` writes, because it is the same code."""

    def _write(self, children, existing=()):
        posted: list[tuple[str, str]] = []
        labelled: list[tuple[str, str]] = []
        with patch.object(linear_ops, "comment_bodies", return_value=list(existing)), \
                patch.object(linear_ops, "cmd_comment",
                             side_effect=lambda i, b: posted.append((i, b))), \
                patch.object(linear_ops, "add_label",
                             side_effect=lambda i, l: labelled.append((i, l))):
            written = proof_and_demo.write_stamps(children)
        return written, posted, labelled

    def test_it_posts_one_verdict_comment_per_card(self):
        written, posted, _ = self._write(_plan())
        assert written == 2
        assert [i for i, _ in posted] == ["DRE-9091", "DRE-9092"]
        for identifier, body in posted:
            assert routing_verdict.verdict_on([body]) == "OPERATOR", identifier

    def test_it_applies_the_marks_the_verdict_declares(self):
        """`hand-built` is what stops the sweep dispatching a competing run —
        the signal `reconcile.hand_built` already reads."""
        _, _, labelled = self._write(_plan())
        for identifier in ("DRE-9091", "DRE-9092"):
            applied = [l for i, l in labelled if i == identifier]
            assert applied == list(routing_verdict.marks("OPERATOR")), identifier
            assert "hand-built" in applied

    def test_it_never_writes_a_second_verdict_onto_a_card(self):
        """A card leaving Planning carries exactly one, and a re-planned epic
        runs this check again."""
        already = routing_verdict.verdict_comment("OPERATOR", "an operator runs it")
        written, posted, labelled = self._write(_plan(), existing=[already])
        assert written == 0
        assert posted == [] and labelled == []

    def test_a_malformed_pair_writes_nothing(self):
        written, posted, labelled = self._write(_plan(demo=False))
        assert (written, posted, labelled) == (0, [], [])

    def test_the_check_stamps_by_default_and_no_stamp_holds_the_pen(self):
        """Nothing in plan.yml has to opt in: the check that computes the
        verdict is the thing that writes it."""
        for args, expected in ((["check", "--epic", EPIC], 2),
                               (["check", "--epic", EPIC, "--no-stamp"], 0)):
            posted: list[tuple[str, str]] = []
            with patch.object(sys, "stdin", io.StringIO(json.dumps(_plan()))), \
                    patch.object(linear_ops, "comment_bodies", return_value=[]), \
                    patch.object(linear_ops, "cmd_comment",
                                 side_effect=lambda i, b: posted.append((i, b))), \
                    patch.object(linear_ops, "add_label"):
                assert proof_and_demo.main(args) == 0
            assert len(posted) == expected, args


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
