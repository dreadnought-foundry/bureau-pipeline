"""Is the release carrying the siblings' merges live? (DRE-5922)

`scripts/proof_release.py` is the one reader of that question, and every rule
it has is pinned here with fixture reads and no network — the shape
`tests/test_release_train.py` drives the train in. What this file pins, in the
order the card lists it:

  (a) no `release.json` reads `ready`, and says so;
  (b) a docs-only merge reads `ready`, naming the surface it left untouched;
  (c) a touched `tag` surface whose newest tag's compare is `behind` reads
      `ready`, and `ahead` reads `waiting` naming surface, tag, PR and sha;
  (d) a `channel` surface is compared against the channel tag itself;
  (e) a surface with no tag at all is `waiting`;
  (f) a failing read is `unknown`, never `ready`;
  (g) `paths: []` — bureau-pipeline's own `pipeline-channel` — is the whole
      repository, never "no path matches".

plus the touch rule's two halves (the surface's `ignore` globs and
`release_train.DOCUMENTATION_IGNORE`, imported, never restated), git parity
for the glob semantics, the newest tag chosen by date across every glob in
the series, and the CLI.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import proof_release  # noqa: E402
import release_train  # noqa: E402

REPO = "dreadnought-foundry/portico"
RELEASE_JSON = f"repos/{REPO}/contents/.github/bureau/release.json"

SHA = "4b88482" + "0" * 33
CHANNEL_SHA = "1a2b3c4" + "f" * 33


def _contents(data: dict) -> dict:
    """The contents API's answer for a JSON file."""
    raw = json.dumps(data).encode()
    return {"encoding": "base64", "content": base64.b64encode(raw).decode()}


def _surface(**over) -> dict:
    entry = {"tag_series": ["portico-portals-v*"], "paths": ["portals/"],
             "script": "scripts/release.sh", "rollback": "x",
             "spacing_minutes": 0, "auto": True, "identity": "role"}
    entry.update(over)
    return entry


def _annotated(name: str, tag_sha: str) -> dict:
    return {"ref": f"refs/tags/{name}",
            "object": {"type": "tag", "sha": tag_sha}}


def _lightweight(name: str, commit_sha: str) -> dict:
    return {"ref": f"refs/tags/{name}",
            "object": {"type": "commit", "sha": commit_sha}}


class Reads:
    """A recorded GitHub: every path the reader asks for is answered from
    `table`, or raises — an exception value is raised, an unknown path is a
    failed read."""

    def __init__(self, table: dict):
        self.table = table
        self.asked: list[str] = []

    def __call__(self, path: str):
        self.asked.append(path)
        if path not in self.table:
            raise RuntimeError(f"gh api {path}: HTTP 500 (not recorded)")
        value = self.table[path]
        if isinstance(value, Exception):
            raise value
        return value


def portals(compare: str, *, files=("portals/src/app.ts",)) -> tuple:
    """A portals repo whose newest tag is portico-portals-v1.0.107."""
    read = Reads({
        RELEASE_JSON: _contents({"surfaces": {"portals": _surface()}}),
        f"repos/{REPO}/git/matching-refs/tags/portico-portals-v": [
            _annotated("portico-portals-v1.0.106", "t106"),
            _annotated("portico-portals-v1.0.107", "t107"),
        ],
        f"repos/{REPO}/git/tags/t106": {"tagger": {"date": "2026-10-01T10:00:00Z"}},
        f"repos/{REPO}/git/tags/t107": {"tagger": {"date": "2026-10-02T10:00:00Z"}},
        f"repos/{REPO}/compare/portico-portals-v1.0.107...{SHA}": {"status": compare},
    })
    merge = proof_release.Merge("DRE-5590", 866, SHA, list(files))
    return read, merge


def pipeline_channel(compare: str | None) -> Reads:
    """bureau-pipeline's own release.json: `pipeline-channel`, `paths: []`,
    record `channel`, series `["stable"]`."""
    repo = "dreadnought-foundry/bureau-pipeline"
    data = json.loads((ROOT / ".github/bureau/release.json").read_text())
    table = {
        f"repos/{repo}/contents/.github/bureau/release.json": _contents(data),
        f"repos/{repo}/git/matching-refs/tags/stable": [
            _lightweight("stable", "c" * 40)],
        f"repos/{repo}/git/commits/{'c' * 40}": {
            "committer": {"date": "2026-10-05T12:00:00Z"}},
    }
    if compare is not None:
        table[f"repos/{repo}/compare/stable...{CHANNEL_SHA}"] = {"status": compare}
    return Reads(table)


