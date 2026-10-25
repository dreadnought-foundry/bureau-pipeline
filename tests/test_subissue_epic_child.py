"""`linear_ops.py subissue --epic` creates an epic under an epic (DRE-4698).

The seam rule (DRE-4697, the CEO's rule of 2026-09-23) files a plan that
waits on an observation as CHILD EPICS under the original, and the planner
files them through the same door it files work cards through. That door gave
every child a build role — `agent:engineer`, or `agent:devops` under a
pipeline epic — and a child epic wearing one is a container the relay would
dispatch an engineer at. The retired wave filer stripped the role after the
create; `--epic` never applies it in the first place.

Two refusals close the door both ways, before anything is written:

  * `--epic` on a title that does not start with `[EPIC]` — the flag says
    "container" and the title, which is what every sweep reads, says "work";
  * an `[EPIC]` title WITHOUT `--epic` — the sweep reads the card as an epic
    while it wears the build role the flag would have kept off it.

Everything else is the ordinary child path: the parent must already be an
epic, the child lands in the queue lane (`lane_contract.QUEUE_LANE`, DRE-6744),
`**Blocked by:**` and `--blocked-by` become real relations, and the same
`validate_card` gate runs.

The harness is `FakeLinear` from tests/test_subissue_valid_children.py.
"""

from __future__ import annotations

import ast
import io
import os
import sys
import tokenize
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import lane_contract  # noqa: E402
import linear_ops  # noqa: E402
import validate_card  # noqa: E402

from test_subissue_valid_children import FakeLinear  # noqa: E402

BUILD_ROLES = ("agent:engineer", "agent:devops")
PIPELINE_PARENT = ["repo:bureau-pipeline", "initiative:bureau", "agent:planner"]
BODY = (
    "The first half of the plan, up to the observation.\n\n"
    "## Acceptance criteria\n- [ ] the children are planned and green-lit"
)


def _subissue(fake, tmp_path, title, body=BODY, *flags):
    f = tmp_path / "child-epic.md"
    f.write_text(body)
    buf = io.StringIO()
    with patch.object(linear_ops, "gql", side_effect=fake.gql):
        with redirect_stdout(buf):
            linear_ops.cmd_subissue("DRE-EPIC", title, str(f), *flags)
    return buf.getvalue()


def _refused(fake, tmp_path, title, *flags, body=BODY):
    with pytest.raises(linear_ops.LinearError) as exc:
        _subissue(fake, tmp_path, title, body, *flags)
    return str(exc.value)


def _nothing_written(fake):
    assert fake.created is None
    assert fake.label_create_names == []
    assert fake.relations == []


def _attached(fake):
    """The label names on the created child, in attach order."""
    return [lid[len("lbl-"):] for lid in fake.created["labelIds"]]


# --- the labels a child epic carries -----------------------------------------


class TestTheInheritedLabels:
    @pytest.mark.parametrize("parent", [
        ["repo:bureau-pipeline", "initiative:bureau", "agent:planner"],
        ["repo:atlas", "initiative:bureau", "agent:planner"],
        ["repo:atlas", "initiative:bureau", "agent:devops"],
    ])
    def test_the_epic_switch_gives_the_planner_role_and_no_build_role(self, parent):
        out = linear_ops.parent_inherited_labels(parent, epic=True)
        assert out == [parent[0], "initiative:bureau", "agent:planner"]

    def test_the_default_is_unchanged(self):
        assert linear_ops.parent_inherited_labels(PIPELINE_PARENT) == [
            "repo:bureau-pipeline", "initiative:bureau", "agent:devops"]
        assert linear_ops.child_labels_from(PIPELINE_PARENT, []) == [
            "repo:bureau-pipeline", "initiative:bureau", "agent:devops"]

    def test_an_explicit_repo_label_replaces_the_inherited_one(self):
        out = linear_ops.child_labels_from(
            PIPELINE_PARENT, ["repo:agent-bureau"], epic=True)
        assert out == ["repo:agent-bureau", "initiative:bureau", "agent:planner"]


