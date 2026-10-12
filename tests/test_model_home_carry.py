"""A merged change to config/models.yaml's choices is carried into the home (DRE-6986).

Until the console writes the home itself (DRE-6976), a save on Settings ›
Agents & models still rides today's route: a pull request editing
`config/models.yaml`. Once the home is seeded every run reads the home and not
that file, so `scripts/model_home_carry.py` compares the file's choices at a
push's `before` and `after` commits and carries the six changes the page makes
into the home, setting by setting.

Every case builds its two files from the frozen fixture
(`tests/fixtures/model-config-frozen/`), the page's own edits made by
`model_config_change.edit_text`, and runs `carry` against a stub home: the
GitHub API and the pulls read are replaced, so nothing here reaches the
network. Every model id is derived from the fixture, never a literal.

Run: cd bureau-pipeline && python3 -m pytest tests/test_model_home_carry.py -v
"""

from __future__ import annotations

import copy
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import check_action_pins  # noqa: E402
import check_model_home_scope  # noqa: E402
import model_config_change as mcc  # noqa: E402
import model_fallback as mf  # noqa: E402
import model_home  # noqa: E402
import model_home_carry as carry_mod  # noqa: E402
import proof_reach  # noqa: E402
from model_fixture import (FIXTURE_DIR, fixture_config, fixture_home,  # noqa: E402
                           fixture_prices, fixture_text, ladder)

WORKFLOW = ROOT / ".github" / "workflows" / "model-home-carry.yml"
OLD_ROUTE = ROOT / ".github" / "workflows" / "model-config-change.yml"
AFTER_SHA = "0123456789abcdef0123456789abcdef01234567"
TODAY = "2026-10-12"


# --------------------------------------------------------------------------- #
# The stubs                                                                    #
# --------------------------------------------------------------------------- #

class StubHome:
    """The variable's API: each GET answers the next document in `docs` (the
    last one again once they run out), or `status` with no document; each
    PATCH is recorded and answered 204."""

    def __init__(self, *docs, status=200):
        self.docs = [copy.deepcopy(d) for d in docs]
        self.status = status
        self.gets = 0
        self.patches: list[dict] = []
        self.tokens: list[tuple[str, str]] = []

    def __call__(self, method, url, token, body=None):
        assert url == model_home.API_URL, url
        self.tokens.append((method, token))
        if method == "GET":
            if self.status != 200:
                return self.status, '{"message": "Not Found"}'
            doc = self.docs[min(self.gets, len(self.docs) - 1)]
            self.gets += 1
            return 200, json.dumps({"name": model_home.VARIABLE, "value": json.dumps(doc)})
        assert method == "PATCH", method
        self.patches.append(json.loads(body))
        return 204, ""

    def written(self) -> dict:
        assert len(self.patches) == 1, self.patches
        patch = self.patches[0]
        assert patch["name"] == model_home.VARIABLE
        return json.loads(patch["value"])


def _side() -> dict:
    cfg = fixture_config()
    ids = lambda rungs: [r["model"] if isinstance(r, dict) else r for r in rungs or []]  # noqa: E731
    return {"excluded": ids(cfg.get("excluded")), "retired": ids(cfg.get("retired")),
            "session_agents": list(cfg.get("session_agents") or []),
            "agents": model_home._registry_names(FIXTURE_DIR / "agents.yaml")}


@pytest.fixture
def stubs(monkeypatch):
    """The stub home and pulls read, the tokens set, the fixture's facts."""
    state = {"home": StubHome(fixture_home()), "pulls": []}
    monkeypatch.setattr(carry_mod, "_http", lambda *a, **k: state["home"](*a, **k))
    monkeypatch.setattr(carry_mod, "_pulls", lambda sha: state["pulls"])
    monkeypatch.setattr(carry_mod, "_facts", lambda: (
        fixture_prices(), _side(), mf.declared_repo_slugs()))
    monkeypatch.setenv(carry_mod.READ_TOKEN_ENV, "read-token")
    monkeypatch.setenv(carry_mod.WRITE_TOKEN_ENV, "write-token")
    return state


def _edited(*changes) -> str:
    text = fixture_text()
    for change in changes:
        text = mcc.edit_text(text, change, TODAY)
    return text


def _dumped(edit) -> str:
    raw = fixture_config()
    edit(raw)
    return yaml.safe_dump(raw, sort_keys=False)


