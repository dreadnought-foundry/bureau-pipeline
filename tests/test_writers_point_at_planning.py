"""The three code writers point at Planning, not Backlog or Triage (DRE-2858).

Three writers put a card straight into a lane the pipeline treats as ready work
— or into the broken-card lane — without it having passed through Planning:

  * `linear_ops._create_card`  — the one-off route's creator, `Backlog`
  * `linear_ops.cmd_create`    — the medic / red-main-repair seam, `Triage`
  * `validate_card._bounce`    — the Todo gate's refusal, `Backlog`

One test per writer, and each NAMES the writer it pins, so reverting any single
one of the three goes red on its own and the failure says which.

## The one route that must NOT move, and why it is pinned here too

`_create_card` is shared: `cmd_oneoff` calls it (the one-off route the card
names) and so does `cmd_subissue` (an epic's children). Those two are not the
same fact. `reconcile.promote_ready` reads `backlog_children()`, which queries
`state: {name: {eq: "Backlog"}}`, and a CHILD with no verdict promotes exactly
as before (reconcile.py, DRE-2724's comment) — so an epic's children created
anywhere but Backlog are children nothing ever promotes. `test_an_epic_child_
still_lands_in_backlog` is that guard: it fails if a later edit "finishes the
job" by moving the children too.
"""

import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import linear_ops  # noqa: E402
import validate_card  # noqa: E402

# The lane Planning owns — the destination this card points every one of the
# three writers at.
PLANNING = "Planning"

GOOD_BODY = "Build the widget.\n\n## Acceptance criteria\n- [ ] it works"

PLAN_LABELS = (
    "--label", "repo:atlas",
    "--label", "initiative:bureau",
    "--label", "agent:engineer",
)


class FakeBoard:
    """A scriptable Linear endpoint for the two CREATE writers.

    Every lane the writers could reach is on the board, so a test that fails
    fails on the lane the writer CHOSE — never on a lane the fake forgot.
    """

    LANES = (
        ("state-intake", "Intake", "backlog"),
        ("state-planning", "Planning", "backlog"),
        ("state-backlog", "Backlog", "backlog"),
        ("state-triage", "Triage", "unstarted"),
    )

    def __init__(self):
        self.created = None

    def gql(self, query, variables=None):
        v = variables or {}
        q = " ".join(query.split())
        if "teams(filter:" in q:
            return {"teams": {"nodes": [{"id": "team-1"}]}}
        if "workflowStates" in q:
            return {
                "workflowStates": {
                    "nodes": [
                        {"id": i, "name": n, "type": t} for i, n, t in self.LANES
                    ]
                }
            }
        if "issue(id: $id) { id identifier title team" in q:
            return {"issue": {"id": "epic-uuid", "identifier": v["id"],
                              "team": {"id": "team-1"},
                              "state": {"name": "In Progress"}}}
        if "children { nodes { id } }" in q:
            return {"issue": {"children": {"nodes": [{"id": "kid-1"}]}}}
        if "labels { nodes { name } }" in q:
            return {"issue": {"labels": {"nodes": [
                {"name": "repo:atlas"},
                {"name": "initiative:bureau"},
                {"name": "agent:planner"},
            ]}}}
        if "team(id: $teamId)" in q and "labels(first: 250)" in q:
            return {"team": {"labels": {"nodes": []}}}
        if "issueLabelCreate" in q:
            name = v["input"]["name"]
            return {"issueLabelCreate": {"issueLabel": {"id": f"lbl-{name}"}}}
        if "issueCreate" in q:
            self.created = v["input"]
            return {"issueCreate": {"issue": {
                "id": "new-uuid", "identifier": "DRE-300", "url": "u"}}}
        raise AssertionError(f"unexpected query: {q[:80]}")

    def lane_of(self, state_id):
        return next(n for i, n, _ in self.LANES if i == state_id)


def _run(fake, fn, *args):
    buf = io.StringIO()
    with mock.patch.object(linear_ops, "gql", side_effect=fake.gql):
        with redirect_stdout(buf):
            fn(*args)
    return buf.getvalue()


class TheOneOffRouteCreatesInPlanning(unittest.TestCase):
    """WRITER 1 — `linear_ops._create_card`, reached by `linear_ops.cmd_oneoff`."""

    def test_linear_ops_create_card_puts_a_one_off_in_planning(self, ):
        fake = FakeBoard()
        with mock.patch.object(linear_ops, "_reject_unless_creatable", lambda *a, **k: None):
            _run(fake, linear_ops.cmd_oneoff, "Build a widget", GOOD_BODY, *PLAN_LABELS)
        self.assertEqual(fake.lane_of(fake.created["stateId"]), PLANNING)


class TheStandaloneCreateSeamCreatesInPlanning(unittest.TestCase):
    """WRITER 2 — `linear_ops.cmd_create`, the seam the medic and
    red-main-repair use to mint a failure card."""

    def test_linear_ops_cmd_create_puts_a_card_in_planning(self):
        fake = FakeBoard()
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write(GOOD_BODY)
            body_file = f.name
        try:
            _run(fake, linear_ops.cmd_create, "Pipeline failure: ci", body_file)
        finally:
            os.unlink(body_file)
        self.assertEqual(fake.lane_of(fake.created["stateId"]), PLANNING)


