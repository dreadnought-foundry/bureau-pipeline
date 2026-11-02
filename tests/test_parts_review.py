"""A pull request's diff is read against the parts catalog (DRE-8719).

`scripts/parts_review.py review` reads a unified diff beside the checkout's
parts catalog (`scripts/design_parts.py`, DRE-8717) and hands the design
critic grounded candidates: a copy or look-alike of a shared part, named with
its "ships at" file, and a new part with its provisional record line.

The replay cases run the real CLI over the stored diffs and trees in
`tests/fixtures/parts-review/`. Each UI diff is replayed in both shapes a real
review's checkout has: the tree a `pull_request` run holds (the base plus the
diff's files) and `tree-base/` alone, the default-branch tree a
`workflow_dispatch` re-review holds. The two must print the same stdout,
because every component and its props are read from the diff and only the
catalog is read from the checkout. One test proves that by making every read
of a path the diff names fail.
"""

from __future__ import annotations

import ast
import builtins
import io
import pathlib
import re
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import parts_review  # noqa: E402

SCRIPT = ROOT / "scripts" / "parts_review.py"
CATALOG_SCRIPT = ROOT / "scripts" / "design_parts.py"
FIXTURES = ROOT / "tests" / "fixtures" / "parts-review"

NOTICE = "console/web/src/components/molecules/Notice.tsx"
STEPPER = "console/web/src/components/molecules/Stepper.tsx"
BANNER = "console/web/src/components/molecules/StatusBanner.tsx"
INITIATIVES = "console/web/src/pages/initiatives/InitiativesPage.tsx"
SETTINGS = "console/web/src/pages/settings/SettingsPage.tsx"

APPLIES_1 = ("PARTS REVIEW: applies — 1 UI file(s) changed in a repo whose "
             "design system exists")
BASE_CATALOG = [
    "PARTS CATALOG: 1 part(s) from .github/bureau/design-system.json",
    f"- Notice (molecules) — ships at {NOTICE}",
]

COPY_TEXT = (f"uses-the-shared-parts: `Notice` in `{INITIATIVES}` is a copy of "
             f"the shared part `Notice` — use `{NOTICE}` and delete the copy.")
LOOK_ALIKE_TEXT = (f"uses-the-shared-parts: `StatusBanner` in `{SETTINGS}` looks "
                   f"like the shared part `Notice` (shared props: children, "
                   f"onDismiss, title, tone) — use `{NOTICE}` and delete the copy.")
NEW_PART_TEXT = (f"new-part: `Stepper` at `{STEPPER}` — no shared part is near "
                 f"it; approve it as provisional with its `🪴 part-provisional:` "
                 f"line unless rule 4(c) applies.")
NEW_LOOK_ALIKE_TEXT = (f"uses-the-shared-parts: `StatusBanner` in `{BANNER}` looks "
                       f"like the shared part `Notice` (shared props: children, "
                       f"onDismiss, title, tone) — use `{NOTICE}` and delete the copy.")

NO_UI_REASON = "the diff adds or changes no UI file (.tsx, .jsx, .vue, .svelte)"
NO_DESIGN_SYSTEM_REASON = (
    "the repo's design system does not exist (no "
    ".github/bureau/design-system.json and no design/LOCK.json)")

#: Each UI diff and the tree a `pull_request` run holds for it.
REPLAYS = {
    "notice-copy.diff": "tree-notice-copy",
    "status-banner.diff": "tree-status-banner",
    "new-part.diff": "tree-new-part",
    "new-look-alike.diff": "tree-new-look-alike",
}

FINDING = re.compile(
    r"^FINDING (?P<kind>[a-z-]+) \(suggested verdict: (?P<verdict>[A-Z_]+)\) — "
    r"(?P<text>.+)$")


def _run(*argv):
    return subprocess.run([sys.executable, str(SCRIPT), *argv],
                          capture_output=True, text=True)


