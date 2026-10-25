"""Every writer that files a Linear card is declared (DRE-7452, epic of the
pipeline notices).

`config/notice-writers.json` is the enumeration the CEO asked for: one row per
writer that creates a Linear card automatically, each declared `notice` (this
epic repoints it to a console alert) or `kept` (it stays a card, and its
reason says why: `work`, `decision` or `record`). `scripts/notice_writers.py`
reads it two ways:

* `check` DISCOVERS every card-creating file — in the bureau-pipeline checkout
  the script sits in and, given `--agent-bureau-dir`, in that agent-bureau
  checkout too — and exits 1 naming any file no row declares for its repo.
  Discovered, never listed: a writer nobody remembered fails by its own file.
* `report` prints, for each `notice` row, whether its file is `repointed`,
  `still-files-cards` or `unread`, and always exits 0.

What this file proves:

* the repository itself passes `check` — this test is the CI gate that turns a
  future undeclared card-filing writer in bureau-pipeline red;
* the declared list carries the 19 writers the card names, with their
  dispositions, reasons and notice keys;
* the discovery rules on THROWAWAY CHECKOUTS (a copy of the script, a fixture
  config and fixture files), so each rule is watched naming a file — and the
  whole-name rule watched NOT naming `cmd_create_label(`;
* the agent-bureau tree is read only when it is named;
* `report`'s three outcomes, each exiting 0.

Run: cd bureau-pipeline && python3 -m pytest tests/test_notice_writers.py -v
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "notice_writers.py"
CONFIG = ROOT / "config" / "notice-writers.json"

#: The text of each card-creating form, assembled here so this test file reads
#: as data. (`tests/` is never discovered anyway; this keeps the fixtures
#: honest about what they hold.)
CREATE_CARD = "create_card" + "("
ISSUE_CREATE = "issue" + "Create("


def run(script: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True, text=True, timeout=120,
    )


def row(id_, file, *, repo="bureau-pipeline", disposition="kept",
        reason="work: a fixture", title="Fixture card", **extra) -> dict:
    r = {"id": id_, "repo": repo, "file": file, "title": title,
         "disposition": disposition, "reason": reason}
    r.update(extra)
    return r


def checkout(tmp_path: Path, rows: list[dict], files: dict[str, str]) -> Path:
    """A throwaway bureau-pipeline checkout: this script, a config, files."""
    root = tmp_path / "bureau-pipeline"
    (root / "scripts").mkdir(parents=True)
    (root / "config").mkdir()
    shutil.copy(SCRIPT, root / "scripts" / "notice_writers.py")
    (root / "config" / "notice-writers.json").write_text(
        json.dumps({"writers": rows}, indent=2))
    write_tree(root, files)
    return root


def write_tree(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)


def script_in(root: Path) -> Path:
    return root / "scripts" / "notice_writers.py"


# --- the repository itself --------------------------------------------------


def test_the_repository_declares_every_card_creating_file():
    """The CI gate: a card-filing writer added to bureau-pipeline and not
    declared turns this red until it is."""
    out = run(SCRIPT, "check")
    assert out.returncode == 0, out.stdout + out.stderr


def test_the_repository_check_reads_no_agent_bureau_file():
    """Without the flag no agent-bureau row is weighed: none is named."""
    out = run(SCRIPT, "check")
    assert out.returncode == 0, out.stdout + out.stderr
    assert "agent-bureau:" not in out.stdout


def test_the_repository_report_reads_every_notice_row_and_exits_zero():
    out = run(SCRIPT, "report")
    assert out.returncode == 0, out.stdout + out.stderr
    lines = [ln for ln in out.stdout.splitlines()
             if ln.startswith("notice-writers: ")]
    data = json.loads(CONFIG.read_text())
    notice = [r["id"] for r in data["writers"]
              if r["disposition"] == "notice"]
    assert [ln.split()[1] for ln in lines] == notice
    # agent-bureau's one notice writer is unread without the flag.
    assert "notice-writers: standards-sync unread" in lines


# --- the declared list -------------------------------------------------------

#: The 19 writers the card names: id → (repo, file, disposition, notice key
#: or the reason's category).
NINETEEN = {
    "medic": ("bureau-pipeline", ".github/workflows/medic.yml",
              "notice", "medic:<repo>:<workflow file>"),
    "red-main-budget": ("bureau-pipeline", ".github/workflows/red-main-repair.yml",
                        "notice", "red_main:<repo>:budget_exhausted"),
    "red-main-decision": ("bureau-pipeline", ".github/workflows/red-main-repair.yml",
                          "notice", "red_main:<repo>:needs_decision"),
    "nightly-watch": ("bureau-pipeline", ".github/workflows/nightly-watch.yml",
                      "notice", None),
    "model-trial": ("bureau-pipeline", ".github/workflows/model-adoption.yml",
                    "notice", "model_trial:<candidate>"),
    "switch-alarm": ("bureau-pipeline", "scripts/switch_reason.py",
                     "notice", "switch_off:<repo>:<SWITCH_NAME>"),
    "groom-stall": ("bureau-pipeline", "scripts/reconcile.py",
                    "notice", "groom_stall"),
    "reviewer-outage": ("bureau-pipeline", "scripts/reconcile.py",
                        "notice", "reviewer_outage:fleet"),
    "standards-sync": ("agent-bureau", ".github/workflows/standards-sync.yml",
                       "notice", "standards_sync:publish_failed"),
    "spending-decision": ("bureau-pipeline", "scripts/model_adoption_actions.py",
                          "kept", "decision"),
    "model-adoption-record": ("bureau-pipeline",
                              "scripts/model_adoption_actions.py",
                              "kept", "record"),
    "release-deferral": ("bureau-pipeline", "scripts/release_deferral.py",
                         "kept", "decision"),
    "epic-growth": ("bureau-pipeline", "scripts/reconcile.py",
                    "kept", "decision"),
    "mirror-refusal": ("bureau-pipeline", "scripts/mirror_check.py",
                       "kept", "work"),
    "onboarding-stopped": ("agent-bureau", ".github/workflows/onboard.yml",
                           "kept", "work"),
    "weekly-report": ("agent-bureau", "console/backend/weekly_agent_report_card.py",
                      "kept", "record"),
    "sunday-make-card": ("agent-bureau",
                         "console/backend/morning_briefing_sunday_actions.py",
                         "kept", "work"),
    "project-agent-card": ("agent-bureau", "console/backend/agent_tools.py",
                           "kept", "work"),
    "demo-seed": ("agent-bureau", "demo/demo_library.py", "kept", "work"),
}


def declared() -> dict[str, dict]:
    return {r["id"]: r for r in json.loads(CONFIG.read_text())["writers"]}


@pytest.mark.parametrize("wid", sorted(NINETEEN))
def test_the_nineteen_writers_are_declared(wid):
    repo, file, disposition, key_or_reason = NINETEEN[wid]
    r = declared()[wid]
    assert (r["repo"], r["file"], r["disposition"]) == (repo, file, disposition)
    titles = r["title"] if isinstance(r["title"], list) else [r["title"]]
    assert titles and all(t.strip() for t in titles)
    if disposition == "notice":
        if key_or_reason is not None:
            assert r["notice_key"] == key_or_reason
    else:
        assert r["reason"].split(":")[0] == key_or_reason


def test_the_nightly_watch_row_carries_both_of_its_keys():
    """Two titles, two keys, one writer: the row names both."""
    r = declared()["nightly-watch"]
    assert len(r["title"]) == 2
    assert r["notice_key"] == [
        "nightly_watch:<owner>:did_not_run",
        "nightly_watch:<owner>:cannot_read",
    ]


def test_the_notice_fallback_is_kept_as_a_record():
    r = declared()["notice-fallback"]
    assert (r["file"], r["disposition"]) == ("scripts/pipeline_notice.py", "kept")
    assert r["reason"] == "record: fallback when the console door is down"


def test_every_row_is_well_formed():
    out = run(SCRIPT, "check")
    assert out.returncode == 0, out.stdout + out.stderr
    assert "malformed" not in out.stdout + out.stderr


# --- discovery, on throwaway checkouts ----------------------------------------


def test_an_undeclared_create_card_script_fails_naming_it(tmp_path):
    root = checkout(tmp_path, [], {
        "scripts/new_alarm.py":
            "import linear_ops\n"
            f"linear_ops.{CREATE_CARD}'Something broke', 'body')\n",
    })
    out = run(script_in(root), "check")
    assert out.returncode == 1
    assert "bureau-pipeline:scripts/new_alarm.py" in out.stdout


def test_an_undeclared_raw_issue_create_mutation_fails_naming_it(tmp_path):
    root = checkout(tmp_path, [], {
        "scripts/raw_writer.py":
            'QUERY = """mutation ($input: IssueCreateInput!) {\n'
            f'  {ISSUE_CREATE}input: $input) {{ issue {{ id }} }}\n'
            '}"""\n',
    })
    out = run(script_in(root), "check")
    assert out.returncode == 1
    assert "bureau-pipeline:scripts/raw_writer.py" in out.stdout


