"""The duplicate-parts check counts copies from the files and judges a diff (DRE-8841).

`scripts/parts_check.py count` counts every copy of a shared part in a
checkout: a component declared again, under a part's name, in any other UI
file of the repo. `check --diff` judges only what a pull request's diff adds
(DRE-8949): it fails for a copy whose own declaration sits on a line the diff
adds, or whose shared part's file the diff adds, and for a UI file outside
every declared folder in which the diff adds a line declaring a component and
which imports none of the shared parts.

Every case builds a real repo in a temporary folder, commits a base tree, and
takes the pull request's diff from `git diff --cached`, so the check reads the
same shape of diff a review step hands it. The copy is computed from the
checkout's files on every run; no case writes, or reads, a stored list.
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

import parts_check  # noqa: E402
import proof_reach  # noqa: E402

SCRIPT = ROOT / "scripts" / "parts_check.py"
DOC = ROOT / "docs" / "parts-check.md"

DECLARATION = ".github/bureau/design-system.json"
ATOMS = "console/web/src/components/atoms"
MOLECULES = "console/web/src/components/molecules"
BUTTON = f"{ATOMS}/Button.tsx"
NOTICE = f"{MOLECULES}/Notice.tsx"
INITIATIVES = "console/web/src/pages/initiatives/InitiativesPage.tsx"
SETTINGS = "console/web/src/pages/settings/SettingsPage.tsx"
HOME = "console/web/src/pages/home/HomePage.tsx"

DECLARED = json.dumps({"parts": {"atoms": [ATOMS], "molecules": [MOLECULES],
                                 "organisms": [], "templates": []}})

BUTTON_SRC = """\
import React from "react";

export function Button({ label }: { label: string }) {
  return <button>{label}</button>;
}
"""

NOTICE_SRC = """\
import React from "react";

export function Notice({ title, children }: { title: string; children?: React.ReactNode }) {
  const tone = "info";
  return <div role="status" data-tone={tone}>{title}{children}</div>;
}
"""

#: The 2026-10-30 shape: a local `Notice` on the initiatives page.
INITIATIVES_COPY = """\
import React from "react";
import { Button } from "@/components/atoms/Button";

function Notice({ title }: { title: string }) {
  return <p className="notice">{title}</p>;
}

export function InitiativesPage() {
  return <div><Notice title="Drafts" /><Button label="New" /></div>;
}
"""

#: The same page with the copy deleted: it uses the shared `Notice`.
INITIATIVES_SHARED = """\
import React from "react";
import { Button } from "@/components/atoms/Button";
import { Notice } from "@/components/molecules/Notice";

export function InitiativesPage() {
  return <div><Notice title="Drafts" /><Button label="New" /></div>;
}
"""

BASE = {
    DECLARATION: DECLARED,
    BUTTON: BUTTON_SRC,
    NOTICE: NOTICE_SRC,
    "console/web/src/main.ts": "export const main = 1;\n",
}

NO_UI_REASON = "the diff adds or changes no UI file (.tsx, .jsx, .vue, .svelte)"
NO_DESIGN_SYSTEM = ("the repo's design system does not exist (no "
                    ".github/bureau/design-system.json and no design/LOCK.json)")


def _page(imports: str) -> str:
    return f"""\