def _review(diff, tree="tree-base"):
    """`review` over a stored diff and tree, asserting it exits 0 and prints
    exactly one stderr line; returns the stdout lines and that line."""
    diff_path = diff if isinstance(diff, Path) else FIXTURES / diff
    root = tree if isinstance(tree, Path) else FIXTURES / tree
    done = _run("review", "--diff", str(diff_path), "--repo-root", str(root))
    assert done.returncode == 0, done.stdout + done.stderr
    errors = done.stderr.splitlines()
    assert len(errors) == 1, f"one stderr line, got {errors!r}"
    return done.stdout.splitlines(), errors[0]


def _findings(lines):
    """`(kind, verdict, text)` for every finding line."""
    found = []
    for line in lines:
        match = FINDING.match(line)
        if match:
            found.append((match["kind"], match["verdict"], match["text"]))
    return found


def _catalog_stdout(tree):
    done = subprocess.run([sys.executable, str(CATALOG_SCRIPT), "catalog",
                           "--repo-root", str(FIXTURES / tree)],
                          capture_output=True, text=True)
    return done.stdout.splitlines()


def _in_process(*argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = parts_review.main(list(argv))
    return code, out.getvalue().splitlines(), err.getvalue().splitlines()


# ---------------------------------------------------------------------------
# Synthetic repos and diffs for the unit cases
# ---------------------------------------------------------------------------

DECLARATION = (
    '{"parts": {"atoms": ["ui/atoms"], "molecules": ["ui/molecules"], '
    '"organisms": [], "templates": []}}\n')


class _Repo:
    """A throwaway checkout with a declared design system."""

    def __init__(self, tmp: str, declaration: str | None = DECLARATION):
        self.root = Path(tmp) / "repo"
        self.root.mkdir()
        if declaration is not None:
            self.add(".github/bureau/design-system.json", declaration)
        self.diff = Path(tmp) / "pr.diff"

    def add(self, relative: str, text: str = "x\n") -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def review(self, diff_text: str):
        self.diff.write_text(diff_text, encoding="utf-8")
        code, out, err = _in_process("review", "--diff", str(self.diff),
                                     "--repo-root", str(self.root))
        assert code == 0
        assert len(err) == 1, err
        return out, err[0]


def _added(path: str, text: str) -> str:
    lines = text.splitlines()
    return (f"diff --git a/{path} b/{path}\nnew file mode 100644\n"
            f"--- /dev/null\n+++ b/{path}\n@@ -0,0 +1,{len(lines)} @@\n"
            + "".join(f"+{line}\n" for line in lines))


def _changed(path: str, added: str, context: str = "") -> str:
    """A diff that changes `path`: `context` lines kept, `added` lines added."""
    kept = context.splitlines()
    new = added.splitlines()
    body = "".join(f" {line}\n" for line in kept) + "".join(f"+{line}\n" for line in new)
    return (f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
            f"@@ -1,{len(kept)} +1,{len(kept) + len(new)} @@\n" + body)


def _deleted(path: str, text: str) -> str:
    lines = text.splitlines()
    return (f"diff --git a/{path} b/{path}\ndeleted file mode 100644\n"
            f"--- a/{path}\n+++ /dev/null\n@@ -1,{len(lines)} +0,0 @@\n"
            + "".join(f"-{line}\n" for line in lines))


BUTTON = """export interface ButtonProps {
  label: string;
  onPress: () => void;
  disabled?: boolean;
  size?: "sm" | "md";
}

export function Button({ label, onPress, disabled, size }: ButtonProps) {
  return <button onClick={onPress}>{label}</button>;
}
"""


# ---------------------------------------------------------------------------
# The replays — what the acceptance criteria pin
# ---------------------------------------------------------------------------

class ReplayTest(unittest.TestCase):

    def test_a_local_notice_is_a_copy_of_the_shared_part(self):
        out, _ = _review("notice-copy.diff", "tree-notice-copy")
        self.assertEqual(out[0], APPLIES_1)
        self.assertEqual(_findings(out), [("copy", "REQUEST_CHANGES", COPY_TEXT)])
        self.assertEqual(out, [
            APPLIES_1, *BASE_CATALOG,
            f"FINDING copy (suggested verdict: REQUEST_CHANGES) — {COPY_TEXT}"])

    def test_a_status_banner_in_a_page_looks_like_the_notice(self):
        out, _ = _review("status-banner.diff", "tree-status-banner")
        self.assertEqual(out[0], APPLIES_1)
        self.assertEqual(out[1:3], BASE_CATALOG)
        self.assertEqual(_findings(out),
                         [("look-alike", "REQUEST_CHANGES", LOOK_ALIKE_TEXT)])

    def test_a_new_part_far_from_every_shared_part_is_approved_as_provisional(self):
        out, _ = _review("new-part.diff", "tree-new-part")
        self.assertEqual(out[0], APPLIES_1)
        self.assertEqual(_findings(out), [("new-part", "APPROVE", NEW_PART_TEXT)])
        self.assertEqual(out[1:3], BASE_CATALOG, "the catalog before the diff")
        text = "\n".join(out)
        self.assertNotIn("- Stepper", text)
        self.assertNotIn("🎨 design-question:", text)
        self.assertNotIn("REQUEST_CHANGES", text)

    def test_a_new_part_near_the_notice_is_sent_back_naming_it(self):
        out, _ = _review("new-look-alike.diff", "tree-new-look-alike")
        self.assertEqual(out[1:3], BASE_CATALOG)
        self.assertNotIn("- StatusBanner", "\n".join(out))
        self.assertEqual(_findings(out),
                         [("new-part", "REQUEST_CHANGES", NEW_LOOK_ALIKE_TEXT)])

    def test_no_finding_line_ever_raises_a_design_question(self):
        for diff, tree in REPLAYS.items():
            out, _ = _review(diff, tree)
            self.assertNotIn("🎨 design-question:", "\n".join(out), diff)


class DispatchShapeTest(unittest.TestCase):
    """A `workflow_dispatch` re-review checks out the default branch, which
    holds none of the diff's files. The findings must not change."""

    def test_each_diff_prints_the_same_stdout_against_the_base_tree(self):
        for diff, tree in REPLAYS.items():
            with self.subTest(diff=diff):
                own, _ = _review(diff, tree)
                base, _ = _review(diff, "tree-base")
                self.assertEqual(base, own)
                self.assertTrue(_findings(base), "a finding in both shapes")

    def test_the_stepper_is_read_from_the_diff_on_a_tree_without_it(self):
        self.assertFalse((FIXTURES / "tree-base" / STEPPER).exists())
        out, _ = _review("new-part.diff", "tree-base")
        self.assertEqual(out[1:3], BASE_CATALOG)
        self.assertEqual(_findings(out), [("new-part", "APPROVE", NEW_PART_TEXT)])

    def test_the_new_status_banner_names_the_notice_on_a_tree_without_it(self):
        self.assertFalse((FIXTURES / "tree-base" / BANNER).exists())
        out, _ = _review("new-look-alike.diff", "tree-base")
        self.assertEqual(_findings(out),
                         [("new-part", "REQUEST_CHANGES", NEW_LOOK_ALIKE_TEXT)])


class NoChangedFileIsReadTest(unittest.TestCase):
    """Every read of a path the diff names is made to fail: the output is
    unchanged, and no such read was even tried."""

    def _guarded(self, diff, tree):
        named = set(re.findall(r"^(?:---|\+\+\+) [ab]/(\S+)$",
                               (FIXTURES / diff).read_text(encoding="utf-8"), re.M))
        self.assertTrue(named)
        tried, allowed = [], []

        def guard(original):
            def wrapper(target, *args, **kwargs):
                text = str(target).replace("\\", "/")
                if any(text.endswith("/" + rel) for rel in named):
                    tried.append(text)
                    raise OSError(f"read of a path the diff names: {text}")
                allowed.append(text)
                return original(target, *args, **kwargs)
            return wrapper

        patches = [
            mock.patch.object(pathlib.Path, "read_text", guard(pathlib.Path.read_text)),
            mock.patch.object(pathlib.Path, "read_bytes", guard(pathlib.Path.read_bytes)),
            mock.patch.object(pathlib.Path, "open", guard(pathlib.Path.open)),
            mock.patch.object(builtins, "open", guard(builtins.open)),
            mock.patch.object(io, "open", guard(io.open)),
        ]
        for patch in patches:
            patch.start()
        try:
            code, out, err = _in_process("review", "--diff", str(FIXTURES / diff),
                                         "--repo-root", str(FIXTURES / tree))
        finally:
            for patch in reversed(patches):
                patch.stop()
        self.assertEqual(code, 0)
        self.assertEqual(tried, [], "the review read a file the diff names")
        self.assertTrue(any(path.endswith(NOTICE) for path in allowed),
                        "the guard saw the catalog part's read")
        return out, err

    def test_the_notice_copy_review_reads_no_file_the_diff_changes(self):
        expected, _ = _review("notice-copy.diff", "tree-notice-copy")
        out, _ = self._guarded("notice-copy.diff", "tree-notice-copy")
        self.assertEqual(out, expected)

    def test_the_new_part_review_reads_no_file_the_diff_adds(self):
        expected, _ = _review("new-part.diff", "tree-new-part")
        out, _ = self._guarded("new-part.diff", "tree-new-part")
        self.assertEqual(out, expected)


# ---------------------------------------------------------------------------
# does not apply, UNKNOWN, and the stderr line
# ---------------------------------------------------------------------------

class DoesNotApplyTest(unittest.TestCase):

    def test_a_diff_with_no_ui_file_does_not_apply(self):
        out, err = _review("no-ui.diff", "tree-base")
        self.assertEqual(out, [f"PARTS REVIEW: does not apply — {NO_UI_REASON}"])
        self.assertEqual(err, f"parts-review: does not apply — {NO_UI_REASON}")

    def test_any_diff_against_a_repo_with_no_design_system_does_not_apply(self):
        for diff in REPLAYS:
            with self.subTest(diff=diff):
                out, err = _review(diff, "tree-no-design-system")
                self.assertEqual(
                    out, [f"PARTS REVIEW: does not apply — {NO_DESIGN_SYSTEM_REASON}"])
                self.assertEqual(
                    err, f"parts-review: does not apply — {NO_DESIGN_SYSTEM_REASON}")

    def test_a_diff_that_only_deletes_a_ui_file_does_not_apply(self):
        with TemporaryDirectory() as tmp:
            repo = _Repo(tmp)
            repo.add("ui/molecules/Button.tsx", BUTTON)
            out, _ = repo.review(_deleted("web/src/Old.tsx", "export function Old() {}\n"))
        self.assertEqual(out, [f"PARTS REVIEW: does not apply — {NO_UI_REASON}"])


class UnknownTest(unittest.TestCase):

    def assertUnknown(self, diff: Path, reason: str):
        out, err = _review(diff, "tree-base")
        self.assertEqual(out[0], f"PARTS REVIEW: UNKNOWN — {reason}")
        self.assertEqual(out[1:], _catalog_stdout("tree-base"),
                         "followed by the checkout's catalog block")
        self.assertEqual(err, f"parts-review: UNKNOWN — {reason}")

    def test_a_missing_diff_file_is_unknown(self):
        with TemporaryDirectory() as tmp:
            missing = Path(tmp) / "pr.diff"
            self.assertUnknown(missing, f"the diff file {missing} is missing")

    def test_an_empty_diff_file_is_unknown(self):
        with TemporaryDirectory() as tmp:
            empty = Path(tmp) / "pr.diff"
            empty.write_text("\n", encoding="utf-8")
            self.assertUnknown(empty, f"the diff file {empty} is empty")

    def test_a_diff_that_cannot_be_parsed_is_unknown(self):
        cases = {
            "no file header": "this is a pull request description, not a diff\n",
            "a broken hunk header": "--- a/x.tsx\n+++ b/x.tsx\n@@ broken @@\n+x\n",
            "a short hunk": "--- a/x.tsx\n+++ b/x.tsx\n@@ -1,1 +1,3 @@\n+a\n",
            "a hunk before a file": "@@ -1 +1 @@\n-a\n+b\n",
            "--- with no +++": "--- a/x.tsx\nnot a header\n@@ -1 +1 @@\n-a\n+b\n",
        }
        for label, text in cases.items():
            with self.subTest(label), TemporaryDirectory() as tmp:
                bad = Path(tmp) / "pr.diff"
                bad.write_text(text, encoding="utf-8")
                out, err = _review(bad, "tree-base")
                prefix = f"PARTS REVIEW: UNKNOWN — the diff file {bad} cannot be parsed: "
                self.assertTrue(out[0].startswith(prefix), out[0])
                self.assertEqual(out[1:], _catalog_stdout("tree-base"))
                self.assertTrue(err.startswith("parts-review: UNKNOWN — "), err)

    def test_a_diff_that_is_not_text_is_unknown(self):
        with TemporaryDirectory() as tmp:
            bad = Path(tmp) / "pr.diff"
            bad.write_bytes(b"\xff\xfe\x00--- a/x\n")
            out, _ = _review(bad, "tree-base")
        self.assertTrue(out[0].startswith(
            f"PARTS REVIEW: UNKNOWN — the diff file {bad} cannot be read"), out[0])

    def test_a_declaration_that_cannot_be_read_is_unknown_with_its_catalog(self):
        with TemporaryDirectory() as tmp:
            repo = _Repo(tmp, '{"parts": {"widgets": []}}\n')
            out, err = repo.review(_added("web/src/A.tsx", "export function A() {}\n"))
        self.assertTrue(out[0].startswith(
            "PARTS REVIEW: UNKNOWN — the design system's declaration cannot be read: "),
            out[0])
        self.assertTrue(out[1].startswith("PARTS CATALOG: UNKNOWN — "), out[1])
        self.assertTrue(err.startswith("parts-review: UNKNOWN — "))


class StderrLineTest(unittest.TestCase):

    def test_the_notice_copy_replay_names_its_one_finding(self):
        _, err = _review("notice-copy.diff", "tree-notice-copy")
        self.assertEqual(err, "parts-review: applies — 1 UI file(s) changed in a "
                              "repo whose design system exists, 1 finding(s)")

    def test_no_ui_names_why_it_does_not_apply(self):
        _, err = _review("no-ui.diff", "tree-base")
        self.assertEqual(err, f"parts-review: does not apply — {NO_UI_REASON}")

    def test_a_missing_diff_names_the_unknown(self):
        with TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nope.diff"
            _, err = _review(missing, "tree-base")
        self.assertEqual(err, f"parts-review: UNKNOWN — the diff file {missing} is missing")

    def test_every_replay_prints_one_stderr_line_echoing_stdout(self):
        for diff, tree in {**REPLAYS, "no-ui.diff": "tree-base"}.items():
            with self.subTest(diff=diff):
                out, err = _review(diff, tree)
                echoed = f"parts-review: {out[0][len('PARTS REVIEW: '):]}"
                if out[0].startswith("PARTS REVIEW: applies"):
                    echoed += f", {len(_findings(out))} finding(s)"
                self.assertEqual(err, echoed)


# ---------------------------------------------------------------------------
# The catalog before the diff
# ---------------------------------------------------------------------------

class CatalogBeforeTheDiffTest(unittest.TestCase):

    def test_with_nothing_added_the_block_is_the_catalog_command_s_own(self):
        out, _ = _review("status-banner.diff", "tree-status-banner")
        self.assertEqual(out[1:3], _catalog_stdout("tree-status-banner"))

    def test_a_part_the_diff_adds_is_taken_out_and_the_count_drops(self):
        self.assertEqual(_catalog_stdout("tree-new-part")[0],
                         "PARTS CATALOG: 2 part(s) from .github/bureau/design-system.json")
        out, _ = _review("new-part.diff", "tree-new-part")
        self.assertEqual(out[1], BASE_CATALOG[0])

    def test_a_subfolder_part_whose_index_the_diff_adds_is_taken_out(self):
        index = "export function Card({ title, body }: { title: string; body: string }) {}\n"
        with TemporaryDirectory() as tmp:
            repo = _Repo(tmp)
            repo.add("ui/molecules/Button.tsx", BUTTON)
            repo.add("ui/atoms/Card/index.tsx", index)
            out, _ = repo.review(_added("ui/atoms/Card/index.tsx", index))
        self.assertEqual(out[1:3], [
            "PARTS CATALOG: 1 part(s) from .github/bureau/design-system.json",
            "- Button (molecules) — ships at ui/molecules/Button.tsx"])
        self.assertEqual([f[0] for f in _findings(out)], ["new-part"])
        self.assertIn("`Card` at `ui/atoms/Card/index.tsx`", out[-1])

    def test_with_every_part_taken_out_the_block_says_none(self):
        with TemporaryDirectory() as tmp:
            repo = _Repo(tmp)
            repo.add("ui/molecules/Button.tsx", BUTTON)
            out, _ = repo.review(_added("ui/molecules/Button.tsx", BUTTON))
        self.assertEqual(out[1], "PARTS CATALOG: none — no part ships in the "
                                 "folders read from .github/bureau/design-system.json")


# ---------------------------------------------------------------------------
# Components, props and classification
# ---------------------------------------------------------------------------

class ComponentsTest(unittest.TestCase):

    def test_every_declaration_form_is_recognized(self):
        source = "\n".join([
            "function Alpha(props) {}",
            "export function Bravo(props) {}",
            "export default function Charlie(props) {}",
            "const Delta = (props) => null;",
            "const Echo = forwardRef((props, ref) => null);",
            "export const Foxtrot = React.forwardRef((props, ref) => null);",
            "function lowercase(props) {}",
            "const URL_BASE = (x);",
            "const helper = (x) => x;",
        ]) + "\n"
        with TemporaryDirectory() as tmp:
            repo = _Repo(tmp)
            repo.add("ui/molecules/Button.tsx", BUTTON)
            out, _ = repo.review(_added("web/src/Page.tsx", source))
        names = [re.search(r"`(\w+)`", text).group(1) for _, _, text in _findings(out)]
        self.assertEqual(names, ["Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot"])

    def test_a_declaration_on_a_context_line_is_not_a_finding(self):
        with TemporaryDirectory() as tmp:
            repo = _Repo(tmp)
            repo.add("ui/molecules/Button.tsx", BUTTON)
            out, _ = repo.review(_changed(
                "web/src/Page.tsx", "  const x = 1;\n",
                context="export function Button({ label, onPress }) {\n"))
        self.assertEqual(_findings(out), [])

    def test_a_component_outside_the_folders_matching_nothing_is_a_local_component(self):
        with TemporaryDirectory() as tmp:
            repo = _Repo(tmp)
            repo.add("ui/molecules/Button.tsx", BUTTON)
            out, _ = repo.review(_added(
                "web/src/Page.tsx", "function Chart({ series, axis }) {}\n"))
        self.assertEqual(_findings(out), [(
            "local-component", "NOTE",
            "local-component: `Chart` in `web/src/Page.tsx` is declared outside "
            "every design-system folder and is near no shared part — the critic "
            "judges whether it belongs in the shared parts; never blocking by itself.")])

    def test_a_component_inside_a_folder_that_is_no_new_part_gets_no_finding(self):
        with TemporaryDirectory() as tmp:
            repo = _Repo(tmp)
            repo.add("ui/molecules/Button.tsx", BUTTON)
            out, _ = repo.review(_changed(
                "ui/molecules/Button.tsx", "function Spinner({ size, tone }) {}\n"))
        self.assertEqual(_findings(out), [])

    def test_an_added_part_file_with_no_recognized_declaration_is_still_a_new_part(self):
        with TemporaryDirectory() as tmp:
            repo = _Repo(tmp)
            repo.add("ui/molecules/Button.tsx", BUTTON)
            out, _ = repo.review(_added("ui/atoms/Chip.vue", "<template><span/></template>\n"))
        self.assertEqual(_findings(out), [(
            "new-part", "APPROVE",
            "new-part: `Chip` at `ui/atoms/Chip.vue` — no shared part is near it; "
            "approve it as provisional with its `🪴 part-provisional:` line unless "
            "rule 4(c) applies.")])


class PropsTest(unittest.TestCase):

    def _look(self, page: str, part: str = BUTTON, part_path="ui/molecules/Button.tsx"):
        with TemporaryDirectory() as tmp:
            repo = _Repo(tmp)
            repo.add(part_path, part)
            out, _ = repo.review(_added("web/src/Page.tsx", page))
        return _findings(out)

    def test_props_come_from_an_interface(self):
        found = self._look("interface CtaProps {\n  label: string;\n  onPress(): void;\n"
                           "  disabled?: boolean;\n}\nfunction Cta(p: CtaProps) {}\n")
        self.assertEqual(found[0][0], "look-alike")
        self.assertIn("(shared props: disabled, label, onPress)", found[0][2])

    def test_props_come_from_a_type(self):
        found = self._look("type CtaProps = {\n  readonly label: string;\n"
                           "  onPress: (e: { x: number; y: number }) => void;\n"
                           "  size?: Record<string, number>;\n} & Base;\n"
                           "const Cta = (p: CtaProps) => null;\n")
        self.assertEqual(found[0][0], "look-alike")
        self.assertIn("(shared props: label, onPress, size)", found[0][2])

    def test_props_come_from_destructured_parameters_when_no_type_is_shown(self):
        found = self._look("export const Cta = forwardRef(({ label, onPress = noop, "
                           "disabled: off, ...rest }, ref) => null);\n")
        self.assertEqual(found[0][0], "look-alike")
        self.assertIn("(shared props: disabled, label, onPress)", found[0][2])

    def test_props_are_read_from_the_context_lines_of_the_same_file(self):
        with TemporaryDirectory() as tmp:
            repo = _Repo(tmp)
            repo.add("ui/molecules/Button.tsx", BUTTON)
            out, _ = repo.review(_changed(
                "web/src/Page.tsx", "function Cta(p: CtaProps) {}\n",
                context="interface CtaProps {\n  label: string;\n  onPress: () => void;\n"
                        "  size?: string;\n}\n"))
        self.assertEqual(_findings(out)[0][0], "look-alike")

    def test_with_no_props_shown_a_component_is_never_a_look_alike(self):
        self.assertEqual([f[0] for f in self._look("function Cta(props) {}\n")],
                         ["local-component"])

    def test_with_no_props_shown_a_component_can_still_be_a_copy_by_name(self):
        self.assertEqual([f[0] for f in self._look("function Button(props) {}\n")],
                         ["copy"])

    def test_a_part_s_props_come_from_its_destructured_parameters(self):
        part = "export function Button({ label, onPress, size }) {}\n"
        found = self._look("function Cta({ label, onPress, size, tone }) {}\n", part)
        self.assertEqual(found[0][0], "look-alike")

    def test_a_part_file_that_cannot_be_read_gives_it_no_props(self):
        with TemporaryDirectory() as tmp:
            repo = _Repo(tmp)
            repo.add("ui/molecules/Button.tsx", BUTTON)
            (repo.root / "ui/molecules/Button.tsx").write_bytes(b"\xff\xfe\xfa")
            out, _ = repo.review(_added(
                "web/src/Page.tsx", "function Cta({ label, onPress, size }) {}\n"))
        self.assertEqual([f[0] for f in _findings(out)], ["local-component"])


class SimilarityTest(unittest.TestCase):

    def _kind(self, props: str):
        with TemporaryDirectory() as tmp:
            repo = _Repo(tmp)
            repo.add("ui/molecules/Button.tsx", BUTTON)
            out, _ = repo.review(_added("web/src/Page.tsx", f"function Cta({{ {props} }}) {{}}\n"))
        return _findings(out)[0]

    def test_three_of_four_shared_is_a_look_alike(self):
        # {label, onPress, disabled} vs {label, onPress, disabled, size}: 3/4 = 0.75
        self.assertEqual(self._kind("label, onPress, disabled")[0], "look-alike")

    def test_exactly_0_6_is_a_look_alike(self):
        # {label, onPress, disabled, tone} vs the four: 3/5 = 0.6
        self.assertEqual(self._kind("label, onPress, disabled, tone")[0], "look-alike")

    def test_below_0_6_is_not(self):
        # {label, onPress, tone} vs the four: 2/5 = 0.4
        self.assertEqual(self._kind("label, onPress, tone")[0], "local-component")

    def test_one_shared_prop_is_never_enough(self):
        with TemporaryDirectory() as tmp:
            repo = _Repo(tmp)
            repo.add("ui/atoms/Dot.tsx", "export function Dot({ tone }) {}\n")
            out, _ = repo.review(_added("web/src/Page.tsx", "function Pip({ tone }) {}\n"))
        self.assertEqual(_findings(out)[0][0], "local-component")

    def test_the_most_similar_part_is_named(self):
        with TemporaryDirectory() as tmp:
            repo = _Repo(tmp)
            repo.add("ui/atoms/Alpha.tsx", "export function Alpha({ a, b, c, x }) {}\n")
            repo.add("ui/atoms/Beta.tsx", "export function Beta({ a, b, c }) {}\n")
            out, _ = repo.review(_added("web/src/Page.tsx", "function Mine({ a, b, c }) {}\n"))
        self.assertIn("the shared part `Beta`", _findings(out)[0][2])

    def test_a_copy_wins_over_a_look_alike(self):
        with TemporaryDirectory() as tmp:
            repo = _Repo(tmp)
            repo.add("ui/molecules/Button.tsx", BUTTON)
            out, _ = repo.review(_added(
                "web/src/Page.tsx", "function Button({ label, onPress, disabled, size }) {}\n"))
        self.assertEqual(_findings(out)[0][0], "copy")

    def test_a_new_part_with_one_shared_prop_is_not_near(self):
        with TemporaryDirectory() as tmp:
            repo = _Repo(tmp)
            repo.add("ui/molecules/Button.tsx", BUTTON)
            out, _ = repo.review(_added(
                "ui/atoms/Tag.tsx", "export function Tag({ label, tone }) {}\n"))
        self.assertEqual(_findings(out)[0][:2], ("new-part", "APPROVE"))


# ---------------------------------------------------------------------------
# The provisional record
# ---------------------------------------------------------------------------

class ProvisionalRecordsTest(unittest.TestCase):

    def test_an_approve_verdict_with_the_line_yields_its_record(self):
        text = (FIXTURES / "approve-with-provisional.md").read_text(encoding="utf-8")
        self.assertEqual(parts_review.provisional_records(text), [(
            "Stepper", STEPPER,
            "no shared part shows a sequence of steps, and the stepper is built "
            "from tokens alone.")])

    def test_a_verdict_without_the_line_yields_none(self):
        text = (FIXTURES / "approve-without-provisional.md").read_text(encoding="utf-8")
        self.assertEqual(parts_review.provisional_records(text), [])

    def test_every_line_is_read_and_only_where_it_opens_a_line(self):
        text = ("🪴 part-provisional: `A` at `ui/A.tsx` — reasoning: one.\n"
                "see the `🪴 part-provisional: `B` at `ui/B.tsx` — reasoning: no.` line\n"
                "  🪴 part-provisional: `C` at `ui/C/index.tsx` — reasoning: two.\n")
        self.assertEqual(parts_review.provisional_records(text), [
            ("A", "ui/A.tsx", "one."), ("C", "ui/C/index.tsx", "two.")])

    def test_the_mark_and_the_tag(self):
        self.assertEqual(parts_review.PROVISIONAL_MARK, "🪴 part-provisional:")
        self.assertEqual(parts_review.TAG, "parts-review:")


# ---------------------------------------------------------------------------
# The runner's own python3 — nothing pip-installed
# ---------------------------------------------------------------------------

class ImportsTest(unittest.TestCase):

    def test_it_imports_only_the_standard_library_and_design_parts(self):
        tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0 and node.module:
                    names.add(node.module.split(".")[0])
        self.assertEqual({n for n in names if n not in sys.stdlib_module_names},
                         {"design_parts"})

    def test_it_runs_with_site_packages_switched_off(self):
        done = subprocess.run(
            [sys.executable, "-I", "-S", str(SCRIPT), "review",
             "--diff", str(FIXTURES / "notice-copy.diff"),
             "--repo-root", str(FIXTURES / "tree-notice-copy")],
            capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(done.stdout.splitlines()[0], APPLIES_1)


if __name__ == "__main__":
    unittest.main()
