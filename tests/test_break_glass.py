"""Break glass — one sanctioned way past the intake gate (DRE-2737).

THE GAP: production breaks at 02:00 and every route past the gate ends in a
queue that waits on a person. The sanctioned path is Intake → Planning →
one-off → Backlog → the promoter; the escalation route is the CEO's own
queue, which at 2am is exactly the person the wave exists to stop
interrupting. `fast-track` is the argument for designing the exception: it
was the hand-workaround for the propose-gate bug (DRE-1980), it outlived the
bug by months as a convention nobody could explain, and it is still in the
label list marked RETIRED. An unenforceable rule with no sanctioned
exception grows an unsanctioned one.

THE DESIGN UNDER TEST — one operator-applied marker, `break-glass`, with
three properties that make it safe rather than a hole:

  1. The gate RECORDS it and does not undo it — the Layer-1 bounce is
     suppressed and the event is written to the card (a notice comment naming
     what was skipped, by whom, when, and what is still owed, plus the
     queryable `break-glass:used` receipt label).
  2. Every use is REPAID: when the work merges, the card returns to Planning
     for the classification it skipped instead of going Done.
  3. It is COUNTED, off the receipt label — so removing the marker mid-flight
     neither strands the card nor undoes the count.

NO AGENT MAY APPLY IT. The fleet shares one LINEAR_API_KEY, so every
automated write resolves to the operator's own user (verified live on
DRE-2737: the Todo gate's own `agent:engineer` write reads
`actor: Frederick Conklin, botActor: None`). Actor identity therefore cannot
tell agent from operator, which is the same conclusion DRE-2725 reached about
"who moved the card". So the load-bearing control is the WRITE SEAM — the
fleet's only label-writing paths refuse to apply the marker — with the
bot-actor provenance check as the second layer for writers we do not own.

Run: cd bureau-pipeline && python3 -m pytest tests/test_break_glass.py -v
"""
from __future__ import annotations

import io
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import break_glass  # noqa: E402
import linear_ops  # noqa: E402
import reconcile  # noqa: E402
import validate_card  # noqa: E402

README = ROOT / "README.md"


# --------------------------------------------------------------------------
# the pure core
# --------------------------------------------------------------------------
def test_the_marker_is_the_name_the_operator_types():
    assert break_glass.MARKER == "break-glass"


@pytest.mark.parametrize(
    ("labels", "expected"),
    [
        (["break-glass", "agent:engineer"], True),
        (["Break-Glass"], True),  # Linear label case is the operator's business
        (["agent:engineer"], False),
        (["break-glass:used"], False),  # the receipt is not the marker
        ([], False),
        (None, False),
    ],
)
def test_marked(labels, expected):
    assert break_glass.marked(labels) is expected


@pytest.mark.parametrize(
    ("labels", "expected"),
    [
        (["break-glass:used"], True),
        (["BREAK-GLASS:USED"], True),
        (["break-glass"], False),  # marker applied, gate never bypassed → owes nothing
        ([], False),
    ],
)
def test_owes_review(labels, expected):
    assert break_glass.owes_review(labels) is expected


def test_bot_provenance_is_refused_and_a_human_is_honored():
    assert break_glass.refusal_reason({"who": "Zapier", "bot": True}) is not None
    assert break_glass.refusal_reason({"who": "Frederick Conklin", "bot": False}) is None


def test_unreadable_provenance_is_honored():
    """No expiry, no approval step, no auto-revoke: every one of those is a way
    for the emergency path to fail during an emergency. An unreadable history
    is loud in the notice, never a refusal."""
    assert break_glass.refusal_reason({"who": None, "bot": False, "readable": False}) is None


def test_the_notice_names_what_was_skipped_who_when_and_what_is_owed():
    body = break_glass.bypass_notice(
        ["repo: label (or legacy **Repo:** line)"],
        {"who": "Frederick Conklin", "bot": False, "at": "2026-08-26T02:04:00Z"},
    )
    assert break_glass.BYPASS_TAG in body
    assert "repo: label" in body                    # what was skipped
    assert "Frederick Conklin" in body              # by whom
    assert "2026-08-26T02:04:00Z" in body           # when
    assert break_glass.REVIEW_STATE in body         # what the card still owes
    assert "not undone" in body.lower() or "not been undone" in body.lower()


