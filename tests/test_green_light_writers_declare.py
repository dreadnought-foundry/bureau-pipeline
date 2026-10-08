"""Every decision-kind Green Light writer declares the three lines (DRE-3915, epic DRE-3893).

A decision-kind row in Green Light — the planner's business question, a
build's escalation to a person — carries the Finding, Question and
Recommendation lines `scripts/console_escalation.py` declares, with `none
given — …` where nothing was recommended. The writer cards (DRE-3909, DRE-5204,
DRE-3911, DRE-6174) each taught one writer the lines. This holds all of them,
and any later one, to it.

## Discovered, never listed

The writer set is `green_light_rows.green_light_writes()` — every write into
the lane the pipeline's own discovery finds, located at its unit in the
contract's grammar — and the kind of each is the one its `arrivals` record
declares. No count of writers is kept here. Each discovered unit whose kind is
a decision kind, and each discovered unit no record declares, must have a case
in `CASES`, keyed by its unit name; one with none FAILS NAMING IT. So a writer
added later — DRE-6181's review-cap park, `reconcile.py#hand_review_nudge_to_person`,
is the next one expected — fails here by name until its card adds one entry
under that name, and touches no other.

The kinds are read off `green_light_rows.kinds()`, the contract's vocabulary,
and every one of them is classified below: held to the lines, or excluded
with its reason. A kind in neither set fails by name, so a new kind is
classified before this is green again.

## What it does not see

Whatever `green_light_rows` and the discovery modules say they cannot see — a
hand move in the Linear UI above all — and the weekly agent-failure report,
whose writer lives in agent-bureau and has no `arrivals` record here.

Run: cd bureau-pipeline && python3 -m pytest tests/test_green_light_writers_declare.py -v
"""
from __future__ import annotations

import copy
import functools
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
# `reconcile` reads these at import, and `ready_lane_writers` imports it to read
# its published `destinations()`.
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import code_owner_hold  # noqa: E402
import console_escalation  # noqa: E402
import green_light_rows  # noqa: E402
import lane_contract  # noqa: E402
import planning_escalation  # noqa: E402
import step_shell  # noqa: E402
from test_no_unplanned_ready_lane_writer import _Staged  # noqa: E402

# --------------------------------------------------------------------------- #
# the kinds                                                                    #
# --------------------------------------------------------------------------- #

#: The decision kinds: a row that asks the CEO to choose, held to the lines.
DECISION_KINDS = ("question", "agent-escalation")

#: The kinds that are not questions, each with the reason it is excluded.
EXCLUDED_KINDS = {
    "passed-plan": "a plan both critics passed is a plan to approve, not a "
                   "question — the plan itself is what he reads",
    "queued-epic": "an approved epic waiting in line asks nothing of the CEO — "
                   "the row shows where his approved work is in the line",
    "weekly-report": "a report to read and close is not a question — and its "
                     "writer lives in agent-bureau, with no arrivals record here",
}


def unclassified(vocabulary) -> list:
    """Every kind in `vocabulary` that is neither held nor excluded."""
    return [k for k in vocabulary if k not in DECISION_KINDS and k not in EXCLUDED_KINDS]


def held_units(root: Path = ROOT, contract: dict | None = None) -> dict:
    """`{unit: kind}` for every discovered write into the lane this file holds
    to the lines: a decision kind, or no declared kind at all (None) — an
    undeclared writer is never presumed excluded."""
    kind_of = {r.get("where"): r.get("kind")
               for r in green_light_rows.arrivals(contract)}
    out: dict = {}
    for _, unit in green_light_rows.green_light_writes(str(root), contract):
        kind = kind_of.get(unit)
        if kind not in EXCLUDED_KINDS:
            out[unit] = kind
    return out


def uncased(units) -> list:
    """Every held unit with no case in `CASES`, each named."""
    return [
        f"{unit} writes {green_light_rows.lane_name()} as kind "
        f"{kind or 'UNDECLARED'} and has no case in CASES — add one under its "
        "unit name that exercises its three lines"
        for unit, kind in sorted(units.items()) if unit not in CASES
    ]


