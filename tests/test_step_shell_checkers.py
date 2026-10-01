"""RED-first tests for DRE-5220 — a moved step changes nothing the four shell checkers report.

DRE-3488 moves five oversized `run: |` blocks into `scripts/<name>.sh`, leaving
one delegation line in the workflow (DRE-5380 owns its grammar in
`scripts/step_shell.py`). Four checkers read the shell inside workflow steps to
keep a census, and a script under `scripts/` was invisible to all four:

  * `check_act_receipts.py` binds every comment-writing site to a step;
  * `pipeline_act.py` counts each declared act's anchor and adopted tag in the
    file its row names;
  * `ready_lane_writers.py` discovers every `linear_ops.py` lane write, and
    names a module after the workflow that runs it;
  * `lane_callers.py` maps each lane writer to the step that calls it.

The property: one fixture workflow, written to a temp root twice — once inline
and once moved with `step_shell.move` — reads the same through all four. The
fixture carries the shapes the real blocks use: a `linear_ops.py state "$CARD"
"Backlog"` write, a `pipeline_act.py receipt` posted through `--body-file`, an
`--act=` flag assembled into a variable, a raw post the registry excuses by
step, and a `code_owner_hold.py hold` that reaches `park`. Each reader's result
is also checked for what it must contain, so two empty answers never pass as
the same answer.
"""

from __future__ import annotations

import shutil
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import check_act_receipts  # noqa: E402
import lane_callers  # noqa: E402
import pipeline_act  # noqa: E402
import ready_lane_writers  # noqa: E402
import step_shell  # noqa: E402

WORKFLOW = ".github/workflows/fixture.yml"
REPORT = "Report the fixture result"
HOLD = "Hold for the code owner"
HOLD_MODULE = "scripts/code_owner_hold.py"

# The scripts the readers need in the temp root: the write layer, whose CLI
# map says which argument is a lane, and the module whose `park` is called.
SCRIPTS = ("linear_ops.py", "code_owner_hold.py")

FIXTURE = textwrap.dedent("""\
    name: Fixture
    on: workflow_dispatch
    jobs:
      work:
        runs-on: ubuntu-latest
        steps:
          - name: Report the fixture result
            env:
              CARD: DRE-1
            run: |
              # DRE-5220: the shapes the real blocks use.
              python3 .bureau-pipeline/scripts/linear_ops.py state "$CARD" "Backlog"
              python3 .bureau-pipeline/scripts/pipeline_act.py receipt fixture-bounce \\
                --body "Bounced <!-- fixture-bounce-tag -->" --out /tmp/fixture-receipt.md
              gh pr comment "$PR" --body-file /tmp/fixture-receipt.md
              ACT_FLAG="--act=fixture-park"
              python3 .bureau-pipeline/scripts/linear_ops.py comment "$CARD" \\
                "Parked for a human" $ACT_FLAG
              python3 .bureau-pipeline/scripts/linear_ops.py comment "$CARD" "Raw fixture notice"
          - name: Hold for the code owner
            run: |
              python3 "$PIPELINE_DIR"/scripts/code_owner_hold.py hold \\
                --card "$CARD" --pr "$PR"
    """)


def _act(name: str, tag: str, anchor: str, adopted: bool) -> dict:
    return {
        "name": name,
        "tag": tag,
        "kind": "hold",
        "state": "parked",
        "next_actor": "operator",
        "subscriber": "fixture.yml",
        "discharges": None,
        "adopted": adopted,
        "emits": {"file": WORKFLOW, "anchor": anchor},
    }


# One adopted act, whose tag the moved step carries, and one declared but not
# yet emitted, whose tag nothing carries.
ACTS = [
    _act("fixture-bounce", "fixture-bounce-tag", "receipt fixture-bounce", True),
    _act("fixture-park", "fixture-unsaid-tag", 'ACT_FLAG="--act=fixture-park"', False),
]
DOC = {
    "acts": ACTS,
    "unconverted": [{
        "file": WORKFLOW,
        "step": REPORT,
        "anchor": "Raw fixture notice",
        "why": "a fixture's deliberately raw post",
    }],
}


def _tree(root: Path) -> Path:
    (root / "scripts").mkdir(parents=True)
    for name in SCRIPTS:
        shutil.copy2(ROOT / "scripts" / name, root / "scripts" / name)
    workflow = root / WORKFLOW
    workflow.parent.mkdir(parents=True)
    workflow.write_text(FIXTURE)
    return root


