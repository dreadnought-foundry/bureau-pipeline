#!/usr/bin/env python3
"""The split ledger, derived (DRE-3077 — piece 1 of 3 of DRE-3022).

Every card that did not fit one run left a record, and until now nobody read
it. A turn-cap death posts its own receipt on the card with the cap it hit and
the dollars it burned; a split leaves a Canceled or Backlog card whose
successors cite it; a hand-back leaves the agent's own note saying "this is an
epic's worth". Three receipts, all machine-readable, all already on the board.

`derive` reads them into `config/split-ledger.json` and renders
`docs/split-ledger.md` from it. That is the whole card: the ledger, and nothing
that consumes it. Injecting it into the planner is DRE-3078 and the plan-critic
check plus the scorer row is DRE-3079 — this module owes them nothing but a
file.

## What a row says

The card, its declared `size:` label, its role label, the file footprint it
DECLARED (the `**Files:**` line the planner brief makes the input to the
ordering), the footprint its split pieces ACTUALLY touched, how many pieces
there were, how many turn-cap deaths and how many dollars they cost, and which
of DRE-2893's four tells applied in hindsight.

## UNKNOWN, never 0

The rule the whole module is built around, and the one `planner_score` already
follows for the same reason: a read that fails records `UNKNOWN`, never `0` and
never `[]`. "GitHub would not say" and "the pull request touched nothing" are
different facts, and collapsing them turns an unread ledger into a clean one.
`gh pr list --repo <invisible>` exits 0 and prints `[]`, so the repo is probed
before any PR search is believed — the same guard, for the same measured
reason.

One field going UNKNOWN does not poison its row: a card whose successor search
failed still reports the deaths its own comments carry. Every unknown says why,
in the row's `unreadable` list.

## The tells are a pure function over a body

`tells(body)` takes a card body and returns which of the four applied. No
Linear, no GitHub, no clock — so a fixture test can pin each one, which is what
tests/test_split_ledger.py does. They are deterministic READINGS of the text,
not judgements: each under-reports rather than guessing, because a tell that
fires on every card is a label rather than a measurement.

## The population discovers itself (DRE-3356)

`derive` used to read the ten cards DRE-3077 named and nothing else, so the
ledger's history was whatever a person had remembered to type. `discover()`
asks the board instead, three ways, each one a receipt the pipeline already
writes: a comment carrying the turn-cap tag or the hold receipt, a comment
opening with the hand-back receipt, and a description citing the card it was
cut from — the origin taken from the successor's own words and kept only when
`cites()` agrees, which is the same reading the successor search already uses.

Every search is bounded by `--window-days` on `createdAt` (90 by default), so
the Linear spend is bounded with it (`check_linear_budget.py`). The seeds stay
in the population whatever the window says: they are in the ledger because
DRE-3077 named them, and a narrow window does not un-name them. A search that
could not be read is NAMED in the ledger's `source` sentence — a discovery
that quietly loses a search reports a smaller history in exactly the same
shape, which is the silent zero this module exists to refuse.

## Dated rows and a monthly count

Every row carries `created_at` — the card's Linear `createdAt` as ISO-8601 UTC,
or `UNKNOWN`. That is what lets a reader show "the last N splits" in order.
`monthly` then counts, per calendar month the window touches, how many cards
the planner gave a parent, how many ledger rows created that month were split,
and how many died at the turn cap. `complete` says whether the record covers
the whole month; an incomplete month is a PARTIAL count, not a low one.

CLI:

    python3 scripts/split_ledger.py derive [--card DRE-N ...] [--from J]
                                           [--window-days N] [--no-discover]
    python3 scripts/split_ledger.py collect [--card DRE-N ...] [--window-days N]
    python3 scripts/split_ledger.py discover [--window-days N]
    python3 scripts/split_ledger.py tells --body-file F
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_agent_result  # noqa: E402
import dead_run  # noqa: E402
import planner_score  # noqa: E402
import validate_card  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
LEDGER_PATH = os.path.join(ROOT, "config", "split-ledger.json")
DOC_PATH = os.path.join(ROOT, "docs", "split-ledger.md")

#: The literal every unreadable field carries. Never `0`, never `[]`.
UNKNOWN = "UNKNOWN"

#: How long the derive looks back, in days, when nobody says otherwise
#: (DRE-3356). It bounds every discovery search and every monthly count, which
#: is what keeps a full derive inside a few hundred Linear calls.
DEFAULT_WINDOW_DAYS = 90

#: The one timestamp format this module writes — `generated_at`, the window
#: bounds and every row's `created_at`. One format so the bounds can be
#: compared as strings, which is the whole reason the months are cheap.
ISO = "%Y-%m-%dT%H:%M:%SZ"

#: The ten cards DRE-3077 names as seed rows — the medic's history on DRE-2812
#: names them all. The population is these plus whatever the successor search
#: turns up; `derive --card` adds more.
SEED_CARDS = (
    "DRE-3029",
    "DRE-3016",
    "DRE-3022",
    "DRE-2719",
    "DRE-2838",
    "DRE-2847",
    "DRE-2871",
    "DRE-2937",
    "DRE-2891",
    "DRE-2676",
)

# --------------------------------------------------------------------------- #
# the receipts — the pipeline's own words, never a second spelling             #
# --------------------------------------------------------------------------- #

#: `dead_run.TURN_TAG` — the requeue receipt after a turn-cap death.
TURN_TAG = dead_run.TURN_TAG

#: `dead_run.decide`'s hold branch — the second death, which parks the card.
#: Built from the tag rather than typed out, so a rename cannot leave this
#: module matching a string nobody writes any more.
TURN_HOLD_MARK = f"held-for-human ({TURN_TAG} cap reached)"

#: `agent-task.yml` — the agent found an epic inside a one-off card. Shared
#: with `planner_score`, which reads the same receipt for a different question.
HANDBACK_RECEIPT_PREFIX = planner_score.HANDBACK_RECEIPT_PREFIX

#: The run record's own turn-cap signature (`error_max_turns` and its
#: siblings), read through the module that owns the vocabulary.
_is_turn_exhaustion = check_agent_result.is_turn_exhaustion

#: The cost clause `check_agent_result.turn_exhaustion_facts` writes into every
#: turn-cap receipt: "the 150-turn cap after 151 turns and $20.10". Anchored on
#: the turns-and-dollars shape rather than on a bare `$`, so a dollar figure
#: quoted elsewhere in a comment is not read as this run's spend.
_RECEIPT_COST = re.compile(r"turns?\s+and\s+\$\s*([0-9]+(?:\.[0-9]+)?)")

#: The states a split leaves the original in (standards/card-quality.md: "Cancel
#: the original, never Done"). Backlog is the second one because that is where
#: the turn-cap hold parks a card that a human then splits.
SPLIT_STATE_TYPES = ("canceled", "backlog")

#: How a successor cites the card it was cut from, with where each spelling was
#: read from. The third is the one the board actually writes most: the DRE-2719,
#: DRE-2847 and DRE-2871 splits all open "One of three cards splitting DRE-N",
#: and a reader that knew only "split from" found ZERO pieces for five cards
#: that were demonstrably split.
_CITATIONS = (
    (r"split\s+(?:out\s+)?(?:of|from)\b[^\n]{{0,80}}?\b{card}\b",
     "DRE-3077 — \"**Split from** [DRE-3022]\""),
    (r"piece\s+\d+\s+of\s+\d+\s+of\b[^\n]{{0,40}}?\b{card}\b",
     "DRE-3077 — \"piece 1 of 3 of DRE-3022\""),
    (r"splitting\b[^\n]{{0,40}}?\b{card}\b",
     "DRE-2910/2911/2912 — \"One of three splitting DRE-2871\""),
    (r"\bhalf\s+of\b[^\n]{{0,40}}?\b{card}\b",
     "DRE-2952/2953 — \"Backend half of [DRE-2937]\""),
)

#: The literal needles the DISCOVERY search hands Linear, one per spelling
#: `_CITATIONS` above knows (DRE-3356). `containsIgnoreCase` takes a literal,
#: not the regex — so each needle is the fixed part of a phrase and `cites()`
#: is what decides afterwards, exactly as it already decides the successor
#: search. `"piece "` carries its trailing space on purpose: without it the
#: needle also matches every card that writes "pieces", which is most of this
#: repo's vocabulary and none of its citations.
#:
#: tests/test_split_ledger.py holds the two halves to each other — every real
#: successor opening must be reachable by a needle AND accepted by `cites()`. A
#: phrase no needle reaches is a card the discovery cannot see at all.
CITATION_NEEDLES = (
    "split from",
    "split of",
    "split out of",
    "splitting",
    "piece ",
    "half of",
)

# Why a card is in the ledger at all. A card can carry more than one.
REASON_TURN_CAP = "turn-cap-death"
REASON_SPLIT = "split"
REASON_HANDBACK = "handed-back"
#: A seed row that history does not (yet) answer any of the three ways — it is
#: in the ledger because DRE-3077 named it. Recorded rather than left blank: a
#: row with no reason at all reads as a bug in the reader.
REASON_SEED = "named-as-a-seed"
#: The reasons that record the card DYING rather than merely being NAMED —
#: every reason except the seed. Defined once, here, because the readers
#: (`plan_critic.ledger_death_rows`, `planner_score.split_ledger_cards`) select
#: rows on it: a hand-copied tuple in each of them would go on matching the OLD
#: strings if a reason is ever renamed or added, and report fewer rows with no
#: error and no log line (DRE-3079 review).
DEATH_REASONS = (REASON_TURN_CAP, REASON_SPLIT, REASON_HANDBACK)
REASONS = DEATH_REASONS + (REASON_SEED,)

#: The receipts DISCOVERY searches comments for, each with the reason it
#: records (DRE-3356). Built from the constants above rather than typed out, so
#: a reword of a receipt changes the search and the reader together.
#:
#: `TURN_HOLD_MARK` embeds `TURN_TAG`, so the second search is a subset of the
#: first today. It is asked anyway, for one Linear call: the two strings are
#: written by different branches of `dead_run.decide` and nothing stops one of
#: them being reworded out of the other's shape.
COMMENT_NEEDLES = (
    (TURN_TAG, REASON_TURN_CAP),
    (TURN_HOLD_MARK, REASON_TURN_CAP),
    (HANDBACK_RECEIPT_PREFIX, REASON_HANDBACK),
)

# --------------------------------------------------------------------------- #
# DRE-2893's four tells, as a deterministic read of a card body                #
# --------------------------------------------------------------------------- #

TELL_CONTRACT = "contracts-between-pieces"
TELL_TIERS = "two-languages-or-tiers"
TELL_UNENUMERATED = "unenumerated-count"
TELL_UNBOUNDED = "unbounded-quantifier"
TELLS = (TELL_CONTRACT, TELL_TIERS, TELL_UNENUMERATED, TELL_UNBOUNDED)

#: What each tell means, for the rendered document. Verbatim from the standard.
TELL_QUESTIONS = {
    TELL_CONTRACT: "Does one deliverable read what another writes? The "
                   "strongest tell — if B reads what A writes it is not one "
                   "card.",
    TELL_TIERS: "Does the declared footprint span two languages or two tiers? "
                "Bounded is not the same as small.",
    TELL_UNENUMERATED: "Does a criterion count something the body never "
                       "enumerates? DRE-2837 said \"the nine derivations\" and "
                       "the nine were named nowhere.",
    TELL_UNBOUNDED: "Does the card quantify without a bound — \"every "
                    "surface\", \"all call sites\"? DRE-2838's was 57 mount "
                    "sites.",
}

#: The phrases a contract between pieces is actually written with, each named
#: with where it was read from. Following `routing_verdict`'s rule: match the
#: phrases real cards write, not the phrases a model imagines they write.
_CONTRACT_PHRASES = (
    ("contract between", "DRE-3077 — \"a contract between them\""),
    ("contracts between", "DRE-2719's hand-back — \"contracts between them\""),
    ("a contract the others read", "standards/card-quality.md"),
    ("reads what", "standards/card-quality.md — \"B reads what A writes\""),
    ("injection into", "DRE-3022 — \"its injection into the planner\""),
    ("consumed by", "DRE-2676 — \"the record the gates are consumed by\""),
)

#: Extension → tier. Only tiers a card can be TOO BIG across are listed: a
#: markdown doc, a JSON config or a fixture is not a second language, it is the
#: same work written down, and counting it would fire this tell on every card
#: in the repo.
_TIERS = {
    "py": "python",
    "ts": "web",
    "tsx": "web",
    "js": "web",
    "jsx": "web",
    "css": "web",
    "yml": "workflow",
    "yaml": "workflow",
    "sql": "database",
    "tf": "infra",
}

#: A path token in a card body. Requires a real extension, so "e.g." and "2.0"
#: are not read as files. The optional leading dot is `.github/workflows/*.yml`,
#: which is a third of this repo's footprints.
_PATH_TOKEN = re.compile(
    r"(?<![\w.])\.?[\w][\w./@-]*\.(?:"
    + "|".join(sorted(_TIERS)) + r"|md|json|txt)\b"
)

#: How prose spells a small count. Written out because a card says "the nine
#: derivations" far more often than it says "the 9 derivations".
_NUMBER_WORDS = {
    "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "twenty": 20,
}

_COUNT_PHRASE = re.compile(
    r"\b(\d{1,3}|" + "|".join(_NUMBER_WORDS) + r")\s+([a-z][a-z-]{2,}s)\b",
    re.IGNORECASE,
)

_QUANTIFIER = re.compile(
    r"\b(every|all|each)\s+(?:of\s+the\s+|the\s+)?([a-z][a-z-]{2,})\b",
    re.IGNORECASE,
)

#: Nouns a count is never a deliverable count of. All four are what a card
#: writes when it QUOTES a receipt — "151 turns and $20.10", "six dead runs",
#: "three hours" — which is history, not a criterion.
_NOT_DELIVERABLES = frozenset({
    "turns", "minutes", "hours", "days", "weeks", "months", "seconds",
    "times", "dollars", "cents", "runs", "attempts", "rounds",
})

_LIST_ITEM = re.compile(r"^[ \t]*(?:[-*+]\s+|\d+\.\s+)", re.MULTILINE)
_CARD_REF = re.compile(r"\b[A-Z]{2,}-\d+\b")
_ACCEPTANCE = re.compile(r"^##+\s*Acceptance criteria\s*$", re.MULTILINE | re.I)
_NEXT_HEADING = re.compile(r"^##+\s", re.MULTILINE)
_FOOTPRINT_HEADING = re.compile(
    r"^##+\s*(?:File footprint|Files)\s*$", re.MULTILINE | re.IGNORECASE)


class LedgerError(RuntimeError):
    """The ledger file is malformed. Raised rather than defaulted — a ledger
    that silently loses its rows reports a smaller history in the same shape."""


# --------------------------------------------------------------------------- #
# the clock — one format, so the window can be compared as strings             #
# --------------------------------------------------------------------------- #


def _moment(value):
    """Any ISO-8601 instant Linear or this file writes, as an aware UTC
    datetime — or None when it said nothing readable."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = _dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed.astimezone(_dt.timezone.utc)