@functools.lru_cache(maxsize=1)
def _discovered() -> tuple:
    """The real tree's discovery, read once: `(write, unit)` pairs."""
    return tuple(green_light_rows.green_light_writes(str(ROOT)))


@functools.lru_cache(maxsize=1)
def _held() -> dict:
    return held_units()


# --------------------------------------------------------------------------- #
# assertions                                                                   #
# --------------------------------------------------------------------------- #


def assert_declares_the_lines(note: str) -> console_escalation.Escalation:
    """The contract, read through the module that declares it."""
    assert console_escalation.problems(note) == [], note
    esc = console_escalation.parse(note)
    assert esc is not None, note
    return esc


def _none_given_line(esc: console_escalation.Escalation) -> str:
    return console_escalation.render(esc).split("\n")[-1]


def _recommendation_line(note: str) -> str:
    return next(line for line in note.split("\n")
                if line.startswith(console_escalation.RECOMMENDATION_PREFIX))


# --------------------------------------------------------------------------- #
# fixtures                                                                     #
# --------------------------------------------------------------------------- #

CARD = "DRE-2848"

#: A plain reason that declares none of the lines and recommends nothing.
PLAIN_REASON = (
    "This one is a judgement call about who we are selling to, not a piece of "
    "work an agent can finish. Deciding it wrong costs us a quarter, and the "
    "decision needs you rather than a plan."
)

#: The plain sentence a reason that declares the lines opens with.
PREAMBLE = "The reviewer read this card twice and could not settle it on its own."

#: A free-prose question an agent wrote that declares none of the lines.
FREE_PROSE = (
    "The card asks for the old page to go, but two customers still link to it. "
    "Should we keep it up for a week first?"
)


def _fixture_escalation(card: str = "DRE-3879") -> console_escalation.Escalation:
    """A synthetic case of 2026-09-14 as the escalation a writer renders."""
    record = next(r for r in console_escalation.load_fixtures() if r["card"] == card)
    return console_escalation.Escalation(
        finding=record["finding"],
        question=record["question"],
        recommendation=record["recommendation"],
        why=record["recommendation_why"],
        choices=tuple(console_escalation.Choice(c["id"], c["label"], c["effect"],
                                                c["outcome"])
                      for c in record["choices"]),
        recommended=record["recommended"],
    )


def _block(card: str = "DRE-3879") -> dict:
    built = console_escalation.block(_fixture_escalation(card))
    assert built is not None
    return built


def _recommended_label(block: dict) -> str:
    return next(c["label"] for c in block["choices"] if c["id"] == block["recommended"])


# --------------------------------------------------------------------------- #
# the cases, keyed by the discovery's unit name                                #
# --------------------------------------------------------------------------- #


def _question_notes() -> None:
    """`planning_escalation.py#escalate` (DRE-3909): its note is
    `escalation_comment`, on each of the reasons it is handed."""
    declared = (f"{PREAMBLE}\n\n"
                + console_escalation.render_with_block(_fixture_escalation()))
    # A reason that declares the lines and a block: lifted, shown once.
    esc = assert_declares_the_lines(
        planning_escalation.escalation_comment(CARD, declared))
    assert esc.question == _fixture_escalation().question
    # A reason that declares nothing: completed, recommending nothing.
    esc = assert_declares_the_lines(
        planning_escalation.escalation_comment(CARD, PLAIN_REASON))
    assert esc.recommendation is None
    # One completed from a choices block: the answer is the block's label.
    block = _block("DRE-3889")
    esc = assert_declares_the_lines(
        planning_escalation.escalation_comment(CARD, PLAIN_REASON, choices=block))
    assert esc.recommendation == _recommended_label(block)
    # The classifier's transport failure (DRE-3074's arrival at this door).
    assert_declares_the_lines(
        planning_escalation.escalation_comment(CARD, None, transport=True))


