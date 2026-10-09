"""RED-first: the epic growth record survives two writers (DRE-6162).

THE INCIDENT. On 2026-10-07 at about 09:07 PT the operator filed four mid-epic
additions under DRE-6059 — DRE-6157, DRE-6159, DRE-6160, DRE-6161. Reconcile
run 37649266193 read DRE-6059 between two of those filings, and then wrote
back the growth block it had built from that read. The read was already
stale: it carried neither DRE-6159's record nor DRE-6160's, so the write
erased both, and the same stale read posted two false
`🚨 mid-epic-unrecorded` alarms. The operator re-recorded both by hand after
run 37649416124. Unattended, the record would have stayed wrong.

THE CAUSE. `mid_epic.refresh_epic_growth` read the epic's description, rebuilt
the block, and wrote the WHOLE description back with no check that it was
still the description it read. Reconcile hands it a record read at the top of
the pass, so its window between read and write is most of a sweep.

WHAT IS UNDER TEST:
  * The write is guarded: just before writing, the epic is read again, and
    the block is written only if the description is still the one it was
    built from. If it moved, the block is rebuilt from the fresh read — up to
    `GROWTH_WRITE_ATTEMPTS` (3) times.
  * Both writers' entries survive, through `mid_epic` directly and through
    the sweep (`reconcile.report_epic_growth`), which reaches the block only
    through that one function.
  * After three moved reads it writes NOTHING and posts ONE `🚨` comment on
    the epic naming the card it could not record — and none of the notices a
    read it knows is stale would have posted.

Run: cd bureau-pipeline && python3 -m pytest tests/test_mid_epic_growth_two_writers.py -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import linear_ops  # noqa: E402
import mid_epic  # noqa: E402
import reconcile  # noqa: E402

EPIC = "DRE-6059"
GREEN_LIGHT = "2026-10-01T18:00:00.000Z"
BEFORE = "2026-10-01T17:00:00.000Z"
AFTER = "2026-10-07T16:00:00.000Z"

PLANNED = [(f"DRE-60{n}", BEFORE) for n in range(60, 66)]
FILED = ["DRE-6157", "DRE-6159", "DRE-6160", "DRE-6161"]


def roster(*filed):
    return PLANNED + [(ident, AFTER) for ident in filed]


def recorded(description: str) -> list[str]:
    return [a["id"] for a in mid_epic.parse_artifact(description)["additions"]]


def because(ident: str) -> str:
    return f"another call site found while building ({ident})"


class _Epic:
    """The `linear_ops` MODULE for one epic, with a SECOND WRITER.

    `gql` answers `mid_epic._EPIC_QUERY`. `between` is the second writer: a
    list of callables, and each read pops the next one and runs it AFTER the
    epic's record has been taken and BEFORE it is returned — so the reader
    holds a record that the second writer has already made stale, which is
    exactly the window reconcile run 37649266193 read DRE-6059 in. The second
    writer's own reads run with nothing queued, so it never interleaves with
    itself.
    """

    def __init__(self, description, children):
        self.description = description
        self.children = list(children)
        self.between: list = []
        self.reads = 0
        self.writes: list[str] = []
        self.comments: list[tuple[str, str]] = []
        self.LinearError = linear_ops.LinearError

    def record(self) -> dict:
        return {
            "identifier": EPIC,
            "description": self.description,
            "state": {"name": "In Progress"},
            "children": {"nodes": [
                {"identifier": i, "createdAt": at} for i, at in self.children
            ]},
            "history": {"nodes": [
                {"createdAt": GREEN_LIGHT, "toState": {"name": "In Progress"}}
            ]},
        }

    def gql(self, query, variables=None):
        self.reads += 1
        taken = self.record()
        if self.between:
            writer = self.between.pop(0)
            if writer is not None:
                held, self.between = self.between, []
                writer()
                self.between = held
        return {"issue": taken}

    def comment_count(self, issue_id):
        return 12

    def count_comments(self, identifier, needle, **kw):
        return sum(1 for i, b in self.comments if i == identifier and needle in b)

    def set_description(self, identifier, body):
        self.writes.append(body)
        self.description = body

    def cmd_comment(self, identifier, body):
        self.comments.append((identifier, body))


def with_6157_recorded() -> str:
    """DRE-6059 after the first of the four filings: DRE-6157 recorded."""
    ops = _Epic("The epic's plan.", roster("DRE-6157"))
    mid_epic.refresh_epic_growth(
        ops, EPIC, add={"id": "DRE-6157", "because": because("DRE-6157")}
    )
    return ops.description


def discovery_files(ops: _Epic, ident: str):
    """The second writer: `mid_epic discovery` recording one addition — the
    card already exists (`cmd_subissue` ran), its growth record is written."""
    def write():
        if not any(i == ident for i, _ in ops.children):
            ops.children.append((ident, AFTER))
        mid_epic.refresh_epic_growth(ops, EPIC, add={"id": ident, "because": because(ident)})
    return write


def alarms(ops: _Epic) -> list[str]:
    return [b for _, b in ops.comments if mid_epic.UNRECORDED_TAG in b]


# ===========================================================================
# 1: the incident, replayed — a record read before two filings is not written
# ===========================================================================
class TestTheIncident:
    def test_a_sweep_holding_a_stale_record_erases_neither_filing(self):
        """Reconcile read DRE-6059 at the top of its pass. Before it reached
        the growth phase, discovery filed DRE-6159 and DRE-6160 and recorded
        both. The sweep's write must not take them back out."""
        ops = _Epic(with_6157_recorded(), roster("DRE-6157", "DRE-6159"))
        # The pass's epic record, read early: DRE-6159 exists and is not yet
        # recorded, DRE-6160 does not exist yet.
        sweep_record = ops.record()
        discovery_files(ops, "DRE-6159")()
        discovery_files(ops, "DRE-6160")()
        assert recorded(ops.description) == ["DRE-6157", "DRE-6159", "DRE-6160"]

        mid_epic.refresh_epic_growth(ops, EPIC, issue=sweep_record)

        assert recorded(ops.description) == ["DRE-6157", "DRE-6159", "DRE-6160"], (
            "the sweep wrote a block built from a read taken before DRE-6159 "
            "and DRE-6160 were recorded, erasing both — run 37649266193"
        )
        assert alarms(ops) == [], (
            "both cards are recorded on the epic; a stale read is not evidence "
            "that they are not"
        )

    def test_a_discovery_whose_read_goes_stale_keeps_the_other_writers_entry(self):
        """Writer A (discovery for DRE-6161) reads the epic; writer B records
        DRE-6160 before A writes. BOTH entries end up in the description."""
        ops = _Epic(with_6157_recorded(), roster("DRE-6157", "DRE-6161"))
        ops.between = [discovery_files(ops, "DRE-6160")]

        mid_epic.refresh_epic_growth(
            ops, EPIC, add={"id": "DRE-6161", "because": because("DRE-6161")}
        )

        assert set(recorded(ops.description)) == {"DRE-6157", "DRE-6160", "DRE-6161"}

    def test_the_guarded_write_retries_rather_than_overwriting(self):
        """A's first build is discarded unwritten: the only writes are B's
        and A's rebuilt one, and A's rebuilt one carries B's entry."""
        ops = _Epic(with_6157_recorded(), roster("DRE-6157", "DRE-6161"))
        ops.between = [discovery_files(ops, "DRE-6160")]

        mid_epic.refresh_epic_growth(
            ops, EPIC, add={"id": "DRE-6161", "because": because("DRE-6161")}
        )

        assert len(ops.writes) == 2, ops.writes
        first, last = ops.writes
        # B's write also names DRE-6161, which already joined, as unrecorded
        # (DRE-6414); A's rebuilt write replaces that with its own record.
        assert recorded(first) == ["DRE-6157", "DRE-6160", "DRE-6161"], "B's write"
        assert set(recorded(last)) == {"DRE-6157", "DRE-6160", "DRE-6161"}, (
            "A wrote from a fresh read, not over B"
        )
        additions = mid_epic.parse_artifact(last)["additions"]
        assert [a["because"] for a in additions if a["id"] == "DRE-6161"] == [
            because("DRE-6161")], "the filer's record, not the unrecorded line"