# --- the create path ---------------------------------------------------------


class TestAChildEpicIsCreated:
    @pytest.mark.parametrize("parent,repo", [
        (PIPELINE_PARENT, "repo:bureau-pipeline"),
        (["repo:atlas", "initiative:bureau", "agent:planner"], "repo:atlas"),
    ])
    def test_planner_role_and_no_build_role(self, tmp_path, parent, repo):
        fake = FakeLinear(parent_labels=parent)
        slug = repo.split(":", 1)[1]
        out = _subissue(fake, tmp_path, f"[EPIC] {slug}: the first half", BODY, "--epic")
        labels = _attached(fake)
        assert labels == [repo, "initiative:bureau", "agent:planner"]
        assert not any(role in labels for role in BUILD_ROLES), labels
        assert f"labels={repo},initiative:bureau,agent:planner" in out

    def test_exactly_one_role_label(self, tmp_path):
        fake = FakeLinear(parent_labels=PIPELINE_PARENT)
        _subissue(fake, tmp_path, "[EPIC] bureau-pipeline: x", BODY, "--epic")
        roles = [l for l in _attached(fake) if l.startswith("agent:")]
        assert roles == ["agent:planner"]

    def test_lands_in_backlog_under_the_parent(self, tmp_path):
        fake = FakeLinear(parent_labels=PIPELINE_PARENT)
        _subissue(fake, tmp_path, "[EPIC] bureau-pipeline: x", BODY, "--epic")
        assert fake.created["stateId"] == "state-backlog"
        assert fake.created["parentId"] == "epic-uuid"
        assert fake.created["title"] == "[EPIC] bureau-pipeline: x"

    def test_blocked_by_line_becomes_a_relation(self, tmp_path):
        fake = FakeLinear(parent_labels=PIPELINE_PARENT)
        body = BODY + "\n\n**Blocked by:** DRE-100"
        out = _subissue(fake, tmp_path, "[EPIC] bureau-pipeline: x", body, "--epic")
        assert fake.relations == [("blk-100", "child-uuid")]
        assert "blockedBy=DRE-100" in out

    def test_blocked_by_flag_becomes_a_relation_and_the_parent_is_stripped(self, tmp_path):
        fake = FakeLinear(parent_labels=PIPELINE_PARENT)
        body = BODY + "\n\n**Blocked by:** DRE-EPIC"
        _subissue(fake, tmp_path, "[EPIC] bureau-pipeline: x", body,
                  "--epic", "--blocked-by", "DRE-100,DRE-EPIC")
        assert fake.relations == [("blk-100", "child-uuid")]

    def test_the_validate_card_gate_runs_and_the_planner_role_satisfies_it(self, tmp_path):
        fake = FakeLinear(parent_labels=PIPELINE_PARENT)
        with patch.object(validate_card, "missing", wraps=validate_card.missing) as spy:
            _subissue(fake, tmp_path, "[EPIC] bureau-pipeline: x", BODY, "--epic")
        assert spy.called
        _, labels = spy.call_args[0]
        assert "agent:planner" in labels
        assert fake.created is not None

    def test_a_child_epic_with_no_resolvable_repo_is_refused(self, tmp_path):
        fake = FakeLinear(parent_labels=["agent:planner"])
        message = _refused(fake, tmp_path, "[EPIC] a slice", "--epic")
        assert "validate_card" in message
        _nothing_written(fake)

    def test_the_parent_must_already_be_an_epic(self, tmp_path):
        # Nothing stamped, no [EPIC] title, no children: a plain card.
        fake = FakeLinear(parent_labels=PIPELINE_PARENT, parent_comments=[])
        message = _refused(fake, tmp_path, "[EPIC] bureau-pipeline: x", "--epic")
        assert "a card has no children" in message
        _nothing_written(fake)


# --- the repo override -------------------------------------------------------