def now_iso() -> str:
    """This instant, in the one format."""
    return _dt.datetime.now(_dt.timezone.utc).strftime(ISO)


def created_at_of(value) -> str:
    """Linear's `createdAt` as ISO-8601 UTC, or UNKNOWN.

    Never `""`. An empty string is a value a reader sorting "the last N splits"
    silently sorts FIRST, which is the same class of lie as an unread footprint
    printed as clean (DRE-3358 sorts the ledger's rows on exactly this field).
    """
    parsed = _moment(value)
    return parsed.strftime(ISO) if parsed else UNKNOWN


def window_start(window_days, now=None) -> str:
    """The instant `window_days` before `now` — the `createdAt` floor every
    discovery search and every monthly count is bounded by."""
    parsed = _moment(now) or _dt.datetime.now(_dt.timezone.utc)
    return (parsed - _dt.timedelta(days=int(window_days))).strftime(ISO)


# --------------------------------------------------------------------------- #
# the tells                                                                    #
# --------------------------------------------------------------------------- #


def _counting_scope(body: str) -> str:
    """The headline and the acceptance criteria — where a COUNT is a claim.

    The standard's own framing: "A criterion counting something never
    enumerated", and DRE-2837's tell was in its HEADLINE. A number in the
    middle of a card's history section is a fact about the past, and reading it
    as a criterion is how a check ends up firing on every card that quotes its
    own receipts.
    """
    body = body or ""
    head = body.split("\n\n", 1)[0]
    match = _ACCEPTANCE.search(body)
    if not match:
        return head
    rest = body[match.end():]
    stop = _NEXT_HEADING.search(rest)
    return head + "\n" + (rest[: stop.start()] if stop else rest)


