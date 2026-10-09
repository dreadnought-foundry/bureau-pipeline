"""The create seam files an operator step held under a reason the sweep lifts (DRE-6428).

The planner files an operator step — work a person does once the cards before
it are finished — as `needs-human` + `no-code` + `agent:devops`, through
`linear_ops.py subissue` or `oneoff` (`briefs/planner.md`). The seam used to
write the two labels at creation and nothing else: no stamp, and no registry
row for the write, so the hold read `manual` and nothing ever lifted it
(DRE-6408). Now both commands mark the card `operator-step` and stamp the hold
`operator-step`, which the sweep lifts once every blocker is terminal
(DRE-6426, DRE-6427).

`create_card` is deliberately NOT a route: `model_adoption_actions` files its
question card through it already held, under its own `manual` row, and that
card is a decision for the CEO rather than a step waiting on blockers.
"""

from __future__ import annotations

import io
import os
import re
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import hold  # noqa: E402
import linear_ops  # noqa: E402
import model_adoption_actions  # noqa: E402
import routing_verdict  # noqa: E402

from test_subissue_valid_children import FakeLinear  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

#: The stamp the card fixes, exactly.
STAMP = "🔒 hold: reason=operator-step at=none lifts=blockers-terminal by=linear_ops.py"

BODY = ("# Raise the org Actions budget\n\n**What to do**\n\nAn operator raises "
        "the budget in the org settings.\n\n## Acceptance criteria\n"
        "- [ ] the budget reads the new figure")


