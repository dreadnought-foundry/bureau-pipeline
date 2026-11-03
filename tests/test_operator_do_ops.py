"""RED-first: the steps the Operator Agent may do itself, as data (DRE-8922).

`config/operator-do.json` declares the four kinds of step the agent carries
out — `variable`, `bot-pr`, `linear-admin` and `script` — each with its
credential and its ops, and each op with the command it becomes and whether
it writes. `scripts/operator_do_ops.py` reads it and answers, purely:

    render(op) -> list[str]      command_line(op) -> str     writes(op) -> bool
    credential(kind) -> str      plan_repo(plan) -> str | None
    check(plan, card_text, repo_map, card_repo) -> (ok, refusals)
    close_rule(title, description) -> one of CLOSE_RULES

The fixture cards are copied from the live cards (read 2026-11-03): each
card's identifier, lane, title, labels and at most three short sentences of
its description, the shape `operator-agent-board-2026-11-01.json` keeps.

Run: cd bureau-pipeline && python3 -m pytest tests/test_operator_do_ops.py -v
"""
from __future__ import annotations

import json
import re
import shlex
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import operator_do_ops  # noqa: E402
import operator_step_kind  # noqa: E402

MODULE_PATH = ROOT / "scripts" / "operator_do_ops.py"
CONFIG_PATH = ROOT / "config" / "operator-do.json"
FIXTURE = ROOT / "tests" / "fixtures" / "operator-do-cards.json"
REPO_MAP = json.loads((ROOT / "config" / "repo-map.json").read_text(encoding="utf-8"))

FIXTURE_CARDS = ["DRE-8637", "DRE-8623", "DRE-8846", "DRE-7579", "DRE-7942",
                 "DRE-7978", "DRE-7328"]
DO_KINDS = {"variable", "bot-pr", "linear-admin", "script"}
DEMO = "dreadnought-foundry/agent-bureau-demo"
SHA = "0123456789abcdef0123456789abcdef01234567"


def _fixture() -> list:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _row(card: str) -> dict:
    return next(r for r in _fixture() if r["card"] == card)


def _description(card: str) -> str:
    return "\n\n".join(e["text"] for e in _row(card)["excerpts"] if e["from"] == "description")


def _text(card: str) -> str:
    """The card's title and description as one text, the way `check` reads it."""
    return f"{_row(card)['title']}\n{_description(card)}"


def _label(card: str) -> str | None:
    return next((l.split(":", 1)[1] for l in _row(card)["labels"] if l.startswith("repo:")), None)


def _plan(card: str, kind: str, *ops) -> dict:
    return {"card": card, "kind": kind, "step": "one step.", "reason": "one reason.",
            "ops": list(ops), "continues": None}


def _check(card: str, kind: str, *ops, text: str | None = None, card_repo="label"):
    repo = _label(card) if card_repo == "label" else card_repo
    return operator_do_ops.check(_plan(card, kind, *ops), _text(card) if text is None else text,
                                 REPO_MAP, repo)


def _refused(refusals, index: int, op: str, reason: str) -> bool:
    """True when one refusal names op `index` (1-based), its op and `reason`."""
    head = f"op {index} {op}: "
    return any(r.startswith(head) and reason in r for r in refusals)


#: The variable DRE-8623 deletes, read from its card rather than spelled here:
#: it is the retired planner cap's name, which `test_no_planner_cap.py` refuses
#: in every tracked file outside the fixtures.
VARIABLE = re.search(r"gh variable delete (\S+) -R", _description("DRE-8623")).group(1)


# --- the file ---------------------------------------------------------------


def test_the_file_declares_the_four_kinds_and_nothing_else():
    doc = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    assert set(doc["kinds"]) == DO_KINDS
    assert DO_KINDS <= set(operator_step_kind.MACHINE_KINDS)
    assert set(operator_do_ops.DO_KINDS) == DO_KINDS


def test_the_file_declares_each_op_with_its_command_and_its_writes():
    doc = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    ops = {kind: set(row["ops"]) for kind, row in doc["kinds"].items()}
    assert ops == {
        "variable": {"variable-set", "variable-delete", "variable-list"},
        "bot-pr": {"branch-create", "file-put", "pr-open", "pr-close", "branch-delete", "pr-view"},
        "linear-admin": {"project-status"},
        "script": {"script"},
    }
    for kind, row in doc["kinds"].items():
        assert row["credential"] in ("GH_TOKEN", "LINEAR_API_KEY"), kind
        assert row["mint"].strip(), kind
        for name, op in row["ops"].items():
            assert isinstance(op["writes"], bool), name
            assert op["command"] and all(isinstance(a, str) for a in op["command"]), name
            assert op["fields"], name


