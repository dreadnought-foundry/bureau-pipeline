#!/usr/bin/env python3
"""One catalog of the pipeline's switches, and one reader of a switch's off-reason (DRE-6434).

A pipeline switch is a repository variable a reusable workflow reads from
`vars.*` and treats as live only when it is exactly `true`. Before this card a
switch that was off carried no reason, so nothing could say when the reason
was over. `config/switches.json` is the list of switches as data, and this
module reads a switch's declared off-reason and composes the one line every
reader prints. The sweep's `Read the switches` step runs it (DRE-6436), and
when a reason clears it posts the receipt and files the alarm (DRE-6437). It
never sets a variable: turning a switch on is a person's act.

## The contract (identical in every sibling card)

The companion variable of a switch `<SWITCH>` is `<SWITCH>_OFF_UNTIL`
(`companion`). A switch is on when its own variable is exactly `true`
(`is_on`); every other value, absent included, is off. The companion's value
names the cards the switch waits on — `DRE-<n>` ids separated by commas or
spaces, free text around them kept verbatim (`parse_reason`).

`off_line(name, env)` is exactly one of:

    <SWITCH> is on
    <SWITCH> is on — its off-reason is stale, delete <SWITCH>_OFF_UNTIL
    <SWITCH> is off — no reason given
    <SWITCH> is off — until DRE-A, DRE-B land
    <SWITCH> is off — its reason names no card: <text>

`reading(name, env, states, now)` adds the cards' Linear states: a reason is
cleared only when every named card is in `prose_blockers.TERMINAL`, and a card
with no state is `unread`, never terminal.

## The sweep's read (DRE-6436)

    python3 scripts/switch_reason.py

prints `switches: <reading line>` for every row of the catalog, in its order,
then the `linear-budget:` trailer — the sweep's `Read the switches` step runs it
on every full pass. A switch's cards are read in ONE Linear request
(`read_states`), and in none when the switch is on or its companion names no
card. A read that fails leaves every card of that switch `unread`, so its
reason never reads as cleared, and the pass still exits 0.

## The receipt and the alarm (DRE-6437)

When a switch is off and every card its companion names is terminal, the pass
reads the thread of the FIRST card named, in the order written. With no
receipt there it posts one, composed by `pipeline_act.receipt`:

    🔀 switch-cleared: <SWITCH> in <repo-slug> — its reason cleared at <PT time>: DRE-A Done, DRE-B Done. It may be turned on: gh variable set <SWITCH> --body true -R <owner/repo>

The once-key is a comment whose FIRST line opens `🔀 switch-cleared: <SWITCH>
in <repo-slug>`, so each repository posts its own and none repeats. A receipt
already standing is when the reason cleared: once it is `alarm_after_hours`
old (`config/switches.json`) and the switch is still off, the pass files one
card titled `Switch <SWITCH> in <repo-slug> is still off after its reason
cleared`, after `find_open` finds none open under that title. The receipt is
written first and the alarm reads only the receipt, so a pass that dies
between the two loses nothing: the next one finds the receipt and times the
alarm from it.

No thread is read when the reason has not cleared, when `REPO` is unset, or
when the switch is on. A read, a post or a filing that fails is printed and the
pass still exits 0. The phase is dry — `would: post …` and `would: alarm …`,
nothing written — when `GITHUB_ACTIONS` is not `true` or `--dry-run` is passed.

## The check

    python3 scripts/switch_reason.py check [--root DIR]

holds the catalog to the tree at DIR (this repository by default). The reads
are DISCOVERED, never listed: every `vars.<NAME>_LIVE` under
`.github/workflows/`, outside a YAML comment, attributed to the step whose
`name` encloses it. A read with no row fails by its name, and a row whose
`reader` does not read `vars.<name>` inside its `step` fails by the row's.

Import-safe and pure: no I/O at import, and every function but `load`,
`discover`, `problems` and `main` reads only its arguments; `read_states`
reaches Linear only through the `gql` it is handed, and the receipt phase only
through the `linear` it is handed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, NamedTuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pipeline_act  # noqa: E402 — import-safe: no I/O at import
import prose_blockers  # noqa: E402 — import-safe: no I/O at import

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
CATALOG = os.path.join("config", "switches.json")
WORKFLOWS = os.path.join(".github", "workflows")

# --------------------------------------------------------------------------- #
# the contract                                                                 #
# --------------------------------------------------------------------------- #

COMPANION_SUFFIX = "_OFF_UNTIL"
ON_VALUE = "true"
UNREAD = "unread"
CATALOG_VERSION = 1
ROW_FIELDS = ("name", "reader", "step", "means")

#: A card id the companion may name. Bounded both sides, so `XDRE-12` and
#: `DRE-12a` are free text rather than a card.
_CARD = re.compile(r"(?<![A-Za-z0-9-])DRE-\d+(?![A-Za-z0-9])")


class Reason(NamedTuple):
    text: str
    cards: tuple


class Reading(NamedTuple):
    line: str
    off: bool
    cleared: bool
    cards: tuple
    unread: tuple


def companion(name: str) -> str:
    """The variable that carries `name`'s off-reason."""
    return f"{name}{COMPANION_SUFFIX}"


