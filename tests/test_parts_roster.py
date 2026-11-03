"""One reader says where a checked-out repo's design system stands (DRE-8950).

`scripts/parts_roster.py read --repo <slug>` reads one checkout and prints
exactly one `parts-roster:` line: `yes` when `design_parts.exists` says so,
`undeclared` when it says no and a layer folder on disk holds a part file,
`no` when neither, `UNKNOWN` when the declaration cannot be read. The first
three also say whether the repo's count (`config/parts-count.json` in the
pipeline checkout) and its Parts Check stub (a job `uses:` of the reusable)
are on.

Every case builds a real tree in a temporary folder, and one reads the
bureau-pipeline checkout itself.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import parts_roster  # noqa: E402
import proof_reach  # noqa: E402

SCRIPT = ROOT / "scripts" / "parts_roster.py"

DECLARATION = ".github/bureau/design-system.json"
ATOMS = "web/src/components/atoms"
REUSABLE = "dreadnought-foundry/bureau-pipeline/.github/workflows/parts-check.yml"

STUB = f"""name: Parts Check

on:
  pull_request:

permissions:
  contents: read

jobs:
  call:
    uses: {REUSABLE}@stable
    with:
      pipeline_ref: stable
"""


def _write(root: Path, path: str, text: str = "") -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _declare(root: Path, **layers) -> None:
    parts = {layer: list(layers.get(layer, ())) for layer in
             ("atoms", "molecules", "organisms", "templates")}
    _write(root, DECLARATION, json.dumps({"parts": parts}))


def _pipeline(root: Path, *slugs) -> Path:
    _write(root, "config/parts-count.json",
           json.dumps({"repos": [{"repo": slug, "agent": "agent:frontend"}
                                 for slug in slugs]}))
    return root


def _run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True)


class Trees(unittest.TestCase):
    """The four answers, read from fixture trees."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.repo = Path(self._tmp.name) / "repo"
        self.repo.mkdir()
        self.pipeline = _pipeline(Path(self._tmp.name) / "pipeline")

    def tearDown(self):
        self._tmp.cleanup()

    def line(self, slug="portico"):
        return parts_roster.read(slug, self.repo, self.pipeline).line

    def test_a_declaration_naming_a_present_folder_is_yes_with_the_count(self):
        _declare(self.repo, atoms=[ATOMS])
        _write(self.repo, f"{ATOMS}/Button.tsx")
        _write(self.repo, f"{ATOMS}/Badge.vue")
        _write(self.repo, f"{ATOMS}/Button.test.tsx")
        self.assertEqual(
            "parts-roster: portico — yes — .github/bureau/design-system.json, "
            "2 part(s); count off; stub off", self.line())

    def test_a_lock_alone_is_yes_from_the_lock(self):
        _write(self.repo, "design/LOCK.json", "{}")
        _write(self.repo, "design/atoms/Button.tsx")
        self.assertEqual(
            "parts-roster: portico — yes — design/LOCK.json, 1 part(s); count off; stub off",
            self.line())

    def test_a_layer_folder_with_a_part_and_no_declaration_is_undeclared(self):
        _write(self.repo, f"{ATOMS}/Button.tsx")
        self.assertEqual(
            "parts-roster: portico — undeclared — layer folder(s) web/src/components/atoms; "
            "count off; stub off", self.line())

    def test_index_test_and_story_files_are_not_parts(self):
        for name in ("index.ts", "Button.test.tsx", "Button.stories.tsx"):
            _write(self.repo, f"{ATOMS}/{name}")
        self.assertEqual(
            "parts-roster: portico — no — no .github/bureau/design-system.json and no "
            "design/LOCK.json; count off; stub off", self.line())

    def test_a_part_one_folder_down_does_not_make_the_layer_folder(self):
        _write(self.repo, f"{ATOMS}/Button/Button.tsx")
        self.assertEqual("no", parts_roster.read("portico", self.repo, self.pipeline).state)

    def test_no_carries_the_reason_design_parts_gives(self):
        _declare(self.repo, atoms=["web/missing"])
        self.assertEqual(
            "parts-roster: portico — no — no folder .github/bureau/design-system.json "
            "names is in the checkout; count off; stub off", self.line())

    def test_an_invalid_declaration_is_unknown_and_exits_1(self):
        _write(self.repo, DECLARATION, "{not json")
        reading = parts_roster.read("portico", self.repo, self.pipeline)
        self.assertEqual("UNKNOWN", reading.state)
        self.assertTrue(reading.line.startswith(
            "parts-roster: portico — UNKNOWN — .github/bureau/design-system.json is not "
            "valid JSON"), reading.line)
        self.assertNotIn("count", reading.line)
        result = _run("read", "--repo", "portico", "--repo-root", str(self.repo),
                      "--pipeline-root", str(self.pipeline))
        self.assertEqual(1, result.returncode)
        self.assertEqual([reading.line], result.stdout.splitlines())

    def test_a_root_that_is_not_a_directory_is_unknown(self):
        missing = self.repo / "nope"
        result = _run("read", "--repo", "portico", "--repo-root", str(missing),
                      "--pipeline-root", str(self.pipeline))
        self.assertEqual(1, result.returncode)
        self.assertEqual([f"parts-roster: portico — UNKNOWN — {missing} is not a directory"],
                         result.stdout.splitlines())

    def test_every_other_line_exits_0_with_one_line(self):
        _write(self.repo, f"{ATOMS}/Button.tsx")
        result = _run("read", "--repo", "portico", "--repo-root", str(self.repo),
                      "--pipeline-root", str(self.pipeline))
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual([self.line()], result.stdout.splitlines())


