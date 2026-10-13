"""DRE-6939 — the one home for model choices, and the selector that reads it.

The CEO, 2026-10-11, on Settings › Agents & models: "These should all be
externalized and all we're doing is changing the values of the configuration."
The choices — which models a group runs on and in what order, which group an
agent is in, a project's own model — live in one GitHub Actions variable,
`BUREAU_MODEL_CHOICES` on dreadnought-foundry/bureau-pipeline, and every run
reads it when it first asks which model to use: the home first, the defaults in
the code otherwise, never the choices written in `config/models.yaml`.

`scripts/model_home.py` is the reader and the rules; `scripts/model_fallback.py`
reads the home through it once per process, at the first call of one of the
five choice functions. Every test here hands the reader a document of its own
through `BUREAU_MODEL_HOME_FILE`, or a fake transport for the API — none makes
a network call — and every model id it expects is read off that document or off
`model_fallback.code_defaults()`, never typed into an assertion.

Run: cd bureau-pipeline && python3 -m pytest tests/test_model_home.py -q
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import model_fallback as mf  # noqa: E402
import model_fixture  # noqa: E402
import model_home as mh  # noqa: E402

SELECTOR = ROOT / "scripts" / "model_fallback.py"
READER = ROOT / "scripts" / "model_home.py"
NAMES = ("BUREAU_MODEL_HOME_TOKEN", "BUREAU_MODEL_HOME_FILE")

READ_CLAUSE = " · model-home: read"
UNREADABLE_CLAUSE = re.compile(
    r" · model-home: unreadable \((?P<reason>.*)\), running on the defaults in the code$")
NOT_SET_LINE = "model-home: not set — running on the defaults in the code"

# Model ids no release of the pipeline prices today: what a home written on a
# newer `main` can name before the run's own release knows it.
NEWER_MODEL = "claude-opus-5-6"


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #

def defaults_doc(d: dict | None = None) -> dict:
    """The defaults in the code — or the normalized config `d` — written as a
    home document by hand: the shape the contract states, not
    `render_defaults`, so a test of the reader does not lean on the writer."""
    d = mf.code_defaults() if d is None else d
    return {
        "version": 1,
        "written_at": "2026-10-11T18:03:12Z",
        "written_by": "console",
        "groups": {
            key: {"label": d["groups"][key]["label"],
                  "purpose": d["groups"][key]["purpose"],
                  "models": list(d["ladders"][d["kinds"][key]])}
            for key in mf.ROLE_KINDS
        },
        "agents": dict(d["agents"]),
        "added_agents": {},
        "projects": {slug: dict(entry) for slug, entry in d["projects"].items()},
    }


def lead(doc: dict, group: str, model: str) -> dict:
    """`doc` with `model` moved (or added) to the top of `group`."""
    models = [m for m in doc["groups"][group]["models"] if m != model]
    doc["groups"][group]["models"] = [model] + models
    return doc


def scenario_doc() -> dict:
    """The first criterion's home: Builders lead with Sonnet 5.5, `designer`
    is in the Reviewers and atlas's Reviewers lead with Opus 5.5."""
    doc = lead(defaults_doc(), "workhorse", "claude-sonnet-5-5")
    doc["agents"]["designer"] = "advisory"
    doc["projects"]["atlas"] = {"advisory": "claude-opus-5-5"}
    return doc


def write(path: Path, doc) -> Path:
    path.write_text(doc if isinstance(doc, str) else json.dumps(doc))
    return path


def child_env(**names) -> dict:
    """The environment a child process runs with: this one, no home name
    unless the test names it, and every model up without a network call."""
    env = {k: v for k, v in os.environ.items() if k not in NAMES}
    env["BUREAU_FAKE_AVAILABLE"] = "{}"
    env.update({k: str(v) for k, v in names.items()})
    return env