def _run(before: str, after: str, capsys) -> tuple[int, str]:
    rc = carry_mod.carry(before, after, AFTER_SHA)
    out = capsys.readouterr()
    return rc, out.out + out.err


def _assert_fresh(doc: dict) -> None:
    when = datetime.strptime(doc["written_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    assert doc["written_at"] != fixture_home()["written_at"]
    assert abs((datetime.now(timezone.utc) - when).total_seconds()) < 300
    assert doc["written_by"] in ("console", "operator")


def _tagged(out: str) -> list[str]:
    return [line for line in out.splitlines() if line.startswith(carry_mod.TAG)]


# --------------------------------------------------------------------------- #
# The six changes the page makes                                               #
# --------------------------------------------------------------------------- #

def test_set_default_model_moves_the_model_to_the_front(stubs, capsys):
    rungs = ladder(fixture_config(), "workhorse")
    chosen = rungs[2]
    rc, out = _run(fixture_text(), _edited(
        {"op": "set_default_model", "group": "workhorse", "model": chosen}), capsys)
    assert rc == 0, out
    doc = stubs["home"].written()
    home_rungs = ladder(fixture_home(), "workhorse")
    assert doc["groups"]["workhorse"]["models"] == [chosen] + [m for m in home_rungs if m != chosen]
    expected = fixture_home()
    expected["groups"]["workhorse"]["models"] = doc["groups"]["workhorse"]["models"]
    assert {k: v for k, v in doc.items() if k not in ("written_at", "written_by")} == \
        {k: v for k, v in expected.items() if k not in ("written_at", "written_by")}
    _assert_fresh(doc)
    tagged = _tagged(out)
    assert len(tagged) == 1, out
    assert tagged[0].startswith(f"{carry_mod.TAG} carried 1 setting(s) from {AFTER_SHA[:7]} — written ")
    assert f"groups.workhorse.models[0]: {home_rungs[0]} → {chosen}" in out


def test_set_default_model_inserts_a_model_new_to_the_group(stubs, capsys):
    workhorse = ladder(fixture_config(), "workhorse")
    new = next(m for m in ladder(fixture_config(), "judgement") if m not in workhorse)
    rc, out = _run(fixture_text(), _edited(
        {"op": "set_default_model", "group": "workhorse", "model": new}), capsys)
    assert rc == 0, out
    assert stubs["home"].written()["groups"]["workhorse"]["models"] == \
        [new] + ladder(fixture_home(), "workhorse")


def test_only_the_first_rung_is_carried_past_a_rung_the_adoption_replaced(stubs, capsys):
    # DRE-6943: the daily adoption replaced a Builders rung in the home. The
    # file still holds the old rung, and a merge choosing another default must
    # not put it back.
    workhorse = ladder(fixture_config(), "workhorse")
    successor = next(m for m in ladder(fixture_config(), "judgement") if m not in workhorse)
    replaced = workhorse[1]
    home = fixture_home()
    home["groups"]["workhorse"]["models"] = [successor if m == replaced else m for m in workhorse]
    stubs["home"] = StubHome(home)
    chosen = workhorse[2]
    rc, out = _run(fixture_text(), _edited(
        {"op": "set_default_model", "group": "workhorse", "model": chosen}), capsys)
    assert rc == 0, out
    models = stubs["home"].written()["groups"]["workhorse"]["models"]
    assert models == [chosen] + [m for m in home["groups"]["workhorse"]["models"] if m != chosen]
    assert replaced not in models
    assert successor in models


def test_move_agent_sets_the_agents_group(stubs, capsys):
    agent = fixture_config()["ungrouped"][0]
    rc, out = _run(fixture_text(), _edited(
        {"op": "move_agent", "agent": agent, "group": "advisory"}), capsys)
    assert rc == 0, out
    doc = stubs["home"].written()
    assert doc["agents"][agent] == "advisory"
    assert {n: g for n, g in doc["agents"].items() if n != agent} == fixture_home()["agents"]
    _assert_fresh(doc)
    assert f"agents.{agent}: (none) → advisory" in out


def test_move_agent_removes_an_agent_that_left_every_group(stubs, capsys):
    agent, group = next(iter(fixture_config()["agents"].items()))
    assert fixture_home()["agents"][agent] == group
    after = _dumped(lambda raw: raw["agents"].pop(agent))
    rc, out = _run(fixture_text(), after, capsys)
    assert rc == 0, out
    doc = stubs["home"].written()
    assert agent not in doc["agents"]
    _assert_fresh(doc)
    assert f"agents.{agent}: {group} → (none)" in out


def test_add_group_adds_the_group_with_its_label_purpose_and_ladder(stubs, capsys):
    model = ladder(fixture_config(), "advisory")[0]
    change = {"op": "add_group", "group": "research", "label": "Researchers",
              "purpose": "Read the sources a card names.", "model": model}
    rc, out = _run(fixture_text(), _edited(change), capsys)
    assert rc == 0, out
    doc = stubs["home"].written()
    assert doc["groups"]["research"] == {"label": "Researchers",
                                         "purpose": "Read the sources a card names.",
                                         "models": [model]}
    assert {k: v for k, v in doc["groups"].items() if k != "research"} == fixture_home()["groups"]
    _assert_fresh(doc)


def test_set_project_model_sets_the_projects_model(stubs, capsys):
    model = ladder(fixture_config(), "workhorse")[2]
    rc, out = _run(fixture_text(), _edited(
        {"op": "set_project_model", "project": "atlas", "group": "workhorse",
         "model": model}), capsys)
    assert rc == 0, out
    doc = stubs["home"].written()
    assert doc["projects"]["atlas"] == {"workhorse": model}
    assert doc["projects"]["agent-bureau-demo"] == fixture_home()["projects"]["agent-bureau-demo"]
    _assert_fresh(doc)
    assert f"projects.atlas.workhorse: (none) → {model}" in out


def test_clear_project_model_removes_the_projects_model(stubs, capsys):
    standing = fixture_home()["projects"]["agent-bureau-demo"]["workhorse"]
    rc, out = _run(fixture_text(), _edited(
        {"op": "clear_project_model", "project": "agent-bureau-demo",
         "group": "workhorse"}), capsys)
    assert rc == 0, out
    doc = stubs["home"].written()
    assert "agent-bureau-demo" not in doc["projects"]
    _assert_fresh(doc)
    assert f"projects.agent-bureau-demo.workhorse: {standing} → (none)" in out


def test_add_agent_carries_nothing(stubs, capsys):
    rc, out = _run(fixture_text(), _edited(
        {"op": "add_agent", "agent": "scout", "about": "Scouts what a card needs"}), capsys)
    assert rc == 0, out
    assert stubs["home"].patches == []
    tagged = _tagged(out)
    assert len(tagged) == 1 and tagged[0].startswith(f"{carry_mod.TAG} nothing carried — "), out
    assert "scout" in out


def test_several_changes_in_one_merge_are_one_patch(stubs, capsys):
    agent = fixture_config()["ungrouped"][0]
    chosen = ladder(fixture_config(), "advisory")[1]
    rc, out = _run(fixture_text(), _edited(
        {"op": "move_agent", "agent": agent, "group": "workhorse"},
        {"op": "set_default_model", "group": "advisory", "model": chosen}), capsys)
    assert rc == 0, out
    doc = stubs["home"].written()
    assert doc["agents"][agent] == "workhorse"
    assert doc["groups"]["advisory"]["models"][0] == chosen
    assert _tagged(out)[0].startswith(f"{carry_mod.TAG} carried 2 setting(s)")


def test_a_home_already_holding_the_change_is_not_written_again(stubs, capsys):
    # A re-run of a run that wrote: the settings are in the home already.
    agent = fixture_config()["ungrouped"][0]
    home = fixture_home()
    home["agents"][agent] = "advisory"
    stubs["home"] = StubHome(home)
    rc, out = _run(fixture_text(), _edited(
        {"op": "move_agent", "agent": agent, "group": "advisory"}), capsys)
    assert rc == 0, out
    assert stubs["home"].patches == []
    assert _tagged(out)[0].startswith(f"{carry_mod.TAG} nothing carried — ")


# --------------------------------------------------------------------------- #
# What is never carried                                                        #
# --------------------------------------------------------------------------- #

def test_a_ladder_reordered_below_its_first_rung_is_not_carried(stubs, capsys):
    def swap(raw):
        rungs = raw["ladders"]["workhorse"]
        rungs[1], rungs[2] = rungs[2], rungs[1]
    rc, out = _run(fixture_text(), _dumped(swap), capsys)
    assert rc == 0, out
    assert stubs["home"].patches == []
    assert _tagged(out)[0].startswith(f"{carry_mod.TAG} nothing carried — ")
    assert any(line.startswith("not carried:") and "workhorse" in line and "below its first rung" in line
               for line in out.splitlines()), out


def test_a_group_removed_is_not_carried(stubs, capsys):
    model = ladder(fixture_config(), "advisory")[0]
    before = _edited({"op": "add_group", "group": "research", "label": "Researchers",
                      "purpose": "Read the sources a card names.", "model": model})
    rc, out = _run(before, fixture_text(), capsys)
    assert rc == 0, out
    assert stubs["home"].patches == []
    assert any(line.startswith("not carried:") and "research" in line and "removed" in line
               for line in out.splitlines()), out


def test_a_whole_projects_key_removed_is_not_carried(stubs, capsys):
    rc, out = _run(fixture_text(), _dumped(lambda raw: raw.pop("projects")), capsys)
    assert rc == 0, out
    assert stubs["home"].patches == []
    assert any(line.startswith("not carried:") and "`projects`" in line
               for line in out.splitlines()), out


@pytest.mark.parametrize("key", ["ladders", "kinds", "agents", "ungrouped"])
def test_every_whole_choice_key_removed_is_not_carried(stubs, capsys, key):
    rc, out = _run(fixture_text(), _dumped(lambda raw: raw.pop(key)), capsys)
    assert rc == 0, out
    assert stubs["home"].patches == []
    assert any(line.startswith("not carried:") and f"`{key}`" in line
               for line in out.splitlines()), out


def test_a_carried_change_beside_one_not_carried_is_still_carried(stubs, capsys):
    agent = fixture_config()["ungrouped"][0]
    raw = yaml.safe_load(_edited({"op": "move_agent", "agent": agent, "group": "advisory"}))
    raw.pop("projects")
    rc, out = _run(fixture_text(), yaml.safe_dump(raw, sort_keys=False), capsys)
    assert rc == 0, out
    doc = stubs["home"].written()
    assert doc["agents"][agent] == "advisory"
    assert doc["projects"] == fixture_home()["projects"]
    assert any(line.startswith("not carried:") and "`projects`" in line
               for line in out.splitlines()), out


# --------------------------------------------------------------------------- #
# The write, guarded                                                           #
# --------------------------------------------------------------------------- #

def test_a_home_with_no_document_yet_carries_nothing_and_is_green(stubs, capsys):
    stubs["home"] = StubHome(status=404)
    agent = fixture_config()["ungrouped"][0]
    rc, out = _run(fixture_text(), _edited(
        {"op": "move_agent", "agent": agent, "group": "advisory"}), capsys)
    assert rc == 0, out
    assert stubs["home"].patches == []
    tagged = _tagged(out)
    assert len(tagged) == 1 and tagged[0].startswith(f"{carry_mod.TAG} nothing carried — "), out
    assert "defaults in the code" in tagged[0]
    assert "seed" in tagged[0]


def test_a_document_the_rules_refuse_is_not_written_and_is_red(stubs, capsys):
    # A hand edit the page would never make: the critic out of the Reviewers.
    after = _dumped(lambda raw: raw["agents"].__setitem__("critic", "workhorse"))
    rc, out = _run(fixture_text(), after, capsys)
    assert rc == 1, out
    assert stubs["home"].patches == []
    assert "The critic must be in the advisory group" in out


def test_a_home_written_between_the_reads_gets_the_change_applied_again(stubs, capsys):
    agent = fixture_config()["ungrouped"][0]
    first = fixture_home()
    newer = fixture_home()
    newer["written_at"] = "2026-10-12T09:00:00Z"
    newer["written_by"] = "daily-adoption"
    advisory = newer["groups"]["advisory"]["models"]
    newer["groups"]["advisory"]["models"] = advisory[1:] + advisory[:1]
    stubs["home"] = StubHome(first, newer, newer)
    rc, out = _run(fixture_text(), _edited(
        {"op": "move_agent", "agent": agent, "group": "advisory"}), capsys)
    assert rc == 0, out
    doc = stubs["home"].written()
    assert doc["agents"][agent] == "advisory"
    assert doc["groups"]["advisory"]["models"] == newer["groups"]["advisory"]["models"]
    _assert_fresh(doc)
    assert doc["written_at"] != newer["written_at"]


def test_a_home_written_twice_between_the_reads_is_red_naming_both(stubs, capsys):
    agent = fixture_config()["ungrouped"][0]
    second, third = fixture_home(), fixture_home()
    second["written_at"] = "2026-10-12T09:00:00Z"
    third["written_at"] = "2026-10-12T09:00:05Z"
    stubs["home"] = StubHome(fixture_home(), second, third)
    rc, out = _run(fixture_text(), _edited(
        {"op": "move_agent", "agent": agent, "group": "advisory"}), capsys)
    assert rc == 1, out
    assert stubs["home"].patches == []
    assert "2026-10-12T09:00:00Z" in out and "2026-10-12T09:00:05Z" in out


def test_an_unreadable_home_is_red(stubs, capsys):
    stubs["home"] = StubHome(status=403)
    agent = fixture_config()["ungrouped"][0]
    rc, out = _run(fixture_text(), _edited(
        {"op": "move_agent", "agent": agent, "group": "advisory"}), capsys)
    assert rc == 1, out
    assert stubs["home"].patches == []
    assert "403" in out


def test_the_read_and_the_write_use_their_own_tokens(stubs, capsys):
    agent = fixture_config()["ungrouped"][0]
    rc, out = _run(fixture_text(), _edited(
        {"op": "move_agent", "agent": agent, "group": "advisory"}), capsys)
    assert rc == 0, out
    tokens = stubs["home"].tokens
    assert {t for m, t in tokens if m == "GET"} == {"read-token"}
    assert [t for m, t in tokens if m == "PATCH"] == ["write-token"]


@pytest.mark.parametrize("ref, role", [
    ("agent/model-config-1a2b3c4d5e6f", "console"),
    ("agent/DRE-6986-model-home-carry", "operator"),
    (None, "operator"),
])
def test_written_by_follows_the_merged_branch(stubs, capsys, ref, role):
    stubs["pulls"] = [] if ref is None else [{"number": 7, "head": {"ref": ref}}]
    agent = fixture_config()["ungrouped"][0]
    rc, out = _run(fixture_text(), _edited(
        {"op": "move_agent", "agent": agent, "group": "advisory"}), capsys)
    assert rc == 0, out
    assert stubs["home"].written()["written_by"] == role
    assert _tagged(out)[0].endswith(f" by {role}")


def test_the_console_branch_is_the_old_routes_branch():
    assert carry_mod.CONSOLE_BRANCH == mcc.BRANCH_PREFIX == "agent/model-config-"


# --------------------------------------------------------------------------- #
# The CLI                                                                      #
# --------------------------------------------------------------------------- #

def test_the_cli_reads_both_commits_from_git(stubs, capsys, monkeypatch):
    agent = fixture_config()["ungrouped"][0]
    files = {"b" * 40: fixture_text(),
             "a" * 40: _edited({"op": "move_agent", "agent": agent, "group": "advisory"})}
    monkeypatch.setattr(carry_mod, "_show", lambda sha: files[sha])
    rc = carry_mod.main(["carry", "--before", "b" * 40, "--after", "a" * 40])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert stubs["home"].written()["agents"][agent] == "advisory"
    assert f"from {'a' * 7}" in out


def test_a_push_with_no_before_commit_carries_nothing(stubs, capsys, monkeypatch):
    monkeypatch.setattr(carry_mod, "_show", lambda sha: pytest.fail("read " + sha))
    rc = carry_mod.main(["carry", "--before", "0" * 40, "--after", "a" * 40])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert stubs["home"].patches == []
    assert _tagged(out)[0].startswith(f"{carry_mod.TAG} nothing carried — ")


def test_the_cli_refuses_a_missing_argument():
    assert carry_mod.main(["carry", "--before", "b" * 40]) == 2


def test_the_script_declares_it_reads_the_home():
    assert carry_mod.READS_MODEL_HOME == ("*",)
    assert carry_mod.TAG == "model-home-carry:"


# --------------------------------------------------------------------------- #
# The workflow                                                                 #
# --------------------------------------------------------------------------- #

def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())


def _steps() -> list[dict]:
    return _workflow()["jobs"]["carry"]["steps"]


def _step(name: str) -> dict:
    return next(s for s in _steps() if s.get("name") == name)


def test_it_runs_on_a_push_to_main_touching_the_file():
    doc = _workflow()
    on = doc.get("on", doc.get(True))
    assert list(on) == ["push"]
    assert on["push"] == {"branches": ["main"], "paths": ["config/models.yaml"]}


def test_it_runs_one_at_a_time_without_cancelling():
    assert _workflow()["concurrency"] == {"group": "model-home-carry",
                                          "cancel-in-progress": False}


def test_it_uses_the_old_routes_runner_and_python_setup():
    old = yaml.safe_load(OLD_ROUTE.read_text())["jobs"]["change"]
    job = _workflow()["jobs"]["carry"]
    assert job["runs-on"] == old["runs-on"]
    setup = next(s for s in old["steps"] if "setup-python-cached" in str(s.get("uses")))
    assert setup in job["steps"]


def test_it_checks_out_the_history_the_before_commit_needs():
    checkout = next(s for s in _steps() if str(s.get("uses", "")).startswith("actions/checkout@"))
    assert checkout["with"]["fetch-depth"] == 0


def test_the_two_mints():
    pinned = yaml.safe_load(OLD_ROUTE.read_text())["jobs"]["change"]["steps"][0]["uses"]
    names = [s.get("name") for s in _steps()]
    read = _step("Mint the model-home read token")
    write = _step("Mint the model-home write token")
    carry = names.index("Carry the merged choices into the home")
    assert names.index(read["name"]) < carry and names.index(write["name"]) < carry
    for mint in (read, write):
        assert mint["uses"] == pinned
        assert mint["with"]["owner"] == "dreadnought-foundry"
        assert mint["with"]["repositories"] == "bureau-pipeline"
    assert read["id"] == "model_home"
    assert read["continue-on-error"] is True
    assert read["with"]["permission-variables"] == "read"
    assert write["id"] == "model_home_write"
    assert "continue-on-error" not in write
    assert write["with"]["permission-variables"] == "write"


def test_the_carry_step_passes_before_after_and_the_tokens():
    step = _step("Carry the merged choices into the home")
    env = step["env"]
    assert env["BUREAU_MODEL_HOME_TOKEN"] == "${{ steps.model_home.outputs.token }}"
    assert env["BUREAU_MODEL_HOME_WRITE_TOKEN"] == "${{ steps.model_home_write.outputs.token }}"
    assert env["GH_TOKEN"] == "${{ github.token }}"
    assert env["BEFORE"] == "${{ github.event.before }}"
    assert env["AFTER"] == "${{ github.event.after }}"
    assert step["run"].strip() == \
        'python3 scripts/model_home_carry.py carry --before "$BEFORE" --after "$AFTER"'
    assert "${{" not in step["run"]


def test_the_scope_check_holds_the_carry_step():
    violations, stats = check_model_home_scope.check_tree(ROOT)
    assert violations == []
    found, scripts = check_model_home_scope.readers(ROOT / "scripts")
    packages = check_model_home_scope.star_packages(scripts)
    steps = check_model_home_scope.reading_steps(_workflow(), scripts, packages)
    assert [s[2]["name"] for s in steps] == ["Carry the merged choices into the home"]


def test_the_scope_check_refuses_the_carry_step_without_its_token():
    doc = _workflow()
    for step in doc["jobs"]["carry"]["steps"]:
        (step.get("env") or {}).pop("BUREAU_MODEL_HOME_TOKEN", None)
    found, scripts = check_model_home_scope.readers(ROOT / "scripts")
    packages = check_model_home_scope.star_packages(scripts)
    violations = check_model_home_scope.check_workflow(doc, WORKFLOW.name, scripts, packages)
    assert any("Carry the merged choices into the home" in v for v in violations), violations


def test_every_action_in_it_is_pinned():
    assert check_action_pins.problems(WORKFLOW.read_text(), str(WORKFLOW)) == []


def test_the_proof_reach_declaration_is_green_with_the_carry_line():
    assert proof_reach.check(ROOT) == []
    tags = {e["tag"]: e for e in proof_reach.load()["log_lines"]}
    assert tags["model-home-carry:"]["printed_by"] == "scripts/model_home_carry.py"


def test_the_readme_names_the_workflow():
    text = (ROOT / "config" / "README.md").read_text()
    assert "model-home-carry.yml" in text
    section = text[text.index("model-home-carry.yml") - 1200: text.index("model-home-carry.yml") + 1200]
    assert "part 2" in section or "DRE-6976" in section
    assert "carr" in section