@pytest.fixture(scope="module")
def trees(tmp_path_factory):
    """(inline root, moved root): the same fixture, before and after the moves."""
    inline = _tree(tmp_path_factory.mktemp("inline"))
    moved = _tree(tmp_path_factory.mktemp("moved"))
    assert step_shell.move(moved, "fixture.yml", REPORT, "fixture_report") == "moved"
    assert step_shell.move(moved, "fixture.yml", HOLD, "fixture_hold", via="pipeline-dir") == "moved"
    return inline, moved


def test_the_moved_tree_really_delegates(trees):
    """Without this every comparison below could be two inline trees agreeing."""
    inline, moved = trees
    for root, delegates in ((inline, False), (moved, True)):
        steps = {
            s["name"]: s
            for s in yaml.safe_load((root / WORKFLOW).read_text())["jobs"]["work"]["steps"]
        }
        for name in (REPORT, HOLD):
            assert (step_shell.delegated_script(steps[name]["run"]) is not None) is delegates


# --- check_act_receipts.py --------------------------------------------------


def _sites(root: Path) -> list:
    return [
        (s.path, s.line, s.step, s.composed_as, s.text)
        for s in check_act_receipts.shell_sites(str(root))
    ]


def test_act_receipts_find_the_same_sites_in_the_same_steps(trees):
    inline, moved = trees
    assert _sites(moved) == _sites(inline)
    found = {(path, step, act) for path, _, step, act, _ in _sites(inline)}
    assert found == {
        (WORKFLOW, REPORT, "fixture-bounce"),  # composed through --body-file
        (WORKFLOW, REPORT, "fixture-park"),  # composed through $ACT_FLAG
        (WORKFLOW, REPORT, None),  # the raw post the registry excuses
    }


def test_act_receipts_read_the_same_act_flags(trees):
    inline, moved = trees
    flags = check_act_receipts.shell_act_flags(str(inline))
    assert check_act_receipts.shell_act_flags(str(moved)) == flags
    assert [(path, act) for path, _, act in flags] == [(WORKFLOW, "fixture-park")]


def test_act_receipts_match_the_declaration_by_step_after_the_move(trees):
    """The registry's row keeps `file` = the workflow and `step` = the step
    name, so it must still match exactly one site once the step has moved."""
    inline, moved = trees

    def about_the_fixture(root):
        return [p for p in check_act_receipts.problems(DOC, str(root)) if WORKFLOW in p]

    assert about_the_fixture(inline) == []
    assert about_the_fixture(moved) == []


def test_act_receipts_see_the_same_pending_acts(trees):
    """An act is pending only while no posting file names it; the moved step
    names both, so neither is pending in either tree."""
    inline, moved = trees
    assert check_act_receipts.pending_acts(DOC, str(inline)) == frozenset()
    assert check_act_receipts.pending_acts(DOC, str(moved)) == frozenset()


# --- pipeline_act.py --------------------------------------------------------


def _registry_problems(root: Path, monkeypatch) -> list:
    monkeypatch.setattr(pipeline_act, "ROOT", str(root))
    found = []
    for entry in ACTS:
        found += pipeline_act._emitter_problems(entry)
    found += pipeline_act._binding_problems(ACTS, [e["tag"] for e in ACTS])
    return found


def test_pipeline_act_counts_the_same_anchors_and_adopted_tags(trees, monkeypatch):
    """Each anchor appears once and the adopted tag is emitted, inline or moved."""
    inline, moved = trees
    assert _registry_problems(inline, monkeypatch) == []
    assert _registry_problems(moved, monkeypatch) == []


# --- ready_lane_writers.py --------------------------------------------------


def test_ready_lane_writers_discover_the_same_writes(trees):
    inline, moved = trees
    writes = ready_lane_writers.writes(str(inline))
    assert ready_lane_writers.writes(str(moved)) == writes
    # The workflow's own lane write.
    assert any(
        w.writer == "fixture.yml" and w.how == "workflow" and w.lane == "Backlog"
        and w.where.startswith(f"{WORKFLOW}:")
        for w in writes
    ), writes
    # A module no glossary names writes as the workflow that runs it.
    hold_writes = [w for w in writes if w.where.startswith(f"{HOLD_MODULE}:")]
    assert hold_writes and {w.writer for w in hold_writes} == {"fixture.yml"}, hold_writes


# --- lane_callers.py --------------------------------------------------------


def test_lane_callers_name_the_same_step(trees):
    inline, moved = trees
    report = lane_callers.callers_of(HOLD_MODULE, "park", root=str(inline))
    assert lane_callers.callers_of(HOLD_MODULE, "park", root=str(moved)) == report
    assert f"{WORKFLOW}#{HOLD}" in report.callers
    assert report.unread == frozenset()
