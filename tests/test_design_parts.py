"""A repo says whether its design system exists, and which parts ship (DRE-8717).

`.github/bureau/design-system.json` names the folders each layer of a repo's
design system lives in. `scripts/design_parts.py` is its one reader, and the
parts catalog is computed from the checkout's files at run time — never from
a stored list. `docs/design-system-declaration.md` is the human contract.

The catalog cases run the real CLI over the fixture repo trees in
`tests/fixtures/design-parts/`, each pinned line for line, stdout and stderr.
Every refusal `check` makes has its own test, and each goes red when the rule
is removed. The reader runs on the runner's own `python3` with nothing
pip-installed, so one test reads its imports and another runs it with
site-packages switched off.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import design_parts  # noqa: E402

SCRIPT = ROOT / "scripts" / "design_parts.py"
DOC = ROOT / "docs" / "design-system-declaration.md"
FIXTURES = ROOT / "tests" / "fixtures" / "design-parts"
DECLARATION = ".github/bureau/design-system.json"

DECLARED_CATALOG = [
    "PARTS CATALOG: 6 part(s) from .github/bureau/design-system.json",
    "- Badge (atoms) — ships at web/src/components/atoms/Badge.vue",
    "- Button (atoms) — ships at web/src/components/atoms/Button.tsx",
    "- Card (atoms) — ships at web/src/components/atoms/Card/index.tsx",
    "- SearchField (molecules) — ships at web/src/components/molecules/SearchField.jsx",
    "- Header (organisms) — ships at web/src/components/organisms/Header.svelte",
    "- PageShell (templates) — ships at web/src/components/templates/PageShell.tsx",
]


def _run(*argv):
    return subprocess.run([sys.executable, str(SCRIPT), *argv],
                          capture_output=True, text=True)


def _catalog(fixture: str):
    """`catalog` over a fixture tree, asserting it exits 0 and prints exactly
    one stderr line; returns the stdout lines and that stderr line."""
    done = _run("catalog", "--repo-root", str(FIXTURES / fixture))
    assert done.returncode == 0, done.stdout + done.stderr
    errors = done.stderr.splitlines()
    assert len(errors) == 1, f"one stderr line, got {errors!r}"
    return done.stdout.splitlines(), errors[0]


def _layers(**over) -> dict:
    parts = {"atoms": [], "molecules": [], "organisms": [], "templates": []}
    parts.update(over)
    return {"parts": {k: v for k, v in parts.items() if v is not None}}


class _Repo:
    """A throwaway repo root holding the declaration a test writes."""

    def __init__(self, tmp: str, declaration=None):
        self.root = Path(tmp)
        self.path = self.root / DECLARATION
        if declaration is not None:
            self.path.parent.mkdir(parents=True)
            text = declaration if isinstance(declaration, str) else json.dumps(declaration)
            self.path.write_text(text, encoding="utf-8")

    def add(self, relative: str, text: str = "x\n") -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# catalog — the declared, none and UNKNOWN forms, pinned exactly
# ---------------------------------------------------------------------------

class CatalogCliTest(unittest.TestCase):

    def test_a_declared_repo_lists_every_part_in_its_four_folders(self):
        out, err = _catalog("declared")
        self.assertEqual(out, DECLARED_CATALOG)
        self.assertEqual(
            err, "design-parts: 6 part(s) from .github/bureau/design-system.json")

    def test_tests_stories_specs_and_index_files_are_left_out(self):
        text = "\n".join(_catalog("declared")[0])
        for left_out in ("Button.test.tsx", "Button.stories.tsx",
                         "SearchField.spec.jsx", "atoms/index.ts",
                         "molecules/index.tsx", "tokens.ts"):
            self.assertNotIn(left_out, text)

    def test_a_subfolder_with_an_index_is_one_part_named_for_the_subfolder(self):
        out, _ = _catalog("declared")
        self.assertIn(
            "- Card (atoms) — ships at web/src/components/atoms/Card/index.tsx", out)
        text = "\n".join(out)
        self.assertNotIn("CardHeader", text, "only direct files are parts")
        self.assertNotIn("helpers", text, "a subfolder with no index is no part")
        self.assertNotIn("App.tsx", text, "a UI file outside the folders is no part")

    def test_a_repo_with_no_declaration_and_no_lock_reads_none(self):
        out, err = _catalog("bare")
        self.assertEqual(out, ["PARTS CATALOG: none — no "
                               ".github/bureau/design-system.json and no design/LOCK.json"])
        self.assertEqual(err, "design-parts: none — no "
                              ".github/bureau/design-system.json and no design/LOCK.json")

    def test_a_lock_and_no_declaration_reads_the_design_folders(self):
        out, err = _catalog("locked")
        self.assertEqual(out, [
            "PARTS CATALOG: 4 part(s) from design/LOCK.json",
            "- Button (atoms) — ships at design/atoms/Button.tsx",
            "- Field (molecules) — ships at design/molecules/Field.tsx",
            "- Nav (organisms) — ships at design/organisms/Nav.tsx",
            "- Master (templates) — ships at design/templates/Master.tsx",
        ])
        self.assertEqual(err, "design-parts: 4 part(s) from design/LOCK.json")

    def test_a_declaration_of_empty_lists_reads_none(self):
        out, err = _catalog("empty-lists")
        self.assertEqual(out, ["PARTS CATALOG: none — "
                               ".github/bureau/design-system.json names no folder"])
        self.assertEqual(err, "design-parts: none — "
                              ".github/bureau/design-system.json names no folder")

    def test_a_declaration_naming_only_absent_folders_reads_none(self):
        out, err = _catalog("absent-folders")
        line = ("none — no folder .github/bureau/design-system.json names "
                "is in the checkout")
        self.assertEqual(out, [f"PARTS CATALOG: {line}"])
        self.assertEqual(err, f"design-parts: {line}")

    def test_an_invalid_declaration_reads_unknown_and_exits_0(self):
        out, err = _catalog("unknown-key")
        line = ("UNKNOWN — .github/bureau/design-system.json carries unknown "
                "key(s) colors (the one key is parts)")
        self.assertEqual(out, [f"PARTS CATALOG: {line}"])
        self.assertEqual(err, f"design-parts: {line}")

    def test_an_unreadable_declaration_reads_unknown_and_exits_0(self):
        out, err = _catalog("malformed")
        self.assertEqual(len(out), 1)
        self.assertRegex(out[0], r"^PARTS CATALOG: UNKNOWN — "
                                 r"\.github/bureau/design-system\.json is not valid JSON: \S")
        self.assertEqual(err, "design-parts: " + out[0][len("PARTS CATALOG: "):])

    def test_a_declaration_path_that_is_not_a_file_reads_unknown(self):
        with TemporaryDirectory() as tmp:
            (Path(tmp) / DECLARATION).mkdir(parents=True)
            done = _run("catalog", "--repo-root", tmp)
        self.assertEqual(done.returncode, 0)
        self.assertRegex(done.stdout, r"^PARTS CATALOG: UNKNOWN — "
                                      r"\.github/bureau/design-system\.json")
        self.assertEqual(len(done.stderr.splitlines()), 1)

    def test_a_present_folder_with_no_part_reads_none(self):
        with TemporaryDirectory() as tmp:
            repo = _Repo(tmp, _layers(atoms=["ui/atoms"]))
            repo.add("ui/atoms/index.ts")
            done = _run("catalog", "--repo-root", tmp)
        self.assertEqual(done.stdout.splitlines(), [
            "PARTS CATALOG: none — no part ships in the folders read from "
            ".github/bureau/design-system.json"])
        self.assertEqual(done.stderr.splitlines(), [
            "design-parts: none — no part ships in the folders read from "
            ".github/bureau/design-system.json"])

    def test_the_default_repo_root_is_the_working_directory(self):
        done = subprocess.run([sys.executable, str(SCRIPT), "catalog"],
                              cwd=FIXTURES / "locked", capture_output=True, text=True)
        self.assertEqual(done.stdout.splitlines()[0],
                         "PARTS CATALOG: 4 part(s) from design/LOCK.json")

    def test_a_folder_path_is_read_normalized(self):
        with TemporaryDirectory() as tmp:
            repo = _Repo(tmp, _layers(atoms=["./ui//atoms/"]))
            repo.add("ui/atoms/Button.tsx")
            done = _run("catalog", "--repo-root", tmp)
        self.assertEqual(done.stdout.splitlines(), [
            "PARTS CATALOG: 1 part(s) from .github/bureau/design-system.json",
            "- Button (atoms) — ships at ui/atoms/Button.tsx"])


# ---------------------------------------------------------------------------
# check — strict, and it names what is wrong
# ---------------------------------------------------------------------------

class CheckTest(unittest.TestCase):

    def reason(self, declaration) -> str:
        """`check`'s refusal for `declaration`, asserting it IS a refusal."""
        with TemporaryDirectory() as tmp:
            _Repo(tmp, declaration)
            done = _run("check", "--repo-root", tmp)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        line = done.stdout.strip()
        self.assertTrue(line.startswith("design-parts: invalid — "), line)
        return line

    def test_a_valid_declaration_is_ok(self):
        done = _run("check", "--repo-root", str(FIXTURES / "declared"))
        self.assertEqual(done.returncode, 0, done.stdout)
        self.assertEqual(done.stdout.strip(), "design-parts: ok — "
                         ".github/bureau/design-system.json declares 4 folder(s)")

    def test_the_empty_lists_starting_file_is_ok(self):
        done = _run("check", "--repo-root", str(FIXTURES / "empty-lists"))
        self.assertEqual(done.returncode, 0, done.stdout)
        self.assertEqual(done.stdout.strip(), "design-parts: ok — "
                         ".github/bureau/design-system.json declares 0 folder(s)")

    def test_no_declaration_is_none_never_an_error(self):
        for fixture in ("bare", "locked"):
            done = _run("check", "--repo-root", str(FIXTURES / fixture))
            self.assertEqual(done.returncode, 0, done.stdout)
            self.assertEqual(done.stdout.strip(), "design-parts: none — "
                             "no .github/bureau/design-system.json")

    def test_an_unknown_top_level_key_is_named(self):
        line = self.reason({**_layers(), "colors": []})
        self.assertIn("unknown key(s) colors", line)

    def test_a_missing_parts_key_is_named(self):
        self.assertIn("`parts`", self.reason({}))

    def test_an_unknown_layer_key_is_named(self):
        line = self.reason(_layers(screens=["design/screens"]))
        self.assertIn("unknown key(s) screens", line)

    def test_a_missing_layer_key_is_named(self):
        line = self.reason(_layers(templates=None))
        self.assertIn("missing layer key(s) templates", line)

    def test_an_absolute_path_is_named(self):
        line = self.reason(_layers(atoms=["/etc/atoms"]))
        self.assertIn("`parts.atoms[0]` /etc/atoms is an absolute path", line)

    def test_a_dotdot_segment_is_named(self):
        line = self.reason(_layers(molecules=["ui", "ui/../../elsewhere"]))
        self.assertIn("`parts.molecules[1]` ui/../../elsewhere has a `..` segment", line)

    def test_a_backslash_path_is_named(self):
        line = self.reason(_layers(atoms=["..\\elsewhere"]))
        self.assertIn("`parts.atoms[0]`", line)
        self.assertIn("`/`", line)

    def test_a_layer_that_is_not_a_list_is_named(self):
        line = self.reason(_layers(organisms="ui/organisms"))
        self.assertIn("`parts.organisms` must be a list", line)

    def test_a_folder_that_is_not_a_non_empty_string_is_named(self):
        for bad in (3, "", "   ", None):
            line = self.reason(_layers(atoms=[bad]))
            self.assertIn("`parts.atoms[0]`", line)

    def test_a_folder_with_a_control_character_is_named(self):
        line = self.reason(_layers(atoms=["ui/atoms\nPARTS CATALOG: 99"]))
        self.assertIn("`parts.atoms[0]` must be one line", line)

    def test_parts_that_is_not_an_object_is_named(self):
        self.assertIn("`parts` must be an object", self.reason({"parts": []}))

    def test_a_file_that_is_not_an_object_is_named(self):
        self.assertIn("one JSON object", self.reason([]))

    def test_unparseable_json_is_named(self):
        self.assertIn("is not valid JSON", self.reason("{not json"))

    def test_the_contract_names_are_the_modules(self):
        self.assertEqual(design_parts.DECLARATION, DECLARATION)
        self.assertEqual(design_parts.LOCK, "design/LOCK.json")
        self.assertEqual(design_parts.LAYERS,
                         ("atoms", "molecules", "organisms", "templates"))
        self.assertEqual(design_parts.UI_EXTENSIONS,
                         (".tsx", ".jsx", ".vue", ".svelte"))
        self.assertEqual(design_parts.TAG, "design-parts:")


