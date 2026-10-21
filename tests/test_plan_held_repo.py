"""A plan for a held repo is sent back unless it names lifting the hold as a
step that needs the CEO, quoting the stub's own hold comment (DRE-7752).

On 2026-10-19 the CEO approved DRE-7600, a plan for agent-bureau-demo, a repo
whose reconcile stub had held `max_wip: "0"` since 2026-09-08. The plan's
up-front question named one hand step and not the hold, so the plan sat with
nothing built and nobody told. The planner never read the cap, and neither
critic did.

`scripts/plan_held_repo.py` reads the plan's own repo's holds through
`sweep_holds.read` (DRE-7750). `context` tells the planner, quoting the stub;
`findings` appends one `held-repo:` line per hold the plan does not name to
the first critic's structural file, which `plan_seam_gate.py` forwards into
the result — so the round is a send-back whatever the model wrote.

Run: GITHUB_ACTIONS=true LINEAR_API_KEY=test \
     python3 -m pytest tests/test_plan_held_repo.py -v
"""

from __future__ import annotations

import io
import os
import re
import subprocess  # nosec B404 — runs this repo's own script
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
PLAN_HELD_REPO_PY = SCRIPTS / "plan_held_repo.py"
sys.path.insert(0, str(SCRIPTS))

import plan_critic  # noqa: E402
import plan_held_repo  # noqa: E402
import plan_seam_gate  # noqa: E402
import sweep_holds  # noqa: E402

#: The hold comment the card plants, the shape agent-bureau-demo's stub carries.
HOLD = (
    "HELD at 0 on 2026-09-08 by CEO decision — this sandbox builds nothing "
    "until the CEO lifts it. Restore it to resume building here."
)
USES = "dreadnought-foundry/bureau-pipeline/.github/workflows/reconcile.yml@stable"
STUB_PATH = ".github/workflows/reconcile.yml"
SCOPE = "— read for this plan's own repo only"


def _stub(cap_line: str, comment: str = "") -> str:
    above = f"      # {comment}\n" if comment else ""
    return (
        "name: Reconcile\n"
        "on:\n"
        "  schedule:\n"
        "    - cron: \"*/15 * * * *\"\n"
        "jobs:\n"
        "  call:\n"
        f"    uses: {USES}\n"
        "    with:\n"
        "      pipeline_ref: stable\n"
        f"{above}"
        f"{cap_line}"
        "      intake_hold: ${{ vars.INTAKE_HOLD }}\n"
        "    secrets: inherit\n"
    )


HELD_STUB = _stub('      max_wip: "0"\n', HOLD)
OPEN_STUB = _stub('      max_wip: "8"\n')
NO_CAP_STUB = _stub("")

#: An artifact with a risk section and no paragraph naming any hold.
BARE_ARTIFACT = (
    "# Plan\n\n"
    "## Risk assessment\n\n"
    "Step that is not one: nothing here names the hold.\n"
)


def _artifact_with(paragraph: str) -> str:
    return BARE_ARTIFACT + "\n" + paragraph + "\n\nMore prose after it.\n"


#: A paragraph that names `max_wip` and quotes the comment, hard-wrapped and
#: led by a list marker and bold markup, as a planner would write it.
NAMED = (
    "- **Step that needs the CEO:** lifting this repo's max_wip hold. The\n"
    "  reconcile stub says: \"HELD at 0 on 2026-09-08 by CEO decision — this\n"
    "  sandbox builds nothing until the CEO lifts it. Restore it to resume\n"
    "  building here.\""
)
#: Names `max_wip` and `0`, and does not quote the comment.
NAMED_WITHOUT_QUOTE = (
    "Step that needs the CEO: lifting max_wip, which is \"0\" in this repo."
)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for name in ("MAX_WIP", "BUREAU_MAX_WIP", "INTAKE_HOLD", "GITHUB_WORKSPACE"):
        monkeypatch.delenv(name, raising=False)


def _workspace(tmp_path: Path, stub: str | None) -> str:
    ws = tmp_path / "ws"
    wf = ws / ".github" / "workflows"
    wf.mkdir(parents=True)
    if stub is not None:
        (wf / "reconcile.yml").write_text(stub, encoding="utf-8")
    return str(ws)


def _run(argv: list[str]) -> tuple[int, str]:
    out = io.StringIO()
    with mock.patch.object(sys, "stdout", out):
        code = plan_held_repo.main(argv)
    return code, out.getvalue()


