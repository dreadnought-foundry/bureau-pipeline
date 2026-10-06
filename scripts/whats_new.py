#!/usr/bin/env python3
"""The What's New contract: the pull request line, the file, the switch (DRE-5508, stdlib only).

A person who uses a product learns what changed from one place: the release's
`whats-new.json`, which the train publishes as an asset on the product's
GitHub Release. Every entry in that file is written exactly ONCE, as a line in
the body of the pull request that made the change:

    What's new: none
    What's new: <kind>, <audience>: <sentence> [<second sentence>] [(open: </path>)]

The file and the Linear release note are both derived from those lines and
never edited by hand. `standards/whats-new.md` is the rule for whoever writes
the line or builds a panel that reads the file; this module is the one thing
every consumer runs — the gate, the critic's context, the collector and the
train import it, and a product repository validates its file with

    python3 .bureau-pipeline/scripts/whats_new.py validate whats-new.json

Two questions decide whether a pull request is held for a missing line, and
they are separate on purpose:

  * `required_for(head_branch)` — does this pull request owe a line at all?
    The branch alone answers it: a machine-written branch has no sentence to
    write. It never reads the cutover.
  * `enforced_for(created_at)` — is the rule switched on for a pull request
    opened then? `config/whats-new-cutover.json` (DRE-5576) names the instant;
    absent, the rule is off. A pull request opened before the instant could
    not have known to write the line.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

KINDS = ("new", "improved", "fixed")
AUDIENCES = ("everyone", "moderators", "admins")

#: Machine-authored pull requests: no person writes their body, nothing in
#: them is something a person sees, and they count as `none`.
EXEMPT_PREFIXES = ("dependabot/", "repair/", "bot/")

#: The switch. Absent: the rule is off. Present: the instant it is on from.
CUTOVER_FILE = Path(__file__).resolve().parents[1] / "config" / "whats-new-cutover.json"

INTERNAL_WORDS = (
    "PR", "pull request", "merge", "merged", "commit", "refactor", "CI",
    "workflow", "endpoint", "schema", "migration", "card", "branch",
)

#: Matched exactly as written; every other internal word ignores case.
_CASE_SENSITIVE_WORDS = ("PR", "CI")

FORMS = (
    "What's new: none\n"
    "What's new: <kind>, <audience>: <sentence> [<second sentence>] [(open: </path>)]\n"
    f"  <kind> is one of {', '.join(KINDS)}; "
    f"<audience> is one of {', '.join(AUDIENCES)}; "
    "the first sentence ends with a period."
)

_CUTOVER_SHAPE = (
    '{"enforced_from": "<ISO-8601 instant with a UTC offset>", '
    '"why": "<one sentence>"}'
)


class WhatsNewError(ValueError):
    """What is wrong, followed by what is accepted (the two line forms unless
    the caller names another shape)."""

    def __init__(self, problem: str, *, accepted: str = FORMS):
        self.problem = problem
        super().__init__(f"{problem}\nAccepted:\n{accepted}")


class NoLineError(WhatsNewError):
    """The body carries no `What's new:` line at all — as opposed to a line
    that is there and does not fit. Told apart by type, never by message: the
    other messages quote the author's text, which can say anything."""


@dataclass(frozen=True)
class Entry:
    kind: str
    audience: str
    title: str
    body: str = ""
    open: str | None = None
    #: The cards that delivered it, read off the pull request's branch by the
    #: collector (DRE-6015) — never parsed from the line, never in the title.
    cards: tuple[str, ...] = ()

    def as_item(self) -> dict:
        item = {"kind": self.kind, "audience": self.audience,
                "title": self.title, "body": self.body}
        if self.open is not None:
            item["open"] = self.open
        if self.cards:
            item["cards"] = list(self.cards)
        return item


# --- the pull request line ---------------------------------------------------

_LABEL = re.compile(r"^(\*\*)?What['’]s new:(?(1)\*\*)\s*(?P<rest>.*)$")
_FENCE = re.compile(r"^ {0,3}(?P<fence>`{3,}|~{3,})")
_HEAD = re.compile(r"^(?P<kind>[^,:\s]+)\s*,\s*(?P<audience>[^,:\s]+)\s*:\s*(?P<sentence>.*)$")
_OPEN = re.compile(r"\(open:\s*(?P<path>[^)]*)\)\s*$")
_TITLE_END = re.compile(r"\.(?=\s|$)|[?!]")

#: A page inside the product: one leading `/`, never `//` or `/\` — a browser
#: reads both as another site — and no whitespace or control characters.
_OPEN_PATH = re.compile(r"/(?![/\\])[^\s\x00-\x1f\x7f-\x9f]*")


def _valid_open(path: object) -> bool:
    """Is `path` a page inside the product? The line and the file share it."""
    return isinstance(path, str) and _OPEN_PATH.fullmatch(path) is not None