def test_a_declared_file_passes(tmp_path):
    root = checkout(tmp_path, [row("new-alarm", "scripts/new_alarm.py")], {
        "scripts/new_alarm.py": f"linear_ops.{CREATE_CARD}'t', 'b')\n",
    })
    out = run(script_in(root), "check")
    assert out.returncode == 0, out.stdout + out.stderr


def test_a_file_declared_for_the_other_repo_is_still_undeclared(tmp_path):
    root = checkout(tmp_path, [
        row("new-alarm", "scripts/new_alarm.py", repo="agent-bureau"),
    ], {"scripts/new_alarm.py": f"linear_ops.{CREATE_CARD}'t', 'b')\n"})
    out = run(script_in(root), "check")
    assert out.returncode == 1
    assert "bureau-pipeline:scripts/new_alarm.py" in out.stdout


def test_cmd_create_label_is_not_a_card_creating_call(tmp_path):
    """The whole-name rule: `cmd_create_label(` and its kin create no card."""
    root = checkout(tmp_path, [], {
        "scripts/labels.py":
            "cmd_create_label('x')\ncmd_create_milestone('y')\n"
            "my_create_card_helper('z')\nprecreate_card('w')\n",
    })
    out = run(script_in(root), "check")
    assert out.returncode == 0, out.stdout + out.stderr