def _code_owner_note() -> None:
    """`code_owner_hold.py#park` (DRE-5204): its comment is `card_comment`
    over `hold_sentence`, the signature its own card keeps."""
    pr = 812
    groups = [(["@dreadnought-foundry/platform"], ["scripts/", ".github/"])]
    note = code_owner_hold.card_comment(pr, code_owner_hold.hold_sentence(pr, groups))
    esc = assert_declares_the_lines(note)
    assert esc.recommendation is not None


def _step_lines(write) -> list:
    """The write's own step, from its first line up to the write line, read
    through `step_shell.workflow_source` and then as the shell reads it."""
    file, _, line = write.where.rpartition(":")
    path = ROOT / file
    first, _, _ = green_light_rows._step_at(str(path), str(ROOT), int(line))
    assert first is not None, write.where
    source = step_shell.workflow_source(path, ROOT).splitlines()
    return green_light_rows._shell_lines("\n".join(source[first - 1:int(line) - 1]))


def _runs(args: list, script: str, command: str) -> bool:
    return any(os.path.basename(a) == script and args[i + 1:i + 2] == [command]
               for i, a in enumerate(args))


def _option(args: list, name: str) -> str:
    return args[args.index(name) + 1]


def _workflow_case(unit: str):
    """A workflow writer (DRE-3911, DRE-6174): between the comment's preamble
    — the receipt `green_light_rows` already holds it to — and the
    `linear_ops.py comment` that precedes the lane write, its step runs
    `console_escalation.py complete`. What that invocation prints for a
    free-prose question is then held to the lines too."""
    receipt = green_light_rows.AGENT_ESCALATION_STEPS[unit]

    def case() -> None:
        writes = [w for w, u in _discovered() if u == unit]
        assert writes, f"{unit} no longer writes the lane"
        for write in writes:
            lines = _step_lines(write)
            at_receipt = next((i for i, args in enumerate(lines)
                               if any(a.startswith(receipt) for a in args)), None)
            at_complete = next((i for i, args in enumerate(lines)
                                if _runs(args, "console_escalation.py", "complete")), None)
            at_comment = max((i for i, args in enumerate(lines)
                              if _runs(args, "linear_ops.py", "comment")), default=None)
            assert None not in (at_receipt, at_complete, at_comment), (
                write.where, at_receipt, at_complete, at_comment)
            assert at_receipt < at_complete < at_comment, write.where
            args = lines[at_complete]
            printed = console_escalation.complete(
                FREE_PROSE, question=_option(args, "--question"),
                who=_option(args, "--who"))
            esc = assert_declares_the_lines(printed)
            assert esc.recommendation is None

    return case


#: One case per decision-kind writer, keyed by the discovery's unit name. A
#: writer added later adds one entry under its own unit name.
CASES = {
    "planning_escalation.py#escalate": _question_notes,
    "code_owner_hold.py#park": _code_owner_note,
    "agent-task.yml#Report result to Linear":
        _workflow_case("agent-task.yml#Report result to Linear"),
    "proof-task.yml#Report proof result to Linear":
        _workflow_case("proof-task.yml#Report proof result to Linear"),
}


# --------------------------------------------------------------------------- #
# the tests                                                                    #
# --------------------------------------------------------------------------- #


class TestTheKindsAreClassified:
    def test_every_kind_in_the_vocabulary_is_held_or_excluded(self):
        assert unclassified(green_light_rows.kinds()) == []

    def test_a_kind_outside_both_sets_fails_by_name(self):
        contract = copy.deepcopy(lane_contract.load())
        lane = lane_contract.lane(green_light_rows.lane_name(), contract=contract)
        lane["clauses"]["entrance"]["kinds"].append("look-again")
        assert unclassified(green_light_rows.kinds(contract)) == ["look-again"]

    def test_the_classified_kinds_are_the_contracts(self):
        # A classification for a kind the contract no longer carries is stale.
        vocabulary = set(green_light_rows.kinds())
        assert set(DECISION_KINDS) <= vocabulary
        assert set(EXCLUDED_KINDS) <= vocabulary

    @pytest.mark.parametrize("kind", sorted(EXCLUDED_KINDS))
    def test_each_excluded_kind_names_its_reason(self, kind):
        assert EXCLUDED_KINDS[kind].strip(), kind
        assert kind not in DECISION_KINDS