def test_the_file_carries_the_one_script_and_the_pass_limits():
    doc = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    assert list(doc["scripts"]) == ["scripts/proof_release.py"]
    assert doc["max_ops"] == 20
    assert doc["max_do_per_pass"] == 3
    assert doc["max_close_per_pass"] == 10
    assert doc["pr_wait_days"] == 14


def test_the_kinds_credentials_are_the_two_env_names():
    assert operator_do_ops.credential("variable") == "GH_TOKEN"
    assert operator_do_ops.credential("bot-pr") == "GH_TOKEN"
    assert operator_do_ops.credential("script") == "GH_TOKEN"
    assert operator_do_ops.credential("linear-admin") == "LINEAR_API_KEY"
    for kind in ("deploy", "person", "initiative"):
        with pytest.raises(ValueError):
            operator_do_ops.credential(kind)


def _write(tmp_path, mutate) -> Path:
    doc = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    mutate(doc)
    path = tmp_path / "operator-do.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def test_load_refuses_a_deploy_kind(tmp_path):
    path = _write(tmp_path, lambda d: d["kinds"].__setitem__("deploy", d["kinds"]["script"]))
    with pytest.raises(ValueError, match="deploy"):
        operator_do_ops.load(path)


def test_load_refuses_a_kind_outside_the_machine_kinds(tmp_path):
    path = _write(tmp_path, lambda d: d["kinds"].__setitem__("initiative", d["kinds"]["script"]))
    with pytest.raises(ValueError, match="initiative"):
        operator_do_ops.load(path)


def test_load_refuses_a_credential_outside_the_two(tmp_path):
    path = _write(tmp_path, lambda d: d["kinds"]["script"].__setitem__("credential", "AWS_PROFILE"))
    with pytest.raises(ValueError, match="AWS_PROFILE"):
        operator_do_ops.load(path)


def test_load_refuses_a_command_naming_a_field_the_op_lacks(tmp_path):
    path = _write(tmp_path, lambda d: d["kinds"]["variable"]["ops"]["variable-list"]
                  .__setitem__("command", ["gh", "variable", "list", "-R", "{owner}"]))
    with pytest.raises(ValueError, match="owner"):
        operator_do_ops.load(path)


def test_the_shipped_file_loads():
    doc = operator_do_ops.load()
    assert set(doc["kinds"]) == DO_KINDS


# --- render, command_line, writes ------------------------------------------

RENDERED = [
    ({"op": "variable-set", "repo": DEMO, "name": "ALARM_REPAIR_LIVE", "value": "true"},
     ["gh", "variable", "set", "ALARM_REPAIR_LIVE", "--body", "true", "-R", DEMO], True),
    ({"op": "variable-delete", "repo": DEMO, "name": VARIABLE},
     ["gh", "variable", "delete", VARIABLE, "-R", DEMO], True),
    ({"op": "variable-list", "repo": DEMO},
     ["gh", "variable", "list", "-R", DEMO], False),
    ({"op": "branch-create", "repo": DEMO, "branch": "agent/DRE-7978-control", "from": SHA},
     ["gh", "api", "--method", "POST", f"repos/{DEMO}/git/refs",
      "-f", "ref=refs/heads/agent/DRE-7978-control", "-f", f"sha={SHA}"], True),
    ({"op": "file-put", "repo": DEMO, "branch": "agent/DRE-7978-control",
      "path": "briefs/groom-verify.md", "content": "hi\n", "message": "negative control"},
     ["gh", "api", "--method", "PUT", f"repos/{DEMO}/contents/briefs/groom-verify.md",
      "-f", "message=negative control", "-f", "content=aGkK",
      "-f", "branch=agent/DRE-7978-control"], True),
    ({"op": "pr-open", "repo": DEMO, "branch": "agent/DRE-7978-control",
      "title": "canary: do not merge", "body": "For DRE-7978.", "draft": True},
     ["gh", "api", "--method", "POST", f"repos/{DEMO}/pulls",
      "-f", "head=agent/DRE-7978-control", "-f", "base=main",
      "-f", "title=canary: do not merge", "-f", "body=For DRE-7978.", "-F", "draft=true"], True),
    ({"op": "pr-close", "repo": DEMO, "number": 1362},
     ["gh", "api", "--method", "PATCH", f"repos/{DEMO}/pulls/1362", "-f", "state=closed"], True),
    ({"op": "branch-delete", "repo": DEMO, "branch": "agent/DRE-7978-control"},
     ["gh", "api", "--method", "DELETE", f"repos/{DEMO}/git/refs/heads/agent/DRE-7978-control"],
     True),
    ({"op": "pr-view", "repo": DEMO, "number": 1362},
     ["gh", "api", f"repos/{DEMO}/pulls/1362"], False),
    ({"op": "project-status", "project": "onboard-sandbox: Onboard Sandbox", "status": "Completed"},
     ["python3", "scripts/operator_do.py", "linear", "project-status",
      "onboard-sandbox: Onboard Sandbox", "Completed"], True),
    ({"op": "script", "repo": "dreadnought-foundry/agent-bureau", "path": "scripts/proof_release.py",
      "args": ["check", "--repo", "dreadnought-foundry/agent-bureau",
               "--merge", "DRE-8620:4123:abc1234"]},
     ["python3", "scripts/proof_release.py", "check", "--repo",
      "dreadnought-foundry/agent-bureau", "--merge", "DRE-8620:4123:abc1234"], False),
]


