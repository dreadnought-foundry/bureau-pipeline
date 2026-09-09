#!/usr/bin/env python3
"""The two non-human Linear identities are what the declaration says (DRE-3172).

Since 2026-09-05 the workspace has three Linear users: the FLEET user every
sweep, planner, merge-sync, relay and console run as; the OPERATOR-TOOLS user
the operator's scripts and assistant sessions run as; and the CEO, who
approves. Linear's 2,500 requests/hour limit is PER USER, so the two non-human
users are two separate budgets — the whole point of there being two.

Nothing in code declared that before this card, and so nothing could notice
the two ways it silently stops being true: a key rotated onto an admin's user
(an unattended actor that can now do anything on the board), or both keys
resolving to ONE user (one budget again, and every sweep starves the operator's
terminal). `config/linear-identities.json` declares each identity — its display
name, the variable this check reads its key from, where the key lives, what it
is for — and the rules it owes: `must_not_be_admin`, `must_differ_from`.

## Every home of a key, not just the first (DRE-3334)

One identity's key is not kept in one place. The fleet key is the Actions
secret in every product repo AND the relay Lambda's own copy in Secrets
Manager `bureau/relay/linear-api-key` — and until agent-bureau PR #2299 the
relay's deploy copied the operator's `LINEAR_API_KEY` into that secret on
every deploy, which since 2026-09-05 is the `bureau-tools` key. One routine
deploy would have put every parking reason, escalation and receipt the relay
writes on the operator's user and the operator's budget, silently. The deploy
guard that refuses that lives in the other repo; this check outlives any
deploy script.

So an identity may declare `homes`, each `{"name", "env", "lives_in"}`. Its
top-level `env`/`lives_in` are its FIRST home and are named for the identity
itself; every other home prints as `<identity>/<home>`. Every home is
resolved and judged, and `one_user_per_identity` says every home of an
identity must resolve to the same user as its first.

An identity's USER is the user its FIRST home resolves to, so the rules that
judge the identity — `display_name` and `must_differ_from` — are asked of
first homes only, and a secondary home answers to `one_user_per_identity`
instead. That is not tidiness: this report's whole job is naming the key to
rotate, and asking an identity's rules of one of its other homes prints the
IDENTITY's name against a user only that HOME has (`fleet and operator-tools
both resolve to 'bureau-tools'` when it is fleet's relay copy that does).
`must_not_be_admin` is asked of every home — it is a fact about the key in
front of it, the `[OK]` line claims it of every home, and an unreadable first
home leaves `one_user_per_identity` nothing to hold the rest against.

## What the check does

For each home of each declared identity it takes the key from the named
environment variable (never from an argument: an argument lands in shell
history), asks Linear `viewer { id name admin }`, and reports one of three
things:

  * `[OK]`       — the viewer matches the declaration and breaks no rule;
  * `[FAIL]`     — a rule broke, and the line names WHICH: `must_not_be_admin`,
                   `display_name`, `must_differ_from` (one line naming both
                   identities that landed on one user), or
                   `one_user_per_identity` (one identity's homes are two
                   users);
  * `[UNKNOWN]`  — the key is absent, or Linear did not answer. Reported and
                   non-zero, never OK: a check that passes when it could not
                   look is no check, and the relay's key unread is exactly the
                   case this exists for.

It prints names and ids, never keys — and the error path is redacted too,
because a 401 body or a URLError is where a key fragment would leak.

## Why this is its own script

`scripts/ready_lane_writers.py check` is the existing assertion about the
Linear writers, but it is STATIC: it reads code and runs in CI with no
credentials. This one needs two live keys and the operator's machine, so
folding it in would turn the writer check red on every keyless run or hand it
a skip. One `check_<thing>.py` per concern is the repo's shape.

## What this cannot see

Where a key is used, only who it resolves to. A workflow wired to the wrong
secret name is `tests/test_agent_linear_key.py`'s business; whether the name
`LINEAR_API_KEY` means the fleet (in GitHub Actions) or operator-tools (on the
operator's machine) is said in the config's own readme, because that collision
is the easiest way to misfile a key.

CLI:

    LINEAR_API_KEY_FLEET=… LINEAR_API_KEY_RELAY=… LINEAR_API_KEY=… \
        python3 scripts/check_linear_identities.py check
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import linear_ops  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
CONFIG_PATH = os.path.join(ROOT, "config", "linear-identities.json")

VIEWER_QUERY = "{ viewer { id name admin } }"

#: The fields every declared identity must carry.
REQUIRED_FIELDS = (
    "name", "display_name", "env", "lives_in", "purpose",
    "must_not_be_admin", "must_differ_from",
)

#: The fields every declared home must carry. An identity's own `env` and
#: `lives_in` are its first home, so a home is those two plus a short name.
HOME_FIELDS = ("name", "env", "lives_in")

#: The rule names, exactly as the FAIL lines print them.
RULE_ADMIN = "must_not_be_admin"
RULE_NAME = "display_name"
RULE_DIFFER = "must_differ_from"
RULE_ONE_USER = "one_user_per_identity"

OK, FAIL, UNKNOWN = "OK", "FAIL", "UNKNOWN"
REDACTED = "<redacted>"


class IdentityError(RuntimeError):
    """The declaration is malformed. Raised rather than defaulted: a file that
    silently loses a rule is a file that silently stops checking it."""


# --------------------------------------------------------------------------- #
# loading                                                                      #
# --------------------------------------------------------------------------- #


def load(path: str | None = None) -> dict:
    path = path or CONFIG_PATH
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as e:
        raise IdentityError(f"cannot read the identity declaration at {path}: {e}") from e


def identities(doc: dict | None = None) -> tuple:
    doc = doc if doc is not None else load()
    try:
        return tuple(doc["identities"])
    except (KeyError, TypeError) as e:
        raise IdentityError(f"this declaration names no identities: {e}") from e


def label_of(identity: str, home: str | None) -> str:
    """What one home prints as: the identity for its first home, and
    `<identity>/<home>` for every other. One definition, so the report, the
    rules and the config problems all name a home the same way."""
    return identity if home is None else f"{identity}/{home}"


def homes_of(row: Mapping) -> list:
    """Every place one identity's key lives, FIRST HOME FIRST.

    The identity's own `env`/`lives_in` are its first home and carry no name
    of their own — they print as the identity. `homes` declares the rest.
    """
    first = {"name": None, "env": row["env"], "lives_in": row["lives_in"]}
    return [first] + [dict(h) for h in (row.get("homes") or [])]


def _home_problems(name: str, row: Mapping) -> list:
    """Everything wrong with one identity's `homes` list."""
    problems: list[str] = []
    homes = row.get("homes", [])
    if not isinstance(homes, list):
        return [f"{name}: homes must be a list of {{name, env, lives_in}} objects"]
    home_names: list = []
    for home in homes:
        if not isinstance(home, dict):
            problems.append(f"{name}: a home is not an object: {home!r}")
            continue
        missing = [f for f in HOME_FIELDS if f not in home]
        if missing:
            problems.append(f"{name}: a home is missing {', '.join(missing)}")
            continue
        empty = [f for f in HOME_FIELDS
                 if not (isinstance(home[f], str) and home[f].strip())]
        if empty:
            problems.append(f"{name}: a home has {', '.join(empty)} that is not "
                            "a non-empty string")
            continue
        home_names.append(home["name"])
    for dup in sorted({n for n in home_names if home_names.count(n) > 1}):
        problems.append(f"{name}: home {dup!r} is declared more than once — "
                        "a home name is how a line names itself")
    return problems


