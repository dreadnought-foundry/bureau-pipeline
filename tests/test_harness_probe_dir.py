"""RED-first tests for the probe directory's name (DRE-2103 follow-up).

Harness run 29795108949 proved bot_pr_flow self-defeating: the probe
record lands in top-level `harness_runs/` in the sandbox, the sandbox's
pyproject relies on setuptools FLAT-LAYOUT AUTO-DISCOVERY, and discovery
counts every top-level valid-identifier directory as a package (PEP 420 —
no __init__.py needed). So the probe commit itself broke the sandbox's
`pip install -e .` — `Multiple top-level packages discovered in a
flat-layout: ['harness_pkg', 'harness_runs']` — CI went red on EVERY
probe PR, and the merge gate correctly waited forever ("2 of 2 check runs
not green — wait"). The proof pair from the live run: the sweep commits
(which only delete probe files) passed CI at 03:10:53; the probe commit
one second later failed.

The fix stays driver-side (the gate behaved exactly as designed): the
probe directory must NOT be a valid Python identifier — `harness-runs` —
so no package discovery can ever mistake it for code, and the sweep must
still clear the LEGACY `harness_runs/` dir (run 29795108949's gate_paths
cleanup died on the token 401 and stranded a probe file there on the
sandbox's real main, holding its CI red until swept).

These tests must FAIL against PROBE_DIR = "harness_runs", and PASS after.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.dirname(__file__))

from harness import framework  # noqa: E402
from harness.scenarios import bot_pr_flow, gate_paths  # noqa: E402


class ProbeDirNameTest(unittest.TestCase):
    def test_probe_dir_is_never_a_python_identifier(self):
        # The load-bearing property: setuptools flat-layout auto-discovery
        # only considers top-level directories whose names are importable
        # identifiers. A non-identifier name can never be "a second
        # package" no matter what the sandbox's pyproject omits.
        self.assertFalse(
            framework.PROBE_DIR.isidentifier(),
            f"PROBE_DIR {framework.PROBE_DIR!r} is a valid Python "
            "identifier — flat-layout discovery will claim it and kill the "
            "sandbox build (run 29795108949)",
        )

    def test_probe_dir_is_a_single_plain_top_level_dir(self):
        # Still one visible top-level directory (not hidden, not nested):
        # the PR body and cleanup story both point a human straight at it.
        self.assertNotIn("/", framework.PROBE_DIR)
        self.assertFalse(framework.PROBE_DIR.startswith("."))

    def test_scenario_probe_paths_ride_the_safe_dir(self):
        for path in (
            bot_pr_flow.probe_path("gha-1-1"),
            gate_paths.probe_path("gha-1-1", "green"),
            gate_paths.base_advance_path("gha-1-1"),
        ):
            self.assertTrue(path.startswith(framework.PROBE_DIR + "/"), path)


class LegacyProbeDirSweepTest(unittest.TestCase):
    def test_sweep_clears_current_and_legacy_probe_dirs(self):
        # The stranded reality on the sandbox's main TODAY:
        # harness_runs/gha-29795108949-2-gate_paths-base-advance.md — left
        # by the 401-dead cleanup, red-CI-poisoning every branch cut from
        # main. The sweep must clear the legacy dir alongside the new one.
        from test_harness_bot_pr_flow import FakeGitHub  # shared fake

        gh = FakeGitHub(default_branch="main")
        gh.files[("main", f"{framework.PROBE_DIR}/crashed-bot_pr_flow.md")] = "stale"
        gh.files[("main", "harness_runs/gha-old-gate_paths-base-advance.md")] = "stale"

        swept = framework.sweep_leftovers(gh, "o/r", log=lambda *_: None)

        self.assertNotIn(
            ("main", f"{framework.PROBE_DIR}/crashed-bot_pr_flow.md"), gh.files
        )
        self.assertNotIn(
            ("main", "harness_runs/gha-old-gate_paths-base-advance.md"), gh.files
        )
        self.assertEqual(swept["files_deleted"], 2)

    def test_legacy_dir_is_swept_but_never_written(self):
        self.assertIn("harness_runs", framework.LEGACY_PROBE_DIRS)
        self.assertNotIn(framework.PROBE_DIR, framework.LEGACY_PROBE_DIRS)


if __name__ == "__main__":
    unittest.main()
