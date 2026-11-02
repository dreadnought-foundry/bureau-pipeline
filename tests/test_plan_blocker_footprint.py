"""A plan that chains two sibling cards whose files never meet is sent back,
unless the card's Blocked by line says why (DRE-8688).

On 2026-11-01 epic DRE-8541's last build cards were planned as a strict chain,
DRE-8553 then DRE-8554 then DRE-8555, although their code files do not overlap.
The one file they share is a count ledger, a JSON map sorted by path where each
card lowers or deletes its own entries. DRE-8554's entries sit apart from
DRE-8553's, so their pull requests would have merged cleanly, and DRE-8554
waited about 22 hours anyway. DRE-8555's entries are next to DRE-8553's, so
that pair does conflict and its order is real.

An edge is justified when the two cards share a file that is not a declared
ledger, when they share only ledgers and their lines in one of them touch
(within 1), or when the blocked card's declaring line states a reason
(`because` after the last card id, three words or more, no card id). Any other
edge is one `needless-blocker:` line in the structural file.

The replay fixture carries the three cards' real `Files:` lines, copied as they
read on 2026-11-01, and a 17-line STAND-IN ledger written for this card. The
titles and problem sentences are stand-ins too. The ledger reproduces the shape
of the real case — DRE-8553/DRE-8554 apart, DRE-8554/DRE-8555 apart,
DRE-8553/DRE-8555 touching — and proves nothing about the real file's lines.

Run: python3 -m pytest tests/test_plan_blocker_footprint.py -v
"""

from __future__ import annotations

import copy
import fnmatch
import json
import os
import re
import shlex
import subprocess  # nosec B404 — runs this repo's own script
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import blocker_prose  # noqa: E402
import plan_blocker_footprint  # noqa: E402
import plan_footprint  # noqa: E402

SCRIPT = SCRIPTS / "plan_blocker_footprint.py"
WORKFLOW = ROOT / ".github" / "workflows" / "plan.yml"
FIXTURE = ROOT / "tests" / "fixtures" / "plan-blocker-footprint-dre8541"
LEDGER_PATH = "console/web/src/theme/rawValues.baseline.json"
CONFIG = ROOT / "config" / "ledger-files.json"

CEO = "plan_ceo_steps.py findings"
BLOCKERS = "plan_blocker_footprint.py findings"

CALLER = "agent-bureau"

# The stand-in ledger, exactly as the card writes it.
LEDGER_TEXT = """{
  "src/components/AgentCard.tsx": 3,
  "src/components/AppSidebar.tsx": 7,
  "src/components/Board.tsx": 2,
  "src/components/TopBar.tsx": 5,
  "src/components/Wizard.tsx": 1,
  "src/components/mobile/MobileDashboard.tsx": 12,
  "src/components/mobile/MobileTopBar.tsx": 4,
  "src/components/quickjump/QuickJump.tsx": 6,
  "src/components/ui/badge.tsx": 2,
  "src/components/ui/sheet.tsx": 3,
  "src/hooks/useBoard.ts": 1,
  "src/lib/documentIdentity.ts": 2,
  "src/pages/BoardPage.tsx": 4,
  "src/pages/settings/PlanPage.tsx": 9,
  "src/stores/board.ts": 1
}
"""