# ---------------------------------------------------------------------------
# exists — yes or no, and why
# ---------------------------------------------------------------------------

class ExistsTest(unittest.TestCase):

    def answer(self, root) -> str:
        done = _run("exists", "--repo-root", str(root))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        return done.stdout.strip()

    def test_a_declaration_naming_a_present_folder_exists(self):
        self.assertEqual(self.answer(FIXTURES / "declared"),
                         "yes — .github/bureau/design-system.json")

    def test_a_lock_exists(self):
        self.assertEqual(self.answer(FIXTURES / "locked"), "yes — design/LOCK.json")

    def test_a_lock_beside_an_empty_declaration_still_exists(self):
        with TemporaryDirectory() as tmp:
            repo = _Repo(tmp, _layers())
            repo.add("design/LOCK.json", "{}\n")
            self.assertEqual(self.answer(tmp), "yes — design/LOCK.json")

    def test_a_bare_repo_does_not_exist(self):
        self.assertEqual(self.answer(FIXTURES / "bare"), "no — no "
                         ".github/bureau/design-system.json and no design/LOCK.json")

    def test_empty_lists_and_absent_folders_do_not_exist(self):
        self.assertEqual(self.answer(FIXTURES / "empty-lists"),
                         "no — .github/bureau/design-system.json names no folder")
        self.assertEqual(self.answer(FIXTURES / "absent-folders"),
                         "no — no folder .github/bureau/design-system.json "
                         "names is in the checkout")

    def test_a_named_path_that_is_a_file_is_not_a_present_folder(self):
        with TemporaryDirectory() as tmp:
            repo = _Repo(tmp, _layers(atoms=["ui/atoms"]))
            repo.add("ui/atoms", "not a folder\n")
            self.assertEqual(self.answer(tmp), "no — no folder "
                             ".github/bureau/design-system.json names is in the checkout")

    def test_an_invalid_declaration_is_unknown_and_exits_1(self):
        done = _run("exists", "--repo-root", str(FIXTURES / "unknown-key"))
        self.assertEqual(done.returncode, 1)
        self.assertTrue(done.stdout.startswith("unknown — "), done.stdout)


