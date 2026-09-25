"""The train's decision as a GitHub deployment message (DRE-4768).

`scripts/release_decision.py` turns a `release_train.Decision` into a
deployment plus one deployment status whose payload IS the decision. Nothing
here needs a token, a runner or a deployment: the two `gh api` posts go
through one seam a test replaces.

What this file pins:

  1. `CODES` is the train's own vocabulary — the set is DERIVED from
     `scripts/release_train.py`'s source, so a code added to the train
     without a row here is a red test rather than a silent gap;
  2. every code in `CODES` produces a record `check_record` accepts, and a
     payload with a missing key, an unknown code, an act outside the four, a
     state that does not follow from the act or a description over the cap is
     refused with the problem NAMED;
  3. `hand_act` answers with the one command a person's hand clears the
     decision with for the eight codes a hand clears, and `None` for every
     other — walked over all of `CODES`;
  4. `write` posts the deployment first and its status second with the exact
     bodies the contract names, and NEVER raises: a refused post is one
     clause and the run is as green as it was;
  5. the committed `docs/release-decision.md` is the render.
"""

from __future__ import annotations

import ast
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import release_decision  # noqa: E402
import release_train  # noqa: E402

TRAIN = ROOT / "scripts" / "release_train.py"
DOC = ROOT / "docs" / "release-decision.md"

UTC = timezone.utc
NOW = datetime(2026, 9, 25, 17, 4, 9, tzinfo=UTC)

ENV = {
    "GITHUB_SERVER_URL": "https://github.com",
    "GITHUB_REPOSITORY": "dreadnought-foundry/agent-bureau",
    "GITHUB_RUN_ID": "41",
    "GITHUB_RUN_ATTEMPT": "2",
    "GITHUB_EVENT_NAME": "workflow_run",
}


# --------------------------------------------------------------------------
# The train's own vocabulary, read off the train.
# --------------------------------------------------------------------------

def train_decisions() -> dict:
    """`{code: act}` for every decision the train constructs, read off its
    SOURCE: every `Decision(` literal's second argument with the act its first
    argument names, plus `_ORDER`'s first column split on `/`.

    Source, not the module: two of the train's codes are built from an
    f-string (`ci-{checks.state}`) and a variable, and only the decision table
    names those in full.
    """
    tree = ast.parse(TRAIN.read_text(encoding="utf-8"))
    found: dict[str, str] = {}

    def act_of(node) -> str:
        # `Decision(REFUSE, …)` — the act is one of the train's four constants.
        return getattr(release_train, node.id) if isinstance(node, ast.Name) else "?"

    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "Decision" and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)):
            found[node.args[1].value] = act_of(node.args[0])
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(getattr(target, "id", "") == "_ORDER" for target in node.targets):
            continue
        for row in node.value.elts:
            for code in row.elts[0].value.split("/"):
                found.setdefault(code.strip(), act_of(row.elts[1]))
    return found


def decision(code: str, **over):
    """A `release_train.Decision` carrying `code`, with the act the train
    constructs it with."""
    fields = {"act": train_decisions()[code], "code": code,
              "reason": f"the sentence the log line carries for {code}"}
    fields.update(over)
    return release_train.Decision(**fields)


def a_record(code: str = "released", **over):
    """The record the train's own surface would send, with overrides."""
    fields = {
        "repo": "dreadnought-foundry/agent-bureau", "surface": "console",
        "phase": "release", "sha": "a" * 40, "head": "b" * 40,
        "deployed": "console/v41", "now": NOW, "env": ENV,
    }
    fields.update(over)
    return release_decision.record(decision(code), **fields)


def test_the_codes_are_exactly_the_ones_the_train_constructs():
    """A code added to the train without a row here is a red test."""
    assert set(release_decision.CODES) == set(train_decisions())
    assert len(release_decision.CODES) == len(set(release_decision.CODES))


def test_every_act_the_train_constructs_maps_to_a_state():
    for code, act in train_decisions().items():
        assert act in release_decision.STATES, code
    assert release_decision.STATES == {
        "release": "success", "refuse": "failure",
        "held": "inactive", "no-op": "inactive",
    }