def _findings(tmp_path: Path, workspace: str, artifact: str | None,
              structural: str = "") -> tuple[int, list[str], str, str]:
    """Run `findings`; return (exit, structural lines, note text, stdout)."""
    art = tmp_path / "artifact.md"
    if artifact is not None:
        art.write_text(artifact, encoding="utf-8")
    sfile = tmp_path / "structural.txt"
    sfile.write_text(structural, encoding="utf-8")
    note = tmp_path / "note.md"
    note.write_text("Mechanical findings\n", encoding="utf-8")
    code, out = _run(["findings", "--artifact", str(art), "--workspace", workspace,
                      "--structural-file", str(sfile), "--note-file", str(note)])
    lines = [l for l in sfile.read_text(encoding="utf-8").splitlines() if l.strip()]
    return code, lines, note.read_text(encoding="utf-8"), out


def _note_lines(note: str) -> list[str]:
    return [l for l in note.splitlines() if l.startswith("Repo holds:")]


def _gate(tmp_path: Path, structural_lines: list[str]) -> tuple[str, str]:
    """Run the seam gate over a critic PASS; return (before, after)."""
    sfile = tmp_path / "gate-structural.txt"
    sfile.write_text("".join(l + "\n" for l in structural_lines), encoding="utf-8")
    result = tmp_path / "result.md"
    before = plan_critic.result_line(plan_critic.PASS) + "\n"
    result.write_text(before, encoding="utf-8")
    with mock.patch.object(sys, "stdout", io.StringIO()):
        assert plan_seam_gate.main(["gate", "--result-file", str(result),
                                    "--structural-file", str(sfile)]) == 0
    return before, result.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# Held repo, plan without the step
# --------------------------------------------------------------------------
def test_held_repo_plan_without_the_step_is_one_line_and_a_send_back(tmp_path):
    ws = _workspace(tmp_path, HELD_STUB)
    code, lines, note, out = _findings(tmp_path, ws, BARE_ARTIFACT)
    assert code == 0
    assert len(lines) == 1, lines
    line = lines[0]
    assert line.startswith("held-repo:"), line
    assert 'max_wip "0"' in line
    assert STUB_PATH in line
    assert HOLD in line
    assert line in out

    _before, after = _gate(tmp_path, lines)
    verdict, reason = plan_critic.read_result(after)
    assert verdict == plan_critic.SEND_BACK
    assert "held-repo:" in reason


def test_a_bare_artifact_with_no_risk_section_is_still_read(tmp_path):
    ws = _workspace(tmp_path, HELD_STUB)
    _code, lines, _note, _out = _findings(tmp_path, ws, "# Plan\n\nNothing.\n")
    assert len(lines) == 1 and lines[0].startswith("held-repo:")


def test_findings_appends_and_never_truncates(tmp_path):
    seam = "DRE-1: a seam found first."
    ws = _workspace(tmp_path, HELD_STUB)
    _code, lines, _note, _out = _findings(tmp_path, ws, BARE_ARTIFACT,
                                          structural=seam + "\n")
    assert lines[0] == seam
    assert len(lines) == 2 and lines[1].startswith("held-repo:")


# --------------------------------------------------------------------------
# Held repo, plan with the step
# --------------------------------------------------------------------------
def test_held_repo_plan_with_the_step_appends_nothing_and_the_gate_is_a_no_op(tmp_path):
    ws = _workspace(tmp_path, HELD_STUB)
    code, lines, note, _out = _findings(tmp_path, ws, _artifact_with(NAMED))
    assert code == 0
    assert lines == []
    assert (tmp_path / "structural.txt").read_text(encoding="utf-8") == ""

    before, after = _gate(tmp_path, lines)
    assert after == before


@pytest.mark.parametrize("opener", [
    "Step that needs the CEO:",
    "**Step that needs the CEO:**",
    "* Step that needs the CEO:",
    "1. **Step that needs the CEO:**",
])
def test_the_opener_may_follow_a_list_marker_or_bold_markup(tmp_path, opener):
    ws = _workspace(tmp_path, HELD_STUB)
    paragraph = f"{opener} lifting max_wip. The stub says: {HOLD}"
    _code, lines, _note, _out = _findings(tmp_path, ws, _artifact_with(paragraph))
    assert lines == []


def test_the_quote_counts_only_inside_the_paragraph(tmp_path):
    """The comment quoted elsewhere in the artifact, after a blank line, is not
    the paragraph naming the step."""
    ws = _workspace(tmp_path, HELD_STUB)
    artifact = _artifact_with(
        "Step that needs the CEO: lifting max_wip.\n\n" + HOLD)
    _code, lines, _note, _out = _findings(tmp_path, ws, artifact)
    assert len(lines) == 1 and lines[0].startswith("held-repo:")


