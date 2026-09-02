"""What briefs/planner.md must tell the planner about the SHIPPED model
(DRE-2832).

DRE-2727 updated every brief to the model that had already shipped and
deliberately left this half out: the three shapes, the artifact gate and the
execution plan describe a mechanism that did not exist when it was written.
The mechanism exists now — `config/planning-shapes.json` (DRE-2843),
`scripts/planning_route.py` (DRE-2844), `scripts/wave_plan.py` (DRE-2845) and
`scripts/plan_artifact.py` (DRE-2720) — so the brief owes the description.

Every assertion here binds the brief to something that is not the brief: the
shape vocabulary, the lane contract, the standard that owns a rule, or the
write layer's own source. A document is the one consumer of a pipeline change
that no import, no schema and no call site checks, so the check has to be the
test — and a test that only read the brief back to itself would pass on a brief
that describes a pipeline nobody built. That is the exact defect this card was
carved out of DRE-2727 to avoid.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRIEF = ROOT / "briefs" / "planner.md"
SHAPES = ROOT / "config" / "planning-shapes.json"
CONTRACT = ROOT / "config" / "lane-contract.json"
OPS = ROOT / "scripts" / "linear_ops.py"
ENGINEERING = ROOT / "standards" / "engineering.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _brief() -> str:
    return _read(BRIEF)


def _shapes() -> list[dict]:
    with open(SHAPES, encoding="utf-8") as fh:
        return json.load(fh)["shapes"]


def _lane(name: str) -> dict:
    with open(CONTRACT, encoding="utf-8") as fh:
        lanes = json.load(fh)["lanes"]
    return next(lane for lane in lanes if lane["name"] == name)


def _row(body: str, label: str) -> str | None:
    """The brief's markdown table row whose first cell is `**<label>**`."""
    match = re.search(rf"^\|\s*\*\*{re.escape(label)}\*\*\s*\|.*$", body, re.M)
    return match.group(0) if match else None


def _sentences(body: str) -> list[str]:
    return re.split(r"(?<=[.!?])\s+", body)


def _section(body: str, needle: str) -> str:
    """The `##` section of the brief whose heading contains `needle`.

    Scoped rather than whole-file on purpose: `DIRTY` and `blockedBy` already
    appear elsewhere in this brief, so a whole-file search for either would
    pass on a brief that never gained the execution plan at all.
    """
    headings = [m for m in re.finditer(r"^## .*$", body, re.M)]
    for i, m in enumerate(headings):
        if needle.lower() in m.group(0).lower():
            end = headings[i + 1].start() if i + 1 < len(headings) else len(body)
            return body[m.start() : end]
    return ""


class TestTheThreeShapes:
    """A planner that has never been told the vocabulary plans every card as
    an epic — which is the run and the CEO decision DRE-2844 exists to stop
    spending on a one-liner."""

    def test_it_names_every_shape_in_the_vocabulary(self):
        body = _brief()
        for shape in _shapes():
            assert shape["name"] in body, (
                f"planner.md never names the {shape['name']!r} shape"
            )

    def test_each_shape_row_carries_the_destination_the_file_declares(self):
        body = _brief()
        for shape in _shapes():
            row = _row(body, shape["name"])
            assert row, (
                f"planner.md has no table row for the {shape['name']!r} shape"
            )
            assert shape["destination"] in row, (
                f"planner.md's {shape['name']!r} row does not name its "
                f"destination {shape['destination']!r}"
            )

    def test_it_points_at_the_vocabulary_file_rather_than_only_its_own_table(self):
        # The table in a document is a copy, and the copy is what drifts.
        assert "config/planning-shapes.json" in _brief(), (
            "planner.md restates the shapes without pointing at the file they "
            "are declared in"
        )

    def test_a_card_with_no_shape_is_refused_and_never_defaulted(self):
        body = _brief()
        assert re.search(r"refus\w+", body, re.I), (
            "planner.md never says an unclassified card is refused"
        )
        assert re.search(r"never defaulted|not defaulted|never default\b", body, re.I), (
            "planner.md must say a card carrying no shape is REFUSED rather "
            "than defaulted to one — a defaulted shape is a classification "
            "nobody made"
        )

    def test_the_shape_axis_is_kept_apart_from_the_size_axis(self):
        body = _brief()
        assert "`size:" in body, (
            "planner.md never names the `size:` labels the shape axis must "
            "not be confused with"
        )
        assert re.search(r"shape is not size|not size|effort", body, re.I), (
            "planner.md must say shape is STRUCTURE and `size:` is EFFORT — "
            "two questions behind one word is the DRE-1494 naming failure"
        )


class TestWhatEachShapeProduces:
    """Naming the shapes is half of it; the other half is the artifact each
    owes, and that the run refuses the exit without it."""

    def test_the_epic_owes_the_plan_artifact_and_is_bounced_without_one(self):
        body = _brief()
        assert (ROOT / "standards" / "plan-artifact.md").exists()
        assert "standards/plan-artifact.md" in body
        assert "plan_artifact.py" in body, (
            "planner.md must name the check that reads the artifact"
        )
        assert re.search(
            r"(no artifact, no exit|does not leave|refus\w+|bounce\w*|fails?)"
            r"[^.]{0,160}artifact|artifact[^.]{0,160}"
            r"(no exit|does not leave|refus\w+|bounce\w*|fails?)",
            body,
            re.I,
        ), (
            "planner.md must say an epic with no artifact does not leave "
            "Planning"
        )

    def test_the_wave_owes_a_wave_plan_and_the_epics_it_commits_to(self):
        body = _brief()
        assert (ROOT / "standards" / "wave-plan.md").exists()
        assert "standards/wave-plan.md" in body, (
            "planner.md never points the wave route at the standard its plan "
            "is checked against"
        )
        assert "wave_plan.py" in body, (
            "planner.md must name the checker the wave plan is read by"
        )
        assert re.search(r"epics it commits to|in order", body, re.I), (
            "planner.md must say a wave plan names the epics it commits to, "
            "in order"
        )