def select_cli(*args: str, tmp: Path, selector: Path = SELECTOR, **names):
    """`(model, note, stderr lines)` from the real selector CLI."""
    note = tmp / "note.txt"
    if note.exists():
        note.unlink()
    proc = subprocess.run(
        [sys.executable, str(selector), "select", *args, "--explain-file", str(note)],
        capture_output=True, text=True, env=child_env(**names), cwd=tmp, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip(), note.read_text().rstrip("\n"), proc.stderr.splitlines()


def reader_cli(*args: str, cwd: Path, reader: Path = READER, **names):
    return subprocess.run(
        [sys.executable, str(reader), *args],
        capture_output=True, text=True, env=child_env(**names), cwd=cwd, timeout=120,
    )


def up(model: str) -> bool:
    return True


def defaults_top(kind: str) -> str:
    d = mf.code_defaults()
    return d["ladders"][d["kinds"][kind]][0]


def defaults_top_for(role: str) -> str:
    """The top of `role`'s group in the defaults in the code — read off the
    defaults, never assumed, since a console save can move `role` to another
    group."""
    return defaults_top(mf.code_defaults()["agents"][role])


def chosen(role: str, **kwargs) -> tuple[dict, str]:
    """`(decision, note)` for `role` in this process, every model up."""
    decision = mf.select_with_reasons(role, probe=up, **kwargs)
    return decision, mf.selection_note(decision)


# --------------------------------------------------------------------------- #
# The home in force                                                            #
# --------------------------------------------------------------------------- #

def test_a_readable_home_drives_the_selector_cli(tmp_path):
    doc = scenario_doc()
    home = write(tmp_path / "home.json", doc)
    model, note, _ = select_cli("engineer", tmp=tmp_path, BUREAU_MODEL_HOME_FILE=home)
    assert model == doc["groups"]["workhorse"]["models"][0]
    assert note.startswith("model-policy:")
    assert note.endswith(READ_CLAUSE)
    model, note, _ = select_cli("critic", "--repo", "EveryBite/atlas", tmp=tmp_path,
                                BUREAU_MODEL_HOME_FILE=home)
    assert model == doc["projects"]["atlas"]["advisory"]
    assert note.startswith("model-policy:")
    assert note.endswith(READ_CLAUSE)


def test_a_session_agent_in_a_home_group_still_selects_nothing(tmp_path, monkeypatch):
    doc = scenario_doc()
    monkeypatch.setenv("BUREAU_MODEL_HOME_FILE", str(write(tmp_path / "home.json", doc)))
    session = next(n for n in mf.SESSION_AGENTS if doc["agents"].get(n) == "advisory")
    assert mf.ladder_for(session) == doc["groups"]["workhorse"]["models"]
    assert mf.ladder_for(session) == mf.LADDER
    assert mf.HOME_STATUS == "read"


def test_the_home_replaces_every_choice_in_place(tmp_path, monkeypatch):
    doc = scenario_doc()
    monkeypatch.setenv("BUREAU_MODEL_HOME_FILE", str(write(tmp_path / "home.json", doc)))
    mf.use_home()
    assert mf.LADDERS == {k: g["models"] for k, g in doc["groups"].items()}
    assert mf.KINDS == {k: k for k in doc["groups"]}
    assert mf.CONFIG["agents"] == doc["agents"]
    assert mf.CONFIG["projects"] == doc["projects"]
    assert mf.AGENT_KINDS == {r: k for r, k in doc["agents"].items()
                              if r not in mf.SESSION_AGENTS}
    assert mf.LADDER == doc["groups"]["workhorse"]["models"]
    labels = {k: g["label"] for k, g in doc["groups"].items()}
    assert {k: g["label"] for k, g in mf.CONFIG["groups"].items()} == labels


def test_a_name_imported_before_the_read_follows_the_home(tmp_path, monkeypatch):
    from model_fallback import AGENT_KINDS, LADDERS

    doc = scenario_doc()
    monkeypatch.setenv("BUREAU_MODEL_HOME_FILE", str(write(tmp_path / "home.json", doc)))
    mf.ladder_for("engineer")
    assert LADDERS["workhorse"][0] == doc["groups"]["workhorse"]["models"][0]
    assert AGENT_KINDS == {r: k for r, k in doc["agents"].items()
                           if r not in mf.SESSION_AGENTS}


def test_reset_home_puts_the_defaults_back_in_place(tmp_path, monkeypatch):
    from model_fallback import LADDERS

    doc = scenario_doc()
    monkeypatch.setenv("BUREAU_MODEL_HOME_FILE", str(write(tmp_path / "home.json", doc)))
    mf.ladder_for("engineer")
    assert LADDERS["workhorse"] == doc["groups"]["workhorse"]["models"]
    mf.reset_home()
    defaults = mf.code_defaults()
    assert LADDERS["workhorse"] == defaults["ladders"]["workhorse"]
    assert mf.LADDER == defaults["ladders"][defaults["default_ladder"]]
    assert mf.HOME_STATUS == ""
    # Forgotten, so the next choice call reads the home again.
    monkeypatch.delenv("BUREAU_MODEL_HOME_FILE")
    mf.ladder_for("engineer")
    assert mf.HOME_STATUS == "not set"


class TestEveryTestStartsNotSet:
    """Run in this order in one session: the first sets a home, the second
    must not see it (tests/conftest.py)."""

    def test_1_a_test_with_a_home_selects_from_it(self, tmp_path, monkeypatch):
        doc = scenario_doc()
        monkeypatch.setenv("BUREAU_MODEL_HOME_FILE", str(write(tmp_path / "h.json", doc)))
        decision, note = chosen("engineer")
        assert decision["model"] == doc["groups"]["workhorse"]["models"][0]
        assert note.endswith(READ_CLAUSE)

    def test_2_the_next_test_runs_on_the_defaults_with_no_clause(self):
        for name in NAMES:
            assert name not in os.environ
        decision, note = chosen("engineer")
        assert decision["model"] == defaults_top_for("engineer")
        assert "model-home" not in note
        assert mf.HOME_STATUS == "not set"


# --------------------------------------------------------------------------- #
# Not set: what main prints                                                    #
# --------------------------------------------------------------------------- #

def test_not_set_is_main_s_note_with_the_tagged_line_before_it(tmp_path):
    model, note, err = select_cli("engineer", tmp=tmp_path)
    assert model == defaults_top_for("engineer")
    role = "engineer"
    kind = mf.kind_for(role)
    assert note == (f"model-policy: {model} chosen for {role} ({kind} kind) — top of "
                    "the ladder, nothing above it was skipped — project none: fleet ladder")
    assert NOT_SET_LINE in err
    assert err.index(NOT_SET_LINE) < err.index(note)


def test_not_set_is_not_degraded():
    decision, note = chosen("engineer")
    assert decision["degraded"] is False
    assert not note.startswith("DEGRADED")
    assert mf.HOME_STATUS == "not set"


def test_a_note_built_before_any_choice_call_carries_no_clause():
    assert mf.HOME_STATUS == ""
    note = mf.selection_note({"model": "m", "role": "r", "kind": "workhorse"})
    assert "model-home" not in note


# --------------------------------------------------------------------------- #
# Unreadable: the defaults, loudly                                             #
# --------------------------------------------------------------------------- #

class FakeTransport:
    """Stands in for the one GET; records the request it was handed."""

    def __init__(self, status=None, body="", raises=None):
        self.status, self.body, self.raises = status, body, raises
        self.calls: list[tuple] = []

    def __call__(self, url, headers, timeout):
        self.calls.append((url, headers, timeout))
        if self.raises is not None:
            raise self.raises
        return self.status, self.body


def assert_unreadable(note: str, decision: dict, *words: str) -> str:
    assert decision["model"] == defaults_top(decision["kind"])
    assert note.startswith("DEGRADED model-policy:"), note
    found = UNREADABLE_CLAUSE.search(note)
    assert found, note
    reason = found.group("reason")
    for word in words:
        assert word in reason, reason
    return reason


def test_an_empty_token_is_unreadable(monkeypatch):
    fake = FakeTransport(200, "{}")
    monkeypatch.setattr(mh, "_http_get", fake)
    monkeypatch.setenv("BUREAU_MODEL_HOME_TOKEN", "")
    decision, note = chosen("engineer")
    assert_unreadable(note, decision, "BUREAU_MODEL_HOME_TOKEN", "empty")
    assert fake.calls == []


def test_a_refused_mint_says_so_plainly(monkeypatch, capsys):
    """DRE-7230: the mint step is continue-on-error and shows as passing, so
    the selector's line is the one sign a mint was refused. It says the mint
    was refused and that the built-in defaults are in use."""
    fake = FakeTransport(200, "{}")
    monkeypatch.setattr(mh, "_http_get", fake)
    monkeypatch.setenv("BUREAU_MODEL_HOME_TOKEN", "")
    outcome = mh.read()
    assert outcome["state"] == "unreadable"
    assert capsys.readouterr().err.splitlines() == [
        "model-home: unreadable — the token mint was refused: "
        "BUREAU_MODEL_HOME_TOKEN is set and empty; the built-in defaults are in use"]
    assert fake.calls == []
    assert mh.status_clause(outcome) == (
        " · model-home: unreadable (the token mint was refused: BUREAU_MODEL_HOME_TOKEN "
        "is set and empty), running on the defaults in the code")


@pytest.mark.parametrize("status", [404, 401])
def test_a_refused_token_is_unreadable(monkeypatch, status):
    fake = FakeTransport(status, '{"message": "Bad credentials"}')
    monkeypatch.setattr(mh, "_http_get", fake)
    monkeypatch.setenv("BUREAU_MODEL_HOME_TOKEN", "ghs_fake")
    decision, note = chosen("engineer")
    assert_unreadable(note, decision, str(status))
    [(url, headers, timeout)] = fake.calls
    assert url == ("https://api.github.com/repos/dreadnought-foundry/bureau-pipeline"
                   "/actions/variables/BUREAU_MODEL_CHOICES")
    assert headers["Authorization"] == "Bearer ghs_fake"
    assert timeout == 10


def test_a_timeout_is_unreadable(monkeypatch):
    monkeypatch.setattr(mh, "_http_get", FakeTransport(raises=TimeoutError("timed out")))
    monkeypatch.setenv("BUREAU_MODEL_HOME_TOKEN", "ghs_fake")
    decision, note = chosen("engineer")
    assert_unreadable(note, decision, "10 seconds")


def test_a_missing_file_is_unreadable(tmp_path, monkeypatch):
    monkeypatch.setenv("BUREAU_MODEL_HOME_FILE", str(tmp_path / "nowhere.json"))
    decision, note = chosen("engineer")
    assert_unreadable(note, decision, "does not exist")


def test_a_home_read_by_the_api_is_in_force(monkeypatch):
    doc = scenario_doc()
    body = json.dumps({"name": "BUREAU_MODEL_CHOICES", "value": json.dumps(doc)})
    monkeypatch.setattr(mh, "_http_get", FakeTransport(200, body))
    monkeypatch.setenv("BUREAU_MODEL_HOME_TOKEN", "ghs_fake")
    decision, note = chosen("engineer")
    assert decision["model"] == doc["groups"]["workhorse"]["models"][0]
    assert note.endswith(READ_CLAUSE)


def _break(rule: str):
    doc = defaults_doc()
    if rule == "not-json":
        return "{not json"
    if rule == "version":
        doc["version"] = 2
    elif rule == "no-workhorse":
        del doc["groups"]["workhorse"]
    elif rule == "empty-advisory":
        doc["groups"]["advisory"]["models"] = []
    elif rule == "no-judgement":
        del doc["groups"]["judgement"]
    elif rule == "critic-builds":
        doc["agents"]["critic"] = "workhorse"
    elif rule == "verifier-ungrouped":
        del doc["agents"]["verifier"]
    elif rule == "builder-plans":
        doc["agents"]["engineer"] = "judgement"
    return doc


WHOLE_DOCUMENT_RULES = {
    "not-json": "JSON",
    "version": "version",
    "no-workhorse": "workhorse",
    "empty-advisory": "advisory",
    "no-judgement": "judgement",
    "critic-builds": "critic",
    "verifier-ungrouped": "verifier",
    "builder-plans": "engineer",
}


@pytest.mark.parametrize("rule", list(WHOLE_DOCUMENT_RULES))
def test_a_document_breaking_a_whole_document_rule_is_unreadable(rule, tmp_path, monkeypatch):
    monkeypatch.setenv("BUREAU_MODEL_HOME_FILE", str(write(tmp_path / "h.json", _break(rule))))
    decision, note = chosen("engineer")
    assert_unreadable(note, decision, WHOLE_DOCUMENT_RULES[rule])


@pytest.mark.parametrize("rule", list(WHOLE_DOCUMENT_RULES))
def test_structure_errors_names_each_rule_in_a_sentence(rule):
    found = mh.structure_errors(_break(rule), mh.code_side())
    assert found, rule
    assert any(WHOLE_DOCUMENT_RULES[rule] in line for line in found), found
    for line in found:
        assert line.endswith("."), line


def test_the_defaults_break_no_rule():
    assert mh.structure_errors(defaults_doc(), mh.code_side()) == []
    assert mh.home_errors(defaults_doc(), mf.declared_prices(), mh.code_side(),
                          mf.declared_repo_slugs()) == []


def test_the_selector_cli_still_prints_a_model_on_an_unreadable_home(tmp_path):
    model, note, err = select_cli("engineer", tmp=tmp_path, BUREAU_MODEL_HOME_TOKEN="")
    assert model == defaults_top_for("engineer")
    assert note.startswith("DEGRADED model-policy:")
    assert UNREADABLE_CLAUSE.search(note), note
    tag = next(line for line in err if line.startswith("model-home: unreadable — "))
    assert tag.endswith("; running on the defaults in the code")
    assert err.index(tag) < err.index(note)


def test_the_read_line_precedes_the_note(tmp_path):
    doc = scenario_doc()
    home = write(tmp_path / "home.json", doc)
    _, note, err = select_cli("engineer", tmp=tmp_path, BUREAU_MODEL_HOME_FILE=home)
    tag = f"model-home: read — written {doc['written_at']} by {doc['written_by']}"
    assert tag in err
    assert err.index(tag) < err.index(note)


# --------------------------------------------------------------------------- #
# One safe line                                                                #
# --------------------------------------------------------------------------- #

HOSTILE = 'refused\nnext line "quoted" `tick` $(id) ' + "x" * 300


def test_one_line_collapses_whitespace():
    assert mh.one_line("a \n\t  b\r\nc") == "a b c"


def test_one_line_replaces_every_unlisted_character():
    assert mh.one_line("a\"b`c$d'e|f>g") == "a?b?c?d?e?f?g"
    assert mh.one_line("é·—") == "???"
    kept = "Ab9 . , : ; _ / ( ) = + @ -"
    assert mh.one_line(kept) == kept


def test_one_line_cuts_to_160():
    assert len(mh.one_line("y" * 500)) == 160
    assert mh.one_line("short") == "short"


def test_a_hostile_reason_makes_a_safe_note(monkeypatch, tmp_path):
    monkeypatch.setattr(mh, "_http_get", FakeTransport(raises=OSError(HOSTILE)))
    monkeypatch.setenv("BUREAU_MODEL_HOME_TOKEN", "ghs_fake")
    decision, note = chosen("engineer")
    reason = assert_unreadable(note, decision)
    for bad in ("\n", '"', "`", "$"):
        assert bad not in note
    assert len(reason) <= 160
    assert "$" not in mf.HOME_STATUS and "\n" not in mf.HOME_STATUS

    output = tmp_path / "github_output"
    output.write_text("")
    subprocess.run(["bash", "-c", 'echo "why=$WHY" >> "$GITHUB_OUTPUT"'],
                   env={**os.environ, "WHY": note, "GITHUB_OUTPUT": str(output)},
                   check=True, timeout=30)
    assert output.read_text().splitlines() == [f"why={note}"]


def test_a_hostile_written_by_never_reaches_the_tagged_line(tmp_path, capsys):
    doc = defaults_doc()
    doc["written_at"] = "2026\n$(id)"
    outcome = mh.read(path=str(write(tmp_path / "h.json", doc)))
    line = mh.status_line(outcome)
    assert "\n" not in line and "$" not in line


# --------------------------------------------------------------------------- #
# A home newer than the run's release                                          #
# --------------------------------------------------------------------------- #

def frozen_defaults() -> dict:
    """The defaults in the code of the older tree below: the frozen files'."""
    return mf._normalize_config(model_fixture.fixture_config())


def newer_doc(builders: list[str], base: dict | None = None) -> dict:
    doc = lead(defaults_doc(base), "advisory", "claude-opus-5-5")
    doc["groups"]["workhorse"]["models"] = list(builders)
    doc["agents"]["scout"] = "workhorse"
    doc["projects"]["portico"] = {"workhorse": builders[-1]}
    return doc


@pytest.fixture(scope="module")
def older_tree(tmp_path_factory) -> Path:
    """Today's scripts on the frozen files, in a release that prices no
    `claude-opus-5-6` and routes to no `portico`."""
    tree = model_fixture.fixture_tree(tmp_path_factory.mktemp("older-release"))
    repo_map = json.loads((tree / "config" / "repo-map.json").read_text())
    repo_map.pop("portico", None)
    (tree / "config" / "repo-map.json").write_text(json.dumps(repo_map))
    return tree


def test_a_home_newer_than_the_release_is_still_in_force(older_tree, tmp_path):
    builders = [NEWER_MODEL, "claude-sonnet-5-5"]
    doc = newer_doc(builders, frozen_defaults())
    home = write(tmp_path / "home.json", doc)
    selector = older_tree / "scripts" / "model_fallback.py"
    model, note, err = select_cli("engineer", tmp=tmp_path, selector=selector,
                                  BUREAU_MODEL_HOME_FILE=home)
    assert model == builders[1]
    assert note.startswith("model-policy:")
    assert note.endswith(READ_CLAUSE)
    tag = next(line for line in err if line.startswith("model-home: read — "))
    head, _, aside = tag.partition("; set aside as unknown to this release: ")
    assert aside, tag
    assert f"{NEWER_MODEL} (no price here)" in aside
    assert "agent scout (" in aside
    assert "project portico (not in repo-map)" in aside

    model, note, _ = select_cli("critic", tmp=tmp_path, selector=selector,
                                BUREAU_MODEL_HOME_FILE=home)
    assert model == doc["groups"]["advisory"]["models"][0]
    assert note.endswith(READ_CLAUSE)


def test_a_group_left_empty_by_setting_aside_is_unreadable(older_tree, tmp_path):
    home = write(tmp_path / "home.json", newer_doc([NEWER_MODEL], frozen_defaults()))
    selector = older_tree / "scripts" / "model_fallback.py"
    model, note, err = select_cli("engineer", tmp=tmp_path, selector=selector,
                                  BUREAU_MODEL_HOME_FILE=home)
    frozen = model_fixture.fixture_config()
    assert model == model_fixture.ladder(frozen, "workhorse")[0]
    assert note.startswith("DEGRADED model-policy:")
    assert UNREADABLE_CLAUSE.search(note), note
    assert any(line.startswith("model-home: unreadable — ") for line in err)


def test_home_errors_and_check_refuse_all_three_entries(older_tree, tmp_path):
    doc = newer_doc([NEWER_MODEL, "claude-sonnet-5-5"], frozen_defaults())
    slugs = set(mf.declared_repo_slugs()) - {"portico"}
    found = mh.home_errors(doc, mf.declared_prices(), mh.code_side(), slugs)
    assert len(found) == 3, found
    for word in (NEWER_MODEL, "scout", "portico"):
        assert sum(word in line for line in found) == 1, (word, found)
    for line in found:
        assert line.endswith("."), line

    home = write(tmp_path / "home.json", doc)
    proc = reader_cli("check", str(home), cwd=tmp_path,
                      reader=older_tree / "scripts" / "model_home.py")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    lines = proc.stdout.strip().splitlines()
    assert len(lines) == 3, lines
    for word in (NEWER_MODEL, "scout", "portico"):
        assert any(word in line for line in lines), (word, lines)


def test_unknown_entries_names_each_entry_with_its_reason():
    doc = newer_doc([NEWER_MODEL, "claude-sonnet-5-5"])
    doc["groups"]["judgement"]["models"].append("claude-fable-5")
    doc["projects"]["atlas"] = {"nobody": "claude-sonnet-5-5"}
    slugs = set(mf.declared_repo_slugs()) - {"portico"}
    entries = mh.unknown_entries(doc, mf.declared_prices(), mh.code_side(), slugs)
    shown = {(e["entry"], e["reason"]) for e in entries}
    assert (NEWER_MODEL, "no price here") in shown
    assert ("claude-fable-5", "excluded here") in shown
    assert ("agent scout", "not an agent here") in shown
    assert ("project portico", "not in repo-map") in shown
    assert ("project atlas nobody", "no such group") in shown
    for entry in entries:
        assert entry["sentence"].endswith("."), entry


def test_an_added_agent_is_known():
    doc = newer_doc([NEWER_MODEL, "claude-sonnet-5-5"])
    doc["added_agents"]["scout"] = "Looks around first"
    entries = mh.unknown_entries(doc, mf.declared_prices(), mh.code_side(),
                                 mf.declared_repo_slugs())
    assert not any("scout" in e["entry"] for e in entries)


# --------------------------------------------------------------------------- #
# Which processes read the home                                                #
# --------------------------------------------------------------------------- #

STUB = """
import json, sys, types
calls = []
stub = types.ModuleType("model_home")
def read(*args, **kwargs):
    calls.append(1)
    return {"state": "not set", "reason": None, "document": None, "set_aside": []}
stub.read = read
stub.status_clause = lambda outcome: ""
sys.modules["model_home"] = stub
sys.path.insert(0, %r)
import model_fallback as mf
""" % str(ROOT / "scripts")


def stub_run(code: str) -> int:
    proc = subprocess.run([sys.executable, "-c", STUB + code + "\nprint(len(calls))"],
                          capture_output=True, text=True, env=child_env(), timeout=60)
    assert proc.returncode == 0, proc.stderr
    return int(proc.stdout.strip().splitlines()[-1])


def test_importing_and_the_code_side_facts_read_nothing():
    assert stub_run(
        "mf.CAPACITY_SIGNATURES; mf.EFFORT_LEVELS; mf.KNOWN_MODELS\n"
        "mf.effort_for('x'); mf.capacity_refusal('monthly spend limit')\n"
        "mf.error_marker('x'); mf.policy_errors({})\n"
        "import contextlib, io\n"
        "with contextlib.redirect_stdout(io.StringIO()):\n"
        "    mf.main(['effort', 'x']); mf.main(['build-model', '/nonexistent'])\n"
        "    mf.main(['role-of', 'agent:planner'])\n"
    ) == 0


@pytest.mark.parametrize("call", [
    "mf.kind_for('engineer')",
    "mf.ladder_for('engineer')",
    "mf.fallback_for('engineer', 'x')",
    "mf.select_with_reasons('engineer', probe=lambda m: True)",
    "mf.select('engineer', probe=lambda m: True)",
])
def test_each_choice_function_reads_the_home_once(call):
    assert stub_run(call) == 1
    assert stub_run(f"{call}\n{call}\nmf.ladder_for('critic')") == 1


def test_the_two_scripts_declare_what_reads_the_home():
    assert mf.READS_MODEL_HOME == ("select",)
    assert mh.READS_MODEL_HOME == ("read",)
    assert mh.TAG == "model-home:"


def test_the_contract_names():
    assert mh.VARIABLE == "BUREAU_MODEL_CHOICES"
    assert mh.REPOSITORY == "dreadnought-foundry/bureau-pipeline"
    assert (mh.TOKEN_ENV, mh.FILE_ENV) == NAMES


# --------------------------------------------------------------------------- #
# The status line and clause                                                   #
# --------------------------------------------------------------------------- #

def test_the_three_outcomes_in_the_contract_s_words(tmp_path, capsys):
    outcome = mh.read()
    assert outcome["state"] == "not set"
    assert mh.status_line(outcome) == NOT_SET_LINE
    assert mh.status_clause(outcome) == ""
    assert capsys.readouterr().err.splitlines() == [NOT_SET_LINE]

    doc = defaults_doc()
    outcome = mh.read(path=str(write(tmp_path / "h.json", doc)))
    assert outcome["state"] == "read"
    assert outcome["document"] == doc
    assert mh.status_line(outcome) == (
        f"model-home: read — written {doc['written_at']} by {doc['written_by']}")
    assert mh.status_clause(outcome) == READ_CLAUSE

    outcome = mh.read(path=str(tmp_path / "nowhere.json"))
    assert outcome["state"] == "unreadable"
    assert outcome["document"] is None
    assert mh.status_line(outcome) == (
        f"model-home: unreadable — {outcome['reason']}; running on the defaults in the code")
    assert mh.status_clause(outcome) == (
        f" · model-home: unreadable ({outcome['reason']}), running on the defaults in the code")


def test_read_never_raises(monkeypatch):
    monkeypatch.setattr(mh, "_http_get", FakeTransport(raises=RuntimeError("boom")))
    outcome = mh.read(token="ghs_fake")
    assert outcome["state"] == "unreadable"


def test_a_document_too_large_is_unreadable(tmp_path):
    doc = defaults_doc()
    doc["added_agents"] = {f"a{i}": "z" * 100 for i in range(600)}
    outcome = mh.read(path=str(write(tmp_path / "h.json", doc)))
    assert outcome["state"] == "unreadable"
    assert "48 KB" in outcome["reason"]


# --------------------------------------------------------------------------- #
# The CLI                                                                      #
# --------------------------------------------------------------------------- #

def test_read_out_writes_the_document_it_will_use(tmp_path):
    doc = newer_doc([NEWER_MODEL, "claude-sonnet-5-5"])
    home = write(tmp_path / "home.json", doc)
    out = tmp_path / "out.json"
    proc = reader_cli("read", "--out", str(out), cwd=tmp_path, BUREAU_MODEL_HOME_FILE=home)
    assert proc.returncode == 0, proc.stderr
    assert proc.stderr.startswith("model-home: read — ")
    used = json.loads(out.read_text())
    assert used["groups"]["workhorse"]["models"] == doc["groups"]["workhorse"]["models"][1:]
    assert "scout" not in used["agents"]
    assert NEWER_MODEL not in json.dumps(used)
    # portico IS a slug of this checkout's repo map, so its choice stays.
    assert used["projects"]["portico"] == doc["projects"]["portico"]


@pytest.mark.parametrize("names", [{}, {"BUREAU_MODEL_HOME_TOKEN": ""},
                                   {"BUREAU_MODEL_HOME_FILE": "/nonexistent/home.json"}])
def test_read_out_writes_nothing_unless_read(tmp_path, names):
    out = tmp_path / "out.json"
    proc = reader_cli("read", "--out", str(out), cwd=tmp_path, **names)
    assert proc.returncode == 0, proc.stderr
    assert not out.exists()
    assert proc.stderr.startswith("model-home: ")


def test_check_refuses_a_broken_document_one_sentence_per_rule(tmp_path):
    doc = defaults_doc()
    doc["version"] = 2
    doc["agents"]["critic"] = "workhorse"
    proc = reader_cli("check", str(write(tmp_path / "h.json", doc)), cwd=tmp_path)
    assert proc.returncode == 1
    lines = proc.stdout.strip().splitlines()
    assert len(lines) == 2, lines
    assert any("version" in line for line in lines)
    assert any("critic" in line for line in lines)


def test_render_defaults_is_a_document_check_accepts(tmp_path):
    proc = reader_cli("render-defaults", cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    doc = json.loads(proc.stdout)
    assert doc["written_by"] == "operator"
    assert doc["version"] == 1
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", doc["written_at"])
    defaults = mf.code_defaults()
    assert doc["groups"]["workhorse"]["models"] == defaults["ladders"]["workhorse"]
    assert doc["agents"] == defaults["agents"]
    proc = reader_cli("check", str(write(tmp_path / "d.json", doc)), cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert mh.render_defaults()["written_by"] == "operator"


def test_render_defaults_carries_no_person():
    doc = mh.render_defaults()
    assert set(doc) == {"version", "written_at", "written_by", "groups", "agents",
                        "added_agents", "projects"}
    assert doc["written_by"] in mh.WRITERS
