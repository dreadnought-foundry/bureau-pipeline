"""What every brief and standard must actually TELL an agent (DRE-2727).

The lanes, the flow and the routing changed. These assertions pin the parts of
the agents' operating instructions that had to change with them, so a later
edit that quietly drops one fails here rather than in a card nobody notices
went the old way.

Each assertion is content-shaped on purpose: a document is the only consumer of
a pipeline change that no import, no schema and no call site checks.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import agent_marker  # noqa: E402

BRIEFS = ROOT / "briefs"
STANDARDS = ROOT / "standards"

# The five briefs a card is actually built (or proved) under. planner.md is
# judged separately below — it owns the routing decision, not the build.
BUILD_BRIEFS = (
    "engineer.md",
    "frontend.md",
    "devops.md",
    "database-architect.md",
    "verifier.md",
)

# The four that run under agent-task.yml, and so have the workflow's hand-back
# file available to them. The verifier never opens a PR and never writes the
# card's lane, so it carries the rule without the file.
AGENT_TASK_BRIEFS = ("engineer.md", "frontend.md", "devops.md", "database-architect.md")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _verdicts() -> list[dict]:
    with open(ROOT / "config" / "routing-verdicts.json", encoding="utf-8") as fh:
        return json.load(fh)["verdicts"]


def _repo_slugs() -> set[str]:
    with open(ROOT / "config" / "repo-map.json", encoding="utf-8") as fh:
        return set(json.load(fh))


class TestTheHandBackRule:
    """Open a one-off, find an epic, hand it back to Planning rather than
    sprawling. Without it a dispatched agent's only options are to build the
    whole thing badly in one PR or to blocker the card into Backlog."""

    def test_every_build_brief_carries_the_hand_back_rule(self):
        for name in BUILD_BRIEFS:
            body = _read(BRIEFS / name)
            assert "hand-back" in body.lower(), f"{name} never names the hand-back rule"
            assert re.search(r"hand it back\s+to\s+`?Planning`?", body), (
                f"{name} does not say where a card that outgrew itself goes"
            )

    def test_the_dispatched_briefs_name_the_hand_back_file(self):
        for name in AGENT_TASK_BRIEFS:
            body = _read(BRIEFS / name)
            assert "/tmp/agent-handback.txt" in body, (
                f"{name} states the rule with no mechanism — the agent has no "
                "way to actually hand the card back"
            )

    def test_the_hand_back_path_exists_in_the_build_workflow(self):
        # The brief may not promise a channel the workflow does not read.
        wf = _read(ROOT / ".github" / "workflows" / "agent-task.yml")
        assert "/tmp/agent-handback.txt" in wf
        assert re.search(r'advance "\$CARD" "Planning"', wf), (
            "agent-task.yml reads the hand-back file but never moves the card "
            "to Planning"
        )

    def test_planning_permits_the_build_run_as_a_writer(self):
        # A lane a workflow writes and is not a permitted writer of is a
        # contract violation the guard would bounce.
        with open(ROOT / "config" / "lane-contract.json", encoding="utf-8") as fh:
            contract = json.load(fh)
        planning = next(l for l in contract["lanes"] if l["name"] == "Planning")
        assert "agent-task.yml" in planning["clauses"]["writers"]["who"]


class TestTheObservabilityMarker:
    def test_every_build_brief_carries_the_machine_readable_marker(self):
        for name in BUILD_BRIEFS:
            body = _read(BRIEFS / name)
            assert agent_marker.ACTOR_MARKER in body, (
                f"{name} never shows the machine-readable form "
                f"({agent_marker.ACTOR_MARKER!r})"
            )
            assert "linear_ops.py actor" in body, (
                f"{name} names no command that records which agent acted"
            )

    def test_the_command_the_briefs_name_exists(self):
        ops = _read(ROOT / "scripts" / "linear_ops.py")
        assert '"actor"' in ops, "linear_ops.py has no `actor` command"


class TestACardWithNoVerdictIsADefect:
    def test_every_build_brief_says_so(self):
        for name in BUILD_BRIEFS:
            body = _read(BRIEFS / name)
            assert "routing verdict" in body.lower(), (
                f"{name} never mentions the routing verdict it should be "
                "arriving with"
            )
            assert re.search(r"no verdict|without a verdict|missing verdict", body, re.I), (
                f"{name} does not say what to do about a card that arrives "
                "with no verdict"
            )


class TestThePlannerBriefDescribesTheRoutingVerdict:
    def test_it_names_every_verdict_and_its_destination(self):
        body = _read(BRIEFS / "planner.md")
        for verdict in _verdicts():
            assert verdict["name"] in body, f"planner.md never names {verdict['name']}"
            assert verdict["destination"] in body, (
                f"planner.md names {verdict['name']} without its destination "
                f"{verdict['destination']!r}"
            )

    def test_parked_is_landed_by_the_planning_exit_writer_not_a_human(self):
        # DRE-2824: Backlog is process-controlled and no human may write it.
        body = _read(BRIEFS / "planner.md")
        parked = next(v for v in _verdicts() if v["name"] == "PARKED")
        assert parked["actor"] == "plan.yml"
        section = body[body.index("PARKED") : body.index("PARKED") + 600]
        assert "operator" not in section.lower(), (
            "planner.md still describes PARKED as an operator's lane; the "
            "planning-exit writer lands it and only a human revives it"
        )

    def test_it_states_the_acceptance_criteria_rule(self):
        body = _read(BRIEFS / "planner.md")
        assert re.search(r"acceptance criteria", body, re.I)
        assert "SATISFY" in body, (
            "the mechanical rule is whether an unattended agent can SATISFY "
            "the acceptance criteria, not whether it could write the code"
        )

    def test_an_epic_activates_at_in_progress_not_todo(self):
        body = _read(BRIEFS / "planner.md")
        assert re.search(r"In Progress\b", body)
        assert not re.search(r"move (this|the) epic to `?Todo`?", body, re.I), (
            "planner.md still tells the CEO to start an epic from Todo"
        )


class TestCardQualityDerivesTheSlugList:
    def test_it_points_at_the_canonical_map(self):
        body = _read(STANDARDS / "card-quality.md")
        assert "config/repo-map.json" in body, (
            "card-quality.md must derive the valid slugs from the canonical "
            "map rather than restate them"
        )

    def test_it_restates_no_slug_that_the_map_does_not_carry(self):
        # The divergence test. A slug named here as a repo label that the live
        # map has dropped is a slug someone will write onto a card, and the
        # relay will bounce it.
        body = _read(STANDARDS / "card-quality.md")
        named = set(re.findall(r"`repo:([a-z0-9][a-z0-9-]*)`", body))
        # `repo:<slug>` is the placeholder form, not a slug.
        named.discard("slug")
        stale = named - _repo_slugs()
        assert not stale, (
            f"card-quality.md names repo slug(s) {sorted(stale)} that "
            "config/repo-map.json does not carry"
        )

    def test_no_agent_document_restates_a_stale_slug(self):
        slugs = _repo_slugs()
        offenders = []
        for doc in sorted(BRIEFS.glob("*.md")) + sorted(STANDARDS.glob("*.md")):
            named = set(re.findall(r"`repo:([a-z0-9][a-z0-9-]*)`", _read(doc)))
            named.discard("slug")
            for bad in sorted(named - slugs):
                offenders.append(f"{doc.name} names repo:{bad}")
        assert not offenders, "; ".join(offenders)


class TestTheStandardsTheWaveChanged:
    def test_engineering_checks_disjoint_file_ownership_at_plan_time(self):
        body = _read(STANDARDS / "engineering.md")
        assert re.search(r"plan time|at plan time|planning time", body, re.I), (
            "engineering.md still only ASKS the builder for disjoint files; "
            "the check happens when the plan is cut"
        )

    def test_design_parity_states_the_live_visual_qa_caveat(self):
        body = _read(STANDARDS / "design-parity.md")
        assert "visual-QA" in body or "visual QA" in body
        assert "DRE-2831" in body, (
            "design-parity.md must state that the mechanical FLEET signal "
            "rarely fires on real cards"
        )
        assert re.search(r"rarely fires|does not always|not a promise", body, re.I)

    def test_untrusted_content_names_fetched_web_content(self):
        body = _read(STANDARDS / "untrusted-content.md")
        assert re.search(r"fetched web|web content|web page|web search", body, re.I), (
            "untrusted-content.md never names fetched web content as "
            "untrusted data"
        )

    def test_architecture_shows_the_lane_flow(self):
        body = _read(STANDARDS / "architecture.md")
        assert "lane-contract" in body, (
            "architecture.md must point at the contract the flow is rendered "
            "from rather than carry a second copy that drifts"
        )
