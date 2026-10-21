"""One import-safe reader of a repo's sweep holds (DRE-7750).

The sweep already knows when a repo is held: the WIP cap, read off the
caller's own reconcile stub, and the Intake-hold switch. Neither could be
asked from planning, because `reconcile.py` needs REPO and LINEAR_API_KEY at
import. `scripts/sweep_holds.py` holds the cap reading now, `reconcile.py`
binds the same objects, and `read()` adds the one thing planning needs on
top: the stub's own comment about its `max_wip:` line, so a plan can quote
why the repo is held instead of guessing.

The comment is read only from inside the job that holds the cap line, and
only when it is about the cap. A file header, a cron note under `on:` and
another job's comments are never quoted as the hold's reason.
"""

from __future__ import annotations

import json
import os
import subprocess  # nosec B404 — runs this repo's own script
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
SWEEP_HOLDS_PY = SCRIPTS / "sweep_holds.py"
sys.path.insert(0, str(SCRIPTS))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")

import sweep_holds  # noqa: E402

HOLD = (
    "HELD at 0 on 2026-09-08 by CEO decision — this sandbox builds nothing "
    "until the CEO lifts it. Restore it to resume building here."
)
USES = "dreadnought-foundry/bureau-pipeline/.github/workflows/reconcile.yml@stable"
FIELDS = (
    "cap",
    "cap_source",
    "stub_path",
    "stub_comment",
    "intake_hold",
    "max_wip_held",
    "intake_held",
    "held",
)


def _workspace(tmp_path: Path, stubs: dict[str, str]) -> str:
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    for name, text in stubs.items():
        (wf / name).write_text(text)
    return str(tmp_path)


def _stub(job: str, *, header: str = "") -> str:
    return (
        f"{header}"
        "name: Reconcile\n"
        "on:\n"
        "  schedule:\n"
        "    - cron: \"*/15 * * * *\"\n"
        "jobs:\n"
        f"{job}"
    )


def _job(max_wip: str = '"0"', *, key="call", above_key="", above_uses="",
         above_with="", above_cap="", below_cap="", trailing="", after=""):
    """One stub job, with a comment planted wherever the test asks."""
    cap = f"      max_wip: {max_wip}{f'  # {trailing}' if trailing else ''}\n"
    return (
        f"{above_key}"
        f"  {key}:\n"
        f"{above_uses}"
        f"    uses: {USES}\n"
        f"{above_with}"
        "    with:\n"
        "      pipeline_ref: stable\n"
        f"{above_cap}"
        f"{cap}"
        f"{below_cap}"
        "      intake_hold: ${{ vars.INTAKE_HOLD }}\n"
        "    secrets: inherit\n"
        f"{after}"
    )


HELD_STUB = _stub(_job(above_cap=f"      # {HOLD}\n"))