@pytest.mark.parametrize("name", [
    "create_card", "_create_card", "cmd_oneoff", "cmd_subissue", "cmd_create",
])
def test_each_named_call_is_discovered(tmp_path, name):
    root = checkout(tmp_path, [], {
        "scripts/w.py": f"linear_ops.{name} ('t', 'b')\n",
    })
    out = run(script_in(root), "check")
    assert out.returncode == 1
    assert "bureau-pipeline:scripts/w.py" in out.stdout


@pytest.mark.parametrize("verb", ["create", "oneoff", "subissue"])
def test_a_workflow_line_invoking_linear_ops_is_discovered(tmp_path, verb):
    root = checkout(tmp_path, [], {
        ".github/workflows/alarm.yml":
            "jobs:\n  a:\n    steps:\n      - run: |\n"
            f'          python3 .bureau-pipeline/scripts/linear_ops.py {verb} "$T" b.md\n',
        "scripts/alarm.sh":
            f'python3 scripts/linear_ops.py {verb} "$T" b.md\n',
    })
    out = run(script_in(root), "check")
    assert out.returncode == 1
    assert "bureau-pipeline:.github/workflows/alarm.yml" in out.stdout
    assert "bureau-pipeline:scripts/alarm.sh" in out.stdout


def test_a_linear_ops_verb_that_files_no_card_is_not_discovered(tmp_path):
    root = checkout(tmp_path, [], {
        ".github/workflows/w.yml":
            "jobs:\n  a:\n    steps:\n      - run: |\n"
            '          python3 scripts/linear_ops.py comment "$C" hi\n'
            '          python3 scripts/linear_ops.py create-label x\n'
            '          python3 scripts/linear_ops.py children "$E"\n',
    })
    out = run(script_in(root), "check")
    assert out.returncode == 0, out.stdout + out.stderr


