"""A card the planner files under an epic lands in the epic's project (DRE-7675).

The console's Initiatives page counts a project's work by the cards IN that
project. `cmd_subissue` gave a child its parent's `repo:` and role labels but
never its project, so every child landed in none: on 2026-10-19 four epics'
48 cards read "0 of 0 done" while they were being built, until the operator
attached them by hand.

The child now inherits the parent's project — a child epic too, so its own
children inherit in turn. A parent in no project gives a child in none:
nothing guesses one. The project rides on the ONE read of the parent
(`get_issue`), never a second; the fake answers `project` only when that read
selects it, the way Linear answers only what a query asks for.

The harness is `FakeLinear` from tests/test_subissue_valid_children.py.
"""

from __future__ import annotations

import io
import os
import sys
from contextlib import redirect_stdout
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import linear_ops  # noqa: E402

from test_oneoff_card_creation import FakeLinear as FakeOneoffLinear  # noqa: E402
from test_oneoff_card_creation import GOOD_BODY, PLAN_LABELS  # noqa: E402
from test_subissue_valid_children import FakeLinear  # noqa: E402

PARENT = ["repo:atlas", "initiative:bureau", "agent:planner"]
BODY = "**Repo:** atlas\n\nBuild it.\n\n## Acceptance criteria\n- [ ] done"
EPIC_BODY = (
    "The first half of the plan, up to the observation.\n\n"
    "## Acceptance criteria\n- [ ] the children are planned and green-lit"
)


class ProjectLinear(FakeLinear):
    """`FakeLinear` whose parent epic sits in `project_id` (None: in no
    project). The parent read answers `project` only when it selects it."""

    def __init__(self, project_id, **kw):
        super().__init__(parent_labels=PARENT, **kw)
        self.project = {"id": project_id} if project_id is not None else None
        self.parent_reads = 0

    def gql(self, query, variables=None, **kw):
        q = " ".join(query.split())
        if "issue(id: $id) { id identifier title team" in q:
            issue = dict(self._issues.get((variables or {})["id"]) or {})
            if (variables or {}).get("id") == "DRE-EPIC":
                self.parent_reads += 1
                if "project { id }" in q:
                    issue["project"] = self.project
            return {"issue": issue}
        return super().gql(query, variables, **kw)


def _subissue(fake, tmp_path, title="Build a widget", body=BODY, *flags):
    f = tmp_path / "card.md"
    f.write_text(body)
    with patch.object(linear_ops, "gql", side_effect=fake.gql):
        with redirect_stdout(io.StringIO()):
            linear_ops.cmd_subissue("DRE-EPIC", title, str(f), *flags)
    return fake.created


def test_a_child_lands_in_its_parent_epics_project(tmp_path):
    fake = ProjectLinear("proj-desk-4")
    created = _subissue(fake, tmp_path)
    assert created["projectId"] == "proj-desk-4"
    assert created["parentId"] == "epic-uuid"


def test_a_child_epic_lands_in_its_parent_epics_project(tmp_path):
    fake = ProjectLinear("proj-desk-4")
    created = _subissue(fake, tmp_path, "[EPIC] atlas: the first half",
                        EPIC_BODY, "--epic")
    assert created["projectId"] == "proj-desk-4"
    assert created["title"] == "[EPIC] atlas: the first half"


def test_a_parent_in_no_project_gives_a_child_with_no_project_key(tmp_path):
    fake = ProjectLinear(None)
    created = _subissue(fake, tmp_path)
    assert created is not None
    assert "projectId" not in created


def test_a_child_epic_under_a_parent_in_no_project_has_no_project_key(tmp_path):
    fake = ProjectLinear(None)
    created = _subissue(fake, tmp_path, "[EPIC] atlas: x", EPIC_BODY, "--epic")
    assert created is not None
    assert "projectId" not in created


def test_the_project_rides_on_the_one_read_of_the_parent(tmp_path):
    fake = ProjectLinear("proj-desk-4")
    _subissue(fake, tmp_path)
    assert fake.parent_reads == 1


def test_a_oneoff_is_created_with_no_project_key(tmp_path):
    fake = FakeOneoffLinear()
    f = tmp_path / "card.md"
    f.write_text(GOOD_BODY)
    with patch.object(linear_ops, "gql", side_effect=fake.gql):
        with redirect_stdout(io.StringIO()):
            linear_ops.cmd_oneoff("Build a widget", str(f), *PLAN_LABELS)
    assert fake.created is not None
    assert "projectId" not in fake.created
