"""Every step that reads the model home holds the token that reads it (DRE-6940).

The selector reads the one home for model choices (DRE-6939) with the token in
`BUREAU_MODEL_HOME_TOKEN`, and every reusable workflow here mints its boot
token for the calling repository only — so a run in another organization held
nothing that could read the home, and selected on the defaults in the code
whatever the console said. `scripts/check_model_home_scope.py` holds every
home-reading step to a token minted in its own job, and every script that
reaches the home to a `READS_MODEL_HOME` declaration the workflow side reads.

These tests run that check against the live tree and against a synthetic
tree, one case per failure it must catch and one tree it must pass.
"""

import ast
import copy
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import check_model_home_scope as scope  # noqa: E402

WORKFLOWS = ROOT / ".github" / "workflows"
TOKEN = "${{ steps.model_home.outputs.token }}"
PINNED = "actions/create-github-app-token@bcd2ba49218906704ab6c1aa796996da409d3eb1"

# The twenty steps the card names, as (workflow, step name).
NAMED_STEPS = {
    ("agent-fix.yml", "Select model"),
    ("agent-task.yml", "Select model"),
    ("groomer.yml", "Select model"),
    ("medic.yml", "Select model"),
    ("plan.yml", "Select model"),
    ("plan.yml", "Select model — one-off critic"),
    ("plan.yml", "Select model — first critic"),
    ("plan.yml", "Select model — second critic"),
    ("proof-task.yml", "Select model"),
    ("qa-review.yml", "Select model"),
    ("red-main-repair.yml", "Select model"),
    ("verify.yml", "Select model"),
    ("plan.yml", "Classify the card — one-off, epic or wave"),
    ("plan.yml", "Plan epic — out of capacity?"),
    ("plan.yml", "Re-plan after send-back — out of capacity?"),
    ("plan.yml", "Re-plan after the second critic sent it back — out of capacity?"),
    ("plan.yml", "Roll-up route — split into child epics — out of capacity?"),
    ("groomer.yml", "Groom"),
    ("groomer.yml", "Post the verified proposal"),
    ("harness.yml", "Run harness scenarios"),
}

# Steps that run a home-reading script in a way that reads nothing.
NON_READING_STEPS = {
    ("plan.yml", "Plan epic — finished?"),
    ("plan.yml", "Re-plan after send-back — finished?"),
    ("plan.yml", "Re-plan after the second critic sent it back — finished?"),
    ("plan.yml", "Roll-up route — split into child epics — finished?"),
    ("model-trial.yml", "Effort level for the candidate"),
}


def _live_scripts():
    _violations, _found, scripts = scope.check_declarations(ROOT / "scripts")
    return scripts


def _live_reading_steps():
    scripts = _live_scripts()
    packages = scope.star_packages(scripts)
    found = {}
    for path in sorted(WORKFLOWS.glob("*.yml")):
        doc = yaml.safe_load(path.read_text())
        if not isinstance(doc, dict):
            continue
        for job_id, i, step, _reasons in scope.reading_steps(doc, scripts, packages):
            found[(path.name, step.get("name"))] = (doc, job_id, i, step)
    return found


# --------------------------------------------------------------------------- #
# The live tree                                                                #
# --------------------------------------------------------------------------- #

def test_live_tree_passes(capsys):
    assert scope.main(["check_model_home_scope.py", str(ROOT)]) == 0, capsys.readouterr().out


def test_every_named_step_reads_the_home_and_holds_the_token():
    found = _live_reading_steps()
    missing = NAMED_STEPS - set(found)
    assert not missing, f"steps the check does not see as reading the home: {sorted(missing)}"
    for (name, step_name), (doc, job_id, i, step) in sorted(found.items()):
        if (name, step_name) not in NAMED_STEPS:
            continue
        value = step.get("env", {}).get("BUREAU_MODEL_HOME_TOKEN")
        mint_id = scope.mint_id_of(value)
        assert mint_id, (name, step_name, value)
        steps = doc["jobs"][job_id]["steps"]
        mints = [s for s in steps[:i] if s.get("id") == mint_id]
        assert mints, f"{name}: job {job_id} mints no {mint_id} before {step_name!r}"
        mint = mints[-1]
        assert mint["name"].startswith("Mint the model-home read token") \
            or mint["name"].startswith("Re-mint the model-home read token")
        assert mint["uses"].startswith(PINNED)
        assert mint["continue-on-error"] is True
        assert mint["with"] == {
            "app-id": "${{ secrets.BUREAU_APP_ID }}",
            "private-key": "${{ secrets.BUREAU_APP_PRIVATE_KEY }}",
            "owner": "dreadnought-foundry",
            "repositories": "bureau-pipeline",
        }


