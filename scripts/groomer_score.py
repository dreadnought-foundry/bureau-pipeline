#!/usr/bin/env python3
"""Score the groomer's judgement against a hand audit it never saw (DRE-3155).

The planner has an audit (DRE-3016) and the critic had one before it (DRE-2685).
The groomer's judgement — the one ranked read that says `now`, `not-now`,
`likely-done` or nothing at all — has had none: the CEO reads a batch, approves
it or does not, and whether the read was any good is learned one cycle at a
time.

This is the same instrument, pointed at the groomer.

## The held-out answer is the CEO's own

DRE-3151 records it: a markdown table in a comment on that card, written BEFORE
he reads the model's reasons. The proposal the run posted was written first, so
neither side could be composed to match the other. This module never fetches
either — the audit thread arrives on stdin (`linear_ops.py dump-comments`) and
the proposal arrives as a file. There is no client here to import.

## The exclusion is the whole thing (DRE-2685's rule, one role over)

`constraints` — collisions, blockers, epic units and capacity — is **excluded
as contaminated and never scored**. `groomer.sequence` applies those constraints
AFTER the model's order and the groomer's own tests hold them there, so the
judgement cannot break one of them however it reads the board. Scoring the
dimension grades the code rather than the judgement.

That exclusion is CHECKED, not asserted: `reference_problems` reads
`scripts/groomer.py` for every constraint function the reference names. Take one
away and the dimension stops being contaminated, and this module says so instead
of quietly keeping a stale exclusion.

## A row nobody answered is UNKNOWN

A blank or `unsure` CEO column is the ABSENCE of an answer, not an agreement. It
is reported under its own heading and left out of the rate — never 0, never
"clean".

## `unranked` is counted and never scored

A refusal is the designed answer (`groom_judgement.WITHHELD_REASON`), not a
miss. Scoring it as a disagreement would push the model towards answering when
it should not, which is the one failure the whole rail exists to avoid.

## Both halves, empty or not

`render_report` always prints Agreement AND Disagreement. An audit that prints
only its hits is a marketing document.

## And the one number this exists for

A **false "done"** — the judgement calling a card likely-done that the CEO says
is live work — is the expensive miss: it leaves real work unscheduled and
nobody goes looking. It is counted on a line of its own rather than folded into
the rate. A dead row the card's OWN description condemned is not the
judgement's call and is not counted (`groomer._mark` never re-judges one).

CLI:

    python3 scripts/groomer_score.py check                     # the reference
    python3 scripts/groomer_score.py score --card DRE-3151 \\
        [--proposal proposal.json] [--out J] [--report M]  < thread.json
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
REFERENCE_PATH = os.path.join(ROOT, "config", "groomer-audit.json")
GROOMER_PATH = os.path.join(_HERE, "groomer.py")

#: The audit table's header, cell by cell. Matched EXACTLY rather than by
#: width: `Rules said` and `Judgement said` are one column apart, so a table
#: read in the wrong order scores the RULES against the CEO and reports the
#: number as the judgement's.
HEADER = ("Card", "Rules said", "Judgement said", "CEO said", "Agree?")

#: The same header as the line a person writes, for every refusal message. The
#: parser names what it expected rather than saying a header was wrong.
HEADER_LINE = "| " + " | ".join(HEADER) + " |"

#: The record keys the header's cells become, in the same order.
COLUMNS = ("card", "rules", "judgement", "ceo", "agree")

#: The column that makes a table THE HAND AUDIT. The proposal comment carries
#: tables of its own; only the CEO's column says a table is the held-out
#: answer, so the others are passed over rather than refused.
CEO_COLUMN = "CEO said"

#: What the three call columns may say. The reference declares the same
#: vocabulary and `reference_problems` pins the two together — a call this
#: module counts and the reference cannot hold is a row scored against nothing.
CALLS = ("now", "not-now", "likely-done", "unranked")

#: The judgement declining to rank a card. Counted, never scored.
UNRANKED = "unranked"

#: The judgement calling a card done. The half of it the CEO contradicts is the
#: false done this module exists to count.
LIKELY_DONE = "likely-done"

#: What the CEO writes when he has no answer. A blank cell says the same thing.
UNSURE = "unsure"

#: `groomer.DEAD_FROM_LINE` — the marker a dead row carries when the CARD'S OWN
#: description condemned it rather than the model. Spelled here rather than
#: imported, because this module imports no groomer and no client;
#: `tests/test_groomer_score.py` pins it to the groomer's own constant. A
#: marker that drifted would read as "the model made this call", which is
#: exactly the false done being counted.
DEAD_FROM_LINE = "superseded-line"

#: Every dimension this module can report on. `reference_problems` pins this
#: against the file in both directions: a dimension declared and never reported
#: is as wrong as a row for a dimension nobody declared.
DIMENSIONS = ("placement", "likely-done", "trigger", "unranked", "constraints")

#: The dimension the judgement is handed face-up by the code beneath it.
CONTAMINATED_DIMENSION = "constraints"

#: Every value each dimension's reader can produce, checked against the
#: declared vocabulary for DRE-2685's reason: a value the reference cannot hold
#: comes out as a disagreement neither side ever expressed.
EMITTED_VALUES = {
    "placement": ("now", "not-now", "likely-done"),
    "likely-done": ("confirmed", "false-done"),
    "trigger": ("event", "date", "unknown"),
    "unranked": ("unranked",),
}

#: What a row can come out as. `unranked` is its own outcome rather than a
#: disagreement, and `unknown` is its own rather than a zero.
OUTCOMES = ("agree", "disagree", "unknown", "unranked")


class AuditError(RuntimeError):
    """The audit thread, or the reference, is not the shape the contract says.

    Raised rather than defaulted: a table read against the wrong header still
    produces a number, in the same shape, off the wrong column.
    """


# --------------------------------------------------------------------------- #
# the reference                                                                #
# --------------------------------------------------------------------------- #

_CACHE: dict = {}


def load(path: str | None = None) -> dict:
    path = path or REFERENCE_PATH
    if path not in _CACHE:
        try:
            with open(path, encoding="utf-8") as fh:
                _CACHE[path] = json.load(fh)
        except (OSError, ValueError) as e:
            raise AuditError(f"cannot read the audit reference at {path}: {e}") from e
    return _CACHE[path]


def contract(doc: dict | None = None) -> dict:
    return dict((doc or load()).get("contract") or {})


def dimensions(doc: dict | None = None) -> dict:
    return dict((doc or load()).get("dimensions") or {})


def source(doc: dict | None = None) -> dict:
    return dict((doc or load()).get("source") or {})


def is_scored(dimension: str, doc: dict | None = None) -> bool:
    return bool(dimensions(doc).get(dimension, {}).get("scored"))


_GROOMER_CACHE: dict = {}


def groomer_defines(name: str, path: str | None = None) -> bool:
    """Does `scripts/groomer.py` still define `name`?

    The load-bearing half of the exclusion. `constraints` is excluded BECAUSE
    `groomer.sequence` and the functions it calls enforce it; take one of those
    away and the dimension stops being contaminated, and an exclusion nobody
    re-checked would keep a real result out of the number.

    Module-level definitions only. A nested helper is an implementation detail
    of the function around it and cannot be named as the thing that enforces a
    constraint.
    """
    path = path or GROOMER_PATH
    if path not in _GROOMER_CACHE:
        try:
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
        except (OSError, SyntaxError):
            return False
        _GROOMER_CACHE[path] = {
            node.name for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
    return name in _GROOMER_CACHE[path]


def reference_problems(doc: dict | None = None) -> list:
    """Everything wrong with the audit reference, or an empty list."""
    doc = doc if doc is not None else load()
    problems: list[str] = []

    declared_header = list(contract(doc).get("header") or ())
    if declared_header != list(HEADER):
        problems.append(
            f"the reference's contract header is {declared_header} and the "
            f"parser demands {HEADER_LINE} — one contract, written once, or a "
            "table is read against a header nobody else holds"
        )
    declared_calls = list(contract(doc).get("calls") or ())
    missing_calls = [call for call in CALLS if call not in declared_calls]
    if missing_calls:
        problems.append(
            f"the contract does not carry the call(s) {missing_calls}, which "
            "the parser reads off the table — a call the reference cannot hold "
            "is scored against nothing"
        )

    declared = dimensions(doc)
    for name in DIMENSIONS:
        if name not in declared:
            problems.append(
                f"this module reports on {name!r} and the reference does not "
                "declare it, so those rows would be scored against nothing"
            )
    for name in declared:
        if name not in DIMENSIONS:
            problems.append(
                f"dimension {name!r} is declared and nothing reports it — a "
                "dimension no reader answers is a promise, not an audit"
            )

    for name, block in declared.items():
        if not (block.get("question") or "").strip():
            problems.append(f"dimension {name!r} states no question")
        if not block.get("values"):
            problems.append(f"dimension {name!r} carries no values")
        unheld = sorted(set(EMITTED_VALUES.get(name, ()))
                        - set(block.get("values") or ()))
        if unheld:
            problems.append(
                f"dimension {name!r} can be answered {unheld}, which it does "
                "not carry — a value the reference cannot hold comes out as a "
                "disagreement neither side ever expressed"
            )
        if block.get("scored"):
            if not (block.get("why") or "").strip():
                problems.append(
                    f"dimension {name!r} is scored and does not say what the "
                    "audit answers it with"
                )
        elif not (block.get("not_scored_why") or "").strip():
            problems.append(
                f"dimension {name!r} is left out of the rate and does not say "
                "why — an unexplained exclusion is indistinguishable from a "
                "convenient one"
            )

    block = declared.get(CONTAMINATED_DIMENSION) or {}
    if block:
        if block.get("scored"):
            problems.append(
                f"{CONTAMINATED_DIMENSION!r} is scored — groomer.sequence "
                "applies the constraints after the model's order and the "
                "groomer's tests hold them there, so every row it produces was "
                "handed over face-up (DRE-2685)"
            )
        if not (block.get("contaminated") or "").strip():
            problems.append(
                f"dimension {CONTAMINATED_DIMENSION!r} is excluded as "
                "contaminated and does not say why"
            )
        enforces = list(block.get("enforces") or ())
        if not enforces:
            problems.append(
                f"dimension {CONTAMINATED_DIMENSION!r} names no function that "
                "enforces it, so the exclusion rests on nothing this can read"
            )
        for function in enforces:
            if not groomer_defines(function):
                problems.append(
                    f"the {CONTAMINATED_DIMENSION!r} exclusion says "
                    f"{function} enforces the constraints and "
                    f"scripts/groomer.py no longer defines {function} — the "
                    "dimension may have stopped being contaminated, and an "
                    "exclusion nobody re-checked keeps a real result out of "
                    "the number"
                )
    return problems


# --------------------------------------------------------------------------- #
# the parser — the exact header, and nothing else                              #
# --------------------------------------------------------------------------- #

_DIVIDER = re.compile(r"^:?-{2,}:?$")

#: `false-done: 3` / `- **unranked:** 0` — anchored at the start of its own
#: line, optionally bulleted, optionally bold. The same anchoring rule every
#: other marker in this pipeline follows: a sentence mentioning a count
#: declares nothing.
_COUNT_LINE = re.compile(
    r"^[ \t]*(?:[-*+][ \t]+)?\*{0,2}(?P<name>false-done|unranked)\*{0,2}"
    r"[ \t]*:[ \t]*(?P<count>\d+)[ \t]*$",
    re.MULTILINE | re.IGNORECASE,
)


def _cells(line: str):
    """A markdown table row's cells, or None when the line is not one."""
    stripped = (line or "").strip()
    if not stripped.startswith("|") or not stripped.endswith("|") \
            or len(stripped) < 2:
        return None
    return [cell.strip() for cell in stripped[1:-1].split("|")]