class TestTheRepoOverride:
    def test_explicit_repo_label_replaces_the_parents(self, tmp_path):
        fake = FakeLinear(parent_labels=PIPELINE_PARENT)
        _subissue(fake, tmp_path, "[EPIC] agent-bureau: x", BODY,
                  "--epic", "--label", "repo:agent-bureau")
        labels = _attached(fake)
        assert labels == ["repo:agent-bureau", "initiative:bureau", "agent:planner"]
        assert "repo:bureau-pipeline" not in labels

    def test_a_title_naming_another_repo_is_refused(self, tmp_path):
        fake = FakeLinear(parent_labels=PIPELINE_PARENT)
        message = _refused(fake, tmp_path, "[EPIC] bureau-pipeline: x",
                           "--epic", "--label", "repo:agent-bureau")
        assert "'bureau-pipeline'" in message
        assert "repo:agent-bureau" in message
        _nothing_written(fake)

    def test_an_inherited_repo_the_title_disagrees_with_is_refused(self, tmp_path):
        fake = FakeLinear(parent_labels=PIPELINE_PARENT)
        message = _refused(fake, tmp_path, "[EPIC] agent-bureau: x", "--epic")
        assert "'agent-bureau'" in message
        assert "repo:bureau-pipeline" in message
        _nothing_written(fake)


# --- the two title refusals --------------------------------------------------


class TestTheTitleRefusals:
    @pytest.mark.parametrize("title", [
        "bureau-pipeline: the first half",
        "The first half [EPIC]",
        "EPIC bureau-pipeline: x",
    ])
    def test_the_flag_without_the_prefix_is_refused(self, tmp_path, title):
        fake = FakeLinear(parent_labels=PIPELINE_PARENT)
        message = _refused(fake, tmp_path, title, "--epic")
        assert "[EPIC]" in message
        assert "--epic" in message
        _nothing_written(fake)

    @pytest.mark.parametrize("title", [
        "[EPIC] bureau-pipeline: the first half",
        "[Epic] bureau-pipeline: the first half",
    ])
    def test_the_prefix_without_the_flag_is_refused_naming_it(self, tmp_path, title):
        fake = FakeLinear(parent_labels=PIPELINE_PARENT)
        message = _refused(fake, tmp_path, title)
        assert "--epic" in message
        _nothing_written(fake)

    def test_the_prefix_is_refused_even_with_the_planner_label(self, tmp_path):
        # `--label agent:planner` is not the flag: the inherited build role
        # would still ride along beside it.
        fake = FakeLinear(parent_labels=PIPELINE_PARENT)
        message = _refused(fake, tmp_path, "[EPIC] bureau-pipeline: x",
                           "--label", "agent:planner")
        assert "--epic" in message
        _nothing_written(fake)

    @pytest.mark.parametrize("role", ["agent:engineer", "agent:devops", "agent:ops"])
    def test_a_second_role_beside_the_flag_is_refused(self, tmp_path, role):
        fake = FakeLinear(parent_labels=PIPELINE_PARENT)
        message = _refused(fake, tmp_path, "[EPIC] bureau-pipeline: x",
                           "--epic", "--label", role)
        assert role in message
        _nothing_written(fake)

    def test_an_ordinary_child_is_unaffected(self, tmp_path):
        fake = FakeLinear(parent_labels=PIPELINE_PARENT)
        _subissue(fake, tmp_path, "bureau-pipeline: a work card", BODY)
        assert _attached(fake) == [
            "repo:bureau-pipeline", "initiative:bureau", "agent:devops"]


# --- the lane a child lands in (DRE-6744) ------------------------------------


class _QueueBoard(FakeLinear):
    """The same harness on a board carrying `lanes` — both queue names by
    default, so the lane the child lands in is the one the write named."""

    def __init__(self, lanes=("Backlog", "Queued"), **kw):
        super().__init__(**kw)
        self.lanes = lanes

    def gql(self, query, variables=None, **kw):
        if "workflowStates" in " ".join(query.split()):
            return {"workflowStates": {"nodes": [
                {"id": f"state-{name.lower()}", "name": name, "type": "backlog"}
                for name in self.lanes]}}
        return super().gql(query, variables, **kw)


