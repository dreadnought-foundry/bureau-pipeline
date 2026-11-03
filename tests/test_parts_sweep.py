"""The weekly sweep reads the roster live and counts each repo's copies of
shared parts now and a week ago, one record per repo (DRE-8944).

`scripts/parts_sweep.py` has three commands. `roster` reads
`config/repo-map.json` at run time and prints one row per key, with the labels
a cleanup card or a replacement card is filed under. `count` runs in one
repo's checkout: it counts copies with `parts_check.count` at the checkout and
again in a temporary `git worktree` of the newest commit at or before
`--week-ago`, and writes the count record. `record` writes the record of a
repo the workflow did not count.

Every count case builds a real git repo in a temporary folder, with commits
dated by hand, so the week-ago commit is the one git chooses from the history.
The pipeline root is a temporary copy of the two config files, so no case
reads the live roster.

Run: cd bureau-pipeline && python3 -m pytest tests/test_parts_sweep.py -v
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")

import linear_ops  # noqa: E402
import parts_cleanup  # noqa: E402
import parts_sweep  # noqa: E402
import validate_card  # noqa: E402
from test_parts_cleanup import Board  # noqa: E402 — the stubbed board the filer's own tests use

SCRIPT = ROOT / "scripts" / "parts_sweep.py"
PARTS_CHECK = ROOT / "scripts" / "parts_check.py"
DOC = ROOT / "docs" / "parts-sweep.md"

#: Today's roster, ten keys, the four sandboxes and bureau-pipeline included.
REPO_MAP = {
    "atlas": "EveryBite/atlas",
    "deltasolv": "DeltaSolv/deltasolv",
    "agent-bureau": "dreadnought-foundry/agent-bureau",
    "agent-bureau-demo": "dreadnought-foundry/agent-bureau-demo",
    "bureau-pipeline": "dreadnought-foundry/bureau-pipeline",
    "portico": "dreadnought-foundry/portico",
    "foundry": "dreadnought-foundry/foundry",
    "delete-sandbox": "dreadnought-foundry/delete-sandbox",
    "handover-sandbox": "dreadnought-foundry/handover-sandbox",
    "pause-sandbox": "dreadnought-foundry/pause-sandbox",
}
SANDBOXES = ("agent-bureau-demo", "delete-sandbox", "handover-sandbox", "pause-sandbox")

LISTED = "portico"
LISTED_ENTRY = {"repo": LISTED, "agent": "agent:backend", "initiative": "initiative:foundry",
                "owned_by": "DRE-8536"}
UNLISTED = "atlas"
NOT_CARDED = ", not carded yet — no entry in config/parts-count.json"

UNREAD_REASON = "the read token could not be minted for EveryBite/atlas"
ARCHIVED_REASON = "archived on GitHub, so it is read-only and not swept"
FAILED_REASON = ("the read token could not be minted for dreadnought-foundry/foundry, "
                 "inside this organization")

DECLARATION = ".github/bureau/design-system.json"
MOLECULES = "web/src/components/molecules"
NOTICE = f"{MOLECULES}/Notice.tsx"
DECLARED = json.dumps({"parts": {"atoms": [], "molecules": [MOLECULES],
                                 "organisms": [], "templates": []}})
NOTICE_SRC = """\
import React from "react";

export function Notice({ title }: { title: string }) {
  return <div role="status">{title}</div>;
}
"""


def _page(name: str) -> str:
    """A page that grew its own `Notice`: one copy of the shared part."""
    return f"""\
import React from "react";

function Notice({{ title }}: {{ title: string }}) {{
  return <p className="notice">{{title}}</p>;
}}

