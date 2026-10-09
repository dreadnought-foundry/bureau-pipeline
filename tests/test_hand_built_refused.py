"""The label seam refuses `hand-built` (DRE-6361).

THE RULE: the CEO's, 2026-10-07 — "Hand-built cards are typically when I tell
them, 'Hey do that by hand.' It shouldn't come from anybody else." After
DRE-6227 flipped the vocabulary and DRE-6228 switched the three writers, no
pipeline path writes `hand-built` on purpose. Nothing stopped the next one.

THE DESIGN UNDER TEST — the mark is refused the way `break-glass` already is
(DRE-2737), at the seam rather than at each writer, so a writer added tomorrow
is refused before any request instead of discovered on the board:

  * `agent_label_refusal` names the rule and its date;
  * `add_label` raises before any request;
  * `child_labels_from` drops it from a planner-created child, loudly;
  * the create paths (`create_card`, `cmd_create`, `cmd_oneoff`) refuse an
    explicit label before any request.

Taking the mark OFF stays writable — `remove_label` is the migration's tool.

Every seam runs against a stubbed `gql` that records each request, so
"before any request" is a count of zero, not a reading of the code.

Run: cd bureau-pipeline && python3 -m pytest tests/test_hand_built_refused.py -v
"""
from __future__ import annotations

import io
import os
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")

import break_glass  # noqa: E402
import linear_ops  # noqa: E402
import routing_verdict  # noqa: E402

HAND_BUILT = routing_verdict.HAND_BUILT_LABEL

GOOD_BODY = (
    "The thing to build.\n\n**Files:** scripts/x.py\n\n"
    "## Acceptance criteria\n- [ ] it is built"
)


class RecordingLinear:
    """A stub `gql` that records every request and answers the shapes the
    label and create seams ask for."""

    def __init__(self, labels=()):
        self.requests: list[str] = []
        self.labels = [{"id": f"lbl-{n}", "name": n} for n in labels]
        self.updated = None
        self.created = None

    def gql(self, query, variables=None):
        q = " ".join(query.split())
        self.requests.append(q)
        v = variables or {}
        if "teams(filter:" in q:
            return {"teams": {"nodes": [{"id": "team-1"}]}}
        if "workflowStates" in q:
            return {"workflowStates": {"nodes": [
                {"id": "state-planning", "name": "Planning", "type": "backlog"}]}}
        if "team(id: $teamId)" in q:
            return {"team": {"labels": {"nodes": []}}}
        if "issueLabelCreate" in q:
            return {"issueLabelCreate": {"issueLabel": {"id": f"lbl-{v['input']['name']}"}}}
        if "issueUpdate" in q:
            self.updated = v["input"]
            return {"issueUpdate": {"success": True}}
        if "issueCreate" in q:
            self.created = v["input"]
            return {"issueCreate": {"issue": {
                "id": "new-uuid", "identifier": "DRE-300", "url": "u"}}}
        if "issue(id: $id)" in q:
            return {"issue": {"id": "uuid-1", "team": {"id": "team-1"},
                              "labels": {"nodes": list(self.labels)}}}
        raise AssertionError(f"unexpected query: {q[:80]}")


def _quiet(fn, *args, **kwargs):
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        return fn(*args, **kwargs)


# --------------------------------------------------------------------------
# the contract: the refusal names the rule and its date, and nothing else
# --------------------------------------------------------------------------
def test_the_refusal_names_the_ceos_rule_and_its_date():
    refusal = linear_ops.agent_label_refusal(HAND_BUILT)
    assert refusal
    assert "2026-10-07" in refusal
    assert "CEO" in refusal


def test_the_refusal_is_case_and_space_blind():
    assert linear_ops.agent_label_refusal(" Hand-Built ") is not None


@pytest.mark.parametrize("label", [
    routing_verdict.OPERATOR_STEP_LABEL, "no-code", "automation",
    break_glass.RECEIPT_LABEL, "needs-human",
])
def test_the_markers_the_pipeline_writes_stay_writable(label):
    assert linear_ops.agent_label_refusal(label) is None


def test_break_glass_is_refused_exactly_as_before():
    refusal = linear_ops.agent_label_refusal(break_glass.MARKER)
    assert refusal is not None
    assert "operator action" in refusal
    assert "DRE-2737" in refusal
    assert "2026-10-07" not in refusal


# --------------------------------------------------------------------------
# seam 1 — add_label
# --------------------------------------------------------------------------
def test_add_label_refuses_hand_built_before_any_request():
    """MUTATION CHECK: drop the hand-built branch from agent_label_refusal and
    the stub records the issue read — red here."""
    fake = RecordingLinear()
    with patch.object(linear_ops, "gql", side_effect=fake.gql):
        with pytest.raises(linear_ops.LinearError) as err:
            _quiet(linear_ops.add_label, "DRE-1", HAND_BUILT)
    assert fake.requests == []
    assert "2026-10-07" in str(err.value)
    assert "DRE-1" in str(err.value)