def _enumerated_items(body: str) -> int:
    """How many things the body actually names.

    Three ways a card enumerates, and the largest wins: markdown list items,
    distinct card references, distinct path tokens. DRE-2871 named its eight
    sites with a file each and was countable — this is the reading that says so.
    """
    return max(
        len(_LIST_ITEM.findall(body or "")),
        len(set(_CARD_REF.findall(body or ""))),
        len(_paths(body)),
    )


def _paths(body: str) -> list:
    """Every path-shaped token in the body, lowercased and de-duplicated in
    order of appearance."""
    out: list[str] = []
    for match in _PATH_TOKEN.finditer(body or ""):
        token = match.group(0).lower().rstrip(".")
        if token not in out:
            out.append(token)
    return out


def _number(token: str) -> int | None:
    token = token.lower()
    if token.isdigit():
        return int(token)
    return _NUMBER_WORDS.get(token)


def _tiers(body: str) -> list:
    """The distinct tiers the body's paths live in, in a stable order."""
    seen: list[str] = []
    for path in _paths(body):
        tier = _TIERS.get(path.rsplit(".", 1)[-1])
        if tier and tier not in seen:
            seen.append(tier)
    return seen


def _contract_evidence(body: str) -> str | None:
    lowered = (body or "").lower()
    for phrase, source in _CONTRACT_PHRASES:
        if phrase in lowered:
            return f"{phrase!r} ({source})"
    return None


def _unenumerated_evidence(body: str) -> str | None:
    named = _enumerated_items(body)
    for match in _COUNT_PHRASE.finditer(_counting_scope(body)):
        count = _number(match.group(1))
        noun = match.group(2).lower()
        if count is None or count < 3 or noun in _NOT_DELIVERABLES:
            continue
        if count > named:
            return f"{match.group(0)!r} — the body names {named}"
    return None


def _unbounded_evidence(body: str) -> str | None:
    scope = _counting_scope(body)
    for match in _QUANTIFIER.finditer(scope):
        noun = match.group(2).lower()
        if noun in _NOT_DELIVERABLES or _number(noun) is not None:
            continue
        stem = noun[:-1] if noun.endswith("s") else noun
        # Bounded when the card states the number: "all five call sites", or a
        # count of the same noun anywhere in the body.
        following = scope[match.end():match.end() + 40].split()
        if following and _number(following[0].strip(".,;")) is not None:
            continue
        counted = re.search(
            r"\b(\d{1,4}|" + "|".join(_NUMBER_WORDS) + r")\s+"
            + re.escape(stem) + r"s?\b",
            body or "", re.IGNORECASE,
        )
        if counted:
            continue
        return f"{match.group(0)!r} with no count of {stem}s anywhere"
    return None


def tells(body: str) -> list:
    """Which of DRE-2893's four tells this card body carries, in TELLS order.

    Pure and deterministic — the body is the only input. Each reading
    under-reports on purpose (a phrase list rather than a judgement, a footprint
    rather than a guess at "tiers"), because the ledger is evidence and a check
    that fires on everything is not evidence of anything.
    """
    return [name for name, evidence in tell_evidence(body).items() if evidence]


def tell_evidence(body: str) -> dict:
    """Every tell mapped to the text that fired it, or None. The rendered
    document prints this so a reader can disagree with a row."""
    tiers = _tiers(body)
    return {
        TELL_CONTRACT: _contract_evidence(body),
        TELL_TIERS: (f"the footprint spans {', '.join(tiers)}"
                     if len(tiers) > 1 else None),
        TELL_UNENUMERATED: _unenumerated_evidence(body),
        TELL_UNBOUNDED: _unbounded_evidence(body),
    }


# --------------------------------------------------------------------------- #
# what the card declared                                                       #
# --------------------------------------------------------------------------- #


def _footprint_section(body: str) -> list:
    """The paths under a `## File footprint` / `## Files` heading, in order.

    Only inside that section: a path named in the prose above it is context,
    not a declaration, and reading the whole body would let a card that
    mentions a neighbour's file claim it.
    """
    match = _FOOTPRINT_HEADING.search(body or "")
    if not match:
        return []
    rest = (body or "")[match.end():]
    stop = _NEXT_HEADING.search(rest)
    return _paths(rest[: stop.start()] if stop else rest)


def _label_value(labels, prefix: str) -> str:
    for label in labels or ():
        if label.startswith(prefix):
            return label[len(prefix):]
    return UNKNOWN