# ---------------------------------------------------------------------------
# The module — what DRE-8719 imports
# ---------------------------------------------------------------------------

class ModuleTest(unittest.TestCase):

    def test_load_returns_the_declared_folders_and_their_source(self):
        found = design_parts.load(FIXTURES / "declared")
        self.assertEqual(found.source, DECLARATION)
        self.assertEqual(found.folders, {
            "atoms": ("web/src/components/atoms",),
            "molecules": ("web/src/components/molecules",),
            "organisms": ("web/src/components/organisms",),
            "templates": ("web/src/components/templates",),
        })

    def test_load_reads_the_design_folders_off_a_lock(self):
        found = design_parts.load(FIXTURES / "locked")
        self.assertEqual(found.source, "design/LOCK.json")
        self.assertEqual(found.folders, {
            "atoms": ("design/atoms",), "molecules": ("design/molecules",),
            "organisms": ("design/organisms",), "templates": ("design/templates",)})

    def test_a_declaration_wins_over_a_lock(self):
        with TemporaryDirectory() as tmp:
            repo = _Repo(tmp, _layers(atoms=["ui/atoms"]))
            repo.add("design/LOCK.json", "{}\n")
            found = design_parts.load(tmp)
        self.assertEqual(found.source, DECLARATION)
        self.assertEqual(found.folders["atoms"], ("ui/atoms",))
        self.assertEqual(found.folders["templates"], ())

    def test_load_is_none_with_neither(self):
        self.assertIsNone(design_parts.load(FIXTURES / "bare"))

    def test_load_raises_invalid_naming_the_problem(self):
        with self.assertRaisesRegex(design_parts.Invalid, "unknown key"):
            design_parts.load(FIXTURES / "unknown-key")

    def test_catalog_returns_name_layer_and_ships_at(self):
        parts = design_parts.catalog(FIXTURES / "declared")
        self.assertEqual([tuple(p) for p in parts], [
            ("Badge", "atoms", "web/src/components/atoms/Badge.vue"),
            ("Button", "atoms", "web/src/components/atoms/Button.tsx"),
            ("Card", "atoms", "web/src/components/atoms/Card/index.tsx"),
            ("SearchField", "molecules", "web/src/components/molecules/SearchField.jsx"),
            ("Header", "organisms", "web/src/components/organisms/Header.svelte"),
            ("PageShell", "templates", "web/src/components/templates/PageShell.tsx"),
        ])
        self.assertEqual(parts[0].name, "Badge")
        self.assertEqual(parts[0].layer, "atoms")
        self.assertEqual(parts[0].ships_at, "web/src/components/atoms/Badge.vue")

    def test_catalog_is_empty_with_no_design_system(self):
        for fixture in ("bare", "empty-lists", "absent-folders"):
            self.assertEqual(design_parts.catalog(FIXTURES / fixture), [])

    def test_catalog_reads_the_files_at_run_time(self):
        with TemporaryDirectory() as tmp:
            repo = _Repo(tmp, _layers(atoms=["ui/atoms"]))
            repo.add("ui/atoms/Button.tsx")
            self.assertEqual([p.name for p in design_parts.catalog(tmp)], ["Button"])
            repo.add("ui/atoms/Avatar.svelte")
            self.assertEqual([p.name for p in design_parts.catalog(tmp)],
                             ["Avatar", "Button"])

    def test_every_ui_extension_ships_a_part_and_nothing_else_does(self):
        with TemporaryDirectory() as tmp:
            repo = _Repo(tmp, _layers(atoms=["ui"]))
            for name in ("A.tsx", "B.jsx", "C.vue", "D.svelte", "E.ts", "F.js",
                         "G.css", "H.mdx", "I.spec.tsx", "J.test.vue",
                         "K.stories.svelte", "index.jsx"):
                repo.add(f"ui/{name}")
            for folder, index in (("L", "index.vue"), ("M", "index.svelte"),
                                  ("N", "index.ts"), ("O", "main.tsx")):
                repo.add(f"ui/{folder}/{index}")
            names = [p.name for p in design_parts.catalog(tmp)]
        self.assertEqual(names, ["A", "B", "C", "D", "L", "M"])

    def test_a_ui_file_is_one_of_the_ui_extensions(self):
        self.assertTrue(design_parts.is_ui_file("console/web/src/App.tsx"))
        self.assertTrue(design_parts.is_ui_file("a/b/Thing.svelte"))
        self.assertFalse(design_parts.is_ui_file("scripts/design_parts.py"))
        self.assertFalse(design_parts.is_ui_file("web/src/index.ts"))