def _is_divider(cells) -> bool:
    return bool(cells) and all(_DIVIDER.match(cell or "") for cell in cells)


def _norm(value) -> str:
    """A cell as it is compared: no backticks, no emphasis, lower case."""
    return (value or "").strip().strip("`*_ ").strip().lower()


def parse_table(text: str) -> list:
    """Every markdown table in `text`, as `{header, rows}` records.

    Pure over the string. Nothing here reads a file or a client — the audit
    thread arrives on stdin and this is the whole of what turns it into records.
    """
    tables: list[dict] = []
    header = None
    rows: list[list[str]] = []
    for line in (text or "").splitlines():
        cells = _cells(line)
        if cells is None:
            if header is not None:
                tables.append({"header": header, "rows": rows})
                header, rows = None, []
            continue
        if header is None:
            header, rows = cells, []
            continue
        if _is_divider(cells):
            continue
        rows.append(cells)
    if header is not None:
        tables.append({"header": header, "rows": rows})
    return tables


def _declared_counts(body: str) -> dict:
    """The two count lines the audit comment carries, read off that comment.

    Absent is None, never 0: "the audit did not say" and "the audit said none"
    are different facts, and only one of them is a number to compare against.
    """
    declared = {"false-done": None, "unranked": None}
    for match in _COUNT_LINE.finditer(body or ""):
        declared[match.group("name").lower()] = int(match.group("count"))
    return declared