def size_of(labels) -> str:
    """The `size:` label the card declares, or UNKNOWN.

    `briefs/planner.md`: "`size:XS` through `size:XL` already exist and mean
    EFFORT". A card carrying none declared no size, which is a read that found
    nothing — not a size of zero.
    """
    return _label_value(labels, "size:")


def role_of(labels) -> str:
    """The `agent:` role label, or UNKNOWN."""
    return _label_value(labels, "agent:")


def declared_files(body: str):
    """The footprint the card declared, or UNKNOWN when it declared none.

    Two spellings, because the board carries both. The `**Files:**` line is read
    through `planner_score.declared_files` rather than written again here — the
    anchoring rule (start of a line, optionally bold, optionally a list item) is
    the same rule and must not drift. The `## File footprint` SECTION is the
    other one, and it is what most of the seed cards actually wrote: DRE-3077's
    own body declares its four files under that heading and nowhere else, so a
    reader that knew only the line would have called ten well-scoped cards
    footprint-less and put every one of them in the `UNKNOWN` pile.
    """
    files = planner_score.declared_files(body or "")
    return files or _footprint_section(body) or UNKNOWN


# --------------------------------------------------------------------------- #
# what history says                                                            #
# --------------------------------------------------------------------------- #


def _is_turn_cap_receipt(body: str) -> bool:
    """Anchored at the START of the comment, the rule every receipt reader in
    this repo follows: a comment QUOTING a receipt is not one."""
    first = (body or "").lstrip()
    return first.startswith(f"🪦 {TURN_TAG}") or first.startswith(
        f"🚨 {TURN_HOLD_MARK}")


def turn_cap_deaths(comment_bodies, executions=()) -> list:
    """Every run that died at the turn cap, with what it spent.

    Three signals, all named by the card: the requeue receipt, the hold receipt
    the second death posts, and `error_max_turns` in a run's own execution
    record. Each row carries `dollars` or None — None being "the receipt
    carried no figure", which `dollars_spent` refuses to add up.
    """
    deaths: list[dict] = []
    for body in comment_bodies or ():
        if not _is_turn_cap_receipt(body):
            continue
        match = _RECEIPT_COST.search(body)
        deaths.append({
            "source": "receipt",
            "dollars": float(match.group(1)) if match else None,
        })
    for execution in executions or ():
        if not _is_turn_exhaustion(execution):
            continue
        cost = (execution or {}).get("total_cost_usd")
        deaths.append({
            "source": "run",
            "dollars": float(cost) if isinstance(cost, (int, float))
            and not isinstance(cost, bool) else None,
        })
    return deaths


def dollars_spent(deaths):
    """What the dead runs cost, or UNKNOWN.

    UNKNOWN as soon as ONE death carries no figure. A partial sum printed as a
    total is the same silent zero as an unread footprint printed as clean — the
    reader has no way to see that half the runs were left out.
    """
    if not deaths:
        return 0.0
    if any(death["dollars"] is None for death in deaths):
        return UNKNOWN
    return round(sum(death["dollars"] for death in deaths), 2)


def handed_back(comment_bodies) -> bool:
    """Did a build run hand this card back to Planning as an epic?"""
    return any((body or "").lstrip().startswith(HANDBACK_RECEIPT_PREFIX)
               for body in comment_bodies or ())


def cites(body: str, identifier: str) -> bool:
    """Does this body cite `identifier` as the card it was cut from?

    Read in the OPENING PARAGRAPH only, which is where a successor declares its
    parentage — every real one on the board does. The anchor is what keeps this
    a citation rather than a mention: DRE-2719 is named in the body of 39 cards
    and cut into six, and the difference between those two numbers is entirely
    the anchor. It under-reports rather than guesses, the same direction every
    other reading here leans.
    """
    head = (body or "").split("\n\n", 1)[0]
    card = re.escape(identifier)
    return any(re.search(template.format(card=card), head, re.IGNORECASE)
               for template, _ in _CITATIONS)


def cited_origins(body: str) -> list:
    """Every card this body cites as the one it was cut from (DRE-3356).

    The other direction of `cites`: the successor search asks "does this body
    cite THAT card", and discovery has no card to ask about — it has a page of
    successors and needs the origin each one names. So the candidates are the
    card references in the opening paragraph, and `cites()` is what keeps them:
    the reading that decides membership is the same one either way round, which
    is what stops the discovered population and the successor search disagreeing
    about what a citation is.
    """
    head = (body or "").split("\n\n", 1)[0]
    return [identifier for identifier in dict.fromkeys(_CARD_REF.findall(head))
            if cites(body, identifier)]


def reasons(record: dict) -> list:
    """Why this card is in the ledger — one, two or all three."""
    out: list[str] = []
    comments = record.get("comments")
    if comments and turn_cap_deaths(comments):
        out.append(REASON_TURN_CAP)
    successors = record.get("successors")
    if (record.get("state_type") in SPLIT_STATE_TYPES) and successors:
        out.append(REASON_SPLIT)
    if comments and handed_back(comments):
        out.append(REASON_HANDBACK)
    return out


# --------------------------------------------------------------------------- #
# a row                                                                        #
# --------------------------------------------------------------------------- #


def _piece_files(successors, unreadable: list):
    """The files the split pieces actually touched, or UNKNOWN.

    The union of the merged PRs' files, in order of first appearance. UNKNOWN
    when there are no pieces, or when not one of them produced a PR this run
    could read — an empty union would read as "the pieces changed nothing".
    """
    if successors is None:
        return UNKNOWN
    files: list[str] = []
    read_any = False
    for successor in successors:
        note = successor.get("pr_unreadable")
        if note:
            unreadable.append(f"{successor['identifier']}: {note}")
            continue
        pr = successor.get("pr")
        if not pr or pr.get("files") is None:
            unreadable.append(
                f"{successor['identifier']}: no merged pull request this run "
                "could read")
            continue
        read_any = True
        for path in pr["files"]:
            if path not in files:
                files.append(path)
    return files if read_any else UNKNOWN


def row(record: dict) -> dict:
    """One ledger row for one card. Every field that could not be read is
    UNKNOWN and says why in `unreadable`."""
    unreadable: list[str] = []
    identifier = record.get("identifier") or UNKNOWN
    body = record.get("body") or ""

    comments = record.get("comments")
    if comments is None:
        deaths, dollars = UNKNOWN, UNKNOWN
        unreadable.append(
            record.get("comments_unreadable")
            or "this card's comments could not be read, so its dead runs are "
               "not countable")
    else:
        found = turn_cap_deaths(comments, record.get("executions") or ())
        deaths = len(found)
        dollars = dollars_spent(found)
        if dollars is UNKNOWN:
            unreadable.append(
                f"{sum(1 for d in found if d['dollars'] is None)} of {deaths} "
                "dead runs posted no cost figure, so the total is not a total")

    successors = record.get("successors")
    if successors is None:
        pieces = UNKNOWN
        unreadable.append(record.get("successors_unreadable")
                          or "the successor search could not be read")
    else:
        pieces = len(successors)

    declared = declared_files(body)
    if declared is UNKNOWN:
        unreadable.append("the card declares no `Files:` line, so it made no "
                          "footprint claim to compare against")

    created = created_at_of(record.get("created_at"))
    if created is UNKNOWN:
        unreadable.append("this card's creation date could not be read, so the "
                          "row belongs to no month and to no ordering")

    return {
        "card": identifier,
        "title": record.get("title") or "",
        "url": record.get("url") or "",
        "state": record.get("state") or UNKNOWN,
        "created_at": created,
        "reasons": (reasons(record)
                    or ([REASON_SEED] if identifier in SEED_CARDS else [])),
        "size": size_of(record.get("labels")),
        "role": role_of(record.get("labels")),
        "declared_files": declared,
        "declared_file_count": UNKNOWN if declared is UNKNOWN else len(declared),
        "piece_files": _piece_files(successors, unreadable),
        "pieces": pieces,
        "pieces_named": ([s["identifier"] for s in successors]
                         if successors is not None else UNKNOWN),
        "deaths": deaths,
        "dollars": dollars,
        "tells": tells(body),
        "tell_evidence": {k: v for k, v in tell_evidence(body).items() if v},
        "unreadable": unreadable,
    }