@pytest.mark.parametrize("op,argv,writes", RENDERED, ids=[r[0]["op"] for r in RENDERED])
def test_render_turns_each_op_into_its_argv(op, argv, writes):
    assert operator_do_ops.render(op) == argv
    assert operator_do_ops.command_line(op) == shlex.join(argv)
    assert operator_do_ops.writes(op) is writes


def test_the_render_table_covers_every_op_in_the_file():
    doc = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    declared = {name for row in doc["kinds"].values() for name in row["ops"]}
    assert {r[0]["op"] for r in RENDERED} == declared


def test_writes_answers_as_the_file_declares():
    doc = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    for op, _argv, _writes in RENDERED:
        kind = next(k for k, row in doc["kinds"].items() if op["op"] in row["ops"])
        assert operator_do_ops.writes(op) is doc["kinds"][kind]["ops"][op["op"]]["writes"]


def test_a_draft_false_renders_false_and_a_value_is_one_argument():
    op = {"op": "pr-open", "repo": DEMO, "branch": "agent/DRE-1-x", "title": "a; rm -rf /",
          "body": "$(id)", "draft": False}
    argv = operator_do_ops.render(op)
    assert "-F" in argv and argv[argv.index("-F") + 1] == "draft=false"
    assert "title=a; rm -rf /" in argv and "body=$(id)" in argv


def test_render_refuses_an_op_not_in_the_file():
    for op in ({"op": "workflow-run", "repo": DEMO}, {"op": "initiative-status"}):
        with pytest.raises(ValueError):
            operator_do_ops.render(op)
        with pytest.raises(ValueError):
            operator_do_ops.writes(op)


def test_no_command_reaches_an_org_an_environment_or_a_secret():
    for op, _argv, _writes in RENDERED:
        argv = operator_do_ops.render(op)
        assert "--org" not in argv and "--env" not in argv and "-e" not in argv
        assert not any("secret" in a.lower() for a in argv)


# --- plan_repo --------------------------------------------------------------


def test_plan_repo_is_the_plans_one_repository():
    assert operator_do_ops.plan_repo(_plan("DRE-8623", "variable",
                                           {"op": "variable-list", "repo": DEMO},
                                           {"op": "variable-delete", "repo": DEMO,
                                            "name": VARIABLE})) == DEMO


def test_plan_repo_is_none_for_a_linear_admin_plan():
    assert operator_do_ops.plan_repo(_plan("DRE-7579", "linear-admin",
                                           {"op": "project-status", "status": "Completed",
                                            "project": "onboard-sandbox: Onboard Sandbox"})) is None


def test_plan_repo_refuses_two_repositories():
    with pytest.raises(ValueError):
        operator_do_ops.plan_repo(_plan("DRE-8623", "variable",
                                        {"op": "variable-list", "repo": DEMO},
                                        {"op": "variable-list",
                                         "repo": "dreadnought-foundry/portico"}))


# --- check: what passes -----------------------------------------------------


def test_check_passes_a_quoted_repository_on_dre_8623():
    ok, refusals = _check("DRE-8623", "variable",
                          {"op": "variable-delete", "repo": DEMO, "name": VARIABLE},
                          {"op": "variable-list", "repo": DEMO})
    assert (ok, refusals) == (True, [])


def test_check_passes_the_labels_repository_on_a_card_labeled_repo_agent_bureau_demo():
    """DRE-8637's text quotes no `owner/name`, so on a card labeled
    `repo:agent-bureau-demo` the repository is the map's value for the slug."""
    ok, refusals = _check("DRE-8637", "variable",
                          {"op": "variable-set", "repo": DEMO, "name": "ALARM_REPAIR_LIVE",
                           "value": "true"},
                          card_repo="agent-bureau-demo")
    assert (ok, refusals) == (True, [])


def test_check_passes_a_project_status_on_dre_7579():
    ok, refusals = _check("DRE-7579", "linear-admin",
                          {"op": "project-status", "project": "onboard-sandbox: Onboard Sandbox",
                           "status": "Completed"})
    assert (ok, refusals) == (True, [])