export function {name}() {{
  return <Notice title="{name}" />;
}}
"""


def _pages(n: int) -> dict:
    """Pages 1 to `n` each holding a copy, and any page past `n` deleted."""
    return {f"web/src/pages/Page{i}.tsx": _page(f"Page{i}") if i <= n else None
            for i in range(1, 7)}


def _with_copies(n: int) -> dict:
    return {DECLARATION: DECLARED, NOTICE: NOTICE_SRC, **_pages(n)}


def _git(root: Path, *args: str, when: str | None = None) -> str:
    env = dict(os.environ)
    if when is not None:
        env.update(GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when)
    return subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.com",
         "-c", "commit.gpgsign=false", *args],
        check=True, capture_output=True, text=True, env=env).stdout


def _commit(root: Path, files: dict, when: str) -> str:
    """Write `files` (None deletes), commit them dated `when`, return the sha."""
    for path, text in files.items():
        target = root / path
        if text is None:
            target.unlink(missing_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    if not (root / ".git").exists():
        _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "--allow-empty", "-m", f"at {when}", when=when)
    return _git(root, "rev-parse", "HEAD").strip()


def _worktrees(root: Path) -> list:
    listed = _git(root, "worktree", "list", "--porcelain")
    return [line for line in listed.splitlines() if line.startswith("worktree ")]


class Run:
    def __init__(self, code, out, err):
        self.code, self.out, self.err = code, out, err
        self.err_lines = err.splitlines()


class SweepCase(unittest.TestCase):

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.pipeline = self.base / "pipeline"
        (self.pipeline / "config").mkdir(parents=True)
        self.write_config(REPO_MAP, [LISTED_ENTRY])
        self.repo = self.base / "repo"
        self.repo.mkdir()
        self.json_out = self.base / "out" / "count.json"
        self.count_out = self.base / "out" / "parts-count.json"
        self.json_out.parent.mkdir()

    def write_config(self, repo_map: dict, entries: list):
        config = self.pipeline / "config"
        (config / "repo-map.json").write_text(json.dumps(repo_map), encoding="utf-8")
        (config / "parts-count.json").write_text(json.dumps({"repos": entries}),
                                                 encoding="utf-8")

    def sweep(self, *args: str) -> Run:
        got = subprocess.run([sys.executable, str(SCRIPT), *args,
                              "--pipeline-root", str(self.pipeline)],
                             capture_output=True, text=True)
        return Run(got.returncode, got.stdout, got.stderr)

    def sweep_in_process(self, *args: str) -> Run:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = parts_sweep.main([*args, "--pipeline-root", str(self.pipeline)])
        return Run(code, out.getvalue(), err.getvalue())

    def count(self, slug: str, week_ago: str) -> Run:
        return self.sweep("count", "--repo", slug, "--repo-root", str(self.repo),
                          "--week-ago", week_ago, "--json-out", str(self.json_out),
                          "--count-out", str(self.count_out))

    def record_of(self) -> dict:
        return json.loads(self.json_out.read_text(encoding="utf-8"))

    def assert_one_line(self, run: Run, line: str):
        self.assertEqual([line], run.err_lines, run.err)


# ---------------------------------------------------------------------------
# roster
# ---------------------------------------------------------------------------

def _rows(run: Run) -> list:
    lines = run.out.splitlines()
    assert len(lines) == 1 and lines[0].startswith("repos="), run.out
    return json.loads(lines[0][len("repos="):])


class RosterTest(SweepCase):

    def test_ten_keys_print_ten_rows_in_the_maps_order(self):
        run = self.sweep("roster")
        self.assertEqual(0, run.code, run.err)
        rows = _rows(run)
        self.assertEqual(list(REPO_MAP), [row["repo"] for row in rows])
        self.assertEqual(10, len(rows))
        for row in rows:
            self.assertEqual(["repo", "owner", "name", "agent", "initiative", "owned_by",
                              "listed"], list(row))
            self.assertEqual(REPO_MAP[row["repo"]], f"{row['owner']}/{row['name']}")

    def test_the_listed_repo_takes_its_entrys_labels(self):
        rows = {row["repo"]: row for row in _rows(self.sweep("roster"))}
        self.assertEqual({"repo": LISTED, "owner": "dreadnought-foundry", "name": LISTED,
                          "agent": "agent:backend", "initiative": "initiative:foundry",
                          "owned_by": "DRE-8536", "listed": True}, rows[LISTED])

    def test_an_unlisted_repo_takes_frontend_and_its_own_slug(self):
        rows = {row["repo"]: row for row in _rows(self.sweep("roster"))}
        self.assertEqual({"repo": UNLISTED, "owner": "EveryBite", "name": UNLISTED,
                          "agent": "agent:frontend", "initiative": f"initiative:{UNLISTED}",
                          "owned_by": "", "listed": False}, rows[UNLISTED])

    def test_unlisted_agent_bureau_gets_initiative_bureau(self):
        rows = {row["repo"]: row for row in _rows(self.sweep("roster"))}
        self.assertEqual("initiative:bureau", rows["agent-bureau"]["initiative"])
        self.assertFalse(rows["agent-bureau"]["listed"])
        self.assertEqual("", rows["agent-bureau"]["owned_by"])

    def test_the_sandboxes_and_the_pipeline_are_rows_like_any_other(self):
        rows = {row["repo"]: row for row in _rows(self.sweep("roster"))}
        for slug in (*SANDBOXES, "bureau-pipeline"):
            self.assertEqual({"agent": "agent:frontend", "initiative": f"initiative:{slug}",
                              "owned_by": "", "listed": False},
                             {k: rows[slug][k] for k in ("agent", "initiative", "owned_by",
                                                         "listed")})

    def test_the_map_is_read_live(self):
        self.write_config({**REPO_MAP, "newcomer": "dreadnought-foundry/newcomer"},
                          [LISTED_ENTRY])
        rows = _rows(self.sweep("roster"))
        self.assertEqual(11, len(rows))
        self.assertEqual("newcomer", rows[-1]["repo"])

    def test_an_entry_with_no_owner_prints_an_empty_owned_by(self):
        self.write_config(REPO_MAP, [{"repo": "foundry", "agent": "agent:frontend",
                                      "initiative": "initiative:foundry"}])
        rows = {row["repo"]: row for row in _rows(self.sweep("roster"))}
        self.assertEqual(("", True), (rows["foundry"]["owned_by"], rows["foundry"]["listed"]))

    def test_repo_keeps_the_one_row(self):
        run = self.sweep("roster", "--repo", "agent-bureau")
        self.assertEqual(0, run.code, run.err)
        self.assertEqual(["agent-bureau"], [row["repo"] for row in _rows(run)])

    def test_repo_names_a_slug_the_map_does_not_carry(self):
        run = self.sweep("roster", "--repo", "storefront")
        self.assertEqual(1, run.code)
        self.assertEqual("", run.out)
        self.assertEqual(1, len(run.err_lines), run.err)
        self.assertTrue(run.err_lines[0].startswith("parts-sweep: storefront — UNKNOWN — "),
                        run.err)
        self.assertIn("storefront", run.err_lines[0].split("UNKNOWN", 1)[1])

    def test_an_unreadable_map_is_unknown(self):
        (self.pipeline / "config" / "repo-map.json").write_text("{", encoding="utf-8")
        run = self.sweep("roster")
        self.assertEqual(1, run.code)
        self.assertEqual("", run.out)
        self.assertIn("config/repo-map.json", run.err)


class InitiativeTableReadInvertedTest(SweepCase):
    """`validate_card._INITIATIVE_ALIAS` maps an initiative to the repo slug it
    routes to; an unlisted repo's initiative is that table read backwards."""

    def test_the_real_table_gives_agent_bureau_initiative_bureau(self):
        self.assertEqual({"bureau": "agent-bureau"}, validate_card._INITIATIVE_ALIAS)
        rows = {row["repo"]: row for row in _rows(self.sweep_in_process("roster"))}
        self.assertEqual("initiative:bureau", rows["agent-bureau"]["initiative"])

    def test_a_patched_table_gives_storefront_initiative_shop(self):
        self.write_config({**REPO_MAP, "storefront": "dreadnought-foundry/storefront"},
                          [LISTED_ENTRY])
        with patch.dict(validate_card._INITIATIVE_ALIAS, {"shop": "storefront"}):
            rows = {row["repo"]: row for row in _rows(self.sweep_in_process("roster"))}
        self.assertEqual("initiative:shop", rows["storefront"]["initiative"])
        self.assertEqual("initiative:bureau", rows["agent-bureau"]["initiative"])

    def test_no_row_carries_initiative_agent_bureau(self):
        for run in (self.sweep("roster"), self.sweep_in_process("roster")):
            self.assertNotIn("initiative:agent-bureau",
                             [row["initiative"] for row in _rows(run)])