def _declared_envs(name: str, row: Mapping) -> list:
    """`(env, label)` for every home of one identity whose shape is sound.
    Anything malformed is already its own problem and is skipped here."""
    out: list = []
    if isinstance(row.get("env"), str):
        out.append((row["env"], label_of(name, None)))
    for home in row.get("homes") or []:
        if isinstance(home, dict) and isinstance(home.get("env"), str) \
                and isinstance(home.get("name"), str):
            out.append((home["env"], label_of(name, home["name"])))
    return out


def config_problems(doc: dict | None = None) -> list:
    """Everything wrong with the declaration itself, before any key is read."""
    problems: list[str] = []
    try:
        rows = identities(doc)
    except IdentityError as e:
        return [str(e)]
    names = [r.get("name") for r in rows if isinstance(r, dict)]
    for name in sorted({n for n in names if names.count(n) > 1}):
        problems.append(f"identity {name!r} is declared more than once")
    seen_env: dict = {}
    for row in rows:
        if not isinstance(row, dict):
            problems.append(f"an identity is not an object: {row!r}")
            continue
        name = row.get("name") or "<unnamed>"
        missing = [f for f in REQUIRED_FIELDS if f not in row]
        if missing:
            problems.append(f"{name}: missing {', '.join(missing)}")
            continue
        for text_field in ("display_name", "env", "lives_in", "purpose"):
            if not (isinstance(row[text_field], str) and row[text_field].strip()):
                problems.append(f"{name}: {text_field} must be a non-empty string")
        if row["must_not_be_admin"] is not True:
            problems.append(
                f"{name}: must_not_be_admin must be true — a non-human user "
                "with admin is the failure this file exists to catch"
            )
        if not isinstance(row["must_differ_from"], list):
            problems.append(f"{name}: must_differ_from must be a list")
        else:
            for other in row["must_differ_from"]:
                if other == name:
                    problems.append(f"{name}: must_differ_from names itself")
                elif other not in names:
                    problems.append(
                        f"{name}: must_differ_from names {other!r}, which is "
                        "not a declared identity"
                    )
        problems.extend(_home_problems(name, row))
        # One variable is one key, so two homes behind one name are one home —
        # and the reader would never know which of the two it just proved.
        for env, owner in _declared_envs(name, row):
            if env in seen_env:
                problems.append(
                    f"{owner} and {seen_env[env]} both read their key from "
                    f"{env} — two identities behind one variable are one identity"
                )
            seen_env.setdefault(env, owner)
    return problems


