#!/usr/bin/env python3
"""The act registry's CONSUMER guard (DRE-3091) — console first, then here.

`config/pipeline-acts.json` is a contract with a reader in another repository:
the console's `ACTS`, in `console/backend/receipts.py`. The console has always
checked it — `test_every_act_the_pipeline_declares_is_known_to_the_console`
(DRE-2825) — and that guard is right. It just lives only in the CONSUMER, so
it fires in the wrong repo, after the fact, and holds unrelated pull requests
hostage until someone files the one-line console card.

Twice in twelve hours a bureau-pipeline card added an act, merged green, and
broke every open PR in agent-bureau: DRE-3042's `conflict-sweep-crashed`
(2026-09-03 22:26 PT, reported as DRE-3081) and DRE-3084's `refuted-finding`
(2026-09-04 09:13 PT, DRE-3090).

`standards/engineering.md` states the rule: a shared contract is either one
module both sides import, or every consumer is updated in the same change.
Across two repositories neither is available, so the producer grows the same
question the consumer already asks, and asks it BEFORE the merge.

## Unread is never a pass

The one thing this must never do is conclude "the console carries no acts"
because it could not read the console. There is no token in a local checkout,
there is no network in some runners, the file may move, and `ACTS` may one day
be built by a comprehension rather than written as a literal. Every one of
those is UNREAD — reported with a visible reason, `ok` False, and `skipped`
True. `tests.yml`'s `act registry consumers` job runs the guard under pytest
with `--junit-xml` and then runs `assert-ran` over the report, so a skip is a
RED BUILD there and a clean skip everywhere else.

## What "the console carries the act" means

The console owns its own literal. A producer-side guard that pinned the shape
of `ACTS` would break on a console refactor that changed nothing about what
the console knows, and would be a second, disagreeing reading of a file the
console already reads correctly. So this parses the console's module with
`ast` (never a regex — the lane-contract scenario makes the same argument) and
collects every string constant standing inside the `ACTS` assignment. An act
is KNOWN when either its tag or its name is one of them: both are unique
identifiers of the act, and an act the console has never heard of carries
neither.

## …and the same question about the NUMBER on the row (DRE-3389)

Knowing the act is not enough once a row carries a `cadence_s` the console
renders as a claim about a specific piece of work. DRE-3388 MEASURED the three
lifecycle cadences — the ⏳ build heartbeat, the review run, the gate's
re-check — against live Actions runs and declared them in the console FIRST;
`config/pipeline-acts.json` copies them. Two files holding the same number is a
number that can drift, so `cadences` reads it out of both and compares.

The reader is deliberately SHAPE-TOLERANT, for the same reason `check` reads
strings rather than a literal's shape: the console owns its own file. It parses
the module, resolves module-level integer constants (`_REVIEW_BOUND = 65 * 60 *
1000` is a real one), and for every act collects the integers standing in any
container row keyed on that act's tag or name — inside `ACTS` or in a cadence
table beside it. Seconds and milliseconds are the same cadence: 6300 and
6_300_000 both agree with a registry that declares 6300.

What that buys, and what it does not:

  * a DIFFERENCE always fails — the console declares a number for the act and
    this file declares another;
  * an act whose number cannot be located at all is `unconfirmed`, printed with
    the rest, and fatal only for a `progress` act. Those three are what this
    check exists for; demanding to place every one of the other nineteen would
    turn a console refactor that changed nothing into a red build here.

## CLI

    check_act_consumers.py check [--registry PATH]   0 ok · 1 gap · 3 unread
    check_act_consumers.py cadences [--registry PATH]  same three answers
    check_act_consumers.py context --changed-files F --out G
    check_act_consumers.py assert-ran REPORT.xml     the no-skip assertion

Environment:
    BUREAU_CONSOLE_TOKEN       a token that can read the console repository.
    BUREAU_CONSOLE_ACTS_FILE   a local console file, read instead of the
                               network — the test hook (dispatch_pool.py's
                               BUREAU_FAKE_RATE_LIMITS pattern), and the way
                               the guard is exercisable end-to-end offline.

Deliberately NOT `GH_TOKEN`: the token this repo's CI already holds is scoped
to this repository and 404s on the console, and a guard that fell back to it
would report "unread" for a reason nobody could act on.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
CONFIG_PATH = os.path.join(ROOT, "config", "pipeline-acts.json")

#: The file a pull request must touch for any of this to be its business.
REGISTRY_PATH = "config/pipeline-acts.json"

#: `check` exit codes. 3 rather than 1 so a caller can tell "the console does
#: not know an act" (actionable here) from "nobody could look" (actionable in
#: the wiring), which are two different bugs with two different owners.
EXIT_OK = 0
EXIT_GAP = 1
EXIT_SKIPPED = 3

TOKEN_ENV = "BUREAU_CONSOLE_TOKEN"
SOURCE_ENV = "BUREAU_CONSOLE_ACTS_FILE"

#: The kind whose cadence this repo did not choose (DRE-3389). Read off the
#: registry rather than restated in a list here — `cadences()` asks each row
#: what kind it is, and the three acts under this one are the rows whose number
#: MUST be confirmed against the console rather than merely not contradicted.
PROGRESS_KIND = "progress"

_TIMEOUT = 15


def load(path: str | None = None) -> dict:
    with open(path or CONFIG_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def console_spec(doc: dict | None = None) -> dict:
    """Where the consumer lives — repo, path, module, symbol, fix template."""
    spec = (doc if doc is not None else load()).get("console") or {}
    return {k: v for k, v in spec.items() if not k.startswith("_")}


# ── reading the console ─────────────────────────────────────────────────────
def _get(url: str, token: str, accept: str) -> str | None:
    import urllib.request

    req = urllib.request.Request(
        url,
        headers={
            "authorization": f"Bearer {token}",
            "accept": accept,
            "user-agent": "bureau-act-consumers",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return resp.read().decode("utf-8", "replace")
    except Exception:
        return None


def read_console_source(spec: dict, env=None) -> tuple[str | None, str]:
    """`(source, reason)` — the console file's text, or None and WHY not.

    The raw file, never a checkout: this runs in a job with no clone of the
    console and no business making one.
    """
    env = os.environ if env is None else env
    local = (env.get(SOURCE_ENV) or "").strip()
    if local:
        try:
            with open(local, encoding="utf-8") as fh:
                return fh.read(), ""
        except OSError as e:
            return None, f"{SOURCE_ENV}={local} could not be read ({e})"

    token = (env.get(TOKEN_ENV) or "").strip()
    if not token:
        return None, (
            f"{TOKEN_ENV} is unset, so the console's "
            f"{spec.get('path')} cannot be read"
        )
    repo, path = spec.get("repo") or "", spec.get("path") or ""
    if not repo or not path:
        return None, "the registry declares no console repo/path to read"

    raw_accept = "application/vnd.github.raw+json"
    source = _get(
        f"https://api.github.com/repos/{repo}/contents/{path}", token, raw_accept
    )
    if source is not None:
        return source, ""

    # The path 404'd. Locate the module BY NAME in the tree rather than
    # trusting a path written down here — a remembered path is exactly the
    # enumeration of a derivable set this repo keeps being bitten by
    # (config/lane-contract.json's console block, DRE-2726).
    module = f"{spec.get('module') or ''}.py"
    default = _get(
        f"https://api.github.com/repos/{repo}", token, "application/vnd.github+json"
    )
    ref = ""
    if default:
        try:
            ref = (json.loads(default) or {}).get("default_branch") or ""
        except ValueError:
            ref = ""
    if ref and module != ".py":
        tree = _get(
            f"https://api.github.com/repos/{repo}/git/trees/{ref}?recursive=1",
            token,
            "application/vnd.github+json",
        )
        try:
            entries = (json.loads(tree or "{}") or {}).get("tree") or []
        except ValueError:
            entries = []
        for entry in entries:
            found = entry.get("path") or ""
            if entry.get("type") == "blob" and os.path.basename(found) == module:
                source = _get(
                    f"https://api.github.com/repos/{repo}/contents/{found}",
                    token, raw_accept,
                )
                if source is not None:
                    return source, ""
    return None, (
        f"{repo}:{path} could not be read with {TOKEN_ENV} (and no "
        f"{module} was found in that repository's tree)"
    )


def console_vocabulary(source: str, symbol: str) -> tuple[frozenset | None, str]:
    """Every string standing inside the `symbol` assignment, or None and why.

    Parsed, never regexed. Returning an EMPTY set where the symbol is present
    but unreadable would read as "the console carries no act" and fail every
    act at once for a reason that is not true, so that case is unread too.
    """
    try:
        tree = ast.parse(source or "")
    except SyntaxError as e:
        return None, f"the console module does not parse ({e.msg})"

    value = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets, candidate = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, candidate = [node.target], node.value
        else:
            continue
        if symbol in {t.id for t in targets if isinstance(t, ast.Name)}:
            value = candidate
            break
    if value is None:
        return None, f"the console module declares no {symbol}"

    strings = frozenset(
        n.value
        for n in ast.walk(value)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    )
    if not strings:
        return None, (
            f"{symbol} is present but carries no readable strings — it is "
            "built rather than written out, so this guard cannot say what "
            "the console knows"
        )
    return strings, ""


# ── reading the console's NUMBERS (DRE-3389) ────────────────────────────────
#: The arithmetic a declared constant is allowed to be built out of. Enough for
#: `65 * 60 * 1000` and nothing more: this resolves a console's own literals, it
#: does not execute the console.
_FOLD = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.FloorDiv: lambda a, b: a // b,
}


def _as_int(node, constants: dict):
    """`node` as an int, or None if it is not one this reader can resolve.

    `True` is not an integer here even though Python says it is — a boolean
    read as 1 would agree with nothing and disagree with everything.
    """
    if isinstance(node, ast.Constant):
        value = node.value
        return value if isinstance(value, int) and not isinstance(value, bool) else None
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        inner = _as_int(node.operand, constants)
        return None if inner is None else -inner
    if isinstance(node, ast.BinOp) and type(node.op) in _FOLD:
        left, right = _as_int(node.left, constants), _as_int(node.right, constants)
        if left is None or right is None:
            return None
        try:
            return _FOLD[type(node.op)](left, right)
        except ZeroDivisionError:
            return None
    return None


def _module_constants(tree) -> dict:
    """`NAME = <int>` at module level, resolved in file order.

    In order on purpose: `_REVIEW_BOUND = 65 * 60 * 1000` is written before the
    table that uses it, so a single forward pass resolves everything the console
    actually writes without this reader needing to iterate to a fixed point.
    """
    constants: dict[str, int] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        value = _as_int(node.value, constants)
        if value is None:
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                constants[target.id] = value
    return constants


def _strings(node) -> frozenset:
    return frozenset(
        n.value for n in ast.walk(node)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    )


def _ints(node, constants: dict) -> frozenset:
    return frozenset(
        v for v in (_as_int(n, constants) for n in ast.walk(node)) if v is not None
    )


def console_cadences(source: str) -> tuple:
    """`(by_key, reason)` — every integer the console stands beside each key.

    `by_key` maps a string the console uses as a row key (or writes inside a
    row) to every integer resolvable in that row, unioned across the whole
    module. Deliberately not scoped to `ACTS`: a console is free to keep its
    cadences in a table of its own beside it, and a reader that insisted on one
    shape would report a drift where there is only a refactor.
    """
    try:
        tree = ast.parse(source or "")
    except SyntaxError as e:
        return None, f"the console module does not parse ({e.msg})"

    constants = _module_constants(tree)
    by_key: dict = {}

    def _add(key: str, numbers) -> None:
        if key and numbers:
            by_key[key] = by_key.get(key, frozenset()) | numbers

    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if key is None:
                    continue
                numbers = _ints(value, constants)
                for name in _strings(key):
                    _add(name, numbers)
        elif isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            for element in node.elts:
                numbers = _ints(element, constants)
                for name in _strings(element):
                    _add(name, numbers)

    if not by_key:
        return None, (
            "no act in the console module stands beside a number this reader "
            "can resolve — it is built rather than written out, so this guard "
            "cannot say what cadence the console declares"
        )
    return by_key, ""


@dataclass(frozen=True)
class CadenceReport:
    """Agreement about the NUMBER, act by act.

    Three outcomes and never a fourth: the console says the same thing, the
    console says something else (`mismatched`, always a failure), or this reader
    could not place the act's number at all (`unconfirmed`, a failure only for
    the `progress` acts this check exists for).
    """

    spec: dict
    by_key: dict | None
    mismatched: tuple  # (name, tag, kind, declared_s, found)
    unconfirmed: tuple  # (name, tag, kind, declared_s)
    checked: int
    reason: str

    @property
    def skipped(self) -> bool:
        return self.by_key is None

    @property
    def unconfirmed_progress(self) -> tuple:
        return tuple(row for row in self.unconfirmed if row[2] == PROGRESS_KIND)

    @property
    def ok(self) -> bool:
        return (
            self.by_key is not None
            and not self.mismatched
            and not self.unconfirmed_progress
        )

    def text(self) -> str:
        head = (
            f"{REGISTRY_PATH} declares {self.checked} cadence(s); the consumer "
            f"is {self.spec.get('repo')}:{self.spec.get('path')}."
        )
        if self.skipped:
            return (
                f"SKIPPED — {self.reason}. Unread is never a pass: the `act "
                f"registry consumers` job in tests.yml fails on this. {head}"
            )

        lines = []
        for name, tag, _kind, declared, found in self.mismatched:
            lines.append(
                f"{name} ({tag}): this file declares {declared}s, the console "
                f"declares {sorted(found)} — one of the two moved, and the row "
                "the console renders is a claim about a specific piece of work"
            )
        for name, tag, kind, declared in self.unconfirmed:
            note = (
                "and a progress cadence is exactly what this check exists to "
                "keep equal — has the console shipped it yet?"
                if kind == PROGRESS_KIND
                else "reported, not failed: the console owns its own literal"
            )
            lines.append(
                f"{name} ({tag}): declares {declared}s and no number for it "
                f"could be located in the console — {note}"
            )
        if not lines:
            return f"OK — every declared cadence is the console's. {head}"
        verdict = "OK with notes" if self.ok else f"{len(self.mismatched)} difference(s)"
        return "\n".join([f"{verdict}. {head}", *lines])


def cadences(doc: dict | None = None, source: str | None = -1, reason: str = "",
             registry: str | None = None) -> CadenceReport:
    """Does the console declare the same cadence this file does?

    `source` takes the same sentinel `check()` does: -1 means "go and read it",
    None means "it could not be read" and `reason` says why.
    """
    doc = doc if doc is not None else load(registry)
    spec = console_spec(doc)
    rows = tuple(
        (
            (e.get("name") or "").strip(),
            (e.get("tag") or "").strip(),
            (e.get("kind") or "").strip(),
            e.get("cadence_s"),
        )
        for e in (doc.get("acts") or ())
        if isinstance(e.get("cadence_s"), int) and not isinstance(e.get("cadence_s"), bool)
    )

    if source == -1:
        source, reason = read_console_source(spec)
    if source is None:
        return CadenceReport(
            spec, None, (), (), len(rows), reason or "the console could not be read"
        )

    by_key, why = console_cadences(source)
    if by_key is None:
        return CadenceReport(spec, None, (), (), len(rows), why)

    mismatched, unconfirmed = [], []
    for name, tag, kind, declared in rows:
        found = (by_key.get(tag) or frozenset()) | (by_key.get(name) or frozenset())
        if not found:
            unconfirmed.append((name, tag, kind, declared))
        # The console serves milliseconds and this file declares seconds. The
        # comparison is of the CADENCE, not of the unit either side stores it
        # in — anything else would report a difference that is not one.
        elif declared not in found and declared * 1000 not in found:
            mismatched.append((name, tag, kind, declared, found))

    return CadenceReport(
        spec, by_key, tuple(mismatched), tuple(unconfirmed), len(rows), ""
    )


# ── the report ──────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Report:
    """One answer, and never a fourth state: known, gap, or unread."""

    spec: dict
    vocabulary: frozenset | None
    unknown: tuple  # the act NAMES the console does not carry
    checked: int
    reason: str
    unknown_rows: tuple = ()  # the same acts as (name, tag, kind)

    @property
    def skipped(self) -> bool:
        return self.vocabulary is None

    @property
    def ok(self) -> bool:
        return self.vocabulary is not None and not self.unknown

    def fixes(self) -> list:
        """One line per unknown act, naming the act and the console fix."""
        template = self.spec.get("fix") or "add {tag!r} to {symbol} in {repo}:{path}"
        return [
            f"{name}: the console's {self.spec.get('symbol')} does not carry it — "
            + template.format(
                name=name, tag=tag, kind=kind,
                symbol=self.spec.get("symbol"), repo=self.spec.get("repo"),
                path=self.spec.get("path"),
            )
            for name, tag, kind in self.unknown_rows
        ]

    def text(self) -> str:
        head = (
            f"{REGISTRY_PATH} declares {self.checked} act(s); the consumer is "
            f"{self.spec.get('repo')}:{self.spec.get('path')} ({self.spec.get('symbol')})."
        )
        if self.skipped:
            return (
                f"SKIPPED — {self.reason}. Unread is never a pass: the `act "
                f"registry consumers` job in tests.yml fails on this. {head}"
            )
        if self.ok:
            return f"OK — every declared act is known to the console. {head}"

        return "\n".join(
            [f"{len(self.unknown)} act(s) the console does not know. {head}", *self.fixes()]
        )


def check(doc: dict | None = None, source: str | None = -1, reason: str = "",
          registry: str | None = None) -> Report:
    """The guard's answer.

    `source` defaults to the sentinel -1, meaning "go and read it". Passing
    None means "it could not be read" and `reason` says why — the tests use
    both, and so does `context()`.
    """
    doc = doc if doc is not None else load(registry)
    spec = console_spec(doc)
    rows = tuple(
        (
            (e.get("name") or "").strip(),
            (e.get("tag") or "").strip(),
            (e.get("kind") or "").strip(),
        )
        for e in (doc.get("acts") or ())
    )

    if source == -1:
        source, reason = read_console_source(spec)
    if source is None:
        return Report(spec, None, (), len(rows), reason or "the console could not be read")

    vocabulary, why = console_vocabulary(source, spec.get("symbol") or "ACTS")
    if vocabulary is None:
        return Report(spec, None, (), len(rows), why)

    missing = tuple(r for r in rows if r[1] not in vocabulary and r[0] not in vocabulary)
    return Report(
        spec, vocabulary, tuple(r[0] for r in missing), len(rows), "",
        unknown_rows=missing,
    )


def context(changed_files, doc: dict | None = None, source: str | None = -1,
            reason: str = "", registry: str | None = None) -> str:
    """The critic's receipt line, or "" when this PR is not about the registry.

    Reported on a CLEAN check as well as a failing one. A receipt that only
    appears when something is wrong teaches a reviewer that its absence means
    the check ran and passed — most of the time it means the check never ran.
    """
    if REGISTRY_PATH not in {(p or "").strip() for p in changed_files or ()}:
        return ""
    report = check(doc=doc, source=source, reason=reason, registry=registry)
    lead = (
        f"ACT REGISTRY CONSUMER CHECK (DRE-3091). This PR changes "
        f"{REGISTRY_PATH}, a contract the console reads. A new act ships "
        "CONSOLE-FIRST (docs/pipeline-acts.md); the pipeline PR cannot merge "
        "ahead of the console one. The producer-side guard says:"
    )
    # Both halves of the same contract, always together (DRE-3389). The act
    # check answers "does the console know this row exists"; the cadence check
    # answers "does it hold the same NUMBER on it". A reviewer told only the
    # first would read a clean receipt as a clean registry.
    paced = cadences(doc=doc, source=source, reason=reason, registry=registry)
    pace_lead = (
        "AND THE CADENCES ON THOSE ROWS. The numbers were measured once, by "
        "the console card, and copied here; two files holding one number is a "
        "number that can drift, so both are read and compared:"
    )
    return f"{lead}\n{report.text()}\n\n{pace_lead}\n{paced.text()}"


# ── the no-skip assertion ───────────────────────────────────────────────────
def assert_ran(path: str) -> tuple[int, str]:
    """`(exit, message)` over a pytest `--junit-xml` report.

    Zero tests and one skip are the same failure wearing two faces: the guard
    did not answer. Both are red here, which is what turns "skips loudly" into
    "cannot merge".
    """
    try:
        tree = ET.parse(path)
    except (OSError, ET.ParseError) as e:
        return 1, f"the guard's junit report at {path} could not be read ({e})"

    suites = [tree.getroot()] if tree.getroot().tag == "testsuite" else list(
        tree.getroot().iter("testsuite")
    )
    tests = sum(int(s.get("tests") or 0) for s in suites)
    skipped = sum(int(s.get("skipped") or 0) for s in suites)
    if not tests:
        return 1, f"the guard ran ZERO tests ({path}) — a green run that checked nothing"
    if skipped:
        return 1, (
            f"the guard SKIPPED {skipped} of {tests} test(s). In this job the "
            "console must be readable: a skip here is a build that proved "
            "nothing about whether the console knows every act."
        )
    return 0, f"the guard ran {tests} test(s) and skipped none"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command")
    run = sub.add_parser("check")
    run.add_argument("--registry", default=None)
    pace = sub.add_parser("cadences")
    pace.add_argument("--registry", default=None)
    ctx = sub.add_parser("context")
    ctx.add_argument("--changed-files", required=True)
    ctx.add_argument("--out", required=True)
    ctx.add_argument("--registry", default=None)
    ran = sub.add_parser("assert-ran")
    ran.add_argument("report")

    args = parser.parse_args(argv)
    command = args.command or "check"

    if command == "assert-ran":
        code, message = assert_ran(args.report)
        print(message)
        return code

    if command == "context":
        try:
            with open(args.changed_files, encoding="utf-8") as fh:
                changed = [line.strip() for line in fh if line.strip()]
        except OSError:
            changed = []
        # Fail-soft, exactly like review_card_context.py: a context builder
        # that wedges the gate is worse than a missing paragraph.
        try:
            text = context(changed, registry=args.registry)
        except Exception as e:  # noqa: BLE001
            text = ""
            print(f"check_act_consumers: {e}", file=sys.stderr)
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        return 0

    if command == "cadences":
        paced = cadences(registry=args.registry)
        print(paced.text())
        if paced.skipped:
            return EXIT_SKIPPED
        return EXIT_OK if paced.ok else EXIT_GAP

    report = check(registry=args.registry)
    print(report.text())
    if report.skipped:
        return EXIT_SKIPPED
    return EXIT_GAP if report.unknown else EXIT_OK


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