# ---------------------------------------------------------------------------
# count
# ---------------------------------------------------------------------------

class SkippedTest(SweepCase):

    def test_no_design_system_is_skipped_with_the_reason(self):
        _commit(self.repo, {"README.md": "no design system\n"}, "2026-10-20T12:00:00+00:00")
        run = self.count(LISTED, "2026-10-27T12:00:00+00:00")
        self.assertEqual(0, run.code, run.err)
        self.assert_one_line(
            run, f"parts-sweep: {LISTED} — skipped — no design system "
                 f"(no {DECLARATION} and no design/LOCK.json)")
        self.assertEqual({"repo": LISTED, "state": "skipped", "listed": True,
                          "reason": f"no {DECLARATION} and no design/LOCK.json",
                          "source": None, "copies": None, "last_week": None,
                          "last_week_sha": None, "last_week_reason": None},
                         self.record_of())
        self.assertFalse(self.count_out.exists(), "a skipped repo wrote --count-out")

    def test_an_unlisted_skipped_repo_records_listed_false(self):
        _commit(self.repo, {"README.md": "x\n"}, "2026-10-20T12:00:00+00:00")
        run = self.count(UNLISTED, "2026-10-27T12:00:00+00:00")
        self.assertEqual(0, run.code, run.err)
        self.assertEqual(("skipped", False), (self.record_of()["state"],
                                              self.record_of()["listed"]))