# ===========================================================================
# 2: the guard — re-read before writing, rebuild on a moved description
# ===========================================================================
class TestTheGuard:
    def test_three_attempts(self):
        assert mid_epic.GROWTH_WRITE_ATTEMPTS == 3

    def test_an_uncontended_write_re_reads_once_and_writes_once(self):
        ops = _Epic(with_6157_recorded(), roster("DRE-6157", "DRE-6159"))
        mid_epic.refresh_epic_growth(
            ops, EPIC, add={"id": "DRE-6159", "because": because("DRE-6159")}
        )
        assert ops.reads == 2, "the read it builds from, and the read it checks"
        assert len(ops.writes) == 1
        assert recorded(ops.description) == ["DRE-6157", "DRE-6159"]

    def test_a_steady_epic_handed_its_record_still_reads_nothing(self):
        """No write is owed, so no write is guarded: the convergence DRE-3643
        bought is not spent on a check nobody needs."""
        ops = _Epic(with_6157_recorded(), roster("DRE-6157"))
        mid_epic.refresh_epic_growth(ops, EPIC, issue=ops.record())
        assert ops.reads == 0 and ops.writes == []

    def test_contended_twice_then_quiet_writes_on_the_third_attempt(self):
        ops = _Epic(with_6157_recorded(), roster("DRE-6157", "DRE-6161"))
        ops.between = [discovery_files(ops, "DRE-6159"), discovery_files(ops, "DRE-6160")]

        report = mid_epic.refresh_epic_growth(
            ops, EPIC, add={"id": "DRE-6161", "because": because("DRE-6161")}
        )

        assert set(recorded(ops.description)) == set(FILED)
        assert report["contended"] is None
        assert not [b for _, b in ops.comments if mid_epic.GROWTH_CONTENDED_TAG in b]

    def test_a_rebuilt_record_that_needs_no_write_writes_nothing(self):
        """The other writer already recorded exactly what this refresh would
        have: the fresh read converges, and nothing more is written."""
        ops = _Epic(with_6157_recorded(), roster("DRE-6157", "DRE-6159"))
        ops.between = [discovery_files(ops, "DRE-6159")]
        mid_epic.refresh_epic_growth(ops, EPIC)
        assert len(ops.writes) == 1, "only the other writer's"
        assert recorded(ops.description) == ["DRE-6157", "DRE-6159"]


