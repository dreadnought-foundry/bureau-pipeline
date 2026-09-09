"""A child's title and its `repo:` label must name the same repo (DRE-3278).

The planner writes a repo into a child card TWICE — once as the
`bureau-pipeline: …` / `portico: …` prefix its brief tells it to use, and once
as the `repo:<slug>` label that actually routes the card. Nothing compared
them, so DRE-3275 was filed titled for one repo (`bureau-pipeline`, where both
its files live) and labelled for another (`repo:agent-bureau`, inherited from
the epic). The label is what routes: the card would have dispatched a build run
against a repo that does not contain the files it names. The operator caught it
by hand before approval.

The mismatch is readable at plan time, from the two strings the planner already
wrote, so the create seam (`linear_ops._reject_unless_creatable`, shared by
`subissue` and `oneoff`) and the post-plan sweep (`validate_card.child_problems`,
run as `check-children` in plan.yml) both refuse it — with both halves quoted,
because "they disagree" without the values is not a fix anyone can make.

Only a KNOWN slug in the title counts. `PROOF: …`, `DEMO: …` and
`SIGN-OFF (OPERATOR): …` are title conventions, not repos, and a card whose
prefix is any other word is prose — reading those as a repo claim would refuse
most of the board.
"""

from __future__ import annotations

import io
import os
import sys
from contextlib import redirect_stdout
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import linear_ops  # noqa: E402
import validate_card  # noqa: E402

from test_subissue_valid_children import FakeLinear, _run_subissue  # noqa: E402


class TestTitleRepoSlug:
    def test_a_known_slug_prefix_is_read_as_a_repo_claim(self):
        assert validate_card.title_repo_slug(
            "bureau-pipeline: add a pipeline-acts row"
        ) == "bureau-pipeline"
        assert validate_card.title_repo_slug("portico: fix the login copy") == "portico"

    def test_case_and_spacing_do_not_hide_the_claim(self):
        assert validate_card.title_repo_slug("Portico : fix the copy") == "portico"
        assert validate_card.title_repo_slug("  BUREAU-PIPELINE: x") == "bureau-pipeline"

    def test_a_title_convention_is_not_a_repo(self):
        for title in (
            "PROOF: the sweep promoted the child",
            "DEMO: show the CEO the new lane",
            "SIGN-OFF (OPERATOR): cut the v9 tag",
        ):
            assert validate_card.title_repo_slug(title) is None, title

    def test_an_unknown_prefix_is_not_a_repo(self):
        assert validate_card.title_repo_slug("planner: teach it the new rule") is None
        assert validate_card.title_repo_slug("Add a helper to linear_ops") is None

    def test_the_slug_must_be_anchored_at_the_start(self):
        # Same anchoring rule the routing verdicts read title conventions with:
        # a slug mentioned mid-title is prose, not a claim about the card.
        assert validate_card.title_repo_slug(
            "Teach the planner that bureau-pipeline: is a real repo"
        ) is None


class TestRepoTitleMismatch:
    def test_the_dre_3275_shape_is_refused_with_both_halves_quoted(self):
        problem = validate_card.repo_title_mismatch(
            "bureau-pipeline: add the pipeline-acts row and the linear_ops helper",
            ["repo:agent-bureau", "agent:engineer"],
        )
        assert problem is not None
        assert "bureau-pipeline" in problem
        assert "repo:agent-bureau" in problem
        assert "add the pipeline-acts row" in problem

    def test_agreement_is_not_a_problem(self):
        assert validate_card.repo_title_mismatch(
            "bureau-pipeline: add the row", ["repo:bureau-pipeline", "agent:devops"]
        ) is None

    def test_an_owner_qualified_label_still_agrees(self):
        # `_repo_label_slugs` strips the owner; the comparison must too, or a
        # `repo:dreadnought-foundry/portico` card is refused for agreeing.
        assert validate_card.repo_title_mismatch(
            "portico: fix the copy", ["repo:dreadnought-foundry/portico"]
        ) is None

    def test_any_matching_repo_label_satisfies_it(self):
        # A child that inherited its epic's repo AND was given its own is not
        # what this check is for — whose label routes is a different question.
        assert validate_card.repo_title_mismatch(
            "bureau-pipeline: add the row",
            ["repo:agent-bureau", "repo:bureau-pipeline"],
        ) is None

    def test_no_repo_label_at_all_is_the_other_check_s_finding(self):
        # An absent label is `missing()`'s WANT_REPO, and reporting it twice in
        # two different vocabularies is how a card gets a fix that is not one.
        assert validate_card.repo_title_mismatch(
            "portico: fix the copy", ["agent:engineer"]
        ) is None

    def test_a_title_naming_no_repo_is_never_a_mismatch(self):
        assert validate_card.repo_title_mismatch(
            "PROOF: the sweep promoted the child", ["repo:agent-bureau"]
        ) is None