def test_the_constants_are_the_contract():
    assert release_decision.ENVIRONMENT == "release-train"
    assert release_decision.TASK == "release-train-decision"
    assert release_decision.SCHEMA == "release-train-decision/1"
    assert release_decision.DESCRIPTION_LIMIT == 140


# --------------------------------------------------------------------------
# 1. The record.
# --------------------------------------------------------------------------

def test_the_record_is_the_contract_shape_with_every_key_present():
    made = a_record("released", now=NOW)
    assert made == {
        "schema": "release-train-decision/1",
        "repo": "dreadnought-foundry/agent-bureau",
        "surface": "console",
        "act": "release",
        "code": "released",
        "reason": "the sentence the log line carries for released",
        "phase": "release",
        "sha": "a" * 40,
        "head": "b" * 40,
        "deployed": "console/v41",
        "version": None,
        "hand_act": None,
        "re_arm_at": None,
        "run_id": 41,
        "run_attempt": 2,
        "run_url": "https://github.com/dreadnought-foundry/agent-bureau/actions/runs/41",
        "event": "workflow_run",
        "decided_at": "2026-09-25T17:04:09Z",
    }


def test_the_version_is_the_tag_cut_and_null_when_nothing_was_cut():
    cut = release_decision.record(
        decision("released", tag="console/v42"),
        repo="o/r", surface="console", phase="release", sha="a" * 40,
        head="b" * 40, deployed="console/v41", now=NOW, env=ENV)
    assert cut["version"] == "console/v42"
    assert cut["deployed"] == "console/v41"
    # The plan cannot know the next number — the surface script chooses it.
    assert a_record("spacing")["version"] is None


def test_the_reason_is_the_decisions_own_sentence_never_reworded():
    said = release_train.Decision(
        release_train.NO_OP, "spacing",
        "console was released 3 minutes ago and its spacing is 30 minutes")
    made = release_decision.record(
        said, repo="o/r", surface="console", phase="plan", sha="a" * 40,
        head="b" * 40, deployed=None, now=NOW, env=ENV)
    assert made["reason"] == said.reason
    assert made["deployed"] is None


def test_the_re_arm_minute_rides_when_the_decision_names_one():
    made = release_decision.record(
        decision("spacing", re_arm_at=NOW + timedelta(minutes=27)),
        repo="o/r", surface="console", phase="plan", sha="a" * 40,
        head="b" * 40, deployed=None, now=NOW, env=ENV)
    assert made["re_arm_at"] == "2026-09-25T17:31:09Z"
    assert made["decided_at"] == "2026-09-25T17:04:09Z"


def test_a_surface_object_carries_its_rollback_into_the_hand_act():
    """The train holds a `Surface`; a caller with only a name loses nothing
    but the rollback, which is the surface's own declaration."""
    surface = release_train.surface("console", {
        "tag_series": ["console/v*"], "paths": ["console/"],
        "script": "infra/release-console.sh",
        "rollback": "make rollback-console VERSION=<tag>",
        "spacing_minutes": 30, "window": "always", "auto": True,
        "identity": "bureau-console-release", "record": "tag"})
    made = release_decision.record(
        decision("script-failed"), repo="o/r", surface=surface, phase="release",
        sha="a" * 40, head="b" * 40, deployed="console/v41", now=NOW, env=ENV)
    assert made["surface"] == "console"
    assert made["hand_act"] == "make rollback-console VERSION=<tag>"


def test_a_record_with_no_run_in_the_environment_still_has_every_key():
    made = a_record("unreadable", env={})
    assert made["run_id"] == 0 and made["run_attempt"] == 0
    assert made["run_url"] == ""
    assert made["event"] == ""
    assert release_decision.check_record(made) == []


# --------------------------------------------------------------------------
# 2. The schema test.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("code", release_decision.CODES)
def test_every_code_produces_a_record_check_record_accepts(code):
    made = a_record(code)
    assert made["code"] == code
    assert release_decision.check_record(made) == []


def test_a_missing_key_is_named():
    made = a_record()
    del made["sha"]
    problems = release_decision.check_record(made)
    assert problems == ["sha: missing"]


