"""The release train puts the Node a surface declares on its runner (DRE-8463).

Agent-bureau's console release synthesizes a CDK stack from `infra/`, whose
`package.json` says `"node": ">=24 <25"` under `engine-strict=true`. The
train's `release` job had no Node step at all, so `npm ci --prefix infra`
would exit `EBADENGINE` on whatever Node the runner shipped.

The contract this file pins, and nothing more:

* `release.json`'s surface schema gains an optional `node_dir`: a directory
  in the caller, relative to its root, whose `.nvmrc` names the Node major the
  surface's script needs. Omitted, nothing changes. A value that is not a
  non-empty string, is absolute, climbs out with `..`, or (when the caller is
  read) has no `.nvmrc` under it is refused, naming `<surface>.node_dir`.
* `release_train.py node-dir --surface <name>` prints exactly one line,
  `node_dir=<value>`, empty when the surface declares none.
* The `release` job reads it (`Read the surface's node`) and, when declared,
  sets that Node up through the in-tree shared node action with no install
  (`Set up the surface's node`), after the pipeline checkout and before the
  identity is assumed. The step never stops the train (DRE-3263), and the
  version is never written in this repo — it is the caller's `.nvmrc`.

What it cannot show: Node 24 on a real release runner. That exists only once
this workflow is on `stable` and a caller declares the field (DRE-8464).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import release_train  # noqa: E402

WORKFLOW = ROOT / ".github" / "workflows" / "release-train.yml"
OWN_DATA = ROOT / ".github" / "bureau" / "release.json"
DOC = ROOT / "docs" / "release-train.md"

READ = "Read the surface's node"
SETUP = "Set up the surface's node"
NODE_ACTION = "./.bureau-pipeline/.github/actions/setup-node-cached"


def _surface_data(**over):
    """A well-formed surface, with overrides."""
    data = {
        "tag_series": ["console/v*"],
        "paths": ["console/"],
        "script": "infra/release-console.sh",
        "rollback": "make rollback-console VERSION=<tag>",
        "spacing_minutes": 30,
        "auto": True,
        "identity": "bureau-console-release",
        "record": "tag",
    }
    data.update(over)
    return data


def _caller(tmp_path, nvmrc=True):
    """A caller checkout carrying the console script and, by default,
    `infra/.nvmrc`."""
    (tmp_path / "infra").mkdir()
    (tmp_path / "infra" / "release-console.sh").write_text("#!/usr/bin/env bash\n")
    if nvmrc:
        (tmp_path / "infra" / ".nvmrc").write_text("24\n")
    return tmp_path


# --------------------------------------------------------------------------
# The field.
# --------------------------------------------------------------------------

def test_a_surface_declaring_node_dir_with_its_nvmrc_is_well_formed(tmp_path):
    root = _caller(tmp_path)
    data = {"surfaces": {"console": _surface_data(node_dir="infra")}}
    assert release_train.check_schema(data, repo_root=root) == []
    assert release_train.check_schema(data) == []


@pytest.mark.parametrize("value", ["/infra", "../infra", "infra/../..", "", 7,
                                   None, ["infra"], "infra\nsetup=true"])
def test_a_malformed_node_dir_is_refused_by_name(tmp_path, value):
    root = _caller(tmp_path)
    data = {"surfaces": {"console": _surface_data(node_dir=value)}}
    problems = release_train.check_schema(data, repo_root=root)
    assert problems, f"node_dir={value!r} must be refused"
    assert all(p.startswith("console.node_dir:") for p in problems), problems
    # Refused for its value, never because the field is unknown.
    assert not any("is not a field" in p for p in problems), problems


def test_a_node_dir_with_no_nvmrc_under_it_is_refused_naming_the_path(tmp_path):
    root = _caller(tmp_path, nvmrc=False)
    data = {"surfaces": {"console": _surface_data(node_dir="infra")}}
    problems = release_train.check_schema(data, repo_root=root)
    assert len(problems) == 1, problems
    assert problems[0].startswith("console.node_dir:"), problems
    assert "infra/.nvmrc" in problems[0], problems
    # Without the caller to read, only the shape is judged.
    assert release_train.check_schema(data) == []


def test_a_surface_without_node_dir_still_passes(tmp_path):
    root = _caller(tmp_path, nvmrc=False)
    data = {"surfaces": {"console": _surface_data()}}
    assert release_train.check_schema(data, repo_root=root) == []
    assert release_train.surface("console", data["surfaces"]["console"]).node_dir == ""


def test_this_repos_own_release_json_still_passes():
    data = json.loads(OWN_DATA.read_text())
    assert release_train.check_schema(data, repo_root=ROOT) == []


def test_the_surface_carries_the_value():
    entry = release_train.surface("console", _surface_data(node_dir="infra"))
    assert entry.node_dir == "infra"


def test_node_dir_is_an_optional_field_of_the_schema():
    (field,) = [f for f in release_train.SCHEMA if f.name == "node_dir"]
    assert field.optional
    assert "node_dir" not in release_train.REQUIRED_FIELDS


# --------------------------------------------------------------------------
# The read.
# --------------------------------------------------------------------------

def _release_json(root, surfaces):
    path = root / ".github" / "bureau" / "release.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"surfaces": surfaces}))
    return path


def _node_dir(root, path, name, capsys):
    code = release_train.main([
        "--repo", "dreadnought-foundry/demo", "--repo-root", str(root),
        "--file", str(path), "node-dir", "--surface", name,
    ])
    return code, capsys.readouterr()


def test_node_dir_prints_the_declared_directory(tmp_path, capsys):
    root = _caller(tmp_path)
    path = _release_json(root, {"console": _surface_data(node_dir="infra")})
    code, out = _node_dir(root, path, "console", capsys)
    assert code == 0
    assert out.out == "node_dir=infra\n"


def test_node_dir_prints_an_empty_value_for_a_surface_that_declares_none(
        tmp_path, capsys):
    root = _caller(tmp_path)
    path = _release_json(root, {"console": _surface_data(node_dir="infra"),
                                "docs": _surface_data(script=None, auto=False)})
    code, out = _node_dir(root, path, "docs", capsys)
    assert code == 0
    assert out.out == "node_dir=\n"


def test_node_dir_for_an_undeclared_surface_prints_empty_and_says_why(
        tmp_path, capsys):
    root = _caller(tmp_path)
    path = _release_json(root, {"console": _surface_data(node_dir="infra")})
    code, out = _node_dir(root, path, "nope", capsys)
    assert code == 1
    assert out.out == "node_dir=\n"
    assert "nope" in out.err


def test_node_dir_never_prints_a_malformed_value(tmp_path, capsys):
    """The line goes into `$GITHUB_OUTPUT`: a value the schema refuses —
    a newline above all, which would be a second output line of the
    caller's choosing — is never printed."""
    root = _caller(tmp_path)
    path = _release_json(root, {"console": _surface_data(
        node_dir="infra\nsetup=true")})
    code, out = _node_dir(root, path, "console", capsys)
    assert code == 1
    assert out.out == "node_dir=\n"
    assert "console.node_dir" in out.err


