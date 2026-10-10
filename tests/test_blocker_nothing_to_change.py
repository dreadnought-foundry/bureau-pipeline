"""RED-first tests: the `nothing-to-change` class's action module (DRE-6458).

A build agent that finds every acceptance criterion already met on the
default branch writes a `nothing-to-change` blocker, with one
`- [x] <criterion> — <evidence>` line per criterion under the stamp (the brief,
DRE-6443), and the poster quotes those lines in the marker (DRE-6444).
`scripts/blocker_nothing_to_change.py` counts them against the card: a card
whose every criterion was attested is canceled, every other card is sent to
Planning once, and a second note that still attests fewer is a person's call.

WHAT THESE TESTS PIN, with `linear_ops` stubbed and the module driven alone —
the run through the real resolver and one real sweep pass is DRE-6509's:

  * Every criterion attested → the conditional `Canceled` write, no other
    write, and no `get_issue` call at all: the criteria are read off the card
    dict the sweep hands in, never re-read.
  * Fewer attested, none attested (DRE-5195's legacy note), no criteria, no
    `description` key → the held `Planning` advance and the lane read back.
  * A refused cancel and an advance that did not land raise `NotNow`.
  * The resolver's earlier `replanned` receipt on the thread → `None`, unless
    the new note attests every criterion.
  * The module posts no comment and never names the resolver's tag.

Run: cd bureau-pipeline && python3 -m pytest tests/test_blocker_nothing_to_change.py -v
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import blocker_class  # noqa: E402
import blocker_nothing_to_change as module  # noqa: E402
import linear_ops  # noqa: E402

SCRIPT = ROOT / "scripts" / "blocker_nothing_to_change.py"
FIXTURE = ROOT / "tests" / "fixtures" / "blocker-reasons.json"
REPO = "dreadnought-foundry/bureau-pipeline"

CRITERIA = (
    "The cap file says 15.",
    "The board reads under the cap.",
    "The proof card records the count.",
)
DESCRIPTION = (
    "Set the epic cap.\n\n"
    "## Acceptance criteria\n\n"
    + "".join(f"- [ ] {text}\n" for text in CRITERIA)
)
EVIDENCE = (
    "config/epic-cap.json holds 15 on main",
    "the board shows 9 of 15",
    "the proof card's record names 9 of 15",
)
PARKED = (" — parked in Backlog until the blocker is resolved (a Todo return here "
          "would redispatch agents into the same wall). Run: "
          "https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/1")
RECEIPT_WORDS = "class=nothing-to-change action=replanned"


def _lines(count, mark="[x]"):
    return [f"- {mark} {CRITERIA[i]} — {EVIDENCE[i]}" for i in range(count)]


def _marker(lines):
    """A marker in the poster's grammar, the first attested line on the
    marker's own line, after the `·`."""
    return f"🛑 Agent blocked: class=nothing-to-change · {chr(10).join(lines)}{PARKED}"


def _reason(lines):
    return blocker_class.marker_reason(_marker(lines))


def _card(description=DESCRIPTION, comments=(), *, with_description=True):
    card = {
        "identifier": "DRE-9001",
        "title": "Set the epic cap to 15",
        "labels": {"nodes": [{"name": "repo:bureau-pipeline"}]},
        "comments": {"nodes": [{"body": body} for body in reversed(comments)]},
    }
    if with_description:
        card["description"] = description
    return card


class StubLinear:
    """`linear_ops` as the module sees it: the three calls it may make, each
    recorded, and anything else an AttributeError — so "no other write" is a
    fact the stub enforces rather than one the test hopes for."""

    def __init__(self, *, state_ok=True, lands=True, lane="Backlog"):
        self.calls = []
        self.state_ok = state_ok
        self.lands = lands
        self.lane = lane

    def cmd_state(self, identifier, state_name, *flags, **kwargs):
        self.calls.append(("cmd_state", identifier, state_name, flags, kwargs))
        return self.state_ok

    def cmd_advance(self, identifier, to_state, from_states_csv, *flags, **kwargs):
        self.calls.append(("cmd_advance", identifier, to_state, from_states_csv, flags, kwargs))
        if self.lands:
            self.lane = to_state
        else:
            print(f"{identifier} is not in {from_states_csv} — not advancing")

    def get_issue(self, identifier, **kwargs):
        self.calls.append(("get_issue", identifier, kwargs))
        return {"identifier": identifier, "state": {"name": self.lane}}

    def window_nodes(self, comments):
        return linear_ops.window_nodes(comments)

    def writes(self):
        return [call for call in self.calls if call[0] != "get_issue"]


