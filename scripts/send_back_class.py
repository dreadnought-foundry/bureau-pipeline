"""Classify a send-back finding as a revision or a decision (DRE-6356).

The one-off critic writes `SEND_BACK` or `QUESTION`, and nothing reads the
finding behind the word (`plan_critic.one_off_decide`). On DRE-3879 two of the
five rounds that reached the CEO named something the card had to SAY — an
unverifiable claim, two branch names he had already given — and were sent to
him as decisions. This module is the one reader that tells the two apart. It
routes nothing itself: the sibling card wires it into the one-off exit, so it
imports nothing from `plan_critic` or `planning_route` (they will import this
module, and a cycle is refused).

## The rule

* `revision` — the finding names something the card must SAY: a missing name,
  an unstated scope, an unverifiable claim, a decision that exists in a comment
  but is not written into the card, a contradiction between the card and the
  CEO's verified answer or between two of its own lines, no acceptance
  criteria. A rewrite fixes it.
* `decision` — the finding names a call nobody has made: a choice between
  defensible options, a standing rule that would have to change before the
  card can be built, or the classifier's own refusal of a card that asks for a
  decision rather than for work. Only the CEO can make it.
* Precedence — a finding whose complaint is PROVENANCE (a decision is claimed
  but not verified, not confirmed, not signed) is `revision` even when it also
  talks about a decision, because the remedy is re-stating the answer through
  the signed channel, never a judgement. That is the epic's reading of
  DRE-3879 round 2, and it is the one place the two lists overlap on purpose.
* Uncertain — a finding that matches both lists outside that rule, or
  neither, is `None`. `route_class()` sends it to the CEO; the reason sits
  beside `UNCERTAIN_GOES_TO`.

The vocabulary below is that rule as data, and it lives here and nowhere else.
Each phrase is a tuple of parts that must appear in the finding in that order
— most have one part. Matching ignores case, collapses whitespace and reads a
curly apostrophe as a straight one. The phrases are the words the critic
really wrote on DRE-3879 and DRE-3880 (`tests/fixtures/send-back-findings.json`
carries those findings verbatim), plus two board phrases quoted from the
modules that own them.

The CLI reads one finding per line and prints `<class>\\t<why>\\t<finding>`,
`<class>` being `revision`, `decision` or `uncertain`. It always exits 0:

    python3 scripts/send_back_class.py classify < findings.txt
"""

from __future__ import annotations

import argparse
import sys

REVISION = "revision"
DECISION = "decision"
# A finding this module cannot place goes to the CEO as a decision, because
# under-asking a genuine decision is the worse failure: a decision rewritten
# as card text ships somebody's guess, while a revision asked as a decision
# costs one round of his time.
UNCERTAIN_GOES_TO = DECISION

# The card must SAY something it does not.
REVISION_PHRASES: tuple[tuple[str, ...], ...] = (
    ("doesn't say which",),
    ("never written into the card's own checklist",),
    ("doesn't show up anywhere we can actually verify",),
    ("not confirmed to be from the CEO",),
    ("the card still", "but the CEO's newest, verified answer says"),
    # Board phrase: the one-off route's own refusal, `routing_verdict.py`.
    ("no exit condition to route on",),
)

# A call nobody has made.
DECISION_PHRASES: tuple[tuple[str, ...], ...] = (
    ("without picking one",),
    ("a security policy call",),
    ("nobody has made that call",),
    ("never decides whether",),
    ("cannot actually be built as written",),
    # Board phrase: the classifier's own refusal, `planning_classify.py`.
    ("asks for a decision rather than for work",),
)

# Precedence: a provenance complaint is a revision even beside a decision
# phrase. Every entry is also a REVISION_PHRASES entry.
PROVENANCE_PHRASES: tuple[tuple[str, ...], ...] = (
    ("doesn't show up anywhere we can actually verify",),
    ("not confirmed to be from the CEO",),
)

# The two phrases quoted from the modules that own the strings, rather than
# from a critic finding.
BOARD_QUOTED: tuple[tuple[str, ...], ...] = (
    ("no exit condition to route on",),
    ("asks for a decision rather than for work",),
)


def _norm(text: str) -> str:
    return " ".join(text.replace("’", "'").split()).lower()


def _has(finding: str, phrase: tuple[str, ...]) -> bool:
    at = 0
    for part in phrase:
        found = finding.find(_norm(part), at)
        if found < 0:
            return False
        at = found + len(_norm(part))
    return True


def matches(finding: str, phrases: tuple[tuple[str, ...], ...]) -> list[tuple[str, ...]]:
    """The phrases of `phrases` that `finding` carries, in list order."""
    text = _norm(finding)
    return [p for p in phrases if _has(text, p)]


def _show(phrase: tuple[str, ...]) -> str:
    return '"' + " … ".join(phrase) + '"'


def _read(finding: str) -> tuple[str | None, str]:
    """The class and the one line that explains it, decided once."""
    rev = matches(finding, REVISION_PHRASES)
    dec = matches(finding, DECISION_PHRASES)
    provenance = [p for p in rev if p in PROVENANCE_PHRASES]
    if rev and dec:
        if provenance:
            return REVISION, (
                f"revision — provenance {_show(provenance[0])} outranks "
                f"{_show(dec[0])}: the card must re-state the answer through "
                "the signed channel"
            )
        return None, (
            f"uncertain — matches both {_show(rev[0])} and {_show(dec[0])}; "
            "goes to the CEO as a decision"
        )
    if rev:
        return REVISION, f"revision — the card must say it: {_show(rev[0])}"
    if dec:
        return DECISION, f"decision — a call nobody has made: {_show(dec[0])}"
    return None, "uncertain — matches neither list; goes to the CEO as a decision"


def classify(finding: str) -> str | None:
    """REVISION, DECISION, or None when the vocabulary cannot place it."""
    return _read(finding)[0]


def route_class(finding: str) -> str:
    """`classify()` with None collapsed to UNCERTAIN_GOES_TO."""
    cls = classify(finding)
    return UNCERTAIN_GOES_TO if cls is None else cls


def why(finding: str) -> str:
    """One line: which phrase decided, or "uncertain — …"."""
    return _read(finding)[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("classify", help="classify one finding per stdin line")
    parser.parse_args(argv)
    for raw in sys.stdin:
        finding = raw.rstrip("\r\n")
        if not finding.strip():
            continue
        cls = classify(finding)
        print(f"{cls or 'uncertain'}\t{why(finding)}\t{finding}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