import React from "react";
{imports}
export function HomePage() {{
  return <main>home</main>;
}}
"""


def _write(root: Path, files: dict) -> None:
    for path, text in files.items():
        target = root / path
        if text is None:
            target.unlink()
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.com",
         "-c", "commit.gpgsign=false", *args],
        check=True, capture_output=True, text=True).stdout


def _repo(root: Path, files: dict) -> Path:
    """A git work tree holding `files`, committed."""
    _write(root, files)
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    return root


def _change(root: Path, files: dict) -> Path:
    """Apply `files` to the work tree and return the path of its diff."""
    _write(root, files)
    _git(root, "add", "-A")
    diff = _git(root, "diff", "--cached", "--no-color", "--no-ext-diff", "HEAD")
    out = root.parent / f"{root.name}.diff"
    out.write_text(diff, encoding="utf-8")
    return out


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True)


def _check(root: Path, diff: Path) -> subprocess.CompletedProcess:
    return _run("check", "--diff", str(diff), "--repo-root", str(root))


def _count(root: Path, *extra: str) -> subprocess.CompletedProcess:
    return _run("count", "--repo-root", str(root), *extra)


def _copies(root: Path) -> list:
    got = _count(root, "--json")
    return json.loads(got.stdout)["copies"]


def _tree(root: Path) -> list:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


def _line(text: str, needle: str) -> int:
    return next(i for i, line in enumerate(text.splitlines(), 1) if needle in line)


class CountFromTheFilesTest(unittest.TestCase):
    """`count` computes the copies from the checkout on every run."""

    def _rises_by_one(self, root: Path) -> None:
        before_files = _tree(root)
        before = _copies(root)
        self.assertEqual(_tree(root), before_files, "count wrote a file")
        _write(root, {INITIATIVES: INITIATIVES_COPY})
        after = _copies(root)
        self.assertEqual(len(before) + 1, len(after))
        self.assertEqual(sorted(before_files + [INITIATIVES]), _tree(root))
        self.assertEqual([{"part": "Notice", "layer": "molecules",
                           "part_path": NOTICE, "part_line": 3,
                           "copy_path": INITIATIVES, "copy_line": 4}],
                         [c for c in after if c not in before])
        got = _check(root, self._plain_diff(root))
        self.assertEqual(0, got.returncode, got.stderr)
        self.assertEqual(sorted(before_files + [INITIATIVES]), _tree(root),
                         "check wrote a file")

    def _plain_diff(self, root: Path) -> Path:
        out = root.parent / "plain.diff"
        out.write_text(f"--- a/{NOTICE}\n+++ b/{NOTICE}\n@@ -4 +4 @@\n"
                       f"-  const tone = \"info\";\n+  const tone = \"warn\";\n",
                       encoding="utf-8")
        return out

    def test_a_directory_tree(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            _write(root, BASE)
            self._rises_by_one(root)

    def test_a_git_work_tree(self):
        with TemporaryDirectory() as tmp:
            root = _repo(Path(tmp) / "repo", BASE)
            self._rises_by_one(root)

    def test_the_script_writes_no_file(self):
        source = SCRIPT.read_text(encoding="utf-8")
        for writer in ("write_text", "write_bytes", "json.dump(", ".mkdir(",
                       "open("):
            self.assertNotIn(writer, source)
        self.assertNotRegex(source, r"(?i)\bbaseline\w*\s*=")

    def test_git_ls_files_in_a_work_tree_and_the_walk_otherwise(self):
        ignored = "console/web/dist/Notice.tsx"
        files = {**BASE, ".gitignore": "dist/\n"}
        with TemporaryDirectory() as tmp:
            root = _repo(Path(tmp) / "repo", files)
            _write(root, {ignored: NOTICE_SRC})
            self.assertEqual([], _copies(root))
            _write(root, {INITIATIVES: INITIATIVES_COPY})
            self.assertEqual([INITIATIVES], [c["copy_path"] for c in _copies(root)])
            plain = Path(tmp) / "plain"
            _write(plain, {**files, ignored: NOTICE_SRC})
            self.assertEqual([ignored], [c["copy_path"] for c in _copies(plain)])

    def test_which_files_are_read(self):
        copy = "function Notice() {\n  return null;\n}\n"
        files = {
            **BASE,
            f"{MOLECULES}/Card.tsx": copy,                       # inside the folders
            "console/web/src/pages/A.jsx": copy,                 # outside them
            "console/web/src/pages/A.test.tsx": copy,
            "console/web/src/pages/A.spec.tsx": copy,
            "console/web/src/pages/A.stories.tsx": copy,
            "node_modules/lib/Notice.tsx": copy,
            ".bureau-pipeline/console/Notice.tsx": copy,
            "console/web/src/pages/notice.ts": copy,             # not a UI file
        }
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            _write(root, files)
            self.assertEqual([f"{MOLECULES}/Card.tsx", "console/web/src/pages/A.jsx"],
                             [c["copy_path"] for c in _copies(root)])


class CountOutputTest(unittest.TestCase):

    def test_two_copies_of_one_part_and_one_of_another(self):
        button_copy = 'import React from "react";\nexport const Button = () => <b />;\n'
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            _write(root, {**BASE, INITIATIVES: INITIATIVES_COPY,
                          SETTINGS: "\n\nexport function Notice() {\n  return null;\n}\n",
                          HOME: button_copy})
            got = _count(root, "--json", "--repo", "agent-bureau")
            self.assertEqual(0, got.returncode, got.stderr)
            self.assertEqual({
                "repo": "agent-bureau", "source": DECLARATION, "reason": None,
                "copies": [
                    {"part": "Button", "layer": "atoms", "part_path": BUTTON,
                     "part_line": 3, "copy_path": HOME, "copy_line": 2},
                    {"part": "Notice", "layer": "molecules", "part_path": NOTICE,
                     "part_line": 3, "copy_path": INITIATIVES, "copy_line": 4},
                    {"part": "Notice", "layer": "molecules", "part_path": NOTICE,
                     "part_line": 3, "copy_path": SETTINGS, "copy_line": 3},
                ]}, json.loads(got.stdout))
            self.assertEqual(["parts-count: agent-bureau — 3 copy(ies) of shared parts "
                              f"from {DECLARATION}"], got.stderr.splitlines())
            plain = _count(root)
            self.assertEqual(0, plain.returncode)
            self.assertEqual([f"parts-count: {root.resolve()} — 3 copy(ies) of shared "
                              f"parts from {DECLARATION}"], plain.stderr.splitlines())
            self.assertEqual([
                f"COPY Button — shared part declared at {BUTTON}:3; copy declared at {HOME}:2",
                f"COPY Notice — shared part declared at {NOTICE}:3; copy declared at "
                f"{INITIATIVES}:4",
                f"COPY Notice — shared part declared at {NOTICE}:3; copy declared at "
                f"{SETTINGS}:3",
            ], plain.stdout.splitlines())

    def test_no_copy_is_a_count_of_zero(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            _write(root, BASE)
            got = _count(root, "--repo", "portico")
            self.assertEqual(0, got.returncode)
            self.assertEqual([f"parts-count: portico — 0 copy(ies) of shared parts from "
                              f"{DECLARATION}"], got.stderr.splitlines())

    def test_a_design_system_that_ships_no_part(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            _write(root, {DECLARATION: DECLARED, f"{ATOMS}/index.ts": "export {};\n"})
            got = _count(root, "--json", "--repo", "portico")
            self.assertEqual(0, got.returncode)
            reason = f"no part ships in the folders read from {DECLARATION}"
            self.assertEqual({"repo": "portico", "source": DECLARATION, "reason": reason,
                              "copies": []}, json.loads(got.stdout))
            self.assertEqual([f"parts-count: portico — none — {reason}"],
                             got.stderr.splitlines())

    def test_an_unreadable_declaration_is_unknown(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            _write(root, {**BASE, DECLARATION: "{not json"})
            got = _count(root, "--repo", "portico")
            self.assertEqual(1, got.returncode)
            [line] = got.stderr.splitlines()
            self.assertTrue(line.startswith(
                "parts-count: portico — UNKNOWN — .github/bureau/design-system.json "
                "is not valid JSON"), line)


class NoDesignSystemTest(unittest.TestCase):

    def test_check_does_not_apply_and_count_is_empty(self):
        files = {INITIATIVES: INITIATIVES_COPY.replace("Notice", "Panel")}
        with TemporaryDirectory() as tmp:
            root = _repo(Path(tmp) / "repo", {"README.md": "x\n"})
            diff = _change(root, files)
            got = _check(root, diff)
            self.assertEqual(0, got.returncode, got.stderr)
            self.assertEqual([f"parts-check: does not apply — {NO_DESIGN_SYSTEM}"],
                             got.stderr.splitlines())
            self.assertEqual("", got.stdout)
            counted = _count(root, "--json")
            self.assertEqual(0, counted.returncode)
            self.assertEqual({"repo": None, "source": None, "reason": NO_DESIGN_SYSTEM,
                              "copies": []}, json.loads(counted.stdout))
            self.assertEqual([f"parts-count: {root.resolve()} — none — {NO_DESIGN_SYSTEM}"],
                             counted.stderr.splitlines())


class ReplayTheLocalNoticeTest(unittest.TestCase):
    """The 2026-10-30 local `Notice`, replayed."""

    def test_the_copy_fails_the_pull_request(self):
        with TemporaryDirectory() as tmp:
            root = _repo(Path(tmp) / "repo", BASE)
            got = _check(root, _change(root, {INITIATIVES: INITIATIVES_COPY}))
            self.assertEqual(1, got.returncode, got.stderr)
            line = _line(INITIATIVES_COPY, "function Notice")
            self.assertEqual([f"parts-check: COPY Notice — shared part declared at "
                              f"{NOTICE}:3; copy declared at {INITIATIVES}:{line}"],
                             got.stdout.splitlines())
            self.assertEqual(["parts-check: fail — 1 copy(ies) added, 0 UI file(s) "
                              "importing no shared part"], got.stderr.splitlines())

    def test_the_same_page_with_the_copy_deleted_passes(self):
        with TemporaryDirectory() as tmp:
            root = _repo(Path(tmp) / "repo", BASE)
            got = _check(root, _change(root, {INITIATIVES: INITIATIVES_SHARED}))
            self.assertEqual(0, got.returncode, got.stdout + got.stderr)
            self.assertEqual(["parts-check: pass — 1 UI file(s) checked, 0 copies added, "
                              "0 UI files importing no shared part"],
                             got.stderr.splitlines())
            self.assertEqual("", got.stdout)


class ImportsNoSharedPartTest(unittest.TestCase):

    def _judge(self, files: dict) -> subprocess.CompletedProcess:
        with TemporaryDirectory() as tmp:
            root = _repo(Path(tmp) / "repo", BASE)
            return _check(root, _change(root, files))

    def test_a_page_importing_no_shared_part_fails(self):
        got = self._judge({HOME: _page('import { useState } from "react";\n')})
        self.assertEqual(1, got.returncode)
        self.assertEqual([f"parts-check: NO-SHARED-PART {HOME} — imports none of the "
                          f"shared parts"], got.stdout.splitlines())
        self.assertEqual(["parts-check: fail — 0 copy(ies) added, 1 UI file(s) "
                          "importing no shared part"], got.stderr.splitlines())

    def test_a_name_through_an_alias_specifier_passes(self):
        got = self._judge({HOME: _page(
            'import { Notice as Banner } from "@/components/molecules";\n')})
        self.assertEqual(0, got.returncode, got.stdout)

    def test_a_multi_line_named_import_passes(self):
        got = self._judge({HOME: _page(
            'import {\n  type Foo,\n  Button,\n} from "~ui/atoms";\n')})
        self.assertEqual(0, got.returncode, got.stdout)

    def test_a_default_import_counts_by_its_local_name(self):
        self.assertEqual(0, self._judge({HOME: _page(
            'import Notice from "@/anything";\n')}).returncode)
        self.assertEqual(1, self._judge({HOME: _page(
            'import Banner from "@/components/molecules/Notice";\n')}).returncode)

    def test_a_relative_specifier_into_a_declared_folder_passes(self):
        got = self._judge({HOME: _page(
            'import Banner from "../../components/molecules/Notice";\n')})
        self.assertEqual(0, got.returncode, got.stdout)
        self.assertEqual(0, self._judge({HOME: _page(
            'import * as ui from "../../components/atoms";\n')}).returncode)

    def test_a_relative_specifier_outside_the_folders_does_not_count(self):
        got = self._judge({HOME: _page('import Banner from "../../components/Banner";\n')})
        self.assertEqual(1, got.returncode)

    def test_a_commented_out_import_does_not_count(self):
        got = self._judge({HOME: _page('// import { Notice } from "@/ui";\n')})
        self.assertEqual(1, got.returncode)

    def test_files_the_rule_does_not_judge(self):
        no_import = _page("")
        for path in (f"{MOLECULES}/Panel.tsx",                 # inside a folder
                     "console/web/src/pages/home/Home.test.tsx",
                     "console/web/src/pages/home/Home.stories.tsx",
                     "console/web/src/pages/home/Home.spec.jsx"):
            self.assertEqual(0, self._judge({path: no_import}).returncode, path)
        helper = 'export const format = (x: string) => x.trim();\n'
        self.assertEqual(0, self._judge({"console/web/src/pages/fmt.tsx": helper})
                         .returncode)

    def test_a_changed_file_is_judged_when_the_diff_adds_a_declaration(self):
        """Moved by DRE-8949's first narrowing: a changed file is judged only
        when the diff adds a line on which a component is declared, so this
        diff now edits the declaration line rather than the body."""
        with TemporaryDirectory() as tmp:
            root = _repo(Path(tmp) / "repo", {**BASE, HOME: _page("")})
            got = _check(root, _change(root, {HOME: _page("").replace(
                "HomePage()", "HomePage(): JSX.Element")}))
            self.assertEqual(1, got.returncode)
            self.assertIn(f"NO-SHARED-PART {HOME}", got.stdout)


class OnlyTheDiffIsJudgedTest(unittest.TestCase):
    """An existing copy fails only the pull request that adds one of its
    declarations."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = _repo(Path(self._tmp.name) / "repo",
                          {**BASE, INITIATIVES: INITIATIVES_COPY})

    def tearDown(self):
        self._tmp.cleanup()

    def test_a_diff_touching_other_files_passes(self):
        got = _check(self.root, _change(self.root, {
            SETTINGS: _page('import { Notice } from "@/components/molecules/Notice";\n'),
            "console/web/src/main.ts": "export const main = 2;\n"}))
        self.assertEqual(0, got.returncode, got.stdout)
        self.assertEqual(["parts-check: pass — 1 UI file(s) checked, 0 copies added, "
                          "0 UI files importing no shared part"], got.stderr.splitlines())

    def test_editing_the_parts_body_passes(self):
        got = _check(self.root, _change(self.root, {
            NOTICE: NOTICE_SRC.replace('"info"', '"warn"')}))
        self.assertEqual(0, got.returncode, got.stdout)

    def test_editing_the_copys_body_passes(self):
        got = _check(self.root, _change(self.root, {
            INITIATIVES: INITIATIVES_COPY.replace('className="notice"', 'className="n"')}))
        self.assertEqual(0, got.returncode, got.stdout)

    def test_adding_the_copys_declaration_line_fails(self):
        got = _check(self.root, _change(self.root, {
            INITIATIVES: INITIATIVES_COPY.replace(
                "function Notice({ title }: { title: string })",
                "function Notice({ title }: { title: string; })")}))
        self.assertEqual(1, got.returncode)
        self.assertIn(f"COPY Notice — shared part declared at {NOTICE}:3; copy declared "
                      f"at {INITIATIVES}:4", got.stdout)

    def test_editing_the_parts_declaration_line_passes(self):
        """Moved by DRE-8949's second narrowing: the part's side counts only
        when the diff adds the part's file, so editing an existing part's
        declaration line no longer fails over the untouched copy."""
        got = _check(self.root, _change(self.root, {
            NOTICE: NOTICE_SRC.replace("export function Notice(", "export function Notice (")}))
        self.assertEqual(0, got.returncode, got.stdout)
        self.assertNotIn("COPY", got.stdout)


