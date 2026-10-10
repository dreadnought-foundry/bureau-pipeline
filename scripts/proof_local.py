#!/usr/bin/env python3
"""How a repo's released commit runs locally, as the repo declares it (DRE-6024).

A proof run that opens a browser on "a local run of the released commit" needs
the repo to say three things nothing in the pipeline can guess: how the
checked-out commit is served locally, which release surface decides what "the
released commit" is, and which page a `Local screen:` criterion opens. The
consumer repo says them in `.github/bureau/proof-local.json`, beside the
`setup.sh` and `release.json` the proof workflow already reads; this module is
the file's one reader. `docs/proof-local-run.md` is the human contract.

Nothing here starts a process or opens a browser — the runtime is DRE-6025's
and DRE-6047's. It runs on the runner's own `python3`, like every other
`.bureau-pipeline/scripts/…` step in `proof-task.yml`, so it imports the
standard library and `release_train` and nothing pip-installed.

The released commit is READ, never assumed, in the release train's own terms:
`surface` is a key of `release_train.surfaces()` over the repo's
`release.json`, and the released commit is the newest tag across that
surface's `tag_series` (`release_train.newest_tag`). `main` is not released: a
surface with no tag resolves to nothing, never to the default branch.

CLI:
    proof_local.py check   [--declaration PATH]
    proof_local.py read    [--declaration PATH] --github-output PATH
    proof_local.py resolve [--declaration PATH] [--repo-root DIR]

`check` exits 0 for `ok` and `none`, 1 for `invalid`. `read` writes exactly
`declared`, `node_dir` and `surface` as GitHub output lines. `resolve` exits 0
with the released commit, 2 when the surface has no release yet (or the repo
declares nothing), 1 when the declaration is invalid.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path, PurePosixPath
from typing import NamedTuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import release_train  # noqa: E402

#: Opens every line the CLI prints.
TAG = "proof-local"

DECLARATION = ".github/bureau/proof-local.json"
RELEASE_JSON = ".github/bureau/release.json"

DEFAULT_READY_TIMEOUT = 180
REQUIRED = ("surface", "start", "ready_url", "pages")
OPTIONAL = ("node_dir", "setup", "ready_timeout_seconds", "login")
#: `login` carries `command` and exactly one of these (DRE-6536): `form` is
#: filled with the file's username and password, `session` is planted in the
#: browser from the file's `session` values and fills no form.
LOGIN_KINDS = ("form", "session")
LOGIN_KEYS = ("command", "form", "session")
FORM_FIELDS = ("username", "password", "submit")
SESSION_KEYS = ("local_storage", "cookies")
STORAGE_ENTRY_KEYS = ("key", "from")
COOKIE_ENTRY_KEYS = ("name", "from")
#: The GitHub output keys `read` writes — these and no others.
OUTPUT_KEYS = ("declared", "node_dir", "surface")

#: The local run is local: plain http, the loopback address, an explicit
#: port, and a path. `localhost` is refused because it can resolve to `::1`
#: while the app listens on 127.0.0.1.
_READY_URL = re.compile(r"http://127\.0\.0\.1:(\d{1,5})(/[^\s@\\]*)")
_PAGE_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
#: A `from`: a field of the session file's `session` object.
_FIELD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class Invalid(ValueError):
    """The declaration is wrong; the message names what is wrong."""


class Declaration(NamedTuple):
    surface: str
    node_dir: str | None
    setup: str | None
    start: str
    ready_url: str
    ready_timeout_seconds: int
    pages: dict
    login: dict | None


class Released(NamedTuple):
    sha: str
    tag: str
    surface: str


# ---------------------------------------------------------------------------
# The rules
# ---------------------------------------------------------------------------

def _text(data: dict, key: str, label: str | None = None) -> str:
    """A required one-line, non-empty string."""
    label = label or key
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise Invalid(f"`{label}` must be a non-empty string")
    if _CONTROL.search(value):
        raise Invalid(f"`{label}` must be one line with no control characters")
    return value


def _unknown(data: dict, known, where: str) -> None:
    extra = sorted(set(data) - set(known))
    if extra:
        raise Invalid(f"{where} carries unknown key(s) {', '.join(extra)} — "
                      f"the keys are {', '.join(known)}")


def _declared(release) -> dict:
    """`release_train.surfaces` over `release`, with a malformed file named
    as `Invalid` — `surfaces` only reads, and assumes the shapes it reads."""
    try:
        return release_train.surfaces(release)
    except (AttributeError, TypeError, ValueError) as exc:
        raise Invalid(f"{RELEASE_JSON} is malformed: {exc}") from None


def _surface(data: dict, release: dict) -> str:
    name = _text(data, "surface")
    declared = _declared(release)
    if name not in declared:
        named = ", ".join(declared) or "none"
        raise Invalid(f"surface {name} is not a surface of {RELEASE_JSON} "
                      f"(declared: {named})")
    return name


def _node_dir(data: dict) -> str | None:
    if "node_dir" not in data:
        return None
    value = _text(data, "node_dir")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise Invalid(f"`node_dir` {value} must be a directory inside the "
                      f"released worktree — relative, with no `..`")
    return value


def _ready_url(data: dict) -> str:
    value = data.get("ready_url")
    found = _READY_URL.fullmatch(value) if isinstance(value, str) else None
    if not found or not 0 < int(found.group(1)) < 65536:
        raise Invalid(f"`ready_url` {value!r} must be http://127.0.0.1:<port>/… "
                      f"— the local run is local")
    return value


def _timeout(data: dict) -> int:
    value = data.get("ready_timeout_seconds", DEFAULT_READY_TIMEOUT)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise Invalid(f"`ready_timeout_seconds` {value!r} must be a positive "
                      f"whole number of seconds")
    return value


def _pages(data: dict) -> dict:
    pages = data.get("pages")
    if not isinstance(pages, dict):
        raise Invalid("`pages` must be an object of page key → path")
    if not pages:
        raise Invalid("`pages` is empty — declare at least one page a "
                      "`Local screen:` criterion can open")
    for key, path in pages.items():
        if not _PAGE_KEY.fullmatch(key):
            raise Invalid(f"page key {key!r} must be letters, digits, `_`, "
                          f"`-` or `.`")
        if not isinstance(path, str) or not path.startswith("/"):
            raise Invalid(f"`pages.{key}` path {path!r} must open with `/`")
        if path.startswith(("//", "/\\")) or _CONTROL.search(path) or " " in path:
            raise Invalid(f"`pages.{key}` path {path!r} must stay on the local run "
                          f"— no `//`, `/\\`, spaces or control characters")
    return dict(pages)


def _form(form) -> dict:
    if not isinstance(form, dict):
        raise Invalid("`login.form` must be an object with "
                      + ", ".join(FORM_FIELDS))
    _unknown(form, FORM_FIELDS, "`login.form`")
    for field in FORM_FIELDS:
        _text(form, field, f"login.form.{field}")
    return dict(form)


def _entries(session: dict, name: str, keys: tuple) -> list:
    """One list of `login.session`, its entries in declared order. `keys[0]`
    is what the entry sets — a localStorage key or a cookie name."""
    where = f"login.session.{name}"
    entries = session.get(name, [])
    if not isinstance(entries, list):
        raise Invalid(f"`{where}` must be a list of objects with "
                      + " and ".join(keys))
    seen = set()
    for i, entry in enumerate(entries):
        at = f"{where}[{i}]"
        if not isinstance(entry, dict):
            raise Invalid(f"`{at}` must be an object with " + " and ".join(keys))
        _unknown(entry, keys, f"`{at}`")
        target = _text(entry, keys[0], f"{at}.{keys[0]}")
        if target in seen:
            raise Invalid(f"`{at}.{keys[0]}` {target} is declared twice in `{where}`")
        seen.add(target)
        field = entry.get("from")
        if not isinstance(field, str) or not _FIELD.fullmatch(field):
            raise Invalid(f"`{at}.from` {field!r} must name a field of the session "
                          f"file's `session` — letters, digits and `_`, not "
                          f"opening with a digit")
    return [dict(entry) for entry in entries]


def _session(session) -> dict:
    if not isinstance(session, dict):
        raise Invalid("`login.session` must be an object with "
                      + " and/or ".join(SESSION_KEYS))
    _unknown(session, SESSION_KEYS, "`login.session`")
    planted = {
        "local_storage": _entries(session, "local_storage", STORAGE_ENTRY_KEYS),
        "cookies": _entries(session, "cookies", COOKIE_ENTRY_KEYS),
    }
    if not any(planted.values()):
        raise Invalid("`login.session` declares no entry — name at least one "
                      "in `local_storage` or `cookies`")
    return planted


def _login(data: dict) -> dict | None:
    if "login" not in data:
        return None
    login = data["login"]
    if not isinstance(login, dict):
        raise Invalid("`login` must be an object with `command` and one of "
                      "`form` or `session`")
    _unknown(login, LOGIN_KEYS, "`login`")
    _text(login, "command", "login.command")
    kinds = [kind for kind in LOGIN_KINDS if kind in login]
    if len(kinds) > 1:
        raise Invalid("`login` carries both `form` and `session` — declare one")
    if not kinds:
        raise Invalid("`login` must carry one of `form` or `session` beside `command`")
    if kinds == ["form"]:
        return {"command": login["command"], "form": _form(login["form"])}
    return {"command": login["command"], "session": _session(login["session"])}


def validate(data, release: dict) -> Declaration:
    """`data` (the parsed file) as a `Declaration`, or `Invalid` naming the
    first thing wrong. `release` is the parsed `release.json`."""
    if not isinstance(data, dict):
        raise Invalid("the file must hold one JSON object")
    _unknown(data, REQUIRED + OPTIONAL, "the declaration")
    return Declaration(
        surface=_surface(data, release),
        node_dir=_node_dir(data),
        setup=_text(data, "setup") if "setup" in data else None,
        start=_text(data, "start"),
        ready_url=_ready_url(data),
        ready_timeout_seconds=_timeout(data),
        pages=_pages(data),
        login=_login(data),
    )


def load(path=DECLARATION, release=None) -> Declaration:
    """Read and validate the declaration at `path`.

    `release` is the `release.json` the surface is checked against; by
    default the one beside the declaration, which is where a repo keeps it.
    An absent declaration raises `FileNotFoundError` — that is the `none`
    answer, never an `Invalid`.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise Invalid(f"{DECLARATION} is not valid JSON: {exc}") from None
    release_path = Path(release) if release else path.parent / "release.json"
    try:
        release_data = release_train.load(release_path)
    except json.JSONDecodeError as exc:
        raise Invalid(f"{RELEASE_JSON} is not valid JSON: {exc}") from None
    return validate(data, release_data)


