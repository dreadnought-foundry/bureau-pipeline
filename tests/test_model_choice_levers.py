"""DRE-7195 — every lever `docs/model-choice-levers.md` names, pulled once.

The doc says where a model choice takes effect today and which one step back
each sibling epic of DRE-7184 pulls when it switches a job off Opus. This file
pulls each of those levers against the REAL selector and a temporary copy of
the real config, and checks the selection note moves the way the doc says:

  * a project exception added through `config/models.yaml` plus a regenerated
    literal, with no home set, and rolled back by restoring the file;
  * the same exception in a home file (`BUREAU_MODEL_HOME_FILE`) winning over
    a literal that lacks it, and the home rolled back by
    `python3 scripts/model_home.py render-defaults`;
  * the regime the doc finds in force today — the home named and unreadable,
    the run on the defaults in the code;
  * `config/model-choices.json` read by nothing on the dispatch path;
  * each row of the doc's sibling table, as a before/after pair on its roles.
    A row naming a lever this file does not exercise fails here.

Every model id and slug a check expects is read off the copy the test builds —
its ladders, its repo map — never typed, and no committed model file is
compared with a literal, so a save from the console turns nothing here red
(DRE-6853). Each selection runs the selector's CLI the way the workflows do,
with every model up (`BUREAU_FAKE_AVAILABLE`) and no network call.

Run: cd bureau-pipeline && python3 -m pytest tests/test_model_choice_levers.py -q
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import model_fallback as mf  # noqa: E402 — the marker's one definition

DOC = ROOT / "docs" / "model-choice-levers.md"

HOME_NAMES = ("BUREAU_MODEL_HOME_TOKEN", "BUREAU_MODEL_HOME_FILE")

# The config files a tree carries beside its scripts. `model-choices.json` is
# one of the generator's three targets, so it is copied for the generator to
# rewrite, and `repo-map.json` is the routing fact a project slug is read from.
CONFIG_FILES = ("models.yaml", "model-prices.yaml", "repo-map.json", "model-choices.json")

SIBLINGS = ("DRE-7185", "DRE-7187", "DRE-7188", "DRE-7189")

# The heading the sibling table sits under, and the three sources the doc
# names with the function each is read through.
TABLE_HEADING = "## The sibling epics' one step back"
SOURCE_FUNCTIONS = ("select_with_reasons", "use_home", "code_defaults")


# --------------------------------------------------------------------------- #
# A temporary copy of the real config, and the selector run inside it          #
# --------------------------------------------------------------------------- #

def real_raw() -> dict:
    """The committed `config/models.yaml`, parsed — the copy's starting point."""
    return yaml.safe_load((ROOT / "config" / "models.yaml").read_text())


def ladder_ids(raw: dict, kind: str) -> list[str]:
    """The model ids `kind` walks in a raw config."""
    spec = raw["kinds"][kind]
    name = spec["ladder"] if isinstance(spec, dict) else spec
    return [r["model"] if isinstance(r, dict) else r for r in raw["ladders"][name]]