def test_a_comment_line_is_not_a_writer(tmp_path):
    root = checkout(tmp_path, [], {
        ".github/workflows/w.yml":
            "# filed through `linear_ops.py oneoff` elsewhere\n"
            "jobs: {}\n",
        "scripts/w.py": f"# a writer would call linear_ops.{CREATE_CARD}...)\n",
    })
    out = run(script_in(root), "check")
    assert out.returncode == 0, out.stdout + out.stderr


@pytest.mark.parametrize("rel", [
    ".git/hooks/w.py",
    ".bureau-pipeline/scripts/w.py",
    "node_modules/pkg/w.py",
    "tests/w.py",
    "console/backend/tests/w.py",
    "scripts/test_w.py",
    "docs/w.md",
    "scripts/w.json",
])
def test_skipped_paths_are_not_read(tmp_path, rel):
    root = checkout(tmp_path, [], {
        rel: f"linear_ops.{CREATE_CARD}'t', 'b')\n{ISSUE_CREATE}x)\n",
    })
    out = run(script_in(root), "check")
    assert out.returncode == 0, out.stdout + out.stderr


@pytest.mark.parametrize("rel", ["scripts/w.yaml", "deploy/w.sh", "scripts/w.yml"])
def test_every_read_extension_is_read(tmp_path, rel):
    root = checkout(tmp_path, [], {rel: f"{ISSUE_CREATE}x)\n"})
    out = run(script_in(root), "check")
    assert out.returncode == 1
    assert f"bureau-pipeline:{rel}" in out.stdout


def test_a_malformed_row_fails_the_check(tmp_path):
    root = checkout(tmp_path, [
        row("x", "scripts/x.py", disposition="maybe"),
        row("y", "scripts/y.py", reason="because"),
        row("z", "scripts/z.py", disposition="notice", reason="r"),
    ], {})
    out = run(script_in(root), "check")
    assert out.returncode == 1
    for wid in ("x", "y", "z"):
        assert f"malformed row {wid}" in out.stdout


def test_two_rows_with_one_id_fail_the_check(tmp_path):
    root = checkout(tmp_path, [row("x", "scripts/a.py"), row("x", "scripts/b.py")], {})
    out = run(script_in(root), "check")
    assert out.returncode == 1
    assert "duplicate id x" in out.stdout


# --- agent-bureau -------------------------------------------------------------


def agent_bureau(tmp_path: Path, files: dict[str, str]) -> Path:
    ab = tmp_path / "agent-bureau"
    ab.mkdir()
    write_tree(ab, files)
    return ab


AB_WRITER = {
    "console/backend/new_card.py":
        f'Q = "mutation {{ {ISSUE_CREATE}input: $i) {{ success }} }}"\n',
}


def test_an_undeclared_agent_bureau_writer_fails_naming_it(tmp_path):
    root = checkout(tmp_path, [], {})
    ab = agent_bureau(tmp_path, AB_WRITER)
    out = run(script_in(root), "check", "--agent-bureau-dir", str(ab))
    assert out.returncode == 1
    assert "agent-bureau:console/backend/new_card.py" in out.stdout


def test_a_declared_agent_bureau_writer_passes(tmp_path):
    root = checkout(tmp_path, [
        row("new-card", "console/backend/new_card.py", repo="agent-bureau"),
    ], {})
    ab = agent_bureau(tmp_path, AB_WRITER)
    out = run(script_in(root), "check", "--agent-bureau-dir", str(ab))
    assert out.returncode == 0, out.stdout + out.stderr


def test_an_agent_bureau_writer_declared_for_bureau_pipeline_fails(tmp_path):
    root = checkout(tmp_path, [
        row("new-card", "console/backend/new_card.py"),
    ], {})
    ab = agent_bureau(tmp_path, AB_WRITER)
    out = run(script_in(root), "check", "--agent-bureau-dir", str(ab))
    assert out.returncode == 1
    assert "agent-bureau:console/backend/new_card.py" in out.stdout