# --------------------------------------------------------------------------
# Import safety, and one set of objects
# --------------------------------------------------------------------------
def test_imports_with_no_environment_at_all():
    """No REPO, no LINEAR_API_KEY, no YAML parser on the path — and neither
    reconcile nor linear_ops is pulled in by the import."""
    probe = (
        "import sys\n"
        f"sys.path.insert(0, {str(SCRIPTS)!r})\n"
        "sys.modules['yaml'] = None\n"
        "import sweep_holds\n"
        "bad = sorted(m for m in ('reconcile', 'linear_ops', 'plan_critic', 'yaml')"
        " if sys.modules.get(m) is not None)\n"
        "print(','.join(bad))\n"
    )
    out = subprocess.run(  # nosec B603 — this interpreter, a fixed probe
        [sys.executable, "-c", probe],
        env={"PATH": os.environ.get("PATH", "")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == ""


def test_reconcile_binds_the_moved_objects():
    import reconcile

    for name in (
        "DEFAULT_MAX_WIP",
        "_whole_number",
        "_RECONCILE_STUB_USES",
        "_STUB_MAX_WIP",
        "cap_and_source",
        "_stub_cap",
    ):
        assert getattr(reconcile, name) is getattr(sweep_holds, name), name


def test_check_wip_cap_reads_the_default_from_sweep_holds():
    import check_wip_cap

    assert Path(check_wip_cap.SWEEP_HOLDS_PY) == SWEEP_HOLDS_PY
    assert check_wip_cap.script_default() == sweep_holds.DEFAULT_MAX_WIP


# --------------------------------------------------------------------------
# The held stub
# --------------------------------------------------------------------------
def test_a_held_stub_is_read_with_its_own_hold_comment(tmp_path):
    ws = _workspace(tmp_path, {"reconcile.yml": HELD_STUB})
    holds = sweep_holds.read(ws)
    assert holds.cap == 0
    assert holds.max_wip_held is True
    assert holds.held is True
    assert holds.stub_path == ".github/workflows/reconcile.yml"
    assert holds.stub_comment == HOLD
    assert holds.cap_source == "the caller's reconcile stub .github/workflows/reconcile.yml"


def test_the_sweeps_own_input_still_quotes_the_stub(tmp_path):
    """The sweep receives the stub's literal as MAX_WIP, so the cap comes
    from the input — and the stub that holds the repo is still the one quoted."""
    ws = _workspace(tmp_path, {"reconcile.yml": HELD_STUB})
    plain = sweep_holds.read(ws)
    holds = sweep_holds.read(ws, max_wip_input="0")
    assert holds.cap == 0
    assert holds.cap_source == "this run's max_wip input"
    assert holds.stub_path == ".github/workflows/reconcile.yml"
    assert holds.stub_comment == HOLD
    assert (holds.stub_path, holds.stub_comment) == (plain.stub_path, plain.stub_comment)


def test_an_input_naming_another_number_does_not_quote_the_stub(tmp_path):
    ws = _workspace(tmp_path, {"reconcile.yml": _stub(_job('"8"', above_cap=f"      # {HOLD}\n"))})
    holds = sweep_holds.read(ws, max_wip_input="0")
    assert holds.cap == 0
    assert holds.max_wip_held is True
    assert holds.stub_path is None
    assert holds.stub_comment == ""


def test_a_variable_cap_equal_to_the_stub_still_quotes_it(tmp_path):
    """Whatever source gave `cap`, the line naming that number is quoted."""
    ws = _workspace(tmp_path, {"reconcile.yml": HELD_STUB})
    holds = sweep_holds.read(ws, max_wip_input="", bureau_max_wip="0")
    assert holds.cap == 0
    assert holds.stub_comment == HOLD


# --------------------------------------------------------------------------
# Where a hold comment can sit
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "layout",
    [
        {"above_cap": f"      # {HOLD}\n"},
        {"below_cap": f"      # {HOLD}\n"},
        {"above_with": f"    # {HOLD}\n"},
        {"above_key": f"  # {HOLD}\n\n"},
    ],
    ids=["above-max_wip", "below-max_wip", "above-with", "above-job-key-after-blank"],
)
def test_the_hold_comment_is_found_in_each_layout(tmp_path, layout):
    ws = _workspace(tmp_path, {"reconcile.yml": _stub(_job(**layout))})
    holds = sweep_holds.read(ws)
    assert holds.cap == 0
    assert holds.stub_comment == HOLD


def test_a_trailing_comment_on_the_cap_line_is_kept(tmp_path):
    ws = _workspace(tmp_path, {"reconcile.yml": _stub(_job(trailing="the sandbox's cap"))})
    assert sweep_holds.read(ws).stub_comment == "the sandbox's cap"


def test_the_kept_block_comes_first_and_the_trailing_comment_last(tmp_path):
    ws = _workspace(
        tmp_path,
        {"reconcile.yml": _stub(_job(above_cap=f"      # {HOLD}\n", trailing="see above"))},
    )
    assert sweep_holds.read(ws).stub_comment == f"{HOLD} see above"


def test_a_multi_line_block_is_joined_with_single_spaces(tmp_path):
    block = "      # HELD at 0 on 2026-09-08 by CEO decision.\n      #\n      #   Restore it to resume.\n"
    ws = _workspace(tmp_path, {"reconcile.yml": _stub(_job(above_cap=block))})
    assert sweep_holds.read(ws).stub_comment == (
        "HELD at 0 on 2026-09-08 by CEO decision. Restore it to resume."
    )


def test_a_comment_after_the_job_belonging_to_the_next_is_never_read(tmp_path):
    """Below the job, after a blank line, indented as the next job's key."""
    after = f"\n  # {HOLD}\n  other:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo hi\n"
    ws = _workspace(tmp_path, {"reconcile.yml": _stub(_job(after=after))})
    holds = sweep_holds.read(ws)
    assert holds.cap == 0
    assert holds.stub_path == ".github/workflows/reconcile.yml"
    assert holds.stub_comment == ""


def test_a_comment_below_after_a_blank_line_is_never_read(tmp_path):
    below = f"\n      # {HOLD}\n"
    ws = _workspace(tmp_path, {"reconcile.yml": _stub(_job(below_cap=below))})
    assert sweep_holds.read(ws).stub_comment == ""


def test_a_comment_below_indented_less_than_the_cap_line_is_never_read(tmp_path):
    below = f"    # {HOLD}\n"
    ws = _workspace(tmp_path, {"reconcile.yml": _stub(_job(below_cap=below))})
    assert sweep_holds.read(ws).stub_comment == ""


def test_a_comment_below_a_later_sibling_key_is_read(tmp_path):
    below = f"      other_input: x\n      # {HOLD}\n"
    ws = _workspace(tmp_path, {"reconcile.yml": _stub(_job(below_cap=below))})
    assert sweep_holds.read(ws).stub_comment == HOLD


def _unrelated_stub(above_key=""):
    return (
        "# The sweep's trigger stub; the cap is set below.\n"
        "# max_wip is how many cards build at once.\n"
        "name: Reconcile\n"
        "on:\n"
        "  schedule:\n"
        "    # every fifteen minutes, off the hour; a held repo still sweeps\n"
        "    - cron: \"7,22,37,52 * * * *\"\n"
        "jobs:\n"
        f"{above_key}"
        "  call:\n"
        "    # thin caller of the sweep\n"
        f"    uses: {USES}\n"
        "    with:\n"
        "      pipeline_ref: stable\n"
        "      max_wip: \"0\"\n"
        "    secrets: inherit\n"
    )


def test_unrelated_comments_are_never_quoted_as_the_hold(tmp_path):
    """A file header and a cron note that both say "cap" or "held", and a
    `uses:` note that says neither, give no comment at all."""
    ws = _workspace(tmp_path, {"reconcile.yml": _unrelated_stub()})
    holds = sweep_holds.read(ws)
    assert holds.cap == 0
    assert holds.stub_path == ".github/workflows/reconcile.yml"
    assert holds.stub_comment == ""


def test_the_hold_above_the_job_key_is_quoted_alone(tmp_path):
    ws = _workspace(tmp_path, {"reconcile.yml": _unrelated_stub(f"  # {HOLD}\n\n")})
    assert sweep_holds.read(ws).stub_comment == HOLD


def test_another_jobs_trailing_comment_is_not_read_above_the_job_key(tmp_path):
    """A note that sits directly under the first job's last line, indented
    inside it, is the first job's — never the second job's hold."""
    first = _job('"8"', key="first", after="    # held note of the first job\n")
    ws = _workspace(tmp_path, {"reconcile.yml": _stub(first + _job(key="second"))})
    holds = sweep_holds.read(ws)
    assert holds.cap == 0
    assert holds.stub_comment == ""


# --------------------------------------------------------------------------
# Which line is the cap line
# --------------------------------------------------------------------------
def test_two_jobs_quote_the_line_that_gave_the_lowest_cap(tmp_path):
    jobs = _job('"8"', key="first", above_cap="      # cap of 8 for the first job\n") + _job(
        key="second", above_cap=f"      # {HOLD}\n"
    )
    ws = _workspace(tmp_path, {"reconcile.yml": _stub(jobs)})
    holds = sweep_holds.read(ws)
    assert holds.cap == 0
    assert holds.stub_comment == HOLD


def test_a_tie_between_stubs_quotes_the_first_by_file_name(tmp_path):
    ws = _workspace(
        tmp_path,
        {
            "b-reconcile.yml": _stub(_job(above_cap="      # held for b\n")),
            "a-reconcile.yml": _stub(_job(above_cap="      # held for a\n")),
        },
    )
    holds = sweep_holds.read(ws)
    assert holds.cap == 0
    assert holds.stub_path == ".github/workflows/a-reconcile.yml"
    assert holds.stub_path in holds.cap_source
    assert holds.stub_comment == "held for a"


def test_a_value_that_is_not_a_whole_number_is_never_the_cap_line(tmp_path):
    jobs = _job('"lots"', key="first", above_cap="      # held wrongly\n") + _job(
        key="second", above_cap=f"      # {HOLD}\n"
    )
    ws = _workspace(tmp_path, {"reconcile.yml": _stub(jobs)})
    assert sweep_holds.read(ws).stub_comment == HOLD


def test_a_stub_with_no_max_wip_line_quotes_nothing(tmp_path):
    job = _job().replace('      max_wip: "0"\n', "")
    ws = _workspace(tmp_path, {"reconcile.yml": _stub(job)})
    holds = sweep_holds.read(ws)
    assert (holds.cap, holds.cap_source) == sweep_holds.cap_and_source(None, ws)
    assert holds.cap == sweep_holds.DEFAULT_MAX_WIP
    assert holds.stub_path is None
    assert holds.stub_comment == ""
    assert holds.max_wip_held is False


def test_no_checkout_quotes_nothing():
    holds = sweep_holds.read(None)
    assert (holds.cap, holds.stub_path, holds.stub_comment) == (
        sweep_holds.DEFAULT_MAX_WIP,
        None,
        "",
    )


def test_the_stub_cap_reading_is_unchanged(tmp_path):
    ws = _workspace(tmp_path, {"reconcile.yml": _stub(_job('"4"'))})
    assert sweep_holds._stub_cap(ws, lambda *a: None) == (
        4,
        "the caller's reconcile stub .github/workflows/reconcile.yml",
    )


# --------------------------------------------------------------------------
# The Intake hold
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "hold", "held"),
    [("2026-09-08", "2026-09-08", True), ("", None, False), ("true", "", True)],
)
def test_the_intake_hold_is_the_switch_reading(tmp_path, raw, hold, held):
    import intake_controls

    ws = _workspace(tmp_path, {"reconcile.yml": _stub(_job('"4"'))})
    holds = sweep_holds.read(ws, intake_hold_raw=raw)
    assert holds.intake_hold == hold == intake_controls.hold(raw)
    assert holds.intake_held is held
    assert holds.held is held
    assert holds.max_wip_held is False