# ---------------------------------------------------------------------------
# The released commit
# ---------------------------------------------------------------------------

def released(repo_root, declaration) -> Released | None:
    """The newest tag across the declared surface's `tag_series`, with the
    commit it points at — or None when the surface has no tag yet."""
    if not isinstance(declaration, Declaration):
        declaration = load(declaration)
    root = Path(repo_root)
    declared = _declared(release_train.load(root / RELEASE_JSON))
    surface = declared.get(declaration.surface)
    if surface is None:
        raise Invalid(f"surface {declaration.surface} is not a surface of "
                      f"{RELEASE_JSON}")
    tag, _ = release_train.newest_tag(repo_root, surface.tag_series)
    if not tag:
        return None
    sha = release_train.tag_target(repo_root, tag)
    if not sha:
        raise RuntimeError(f"tag {tag} resolves to no commit")
    return Released(sha, tag, declaration.surface)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _none() -> str:
    return f"{TAG}: none — no {DECLARATION}"


def _invalid(exc: Exception) -> str:
    return f"{TAG}: invalid — {exc}"


def cmd_check(args) -> int:
    try:
        found = load(args.declaration)
    except FileNotFoundError:
        print(_none())
        return 0
    except Invalid as exc:
        print(_invalid(exc))
        return 1
    login = "declared" if found.login else "none"
    print(f"{TAG}: ok — surface {found.surface}, {len(found.pages)} page(s), "
          f"login {login}")
    return 0


