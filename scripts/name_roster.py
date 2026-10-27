#!/usr/bin/env python3
"""The live roster of people whose names must never reach this public repository (DRE-8165, stdlib only).

A committed list of names would itself be the leak, so the roster is read live
on every use, held in memory only, and never printed, logged or written to
disk. This module is the contract the redaction seam (`name_redact`) reads;
nothing else runs it directly.

THE READS. Linear's `users(includeDisabled: true)`, every page, through
`linear_ops.gql` with the `LINEAR_API_KEY` the calling workflow holds; the
non-human users `config/linear-identities.json` declares by `display_name` are
dropped. Then the caller organization's members (`orgs/<owner>/members`, every
page) and each member's profile name (`users/<login>`), through `gh api` as the
`GH_TOKEN` in the environment, plus any extra logins the caller hands over (a
workflow run's `GITHUB_ACTOR` and `GITHUB_TRIGGERING_ACTOR`). A login ending in
`[bot]` is never read and never a person.

The pages are walked here rather than by `linear_ops.gql_paged`: that walker
returns what it has on a cursor that never advances, and a roster that cannot
be read in full is never a partial roster. It also reads through the module's
own `gql`, where this one reads through the `gql` it is handed.

THE FORMS. For each person: the full name as Linear spells it and as the
GitHub profile spells it, the display name followed by the surname, the
surname alone, the display name alone, the login, and the email. The surname is
the last whitespace-separated token of the Linear `name`. A member is linked to
the one Linear person whose surname matches their profile name's, case-folded;
a member no single Linear person matches is a person of their own. Every form
matches whole words, case-insensitively except `display`, which is
case-sensitive so a short handle never matches an ordinary word. The forms are
longest literal first, so the redaction seam replaces a full name before the
surname inside it.

THE ROLES. The one Linear admin is the CEO and every one of their forms carries
`[the CEO]`; every other form carries `[a person]`. Exactly one admin is
expected: none, or more than one, is an unreadable roster.

UNREADABLE. Any failed or malformed read, a roster with no people, or an admin
count other than one raises `RosterUnreadable`. Its reason names the read that
failed — `linear users`, `github members`, `github profile <n>` by count,
`people: 0`, `admins: <count>` — never a name, a login or an email, and the
failed read's own message is never chained onto it, since that message can
carry the login it asked about.

THE LINE. `check` prints one line tagged `name-roster:` (`TAG`), declared in
`config/proof-reach.json` as a line a proof run may quote:

    python3 scripts/name_roster.py check --owner <org>
        name-roster: ok — <people> people, <forms> forms (linear <n>, github <m>)   exit 0
        name-roster: unreadable — <reason>                                         exit 2
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import linear_ops  # noqa: E402 — after the path insert, by design

#: The tag of the one line `check` prints (config/proof-reach.json).
TAG = "name-roster:"

ROLE_CEO = "[the CEO]"
ROLE_PERSON = "[a person]"

#: Every kind of form, in the order a tie in length is broken.
KINDS = ("full-name", "display-surname", "surname", "display", "login", "email")

IDENTITIES = ROOT / "config" / "linear-identities.json"

#: GitHub serves at most 100 members a page.
PAGE_SIZE = 100

#: One `gh api` call's ceiling, in seconds.
GH_TIMEOUT = 60

USERS_QUERY = """
query Roster($after: String) {
  users(includeDisabled: true, first: 100, after: $after) {
    nodes { name displayName email admin active }
    pageInfo { hasNextPage endCursor }
  }
}
"""


class RosterUnreadable(RuntimeError):
    """The roster could not be read in full; the reason names the read, never a name."""


class Form(NamedTuple):
    pattern: re.Pattern
    role: str
    kind: str

    def __repr__(self) -> str:
        # The pattern IS a name: a form shows what it is, never what it matches.
        return f"Form(kind={self.kind!r}, role={self.role!r})"

    __str__ = __repr__


class Roster:
    """The forms of every person's name, with counts that are safe to print."""

    def __init__(self, forms: list[Form], people: int, linear: int, github: int):
        self.forms = forms
        self.people = people
        self.counts = {"linear": linear, "github": github, "forms": len(forms)}

    def __repr__(self) -> str:
        c = self.counts
        return (f"Roster(people={self.people}, forms={c['forms']}, "
                f"linear={c['linear']}, github={c['github']})")

    __str__ = __repr__


def gh_api(path: str):
    """One `gh api` GET as the `GH_TOKEN` in the environment, parsed.

    Output is captured, never echoed: a failed call raises with its exit code
    only, because the path and gh's own message can carry a login.
    """
    done = subprocess.run(["gh", "api", path], capture_output=True, text=True,
                          timeout=GH_TIMEOUT)
    if done.returncode != 0:
        raise RuntimeError(f"gh api exited {done.returncode}")
    return json.loads(done.stdout)


def _is_bot(login: str) -> bool:
    return login.endswith("[bot]")


def _words(text) -> list[str]:
    return str(text or "").split()


def _non_humans(identities_path) -> set[str]:
    try:
        with open(identities_path, encoding="utf-8") as fh:
            doc = json.load(fh)
        names = {i["display_name"].casefold() for i in doc["identities"]}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise RosterUnreadable("linear identities") from None
    return names


