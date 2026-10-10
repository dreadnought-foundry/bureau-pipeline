"""A repo declares how its released commit runs locally (DRE-6024).

`.github/bureau/proof-local.json` in a consumer repo says three things the
pipeline cannot guess: how the checked-out commit is served locally, which
release surface decides what "the released commit" is, and which page a
`Local screen:` criterion opens. `scripts/proof_local.py` is its one reader.

Every rule `check` enforces has its own test below, and each goes red when the
rule is removed. The released commit is resolved on a throwaway git repository
with real tags, through `release_train.surfaces` and `release_train.newest_tag`
— never a re-derivation. The reader runs on the runner's own `python3` with
nothing pip-installed, so one test reads its imports and another runs it with
site-packages switched off.
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import proof_local  # noqa: E402
import release_train  # noqa: E402

SCRIPT = ROOT / "scripts" / "proof_local.py"
DOC = ROOT / "docs" / "proof-local-run.md"
BRIEF = ROOT / "briefs" / "planner.md"

RELEASE = {
    "surfaces": {
        "portals": {"tag_series": ["portals-v*", "portal-v*"], "paths": []},
        "pipeline-channel": {"tag_series": ["stable"], "paths": []},
    }
}


def _valid(**over) -> dict:
    data = {
        "surface": "portals",
        "node_dir": "web",
        "setup": "npm ci --prefix web",
        "start": "npm run dev --prefix web -- --port 5173",
        "ready_url": "http://127.0.0.1:5173/",
        "ready_timeout_seconds": 120,
        "pages": {"home": "/", "documents": "/documents"},
        "login": {
            "command": "node scripts/proof-login.mjs",
            "form": {"username": "#email", "password": "#password",
                     "submit": "button[type=submit]"},
        },
    }
    data.update(over)
    return {k: v for k, v in data.items() if v is not None}


def _session(**over) -> dict:
    """A valid declaration whose `login` declares the `session` kind;
    `over` replaces keys of `login.session`, a None value drops one."""
    session = {
        "local_storage": [{"key": "app_id_token", "from": "id_token"},
                          {"key": "app_refresh_token", "from": "refresh_token"}],
        "cookies": [{"name": "app_session", "from": "session_cookie"}],
    }
    session.update(over)
    session = {k: v for k, v in session.items() if v is not None}
    return _valid(login={"command": "node scripts/proof-session.mjs",
                         "session": session})


class _Repo:
    """A repo root with `.github/bureau/` holding the files a test names."""

    def __init__(self, tmp: str, declaration=None, release=RELEASE):
        self.root = Path(tmp)
        self.bureau = self.root / ".github" / "bureau"
        self.bureau.mkdir(parents=True)
        if release is not None:
            (self.bureau / "release.json").write_text(json.dumps(release))
        self.declaration = self.bureau / "proof-local.json"
        if isinstance(declaration, dict):
            self.declaration.write_text(json.dumps(declaration))
        elif isinstance(declaration, str):
            self.declaration.write_text(declaration)


def _git(root, *args) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    return subprocess.run(["git", "-C", str(root), *args], check=True, env=env,
                          capture_output=True, text=True).stdout.strip()


def _commit(root, message: str) -> str:
    _git(root, "commit", "-q", "--allow-empty", "-m", message)
    return _git(root, "rev-parse", "HEAD")


def _run(*argv, cwd=None):
    return subprocess.run([sys.executable, str(SCRIPT), *argv], cwd=cwd,
                          capture_output=True, text=True)


# ---------------------------------------------------------------------------
# check — strict, and it names what is wrong
# ---------------------------------------------------------------------------

class CheckTest(unittest.TestCase):

    def reason(self, data) -> str:
        """`check`'s refusal for `data`, asserting it IS a refusal."""
        with TemporaryDirectory() as tmp:
            repo = _Repo(tmp, data)
            done = _run("check", "--declaration", str(repo.declaration))
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        line = done.stdout.strip()
        self.assertTrue(line.startswith("proof-local: invalid — "), line)
        return line

    def test_the_documented_example_is_accepted(self):
        example = _doc_example()
        with TemporaryDirectory() as tmp:
            release = {"surfaces": {example["surface"]: {"tag_series": ["v*"]}}}
            repo = _Repo(tmp, example, release=release)
            done = _run("check", "--declaration", str(repo.declaration))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(
            done.stdout.strip(),
            f"proof-local: ok — surface {example['surface']}, "
            f"{len(example['pages'])} page(s), login declared")

    def test_a_valid_file_without_login_says_login_none(self):
        with TemporaryDirectory() as tmp:
            repo = _Repo(tmp, _valid(login=None))
            done = _run("check", "--declaration", str(repo.declaration))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(done.stdout.strip(),
                         "proof-local: ok — surface portals, 2 page(s), login none")

    def test_no_file_is_none_never_an_error(self):
        with TemporaryDirectory() as tmp:
            repo = _Repo(tmp)
            done = _run("check", "--declaration", str(repo.declaration))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(done.stdout.strip(),
                         "proof-local: none — no .github/bureau/proof-local.json")

    def test_the_default_path_is_the_repo_declaration(self):
        self.assertEqual(proof_local.DECLARATION, ".github/bureau/proof-local.json")
        with TemporaryDirectory() as tmp:
            _Repo(tmp, _valid())
            done = _run("check", cwd=tmp)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("surface portals", done.stdout)

    def test_unparseable_json_is_named(self):
        self.assertIn("not valid JSON", self.reason("{not json"))

    def test_an_unknown_surface_is_named(self):
        line = self.reason(_valid(surface="docs-site"))
        self.assertIn("surface docs-site", line)
        self.assertIn("release.json", line)

    def test_a_surface_with_no_release_json_is_unknown(self):
        with TemporaryDirectory() as tmp:
            repo = _Repo(tmp, _valid(), release=None)
            done = _run("check", "--declaration", str(repo.declaration))
        self.assertEqual(done.returncode, 1)
        self.assertIn("surface portals", done.stdout)

    def release_reason(self, release) -> str:
        """`check`'s refusal for a valid declaration beside `release`."""
        with TemporaryDirectory() as tmp:
            repo = _Repo(tmp, _valid(), release=release)
            done = _run("check", "--declaration", str(repo.declaration))
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertNotIn("Traceback", done.stderr)
        line = done.stdout.strip()
        self.assertTrue(line.startswith("proof-local: invalid — "), line)
        self.assertIn("release.json", line)
        return line

    def test_a_release_json_that_is_not_an_object_is_named(self):
        self.release_reason([])

    def test_a_surface_that_is_not_an_object_is_named(self):
        self.release_reason({"surfaces": {"portals": "x"}})

    def test_a_surfaces_value_that_is_not_an_object_is_named(self):
        self.release_reason({"surfaces": ["portals"]})

    def test_a_surface_with_a_non_numeric_spacing_is_named(self):
        self.release_reason({"surfaces": {"portals": {
            "tag_series": ["portals-v*"], "spacing_minutes": "abc"}}})

    def test_a_non_loopback_ready_url_is_named(self):
        for url in ("http://localhost:5173/", "https://127.0.0.1:5173/",
                    "http://portico.example.com:5173/", "http://127.0.0.1/",
                    "http://127.0.0.1:5173", "http://127.0.0.1:0/",
                    "http://127.0.0.1:70000/", "http://127.0.0.1:5173@evil.test/"):
            with self.subTest(url=url):
                self.assertIn("ready_url", self.reason(_valid(ready_url=url)))

    def test_a_loopback_ready_url_with_a_path_is_accepted(self):
        proof_local.validate(_valid(ready_url="http://127.0.0.1:8080/health?x=1"),
                             RELEASE)

    def test_an_empty_pages_map_is_named(self):
        line = self.reason(_valid(pages={}))
        self.assertIn("pages", line)
        self.assertIn("empty", line)

    def test_a_missing_pages_map_is_named(self):
        data = _valid()
        del data["pages"]
        self.assertIn("pages", self.reason(data))

    def test_a_page_path_without_a_leading_slash_is_named(self):
        line = self.reason(_valid(pages={"home": "/", "documents": "documents"}))
        self.assertIn("documents", line)
        self.assertIn("/", line)

    def test_a_page_path_that_leaves_the_local_host_is_refused(self):
        for path in ("//evil.test/", "/\\evil.test"):
            with self.subTest(path=path):
                self.assertIn("pages", self.reason(_valid(pages={"home": path})))

    def test_a_login_missing_a_form_selector_is_named(self):
        for field in ("username", "password", "submit"):
            data = _valid()
            del data["login"]["form"][field]
            with self.subTest(field=field):
                line = self.reason(data)
                self.assertIn("login.form", line)
                self.assertIn(field, line)

    def test_a_login_missing_its_command_is_named(self):
        data = _valid()
        del data["login"]["command"]
        self.assertIn("login.command", self.reason(data))

    # The `session` kind (DRE-6536): a sign-in that fills no form.

    def test_a_session_login_is_accepted_with_the_same_ok_line(self):
        with TemporaryDirectory() as tmp:
            repo = _Repo(tmp, _session())
            done = _run("check", "--declaration", str(repo.declaration))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(done.stdout.strip(),
                         "proof-local: ok — surface portals, 2 page(s), login declared")

    def test_a_session_login_with_one_list_is_accepted(self):
        for absent in ("local_storage", "cookies"):
            with self.subTest(absent=absent):
                found = proof_local.validate(_session(**{absent: None}), RELEASE)
                self.assertEqual(found.login["session"][absent], [])

    def test_a_login_carrying_both_kinds_is_refused(self):
        data = _session()
        data["login"]["form"] = _valid()["login"]["form"]
        self.assertIn("`login` carries both `form` and `session` — declare one",
                      self.reason(data))

    def test_a_login_carrying_neither_kind_is_refused(self):
        line = self.reason(_valid(login={"command": "node scripts/proof-login.mjs"}))
        self.assertIn("`login` must carry one of `form` or `session` beside `command`",
                      line)

    def test_a_session_with_no_entry_is_refused(self):
        for session in ({}, {"local_storage": []}, {"cookies": []},
                        {"local_storage": [], "cookies": []}):
            data = _valid(login={"command": "x", "session": session})
            with self.subTest(session=session):
                line = self.reason(data)
                self.assertIn("login.session", line)
                self.assertIn("no entry", line)

    def test_a_session_that_is_not_an_object_is_refused(self):
        data = _valid(login={"command": "x", "session": [{"key": "a", "from": "b"}]})
        self.assertIn("login.session", self.reason(data))

    def test_a_session_list_that_is_not_a_list_is_refused(self):
        line = self.reason(_session(cookies={"name": "a", "from": "b"}))
        self.assertIn("login.session.cookies", line)
        self.assertIn("list", line)

    def test_an_entry_that_is_not_an_object_is_refused(self):
        line = self.reason(_session(local_storage=["app_id_token"]))
        self.assertIn("login.session.local_storage[0]", line)

    def test_an_entry_missing_from_is_refused_and_named(self):
        for where, entry, path in (
                ("cookies", {"name": "app_session"}, "login.session.cookies[0].from"),
                ("local_storage", {"key": "k"}, "login.session.local_storage[0].from")):
            with self.subTest(where=where):
                self.assertIn(path, self.reason(_session(**{where: [entry]})))

    def test_a_from_of_the_wrong_shape_is_refused_and_named(self):
        for bad in ("", "1token", "id-token", "id token", "id.token", "tök",
                    "id_token\n", 5, None, ["id_token"]):
            entry = {"name": "app_session", "from": bad}
            with self.subTest(bad=bad):
                line = self.reason(_session(cookies=[entry]))
                self.assertIn("login.session.cookies[0].from", line)

    def test_a_from_of_the_right_shape_is_accepted(self):
        for good in ("_", "a", "id_token", "Token2", "_9"):
            with self.subTest(good=good):
                proof_local.validate(
                    _session(cookies=[{"name": "c", "from": good}]), RELEASE)

    def test_an_entry_missing_its_key_or_name_is_refused_and_named(self):
        for where, entry, path in (
                ("local_storage", {"from": "id_token"}, "login.session.local_storage[0].key"),
                ("cookies", {"from": "id_token"}, "login.session.cookies[0].name"),
                ("local_storage", {"key": "", "from": "a"}, "login.session.local_storage[0].key"),
                ("cookies", {"name": "a\nb", "from": "a"}, "login.session.cookies[0].name")):
            with self.subTest(where=where, entry=entry):
                self.assertIn(path, self.reason(_session(**{where: [entry]})))

    def test_two_entries_sharing_a_key_or_name_are_refused(self):
        for where, field in (("local_storage", "key"), ("cookies", "name")):
            entries = [{field: "same", "from": "a"}, {field: "same", "from": "b"}]
            with self.subTest(where=where):
                line = self.reason(_session(**{where: entries}))
                self.assertIn(f"login.session.{where}[1].{field}", line)
                self.assertIn("same", line)

    def test_the_same_name_in_both_lists_is_accepted(self):
        proof_local.validate(_session(local_storage=[{"key": "t", "from": "a"}],
                                      cookies=[{"name": "t", "from": "a"}]), RELEASE)

    def test_an_unknown_key_inside_the_session_is_refused(self):
        line = self.reason(_session(session_storage=[{"key": "a", "from": "b"}]))
        self.assertIn("login.session", line)
        self.assertIn("session_storage", line)

    def test_an_unknown_key_inside_an_entry_is_refused(self):
        for where, entry, extra in (
                ("local_storage", {"key": "k", "from": "a", "value": "v"}, "value"),
                ("local_storage", {"key": "k", "name": "k", "from": "a"}, "name"),
                ("cookies", {"name": "n", "from": "a", "domain": "evil.test"}, "domain"),
                ("cookies", {"name": "n", "key": "n", "from": "a"}, "key")):
            with self.subTest(where=where, extra=extra):
                line = self.reason(_session(**{where: [entry]}))
                self.assertIn(f"login.session.{where}[0]", line)
                self.assertIn(f"unknown key(s) {extra}", line)

    def test_the_contract_names_are_the_modules(self):
        self.assertEqual(proof_local.LOGIN_KINDS, ("form", "session"))
        self.assertEqual(proof_local.LOGIN_KEYS, ("command", "form", "session"))
        self.assertEqual(proof_local.SESSION_KEYS, ("local_storage", "cookies"))
        self.assertEqual(proof_local.STORAGE_ENTRY_KEYS, ("key", "from"))
        self.assertEqual(proof_local.COOKIE_ENTRY_KEYS, ("name", "from"))
        self.assertEqual(proof_local.FORM_FIELDS, ("username", "password", "submit"))

    def test_start_is_required(self):
        data = _valid()
        del data["start"]
        self.assertIn("start", self.reason(data))

    def test_an_unknown_key_is_named(self):
        self.assertIn("page", self.reason(_valid(page={"home": "/"})))

    def test_a_node_dir_outside_the_worktree_is_refused(self):
        for node_dir in ("/srv/web", "../web", "web/../../x"):
            with self.subTest(node_dir=node_dir):
                self.assertIn("node_dir", self.reason(_valid(node_dir=node_dir)))

    def test_ready_timeout_must_be_a_positive_whole_number(self):
        for bad in (0, -5, "180", True, 1.5):
            with self.subTest(bad=bad):
                self.assertIn("ready_timeout_seconds",
                              self.reason(_valid(ready_timeout_seconds=bad)))