class TestWhereThePlannerOutputLands:
    """The brief's claim about a lane is checked against the writer, not
    against another document. A brief that says a card lands where the code
    does not put it is the producer/consumer drift this card was split off to
    avoid — and it reads as true to every planner until a card goes missing."""

    def _create_card_default_lane(self) -> str:
        source = _read(OPS)
        match = re.search(r'def _create_card\(.*?lane: str = "([^"]+)"', source, re.S)
        assert match, "linear_ops._create_card no longer declares a default lane"
        return match.group(1)

    def _subissue_lane(self) -> str:
        source = _read(OPS)
        body = source[source.index("def cmd_subissue(") :]
        body = body[: body.index("\ndef ")]
        match = re.search(r'lane="([^"]+)"', body)
        # No explicit lane means the child rides `_create_card`'s default.
        return match.group(1) if match else self._create_card_default_lane()

    def test_the_children_row_names_the_lane_subissue_actually_creates_in(self):
        row = _row(_brief(), "subissue")
        assert row, (
            "planner.md has no row saying where the children it creates with "
            "`subissue` land"
        )
        lane = self._subissue_lane()
        assert lane in row, (
            f"planner.md says the children land somewhere other than {lane!r}, "
            "which is where scripts/linear_ops.py actually creates them"
        )

    def test_the_oneoff_row_names_the_lane_oneoff_actually_creates_in(self):
        row = _row(_brief(), "oneoff")
        assert row, (
            "planner.md has no row saying where a parentless one-off it "
            "creates with `oneoff` lands"
        )
        lane = self._create_card_default_lane()
        assert lane in row, (
            f"planner.md says a parentless one-off lands somewhere other than "
            f"{lane!r}, which is where scripts/linear_ops.py creates it"
        )

    def test_it_names_intake_as_the_front_door_new_work_arrives_by(self):
        body = _brief()
        assert "Intake" in body
        intake = _lane("Intake")
        assert intake["clauses"]["entrance"]["enforced_from"] is not None
        assert re.search(
            r"Intake[^.]{0,200}(front door|creates work|first lane|arrives)",
            body,
            re.I,
        ), (
            "planner.md must name Intake as the lane every writer that "
            "creates work writes to first"
        )

    def test_it_tells_the_planner_to_read_the_phase_before_believing_a_document(self):
        # The front-door clauses are enforced from a phase the board has not
        # reached, and the phase is data. A brief that describes them as live
        # is the same defect from the other direction.
        body = _brief()
        assert "phases" in body and "config/lane-contract.json" in body, (
            "planner.md must point at the lane contract's own phase record "
            "rather than asserting how far the front door has shipped"
        )


class TestTheExecutionPlan:
    """Declared footprints per card, parallel where they are disjoint, a
    native relation where they overlap — and restructuring tried before
    serializing."""

    def test_every_card_declares_the_files_it_will_touch(self):
        body = _brief()
        plan = _section(body, "execution plan")
        assert plan, "planner.md has no `## The execution plan` section"
        assert "**Files:**" in plan, (
            "planner.md does not require a declared file footprint on each card"
        )
        template = _section(body, "Sub-issue description template")
        assert "**Files:**" in template, (
            "the sub-issue template in planner.md carries no `**Files:**` line, "
            "so the footprint is a rule with nowhere to be written"
        )

    def test_parallelism_is_derived_from_the_footprints(self):
        plan = _section(_brief(), "execution plan")
        assert re.search(r"disjoint[^.]{0,200}parallel|parallel[^.]{0,200}disjoint",
                         plan, re.I | re.S), (
            "planner.md must derive parallelism from the footprints — "
            "disjoint runs in parallel"
        )
        assert re.search(r"(intersect|overlap)[^.]{0,240}blockedBy", plan, re.I | re.S), (
            "planner.md must say that overlapping footprints get a native "
            "`blockedBy` relation, not prose"
        )

    def test_restructuring_is_preferred_over_serializing(self):
        plan = _section(_brief(), "execution plan")
        hits = [
            s for s in _sentences(plan)
            if re.search(r"restructur", s, re.I) and re.search(r"serializ", s, re.I)
        ]
        assert hits, (
            "planner.md never states the preference: restructure the sharing "
            "away before you serialize two cards on it"
        )
        assert any(re.search(r"prefer|before|rather than|first", s, re.I) for s in hits), (
            "planner.md names both options without saying which is preferred"
        )

    def test_it_cites_the_standard_section_that_owns_the_rule(self):
        plan = _section(_brief(), "execution plan")
        heading = "## Don't fight over shared files"
        assert heading in _read(ENGINEERING), (
            "standards/engineering.md no longer carries the section the brief "
            "cites — move the citation with it"
        )
        assert heading.lstrip("# ") in plan, (
            "planner.md must cite `Don't fight over shared files` in "
            "standards/engineering.md, which owns this rule"
        )

    def test_it_names_what_ignoring_the_footprints_cost(self):
        # Three PRs that passed full review went DIRTY within an hour of each
        # other purely on merge order, with no defect in any of them. A rule
        # with no cost attached is a rule a planner under pressure drops.
        plan = _section(_brief(), "execution plan")
        assert re.search(r"DIRTY|merge order", plan, re.I), (
            "planner.md states the footprint rule without the incident that "
            "produced it"
        )
