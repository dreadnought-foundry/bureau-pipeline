"""The gate's fallback literal is a GENERATED region, not a hand edit.

`validate_card._FALLBACK_REPO_MAP` must byte-match `config/repo-map.json`
(test_repo_map_snapshot.py pins them). But the literal had no generated-region
markers, so agent-bureau's onboarding tool — which opens a PR here updating
only the JSON snapshot — could never produce a green PR: every onboarding PR
was born red, training people to ignore red.

These tests make the literal a machine-splice target:

  * the literal sits between ``BEGIN generated repo mirror`` /
    ``END generated repo mirror`` marker lines — the EXACT phrases
    agent-bureau's scripts/sync_repo_mirrors.py splices on, so its onboarding
    tool can later splice this region cross-repo with the same logic;
  * ``scripts/sync_fallback_map.py`` regenerates the region from the snapshot
    byte-for-byte (running it against today's snapshot is a no-op diff); and
  * ``--check`` exits non-zero on a stale region and names the repair command
    (``python3 scripts/sync_fallback_map.py``).
"""

import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALIDATE_CARD = ROOT / "scripts" / "validate_card.py"
SYNC_SCRIPT = ROOT / "scripts" / "sync_fallback_map.py"
SNAPSHOT = ROOT / "config" / "repo-map.json"

BEGIN_MARKER = "BEGIN generated repo mirror"
END_MARKER = "END generated repo mirror"

# The one command that repairs a stale region — the --check failure must name
# it verbatim so the fix is copy-pasteable from the CI log.
REPAIR_COMMAND = "python3 scripts/sync_fallback_map.py"

_TREE_FILES = (
    "scripts/validate_card.py",
    "scripts/sync_fallback_map.py",
    "config/repo-map.json",
)


def _copy_tree(tmp: Path) -> Path:
    """A minimal repo copy the sync script can run against, so the writer mode
    is exercised without ever mutating the real working tree."""
    for rel in _TREE_FILES:
        dest = tmp / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / rel, dest)
    return tmp


def _run_sync(tree: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(tree / "scripts" / "sync_fallback_map.py"), *args],
        capture_output=True,
        text=True,
    )


class MarkerRegionTest(unittest.TestCase):
    def test_fallback_literal_sits_between_generated_region_markers(self):
        # The EXACT marker phrases agent-bureau's sync_repo_mirrors.py splices
        # on — its onboarding tool will splice this region cross-repo, so the
        # phrases must match to the byte.
        source = VALIDATE_CARD.read_text()
        lines = source.splitlines()
        begin = [i for i, ln in enumerate(lines) if BEGIN_MARKER in ln]
        end = [i for i, ln in enumerate(lines) if END_MARKER in ln]
        self.assertEqual(
            len(begin), 1, f"expected exactly one '{BEGIN_MARKER}' marker line")
        self.assertEqual(
            len(end), 1, f"expected exactly one '{END_MARKER}' marker line")
        literal = [
            i for i, ln in enumerate(lines) if ln.startswith("_FALLBACK_REPO_MAP = {")
        ]
        self.assertEqual(len(literal), 1, "expected one _FALLBACK_REPO_MAP literal")
        self.assertLess(
            begin[0], literal[0],
            "_FALLBACK_REPO_MAP must come AFTER the BEGIN marker")
        self.assertLess(
            literal[0], end[0],
            "_FALLBACK_REPO_MAP must come BEFORE the END marker")

    def test_markers_are_python_comments(self):
        # The markers must not change validate_card's runtime behavior — they
        # are comment lines only.
        for line in VALIDATE_CARD.read_text().splitlines():
            if BEGIN_MARKER in line or END_MARKER in line:
                self.assertTrue(
                    line.lstrip().startswith("#"),
                    f"marker line must be a comment: {line!r}")


class SyncScriptTest(unittest.TestCase):
    def test_regeneration_of_todays_snapshot_is_a_noop(self):
        # Byte-for-byte: running the writer against today's snapshot must not
        # change validate_card.py at all — the rendered literal IS the literal.
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            tree = _copy_tree(Path(td))
            before = (tree / "scripts" / "validate_card.py").read_bytes()
            proc = _run_sync(tree)
            self.assertEqual(
                proc.returncode, 0,
                f"sync failed: {proc.stdout}\n{proc.stderr}")
            after = (tree / "scripts" / "validate_card.py").read_bytes()
            self.assertEqual(
                before, after,
                "regenerating from an unchanged snapshot must be a no-op diff")

    def test_check_mode_passes_on_todays_tree(self):
        # The real working tree must be in sync (this is the CI invocation).
        proc = _run_sync(ROOT, "--check")
        self.assertEqual(
            proc.returncode, 0,
            f"--check failed on an in-sync tree: {proc.stdout}\n{proc.stderr}")

    def test_check_mode_fails_on_snapshot_edit_and_names_the_repair_command(self):
        # The born-red-onboarding-PR bug: a snapshot-only edit (exactly what
        # agent-bureau's onboarding tool pushes) must be detected by --check,
        # exit non-zero, and tell the reader the one command that repairs it.
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            tree = _copy_tree(Path(td))
            snap_path = tree / "config" / "repo-map.json"
            snap = json.loads(snap_path.read_text())
            snap["newco"] = "dreadnought-foundry/newco"
            snap_path.write_text(json.dumps(snap, indent=2) + "\n")

            proc = _run_sync(tree, "--check")
            self.assertNotEqual(
                proc.returncode, 0,
                "--check must exit non-zero when the region is stale")
            output = proc.stdout + proc.stderr
            self.assertIn(
                REPAIR_COMMAND, output,
                f"--check failure must name the repair command {REPAIR_COMMAND!r}")

    def test_writer_repairs_a_stale_region(self):
        # After a snapshot edit, one run of the writer brings the literal back
        # into lockstep: --check goes green and the new slug is in the region.
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            tree = _copy_tree(Path(td))
            snap_path = tree / "config" / "repo-map.json"
            snap = json.loads(snap_path.read_text())
            snap["newco"] = "dreadnought-foundry/newco"
            snap_path.write_text(json.dumps(snap, indent=2) + "\n")

            write = _run_sync(tree)
            self.assertEqual(
                write.returncode, 0,
                f"writer failed: {write.stdout}\n{write.stderr}")
            check = _run_sync(tree, "--check")
            self.assertEqual(
                check.returncode, 0,
                f"--check still red after repair: {check.stdout}\n{check.stderr}")
            spliced = (tree / "scripts" / "validate_card.py").read_text()
            self.assertIn('"newco": "dreadnought-foundry/newco",', spliced)


if __name__ == "__main__":
    unittest.main()
