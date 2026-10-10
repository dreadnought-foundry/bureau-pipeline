"""The daily split-ledger job is retired (DRE-6056).

The CEO's decision of 2026-10-06: the planner reads the split history from our
own database instead of from a file a scheduled job commits. DRE-6054 serves it
(`GET /api/v1/pipeline/split-history`), DRE-6055 derives the ledger from it once
per plan run, and this card deletes the job and everything that existed only
for it. What these tests hold:

  * **The job is gone from the code, not just switched off.** While the
    workflow file exists, re-enabling "everything that was paused" turns it
    back on. Its committed output, its publisher and the publisher's tests go
    with it.
  * **The trusted-branch list narrows by exactly the job's branch.** The CEO's
    signed answer of 2026-09-15 added two literals; DRE-6049 removed the
    first, and this removes the second. `bot/standards-sync` keeps its entry,
    and the list is the same exact set in every copy: the gate's event
    filter, its branch `case`, and `reconcile.PIPELINE_BRANCH_PREFIXES`.
  * **There is no committed fallback.** With no path and no
    `SPLIT_LEDGER_PATH`, `split_ledger.load()` raises `LedgerError`, so every
    reader renders "could not be read" and never a stale file.

The retired names are built from pieces rather than spelled whole: the card's
own acceptance check is that they appear nowhere outside the historical records
and `tests/fixtures/`, and this file is held to that check too.

Run: cd bureau-pipeline && python3 -m pytest tests/test_split_ledger_retired.py -v
"""

from __future__ import annotations

import os
import re
import subprocess  # nosec B404 — runs the gate's own shell pattern and git
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import ledger_context  # noqa: E402
import plan_critic  # noqa: E402
import reconcile  # noqa: E402
import split_ledger  # noqa: E402
import step_shell  # noqa: E402

#: The retired job's own branch, assembled so this file does not carry it.
RETIRED_BRANCH = "bot/" + "split-ledger"
#: The two files the job committed, and its workflow, assembled the same way.
LEDGER_JSON = "config/" + "split-ledger.json"
LEDGER_DOC = "docs/" + "split-ledger.md"
WORKFLOW_FILE = "split-ledger" + ".yml"

#: The publisher the job was the last caller of, assembled the same way.
PUBLISHER = "bot_branch" + "_pr"

#: Every path the card deletes. `tests/bot_branch_harness.py` is the deleted
#: tests' own harness, with no other caller.
DELETED = (
    ".github/workflows/" + WORKFLOW_FILE,
    LEDGER_JSON,
    LEDGER_DOC,
    f"scripts/{PUBLISHER}.py",
    f"tests/test_{PUBLISHER}.py",
    "tests/test_split_ledger_workflow.py",
    "tests/test_bot_pipeline_branches.py",
    "tests/bot_branch_harness.py",
)

#: The trusted list after this card: the branch shapes the gate trusted before
#: DRE-3879, and nothing else.
TRUSTED = {"agent/", "repair/", "dependabot/", "bot/standards-sync"}

#: The historical records the card names, never edited. Each one may keep
#: naming the retired branch and the retired files, and nothing else may.
HISTORICAL = (
    "docs/split-ledger-audit.md",
    "docs/release-decision-proof-2026-09.md",
    "docs/medic-wake-proof-2026-09.md",
    "config/planner-audit.json",
    # DRE-6381's proof record quotes the retired branch and workflow verbatim:
    # the dry run's own output and the CEO's signed answers. A quote cannot be
    # reworded, so the record is a historical record like the ones above.
    "docs/send-back-class-proof-2026-10.md",
)


def _doc(name: str) -> dict:
    return yaml.safe_load(step_shell.workflow_source(WORKFLOWS / name))


def _gate_pattern(name: str = "merge-gate.yml") -> str:
    m = re.search(r'case "\$BRANCH" in ([^)]+)\)',
                  step_shell.workflow_source(WORKFLOWS / name))
    assert m is not None, f"{name}: no branch case statement"
    return m.group(1)