APP = "console/web/src/app/App.tsx"

#: A router shell: it declares a component, composes feature code, and has no
#: shared part to import.
APP_SRC = """\
import React from "react";
import { HomePage } from "../pages/home/HomePage";

const routes = [
  { path: "/", element: <HomePage /> },
];

export function App() {
  return <Router routes={routes} />;
}
"""


class OnlyWhatTheDiffAddsTest(unittest.TestCase):
    """The check judges only what a diff adds (DRE-8949): a UI file with no
    shared part fails only when the diff declares a component in it, and a
    part's side counts only when the diff adds the part's file."""

    def _judge(self, base: dict, files: dict) -> subprocess.CompletedProcess:
        with TemporaryDirectory() as tmp:
            root = _repo(Path(tmp) / "repo", {**BASE, **base})
            return _check(root, _change(root, files))

    def test_a_route_line_in_an_existing_shell_passes(self):
        got = self._judge({APP: APP_SRC}, {APP: APP_SRC.replace(
            '  { path: "/", element: <HomePage /> },\n',
            '  { path: "/", element: <HomePage /> },\n'
            '  { path: "/home", element: <HomePage /> },\n')})
        self.assertEqual(0, got.returncode, got.stdout + got.stderr)
        self.assertEqual("", got.stdout)
        self.assertEqual(["parts-check: pass — 1 UI file(s) checked, 0 copies added, "
                          "0 UI files importing no shared part"], got.stderr.splitlines())

    def test_a_component_declared_in_that_shell_fails(self):
        got = self._judge({APP: APP_SRC}, {APP: APP_SRC + (
            "\nfunction StatusBanner() {\n  return <p>status</p>;\n}\n")})
        self.assertEqual(1, got.returncode, got.stderr)
        self.assertEqual([f"parts-check: NO-SHARED-PART {APP} — imports none of the "
                          f"shared parts"], got.stdout.splitlines())
        self.assertEqual(["parts-check: fail — 0 copy(ies) added, 1 UI file(s) "
                          "importing no shared part"], got.stderr.splitlines())

    def test_a_new_file_that_declares_a_component_still_fails(self):
        got = self._judge({}, {APP: APP_SRC})
        self.assertEqual(1, got.returncode, got.stderr)
        self.assertEqual([f"parts-check: NO-SHARED-PART {APP} — imports none of the "
                          f"shared parts"], got.stdout.splitlines())

    def test_editing_the_parts_declaration_line_leaves_the_copy_alone(self):
        got = self._judge({INITIATIVES: INITIATIVES_COPY}, {
            NOTICE: NOTICE_SRC.replace("{ title, children }", "{ title, children = null }")})
        self.assertEqual(0, got.returncode, got.stdout)
        self.assertNotIn("COPY", got.stdout)
        self.assertEqual(["parts-check: pass — 1 UI file(s) checked, 0 copies added, "
                          "0 UI files importing no shared part"], got.stderr.splitlines())

    def test_a_new_part_whose_name_a_local_component_already_uses_fails(self):
        banner = f"{MOLECULES}/Banner.tsx"
        settings = ('import React from "react";\n'
                    'import { Notice } from "@/components/molecules/Notice";\n\n'
                    "function Banner() {\n  return <Notice title=\"b\" />;\n}\n")
        got = self._judge({SETTINGS: settings}, {banner: (
            'import React from "react";\n\n'
            "export function Banner() {\n  return <div role=\"banner\" />;\n}\n")})
        self.assertEqual(1, got.returncode, got.stderr)
        self.assertEqual([f"parts-check: COPY Banner — shared part declared at {banner}:3; "
                          f"copy declared at {SETTINGS}:4"], got.stdout.splitlines())
        self.assertEqual(["parts-check: fail — 1 copy(ies) added, 0 UI file(s) "
                          "importing no shared part"], got.stderr.splitlines())

    def test_a_local_notice_added_to_a_page_still_fails(self):
        got = self._judge({}, {INITIATIVES: INITIATIVES_COPY})
        self.assertEqual(1, got.returncode, got.stderr)
        line = _line(INITIATIVES_COPY, "function Notice")
        self.assertEqual([f"parts-check: COPY Notice — shared part declared at "
                          f"{NOTICE}:3; copy declared at {INITIATIVES}:{line}"],
                         got.stdout.splitlines())