# The three `Files:` lines, verbatim from DRE-8553, DRE-8554 and DRE-8555 as
# they read on 2026-11-01.
FILES_8553 = "**Files:** console/web/src/components/mobile/MobileDashboard.tsx, console/web/src/components/mobile/MobileTopBar.tsx, console/web/src/components/mobile/StateChipsFilter.tsx, console/web/src/components/mobile/SubTabStrip.tsx, console/web/src/components/mobile/MoreMenu.tsx, console/web/src/components/mobile/MobileTabBar.tsx, console/web/src/components/mobile/BoardCardItem.tsx, console/web/src/components/mobile/IconRail.tsx, console/web/src/components/quickjump/QuickJump.tsx, console/web/src/components/whatsnew/WhatsNewPanel.tsx, console/web/src/components/whatsnew/WhatsNewButton.tsx, console/web/src/components/mobile/MobileDashboard.test.tsx, console/web/src/components/mobile/MobileDashboard.loading.test.tsx, console/web/src/components/mobile/MobileDashboard.operatorSplit.test.tsx, console/web/src/components/mobile/MobileDashboard.wrap.test.tsx, console/web/src/components/mobile/MobileTopBar.test.tsx, console/web/src/components/mobile/StateChipsFilter.test.tsx, console/web/src/components/mobile/SubTabStrip.test.tsx, console/web/src/components/mobile/MoreMenu.test.tsx, console/web/src/components/mobile/MobileTabBar.test.tsx, console/web/src/components/mobile/BoardCardItem.test.tsx, console/web/src/components/mobile/MobileShell.test.tsx, console/web/src/components/quickjump/QuickJump.test.tsx, console/web/src/components/quickjump/QuickJump.loading.test.tsx, console/web/src/components/whatsnew/WhatsNewPanel.test.tsx, console/web/src/components/whatsnew/WhatsNewButton.test.tsx, console/web/src/theme/rawValues.baseline.json"  # noqa: E501
FILES_8554 = "**Files:** console/web/src/components/AppSidebar.tsx, console/web/src/components/TopBar.tsx, console/web/src/components/AppShell.tsx, console/web/src/components/SidebarDrawer.tsx, console/web/src/components/DemoBanner.tsx, console/web/src/components/ReleaseReloadHost.tsx, console/web/src/components/AgentsSectionTabs.tsx, console/web/src/components/AlertBellBadge.tsx, console/web/src/components/BlockingAlertDialog.tsx, console/web/src/components/sidebar/FavoritesGroup.tsx, console/web/src/components/sidebar/RecentGroup.tsx, console/web/src/components/atoms/CountBadge.tsx, console/web/src/lib/documentIdentity.ts, console/web/src/components/AppSidebar.test.tsx, console/web/src/components/AppSidebar.collapse.test.tsx, console/web/src/components/AppSidebar.accountFooter.test.tsx, console/web/src/components/TopBar.test.tsx, console/web/src/components/TopBar.fit.test.tsx, console/web/src/components/AppShell.test.tsx, console/web/src/components/SidebarDrawer.test.tsx, console/web/src/components/DemoBanner.test.tsx, console/web/src/components/ReleaseReloadHost.test.tsx, console/web/src/components/AgentsSectionTabs.test.tsx, console/web/src/components/BlockingAlertDialog.test.tsx, console/web/src/components/sidebar/FavoritesGroup.test.tsx, console/web/src/components/sidebar/RecentGroup.test.tsx, console/web/src/components/atoms/CountBadge.test.tsx, console/web/src/lib/documentIdentity.test.ts, console/web/src/theme/rawValues.baseline.json"  # noqa: E501
FILES_8555 = "**Files:** console/web/src/components/ui/badge.tsx, console/web/src/components/ui/badge.test.tsx, console/web/src/components/ui/dropdown-menu.tsx, console/web/src/components/ui/sheet.tsx, console/web/src/components/ui/sheet.test.tsx, console/web/src/components/cards/ProjectCard.tsx, console/web/src/components/composed/HealthBadge.tsx, console/web/src/components/composed/ModelChip.tsx, console/web/src/components/composed/ReasonChip.tsx, console/web/src/components/dashboard/PlannerChip.tsx, console/web/src/components/desk/ContextRing.tsx, console/web/src/components/initiatives/HealthChip.tsx, console/web/src/components/initiatives/InitiativeDecisionsPanel.tsx, console/web/src/pages/projectSettings/LifecycleCard.tsx, console/web/src/pages/settings/CreateCustomerPage.tsx, console/web/src/pages/settings/DemoResetPage.tsx, console/web/src/pages/settings/IntegrationsPage.tsx, console/web/src/pages/settings/MembersPage.tsx, console/web/src/pages/settings/NotificationsPage.tsx, console/web/src/pages/settings/OrgSecurityPage.tsx, console/web/src/pages/settings/PlanPage.tsx, console/web/src/pages/settings/ProfilePage.tsx, console/web/src/pages/settings/SecurityPage.tsx, console/web/src/components/cards/ProjectCard.test.tsx, console/web/src/components/composed/HealthBadge.test.tsx, console/web/src/components/composed/ModelChip.test.tsx, console/web/src/components/composed/ReasonChip.test.tsx, console/design/system/src/parts/02-atoms.html, console/design/system/src/parts/08-status.html, console/design/system/src/parts/09-changelog.html, console/design/system/dist/index.html, console/web/src/theme/rawValues.baseline.json"  # noqa: E501
FILES = {"DRE-8553": FILES_8553, "DRE-8554": FILES_8554, "DRE-8555": FILES_8555}

