"""RED-first: the live roster reader builds every form of each person's name, holds them in memory only (DRE-8165).

A committed list of names would itself be the leak, so `scripts/name_roster.py` reads the roster live — Linear's
users and the GitHub organization's members — and builds the forms the redaction seam matches. These tests hold:

  A. Every kind of form is built for a synthetic person; the one admin's forms carry `[the CEO]` and everyone
     else's `[a person]`; the non-human Linear users `config/linear-identities.json` declares and a `[bot]` login
     produce no form.
  B. No name reaches stdout, stderr, a log record or disk: the roster's `repr` and `str`, the `check` CLI's output
     and every exception message carry counts and read names only, and the module opens no file for writing.
  C. A failed Linear read, a failed members read, a failed profile read, zero people, and zero or two admins each
     raise `RosterUnreadable`, and the `check` CLI prints `name-roster: unreadable — …` and exits 2.
  D. Every fixture is synthetic: each name-shaped word in the card's two files carries the marker `synth`.
  E. `config/proof-reach.json` keeps the `name-roster:` line.

Run: cd bureau-pipeline && python3 -m pytest tests/test_name_roster.py -v
"""
from __future__ import annotations

import ast
import builtins
import contextlib
import io
import json
import logging
import os
import re
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")

import name_roster  # noqa: E402
import proof_reach  # noqa: E402

#: The marker every synthetic name, display name, login and email carries.
MARKER = "synth"

OWNER = "synth-org"

#: The admin, as Linear spells them, and their GitHub member.
CEO = {"name": "Synthora Synthbridge", "displayName": "synthora",
       "email": "synthora.synthbridge@synthetic.invalid", "admin": True, "active": True}
CEO_LOGIN = "synthbridge-synth"
CEO_PROFILE = "Synthora Q Synthbridge"

#: A person who is not the admin, and their GitHub member.
PERSON = {"name": "Synthia Synthwood", "displayName": "synthia",
          "email": "synthia.synthwood@synthetic.invalid", "admin": False, "active": False}
PERSON_LOGIN = "synthwood-synth"
PERSON_PROFILE = "Synthia Synthwood"

#: A GitHub member no Linear user matches: a person of their own.
LONER_LOGIN = "synthkell"
LONER_PROFILE = "Synthkell Synthorne"

#: A login the caller hands over (the run's actor), not a member of the org.
ACTOR_LOGIN = "synthactor"
ACTOR_PROFILE = "Synthactor Synthmere"

BOT_LOGIN = "synthbot[bot]"

#: Every synthetic string that must never be printed, logged or written.
SECRETS = (
    CEO["name"], CEO["displayName"], CEO["email"], CEO_LOGIN, CEO_PROFILE, "Synthbridge",
    PERSON["name"], PERSON["displayName"], PERSON["email"], PERSON_LOGIN, "Synthwood",
    LONER_LOGIN, LONER_PROFILE, "Synthorne", ACTOR_LOGIN, ACTOR_PROFILE,
)


def _non_humans() -> list[dict]:
    """The declared non-human users, read off the config rather than restated."""
    doc = json.loads((ROOT / "config" / "linear-identities.json").read_text(encoding="utf-8"))
    return [{"name": i["display_name"], "displayName": i["display_name"],
             "email": f"{i['name']}@synthetic.invalid", "admin": False, "active": True}
            for i in doc["identities"]]


