"""RED-first tests: every act in the registry declares its cadence (DRE-3298).

The One River row's pulse and its journey-bar sweep are both switched on by ONE
derived verdict, `deriveLiveness` (DRE-2852): a recent act, INSIDE THAT ACT'S
DECLARED CADENCE. The verdict has two inputs and the pipeline supplies neither
yet. This is the first — the cadence — and without it the console cannot tell a
card that is working from a card that died forty minutes ago, so every row reads
`unknown` and nothing on the river moves.

Two new fields per act, and nothing else:

  * `cadence_s` — the longest silence, in seconds, after which this act's work
    should be read as stuck. A positive integer, or `null` when nothing is
    expected to follow. `null` is a DECLARATION, not an omission: the console
    renders it as "parked", never as "overdue".
  * `cadence_why` — one sentence saying where the number comes from. A number
    nobody can explain is a guess wearing a data type, so every one of them is
    read off something that already runs in this repo and the test below proves
    the sentence names a file that exists.

THE RULE IS MECHANICAL, and it is pinned here rather than remembered: an act
whose declared `state` is `dispatched` has a run coming back and takes that
run's own job timeout; every other act hands the work to a person, and a person
has no declared bound, so it declares `null`. Nothing is invented and nothing is
defaulted.

WHAT MUST NOT CHANGE. The trailer grammar carries no cadence and receipt bodies
are byte-identical — the DRE-2825 warning stands, and the tests here pin it from
this side too: the tags are live idempotency keys and per-sha budget counters,
so a receipt whose body moved by one byte is a budget that re-arms from zero.

Run: cd bureau-pipeline && python3 -m pytest tests/test_act_cadence.py -v
"""
from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import pipeline_act  # noqa: E402

# A repo-relative path as a sentence would spell one. Deliberately narrow: the
# point is to catch a `cadence_why` that gestures at "the workflow timeout"
# without naming which, so a reader can go and check the number themselves.
# The leading dot is admitted on purpose — every workflow this repo declares
# lives under `.github/`, and a class that excluded it would silently match
# `github/workflows/qa-review.yml`, which is not a path that exists.
_PATH = re.compile(r"[A-Za-z0-9_.][A-Za-z0-9_./-]*\.(?:yml|yaml|py|json|md)")

# The state that means a run was dispatched and is expected to report back —
# the one state that reads its bound off a workflow's own job timeout.
_DISPATCHED = "dispatched"

# The kind added by DRE-3389: an act that says the ordinary work is still
# moving. It leaves the work `unchanged` and still declares a number, because
# something IS expected to speak next — the number is measured rather than read
# off a timeout.
_PROGRESS = "progress"

# The progress acts that expect NOTHING to follow them (DRE-3521). A progress
# act normally declares a number because ordinary work is still moving and will
# speak again; `live` is the end of a release journey — the tag is cut, the
# deployment record is written, and there is no next stage. So it declares
# `null`, which is the same "nothing is expected to speak" every other null in
# this file means, and the console renders it as complete rather than overdue.
# Named, not inferred: a second progress act quietly going null is exactly what
# this list has to fail on.
_TERMINAL_PROGRESS = frozenset({"release-live"})


def _doc() -> dict:
    return copy.deepcopy(pipeline_act.load())


def _first(doc: dict) -> dict:
    return doc["acts"][0]


# --------------------------------------------------------------------------- #
# 1. every act carries the two fields                                          #
# --------------------------------------------------------------------------- #