def test_a_key_the_shape_does_not_carry_is_named():
    made = a_record()
    made["surprise"] = "hello"
    assert release_decision.check_record(made) == ["surprise: not a field of the record"]


def test_a_code_outside_the_trains_vocabulary_is_named():
    made = a_record(code="released")
    made["code"] = "released-ish"
    problems = release_decision.check_record(made)
    assert len(problems) == 1
    assert "code: 'released-ish' is not one of the train's codes" in problems[0]


def test_an_act_outside_the_four_is_named():
    made = a_record()
    made["act"] = "deploy"
    problems = release_decision.check_record(made)
    assert len(problems) == 1
    assert "act: 'deploy' is not one of" in problems[0]
    assert "release" in problems[0] and "no-op" in problems[0]


def test_a_state_that_does_not_follow_from_the_act_is_named():
    """A consumer reading a `deployment_status` has the state beside the
    payload — this is what it checks the pair with."""
    made = a_record("spacing")
    assert release_decision.check_record({**made, "state": "inactive"}) == []
    assert release_decision.check_record({**made, "state": "success"}) == [
        "state: 'success' does not follow from act 'no-op' — that act is 'inactive'"
    ]


def test_a_description_over_the_cap_is_named():
    made = a_record()
    assert release_decision.check_record({**made, "description": "x" * 140}) == []
    problems = release_decision.check_record({**made, "description": "x" * 141})
    assert problems == ["description: 141 characters, over GitHub's cap of 140"]


def test_every_problem_is_named_not_only_the_first():
    made = a_record()
    made["act"] = "deploy"
    made["code"] = "nope"
    del made["head"]
    problems = release_decision.check_record(made)
    assert len(problems) == 3, problems
    assert any(p.startswith("head:") for p in problems)
    assert any(p.startswith("act:") for p in problems)
    assert any(p.startswith("code:") for p in problems)


def test_a_record_that_is_not_an_object_is_refused():
    assert release_decision.check_record(["nope"]) == [
        "record: must be an object"]


# --------------------------------------------------------------------------
# 3. The hand act.
# --------------------------------------------------------------------------

#: The codes a person's hand clears. Every other code in `CODES` is `None` —
#: nobody's hand clears a spacing hold or a commit that is still checking.
BY_HAND = ("held", "auto-false", "walk-bound", "deferred",
           "script-failed", "no-tag", "tag-not-annotated", "tag-elsewhere")


@pytest.mark.parametrize("code", release_decision.CODES)
def test_hand_act_answers_for_the_hand_codes_and_null_for_the_rest(code):
    act = release_decision.hand_act(
        code, repo="dreadnought-foundry/agent-bureau", surface="console",
        rollback="make rollback-console VERSION=<tag>")
    if code in BY_HAND:
        assert act, code
    else:
        assert act is None, code


def test_the_brake_names_the_repository_variable_and_the_organization():
    act = release_decision.hand_act(
        "held", repo="dreadnought-foundry/agent-bureau", surface="console",
        rollback=None)
    assert release_train.ENV_HOLD in act
    assert "gh variable delete" in act
    assert "--repo dreadnought-foundry/agent-bureau" in act
    # The brake resolves against the caller repo AND the organization, so one
    # string names both routes (standards/release-train.md).
    assert "--org dreadnought-foundry" in act


@pytest.mark.parametrize("code", ("auto-false", "walk-bound"))
def test_a_skipped_or_unwalkable_surface_is_cleared_by_a_hand_dispatch(code):
    act = release_decision.hand_act(code, repo="o/r", surface="console",
                                    rollback="make rollback-console")
    assert act == "gh workflow run release-train.yml --repo o/r -f surface=console"


@pytest.mark.parametrize("code", ("script-failed", "no-tag",
                                  "tag-not-annotated", "tag-elsewhere"))
def test_the_failure_codes_hand_back_the_surfaces_declared_rollback(code):
    assert release_decision.hand_act(
        code, repo="o/r", surface="console",
        rollback="make rollback-console VERSION=<tag>"
    ) == "make rollback-console VERSION=<tag>"
    # A surface that declares no rollback has no command to name.
    assert release_decision.hand_act(
        code, repo="o/r", surface="console", rollback=None) is None