def test_steps_that_read_nothing_are_not_counted():
    found = _live_reading_steps()
    assert not NON_READING_STEPS & set(found)
    # A step reading nothing is handed no token either.
    for path in sorted(WORKFLOWS.glob("*.yml")):
        doc = yaml.safe_load(path.read_text())
        for job in (doc.get("jobs") or {}).values():
            for step in (job or {}).get("steps") or []:
                if (path.name, step.get("name")) in NON_READING_STEPS:
                    assert "BUREAU_MODEL_HOME_TOKEN" not in (step.get("env") or {})


def test_the_token_never_leaves_its_step():
    """Not a job output, not $GITHUB_ENV — the agent steps in the same job
    never hold it."""
    for path in sorted(WORKFLOWS.glob("*.yml")):
        text = path.read_text()
        doc = yaml.safe_load(text)
        for job_id, job in (doc.get("jobs") or {}).items():
            outputs = yaml.safe_dump((job or {}).get("outputs") or {})
            assert "steps.model_home" not in outputs, (path.name, job_id)
            assert "BUREAU_MODEL_HOME_TOKEN" not in ((job or {}).get("env") or {}), (path.name, job_id)
        assert "BUREAU_MODEL_HOME_TOKEN" not in (doc.get("env") or {}), path.name
        for line in text.splitlines():
            if "GITHUB_ENV" in line:
                assert "BUREAU_MODEL_HOME_TOKEN" not in line, (path.name, line)


@pytest.mark.parametrize("rel, want", [
    ("planning_classify.py", ("*",)),
    ("groom_judgement.py", ("*",)),
    ("groomer.py", ("*",)),
    ("harness/agent_run.py", ("*",)),
    ("planner_capacity.py", ("decide",)),
    ("model_fallback.py", ("select",)),
    ("model_home.py", ("read",)),
])
def test_each_reader_declares_its_commands(rel, want):
    tree = ast.parse((ROOT / "scripts" / rel).read_text())
    assert scope.declared(tree) == want


def test_the_walk_derives_every_reader_on_main():
    found, _scripts = scope.readers(ROOT / "scripts")
    assert {
        "model_fallback.py", "model_home.py", "planning_classify.py",
        "planner_capacity.py", "groom_judgement.py", "groomer.py",
        "harness/agent_run.py",
    } <= set(found)
    assert "groom_judgement._pick_model" not in found["groom_judgement.py"]
    assert "planning_classify._pick_model" in found["groom_judgement.py"]
    assert "child process" in found["harness/agent_run.py"]


def test_tests_workflow_runs_the_check():
    doc = yaml.safe_load((WORKFLOWS / "tests.yml").read_text())
    runs = [
        (step.get("name"), step.get("run"))
        for job in doc["jobs"].values()
        for step in (job or {}).get("steps") or []
    ]
    assert ("Every step that reads the model home holds its token",
            "python3 scripts/check_model_home_scope.py") in runs


# --------------------------------------------------------------------------- #
# A synthetic tree                                                             #
# --------------------------------------------------------------------------- #

SCRIPTS = {
    "model_fallback.py": '''
        READS_MODEL_HOME = ("select",)

        def kind_for(role): ...
        def ladder_for(role, repo=None): ...
        def fallback_for(role, model, repo=None): ...
        def select_with_reasons(role): ...
        def select(role): ...
        def effort_for(model): ...
    ''',
    "model_home.py": '''
        READS_MODEL_HOME = ("read",)

        def read(): ...
    ''',
    "planner_capacity.py": '''
        import model_fallback

        READS_MODEL_HOME = ("decide",)

        def decide(role, model):
            return model_fallback.fallback_for(role, model)
    ''',
    "groomer.py": '''
        import model_fallback

        READS_MODEL_HOME = ("*",)

        def _build():
            return model_fallback.select("planner")
    ''',
    # Reads nothing: `effort` is outside the selector's tuple.
    "effort_reader.py": '''
        import subprocess

        def effort(model):
            return subprocess.run(["model_fallback.py", "effort", model])
    ''',
    "harness/__init__.py": "",
    "harness/agent_run.py": '''
        import os
        import sys

        READS_MODEL_HOME = ("*",)

        def _select_model(root, runner):
            return runner([sys.executable,
                           os.path.join(root, "scripts", "model_fallback.py"),
                           "select", "engineer"])
    ''',
}