REASON = "it imports the hook that card adds"


def _load(name: str) -> list[dict]:
    return json.loads((FIXTURE / name).read_text(encoding="utf-8"))


class Board:
    """The two board reads, as filed, with the edits a scenario makes."""

    def __init__(self):
        self.cards = _load("children.json")
        self.details = _load("children-detail.json")

    def _both(self, ident: str):
        return ([c for c in self.cards if c["identifier"] == ident]
                + [d for d in self.details if d["identifier"] == ident])

    def edit_body(self, ident: str, old: str, new: str) -> "Board":
        for rec in self._both(ident):
            assert old in rec["body"], (ident, old)
            rec["body"] = rec["body"].replace(old, new)
        return self

    def block(self, ident: str, *blockers: str, line: str | None = None) -> "Board":
        """`ident` is blocked by exactly `blockers`, and its declaring line
        reads `line` (default: the blockers, comma-separated)."""
        for rec in self.details:
            if rec["identifier"] == ident:
                rec["blocked_by"] = list(blockers)
        for rec in self._both(ident):
            body = re.sub(r"\*\*Blocked by:\*\*[^\n]*\n\n", "", rec["body"])
            if blockers or line:
                declared = line or f"**Blocked by:** {', '.join(blockers)}"
                body = body.replace("**Files:**", f"{declared}\n\n**Files:**", 1)
            rec["body"] = body
        return self

    def set(self, ident: str, **fields) -> "Board":
        for rec in self._both(ident):
            for key, value in fields.items():
                if key == "state":
                    if "state" in rec or "parent" in rec:
                        rec["state"] = value
                elif key == "title":
                    if "title" in rec:
                        rec["title"] = value
                else:
                    rec[key] = copy.deepcopy(value)
        return self

    def drop_ledger(self) -> "Board":
        for ident in FILES:
            self.edit_body(ident, ", " + LEDGER_PATH, "")
        return self


class Run:
    """The CLI as the workflow calls it, over scratch files, in `cwd`."""

    def __init__(self, board: Board, cwd: Path | None = None, structural: str = "",
                 stdin: str | None = None, detail: str | None = None,
                 caller: str = CALLER):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.detail = base / "detail.json"
        if detail is None:
            self.detail.write_text(json.dumps(board.details), encoding="utf-8")
        elif detail:
            self.detail.write_text(detail, encoding="utf-8")
        self.structural = base / "structural.txt"
        if structural:
            self.structural.write_text(structural, encoding="utf-8")
        self.note = base / "note.md"
        self.note.write_text("Mechanical findings\n", encoding="utf-8")
        self.done = subprocess.run(  # nosec B603 — this repo's own script
            [sys.executable, str(SCRIPT), "findings",
             "--detail-file", str(self.detail),
             "--caller", caller,
             "--structural-file", str(self.structural),
             "--note-file", str(self.note)],
            input=json.dumps(board.cards) if stdin is None else stdin,
            capture_output=True, text=True, timeout=60,
            cwd=str(cwd or FIXTURE))

    def structural_text(self) -> str:
        return (self.structural.read_text(encoding="utf-8")
                if self.structural.exists() else "")

    def lines(self) -> list[str]:
        return [line for line in self.structural_text().splitlines()
                if line.startswith(plan_blocker_footprint.FINDING_PREFIX)]

    def note_text(self) -> str:
        return self.note.read_text(encoding="utf-8")

    def close(self):
        self.tmp.cleanup()


class CliCase(unittest.TestCase):

    def run_cli(self, board: Board, **kwargs) -> Run:
        run = Run(board, **kwargs)
        self.addCleanup(run.close)
        self.assertEqual(0, run.done.returncode, run.done.stderr)
        return run

    def line_for(self, run: Run, blocked: str, blocker: str) -> str:
        hits = [line for line in run.lines()
                if f"{blocked} is blocked by {blocker}" in line]
        self.assertEqual(1, len(hits), run.structural_text())
        return hits[0]


# --- The fixture is the one on the card -------------------------------------

