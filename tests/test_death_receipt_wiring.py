"""Every workflow that runs a model writes a death-cause receipt (DRE-4340).

The durable half of the card is this guard, not the wiring. A list of the
workflows that run a model drifts the moment somebody adds the tenth, and the
cost of the drift is exactly what 2026-09-19's count measured: 69 failures out
of 1,310, $97 of model work, that could not be attributed to any cause at all
because the workflow they died in wrote nothing down.

So the set is DISCOVERED — from the workflow files themselves, at test time —
and nothing here names a workflow. `scripts/check_death_receipts.py` owns the
discovery and the rules; this exercises it two ways:

  * against the LIVE workflow files, so a workflow added without a receipt
    turns Pipeline Tests red rather than adding a silent blind spot;
  * against a MUTATED copy of them — one receipt step removed, one at a time,
    for every workflow the discovery found. That is the proof the guard bites,
    and it is a removal rather than an assertion because a guard that has
    never been seen to fail has never been shown to work.

Run: cd bureau-pipeline && python3 -m pytest tests/test_death_receipt_wiring.py -v
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
WORKFLOWS = ROOT / ".github" / "workflows"
sys.path.insert(0, str(SCRIPTS))

import check_death_receipts as guard  # noqa: E402


def _discovered():
    """(filename, job) for every model-running reusable workflow, live."""
    return [(mj.filename, mj.job) for mj in guard.model_jobs(WORKFLOWS)]


def _crudely_discovered():
    """The same population by a DIFFERENT, cruder route: a raw text scan.

    The structural walk in the guard reads jobs and steps; this reads the file
    as text. They agree or one of them is wrong — and neither is a list of
    names somebody has to remember to update.
    """
    found = set()
    for path in sorted(WORKFLOWS.glob("*.yml")):
        text = path.read_text()
        doc = yaml.safe_load(text)
        if not isinstance(doc, dict):
            continue
        if "workflow_call" not in guard.on_block(doc):
            continue
        hands_a_credential = any(
            f"{name}:" in text for name in guard.MODEL_CREDENTIALS)
        if guard.MODEL_ACTION in text or hands_a_credential:
            found.add(path.name)
    return found


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------

class TestDiscovery:
    def test_it_finds_model_running_reusable_workflows_and_is_not_vacuous(self):
        found = _discovered()
        assert found, "discovered no model-running workflow — the guard went vacuous"
        # More than one, or the guard proves nothing about a fleet.
        assert len({name for name, _ in found}) > 1

    def test_the_structural_walk_and_a_raw_text_scan_agree(self):
        walked = {name for name, _ in _discovered()}
        # The text scan is deliberately looser (a file that only DECLARES the
        # credential as a `secrets:` input matches it), so it is a superset.
        assert walked <= _crudely_discovered()
        # ...and every file carrying the vendor model action must be walked:
        # that one is unambiguous.
        for path in sorted(WORKFLOWS.glob("*.yml")):
            doc = yaml.safe_load(path.read_text())
            if not isinstance(doc, dict):
                continue
            if "workflow_call" not in guard.on_block(doc):
                continue
            if guard.MODEL_ACTION in path.read_text():
                assert path.name in walked, path.name

    def test_a_workflow_that_runs_no_model_is_not_in_the_population(self, tmp_path):
        (tmp_path / "quiet.yml").write_text(
            "name: Quiet\n"
            "on:\n  workflow_call:\n"
            "jobs:\n"
            "  tidy:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - run: echo no model here\n"
        )
        assert guard.model_jobs(tmp_path) == []

    def test_a_workflow_that_cannot_be_called_is_not_in_the_population(self, tmp_path):
        # A model step inside a workflow nothing calls is not fleet work; the
        # card's population is the REUSABLE workflows.
        (tmp_path / "standalone.yml").write_text(
            "name: Standalone\n"
            "on:\n  workflow_dispatch:\n"
            "jobs:\n"
            "  go:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            f"      - uses: {guard.MODEL_ACTION}@" + "a" * 40 + "\n"
        )
        assert guard.model_jobs(tmp_path) == []


# --------------------------------------------------------------------------
# The live files
# --------------------------------------------------------------------------

class TestLive:
    def test_every_model_running_workflow_writes_the_receipt(self):
        violations, stats = guard.check_dir(WORKFLOWS)
        assert violations == [], "\n".join(violations)
        assert stats["model_jobs"] == len(_discovered())

    def test_the_guard_refuses_an_empty_directory(self, tmp_path):
        # A checker pointed at the wrong place must say so, not pass.
        assert guard.main([str(tmp_path)]) == 1

    def test_the_cli_is_green_on_the_live_files(self):
        assert guard.main([str(WORKFLOWS)]) == 0

    def test_it_runs_in_ci(self):
        tests_yml = (WORKFLOWS / "tests.yml").read_text()
        assert "check_death_receipts.py" in tests_yml


# --------------------------------------------------------------------------
# Remove one receipt step and the guard must bite
# --------------------------------------------------------------------------

def _mutated_dir(tmp_path, filename, mutate):
    """A copy of the live workflows with one file's YAML rewritten."""
    workdir = tmp_path / "workflows"
    shutil.copytree(WORKFLOWS, workdir)
    path = workdir / filename
    doc = yaml.safe_load(path.read_text())
    mutate(doc)
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    return workdir