class Board(FakeLinear):
    """FakeLinear plus the card the create made, and its write layer.

    The created card's labels are read off the create's `labelIds`; `add_label`
    and `cmd_comment` are stubbed against that card, so what the seam writes
    after the create is what a reader of the card would see."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.labels: list[str] = []
        self.comments: list[str] = []
        self.label_writes: list[str] = []

    def gql(self, query, variables=None):
        q = " ".join(query.split())
        if "teams(filter:" in q:
            return {"teams": {"nodes": [{"id": "team-1"}]}}
        if "workflowStates" in q:
            return {"workflowStates": {"nodes": [
                {"id": "state-backlog", "name": "Backlog", "type": "backlog"},
                {"id": "state-planning", "name": "Planning", "type": "backlog"},
            ]}}
        out = super().gql(query, variables)
        if "issueCreate" in q:
            self.labels = [i[len("lbl-"):] for i in (variables or {})["input"]["labelIds"]]
        return out

    def add_label(self, identifier, label_name):
        assert identifier == "DRE-200", identifier
        self.label_writes.append(label_name)
        if label_name.lower() not in {name.lower() for name in self.labels}:
            self.labels.append(label_name)

    def cmd_comment(self, identifier, body, *flags):
        assert identifier == "DRE-200", identifier
        self.comments.append(body)


def _run(board, fn, *args):
    out = io.StringIO()
    err = io.StringIO()
    with patch.object(linear_ops, "gql", side_effect=board.gql), \
            patch.object(linear_ops, "add_label", side_effect=board.add_label), \
            patch.object(linear_ops, "cmd_comment", side_effect=board.cmd_comment), \
            redirect_stdout(out), redirect_stderr(err):
        result = fn(*args)
    return result, out.getvalue(), err.getvalue()


def _subissue(board, tmp_path, *flags):
    f = tmp_path / "card.md"
    f.write_text(BODY)
    return _run(board, linear_ops.cmd_subissue, "DRE-EPIC",
                "Raise the org Actions budget", str(f), *flags)


def _oneoff(board, tmp_path, *flags):
    f = tmp_path / "card.md"
    f.write_text(BODY)
    return _run(board, linear_ops.cmd_oneoff, "Raise the org Actions budget",
                str(f), *flags)


PARENT = ["repo:bureau-pipeline", "initiative:bureau", "agent:planner"]


class TestTheContract:
    def test_the_names_are_the_ones_the_sweep_reads(self):
        assert hold.OPERATOR_STEP_REASON == "operator-step"
        assert routing_verdict.OPERATOR_STEP_LABEL == "operator-step"
        assert hold.stamp_line(hold.OPERATOR_STEP_REASON, None, "linear_ops.py") == STAMP


class TestSubissueFilesTheStepHeld:
    def test_the_pair_is_marked_and_stamped(self, tmp_path):
        board = Board(parent_labels=PARENT)
        issue, out, _ = _subissue(board, tmp_path, "--label", "needs-human",
                                  "--label", "no-code", "--label", "agent:devops")
        assert issue["identifier"] == "DRE-200"
        assert board.created is not None
        assert "operator-step" in board.labels
        assert board.comments == [STAMP]
        assert hold.reason_of(board.labels, board.comments) == "operator-step"
        # The receipt names the stamp, so the planner's log says what was held.
        assert STAMP in out

    def test_operator_step_is_not_written_twice(self, tmp_path):
        board = Board(parent_labels=PARENT)
        _subissue(board, tmp_path, "--label", "needs-human", "--label", "no-code",
                  "--label", "operator-step")
        assert "operator-step" not in board.label_writes
        assert board.labels.count("operator-step") == 1
        assert board.comments == [STAMP]

    def test_a_child_without_the_pair_is_untouched(self, tmp_path):
        board = Board(parent_labels=PARENT)
        _subissue(board, tmp_path, "--label", "size:S")
        assert board.created is not None
        assert board.label_writes == []
        assert board.comments == []
        assert "operator-step" not in board.labels

    def test_no_code_alone_is_not_an_operator_step(self, tmp_path):
        board = Board(parent_labels=PARENT)
        _subissue(board, tmp_path, "--label", "no-code")
        assert board.label_writes == []
        assert board.comments == []

    def test_a_failed_hold_write_never_unmakes_the_card(self, tmp_path):
        # The card already exists when the hold is written. Raising would make
        # the planner's command fail on a card that was filed, and a retry
        # files a second one — so the seam returns the card and says, loudly,
        # that it reads `manual` and how to stamp it.
        board = Board(parent_labels=PARENT)

        def broken(*_a, **_k):
            raise linear_ops.LinearError("Linear said no")

        board.cmd_comment = broken
        issue, _, err = _subissue(board, tmp_path, "--label", "needs-human",
                                  "--label", "no-code")
        assert issue["identifier"] == "DRE-200"
        assert "DRE-200" in err and "manual" in err
        assert "hold.py apply DRE-200 --reason operator-step --by linear_ops.py" in err


class TestOneoffFilesTheStepHeld:
    LABELS = ("--label", "repo:agent-bureau", "--label", "agent:devops")

    def test_the_pair_is_marked_and_stamped(self, tmp_path):
        board = Board()
        _, out, _ = _oneoff(board, tmp_path, *self.LABELS,
                            "--label", "needs-human", "--label", "no-code")
        assert board.created is not None
        assert board.created["stateId"] == "state-planning"
        assert "operator-step" in board.labels
        assert board.comments == [STAMP]
        assert hold.reason_of(board.labels, board.comments) == "operator-step"
        assert STAMP in out

    def test_a_oneoff_without_the_pair_is_untouched(self, tmp_path):
        board = Board()
        _oneoff(board, tmp_path, *self.LABELS)
        assert board.created is not None
        assert board.label_writes == []
        assert board.comments == []


class TestTheQuestionCardIsNotARoute:
    def test_create_card_files_the_question_card_unstamped(self):
        board = Board()
        _run(board, lambda: linear_ops.create_card(
            "Adopt the new model?", BODY, repo_slug="bureau-pipeline",
            labels=list(model_adoption_actions.QUESTION_LABELS),
            lane=model_adoption_actions.QUESTION_LANE))
        assert board.created is not None
        assert {"needs-human", "no-code"} <= set(board.labels)
        assert "operator-step" not in board.labels
        assert board.label_writes == []
        assert board.comments == []
        # Its own row stands: a label with no stamp is a person's hold.
        assert hold.reason_of(board.labels, board.comments) == "manual"

    def test_create_card_never_calls_the_helper(self):
        import inspect
        assert "_file_operator_hold" not in inspect.getsource(linear_ops.create_card)
        assert "_file_operator_hold" not in inspect.getsource(linear_ops._create_card)


class TestTheRegistryNamesTheSite:
    def test_check_reports_no_problems(self):
        assert hold.problems(hold.load(), root=str(ROOT)) == []

    def test_exactly_one_site_in_linear_ops(self):
        sites = [s for s in hold.discover() if s.file == "scripts/linear_ops.py"]
        assert len(sites) == 1, [s.where for s in sites]
        assert sites[0].scope == "_file_operator_hold"

    def test_its_row(self):
        site = next(s for s in hold.discover() if s.file == "scripts/linear_ops.py")
        rows = [r for r in hold.load()["sites"] if hold.row_matches(r, site)]
        assert len(rows) == 1
        row = rows[0]
        assert row["scope"] == "_file_operator_hold"
        assert row["reasons"] == [{
            "reason": "operator-step", "lifts": "blockers-terminal",
            "tried_first": "none — the card is filed held by design; the sweep "
                           "lifts it when every blocker is terminal",
        }]
        assert row["readers"] == ["fix-dispatch", "medic", "limit-recovery"]
        assert "sweep" not in row["readers"]


class TestThePlannerBriefSaysSo:
    def _bullet(self) -> str:
        text = (ROOT / "briefs" / "planner.md").read_text(encoding="utf-8")
        match = re.search(r"- \*\*Human/infra work is NOT agent:engineer\*\*"
                          r"[^\n]*(?:\n  [^\n]*)*", text)
        assert match, "the brief's operator-step bullet is gone"
        return " ".join(match.group(0).split())

    def test_the_labels_the_planner_passes_are_unchanged(self):
        bullet = self._bullet()
        assert "`needs-human` + `no-code` + `agent:devops`" in bullet

    def test_the_old_claim_is_gone(self):
        text = " ".join((ROOT / "briefs" / "planner.md").read_text(encoding="utf-8").split())
        stale = "tells the reconcile sweep and promotion gate to leave it for the operator"
        found = stale in text
        assert not found, stale

    def test_it_says_the_seam_stamps_and_the_sweep_carries_it(self):
        bullet = self._bullet()
        for phrase in ("`operator-step`", "Hand-work", "blocker", "DRE-6408"):
            assert phrase in bullet, phrase
        assert re.search(r"seam[^.]*stamps the hold `operator-step`", bullet), bullet


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