def test_check_passes_a_bot_pr_on_a_quoted_branch_and_on_the_cards_own_branch():
    for branch in ("operator/replay-gate-negative-control", "agent/DRE-7978-negative-control"):
        ok, refusals = _check("DRE-7978", "bot-pr",
                              {"op": "branch-create", "repo": "dreadnought-foundry/bureau-pipeline",
                               "branch": branch, "from": SHA},
                              {"op": "file-put", "repo": "dreadnought-foundry/bureau-pipeline",
                               "branch": branch, "path": "briefs/groom-verify.md",
                               "content": "x\n", "message": "negative control"},
                              {"op": "pr-open", "repo": "dreadnought-foundry/bureau-pipeline",
                               "branch": branch, "title": "negative control", "body": "DRE-7978",
                               "draft": True})
        assert (ok, refusals) == (True, []), branch


def test_check_passes_twenty_ops():
    ops = [{"op": "variable-list", "repo": DEMO}] * 20
    assert _check("DRE-8623", "variable", *ops) == (True, [])


# --- check: what it refuses -------------------------------------------------


def test_check_refuses_an_initiative_status_op():
    ok, refusals = _check("DRE-7579", "linear-admin",
                          {"op": "initiative-status", "initiative": "Onboard Sandbox",
                           "status": "Completed"})
    assert not ok
    assert _refused(refusals, 1, "initiative-status", "not an op in config/operator-do.json")


def test_check_refuses_a_workflow_run_op():
    ok, refusals = _check("DRE-8846", "bot-pr",
                          {"op": "workflow-run", "repo": "dreadnought-foundry/agent-bureau",
                           "workflow": "merge-gate.yml"})
    assert not ok
    assert _refused(refusals, 1, "workflow-run", "not an op in config/operator-do.json")


def test_check_refuses_an_op_of_another_kind():
    ok, refusals = _check("DRE-7579", "linear-admin",
                          {"op": "variable-list", "repo": "dreadnought-foundry/agent-bureau"})
    assert not ok
    assert _refused(refusals, 1, "variable-list", "a variable op, and the card's kind is linear-admin")


def test_check_refuses_a_plan_kind_other_than_the_cards():
    ok, refusals = _check("DRE-7579", "variable",
                          {"op": "variable-list", "repo": "dreadnought-foundry/agent-bureau"})
    assert not ok
    assert any(r.startswith("plan: ") and "the card's kind is linear-admin" in r for r in refusals)


def test_check_refuses_a_plan_on_a_card_a_person_must_do():
    text = _text("DRE-8637") + "\nThe operator signs in to the console first."
    ok, refusals = _check("DRE-8637", "variable",
                          {"op": "variable-list", "repo": "dreadnought-foundry/bureau-pipeline"},
                          text=text)
    assert not ok
    assert any(r.startswith("plan: ") and "person" in r for r in refusals)


def test_check_refuses_a_repo_given_as_the_slug():
    ok, refusals = _check("DRE-8623", "variable",
                          {"op": "variable-list", "repo": "agent-bureau-demo"})
    assert not ok
    assert _refused(refusals, 1, "variable-list", "not a value of config/repo-map.json")
    assert any(DEMO in r for r in refusals)


def test_check_refuses_a_repo_in_another_organization_even_when_quoted():
    for repo in ("EveryBite/atlas", "DeltaSolv/deltasolv"):
        assert repo in REPO_MAP.values() and repo in _text("DRE-8623")
        ok, refusals = _check("DRE-8623", "variable", {"op": "variable-list", "repo": repo})
        assert not ok
        assert _refused(refusals, 1, "variable-list", "does not open dreadnought-foundry/"), repo


def test_check_refuses_a_repo_neither_the_labels_nor_quoted():
    ok, refusals = _check("DRE-8637", "variable",
                          {"op": "variable-list", "repo": "dreadnought-foundry/foundry"})
    assert not ok
    assert _refused(refusals, 1, "variable-list",
                    "neither the repo:bureau-pipeline label's repository nor quoted")


def test_check_refuses_a_plan_naming_two_repositories():
    ok, refusals = _check("DRE-8623", "variable",
                          {"op": "variable-delete", "repo": "dreadnought-foundry/agent-bureau",
                           "name": VARIABLE},
                          {"op": "variable-delete", "repo": "dreadnought-foundry/portico",
                           "name": VARIABLE})
    assert not ok
    assert _refused(refusals, 2, "variable-delete", "a second repository")


def test_check_refuses_a_variable_name_the_card_does_not_quote():
    ok, refusals = _check("DRE-8637", "variable",
                          {"op": "variable-set", "repo": "dreadnought-foundry/bureau-pipeline",
                           "name": "ALARM_REPAIR_MODE", "value": "true"})
    assert not ok
    assert _refused(refusals, 1, "variable-set", "`ALARM_REPAIR_MODE` is not quoted")