def _receipt_index(steps):
    return [i for i, s in enumerate(steps)
            if guard.RECEIPT_CALL in str(s.get("run") or "")]


@pytest.mark.parametrize("filename,job", _discovered())
def test_removing_the_receipt_step_is_caught(tmp_path, filename, job):
    def drop(doc):
        steps = doc["jobs"][job]["steps"]
        for i in reversed(_receipt_index(steps)):
            del steps[i]

    workdir = _mutated_dir(tmp_path, filename, drop)
    violations, _ = guard.check_dir(workdir)
    assert any(filename in v for v in violations), (
        f"removing {filename}'s receipt step was not caught: {violations}")
    assert guard.main([str(workdir)]) == 1


@pytest.mark.parametrize("filename,job", _discovered())
def test_a_receipt_step_that_only_runs_on_success_is_caught(tmp_path, filename, job):
    # A receipt written only when the run survived is exactly no receipt for
    # the runs this card exists to count.
    def gate_on_success(doc):
        steps = doc["jobs"][job]["steps"]
        for i in _receipt_index(steps):
            steps[i]["if"] = "success()"

    workdir = _mutated_dir(tmp_path, filename, gate_on_success)
    assert any(filename in v for v in guard.check_dir(workdir)[0])


@pytest.mark.parametrize("filename,job", _discovered())
def test_a_receipt_step_that_can_fail_the_job_is_caught(tmp_path, filename, job):
    def let_it_fail(doc):
        steps = doc["jobs"][job]["steps"]
        for i in _receipt_index(steps):
            steps[i].pop("continue-on-error", None)

    workdir = _mutated_dir(tmp_path, filename, let_it_fail)
    assert any(filename in v for v in guard.check_dir(workdir)[0])


@pytest.mark.parametrize("filename,job", _discovered())
def test_an_interpolation_in_the_run_body_is_caught(tmp_path, filename, job):
    # The 21k-character expression ceiling and the injection rule: every value
    # arrives through `env:`.
    def interpolate(doc):
        steps = doc["jobs"][job]["steps"]
        for i in _receipt_index(steps):
            steps[i]["run"] += "\necho ${{ github.event.client_payload.identifier }}"

    workdir = _mutated_dir(tmp_path, filename, interpolate)
    assert any(filename in v for v in guard.check_dir(workdir)[0])


@pytest.mark.parametrize("filename,job", _discovered())
def test_a_receipt_written_before_the_model_ran_is_caught(tmp_path, filename, job):
    # A receipt taken before the last model step describes a run that had not
    # died yet.
    def move_to_front(doc):
        steps = doc["jobs"][job]["steps"]
        found = _receipt_index(steps)
        moved = [steps.pop(i) for i in reversed(found)]
        for step in moved:
            steps.insert(0, step)

    workdir = _mutated_dir(tmp_path, filename, move_to_front)
    assert any(filename in v for v in guard.check_dir(workdir)[0])


@pytest.mark.parametrize("filename,job", _discovered())
def test_dropping_the_upload_is_caught(tmp_path, filename, job):
    # A receipt on a disk destroyed with the runner is not a receipt.
    def drop_upload(doc):
        steps = doc["jobs"][job]["steps"]
        for i in reversed(_receipt_index(steps)):
            if i + 1 < len(steps):
                del steps[i + 1]

    workdir = _mutated_dir(tmp_path, filename, drop_upload)
    assert any(filename in v for v in guard.check_dir(workdir)[0])


def test_a_new_model_running_workflow_with_no_receipt_is_caught(tmp_path):
    """The case the card exists for: the tenth workflow, added blind."""
    workdir = tmp_path / "workflows"
    shutil.copytree(WORKFLOWS, workdir)
    (workdir / "brand-new.yml").write_text(
        "name: Brand New (reusable)\n"
        "on:\n  workflow_call:\n"
        "jobs:\n"
        "  go:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        f"      - uses: {guard.MODEL_ACTION}@" + "a" * 40 + "\n"
    )
    violations, _ = guard.check_dir(workdir)
    assert any("brand-new.yml" in v for v in violations), violations


def test_the_unmutated_copy_still_passes(tmp_path):
    """The control: the mutations above fail because of the mutation, not
    because copying the tree breaks the guard."""
    workdir = tmp_path / "workflows"
    shutil.copytree(WORKFLOWS, workdir)
    assert guard.check_dir(workdir)[0] == []


def test_a_yaml_roundtrip_of_every_discovered_file_still_passes(tmp_path):
    """The other control: `_mutated_dir` rewrites the file through PyYAML, so
    a round trip alone must not produce a violation."""
    for filename, job in _discovered():
        workdir = _mutated_dir(tmp_path / filename, filename, lambda doc: None)
        violations, _ = guard.check_dir(workdir)
        assert violations == [], f"{filename} round trip: {violations}"


def test_the_guard_reads_the_same_receipt_call_every_workflow_writes():
    """One script, one spelling — the guard matches the emitter's own name."""
    assert guard.RECEIPT_CALL.startswith("death_receipt.py")
    assert (SCRIPTS / "death_receipt.py").is_file()


def test_every_discovered_job_is_reported_in_the_stats():
    _, stats = guard.check_dir(WORKFLOWS)
    assert stats["workflows"] > stats["model_jobs"] > 0
    assert stats["receipts"] == stats["model_jobs"]