# ===========================================================================
# 3: three moved reads — give up loudly, write nothing
# ===========================================================================
def an_editor(ops: _Epic):
    """A writer that edits the epic's plan text on every read — contention
    that never lets up. It edits Linear's copy directly, so every
    `set_description` the test sees is the refresh's own."""
    def write():
        ops.edits += 1
        plan, sep, rest = ops.description.partition("\n")
        ops.description = f"The epic's plan, edit {ops.edits}.{sep}{rest}"
    return write


def contended(ops: _Epic) -> _Epic:
    ops.edits = 0
    ops.between = [an_editor(ops) for _ in range(10)]
    return ops


class TestGivingUp:
    def test_it_writes_nothing_after_three_moved_reads(self):
        ops = contended(_Epic(with_6157_recorded(), roster("DRE-6157", "DRE-6161")))

        report = mid_epic.refresh_epic_growth(
            ops, EPIC, add={"id": "DRE-6161", "because": because("DRE-6161")}
        )

        assert ops.writes == [], "a block built from a stale read was written"
        assert "DRE-6161" not in recorded(ops.description)
        assert report["contended"]
        assert ops.reads == 1 + mid_epic.GROWTH_WRITE_ATTEMPTS

    def test_it_posts_one_alarm_naming_the_card_it_could_not_record(self):
        ops = contended(_Epic(with_6157_recorded(), roster("DRE-6157", "DRE-6161")))

        mid_epic.refresh_epic_growth(
            ops, EPIC, add={"id": "DRE-6161", "because": because("DRE-6161")}
        )

        posted = [(i, b) for i, b in ops.comments if mid_epic.GROWTH_CONTENDED_TAG in b]
        assert len(posted) == 1, ops.comments
        ident, body = posted[0]
        assert ident == EPIC
        assert body.startswith("🚨")
        assert "DRE-6161" in body
        assert because("DRE-6161") in body, "the line a person adds by hand"

    def test_it_posts_no_notice_off_a_read_it_knows_is_stale(self):
        """DRE-6159 is a child with no record in the read it gave up on. That
        read is stale by definition — the false alarms of run 37649266193
        came from exactly such a read — so it is not reported unrecorded."""
        ops = contended(
            _Epic(with_6157_recorded(), roster("DRE-6157", "DRE-6159", "DRE-6161"))
        )

        report = mid_epic.refresh_epic_growth(
            ops, EPIC, add={"id": "DRE-6161", "because": because("DRE-6161")}
        )

        assert alarms(ops) == []
        assert report["unrecorded"] == []
        assert len(ops.comments) == 1

    def test_an_amendment_it_could_not_record_is_named(self):
        ops = contended(_Epic(with_6157_recorded(), roster("DRE-6157")))

        mid_epic.refresh_epic_growth(
            ops, EPIC,
            amend={"at": AFTER, "because": "the plan no longer holds",
                   "re_green_lit": None},
        )

        posted = [b for _, b in ops.comments if mid_epic.GROWTH_CONTENDED_TAG in b]
        assert len(posted) == 1
        assert "the plan no longer holds" in posted[0]

    def test_a_plain_refresh_that_gives_up_loses_nothing_and_alarms_nobody(self, capsys):
        """The sweep's refresh records no card: everything in its block is
        re-derived from Linear on the next pass. It says so in the sweep log
        rather than raising a 🚨 over a record nobody lost."""
        ops = contended(_Epic(with_6157_recorded(), roster("DRE-6157", "DRE-6159")))

        report = mid_epic.refresh_epic_growth(ops, EPIC)

        assert ops.writes == []
        assert ops.comments == []
        assert report["contended"]
        assert mid_epic.GROWTH_CONTENDED_TAG in capsys.readouterr().out