def test_a_deferral_points_at_the_line_the_script_printed():
    act = release_decision.hand_act("deferred", repo="o/r", surface="console",
                                    rollback=None)
    assert act.startswith(release_train.DEFERRAL_PREFIX)
    assert "reason" in act


def test_the_record_carries_the_hand_act_for_the_codes_a_hand_clears():
    assert a_record("held")["hand_act"].startswith("gh variable delete")
    assert a_record("auto-false")["hand_act"].startswith("gh workflow run")
    assert a_record("ci-pending")["hand_act"] is None


# --------------------------------------------------------------------------
# 4. The writer that never fails a run.
# --------------------------------------------------------------------------

class FakeGh:
    """The `gh api` seam: records every post, answers what it was given."""

    def __init__(self, answers=None, fail=None):
        self.calls = []
        self.answers = list(answers or [{"id": 7001}, {"id": 8002}])
        self.fail = fail

    def __call__(self, path, body):
        self.calls.append((path, body))
        if self.fail is not None:
            raise self.fail
        return self.answers[len(self.calls) - 1] if self.answers else {}


def test_write_posts_the_deployment_then_its_status_with_the_contract_bodies():
    made = a_record("released", now=NOW)
    gh = FakeGh()
    said = release_decision.write(made, repo="o/r", gh=gh)

    assert said == "decision recorded: deployment 7001 (success)"
    assert [path for path, _ in gh.calls] == [
        "repos/o/r/deployments", "repos/o/r/deployments/7001/statuses"]

    deployment = gh.calls[0][1]
    assert deployment == {
        "ref": made["sha"],
        "environment": "release-train",
        "task": "release-train-decision",
        "auto_merge": False,
        "required_contexts": [],
        "description": "release-train-decision/1 release released console",
        "payload": made,
    }
    status = gh.calls[1][1]
    assert status == {
        "state": "success",
        "environment": "release-train",
        "description": "release-train-decision/1 release released console",
        "log_url": made["run_url"],
        "auto_inactive": False,
    }


def test_the_state_follows_the_act_on_every_code():
    for code in release_decision.CODES:
        made = a_record(code)
        gh = FakeGh()
        said = release_decision.write(made, repo="o/r", gh=gh)
        state = release_decision.STATES[made["act"]]
        assert said == f"decision recorded: deployment 7001 ({state})"
        assert gh.calls[1][1]["state"] == state


def test_the_description_opens_with_the_schema_and_is_cut_to_the_cap():
    made = a_record("released", surface="s" * 200)
    line = release_decision.description(made)
    assert line.startswith(release_decision.SCHEMA)
    assert len(line) == release_decision.DESCRIPTION_LIMIT
    gh = FakeGh()
    release_decision.write(made, repo="o/r", gh=gh)
    assert gh.calls[0][1]["description"] == line
    assert gh.calls[1][1]["description"] == line


def test_a_refused_post_is_one_clause_and_never_a_raise():
    gh = FakeGh(fail=RuntimeError("gh: HTTP 502 Bad Gateway"))
    said = release_decision.write(a_record(), repo="o/r", gh=gh)
    assert said.startswith("decision not recorded: ")
    assert "502" in said
    # The deployment was attempted and nothing else was.
    assert len(gh.calls) == 1


def test_a_403_names_the_permission_the_caller_stub_lacks():
    gh = FakeGh(fail=RuntimeError(
        "gh: Resource not accessible by integration (HTTP 403)"))
    said = release_decision.write(a_record(), repo="o/r", gh=gh)
    assert said == ("decision not recorded: caller stub lacks deployments: "
                    "write (standards/release-train.md)")


def test_a_status_that_is_refused_still_names_the_deployment_that_landed():
    class Half(FakeGh):
        def __call__(self, path, body):
            self.calls.append((path, body))
            if "statuses" in path:
                raise RuntimeError("gh: HTTP 422 Unprocessable Entity")
            return {"id": 7001}

    said = release_decision.write(a_record(), repo="o/r", gh=Half())
    assert said.startswith("decision not recorded: ")
    assert "7001" in said and "422" in said