class NoReleaseRecord(unittest.TestCase):
    """(a) No `release.json` — there is no release to wait for."""

    def test_a_missing_release_json_reads_ready_and_says_so(self):
        read = Reads({RELEASE_JSON: proof_release.Missing("HTTP 404: Not Found")})
        got = proof_release.reading(
            REPO, [proof_release.Merge("DRE-1", 1, SHA, ["a.py"])], read=read)
        self.assertEqual(got.state, "ready")
        self.assertEqual(got.lines, [
            "ready — no release record at .github/bureau/release.json: "
            "nothing to wait for"])

    def test_a_404_raised_as_plain_text_is_also_no_file(self):
        """The dispatcher may hand its own `gh api` read in: a 404 is read
        off the error, not only off this module's exception type."""
        read = Reads({RELEASE_JSON: RuntimeError("gh: Not Found (HTTP 404)")})
        got = proof_release.reading(
            REPO, [proof_release.Merge("DRE-1", 1, SHA, ["a.py"])], read=read)
        self.assertEqual(got.state, "ready")

    def test_a_file_declaring_no_surfaces_reads_ready(self):
        read = Reads({RELEASE_JSON: _contents({"surfaces": {}})})
        got = proof_release.reading(
            REPO, [proof_release.Merge("DRE-1", 1, SHA, ["a.py"])], read=read)
        self.assertEqual(got.state, "ready")
        self.assertIn("declares no surfaces", got.lines[0])
        self.assertTrue(got.lines[0].startswith("ready — "))


class DocsOnly(unittest.TestCase):
    """(b) A merge that changed only documentation owes no release."""

    def test_b_docs_only_merge_reads_ready_naming_the_untouched_surface(self):
        read, merge = portals("ahead", files=("portals/README.md",
                                              "portals/docs/guide.txt"))
        got = proof_release.reading(REPO, [merge], read=read)
        self.assertEqual(got.state, "ready")
        self.assertEqual(len(got.lines), 1)
        self.assertTrue(got.lines[0].startswith("ready — portals: untouched"),
                        got.lines[0])
        self.assertIn("#866 (4b88482) for DRE-5590", got.lines[0])
        # an untouched surface never reads its tags or compares
        self.assertEqual(read.asked, [RELEASE_JSON])

    def test_b_a_merge_outside_the_surface_paths_touches_nothing(self):
        read, merge = portals("ahead", files=("backend/app.py",))
        got = proof_release.reading(REPO, [merge], read=read)
        self.assertEqual(got.state, "ready")
        self.assertIn("untouched", got.lines[0])


class TagSurface(unittest.TestCase):
    """(c) A touched `record: tag` surface, against its newest tag."""

    def test_c_behind_is_carried_and_reads_ready(self):
        read, merge = portals("behind")
        got = proof_release.reading(REPO, [merge], read=read)
        self.assertEqual(got.state, "ready")
        self.assertEqual(got.lines, [
            "ready — portals: newest portico-portals-v1.0.107 carries #866 "
            "(4b88482) for DRE-5590"])

    def test_c_identical_is_carried_too(self):
        read, merge = portals("identical")
        self.assertEqual(proof_release.reading(REPO, [merge], read=read).state,
                         "ready")

    def test_c_ahead_reads_waiting_naming_surface_tag_pr_and_sha(self):
        read, merge = portals("ahead")
        got = proof_release.reading(REPO, [merge], read=read)
        self.assertEqual(got.state, "waiting")
        self.assertEqual(got.lines, [
            "waiting — portals: newest portico-portals-v1.0.107 does not "
            "carry #866 (4b88482) for DRE-5590"])

    def test_c_diverged_reads_waiting(self):
        read, merge = portals("diverged")
        self.assertEqual(proof_release.reading(REPO, [merge], read=read).state,
                         "waiting")

    def test_c_the_carried_answers_are_the_channel_records(self):
        """`identical`/`behind` are `channel_record.CARRIED`, imported."""
        import channel_record

        self.assertIs(proof_release.CARRIED, channel_record.CARRIED)

    def test_c_one_waiting_merge_holds_the_whole_reading(self):
        read, merge = portals("behind")
        second = proof_release.Merge("DRE-5591", 870, "9" * 40,
                                     ["portals/src/x.ts"])
        read.table[f"repos/{REPO}/compare/portico-portals-v1.0.107...{'9' * 40}"] = {
            "status": "ahead"}
        got = proof_release.reading(REPO, [merge, second], read=read)
        self.assertEqual(got.state, "waiting")
        self.assertEqual([line.split(" — ")[0] for line in got.lines],
                         ["ready", "waiting"])
        self.assertIn("#870 (9999999) for DRE-5591", got.lines[1])


