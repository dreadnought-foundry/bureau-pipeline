#!/usr/bin/env python3
"""The operator agent's fixed routes (DRE-8743, epic DRE-8738).

Some cards waiting on a person must reach the CEO, or the studio host, whatever
a model reads into them. Those routes are declared once, as data, in
`config/operator-agent.json`, and this module reads them:

    load_rules(path=None) -> dict
    rule_outcome(title, description, rules=None) -> RuleMatch | None
    host_limits_text(rules=None) -> str
    fixture_card(record) -> dict

## The table

* `ceo_acts` — the six acts the CEO kept on 2026-10-30, one record per act,
  `{"id", "label", "phrases"}`, ids exactly `KEPT_ACTS`.
* `destructive_go` — `destructive` (a history rewrite, a force-push) and `go`
  (the card says the CEO's go is needed right before the step). The route
  fires only when both lists match.
* `studio_access` — phrases naming production access no test environment
  gives.
* `deploy` — phrases naming a deploy step, so a receipt can say the card also
  waits on `studio.deploy_epic`.
* `studio` — `blocker`, `deploy_epic`, `never_blocker` and `host_limits`. The
  apply reads the ids from here and never restates them. `host_limits` is
  `{"source", "read_on", "cannot"}`: this repository's one copy of what the
  studio host cannot do as built, written from the document `source` names as
  the operator read it on `read_on`. A run here does not read that other
  repository, so a `studio` receipt quotes this copy and nothing else.
* `leave_alone` — the cards the agent never takes, `{"card", "why"}` each.
  The sibling `select` reads this list, never a copy.

## Matching

A phrase matches case-insensitively and whole — never inside a longer word,
with any run of whitespace standing for a space, a curly apostrophe read as a
straight one (`operator_step_kind`'s matcher) — in the card's title or
description, outside fenced code. Its last word also matches in the singular
or the plural, so `AWS role` fires on `AWS roles`, `AWS identity` on `AWS
identities` and `org-level secrets` on `org-level secret`; `AWS roleplay`
still matches nothing. Every phrase was taken from a sentence of
the replay fixture, `tests/fixtures/operator-agent-board-2026-11-01.json`.

## Precedence

1. A CEO-kept act: the first act in the table's order with a phrase in the
   text. `github-org-creation` is listed before `github-secrets` because
   standing up a new organization includes setting its first secrets
   (DRE-8227 does both).
2. `destructive-go`: a `destructive` phrase and a `go` phrase both match.
3. Studio access.

No match returns `None`: the card is the model's to read. A rule never decides
a card on its own — a card the model has not answered gets no decision on
that pass, and a `done-already` answer quoting the card's met criteria word
for word comes before a kept-act match (both DRE-8744). A `studio` match
chooses the route; the card is applied only when the model quotes a line of
`host_limits_text()` for that step, and is otherwise held (DRE-8744).

Pure: it reads the one table and writes nothing — no model, no Linear, no
network.

    python3 scripts/operator_rules.py "<title>" "<description>"
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from operator_step_kind import _APOSTROPHES

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "operator-agent.json"

KEYS = ("ceo_acts", "destructive_go", "studio_access", "deploy", "studio", "leave_alone")
KEPT_ACTS = (
    "aws-access", "billing-and-account-settings", "github-org-membership",
    "github-secrets", "github-branch-protection", "github-org-creation",
)
DESTRUCTIVE_GO = "destructive-go"
CEO = "ceo"
STUDIO = "studio"
STUDIO_KEYS = ("blocker", "deploy_epic", "never_blocker", "host_limits")
HOST_LIMIT_KEYS = ("source", "read_on", "cannot")

#: `fixture_card`'s `author_kind` for every comment. A fixture excerpt keeps
#: no author, and every comment excerpt in the replay fixture was written by a
#: person or the operator's own session, never by the pipeline.
FIXTURE_AUTHOR_KIND = "person"

_FENCE = re.compile(r"(`{3,}|~{3,})")
_LAST_WORD = re.compile(r"([A-Za-z]+)$")


@dataclass(frozen=True)
class RuleMatch:
    """One fixed route. `act` is a `KEPT_ACTS` id or `destructive-go` for
    `ceo`, and empty for `studio`. `phrase` is the words that matched, as the
    card writes them. `deploy` is true when a `deploy` phrase matched."""

    outcome: str
    act: str
    phrase: str
    deploy: bool


def _exact_keys(what: str, found: dict, keys) -> None:
    if not isinstance(found, dict):
        raise ValueError(f"{what} is not an object")
    unknown = sorted(set(found) - set(keys))
    missing = [key for key in keys if key not in found]
    if unknown:
        raise ValueError(f"{what} has an unknown key: {', '.join(unknown)}")
    if missing:
        raise ValueError(f"{what} lacks {', '.join(missing)}")


def _phrases(what: str, found) -> list:
    if (not isinstance(found, list) or not found
            or not all(isinstance(p, str) and p.strip() for p in found)):
        raise ValueError(f"{what} names no phrase — a phrase list may not be empty")
    return found


def _text(what: str, found) -> str:
    if not isinstance(found, str) or not found.strip():
        raise ValueError(f"{what} is empty")
    return found


def load_rules(path: Path | str | None = None) -> dict:
    """The table, checked. A table that breaks the shape is refused with a
    `ValueError` naming what broke: an unknown or missing key, an empty phrase
    list, an act id outside `KEPT_ACTS` or named twice, a `host_limits`
    without its `source` or with an empty `cannot`, or a `leave_alone` record
    without both `card` and `why`."""
    with open(path or CONFIG_PATH, encoding="utf-8") as fh:
        table = json.load(fh)
    _exact_keys("the table", table, KEYS)

    seen = []
    for act in table["ceo_acts"]:
        _exact_keys("a ceo_acts record", act, ("id", "label", "phrases"))
        if act["id"] not in KEPT_ACTS:
            raise ValueError(f"{act['id']!r} is not a kept act — the acts are "
                             f"{', '.join(KEPT_ACTS)}")
        if act["id"] in seen:
            raise ValueError(f"the act {act['id']} is named twice")
        seen.append(act["id"])
        _text(f"the {act['id']} label", act["label"])
        _phrases(f"the {act['id']} act", act["phrases"])
    if sorted(seen) != sorted(KEPT_ACTS):
        missing = sorted(set(KEPT_ACTS) - set(seen))
        raise ValueError(f"ceo_acts lacks {', '.join(missing)}")

    _exact_keys("destructive_go", table["destructive_go"], ("destructive", "go"))
    _phrases("destructive_go.destructive", table["destructive_go"]["destructive"])
    _phrases("destructive_go.go", table["destructive_go"]["go"])
    _phrases("studio_access", table["studio_access"])
    _phrases("deploy", table["deploy"])

    studio = table["studio"]
    _exact_keys("studio", studio, STUDIO_KEYS)
    for key in ("blocker", "deploy_epic", "never_blocker"):
        _text(f"studio.{key}", studio[key])
    limits = studio["host_limits"]
    _exact_keys("studio.host_limits", limits, HOST_LIMIT_KEYS)
    _text("studio.host_limits.source", limits["source"])
    _text("studio.host_limits.read_on", limits["read_on"])
    cannot = limits["cannot"]
    if (not isinstance(cannot, list) or not cannot
            or not all(isinstance(line, str) and line.strip() for line in cannot)):
        raise ValueError("studio.host_limits.cannot is empty — the host's limits "
                         "are what a studio receipt quotes")

    if not isinstance(table["leave_alone"], list):
        raise ValueError("leave_alone is not a list")
    for row in table["leave_alone"]:
        _exact_keys("a leave_alone record", row, ("card", "why"))
        _text("a leave_alone card", row["card"])
        _text(f"{row['card']}'s why", row["why"])
    return table


def host_limits_text(rules: dict | None = None) -> str:
    """The `cannot` lines joined by newlines: the text the decision checks a
    `studio` quote against."""
    rules = rules if rules is not None else load_rules()
    return "\n".join(rules["studio"]["host_limits"]["cannot"])


def _outside_fences(text: str) -> str:
    """`text` with every fenced code block taken out. An unclosed fence runs
    to the end, as Markdown draws it."""
    kept, fence = [], None
    for line in text.splitlines():
        stripped = line.strip()
        if fence is None:
            opened = _FENCE.match(stripped)
            if opened:
                fence = opened.group(1)
                continue
            kept.append(line)
        elif stripped.startswith(fence) and not stripped.strip(fence[0]):
            fence = None
    return "\n".join(kept)


def _number(word: str) -> str:
    """A pattern for `word` in the singular or the plural."""
    if re.search(r"[^aeiou]y$", word, re.IGNORECASE):
        return re.escape(word[:-1]) + "(?:y|ies)"
    if re.search(r"[^s]s$", word, re.IGNORECASE):
        return re.escape(word[:-1]) + "s?"
    return re.escape(word) + "(?:e?s)?"


def _phrase_re(phrase: str) -> re.Pattern:
    """`operator_step_kind`'s whole-phrase matcher with the last word read in
    either number. That matcher stays exact: its own tests pin it."""
    phrase = phrase.translate(_APOSTROPHES)
    last = _LAST_WORD.search(phrase)
    stem = phrase[:last.start()] if last else phrase
    body = re.escape(stem).replace("\\ ", r"\s+") + (_number(last.group(1)) if last else "")
    head = r"(?<![\w-])" if re.match(r"\w", phrase) else ""
    tail = r"(?![\w-])" if re.search(r"\w$", phrase) else ""
    return re.compile(f"{head}{body}{tail}", re.IGNORECASE)


def _first(text: str, phrases) -> str | None:
    """The card's own words for the first phrase, in list order, that
    matches."""
    for phrase in phrases:
        found = _phrase_re(phrase).search(text)
        if found:
            return found.group(0)
    return None


def rule_outcome(title: str | None, description: str | None,
                 rules: dict | None = None) -> RuleMatch | None:
    """The fixed route for one card, or `None` — see the module docstring."""
    rules = rules if rules is not None else load_rules()
    text = f"{title or ''}\n{_outside_fences(description or '')}".translate(_APOSTROPHES)
    deploy = _first(text, rules["deploy"]) is not None
    for act in rules["ceo_acts"]:
        said = _first(text, act["phrases"])
        if said is not None:
            return RuleMatch(CEO, act["id"], said, deploy)
    destructive = _first(text, rules["destructive_go"]["destructive"])
    go = _first(text, rules["destructive_go"]["go"])
    if destructive is not None and go is not None:
        return RuleMatch(CEO, DESTRUCTIVE_GO, f"{destructive} … {go}", deploy)
    said = _first(text, rules["studio_access"])
    if said is not None:
        return RuleMatch(STUDIO, "", said, deploy)
    return None


def fixture_card(record: dict) -> dict:
    """One replay-fixture record as the card the siblings read: its
    description excerpts joined by a blank line, and its comment excerpts as
    `{"author_kind", "created_at", "body"}`."""
    excerpts = record["excerpts"]
    return {
        "card": record["card"],
        "lane": record["lane"],
        "title": record["title"],
        "labels": list(record["labels"]),
        "description": "\n\n".join(e["text"] for e in excerpts if e["from"] == "description"),
        "comments": [
            {"author_kind": FIXTURE_AUTHOR_KIND, "created_at": e["created_at"], "body": e["text"]}
            for e in excerpts if e["from"] == "comment"
        ],
    }


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not 1 <= len(args) <= 2:
        print(__doc__.strip().splitlines()[-1].strip(), file=sys.stderr)
        return 2
    match = rule_outcome(args[0], args[1] if len(args) > 1 else "")
    if match is None:
        print("no rule — the model's to read")
    else:
        print(f"{match.outcome} {match.act or '-'} — {match.phrase}"
              + (" (deploy)" if match.deploy else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