def parse_audit(comments) -> dict:
    """The hand audit's table, out of the card's comment thread.

    A table with no `CEO said` column is not the hand audit — the proposal
    comment carries tables of its own — so it is passed over. A table that DOES
    carry one and whose header is not the contract's is REFUSED, naming the
    header it expected: `Rules said` and `Judgement said` are one column apart,
    and a table read in the wrong order scores the rules against the CEO and
    reports the number as the judgement's.
    """
    for body in comments or ():
        for table in parse_table(body or ""):
            if CEO_COLUMN not in table["header"]:
                continue
            if list(table["header"]) != list(HEADER):
                raise AuditError(
                    "this comment's audit table opens `| "
                    + " | ".join(table["header"])
                    + f" |`, and the contract's header is `{HEADER_LINE}` — "
                    "refused rather than read, because the columns are one "
                    "apart and reading them in the wrong order scores the "
                    "rules against the CEO and reports the number as the "
                    "judgement's"
                )
            rows = []
            for cells in table["rows"]:
                if len(cells) != len(HEADER) or not (cells[0] or "").strip():
                    continue
                row = dict(zip(COLUMNS, [cell.strip() for cell in cells]))
                row["card"] = row["card"].strip("`*_ ").strip()
                rows.append(row)
            return {"rows": rows, "declared": _declared_counts(body or "")}
    raise AuditError(
        "no comment in this thread carries the hand audit's table — the "
        f"contract's header is `{HEADER_LINE}`, and nothing here opens with it"
    )


