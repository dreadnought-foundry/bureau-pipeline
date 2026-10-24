"""The card-validation gate reads the start lanes, not Todo alone (DRE-7110).

Before the flip a build card arrives in `Todo`; after it, the sweep has already
moved it to `In Progress`. So `validate_card.py gate` acts on a card in any of
`lane_contract.START_LANES`, plus `Triage`: a malformed card promoted straight
into `In Progress` is bounced to Planning before any build step, exactly as one
in `Todo` is.

The same gate runs in `plan.yml` (`Card-validation gate`, before the duplicate
guard, on every `agent-plan` dispatch) — and an epic the CEO approves into
`In Progress` is one of those dispatches. Widening the gate must leave that run
alone, so in a start lane other than `Todo` two cards are skipped exactly as
they were when the lane was not gateable at all:

  * an epic on the evidence (`epic_todo_gate.is_epic_card` — the title, the
    children, the comment bodies), and
  * an `agent:planner` card whose shape stamp is not `one-off` (no stamp
    counts, and an unreadable thread is no stamp).

A stamped one-off with no children is a build card and is gated, whatever its
labels.

Since DRE-7660 `Todo` is gone from the contract and `In Progress` is the one
start lane; the old lane's epic carry is exercised against a stand-in.

Every case here runs the gate the way `plan.yml`'s step runs it: the CLI
command, no agent-task snapshot in the environment, `$GITHUB_OUTPUT` set.
Linear is faked at the `linear_ops` module surface the gate imports, and every
write it could make is recorded.
"""

import os
import runpy
import sys
from contextlib import redirect_stdout
from io import StringIO

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)

import agent_task_snapshot  # noqa: E402
import lane_contract  # noqa: E402
import planning_shape  # noqa: E402

CARD = "DRE-999"
SKIPPED = f"{CARD} is an approved epic in 'in progress' — gate skipped"


def _stamp(shape: str) -> str:
    return planning_shape.shape_comment(shape, "classified at Planning's front door")


class FakeLinear:
    """The `linear_ops` surface the gate reads, with every write recorded.

    `comments=None` makes the thread read raise, the way an unreadable thread
    does."""

    def __init__(self, state, *, title="A card", description="",
                 labels=(), children=0, comments=()):
        self._state = state
        self._title = title
        self._description = description
        self._labels = list(labels)
        self._children = children
        self._comments = comments
        self.states: list = []
        self.comments: list = []
        self.added_labels: list = []

    def get_issue(self, identifier, fresh=False):
        return {"id": "x", "identifier": identifier, "state": {"name": self._state}}

    def gql(self, query, variables=None):
        if "history" in query:
            return {"issue": {"history": {"nodes": []}}}
        return {"issue": {
            "title": self._title,
            "description": self._description,
            "labels": {"nodes": [{"name": n} for n in self._labels]},
            "children": {"nodes": [{"id": i} for i in range(self._children)]},
        }}

    def comment_bodies(self, identifier, **kw):
        if self._comments is None:
            raise RuntimeError("Linear answered 500")
        return list(self._comments)

    def count_comments(self, identifier, needle, since=None):
        return 0

    def cmd_comment(self, identifier, body):
        self.comments.append(body)

    def cmd_state(self, identifier, state, *flags):
        self.states.append(state)

    def add_label(self, identifier, label):
        self.added_labels.append(label)

    @property
    def writes(self):
        return (self.states, self.comments, self.added_labels)


def _gate(monkeypatch, tmp_path, fake):
    """Run `validate_card.py gate CARD` as plan.yml's step does; answer
    (bounced, role, stdout)."""
    out = tmp_path / "github_output"
    out.write_text("")
    monkeypatch.delenv(agent_task_snapshot.ENV, raising=False)
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setitem(sys.modules, "linear_ops", fake)
    monkeypatch.setattr(sys, "argv", ["validate_card.py", "gate", CARD])
    buf = StringIO()
    with redirect_stdout(buf):
        runpy.run_path(os.path.join(SCRIPTS, "validate_card.py"), run_name="__main__")
    pairs = dict(line.split("=", 1) for line in out.read_text().splitlines() if line)
    return pairs.get("bounced"), pairs.get("role"), buf.getvalue()


# ── the lanes ────────────────────────────────────────────────────────────────


def test_the_start_lanes_are_read_off_the_contract():
    assert lane_contract.START_LANES == ("In Progress",)


@pytest.mark.parametrize("lane", lane_contract.START_LANES)
def test_a_malformed_card_with_no_pr_is_bounced_to_planning(monkeypatch, tmp_path, lane):
    fake = FakeLinear(lane, title="Fix the widget", description="no repo here")
    bounced, _, _ = _gate(monkeypatch, tmp_path, fake)
    assert bounced == "true"
    assert fake.states == ["Planning"]
    assert len(fake.comments) == 1
    assert fake.comments[0].startswith("🚧 Not ready for build — missing:")
    assert fake.added_labels == []


@pytest.mark.parametrize("lane", lane_contract.START_LANES)
def test_a_well_formed_card_passes(monkeypatch, tmp_path, lane):
    fake = FakeLinear(lane, title="Fix the widget",
                      labels=["repo:atlas", "agent:engineer"],
                      comments=[_stamp("one-off")])
    bounced, role, out = _gate(monkeypatch, tmp_path, fake)
    assert bounced == "false"
    assert role == "engineer"
    assert fake.writes == ([], [], [])
    assert f"{CARD} is clean — proceeding to build" in out


