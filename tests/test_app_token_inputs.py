"""No step hands `actions/create-github-app-token` an input its pin lacks (DRE-7230).

v3.2.0 declares no input for repository variables, yet 25 model-home mints
asked for `permission-variables`. The action logged that the input is not
valid, still forwarded it as `variables: read` (or `write`), and GitHub
refused the mint: every selecting run read the home as unreadable and ran on
the defaults in the code. A warning in a step marked `continue-on-error` is
easy to miss, so this test reads every step's `with:` keys against the inputs
its pinned release declares.

It runs offline. The declared inputs are a copy kept in the repo,
`tests/fixtures/create-github-app-token-inputs.json`, keyed by the pinned sha
and listing the input names exactly as that release's `action.yml` does. A
workflow that pins a sha the copy has no entry for fails, so a Dependabot bump
refreshes the copy in the same pull request.
"""

import json
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "create-github-app-token-inputs.json"
ACTION = "actions/create-github-app-token"

# The one undeclared input allowed, as (workflow, step name, input): reason.
EXCEPTIONS = {
    ("proof-task.yml", "Mint the read-only proof identity", "permission-variables"): (
        "the read-only proof identity keeps its declared read-only subset — "
        "dropping it would give the proof run write access — and moving its "
        "variables reading off this input changes config/proof-reach.json and "
        "scripts/proof_reach.py, a separate card"
    ),
}


def recorded():
    return json.loads(FIXTURE.read_text())


def _files(root):
    """Every workflow, and every composite action's `action.yml`."""
    root = Path(root)
    yield from sorted((root / ".github" / "workflows").glob("*.yml"))
    yield from sorted((root / ".github" / "actions").glob("*/action.y*ml"))


def _steps(doc):
    for job in (doc.get("jobs") or {}).values():
        yield from (job or {}).get("steps") or []
    yield from ((doc.get("runs") or {}).get("steps") or [])


def mints(root):
    """[(file name, step, pinned sha)] for every step using the action."""
    found = []
    for path in _files(root):
        doc = yaml.safe_load(path.read_text())
        if not isinstance(doc, dict):
            continue
        for step in _steps(doc):
            uses = step.get("uses") if isinstance(step, dict) else None
            if isinstance(uses, str) and uses.split("@", 1)[0] == ACTION:
                found.append((path.name, step, uses.split("@", 1)[1].split()[0]))
    return found


def check(root, pins):
    """(unrecorded pins, undeclared inputs) for the tree under `root`:
    [(file, step name, sha)] and [(file, step name, input)]."""
    unrecorded, undeclared = [], []
    for name, step, sha in mints(root):
        if sha not in pins:
            unrecorded.append((name, step.get("name"), sha))
            continue
        declared = set(pins[sha]["inputs"])
        for key in (step.get("with") or {}):
            if key not in declared:
                undeclared.append((name, step.get("name"), key))
    return unrecorded, undeclared


# --------------------------------------------------------------------------- #
# The recorded copy                                                            #
# --------------------------------------------------------------------------- #

def test_the_copy_records_each_pin_with_its_version_and_source():
    data = recorded()
    assert data["action"] == ACTION
    assert data["pins"]
    for sha, pin in data["pins"].items():
        assert re.fullmatch(r"[0-9a-f]{40}", sha), sha
        assert re.fullmatch(r"v\d+\.\d+\.\d+", pin["version"]), pin
        assert pin["action_yml"] == (
            f"https://github.com/{ACTION}/blob/{sha}/action.yml")
        inputs = pin["inputs"]
        assert len(inputs) == len(set(inputs)), sha
        assert {"private-key", "owner", "repositories"} <= set(inputs)


def test_v3_2_0_declares_no_variables_input():
    """Why the model-home mints ask for no permission subset at all."""
    inputs = recorded()["pins"]["bcd2ba49218906704ab6c1aa796996da409d3eb1"]["inputs"]
    assert "permission-contents" in inputs
    assert not [i for i in inputs if "variable" in i]


