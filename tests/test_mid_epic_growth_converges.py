"""RED-first: the epic growth record converges — an epic whose record did not
move costs no description write, sweep after sweep (DRE-3643).

THE MEASUREMENT. `reconcile.report_epic_growth` runs on every full sweep, in
every repo, every 15 minutes, and on 2026-09-12 it rewrote all nine of this
repo's active epics with nothing changed (`DRE-2964 description updated`, nine
times a pass). On 2026-09-15 the growth phase was the largest single consumer
of the fleet's Linear quota — about 560 of the 2,500 requests an hour (1,352
calls across 34 sampled sweeps in 2.4 hours).

THE CAUSE. `mid_epic.render_artifact` writes the managed region with `- `
bullets. Linear stores markdown in its own dialect and hands it back with `* `
bullets and a blank line after each heading — the region captured live on
DRE-3164 (`tests/fixtures/dre-3164-epic-2026-09-05.json`) reads

    Added since green light:

    * (none)

so `merge_artifact(description, block) != description` on every read, forever,
and every pass paid `set_description`'s own `get_issue` read plus the
`issueUpdate` for a record that said exactly what it said before.

THE SECOND HALF OF THE SAME FACT. Linear re-renders EVERY bullet, not only
`(none)`: an addition written as `- DRE-2740 — why` comes back as
`* DRE-2740 — why`. The parser accepted only `-`, so a recorded addition read
back as no addition at all — the sweep then re-flagged it as silent growth and
the next genuine write erased it. Comparing what the record MEANS is only
honest if the parser can read the record Linear hands back.

WHAT IS UNDER TEST:
  * The record is written only when there is no managed region yet, or when
    `parse_artifact(merged) != parse_artifact(description)` — meaning, never
    bytes.
  * `parse_artifact` reads `*`, `-` and `+` bullets alike.
  * A genuine change — a new child, an addition, an amendment, an observed
    re-approval — still writes, once, and then the record converges again.
  * `refresh_epic_growth` and `last_green_light` take a pre-read record
    (`issue=`) and make no read of their own for it; the comment count rides
    that record when its first page is its last. Nothing passes a record yet —
    `reconcile.epic_records` is DRE-3642's seam — so today's callers read
    exactly as before.

Run: cd bureau-pipeline && python3 -m pytest tests/test_mid_epic_growth_converges.py -v
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import linear_ops  # noqa: E402
import mid_epic  # noqa: E402
import reconcile  # noqa: E402

EPIC = "DRE-3164"
GREEN_LIGHT = "2026-09-05T18:00:00.000Z"
BEFORE = "2026-09-05T17:00:00.000Z"
AFTER = "2026-09-07T17:00:00.000Z"
LATER = "2026-09-09T17:00:00.000Z"

FIXTURE = ROOT / "tests" / "fixtures" / "dre-3164-epic-2026-09-05.json"


def live_description() -> str:
    """DRE-3164's description exactly as Linear returned it on 2026-09-05 —
    green-lit at 7 cards, running 9, with Linear's `* (none)` rendering."""
    cards = json.loads(FIXTURE.read_text())["cards"]
    return next(c["body"] for c in cards if c["card"] == EPIC)


def linear_renders(body: str) -> str:
    """What Linear hands back after storing `body`, to the two rules the live
    capture shows: every `- ` bullet becomes `* `, and a list gets a blank line
    between it and the line above it."""
    out: list[str] = []
    for line in body.split("\n"):
        bullet = line.startswith("- ")
        if bullet:
            line = "* " + line[2:]
        if bullet and out and out[-1].strip() and not out[-1].startswith("* "):
            out.append("")
        out.append(line)
    return "\n".join(out)


def region(additions=("* (none)",), amendments=("* (none)",), green_lit=7, current=9):
    """A managed region in Linear's rendering, for records the live capture
    does not happen to carry (a recorded addition, a pending amendment)."""
    return "\n".join([
        mid_epic.ARTIFACT_BEGIN, "", "## Epic growth", "",
        f"**Green-lit at:** {green_lit} cards · **Now running:** {current} cards",
        "", mid_epic.ADDITIONS_HEADING, "", *additions,
        "", mid_epic.AMENDMENTS_HEADING, "", *amendments,
        "", mid_epic.ARTIFACT_END, "",
    ])