class FakeLinear:
    """Stub of the linear_ops surface `validate_card.cmd_gate` touches —
    the same shape tests/test_validate_card_gate.py uses."""

    def __init__(self, state, description, labels, title="A card",
                 children=0, project=None):
        self._state = state
        self._description = description
        self._labels = list(labels)
        self._title = title
        self._children = children
        self._project = project
        self.comments: list[tuple[str, str]] = []
        self.states: list[tuple[str, str]] = []
        self.added_labels: list[tuple[str, str]] = []

    def get_issue(self, identifier):
        return {"id": "x", "identifier": identifier, "state": {"name": self._state}}

    def gql(self, query, variables=None):
        return {
            "issue": {
                "title": self._title,
                "description": self._description,
                "labels": {"nodes": [{"name": n} for n in self._labels]},
                "children": {"nodes": [{"id": i} for i in range(self._children)]},
                "project": ({"name": self._project} if self._project else None),
            }
        }

    def cmd_comment(self, identifier, body):
        self.comments.append((identifier, body))

    def cmd_state(self, identifier, state):
        self.states.append((identifier, state))

    def add_label(self, identifier, label):
        self.added_labels.append((identifier, label))
        self._labels.append(label)


def _gate(fake) -> bool:
    emitted = {}
    with mock.patch.dict(sys.modules, {"linear_ops": fake}), mock.patch.object(
        validate_card, "_emit", lambda b: emitted.__setitem__("bounced", b)
    ):
        with redirect_stdout(io.StringIO()):
            validate_card.cmd_gate("DRE-999")
    return emitted["bounced"]


class TheTodoGateRefusesToPlanning(unittest.TestCase):
    """WRITER 3 — `validate_card._bounce`, the one writer that knows for
    certain a card is invalid."""

    def _uninferable(self):
        # No repo line, no repo:/initiative: label, no project — the ONE case
        # the fix-first gate still refuses, because a repair would be a
        # wrong-repo guess.
        return FakeLinear("Todo", "no repo line", ["agent:engineer"], project=None)

    def test_validate_card_bounce_puts_a_card_in_planning(self):
        fake = self._uninferable()
        self.assertTrue(_gate(fake))
        self.assertEqual(fake.states, [("DRE-999", PLANNING)])

    def test_the_bounce_comment_names_the_lane_the_card_actually_lands_in(self):
        # The refusal still carries its plain-English reason, byte for byte —
        # and that reason has to describe the move it just made. Naming Backlog
        # here would be false, and "move to Todo again" would tell the author to
        # skip the classification the bounce is asking for, which is the exact
        # shortcut this card closes.
        fake = self._uninferable()
        _gate(fake)
        self.assertEqual(
            fake.comments,
            [(
                "DRE-999",
                "🚧 Not ready for build — missing: repo: label (or legacy "
                "**Repo:** line). Returned to Planning; fix what is missing "
                "and Planning routes it on from there — do not move it "
                "straight to Todo.",
            )],
        )


class TheTodoGateStillRepairsWhatItCanInfer(unittest.TestCase):
    """Operator decision D1 (2026-08-23), unchanged by this card: the gate
    infers and repairs what it can, and refuses only what it cannot. Bouncing
    what a lookup could fix trains people to route around the gate."""

    def test_a_missing_role_label_is_repaired_and_the_card_does_not_move(self):
        fake = FakeLinear("Todo", "**Repo:** atlas", [], title="Fix it")
        self.assertFalse(_gate(fake))
        self.assertIn(("DRE-999", "agent:engineer"), fake.added_labels)
        self.assertEqual(fake.states, [])

    def test_a_repo_inferred_from_the_initiative_label_is_repaired_not_refused(self):
        fake = FakeLinear("Todo", "no repo line", ["agent:engineer", "initiative:bureau"])
        self.assertFalse(_gate(fake))
        self.assertEqual(fake.states, [])
        self.assertTrue(
            any(lbl.startswith("repo:") for _, lbl in fake.added_labels),
            f"expected a repaired repo label, got {fake.added_labels}",
        )


class AnEpicsChildrenStayWhereTheSweepLooksForThem(unittest.TestCase):
    """The half of `_create_card` that must NOT move, pinned so a later edit
    cannot quietly "finish the job".

    `reconcile.promote_ready` reads `backlog_children()`, whose Linear query is
    `state: {name: {eq: "Backlog"}}`, and a child carrying no routing verdict
    promotes exactly as before. Children created anywhere else are children
    nothing ever promotes.
    """

    def test_an_epic_child_still_lands_in_backlog(self):
        fake = FakeBoard()
        with mock.patch.object(linear_ops, "_reject_unless_creatable", lambda *a, **k: None):
            _run(fake, linear_ops.cmd_subissue, "DRE-100", "A child", GOOD_BODY)
        self.assertEqual(fake.lane_of(fake.created["stateId"]), "Backlog")


if __name__ == "__main__":
    unittest.main()
