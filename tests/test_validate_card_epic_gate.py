"""The Todo-entry gate stops a build dispatched at an epic (DRE-5319).

The relay dispatches nothing for an epic in Todo that wears `agent:planner`,
so the epic this gate meets is the one that does NOT: a hand-made card with
children, or an `[EPIC]` title nobody labelled. The relay dispatches that as a
build, and before this card the gate either found it clean or repaired its
missing role label — and an engineer built a whole epic as one pull request.

Now `cmd_gate`, on a card in Todo that `epic_todo_gate.is_epic_card` calls an
epic (title, children and comment bodies — never its labels), carries it to
`epic_todo_gate`'s lane (In Progress when it came from there, Planning
otherwise), posts the refusal through `post_refusal`, emits `bounced=true` and
repairs no label. Every other card takes the fix-first path unchanged.

`linear_ops` is stubbed so no network is touched.
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import epic_todo_gate  # noqa: E402
import planning_shape  # noqa: E402
import validate_card  # noqa: E402

CARD = "DRE-999"


def _stamp(shape: str) -> str:
    return planning_shape.shape_comment(shape, "classified at Planning's front door")


class FakeLinear:
    """The linear_ops surface cmd_gate and epic_todo_gate touch.

    `history` is the list of (fromState, toState) names Linear returns for the
    card, newest first; `history=None` makes the history read raise, the way an
    unreadable history does.
    """

    def __init__(self, state, *, title="A card", description="**Repo:** atlas",
                 labels=(), children=0, comments=(), history=()):
        self._state = state
        self._title = title
        self._description = description
        self._labels = list(labels)
        self._children = children
        self._comments = list(comments)
        self._history = history
        self.states: list[tuple[str, str]] = []
        self.comments: list[tuple[str, str]] = []
        self.added_labels: list[tuple[str, str]] = []

    def get_issue(self, identifier, fresh=False):
        return {"id": "x", "identifier": identifier, "state": {"name": self._state}}

    def gql(self, query, variables=None):
        if "history" in query:
            if self._history is None:
                raise RuntimeError("Linear answered 500")
            return {"issue": {"history": {"nodes": [
                {"fromState": {"name": frm}, "toState": {"name": to}}
                for frm, to in self._history
            ]}}}
        return {"issue": {
            "title": self._title,
            "description": self._description,
            "labels": {"nodes": [{"name": n} for n in self._labels]},
            "children": {"nodes": [{"id": i} for i in range(self._children)]},
        }}

    def comment_bodies(self, identifier):
        return list(self._comments) + [body for _, body in self.comments]

    def count_comments(self, identifier, needle, since=None):
        return sum(1 for body in self.comment_bodies(identifier) if needle in body)

    def cmd_comment(self, identifier, body):
        self.comments.append((identifier, body))

    def cmd_state(self, identifier, state, *flags):
        self.states.append((identifier, state))

    def add_label(self, identifier, label):
        self.added_labels.append((identifier, label))
        self._labels.append(label)


def _run(fake):
    """Run the gate against `fake`; return (bounced, role, refusals posted)."""
    emitted = {}
    posted = []
    real_post = epic_todo_gate.post_refusal

    def spy(ops, identifier, body):
        posted.append((identifier, body))
        return real_post(ops, identifier, body)

    with mock.patch.dict(sys.modules, {"linear_ops": fake}), \
            mock.patch.object(validate_card, "_emit",
                              lambda b: emitted.__setitem__("bounced", b)), \
            mock.patch.object(validate_card, "_emit_role",
                              lambda r: emitted.__setitem__("role", r)), \
            mock.patch.object(epic_todo_gate, "post_refusal", spy):
        validate_card.cmd_gate(CARD)
    return emitted["bounced"], emitted.get("role"), posted


def _epic(history, **kw):
    """An epic in Todo that does NOT wear agent:planner: a card with a child."""
    kw.setdefault("labels", ["repo:atlas"])
    return FakeLinear("Todo", title="Rebuild the widget", children=1,
                      history=history, **kw)


class AnUnapprovedEpicIsCarriedToPlanning(unittest.TestCase):
    def setUp(self):
        self.fake = _epic([("Green Light", "Todo"), ("Planning", "Green Light")])
        self.bounced, self.role, self.posted = _run(self.fake)

    def test_it_is_moved_to_planning_exactly_once(self):
        self.assertEqual(self.fake.states, [(CARD, "Planning")])

    def test_the_build_stops(self):
        self.assertTrue(self.bounced)

    def test_one_refusal_is_posted_through_post_refusal(self):
        self.assertEqual(len(self.posted), 1)
        self.assertEqual(len(self.fake.comments), 1)
        self.assertEqual(self.fake.comments[0], self.posted[0])

    def test_the_refusal_opens_with_the_tag_and_says_where_it_went(self):
        body = self.posted[0][1]
        self.assertTrue(body.startswith("🚫 epic-not-todo:"), body)
        self.assertIn("approval in Green Light", body)
        self.assertIn("carried to Planning", body)

    def test_the_refusal_is_the_one_epic_todo_gate_builds(self):
        want = epic_todo_gate.refusal(
            CARD, "Todo", "Rebuild the widget", True, [], "Green Light",
            carried_to="Planning")
        self.assertEqual(self.posted[0][1], want)

    def test_no_label_is_repaired(self):
        # The card wears no agent:* label — the fix-first path would have
        # added agent:planner and built it.
        self.assertEqual(self.fake.added_labels, [])


class AnApprovedEpicIsCarriedBackToInProgress(unittest.TestCase):
    def test_it_returns_to_in_progress(self):
        fake = _epic([("In Progress", "Todo"), ("Green Light", "In Progress")])
        bounced, _, posted = _run(fake)
        self.assertTrue(bounced)
        self.assertEqual(fake.states, [(CARD, "In Progress")])
        self.assertEqual(len(posted), 1)
        self.assertIn("carried to In Progress", posted[0][1])
        self.assertIn("already approved", posted[0][1])
        self.assertEqual(fake.added_labels, [])


class AnEpicWithAnUnreadableHistoryIsNotApproved(unittest.TestCase):
    def test_it_goes_to_planning(self):
        fake = _epic(None)
        bounced, _, posted = _run(fake)
        self.assertTrue(bounced)
        self.assertEqual(fake.states, [(CARD, "Planning")])
        self.assertIn("carried to Planning", posted[0][1])


class TheOtherEpicShapesAreStoppedToo(unittest.TestCase):
    def test_an_unlabelled_epic_title_is_stopped(self):
        fake = FakeLinear("Todo", title="[EPIC] atlas: the new widget",
                          labels=["repo:atlas"], history=[("Backlog", "Todo")])
        bounced, _, _ = _run(fake)
        self.assertTrue(bounced)
        self.assertEqual(fake.states, [(CARD, "Planning")])
        self.assertEqual(fake.added_labels, [])

    def test_a_stamped_epic_is_stopped_whatever_it_wears(self):
        # Labels are never read: an epic stamp on a card that carries a build
        # role is an epic all the same.
        fake = FakeLinear("Todo", title="Rebuild the widget",
                          labels=["repo:atlas", "agent:engineer"],
                          comments=[_stamp("epic")],
                          history=[("Backlog", "Todo")])
        bounced, _, _ = _run(fake)
        self.assertTrue(bounced)
        self.assertEqual(fake.states, [(CARD, "Planning")])

    def test_a_refusal_already_on_the_card_is_not_posted_twice(self):
        prior = epic_todo_gate.refusal(CARD, "Todo", "Rebuild the widget", True,
                                       [], "Backlog", carried_to="Planning")
        fake = _epic([("Backlog", "Todo")], comments=[prior])
        bounced, _, _ = _run(fake)
        self.assertTrue(bounced)
        self.assertEqual(fake.states, [(CARD, "Planning")])
        self.assertEqual(fake.comments, [])


class EveryOtherCardIsHandledAsBefore(unittest.TestCase):
    def test_a_planner_owned_one_off_takes_the_fix_first_path_and_builds(self):
        # agent:planner, no children, a one-off stamp, and no repo label: the
        # fix-first path repairs the repo from the initiative and proceeds.
        fake = FakeLinear("Todo", title="bureau-pipeline: a small fix",
                          description="plain body",
                          labels=["agent:planner", "initiative:bureau-pipeline"],
                          comments=[_stamp("one-off")],
                          history=[("Backlog", "Todo")])
        bounced, role, posted = _run(fake)
        self.assertFalse(bounced)
        self.assertEqual(role, "engineer")
        self.assertEqual(fake.states, [])
        self.assertEqual(posted, [])
        self.assertEqual(fake.added_labels, [(CARD, "repo:bureau-pipeline")])
        self.assertTrue(fake.comments[0][1].startswith("🔧 Auto-fixed"))

    def test_a_clean_planner_owned_one_off_proceeds_untouched(self):
        fake = FakeLinear("Todo", title="a small fix",
                          labels=["agent:planner", "repo:atlas"],
                          comments=[_stamp("one-off")])
        bounced, _, posted = _run(fake)
        self.assertFalse(bounced)
        self.assertEqual((fake.states, fake.comments, posted), ([], [], []))

    def test_an_epic_in_triage_is_handled_as_before(self):
        # Triage is not Todo: the gate repairs the missing role and proceeds,
        # exactly as it did before this card.
        fake = FakeLinear("Triage", title="Rebuild the widget", children=1,
                          labels=["repo:atlas"])
        bounced, _, posted = _run(fake)
        self.assertFalse(bounced)
        self.assertEqual(posted, [])
        self.assertEqual(fake.states, [])
        self.assertEqual(fake.added_labels, [(CARD, "agent:planner")])

    def test_an_epic_outside_the_gateable_lanes_is_untouched(self):
        fake = FakeLinear("In Progress", title="[EPIC] the new widget", children=2)
        bounced, _, posted = _run(fake)
        self.assertFalse(bounced)
        self.assertEqual((fake.states, fake.comments, posted), ([], [], []))
        self.assertEqual(fake.added_labels, [])


if __name__ == "__main__":
    unittest.main()