def test_check_refuses_a_variable_name_quoted_only_inside_fenced_code():
    text = _text("DRE-8637") + "\n```\ngh variable set ALARM_REPAIR_MODE --body true\n```\n"
    ok, refusals = _check("DRE-8637", "variable",
                          {"op": "variable-set", "repo": "dreadnought-foundry/bureau-pipeline",
                           "name": "ALARM_REPAIR_MODE", "value": "true"}, text=text)
    assert not ok
    assert _refused(refusals, 1, "variable-set", "`ALARM_REPAIR_MODE` is not quoted")


def test_check_refuses_a_name_found_only_inside_a_longer_word():
    ok, refusals = _check("DRE-8637", "variable",
                          {"op": "variable-set", "repo": "dreadnought-foundry/bureau-pipeline",
                           "name": "ALARM_REPAIR", "value": "true"})
    assert not ok
    assert _refused(refusals, 1, "variable-set", "`ALARM_REPAIR` is not quoted")


def test_check_refuses_a_project_name_the_card_does_not_quote():
    ok, refusals = _check("DRE-7942", "linear-admin",
                          {"op": "project-status", "project": "onboard-sandbox: Onboard Sandbox",
                           "status": "Completed"})
    assert not ok
    assert _refused(refusals, 1, "project-status", "is not quoted")


def test_check_refuses_a_project_status_outside_completed_and_canceled():
    ok, refusals = _check("DRE-7579", "linear-admin",
                          {"op": "project-status", "project": "onboard-sandbox: Onboard Sandbox",
                           "status": "Archived"})
    assert not ok
    assert _refused(refusals, 1, "project-status", "Archived")


def test_check_refuses_a_branch_the_card_does_not_quote():
    for branch in ("operator/some-other-branch", "agent/DRE-9999-not-this-card"):
        ok, refusals = _check("DRE-7978", "bot-pr",
                              {"op": "branch-delete", "repo": "dreadnought-foundry/bureau-pipeline",
                               "branch": branch})
        assert not ok
        assert _refused(refusals, 1, "branch-delete", f"`{branch}` is not quoted"), branch


def test_check_refuses_the_default_branch_even_when_quoted():
    assert "`main`" in _text("DRE-7978")
    for op in ({"op": "branch-delete", "branch": "main"},
               {"op": "file-put", "branch": "main", "path": "briefs/groom-verify.md",
                "content": "x", "message": "m"}):
        op = {**op, "repo": "dreadnought-foundry/bureau-pipeline"}
        ok, refusals = _check("DRE-7978", "bot-pr", op)
        assert not ok
        assert _refused(refusals, 1, op["op"], "never written"), op["op"]


def test_check_refuses_a_workflow_file_the_card_does_not_quote():
    ok, refusals = _check("DRE-7978", "bot-pr",
                          {"op": "file-put", "repo": "dreadnought-foundry/bureau-pipeline",
                           "branch": "operator/replay-gate-negative-control",
                           "path": ".github/workflows/groom-replay-gate.yml",
                           "content": "x", "message": "m"})
    assert not ok
    assert _refused(refusals, 1, "file-put", "`.github/workflows/groom-replay-gate.yml` is not quoted")


def test_check_refuses_a_linear_ops_dump_comments_script():
    ok, refusals = _check("DRE-8623", "variable",
                          {"op": "script", "repo": "dreadnought-foundry/bureau-pipeline",
                           "path": "scripts/linear_ops.py", "args": ["dump-comments", "DRE-8623"]})
    assert not ok
    assert _refused(refusals, 1, "script", "`scripts/linear_ops.py` is not on the script list")


def test_check_refuses_proof_release_with_other_arguments():
    for args in (["check", "--repo", "dreadnought-foundry/portico", "--merge", "DRE-1:2:abc1234"],
                 ["record", "--repo", "dreadnought-foundry/agent-bureau"],
                 ["check", "--repo", "dreadnought-foundry/agent-bureau"],
                 ["check", "--repo", "dreadnought-foundry/agent-bureau", "--write", "x"]):
        ok, refusals = _check("DRE-8623", "variable",
                              {"op": "script", "repo": "dreadnought-foundry/agent-bureau",
                               "path": "scripts/proof_release.py", "args": args})
        assert _refused(refusals, 1, "script", "arguments"), args


def test_a_well_formed_proof_release_check_is_refused_for_its_kind_alone():
    ok, refusals = _check("DRE-8623", "variable",
                          {"op": "script", "repo": "dreadnought-foundry/agent-bureau",
                           "path": "scripts/proof_release.py",
                           "args": ["check", "--repo", "dreadnought-foundry/agent-bureau",
                                    "--merge", "DRE-8620:4123:abc1234"]})
    assert not ok
    assert refusals == ["op 1 script: a script op, and the card's kind is variable"]