class TestEveryDiscoveredDecisionWriterHasACase:
    def test_the_discovery_finds_decision_writers_in_python_and_the_workflows(self):
        # Guards the guard: every assertion below passes over an empty sweep.
        hows = {w.how for w, u in _discovered() if u in _held()}
        assert hows == {"python", "workflow"}, _held()

    def test_every_held_unit_has_a_case(self):
        assert uncased(_held()) == []

    def test_every_case_is_a_unit_the_discovery_holds(self):
        stale = sorted(set(CASES) - set(_held()))
        assert stale == [], f"cases for units that no longer write the lane: {stale}"

    @pytest.mark.parametrize("unit", sorted(CASES))
    def test_the_case_exercises_its_writers_three_lines(self, unit):
        CASES[unit]()

    def test_a_staged_python_writer_with_no_case_fails_by_name(self):
        rogue = "zz_rogue_green_light_park.py"
        source = (
            "import linear_ops\n\n"
            "def park(card):\n"
            f"    linear_ops.cmd_state(card, {green_light_rows.lane_name()!r})\n"
        )
        with _Staged(f"scripts/{rogue}", source):
            named = uncased(held_units())
        assert any(p.startswith(f"{rogue}#park ") for p in named), named
        assert any("UNDECLARED" in p for p in named), named


class TestTheLines:
    def test_a_writer_given_no_recommendation_says_none_given(self):
        printed = console_escalation.complete(
            FREE_PROSE, question="Which way should this go?", who="the build agent")
        esc = assert_declares_the_lines(printed)
        assert esc.recommendation is None
        line = _recommendation_line(printed)
        assert line == _none_given_line(esc)
        assert line.startswith(
            f"{console_escalation.RECOMMENDATION_PREFIX} "
            f"{console_escalation.NONE_GIVEN}{console_escalation.SEPARATOR}")

    def test_a_note_carrying_a_choices_block_recommends_its_label(self):
        # The note `escalate` posts: the comment, then the block it chose.
        block = _block("DRE-3879")
        note = (planning_escalation.escalation_comment(CARD, PLAIN_REASON, choices=block)
                + "\n\n" + planning_escalation.choices_block(block))
        esc = assert_declares_the_lines(note)
        carried = planning_escalation.parse_choices(note)
        assert carried == block
        assert esc.recommendation == _recommended_label(carried)
        assert esc.recommended == carried["recommended"]


class TestTheWordsMoveWithTheWriters:
    def _entrance(self) -> str:
        return lane_contract.lane(green_light_rows.lane_name())["clauses"]["entrance"]["text"]

    def test_the_entrance_no_longer_says_an_escalation_recommends_nothing(self):
        assert "carries no recommendation" not in self._entrance()

    def test_the_entrance_says_each_decision_row_carries_the_lines(self):
        text = self._entrance()
        for needle in ("Finding", "Question", "Recommendation", "none given",
                       planning_escalation.CHOICES_FENCE, "its own kind"):
            assert needle in text, needle

    def test_the_lifecycle_paragraph_says_the_question_carries_the_lines(self):
        standard = (ROOT / "standards" / "card-quality.md").read_text(encoding="utf-8")
        start = standard.index("## Lifecycle — build by default; escalate by exception")
        section = standard[start:standard.index("\n## ", start + 1)]
        for needle in ("Finding", "Question", "Recommendation",
                       "scripts/console_escalation.py",
                       f"{console_escalation.NONE_GIVEN} —", "DRE-3893"):
            assert needle in section, needle
