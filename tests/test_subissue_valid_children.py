"""Planner emits complete, valid child cards (DRE-1715).

The planner is an LLM agent; left unguarded it has, in practice, (a) written a
scratch-file PATH ("/tmp/card2.md") where the card CONTENTS belong, (b) created
label-less children, and (c) left build ordering as English prose instead of a
real Linear blockedBy relation. `linear_ops.cmd_subissue` now closes all three
at the create seam, and validates each child through the EXISTING validate_card
gate before creating it. These tests pin that behaviour.

The pure helpers (body_problem / parse_blocked_by / parent_inherited_labels) are
no-I/O and tested directly; the create path is tested by capturing the GraphQL
calls through a fake `gql`.
"""

import io
import os
import sys
from contextlib import redirect_stdout
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import linear_ops  # noqa: E402


# --- (a) path-like / empty / no-markdown bodies are rejected -----------------


class TestBodyProblem:
    def test_bare_tmp_path_rejected(self):
        prob = linear_ops.body_problem("/tmp/card2.md")
        assert prob and "PATH" in prob

    def test_relative_path_rejected(self):
        for p in ("card2.md", "./drafts/card.txt", "~/x.json", "plan.markdown"):
            prob = linear_ops.body_problem(p)
            assert prob and "PATH" in prob, p

    def test_empty_and_whitespace_rejected(self):
        assert linear_ops.body_problem("") == "empty body"
        assert linear_ops.body_problem("   \n  \t") == "empty body"
        assert linear_ops.body_problem(None) == "empty body"

    def test_no_markdown_stub_rejected(self):
        prob = linear_ops.body_problem("just a sentence, no structure at all")
        assert prob and "markdown" in prob

    def test_real_card_passes(self):
        body = (
            "**Repo:** atlas\n\n"
            "Build the thing.\n\n"
            "## Acceptance criteria\n- [ ] it works"
        )
        assert linear_ops.body_problem(body) is None

    def test_heading_or_list_only_passes(self):
        assert linear_ops.body_problem("# Title\nsome prose") is None
        assert linear_ops.body_problem("- a list item\n- another") is None

    def test_prose_mentioning_a_path_is_not_flagged(self):
        # A multi-line real body that merely references a path in prose is fine.
        body = "**Repo:** atlas\n\nEdit config/app.json to add the flag."
        assert linear_ops.body_problem(body) is None


# --- (c) plan ordering becomes blockedBy -------------------------------------


class TestParseBlockedBy:
    def test_bold_blocked_by_line(self):
        body = "Do X.\n**Blocked by:** DRE-100, DRE-101\n## AC\n- [ ] x"
        assert linear_ops.parse_blocked_by(body) == ["DRE-100", "DRE-101"]

    def test_plain_blocked_by_and_case(self):
        assert linear_ops.parse_blocked_by("Blocked by: dre-9") == ["DRE-9"]

    def test_no_blocked_by(self):
        assert linear_ops.parse_blocked_by("totally independent card") == []

    def test_dedup_order_preserving(self):
        body = "**Blocked by:** DRE-5, DRE-5, DRE-3"
        assert linear_ops.parse_blocked_by(body) == ["DRE-5", "DRE-3"]


# --- (b) inherited labels: every child carries repo + role -------------------


class TestParentInheritedLabels:
    def test_repo_and_engineer_by_default(self):
        assert linear_ops.parent_inherited_labels(
            ["repo:atlas", "agent:planner"]
        ) == ["repo:atlas", "agent:engineer"]

    def test_pipeline_epic_yields_devops(self):
        assert linear_ops.parent_inherited_labels(
            ["repo:bureau-pipeline", "agent:planner"]
        ) == ["repo:bureau-pipeline", "agent:devops"]

    def test_parent_devops_label_propagates(self):
        assert linear_ops.parent_inherited_labels(
            ["repo:atlas", "agent:devops"]
        ) == ["repo:atlas", "agent:devops"]

    def test_never_labelless(self):
        # Even a label-less parent gives the child a role (engineer).
        assert linear_ops.parent_inherited_labels([]) == ["agent:engineer"]


# --- flag parsing -------------------------------------------------------------


class TestParseFlags:
    def test_repeatable_label_and_blocked_by(self):
        labels, blockers = linear_ops._parse_flags(
            ("--label", "no-code", "--label", "initiative:bureau", "--blocked-by", "DRE-1, DRE-2")
        )
        assert labels == ["no-code", "initiative:bureau"]
        assert blockers == ["DRE-1", "DRE-2"]

    def test_no_flags(self):
        assert linear_ops._parse_flags(()) == ([], [])


# --- create path: a fake gql captures every call ----------------------------


