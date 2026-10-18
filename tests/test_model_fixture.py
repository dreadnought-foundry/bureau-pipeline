"""The frozen model fixture and its helpers (DRE-6859, DRE-6972).

Every model rule test used to read the committed `config/models.yaml`, so a
legitimate console save (Builders to another model, an agent moved between
groups, a project exception) turned rule tests red. `tests/model_fixture.py`
gives those tests a frozen copy of the three model files, a throwaway tree
built from it under today's code, and the one derivation of which test files
can read the model files at all. Since the selector selects on the home or the
defaults in the code (DRE-6939), it also gives them the frozen choices as a
home document and the helpers that edit one and hand it to a tree. These tests
prove the helpers do what the sibling cards rely on, and they read no git
history: the harness copies the tree without `.git`.
"""
from __future__ import annotations

import ast
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tokenize
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "scripts"))

import model_fixture  # noqa: E402
from model_fixture import (  # noqa: E402
    FIXTURE_DIR,
    FIXTURE_NAME,
    FixtureStale,
    fixture_config,
    fixture_home,
    fixture_module,
    fixture_prices,
    fixture_text,
    fixture_tree,
    home_env,
    ladder,
    lead_with,
    model_reading_tests,
    move_agent,
    with_project,
)

README = FIXTURE_DIR / "README.md"
STALE_SENTENCE = (
    "the frozen model fixture no longer regenerates under today's code — "
    "re-freeze it in this pull request "
    "(tests/fixtures/model-config-frozen/README.md, the refresh rule)"
)


def _ids(rungs) -> list[str]:
    return [r["model"] if isinstance(r, dict) else r for r in rungs]


@pytest.fixture(scope="module")
def tree(tmp_path_factory) -> Path:
    """One fixture tree, shared by the read-only checks below."""
    return fixture_tree(tmp_path_factory.mktemp("fixture") / "tree")


# --------------------------------------------------------------------------- #
# 1. The fixture directory and its README                                      #
# --------------------------------------------------------------------------- #

def test_the_fixture_lives_where_its_name_says():
    assert FIXTURE_NAME == "model-config-frozen"
    assert FIXTURE_DIR == ROOT / "tests" / "fixtures" / FIXTURE_NAME


def test_the_five_files_exist():
    for name in ("models.yaml", "model-prices.yaml", "agents.yaml", "home.json",
                 "README.md"):
        assert (FIXTURE_DIR / name).is_file(), name


def test_the_readme_names_the_commit_and_the_date_it_was_copied_from():
    text = README.read_text()
    assert re.search(r"\b[0-9a-f]{40}\b", text), "no 40-character commit sha"
    assert re.search(r"\b\d{4}-\d{2}-\d{2}\b", text), "no date"


def test_the_readme_carries_both_rules():
    text = " ".join(README.read_text().split())
    # Rule 1: what a test may read the model files for.
    assert "tested against this frozen copy" in text
    assert "never from a literal" in text
    assert "No test asserts a choice the console can change" in text
    # Rule 2: the refresh rule, in the words a stale fixture says.
    assert "the refresh rule" in text
    assert "re-freeze it in this pull request" in README.read_text()
    assert "needs no re-freeze" in text


def test_the_readme_carries_the_rule_for_documents():
    text = " ".join(README.read_text().split())
    assert "home.json" in text
    assert ("proved against the frozen document or a document built in the test"
            in text)
    assert "never compared with what `render-defaults` prints now" in text
    # The refresh rule for the document: re-rendered inside a fixture tree.
    assert "when `render-defaults` changes shape" in text
    assert "inside a tree `fixture_tree` builds" in text


def test_the_readme_says_how_a_tree_is_built_and_whom_the_default_serves():
    text = " ".join(README.read_text().split())
    # The rule for documents, and the rule for the committed files.
    assert "proved on a document, in a tree built with `regenerate=False`" in text
    assert "checks only what must hold for any saved state" in text
    # What the default still does, for which tests, and until when.
    assert "`fixture_tree`'s default still regenerates the literal" in text
    for name in ("tests/test_model_config_change.py", "tests/console_change_walk.py",
                 "tests/test_console_*_keeps_the_suite_green.py",
                 "tests/test_model_config.py", "tests/test_model_choices.py",
                 "tests/test_model_caution.py"):
        assert name in text, name
    assert "until part 3 removes both" in text


