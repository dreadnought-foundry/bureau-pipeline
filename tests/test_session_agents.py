"""DRE-6833 — an agent the pipeline does not run can be put in a group, and the
published file says which moves are allowed.

THE INCIDENT. On 2026-10-10 the CEO dragged Designer into Builders on Settings ›
Agents & models. bureau-pipeline run 38114763170 failed at "Validate the change
set" with:

    change 1 of 1 (move_agent designer workhorse): designer has no entry in the
    agent registry (agents.yaml), so the pipeline has no way to run it — an
    agent joins a group only once it is defined there

while `config/model-choices.json` published `"moves": []` for designer, so the
page had nothing telling it the move would be refused.

THE FIX, as this file pins it:

  * `config/models.yaml` names the agents that run in a person's session rather
    than a pipeline workflow under `session_agents`. Such an agent may join any
    group, and stays in `session_agents` once it has one.
  * A group never makes it a pipeline role: no agents.yaml entry, not in
    `model_fallback.AGENT_KINDS`, and the selector answers it exactly as it
    answers an unknown name, from the YAML and from the degrade mirror alike.
  * Rule 10 (only the planner is `judgement`) exempts session-run agents and
    nobody else.
  * Every line the change script prints for a refused `move_agent` or
    `add_agent` is a plain sentence, and the generator publishes the same
    sentence as the move's `reason`.

Run: cd bureau-pipeline && python3 -m pytest tests/test_session_agents.py -q
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "models.yaml"
AGENTS = ROOT / "agents.yaml"
CHOICES = ROOT / "config" / "model-choices.json"
SCRIPT = ROOT / "scripts" / "model_config_change.py"

sys.path.insert(0, str(ROOT / "scripts"))

import model_config_change as mcc  # noqa: E402
import model_fallback as mf  # noqa: E402
import proof_and_demo  # noqa: E402
import sync_model_config  # noqa: E402

TODAY = "2026-10-11"
SESSION = ["designer", "ux-ui-engineer", "fullstack-engineer", "mobile", "product-lead"]
GROUPS = ["workhorse", "advisory", "judgement"]
REQUESTED_BY = "ceo@example.com"


# --------------------------------------------------------------------------- #
# The plain-words check — written once, applied to every line under test       #
# --------------------------------------------------------------------------- #

_FORBIDDEN_WORDS = (
    "registry", "agents.yaml", "models.yaml", "model-prices", "repo-map", "kind",
    "ladder", "rule", "policy", "validation", "review_separation", "ungrouped",
    "session_agents", "workhorse", "advisory", "judgement",
)
_FILE_TOKEN = re.compile(r"\S+\.(?:py|yaml|json)\b")


def plain_words_problems(line: str) -> list[str]:
    """Everything in `line` a non-engineer should not have to read."""
    lowered = line.lower()
    found = [word for word in _FORBIDDEN_WORDS if word in lowered]
    found += [ch for ch in ("`", "_", "/") if ch in line]
    found += _FILE_TOKEN.findall(line)
    return found


def assert_plain(line: str) -> None:
    assert line.strip(), "an empty line says nothing"
    problems = plain_words_problems(line)
    assert not problems, f"not plain words ({problems}): {line!r}"


def test_the_plain_words_check_catches_what_it_names():
    """The check itself must bite, or every assertion below is vacuous."""
    for bad in (
        "change 1 of 1 (move_agent designer workhorse): designer has no entry",
        "agents.critic: must be 'advisory'",
        "config/models.yaml already fails validation",
        "'judgement' is the planner's kind",
        "it is in `session_agents`",
        "see scripts/x.py",
    ):
        assert plain_words_problems(bad), bad
    assert not plain_words_problems(
        "Move designer (today in no group) into the Builders group.")


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #

def _text() -> str:
    return CONFIG.read_text()


def _move(agent: str, group: str) -> dict:
    return {"op": "move_agent", "agent": agent, "group": group}


def _apply(*changes: dict, text: str | None = None):
    return mcc.apply_change_set(
        _text() if text is None else text,
        {"requested_by": REQUESTED_BY, "changes": list(changes)}, today=TODAY)


def _reasons(refusals) -> str:
    return "\n".join(r for refusal in refusals for r in refusal["reasons"])


def _env(**extra: str) -> dict:
    env = dict(os.environ)
    env["BUREAU_FAKE_AVAILABLE"] = "{}"  # every model available, no network
    env.pop("GITHUB_STEP_SUMMARY", None)
    env.update(extra)
    return env


def _cli_apply(tmp_path: Path, *changes: dict, config: Path | None = None):
    """`(proc, error lines, summary bullets)` of `apply --check`, which writes
    nothing, against `config` (the real file when None)."""
    changes_path = tmp_path / "change-set.json"
    changes_path.write_text(json.dumps({"requested_by": REQUESTED_BY,
                                        "changes": list(changes)}))
    summary = tmp_path / "summary.md"
    summary.write_text("")
    args = [sys.executable, str(SCRIPT), "apply", "--changes", str(changes_path),
            "--check"]
    if config is not None:
        args += ["--config", str(config)]
    proc = subprocess.run(args, capture_output=True, text=True, cwd=ROOT,
                          env=_env(GITHUB_STEP_SUMMARY=str(summary)))
    errors = [line[len("::error::"):] for line in proc.stdout.splitlines()
              if line.startswith("::error::")]
    bullets = [line[2:] for line in summary.read_text().splitlines()
               if line.startswith("- ")]
    return proc, errors, bullets


def _refusal_lines(errors: list[str]) -> list[str]:
    """The per-change lines, without the one header line every refusal opens
    with."""
    return [line for line in errors if not line.startswith("the change set from")]


# --------------------------------------------------------------------------- #
# 1. The replay of run 38114763170                                            #
# --------------------------------------------------------------------------- #

def test_the_real_file_names_the_five_session_agents():
    raw = yaml.safe_load(_text())
    assert raw["session_agents"] == SESSION
    assert raw["ungrouped"] == SESSION
    assert mf.policy_errors(raw) == []


def test_the_change_set_of_run_38114763170_validates_and_applies():
    text, refusals = _apply(_move("designer", "workhorse"))
    assert refusals == [], _reasons(refusals)
    doc = yaml.safe_load(text)
    assert doc["agents"]["designer"] == "workhorse"
    assert "designer" not in doc["ungrouped"]
    assert "designer" in doc["session_agents"]
    assert mf.policy_errors(doc) == []


def test_the_cli_accepts_the_change_set_of_run_38114763170(tmp_path):
    proc, errors, _ = _cli_apply(tmp_path, _move("designer", "workhorse"))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert errors == []
    assert "+  designer: workhorse" in proc.stdout


# --------------------------------------------------------------------------- #
# 2. Every session agent into every group, and the selector never notices      #
# --------------------------------------------------------------------------- #

def _tree(base: Path, models_text: str) -> Path:
    """`scripts/` and `config/` copied to a temp tree (the seam
    tests/test_project_model_exceptions.py uses), plus agents.yaml so the
    generator can run there, with `models_text` as its models.yaml."""
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(ROOT / "scripts", base / "scripts", ignore=ignore)
    shutil.copytree(ROOT / "config", base / "config", ignore=ignore)
    shutil.copy(AGENTS, base / "agents.yaml")
    (base / "config" / "models.yaml").write_text(models_text)
    return base


def _select(tree: Path, role: str) -> tuple[str, str]:
    """`(model, note)` from the real `model_fallback.py select` in `tree`."""
    proc = subprocess.run(
        [sys.executable, str(tree / "scripts" / "model_fallback.py"), "select", role],
        capture_output=True, text=True, env=_env(), cwd=tree)
    assert proc.returncode == 0, proc.stderr
    assert "REFUSING" not in proc.stderr, proc.stderr
    note = next(line for line in proc.stderr.splitlines()
                if line.startswith("model-policy:"))
    return proc.stdout.strip(), note


def _reads(tree: Path, role: str) -> dict:
    """What the tree's `model_fallback` answers for `role`."""
    code = (
        "import json, sys\n"
        f"sys.path.insert(0, {str(tree / 'scripts')!r})\n"
        "import model_fallback as mf\n"
        f"role = {role!r}\n"
        "print(json.dumps({'kind': mf.kind_for(role), 'ladder': mf.ladder_for(role),\n"
        "    'default': mf.LADDER, 'judgement': mf.LADDERS[mf.KINDS['judgement']],\n"
        "    'in_agent_kinds': role in mf.AGENT_KINDS,\n"
        "    'session': mf.CONFIG.get('session_agents'),\n"
        "    'agents': mf.CONFIG['agents']}))\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True, env=_env(), cwd=tree)
    assert proc.returncode == 0, proc.stderr
    assert "REFUSING" not in proc.stderr, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _assert_answered_as_unknown(tree: Path, agent: str, group: str, read: str):
    reads = _reads(tree, agent)
    # The tree really carries the move — a refused or unread config would make
    # every assertion below pass for the wrong reason.
    assert reads["agents"].get(agent) == group, (read, reads)
    assert agent in reads["session"], read
    assert reads["in_agent_kinds"] is False, read
    assert reads["kind"] == "workhorse", read
    assert reads["ladder"] == reads["default"], read
    assert reads["ladder"] != reads["judgement"], read
    model, note = _select(tree, agent)
    unknown_model, unknown_note = _select(tree, "nosuchagent")
    assert model == reads["default"][0], read
    assert (model, note) == (unknown_model,
                             unknown_note.replace("nosuchagent", agent)), read
    assert model not in reads["judgement"][:1], read


@pytest.mark.parametrize("agent", SESSION)
@pytest.mark.parametrize("group", GROUPS)
def test_a_session_agent_joins_any_group_and_selects_like_an_unknown_name(
        tmp_path, agent, group):
    text, refusals = _apply(_move(agent, group))
    assert refusals == [], _reasons(refusals)
    doc = yaml.safe_load(text)
    assert doc["agents"][agent] == group
    assert agent not in doc["ungrouped"]
    assert agent in doc["session_agents"]

    tree = _tree(tmp_path / "tree", text)
    _assert_answered_as_unknown(tree, agent, group, "from the YAML")

    # The degrade mirror: regenerate the literal in the copy, then take the
    # YAML away so `_load_config` falls back to it.
    sync = subprocess.run(
        [sys.executable, str(tree / "scripts" / "sync_model_config.py")],
        capture_output=True, text=True, cwd=tree)
    assert sync.returncode == 0, sync.stdout + sync.stderr
    (tree / "config" / "models.yaml").unlink()
    _assert_answered_as_unknown(tree, agent, group, "from the mirror")


def test_a_session_agent_can_be_moved_again_from_group_to_group():
    text, refusals = _apply(_move("designer", "workhorse"),
                            _move("designer", "judgement"),
                            _move("designer", "advisory"))
    assert refusals == [], _reasons(refusals)
    doc = yaml.safe_load(text)
    assert doc["agents"]["designer"] == "advisory"
    assert "designer" in doc["session_agents"]
    text, refusals = _apply(_move("designer", "judgement"), text=text)
    assert refusals == [], _reasons(refusals)
    assert yaml.safe_load(text)["agents"]["designer"] == "judgement"


def test_the_mirror_carries_session_agents_beside_ungrouped():
    mirror = mf._FALLBACK_MODEL_CONFIG
    assert mirror["session_agents"] == yaml.safe_load(_text())["session_agents"]
    keys = list(mirror)
    assert keys.index("session_agents") == keys.index("ungrouped") + 1


# --------------------------------------------------------------------------- #
# 3. A group never makes it a pipeline role                                    #
# --------------------------------------------------------------------------- #

def test_a_grouped_session_agent_is_in_no_list_the_pipeline_dispatches(tmp_path):
    text, refusals = _apply(_move("designer", "judgement"))
    assert refusals == [], _reasons(refusals)
    tree = _tree(tmp_path / "tree", text)
    registry_before = (tree / "agents.yaml").read_bytes()

    sync = subprocess.run(
        [sys.executable, str(tree / "scripts" / "sync_model_config.py")],
        capture_output=True, text=True, cwd=tree)
    assert sync.returncode == 0, sync.stdout + sync.stderr
    check = subprocess.run(
        [sys.executable, str(tree / "scripts" / "sync_model_config.py"), "--check"],
        capture_output=True, text=True, cwd=tree)
    assert check.returncode == 0, check.stdout + check.stderr

    # agents.yaml: no entry for it, and regeneration did not touch the file.
    assert (tree / "agents.yaml").read_bytes() == registry_before
    names = [a["name"] for a in yaml.safe_load(registry_before)["agents"]]
    assert "designer" not in names

    # proof_and_demo.build_roles(): the roles on agent-task.yml, unchanged.
    proof_and_demo._ROSTER_CACHE.clear()
    roles = proof_and_demo.build_roles(str(tree / "agents.yaml"))
    assert roles == proof_and_demo.build_roles(str(AGENTS))
    assert "designer" not in roles

    # model_fallback.AGENT_KINDS: never holds it.
    assert _reads(tree, "designer")["in_agent_kinds"] is False
    assert "designer" not in mf.AGENT_KINDS


def test_splice_registry_skips_a_grouped_session_agent():
    raw = yaml.safe_load(_text())
    raw["agents"]["designer"] = "advisory"
    raw["ungrouped"].remove("designer")
    cfg = mf._normalize_config(raw)
    assert sync_model_config.splice_registry(AGENTS.read_text(), cfg) == AGENTS.read_text()


def test_splice_registry_refuses_a_session_agent_with_a_generated_region():
    cfg = sync_model_config.load_config()
    cfg = dict(cfg, session_agents=cfg["session_agents"] + ["engineer"])
    with pytest.raises(SystemExit) as caught:
        sync_model_config.splice_registry(AGENTS.read_text(), cfg)
    assert "engineer" in str(caught.value)
    assert "session_agents" in str(caught.value)


# --------------------------------------------------------------------------- #
# 4. add_agent then move_agent, in one change set                              #
# --------------------------------------------------------------------------- #

def test_add_agent_then_move_agent_in_one_change_set_applies():
    text, refusals = _apply(
        {"op": "add_agent", "agent": "data-analyst",
         "about": "Answers questions from our data"},
        _move("data-analyst", "workhorse"))
    assert refusals == [], _reasons(refusals)
    doc = yaml.safe_load(text)
    assert doc["agents"]["data-analyst"] == "workhorse"
    assert "data-analyst" not in doc["ungrouped"]
    assert doc["session_agents"] == SESSION + ["data-analyst"]
    assert mf.policy_errors(doc) == []


def test_add_agent_alone_puts_the_agent_in_session_agents():
    text, refusals = _apply({"op": "add_agent", "agent": "data-analyst",
                             "about": "Answers questions from our data"})
    assert refusals == []
    doc = yaml.safe_load(text)
    assert doc["ungrouped"] == SESSION + ["data-analyst"]
    assert doc["session_agents"] == SESSION + ["data-analyst"]


def test_move_agent_before_add_agent_is_refused_in_plain_words(tmp_path):
    proc, errors, bullets = _cli_apply(
        tmp_path, _move("data-analyst", "workhorse"),
        {"op": "add_agent", "agent": "data-analyst", "about": "Answers questions"})
    assert proc.returncode == 2
    lines = _refusal_lines(errors)
    assert lines and any("there is no agent named data-analyst — add it first" in line
                         for line in lines), errors
    for line in errors + bullets:
        assert_plain(line)


# --------------------------------------------------------------------------- #
# 5. Every family of refusal prints plain words                                #
# --------------------------------------------------------------------------- #

def _hand_edited(tmp_path: Path, edit) -> Path:
    path = tmp_path / "models.yaml"
    path.write_text(edit(_text()))
    return path


def _with_ghost(text: str) -> str:
    return text.replace("ungrouped:\n", "ungrouped:\n  - ghost\n", 1)


def _discovery_on_workhorse(text: str) -> str:
    assert "  on_new_model: advisory\n" in text
    return text.replace("  on_new_model: advisory\n", "  on_new_model: workhorse\n", 1)


def _flow_agents(text: str) -> str:
    raw = yaml.safe_load(text)
    lines = text.splitlines(keepends=True)
    start = lines.index("agents:\n")
    end = next(i for i in range(start + 1, len(lines))
               if lines[i].strip() and not lines[i][0].isspace()
               and not lines[i].startswith("#"))
    flow = ", ".join(f"{k}: {v}" for k, v in raw["agents"].items())
    return "".join(lines[:start] + [f"agents: {{{flow}}}\n\n"] + lines[end:])


FAMILIES = [
    # (id, change, edit of the file or None, a phrase the plain line carries)
    ("rules 3 and 8", _move("critic", "workhorse"), None, "Reviewers"),
    ("rule 10", _move("engineer", "judgement"), None, "Planners"),
    ("no such group", _move("proof", "nowhere"), None, "no group called nowhere"),
    ("already there", _move("proof", "workhorse"), None, "already in the Builders group"),
    ("no such agent", _move("nobody", "workhorse"), None, "no agent named nobody"),
    ("no registry entry", _move("ghost", "workhorse"), _with_ghost, "ghost"),
    ("already an agent", {"op": "add_agent", "agent": "engineer", "about": "Builds"},
     None, "already an agent named engineer"),
    ("already fails", _move("proof", "advisory"), _discovery_on_workhorse,
     "nothing to do with this change"),
    ("could not be edited", _move("proof", "advisory"), _flow_agents, "by hand"),
]


@pytest.mark.parametrize("change, edit, phrase",
                         [f[1:] for f in FAMILIES], ids=[f[0] for f in FAMILIES])
def test_every_refusal_line_and_summary_bullet_is_plain_words(tmp_path, change, edit,
                                                              phrase):
    config = _hand_edited(tmp_path, edit) if edit else None
    proc, errors, bullets = _cli_apply(tmp_path, change, config=config)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    lines = _refusal_lines(errors)
    assert lines, proc.stdout
    assert any(phrase in line for line in lines), lines
    assert bullets, "the refusal reaches the summary page"
    for line in errors + bullets:
        assert_plain(line)


def test_a_move_line_names_the_change_as_the_pull_request_body_does(tmp_path):
    _, errors, _ = _cli_apply(tmp_path, _move("critic", "workhorse"))
    lines = _refusal_lines(errors)
    head = mcc.describe(_move("critic", "workhorse"), yaml.safe_load(_text())).rstrip(".")
    assert head == "Move critic from the Reviewers group into the Builders group"
    assert all(line.startswith(head) for line in lines), lines
    assert len(lines) == 1, "rules 3 and 8 say one thing about the critic, once"


def test_a_file_that_already_fails_sends_its_findings_to_the_log_only(tmp_path):
    config = _hand_edited(tmp_path, _discovery_on_workhorse)
    proc, errors, bullets = _cli_apply(tmp_path, _move("proof", "advisory"),
                                       config=config)
    assert proc.returncode == 2
    assert "discovery.on_new_model" in proc.stdout, "the findings reach the log"
    assert not any("discovery" in line for line in errors + bullets)
    assert any("nothing was written" in line.lower() for line in errors)


def test_the_registry_line_still_refuses_a_name_that_is_in_neither(tmp_path):
    raw_text = _with_ghost(_text())
    _, refusals = _apply(_move("ghost", "workhorse"), text=raw_text)
    assert refusals and refusals[0]["index"] == 1
    assert "session_agents" in _reasons(refusals)
    config = _hand_edited(tmp_path, _with_ghost)
    proc, errors, _ = _cli_apply(tmp_path, _move("ghost", "workhorse"), config=config)
    assert proc.returncode == 2
    for line in errors:
        assert_plain(line)


# --------------------------------------------------------------------------- #
# 6. The published file and the change script agree, move for move            #
# --------------------------------------------------------------------------- #

def _choices() -> dict:
    return json.loads(CHOICES.read_text(encoding="utf-8"))


def test_every_agent_carries_runs_in_in_the_contract_order():
    for agent in _choices()["agents"]:
        assert list(agent) == ["name", "label", "about", "group", "runs_in", "moves"]
        expected = "session" if agent["name"] in SESSION else "pipeline"
        assert agent["runs_in"] == expected, agent["name"]


def test_every_agent_has_a_move_for_every_group_it_is_not_in():
    for agent in _choices()["agents"]:
        others = [g for g in GROUPS if g != agent["group"]]
        assert [m["group"] for m in agent["moves"]] == others, agent["name"]
        assert agent["moves"], agent["name"]
    for name in SESSION:
        agent = next(a for a in _choices()["agents"] if a["name"] == name)
        assert len(agent["moves"]) == 3
        assert all(m["allowed"] for m in agent["moves"]), agent


def test_every_published_move_is_what_apply_check_answers(tmp_path):
    """The full grid, through the real CLI against the real file: `allowed` is
    its exit code, and a refused move's `reason` is the line it prints."""
    checked = 0
    for agent in _choices()["agents"]:
        for move in agent["moves"]:
            proc, errors, _ = _cli_apply(tmp_path, _move(agent["name"], move["group"]))
            assert proc.returncode in (0, 2), proc.stdout + proc.stderr
            assert move["allowed"] is (proc.returncode == 0), (agent["name"], move)
            if move["allowed"]:
                assert move["reason"] is None
            else:
                assert move["reason"] == " ".join(_refusal_lines(errors)), (
                    agent["name"], move)
                assert_plain(move["reason"])
            checked += 1
    raw = yaml.safe_load(_text())
    assert checked == len(raw["agents"]) * 2 + len(SESSION) * 3