# --------------------------------------------------------------------------- #
# asking Linear                                                                #
# --------------------------------------------------------------------------- #


def viewer_of(key: str) -> dict:
    """Who `key` is, straight from Linear: `{"id", "name", "admin"}`.

    Not `linear_ops.gql`, which reads its key from `LINEAR_API_KEY` — this
    check holds two keys at once and must present each one deliberately.
    Same endpoint, same timeout; a failure raises with the response body,
    which the caller redacts before it is printed.
    """
    payload = json.dumps({"query": VIEWER_QUERY, "variables": {}}).encode()
    req = urllib.request.Request(
        linear_ops.API,
        data=payload,
        headers={"Authorization": key, "Content-Type": "application/json"},
    )
    try:
        # B310: the constant https://api.linear.app endpoint, no user input.
        with urllib.request.urlopen(req, timeout=15) as resp:  # nosec B310
            out = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:linear_ops.BODY_CHARS]
        raise RuntimeError(f"HTTP {exc.code} from {linear_ops.API}: {body}") from exc
    if out.get("errors"):
        raise RuntimeError(f"linear error from {linear_ops.API}: {out['errors']}")
    viewer = (out.get("data") or {}).get("viewer") or {}
    if not viewer.get("id"):
        raise RuntimeError(f"no viewer in the answer from {linear_ops.API}")
    return viewer


# --------------------------------------------------------------------------- #
# the check                                                                    #
# --------------------------------------------------------------------------- #


@dataclass
class Resolved:
    """What one declared HOME of one identity turned out to be."""

    #: The printed label: `fleet` for a first home, `fleet/relay` for the rest.
    name: str
    env: str
    status: str
    #: The identity this home belongs to, and the home's own name (`None` for
    #: a first home, which has no name of its own).
    identity: str = ""
    home: str | None = None
    id: str | None = None
    display_name: str | None = None
    admin: bool | None = None
    reason: str | None = None
    problems: list = field(default_factory=list)