def test_each_pin_comment_names_the_recorded_version():
    pins = recorded()["pins"]
    pattern = re.compile(re.escape(ACTION) + r"@([0-9a-f]{40})\s*#\s*(\S+)")
    seen = 0
    for path in _files(ROOT):
        for match in pattern.finditer(path.read_text()):
            sha, version = match.groups()
            if sha in pins:
                seen += 1
                assert version == pins[sha]["version"], (path.name, sha, version)
    assert seen


# --------------------------------------------------------------------------- #
# The live tree                                                                #
# --------------------------------------------------------------------------- #

def test_the_live_tree_mints_with_the_action():
    assert len(mints(ROOT)) > 25


def test_every_pinned_sha_is_recorded():
    unrecorded, _ = check(ROOT, recorded()["pins"])
    assert unrecorded == [], (
        f"pinned shas with no entry in {FIXTURE.relative_to(ROOT)} — copy the "
        f"release's action.yml inputs into it in the same pull request: {unrecorded}")


def test_no_step_passes_an_undeclared_input():
    _, undeclared = check(ROOT, recorded()["pins"])
    unexpected = [u for u in undeclared if u not in EXCEPTIONS]
    assert unexpected == [], (
        f"inputs the pinned {ACTION} does not declare — it warns, forwards any "
        f"permission-* input anyway, and GitHub refuses a permission the "
        f"installation does not grant: {unexpected}")


@pytest.mark.parametrize("exception", sorted(EXCEPTIONS))
def test_each_exception_is_still_needed(exception):
    """An exception the tree no longer needs is removed, not left to rot."""
    _, undeclared = check(ROOT, recorded()["pins"])
    assert exception in undeclared, (
        f"{exception} no longer passes an undeclared input — delete it from "
        f"EXCEPTIONS")


# --------------------------------------------------------------------------- #
# A synthetic tree                                                             #
# --------------------------------------------------------------------------- #

SHA = "a" * 40
PINS = {SHA: {"version": "v9.9.9", "inputs": ["app-id", "private-key", "owner",
                                                "repositories", "permission-contents"]}}


def _tree(tmp_path, *withs, sha=SHA):
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    steps = [{"name": f"Mint {i}", "uses": f"{ACTION}@{sha}", "with": w}
             for i, w in enumerate(withs)]
    doc = {"on": "push", "jobs": {"j": {"runs-on": "ubuntu-latest", "steps": steps}}}
    (wf / "w.yml").write_text(yaml.safe_dump(doc))
    return tmp_path


def test_a_declared_input_passes(tmp_path):
    root = _tree(tmp_path, {"app-id": "1", "owner": "o", "permission-contents": "read"})
    assert check(root, PINS) == ([], [])


def test_an_undeclared_input_fails(tmp_path):
    root = _tree(tmp_path, {"owner": "o", "permission-variables": "read"})
    assert check(root, PINS) == ([], [("w.yml", "Mint 0", "permission-variables")])


def test_an_unrecorded_pin_fails(tmp_path):
    other = "b" * 40
    root = _tree(tmp_path, {"owner": "o"}, sha=other)
    assert check(root, PINS) == ([("w.yml", "Mint 0", other)], [])


def test_a_composite_actions_mint_is_read(tmp_path):
    action = tmp_path / ".github" / "actions" / "mint"
    action.mkdir(parents=True)
    (action / "action.yml").write_text(yaml.safe_dump({"runs": {
        "using": "composite",
        "steps": [{"name": "Mint", "uses": f"{ACTION}@{SHA}",
                   "with": {"permission-secret-santa": "read"}}]}}))
    (tmp_path / ".github" / "workflows").mkdir()
    assert check(tmp_path, PINS) == ([], [("action.yml", "Mint", "permission-secret-santa")])