# ---------------------------------------------------------------------------
# load — the Declaration the siblings read
# ---------------------------------------------------------------------------

class LoadTest(unittest.TestCase):

    def test_load_returns_every_attribute(self):
        with TemporaryDirectory() as tmp:
            repo = _Repo(tmp, _valid())
            found = proof_local.load(repo.declaration)
        self.assertEqual(found.surface, "portals")
        self.assertEqual(found.node_dir, "web")
        self.assertEqual(found.setup, "npm ci --prefix web")
        self.assertEqual(found.start, "npm run dev --prefix web -- --port 5173")
        self.assertEqual(found.ready_url, "http://127.0.0.1:5173/")
        self.assertEqual(found.ready_timeout_seconds, 120)
        self.assertEqual(found.pages, {"home": "/", "documents": "/documents"})
        self.assertEqual(found.login["command"], "node scripts/proof-login.mjs")
        self.assertEqual(found.login["form"]["submit"], "button[type=submit]")

    def test_the_optional_keys_default(self):
        data = _valid(node_dir=None, setup=None, login=None)
        del data["ready_timeout_seconds"]
        with TemporaryDirectory() as tmp:
            repo = _Repo(tmp, data)
            found = proof_local.load(repo.declaration)
        self.assertIsNone(found.node_dir)
        self.assertIsNone(found.setup)
        self.assertIsNone(found.login)
        self.assertEqual(found.ready_timeout_seconds, 180)

    def test_a_form_login_loads_exactly_as_before(self):
        with TemporaryDirectory() as tmp:
            repo = _Repo(tmp, _valid())
            found = proof_local.load(repo.declaration)
        self.assertEqual(found.login, {
            "command": "node scripts/proof-login.mjs",
            "form": {"username": "#email", "password": "#password",
                     "submit": "button[type=submit]"}})

    def test_a_session_login_loads_as_command_and_both_lists(self):
        with TemporaryDirectory() as tmp:
            repo = _Repo(tmp, _session())
            found = proof_local.load(repo.declaration)
        self.assertEqual(found.login, {
            "command": "node scripts/proof-session.mjs",
            "session": {
                "local_storage": [{"key": "app_id_token", "from": "id_token"},
                                  {"key": "app_refresh_token", "from": "refresh_token"}],
                "cookies": [{"name": "app_session", "from": "session_cookie"}]}})
        self.assertNotIn("form", found.login)

    def test_a_session_login_returns_an_absent_list_as_empty(self):
        with TemporaryDirectory() as tmp:
            repo = _Repo(tmp, _session(local_storage=None))
            found = proof_local.load(repo.declaration)
        self.assertEqual(found.login["session"], {
            "local_storage": [],
            "cookies": [{"name": "app_session", "from": "session_cookie"}]})
        self.assertEqual(list(found.login["session"]), ["local_storage", "cookies"])

    def test_a_session_login_keeps_the_declared_order(self):
        entries = [{"key": k, "from": k} for k in ("zeta", "alpha", "mid")]
        found = proof_local.validate(_session(local_storage=entries, cookies=None),
                                     RELEASE)
        self.assertEqual([e["key"] for e in found.login["session"]["local_storage"]],
                         ["zeta", "alpha", "mid"])
        self.assertEqual(found.login["session"]["cookies"], [])

    def test_an_invalid_file_raises_invalid(self):
        with TemporaryDirectory() as tmp:
            repo = _Repo(tmp, _valid(pages={}))
            with self.assertRaises(proof_local.Invalid):
                proof_local.load(repo.declaration)

    def test_an_absent_file_raises_file_not_found(self):
        with TemporaryDirectory() as tmp:
            repo = _Repo(tmp)
            with self.assertRaises(FileNotFoundError):
                proof_local.load(repo.declaration)