@pytest.mark.parametrize("switch", ["OPERATOR_AGENT_LIVE", "OPERATOR_AGENT_DO_LIVE",
                                    "OPERATOR_AGENT_LIVE_OFF_UNTIL",
                                    "OPERATOR_AGENT_DO_LIVE_OFF_UNTIL"])
def test_check_refuses_the_agents_own_switches_even_when_quoted(switch):
    text = _text("DRE-8637") + f"\nSet `{switch}` to `true`."
    ok, refusals = _check("DRE-8637", "variable",
                          {"op": "variable-set", "repo": "dreadnought-foundry/bureau-pipeline",
                           "name": switch, "value": "true"}, text=text)
    assert not ok
    assert refusals == [f"op 1 variable-set: `{switch}` is the agent's own switch"]


SWITCHES = ["OPERATOR_AGENT_LIVE", "OPERATOR_AGENT_DO_LIVE", "OPERATOR_AGENT_LIVE_OFF_UNTIL",
            "OPERATOR_AGENT_DO_LIVE_OFF_UNTIL"]


@pytest.mark.parametrize("switch", SWITCHES)
@pytest.mark.parametrize("spell", [str.lower, str.title], ids=["lower", "mixed"])
def test_check_refuses_the_agents_own_switches_in_any_letter_case(switch, spell):
    """GitHub reads a variable name without letter case: `operator_agent_do_live`
    is `OPERATOR_AGENT_DO_LIVE`."""
    name = spell(switch)
    text = _text("DRE-8637") + f"\nSet `{name}` to `true`."
    for op in ({"op": "variable-set", "name": name, "value": "true"},
               {"op": "variable-delete", "name": name}):
        op = {**op, "repo": "dreadnought-foundry/bureau-pipeline"}
        ok, refusals = _check("DRE-8637", "variable", op, text=text)
        assert not ok
        assert refusals == [f"op 1 {op['op']}: `{switch}` is the agent's own switch"], name


WORKFLOW = ".github/workflows/groom-replay-gate.yml"


@pytest.mark.parametrize("path", [f"./{WORKFLOW}", ".github//workflows/groom-replay-gate.yml",
                                  ".github/./workflows/groom-replay-gate.yml",
                                  ".github/workflows//groom-replay-gate.yml",
                                  ".GitHub/Workflows/groom-replay-gate.yml",
                                  "briefs/groom-verify.md?ref=main", "briefs/groom-verify.md#x",
                                  "briefs/%2e%2e/groom-verify.md", "briefs/groom\x01verify.md",
                                  "./briefs/groom-verify.md", "briefs//groom-verify.md",
                                  "briefs/./groom-verify.md"])
def test_check_refuses_a_path_spelled_around_its_guard(path):
    """A workflow file is only ever one the card names, however its path is
    written, and nothing in a path reaches past `repos/{repo}/contents/`."""
    ok, refusals = _check("DRE-7978", "bot-pr",
                          {"op": "file-put", "repo": "dreadnought-foundry/bureau-pipeline",
                           "branch": "operator/replay-gate-negative-control", "path": path,
                           "content": "x", "message": "m"})
    assert not ok
    assert _refused(refusals, 1, "file-put", f"`{path}` is not"), refusals


@pytest.mark.parametrize("branch", ["operator/x?y", "operator/x#y", "operator/x%2fy",
                                    "operator/x\x01y", "operator/../x", "operator/./x",
                                    "operator//x"])
def test_check_refuses_a_quoted_branch_that_reaches_past_its_url(branch):
    text = _text("DRE-7978") + f"\nDelete the branch `{branch}`."
    ok, refusals = _check("DRE-7978", "bot-pr",
                          {"op": "branch-delete", "repo": "dreadnought-foundry/bureau-pipeline",
                           "branch": branch}, text=text)
    assert not ok
    assert _refused(refusals, 1, "branch-delete", f"`{branch}` is not a branch name"), refusals


BAD_VALUES = [["x"], {"x": 1}, 7, None]
BAD_IDS = ["list", "dict", "int", "none"]


@pytest.mark.parametrize("bad", BAD_VALUES, ids=BAD_IDS)
def test_check_never_raises_on_a_wrong_typed_value_in_any_field_of_any_op(bad):
    doc = operator_do_ops.load()
    for kind, row in doc["kinds"].items():
        for name, spec in row["ops"].items():
            for field in ["op", *spec["fields"]]:
                op = {"op": name, **{f: "x" for f in spec["fields"]}, field: bad}
                ok, refusals = operator_do_ops.check(_plan("DRE-8623", kind, op),
                                                     _text("DRE-8623"), REPO_MAP, None)
                assert not ok and refusals, (name, field)
                for refusal in refusals:
                    assert re.match(r"^(plan|op 1 [a-z-]+): \S", refusal), refusal


