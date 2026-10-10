"""DRE-6634 — a `judgement` exception reaches every planner seam that picks a
model in Python.

Two scripts pick the planner's model without going through
`model_fallback.py select`: the planning classifier (`_pick_model`, and
`call_with_capacity_fallback` for the fall to the next rung) and the planner
steps' capacity retry (`planner_capacity.decide`). Each takes `repo` and walks
the project's walk, so a project whose planner leads with Sonnet 4.6 gets Sonnet
4.6 at the classifier, and its capacity refusal falls to the rung below on THAT
walk — not off the end of the fleet ladder, where Sonnet 4.6 is the last rung.

The groomer is the one planner seam that takes no repo, by design: its one
ranking call serves every repo in its census. tests/test_groom_judgement.py
passes unchanged, and `test_the_groomer_still_picks_with_no_repo` pins the
omission.

Same temp tree as tests/test_project_model_exceptions.py, with
`projects: {atlas: {judgement: claude-sonnet-4-6}}`. Expected ids are read off
the tree's judgement ladder rather than typed.

Run: cd bureau-pipeline && python3 -m pytest tests/test_project_reaches_planner_pickers.py -q
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from test_project_model_exceptions import ROOT, ladder, make_tree, tree_config

SONNET46 = "claude-sonnet-4-6"
PROJECTS = {"atlas": {"judgement": SONNET46}}
SPEND_LIMIT = ("You've hit your monthly spend limit. Switch to another model to "
               "continue.")


def _refusal(model):
    return [{"type": "system", "subtype": "init", "model": model},
            {"type": "result", "subtype": "success", "is_error": True,
             "num_turns": 1, "total_cost_usd": 0, "duration_ms": 646,
             "result": SPEND_LIMIT}]


def env() -> dict:
    out = dict(os.environ)
    out["BUREAU_FAKE_AVAILABLE"] = "{}"
    out["ANTHROPIC_API_KEY"] = ""  # subscription mode: the top rung, read directly
    return out


def py(tree: Path, code: str):
    proc = subprocess.run(
        [sys.executable, "-c",
         "import json, sys\n"
         f"sys.path.insert(0, {str(tree / 'scripts')!r})\n"
         "import model_fallback, planning_classify, planner_capacity\n" + code],
        capture_output=True, text=True, env=env(), cwd=tree,
    )
    assert proc.returncode == 0, proc.stderr
    assert "REFUSING" not in proc.stderr, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def tree(tmp_path_factory) -> Path:
    return make_tree(tmp_path_factory.mktemp("dre6634-planner"), PROJECTS)


@pytest.fixture(scope="module")
def fleet(tree) -> list[str]:
    return ladder(tree_config(tree), "judgement")


@pytest.fixture(scope="module")
def walk(fleet) -> list[str]:
    return [SONNET46] + [m for m in fleet if m != SONNET46]


def test_the_tree_s_config_loads_with_its_project_entry(tree, fleet, walk):
    assert py(tree, "print(json.dumps(model_fallback.CONFIG['projects']))") == PROJECTS
    assert fleet[-1] == SONNET46, "Sonnet 4.6 must be the fleet ladder's last rung"
    assert walk == ["claude-sonnet-4-6", "claude-fable-5-1", "claude-opus-5-5"]


# --------------------------------------------------------------------------- #
# planning_classify                                                            #
# --------------------------------------------------------------------------- #

def test_pick_model_reads_the_top_of_the_project_walk(tree, fleet, walk):
    got = py(tree, "print(json.dumps([planning_classify.api_key_mode(), "
                   "planning_classify._pick_model(repo='atlas'), "
                   "planning_classify._pick_model(repo='EveryBite/atlas'), "
                   "planning_classify._pick_model()]))")
    assert got == [False, walk[0], walk[0], fleet[0]]


def test_capacity_fallback_falls_to_the_rung_below_on_the_project_walk(tree, walk):
    got = py(tree, f"""
seen = []
def call(model, prompt, **kwargs):
    seen.append([model, sorted(kwargs)])
    if model == {SONNET46!r}:
        raise planning_classify.TransportError(
            "the classification call reported subtype 'success', is_error "
            "True: " + {SPEND_LIMIT!r}, "success")
    return planning_classify.Answer(text="ok", model=model)
answer, on, fell_from, because, calls = planning_classify.call_with_capacity_fallback(
    call, {SONNET46!r}, "p", repo="atlas")
print(json.dumps([on, fell_from, calls, seen]))
""")
    # `repo` steers the walk and is never handed to the transport.
    assert got == [walk[1], SONNET46, 2, [[SONNET46, []], [walk[1], []]]]


def test_with_no_repo_the_last_fleet_rung_has_nowhere_to_fall(tree):
    got = py(tree, f"""
def call(model, prompt, **kwargs):
    raise planning_classify.TransportError("x: " + {SPEND_LIMIT!r}, "success")
try:
    planning_classify.call_with_capacity_fallback(call, {SONNET46!r}, "p")
    print(json.dumps("answered"))