# --------------------------------------------------------------------------- #
# the trigger split — an event a person will notice, or a date nobody watches  #
# --------------------------------------------------------------------------- #

#: A date somebody would have to be watching the calendar to act on. A cycle
#: number is one of these: nothing pages when cycle 14 opens.
_DATE_PATTERNS = (
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    re.compile(r"\bcycle\s+\d+\b", re.IGNORECASE),
    re.compile(r"\b(?:jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2}\b",
               re.IGNORECASE),
    re.compile(r"\b(?:in|after)\s+\d+\s+(?:day|week|month)s?\b", re.IGNORECASE),
    re.compile(r"\bnext\s+(?:week|month|quarter|cycle)\b", re.IGNORECASE),
)


def trigger_kind(text) -> str:
    """`event` · `date` · `unknown` — how a not-now comes back.

    A trigger naming something that HAPPENS somewhere the pipeline already
    watches — a card landing, an epic closing, an operator pushing — is an
    event: somebody notices it. A date is not watched by anything, so a card
    parked behind one comes back only if a person remembers.

    A missing trigger is `unknown` and never an event. A not-now with no
    trigger comes back never, and reporting it as an event would hide exactly
    the rows worth going and looking at.
    """
    value = (text or "").strip()
    if not value:
        return "unknown"
    for pattern in _DATE_PATTERNS:
        if pattern.search(value):
            return "date"
    return "event"