# --------------------------------------------------------------------------
# (1) the gate: a marked card is dispatched, and Layer 1 does not return it
# --------------------------------------------------------------------------
class FakeLinear:
    """Stub of the linear_ops surface cmd_gate + break_glass touch."""

    def __init__(self, state, description, labels, title="A card",
                 children=0, project=None, actor="Frederick Conklin",
                 bot=None, history_readable=True):
        self._state = state
        self._description = description
        self._labels = list(labels)
        self._title = title
        self._children = children
        self._project = project
        self._actor = actor
        self._bot = bot
        self._history_readable = history_readable
        self.comments: list[tuple[str, str]] = []
        self.states: list[tuple[str, str]] = []
        self.added_labels: list[tuple[str, str]] = []

    def get_issue(self, identifier):
        return {"id": "x", "identifier": identifier, "state": {"name": self._state}}

    def gql(self, query, variables=None):
        if "history" in query:
            if not self._history_readable:
                raise RuntimeError("Linear history unreadable")
            return {
                "issue": {
                    "history": {
                        "nodes": [
                            {
                                "createdAt": "2026-08-26T02:04:00Z",
                                "actor": ({"name": self._actor} if self._actor else None),
                                "botActor": ({"name": self._bot} if self._bot else None),
                                "addedLabels": [{"name": break_glass.MARKER}],
                            }
                        ]
                    }
                }
            }
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

    def cmd_state(self, identifier, state, *flags):
        self.states.append((identifier, state))

    def add_label(self, identifier, label):
        self.added_labels.append((identifier, label))
        self._labels.append(label)


def _run_gate(fake) -> bool:
    """cmd_gate with linear_ops faked; returns whether it bounced."""
    emitted = {}
    with mock.patch.dict(sys.modules, {"linear_ops": fake}), mock.patch.object(
        validate_card, "_emit", lambda b: emitted.__setitem__("bounced", b)
    ):
        validate_card.cmd_gate("DRE-999")
    return emitted["bounced"]


def test_a_break_glass_card_is_not_returned_by_the_gate():
    """AC 1. The same card WITHOUT the marker is the bounce case below, so the
    fixture proves the guard would have fired.

    MUTATION CHECK: remove the break_glass consult in validate_card.cmd_gate
    and this card bounces to Planning — red here.
    """
    fake = FakeLinear("Todo", "no repo line", ["agent:engineer", "break-glass"])
    assert _run_gate(fake) is False
    assert fake.states == []                      # never returned


def test_the_same_card_without_the_marker_still_bounces():
    """The gate is not weakened for everyone else — the marker is the whole
    difference between these two tests."""
    fake = FakeLinear("Todo", "no repo line", ["agent:engineer"])
    assert _run_gate(fake) is True
    assert fake.states == [("DRE-999", "Planning")]


def test_the_bypass_posts_the_notice_and_stamps_the_receipt():
    """AC 2 + the counter's source of truth."""
    fake = FakeLinear("Todo", "no repo line", ["agent:engineer", "break-glass"])
    _run_gate(fake)
    body = "\n".join(b for _, b in fake.comments)
    assert break_glass.BYPASS_TAG in body
    assert "repo:" in body                                     # what was skipped
    assert "Frederick Conklin" in body                         # by whom
    assert break_glass.REVIEW_STATE in body                    # what is still owed
    assert ("DRE-999", break_glass.RECEIPT_LABEL) in fake.added_labels


def test_a_clean_break_glass_card_records_nothing():
    """The marker is not an event: a card that satisfies the gate on its own
    owes nothing, so no notice, no receipt, nothing to repay."""
    fake = FakeLinear("Todo", "", ["agent:engineer", "repo:atlas", "break-glass"])
    assert _run_gate(fake) is False
    assert fake.comments == []
    assert fake.added_labels == []


# --------------------------------------------------------------------------
# (2) no agent may apply it
# --------------------------------------------------------------------------
def test_the_gate_refuses_a_marker_a_bot_applied():
    """AC 4, second layer: a writer we do not own (a Linear integration, an
    automation app) shows up as a botActor, and its bypass is not honored —
    the card bounces exactly as if the marker were absent."""
    fake = FakeLinear(
        "Todo", "no repo line", ["agent:engineer", "break-glass"],
        actor=None, bot="Some Automation",
    )
    assert _run_gate(fake) is True
    assert fake.states == [("DRE-999", "Planning")]
    body = "\n".join(b for _, b in fake.comments)
    assert break_glass.REFUSED_TAG in body
    assert "Some Automation" in body
    assert (("DRE-999", break_glass.RECEIPT_LABEL)) not in fake.added_labels