class _CappedEpic(_Epic):
    """The same epic at Linear's comment cap, reached through `discovery`: it
    refuses every comment the way the real `cmd_comment` does (DRE-3343) —
    returning the condition rather than raising."""

    def cmd_subissue(self, parent, title, body, *flags):
        self.children.append(("DRE-6161", AFTER))
        return {"identifier": "DRE-6161"}

    def cmd_comment(self, identifier, body):
        if identifier == EPIC:
            return linear_ops.COMMENT_CAP_CONDITION
        self.comments.append((identifier, body))
        return None


class TestGivingUpThroughDiscovery:
    def test_a_capped_epic_s_contended_record_is_named_on_the_new_card(self, capsys):
        """The epic refused the one 🚨 naming the unwritten record, so the card
        that was just filed carries it — not the cap note, which would say the
        growth record WAS written."""
        ops = contended(_CappedEpic(with_6157_recorded(), roster("DRE-6157")))

        ident = mid_epic.discovery(
            ops, EPIC, kind=mid_epic.ADDITION, because=because("DRE-6161"),
            title="fix the fourth call site", body="- work",
        )

        assert ops.writes == []
        on_card = [b for i, b in ops.comments if i == ident]
        contended_notes = [b for b in on_card if mid_epic.GROWTH_CONTENDED_TAG in b]
        assert len(contended_notes) == 1
        assert because("DRE-6161") in contended_notes[0]
        assert not [b for b in on_card if mid_epic.GROWTH_CAPPED_TAG in b]
        assert "growth record NOT written" in capsys.readouterr().out


# ===========================================================================
# 4: the sweep reaches the block only through the guarded write
# ===========================================================================
class TestTheSweepPath:
    def test_report_epic_growth_does_not_write_its_stale_pass_record(self):
        """Through the real `linear_ops` module: the pass's batched epic record
        was read before DRE-6159 and DRE-6160 were recorded; Linear now holds
        both. Whatever the sweep writes carries both."""
        epic = _Epic(with_6157_recorded(), roster("DRE-6157", "DRE-6159"))
        stale = epic.record()
        discovery_files(epic, "DRE-6159")()
        discovery_files(epic, "DRE-6160")()
        sent: list[tuple[str, dict]] = []

        def gql(query, variables=None):
            sent.append((query, variables or {}))
            if "issueUpdate" in query:
                epic.description = variables["input"]["description"]
                return {"issueUpdate": {"success": True}}
            if "comments(filter" in query:
                return {"comments": {"nodes": [], "pageInfo": {
                    "hasNextPage": False, "endCursor": None}}}
            if "$numbers" in query:
                return {"issues": {"nodes": [stale], "pageInfo": {
                    "hasNextPage": False, "endCursor": None}}}
            if "team { id }" in query:  # set_description's get_issue
                return {"issue": {"id": "uuid-6059", "identifier": EPIC}}
            return {"issue": epic.record()}

        reconcile.reset_sweep_cards()
        try:
            with patch.object(linear_ops, "gql", gql), \
                    patch.object(linear_ops, "count_comments", return_value=0), \
                    patch.object(linear_ops, "cmd_comment") as comment, \
                    patch.object(linear_ops, "_card_memo", {}, create=True):
                reconcile.report_epic_growth({EPIC})
        finally:
            reconcile.reset_sweep_cards()

        assert recorded(epic.description) == ["DRE-6157", "DRE-6159", "DRE-6160"]
        assert not [c for c in comment.call_args_list
                    if mid_epic.UNRECORDED_TAG in str(c)]