def _redact(text: str, keys) -> str:
    for key in keys:
        if key:
            text = text.replace(key, REDACTED)
    return text


def every_home(doc: dict | None = None) -> list:
    """`(identity_row, home)` for every home of every identity, declaration
    order, first home first. The one place the check's unit of work is
    defined: one home is one key, one `viewer` call and one printed line."""
    return [(row, home) for row in identities(doc) for home in homes_of(row)]


def declared_keys(doc: dict | None, env: Mapping) -> list:
    """Every key this run holds — the redaction list, so a home added to the
    config is redacted without anyone remembering to add it here."""
    return [env.get(home["env"]) for _, home in every_home(doc)]


def resolve(doc: dict | None, env: Mapping, viewer: Callable[[str], dict]) -> list:
    """Ask `viewer` who each declared key is, once per HOME. Absent, empty or
    unanswered is UNKNOWN with the reason; nothing here judges a rule yet."""
    keys = declared_keys(doc, env)
    out: list[Resolved] = []
    for row, home in every_home(doc):
        identity, var = row["name"], home["env"]
        name = label_of(identity, home["name"])
        key = env.get(var)
        if not key:
            out.append(Resolved(name, var, UNKNOWN, identity, home["name"],
                                reason=f"{var} is not set, so nothing can be said "
                                       "about who this key is"))
            continue
        try:
            who = viewer(key)
        except Exception as e:  # noqa: BLE001 — any failure is UNKNOWN, reported
            out.append(Resolved(name, var, UNKNOWN, identity, home["name"],
                                reason=f"Linear did not answer for {var}: "
                                       f"{_redact(str(e), keys)}"))
            continue
        out.append(Resolved(
            name, var, OK, identity, home["name"],
            id=str(who.get("id") or ""),
            display_name=str(who.get("name") or ""),
            admin=bool(who.get("admin")),
        ))
    return out