@pytest.fixture
def stub(monkeypatch):
    fake = StubLinear()
    monkeypatch.setattr(module, "linear_ops", fake)
    return fake


def _use(monkeypatch, fake):
    monkeypatch.setattr(module, "linear_ops", fake)
    return fake


class TestEveryCriterionAttestedCancels:
    def test_three_of_three_cancels_with_the_conditional_write(self, stub):
        reason = _reason(_lines(3))
        # The fixture is what the card describes: the first attested line
        # follows the `·` on the marker's own line.
        assert reason.startswith("- [x] ")
        result = module.resolve(_card(), reason, repo=REPO)
        assert stub.calls == [(
            "cmd_state", "DRE-9001", "Canceled", (),
            {"expect": ("Backlog",), "labels_absent": ("needs-human",)},
        )]
        action, note = result
        assert action == "canceled"
        assert note.startswith("the agent attested every criterion: ")
        assert reason[:40] in note

    def test_the_note_quotes_at_most_two_hundred_characters(self, stub):
        reason = _reason(_lines(3)) + "\n" + "x" * 500
        _, note = module.resolve(_card(), reason, repo=REPO)
        assert note == "the agent attested every criterion: " + reason[:200]

    def test_no_get_issue_call_reads_the_criteria(self, stub):
        module.resolve(_card(), _reason(_lines(3)), repo=REPO)
        assert not [call for call in stub.calls if call[0] == "get_issue"]

    def test_more_attested_lines_than_criteria_still_cancels(self, stub):
        lines = _lines(3) + ["- [x] One more thing — also on main"]
        action, _ = module.resolve(_card(), _reason(lines), repo=REPO)
        assert action == "canceled"

    def test_a_refused_cancel_raises_not_now_and_writes_nothing_else(self, monkeypatch):
        fake = _use(monkeypatch, StubLinear(state_ok=False))
        with pytest.raises(blocker_class.NotNow, match="Canceled"):
            module.resolve(_card(), _reason(_lines(3)), repo=REPO)
        assert [call[0] for call in fake.calls] == ["cmd_state"]