def build_tree(dest: Path, edit: Callable[[dict], None] | None = None) -> Path:
    """Today's scripts and a copy of the real config at `dest`, the copy's
    `models.yaml` edited by `edit` (left byte for byte as committed without
    one — what a revert restores), and the generated mirrors regenerated
    from it by the real generator."""
    dest = Path(dest)
    (dest / "scripts").mkdir(parents=True, exist_ok=True)
    (dest / "config").mkdir(parents=True, exist_ok=True)
    for path in sorted((ROOT / "scripts").glob("*.py")):
        shutil.copy2(path, dest / "scripts" / path.name)
    for name in CONFIG_FILES:
        shutil.copy2(ROOT / "config" / name, dest / "config" / name)
    shutil.copy2(ROOT / "agents.yaml", dest / "agents.yaml")
    if edit is not None:
        raw = yaml.safe_load((dest / "config" / "models.yaml").read_text())
        edit(raw)
        (dest / "config" / "models.yaml").write_text(yaml.safe_dump(raw, sort_keys=False))
    proc = subprocess.run(
        [sys.executable, str(dest / "scripts" / "sync_model_config.py")],
        cwd=dest, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return dest


def tree_raw(tree: Path) -> dict:
    return yaml.safe_load((tree / "config" / "models.yaml").read_text())


def child_env(**names: str) -> dict:
    """This environment with no home name unless given, and every model up."""
    env = {k: v for k, v in os.environ.items() if k not in HOME_NAMES}
    env["BUREAU_FAKE_AVAILABLE"] = "{}"
    env.update(names)
    return env


def select(tree: Path, role: str, *args: str, **names: str) -> tuple[str, str]:
    """`(model, note)` from the tree's selector CLI, the way a workflow's
    Select model step calls it."""
    note = tree / "note.txt"
    if note.exists():
        note.unlink()
    proc = subprocess.run(
        [sys.executable, str(tree / "scripts" / "model_fallback.py"), "select", role,
         *args, "--explain-file", str(note)],
        cwd=tree, capture_output=True, text=True, env=child_env(**names), timeout=120)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip(), note.read_text().rstrip("\n")


def build_model(tree: Path, bodies: list[str]) -> str:
    """What the tree's `build-model` reads off a card thread of `bodies` —
    empty when no attempt line is there, as the reviewer workflows read it."""
    thread = tree / "thread.json"
    thread.write_text(json.dumps(bodies))
    proc = subprocess.run(
        [sys.executable, str(tree / "scripts" / "model_fallback.py"), "build-model",
         str(thread)],
        cwd=tree, capture_output=True, text=True, env=child_env(), timeout=120)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def review(tree: Path, role: str, bodies: list[str], **names: str) -> tuple[str, str]:
    """A reviewer's selection as `qa-review.yml` makes it: `--built-on` the
    thread's build model, `--built-on-unknown` when it names none."""
    built_on = build_model(tree, bodies)
    flags = ["--built-on", built_on] if built_on else ["--built-on-unknown"]
    return select(tree, role, *flags, **names)


def render_defaults(tree: Path) -> dict:
    """`python3 scripts/model_home.py render-defaults` in the tree: its
    defaults in the code, as a home document."""
    proc = subprocess.run(
        [sys.executable, str(tree / "scripts" / "model_home.py"), "render-defaults"],
        cwd=tree, capture_output=True, text=True, env=child_env(), timeout=120)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def write_home(tree: Path, doc: dict) -> str:
    path = tree / "config" / "model-home.json"
    path.write_text(json.dumps(doc, indent=2) + "\n")
    return str(path)


# The live regime of the doc's reading: a token was asked for and the mint
# was refused, so the step hands the reader an empty one.
LIVE = {"BUREAU_MODEL_HOME_TOKEN": ""}


# --------------------------------------------------------------------------- #
# What the copy says                                                           #
# --------------------------------------------------------------------------- #

def free_slug(tree: Path) -> str:
    """The first project slug of the copy's repo map with no exception in the
    copy's `models.yaml`."""
    projects = tree_raw(tree).get("projects") or {}
    repo_map = json.loads((tree / "config" / "repo-map.json").read_text())
    return next(slug for slug in repo_map if slug not in projects)


def exception_model(raw: dict) -> str:
    """A model the copy prices and runs that is not its Builders' first rung —
    what a project exception for the Builders puts first."""
    top = ladder_ids(raw, "workhorse")[0]
    return next(m for m in [*ladder_ids(raw, "advisory"), *ladder_ids(raw, "workhorse")]
                if m != top)


def add_exception(slug: str, model: str) -> Callable[[dict], None]:
    def edit(raw: dict) -> None:
        raw.setdefault("projects", {})
        if raw["projects"] is None:
            raw["projects"] = {}
        raw["projects"].setdefault(slug, {})["workhorse"] = model
    return edit


@pytest.fixture(scope="module")
def base(tmp_path_factory) -> Path:
    """The copy as committed: what a revert of any of the edits below restores."""
    return build_tree(tmp_path_factory.mktemp("base"))


@pytest.fixture(scope="module")
def excepted(tmp_path_factory, base) -> tuple[Path, str, str]:
    """The copy with one project's Builders exception added, regenerated."""
    slug, model = free_slug(base), exception_model(tree_raw(base))
    tree = build_tree(tmp_path_factory.mktemp("excepted"), add_exception(slug, model))
    return tree, slug, model


# --------------------------------------------------------------------------- #
# The defaults in the code: config/models.yaml plus the regenerated literal    #
# --------------------------------------------------------------------------- #

def test_an_exception_written_in_models_yaml_takes_effect_through_the_literal(base, excepted):
    tree, slug, model = excepted
    top = ladder_ids(tree_raw(base), "workhorse")[0]

    before, before_note = select(base, "engineer", "--repo", slug)
    after, after_note = select(tree, "engineer", "--repo", slug)

    assert before == top
    assert f"project {slug}: no exception, fleet ladder" in before_note
    assert after == model
    assert f"project {slug}: exception {model} leads the" in after_note
    assert "model-home" not in after_note


def test_reverting_models_yaml_and_its_literal_rolls_the_exception_back(tmp_path, excepted):
    _, slug, model = excepted
    reverted = build_tree(tmp_path / "reverted")
    chosen, note = select(reverted, "engineer", "--repo", slug)
    assert chosen != model
    assert f"project {slug}: no exception, fleet ladder" in note


def test_the_literal_alone_carries_the_choice_and_models_yaml_choices_are_not_read(
        tmp_path, excepted):
    """The exception's literal under a `models.yaml` that lost it again: the
    selector still runs the exception, because it reads the choices from the
    literal and never from the file."""
    tree, slug, model = excepted
    stale = tmp_path / "stale"
    shutil.copytree(tree, stale)
    shutil.copy2(ROOT / "config" / "models.yaml", stale / "config" / "models.yaml")
    chosen, note = select(stale, "engineer", "--repo", slug)
    assert chosen == model
    assert f"exception {model} leads the" in note


def test_today_s_regime_an_unreadable_home_runs_on_the_literal(base, excepted):
    """The token step refused, an empty token handed over: the note opens
    DEGRADED, ends with the unreadable clause, and the literal's exception is
    in force."""
    tree, slug, model = excepted
    chosen, note = select(tree, "engineer", "--repo", slug, **LIVE)
    assert chosen == model
    assert note.startswith("DEGRADED ")
    assert note.endswith("· model-home: unreadable (BUREAU_MODEL_HOME_TOKEN is set and "
                         "empty, so the token mint failed), running on the defaults in "
                         "the code")
    plain, plain_note = select(base, "engineer", "--repo", slug, **LIVE)
    assert plain != model
    assert "no exception, fleet ladder · model-home: unreadable" in plain_note


def test_model_choices_json_is_not_read_on_the_dispatch_path(tmp_path, excepted):
    tree, slug, _ = excepted
    _, note = select(tree, "engineer", "--repo", slug)
    garbled = tmp_path / "garbled"
    shutil.copytree(tree, garbled)
    (garbled / "config" / "model-choices.json").write_text("not json {")
    _, garbled_note = select(garbled, "engineer", "--repo", slug)
    (garbled / "config" / "model-choices.json").unlink()
    _, missing_note = select(garbled, "engineer", "--repo", slug)
    assert garbled_note == note
    assert missing_note == note


# --------------------------------------------------------------------------- #
# The home: BUREAU_MODEL_CHOICES, or a local copy of it                        #
# --------------------------------------------------------------------------- #

def test_an_exception_in_the_home_wins_over_a_literal_that_lacks_it(base):
    slug, model = free_slug(base), exception_model(tree_raw(base))
    doc = render_defaults(base)
    doc["projects"].setdefault(slug, {})["workhorse"] = model
    home = write_home(base, doc)

    chosen, note = select(base, "engineer", "--repo", slug, BUREAU_MODEL_HOME_FILE=home)
    assert chosen == model
    assert f"project {slug}: exception {model} leads the" in note
    assert note.endswith(" · model-home: read")


def test_render_defaults_rolls_the_home_back_to_the_literal(base):
    slug, model = free_slug(base), exception_model(tree_raw(base))
    doc = render_defaults(base)
    doc["projects"].setdefault(slug, {})["workhorse"] = model
    home = write_home(base, doc)
    assert select(base, "engineer", "--repo", slug, BUREAU_MODEL_HOME_FILE=home)[0] == model

    home = write_home(base, render_defaults(base))
    chosen, note = select(base, "engineer", "--repo", slug, BUREAU_MODEL_HOME_FILE=home)
    assert chosen != model
    assert f"project {slug}: no exception, fleet ladder · model-home: read" in note


def test_the_home_wins_over_a_literal_that_carries_another_choice(excepted):
    """With the home in force, the literal's exception is not consulted: a
    home rendered without it runs the fleet ladder for that project."""
    tree, slug, model = excepted
    doc = render_defaults(tree)
    doc["projects"].pop(slug, None)
    home = write_home(tree, doc)
    chosen, note = select(tree, "engineer", "--repo", slug, BUREAU_MODEL_HOME_FILE=home)
    assert chosen != model
    assert f"project {slug}: no exception, fleet ladder · model-home: read" in note


# --------------------------------------------------------------------------- #
# The doc's sibling table                                                      #
# --------------------------------------------------------------------------- #

def table_rows(text: str) -> list[dict[str, str]]:
    """The rows of the table under `TABLE_HEADING`, each a map from the
    header's words to the row's cell."""
    section = text.split(TABLE_HEADING, 1)[1] if TABLE_HEADING in text else ""
    lines = [line.strip() for line in section.splitlines()]
    start = next((i for i, line in enumerate(lines) if line.startswith("|")), None)
    if start is None:
        return []
    block = []
    for line in lines[start:]:
        if not line.startswith("|"):
            break
        block.append([cell.strip() for cell in line.strip("|").split("|")])
    header, rows = block[0], block[2:]
    return [dict(zip(header, row)) for row in rows]


def ticked(cell: str) -> list[str]:
    return re.findall(r"`([^`]+)`", cell or "")


def lever_of(row: dict[str, str]) -> str:
    found = ticked(row.get("Lever", ""))
    return found[0] if found else ""


def _group_edit(role: str, group: str, lead: str) -> Callable[[dict], None]:
    """`role` in `group` in a raw config; a group the config lacks is added
    as a build group led by `lead`, the Builders' rungs after it."""
    def edit(raw: dict) -> None:
        if group not in raw["kinds"]:
            raw["kinds"][group] = {"ladder": group, "label": group.title(),
                                   "purpose": f"Run the {role} jobs."}
            rest = [m for m in ladder_ids(raw, "workhorse") if m != lead]
            raw["ladders"][group] = [lead, *rest]
        raw["agents"][role] = group
    return edit


def _defaults_pair(tmp: Path, role: str, switched: str, back: str) -> tuple[str, str]:
    """Before and after notes for an agent's group moved through
    `config/models.yaml` and its regenerated literal, in today's regime. The
    switched group, when the config lacks it, is led by the Reviewers' first
    model — the Sonnet 5.5 each sibling switches to, read off the copy."""
    lead = ladder_ids(real_raw(), "advisory")[0]
    on = build_tree(tmp / "switched", _group_edit(role, switched, lead))
    off = build_tree(tmp / "back", lambda raw: (_group_edit(role, switched, lead)(raw),
                                                _group_edit(role, back, lead)(raw)))
    return select(on, role, **LIVE)[1], select(off, role, **LIVE)[1]


def _home_pair(tmp: Path, role: str, switched: str, back: str) -> tuple[str, str]:
    """The same pair with the agent's group written in a home file."""
    tree = build_tree(tmp / "home")
    doc = render_defaults(tree)
    lead = doc["groups"]["advisory"]["models"][0]
    if switched not in doc["groups"]:
        rest = [m for m in doc["groups"]["workhorse"]["models"] if m != lead]
        doc["groups"][switched] = {"label": switched.title(), "purpose": f"Run the {role} jobs.",
                                   "models": [lead, *rest]}
    if role not in doc["agents"]:
        doc["added_agents"][role] = f"Runs the {role} jobs."
    doc["agents"][role] = switched
    on = select(tree, role, BUREAU_MODEL_HOME_FILE=write_home(tree, doc))[1]
    doc["agents"][role] = back
    off = select(tree, role, BUREAU_MODEL_HOME_FILE=write_home(tree, doc))[1]
    return on, off


def _line_pair(tmp: Path, role: str, switched: str, back: str) -> tuple[str, str]:
    """Before and after notes for a reviewer whose build posted its
    `model-attempt:` line, and then did not — the line is the lever."""
    tree = build_tree(tmp / "line")
    built = ladder_ids(tree_raw(tree), "workhorse")[0]
    line = f"🧠 {mf.attempt_marker(built)} — engineer agent starting"
    on = review(tree, role, ["⏳ 1/5 plan", line], **LIVE)[1]
    off = review(tree, role, ["⏳ 1/5 plan"], **LIVE)[1]
    return on, off


# Every lever the doc's table may name, and how this file pulls it.
EXERCISED: dict[str, Callable[[Path, str, str, str], tuple[str, str]]] = {
    "defaults in the code": _defaults_pair,
    "the home": _home_pair,
    "model-attempt line": _line_pair,
}


def unexercised(rows: list[dict[str, str]]) -> list[str]:
    """`<epic>: <lever>` for each row naming a lever `EXERCISED` lacks."""
    return [f"{row.get('Epic', '?')}: {lever_of(row) or '(none)'}" for row in rows
            if lever_of(row) not in EXERCISED]


def doc_text() -> str:
    assert DOC.exists(), f"{DOC.relative_to(ROOT)} is missing"
    return DOC.read_text(encoding="utf-8")


def test_the_doc_has_one_row_per_sibling_epic():
    rows = table_rows(doc_text())
    epics = [ticked(row.get("Epic", ""))[:1] or [row.get("Epic", "")] for row in rows]
    named = [re.search(r"DRE-\d+", e[0]).group(0) for e in epics if re.search(r"DRE-\d+", e[0])]
    assert sorted(named) == sorted(SIBLINGS)
    for row in rows:
        for column in ("Lever", "Role", "Switched → back", "One step back", "In force",
                       "Rolled back, the note shows"):
            assert row.get(column, "").strip(), f"{row.get('Epic')} has no {column!r}"


def test_every_lever_the_table_names_is_one_this_file_exercises():
    rows = table_rows(doc_text())
    assert rows
    assert unexercised(rows) == []


def test_a_row_naming_a_lever_nobody_pulls_is_caught():
    text = (f"{TABLE_HEADING}\n\n| Epic | Lever |\n| -- | -- |\n"
            "| DRE-1 | `an environment variable` |\n| DRE-2 | `the home` |\n")
    assert unexercised(table_rows(text)) == ["DRE-1: an environment variable"]


def _sibling_rows() -> list[dict[str, str]]:
    return table_rows(DOC.read_text(encoding="utf-8")) if DOC.exists() else []


@pytest.mark.parametrize(
    "row", _sibling_rows() or [{"Epic": "missing"}],
    ids=lambda row: (ticked(row.get("Epic", "")) or [row.get("Epic", "missing")])[0])
def test_each_sibling_step_back_moves_the_note_the_way_the_doc_says(row, tmp_path):
    lever = lever_of(row)
    assert lever in EXERCISED, f"{row.get('Epic')} names {lever!r}, which no test pulls"
    clause = ticked(row["Rolled back, the note shows"])[0]
    states = ticked(row["Switched → back"])
    switched, back = (states + ["", ""])[:2]
    roles = ticked(row["Role"])
    assert roles
    for role in roles:
        before, after = EXERCISED[lever](tmp_path / role, role, switched, back)
        assert clause not in before, before
        assert clause in after, after


# --------------------------------------------------------------------------- #
# The doc's other claims                                                       #
# --------------------------------------------------------------------------- #

def test_the_header_names_the_main_commit_and_the_pt_time_it_was_read_at():
    head = "\n".join(doc_text().splitlines()[:12])
    assert re.search(r"`main` at `[0-9a-f]{8,40}`", head), head
    assert re.search(r"\d{4}-\d{2}-\d{2} at \d{1,2}:\d{2} (AM|PM) PT", head), head


def test_the_three_sources_are_named_with_the_functions_that_read_them():
    text = doc_text()
    refs = re.findall(r"`(\w+)` \(`(scripts/\w+\.py):(\d+)`\)", text)
    named = {name for name, _, _ in refs}
    assert set(SOURCE_FUNCTIONS) <= named, named
    for name, path, _ in refs:
        assert re.search(rf"^def {name}\(", (ROOT / path).read_text(), re.M), (name, path)
    for source in ("BUREAU_MODEL_CHOICES", "_FALLBACK_MODEL_CONFIG", "config/models.yaml"):
        assert source in text


def test_the_doc_says_model_choices_json_is_off_the_dispatch_path():
    text = doc_text()
    assert re.search(r"`config/model-choices\.json`[^.]*never on the dispatch path", text)


def test_the_doc_quotes_a_model_home_line_from_each_selecting_workflow():
    text = doc_text()
    for workflow in ("QA Review", "Agent Task", "Agent Plan"):
        section = re.search(
            rf"\*\*{workflow}\*\*(.*?)(?=\n\*\*|\n## )", text, re.S)
        assert section, workflow
        body = section.group(1)
        assert re.search(r"https://github\.com/[\w.-]+/[\w.-]+/actions/runs/\d+", body), workflow
        assert "model-home: " in body, workflow
        assert re.search(r"\d{1,2}:\d{2}(:\d{2})? (AM|PM) PT", body), workflow