# --------------------------------------------------------------------------- #
# the rates                                                                    #
# --------------------------------------------------------------------------- #


def _died(row_: dict) -> bool:
    return isinstance(row_.get("deaths"), int) and row_["deaths"] > 0


def rates(rows: list) -> dict:
    """The summary the card asks for, in the words it asks for them in:
    *"cards declaring more than N files died X of Y times"*.

    Rows whose footprint could not be read are counted APART, never into a
    band — an unread card in the denominator is a card the rate is wrong about
    in a direction nobody can see.
    """
    readable = [r for r in rows if isinstance(r.get("declared_file_count"), int)]
    unreadable = len(rows) - len(readable)
    widest = max((r["declared_file_count"] for r in readable), default=0)

    bands = []
    for threshold in range(1, max(widest, 1)):
        population = [r for r in readable
                      if r["declared_file_count"] > threshold]
        died = [r for r in population if _died(r)]
        bands.append({
            "more_than": threshold,
            "of": len(population),
            "died": len(died),
            "cards": [r["card"] for r in died],
            "sentence": (
                f"cards declaring more than {threshold} file"
                f"{'' if threshold == 1 else 's'} died {len(died)} of "
                f"{len(population)} times"),
        })

    by_tell = []
    for tell in TELLS:
        population = [r for r in rows if tell in r.get("tells", ())]
        died = [r for r in population if _died(r)]
        by_tell.append({
            "tell": tell,
            "of": len(population),
            "died": len(died),
            "sentence": (f"cards carrying the {tell} tell died {len(died)} of "
                         f"{len(population)} times"),
        })

    spend = [r["dollars"] for r in rows if isinstance(r.get("dollars"), float)]
    partial = sum(1 for r in rows if r.get("dollars") is UNKNOWN)
    return {
        "cards": len(rows),
        "died": sum(1 for r in rows if _died(r)),
        "by_declared_files": bands,
        "by_tell": by_tell,
        "unreadable_footprint": unreadable,
        "dead_dollars": round(sum(spend), 2) if spend else UNKNOWN,
        "dead_dollars_unreadable": partial,
    }


# --------------------------------------------------------------------------- #
# the months (DRE-3356)                                                        #
# --------------------------------------------------------------------------- #


def _months_between(since: str, until: str) -> list:
    """Every `YYYY-MM` the window touches, first to last, inclusive."""
    start, end = _moment(since), _moment(until)
    if start is None or end is None or start > end:
        return []
    months, year, month = [], start.year, start.month
    while (year, month) <= (end.year, end.month):
        months.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


def month_windows(since: str, until: str) -> list:
    """One record per calendar month the window touches, CLAMPED to it.

    A month at either end of the window is only partly inside it, and the
    honest count for that month is the part the window covers — not the whole
    calendar month, which would report days the derive never looked at.
    `complete` is exactly "the clamp did nothing": the window covers the whole
    month and the month ended before the derive ran. A reader who sees a low
    number on an incomplete month is reading a PARTIAL count, and the flag is
    what tells them so.

    The bounds are strings in the one format, so every comparison downstream is
    a string comparison and costs nothing.
    """
    first, last = created_at_of(since), created_at_of(until)
    if UNKNOWN in (first, last):
        return []
    windows = []
    for month in _months_between(first, last):
        opens, closes = planner_score._month_window(month)
        windows.append({
            "month": month,
            "since": max(opens, first),
            "until": min(closes, last),
            "complete": opens >= first and closes <= last,
        })
    return windows


def monthly(rows: list, children=None, *, since: str,
            generated_at: str) -> list:
    """The by-month block: how many children the planner made, how many of the
    ledger's own rows from that month were split, and how many died.

    `planner_children` is `UNKNOWN` — never `0` — for a month whose count could
    not be read. `split` and `died` are counted off the ROWS, which are already
    the ledger's decision about what "did not fit one run" means, so there is
    no second definition here. A row whose `created_at` is UNKNOWN belongs to no
    month and says so in its own `unreadable` list rather than landing in one.
    """
    counts = children or {}
    block = []
    for window in month_windows(since, generated_at):
        dated = [r for r in rows or ()
                 if window["since"] <= str((r or {}).get("created_at") or "")
                 < window["until"]]
        count = counts.get(window["month"])
        block.append({
            "month": window["month"],
            "planner_children": (count if isinstance(count, int)
                                 and not isinstance(count, bool) else UNKNOWN),
            "split": sum(1 for r in dated
                         if REASON_SPLIT in (r.get("reasons") or ())),
            "died": sum(1 for r in dated
                        if REASON_TURN_CAP in (r.get("reasons") or ())),
            "complete": window["complete"],
        })
    return block


#: What the ledger is derived from, when nobody composes a fuller sentence.
DEFAULT_SOURCE = ("Linear card bodies, labels and comment receipts, plus the "
                  "merged pull requests of each card's split pieces")


def ledger(records: list, *, generated_at: str | None = None,
           source: str = "", window_days: int = DEFAULT_WINDOW_DAYS,
           children_by_month=None) -> dict:
    """The whole ledger: when it was derived, over what window, every row, the
    by-month counts and the rates."""
    rows = [row(record) for record in records]
    rows.sort(key=lambda r: r["card"])
    generated = generated_at or now_iso()
    return {
        "generated_at": generated,
        "window_days": int(window_days),
        "generated_by": "scripts/split_ledger.py derive",
        "source": source or DEFAULT_SOURCE,
        "seed_cards": list(SEED_CARDS),
        "rows": rows,
        "monthly": monthly(rows, children_by_month,
                           since=window_start(window_days, generated),
                           generated_at=generated),
        "rates": rates(rows),
    }


