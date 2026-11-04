"""The planner job and its classifier read the card through the door (DRE-8903).

Agent Plan's `plan` job set no `BUREAU_READ`, so every reader in it ran with
the door off: on 2026-11-01 the planner sent 2,718 Linear requests on its own
bucket (DRE-8783). Now:

* the `plan` job's job-level `env:` carries the door's four variables, set
  exactly as `reconcile.yml` sets them, and `BUREAU_READ_CARD_MAX_AGE: 60`;
  in `publish` only the `Plan artifact — announce` step does, because its
  `linear_ops.py comment` pre-read is the one Linear read that job makes;
* `planning_classify.roll_up_parent` reads the card's parent off
  `linear_ops.card_via_door` before its `PARENT_QUERY`;
* `critic_score.read_card` reads the card off the door when the door's node
  has no children, and Linear otherwise, because the door lists children by
  internal id.

No test reaches Linear or the console: Linear is a urlopen stand-in that
records every request (the transport seam), and the door and its token issuer
are `tests/bureau_read_fakes.py` on 127.0.0.1.
"""
from __future__ import annotations

import json
import sys
import urllib.request
from contextlib import contextmanager
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(TESTS))

import bureau_read  # noqa: E402
import critic_score  # noqa: E402
import linear_ops  # noqa: E402
import planning_classify  # noqa: E402
import planning_shape  # noqa: E402
from bureau_read_fakes import FakeDoor, FakeIssuer, card, door_env  # noqa: E402
from test_linear_ops_card_via_door import DOOR_ENVS, LinearStub  # noqa: E402

PLAN_YML = ROOT / ".github" / "workflows" / "plan.yml"
RECONCILE_YML = ROOT / ".github" / "workflows" / "reconcile.yml"
ANNOUNCE = "Plan artifact — announce"

#: The door's four variables, as DRE-8898's contract and reconcile.yml set them.
DOOR_VARS = {
    "BUREAU_READ": "${{ vars.BUREAU_READ || 'off' }}",
    "BUREAU_READ_URL": "${{ vars.BUREAU_READ_URL }}",
    "BUREAU_READ_AUDIENCE": "${{ vars.BUREAU_READ_AUDIENCE }}",
    "BUREAU_PIPELINE_REF": "${{ inputs.pipeline_ref }}",
}
MAX_AGE = "BUREAU_READ_CARD_MAX_AGE"
FIVE = (*DOOR_VARS, MAX_AGE)

PARENT = "DRE-5770"
MODEL = "claude-opus-5"
ONE_OFF = json.dumps({"shape": "one-off", "why": "one file, one pull request",
                      "tells": [1, 2], "decision": False})

#: The cards the door holds: DRE-7 with no parent and no children, DRE-8 a
#: child of the roll-up DRE-5770, DRE-9 with a child.
WORLD = {
    "DRE-7": card("DRE-7", "Planning", labels=("repo:bureau-pipeline",),
                  title="A small card", description="Change one line in one file."),
    "DRE-8": card("DRE-8", "Planning", parent=PARENT, title="A roll-up's child"),
    "DRE-9": card("DRE-9", "Planning", children=True, title="An epic"),
    PARENT: card(PARENT, "In Progress", title="A roll-up",
                 comments=(planning_shape.shape_comment("roll-up", "the planner's reading"),)),
}


# ── the workflow ────────────────────────────────────────────────────────────


def _jobs() -> dict:
    return yaml.safe_load(PLAN_YML.read_text(encoding="utf-8"))["jobs"]


def _announce() -> dict:
    (step,) = [s for s in _jobs()["publish"]["steps"] if s.get("name") == ANNOUNCE]
    return step


def _reconcile_door_vars() -> dict:
    steps = yaml.safe_load(RECONCILE_YML.read_text(encoding="utf-8"))["jobs"]["sweep"]["steps"]
    (sweep,) = [s for s in steps if s.get("name") == "Sweep"]
    return {k: sweep["env"][k] for k in DOOR_VARS}


def test_the_door_variables_are_set_as_reconcile_sets_them():
    assert _reconcile_door_vars() == DOOR_VARS


def test_the_plan_job_carries_the_five_at_job_level():
    env = _jobs()["plan"]["env"]
    assert env["LINEAR_KEY_HOME"] == "planner-oauth"
    assert {k: env.get(k) for k in DOOR_VARS} == DOOR_VARS
    assert str(env.get(MAX_AGE)) == "60"