class CheckEdgesTest(unittest.TestCase):

    def test_a_diff_with_no_ui_file_does_not_apply(self):
        with TemporaryDirectory() as tmp:
            root = _repo(Path(tmp) / "repo", BASE)
            got = _check(root, _change(root, {"console/web/src/main.ts": "x\n"}))
            self.assertEqual(0, got.returncode)
            self.assertEqual([f"parts-check: does not apply — {NO_UI_REASON}"],
                             got.stderr.splitlines())

    def test_a_missing_diff_is_unknown(self):
        with TemporaryDirectory() as tmp:
            root = _repo(Path(tmp) / "repo", BASE)
            got = _check(root, Path(tmp) / "absent.diff")
            self.assertEqual(1, got.returncode)
            [line] = got.stderr.splitlines()
            self.assertRegex(line, r"^parts-check: UNKNOWN — the diff file .* is missing$")

    def test_an_unreadable_declaration_is_unknown(self):
        with TemporaryDirectory() as tmp:
            root = _repo(Path(tmp) / "repo", BASE)
            diff = _change(root, {DECLARATION: '{"parts": {}}', HOME: _page("")})
            got = _check(root, diff)
            self.assertEqual(1, got.returncode)
            [line] = got.stderr.splitlines()
            self.assertTrue(line.startswith("parts-check: UNKNOWN — "), line)

    def test_the_tags(self):
        self.assertEqual("parts-check:", parts_check.CHECK_TAG)
        self.assertEqual("parts-count:", parts_check.COUNT_TAG)