class FakeLinear:
    """A scriptable stand-in for the Linear GraphQL endpoint. Records the
    create input and any relation creates so tests can assert on labels +
    blockedBy without touching the network."""

    def __init__(self, parent_labels=("repo:atlas", "agent:planner")):
        self.parent_labels = list(parent_labels)
        self.created = None          # the issueCreate input
        self.relations = []          # (blocker_id, child_id) blocks-relations
        self.label_create_names = []  # team labels we had to create
        self._issues = {
            "DRE-EPIC": {"id": "epic-uuid", "team": {"id": "team-1"}, "state": {"name": "Planning"}},
            "DRE-100": {"id": "blk-100", "team": {"id": "team-1"}, "state": {"name": "Backlog"}},
        }

    def gql(self, query, variables=None):
        v = variables or {}
        q = " ".join(query.split())
        # get_issue
        if "issue(id: $id) { id identifier title team" in q:
            return {"issue": self._issues.get(v["id"])}
        # _issue_label_names (parent labels)
        if "issue(id: $id) { labels { nodes { name } } }" in q:
            return {"issue": {"labels": {"nodes": [{"name": n} for n in self.parent_labels]}}}
        # state_id
        if "workflowStates" in q:
            return {"workflowStates": {"nodes": [{"id": "state-backlog", "name": "Backlog"}]}}
        # _team_label_id lookups → pretend none exist yet
        if "team(id: $teamId)" in q and "labels(first: 250)" in q:
            return {"team": {"labels": {"nodes": []}}}
        # issueLabelCreate
        if "issueLabelCreate" in q:
            name = v["input"]["name"]
            self.label_create_names.append(name)
            return {"issueLabelCreate": {"issueLabel": {"id": f"lbl-{name}"}}}
        # issueCreate
        if "issueCreate" in q:
            self.created = v["input"]
            return {"issueCreate": {"issue": {"id": "child-uuid", "identifier": "DRE-200", "url": "u"}}}
        # issueRelationCreate
        if "issueRelationCreate" in q:
            self.relations.append((v["input"]["issueId"], v["input"]["relatedIssueId"]))
            return {"issueRelationCreate": {"success": True}}
        raise AssertionError(f"unexpected query: {q[:80]}")


def _run_subissue(fake, tmp_path, body, *flags):
    f = tmp_path / "card.md"
    f.write_text(body)
    buf = io.StringIO()
    with patch.object(linear_ops, "gql", side_effect=fake.gql):
        with redirect_stdout(buf):
            linear_ops.cmd_subissue("DRE-EPIC", "Build a widget", str(f), *flags)
    return buf.getvalue()


def test_created_child_carries_repo_and_role_labels(tmp_path):
    fake = FakeLinear(parent_labels=["repo:atlas", "agent:planner"])
    body = "**Repo:** atlas\n\nBuild it.\n\n## Acceptance criteria\n- [ ] done"
    out = _run_subissue(fake, tmp_path, body)
    # Two labels created/attached: repo:atlas + agent:engineer (NOT planner).
    assert set(fake.label_create_names) == {"repo:atlas", "agent:engineer"}
    assert fake.created["labelIds"] == ["lbl-repo:atlas", "lbl-agent:engineer"]
    assert "labels=repo:atlas,agent:engineer" in out


def test_created_child_inlines_contents_not_path(tmp_path):
    fake = FakeLinear()
    body = "**Repo:** atlas\n\n# Real card\nBuild it."
    _run_subissue(fake, tmp_path, body)
    # The description is the FILE CONTENTS, never the path string.
    assert fake.created["description"] == body
    assert not fake.created["description"].endswith(".md")


def test_plan_ordering_becomes_blockedby_relation(tmp_path):
    fake = FakeLinear()
    body = (
        "**Repo:** atlas\n\nBuild B.\n\n"
        "## Acceptance criteria\n- [ ] done\n\n"
        "**Blocked by:** DRE-100"
    )
    out = _run_subissue(fake, tmp_path, body)
    # A real Linear relation: blocker DRE-100 blocks the new child.
    assert fake.relations == [("blk-100", "child-uuid")]
    assert "blockedBy=DRE-100" in out


def test_blocked_by_flag_also_works(tmp_path):
    fake = FakeLinear()
    body = "**Repo:** atlas\n\n# Card\nBuild it."
    _run_subissue(fake, tmp_path, body, "--blocked-by", "DRE-100")
    assert fake.relations == [("blk-100", "child-uuid")]


def test_never_blocks_on_parent_epic(tmp_path):
    fake = FakeLinear()
    body = "**Repo:** atlas\n\n# Card\nBuild it.\n\n**Blocked by:** DRE-EPIC"
    _run_subissue(fake, tmp_path, body)
    # The parent epic id is stripped — epics deadlock the dependency gate.
    assert fake.relations == []