# ---------------------------------------------------------------------------
# The runner's own python3 — nothing pip-installed
# ---------------------------------------------------------------------------

class ImportsTest(unittest.TestCase):

    def test_it_imports_only_the_standard_library(self):
        tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0 and node.module:
                    names.add(node.module.split(".")[0])
        self.assertEqual({n for n in names if n not in sys.stdlib_module_names}, set())

    def test_it_runs_with_site_packages_switched_off(self):
        done = subprocess.run(
            [sys.executable, "-I", "-S", str(SCRIPT), "catalog",
             "--repo-root", str(FIXTURES / "declared")],
            capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(done.stdout.splitlines(), DECLARED_CATALOG)


# ---------------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------------

def _loose(needle: str) -> re.Pattern:
    return re.compile(r"\s+".join(re.escape(w) for w in needle.split()), re.I)


def _section(text: str, title: str) -> str:
    found = re.search(rf"^## {re.escape(title)}\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    assert found, f"docs/design-system-declaration.md must carry `## {title}`"
    return found.group(1)


def _json_block(section: str) -> dict:
    found = re.search(r"```json\n(.*?)```", section, re.S)
    assert found, "the section must carry a json block"
    return json.loads(found.group(1))


class DocTest(unittest.TestCase):

    def setUp(self):
        self.text = DOC.read_text(encoding="utf-8")

    def assertSays(self, phrase: str, text: str | None = None):
        self.assertRegex(text if text is not None else self.text, _loose(phrase))

    def test_it_carries_the_schema(self):
        section = _section(self.text, "The schema")
        for key in ("`parts`", "`atoms`", "`molecules`", "`organisms`", "`templates`"):
            self.assertIn(key, section)
        self.assertSays("`.github/bureau/design-system.json`")
        self.assertSays("a list may be empty", section)
        self.assertSays("an absolute path", section)
        self.assertSays("a `..` segment", section)
        design_parts.validate(_json_block(section))

    def test_it_states_the_exists_rule(self):
        section = _section(self.text, "When a design system exists")
        self.assertSays("`design/LOCK.json` is present", section)
        self.assertSays("names at least one folder that is present in the checkout", section)
        for folder in ("design/atoms", "design/molecules", "design/organisms",
                       "design/templates"):
            self.assertIn(f"`{folder}`", section)

    def test_it_states_what_counts_as_a_part(self):
        section = _section(self.text, "What counts as a part")
        for ext in design_parts.UI_EXTENSIONS:
            self.assertIn(f"`{ext}`", section)
        for left_out in ("`index.*`", "`.test.`", "`.spec.`", "`.stories.`"):
            self.assertIn(left_out, section)
        self.assertSays("its file stem", section)
        self.assertSays("named after the subfolder", section)
        self.assertSays("`design_parts.UI_EXTENSIONS`")

    def test_it_carries_the_catalog_contract(self):
        for phrase in ("python3 .bureau-pipeline/scripts/design_parts.py catalog",
                       "PARTS CATALOG: <n> part(s) from <source>",
                       "- <Name> (<layer>) — ships at <path>",
                       "PARTS CATALOG: none — <reason>",
                       "PARTS CATALOG: UNKNOWN — <problem>",
                       "design-parts: <n> part(s) from <source>",
                       "design-parts: none — <reason>",
                       "design-parts: UNKNOWN — <problem>",
                       "always exits 0"):
            self.assertIn(phrase, self.text)
        for command in ("check", "catalog", "exists"):
            self.assertSays(f"design_parts.py {command}")

    def test_the_console_example_is_valid(self):
        section = _section(self.text, "A worked example — agent-bureau's console")
        found = design_parts.validate(_json_block(section))
        self.assertEqual(found["atoms"], ("console/web/src/components/atoms",))

    def test_the_scaffold_starting_file_is_the_empty_lists_and_reads_none(self):
        section = _section(self.text, "The scaffold's starting file")
        block = _json_block(section)
        self.assertEqual(block, _layers())
        self.assertEqual(
            json.loads((FIXTURES / "empty-lists" / DECLARATION).read_text()), block)
        self.assertSays("reads `none` until the repo names its folders", section)

    def test_it_names_the_interpreter_and_the_siblings(self):
        self.assertSays("the runner's own `python3`")
        self.assertSays("DRE-8719")
        self.assertSays("DRE-8721")


if __name__ == "__main__":
    unittest.main()