# --------------------------------------------------------------------------- #
# the score                                                                    #
# --------------------------------------------------------------------------- #


def _proposal_rows(proposal) -> dict:
    """`{card identifier: the proposal's row}`, out of `groomer.propose`'s JSON.

    The `sequence` block first — every row carries its outcome there — falling
    back to the three outcome buckets for a proposal rendered without it.
    """
    rows: dict = {}
    if not proposal:
        return rows
    for record in proposal.get("sequence") or ():
        if record.get("identifier"):
            rows[record["identifier"]] = dict(record)
    for outcome, bucket in (proposal.get("outcomes") or {}).items():
        for record in bucket or ():
            identifier = record.get("identifier")
            if identifier and identifier not in rows:
                rows[identifier] = {**record, "outcome": outcome}
    return rows


def _is_the_models_call(record: dict | None) -> bool:
    """Was the dead row the JUDGEMENT's call?

    `groomer._mark`: a card whose description says it was superseded is never
    re-judged — the declaration a person wrote outranks the read. A dead row
    sourced from that line is the CARD's answer, so calling it a false done
    would book a miss against a model that was never asked.
    """
    if not record:
        return True
    if record.get("source") == DEAD_FROM_LINE:
        return False
    return record.get("judged", True) is not False


def score(parsed: dict, proposal: dict | None = None, *,
          card: str | None = None) -> dict:
    """Score one hand audit against the judgement that produced the batch.

    Every row comes out carrying exactly one outcome — agree, disagree, unknown
    or unranked — because a row that quietly fell out of the population is the
    same failure as a silent zero: a smaller set reported in the same shape.

    `proposal`, when given, is `groomer.propose`'s JSON. It is read for what the
    table cannot say: the trigger a not-now names, and whether a dead row was
    the model's call at all.
    """
    proposal_rows = _proposal_rows(proposal)
    declared = dict((parsed or {}).get("declared") or {})

    rows: list[dict] = []
    counts = {name: 0 for name in OUTCOMES}
    false_done_cards: list[str] = []
    unranked_cards: list[str] = []
    triggers = {"event": 0, "date": 0, "unknown": 0}
    contradictions = 0

    for record in (parsed or {}).get("rows") or ():
        identifier = record["card"]
        judgement, ceo = _norm(record.get("judgement")), _norm(record.get("ceo"))
        proposed = proposal_rows.get(identifier)

        if judgement == UNRANKED:
            outcome = UNRANKED
            why = ("the judgement declined to rank this card — a refusal is the "
                   "designed answer, so it is counted and never scored")
            unranked_cards.append(identifier)
        elif not judgement:
            outcome = "unknown"
            why = ("the judgement column is empty, so there is no call to "
                   "compare — reported rather than scored")
        elif ceo in ("", UNSURE):
            outcome = "unknown"
            why = ("the CEO column is blank or `unsure` — the absence of an "
                   "answer, not an agreement, so it is out of the rate")
        elif judgement == ceo:
            outcome = "agree"
            why = "the judgement and the CEO reached the same answer"
        else:
            outcome = "disagree"
            why = (f"the judgement said `{judgement}` and the CEO would have "
                   f"said `{ceo}`")

        # The `Agree?` column is the CEO's own arithmetic, and it is checked
        # rather than trusted: the outcome is derived from the two columns
        # beside it, so a transcription slip is REPORTED on the row it is on
        # instead of moving the number.
        stated = _norm(record.get("agree"))
        if outcome in ("agree", "disagree") and stated in ("yes", "no"):
            if (stated == "yes") != (outcome == "agree"):
                contradictions += 1
                why += (f" — the table's `Agree?` column says `{stated}`, which "
                        "contradicts the two columns beside it")

        false_done = (judgement == LIKELY_DONE and outcome == "disagree"
                      and _is_the_models_call(proposed))
        if false_done:
            false_done_cards.append(identifier)

        if judgement == "not-now":
            triggers[trigger_kind((proposed or {}).get("trigger"))] += 1

        counts[outcome] += 1
        rows.append({
            **record,
            "outcome": outcome,
            "why": why,
            "false_done": false_done,
            "trigger": (proposed or {}).get("trigger"),
        })

    scored = counts["agree"] + counts["disagree"]
    notes: list[str] = []
    for name, counted in (("false-done", len(false_done_cards)),
                          ("unranked", len(unranked_cards))):
        stated_count = declared.get(name)
        if stated_count is not None and stated_count != counted:
            notes.append(
                f"the audit declares `{name}: {stated_count}` and the table "
                f"carries {counted} — both are reported, and the difference is "
                "a row to go and read rather than a number to pick between"
            )
    if contradictions:
        notes.append(
            f"{contradictions} row(s) carry an `Agree?` column that contradicts "
            "the two columns beside it; the outcome is taken from the columns, "
            "and the row says so"
        )

    return {
        "card": card,
        "rows": rows,
        "counts": counts,
        "scored": scored,
        "rate": round(counts["agree"] / scored, 4) if scored else None,
        "false_done": {
            "counted": len(false_done_cards),
            "cards": false_done_cards,
            "declared": declared.get("false-done"),
        },
        "unranked": {
            "counted": len(unranked_cards),
            "cards": unranked_cards,
            "declared": declared.get("unranked"),
        },
        "triggers": triggers,
        "notes": notes,
        "judgement": dict((proposal or {}).get("judgement") or {}),
        "proposal": (proposal or {}).get("id"),
    }