# --------------------------------------------------------------------------
# The steps.
# --------------------------------------------------------------------------

def _steps():
    return yaml.safe_load(WORKFLOW.read_text())["jobs"]["release"]["steps"]


def _label(step):
    if "name" in step:
        return step["name"]
    if str(step.get("uses", "")).startswith("actions/checkout@"):
        repo = (step.get("with") or {}).get("repository")
        return f"checkout: {repo or 'the caller'}"
    return str(step.get("uses"))


def _step(name):
    (step,) = [s for s in _steps() if s.get("name") == name]
    return step


def test_the_node_steps_sit_after_the_pipeline_checkout_and_before_the_surface():
    labels = [_label(s) for s in _steps()]
    checkout = labels.index("checkout: dreadnought-foundry/bureau-pipeline")
    read, setup = labels.index(READ), labels.index(SETUP)
    assume = labels.index("Assume the caller's release identity")
    run = labels.index("Run the surface")
    assert checkout < read < setup < assume < run, labels
    assert setup == read + 1, labels


def test_the_setup_uses_the_in_tree_shared_node_action():
    assert _step(SETUP)["uses"] == NODE_ACTION


def test_the_setup_installs_nothing():
    assert _step(SETUP)["with"]["install"] == "false"


def test_a_failed_node_step_never_stops_the_train():
    assert _step(SETUP)["continue-on-error"] is True
    assert _step(READ)["continue-on-error"] is True