def test_without_the_flag_no_agent_bureau_file_is_read(tmp_path):
    """The agent-bureau tree sits beside the checkout and is never read
    unless it is named."""
    root = checkout(tmp_path, [], {})
    agent_bureau(tmp_path, AB_WRITER)
    out = run(script_in(root), "check")
    assert out.returncode == 0, out.stdout + out.stderr
    assert "new_card.py" not in out.stdout


def test_a_missing_agent_bureau_dir_fails_the_check(tmp_path):
    root = checkout(tmp_path, [], {})
    out = run(script_in(root), "check", "--agent-bureau-dir",
              str(tmp_path / "nowhere"))
    assert out.returncode != 0


def test_the_same_rules_read_the_agent_bureau_tree(tmp_path):
    root = checkout(tmp_path, [], {})
    ab = agent_bureau(tmp_path, {
        "scripts/linear-api.py": "cmd_create_label('x')\ncmd_create_milestone('y')\n",
        "console/backend/tests/test_fake.py": f"{ISSUE_CREATE}x)\n",
        "node_modules/x/y.py": f"{ISSUE_CREATE}x)\n",
        ".github/workflows/onboard.yml":
            "jobs:\n  a:\n    steps:\n      - run: |\n"
            '          python3 .bureau-pipeline/scripts/linear_ops.py oneoff "$T" b.md\n',
    })
    out = run(script_in(root), "check", "--agent-bureau-dir", str(ab))
    assert out.returncode == 1
    assert "agent-bureau:.github/workflows/onboard.yml" in out.stdout
    for quiet in ("linear-api.py", "test_fake.py", "node_modules"):
        assert quiet not in out.stdout


# --- report -------------------------------------------------------------------

NOTICE = dict(disposition="notice", reason="repointed to a console alert by this epic")


def test_report_reads_a_repointed_writer(tmp_path):
    root = checkout(tmp_path, [
        row("alarm", "scripts/alarm.py", title="Alarm in <repo>",
            notice_key="alarm:<repo>", **NOTICE),
    ], {
        "scripts/alarm.py":
            "import pipeline_notice\n"
            "pipeline_notice.raise_notice(f'alarm:{slug}', severity='WARN',\n"
            "    title=f'Alarm in {slug}', body='b',\n"
            "    fallback={'title': f'Alarm in {slug}', 'lane': 'Intake'})\n",
    })
    out = run(script_in(root), "report")
    assert out.returncode == 0
    assert out.stdout.splitlines() == ["notice-writers: alarm repointed"]


def test_report_reads_a_writer_that_still_files_cards(tmp_path):
    root = checkout(tmp_path, [
        row("alarm", "scripts/alarm.py", title="Alarm in <repo>",
            notice_key="alarm:<repo>", **NOTICE),
    ], {
        "scripts/alarm.py":
            f"linear_ops.{CREATE_CARD}f'Alarm in {{slug}}', 'b')\n",
    })
    out = run(script_in(root), "report")
    assert out.returncode == 0
    assert out.stdout.splitlines() == ["notice-writers: alarm still-files-cards"]


def test_report_still_files_cards_while_the_card_call_remains(tmp_path):
    """A writer half way through its repoint — the client is called, and the
    card is still filed — has not been repointed."""
    root = checkout(tmp_path, [
        row("alarm", ".github/workflows/alarm.yml", title="Alarm in <repo>",
            notice_key="alarm:<repo>", **NOTICE),
    ], {
        ".github/workflows/alarm.yml":
            "jobs:\n  a:\n    steps:\n      - run: |\n"
            '          python3 .bureau-pipeline/scripts/pipeline_notice.py raise --notice "alarm:$SLUG" \\\n'
            '            --severity WARN --title "$T" --body-file b.md\n'
            '          python3 .bureau-pipeline/scripts/linear_ops.py create "$T" b.md\n',
    })
    out = run(script_in(root), "report")
    assert out.returncode == 0
    assert out.stdout.splitlines() == ["notice-writers: alarm still-files-cards"]