def seven_then_two():
    """DRE-3164's roster as its record states it: seven cards at the green
    light, two since."""
    return [(f"DRE-31{n:02d}", BEFORE) for n in range(65, 72)] + [
        ("DRE-3301", AFTER), ("DRE-3302", AFTER),
    ]


class _Epic:
    """The `linear_ops` MODULE for one epic, counting every request.

    `gql` answers `mid_epic._EPIC_QUERY`; `set_description` stores the body the
    way Linear does (`linear_renders`) so a second read sees what the live
    board would. Notices already posted are counted by `count_comments`, so a
    steady-state epic posts nothing twice.
    """

    def __init__(self, description, children, *, green_lit_at=GREEN_LIGHT,
                 state="In Progress", uuid=None, total_comments=12,
                 comment_page=None):
        self.description = description
        self.children = list(children)
        self.green_lit_at = green_lit_at
        self.state = state
        self.uuid = uuid
        self.total_comments = total_comments
        self.comment_page = comment_page
        self.reads: list[str] = []
        self.writes: list[str] = []
        self.counted: list[str] = []
        self.comments: list[tuple[str, str]] = []
        self.LinearError = linear_ops.LinearError

    def record(self) -> dict:
        history = (
            [{"createdAt": self.green_lit_at, "toState": {"name": "In Progress"}}]
            if self.green_lit_at else []
        )
        issue = {
            "identifier": EPIC,
            "description": self.description,
            "state": {"name": self.state},
            "children": {"nodes": [
                {"identifier": i, "createdAt": at} for i, at in self.children
            ]},
            "history": {"nodes": history},
        }
        if self.uuid:
            issue["id"] = self.uuid
        if self.comment_page is not None:
            issue["comments"] = self.comment_page
        return issue

    # --- reads ------------------------------------------------------------
    def gql(self, query, variables=None):
        self.reads.append(query)
        return {"issue": self.record()}

    def comment_count(self, issue_id):
        self.counted.append(issue_id)
        return self.total_comments

    def count_comments(self, identifier, needle, **kw):
        return sum(1 for i, b in self.comments if i == identifier and needle in b)

    # --- writes -----------------------------------------------------------
    def set_description(self, identifier, body):
        self.writes.append(body)
        self.description = linear_renders(body)

    def cmd_comment(self, identifier, body):
        self.comments.append((identifier, body))


# ===========================================================================
# 1: the region Linear re-rendered is not rewritten
# ===========================================================================
class TestTheLiveShapeConverges:
    def test_the_captured_region_parses_to_the_record_it_states(self):
        """Guard the fixture: it is the live shape this card is about."""
        parsed = mid_epic.parse_artifact(live_description())
        assert (parsed["green_lit"], parsed["current"]) == (7, 9)
        assert parsed["additions"] == [] and parsed["amendments"] == []
        assert "\n\n* (none)\n" in live_description()

    def test_the_region_linear_re_rendered_is_not_rewritten(self):
        ops = _Epic(live_description(), seven_then_two())
        report = mid_epic.refresh_epic_growth(ops, EPIC)
        assert (report["green_lit"], report["current"]) == (7, 9)
        assert ops.writes == [], (
            "the record says green-lit at 7, running 9, nothing added, nothing "
            "amended — and so does Linear's copy of it; rewriting it only "
            "changes `-` back to `*` and spends two requests doing so"
        )

    def test_a_steady_epic_costs_no_write_on_any_sweep(self):
        ops = _Epic(live_description(), seven_then_two())
        for _ in range(4):
            mid_epic.refresh_epic_growth(ops, EPIC)
        assert ops.writes == []

    def test_the_epic_body_is_left_exactly_as_linear_has_it(self):
        ops = _Epic(live_description(), seven_then_two())
        mid_epic.refresh_epic_growth(ops, EPIC)
        assert ops.description == live_description()

    def test_a_record_written_once_converges_after_linear_re_renders_it(self):
        """The first sweep of a new epic writes its record; Linear re-renders
        it; every sweep after that reads it back and writes nothing."""
        ops = _Epic("The epic.", seven_then_two())
        for _ in range(4):
            mid_epic.refresh_epic_growth(ops, EPIC)
        assert len(ops.writes) == 1

    def test_an_epic_with_no_record_yet_gets_one(self):
        ops = _Epic("The epic.", seven_then_two())
        mid_epic.refresh_epic_growth(ops, EPIC)
        assert len(ops.writes) == 1
        assert mid_epic.parse_artifact(ops.description)["current"] == 9