def _first_line(pr_body: str) -> str | None:
    """The text after the first `What's new:` label outside fenced code."""
    fence = None
    for line in pr_body.splitlines():
        opened = _FENCE.match(line)
        if fence is not None:
            closing = line.strip()
            if closing.startswith(fence) and not closing.strip(fence[0]):
                fence = None
            continue
        if opened:
            fence = opened["fence"]
            continue
        label = _LABEL.match(line.rstrip())
        if label:
            return label["rest"].strip()
    return None


def parse_line(pr_body: str) -> Entry | None:
    """The body's `What's new:` line: None for `none`, an Entry for a sentence.

    Raises NoLineError when there is no line, WhatsNewError when the line
    does not fit."""
    rest = _first_line(pr_body or "")
    if rest is None:
        raise NoLineError(
            "The pull request body has no `What's new:` line outside fenced code "
            "— put one in the first lines of the body.")
    if rest.lower() == "none":
        return None
    head = _HEAD.match(rest)
    if not head:
        raise WhatsNewError(
            f"The `What's new:` line {rest!r} is neither `none` nor "
            "`<kind>, <audience>: <sentence>`.")
    kind, audience = head["kind"], head["audience"]
    if kind not in KINDS:
        raise WhatsNewError(f"Unknown kind {kind!r} — use one of {', '.join(KINDS)}.")
    if audience not in AUDIENCES:
        raise WhatsNewError(
            f"Unknown audience {audience!r} — use one of {', '.join(AUDIENCES)}.")

    sentence, path = head["sentence"].strip(), None
    opened = _OPEN.search(sentence)
    if opened:
        path = opened["path"].strip()
        sentence = sentence[:opened.start()].strip()
        if not _valid_open(path):
            raise WhatsNewError(
                f"The open path {path!r} must be a page in the product: one leading `/`, "
                "never `//` or `/\\`, no spaces — `(open: /documents)`.")
    if not sentence:
        raise WhatsNewError("The `What's new:` line names a kind and audience but no sentence.")

    end = _TITLE_END.search(sentence)
    title = sentence if end is None else sentence[:end.end()]
    if not title.endswith("."):
        raise WhatsNewError(
            f"The first sentence {title!r} must end with a period.")
    return Entry(kind=kind, audience=audience, title=title,
                 body=sentence[end.end():].strip(), open=path)


def check_wording(title: str) -> list[str]:
    """The mechanical wording problems in a title, one sentence each."""
    problems = []
    for number in re.findall(r"\bDRE-\d+\b", title, re.I):
        problems.append(f"It names the card number {number} — say what a person can now do.")
    for number in re.findall(r"(?<![\w&])#\d+\b", title):
        problems.append(
            f"It names the pull request number {number} — say what a person can now do.")
    if "`" in title:
        problems.append("It contains a backtick — write plain words, not code.")
    for word in INTERNAL_WORDS:
        flags = 0 if word in _CASE_SENSITIVE_WORDS else re.I
        pattern = r"\b" + r"\s+".join(map(re.escape, word.split())) + r"\b"
        if re.search(pattern, title, flags):
            problems.append(
                f"It uses the internal word {word!r} — describe what a person sees instead.")
    return problems


# --- the switch --------------------------------------------------------------

_UNSET = object()