def test_report_needs_the_rows_own_key_to_call_it_repointed(tmp_path):
    root = checkout(tmp_path, [
        row("alarm", "scripts/alarm.py", title="Alarm",
            notice_key="alarm:<repo>:budget", **NOTICE),
    ], {
        "scripts/alarm.py":
            "pipeline_notice.raise_notice(f'alarm:{slug}:other', severity='WARN',\n"
            "    title='Alarm', body='b')\n",
    })
    out = run(script_in(root), "report")
    assert out.returncode == 0
    assert out.stdout.splitlines() == ["notice-writers: alarm still-files-cards"]


def test_report_reads_a_writer_by_its_site_beside_kept_writers(tmp_path):
    """A row naming its `site` is read in that function only, so a file that
    also holds kept writers can still read as repointed."""
    root = checkout(tmp_path, [
        row("stall", "scripts/sweep.py", title="Stall", site="report_stall",
            notice_key="stall", **NOTICE),
        row("other", "scripts/sweep.py", title="Work card"),
    ], {
        "scripts/sweep.py":
            "def report_stall():\n"
            "    pipeline_notice.raise_notice('stall', severity='WARN',\n"
            "        title='Stall', body='b')\n"
            "\n"
            "def file_work():\n"
            f"    linear_ops.{CREATE_CARD}'Work card', 'b')\n",
    })
    out = run(script_in(root), "report")
    assert out.returncode == 0
    assert out.stdout.splitlines() == ["notice-writers: stall repointed"]


def test_report_reads_a_site_that_still_files_a_card(tmp_path):
    root = checkout(tmp_path, [
        row("stall", "scripts/sweep.py", title="Stall", site="report_stall",
            notice_key="stall", **NOTICE),
    ], {
        "scripts/sweep.py":
            "def report_stall():\n"
            f"    linear_ops.{CREATE_CARD}'Stall', 'b')\n"
            "\n"
            "def other():\n"
            "    pipeline_notice.raise_notice('stall', severity='WARN',\n"
            "        title='Stall', body='b')\n",
    })
    out = run(script_in(root), "report")
    assert out.returncode == 0
    assert out.stdout.splitlines() == ["notice-writers: stall still-files-cards"]


def test_report_leaves_an_agent_bureau_row_unread_without_the_flag(tmp_path):
    root = checkout(tmp_path, [
        row("sync", ".github/workflows/standards-sync.yml", repo="agent-bureau",
            title="sync failed", notice_key="standards_sync:publish_failed",
            **NOTICE),
    ], {})
    out = run(script_in(root), "report")
    assert out.returncode == 0
    assert out.stdout.splitlines() == ["notice-writers: sync unread"]


def test_report_reads_an_agent_bureau_row_given_the_flag(tmp_path):
    root = checkout(tmp_path, [
        row("sync", ".github/workflows/standards-sync.yml", repo="agent-bureau",
            title="sync failed", notice_key="standards_sync:publish_failed",
            **NOTICE),
    ], {})
    ab = agent_bureau(tmp_path, {
        ".github/workflows/standards-sync.yml":
            "jobs:\n  a:\n    steps:\n      - run: |\n"
            '          python3 .bureau-pipeline/scripts/linear_ops.py create "$T" b.md\n',
    })
    out = run(script_in(root), "report", "--agent-bureau-dir", str(ab))
    assert out.returncode == 0
    assert out.stdout.splitlines() == ["notice-writers: sync still-files-cards"]


def test_report_names_only_notice_rows(tmp_path):
    root = checkout(tmp_path, [
        row("kept-one", "scripts/k.py"),
        row("alarm", "scripts/alarm.py", title="Alarm", notice_key="alarm",
            **NOTICE),
    ], {
        "scripts/k.py": f"linear_ops.{CREATE_CARD}'K', 'b')\n",
        "scripts/alarm.py": f"linear_ops.{CREATE_CARD}'Alarm', 'b')\n",
    })
    out = run(script_in(root), "report")
    assert out.returncode == 0
    assert out.stdout.splitlines() == ["notice-writers: alarm still-files-cards"]


def test_report_exits_zero_on_a_malformed_config(tmp_path):
    root = checkout(tmp_path, [], {})
    (root / "config" / "notice-writers.json").write_text("{not json")
    out = run(script_in(root), "report")
    assert out.returncode == 0