class TestFewerAttestedReplans:
    def test_two_of_three_advances_to_planning_and_reads_the_lane_back(self, stub):
        result = module.resolve(_card(), _reason(_lines(2)), repo=REPO)
        assert stub.calls == [
            ("cmd_advance", "DRE-9001", "Planning", "Backlog", (), {"held": True}),
            ("get_issue", "DRE-9001", {"fresh": True}),
        ]
        action, note = result
        assert action == "replanned"
        assert note.startswith("2 of 3 criteria attested — ")
        assert "Planning" in note

    def test_the_dre_5195_legacy_note_is_replanned_never_canceled(self, stub):
        entry = next(row for row in json.loads(FIXTURE.read_text(encoding="utf-8"))
                     if row["card"] == "DRE-5195")
        reason = blocker_class.marker_reason(entry["body"])
        assert reason == entry["reason"]
        action, note = module.resolve(_card(), reason, repo=REPO)
        assert action == "replanned"
        assert note.startswith("0 of 3 ")
        assert not [call for call in stub.calls if call[0] == "cmd_state"]

    def test_a_card_with_no_criteria_is_replanned(self, stub):
        action, note = module.resolve(_card("No checklist here."), _reason(_lines(3)),
                                      repo=REPO)
        assert (action, note.split(" criteria")[0]) == ("replanned", "0 of 0")
        assert stub.calls[0][0] == "cmd_advance"

    def test_a_card_with_no_description_key_is_replanned(self, stub):
        card = _card(with_description=False)
        assert "description" not in card
        action, note = module.resolve(card, _reason(_lines(3)), repo=REPO)
        assert (action, note.split(" criteria")[0]) == ("replanned", "0 of 0")

    def test_a_criterion_inside_a_fence_is_not_counted(self, stub):
        description = DESCRIPTION + "\n```\n- [ ] An example of a criterion\n```\n"
        action, _ = module.resolve(_card(description), _reason(_lines(3)), repo=REPO)
        assert action == "canceled"
        description = DESCRIPTION + "- [ ] A fourth criterion, outside any fence\n"
        stub.calls.clear()
        action, note = module.resolve(_card(description), _reason(_lines(3)), repo=REPO)
        assert (action, note.split(" criteria")[0]) == ("replanned", "3 of 4")

    def test_an_unchecked_line_is_not_attested(self, stub):
        lines = _lines(2) + [f"- [ ] {CRITERIA[2]} — not yet"]
        action, note = module.resolve(_card(), _reason(lines), repo=REPO)
        assert action == "replanned"
        assert note.startswith("2 of 3 ")

    def test_an_advance_that_did_not_land_raises_not_now(self, monkeypatch):
        fake = _use(monkeypatch, StubLinear(lands=False))
        with pytest.raises(blocker_class.NotNow) as raised:
            module.resolve(_card(), _reason(_lines(2)), repo=REPO)
        assert "Planning" in str(raised.value)
        assert "Backlog" in str(raised.value)
        assert [call[0] for call in fake.calls] == ["cmd_advance", "get_issue"]

    def test_a_card_another_writer_carried_to_planning_passes(self, monkeypatch):
        fake = _use(monkeypatch, StubLinear(lands=False, lane="Planning"))
        action, _ = module.resolve(_card(), _reason(_lines(1)), repo=REPO)
        assert action == "replanned"
        assert fake.lane == "Planning"


class TestOneHandOffPerCard:
    THREAD = (
        "🛑 Agent blocked: class=nothing-to-change · an older note",
        f"🧭 the resolver — {RECEIPT_WORDS} — 1 of 3 criteria attested — sent to Planning",
        "🛑 Agent blocked: class=nothing-to-change · the newer note",
    )

    def test_a_second_note_attesting_fewer_is_a_persons_call(self, stub):
        result = module.resolve(_card(comments=self.THREAD), _reason(_lines(2)), repo=REPO)
        assert result is None
        assert stub.calls == []

    def test_a_second_note_attesting_every_criterion_still_cancels(self, stub):
        action, _ = module.resolve(_card(comments=self.THREAD), _reason(_lines(3)),
                                   repo=REPO)
        assert action == "canceled"
        assert [call[0] for call in stub.calls] == ["cmd_state"]

    def test_a_canceled_receipt_is_not_a_hand_off(self, stub):
        thread = ("receipt: class=nothing-to-change action=canceled — earlier",)
        action, _ = module.resolve(_card(comments=thread), _reason(_lines(2)), repo=REPO)
        assert action == "replanned"


class TestTheModulePostsNothingAndNamesNoTag:
    def test_the_module_never_names_the_resolvers_tag(self):
        assert "agent-blocker-resolved" not in SCRIPT.read_text(encoding="utf-8")

    def test_the_module_never_calls_a_comment_writer(self):
        source = SCRIPT.read_text(encoding="utf-8")
        for writer in ("cmd_comment", "post_comment", "comment_once", "linear_ops.comment"):
            assert writer not in source, writer

    def test_the_vocabulary_names_this_module_for_the_class(self):
        row = blocker_class.load()["classes"]["nothing-to-change"]
        assert row["action"] == SCRIPT.stem

    def test_check_act_receipts_is_green(self):
        done = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "check_act_receipts.py")],
            cwd=ROOT, capture_output=True, text=True,
            env={**os.environ, "PYTHONPATH": str(ROOT / "scripts")},
        )
        assert done.returncode == 0, done.stdout + done.stderr