def test_add_label_still_writes_operator_step():
    fake = RecordingLinear()
    with patch.object(linear_ops, "gql", side_effect=fake.gql):
        _quiet(linear_ops.add_label, "DRE-1", routing_verdict.OPERATOR_STEP_LABEL)
    assert fake.requests
    assert fake.updated == {"labelIds": [f"lbl-{routing_verdict.OPERATOR_STEP_LABEL}"]}


def test_remove_label_still_takes_hand_built_off():
    """Taking the mark OFF is the migration's job and stays writable."""
    fake = RecordingLinear(labels=[HAND_BUILT, "repo:x"])
    with patch.object(linear_ops, "gql", side_effect=fake.gql):
        _quiet(linear_ops.remove_label, "DRE-1", HAND_BUILT)
    assert fake.updated == {"labelIds": ["lbl-repo:x"]}


# --------------------------------------------------------------------------
# seam 2 — child_labels_from: a child never inherits it
# --------------------------------------------------------------------------
def test_a_child_of_a_hand_built_epic_does_not_carry_the_mark_and_says_so():
    err = io.StringIO()
    with redirect_stderr(err):
        child = linear_ops.child_labels_from(
            [HAND_BUILT, "repo:x", "agent:engineer"], [])
    assert HAND_BUILT not in [l.lower() for l in child]
    assert "repo:x" in child
    assert "2026-10-07" in err.getvalue()
    assert HAND_BUILT in err.getvalue()


def test_an_explicit_hand_built_on_a_child_is_dropped_loudly():
    err = io.StringIO()
    with redirect_stderr(err):
        child = linear_ops.child_labels_from(["repo:x"], [HAND_BUILT, "web"])
    assert HAND_BUILT not in [l.lower() for l in child]
    assert "web" in child
    assert "2026-10-07" in err.getvalue()


def test_a_child_still_drops_break_glass():
    child = _quiet(linear_ops.child_labels_from,
                   ["repo:x", break_glass.MARKER], [break_glass.MARKER, "web"])
    assert break_glass.MARKER not in [l.lower() for l in child]
    assert "web" in child


# --------------------------------------------------------------------------
# seam 3 — the create paths
# --------------------------------------------------------------------------
def test_create_card_refuses_an_explicit_hand_built_before_any_request():
    fake = RecordingLinear()
    with patch.object(linear_ops, "gql", side_effect=fake.gql):
        with pytest.raises(linear_ops.LinearError) as err:
            _quiet(linear_ops.create_card, "bureau-pipeline: a thing", GOOD_BODY,
                   repo_slug="bureau-pipeline", labels=[HAND_BUILT])
    assert fake.requests == []
    assert fake.created is None
    assert "2026-10-07" in str(err.value)


def test_create_card_still_writes_operator_step():
    fake = RecordingLinear()
    with patch.object(linear_ops, "gql", side_effect=fake.gql):
        _quiet(linear_ops.create_card, "bureau-pipeline: a thing", GOOD_BODY,
               repo_slug="bureau-pipeline",
               labels=[routing_verdict.OPERATOR_STEP_LABEL])
    assert fake.created is not None
    assert f"lbl-{routing_verdict.OPERATOR_STEP_LABEL}" in fake.created["labelIds"]


def test_cmd_create_refuses_label_hand_built_before_any_request(tmp_path):
    body = tmp_path / "body.md"
    body.write_text(GOOD_BODY)
    fake = RecordingLinear()
    with patch.object(linear_ops, "gql", side_effect=fake.gql):
        with pytest.raises(linear_ops.LinearError) as err:
            _quiet(linear_ops.cmd_create, "bureau-pipeline: a thing", str(body),
                   "--repo", "bureau-pipeline", "--label", HAND_BUILT)
    assert fake.requests == []
    assert "2026-10-07" in str(err.value)


def test_cmd_oneoff_refuses_label_hand_built_before_any_request(tmp_path):
    body = tmp_path / "body.md"
    body.write_text(GOOD_BODY)
    fake = RecordingLinear()
    with patch.object(linear_ops, "gql", side_effect=fake.gql):
        with pytest.raises(linear_ops.LinearError) as err:
            _quiet(linear_ops.cmd_oneoff, "bureau-pipeline: a thing", str(body),
                   "--label", "repo:bureau-pipeline", "--label", "agent:devops",
                   "--label", HAND_BUILT)
    assert fake.requests == []
    assert fake.created is None
    assert "2026-10-07" in str(err.value)