class CountedTest(SweepCase):

    def setUp(self):
        super().setUp()
        self.old = _commit(self.repo, _with_copies(2), "2026-10-20T12:00:00+00:00")
        self.new = _commit(self.repo, _with_copies(3), "2026-10-27T12:00:00+00:00")

    def test_three_now_two_a_week_ago(self):
        run = self.count(LISTED, "2026-10-24T00:00:00+00:00")
        self.assertEqual(0, run.code, run.err)
        self.assert_one_line(run, f"parts-sweep: {LISTED} — 3 copy(ies) now, 2 a week ago (+1)")
        self.assertEqual({"repo": LISTED, "state": "counted", "listed": True, "reason": None,
                          "source": DECLARATION, "copies": 3, "last_week": 2,
                          "last_week_sha": self.old, "last_week_reason": None},
                         self.record_of())

    def test_no_worktree_is_left_behind(self):
        before = _worktrees(self.repo)
        self.count(LISTED, "2026-10-24T00:00:00+00:00")
        self.assertEqual(before, _worktrees(self.repo))
        self.assertEqual(1, len(before))
        self.assertEqual("", _git(self.repo, "status", "--porcelain"))
        self.assertEqual(self.new, _git(self.repo, "rev-parse", "HEAD").strip())

    def test_count_out_is_the_unchanged_parts_check_object(self):
        self.count(LISTED, "2026-10-24T00:00:00+00:00")
        printed = subprocess.run(
            [sys.executable, str(PARTS_CHECK), "count", "--repo-root", str(self.repo),
             "--repo", LISTED, "--json"], capture_output=True, text=True, check=True).stdout
        written = json.loads(self.count_out.read_text(encoding="utf-8"))
        self.assertEqual(json.loads(printed), written)
        self.assertEqual(3, len(written["copies"]))

    def test_the_filer_accepts_count_out_and_files_once(self):
        self.count(LISTED, "2026-10-24T00:00:00+00:00")
        board = Board()
        argv = ["file", "--repo", LISTED, "--count-json", str(self.count_out),
                "--agent", LISTED_ENTRY["agent"], "--initiative", LISTED_ENTRY["initiative"]]
        summaries = []
        for _ in range(2):
            err = io.StringIO()
            with patch.object(linear_ops, "create_card", board.create_card), \
                    patch.object(linear_ops, "gql", board.gql), \
                    redirect_stdout(io.StringIO()), redirect_stderr(err):
                self.assertEqual(0, parts_cleanup.main(argv), err.getvalue())
            summaries.append(err.getvalue().strip())
        self.assertEqual([f"parts-cleanup: {LISTED} — 3 copy(ies), 3 filed, 0 already filed",
                          f"parts-cleanup: {LISTED} — 3 copy(ies), 0 filed, 3 already filed"],
                         summaries)
        self.assertEqual(3, len(board.created))

    def test_a_fall_shows_the_minus_sign(self):
        _commit(self.repo, {"web/src/pages/Page3.tsx": None, "web/src/pages/Page2.tsx": None},
                "2026-10-30T12:00:00+00:00")
        run = self.count(LISTED, "2026-10-28T00:00:00+00:00")
        self.assert_one_line(run, f"parts-sweep: {LISTED} — 1 copy(ies) now, 3 a week ago "
                                  f"(−2)")

    def test_the_same_count_is_no_change(self):
        run = self.count(LISTED, "2026-10-28T00:00:00+00:00")
        self.assert_one_line(run, f"parts-sweep: {LISTED} — 3 copy(ies) now, 3 a week ago "
                                  f"(no change)")
        self.assertEqual(self.new, self.record_of()["last_week_sha"])

    def test_no_commit_that_old(self):
        run = self.count(LISTED, "2026-10-01T00:00:00+00:00")
        self.assertEqual(0, run.code, run.err)
        record = self.record_of()
        self.assertEqual(("counted", 3, None, None),
                         (record["state"], record["copies"], record["last_week"],
                          record["last_week_sha"]))
        self.assertTrue(record["last_week_reason"])
        self.assert_one_line(run, f"parts-sweep: {LISTED} — 3 copy(ies) now, not counted a "
                                  f"week ago — {record['last_week_reason']}")
        self.assertTrue(self.count_out.exists())

    def test_a_week_ago_commit_with_no_design_system(self):
        repo = self.base / "late"
        repo.mkdir()
        self.repo = repo
        before = _commit(repo, {"README.md": "x\n"}, "2026-10-20T12:00:00+00:00")
        _commit(repo, _with_copies(3), "2026-10-27T12:00:00+00:00")
        run = self.count(LISTED, "2026-10-24T00:00:00+00:00")
        self.assertEqual(0, run.code, run.err)
        record = self.record_of()
        self.assertEqual((3, None, before), (record["copies"], record["last_week"],
                                             record["last_week_sha"]))
        self.assertIn("no design system", record["last_week_reason"])
        self.assert_one_line(run, f"parts-sweep: {LISTED} — 3 copy(ies) now, not counted a "
                                  f"week ago — {record['last_week_reason']}")
        self.assertEqual(1, len(_worktrees(repo)))

    def test_an_unlisted_repo_is_not_carded_yet(self):
        run = self.count(UNLISTED, "2026-10-24T00:00:00+00:00")
        self.assertEqual(0, run.code, run.err)
        self.assertFalse(self.record_of()["listed"])
        self.assert_one_line(run, f"parts-sweep: {UNLISTED} — 3 copy(ies) now, 2 a week ago "
                                  f"(+1){NOT_CARDED}")

    def test_an_unlisted_repo_with_no_week_ago_count_is_not_carded_yet(self):
        run = self.count(UNLISTED, "2026-10-01T00:00:00+00:00")
        self.assertTrue(run.err_lines[0].endswith(NOT_CARDED), run.err)
        self.assertIn(" — 3 copy(ies) now, not counted a week ago — ", run.err_lines[0])

    def test_a_week_ago_with_no_offset_is_unknown(self):
        run = self.count(LISTED, "2026-10-24T00:00:00")
        self.assertEqual(1, run.code)
        self.assertEqual(1, len(run.err_lines), run.err)
        self.assertTrue(run.err_lines[0].startswith(f"parts-sweep: {LISTED} — UNKNOWN — "))

    def test_an_unreadable_declaration_is_unknown_and_recorded_failed(self):
        _commit(self.repo, {DECLARATION: "{"}, "2026-10-28T12:00:00+00:00")
        run = self.count(LISTED, "2026-10-24T00:00:00+00:00")
        self.assertEqual(1, run.code)
        self.assertEqual(1, len(run.err_lines), run.err)
        self.assertTrue(run.err_lines[0].startswith(f"parts-sweep: {LISTED} — UNKNOWN — "))
        record = self.record_of()
        self.assertEqual(("failed", None, None), (record["state"], record["listed"],
                                                  record["copies"]))
        self.assertTrue(record["reason"])
        self.assertFalse(self.count_out.exists())