class ChannelSurface(unittest.TestCase):
    """(d) A `record: channel` surface is compared against the moving tag."""

    def test_d_channel_surface_compares_against_the_channel_tag(self):
        repo = "dreadnought-foundry/bureau-pipeline"
        read = pipeline_channel("behind")
        merge = proof_release.Merge("DRE-5921", 744, CHANNEL_SHA,
                                    ["scripts/proof_run_state.py"])
        got = proof_release.reading(repo, [merge], read=read)
        self.assertEqual(got.state, "ready")
        self.assertIn(f"repos/{repo}/compare/stable...{CHANNEL_SHA}", read.asked)
        self.assertEqual(got.lines, [
            "ready — pipeline-channel (the whole repository): stable carries "
            "#744 (1a2b3c4) for DRE-5921"])


class NoTag(unittest.TestCase):
    """(e) A first release is exactly what "nothing changed" would swallow."""

    def test_e_a_touched_surface_with_no_tag_is_waiting(self):
        read, merge = portals("behind")
        read.table[f"repos/{REPO}/git/matching-refs/tags/portico-portals-v"] = []
        got = proof_release.reading(REPO, [merge], read=read)
        self.assertEqual(got.state, "waiting")
        self.assertEqual(got.lines, [
            "waiting — portals: no tag in portico-portals-v* yet, so nothing "
            "carries #866 (4b88482) for DRE-5590"])


class FailedRead(unittest.TestCase):
    """(f) An unreadable release record holds the proof back and says why."""

    def test_f_a_failing_release_json_read_is_unknown(self):
        read = Reads({RELEASE_JSON: RuntimeError("HTTP 502: Bad Gateway")})
        got = proof_release.reading(
            REPO, [proof_release.Merge("DRE-1", 1, SHA, ["a.py"])], read=read)
        self.assertEqual(got.state, "unknown")
        self.assertIn("HTTP 502", got.lines[0])
        self.assertTrue(got.lines[0].startswith("unknown — "))

    def test_f_a_failing_compare_is_unknown_never_ready(self):
        read, merge = portals("behind")
        read.table[f"repos/{REPO}/compare/portico-portals-v1.0.107...{SHA}"] = (
            RuntimeError("HTTP 500"))
        got = proof_release.reading(REPO, [merge], read=read)
        self.assertEqual(got.state, "unknown")

    def test_f_a_failing_tag_read_is_unknown(self):
        read, merge = portals("behind")
        read.table[f"repos/{REPO}/git/tags/t107"] = RuntimeError("HTTP 500")
        self.assertEqual(proof_release.reading(REPO, [merge], read=read).state,
                         "unknown")

    def test_f_an_unreadable_release_json_is_unknown(self):
        read = Reads({RELEASE_JSON: {"encoding": "base64",
                                     "content": base64.b64encode(b"{not json").decode()}})
        got = proof_release.reading(
            REPO, [proof_release.Merge("DRE-1", 1, SHA, ["a.py"])], read=read)
        self.assertEqual(got.state, "unknown")

    def test_f_an_unknown_compare_answer_is_unknown(self):
        read, merge = portals("something-new")
        self.assertEqual(proof_release.reading(REPO, [merge], read=read).state,
                         "unknown")

    def test_f_unknown_outranks_waiting(self):
        read, merge = portals("ahead")
        second = proof_release.Merge("DRE-5591", 870, "9" * 40,
                                     ["portals/src/x.ts"])
        got = proof_release.reading(REPO, [merge, second], read=read)
        self.assertEqual(got.state, "unknown")


class WholeRepository(unittest.TestCase):
    """(g) `paths: []` is the whole repository — bureau-pipeline's shape."""

    def setUp(self):
        self.repo = "dreadnought-foundry/bureau-pipeline"

    def test_g_the_live_release_json_still_declares_the_shape(self):
        data = json.loads((ROOT / ".github/bureau/release.json").read_text())
        entry = data["surfaces"]["pipeline-channel"]
        self.assertEqual(entry["paths"], [])
        self.assertEqual(entry["record"], "channel")

    def test_g_a_py_change_touches_it_and_waits_when_stable_lacks_the_sha(self):
        read = pipeline_channel("ahead")
        merge = proof_release.Merge("DRE-5921", 744, CHANNEL_SHA,
                                    ["scripts/proof_run_state.py"])
        got = proof_release.reading(self.repo, [merge], read=read)
        self.assertEqual(got.state, "waiting")
        self.assertEqual(got.lines, [
            "waiting — pipeline-channel (the whole repository): stable does "
            "not carry #744 (1a2b3c4) for DRE-5921"])

    def test_g_only_md_files_read_ready_with_the_surface_untouched(self):
        read = pipeline_channel(None)
        merge = proof_release.Merge("DRE-5921", 744, CHANNEL_SHA,
                                    ["README.md", "standards/proof.md"])
        got = proof_release.reading(self.repo, [merge], read=read)
        self.assertEqual(got.state, "ready")
        self.assertTrue(got.lines[0].startswith(
            "ready — pipeline-channel (the whole repository): untouched"),
            got.lines[0])