def test_an_unreadable_history_does_not_fail_the_emergency_path():
    """The control is that it is loud and counted, not that it is hard to use:
    a provenance read that fails must not become an approval step."""
    fake = FakeLinear(
        "Todo", "no repo line", ["agent:engineer", "break-glass"],
        history_readable=False,
    )
    assert _run_gate(fake) is False
    body = "\n".join(b for _, b in fake.comments)
    assert break_glass.BYPASS_TAG in body
    # Honest about what it could not read, rather than naming a likely human:
    # Linear's history is eventually consistent and a freshly-marked card
    # routinely returns nothing (measured live on DRE-2757).
    assert "unknown" in body.lower() and "history" in body.lower()


def test_add_label_refuses_the_marker():
    """AC 4, the load-bearing layer. `add_label` is the fleet's ONE
    label-writing path (the dead-run hold, the Todo gate's autofix, the
    watchdog) — refusing here is what "no agent may apply it" actually means,
    because actor identity cannot tell an agent from the operator.

    MUTATION CHECK: drop the guard and `gql` is called — red here.
    """
    with patch.object(linear_ops, "gql") as gql:
        with pytest.raises(linear_ops.LinearError) as err:
            linear_ops.add_label("DRE-999", "break-glass")
    gql.assert_not_called()
    assert "operator" in str(err.value).lower()


def test_add_label_still_writes_every_other_label():
    """The refusal is exactly one label wide — the hold label still works."""
    with patch.object(linear_ops, "gql") as gql:
        gql.side_effect = [
            {"issue": {"id": "u", "team": {"id": "t"}, "labels": {"nodes": []}}},
            {"team": {"labels": {"nodes": [{"id": "l1", "name": "needs-human"}]}}},
            {"issueUpdate": {"success": True}},
        ]
        linear_ops.add_label("DRE-999", "needs-human")
    assert gql.call_count == 3


def test_the_receipt_label_is_writable_by_the_pipeline():
    """The pipeline stamps the receipt itself — only the MARKER is refused."""
    assert linear_ops.agent_label_refusal(break_glass.RECEIPT_LABEL) is None
    assert linear_ops.agent_label_refusal(break_glass.MARKER) is not None


def test_a_planner_child_never_inherits_the_marker():
    """A child created under a broken-glass epic must not be born with the
    marker — that would let one operator action open the gate for a whole
    epic's worth of cards nobody looked at."""
    child = linear_ops.child_labels_from(
        ["repo:bureau-pipeline", "initiative:bureau", "break-glass"],
        ["break-glass", "web"],
    )
    assert break_glass.MARKER not in [l.lower() for l in child]
    assert "web" in child
    assert "repo:bureau-pipeline" in child


# --------------------------------------------------------------------------
# (3) the debt is repaid: merged work returns to Planning, not Done
# --------------------------------------------------------------------------
def _issue(title, labels):
    return {
        "id": "card-uuid",
        "identifier": "DRE-2737",
        "title": title,
        "team": {"id": "team-1"},
        "state": {"name": "In Review", "type": "started"},
        "labels": {"nodes": [{"name": n} for n in labels]},
    }


def _run_card_done(title, labels):
    buf = io.StringIO()
    with patch.object(
        linear_ops, "get_issue", return_value=_issue(title, labels)
    ), patch.object(linear_ops, "cmd_state") as state, patch.object(
        linear_ops, "cmd_comment"
    ) as comment:
        with redirect_stdout(buf):
            linear_ops.cmd_card_done("DRE-2737", "https://github.com/o/r/pull/40")
    return buf.getvalue(), state, comment


def test_merged_break_glass_card_returns_to_planning_not_done():
    """AC 3. The shortcut is repaid, not forgiven: the card still owes the
    classification it skipped, and the review happens when it is cheap.

    MUTATION CHECK: drop the owes_review branch in cmd_card_done and the card
    goes Done — red here.
    """
    _, state, comment = _run_card_done("Hotfix the outage", ["break-glass:used"])
    state.assert_called_once_with("DRE-2737", break_glass.REVIEW_STATE)
    body = comment.call_args.args[1]
    assert break_glass.REVIEW_TAG in body
    assert "https://github.com/o/r/pull/40" in body