def test_only_the_announce_step_of_publish_carries_them():
    publish = _jobs()["publish"]
    assert not set(FIVE) & set(publish.get("env") or {}), "publish's job-level env"
    assert "permissions" not in publish, "the caller's id-token: write must reach it"
    env = _announce().get("env") or {}
    assert {k: env.get(k) for k in DOOR_VARS} == DOOR_VARS
    assert str(env.get(MAX_AGE)) == "60"
    for step in publish["steps"]:
        if step.get("name") == ANNOUNCE:
            continue
        carried = set(FIVE) & set(step.get("env") or {})
        assert not carried, f"{step.get('name') or step.get('uses')} carries {carried}"


def test_the_announce_step_posts_through_linear_ops_comment():
    assert 'linear_ops.py comment "$EPIC"' in _announce()["run"]


# ── the fakes ───────────────────────────────────────────────────────────────


class Linear(LinearStub):
    """`LinearStub`, answering the classifier's and the critic's own queries
    too. `parents` is the parent Linear names for each card."""

    def __init__(self):
        super().__init__()
        self.parents: dict[str, str | None] = {}

    def answer(self, query: str, variables: dict) -> dict:
        ident = variables.get("id")
        if query == planning_classify.PARENT_QUERY:
            parent = self.parents.get(ident)
            return {"data": {"issue": {"parent": {"identifier": parent} if parent else None}}}
        if query == critic_score.CARD_QUERY:
            node = WORLD[ident]
            kids = [{"identifier": f"{ident}-kid"}] if node["children"]["nodes"] else []
            return {"data": {"issue": {
                "identifier": ident, "title": node["title"],
                "description": node["description"],
                "labels": {"nodes": list(node["labels"]["nodes"])},
                "children": {"nodes": kids}}}}
        return super().answer(query, variables)

    def mutations(self) -> list[str]:
        return [linear_ops._mutation_field(q) for q in self.queries()
                if linear_ops._mutation_field(q)]