def cmd_read(args) -> int:
    try:
        found = load(args.declaration)
        values = {"declared": "true", "node_dir": found.node_dir or "",
                  "surface": found.surface}
    except FileNotFoundError:
        values = {"declared": "false", "node_dir": "", "surface": ""}
    except Invalid as exc:
        print(_invalid(exc))
        return 1
    lines = []
    for key in OUTPUT_KEYS:
        if _CONTROL.search(values[key]):
            print(_invalid(f"{key} must be one line"))
            return 1
        lines.append(f"{key}={values[key]}\n")
    with open(args.github_output, "a", encoding="utf-8") as fh:
        fh.writelines(lines)
    print(f"{TAG}: read — " + ", ".join(f"{k}={values[k]}" for k in OUTPUT_KEYS))
    return 0


def cmd_resolve(args) -> int:
    declaration = args.declaration or os.path.join(args.repo_root, DECLARATION)
    try:
        found = load(declaration)
        result = released(args.repo_root, found)
    except FileNotFoundError:
        print(_none())
        return 2
    except Invalid as exc:
        print(_invalid(exc))
        return 1
    if result is None:
        print(f"{TAG}: no release yet for surface {found.surface}")
        return 2
    print(f"{TAG}: released — {result.surface} at {result.sha} ({result.tag})")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="validate the declaration")
    check.add_argument("--declaration", default=DECLARATION)
    check.set_defaults(func=cmd_check)

    read = sub.add_parser("read", help="write the workflow's output keys")
    read.add_argument("--declaration", default=DECLARATION)
    read.add_argument("--github-output", required=True)
    read.set_defaults(func=cmd_read)

    resolve = sub.add_parser("resolve", help="the released commit to run")
    resolve.add_argument("--declaration", default=None,
                         help=f"default: <repo-root>/{DECLARATION}")
    resolve.add_argument("--repo-root", default=".")
    resolve.set_defaults(func=cmd_resolve)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