def _script_pattern() -> str:
    m = re.search(r'case "\$BRANCH" in ([^)]+)\)',
                  (ROOT / "scripts" / "evaluate_and_merge.sh").read_text())
    assert m is not None, "evaluate_and_merge.sh: no branch case statement"
    return m.group(1)


def _admits(pattern: str, branch: str) -> bool:
    """Whether a shell `case` pattern matches `branch` — RUN, not read for."""
    script = f'case "$1" in {pattern}) echo yes;; *) echo no;; esac'
    done = subprocess.run(  # nosec B603 B607 — the gate's own pattern
        ["bash", "-c", script, "bash", branch],
        capture_output=True, text=True, check=True)
    return done.stdout.strip() == "yes"


def _tracked() -> list[str]:
    out = subprocess.run(  # nosec B603 B607 — fixed args, no shell
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True,
        check=True)
    files = [p for p in out.stdout.split("\0") if p]
    assert files, "git ls-files returned nothing — cannot run the sweep"
    return files


def _mentions(*needles: str) -> list[str]:
    """Every tracked file outside the fixtures and the historical records that
    still names one of `needles`."""
    found = []
    for rel in _tracked():
        if rel.startswith("tests/fixtures/") or rel in HISTORICAL:
            continue
        try:
            text = (ROOT / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, IsADirectoryError, FileNotFoundError):
            continue
        if any(needle in text for needle in needles):
            found.append(rel)
    return sorted(found)


# --------------------------------------------------------------------------- #
# the job and everything that existed only for it                              #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("rel", DELETED)
def test_the_retired_file_is_gone(rel):
    assert not (ROOT / rel).exists(), f"{rel} is still here"


def test_no_workflow_publishes_a_bot_branch():
    callers = sorted(path.name for path in WORKFLOWS.glob("*.yml")
                     if PUBLISHER in step_shell.workflow_source(path))
    assert callers == []


def test_no_tracked_file_calls_the_publisher():
    assert _mentions(PUBLISHER) == []


def test_the_medic_no_longer_watches_the_job():
    doc = _doc("self-medic.yml")
    watched = doc.get("on", doc.get(True))["workflow_run"]["workflows"]
    assert "Split " + "ledger" not in watched


def test_every_name_the_medic_watches_is_a_workflow_here():
    """A watched name with no workflow behind it is a dangling entry."""
    names = {(_doc(path.name) or {}).get("name")
             for path in WORKFLOWS.glob("*.yml")}
    doc = _doc("self-medic.yml")
    watched = doc.get("on", doc.get(True))["workflow_run"]["workflows"]
    assert [w for w in watched if w not in names] == []