def test_a_paragraph_without_the_opener_does_not_count(tmp_path):
    ws = _workspace(tmp_path, HELD_STUB)
    artifact = _artifact_with(f"The CEO lifts max_wip. The stub says: {HOLD}")
    _code, lines, _note, _out = _findings(tmp_path, ws, artifact)
    assert len(lines) == 1


def test_the_paragraph_must_name_max_wip_as_well_as_the_quote(tmp_path):
    ws = _workspace(tmp_path, HELD_STUB)
    artifact = _artifact_with(f"Step that needs the CEO: lifting the cap. {HOLD}")
    _code, lines, _note, _out = _findings(tmp_path, ws, artifact)
    assert len(lines) == 1


# --------------------------------------------------------------------------
# Cap passed as the input
# --------------------------------------------------------------------------
def test_cap_passed_as_the_input_still_wants_the_quote(tmp_path, monkeypatch):
    monkeypatch.setenv("MAX_WIP", "0")
    ws = _workspace(tmp_path, HELD_STUB)
    assert sweep_holds.read(ws, "0").stub_comment == HOLD

    _code, lines, _note, _out = _findings(
        tmp_path, ws, _artifact_with(NAMED_WITHOUT_QUOTE))
    assert len(lines) == 1, lines
    assert lines[0].startswith("held-repo:")
    assert HOLD in lines[0]

    _code, lines, _note, _out = _findings(tmp_path, ws, _artifact_with(NAMED))
    assert lines == []


def test_max_wip_and_zero_is_enough_only_when_the_stub_has_no_comment(tmp_path):
    ws = _workspace(tmp_path, _stub('      max_wip: "0"\n'))
    assert sweep_holds.read(ws).stub_comment == ""
    _code, lines, _note, _out = _findings(
        tmp_path, ws, _artifact_with(NAMED_WITHOUT_QUOTE))
    assert lines == []

    _code, lines, _note, _out = _findings(
        tmp_path, ws, _artifact_with("Step that needs the CEO: lifting max_wip."))
    assert len(lines) == 1
    assert 'max_wip "0"' in lines[0]


def test_a_zero_inside_a_date_is_not_the_cap(tmp_path):
    ws = _workspace(tmp_path, _stub('      max_wip: "0"\n'))
    artifact = _artifact_with("Step that needs the CEO: max_wip since 2026-09-08.")
    _code, lines, _note, _out = _findings(tmp_path, ws, artifact)
    assert len(lines) == 1


# --------------------------------------------------------------------------
# Intake hold
# --------------------------------------------------------------------------
def test_intake_hold_without_the_paragraph_is_one_line(tmp_path, monkeypatch):
    monkeypatch.setenv("INTAKE_HOLD", "2026-09-08")
    ws = _workspace(tmp_path, OPEN_STUB)
    _code, lines, _note, _out = _findings(tmp_path, ws, BARE_ARTIFACT)
    assert len(lines) == 1, lines
    assert lines[0].startswith("held-repo:")
    assert "INTAKE_HOLD" in lines[0] and "2026-09-08" in lines[0]
    assert "max_wip" not in lines[0]


def test_intake_hold_with_the_paragraph_is_none(tmp_path, monkeypatch):
    monkeypatch.setenv("INTAKE_HOLD", "2026-09-08")
    ws = _workspace(tmp_path, OPEN_STUB)
    artifact = _artifact_with(
        "Step that needs the CEO: clearing INTAKE_HOLD so new work leaves Intake.")
    _code, lines, _note, _out = _findings(tmp_path, ws, artifact)
    assert lines == []


def test_both_holds_on_and_neither_named_is_two_lines(tmp_path, monkeypatch):
    monkeypatch.setenv("INTAKE_HOLD", "2026-09-08")
    ws = _workspace(tmp_path, HELD_STUB)
    _code, lines, _note, _out = _findings(tmp_path, ws, BARE_ARTIFACT)
    assert len(lines) == 2, lines
    assert all(l.startswith("held-repo:") for l in lines)


# --------------------------------------------------------------------------
# Unheld repo
# --------------------------------------------------------------------------
@pytest.mark.parametrize("stub", [OPEN_STUB, NO_CAP_STUB], ids=["max_wip-8", "no-max_wip"])
@pytest.mark.parametrize("artifact", [BARE_ARTIFACT, _artifact_with(NAMED)],
                         ids=["bare", "named"])