# ===========================================================================
# 2: a genuine change still rewrites — once — and converges again
# ===========================================================================
class TestAGenuineChangeStillWrites:
    def test_a_child_added_after_the_green_light_moves_the_record(self):
        ops = _Epic(live_description(), seven_then_two() + [("DRE-3303", LATER)])
        mid_epic.refresh_epic_growth(ops, EPIC)
        assert len(ops.writes) == 1
        assert mid_epic.parse_artifact(ops.description)["current"] == 10
        mid_epic.refresh_epic_growth(ops, EPIC)
        assert len(ops.writes) == 1, "and the moved record converges again"

    def test_an_addition_is_recorded(self):
        ops = _Epic(live_description(), seven_then_two())
        mid_epic.refresh_epic_growth(
            ops, EPIC, add={"id": "DRE-3301", "because": "a second call site"}
        )
        assert len(ops.writes) == 1
        assert mid_epic.parse_artifact(ops.description)["additions"] == [
            {"id": "DRE-3301", "because": "a second call site"}
        ]

    def test_an_amendment_is_recorded(self):
        ops = _Epic(live_description(), seven_then_two(), state="Planning")
        mid_epic.refresh_epic_growth(
            ops, EPIC,
            amend={"at": LATER, "because": "the plan no longer holds",
                   "re_green_lit": None},
        )
        assert len(ops.writes) == 1
        amendments = mid_epic.parse_artifact(ops.description)["amendments"]
        assert [a["because"] for a in amendments] == ["the plan no longer holds"]

    def test_a_re_approval_read_back_off_linears_rendering_is_observed(self):
        """The amendment was written, Linear re-rendered it with `*`, and the
        epic is back in an active lane: the re-approval is a real change."""
        pending = f"* {LATER} — the plan no longer holds — {mid_epic.AWAITING_REAPPROVAL}"
        ops = _Epic("The epic.\n\n" + region(amendments=(pending,)), seven_then_two())
        report = mid_epic.refresh_epic_growth(ops, EPIC)
        assert report["re_approved"] == [EPIC]
        assert len(ops.writes) == 1
        amendment = mid_epic.parse_artifact(ops.description)["amendments"][0]
        assert amendment["re_green_lit"] == GREEN_LIGHT


# ===========================================================================
# 3: the record Linear hands back is the record the parser reads
# ===========================================================================
class TestLinearsBulletsAreRead:
    def test_a_star_bulleted_addition_is_read(self):
        parsed = mid_epic.parse_artifact(
            region(additions=("* DRE-3301 — a second call site",))
        )
        assert parsed["additions"] == [
            {"id": "DRE-3301", "because": "a second call site"}
        ]

    def test_a_star_bulleted_amendment_is_read(self):
        parsed = mid_epic.parse_artifact(region(amendments=(
            f"* {LATER} — the plan no longer holds — re-green-lit {GREEN_LIGHT}",
        )))
        assert parsed["amendments"] == [{
            "at": LATER, "because": "the plan no longer holds",
            "re_green_lit": GREEN_LIGHT,
        }]

    @pytest.mark.parametrize("none", ["* (none)", "- (none)", "+ (none)"])
    def test_none_is_nothing_in_any_bullet(self, none):
        parsed = mid_epic.parse_artifact(region(additions=(none,), amendments=(none,)))
        assert parsed["additions"] == [] and parsed["amendments"] == []

    def test_a_recorded_addition_is_not_re_flagged_as_silent_growth(self):
        ops = _Epic(
            "The epic.\n\n" + region(
                additions=("* DRE-3301 — a second call site",), current=8
            ),
            [(f"DRE-31{n:02d}", BEFORE) for n in range(65, 72)]
            + [("DRE-3301", AFTER)],
        )
        report = mid_epic.refresh_epic_growth(ops, EPIC)
        assert report["unrecorded"] == []
        assert not ops.comments, "a recorded addition is not silent accretion"
        assert ops.writes == [], "and a record that did not move is not rewritten"

    def test_a_recorded_addition_survives_the_next_write(self):
        """Before this, the next genuine write rendered the record from a parse
        that had dropped every `*` line — erasing the additions it held."""
        ops = _Epic(
            "The epic.\n\n" + region(additions=("* DRE-3301 — a second call site",)),
            seven_then_two(),
        )
        mid_epic.refresh_epic_growth(
            ops, EPIC, add={"id": "DRE-3302", "because": "a third call site"}
        )
        ids = [a["id"] for a in mid_epic.parse_artifact(ops.description)["additions"]]
        assert ids == ["DRE-3301", "DRE-3302"]