class TheFixture(unittest.TestCase):

    def test_the_ledger_is_the_cards_block_byte_for_byte(self):
        self.assertEqual(LEDGER_TEXT.encode("utf-8"),
                         (FIXTURE / LEDGER_PATH).read_bytes())
        self.assertEqual(17, len(LEDGER_TEXT.splitlines()))

    def test_each_card_declares_the_real_files_line(self):
        counts = {"DRE-8553": 27, "DRE-8554": 29, "DRE-8555": 32}
        for name in ("children.json", "children-detail.json"):
            for rec in _load(name):
                ident = rec["identifier"]
                self.assertIn(FILES[ident] + "\n", rec["body"], (name, ident))
                files = plan_footprint.declared_files(rec["body"])
                self.assertEqual(counts[ident], len(files), (name, ident))
                self.assertEqual(plan_footprint.declared_files(FILES[ident]), files)

    def test_the_three_lines_share_exactly_the_ledger(self):
        sets = [plan_footprint.declared_files(line) for line in FILES.values()]
        everything = [path for files in sets for path in files]
        shared = {path for path in everything if everything.count(path) > 1}
        self.assertEqual({LEDGER_PATH}, shared)
        self.assertEqual(3, everything.count(LEDGER_PATH))

    def test_the_cards_are_open_agent_bureau_engineer_cards(self):
        for rec in _load("children.json"):
            self.assertEqual("Queued", rec["state"])
            self.assertIn("repo:agent-bureau", rec["labels"])
            self.assertIn("agent:engineer", rec["labels"])

    def test_the_relations_are_the_chain_as_filed(self):
        relations = {d["identifier"]: d["blocked_by"]
                     for d in _load("children-detail.json")}
        self.assertEqual({"DRE-8553": [], "DRE-8554": ["DRE-8553"],
                          "DRE-8555": ["DRE-8554"]}, relations)
        for rec in _load("children-detail.json"):
            self.assertEqual(rec["blocked_by"], blocker_prose.blocker_ids(rec["body"]))


# --- The replay -------------------------------------------------------------

class TheReplayAsFiled(CliCase):

    def test_exactly_two_lines_one_per_link_of_the_chain(self):
        run = self.run_cli(Board())
        self.assertEqual(2, len(run.lines()), run.structural_text())
        self.line_for(run, "DRE-8554", "DRE-8553")
        self.line_for(run, "DRE-8555", "DRE-8554")

    def test_each_line_gives_both_cards_ledger_lines(self):
        run = self.run_cli(Board())
        first = self.line_for(run, "DRE-8554", "DRE-8553")
        self.assertIn(LEDGER_PATH, first)
        self.assertIn("DRE-8554: lines 3, 5, 13", first)
        self.assertIn("DRE-8553: lines 7, 8, 9", first)
        second = self.line_for(run, "DRE-8555", "DRE-8554")
        self.assertIn("DRE-8555: lines 10, 11, 15", second)
        self.assertIn("DRE-8554: lines 3, 5, 13", second)

    def test_the_dre8555_line_names_the_sibling_its_lines_touch(self):
        run = self.run_cli(Board())
        second = self.line_for(run, "DRE-8555", "DRE-8554")
        self.assertIn("DRE-8553", second)
        first = self.line_for(run, "DRE-8554", "DRE-8553")
        self.assertNotIn("DRE-8555", first, "DRE-8554 touches no sibling")

    def test_each_line_ends_with_the_fix(self):
        run = self.run_cli(Board())
        for line in run.lines():
            self.assertIn("drop the relation", line)
            self.assertTrue(line.rstrip(".").endswith("Blocked by line"), line)
            self.assertIn("because <the non-file dependency>", line)

    def test_the_note_counts_two_refused(self):
        run = self.run_cli(Board())
        note = run.note_text()
        self.assertTrue(note.startswith("Mechanical findings\n"), note)
        self.assertIn(plan_blocker_footprint.NOTE_PREFIX + " 2 edge(s) read", note)
        self.assertIn("2 refused", note)
        self.assertIn("shared by design", note)

    def test_the_line_is_a_structural_send_back(self):
        import plan_critic
        import plan_seam_gate
        run = self.run_cli(Board())
        lines = plan_seam_gate.structural_lines(str(run.structural))
        self.assertEqual(2, len(lines))
        result, _ = plan_critic.read_result(plan_seam_gate.rewrite("", lines))
        self.assertEqual(plan_critic.SEND_BACK, result)