def load(path: str | None = None) -> dict:
    try:
        with open(path or LEDGER_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as e:
        raise LedgerError(f"cannot read the split ledger: {e}") from e


# --------------------------------------------------------------------------- #
# the document                                                                 #
# --------------------------------------------------------------------------- #


def _cell(value) -> str:
    if value is UNKNOWN or value == UNKNOWN:
        return f"`{UNKNOWN}`"
    if isinstance(value, list):
        return ", ".join(f"`{v}`" for v in value) if value else "—"
    if isinstance(value, float):
        return f"${value:.2f}"
    return str(value)


def _count_cell(value) -> str:
    """A footprint as a COUNT, for the summary table. The files themselves get
    their own section — DRE-2719's six pieces touched 30 files between them, and
    a table cell carrying all 30 stops being a table."""
    if value is UNKNOWN or value == UNKNOWN:
        return f"`{UNKNOWN}`"
    return f"{len(value)} file{'' if len(value) == 1 else 's'}"


def render_markdown(doc: dict) -> str:
    """`docs/split-ledger.md`, rendered from the JSON.

    A hand-written second copy of a table drifts, so there is not one — the same
    discipline `docs/routing-verdicts.md` and `docs/lane-contract.md` are held
    to, and tests/test_split_ledger.py fails the build when the committed file
    and this render disagree.
    """
    out: list[str] = []
    w = out.append
    w("# The split ledger")
    w("")
    w("<!-- GENERATED FILE — do not edit by hand. -->")
    w("<!-- Regenerate with `python3 scripts/split_ledger.py derive`. -->")
    w("")
    w(f"Generated **{doc['generated_at']}** from {doc['source']}.")
    w("")
    w("Every card here did not fit one run: it died at the turn cap, it was "
      "split, or a build run handed it back as an epic. The point of writing "
      "it down is DRE-3022's: the planner has been sizing cards against "
      "nothing.")
    w("")
    window = doc.get("window_days", UNKNOWN)
    w("**The population discovers itself.** Nobody names it: the derive asks "
      "Linear for every card whose own comments carry a turn-cap or hand-back "
      "receipt, and for every card a successor cites as the one it was cut "
      "from, created in the "
      + (f"**{window} days**" if isinstance(window, int) else f"`{UNKNOWN}`")
      + " before that timestamp. The seed cards stay in whatever the window "
        "says, because they are here for a different reason — DRE-3077 named "
        "them.")
    w("")
    w("**A read that failed says `UNKNOWN`, never 0 and never \"none\".** "
      "\"GitHub would not say\" and \"the pull request touched nothing\" are "
      "different facts, and a ledger that collapses them reports a history "
      "that never happened.")
    w("")

    rates_ = doc["rates"]
    w("## The rates")
    w("")
    w(f"{rates_['cards']} card(s) in the ledger, {rates_['died']} of which "
      f"died at the turn cap at least once. "
      + (f"They cost **${rates_['dead_dollars']:.2f}** in dead runs"
         if isinstance(rates_["dead_dollars"], float)
         else "Their cost is `UNKNOWN`")
      + (f" ({rates_['dead_dollars_unreadable']} card(s) carry no readable "
         "cost)." if rates_["dead_dollars_unreadable"] else "."))
    w("")
    if rates_["unreadable_footprint"]:
        w(f"{rates_['unreadable_footprint']} card(s) declared no footprint at "
          "all. They are counted apart from every band below, never into one — "
          "an unread card in a denominator is a rate nobody can check.")
        w("")
    w("| Declared footprint | Cards | Died | Rate |")
    w("| --- | --- | --- | --- |")
    for band in rates_["by_declared_files"]:
        rate = (f"{100 * band['died'] / band['of']:.0f}%"
                if band["of"] else f"`{UNKNOWN}`")
        w(f"| more than {band['more_than']} file"
          f"{'' if band['more_than'] == 1 else 's'} | {band['of']} | "
          f"{band['died']} | {rate} |")
    w("")
    for band in rates_["by_declared_files"]:
        w(f"- {band['sentence']}")
    w("")
    w("## By month")
    w("")
    w("One row per calendar month the window touches. **Planner-created "
      "children** is every card the planner gave a parent in that month — the "
      "denominator DRE-3022's split rate is measured against. **Split** and "
      "**died** are this ledger's own rows created in that month, so a card "
      "created before the window belongs to no row here.")
    w("")
    w("A month is **complete** when the window covers all of it and it ended "
      "before this file was generated. An incomplete month is a PARTIAL count, "
      "not a low one — and a count that could not be read says `UNKNOWN`, "
      "never 0.")
    w("")
    months = doc.get("monthly") or []
    if not months:
        w("*(no month fell inside the window)*")
        w("")
    else:
        w("| Month | Planner-created children | Split | Died at the turn cap | "
          "Complete |")
        w("| --- | --- | --- | --- | --- |")
        for record in months:
            w(f"| {record['month']} | {_cell(record['planner_children'])} | "
              f"{_cell(record['split'])} | {_cell(record['died'])} | "
              + ("yes" if record["complete"] else "no — a partial count")
              + " |")
        w("")
    w("## The tells, in hindsight")
    w("")
    w("DRE-2893's four tells, read back over each card's own body by "
      "`split_ledger.tells` — a deterministic reading of the text, not a "
      "judgement. Each one under-reports on purpose.")
    w("")
    w("| Tell | What it asks | Cards | Died |")
    w("| --- | --- | --- | --- |")
    for band in rates_["by_tell"]:
        w(f"| `{band['tell']}` | {TELL_QUESTIONS[band['tell']]} | "
          f"{band['of']} | {band['died']} |")
    w("")
    w("## The rows")
    w("")
    w("| Card | Created | Size | Role | Declared | Pieces touched | Pieces | "
      "Deaths | Cost | Tells | Why it is here |")
    w("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for row_ in doc["rows"]:
        w("| " + " | ".join([
            f"[{row_['card']}]({row_['url']})" if row_.get("url")
            else row_["card"],
            _cell(row_.get("created_at", UNKNOWN)),
            _cell(row_["size"]),
            _cell(row_["role"]),
            _count_cell(row_["declared_files"]),
            _count_cell(row_["piece_files"]),
            _cell(row_["pieces"]),
            _cell(row_["deaths"]),
            _cell(row_["dollars"]),
            _cell(row_["tells"]),
            _cell(row_["reasons"]),
        ]) + " |")
    w("")
    w("## The footprints")
    w("")
    w("What each card SAID it would touch, against what its pieces actually "
      "touched. The two columns above are the counts; these are the files, and "
      "they are the input DRE-3078 sizes against.")
    w("")
    for row_ in doc["rows"]:
        w(f"### {row_['card']}")
        w("")
        w(f"- declared: {_cell(row_['declared_files'])}")
        w(f"- pieces touched: {_cell(row_['piece_files'])}")
        w("")
    w("## What could not be read")
    w("")
    w("Named rather than counted, because the absence of evidence is not "
      "evidence that a card was well sized.")
    w("")
    unread = [r for r in doc["rows"] if r["unreadable"]]
    if not unread:
        w("*(nothing — every field of every row was read)*")
        w("")
    for row_ in unread:
        w(f"- **{row_['card']}** — " + "; ".join(row_["unreadable"]))
    w("")
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# collecting the history — the one live seam                                   #
# --------------------------------------------------------------------------- #

#: The fields `gh pr list` is asked for. `files` is the footprint the pieces
#: actually touched; `state` says whether the piece shipped.
PR_FIELDS = "number,url,headRefName,state,files"

_CARD_QUERY = """query($id: String!) {
  issue(id: $id) {
    identifier title url description createdAt
    state { name type }
    labels { nodes { name } }
  }
}"""

#: The three discovery searches (DRE-3356). Each selects the least it can: the
#: comment and children searches need only an identifier, and only the citation
#: search needs the description, because only it has a reading left to do.
_COMMENT_SEARCH_QUERY = """query($after: String, $filter: IssueFilter) {
  issues(first: 100, after: $after, filter: $filter) {
    pageInfo { hasNextPage endCursor }
    nodes { identifier }
  }
}"""

_CITATION_SEARCH_QUERY = """query($after: String, $filter: IssueFilter) {
  issues(first: 100, after: $after, filter: $filter) {
    pageInfo { hasNextPage endCursor }
    nodes { identifier description }
  }
}"""

#: A month's planner-created children. "Planner-created child" is operationally
#: "a card with a parent", the same definition `planner_score.collect_month`
#: uses — the planner's one writer files sub-issues and nothing else on this
#: board gives a card a parent.
_CHILD_COUNT_QUERY = _COMMENT_SEARCH_QUERY

_SUCCESSOR_QUERY = """query($needle: String!) {
  issues(filter: {description: {containsIgnoreCase: $needle}}, first: 50) {
    nodes {
      identifier title description
      state { name type }
      labels { nodes { name } }
    }
  }
}"""


def _labels(node: dict) -> list:
    return [label["name"] for label in ((node.get("labels") or {}).get("nodes") or [])]


def _paged(lops, query: str, variables: dict, connection: str = "issues") -> list:
    """Every node of a paginated Linear connection, followed to exhaustion.

    `linear_ops.gql_paged`'s walk, done against the injected `lops` seam so a
    fixture only has to answer `gql`. The lesson is the same one and it is not
    optional: Linear serves at most 100 nodes per page and says so ONLY in
    `pageInfo`, so a search that reads page one has a population decided by
    Linear's default ordering and nothing anywhere says so (DRE-2681).
    """
    nodes: list = []
    after: str | None = None
    seen: set = set()
    while True:
        page = ((lops.gql(query, {**(variables or {}), "after": after}) or {})
                .get(connection)) or {}
        nodes += page.get("nodes") or []
        info = page.get("pageInfo") or {}
        if not info.get("hasNextPage"):
            return nodes
        after = info.get("endCursor")
        if not after or after in seen:
            print(f"split_ledger: {connection} claims another page with cursor "
                  f"{after!r} — stopping at {len(nodes)} node(s)",
                  file=sys.stderr)
            return nodes
        seen.add(after)


def discover(*, window_days: int = DEFAULT_WINDOW_DAYS, lops=None, now=None,
             seeds=SEED_CARDS) -> dict:
    """The population, without anyone naming it (DRE-3356).

    Three searches over receipts the pipeline already writes, all bounded by
    `createdAt` inside the window:

      1. comments carrying the turn-cap tag or the hold receipt,
      2. comments opening with the hand-back receipt,
      3. descriptions carrying a citation phrase — and for each hit, the card
         the successor NAMES, kept only when `cites()` agrees.

    The seeds are added last and unconditionally: they are in the ledger
    because DRE-3077 named them, and a narrow window does not un-name them.

    Reads are SERIAL through the one `LINEAR_API_KEY`, the bound every reader
    in this repo takes. A search that raised is recorded in `unreadable` and
    the others still count — one refused search must not empty a population,
    but it must never be invisible either.
    """
    if lops is None:
        import linear_ops as lops                   # noqa: PLC0415 - live seam
    since = window_start(window_days, now)
    window = {"createdAt": {"gte": since}}
    found: dict = {}
    unreadable: list = []

    def _record(identifier: str, reason: str) -> None:
        if identifier and reason not in found.setdefault(identifier, []):
            found[identifier].append(reason)

    for needle, reason in COMMENT_NEEDLES:
        try:
            nodes = _paged(lops, _COMMENT_SEARCH_QUERY, {"filter": {
                **window, "comments": {"body": {"containsIgnoreCase": needle}}}})
        except Exception as e:                      # noqa: BLE001 - live seam
            unreadable.append(
                f"the search for comments carrying {needle!r} could not be "
                f"read, so any card it alone would have found is missing: {e}")
            continue
        for node in nodes:
            _record(node.get("identifier"), reason)

    for needle in CITATION_NEEDLES:
        try:
            nodes = _paged(lops, _CITATION_SEARCH_QUERY, {"filter": {
                **window, "description": {"containsIgnoreCase": needle}}})
        except Exception as e:                      # noqa: BLE001 - live seam
            unreadable.append(
                f"the search for descriptions citing {needle!r} could not be "
                f"read, so any origin it alone would have named is missing: {e}")
            continue
        for node in nodes:
            for origin in cited_origins(node.get("description") or ""):
                if origin != node.get("identifier"):
                    _record(origin, REASON_SPLIT)

    for seed in seeds or ():
        _record(seed, REASON_SEED)

    return {
        "window_days": int(window_days),
        "since": since,
        "cards": sorted(found),
        "found": found,
        "unreadable": unreadable,
    }


def child_counts(windows, lops=None, unreadable=None) -> dict:
    """Month → how many planner-created children were created inside it.

    A month whose read failed is absent from the map, never `0` — `monthly`
    turns the absence into `UNKNOWN`, and the reason lands in `unreadable`.
    """
    if lops is None:
        import linear_ops as lops                   # noqa: PLC0415 - live seam
    counts: dict = {}
    for window in windows or ():
        try:
            nodes = _paged(lops, _CHILD_COUNT_QUERY, {"filter": {
                "createdAt": {"gte": window["since"], "lt": window["until"]},
                "parent": {"null": False}}})
        except Exception as e:                      # noqa: BLE001 - live seam
            if unreadable is not None:
                unreadable.append(
                    f"{window['month']}: the planner-children count could not "
                    f"be read, so the month says UNKNOWN rather than 0: {e}")
            continue
        counts[window["month"]] = len(nodes)
    return counts


def _pr_for(identifier: str, labels, finder, readable, seen: dict):
    """The card's merged pull request, or (None, why it could not be read).

    The repo is probed before the search is believed: `gh pr list --repo
    <invisible>` exits 0 and prints `[]`, which is indistinguishable from a card
    that never produced a PR. Probed once per repo — the answer cannot change
    inside one run.
    """
    repo = planner_score._repo_for(labels)
    if repo is None:
        return None, ("the card names no repo this rail routes, so there is "
                      "nowhere to look for its pull request")
    if repo not in seen:
        seen[repo] = readable(repo)
    if not seen[repo]:
        return None, (f"this token cannot read {repo}, and an empty PR search "
                      "there is indistinguishable from a card that never "
                      "produced one")
    try:
        found = finder(identifier, repo=repo, fields=PR_FIELDS)
    except Exception as e:                          # noqa: BLE001 - see docstring
        return None, str(e)
    if not found:
        return None, None
    files = found.get("files")
    return {
        "number": found.get("number"),
        "merged": found.get("state") == "MERGED",
        "files": ([f.get("path") for f in files if f.get("path")]
                  if files is not None else None),
    }, None


def collect(identifiers, lops=None, finder=None, readable=None, *,
            window_days=None, generated_at=None) -> dict:
    """Every named card, its receipts, its successors and their pull requests.

    With `window_days` set it also reads the by-month children counts and
    stamps the payload with the instant and the window it was gathered over, so
    `derive --from` derives the same ledger offline that a live `derive` would
    have written.

    Reads are SERIAL through the one `LINEAR_API_KEY` — the same bound
    `planner_score.collect` and `critic_score.read_population` take, for the
    same measured reason: two processes contending for one credential killed a
    paid run 23 turns in.
    """
    if lops is None:
        import linear_ops as lops                   # noqa: PLC0415 - live seam
    if finder is None:
        import card_pr                              # noqa: PLC0415 - live seam

        finder = card_pr.find
    if readable is None:
        readable = planner_score.repo_is_readable
    seen_repos: dict = {}

    cards = []
    for identifier in identifiers:
        issue = (lops.gql(_CARD_QUERY, {"id": identifier}) or {}).get("issue") or {}
        labels = _labels(issue)
        try:
            comments = lops.comment_bodies(identifier)
            comments_unreadable = None
        except Exception as e:                      # noqa: BLE001 - live seam
            comments, comments_unreadable = None, (
                f"this card's comments could not be read: {e}")

        successors, successors_unreadable = None, None
        try:
            found = (lops.gql(_SUCCESSOR_QUERY, {"needle": identifier})
                     or {}).get("issues") or {}
            successors = []
            for node in found.get("nodes") or []:
                if node["identifier"] == identifier:
                    continue
                if not cites(node.get("description") or "", identifier):
                    continue
                pr, why = _pr_for(node["identifier"], _labels(node),
                                  finder, readable, seen_repos)
                successors.append({
                    "identifier": node["identifier"],
                    "title": node.get("title") or "",
                    "pr": pr,
                    "pr_unreadable": why,
                })
        except Exception as e:                      # noqa: BLE001 - live seam
            successors, successors_unreadable = None, (
                f"the successor search could not be read: {e}")

        cards.append({
            "identifier": issue.get("identifier") or identifier,
            "title": issue.get("title") or "",
            "url": issue.get("url") or "",
            "created_at": issue.get("createdAt") or "",
            "body": issue.get("description") or "",
            "labels": labels,
            "state": (issue.get("state") or {}).get("name") or UNKNOWN,
            "state_type": (issue.get("state") or {}).get("type") or "",
            "size": size_of(labels),
            "role": role_of(labels),
            "comments": comments,
            "comments_unreadable": comments_unreadable,
            "successors": successors,
            "successors_unreadable": successors_unreadable,
        })

    payload: dict = {"cards": cards}
    if window_days is not None:
        generated = generated_at or now_iso()
        notes: list = []
        payload.update({
            "generated_at": generated,
            "window_days": int(window_days),
            "children_by_month": child_counts(
                month_windows(window_start(window_days, generated), generated),
                lops=lops, unreadable=notes),
            "children_unreadable": notes,
        })
    return payload


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #


def derived_source(window_days, notes) -> str:
    """What this ledger was derived from, in one sentence — including the reads
    that failed.

    The failures go HERE because `source` is a field the ledger already
    carries: a discovery that loses a search reports a smaller history in the
    same shape, and the one place a reader is certain to look is the sentence
    saying where the rows came from.
    """
    sentence = (f"{DEFAULT_SOURCE}, over a population DISCOVERED from the "
                f"turn-cap, hand-back and split-citation receipts of the last "
                f"{window_days} days plus the seed cards")
    if notes:
        sentence += (f" — with {len(notes)} read(s) that could not be made and "
                     "are therefore in no count below: " + "; ".join(notes))
    return sentence


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command")

    gathering = sub.add_parser(
        "collect", help="read the cards, their receipts and their pieces, as JSON")
    derived = sub.add_parser(
        "derive", help="write config/split-ledger.json and docs/split-ledger.md")
    finding = sub.add_parser(
        "discover", help="print the population the board answers with, as JSON")
    for cmd in (gathering, derived):
        cmd.add_argument("--card", action="append", default=[],
                         help="a card to read (repeatable); ADDED to the "
                              "discovered population and the seeds")
    for cmd in (gathering, derived, finding):
        cmd.add_argument("--window-days", type=int, default=DEFAULT_WINDOW_DAYS,
                         help="how far back the discovery searches and the "
                              "monthly counts look (default "
                              f"{DEFAULT_WINDOW_DAYS})")
    for cmd in (gathering, derived):
        cmd.add_argument("--no-discover", action="store_true",
                         help="read only the seeds and any --card, the way "
                              "derive behaved before DRE-3356")
    derived.add_argument("--from", dest="source",
                         help="a collect JSON to derive from, instead of reading "
                              "Linear")
    derived.add_argument("--out", default=LEDGER_PATH)
    derived.add_argument("--doc", default=DOC_PATH)

    telling = sub.add_parser("tells")
    telling.add_argument("--body-file", required=True)

    args = parser.parse_args(argv)
    command = args.command or "derive"

    if command == "tells":
        with open(args.body_file, encoding="utf-8") as fh:
            body = fh.read()
        for name, evidence in tell_evidence(body).items():
            print(f"  [{'HIT ' if evidence else 'miss'}] {name}"
                  + (f" — {evidence}" if evidence else ""))
        print(f"{len(tells(body))} of {len(TELLS)} tells")
        return 0

    if command == "discover":
        print(json.dumps(discover(window_days=args.window_days), indent=2))
        return 0

    # The population: what the board answers with, plus the seeds, plus
    # whatever was named on the command line. `--card` ADDS since DRE-3356 —
    # it used to REPLACE the seeds, which is how a targeted run could quietly
    # rewrite the committed ledger down to one row.
    notes: list = []
    population = list(args.card)
    if not (args.no_discover or getattr(args, "source", None)):
        found = discover(window_days=args.window_days)
        population += found["cards"]
        notes += found["unreadable"]
    population += list(SEED_CARDS)
    cards = list(dict.fromkeys(population))

    if command == "collect":
        print(json.dumps(collect(cards, window_days=args.window_days), indent=2))
        return 0

    if command == "derive":
        if args.source:
            with open(args.source, encoding="utf-8") as fh:
                gathered = json.load(fh)
        else:
            gathered = collect(cards, window_days=args.window_days)
        notes += gathered.get("children_unreadable") or []
        window_days = gathered.get("window_days", args.window_days)
        doc = ledger(
            gathered["cards"],
            generated_at=gathered.get("generated_at"),
            window_days=window_days,
            children_by_month=gathered.get("children_by_month"),
            source=derived_source(window_days, notes),
        )
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2)
            fh.write("\n")
        with open(args.doc, "w", encoding="utf-8") as fh:
            fh.write(render_markdown(doc))
        print(f"wrote {args.out} ({len(doc['rows'])} rows, "
              f"{len(doc['monthly'])} month(s)) and {args.doc}")
        for note in notes:
            print(f"  unread: {note}", file=sys.stderr)
        return 0

    parser.print_usage(sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