MINT = {
    "name": "Mint the model-home read token",
    "id": "model_home",
    "continue-on-error": True,
    "uses": f"{PINNED} # v3.2.0",
    "with": {
        "app-id": "${{ secrets.BUREAU_APP_ID }}",
        "private-key": "${{ secrets.BUREAU_APP_PRIVATE_KEY }}",
        "owner": "dreadnought-foundry",
        "repositories": "bureau-pipeline",
    },
}

HOLDS = {"env": {"BUREAU_MODEL_HOME_TOKEN": TOKEN}}

STEPS = {
    "decide": {"name": "Out of capacity?",
               "run": 'python3 .bureau-pipeline/scripts/planner_capacity.py decide "$EXEC_FILE"'},
    "select": {"name": "Select model",
               "run": 'MODEL=$(python3 .bureau-pipeline/scripts/model_fallback.py select fixer \\\n'
                      '  --explain-file "$RUNNER_TEMP/why")'},
    "inline": {"name": "Post",
               "run": "python3 -c 'import json, sys; sys.path.insert(0, \"s\"); "
                      "import groomer; print(groomer.render())'"},
    "harness": {"name": "Run harness scenarios",
                "run": 'cd .bureau-pipeline/scripts\npython3 -m harness --repo "$R"'},
}

# Steps that read nothing and carry no token.
QUIET = [
    {"name": "Effort", "run": 'EFFORT=$(python3 .bureau-pipeline/scripts/model_fallback.py effort "$M")'},
    {"name": "Marker", "run": "python3 -c 'import sys; sys.path.insert(0, \"s\"); "
                              "import model_fallback; print(model_fallback.effort_for(\"m\"))'"},
    {"name": "Finish", "run": "python3 .bureau-pipeline/scripts/planner_capacity.py finish x"},
]


def _write(root, scripts=None, workflows=None):
    for rel, text in (SCRIPTS if scripts is None else scripts).items():
        path = root / "scripts" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text))
    wf = root / ".github" / "workflows"
    wf.mkdir(parents=True, exist_ok=True)
    for name, doc in (workflows or {}).items():
        (wf / name).write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True))
    return root


def _workflow(*steps, mint=True):
    """One job: the mint (optionally), then each step."""
    return {"name": "w", "on": {"workflow_call": {}},
            "jobs": {"j": {"runs-on": "ubuntu-latest",
                           "steps": copy.deepcopy(([MINT] if mint else []) + list(steps))}}}


def _held(key):
    return {**STEPS[key], **HOLDS}


def _clean():
    return {"w.yml": _workflow(*[_held(k) for k in STEPS], *QUIET)}


def _violations(root):
    violations, _stats = scope.check_tree(root)
    return violations


def test_synthetic_clean_tree_passes(tmp_path):
    root = _write(tmp_path, workflows=_clean())
    violations, stats = scope.check_tree(root)
    assert violations == []
    assert stats["steps"] == len(STEPS)


def test_quiet_steps_and_an_effort_command_list_need_nothing(tmp_path):
    root = _write(tmp_path, workflows={"w.yml": _workflow(*QUIET, mint=False)})
    violations, stats = scope.check_tree(root)
    assert violations == []
    assert stats["steps"] == 0
    found, _scripts = scope.readers(root / "scripts")
    assert "effort_reader.py" not in found