def test_a_card_in_review_is_left_alone(monkeypatch, tmp_path):
    fake = FakeLinear("In Review", title="Fix the widget", description="no repo here")
    bounced, _, out = _gate(monkeypatch, tmp_path, fake)
    assert bounced == "false"
    assert fake.writes == ([], [], [])
    assert "gate skipped" in out


# ── plan.yml's own gate step: an approved epic is skipped ───────────────────

#: Each card's labels are its role labels only; every case runs with and
#: without `repo:atlas`, and the epics on the evidence also run with no role
#: label at all.
UNTOUCHED = {
    "an approved epic with children": dict(
        title="Rebuild the widget", children=3, labels=["agent:planner"],
        comments=[_stamp("epic")]),
    "an approved epic with children and no role label": dict(
        title="Rebuild the widget", children=2, labels=[]),
    "an [EPIC] title with no role label": dict(
        title="[EPIC] Rebuild the widget", labels=[]),
    "an epic stamp with no role label": dict(
        labels=[], comments=[_stamp("epic")]),
    "agent:planner with an epic stamp": dict(
        labels=["agent:planner"], comments=[_stamp("epic")]),
    "agent:planner with a roll-up stamp": dict(
        labels=["agent:planner"], comments=[_stamp("roll-up")]),
    "agent:planner with no stamp": dict(
        labels=["agent:planner"], comments=["a plain comment"]),
    "agent:planner with an unreadable thread": dict(
        labels=["agent:planner"], comments=None),
}


@pytest.mark.parametrize("with_repo", [True, False])
@pytest.mark.parametrize("name", sorted(UNTOUCHED))
def test_plan_yml_gate_leaves_an_approved_epic_untouched(monkeypatch, tmp_path,
                                                         name, with_repo):
    kw = dict(UNTOUCHED[name])
    labels = list(kw.pop("labels")) + (["repo:atlas"] if with_repo else [])
    fake = FakeLinear("In Progress", labels=labels, **kw)
    bounced, _, out = _gate(monkeypatch, tmp_path, fake)
    assert bounced == "false"
    assert fake.writes == ([], [], [])
    assert SKIPPED in out


def test_an_epic_missing_both_labels_is_still_untouched(monkeypatch, tmp_path):
    """The fix-first path would add `agent:planner` with a "Building now"
    comment, or bounce for the repo — neither happens."""
    fake = FakeLinear("In Progress", title="Rebuild the widget", children=2,
                      description="no repo here", comments=[_stamp("epic")])
    bounced, _, out = _gate(monkeypatch, tmp_path, fake)
    assert bounced == "false"
    assert fake.writes == ([], [], [])
    assert SKIPPED in out


# ── a stamped one-off is a build card, whatever it wears ────────────────────


def test_a_stamped_one_off_wearing_agent_planner_is_repaired_as_a_build(
        monkeypatch, tmp_path):
    fake = FakeLinear("In Progress", title="Fix the widget",
                      labels=["agent:planner", "initiative:atlas"],
                      comments=[_stamp("one-off")])
    bounced, _, out = _gate(monkeypatch, tmp_path, fake)
    assert bounced == "false"
    assert fake.added_labels == ["repo:atlas"]
    assert fake.states == []
    assert len(fake.comments) == 1
    assert fake.comments[0].startswith("🔧 Auto-fixed by the start gate: added repo:atlas")
    assert SKIPPED not in out


def test_a_stamped_one_off_missing_its_role_label_is_repaired_as_a_build(
        monkeypatch, tmp_path):
    fake = FakeLinear("In Progress", title="Fix the widget",
                      labels=["repo:atlas"], comments=[_stamp("one-off")])
    bounced, role, out = _gate(monkeypatch, tmp_path, fake)
    assert bounced == "false"
    assert role == "engineer"
    assert fake.added_labels == ["agent:engineer"]
    assert fake.states == []
    assert fake.comments[0].startswith("🔧 Auto-fixed by the start gate: added agent:engineer")
    assert SKIPPED not in out


def test_a_card_without_agent_planner_that_is_no_epic_is_gated(monkeypatch, tmp_path):
    """No stamp, no children, no `[EPIC]`, no `agent:planner`: a build card."""
    fake = FakeLinear("In Progress", title="Fix the widget",
                      labels=["agent:engineer"], description="no repo here")
    bounced, _, _ = _gate(monkeypatch, tmp_path, fake)
    assert bounced == "true"
    assert fake.states == ["Planning"]


def test_the_todo_epic_carry_still_reads_todo(monkeypatch, tmp_path):
    """The `epic-not-todo` carry is Todo's alone: an epic in Todo is carried
    out and the build stopped, never skipped as an approved epic."""
    # The contract names no retiring start lane since DRE-7660, so the carry is
    # exercised against a stand-in; the gate reads both names as it loads.
    import epic_todo_gate

    monkeypatch.setattr(epic_todo_gate, "TODO", "Todo")
    monkeypatch.setattr(lane_contract, "START_LANES",
                        lane_contract.START_LANES + ("Todo",))
    fake = FakeLinear("Todo", title="Rebuild the widget", children=2,
                      labels=["repo:atlas"])
    bounced, _, out = _gate(monkeypatch, tmp_path, fake)
    assert bounced == "true"
    assert fake.states == ["Planning"]
    assert fake.comments[0].startswith("🚫 epic-not-todo:")
    assert SKIPPED not in out