class TouchRule(unittest.TestCase):
    """A merge touches a surface when a changed file falls under `paths` and
    matches neither the surface's `ignore` nor `DOCUMENTATION_IGNORE`."""

    def _surface(self, **over):
        return release_train.surface("s", _surface(**over))

    def test_documentation_ignore_is_imported_never_restated(self):
        self.assertIs(proof_release.DOCUMENTATION_IGNORE,
                      release_train.DOCUMENTATION_IGNORE)
        source = (ROOT / "scripts/proof_release.py").read_text()
        for glob in release_train.DOCUMENTATION_IGNORE:
            self.assertNotIn(f'"{glob}"', source)

    def test_the_surface_ignore_globs_are_honored(self):
        surface = self._surface(paths=[], ignore=["**/*.lock", "fixtures/**"])
        self.assertEqual(
            proof_release.touching(surface, ["a/yarn.lock", "fixtures/x/y.json"]),
            [])
        self.assertEqual(
            proof_release.touching(surface, ["a/yarn.lock", "src/app.py"]),
            ["src/app.py"])

    def test_documentation_counts_even_when_the_surface_declares_ignore(self):
        """The card's rule: none of its `ignore` globs AND none of
        `DOCUMENTATION_IGNORE` — a declared `ignore` never re-admits docs."""
        surface = self._surface(paths=[], ignore=["**/*.lock"])
        self.assertEqual(proof_release.touching(
            surface, ["notes.md", "deep/docs/a.py"]), [])

    def test_every_file_ignored_touches_nothing(self):
        surface = self._surface(paths=["portals/"], ignore=["portals/gen/**"])
        self.assertEqual(proof_release.touching(
            surface, ["portals/gen/a.ts", "portals/x.md"]), [])

    def test_empty_paths_is_the_whole_repository(self):
        surface = self._surface(paths=[])
        self.assertEqual(proof_release.touching(surface, ["anything/at/all.c"]),
                         ["anything/at/all.c"])

    def test_glob_semantics_match_git(self):
        """The same files git's pathspec selects, through `_pathspec`, for
        each shape a `release.json` declares — the parity a regex can lose."""
        files = ["a.md", "top.py", "docs/a/b.py", "portals/docs/c.py",
                 "portals/x.md", "portals/src/y.ts", "src/x/y.py",
                 "src/x/docsy.py", "src/z.py", "README.d/z.txt",
                 "portals2/a.ts", "a/yarn.lock", "gen/deep/x.json"]
        shapes = [
            ([], release_train.DOCUMENTATION_IGNORE),
            (["portals/"], release_train.DOCUMENTATION_IGNORE),
            (["portals"], ()),
            (["src/*.py"], ()),
            (["src", "top.py"], ("src/*",)),
            ([], ("src/**", "*.py")),
            ([], ("**/*.lock", "gen", "docs")),
            ([], ("**/x/**", "README.d/?.txt")),
        ]
        with TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "-q", tmp], check=True)
            for name in files:
                path = Path(tmp, name)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("x\n")
            subprocess.run(["git", "-C", tmp, "add", "."], check=True)
            for paths, ignore in shapes:
                spec = release_train._pathspec(paths, ignore)
                listed = subprocess.run(
                    ["git", "-C", tmp, "ls-files", "--", *spec] if spec
                    else ["git", "-C", tmp, "ls-files"],
                    capture_output=True, text=True, check=True).stdout.split()
                ours = [f for f in files
                        if proof_release.under(f, paths)
                        and not proof_release.ignored(f, ignore)]
                self.assertEqual(sorted(ours), sorted(listed),
                                 f"paths={paths} ignore={ignore}")