class UndeclaredFolders(unittest.TestCase):

    def test_nine_folders_print_eight_sorted_then_one_more(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            folders = [f"pkg{i}/{layer}" for i in range(3)
                       for layer in ("templates", "atoms", "organisms")]
            for folder in folders:
                _write(repo, f"{folder}/Part.tsx")
            _write(repo, "node_modules/lib/atoms/Button.tsx")
            _write(repo, "pkg0/node_modules/lib/molecules/Card.tsx")
            reading = parts_roster.read("portico", repo, _pipeline(Path(tmp) / "pipeline"))
        self.assertEqual("undeclared", reading.state)
        shown = ", ".join(sorted(folders)[:8])
        self.assertEqual(
            f"parts-roster: portico — undeclared — layer folder(s) {shown}, +1 more; "
            f"count off; stub off", reading.line)
        self.assertNotIn("node_modules", reading.line)

    def test_a_folder_under_a_skipped_segment_is_never_named(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            for skipped in ("node_modules", ".git", ".bureau-pipeline", "tests/fixtures"):
                _write(repo, f"{skipped}/web/atoms/Button.tsx")
            reading = parts_roster.read("portico", repo, _pipeline(Path(tmp) / "pipeline"))
        self.assertEqual("no", reading.state, reading.line)


class CountAndStub(unittest.TestCase):

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.repo = Path(self._tmp.name) / "repo"
        self.repo.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def read(self, *listed):
        pipeline = _pipeline(Path(self._tmp.name) / "pipeline", *listed)
        return parts_roster.read("portico", self.repo, pipeline)

    def test_count_follows_the_parts_count_file(self):
        self.assertTrue(self.read("agent-bureau", "portico").line.endswith("; count on; stub off"))
        self.assertTrue(self.read("agent-bureau").line.endswith("; count off; stub off"))

    def test_an_unreadable_count_file_is_unknown(self):
        pipeline = Path(self._tmp.name) / "pipeline"
        _write(pipeline, "config/parts-count.json", "[broken")
        reading = parts_roster.read("portico", self.repo, pipeline)
        self.assertEqual("UNKNOWN", reading.state, reading.line)

    def test_a_job_that_uses_the_reusable_at_stable_turns_the_stub_on(self):
        _write(self.repo, ".github/workflows/parts-check.yml", STUB)
        self.assertTrue(self.read().line.endswith("; count off; stub on"))

    def test_any_workflow_file_name_and_a_yaml_extension_count(self):
        _write(self.repo, ".github/workflows/design.yaml",
               STUB.replace(f"{REUSABLE}@stable", f'"{REUSABLE}@main"  # canary'))
        self.assertTrue(self.read().line.endswith("; stub on"))

    def test_a_comment_or_a_step_name_mentioning_the_file_is_stub_off(self):
        _write(self.repo, ".github/workflows/ci.yml", f"""name: CI
# Parts Check: uses: {REUSABLE}@stable once the stub lands
on: push
jobs:
  build:
    # uses: {REUSABLE}@stable
    runs-on: ubuntu-latest
    steps:
      - name: Run parts-check.yml by hand ({REUSABLE}@stable)
        run: |
          echo "uses: {REUSABLE}@stable"
""")
        self.assertTrue(self.read().line.endswith("; stub off"))

    def test_a_step_uses_is_not_a_job_uses(self):
        _write(self.repo, ".github/workflows/ci.yml", f"""on: push
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: {REUSABLE}@stable
""")
        self.assertTrue(self.read().line.endswith("; stub off"))

    def test_another_reusable_is_stub_off(self):
        _write(self.repo, ".github/workflows/review.yml",
               STUB.replace("parts-check.yml", "qa-review.yml"))
        _write(self.repo, ".github/workflows/local.yml",
               STUB.replace(REUSABLE, "./.github/workflows/parts-check.yml"))
        self.assertTrue(self.read().line.endswith("; stub off"))


class ThisCheckout(unittest.TestCase):

    def test_bureau_pipeline_reads_no(self):
        result = _run("read", "--repo", "bureau-pipeline", "--repo-root", str(ROOT))
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            ["parts-roster: bureau-pipeline — no — no .github/bureau/design-system.json and "
             "no design/LOCK.json; count off; stub off"], result.stdout.splitlines())


class ProofReach(unittest.TestCase):

    def test_the_line_is_kept(self):
        self.assertEqual("parts-roster:", parts_roster.TAG)
        tags = {e["tag"]: e for e in proof_reach.load()["log_lines"]}
        entry = tags["parts-roster:"]
        self.assertEqual(("scripts/parts_roster.py", "TAG"),
                         (entry["printed_by"], entry["constant"]))
        self.assertIn("DRE-8950", entry["when"])
        self.assertEqual([], proof_reach._log_line_problems(ROOT, entry))


if __name__ == "__main__":
    unittest.main()
