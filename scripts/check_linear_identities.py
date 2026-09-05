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

## What the check does

For each declared identity it takes the key from the named environment
variable (never from an argument: an argument lands in shell history), asks
Linear `viewer { id name admin }`, and reports one of three things:

  * `[OK]`       — the viewer matches the declaration and breaks no rule;
  * `[FAIL]`     — a rule broke, and the line names WHICH: `must_not_be_admin`,
                   `display_name`, or `must_differ_from` (one line naming both
                   identities that landed on one user);
  * `[UNKNOWN]`  — the key is absent, or Linear did not answer. Reported and
                   non-zero, never OK: a check that passes when it could not
                   look is no check.

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

    LINEAR_API_KEY_FLEET=… LINEAR_API_KEY=… python3 scripts/check_linear_identities.py check
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

#: The rule names, exactly as the FAIL lines print them.
RULE_ADMIN = "must_not_be_admin"
RULE_NAME = "display_name"
RULE_DIFFER = "must_differ_from"

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
        env = row["env"]
        if isinstance(env, str) and env in seen_env:
            problems.append(
                f"{name} and {seen_env[env]} both read their key from {env} — "
                "two identities behind one variable are one identity"
            )
        seen_env.setdefault(env, name)
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
    """What one declared identity turned out to be."""

    name: str
    env: str
    status: str
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


def resolve(doc: dict | None, env: Mapping, viewer: Callable[[str], dict]) -> list:
    """Ask `viewer` who each declared key is. Absent, empty or unanswered is
    UNKNOWN with the reason; nothing here judges a rule yet."""
    keys = [env.get(row["env"]) for row in identities(doc)]
    out: list[Resolved] = []
    for row in identities(doc):
        name, var = row["name"], row["env"]
        key = env.get(var)
        if not key:
            out.append(Resolved(name, var, UNKNOWN,
                                reason=f"{var} is not set, so nothing can be said "
                                       "about who this key is"))
            continue
        try:
            who = viewer(key)
        except Exception as e:  # noqa: BLE001 — any failure is UNKNOWN, reported
            out.append(Resolved(name, var, UNKNOWN,
                                reason=f"Linear did not answer for {var}: "
                                       f"{_redact(str(e), keys)}"))
            continue
        out.append(Resolved(
            name, var, OK,
            id=str(who.get("id") or ""),
            display_name=str(who.get("name") or ""),
            admin=bool(who.get("admin")),
        ))
    return out


def judge(doc: dict | None, resolved: list) -> list:
    """Apply the rules. Returns the same rows, statuses and problems filled in."""
    rows = {r["name"]: r for r in identities(doc)}
    by_name = {r.name: r for r in resolved}
    for r in resolved:
        if r.status == UNKNOWN:
            continue
        declared = rows[r.name]
        if r.admin:
            r.problems.append(
                f"rule {RULE_ADMIN} broke: {r.env} resolves to "
                f"{r.display_name!r} (id {r.id}), who is an admin — an "
                "unattended actor must not be able to do anything on the board"
            )
        if r.display_name != declared["display_name"]:
            r.problems.append(
                f"rule {RULE_NAME} broke: expected {declared['display_name']!r}, "
                f"{r.env} resolves to {r.display_name!r} (id {r.id})"
            )
    judged_pairs: set = set()
    for r in resolved:
        if r.status == UNKNOWN:
            continue
        for other_name in rows[r.name]["must_differ_from"]:
            other = by_name.get(other_name)
            if other is None or other.status == UNKNOWN:
                # An UNKNOWN identity already has its own line; judging the
                # pair against a user nobody could read would invent an answer.
                continue
            pair = tuple(sorted((r.name, other_name)))
            if pair in judged_pairs or r.id != other.id:
                continue
            judged_pairs.add(pair)
            r.problems.append(
                f"rule {RULE_DIFFER} broke: {pair[0]} and {pair[1]} both resolve "
                f"to {r.display_name!r} (id {r.id}) — {by_name[pair[0]].env} and "
                f"{by_name[pair[1]].env} are one user, so one 2,500/hour budget, "
                "which is what having two identities exists to prevent"
            )
    for r in resolved:
        if r.status != UNKNOWN and r.problems:
            r.status = FAIL
    return resolved


def report(resolved: list) -> list:
    """The lines the check prints. Names and ids only — never a key."""
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
        f"{len(resolved)} identit{'y' if len(resolved) == 1 else 'ies'} declared; "
        f"{failed} failed, {unknown} unknown"
        + (" — unknown is not a pass" if unknown else "")
    )
    return lines


def run(env: Mapping | None = None, viewer: Callable[[str], dict] | None = None,
        doc: dict | None = None) -> int:
    """The whole check, printed. 0 only when every identity is OK."""
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
    keys = [env.get(r["env"]) for r in identities(doc)]
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