@pytest.fixture
def linear(monkeypatch, tmp_path):
    for name in (*DOOR_ENVS, "LINEAR_KEY_HOME"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setattr(linear_ops.time, "sleep", lambda _s: None)
    monkeypatch.setenv("LINEAR_API_KEY", "test-key")
    monkeypatch.delenv(linear_ops.FALLBACK_ENV, raising=False)
    monkeypatch.setenv(linear_ops.IDENTITY_ENV, "fleet")
    linear_ops.reset_pass_cache()
    stub = Linear()
    monkeypatch.setattr(urllib.request, "urlopen", stub)
    yield stub
    linear_ops.reset_pass_cache()


def _resolved(env: dict, door: FakeDoor, issuer: FakeIssuer) -> dict:
    """A workflow `env:` block as a runner would see it, with the repository
    variable `BUREAU_READ=on` and the door and issuer at their fake addresses.
    A literal is taken as written; an expression this test does not name is
    left out."""
    pointed = door_env(door_url=door.url, issuer=issuer)
    out = {}
    for key, value in env.items():
        if key in pointed:
            out[key] = pointed[key]
        elif "${{" not in str(value):
            out[key] = str(value)
    for key in ("ACTIONS_ID_TOKEN_REQUEST_URL", "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
                "GITHUB_REPOSITORY"):
        out[key] = pointed[key]
    return out


@contextmanager
def door_up(monkeypatch, env: dict):
    """A door answering FRESH from WORLD, and the step environment `env`."""
    with FakeIssuer() as issuer, FakeDoor(WORLD) as door:
        resolved = _resolved(env, door, issuer)
        assert resolved["BUREAU_READ"] == "on"
        for key, value in resolved.items():
            monkeypatch.setenv(key, value)
        yield door


def _plan_env() -> dict:
    return _jobs()["plan"]["env"]


def fallback_lines(err: str) -> list[str]:
    return [ln for ln in err.splitlines() if ln.startswith(bureau_read.FALLBACK_TAG)]


# ── the announce step's comment ─────────────────────────────────────────────


def test_the_announce_comment_sends_only_its_comment_create(linear, monkeypatch):
    env = {**_jobs()["publish"]["env"], **_announce()["env"]}
    with door_up(monkeypatch, env) as door:
        linear_ops.cmd_comment("DRE-7", "📄 The plan artifact is published: …")
    assert linear.mutations() == ["commentCreate"]
    assert len(linear.sent) == 1, linear.queries()
    assert linear.sent[0][1]["input"]["issueId"] == "uuid-DRE-7"
    (asked,) = door.asked("/cards")
    assert asked["query"]["ids"] == "DRE-7"
    assert asked["headers"]["x-bureau-max-age"] == "60"


# ── 2. the classifier's own reads ───────────────────────────────────────────


def test_roll_up_parent_reads_the_parent_off_the_door(linear, monkeypatch):
    with door_up(monkeypatch, _plan_env()) as door:
        assert planning_classify.roll_up_parent(linear_ops, "DRE-8") == PARENT
    assert planning_classify.PARENT_QUERY not in linear.queries()
    assert linear.sent == []
    first = door.asked("/cards")[0]
    assert first["query"]["ids"] == "DRE-8"
    assert first["query"]["comments"] == "1"


def test_roll_up_parent_with_no_parent_on_the_door_asks_linear_nothing(linear, monkeypatch):
    with door_up(monkeypatch, _plan_env()):
        assert planning_classify.roll_up_parent(linear_ops, "DRE-7") is None
    assert linear.sent == []


def test_roll_up_parent_door_unknown_reads_linear(linear, monkeypatch, capsys):
    with door_up(monkeypatch, _plan_env()) as door:
        door.routes["/cards"] = door.unknown("stale")
        assert planning_classify.roll_up_parent(linear_ops, "DRE-7") is None
    assert linear.queries() == [planning_classify.PARENT_QUERY]
    assert len(fallback_lines(capsys.readouterr().err)) == 1


def test_roll_up_parent_without_the_door_reads_linear(linear):
    linear.parents["DRE-8"] = PARENT
    linear_ops.reset_pass_cache()
    planning_classify.roll_up_parent(linear_ops, "DRE-8")
    assert linear.queries()[0] == planning_classify.PARENT_QUERY


def test_read_card_off_the_door_is_what_linear_returns(linear, monkeypatch):
    from_linear = critic_score.read_card(linear_ops, "DRE-7")
    assert linear.queries() == [critic_score.CARD_QUERY], "the baseline is a Linear read"
    linear.sent.clear()
    with door_up(monkeypatch, _plan_env()) as door:
        from_door = critic_score.read_card(linear_ops, "DRE-7")
    assert linear.sent == []
    assert from_door == from_linear
    (asked,) = door.asked("/cards")
    assert asked["query"]["comments"] == "1"


def test_read_card_with_a_child_on_the_door_reads_linear(linear, monkeypatch):
    with door_up(monkeypatch, _plan_env()):
        got = critic_score.read_card(linear_ops, "DRE-9")
    assert linear.queries() == [critic_score.CARD_QUERY]
    assert got["has_children"] is True


def test_read_card_door_unknown_reads_linear(linear, monkeypatch, capsys):
    with door_up(monkeypatch, _plan_env()) as door:
        door.routes["/cards"] = door.unknown("stale")
        got = critic_score.read_card(linear_ops, "DRE-7")
    assert linear.queries() == [critic_score.CARD_QUERY]
    assert got["title"] == "A small card"
    assert len(fallback_lines(capsys.readouterr().err)) == 1


def test_a_lops_without_the_door_seam_reads_linear():
    """The suite's own `lops` fakes carry no `card_via_door`: both readers
    then make the Linear read they always made."""
    class Lops:
        def __init__(self):
            self.queries = []

        def gql(self, query, variables=None):
            self.queries.append(query)
            return {"issue": {"identifier": "DRE-7", "title": "t", "description": "",
                              "labels": {"nodes": []}, "children": {"nodes": []},
                              "parent": None}}

    lops = Lops()
    assert planning_classify.roll_up_parent(lops, "DRE-7") is None
    critic_score.read_card(lops, "DRE-7")
    assert lops.queries == [planning_classify.PARENT_QUERY, critic_score.CARD_QUERY]


# ── the classify step, end to end ───────────────────────────────────────────


def test_classify_sends_no_linear_query_before_its_stamp(linear, monkeypatch, tmp_path):
    monkeypatch.setattr(planning_classify, "_pick_model", lambda repo=None: MODEL)
    monkeypatch.setattr(planning_classify, "_call_real",
                        lambda model, prompt, **_kw: ONE_OFF)
    out = tmp_path / "github-output"
    with door_up(monkeypatch, _plan_env()) as door:
        assert planning_classify.main(
            ["classify", "DRE-7", "--github-output", str(out)]) == 0
    assert "shape=one-off" in out.read_text(encoding="utf-8")
    fields = [linear_ops._mutation_field(q) for q in linear.queries()]
    assert "commentCreate" in fields, linear.queries()
    before = linear.queries()[:fields.index("commentCreate")]
    assert before == [], before
    callers = {line.split("caller=")[-1] for line in linear_ops.read_lines()}
    for reader in ("planning_classify.roll_up_parent", "critic_score.read_card",
                   "linear_ops._fetch_thread", "linear_ops._comment_target"):
        assert reader not in callers
    assert {r["query"]["ids"] for r in door.asked("/cards")} == {"DRE-7"}