def judge(doc: dict | None, resolved: list) -> list:
    """Apply the rules to every home. Returns the same rows, statuses and
    problems filled in. Each problem lands on the HOME that broke the rule,
    so a bad relay key fails `fleet/relay` and leaves `fleet` saying what it
    truthfully is.

    WHICH RULES ASK WHICH HOME. An identity's USER is the user its first home
    resolves to, so the two rules that judge the identity — the name it must
    wear (`display_name`) and the identity it must not collide with
    (`must_differ_from`) — are asked of first homes only. A secondary home
    owes `one_user_per_identity` instead, which names the home itself. Asking
    the identity's rules of a secondary home prints the IDENTITY's name
    against a user the identity does not have: fleet's relay home on the
    operator's user used to print `fleet and operator-tools both resolve to
    'bureau-tools'` when fleet's own key resolved to `Agent-Bureau`, sending
    an operator to rotate the wrong key, plus a `display_name` line restating
    what `one_user_per_identity` had already said.

    `must_not_be_admin` is the exception and is asked of EVERY home: it is a
    fact about the key in front of it, not about the identity, the `[OK]`
    line says "not an admin" about every home, and a first home that could
    not be read leaves `one_user_per_identity` nothing to hold the rest
    against.
    """
    rows = {r["name"]: r for r in identities(doc)}
    by_identity: dict = {}
    for r in resolved:
        by_identity.setdefault(r.identity, []).append(r)
    first_of = {identity: group[0] for identity, group in by_identity.items()}
    for r in resolved:
        if r.status == UNKNOWN:
            continue
        if r.admin:
            r.problems.append(
                f"rule {RULE_ADMIN} broke: {r.env} resolves to "
                f"{r.display_name!r} (id {r.id}), who is an admin — an "
                "unattended actor must not be able to do anything on the board"
            )
        if r is not first_of[r.identity]:
            continue
        declared = rows[r.identity]
        if r.display_name != declared["display_name"]:
            r.problems.append(
                f"rule {RULE_NAME} broke: expected {declared['display_name']!r}, "
                f"{r.env} resolves to {r.display_name!r} (id {r.id})"
            )
    # One identity is one user, however many places its key is kept. A home
    # that resolves to somebody else is the relay running on the operator's
    # budget — the thing DRE-3334 exists to catch.
    for identity, group in by_identity.items():
        first = group[0]
        if first.status == UNKNOWN:
            # Nothing to hold the other homes against; each already says so.
            continue
        for r in group[1:]:
            if r.status == UNKNOWN or r.id == first.id:
                continue
            r.problems.append(
                f"rule {RULE_ONE_USER} broke: {r.env} resolves to "
                f"{r.display_name!r} (id {r.id}), but {identity} is "
                f"{first.display_name!r} (id {first.id}) from {first.env} — "
                "every home of one identity is one user, or it is two "
                "identities wearing one name"
            )
    judged_pairs: set = set()
    for r in first_of.values():
        if r.status == UNKNOWN:
            continue
        for other_name in rows[r.identity]["must_differ_from"]:
            pair = tuple(sorted((r.identity, other_name)))
            if pair in judged_pairs:
                continue
            other = first_of.get(other_name)
            # An UNKNOWN home already has its own line; judging the pair
            # against a user nobody could read would invent an answer.
            if other is None or other.status == UNKNOWN or other.id != r.id:
                continue
            judged_pairs.add(pair)
            first_env, second_env = (
                (r.env, other.env) if r.identity == pair[0]
                else (other.env, r.env)
            )
            r.problems.append(
                f"rule {RULE_DIFFER} broke: {pair[0]} and {pair[1]} both "
                f"resolve to {r.display_name!r} (id {r.id}) — {first_env} "
                f"and {second_env} are one user, so one 2,500/hour budget, "
                "which is what having two identities exists to prevent"
            )
    for r in resolved:
        if r.status != UNKNOWN and r.problems:
            r.status = FAIL
    return resolved


def report(resolved: list) -> list:
    """The lines the check prints, one per HOME. Names and ids only — never a
    key."""
    lines: list[str] = []
    for r in resolved:
        if r.status == UNKNOWN:
            lines.append(f"  [UNKNOWN] {r.name}: {r.reason}")
        elif r.status == OK:
            lines.append(f"  [OK] {r.name}: {r.env} resolves to {r.display_name!r} "
                         f"(id {r.id}), not an admin")
        else:
            for problem in r.problems:
                lines.append(f"  [FAIL] {r.name}: {problem}")
    failed = sum(1 for r in resolved if r.status == FAIL)
    unknown = sum(1 for r in resolved if r.status == UNKNOWN)
    lines.append(
        f"{len(resolved)} key home{'' if len(resolved) == 1 else 's'} declared; "
        f"{failed} failed, {unknown} unknown"
        + (" — unknown is not a pass" if unknown else "")
    )
    return lines


def run(env: Mapping | None = None, viewer: Callable[[str], dict] | None = None,
        doc: dict | None = None) -> int:
    """The whole check, printed. 0 only when every home of every identity is
    OK."""
    env = os.environ if env is None else env
    viewer = viewer_of if viewer is None else viewer
    doc = load() if doc is None else doc
    bad_config = config_problems(doc)
    if bad_config:
        for problem in bad_config:
            print(f"  [FAIL] config: {problem}")
        print(f"{len(bad_config)} problem(s) in {os.path.relpath(CONFIG_PATH, ROOT)}; "
              "no key was read")
        return 1
    resolved = judge(doc, resolve(doc, env, viewer))
    keys = declared_keys(doc, env)
    for line in report(resolved):
        print(_redact(line, keys))
    return 0 if all(r.status == OK for r in resolved) else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("check")
    args = parser.parse_args(argv)
    if (args.command or "check") != "check":
        parser.print_usage(sys.stderr)
        return 2
    return run()


if __name__ == "__main__":
    sys.exit(main())