class TestEveryActDeclaresItsCadence:
    def test_the_registry_still_declares_thirty_acts(self):
        """The card counts them. If an act is added, it declares a cadence with
        the rest of its row or this goes red — which is the whole point of the
        field being data rather than a default.

        Nineteen when DRE-3298 wrote this; twenty-two since DRE-3389 added the
        three LIFECYCLE acts under the `progress` kind; twenty-eight since
        DRE-3521 added the six RELEASE acts under the same kind, one per stage
        of a train run; twenty-nine since DRE-3428 added the
        runner-environment hold; thirty since DRE-3433 added the fleet-wide
        reviewer outage."""
        assert len(pipeline_act.acts()) == 30

    def test_every_act_carries_a_cadence_and_a_reason(self):
        for name in pipeline_act.acts():
            entry = pipeline_act.record(name)
            assert "cadence_s" in entry, f"{name}: absent is null, never missing"
            assert "cadence_why" in entry, f"{name}: a number nobody can explain"
            value = entry["cadence_s"]
            assert value is None or (
                isinstance(value, int) and not isinstance(value, bool) and value > 0
            ), f"{name}: cadence_s is a positive integer or null, got {value!r}"
            assert (entry["cadence_why"] or "").strip(), (
                f"{name}: a cadence with no reason is a guess wearing a data type"
            )

    def test_every_cadence_why_names_a_file_that_exists(self):
        """Traceable, not plausible. Each sentence names the file the number was
        read off — a workflow's job timeout, a sweep's cron, the script that
        declines to act — and that file is in this repo."""
        for name in pipeline_act.acts():
            why = pipeline_act.record(name)["cadence_why"]
            named = [p for p in _PATH.findall(why) if (ROOT / p).exists()]
            assert named, (
                f"{name}: {why!r} names no file or workflow that exists in the "
                "repo, so nobody can check where the number came from"
            )

    def test_a_dispatched_act_declares_a_number_and_every_other_declares_null(self):
        """The mechanical rule, pinned. A `dispatched` act has a run coming back
        and takes that run's own bound; every other act has handed the work to a
        person, and no workflow declares how long a person takes.

        A `progress` act (DRE-3389) is the one exception, and it is an exception
        to the REASON rather than to the rule: it announces that ordinary work
        is still moving, so something IS expected to speak next — the next
        heartbeat, the next review run, the next gate wake. Its bound is not a
        job timeout, because nothing times a healthy build; it is measured, and
        `test_the_numbers_are_the_ones_the_workflows_declare` below is where the
        measurement has to show itself.

        And the exception has its own exception (DRE-3521): a progress act at
        the END of a journey has no next stage to expect, so `release-live`
        declares `null` like every other act nothing is coming back to."""
        for name in pipeline_act.acts():
            entry = pipeline_act.record(name)
            if name in _TERMINAL_PROGRESS:
                assert entry["kind"] == _PROGRESS, (
                    f"{name} is listed as a terminal progress act and is not a "
                    "progress act at all"
                )
                assert entry["cadence_s"] is None, (
                    f"{name} is the last stage of its journey — nothing speaks "
                    "after it, so the console must read it as complete, not "
                    "overdue"
                )
            elif entry["state"] == _DISPATCHED or entry["kind"] == _PROGRESS:
                assert isinstance(entry["cadence_s"], int), (
                    f"{name} expects something to speak next — a dispatched run "
                    "reads as stuck past its own job timeout, and ordinary work "
                    "past the silence that was measured for it"
                )
            else:
                assert entry["cadence_s"] is None, (
                    f"{name} leaves the work {entry['state']!r} with "
                    f"{entry['next_actor']!r} next — nothing is expected to "
                    "speak, so the console must read it as parked, not overdue"
                )

    def test_the_numbers_are_the_ones_the_workflows_declare(self):
        """Read off the source, never invented. `timeout-minutes: 65` on
        qa-review's review job and `timeout-minutes: 120` on agent-fix's fix job
        are the two bounds the dispatching acts in this registry wait on.

        A `progress` act has no such bound to read — no workflow declares how
        long a healthy build may go quiet — so its number is MEASURED instead,
        and the row owes the measurement in its `why`: how many runs, when, and
        the longest green gap the sample held. Either way nothing is invented:
        the number is read off something, and the row says off what.

        A progress act this repo did not choose at all (DRE-3521's six release
        acts) owes the same thing one step removed: the console declared the
        number first and the row names the card that did it, so a reader can
        follow it to the source the console cites."""
        bounds = {
            ".github/workflows/qa-review.yml": 65 * 60,
            ".github/workflows/agent-fix.yml": 120 * 60,
        }
        for workflow, seconds in bounds.items():
            text = (ROOT / workflow).read_text(encoding="utf-8")
            assert f"timeout-minutes: {seconds // 60}" in text, (
                f"{workflow} no longer declares timeout-minutes: {seconds // 60}"
                " — the cadences read off it move with it"
            )
        for name in pipeline_act.acts():
            entry = pipeline_act.record(name)
            if entry["cadence_s"] is None:
                continue
            source = [p for p in _PATH.findall(entry["cadence_why"]) if p in bounds]
            if source:
                assert entry["cadence_s"] == bounds[source[0]], (
                    f"{name}: declares {entry['cadence_s']}s but {source[0]} "
                    f"declares {bounds[source[0]]}s"
                )
                continue
            assert entry["kind"] == _PROGRESS, (
                f"{name}: its reason names no workflow carrying a bound, and only "
                "a measured progress act is allowed to have none"
            )
            assert "DRE-3388" in entry["why"] or "DRE-3518" in entry["why"], (
                f"{name}: a progress cadence that names no bounded workflow "
                "owes its source in `why` — the measurement that produced it "
                "(DRE-3388), or the console declaration it was copied from "
                "(DRE-3518)"
            )