@pytest.mark.parametrize("bad", BAD_VALUES, ids=BAD_IDS)
def test_check_never_raises_on_a_wrong_typed_plan_key(bad):
    for key in ("card", "kind", "step", "reason", "ops", "continues"):
        plan = {**_plan("DRE-8623", "variable", {"op": "variable-list", "repo": DEMO}), key: bad}
        ok, refusals = operator_do_ops.check(plan, _text("DRE-8623"), REPO_MAP, None)
        if (key, bad) != ("continues", None):
            assert not ok and refusals, key


@pytest.mark.parametrize("bad", BAD_VALUES, ids=BAD_IDS)
def test_check_refuses_a_script_path_that_is_not_text(bad):
    ok, refusals = _check("DRE-8623", "variable",
                          {"op": "script", "repo": "dreadnought-foundry/agent-bureau",
                           "path": bad, "args": ["check"]})
    assert not ok
    assert _refused(refusals, 1, "script", "is not a script path")


@pytest.mark.parametrize("bad", BAD_VALUES, ids=BAD_IDS)
def test_plan_repo_raises_only_value_error_on_a_repo_that_is_not_text(bad):
    with pytest.raises(ValueError):
        operator_do_ops.plan_repo(_plan("DRE-8623", "variable",
                                        {"op": "variable-list", "repo": bad}))
    with pytest.raises(ValueError):
        operator_do_ops.plan_repo({**_plan("DRE-8623", "variable"), "ops": 7})


def test_render_refuses_a_spread_that_is_not_a_list():
    with pytest.raises(ValueError):
        operator_do_ops.render({"op": "script", "repo": "dreadnought-foundry/agent-bureau",
                                "path": "scripts/proof_release.py", "args": "check"})


def test_check_refuses_more_than_max_ops():
    ops = [{"op": "variable-list", "repo": DEMO}] * 21
    ok, refusals = _check("DRE-8623", "variable", *ops)
    assert not ok
    assert any(r.startswith("plan: ") and "21 ops" in r and "20" in r for r in refusals)


def test_check_refuses_a_missing_or_extra_field():
    ok, refusals = _check("DRE-8637", "variable",
                          {"op": "variable-set", "repo": "dreadnought-foundry/bureau-pipeline",
                           "name": "ALARM_REPAIR_LIVE"},
                          {"op": "variable-list", "repo": "dreadnought-foundry/bureau-pipeline",
                           "org": "dreadnought-foundry"})
    assert not ok
    assert _refused(refusals, 1, "variable-set", "value")
    assert _refused(refusals, 2, "variable-list", "org")


def test_check_refuses_a_branch_create_from_a_branch_name():
    ok, refusals = _check("DRE-7978", "bot-pr",
                          {"op": "branch-create", "repo": "dreadnought-foundry/bureau-pipeline",
                           "branch": "operator/replay-gate-negative-control", "from": "main"})
    assert not ok
    assert _refused(refusals, 1, "branch-create", "commit sha")


def test_check_refuses_a_plan_with_no_ops_or_a_bad_shape():
    assert _check("DRE-8637", "variable") == (False, ["plan: it names no op"])
    ok, refusals = operator_do_ops.check([], _text("DRE-8637"), REPO_MAP, "bureau-pipeline")
    assert not ok and refusals


def test_every_refusal_names_the_op_or_the_plan():
    _ok, refusals = _check("DRE-8623", "linear-admin",
                           {"op": "variable-list", "repo": "agent-bureau-demo"},
                           {"op": "initiative-status"},
                           {"op": "variable-set", "repo": "EveryBite/atlas",
                            "name": "OPERATOR_AGENT_DO_LIVE", "value": "true"})
    assert refusals
    for refusal in refusals:
        assert re.match(r"^(plan|op \d+ [a-z-]+): \S", refusal), refusal


# --- close_rule -------------------------------------------------------------

#: The two closes no fixture card names, each added to a fixture card's text as
#: one sentence. The first is DRE-8927's, word for word.
CARD_DONE = "This card closes with `card-done`."
OWN_PR_MERGE = "This card closes on the merge of the pull request it opens."


def test_close_rules_are_the_four():
    assert operator_do_ops.CLOSE_RULES == ("record-outcome", "card-done", "own-pr-merge", "none")


def test_close_rule_reads_record_the_outcome_on_dre_7328():
    row = _row("DRE-7328")
    assert operator_do_ops.close_rule(row["title"], _description("DRE-7328")) == "record-outcome"
    assert operator_do_ops.close_rule("", _description("DRE-7328")) == "record-outcome"