class ProofReachTest(unittest.TestCase):

    def test_both_lines_are_kept(self):
        doc = json.loads((ROOT / "config" / "proof-reach.json").read_text(encoding="utf-8"))
        tags = {e["tag"]: e for e in doc["log_lines"]}
        for tag, constant in (("parts-check:", "CHECK_TAG"), ("parts-count:", "COUNT_TAG")):
            entry = tags[tag]
            self.assertEqual(("scripts/parts_check.py", constant),
                             (entry["printed_by"], entry["constant"]))
            self.assertIn("DRE-8841", entry["when"])
            self.assertEqual([], proof_reach._log_line_problems(ROOT, entry))


class DocTest(unittest.TestCase):
    """`docs/parts-check.md` states the contract word for word."""

    LINES = (
        "python3 <pipeline>/scripts/parts_check.py check --diff <unified diff file> "
        "[--repo-root DIR]",
        "python3 <pipeline>/scripts/parts_check.py count [--repo-root DIR] [--repo SLUG] "
        "[--json]",
        "parts-check: pass — <u> UI file(s) checked, 0 copies added, 0 UI files "
        "importing no shared part",
        "parts-check: does not apply — <reason>",
        "parts-check: fail — <k> copy(ies) added, <m> UI file(s) importing no shared part",
        "parts-check: UNKNOWN — <problem>",
        "parts-check: COPY <Part> — shared part declared at <part path>:<line>; copy "
        "declared at <copy path>:<line>",
        "parts-check: NO-SHARED-PART <file> — imports none of the shared parts",
        "parts-count: <who> — <n> copy(ies) of shared parts from <source>",
        "parts-count: <who> — none — <reason>",
        "parts-count: <who> — UNKNOWN — <problem>",
        '{"repo": "<slug or null>", "source": "<declaration source or null>", '
        '"reason": "<why none, or null>",',
        '"copies": [{"part": "Notice", "layer": "molecules", "part_path": "<path>", '
        '"part_line": 3,',
        '"copy_path": "<path>", "copy_line": 41}]}',
    )

    def test_every_contract_line(self):
        text = DOC.read_text(encoding="utf-8")
        flat = " ".join(text.split())
        for line in self.LINES:
            self.assertIn(" ".join(line.split()), flat)
        for heading in ("## The two commands", "## A copy", "## The import rule",
                        "## A copy's identity"):
            self.assertIn(heading, text)
        self.assertIn("(`part`, `copy_path`, `copy_line`)", flat)

    def test_the_documents_state_the_narrowed_rule(self):
        """DRE-8949: rule 9, this page and the script's docstring say the
        check judges only what a pull request adds."""
        standard = " ".join((ROOT / "standards" / "design-system.md")
                            .read_text(encoding="utf-8").split())
        self.assertIn("flags a UI file the pull request adds, or in which it declares a "
                      "component, that imports none of the shared parts", standard)
        self.assertNotIn("any new or changed UI file", standard)
        for text in (" ".join(DOC.read_text(encoding="utf-8").split()),
                     " ".join((parts_check.__doc__ or "").split())):
            self.assertIn("UI file the diff adds, or in which it adds a line declaring "
                          "a component", text)
            self.assertIn("the diff adds the part's file", text)
            self.assertNotIn("adds or changes, outside every declared folder", text)


if __name__ == "__main__":
    unittest.main()