class NewestTag(unittest.TestCase):
    """The record is the newest tag across ALL the globs, by creator date —
    `release_train.newest_tag`'s ordering, read through the REST API."""

    def test_newest_by_date_across_globs_not_by_glob_order(self):
        read = Reads({
            f"repos/{REPO}/git/matching-refs/tags/new-v": [
                _annotated("new-v2", "tn2"), _annotated("new-v10", "tn10")],
            f"repos/{REPO}/git/matching-refs/tags/old-v": [
                _lightweight("old-v9", "c9")],
            f"repos/{REPO}/git/tags/tn2": {"tagger": {"date": "2026-10-03T00:00:00Z"}},
            f"repos/{REPO}/git/tags/tn10": {"tagger": {"date": "2026-10-01T00:00:00Z"}},
            f"repos/{REPO}/git/commits/c9": {"committer": {"date": "2026-10-04T00:00:00Z"}},
        })
        self.assertEqual(
            proof_release.newest_tag(REPO, ["new-v*", "old-v*"], read=read),
            "old-v9")
        del read.table[f"repos/{REPO}/git/matching-refs/tags/old-v"]
        read.table[f"repos/{REPO}/git/matching-refs/tags/old-v"] = []
        # within one glob: date, not name — v2 is newer than v10 here
        self.assertEqual(
            proof_release.newest_tag(REPO, ["new-v*", "old-v*"], read=read),
            "new-v2")

    def test_the_prefix_read_is_filtered_by_the_glob(self):
        """`matching-refs/tags/stable` also answers `stable-old`; the glob
        `stable` names only `stable`."""
        read = Reads({
            f"repos/{REPO}/git/matching-refs/tags/stable": [
                _lightweight("stable", "c1"), _lightweight("stable-old", "c2")],
            f"repos/{REPO}/git/commits/c1": {"committer": {"date": "2026-10-01T00:00:00Z"}},
            f"repos/{REPO}/git/commits/c2": {"committer": {"date": "2026-10-05T00:00:00Z"}},
        })
        self.assertEqual(proof_release.newest_tag(REPO, ["stable"], read=read),
                         "stable")

    def test_no_tag_is_none(self):
        read = Reads({f"repos/{REPO}/git/matching-refs/tags/x-v": []})
        self.assertIsNone(proof_release.newest_tag(REPO, ["x-v*"], read=read))


class FileList(unittest.TestCase):
    """A merge handed in with no file list reads its pull request's files."""

    def test_an_empty_file_list_is_read_from_the_pull_request(self):
        read, merge = portals("ahead")
        merge = merge._replace(files=[])
        read.table[f"repos/{REPO}/pulls/866/files?per_page=100&page=1"] = [
            {"filename": "portals/README.md"}]
        got = proof_release.reading(REPO, [merge], read=read)
        self.assertEqual(got.state, "ready")
        self.assertIn("untouched", got.lines[0])


class Cli(unittest.TestCase):
    """`check` prints one `proof-release:` line per reading line; exit 0 for
    ready/waiting, 2 for unknown."""

    def _run(self, argv, read):
        import contextlib
        import io

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = proof_release.main(argv, read=read)
        return code, out.getvalue().splitlines()

    def test_waiting_prints_each_line_and_exits_0(self):
        read, _ = portals("ahead")
        code, printed = self._run(
            ["check", "--repo", REPO, "--merge",
             f"DRE-5590:866:{SHA}:portals/src/app.ts,portals/x.md"], read)
        self.assertEqual(code, 0)
        self.assertEqual(printed, [
            "proof-release: waiting — portals: newest portico-portals-v1.0.107 "
            "does not carry #866 (4b88482) for DRE-5590"])

    def test_ready_exits_0(self):
        read = Reads({RELEASE_JSON: proof_release.Missing("HTTP 404")})
        code, printed = self._run(
            ["check", "--repo", REPO, "--merge", f"DRE-1:1:{SHA}:a.py",
             "--merge", f"DRE-2:2:{SHA}"], read)
        self.assertEqual(code, 0)
        self.assertEqual(len(printed), 1)
        self.assertTrue(printed[0].startswith("proof-release: ready — "))

    def test_unknown_exits_2(self):
        read = Reads({RELEASE_JSON: RuntimeError("HTTP 502")})
        code, printed = self._run(
            ["check", "--repo", REPO, "--merge", f"DRE-1:1:{SHA}:a.py"], read)
        self.assertEqual(code, 2)
        self.assertTrue(printed[0].startswith("proof-release: unknown — "))

    def test_a_malformed_merge_is_refused(self):
        with self.assertRaises(SystemExit):
            with open(os.devnull, "w") as sink:
                import contextlib

                with contextlib.redirect_stderr(sink):
                    proof_release.main(["check", "--repo", REPO,
                                        "--merge", "DRE-1"], read=Reads({}))


if __name__ == "__main__":
    unittest.main()