def test_pathlike_body_passed_as_arg_is_rejected(tmp_path):
    # The classic mistake: the planner passes "/tmp/card2.md" (a path) directly,
    # not a real file. No such file exists, so it is treated as the body and the
    # path-guard rejects it — the broken child is NEVER created.
    fake = FakeLinear()
    buf = io.StringIO()
    with patch.object(linear_ops, "gql", side_effect=fake.gql):
        with redirect_stdout(buf):
            with pytest.raises(SystemExit) as exc:
                linear_ops.cmd_subissue("DRE-EPIC", "Bad card", "/tmp/card2.md")
    assert "PATH" in str(exc.value)
    assert fake.created is None  # nothing was created


def test_empty_body_file_is_rejected(tmp_path):
    fake = FakeLinear()
    with patch.object(linear_ops, "gql", side_effect=fake.gql):
        with pytest.raises(SystemExit) as exc:
            _run_subissue(fake, tmp_path, "   \n  ")
    assert "empty body" in str(exc.value)
    assert fake.created is None


def test_child_failing_validate_card_is_rejected(tmp_path):
    # (d) each child runs through validate_card. A body with no **Repo:** line AND
    # a parent epic with no repo: label → the child has no resolvable repo and is
    # rejected by the SAME gate, not created broken.
    fake = FakeLinear(parent_labels=["agent:planner"])  # no repo: label to inherit
    body = "# A card with no repo frontmatter\nBuild it."
    with patch.object(linear_ops, "gql", side_effect=fake.gql):
        with pytest.raises(SystemExit) as exc:
            _run_subissue(fake, tmp_path, body)
    assert "validate_card" in str(exc.value)
    assert "Repo:" in str(exc.value)
    assert fake.created is None


# --- (d) the post-plan sweep reuses validate_card over every child -----------


class TestCheckChildren:
    def test_child_problems_reuses_missing_and_body_guard(self):
        import validate_card

        # Clean child → no problems.
        assert validate_card.child_problems(
            "Build it", "**Repo:** atlas\n\n# Card\n- [ ] do", ["repo:atlas", "agent:engineer"]
        ) == []
        # Missing role + path body → both flagged (missing() + body_problem).
        probs = validate_card.child_problems("Bad", "/tmp/card2.md", ["repo:atlas"])
        assert any("agent:" in p for p in probs)
        assert any("PATH" in p for p in probs)

    def test_check_children_passes_when_all_valid(self):
        import validate_card

        payload = {
            "issue": {"children": {"nodes": [
                {"identifier": "DRE-201", "title": "A",
                 "description": "**Repo:** atlas\n\n# C\n- [ ] x",
                 "labels": {"nodes": [{"name": "repo:atlas"}, {"name": "agent:engineer"}]}},
            ]}}
        }
        with patch.object(validate_card, "gql_unused", create=True):
            import linear_ops as _lo
            with patch.object(_lo, "gql", return_value=payload):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    validate_card.cmd_check_children("DRE-EPIC")
        assert "all 1 child" in buf.getvalue()

    def test_check_children_fails_on_invalid_child(self):
        import linear_ops as _lo
        import validate_card

        payload = {
            "issue": {"children": {"nodes": [
                {"identifier": "DRE-202", "title": "Bad",
                 "description": "/tmp/card2.md",   # path body
                 "labels": {"nodes": [{"name": "repo:atlas"}]}},  # no role
            ]}}
        }
        with patch.object(_lo, "gql", return_value=payload):
            with pytest.raises(SystemExit) as exc:
                validate_card.cmd_check_children("DRE-EPIC")
        assert exc.value.code == 1


def test_child_uses_existing_validate_card_gate(tmp_path):
    # The guard reuses validate_card.missing (single source of truth), not a
    # parallel checker — assert it is actually invoked on the create path.
    import validate_card

    fake = FakeLinear()
    body = "**Repo:** atlas\n\n# Card\nBuild it."
    f = tmp_path / "c.md"
    f.write_text(body)
    with patch.object(linear_ops, "gql", side_effect=fake.gql):
        with patch.object(validate_card, "missing", wraps=validate_card.missing) as spy:
            with redirect_stdout(io.StringIO()):
                linear_ops.cmd_subissue("DRE-EPIC", "Card", str(f))
    assert spy.called
    # Called with the child's full resolved label set (inherited repo + role).
    _, called_labels = spy.call_args[0]
    assert "repo:atlas" in called_labels
    assert any(lbl.startswith("agent:") for lbl in called_labels)