class DaylightSavingTest(SweepCase):
    """US daylight time ended on 2026-11-01. A week before 2026-11-03 05:00
    Pacific standard time is 2026-10-27 05:00 Pacific DAYLIGHT time, 12:00
    UTC — an hour earlier than the standard-time offset would make it."""

    WEEK_AGO = "2026-10-27T05:00:00-07:00"

    def test_the_commit_chosen_is_the_newest_at_or_before_the_daylight_instant(self):
        before = _commit(self.repo, _with_copies(2), "2026-10-27T11:30:00+00:00")
        _commit(self.repo, _with_copies(4), "2026-10-27T12:30:00+00:00")
        _commit(self.repo, _with_copies(3), "2026-11-03T13:00:00+00:00")
        run = self.count(LISTED, self.WEEK_AGO)
        self.assertEqual(0, run.code, run.err)
        record = self.record_of()
        # Read at -08:00 the instant is 13:00 UTC, and the 12:30 commit (4) is chosen.
        self.assertEqual((before, 2, 3), (record["last_week_sha"], record["last_week"],
                                          record["copies"]))
        self.assert_one_line(run, f"parts-sweep: {LISTED} — 3 copy(ies) now, 2 a week ago (+1)")

    def test_a_commit_at_the_instant_itself_is_chosen(self):
        _commit(self.repo, _with_copies(2), "2026-10-27T11:30:00+00:00")
        at = _commit(self.repo, _with_copies(1), "2026-10-27T05:00:00-07:00")
        _commit(self.repo, _with_copies(3), "2026-11-03T13:00:00+00:00")
        self.count(LISTED, self.WEEK_AGO)
        self.assertEqual((at, 1), (self.record_of()["last_week_sha"],
                                   self.record_of()["last_week"]))