@pytest.mark.parametrize("key, says", [
    ("decide", "planner_capacity.py decide"),
    ("select", "model_fallback.py select"),
    ("inline", "imports groomer inline"),
    ("harness", "python3 -m harness"),
])
def test_a_reading_step_without_the_token_fails(tmp_path, key, says):
    steps = [STEPS[key] if k == key else _held(k) for k in STEPS]
    root = _write(tmp_path, workflows={"w.yml": _workflow(*steps)})
    violations = _violations(root)
    assert len(violations) == 1, violations
    assert "w.yml: job j: step" in violations[0]
    assert repr(STEPS[key]["name"]) in violations[0]
    assert says in violations[0]
    assert "no BUREAU_MODEL_HOME_TOKEN in scope" in violations[0]


def test_the_token_in_job_scope_counts(tmp_path):
    doc = _workflow(*STEPS.values())
    doc["jobs"]["j"]["env"] = {"BUREAU_MODEL_HOME_TOKEN": TOKEN}
    root = _write(tmp_path, workflows={"w.yml": doc})
    assert _violations(root) == []


def test_a_literal_token_fails(tmp_path):
    literal = {**STEPS["select"], "env": {"BUREAU_MODEL_HOME_TOKEN": "ghs_literal"}}
    root = _write(tmp_path, workflows={"w.yml": _workflow(literal)})
    violations = _violations(root)
    assert len(violations) == 1, violations
    assert "'ghs_literal'" in violations[0] and "must be exactly" in violations[0]


def test_another_steps_token_fails(tmp_path):
    other = {**STEPS["select"], "env": {"BUREAU_MODEL_HOME_TOKEN": "${{ steps.app.outputs.token }}"}}
    root = _write(tmp_path, workflows={"w.yml": _workflow(other)})
    assert len(_violations(root)) == 1


def test_a_job_with_no_mint_fails(tmp_path):
    root = _write(tmp_path, workflows={"w.yml": _workflow(_held("decide"), mint=False)})
    violations = _violations(root)
    assert len(violations) == 1, violations
    assert "job j has no mint step (id: model_home) before it" in violations[0]


def test_a_mint_after_the_step_fails(tmp_path):
    doc = _workflow(_held("select"), mint=False)
    doc["jobs"]["j"]["steps"].append(copy.deepcopy(MINT))
    root = _write(tmp_path, workflows={"w.yml": doc})
    assert any("no mint step" in v for v in _violations(root))


def test_a_mint_without_continue_on_error_fails(tmp_path):
    doc = _workflow(_held("select"))
    del doc["jobs"]["j"]["steps"][0]["continue-on-error"]
    root = _write(tmp_path, workflows={"w.yml": doc})
    violations = _violations(root)
    assert len(violations) == 1, violations
    assert "w.yml: job j: mint step model_home lacks `continue-on-error: true`" in violations[0]


@pytest.mark.parametrize("key, value", [
    ("owner", "EveryBite"),
    ("repositories", "atlas"),
])
def test_a_mint_for_the_wrong_scope_fails(tmp_path, key, value):
    doc = _workflow(_held("select"))
    doc["jobs"]["j"]["steps"][0]["with"][key] = value
    root = _write(tmp_path, workflows={"w.yml": doc})
    violations = _violations(root)
    assert len(violations) == 1, violations
    assert key in violations[0]


# DRE-7230: the pinned action declares no variables input, so a subset never
# holds variables — `permission-variables` was forwarded undeclared and refused.
@pytest.mark.parametrize("subset", [
    {"permission-variables": "read"},
    {"permission-contents": "read"},
    {"permission-actions": "read", "permission-variables": "write"},
])
def test_a_mint_asking_for_a_subset_without_variables_fails(tmp_path, subset):
    doc = _workflow(_held("select"))
    doc["jobs"]["j"]["steps"][0]["with"].update(subset)
    root = _write(tmp_path, workflows={"w.yml": doc})
    violations = _violations(root)
    assert len(violations) == 1, violations
    assert "w.yml: job j: mint step model_home asks for a permission subset" in violations[0]
    assert "without variables" in violations[0]
    for key in subset:
        assert key in violations[0]