def test_the_frozen_config_is_after_session_agents_landed():
    assert "session_agents" in fixture_config()


# --------------------------------------------------------------------------- #
# 1b. The frozen home document                                                 #
# --------------------------------------------------------------------------- #

def test_the_frozen_document_is_the_frozen_files_written_as_a_home():
    """Compared with the frozen files alone, `written_at` and `written_by` set
    aside: neither the live config nor the live defaults in the code enters
    it, so a legitimate save cannot turn it red."""
    doc, cfg = fixture_home(), fixture_config()
    for kind in ("workhorse", "advisory", "judgement"):
        group = doc["groups"][kind]
        assert group["models"] == ladder(cfg, kind), kind
        assert group["label"] == cfg["kinds"][kind]["label"], kind
        assert group["purpose"] == cfg["kinds"][kind]["purpose"], kind
    assert doc["agents"] == cfg["agents"]
    assert doc["projects"] == cfg["projects"]
    assert doc["added_agents"] == {}


def test_the_frozen_document_is_written_by_the_operator_on_the_freeze_date():
    doc = fixture_home()
    assert doc["written_by"] == "operator"
    date = re.search(r"- date: (\d{4}-\d{2}-\d{2})", README.read_text()).group(1)
    assert doc["written_at"].startswith(date)


def test_fixture_home_reads_the_frozen_document_afresh():
    text = (FIXTURE_DIR / "home.json").read_text()
    assert fixture_home() == json.loads(text)
    fixture_home()["agents"]["engineer"] = "advisory"
    assert fixture_home() == json.loads(text)