class TheReplayReplanned(CliCase):
    """The board as it reads at this revision: DRE-8554 carries no blocker,
    DRE-8555 is blocked by DRE-8553 and by DRE-8527, outside the epic."""

    def board(self) -> Board:
        return Board().block("DRE-8554").block("DRE-8555", "DRE-8553", "DRE-8527")

    def test_the_structural_file_is_empty(self):
        run = self.run_cli(self.board())
        self.assertEqual("", run.structural_text())

    def test_the_note_counts_one_touching_and_one_outside(self):
        run = self.run_cli(self.board())
        note = run.note_text()
        self.assertIn("2 edge(s) read", note)
        self.assertIn("1 by touching ledger lines", note)
        self.assertIn("1 outside the epic", note)
        self.assertIn("0 refused", note)


class TheReplayWithNoLedger(CliCase):

    def test_both_edges_share_no_file(self):
        run = self.run_cli(Board().drop_ledger())
        self.assertEqual(2, len(run.lines()), run.structural_text())
        for blocked, blocker in (("DRE-8554", "DRE-8553"), ("DRE-8555", "DRE-8554")):
            self.assertIn("share no file", self.line_for(run, blocked, blocker))

    def test_a_third_edge_is_refused_too(self):
        board = Board().drop_ledger().block("DRE-8555", "DRE-8554", "DRE-8553")
        run = self.run_cli(board)
        self.assertEqual(3, len(run.lines()), run.structural_text())
        self.assertIn("share no file", self.line_for(run, "DRE-8555", "DRE-8553"))


class AStatedReason(CliCase):

    LINE = f"**Blocked by:** DRE-8553 — because {REASON}"

    def test_it_justifies_the_edge_and_is_quoted(self):
        run = self.run_cli(Board().block("DRE-8554", "DRE-8553", line=self.LINE))
        self.assertEqual(1, len(run.lines()), run.structural_text())
        self.line_for(run, "DRE-8555", "DRE-8554")
        note = run.note_text()
        self.assertIn(REASON, note)
        self.assertIn("1 by a stated reason", note)

    def test_the_line_still_declares_only_the_blocker(self):
        self.assertEqual(["DRE-8553"], blocker_prose.blocker_ids(self.LINE))

    def test_fewer_than_three_words_is_no_reason(self):
        line = "**Blocked by:** DRE-8553 — because it must"
        run = self.run_cli(Board().block("DRE-8554", "DRE-8553", line=line))
        self.line_for(run, "DRE-8554", "DRE-8553")

    def test_a_reason_that_names_a_card_is_no_reason(self):
        for line in ("**Blocked by:** DRE-8553 — because DRE-8553 adds the hook first",
                     "**Blocked by:** DRE-8553 — because it imports the hook from DRE-8553"):
            with self.subTest(line=line):
                run = self.run_cli(Board().block("DRE-8554", "DRE-8553", line=line))
                self.line_for(run, "DRE-8554", "DRE-8553")

    def test_the_reason_reader(self):
        read = plan_blocker_footprint.stated_reason
        self.assertEqual(REASON, read(self.LINE + "\n", "DRE-8553"))
        self.assertIsNone(read(self.LINE + "\n", "DRE-8554"), "names another blocker")
        self.assertIsNone(read("**Blocked by:** DRE-8553\n", "DRE-8553"))
        self.assertIsNone(read("**Blocked by:** DRE-8553 — because it must\n", "DRE-8553"))
        self.assertIsNone(read("We wait on DRE-8553 because it imports the hook\n",
                               "DRE-8553"), "not a declaring line")
        self.assertIsNone(read("**Blocked by:** DRE-8553 — because DRE-1 lands first ok\n",
                               "DRE-8553"))


class ASharedFileThatIsNoLedger(CliCase):

    def test_is_never_refused_whatever_the_ledger_lines(self):
        board = (Board().block("DRE-8555")
                 .edit_body("DRE-8553", "**Files:** ", "**Files:** package.json, ")
                 .edit_body("DRE-8554", "**Files:** ", "**Files:** package.json, "))
        run = self.run_cli(board)
        self.assertEqual([], run.lines(), run.structural_text())
        self.assertIn("1 justified by a shared file", run.note_text())