def test_a_declared_variables_input_may_be_the_subset(tmp_path, monkeypatch):
    """Should a pinned release declare a variables input, a mint may ask for
    that one input — and still nothing else."""
    monkeypatch.setattr(scope, "VARIABLES_INPUTS", ("permission-repository-variables",))
    doc = _workflow(_held("select"))
    doc["jobs"]["j"]["steps"][0]["with"]["permission-repository-variables"] = "read"
    root = _write(tmp_path, workflows={"w.yml": doc})
    assert _violations(root) == []
    doc["jobs"]["j"]["steps"][0]["with"]["permission-contents"] = "read"
    root = _write(tmp_path, workflows={"w.yml": doc})
    violations = _violations(root)
    assert len(violations) == 1, violations
    assert "permission-contents" in violations[0]
    assert "permission-repository-variables" not in violations[0]


def _write_mint():
    """A write mint: read by no selecting step, and no continue-on-error, so
    a refused write is red."""
    mint = {**copy.deepcopy(MINT), "id": "model_home_write",
            "name": "Mint the model-home write token"}
    del mint["continue-on-error"]
    return mint


def test_a_write_mint_with_no_subset_passes(tmp_path):
    doc = _workflow(_held("select"), _write_mint())
    root = _write(tmp_path, workflows={"w.yml": doc})
    assert _violations(root) == []


@pytest.mark.parametrize("key, value, says", [
    ("permission-variables", "write", "asks for a permission subset"),
    ("permission-contents", "write", "asks for a permission subset"),
    ("repositories", "atlas", "repositories"),
])
def test_a_write_mint_no_step_reads_is_still_held(tmp_path, key, value, says):
    write = _write_mint()
    write["with"][key] = value
    root = _write(tmp_path, workflows={"w.yml": _workflow(_held("select"), write)})
    violations = _violations(root)
    assert len(violations) == 1, violations
    assert "w.yml: job j: mint step model_home_write" in violations[0]
    assert says in violations[0]


def test_every_live_model_home_mint_asks_for_no_subset():
    """All of them — the 23 read mints and both write mints."""
    found = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        doc = yaml.safe_load(path.read_text())
        for job_id, _i, step in scope._steps(doc):
            if scope.is_model_home_mint(step):
                found.append((path.name, job_id, step["id"]))
                assert step["uses"].startswith(PINNED), (path.name, step["id"])
                assert not [k for k in step["with"] if k.startswith("permission-")], \
                    (path.name, job_id, step["id"], step["with"])
    assert len(found) == 25, found
    assert sum(mint_id == "model_home_write" for *_, mint_id in found) == 2


MODEL_STEP ={"name": "Plan", "id": "plan",
              "uses": "anthropics/claude-code-action@0000000000000000000000000000000000000000"}


def _re_mint(step_id="model_home_2"):
    return {**copy.deepcopy(MINT), "id": step_id,
            "name": "Re-mint the model-home read token"}


def _reads(key, step_id):
    return {**STEPS[key],
            "env": {"BUREAU_MODEL_HOME_TOKEN": f"${{{{ steps.{step_id}.outputs.token }}}}"}}


def test_a_step_reading_the_mint_across_a_model_step_fails(tmp_path):
    # DRE-6940: an App token lives an hour and a model step may pass it, so a
    # step after one that reads the mint before it reads with a dead token —
    # and the reader, which never raises, selects on the defaults in the code.
    root = _write(tmp_path, workflows={"w.yml": _workflow(MODEL_STEP, _held("decide"))})
    violations = _violations(root)
    assert len(violations) == 1, violations
    assert repr(STEPS["decide"]["name"]) in violations[0]
    assert "minted before model step(s)" in violations[0]
    assert "'Plan'" in violations[0]


def test_a_re_mint_after_the_model_step_passes(tmp_path):
    doc = _workflow(_held("select"), MODEL_STEP, _re_mint(),
                    _reads("decide", "model_home_2"))
    root = _write(tmp_path, workflows={"w.yml": doc})
    violations, stats = scope.check_tree(root)
    assert violations == []
    assert stats["steps"] == 2


def test_a_re_mint_before_the_model_step_fails(tmp_path):
    doc = _workflow(_held("select"), _re_mint(), MODEL_STEP,
                    _reads("decide", "model_home_2"))
    root = _write(tmp_path, workflows={"w.yml": doc})
    violations = _violations(root)
    assert len(violations) == 1, violations
    assert "reads `model_home_2`, minted before model step(s)" in violations[0]


