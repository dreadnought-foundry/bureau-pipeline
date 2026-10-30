"""RED-first tests: a PROOF card can say when it can first be observed (DRE-8436).

On 2026-10-10 the pipeline dispatched three first proof runs at DRE-6678 on the
evening before the 06:00 PT groom proposal they had to observe. All nine rows
read "Not observed. waiting for the morning of October 11": the card had no way
to say "not before 06:00 tomorrow".

So a PROOF card may carry one line of its own, `Observe after: …`, in one of
five forms:

    Observe after: <YYYY-MM-DD> <HH:MM> PT
    Observe after: <event> — next <HH:MM> PT after release
    Observe after: <event> — next <Weekday> <HH:MM> PT after release
    Observe after: <event> — <N> days after release
    Observe after: <event>

WHAT THIS PINS, one section per acceptance criterion:

  1. `observe_after` reads each of the five forms, reads nothing from a card
     with no line or with the line only inside fenced code, and refuses a line
     that fits no form, or a second line.
  2. `observable_at` turns the line and the release time into the earliest
     time the proof may run, in UTC, Pacific daylight saving included.
  3. `proof_and_demo.py check` passes a PROOF card with a well-formed line and
     names the PROOF card and quotes the line when it is malformed. Only the
     PROOF card's line is read.
  4. The card standard and the planner brief document the line.

Nothing here reads the time for dispatch: the proof dispatcher's use of it is
the sibling dispatch-gate card, which imports the reader this card adds.

Run: cd bureau-pipeline && python3 -m pytest tests/test_proof_observe_after.py -v
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import proof_and_demo  # noqa: E402
from proof_and_demo import (  # noqa: E402
    OBSERVE_AFTER,
    ObserveAfter,
    ObserveAfterMalformed,
    observable_at,
    observe_after,
)

PT = ZoneInfo("America/Los_Angeles")
EVENT = "the first groom proposal after release"

# The evening DRE-6678's three proof runs were dispatched on.
RELEASED = datetime(2026, 10, 10, 19, 0, tzinfo=PT)


def _body(*lines: str) -> str:
    return "\n".join((
        "Record what the groom proposal showed.",
        "",
        *lines,
        "",
        "## Acceptance criteria",
        "",
        "- [ ] the proposal is observed naming the card",
    ))


def _at(y, mo, d, h, mi) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=PT)


# ===========================================================================
# 1: the reader
# ===========================================================================
class TestTheContract:
    def test_the_opening_is_the_module_constant(self):
        assert OBSERVE_AFTER == "Observe after:"

    def test_the_record_has_the_four_contract_fields(self):
        assert ObserveAfter._fields == ("line", "event", "at", "rule")

    def test_malformed_is_a_value_error(self):
        assert issubclass(ObserveAfterMalformed, ValueError)


class TestTheFiveForms:
    def test_the_absolute_form(self):
        line = "Observe after: 2026-10-11 06:00 PT"
        found = observe_after(_body(line))
        assert found == ObserveAfter(
            line=line, event=None, at=_at(2026, 10, 11, 6, 0), rule="at")
        assert found.at.utcoffset() is not None, "`at` is an aware datetime"

    def test_the_next_time_form(self):
        line = f"Observe after: {EVENT} — next 06:00 PT after release"
        assert observe_after(_body(line)) == ObserveAfter(
            line=line, event=EVENT, at=None, rule="next")

    def test_the_next_weekday_form(self):
        line = f"Observe after: {EVENT} — next Sunday 06:30 PT after release"
        assert observe_after(_body(line)) == ObserveAfter(
            line=line, event=EVENT, at=None, rule="next")

    def test_the_days_form(self):
        line = f"Observe after: {EVENT} — 7 days after release"
        assert observe_after(_body(line)) == ObserveAfter(
            line=line, event=EVENT, at=None, rule="days")

    def test_the_bare_event_form(self):
        line = f"Observe after: {EVENT}"
        assert observe_after(_body(line)) == ObserveAfter(
            line=line, event=EVENT, at=None, rule="event")


class TestWhereTheLineIsRead:
    @pytest.mark.parametrize("dash", ["—", "-", "--"])
    def test_the_em_dash_may_be_written_as_a_hyphen(self, dash):
        line = f"Observe after: {EVENT} {dash} next 06:00 PT after release"
        found = observe_after(_body(line))
        assert found.rule == "next" and found.event == EVENT

    @pytest.mark.parametrize("line", [
        f"- Observe after: {EVENT} — 7 days after release",
        f"* Observe after: {EVENT} — 7 days after release",
        f"1. Observe after: {EVENT} — 7 days after release",
        f"**Observe after:** {EVENT} — 7 days after release",
        f"- **Observe after:** {EVENT} — 7 days after release",
        f"**Observe after**: {EVENT} — 7 days after release",
    ])
    def test_inside_a_list_item_or_bold_markup(self, line):
        found = observe_after(_body(line))
        assert found == ObserveAfter(
            line=line, event=EVENT, at=None, rule="days")

    def test_the_line_is_kept_as_written_stripped(self):
        line = f"Observe after: {EVENT} — 7 days after release"
        found = observe_after(_body(f"   {line}   "))
        assert found.line == line

    def test_no_line_is_none(self):
        assert observe_after(_body()) is None
        assert observe_after("") is None
        assert observe_after(None) is None

    def test_a_line_only_inside_fenced_code_is_none(self):
        body = _body(
            "```",
            "Observe after: 2026-10-11 06:00 PT",
            "```",
        )
        assert observe_after(body) is None

    def test_a_malformed_line_inside_fenced_code_is_not_read(self):
        body = _body("~~~", "Observe after: soon", "~~~")
        assert observe_after(body) is None

    def test_a_line_after_a_closed_fence_is_read(self):
        body = _body(
            "```",
            "Observe after: soon",
            "```",
            "Observe after: 2026-10-11 06:00 PT",
        )
        assert observe_after(body).rule == "at"

    def test_a_sentence_that_mentions_the_words_is_not_the_line(self):
        body = _body(
            "We cannot observe after: the groom runs only at six in the morning.")
        assert observe_after(body) is None


class TestAMalformedLine:
    def test_a_vague_time_is_refused(self):
        with pytest.raises(ObserveAfterMalformed) as caught:
            observe_after(_body("Observe after: soon"))
        assert "Observe after: soon" in str(caught.value)

    def test_an_impossible_date_is_refused(self):
        line = "Observe after: 2026-13-40 06:00 PT"
        with pytest.raises(ObserveAfterMalformed) as caught:
            observe_after(_body(line))
        assert line in str(caught.value)

    def test_two_lines_are_refused_naming_both(self):
        first = "Observe after: 2026-10-11 06:00 PT"
        second = f"Observe after: {EVENT} — 7 days after release"
        with pytest.raises(ObserveAfterMalformed) as caught:
            observe_after(_body(first, second))
        assert first in str(caught.value)
        assert second in str(caught.value)

    @pytest.mark.parametrize("line", [
        "Observe after:",
        "Observe after: 2026-10-11 06:00",
        "Observe after: 2026-10-11 25:00 PT",
        "Observe after: tomorrow 06:00 PT",
        f"Observe after: {EVENT} — next week",
        f"Observe after: {EVENT} — next Someday 06:30 PT after release",
        f"Observe after: {EVENT} — next 06:61 PT after release",
        f"Observe after: {EVENT} — 0 days after release",
        f"Observe after: {EVENT} — seven days after release",
        "Observe after: — next 06:00 PT after release",
    ])
    def test_a_line_that_fits_no_form_is_refused(self, line):
        with pytest.raises(ObserveAfterMalformed) as caught:
            observe_after(_body(line))
        assert line.strip() in str(caught.value)

    def test_the_refusal_carries_the_line(self):
        line = "Observe after: soon"
        with pytest.raises(ObserveAfterMalformed) as caught:
            observe_after(_body(f"- {line}"))
        assert caught.value.line == f"- {line}"


# ===========================================================================
# 2: the earliest time the proof may run
# ===========================================================================
class TestObservableAt:
    def _when(self, line, released=RELEASED):
        return observable_at(observe_after(_body(line)), released)

    def test_next_time_after_release(self):
        when = self._when(f"Observe after: {EVENT} — next 06:00 PT after release")
        assert when == _at(2026, 10, 11, 6, 0)

    def test_next_weekday_is_the_first_one_strictly_after_release(self):
        """Released Saturday 2026-10-10 19:00 PT, so the first Sunday 06:30 PT
        strictly after it is the next morning, 2026-10-11 — the contract's
        rule. A release on that Sunday after 06:30 waits a full week."""
        line = f"Observe after: {EVENT} — next Sunday 06:30 PT after release"
        assert self._when(line) == _at(2026, 10, 11, 6, 30)
        assert self._when(line, _at(2026, 10, 11, 7, 0)) == _at(2026, 10, 18, 6, 30)

    def test_next_weekday_is_strictly_after_not_at(self):
        line = f"Observe after: {EVENT} — next Sunday 06:30 PT after release"
        assert self._when(line, _at(2026, 10, 11, 6, 30)) == _at(2026, 10, 18, 6, 30)

    def test_next_time_is_strictly_after_not_at(self):
        line = f"Observe after: {EVENT} — next 06:00 PT after release"
        assert self._when(line, _at(2026, 10, 11, 6, 0)) == _at(2026, 10, 12, 6, 0)

    def test_days_after_release(self):
        when = self._when(f"Observe after: {EVENT} — 7 days after release")
        assert when == _at(2026, 10, 17, 19, 0)

    def test_a_bare_event_is_twelve_hours_after_release(self):
        assert self._when(f"Observe after: {EVENT}") == _at(2026, 10, 11, 7, 0)

    def test_the_bare_event_hours_are_the_dispatchers_recheck_hours(self):
        import proof_dispatch

        assert (proof_and_demo.BARE_EVENT_HOURS
                == proof_dispatch.WAITING_RECHECK_HOURS == 12)

    def test_the_absolute_form_is_the_stated_time(self):
        assert self._when("Observe after: 2026-10-12 06:00 PT") == _at(2026, 10, 12, 6, 0)

    def test_the_answer_is_in_utc(self):
        for line in (
            "Observe after: 2026-10-12 06:00 PT",
            f"Observe after: {EVENT} — next 06:00 PT after release",
            f"Observe after: {EVENT} — 7 days after release",
            f"Observe after: {EVENT}",
        ):
            assert self._when(line).utcoffset() == timedelta(0), line

    def test_a_utc_release_time_reads_the_same(self):
        line = f"Observe after: {EVENT} — next 06:00 PT after release"
        assert (self._when(line, RELEASED.astimezone(timezone.utc))
                == _at(2026, 10, 11, 6, 0))

    def test_across_the_daylight_saving_change_on_2026_11_01(self):
        """06:00 PT on 2026-10-31 is 13:00 UTC (PDT); on 2026-11-01 it is
        14:00 UTC (PST). A release the evening before the change waits for
        the Pacific wall clock, not for a fixed offset."""
        line = f"Observe after: {EVENT} — next 06:00 PT after release"
        when = self._when(line, _at(2026, 10, 31, 19, 0))
        assert when == datetime(2026, 11, 1, 14, 0, tzinfo=timezone.utc)
        assert observe_after(_body("Observe after: 2026-11-02 06:00 PT")).at == \
            datetime(2026, 11, 2, 14, 0, tzinfo=timezone.utc)


# ===========================================================================
# 3: the plan check
# ===========================================================================
WORK_IDS = ["DRE-9001", "DRE-9002"]
WORK_LABELS = ["repo:bureau-pipeline", "agent:engineer", "initiative:pipeline"]
PROOF_LABELS = ["repo:bureau-pipeline", "agent:ops", "initiative:pipeline"]


def _plan(proof_body: str, work_body: str | None = None) -> list:
    work_body = work_body or _body()
    children = [
        {"identifier": i, "title": f"Build piece {n}", "body": work_body,
         "labels": WORK_LABELS, "blocked_by": []}
        for n, i in enumerate(WORK_IDS, 1)
    ]
    children.append({
        "identifier": "DRE-9091", "title": "PROOF: the groom proposal names it",
        "body": proof_body, "labels": PROOF_LABELS, "blocked_by": WORK_IDS,
    })
    return children


class TestTheCheck:
    def test_a_proof_card_with_no_line_passes(self):
        assert proof_and_demo.findings(_plan(_body())) == []

    @pytest.mark.parametrize("line", [
        "Observe after: 2026-10-11 06:00 PT",
        f"Observe after: {EVENT} — next 06:00 PT after release",
        f"**Observe after:** {EVENT} — next Sunday 06:30 PT after release",
        f"Observe after: {EVENT} — 7 days after release",
        f"Observe after: {EVENT}",
    ])
    def test_a_well_formed_line_passes(self, line):
        assert proof_and_demo.findings(_plan(_body(line))) == []

    def test_a_malformed_line_names_the_proof_card_and_quotes_the_line(self):
        line = "Observe after: 2026-13-40 06:00 PT"
        found = proof_and_demo.findings(_plan(_body(line)))
        assert len(found) == 1, found
        assert "DRE-9091" in found[0]
        assert line in found[0]

    def test_two_lines_on_the_proof_card_are_a_finding(self):
        found = proof_and_demo.findings(_plan(_body(
            "Observe after: 2026-10-11 06:00 PT", f"Observe after: {EVENT}")))
        assert len(found) == 1 and "DRE-9091" in found[0]

    def test_a_non_proof_cards_line_is_ignored(self):
        work = _body("Observe after: soon")
        assert proof_and_demo.findings(_plan(_body(), work_body=work)) == []

    def test_a_malformed_line_bounces_the_epic_with_the_line_named(self):
        line = "Observe after: soon"
        found = proof_and_demo.findings(_plan(_body(line)))
        comment = proof_and_demo.bounce_comment("DRE-9000", found)
        assert line in comment

    def _run(self, children):
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "proof_and_demo.py"), "check",
             "--epic", "DRE-9000", "--no-stamp"],
            input=json.dumps(children), capture_output=True, text=True,
        )

    def test_the_cli_passes_a_well_formed_line(self):
        r = self._run(_plan(_body(f"Observe after: {EVENT} — 7 days after release")))
        assert r.returncode == 0, r.stdout + r.stderr

    def test_the_cli_reports_a_malformed_line(self):
        line = "Observe after: soon"
        r = self._run(_plan(_body(line)))
        assert r.returncode == 1, r.stdout + r.stderr
        assert "DRE-9091" in r.stdout and line in r.stdout


# ===========================================================================
# 4: the documents
# ===========================================================================
def _section(path: Path, heading: str) -> str:
    text = path.read_text(encoding="utf-8")
    start = text.index(heading)
    after = text.find("\n## ", start + len(heading))
    return text[start:after if after != -1 else len(text)]


PROOF_HEADING = "## Every epic ends with a proof card"


class TestTheDocuments:
    def test_the_card_standard_documents_the_line(self):
        section = _section(ROOT / "standards" / "card-quality.md", PROOF_HEADING)
        assert OBSERVE_AFTER in section
        for form in (
            "Observe after: <YYYY-MM-DD> <HH:MM> PT",
            "Observe after: <event> — next <HH:MM> PT after release",
            "Observe after: <event> — next <Weekday> <HH:MM> PT after release",
            "Observe after: <event> — <N> days after release",
            "Observe after: <event>",
        ):
            assert form in section, form
        assert "America/Los_Angeles" in section
        assert re.search(r"Pacific time", section)
        assert re.search(r"no `?Observe after:`? line[^.]*dispatched as before",
                         section, re.IGNORECASE | re.DOTALL)

    def test_the_planner_brief_tells_the_planner_to_write_it(self):
        section = _section(ROOT / "briefs" / "planner.md", PROOF_HEADING)
        assert OBSERVE_AFTER in section
        examples = [
            line.strip() for line in section.splitlines()
            if line.strip().startswith(OBSERVE_AFTER)
        ]
        assert examples, "the brief carries one example line"
        for example in examples:
            assert observe_after(example) is not None, example