def test_close_rule_reads_none_on_the_cards_that_name_no_close():
    for card in ("DRE-8637", "DRE-8623", "DRE-8846", "DRE-7579", "DRE-7942"):
        assert operator_do_ops.close_rule(_row(card)["title"], _description(card)) == "none", card


def test_a_pull_request_merging_is_not_the_cards_close():
    """DRE-7978 waits on another pull request's merge and closes its own
    unmerged: no sentence says the card closes on a merge."""
    assert "pull request merges" in _description("DRE-7978")
    assert operator_do_ops.close_rule(_row("DRE-7978")["title"], _description("DRE-7978")) == "none"


def test_close_rule_reads_card_done():
    row = _row("DRE-8637")
    assert operator_do_ops.close_rule(row["title"],
                                      f"{_description('DRE-8637')}\n\n{CARD_DONE}") == "card-done"


def test_close_rule_reads_own_pr_merge():
    row = _row("DRE-8846")
    assert operator_do_ops.close_rule(row["title"],
                                      f"{_description('DRE-8846')}\n\n{OWN_PR_MERGE}") == "own-pr-merge"


def test_close_rule_takes_the_first_in_order():
    title = _row("DRE-7328")["title"]
    assert operator_do_ops.close_rule(title, f"{CARD_DONE} {OWN_PR_MERGE}") == "record-outcome"
    assert operator_do_ops.close_rule("x", f"{OWN_PR_MERGE} {CARD_DONE}") == "card-done"


def test_close_rule_does_not_read_a_sentence_inside_fenced_code():
    sentence = _row("DRE-7328")["excerpts"][2]["text"]
    for fence in ("```", "~~~"):
        description = f"{_description('DRE-8637')}\n\n{fence}\n{sentence}\n{CARD_DONE}\n{fence}\n"
        assert operator_do_ops.close_rule(_row("DRE-8637")["title"], description) == "none"
    assert operator_do_ops.close_rule(_row("DRE-8637")["title"], sentence) == "record-outcome"


# --- the fixture ------------------------------------------------------------


def test_the_fixture_holds_exactly_the_seven_live_cards():
    cards = [r["card"] for r in _fixture()]
    assert cards == FIXTURE_CARDS


def test_every_fixture_row_keeps_the_board_fixtures_shape():
    for record in _fixture():
        assert set(record) == {"card", "lane", "title", "labels", "excerpts"}, record["card"]
        assert re.fullmatch(r"DRE-\d+", record["card"])
        assert record["lane"] in ("Hand-work", "Queued", "Done"), record["card"]
        assert isinstance(record["labels"], list) and record["labels"], record["card"]
        assert sum(l.startswith("repo:") for l in record["labels"]) == 1, record["card"]
        assert 1 <= len(record["excerpts"]) <= 3, record["card"]
        for excerpt in record["excerpts"]:
            assert set(excerpt) == {"from", "created_at", "text"}, record["card"]
            assert excerpt["from"] in ("description", "comment"), record["card"]
            if excerpt["from"] == "description":
                assert excerpt["created_at"] is None, record["card"]
            else:
                assert re.fullmatch(r"\d{4}-\d\d-\d\dT[\d:.]+Z", excerpt["created_at"])
            text = excerpt["text"]
            assert text.strip() and len(text) <= 300, record["card"]
            assert "\n" not in text and "\r" not in text, record["card"]


def test_the_fixture_cards_read_as_the_kinds_the_hygiene_lane_gives_them():
    kinds = {card: operator_step_kind.classify(_row(card)["title"], _description(card))[1]
             for card in FIXTURE_CARDS}
    assert kinds == {"DRE-8637": "variable", "DRE-8623": "variable", "DRE-8846": "bot-pr",
                     "DRE-7579": "linear-admin", "DRE-7942": "linear-admin",
                     "DRE-7978": "bot-pr", "DRE-7328": "variable"}


# --- what this card adds names no person ------------------------------------

ADDED = [CONFIG_PATH, MODULE_PATH, FIXTURE, Path(__file__)]


def test_nothing_added_names_a_person_an_email_or_a_sign_in():
    sign = "sign" + r"(?:-in|ed-in|s in| in)\b"
    for path in ADDED:
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text), path.name
        assert not re.search(r"\bS" + r"id\b|Conk" + r"lin", text), path.name
        if path != Path(__file__):
            assert not re.search(sign, text, re.IGNORECASE), path.name


def test_the_module_is_pure():
    source = MODULE_PATH.read_text(encoding="utf-8")
    for word in ("subprocess", "urllib", "requests", "os.system", "linear_ops"):
        assert word not in source, word