def test_a_step_naming_a_re_mint_the_job_lacks_fails(tmp_path):
    doc = _workflow(MODEL_STEP, _reads("decide", "model_home_2"))
    root = _write(tmp_path, workflows={"w.yml": doc})
    violations = _violations(root)
    assert len(violations) == 1, violations
    assert "job j has no mint step (id: model_home_2) before it" in violations[0]


def test_a_re_mint_for_the_wrong_scope_fails(tmp_path):
    re_mint = _re_mint()
    re_mint["with"]["repositories"] = "atlas"
    doc = _workflow(MODEL_STEP, re_mint, _reads("decide", "model_home_2"))
    root = _write(tmp_path, workflows={"w.yml": doc})
    violations = _violations(root)
    assert len(violations) == 1, violations
    assert "w.yml: job j: mint step model_home_2 has repositories" in violations[0]


def test_a_token_from_another_mint_family_still_fails(tmp_path):
    other = _reads("select", "model_homes")
    root = _write(tmp_path, workflows={"w.yml": _workflow(other)})
    violations = _violations(root)
    assert len(violations) == 1, violations
    assert "must be" in violations[0]


def _undeclared(tmp_path, rel, text):
    scripts = {**SCRIPTS, rel: text}
    return _violations(_write(tmp_path, scripts=scripts, workflows=_clean()))


def test_a_script_calling_a_choice_function_must_declare(tmp_path):
    violations = _undeclared(tmp_path, "capacity_two.py", '''
        import model_fallback

        def below(role, model):
            return model_fallback.fallback_for(role, model)
    ''')
    assert len(violations) == 1, violations
    assert violations[0].startswith("scripts/capacity_two.py: reads the model home")
    assert "capacity_two.below → model_fallback.fallback_for" in violations[0]
    assert "declares no READS_MODEL_HOME" in violations[0]


def test_a_from_import_of_a_choice_function_counts(tmp_path):
    violations = _undeclared(tmp_path, "picker.py", '''
        from model_fallback import ladder_for as rungs

        def pick(role):
            return rungs(role)[0]
    ''')
    assert len(violations) == 1, violations
    assert "scripts/picker.py" in violations[0]


def test_a_script_reaching_through_another_scripts_function_must_declare(tmp_path):
    scripts = {
        **SCRIPTS,
        # Declares — it calls the selector itself.
        "classify.py": '''
            import model_fallback

            READS_MODEL_HOME = ("*",)

            def _pick_model():
                return model_fallback.fallback_for("planner", "m")
        ''',
        "judgement.py": '''
            import classify

            def judge():
                return classify._pick_model()
        ''',
    }
    violations = _violations(_write(tmp_path, scripts=scripts, workflows=_clean()))
    assert len(violations) == 1, violations
    assert "scripts/judgement.py" in violations[0]
    assert "judgement.judge → classify._pick_model → model_fallback.fallback_for" in violations[0]


def test_a_child_process_command_list_under_a_subdirectory_must_declare(tmp_path):
    violations = _undeclared(tmp_path, "harness/runner.py", '''
        import os
        import sys

        def _select_model(root, runner):
            return runner([sys.executable,
                           os.path.join(root, "scripts", "model_fallback.py"),
                           "select", "engineer"])
    ''')
    assert len(violations) == 1, violations
    assert "scripts/harness/runner.py" in violations[0]
    assert "runs model_fallback.py select as a child process" in violations[0]


def test_a_script_that_only_imports_the_selector_needs_nothing(tmp_path):
    assert _undeclared(tmp_path, "marker.py", '''
        import model_fallback

        def marker(text):
            return model_fallback.effort_for(text), model_fallback.CAPACITY
    ''') == []


def test_a_malformed_declaration_fails(tmp_path):
    violations = _undeclared(tmp_path, "odd.py", "READS_MODEL_HOME = 'select'\n")
    assert violations == ["scripts/odd.py: READS_MODEL_HOME must be a literal "
                          "tuple of command names, or (\"*\",)"]


def test_an_empty_tree_is_vacuous_and_fails(tmp_path, capsys):
    root = _write(tmp_path, workflows={"w.yml": _workflow(*QUIET, mint=False)})
    assert scope.main(["check_model_home_scope.py", str(root)]) == 1
    assert "vacuous" in capsys.readouterr().out