# --------------------------------------------------------------------------- #
# 2. the check refuses a cadence it cannot trust                               #
# --------------------------------------------------------------------------- #


class TestCheckRefusesABadCadence:
    def test_the_shipped_file_is_clean(self):
        assert pipeline_act.problems() == []

    def test_a_missing_cadence_fails(self):
        doc = _doc()
        del _first(doc)["cadence_s"]
        assert any("cadence" in p for p in pipeline_act.problems(doc))

    def test_a_missing_reason_fails(self):
        doc = _doc()
        del _first(doc)["cadence_why"]
        assert any("cadence_why" in p for p in pipeline_act.problems(doc))

    @pytest.mark.parametrize("value", [0, -1, -3900])
    def test_a_non_positive_cadence_fails(self, value):
        doc = _doc()
        _first(doc)["cadence_s"] = value
        assert any("cadence_s" in p for p in pipeline_act.problems(doc)), (
            f"a cadence of {value} declares a silence that has already elapsed"
        )

    @pytest.mark.parametrize("value", [60.5, 3900.0, "3900", True, [3900], {}])
    def test_a_non_integer_cadence_fails(self, value):
        """`True` is in the list on purpose: `isinstance(True, int)` is True in
        Python, so a check that asks only "is it an int" reads a boolean as one
        second and the console reads every act as overdue."""
        doc = _doc()
        _first(doc)["cadence_s"] = value
        assert any("cadence_s" in p for p in pipeline_act.problems(doc)), (
            f"{value!r} is not a positive integer of seconds"
        )

    def test_a_null_cadence_with_an_empty_reason_fails(self):
        """`null` is a declaration, not an omission. Without the sentence there
        is no way to tell a deliberate park from a field somebody skipped."""
        doc = _doc()
        entry = next(a for a in doc["acts"] if a["cadence_s"] is None)
        entry["cadence_why"] = "   "
        assert any("cadence_why" in p for p in pipeline_act.problems(doc))

    def test_a_number_with_an_empty_reason_fails(self):
        doc = _doc()
        entry = next(a for a in doc["acts"] if a["cadence_s"] is not None)
        entry["cadence_why"] = ""
        assert any("cadence_why" in p for p in pipeline_act.problems(doc))

    def test_the_check_command_exits_one_on_a_bad_cadence(self, tmp_path, monkeypatch):
        """The acceptance criterion names the command, so the command is what is
        run — not only the function underneath it."""
        doc = _doc()
        _first(doc)["cadence_s"] = 0
        path = tmp_path / "pipeline-acts.json"
        path.write_text(json.dumps(doc), encoding="utf-8")
        monkeypatch.setattr(pipeline_act, "CONFIG_PATH", str(path))
        assert pipeline_act.main(["check"]) == 1

    def test_the_check_command_exits_zero_on_the_shipped_file(self):
        r = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "pipeline_act.py"), "check"],
            capture_output=True, text=True, cwd=str(ROOT), check=False,
        )
        assert r.returncode == 0, r.stdout + r.stderr


