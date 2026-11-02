#!/usr/bin/env python3
"""A failure already filed as a card adds a count to that card (DRE-8635).

The title "a fix commit is stranded on the merged branch" was filed eight
times from 09-20, and three of those cards — DRE-4828, DRE-4831 and DRE-4861 —
are one failure, portico's `agent/DRE-4518-mcp-form-read-tools`, filed three
times. Both of its writers looked only for an OPEN card with the exact title
(`linear_ops.find_open`), so once a person canceled the card, or marked it a
duplicate, the next sweep found the same stranded commits and filed it again.

## The rule

`file_or_tally(title, body, *, occurrence, repo_slug, labels) -> Tally`. The
title is the failure's key and `occurrence` names this one instance of it —
for the stranded-fix class, `<branch>@<sha8 of the newest stranded commit>`.

1. **Find the card.** Every card carrying `repo:<repo_slug>` whose title
   equals `title` exactly, filed within `FAILURE_TALLY_WINDOW_DAYS`, in any
   state (`linear_ops.repo_cards_by_title_prefix`, kept to exact equality). A
   search that fails files nothing and answers `unread`: a card minted on a
   search that never answered is the duplicate the search exists to prevent.
2. **Nothing found:** file the card (`linear_ops.cmd_oneoff`) with one line
   added at the end of its body, `Occurrence: <occurrence>`. `filed`.
3. **The newest match is open, Canceled or Duplicate:** if the occurrence is
   already on it — in its description or in a `🔢 failure-tally:` receipt the
   pipeline wrote — nothing is written and the answer is `already-tallied`.
   Otherwise the tally receipt is posted on it, composed through the act
   registry as the `failure-tally` act, its first line
   `🔢 failure-tally: <n> · <occurrence>`, where `<n>` counts every occurrence,
   the first filing included: 2 plus the receipts already on the card.
   `tallied`. A canceled card keeps the person's decision and gains the count,
   so the failure's history stays on one card.
4. **The newest match is Done:** the failure came back after it was fixed. A
   new card is filed as in 2, naming the Done card: `This is occurrence <n> of
   this failure; <DRE-n> closed it as Done on <date>.` A count on a closed
   card is seen by nobody. `filed`. An occurrence the Done card already names
   is the same instance read again, not a return, and answers
   `already-tallied`.

Every call prints one line: `failure-tally: <outcome> · <title> · card=<id>`.

## Who uses it

The stranded-fix class, at both of its writers: `reconcile._file_stranded_fix_card`
and `scripts/report_fix_result.sh` (through the `file` command below). Any
other card writer — the flaky-test card, the proof-gap card, the red-main
repair card — can adopt it by calling `file_or_tally` with its own title and an
occurrence that names one instance of its failure, stable across sweeps that
see the same instance and different for a new one. The title stays the
writer's per-failure key.

## CLI

    failure_tally.py file --title T --body-file F --occurrence O --repo SLUG \\
        [--label L ...]

Exit 0 on `filed`, `tallied` or `already-tallied`; 1 on `unread` or a write
that failed.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import linear_ops  # noqa: E402
import pipeline_act  # noqa: E402

# --------------------------------------------------------------------------- #
# the contract (identical in every card of DRE-8606)                           #
# --------------------------------------------------------------------------- #

ACT = "failure-tally"
RECEIPT_MARK = "🔢"
RECEIPT_KEY = f"{RECEIPT_MARK} {ACT}:"
#: The act's registry tag, which every receipt's trailer carries.
FAILURE_TALLY_TAG = "failure-tally-receipt"
LOG_PREFIX = "failure-tally:"
FAILURE_TALLY_WINDOW_DAYS = 30
#: The receipt's separator: space, U+00B7, space.
SEP = " · "

FILED = "filed"
TALLIED = "tallied"
ALREADY_TALLIED = "already-tallied"
UNREAD = "unread"
OUTCOMES = (FILED, TALLIED, ALREADY_TALLIED, UNREAD)

DONE = "Done"
OCCURRENCE_LINE = "Occurrence: "

EXIT_OK = 0
EXIT_FAILED = 1


@dataclass(frozen=True)
class Tally:
    """What one call did: its outcome, and the card it was on or filed."""

    outcome: str
    card: str | None


# --------------------------------------------------------------------------- #
# reading                                                                      #
# --------------------------------------------------------------------------- #


def _names(text: str, occurrence: str) -> bool:
    """Does `text` carry `occurrence` as a whole token? Bounded, so a branch
    that only ends the same way, or a longer sha, is not this occurrence."""
    return bool(re.search(
        rf"(?<![\w./-]){re.escape(occurrence)}(?![\w])", text or ""))


def is_receipt(entry) -> bool:
    """A comment the pipeline wrote whose body opens with `RECEIPT_KEY`. Its
    opening only, so a receipt quoted inside another comment counts nothing,
    and a comment anyone else wrote counts nothing either."""
    if not isinstance(entry, dict) or not entry.get("authored_by_pipeline"):
        return False
    return (entry.get("body") or "").lstrip().startswith(RECEIPT_KEY)


def _receipts(records) -> list[str]:
    return [r.get("body") or "" for r in records or [] if is_receipt(r)]


def _newest(cards: list[dict]) -> dict | None:
    return max(cards, key=lambda c: c.get("createdAt") or "", default=None)


def _day(value: str) -> str:
    return (value or "")[:10] or "an unrecorded date"


# --------------------------------------------------------------------------- #
# writing                                                                      #
# --------------------------------------------------------------------------- #


def receipt_line(n: int, occurrence: str) -> str:
    return f"{RECEIPT_KEY} {n}{SEP}{occurrence}"


def receipt_body(n: int, occurrence: str, state: str) -> str:
    return (f"{receipt_line(n, occurrence)}\n\n"
            f"This failure happened again: `{occurrence}` is new since this "
            f"card was filed. It is occurrence {n} of the failure, counted "
            f"here rather than filed as another card, and the card stays "
            f"{state}, as it was left.")


def _with_occurrence(body: str, occurrence: str, *extra: str) -> str:
    parts = [(body or "").rstrip("\n"), *extra, f"{OCCURRENCE_LINE}{occurrence}"]
    return "\n\n".join(p for p in parts if p) + "\n"


def _file(title: str, body: str, labels) -> str | None:
    flags: list[str] = []
    for label in labels or ():
        flags += ["--label", label]
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False,
                                     encoding="utf-8") as fh:
        fh.write(body)
        path = fh.name
    try:
        issue = linear_ops.cmd_oneoff(title, path, *flags)
    finally:
        os.unlink(path)
    return (issue or {}).get("identifier") or None


def _post(card: str, body: str) -> None:
    """Post one receipt through the act registry. Raises on any failure,
    including a comment cap, so a count is never read as made that did not
    land."""
    problem = linear_ops.cmd_comment(card, pipeline_act.receipt("failure-tally", body))
    if problem:
        raise RuntimeError(problem)


def _say(tally: Tally, title: str) -> Tally:
    print(f"{LOG_PREFIX} {tally.outcome}{SEP}{title}{SEP}card={tally.card or 'none'}")
    return tally


# --------------------------------------------------------------------------- #
# the rule                                                                     #
# --------------------------------------------------------------------------- #


def file_or_tally(title: str, body: str, *, occurrence: str, repo_slug: str,
                  labels, now: datetime | None = None) -> Tally:
    """File the failure's card, or add a count to the card it is already on.
    The module docstring states the rule."""
    since = (now or datetime.now(UTC)) - timedelta(days=FAILURE_TALLY_WINDOW_DAYS)
    try:
        found = linear_ops.repo_cards_by_title_prefix(
            title, repo_slug=repo_slug, since=since)
    except Exception as exc:  # noqa: BLE001 — a search that never answered
        print(f"{LOG_PREFIX} the search for {title!r} did not answer — {exc}")
        return _say(Tally(UNREAD, None), title)
    card = _newest([c for c in found or [] if c.get("title") == title])

    if card is None:
        filed = _file(title, _with_occurrence(body, occurrence), labels)
        return _say(Tally(FILED, filed), title)

    identifier = card.get("identifier") or ""
    try:
        receipts = _receipts(linear_ops.comment_records(identifier, whole_thread=True))
    except Exception as exc:  # noqa: BLE001 — an unread thread counts nothing
        print(f"{LOG_PREFIX} {identifier}'s thread did not answer — {exc}")
        return _say(Tally(UNREAD, identifier), title)

    if _names(card.get("description") or "", occurrence) or any(
            _names(r, occurrence) for r in receipts):
        return _say(Tally(ALREADY_TALLIED, identifier), title)

    n = 2 + len(receipts)
    state = card.get("state") or ""
    if state == DONE:
        returned = (f"This is occurrence {n} of this failure; {identifier} "
                    f"closed it as Done on {_day(card.get('completedAt'))}.")
        filed = _file(title, _with_occurrence(body, occurrence, returned), labels)
        return _say(Tally(FILED, filed), title)

    _post(identifier, receipt_body(n, occurrence, state or "where it is"))
    return _say(Tally(TALLIED, identifier), title)


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #


def _flag_values(flags, name: str) -> list:
    """Every value given as `<name> <value>` in `flags`, in order."""
    return [flags[i + 1] for i in range(len(flags) - 1) if flags[i] == name]


def _file_parser() -> argparse.ArgumentParser:
    f = argparse.ArgumentParser(prog="failure_tally.py file",
                                description="file the card, or add a count to it")
    f.add_argument("--title", required=True)
    f.add_argument("--body-file", required=True)
    f.add_argument("--occurrence", required=True)
    f.add_argument("--repo", required=True, help="the repo slug")
    f.add_argument("--label", action="append", default=[])
    return f


def cmd_file(*flags: str) -> int:
    args = _file_parser().parse_args(flags)
    # The labels are read off the flags themselves rather than argparse's
    # namespace, so `label_writers.py` can follow each one from the script
    # that names it to the card — `pipeline_notice.py`'s shape (DRE-6362).
    labels = _flag_values(flags, "--label")
    try:
        with open(args.body_file, encoding="utf-8") as fh:
            body = fh.read()
        tally = file_or_tally(args.title, body, occurrence=args.occurrence,
                              repo_slug=args.repo, labels=labels)
    except Exception as exc:  # noqa: BLE001 — say so; the caller names the finding
        print(f"ERROR: {LOG_PREFIX} {args.title!r} — {exc}", file=sys.stderr)
        return EXIT_FAILED
    return EXIT_FAILED if tally.outcome == UNREAD else EXIT_OK


#: The command line, by subcommand. A dict of handlers, the shape
#: `label_writers.py` reads a command line in.
COMMANDS = {"file": cmd_file}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if not argv or argv[0] not in COMMANDS:
        print("usage: failure_tally.py file --title T --body-file F "
              "--occurrence O --repo SLUG [--label L ...]", file=sys.stderr)
        return 2
    return COMMANDS[argv[0]](*argv[1:])


if __name__ == "__main__":
    sys.exit(main())
