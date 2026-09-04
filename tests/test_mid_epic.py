"""RED-first tests: `agent:planner` says who OWNS a card, not that it IS an epic
(DRE-3038).

`mid_epic.is_epic()` answered "epic" for any card carrying `agent:planner`. Every
card the relay dispatches to `plan.yml` from Planning carries that label — the
relay requires it — so `routing_verdict.route()` answered "epic, no verdict" for
every ONE-OFF, the whole role-label → title → criteria precedence never ran, and
`planning_route._one_off_check()` fell to a fixed FLEET sentence. Observed on
DRE-3018 and DRE-3020: both carry zero `- [ ]` items, for which
`criteria_verdict()` documents NEEDS WORK, and both were stamped FLEET.

The label means the planner owns the card. The **shape stamp** (DRE-2843) says
what the card is. So:

  1. `is_epic()` no longer takes labels at all — it cannot read `agent:planner`
     because it is not given it.
  2. A caller that knows the card's shape passes it, and the stamp decides.
     Title and children are the fallback, for a card nothing has classified.
  3. Every caller is WALKED here, and the walk is DISCOVERED from the source
     rather than listed — a new caller that reads epic-ness some other way
     fails this file rather than shipping.

Run: cd bureau-pipeline && python3 -m pytest tests/test_mid_epic.py -v
"""
from __future__ import annotations

import ast
import inspect
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")

import linear_ops  # noqa: E402
import mid_epic  # noqa: E402
import planning_shape  # noqa: E402
import routing_verdict  # noqa: E402

SCRIPTS = ROOT / "scripts"

# The card the finding was made on: a real one-off, sitting in Planning, wearing
# the label the relay requires and stamped for what it actually is.
PROBE_TITLE = (
    "PROOF-FD-4b — a one-off card in Planning WITH agent:planner: expect a "
    "routing verdict and Backlog, never Green Light (throwaway, safe to cancel)"
)
PROBE_LABELS = ["repo:agent-bureau-demo", "agent:planner"]
PROBE_BODY = (ROOT / "tests" / "fixtures" / "dre-3018-fd-4b-one-off-probe.md").read_text(
    encoding="utf-8"
)


def _stamp(name: str) -> str:
    """The comment that IS a shape, written by the module that owns it."""
    return planning_shape.shape_comment(name, "classified at Planning's front door")


# ===========================================================================
# 1: the label is not the classification
# ===========================================================================
class TestTheLabelIsNotTheClassification:
    def test_is_epic_is_not_given_the_labels_at_all(self):
        """The strongest form of "it stops reading `agent:planner`": the
        parameter is gone, so no future edit can quietly read it back."""
        params = inspect.signature(mid_epic.is_epic).parameters
        assert "parent_labels" not in params
        assert not any("label" in name for name in params), (
            f"is_epic still takes a label parameter: {list(params)}"
        )

    def test_a_planner_owned_card_with_no_children_is_not_an_epic(self):
        assert mid_epic.is_epic(PROBE_TITLE, has_children=False) is False

    def test_the_shape_stamp_decides_when_the_card_carries_one(self):
        assert mid_epic.is_epic(PROBE_TITLE, False, shape="one-off") is False
        assert mid_epic.is_epic("the intake front door", False, shape="epic") is True

    def test_the_stamp_is_read_before_the_title_and_the_children(self):
        """"Read the stamp first and the title/children second" — so a card
        stamped one-off is a one-off even if somebody typed [EPIC] into its
        title or hung a child off it by hand."""
        assert mid_epic.is_epic("[EPIC] mis-titled", True, shape="one-off") is False
        assert mid_epic.is_epic("a plain title", False, shape="epic") is True

    def test_the_epic_shape_it_reads_is_a_shape_the_vocabulary_carries(self):
        """`mid_epic` cannot import `planning_shape` — that would close a cycle
        through `routing_verdict` — so the word is named there and bound here.
        Rename the shape in `config/planning-shapes.json` and this fails rather
        than the stamp quietly ceasing to mean anything."""
        import planning_route

        assert mid_epic.EPIC_SHAPE in planning_shape.shapes()
        stops = [r.shape for r in planning_route.routes() if r.owes_green_light]
        assert stops == [mid_epic.EPIC_SHAPE], (
            "the shape that stops for a human is the shape that is an epic"
        )

    def test_the_title_and_children_still_answer_an_unstamped_card(self):
        assert mid_epic.is_epic("[EPIC] the front door", False) is True
        assert mid_epic.is_epic("[epic] lowercase counts too", False) is True
        assert mid_epic.is_epic("an epic that already has children", True) is True
        assert mid_epic.is_epic("a plain card", False) is False