# --------------------------------------------------------------------------- #
# 3. the cadence rides with the rest of the row                                #
# --------------------------------------------------------------------------- #


class TestTheCadenceRidesWithTheRow:
    def test_the_accessors_return_what_the_row_declares(self):
        for name in pipeline_act.acts():
            entry = pipeline_act.record(name)
            assert pipeline_act.cadence_s(name) == entry["cadence_s"]
            assert pipeline_act.cadence_why(name) == entry["cadence_why"]

    def test_rows_carries_the_two_fields_with_the_rest_of_the_row(self):
        """The row-shaped read, for the console's mirror (DRE-3091): the cadence
        arrives on the same row as the tag, the kind and the state, off one
        parse. `acts()` keeps returning NAMES — a dozen call sites here and in
        `check_act_receipts.py` build sets and join strings out of it — so the
        row shape is its own function rather than a change of meaning under
        readers that would go on working and be wrong."""
        rows = pipeline_act.rows()
        assert [r["name"] for r in rows] == list(pipeline_act.acts())
        for row in rows:
            entry = pipeline_act.record(row["name"])
            assert row == entry
            assert "cadence_s" in row and "cadence_why" in row
            assert row["tag"] and row["kind"] and row["state"]

    def test_rows_hands_back_a_copy_nobody_can_corrupt(self):
        """The registry is cached per path and read per card by the sweep. A
        caller that mutated a row would be editing every later reader's copy."""
        pipeline_act.rows()[0]["cadence_s"] = 1
        assert pipeline_act.cadence_s(pipeline_act.acts()[0]) != 1

    def test_list_carries_the_two_fields_with_the_rest_of_the_row(self):
        """The console's mirror (DRE-3091) reads a row, not a second parser: the
        cadence arrives in the same row as the tag, the kind and the state."""
        r = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "pipeline_act.py"), "list"],
            capture_output=True, text=True, cwd=str(ROOT), check=False,
        )
        assert r.returncode == 0, r.stdout + r.stderr
        rows = json.loads(r.stdout)
        assert len(rows) == len(pipeline_act.acts())
        for row in rows:
            entry = pipeline_act.record(row["act"])
            assert row["cadence_s"] == entry["cadence_s"]
            assert row["cadence_why"] == entry["cadence_why"]
            # …with the rest of the row, in one read.
            assert row["tag"] == entry["tag"]
            assert row["kind"] == entry["kind"]
            assert row["state"] == entry["state"]


# --------------------------------------------------------------------------- #
# 4. the trailer grammar does not change (the DRE-2825 warning stands)         #
# --------------------------------------------------------------------------- #


class TestTheTrailerGrammarDoesNotChange:
    def test_no_trailer_carries_a_cadence(self):
        """The cadence is data the console reads off the registry, never a field
        on a receipt. Adding it to the trailer would change every receipt body
        this pipeline has ever posted."""
        for name in pipeline_act.acts():
            body = pipeline_act.trailer(name)
            assert "cadence" not in body
            fields = pipeline_act.read_trailer(body)
            assert set(fields) == {
                "act", "kind", "state", "next", "discharges", "subscriber", "tag",
            }

    def test_receipt_bodies_are_byte_identical(self):
        body = "  \U0001f6a8 leading space, inner  gap, no trailing newline  "
        for name in pipeline_act.acts():
            out = pipeline_act.receipt(name, body)
            trailer = pipeline_act.trailer(name)
            assert out == f"{body}\n\n{trailer}"
            assert len(out) == len(body) + 2 + len(trailer)