def test_a_deployment_with_no_id_is_not_a_recorded_decision():
    gh = FakeGh(answers=[{"message": "Conflict"}])
    said = release_decision.write(a_record(), repo="o/r", gh=gh)
    assert said.startswith("decision not recorded: ")
    assert len(gh.calls) == 1


def test_an_invalid_record_is_never_posted():
    made = a_record()
    made["code"] = "made-up"
    gh = FakeGh()
    said = release_decision.write(made, repo="o/r", gh=gh)
    assert said.startswith("decision not recorded: ")
    assert "made-up" in said
    assert gh.calls == []


def test_write_never_raises_whatever_the_seam_does():
    for boom in (ValueError("nope"), KeyError("id"), OSError("no gh on PATH"),
                 TypeError("None is not subscriptable")):
        said = release_decision.write(a_record(), repo="o/r", gh=FakeGh(fail=boom))
        assert said.startswith("decision not recorded: ")
    # Even a record that is not a record at all.
    assert release_decision.write(None, repo="o/r",
                                  gh=FakeGh()).startswith("decision not recorded: ")


def test_the_default_seam_is_the_module_level_gh(monkeypatch):
    gh = FakeGh()
    monkeypatch.setattr(release_decision, "gh", gh)
    said = release_decision.write(a_record(), repo="o/r")
    assert said == "decision recorded: deployment 7001 (success)"
    assert len(gh.calls) == 2


def test_the_failure_detail_goes_to_the_log_in_full():
    lines = []
    gh = FakeGh(fail=RuntimeError("gh: " + "detail " * 60))
    said = release_decision.write(a_record(), repo="o/r", gh=gh,
                                  out=lines.append)
    assert len(said) < len(lines[0])
    assert lines[0].startswith(release_decision.TAG)


# --------------------------------------------------------------------------
# 5. The document is the render.
# --------------------------------------------------------------------------

def test_the_committed_document_is_the_render():
    assert DOC.read_text() == release_decision.render(), (
        "docs/release-decision.md is stale — regenerate it with "
        "`python3 scripts/release_decision.py render`"
    )


def test_the_render_states_the_shape_field_by_field():
    rendered = release_decision.render()
    for name, means in release_decision.FIELDS:
        assert f"`{name}`" in rendered, name
        assert means.split(".")[0][:40] in rendered, name
    assert {name for name, _ in release_decision.FIELDS} == set(a_record())


def test_the_render_states_the_state_mapping_and_the_hand_act_table():
    rendered = release_decision.render()
    for act, state in release_decision.STATES.items():
        assert f"`{act}`" in rendered and f"`{state}`" in rendered
    for code in release_decision.CODES:
        assert f"`{code}`" in rendered, code
    assert release_train.ENV_HOLD in rendered


def test_the_render_states_the_vendor_limits():
    rendered = release_decision.render()
    for needle in ("140", "`auto_merge: false`", "`required_contexts: []`",
                   "`auto_inactive: false`", "DRE-3519", "`task`",
                   "deployment_status", "GITHUB_TOKEN"):
        assert needle in rendered, needle
    # The rule that keeps the train from triggering itself.
    assert "does not start any workflow" in rendered


def test_the_render_says_the_train_cannot_know_the_next_number():
    rendered = release_decision.render()
    assert "the surface script chooses it" in rendered


def test_the_cli_writes_the_document(tmp_path, monkeypatch, capsys):
    out = tmp_path / "release-decision.md"
    monkeypatch.setattr(release_decision, "DOC_PATH", out)
    assert release_decision.main(["render"]) == 0
    assert out.read_text() == release_decision.render()
    assert "wrote" in capsys.readouterr().out


def test_the_document_is_valid_json_where_it_claims_to_be():
    """Every fenced json block in the document parses — the shape is the
    contract the wiring card reads."""
    blocks, buffer, inside = [], [], False
    for line in DOC.read_text().splitlines():
        if line.strip() == "```json":
            inside, buffer = True, []
            continue
        if inside and line.strip() == "```":
            blocks.append("\n".join(buffer))
            inside = False
            continue
        if inside:
            buffer.append(line)
    assert blocks
    for block in blocks:
        json.loads(block)