def _instant(text: object, field: str, *, accepted: str = FORMS) -> datetime:
    """An aware datetime from ISO-8601 text with a UTC offset, `Z` accepted."""
    if not isinstance(text, str):
        raise WhatsNewError(f"{field}: {text!r} is not an ISO-8601 instant.", accepted=accepted)
    raw = text.strip()
    if raw.endswith(("Z", "z")):
        raw = raw[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        raise WhatsNewError(
            f"{field}: {text!r} is not an ISO-8601 instant.", accepted=accepted) from None
    if moment.utcoffset() is None:
        raise WhatsNewError(
            f"{field}: {text!r} has no UTC offset — end it with `Z` or `+00:00`.",
            accepted=accepted)
    return moment


def required_for(head_branch: str) -> bool:
    """Does a pull request from this head branch owe a `What's new:` line?

    The branch alone answers it; the cutover is a separate question."""
    return not head_branch.startswith(EXEMPT_PREFIXES)


def enforced_from(path: Path | str | None = None) -> datetime | None:
    """The instant the rule is on from, or None when it is not switched on.

    `path` defaults to CUTOVER_FILE, read when called. A file that is present
    but does not fit is a repository defect, raised as WhatsNewError."""
    path = Path(CUTOVER_FILE if path is None else path)
    try:
        text = path.read_text()
    except FileNotFoundError:
        return None
    where = path.name
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise WhatsNewError(f"{where} is not JSON: {error}.", accepted=_CUTOVER_SHAPE) from None
    if not isinstance(data, dict):
        raise WhatsNewError(f"{where} must be a JSON object.", accepted=_CUTOVER_SHAPE)
    extra = sorted(set(data) - {"enforced_from", "why"})
    if extra:
        raise WhatsNewError(f"{where}: unknown key {extra[0]!r}.", accepted=_CUTOVER_SHAPE)
    for key in ("enforced_from", "why"):
        if key not in data:
            raise WhatsNewError(f"{where}: missing {key!r}.", accepted=_CUTOVER_SHAPE)
    if not isinstance(data["why"], str) or not data["why"].strip():
        raise WhatsNewError(f"{where}: 'why' must be a non-empty sentence.",
                            accepted=_CUTOVER_SHAPE)
    moment = _instant(data["enforced_from"], f"{where}: enforced_from", accepted=_CUTOVER_SHAPE)
    return moment.astimezone(timezone.utc)


def enforced_for(created_at: str | None, *, cutover=_UNSET) -> bool:
    """Is the rule on for a pull request opened at `created_at`?

    `cutover` defaults to enforced_from(); None means not switched on. A
    `created_at` of None asks only whether the rule is on."""
    if cutover is _UNSET:
        cutover = enforced_from()
    if cutover is None:
        return False
    if created_at is None:
        return True
    return _instant(created_at, "created_at") >= cutover


# --- the file ----------------------------------------------------------------

_DOCUMENT_KEYS = ("product", "release", "shipped", "items")
_ITEM_KEYS = ("kind", "audience", "title", "body", "open", "cards")

#: One card number in an item's `cards`.
_CARD = re.compile(r"DRE-[0-9]+")


def _item_problems(index: int, item: object) -> list[str]:
    at = f"items[{index}]"
    if not isinstance(item, dict):
        return [f"{at}: must be an object."]
    problems = [f"{at}.{key}: unknown key." for key in item if key not in _ITEM_KEYS]
    for key, allowed in (("kind", KINDS), ("audience", AUDIENCES)):
        if key not in item:
            problems.append(f"{at}.{key}: missing — one of {', '.join(allowed)}.")
        elif item[key] not in allowed:
            problems.append(f"{at}.{key}: {item[key]!r} is not one of {', '.join(allowed)}.")
    title = item.get("title")
    if not isinstance(title, str) or not title.strip():
        problems.append(f"{at}.title: missing — a sentence ending with a period.")
    elif not title.endswith("."):
        problems.append(f"{at}.title: {title!r} must end with a period.")
    else:
        problems.extend(f"{at}.title: {problem}" for problem in check_wording(title))
    if not isinstance(item.get("body"), str):
        problems.append(f"{at}.body: must be a string (may be empty).")
    if "open" in item and not _valid_open(item["open"]):
        problems.append(
            f"{at}.open: {item['open']!r} must be a page in the product: one leading `/`, "
            "never `//` or `/\\`, no spaces.")
    if "cards" in item:
        cards = item["cards"]
        if (not isinstance(cards, list) or not cards
                or not all(isinstance(card, str) and _CARD.fullmatch(card) for card in cards)):
            problems.append(
                f"{at}.cards: {cards!r} must be a non-empty list of card numbers like "
                "\"DRE-123\" — leave the key out when no card is known.")
    return problems


def validate(document) -> list[str]:
    """The problems with a whats-new.json document, one per problem naming the
    field; [] when valid."""
    if not isinstance(document, dict):
        return ["document: must be a JSON object."]
    problems = [f"{key}: unknown key." for key in document if key not in _DOCUMENT_KEYS]
    for key in ("product", "release"):
        if key not in document:
            problems.append(f"{key}: missing.")
        elif not isinstance(document[key], str) or not document[key].strip():
            problems.append(f"{key}: must be a non-empty string.")
    if "shipped" not in document:
        problems.append("shipped: missing — an ISO-8601 time with a UTC offset.")
    else:
        try:
            _instant(document["shipped"], "shipped")
        except WhatsNewError as error:
            problems.append(error.problem)
    items = document.get("items")
    if "items" not in document:
        problems.append("items: missing.")
    elif not isinstance(items, list):
        problems.append("items: must be a list.")
    elif not items:
        problems.append("items: empty — a release with nothing to say publishes no file.")
    else:
        for index, item in enumerate(items):
            problems.extend(_item_problems(index, item))
    return problems


# --- the command line --------------------------------------------------------

USAGE = (
    "usage: whats_new.py validate <whats-new.json>\n"
    "       whats_new.py line <pr-body-file>"
)


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] not in ("validate", "line"):
        print(USAGE, file=sys.stderr)
        return 2
    command, name = argv
    try:
        text = Path(name).read_text()
    except OSError as error:
        print(f"whats-new: cannot read {name}: {error.strerror}", file=sys.stderr)
        return 1
    if command == "line":
        try:
            entry = parse_line(text)
        except WhatsNewError as error:
            print(error, file=sys.stderr)
            return 1
        print("none" if entry is None else json.dumps(entry.as_item()))
        return 0
    try:
        document = json.loads(text)
    except json.JSONDecodeError as error:
        print(f"whats-new: {name} is not JSON: {error}", file=sys.stderr)
        return 1
    problems = validate(document)
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        return 1
    print(f"whats-new: {name} is valid ({len(document['items'])} items)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