def _linear_users(gql, identities_path) -> list[dict]:
    """Every human Linear user, every page; unreadable on any failure."""
    drop = _non_humans(identities_path)
    nodes: list[dict] = []
    after = None
    seen: set[str] = set()
    try:
        while True:
            conn = gql(USERS_QUERY, {"after": after})["users"]
            page = conn["nodes"]
            if not isinstance(page, list):
                raise TypeError("nodes")
            nodes.extend(page)
            info = conn.get("pageInfo") or {}
            if not info.get("hasNextPage"):
                break
            after = info.get("endCursor")
            if not after or after in seen:
                raise ValueError("a cursor that never advances")
            seen.add(after)
        people = []
        for n in nodes:
            name = " ".join(_words(n["name"]))
            display = " ".join(_words(n.get("displayName")))
            if not name or name.casefold() in drop or display.casefold() in drop:
                continue
            people.append({"name": name, "display": display,
                           "email": str(n.get("email") or "").strip(),
                           "admin": n.get("admin") is True})
    except (Exception, SystemExit):
        raise RosterUnreadable("linear users") from None
    return people


def _github_logins(gh, owner: str, extra_logins, page_size: int) -> list[str]:
    """Every member's login, every page, then the extra logins; bots skipped."""
    logins: list[str] = []
    try:
        page = 1
        while True:
            chunk = gh(f"orgs/{owner}/members?per_page={page_size}&page={page}")
            if not isinstance(chunk, list):
                raise TypeError("members")
            logins.extend(str(m["login"]) for m in chunk)
            if len(chunk) < page_size:
                break
            page += 1
    except (Exception, SystemExit):
        raise RosterUnreadable("github members") from None
    out, seen = [], set()
    for login in [*logins, *(str(x or "").strip() for x in extra_logins)]:
        if login and not _is_bot(login) and login.casefold() not in seen:
            seen.add(login.casefold())
            out.append(login)
    return out


def _profiles(gh, logins: list[str]) -> list[str]:
    """Each login's profile name ("" for none), read in order."""
    names = []
    for n, login in enumerate(logins, start=1):
        try:
            profile = gh(f"users/{login}")
            if not isinstance(profile, dict):
                raise TypeError("profile")
            names.append(" ".join(_words(profile.get("name"))))
        except (Exception, SystemExit):
            raise RosterUnreadable(f"github profile {n}") from None
    return names


def _pattern(literal: str, kind: str) -> re.Pattern:
    """Whole words; the literal's spaces match any run of whitespace."""
    body = r"\s+".join(re.escape(w) for w in literal.split())
    flags = 0 if kind == "display" else re.IGNORECASE
    return re.compile(rf"(?<!\w){body}(?!\w)", flags)


def _forms(people: list[dict]) -> list[Form]:
    """Every person's forms, deduplicated per kind, longest literal first."""
    found: dict[tuple, tuple] = {}
    for person in people:
        for kind, literal in person["literals"]:
            literal = " ".join(_words(literal))
            if not literal:
                continue
            key = (kind, literal if kind == "display" else literal.casefold())
            role = person["role"]
            if key in found and found[key][2] != role:
                # One spelling shared by the CEO and another person reads as
                # the less specific role: it may not be the CEO it names.
                role = ROLE_PERSON
            found[key] = (literal, kind, role)
    ordered = sorted(found.values(), key=lambda v: (-len(v[0]), KINDS.index(v[1])))
    return [Form(_pattern(literal, kind), role, kind) for literal, kind, role in ordered]


def read(*, gql=linear_ops.gql, gh=gh_api, owner: str, identities_path=IDENTITIES,
         extra_logins=(), page_size: int = PAGE_SIZE) -> Roster:
    """The live roster, read in full, or `RosterUnreadable`."""
    users = _linear_users(gql, identities_path)
    logins = _github_logins(gh, owner, extra_logins, page_size)
    profiles = _profiles(gh, logins)

    admins = sum(1 for u in users if u["admin"])
    by_surname: dict[str, list[dict]] = {}
    people = []
    for u in users:
        surname = u["name"].split()[-1]
        literals = [("full-name", u["name"]), ("surname", surname),
                    ("display", u["display"]), ("email", u["email"])]
        if u["display"]:
            literals.append(("display-surname", f"{u['display']} {surname}"))
        person = {"role": ROLE_CEO if u["admin"] else ROLE_PERSON, "literals": literals}
        by_surname.setdefault(surname.casefold(), []).append(person)
        people.append(person)
    for login, profile in zip(logins, profiles):
        words = profile.split()
        match = by_surname.get(words[-1].casefold(), []) if words else []
        if len(match) == 1:
            match[0]["literals"] += [("login", login), ("full-name", profile)]
            continue
        literals = [("login", login), ("full-name", profile)]
        if len(words) > 1:
            literals.append(("surname", words[-1]))
        people.append({"role": ROLE_PERSON, "literals": literals})

    if not people:
        raise RosterUnreadable("people: 0")
    if admins != 1:
        raise RosterUnreadable(f"admins: {admins}")
    return Roster(_forms(people), people=len(people), linear=len(users), github=len(logins))


def main(argv=None, *, gql=linear_ops.gql, gh=gh_api, page_size: int = PAGE_SIZE) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="read the roster and print its counts")
    check.add_argument("--owner", required=True, help="the GitHub organization")
    args = parser.parse_args(argv)
    extra = (os.environ.get("GITHUB_ACTOR", ""), os.environ.get("GITHUB_TRIGGERING_ACTOR", ""))
    try:
        roster = read(gql=gql, gh=gh, owner=args.owner, extra_logins=extra,
                      page_size=page_size)
    except RosterUnreadable as exc:
        print(f"{TAG} unreadable — {exc}")
        return 2
    c = roster.counts
    print(f"{TAG} ok — {roster.people} people, {c['forms']} forms "
          f"(linear {c['linear']}, github {c['github']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