# ===========================================================================
# 2: every caller, walked — and the walk is discovered, not listed
# ===========================================================================
def _call_sites() -> set:
    """Every `is_epic` call in `scripts/`, as (module, enclosing function).

    Discovered rather than enumerated: the caller nobody remembered is exactly
    the one still reading `agent:planner` as epic-ness.
    """
    found: set = set()
    for path in sorted(SCRIPTS.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for inner in ast.walk(node):
                if not isinstance(inner, ast.Call):
                    continue
                func = inner.func
                name = (
                    func.attr if isinstance(func, ast.Attribute)
                    else func.id if isinstance(func, ast.Name)
                    else None
                )
                if name == "is_epic":
                    found.add((path.stem, node.name))
    return found


# The callers this file walks below. Kept in step with `_call_sites()` by the
# first test: a new reader of `is_epic` fails here until it is walked too.
WALKED = {
    ("mid_epic", "subissue_refusal"),
    ("routing_verdict", "route"),
    ("linear_ops", "cmd_subissue"),
}


class TestEveryCallerIsWalked:
    def test_the_walk_covers_every_call_site_in_the_repo(self):
        assert _call_sites() == WALKED, (
            "a caller of is_epic is not walked below — every reader of "
            "epic-ness owes the one-off case a test"
        )

    def test_route_gives_a_planner_owned_one_off_a_real_verdict(self):
        """`routing_verdict.route` — the widest blast radius. It answered
        "epic, no verdict" for every one-off, which is what sent the whole
        precedence chain unread."""
        decision = routing_verdict.route(
            PROBE_TITLE, PROBE_BODY, PROBE_LABELS, False, shape="one-off"
        )
        assert decision.source != "epic"
        assert decision.verdict == "NEEDS WORK", (
            "DRE-3018 carries zero `- [ ]` items, and that is the criteria "
            "branch's own answer"
        )

    def test_route_still_answers_epic_for_a_card_stamped_epic(self):
        decision = routing_verdict.route(
            "the intake front door", PROBE_BODY, PROBE_LABELS, False, shape="epic"
        )
        assert decision.source == "epic"
        assert decision.verdict is None

    def test_subissue_refusal_refuses_a_child_of_a_planner_owned_one_off(self):
        """`mid_epic.subissue_refusal` — giving a one-off children is the
        reclassification the guard exists to stop, and `agent:planner` was
        waving it through."""
        refusal = mid_epic.subissue_refusal(PROBE_TITLE, False, shape="one-off")
        assert refusal is not None
        assert "sibling" in refusal.lower()

    def test_subissue_refusal_lets_a_stamped_epic_through(self):
        assert mid_epic.subissue_refusal(
            "the intake front door", False, shape="epic"
        ) is None

    def test_cmd_subissue_refuses_a_child_of_a_planner_owned_one_off(self):
        """`linear_ops.cmd_subissue` — the create seam, where the refusal is an
        enforcement rather than a convention. Nothing is written."""
        # `initiative:` included so the only thing that can refuse this is the
        # epic guard — the create seam's own validation would otherwise.
        card = _FakeParent(
            title=PROBE_TITLE, labels=[*PROBE_LABELS, "initiative:bureau"],
            comments=[_stamp("one-off")],
        )
        with card.patched(), pytest.raises(linear_ops.LinearError) as err:
            linear_ops.cmd_subissue("DRE-3018", "a finding", "## What\n- work")
        assert "agent:planner" in str(err.value)
        assert card.created == []

    def test_cmd_subissue_still_creates_the_first_child_of_a_stamped_epic(self):
        """Guard the guard, and the case the label used to cover: an epic whose
        title says nothing and whose children do not exist yet. The stamp is
        what carries it now, so the planner's own path is untouched."""
        card = _FakeParent(
            title="the intake front door",
            labels=["agent:planner", "repo:atlas", "initiative:bureau"],
            comments=[_stamp("epic")],
        )
        with card.patched():
            created = linear_ops.cmd_subissue("DRE-3013", "a child", "## What\n- work")
        assert created["identifier"] == "DRE-3040"


class _FakeParent:
    """One parent epic as `cmd_subissue` reads it, with every write recorded."""

    def __init__(self, *, title: str, labels: list, comments: list):
        self.title = title
        self.labels = list(labels)
        self.comments = list(comments)
        self.created: list = []

    def _gql(self, query, variables=None):
        self.created.append(variables)
        return {
            "issueCreate": {
                "success": True,
                "issue": {"id": "uuid-3040", "identifier": "DRE-3040",
                          "url": "https://linear.app/x/DRE-3040"},
            }
        }

    def patched(self):
        return _patch_all(
            patch.object(linear_ops, "get_issue", return_value={
                "id": "uuid-parent", "identifier": "DRE-PARENT",
                "title": self.title, "team": {"id": "team-1"},
                "labels": {"nodes": [{"name": n} for n in self.labels]},
            }),
            patch.object(linear_ops, "_issue_label_names", return_value=self.labels),
            patch.object(linear_ops, "_issue_has_children", return_value=False),
            patch.object(linear_ops, "comment_bodies", return_value=self.comments),
            patch.object(linear_ops, "state_id", return_value="state-backlog"),
            patch.object(linear_ops, "_team_label_ids", return_value=["l1"]),
            patch.object(linear_ops, "gql", side_effect=self._gql),
        )


class _patch_all:
    """Several patches as one context manager — the nesting these tests would
    otherwise repeat five times over."""

    def __init__(self, *patches):
        self._patches = patches

    def __enter__(self):
        for p in self._patches:
            p.__enter__()
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.__exit__(*exc)
        return False