class SkippedEdges(CliCase):
    """Only DRE-8554 → DRE-8553 is on the board, and its blocker is made a
    card the check does not read. Unskipped, the edge would be refused."""

    def board(self) -> Board:
        return Board().block("DRE-8555")

    def assert_skipped(self, board: Board, kind: str):
        run = self.run_cli(board)
        self.assertEqual([], run.lines(), run.structural_text())
        self.assertIn(f"1 {kind}", run.note_text())
        self.assertIn("0 refused", run.note_text())

    def test_the_control_is_refused(self):
        run = self.run_cli(self.board())
        self.assertEqual(1, len(run.lines()), run.structural_text())

    def test_a_proof_card(self):
        board = self.board().set("DRE-8553", title="PROOF: agent-bureau: the console is token-clean")
        self.assert_skipped(board, "not a build card")

    def test_an_ops_card(self):
        board = self.board().set("DRE-8553", labels=["repo:agent-bureau", "agent:ops"])
        self.assert_skipped(board, "not a build card")

    def test_an_operator_card_wearing_devops(self):
        board = self.board().set("DRE-8553", labels=[
            "repo:agent-bureau", "agent:devops", "needs-human", "no-code"])
        self.assert_skipped(board, "not a build card")

    def test_an_operator_title(self):
        board = self.board().set("DRE-8553", title="[OPERATOR] agent-bureau: rotate the key")
        self.assert_skipped(board, "not a build card")

    def test_a_delivered_child(self):
        for state in ("Done", "Canceled", "Duplicate"):
            with self.subTest(state=state):
                self.assert_skipped(self.board().set("DRE-8553", state=state), "delivered")

    def test_a_card_with_no_files_section(self):
        board = self.board().edit_body("DRE-8553", FILES_8553, "No footprint here.")
        self.assert_skipped(board, "with no Files: section")

    def test_a_card_in_another_repo(self):
        board = self.board().set("DRE-8553", labels=["repo:portico", "agent:engineer"])
        self.assert_skipped(board, "in another repo")

    def test_the_caller_decides_the_repo(self):
        run = self.run_cli(self.board(), caller="portico")
        self.assertEqual([], run.lines(), run.structural_text())
        self.assertIn("1 in another repo", run.note_text())

    def test_a_blocker_outside_the_epic(self):
        self.assert_skipped(Board().block("DRE-8555").block("DRE-8554", "DRE-9999"),
                            "outside the epic")


# --- The last-entry rule ----------------------------------------------------

SMALL_LEDGER = """{
  "src/a.tsx": 1,
  "src/b.tsx": 1,
  "src/c.tsx": 1,
  "src/d.tsx": 1
}
"""


def _small_board(b_file: str) -> Board:
    """A deletes the last key (`src/d.tsx`, line 5); B edits `b_file`; B is
    blocked by A."""
    board = Board().block("DRE-8555").block("DRE-8554", "DRE-8553")
    board.edit_body("DRE-8553", FILES_8553,
                    f"**Files:** console/web/src/d.tsx, {LEDGER_PATH}")
    board.edit_body("DRE-8554", FILES_8554,
                    f"**Files:** console/web/{b_file}, {LEDGER_PATH}")
    return board


class TheLastEntry(CliCase):

    def run_small(self, b_file: str) -> Run:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        ledger = Path(tmp.name) / LEDGER_PATH
        ledger.parent.mkdir(parents=True)
        ledger.write_text(SMALL_LEDGER, encoding="utf-8")
        return self.run_cli(_small_board(b_file), cwd=Path(tmp.name))

    def test_the_key_just_above_touches(self):
        self.assertEqual([], self.run_small("src/c.tsx").lines())

    def test_one_key_between_still_touches_by_the_comma(self):
        self.assertEqual([], self.run_small("src/b.tsx").lines())

    def test_three_lines_above_is_apart(self):
        run = self.run_small("src/a.tsx")
        line = self.line_for(run, "DRE-8554", "DRE-8553")
        self.assertIn("DRE-8553: lines 4, 5", line)
        self.assertIn("DRE-8554: lines 2", line)


# --- The fallback: allow the order, and say so ------------------------------