def test_an_unheld_repo_writes_nothing_structural(tmp_path, stub, artifact):
    ws = _workspace(tmp_path, stub)
    code, lines, note, _out = _findings(tmp_path, ws, artifact)
    assert code == 0
    assert lines == []
    assert (tmp_path / "structural.txt").read_text(encoding="utf-8") == ""
    notes = _note_lines(note)
    assert len(notes) == 1, note
    assert notes[0].startswith("Repo holds: none")
    assert notes[0].endswith(SCOPE)


def test_an_unheld_repo_with_an_unreadable_artifact_still_writes_nothing(tmp_path):
    ws = _workspace(tmp_path, OPEN_STUB)
    code, lines, _note, _out = _findings(tmp_path, ws, None)
    assert code == 0 and lines == []


# --------------------------------------------------------------------------
# Unread is never a pass
# --------------------------------------------------------------------------
def test_an_unreadable_artifact_while_held_is_a_finding(tmp_path):
    ws = _workspace(tmp_path, HELD_STUB)
    code, lines, _note, _out = _findings(tmp_path, ws, None)
    assert code == 0
    assert len(lines) == 1, lines
    assert lines[0].startswith("held-repo:")
    assert "could not be read" in lines[0]
    assert 'max_wip "0"' in lines[0]


# --------------------------------------------------------------------------
# The note line
# --------------------------------------------------------------------------
@pytest.mark.parametrize("artifact", [BARE_ARTIFACT, _artifact_with(NAMED), None],
                         ids=["unnamed", "named", "unread"])
def test_every_note_line_ends_with_the_scope(tmp_path, monkeypatch, artifact):
    monkeypatch.setenv("INTAKE_HOLD", "2026-09-08")
    ws = _workspace(tmp_path, HELD_STUB)
    _code, _lines, note, _out = _findings(tmp_path, ws, artifact)
    assert note.startswith("Mechanical findings\n")
    notes = _note_lines(note)
    assert len(notes) == 1, note
    assert notes[0].endswith(SCOPE), notes[0]
    assert 'max_wip "0"' in notes[0] and "INTAKE_HOLD" in notes[0]


def test_the_scope_constant_is_the_contract_wording():
    assert plan_held_repo.SCOPE == SCOPE
    assert plan_held_repo.OPENER == "Step that needs the CEO:"


# --------------------------------------------------------------------------
# context
# --------------------------------------------------------------------------
def test_context_on_an_unheld_repo_is_one_line(tmp_path):
    ws = _workspace(tmp_path, OPEN_STUB)
    code, out = _run(["context", "--workspace", ws])
    assert code == 0
    assert out.strip().splitlines() == [
        "REPO HOLDS: none — max_wip 8 (the caller's reconcile stub "
        ".github/workflows/reconcile.yml); Intake open"
    ]


def _context_paragraph(out: str) -> str:
    """The ready-to-use paragraph: the opener line and its continuation."""
    block = out.splitlines()
    at = next(i for i, l in enumerate(block) if l.startswith("Step that needs the CEO:"))
    para = []
    for line in block[at:]:
        if not line.strip():
            break
        para.append(line)
    return "\n".join(para)


@pytest.mark.parametrize("intake", [None, "2026-09-08"])
@pytest.mark.parametrize("max_wip_input", [None, "0"])
def test_context_on_a_held_repo_quotes_the_comment_and_its_paragraph_passes(
        tmp_path, monkeypatch, intake, max_wip_input):
    if intake:
        monkeypatch.setenv("INTAKE_HOLD", intake)
    if max_wip_input:
        monkeypatch.setenv("MAX_WIP", max_wip_input)
    ws = _workspace(tmp_path, HELD_STUB)
    code, out = _run(["context", "--workspace", ws])
    assert code == 0
    assert out.startswith("REPO HOLDS:")
    assert HOLD in out
    assert 'max_wip "0"' in out
    if intake:
        assert "INTAKE_HOLD" in out and intake in out
    paragraph = _context_paragraph(out)
    assert re.search(r"for this plan's cards only|in general", paragraph)

    _code, lines, _note, _out = _findings(tmp_path, ws, _artifact_with(paragraph))
    assert lines == [], (paragraph, lines)