def test_removing_the_marker_mid_flight_does_not_strand_the_card():
    """AC 6. The merge path reads the RECEIPT (what actually happened), never
    the live marker — so a marker removed after the bypass neither strands the
    card in a lane nothing grooms nor erases the debt."""
    _, state, comment = _run_card_done("Hotfix the outage", ["break-glass:used"])
    state.assert_called_once_with("DRE-2737", break_glass.REVIEW_STATE)
    assert break_glass.REVIEW_TAG in comment.call_args.args[1]


def test_a_marker_with_no_bypass_still_closes_normally():
    """The operator marked a card the gate never had to bounce: nothing was
    skipped, so nothing is owed and the card closes like any other."""
    _, state, comment = _run_card_done("Hotfix the outage", ["break-glass"])
    state.assert_called_once_with("DRE-2737", "Done")
    assert "✅ Merged" in comment.call_args.args[1]


def test_an_ordinary_card_still_goes_done():
    _, state, comment = _run_card_done("Add folder ACL enforcement", ["agent:engineer"])
    state.assert_called_once_with("DRE-2737", "Done")


def test_a_no_code_break_glass_card_keeps_its_operator_close_and_still_owes():
    """The `no-code` guard (six false portico closes) wins on the STATE — the
    operator closes that card by hand — but the break-glass debt is still
    recorded on it, not silently dropped."""
    _, state, comment = _run_card_done("Operator: rotate the key", ["no-code", "break-glass:used"])
    state.assert_not_called()
    bodies = "\n".join(c.args[1] for c in comment.call_args_list)
    assert linear_ops.MERGED_NOT_CLOSED_MARKER in bodies
    assert break_glass.REVIEW_TAG in bodies


# --------------------------------------------------------------------------
# (3b) the same debt, through reconcile's merged-PR backstop
# --------------------------------------------------------------------------
def _sweep_card(title, labels, identifier="DRE-2737"):
    return {
        "id": f"uuid-{identifier}",
        "identifier": identifier,
        "title": title,
        "description": "hotfix",
        "updatedAt": "2026-07-01T00:00:00Z",
        "state": {"name": "In Review"},
        "labels": {"nodes": [{"name": n} for n in labels]},
    }


def _run_merged_sweep(card, note_already_posted=False):
    reconcile._write_failures.clear()
    merged_pr = {
        "number": 40,
        "headRefName": f"agent/{card['identifier']}-hotfix",
        "state": "MERGED",
        "comments": [],
        "headRefOid": "a" * 40,
    }
    mocks = {
        "unstick_conflicts": MagicMock(),
        "retrigger_dead_heads": MagicMock(),
        "check_dependabot_capacity": MagicMock(),
        "fix_approved_but_red": MagicMock(),
        "retry_dead_fix_runs": MagicMock(),
        "review_dependabot_prs": MagicMock(),
        "recover_crashed_reviews": MagicMock(),
        "flag_no_checks_prs": MagicMock(),
        "flag_unowned_prs": MagicMock(),
        "restart_answered_blockers": MagicMock(),
        "close_finished_epics": MagicMock(),
        "flag_stranded": MagicMock(return_value=set()),
        "promote_ready": MagicMock(return_value=0),
        "active_cards": MagicMock(return_value=[card]),
        "pr_for": MagicMock(return_value=merged_pr),
        "report_break_glass": MagicMock(),
    }
    with patch.multiple(reconcile, **mocks), patch.object(
        reconcile, "REPO_SLUG", "bureau-pipeline"
    ), patch.object(reconcile.linear_ops, "cmd_state") as state, patch.object(
        reconcile.linear_ops, "cmd_comment"
    ) as comment, patch.object(
        reconcile.linear_ops,
        "count_comments",
        return_value=1 if note_already_posted else 0,
    ):
        reconcile.main()
    return state, comment