def _gql(users: list[dict], *, pages: int = 2, fail: bool = False):
    """A stand-in for `linear_ops.gql`, serving `users` across `pages` pages."""
    size = max(1, -(-len(users) // pages))
    calls = []

    def gql(query, variables=None):
        calls.append((query, dict(variables or {})))
        if fail:
            raise RuntimeError("linear error: synthetic outage")
        after = (variables or {}).get("after")
        start = int(after) if after else 0
        chunk = users[start:start + size]
        more = start + size < len(users)
        return {"users": {"nodes": chunk,
                          "pageInfo": {"hasNextPage": more,
                                       "endCursor": str(start + size) if more else None}}}

    gql.calls = calls
    return gql


def _gh(members: list[str], profiles: dict[str, str | None], *, fail_members=False,
        fail_profile: str | None = None, page_size: int = 2):
    """A stand-in for the `gh api` runner: a path in, parsed JSON out."""
    calls = []

    def gh(path):
        calls.append(path)
        m = re.fullmatch(r"orgs/([^/]+)/members\?per_page=(\d+)&page=(\d+)", path)
        if m:
            if fail_members:
                raise RuntimeError("gh: HTTP 403 synthetic")
            per = min(int(m.group(2)), page_size)
            page = int(m.group(3))
            chunk = members[(page - 1) * per:page * per]
            return [{"login": login} for login in chunk]
        m = re.fullmatch(r"users/(.+)", path)
        if m:
            login = m.group(1)
            if fail_profile == login:
                raise RuntimeError(f"gh: HTTP 404 for {login}")
            return {"login": login, "name": profiles.get(login)}
        raise AssertionError(f"unexpected gh path {path}")

    gh.calls = calls
    gh.page_size = page_size
    return gh


def _world(**over):
    users = over.pop("users", [CEO, PERSON, *_non_humans()])
    members = over.pop("members", [CEO_LOGIN, PERSON_LOGIN, LONER_LOGIN, BOT_LOGIN])
    profiles = over.pop("profiles", {CEO_LOGIN: CEO_PROFILE, PERSON_LOGIN: PERSON_PROFILE,
                                     LONER_LOGIN: LONER_PROFILE, ACTOR_LOGIN: ACTOR_PROFILE,
                                     BOT_LOGIN: "Synthbot"})
    gql = _gql(users, fail=over.pop("fail_linear", False))
    gh = _gh(members, profiles, **over)
    return gql, gh


def _read(gql, gh, extra=(ACTOR_LOGIN, "synthbot2[bot]")):
    return name_roster.read(gql=gql, gh=gh, owner=OWNER, extra_logins=extra,
                            page_size=gh.page_size)


def _hits(roster, text: str) -> list:
    return [f for f in roster.forms if f.pattern.search(text)]


# --------------------------------------------------------------------------- #
# A. the forms                                                                 #
# --------------------------------------------------------------------------- #

def test_the_contract_constants():
    assert name_roster.ROLE_CEO == "[the CEO]"
    assert name_roster.ROLE_PERSON == "[a person]"
    assert name_roster.KINDS == ("full-name", "display-surname", "surname", "display", "login", "email")
    assert issubclass(name_roster.RosterUnreadable, RuntimeError)
    assert name_roster.TAG == "name-roster:"


def test_every_kind_is_built_for_the_admin_and_carries_the_ceo_role():
    roster = _read(*_world())
    ceo_texts = {
        "full-name": [CEO["name"], CEO_PROFILE],
        "display-surname": ["synthora Synthbridge"],
        "surname": ["Synthbridge"],
        "display": ["synthora"],
        "login": [CEO_LOGIN],
        "email": [CEO["email"]],
    }
    for kind, texts in ceo_texts.items():
        for text in texts:
            hits = [f for f in roster.forms if f.kind == kind and f.pattern.fullmatch(text)]
            assert hits, f"no {kind} form matches the admin's {kind}"
            assert {f.role for f in hits} == {name_roster.ROLE_CEO}
    assert {f.kind for f in roster.forms} == set(name_roster.KINDS)


def test_every_kind_is_built_for_a_person_and_carries_the_person_role():
    roster = _read(*_world())
    person_texts = {
        "full-name": [PERSON["name"]],
        "display-surname": ["synthia Synthwood"],
        "surname": ["Synthwood"],
        "display": ["synthia"],
        "login": [PERSON_LOGIN],
        "email": [PERSON["email"]],
    }
    for kind, texts in person_texts.items():
        for text in texts:
            hits = [f for f in roster.forms if f.kind == kind and f.pattern.fullmatch(text)]
            assert hits, f"no {kind} form matches the person's {kind}"
            assert {f.role for f in hits} == {name_roster.ROLE_PERSON}


def test_an_unlinked_member_and_an_extra_login_are_people_of_their_own():
    roster = _read(*_world())
    for text in (LONER_LOGIN, LONER_PROFILE, ACTOR_LOGIN, ACTOR_PROFILE):
        hits = _hits(roster, f"said {text} today")
        assert hits and {f.role for f in hits} == {name_roster.ROLE_PERSON}
    # Two Linear people, the unlinked member, and the extra login.
    assert roster.people == 4
    assert roster.counts["linear"] == 2
    assert roster.counts["github"] == 4
    assert roster.counts["forms"] == len(roster.forms)


def test_non_humans_and_bots_produce_no_form():
    roster = _read(*_world())
    for user in _non_humans():
        assert not _hits(roster, f"ran as {user['name']} via {user['email']}"), user["name"]
    for bot in (BOT_LOGIN, "synthbot2[bot]", "Synthbot"):
        assert not _hits(roster, f"pushed by {bot} today")


def test_bot_profiles_are_never_read():
    gql, gh = _world()
    _read(gql, gh)
    assert not [p for p in gh.calls if p.startswith("users/") and p.endswith("[bot]")]


def test_every_member_page_and_every_linear_page_is_read():
    members = [CEO_LOGIN, PERSON_LOGIN, LONER_LOGIN, "synthfourth", "synthfifth"]
    gql, gh = _world(members=members)
    roster = _read(gql, gh, extra=())
    assert len(gql.calls) == 2, "both Linear pages"
    assert "includeDisabled: true" in gql.calls[0][0]
    for field in ("name", "displayName", "email", "admin", "active"):
        assert field in gql.calls[0][0]
    pages = [p for p in gh.calls if p.startswith(f"orgs/{OWNER}/members")]
    assert len(pages) == 3, "two full pages of two, one short page"
    assert roster.counts["github"] == 5


def test_display_is_case_sensitive_and_every_other_kind_is_not():
    roster = _read(*_world())
    display = [f for f in roster.forms if f.kind == "display"]
    assert display and all(not f.pattern.flags & re.IGNORECASE for f in display)
    others = [f for f in roster.forms if f.kind != "display"]
    assert others and all(f.pattern.flags & re.IGNORECASE for f in others)
    assert _hits(roster, "SYNTHBRIDGE wrote")
    assert not [f for f in roster.forms if f.kind == "display" and f.pattern.search("SYNTHIA")]


def test_forms_match_whole_words_only():
    roster = _read(*_world())
    assert not _hits(roster, "the Synthwoodshed and asynthia")
    assert _hits(roster, "(Synthwood)")


def test_forms_are_longest_literal_first():
    roster = _read(*_world())

    def at(kind, text):
        return next(i for i, f in enumerate(roster.forms)
                    if f.kind == kind and f.pattern.fullmatch(text))

    assert at("full-name", CEO["name"]) < at("display-surname", "synthora Synthbridge") \
        < at("surname", "Synthbridge") < at("display", "synthora")
    assert at("email", CEO["email"]) < at("login", CEO_LOGIN)


def test_a_shared_surname_links_on_casefold():
    profiles = {CEO_LOGIN: "synthora SYNTHBRIDGE", PERSON_LOGIN: PERSON_PROFILE,
                LONER_LOGIN: LONER_PROFILE}
    roster = _read(*_world(profiles=profiles), extra=())
    hits = [f for f in roster.forms if f.kind == "login" and f.pattern.fullmatch(CEO_LOGIN)]
    assert {f.role for f in hits} == {name_roster.ROLE_CEO}
    assert roster.people == 3


def test_a_member_with_no_profile_name_keeps_their_login():
    profiles = {CEO_LOGIN: CEO_PROFILE, PERSON_LOGIN: PERSON_PROFILE, LONER_LOGIN: None}
    roster = _read(*_world(profiles=profiles), extra=())
    hits = _hits(roster, f"by {LONER_LOGIN}")
    assert hits and {f.role for f in hits} == {name_roster.ROLE_PERSON}


# --------------------------------------------------------------------------- #
# B. nothing printed, logged or written                                         #
# --------------------------------------------------------------------------- #

class _Records(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.seen = []

    def emit(self, record):
        self.seen.append(record.getMessage())


@contextlib.contextmanager
def _watched():
    """Capture stdout, stderr and every log record, and refuse every write-mode open."""
    real_open = builtins.open
    writes = []

    def guarded(file, mode="r", *a, **k):
        if any(c in mode for c in "wax+"):
            writes.append(str(file))
            raise AssertionError(f"the roster opened {file!r} for writing")
        return real_open(file, mode, *a, **k)

    handler = _Records()
    root = logging.getLogger()
    old_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    out, err = io.StringIO(), io.StringIO()
    try:
        with mock.patch.object(builtins, "open", guarded), \
                mock.patch.object(io, "open", guarded), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            yield {"out": out, "err": err, "logs": handler.seen, "writes": writes}
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)


def _no_secret(text: str):
    low = text.casefold()
    for s in SECRETS:
        assert s.casefold() not in low, "a synthetic name reached the output"
    assert MARKER not in low.replace(OWNER, ""), "a synthetic string reached the output"


def test_repr_and_str_carry_counts_only():
    roster = _read(*_world())
    for text in (repr(roster), str(roster), repr(roster.forms), str(roster.forms)):
        _no_secret(text)
    assert str(roster.people) in str(roster)


def test_a_read_prints_logs_and_writes_nothing():
    gql, gh = _world()
    with _watched() as w:
        roster = _read(gql, gh)
    assert roster.people == 4
    assert w["out"].getvalue() == "" and w["err"].getvalue() == ""
    assert not w["writes"]
    for line in w["logs"]:
        _no_secret(line)


def _cli(gql, gh, argv=("check", "--owner", OWNER), env=None):
    env = {"GITHUB_ACTOR": ACTOR_LOGIN, "GITHUB_TRIGGERING_ACTOR": "synthbot2[bot]", **(env or {})}
    with mock.patch.dict(os.environ, env), _watched() as w:
        code = name_roster.main(list(argv), gql=gql, gh=gh, page_size=gh.page_size)
    return code, w


def test_the_check_cli_prints_one_count_line_and_no_name():
    code, w = _cli(*_world())
    assert code == 0
    out = w["out"].getvalue()
    assert out == "name-roster: ok — 4 people, %d forms (linear 2, github 4)\n" % (
        _read(*_world()).counts["forms"])
    _no_secret(out + w["err"].getvalue() + "\n".join(w["logs"]))
    assert not w["writes"]


def test_the_check_cli_reads_the_actor_logins_from_the_environment():
    gql, gh = _world()
    _cli(gql, gh)
    assert f"users/{ACTOR_LOGIN}" in gh.calls
    assert not [p for p in gh.calls if p.endswith("[bot]")]


# --------------------------------------------------------------------------- #
# C. an unreadable roster is never a partial one                                #
# --------------------------------------------------------------------------- #

UNREADABLE = {
    "linear": (dict(fail_linear=True), "linear users"),
    "members": (dict(fail_members=True), "github members"),
    "profile": (dict(fail_profile=PERSON_LOGIN), "github profile 2"),
    "no-people": (dict(users=_non_humans(), members=[BOT_LOGIN]), "people: 0"),
    # A token without `members: read` is answered 200 with an empty list, not
    # an error: an organization always has a member, so none is unreadable.
    "no-members": (dict(members=[]), "github members"),
    "only-bots": (dict(members=[BOT_LOGIN]), "github members"),
    "no-admin": (dict(users=[{**CEO, "admin": False}, PERSON]), "admins: 0"),
    "two-admins": (dict(users=[CEO, {**PERSON, "admin": True}]), "admins: 2"),
}


@pytest.mark.parametrize("case", sorted(UNREADABLE))
def test_an_unreadable_roster_raises_naming_the_read(case):
    over, reason = UNREADABLE[case]
    gql, gh = _world(**over)
    extra = () if case == "no-people" else (ACTOR_LOGIN,)
    with pytest.raises(name_roster.RosterUnreadable) as exc:
        _read(gql, gh, extra=extra)
    assert reason in str(exc.value)
    _no_secret(str(exc.value))
    _no_secret(repr(exc.value))
    assert exc.value.__cause__ is None and (
        exc.value.__context__ is None or exc.value.__suppress_context__), \
        "the failed read's own message (which may name a login) is not chained"


@pytest.mark.parametrize("case", sorted(UNREADABLE))
def test_the_check_cli_says_unreadable_and_exits_2(case):
    over, reason = UNREADABLE[case]
    gql, gh = _world(**over)
    env = {"GITHUB_ACTOR": "", "GITHUB_TRIGGERING_ACTOR": ""} if case == "no-people" else None
    code, w = _cli(gql, gh, env=env)
    assert code == 2
    out = w["out"].getvalue()
    assert out.startswith("name-roster: unreadable — ") and reason in out
    assert out.count("\n") == 1
    _no_secret(out + w["err"].getvalue() + "\n".join(w["logs"]))


def test_a_linear_page_that_never_advances_is_unreadable_not_partial():
    def gql(query, variables=None):
        return {"users": {"nodes": [CEO], "pageInfo": {"hasNextPage": True, "endCursor": "same"}}}
    _, gh = _world()
    with pytest.raises(name_roster.RosterUnreadable, match="linear users"):
        _read(gql, gh)


def test_a_malformed_answer_is_unreadable():
    def gql(query, variables=None):
        return {"users": None}
    _, gh = _world()
    with pytest.raises(name_roster.RosterUnreadable, match="linear users"):
        _read(gql, gh)

    def gh(path):
        return {"message": "missing"}
    gh.page_size = 2
    with pytest.raises(name_roster.RosterUnreadable, match="github members"):
        _read(_world()[0], gh)


def test_the_default_gh_runner_raises_on_a_failed_call():
    failed = mock.Mock(returncode=1, stdout="", stderr="HTTP 403")
    with mock.patch.object(name_roster.subprocess, "run", return_value=failed):
        with pytest.raises(RuntimeError):
            name_roster.gh_api("orgs/x/members")
    ok = mock.Mock(returncode=0, stdout='[{"login": "synthone"}]', stderr="")
    with mock.patch.object(name_roster.subprocess, "run", return_value=ok) as run:
        assert name_roster.gh_api("orgs/x/members") == [{"login": "synthone"}]
    assert run.call_args.args[0] == ["gh", "api", "orgs/x/members"]
    assert run.call_args.kwargs.get("capture_output") is True


# --------------------------------------------------------------------------- #
# D. synthetic fixtures only                                                    #
# --------------------------------------------------------------------------- #

#: Words a name-shaped pair in these two files may hold without the marker:
#: product names and prose, none of them a person's name.
PROSE = {
    "A", "An", "The", "No", "Every", "Each", "One", "Two", "Linear", "GitHub", "Roster", "Form",
    "CEO", "Run", "Skills", "App", "Actions", "Only", "Until", "Exactly", "Read", "Never",
}


def _name_shaped(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    pairs = re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+\b", text)
    strings = [n.value for n in ast.walk(ast.parse(text))
               if isinstance(n, ast.Constant) and isinstance(n.value, str)
               and re.fullmatch(r"[A-Z][a-z]+(?: [A-Z][a-z]+)+", n.value)]
    return pairs + strings


@pytest.mark.parametrize("path", ["scripts/name_roster.py", "tests/test_name_roster.py"])
def test_no_real_name_is_in_the_cards_files(path):
    found = []
    for pair in _name_shaped(ROOT / path):
        words = re.findall(r"[A-Za-z]+", pair)
        if any(MARKER in w.casefold() for w in words):
            assert all(MARKER in w.casefold() or w in PROSE or len(w) == 1 for w in words), pair
            continue
        if any(w in PROSE for w in words):
            continue
        found.append(pair)
    assert not found, f"name-shaped text without the synthetic marker in {path}: {found}"


def test_every_fixture_name_carries_the_marker():
    for s in SECRETS:
        assert MARKER in s.casefold()
    for word in re.findall(r"[A-Za-z]+", " ".join(SECRETS)):
        if word.casefold() not in {"invalid", "synthetic", "q"}:
            assert MARKER in word.casefold(), word


def test_the_module_holds_no_name():
    source = (ROOT / "scripts" / "name_roster.py").read_text(encoding="utf-8")
    assert MARKER not in source.casefold(), "the module carries no fixture either"
    assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", source), "the module carries no email"


# --------------------------------------------------------------------------- #
# E. the proof-reach line                                                       #
# --------------------------------------------------------------------------- #

def test_the_proof_reach_line_is_declared():
    entries = [e for e in proof_reach.load()["log_lines"] if e["tag"] == "name-roster:"]
    assert len(entries) == 1
    assert entries[0]["printed_by"] == "scripts/name_roster.py"
    assert entries[0]["constant"] == "TAG"
    assert proof_reach.check(ROOT) == []