def test_the_setup_runs_only_for_a_declared_node_dir_the_read_resolved():
    when = _step(SETUP)["if"]
    assert "steps.node_decl.outputs.node_dir != ''" in when
    assert "steps.node_decl.outputs.setup == 'true'" in when


def test_the_setup_takes_the_directory_and_version_from_the_read():
    given = _step(SETUP)["with"]
    assert given["working-directory"] == "${{ steps.node_decl.outputs.node_dir }}"
    assert given["node-version"] == "${{ steps.node_decl.outputs.node-version }}"
    assert given["node-version-file"] == (
        "${{ steps.node_decl.outputs.node-version-file }}")


def test_no_node_version_in_the_job_is_a_literal_number():
    for step in _steps():
        for key, value in (step.get("with") or {}).items():
            if key.startswith("node-version"):
                assert not re.match(r"^\s*v?\d", str(value)), (step, key)


def test_the_read_step_keeps_every_caller_value_out_of_the_shell_line():
    step = _step(READ)
    assert step["id"] == "node_decl"
    assert "${{" not in step["run"], step["run"]
    assert step["env"]["SURFACE"] == "${{ matrix.surface }}"
    assert "node-dir --surface \"$SURFACE\"" in step["run"]
    assert "consumer_node.py \"$NODE_DIR\"" in step["run"]


# --------------------------------------------------------------------------
# The read step's shell, run for real against a caller checkout.
# --------------------------------------------------------------------------

def _run_read_step(tmp_path, surface_name):
    caller = tmp_path / "caller"
    caller.mkdir()
    _caller(caller)
    _release_json(caller, {"console": _surface_data(node_dir="infra"),
                           "docs": _surface_data(script=None, auto=False)})
    (caller / ".bureau-pipeline").symlink_to(ROOT)
    output = tmp_path / "github_output"
    output.write_text("")
    temp = tmp_path / "runner_temp"
    temp.mkdir()
    env = {**os.environ, "GITHUB_OUTPUT": str(output), "RUNNER_TEMP": str(temp),
           "GITHUB_REPOSITORY": "dreadnought-foundry/demo",
           **{k: str(v) for k, v in _step(READ)["env"].items()
              if not str(v).startswith("${{")},
           "SURFACE": surface_name}
    proc = subprocess.run(["bash", "-e", "-c", _step(READ)["run"]], cwd=caller,
                          env=env, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    lines = [l for l in output.read_text().splitlines() if l]
    return dict(l.split("=", 1) for l in lines), lines


def test_the_read_step_hands_the_setup_the_callers_nvmrc(tmp_path):
    outputs, lines = _run_read_step(tmp_path, "console")
    assert outputs["node_dir"] == "infra"
    assert outputs["setup"] == "true"
    assert outputs["node-version-file"] == "infra/.nvmrc"
    assert outputs["node-version"] == ""
    assert {l.split("=", 1)[0] for l in lines} <= {
        "node_dir", "setup", "version", "source", "node-version",
        "node-version-file"}


def test_the_read_step_turns_nothing_on_for_a_surface_that_declares_none(tmp_path):
    outputs, lines = _run_read_step(tmp_path, "docs")
    assert lines == ["node_dir="]


# --------------------------------------------------------------------------
# The document.
# --------------------------------------------------------------------------

def test_the_document_is_the_render_and_names_node_dir():
    assert DOC.read_text() == release_train.render_markdown()
    assert "`node_dir`" in DOC.read_text()