def is_on(name: str, env: Mapping) -> bool:
    """True only when the switch's own variable is exactly `true`."""
    return env.get(name) == ON_VALUE


def parse_reason(value: str | None) -> Reason | None:
    """The companion's text and the card ids it names, in the order written,
    duplicates dropped; None for an empty or unset value."""
    text = (value or "").strip()
    if not text:
        return None
    return Reason(text, tuple(dict.fromkeys(_CARD.findall(text))))


def off_line(name: str, env: Mapping) -> str:
    """The line for `name` with no card states — one of the five contract forms."""
    reason = parse_reason(env.get(companion(name)))
    if is_on(name, env):
        if reason is None:
            return f"{name} is on"
        return f"{name} is on — its off-reason is stale, delete {companion(name)}"
    if reason is None:
        return f"{name} is off — no reason given"
    if not reason.cards:
        return f"{name} is off — its reason names no card: {reason.text}"
    return f"{name} is off — until {', '.join(reason.cards)} land"


def reading(name: str, env: Mapping, states: Mapping | None, now) -> Reading:
    """The line for `name` with each named card's Linear state.

    `states` maps a card id to its state name; a missing id, or one mapped to
    nothing, is unread. `now` is the moment every reader of one pass shares;
    the line itself carries no time.
    """
    states = states or {}
    line = off_line(name, env)
    reason = parse_reason(env.get(companion(name)))
    cards = reason.cards if reason else ()
    unread = tuple(card for card in cards if not states.get(card))
    off = not is_on(name, env)
    if not off or not cards:
        return Reading(line, off, False, cards, unread)
    named = ", ".join(f"{card} {states.get(card) or UNREAD}" for card in cards)
    cleared = not unread and all(states[card] in prose_blockers.TERMINAL
                                 for card in cards)
    if cleared:
        line = (f"{name} is off — its reason cleared: {named}; "
                "the switch may be turned on")
    else:
        line = f"{line}: {named}"
    return Reading(line, off, cleared, cards, unread)


# --------------------------------------------------------------------------- #
# the catalog                                                                  #
# --------------------------------------------------------------------------- #


def load(path: str | None = None) -> dict:
    """The catalog, from `config/switches.json` unless `path` names another."""
    with open(path or os.path.join(ROOT, CATALOG), encoding="utf-8") as handle:
        return json.load(handle)


class Read(NamedTuple):
    name: str
    file: str
    line: int
    step: str | None