def test_the_sweep_backstop_returns_a_break_glass_card_to_planning():
    """linear-sync can be down; the backstop must repay the same debt rather
    than closing the card the fast path deliberately did not close.

    MUTATION CHECK: drop the branch and `state` records ('DRE-2737', 'Done').
    """
    card = _sweep_card("Hotfix", ["repo:bureau-pipeline", "break-glass:used"])
    state, comment = _run_merged_sweep(card)
    state.assert_called_once_with("DRE-2737", break_glass.REVIEW_STATE)
    assert break_glass.REVIEW_TAG in comment.call_args.args[1]


def test_the_sweep_posts_the_review_note_only_once():
    card = _sweep_card("Hotfix", ["repo:bureau-pipeline", "break-glass:used"])
    state, comment = _run_merged_sweep(card, note_already_posted=True)
    state.assert_called_once_with("DRE-2737", break_glass.REVIEW_STATE)
    comment.assert_not_called()


def test_the_sweep_still_closes_an_ordinary_merged_card():
    card = _sweep_card("Add ACLs", ["repo:bureau-pipeline", "agent:engineer"])
    state, _ = _run_merged_sweep(card)
    state.assert_called_once_with("DRE-2737", "Done")


# --------------------------------------------------------------------------
# (4) it is counted, and the count is read a specific way
# --------------------------------------------------------------------------
def _counts_linear(nodes):
    fake = MagicMock()
    fake.gql.return_value = {"issues": {"nodes": nodes}}
    return fake


def test_the_count_is_taken_from_the_receipt_not_the_marker():
    """Frequent use is not people cheating — it means the front door is too
    slow, and that is the finding. So the number must survive an operator
    tidying the marker off the card afterwards."""
    fake = _counts_linear([
        {"identifier": "DRE-1", "state": {"name": "Done", "type": "completed"},
         "labels": {"nodes": [{"name": "break-glass:used"}]}},
        {"identifier": "DRE-2", "state": {"name": "Planning", "type": "unstarted"},
         "labels": {"nodes": [{"name": "break-glass:used"}]}},
    ])
    counts = break_glass.counts(fake)
    assert counts["recorded"] == 2
    assert counts["owing"] == 1
    # queried by the receipt label, never by the operator's marker
    label_arg = str(fake.gql.call_args)
    assert break_glass.RECEIPT_LABEL in label_arg


def test_the_count_line_reads_beside_the_other_numbers_without_a_filter():
    line = break_glass.count_line(3, 1)
    assert "break-glass" in line
    assert "3" in line and "1" in line


def test_an_unavailable_count_reads_unknown_never_zero():
    """console-honesty rule 2: unknown is shown as unknown. A break-glass KPI
    that renders 0 when the query failed says "the front door is fine"."""
    line = break_glass.count_line(None, None, error="Linear 500")
    assert "unknown" in line.lower()
    assert "0" not in line.split("unknown")[0]


def test_the_sweep_reports_the_count_every_run():
    """The number is emitted by the sweep that already runs — no filter, no
    dashboard query, no one remembering to look."""
    fake = _counts_linear([
        {"identifier": "DRE-1", "state": {"name": "Done", "type": "completed"},
         "labels": {"nodes": [{"name": "break-glass:used"}]}},
    ])
    buf = io.StringIO()
    with patch.object(reconcile, "linear_ops", fake), redirect_stdout(buf):
        reconcile.report_break_glass()
    assert "break-glass" in buf.getvalue()
    assert "1" in buf.getvalue()


def test_a_failed_count_never_fails_the_sweep():
    fake = MagicMock()
    fake.gql.side_effect = RuntimeError("Linear down")
    buf = io.StringIO()
    with patch.object(reconcile, "linear_ops", fake), redirect_stdout(buf):
        reconcile.report_break_glass()          # must not raise
    assert "unknown" in buf.getvalue().lower()


# --------------------------------------------------------------------------
# (5) the record: the README says what the sanctioned exception is
# --------------------------------------------------------------------------
def test_the_readme_names_break_glass_as_the_sanctioned_exception():
    """`fast-track` grew because the README said "nobody should re-add it" and
    offered nothing instead. The document that retires the unsanctioned
    workaround has to carry the sanctioned one."""
    text = README.read_text()
    assert "break-glass" in text
    assert "fast-track" in text                 # the retirement stays recorded
    flat = " ".join(text.split())
    assert "break-glass:used" in flat           # the receipt/count is documented
    assert "Planning" in flat


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