# --------------------------------------------------------------------------- #
# the report                                                                   #
# --------------------------------------------------------------------------- #

SECTIONS = (
    ("## Agreement", "agree",
     "The judgement put the card where the CEO would have put it."),
    ("## Disagreement", "disagree",
     "The judgement said one thing and the CEO would have said another. Both "
     "halves are printed, empty or not; neither side is assumed right."),
    ("## Unknown", "unknown",
     "The CEO column is blank or `unsure`. Nobody answered these, so they are "
     "named and left out of the rate — the absence of an answer is not an "
     "agreement."),
    ("## Unranked", UNRANKED,
     "The judgement declined to rank these. A refusal is the designed answer, "
     "not a miss, so they are counted here and never scored."),
)


def _table(rows: list) -> list:
    out = ["| Card | Rules said | Judgement said | CEO said | Why |",
           "| --- | --- | --- | --- | --- |"]
    for row in rows:
        out.append(f"| {row['card']} | {row.get('rules') or '—'} | "
                   f"{row.get('judgement') or '—'} | {row.get('ceo') or '—'} | "
                   f"{row.get('why') or '—'} |")
    return out


def render_report(result: dict, doc: dict | None = None) -> str:
    """Both directions, always. The misses are the half that says where the
    judgement had no cover.

    Every audited card is printed exactly ONCE, under the one heading its
    outcome belongs to. The counts above name no card, so the reader can take
    the population off the sections and have it add up.
    """
    doc = doc if doc is not None else load()
    counts = result["counts"]
    rate = result.get("rate")
    out: list[str] = []
    w = out.append

    subject = result.get("card") or "the hand audit"
    w(f"# The groomer's judgement, scored against {subject}")
    w("")
    w("The held-out answer is the CEO's own, written before he read the "
      "model's reasons. Agreement and disagreement are both below, empty or "
      "not.")
    w("")
    w(f"- agreement: {counts['agree']} of {result['scored']} scored row(s)"
      + (f" ({rate * 100:.1f}%)" if rate is not None else " (no rate — nothing "
         "was scored)"))
    w(f"- false-done: {result['false_done']['counted']}"
      + _beside(result["false_done"]["declared"])
      + " — the judgement called a card done and the CEO says it is live work, "
        "the expensive miss, counted on its own line rather than folded into "
        "the rate")
    w(f"- unranked: {result['unranked']['counted']}"
      + _beside(result["unranked"]["declared"])
      + " — counted, never scored")
    w(f"- triggers on the not-now rows: {result['triggers']['event']} name an "
      f"event a person will notice, {result['triggers']['date']} name a date "
      f"nobody watches, {result['triggers']['unknown']} name nothing at all")
    w(f"- unanswered: {counts['unknown']} row(s) the CEO left blank or "
      "`unsure`, out of the rate")
    for note in result.get("notes") or ():
        w(f"- note: {note}")
    w("")

    for heading, outcome, blurb in SECTIONS:
        rows = [row for row in result["rows"] if row["outcome"] == outcome]
        w(heading)
        w("")
        w(blurb)
        w("")
        if not rows:
            w("*(none)*")
            w("")
            continue
        out.extend(_table(rows))
        w("")

    block = dimensions(doc).get(CONTAMINATED_DIMENSION) or {}
    w("## Excluded as contaminated")
    w("")
    w(f"`{CONTAMINATED_DIMENSION}` — {block.get('contaminated') or ''}".rstrip())
    w("")
    enforced = ", ".join(f"`{name}`" for name in block.get("enforces") or ())
    w(f"Enforced in code by {enforced or 'nothing this reference names'}, and "
      "checked by `groomer_score.py check` on every run. Scoring it would "
      "grade the code rather than the judgement, so it is named and never "
      "counted.")
    return "\n".join(out) + "\n"


