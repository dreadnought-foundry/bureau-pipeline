"""DRE-7195 — every lever `docs/model-choice-levers.md` names, pulled once.

The doc says where a model choice takes effect and which one step back each
sibling epic of DRE-7184 pulls when it switches a job off Opus. This file
pulls each of those levers against the REAL selector and a temporary copy of
today's scripts and config, and checks the selection note moves the way the
doc says. It pulls two levers (DRE-8024):

  * the home — a project exception or an agent's group written in a home file
    (`BUREAU_MODEL_HOME_FILE`), and rolled back by writing the earlier
    document, `python3 scripts/model_home.py render-defaults`, back;
  * the `model-attempt:` line — a reviewer's build posting it, and then not.

It also holds the regime the doc found in force on 2026-10-12, the home named
and unreadable, the run on the defaults in the code; and each row of the doc's
sibling table, as a before/after pair on its roles. A row naming a lever this
file does not exercise fails here.

Every model id and slug a check expects is read off the copy the test builds —
its defaults in the code written as a home document, its repo map — never
typed, and no committed model file is compared with a literal, so a save from
the console turns nothing here red (DRE-6853). Each selection runs the selector's CLI the way the workflows do,
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import model_fallback as mf  # noqa: E402 — the marker's and session agents' one definition

DOC = ROOT / "docs" / "model-choice-levers.md"

HOME_NAMES = ("BUREAU_MODEL_HOME_TOKEN", "BUREAU_MODEL_HOME_FILE")

# The config files a tree carries beside its scripts. `repo-map.json` is the
# routing fact a project slug is read from.
CONFIG_FILES = ("models.yaml", "model-prices.yaml", "repo-map.json")

SIBLINGS = ("DRE-7185", "DRE-7187", "DRE-7188", "DRE-7189")

# The heading the sibling table sits under, and the three sources the doc
# names with the function each is read through.
TABLE_HEADING = "## The sibling epics' one step back"
SOURCE_FUNCTIONS = ("select_with_reasons", "use_home", "code_defaults")


# --------------------------------------------------------------------------- #
# A temporary copy of the real config, and the selector run inside it          #
# --------------------------------------------------------------------------- #

def build_tree(dest: Path) -> Path:
    """Today's scripts, the config files and `agents.yaml` at `dest`, as
    committed. Nothing is regenerated: every choice a test moves, it moves
    in a home document."""
    dest = Path(dest)
    (dest / "scripts").mkdir(parents=True, exist_ok=True)
    (dest / "config").mkdir(parents=True, exist_ok=True)
    for path in sorted((ROOT / "scripts").glob("*.py")):
        shutil.copy2(path, dest / "scripts" / path.name)
    for name in CONFIG_FILES:
        shutil.copy2(ROOT / "config" / name, dest / "config" / name)
    shutil.copy2(ROOT / "agents.yaml", dest / "agents.yaml")
    return dest


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

def ladder(doc: dict, group: str) -> list[str]:
    """The model ids `group` walks in a home document."""
    return doc["groups"][group]["models"]


def free_slug(tree: Path, doc: dict) -> str:
    """The first project slug of the copy's repo map with no exception in
    the home document `doc`."""
    projects = doc.get("projects") or {}
    repo_map = json.loads((tree / "config" / "repo-map.json").read_text())
    return next(slug for slug in repo_map if slug not in projects)


def builder(doc: dict) -> str:
    """The first pipeline agent a home document places in the Builders — the
    role a Builders exception moves. Read off the document, so a save that
    moves an agent out of the Builders moves this choice with it."""
    return next(name for name, group in doc["agents"].items()
                if group == "workhorse" and name not in mf.SESSION_AGENTS)


BUILDER = builder(render_defaults(ROOT))


def exception_model(doc: dict) -> str:
    """A model the document runs that is not its Builders' first rung — what
    a project exception for the Builders puts first."""
    top = ladder(doc, "workhorse")[0]
    return next(m for m in [*ladder(doc, "advisory"), *ladder(doc, "workhorse")]
                if m != top)


@pytest.fixture(scope="module")
def base(tmp_path_factory) -> Path:
    """The copy as committed."""
    return build_tree(tmp_path_factory.mktemp("base"))


# --------------------------------------------------------------------------- #
# The regime the doc read: the home named and unreadable                       #
# --------------------------------------------------------------------------- #

def test_today_s_regime_an_unreadable_home_runs_on_the_defaults_in_the_code(base):
    """The token step refused, an empty token handed over: the note opens
    DEGRADED, ends with the unreadable clause, and the run is on the first
    Builders model of the defaults in the code."""
    chosen, note = select(base, BUILDER, **LIVE)
    assert chosen == ladder(render_defaults(base), "workhorse")[0]
    assert note.startswith("DEGRADED ")
    assert note.endswith("· model-home: unreadable (the token mint was refused: "
                         "BUREAU_MODEL_HOME_TOKEN is set and empty), running on the "
                         "defaults in the code")


# --------------------------------------------------------------------------- #
# The home: BUREAU_MODEL_CHOICES, or a local copy of it                        #
# --------------------------------------------------------------------------- #

def test_an_exception_in_the_home_wins_over_a_literal_that_lacks_it(base):
    doc = render_defaults(base)
    slug, model = free_slug(base, doc), exception_model(doc)
    doc["projects"].setdefault(slug, {})["workhorse"] = model
    home = write_home(base, doc)

    chosen, note = select(base, BUILDER, "--repo", slug, BUREAU_MODEL_HOME_FILE=home)
    assert chosen == model
    assert f"project {slug}: exception {model} leads the" in note
    assert note.endswith(" · model-home: read")


def test_render_defaults_rolls_the_home_back_to_the_literal(base):
    doc = render_defaults(base)
    slug, model = free_slug(base, doc), exception_model(doc)
    doc["projects"].setdefault(slug, {})["workhorse"] = model
    home = write_home(base, doc)
    assert select(base, BUILDER, "--repo", slug, BUREAU_MODEL_HOME_FILE=home)[0] == model

    home = write_home(base, render_defaults(base))
    chosen, note = select(base, BUILDER, "--repo", slug, BUREAU_MODEL_HOME_FILE=home)
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
    built = ladder(render_defaults(tree), "workhorse")[0]
    line = f"🧠 {mf.attempt_marker(built)} — engineer agent starting"
    on = review(tree, role, ["⏳ 1/5 plan", line], **LIVE)[1]
    off = review(tree, role, ["⏳ 1/5 plan"], **LIVE)[1]
    return on, off


# Every lever the doc's table may name, and how this file pulls it.
EXERCISED: dict[str, Callable[[Path, str, str, str], tuple[str, str]]] = {
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


def test_a_row_naming_the_defaults_in_the_code_is_caught():
    """The defaults in the code are hand-written once DRE-6981 lands, so they
    are no lever, and a row naming them names a step back nobody can take."""
    assert set(EXERCISED) == {"the home", "model-attempt line"}
    text = (f"{TABLE_HEADING}\n\n| Epic | Lever |\n| -- | -- |\n"
            "| DRE-1 | `defaults in the code` |\n| DRE-2 | `the home` |\n")
    assert unexercised(table_rows(text)) == ["DRE-1: defaults in the code"]


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
    for source in ("BUREAU_MODEL_CHOICES", "config/models.yaml"):
        assert source in text


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