def test_the_record_has_exactly_the_contract_fields():
    assert sweep_holds.SweepHolds._fields == FIELDS


# --------------------------------------------------------------------------
# The CLI
# --------------------------------------------------------------------------
def _cli(ws, **env):
    base = {"PATH": os.environ.get("PATH", ""), "GITHUB_WORKSPACE": ws}
    base.update(env)
    out = subprocess.run(  # nosec B603 — this interpreter, this repo's script
        [sys.executable, str(SWEEP_HOLDS_PY), "read"],
        env=base,
        capture_output=True,
        text=True,
        check=False,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_the_cli_prints_the_record_as_json(tmp_path):
    ws = _workspace(tmp_path, {"reconcile.yml": HELD_STUB})
    unset = _cli(ws)
    passed = _cli(ws, MAX_WIP="0")
    assert tuple(unset) == FIELDS
    assert set(passed) == set(FIELDS)
    assert unset["cap"] == passed["cap"] == 0
    assert passed["cap_source"] == "this run's max_wip input"
    assert (unset["stub_path"], unset["stub_comment"]) == (
        passed["stub_path"],
        passed["stub_comment"],
    ) == (".github/workflows/reconcile.yml", HOLD)


def test_the_cli_takes_a_workspace_and_the_intake_switch(tmp_path):
    ws = _workspace(tmp_path, {"reconcile.yml": _stub(_job('"4"'))})
    out = subprocess.run(  # nosec B603 — this interpreter, this repo's script
        [sys.executable, str(SWEEP_HOLDS_PY), "read", "--workspace", ws],
        env={"PATH": os.environ.get("PATH", ""), "INTAKE_HOLD": "2026-09-08",
             "BUREAU_MAX_WIP": "2"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert out.returncode == 0, out.stderr
    record = json.loads(out.stdout)
    assert record["cap"] == 4
    assert record["intake_hold"] == "2026-09-08"
    assert record["held"] is True