def _beside(declared) -> str:
    """The count the audit DECLARED, printed beside the one that was counted.

    Never instead of it: a declared count that does not match the table is a
    row to go and read, and picking one of the two numbers hides which.
    """
    return "" if declared is None else f" (the audit declared {declared})"


# --------------------------------------------------------------------------- #
# CLI — the audit thread on stdin, a report out                                #
# --------------------------------------------------------------------------- #


def comment_bodies(payload) -> list:
    """The thread's comment bodies, out of whatever `dump-comments` handed over.

    Bare `dump-comments` prints an array of strings; `--with-authors` prints
    records. Both are read, so a caller cannot silently score an empty thread by
    passing the wrong one.
    """
    if isinstance(payload, dict):
        payload = payload.get("comments") or []
    bodies = []
    for item in payload or []:
        if isinstance(item, str):
            bodies.append(item)
        elif isinstance(item, dict):
            bodies.append(item.get("body") or "")
    return bodies


def _stdin_json(default):
    if sys.stdin is None or sys.stdin.isatty():
        return default
    raw = sys.stdin.read().strip()
    return json.loads(raw) if raw else default


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("check", help="the reference file against this code")

    scoring = sub.add_parser(
        "score", help="score the hand audit; the card's comments arrive on stdin")
    scoring.add_argument("--card", required=True,
                         help="the card the hand audit is recorded on")
    scoring.add_argument("--proposal", help="the groomer proposal's JSON")
    scoring.add_argument("--out", help="write the result as JSON")
    scoring.add_argument("--report", help="write the markdown report")

    args = parser.parse_args(argv)
    command = args.command or "check"

    if command == "check":
        problems = reference_problems()
        for problem in problems:
            print(f"  [FAIL] {problem}")
        print(f"{len(dimensions())} dimension(s), "
              f"{sum(1 for n in dimensions() if is_scored(n))} scored, "
              f"{len(problems)} problem(s)")
        return 1 if problems else 0

    if command == "score":
        proposal = None
        if args.proposal:
            with open(args.proposal, encoding="utf-8") as fh:
                proposal = json.load(fh)
        try:
            parsed = parse_audit(comment_bodies(_stdin_json([])))
        except AuditError as e:
            print(str(e), file=sys.stderr)
            return 1
        result = score(parsed, proposal=proposal, card=args.card)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as fh:
                json.dump(result, fh, indent=2)
        report = render_report(result)
        if args.report:
            with open(args.report, "w", encoding="utf-8") as fh:
                fh.write(report)
        print(report)
        return 0

    parser.print_usage(sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