_READ = re.compile(r"\bvars\.([A-Z][A-Z0-9_]*_LIVE)\b")
_ITEM = re.compile(r"^(?P<indent>\s*)-\s")
_STEP_NAME = re.compile(r"^(?P<indent>\s*)-\s+name:\s*(?P<name>\S.*?)\s*$")
_NAME_KEY = re.compile(r"^(?P<indent>\s*)name:\s*(?P<name>\S.*?)\s*$")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _step_name(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return raw[1:-1]
    return raw.split(" #", 1)[0].rstrip()


def _reads(rel: str, text: str) -> list:
    """Every switch read in one workflow's text, each with its enclosing step.

    A step opens at a list item; its `name` is the item's own `- name:` or a
    `name:` key two columns in. It closes at the next item at its column or at
    any line left of that column — a job key, the next job.
    """
    found = []
    step, column = None, None
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = _indent(line)
        item = _ITEM.match(line)
        if column is not None and (indent < column or (item and indent == column)):
            step, column = None, None
        if item and column is None:
            column = indent
            named = _STEP_NAME.match(line)
            step = _step_name(named.group("name")) if named else None
        elif column is not None and step is None:
            key = _NAME_KEY.match(line)
            if key and len(key.group("indent")) == column + 2:
                step = _step_name(key.group("name"))
        code = line.split(" #", 1)[0]
        for name in _READ.findall(code):
            found.append(Read(name, rel, number, step))
    return found


def discover(root: str | None = None) -> list:
    """Every `vars.<NAME>_LIVE` read under `<root>/.github/workflows/`."""
    base = Path(root or ROOT)
    found = []
    for path in sorted((base / WORKFLOWS).rglob("*")):
        if path.suffix not in (".yml", ".yaml") or not path.is_file():
            continue
        rel = path.relative_to(base).as_posix()
        found += _reads(rel, path.read_text(encoding="utf-8"))
    return found


def _header_problems(doc: dict) -> list:
    found = []
    if not str(doc.get("_readme") or "").strip():
        found.append("the catalog carries no _readme")
    if doc.get("version") != CATALOG_VERSION:
        found.append(f"the catalog's version is {doc.get('version')!r}, "
                     f"not {CATALOG_VERSION}")
    if doc.get("companion_suffix") != COMPANION_SUFFIX:
        found.append(f"the catalog's companion_suffix is "
                     f"{doc.get('companion_suffix')!r}; the contract fixes "
                     f"{COMPANION_SUFFIX!r}")
    hours = doc.get("alarm_after_hours")
    if isinstance(hours, bool) or not isinstance(hours, (int, float)) or hours <= 0:
        found.append(f"the catalog's alarm_after_hours is {hours!r}, "
                     "not a positive number of hours")
    return found


def problems(doc: dict | None = None, root: str | None = None) -> list:
    """Everything wrong with the catalog against the tree at `root`."""
    root = root or ROOT
    doc = doc if doc is not None else load(os.path.join(root, CATALOG))
    found = _header_problems(doc)
    reads = discover(root)
    rows = doc.get("switches") or []
    names = [row.get("name") for row in rows]
    for index, row in enumerate(rows):
        name = row.get("name") or f"row {index}"
        missing = [field for field in ROW_FIELDS if not str(row.get(field) or "").strip()]
        if missing:
            found.append(f"{name} names no {', no '.join(missing)}")
        if row.get("name") and names.count(row["name"]) > 1 and names.index(row["name"]) == index:
            found.append(f"{name} has more than one row in {CATALOG}")
        if missing:
            continue
        if not any(r.name == row["name"] and r.file == row["reader"]
                   and r.step == row["step"] for r in reads):
            found.append(f"{name}: {row['reader']} reads no vars.{row['name']} "
                         f"inside the step {row['step']!r} — a row whose read "
                         "is gone has outlived its switch, and a moved read "
                         "moves its row")
    for read in reads:
        if read.name not in names:
            step = repr(read.step) if read.step else "outside any step"
            found.append(f"{read.name} is read at {read.file}:{read.line} "
                         f"(step {step}) and has no row in {CATALOG} — add one: "
                         "its reader, its step and what it means")
    return found


# --------------------------------------------------------------------------- #
# the sweep's read                                                             #
# --------------------------------------------------------------------------- #

PREFIX = "switches:"

#: The named cards' states, by number — the `issues(filter: …)` shape
#: `linear_ops.find_open_prefix` uses, so a switch naming any number of cards
#: costs one request.
_STATES_QUERY = """query($numbers: [Float!]) {
           issues(first: 50, filter: {
             team: {key: {eq: "DRE"}},
             number: {in: $numbers}
           }) { nodes { identifier state { name } } } }"""


def read_states(cards, *, gql) -> dict:
    """Each named card's Linear state name, keyed by id, in ONE `gql` request.

    No cards is no request. A card Linear does not answer for is absent, which
    `reading` prints as unread. A refusal raises — `gql`'s own exception.
    """
    cards = tuple(cards)
    if not cards:
        return {}
    data = gql(_STATES_QUERY,
               {"numbers": [int(card.split("-")[1]) for card in cards]})
    states = {}
    for node in (data.get("issues") or {}).get("nodes") or []:
        ident = node.get("identifier")
        if ident in cards:
            states[ident] = (node.get("state") or {}).get("name")
    return states


def _one_line(text: str) -> str:
    return " ".join(str(text).split())


def read_switches(env: Mapping, *, gql, now, catalog: dict | None = None,
                  linear=None, live: bool = False) -> list:
    """The `switches:` line of every catalog switch, in the catalog's order.

    Handed a `linear`, a switch whose reason cleared also gets the receipt
    phase's lines, right after its own (`receipt_phase`).
    """
    doc = catalog if catalog is not None else load()
    lines = []
    for row in doc.get("switches") or []:
        name = row["name"]
        reason = parse_reason(env.get(companion(name)))
        states, error = {}, None
        if not is_on(name, env) and reason and reason.cards:
            try:
                states = read_states(reason.cards, gql=gql)
            except Exception as exc:  # noqa: BLE001 — every refusal is unread
                states, error = {}, exc
        read = reading(name, env, states, now)
        line = read.line
        if error is not None:
            line = f"{line} — the read failed: {_one_line(error) or type(error).__name__}"
        lines.append(f"{PREFIX} {line}")
        if linear is not None and read.cleared:
            lines += receipt_phase(name, read, states, env, linear=linear, now=now,
                                   hours=doc.get("alarm_after_hours"), live=live)
    return lines


# --------------------------------------------------------------------------- #
# the receipt and the alarm (DRE-6437)                                         #
# --------------------------------------------------------------------------- #

RECEIPT_ICON = "🔀"
WOULD = "would:"


def _pt(moment: datetime) -> str:
    """`2026-10-09 05:00 PT` — every time a person reads is Pacific."""
    try:
        from zoneinfo import ZoneInfo  # noqa: PLC0415

        local = moment.astimezone(ZoneInfo("America/Los_Angeles"))
        return local.strftime("%Y-%m-%d %H:%M PT")
    except Exception:  # noqa: BLE001 — no tz database: say UTC, never a wrong PT
        return moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def receipt_key(name: str, slug: str) -> str:
    """What the first line of `name`'s receipt in `slug` opens with."""
    return f"{RECEIPT_ICON} switch-cleared: {name} in {slug}"


def receipt_line(name: str, slug: str, repo: str, states: Mapping, cards, now) -> str:
    """The receipt's first line — its once-key, the cards, and the command."""
    named = ", ".join(f"{card} {states[card]}" for card in cards)
    return (f"{receipt_key(name, slug)} — its reason cleared at {_pt(now)}: "
            f"{named}. It may be turned on: gh variable set {name} --body true "
            f"-R {repo}")


def alarm_title(name: str, slug: str) -> str:
    return f"Switch {name} in {slug} is still off after its reason cleared"


def standing_receipt(thread, name: str, slug: str) -> dict | None:
    """The oldest comment whose FIRST line opens `name`'s key in `slug`.

    The key ends where the slug ends: `agent-bureau-console` is another
    repository, and a comment quoting a receipt below its own first line is
    not one.
    """
    key = receipt_key(name, slug)
    for node in thread or ():
        first = (node.get("body") or "").split("\n", 1)[0].strip()
        if first == key or first.startswith(f"{key} "):
            return node
    return None


def _moment(stamp) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(stamp or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else None


def _hours(seconds: float) -> str:
    whole = int(seconds // 3600)
    return f"{whole} hour" if whole == 1 else f"{whole} hours"


def alarm_body(name: str, slug: str, repo: str, states: Mapping, cards,
               first: str, posted: datetime, now) -> str:
    named = ", ".join(f"{card} {states[card]}" for card in cards)
    since = _hours((now - posted).total_seconds())
    return "\n".join([
        f"The switch `{name}` in {slug} is still off {since} after its reason "
        f"cleared. The sweep posted its receipt on {first} at {_pt(posted)}: "
        f"every card the switch waited on is terminal — {named}.",
        "",
        "The sweep never sets the variable: turning a production behavior on "
        "is a person's act. If it should come on, this is the one command:",
        "",
        f"    gh variable set {name} --body true -R {repo}",
        "",
        f"then delete its reason with `gh variable delete {companion(name)} -R "
        f"{repo}`. If it should stay off, give it a new reason naming the cards "
        f"it now waits on (`gh variable set {companion(name)} --body \"DRE-<n>\" "
        f"-R {repo}`), and close this card.",
        "",
        "Filed by the sweep's `Read the switches` step (scripts/switch_reason.py, "
        "DRE-6437); docs/switches.md describes it.",
    ])


def receipt_phase(name: str, read: Reading, states: Mapping, env: Mapping, *,
                  linear, now, hours, live: bool) -> list:
    """The receipt, or the alarm, for one switch whose reason cleared.

    Every write is preceded by its read, and every failure is a line rather
    than an exception, so one switch's trouble never costs another its turn.
    """
    if not read.off or not read.cleared or not read.cards:
        return []
    repo = (env.get("REPO") or "").strip()
    if not repo:
        return [f"{PREFIX} {name} — no receipt read: REPO names no repository"]
    slug = (env.get("REPO_SLUG") or "").strip() or repo.rsplit("/", 1)[-1].lower()
    first = read.cards[0]
    try:
        thread = linear.thread(first)
    except Exception as exc:  # noqa: BLE001 — a refused read posts nothing
        return [f"{PREFIX} {name} — {first}'s thread could not be read, so no "
                f"receipt was posted: {_one_line(exc) or type(exc).__name__}"]
    receipt = standing_receipt(thread, name, slug)
    if receipt is None:
        line = receipt_line(name, slug, repo, states, read.cards, now)
        if not live:
            return [f"{WOULD} post {first} — {line}"]
        body = pipeline_act.receipt("switch-reason-cleared", line)
        try:
            refused = linear.cmd_comment(first, body)
        except Exception as exc:  # noqa: BLE001 — reported, never fatal
            refused = _one_line(exc) or type(exc).__name__
        if refused:
            return [f"{PREFIX} {name} — the receipt was not posted on {first}: "
                    f"{_one_line(refused)}"]
        return [f"{PREFIX} {name} — receipt posted on {first}"]

    posted = _moment(receipt.get("createdAt"))
    if posted is None:
        return [f"{PREFIX} {name} — the receipt on {first} carries no readable "
                f"time ({receipt.get('createdAt')!r}), so no alarm is timed from it"]
    age = (now - posted).total_seconds()
    if isinstance(hours, bool) or not isinstance(hours, (int, float)) or hours <= 0:
        return [f"{PREFIX} {name} — the receipt already stands on {first}, and "
                f"{CATALOG} names no alarm_after_hours to time an alarm by"]
    if age < hours * 3600:
        return [f"{PREFIX} {name} — the receipt already stands on {first}, "
                f"posted {_pt(posted)}"]
    title = alarm_title(name, slug)
    try:
        open_card = linear.find_open(title)
    except Exception as exc:  # noqa: BLE001 — an unread dedupe files nothing
        return [f"{PREFIX} {name} — still off {_hours(age)} after its reason "
                "cleared, and the open alarms could not be read, so none was "
                f"filed: {_one_line(exc) or type(exc).__name__}"]
    if open_card:
        return [f"{PREFIX} {name} — still off {_hours(age)} after its reason "
                f"cleared; the alarm {open_card} is already open"]
    body = alarm_body(name, slug, repo, states, read.cards, first, posted, now)
    if not live:
        return [f"{WOULD} alarm — {title}"]
    try:
        issue = linear.create_card(title, body, repo_slug=slug)
    except Exception as exc:  # noqa: BLE001 — reported, never fatal
        return [f"{PREFIX} {name} — still off {_hours(age)} after its reason "
                f"cleared, and the alarm was not filed: "
                f"{_one_line(exc) or type(exc).__name__}"]
    return [f"{PREFIX} {name} — still off {_hours(age)} after its reason "
            f"cleared; filed the alarm {issue.get('identifier')} "
            f"{issue.get('url') or ''}".rstrip()]


class LinearWrites:
    """The receipt phase's one read and three writes, through `linear_ops`."""

    def __init__(self, linear_ops):
        self._ops = linear_ops

    def thread(self, identifier: str):
        """The whole thread, oldest first, paged past the window (DRE-5850)."""
        nodes, _viewer = self._ops._thread_and_viewer(identifier, "body",
                                                      "createdAt", whole=True)
        return nodes

    def cmd_comment(self, identifier: str, body: str):
        return self._ops.cmd_comment(identifier, body)

    def find_open(self, title: str):
        return self._ops.find_open(title)

    def create_card(self, title: str, body: str, *, repo_slug: str):
        return self._ops.create_card(title, body, repo_slug=repo_slug)


def _read_pass(env: Mapping | None, gql, now, linear=None, dry: bool = False) -> int:
    import linear_ops  # noqa: PLC0415 — the import stays off the pure path

    env = os.environ if env is None else env
    live = not dry and env.get("GITHUB_ACTIONS") == "true"
    try:
        for line in read_switches(env, gql=gql or linear_ops.gql,
                                  now=now or datetime.now(timezone.utc),
                                  linear=linear or LinearWrites(linear_ops),
                                  live=live):
            print(line, flush=True)
        return 0
    finally:
        print(linear_ops.budget_line(), flush=True)


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #


def main(argv=None, *, env: Mapping | None = None, gql=None, now=None,
         linear=None) -> int:
    """No command: the sweep's read, and its receipt phase (`--dry-run` writes
    nothing). `check`: the catalog against the tree."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true")
    sub = parser.add_subparsers(dest="command")
    p_check = sub.add_parser("check")
    p_check.add_argument("--root", default=ROOT)
    args = parser.parse_args(argv)
    if args.command is None:
        return _read_pass(env, gql, now, linear=linear, dry=args.dry_run)

    doc = load(os.path.join(args.root, CATALOG))
    found = problems(doc, root=args.root)
    for problem in found:
        print(f"  [FAIL] {problem}")
    print(f"{len(discover(args.root))} switch read(s), "
          f"{len(doc.get('switches') or [])} row(s), {len(found)} problem(s)")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