# ---------------------------------------------------------------------------
# record
# ---------------------------------------------------------------------------

class RecordTest(SweepCase):

    def record(self, slug: str, state: str, reason: str) -> Run:
        return self.sweep("record", "--repo", slug, "--state", state, "--reason", reason,
                          "--json-out", str(self.json_out))

    def assert_record(self, slug: str, state: str, reason: str):
        self.assertEqual({"repo": slug, "state": state, "listed": None, "reason": reason,
                          "source": None, "copies": None, "last_week": None,
                          "last_week_sha": None, "last_week_reason": None},
                         self.record_of())

    def test_unread_exits_zero(self):
        run = self.record("atlas", "unread", UNREAD_REASON)
        self.assertEqual(0, run.code, run.err)
        self.assert_one_line(run, f"parts-sweep: atlas — unread — {UNREAD_REASON}")
        self.assert_record("atlas", "unread", UNREAD_REASON)

    def test_archived_exits_zero(self):
        run = self.record("pause-sandbox", "archived", ARCHIVED_REASON)
        self.assertEqual(0, run.code, run.err)
        self.assert_one_line(run, f"parts-sweep: pause-sandbox — archived — {ARCHIVED_REASON}")
        self.assert_record("pause-sandbox", "archived", ARCHIVED_REASON)

    def test_failed_exits_one(self):
        run = self.record("foundry", "failed", FAILED_REASON)
        self.assertEqual(1, run.code, run.err)
        self.assert_one_line(run, f"parts-sweep: foundry — failed — {FAILED_REASON}")
        self.assert_record("foundry", "failed", FAILED_REASON)

    def test_counted_and_skipped_are_not_recordable(self):
        for state in ("counted", "skipped"):
            run = self.record("atlas", state, "x")
            self.assertNotEqual(0, run.code)
            self.assertFalse(self.json_out.exists())

    def test_an_empty_reason_is_unknown(self):
        run = self.record("atlas", "unread", "  ")
        self.assertEqual(1, run.code)
        self.assertTrue(run.err_lines[0].startswith("parts-sweep: atlas — UNKNOWN — "))