class TestTheChildLandsInTheQueueLane:
    """`subissue` writes `lane_contract.QUEUE_LANE`, never a lane of its own:
    the planning exit's destination today, and `Queued` the day DRE-6742 moves
    the exit — with no edit here. The sweep reads children through
    `QUEUE_LANES` (DRE-6743), so writer and reader agree under either exit."""

    @pytest.mark.parametrize("flipped", [False, True], ids=["shipped", "flipped"])
    @pytest.mark.parametrize("flags", [(), ("--epic",)], ids=["work", "epic"])
    def test_the_child_lands_in_the_queue_lane(self, tmp_path, monkeypatch,
                                               flipped, flags):
        if flipped:
            monkeypatch.setattr(linear_ops, "QUEUE_LANE", "Queued")
        lane = linear_ops.QUEUE_LANE
        assert lane == ("Queued" if flipped else lane_contract.queue_lane())
        fake = _QueueBoard(parent_labels=PIPELINE_PARENT)
        title = "[EPIC] bureau-pipeline: x" if flags else "bureau-pipeline: a work card"
        _subissue(fake, tmp_path, title, BODY, *flags)
        assert fake.created["stateId"] == f"state-{lane.lower()}"
        assert fake.created["parentId"] == "epic-uuid"

    def test_the_shipped_queue_lane_is_the_planning_exit(self):
        assert lane_contract.QUEUE_LANE == lane_contract.planning_exit()[1]
        assert lane_contract.QUEUE_LANE in lane_contract.QUEUE_LANES

    def test_a_board_without_queued_takes_the_child_in_backlog_and_says_so(
            self, tmp_path, monkeypatch):
        monkeypatch.setattr(linear_ops, "QUEUE_LANE", "Queued")
        fake = _QueueBoard(lanes=("Backlog",), parent_labels=PIPELINE_PARENT)
        out = _subissue(fake, tmp_path, "bureau-pipeline: a work card", BODY)
        assert fake.created["stateId"] == "state-backlog"
        assert "'Queued' is not on the board yet" in out
        assert "pre-rename lane 'Backlog'" in out


class TestTheSourceShape:
    """agent-bureau's phase prover reads this shape off the file (DRE-7053):
    `cmd_subissue` passes `lane=QUEUE_LANE`, the name imported from
    `lane_contract`, and a `#` comment in its body names `Queued` as the one
    deliberate exception to the Planning default (DRE-2858)."""

    SOURCE = Path(linear_ops.__file__).read_text(encoding="utf-8")

    def _function(self, name):
        tree = ast.parse(self.SOURCE)
        return next(n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name == name)

    def test_queue_lane_is_imported_from_lane_contract(self):
        imported = {
            alias.name
            for node in ast.walk(ast.parse(self.SOURCE))
            if isinstance(node, ast.ImportFrom) and node.module == "lane_contract"
            for alias in node.names
        }
        assert "QUEUE_LANE" in imported
        assert linear_ops.QUEUE_LANE is lane_contract.QUEUE_LANE

    def test_cmd_subissue_passes_lane_equals_queue_lane(self):
        lanes = [
            kw.value for node in ast.walk(self._function("cmd_subissue"))
            if isinstance(node, ast.Call)
            for kw in node.keywords if kw.arg == "lane"
        ]
        assert len(lanes) == 1
        assert isinstance(lanes[0], ast.Name) and lanes[0].id == "QUEUE_LANE"

    def test_a_comment_in_cmd_subissue_names_queued_as_the_exception(self):
        fn = self._function("cmd_subissue")
        body = "\n".join(self.SOURCE.splitlines()[fn.lineno - 1:fn.end_lineno])
        comments = [tok.string for tok in tokenize.generate_tokens(io.StringIO(body).readline)
                    if tok.type == tokenize.COMMENT]
        assert any("Queued" in c for c in comments), comments
        text = " ".join(comments)
        assert "DRE-2858" in text and "Planning" in text
