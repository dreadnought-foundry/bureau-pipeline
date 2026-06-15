"""Tests for the visual-QA stage planner (DRE-1481, layer 3b of DRE-1477).

The planner is the SAFE decision core for qa-review.yml's visual stage: given a
PR's card text, body, changed files, and the console screen map, it decides
whether to render screenshots and have the critic compare them to the design —
and which screens. It must NEVER fire (let alone block) when it shouldn't,
because a false block stalls the whole bureau.

These tests pin the three behaviours the card's acceptance criteria require:

  (a) a UI PR with a **Design:** ref → run, with the affected screen selected,
      so the critic gets design+render and can catch a deliberate visual
      regression as a blocking finding (the block is the critic's call, proven
      here by showing the planner DOES hand it the right pair to compare);
  (b) non-UI PRs and PRs with NO **Design:** ref → SKIP cleanly, no run, no
      block;
  (c) a harness/map failure degrades to SKIP (a non-fatal note), never a block.

The screen map is read from the real console harness contract shape
(key/design/sources), so a rename there that breaks pairing would break these
tests too.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import visual_qa_plan as vqp  # noqa: E402


# A faithful slice of console/web/scripts/visual-qa/screens.mjs (DRE-1480).
SCREENS_MJS = """
export const VIEWPORTS = { desktop: { width: 1440, height: 900 } }
export const RENDER_DIR = 'console/design/images/_renders'
export const SCREENS_DIR = 'console/design/images/screens'
export const SCREENS = [
  {
    key: 'board',
    route: '/board',
    viewport: 'desktop',
    design: 'desktop/kanban-planning-board.png',
    sources: ['src/pages/Board.tsx'],
  },
  {
    key: 'agents',
    route: '/agents',
    viewport: 'desktop',
    design: 'desktop/agents-configuration.png',
    sources: ['src/pages/Agents.tsx', 'src/pages/MobileAgents.tsx'],
  },
  {
    key: 'dashboard',
    route: '/',
    viewport: 'desktop',
    design: 'desktop/fleet-landing-page.png',
    sources: ['src/pages/Dashboard.tsx'],
  },
]
"""

BOARD_DESIGN = "console/design/images/screens/desktop/kanban-planning-board.png"
AGENTS_DESIGN = "console/design/images/screens/desktop/agents-configuration.png"


class ParseDesignRefsTest(unittest.TestCase):
    def test_single_ref(self):
        text = f"**Repo:** agent-bureau\n\n**Design:** {BOARD_DESIGN}\n\nbuild it"
        self.assertEqual(vqp.parse_design_refs(text), [BOARD_DESIGN])

    def test_multiple_refs_one_line_comma_separated(self):
        text = f"**Design:** {BOARD_DESIGN}, {AGENTS_DESIGN}"
        self.assertEqual(vqp.parse_design_refs(text), [BOARD_DESIGN, AGENTS_DESIGN])

    def test_ref_inside_markdown_backticks(self):
        text = f"**Design:** `{BOARD_DESIGN}`"
        self.assertEqual(vqp.parse_design_refs(text), [BOARD_DESIGN])

    def test_dedupe_across_card_and_body(self):
        self.assertEqual(
            vqp.parse_design_refs(f"**Design:** {BOARD_DESIGN}", f"**Design:** {BOARD_DESIGN}"),
            [BOARD_DESIGN],
        )

    def test_no_design_line_is_empty(self):
        self.assertEqual(vqp.parse_design_refs("**Repo:** agent-bureau\n\njust backend work"), [])

    def test_design_line_pointing_offsite_yields_no_png(self):
        # A **Design:** line that is a Figma URL / prose names no screen PNG, so
        # there is nothing to screenshot and nothing to block on.
        self.assertEqual(vqp.parse_design_refs("**Design:** see Figma link in the epic"), [])


class ParseScreenMapTest(unittest.TestCase):
    def test_parses_keys_designs_sources(self):
        screens = vqp.parse_screen_map(SCREENS_MJS)
        self.assertEqual([s["key"] for s in screens], ["board", "agents", "dashboard"])
        self.assertEqual(screens[0]["design"], "desktop/kanban-planning-board.png")
        self.assertEqual(screens[1]["sources"], ["src/pages/Agents.tsx", "src/pages/MobileAgents.tsx"])

    def test_unparseable_map_is_empty_not_crash(self):
        self.assertEqual(vqp.parse_screen_map("not javascript at all"), [])
        self.assertEqual(vqp.parse_screen_map(""), [])


class PlanRunPathTest(unittest.TestCase):
    """(a) UI PR + Design ref → run, with the right screen+design pair so the
    critic can catch a regression."""

    def setUp(self):
        self.screens = vqp.parse_screen_map(SCREENS_MJS)

    def test_ui_pr_with_design_ref_runs_on_affected_screen(self):
        card = f"**Repo:** agent-bureau\n\n**Design:** {BOARD_DESIGN}"
        changed = ["console/web/src/pages/Board.tsx"]
        result = vqp.plan(card, "", changed, self.screens)
        self.assertTrue(result["run"])
        self.assertEqual(result["screens"], ["board"])
        self.assertEqual(result["designs"], [BOARD_DESIGN])

    def test_only_the_designed_screen_is_shot_not_every_changed_screen(self):
        # Two screens' sources change, but the card only designs the board. CI
        # time stays bounded: we shoot ONLY the board (the design baseline the
        # card vouches for), not agents.
        card = f"**Design:** {BOARD_DESIGN}"
        changed = ["console/web/src/pages/Board.tsx", "console/web/src/pages/Agents.tsx"]
        result = vqp.plan(card, "", changed, self.screens)
        self.assertTrue(result["run"])
        self.assertEqual(result["screens"], ["board"])

    def test_design_ref_in_pr_body_also_counts(self):
        result = vqp.plan("", f"**Design:** {AGENTS_DESIGN}",
                          ["console/web/src/pages/Agents.tsx"], self.screens)
        self.assertTrue(result["run"])
        self.assertEqual(result["screens"], ["agents"])

    def test_regression_pair_is_well_formed_for_the_critic(self):
        # The "catch" path: the planner hands the critic a (design, render-key)
        # pair. A deliberate wrong-width/gutter render of that key, compared to
        # this exact design PNG, is what the critic flags as blocking. We assert
        # the pair is present and correctly aligned (key ↔ design).
        card = f"**Design:** {BOARD_DESIGN}"
        result = vqp.plan(card, "", ["console/web/src/pages/Board.tsx"], self.screens)
        self.assertEqual(len(result["screens"]), 1)
        self.assertEqual(len(result["designs"]), 1)
        self.assertEqual(result["screens"][0], "board")
        self.assertEqual(result["designs"][0], BOARD_DESIGN)


class PlanSkipPathTest(unittest.TestCase):
    """(b) skip cleanly — no run, no block — on the safe-default paths."""

    def setUp(self):
        self.screens = vqp.parse_screen_map(SCREENS_MJS)

    def test_no_design_ref_skips(self):
        result = vqp.plan("**Repo:** agent-bureau\n\nbackend refactor", "",
                          ["console/web/src/pages/Board.tsx"], self.screens)
        self.assertFalse(result["run"])
        self.assertEqual(result["screens"], [])
        self.assertIn("Design", result["reason"])

    def test_design_ref_but_no_ui_change_skips(self):
        # A card may carry a Design ref while a given PR only touches backend /
        # infra. Nothing UI changed → skip; never block.
        card = f"**Design:** {BOARD_DESIGN}"
        changed = ["console/backend/app/main.py", "cloud/relay/handler.py"]
        result = vqp.plan(card, "", changed, self.screens)
        self.assertFalse(result["run"])
        self.assertEqual(result["screens"], [])

    def test_design_ref_names_unmapped_screen_skips(self):
        # A stray / not-yet-mapped design path is not a reason to block.
        card = "**Design:** console/design/images/screens/desktop/some-unmapped-screen.png"
        changed = ["console/web/src/pages/Board.tsx"]
        result = vqp.plan(card, "", changed, self.screens)
        self.assertFalse(result["run"])

    def test_empty_inputs_skip(self):
        self.assertFalse(vqp.plan("", "", [], self.screens)["run"])


class PlanDegradePathTest(unittest.TestCase):
    """(c) harness/map failure degrades to SKIP — a non-fatal note, no block."""

    def test_unparseable_screen_map_degrades_to_skip(self):
        # We have a Design ref + a UI change but the screen map can't be read.
        # We do NOT guess a screen; we skip (degrade) so the gate cannot block
        # on a stale/broken map.
        card = f"**Design:** {BOARD_DESIGN}"
        changed = ["console/web/src/pages/Board.tsx"]
        result = vqp.plan(card, "", changed, screen_map=[])
        self.assertFalse(result["run"])
        self.assertIn("screen map", result["reason"])


class CliTest(unittest.TestCase):
    """The CLI always exits 0 and emits a JSON plan — a planner crash must not
    wedge the gate (the workflow reads run:true/false, not the exit code)."""

    def _run(self, card="", body="", changed="", mjs=SCREENS_MJS):
        with tempfile.TemporaryDirectory() as td:
            paths = {}
            for name, content in (("card", card), ("body", body),
                                  ("changed", changed), ("mjs", mjs)):
                p = os.path.join(td, name)
                with open(p, "w") as f:
                    f.write(content)
                paths[name] = p
            return subprocess.run(
                [sys.executable,
                 os.path.join(os.path.dirname(__file__), "..", "scripts", "visual_qa_plan.py"),
                 "--card-text-file", paths["card"],
                 "--pr-body-file", paths["body"],
                 "--changed-file", paths["changed"],
                 "--screens-mjs", paths["mjs"]],
                capture_output=True, text=True,
            )

    def test_cli_run_path(self):
        p = self._run(card=f"**Design:** {BOARD_DESIGN}",
                      changed="console/web/src/pages/Board.tsx\n")
        self.assertEqual(p.returncode, 0)
        plan = json.loads(p.stdout)
        self.assertTrue(plan["run"])
        self.assertEqual(plan["screens"], ["board"])

    def test_cli_skip_path_exits_zero(self):
        p = self._run(card="no design here", changed="console/web/src/pages/Board.tsx\n")
        self.assertEqual(p.returncode, 0)
        self.assertFalse(json.loads(p.stdout)["run"])

    def test_cli_missing_files_exit_zero_and_skip(self):
        # No args at all → empty inputs → SKIP, exit 0. The gate degrades safe
        # even if the caller mis-wires the paths.
        p = subprocess.run(
            [sys.executable,
             os.path.join(os.path.dirname(__file__), "..", "scripts", "visual_qa_plan.py")],
            capture_output=True, text=True,
        )
        self.assertEqual(p.returncode, 0)
        self.assertFalse(json.loads(p.stdout)["run"])


if __name__ == "__main__":
    unittest.main()
