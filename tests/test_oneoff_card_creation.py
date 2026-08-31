"""The planner can create a PARENTLESS card, with `repo:` taken from the plan
(DRE-2754, second half).

`cmd_subissue` requires a parent and derives the child's `repo:` label from
`parent_inherited_labels()`. A one-off has no parent by definition, so the
one-off route Wave 1.5 designs — Planning → structural check → the build
queue, never reaching the CEO — had no producer at all, and no source for the `repo:` label
that DRE-2744 makes the only routing key.

`cmd_oneoff` is that producer. It is `cmd_subissue` minus the parent: the same
path-guard on the body, the same `validate_card` gate and the same
`--blocked-by` relation handling — with labels supplied by the plan rather than
inherited. It lands in Planning, not Backlog (DRE-2858): a card minted here has
been through nobody's planning run of its own, so it owes the classification
Planning makes.
"""

import io
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import linear_ops  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

PLAN_LABELS = (
    "--label", "repo:atlas",
    "--label", "initiative:bureau",
    "--label", "agent:engineer",
)

GOOD_BODY = "**Repo:** atlas\n\nBuild the widget.\n\n## Acceptance criteria\n- [ ] it works"


class FakeLinear:
    """A scriptable stand-in for the Linear endpoint — no parent lookup, since
    a one-off has none."""

    def __init__(self):
        self.created = None
        self.relations = []
        self.label_create_names = []
        self._issues = {
            "DRE-100": {"id": "blk-100", "team": {"id": "team-1"}, "state": {"name": "Backlog"}},
        }

    def gql(self, query, variables=None):
        v = variables or {}
        q = " ".join(query.split())
        if "teams(filter:" in q:
            return {"teams": {"nodes": [{"id": "team-1"}]}}
        if "issue(id: $id) { id identifier title team" in q:
            return {"issue": self._issues.get(v["id"])}
        if "workflowStates" in q:
            return {
                "workflowStates": {
                    "nodes": [
                        {"id": "state-planning", "name": "Planning", "type": "backlog"},
                        {"id": "state-backlog", "name": "Backlog", "type": "backlog"},
                        {"id": "state-triage", "name": "Triage", "type": "unstarted"},
                    ]
                }
            }
        if "team(id: $teamId)" in q and "labels(first: 250)" in q:
            return {"team": {"labels": {"nodes": []}}}
        if "issueLabelCreate" in q:
            name = v["input"]["name"]
            self.label_create_names.append(name)
            return {"issueLabelCreate": {"issueLabel": {"id": f"lbl-{name}"}}}
        if "issueCreate" in q:
            self.created = v["input"]
            return {"issueCreate": {"issue": {"id": "one-uuid", "identifier": "DRE-300", "url": "u"}}}
        if "issueRelationCreate" in q:
            self.relations.append((v["input"]["issueId"], v["input"]["relatedIssueId"]))
            return {"issueRelationCreate": {"success": True}}
        raise AssertionError(f"unexpected query: {q[:80]}")


def _run_oneoff(fake, tmp_path, body, *flags):
    f = tmp_path / "oneoff.md"
    f.write_text(body)
    buf = io.StringIO()
    with patch.object(linear_ops, "gql", side_effect=fake.gql):
        with redirect_stdout(buf):
            linear_ops.cmd_oneoff("Build a widget", str(f), *flags)
    return buf.getvalue()


class TestOneOffCreation:
    def test_created_card_has_no_parent(self, tmp_path):
        fake = FakeLinear()
        _run_oneoff(fake, tmp_path, GOOD_BODY, *PLAN_LABELS)
        # The whole point: a card with no parent epic. Never a stray parentId.
        assert "parentId" not in fake.created

    def test_repo_label_comes_from_the_plan(self, tmp_path):
        fake = FakeLinear()
        out = _run_oneoff(fake, tmp_path, GOOD_BODY, *PLAN_LABELS)
        assert fake.created["labelIds"] == [
            "lbl-repo:atlas",
            "lbl-initiative:bureau",
            "lbl-agent:engineer",
        ]
        assert "labels=repo:atlas,initiative:bureau,agent:engineer" in out

    def test_lands_in_planning(self, tmp_path):
        # DRE-2858: a card this seam mints carries no shape stamp and no routing
        # verdict, so it lands in the lane that makes one — never straight into
        # the lane the rest of the system reads as ready work.
        fake = FakeLinear()
        _run_oneoff(fake, tmp_path, GOOD_BODY, *PLAN_LABELS)
        assert fake.created["stateId"] == "state-planning"

    def test_body_is_inlined_contents_not_a_path(self, tmp_path):
        fake = FakeLinear()
        _run_oneoff(fake, tmp_path, GOOD_BODY, *PLAN_LABELS)
        assert fake.created["description"] == GOOD_BODY

    def test_blocked_by_becomes_a_real_relation(self, tmp_path):
        fake = FakeLinear()
        body = GOOD_BODY + "\n\n**Blocked by:** DRE-100"
        out = _run_oneoff(fake, tmp_path, body, *PLAN_LABELS)
        assert fake.relations == [("blk-100", "one-uuid")]
        assert "blockedBy=DRE-100" in out


class TestOneOffIsGatedByTheSameChecks:
    def test_pathlike_body_is_rejected(self, tmp_path):
        fake = FakeLinear()
        with patch.object(linear_ops, "gql", side_effect=fake.gql):
            with pytest.raises(linear_ops.LinearError) as exc:
                linear_ops.cmd_oneoff("Bad card", "/tmp/card2.md", *PLAN_LABELS)
        assert "PATH" in str(exc.value)
        assert fake.created is None

    def test_a_card_with_no_repo_label_is_rejected(self, tmp_path):
        # No parent to inherit from, so a missing repo: label is fatal — and it
        # is the SAME validate_card gate, not a parallel checker.
        fake = FakeLinear()
        body = "Build the widget.\n\n## Acceptance criteria\n- [ ] it works"
        with pytest.raises(linear_ops.LinearError) as exc:
            _run_oneoff(fake, tmp_path, body, "--label", "agent:engineer",
                        "--label", "initiative:bureau")
        assert "repo" in str(exc.value).lower()
        assert fake.created is None

    def test_a_card_with_no_role_label_is_rejected(self, tmp_path):
        fake = FakeLinear()
        with pytest.raises(linear_ops.LinearError) as exc:
            _run_oneoff(fake, tmp_path, GOOD_BODY, "--label", "repo:atlas",
                        "--label", "initiative:bureau")
        assert "agent:" in str(exc.value)
        assert fake.created is None

    def test_a_card_with_no_initiative_label_is_rejected(self, tmp_path):
        # Same as a planned child: without initiative:* the reconcile
        # dependency gate never promotes it (DRE-1722).
        fake = FakeLinear()
        with pytest.raises(linear_ops.LinearError) as exc:
            _run_oneoff(fake, tmp_path, GOOD_BODY, "--label", "repo:atlas",
                        "--label", "agent:engineer")
        assert "initiative" in str(exc.value).lower()
        assert fake.created is None


class TestOneOffIsReachable:
    def test_cli_registers_the_command(self):
        src = (ROOT / "scripts" / "linear_ops.py").read_text()
        assert '"oneoff": cmd_oneoff,' in src

    def test_the_planner_is_told_about_it(self):
        # A command the planner never hears about is not a path. plan.yml's
        # prompt documents `subissue` for the epic route; the one-off route
        # needs the same treatment or it has no producer.
        plan = (ROOT / ".github" / "workflows" / "plan.yml").read_text()
        assert "linear_ops.py oneoff" in plan
        assert "one-off" in plan.lower()