# ===========================================================================
# 4: a pre-read record is used, not re-read (the seam DRE-3642 plugs into)
# ===========================================================================
class TestTheRecordCanBeHandedIn:
    def test_refresh_reads_nothing_when_handed_the_record(self):
        ops = _Epic(live_description(), seven_then_two())
        report = mid_epic.refresh_epic_growth(ops, EPIC, issue=ops.record())
        assert ops.reads == []
        assert (report["green_lit"], report["current"]) == (7, 9)
        assert ops.writes == []

    def test_last_green_light_reads_nothing_when_handed_the_record(self):
        ops = _Epic(live_description(), seven_then_two())
        assert mid_epic.last_green_light(ops, EPIC, issue=ops.record()) == GREEN_LIGHT
        assert ops.reads == []

    def test_without_a_record_both_read_as_they_always_have(self):
        ops = _Epic(live_description(), seven_then_two())
        mid_epic.refresh_epic_growth(ops, EPIC)
        assert mid_epic.last_green_light(ops, EPIC) == GREEN_LIGHT
        assert len(ops.reads) == 2

    def test_a_first_page_that_is_the_last_is_the_count(self):
        page = {"nodes": [{"id": f"c{n}"} for n in range(40)],
                "pageInfo": {"hasNextPage": False, "endCursor": "c39"}}
        ops = _Epic(live_description(), seven_then_two(), uuid="uuid-3164",
                    comment_page=page)
        report = mid_epic.refresh_epic_growth(ops, EPIC, issue=ops.record())
        assert report["comments"] == 40
        assert ops.counted == [] and ops.reads == []

    def test_an_epic_past_its_first_page_is_still_counted_in_full(self):
        """An epic near the cap is the epic the warning exists for: its count
        is paged, never read off a first page that says there is more."""
        page = {"nodes": [{"id": f"c{n}"} for n in range(250)],
                "pageInfo": {"hasNextPage": True, "endCursor": "c249"}}
        ops = _Epic(live_description(), seven_then_two(), uuid="uuid-3164",
                    comment_page=page, total_comments=1850)
        report = mid_epic.refresh_epic_growth(ops, EPIC, issue=ops.record())
        assert report["comments"] == 1850
        assert ops.counted == ["uuid-3164"]


# ===========================================================================
# 5: the sweep's own path spends no write on a steady epic
# ===========================================================================
class TestTheSweepPath:
    def test_report_epic_growth_spends_only_its_reads_on_a_steady_epic(self):
        """Through the real `linear_ops` module, every request counted: the
        epic's read and its one comment page — no `get_issue`, no
        `issueUpdate`. Before this it was four requests an epic, every pass."""
        epic = _Epic(live_description(), seven_then_two(), uuid="uuid-3164")
        sent: list[str] = []

        def gql(query, variables=None):
            sent.append(query)
            if "issueUpdate" in query:
                return {"issueUpdate": {"success": True}}
            if "comments(filter" in query:
                return {"comments": {"nodes": [{"id": "c1"}],
                                     "pageInfo": {"hasNextPage": False,
                                                  "endCursor": "c1"}}}
            return {"issue": epic.record()}

        with patch.object(linear_ops, "gql", gql), \
                patch.object(linear_ops, "count_comments", return_value=1), \
                patch.object(linear_ops, "_card_memo", {}, create=True):
            reconcile.report_epic_growth({EPIC})

        assert not [q for q in sent if "issueUpdate" in q], "no description write"
        assert len(sent) == 2, sent