def test_the_frozen_document_passes_the_home_check(tree):
    """`model_home.py check` inside a tree that runs today's code on the
    frozen files: the document breaks no rule of either tier."""
    proc = subprocess.run(
        [sys.executable, "scripts/model_home.py", "check",
         str(FIXTURE_DIR / "home.json")],
        cwd=tree, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr


# --------------------------------------------------------------------------- #
# 2. The readers                                                               #
# --------------------------------------------------------------------------- #

def test_the_readers_read_the_fixture_not_the_repo():
    text = (FIXTURE_DIR / "models.yaml").read_text()
    assert fixture_text() == text
    assert fixture_config() == yaml.safe_load(text)
    prices = yaml.safe_load((FIXTURE_DIR / "model-prices.yaml").read_text())
    assert fixture_prices() == prices["prices"]


def test_ladder_is_the_rung_ids_of_a_kinds_ladder():
    cfg = {
        "kinds": {"builders": {"ladder": "build"}},
        "ladders": {"build": [{"model": "m-1", "reason": "x"}, "m-2"]},
    }
    assert ladder(cfg, "builders") == ["m-1", "m-2"]
    cfg = fixture_config()
    for kind, spec in cfg["kinds"].items():
        assert ladder(cfg, kind) == _ids(cfg["ladders"][spec["ladder"]])


def test_ladder_answers_a_document_from_its_groups():
    doc = {"groups": {"workhorse": {"label": "B", "purpose": "p",
                                    "models": ["m-1", "m-2"]}}}
    assert ladder(doc, "workhorse") == ["m-1", "m-2"]


def test_ladder_answers_the_same_list_for_the_frozen_document_and_config():
    doc, cfg = fixture_home(), fixture_config()
    for kind in cfg["kinds"]:
        assert ladder(doc, kind) == ladder(cfg, kind), kind
    # Not the same list everywhere: the document's own models are answered.
    assert ladder(doc, "workhorse") != ladder(doc, "advisory")


# --------------------------------------------------------------------------- #
# 2b. The document editors                                                     #
# --------------------------------------------------------------------------- #

def test_lead_with_puts_a_model_first_and_leaves_the_rest_in_order():
    doc = fixture_home()
    rungs = ladder(doc, "workhorse")
    last = rungs[-1]
    led = lead_with(doc, "workhorse", last)
    assert ladder(led, "workhorse") == [last, *rungs[:-1]]
    # A model on no ladder of the group joins it at the top.
    assert ladder(lead_with(doc, "workhorse", "m-new"), "workhorse") == ["m-new", *rungs]
    # A new document: the one given is untouched, and so is every other group.
    assert doc == fixture_home()
    assert led["groups"]["advisory"] == doc["groups"]["advisory"]
    assert led["groups"]["workhorse"] is not doc["groups"]["workhorse"]


def test_move_agent_puts_an_agent_in_another_group():
    doc = fixture_home()
    agent, now = next(iter(doc["agents"].items()))
    other = next(group for group in doc["groups"] if group != now)
    moved = move_agent(doc, agent, other)
    assert moved["agents"][agent] == other
    assert {k: v for k, v in moved["agents"].items() if k != agent} == {
        k: v for k, v in doc["agents"].items() if k != agent}
    assert doc == fixture_home()


def test_with_project_gives_a_project_a_model_of_its_own():
    doc = fixture_home()
    first = with_project(doc, "bureau-pipeline", "advisory", "m-1")
    assert first["projects"]["bureau-pipeline"] == {"advisory": "m-1"}
    second = with_project(first, "bureau-pipeline", "workhorse", "m-2")
    assert second["projects"]["bureau-pipeline"] == {"advisory": "m-1", "workhorse": "m-2"}
    assert first["projects"]["bureau-pipeline"] == {"advisory": "m-1"}
    for slug, entry in doc["projects"].items():
        assert second["projects"][slug] == entry
    assert doc == fixture_home()


def test_home_env_writes_the_document_and_names_it(tmp_path):
    (tmp_path / "config").mkdir()
    doc = move_agent(fixture_home(), "medic", "workhorse")
    env = home_env(tmp_path, doc, BUREAU_FAKE_AVAILABLE='{"m-1": false}')
    path = tmp_path / "config" / "model-home.json"
    assert json.loads(path.read_text()) == doc
    assert env["BUREAU_MODEL_HOME_FILE"] == str(path)
    assert env["BUREAU_FAKE_AVAILABLE"] == '{"m-1": false}'
    # The rest of the process environment rides along; the process's own is
    # left alone.
    assert env["PATH"] == os.environ["PATH"]
    assert "BUREAU_MODEL_HOME_FILE" not in os.environ


# --------------------------------------------------------------------------- #
# 3. The tree                                                                  #
# --------------------------------------------------------------------------- #

def test_the_tree_holds_todays_code_and_the_frozen_files(tree):
    for path in sorted((ROOT / "scripts").glob("*.py")):
        assert (tree / "scripts" / path.name).is_file(), path.name
    for name in ("models.yaml", "model-prices.yaml"):
        assert (tree / "config" / name).read_bytes() == (FIXTURE_DIR / name).read_bytes()
    assert (tree / "config" / "repo-map.json").read_bytes() == (
        ROOT / "config" / "repo-map.json").read_bytes()
    assert (tree / "config" / "model-choices.json").is_file()


def test_the_tree_is_in_sync(tree):
    proc = subprocess.run(
        [sys.executable, "scripts/sync_model_config.py", "--check"],
        cwd=tree, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_the_tree_passes_the_policy(tree):
    mf = fixture_module(tree)
    raw = yaml.safe_load((tree / "config" / "models.yaml").read_text())
    assert mf.policy_errors(raw) == []


def test_the_module_runs_on_the_fixture_and_leaves_the_repo_module_alone(tree):
    import model_fallback as repo_mf

    before = sys.modules["model_fallback"]
    mf = fixture_module(tree)
    assert Path(mf.__file__).resolve().parent == (tree / "scripts").resolve()
    assert mf is not repo_mf
    cfg = fixture_config()
    assert mf.CONFIG["ladders"] == {
        name: _ids(rungs) for name, rungs in cfg["ladders"].items()}
    assert sys.modules["model_fallback"] is before
    assert Path(repo_mf.__file__).resolve() == ROOT / "scripts" / "model_fallback.py"


def test_a_module_from_one_tree_is_not_a_module_from_another(tmp_path, tree):
    other = fixture_tree(tmp_path / "other")
    assert fixture_module(other).__name__ != fixture_module(tree).__name__


# --------------------------------------------------------------------------- #
# 3b. A tree selecting on a document                                           #
# --------------------------------------------------------------------------- #

def _demoted_rung(tree: Path) -> str:
    """A Builders rung of the tree's own config that is not its top, so a
    document leading with it cannot be confused with the defaults."""
    rungs = ladder(yaml.safe_load((tree / "config" / "models.yaml").read_text()),
                   "workhorse")
    assert len(rungs) > 1
    return rungs[-1]


def test_a_document_through_home_env_makes_the_tree_select_on_it(tree):
    m = _demoted_rung(tree)
    doc = lead_with(fixture_home(), "workhorse", m)
    proc = subprocess.run(
        [sys.executable, "scripts/model_fallback.py", "select", "engineer"],
        cwd=tree, capture_output=True, text=True,
        env=home_env(tree, doc, BUREAU_FAKE_AVAILABLE="{}"))
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == m
    assert "model-home: read" in proc.stderr


def test_fixture_module_with_a_document_has_already_read_it(tree, monkeypatch):
    monkeypatch.setenv("BUREAU_MODEL_HOME_FILE", "/elsewhere/home.json")
    m = _demoted_rung(tree)
    doc = lead_with(fixture_home(), "workhorse", m)
    mf = fixture_module(tree, doc=doc)
    # The environment is put back before the module is called, and the module
    # still selects on the document it read while the name was set.
    assert os.environ["BUREAU_MODEL_HOME_FILE"] == "/elsewhere/home.json"
    monkeypatch.delenv("BUREAU_MODEL_HOME_FILE")
    assert mf.HOME_STATUS == "read"
    assert mf.ladder_for("engineer")[0] == m
    decision = mf.select_with_reasons("engineer", probe=lambda model: True,
                                      clock=lambda: 0.0)
    assert decision["model"] == m
    assert decision["degraded"] is False


def test_fixture_module_with_a_document_leaves_the_environment_unset(tree):
    m = _demoted_rung(tree)
    mf = fixture_module(tree, doc=lead_with(fixture_home(), "workhorse", m))
    assert "BUREAU_MODEL_HOME_FILE" not in os.environ
    assert mf.ladder_for("engineer")[0] == m


def test_fixture_module_with_a_document_leaves_the_repo_modules_alone(tree):
    import model_fallback as repo_mf

    before = {name: sys.modules.get(name) for name in ("model_fallback", "model_home")}
    m = _demoted_rung(tree)
    fixture_module(tree, doc=lead_with(fixture_home(), "workhorse", m))
    assert {name: sys.modules.get(name) for name in before} == before
    assert sys.modules["model_fallback"] is repo_mf
    assert repo_mf.HOME_STATUS == ""


def test_fixture_module_without_a_document_reads_nothing_and_runs_on_the_tree(tree):
    """`doc=None` loads the module exactly as before: its first choice call
    finds no home named, reads `not set`, and selects on the tree's own
    defaults in the code, with no `model-home` clause and no `DEGRADED`."""
    mf = fixture_module(tree)
    assert mf.HOME_STATUS == ""
    top = ladder(yaml.safe_load((tree / "config" / "models.yaml").read_text()),
                 "workhorse")[0]
    decision = mf.select_with_reasons("engineer", probe=lambda model: True,
                                      clock=lambda: 0.0)
    assert decision["model"] == top
    assert decision["degraded"] is False
    assert mf.HOME_STATUS == "not set"
    note = mf.selection_note(decision)
    assert "model-home" not in note
    assert not note.startswith("DEGRADED")


def test_fixture_module_with_a_document_hands_it_to_the_selector_the_module_imported(
        tmp_path):
    """A module that imports the selector — the classifier, the groomer — is
    loaded with the tree's selector already on `doc`."""
    dest = fixture_tree(tmp_path / "tree", regenerate=False)
    # The classifier reads the rest of config/ (the lane contract) as it loads.
    for path in sorted((ROOT / "config").iterdir()):
        if path.is_file() and not (dest / "config" / path.name).exists():
            shutil.copy2(path, dest / "config" / path.name)
    home = fixture_home()
    last = ladder(home, "judgement")[-1]
    classify = fixture_module(dest, "planning_classify",
                              doc=lead_with(home, "judgement", last))
    selector = classify.model_fallback
    assert Path(selector.__file__).resolve().parent == (dest / "scripts").resolve()
    assert selector.HOME_STATUS == "read"
    assert selector.ladder_for("planner")[0] == last
    assert "BUREAU_MODEL_HOME_FILE" not in os.environ


# --------------------------------------------------------------------------- #
# 3c. A tree built without the generator (DRE-6990)                            #
# --------------------------------------------------------------------------- #

def _record_subprocesses(monkeypatch) -> list:
    """Every `subprocess.run` from here on, as `(argv, cwd)`, still run."""
    calls: list = []
    real = subprocess.run

    def run(*args, **kwargs):
        calls.append(([str(a) for a in args[0]], kwargs.get("cwd")))
        return real(*args, **kwargs)

    monkeypatch.setattr(model_fixture.subprocess, "run", run)
    return calls


def _stub_a_refusing_generator(monkeypatch) -> None:
    copy = model_fixture._copy_into

    def copy_then_stub(dest: Path) -> None:
        copy(dest)
        (dest / "scripts" / "sync_model_config.py").write_text(
            "import sys\n"
            "print('stub generator: refusing the frozen config', file=sys.stderr)\n"
            "sys.exit(1)\n")

    monkeypatch.setattr(model_fixture, "_copy_into", copy_then_stub)


def test_regenerate_false_copies_the_tree_and_runs_no_subprocess(tmp_path, monkeypatch):
    calls = _record_subprocesses(monkeypatch)
    dest = fixture_tree(tmp_path / "tree", regenerate=False)
    assert calls == []
    assert dest == tmp_path / "tree"
    # Today's scripts as committed: no generated region was rewritten.
    for path in sorted((ROOT / "scripts").glob("*.py")):
        assert (dest / "scripts" / path.name).read_bytes() == path.read_bytes(), path.name
    for name in ("models.yaml", "model-prices.yaml"):
        assert (dest / "config" / name).read_bytes() == (FIXTURE_DIR / name).read_bytes()
    assert (dest / "agents.yaml").read_bytes() == (FIXTURE_DIR / "agents.yaml").read_bytes()
    assert (dest / "config" / "repo-map.json").read_bytes() == (
        ROOT / "config" / "repo-map.json").read_bytes()
    # The generator writes the console's choices file; nothing wrote it here.
    assert not (dest / "config" / "model-choices.json").exists()


def test_regenerate_false_never_meets_a_refusing_generator(tmp_path, monkeypatch):
    _stub_a_refusing_generator(monkeypatch)
    calls = _record_subprocesses(monkeypatch)
    dest = fixture_tree(tmp_path / "tree", regenerate=False)
    assert calls == []
    assert (dest / "scripts" / "sync_model_config.py").is_file()


def test_the_default_runs_the_generator_in_the_tree_exactly_once(tmp_path, monkeypatch):
    calls = _record_subprocesses(monkeypatch)
    dest = fixture_tree(tmp_path / "tree")
    assert calls == [([sys.executable, str(dest / "scripts" / "sync_model_config.py")], dest)]
    assert (dest / "config" / "model-choices.json").is_file()


def test_regenerate_true_is_the_default(tmp_path, monkeypatch):
    calls = _record_subprocesses(monkeypatch)
    dest = fixture_tree(tmp_path / "tree", regenerate=True)
    assert calls == [([sys.executable, str(dest / "scripts" / "sync_model_config.py")], dest)]


# --------------------------------------------------------------------------- #
# 4. Independent of today's choices                                            #
# --------------------------------------------------------------------------- #

def test_the_tree_selects_what_its_own_config_says(tmp_path):
    dest = fixture_tree(tmp_path / "tree")
    path = dest / "config" / "models.yaml"
    raw = yaml.safe_load(path.read_text())
    workhorse = raw["kinds"][raw["agents"]["engineer"]]["ladder"]
    rungs = raw["ladders"][workhorse]
    second = _ids(rungs)[1]
    rungs[0], rungs[1] = rungs[1], rungs[0]
    path.write_text(yaml.safe_dump(raw, sort_keys=False))
    proc = subprocess.run([sys.executable, "scripts/sync_model_config.py"],
                          cwd=dest, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    # The selector runs on the regenerated literal, never on the YAML's
    # choices (DRE-6939). Its old bytecode goes with it: swapping two rungs
    # keeps the file's size, so a same-second rewrite would load the old one.
    shutil.rmtree(dest / "scripts" / "__pycache__", ignore_errors=True)

    mf = fixture_module(dest)
    assert mf.select("engineer", probe=lambda m: True, clock=lambda: 0.0) == second


# --------------------------------------------------------------------------- #
# 5. The refresh rule is what a stale fixture says                             #
# --------------------------------------------------------------------------- #

def test_a_generator_that_fails_raises_fixture_stale(tmp_path, monkeypatch):
    _stub_a_refusing_generator(monkeypatch)
    calls = _record_subprocesses(monkeypatch)
    dest = tmp_path / "tree"
    with pytest.raises(FixtureStale) as caught:
        fixture_tree(dest)
    assert calls == [([sys.executable, str(dest / "scripts" / "sync_model_config.py")], dest)]
    message = str(caught.value)
    assert STALE_SENTENCE in message
    assert "tests/fixtures/model-config-frozen/README.md" in message
    assert "stub generator: refusing the frozen config" in message


# --------------------------------------------------------------------------- #
# 6. Which test files can read the model files                                 #
# --------------------------------------------------------------------------- #

def _plant(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_model_reading_tests_on_a_planted_tree(tmp_path):
    _plant(tmp_path, "config/models.yaml", yaml.safe_dump({
        "ladders": {"solo": [{"model": "vendor-model-9", "reason": "x"}]}}))
    rung = _plant(tmp_path, "tests/test_rung.py", 'MODEL = "vendor-model-9"\n')
    selector = _plant(tmp_path, "tests/nested/test_selector.py", "import model_fallback\n")
    generator = _plant(tmp_path, "tests/test_generator.py",
                       'CMD = ["python3", "scripts/sync_model_config.py"]\n')
    _plant(tmp_path, "tests/test_unrelated.py", "def test_nothing():\n    pass\n")
    _plant(tmp_path, "tests/node_modules/test_skipped.py", "import model_fallback\n")
    _plant(tmp_path, "tests/.hidden/test_hidden.py", 'MODEL = "vendor-model-9"\n')
    _plant(tmp_path, "tests/helper.py", "import model_fallback\n")

    assert model_reading_tests(tmp_path) == sorted([rung, selector, generator])


@pytest.mark.parametrize("token", [
    "models.yaml", "agents.yaml", "model-choices.json",
    "model_fallback", "sync_model_config", "model_config_change",
])
def test_each_named_file_puts_a_test_in_the_set(tmp_path, token):
    _plant(tmp_path, "config/models.yaml", yaml.safe_dump({"ladders": {"solo": ["m-1"]}}))
    named = _plant(tmp_path, "tests/test_named.py", f"# reads {token}\n")
    assert model_reading_tests(tmp_path) == [named]


def test_model_reading_tests_on_the_repo_finds_the_selector_through_a_workflow():
    found = model_reading_tests(ROOT)
    assert ROOT / "tests" / "test_plan_model_death_swing.py" in found
    assert ROOT / "tests" / "test_model_fixture.py" in found
    assert found == sorted(found)


# --------------------------------------------------------------------------- #
# 7. The planner's path proves its scenarios on documents (DRE-6990)           #
# --------------------------------------------------------------------------- #

# The seven tests of the planner's path that copy a tree and select in it.
PLANNER_PATH = (
    "tests/test_project_reaches_planner_pickers.py",
    "tests/test_model_effort.py",
    "tests/test_plan_model_death_swing.py",
    "tests/test_planner_capacity_retry.py",
    "tests/test_planning_capacity_fallback.py",
    "tests/test_planning_classify_turn_limit.py",
    "tests/test_planning_unread_park.py",
)
OLD_ROUTE = ("sync_model_config", "FixtureStale")
BARE_ID = re.compile(r"claude-[a-z]+-[0-9]")


def _source(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def fixture_tree_calls(text: str) -> list[tuple[int, bool]]:
    """`(line, passes regenerate=False)` for every `fixture_tree(...)` call."""
    found = []
    for node in ast.walk(ast.parse(text)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name != "fixture_tree":
            continue
        off = any(k.arg == "regenerate" and isinstance(k.value, ast.Constant)
                  and k.value.value is False for k in node.keywords)
        found.append((node.lineno, off))
    return found


def old_route_mentions(text: str) -> list[tuple[int, str]]:
    """`(line, name)` for every line of `text` naming the generator or its
    refusal."""
    return [(n, name) for n, line in enumerate(text.splitlines(), 1)
            for name in OLD_ROUTE if name in line]


def bare_ids(text: str) -> list[tuple[int, str]]:
    """`(line, id)` for every model id spelled in `text` outside a comment —
    docstrings and strings included."""
    found = []
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        if tok.type == tokenize.COMMENT:
            continue
        for match in BARE_ID.finditer(tok.string):
            line = tok.start[0] + tok.string.count("\n", 0, match.start())
            found.append((line, match.group(0)))
    return found


def test_every_one_of_the_seven_builds_its_trees_without_the_generator():
    calls = {rel: fixture_tree_calls(_source(rel)) for rel in PLANNER_PATH}
    # Each of the seven builds a tree: a file with no call proves nothing here.
    assert all(calls.values()), {rel: c for rel, c in calls.items() if not c}
    regenerating = {rel: [line for line, off in c if not off] for rel, c in calls.items()}
    assert not any(regenerating.values()), regenerating


def test_none_of_the_seven_names_the_generator_or_its_refusal():
    found = {rel: old_route_mentions(_source(rel)) for rel in PLANNER_PATH}
    assert not any(found.values()), found


def test_none_of_the_seven_spells_a_model_id_outside_a_comment():
    found = {rel: bare_ids(_source(rel)) for rel in PLANNER_PATH}
    assert not any(found.values()), found


def test_the_tree_scan_finds_a_call_that_regenerates():
    text = ("tree = model_fixture.fixture_tree(dest)\n"
            "other = fixture_tree(dest, regenerate=True)\n"
            "third = fixture_tree(dest, regenerate=False)\n")
    assert fixture_tree_calls(text) == [(1, False), (2, False), (3, True)]


def test_the_old_route_scan_finds_the_generator_and_its_refusal():
    text = ("import os\n"
            f"subprocess.run(['scripts/{OLD_ROUTE[0]}.py'])\n"
            f"except model_fixture.{OLD_ROUTE[1]}:\n")
    assert old_route_mentions(text) == [(2, OLD_ROUTE[0]), (3, OLD_ROUTE[1])]


def test_the_id_scan_reads_strings_and_docstrings_and_skips_comments():
    model = "-".join(("claude", "opus", "9"))
    text = (f'"""The scenario.\n\nBuilt on {model}."""\n'
            f"# {model} in a comment is history\n"
            f"MODEL = '{model}'\n")
    assert bare_ids(text) == [(3, model), (5, model)]