class ALedgerThatCannotBeRead(CliCase):

    def run_in(self, ledger_text: str | None) -> Run:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        if ledger_text is not None:
            ledger = Path(tmp.name) / LEDGER_PATH
            ledger.parent.mkdir(parents=True)
            ledger.write_text(ledger_text, encoding="utf-8")
        return self.run_cli(Board(), cwd=Path(tmp.name))

    def test_absent_allows_the_order(self):
        run = self.run_in(None)
        self.assertEqual([], run.lines(), run.structural_text())
        note = run.note_text()
        self.assertIn(LEDGER_PATH, note)
        self.assertIn("could not be read", note)
        self.assertIn("0 refused", note)

    def test_not_json_allows_the_order(self):
        run = self.run_in("{ not json\n")
        self.assertEqual([], run.lines(), run.structural_text())
        self.assertIn(LEDGER_PATH, run.note_text())
        self.assertIn("could not be read", run.note_text())

    def test_a_card_whose_files_map_to_no_key_allows_the_order(self):
        board = Board().block("DRE-8555").edit_body(
            "DRE-8554", FILES_8554, f"**Files:** console/web/src/Unlisted.tsx, {LEDGER_PATH}")
        run = self.run_cli(board)
        self.assertEqual([], run.lines(), run.structural_text())
        self.assertIn("no key", run.note_text())


# --- The files the check writes ---------------------------------------------

class TheStructuralFile(CliCase):

    def test_it_is_appended_to_and_never_truncated(self):
        before = "seam: two epics in one plan\n"
        run = self.run_cli(Board(), structural=before)
        text = run.structural_text()
        self.assertTrue(text.startswith(before), text)
        self.assertEqual(2, len(run.lines()), text)

    def test_a_clean_plan_opens_no_file(self):
        run = self.run_cli(Board().block("DRE-8554").block("DRE-8555", "DRE-8553"))
        self.assertFalse(run.structural.exists())

    def test_unreadable_stdin_is_unknown(self):
        run = self.run_cli(Board(), stdin="this is not json")
        self.assertIn(plan_blocker_footprint.NOTE_PREFIX + " UNKNOWN", run.note_text())
        self.assertFalse(run.structural.exists())

    def test_an_unreadable_detail_file_is_unknown(self):
        for detail in ("", "{ not json"):
            with self.subTest(detail=detail):
                run = self.run_cli(Board(), detail=detail)
                self.assertIn(plan_blocker_footprint.NOTE_PREFIX + " UNKNOWN",
                              run.note_text())
                self.assertFalse(run.structural.exists())


# --- The ledger declaration -------------------------------------------------

class TheLedgerDeclaration(unittest.TestCase):

    def test_it_parses_to_lists_of_globs(self):
        doc = json.loads(CONFIG.read_text(encoding="utf-8"))
        self.assertIsInstance(doc, dict)
        for slug, globs in doc.items():
            self.assertIsInstance(globs, list, slug)
            self.assertTrue(globs, slug)
            self.assertTrue(all(isinstance(g, str) and g for g in globs), slug)

    def test_the_agent_bureau_glob_matches_the_ledger(self):
        doc = json.loads(CONFIG.read_text(encoding="utf-8"))
        self.assertTrue(any(fnmatch.fnmatchcase(LEDGER_PATH, g)
                            for g in doc["agent-bureau"]))
        self.assertTrue(plan_blocker_footprint.is_ledger(LEDGER_PATH, CALLER))
        self.assertFalse(plan_blocker_footprint.is_ledger("package.json", CALLER))

    def test_the_config_readme_names_it(self):
        self.assertIn("`ledger-files.json`",
                      (ROOT / "config" / "README.md").read_text(encoding="utf-8"))


class TheWords(unittest.TestCase):

    def test_the_planner_brief_states_the_rule(self):
        text = (ROOT / "briefs" / "planner.md").read_text(encoding="utf-8")
        self.assertIn("because <reason>", text)

    def test_the_engineering_standard_names_the_check(self):
        text = (ROOT / "standards" / "engineering.md").read_text(encoding="utf-8")
        self.assertIn("plan_blocker_footprint.py", text)


# --- Wiring: plan.yml runs it wherever it runs the CEO-step check -----------

def _steps(text: str | None = None) -> list[dict]:
    doc = yaml.safe_load(text if text is not None else WORKFLOW.read_text(encoding="utf-8"))
    return [s for job in (doc.get("jobs") or {}).values()
            for s in (job or {}).get("steps") or [] if isinstance(s, dict)]


def _commands(run: str) -> list[str]:
    return [line.strip() for line in re.sub(r"\\\n\s*", " ", run).splitlines()
            if line.strip()]


def _hits(lines: list[str], needle: str) -> list[int]:
    return [i for i, line in enumerate(lines) if needle in line]


def _flag(line: str, name: str):
    words = shlex.split(line)
    return words[words.index(name) + 1] if name in words else None