# ---------------------------------------------------------------------------
# read — the three GitHub output keys, and nothing else
# ---------------------------------------------------------------------------

def _outputs(text: str) -> list:
    return [line.split("=", 1) for line in text.splitlines()]


class ReadTest(unittest.TestCase):

    def _read(self, data):
        with TemporaryDirectory() as tmp:
            repo = _Repo(tmp, data)
            out = Path(tmp) / "github-output"
            out.write_text("earlier=kept\n")
            done = _run("read", "--declaration", str(repo.declaration),
                        "--github-output", str(out))
            return done, out.read_text()

    def test_a_declared_repo_writes_exactly_the_three_keys(self):
        done, text = self._read(_valid())
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(_outputs(text), [["earlier", "kept"],
                                          ["declared", "true"],
                                          ["node_dir", "web"],
                                          ["surface", "portals"]])

    def test_no_node_dir_writes_it_empty(self):
        done, text = self._read(_valid(node_dir=None))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("node_dir=\n", text)

    def test_no_file_writes_declared_false(self):
        done, text = self._read(None)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(_outputs(text), [["earlier", "kept"],
                                          ["declared", "false"],
                                          ["node_dir", ""],
                                          ["surface", ""]])

    def test_a_value_cannot_smuggle_an_output_line(self):
        for over in ({"node_dir": "web\nproof_ok=true"},
                     {"node_dir": "web\rproof_ok=true"}):
            with self.subTest(over=over):
                done, text = self._read(_valid(**over))
                self.assertEqual(done.returncode, 1)
                self.assertNotIn("proof_ok", text)
                self.assertEqual(text, "earlier=kept\n")

    def test_a_surface_name_cannot_smuggle_an_output_line(self):
        name = "portals\nproof_ok=true"
        release = {"surfaces": {name: {"tag_series": ["v*"]}}}
        with TemporaryDirectory() as tmp:
            repo = _Repo(tmp, _valid(surface=name), release=release)
            out = Path(tmp) / "github-output"
            done = _run("read", "--declaration", str(repo.declaration),
                        "--github-output", str(out))
            text = out.read_text() if out.exists() else ""
        self.assertEqual(done.returncode, 1)
        self.assertNotIn("proof_ok", text)

    def test_extra_consumer_keys_never_reach_the_output(self):
        done, text = self._read(_valid(proof_ok=True))
        self.assertEqual(done.returncode, 1)
        self.assertEqual(text, "earlier=kept\n")

    def test_the_output_writer_names_only_the_contract_keys(self):
        self.assertEqual(proof_local.OUTPUT_KEYS, ("declared", "node_dir", "surface"))