class TestTheSweepRefusesTheMismatch:
    def test_child_problems_reports_it(self):
        probs = validate_card.child_problems(
            "bureau-pipeline: add the pipeline-acts row",
            "**Repo:** agent-bureau\n\n# Card\n- [ ] do",
            ["repo:agent-bureau", "agent:engineer"],
        )
        assert any("bureau-pipeline" in p and "repo:agent-bureau" in p for p in probs), probs

    def test_child_problems_stays_quiet_when_they_agree(self):
        assert validate_card.child_problems(
            "bureau-pipeline: add the pipeline-acts row",
            "# Card\n- [ ] do",
            ["repo:bureau-pipeline", "agent:devops"],
        ) == []

    def test_check_children_fails_the_plan_on_a_mismatch(self):
        payload = {
            "issue": {"children": {"nodes": [
                {"identifier": "DRE-3275",
                 "title": "bureau-pipeline: add the pipeline-acts row",
                 "description": "# Card\n- [ ] do",
                 "labels": {"nodes": [
                     {"name": "repo:agent-bureau"}, {"name": "agent:engineer"},
                 ]}},
            ]}}
        }
        with patch.object(linear_ops, "gql", return_value=payload):
            buf = io.StringIO()
            with redirect_stdout(buf):
                with pytest.raises(SystemExit) as exc:
                    validate_card.cmd_check_children("DRE-EPIC")
        assert exc.value.code == 1


class TestTheCreateSeamRefusesTheMismatch:
    def test_subissue_is_rejected_and_nothing_is_created(self, tmp_path):
        fake = FakeLinear(parent_labels=["repo:agent-bureau", "agent:planner"])
        body = "# Card\n\nAdd the row.\n\n## Acceptance criteria\n- [ ] done"
        with patch.object(linear_ops, "gql", side_effect=fake.gql):
            with pytest.raises(linear_ops.LinearError) as exc:
                f = tmp_path / "card.md"
                f.write_text(body)
                with redirect_stdout(io.StringIO()):
                    linear_ops.cmd_subissue(
                        "DRE-EPIC",
                        "bureau-pipeline: add the pipeline-acts row",
                        str(f),
                    )
        message = str(exc.value)
        assert "bureau-pipeline" in message
        assert "repo:agent-bureau" in message
        assert fake.created is None

    def test_the_same_child_is_created_once_the_label_agrees(self, tmp_path):
        fake = FakeLinear(parent_labels=["repo:bureau-pipeline", "agent:planner"])
        body = "# Card\n\nAdd the row.\n\n## Acceptance criteria\n- [ ] done"
        _run_subissue(fake, tmp_path, body)
        assert fake.created is not None

    def test_a_title_with_no_repo_prefix_is_unaffected(self, tmp_path):
        # The guard must not turn every ordinary child into a refusal.
        fake = FakeLinear(parent_labels=["repo:atlas", "agent:planner"])
        body = "# Card\n\nBuild it.\n\n## Acceptance criteria\n- [ ] done"
        _run_subissue(fake, tmp_path, body)
        assert fake.created is not None