class ContractTest(unittest.TestCase):

    def test_the_tag(self):
        self.assertEqual("parts-sweep:", parts_sweep.TAG)

    def test_the_states(self):
        self.assertEqual(("counted", "skipped", "unread", "archived", "failed"),
                         parts_sweep.STATES)

    def test_standard_library_only(self):
        text = SCRIPT.read_text(encoding="utf-8")
        for module in ("yaml", "requests", "linear_ops"):
            self.assertNotIn(f"import {module}", text)


class DocTest(unittest.TestCase):
    """`docs/parts-sweep.md` carries the contract word for word."""

    WORD_FOR_WORD = (
        "python3 <pipeline>/scripts/parts_sweep.py roster [--repo <slug>]",
        "python3 <pipeline>/scripts/parts_sweep.py count --repo <slug> --repo-root DIR "
        "--week-ago <ISO 8601> \\\n    --json-out FILE --count-out FILE",
        "python3 <pipeline>/scripts/parts_sweep.py record --repo <slug> --state "
        "<unread|archived|failed> \\\n    --reason <text> --json-out FILE",
        '{"repo": "<slug>", "state": "counted", "listed": true, "reason": null,\n'
        ' "source": "<declaration source>", "copies": 3, "last_week": 4,\n'
        ' "last_week_sha": "<sha>", "last_week_reason": null}',
        "the read token could not be minted for <owner>/<name>",
        "archived on GitHub, so it is read-only and not swept",
        "the read token could not be minted for <owner>/<name>, inside this organization",
        "parts-sweep: <slug> — skipped — no design system (<reason>)",
        "parts-sweep: <slug> — unread — <reason>",
        "parts-sweep: <slug> — archived — <reason>",
        "parts-sweep: <slug> — failed — <reason>      (exit 1)",
        "parts-sweep: <slug> — <n> copy(ies) now, <m> a week ago (<change>)",
        "parts-sweep: <slug> — <n> copy(ies) now, not counted a week ago — <reason>",
        "parts-sweep: <slug> — UNKNOWN — <problem>      (exit 1)",
        "`<change>` is `+n`, `−n` or `no change`",
        ", not carded yet — no entry in config/parts-count.json",
        "`validate_card._INITIATIVE_ALIAS`",
        "`initiative:bureau`",
        "git rev-list -1 --before=",
        "git worktree",
    )

    def test_the_doc_carries_the_contract(self):
        text = DOC.read_text(encoding="utf-8")
        for needle in self.WORD_FOR_WORD:
            self.assertIn(needle, text)
        for state in parts_sweep.STATES:
            self.assertIn(f"`{state}`", text)


if __name__ == "__main__":
    unittest.main()