# ---------------------------------------------------------------------------
# resolve / released() — the release train's own terms, on real tags
# ---------------------------------------------------------------------------

class ReleasedTest(unittest.TestCase):

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = _Repo(self._tmp.name, _valid())
        root = self.repo.root
        _git(root, "init", "-q", "-b", "main")
        _git(root, "add", ".")
        self.first = _commit(root, "first")
        _git(root, "tag", "portal-v0.9.0")          # the legacy glob
        self.second = _commit(root, "second")
        self.third = _commit(root, "third — main moves past the release")

    def _tag(self, name, sha, when, annotated=False):
        env = {**os.environ, "GIT_COMMITTER_DATE": when,
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        args = ["-a", "-m", name] if annotated else []
        subprocess.run(["git", "-C", str(self.repo.root), "tag", *args, name, sha],
                       check=True, env=env, capture_output=True)

    def test_the_newest_tag_across_the_series_is_the_released_commit(self):
        self._tag("portals-v1.0.0", self.second, "2030-01-01T00:00:00Z",
                  annotated=True)
        found = proof_local.released(self.repo.root,
                                     proof_local.load(self.repo.declaration))
        self.assertEqual(found, proof_local.Released(self.second, "portals-v1.0.0",
                                                     "portals"))
        self.assertNotEqual(found.sha, self.third, "main is not released")

    def test_it_reads_through_release_train(self):
        self._tag("portals-v1.0.0", self.second, "2030-01-01T00:00:00Z",
                  annotated=True)
        with mock.patch.object(release_train, "newest_tag",
                               wraps=release_train.newest_tag) as newest, \
             mock.patch.object(release_train, "surfaces",
                               wraps=release_train.surfaces) as surfaces:
            found = proof_local.released(self.repo.root,
                                         proof_local.load(self.repo.declaration))
        self.assertEqual(found.tag, "portals-v1.0.0")
        surfaces.assert_called()
        newest.assert_called_once_with(self.repo.root,
                                       ["portals-v*", "portal-v*"])

    def test_a_surface_with_no_tag_is_none(self):
        found = proof_local.released(
            self.repo.root, proof_local.load(self.repo.declaration)._replace(
                surface="pipeline-channel"))
        self.assertIsNone(found)

    def test_the_cli_prints_the_released_line(self):
        self._tag("portals-v1.0.0", self.second, "2030-01-01T00:00:00Z",
                  annotated=True)
        done = _run("resolve", "--repo-root", str(self.repo.root))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(done.stdout.strip(),
                         f"proof-local: released — portals at {self.second} "
                         f"(portals-v1.0.0)")

    def test_the_cli_says_no_release_yet_and_exits_2(self):
        decl = _valid(surface="pipeline-channel")
        self.repo.declaration.write_text(json.dumps(decl))
        done = _run("resolve", "--declaration", str(self.repo.declaration),
                    "--repo-root", str(self.repo.root))
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertEqual(done.stdout.strip(),
                         "proof-local: no release yet for surface pipeline-channel")

    def test_the_cli_refuses_an_invalid_declaration(self):
        self.repo.declaration.write_text(json.dumps(_valid(pages={})))
        done = _run("resolve", "--repo-root", str(self.repo.root))
        self.assertEqual(done.returncode, 1)
        self.assertTrue(done.stdout.startswith("proof-local: invalid — "))


# ---------------------------------------------------------------------------
# The runner's own python3 — nothing pip-installed
# ---------------------------------------------------------------------------

class ImportsTest(unittest.TestCase):

    def test_it_imports_only_the_standard_library_and_release_train(self):
        tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0 and node.module:
                    names.add(node.module.split(".")[0])
        foreign = {n for n in names
                   if n not in sys.stdlib_module_names and n != "release_train"}
        self.assertEqual(foreign, set())
        self.assertIn("release_train", names)

    def test_it_runs_with_site_packages_switched_off(self):
        with TemporaryDirectory() as tmp:
            repo = _Repo(tmp, _valid())
            done = subprocess.run(
                [sys.executable, "-I", "-S", str(SCRIPT), "check",
                 "--declaration", str(repo.declaration)],
                capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


# ---------------------------------------------------------------------------
# The documents
# ---------------------------------------------------------------------------

def _doc_example() -> dict:
    text = DOC.read_text(encoding="utf-8")
    found = re.search(r"^## A worked example.*?```json\n(.*?)```", text, re.S | re.M)
    assert found, "docs/proof-local-run.md must carry `## A worked example` with a json block"
    return json.loads(found.group(1))


def _loose(needle: str) -> re.Pattern:
    return re.compile(r"\s+".join(re.escape(w) for w in needle.split()), re.I)


class DocTest(unittest.TestCase):

    def setUp(self):
        self.text = DOC.read_text(encoding="utf-8")

    def assertSays(self, phrase: str):
        self.assertRegex(self.text, _loose(phrase))

    def test_it_carries_the_schema_and_the_cli(self):
        for key in ("surface", "node_dir", "setup", "start", "ready_url",
                    "ready_timeout_seconds", "pages", "login"):
            self.assertIn(f"`{key}`", self.text)
        self.assertSays("python3 scripts/proof_local.py check")
        self.assertSays("python3 scripts/proof_local.py read")
        self.assertSays("python3 scripts/proof_local.py resolve")
        self.assertSays("proof-local: no release yet for surface")

    def test_it_states_the_two_trees_rule(self):
        self.assertSays("`setup` and `start` run in the released worktree")
        self.assertSays("`login.command` runs from the root of the default-branch checkout")
        self.assertSays("`PROOF_LOGIN_FILE`")
        self.assertSays("brings its own runner and dependencies")

    def test_it_states_the_one_line_output_rule(self):
        self.assertSays("the last non-empty line of its standard output")
        self.assertSays("earlier lines are ignored")
        self.assertSays("standard error is never quoted")

    def test_it_names_the_interpreter(self):
        self.assertSays("the runner's own `python3`")
        self.assertSays("no setup-python step")

    def test_it_leaves_the_runtime_and_the_workflow_to_the_siblings(self):
        self.assertSays("DRE-6025")
        self.assertSays("DRE-6047")

    def test_it_describes_the_session_kind(self):
        for name in ("`login.session`", "`local_storage`", "`cookies`", "`from`"):
            self.assertIn(name, self.text)
        self.assertSays("`login` carries both `form` and `session` — declare one")
        self.assertSays("`login` must carry one of `form` or `session` beside `command`")
        self.assertSays("its own sign-in page offers no password path")
        self.assertSays("`[A-Za-z_][A-Za-z0-9_]*`")
        self.assertSays("before the first declared page is opened")

    def test_it_keeps_the_login_file_rules_for_the_session_kind(self):
        section = _section(self.text, "The `session` kind")
        for phrase in ("written 0600", "never printed",
                       "never put on a command line",
                       "removed at the end of the run", "`proof_browser.py stop`",
                       "runs once"):
            self.assertRegex(section, _loose(phrase))

    def test_the_session_worked_example_is_accepted(self):
        section = _section(self.text, "The `session` kind")
        blocks = re.findall(r"```json\n(.*?)```", section, re.S)
        example = json.loads(blocks[-1])
        found = proof_local.validate(
            example, {"surfaces": {example["surface"]: {"tag_series": ["v*"]}}})
        self.assertEqual(len(found.login["session"]["local_storage"]), 2)
        self.assertEqual(len(found.login["session"]["cookies"]), 1)
        self.assertNotIn("form", found.login)
        self.assertNotIn("eb_", blocks[-1], "the example names Portico's keys")


def _section(text: str, title: str) -> str:
    found = re.search(rf"^## {re.escape(title)}\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    assert found, f"docs/proof-local-run.md must carry `## {title}`"
    return found.group(1)


class PlannerBriefTest(unittest.TestCase):

    def setUp(self):
        text = BRIEF.read_text(encoding="utf-8")
        found = re.search(r"^## Every epic ends with a proof card.*?(?=^## )",
                          text, re.S | re.M)
        self.section = found.group(0)

    def test_the_local_screen_example_names_a_page_key(self):
        self.assertRegex(self.section, _loose(
            "Local screen: on the `home` page of a local run of the released commit"))
        self.assertRegex(self.section, _loose("`.github/bureau/proof-local.json`"))

    def test_a_criterion_naming_no_declared_page_is_not_observed(self):
        self.assertRegex(self.section, _loose("names no declared page is `Not observed.`"))


if __name__ == "__main__":
    unittest.main()