def test_context_on_an_intake_hold_alone_gives_a_passing_paragraph(tmp_path, monkeypatch):
    monkeypatch.setenv("INTAKE_HOLD", "2026-09-08")
    ws = _workspace(tmp_path, OPEN_STUB)
    _code, out = _run(["context", "--workspace", ws])
    assert out.startswith("REPO HOLDS:")
    assert "2026-09-08" in out
    paragraph = _context_paragraph(out)
    _code, lines, _note, _out = _findings(tmp_path, ws, _artifact_with(paragraph))
    assert lines == []


def test_context_with_no_stub_comment_names_max_wip_and_zero(tmp_path):
    ws = _workspace(tmp_path, _stub('      max_wip: "0"\n'))
    _code, out = _run(["context", "--workspace", ws])
    paragraph = _context_paragraph(out)
    assert "max_wip" in paragraph
    _code, lines, _note, _out = _findings(tmp_path, ws, _artifact_with(paragraph))
    assert lines == []


# --------------------------------------------------------------------------
# Reads holds only through sweep_holds.read
# --------------------------------------------------------------------------
def test_it_reads_holds_only_through_sweep_holds_read(tmp_path):
    source = PLAN_HELD_REPO_PY.read_text(encoding="utf-8")
    assert "sweep_holds.read(" in source
    for second_reader in ("max_wip:", "_cap_line", "_stub_cap", "cap_and_source",
                          "intake_controls.hold", "os.listdir", "workflows"):
        assert second_reader not in source, second_reader

    ws = _workspace(tmp_path, OPEN_STUB)
    held = sweep_holds.read(ws)._replace(
        cap=0, max_wip_held=True, held=True, stub_path=STUB_PATH, stub_comment=HOLD)
    with mock.patch.object(sweep_holds, "read", return_value=held) as read:
        _code, lines, _note, _out = _findings(tmp_path, ws, BARE_ARTIFACT)
    assert read.call_count == 1
    assert len(lines) == 1 and HOLD in lines[0]


def test_the_cli_reads_the_environment_and_defaults_the_workspace(tmp_path):
    ws = _workspace(tmp_path, OPEN_STUB)
    env = {"PATH": os.environ.get("PATH", ""), "GITHUB_ACTIONS": "true",
           "LINEAR_API_KEY": "test", "GITHUB_WORKSPACE": ws,
           "INTAKE_HOLD": "2026-09-08"}
    out = subprocess.run(  # nosec B603 — this interpreter, this repo's script
        [sys.executable, str(PLAN_HELD_REPO_PY), "context"],
        env=env, capture_output=True, text=True, check=False)
    assert out.returncode == 0, out.stderr
    assert out.stdout.startswith("REPO HOLDS:")
    assert "2026-09-08" in out.stdout


# --------------------------------------------------------------------------
# The documents say it
# --------------------------------------------------------------------------
def _loose(needle: str) -> re.Pattern:
    return re.compile(r"\s+".join(re.escape(w) for w in needle.split()), re.I)


def _dre_7521_section() -> str:
    body = (ROOT / "standards" / "card-quality.md").read_text(encoding="utf-8")
    at = body.find("## Every step that needs the CEO is agreed before the work "
                   "starts (DRE-7521)")
    assert at >= 0
    rest = body[at:]
    end = re.compile(r"^## ", re.M).search(rest, 1)
    return rest[: end.start()] if end else rest


def _planner_section() -> str:
    body = (ROOT / "briefs" / "planner.md").read_text(encoding="utf-8")
    at = body.find("Step that needs the CEO:")
    assert at >= 0, "briefs/planner.md does not name `Step that needs the CEO:`"
    head = body.rfind("\n## ", 0, at)
    rest = body[head + 1:]
    end = re.compile(r"^## ", re.M).search(rest, 1)
    return rest[: end.start()] if end else rest


@pytest.mark.parametrize("section", [_dre_7521_section, _planner_section],
                         ids=["card-quality", "planner"])
def test_the_documents_say_what_is_checked_and_what_is_guidance(section):
    text = section()
    assert "Step that needs the CEO:" in text
    assert "max_wip" in text and "INTAKE_HOLD" in text
    assert _loose("plan's own repo").search(text)
    assert _loose("guidance").search(text)
    assert _loose("checked").search(text)


def test_the_brief_says_where_the_paragraph_goes():
    text = _planner_section()
    assert "## Risk assessment" in text
    assert _loose("plan comment").search(text)
    assert "plan_held_repo.py context" in text


def test_the_standard_leaves_every_other_hand_step_to_dre_7523():
    assert "DRE-7523" in _dre_7521_section()