def wiring_problems(steps: list[dict]) -> list[str]:
    """Every way a step running the CEO-step check fails to run this one."""
    found = [s for s in steps if CEO in str(s.get("run") or "")]
    if not found:
        return ["no plan.yml step runs the CEO-step check"]
    problems = []
    for step in found:
        name = step.get("name")
        lines = _commands(step["run"])
        ceo, ours, post = (_hits(lines, CEO), _hits(lines, BLOCKERS),
                           _hits(lines, "linear_ops.py comment"))
        if len(ceo) != 1 or len(ours) != 1 or len(post) != 1:
            problems.append(f"{name}: {len(ceo)} CEO-step, {len(ours)} blocker, "
                            f"{len(post)} comment lines")
            continue
        line, ceo_line = lines[ours[0]], lines[ceo[0]]
        for flag in ("--structural-file", "--note-file"):
            if _flag(line, flag) != _flag(ceo_line, flag):
                problems.append(f"{name}: {flag} differs from the CEO-step call")
        if _flag(line, "--caller") != "$REPO_SLUG":
            problems.append(f"{name}: no --caller \"$REPO_SLUG\"")
        if _flag(line, "--detail-file") != "$DETAIL":
            problems.append(f"{name}: no --detail-file \"$DETAIL\"")
        if _flag(line, "--artifact") is not None:
            problems.append(f"{name}: passes --artifact, which the check does not read")
        words = shlex.split(line)
        if "<" not in words or words[words.index("<") + 1] != "$CHILDREN":
            problems.append(f"{name}: children-json is not on stdin")
        if not (step.get("env") or {}).get("REPO_SLUG"):
            problems.append(f"{name}: REPO_SLUG is not in the step's env")
        if not ceo[0] < ours[0] < post[0]:
            problems.append(f"{name}: not after the CEO-step call and before the note")
    return problems


def _blocker_call_span(run: str) -> tuple[int, int]:
    """The character span of this check's call (all its continuation lines)."""
    start = run.index("python3 .bureau-pipeline/scripts/plan_blocker_footprint.py")
    end = run.index("\n", run.index('< "$CHILDREN"', start))
    return start, end


class EveryCeoStepRunsTheBlockerCheck(unittest.TestCase):

    def test_the_workflow_is_wired(self):
        self.assertEqual([], wiring_problems(_steps()))

    def test_the_three_places(self):
        names = [s.get("name") for s in _steps() if BLOCKERS in str(s.get("run") or "")]
        self.assertEqual(3, len(names), names)
        self.assertEqual(names, [s.get("name") for s in _steps()
                                 if CEO in str(s.get("run") or "")])

    def test_the_two_plan_rounds_write_the_structural_file(self):
        structural = [s for s in _steps() if BLOCKERS in str(s.get("run") or "")
                      and _flag(_commands(s["run"])[_hits(_commands(s["run"]), BLOCKERS)[0]],
                                "--structural-file")]
        self.assertEqual(2, len(structural), [s.get("name") for s in structural])

    def test_removing_the_call_from_any_one_step_is_caught(self):
        steps = _steps()
        wired = [i for i, s in enumerate(steps) if BLOCKERS in str(s.get("run") or "")]
        self.assertEqual(3, len(wired))
        for i in wired:
            mutated = copy.deepcopy(steps)
            start, end = _blocker_call_span(mutated[i]["run"])
            mutated[i]["run"] = mutated[i]["run"][:start] + mutated[i]["run"][end:]
            with self.subTest(step=steps[i].get("name")):
                self.assertNotIn(BLOCKERS, mutated[i]["run"])
                self.assertTrue(wiring_problems(mutated))

    def test_dropping_the_caller_from_any_one_step_is_caught(self):
        steps = _steps()
        wired = [i for i, s in enumerate(steps) if BLOCKERS in str(s.get("run") or "")]
        for i in wired:
            mutated = copy.deepcopy(steps)
            start, end = _blocker_call_span(mutated[i]["run"])
            call = mutated[i]["run"][start:end]
            self.assertIn('--caller "$REPO_SLUG"', call)
            dropped = re.sub(r'\s*--caller "\$REPO_SLUG" \\\n', "\n", call)
            self.assertNotIn("--caller", dropped)
            mutated[i]["run"] = mutated[i]["run"][:start] + dropped + mutated[i]["run"][end:]
            with self.subTest(step=steps[i].get("name")):
                self.assertTrue(wiring_problems(mutated))


if __name__ == "__main__":
    unittest.main()