def test_the_workflow_watcher_check_passes():
    done = subprocess.run(  # nosec B603 B607 — the repo's own checker
        [sys.executable, str(ROOT / "scripts" / "check_workflow_watchers.py")],
        cwd=ROOT, capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr


# --------------------------------------------------------------------------- #
# the trusted list narrows by exactly that branch                              #
# --------------------------------------------------------------------------- #


def test_the_gates_branch_case_is_exactly_the_trusted_list():
    prefixes = {p.strip().rstrip("*") for p in _gate_pattern().split("|")}
    assert prefixes == TRUSTED


def test_the_merge_script_case_is_exactly_the_trusted_list():
    prefixes = {p.strip().rstrip("*") for p in _script_pattern().split("|")}
    assert prefixes == TRUSTED


def test_the_gates_event_filter_names_only_bot_standards_sync():
    condition = _doc("merge-gate.yml")["jobs"]["evaluate"]["if"]
    assert set(re.findall(r"head_branch == '([^']+)'", condition)) == {
        "bot/standards-sync"}


def test_the_sweeps_trusted_list_is_exactly_the_trusted_list():
    assert set(reconcile.PIPELINE_BRANCH_PREFIXES) == TRUSTED


def test_the_retired_branch_has_no_merge_rights_anywhere():
    assert not _admits(_gate_pattern(), RETIRED_BRANCH)
    assert not _admits(_script_pattern(), RETIRED_BRANCH)
    assert not reconcile.pipeline_owns(RETIRED_BRANCH)


def test_bot_standards_sync_keeps_its_entry():
    """The control: without it the test above passes on a gate that admits
    nothing at all."""
    assert _admits(_gate_pattern(), "bot/standards-sync")
    assert _admits(_script_pattern(), "bot/standards-sync")
    assert reconcile.pipeline_owns("bot/standards-sync")


def test_no_live_file_names_the_retired_branch():
    assert _mentions(RETIRED_BRANCH) == []


def test_no_live_file_names_the_retired_ledger_files():
    assert _mentions(LEDGER_JSON, LEDGER_DOC) == []


def test_no_live_file_names_the_retired_workflow():
    """By its file name. The plan job's own derive step is still called "Split
    ledger — derive it from the read door" (DRE-6055), and that is not the
    retired job."""
    assert _mentions(WORKFLOW_FILE) == []


# --------------------------------------------------------------------------- #
# no committed fallback                                                        #
# --------------------------------------------------------------------------- #


def test_load_with_no_path_and_no_env_raises(monkeypatch):
    monkeypatch.delenv("SPLIT_LEDGER_PATH", raising=False)
    with pytest.raises(split_ledger.LedgerError, match="SPLIT_LEDGER_PATH"):
        split_ledger.load()


def test_load_with_an_empty_env_raises(monkeypatch):
    monkeypatch.setenv("SPLIT_LEDGER_PATH", "")
    with pytest.raises(split_ledger.LedgerError):
        split_ledger.load()


def test_the_module_names_no_committed_ledger():
    assert not hasattr(split_ledger, "LEDGER_PATH")
    assert not hasattr(split_ledger, "DOC_PATH")
    assert not hasattr(ledger_context, "LEDGER_PATH")


def test_the_markdown_renderer_is_gone():
    assert not hasattr(split_ledger, "render_markdown")


def test_derive_names_its_output_or_refuses(monkeypatch, capsys):
    """`--out` has no default: nothing is written into the checkout."""
    monkeypatch.delenv("SPLIT_LEDGER_PATH", raising=False)
    with pytest.raises(SystemExit) as raised:
        split_ledger.main(["derive"])
    assert raised.value.code == 2
    assert "--out" in capsys.readouterr().err


def test_derive_offers_no_markdown_flags(capsys):
    with pytest.raises(SystemExit):
        split_ledger.main(["derive", "--help"])
    help_text = capsys.readouterr().out
    assert "--doc" not in help_text
    assert "--no-doc" not in help_text


def test_render_with_no_ledger_says_could_not_be_read_and_exits_0(
        monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("SPLIT_LEDGER_PATH", raising=False)
    code = ledger_context.main(["render", "--mulch", str(tmp_path / "none.jsonl"),
                                "--now", "2026-10-08T00:00:00Z"])
    assert code == 0
    status = [line for line in capsys.readouterr().out.splitlines()
              if line.startswith("LEDGER STATUS:")]
    assert len(status) == 1, status
    assert status[0].startswith("LEDGER STATUS: UNKNOWN — ")
    assert "could not be read" in status[0]


def test_render_reads_split_ledger_path_when_no_ledger_is_named(
        monkeypatch, tmp_path, capsys):
    """The plan job exports the path; the context step names no `--ledger`."""
    derived = tmp_path / "derived.json"
    derived.write_text('{"generated_at": "2026-10-08T00:00:00Z", "rows": [], '
                       '"rates": {"by_tell": []}}', encoding="utf-8")
    monkeypatch.setenv("SPLIT_LEDGER_PATH", str(derived))
    assert ledger_context.main(["render", "--mulch", str(tmp_path / "none.jsonl"),
                                "--now", "2026-10-08T01:00:00Z"]) == 0
    out = capsys.readouterr().out
    assert "LEDGER STATUS: fresh — " in out


def test_the_critic_cites_the_ledger_as_derived_at_plan_time():
    assert plan_critic.LEDGER_FILE == (
        "the split ledger, derived from the console's record at plan time")