except planning_classify.TransportError:
    print(json.dumps("raised"))
""")
    assert got == "raised"


def test_classify_carries_the_repo_to_the_picker(tree, walk, fleet):
    got = py(tree, """
asked = []
def call(model, prompt, **kwargs):
    asked.append(model)
    return planning_classify.Answer(text="not json", model=model)
# The tree carries scripts/ and config/ only; the prompt's brief is not in it.
planning_classify.prompt_for = lambda card, doc=None: "p"
card ={"identifier": "DRE-1", "title": "t", "description": "d"}
a = planning_classify.classify(card, call=call, repo="atlas")
b = planning_classify.classify(card, call=call)
print(json.dumps([a.asked, b.asked, asked]))
""")
    assert got == [walk[0], fleet[0], [walk[0], fleet[0]]]


def test_run_carries_the_repo_to_classify(tree):
    got = py(tree, """
import inspect
seen = {}
def fake_classify(card, **kwargs):
    seen.update(kwargs)
    return planning_classify.Decision(refusal="stop here")
planning_classify.classify = fake_classify
class Lops:
    def comment_bodies(self, identifier, whole_thread=False):
        return []
import critic_score
critic_score.read_card = lambda lops, identifier: {"identifier": identifier}
planning_classify.roll_up_parent = lambda lops, identifier, doc=None: None
planning_classify.run(Lops(), "DRE-1", repo="EveryBite/atlas")
print(json.dumps(seen.get("repo")))
""")
    assert got == "EveryBite/atlas"


def test_the_classify_command_takes_repo(tree):
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "planning_classify.py"),
         "classify", "--help"],
        capture_output=True, text=True, env=env(),
    )
    assert proc.returncode == 0, proc.stderr
    assert "--repo" in proc.stdout


def test_the_classify_command_hands_repo_to_run(tree):
    got = py(tree, """
seen = []
def fake_run(lops, identifier, **kwargs):
    seen.append(kwargs.get("repo"))
    return planning_classify.Decision(already=True, shape="one-off")
planning_classify.run = fake_run
import types; sys.modules["linear_ops"] = types.ModuleType("linear_ops")
planning_classify.main(["classify", "DRE-1", "--repo", "EveryBite/atlas"])
planning_classify.main(["classify", "DRE-1"])
print(json.dumps(seen))
""")
    assert got == ["EveryBite/atlas", None]


# --------------------------------------------------------------------------- #
# planner_capacity                                                             #
# --------------------------------------------------------------------------- #

def test_decide_retries_on_the_rung_below_on_the_project_walk(tree, walk):
    got = py(tree, f"""
rec = {json.dumps(_refusal(SONNET46)[-1])!r}
import json as j
rec = j.loads(rec)
print(json.dumps([planner_capacity.decide(rec, {SONNET46!r}, repo="EveryBite/atlas"),
                  planner_capacity.decide(rec, {SONNET46!r})]))
""")
    with_repo, without = got
    assert with_repo["retry"] == "true"
    assert with_repo["model"] == walk[1] == "claude-fable-5-1"
    assert without["retry"] == "false"


def test_the_decide_command_writes_the_same_answer(tree, tmp_path, walk):
    record = tmp_path / "exec.json"
    record.write_text(json.dumps(_refusal(SONNET46)))
    outputs = {}
    for name, extra in (("atlas", ["--repo", "atlas"]), ("none", [])):
        out = tmp_path / f"out-{name}"
        proc = subprocess.run(
            [sys.executable, str(tree / "scripts" / "planner_capacity.py"),
             "decide", str(record), "--model", SONNET46, *extra,
             "--github-output", str(out)],
            capture_output=True, text=True, env=env(), cwd=tree,
        )
        assert proc.returncode == 0, proc.stderr
        outputs[name] = dict(line.split("=", 1)
                             for line in out.read_text().splitlines())
    assert outputs["atlas"]["retry"] == "true"
    assert outputs["atlas"]["model"] == walk[1]
    assert outputs["none"]["retry"] == "false"


def test_the_decide_command_takes_repo():
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "planner_capacity.py"),
         "decide", "--help"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "--repo" in proc.stdout


# --------------------------------------------------------------------------- #
# The groomer: no project, by design                                           #
# --------------------------------------------------------------------------- #

def test_the_groomer_still_picks_with_no_repo():
    tree = ast.parse((ROOT / "scripts" / "groom_judgement.py").read_text())
    picks = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in ("_pick_model", "call_with_capacity_fallback")
    ]
    assert {n.func.attr for n in picks} == {"_pick_model", "call_with_capacity_fallback"}
    for node in picks:
        assert "repo" not in {k.arg for k in node.keywords}, node.func.attr


def test_pick_model_says_why_the_groomer_passes_no_repo():
    sys.path.insert(0, str(ROOT / "scripts"))
    import planning_classify

    doc = " ".join((planning_classify._pick_model.__doc__ or "").split())
    assert "groomer" in doc
    assert "fleet ladder" in doc
